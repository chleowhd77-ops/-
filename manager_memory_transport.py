"""Direct memory MCP transport. No model request, retries, or learning rules."""
import json
from pathlib import Path
import tempfile
import time

POLICY = 'direct-memory-mcp-v26'
SETTINGS = ('features.code_mode.enabled', 'features.code_mode')


def feature_state(output):
    rows = []
    for line in output.splitlines():
        fields = line.split()
        if fields and fields[0] in ('code_mode', 'code_mode.enabled'):
            if fields[-1].lower() in ('true', 'false'):
                rows.append(fields[-1].lower() == 'true')
    return rows[0] if len(rows) == 1 else None


def direct_setting(client):
    """Prove that this exact CLI accepts and applies an on/off override, locally."""
    saved = getattr(client, '_dj_direct_memory_setting', None)
    if saved in SETTINGS:
        return saved
    if not client.executable:
        raise ValueError('기억 연결 전 CLI 실행 확인 필요 · AI 요청 없음')
    with tempfile.TemporaryDirectory(prefix='dj-memory-feature-check-') as cwd:
        for key in SETTINGS:
            states = []
            for value in ('true', 'false'):
                argv = [client.executable, '-c', key+'='+value, 'features', 'list']
                code, out, err = client._command(argv, cwd, None, 15)
                states.append(feature_state(out) if code == 0 else None)
            if states == [True, False]:
                client._dj_direct_memory_setting = key
                print('[기억 직접 연결] '+key+'=false · CLI 설정 적용 확인 · 기억원문 검증 유지', flush=True)
                return key
    raise ValueError('CLI의 기억 직접 연결 설정 지원을 확인하지 못함 · AI 요청 전 중단')


def configure(argv, client):
    key = direct_setting(client)
    argv = list(argv)
    stdin_marker = bool(argv and argv[-1] == '-')
    if stdin_marker:
        argv.pop()
    # Only the internal tool routing changes. The same read-only MCP remains enabled.
    argv.extend(['-c', key+'=false'])
    if stdin_marker:
        argv.append('-')
    return argv


def check_memory(client, code, ref):
    """Check one own record through real local STDIO, not a paid AI call.

    This temporary audit is a connection self-test; never attach it to an analyst
    answer, never count it as the analyst using memory, and never touch saved audits.
    """
    from manager_memory_library import Reader
    if ref.get('retrieval_policy') != 'server-memory-library-v1':
        raise ValueError('실제 서버 기억 참조 확인 필요')
    started = time.monotonic()
    reader = Reader(ref)
    try:
        found = reader.search(limit=1)['records']
        if not found:
            raise ValueError('기억 연결 확인용 원본 기록 없음')
        record = reader.read(found[0]['record_id'])
    finally:
        reader.c.close()
    tool = Path(code)/'manager_memory_tools.py'
    with tempfile.TemporaryDirectory(prefix='dj-memory-stdio-check-') as td:
        folder = Path(td)
        reference = folder/'reference.json'
        audit = folder/'audit.jsonl'
        reference.write_text(json.dumps(ref, ensure_ascii=False), encoding='utf-8')
        messages = [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05'}},
            {'jsonrpc':'2.0','id':2,'method':'tools/list'},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'memory_catalog','arguments':{}}},
            {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'memory_read','arguments':{'record_id':record['record_id']}}},
        ]
        import sys
        payload = ('\n'.join(json.dumps(m) for m in messages)+'\n').encode()
        status, out, err = client._command([sys.executable, str(tool), '--reference', str(reference), '--audit', str(audit)], str(code), payload, 15)
        if status:
            raise ValueError('로컬 기억 도구 연결 점검 실패 · AI 요청 없음')
        replies = [json.loads(line) for line in out.splitlines() if line.strip()]
        by_id = {r.get('id'):r for r in replies}
        if set(by_id) != {1,2,3,4} or any('error' in r for r in replies):
            raise ValueError('로컬 기억 도구 응답 확인 실패')
        tools = by_id[2]['result']['tools']
        if {t['name'] for t in tools} != {'memory_catalog','memory_search','memory_read'}:
            raise ValueError('읽기 전용 기억 도구 목록 확인 실패')
        if any(not t.get('annotations',{}).get('readOnlyHint') for t in tools):
            raise ValueError('기억 도구 읽기 전용 속성 확인 실패')
        catalog = by_id[3]['result']
        actual = by_id[4]['result']
        if catalog.get('isError') or actual.get('isError'):
            raise ValueError('기억 도구 원문 조회 실패')
        if json.loads(catalog['content'][0]['text'])['counts'] != ref['counts']:
            raise ValueError('기억 도구 원본 건수 불일치')
        value = json.loads(actual['content'][0]['text'])
        if value != record or not value['text']:
            raise ValueError('기억 도구 원문 전달 불일치')
        from manager_memory_access import verify_reads
        if verify_reads(audit, ref) != [record['record_id']]:
            raise ValueError('기억 도구 임시 조회 증거 확인 실패')
    return {'analyst':ref['analyst'], 'records':sum(ref['counts'].values()),
            'read_chars':len(record['text']), 'elapsed_ms':round((time.monotonic()-started)*1000,3),
            'connection_self_test_only':True, 'AI_requests':0}
