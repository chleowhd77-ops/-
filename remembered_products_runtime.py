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
from remembered_products_contract import ENGINES, PUBLIC_KEYS, LABELS, VERSION, validate, probability, settle
from remembered_products_transport import Author, TransportFailure, write, optional, split_packets, stamp


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


def analyze(root, state, manager_state, release, engine, author, inputs):
    own=records(state,engine); known={p['case_id'] for p in own}
    pool=[q for product in ('proto','toto14') for q in inputs[product]]
    plans=state/'cycles'/engine
    pending=[p for p in sorted(plans.glob('*/plan.json')) if not (p.parent/'completed.json').exists()]
    if pending:
        plan_path=pending[0]; plan=read(plan_path)
    else:
        questions=[q for q in pool if q['case_id'] not in known]
        if not questions: return
        mem=memory_for(release,manager_state,state,engine,pool)
        batches=split_packets({'mode':'analyze','analyst':engine,'memory':mem},'questions',questions)
        plan={'questions':questions,'packets':batches,'memory':mem,'created_at':stamp()}
        plan_path=plans/digest([VERSION,questions])/'plan.json'; write(plan_path,plan)
    folder=plan_path.parent
    answers=[]
    for i,packet in enumerate(plan['packets']):
        if not any(q['identity']['kickoff']>time.time() for q in packet['questions']):
            continue
        print(f'{LABELS[engine]} 공개픽 전체 자료 분석 {i+1}/{len(plan["packets"])}',flush=True)
        response=author.ask(folder,f'analysis-{i:03d}',packet)
        answers.extend(validate(response,packet['questions']))
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
        print(f'{LABELS[engine]} 승무패14 최종 마킹 · {rid}회차',flush=True)
        response=author.ask(folder,'ticket-'+digest(rid)[:16],packet)
        by_id.update({p['case_id']:p for p in validate(response,qs,ticket=True,prior=prior)})
    current=load_inputs(root)
    eligible={q['case_id'] for product in ('proto','toto14') for q in current[product]}
    saved=[]
    for q in plan['questions']:
        cid=q['case_id']
        if cid not in by_id or cid not in eligible or q['identity']['kickoff']<=time.time(): continue
        # A Toto allocation is valid as a complete round only, frozen at its first kickoff.
        if q['product']=='toto14' and any(x['case_id'] not in eligible for x in groups[q['round_id']]): continue
        target=state/'picks'/engine/(cid+'.json')
        if target.exists(): continue
        answer=by_id[cid]
        options=[o for o in q['options'] if o['option_id'] in answer['selected_ids']]
        original={'case_id':cid,'engine':engine,'identity':q['identity'],'product':q['product'],
            'round_id':q['round_id'],'number':q['number'],'question':q,'answer':answer,'options':options,
            'model_version':VERSION,'frozen_at':stamp(),'cycle':folder.name}
        write(target,original); saved.append(cid)
    write(folder/'completed.json',{'saved':saved,'finished_at':stamp(),
        'reviewed':len(answers),'expired_or_unlinked':len(plan['questions'])-len(saved)})
    print(f'{LABELS[engine]} 공개픽 저장 {len(saved)}경기',flush=True)


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
            for engine in ENGINES:
                review(state,manager_state,args.release,engine,author)
                analyze(root,state,manager_state,args.release,engine,author,inputs)
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
