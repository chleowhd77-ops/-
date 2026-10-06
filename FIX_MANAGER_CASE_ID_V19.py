"""Recover a validated saved V2 answer without AI requests or resuming analysis."""
import argparse,ast,base64,fcntl,hashlib,json,os,subprocess,sys,tempfile,time
from pathlib import Path
ORIGINAL_ASK="def ask(self, folder, name, packet):\n    folder = Path(folder)/'paper-original'\n    if packet['mode'] == 'candidates':\n        outgoing, cases, mappings = exam_packet(packet)\n        reference=packet.get('memory',{})\n        memory_ids=[]\n        if reference.get('retrieval_policy')=='server-memory-library-v1':\n            from manager_memory_access import NOTE,verify_reads\n            if not self.exam.instructions.startswith(NOTE):self.exam.instructions=NOTE+self.exam.instructions\n            audit=folder/(name+'.memory-access.jsonl')\n            attach_lossless_transport(self.exam,reference,audit)\n        raw = self.exam.ask(folder, name, outgoing)\n        if reference.get('retrieval_policy')=='server-memory-library-v1':memory_ids=verify_reads(audit,reference)\n        values = self.practice.predictions(raw, cases)\n        picks = [{'case_id':cid, 'selected_id':mappings[cid][a['selected_id']],\n                  'probability':a['scores'][a['selected_id']], 'reason':a['reason'],\n                  'scores':{mappings[cid][k]:v for k,v in a['scores'].items()},\n                  'memory_record_ids':memory_ids}\n                 for cid,a in values.items()]\n        return {'summary':raw['summary'], 'picks':picks, 'reflections':[]}\n    if packet['mode'] != 'review':\n        raise ValueError('모의투자 연결 대상 모드 아님')\n    wrong, notes = review_packet(packet)\n    if wrong:\n        outgoing = {'mode':'review', 'analyst':packet['analyst'], 'wrong_answers':wrong}\n        raw = self.review.ask(folder, name, outgoing)\n        if raw.get('predictions'):\n            raise ValueError('복기에서 기존 픽 변경 차단')\n        notes += raw['reflections']\n    return {'summary':'모의투자 원본 복기 경로', 'picks':[], 'reflections':notes}"
OLD_LINE='values = self.practice.predictions(raw, cases)'
NEW_LINE="from manager_case_identity import validated_predictions\n            values = validated_predictions(self.practice, raw, cases, folder/(name+'.case-id-repair.json'))"
HELPER='IiIiVmFsaWRhdGUgc2F2ZWQgYW5zd2VyczsgcmVwYWlyIG9ubHkgb25lIGV4cGxpY2l0bHkgaWRlbnRpZmlhYmxlIGNhc2UtSUQgdHlwby4iIiIKaW1wb3J0IGNvcHkKaW1wb3J0IGhhc2hsaWIKaW1wb3J0IGpzb24KZnJvbSBwYXRobGliIGltcG9ydCBQYXRoCmltcG9ydCByZQoKCmRlZiBkaWdlc3QodmFsdWUpOgogICAgcmV0dXJuIGhhc2hsaWIuc2hhMjU2KGpzb24uZHVtcHModmFsdWUsIGVuc3VyZV9hc2NpaT1GYWxzZSwgc29ydF9rZXlzPVRydWUsCiAgICAgICAgc2VwYXJhdG9ycz0oJywnLCAnOicpKS5lbmNvZGUoKSkuaGV4ZGlnZXN0KCkKCgpkZWYgbm9ybWFsaXplZCh2YWx1ZSk6CiAgICByZXR1cm4gcmUuc3ViKHInW14wLTlhLXrqsIAt7Z6jXScsICcnLCBzdHIodmFsdWUpLmNhc2Vmb2xkKCkpCgoKZGVmIHZhbGlkYXRlZF9wcmVkaWN0aW9ucyhwcmFjdGljZSwgcmF3LCBjYXNlcywgYXVkaXRfcGF0aCk6CiAgICAiIiJOZXZlciBhbHRlciB0aGUgcmF3IGFuc3dlciwgcHJvYmFiaWxpdGllcywgc2VsZWN0aW9uIG9yIG9yaWdpbmFsIHZhbGlkYXRvci4iIiIKICAgIHRyeToKICAgICAgICByZXR1cm4gcHJhY3RpY2UucHJlZGljdGlvbnMocmF3LCBjYXNlcykKICAgIGV4Y2VwdCBWYWx1ZUVycm9yIGFzIGVycm9yOgogICAgICAgIGlmIHN0cihlcnJvcikgIT0gJ1VuZXhwZWN0ZWQgcHJhY3RpY2UgbWF0Y2ggSURzJzoKICAgICAgICAgICAgcmFpc2UKICAgIHZhbHVlcyA9IHJhdy5nZXQoJ3ByZWRpY3Rpb25zJywgW10pCiAgICBleHBlY3RlZCA9IHtjWydjYXNlX2lkJ106YyBmb3IgYyBpbiBjYXNlc30KICAgIHJldHVybmVkID0ge3ZbJ2Nhc2VfaWQnXSBmb3IgdiBpbiB2YWx1ZXN9CiAgICBtaXNzaW5nLCBleHRyYSA9IHNldChleHBlY3RlZCktcmV0dXJuZWQsIHJldHVybmVkLXNldChleHBlY3RlZCkKICAgIGlmIChsZW4odmFsdWVzKSAhPSBsZW4oY2FzZXMpIG9yIGxlbihyZXR1cm5lZCkgIT0gbGVuKHZhbHVlcykKICAgICAgICAgICAgb3IgbGVuKG1pc3NpbmcpICE9IDEgb3IgbGVuKGV4dHJhKSAhPSAxKToKICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCfqsr3quLAgSUQg67O16rWsIOu2iOqwgDog64u17JWIIOuIhOudvcK37KSR67O1IOuYkOuKlCDsl6zrn6wg6rK96riwIOu2iOydvOy5mCcpCiAgICBvcmlnaW5hbF9pZCwgY29ycmVjdGVkX2lkID0gbmV4dChpdGVyKGV4dHJhKSksIG5leHQoaXRlcihtaXNzaW5nKSkKICAgIGlmIChub3QgcmUuZnVsbG1hdGNoKHInWzAtOWEtZl17NjR9JywgY29ycmVjdGVkX2lkKQogICAgICAgICAgICBvciBub3QgcmUuZnVsbG1hdGNoKHInWzAtOWEtZl17MjQsNjR9Jywgb3JpZ2luYWxfaWQpKToKICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCfqsr3quLAgSUQg67O16rWsIOu2iOqwgDog7ZW07IucIO2YleyLnSDtmZXsnbgg7ZWE7JqUJykKICAgIHByZWZpeCA9IDAKICAgIGZvciBhLCBiIGluIHppcChvcmlnaW5hbF9pZCwgY29ycmVjdGVkX2lkKToKICAgICAgICBpZiBhICE9IGI6CiAgICAgICAgICAgIGJyZWFrCiAgICAgICAgcHJlZml4ICs9IDEKICAgIGlmIHByZWZpeCA8IDI0IG9yIHN1bShjaWQuc3RhcnRzd2l0aChvcmlnaW5hbF9pZFs6MjRdKSBmb3IgY2lkIGluIGV4cGVjdGVkKSAhPSAxOgogICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoJ+qyveq4sCBJRCDrs7Xqtawg67aI6rCAOiDsm5Drs7jqs7zsnZgg64uo64+FIOyXsOqysCDtmZXsnbgg67aI6rCAJykKICAgIGFuc3dlciA9IG5leHQodiBmb3IgdiBpbiB2YWx1ZXMgaWYgdlsnY2FzZV9pZCddID09IG9yaWdpbmFsX2lkKQogICAgaWRlbnRpdHkgPSBleHBlY3RlZFtjb3JyZWN0ZWRfaWRdWydxdWVzdGlvbiddWydpZGVudGl0eSddCiAgICByZWFzb24gPSBub3JtYWxpemVkKGFuc3dlci5nZXQoJ3JlYXNvbicsICcnKSkKICAgIHRlYW1zID0gW25vcm1hbGl6ZWQoaWRlbnRpdHkuZ2V0KGssICcnKSkgZm9yIGsgaW4gKCdob21lJywgJ2F3YXknKV0KICAgIGlmIGFueShsZW4odGVhbSkgPCAyIG9yIHRlYW0gbm90IGluIHJlYXNvbiBmb3IgdGVhbSBpbiB0ZWFtcykgb3IgdGVhbXNbMF0gPT0gdGVhbXNbMV06CiAgICAgICAgcmFpc2UgVmFsdWVFcnJvcign6rK96riwIElEIOuzteq1rCDrtojqsIA6IOyEoO2DnSDsnbTsnKDsnZgg7ZmIwrfsm5DsoJXtjIAg7ZmV7J24IO2VhOyalCcpCiAgICByZXBhaXJlZCA9IGNvcHkuZGVlcGNvcHkocmF3KQogICAgbmV4dCh2IGZvciB2IGluIHJlcGFpcmVkWydwcmVkaWN0aW9ucyddIGlmIHZbJ2Nhc2VfaWQnXSA9PSBvcmlnaW5hbF9pZClbJ2Nhc2VfaWQnXSA9IGNvcnJlY3RlZF9pZAogICAgcmVzdWx0ID0gcHJhY3RpY2UucHJlZGljdGlvbnMocmVwYWlyZWQsIGNhc2VzKQogICAgcmVjb3JkID0geydwb2xpY3knOidzaW5nbGUtY2FzZS1pZC1yZXBhaXItdjE5JywgJ29yaWdpbmFsX3Jlc3BvbnNlX2RpZ2VzdCc6ZGlnZXN0KHJhdyksCiAgICAgICAgJ2Nhc2VzX2RpZ2VzdCc6ZGlnZXN0KGNhc2VzKSwgJ29yaWdpbmFsX2lkJzpvcmlnaW5hbF9pZCwgJ2NvcnJlY3RlZF9pZCc6Y29ycmVjdGVkX2lkLAogICAgICAgICdpZGVudGl0eSc6aWRlbnRpdHksICdjb21tb25fcHJlZml4X2NoYXJzJzpwcmVmaXgsCiAgICAgICAgJ2NvcnJlY3RlZF9yZXNwb25zZV9kaWdlc3QnOmRpZ2VzdChyZXBhaXJlZCksCiAgICAgICAgJ3Jhd19hbnN3ZXJfY2hhbmdlZCc6RmFsc2UsICdhbmFseXNpc19jaGFuZ2VkJzpGYWxzZSwgJ0FJX3JlcXVlc3RzJzowfQogICAgcGF0aCA9IFBhdGgoYXVkaXRfcGF0aCkKICAgIGlmIHBhdGguZXhpc3RzKCk6CiAgICAgICAgaWYganNvbi5sb2FkcyhwYXRoLnJlYWRfdGV4dCgpKSAhPSByZWNvcmQ6CiAgICAgICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoJ+q4sOyhtCDqsr3quLAgSUQg67O16rWsIOq4sOuhnSDrtojsnbzsuZgnKQogICAgZWxzZToKICAgICAgICBwYXRoLnBhcmVudC5ta2RpcihwYXJlbnRzPVRydWUsIGV4aXN0X29rPVRydWUpCiAgICAgICAgd2l0aCBwYXRoLm9wZW4oJ3gnLCBlbmNvZGluZz0ndXRmLTgnKSBhcyBmOgogICAgICAgICAgICBqc29uLmR1bXAocmVjb3JkLCBmLCBlbnN1cmVfYXNjaWk9RmFsc2UsIGluZGVudD0yKQogICAgcmV0dXJuIHJlc3VsdAo='

MANAGER = 'dj-remembered-manager.service'
TIMER = 'dj-remembered-manager.timer'

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def bridge_patch(raw):
    text = raw.decode('utf-8-sig')
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PaperConnection')
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'ask')
    expected = ast.parse(ORIGINAL_ASK).body[0]
    node_text = ast.get_source_segment(text, node)
    changed = ORIGINAL_ASK.replace(OLD_LINE, NEW_LINE.replace("\n            ","\n        "))
    updated = ast.parse(changed).body[0]
    same = lambda a,b: ast.dump(a, include_attributes=False) == ast.dump(b, include_attributes=False)
    if same(node, updated):
        return raw
    if not same(node, expected):
        raise ValueError('운영 관리자 연결 함수가 기준과 다름 · 변경 없음')
    if node_text.count(OLD_LINE) != 1:
        raise ValueError('관리자 답안 검증 연결 위치 확인 필요 · 변경 없음')
    # Preserve all other source text; add only the ID validation adapter.
    segment = node_text.replace(OLD_LINE, NEW_LINE)
    lines = text.splitlines(keepends=True)
    newline = '\r\n' if b'\r\n' in raw else '\n'
    # get_source_segment includes original indentation after its first line.
    lines[node.lineno-1:node.end_lineno] = ['    '+segment.replace('\r\n','\n').replace('\n',newline)+newline]
    value = ''.join(lines).encode()
    if raw.startswith(b'\xef\xbb\xbf'):
        value = b'\xef\xbb\xbf'+value
    after = ast.parse(value.decode('utf-8-sig'))
    ac = next(n for n in after.body if isinstance(n,ast.ClassDef) and n.name=='PaperConnection')
    an = next(n for n in ac.body if isinstance(n,ast.FunctionDef) and n.name=='ask')
    if not same(an, updated):
        raise ValueError('관리자 연결 수정 범위 확인 실패')
    # Compare the entire module after restoring just this method in the new AST.
    ac.body[ac.body.index(an)] = node
    if not same(tree,after):
        raise ValueError('대상 함수 외 운영 코드 변경 감지')
    compile(value.decode('utf-8-sig'),'manager_practice_bridge.py','exec')
    return value


def find_recovery(state, rt, bridge, validator):
    from manager_memory_access import NOTE, verify_reads
    # Constructors set up local wrappers only. No check() or ask() is called.
    settings = {'codex_command':str(Path.home()/'.local/bin/codex'),
                'pause_on_network_loss':False, 'max_output_bytes':8*1024*1024}
    paper = bridge.PaperConnection(settings, state/'PAUSED.json')
    blocked = lambda *a,**k: (_ for _ in ()).throw(RuntimeError('복구 중 AI 요청 차단'))
    paper.exam.client._command = blocked
    paper.review.client._command = blocked
    repairs = []
    for p in sorted(state.glob('cycles/*/v2/**/*.packet.json')):
        receipt = read(p)
        packet = receipt.get('packet',{})
        if packet.get('analyst')!='v2' or packet.get('mode')!='candidates':
            continue
        name = p.name.removesuffix('.packet.json')
        answer_path = p.with_name(name+'.answer.json')
        pending_path = p.with_name(name+'.pending.json')
        raw_path = p.parent/'paper-original'/(name+'.json')
        if answer_path.exists() or not pending_path.exists() or not raw_path.exists():
            continue
        expected_id = rt.digest([rt.VERSION, rt.INSTRUCTION, rt.SCHEMA, packet])
        if receipt.get('request_id')!=expected_id or read(pending_path).get('request_id')!=expected_id:
            raise ValueError('V2 원본 요청·대기 기록 불일치')
        outgoing, cases, mappings = bridge.exam_packet(packet)
        ref = packet.get('memory',{})
        instructions = paper.exam.instructions
        if ref.get('retrieval_policy')=='server-memory-library-v1' and not instructions.startswith(NOTE):
            instructions = NOTE+instructions
        native_id = bridge.originals()[1].digest([instructions,outgoing,paper.exam.schema,settings.get('model')])
        saved = read(raw_path)
        if saved.get('request_id')!=native_id or saved.get('backend')!='codex_chatgpt_subscription':
            raise ValueError('V2 저장 답안이 실제 원본 요청과 다름 · AI 재요청 없음')
        raw = saved['response']
        with tempfile.TemporaryDirectory(prefix='dj-v19-validate-') as tmp:
            audit = Path(tmp)/'repair.json'
            values = validator(bridge.originals()[1],raw,cases,audit)
            if not audit.exists():
                continue
            record = read(audit)
        memory_ids = verify_reads(p.parent/'paper-original'/(name+'.memory-access.jsonl'),ref) if ref.get('retrieval_policy')=='server-memory-library-v1' else []
        picks = [{'case_id':cid,'selected_id':mappings[cid][a['selected_id']],
                  'probability':a['scores'][a['selected_id']], 'reason':a['reason'],
                  'scores':{mappings[cid][k]:v for k,v in a['scores'].items()},
                  'memory_record_ids':memory_ids} for cid,a in values.items()]
        response = {'summary':raw['summary'],'picks':picks,'reflections':[]}
        rt.validate_picks(response,packet['questions'],len(packet['questions']))
        if len(picks)!=len(packet['questions']):
            raise ValueError('복구 후 전체 답안 검증 실패')
        promoted = {'request_id':expected_id,'response':response,
                    'backend':'unchanged_paper_investment_code','created_at':rt.stamp(),
                    'saved_raw_response_recovered':True,'AI_requests':0}
        repairs.append({'packet':p, 'pending':pending_path, 'raw':raw_path,
            'answer':answer_path,'audit':raw_path.with_name(name+'.case-id-repair.json'),
            'record':record,'promoted':promoted, 'count':len(picks)})
    if len(repairs)!=1:
        raise ValueError('단일 V2 답안 복구 대상 확인 필요: '+str(len(repairs)))
    return repairs


def atomic(path, raw):
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd,tmp = tempfile.mkstemp(dir=path.parent,prefix='.case-id-')
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp,mode); os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def service_active(name):
    return subprocess.check_output(['systemctl','show',name,'-p','ActiveState','--value'],text=True).strip()


def install(root, apply=False):
    state = root/'dj-manager-memory/runtime'
    code = Path(read(state/'ACTIVATED.json')['code']).resolve()
    code.relative_to((root/'dj-manager-memory/runtime-code').resolve())
    bridge_path = code/'manager_practice_bridge.py'
    helper_path = code/'manager_case_identity.py'
    if bridge_path.is_symlink() or helper_path.is_symlink():
        raise ValueError('연결 원본 경로 확인 필요')
    patched = bridge_patch(bridge_path.read_bytes())
    helper = base64.b64decode(HELPER)
    if helper_path.exists() and helper_path.read_bytes()!=helper:
        raise ValueError('다른 ID 검증 코드 존재 · 변경 없음')
    compile(helper.decode(),'manager_case_identity.py','exec')
    paused = state/'PAUSED.json'
    if not paused.exists() or read(paused).get('reason')!='Unexpected practice match IDs':
        raise ValueError('현재 중단 사유 확인 필요 · 자동 해제하지 않음')
    if service_active(MANAGER) not in ('inactive','failed'):
        raise ValueError('관리자 분석 실행 중 · 종료 후 확인 필요')
    if bridge_path.read_bytes()==patched and helper_path.exists() and helper_path.read_bytes()==helper:
        for receipt in sorted(state.glob('deployments/case-id-v19-*/installed.json'),reverse=True):
            saved=read(receipt)
            if (saved.get('pause_sha256')==sha(paused.read_bytes())
                    and saved.get('files') and all(Path(name).is_file() and sha(Path(name).read_bytes())==value for name,value in saved['files'].items())
                    and all(Path(name).is_file() and sha(Path(name).read_bytes())==value for name,value in saved['raw_source_hashes'].items())):
                print('V19 이미 복구 완료 · 일시중지 유지 · AI 요청 0회',flush=True)
                return
    sys.path[:0] = [str(code),str(root)]
    import manager_remembered_runtime as rt
    import manager_practice_bridge as bridge
    namespace = {}
    exec(compile(helper.decode(),'<local-case-id-check>','exec'),namespace)
    validator = namespace['validated_predictions']
    with (state/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        repairs = find_recovery(state,rt,bridge,validator)
        # Collect all hashes now and verify unchanged before writing.
        observed = {p:p.read_bytes() for p in (bridge_path,paused)}
        for r in repairs:
            for p in (r['packet'],r['pending'],r['raw']): observed[p]=p.read_bytes()
        encoded = lambda v: json.dumps(v,ensure_ascii=False,indent=2).encode()
        changes = {bridge_path:patched, helper_path:helper}
        for r in repairs:
            if r['audit'].exists() and read(r['audit'])!=r['record']:
                raise ValueError('기존 ID 복구 기록 불일치')
            changes[r['answer']] = encoded(r['promoted'])
            if not r['audit'].exists(): changes[r['audit']]=encoded(r['record'])
        print('ID 단독 일치·홈/원정팀·전체 선택지/확률·원본 요청·기억 조회 검증 PASS',flush=True)
        for r in repairs:
            print('V2 저장 답안 '+str(r['count'])+'경기 복구 가능 · ID 1개 연결 정정 · AI 요청 0회',flush=True)
        if not apply:
            print('확인만 완료 · 운영 원본 변경 없음',flush=True);return
        timer_active=service_active(TIMER)=='active'
        stopped=False
        try:
            if timer_active:
                subprocess.run(['sudo','-n','systemctl','stop',TIMER],check=True);stopped=True
            if service_active(MANAGER) not in ('inactive','failed'):
                raise ValueError('분석 실행 상태 변경 · 수정 보류')
            for p,raw in observed.items():
                if p.read_bytes()!=raw: raise ValueError('확인 중 원본 변경 · 수정 보류')
            old={p:p.read_bytes() if p.exists() else None for p in changes}
            backup=state/'deployments'/('case-id-v19-'+str(time.time_ns()))
            backup.mkdir(parents=True,mode=0o700)
            entries=[]
            for i,(p,raw) in enumerate(old.items()):
                name=f'{i:02d}-{p.name}.before'
                if raw is not None:(backup/name).write_bytes(raw)
                entries.append({'path':str(p),'backup':name if raw is not None else None,'sha256':sha(raw) if raw is not None else None})
            (backup/'PAUSED.before.json').write_bytes(observed[paused])
            (backup/'manifest.json').write_bytes(encoded(entries))
            changed=[]
            try:
                for p,raw in changes.items():
                    if old[p]!=raw:atomic(p,raw);changed.append(p)
                for p,raw in changes.items():
                    if p.read_bytes()!=raw:raise ValueError('복구 파일 저장 검증 실패')
                for p,raw in observed.items():
                    if p!=bridge_path and p.read_bytes()!=raw:raise ValueError('원본 답안/대기/중단 기록 변경 감지')
                (backup/'installed.json').write_bytes(encoded({'AI_requests':0,'analysis_resumed':False,
                    'paused_preserved':True,'pause_sha256':sha(observed[paused]),
                    'files':{str(p):sha(raw) for p,raw in changes.items()},'recovered_answer_count':sum(r['count'] for r in repairs),
                    'raw_source_hashes':{str(r['raw']):sha(observed[r['raw']]) for r in repairs}}))
            except BaseException:
                for p in reversed(changed):
                    if old[p] is None:p.unlink()
                    else:atomic(p,old[p])
                raise
            print('CASE_ID_V19_RECOVERED · V2 원본 분석 보존 · 재분석 요청 0회',flush=True)
            print('일시중지 유지 · 관리자 분석 자동 재개 없음 · 백업: '+str(backup),flush=True)
        finally:
            if stopped:subprocess.run(['sudo','-n','systemctl','start',TIMER],check=True)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,default=Path('/home/ubuntu'))
    p.add_argument('--apply',action='store_true')
    args=p.parse_args()
    try:install(args.root,args.apply)
    except Exception as error:
        print('복구 중단 · 자동 재분석 없음: '+str(error),flush=True);raise SystemExit(1)

if __name__=='__main__':
    os.umask(0o077);sys.dont_write_bytecode=True;main()
