"""Read-only EC2 verification; never requests a model or changes any picks."""
import json
from pathlib import Path
import sys
import time


def main():
    root=Path('/home/ubuntu');state=root/'dj-public-products/runtime'
    activation=json.loads((state/'ACTIVATED.json').read_text())
    sys.path[:0]=[activation['code'],str(root)]
    from remembered_products_inputs import load_inputs
    from remembered_products_runtime import public_rows
    from remembered_products_contract import LABELS, PUBLIC_KEYS
    from manager_memory_inputs import epoch
    source=load_inputs(root);rows=public_rows(state)
    payload=json.loads((root/'remembered_products.json').read_text()) if (root/'remembered_products.json').exists() else {}
    now=time.time()
    print('관리자픽 변경 없음 · 공개픽 연결 확인')
    for engine,label in LABELS.items():
        public=PUBLIC_KEYS[engine]
        own=[r for r in rows if r['engine']==public and r['published_at'] and
             epoch(r['published_at'])<r['identity']['kickoff'] and r['identity']['kickoff']>now]
        for product in ('proto','toto14'):
            ids={q['case_id'] for q in source[product]}
            ready=[r for r in own if r['product']==product and r['case_id'] in ids]
            print(f'{label} {product}: 현재 대상 {len(ids)} / 게시 확인 {len(ready)} / 대기 {len(ids)-len(ready)}')
        top=payload.get('top3',{}).get(public,[])
        print(f'{label} TOP3: {sum(r["case_id"] in top for r in own)}경기')
    print('원자료 연결 확인 필요:',len(source['excluded']),'건')
    for row in source['excluded']:
        print(row.get('product'),row.get('match_id') or row.get('round_id'),row['reason'])
    for base in (state,root/'dj-manager-memory/runtime'):
        if (base/'PAUSED.json').exists(): print('중단 기록:',json.loads((base/'PAUSED.json').read_text()).get('reason'))
    print('확인 완료 · AI 요청 0회 · 자료 변경 없음')


if __name__=='__main__':main()
