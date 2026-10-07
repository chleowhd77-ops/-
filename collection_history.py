"""Collect original statistics progressively without changing forecast formulas.

The normal collector still computes its existing recent-two-match indicators.
All validated recent fixtures are also eligible for cached original statistics.
Missing older statistics use the existing bounded recovery worker, never a new
network call from an analyst, dashboard or this planner.
"""
import json
import time


def plan(team_id, fixtures, get_cache, set_cache, enqueue, runtime_connect):
    team_id = int(team_id or 0)
    received, pending, wanted = [], [], []
    if not team_id:
        return {'received': [], 'pending_fixture_ids': [], 'requested_count': 0}
    # One read connection per team; the provider queue owns all actual calls.
    conn = runtime_connect()
    try:
        for fixture in fixtures or []:
            fid = int((fixture.get('fixture') or {}).get('id') or 0)
            members = {(fixture.get('teams', {}).get(s) or {}).get('id')
                       for s in ('home', 'away')}
            status = (fixture.get('fixture') or {}).get('status', {}).get('short')
            kickoff = (fixture.get('fixture') or {}).get('timestamp') or 0
            if not fid or team_id not in members or status not in ('FT', 'AET', 'PEN') or not 0 < kickoff < time.time():
                continue
            if fid in wanted:
                continue
            wanted.append(fid)
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
            if own is None:
                pending.append(fid)
                enqueue('/fixtures/statistics', {'fixture': fid},
                        reason='recent_history_collection', priority=1)
                continue
            set_cache(key, rows)
            received.append({'fixture_id': fid, 'team_id': team_id,
                             'statistics': own['statistics']})
    finally:
        conn.close()
    # Arrival progress is explicit; lack of support is never made up as zero.
    result = {'received': received, 'pending_fixture_ids': pending,
              'requested_count': len(wanted)}
    key = f'recent_fixture_statistics_v1_{team_id}'
    if get_cache(key, 24*30) != result:
        set_cache(key, result)
    return result
