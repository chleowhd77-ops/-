"""Independent V2 price models for totals and three-way handicap markets."""
import math
import pickle
from pathlib import Path
from collections import defaultdict
from analyst_curriculum import load_exercises, temporal_split, market_line
from learning_state import atomic_json, digest, read_json, now_iso


def features(row):
    # Only observed price, side and line. Never use another analyst's probability.
    return {'odd':float(row['odd']),'line':float(row['line']),
            'side':str(row['selection_side']),'market':str(row['market_key'])}


def train(root, exercises=None, notify=None):
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.pipeline import make_pipeline
    root=Path(root)
    rows,audit=exercises if exercises is not None else load_exercises(root)
    old=read_json(root/'v2_market_status.json')
    report=dict(version='v2-markets-v1',updated_at=now_iso(),markets=dict(old.get('markets') or {}),source_audit=audit)
    for market in ('totals','handicap'):
        if notify: notify('V2 ' + market + ' 학습·시험')
        sample=[r for r in rows if r['inputs']['market_key']==market
            and r['inputs']['selection_side'] in (('over','under') if market=='totals' else ('home','draw','away'))]
        # Three-way integer handicap and half-goal totals avoid push/half-settlement mislabels.
        sample=[r for r in sample if (float(r['inputs']['line']).is_integer() if market=='handicap'
                   else abs(r['inputs']['line']%1-.5)<1e-9)]
        revision=digest(['v2-markets-v1',sample])
        previous=report['markets'].get(market) or {}
        if previous.get('data_revision')==revision:
            continue
        split,boundaries=temporal_split(sample)
        past=split['train']+split['tune'];exam=split['exam']
        n=len({r['fixture_id'] for r in past})
        if n<20 or len({r['answer'] for r in past})<2 or not exam:
            report['markets'][market]={**previous,'status':'WAITING_DATA','training_matches':n,
                'reason':'시장별 경기 전 배당·기준점·정답 표본 부족','data_revision':revision}
            continue
        def fit(part):
            model=make_pipeline(DictVectorizer(),RandomForestClassifier(
                n_estimators=80,min_samples_leaf=4,random_state=71313,n_jobs=1))
            counts=defaultdict(int)
            for r in part:counts[r['fixture_id']]+=1
            model.fit([features(r['inputs']) for r in part],[r['answer'] for r in part],
                randomforestclassifier__sample_weight=[1/counts[r['fixture_id']] for r in part])
            return model
        tested=fit(past)
        probabilities=tested.predict_proba([features(r['inputs']) for r in exam])[:,list(tested.classes_).index(1)]
        raw_groups=defaultdict(list)
        for r,p in zip(exam,probabilities):
            raw_groups[(r['fixture_id'],r['inputs']['line'])].append((r,float(p)))
        groups={}
        required={'over','under'} if market=='totals' else {'home','draw','away'}
        for key, group in raw_groups.items():
            by_side={r['inputs']['selection_side']:(r,p) for r,p in group}
            total=sum(p for r,p in by_side.values())
            if set(by_side)==required and total>0:
                groups[key]=[(r,p/total) for r,p in by_side.values()]
        if not groups:
            report['markets'][market]={**previous,'status':'WAITING_DATA',
                'reason':'시험 구간의 완전한 시장별 선택지 대기','data_revision':revision}
            continue
        hits=sum(max(group,key=lambda x:x[1])[0]['answer'] for group in groups.values())
        brier=sum(sum((p-r['answer'])**2 for r,p in group)/len(group) for group in groups.values())/len(groups)
        model=fit(sample)
        folder=root/'.learning_models';folder.mkdir(exist_ok=True)
        name=f'v2-{market}-{revision}.pkl'
        # A uniquely versioned file is never overwritten while a reader uses it.
        path=folder/name
        if not path.exists():
            temporary=folder/(name+'.tmp')
            temporary.write_bytes(pickle.dumps(model))
            temporary.replace(path)
        report['markets'][market]=dict(status='VALIDATING',label='학습 후 실전 검증 중',
            artifact=name,model_version=name[:-4],data_revision=revision,
            previous_artifact=previous.get('artifact'),training_matches=len({r['fixture_id'] for r in sample}),
            exam=dict(matches=len({key[0] for key in groups}),market_cases=len(groups),
                accuracy=hits/len(groups),brier=brier,normalization='same-as-serving',**boundaries),
            scope='경기 전 해당 시장 배당·선택·기준점; 다른 분석가 확률 미사용')
        atomic_json(root/'v2_market_status.json',report)
    atomic_json(root/'v2_market_status.json',report)
    return report


_MODELS={}
def predict(candidates,root):
    root=Path(root)
    status=read_json(root/'v2_market_status.json')
    grouped=defaultdict(list)
    for c in candidates or []:
        market=c.get('market_key');line=market_line(c)
        sides=('over','under') if market=='totals' else ('home','draw','away')
        if market not in ('totals','handicap') or line is None or c.get('selection_side') not in sides:
            continue
        if (market=='handicap' and not line.is_integer()) or (market=='totals' and abs(line%1-.5)>1e-9):
            continue
        try:
            if not math.isfinite(float(c.get('odd'))) or float(c['odd'])<=1:continue
        except (TypeError,ValueError):continue
        grouped[(market,line)].append(c)
    result=[]
    for (market,line),rows in grouped.items():
        entry=(status.get('markets') or {}).get(market) or {}
        name=entry.get('artifact')
        if not name or Path(name).name!=name:continue
        required={'over','under'} if market=='totals' else {'home','draw','away'}
        by_side={r['selection_side']:r for r in rows}
        if set(by_side)!=required:continue
        rows=list(by_side.values())
        try:
            key=str(root/name)
            if key not in _MODELS:
                with (root/'.learning_models'/name).open('rb') as stream:_MODELS[key]=pickle.load(stream)
            model=_MODELS[key]
            values=model.predict_proba([features({**r,'line':line}) for r in rows])[:,list(model.classes_).index(1)]
            total=float(sum(values))
            if total<=0:continue
            for r,p in zip(rows,values):
                probability=float(p)/total
                result.append({**r,'engine':'v2-ai','model_probability':probability,
                    'probability':probability,'prob':probability,'model_version':entry['model_version'],
                    'validation_status':'VALIDATING','label':'V2 학습 후 실전 검증 중'})
        except (OSError,ValueError,AttributeError,pickle.UnpicklingError):
            continue
    return result
