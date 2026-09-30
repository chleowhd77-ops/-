"""Small, atomic learning registry shared by learners, publishers and readers."""
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CAMPAIGN = 'R7.13.13-visible-learning-review'
ENGINES = ('official', 'robot_proto', 'robot_toto14', 'v2', 'v3')


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {} if default is None else default


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:20]


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name+'.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def state(root):
    return read_json(Path(root)/'learning_status.json')


def active_entry(root, engine):
    return (state(root).get('engines') or {}).get(engine) or {}


def active_artifact(root, engine):
    entry = active_entry(root, engine)
    name = entry.get('artifact')
    return read_json(Path(root)/'.learning_models'/name) if name and Path(name).name == name else {}


def campaign_ready(root):
    info = state(root)
    return info.get('campaign') == CAMPAIGN and bool(info.get('refresh_ready'))


def versions(root, source='PROTO'):
    entries = state(root).get('engines') or {}
    return {name: (entries.get(key) or {}).get('active_version', 'legacy-unverified')
            for name, key in [('official','official'),('robot','robot_toto14' if source=='TOTO14' else 'robot_proto'),('v2','v2'),('v3','v3')]}


def stamp(root, source='PROTO'):
    return dict(learning_models=versions(root,source), learning_applied_at=now_iso(),
                learning_campaign=CAMPAIGN if campaign_ready(root) else '')


def needs_refresh(root, card, kickoff, now):
    return bool(kickoff and now < kickoff and campaign_ready(root)
                and (card or {}).get('learning_campaign') != CAMPAIGN)


def archive_json_row(payload, key, row, reason=CAMPAIGN):
    archive = payload.setdefault('pick_revision_history', {})
    fingerprint = digest([key,row])
    archive.setdefault(fingerprint, dict(ledger_key=key, previous=dict(row), archived_at=now_iso(), reason=reason))


def before_kickoff(match, now=None):
    from scorecard_core import epoch, KST
    moment = now.timestamp() if now is not None else datetime.now(timezone.utc).timestamp()
    return epoch(match.get('match_time') or match.get('kickoff_at'), KST) > moment


def revision_allowed(old, fresh, match, now=None):
    return bool(old and fresh.get('learning_campaign') == CAMPAIGN
                and old.get('learning_campaign') != CAMPAIGN
                and old.get('is_correct') not in (0,1)
                and str(old.get('status','')).upper() not in ('FINISHED','CANCELED')
                and before_kickoff(match,now))


def guard_ledger_revisions(database, payload, previous):
    """Final publication check; settlement may update grades, never old answers."""
    import sqlite3
    from scorecard_core import epoch, KST
    picks = payload.get('picks') or {}
    old_picks = previous.get('picks') or {}
    now = datetime.now(timezone.utc).timestamp()
    with sqlite3.connect(f'file:{Path(database)}?mode=ro',uri=True) as db:
        for key in set(picks) | set(old_picks):
            new,old = picks.get(key),old_picks.get(key)
            # A grade-only update is always preserved.
            fields = ('raw_pick','selection_side','learning_campaign','model_version')
            if old and new and all(old.get(k)==new.get(k) for k in fields):
                continue
            row = new or old or {}
            mid = str(row.get('match_id') or key.split(':',1)[-1])
            saved = db.execute('SELECT actual_result,match_time FROM predictions WHERE match_id=?',(mid,)).fetchone()
            times = [epoch(r.get('kickoff_at'),KST) for r in (old,new) if r and r.get('kickoff_at')]
            if saved:
                times.append(epoch(saved[1],KST))
            locked = (not times or any(t<=now for t in times) or (saved and saved[0]!='PENDING'))
            if not locked:
                continue
            if old:
                picks[key]=dict(old)
            else:
                picks.pop(key,None)
            payload['pick_revision_history']={k:v for k,v in (payload.get('pick_revision_history') or {}).items()
                if v.get('ledger_key')!=key or k in (previous.get('pick_revision_history') or {})}
            receipts=payload.get('reanalysis_receipts') or {}
            if key in (previous.get('reanalysis_receipts') or {}):
                receipts[key]=previous['reanalysis_receipts'][key]
            else:
                receipts.pop(key,None)
    return payload
