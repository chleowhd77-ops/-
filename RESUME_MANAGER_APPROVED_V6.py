"""User-authorized recovery of four interrupted manager requests. No AI in installer."""
import argparse,fcntl,hashlib,json,os,subprocess,time
from pathlib import Path

ALLOWED={('official','review'),('v2','review'),('v3','review'),('robot_proto','candidates')}
START=1791269280.0  # 2026-10-06T15:48:00+09:00
WATCH_HASH='fe5ebc7ebdd86a1cee79fd4067862b10f9efce5cd86d63b28f6ccac21a642b7f'

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(raw):return hashlib.sha256(raw).hexdigest()
def answer_exists(p):
    base=str(p)[:-len('.pending.json')]
    return any(Path(base+s).is_file() for s in ('.answer.json','.json'))

def recover(root,apply):
    state=root/'dj-manager-memory/runtime'
    active=read(state/'ACTIVATED.json');code=Path(active['code']).resolve()
    code.relative_to((root/'dj-manager-memory/runtime-code').resolve())
    if sha((code/'manager_remembered_runtime.py').read_bytes())!=WATCH_HASH:
        raise ValueError('먼저 FIX_MANAGER_PROCESS_WATCH_V6.py를 적용하세요. 재요청 없음')
    service=subprocess.check_output(['systemctl','show','dj-remembered-manager.service','-p','ActiveState','--value'],text=True).strip()
    if service not in ('inactive','failed'):raise ValueError('관리자 서비스 실행 중. 중복 요청 없음')
    public=subprocess.check_output(['systemctl','show','dj-remembered-products.timer','-p','ActiveState','--value'],text=True).strip()
    if public=='active' and not (root/'dj-public-products/runtime/PAUSED.json').exists():
        raise ValueError('공개픽 재개 영향 확인 필요. 관리자 기록 변경 없음')
    with (state/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('기존 작업 실행 중. 변경 없음')
        marker=state/'PAUSED.json';pause_raw=marker.read_bytes();pause=read(marker)
        if not pause.get('reason','').startswith('인터넷 연결 확인 실패'):
            raise ValueError('예상과 다른 중단 사유. 자동 해제 없음: '+str(pause.get('reason')))
        snapshots={};groups=[];seen=set()
        for p in sorted(state.rglob('*.pending.json')):
            if answer_exists(p):continue
            if '/paper-original/' in p.as_posix():continue
            if p.stat().st_mtime<START:raise ValueError('이전 미완료 요청이 별도로 있습니다: '+str(p))
            prefix=str(p)[:-len('.pending.json')]
            packet_path=Path(prefix+'.packet.json')
            receipt=read(packet_path);packet=receipt['packet']
            key=(packet.get('analyst'),packet.get('mode'))
            if key not in ALLOWED or key in seen:raise ValueError('승인한 네 요청 이외의 요청 확인 필요: '+str(p))
            if receipt.get('request_id')!=read(p).get('request_id'):
                raise ValueError('요청 식별자 불일치: '+str(p))
            seen.add(key);groups.append({'analyst':key[0],'mode':key[1],'request':str(p.relative_to(state))})
            snapshots[p]=p.read_bytes()
            native=p.parent/'paper-original'/p.name
            if native.exists() and not answer_exists(native):snapshots[native]=native.read_bytes()
        for p in state.rglob('*.pending.json'):
            if not answer_exists(p) and p not in snapshots:
                raise ValueError('승인 범위 밖의 미완료 요청. 변경 없음: '+str(p))
        if not groups:raise ValueError('재시도할 승인 요청 없음. 중단 기록 변경 없음')
        print(json.dumps({'check':'PASS','approved_retry_requests':groups,'preserved_pending_files':len(snapshots),'AI_requests':0,'files_changed':False},ensure_ascii=False,indent=2),flush=True)
        if not apply:return
        if marker.read_bytes()!=pause_raw or read(state/'ACTIVATED.json')!=active or any(p.read_bytes()!=raw for p,raw in snapshots.items()):
            raise ValueError('검증 중 기록 변경. 적용 없음')
        audit=state/'deployments'/('approved-network-retry-'+str(time.time_ns()));audit.mkdir(parents=True)
        (audit/'authorization.json').write_text(json.dumps({'authorization':'사용자가 관리자픽 실행 및 중단된 네 요청 재시도를 승인함','groups':groups,'at':time.time(),'automatic_retry':False},ensure_ascii=False,indent=2))
        (audit/'PAUSED.before.json').write_bytes(pause_raw)
        moved=[]
        try:
            for p in snapshots:
                dest=audit/'pending_before'/p.relative_to(state);dest.parent.mkdir(parents=True,exist_ok=True)
                p.rename(dest);moved.append((p,dest))
            marker.rename(audit/'PAUSED.resolved.json')
        except BaseException:
            for p,dest in reversed(moved):dest.rename(p)
            raise
        print('APPROVED_RETRY_READY · 기존 요청 원문·답안·기억 보존 · 이전 대기 기록 백업: '+str(audit))
        print('관리자 서비스 시작 후에만 AI 재시도가 발생합니다. 추가 사용량이 발생할 수 있습니다.')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('/home/ubuntu'));parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    os.umask(0o077);recover(args.root.resolve(),args.apply)
if __name__=='__main__':main()
