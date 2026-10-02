"""Offline, one-engine-at-a-time training. No sports API or UI work here."""
import csv
import faulthandler
import fcntl
import hashlib
import json
import math
import os
import pickle
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from learning_state import (CAMPAIGN, ENGINES, active_artifact, atomic_json,
                            digest, now_iso, read_json, state)
from scorecard_core import epoch, KST
from learning_runtime import LearningPaused, StageCache

_PROGRESS = None


def progress(stage):
    if _PROGRESS:
        _PROGRESS(stage)


def mark_interrupted(root, reason):
    """Scheduler calls this only after stopping the child process tree."""
    root = Path(root)
    with (root/'.learning_worker.lock').open('a') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        info = state(root)
        changed = False
        for engine, entry in (info.get('engines') or {}).items():
            if entry.get('status') == 'TRAINING':
                stage = entry.get('stage', '단계 기록 없음')
                entry.update(status='ERROR', reason=f'{reason} · {stage} · 기존 모델 유지',
                             last_failure_at=now_iso())
                print(f'❌ 학습 중단 [{engine}] · {reason} · 마지막 단계: {stage}', flush=True)
                changed = True
        if changed:
            info['updated_at'] = now_iso()
            atomic_json(root/'learning_status.json', info)
        return changed


def result_signature(root):
    with sqlite3.connect(f'file:{Path(root)/"ai_predictions.db"}?mode=ro', uri=True) as db:
        rows = db.execute("SELECT match_id,actual_result,actual_score FROM predictions WHERE actual_result='FINISHED' ORDER BY match_id").fetchall()
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = []
        for table in ('prediction_candidate_results','robot_learning_samples'):
            if table in tables:
                where = 'is_correct IN (0,1)' if table == 'prediction_candidate_results' else 'actual_home_goals IS NOT NULL'
                available = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                wanted = ('id','is_correct','graded_at') if table=='prediction_candidate_results' else (
                    'id','actual_home_goals','actual_away_goals','robot_pick_correct','result_known_timestamp')
                columns = [c for c in wanted if c in available]
                # Count/max alone miss corrected labels on already graded rows.
                counts.append((table,db.execute(f'SELECT {",".join(columns)} FROM {table} WHERE {where} ORDER BY id').fetchall()))
    textbook = Path(root)/'master_training_data.csv'
    textbook_hash = hashlib.sha256(textbook.read_bytes()).hexdigest() if textbook.exists() else 'missing'
    return digest(['R7.13.16', CAMPAIGN, rows, counts, textbook_hash])


def save_model(root, engine, value, binary=False):
    folder = Path(root)/'.learning_models'
    folder.mkdir(exist_ok=True)
    data = pickle.dumps(value) if binary else json.dumps(value,ensure_ascii=False,sort_keys=True).encode()
    name = engine+'-'+hashlib.sha256(data).hexdigest()[:20]+('.pkl' if binary else '.json')
    path = folder/name
    if not path.exists():
        temp = path.with_suffix(path.suffix+'.tmp')
        temp.write_bytes(data)
        os.replace(temp,path)
    return name


def metric_pass(candidate, baseline):
    return (candidate['accuracy'] >= baseline['accuracy'] and candidate['brier'] <= baseline['brier']
            and (candidate['accuracy'] > baseline['accuracy'] or candidate['brier'] < baseline['brier']))


def train_official(root, old):
    import collector as c
    candidate = c.load_market_performance(force_refresh=True)
    if not any('calibration_validated' in (candidate.get(k) or {}) for k in c.MARKET_LABELS):
        raise RuntimeError('공식 학습 후보·시간 검증 결과 생성 실패')
    previous = active_artifact(root,'official')
    deployed = dict(candidate)
    examinations = {}
    for market in c.MARKET_LABELS:
        fresh = dict(candidate.get(market) or {})
        prior = previous.get(market) or {}
        for key, flag in [('official_selection_policy','active'),('price_policy','active')]:
            if (prior.get(key) or {}).get(flag) and not (fresh.get(key) or {}).get(flag):
                fresh[key] = prior[key]
        if prior.get('calibration_validated') and not fresh.get('calibration_validated'):
            for key in ('calibration_bins','calibration_validated','validation_fixtures','baseline_brier','corrected_brier'):
                fresh[key] = prior.get(key)
        deployed[market] = fresh
        examinations[market] = {k:candidate[market].get(k) for k in
            ('samples','validation_fixtures','calibration_validated','baseline_brier','corrected_brier','official_selection_policy')}
    with sqlite3.connect(Path(root)/'ai_predictions.db') as db:
        toto = c._verified_toto14_probability_policy(db)
    if not toto.get('active') and (previous.get('_toto14') or {}).get('active'):
        deployed['_toto14'] = previous['_toto14']
    else:
        deployed['_toto14'] = toto
    if (previous.get('_movement_policy') or {}).get('active') and not (candidate.get('_movement_policy') or {}).get('active'):
        deployed['_movement_policy'] = previous['_movement_policy']
    name = save_model(root,'official',deployed)
    learned = any((deployed.get(k) or {}).get('calibration_validated') or
                  ((deployed.get(k) or {}).get('official_selection_policy') or {}).get('active') for k in c.MARKET_LABELS)
    return dict(artifact=name,active_version=name[:-5], status='READY' if learned else 'BASELINE',
                reason='검증된 확률·선택 정책 적용' if learned else '새 보정 검증 미통과·기초 분석 유지',
                training_samples=max((candidate.get(k) or {}).get('samples',0) for k in c.MARKET_LABELS),
                exam=examinations,toto14_exam=toto,scope='시장별 후보 보정; 승무패14 별도 정책')


def train_robot(root, old, source):
    import collector as c
    engine = 'robot_toto14' if source=='TOTO14' else 'robot_proto'
    checkpoint = StageCache(root, engine, notify=progress)
    c._AUTONOMOUS_ROBOT_CACHE.clear()
    artifact = c._load_autonomous_robot_artifact(source,serving_only=False,checkpoint=checkpoint)
    if artifact.get('reason') == '학습표본 저장소 확인 대기':
        raise RuntimeError('자율로봇 학습 저장소 오류: '+str(artifact.get('failure_detail') or '상세 기록 없음'))
    previous = active_artifact(root,engine)
    # Compare the candidate's untouched chronological exam, never its final
    # all-history refit (which has already seen those exam outcomes).
    challenger = bool(artifact.get('deployment_eligible')) and not artifact.get('latest_challenger_reason')
    comparison = {}
    if challenger and previous.get('parameters'):
        import football_model as fm
        count = int(artifact.get('validation_fixtures') or 0)
        rows = [r for r in robot_validation_rows(root,source) if r.get('candidates')]
        keys = artifact.get('goal_validation_fixture_keys') or []
        exam_rows = [r for r in rows if r['fixture_key'] in keys]
        if len(exam_rows)!=len(keys):
            exam_rows=[]
        left = (artifact.get('model_family_validation') or {}).get(artifact.get('selected_model_family')) or {}
        boundary = float(old.get('trained_through') or float('inf'))
        if len(exam_rows) < 20 or min(r['kickoff'] for r in exam_rows) <= boundary or not left.get('samples'):
            challenger = False
            comparison['reason'] = '기존 모델 학습 이후 겹치지 않는 새 시험 경기 20건 대기'
        else:
            right = fm._robot_frozen_pick_metrics(exam_rows,previous['parameters'])
            comparison = {'candidate':left,'incumbent':right,'matches':len(exam_rows),
                          'method':'candidate-heldout-before-refit-versus-incumbent'}
            challenger = metric_pass(left,right)
    artifact = dict(artifact)
    validating = bool(artifact.get('parameters') and artifact.get('samples') and not challenger)
    if validating:
        artifact.update(active=True,validation_status='VALIDATING',
                        reason='자체 학습 결과 공개·실전 채점 검증 중')
    with sqlite3.connect(Path(root)/'ai_predictions.db') as db:
        where,params = c._robot_track_sql(source)
        last = db.execute(f'SELECT MAX(kickoff_timestamp) FROM robot_learning_samples WHERE actual_home_goals IS NOT NULL AND {where}',params).fetchone()[0]
    artifact['grading_experience_samples'] = (artifact.get('grading_experience') or {}).get('samples',0)
    artifact['learning_revision_marker'] = digest(artifact)
    name = save_model(root,engine,artifact)
    return dict(artifact=name,previous_artifact=old.get('artifact'),active_version=name[:-5],
                status='VALIDATING' if validating else 'READY' if artifact.get('deployment_eligible') else 'BASELINE',
                training_samples=artifact.get('samples',0),exam=comparison or {
                    k:artifact.get(k) for k in ('validation_fixtures','goal_validation_accuracy','deployment_eligible','reason')},
                trained_through=last,
                reason=artifact.get('reason',''),
                scope='승무패14 전용' if source=='TOTO14' else '프로토 전 시장')


def robot_validation_rows(root, source):
    import collector as c
    import football_model as fm
    with sqlite3.connect(Path(root)/'ai_predictions.db') as db:
        where,params=c._robot_track_sql(source,'sample')
        schemas=tuple(c.ROBOT_COMPATIBLE_FEATURE_SCHEMAS)
        marks=','.join('?' for _ in schemas)
        rows=db.execute(f'''SELECT sample.fixture_key,sample.kickoff_timestamp,
            COALESCE(observation.captured_timestamp,sample.captured_timestamp),sample.result_known_timestamp,
            COALESCE(observation.features_json,sample.features_json),sample.actual_home_goals,
            sample.actual_away_goals,sample.candidate_results_json
            FROM robot_learning_samples sample LEFT JOIN robot_pre_match_observations observation
            ON observation.id=(SELECT latest.id FROM robot_pre_match_observations latest
                WHERE latest.fixture_key=sample.fixture_key AND latest.robot_pick_version=sample.robot_pick_version
                  AND latest.captured_timestamp<sample.kickoff_timestamp
                ORDER BY latest.captured_timestamp DESC,latest.id DESC LIMIT 1)
            WHERE sample.actual_home_goals IS NOT NULL AND sample.actual_away_goals IS NOT NULL
                AND sample.feature_schema_version IN ({marks}) AND {where}
            ORDER BY sample.kickoff_timestamp,sample.id''',schemas+tuple(params)).fetchall()
    examples=[]
    for key,ko,at,known,features,h,a,candidates in rows:
        try: examples.append(dict(fixture_key=key,kickoff=ko,captured_at=at,known_at=known,
            features=json.loads(features),home_goals=h,away_goals=a,candidates=json.loads(candidates or '[]')))
        except (TypeError,ValueError): continue
    return fm._clean_robot_examples(examples)


def load_v2_rows(root):
    rows, excluded = {}, Counter()
    archive_keys = {}
    def identity(day, home, away):
        return (day, ' '.join(str(home).casefold().split()), ' '.join(str(away).casefold().split()))
    textbook=Path(root)/'master_training_data.csv'
    if textbook.exists():
        with textbook.open(encoding='utf-8-sig') as stream:
            for r in csv.DictReader(stream):
                try:
                    date=None
                    for fmt in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
                        try: date=datetime.strptime(r['Date'],fmt).replace(tzinfo=timezone.utc);break
                        except ValueError: pass
                    odds=[float(r[k]) for k in ('B365H','B365D','B365A')]
                    if not date or date.timestamp()>=time.time() or r['FTR'] not in ('H','D','A') or not all(math.isfinite(x) and x>1 for x in odds):
                        raise ValueError()
                    key=(date.date().isoformat(),r['HomeTeam'],r['AwayTeam'])
                    normalized=identity(*key)
                    if normalized in archive_keys:
                        excluded['duplicate_archive_fixture']+=1
                        continue
                    archive_keys[normalized]=key
                    rows[key]=dict(time=date.timestamp(),known=date.timestamp()+86400,odds=odds,label=r['FTR'],source='archive_B365',key=key)
                except (KeyError,TypeError,ValueError): excluded['invalid_archive_row']+=1
    progress('V2 저장 배당 조회 시작')
    with sqlite3.connect(f'file:{Path(root)/"ai_predictions.db"}?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        snapshots=db.execute('''SELECT p.match_id,p.match_time,p.home_team,p.away_team,p.actual_score,
            p.api_fixture_id,g.known_at,s.odd_h,s.odd_d,s.odd_a,s.created_at,s.id,s.api_fixture_id AS snapshot_fixture
            FROM predictions p JOIN prediction_snapshots s ON s.match_id=p.match_id
            LEFT JOIN (SELECT match_id,MAX(graded_at) AS known_at FROM prediction_candidate_results
                       WHERE is_correct IN (0,1) GROUP BY match_id) g ON g.match_id=p.match_id
            WHERE p.actual_result='FINISHED' ORDER BY s.id DESC''').fetchall()
    progress(f'V2 저장 배당 {len(snapshots)}행 조회 완료')
    fixtures=set()
    for r in snapshots:
        ko=epoch(r['match_time'],KST);captured=epoch(r['created_at']);known=epoch(r['known_at'])
        try:
            odds=[float(r[k]) for k in ('odd_h','odd_d','odd_a')]
            h,a=map(int,str(r['actual_score']).split(':'))
            if not 0<captured<ko<known<=time.time() or not all(math.isfinite(x) and x>1 for x in odds) or not r['api_fixture_id'] or r['snapshot_fixture']!=r['api_fixture_id']:
                raise ValueError()
        except (TypeError,ValueError):excluded['invalid_or_postkickoff_site_row']+=1;continue
        fid=int(r['api_fixture_id'])
        if fid in fixtures:continue
        fixtures.add(fid)
        # Only exact normalized names and date may deduplicate an archive row.
        # No guessing across translated names, competitions or adjacent dates.
        day=datetime.fromtimestamp(ko,timezone.utc).date().isoformat()
        archive_key=archive_keys.get(identity(day,r['home_team'],r['away_team']))
        if archive_key in rows:
            del rows[archive_key]
            excluded['archive_duplicate_replaced_by_verified_site_snapshot']+=1
        key=('fixture',fid)
        rows[key]=dict(time=ko,known=known,odds=odds,label='H' if h>a else 'A' if a>h else 'D',
                       source='site_snapshot_mixed_provider',key=key)
    return sorted(rows.values(),key=lambda r:r['time']),dict(excluded)


def deployed_binary(root, old):
    name=old.get('artifact')
    if name and Path(name).name==name and old.get('trained_through'):
        with (Path(root)/'.learning_models'/name).open('rb') as stream:
            return pickle.load(stream)
    return None


def train_v2(root, old):
    import pandas as pd
    from sklearn.ensemble import RandomForestClassifier
    progress('V2 승무패 자료 읽기')
    rows,excluded=load_v2_rows(root)
    progress(f'V2 유효 학습 자료 {len(rows)}경기 준비')
    if len(rows)<100:
        return dict(status='WAITING_DATA',reason='시간순 학습·시험용 유효 경기 100건 미만',training_samples=len(rows),excluded=excluded)
    incumbent=deployed_binary(root,old)
    fresh=[r for r in rows if r['time']>float(old.get('trained_through') or 0)]
    if incumbent is not None and len(fresh)<20:
        return dict(status='RETAINED',reason='기존 모델 학습 이후 새 시험 경기 20건 대기',training_samples=len(rows),
                    exam={'status':'WAITING_PROSPECTIVE_EXAM','new_matches':len(fresh)})
    cut=int(len(rows)*.6);cut2=int(len(rows)*.8)
    train=[r for r in rows[:cut] if r['known']<rows[cut]['time']]
    final=rows[cut2:]
    tune=[r for r in rows[cut:cut2] if r['known']<final[0]['time']]
    if incumbent is not None:
        final=fresh[-max(20,min(120,len(fresh)//5)):]
    combined=[r for r in rows if r['known']<final[0]['time']]
    columns=['B365H','B365D','B365A']
    def frame(part):return pd.DataFrame([r['odds'] for r in part],columns=columns)
    def fit(part,leaf):
        return RandomForestClassifier(n_estimators=100,min_samples_leaf=leaf,random_state=42,n_jobs=1).fit(frame(part),[r['label'] for r in part])
    def score(model,part):
        if not part:return None
        predictions=model.predict(frame(part));probs=model.predict_proba(frame(part))
        classes=list(model.classes_)
        return dict(matches=len(part),accuracy=sum(p==r['label'] for p,r in zip(predictions,part))/len(part),
                    brier=sum(sum(((float(p[classes.index(k)]) if k in classes else 0.0)-int(r['label']==k))**2 for k in ('H','D','A')) for p,r in zip(probs,part))/len(part))
    if len(train)<40 or not tune or len(final)<20 or len({r['label'] for r in train})<3:
        return dict(status='WAITING_DATA',training_samples=len(rows),reason='시간·클래스 분리 후 학습 또는 시험 표본 부족',excluded=excluded)
    progress('V2 승무패 시간순 검증·시험')
    choices=[(leaf,score(fit(train,leaf),tune)) for leaf in (1,4,8)]
    leaf, _=max(choices,key=lambda x:(x[1]['accuracy'],-x[1]['brier']))
    baseline_leaf=int(old.get('parameters',{}).get('min_samples_leaf',1))
    model=fit(combined,leaf);baseline=incumbent if incumbent is not None else fit(combined,baseline_leaf)
    new_score=score(model,final);old_score=score(baseline,final)
    passed=metric_pass(new_score,old_score)
    site=[r for r in final if r['source'].startswith('site')]
    source_exam={src:dict(candidate=score(model,[r for r in final if r['source']==src]),
                          incumbent=score(baseline,[r for r in final if r['source']==src])) for src in {r['source'] for r in final}}
    if len(site)>=20:
        a,b=score(model,site),score(baseline,site)
        passed=passed and a['accuracy']>=b['accuracy'] and a['brier']<=b['brier']
    info=dict(status='RETAINED',reason='새 모델 시간순 비교 미통과·기존 V2 유지',training_samples=len(rows),
        excluded=excluded,exam={'candidate':new_score,'incumbent':old_score,'passed':passed,'by_source':source_exam},
        source_counts=dict(Counter(r['source'] for r in rows)),scope='승무패만·배당 세 값',
        source_notice='B365 역사 배당과 사이트 배당은 공급사·시점이 다를 수 있으며 출처별 시험을 별도 표시',
        exam_method=('기존 승인 모델이 학습하지 않은 새 경기만 비교' if incumbent is not None else '기존 학습 기간 불명: 같은 과거 구간으로 설정 재훈련·미래 구간 비교'))
    name=save_model(root,'v2',fit(rows,leaf),binary=True)
    info.update(status='READY' if passed else 'VALIDATING',
                reason='시간순 비교 통과' if passed else '새 학습 모델 공개·실전 채점 검증 중',
                previous_artifact=old.get('artifact'),artifact=name,active_version=name[:-4],
                parameters={'min_samples_leaf':leaf},trained_through=max(r['time'] for r in rows))
    return info


def train_v3(root, old):
    import official_meta_v3 as meta
    data,audit=meta.load_frozen_examples(Path(root)/'ai_predictions.db')
    deployed=deployed_binary(root,old)
    fresh=[g for g in meta._group_examples(data) if g[0].kickoff_timestamp>float(old.get('trained_through') or 0)]
    if deployed is not None and len(fresh)<20:
        return dict(status='RETAINED',reason='기존 V3 학습 이후 새 시험 경기 20건 대기',training_samples=audit['usable_matches'],
                    exam={'status':'WAITING_PROSPECTIVE_EXAM','new_matches':len(fresh)})
    try:
        exam=meta.run_chronological_exam(data)
    except meta.DataReadinessError as error:
        return dict(status='WAITING_DATA',reason=str(error),training_samples=audit['usable_matches'])
    if exam.get('status')!='EXAM_COMPLETE':
        return dict(status='WAITING_DATA',reason='V3 시간순 시험 표본 대기',training_samples=audit['usable_matches'],exam=exam)
    train,tune,final=meta.chronological_split(data)
    past=meta._flatten(train+tune)
    if deployed is not None:
        final=fresh[-max(20,min(120,len(fresh)//5)):]
        boundary=final[0][0].kickoff_timestamp
        past=[r for r in data if r.kickoff_timestamp<boundary and r.result_known_timestamp<boundary]
    encoder=meta.FrozenFeatureEncoder().fit(past)
    new_config=exam['selected_config']
    old_config=old.get('parameters') or {'iterations':72,'learning_rate':.05,'min_leaf':24}
    # Both settings see only the same past. A legacy model trained on the final
    # block must never be scored on that block as if it had never seen it.
    candidate=meta.ChallengerModel(**new_config).fit(encoder.transform(past),[r.label for r in past])
    if deployed is not None:
        incumbent,incumbent_encoder,_=deployed
    else:
        incumbent=meta.ChallengerModel(**old_config).fit(encoder.transform(past),[r.label for r in past])
        incumbent_encoder=encoder
    a=meta._evaluate(candidate,encoder,final);b=meta._evaluate(incumbent,incumbent_encoder,final)
    passed=metric_pass({'accuracy':a['meta_accuracy'],'brier':a['meta_brier']},
                       {'accuracy':b['meta_accuracy'],'brier':b['meta_brier']})
    by_market={}
    for market in ('1x2','handicap','totals'):
        groups=[[r for r in g if r.market_key==market] for g in final]
        groups=[g for g in groups if g]
        if groups:
            from dataclasses import replace
            # Market-only exams need a reference within that market. The main
            # official reference may be in another market; its absence is not
            # a reason to fail this candidate-versus-incumbent comparison.
            groups=[[replace(r,baseline_selected=r is max(g,key=lambda x:x.features.get('model_probability',0))) for r in g] for g in groups]
            by_market[market]={'candidate':meta._evaluate(candidate,encoder,groups),'incumbent':meta._evaluate(incumbent,incumbent_encoder,groups)}
    for cell in by_market.values():
        a_market,b_market=cell['candidate'],cell['incumbent']
        passed=passed and a_market['meta_accuracy']>=b_market['meta_accuracy'] and a_market['meta_brier']<=b_market['meta_brier']
    info=dict(status='RETAINED',reason='새 V3 시간순 비교 미통과·기존 모델 유지',training_samples=audit['usable_matches'],
        source_audit=audit,exam={'candidate':a,'incumbent':b,'passed':passed,'by_market':by_market},
        exam_method=('기존 승인 V3가 학습하지 않은 새 경기만 비교' if deployed is not None else '기존 학습 기간 불명: 같은 과거 구간으로 설정 재훈련·미래 구간 비교'),scope='승무패·핸디캡·언오버 후보 평가')
    legacy=Path(root)/'.v3_serving_cache.pkl'
    encoder=meta.FrozenFeatureEncoder().fit(data)
    model=meta.ChallengerModel(**new_config).fit(encoder.transform(data),[r.label for r in data])
    summary=dict(completed_matches=audit['usable_matches'],completed_candidates=len(data),training_config=new_config,
                 historical_exam=exam,learner_backend=model.backend,source_audit=audit)
    name=save_model(root,'v3',(model,encoder,summary),binary=True)
    info.update(status='READY' if passed else 'VALIDATING',
                reason='시간순 비교 통과' if passed else '새 학습 모델 공개·실전 채점 검증 중',
                previous_artifact=old.get('artifact'),artifact=name,active_version=name[:-4],parameters=new_config,trained_through=max(r.kickoff_timestamp for r in data))
    return info


def run_one(root, on_status=None):
    global _PROGRESS
    root=Path(root).resolve()
    with (root/'.learning_worker.lock').open('a') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        info=state(root)
        from offline_mode import enabled as offline_enabled
        info.update(schema_version='learning-status.v1',campaign=CAMPAIGN,
                    operating_mode='stored-data-only' if offline_enabled(root) else 'normal')
        engines=info.setdefault('engines',{})
        from operational_repairs import ensure_read_indexes
        with sqlite3.connect(root/'ai_predictions.db') as db:
            ensure_read_indexes(db)
        signature=result_signature(root)
        interrupted=False
        for entry in engines.values():
            if entry.get('status')=='TRAINING':
                entry.update(status='ERROR',reason='이전 학습 작업이 중단됨·기존 모델 유지',last_failure_at=now_iso())
                interrupted=True
        if interrupted:
            atomic_json(root/'learning_status.json',info)
            if on_status: on_status()
        # An error's cooldown applies even when the result signature changed.
        # Otherwise one broken learner is retried on every scheduler cycle.
        pending=[e for e in ENGINES
                 if ((engines.get(e) or {}).get('status') != 'ERROR'
                     or time.time()-epoch((engines.get(e) or {}).get('last_attempt_at')) > 1800)
                 and ((engines.get(e) or {}).get('reviewed_signature') != signature
                      or (engines.get(e) or {}).get('status') in ('ERROR','PAUSED')
                      or (e=='v2' and ((engines.get(e) or {}).get('market_learning') or {}).get('status')=='ERROR'
                          and time.time()-epoch((engines.get(e) or {}).get('last_attempt_at'))>1800))]
        if not pending:
            failures = [(e,engines.get(e) or {}) for e in ENGINES
                        if (engines.get(e) or {}).get('status')=='ERROR'
                        or ((engines.get(e) or {}).get('market_learning') or {}).get('status')=='ERROR']
            if failures:
                for name,entry in failures:
                    wait = max(0,int(1800-(time.time()-epoch(entry.get('last_attempt_at')))))
                    reason = (entry.get('market_learning') or {}).get('reason') if entry.get('status')!='ERROR' else entry.get('reason')
                    print(f'⚠️ 학습 재시도 대기 [{name}] · {wait}초 후 재시도 · {reason} · 기존 모델 유지',flush=True)
            else:
                print('📚 학습 대기: 새 정산 자료 없음 · 기존 모델/중간 결과 보존',flush=True)
            return info
        engine=min(pending,key=lambda e:(engines.get(e) or {}).get('last_attempt_at',''))
        old=dict(engines.get(engine) or {})
        started=now_iso()
        engines[engine]={**old,'status':'TRAINING','last_attempt_at':started,'stage':'자료 읽기',
                         'reason':'저장된 경기 자료로 학습 중'}
        info['updated_at']=started
        atomic_json(root/'learning_status.json',info)
        if on_status: on_status()
        print(f'📚 학습 시작 [{engine}] · 새 결과 묶음 {signature}',flush=True)
        monotonic_start = time.monotonic()
        def notify(stage):
            engines[engine].update(stage=stage, stage_updated_at=now_iso(),
                elapsed_seconds=round(time.monotonic()-monotonic_start,1))
            info['updated_at']=now_iso()
            atomic_json(root/'learning_status.json',info)
            print(f'📚 학습 단계 [{engine}] {stage} · {engines[engine]["elapsed_seconds"]}초',flush=True)
        _PROGRESS = notify
        functions={'official':train_official,'v2':train_v2,'v3':train_v3,
                   'robot_proto':lambda r,o:train_robot(r,o,'PROTO'),
                   'robot_toto14':lambda r,o:train_robot(r,o,'TOTO14')}
        try:
            # Stack traces show the actual slow call if an unexpected DB/model
            # operation stalls, before the scheduler's 600-second hard limit.
            faulthandler.dump_traceback_later(120, repeat=True)
            from threadpoolctl import threadpool_limits
            from offline_mode import no_training_network, training_root
            with threadpool_limits(limits=1), no_training_network(), training_root(root):
                result=functions[engine](root,old)
                if engine == 'v2':
                    # Persist the completed WDL result before slower independent courses.
                    engines[engine] = {**old, **result, 'status':'TRAINING',
                        'wdl_status':result.get('status'), 'last_attempt_at':started}
                    atomic_json(root/'learning_status.json',info)
                    from analyst_curriculum import prepare, load_exercises
                    from v2_market_learning import train
                    try:
                        progress('V2 핸디·언오버 시험 자료 읽기')
                        exercises = load_exercises(root)
                        progress(f'V2 확장 시험 자료 {len(exercises[0])}문항 준비')
                        result['course'] = prepare(root, exercises=exercises)
                        result['market_learning'] = train(root, exercises=exercises, notify=progress)
                    except Exception as error:
                        result['market_learning'] = {'status':'ERROR','reason':str(error)}
                        print(f'⚠️ V2 확장 학습 준비 실패: {type(error).__name__}: {error}',flush=True)
            if result.get('exam',{}).get('status') == 'WAITING_PROSPECTIVE_EXAM' and old.get('exam',{}).get('status') != 'WAITING_PROSPECTIVE_EXAM':
                result['last_completed_exam'] = old.get('exam')
            engines[engine]={**old,**result,'stage':'검토 완료','last_attempt_at':started,'last_review_at':now_iso(),
                             'reviewed_signature':signature,'campaign_reviewed':CAMPAIGN}
            if result.get('status') == 'READY':
                engines[engine]['last_success_at']=now_iso()
        except LearningPaused as paused:
            engines[engine].update(status='PAUSED',stage=str(paused),
                reason='학습 중간 결과 저장 완료·다음 차례에 이어서 진행·기존 모델 유지')
            print(f'⏸️ 학습 중간 저장 [{engine}] · {paused} · 수집 작업에 차례 반환',flush=True)
        except Exception as error:
            engines[engine]={**old,'status':'ERROR','reason':f'{type(error).__name__}: {error}',
                             'last_attempt_at':started,'last_failure_at':now_iso(),
                             'reviewed_signature':signature}
            print(f'⚠️ 학습 실패 [{engine}] · 기존 모델 유지 · {type(error).__name__}: {error}',flush=True)
        finally:
            faulthandler.cancel_dump_traceback_later()
            _PROGRESS = None
        info['refresh_ready']=all((engines.get(e) or {}).get('campaign_reviewed')==CAMPAIGN for e in ENGINES)
        info['updated_at']=now_iso()
        atomic_json(root/'learning_status.json',info)
        label = '학습 일시 저장' if engines[engine]['status']=='PAUSED' else '학습 검토 종료'
        print(f"📚 {label} [{engine}] · {engines[engine]['status']} · {engines[engine].get('reason','')} · 경기전 1회 갱신 준비={info['refresh_ready']}",flush=True)
        print(f"📚 학습 증빙 [{engine}] · 표본={engines[engine].get('training_samples',0)} · 모델={engines[engine].get('active_version','없음')} · 저장={bool(engines[engine].get('artifact'))}",flush=True)
        return info
