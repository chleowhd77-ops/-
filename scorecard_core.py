"""Read-only score projection and append-only product publication receipts.

No model calls, network calls, or updates to historical picks/results belong here.
"""
import json
import math
import re
from copy import deepcopy
from collections import Counter
from learning_state import CAMPAIGN, digest, revision_allowed, before_kickoff
from datetime import datetime, timezone, timedelta

ENGINES = ('official', 'robot', 'v2', 'v3')
TRACKS = ('manager', 'top3', 'proto_world', 'toto14')
KST = timezone(timedelta(hours=9))
VERSION = 'R7.13.11'


def obj(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or '{}')
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else 0.0
    except (ValueError, TypeError):
        return 0.0


def epoch(value, naive_tz=timezone.utc):
    if isinstance(value, (int, float)):
        return number(value)
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or '')
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            m = re.search(r'(\d{2,4})[.\-/](\d{1,2})[.\-/](\d{1,2}).*?(\d{1,2}):(\d{2})', raw)
            if not m:
                return 0.0
            y, mo, d, h, mi = map(int, m.groups())
            try:
                parsed = datetime(y + 2000 if y < 100 else y, mo, d, h, mi)
                naive_tz = KST
            except ValueError:
                return 0.0
    return parsed.replace(tzinfo=naive_tz).timestamp() if parsed.tzinfo is None else parsed.timestamp()


def table_rows(conn, table, columns='*', order=''):
    # Call sites supply literal table/column names, never user input.
    if not conn.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?', (table,)).fetchone():
        return []
    cursor = conn.execute(f'SELECT {columns} FROM {table} {order}')
    names = [c[0] for c in cursor.description]
    return (dict(zip(names, row)) for row in cursor)


def source_track(source):
    return {'PROTO': 'proto_world', 'TOTO14': 'toto14', 'TOP3': 'top3',
            'MANAGER': 'manager'}.get(str(source or '').upper())


def from_prediction(p):
    mid = str(p.get('match_id') or '')
    return {'match_id': mid, 'home_team': p.get('home_team', ''),
            'away_team': p.get('away_team', ''), 'kickoff_at': p.get('match_time', ''),
            'api_fixture_id': p.get('api_fixture_id', 0),
            'track': 'toto14' if p.get('is_toto14') else 'proto_world'}


def summary(rows):
    done = [r for r in rows if r.get('is_correct') in (0, 1)]
    hits = sum(int(r['is_correct']) for r in done)
    return {'graded': len(done), 'correct': hits,
            'accuracy': hits / len(done) if done else None,
            'pending': len(rows) - len(done), 'stored': len(rows)}


def project(records, audit=None, manager_legacy=None):
    tracks = {t: {e: {'rows': [], 'summary': {}} for e in ENGINES} for t in TRACKS}
    for row in records:
        if row.get('track') in tracks and row.get('engine') in ENGINES:
            tracks[row['track']][row['engine']]['rows'].append(row)
    for cells in tracks.values():
        for cell in cells.values():
            cell['rows'].sort(key=lambda r: epoch(r.get('kickoff_at'), KST), reverse=True)
            cell['summary'] = summary(cell['rows'])
    return {'version': VERSION, 'tracks': tracks, 'audit': audit or {},
            'manager_legacy': manager_legacy or [],
            'generated_at': datetime.now(timezone.utc).isoformat()}


def build_scorecard(conn, evaluate, v3=None, manager=None):
    """Join each analyst independently; old versions are not silently discarded."""
    predictions = {str(r['match_id']): r for r in table_rows(conn, 'predictions',
        'match_id,home_team,away_team,match_time,is_toto14,api_fixture_id,actual_result,actual_score')}
    records, audit = {}, Counter()
    legacy_references = []
    manager_v2 = (manager or {}).get('schema_version') == 'dj-sports.manager-investment-ledger.v2'
    result_index = {}
    for p in predictions.values():
        if p.get('actual_result') == 'FINISHED' and re.fullmatch(r'\d+\s*:\s*\d+', str(p.get('actual_score') or '')):
            fid = int(p.get('api_fixture_id') or 0)
            if fid:
                result_index[(fid, p.get('home_team'), p.get('away_team'))] = p

    def add(meta, engine, pick, captured, provenance, grade=None, score='', trusted=False):
        track, mid = meta.get('track'), str(meta.get('match_id') or '')
        raw = str(pick.get('raw_pick') or pick.get('pick') or '').strip()
        if not track or engine not in ENGINES or not raw or not mid:
            audit['source_or_answer_missing'] += 1
            return
        ko = epoch(meta.get('kickoff_at'), KST)
        stamp = epoch(captured)
        if not trusted and (not ko or not stamp or stamp >= ko):
            audit['pre_kickoff_unverified'] += 1
            return
        key = (track, mid, engine)
        if key in records:
            return
        p = predictions.get(mid) or {}
        if (meta.get('home_team'), meta.get('away_team')) != (p.get('home_team'), p.get('away_team')):
            p = {}
        stored_fid = int(meta.get('api_fixture_id') or 0)
        if stored_fid and int(p.get('api_fixture_id') or 0) not in (0, stored_fid):
            p = {}
        if not p and stored_fid:
            p = result_index.get((stored_fid, meta.get('home_team'), meta.get('away_team')), {})
        if grade not in (0, 1) and p.get('actual_result') == 'FINISHED':
            score = str(p.get('actual_score') or '')
            match = re.fullmatch(r'(\d+)\s*:\s*(\d+)', score)
            if match:
                grade = int(evaluate(raw, meta.get('home_team'), meta.get('away_team'), *map(int, match.groups())))
        compact_meta = {k: meta.get(k) for k in ('track', 'match_id', 'home_team', 'away_team',
                        'kickoff_at', 'api_fixture_id', 'kind')}
        records[key] = {**compact_meta, 'engine': engine, 'raw_pick': raw,
                        'probability': number(pick.get('probability', pick.get('prob'))),
                        'odd': number(pick.get('odd')), 'is_correct': grade if grade in (0, 1) else None,
                        'actual_score': score, 'captured_at': captured, 'provenance': provenance,
                        'model_version':pick.get('model_version'),
                        'grading_wait_reason':('' if grade in (0,1) else
                            '경기 결과 미수집' if p and p.get('actual_result')!='FINISHED' else
                            '경기 연결 또는 정산 자료 확인 필요')}

    for row in table_rows(conn, 'product_pick_receipts', order='ORDER BY captured_at ASC'):
        data = obj(row['payload_json'])
        if manager_v2 and row['track'] == 'manager':
            legacy_references.append({**data, 'record_kind':'legacy_analyst_reference'})
            continue
        add(data, row['engine'], data, row['captured_at'], 'product_receipt')
    for row in table_rows(conn, 'toto14_prediction_freezes'):
        payload = obj(row.get('payload_json'))
        mid = str(row.get('match_id'))
        mid = mid if mid.startswith('TOTO14_') else 'TOTO14_' + mid
        meta = {**row, 'match_id': mid, 'track': 'toto14', 'kickoff_at': row.get('match_time'),
                'api_fixture_id': payload.get('api_fixture_id')}
        marks = payload.get('analyst_toto14_marks') or {}
        for engine in ENGINES:
            values = (marks.get(engine) or {}).get('marks') or (payload.get('picks') if engine == 'official' else [])
            raw = ', '.join(str(x) for x in values) if values else ''
            if not raw:
                raw = str((payload.get('robot_pick') or {}).get('raw_pick') or '') if engine == 'robot' else ''
            if not raw and engine == 'v2':
                answer = v2_answer(payload.get('alphago_pick') or payload.get('robot_pick'),
                                   meta.get('home_team'),meta.get('away_team'))
                if answer:
                    add(meta, engine, answer, row.get('frozen_at'), 'toto14_v2_saved_answer')
            if raw:
                add(meta, engine, {'raw_pick': raw}, row.get('frozen_at'), 'toto14_ticket')

    # Existing primary engine receipts take precedence over archive recovery.
    for row in table_rows(conn, 'three_engine_pick_snapshots', order='ORDER BY captured_timestamp DESC,id DESC'):
        if str(row.get('engine_version') or '').startswith('archive-'):
            continue  # Revalidate the original receipt instead of the old R7.13.6 migration.
        meta = {**row, 'track': source_track(row.get('source'))}
        if manager_v2 and meta['track'] == 'manager':
            legacy_references.append({**row, 'record_kind':'legacy_analyst_reference'})
            continue
        score = f"{row['actual_home_goals']}:{row['actual_away_goals']}" if row.get('actual_home_goals') is not None else ''
        add(meta, row.get('engine_key'), row, row.get('captured_at'), 'engine_snapshot', row.get('is_correct'), score)

    # Official publication snapshots have the exact published answer, not a
    # reconstructed best candidate. Avoid copying one analyst into another.
    for row in table_rows(conn, 'prediction_snapshots',
                          'id,match_id,prob_pick,prob_pick_prob,api_fixture_id,stage,created_at', 'ORDER BY id DESC'):
        p = predictions.get(str(row.get('match_id'))) or {}
        if not p or str(p.get('match_id')).startswith('WORLD_'):
            continue
        if row.get('api_fixture_id') and p.get('api_fixture_id') and row['api_fixture_id'] != p['api_fixture_id']:
            audit['fixture_mismatch'] += 1
            continue
        if 'preview' in str(row.get('stage') or '').lower():
            continue
        add(from_prediction(p), 'official', {'raw_pick': row.get('prob_pick'),
            'probability': number(row.get('prob_pick_prob')) / 100}, row.get('created_at'), 'published_snapshot')

    # Recover saved robot/V2 decisions independently even if official already exists.
    for row in table_rows(conn, 'prediction_analysis_snapshots',
                          'id,match_id,stage,decision_json,created_at', 'ORDER BY id DESC'):
        p = predictions.get(str(row.get('match_id'))) or {}
        if not p or str(p.get('match_id')).startswith('WORLD_') or 'preview' in str(row.get('stage')).lower():
            continue
        decision = obj(row.get('decision_json'))
        robot = obj(decision.get('robot_pick'))
        if robot.get('raw_pick'):
            add(from_prediction(p), 'robot', robot, row.get('created_at'), 'robot_decision')
        v2pick = v2_answer(decision.get('alphago_pick') or robot, p.get('home_team'), p.get('away_team'))
        if v2pick:
            add(from_prediction(p), 'v2', v2pick, row.get('created_at'), 'v2_decision')
    for row in table_rows(conn, 'robot_learning_samples', order='ORDER BY captured_timestamp DESC,id DESC'):
        meta = {**row, 'track': source_track(row.get('source'))}
        if manager_v2 and meta['track'] == 'manager':
            legacy_references.append({**row, 'record_kind':'legacy_analyst_reference'})
            continue
        pick = obj(row.get('robot_pick_json'))
        add(meta, 'robot', pick, row.get('captured_at'), 'robot_learning_sample')
        v2pick = v2_answer(pick, row.get('home_team'), row.get('away_team'))
        if v2pick:
            add(meta, 'v2', v2pick, row.get('captured_at'), 'v2_learning_sample')
    # Independent V3 file already owns its grades; do not hide those behind a
    # current-version filter or pretend they are TOP3/manager publication receipts.
    for mid, pick in ((v3 or {}).get('picks') or {}).items():
        if not isinstance(pick, dict):
            continue
        mid = str(pick.get('match_id') or mid)
        p = predictions.get(mid) or {}
        track = 'toto14' if mid.startswith('TOTO14_') or pick.get('source_kind') == 'toto14_freeze' else 'proto_world'
        if mid.startswith('WORLD_') or pick.get('source_kind') == 'world_dashboard_card':
            audit['v3_world_archive'] += 1
            continue
        meta = {**from_prediction(p), 'match_id': mid, 'track': track}
        meta.update({k: pick[k] for k in ('home_team', 'away_team', 'kickoff_at', 'api_fixture_id') if pick.get(k)})
        # Older ledgers used home/away. Resolve display identity only from a
        # unique fixture and matching kickoff, never from fuzzy team names.
        for field,alias in (('home_team','home'),('away_team','away')):
            if not meta.get(field) and pick.get(alias):meta[field]=pick[alias]
        if (not meta.get('home_team') or not meta.get('away_team')) and meta.get('api_fixture_id'):
            matches=[r for r in predictions.values()
                     if str(r.get('api_fixture_id'))==str(meta['api_fixture_id'])
                     and epoch(meta.get('kickoff_at'),KST)>0
                     and epoch(r.get('match_time'),KST)==epoch(meta.get('kickoff_at'),KST)]
            pairs={(r.get('home_team'),r.get('away_team')) for r in matches}
            if len(pairs)==1:
                home,away=next(iter(pairs))
                if (not meta.get('home_team') or meta['home_team']==home) and (not meta.get('away_team') or meta['away_team']==away):
                    meta.update(home_team=home,away_team=away)
        if not meta.get('kickoff_at') and pick.get('is_correct') not in (0, 1):
            audit['v3_identity_or_time_missing'] += 1
            continue
        # An already graded V3 receipt remains visible, even when an old DB copy
        # cannot resolve its kickoff. Mark provenance; never manufacture a grade.
        add(meta, 'v3', pick, pick.get('frozen_at'), 'v3_independent_ledger',
            pick.get('is_correct'), pick.get('actual_score', ''), trusted=pick.get('is_correct') in (0, 1))

    legacy = list(legacy_references)
    for pick in ((manager or {}).get('picks') or {}).values():
        if not isinstance(pick, dict):
            continue
        engine = pick.get('engine_key') or pick.get('analyst')
        if engine not in ENGINES:
            saved = dict(pick)
            p = predictions.get(str(pick.get('match_id'))) or {}
            score = re.fullmatch(r'(\d+)\s*:\s*(\d+)', str(p.get('actual_score') or ''))
            if (pick.get('is_correct') not in (0, 1) and p.get('actual_result') == 'FINISHED' and score
                and (pick.get('home'), pick.get('away')) == (p.get('home_team'), p.get('away_team'))
                and 0 < epoch(pick.get('frozen_at')) < epoch(pick.get('kickoff_at'), KST)):
                saved['is_correct'] = int(evaluate(pick.get('raw_pick'), pick.get('home'), pick.get('away'), *map(int, score.groups())))
                saved['actual_score'] = p['actual_score']
            legacy.append(saved)  # Separate investment engine is not Codex official.
            continue
        meta = {'track': 'manager', 'match_id': str(pick.get('match_id') or ''),
                'home_team': pick.get('home'), 'away_team': pick.get('away'),
                'kickoff_at': pick.get('kickoff_at'), 'api_fixture_id': pick.get('api_fixture_id')}
        add(meta, engine, pick, pick.get('frozen_at'), 'manager_ledger', pick.get('is_correct'), pick.get('actual_score', ''))
    audit['prediction_matches_without_verified_answer'] = sum(
        not any((t, mid, e) in records for e in ENGINES)
        for mid, p in predictions.items() if not mid.startswith('WORLD_')
        for t in ['toto14' if p.get('is_toto14') else 'proto_world'])
    audit['stored_answers'] = len(records)
    return project(list(records.values()), dict(audit), legacy)


def v2_answer(pick, home, away):
    pick = obj(pick)
    if pick.get('engine') in ('v2-ai','v2') and pick.get('raw_pick') and pick.get('market_key') in ('1x2','totals','handicap'):
        return dict(pick)
    code = str(pick.get('code') or pick.get('v2_ai_pick') or '').upper()
    raw = {'H': f'{home} 승', 'D': '무승부', 'A': f'{away} 승'}.get(code)
    return {'raw_pick': raw, 'code': code, 'selection_side': {'H': 'home', 'D': 'draw', 'A': 'away'}.get(code),
            'market_key': '1x2', 'probability': pick.get('probability'),
            'model_version':pick.get('model_version')} if raw else {}


def published_scorecard(snapshot, v3, manager):
    """Read older publications honestly during an app/collector deployment gap."""
    if (snapshot.get('scorecard_v2') or {}).get('version') == VERSION:
        return snapshot['scorecard_v2']
    records, seen = [], set()
    identities = {}
    for track, data in ((snapshot.get('three_engine') or {}).get('tracks') or {}).items():
        if track not in TRACKS:
            continue
        for row in (data.get('finished') or []) + (data.get('pending') or []):
            identities[(track,str(row.get('match_id')))] = row
            for engine, pick in (row.get('engines') or {}).items():
                key = (track, str(row.get('match_id')), engine)
                if key in seen or engine not in ENGINES:
                    continue
                seen.add(key)
                records.append({**row, **pick, 'track': track, 'engine': engine, 'provenance': 'published_engine_feed'})
    for mid, pick in (v3 or {}).items():
        if not isinstance(pick, dict) or str(mid).startswith('WORLD_') or pick.get('source_kind') == 'world_dashboard_card':
            continue
        track = 'toto14' if str(mid).startswith('TOTO14_') or pick.get('source_kind') == 'toto14_freeze' else 'proto_world'
        identity = identities.get((track,str(mid))) or {}
        records.append({**pick,
            'home_team':pick.get('home_team') or pick.get('home') or identity.get('home_team') or identity.get('home'),
            'away_team':pick.get('away_team') or pick.get('away') or identity.get('away_team') or identity.get('away'),
            'kickoff_at':pick.get('kickoff_at') or identity.get('kickoff_at') or identity.get('match_time'),
            'track': track, 'engine': 'v3', 'match_id': str(mid), 'provenance': 'v3_independent_ledger'})
    data = project(records, {'collector_update_pending': True},
                   [p for p in (manager.get('picks') or {}).values() if isinstance(p, dict)])
    data['generated_at'] = snapshot.get('generated_at')
    return data


def freeze_products(conn, dashboard, v3, official_selector, robot_selector, now=None):
    """Freeze exactly the current TOP3 and administrator lists before kickoff.

    Receipt insertion is idempotent; subsequent cycles serve the same answer.
    V2/V3 manager rows remain clearly labelled comparison answers, not value picks.
    """
    simulated_now = now
    now = now or datetime.now(timezone.utc)
    conn.execute('''CREATE TABLE IF NOT EXISTS product_pick_receipts (
        track TEXT NOT NULL, match_id TEXT NOT NULL, engine TEXT NOT NULL,
        captured_at TEXT NOT NULL, payload_json TEXT NOT NULL,
        PRIMARY KEY(track,match_id,engine))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS product_pick_revision_history (
        fingerprint TEXT PRIMARY KEY, track TEXT,match_id TEXT,engine TEXT,
        captured_at TEXT,payload_json TEXT,archived_at TEXT,reason TEXT)''')
    proto = []
    for card in dashboard.get('proto', []):
        match = card.get('match') or {}
        ko = epoch(card.get('timestamp')) or epoch(match.get('match_time'), KST)
        stage = str(card.get('analysis_stage') or '').lower()
        if ko <= now.timestamp() or card.get('public_pick_block_reason') or any(x in stage for x in ('pending', 'preview', 'deferred')):
            continue
        if not stage:
            continue
        proto.append(card)
    by_id = {str((c.get('match') or {}).get('id')): c for c in proto}
    by_fixture = {str(c.get('api_fixture_id') or (c.get('match') or {}).get('id')): c for c in proto}
    def save(track, card, engine, pick, kind='pick'):
        match = card.get('match') or {}
        raw = str(pick.get('raw_pick') or pick.get('pick') or '').strip()
        if not raw or pick.get('display_only') or pick.get('recommendation_status') == 'WITHHELD':
            return
        payload = {**pick, 'track': track, 'engine': engine, 'raw_pick': raw,
                   'match_id': str(match.get('id')), 'home_team': match.get('home'),
                   'away_team': match.get('away'), 'api_fixture_id': card.get('api_fixture_id', 0),
                   'kickoff_at': match.get('match_time') or datetime.fromtimestamp(epoch(card.get('timestamp')), timezone.utc).isoformat(),
                   'kind': kind}
        payload.update(learning_campaign=card.get('learning_campaign',''),
                       model_version=pick.get('model_version') or (card.get('learning_models') or {}).get(engine,'legacy-unverified'))
        # V3 must have been regenerated as well; do not mark its old answer refreshed.
        if engine == 'v3':
            payload['learning_campaign'] = pick.get('learning_campaign','')
        # Use the injected clock for replay tests, the real clock for publishing.
        checked_at = simulated_now or datetime.now(timezone.utc)
        if epoch(payload['kickoff_at'],KST) <= checked_at.timestamp():
            return
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='predictions'").fetchone():
            saved = conn.execute('SELECT actual_result,match_time,home_team,away_team FROM predictions WHERE match_id=?',(payload['match_id'],)).fetchone()
            if saved and (saved[0]!='PENDING' or epoch(saved[1],KST)<=checked_at.timestamp()
                          or (saved[2],saved[3])!=(match.get('home'),match.get('away'))):
                return
        old_row = conn.execute('SELECT captured_at,payload_json FROM product_pick_receipts WHERE track=? AND match_id=? AND engine=?',
                               (track,payload['match_id'],engine)).fetchone()
        old_payload = obj(old_row[1]) if old_row else {}
        # PROTO retains its pre-existing stage-refresh policy. Its grading
        # receipt must follow the exact displayed pre-kickoff answer. TOP3
        # remains a separately frozen selection after the one-time migration.
        proto_revision = (track=='proto_world' and old_row
            and old_payload.get('learning_campaign')==payload.get('learning_campaign')==CAMPAIGN
            and any(old_payload.get(k)!=payload.get(k) for k in ('raw_pick','model_version','market_key','selection_side'))
            and before_kickoff(match,now))
        if old_row and (revision_allowed(old_payload,payload,match,now) or proto_revision):
            old_payload = obj(old_row[1])
            old_ko = epoch(old_payload.get('kickoff_at'),KST)
            if old_ko <= datetime.now(timezone.utc).timestamp():
                return
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='predictions'").fetchone():
                saved = conn.execute('SELECT actual_result,match_time,home_team,away_team FROM predictions WHERE match_id=?', (payload['match_id'],)).fetchone()
                if saved and (saved[0]!='PENDING' or epoch(saved[1],KST)<=datetime.now(timezone.utc).timestamp()
                              or (saved[2],saved[3])!=(match.get('home'),match.get('away'))):
                    return
            conn.execute('INSERT OR IGNORE INTO product_pick_revision_history VALUES (?,?,?,?,?,?,?,?)',
                         (digest([track,payload['match_id'],engine,old_row[1]]),track,payload['match_id'],engine,
                          old_row[0],old_row[1],now.isoformat(),CAMPAIGN))
            conn.execute('UPDATE product_pick_receipts SET captured_at=?,payload_json=? WHERE track=? AND match_id=? AND engine=?',
                         (now.isoformat(),json.dumps(payload,ensure_ascii=False),track,payload['match_id'],engine))
        conn.execute('INSERT OR IGNORE INTO product_pick_receipts VALUES (?,?,?,?,?)',
                     (track, payload['match_id'], engine, now.isoformat(), json.dumps(payload, ensure_ascii=False)))
    selectors = () if dashboard.get('manager_product_mode') == 'investment_by_analyst' else (('official', official_selector), ('robot', robot_selector))
    for engine, selector in selectors:
        for pick in selector(proto, 5, 10).get('picks', []):
            card = by_fixture.get(str(pick.get('fixture_id')))
            if card:
                save('manager', card, engine, pick)
    for card in proto:
        mid = str(card['match']['id'])
        pick3 = ((v3 or {}).get('picks') or {}).get(mid) or {}
        card['v3_learning_pick'] = pick3
        answers = {
            'official': (card.get('pick_categories') or {}).get('high_probability') or {},
            'robot': card.get('robot_pick') or {},
            'v2': v2_answer(card.get('v2_market_pick') or card.get('alphago_pick') or card.get('robot_pick'), card['match'].get('home'), card['match'].get('away')),
            'v3': pick3,
        }
        if not 0 < epoch(pick3.get('frozen_at')) <= now.timestamp():
            answers['v3'] = {}
        if dashboard.get('manager_product_mode') != 'investment_by_analyst':
            for engine in ('v2', 'v3'):
                save('manager', card, engine, answers[engine], 'comparison')
        if card.get('learning_campaign') == CAMPAIGN:
            for engine, pick in answers.items():
                save('proto_world',card,engine,pick)
        if any(str((x.get('match') or {}).get('id')) == mid for x in dashboard.get('top3', [])):
            for engine, pick in answers.items():
                save('top3', card, engine, pick)
    for original in dashboard.get('toto14', []):
        match = original.get('match') or {}
        ko = epoch(match.get('match_time'), KST)
        if ko <= now.timestamp():
            continue
        mid = 'TOTO14_' + str(match.get('id') or '')
        card = {**original, 'match':{**match, 'id':mid}}
        v2 = v2_answer(original.get('alphago_pick'), match.get('home'), match.get('away'))
        save('toto14', card, 'v2', v2)
        pick3 = ((v3 or {}).get('picks') or {}).get(mid) or {}
        if 0 < epoch(pick3.get('frozen_at')) <= now.timestamp() and pick3.get('market_key') == '1x2':
            save('toto14', card, 'v3', pick3)
    conn.commit()
    # Send the very same frozen manager answers to the UI. No selection work
    # is repeated on button clicks and a shown answer cannot drift from grading.
    manager_picks = {e: [] for e in ENGINES}
    for row in table_rows(conn, 'product_pick_receipts'):
        if row['track'] == 'manager' and row['match_id'] in by_id:
            manager_picks[row['engine']].append(obj(row['payload_json']))
    dashboard['manager_analyst_picks'] = manager_picks
    dashboard['top3'] = deepcopy(dashboard.get('top3', []))
    for card in dashboard.get('top3', []):
        mid = str((card.get('match') or {}).get('id'))
        stored = {r['engine']: obj(r['payload_json']) for r in table_rows(conn, 'product_pick_receipts')
                  if r['track'] == 'top3' and r['match_id'] == mid}
        if 'official' in stored:
            card['pick_categories'] = {**(card.get('pick_categories') or {}), 'high_probability': stored['official']}
        if 'robot' in stored:
            card['robot_pick'] = stored['robot']
        if 'v3' in stored:
            card['v3_learning_pick'] = stored['v3']
        if 'v2' in stored:
            card['alphago_pick'] = stored['v2']
            card['v2_market_pick'] = {**stored['v2'], 'engine':'v2-ai'}
    return manager_picks
