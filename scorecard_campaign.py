"""Read-only display baseline. Historical picks, grades and memories stay intact."""
from copy import deepcopy
from scorecard_core import epoch, KST, summary

START = '2026-10-05T07:03:06+09:00'
VERSIONS = {'manager': 'remembered-manager-v1', 'top3': 'remembered-products-v1',
            'proto_world': 'remembered-products-v1', 'toto14': 'remembered-products-v1'}


def campaign_rows(rows, track):
    return [r for r in rows if r.get('model_version') == VERSIONS.get(track)
            and epoch(r.get('kickoff_at'), KST) >= epoch(START)]


def totals(rows):
    return summary([r for r in rows if r.get('settlement') != 'REFUND'])


def with_manager_answers(data, payload):
    if payload.get('schema_version') != VERSIONS['manager']:
        return data
    data = deepcopy(data)
    cells = data.setdefault('tracks', {}).setdefault('manager', {})
    for engine in ('official', 'robot', 'v2', 'v3'):
        fresh = []
        for p in (payload.get('picks') or {}).values():
            if p.get('engine_key') != engine:
                continue
            delivered = p.get('delivered_at')
            if isinstance(delivered, dict):
                delivered = delivered.get('confirmed_at')
            if not 0 < epoch(p.get('frozen_at')) <= epoch(delivered) < epoch(p.get('kickoff_at'), KST):
                continue
            fresh.append({**p, 'track': 'manager', 'engine': engine,
                          'home_team': p.get('home'), 'away_team': p.get('away'),
                          'model_version': VERSIONS['manager']})
        cell = cells.setdefault(engine, {'rows': []})
        cell['rows'] = [r for r in cell.get('rows', [])
                        if r.get('model_version') != VERSIONS['manager']] + fresh
        cell['summary'] = totals(cell['rows'])
        data.setdefault('active_models', {}).setdefault('manager', {})[engine] = VERSIONS['manager']
    return data
