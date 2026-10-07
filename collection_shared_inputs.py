"""Read the shared collector dossier directly; no API, AI or database writes.

Only material for the exact fixture, ordered team IDs and scheduled kickoff is
eligible. Old frozen receipts retain their original question forever.
"""
import copy
import hashlib
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path


def _cached(root, key, now):
    for name, table in (('api_runtime.db', 'general_cache'), ('ai_predictions.db', 'api_cache')):
        path = Path(root)/name
        if not path.is_file():
            continue
        conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
        try:
            conn.execute('PRAGMA query_only=ON')
            if not conn.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?', (table,)).fetchone():
                continue
            row = conn.execute(f'SELECT cache_value,updated_at FROM {table} WHERE cache_key=?', (key,)).fetchone()
            if not row:
                continue
            captured = row[1]
            if not isinstance(captured, (int, float)):
                dt = datetime.fromisoformat(str(captured).replace('Z', '+00:00'))
                captured = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
            if not 0 < captured <= now or now-captured > 120*3600:
                continue
            value = json.loads(row[0])
            if isinstance(value, dict):
                return value, captured
        finally:
            conn.close()
    return {}, 0


def merge(root, identity, current, now, base_captured_at=0):
    ko = float(identity['kickoff'])
    local = datetime.fromtimestamp(ko, timezone(timedelta(hours=9)))
    text = '|'.join([str(identity[s]).strip().casefold() for s in ('home', 'away')]
                    +[local.strftime('%Y-%m-%dT%H:%M')])
    key = 'shared_fixture_dossier_v1_'+hashlib.sha256(text.encode()).hexdigest()[:24]
    dossier, captured = _cached(root, key, now)
    if not dossier or not captured < ko:
        return current, {'used': False, 'reason': 'exact_shared_dossier_pending'}
    try:
        dt = datetime.fromisoformat(str(dossier['kickoff_at']).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return current, {'used': False, 'reason': 'shared_kickoff_unverified'}
        ids = {s: int((dossier.get(s) or {}).get('id') or 0) for s in ('home', 'away')}
        old_ids = {s: int(((current.get('teams') or {}).get(s) or {}).get('id') or 0) for s in ids}
        if (int(dossier.get('fixture_id') or 0) != int(identity['fixture_id'] or 0)
                or dt.timestamp() != ko or not ids['home'] or not ids['away']
                or ids['home'] == ids['away'] or old_ids != ids):
            return current, {'used': False, 'reason': 'shared_fixture_identity_mismatch'}
    except (KeyError, ValueError, TypeError, AttributeError):
        return current, {'used': False, 'reason': 'shared_fixture_identity_unverified'}
    result = copy.deepcopy(current)
    raw = dossier.get('evidence') or {}
    applied = []
    def assign(field, side, value, existing_valid=False):
        if captured < base_captured_at and existing_valid:
            return
        result.setdefault(field, {})[side] = copy.deepcopy(value)
        applied.append(field+'.'+side)
    for side, team_id in ids.items():
        team = (raw.get('teams') or {}).get(str(team_id)) or {}
        if team.get('recent_fixtures'):
            previous = (result.get('schedule_and_travel') or {}).get('recent_fixtures') or {}
            if captured >= base_captured_at or not previous.get(side):
                result.setdefault('schedule_and_travel', {}).setdefault('recent_fixtures', {})[side] = copy.deepcopy(team['recent_fixtures'])
            if (team.get('recent_metrics') or {}).get('matches', 0) > 0:
                assign('recent_form', side, team['recent_metrics'],
                       bool(((current.get('recent_form') or {}).get(side) or {}).get('matches')))
            if team.get('long_term'):
                old = ((current.get('long_term') or {}).get(side) or {})
                assign('long_term', side, team['long_term'], bool(old.get('home_total') or old.get('away_total')))
        stats = team.get('recent_stats') or {}
        if stats.get('sample_size', 0) > 0:
            assign('recent_match_stats', side, stats,
                   ((current.get('recent_match_stats') or {}).get(side) or {}).get('sample_size',0)>0)
        history = team.get('recent_statistics') or {}
        if history.get('received'):
            # Pending request IDs are operational state, not forecast facts.
            assign('recent_fixture_statistics', side, history['received'],
                   bool((current.get('recent_fixture_statistics') or {}).get(side)))
        rank = (raw.get('standings') or {}).get(side) or {}
        if 0 < int(rank.get('rank') or 0) < 99:
            old_rank = ((current.get('standings') or {}).get(side) or {}).get('rank') or 0
            assign('standings', side, rank, 0 < int(old_rank) < 99)
        injury = (raw.get('injuries') or {}).get(side) or {}
        if injury.get('available') is True:
            assign('injuries_and_absences', side, injury,
                   ((current.get('injuries_and_absences') or {}).get(side) or {}).get('available') is True)
        if isinstance(team.get('squad'), list) and team['squad']:
            assign('squads', side, team['squad'], bool((current.get('squads') or {}).get(side)))
        if (team.get('manager') or {}).get('available') is True:
            assign('managers', side, team['manager'],
                   ((current.get('managers') or {}).get(side) or {}).get('available') is True)
    h2h = raw.get('h2h') or {}
    previous_h2h = current.get('h2h_and_matchup') or {}
    if ((h2h.get('available') is True or h2h.get('total', 0) > 0)
            and (captured >= base_captured_at or not (previous_h2h.get('available') or previous_h2h.get('total')))):
        result['h2h_and_matchup'] = copy.deepcopy(h2h)
        applied.append('h2h_and_matchup')
    if raw.get('league_key_players_available') is True and (captured >= base_captured_at or not current.get('league_key_players')):
        result['league_key_players'] = copy.deepcopy(raw.get('league_key_players') or {})
        applied.append('league_key_players')
    lineup = raw.get('lineups') or {}
    if (lineup.get('confirmed') is True and all(len(lineup.get(str(i)) or []) >= 11 for i in ids.values())
            and (captured >= base_captured_at or not (current.get('lineup_learning') or {}).get('official_replaced_prediction'))):
        learned = result.setdefault('lineup_learning', {})
        for side, team_id in ids.items():
            learned[side+'_team_id'] = team_id
            learned[side+'_official_starters'] = copy.deepcopy(lineup[str(team_id)])
            learned[side+'_active_starting_xi'] = copy.deepcopy(lineup[str(team_id)])
        learned['official_replaced_prediction'] = True
        applied.append('lineup_learning')
    result['_shared_collection'] = {'captured_at': captured,
                                  'sections': copy.deepcopy(dossier.get('sections') or {}),
                                  'missing_sections': copy.deepcopy(dossier.get('missing_sections') or [])}
    return result, {'used': True, 'captured_at': captured,
                    'applied_sections': applied, 'policy': 'exact-shared-material-v21'}
