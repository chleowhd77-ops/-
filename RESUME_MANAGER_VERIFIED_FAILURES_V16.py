"""Recover only four verified session-start failures by exact original request hash.
No source changes, model inference, credential reads, or network requests.
"""
import argparse,fcntl,hashlib,json,os,subprocess,sys,time
from pathlib import Path
EXPECTED={'manager_memory_access.py': 'd75c8a0cce159a0b33e547ac49b2ea2392bf694dee20744b18bb18f7a38db464', 'manager_memory_tools.py': 'b741e22c57c458c0a96b0370d87485504f547ad32a730141e9f74bb6ad3f4a35', 'manager_remembered_runtime.py': '3bd29359361f3e7eb578a62d2b1d9d7994ef9786bf84167ae3b596a915d652cf'}
ALLOWED={('official','candidates'),('robot_proto','candidates'),('v2','candidates'),('v3','portfolio')}
def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def answered(p):
 base=str(p)[:-len('.pending.json')]
 return any(Path(base+end).is_file() for end in ('.answer.json','.json'))
def verify_failed_run(diag,input_sha,pending_time):
 matches=[]
 for started in Path(diag).glob('*/started.json'):
  meta=read(started)
  if meta.get('input_sha256')==input_sha and started.stat().st_mtime>=pending_time-2:
   matches.append(started)
 if not matches:raise ValueError('해당 요청과 동일한 실행 로그 없음. 변경 없음')
 latest=max(matches,key=lambda p:p.stat().st_mtime);folder=latest.parent
 done=folder/'finished.json';err=folder/'stderr.log';events=folder/'events.log'
 if not done.exists() or not err.exists() or not events.exists():raise ValueError('실행 종료 로그 불완전. 재요청 금지')
 code=read(done).get('exit_code')
 if not isinstance(code,int) or code==0:raise ValueError('실패 종료가 확인되지 않음. 재요청 금지')
 rows=[]
 for line in events.read_text().splitlines():
  try:rows.append(json.loads(line))
  except ValueError:pass
 if any(isinstance(row,dict) and row.get('type')=='turn.completed' for row in rows):raise ValueError('완료 이벤트 발견. 기존 답안 복구부터 필요')
 error=err.read_text()
 if 'required MCP servers failed to initialize' not in error or 'dj_memory' not in error or 'thread/start' not in error:
  raise ValueError('확인한 기억 도구 세션 시작 실패와 다른 오류. 재요청 금지')
 return {'input_sha256':input_sha,'diagnostic':str(folder),'exit_code':code,'failure_stage':'session_start_memory_mcp'}
def recover(root,apply=False):
 state=root/'dj-manager-memory/runtime';active=read(state/'ACTIVATED.json');code=Path(active['code']).resolve()
 code.relative_to((root/'dj-manager-memory/runtime-code').resolve())
 for name,expected in EXPECTED.items():
  if sha((code/name).read_bytes())!=expected:raise ValueError('검증한 운영 코드와 다름: '+name)
 service=subprocess.check_output(['systemctl','show','dj-remembered-manager.service','-p','ActiveState','--value'],text=True).strip()
 if service not in ('inactive','failed'):raise ValueError('실행 중. 중복 요청 금지')
 public=subprocess.check_output(['systemctl','show','dj-remembered-products.timer','-p','ActiveState','--value'],text=True).strip()
 if public=='active' and not (root/'dj-public-products/runtime/PAUSED.json').exists():raise ValueError('공개픽 작업 상태 확인 필요. 변경 없음')
 with (state/'worker.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  marker=state/'PAUSED.json'
  if not marker.is_file():raise ValueError('중단 표시 없음. 재개 기록 변경 없음')
  pause_raw=marker.read_bytes()
  if not read(marker).get('reason','').startswith('연습 응답 실패. 자동 재요청 없음.'):
   raise ValueError('연습 실행 실패 이외의 중단 상태. 변경 없음')
  # Exact retained wire bytes; no CLI client or inference is constructed.
  sys.path.insert(0,str(code));from manager_remembered_runtime import request_wire_text
  snapshots={};groups=[];seen=set()
  for p in sorted(state.rglob('*.pending.json')):
   if 'deployments' in p.relative_to(state).parts or 'paper-original' in p.parts or answered(p):continue
   base=str(p)[:-len('.pending.json')];receipt=read(Path(base+'.packet.json'));packet=receipt['packet']
   key=(packet.get('analyst'),packet.get('mode'))
   if key not in ALLOWED or key in seen:raise ValueError('확인한 네 요청 밖의 대기 기록. 변경 없음')
   if read(p).get('request_id')!=receipt.get('request_id'):raise ValueError('요청 식별자 불일치')
   for suffix in ('.output.json',):
    if Path(base+suffix).exists() and Path(base+suffix).stat().st_size>0:raise ValueError('원본 응답 출력 발견. 답안 복구 확인부터 필요')
   wire=request_wire_text(packet).encode('utf-8')
   evidence=verify_failed_run(state/'diagnostics',sha(wire),p.stat().st_mtime)
   seen.add(key);snapshots[p]=p.read_bytes()
   if key[1]=='candidates':
    native=p.parent/'paper-original'/p.name
    if not native.is_file() or answered(native):raise ValueError('내부 실행 기록이 확인한 실패 상태와 다름')
    snapshots[native]=native.read_bytes()
   groups.append({'analyst':key[0],'mode':key[1],'request':str(p.relative_to(state)),**evidence})
  outstanding=[p for p in state.rglob('*.pending.json') if 'deployments' not in p.relative_to(state).parts and not answered(p)]
  if seen!=ALLOWED or len(snapshots)!=7 or set(outstanding)!=set(snapshots):raise ValueError('확인한 4건·7개 대기 기록과 다름. 변경 없음')
  print(json.dumps({'check':'PASS','verified_failed_requests':groups,'preserved_pending_files':7,'AI_requests':0,'apply':apply},ensure_ascii=False,indent=2),flush=True)
  if not apply:return
  if marker.read_bytes()!=pause_raw or read(state/'ACTIVATED.json')!=active or any(p.read_bytes()!=raw for p,raw in snapshots.items()):raise ValueError('검증 도중 기록 변경. 재개 없음')
  for name,expected in EXPECTED.items():
   if sha((code/name).read_bytes())!=expected:raise ValueError('검증 도중 코드 변경. 재개 없음')
  backup=state/'deployments'/('verified-memory-retry-v16-'+str(time.time_ns()));backup.mkdir(parents=True,mode=0o700)
  (backup/'failure_verification.json').write_text(json.dumps(groups,ensure_ascii=False,indent=2))
  (backup/'PAUSED.before.json').write_bytes(pause_raw)
  moved=[]
  try:
   for p in snapshots:
    dest=backup/'pending_before'/p.relative_to(state);dest.parent.mkdir(parents=True,exist_ok=True)
    p.rename(dest);moved.append((p,dest))
   marker.rename(backup/'PAUSED.resolved.json')
  except BaseException:
   for original,dest in reversed(moved):dest.rename(original)
   raise
  print('VERIFIED_RETRY_READY · 네 요청의 실제 실패 확인 · 기존 대기 기록 백업: '+str(backup),flush=True)
  print('소스·기억·답안·픽 변경 없음 · 서비스 시작 전 AI 요청 없음',flush=True)
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/home/ubuntu'));p.add_argument('--apply',action='store_true');a=p.parse_args();recover(a.root.resolve(),a.apply)
if __name__=='__main__':os.umask(0o077);main()
