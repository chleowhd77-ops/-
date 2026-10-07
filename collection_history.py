"""Collect original statistics progressively without changing forecast formulas.

The normal collector still computes its existing recent-two-match indicators.
All validated recent fixtures are also eligible for cached original statistics.
Missing older statistics use the existing bounded recovery worker, never a new
network call from an analyst, dashboard or this planner.
"""
import json
import time
from collection_recovery import (schema, register, identity, coverage_key,
                                 parse_coverage, flag, valid_statistics, bulk_key)


def plan(team_id, fixtures, get_cache, set_cache, enqueue, runtime_connect):
    team_id = int(team_id or 0)
    received, pending, wanted, reports = [], [], [], []
    if not team_id:
        return {'received': [], 'pending_fixture_ids': [], 'requested_count': 0}
    # One read connection per team; the provider queue owns all actual calls.
    conn = runtime_connect()
    try:
        schema(conn)
        for fixture in sorted(fixtures or [], key=lambda f: float((f.get('fixture') or {}).get('timestamp') or 0), reverse=True):
            fid = int((fixture.get('fixture') or {}).get('id') or 0)
            members = {(fixture.get('teams', {}).get(s) or {}).get('id')
                       for s in ('home', 'away')}
            status = (fixture.get('fixture') or {}).get('status', {}).get('short')
            kickoff = (fixture.get('fixture') or {}).get('timestamp') or 0
            if not fid or team_id not in members or status not in ('FT', 'AET', 'PEN') or not 0 < kickoff < time.time():
                continue
            if fid in wanted:
                continue
            if not register(conn, fixture, team_id):
                continue
            conn.commit()
            wanted.append(fid)
            source = identity(conn, fid)
            league, season = source['league_id'], source['season']
            support = None
            if league and season:
                support = get_cache(coverage_key(league, season), 24)
                if support is None:
                    coverage_request = json.dumps(['/leagues', {'id':league, 'season':season}], sort_keys=True, ensure_ascii=True)
                    saved_coverage = conn.execute('SELECT body,expires FROM request_cache WHERE key=?', (coverage_request,)).fetchone()
                    if saved_coverage and saved_coverage[0] and saved_coverage[1] > time.time():
                        try:support = parse_coverage(json.loads(saved_coverage[0]), league, season)
                        except (ValueError,TypeError):pass
                        if support is not None:set_cache(coverage_key(league, season),support)
                    if support is None:
                        enqueue('/leagues', {'id':league, 'season':season}, reason='coverage_verification', priority=1)
            key = f'completed_fixture_stats_v1_{fid}'
            rows = get_cache(key, 24*30)
            if rows is None:
                request_key = json.dumps(['/fixtures/statistics', {'fixture': fid}],
                                         sort_keys=True, ensure_ascii=True)
                saved = conn.execute('SELECT body,expires FROM request_cache WHERE key=?',
                                     (request_key,)).fetchone()
                if saved and saved[0] and saved[1] > time.time():
                    try:
                        body = json.loads(saved[0])
                        if not body.get('errors'):
                            rows = body.get('response')
                    except (TypeError, ValueError, AttributeError):
                        pass
            own = next((r for r in rows or [] if isinstance(r, dict)
                        and int((r.get('team') or {}).get('id') or 0) == team_id
                        and any(v.get('value') is not None for v in r.get('statistics') or [])), None)
            if own is None or not valid_statistics(rows, source, team_id):
                pending.append(fid)
                enqueue('/fixtures/statistics', {'fixture': fid},
                        reason='recent_history_collection', priority=1)
                key_request = json.dumps(['/fixtures/statistics', {'fixture':fid}], sort_keys=True, ensure_ascii=True)
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                q = conn.execute('SELECT reason,attempts,next_attempt FROM data_recovery_queue WHERE key=?', (key_request,)).fetchone() if 'data_recovery_queue' in tables else None
                reports.append({'fixture_id':fid, 'league_id':league,'season':season,
                    'provider_statistics_flag':flag(support), 'reason':q[0] if q else 'queued',
                    'attempts':int(q[1]) if q else 0, 'next_attempt':q[2] if q else None})
                continue
            set_cache(key, rows)
            received.append({'fixture_id': fid, 'team_id': team_id,
                             'fixture': fixture['fixture'], 'league': fixture.get('league') or {},
                             'teams': fixture['teams'],
                             'statistics': own['statistics']})
    finally:
        conn.close()
    # Existing bounded worker owns the actual calls. An official bulk request
    # can fill up to 20 raw caches without replacing the individual fallbacks.
    if len(pending)>1:
        for start in range(0,len(pending),20):
            params={'ids':'-'.join(str(v) for v in pending[start:start+20])}
            if get_cache(bulk_key(params),6) is None:
                enqueue('/fixtures',params,reason='recent_statistics_bulk',priority=1)
    # Arrival progress is explicit; lack of support is never made up as zero.
    result = {'received': received, 'pending_fixture_ids': pending,
              'requested_count': len(wanted), 'request_reports': reports}
    key = f'recent_fixture_statistics_v1_{team_id}'
    if get_cache(key, 24*30) != result:
        set_cache(key, result)
    return result
