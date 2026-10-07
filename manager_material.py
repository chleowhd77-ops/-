"""Stable data comparison only; original evidence and analyst decisions stay intact."""
import copy


SOURCE_CAPTURE_FIELDS = {'sample_id','snapshot_id','observation_id','evidence_captured_at',
                         'odds_captured_at','evidence_storage'}


def _without_origin_numbers(value):
    if not isinstance(value, dict):
        return value
    return {k:{a:b for a,b in v.items() if a not in ('observation_id','captured_timestamp')}
            if isinstance(v,dict) else v for k,v in value.items()}


def evidence_value(evidence):
    value = copy.deepcopy(evidence)
    if not isinstance(value,dict): return value
    if '_section_recovery' in value:
        value['_section_recovery'] = _without_origin_numbers(value['_section_recovery'])
    markets = value.get('markets') or {}
    snapshot = markets.get('current_snapshot') if isinstance(markets, dict) else None
    if isinstance(snapshot,dict): snapshot.pop('fetched_at',None)
    movement = markets.get('time_series_and_reverse_signals') if isinstance(markets, dict) else None
    if isinstance(movement,dict): movement.pop('analysis_stage',None)
    for side in ('home','away'):
        stats = (value.get('recent_match_stats') or {}).get(side)
        if isinstance(stats,dict): stats.pop('observed_at',None)
    return value


def question_value(question):
    value = copy.deepcopy(question)
    source = {k:v for k,v in value.get('source',{}).items() if k not in SOURCE_CAPTURE_FIELDS}
    recovery = source.get('evidence_recovery')
    if isinstance(recovery,dict) and 'restored_sections' in recovery:
        recovery['restored_sections'] = _without_origin_numbers(recovery['restored_sections'])
    value['source'] = source
    if 'evidence' in value: value['evidence'] = evidence_value(value['evidence'])
    return value


def dossier_value(evidence):
    value = copy.deepcopy(evidence)
    for team in (value.get('teams',{}) or {}).values():
        if not isinstance(team,dict): continue
        for field in ('recent_stats','recent_metrics'):
            if isinstance(team.get(field),dict): team[field].pop('observed_at',None)
    return value
