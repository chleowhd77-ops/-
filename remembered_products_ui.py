"""Render only confirmed independent answers; no AI calls or server memory access."""
from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import time
from scorecard_core import epoch, summary

VERSION='remembered-products-v1'
LABELS={'official':'공식픽','robot':'자율로봇','v2':'V2','v3':'V3'}
MARKS={'home':'승','draw':'무','away':'패'}


def verified(row):
    return (row.get('model_version')==VERSION and
            0<epoch(row.get('frozen_at'))<=epoch(row.get('published_at'))<row['identity']['kickoff'])


def matches(item, row, product):
    m=item.get('match') or {}; identity=row['identity']
    ko=epoch(item.get('timestamp')) or epoch(m.get('match_time')) or epoch(item.get('final_match_time'))
    return (row['product']==product and str(m.get('id'))==identity['display_id'] and
            (m.get('home'),m.get('away'),ko)==(identity['home'],identity['away'],identity['kickoff']))


def overlay(dashboard, payload, now=None):
    if payload.get('schema_version')!=VERSION: return dashboard
    now=time.time() if now is None else now
    data=deepcopy(dashboard)
    rows=[r for r in payload.get('rows',[]) if verified(r)]
    for product in ('proto','toto14'):
        for item in data.get(product,[]):
            own={r['engine']:r for r in rows if matches(item,r,product)}
            ko=epoch(item.get('timestamp')) or epoch((item.get('match') or {}).get('match_time')) or epoch(item.get('final_match_time'))
            if not own and ko<=now: continue  # Started legacy answers stay as issued.
            item['_remembered_active']=True; item['_remembered_answers']=own
            if product=='toto14':
                item['analyst_toto14_marks']={e:{'marks':[MARKS[o['selection_side']] for o in own[e]['options']] if e in own else [],
                    'available':e in own} for e in LABELS}
            # No contradictory legacy recommendation is presented as new analyst reasoning.
            item['detailed_report']='\n\n'.join(LABELS[e]+': '+r['reason'] for e,r in own.items()) or '네 분석가의 전체 자료 분석을 기다리고 있습니다.'
    data['_remembered_active']=True
    return data


def boxes(item, engine=None):
    own=item.get('_remembered_answers') or {}; result=[]
    for e in ([engine] if engine else LABELS):
        row=own.get(e)
        pick=' / '.join(o['raw_pick'] for o in row['options']) if row else '분석 대기'
        p=f"{row['probability']*100:.1f}%" if row else '—'
        reason=row['reason'] if row else '경기 전체 자료를 검토한 답안이 아직 게시되지 않았습니다.'
        result.append("<div class='pred-box'><div class='pred-label'>"+escape(LABELS[e])+"</div>"
            +"<span class='pred-value'>"+escape(pick)+"</span><span class='pred-prob'>"+p+"</span>"
            +"<div style='font-size:12px;color:#94a3b8;margin-top:8px'>분석가의 적중확률 추정</div>"
            +"<details style='margin-top:8px'><summary>선택 이유</summary><p>"+escape(reason)+"</p></details></div>")
    return ''.join(result)


def top3_cards(dashboard, payload, engine, now=None):
    now=time.time() if now is None else now
    # The published list is the same list that is recorded for TOP3 grading.
    ranks=payload.get('top3',{}).get(engine,[])
    by_id={r['case_id']:r for r in payload.get('rows',[]) if r['engine']==engine and verified(r)}
    cards=[]
    for cid in ranks:
        row=by_id.get(cid)
        if not row or row['identity']['kickoff']<=now: continue
        item=next((c for c in dashboard.get('proto',[]) if matches(c,row,'proto')),None)
        if item is not None: cards.append(item)
    return cards


def render_top3(st, dashboard, payload):
    from scorecard_ui import select_buttons
    engine=select_buttons('remembered-top3-analyst',LABELS,'official')
    st.subheader('오늘의 TOP3')
    st.caption('선택한 분석가의 프로토라이브 픽 중 추정 적중확률이 높은 3경기입니다.')
    cards=top3_cards(dashboard,payload,engine)
    if not cards: st.info('시작 전 TOP3 게시를 기다리고 있습니다.')
    for i,item in enumerate(cards,1):
        m=item['match']
        when=escape(str(item.get('final_match_time') or m.get('match_time') or ''))
        st.markdown("<div class='match-card top3-glow'>"
            +f"<div class='league-title' style='color:#00f2fe'>TOP {i} · {escape(LABELS[engine])}</div>"
            +f"<div style='font-size:24px;font-weight:800;margin:18px 0'>{escape(m['home'])}"
            +f" <span style='font-size:14px;color:#94a3b8'>VS</span> {escape(m['away'])}</div>"
            +f"<div style='color:#94a3b8;margin-bottom:18px'>{when} · 한국 시간</div>"
            +"<div class='pred-grid'>"+boxes(item,engine)+"</div></div>",unsafe_allow_html=True)
    if 0<len(cards)<3: st.caption('다음 서버 갱신에서 남은 시작 전 경기의 순위가 반영됩니다.')


def overlay_scorecard(data, payload):
    if payload.get('schema_version')!=VERSION: return data
    data=deepcopy(data); history=payload.get('top3_history') or {}
    tracks=data.setdefault('tracks',{}); active=data.setdefault('active_models',{})
    for track in ('proto_world','top3','toto14'):
        for engine in LABELS:
            rows=[]
            for r in payload.get('rows',[]):
                if r['engine']!=engine or not verified(r): continue
                if (track=='toto14')!=(r['product']=='toto14'): continue
                private_engine='robot_proto' if engine=='robot' else engine
                if track=='top3':
                    receipt=history.get(private_engine+':'+r['case_id'],{})
                    if not 0<epoch(receipt.get('confirmed_at'))<r['identity']['kickoff']: continue
                ident=r['identity']
                rows.append({'match_id':ident['match_id'],'api_fixture_id':ident['fixture_id'],
                    'home_team':ident['home'],'away_team':ident['away'],
                    'kickoff_at':datetime.fromtimestamp(ident['kickoff'],timezone.utc).isoformat(),
                    'raw_pick':' / '.join(o['raw_pick'] for o in r['options']),
                    'model_version':VERSION,'is_correct':r.get('is_correct'),
                    'actual_score':r.get('actual_score',''),'settlement':r.get('settlement',''),
                    'track':track,'engine':engine,'review':r.get('review',''),
                    'grading_wait_reason':'환급 · 적중률 집계 제외' if r.get('settlement')=='REFUND' else ''})
            cell=tracks.setdefault(track,{}).setdefault(engine,{'rows':[]})
            keys={(r['match_id'],r['home_team'],r['away_team'],int(epoch(r['kickoff_at']))) for r in rows}
            old=cell.get('rows') or []
            superseded=[r for r in old if (str(r.get('match_id')),r.get('home_team'),r.get('away_team'),int(epoch(r.get('kickoff_at')))) in keys]
            retained=[r for r in old if r not in superseded and r.get('model_version')!=VERSION]
            cell['superseded_rows']=superseded
            cell['rows']=sorted(retained+rows,key=lambda r:epoch(r.get('kickoff_at')),reverse=True)
            cell['summary']=summary([r for r in cell['rows'] if r.get('settlement')!='REFUND'])
            active.setdefault(track,{})[engine]=VERSION
    return data
