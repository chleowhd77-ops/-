"""Analyst-owned product adapters; no invented answers or model substitution."""

LABELS = {'official':'Codex 공식픽', 'robot':'자율 로봇픽', 'v2':'V2 알파고', 'v3':'V3 학습픽'}


def investment_candidates(card, engine, v3_pick=None):
    """Keep each engine's probabilities; all price/identity data remain shared."""
    confidence = float(card.get('analysis_confidence') or 0)
    confidence = confidence / 100 if confidence > 1 else confidence
    if engine == 'official':
        source = card.get('display_candidates') or []
    elif engine == 'robot':
        source = card.get('robot_candidates') or []
    elif engine == 'v3':
        source = (v3_pick or {}).get('candidate_scores') or []
    else:
        from v2_ml_engine import get_v2_prediction
        from v2_market_learning import predict
        from pathlib import Path
        source = [p for p in card.get('display_candidates', []) if p.get('market_key') == '1x2']
        quotes = {p.get('selection_side'): p.get('odd') for p in source}
        prediction = get_v2_prediction(*(quotes.get(k) for k in ('home','draw','away')))
        probabilities = prediction.get('probabilities') or {}
        source = [{**p, 'model_probability': probabilities.get(p.get('selection_side')),
                   'model_version':prediction.get('model_version')}
                  for p in source if probabilities.get(p.get('selection_side')) is not None]
        source += predict(card.get('display_candidates') or [],Path(__file__).resolve().parent)
    result = []
    for candidate in source:
        row = dict(candidate)
        probability = (row.get('robot_probability') if engine == 'robot' else
                       row.get('v3_probability') if engine == 'v3' else
                       row.get('model_probability', row.get('prob')))
        if probability is None:
            continue
        row.update(model_probability=float(probability), data_confidence=confidence,
                   fair_probability=row.get('fair_probability', row.get('fair_prob')),
                   analyst=engine)
        result.append(row)
    return result


def attach_v2_markets(card, root):
    """Compute on the collector, never on a UI click. Preserve the WDL answer for Toto14."""
    from v2_market_learning import predict
    from learning_state import before_kickoff
    if not before_kickoff(card.get('match') or {}) or card.get('public_pick_block_reason'):
        return card
    candidates=predict(card.get('display_candidates') or [],root)
    old=card.get('alphago_pick') or {}
    probabilities=old.get('probabilities') or {}
    for c in card.get('display_candidates') or []:
        p=probabilities.get(c.get('selection_side'))
        if c.get('market_key')=='1x2' and p is not None:
            candidates.append({**c,'probability':p,'prob':p,'model_probability':p,
                'model_version':old.get('model_version'),'engine':'v2-ai','validation_status':'VALIDATING'})
    if candidates:
        card['v2_market_candidates']=candidates
        card['v2_market_pick']=dict(max(candidates,key=lambda c:c['probability']))
    return card


def manager_engine_payload(payload, engine):
    """UI and scoring consume the same engine-owned immutable ledger."""
    rows = {k:p for k,p in (payload.get('picks') or {}).items()
            if isinstance(p,dict) and p.get('engine_key') == engine}
    ready = payload.get('schema_version') == 'dj-sports.manager-investment-ledger.v2'
    data = (payload.get('engines') or {}).get(engine) or {}
    return {**payload, **data, 'picks':rows, 'frozen_pick_count':len(rows),
            'analyst_label':LABELS.get(engine,engine),
            'status': payload.get('status','NOT_READY') if ready else 'NOT_READY',
            'reason':data.get('reason') or ('분석가별 투자 장부 갱신 대기' if not ready else payload.get('reason',''))}


def toto_marks(item, engine):
    stored = ((item.get('analyst_toto14_marks') or {}).get(engine) or {}).get('marks') or []
    if stored:
        return [x for x in stored if x in ('승','무','패')]
    match = item.get('match') or {}
    if engine == 'v2':
        mark = {'H':'승','D':'무','A':'패'}.get((item.get('alphago_pick') or {}).get('code'))
        return [mark] if mark else []
    pick = item.get({'official':'official_comparison_pick','robot':'robot_pick','v3':'v3_learning_pick'}[engine]) or {}
    if pick.get('market_key') not in (None,'','1x2'):
        return []
    side = pick.get('selection_side')
    mark = {'home':'승','draw':'무','away':'패'}.get(side)
    raw = str(pick.get('raw_pick') or '')
    if not mark:
        if raw in ('무','무승부'):mark='무'
        elif match.get('away') and match['away'] in raw and '승' in raw:mark='패'
        elif match.get('home') and match['home'] in raw and '승' in raw:mark='승'
    return [mark] if mark else []


def toto_ticket_for_engine(items, engine):
    return [{**item, 'picks':toto_marks(item,engine)} for item in items]


def mark_grid(marks):
    colors={'승':'#00F2FE','무':'#10B981','패':'#EF4444'}
    cells=[]
    for mark,color in colors.items():
        selected=mark in marks
        style=f'background:{color if selected else "#111827"};color:{"#07111F" if selected else "#94A3B8"};border:1px solid {color if selected else "#334155"};'
        cells.append(f'<div role="cell" aria-label="{mark} {"선택" if selected else "미선택"}" style="{style}padding:16px 0;border-radius:8px;text-align:center;font-size:20px;font-weight:900;">{mark}{" ✓" if selected else ""}</div>')
    return '<div role="group" aria-label="승무패 선택" style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;width:100%;">'+''.join(cells)+'</div>'


def refresh_saved_v2(card, root, toto=False):
    """Recompute only V2 from saved real 1X2 quotes; never fetch or invent odds."""
    from learning_state import before_kickoff, campaign_ready, CAMPAIGN, now_iso
    from v2_ml_engine import get_v2_prediction
    match=card.get('match') or {}
    if not before_kickoff(match) or card.get('public_pick_block_reason'):
        return card
    quotes={p.get('selection_side'):p.get('odd') for p in
        (card.get('display_candidates') or card.get('robot_candidates') or [])
        if p.get('market_key')=='1x2'}
    if not quotes:
        quotes=(card.get('alphago_pick') or {}).get('odds') or {}
    if not quotes:
        quotes=dict(zip(('home','draw','away'),(match.get('odd_h'),match.get('odd_d'),match.get('odd_a'))))
    result=get_v2_prediction(*(quotes.get(k) for k in ('home','draw','away')))
    if result.get('status')!='ready':
        card['v2_refresh_wait_reason']=result.get('reason','저장 배당·모델 확인 필요')
        return card
    if not before_kickoff(match):
        return card
    card['alphago_pick']=result
    card.setdefault('learning_models',{})['v2']=result['model_version']
    if campaign_ready(root):card['learning_campaign']=CAMPAIGN
    card['v2_reanalysed_at']=now_iso()
    card.pop('v2_refresh_wait_reason',None)
    if toto:
        card.setdefault('analyst_toto14_marks',{})['v2']={
            'marks':[{'H':'승','D':'무','A':'패'}[result['code']]],'available':True}
    return card
