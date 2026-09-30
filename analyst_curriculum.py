"""Versioned, pre-match-only exercises. Preparation never trains or rewrites picks."""
import argparse
import csv
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from learning_state import atomic_json, digest, now_iso

VERSION = 'curriculum-2026-10-01-v1'
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
        snapshots = {r[0]: json.loads(r[1] or '[]') for r in db.execute(
            'SELECT id,candidates_json FROM prediction_analysis_snapshots')}
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


def prepare(root):
    root=Path(root)
    audit={}
    try:
        rows,audit=load_exercises(root)
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


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',default='.')
    args=parser.parse_args()
    prepare(args.root)
