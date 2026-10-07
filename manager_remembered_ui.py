"""Manager-only presentation. No model calls and no private memory files."""
from datetime import datetime, timezone, timedelta
from html import escape


def local_time(value):
    try:
        parsed = datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float)) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return '시간 확인 중'
        local = parsed.astimezone(timezone(timedelta(hours=9)))
        return f'{local:%Y.%m.%d} ({"월화수목금토일"[local.weekday()]}) {local:%H:%M}'
    except (ValueError, TypeError, OverflowError, OSError):
        return '시간 확인 중'


CARD_STYLE = '''<style>
.dj-invest-card{border:1px solid #304258;border-radius:18px;padding:22px 24px;margin:14px 0 4px;background:linear-gradient(125deg,#142235,#0b1422);color:#f2f7ff}
.dj-invest-top{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center;font-size:13px;color:#c0cbd9}
.dj-invest-tag{color:#7cf0dc;background:#123635;padding:5px 10px;border-radius:7px;font-weight:700}
.dj-invest-teams{font-size:24px;font-weight:800;line-height:1.5;margin:18px 0}
.dj-invest-vs{font-size:13px;font-weight:400;color:#91a3ba;margin:0 10px}
.dj-invest-main{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;align-items:center;border-top:1px solid #304258;padding-top:17px}
.dj-invest-label{font-size:12px;color:#b0c0d5;line-height:1.6}
.dj-invest-pick{font-size:24px;font-weight:800;line-height:1.5;color:#7cf0dc;overflow-wrap:anywhere}
.dj-invest-odd{font-size:36px;font-weight:800;color:#fff;line-height:1.3;text-align:right}
.dj-invest-odd small{font-size:16px;color:#c0cbd9}
@media(max-width:600px){.dj-invest-card{padding:18px 16px}.dj-invest-teams{font-size:20px}.dj-invest-pick{font-size:20px}.dj-invest-odd{font-size:30px}.dj-invest-main{gap:12px}}
</style>'''


def card_html(row, index):
    home, away, pick = (escape(str(row[key])) for key in ('home', 'away', 'raw_pick'))
    market = {'1x2': '승무패', 'handicap': '핸디캡'}.get(row.get('market_key'), '선택한 픽')
    result = escape(str(row.get('result_label') or ('적중' if row.get('is_correct')==1 else '미적중' if row.get('is_correct')==0 else '결과 대기')))
    score = escape(str(row.get('actual_score') or ''))
    return (
        '<div class="dj-invest-card"><div class="dj-invest-top">'
        f'<span class="dj-invest-tag">투자픽 {index:02d} · {market} · {result} {score}</span>'
        f'<span>경기 시작 · {local_time(row["kickoff_at"])} · 한국 시간</span></div>'
        f'<div class="dj-invest-teams">{home}<span class="dj-invest-vs">VS</span>{away}</div>'
        '<div class="dj-invest-main"><div><div class="dj-invest-label">선택한 픽</div>'
        f'<div class="dj-invest-pick">{pick}</div></div><div>'
        '<div class="dj-invest-label" style="text-align:right">선택 당시 배당</div>'
        f'<div class="dj-invest-odd">{row["odd"]:.2f}<small> 배</small></div></div></div></div>'
    )


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
        until=r.get('display_until')
        try:
            expiry=datetime.fromisoformat(until.replace('Z','+00:00')) if until else None
        except (ValueError, TypeError):
            expiry=None
        # Missing end confirmation is retained, never guessed from kickoff.
        (past if expiry and expiry<=now else future).append(r)
    st.markdown(CARD_STYLE, unsafe_allow_html=True)
    st.subheader(f"{labels.get(engine,engine)} 관리자 투자픽")
    st.caption('분석가가 고른 픽 · 경기 시작 후에도 표시 · 묶음의 마지막 경기 종료 확인 후 2시간 유지')
    st.caption('업데이트 · '+local_time(payload.get('generated_at'))+' · 한국 시간')
    if payload.get('paused'):
        st.info('분석 작업이 일시중지 상태입니다. 이미 저장된 픽은 보존됩니다.')
    info=(payload.get('engines') or {}).get(engine,{})
    count=info.get('graded_count',0)
    cols=st.columns(3)
    cols[0].metric('표시 중 투자픽', f'{len(future)}경기')
    cols[1].metric('실전 적중 / 채점', f'{info.get("hit_count",0)} / {count}' if count else '결과 대기')
    cols[2].metric('최장 연속 적중', f'{info.get("longest_wins",0)}경기' if count else '결과 대기')
    if count:
        st.write(f"실전 채점 {count}경기 · 적중 {info.get('hit_count',0)}경기 · "
                 f"경기당 1단위 비교 순손익 {info.get('unit_profit',0):+.2f} · "
                 f"순수익률 {100*info.get('unit_roi',0):+.2f}%")
        st.caption(f"비교 투자 {info.get('unit_stake',0)}단위 · 원금 포함 회수 {info.get('unit_return',0):.2f}단위 · "
                   f"최장 연속 적중 {info.get('longest_wins',0)}경기 · 최장 연속 실패 {info.get('longest_losses',0)}경기")
    for index,row in enumerate(sorted(future,key=lambda r:(r['kickoff_at'],r['cycle'],r['rank'])),1):
        st.markdown(card_html(row,index),unsafe_allow_html=True)
        with st.expander(f'투자픽 {index:02d} · 선택 이유와 상세 정보'):
            st.write(row['reason'])
            st.caption(f"분석가의 적중 가능성 추정 {row['probability']*100:.1f}% · 실제 적중률과 구분")
            st.caption('선택 저장 · '+local_time(row.get('frozen_at'))+' · 한국 시간')
            st.caption('배당 수집 · '+local_time(row.get('odds_captured_at'))+' · 한국 시간')
    st.caption('선택 당시 배당이며 현재 판매 배당은 달라질 수 있습니다.')
    st.caption('실전 성적은 모의시험과 분리합니다. 손익은 경기당 1단위 비교 기준이며 실제 투자금이 아닙니다.')
    if not future:
        st.info('현재 표시할 저장 픽이 없습니다. 분석 진행 상태와 자료 수신을 확인 중입니다.')
    with st.expander(f'지난 픽·채점·복기 {len(past)}건'):
        for row in sorted(past,key=lambda r:r['kickoff_at'],reverse=True):
            grade='적중' if row.get('is_correct')==1 else '미적중' if row.get('is_correct')==0 else '결과 대기'
            if not row.get('delivered_at'): grade='시작 전 게시 확인 없음 · 성적 제외'
            st.write(f"{row['home']} vs {row['away']} · {row['raw_pick']} · {row['odd']:.2f}배 · {grade}")
            st.caption('경기 시작 · '+local_time(row.get('kickoff_at'))+' · 한국 시간')
            st.write('선택 당시 분석: '+row['reason'])
            if row.get('actual_score'): st.write('결과: '+row['actual_score'])
            st.write('복기: '+(row.get('review') or '기록 대기'))
