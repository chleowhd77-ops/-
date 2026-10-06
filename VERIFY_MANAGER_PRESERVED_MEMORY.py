"""Read-only verification. No AI requests, state writes, or service changes."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('/home/ubuntu'))
    parser.add_argument('--release',type=Path);args=parser.parse_args()
    state=args.root/'dj-manager-memory/runtime'
    active=json.loads((state/'ACTIVATED.json').read_text())
    code=Path(active['code']).resolve()
    code.relative_to((args.root/'dj-manager-memory/runtime-code').resolve())
    release=args.release or Path(active.get('release') or args.root/'dj-manager-memory/releases/20261004_203930')
    sys.path.insert(0,str(code))
    inputs=importlib.import_module('manager_memory_inputs')
    rt=importlib.import_module('manager_remembered_runtime')
    retrieval=importlib.import_module('manager_memory_retrieval')
    if retrieval.POLICY!='preserved-paper-and-live-v2':raise SystemExit('새 기억 연결 코드가 활성 경로에 없습니다')
    pool=inputs.load_pool(args.root)['pool'];report={'AI_requests':0,'code':str(code),'release':str(release),'analysts':{}}
    for engine in inputs.ENGINES:
        memory=rt.append_live_memory(inputs.load_memory(release,engine,pool),state,engine,pool)
        packets=rt.split_packets({'mode':'candidates','analyst':engine,'memory':memory},'questions',pool)
        expected=retrieval.prepare_packet({'mode':'candidates','analyst':engine,'memory':memory,'questions':pool})['memory']
        for packet in packets:
            if packet['memory']!=expected:raise ValueError('전달 기억 불일치')
        report['analysts'][engine]={'paper_successes':len(expected['successful_memory']),
            'paper_errors':len(expected['error_memory']),'live_reviews':len(expected['live_pick_reviews']),
            'question_batches':len(packets),'memory_sha256':inputs.digest(expected)}
    report['pending_preserved']=True;report['paused']=(state/'PAUSED.json').exists()
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
