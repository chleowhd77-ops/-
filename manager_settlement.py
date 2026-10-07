"""Local settlement independent of analyst availability. No model or sports API calls."""
import importlib
import json
from pathlib import Path
import sys
import time
from datetime import datetime, timezone, timedelta


def annotate(rt, state, payload):
    """Keep a daily delivered batch until its last confirmed finish plus two hours."""
    ends = rt.optional(Path(state)/'end_observations.json', {})
    groups = {}
    now = time.time()
    for key, row in payload['picks'].items():
        day = datetime.fromtimestamp(rt.epoch(row['frozen_at']), timezone(timedelta(hours=9))).date().isoformat()
        batch = row['engine_key']+':'+day
        row['batch_id'] = batch
        end = ends.get(key, {})
        row['ended_at'] = end.get('ended_at')
        row['end_time_source'] = end.get('source')
        if row.get('status') == 'CANCELED':
            row['result_label'] = '취소 · 채점 제외'
        elif row.get('is_correct') == 1:
            row['result_label'] = '적중'
        elif row.get('is_correct') == 0:
            row['result_label'] = '미적중'
        else:
            row['result_label'] = '채점 대기' if rt.epoch(row['kickoff_at']) <= now else '경기 전'
        groups.setdefault(batch, []).append(row)
    for rows in groups.values():
        # An unpublished pick must not hold a delivered batch open forever.
        relevant = [r for r in rows if r.get('delivered_at')] or rows
        complete = all(r.get('ended_at') and (r.get('is_correct') in (0, 1) or r.get('status')=='CANCELED') for r in relevant)
        until = max(rt.epoch(r['ended_at']) for r in relevant)+7200 if complete else None
        for row in rows:
            row['display_until'] = datetime.fromtimestamp(until, timezone.utc).isoformat() if until else None
            row['display_active'] = until is None or now < until
    return payload


def remember_ends(rt, root, state):
    """Persist the collector's first observed final time; never guess kickoff + 90."""
    live = rt.optional(Path(root)/'live_scores.json', {})
    ends = rt.optional(Path(state)/'end_observations.json', {})
    changed = False
    for engine in rt.ENGINES:
        for path in (Path(state)/'picks'/engine).glob('*.json'):
            pick = rt.read(path)
            identity = pick['identity']
            key = rt.PUBLIC_KEYS[engine]+':'+pick['case_id']
            if key in ends:
                continue
            row = live.get(identity['match_id'], {})
            if not isinstance(row, dict):
                continue
            ended = row.get('terminal_at')
            if (row.get('final') and row.get('fixture_id') == identity['fixture_id']
                    and ended and row.get('status') in {'FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO'}):
                finish = rt.epoch(ended)
                if identity['kickoff'] <= finish <= time.time()+60:
                    ends[key] = {'ended_at': ended, 'source': 'collector_terminal_at',
                                 'fixture_id': identity['fixture_id']}
                    changed = True
            if key not in ends:
                grade = rt.optional(Path(state)/'grades'/engine/path.name, {})
                confirmed = grade.get('graded_at')
                if confirmed and identity['kickoff'] <= rt.epoch(confirmed) <= time.time()+60:
                    # Older live records can already have expired. Keep two full hours
                    # after result confirmation, explicitly labelled; no guessed finish.
                    ends[key] = {'ended_at': confirmed, 'source': 'local_grade_confirmation',
                                 'fixture_id': identity['fixture_id']}
                    changed = True
    if changed:
        rt.write(Path(state)/'end_observations.json', ends)


def settle(rt, root, state, publish=True):
    import fcntl
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    with (state/'settlement.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'already_running': True, 'AI_requests': 0}
        return _settle_locked(rt, root, state, publish)


def _settle_locked(rt, root, state, publish=True):
    state = Path(state)
    if not (state/'ACTIVATED.json').exists():
        return {'activated': False, 'AI_requests': 0}
    remember_ends(rt, root, state)
    before = sum(len(list((state/'grades'/e).glob('*.json'))) for e in rt.ENGINES)
    rt.grade(state, root)
    remember_ends(rt, root, state)
    payload = rt.public_payload(state)
    after = sum(len(list((state/'grades'/e).glob('*.json'))) for e in rt.ENGINES)
    report = {'checked_at': rt.stamp(), 'activated': True, 'new_grades': after-before,
              'total_grades': after, 'engines': payload['engines'], 'AI_requests': 0,
              'publication': 'not_requested',
              'waiting': sum(r.get('is_correct') not in (0, 1) and r.get('status')!='CANCELED' and rt.epoch(r['kickoff_at']) <= time.time()
                             for r in payload['picks'].values()),
              'finish_time_missing': sum(r.get('is_correct') in (0, 1) and not r.get('ended_at')
                                         for r in payload['picks'].values())}
    rt.write(state/'settlement_status.json', report)
    if publish:
        try:
            rt.publish_payload(root, state)
            report['publication'] = 'confirmed'
        except Exception as error:
            report['publication'] = 'pending'
            report['publication_error'] = type(error).__name__+': '+str(error)
            rt.write(state/'settlement_status.json', report)
            raise
    rt.write(state/'settlement_status.json', report)
    print(f"[관리자 채점] 신규 {after-before}건 / 누적 {after}건 / 결과 대기 {report['waiting']}건 · 게시 {report['publication']} · AI 요청 0회", flush=True)
    return report


def from_activation(root, publish=True):
    root = Path(root)
    state = root/'dj-manager-memory/runtime'
    if not (state/'ACTIVATED.json').exists():
        return {'activated': False, 'AI_requests': 0}
    active = json.loads((state/'ACTIVATED.json').read_text())
    code = Path(active['code']).resolve()
    code.relative_to((root/'dj-manager-memory/runtime-code').resolve())
    sys.path[:0] = [str(code), str(root)]
    rt = importlib.import_module('manager_remembered_runtime')
    return settle(rt, root, state, publish)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/home/ubuntu'))
    args = parser.parse_args()
    from_activation(args.root)
