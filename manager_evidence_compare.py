"""One-off, private comparison against saved pre-repair analyst requests.

No publishing, grading, ledger replacement or automatic retry. Original odds,
memory and unchanged candidates are held fixed; restored games are reanalysed.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import sqlite3
import time

from manager_memory_inputs import clean
from manager_evidence_recovery import recover_evidence
from manager_remembered_runtime import (Author, ENGINES, LABELS, digest, read,
    write, stamp, validate_picks, split_packets)


def restore_question(db, question):
    q = copy.deepcopy(question)
    row = db.execute('''SELECT fixture_key,robot_pick_version,api_fixture_id,
        home_team,away_team,kickoff_timestamp FROM robot_learning_samples WHERE id=?''',
        (q['source']['sample_id'],)).fetchone()
    identity = q['identity']
    expected = (identity['fixture_id'], identity['home'], identity['away'], identity['kickoff'])
    if not row or tuple(row[2:]) != expected:
        raise ValueError('복구 원자료 경기 연결 불일치')
    evidence, audit = recover_evidence(db, row[0], row[1], identity['kickoff'],
                                      q['source']['evidence_captured_at'], q['evidence'])
    q['evidence'] = clean(evidence)
    if q['evidence'] != question['evidence']:
        q['source']['evidence_recovery'] = audit
    return q


def prepare_engine(db, old_folder, now):
    questions, old_answers, memories = [], [], []
    for path in sorted(old_folder.glob('candidates-*.packet.json')):
        saved = read(path)
        answer = read(path.with_name(path.name.replace('.packet.json', '.answer.json')))
        if saved['request_id'] != answer['request_id']:
            raise ValueError('과거 요청·응답 연결 불일치')
        memories.append(saved['packet']['memory'])
        questions.extend(saved['packet']['questions'])
        old_answers.extend(answer['response']['picks'])
    if not memories or any(m != memories[0] for m in memories):
        raise ValueError('기존 기억의 일관성 확인 필요')
    old_portfolio = read(old_folder/'portfolio.packet.json')
    old_final = read(old_folder/'portfolio.answer.json')
    if old_portfolio['request_id'] != old_final['request_id']:
        raise ValueError('과거 최종 선택 요청·응답 연결 불일치')
    if old_portfolio['packet']['memory'] != memories[0]:
        raise ValueError('최종 선정의 기억 연결 불일치')
    validate_picks({'picks':old_answers}, questions, len(questions))
    if len(old_answers) != len(questions):
        raise ValueError('과거 후보 답안 누락')
    current = [q for q in questions if q['identity']['kickoff'] > now]
    repaired = [restore_question(db, q) for q in current]
    changed = [new for old,new in zip(current,repaired) if old != new]
    return {'memory': memories[0], 'questions': repaired, 'changed': changed,
            'old_candidates': old_answers, 'old_final':old_final['response']['picks'],
            'maximum_selections':old_portfolio['packet']['maximum_selections'],
            'started_excluded':[q['case_id'] for q in questions if q not in current],
            'prepared_at':stamp()}


def compare_engine(folder, plan, engine, author):
    questions = [q for q in plan['questions'] if q['identity']['kickoff'] > time.time()]
    eligible = {q['case_id'] for q in questions}
    changed = [q for q in plan['changed'] if q['case_id'] in eligible]
    if not changed:
        result = {'status':'NO_RESTORED_UPCOMING_GAMES', 'changes':[], 'AI_requests':0}
        write(folder/'comparison.json', result)
        return result
    proposals = {p['case_id']:p for p in plan['old_candidates'] if p['case_id'] in eligible}
    base = {'mode':'candidates','analyst':engine,'memory':plan['memory'],
            'review_scope':'all_provided_questions'}
    packets_path = folder/'repair-packets.json'
    if packets_path.exists():
        packets = read(packets_path)
        if any(q['identity']['kickoff'] <= time.time() for p in packets for q in p['questions']):
            raise ValueError('저장된 비교 대상이 시작됐습니다. 자동 재분석하지 않습니다')
    else:
        packets = split_packets(base, 'questions', changed)
        write(packets_path, packets)
    for i,packet in enumerate(packets):
        print(f'{LABELS[engine]} 복구 자료 재검토 {i+1}/{len(packets)}',flush=True)
        response = author.ask(folder, f'repair-{i:03d}', packet)
        picks = validate_picks(response, packet['questions'], len(packet['questions']))
        if len(picks) != len(packet['questions']):
            raise ValueError('복구 경기 답안 누락')
        proposals.update({p['case_id']:p for p in picks})
    questions = [q for q in questions if q['identity']['kickoff'] > time.time()]
    eligible = {q['case_id'] for q in questions}
    proposals = {cid:p for cid,p in proposals.items() if cid in eligible}
    finalists = [{'proposal':proposals[q['case_id']], 'identity':q['identity'],
                  'options':q['options']} for q in questions]
    if finalists:
        packet = {'mode':'portfolio','analyst':engine,'memory':plan['memory'],
                  'candidates':finalists,'maximum_selections':plan['maximum_selections']}
        response = author.ask(folder, 'portfolio', packet)
        selected = validate_picks(response, questions, plan['maximum_selections'], list(proposals.values()))
    else:
        selected = []
    before = {p['case_id']:p for p in plan['old_candidates']}
    old_final = {p['case_id']:p for p in plan['old_final']}
    new_final = {p['case_id']:p for p in selected}
    changes = []
    restored_ids = {q['case_id'] for q in plan['changed']}
    for q in questions:
        cid = q['case_id']
        options = {o['option_id']:o for o in q['options']}
        def describe(p):
            return {'pick': options[p['selected_id']]['raw_pick'],
                    'odd':options[p['selected_id']]['odd'],
                    'probability':p['probability'],'reason':p['reason']} if p else None
        old, new = before[cid], proposals[cid]
        changes.append({'case_id':cid,'identity':q['identity'],
                        'restored':cid in restored_ids,
                        'restoration':q['source'].get('evidence_recovery'),
                        'candidate_before':describe(old), 'candidate_after':describe(new),
                        'candidate_changed':old['selected_id']!=new['selected_id'],
                        'final_before':describe(old_final.get(cid)),
                        'final_after':describe(new_final.get(cid))})
    result = {'status':'COMPLETE','finished_at':stamp(), 'changes':changes,
              'final_before_count':sum(cid in eligible for cid in old_final),
              'final_after_count':len(selected), 'restored_reviewed_count':len(changed),
              'unpublished':True, 'scope':'last full-review selection, not the entire existing ledger',
              'started_excluded':plan['started_excluded'],
              'comparison_note':'Original request memory and odds held fixed. Unchanged candidates reused. Model variability may also affect differences.'}
    write(folder/'comparison.json', result)
    return result


def main():
    import fcntl
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--release',type=Path,required=True)
    parser.add_argument('--comparison',type=Path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    state=args.root/'dj-manager-memory/runtime'
    target=args.comparison
    if (state/'PAUSED.json').exists() or (target/'PAUSED.json').exists():
        raise SystemExit('중단 기록 확인 필요. 자동 재개하지 않습니다')
    target.mkdir(parents=True,exist_ok=True)
    with (state/'worker.lock').open('a') as lock:
        print('비교 작업 시작 대기: 기존 분석과 중복 실행하지 않습니다',flush=True)
        fcntl.flock(lock,fcntl.LOCK_EX)
        if (state/'PAUSED.json').exists():
            raise SystemExit('중단 기록 확인 필요. 자동 재개하지 않습니다')
        if (target/'completed.json').exists():
            print('이미 비교 완료. 새 AI 요청 없음'); return
        try:
            setup=read(target/'setup.json')
            source=Path(setup['baseline_cycle'])
            if not all((source/e/'completed.json').exists() for e in ENGINES):
                raise ValueError('기존 분석 미완료')
            # Prepare every analyst before the first model call; do not change
            # another analyst's information cutoff while waiting for responses.
            with sqlite3.connect((args.root/'ai_predictions.db').as_uri()+'?mode=ro',uri=True,timeout=5) as db:
                db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
                for engine in ENGINES:
                    path=target/engine/'plan.json'
                    if not path.exists(): write(path,prepare_engine(db,source/engine,time.time()))
            author=Author(args.release,target)
            summary={}
            for engine in ENGINES:
                folder=target/engine
                result=read(folder/'comparison.json') if (folder/'comparison.json').exists() else compare_engine(
                    folder,read(folder/'plan.json'),engine,author)
                summary[engine]=result
                print(f'{LABELS[engine]} 비교 완료: {result.get("final_before_count","-")} → {result.get("final_after_count","-")}개 (비교용)',flush=True)
                for item in result.get('changes',[]):
                    if item['restored'] or bool(item['final_before']) != bool(item['final_after']):
                        identity=item['identity']
                        before=item['candidate_before']['pick']
                        after=item['candidate_after']['pick']
                        selected_before='선택' if item['final_before'] else '미선택'
                        selected_after='선택' if item['final_after'] else '미선택'
                        print(f"  {identity['home']} vs {identity['away']}: 후보 {before} → {after}; 최종 {selected_before} → {selected_after}",flush=True)
            write(target/'completed.json',{'completed_at':stamp(),'analysts':summary,'web_picks_changed':False})
            print('네 분석가 비교 완료. 기존 웹 픽·구매내역 변경 없음.',flush=True)
        except Exception as error:
            pause={'paused_at':stamp(),'reason':str(error),'automatic_retry':False}
            write(target/'PAUSED.json',pause)
            write(state/'PAUSED.json',pause)
            raise


if __name__=='__main__':
    main()
