"""Bounded scorecard rendering; never reads the network or a database."""
import time
from datetime import datetime, timezone
from html import escape
import streamlit as st
from scorecard_core import ENGINES, TRACKS, KST, epoch, summary

ENGINE_LABELS = dict(zip(ENGINES, ('① Codex 공식픽', '② 자율 로봇픽', '③ V2 알파고', '④ V3 학습픽')))
TRACK_LABELS = dict(zip(TRACKS, ('관리자픽', 'TOP3', '프로토 LIVE', '승무패14')))


def select_buttons(key, labels, default):
    if st.session_state.get(key) not in labels:
        st.session_state[key] = default
    def choose(value):
        st.session_state[key] = value
    for col, (value, label) in zip(st.columns(len(labels)), labels.items()):
        col.button(label, key=f'{key}-{value}', use_container_width=True,
                   type='primary' if st.session_state[key] == value else 'secondary',
                   on_click=choose, args=(value,))
    return st.session_state[key]


def render_rows(rows, key):
    if not rows:
        return
    days = sorted({datetime.fromtimestamp(epoch(r.get('kickoff_at'), KST), KST).strftime('%Y-%m-%d')
                   if epoch(r.get('kickoff_at'), KST) else '날짜 미연결' for r in rows}, reverse=True)
    day = st.selectbox('날짜', ['전체'] + days, key=key + '-day')
    if day != '전체':
        rows = [r for r in rows if (datetime.fromtimestamp(epoch(r.get('kickoff_at'), KST), KST).strftime('%Y-%m-%d')
                if epoch(r.get('kickoff_at'), KST) else '날짜 미연결') == day]
    pages = max(1, (len(rows) + 29) // 30)
    page = st.selectbox('페이지 · 30경기씩', range(1, pages + 1), key=f'{key}-{day}-page')
    batch = rows[(page - 1) * 30:page * 30]
    values = []
    for row in batch:
        hit = row.get('is_correct')
        ko = epoch(row.get('kickoff_at'), KST)
        state = '적중' if hit == 1 else '미적중' if hit == 0 else (
            '시작 전' if ko > time.time() else '결과 연결 대기' if ko else '경기 정보 연결 대기')
        values.append({'경기': f"{row.get('home_team') or ''} vs {row.get('away_team') or ''}".strip()
                       if row.get('home_team') else f"경기 ID {row.get('match_id', '')}",
                       '시각': str(row.get('kickoff_at') or ''), '픽': str(row.get('raw_pick') or ''),
                       '결과': str(row.get('actual_score') or ''), '채점': state,
                       '픽 모델 버전': str(row.get('model_version') or '기록 없음'),
                       '대기 사유': str(row.get('grading_wait_reason') or '') if hit not in (0,1) else ''})
    st.dataframe(values, use_container_width=True, hide_index=True)
    st.caption(f'{len(rows)}경기 중 {(page - 1) * 30 + 1}–{min(page * 30, len(rows))} · 누적 통계는 전체 기록 기준')


def render_scorecard(data):
    started = time.perf_counter()
    st.subheader('채점 노트')
    st.caption('분석가별 · 메뉴별 저장 답안과 실제 결과를 누적 집계합니다.')
    engine = select_buttons('grade-engine-v7', ENGINE_LABELS, 'official')
    track = select_buttons('grade-track-v7', {k: v + ' 채점' for k, v in TRACK_LABELS.items()}, 'proto_world')
    if data.get('audit', {}).get('collector_update_pending'):
        st.warning('화면 수정은 적용됐습니다. 서버의 새 채점 자료는 아직 도착하지 않아 기존 기록을 표시합니다.')
    cells = data.get('tracks') or {}
    for col, name in zip(st.columns(4), TRACKS):
        total = ((cells.get(name) or {}).get(engine) or {}).get('summary') or {}
        n, hits = int(total.get('graded') or 0), int(total.get('correct') or 0)
        value = f'{hits / n * 100:.1f}%' if n else '미채점' if total.get('stored') else '저장 답안 없음'
        col.metric(TRACK_LABELS[name], value)
        col.caption(f"{hits}/{n} 적중 · 대기 {int(total.get('pending') or 0)}건")
    cell = (cells.get(track) or {}).get(engine) or {}
    rows = cell.get('rows') or []
    st.markdown(f'#### {TRACK_LABELS[track]} · {ENGINE_LABELS[engine]}')
    active = ((data.get('active_models') or {}).get(track) or {}).get(engine)
    if active and active != 'legacy-unverified':
        current = [r for r in rows if r.get('model_version') == active]
        totals = summary(current)
        st.caption(f"현재 적용 모델: {active} · 이 버전 저장 {len(current)}건 / 채점 {totals['graded']}건 / 적중 {totals['correct']}건 / 대기 {totals['pending']}건")
        scope = st.radio('모델 기록', ['전체 누적','현재 적용 모델'],horizontal=True,key=f'grade-model-{track}-{engine}')
        if scope == '현재 적용 모델':
            rows = current
        st.caption('상단 적중률은 전체 누적입니다. 버전 기록이 없는 과거 픽은 현재 모델 성적으로 간주하지 않습니다.')
    if track == 'manager':
        st.caption('선택한 분석가의 관리자 투자픽 장부입니다. TOP3·프로토와 별도로 집계합니다.')
    if not rows:
        st.info('이 메뉴에서 이 분석가의 저장 답안이 아직 없습니다. 다른 메뉴의 픽을 대신 합산하지 않습니다.')
    else:
        view = st.radio('기록', ['채점 완료', '채점 대기'], horizontal=True, key=f'grade-state-{track}-{engine}')
        selected = [r for r in rows if (r.get('is_correct') in (0, 1)) == (view == '채점 완료')]
        render_rows(selected, f'grade-rows-{track}-{engine}-{view}')
        if not selected:
            st.caption('해당 상태의 기록이 없습니다.')
    legacy = data.get('manager_legacy') or []
    if track == 'manager' and legacy:
        with st.expander('기존 별도 투자 장부 · 분석가 미지정 기록 보존'):
            total = summary(legacy)
            st.caption(f"{total['correct']}/{total['graded']} 적중 · {total['pending']}건 대기. 공식픽이나 다른 로봇 성적으로 임의 배정하지 않습니다.")
            render_rows([{**r, 'home_team': r.get('home'), 'away_team': r.get('away')} for r in legacy], 'legacy-manager')
    with st.expander('채점 연결 상태'):
        audit = data.get('audit') or {}
        labels = {'prediction_matches_without_verified_answer': '경기 전 답안 미연결 경기',
                  'pre_kickoff_unverified': '경기 전 시각 확인 불가 원본', 'fixture_mismatch': '경기 ID 불일치 원본',
                  'v3_identity_or_time_missing': 'V3 경기·시각 미연결', 'v3_world_archive': '중지된 WORLD 보관 기록',
                  'source_or_answer_missing': '출처·답안 확인 불가 원본', 'stored_answers': '연결된 분석가 답안'}
        for name, label in labels.items():
            if name in audit:
                st.caption(f'{label}: {audit[name]}')
        missing = ((audit.get('missing_finished_answers') or {}).get(track) or {}).get(engine) or []
        st.caption(f'종료 경기 중 해당 분석가의 검증된 답안 미연결: {len(missing)}건')
        if missing:
            st.dataframe(missing[:100],hide_index=True,use_container_width=True)
        st.caption('원본 수는 서로 겹칠 수 있으며 경기 수와 다릅니다. 연결 불가 원본도 삭제하지 않습니다.')
        st.caption(f"자료 생성: {data.get('generated_at') or '미확인'} · 처리 시간 {time.perf_counter() - started:.3f}초")
