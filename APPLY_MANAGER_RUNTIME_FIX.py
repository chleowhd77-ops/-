"""Install only manager CLI fixes; preserve every pending request and frozen pick."""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen
import zipfile

CONFIG={'files': {'analyst_connection.py': '86860d64213874b4ddbdbc4453e24226efbba27b80e951d3f999a2215b978726', 'manager_cli_runtime.py': '7adad8e679195b92b8d11c644ad59b3b60bb6e4b455cb63a54efccb1f7e998ff', 'manager_remembered_runtime.py': '8a422890d4992474c1c853ce21ee724b03425b359b23e43fc6f6dad23db029b6'}, 'before': {'manager_remembered_runtime.py': '38f1c84475d4d9166a0840ac0dd0809813c08ace869da57775d3197e16ac6494'}, 'bundle_sha256': '816145de1c734a068fc932c0bdb15e560d7fcd7daa79fb47cf8edc7c88eb1d82'}
ROOT=Path('/home/ubuntu')
STATE=ROOT/'dj-manager-memory/runtime'
TIMER='dj-remembered-manager.timer'

def sha(raw):return hashlib.sha256(raw).hexdigest()
def atomic(path,raw):
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.runtime-fix-')
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def install(code,state,archive):
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        if sorted(z.namelist())!=sorted(CONFIG['files']):raise ValueError('수정 파일 목록 불일치')
        payload={n:z.read(n) for n in CONFIG['files']}
    previous={}
    for name,raw in payload.items():
        if sha(raw)!=CONFIG['files'][name]:raise ValueError('수정 파일 검증 실패: '+name)
        compile(raw,name,'exec')
        path=code/name;path.resolve().relative_to(code.resolve())
        if path.is_symlink():raise ValueError('수정 대상이 링크입니다')
        old=path.read_bytes() if path.exists() else None
        if name in CONFIG['before']:
            if old is None or sha(old) not in (CONFIG['before'][name],sha(raw)):raise ValueError('서버 버전 불일치: '+name)
        elif old is not None and old!=raw:raise ValueError('같은 이름의 다른 파일 존재: '+name)
        previous[name]=old
    changed={n for n in payload if previous[n]!=payload[n]}
    if not changed:
        print('동일 수정본이 이미 설치됐습니다. AI 호출 없음.');return
    backup=state/'deployments'/('cli-runtime-fix-'+str(time.time_ns()))
    backup.mkdir(parents=True)
    for n in changed:
        if previous[n] is not None:(backup/n).write_bytes(previous[n])
    (backup/'manifest.json').write_text(json.dumps({'before':{n:sha(v) if v else None for n,v in previous.items()},'after':CONFIG['files']}),encoding='utf-8')
    try:
        for n in sorted(changed):atomic(code/n,payload[n])
        check="import sys;sys.path.insert(0,sys.argv[1]);from manager_practice_bridge import originals;originals();import analyst_connection,manager_cli_runtime,manager_remembered_runtime;print('필수 모듈 불러오기 성공')"
        subprocess.run(['/usr/bin/python3','-c',check,str(code)],check=True,timeout=60)
    except BaseException:
        for n in changed:
            if previous[n] is None:
                if (code/n).exists():(code/n).unlink()
            else:atomic(code/n,previous[n])
        raise
    (backup/'installed.json').write_text(json.dumps({'files':sorted(changed),'AI_calls':0}),encoding='utf-8')
    print('관리자 실행 파일 수정 완료. 기존 픽·답안·대기·중단 기록 보존. 분석 재호출 없음.',flush=True)

def main():
    os.umask(0o077)
    code=Path(json.loads((STATE/'ACTIVATED.json').read_text())['code']).resolve()
    code.relative_to((ROOT/'dj-manager-memory/runtime-code').resolve())
    adjacent=Path(__file__).resolve().with_name('MANAGER_RUNTIME_FIX_BUNDLE.zip') if '__file__' in globals() else None
    if adjacent is not None and adjacent.is_file():archive=adjacent.read_bytes()
    else:
        with urlopen('https://raw.githubusercontent.com/chleowhd77-ops/-/main/MANAGER_RUNTIME_FIX_BUNDLE.zip',timeout=30) as r:archive=r.read()
    if sha(archive)!=CONFIG['bundle_sha256']:raise ValueError('GitHub 배포 묶음 검증 실패. 변경 없음')
    active=subprocess.check_output(['systemctl','show',TIMER,'-p','ActiveState','--value'],text=True).strip()=='active'
    subprocess.run(['sudo','-n','systemctl','stop',TIMER],check=True)
    try:
        with (STATE/'worker.lock').open('a') as lock:
            # Never interrupt an in-flight analyst to deploy this fix.
            deadline=time.monotonic()+60
            while True:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic()>deadline:raise ValueError('기존 작업 실행 중. 변경하지 않았습니다')
                    time.sleep(2)
            install(code,STATE,archive)
    finally:
        if active:subprocess.run(['sudo','-n','systemctl','start',TIMER],check=True)

if __name__=='__main__':main()
