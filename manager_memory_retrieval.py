"""Local retrieval of an analyst's saved memories; never invokes an AI.

The authoritative archives are untouched. Only matching records are copied into
request context. Retrieval ranks relevance, never betting selections.
"""
import copy
import hashlib
import json
import re
import unicodedata

POLICY = 'ai-match-reviews-v1'
MAX_MEMORY_CHARS = 12_000
MAX_RECORDS = 4


def serial(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def normalized(value):
    return ' '.join(re.findall(r'[^\W_]+',unicodedata.normalize('NFKC',str(value)).casefold()))


def identity(record):
    return record.get('identity') or record.get('original',{}).get('identity') or {}


def names(question):
    data=identity(question)
    result={normalized(data.get(k,'')) for k in ('home','away')}
    # Provider names can differ from Korean display names.
    evidence=question.get('evidence') or question.get('original',{}).get('question',{}).get('evidence') or {}
    def visit(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if k in ('home','away','home_name','away_name','home_team','away_team','name','league_name','competition_name') and isinstance(v,str):
                    result.add(normalized(v))
                elif isinstance(v,(dict,list)): visit(v)
        elif isinstance(value,list):
            for v in value: visit(v)
    for key in ('match_identity','teams','competition'): visit(evidence.get(key,{}))
    return {x for x in result if len(x)>=2 and x not in ('home','away','unknown','none')}


def record_view(kind, record):
    if kind!='live_pick_reviews': return copy.deepcopy(record)
    # A saved review already contains its original answer and actual outcome.
    # Do not replay that old match's entire question/evidence in a new match.
    original=record.get('original',{})
    return {'case_id':record.get('case_id'),'identity':identity(record),
            'original_answer':original.get('answer'), 'original_option':original.get('option'),
            'grade':record.get('grade'), 'reflection':record.get('reflection'),
            'recorded_at':record.get('recorded_at')}


def related_memory(memory, analyst, questions, mode):
    if memory.get('retrieval_policy')==POLICY:
        if any(r.get('source')!='live_pick_reviews' for r in memory.get('records',[])):
            raise ValueError('AI 실경기 복기 외의 기억은 사용할 수 없습니다')
        if memory.get('analyst')!=analyst or len(serial(memory))>MAX_MEMORY_CHARS:
            raise ValueError('관련 기억의 분석가 또는 용량 확인 필요')
        return copy.deepcopy(memory)
    if memory.get('retrieval_policy')=='related-memory-v1':
        # Old saved packets may contain pre-AI school results. Never forward them.
        views=[r['record'] for r in memory.get('records',[]) if r.get('source')=='live_pick_reviews']
        memory={'analyst':memory.get('analyst'), 'live_pick_reviews':[
            dict(v,original={'answer':v.get('original_answer'),'option':v.get('original_option')}) for v in views]}
    if memory.get('analyst',analyst)!=analyst:
        raise ValueError('다른 분석가의 기억은 전달하지 않습니다')
    result={'retrieval_policy':POLICY,'analyst':analyst,
            'scope':'AI가 직접 낸 실경기 픽과 결과의 관련 복기만 참고합니다. AI 연결 전 학습 기록은 사용하지 않습니다.',
            'records':[]}
    if mode!='candidates':
        result['scope']=('이미 작성한 자기 후보와 이유로 최종 선정합니다. 과거 기억을 재전송하지 않습니다.'
                         if mode=='portfolio' else
                         '이번에 결과가 확인된 픽과 실제 결과만 복기합니다. 과거 복기를 재전송하지 않습니다.')
        return result
    query_names=set().union(*(names(q) for q in questions)) if questions else set()
    excluded={str(identity(q).get('fixture_id')) for q in questions}
    excluded_cases={q.get('case_id') for q in questions}
    ranked=[]
    for kind in ('live_pick_reviews',):
        for record in memory.get(kind,[]):
            if record.get('analyst',analyst)!=analyst: continue
            rid=identity(record)
            if str(rid.get('fixture_id')) in excluded or record.get('case_id') in excluded_cases: continue
            view=record_view(kind,record)
            teams={normalized(rid.get(k,'')) for k in ('home','away')}-{''}
            exact=len(query_names & teams)
            text=' '+normalized(serial(view))+' '
            mentioned=sum((' '+n+' ') in text for n in query_names)
            score=exact*10+mentioned
            if not score: continue
            key=hashlib.sha256(serial(view).encode()).hexdigest()
            ranked.append((-score,key,{'source':kind,'record_id':key,'record':view}))
    seen=set()
    for _, key, item in sorted(ranked,key=lambda x:(x[0],x[1])):
        if key in seen: continue
        candidate=dict(result,records=result['records']+[item])
        if len(serial(candidate))>MAX_MEMORY_CHARS: continue
        result=candidate; seen.add(key)
        if len(result['records'])>=MAX_RECORDS: break
    return result


def prepare_packet(packet):
    if 'memory' not in packet: return copy.deepcopy(packet)
    if not isinstance(packet['memory'],dict):
        raise ValueError('저장 기억 형식 확인 필요')
    result=copy.deepcopy(packet)
    result['memory']=related_memory(packet['memory'],packet.get('analyst',packet['memory'].get('analyst')),
                                    packet.get('questions',[]),packet.get('mode'))
    return result
