"""Lossless forwarding of verified, analyst-specific paper and live memories.
No AI calls, relevance ranking, truncation, or retired bootstrap training.
"""
import copy

POLICY = 'preserved-paper-and-live-v2'
FIELDS = ('successful_memory', 'error_memory', 'live_pick_reviews')


def related_memory(memory, analyst, questions, mode):
    if not isinstance(memory, dict) or memory.get('analyst') != analyst:
        raise ValueError('분석가 기억 연결 불일치')
    if memory.get('retrieval_policy') not in (None, POLICY):
        raise ValueError('구식으로 축소된 기억입니다. 원본 기억을 다시 읽어야 합니다. AI 자동 재요청 없음')
    excluded = {str(q['identity']['fixture_id']) for q in questions}
    case_ids = {q.get('case_id') for q in questions}
    result = {'analyst': analyst, 'retrieval_policy': POLICY}
    for field in FIELDS:
        values = memory.get(field, [])
        if not isinstance(values, list):
            raise ValueError('기억 형식 오류: ' + field)
        result[field] = []
        for record in values:
            if record.get('analyst', record.get('engine', analyst)) != analyst:
                raise ValueError('다른 분석가 기억 혼합 차단')
            identity = record.get('identity') or record.get('original', {}).get('identity') or {}
            if str(identity.get('fixture_id')) in excluded or record.get('case_id') in case_ids:
                continue
            result[field].append(copy.deepcopy(record))
    return result


def prepare_packet(packet):
    result = copy.deepcopy(packet)
    if 'memory' in result:
        result['memory'] = related_memory(result['memory'], result.get('analyst'),
                                           result.get('questions', []), result.get('mode'))
    return result
