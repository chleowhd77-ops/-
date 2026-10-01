"""Read-only connection audit: never report old answers as newly learned."""
from pathlib import Path
from collections import Counter
from learning_state import read_json, state, versions, before_kickoff, now_iso, atomic_json


def build(root, dashboard=None, scorecard=None):
    root=Path(root)
    dashboard=dashboard if dashboard is not None else read_json(root/'dashboard_data.json')
    scorecard=scorecard if scorecard is not None else read_json(root/'grading_results.json').get('scorecard_v2',{})
    info=state(root)
    v3_picks=read_json(root/'v3_learning_picks.json').get('picks',{})
    rows=[]
    for source in ('proto','toto14'):
        current=versions(root,'TOTO14' if source=='toto14' else 'PROTO')
        for card in dashboard.get(source,[]):
            match=card.get('match') or {}
            if not before_kickoff(match):continue
            applied=card.get('learning_models') or {}
            for engine in ('official','robot','v2','v3'):
                wanted=current.get(engine)
                seen=applied.get(engine)
                if engine=='v3':
                    mid=str(match.get('id'))
                    if source=='toto14' and not mid.startswith('TOTO14_'):mid='TOTO14_'+mid
                    seen=(v3_picks.get(mid) or {}).get('model_version',seen)
                rows.append(dict(source=source,match_id=match.get('id'),home=match.get('home'),away=match.get('away'),
                    engine=engine,current_model=wanted,card_model=seen,
                    state='MODEL_UNAVAILABLE' if wanted=='legacy-unverified' else
                        'REFRESH_REQUIRED' if seen!=wanted else 'VERSION_LINKED',
                    notice='버전 연결만 검사; 실제 모델 호출 및 고객 노출 완료 증거와 구분'))
    report=dict(version='R7.13.14',generated_at=now_iso(),
        training={k:{field:v.get(field) for field in ('status','stage','active_version','last_review_at','reason')}
                  for k,v in (info.get('engines') or {}).items()},
        connection_counts=dict(Counter(r['state'] for r in rows)),scheduled_connections=rows,
        grading={track:{engine:cell.get('summary',{}) for engine,cell in cells.items()}
                 for track,cells in (scorecard.get('tracks') or {}).items()},
        grading_audit=scorecard.get('audit',{}),
        scope='저장된 결과만 집계. 미수집 결과/없는 과거 답안을 생성하지 않음.')
    atomic_json(root/'learning_connection_report.json',report)
    print(f"📋 학습·픽 연결 점검: {report['connection_counts']}",flush=True)
    return report
