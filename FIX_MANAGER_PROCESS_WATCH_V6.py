"""Manager process watchdog fix only. No AI request, retry or pause removal."""
import argparse,fcntl,hashlib,json,os,tempfile,time
from pathlib import Path
BEFORE = 'debca280ae64219662d74d89b5299b4e5908789929af3cec63be631d38e0e8f9'
AFTER = 'fe5ebc7ebdd86a1cee79fd4067862b10f9efce5cd86d63b28f6ccac21a642b7f'

def sha(raw):return hashlib.sha256(raw).hexdigest()
def install(root,apply):
    state=root/'dj-manager-memory/runtime'
    active=json.loads((state/'ACTIVATED.json').read_text())
    code=Path(active['code']).resolve();code.relative_to((root/'dj-manager-memory/runtime-code').resolve())
    target=code/'manager_remembered_runtime.py'
    with (state/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('기존 작업 실행 중. 변경 없음')
        if target.is_symlink():raise ValueError('링크 경로 변경 중단')
        old=target.read_bytes()
        if sha(old)==AFTER:print('이미 적용됨 · AI 요청 0회');return
        if sha(old)!=BEFORE:raise ValueError('예상과 다른 관리자 실행 파일. 변경 없음')
        s=old.decode();pattern="'pause_on_network_loss':True"
        if s.count(pattern)!=1:raise ValueError('연결 검사 설정 위치 불일치')
        raw=s.replace(pattern,"'pause_on_network_loss':False").encode()
        compile(raw,str(target),'exec')
        if sha(raw)!=AFTER:raise ValueError('수정 결과 해시 불일치')
        print('검증 PASS · 분석 코드·기억·모델·시간 한도 동일 · AI 요청 0회')
        if not apply:return
        backup=state/'deployments'/('cli-process-watch-'+str(time.time_ns()));backup.mkdir(parents=True)
        (backup/target.name).write_bytes(old)
        fd,name=tempfile.mkstemp(dir=code,prefix='.process-watch-')
        try:
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            if target.read_bytes()!=old:raise ValueError('검증 중 파일 변경')
            os.replace(name,target)
        finally:
            if os.path.exists(name):os.unlink(name)
        print('PROCESS_WATCH_INSTALLED · 백업: '+str(backup))
        print('중단 기록·미완료 요청·기존 답안 보존. 분석 재개·재요청 없음.')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('/home/ubuntu'));parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    os.umask(0o077);install(args.root.resolve(),args.apply)
if __name__=='__main__':main()
