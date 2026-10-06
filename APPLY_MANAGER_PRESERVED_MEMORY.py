"""Install approved memory connection only. No AI, pick changes or service restart."""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import zipfile

CONFIG = {'files': {'manager_memory_retrieval.py': '2dc37331689d38c227d3381614cf756931949c3018c700fa5837a02b2acc2f5a', 'manager_practice_bridge.py': '7329219ed847a099cbaf52093cbeef7eee2eafe645d2849c6917b6aa6cb1027a', 'manager_remembered_runtime.py': '2e2cebfc528c3a48ee8e11ff6747646a2be2af5720454c0db07fc5b49bd4f285'}, 'before': {'manager_memory_retrieval.py': ['d2d79acd8bc6ab3120448b5ffb41d8cd9572d0bce7e3c179e19f7bb4c7cd4f79'], 'manager_practice_bridge.py': ['4af3f2ea5c202299f063a05237f81108e5577fbbf2b637c41de7fe158a9535c1'], 'manager_remembered_runtime.py': ['38f1c84475d4d9166a0840ac0dd0809813c08ace869da57775d3197e16ac6494', '89a7c2f2c059e71150f3f641cfd3865e1a930209b855f1276179f59c4785959a', '8a422890d4992474c1c853ce21ee724b03425b359b23e43fc6f6dad23db029b6']}, 'bundle_sha256': '8bbc35f5905c9ae5c3c016675ca432ad0a0d4e6dfae31c093b5abf1a1fef0e0c'}

def sha(raw): return hashlib.sha256(raw).hexdigest()

def atomic(path, raw):
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.memory-restore-')
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def install(code,state,archive,apply=False):
    if sha(archive)!=CONFIG['bundle_sha256']:raise ValueError('배포 ZIP 검증 실패')
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        if set(z.namelist())!=set(CONFIG['files']):raise ValueError('파일 목록 불일치')
        payload={n:z.read(n) for n in CONFIG['files']}
    previous={}
    for n,raw in payload.items():
        if sha(raw)!=CONFIG['files'][n]:raise ValueError('수정 파일 검증 실패: '+n)
        compile(raw,n,'exec')
        path=code/n
        if path.is_symlink():raise ValueError('링크 대상 차단')
        old=path.read_bytes() if path.exists() else None
        if old is None or sha(old) not in CONFIG['before'][n]+[sha(raw)]:
            raise ValueError('서버 코드가 확인한 버전과 다릅니다. 변경 없음: '+n)
        previous[n]=old
    changed=[n for n in payload if payload[n]!=previous[n]]
    if not apply:
        print(json.dumps({'check':'OK','changed_files':changed,'AI_requests':0,'files_changed':False},ensure_ascii=False));return
    if not changed:
        print('이미 같은 수정본입니다. AI 호출 없음.');return
    backup=state/'deployments'/('preserved-memory-'+str(time.time_ns()));backup.mkdir(parents=True)
    for n in changed:(backup/n).write_bytes(previous[n])
    (backup/'manifest.json').write_text(json.dumps({'before':{n:sha(v) for n,v in previous.items()},'after':CONFIG['files']},indent=2))
    try:
        for n in changed:atomic(code/n,payload[n])
    except BaseException:
        for n in changed:atomic(code/n,previous[n])
        raise
    print('기억 연결 코드 설치 완료. 백업: '+str(backup))
    print('기존 픽·답안·pending·PAUSED 기록 보존. AI 호출·서비스 재시작 없음.')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('/home/ubuntu'))
    parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    os.umask(0o077);state=args.root/'dj-manager-memory/runtime'
    active=json.loads((state/'ACTIVATED.json').read_text());code=Path(active['code']).resolve()
    code.relative_to((args.root/'dj-manager-memory/runtime-code').resolve())
    archive=Path(__file__).with_name('MANAGER_PRESERVED_MEMORY_BUNDLE.zip').read_bytes()
    with (state/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('기존 분석 작업 실행 중. 변경·중단·재요청 없음.')
        install(code,state,archive,args.apply)

if __name__=='__main__':main()
