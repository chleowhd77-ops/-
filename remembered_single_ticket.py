"""Single marks from existing estimates; original tickets and receipts stay intact."""
from copy import deepcopy
import time
from manager_memory_inputs import digest, epoch, read

POLICY = 'single-1000-v1'


def delivery_key(engine, pick):
    return engine + ':' + pick['case_id'] + (
        ':' + POLICY + ':' + digest(pick['answer']) if pick.get('ticket_policy') == POLICY else '')


def select_single(question, answer):
    from remembered_products_contract import validate
    result = deepcopy(answer)
    estimates = {e['option_id']: e['probability'] for e in result['estimates']}
    # Preserve the analyst's original chosen side when probabilities tie.
    selected = max(result['selected_ids'], key=lambda k: estimates[k])
    result['selected_ids'] = [selected]
    validate({'picks': [result]}, [question])
    return result


def effective_records(state, engine, originals):
    delivered_path = state / 'delivery.json'
    delivered = read(delivered_path) if delivered_path.exists() else {}
    by_id = {p['case_id']: p for p in originals}
    for path in sorted((state / 'single_ticket_rounds' / engine).glob('*.json')):
        revision = read(path)
        rows, deadline = revision['rows'], revision['deadline']
        confirmed = all(0 < epoch(delivered.get(delivery_key(engine, p), {}).get('confirmed_at'))
                        < deadline for p in rows)
        # A late/unconfirmed replacement never rewrites a started round.
        if time.time() < deadline or confirmed:
            by_id.update({p['case_id']: p for p in rows})
    return list(by_id.values())


def prepare_existing(state, inputs, engines, write, stamp):
    eligible = {q['case_id'] for q in inputs['toto14']}
    for engine in engines:
        groups = {}
        for path in sorted((state / 'picks' / engine).glob('*.json')):
            p = read(path)
            if p['product'] == 'toto14':
                groups.setdefault(p['round_id'], []).append(p)
        for rid, originals in groups.items():
            target = state / 'single_ticket_rounds' / engine / (digest(rid) + '.json')
            if target.exists() or len(originals) != 14 or len({p['case_id'] for p in originals}) != 14:
                continue
            deadline = min(p['identity']['kickoff'] for p in originals)
            if deadline <= time.time() or not all(p['case_id'] in eligible for p in originals):
                continue
            if all(len(p['answer']['selected_ids']) == 1 for p in originals):
                continue
            if any((state / 'grades' / engine / (p['case_id'] + '.json')).exists() for p in originals):
                continue
            rows = deepcopy(originals)
            for p in rows:
                p['answer'] = select_single(p['question'], p['answer'])
                p['options'] = [o for o in p['question']['options']
                                if o['option_id'] in p['answer']['selected_ids']]
                p['ticket_policy'] = POLICY
                p['frozen_at'] = stamp()
            if deadline <= time.time():
                continue
            write(target, {'deadline': deadline, 'rows': rows, 'created_at': stamp(),
                           'originals': originals, 'reason': '사용자 요청: 1,000원 단통'})
