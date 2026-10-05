"""Retire pre-AI training only. Preserve match DB and AI picks/reviews/receipts."""
import ast
from contextlib import ExitStack
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

EXPECTED={'manager_remembered_runtime.py': 'ab7614250eee62b9a963077db50788c4ec02a92b8a0404af4c469c3d4054bc7e', 'manager_memory_retrieval.py': 'd2d79acd8bc6ab3120448b5ffb41d8cd9572d0bce7e3c179e19f7bb4c7cd4f79', 'remembered_products_packing.py': 'f9547f950759ba9a78df3033830140d07267ca487ac54cffce204ad76f9ee1e0'}
ROOT=Path('/home/ubuntu')
STATE=ROOT/'dj-manager-memory/runtime'
MANAGER='dj-remembered-manager.service'
COLLECTOR='dj-collector.service'
BASELINE='8bc1872498aab8a134bcece90b2d5c79b1d8f1fdc652c52a3701b79ba068d214'
BUILD='ai-only-'+EXPECTED['manager_remembered_runtime.py'][:12]

def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def status(unit):
    return subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','--value'],text=True).strip()
def control(action,unit):subprocess.run(['sudo','-n','systemctl',action,unit],check=True)
def atomic(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.ai-only-')
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def patch_legacy(raw,kind):
    text=raw.decode('utf-8-sig')
    guards=({'run_learning_job':'return True', '_execute_job':"if job_name == 'learning': return 0",
             '_launch_isolated_job':"if job_name == 'learning': return False",
             '_start_isolated_job':"if job_name == 'learning': return False"} if kind=='collector' else
            {'run_one':'return True'})
    lines=text.splitlines(keepends=True)
    nodes={n.name:n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)}
    inserts=[]
    for name,guard in guards.items():
        if name not in nodes:raise ValueError('기존 학습 진입점 확인 실패: '+name)
        node=nodes[name]
        if '# Pre-AI training retired by user.' in ''.join(lines[node.lineno:node.end_lineno]):continue
        inserts.append((node.body[0].lineno-1,'    # Pre-AI training retired by user.\n    '+guard+'\n'))
    for at,value in sorted(inserts,reverse=True):lines.insert(at,value)
    result=''.join(lines)
    # Drop only the legacy training schedules; collection/settlement schedules stay.
    if kind=='collector':
        result=result.replace('        _launch_isolated_job("learning")','        pass  # Legacy training retired')
        result=result.replace('    _launch_isolated_job("learning")\n','')
        result=result.replace('    schedule.every(2).minutes.do(_launch_isolated_job, "learning")\n','')
    compile(result,kind,'exec')
    return result.encode('utf-8')

def cleanup_targets(root,release):
    # Exact legacy-only directories, not DBs, AI state, or an entire release.
    targets=[root/n for n in ('.learning_checkpoints','.learning_models','.learning_course',
        'learning_status.json','learning_course_status.json',
        'DJ_LEARNING_LIGHT_20261002_232211_753783.zip',
        'DJ_LEARNING_STUDY_20261002.zip','DJ_LEARNING_CORE_20261002.zip')]
    targets.append(release/'derived_manager_memory')
    present=[]
    for path in targets:
        path.resolve().relative_to(root.resolve())
        # Refuse redirection, including a linked parent or nested file.
        for ancestor in (path,*path.parents):
            if ancestor==root:break
            if ancestor.is_symlink():raise ValueError('삭제 경로가 링크입니다: '+str(path))
        if not path.exists():continue
        if path.is_dir():
            for parent,dirs,files in os.walk(path,followlinks=False):
                for name in dirs+files:
                    if (Path(parent)/name).is_symlink():raise ValueError('학습 폴더 안 링크 발견: '+str(path))
        elif not path.is_file():raise ValueError('일반 학습 파일이 아닙니다: '+str(path))
        present.append(path)
    return present

def main():
    os.umask(0o077)
    sys.dont_write_bytecode=True
    if status(MANAGER) not in ('inactive','failed'):
        raise SystemExit('관리자 작업 실행 중입니다. 변경하지 않았습니다.')
    active=read(STATE/'ACTIVATED.json')
    code=Path(active['code']).resolve();code.relative_to(ROOT/'dj-manager-memory/runtime-code')
    release=Path(active['release']).resolve();release.relative_to(ROOT/'dj-manager-memory/releases')
    unit=subprocess.check_output(['sudo','-n','systemctl','cat',MANAGER],text=True)
    if str(code/'start_manager.py') not in unit:raise SystemExit('관리자 실행 경로 확인 필요')
    unit=subprocess.check_output(['sudo','-n','systemctl','cat',COLLECTOR],text=True)
    if str(ROOT/'collector.py') not in unit:raise SystemExit('자료수집 실행 경로 확인 필요')
    if sha((code/'manager_remembered_runtime.py').read_bytes()) not in {BASELINE,EXPECTED['manager_remembered_runtime.py']}:
        raise SystemExit('관리자 코드 버전 확인 필요. 변경 없음')
    sources={}
    for name,checksum in EXPECTED.items():
        with urlopen('https://raw.githubusercontent.com/chleowhd77-ops/-/main/'+name,timeout=30) as r:raw=r.read()
        if sha(raw)!=checksum:raise SystemExit('업로드 파일 확인 필요: '+name)
        compile(raw.decode('utf-8-sig'),name,'exec');sources[code/name]=raw
    for name,kind in [('collector.py','collector'),('learning_worker.py','worker')]:
        sources[ROOT/name]=patch_legacy((ROOT/name).read_bytes(),kind)
    # Validation is offline. The exported pre-AI archive is not opened.
    with tempfile.TemporaryDirectory() as temp:
        stage=Path(temp)
        for path,raw in sources.items():
            if path.parent==code:(stage/path.name).write_bytes(raw)
        sys.path[:0]=[str(stage),str(code),str(ROOT)]
        spec=importlib.util.spec_from_file_location('ai_only_check',stage/'manager_remembered_runtime.py')
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        if hasattr(runtime,'load_memory'):raise ValueError('이전 학습 연결이 남아 있습니다')
        packet={'mode':'candidates','analyst':'official','questions':[],
                'memory':{'analyst':'official','error_memory':[{'old':'school'}]}}
        if runtime.prepare_packet(packet)['memory']['records']:raise ValueError('이전 학습 배제 검사 실패')
    targets=cleanup_targets(ROOT,release)
    audit=STATE/'deployments'/BUILD;audit.mkdir(parents=True,exist_ok=True)
    collector_status=status(COLLECTOR)
    if collector_status not in ('active','inactive','failed'):
        raise SystemExit('자료수집 서비스 전환 중입니다. 삭제하지 않았습니다.')
    was_running=collector_status=='active'
    # Hold the AI lock before changing anything. Manager/public timers are untouched.
    with ExitStack() as stack:
        lock=stack.enter_context((STATE/'worker.lock').open('a'))
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            if was_running:control('stop',COLLECTOR)
            learning=stack.enter_context((ROOT/'.learning_worker.lock').open('a'))
            fcntl.flock(learning,fcntl.LOCK_EX|fcntl.LOCK_NB)
            # Revalidate after collector workers have exited.
            targets=cleanup_targets(ROOT,release)
            originals={path:path.read_bytes() if path.exists() else None for path in sources}
            for i,(path,raw) in enumerate(originals.items()):
                backup=audit/(str(i)+'-'+path.name+'.before')
                if raw is not None and not backup.exists():atomic(backup,raw)
            try:
                for path,raw in sources.items():atomic(path,raw)
                for path,raw in sources.items():
                    if path.read_bytes()!=raw:raise ValueError('설치 검증 실패')
            except BaseException:
                for path,raw in originals.items():
                    if raw is not None:atomic(path,raw)
                    elif path.exists():path.unlink()
                raise
            # Code is disabled first. Delete actual legacy artifacts, not archives of them.
            manifest={'AI_requests':0,'deleted':[],'files':{str(p):sha(b) for p,b in sources.items()},'time':time.time()}
            atomic(audit/'cleanup.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode())
            for path in targets:
                if path.is_dir():shutil.rmtree(path)
                else:path.unlink()
                manifest['deleted'].append(str(path))
                atomic(audit/'cleanup.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode())
            print('기존 학습 작업 차단 및 학습 파일 삭제 완료. 삭제 대상 '+str(len(targets))+'개.')
            print('실경기 DB와 AI 픽·결과·복기·완료 답안 보존. 관리자 AI 입력에서 이전 학습 제외. AI 호출 0회.')
        finally:
            if was_running:control('start',COLLECTOR)
    print(('자료수집 재개. ' if was_running else '자료수집 기존 정지 상태 유지. ')+
          '관리자 분석/타이머의 기존 정지 상태는 변경하지 않았습니다.')
    subprocess.run(['df','-h',str(ROOT)],check=True)

if __name__=='__main__':main()
