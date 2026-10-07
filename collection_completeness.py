"""Describe actual collected sections. Does not select picks or invent missing facts."""
import hashlib
import json
from datetime import datetime, timezone
from manager_material import dossier_value


def inspect(dossier):
    evidence = dossier.get('evidence') or {}
    teams = evidence.get('teams') or {}
    sections = {}
    try:
        hours = (datetime.fromisoformat(str(dossier.get('kickoff_at')).replace('Z', '+00:00'))
                 - datetime.now(timezone.utc)).total_seconds()/3600
    except (TypeError, ValueError):
        hours = 0  # Unknown time cannot justify deferring collection.
    for side in ('home', 'away'):
        team_id = (dossier.get(side) or {}).get('id')
        team = teams.get(str(team_id)) or {}
        history = team.get('recent_fixtures')
        sections[side+'.recent_fixtures'] = 'received' if isinstance(history, list) and history else 'empty_or_pending'
        stats = team.get('recent_stats') or {}
        sections[side+'.recent_stats'] = ('received' if stats.get('sample_size', 0) > 0
                                         else 'empty_or_pending' if dossier.get('deep_refresh_due') else 'not_due')
    for field in ('standings', 'league_key_players', 'h2h', 'injuries', 'lineups'):
        value = evidence.get(field)
        # Nonempty containers with only default/unknown fields are not proof of observations.
        if field == 'standings':
            received = bool(value) and all(isinstance(v, dict) and 0 < int(v.get('rank') or 0) < 99 for v in value.values())
        elif field == 'injuries':
            received = bool(value) and all(isinstance(v, dict) and v.get('available') is True for v in value.values())
        elif field == 'h2h':
            received = isinstance(value, dict) and (value.get('available') is True or value.get('total', 0) > 0)
        elif field == 'lineups':
            received = (isinstance(value, dict) and value.get('confirmed') is True
                        and sum(isinstance(v, list) and len(v) >= 11 for v in value.values()) >= 2)
        else:
            received = bool(value) or evidence.get('league_key_players_available') is True
        sections[field] = 'received' if received else 'empty_or_pending'
        if ((field in ('standings','league_key_players') and not dossier.get('deep_refresh_due'))
                or (field == 'lineups' and hours > 2)):
            if not received:
                sections[field] = 'not_due'
    identity = (bool(dossier.get('fixture_id')) and bool((dossier.get('home') or {}).get('id'))
                and bool((dossier.get('away') or {}).get('id'))
                and dossier['home']['id'] != dossier['away']['id'])
    core = identity and all(sections[s+'.recent_fixtures'] == 'received' for s in ('home', 'away'))
    return {'core_ready': bool(core), 'sections': sections,
            'missing_sections': [k for k, v in sections.items() if v == 'empty_or_pending'],
            'material_digest': hashlib.sha256(json.dumps(dossier_value(evidence), ensure_ascii=False,
                                      sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
