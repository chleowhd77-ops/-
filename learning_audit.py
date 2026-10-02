"""Read-only connection audit: never report old answers as newly learned."""
from pathlib import Path
from collections import Counter
from learning_state import read_json, state, versions, before_kickoff, now_iso, atomic_json, ENGINES


def build(root, dashboard=None, scorecard=None):
    root=Path(root)
    dashboard=dashboard if dashboard is not None else read_json(root/'dashboard_data.json')
    scorecard=scorecard if scorecard is not None else read_json(root/'grading_results.json').get('scorecard_v2',{})
    info=state(root)
    market_models=read_json(root/'v2_market_status.json').get('markets') or {}
    def v2_target(pick, default):
        if pick.get('market_key') in ('totals','handicap'):
            return (market_models.get(pick['market_key']) or {}).get('model_version','legacy-unverified')
        return default
    v3_picks=read_json(root/'v3_learning_picks.json').get('picks',{})
    rows=[]
    missing_inputs=[]
    for source in ('proto','toto14','top3'):
        current=versions(root,'TOTO14' if source=='toto14' else 'PROTO')
        for card in dashboard.get(source,[]):
            match=card.get('match') or {}
            if not before_kickoff(match):continue
            applied=card.get('learning_models') or {}
            if (card.get('alphago_pick') or {}).get('status')!='ready':
                missing_inputs.append(dict(source=source,match_id=match.get('id'),
                    home=match.get('home'),away=match.get('away'),
                    reason=card.get('v2_refresh_wait_reason') or (card.get('alphago_pick') or {}).get('reason'),
                    collection=card.get('v2_collection_diagnostic') or {}))
            for engine in ('official','robot','v2','v3'):
                wanted=current.get(engine)
                seen=applied.get(engine)
                if engine=='v2':
                    answer=(card.get('alphago_pick') if source=='toto14' else
                            card.get('v2_market_pick') or card.get('alphago_pick')) or {}
                    seen=answer.get('model_version',seen)
                    wanted=v2_target(answer,wanted)
                if engine=='v3':
                    mid=str(match.get('id'))
                    if source=='toto14' and not mid.startswith('TOTO14_'):mid='TOTO14_'+mid
                    seen=(v3_picks.get(mid) or {}).get('model_version',seen)
                rows.append(dict(source=source,match_id=match.get('id'),home=match.get('home'),away=match.get('away'),
                    engine=engine,current_model=wanted,card_model=seen,
                    state='MODEL_UNAVAILABLE' if wanted=='legacy-unverified' else
                        'REFRESH_REQUIRED' if seen!=wanted else 'VERSION_LINKED',
                    notice='버전 연결만 검사; 실제 모델 호출 및 고객 노출 완료 증거와 구분'))
    for pick in (read_json(root/'manager_investment_picks.json').get('picks') or {}).values():
        if not isinstance(pick,dict) or not before_kickoff(pick):continue
        engine=pick.get('engine_key') or pick.get('analyst')
        if engine not in ('official','robot','v2','v3'):continue
        wanted=versions(root).get(engine);seen=pick.get('model_version')
        if engine=='v2':wanted=v2_target(pick,wanted)
        rows.append(dict(source='manager',match_id=pick.get('match_id'),engine=engine,
            home=pick.get('home'),away=pick.get('away'),current_model=wanted,card_model=seen,
            state='MODEL_UNAVAILABLE' if wanted=='legacy-unverified' else
                'REFRESH_REQUIRED' if seen!=wanted else 'VERSION_LINKED',
            notice='시장 전용 V2 모델은 별도 시장 모델 상태와 비교 필요'))
    training={}
    for engine in ENGINES:
        entry=(info.get('engines') or {}).get(engine) or {}
        training[engine]={field:entry.get(field) for field in
            ('status','stage','active_version','last_review_at','reason','training_samples','exam','market_learning')}
        name=entry.get('artifact')
        training[engine]['artifact_exists']=bool(name and Path(name).name==name and (root/'.learning_models'/name).is_file())
        if not entry:training[engine]['status']='NOT_REVIEWED'
    report=dict(version='R7.13.15',generated_at=now_iso(),training=training,
        v2_missing_inputs=missing_inputs,
        course=read_json(root/'learning_course_status.json'),
        connection_counts=dict(Counter(r['state'] for r in rows)),scheduled_connections=rows,
        grading={track:{engine:cell.get('summary',{}) for engine,cell in cells.items()}
                 for track,cells in (scorecard.get('tracks') or {}).items()},
        grading_audit=scorecard.get('audit',{}),
        grading_pending_reasons={track:{engine:dict(Counter(
            row.get('grading_wait_reason') or '사유 미기록' for row in cell.get('rows',[])
            if row.get('is_correct') not in (0,1))) for engine,cell in cells.items()}
            for track,cells in (scorecard.get('tracks') or {}).items()},
        scope='저장된 결과만 집계. 미수집 결과/없는 과거 답안을 생성하지 않음.')
    atomic_json(root/'learning_connection_report.json',report)
    print(f"📋 학습·픽 연결 점검: {report['connection_counts']}",flush=True)
    return report
