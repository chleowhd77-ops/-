"""Independent public-product ledger. Never writes collector/manager predictions."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import sqlite3
from contextlib import closing
import sys
import time
from datetime import datetime, timezone
from manager_memory_inputs import digest, epoch, load_memory, read
from remembered_products_inputs import load_inputs
from remembered_products_contract import ENGINES, PUBLIC_KEYS, LABELS, VERSION, INSTRUCTION, SCHEMA, validate, probability, settle
from remembered_products_transport import Author, TransportFailure, write, optional, split_packets, stamp
from remembered_products_budget import save_repacked


def memory_for(release, manager_state, state, engine, questions):
    memory = load_memory(release, engine, questions)
    fixtures = {str(q['identity']['fixture_id']) for q in questions}
    reviews=[]
    for base in (manager_state, state):
        for path in sorted((base/'reviews'/engine).glob('*.json')):
            item=read(path)
            if str(item['original']['identity']['fixture_id']) not in fixtures: reviews.append(item)
    memory['live_pick_reviews']=reviews
    return memory


def records(state, engine):
    return [read(p) for p in sorted((state/'picks'/engine).glob('*.json'))]


def cached_response(folder, name, packet):
    path=folder/(name+'.answer.json')
    if not path.exists(): return None
    saved=read(path)
    if saved.get('request_id')!=digest([VERSION,INSTRUCTION,SCHEMA,packet]):
        raise ValueError('저장된 요청과 현재 입력이 다릅니다')
    return saved['response']


def save_ready(root, state, engine, folder, questions, answers, on_progress=None):
    current=load_inputs(root)
    eligible={q['case_id'] for product in ('proto','toto14') for q in current[product]}
    groups={}
    for q in questions:
        if q['product']=='toto14': groups.setdefault(q['round_id'],[]).append(q)
    saved=[]
    for q in questions:
        cid=q['case_id']
        if cid not in answers or cid not in eligible or q['identity']['kickoff']<=time.time(): continue
        if q['product']=='toto14' and (len(groups[q['round_id']])!=14 or any(
            x['case_id'] not in eligible or x['case_id'] not in answers or x['identity']['kickoff']<=time.time()
            for x in groups[q['round_id']])): continue
        target=state/'picks'/engine/(cid+'.json')
        if target.exists(): continue
        answer=answers[cid]
        options=[o for o in q['options'] if o['option_id'] in answer['selected_ids']]
        write(target,{'case_id':cid,'engine':engine,'identity':q['identity'],'product':q['product'],
            'round_id':q['round_id'],'number':q['number'],'question':q,'answer':answer,'options':options,
            'model_version':VERSION,'frozen_at':stamp(),'cycle':folder.name})
        saved.append(cid)
    if saved:
        print(f'{LABELS[engine]} 완료 답안 즉시 저장 {len(saved)}경기',flush=True)
        if on_progress: on_progress()
    return saved


def analyze(root, state, manager_state, release, engine, author, inputs,
            max_requests=None, cache_only=False, existing_only=False, on_progress=None):
    own=records(state,engine); known={p['case_id'] for p in own}
    pool=[q for product in ('proto','toto14') for q in inputs[product]]
    plans=state/'cycles'/engine
    pending=[p for p in sorted(plans.glob('*/plan.json')) if not (p.parent/'completed.json').exists()]
    if pending:
        plan_path=pending[0]; plan=read(plan_path)
    else:
        if existing_only: return False
        questions=[q for q in pool if q['case_id'] not in known]
        if not questions: return False
        questions.sort(key=lambda q:q['identity']['kickoff'])
        mem=memory_for(release,manager_state,state,engine,pool)
        batches=split_packets({'mode':'analyze','analyst':engine,'memory':mem},'questions',questions)
        plan={'questions':questions,'packets':batches,'memory':mem,'created_at':stamp()}
        plan_path=plans/digest([VERSION,questions])/'plan.json'; write(plan_path,plan)
    folder=plan_path.parent
    plan=save_repacked(folder,plan)
    answers=[]; waiting=[]; requests=0
    # Recover every completed batch before issuing any new paid request.
    for i,packet in enumerate(plan['packets']):
        response=cached_response(folder,plan['packet_names'][i],packet)
        if response is not None:
            answers.extend(validate(response,packet['questions']))
        else: waiting.append((i,packet))
    proto=[q for q in plan['questions'] if q['product']=='proto']
    save_ready(root,state,engine,folder,proto,{p['case_id']:p for p in answers},on_progress)
    # Reorder transport calls only; original packets/cache identities stay exact.
    waiting.sort(key=lambda entry:min((q['identity']['kickoff'] for q in entry[1]['questions']
                                     if q['identity']['kickoff']>time.time()),default=float('inf')))
    unfinished=False
    for i,packet in waiting:
        if not any(q['identity']['kickoff']>time.time() for q in packet['questions']):
            continue
        if cache_only or (max_requests is not None and requests>=max_requests):
            unfinished=True; continue
        print(f'{LABELS[engine]} 공개픽 전체 자료 분석 {i+1}/{len(plan["packets"])}',flush=True)
        response=author.ask(folder,plan['packet_names'][i],packet)
        requests+=1
        answers.extend(validate(response,packet['questions']))
        save_ready(root,state,engine,folder,proto,{p['case_id']:p for p in answers},on_progress)
    by_id={p['case_id']:p for p in answers}
    groups={}
    for q in plan['questions']:
        if q['product']=='toto14': groups.setdefault(q['round_id'],[]).append(q)
    for rid,qs in groups.items():
        if min(q['identity']['kickoff'] for q in qs)<=time.time(): continue
        if not all(q['case_id'] in by_id for q in qs): continue
        prior=[by_id[q['case_id']] for q in qs]
        packet={'mode':'ticket','analyst':engine,'memory':plan['memory'],
                'questions':[{k:v for k,v in q.items() if k!='evidence'} for q in qs],
                'own_analyses':prior,'maximum_combinations':8}
        name='ticket-'+digest(rid)[:16]
        response=cached_response(folder,name,packet)
        if response is None:
            if cache_only or (max_requests is not None and requests>=max_requests):
                unfinished=True; continue
            print(f'{LABELS[engine]} 승무패14 최종 마킹 · {rid}회차',flush=True)
            response=author.ask(folder,name,packet); requests+=1
        ticket_answers={p['case_id']:p for p in validate(response,qs,ticket=True,prior=prior)}
        save_ready(root,state,engine,folder,qs,ticket_answers,on_progress)
    if unfinished: return True
    saved=[q['case_id'] for q in plan['questions'] if (state/'picks'/engine/(q['case_id']+'.json')).exists()]
    write(folder/'completed.json',{'saved':saved,'finished_at':stamp(),
        'reviewed':len(answers),'expired_or_unlinked':len(plan['questions'])-len(saved)})
    print(f'{LABELS[engine]} 공개픽 저장 {len(saved)}경기',flush=True)
    return False


def run_predictions(root, state, manager_state, release, author, inputs):
    def ready(): publish(root,state,load_inputs(root))
    # An interrupted prior run may have saved answers for several analysts.
    # Publish all of these before waiting for the first new inference.
    for engine in ENGINES:
        analyze(root,state,manager_state,release,engine,author,inputs,
                cache_only=True,existing_only=True,on_progress=ready)
    ready()
    active=list(ENGINES)
    while active:
        following=[]
        for engine in active:
            if analyze(root,state,manager_state,release,engine,author,inputs,
                       max_requests=1,on_progress=ready): following.append(engine)
        ready()
        active=following


def grade(root, state):
    delivered=optional(state/'delivery.json',{})
    with closing(sqlite3.connect((root/'ai_predictions.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
        db.row_factory=sqlite3.Row; db.execute('PRAGMA query_only=ON')
        for engine in ENGINES:
            for p in records(state,engine):
                identity=p['identity']; cid=p['case_id']; key=engine+':'+cid
                target=state/'grades'/engine/(cid+'.json')
                if target.exists() or key not in delivered or not epoch(delivered[key]['confirmed_at'])<identity['kickoff']<=time.time(): continue
                row=db.execute('''SELECT * FROM predictions WHERE match_id=? AND actual_result='FINISHED'
                    ORDER BY id DESC LIMIT 1''',(identity['match_id'],)).fetchone()
                if row is None or (row['home_team'],row['away_team'],row['api_fixture_id'],epoch(row['match_time'])) != (
                    identity['home'],identity['away'],identity['fixture_id'],identity['kickoff']): continue
                match=re.fullmatch(r'\s*(\d+)\s*:\s*(\d+)\s*',row['actual_score'] or '')
                if not match: continue
                h,a=map(int,match.groups()); hit=settle(p['options'],h,a)
                write(target,{'actual_score':row['actual_score'],'is_correct':hit,
                    'settlement':'REFUND' if hit is None else 'HIT' if hit else 'MISS',
                    'graded_at':stamp(),'pick_digest':digest(p)})


def review(state, manager_state, release, engine, author):
    todo=[]
    for p in records(state,engine):
        path=state/'grades'/engine/(p['case_id']+'.json')
        if path.exists() and not (state/'reviews'/engine/path.name).exists():
            todo.append({'case_id':p['case_id'],'original':p,'grade':read(path)})
    plans=state/'review_requests'/engine
    pending=[p for p in sorted(plans.glob('*/plan.json')) if not (p.parent/'completed.json').exists()]
    if pending: path=pending[0]; packets=read(path)
    elif todo:
        packets=split_packets({'mode':'review','analyst':engine,
            'memory':memory_for(release,manager_state,state,engine,[])},'records',todo)
        path=plans/digest(todo)/'plan.json'; write(path,packets)
    else: return
    for i,packet in enumerate(packets):
        response=author.ask(path.parent,f'review-{i:03d}',packet)
        notes=response.get('reflections') or []
        if response['picks'] or len(notes)!=len(packet['records']) or {n['case_id'] for n in notes}!={r['case_id'] for r in packet['records']}:
            raise ValueError('모든 실전 답안의 독립 복기 확인 필요')
        for record in packet['records']:
            note=next(n['explanation'] for n in notes if n['case_id']==record['case_id'])
            if not note.strip(): raise ValueError('빈 복기 응답')
            target=state/'reviews'/engine/(record['case_id']+'.json')
            if not target.exists(): write(target,{**record,'reflection':note,'reviewed_at':stamp()})
    write(path.parent/'completed.json',{'finished_at':stamp()})


def public_rows(state):
    rows=[]; delivered=optional(state/'delivery.json',{})
    for engine in ENGINES:
        for p in records(state,engine):
            cid=p['case_id']; delivery=delivered.get(engine+':'+cid)
            # Unconfirmed future records are staged publicly; UI waits for the receipt.
            if not delivery and p['identity']['kickoff']<=time.time(): continue
            g=optional(state/'grades'/engine/(cid+'.json'),{})
            r=optional(state/'reviews'/engine/(cid+'.json'),{})
            rows.append({'case_id':cid,'engine':PUBLIC_KEYS[engine],'identity':p['identity'],
                'product':p['product'],'round_id':p['round_id'],'number':p['number'],
                'options':p['options'],'probability':probability(p['answer']),
                'reason':p['answer']['reason'],'frozen_at':p['frozen_at'],'model_version':VERSION,
                'source_captured_at':p['question']['source']['evidence_captured_at'],
                'missing_sections':p['question']['source']['missing_sections'],
                'published_at':delivery['confirmed_at'] if delivery else None,
                'review':r.get('reflection',''),**g})
    return rows


def top3(rows, now):
    result={}
    for e in PUBLIC_KEYS.values():
        own=[r for r in rows if r['engine']==e and r['product']=='proto' and r['identity']['kickoff']>now]
        # Only the analyst's own estimated hit probability determines rank.
        result[e]=[r['case_id'] for r in sorted(own,key=lambda r:(-r['probability'],r['identity']['kickoff'],r['case_id']))[:3]]
    return result


def put_feed(payload):
    import requests
    import base64
    from manager_investment_publish import _settings
    from runtime_publisher import DATA_BRANCH, ensure_data_branch
    repo,token=_settings(); headers={'Authorization':f'token {token}','Accept':'application/vnd.github+json'}
    ensure_data_branch(repo,headers)
    url=f'https://api.github.com/repos/{repo}/contents/remembered_products.json'
    old=requests.get(url,headers=headers,params={'ref':DATA_BRANCH},timeout=20)
    if old.status_code not in (200,404): raise ValueError(f'공개픽 게시 확인 HTTP {old.status_code}')
    raw=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
    body={'message':'Publish independent remembered product picks','branch':DATA_BRANCH,
          'content':base64.b64encode(raw).decode()}
    if old.status_code==200: body['sha']=old.json()['sha']
    response=requests.put(url,headers=headers,json=body,timeout=30)
    if response.status_code not in (200,201): raise ValueError(f'공개픽 게시 HTTP {response.status_code}')
    sha=response.json()['content']['sha']
    expected=__import__('hashlib').sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    if sha!=expected: raise ValueError('게시된 공개픽 내용 검증 실패')
    return {'confirmed_at':stamp(),'blob_sha':sha,'commit_sha':response.json()['commit']['sha']}


def publish(root, state, inputs):
    # Persist the exact outbox before any remote write. An unknown outcome pauses.
    delivery=optional(state/'delivery.json',{})
    top_history=optional(state/'top3_history.json',{})
    for phase in range(2):
        rows=public_rows(state); ranks=top3(rows,time.time())
        # Partial PROTO delivery must not turn every provisional leader into a
        # graded TOP3 pick. Keep the existing selection until coverage is ready.
        required={q['case_id'] for q in inputs['proto'] if q['identity']['kickoff']>time.time()}
        previous=optional(root/'remembered_products.json',{}).get('top3',{})
        for engine in PUBLIC_KEYS.values():
            available={r['case_id'] for r in rows if r['engine']==engine and r['product']=='proto'
                       and r['identity']['kickoff']>time.time()}
            if not required<=available:
                ranks[engine]=[cid for cid in previous.get(engine,[]) if cid in available]
        payload={'schema_version':VERSION,'generated_at':stamp(),'rows':rows,'top3':ranks,
            'top3_history':top_history,'coverage':{'eligible_proto':len(inputs['proto']),
                'eligible_toto14':len(inputs['toto14']),'excluded':inputs['excluded']},
            'paused':(state/'PAUSED.json').exists()}
        content_digest=digest({k:v for k,v in payload.items() if k!='generated_at'})
        if optional(state/'published.json',{}).get('digest')==content_digest: return
        write(state/'outbox.json',payload)
        receipt=put_feed(payload)
        changed=False
        for row in rows:
            if epoch(receipt['confirmed_at'])>=row['identity']['kickoff']: continue
            engine='robot_proto' if row['engine']=='robot' else row['engine']
            key=engine+':'+row['case_id']
            if key not in delivery: delivery[key]=receipt; changed=True
            if row['case_id'] in ranks[row['engine']] and key not in top_history:
                top_history[key]=receipt; changed=True
        write(state/'delivery.json',delivery); write(state/'top3_history.json',top_history)
        write(state/'published.json',dict(receipt,digest=content_digest))
        write(root/'remembered_products.json',payload)
        if not changed: break
    print('공개픽·TOP3·승무패14 게시 확인 완료',flush=True)


def main():
    import fcntl
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path('/home/ubuntu'))
    parser.add_argument('--release',type=Path,required=True)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args(); root=args.root
    manager_state=root/'dj-manager-memory/runtime'
    state=root/'dj-public-products/runtime'; state.mkdir(parents=True,exist_ok=True)
    sys.path.append(str(root)); os.umask(0o077)
    with (manager_state/'worker.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('관리자 분석 또는 자료 비교 실행 중 · 다음 주기에 확인합니다',flush=True); return
        inputs=load_inputs(root)
        if args.check:
            report={'eligible_proto':len(inputs['proto']),'eligible_toto14':len(inputs['toto14']),
                    'excluded':inputs['excluded'],'AI_requests':0,'analysts':{}}
            for engine in ENGINES:
                mem=memory_for(args.release,manager_state,state,engine,inputs['proto']+inputs['toto14'])
                packets=split_packets({'mode':'analyze','analyst':engine,'memory':mem},'questions',inputs['proto']+inputs['toto14'])
                report['analysts'][engine]={'correct_memory':len(mem['successful_memory']),
                    'error_memory':len(mem['error_memory']),'live_reviews':len(mem['live_pick_reviews']),
                    'analysis_batches':len(packets)}
            write(state/'readiness.json',report); print(json.dumps(report,ensure_ascii=False)); return
        if not (state/'ACTIVATED.json').exists(): raise SystemExit('공개픽 활성화 기록 없음')
        if any((s/'PAUSED.json').exists() for s in (state,manager_state)):
            print('중단 기록 있음 · 새 AI 요청 없음',flush=True); return
        try:
            author=Author(args.release,manager_state)
            grade(root,state)
            run_predictions(root,state,manager_state,args.release,author,inputs)
            # New match delivery must not wait behind post-match reflections.
            for engine in ENGINES:
                review(state,manager_state,args.release,engine,author)
            publish(root,state,load_inputs(root))
            write(state/'last_completed.json',{'completed_at':stamp(),'version':VERSION})
        except Exception as exc:
            pause={'at':stamp(),'source':'remembered-public-products','reason':str(exc),'automatic_retry':False}
            write(state/'PAUSED.json',pause)
            # Shared subscription transport must not continue calls after a network/account failure.
            if isinstance(exc,(TransportFailure,ConnectionError,TimeoutError)) or type(exc).__module__.startswith(('requests','urllib3')):
                write(manager_state/'PAUSED.json',pause)
            print('공개픽 작업 중단: '+str(exc),flush=True); raise


if __name__=='__main__': main()
