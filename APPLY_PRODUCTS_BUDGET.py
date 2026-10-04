"""Install lossless batching, preview remaining requests, keep budget hold by default."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen

EXPECTED={'remembered_products_packing.py': 'f9547f950759ba9a78df3033830140d07267ca487ac54cffce204ad76f9ee1e0', 'remembered_products_budget.py': '137e39cfef35ebf0116ff2a11e5efb23153090600f6902f8e8529e23718ad6cf', 'remembered_products_transport.py': '3b59a74ebfa20f4ef241fd32d0b452889c34e697c5711e8e04014d3bb66c07b5', 'remembered_products_runtime.py': '127288e3f5ad8a65b53a336cc651718b2683d4084f086c4be39167e3713858d5'}
BASELINE={'app.py': '73c8376744787d9f4dc0e9385374bd897ed012f1c018e9b353ab9235d9c83972', 'analyst_products.py': '27dbaf684eccc8427403b30ecddf6fd9273ac2d6e34eb11942d8a0dd327f32fd', 'scorecard_ui.py': '874635082358ec0f9855ef9ebc7429412e428d833bc55fd318ce7191bcaaa248', 'remembered_products_contract.py': '34ddb2d53682c9e7af871877490eae605d30d3fe40e17554424ca6e6bc071b80', 'remembered_products_inputs.py': 'fbd022399434a5fd0b2061d6a99778e1c2176ee05b1f61815d667b92cca2eb81', 'remembered_products_runtime.py': 'cd10bf239d164fcf8249f54234ee2575c686f2bfbb07c14c836ffb5df06e54ea', 'remembered_products_transport.py': '0c5f458b2b9a36e2e1aa3fbb3048f42a7d9b49cc50c148fe07c51ae2c2503585', 'remembered_products_ui.py': '094e83eceb33d3525479798345ce8be819338279fbdca40d409c9012cd78c0c4', 'manager_memory_inputs.py': '870d8662e09282ae893327f0024958508e18488b06fe58873b6726ac48097032', 'manager_evidence_recovery.py': '95d54b3c210c98a6a8a1bf70d02e3ea00c3c7275e4e67723ffb0897e015a4680', 'VERIFY_PUBLIC_PRODUCTS.py': 'b01ebd94130b4aedba355fa21b6d749c4cb8d7b002663f09a55c5e13ae52e544'}
ROOT=Path('/home/ubuntu')
STATE=ROOT/'dj-public-products/runtime'
MANAGER=ROOT/'dj-manager-memory/runtime'
BUILD='budget-'+hashlib.sha256(json.dumps(EXPECTED,sort_keys=True).encode()).hexdigest()[:12]
CODE=ROOT/'dj-public-products/code'/BUILD
AUDIT=STATE/'deployments'/BUILD
SERVICE='dj-remembered-products.service'
TIMER='dj-remembered-products.timer'
HOLD='public-products-budget-review-20261005'


def atomic(path,raw):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.writing')
    with tmp.open('wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
def run(args,**kw):return subprocess.run(args,check=True,**kw)
def read(path):return json.loads(Path(path).read_text())
def checked_holds():
    shared=MANAGER/'PAUSED.json';own=STATE/'PAUSED.json'
    if not shared.exists() or read(shared).get('source')!=HOLD:
        raise SystemExit('사용량 절약 대기 기록 확인 필요. 다른 중단 기록은 자동 해제하지 않습니다.')
    if own.exists() and read(own).get('reason')!='중단 상태. 직접 재개 필요':
        raise SystemExit('공개픽의 다른 중단 원인 확인 필요: '+str(read(own).get('reason')))
    return [p for p in (shared,own) if p.exists()]


def preview(release):
    from remembered_products_inputs import load_inputs
    from remembered_products_runtime import memory_for,records
    from remembered_products_budget import repack_plan
    from remembered_products_transport import split_packets,MAX_CHARS
    from remembered_products_contract import INSTRUCTION,ENGINES,LABELS
    from remembered_products_packing import serial,wire_text
    source=load_inputs(ROOT);pool=source['proto']+source['toto14'];report={}
    for engine in ENGINES:
        pending=[p for p in sorted((STATE/'cycles'/engine).glob('*/plan.json')) if not (p.parent/'completed.json').exists()]
        if pending:
            p=pending[0];new=repack_plan(p.parent,read(p));r=new['budget_report']
        else:
            known={p['case_id'] for p in records(STATE,engine)}
            questions=[q for q in pool if q['case_id'] not in known]
            base={'mode':'analyze','analyst':engine,'memory':memory_for(release,MANAGER,STATE,engine,pool)}
            old=[];batch=[]
            for q in questions:
                if batch and len(INSTRUCTION+serial(dict(base,questions=batch+[q])))>MAX_CHARS:
                    old.append(dict(base,questions=batch));batch=[]
                batch.append(q)
            if batch:old.append(dict(base,questions=batch))
            new=split_packets(base,'questions',questions)
            r={'completed_requests_reused':0,'old_unsent_batches':len(old),'new_unsent_batches':len(new),
               'old_unsent_characters':sum(len(INSTRUCTION+serial(p)) for p in old),
               'new_unsent_characters':sum(len(wire_text(INSTRUCTION,p)) for p in new),
               'questions_preserved':len(questions),'AI_requests':0}
        report[engine]=r
        print(f'{LABELS[engine]}: 완료 답안 재사용 {r["completed_requests_reused"]}묶음 / 남은 자료 요청 {r["old_unsent_batches"]} → {r["new_unsent_batches"]}묶음',flush=True)
        print(f'  전송 글자 수 {r["old_unsent_characters"]:,} → {r["new_unsent_characters"]:,} / 남은 경기 {r["questions_preserved"]}',flush=True)
    atomic(AUDIT/'budget-preview.json',json.dumps(report,ensure_ascii=False,indent=2).encode())
    print('승무패14 최종 마킹·추후 결과 복기는 별도 요청입니다. 위 수치는 자료 분석 요청 비교이며 실제 크레딧 절감률은 아닙니다.')


def main(resume=False):
    os.umask(0o077)
    status=subprocess.check_output(['systemctl','show',SERVICE,'-p','ActiveState','--value'],text=True).strip()
    if status not in ('inactive','failed'):
        raise SystemExit('현재 요청이 아직 마무리 중입니다. 완료 후 같은 명령을 실행하세요. 진행 중인 요청은 취소하지 않았습니다.')
    checked_holds()
    with (MANAGER/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('다른 분석 요청이 마무리 중입니다. 완료 후 실행하세요.')
        activation=read(STATE/'ACTIVATED.json');old=Path(activation['code']);release=Path(activation['release'])
        installed=(AUDIT/'installed.json').exists()
        if not installed:
            for name,expected in BASELINE.items():
                if hashlib.sha256((old/name).read_bytes()).hexdigest()!=expected:
                    raise SystemExit('기존 실행 코드 버전 확인 필요: '+name)
            sources={}
            for name,expected in EXPECTED.items():
                with urlopen('https://raw.githubusercontent.com/chleowhd77-ops/-/main/'+name,timeout=30) as response:raw=response.read()
                if hashlib.sha256(raw).hexdigest()!=expected:raise SystemExit('업로드 확인 필요: '+name)
                compile(raw.decode('utf-8-sig'),name,'exec');sources[name]=raw
            for name in BASELINE:
                sources.setdefault(name,(old/name).read_bytes())
            previous=subprocess.check_output(['sudo','-n','cat','/etc/systemd/system/'+SERVICE],text=True)
            if previous.count(str(old/'start_products.py'))!=1:raise SystemExit('기존 서비스 실행 경로 확인 필요')
            updated=previous.replace(str(old/'start_products.py'),str(CODE/'start_products.py'))
            launcher=(old/'start_products.py').read_text().replace(str(old),str(CODE))
            sources['start_products.py']=launcher.encode()
            for name,raw in sources.items():
                if (CODE/name).exists() and (CODE/name).read_bytes()!=raw:raise SystemExit('기존 설치 파일 보존: '+name)
                atomic(CODE/name,raw)
        sys.path[:0]=[str(CODE),str(ROOT)]
        preview(release) # No AI, no rewriting original plans or completed responses.
        if not installed:
            for name,raw in {'previous.service':previous.encode(),'previous-activation.json':json.dumps(activation).encode()}.items():
                if not (AUDIT/name).exists():atomic(AUDIT/name,raw)
            try:
                run(['sudo','-n','tee','/etc/systemd/system/'+SERVICE],input=updated,text=True,stdout=subprocess.DEVNULL)
                atomic(STATE/'ACTIVATED.json',json.dumps({**activation,'code':str(CODE),'budget_build':BUILD}).encode())
                run(['sudo','-n','systemctl','daemon-reload'])
                atomic(AUDIT/'installed.json',json.dumps({'build':BUILD,'files':EXPECTED}).encode())
            except BaseException:
                run(['sudo','-n','tee','/etc/systemd/system/'+SERVICE],input=previous,text=True,stdout=subprocess.DEVNULL)
                atomic(STATE/'ACTIVATED.json',json.dumps(activation).encode())
                run(['sudo','-n','systemctl','daemon-reload']);raise
        if not resume:
            print('절약 수정 설치 완료. AI 요청 0회. 현재 대기를 유지합니다. 이 결과를 캡처해서 보내주세요.');return
        for p in checked_holds():
            archive=AUDIT/('manager-budget-hold.json' if p.parent==MANAGER else 'public-budget-hold.json')
            if archive.exists():raise SystemExit('기존 재개 기록 확인 필요. 자동 중복 재개하지 않습니다.')
        for p in checked_holds():
            archive=AUDIT/('manager-budget-hold.json' if p.parent==MANAGER else 'public-budget-hold.json')
            os.replace(p,archive)
    run(['sudo','-n','systemctl','start','--no-block',SERVICE])
    print('완료 답안을 재사용하며 남은 분석을 재개했습니다.')


if __name__=='__main__':main(globals().get('RESUME_AFTER_INSTALL',False))
