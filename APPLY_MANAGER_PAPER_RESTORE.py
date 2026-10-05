"""Install archived paper analysis code after current answers have been saved."""
import fcntl
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen
import zipfile

CONFIG = {'files': {'manager_remembered_runtime.py': '38f1c84475d4d9166a0840ac0dd0809813c08ace869da57775d3197e16ac6494', 'manager_memory_inputs.py': 'e16b640ab8470cba2a865c4691a9cd43da832992e5b82f92942a175dbd9d8046', 'manager_practice_bridge.py': '4af3f2ea5c202299f063a05237f81108e5577fbbf2b637c41de7fe158a9535c1', 'practice_original/analyst_codex_generator.py': 'e41c8d3fde67dd9e3a06c0ac33fe2c40baa73dc5f6c7f0ac4fd55a29c64779f4', 'practice_original/analyst_method_loop.py': '3e2bcf16cfbcd732f9073a00ecf8e0a2e8775a4c0b89e31457c77c2758b87b79', 'practice_original/analyst_remembered_exam.py': '14d51a9ec27f5a2e0d843e1d86f2b63a1332576695ac66296a00773bc8b55185', 'practice_original/analyst_ten_match_practice.py': '6673e8217891995fdd00e167d4753bf9ffe9a1e9278e33f7f58ea9e672e19d7c', 'practice_original/learning_state.py': '05b15b6226fc2d9e593318b1cefb9951b692542bec74ce61cf635932b287fb11', 'practice_original/manager_learning_goal.py': '81f729b65d3190cd00532d3f4ec0b4da5d581ea3b12420de6869062427bae345', 'practice_original/manifest.json': '911e44855a406911156ece53f66e59646bfc9e28a7563bf6b94103bb0f98b3f5', 'practice_original/scorecard_core.py': '7ca25664f45cd7080536d632d6d7f75ba85d082c60f3d1d5289486ba6b9e20f4', 'weather_observation.py': 'ccb0b70f53611e9413b0a2c93ffe4919776def760aaa679be86b4a16a338c7a3'}, 'bundle_sha256': '9d2f5071c79bc5ff162e96d670320109bfd6b330a0d69ea43429114e8aa3de83', 'before': {'manager_remembered_runtime.py': ['89a7c2f2c059e71150f3f641cfd3865e1a930209b855f1276179f59c4785959a', 'ab7614250eee62b9a963077db50788c4ec02a92b8a0404af4c469c3d4054bc7e'], 'manager_memory_inputs.py': ['870d8662e09282ae893327f0024958508e18488b06fe58873b6726ac48097032', '16625a77b6958860dd741d7610a8b34e966eb00d2de6cace99eb7db20ac85aac']}, 'original_modules': 7, 'AI_calls': 0, 'server_applied': False, 'installer_sha256': '3c5219fd1a3963e19dfb304a50d4fc4c1f81f3141f2b735dc826bc0399a939fc', 'weather_patch': {'old': {'fetch_weather_details_api': 'def fetch_weather_details_api(city_name, ttl_h):\n    default = {"available": False, "condition": "Unknown", "city": str(city_name or "")}\n    if not city_name:\n        return default\n    clean_city = city_name.split(\',\')[0].strip()\n    cache_key = f"weather_details_v2_{clean_city}"\n    cached_data = get_db_cache(cache_key, ttl_h)\n    if isinstance(cached_data, dict):\n        return cached_data\n    if _API_CACHE_ONLY:\n        return default\n    try:\n        res = requests.get(f"https://wttr.in/{clean_city}?format=j1", timeout=4)\n        data = res.json()\n        current = (data.get("current_condition") or [{}])[0]\n        raw_condition = str(\n            ((current.get("weatherDesc") or [{}])[0]).get("value") or ""\n        ).strip()\n        condition_text = raw_condition.casefold()\n        if any(word in condition_text for word in ("rain", "shower", "drizzle")):\n            condition = "Rain"\n        elif any(word in condition_text for word in ("snow", "blizzard")):\n            condition = "Snow"\n        else:\n            condition = "Clear"\n        result = {\n            "available": True,\n            "city": clean_city,\n            "condition": condition,\n            "description": raw_condition,\n            "temperature_c": current.get("temp_C"),\n            "feels_like_c": current.get("FeelsLikeC"),\n            "humidity_pct": current.get("humidity"),\n            "wind_speed_kmph": current.get("windspeedKmph"),\n            "wind_direction": current.get("winddir16Point"),\n            "precipitation_mm": current.get("precipMM"),\n            "cloud_cover_pct": current.get("cloudcover"),\n            "visibility_km": current.get("visibility"),\n            "pressure_hpa": current.get("pressure"),\n            "uv_index": current.get("uvIndex"),\n            "observation_time": current.get("localObsDateTime") or current.get("observation_time"),\n        }\n        set_db_cache(cache_key, result)\n        return result\n    except Exception:\n        return default', 'fetch_weather_api': 'def fetch_weather_api(city_name, ttl_h):\n    details = fetch_weather_details_api(city_name, ttl_h)\n    # Preserve the existing public-analysis fallback while the robot receives\n    # the explicit available=False flag and never learns a failed call as sun.\n    return str(details.get("condition") or "Clear") if details.get("available") else "Clear"'}, 'new': {'fetch_weather_details_api': 'def fetch_weather_details_api(city_name, ttl_h):\n    from weather_observation import normalize_observation\n    default = normalize_observation({"available": False, "condition": "Unknown", "city": str(city_name or "")})\n    if not city_name:\n        return default\n    clean_city = city_name.split(\',\')[0].strip()\n    cache_key = f"weather_details_v3_{clean_city}"\n    cached_data = get_db_cache(cache_key, ttl_h)\n    if isinstance(cached_data, dict):\n        return normalize_observation(cached_data)\n    if _API_CACHE_ONLY:\n        return default\n    try:\n        res = requests.get(f"https://wttr.in/{clean_city}?format=j1", timeout=4)\n        res.raise_for_status()\n        data = res.json()\n        if not isinstance(data, dict):\n            return default\n        current = (data.get("current_condition") or [{}])[0]\n        raw_condition = str(\n            ((current.get("weatherDesc") or [{}])[0]).get("value") or ""\n        ).strip()\n        result = {\n            "available": True,\n            "city": clean_city,\n            "condition": "Unknown",\n            "description": raw_condition,\n            "temperature_c": current.get("temp_C"),\n            "feels_like_c": current.get("FeelsLikeC"),\n            "humidity_pct": current.get("humidity"),\n            "wind_speed_kmph": current.get("windspeedKmph"),\n            "wind_direction": current.get("winddir16Point"),\n            "precipitation_mm": current.get("precipMM"),\n            "cloud_cover_pct": current.get("cloudcover"),\n            "visibility_km": current.get("visibility"),\n            "pressure_hpa": current.get("pressure"),\n            "uv_index": current.get("uvIndex"),\n            "observation_time": current.get("localObsDateTime") or current.get("observation_time"),\n        }\n        result["fetched_at"] = datetime.now(timezone.utc).isoformat()\n        result = normalize_observation(result)\n        if not result["available"]:\n            return result\n        set_db_cache(cache_key, result)\n        return result\n    except Exception:\n        return default', 'fetch_weather_api': 'def fetch_weather_api(city_name, ttl_h):\n    details = fetch_weather_details_api(city_name, ttl_h)\n    return str(details.get("condition") or "Unknown") if details.get("available") else "Unknown"'}}}
ROOT=Path('/home/ubuntu')
STATE=ROOT/'dj-manager-memory/runtime'
SERVICE='dj-remembered-manager.service'
TIMER='dj-remembered-manager.timer'
BUILD='paper-restore-'+CONFIG['bundle_sha256'][:12]

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(b):return hashlib.sha256(b).hexdigest()
def atomic(p,b):
    p.parent.mkdir(parents=True,exist_ok=True)
    fd,n=tempfile.mkstemp(dir=p.parent,prefix='.paper-restore-')
    try:
        with os.fdopen(fd,'wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
        os.replace(n,p)
    finally:
        if os.path.exists(n):os.unlink(n)

def patch_weather(raw):
    text=raw.decode('utf-8-sig');nodes={n.name:n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)}
    lines=text.splitlines(keepends=True)
    for name in sorted(CONFIG['weather_patch']['new'],key=lambda n:nodes[n].lineno,reverse=True):
        node=nodes[name];current=ast.get_source_segment(text,node).replace('\r\n','\n')
        if current not in (CONFIG['weather_patch']['old'][name],CONFIG['weather_patch']['new'][name]):
            raise SystemExit('서버 날씨 함수 버전 확인 필요: '+name+' / 변경 없음')
        lines[node.lineno-1:node.end_lineno]=[CONFIG['weather_patch']['new'][name]+'\n']
    result=''.join(lines).encode('utf-8');compile(result,'api_engine.py','exec');return result

def main():
    os.umask(0o077)
    code=Path(read(STATE/'ACTIVATED.json')['code']).resolve()
    code.relative_to(ROOT/'dj-manager-memory/runtime-code')
    unit=subprocess.check_output(['sudo','-n','systemctl','cat',SERVICE],text=True)
    if str(code/'start_manager.py') not in unit:raise SystemExit('관리자 실행 경로 불일치. 변경 없음')
    originals={}
    for n,h in CONFIG['files'].items():
        p=code/n;p.resolve().relative_to(code)
        if p.is_symlink():raise SystemExit('대상 파일이 링크입니다. 변경 없음')
        previous=p.read_bytes() if p.exists() else None;originals[n]=previous
        if n in CONFIG['before'] and (previous is None or sha(previous) not in CONFIG['before'][n]+[h]):
            raise SystemExit('서버 코드 버전 확인 필요: '+n+' / 변경 없음')
        if n not in CONFIG['before'] and previous is not None and sha(previous)!=h:
            raise SystemExit('동일 이름의 다른 코드가 있습니다: '+n+' / 변경 없음')
    api=ROOT/'api_engine.py';weather=ROOT/'weather_observation.py'
    for p in (api,weather):
        p.resolve().relative_to(ROOT)
        if p.is_symlink():raise SystemExit('날씨 코드 경로가 링크입니다. 변경 없음')
    external_before={api:api.read_bytes(),weather:weather.read_bytes() if weather.exists() else None}
    patched_api=patch_weather(external_before[api])
    if external_before[weather] is not None and sha(external_before[weather])!=CONFIG['files']['weather_observation.py']:
        raise SystemExit('같은 이름의 다른 날씨 코드가 있습니다. 변경 없음')
    if (all(v is not None and sha(v)==CONFIG['files'][n] for n,v in originals.items())
        and external_before[api]==patched_api and external_before[weather] is not None
        and not (STATE/'PAUSED.json').exists()):
        print('이미 적용된 코드입니다. AI 재호출·서비스 재시작 없음.');return
    pause=STATE/'PAUSED.json';audit=STATE/'deployments'/BUILD
    if pause.exists() and not (audit/'handoff.json').exists():
        raise SystemExit('기존 중단 사유가 있습니다. 자동 재개하지 않았습니다.')
    with urlopen('https://raw.githubusercontent.com/chleowhd77-ops/-/main/MANAGER_PAPER_RESTORE_BUNDLE.zip',timeout=45) as r:raw=r.read()
    if sha(raw)!=CONFIG['bundle_sha256']:raise SystemExit('업로드 묶음이 다릅니다. 변경 없음')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if len(z.namelist())!=len(CONFIG['files']) or set(z.namelist())!=set(CONFIG['files']):
            raise SystemExit('묶음 파일 목록 불일치')
        incoming={n:z.read(n) for n in CONFIG['files']}
    for n,b in incoming.items():
        if sha(b)!=CONFIG['files'][n]:raise SystemExit('파일 검증 실패: '+n)
        if n.endswith('.py'):compile(b.decode('utf-8-sig'),n,'exec')
    collector_active=subprocess.check_output(['systemctl','show','dj-collector.service','-p','ActiveState','--value'],text=True).strip()=='active'
    subprocess.run(['sudo','-n','systemctl','stop',TIMER],check=True)
    atomic(audit/'handoff.json',json.dumps({'source':BUILD,'at':time.time()}).encode())
    if not pause.exists():
        with pause.open('x',encoding='utf-8') as f:
            json.dump({'source':BUILD,'reason':'현재 답안 저장 후 모의투자 원본 연결','automatic_retry':False},f,ensure_ascii=False)
    print('진행 중인 답안을 저장한 뒤 연결합니다. 강제 종료·완료 답안 재호출 없음.',flush=True)
    deadline=time.monotonic()+1260;notice=0
    with (STATE/'worker.lock').open('a') as lock:
        while True:
            status=subprocess.check_output(['systemctl','show',SERVICE,'-p','ActiveState','--value'],text=True).strip()
            if status in ('inactive','failed'):
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:pass
            if time.monotonic()>deadline:raise SystemExit('답안 대기 시간 초과. 강제 종료하지 않았습니다.')
            if time.monotonic()>=notice:
                print('현재 답안 저장 대기 중…',flush=True);notice=time.monotonic()+30
            time.sleep(2)
        stopped=read(pause)
        if stopped.get('source')!=BUILD and stopped.get('reason')!='중단 상태. 직접 재개 필요':
            raise SystemExit('별도 오류 확인 필요: '+str(stopped.get('reason')))
        pending=[p for p in STATE.rglob('*.pending.json') if not
                 (p.with_name(p.name.replace('.pending.json','.answer.json')).exists() or
                  ('paper-original' in p.parts and p.with_name(p.name.replace('.pending.json','.json')).exists()))]
        if pending:raise SystemExit('완료 확인이 필요한 요청 '+str(len(pending))+'건. 자동 재호출 없음')
        if Path(read(STATE/'ACTIVATED.json')['code']).resolve()!=code:raise SystemExit('실행 경로 변경 감지')
        for n,b in originals.items():
            p=code/n
            if (p.read_bytes() if p.exists() else None)!=b:raise SystemExit('대기 중 코드 변경 감지: '+n)
            if b is not None:atomic(audit/'previous'/n,b)
        for p,b in external_before.items():
            if (p.read_bytes() if p.exists() else None)!=b:raise SystemExit('대기 중 날씨 코드 변경 감지')
            if b is not None:atomic(audit/'previous-collector'/p.name,b)
        written=[]
        try:
            atomic(weather,incoming['weather_observation.py']);atomic(api,patched_api)
            # Runtime entry is written last; all dependencies must exist first.
            for n in sorted(incoming,key=lambda n:n=='manager_remembered_runtime.py'):
                atomic(code/n,incoming[n]);written.append(n)
            for n,h in CONFIG['files'].items():
                if sha((code/n).read_bytes())!=h:raise ValueError('설치 검증 실패: '+n)
            if collector_active:
                subprocess.run(['sudo','-n','systemctl','restart','dj-collector.service'],check=True)
        except Exception:
            for n in reversed(written):
                if originals[n] is not None:atomic(code/n,originals[n])
                else:(code/n).unlink()
            for p,b in external_before.items():
                if b is not None:atomic(p,b)
                elif p.exists():p.unlink()
            raise
        atomic(audit/'installed.json',json.dumps({'source':BUILD,'files':CONFIG['files'],
            'original_modules':7,'AI_calls_during_install':0,'at':time.time()}).encode())
        pause.rename(audit/('pause-before-resume-'+str(time.time_ns())+'.json'))
    subprocess.run(['sudo','-n','systemctl','enable','--now',TIMER],check=True)
    subprocess.run(['sudo','-n','systemctl','start','--no-block',SERVICE],check=True)
    print('모의투자 원본 연결 완료. 관리자 자동 분석 재개. 실경기 첫 실행 로그 확인 필요.',flush=True)

if __name__=='__main__':main()
