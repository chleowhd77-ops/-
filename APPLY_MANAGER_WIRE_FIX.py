"""Manager-only lossless wire fix. Preflight makes no AI requests."""
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

EXPECTED = {'manager_remembered_runtime.py': '2b4daabe066587ab2199657a0a747c66a9f3a3363530c4002082b936c574b50d', 'remembered_products_packing.py': 'f9547f950759ba9a78df3033830140d07267ca487ac54cffce204ad76f9ee1e0'}
OLD_SHA = 'b6f305d702c8a926023ae327f454045c439223a3c17111ba97861ec40561aa52'
ROOT = Path('/home/ubuntu')
STATE = ROOT/'dj-manager-memory/runtime'
SERVICE = 'dj-remembered-manager.service'
TIMER = 'dj-remembered-manager.timer'
BUILD = 'manager-wire-' + EXPECTED['manager_remembered_runtime.py'][:12]
ALLOWED_REASONS = {
    '기억과 한 경기의 원자료가 전송 한도를 넘음. 원문 유지 후 중단',
    '요청 용량 한도 초과. 원문 유지 후 중단',
}


def sha(raw): return hashlib.sha256(raw).hexdigest()
def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def run(*args): return subprocess.run(args,check=True)
def status(name):
    return subprocess.check_output(['systemctl','show',name,'-p','ActiveState','--value'],text=True).strip()
def atomic(path, raw):
    fd, name = tempfile.mkstemp(prefix='.wire-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def preflight(runtime, release):
    # Match the runtime's first unfinished cycle, using only saved local data.
    pending = [p for p in sorted((STATE/'cycles').glob('*/inputs.json'))
               if not all((p.parent/e/'completed.json').exists() for e in runtime.ENGINES)]
    if not pending:
        raise SystemExit('검사할 미완료 경기 묶음이 없습니다. 분석을 새로 요청하지 않았습니다.')
    cycle = pending[0]; raw_pool = read(cycle)['inputs']['pool']
    report = {'AI_requests':0,'cycle':str(cycle),'analysts':{}}
    for engine in runtime.ENGINES:
        folder = cycle.parent/engine
        if (folder/'completed.json').exists(): continue
        own = [read(p) for p in (STATE/'picks'/engine).glob('*.json')]
        own = [p for p in own if p['identity']['kickoff']>time.time()]
        ids = {p['identity']['fixture_id'] for p in own}
        pool = [q for q in raw_pool if q['identity']['fixture_id'] not in ids]
        if not pool or len(own)>=10: continue
        memory = runtime.append_live_memory(runtime.load_memory(release,engine,pool),STATE,engine,pool)
        base = {'mode':'candidates','analyst':engine,'memory':memory,'review_scope':'all_provided_questions'}
        packets_file = folder/'packets.json'
        # Prepared requests remain byte-for-byte intact so old answers can be reused.
        packets = read(packets_file) if packets_file.exists() else runtime.split_packets(base,'questions',pool)
        sizes = []
        for packet in packets:
            size = len(runtime.wire_text(runtime.INSTRUCTION,packet))
            if size>runtime.MAX_CHARS:
                raise SystemExit(engine+': 무손실 변환 후에도 용량 초과. 기존 중단을 유지합니다.')
            sizes.append(size)
        report['analysts'][engine]={'packets':len(packets),'largest_wire_characters':max(sizes,default=0)}
        print(engine+': 실제 저장자료 전송 검사 통과 / 최대 '+str(max(sizes,default=0))+'자',flush=True)
    return report


def main():
    os.umask(0o077)
    if status(SERVICE) not in ('inactive','failed'):
        raise SystemExit('관리자 작업 실행 중입니다. 강제 종료하지 않았습니다.')
    if status('dj-remembered-products.timer')=='active' or status('dj-remembered-products.service') not in ('inactive','failed'):
        raise SystemExit('공개픽이 실행 중입니다. 관리자 전용 상태 확인이 필요합니다.')
    activation = read(STATE/'ACTIVATED.json')
    code = Path(activation['code']).resolve()
    code.relative_to((ROOT/'dj-manager-memory/runtime-code').resolve())
    release = Path(activation['release']).resolve()
    release.relative_to((ROOT/'dj-manager-memory/releases').resolve())
    unit = subprocess.check_output(['systemctl','cat',SERVICE],text=True)
    if str(code/'start_manager.py') not in unit:
        raise SystemExit('활성 코드와 서비스 경로가 다릅니다. 변경하지 않았습니다.')
    audit = STATE/'deployments'/BUILD
    if (audit/'installed.json').exists():
        print('이미 적용한 수정입니다. 중복 분석을 요청하지 않았습니다.'); return
    sources = {}
    for name, expected in EXPECTED.items():
        with urlopen('https://raw.githubusercontent.com/chleowhd77-ops/-/main/'+name,timeout=30) as response:
            raw = response.read()
        if sha(raw)!=expected: raise SystemExit('업로드 파일 확인 필요: '+name)
        compile(raw.decode('utf-8-sig'),name,'exec'); sources[name]=raw
    run('sudo','-n','true')
    with (STATE/'worker.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise SystemExit('관리자 작업 실행 중. 변경하지 않았습니다.')
        original = (code/'manager_remembered_runtime.py').read_bytes()
        if sha(original)!=OLD_SHA:
            raise SystemExit('기존 관리자 코드 버전이 다릅니다. 변경하지 않았습니다.')
        marker = STATE/'PAUSED.json'
        if not marker.exists() or read(marker).get('reason') not in ALLOWED_REASONS:
            raise SystemExit('용량 초과 중단 기록이 아닙니다. 임의로 재개하지 않았습니다.')
        for p in STATE.rglob('*.pending.json'):
            if not p.with_name(p.name.replace('.pending.json','.answer.json')).exists():
                raise SystemExit('이전 AI 요청의 완료 여부 확인 필요. 재요청하지 않았습니다.')
        with tempfile.TemporaryDirectory(prefix='manager-wire-check-') as temp:
            stage = Path(temp)
            for name, raw in sources.items(): (stage/name).write_bytes(raw)
            sys.path[:0] = [str(stage),str(code),str(ROOT)]
            spec = importlib.util.spec_from_file_location('manager_wire_check',stage/'manager_remembered_runtime.py')
            runtime = importlib.util.module_from_spec(spec); spec.loader.exec_module(runtime)
            report = preflight(runtime,release)
        audit.mkdir(parents=True,exist_ok=True)
        originals = {name:(code/name).read_bytes() if (code/name).exists() else None for name in sources}
        for name, raw in originals.items():
            if raw is not None: atomic(audit/(name+'.before'),raw)
        atomic(audit/'PAUSED.before.json',marker.read_bytes())
        atomic(audit/'preflight.json',json.dumps(report,ensure_ascii=False,indent=2).encode())
        try:
            for name, raw in sources.items(): atomic(code/name,raw)
            for name, expected in EXPECTED.items():
                if sha((code/name).read_bytes())!=expected: raise RuntimeError('설치 검증 실패')
        except BaseException:
            for name, raw in originals.items():
                if raw is not None: atomic(code/name,raw)
                elif (code/name).exists(): (code/name).unlink()
            raise
        # Only the known manager size pause is archived. All answers/data remain.
        marker.rename(audit/'PAUSED.resolved.json')
        atomic(audit/'installed.json',json.dumps({'files':EXPECTED,'preflight':report,'installed_at':time.time()}).encode())
    run('sudo','-n','systemctl','enable','--now',TIMER)
    run('sudo','-n','systemctl','start','--no-block',SERVICE)
    print('관리자 전송 수정 적용 및 관리자픽 재개 요청 완료. 공개픽은 기존 중단 상태입니다.')
    print('이후 관리자 분석에는 구독 사용량이 발생합니다. 설치 검사는 AI 요청 0회입니다.')
    print('sudo journalctl -u dj-remembered-manager.service -n 20 -f -o short-iso')


if __name__=='__main__': main()
