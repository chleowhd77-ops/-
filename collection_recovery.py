"""Collector-only request identity, coverage evidence and fair recovery scheduling.

No network calls here. Forecasts and recent-two-match formulas are unchanged.
Coverage flags are observations about a league-season, not proof that every
fixture has data. Empty fixtures remain eligible for later retries.
"""
import json
import hashlib
import math
import sqlite3
import time


def schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS collection_stat_sources (
        fixture_id INTEGER PRIMARY KEY, home_id INTEGER, away_id INTEGER,
        kickoff REAL, league_id INTEGER, season INTEGER, league_name TEXT,
        requested_teams TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS collection_recovery_cursor (
        name TEXT PRIMARY KEY, position INTEGER NOT NULL)''')
    conn.commit()


def register(conn, fixture, team_id):
    f = fixture.get('fixture') or {}; teams = fixture.get('teams') or {}
    league = fixture.get('league') or {}
    fid = int(f.get('id') or 0); home = int((teams.get('home') or {}).get('id') or 0)
    away = int((teams.get('away') or {}).get('id') or 0); ko = float(f.get('timestamp') or 0)
    if (not fid or not home or not away or home == away or team_id not in (home, away)
            or not 0 < ko < time.time() or (f.get('status') or {}).get('short') not in ('FT','AET','PEN')):
        return False
    old = conn.execute('SELECT home_id,away_id,kickoff,requested_teams FROM collection_stat_sources WHERE fixture_id=?',(fid,)).fetchone()
    if old and (old[0],old[1],old[2]) != (home,away,ko):
        return False  # Conflicting identities never redirect a saved request.
    wanted = sorted(set(json.loads(old[3]) if old else []) | {int(team_id)})
    conn.execute('''INSERT INTO collection_stat_sources VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(fixture_id) DO UPDATE SET requested_teams=excluded.requested_teams''',
        (fid,home,away,ko,int(league.get('id') or 0),int(league.get('season') or 0),str(league.get('name') or ''),json.dumps(wanted)))
    return True


def identity(conn, fid):
    row = conn.execute('SELECT home_id,away_id,kickoff,league_id,season,league_name,requested_teams FROM collection_stat_sources WHERE fixture_id=?',(int(fid),)).fetchone()
    if not row:return {}
    return dict(zip(('home_id','away_id','kickoff','league_id','season','league_name','requested_teams'),row)) | {'fixture_id':int(fid), 'requested_teams':json.loads(row[-1])}


def observed_team_ids(rows):
    found = set()
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict):continue
        tid = int((row.get('team') or {}).get('id') or 0)
        for stat in row.get('statistics') or []:
            if not isinstance(stat,dict) or stat.get('value') in (None,'') or isinstance(stat.get('value'),bool):continue
            try:value = float(str(stat['value']).replace('%',''))
            except (TypeError,ValueError):continue
            if tid and math.isfinite(value) and value >= 0:
                found.add(tid);break
    return found


def valid_statistics(rows, source, required_team=None):
    found = observed_team_ids(rows)
    if not found:return False
    if not source:return True  # Legacy queues lack identity; do not invent one.
    expected = {int(source['home_id']),int(source['away_id'])}
    declared = {int((r.get('team') or {}).get('id') or 0) for r in rows if isinstance(r,dict)}
    wanted = {int(required_team)} if required_team else set(source.get('requested_teams') or expected)
    return declared <= expected and wanted <= found


def coverage_key(league, season):
    return f'league_coverage_v1_{int(league)}_{int(season)}'


def parse_coverage(payload, league, season):
    if not isinstance(payload,dict) or payload.get('errors'):return None
    matches=[]
    for row in payload.get('response') or []:
        if not isinstance(row,dict) or int((row.get('league') or {}).get('id') or 0)!=int(league):continue
        for item in row.get('seasons') or []:
            if int(item.get('year') or 0)==int(season) and isinstance(item.get('coverage'),dict):
                matches.append(item['coverage'])
    if len(matches)!=1:return None
    return {'league_id':int(league),'season':int(season),'coverage':matches[0],
            'observed_at':time.time(),'source':'/leagues'}


def flag(value):
    if not isinstance(value,dict):return None
    result=((value.get('coverage') or {}).get('fixtures') or {}).get('statistics_fixtures')
    return result if isinstance(result,bool) else None


def select(conn, limit, now):
    """Same total request budget; every fourth eligible slot progresses history.

    Current identity/material remains the majority. Fresh league coverage and
    newest missing historical fixtures no longer wait behind repeated empties.
    The cursor survives runs with limit=1 as well as normal batches of 12.
    """
    schema(conn);limit=max(0,int(limit))
    cols='q.key,q.path,q.params_json,q.attempts'
    base=f'''SELECT {cols} FROM data_recovery_queue q
        LEFT JOIN collection_stat_sources s ON q.path='/fixtures/statistics'
        AND s.fixture_id=CAST(json_extract(q.params_json,'$.fixture') AS INTEGER)
        WHERE q.next_attempt<=? AND {{condition}} ORDER BY
        q.attempts, CASE WHEN q.path='/leagues' THEN 0 WHEN q.path='/fixtures' AND json_extract(q.params_json,'$.ids') IS NOT NULL THEN 1 ELSE 2 END,
        COALESCE(s.kickoff,0) DESC,q.next_attempt,q.updated_at,q.key LIMIT ?'''
    # Compatibility with queues created before priority was introduced.
    priority='q.priority' if 'priority' in {r[1] for r in conn.execute('PRAGMA table_info(data_recovery_queue)')} else '0'
    groups=[list(conn.execute(base.format(condition=priority+'<=0'),(now,limit))),
            list(conn.execute(base.format(condition=priority+'>0'),(now,limit)))]
    saved=conn.execute('SELECT position FROM collection_recovery_cursor WHERE name="request"').fetchone()
    position=int(saved[0]) if saved else 0;chosen=[]
    for slot in range(limit):
        group=1 if (position+slot)%4==3 else 0
        if not groups[group]:group=1-group
        if not groups[group]:break
        chosen.append(groups[group].pop(0))
    return chosen,position


def advance(conn, position, attempted):
    conn.execute('''INSERT INTO collection_recovery_cursor VALUES('request',?)
        ON CONFLICT(name) DO UPDATE SET position=excluded.position''',((position+attempted)%4,))
    conn.commit()


def retry_pending(fid, connect):
    conn=connect()
    try:
        key=json.dumps(['/fixtures/statistics',{'fixture':int(fid)}],sort_keys=True,ensure_ascii=True)
        try:row=conn.execute('SELECT next_attempt FROM data_recovery_queue WHERE key=?',(key,)).fetchone()
        except sqlite3.OperationalError:return False
        return bool(row and row[0]>time.time())
    finally:conn.close()


def retry_reason(path, params, payload, status, source, get_cache):
    if status!=200:return f'provider_http_{status}',None
    if payload.get('errors'):return 'provider_api_error',None
    if path!='/fixtures/statistics':return 'provider_empty_pending',None
    value=get_cache(coverage_key(source['league_id'],source['season']),24) if source.get('league_id') and source.get('season') else None
    coverage=flag(value)
    if coverage is False:return 'provider_coverage_false_fixture_pending',False
    if coverage is True:return 'provider_coverage_true_fixture_empty',True
    return 'provider_empty_pending',None


def delay(attempts, reason):
    if reason=='bulk_statistics_probe_failed':return 6*3600
    if reason=='provider_coverage_false_fixture_pending':return 6*3600
    # Confirmed repeated HTTP200 empty results must not crowd out untried data.
    if reason in ('provider_empty_pending','provider_coverage_true_fixture_empty'):
        return max(300,min(6*3600,300*2**min(int(attempts),7)))
    return max(300,min(1800,60*2**min(int(attempts),5)))


def bulk_key(params):
    return 'fixture_statistics_bulk_probe_v1_'+hashlib.sha256(str(params['ids']).encode()).hexdigest()[:24]


def accept_bulk(conn, params, payload, set_cache):
    """Save only actual embedded statistics from the exact registered fixtures.

    Official /fixtures?ids=... supports at most 20 IDs. Missing embedded data
    never replaces any good cache and retains the individual recovery requests.
    """
    wanted={int(v) for v in str(params['ids']).split('-')}
    if not wanted or len(wanted)>20:raise ValueError('bulk_statistics_ids_invalid')
    verified,received=set(),set()
    for row in payload.get('response') or []:
        if not isinstance(row,dict):continue
        f=row.get('fixture') or {};fid=int(f.get('id') or 0)
        if fid not in wanted:continue
        source=identity(conn,fid);teams=row.get('teams') or {}
        home=int((teams.get('home') or {}).get('id') or 0);away=int((teams.get('away') or {}).get('id') or 0)
        ko=float(f.get('timestamp') or 0)
        if (not source or (home,away,ko)!=(source['home_id'],source['away_id'],source['kickoff'])
                or (f.get('status') or {}).get('short') not in ('FT','AET','PEN') or not 0<ko<time.time()):continue
        verified.add(fid)
        rows=row.get('statistics') or []
        if not valid_statistics(rows,source):continue
        set_cache('completed_fixture_stats_v1_'+str(fid),rows)
        key=json.dumps(['/fixtures/statistics',{'fixture':fid}],sort_keys=True,ensure_ascii=True)
        conn.execute('DELETE FROM data_recovery_queue WHERE key=?',(key,))
        received.add(fid)
    conn.commit()
    return {'requested':len(wanted),'verified':len(verified),'received':len(received)}
