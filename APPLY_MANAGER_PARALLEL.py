"""Finish the current paid response, install four independent workers, resume."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen

EXPECTED='89a7c2f2c059e71150f3f641cfd3865e1a930209b855f1276179f59c4785959a'
HELPERS={'manager_memory_retrieval.py': 'd2d79acd8bc6ab3120448b5ffb41d8cd9572d0bce7e3c179e19f7bb4c7cd4f79', 'remembered_products_packing.py': 'f9547f950759ba9a78df3033830140d07267ca487ac54cffce204ad76f9ee1e0'}
BASELINE='ab7614250eee62b9a963077db50788c4ec02a92b8a0404af4c469c3d4054bc7e'
ROOT=Path('/home/ubuntu')
STATE=ROOT/'dj-manager-memory/runtime'
SERVICE='dj-remembered-manager.service'
TIMER='dj-remembered-manager.timer'
BUILD='manager-parallel-'+EXPECTED[:12]
NAME='manager_remembered_runtime.py'

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(b):return hashlib.sha256(b).hexdigest()
def atomic(p,b):
    p.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=p.parent,prefix='.parallel-')
    try:
        with os.fdopen(fd,'wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
        os.replace(name,p)
    finally:
        if os.path.exists(name):os.unlink(name)

def main():
    os.umask(0o077)
    code=Path(read(STATE/'ACTIVATED.json')['code']).resolve()
    code.relative_to(ROOT/'dj-manager-memory/runtime-code')
    unit=subprocess.check_output(['sudo','-n','systemctl','cat',SERVICE],text=True)
    if str(code/'start_manager.py') not in unit:raise SystemExit('관리자 실행 경로 확인 필요')
    before=(code/NAME).read_bytes()
    if sha(before) not in (BASELINE,EXPECTED):raise SystemExit('관리자 코드 버전 확인 필요. 변경 없음')
    for name,value in HELPERS.items():
        if sha((code/name).read_bytes())!=value:raise SystemExit('기존 학습 제외 버전 확인 필요: '+name)
    pause=STATE/'PAUSED.json'
    audit=STATE/'deployments'/BUILD
    if sha(before)==EXPECTED and (audit/'installed.json').exists() and not pause.exists():
        print('이미 적용되어 있습니다. 중복 실행하지 않았습니다.');return
    # An unrelated pause is never cleared by this installer.
    if pause.exists() and not (audit/'handoff.json').exists():
        raise SystemExit('기존 오류 중단 기록이 있습니다. 자동으로 재개하지 않았습니다.')
    with urlopen('https://raw.githubusercontent.com/chleowhd77-ops/-/main/'+NAME,timeout=30) as r:incoming=r.read()
    if sha(incoming)!=EXPECTED:raise SystemExit('업로드 파일이 다릅니다. 실행 중인 분석은 변경하지 않았습니다.')
    compile(incoming.decode('utf-8-sig'),NAME,'exec')
    subprocess.run(['sudo','-n','systemctl','stop',TIMER],check=True)
    audit.mkdir(parents=True,exist_ok=True)
    atomic(audit/'handoff.json',json.dumps({'source':BUILD,'time':time.time()}).encode())
    if not pause.exists():
        with pause.open('x',encoding='utf-8') as f:
            json.dump({'source':BUILD,'reason':'현재 답안 저장 후 네 분석가 동시 실행 적용','automatic_retry':False},f,ensure_ascii=False)
    print('현재 AI 답안 저장을 기다립니다. 저장되면 동시 실행으로 자동 재개합니다.',flush=True)
    deadline=time.monotonic()+1260;notice=0
    with (STATE/'worker.lock').open('a') as lock:
        while True:
            status=subprocess.check_output(['systemctl','show',SERVICE,'-p','ActiveState','--value'],text=True).strip()
            if status in ('inactive','failed'):
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:pass
            if time.monotonic()>deadline:raise SystemExit('답안 저장 대기 시간 초과. 중단 기록 보존. 강제 종료 없음.')
            if time.monotonic()>=notice:
                print('진행 중인 답안 저장 대기 중…',flush=True);notice=time.monotonic()+30
            time.sleep(2)
        if not pause.exists():raise SystemExit('중단 기록이 변경되었습니다. 변경 없음')
        stopped=read(pause)
        if stopped.get('source')!=BUILD and stopped.get('reason')!='중단 상태. 직접 재개 필요':
            raise SystemExit('별도 오류를 확인해야 합니다: '+str(stopped.get('reason')))
        unresolved=[p for p in STATE.rglob('*.pending.json')
                    if not p.with_name(p.name.replace('.pending.json','.answer.json')).exists()]
        if unresolved:raise SystemExit('완료 확인이 필요한 요청 '+str(len(unresolved))+'건. 재호출 없이 중단 기록 보존.')
        if Path(read(STATE/'ACTIVATED.json')['code']).resolve()!=code or (code/NAME).read_bytes()!=before:
            raise SystemExit('대기 중 코드가 변경되었습니다. 변경 없음')
        backup=audit/('previous-'+NAME)
        if not backup.exists():atomic(backup,before)
        atomic(code/NAME,incoming)
        if sha((code/NAME).read_bytes())!=EXPECTED:
            atomic(code/NAME,before);raise SystemExit('설치 검증 실패. 이전 코드 복원')
        atomic(audit/'installed.json',json.dumps({'runtime_sha256':EXPECTED,'common_inputs':True,
            'workers':4,'original_answers_preserved':True,'time':time.time()}).encode())
        pause.rename(audit/('pause-before-resume-'+str(time.time_ns())+'.json'))
    subprocess.run(['sudo','-n','systemctl','enable','--now',TIMER],check=True)
    subprocess.run(['sudo','-n','systemctl','start','--no-block',SERVICE],check=True)
    print('적용 완료. 공통 자료로 네 분석가 동시 실행. 완료 답안 재사용. 관리자 자동 분석 재개.',flush=True)
    print('sudo journalctl -u '+SERVICE+' -n 15 -f -o short-iso')

if __name__=='__main__':main()
