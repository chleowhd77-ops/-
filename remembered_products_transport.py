"""Subscription transport isolated from the running manager service."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
from manager_memory_inputs import digest, read
from remembered_products_contract import VERSION, INSTRUCTION, SCHEMA
MAX_CHARS = 780_000
class TransportFailure(RuntimeError):
    pass
def stamp():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.writing-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def optional(path, default):
    return read(path) if Path(path).exists() else copy.deepcopy(default)


def split_packets(base, field, records):
    """Transport partition only. Final portfolio is selected by the same analyst."""
    batches, batch = [], []
    for record in records:
        candidate = dict(base, **{field:batch+[record]})
        if len(INSTRUCTION+json.dumps(candidate,ensure_ascii=False,separators=(',',':'))) > MAX_CHARS:
            if not batch:
                raise ValueError('기억과 한 경기의 원자료가 전송 한도를 넘음. 원문 유지 후 중단')
            batches.append(dict(base,**{field:batch})); batch=[]
            candidate = dict(base, **{field:[record]})
            if len(INSTRUCTION+json.dumps(candidate,ensure_ascii=False,separators=(',',':'))) > MAX_CHARS:
                raise ValueError('기억과 한 경기의 원자료가 전송 한도를 넘음. 원문 유지 후 중단')
        batch.append(record)
    if batch:
        batches.append(dict(base,**{field:batch}))
    return batches


class Author:
    def __init__(self, release, state):
        # Only reuse the already verified CLI transport, not the old study loop.
        sys.path.insert(0,str(Path(release)/'windows/work/pro-subscription'))
        from analyst_codex_generator import CodexSubscriptionGenerator
        settings = {'codex_command':str(Path.home()/'.local/bin/codex'),
                    'pause_on_network_loss':True,'max_output_bytes':8*1024*1024}
        self.client = CodexSubscriptionGenerator(settings)
        self.state = Path(state)
        self.checked = False

    def ask(self, folder, name, packet):
        folder = Path(folder); folder.mkdir(parents=True,exist_ok=True)
        identity = digest([VERSION,INSTRUCTION,SCHEMA,packet])
        answer_path = folder/(name+'.answer.json')
        pending = folder/(name+'.pending.json')
        if answer_path.exists():
            saved=read(answer_path)
            if saved['request_id'] != identity:
                raise ValueError('저장된 요청과 현재 입력이 다릅니다')
            return saved['response']
        if pending.exists():
            raise ValueError('이전 요청 완료 여부 확인 필요. 자동으로 재요청하지 않습니다')
        if (self.state/'PAUSED.json').exists():
            raise ValueError('중단 상태. 직접 재개 필요')
        text=INSTRUCTION+json.dumps(packet,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        if len(text)>MAX_CHARS:
            raise ValueError('요청 용량 한도 초과. 원문 유지 후 중단')
        if not self.checked:
            try:
                self.client.check(); self.checked=True
            except Exception as exc:
                raise TransportFailure('구독 연결 확인 실패: '+type(exc).__name__) from exc
        write(folder/(name+'.packet.json'),{'request_id':identity,'packet':packet})
        write(pending,{'request_id':identity,'started_at':stamp()})
        # Durable output files survive an interrupted process for manual recovery.
        schema=folder/(name+'.schema.json'); output=folder/(name+'.output.json')
        write(schema,SCHEMA)
        with tempfile.TemporaryDirectory(prefix='dj-products-cli-') as tmp:
            command=[self.client.executable,'exec','--ignore-user-config','--ignore-rules',
                '--skip-git-repo-check','--ephemeral','--sandbox','read-only','--color','never',
                '--json','--cd',tmp,'--output-schema',str(schema.resolve()),
                '--output-last-message',str(output.resolve())]
            for setting in ('forced_login_method="chatgpt"','model_provider="openai"',
                'approval_policy="never"','web_search="disabled"','features.shell_tool=false',
                'features.unified_exec=false','memories.use_memories=false','project_doc_max_bytes=0',
                'model_auto_compact_token_limit=2147483647'):
                command.extend(['-c',setting])
            command.append('-')
            try:
                code,out,err=self.client._command(command,tmp,text.encode(),1200)
            except Exception as exc:
                raise TransportFailure('구독 응답 확인 실패: '+type(exc).__name__) from exc
        (folder/(name+'.events.log')).write_text(out,encoding='utf-8')
        (folder/(name+'.error.log')).write_text(err,encoding='utf-8')
        events=[]
        for line in out.splitlines():
            try:
                event=json.loads(line)
                if isinstance(event,dict): events.append(event)
            except ValueError:
                pass
        completed=[e for e in events if e.get('type')=='turn.completed']
        if (code or not completed or not output.exists()
                or any(e.get('type')=='turn.failed' or 'compact' in str(e.get('type','')).lower() for e in events)):
            raise TransportFailure('AI 응답 완료 확인 실패. 요청 기록 보존, 자동 재요청 없음')
        response=read(output)
        if not isinstance(response,dict) or set(response)!=set(SCHEMA['properties']):
            raise ValueError('응답 형식 확인 필요')
        write(answer_path,{'request_id':identity,'response':response,'completed_at':stamp(),
                           'usage':completed[-1].get('usage',{}),'backend':'codex_chatgpt_subscription'})
        return response

