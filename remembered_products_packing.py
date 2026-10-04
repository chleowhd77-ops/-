"""Lossless request encoding: repeated facts are defined once, never summarized."""
from collections import Counter
import json

GUIDE = '''아래 PACKET_JSON은 shared-json-v1 무손실 표현입니다. data가 원래 packet입니다.
{"@ref":"번호"}는 dictionary[번호]의 동일한 원문 값을 참조합니다. 필요한 곳마다 그 값을
그대로 대입하여 읽으세요. @table의 columns와 rows는 같은 열을 공유하는 객체 목록입니다.
@object는 원래 객체의 [키,값] 목록입니다. 참조된 기억·자료도 모두 검토 대상입니다.
이는 내용 요약이나 항목 생략이 아닙니다. 답안에는 이 인코딩을 사용하지 말고 지정 schema로 답하세요.
'''


def serial(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def pack(value):
    counts=Counter()
    def count(v):
        if isinstance(v,(dict,list,str)):
            key=serial(v)
            if len(key)>=160: counts[key]+=1
        if isinstance(v,dict):
            for child in v.values():count(child)
        elif isinstance(v,list):
            for child in v:count(child)
    count(value)
    ids={};dictionary={}
    def encode(v,reference=True):
        key=serial(v) if isinstance(v,(dict,list,str)) else ''
        if reference and len(key)>=160 and counts[key]>1:
            if key not in ids:
                rid=str(len(ids));ids[key]=rid
                dictionary[rid]=encode(v,False)
            return {'@ref':ids[key]}
        if isinstance(v,dict):
            if len(v)==1 and next(iter(v)) in ('@ref','@table','@object'):
                return {'@object':[[k,encode(x)] for k,x in v.items()]}
            return {k:encode(x) for k,x in v.items()}
        if isinstance(v,list):
            if len(v)>=3 and all(isinstance(x,dict) for x in v):
                keys=list(v[0])
                if keys and all(list(x)==keys for x in v):
                    table={'@table':{'columns':keys,'rows':[[encode(x[k]) for k in keys] for x in v]}}
                    plain=[encode(x) for x in v]
                    return table if len(serial(table))<len(serial(plain)) else plain
            return [encode(x) for x in v]
        return v
    data=encode(value)
    return {'encoding':'shared-json-v1','dictionary':dictionary,'data':data}


def unpack(value):
    def decode(v):
        if isinstance(v,dict):
            if set(v)=={'@ref'}:return decode(value['dictionary'][v['@ref']])
            if set(v)=={'@object'}:return {k:decode(x) for k,x in v['@object']}
            if set(v)=={'@table'}:
                t=v['@table'];return [dict(zip(t['columns'],[decode(x) for x in row])) for row in t['rows']]
            return {k:decode(x) for k,x in v.items()}
        if isinstance(v,list):return [decode(x) for x in v]
        return v
    return decode(value['data'])


def wire_text(instruction,packet):
    packed=pack(packet)
    if unpack(packed)!=packet:raise ValueError('전송 원문 복원 검사 실패')
    compact=GUIDE+instruction+serial(packed)
    original=instruction+serial(packet)
    return compact if len(compact)<len(original) else original
