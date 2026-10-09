"""Shared lineup scheduling from verified provider fixtures; no network calls.

Existing prediction formulas, API quotas and stored answers are unchanged.
"""
import json
import copy
import hashlib
import marshal
import re
from pathlib import Path
import sqlite3
import time

WINDOW_KEY = 'collection_fixture_window_v1_'
PUBLICATION_LEAD_SECONDS = 2 * 3600
CURRENT_GRACE_SECONDS = 3 * 3600
_FILE_REVISIONS = {}


def morning_cutoff(now=None):
    """Return this round's 08:00 KST boundary only during its collection hour."""
    now = time.time() if now is None else float(now)
    local = now + 9 * 3600
    day = int(local // 86400) * 86400
    if 8 * 3600 <= local - day < 9 * 3600:
        return day + 8 * 3600 - 9 * 3600
    return None


def morning_ttl(ttl_h, now=None):
    now = time.time() if now is None else float(now)
    cutoff = morning_cutoff(now)
    return min(float(ttl_h), max(0.0, (now - cutoff) / 3600)) if cutoff is not None else ttl_h


def request_cache_fresh_enough(key, body, expires, ttl_resolver, now=None):
    """Refresh pre-round recent-history responses once; retain shared leases."""
    cutoff = morning_cutoff(now)
    if cutoff is None:
        return True
    path, params = json.loads(key)
    if path != '/fixtures' or not params.get('team') or int(params.get('last') or 0) != 40:
        return True
    payload = json.loads(body)
    return float(expires) - float(ttl_resolver(path, params, payload)) >= cutoff


def _pair_revision(resolver, database, home, away):
    values = [hashlib.sha256(marshal.dumps(resolver.__code__)).hexdigest()]
    root = Path(database).resolve().parent
    for name in ('collection_team_names.py', 'config.py'):
        path = root / name
        if not path.exists():
            values.append(None)
            continue
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        prior = _FILE_REVISIONS.get(str(path))
        if prior is None or prior[0] != signature:
            prior = (signature, hashlib.sha256(path.read_bytes()).hexdigest())
            _FILE_REVISIONS[str(path)] = prior
        values.append(prior[1])
    conn = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        conn.execute('PRAGMA query_only=ON')
        aliases = []
        if 'team_identity_manual_aliases' in _tables(conn):
            names = [re.sub(r'[^0-9A-Za-z가-힣]+', '', str(v or '')).casefold() for v in (home, away)]
            aliases = conn.execute('SELECT normalized_alias,api_team_id,api_team_name,approved_at FROM team_identity_manual_aliases WHERE normalized_alias IN (?,?) ORDER BY normalized_alias', names).fetchall()
        values.append(aliases)
    finally:
        conn.close()
    return hashlib.sha256(json.dumps(values, ensure_ascii=True).encode()).hexdigest()


def _verified_pair(home, away, fixture):
    try:
        teams = fixture.get('teams') or {}
        fid = int((fixture.get('fixture') or {}).get('id') or 0)
        return (fid > 0 and home.get('verified_pair') is True and away.get('verified_pair') is True
                and int(home.get('id') or 0) > 0 and int(away.get('id') or 0) > 0
                and home['id'] != away['id']
                and home['id'] == (teams.get('home') or {}).get('id')
                and away['id'] == (teams.get('away') or {}).get('id'))
    except (AttributeError, ValueError, TypeError, KeyError):
        return False


def resolve_cached_pair(home, away, match_time, ttl_h, league, resolver, get_cache, set_cache, database):
    """Reuse only an exact previously validated pair; never cache a failed match."""
    inputs = [str(v or '').strip() for v in (home, away, match_time, league)]
    ttl = min(2.0, max(0.0, float(ttl_h)))
    try:
        revision = _pair_revision(resolver, database, home, away)
    except (OSError, sqlite3.Error, AttributeError, ValueError, TypeError):
        return resolver(home, away, match_time, ttl_h=ttl_h, league_name=league)
    key = 'collection_verified_pair_v29_' + hashlib.sha256(json.dumps([inputs, revision], ensure_ascii=True).encode()).hexdigest()
    cached = get_cache(key, ttl) if ttl else None
    if (isinstance(cached, dict) and cached.get('inputs') == inputs and cached.get('revision') == revision
            and _verified_pair(cached.get('home'), cached.get('away'), cached.get('fixture'))):
        print('[팀검증 자료 재사용] 검증된 홈·원정·경기 시각 연결을 재사용합니다.', flush=True)
        return copy.deepcopy((cached['home'], cached['away'], cached['fixture']))
    result = resolver(home, away, match_time, ttl_h=ttl_h, league_name=league)
    h, a, fixture = result
    if ttl and match_time not in (None, '', '시간 미정', '마감/진행중') and _verified_pair(h, a, fixture):
        set_cache(key, {'inputs': inputs, 'revision': revision, 'home': h, 'away': a, 'fixture': fixture})
    return result


def remember_fixture(fixture, get_cache, set_cache):
    if not isinstance(fixture, dict):
        return False
    f = fixture.get('fixture') or {}
    teams = fixture.get('teams') or {}
    try:
        fid = int(f.get('id') or 0)
        kickoff = float(f.get('timestamp') or 0)
        home = int((teams.get('home') or {}).get('id') or 0)
        away = int((teams.get('away') or {}).get('id') or 0)
    except (ValueError, TypeError, OverflowError):
        return False
    if not fid > 0 or not 0 < kickoff < 4102444800 or home <= 0 or away <= 0 or home == away:
        return False
    key = WINDOW_KEY + str(fid)
    prior = get_cache(key, 24 * 30)
    if isinstance(prior, dict) and (prior.get('home_id'), prior.get('away_id')) != (home, away):
        return False  # A conflicting pair never redirects a saved fixture.
    value = {'fixture_id': fid, 'kickoff': kickoff, 'home_id': home, 'away_id': away,
             'source': 'verified_provider_fixture'}
    return prior == value or set_cache(key, value) is True


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def kickoff_for(conn, fid):
    tables = _tables(conn)
    if 'general_cache' in tables:
        row = conn.execute('SELECT cache_value FROM general_cache WHERE cache_key=?', (WINDOW_KEY + str(fid),)).fetchone()
        if row:
            try:
                value = json.loads(row[0])
                kickoff = float(value.get('kickoff') or 0)
                if (value.get('fixture_id') == fid and 0 < kickoff < 4102444800
                        and int(value.get('home_id') or 0) > 0 and int(value.get('away_id') or 0) > 0
                        and value['home_id'] != value['away_id']):
                    return kickoff
            except (ValueError, TypeError, KeyError):
                pass
    if 'collection_stat_sources' in tables:
        row = conn.execute('SELECT kickoff FROM collection_stat_sources WHERE fixture_id=?', (fid,)).fetchone()
        if row and 0 < float(row[0] or 0) < 4102444800:
            return float(row[0])
    return None


def lineup_wait_reason(database, fixture_id, now=None):
    """Near-kickoff refreshes may progress; old/unknown requests respect backoff."""
    now = time.time() if now is None else now
    try:
        fid = int(fixture_id)
    except (ValueError, TypeError):
        raise ValueError('Invalid fixture ID')
    if fid <= 0:
        raise ValueError('Invalid fixture ID')
    conn = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        kickoff = kickoff_for(conn, fid)
        if kickoff is not None:
            if now < kickoff - PUBLICATION_LEAD_SECONDS:
                return 'lineup_publication_window_not_open'
            if now <= kickoff + CURRENT_GRACE_SECONDS:
                # Request-cache TTL and single-flight still apply in api_get.
                return None
        if 'data_recovery_queue' in _tables(conn):
            key = json.dumps(['/fixtures/lineups', {'fixture': fid}], sort_keys=True, ensure_ascii=True)
            row = conn.execute('SELECT next_attempt FROM data_recovery_queue WHERE key=?', (key,)).fetchone()
            if row and float(row[0] or 0) > now:
                return 'historical_or_unknown_lineup_retry_pending'
        return None
    finally:
        conn.close()


def scheduling_sql(conn, now, priority):
    """Keep current lineups in the core lane and history in its bounded lane."""
    tables = _tables(conn)
    fid = "CAST(json_extract(q.params_json,'$.fixture') AS INTEGER)"
    sources = []
    if 'general_cache' in tables:
        sources.append("(SELECT CAST(json_extract(w.cache_value,'$.kickoff') AS REAL) FROM general_cache w WHERE w.cache_key='" + WINDOW_KEY + "'||" + fid + ")")
    if 'collection_stat_sources' in tables:
        sources.append('(SELECT w.kickoff FROM collection_stat_sources w WHERE w.fixture_id=' + fid + ')')
    kickoff = 'COALESCE(' + ','.join(sources + ['NULL']) + ')' if sources else 'NULL'
    current = '(' + kickoff + ' BETWEEN ' + str(float(now) - CURRENT_GRACE_SECONDS) + ' AND ' + str(float(now) + PUBLICATION_LEAD_SECONDS) + ')'
    ordinary = 'q.next_attempt<=' + str(float(now))
    due = ordinary
    if 'request_cache' in tables:
        cached = "EXISTS(SELECT 1 FROM request_cache c WHERE c.key=q.key AND c.body IS NOT NULL AND c.expires>" + str(float(now)) + ')'
        due = '(' + ordinary + ' OR (' + current + ' AND NOT ' + cached + '))'
    # A prepublication fixture is not yet eligible. Unknown fixtures retain
    # their existing due time, but cannot displace new, current material.
    allowed_time = '(' + kickoff + ' IS NULL OR ' + kickoff + '<=' + str(float(now) + PUBLICATION_LEAD_SECONDS) + ')'
    received = '0'
    if 'general_cache' in tables:
        received = "EXISTS(SELECT 1 FROM general_cache g WHERE g.cache_key='lineups_v2_detailed_'||" + fid + " AND json_extract(g.cache_value,'$.confirmed')=1)"
    eligible = "(q.path!='/fixtures/lineups' AND " + ordinary + ") OR (q.path='/fixtures/lineups' AND " + allowed_time + ' AND ' + due + ' AND NOT ' + received + ')'
    lane = "(CASE WHEN q.path='/fixtures/lineups' THEN CASE WHEN " + current + ' THEN 0 ELSE 1 END ELSE ' + priority + ' END)'
    return '(' + eligible + ')', lane
