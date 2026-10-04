"""Finish the paid request, then install early publishing and fair scheduling."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen

EXPECTED={'remembered_products_runtime.py': '91558cadfd7725cc854b30a196d8a6ccabc8e7f86f58dcc201453cb55bb888cb'}
ALLOWED={'remembered_products_runtime.py': ['4b5cac5f325f66489129f93f7d4832a59724a83ddbf37b32d3b0b0c1ae9001cf']}
ROOT=Path('/home/ubuntu')
STATE=ROOT/'dj-public-products/runtime'
MANAGER=ROOT/'dj-manager-memory/runtime'
SERVICE='dj-remembered-products.service'
SOURCE='public-products-efficiency-v1'
BUILD=hashlib.sha256(json.dumps(EXPECTED,sort_keys=True).encode()).hexdigest()[:12]

def read(p): return json.loads(p.read_text())
def sha(raw): return hashlib.sha256(raw).hexdigest()
def atomic(p, raw):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+'.efficiency-writing')
    with tmp.open('wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,p)

def main():
    os.umask(0o077)
    code=Path(read(STATE/'ACTIVATED.json')['code']).resolve()
    if not code.is_relative_to((ROOT/'dj-public-products/code').resolve()):
        raise SystemExit('공개픽 실행 경로 확인 필요. 변경하지 않았습니다.')
    before={name:(code/name).read_bytes() for name in EXPECTED}
    for name,raw in before.items():
        if sha(raw) not in ALLOWED[name]+[EXPECTED[name]]:
            raise SystemExit(name+' 버전 확인 필요. 변경하지 않았습니다.')
    shared,own=MANAGER/'PAUSED.json',STATE/'PAUSED.json'
    if shared.exists() and read(shared).get('source')!=SOURCE:
        raise SystemExit('다른 중단 기록을 보존했습니다. 화면을 보내주세요.')
    if own.exists() and not (shared.exists() and read(shared).get('source')==SOURCE
                            and read(own).get('reason')=='중단 상태. 직접 재개 필요'):
        raise SystemExit('기존 공개픽 오류를 보존했습니다. 화면을 보내주세요.')
    incoming={}
    for name,expected in EXPECTED.items():
        with urlopen('https://raw.githubusercontent.com/chleowhd77-ops/-/main/'+name,timeout=30) as response:
            raw=response.read()
        if sha(raw)!=expected:
            raise SystemExit(name+' 업로드 확인 필요. 실행 중인 분석은 변경하지 않았습니다.')
        compile(raw.decode('utf-8-sig'),name,'exec')
        incoming[name]=raw
    if before==incoming and not shared.exists():
        print('이미 적용되었습니다. 중복 재시작하지 않았습니다.',flush=True); return
    try:
        with shared.open('x') as f:
            json.dump({'source':SOURCE,'reason':'현재 답안 저장 후 중복 처리 절감 적용','automatic_retry':False},f,ensure_ascii=False)
    except FileExistsError:
        if read(shared).get('source')!=SOURCE: raise SystemExit('다른 중단 기록을 보존했습니다.')
    print('현재 AI 요청의 답안 저장을 기다립니다. 강제 종료하거나 재요청하지 않습니다.',flush=True)
    deadline=time.monotonic()+1260
    with (MANAGER/'worker.lock').open('a') as lock:
        notice=0
        while True:
            status=subprocess.check_output(['systemctl','show',SERVICE,'-p','ActiveState','--value'],text=True).strip()
            if status in ('inactive','failed'):
                try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); break
                except BlockingIOError: pass
            if time.monotonic()>=deadline:
                raise SystemExit('현재 요청 완료 확인 시간이 지났습니다. 중단 기록을 보존했습니다. 화면을 보내주세요.')
            if time.monotonic()>=notice:
                print('현재 요청 저장 대기 중 · 저장 후 자동 적용·재개합니다.',flush=True)
                notice=time.monotonic()+30
            time.sleep(2)
        if not shared.exists() or read(shared).get('source')!=SOURCE:
            raise SystemExit('중단 상태가 달라졌습니다. 자동 재개하지 않았습니다.')
        if own.exists() and read(own).get('reason')!='중단 상태. 직접 재개 필요':
            raise SystemExit('별도 오류가 있어 보존했습니다: '+str(read(own).get('reason')))
        if Path(read(STATE/'ACTIVATED.json')['code']).resolve()!=code or any(
            (code/name).read_bytes()!=raw for name,raw in before.items()):
            raise SystemExit('설치 중 코드가 달라졌습니다. 대기 상태를 유지합니다.')
        audit=STATE/'deployments'/('efficiency-'+BUILD)
        for name,raw in before.items():
            backup=audit/('previous-'+name)
            if not backup.exists(): atomic(backup,raw)
        for name,raw in incoming.items(): atomic(code/name,raw)
        atomic(audit/'installed.json',json.dumps({'sha256':EXPECTED,'at':time.time(),
            'cache_identity_preserved':True,'original_evidence_preserved':True,'ui_changed':False}).encode())
        for p,label in ((own,'public'),(shared,'manager')):
            if p.exists(): os.replace(p,audit/(label+'-hold-'+str(time.time_ns())+'.json'))
    subprocess.run(['sudo','-n','systemctl','start','--no-block',SERVICE],check=True)
    print('적용 완료. 네 분석가 동시 실행을 유지하고 중복 자료 조회·묶음 구성을 줄였습니다.',flush=True)
    print('새 승무패 조합 요청은 완료된 자기 분석으로 마킹만 정합니다. 원자료·기억·기존 답안은 보존합니다.',flush=True)
    print('확인: sudo journalctl -u '+SERVICE+' -n 25 -f -o short-iso',flush=True)

if __name__=='__main__': main()
