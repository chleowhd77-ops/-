"""Cooperatively finish current request, preserve answers, change future output only."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen

OLD = '3b59a74ebfa20f4ef241fd32d0b452889c34e697c5711e8e04014d3bb66c07b5'
NEW = '704eb3bdb35db0cc9faaba9fd038f5092b7d5fc00afb55462c78b3ffe12a5325'
ROOT = Path('/home/ubuntu')
STATE = ROOT/'dj-public-products/runtime'
MANAGER = ROOT/'dj-manager-memory/runtime'
SERVICE = 'dj-remembered-products.service'
SOURCE = 'public-short-reasons-v1'

def read(p):
    return json.loads(p.read_text())

def atomic(p, raw):
    tmp = p.with_name(p.name+'.short-writing')
    with tmp.open('wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)

def main():
    os.umask(0o077)
    code = Path(read(STATE/'ACTIVATED.json')['code']).resolve()
    if not code.is_relative_to((ROOT/'dj-public-products/code').resolve()):
        raise SystemExit('공개픽 실행 경로 확인 필요. 변경하지 않았습니다.')
    target = code/'remembered_products_transport.py'
    before = target.read_bytes()
    if hashlib.sha256(before).hexdigest() not in (OLD, NEW):
        raise SystemExit('실행 코드 버전 확인 필요. 변경하지 않았습니다.')
    shared, own = MANAGER/'PAUSED.json', STATE/'PAUSED.json'
    if shared.exists() and read(shared).get('source') != SOURCE:
        raise SystemExit('다른 중단 기록이 있어 보존했습니다. 화면을 보내주세요.')
    if own.exists() and not (shared.exists() and read(shared).get('source') == SOURCE
                            and read(own).get('reason') == '중단 상태. 직접 재개 필요'):
        raise SystemExit('기존 공개픽 오류 기록을 먼저 확인해야 합니다. 화면을 보내주세요.')
    with urlopen('https://raw.githubusercontent.com/chleowhd77-ops/-/main/remembered_products_transport.py', timeout=30) as response:
        raw = response.read()
    if hashlib.sha256(raw).hexdigest() != NEW:
        raise SystemExit('업로드 파일 확인 필요. 실행 중인 분석은 변경하지 않았습니다.')
    compile(raw.decode('utf-8-sig'), target.name, 'exec')
    if before == raw and not shared.exists():
        print('이미 적용됐습니다. 중복 실행하지 않았습니다.', flush=True)
        return
    try:
        with shared.open('x') as f:
            json.dump({'source':SOURCE, 'reason':'현재 요청 저장 후 설명 길이 수정',
                       'automatic_retry':False}, f, ensure_ascii=False)
    except FileExistsError:
        if read(shared).get('source') != SOURCE:
            raise SystemExit('다른 중단 기록을 보존했습니다.')
    print('현재 요청이 끝나고 저장될 때까지 기다립니다. 강제 종료·재요청하지 않습니다.', flush=True)
    deadline = time.monotonic()+1260
    with (MANAGER/'worker.lock').open('a') as lock:
        next_notice = 0
        while True:
            state = subprocess.check_output(['systemctl','show',SERVICE,'-p','ActiveState','--value'],text=True).strip()
            if state in ('inactive','failed'):
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    pass
            if time.monotonic() >= deadline:
                raise SystemExit('요청 완료 확인 시간이 지났습니다. 대기 기록은 유지했습니다. 화면을 보내주세요.')
            if time.monotonic() >= next_notice:
                print('현재 요청 저장을 기다리는 중입니다. 완료 후 자동으로 이어집니다.',flush=True)
                next_notice = time.monotonic()+30
            time.sleep(2)
        if not shared.exists() or read(shared).get('source') != SOURCE:
            raise SystemExit('중단 상태가 달라졌습니다. 자동 재개하지 않았습니다.')
        if own.exists() and read(own).get('reason') != '중단 상태. 직접 재개 필요':
            raise SystemExit('별도 오류가 발생했습니다. 원인 확인 전 자동 재개하지 않습니다: '+str(read(own).get('reason')))
        if Path(read(STATE/'ACTIVATED.json')['code']).resolve() != code or target.read_bytes() != before:
            raise SystemExit('설치 중 실행 코드가 변경됐습니다. 대기 상태를 유지합니다.')
        audit = STATE/'deployments'/('short-reasons-'+NEW[:12])
        audit.mkdir(parents=True,exist_ok=True)
        backup = audit/'previous-transport.py'
        if not backup.exists():
            atomic(backup, before)
        atomic(target, raw)
        atomic(audit/'installed.json', json.dumps({'sha256':NEW,'output_only':True,
              'cache_identity_preserved':True,'at':time.time()}).encode())
        for p, label in ((own,'public'),(shared,'manager')):
            if p.exists():
                os.replace(p, audit/(label+'-hold-'+str(time.time_ns())+'.json'))
    subprocess.run(['sudo','-n','systemctl','start','--no-block',SERVICE],check=True)
    print('적용 완료. 새 요청부터 선택 이유 2문장·160자 이내로 작성하도록 지시했습니다.',flush=True)
    print('완료된 답안과 자료·기억을 보존하고 분석을 재개했습니다. 기존 긴 설명은 재작성하지 않습니다.',flush=True)

if __name__ == '__main__':
    main()
