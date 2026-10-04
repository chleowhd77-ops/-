"""Private, read-only input bridge for the four remembered manager analysts.

This module never calls an AI, changes customer picks, or writes to SQLite.
Only actual scheduled PROTO cards enter the pool. Selection belongs to the
analysts; there are no operator probability/EV/price-ranking thresholds here.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone, timedelta

ENGINES = ('official', 'robot_proto', 'v2', 'v3')
KST = timezone(timedelta(hours=9))
EXCLUDED = {
    'actual_result', 'actual_score', 'actual_home_goals', 'actual_away_goals',
    'is_correct', 'outcome', 'result_known_at', 'result_known_timestamp',
    'postmortem_json', 'candidate_results_json', 'robot_pick_correct',
    'prob', 'probability', 'model_probability', 'raw_model_probability',
    'robust_probability', 'robot_probability', 'v3_probability',
    'school_probability', 'robot_pick', 'model_context', 'goal_model_audit',
    'context_audit', 'derived_signals', 'selected_pick', 'selected_id',
    'prob_pick', 'ev_pick', 'official_pick', 'alphago_pick', 'v3_learning_pick',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def epoch(value):
    if isinstance(value, (float, int)):
        return float(value) if math.isfinite(value) else 0
    value = str(value or '').strip()
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=KST).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    except ValueError:
        match = re.fullmatch(r'(\d{2})\.(\d{2})\.(\d{2})\s*\([^)]*\)\s*(\d{2}):(\d{2})', value)
        if not match:
            return 0
        year, month, day, hour, minute = map(int, match.groups())
        try:
            return datetime(2000+year, month, day, hour, minute, tzinfo=KST).timestamp()
        except ValueError:
            return 0


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k not in EXCLUDED}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def options(candidates):
    result = {}
    for c in candidates:
        if not isinstance(c, dict) or c.get('market_key') not in ('1x2', 'handicap'):
            continue
        if c.get('selection_side') not in ('home', 'draw', 'away'):
            continue
        try:
            odd = float(c.get('odd') or 0)
            line = float(c['handicap_base']) if c['market_key'] == 'handicap' else None
        except (ValueError, KeyError, TypeError):
            continue
        if not math.isfinite(odd) or odd <= 1:
            continue
        # The existing source is a three-way handicap, not an Asian split line.
        if line is not None and (not math.isfinite(line) or not line.is_integer()):
            continue
        o = {k: c.get(k) for k in ('market_key', 'selection_side', 'raw_pick')}
        if not o['raw_pick']:
            continue
        o.update(odd=odd, handicap_base=line)
        oid = digest([o['market_key'], o['selection_side'], line, o['raw_pick']])
        result[oid] = dict(o, option_id=oid)
    return list(result.values())


def load_pool(root, now=None):
    """Read full pre-match evidence, never the other analysts' selected answers."""
    root = Path(root)
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    dashboard = read(root/'dashboard_data.json')
    cards = dashboard.get('proto')
    if not isinstance(cards, list):
        raise ValueError('프로토라이브 경기 목록 확인 필요')
    db = sqlite3.connect((root/'ai_predictions.db').resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    pool, excluded, seen = [], [], set()
    try:
        db.execute('BEGIN')
        for card in cards:
            match = card.get('match') or {}
            mid = str(match.get('id') or '')
            kickoff = (epoch(card.get('timestamp')) or epoch(card.get('final_match_time'))
                       or epoch(match.get('match_time') or match.get('time')))
            if not mid or mid in seen:
                continue
            seen.add(mid)
            if kickoff <= now:
                excluded.append({'match_id': mid, 'reason': '시작 전 시각 확인 불가 또는 이미 시작'})
                continue
            row = db.execute('SELECT * FROM predictions WHERE match_id=? ORDER BY id DESC LIMIT 1', (mid,)).fetchone()
            if row is None or row['is_toto14'] or row['actual_result'] not in (None, '', 'PENDING'):
                excluded.append({'match_id': mid, 'reason': '시작 전 프로토 DB 상태 확인 필요'})
                continue
            if ((row['home_team'], row['away_team']) != (match.get('home'), match.get('away'))
                    or epoch(row['match_time']) != kickoff):
                excluded.append({'match_id': mid, 'reason': '화면과 DB의 경기·시각 불일치'})
                continue
            sample = db.execute('''SELECT id,api_fixture_id,home_team,away_team,kickoff_timestamp,
                captured_timestamp,full_evidence_json FROM robot_learning_samples
                WHERE match_id=? AND source='PROTO' AND api_fixture_id=?
                  AND kickoff_timestamp=? AND captured_timestamp>0
                  AND captured_timestamp<kickoff_timestamp AND captured_timestamp<=?
                ORDER BY captured_timestamp DESC,id DESC LIMIT 1''',
                (mid, row['api_fixture_id'], kickoff, now)).fetchone()
            snapshot = db.execute('''SELECT id,created_at,candidates_json,analysis_version,odds_source
                FROM prediction_analysis_snapshots WHERE match_id=?
                AND (stage LIKE 'T-%' OR stage='regular') ORDER BY id DESC LIMIT 1''', (mid,)).fetchone()
            if sample is None or snapshot is None:
                excluded.append({'match_id': mid, 'reason': '원본 경기 자료 또는 배당 기록 미수신'})
                continue
            if (sample['home_team'], sample['away_team']) != (match.get('home'), match.get('away')):
                excluded.append({'match_id': mid, 'reason': '원자료의 팀 연결 불일치'})
                continue
            try:
                # SQLite CURRENT_TIMESTAMP is UTC in the source collector.
                captured = datetime.fromisoformat(snapshot['created_at'].replace('Z', '+00:00'))
                if captured.tzinfo is None:
                    captured = captured.replace(tzinfo=timezone.utc)
                evidence = json.loads(sample['full_evidence_json'])
                offered = options(json.loads(snapshot['candidates_json']))
            except (ValueError, TypeError, AttributeError):
                excluded.append({'match_id': mid, 'reason': '원자료 형식 확인 필요'})
                continue
            if not isinstance(evidence, dict) or not evidence or not offered or not 0 < captured.timestamp() <= now < kickoff:
                excluded.append({'match_id': mid, 'reason': '실제 배당·원자료·기록 시각 확인 필요'})
                continue
            identity = {'match_id': mid, 'fixture_id': row['api_fixture_id'],
                        'home': row['home_team'], 'away': row['away_team'], 'kickoff': kickoff}
            pool.append({'case_id': digest(identity), 'identity': identity,
                         'evidence': clean(evidence), 'options': offered,
                         'source': {'sample_id': sample['id'], 'snapshot_id': snapshot['id'],
                                    'evidence_captured_at': sample['captured_timestamp'],
                                    'odds_captured_at': captured.timestamp(),
                                    'analysis_version': snapshot['analysis_version'],
                                    'odds_source': snapshot['odds_source']}})
    finally:
        db.close()
    return {'captured_at': now, 'pool': pool, 'excluded': excluded,
            'source_card_count': len(cards), 'pool_digest': digest(pool)}


def load_memory(release, engine, pool):
    if engine not in ENGINES:
        raise ValueError('Unknown analyst')
    release = Path(release)
    verification = read(release/'MEMORY_RESTORE_VERIFIED.json')
    raw = (release/'derived_manager_memory'/f'{engine}.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != verification['analysts'][engine]['export_sha256']:
        raise ValueError('이전 기억 파일 검증 실패: '+engine)
    memory = json.loads(raw)
    if memory['analyst'] != engine:
        raise ValueError('분석가 기억 연결 불일치')
    fixture_ids = {str(p['identity']['fixture_id']) for p in pool}
    case_ids = {p['case_id'] for p in pool}
    for field in ('successful_memory', 'error_memory'):
        memory[field] = [m for m in memory[field]
                         if str(m['identity']['fixture_id']) not in fixture_ids
                         and m['case_id'] not in case_ids]
    return memory


def prepare(root, release, now=None):
    inputs = load_pool(root, now)
    memories = {engine: load_memory(release, engine, inputs['pool']) for engine in ENGINES}
    summary = {'source_card_count': inputs['source_card_count'], 'eligible_matches': len(inputs['pool']),
               'excluded': inputs['excluded'], 'pool_digest': inputs['pool_digest'], 'AI_requests': 0,
               'web_active': False, 'analysts': {}}
    for engine, memory in memories.items():
        packet = {'mode': 'manager_selection', 'analyst': engine, 'memory': memory,
                  'questions': inputs['pool'], 'maximum_selections': 10}
        serialized = json.dumps(packet, ensure_ascii=False, allow_nan=False)
        summary['analysts'][engine] = {'correct_memories': len(memory['successful_memory']),
            'wrong_case_memories': len(memory['error_memory']), 'bootstrap_records': memory['bootstrap_records'],
            'packet_characters': len(serialized), 'packet_sha256': hashlib.sha256(serialized.encode()).hexdigest()}
    return inputs, summary
