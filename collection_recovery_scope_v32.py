"""Bound background recovery to current tickets and observed missing material.

No network calls. Raw recent-40 history and native forecast formulas stay intact.
An empty result is an observation, never a fabricated statistic or permanent ban.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import time
from threading import RLock

POLICY = 'current-ticket-recovery-v32-1'
_MEMO = {}
_MEMO_LOCK = RLock()
NEGATIVE = {'provider_empty_pending', 'provider_coverage_false_fixture_pending',
            'provider_coverage_true_fixture_empty', 'statistics_team_missing_or_invalid'}


def epoch(value):
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value or '').strip()
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone(timedelta(hours=9)))).timestamp()
    except ValueError:
        m = re.fullmatch(r'(\d{2})\.(\d{2})\.(\d{2})\s*\([^)]*\)\s*(\d{2}):(\d{2})', value)
        if not m:
            return 0
        try:
            y, month, day, h, minute = map(int, m.groups())
            return datetime(y+2000, month, day, h, minute,
                            tzinfo=timezone(timedelta(hours=9))).timestamp()
        except ValueError:
            return 0


def dossier_key(match, kickoff):
    stamp = datetime.fromtimestamp(kickoff, timezone(timedelta(hours=9))).strftime('%Y-%m-%dT%H:%M')
    identity = '|'.join([str(match.get(k) or '').strip().casefold() for k in ('home', 'away')] + [stamp])
    return 'shared_fixture_dossier_v1_' + hashlib.sha256(identity.encode()).hexdigest()[:24]


def root_for(conn):
    path = next((row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main'), '')
    if not path:
        raise ValueError('runtime database path unavailable')
    return Path(path).resolve().parent


def cache(conn, key):
    row = conn.execute('SELECT cache_value FROM general_cache WHERE cache_key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def build_scope(conn, now=None, root=None):
    now = time.time() if now is None else now
    root = root_for(conn) if root is None else Path(root)
    path = root / 'betman_data.json'
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or not isinstance(data.get('proto_matches'), list):
        raise ValueError('current ticket format unavailable')
    result = {'current': {}, 'teams': {}, 'pairs': set(), 'leagues': set(), 'stats': {}, 'critical': {}, 'unresolved': 0, 'legacy_current': set()}
    pairs = {}
    # First visit: the validated pair is cached before the full dossier exists.
    for pair_row in conn.execute('''SELECT json_extract(cache_value,'$.inputs'),
            json_extract(cache_value,'$.home.id'),json_extract(cache_value,'$.away.id'),
            json_extract(cache_value,'$.fixture.fixture.id'),
            json_extract(cache_value,'$.fixture.fixture.timestamp'),
            json_extract(cache_value,'$.fixture.league.id'),json_extract(cache_value,'$.fixture.league.season')
            FROM general_cache WHERE cache_key LIKE 'collection_verified_pair_v29_%'
            AND updated_at>=? AND json_extract(cache_value,'$.home.verified_pair')=1
            AND json_extract(cache_value,'$.away.verified_pair')=1''', (now-2*3600,)):
        inputs = json.loads(pair_row[0])
        if isinstance(inputs, list) and len(inputs) == 4:
            identity = (str(inputs[0]).strip().casefold(), str(inputs[1]).strip().casefold(), epoch(inputs[2]), str(inputs[3]).strip())
            value = pair_row[1:]
            if identity in pairs and pairs[identity] != value:
                raise ValueError('conflicting cached current pairs')
            pairs[identity] = value
    for field in ('proto_matches', 'toto_14_matches'):
        for match in data.get(field, []) or []:
            if not isinstance(match, dict) or not match.get('home') or not match.get('away'):
                continue
            ko = epoch(match.get('match_time') or match.get('time'))
            if ko <= now:
                continue
            # Exact ticket identity key; do not scan old shared dossier bodies.
            key = dossier_key(match, ko)
            row = conn.execute('''SELECT json_extract(cache_value,'$.home.id'),
                json_extract(cache_value,'$.away.id'),json_extract(cache_value,'$.fixture_id'),
                json_extract(cache_value,'$.kickoff_at'),json_extract(cache_value,'$.league_id'),
                json_extract(cache_value,'$.season'),json_extract(cache_value,'$.home.verified_pair'),
                json_extract(cache_value,'$.away.verified_pair') FROM general_cache WHERE cache_key=?''', (key,)).fetchone()
            if not row or not row[2]:
                p = pairs.get((str(match['home']).strip().casefold(), str(match['away']).strip().casefold(), ko, str(match.get('league') or '').strip()))
                if p:
                    row = (p[0], p[1], p[2], p[3], p[4], p[5], 1, 1)
            if not row:
                result['unresolved'] += 1
                continue
            h, a, fid, saved_ko, league, season, home_verified, away_verified = row
            h, a, fid = int(h or 0), int(a or 0), int(fid or 0)
            window = cache(conn, 'collection_fixture_window_v1_' + str(fid))
            # Older exact-ticket dossiers already contain validated team pairs.
            # A missing new window is compatible; a present conflicting one is not.
            # Do not fabricate provider kickoff records from the ticket target time.
            verified = ((window.get('home_id'), window.get('away_id'), window.get('kickoff')) == (h, a, ko)
                        if isinstance(window, dict) else
                        window is None and home_verified == 1 and away_verified == 1)
            if not h or not a or h == a or not fid or epoch(saved_ko) != ko or not verified:
                result['unresolved'] += 1
                continue
            target = key + ':' + str(int(ko))
            result['current'][fid] = ko
            if window is None:
                result['legacy_current'].add(fid)
            result['pairs'].add((h,a))
            if league and season:
                result['leagues'].add((int(league), int(season)))
            for tid in (h, a):
                result['teams'].setdefault(tid, []).append((ko, target))
    for tid, targets in result['teams'].items():
        raw = cache(conn, 'recent_fixtures_v2_' + str(tid)) or []
        valid = {}
        for f in raw if isinstance(raw, list) else []:
            info = f.get('fixture') or {}; teams = f.get('teams') or {}; league = f.get('league') or {}
            fid = int(info.get('id') or 0); ko = float(info.get('timestamp') or 0)
            h = int((teams.get('home') or {}).get('id') or 0)
            a = int((teams.get('away') or {}).get('id') or 0)
            if (fid > 0 and h > 0 and a > 0 and h != a and tid in (h, a)
                    and 0 < ko < now and (info.get('status') or {}).get('short') in ('FT', 'AET', 'PEN')):
                valid[fid] = {'home_id': h, 'away_id': a, 'kickoff': ko,
                              'league_id': int(league.get('id') or 0), 'season': int(league.get('season') or 0)}
        for fid, source in valid.items():
            prior = result['stats'].get(fid)
            if prior and (prior['home_id'], prior['away_id'], prior['kickoff']) != (source['home_id'], source['away_id'], source['kickoff']):
                raise ValueError('conflicting history identities')
            result['stats'][fid] = source
            if source['league_id'] and source['season']:
                result['leagues'].add((source['league_id'], source['season']))
        for fid in sorted(valid, key=lambda v: valid[v]['kickoff'], reverse=True)[:2]:
            for ko, target in targets:
                if 0 < ko-now <= 2*3600:
                    result['critical'].setdefault(fid, set()).add(target)
    return result


def scope(conn, now):
    root = root_for(conn)
    path = root / 'betman_data.json'
    stat = path.stat()
    signature = (str(root), stat.st_mtime_ns, stat.st_size, int(now//30))
    with _MEMO_LOCK:
        if signature not in _MEMO:
            value = build_scope(conn, now, root)
            _MEMO.clear(); _MEMO[signature] = value
        return _MEMO[signature]


def schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS collection_recovery_observations_v32 (
        fixture_id INTEGER PRIMARY KEY, coverage_revision TEXT, observed_at REAL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS collection_recovery_final_v32 (
        fixture_id INTEGER, target TEXT, checked_at REAL,
        PRIMARY KEY(fixture_id,target))''')
    conn.commit()


def coverage_revision(conn, source):
    key = 'league_coverage_v1_' + str(source['league_id']) + '_' + str(source['season'])
    support = cache(conn, key) or {}
    flag = ((support.get('coverage') or {}).get('fixtures') or {}).get('statistics_fixtures')
    # A refreshed observation of the same coverage flag is not new evidence.
    return json.dumps(flag if isinstance(flag, bool) else None), flag


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def stat_decision(conn, fid, current, now, bulk=False):
    source = current['stats'].get(fid)
    if not source:
        return 'outside_current_history', None
    revision, flag = coverage_revision(conn, source)
    key = json.dumps(['/fixtures/statistics', {'fixture': fid}], sort_keys=True, ensure_ascii=True)
    q = conn.execute('SELECT attempts,reason FROM data_recovery_queue WHERE key=?', (key,)).fetchone()
    t = tables(conn)
    negative = conn.execute('SELECT coverage_revision FROM collection_recovery_observations_v32 WHERE fixture_id=?', (fid,)).fetchone() if 'collection_recovery_observations_v32' in t else None
    if negative and negative[0] != revision and flag is True:
        # Respect transient-error backoff while reopening a genuine new coverage observation.
        reopen = not q or q[1] in NEGATIVE or q[1] == 'recent_history_collection'
        return None, 'coverage_changed_to_true' if reopen and not bulk else None
    known_empty = bool(negative) or bool(q and int(q[0] or 0) >= 1 and q[1] in NEGATIVE)
    if not known_empty:
        return None, None  # Untried history or transient failure keeps native retry rules.
    if bulk:
        return 'observed_empty_bulk_wait', None
    for target in sorted(current['critical'].get(fid, set())):
        checked = conn.execute('SELECT 1 FROM collection_recovery_final_v32 WHERE fixture_id=? AND target=?', (fid, target)).fetchone() if 'collection_recovery_final_v32' in t else None
        if not checked:
            return None, target
    return 'observed_empty_wait_until_current_t120', None


def recovery_scope(conn, now):
    """Pure selection filter. The native selector still sets its total budget."""
    current = scope(conn, now)
    conn.execute('CREATE TEMP TABLE IF NOT EXISTS recovery_allowed_v32(key TEXT PRIMARY KEY)')
    conn.execute('DELETE FROM recovery_allowed_v32')
    conn.execute('CREATE TEMP TABLE IF NOT EXISTS recovery_final_due_v32(key TEXT PRIMARY KEY)')
    conn.execute('DELETE FROM recovery_final_due_v32')
    changed = {}; counts = {}; rows = conn.execute('SELECT key,path,params_json,attempts,reason FROM data_recovery_queue').fetchall()
    def deny(reason):
        counts[reason] = counts.get(reason, 0) + 1
    for key, path, raw, attempts, reason in rows:
        try:
            p = json.loads(raw)
            if not isinstance(p, dict):raise ValueError('params format')
            for field in ('fixture','team','id','league','season'):
                if p.get(field) is not None:int(p[field])
            for field in ('ids','h2h'):
                if p.get(field):
                    ids = [int(v) for v in str(p[field]).split('-')]
                    if not ids or len(ids)>20 or any(v<=0 for v in ids):raise ValueError('fixture IDs')
        except (ValueError,TypeError):
            deny('invalid_saved_request_preserved');continue
        allow = False
        if path == '/fixtures/statistics' and p.get('fixture'):
            wait, target = stat_decision(conn, int(p['fixture']), current, now)
            allow = wait is None
            if target:
                # One current final check may progress despite an old six-hour due time.
                conn.execute('INSERT OR IGNORE INTO recovery_final_due_v32 VALUES(?)', (key,))
            if not allow: deny(wait)
        elif path == '/fixtures' and p.get('ids'):
            if int(attempts or 0) > 0 and reason == 'bulk_statistics_probe_failed':
                deny('failed_bulk_keep_individual_fallback'); continue
            ids = [int(v) for v in str(p['ids']).split('-')]
            wanted = [fid for fid in ids if stat_decision(conn, fid, current, now, bulk=True)[0] is None]
            if wanted:
                allow = True; updated = dict(p); updated['ids'] = '-'.join(map(str, wanted))
                changed[key] = json.dumps(updated)
            else: deny('bulk_outside_scope_or_observed_empty')
        elif path == '/leagues':
            allow = (int(p.get('id') or 0), int(p.get('season') or 0)) in current['leagues']
        elif path == '/fixtures/lineups':
            fid = int(p.get('fixture') or 0)
            allow = fid in current['current'] and 0 < current['current'][fid]-now <= 2*3600
            if not allow:
                deny('lineup_outside_current_t120'); continue
        elif p.get('fixture'):
            allow = int(p['fixture']) in current['current']
        elif p.get('team'):
            allow = int(p['team']) in current['teams']
        elif path == '/fixtures/headtohead' and p.get('h2h'):
            ids = [int(v) for v in str(p['h2h']).split('-')]
            allow = len(ids) == 2 and (tuple(ids) in current['pairs'] or tuple(reversed(ids)) in current['pairs'])
        elif path == '/standings':
            allow = (int(p.get('league') or 0), int(p.get('season') or 0)) in current['leagues']
        if allow:
            conn.execute('INSERT OR IGNORE INTO recovery_allowed_v32 VALUES(?)', (key,))
        elif path not in ('/fixtures/statistics', '/fixtures'):
            deny('no_verified_current_ticket_link')
    count = conn.execute('SELECT COUNT(*) FROM recovery_allowed_v32').fetchone()[0]
    print('[복구 범위 V32.1] 현재 경기', len(current['current']), '/ 대상 요청', count,
          '/ 대기 사유', json.dumps(counts, ensure_ascii=False), '/ 미연결 경기', current['unresolved'], flush=True)
    return changed


def network_wait(database, path, params, purpose, now=None):
    if purpose != 'analysis' or not (path in ('/fixtures/statistics', '/fixtures/lineups') or path == '/fixtures' and params.get('ids')):
        return None
    now = time.time() if now is None else now
    conn = sqlite3.connect(database, timeout=3)
    try:
        current = scope(conn, now)
        if path == '/fixtures/lineups':
            kickoff = current['current'].get(int(params.get('fixture') or 0))
            if kickoff is None:
                return 'lineup_no_verified_current_ticket'
            return None if 0 < kickoff-now <= 2*3600 else 'lineup_wait_until_current_t120'
        ids = [int(params['fixture'])] if path == '/fixtures/statistics' else [int(v) for v in str(params['ids']).split('-')]
        for fid in ids:
            wait, target = stat_decision(conn, fid, current, now, bulk=path == '/fixtures')
            if wait:
                return wait
        return None
    finally:
        conn.close()


def retry_override(conn, fid, now):
    """None keeps native cooldown; True blocks; False opens one current final check."""
    wait, target = stat_decision(conn, int(fid), scope(conn, now), now)
    if wait:
        return True
    return False if target else None


def note_response(database, path, params, payload, purpose, now=None):
    if purpose != 'analysis' or not (path == '/fixtures/statistics' or path == '/fixtures' and params.get('ids')):
        return
    if payload.get('errors') or not isinstance(payload.get('response'), list):
        return
    from collection_recovery import valid_statistics
    now = time.time() if now is None else now
    conn = sqlite3.connect(database, timeout=3)
    try:
        schema(conn); current = scope(conn, now)
        ids = [int(params['fixture'])] if path == '/fixtures/statistics' else [int(v) for v in str(params['ids']).split('-')]
        responses = payload['response']
        by_id = {int((r.get('fixture') or {}).get('id') or 0): r for r in responses} if path == '/fixtures' else {}
        for fid in ids:
            source = current['stats'].get(fid)
            if not source:
                continue
            row = by_id.get(fid)
            if path == '/fixtures':
                if not row:
                    continue  # Unreturned/unverified fixture is not proof of empty statistics.
                f = row.get('fixture') or {}; teams = row.get('teams') or {}
                if ((int((teams.get('home') or {}).get('id') or 0), int((teams.get('away') or {}).get('id') or 0), float(f.get('timestamp') or 0))
                        != (source['home_id'], source['away_id'], source['kickoff'])):
                    continue
                values = row.get('statistics') or []
            else:
                values = responses
            for target in current['critical'].get(fid, set()):
                conn.execute('INSERT OR REPLACE INTO collection_recovery_final_v32 VALUES(?,?,?)', (fid, target, now))
            required = [tid for tid in (source['home_id'], source['away_id']) if tid in current['teams']]
            if any(not valid_statistics(values, source, tid) for tid in required):
                revision, _ = coverage_revision(conn, source)
                conn.execute('INSERT OR REPLACE INTO collection_recovery_observations_v32 VALUES(?,?,?)', (fid, revision, now))
            else:
                conn.execute('DELETE FROM collection_recovery_observations_v32 WHERE fixture_id=?', (fid,))
        conn.commit()
    finally:
        conn.close()
