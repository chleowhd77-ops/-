"""Read-only manager transmission diagnosis. No AI, no file writes, no raw data output."""
import argparse,json,sys,time
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/home/ubuntu'));a=p.parse_args()
 state=a.root/'dj-manager-memory/runtime';active=json.loads((state/'ACTIVATED.json').read_text())
 code=Path(active['code']).resolve();code.relative_to((a.root/'dj-manager-memory/runtime-code').resolve());sys.path.insert(0,str(code))
 import manager_remembered_runtime as rt
 from manager_practice_bridge import exam_packet, originals
 from remembered_products_packing import serial,wire_text
 release=Path(active.get('release',a.root/'dj-manager-memory/releases/20261004_203930'))
 pending=[c for c in sorted((state/'cycles').glob('*/inputs.json')) if not all((c.parent/e/'completed.json').exists() for e in rt.ENGINES)]
 if not pending:raise SystemExit('미완료 주기 없음. AI 요청 0회')
 cycle=pending[0];raw=rt.read(cycle)['inputs']['pool'];report={'AI_requests':0,'files_changed':False,'cycle':cycle.parent.name,'limit_chars':rt.MAX_CHARS,'analysts':{}}
 for engine in rt.ENGINES:
  try:
   active_picks=[rt.read(f) for f in (state/'picks'/engine).glob('*.json') if rt.read(f)['identity']['kickoff']>time.time()]
   own={r['identity']['fixture_id'] for r in active_picks};pool=[q for q in raw if q['identity']['fixture_id'] not in own]
   if not pool:report['analysts'][engine]={'eligible_pool':0};continue
   memory=rt.append_live_memory(rt.load_memory(release,engine,pool),state,engine,pool)
   prepared=rt.prepare_packet({'mode':'candidates','analyst':engine,'memory':memory,'review_scope':'all_provided_questions','questions':pool[:1]})
   out,_,_=exam_packet(prepared);_,_,remembered=originals()
   instruction=remembered.MEMORY_INSTRUCTIONS.replace('This is one attempt on historical matches, not a prospective performance test.','This is one first prediction on actual upcoming matches; their outcomes are not available.')
   report['analysts'][engine]={'completed':(cycle.parent/engine/'completed.json').exists(),'eligible_pool':len(pool),'memory_counts':{k:len(v) for k,v in memory.items() if isinstance(v,list)},'single_match_wire_chars':len(wire_text(instruction,out)),'native_sections_chars':{k:len(serial(v)) for k,v in out.items()},'memory_only_wire_chars':len(wire_text(instruction,dict(out,questions=[])))}
  except Exception as e:report['analysts'][engine]={'error_type':type(e).__name__,'error':str(e)}
 print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
