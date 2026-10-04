"""Rebatch only definitely unsent questions; preserve completed request identities."""
from pathlib import Path
import json
from manager_memory_inputs import digest, read
from remembered_products_contract import INSTRUCTION, SCHEMA, VERSION, validate
from remembered_products_transport import split_packets, write
from remembered_products_packing import serial, wire_text


def repack_plan(folder,plan):
    folder=Path(folder)
    if plan.get('packing_version')=='shared-json-v1':return plan
    names=plan.get('packet_names') or [f'analysis-{i:03d}' for i in range(len(plan['packets']))]
    kept=[];kept_names=[];remaining=[];base=None;old_chars=0
    for name,packet in zip(names,plan['packets']):
        answer=folder/(name+'.answer.json');pending=folder/(name+'.pending.json')
        if answer.exists():
            saved=read(answer)
            if saved.get('request_id')!=digest([VERSION,INSTRUCTION,SCHEMA,packet]):
                raise ValueError('완료 답안 원본 연결 확인 필요')
            validate(saved['response'],packet['questions'])
            kept.append(packet);kept_names.append(name)
        elif pending.exists():
            raise ValueError('응답 확인 중인 요청 보존: '+name+' · 재요청하지 않았습니다')
        else:
            template={k:v for k,v in packet.items() if k!='questions'}
            if base is not None and template!=base:raise ValueError('서로 다른 기억 입력을 합치지 않습니다')
            base=template;remaining.extend(packet['questions'])
            old_chars+=len(INSTRUCTION+serial(packet))
    packets=split_packets(base or {},'questions',remaining)
    report={'completed_requests_reused':len(kept),'old_unsent_batches':len(names)-len(kept),
            'new_unsent_batches':len(packets),'old_unsent_characters':old_chars,
            'new_unsent_characters':sum(len(wire_text(INSTRUCTION,p)) for p in packets),
            'questions_preserved':len(remaining),'AI_requests':0}
    return {**plan,'packets':kept+packets,
        'packet_names':kept_names+['compact-'+digest(p)[:24] for p in packets],
        'packing_version':'shared-json-v1','budget_report':report}


def save_repacked(folder,plan):
    folder=Path(folder);new=repack_plan(folder,plan)
    if new!=plan:
        backup=folder/'plan.before-budget.json'
        if not backup.exists():write(backup,plan)
        write(folder/'plan.json',new)
    return new
