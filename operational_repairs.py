"""Additive read-path indexes and explicit, read-only exam summaries."""
import time

INDEXES = {
    'robot_pre_match_observations': ('idx_robot_observation_lookup_r16', ('fixture_key','robot_pick_version','captured_timestamp','id')),
    'prediction_candidate_results': ('idx_candidate_known_r16', ('match_id','is_correct','graded_at')),
    'robot_learning_samples': ('idx_robot_history_r16', ('captured_timestamp','id')),
    'three_engine_pick_snapshots': ('idx_engine_history_r16', ('captured_timestamp','id')),
}


def ensure_read_indexes(conn):
    started = time.monotonic()
    created = []
    for table, (name, columns) in INDEXES.items():
        available = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if set(columns) <= available:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
            if not exists:
                conn.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {table}({",".join(columns)})')
                created.append(name)
    conn.commit()
    if created:
        print(f'✅ 조회 인덱스 준비 · {len(created)}개 · {time.monotonic()-started:.1f}초 · 기록 보존', flush=True)
    return created


def exam_rows(entries):
    """Do not repeat the overall selection test under each market label."""
    rows = []
    def add(label, n=None, accuracy=None, baseline=None, state='', note=''):
        pct = lambda v: f'{float(v)*100:.2f}%' if isinstance(v,(int,float)) else '—'
        rows.append({'분석가·시험':label, '시험 경기':n if n is not None else '—',
                     '정확도':pct(accuracy),'비교 기준':pct(baseline),'상태':state,'설명':note})
    official = entries.get('official') or {}
    policy = next(((v or {}).get('official_selection_policy') for v in (official.get('exam') or {}).values()
                   if isinstance(v,dict) and v.get('official_selection_policy')), {})
    add('공식 · 전 시장 선택 정책',policy.get('validation_fixtures'),policy.get('validation_accuracy'),
        policy.get('baseline_accuracy'),'적용' if policy.get('active') else '미적용',policy.get('reason','시험 기록 없음'))
    t = official.get('toto14_exam') or {}
    add('공식 · 승무패14',t.get('validation_fixtures'),t.get('validation_accuracy'),t.get('baseline_accuracy'),
        '적용' if t.get('active') else '미적용',t.get('reason','시험 기록 없음'))
    for key,label in [('robot_proto','자율 · 프로토'),('robot_toto14','자율 · 승무패14'),('v2','V2 · 승무패'),('v3','V3 · 전 시장')]:
        item=entries.get(key) or {}; exam=item.get('exam') or {}
        candidate=exam.get('candidate') or {}; incumbent=exam.get('incumbent') or {}
        n=exam.get('validation_fixtures',exam.get('matches',candidate.get('matches',candidate.get('samples'))))
        acc=exam.get('goal_validation_accuracy',candidate.get('accuracy',candidate.get('meta_accuracy')))
        note=exam.get('reason',item.get('reason',''))
        if exam.get('status')=='WAITING_PROSPECTIVE_EXAM':
            note=f"다음 시험용 새 경기 {exam.get('new_matches',0)}건 · 이전 시험 기록은 아래 상세 참조"
        add(label,n,acc,incumbent.get('accuracy',incumbent.get('meta_accuracy')),item.get('status','기록 없음'),note)
    for market,label in [('handicap','V2 · 핸디캡'),('totals','V2 · 언더오버')]:
        item=(((entries.get('v2') or {}).get('market_learning') or {}).get('markets') or {}).get(market) or {}
        ex=item.get('exam') or {}
        add(label,ex.get('matches'),ex.get('accuracy'),state=item.get('status','기록 없음'),
            note=f"시장 시험 {ex.get('market_cases',0)}건 · 실제 고객 누적 채점과 별도")
    return rows
