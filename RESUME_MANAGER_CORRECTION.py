"""One explicitly authorized correction; normal frozen-pick rules are unchanged."""
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime

EXPECTED={'manager_remembered_runtime.py': '8a422890d4992474c1c853ce21ee724b03425b359b23e43fc6f6dad23db029b6', 'manager_memory_inputs.py': 'e16b640ab8470cba2a865c4691a9cd43da832992e5b82f92942a175dbd9d8046', 'manager_practice_bridge.py': '4af3f2ea5c202299f063a05237f81108e5577fbbf2b637c41de7fe158a9535c1', 'practice_original/analyst_codex_generator.py': 'e41c8d3fde67dd9e3a06c0ac33fe2c40baa73dc5f6c7f0ac4fd55a29c64779f4', 'practice_original/analyst_method_loop.py': '3e2bcf16cfbcd732f9073a00ecf8e0a2e8775a4c0b89e31457c77c2758b87b79', 'practice_original/analyst_remembered_exam.py': '14d51a9ec27f5a2e0d843e1d86f2b63a1332576695ac66296a00773bc8b55185', 'practice_original/analyst_ten_match_practice.py': '6673e8217891995fdd00e167d4753bf9ffe9a1e9278e33f7f58ea9e672e19d7c', 'practice_original/learning_state.py': '05b15b6226fc2d9e593318b1cefb9951b692542bec74ce61cf635932b287fb11', 'practice_original/manager_learning_goal.py': '81f729b65d3190cd00532d3f4ec0b4da5d581ea3b12420de6869062427bae345', 'practice_original/manifest.json': '911e44855a406911156ece53f66e59646bfc9e28a7563bf6b94103bb0f98b3f5', 'practice_original/scorecard_core.py': '7ca25664f45cd7080536d632d6d7f75ba85d082c60f3d1d5289486ba6b9e20f4', 'weather_observation.py': 'ccb0b70f53611e9413b0a2c93ffe4919776def760aaa679be86b4a16a338c7a3', 'analyst_connection.py': '86860d64213874b4ddbdbc4453e24226efbba27b80e951d3f999a2215b978726', 'manager_cli_runtime.py': '7adad8e679195b92b8d11c644ad59b3b60bb6e4b455cb63a54efccb1f7e998ff'}
ROOT=Path('/home/ubuntu')
STATE=ROOT/'dj-manager-memory/runtime'
CORRECTION='manager-correction-20261005-paper-weather'
SERVICE='dj-remembered-manager.service'
TIMER='dj-remembered-manager.timer'

def existing(rt,p):return rt.read(p) if p.exists() else None

def checked_authors(rt,release,stage):
    authors={engine:rt.Author(release,stage) for engine in rt.ENGINES}
    client=next(iter(authors.values())).client
    original=client._command
    def preflight(command,cwd,payload=None,timeout=15):
        if payload is not None or command[1:] not in (['exec','--help'],['login','status']):
            raise ValueError('사전 확인 중 분석 요청 차단')
        return original(command,cwd,payload=payload,timeout=60)
    print('실행·로그인 확인 1회 시작 (각 단계 최대 60초, AI 분석 호출 없음)',flush=True)
    client._command=preflight
    try:client.check()
    finally:client._command=original
    for author in authors.values():
        author.client.executable=client.executable
        author.checked=True
    print('실행·로그인 확인 완료. 네 분석가가 확인 결과를 공유합니다.',flush=True)
    return authors

def pick_path(state,engine,cid):
    if engine not in ('official','robot_proto','v2','v3') or not cid or any(c not in '0123456789abcdef' for c in cid):
        raise ValueError('잘못된 픽 저장 경로')
    p=state/'picks'/engine/(cid+'.json');p.resolve().relative_to(state.resolve())
    if p.is_symlink():raise ValueError('픽 경로가 링크입니다')
    return p

def restore(rt,state,changes):
    for change in changes:
        p=pick_path(state,change['engine'],change['case_id'])
        current=existing(rt,p)
        if current not in (change['old'],change['new']):raise ValueError('교체 중 외부 변경 감지')
    for change in changes:
        p=pick_path(state,change['engine'],change['case_id'])
        if change['old'] is not None:rt.write(p,change['old'])
        elif p.exists():p.unlink()

def run_locked(rt,root,state,release,audit):
    if (audit/'done.json').exists():
        print('이번 재분석은 이미 완료됐습니다. 추가 AI 호출 없음.');return
    if (audit/'applied.json').exists():
        rt.publish_payload(root,state)
        rt.write(audit/'done.json',{'finished_at':rt.stamp(),'repeated_AI_calls':0})
        print('저장된 재분석 결과 게시 완료. AI 재호출 없음.');return
    if (audit/'commit.json').exists():
        restore(rt,state,rt.read(audit/'commit.json')['changes'])
    if (state/'PAUSED.json').exists():raise ValueError('현재 관리자 중단 사유 확인 필요. 강제 해제하지 않습니다')
    stage=audit/'analysis'
    pause=check_resume_records(rt,state,stage)
    plan_path=audit/'plan.json'
    if not plan_path.exists():
        inputs=rt.load_pool(root);available={q['case_id']:q for q in inputs['pool']}
        targets=[];protected=[];now=time.time()
        for engine in rt.ENGINES:
            for p in (state/'picks'/engine).glob('*.json'):
                old=rt.read(p);cid=old['case_id']
                if old['identity']['kickoff']<=now:continue
                if (state/'grades'/engine/p.name).exists():continue
                entry={'engine':engine,'case_id':cid,'old':old}
                (targets if cid in available else protected).append(entry)
        if not targets:raise ValueError('재분석 가능한 시작 전 관리자픽이 없습니다. 기존 픽 유지')
        target_ids={t['case_id'] for t in targets}
        inputs['pool']=[q for q in inputs['pool'] if q['case_id'] in target_ids]
        rt.write(plan_path,{'targets':targets,'protected':protected,'inputs':inputs,
            'reason':'사용자가 이번 관리자픽 오류 수정에 한해 재분석 요청','created_at':rt.stamp()})
    plan=rt.read(plan_path)
    for item in plan['targets']+plan['protected']:
        if existing(rt,pick_path(state,item['engine'],item['case_id']))!=item['old']:
            raise ValueError('요청 후 기존 픽이 변경되었습니다. 덮어쓰지 않습니다')
    cp=stage/'cycles'/CORRECTION/'inputs.json'
    if not cp.exists():
        rt.write(cp,{'inputs':plan['inputs'],'created_at':rt.stamp()})
    for item in plan['protected']:
        p=pick_path(stage,item['engine'],item['case_id'])
        if not p.exists():rt.write(p,item['old'])
    print('이번 한 번 재분석: 시작 전 '+str(len(plan['inputs']['pool']))+'경기, 기존 픽 '+str(len(plan['targets']))+'건. 기존 화면은 완료까지 유지.',flush=True)
    authors=checked_authors(rt,release,stage)
    preserve_packet_receipts(rt,authors)
    if pause is not None:
        complete_authorized_resume(rt,state,stage,pause)
    publisher,review,memory=rt.publish_payload,rt.review_new,rt.append_live_memory
    try:
        # Execute the installed four-analyst workflow in a private correction area.
        # Its publisher is delayed until all four have saved their answers.
        rt.publish_payload=lambda *a:None
        rt.review_new=lambda *a:None
        rt.append_live_memory=lambda m,s,e,p:memory(m,state,e,p)
        rt.run_analysts(stage,root,release,cp,author_factory=lambda engine:authors[engine])
    finally:
        rt.publish_payload,rt.review_new,rt.append_live_memory=publisher,review,memory
    verify_completed(rt,stage,plan)
    now=time.time();changes={};protected={(t['engine'],t['case_id']) for t in plan['protected']}
    for item in plan['targets']:
        if item['old']['identity']['kickoff']<=now:continue
        changes[(item['engine'],item['case_id'])]={**item,'new':None}
    allowed={q['case_id'] for q in plan['inputs']['pool']}
    for engine in rt.ENGINES:
        for p in (stage/'picks'/engine).glob('*.json'):
            new=rt.read(p);cid=new['case_id'];key=(engine,cid)
            if key in protected or new['identity']['kickoff']<=now:continue
            if cid not in allowed:raise ValueError('이번 요청 밖의 경기입니다')
            old=existing(rt,pick_path(state,engine,cid))
            if old is not None and key not in changes:raise ValueError('교체 대상 외 픽 변경 차단')
            new={**new,'correction_id':CORRECTION,'supersedes_digest':rt.digest(old) if old else None}
            changes[key]={'engine':engine,'case_id':cid,'old':old,'new':new}
    changes=list(changes.values())
    for item in changes:
        p=pick_path(state,item['engine'],item['case_id'])
        if existing(rt,p)!=item['old']:raise ValueError('기존 픽 변경 감지')
        if (state/'grades'/item['engine']/p.name).exists():raise ValueError('이미 채점된 픽 변경 차단')
        if item['old'] is not None:
            rt.write(audit/'previous-picks'/item['engine']/p.name,item['old'])
    delivery=rt.optional(state/'delivery.json',{})
    rt.write(audit/'previous-delivery.json',delivery)
    rt.write(audit/'commit.json',{'changes':changes,'at':rt.stamp()})
    try:
        for item in changes:
            p=pick_path(state,item['engine'],item['case_id'])
            if item['new'] is not None:rt.write(p,item['new'])
            elif p.exists():p.unlink()
            delivery.pop(item['engine']+':'+item['case_id'],None)
        rt.write(state/'delivery.json',delivery)
        rt.write(audit/'applied.json',{'at':rt.stamp(),'changed':len(changes)})
    except Exception:
        restore(rt,state,changes)
        rt.write(state/'delivery.json',rt.read(audit/'previous-delivery.json'))
        raise
    rt.publish_payload(root,state)
    rt.write(audit/'done.json',{'finished_at':rt.stamp(),'changed':len(changes)})
    print('이번 관리자픽 재분석·교체 완료. 이전 픽 보관. 이후에는 시간 경과로 픽을 변경하지 않습니다.',flush=True)


"""One user-authorized retry of the recorded 2026-10-05 model-list failure."""
KNOWN_PAUSED_AT = '2026-10-05T04:44:26.373463+00:00'
RETRY_NAME = 'authorized-model-list-resume-v1'


def allowed_pending(stage):
    cycle = stage/'cycles'/CORRECTION
    return {p for engine in ('official','robot_proto','v2','v3') for p in (
        cycle/engine/'candidates-000.pending.json',
        cycle/engine/'paper-original/candidates-000-a.pending.json')}


def answered(rt, pending):
    suffixes = ['.answer.json']
    if 'paper-original' in pending.parts:
        suffixes.append('.json')
    for suffix in suffixes:
        p = pending.with_name(pending.name.replace('.pending.json', suffix))
        if p.exists():
            saved = rt.read(p)
            if saved.get('request_id') != rt.read(pending).get('request_id') or 'response' not in saved:
                raise ValueError('저장 답안과 요청 불일치: '+str(p))
            return True
    return False


def retry_paths(stage):
    folder = stage.parent/RETRY_NAME
    return folder, folder/'manifest.json', folder/'consumed.json'


def archived_pending(folder, relative):
    path = folder/'pending'/relative
    return path.with_name(path.name.replace('.pending.json','.failed-attempt.json'))


def known_pause(pause):
    return (isinstance(pause, dict) and pause.get('paused_at') == KNOWN_PAUSED_AT
        and pause.get('automatic_retry') is False
        and str(pause.get('reason','')).startswith('연습 응답 실패. 자동 재요청 없음.')
        and 'failed to refresh available models: request timed out' in pause['reason'])


def check_resume_records(rt, state, stage):
    folder, manifest_path, consumed = retry_paths(stage)
    pause = existing(rt, stage/'PAUSED.json')
    manifest = existing(rt, manifest_path)
    if consumed.exists():
        # A new failure never inherits the authorization for the old attempt.
        if pause is not None:
            raise ValueError('이번 재개 후 다시 중단됨. 자동 재시도 없음. '+str(pause.get('reason','')))
        for pending in state.rglob('*.pending.json'):
            if not answered(rt, pending):
                raise ValueError('재개 후 완료 미확인 요청 보존: '+str(pending.relative_to(state)))
        return None
    if pause is not None and not known_pause(pause):
        raise ValueError('확인한 모델 목록 시간 초과와 다른 중단 기록입니다. 변경 없음')
    if pause is None and manifest is None:
        raise ValueError('이번 재개 대상 중단 기록이 없습니다. 새 분석을 시작하지 않습니다')
    if manifest is not None:
        if not known_pause(manifest.get('pause')):
            raise ValueError('재개 보관 기록 불일치')
        if pause is not None and pause != manifest['pause']:
            raise ValueError('중단 사유 변경 감지')
    allowed = allowed_pending(stage)
    unresolved = []
    cutoff = datetime.fromisoformat(KNOWN_PAUSED_AT).timestamp()
    for pending in state.rglob('*.pending.json'):
        pending.resolve().relative_to(state.resolve())
        if answered(rt, pending):
            continue
        if pending not in allowed or pending.is_symlink():
            raise ValueError('이번 실패 외 완료 미확인 요청: '+str(pending.relative_to(state)))
        data = rt.read(pending)
        at = data.get('started_at') or data.get('created_at')
        when = datetime.fromisoformat(at.replace('Z','+00:00')).timestamp() if at else 0
        if not 0 < when <= cutoff:
            raise ValueError('이후 생성된 요청을 재시도하지 않습니다')
        # Raw output needs validation, not a blind replacement request.
        raw_output = pending.with_name(pending.name.replace('.pending.json','.output.json'))
        if raw_output.exists():
            raise ValueError('복구할 원본 응답이 있습니다. 보존: '+str(raw_output))
        unresolved.append(pending)
    if manifest is None:
        if not (stage.parent/'plan.json').exists():
            raise ValueError('기존 교정 계획이 없습니다. 새 대상을 만들지 않습니다')
        if not unresolved:
            raise ValueError('확인한 실패 요청이 없습니다. 기록 확인 필요')
    else:
        expected = {stage/row['path'] for row in manifest['pending']}
        if not set(unresolved).issubset(expected):
            raise ValueError('재개 계획 이후 새 요청 감지')
        for row in manifest['pending']:
            source = stage/row['path']; source.resolve().relative_to(stage.resolve())
            archive = archived_pending(folder,row['path']); archive.resolve().relative_to(folder.resolve())
            if source.exists() == archive.exists():
                raise ValueError('요청 기록 보관 상태 불일치')
            present = source if source.exists() else archive
            if hashlib.sha256(present.read_bytes()).hexdigest() != row['sha256']:
                raise ValueError('대기 기록 변경 감지')
    return pause if pause is not None else manifest['pause']


def complete_authorized_resume(rt, state, stage, pause):
    folder, manifest_path, consumed = retry_paths(stage)
    check_resume_records(rt, state, stage)
    if consumed.exists():
        return
    manifest = existing(rt, manifest_path)
    if manifest is None:
        rows = []
        for p in sorted(allowed_pending(stage)):
            if p.exists() and not answered(rt, p):
                rows.append({'path':str(p.relative_to(stage)),
                    'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        manifest = {'pause':pause, 'pending':rows, 'created_at':rt.stamp(),
            'authorization':'사용자가 설치 후 중단된 관리자픽 교정을 해결하고 재개하도록 요청함',
            'prior_usage':'unknown', 'automatic_retry':False}
        rt.write(manifest_path, manifest)
    # Persist the complete move plan before moving anything; recover partial moves.
    check_resume_records(rt, state, stage)
    for row in manifest['pending']:
        source = stage/row['path']; archive = archived_pending(folder,row['path'])
        if source.exists():
            archive.parent.mkdir(parents=True, exist_ok=True)
            source.rename(archive)
    source_pause = stage/'PAUSED.json'; archive_pause = folder/'previous-pause.json'
    if source_pause.exists():
        if archive_pause.exists():
            raise ValueError('중단 보관 파일 충돌')
        source_pause.rename(archive_pause)
    if not archive_pause.exists() or rt.read(archive_pause) != manifest['pause']:
        raise ValueError('이전 중단 기록 보관 확인 실패')
    rt.write(consumed, {'at':rt.stamp(), 'automatic_retry':False,
        'archived_pending':len(manifest['pending'])})
    print('이전 실패 대기 기록 '+str(len(manifest['pending']))+'건 보관 완료. 승인된 재개 1회. 완료 답안 재사용.',flush=True)


def preserve_packet_receipts(rt, authors):
    for author in authors.values():
        original = author.ask
        def ask(folder, name, packet, _original=original):
            receipt_path = Path(folder)/(name+'.packet.json')
            if receipt_path.exists():
                receipt = rt.read(receipt_path); saved = receipt['packet']
                if (receipt['request_id'] != rt.digest([rt.VERSION,rt.INSTRUCTION,rt.SCHEMA,saved])
                    or {k:v for k,v in saved.items() if k!='memory'} !=
                       {k:v for k,v in packet.items() if k!='memory'}):
                    raise ValueError('보관된 요청과 입력 불일치. 새 요청 없음')
                packet = saved
            return _original(folder,name,packet)
        author.ask = ask


def verify_completed(rt, stage, plan):
    allowed = {q['case_id'] for q in plan['inputs']['pool']}
    for engine in rt.ENGINES:
        folder = stage/'cycles'/CORRECTION/engine
        report = existing(rt,folder/'completed.json')
        if report is None:
            raise ValueError('분석가 완료 기록 누락: '+engine+' / 기존 픽 유지')
        for cid in report.get('frozen',[]):
            if cid not in allowed or not (stage/'picks'/engine/(cid+'.json')).exists():
                raise ValueError('교정 결과 저장 검증 실패: '+engine)


class ResumeLog:
    def __init__(self, terminal, file):
        self.terminal, self.file = terminal, file
    def write(self, value):
        self.terminal.write(value); self.file.write(value); self.file.flush()
    def flush(self):
        self.terminal.flush(); self.file.flush()


def resume_main():
    import os
    import traceback
    os.umask(0o077)
    code=Path(json.loads((STATE/'ACTIVATED.json').read_text())['code']).resolve()
    code.relative_to(ROOT/'dj-manager-memory/runtime-code')
    print('설치된 실행 파일 확인 중…',flush=True)
    for name,value in EXPECTED.items():
        if hashlib.sha256((code/name).read_bytes()).hexdigest()!=value:
            raise SystemExit('설치 코드 불일치: '+name+' / 분석 요청 없음')
    sys.path[:0]=[str(code),str(ROOT)]
    import manager_remembered_runtime as rt
    release=Path(rt.read(STATE/'ACTIVATED.json')['release'])
    audit=STATE/'corrections'/CORRECTION
    if (audit/'done.json').exists():
        print('이번 교정은 이미 완료됐습니다. 추가 AI 호출 없음.',flush=True)
        return
    was_active=subprocess.check_output(['systemctl','show',TIMER,'-p','ActiveState','--value'],text=True).strip()=='active'
    subprocess.run(['sudo','-n','systemctl','stop',TIMER],check=True)
    try:
        with (STATE/'worker.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise SystemExit('기존 관리자 작업이 실행 중입니다. 종료하지 않았고 추가 요청도 없습니다.')
            audit.mkdir(parents=True,exist_ok=True)
            log_path=audit/'resume-runtime-fix.log'
            terminal=sys.stdout
            with log_path.open('a',encoding='utf-8') as log:
                sys.stdout=ResumeLog(terminal,log)
                try:
                    print('관리자픽 교정 재개 '+rt.stamp(),flush=True)
                    run_locked(rt,ROOT,STATE,release,audit)
                except Exception as error:
                    from manager_cli_runtime import redact
                    print('교정 재개 중단: '+redact(str(error)),flush=True)
                    print(redact(traceback.format_exc()),flush=True)
                    print('완료 답안과 실패 기록을 보존했습니다. 이 실패를 자동 재요청하지 않습니다.',flush=True)
                    raise SystemExit(1)
                finally:
                    sys.stdout=terminal
    finally:
        if was_active:subprocess.run(['sudo','-n','systemctl','start',TIMER],check=True)


if __name__=='__main__':resume_main()
