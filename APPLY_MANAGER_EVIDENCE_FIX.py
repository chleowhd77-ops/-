"""Install verified source files and launch one private comparison on EC2."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen

EXPECTED = {'collector.py': '4706893ab8b66c6a86d727193b5db14faf4846d04278959545aad14d5e92b566', 'manager_memory_inputs.py': '870d8662e09282ae893327f0024958508e18488b06fe58873b6726ac48097032', 'manager_evidence_recovery.py': '95d54b3c210c98a6a8a1bf70d02e3ea00c3c7275e4e67723ffb0897e015a4680', 'manager_evidence_compare.py': '07091b3e14781e16edcce9b5118edc973ce636d01e7a1f55f09dd3cac73f91f9'}
OLD_COLLECTOR_SHA = '73e545449cd842ad1524640ae0a2eeee0eca815d149e3457d226614bd0133b21'
OLD_INPUT_SHA = 'c496e1585169f0fc3500451afc20669375321bbac0db8accc70c469c2be2e039'
RUNTIME_SHA = 'b6f305d702c8a926023ae327f454045c439223a3c17111ba97861ec40561aa52'
ROOT = Path('/home/ubuntu')
STATE = ROOT/'dj-manager-memory/runtime'
BUILD = 'evidence-recovery-' + EXPECTED['manager_memory_inputs.py'][:12]
CODE = ROOT/'dj-manager-memory/runtime-code'/BUILD
AUDIT = STATE/'deployments'/BUILD
COMPARISON = STATE/'comparisons'/BUILD
SERVICE = 'dj-remembered-manager.service'
TIMER = 'dj-remembered-manager.timer'
COMPARE_SERVICE = 'dj-manager-evidence-compare.service'


def sha(raw): return hashlib.sha256(raw).hexdigest()
def run(args, **kw): return subprocess.run(args, check=True, **kw)
def active(name):
    return subprocess.check_output(['systemctl','show',name,'-p','ActiveState','--value'],text=True).strip()
def atomic(path, raw):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.installing')
    with tmp.open('wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path)
def unit_write(name, content):
    run(['sudo','-n','tee','/etc/systemd/system/'+name],input=content,text=True,stdout=subprocess.DEVNULL)


def main():
    os.umask(0o077); os.chdir(ROOT)
    if (STATE/'PAUSED.json').exists(): raise SystemExit('중단 기록 확인 필요. 자동 재개하지 않습니다.')
    if (AUDIT/'installed.json').exists():
        print('이미 설치한 수정입니다. 중복 분석을 시작하지 않습니다.'); return
    if active(SERVICE) not in ('inactive','failed'):
        raise SystemExit('관리자 분석 실행 중입니다. 완료 후 같은 명령을 실행하세요.')
    if active(COMPARE_SERVICE) not in ('inactive','failed',''):
        raise SystemExit('비교 서비스 실행 중입니다. 중복 실행하지 않습니다.')
    for path in (STATE/'cycles').glob('*/inputs.json'):
        if not all((path.parent/e/'completed.json').exists() for e in ('official','robot_proto','v2','v3')):
            raise SystemExit('미완료 분석이 있어 변경하지 않았습니다.')
    sources={}
    for name, expected in EXPECTED.items():
        with urlopen('https://raw.githubusercontent.com/chleowhd77-ops/-/main/'+name,timeout=30) as r:
            raw=r.read()
        if sha(raw)!=expected: raise SystemExit('GitHub 파일 검증 실패: '+name)
        compile(raw.decode('utf-8-sig'),name,'exec'); sources[name]=raw
    activation=json.loads((STATE/'ACTIVATED.json').read_text())
    old=Path(activation['code']); release=Path(activation['release'])
    if sha((old/'manager_remembered_runtime.py').read_bytes())!=RUNTIME_SHA:
        raise SystemExit('기존 분석 코드 버전 확인 필요')
    if sha((old/'manager_memory_inputs.py').read_bytes())!=OLD_INPUT_SHA:
        raise SystemExit('기존 자료 연결 코드 버전 확인 필요')
    original_collector=(ROOT/'collector.py').read_bytes()
    if sha(original_collector)!=OLD_COLLECTOR_SHA:
        raise SystemExit('서버 수집기 버전이 달라 중단했습니다. 결과를 보내주세요.')
    existing_helper=ROOT/'manager_evidence_recovery.py'
    if existing_helper.exists() and existing_helper.read_bytes()!=sources[existing_helper.name]:
        raise SystemExit('동명의 다른 자료복구 파일이 있어 중단합니다.')
    baselines=sorted((STATE/'cycles').glob('full-review-*/inputs.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    if not baselines: raise SystemExit('비교할 기존 재검토 기록 없음')
    baseline=baselines[0].parent
    run(['sudo','-n','true'])
    previous_unit=subprocess.check_output(['sudo','-n','cat','/etc/systemd/system/'+SERVICE],text=True)
    if previous_unit.count(str(old/'start_manager.py'))!=1:
        raise SystemExit('기존 서비스 실행 경로 확인 필요')
    new_unit=previous_unit.replace(str(old/'start_manager.py'),str(CODE/'start_manager.py'))
    launcher=(old/'start_manager.py').read_text().replace(str(old),str(CODE))
    comparison_launcher = '''import os, sys
from pathlib import Path
os.chdir('/home/ubuntu')
try:
    from dotenv import load_dotenv
    load_dotenv('/home/ubuntu/.env')
except ImportError:
    pass
sys.path[:0] = [CODE_PATH, '/home/ubuntu']
sys.argv = ['manager_evidence_compare.py','--root','/home/ubuntu','--release',RELEASE_PATH,'--comparison',COMPARISON_PATH]
from manager_evidence_compare import main
main()
'''.replace('CODE_PATH',repr(str(CODE))).replace('RELEASE_PATH',repr(str(release))).replace('COMPARISON_PATH',repr(str(COMPARISON)))
    compare_unit=previous_unit.replace(str(old/'start_manager.py'),str(CODE/'start_comparison.py'))
    compile(launcher,'start_manager.py','exec'); compile(comparison_launcher,'start_comparison.py','exec')
    timer_active=subprocess.run(['systemctl','is-active','--quiet',TIMER]).returncode==0
    collector_active=subprocess.run(['systemctl','is-active','--quiet','dj-collector.service']).returncode==0
    run(['sudo','-n','systemctl','stop',TIMER])
    mutated=False
    try:
        with (STATE/'worker.lock').open('a') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise SystemExit('분석 실행 중입니다. 변경하지 않았습니다.')
            if (STATE/'PAUSED.json').exists(): raise SystemExit('중단 기록 확인 필요')
            for path in (STATE/'cycles').glob('*/inputs.json'):
                if not all((path.parent/e/'completed.json').exists() for e in ('official','robot_proto','v2','v3')):
                    raise SystemExit('미완료 분석 기록이 있어 변경하지 않았습니다.')
            AUDIT.mkdir(parents=True,exist_ok=True)
            for name,raw in {'previous.collector.py':original_collector,
                             'previous.service':previous_unit.encode(),
                             'previous-activation.json':json.dumps(activation).encode()}.items():
                if not (AUDIT/name).exists(): atomic(AUDIT/name,raw)
            runtime_files={k:v for k,v in sources.items() if k!='collector.py'}
            runtime_files.update({'manager_remembered_runtime.py':(old/'manager_remembered_runtime.py').read_bytes(),
                'start_manager.py':launcher.encode(),'start_comparison.py':comparison_launcher.encode()})
            for name,raw in runtime_files.items():
                if (CODE/name).exists() and (CODE/name).read_bytes()!=raw:
                    raise SystemExit('기존 설치 파일 내용 불일치: '+name)
                atomic(CODE/name,raw)
            sys.path[:0]=[str(CODE),str(ROOT)]
            from manager_memory_inputs import load_pool, load_memory, ENGINES
            from manager_evidence_compare import prepare_engine
            import sqlite3
            pool=load_pool(ROOT)
            for e in ENGINES: load_memory(release,e,pool['pool'])
            with sqlite3.connect((ROOT/'ai_predictions.db').as_uri()+'?mode=ro',uri=True,timeout=5) as db:
                db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
                plans={e:prepare_engine(db,baseline/e,__import__('time').time()) for e in ENGINES}
            atomic(COMPARISON/'setup.json',json.dumps({'baseline_cycle':str(baseline),'scope':'private comparison only'},ensure_ascii=False).encode())
            for e,plan in plans.items():
                atomic(COMPARISON/e/'plan.json',json.dumps(plan,ensure_ascii=False).encode())
                print(e+': 복구 후 비교 대상 '+str(len(plan['changed']))+'경기')
            ledger={e:[json.loads(p.read_text()) for p in (STATE/'picks'/e).glob('*.json')] for e in ENGINES}
            atomic(COMPARISON/'before-ledger.json',json.dumps(ledger,ensure_ascii=False).encode())
            if collector_active: run(['sudo','-n','systemctl','stop','dj-collector.service'])
            mutated=True
            atomic(existing_helper,sources['manager_evidence_recovery.py'])
            atomic(ROOT/'collector.py',sources['collector.py'])
            unit_write(SERVICE,new_unit); unit_write(COMPARE_SERVICE,compare_unit)
            atomic(STATE/'ACTIVATED.json',json.dumps({**activation,'code':str(CODE),'evidence_repair':BUILD}).encode())
            run(['sudo','-n','systemctl','daemon-reload'])
            atomic(AUDIT/'installed.json',json.dumps({'build':BUILD,'files':EXPECTED}).encode())
    except BaseException:
        if mutated:
            atomic(ROOT/'collector.py',original_collector)
            unit_write(SERVICE,previous_unit)
            atomic(STATE/'ACTIVATED.json',json.dumps(activation).encode())
            run(['sudo','-n','systemctl','daemon-reload'])
        raise
    finally:
        if collector_active and active('dj-collector.service') in ('inactive','failed'):
            run(['sudo','-n','systemctl','start','dj-collector.service'])
        if timer_active: run(['sudo','-n','systemctl','start',TIMER])
    run(['sudo','-n','systemctl','start','--no-block',COMPARE_SERVICE])
    print('자료 복구 수정 적용 및 비교 실행 요청 완료. ChatGPT 구독 사용량이 발생합니다.')
    print('기존 픽·구매내역은 유지됩니다. 로그 확인:')
    print('sudo journalctl -u dj-manager-evidence-compare.service -n 30 -f -o cat')


if __name__=='__main__': main()
