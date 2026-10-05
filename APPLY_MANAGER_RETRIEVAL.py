"""Install manager-only local memory retrieval; never resume AI automatically."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

EXPECTED={'manager_remembered_runtime.py': '8bc1872498aab8a134bcece90b2d5c79b1d8f1fdc652c52a3701b79ba068d214', 'manager_memory_retrieval.py': '562c6294e993c9698c135d69f6ddb68fd0077f4218af79ad9efcbded2973215c', 'remembered_products_packing.py': 'f9547f950759ba9a78df3033830140d07267ca487ac54cffce204ad76f9ee1e0'}
ALLOWED_RUNTIME={
 'b6f305d702c8a926023ae327f454045c439223a3c17111ba97861ec40561aa52',
 '2b4daabe066587ab2199657a0a747c66a9f3a3363530c4002082b936c574b50d',
}
ROOT=Path('/home/ubuntu')
STATE=ROOT/'dj-manager-memory/runtime'
SERVICE='dj-remembered-manager.service'
TIMER='dj-remembered-manager.timer'
BUILD='related-memory-'+EXPECTED['manager_remembered_runtime.py'][:12]


def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def status(name):
    return subprocess.check_output(['systemctl','show',name,'-p','ActiveState','--value'],text=True).strip()
def atomic(path,raw):
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.retrieval-')
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def preflight(runtime,release):
    report={'AI_requests':0,'memory_limit':12000,'request_limit':runtime.MAX_CHARS,'analysts':{}}
    pending=[p for p in sorted((STATE/'cycles').glob('*/inputs.json'))
             if not all((p.parent/e/'completed.json').exists() for e in runtime.ENGINES)]
    if not pending:
        report['note']='미완료 경기 묶음 없음. 신규 분석을 실행하지 않았습니다.'
        return report
    cycle=pending[0];pool=read(cycle)['inputs']['pool']
    for engine in runtime.ENGINES:
        folder=cycle.parent/engine
        if (folder/'completed.json').exists():continue
        memory=runtime.append_live_memory(runtime.load_memory(release,engine,pool),STATE,engine,pool)
        base={'mode':'candidates','analyst':engine,'memory':memory,'review_scope':'all_provided_questions'}
        source_plans=read(folder/'packets.json') if (folder/'packets.json').exists() else [dict(base,questions=pool)]
        sizes=[];memory_sizes=[];reused=0
        for i,packet in enumerate(source_plans):
            name=f'candidates-{i:03d}'
            if (folder/(name+'.answer.json')).exists():
                reused+=1;continue
            if (folder/(name+'.pending.json')).exists():continue
            parts=runtime.split_packets({k:v for k,v in packet.items() if k!='questions'},'questions',packet['questions'])
            for part in parts:
                sizes.append(len(runtime.wire_text(runtime.INSTRUCTION,part)))
                memory_sizes.append(len(json.dumps(part['memory'],ensure_ascii=False,separators=(',',':'))))
        report['analysts'][engine]={'unstarted_parts':len(sizes),'max_request_characters':max(sizes,default=0),
            'max_memory_characters':max(memory_sizes,default=0),'saved_answers_reused':reused}
        print(engine+': 검사 통과 / 기억 최대 '+str(max(memory_sizes,default=0))+'자 / 요청 최대 '+str(max(sizes,default=0))+'자',flush=True)
    return report


def main():
    os.umask(0o077)
    if status(SERVICE) not in ('inactive','failed'):
        raise SystemExit('관리자 분석 실행 중입니다. 먼저 관리자 서비스를 중단해 주세요. 변경하지 않았습니다.')
    activation=read(STATE/'ACTIVATED.json')
    code=Path(activation['code']).resolve();code.relative_to((ROOT/'dj-manager-memory/runtime-code').resolve())
    release=Path(activation['release']).resolve();release.relative_to((ROOT/'dj-manager-memory/releases').resolve())
    unit=subprocess.check_output(['sudo','-n','systemctl','cat',SERVICE],text=True)
    if str(code/'start_manager.py') not in unit:raise SystemExit('활성 코드 경로 확인 필요')
    audit=STATE/'deployments'/BUILD
    if (audit/'installed.json').exists():
        print('이미 적용한 수정입니다. 자동으로 재시작하지 않았습니다.');return
    sources={}
    for name,expected in EXPECTED.items():
        with urlopen('https://raw.githubusercontent.com/chleowhd77-ops/-/main/'+name,timeout=30) as response:raw=response.read()
        if sha(raw)!=expected:raise SystemExit('업로드 파일 확인 필요: '+name)
        compile(raw.decode('utf-8-sig'),name,'exec');sources[name]=raw
    subprocess.run(['sudo','-n','systemctl','stop',TIMER],check=True)
    with (STATE/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('분석 작업 실행 중. 코드 변경 없음')
        if sha((code/'manager_remembered_runtime.py').read_bytes()) not in ALLOWED_RUNTIME:
            raise SystemExit('관리자 코드 버전이 다릅니다. 변경하지 않았습니다.')
        unresolved=[str(p.relative_to(STATE)) for p in STATE.rglob('*.pending.json')
                    if not p.with_name(p.name.replace('.pending.json','.answer.json')).exists()]
        with tempfile.TemporaryDirectory(prefix='manager-retrieval-check-') as temp:
            stage=Path(temp)
            for name,raw in sources.items():(stage/name).write_bytes(raw)
            sys.path[:0]=[str(stage),str(code),str(ROOT)]
            spec=importlib.util.spec_from_file_location('manager_retrieval_check',stage/'manager_remembered_runtime.py')
            runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
            report=preflight(runtime,release)
        report['unresolved_requests']=unresolved
        originals={name:(code/name).read_bytes() if (code/name).exists() else None for name in sources}
        audit.mkdir(parents=True,exist_ok=True)
        for name,raw in originals.items():
            if raw is not None:atomic(audit/(name+'.before'),raw)
        if (STATE/'PAUSED.json').exists():atomic(audit/'PAUSED.before.json',(STATE/'PAUSED.json').read_bytes())
        atomic(audit/'preflight.json',json.dumps(report,ensure_ascii=False,indent=2).encode())
        try:
            for name,raw in sources.items():atomic(code/name,raw)
            for name,expected in EXPECTED.items():
                if sha((code/name).read_bytes())!=expected:raise RuntimeError('설치 검증 실패')
        except BaseException:
            for name,raw in originals.items():
                if raw is not None:atomic(code/name,raw)
                elif (code/name).exists():(code/name).unlink()
            raise
        # Existing pause and all pending/answer/review/source records are kept.
        if not (STATE/'PAUSED.json').exists():
            atomic(STATE/'PAUSED.json',json.dumps({'paused_at':runtime.stamp(),
                'reason':'기억 검색 방식 수정 완료. 사용자 재개 대기','automatic_retry':False},ensure_ascii=False).encode())
        atomic(audit/'installed.json',json.dumps({'files':EXPECTED,'time':time.time(),'automatic_resume':False}).encode())
    print('관리자 기억 검색 방식 적용 완료. 분석과 타이머는 중단 상태를 유지합니다. AI 요청 0회.')
    if unresolved:print('이전 요청 완료 확인 필요: '+str(len(unresolved))+'건. 자동 재요청하지 않았습니다.')


if __name__=='__main__':main()
