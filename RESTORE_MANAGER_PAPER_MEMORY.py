"""Rebuild missing paper memories from authenticated saved receipts. No AI calls."""
import argparse
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ENGINES = ('official', 'robot_proto', 'v2', 'v3')

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def atomic(path, raw):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.memory-restore-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

def reconstruct(release, apply=False):
    source = release/'windows'
    verification_path = release/'MEMORY_RESTORE_VERIFIED.json'
    previous_raw = verification_path.read_bytes()
    previous = json.loads(previous_raw)
    destination = release/'derived_manager_memory'
    if destination.exists():
        for engine in ENGINES:
            path = destination/(engine+'.json')
            if not path.is_file() or sha(path.read_bytes()) != previous['analysts'][engine]['export_sha256']:
                raise ValueError('기존 기억 폴더가 불완전하거나 검증되지 않았습니다. 덮어쓰지 않음')
        print(json.dumps({'status':'already_verified','AI_requests':0,'files_changed':False},ensure_ascii=False))
        return
    manifest = read(release/'MANIFEST.json')
    metadata = {item['path']:item for item in manifest['files']}
    material_paths = [source/'outputs/TEN_MATCH_PRACTICE_SET.json'] + sorted((source/'outputs').glob('TEN_MATCH_PRACTICE_SET_[0-9][0-9].json'))
    receipts = list((source/'outputs/ten-match-practice').rglob('*.json'))
    code_dirs = [source/'work/pro-subscription',source/'work/github-reference-3827bfe']
    code_files = [p for folder in code_dirs for p in folder.glob('*.py')]
    if not receipts or not code_files: raise ValueError('원본 기록 또는 원본 코드 없음')
    for p in material_paths + receipts + code_files:
        if p.is_symlink():raise ValueError('원본 파일이 링크입니다')
        relative = p.relative_to(release).as_posix()
        if relative not in metadata or sha(p.read_bytes()) != metadata[relative]['sha256']:
            raise ValueError('원본 매니페스트 해시 불일치: '+relative)
    sys.path[:0] = [str(p) for p in code_dirs]
    remembered = importlib.import_module('analyst_remembered_exam')
    learning = importlib.import_module('learning_state')
    materials = [read(p) for p in material_paths]
    if len(materials) != 12 or any(learning.digest(m['cases']) != m['set_id'] for m in materials):
        raise ValueError('12묶음 원자료 검증 실패')
    cases = [c['case_id'] for m in materials for c in m['cases']]
    if len(cases) != 120 or len(set(cases)) != 120:raise ValueError('120경기 중복·누락 확인 필요')
    base = source/'outputs/ten-match-practice'
    payload = {}; summaries = {}
    for engine in ENGINES:
        success = remembered.successful_memory(engine, materials, base)
        errors = remembered.error_memory(engine, materials, base)
        rounds = list(base.glob('*/'+engine+'/round-*.json'))
        raw_wrong = 0
        for material in materials:
            for path in (base/material['set_id']/engine).glob('round-*.json'):
                receipt = read(path)
                if receipt['case_ids'] != [c['case_id'] for c in material['cases']]:raise ValueError('답안 경기 순서 불일치')
                raw_wrong += sum(not c['answers'][receipt['answers'][c['case_id']]['selected_id']] for c in material['cases'])
        counts = {'round_receipts':len(rounds), 'raw_wrong_answers':raw_wrong,
            'successful_memories':len(success),'wrong_case_memories':len(errors),
            'unique_wrong_attempts':sum(len(e['wrong_attempts']) for e in errors),
            'saved_review_explanations':sum(len(e['saved_review_explanations']) for e in errors)}
        expected = previous['analysts'][engine]
        for field,value in counts.items():
            if expected[field] != value:raise ValueError('기존 검증 기록과 복원 건수 불일치: '+engine+'/'+field)
        if any(m.get('analyst') != engine for m in success+errors):raise ValueError('분석가 기억 혼합')
        obj = {'analyst':engine,'successful_memory':success,'error_memory':errors,
            'bootstrap_records':0,
            'scope':'원본 AI 모의투자 답안·오답·복기 원문에서 복원. 폐기한 bootstrap 학습은 포함하지 않음',
            'historical_disclosure':'같은 경기 재시험 기록 포함. 실전 성과 증거가 아님'}
        raw = json.dumps(obj,ensure_ascii=False,indent=2).encode('utf-8')
        payload[engine] = raw
        summaries[engine] = {**counts,'bootstrap_records':0,'export_sha256':sha(raw),
            'source_manifest_hashes_verified':True}
    report = {'sets':12,'unique_cases':120,'analysts':summaries,'AI_requests':0,
        'status':'Saved AI paper records reconstructed; no new learning or predictions',
        'previous_verification_sha256':sha(previous_raw),'source_manifest_sha256':sha((release/'MANIFEST.json').read_bytes()),
        'web_import_verified':False}
    if not apply:
        print(json.dumps({'check':'OK','AI_requests':0,'files_changed':False,'analysts':summaries},ensure_ascii=False,indent=2));return
    backup = release/'memory_restore_backups'/('saved-records-'+str(time.time_ns()))
    backup.mkdir(parents=True)
    (backup/'MEMORY_RESTORE_VERIFIED.original.json').write_bytes(previous_raw)
    stage = Path(tempfile.mkdtemp(dir=release,prefix='.memory-stage-'))
    try:
        for engine,raw in payload.items():(stage/(engine+'.json')).write_bytes(raw)
        (stage/'RECONSTRUCTION_PROVENANCE.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if destination.exists() or verification_path.read_bytes()!=previous_raw:
            raise ValueError('복원 중 원본 상태 변경 감지. 적용하지 않음')
        os.rename(stage,destination)
        try:atomic(verification_path,json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
        except BaseException:
            os.rename(destination,backup/'uncommitted_reconstruction');raise
    finally:
        if stage.exists():
            # Only remove newly staged files; source receipts are never touched.
            for p in stage.iterdir():p.unlink()
            stage.rmdir()
    for engine in ENGINES:
        if sha((destination/(engine+'.json')).read_bytes())!=report['analysts'][engine]['export_sha256']:
            raise ValueError('복원 후 검증 실패')
    print(json.dumps({'status':'RESTORED','memory_folder':str(destination),'original_verification_backup':str(backup),
        'AI_requests':0,'analysts':{e:{'successes':v['successful_memories'],'errors':v['wrong_case_memories']} for e,v in summaries.items()}},ensure_ascii=False,indent=2))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('/home/ubuntu'))
    parser.add_argument('--release',type=Path);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    os.umask(0o077)
    state=args.root/'dj-manager-memory/runtime'
    active=read(state/'ACTIVATED.json')
    release=(args.release or Path(active.get('release') or args.root/'dj-manager-memory/releases/20261004_203930')).resolve()
    release.relative_to((args.root/'dj-manager-memory/releases').resolve())
    with (state/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('기존 작업 실행 중. 변경·중단·AI 요청 없음')
        reconstruct(release,args.apply)

if __name__=='__main__':main()
