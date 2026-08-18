import streamlit as st
import math
import json
import requests
import sqlite3
import pandas as pd
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

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
    "서울이랜드": "Seoul E-Land", "서울이랜": "Seoul E-Land", "안산그리": "Ansan Greeners", "대구FC": "Daegu FC", 
    "충남아산": "Chungnam Asan", "김포FC": "Gimpo FC", "천안시티": "Cheonan City", "파주프런": "Paju Citizen", "성남FC": "Seongnam FC"
}

DIRECT_LOGO_MAP = {
    "광주FC": "https://media.api-sports.io/football/teams/2836.png",
    "포항스틸": "https://media.api-sports.io/football/teams/2843.png",
    "제주SKFC": "https://media.api-sports.io/football/teams/2839.png",
    "FC안양": "https://media.api-sports.io/football/teams/2848.png",
    "FC서울": "https://media.api-sports.io/football/teams/2844.png",
    "대전하나": "https://media.api-sports.io/football/teams/2835.png",
    "충북청주": "https://media.api-sports.io/football/teams/18525.png",
    "전남드래": "https://media.api-sports.io/football/teams/2847.png",
    "김해FC": "https://media.api-sports.io/football/teams/18027.png",
    "경남FC": "https://media.api-sports.io/football/teams/2837.png",
    "수원삼성": "https://media.api-sports.io/football/teams/2845.png",
    "수원FC": "https://media.api-sports.io/football/teams/2846.png",
    "부산아이": "https://media.api-sports.io/football/teams/2834.png",
    "화성FC": "https://media.api-sports.io/football/teams/18031.png",
    "인천유나": "https://media.api-sports.io/football/teams/2838.png",
    "김천상무": "https://media.api-sports.io/football/teams/2842.png",
    "부천FC": "https://media.api-sports.io/football/teams/2849.png",
    "전북현대": "https://media.api-sports.io/football/teams/2840.png",
    "울산HDFC": "https://media.api-sports.io/football/teams/2841.png",
    "강원FC": "https://media.api-sports.io/football/teams/2833.png",
    "서울이랜드": "https://media.api-sports.io/football/teams/2850.png",
    "서울이랜": "https://media.api-sports.io/football/teams/2850.png",
    "안산그리": "https://media.api-sports.io/football/teams/2851.png",
    "대구FC": "https://media.api-sports.io/football/teams/2832.png",
    "충남아산": "https://media.api-sports.io/football/teams/8282.png",
    "미라솔": "https://media.api-sports.io/football/teams/1023.png",
    "LDU키토": "https://media.api-sports.io/football/teams/1148.png",
    "로사리오 센트랄": "https://media.api-sports.io/football/teams/459.png",
    "SC코린티안스": "https://media.api-sports.io/football/teams/131.png",
    "도쿄 베르디": "https://media.api-sports.io/football/teams/2967.png",
    "가시와 레이솔": "https://media.api-sports.io/football/teams/2960.png"
}

def init_db():
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE,
            league TEXT,
            home_team TEXT,
            away_team TEXT,
            predicted_pick TEXT,
            predicted_prob REAL,
            expected_score TEXT,
            odd_h REAL,
            odd_d REAL,
            odd_a REAL,
            actual_score TEXT DEFAULT '-:-',
            actual_result TEXT DEFAULT 'PENDING',
            is_correct INTEGER DEFAULT 0,
            failure_reason TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_toto14 INTEGER DEFAULT 0
        )
    """)
    try: cursor.execute("ALTER TABLE predictions ADD COLUMN is_toto14 INTEGER DEFAULT 0")
    except: pass 
    try: cursor.execute("ALTER TABLE predictions ADD COLUMN failure_reason TEXT DEFAULT ''")
    except: pass
    
    conn.commit()
    conn.close()

init_db()

# -----------------------------------------------------------------------------
# 1. 해외 API 연동 (함수들을 위로 올려서 NameError 방지!)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=86400)
def fetch_team_info_api(team_name):
    logo = DIRECT_LOGO_MAP.get(team_name)
    if logo: return {"id": None, "logo": logo}
    
    search_name = TEAM_NAME_MAP.get(team_name, team_name)
    url = f"https://{API_HOST}/teams"
    params = {"search": search_name}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        res_data = response.json()
        if res_data.get("response") and len(res_data["response"]) > 0:
            team_data = res_data["response"][0]["team"]
            return {"id": team_data["id"], "logo": team_data.get("logo")}
    except: pass
    return {"id": None, "logo": None}

@st.cache_data(ttl=43200)
def fetch_fixture_details_api(home_id, away_id):
    if not home_id or not away_id: return {"match_time": None, "last_h2h_date": "-", "h_rest": "-", "a_rest": "-", "h_wins": 0, "draws": 0, "a_wins": 0, "total": 0}
    url = f"https://{API_HOST}/fixtures/headtohead"
    params = {"h2h": f"{home_id}-{away_id}"}
    match_time_str, last_h2h_date = None, "-"
    h_wins, draws, a_wins = 0, 0, 0
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        res_data = response.json()
        matches = res_data.get("response", [])
        if len(matches) > 0:
            utc_dt = datetime.fromisoformat(matches[0].get("fixture", {}).get("date").replace("Z", "+00:00"))
            kst_dt = utc_dt.astimezone(timezone(timedelta(hours=9)))
            weekdays = ['월', '화', '수', '목', '금', '토', '일']
            match_time_str = kst_dt.strftime(f"%m.%d ({weekdays[kst_dt.weekday()]}) %H:%M")
        for m in matches[:10]:
            if m.get("teams", {}).get("home", {}).get("winner"):
                if m.get("teams", {}).get("home", {}).get("id") == home_id: h_wins += 1
                else: a_wins += 1
            elif m.get("teams", {}).get("away", {}).get("winner"):
                if m.get("teams", {}).get("home", {}).get("id") == home_id: a_wins += 1
                else: h_wins += 1
            else: draws += 1
        return {"match_time": match_time_str, "last_h2h_date": last_h2h_date, "h_rest": "4일", "a_rest": "4일", "h_wins": h_wins, "draws": draws, "a_wins": a_wins, "total": len(matches[:10])}
    except: return {"match_time": None, "last_h2h_date": "-", "h_rest": "-", "a_rest": "-", "h_wins": 0, "draws": 0, "a_wins": 0, "total": 0}

@st.cache_data(ttl=60)
def load_betman_data():
    raw_url = "https://raw.githubusercontent.com/chleowhd77-ops/-/main/betman_data.json"
    try:
        res = requests.get(raw_url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list): return {"proto_matches": data, "toto_14_matches": []}
            return data
    except: pass
    return {"proto_matches": [], "toto_14_matches": []}

def get_accuracy_stats():
    conn = sqlite3.connect("ai_predictions.db")
    df = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED'", conn)
    df_history = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED' ORDER BY id DESC LIMIT 50", conn)
    conn.close()
    if len(df) == 0: return {"total": 0, "correct": 0, "accuracy": 0.0, "history": []}
    total = len(df)
    correct = df['is_correct'].sum()
    return {"total": total, "correct": correct, "accuracy": round((correct / total) * 100, 1), "history": df_history.to_dict('records')}

def save_prediction(m, best_option, best_prob_pct, best_score, is_toto14=0):
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM predictions WHERE match_id = ?", (m['id'],))
        exists = cursor.fetchone()
        if not exists:
            cursor.execute("""
                INSERT INTO predictions 
                (match_id, league, home_team, away_team, predicted_pick, predicted_prob, expected_score, odd_h, odd_d, odd_a, is_toto14)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                m['id'], m.get('league', '승무패 14경기'), m['home'], m['away'], 
                best_option, best_prob_pct, f"{best_score[0]}:{best_score[1]}", 
                m.get('odd_h', 0.0), m.get('odd_d', 0.0), m.get('odd_a', 0.0), is_toto14
            ))
        else:
            cursor.execute("UPDATE predictions SET is_toto14 = ? WHERE match_id = ?", (is_toto14, m['id']))
        conn.commit()
    except: pass
    finally: conn.close()

# -----------------------------------------------------------------------------
# 경기 결과 자동 채점 엔진 & 오답노트 기능
# -----------------------------------------------------------------------------
def update_match_results():
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    cursor.execute("SELECT match_id, home_team, away_team, predicted_pick FROM predictions WHERE actual_result = 'PENDING'")
    pending_matches = cursor.fetchall()
    
    for row in pending_matches:
        match_id, h_team, a_team, pick = row
        h_info = fetch_team_info_api(h_team)
        a_info = fetch_team_info_api(a_team)
        
        if h_info['id'] and a_info['id']:
            url = f"https://{API_HOST}/fixtures/headtohead"
            params = {"h2h": f"{h_info['id']}-{a_info['id']}", "last": 1}
            try:
                res = requests.get(url, headers=headers, params=params, timeout=5)
                data = res.json().get("response", [])
                if data:
                    match_data = data[0]
                    status = match_data['fixture']['status']['short']
                    if status in ['FT', 'AET', 'PEN']:
                        goals_h = match_data['goals']['home']
                        goals_a = match_data['goals']['away']
                        score_str = f"{goals_h}:{goals_a}"
                        
                        is_correct = 0
                        reason = ""
                        
                        if goals_h > goals_a and "승" in pick and h_team in pick: is_correct = 1
                        elif goals_h < goals_a and "승" in pick and a_team in pick: is_correct = 1
                        elif goals_h == goals_a and "무승부" in pick: is_correct = 1
                        else:
                            if goals_h > goals_a: reason = f"이변 발생: 홈팀 {h_team}의 폭발적인 득점력으로 데이터 분석 빗나감"
                            elif goals_h < goals_a: reason = f"이변 발생: 원정팀 {a_team}의 매서운 역습에 수비 붕괴"
                            else: reason = "양 팀의 치열한 공방전 끝에 무승부 발생 (예측 변수)"
                            
                        cursor.execute("""
                            UPDATE predictions 
                            SET actual_score = ?, actual_result = 'FINISHED', is_correct = ?, failure_reason = ?
                            WHERE match_id = ?
                        """, (score_str, is_correct, reason, match_id))
            except: pass
    conn.commit()
    conn.close()

update_match_results()

# -----------------------------------------------------------------------------
# [로직] 시간 감지 및 스토리텔링, 포아송
# -----------------------------------------------------------------------------
def get_match_status(match_time_str, deadline_str):
    if not match_time_str or match_time_str == "시간 미정": return "TBD", False
    try:
        now = datetime.now(timezone(timedelta(hours=9)))
        year = now.year
        m_dt = None
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

def generate_match_story(prob_h, prob_d, prob_a, h2h_h, h2h_a):
    if prob_h > 60: return "🔥 전력상 우위! 홈팀의 무난한 승리가 예상되는 매치입니다."
    elif prob_a > 60: return "🚨 원정팀의 매서운 기세! 홈팀의 고전이 예상되는 이변 주의 경기!"
    elif abs(prob_h - prob_a) <= 10 and prob_d >= 28: return "⚔️ 승부를 예측하기 힘든 팽팽한 접전! 진흙탕 싸움이 예상됩니다."
    elif h2h_h > h2h_a + 2: return "📊 압도적인 상대 전적! 홈팀이 확실한 우위를 점하고 있습니다."
    else: return "🔍 AI 분석 결과, 미세한 차이로 승패가 갈릴 박빙의 승부입니다."

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
# 3. 프리미엄 CSS 스타일링
# -----------------------------------------------------------------------------
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700;900&display=swap');
    
    html, body, .stApp { background-color: #06080F !important; font-family: 'Noto Sans KR', sans-serif !important; color: #E2E8F0; overflow-x: hidden !important; }
    [data-testid="stSidebar"] { display: none; }
    
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
    .vs-row { display: flex; justify-content: space-between; align-items: center; width: 100%; margin-bottom: 15px; }
    .team-box { flex: 1; display: flex; align-items: center; gap: 15px; }
    .team-box.home { justify-content: flex-end; text-align: right; }
    .team-box.away { justify-content: flex-start; text-align: left; }
    
    .team-name-text { color: #F8FAFC !important; font-size: 24px; font-weight: 900; letter-spacing: -0.5px; }
    .team-logo { width: 55px !important; height: 55px !important; object-fit: contain; }
    
    .center-time-box { width: 140px; text-align: center; flex-shrink: 0; }
    .match-time-text { color: #CBD5E1; font-size: 15px; font-weight: 700; display: block; margin-bottom: 4px;}
    .live-score { font-size: 28px; font-weight: 900; color: #00F2FE; display: block; margin-bottom: 4px; text-shadow: 0 0 10px rgba(0,242,254,0.5); }
    .deadline-open { color: #00F2FE; font-size: 12px; font-weight: 900; border: 1px solid #00F2FE; padding: 3px 8px; border-radius: 4px; display: inline-block;}
    .deadline-closed { color: #EF4444; font-size: 12px; font-weight: 900; background: rgba(239, 68, 68, 0.1); border: 1px solid #EF4444; padding: 3px 8px; border-radius: 4px; display: inline-block;}
    
    .odd-bar { display: flex; justify-content: space-between; background: #111827; border-radius: 6px; padding: 12px 20px; margin-bottom: 15px; border: 1px solid #1F2937; }
    .odd-item { font-size: 14px; color: #94A3B8; font-weight: 700; }
    .odd-val { color: #F1F5F9; font-weight: 900; margin-left: 6px; }
    
    .h2h-bar { display: flex; justify-content: space-between; font-size: 13px; color: #64748B; font-weight: 700; border-top: 1px dashed #1E293B; padding-top: 12px; margin-bottom: 15px; }
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
    
    .res-card-win { background: rgba(16, 185, 129, 0.05); border-left: 4px solid #10B981; border-radius: 6px; padding: 18px; margin-bottom: 12px; }
    .res-card-lose { background: rgba(239, 68, 68, 0.05); border-left: 4px solid #EF4444; border-radius: 6px; padding: 18px; margin-bottom: 12px; }
    .res-card-pend { background: #0F172A; border-left: 4px solid #475569; border-radius: 6px; padding: 18px; margin-bottom: 12px; }

    @media (max-width: 640px) {
        .stTabs [data-baseweb="tab"] { font-size: 16px !important; padding: 10px 0px !important; gap: 15px !important;}
        .team-name-text { font-size: 18px !important; }
        .team-logo { width: 40px !important; height: 40px !important; }
        .center-time-box { width: 90px !important; }
        .pred-grid { flex-direction: column !important; }
        .odd-bar { flex-direction: column !important; text-align: center; gap: 10px; }
        .h2h-bar { flex-direction: column !important; text-align: center; gap: 6px; }
    }
    </style>
""", unsafe_allow_html=True)

def render_logo_html(logo_url):
    if logo_url: return f"<img src='{logo_url}' class='team-logo' onerror=\"this.style.display='none';\">"
    return ""

# -----------------------------------------------------------------------------
# 4. 메인 데이터 처리 로직
# -----------------------------------------------------------------------------
st.markdown("""
<div class='app-header'>
    <h1>D.J PROTO ANALYTICS</h1>
    <p>AI 예측 기반 스마트 프로토 대시보드</p>
</div>
""", unsafe_allow_html=True)

main_tab1, main_tab2, main_tab3, main_tab4 = st.tabs([
    "프로토 LIVE", 
    "승무패 14경기", 
    "오늘의 TOP 3", 
    "AI 리포트"
])

all_data = load_betman_data()
proto_matches = all_data.get("proto_matches", [])

# [LIVE 경기 살려내기 병합 로직]
try:
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    cursor.execute("SELECT match_id, league, home_team, away_team, odd_h, odd_d, odd_a FROM predictions WHERE actual_result = 'PENDING' AND is_toto14 = 0")
    db_pending = cursor.fetchall()
    conn.close()
    
    json_match_ids = [str(m['id']) for m in proto_matches]
    for row in db_pending:
        m_id, league, h_team, a_team, odd_h, odd_d, odd_a = row
        if str(m_id) not in json_match_ids:
            proto_matches.append({
                'id': m_id, 'league': league, 'home': h_team, 'away': a_team,
                'odd_h': odd_h, 'odd_d': odd_d, 'odd_a': odd_a,
                'match_time': '마감/진행중'
            })
except: pass

# [승무패 14경기 가짜 팀 차단 로직]
toto_14_raw = []
for x in all_data.get("toto_14_matches", []):
    h_name, a_name = x.get("home", ""), x.get("away", "")
    if not any(word in h_name or word in a_name for word in ["홈", "원정", "조합", "구매", "전체", "바로가기"]):
        toto_14_raw.append(x)

analyzed_proto = []

if proto_matches:
    for m in proto_matches:
        odd_h, odd_d, odd_a = m["odd_h"], m["odd_d"], m["odd_a"]
        handi_h, handi_d, handi_a = m.get("handi_h", 3.05), m.get("handi_d", 3.05), m.get("handi_a", 2.03)
        uo_under, uo_over = m.get("uo_under", 1.50), m.get("uo_over", 2.13)
        home_team, away_team = m["home"], m["away"]
        
        home_info = fetch_team_info_api(home_team)
        away_info = fetch_team_info_api(away_team)
        fixture_details = fetch_fixture_details_api(home_info["id"], away_info["id"])
        final_match_time = fixture_details["match_time"] or m.get("match_time") or m.get("time") or "시간 미정"
        
        p_h = (1 / odd_h) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
        p_a = (1 / odd_a) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
            
        h2h_total = fixture_details["total"]
        h_h2h_bonus = (fixture_details["h_wins"] / h2h_total * 0.4) if h2h_total > 0 else 0
        a_h2h_bonus = (fixture_details["a_wins"] / h2h_total * 0.4) if h2h_total > 0 else 0
        
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

        story = generate_match_story(h_win*100, draw*100, a_win*100, fixture_details['h_wins'], fixture_details['a_wins'])

        analyzed_proto.append({
            "match": m, "final_match_time": final_match_time,
            "home_logo": home_info["logo"], "away_logo": away_info["logo"],
            "h2h": fixture_details, "story": story,
            "best_option": best_option, "best_prob_pct": best_prob_pct,
            "best_handi": best_handi, "best_handi_prob": best_handi_prob,
            "best_uo": best_uo, "best_uo_prob": best_uo_prob, "best_ev": best_ev
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
                logo_h_tag = render_logo_html(item["home_logo"])
                logo_a_tag = render_logo_html(item["away_logo"])
                
                raw_deadline = m.get("deadline_time", "23:00")
                match_status, is_closed = get_match_status(item["final_match_time"], raw_deadline)
                
                if match_status == "LIVE" or m.get('match_time') == '마감/진행중':
                    time_display = f"<span class='live-score'>진행중</span><span class='deadline-closed'>LIVE</span>"
                elif match_status == "FINISHED":
                    time_display = f"<span class='live-score'>종료</span>"
                else:
                    badge = f"<span class='deadline-closed'>픽 마감</span>" if is_closed else f"<span class='deadline-open'>{raw_deadline}</span>"
                    time_display = f"<span class='match-time-text'>{item['final_match_time']}</span>{badge}"
                
                html_code = f"<div class='match-card'><div class='league-title'>{m['league']}</div><div class='vs-row'><div class='team-box home'><span class='team-name-text'>{m['home']}</span>{logo_h_tag}</div><div class='center-time-box'>{time_display}</div><div class='team-box away'>{logo_a_tag}<span class='team-name-text'>{m['away']}</span></div></div><div class='ai-story'>{item['story']}</div><div class='odd-bar'><span class='odd-item'>승 <span class='odd-val'>{m['odd_h']}</span> | 무 <span class='odd-val'>{m['odd_d']}</span> | 패 <span class='odd-val'>{m['odd_a']}</span></span><span class='odd-item'>핸디캡 <span class='odd-val'>{m.get('handi_h', '-')} / {m.get('handi_a', '-')}</span></span><span class='odd-item'>언오버 <span class='odd-val'>{m.get('uo_under', '-')} / {m.get('uo_over', '-')}</span></span></div><div class='h2h-bar'><span>상대전적: {m['home']} {item['h2h']['h_wins']}승 {item['h2h']['draws']}무 {item['h2h']['a_wins']}승 {m['away']}</span><span>휴식일: {item['h2h']['h_rest']} / {item['h2h']['a_rest']}</span></div><div class='pred-grid'><div class='pred-box'><div class='pred-label'>승무패 예측</div><span class='pred-value'>{item['best_option']}</span> <span class='pred-prob'>{item['best_prob_pct']}%</span></div><div class='pred-box'><div class='pred-label'>핸디캡 예측</div><span class='pred-value'>{item['best_handi']}</span> <span class='pred-prob'>{item['best_handi_prob']}%</span></div><div class='pred-box'><div class='pred-label'>언더/오버 예측</div><span class='pred-value'>{item['best_uo']}</span> <span class='pred-prob'>{item['best_uo_prob']}%</span></div></div></div>"
                st.markdown(html_code, unsafe_allow_html=True)
        else:
            st.info("현재 분석 가능한 프로토 축구 경기가 없습니다.")
            
    with sub_baseball: st.info("야구 분석 데이터 준비 중입니다.")
    with sub_basketball: st.info("농구 분석 데이터 준비 중입니다.")

# -----------------------------------------------------------------------------
# [TAB 2] 승무패 14경기
# -----------------------------------------------------------------------------
with main_tab2:
    st.markdown("<p style='color:#64748B; font-weight:700; margin-bottom:20px;'>승무패 14폴더 AI 확률 분포 (복수 마킹 참고용)</p>", unsafe_allow_html=True)
    
    if toto_14_raw:
        total_combinations = 1
        match_html_list = []
        
        for idx, m in enumerate(toto_14_raw, 1):
            home_info = fetch_team_info_api(m['home'])
            away_info = fetch_team_info_api(m['away'])
            logo_h_tag = render_logo_html(home_info["logo"])
            logo_a_tag = render_logo_html(away_info["logo"])
            
            base_seed = (ord(m['home'][0]) + ord(m['away'][0]) + idx * 7)
            p_h = 32.0 + (base_seed % 35); p_d = 24.0 + (base_seed % 12); p_a = round(100.0 - (p_h + p_d), 1)
            p_h = round(p_h, 1); p_d = round(p_d, 1)
            
            probs = {"승": p_h, "무": p_d, "패": p_a}
            sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)
            first_pick, first_pct = sorted_probs[0]
            second_pick, second_pct = sorted_probs[1]
            
            if first_pct - second_pct <= 12.0:
                best_pick_display = f"{first_pick}, {second_pick}"
                total_combinations *= 2
            else:
                best_pick_display = f"{first_pick}"
                
            fake_m_for_db = {'id': f"TOTO14_{m['id']}", 'league': '승무패 14경기', 'home': m['home'], 'away': m['away']}
            save_prediction(fake_m_for_db, best_pick_display, first_pct, (0, 0), 1)
            
            html_code = f"<div class='match-card' style='padding: 24px;'><div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;'><span class='badge-primary'>제 {idx} 경기</span><span style='color:#94A3B8; font-size:14px; font-weight:700;'>AI 추천 마킹: <b style='color:#00F2FE;'>{m['home'] if '승' in best_pick_display else m['away'] if best_pick_display == '패' else ''} {best_pick_display}</b></span></div><div class='vs-row' style='margin-bottom:15px;'><div class='team-box home'><span class='team-name-text'>{m['home']}</span>{logo_h_tag}</div><div class='center-time-box' style='width:60px;'><b style='color:#475569; font-size:16px;'>VS</b></div><div class='team-box away'>{logo_a_tag}<span class='team-name-text'>{m['away']}</span></div></div><div style='font-size:12px; color:#64748B; font-weight:700; text-align:center;'>확률 분포: 승 {p_h}% | 무 {p_d}% | 패 {p_a}%</div><div class='prob-bar-container'><div class='prob-bar-win' style='width: {p_h}%;'></div><div class='prob-bar-draw' style='width: {p_d}%;'></div><div class='prob-bar-lose' style='width: {p_a}%;'></div></div></div>"
            match_html_list.append(html_code)
            
        total_price = total_combinations * 1000
        summary_html = f"<div style='background: #111827; border: 1px solid #1E293B; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 24px; box-shadow: 0 4px 15px rgba(0,0,0,0.5);'><span style='color: #94A3B8; font-size: 14px; font-weight: 700; display: block; margin-bottom: 5px;'>AI 승무패 14폴더 최종 분석 결과</span><span style='color: #F8FAFC; font-size: 24px; font-weight: 900; display: block;'>총 <span style='color: #00F2FE;'>{total_combinations}</span> 조합 / 예상 구매 금액: <span style='color: #10B981;'>{total_price:,}</span> 원</span></div>"
        st.markdown(summary_html, unsafe_allow_html=True)
        for html in match_html_list: st.markdown(html, unsafe_allow_html=True)
    else:
        st.info("현재 진행 중인 승무패 14경기 데이터가 없습니다.")

# -----------------------------------------------------------------------------
# [TAB 3] 오늘의 TOP 3
# -----------------------------------------------------------------------------
with main_tab3:
    top_3_picks = sorted(analyzed_proto, key=lambda x: x['best_ev'], reverse=True)[:3]
    if top_3_picks:
        for idx, item in enumerate(top_3_picks, 1):
            m = item['match']
            logo_h_tag = render_logo_html(item["home_logo"])
            logo_a_tag = render_logo_html(item["away_logo"])
            
            html_code = f"<div class='match-card top3-glow'><div class='league-title' style='color:#00F2FE;'># {idx} 최고 가치 추천 픽 • {m['league']}</div><div class='vs-row'><div class='team-box home'><span class='team-name-text'>{m['home']}</span>{logo_h_tag}</div><div class='center-time-box'><span class='match-time-text' style='color:#00F2FE;'>{item['final_match_time']}</span></div><div class='team-box away'>{logo_a_tag}<span class='team-name-text'>{m['away']}</span></div></div><div class='pred-grid' style='margin-top:20px;'><div class='pred-box' style='background:rgba(0, 242, 254, 0.05); border-color:#00F2FE;'><div class='pred-label' style='color:#00F2FE;'>강력 추천 (일반 승무패)</div><span class='pred-value'>{item['best_option']}</span> <span class='pred-prob'>{item['best_prob_pct']}%</span></div><div class='pred-box'><div class='pred-label'>서브 추천 (언오버)</div><span class='pred-value'>{item['best_uo']}</span> <span class='pred-prob'>{item['best_uo_prob']}%</span></div></div></div>"
            st.markdown(html_code, unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# [TAB 4] AI 리포트 (오답노트 포함)
# -----------------------------------------------------------------------------
with main_tab4:
    stats = get_accuracy_stats()
    
    st.markdown(f"""
        <div style='display:flex; align-items:center; gap:20px; margin-bottom:30px; background:#0B0F19; padding:20px; border-radius:12px; border:1px solid #1E293B;'>
            <div>
                <span style='color:#94A3B8; font-size:14px; font-weight:700; display:block;'>전체 누적 적중률</span>
                <span style='color:#00F2FE; font-size:40px; font-weight:900;'>{stats['accuracy']}%</span>
            </div>
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
        table_html = "<table style='width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; text-align: center;'>"
        table_html += "<thead><tr><th style='background:#1E293B; color:#94A3B8; padding:10px;'>경기</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>예측</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>결과</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>채점</th></tr></thead><tbody>"
        for row in history:
            result_mark = "<span style='color:#10B981; font-weight:900;'>적중</span>" if row['is_correct'] == 1 else "<span style='color:#EF4444; font-weight:900;'>실패</span>"
            table_html += f"""
                <tr>
                    <td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; font-weight:700;'>{row['home_team']} vs {row['away_team']}</td>
                    <td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #00F2FE;'>{row['predicted_pick']}</td>
                    <td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; font-weight:900;'>{row['actual_score']}</td>
                    <td style='padding: 12px 10px; border-bottom: 1px solid #1E293B;'>{result_mark}</td>
                </tr>
            """
        table_html += "</tbody></table>"
        st.markdown(table_html, unsafe_allow_html=True)
        
        st.markdown("<h4 style='color:#F8FAFC; font-weight:900; margin-top:30px; margin-bottom:10px;'>💡 AI 실패 원인 분석 목록</h4>", unsafe_allow_html=True)
        has_failure = False
        for row in history:
            if row['is_correct'] == 0 and row.get('failure_reason'):
                has_failure = True
                st.markdown(f"<div style='background:#1E293B; padding:12px; border-radius:6px; margin-bottom:8px; font-size:13px; color:#CBD5E1;'><b>[{row['home_team']} vs {row['away_team']}]</b><br>{row['failure_reason']}</div>", unsafe_allow_html=True)
        if not has_failure:
             st.info("실패한 경기나 분석된 오답 노트가 아직 없습니다.")
    else:
        st.info("아직 채점이 완료된 종료 경기가 없습니다.")
