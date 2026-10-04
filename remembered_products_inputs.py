"""Read-only, timestamped inputs for public products. No legacy model answers."""
import json
import math
import sqlite3
from contextlib import closing
import time
from pathlib import Path
from datetime import datetime, timezone
from manager_memory_inputs import clean, digest, epoch, read
from manager_evidence_recovery import recover_evidence, SECTIONS


def market_options(rows):
    result = {}
    for row in rows:
        market, side = row.get('market_key'), row.get('selection_side')
        if market not in ('1x2', 'handicap', 'totals'):
            continue
        if side not in (('under', 'over') if market == 'totals' else ('home', 'draw', 'away')):
            continue
        try:
            odd = float(row['odd'])
            line = float(row['handicap_base' if market == 'handicap' else 'totals_base']) if market != '1x2' else None
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(odd) or odd <= 1 or not row.get('raw_pick'):
            continue
        # Betman three-way integer handicaps; totals support whole/half lines.
        if line is not None and (not math.isfinite(line) or
                (market == 'handicap' and not line.is_integer()) or
                (market == 'totals' and (line < 0 or not (line * 2).is_integer()))):
            continue
        option = {'market_key': market, 'selection_side': side,
                  'line': line, 'odd': odd, 'raw_pick': row['raw_pick']}
        oid = digest([market, side, line])
        result[oid] = dict(option, option_id=oid)
    return list(result.values())


def load_inputs(root, now=None):
    root = Path(root); now = time.time() if now is None else now
    dashboard = read(root/'dashboard_data.json')
    result = {'captured_at': now, 'proto': [], 'toto14': [], 'excluded': []}
    with closing(sqlite3.connect((root/'ai_predictions.db').resolve().as_uri()+'?mode=ro', uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        for product in ('proto', 'toto14'):
            seen = set()
            for card in dashboard.get(product, []):
                m = card.get('match') or {}
                display_id = str(m.get('id') or '')
                mid = ('TOTO14_' + display_id if not display_id.startswith('TOTO14_') else display_id) if product == 'toto14' else display_id
                ko = epoch(card.get('timestamp')) or epoch(m.get('match_time')) or epoch(card.get('final_match_time'))
                if not display_id or mid in seen or ko <= now:
                    continue
                seen.add(mid)
                def exclude(reason):
                    result['excluded'].append({'product': product, 'match_id': mid,
                        'home': m.get('home'), 'away': m.get('away'), 'reason': reason})
                row = db.execute('SELECT * FROM predictions WHERE match_id=? ORDER BY id DESC LIMIT 1', (mid,)).fetchone()
                if row is None or row['actual_result'] not in (None, '', 'PENDING') or bool(row['is_toto14']) != (product == 'toto14'):
                    exclude('시작 전 경기 DB 연결 대기'); continue
                if (row['home_team'], row['away_team'], epoch(row['match_time'])) != (m.get('home'), m.get('away'), ko):
                    exclude('팀·경기 시각 연결 불일치'); continue
                sample = db.execute('''SELECT * FROM robot_learning_samples WHERE match_id=?
                    AND source=? AND api_fixture_id=? AND kickoff_timestamp=?
                    AND captured_timestamp>0 AND captured_timestamp<=? AND captured_timestamp<kickoff_timestamp
                    ORDER BY captured_timestamp DESC,id DESC LIMIT 1''',
                    (mid, 'PROTO' if product == 'proto' else 'TOTO14', row['api_fixture_id'], ko, now)).fetchone()
                if sample is None or (sample['home_team'],sample['away_team']) != (m.get('home'),m.get('away')):
                    exclude('경기 원자료 연결 대기'); continue
                try:
                    evidence = json.loads(sample['full_evidence_json'])
                    if not isinstance(evidence, dict): raise ValueError()
                except (ValueError, TypeError):
                    exclude('원자료 형식 확인 필요'); continue
                evidence, recovery = recover_evidence(db, sample['fixture_key'], sample['robot_pick_version'],
                    ko, min(now, sample['captured_timestamp']), evidence)
                source = {'sample_id': sample['id'], 'evidence_captured_at': sample['captured_timestamp'],
                          'recovery': recovery, 'missing_sections': [s for s in SECTIONS if not evidence.get(s)]}
                if product == 'proto':
                    snapshots = db.execute('''SELECT * FROM prediction_analysis_snapshots WHERE match_id=?
                        AND (stage LIKE 'T-%' OR stage='regular') ORDER BY id DESC''', (mid,))
                    offered = []
                    for snap in snapshots:
                        try:
                            dt = datetime.fromisoformat(snap['created_at'].replace('Z', '+00:00'))
                            captured = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
                            if not 0 < captured <= now < ko: continue
                            if str(snap['odds_source']).lower() != 'betman': continue
                            offered = market_options(json.loads(snap['candidates_json']))
                        except (ValueError, TypeError, KeyError):
                            continue
                        if offered:
                            source.update(snapshot_id=snap['id'], odds_captured_at=captured, odds_source='betman')
                            break
                    if not offered:
                        exclude('실제 베트맨 선택지 연결 대기'); continue
                else:
                    offered = [{'option_id': side, 'market_key':'1x2', 'selection_side':side,
                                'line':None, 'odd':None, 'raw_pick':label}
                               for side,label in (('home','승'),('draw','무'),('away','패'))]
                identity = {'match_id':mid,'display_id':display_id,'fixture_id':row['api_fixture_id'],
                    'home':m['home'],'away':m['away'],'kickoff':ko}
                result[product].append({'case_id':digest([product, identity]), 'product':product,
                    'identity':identity,'evidence':clean(evidence),'source':source,'options':offered,
                    'round_id':str(m.get('round_id') or ''),'number':m.get('num')})
    # A round-wide allocation must see the complete actual ticket, never a subset.
    groups = {}
    for q in result['toto14']: groups.setdefault(q['round_id'], []).append(q)
    valid = []
    for rid, group in groups.items():
        numbers = {str(q['number']) for q in group}
        if rid and len(group) == 14 and numbers == {str(n) for n in range(1,15)}:
            valid.extend(sorted(group, key=lambda q:int(q['number'])))
        else:
            result['excluded'].append({'product':'toto14','round_id':rid,
                                      'reason':f'14경기 전체 연결 대기 ({len(group)}/14)'})
    result['toto14'] = valid
    return result
