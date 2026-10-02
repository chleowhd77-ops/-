"""Versioned, pre-match-only exercises. Preparation never trains or rewrites picks."""
import argparse
import csv
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from learning_state import atomic_json, digest, now_iso

VERSION = 'curriculum-2026-10-02-v2'
LESSONS = {
    'identity': '양 팀·대회·경기시각 연결 및 자료 누락 원인',
    'strength': '경기 전 전력·홈원정·득실점·상대 수준',
    'availability': '발표시각이 확인된 부상·선발·휴식',
    'importance': '당시 순위·진출·강등·남은 일정의 근거',
    'markets': '승무패·언더오버·±핸디 승무패의 가격과 정산',
    'calibration': '예측확률과 실제 결과 비교 및 오답 복습',
}


def market_line(candidate):
    key = candidate.get('market_key')
    field = 'handicap_base' if key == 'handicap' else 'total_line'
    if key == '1x2':
        return 0.0
    try:
        value = candidate.get(field)
        if key == 'totals' and value is None:
            value = candidate.get('totals_base')
        line = float(value)
        return line if math.isfinite(line) else None
    except (TypeError, ValueError):
        return None


def load_exercises(root):
    """Reuse audited snapshot eligibility; never train from an after-match report."""
    from official_meta_v3 import load_frozen_examples
    from scorecard_core import epoch
    root = Path(root)
    examples, audit = load_frozen_examples(root/'ai_predictions.db')
    questions = []
    with sqlite3.connect(f'file:{root/"ai_predictions.db"}?mode=ro',uri=True) as db:
        snapshots = {}
        ids = sorted({ex.snapshot_id for ex in examples})
        for start in range(0,len(ids),400):
            batch=ids[start:start+400]; marks=','.join('?' for _ in batch)
            snapshots.update({r[0]:json.loads(r[1] or '[]') for r in db.execute(
                f'SELECT id,candidates_json FROM prediction_analysis_snapshots WHERE id IN ({marks})',batch)})
        identities = {str(r[0]):r[1:] for r in db.execute(
            'SELECT match_id,api_fixture_id,home_team,away_team FROM predictions')}
    rejected = Counter()
    for ex in examples:
        if ex.match_id.startswith('WORLD_'):
            continue
        candidate = next((c for c in snapshots.get(ex.snapshot_id, [])
            if c.get('raw_pick') == ex.raw_pick and c.get('market_key') == ex.market_key), None)
        if not candidate:
            rejected['original_candidate_missing'] += 1
            continue
        line = market_line(candidate)
        try:
            odd = float(candidate.get('odd'))
            if not math.isfinite(odd) or odd <= 1 or line is None:
                raise ValueError()
        except (TypeError,ValueError):
            rejected['invalid_price_or_line'] += 1
            continue
        identity = identities.get(ex.match_id)
        if not identity or not identity[0]:
            rejected['fixture_missing'] += 1
            continue
        record = dict(question_id=digest([VERSION,identity[0],ex.snapshot_id,ex.market_key,ex.raw_pick]),
            fixture_id=identity[0],match_id=ex.match_id,home=identity[1],away=identity[2],
            snapshot_id=ex.snapshot_id,observed_at=epoch(ex.created_at),kickoff=ex.kickoff_timestamp,
            known_at=ex.result_known_timestamp,source='site_pre_match_snapshot',
            inputs=dict(market_key=ex.market_key,selection_side=candidate.get('selection_side'),
                odd=odd,line=line,raw_pick=ex.raw_pick),answer=int(ex.label))
        questions.append(record)
    audit['curriculum_rejected'] = dict(rejected)
    return questions,audit


def temporal_split(rows):
    """Whole fixture groups, equal kickoff boundaries and result-availability embargo."""
    times=sorted({r['kickoff'] for r in rows})
    if len(times)<5:
        return {'train':[],'tune':[],'exam':[]}, {'reason':'유효 경기시각 구간 부족'}
    first=times[min(len(times)-2,max(1,int(len(times)*.6)))]
    second=times[min(len(times)-1,max(2,int(len(times)*.8)))]
    splits={'train':[],'tune':[],'exam':[]}
    excluded=0
    for r in rows:
        if r['kickoff']<first and r['known_at']<first:
            splits['train'].append(r)
        elif first<=r['kickoff']<second and r['known_at']<second:
            splits['tune'].append(r)
        elif r['kickoff']>=second:
            splits['exam'].append(r)
        else: excluded+=1
    sets=[{r['fixture_id'] for r in part} for part in splits.values()]
    if any(a&b for i,a in enumerate(sets) for b in sets[i+1:]):
        raise ValueError('동일 경기의 시간순 구간 중복')
    return splits,dict(tune_start=first,exam_start=second,excluded_late_results=excluded)


def prepare(root, exercises=None):
    root=Path(root)
    audit={}
    try:
        rows,audit=exercises if exercises is not None else load_exercises(root)
    except (sqlite3.Error,ValueError,RuntimeError) as error:
        rows=[];audit={'source_error':f'{type(error).__name__}: {error}'}
    splits,boundaries=temporal_split(rows)
    archive=root/'master_training_data.csv'
    archive_info={'present':archive.exists(),'used_in_site_market_questions':False}
    if archive.exists():
        with archive.open(encoding='utf-8-sig') as stream:
            reader=csv.DictReader(stream)
            archive_info.update(columns=reader.fieldnames,rows=sum(1 for _ in reader),
                notice='V2 기존 승무패 학습에서 별도 사용. 과거 시점 미확인 항목은 시장 문제집에 자동 병합하지 않음.')
    revision=digest([VERSION,rows,archive_info])
    folder=root/'.learning_course'/revision
    for name,part in splits.items():
        atomic_json(folder/(name+'_questions.json'),[
            {k:v for k,v in r.items() if k not in ('answer','known_at')} for r in part])
        atomic_json(folder/(name+'_answers.json'),[
            {k:r[k] for k in ('question_id','fixture_id','answer','known_at')} for r in part])
    report=dict(version=VERSION,revision=revision,generated_at=now_iso(),lessons=LESSONS,
        status='PREPARED' if splits['exam'] else 'WAITING_DATA',
        historical_archive=archive_info,source_audit=audit,boundaries=boundaries,
        splits={name:dict(questions=len(part),fixtures=len({r['fixture_id'] for r in part}),
            by_market=dict(Counter(r['inputs']['market_key'] for r in part))) for name,part in splits.items()},
        trained=False,promised_accuracy=None,
        unused_lessons=['전력·부상·중요도는 시점이 검증된 원본 특성 확보 후 입력 확장; 설명문만으로 학습했다고 하지 않음'])
    atomic_json(folder/'manifest.json',report)
    atomic_json(root/'learning_course_status.json',report)
    print(f"[교과서 준비] {report['status']} · {len(rows)}문제 · revision={revision}",flush=True)
    return report


def examine(root, submission_path):
    """Grade a versioned held-out submission, without training or modifying picks.

    One selected question per fixture/market/line; a claimed training cutoff
    must precede every exam kickoff. This validates the declaration, not the
    provenance of an externally supplied model.
    """
    from learning_state import read_json
    root=Path(root)
    manifest=read_json(root/'learning_course_status.json')
    submission=json.loads(Path(submission_path).read_text(encoding='utf-8'))
    revision=manifest.get('revision')
    if not revision or submission.get('revision')!=revision:
        raise ValueError('문제집 버전 불일치')
    if submission.get('engine') not in ('official','robot','v2','v3') or not submission.get('model_version'):
        raise ValueError('분석가와 모델 버전 필수')
    cutoff=float(submission.get('trained_through') or 0)
    folder=root/'.learning_course'/revision
    questions={r['question_id']:r for r in read_json(folder/'exam_questions.json',[])}
    answers={r['question_id']:r for r in read_json(folder/'exam_answers.json',[])}
    if not questions or not math.isfinite(cutoff) or cutoff<=0 or cutoff>=min(r['kickoff'] for r in questions.values()):
        raise ValueError('시험 이전 학습 종료시각과 유효 시험 자료 필요')
    groups=lambda r:(r['fixture_id'],r['inputs']['market_key'],r['inputs']['line'])
    available={groups(r) for r in questions.values()}
    seen=set();scores=[]
    for pick in submission.get('picks',[]):
        qid=pick.get('question_id')
        if qid not in questions or qid not in answers:raise ValueError('시험 문제에 없는 답안')
        row=questions[qid];key=groups(row)
        if key in seen:raise ValueError('동일 경기·시장·기준점 중복 선택')
        seen.add(key)
        probability=float(pick['probability'])
        if not math.isfinite(probability) or not 0<=probability<=1:raise ValueError('확률 범위 오류')
        label=answers[qid]['answer'];odd=float(row['inputs']['odd'])
        scores.append(dict(question_id=qid,market=key[1],hit=label,
            brier=(probability-label)**2,unit_profit=odd*label-1))
    def metrics(rows):
        n=len(rows)
        return dict(selections=n,accuracy=sum(r['hit'] for r in rows)/n if n else None,
            brier=sum(r['brier'] for r in rows)/n if n else None,
            unit_stake_roi=sum(r['unit_profit'] for r in rows)/n if n else None)
    report=dict(revision=revision,engine=submission['engine'],model_version=submission['model_version'],
        trained_through=cutoff,generated_at=now_iso(),status='GRADED' if scores else 'NO_ANSWERS',
        available_market_cases=len(available),answered_market_cases=len(seen),
        coverage=len(seen)/len(available) if available else 0,
        overall=metrics(scores),by_market={k:metrics([r for r in scores if r['market']==k])
            for k in ('1x2','totals','handicap')},
        notice='학습 종료시각은 제출 선언값. 모델 생성 이력 별도 확인. 누락은 적중으로 계산하지 않음.',
        mistakes=[r['question_id'] for r in scores if not r['hit']])
    atomic_json(folder/('exam_report_'+submission['engine']+'_'+digest(submission)+'.json'),report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',default='.')
    parser.add_argument('--submission',help='별도 생성한 시험 답안 JSON; 없으면 문제집 준비만 실행')
    args=parser.parse_args()
    if args.submission:
        print(json.dumps(examine(args.root,args.submission),ensure_ascii=False,indent=2))
    else:
        prepare(args.root)
