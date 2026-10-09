"""Compare the complete current pool; reuse only verified own finished answers."""
import copy
from pathlib import Path
import time


POLICY = 'full-current-pool-v20'


def question_key(rt, question):
    from manager_material import question_value
    return rt.digest(question_value(question))


def make_cycle(rt, state, inputs):
    # Inclusion in an old request is not evidence that it was answered.
    pool = sorted(inputs['pool'], key=lambda q: q['case_id'])
    if not pool:
        return None
    from manager_live_review_check import POLICY as REVIEW_POLICY, review_signature
    from manager_memory_transport import POLICY as CONNECTION_POLICY
    key = rt.digest([REVIEW_POLICY, CONNECTION_POLICY, [question_key(rt, q) for q in pool],
                     review_signature(state, rt.ENGINES, time.time(), pool)])
    path = Path(state)/'cycles'/('full-coverage-v20-'+key)/'inputs.json'
    if not path.exists():
        rt.write(path, {'inputs':dict(inputs, pool=pool), 'created_at':rt.stamp(),
                        'coverage_policy':POLICY})
    return path


def cached_candidates(rt, state, engine, pool, memory=None, locked=None):
    wanted = {q['case_id']:q for q in pool}
    result = {}
    # Old completed selections alone are insufficient: check question and receipt.
    for path in sorted((Path(state)/'cycles').glob('*/'+engine+'/**/*.packet.json')):
        saved = rt.read(path)
        packet = saved.get('packet', {})
        if packet.get('mode') != 'candidates' or packet.get('analyst') != engine:
            continue
        answer_path = path.with_name(path.name.replace('.packet.json', '.answer.json'))
        if not answer_path.exists():
            continue
        answer = rt.read(answer_path)
        request_id = rt.digest([rt.VERSION, rt.INSTRUCTION, rt.SCHEMA, packet])
        if saved.get('request_id') != request_id or answer.get('request_id') != request_id:
            continue
        questions = packet.get('questions', [])
        try:
            picks = rt.validate_picks(answer.get('response', {}), questions, len(questions))
            if len(picks) != len(questions):
                continue
        except (ValueError, KeyError, TypeError):
            continue
        by_id = {q['case_id']:q for q in questions}
        for pick in picks:
            cid = pick['case_id']
            if cid in wanted and question_key(rt, by_id[cid]) == question_key(rt, wanted[cid]):
                from manager_live_review_check import usable_cache
                if (wanted[cid]['identity']['fixture_id'] not in (locked or set())
                        and not usable_cache(memory or {}, packet, answer.get('response', {}))):
                    continue
                result[cid] = {'proposal':copy.deepcopy(pick),
                              'receipt':str(answer_path.relative_to(state)),
                              'receipt_digest':rt.digest(answer),
                              'original_source':by_id[cid].get('source', {})}
    return result


def previous_selection(rt,state,engine,pool,memory,current_folder):
    """A shrink-only board is not a new investment question; never copy another analyst."""
    current={q['case_id']:question_key(rt,q) for q in pool}
    generation=memory.get('generation') or rt.digest(memory)
    previous=[]
    for complete in (Path(state)/'cycles').glob('*/'+engine+'/completed.json'):
        if complete.parent==current_folder: continue
        plan_path=complete.parent/'coverage-plan.json'
        inputs_path=complete.parent.parent/'inputs.json'
        if not plan_path.exists() or not inputs_path.exists(): continue
        plan=rt.read(plan_path); old_memory=plan.get('memory',{})
        report=rt.read(complete)
        if report.get('coverage_policy') not in ('full-current-pool-v18', POLICY): continue
        if (old_memory.get('generation') or rt.digest(old_memory))!=generation: continue
        if memory.get('live_wrong_count') and old_memory.get('live_review_policy')!=memory.get('live_review_policy'): continue
        old=rt.read(inputs_path)['inputs']['pool']
        if report.get('reviewed_count') != len(old) or plan.get('pool_digest') != rt.digest(old): continue
        before={q['case_id']:question_key(rt,q) for q in old}
        if all(before.get(cid)==key for cid,key in current.items()):
            previous.append((rt.read(complete).get('finished_at',''),complete))
    return max(previous,key=lambda x:x[0])[1] if previous else None


def run_selection(rt, state, root, release, engine, cycle_path, author, cycle):
    folder = cycle_path.parent/engine
    if (folder/'completed.json').exists():
        return
    pool = cycle['inputs']['pool']
    own_active = [rt.read(p) for p in (Path(state)/'picks'/engine).glob('*.json')
                  if rt.read(p)['identity']['kickoff'] > time.time()]
    locked = {p['identity']['fixture_id'] for p in own_active}
    slots = max(0, 10-len(own_active))
    memory = rt.append_live_memory(rt.load_memory(release, engine, pool), state, engine, pool)
    memory = rt.stable_ref(rt.store_memory(state, engine,
              rt.prepare_packet({'analyst':engine, 'questions':pool, 'memory':memory})['memory']))
    from manager_live_review_check import reference
    memory = reference(memory)
    plan_path = folder/'coverage-plan.json'
    if plan_path.exists():
        plan = rt.read(plan_path)
        if plan['pool_digest'] != rt.digest(pool) or plan['analyst'] != engine:
            raise ValueError('전체 검토 입력 변경 감지. 원본 보존·AI 재요청 없음')
        # A restart uses the exact original memory and transport plan.
        memory = plan['memory']
    else:
        cache = cached_candidates(rt, state, engine, pool, memory, locked)
        missing = [q for q in pool if q['case_id'] not in cache]
        packets = rt.split_packets({'mode':'candidates', 'analyst':engine,
                    'memory':memory, 'review_scope':'all_provided_questions'}, 'questions', missing)
        plan = {'analyst':engine, 'pool_digest':rt.digest(pool), 'memory':memory,
                'cached':cache, 'packets':packets}
        rt.write(plan_path, plan)
        rt.write(folder/'packets.json', packets)
    for cached in plan['cached'].values():
        if rt.digest(rt.read(Path(state)/cached['receipt'])) != cached['receipt_digest']:
            raise ValueError('재사용 답안 원본 변경 감지. 분석 중단·원본 보존')
    proposals = [copy.deepcopy(v['proposal']) for v in plan['cached'].values()]
    print(f'{rt.LABELS[engine]} 전체 후보 {len(pool)}경기 · 완료 답안 재사용 '
          f'{len(proposals)}경기 · 미검토/자료 변경 {len(pool)-len(proposals)}경기', flush=True)
    for index, packet in enumerate(plan['packets']):
        response = rt.ask_saved_or_partitioned(author, folder, f'candidates-{index:03d}', packet)
        picks = rt.validate_picks(response, packet['questions'], len(packet['questions']))
        if len(picks) != len(packet['questions']):
            raise ValueError('전체 후보 검토 답안 누락. 기록 보존·자동 재요청 없음')
        proposals.extend(copy.deepcopy(picks))
    rt.validate_picks({'picks':proposals}, pool, len(pool))
    if {p['case_id'] for p in proposals} != {q['case_id'] for q in pool}:
        raise ValueError('전체 경기 검토 완료 확인 실패. 최종 선정 중단')
    by_id = {q['case_id']:q for q in pool}
    all_candidates = [{'proposal':p, 'identity':by_id[p['case_id']]['identity'],
                       'options':by_id[p['case_id']]['options']} for p in proposals]
    selectable = [item for item in all_candidates
                  if item['identity']['fixture_id'] not in locked]
    chosen = []
    reused_selection=previous_selection(rt,state,engine,pool,memory,folder)
    if selectable and slots and reused_selection is None:
        portfolio = {'mode':'portfolio', 'analyst':engine, 'memory':memory,
                     'candidates':selectable, 'all_reviewed_candidates':all_candidates,
                     'existing_frozen_picks':[{k:p[k] for k in
                         ('case_id','identity','option','answer','frozen_at','rank')}
                         for p in own_active], 'maximum_selections':slots,
                     'reused_answer_sources':{cid:v['original_source']
                                             for cid,v in plan['cached'].items()},
                     'comparison_scope':'현재 전체 후보를 비교. 기존 확정픽은 유지하고 추가 선택만 제출.'}
        response = rt.ask_saved_or_partitioned(author, folder, 'portfolio', portfolio)
        allowed_pool = [by_id[item['proposal']['case_id']] for item in selectable]
        from manager_portfolio_identity import validate as validate_portfolio
        chosen = copy.deepcopy(validate_portfolio(rt.validate_picks, response, allowed_pool, slots,
                               [item['proposal'] for item in selectable], folder/'portfolio.case-id-repair.json'))
        candidate_map = {p['case_id']:p for p in proposals}
        for pick in chosen:
            pick['candidate_analysis'] = copy.deepcopy(candidate_map[pick['case_id']])
            pick['portfolio_memory_record_ids'] = response.get('memory_record_ids', [])
    current = rt.load_pool(root)['pool']
    frozen = rt.freeze_picks(state, engine, chosen, pool, cycle_path.parent.name, current)
    rt.write(folder/'completed.json', {'finished_at':rt.stamp(), 'coverage_policy':POLICY,
             'pool_count':len(pool), 'reviewed_count':len(proposals),
             'cached_count':len(plan['cached']), 'proposed_count':len(chosen),
             'existing_frozen_count':len(own_active), 'frozen':frozen,
             'selection_reused_from':str(reused_selection.relative_to(state)) if reused_selection else None,
             'reason':'현재 전체 후보 검토 완료 · 기존 확정픽 보존'})
    if reused_selection:
        print(f'{rt.LABELS[engine]} 기존 전체 비교 유지 · 후보 감소/수집 시각 변경만 확인 · 최종 선정 AI 요청 없음',flush=True)
    print(f'{rt.LABELS[engine]} 전체 검토 완료 {len(proposals)}/{len(pool)} · '
          f'추가 선택 {len(chosen)} · 신규 저장 {len(frozen)} · 기존 확정픽 보존', flush=True)
