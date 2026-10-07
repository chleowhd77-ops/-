import json, hashlib, collections
from pathlib import Path
from datetime import datetime, timezone

STATE = Path('/home/ubuntu/dj-manager-memory/runtime')
LABEL = {'official':'공식', 'robot_proto':'자율', 'v2':'V2', 'v3':'V3'}
SKIP = {'sample_id','snapshot_id','observation_id','evidence_captured_at','odds_captured_at','evidence_storage'}

def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))

def key(q):
    q = dict(q, source={k:v for k,v in q.get('source',{}).items() if k not in SKIP})
    return q

def changes(a,b,p=''):
    if type(a) is not type(b):
        return [(p,a,b)]
    if isinstance(a,dict):
        out=[]
        for k in sorted(set(a)|set(b)):
            name=p+'.'+k if p else k
            if k not in a or k not in b:
                out.append((name,a.get(k,'<필드 없음>'),b.get(k,'<필드 없음>')))
            else:
                out.extend(changes(a[k],b[k],name))
        return out
    if isinstance(a,list):
        out=[]
        if len(a)!=len(b): out.append((p+'.length',len(a),len(b)))
        for i,(x,y) in enumerate(zip(a,b)): out.extend(changes(x,y,p+f'[{i}]'))
        return out
    return [] if a==b else [(p,a,b)]

def short(v):
    if isinstance(v,(dict,list)): return f'<{type(v).__name__}:{len(v)}>'
    return str(v).replace('\n',' ')[:75]

def run():
    pause=STATE/'PAUSED.json'
    print('=== 중단 상태 ===')
    if pause.exists():
        info=read(pause)
        print('중단 시각:',info.get('paused_at','없음'))
        print('사유:',str(info.get('reason','없음')).splitlines()[0][:250])
    else: print('중단 기록 없음')
    cycles=[]
    for p in (STATE/'cycles').glob('*/inputs.json'):
        c=read(p)
        if c.get('coverage_policy')=='full-current-pool-v18':
            cycles.append((c.get('created_at',''),p,c))
    cycles.sort(key=lambda x:x[0])
    if not cycles:
        print('전체 비교 주기 기록 없음'); return
    _,path,cycle=cycles[-1]
    pool=cycle['inputs']['pool']
    print('=== 최신 주기 ===')
    print('생성:',cycle.get('created_at'),'대상:',len(pool),'경기')
    for engine,label in LABEL.items():
        folder=path.parent/engine
        plan=folder/'coverage-plan.json'
        if not plan.exists(): print(label,'계획 없음'); continue
        data=read(plan)
        needed=sum(len(p.get('questions',[])) for p in data.get('packets',[]))
        print(label,'재사용',len(data.get('cached',{})),'재검토',needed,'완료',(folder/'completed.json').exists())
    plan=path.parent/'official'/'coverage-plan.json'
    if plan.exists():
        needed=[q for p in read(plan).get('packets',[]) for q in p.get('questions',[])]
        previous=[x for x in cycles[:-1] if (x[1].parent/'official'/'completed.json').exists()]
        print('=== 재검토 경기: 직전 완료 주기와 비교 ===')
        if previous:
            prior=previous[-1][2]
            old={q['case_id']:q for q in prior['inputs']['pool']}
            now={q['case_id'] for q in pool}
            print('대상 수',len(old),'→',len(pool),'추가',len(now-set(old)),'빠짐',len(set(old)-now))
            for q in needed:
                i=q['identity']; before=old.get(q['case_id'])
                print(i.get('home'),'-',i.get('away'))
                if before is None: print('  직전 주기에 없던 경기'); continue
                diff=changes(key(before),key(q))
                counts=collections.Counter(p.split('.')[0].split('[')[0] for p,_,_ in diff)
                print('  변경',len(diff),'항목',dict(counts))
                for p,a,b in diff[:8]: print(' ',p,':',short(a),'→',short(b))
                if len(diff)>8: print('  추가 변경',len(diff)-8,'항목 있음')
        else: print('직전 완료 주기 없음')
    print('=== 최신 주기의 미완료 요청 ===')
    count=0
    for p in sorted(path.parent.glob('**/*.pending.json')):
        name=p.name.removesuffix('.pending.json')
        if (p.parent/(name+'.answer.json')).exists(): continue
        count+=1
        raw=p.parent/'paper-original'/(name+'.json')
        response=read(raw).get('response',{}) if raw.exists() else {}
        print(str(p.relative_to(path.parent)), '원본답안',raw.exists(),
              '저장예측',len(response.get('predictions',[])),
              '최종출력',(p.parent/(name+'.output.json')).exists())
    if not count: print('미완료 요청 없음')
    print('=== 최근 실제 실행 진단 ===')
    folders=sorted((STATE/'diagnostics').glob('*'),key=lambda p:p.name)
    for folder in folders[-3:]:
        start=folder/'started.json'; finish=folder/'finished.json'; fail=folder/'failure.json'
        if not start.exists(): continue
        started=read(start)
        status=read(finish).get('exit_code') if finish.exists() else '종료기록 없음'
        completed=0
        events=folder/'events.log'
        if events.exists():
            for line in events.read_text().splitlines():
                try: event=json.loads(line)
                except ValueError: continue
                if isinstance(event,dict) and event.get('type')=='turn.completed': completed+=1
        print(folder.name,'입력바이트',started.get('input_bytes'),'종료',status,'응답완료',completed,'예외',fail.exists())
    print('=== 읽기 전용 완료 · AI 호출/재개/기록 변경 0 ===')

if __name__=='__main__':
    run()
