"""Probe Codex session/MCP startup only. Never send turn/start or football prompts."""
import json,os,queue,signal,subprocess,sys,tempfile,threading,time
from pathlib import Path
ALLOWED={'initialize','initialized','thread/start','mcpServerStatus/list'}
def main():
 state=Path('/home/ubuntu/dj-manager-memory/runtime');code=Path(json.loads((state/'ACTIVATED.json').read_text())['code'])
 sys.path.insert(0,str(code));sys.path.insert(0,str(code/'practice_original'))
 from manager_memory_access import command
 from manager_cli_runtime import redact
 from analyst_codex_generator import subscription_environment
 refs=sorted((state/'cycles').rglob('*.memory-access.reference.json'),key=lambda p:p.stat().st_mtime,reverse=True)
 if not refs:raise SystemExit('기억 참조 파일 없음. 분석 요청 없음')
 ref=json.loads(refs[0].read_text());cli='/home/ubuntu/.local/bin/codex'
 with tempfile.TemporaryDirectory(prefix='dj-memory-connection-') as tmp:
  argv_holder=[]
  class Capture:
   def _command(self,argv,*args):argv_holder.extend(argv)
  command(Capture(),[cli,'app-server'],tmp,None,30,ref,Path(tmp)/'connection-probe.jsonl')
  argv=argv_holder[:1]+['-c','forced_login_method="chatgpt"','-c','model_provider="openai"','-c','approval_policy="never"','-c','features.shell_tool=false','-c','features.unified_exec=false','-c','web_search="disabled"','-c','mcp_servers.dj_memory.experimental_environment="local"','-c','mcp_servers.dj_memory.cwd='+json.dumps(str(code))]+argv_holder[1:]
  with (Path(tmp)/'stderr.log').open('w+') as err:
   p=subprocess.Popen(argv,cwd=tmp,env=subscription_environment(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,text=True,start_new_session=True)
   incoming=queue.Queue()
   def consume():
    for line in p.stdout:
     try:incoming.put(json.loads(line))
     except ValueError:pass
    incoming.put({'closed':True})
   threading.Thread(target=consume,daemon=True).start()
   def send(method,params=None,rid=None):
    if method not in ALLOWED:raise ValueError('분석 요청 차단')
    msg={'method':method,'params':params or {}}
    if rid is not None:msg['id']=rid
    p.stdin.write(json.dumps(msg)+'\n');p.stdin.flush()
   def await_id(rid,seconds):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
     try:msg=incoming.get(timeout=max(.01,until-time.monotonic()))
     except queue.Empty:raise TimeoutError(str(seconds)+'초 응답 없음')
     if msg.get('closed'):raise RuntimeError('Codex 연결 종료')
     if msg.get('id')==rid:return msg
     if msg.get('method')=='mcpServer/startupStatus/updated':
      params=msg.get('params',{});print('도구 시작 상태:',redact(json.dumps(params,ensure_ascii=False))[:800],flush=True)
    raise TimeoutError('연결 확인 시간 초과')
   try:
    print('연결 검사 시작 · 픽 분석·학습 요청 없음 · 로컬 실행 위치를 임시 명시',flush=True)
    send('initialize',{'clientInfo':{'name':'dj_memory_connection_check','version':'1.0'},'capabilities':{'experimentalApi':True}},0)
    msg=await_id(0,10)
    if 'error' in msg:raise ValueError(redact(str(msg['error'])))
    send('initialized');print('Codex 초기 연결 PASS',flush=True)
    send('thread/start',{'cwd':tmp,'approvalPolicy':'never','sandbox':'readOnly','ephemeral':True},1)
    msg=await_id(1,45)
    if 'error' in msg:raise ValueError(redact(str(msg['error'])))
    print('임시 세션 생성 PASS · 분석 turn/start 전송하지 않음',flush=True)
    send('mcpServerStatus/list',{'limit':100},2);msg=await_id(2,10)
    if 'error' in msg:raise ValueError(redact(str(msg['error'])))
    entries=msg.get('result',{}).get('data',[])
    found=[r for r in entries if r.get('name',r.get('serverName'))=='dj_memory']
    if not found:print('기억 도구 상태 목록 없음: 세션 성공과 도구 노출을 별도 확인해야 함',flush=True)
    for r in found:
     tools=r.get('tools',{});names=list(tools) if isinstance(tools,dict) else [t.get('name') for t in tools]
     print('기억 도구:',r.get('name',r.get('serverName')),'사용 가능 도구:',names,flush=True)
   except (TimeoutError,RuntimeError,ValueError,BrokenPipeError) as e:print('연결 검사 결과:',redact(str(e)),flush=True)
   finally:
    if p.poll() is None:
     os.killpg(p.pid,signal.SIGTERM)
     try:p.wait(timeout=3)
     except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
    err.flush();err.seek(0);text=redact(err.read())
    if text:print('연결 상세 오류:\n'+text[-4000:],flush=True)
    print('검사 종료 · 기존 소스·기억·대기 요청·발행 픽 변경 없음 · 분석 요청 없음',flush=True)
if __name__=='__main__':main()
