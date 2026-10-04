"""Restore absent sections from timestamped evidence for the exact same fixture.

This does not fill explicit empty/unknown fields or invent fresh observations.
Older section timestamps remain attached so analysts can judge their relevance.
"""
import copy
import json

SECTIONS = ('competition', 'teams', 'match_identity', 'environment', 'recent_form',
            'recent_match_stats', 'long_term', 'standings', 'motivation',
            'h2h_and_matchup', 'injuries_and_absences', 'lineup_learning',
            'squads', 'schedule_and_travel', 'managers')


def recover_evidence(db, fixture_key, version, kickoff, cutoff, current):
    result = copy.deepcopy(current)
    origins = copy.deepcopy(result.get('_section_recovery') or {})
    absent = set(SECTIONS) - set(result)
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='robot_pre_match_observations'").fetchone()
    if absent and exists and fixture_key and version:
        rows = db.execute('''SELECT id,captured_timestamp,full_evidence_json
            FROM robot_pre_match_observations
            WHERE fixture_key=? AND robot_pick_version=? AND kickoff_timestamp=?
              AND captured_timestamp>0 AND captured_timestamp<=?
              AND captured_timestamp<kickoff_timestamp
            ORDER BY captured_timestamp DESC,id DESC''',
            (fixture_key, version, kickoff, cutoff))
        for oid, captured, raw in rows:
            try:
                old = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(old, dict):
                continue
            for section in sorted(absent & set(old)):
                result[section] = old[section]
                inherited = (old.get('_section_recovery') or {}).get(section)
                origins[section] = inherited or {'observation_id': oid,
                                                'captured_timestamp': captured}
                absent.remove(section)
            if not absent:
                break
    if origins:
        result['_section_recovery'] = origins
    return result, {'restored_sections': origins,
                    'absent_sections': sorted(set(SECTIONS) - set(result)),
                    'policy': 'exact-fixture-prior-section-preservation-v1'}
