"""Manager-only presentation. No model calls and no private memory files."""
from datetime import datetime, timezone
from html import escape


def render(st, payload, engine):
    labels={'official':'공식픽','robot':'자율로봇','v2':'V2','v3':'V3'}
    now=datetime.now(timezone.utc)
    own=[r for r in payload.get('picks',{}).values() if r.get('engine_key')==engine]
    future=[]; past=[]
    for r in own:
        try:
            kickoff=datetime.fromisoformat(r['kickoff_at'].replace('Z','+00:00'))
            if kickoff.tzinfo is None: raise ValueError('missing timezone')
        except (ValueError,KeyError):
            continue
        (future if kickoff>now and r.get('status')!='FINISHED' else past).append(r)
    st.subheader(f"{labels.get(engine,engine)} 관리자 투자픽")
    st.caption('프로토라이브 시작 전 경기에서 분석가가 자신의 정답·오답 기억을 참고해 고른 자신픽입니다. 최대 10개이며 억지로 채우지 않습니다.')
    st.caption('최근 서버 게시: '+str(payload.get('generated_at') or '확인 대기'))
    if payload.get('paused'):
        st.info('분석 작업이 일시중지 상태입니다. 이미 저장된 픽은 보존됩니다.')
    info=(payload.get('engines') or {}).get(engine,{})
    count=info.get('graded_count',0)
    if count:
        st.write(f"실전 채점 {count}경기 · 적중 {info.get('hit_count',0)}경기 · "
                 f"경기당 1단위 비교 순손익 {info.get('unit_profit',0):+.2f} · "
                 f"순수익률 {100*info.get('unit_roi',0):+.2f}%")
        st.caption(f"비교 투자 {info.get('unit_stake',0)}단위 · 원금 포함 회수 {info.get('unit_return',0):.2f}단위 · "
                   f"최장 연속 적중 {info.get('longest_wins',0)}경기 · 최장 연속 실패 {info.get('longest_losses',0)}경기")
    else:
        st.caption('실전 채점 대기 · 이전 모의시험 성적과 분리하여 집계합니다.')
    st.caption('아래 배당은 선택에 사용한 관측 배당이며 최신 판매 배당과 다를 수 있습니다. 손익은 실제 투자금이 아닌 경기당 1단위 비교 기준입니다.')
    for row in sorted(future,key=lambda r:(r['kickoff_at'],r['cycle'],r['rank'])):
        st.markdown(f"**{row['home']} vs {row['away']}**")
        st.write(f"{row['raw_pick']} · 관측 배당 {row['odd']:.2f}배 · 분석가 추정 {row['probability']*100:.1f}%")
        st.write(row['reason'])
        st.caption(f"킥오프 {row['kickoff_at']} · 선택 저장 {row['frozen_at']}")
    if not future:
        st.info('현재 저장된 시작 전 자신픽이 없습니다. 분석 진행 상태와 자료 수신을 확인 중입니다.')
    with st.expander(f'지난 픽·채점·복기 {len(past)}건'):
        for row in sorted(past,key=lambda r:r['kickoff_at'],reverse=True):
            grade='적중' if row.get('is_correct')==1 else '미적중' if row.get('is_correct')==0 else '결과 대기'
            if not row.get('delivered_at'): grade='시작 전 게시 확인 없음 · 성적 제외'
            st.write(f"{row['home']} vs {row['away']} · {row['raw_pick']} · {row['odd']:.2f}배 · {grade}")
            st.write('선택 당시 분석: '+row['reason'])
            if row.get('actual_score'): st.write('결과: '+row['actual_score'])
            st.write('복기: '+(row.get('review') or '기록 대기'))
