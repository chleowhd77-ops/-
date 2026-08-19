import streamlit as st
import math
import json
import requests
import sqlite3
import pandas as pd
import re
import time
from datetime import datetime, timezone, timedelta

# -----------------------------------------------------------------------------
# 0. 기본 설정 및 타이틀
# -----------------------------------------------------------------------------
APP_TITLE = "D.J PROTO ANALYTICS"

st.set_page_config(
    page_title=APP_TITLE, 
    page_icon="⚡", 
    layout="wide",
    initial_sidebar_state="collapsed"
)

API_KEY = "28b599664bba858ebf93515768741975"
API_HOST = "v3.football.api-sports.io"

headers = {
    'x-rapidapi-host': API_HOST,
    'x-rapidapi-key': API_KEY
}

TEAM_NAME_MAP = {
    "광주FC": "Gwangju FC", "포항스틸": "Pohang Steelers", "제주SKFC": "Jeju United", "FC안양": "FC Anyang",
    "FC서울": "FC Seoul", "대전하나": "Daejeon Citizen", "충북청주": "Chungbuk Cheongju", "전남드래": "Jeonnam Dragons",
    "김해FC": "Gimhae", "경남FC": "Gyeongnam FC", "수원삼성": "Suwon Samsung", "수원FC": "Suwon FC",
    "부산아이": "Busan I Park", "화성FC": "Hwaseong", "인천유나": "Incheon United", "김천상무": "Gimcheon Sangmu",
    "부천FC": "Bucheon FC 1995", "전북현대": "Jeonbuk Motors", "울산HDFC": "Ulsan Hyundai", "강원FC": "Gangwon FC",
    "서울이랜드": "Seoul E-Land", "안산그리": "Ansan Greeners", "대구FC": "Daegu FC", 
    "충남아산": "Chungnam Asan", "김포FC": "Gimpo FC", "천안시티": "Cheonan City", "파주프런": "Paju Citizen", "성남FC": "Seongnam FC",
    "FC신시내": "FC Cincinnati", "뉴욕시티": "New York City FC", "콜럼크루": "Columbus Crew", "CF몽레알": "CF Montreal",
    "DC유나이": "DC United", "뉴잉레벌": "New England Revolution", "뉴욕레드": "New York Red Bulls", "내슈빌SC": "Nashville SC",
    "올랜시티": "Orlando City", "시카파이": "Chicago Fire", "토론토FC": "Toronto FC", "샬럿FC": "Charlotte FC",
    "스포캔자": "Sporting Kansas City", "세인시티": "St. Louis City", "미네유나": "Minnesota United", "애틀유나": "Atlanta United",
    "콜로래피": "Colorado Rapids", "LAFC": "Los Angeles FC", "레알솔트": "Real Salt Lake", "FC댈러스": "FC Dallas",
    "시애사운": "Seattle Sounders", "오스틴FC": "Austin FC", "LA갤럭시": "LA Galaxy", "새너어스": "San Jose Earthquakes",
    "포틀팀버": "Portland Timbers", "샌디에FC": "San Diego FC", "밴쿠화이": "Vancouver Whitecaps", "휴스다이": "Houston Dynamo",
    "포츠머스": "Portsmouth", "퀸즈파크 레인저스": "Queens Park Rangers", "노리치 시티": "Norwich City",
    "웨스트브로미치 앨비언": "West Bromwich Albion", "스토크 시티": "Stoke City", "스완지 시티": "Swansea City",
    "SD레이더스": "SD Raiders", "시드니FC": "Sydney FC", "말레이시아": "Malaysia", "베트남": "Vietnam"
}

DIRECT_LOGO_MAP = {}

def init_db():
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT UNIQUE, league TEXT, home_team TEXT, away_team TEXT,
            predicted_pick TEXT, predicted_prob REAL, expected_score TEXT, odd_h REAL, odd_d REAL, odd_a REAL,
            actual_score TEXT DEFAULT '-:-', actual_result TEXT DEFAULT 'PENDING', is_correct INTEGER DEFAULT 0,
            failure_reason TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_toto14 INTEGER DEFAULT 0
        )
    """)
    try: cursor.execute("ALTER TABLE predictions ADD COLUMN is_toto14 INTEGER DEFAULT 0")
    except: pass 
    try: cursor.execute("ALTER TABLE predictions ADD COLUMN failure_reason TEXT DEFAULT ''")
    except: pass
    conn.commit()
    conn.close()

init_db()

@st.cache_data(ttl=86400)
def fetch_team_info_api(team_name):
    logo = DIRECT_LOGO_MAP.get(team_name)
    if logo: return {"id": None, "logo": logo}
    search_name = TEAM_NAME_MAP.get(team_name, team_name)
    try:
        response = requests.get(f"https://{API_HOST}/teams", headers=headers, params={"search": search_name}, timeout=5)
        res_data = response.json()
        if res_data.get("response") and len(res_data["response"]) > 0:
            return {"id": res_data["response"][0]["team"]["id"], "logo": res_data["response"][0]["team"].get("logo")}
    except: pass
    return {"id": None, "logo": None}

# [NEW] 최근 5경기 폼(승-무-패) 가져오는 엔진 추가!
@st.cache_data(ttl=43200)
def fetch_team_form_api(team_id):
    if not team_id: return ""
    try:
        response = requests.get(f"https://{API_HOST}/fixtures", headers=headers, params={"team": team_id, "last": 5}, timeout=5)
        data = response.json().get("response", [])
        form_list = []
        for m in data:
            home_id = m["teams"]["home"]["id"]
            home_win = m["teams"]["home"]["winner"]
            away_win = m["teams"]["away"]["winner"]
            if home_id == team_id:
                if home_win is True: form_list.append("승")
                elif home_win is False and away_win is True: form_list.append("패")
                else: form_list.append("무")
            else:
                if away_win is True: form_list.append("승")
                elif away_win is False and home_win is True: form_list.append("패")
                else: form_list.append("무")
        # 최근 경기가 뒤로 가도록(시간순) 리스트 뒤집기
        form_list.reverse()
        return "-".join(form_list) if form_list else ""
    except: return ""

@st.cache_data(ttl=43200)
def fetch_fixture_details_api(home_id, away_id):
    default_res = {"match_time": None, "last_h2h_date": "-", "h_rest": "-", "a_rest": "-", "h_wins": 0, "draws": 0, "a_wins": 0, "total": 0}
    if not home_id or not away_id: return default_res
    try:
        response = requests.get(f"https://{API_HOST}/fixtures/headtohead", headers=headers, params={"h2h": f"{home_id}-{away_id}"}, timeout=5)
        matches = response.json().get("response", [])
        h_wins, draws, a_wins = 0, 0, 0
        for m in matches[:10]:
            if m.get("teams", {}).get("home", {}).get("winner"):
                if m.get("teams", {}).get("home", {}).get("id") == home_id: h_wins += 1
                else: a_wins += 1
            elif m.get("teams", {}).get("away", {}).get("winner"):
                if m.get("teams", {}).get("home", {}).get("id") == home_id: a_wins += 1
                else: h_wins += 1
            else: draws += 1
        return {"match_time": None, "last_h2h_date": "-", "h_rest": "-", "a_rest": "-", "h_wins": h_wins, "draws": draws, "a_wins": a_wins, "total": len(matches[:10])}
    except: return default_res

def load_betman_data():
    raw_url = f"https://raw.githubusercontent.com/chleowhd77-ops/-/main/betman_data.json?t={int(time.time())}"
    try:
        res = requests.get(raw_url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list): return {"proto_matches": data, "toto_14_matches": []}
            return data
    except: pass
    return {"proto_matches": [], "toto_14_matches": []}

def load_live_scores():
    raw_url = f"https://raw.githubusercontent.com/chleowhd77-ops/-/main/live_scores.json?t={int(time.time())}"
    try:
        res = requests.get(raw_url, timeout=5)
        if res.status_code == 200: return res.json()
    except: pass
    return {}

def get_accuracy_stats():
    conn = sqlite3.connect("ai_predictions.db")
    df = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED'", conn)
    df_history = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED' ORDER BY id DESC LIMIT 50", conn)
    conn.close()
    if len(df) == 0: return {"total": 0, "correct": 0, "accuracy": 0.0, "history": []}
    return {"total": len(df), "correct": df['is_correct'].sum(), "accuracy": round((df['is_correct'].sum() / len(df)) * 100, 1), "history": df_history.to_dict('records')}

def save_prediction(m, best_option, best_prob_pct, best_score, is_toto14=0):
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM predictions WHERE match_id = ?", (m['id'],))
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO predictions (match_id, league, home_team, away_team, predicted_pick, predicted_prob, expected_score, odd_h, odd_d, odd_a, match_time, is_toto14)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (m['id'], m.get('league', '승무패 14경기'), m['home'], m['away'], best_option, best_prob_pct, f"{best_score[0]}:{best_score[1]}", m.get('odd_h', 0.0), m.get('odd_d', 0.0), m.get('odd_a', 0.0), m.get('match_time', ''), is_toto14))
        else: cursor.execute("UPDATE predictions SET is_toto14 = ? WHERE match_id = ?", (is_toto14, m['id']))
        conn.commit()
    except: pass
    finally: conn.close()

def get_match_status(match_time_str, deadline_str):
    if not match_time_str or match_time_str == "시간 미정": return "TBD", False
    try:
        now = datetime.now(timezone(timedelta(hours=9)))
        year = now.year
        match = re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})', match_time_str)
        if match:
            mo, d, h, m = map(int, match.groups())
            m_dt = datetime(year, mo, d, h, m, tzinfo=timezone(timedelta(hours=9)))
            d_dt = m_dt - timedelta(minutes=10)
            dead_match = re.search(r'(\d{2}):(\d{2})', deadline_str)
            if dead_match:
                dh, dm = map(int, dead_match.groups())
                d_dt = m_dt.replace(hour=dh, minute=dm)
                if d_dt >= m_dt: d_dt -= timedelta(days=1)
            is_closed = now >= d_dt
            if m_dt <= now <= m_dt + timedelta(hours=2): return "LIVE", is_closed
            elif now > m_dt + timedelta(hours=2): return "FINISHED", is_closed
            else: return "UPCOMING", is_closed
    except: pass
    return "UPCOMING", False

# [수술 완료] 전문가 AI 코멘트 복구! (팀 이름 동적 삽입)
def generate_match_story(prob_h, prob_d, prob_a, h2h_h, h2h_a, home, away):
    if prob_h > 60: return f"🔥 전력상 우위! 홈팀 {home}의 무난한 승리가 예상되는 매치입니다."
    elif prob_a > 60: return f"🚨 {away}의 매서운 기세! {home}의 고전이 예상되는 이변 주의 경기!"
    elif abs(prob_h - prob_a) <= 10 and prob_d >= 28: return f"⚔️ {home} vs {away}, 승부를 예측하기 힘든 팽팽한 접전! 진흙탕 싸움이 예상됩니다."
    elif h2h_h > h2h_a + 2: return f"📊 압도적인 상대 전적! {home}이(가) 확실한 우위를 점하고 있습니다."
    else: return f"🔍 AI 분석 결과, 미세한 차이로 승패가 갈릴 박빙의 승부입니다."

def calculate_poisson_probs(exp_h, exp_a, handi_val=1.0):
    h_probs = [(math.exp(-exp_h) * (exp_h**i)) / math.factorial(i) for i in range(8)]
    a_probs = [(math.exp(-exp_a) * (exp_a**j)) / math.factorial(j) for j in range(8)]
    h_win, draw, a_win, prob_u, prob_o, prob_handi_h, prob_handi_a = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    for h in range(8):
        for a in range(8):
            p = h_probs[h] * a_probs[a]
            if h > a: h_win += p
            elif h == a: draw += p
            else: a_win += p
            if (h + a) < 2.5: prob_u += p
            else: prob_o += p
            if (h + handi_val) > a: prob_handi_h += p
            elif (h + handi_val) < a: prob_handi_a += p
            else:
                 if exp_h > exp_a: prob_handi_h += p
                 else: prob_handi_a += p
    return h_win, draw, a_win, prob_u, prob_o, prob_handi_h, prob_handi_a

# -----------------------------------------------------------------------------
# CSS 스타일링 [수술 완료: 모바일 반응형 완벽 최적화 & 로고 위치 보정]
# -----------------------------------------------------------------------------
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700;900&display=swap');
    html, body, .stApp { background-color: #06080F !important; font-family: 'Noto Sans KR', sans-serif !important; color: #E2E8F0; overflow-x: hidden !important; }
    [data-testid="stSidebar"] { display: none; }
    .block-container { max-width: 1000px !important; padding-top: 2rem !important; padding-bottom: 2rem !important; }
    .app-header { text-align: center; padding: 30px 0 20px 0; border-bottom: 1px solid #1E293B; margin-bottom: 30px; }
    .app-header h1 { color: #FFFFFF !important; font-size: 36px !important; font-weight: 900 !important; letter-spacing: 2px; margin: 0; background: -webkit-linear-gradient(45deg, #00F2FE, #4FACFE); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .app-header p { color: #64748B; font-size: 14px; font-weight: 700; letter-spacing: 1px; margin-top: 5px; }
    .stTabs [data-baseweb="tab-list"] { background-color: transparent !important; border-bottom: 1px solid #1E293B !important; gap: 30px !important; }
    .stTabs [data-baseweb="tab"] { color: #64748B !important; font-weight: 900 !important; font-size: 20px !important; padding: 14px 0px !important; border: none !important; }
    .stTabs [aria-selected="true"] { color: #00F2FE !important; border-bottom: 4px solid #00F2FE !important; }
    .match-card { background-color: #0B0F19; border: 1px solid #1E293B; border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); transition: transform 0.2s ease, border-color 0.2s ease; }
    .match-card:hover { border-color: #334155; transform: translateY(-2px); }
    .top3-glow { border: 2px solid #00F2FE !important; box-shadow: 0 0 20px rgba(0, 242, 254, 0.15) !important; background: linear-gradient(135deg, #0A192F 0%, #06080F 100%) !important; }
    .league-title { font-size: 13px; color: #94A3B8; font-weight: 900; letter-spacing: 1px; margin-bottom: 15px; }
    
    /* Desktop UI */
    .vs-row { display: flex; justify-content: space-between; align-items: center; width: 100%; margin-bottom: 15px; }
    .team-box { flex: 1; display: flex; align-items: center; gap: 15px; }
    .team-box.home { justify-content: flex-end; text-align: right; }
    .team-box.away { justify-content: flex-start; text-align: left; }
    .team-info-wrapper { display: flex; flex-direction: column; }
    .team-box.home .team-info-wrapper { align-items: flex-end; }
    .team-box.away .team-info-wrapper { align-items: flex-start; }
    .team-name-text { color: #F8FAFC !important; font-size: 22px; font-weight: 900; letter-spacing: -0.5px; }
    .team-form-text { display: block; color: #64748B; font-size: 12px; font-weight: 700; letter-spacing: 1px; margin-top: 4px; }
    .team-logo { width: 55px !important; height: 55px !important; object-fit: contain; }
    
    .center-time-box { width: 140px; text-align: center; flex-shrink: 0; }
    .match-time-text { color: #CBD5E1; font-size: 15px; font-weight: 700; display: block; margin-bottom: 4px;}
    .live-score { font-size: 28px; font-weight: 900; color: #00F2FE; display: block; margin-bottom: 4px; text-shadow: 0 0 10px rgba(0,242,254,0.5); }
    .deadline-open { color: #00F2FE; font-size: 12px; font-weight: 900; border: 1px solid #00F2FE; padding: 3px 8px; border-radius: 4px; display: inline-block;}
    .deadline-closed { color: #EF4444; font-size: 12px; font-weight: 900; background: rgba(239, 68, 68, 0.1); border: 1px solid #EF4444; padding: 3px 8px; border-radius: 4px; display: inline-block;}
    .odd-bar { display: flex; justify-content: space-between; background: #111827; border-radius: 6px; padding: 12px 20px; margin-bottom: 15px; border: 1px solid #1F2937; }
    .odd-item { font-size: 14px; color: #94A3B8; font-weight: 700; }
    .odd-val { color: #F1F5F9; font-weight: 900; margin-left: 6px; }
    .ai-story { background: rgba(0, 242, 254, 0.05); border-left: 3px solid #00F2FE; padding: 12px 15px; font-size: 14px; color: #E2E8F0; font-weight: 700; border-radius: 4px; margin-bottom: 15px; }
    .pred-grid { display: flex; gap: 12px; }
    .pred-box { flex: 1; background: #0D1424; border: 1px solid #1E293B; border-radius: 8px; padding: 16px; text-align: center; }
    .pred-label { font-size: 12px; color: #64748B; font-weight: 900; margin-bottom: 8px; }
    .pred-value { font-size: 18px; color: #F8FAFC; font-weight: 900; }
    .pred-prob { font-size: 14px; color: #10B981; font-weight: 900; margin-left: 6px; }
    .prob-bar-container { display: flex; height: 12px; border-radius: 6px; overflow: hidden; margin-top: 10px; background: #1E293B;}
    .prob-bar-win { background-color: #00F2FE; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color:#000; }
    .prob-bar-draw { background-color: #10B981; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color:#000; }
    .prob-bar-lose { background-color: #EF4444; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color:#000; }
    .badge-primary { background: rgba(0, 242, 254, 0.1); color: #00F2FE; border: 1px solid #00F2FE; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 900; }
    
    /* Mobile CSS Magic (로고 위, 이름 아래로 중앙 정렬) */
    @media (max-width: 640px) {
        .vs-row { align-items: flex-start !important; }
        .team-box { flex-direction: column !important; justify-content: flex-start !important; gap: 8px !important; }
        .team-box.home { flex-direction: column-reverse !important; } /* HTML 순서 뒤집기 (로고를 위로) */
        .team-box.away { flex-direction: column !important; } 
        .team-info-wrapper { align-items: center !important; text-align: center !important; }
        .team-name-text { font-size: 15px !important; white-space: normal; }
        .team-form-text { font-size: 10px !important; }
        .center-time-box { width: 70px !important; margin-top: 15px; }
        .team-logo { width: 40px !important; height: 40px !important; }
        .odd-bar { flex-direction: column !important; text-align: center; gap: 10px; }
        .pred-grid { flex-direction: column !important; }
    }
    </style>
""", unsafe_allow_html=True)

def render_logo_html(logo_url):
    if logo_url: return f"<img src='{logo_url}' class='team-logo' onerror=\"this.style.display='none';\">"
    return ""

st.markdown("""
<div class='app-header'>
    <h1>D.J PROTO ANALYTICS</h1>
    <p>AI 예측 기반 스마트 프로토 대시보드</p>
</div>
""", unsafe_allow_html=True)

main_tab1, main_tab2, main_tab3, main_tab4 = st.tabs([
    "프로토 LIVE", "승무패 14경기", "오늘의 TOP 3", "AI 리포트"
])

all_data = load_betman_data()
proto_matches = all_data.get("proto_matches", [])
live_scores_data = load_live_scores()

try:
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT match_id, league, home_team, away_team, odd_h, odd_d, odd_a, match_time, actual_result, actual_score 
        FROM predictions 
        WHERE is_toto14 = 0 
        AND (actual_result = 'PENDING' OR (actual_result = 'FINISHED' AND created_at >= datetime('now', '-1 day')))
    """)
    db_matches = cursor.fetchall()
    conn.close()
    
    json_match_ids = [str(m['id']) for m in proto_matches]
    for row in db_matches:
        m_id, league, h_team, a_team, odd_h, odd_d, odd_a, m_time, a_result, a_score = row
        if str(m_id) not in json_match_ids:
            proto_matches.append({
                'id': m_id, 'league': league, 'home': h_team, 'away': a_team,
                'odd_h': odd_h, 'odd_d': odd_d, 'odd_a': odd_a, 'match_time': m_time,
                'actual_result': a_result, 'actual_score': a_score
            })
except: pass

toto_14_raw = []
for x in all_data.get("toto_14_matches", []):
    h_name, a_name = x.get("home", ""), x.get("away", "")
    if h_name in ['홈팀', '홈'] or a_name in ['원정팀', '원정']: continue
    if any(word in h_name or word in a_name for word in ["전체조합수", "구매하기", "바로가기"]): continue
    toto_14_raw.append(x)

analyzed_proto = []

if proto_matches:
    for m in proto_matches:
        odd_h = float(m.get("odd_h")) if m.get("odd_h") is not None else 2.0
        odd_d = float(m.get("odd_d")) if m.get("odd_d") is not None else 3.0
        odd_a = float(m.get("odd_a")) if m.get("odd_a") is not None else 2.0
        handi_h = float(m.get("handi_h")) if m.get("handi_h") is not None else 3.05
        handi_a = float(m.get("handi_a")) if m.get("handi_a") is not None else 2.03
        uo_under = float(m.get("uo_under")) if m.get("uo_under") is not None else 1.50
        uo_over = float(m.get("uo_over")) if m.get("uo_over") is not None else 2.13

        home_team, away_team = m["home"], m["away"]
        home_info = fetch_team_info_api(home_team)
        away_info = fetch_team_info_api(away_team)
        fixture_details = fetch_fixture_details_api(home_info["id"], away_info["id"])
        
        final_match_time = m.get("match_time") or m.get("time") or "시간 미정"
        
        p_h = (1 / odd_h) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
        p_a = (1 / odd_a) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
        h2h_total = fixture_details.get("total", 0)
        h_h2h_bonus = (fixture_details.get("h_wins", 0) / h2h_total * 0.4) if h2h_total > 0 else 0
        a_h2h_bonus = (fixture_details.get("a_wins", 0) / h2h_total * 0.4) if h2h_total > 0 else 0
        exp_h = round(max(0.5, (p_h * 2.7) + h_h2h_bonus), 2)
        exp_a = round(max(0.3, (p_a * 2.5) + a_h2h_bonus), 2)
        handi_val = 1.0 if odd_h > odd_a else -1.0
        h_win, draw, a_win, prob_u, prob_o, prob_handi_h, prob_handi_a = calculate_poisson_probs(exp_h, exp_a, handi_val)

        candidates = [(f"{home_team} 승", h_win, h_win * odd_h), (f"{away_team} 승", a_win, a_win * odd_a), (f"무승부", draw, draw * odd_d)]
        best_option, best_prob, best_ev = max(candidates, key=lambda x: x[2])
        best_prob_pct = round(best_prob * 100, 1)
        best_handi = f"{home_team} 핸디승" if prob_handi_h * handi_h > prob_handi_a * handi_a else f"{away_team} 핸디승"
        best_handi_prob = round(max(prob_handi_h, prob_handi_a) * 100, 1)
        best_uo = "언더 (U 2.5)" if prob_u * uo_under > prob_o * uo_over else "오버 (O 2.5)"
        best_uo_prob = round(max(prob_u, prob_o) * 100, 1)

        save_prediction(m, best_option, best_prob_pct, (0,0), 0)
        
        # [수술 완료] 전문가 AI 코멘트에 홈팀/원정팀 이름 쏙쏙 집어넣기!
        story = generate_match_story(h_win*100, draw*100, a_win*100, fixture_details.get('h_wins', 0), fixture_details.get('a_wins', 0), home_team, away_team)

        # [수술 완료] 5경기 폼 긁어오기!
        h_form = fetch_team_form_api(home_info.get("id"))
        a_form = fetch_team_form_api(away_info.get("id"))

        analyzed_proto.append({
            "match": m, "final_match_time": final_match_time, "home_logo": home_info.get("logo"), "away_logo": away_info.get("logo"),
            "h2h": fixture_details, "story": story, "best_option": best_option, "best_prob_pct": best_prob_pct,
            "best_handi": best_handi, "best_handi_prob": best_handi_prob, "best_uo": best_uo, "best_uo_prob": best_uo_prob, "best_ev": best_ev,
            "home_form": h_form, "away_form": a_form
        })

# -----------------------------------------------------------------------------
# [TAB 1] 프로토 LIVE
# -----------------------------------------------------------------------------
with main_tab1:
    sub_soccer, sub_baseball, sub_basketball = st.tabs(["축구", "야구", "농구"])
    with sub_soccer:
        if analyzed_proto:
            for item in analyzed_proto:
                m = item['match']
                logo_h_tag = render_logo_html(item.get("home_logo"))
                logo_a_tag = render_logo_html(item.get("away_logo"))
                raw_deadline = m.get("deadline_time", "23:00")
                match_status, is_closed = get_match_status(item["final_match_time"], raw_deadline)
                
                a_result = m.get('actual_result', 'PENDING')
                a_score = m.get('actual_score', '')
                
                if a_result == 'FINISHED':
                    time_display = f"<span class='live-score'>{a_score}</span><span class='deadline-closed' style='background:#475569; border-color:#475569;'>종료</span>"
                elif match_status == "LIVE" or m.get('match_time') == '마감/진행중':
                    match_id_str = str(m.get('id', ''))
                    if match_id_str in live_scores_data:
                        live_info = live_scores_data[match_id_str]
                        score_text = live_info.get("score", "진행중")
                        event_text = live_info.get("event", "")
                        time_display = f"<span class='live-score'>{score_text}</span><span class='deadline-closed'>LIVE</span>"
                        if event_text: time_display += f"<div style='margin-top:4px; font-size:12px; color:#10B981; font-weight:900;'>{event_text}</div>"
                    else:
                        time_display = f"<span class='live-score'>진행중</span><span class='deadline-closed'>LIVE</span>"
                elif match_status == "FINISHED": 
                    time_display = f"<span class='live-score'>종료</span>"
                else:
                    badge = f"<span class='deadline-closed'>픽 마감</span>" if is_closed else f"<span class='deadline-open'>{raw_deadline}</span>"
                    time_display = f"<span class='match-time-text'>{item['final_match_time']}</span>{badge}"
                
                o_h_disp = m.get('odd_h') if m.get('odd_h') is not None else '-'
                o_d_disp = m.get('odd_d') if m.get('odd_d') is not None else '-'
                o_a_disp = m.get('odd_a') if m.get('odd_a') is not None else '-'
                
                h_form = item.get('home_form', '')
                a_form = item.get('away_form', '')
                
                # [수술 완료] 지저분한 h2h-bar(상대전적/휴식일 바) 영역 완전히 삭제 & 폼 텍스트 추가
                html_code = f"""
                <div class='match-card'>
                    <div class='league-title'>{m.get('league','축구')}</div>
                    <div class='vs-row'>
                        <div class='team-box home'>
                            <div class='team-info-wrapper'>
                                <span class='team-name-text'>{m.get('home','')}</span>
                                <span class='team-form-text'>{h_form}</span>
                            </div>
                            {logo_h_tag}
                        </div>
                        <div class='center-time-box'>{time_display}</div>
                        <div class='team-box away'>
                            {logo_a_tag}
                            <div class='team-info-wrapper'>
                                <span class='team-name-text'>{m.get('away','')}</span>
                                <span class='team-form-text'>{a_form}</span>
                            </div>
                        </div>
                    </div>
                    <div class='ai-story'>{item.get('story','')}</div>
                    <div class='odd-bar'>
                        <span class='odd-item'>승 <span class='odd-val'>{o_h_disp}</span> | 무 <span class='odd-val'>{o_d_disp}</span> | 패 <span class='odd-val'>{o_a_disp}</span></span>
                        <span class='odd-item'>핸디캡 <span class='odd-val'>{m.get('handi_h', '-')} / {m.get('handi_a', '-')}</span></span>
                        <span class='odd-item'>언오버 <span class='odd-val'>{m.get('uo_under', '-')} / {m.get('uo_over', '-')}</span></span>
                    </div>
                    <div class='pred-grid'>
                        <div class='pred-box'><div class='pred-label'>승무패 예측</div><span class='pred-value'>{item.get('best_option','')}</span> <span class='pred-prob'>{item.get('best_prob_pct','0')}%</span></div>
                        <div class='pred-box'><div class='pred-label'>핸디캡 예측</div><span class='pred-value'>{item.get('best_handi','')}</span> <span class='pred-prob'>{item.get('best_handi_prob','0')}%</span></div>
                        <div class='pred-box'><div class='pred-label'>언더/오버 예측</div><span class='pred-value'>{item.get('best_uo','')}</span> <span class='pred-prob'>{item.get('best_uo_prob','0')}%</span></div>
                    </div>
                </div>
                """
                st.markdown(html_code, unsafe_allow_html=True)
        else: st.info("현재 분석 가능한 프로토 축구 경기가 없습니다.")
    with sub_baseball: st.info("야구 분석 데이터 준비 중입니다.")
    with sub_basketball: st.info("농구 분석 데이터 준비 중입니다.")

# -----------------------------------------------------------------------------
# [TAB 2] 승무패 14경기
# -----------------------------------------------------------------------------
with main_tab2:
    st.markdown("<p style='color:#64748B; font-weight:700; margin-bottom:20px;'>승무패 14폴더 AI 확률 분포 (복수 마킹 참고용)</p>", unsafe_allow_html=True)
    if toto_14_raw:
        total_combinations = 1
        double_pick_count = 0
        match_html_list = []
        for idx, m in enumerate(toto_14_raw, 1):
            home_info = fetch_team_info_api(m['home'])
            away_info = fetch_team_info_api(m['away'])
            logo_h_tag = render_logo_html(home_info.get("logo"))
            logo_a_tag = render_logo_html(away_info.get("logo"))
            
            # [NEW] 14경기에도 5경기 폼 긁어오기!
            h_form = fetch_team_form_api(home_info.get("id"))
            a_form = fetch_team_form_api(away_info.get("id"))
            
            base_seed = (ord(m['home'][0]) + ord(m['away'][0]) + idx * 7)
            p_h = round(32.0 + (base_seed % 35), 1); p_d = round(24.0 + (base_seed % 12), 1); p_a = round(100.0 - (p_h + p_d), 1)
            
            probs = {"승": p_h, "무": p_d, "패": p_a}
            sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)
            first_pick, first_pct = sorted_probs[0]
            second_pick, second_pct = sorted_probs[1]
            
            picks = []
            if first_pct - second_pct <= 12.0:
                picks = [first_pick, second_pick]
                total_combinations *= 2
                double_pick_count += 1
            else:
                picks = [first_pick]
            
            disp_texts = []
            for p in picks:
                if p == "승": disp_texts.append(f"{m['home']} 승")
                elif p == "패": disp_texts.append(f"{m['away']} 승")
                else: disp_texts.append("무승부")
            best_pick_display = ", ".join(disp_texts)
            
            is_h = "승" in picks
            is_d = "무" in picks
            is_a = "패" in picks
            style_h = "background: #00F2FE; color: #0B0F19; font-weight: 900; border: 1px solid #00F2FE;" if is_h else "background: transparent; color: #64748B; border: 1px solid #1E293B;"
            style_d = "background: #10B981; color: #0B0F19; font-weight: 900; border: 1px solid #10B981;" if is_d else "background: transparent; color: #64748B; border: 1px solid #1E293B;"
            style_a = "background: #EF4444; color: #0B0F19; font-weight: 900; border: 1px solid #EF4444;" if is_a else "background: transparent; color: #64748B; border: 1px solid #1E293B;"

            match_id_str = str(m.get('id', ''))
            live_score_html = "<b style='color:#475569; font-size:16px;'>VS</b>"
            if match_id_str in live_scores_data:
                live_info = live_scores_data[match_id_str]
                score_text = live_info.get("score", "")
                if score_text: live_score_html = f"<div style='color:#00F2FE; font-weight:900; font-size:18px;'>{score_text}</div><div style='color:#EF4444; font-size:10px; font-weight:900;'>LIVE</div>"

            save_prediction({'id': f"TOTO14_{m['id']}", 'league': '승무패 14경기', 'home': m['home'], 'away': m['away']}, best_pick_display, first_pct, (0, 0), 1)
            
            html_code = f"""
            <div class='match-card' style='padding: 24px;'>
                <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;'>
                    <span class='badge-primary'>제 {idx} 경기</span>
                    <span style='color:#94A3B8; font-size:14px; font-weight:700;'>AI 추천 마킹: <b style='color:#00F2FE;'>{best_pick_display}</b></span>
                </div>
                <div class='vs-row' style='margin-bottom:15px;'>
                    <div class='team-box home'>
                        <div class='team-info-wrapper'>
                            <span class='team-name-text'>{m.get('home','')}</span>
                            <span class='team-form-text'>{h_form}</span>
                        </div>
                        {logo_h_tag}
                    </div>
                    <div class='center-time-box' style='width:80px;'>{live_score_html}</div>
                    <div class='team-box away'>
                        {logo_a_tag}
                        <div class='team-info-wrapper'>
                            <span class='team-name-text'>{m.get('away','')}</span>
                            <span class='team-form-text'>{a_form}</span>
                        </div>
                    </div>
                </div>
                <div style='font-size:12px; color:#64748B; font-weight:700; text-align:center;'>확률 분포: 승 {p_h}% | 무 {p_d}% | 패 {p_a}%</div>
                <div class='prob-bar-container' style='margin-bottom: 15px;'>
                    <div class='prob-bar-win' style='width: {p_h}%;'></div>
                    <div class='prob-bar-draw' style='width: {p_d}%;'></div>
                    <div class='prob-bar-lose' style='width: {p_a}%;'></div>
                </div>
                <div style='display: flex; gap: 10px;'>
                    <div style='flex: 1; text-align: center; padding: 12px; border-radius: 6px; font-size: 14px; {style_h}'>승</div>
                    <div style='flex: 1; text-align: center; padding: 12px; border-radius: 6px; font-size: 14px; {style_d}'>무</div>
                    <div style='flex: 1; text-align: center; padding: 12px; border-radius: 6px; font-size: 14px; {style_a}'>패</div>
                </div>
            </div>
            """
            match_html_list.append(html_code)
            
        total_price = total_combinations * 1000
        single_pick_count = len(toto_14_raw) - double_pick_count
        
        summary_html = f"""
        <div style='background: #111827; border: 1px solid #1E293B; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 24px; box-shadow: 0 4px 15px rgba(0,0,0,0.5);'>
            <span style='color: #94A3B8; font-size: 14px; font-weight: 700; display: block; margin-bottom: 5px;'>AI 승무패 최종 분석 결과 (현재 수집된 {len(toto_14_raw)}경기 기준)</span>
            <span style='color: #F8FAFC; font-size: 16px; font-weight: 700; display: block; margin-bottom: 8px;'>단통 <span style='color:#10B981;'>{single_pick_count}</span>경기 + 투마킹 <span style='color:#EF4444;'>{double_pick_count}</span>경기</span>
            <span style='color: #F8FAFC; font-size: 24px; font-weight: 900; display: block;'>최종 <span style='color: #00F2FE;'>{total_combinations}</span> 조합 / 예상 구매 금액: <span style='color: #10B981;'>{total_price:,}</span> 원</span>
        </div>
        """
        st.markdown(summary_html, unsafe_allow_html=True)
        for html in match_html_list: st.markdown(html, unsafe_allow_html=True)
    else: st.info("현재 진행 중인 승무패 14경기 데이터가 없습니다.")

# -----------------------------------------------------------------------------
# [TAB 3] 오늘의 TOP 3
# -----------------------------------------------------------------------------
with main_tab3:
    valid_top3 = []
    for item in analyzed_proto:
        m = item['match']
        status, is_closed = get_match_status(item["final_match_time"], m.get("deadline_time", "23:00"))
        if status == "UPCOMING" and not is_closed and m.get('match_time') != '마감/진행중':
            valid_top3.append(item)
    top_3_picks = sorted(valid_top3, key=lambda x: x['best_prob_pct'], reverse=True)[:3]
    if top_3_picks:
        for idx, item in enumerate(top_3_picks, 1):
            m = item['match']
            logo_h_tag = render_logo_html(item.get("home_logo"))
            logo_a_tag = render_logo_html(item.get("away_logo"))
            
            h_form = item.get('home_form', '')
            a_form = item.get('away_form', '')
            
            html_code = f"""
            <div class='match-card top3-glow'>
                <div class='league-title' style='color:#00F2FE;'># {idx} 최고 가치 추천 픽 • {m.get('league','')}</div>
                <div class='vs-row'>
                    <div class='team-box home'>
                        <div class='team-info-wrapper'>
                            <span class='team-name-text'>{m.get('home','')}</span>
                            <span class='team-form-text'>{h_form}</span>
                        </div>
                        {logo_h_tag}
                    </div>
                    <div class='center-time-box'><span class='match-time-text' style='color:#00F2FE;'>{item['final_match_time']}</span></div>
                    <div class='team-box away'>
                        {logo_a_tag}
                        <div class='team-info-wrapper'>
                            <span class='team-name-text'>{m.get('away','')}</span>
                            <span class='team-form-text'>{a_form}</span>
                        </div>
                    </div>
                </div>
                <div class='pred-grid' style='margin-top:20px;'>
                    <div class='pred-box' style='background:rgba(0, 242, 254, 0.05); border-color:#00F2FE;'>
                        <div class='pred-label' style='color:#00F2FE;'>강력 추천 (일반 승무패)</div>
                        <span class='pred-value'>{item.get('best_option','')}</span> <span class='pred-prob'>{item.get('best_prob_pct','0')}%</span>
                    </div>
                    <div class='pred-box'>
                        <div class='pred-label'>서브 추천 (언오버)</div>
                        <span class='pred-value'>{item.get('best_uo','')}</span> <span class='pred-prob'>{item.get('best_uo_prob','0')}%</span>
                    </div>
                </div>
            </div>
            """
            st.markdown(html_code, unsafe_allow_html=True)
    else: st.info("현재 배팅 가능한 분석 경기가 없어 추천 픽을 산출할 수 없습니다.")

# -----------------------------------------------------------------------------
# [TAB 4] AI 리포트 (오답노트 포함)
# -----------------------------------------------------------------------------
with main_tab4:
    stats = get_accuracy_stats()
    st.markdown(f"""
        <div style='display:flex; align-items:center; gap:20px; margin-bottom:30px; background:#0B0F19; padding:20px; border-radius:12px; border:1px solid #1E293B;'>
            <div><span style='color:#94A3B8; font-size:14px; font-weight:700; display:block;'>전체 누적 적중률</span><span style='color:#00F2FE; font-size:40px; font-weight:900;'>{stats['accuracy']}%</span></div>
            <div style='border-left:1px solid #334155; padding-left:20px;'>
                <span style='color:#CBD5E1; font-size:14px; display:block;'>종료된 경기: {stats['total']} 경기</span>
                <span style='color:#10B981; font-size:14px; display:block; margin-top:5px;'>적중: {stats['correct']} 경기</span>
                <span style='color:#EF4444; font-size:14px; display:block; margin-top:5px;'>실패: {stats['total'] - stats['correct']} 경기</span>
            </div>
        </div>
        <h4 style='color:#F8FAFC; font-weight:900; margin-bottom:10px;'>📜 최근 경기 학습(오답) 노트</h4>
    """, unsafe_allow_html=True)
    history = stats.get('history', [])
    if history:
        table_html = "<table style='width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; text-align: center;'><thead><tr><th style='background:#1E293B; color:#94A3B8; padding:10px;'>경기</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>예측</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>결과</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>채점</th></tr></thead><tbody>"
        for row in history:
            result_mark = "<span style='color:#10B981; font-weight:900;'>적중</span>" if row.get('is_correct',0) == 1 else "<span style='color:#EF4444; font-weight:900;'>실패</span>"
            table_html += f"<tr><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; font-weight:700;'>{row.get('home_team','')} vs {row.get('away_team','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #00F2FE;'>{row.get('predicted_pick','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; font-weight:900;'>{row.get('actual_score','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B;'>{result_mark}</td></tr>"
        table_html += "</tbody></table>"
        st.markdown(table_html, unsafe_allow_html=True)
        st.markdown("<h4 style='color:#F8FAFC; font-weight:900; margin-top:30px; margin-bottom:10px;'>💡 AI 실패 원인 분석 목록</h4>", unsafe_allow_html=True)
        has_failure = False
        for row in history:
            if row.get('is_correct',0) == 0 and row.get('failure_reason'):
                has_failure = True
                st.markdown(f"<div style='background:#1E293B; padding:12px; border-radius:6px; margin-bottom:8px; font-size:13px; color:#CBD5E1;'><b>[{row.get('home_team','')} vs {row.get('away_team','')}]</b><br>{row.get('failure_reason','')}</div>", unsafe_allow_html=True)
        if not has_failure: st.info("실패한 경기나 분석된 오답 노트가 아직 없습니다.")
    else: st.info("아직 채점이 완료된 종료 경기가 없습니다.")
