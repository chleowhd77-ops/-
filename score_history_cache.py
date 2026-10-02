"""Rebuildable compact grading projection; original histories are never rewritten.

Triggers only record dirty IDs. The worker copies changed rows in bounded batches,
so updates to old answers and deletes cannot silently leave stale grades.
"""
import json
import time

SOURCES = {
    'prediction_analysis_snapshots': (('id','match_id','stage','created_at'), 'decision_json'),
    'robot_learning_samples': (('id','source','match_id','home_team','away_team','kickoff_at',
        'api_fixture_id','captured_at','captured_timestamp'), 'robot_pick_json'),
}
PICK_FIELDS = ('raw_pick','pick','probability','prob','odd','model_version','code',
               'v2_ai_pick','engine','market_key','selection_side')


def obj(value):
    if isinstance(value,dict): return value
    try:
        parsed=json.loads(value or '{}')
        return parsed if isinstance(parsed,dict) else {}
    except (TypeError,ValueError): return {}


def compact_pick(value):
    result = {k:v for k,v in obj(value).items() if k in PICK_FIELDS}
    # A nonempty unavailable alphago response suppresses fallback to robot.
    # Preserve that truthiness even when it contains only diagnostic fields.
    return result or ({'_present':True} if value else {})


def compact(value, column):
    data=obj(value)
    if column=='decision_json':
        data={key:compact_pick(data[key]) for key in ('robot_pick','alphago_pick') if key in data}
    else:
        data=compact_pick(data)
    return json.dumps(data,ensure_ascii=False,separators=(',',':'))


def cache_name(table):
    return 'score_compact_r18_'+table


def resolved_table(conn, table):
    if table not in SOURCES: return table
    exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='score_compact_state_r18'").fetchone()
    if not exists: return table
    state=conn.execute('SELECT watermark,ready FROM score_compact_state_r18 WHERE source=?',(table,)).fetchone()
    if not state or not state[1]: return table
    dirty=conn.execute('SELECT 1 FROM score_compact_dirty_r18 WHERE source=? LIMIT 1',(table,)).fetchone()
    maximum=conn.execute(f'SELECT MAX(id) FROM main.{table}').fetchone()[0] or 0
    return cache_name(table) if not dirty and maximum<=state[0] else table


def prepare(conn, budget_seconds=60):
    """Return False while initial backfill is pending; retain the published feed."""
    started=time.monotonic()
    conn.execute('CREATE TABLE IF NOT EXISTS score_compact_state_r18(source TEXT PRIMARY KEY,watermark INTEGER NOT NULL DEFAULT 0,ready INTEGER NOT NULL DEFAULT 0)')
    conn.execute('CREATE TABLE IF NOT EXISTS score_compact_dirty_r18(source TEXT NOT NULL,id INTEGER NOT NULL,PRIMARY KEY(source,id))')
    copied=0
    for source,(metadata,payload) in SOURCES.items():
        schema={r[1]:r for r in conn.execute(f'PRAGMA table_info({source})')}
        if 'id' not in schema or payload not in schema: continue
        columns=tuple(c for c in metadata if c in schema)+(payload,)
        cache=cache_name(source)
        conn.execute(f'CREATE TABLE IF NOT EXISTS {cache} AS SELECT {",".join(columns)} FROM main.{source} WHERE 0')
        conn.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS idx_{cache}_id ON {cache}(id)')
        conn.execute('INSERT OR IGNORE INTO score_compact_state_r18(source) VALUES (?)',(source,))
        for event,ref in (('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')):
            # Updates irrelevant to grading do not invalidate the projection.
            event_sql='UPDATE OF '+','.join(columns) if event=='UPDATE' else event
            body=f"INSERT OR IGNORE INTO score_compact_dirty_r18 VALUES ('{source}',{ref}.id);"
            if event=='UPDATE':
                body+=f" INSERT OR IGNORE INTO score_compact_dirty_r18 VALUES ('{source}',OLD.id);"
            conn.execute(f'CREATE TRIGGER IF NOT EXISTS dirty_r18_{source}_{event.lower()} AFTER {event_sql} ON main.{source} BEGIN {body} END')
        conn.commit()
        watermark=conn.execute('SELECT watermark FROM score_compact_state_r18 WHERE source=?',(source,)).fetchone()[0]
        conn.execute('UPDATE score_compact_state_r18 SET ready=0 WHERE source=?',(source,))
        conn.commit()
        while True:
            dirty=[r[0] for r in conn.execute('SELECT id FROM score_compact_dirty_r18 WHERE source=? ORDER BY id LIMIT 64',(source,))]
            if dirty:
                marks=','.join('?' for _ in dirty)
                rows=conn.execute(f'SELECT {",".join(columns)} FROM main.{source} WHERE id IN ({marks})',dirty).fetchall()
                conn.executemany(f'DELETE FROM {cache} WHERE id=?',[(i,) for i in dirty])
            else:
                rows=conn.execute(f'SELECT {",".join(columns)} FROM main.{source} WHERE id>? ORDER BY id LIMIT 64',(watermark,)).fetchall()
            if not rows and not dirty:
                conn.execute('UPDATE score_compact_state_r18 SET ready=1 WHERE source=?',(source,))
                conn.commit()
                break
            values=[tuple(row[:-1])+(compact(row[-1],payload),) for row in rows]
            conn.executemany(f'INSERT OR REPLACE INTO {cache} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',values)
            if dirty:
                conn.executemany('DELETE FROM score_compact_dirty_r18 WHERE source=? AND id=?',[(source,i) for i in dirty])
            elif rows:
                watermark=rows[-1][0]
                conn.execute('UPDATE score_compact_state_r18 SET watermark=? WHERE source=?',(watermark,source))
            conn.commit()
            copied+=len(rows)
            if time.monotonic()-started>=budget_seconds:
                print(f'⏳ 채점 답안목록 준비 중 · {source} · 이번 {copied}행 · 위치 {watermark} · 다음 주기에 이어서 · 기존 성적표 유지',flush=True)
                return False
    print(f'✅ 채점 답안목록 준비 완료 · 갱신 {copied}행 · {time.monotonic()-started:.1f}초',flush=True)
    return True
