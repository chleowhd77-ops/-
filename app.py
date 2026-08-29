import os
import streamlit as st
import json
import requests
import sqlite3
import pandas as pd
import time
from datetime import datetime, timezone, timedelta
import re
import hashlib
from html import escape

# -----------------------------------------------------------------------------
# 0. 기본 설정 및 타이틀
# -----------------------------------------------------------------------------
APP_TITLE = "D.J PROTO ANALYTICS V3"

st.set_page_config(
    page_title=APP_TITLE, 
    page_icon="⚡", 
    layout="wide",
    initial_sidebar_state="expanded" 
)

GITHUB_REPO = "chleowhd77-ops/-"
DEFAULT_TEAM_LOGO = "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/Soccerball.svg/120px-Soccerball.svg.png"

NO_CACHE_HEADERS = {
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0'
}

# -----------------------------------------------------------------------------
# 1. 초경량 데이터 로더
# -----------------------------------------------------------------------------
def load_dashboard_data():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/dashboard_data.json?t={int(time.time())}"
    try:
        res = requests.get(url, headers=NO_CACHE_HEADERS, timeout=5)
        if res.status_code == 200: return res.json()
    except: pass
    return {"proto": [], "toto14": [], "top3": []}

def load_live_scores():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/live_scores.json?t={int(time.time())}"
    try:
        res = requests.get(url, headers=NO_CACHE_HEADERS, timeout=5)
        if res.status_code == 200: return res.json()
    except: pass
    return {}

def download_db():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/ai_predictions.db?t={int(time.time())}"
    try:
        res = requests.get(url, headers=NO_CACHE_HEADERS, timeout=5)
        if res.status_code == 200:
            with open("ai_predictions.db", "wb") as f:
                f.write(res.content)
    except: pass

download_db()

# -----------------------------------------------------------------------------
# 2. 보안 및 유저 DB 엔진
# -----------------------------------------------------------------------------
def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def init_user_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS userstable(username TEXT PRIMARY KEY, password TEXT, is_vip INTEGER DEFAULT 0)')
    conn.commit()
    conn.close()

def add_user(username, password):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('INSERT INTO userstable(username, password, is_vip) VALUES (?,?,?)', (username, password, 0))
    conn.commit()
    conn.close()

def login_user(username, password):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT * FROM userstable WHERE username = ? AND password = ?', (username, password))
    data = c.fetchall()
    conn.close()
    return data

def get_all_users():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT username, is_vip FROM userstable')
    data = c.fetchall()
    conn.close()
    return data

def upgrade_to_vip(username):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('UPDATE userstable SET is_vip = 1 WHERE username = ?', (username,))
    conn.commit()
    conn.close()

init_user_db()

# -----------------------------------------------------------------------------
# 3. 디자인 (CSS)
# -----------------------------------------------------------------------------
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700;900&display=swap');
    html, body, .stApp { background-color: #06080F !important; font-family: 'Noto Sans KR', sans-serif !important; color: #E2E8F0; overflow-x: hidden !important; }
    
    .block-container { 
        max-width: 1000px !important; 
        padding-top: 2rem !important; 
        padding-bottom: 2rem !important; 
        margin: 0 auto !important; 
    }
    
    .app-header { text-align: center; padding: 30px 0 20px 0; border-bottom: 1px solid #1E293B; margin-bottom: 30px; }
    .app-header h1 { color: #FFFFFF !important; font-size: 36px !important; font-weight: 900 !important; letter-spacing: 2px; margin: 0; background: -webkit-linear-gradient(45deg, #00F2FE, #4FACFE); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .app-header p { color: #64748B; font-size: 14px; font-weight: 700; letter-spacing: 1px; margin-top: 5px; }
    .stTabs [data-baseweb="tab-list"] { background-color: transparent !important; border-bottom: 1px solid #1E293B !important; gap: 30px !important; justify-content: center !important; }
    .stTabs [data-baseweb="tab"] { color: #64748B !important; font-weight: 900 !important; font-size: 18px !important; padding: 14px 0px !important; border: none !important; }
    .stTabs [aria-selected="true"] { color: #00F2FE !important; border-bottom: 4px solid #00F2FE !important; }
    .match-card { background-color: #0B0F19; border: 1px solid #1E293B; border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); }
    .top3-glow { border: 2px solid #00F2FE !important; box-shadow: 0 0 20px rgba(0, 242, 254, 0.15) !important; background: linear-gradient(135deg, #0A192F 0%, #06080F 100%) !important; }
    .league-title { font-size: 13px; color: #94A3B8; font-weight: 900; letter-spacing: 1px; margin-bottom: 15px; }
    
    .vs-row { display: flex; justify-content: space-between; align-items: center; width: 100%; margin-bottom: 15px; }
    .team-box { flex: 1; display: flex; align-items: center; gap: 15px; }
    .team-box.home { justify-content: flex-end; text-align: right; }
    .team-box.away { justify-content: flex-start; text-align: left; }
    .team-info-wrapper { display: flex; flex-direction: column; justify-content: center; }
    .team-box.home .team-info-wrapper { align-items: flex-end; }
    .team-box.away .team-info-wrapper { align-items: flex-start; }
    .team-name-text { display: block; color: #F8FAFC !important; font-size: 20px; font-weight: 900; letter-spacing: -0.5px; }
    .team-form-text { display: block; color: #64748B; font-size: 12px; font-weight: 700; letter-spacing: 1px; margin-top: 4px; }
    
    .injury-badge { display: block; color: #F87171; font-size: 11px; font-weight: 900; margin-top: 3px; background: rgba(248,113,113,0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid #F87171; }
    .fatigue-badge { display: block; color: #F59E0B; font-size: 11px; font-weight: 900; margin-top: 3px; background: rgba(245,158,11,0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid #F59E0B; }
    .rank-badge { display: block; color: #38BDF8; font-size: 11px; font-weight: 900; margin-top: 3px; background: rgba(56,189,248,0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid #38BDF8; }

    .team-logo { width: 50px !important; height: 50px !important; object-fit: contain; }
    
    .center-time-box { width: 120px; text-align: center; flex-shrink: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
    .match-time-text { color: #CBD5E1; font-size: 14px; font-weight: 700; display: block; margin-bottom: 4px;}
    .live-score { font-size: 28px; font-weight: 900; color: #00F2FE; display: block; margin-bottom: 4px; text-shadow: 0 0 10px rgba(0,242,254,0.6); }
    .deadline-open { color: #00F2FE; font-size: 11px; font-weight: 900; border: 1px solid #00F2FE; padding: 3px 8px; border-radius: 4px; display: inline-block;}
    .deadline-closed { color: #EF4444; font-size: 11px; font-weight: 900; background: rgba(239, 68, 68, 0.1); border: 1px solid #EF4444; padding: 3px 8px; border-radius: 4px; display: inline-block;}
    
    .odd-bar { display: flex; justify-content: space-between; background: #111827; border-radius: 6px; padding: 12px 20px; margin-bottom: 15px; border: 1px solid #1F2937; }
    .odd-item { font-size: 13px; color: #94A3B8; font-weight: 700; }
    .odd-val { color: #F1F5F9; font-weight: 900; margin-left: 6px; }
    
    .ai-story { background: rgba(0, 242, 254, 0.05); border-left: 3px solid #00F2FE; padding: 12px 15px; font-size: 13px; color: #E2E8F0; font-weight: 700; border-radius: 4px; margin-bottom: 15px; line-height: 1.6; }
    
    .pred-grid { display: flex; gap: 12px; }
    .pred-box { flex: 1; background: #0D1424; border: 1px solid #1E293B; border-radius: 8px; padding: 16px; text-align: center; display: flex; flex-direction: column; justify-content: center; align-items: center; }
    .pred-label { font-size: 12px; color: #64748B; font-weight: 900; margin-bottom: 10px; }
    .pred-value { display: block; font-size: 15px; color: #F8FAFC; font-weight: 900; line-height: 1.4; margin-bottom: 12px; word-break: keep-all; text-align: center; }
    .pred-prob { display: inline-block; font-size: 13px; color: #0B0F19; font-weight: 900; background-color: #10B981; padding: 4px 14px; border-radius: 20px; }
    
    .prob-bar-container { display: flex; height: 12px; border-radius: 6px; overflow: hidden; margin-top: 10px; background: #1E293B;}
    .prob-bar-win { background-color: #00F2FE; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color:#000; }
    .prob-bar-draw { background-color: #10B981; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color:#000; }
    .prob-bar-lose { background-color: #EF4444; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color:#000; }
    .badge-primary { background: rgba(0, 242, 254, 0.1); color: #00F2FE; border: 1px solid #00F2FE; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 900; }

    .report-card { background: #0F172A; border: 1px solid #334155; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
    .report-score { font-size: 26px !important; font-weight: 900 !important; color: #F8FAFC !important; letter-spacing: 2px; }
    .report-team { font-size: 16px; font-weight: 900; color: #CBD5E1; }
    .real-ai-note { background: rgba(16, 185, 129, 0.05); border-left: 4px solid #10B981; padding: 15px; font-size: 13px; color: #E2E8F0; margin-top: 15px; border-radius: 4px; line-height: 1.6; font-weight: 700; }
    .real-ai-note-fail { background: rgba(239, 68, 68, 0.05); border-left: 4px solid #EF4444; padding: 15px; font-size: 13px; color: #E2E8F0; margin-top: 15px; border-radius: 4px; line-height: 1.6; font-weight: 700; }

    @media (max-width: 768px) {
        .app-header h1 { font-size: 24px !important; }
        .stTabs [data-baseweb="tab-list"] { gap: 10px !important; }
        .stTabs [data-baseweb="tab"] { font-size: 14px !important; padding: 10px 0px !important; }
        .vs-row { align-items: flex-start !important; gap: 5px; }
        .team-box { width: 40%; flex: none !important; flex-direction: column !important; justify-content: flex-start !important; text-align: center !important; gap: 8px !important; }
        .team-box.home { flex-direction: column-reverse !important; }
        .team-box.away { flex-direction: column !important; }
        .team-box.home .team-info-wrapper, .team-box.away .team-info-wrapper { align-items: center !important; text-align: center !important; width: 100% !important; }
        .team-logo { width: 45px !important; height: 45px !important; margin: 0 auto; }
        .team-name-text { font-size: 14px !important; word-break: keep-all !important; white-space: normal !important; line-height: 1.3; margin-top: 5px; }
        .center-time-box { width: 20%; margin-top: 5px; }
        .live-score { font-size: 20px !important; }
        .match-time-text { font-size: 11px !important; }
        .odd-bar { flex-direction: column; align-items: center; gap: 8px; text-align: center; }
        .pred-grid { flex-direction: column; }
        .report-card > div:first-child { flex-direction: column; text-align: center; gap: 15px; }
        .report-card > div:first-child > div { text-align: center !important; }
        .report-card > div:nth-child(2) { flex-direction: column; }
    }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 4. 사이드바 (로그인 / 회원가입 / 멤버십)
# -----------------------------------------------------------------------------
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'is_vip' not in st.session_state:
    st.session_state['is_vip'] = False
if 'username' not in st.session_state:
    st.session_state['username'] = ""

st.sidebar.title("👑 멤버십 라운지")

if not st.session_state['logged_in']:
    menu = ["로그인", "회원가입"]
    choice = st.sidebar.selectbox("메뉴 선택", menu)

    if choice == "로그인":
        st.sidebar.subheader("접속하기")
        username = st.sidebar.text_input("아이디")
        password = st.sidebar.text_input("비밀번호", type='password')
        if st.sidebar.button("로그인"):
            hashed_pswd = make_hashes(password)
            result = login_user(username, hashed_pswd)
            if result:
                st.session_state['logged_in'] = True
                st.session_state['username'] = username
                st.session_state['is_vip'] = bool(result[0][2])
                st.success(f"환영합니다, {username}님!")
                st.rerun()
            else:
                st.sidebar.warning("아이디 또는 비밀번호가 틀렸습니다.")

    elif choice == "회원가입":
        st.sidebar.subheader("새 계정 만들기")
        new_user = st.sidebar.text_input("사용할 아이디")
        new_password = st.sidebar.text_input("비밀번호", type='password')
        
        st.sidebar.markdown("---")
        st.sidebar.markdown("**[필수] 서비스 이용 약관**")
        st.sidebar.caption("본 서비스는 디지털 정보(픽) 제공 상품으로, VIP 등급 전환 및 유료 정보 열람 즉시 상품의 가치가 소모된 것으로 간주하여 **전자상거래법 제17조 2항에 의거 환불이 절대 불가**합니다.")
        agree = st.sidebar.checkbox("위 환불 불가 정책에 동의합니다.")
        
        if st.sidebar.button("가입하기"):
            if not agree:
                st.sidebar.error("환불 정책에 동의하셔야 가입이 가능합니다.")
            elif new_user == "" or new_password == "":
                st.sidebar.error("아이디와 비밀번호를 입력해주세요.")
            else:
                try:
                    add_user(new_user, make_hashes(new_password))
                    st.sidebar.success("가입 성공! 상단 메뉴에서 로그인해주세요.")
                except sqlite3.IntegrityError:
                    st.sidebar.error("이미 존재하는 아이디입니다.")

else:
    st.sidebar.success(f"👤 {st.session_state['username']} 님 접속 중")
    if st.session_state['is_vip']:
        st.sidebar.markdown("💎 **등급: VIP 프리미엄**")
        st.sidebar.info("모든 경기의 잠금이 해제되었습니다.")
    else:
        st.sidebar.markdown("🥉 **등급: 일반 회원** (무료 3픽 제공)")
        st.sidebar.info("VIP 후원 계좌: 국민은행 123456-00-000000 (4만원)")
        st.sidebar.caption("입금 시 '입금자명=아이디'로 입금 후 텔레그램(@아이디)으로 연락주세요!")

    if st.sidebar.button("로그아웃"):
        st.session_state['logged_in'] = False
        st.session_state['is_vip'] = False
        st.session_state['username'] = ""
        st.rerun()

    # 🔥 기획자님 전용 관리자 모드
    if st.session_state['username'] == "admin":
        st.sidebar.markdown("---")
        st.sidebar.error("👑 관리자(CEO) 전용 모드")
        users = get_all_users()
        user_df = pd.DataFrame(users, columns=["아이디", "VIP상태(1=VIP)"])
        st.sidebar.dataframe(user_df)
        
        upgrade_target = st.sidebar.text_input("VIP 승급시킬 아이디 입력")
        if st.sidebar.button("VIP 권한 부여"):
            upgrade_to_vip(upgrade_target)
            st.sidebar.success(f"[{upgrade_target}] VIP 승급 완료!")

# -----------------------------------------------------------------------------
# 5. 레이아웃 뼈대 생성 (메인 콘텐츠)
# -----------------------------------------------------------------------------
st.markdown("""
<div class='app-header'>
    <h1>D.J PROTO ANALYTICS V3</h1>
    <p>투트랙(안전/꿀픽) A/B 채점 & 딥러닝 리얼 오답노트 시스템</p>
</div>
""", unsafe_allow_html=True)

main_tab1, main_tab2, main_tab3, main_tab4 = st.tabs([
    "프로토 LIVE", "승무패 14경기", "오늘의 TOP 3", "🔥 AI 리포트 (V3)"
])

dashboard_data = load_dashboard_data()
live_scores_data = load_live_scores()

def get_match_status(match_time_str, deadline_str):
    if not match_time_str or match_time_str == "시간 미정": return "TBD", False
    try:
        now = datetime.now(timezone(timedelta(hours=9)))
        match = re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})', match_time_str)
        if match:
            mo, d, h, m = map(int, match.groups())
            # 연말/연초에도 가장 가까운 실제 날짜를 선택한다.
            candidates = [
                datetime(year, mo, d, h, m, tzinfo=timezone(timedelta(hours=9)))
                for year in (now.year - 1, now.year, now.year + 1)
            ]
            m_dt = min(candidates, key=lambda value: abs((value - now).total_seconds()))
            d_dt = m_dt - timedelta(minutes=10)
            dead_match = re.search(r'(\d{2}):(\d{2})', deadline_str)
            if dead_match:
                dh, dm = map(int, dead_match.groups())
                d_dt = m_dt.replace(hour=dh, minute=dm)
                if dh > m_dt.hour + 12: d_dt -= timedelta(days=1)
                elif d_dt >= m_dt: d_dt = m_dt - timedelta(minutes=10)
            is_closed = now >= d_dt
            if m_dt <= now <= m_dt + timedelta(hours=2): return "LIVE", is_closed
            elif now > m_dt + timedelta(hours=2): return "FINISHED", is_closed
            else: return "UPCOMING", is_closed
    except: pass
    return "UPCOMING", False

# 🔥 라이브 경기 판별 함수 (정렬을 위해 추가됨!)
def check_is_live(item):
    m = item.get('match', {})
    match_id_str = str(m.get('id', ''))
    live_info = live_scores_data.get(match_id_str, {})
    return live_info.get("is_live") is True

def render_logo_html(logo_url):
    safe_logo = escape(str(logo_url or DEFAULT_TEAM_LOGO), quote=True)
    safe_fallback = escape(DEFAULT_TEAM_LOGO, quote=True)
    return (
        f'<img src="{safe_logo}" class="team-logo" '
        f'onerror="this.onerror=null;this.src=\'{safe_fallback}\';">'
    )

def generate_pred_boxes(picks, is_top3_tab=False):
    if not picks: return ""
    best_pick_raw = picks[0]['raw_pick']
    display_picks = sorted(picks, key=lambda x: x['prob'], reverse=True)
    html = ""
    for i, pick in enumerate(display_picks):
        is_best = (pick['raw_pick'] == best_pick_raw)
        prob_pct = round(pick.get('prob', 0) * 100, 1)
        if is_best:
            bg_style = "background:rgba(0, 242, 254, 0.05); border-color:#00F2FE;"
            title_color = "color:#00F2FE;"
            stars = "⭐⭐⭐" if prob_pct >= 65 else ("⭐⭐" if prob_pct >= 50 else "⭐")
            label = f"🥇 강력 추천 ({pick.get('label', '')}) {stars}" if is_top3_tab else f"🥇 {pick.get('label', '')} {stars}"
        else:
            bg_style = ""
            title_color = "color:#64748B;"
            label = f"서브 추천 ({pick.get('label', '')})" if is_top3_tab else pick.get('label', '')
            
        html += f"<div class='pred-box' style='{bg_style}'><div class='pred-label' style='{title_color}'>{label}</div><span class='pred-value'>{pick.get('html_pick', '')}</span><span class='pred-prob'>{prob_pct}%</span></div>"
    return html

# -----------------------------------------------------------------------------
# [TAB 1] 프로토 LIVE
# -----------------------------------------------------------------------------
with main_tab1:
    sub_soccer, sub_baseball, sub_basketball = st.tabs(["축구", "야구", "농구"])
    with sub_soccer:
        st.markdown("<div style='background:rgba(0, 242, 254, 0.1); border:1px solid #00F2FE; color:#00F2FE; padding:12px; border-radius:8px; text-align:center; font-weight:700; margin-bottom:24px;'>💡 안내: 비회원 및 일반 회원은 1일 3경기만 확인 가능합니다. (VIP 가입 시 모든 잠금 해제)</div>", unsafe_allow_html=True)
        
        proto_list = dashboard_data.get("proto", [])
        if proto_list:
            col1, col2 = st.columns([3, 1])
            with col1:
                all_leagues = sorted(list(set([m.get('league', '기타') for m in proto_list])))
                selected_league = st.selectbox("🏆 리그 필터링", ["전체 리그 보기"] + all_leagues)
            with col2:
                st.write("") 
                sort_urgent = st.toggle("🔥 마감 임박순 보기")
                
            st.markdown("<hr style='border-color: #1E293B; margin-top: 5px; margin-bottom: 25px;'>", unsafe_allow_html=True)
            
            # 필터링
            if selected_league != "전체 리그 보기": 
                proto_list = [m for m in proto_list if m.get('league') == selected_league]
            
            # 🔥 핵심: 정렬 로직 완벽 적용 (LIVE를 최상단 0순위로 강제 고정!)
            proto_list = sorted(
                proto_list, 
                key=lambda x: (
                    not check_is_live(x), # LIVE인 애들은 0(위로), 아닌 애들은 1(밑으로)
                    x.get('timestamp', 9999999999) if sort_urgent else 0 # 그다음 시간순 정렬
                )
            )
                
            displayed_count = 0
            paywall_shown = False

            for item in proto_list:
                if item.get("final_match_time", "") == "시간 미정" or item.get("match", {}).get("match_time", "") == "시간 미정":
                    continue
                    
                m = item['match']
                logo_h_tag = render_logo_html(item.get("home_logo"))
                logo_a_tag = render_logo_html(item.get("away_logo"))
                raw_deadline = m.get("deadline_time", "23:00")
                match_status, is_closed = get_match_status(item.get("final_match_time", ""), raw_deadline)
                
                match_id_str = str(m.get('id', ''))
                is_live_now = check_is_live(item)
                
                if match_status == "FINISHED" and not is_live_now: continue
                
                displayed_count += 1

                # 🔥 페이월(Paywall) 로직: 4번째 경기부터 잠금
                if displayed_count > 3 and not st.session_state['is_vip']:
                    if not paywall_shown:
                        st.markdown("""
                        <div class='match-card' style='text-align: center; padding: 50px 20px; background: rgba(15, 23, 42, 0.6); backdrop-filter: blur(8px); border: 1px solid #F59E0B;'>
                            <h2 style='color: #F59E0B; font-weight: 900; letter-spacing: 1px;'>🔒 VIP 프리미엄 전용 분석</h2>
                            <p style='color: #94A3B8; font-weight: 700; font-size: 16px; margin-top: 15px;'>4번째 경기부터는 VIP 회원에게만 제공됩니다.<br>적중률 높은 숨겨진 역배 꿀픽과 모든 데이터를 확인하세요!</p>
                            <p style='color: #38BDF8; font-size: 14px; margin-top: 25px; background: rgba(56,189,248,0.1); display: inline-block; padding: 8px 15px; border-radius: 8px;'>👉 좌측 사이드바(화살표 클릭)에서 로그인/회원가입 후 VIP 승급을 요청해주세요.</p>
                        </div>
                        """, unsafe_allow_html=True)
                        paywall_shown = True
                    continue 

                if is_live_now:
                    if match_id_str in live_scores_data:
                        live_info = live_scores_data[match_id_str]
                        score_text = live_info.get("score", "0:0")
                        if not score_text or score_text == "-": score_text = "0:0"
                        event_text = live_info.get("event", "")
                        event_html = f"<div style='margin-bottom:6px; font-size:11px; color:#10B981; font-weight:900;'>{event_text}</div>" if event_text else ""
                        time_display = f"{event_html}<span class='live-score'>{score_text}</span><span class='deadline-closed' style='background:rgba(239, 68, 68, 0.1); border-color:#EF4444; color:#EF4444;'>🔴 LIVE</span>"
                    else:
                        time_display = f"<span class='live-score'>0:0</span><span class='deadline-closed' style='background:rgba(239, 68, 68, 0.1); border-color:#EF4444; color:#EF4444;'>🔴 LIVE</span>"
                else:
                    badge = f"<span class='deadline-closed'>픽 마감</span>" if is_closed else f"<span class='deadline-open'>{raw_deadline}</span>"
                    time_display = f"<span class='match-time-text'>{item.get('final_match_time', '')}</span>{badge}"
                
                dynamic_pred_boxes = generate_pred_boxes(item.get('ev_sorted_picks', []), is_top3_tab=False)

                upset_html = ""
                if item.get('upset_warning'):
                    upset_html = f"<div style='background-color: #3b1c1c; border-left: 4px solid #ff4d4d; padding: 12px 15px; font-size: 13px; color: #ffcccc; border-radius: 4px; margin-bottom: 15px; line-height: 1.6;'><span style='background-color: #FFD700; color: #000; font-weight: 900; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin-right: 5px;'>VIP 전용</span>🚨 <b>슈퍼 역배 주의보 포착!</b><br>{item.get('upset_reason', '역배 전조 증상이 포착되었습니다. 고배당 스나이핑 찬스!')}</div>"
                
                html_code = (
                    f"<div class='match-card'>"
                    f"<div class='league-title'>{m.get('league','축구')}</div>"
                    f"<div class='vs-row'>"
                    f"<div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{item.get('home_form','')}</div>{item.get('h_rank_html','')}{item.get('h_inj_html','')}{item.get('h_rest_html','')}</div>{logo_h_tag}</div>"
                    f"<div class='center-time-box'>{time_display}</div>"
                    f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{item.get('away_form','')}</div>{item.get('a_rank_html','')}{item.get('a_inj_html','')}{item.get('a_rest_html','')}</div></div>"
                    f"</div>"
                    f"<div class='ai-story'>{item.get('story','')}</div>"
                    f"{upset_html}"
                    f"<div class='odd-bar'>"
                    f"<span class='odd-item'>승 <span class='odd-val'>{m.get('odd_h','-')}</span> | 무 <span class='odd-val'>{m.get('odd_d','-')}</span> | 패 <span class='odd-val'>{m.get('odd_a','-')}</span></span>"
                    f"<span class='odd-item'>핸디캡 <span class='odd-val'>{m.get('handi_h', '-')} / {m.get('handi_d', '-')} / {m.get('handi_a', '-')}</span></span>"
                    f"<span class='odd-item'>언오버 <span class='odd-val'>{m.get('uo_under', '-')} / {m.get('uo_over', '-')}</span></span>"
                    f"</div>"
                    f"<div class='pred-grid'>{dynamic_pred_boxes}</div>"
                    f"</div>"
                )
                st.markdown(html_code, unsafe_allow_html=True)
            if displayed_count == 0: st.info("조건에 맞는 경기가 없거나 모두 종료되었습니다.")
        else: st.info("현재 분석 중입니다. 백그라운드 데이터 수집이 완료되면 화면이 표시됩니다.")
    with sub_baseball: st.info("야구 분석 데이터 준비 중입니다.")
    with sub_basketball: st.info("농구 분석 데이터 준비 중입니다.")

# -----------------------------------------------------------------------------
# [TAB 2] 승무패 14경기
# -----------------------------------------------------------------------------
with main_tab2:
    st.markdown("<p style='color:#64748B; font-weight:700; margin-bottom:20px;'>승무패 14폴더 AI 확률 분포 (복수 마킹 참고용)</p>", unsafe_allow_html=True)
    toto14_list = dashboard_data.get("toto14", [])
    
    if toto14_list:
        total_combinations = dashboard_data.get("toto14_meta", {}).get("total_combinations", 1)
        single_pick_count = dashboard_data.get("toto14_meta", {}).get("single_pick_count", 0)
        double_pick_count = dashboard_data.get("toto14_meta", {}).get("double_pick_count", 0)
        total_price = dashboard_data.get("toto14_meta", {}).get("budget", total_combinations * 1000)
        
        summary_html = f"<div style='background: #111827; border: 1px solid #1E293B; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 24px;'><span style='color: #94A3B8; font-size: 14px; font-weight: 700; display: block; margin-bottom: 5px;'>AI 승무패 14경기 풀-스탯 분석 결과</span><span style='color: #F8FAFC; font-size: 16px; font-weight: 700; display: block; margin-bottom: 8px;'>단통 <span style='color:#10B981;'>{single_pick_count}</span>경기 + 투마킹 <span style='color:#EF4444;'>{double_pick_count}</span>경기</span><span style='color: #F8FAFC; font-size: 24px; font-weight: 900; display: block;'>최종 <span style='color: #00F2FE;'>{total_combinations}</span> 조합 / 예상 구매 금액: <span style='color: #10B981;'>{total_price:,}</span> 원</span></div>"
        st.markdown(summary_html, unsafe_allow_html=True)

        toto_displayed = 0
        toto_paywall_shown = False

        for idx, item in enumerate(toto14_list, 1):
            toto_displayed += 1

            if toto_displayed > 3 and not st.session_state['is_vip']:
                if not toto_paywall_shown:
                    st.markdown("""
                    <div class='match-card' style='text-align: center; padding: 50px 20px; background: rgba(15, 23, 42, 0.6); backdrop-filter: blur(8px); border: 1px solid #F59E0B;'>
                        <h2 style='color: #F59E0B; font-weight: 900; letter-spacing: 1px;'>🔒 승무패 14경기 전체 보기 잠금</h2>
                        <p style='color: #94A3B8; font-weight: 700; font-size: 16px; margin-top: 15px;'>4번째 경기부터의 마킹 전략은 VIP 회원에게만 공개됩니다.</p>
                        <p style='color: #38BDF8; font-size: 14px; margin-top: 25px; background: rgba(56,189,248,0.1); display: inline-block; padding: 8px 15px; border-radius: 8px;'>👉 좌측 사이드바에서 VIP 승급을 진행해주세요.</p>
                    </div>
                    """, unsafe_allow_html=True)
                    toto_paywall_shown = True
                continue

            m = item['match']
            logo_h_tag = render_logo_html(item.get("home_logo"))
            logo_a_tag = render_logo_html(item.get("away_logo"))
            
            match_id_str = f"TOTO14_{m['id']}"
            toto_match_time = m.get("match_time", "시간 미정")
            live_score_html = f"<span class='match-time-text'>{toto_match_time}</span><b style='color:#475569; font-size:16px;'>VS</b>"
            live_info = live_scores_data.get(match_id_str, {})
            if live_info.get("is_live") is True:
                score_text = live_info.get("score", "0:0")
                if not score_text or score_text == "-": score_text = "0:0"
                if score_text: live_score_html = f"<div style='color:#00F2FE; font-weight:900; font-size:18px;'>{score_text}</div><div style='color:#EF4444; font-size:10px; font-weight:900;'>LIVE</div>"

            html_code = (
                f"<div class='match-card' style='padding: 24px;'>"
                f"<div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;'><span class='badge-primary'>제 {idx} 경기</span><span style='color:#94A3B8; font-size:14px; font-weight:700;'>AI 추천 마킹: <b style='color:#00F2FE;'>{item.get('best_pick_display', '')}</b></span></div>"
                f"<div class='vs-row' style='margin-bottom:15px;'><div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{item.get('home_form','')}</div>{item.get('h_rank_html','')}{item.get('h_inj_html','')}</div>{logo_h_tag}</div>"
                f"<div class='center-time-box' style='width:80px;'>{live_score_html}</div>"
                f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{item.get('away_form','')}</div>{item.get('a_rank_html','')}{item.get('a_inj_html','')}</div></div></div>"
                f"<div style='font-size:12px; color:#64748B; font-weight:700; text-align:center;'>확률 분포: 승 {item.get('p_h')}% | 무 {item.get('p_d')}% | 패 {item.get('p_a')}%</div>"
                f"<div class='prob-bar-container' style='margin-bottom: 15px;'><div class='prob-bar-win' style='width: {item.get('p_h')}%;'></div><div class='prob-bar-draw' style='width: {item.get('p_d')}%;'></div><div class='prob-bar-lose' style='width: {item.get('p_a')}%;'></div></div>"
                f"<div style='display: flex; gap: 10px;'>{item.get('picks_html', '')}</div>"
                f"</div>"
            )
            st.markdown(html_code, unsafe_allow_html=True)
    else: st.info("현재 진행 중인 승무패 14경기 데이터가 없습니다.")

# -----------------------------------------------------------------------------
# [TAB 3] 오늘의 TOP 3
# -----------------------------------------------------------------------------
with main_tab3:
    top3_list = dashboard_data.get("top3", [])
    if top3_list:
        displayed_top3 = 0
        for idx, item in enumerate(top3_list, 1):
            if item.get("final_match_time", "") == "시간 미정" or item.get("match", {}).get("match_time", "") == "시간 미정":
                continue
                
            displayed_top3 += 1
            m = item['match']
            logo_h_tag = render_logo_html(item.get("home_logo"))
            logo_a_tag = render_logo_html(item.get("away_logo"))
            dynamic_top3_boxes = generate_pred_boxes(item.get('ev_sorted_picks', []), is_top3_tab=True)
            html_code = (
                f"<div class='match-card top3-glow'>"
                f"<div class='league-title' style='color:#00F2FE;'># {displayed_top3} 최고 가치 추천 픽 • {m.get('league','')}</div>"
                f"<div class='vs-row'><div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{item.get('home_form','')}</div>{item.get('h_rank_html','')}</div>{logo_h_tag}</div>"
                f"<div class='center-time-box'><span class='match-time-text' style='color:#00F2FE;'>{item.get('final_match_time', '')}</span></div>"
                f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{item.get('away_form','')}</div>{item.get('a_rank_html','')}</div></div></div>"
                f"<div class='pred-grid' style='margin-top:20px;'>{dynamic_top3_boxes}</div>"
                f"</div>"
            )
            st.markdown(html_code, unsafe_allow_html=True)
        if displayed_top3 == 0: st.info("현재 배팅 가능한 추천 분석 경기가 없습니다.")
    else: st.info("현재 배팅 가능한 분석 경기가 없습니다.")

# -----------------------------------------------------------------------------
# [TAB 4] 🔥 AI 리포트
# -----------------------------------------------------------------------------
with main_tab4:
    def get_v3_accuracy_stats():
        try:
            conn = sqlite3.connect("ai_predictions.db")
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(predictions)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'ev_pick' not in columns:
                conn.close()
                return None
                
            df_finished = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED' ORDER BY match_time DESC", conn)
            df_proto = df_finished[df_finished['is_toto14'] == 0]
            df_toto = df_finished[df_finished['is_toto14'] == 1]
            
            df_pending = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'PENDING' AND is_toto14 = 0 ORDER BY match_time DESC", conn)
            conn.close()
            
            proto_total = len(df_proto)
            proto_prob_hit = int(df_proto['is_correct_prob'].sum()) if proto_total > 0 else 0
            proto_ev_hit = int(df_proto['is_correct_ev'].sum()) if proto_total > 0 else 0
            
            proto_prob_acc = round((proto_prob_hit / proto_total) * 100, 1) if proto_total > 0 else 0.0
            proto_ev_acc = round((proto_ev_hit / proto_total) * 100, 1) if proto_total > 0 else 0.0

            toto_total = len(df_toto)
            toto_hit = int(df_toto['is_correct_prob'].sum()) if toto_total > 0 else 0
            toto_acc = round((toto_hit / toto_total) * 100, 1) if toto_total > 0 else 0.0
            
            return {
                "proto": {"total": proto_total, "prob_hit": proto_prob_hit, "ev_hit": proto_ev_hit, "prob_acc": proto_prob_acc, "ev_acc": proto_ev_acc},
                "toto": {"total": toto_total, "hit": toto_hit, "acc": toto_acc},
                "history": df_proto.head(50).to_dict('records'),
                "pending": df_pending.to_dict('records')
            }
        except Exception as e:
            return None

    stats = get_v3_accuracy_stats()
    
    if stats is None:
        st.warning("⚠️ 백그라운드 로봇이 V3 듀얼 엔진으로 업그레이드 중입니다. 잠시 후 다시 확인해주세요!")
    else:
        p_stats = stats['proto']
        t_stats = stats['toto']
        
        st.markdown(f"""
        <div style='display:flex; gap:20px; margin-bottom:30px;'>
            <div style='flex:1; background:#0B0F19; padding:20px; border-radius:12px; border:1px solid #1E293B;'>
                <span style='color:#94A3B8; font-size:14px; font-weight:900; display:block; margin-bottom:15px;'>📊 라이브 승부식 A/B 채점 (총 {p_stats['total']}경기)</span>
                <div style='display:flex; justify-content:space-between; align-items:center;'>
                    <div style='text-align:center;'>
                        <span style='color:#CBD5E1; font-size:12px; display:block;'>안전제일 확률픽</span>
                        <span style='color:#10B981; font-size:32px; font-weight:900;'>{p_stats['prob_acc']}%</span>
                        <span style='color:#64748B; font-size:12px; display:block;'>({p_stats['prob_hit']}건 적중)</span>
                    </div>
                    <div style='color:#334155; font-size:24px; font-weight:100;'>VS</div>
                    <div style='text-align:center;'>
                        <span style='color:#CBD5E1; font-size:12px; display:block;'>역배수익 꿀픽</span>
                        <span style='color:#F59E0B; font-size:32px; font-weight:900;'>{p_stats['ev_acc']}%</span>
                        <span style='color:#64748B; font-size:12px; display:block;'>({p_stats['ev_hit']}건 적중)</span>
                    </div>
                </div>
            </div>
            <div style='flex:1; background:#0B0F19; padding:20px; border-radius:12px; border:1px solid #1E293B; display:flex; flex-direction:column; justify-content:center; align-items:center;'>
                <span style='color:#94A3B8; font-size:14px; font-weight:900; display:block; margin-bottom:5px;'>🏆 승무패 14경기 단통 적중률</span>
                <span style='color:#00F2FE; font-size:40px; font-weight:900;'>{t_stats['acc']}%</span>
                <span style='color:#64748B; font-size:13px;'>총 {t_stats['total']}경기 중 {t_stats['hit']}경기 적중</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown("<h4 style='color:#F8FAFC; font-weight:900; margin-top:10px; margin-bottom:20px;'>📜 딥러닝 리얼 오답노트 피드</h4>", unsafe_allow_html=True)
        
        history_data = stats['history']
        if not history_data:
            st.info("아직 채점이 완료된 종료 경기가 없습니다. 로봇이 V3 모드로 열심히 경기 결과를 감시 중입니다!")
        
        for row in history_data:
            h_team = row.get('home_team', '')
            a_team = row.get('away_team', '')
            m_time = row.get('match_time', '')
            score = row.get('actual_score', '-:-')
            note = row.get('ai_note', '')
            
            prob_pick = row.get('prob_pick', '')
            ev_pick = row.get('ev_pick', '')
            prob_ok = row.get('is_correct_prob', 0) == 1
            ev_ok = row.get('is_correct_ev', 0) == 1
            
            if row.get('actual_result') == 'CANCELED':
                score_html = "<span class='report-score' style='color:#94A3B8 !important;'>취소/무효</span>"
                note_style = "real-ai-note"
            else:
                score_html = f"<span class='report-score'>{score}</span>"
                note_style = "real-ai-note" if (prob_ok or ev_ok) else "real-ai-note-fail"

            prob_badge = "<span style='background:#10B981; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>적중</span>" if prob_ok else "<span style='background:#EF4444; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>실패</span>"
            ev_badge = "<span style='background:#10B981; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>적중</span>" if ev_ok else "<span style='background:#EF4444; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>실패</span>"

            html = f"""
            <div class='report-card'>
                <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px; border-bottom:1px solid #1E293B; padding-bottom:15px;'>
                    <div>
                        <span style='color:#64748B; font-size:12px; display:block; margin-bottom:4px;'>{m_time} • {row.get('league','')}</span>
                        <span class='report-team'>{h_team} <span style='color:#475569;'>VS</span> {a_team}</span>
                    </div>
                    <div style='text-align:right;'>
                        <span style='color:#94A3B8; font-size:11px; display:block; margin-bottom:4px;'>최종 결과</span>
                        {score_html}
                    </div>
                </div>
                <div style='display:flex; gap:15px; margin-bottom:10px;'>
                    <div style='flex:1; background:#06080F; padding:10px; border-radius:6px; border:1px solid #1E293B;'>
                        <span style='color:#94A3B8; font-size:11px; display:block; margin-bottom:4px;'>🎯 확률픽 예측</span>
                        <div style='font-size:14px; font-weight:900; color:#F8FAFC;'>{prob_badge} {prob_pick}</div>
                    </div>
                    <div style='flex:1; background:#06080F; padding:10px; border-radius:6px; border:1px solid #1E293B;'>
                        <span style='color:#94A3B8; font-size:11px; display:block; margin-bottom:4px;'>🍯 꿀픽 예측</span>
                        <div style='font-size:14px; font-weight:900; color:#F8FAFC;'>{ev_badge} {ev_pick}</div>
                    </div>
                </div>
                <div class='{note_style}'>{note}</div>
            </div>
            """
            st.markdown(html, unsafe_allow_html=True)
            
        pending_data = stats['pending']
        if pending_data:
            st.markdown("<h4 style='color:#64748B; font-weight:900; margin-top:40px; margin-bottom:15px;'>⏳ 향후 경기 일정 및 분석 현황</h4>", unsafe_allow_html=True)
            
            def parse_time_for_ui(t_str):
                now = datetime.now(timezone(timedelta(hours=9)))
                if not t_str or t_str in ["시간 미정", "마감/진행중"]: return now - timedelta(hours=3)
                try:
                    m = re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})', t_str)
                    if m:
                        mo, d, h, mn = map(int, m.groups())
                        yr = now.year if mo <= now.month + 1 else now.year - 1
                        return datetime(yr, mo, d, h, mn, tzinfo=timezone(timedelta(hours=9)))
                except: pass
                return now - timedelta(hours=3)

            displayed_pending = 0
            for row in pending_data:
                m_time_str = row.get('match_time', '')
                
                if m_time_str == "시간 미정":
                    continue
                    
                displayed_pending += 1
                m_id_str = str(row['match_id'])
                temp_score = row.get('actual_score', '-:-')
                
                m_dt = parse_time_for_ui(m_time_str)
                now = datetime.now(timezone(timedelta(hours=9)))
                
                if now < m_dt:
                    badge_html = "<span style='font-size:12px; font-weight:900; background:rgba(56,189,248,0.15); color:#38BDF8; border:1px solid #38BDF8; padding:4px 10px; border-radius:6px;'>진행 예정</span>"
                elif now < m_dt + timedelta(hours=2.5):
                    badge_html = "<span style='font-size:12px; font-weight:900; background:rgba(16,185,129,0.15); color:#10B981; border:1px solid #10B981; padding:4px 10px; border-radius:6px;'>경기 진행중</span>"
                else:
                    badge_html = "<span style='font-size:12px; font-weight:900; background:rgba(245,158,11,0.15); color:#F59E0B; border:1px solid #F59E0B; padding:4px 10px; border-radius:6px;'>채점 로봇 분석중</span>"

                event_html = ""
                if m_id_str in live_scores_data:
                    live_info = live_scores_data[m_id_str]
                    if live_info.get("score"):
                        temp_score = live_info["score"].replace(" : ", ":")
                    if live_info.get("event"):
                        event_html = f"<div style='font-size:11px; color:#10B981; font-weight:900; margin-top:4px;'>{live_info['event']}</div>"
                    badge_html = "<span style='font-size:12px; font-weight:900; background:rgba(239,68,68,0.15); color:#EF4444; border:1px solid #EF4444; padding:4px 10px; border-radius:6px;'>🔴 LIVE</span>"
                
                html_str = f"<div style='background:#0B0F19; border:1px solid #1E293B; border-radius:10px; padding:16px 20px; margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;'><div><div style='color:#64748B; font-size:12px; margin-bottom:4px; font-weight:900;'>{m_time_str}</div><div style='color:#F8FAFC; font-size:16px; font-weight:900;'>{row.get('home_team')} <span style='color:#64748B;'>VS</span> {row.get('away_team')}</div>{event_html}</div><div style='display:flex; align-items:center; gap:15px;'><span style='color:#00F2FE; font-size:24px; font-weight:900; letter-spacing:1px;'>{temp_score}</span>{badge_html}</div></div>"
                st.markdown(html_str, unsafe_allow_html=True)
                
            if displayed_pending == 0:
                st.info("현재 대기 중인 향후 경기 일정이 없습니다.")
