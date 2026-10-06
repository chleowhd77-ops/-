"""Approved manager memory revision connection. No AI during installation."""
import argparse,fcntl,hashlib,json,os,subprocess,tempfile,time
from pathlib import Path
BEFORE='fe5ebc7ebdd86a1cee79fd4067862b10f9efce5cd86d63b28f6ccac21a642b7f'
AFTER='a0c1553def91a241c495d500a8e0b69d9384dfc8d8c562eacd73c9518f8ab95c'
OLD="    if packets_path.exists():\n        packets=read(packets_path)\n        for saved_packet in packets:\n            expected=prepare_packet(dict(saved_packet,memory=memory))['memory']\n            if saved_packet.get('memory') != expected:\n                raise ValueError('이전 요청은 현재 보존 기억과 다릅니다. 기록 보존 후 수동 확인 필요; AI 자동 재요청 없음')\n    else:\n        packets=split_packets(base,'questions',pool); write(packets_path,packets)\n"
NEW="    completion_folder=folder\n    if packets_path.exists():\n        packets=read(packets_path)\n        changed=any(saved.get('memory') != prepare_packet(dict(saved,memory=memory))['memory']\n                    for saved in packets)\n        if changed:\n            # Preserve every old request and answer. A new memory gets its own\n            # durable request namespace; restarting reuses only that namespace.\n            current_packets=split_packets(base,'questions',pool)\n            folder=completion_folder/'memory-revisions'/digest(current_packets)\n            packets_path=folder/'packets.json'\n            if packets_path.exists():\n                packets=read(packets_path)\n                if packets != current_packets:\n                    raise ValueError('기억 갱신 요청 원문 불일치. 기록 보존 후 중단')\n            else:\n                packets=current_packets; write(packets_path,packets)\n            print(f'{LABELS[engine]} 복기 후 기억 갱신 반영 · 이전 요청 보존 · 새 기억 요청 경로 {folder.name}',flush=True)\n    else:\n        packets=split_packets(base,'questions',pool); write(packets_path,packets)\n"
OLD_END="    write(folder/'completed.json',{'frozen':frozen,'finished_at':stamp(),\n                                  'reviewed_count':reviewed_count,'proposed_count':len(proposals)})\n"
NEW_END="    write(folder/'completed.json',{'frozen':frozen,'finished_at':stamp(),\n                                  'reviewed_count':reviewed_count,'proposed_count':len(proposals)})\n    if folder != completion_folder:\n        write(completion_folder/'completed.json',{'frozen':frozen,'finished_at':stamp(),\n              'reviewed_count':reviewed_count,'proposed_count':len(proposals),\n              'request_folder':str(folder.relative_to(completion_folder))})\n"
def sha(b):return hashlib.sha256(b).hexdigest()
def install(root,apply):
 state=root/'dj-manager-memory/runtime'
 active=json.loads((state/'ACTIVATED.json').read_text());code=Path(active['code']).resolve()
 code.relative_to((root/'dj-manager-memory/runtime-code').resolve())
 status=subprocess.check_output(['systemctl','show','dj-remembered-manager.service','-p','ActiveState','--value'],text=True).strip()
 if status not in ('inactive','failed'):raise ValueError('관리자 서비스 실행 중. 변경 없음')
 public=subprocess.check_output(['systemctl','show','dj-remembered-products.timer','-p','ActiveState','--value'],text=True).strip()
 if public=='active' and not (root/'dj-public-products/runtime/PAUSED.json').exists():raise ValueError('공개픽 영향 확인 필요. 변경 없음')
 with (state/'worker.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  target=code/'manager_remembered_runtime.py'
  if target.is_symlink():raise ValueError('링크 경로 중단')
  raw=target.read_bytes();h=sha(raw)
  if h not in (BEFORE,AFTER):raise ValueError('예상과 다른 관리자 소스. 변경 없음')
  text=raw.decode()
  if h==BEFORE:
   if text.count(OLD)!=1 or text.count(OLD_END)!=1:raise ValueError('수정 위치 불일치')
   updated=text.replace(OLD,NEW).replace(OLD_END,NEW_END).encode()
  else:updated=raw
  compile(updated,str(target),'exec')
  if sha(updated)!=AFTER:raise ValueError('결과 해시 불일치')
  pause=state/'PAUSED.json';pause_raw=pause.read_bytes() if pause.exists() else None
  if pause_raw is not None:
   reason=json.loads(pause_raw).get('reason','')
   if not reason.startswith('이전 요청은 현재 보존 기억과 다릅니다.'):
    raise ValueError('다른 중단 사유. 변경 없음: '+reason)
  print('검증 PASS · 이전 요청·답안·기억·동결픽 보존 · 설치 AI 요청 0회',flush=True)
  if not apply:return
  if target.read_bytes()!=raw or (pause.read_bytes() if pause.exists() else None)!=pause_raw:raise ValueError('검증 중 변경 감지')
  if h==AFTER and pause_raw is None:print('이미 적용됨');return
  audit=state/'deployments'/('memory-revision-v7-'+str(time.time_ns()));audit.mkdir(parents=True)
  (audit/target.name).write_bytes(raw)
  if h!=AFTER:
   fd,name=tempfile.mkstemp(dir=code,prefix='.memory-revision-')
   try:
    with os.fdopen(fd,'wb') as f:f.write(updated);f.flush();os.fsync(f.fileno())
    os.replace(name,target)
   finally:
    if os.path.exists(name):os.unlink(name)
  if pause_raw is not None:pause.rename(audit/'PAUSED.resolved.json')
  print('MEMORY_REVISION_INSTALLED · 백업: '+str(audit))
  print('관리자 서비스 시작 시 갱신된 기억으로 새 후보 분석. 이전 요청 자동 재전송 없음.')
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/home/ubuntu'));p.add_argument('--apply',action='store_true');a=p.parse_args();os.umask(0o077);install(a.root.resolve(),a.apply)
if __name__=='__main__':main()
