"""Verify, stage and activate the independent public-products worker on EC2."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen

EXPECTED = {'app.py': '73c8376744787d9f4dc0e9385374bd897ed012f1c018e9b353ab9235d9c83972', 'analyst_products.py': '27dbaf684eccc8427403b30ecddf6fd9273ac2d6e34eb11942d8a0dd327f32fd', 'scorecard_ui.py': '874635082358ec0f9855ef9ebc7429412e428d833bc55fd318ce7191bcaaa248', 'remembered_products_contract.py': '34ddb2d53682c9e7af871877490eae605d30d3fe40e17554424ca6e6bc071b80', 'remembered_products_inputs.py': 'fbd022399434a5fd0b2061d6a99778e1c2176ee05b1f61815d667b92cca2eb81', 'remembered_products_runtime.py': 'cd10bf239d164fcf8249f54234ee2575c686f2bfbb07c14c836ffb5df06e54ea', 'remembered_products_transport.py': '0c5f458b2b9a36e2e1aa3fbb3048f42a7d9b49cc50c148fe07c51ae2c2503585', 'remembered_products_ui.py': '094e83eceb33d3525479798345ce8be819338279fbdca40d409c9012cd78c0c4', 'manager_memory_inputs.py': '870d8662e09282ae893327f0024958508e18488b06fe58873b6726ac48097032', 'manager_evidence_recovery.py': '95d54b3c210c98a6a8a1bf70d02e3ea00c3c7275e4e67723ffb0897e015a4680', 'VERIFY_PUBLIC_PRODUCTS.py': 'b01ebd94130b4aedba355fa21b6d749c4cb8d7b002663f09a55c5e13ae52e544'}
ROOT=Path('/home/ubuntu')
STATE=ROOT/'dj-public-products/runtime'
BUILD='public-products-'+EXPECTED['remembered_products_runtime.py'][:12]
CODE=ROOT/'dj-public-products/code'/BUILD
SERVICE='dj-remembered-products.service'
TIMER='dj-remembered-products.timer'


def run(args,**kw): return subprocess.run(args,check=True,**kw)
def atomic(path,raw):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.installing')
    with tmp.open('wb') as f: f.write(raw);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


def main():
    os.umask(0o077)
    if not ROOT.is_dir(): raise SystemExit('EC2의 ubuntu 계정 터미널에서 실행하세요.')
    manager=ROOT/'dj-manager-memory/runtime'
    if any((p/'PAUSED.json').exists() for p in (manager,STATE)):
        raise SystemExit('중단 기록 확인 필요. 자동 재개하지 않았습니다.')
    activation=json.loads((manager/'ACTIVATED.json').read_text())
    release=Path(activation['release'])
    if not (release/'MEMORY_RESTORE_VERIFIED.json').exists(): raise SystemExit('기존 분석가 기억 확인 필요')
    proof=STATE/'deployments'/BUILD/'installed.json'
    if proof.exists():
        print('이미 설치한 공개픽 연결입니다. 중복 분석을 시작하지 않습니다.');return
    if (STATE/'ACTIVATED.json').exists():
        raise SystemExit('기존 공개픽 실행 버전이 있습니다. 덮어쓰지 않았습니다.')
    sources={}
    for name,expected in EXPECTED.items():
        with urlopen('https://raw.githubusercontent.com/chleowhd77-ops/-/main/'+name,timeout=30) as response:
            raw=response.read()
        if hashlib.sha256(raw).hexdigest()!=expected: raise SystemExit('업로드 파일 확인 필요: '+name)
        compile(raw.decode('utf-8-sig'),name,'exec');sources[name]=raw
    for name,raw in sources.items():
        path=CODE/name
        if path.exists() and path.read_bytes()!=raw: raise SystemExit('설치 폴더의 다른 파일 보존: '+name)
        atomic(path,raw)
    sys.path[:0]=[str(CODE),str(ROOT)]
    from remembered_products_inputs import load_inputs
    from remembered_products_runtime import memory_for
    from remembered_products_contract import ENGINES
    from remembered_products_transport import split_packets,write,stamp
    inputs=load_inputs(ROOT);pool=inputs['proto']+inputs['toto14']
    if not pool: raise SystemExit('시작 전 경기 원자료 연결을 먼저 확인해야 합니다. '+json.dumps(inputs['excluded'],ensure_ascii=False))
    report={'checked_at':stamp(),'eligible_proto':len(inputs['proto']),'eligible_toto14':len(inputs['toto14']),
        'excluded':inputs['excluded'],'analysts':{},'AI_requests':0}
    for engine in ENGINES:
        memory=memory_for(release,manager,STATE,engine,pool)
        batches=split_packets({'mode':'analyze','analyst':engine,'memory':memory},'questions',pool)
        report['analysts'][engine]={'correct_memory':len(memory['successful_memory']),
            'error_memory':len(memory['error_memory']),'review_batches':len(batches)}
    write(STATE/'readiness.json',report)
    print(json.dumps(report,ensure_ascii=False),flush=True)
    launcher='''import os,sys
from pathlib import Path
os.chdir('/home/ubuntu')
try:
    from dotenv import load_dotenv
    load_dotenv('/home/ubuntu/.env')
except ImportError:
    pass
sys.path[:0]=[CODE_PATH,'/home/ubuntu']
sys.argv=['remembered_products_runtime.py','--root','/home/ubuntu','--release',RELEASE_PATH]
from remembered_products_runtime import main
main()
'''.replace('CODE_PATH',repr(str(CODE))).replace('RELEASE_PATH',repr(str(release)))
    atomic(CODE/'start_products.py',launcher.encode())
    unit=f'''[Unit]
Description=Remembered analysts PROTO TOP3 and Toto14
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu
Environment=HOME=/home/ubuntu
Environment=PYTHONUTF8=1
Environment=PYTHONUNBUFFERED=1
Environment={json.dumps('PATH='+os.environ['PATH'])}
ExecStart={sys.executable} {CODE/'start_products.py'}
UMask=0077
Restart=no
TimeoutStartSec=infinity
StandardOutput=journal
StandardError=journal
'''
    timer=f'''[Unit]
Description=Refresh public analyst picks and result reviews
[Timer]
OnBootSec=2min
OnUnitInactiveSec=15min
Unit={SERVICE}
[Install]
WantedBy=timers.target
'''
    # Read privileged unit files through sudo; don't repeat the earlier permission error.
    run(['sudo','-n','true'])
    units={SERVICE:unit,TIMER:timer}
    for name,content in units.items():
        path=Path('/etc/systemd/system')/name
        if path.exists():
            previous=subprocess.check_output(['sudo','-n','cat',str(path)],text=True)
            if previous!=content: raise SystemExit('같은 이름의 다른 서비스를 보존했습니다: '+name)
    for name,content in units.items():
        run(['sudo','-n','tee','/etc/systemd/system/'+name],input=content,text=True,stdout=subprocess.DEVNULL)
    write(STATE/'ACTIVATED.json',{'code':str(CODE),'release':str(release),'activated_at':stamp(),
        'subscription_only':True,'scope':['proto','top3','toto14'],'manager_picks_changed':False})
    run(['sudo','-n','systemctl','daemon-reload'])
    run(['sudo','-n','systemctl','enable','--now',TIMER])
    run(['sudo','-n','systemctl','start','--no-block',SERVICE])
    write(proof,{'build':BUILD,'files':EXPECTED,'installed_at':stamp()})
    print('공개픽 분석 실행 요청 완료. 네 분석가가 순서대로 분석하며 ChatGPT 구독 사용량이 발생합니다.')
    print('관리자픽·구매내역·기존 채점 원본은 변경하지 않았습니다.')
    print('실시간 확인: sudo journalctl -u '+SERVICE+' -n 20 -f -o cat')


if __name__=='__main__':main()
