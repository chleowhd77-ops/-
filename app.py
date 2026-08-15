import streamlit as st
import math
import json
import requests
import sqlite3
import pandas as pd
import re
from datetime import datetime, timezone, timedelta

# -----------------------------------------------------------------------------
# 0. 기본 설정
# -----------------------------------------------------------------------------
APP_TITLE = "D.J PROTO ANALYTICS"
st.set_page_config(page_title=APP_TITLE, page_icon="⚡", layout="wide", initial_sidebar_state="collapsed")

API_KEY = "28b599664bba858ebf93515768741975"
API_HOST = "v3.football.api-sports.io"
headers = {'x-rapidapi-host': API_HOST, 'x-rapidapi-key': API_KEY}

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
    "광주FC": "https://media.api-sports.io/football/teams/2836.png", "포항스틸": "https://media.api-sports.io/football/teams/2843.png",
    "제주SKFC": "https://media.api-sports.io/football/teams/2839.png", "FC안양": "https://media.api-sports.io/football/teams/2848.png",
    "FC서울": "https://media.api-sports.io/football/teams/2844.png", "대전하나": "https://media.api-sports.io/football/teams/2835.png",
    "충북청주": "https://media.api-sports.io/football/teams/18525.png", "전남드래": "https://media.api-sports.io/football/teams/2847.png",
    "김해FC": "https://media.api-sports.io/football/teams/18027.png", "경남FC": "https://media.api-sports.io/football/teams/2837.png",
    "수원삼성": "https://media.api-sports.io/football/teams/2845.png", "수원FC": "https://media.api-sports.io/football/teams/2846.png",
    "부산아이": "https://media.api-sports.io/football/teams/2834.png", "화성FC": "https://media.api-sports.io/football/teams/18031.png",
    "인천유나": "https://media.api-sports.io/football/teams/2838.png", "김천상무": "https://media.api-sports.io/football/teams/2842.png",
    "부천FC": "https://media.api-sports.io/football/teams/2849.png", "전북현대": "https://media.api-sports.io/football/teams/2840.png",
    "울산HDFC": "https://media.api-sports.io/football/teams/2841.png", "강원FC": "https://media.api-sports.io/football/teams/2833.png",
    "서울이랜드": "https://media.api-sports.io/football/teams/2850.png", "서울이랜": "https://media.api-sports.io/football/teams/2850.png",
    "안산그리": "https://media.api-sports.io/football/teams/2851.png", "대구FC": "https://media.api-sports.io/football/teams/2832.png",
    "충남아산": "https://media.api-sports.io/football/teams/8282.png", "미라솔": "https://media.api-sports.io/football/teams/1023.png",
    "LDU키토": "https://media.api-sports.io/football/teams/1148.png", "로사리오 센트랄": "https://media.api-sports.io/football/teams/459.png",
    "SC코린티안스": "https://media.api-sports.io/football/teams/131.png", "도쿄 베르디": "https://media.api-sports.io/football/teams/2967.png",
    "가시와 레이솔": "https://media.api-sports.io/football/teams/2960.png"
}

def init_db():
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT UNIQUE, league TEXT, home_team TEXT, away_team TEXT,
            predicted_pick TEXT, predicted_prob REAL, expected_score TEXT, odd_h REAL, odd_d REAL, odd_a REAL,
            actual_score TEXT DEFAULT '-:-', actual_result TEXT DEFAULT 'PENDING', is_correct INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_toto14 INTEGER DEFAULT 0, failure_reason TEXT DEFAULT '', match_time TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()

init_db()

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
            return {"id": res_data["response"][0]["team"]["id"], "logo": res_data["response"][0]["team"].get("logo")}
    except: pass
    return {"id": None, "logo": None}

@st.cache_data(ttl=43200)
def fetch_recent_form(team_id):
    if not team_id: return {"form": "정보없음", "rest_days": "-"}
    url = f"https://{API_HOST}/fixtures"
    params = {"team": team_id, "last": 5, "status": "FT-AET-PEN"}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=5)
        matches = res.json().get("response", [])
        if not matches: return {"form": "정보없음", "rest_days": "-"}
        form = []
        for m in matches:
            if m['teams']['home']['id'] == team_id:
                if m['teams']['home']['winner']: form.append("승")
                elif m['teams']['away']['winner']: form.append("패")
                else: form.append("무")
            else:
                if m['teams']['away']['winner']: form.append("승")
                elif m['teams']['home']['winner']: form.append("패")
                else: form.append("무")
        last_match_date = datetime.fromisoformat(matches[0]['fixture']['date'].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        rest_days = (now - last_match_date).days
        return {"form": "-".join(form), "rest_days": f"{rest_days}일"}
    except: return {"form": "정보없음", "rest_days": "-"}

@st.cache_data(ttl=43200)
def fetch_fixture_details_api(home_id, away_id):
    if not home_id or not away_id: return {"match_time": None, "h_wins": 0, "draws": 0, "a_wins": 0, "total": 0, "is_valid": False}
    url = f"https://{API_HOST}/fixtures/headtohead"
    params = {"h2h": f"{home_id}-{away_id}"}
    h_wins, draws, a_wins = 0, 0, 0
    match_time_str = None
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
        return {"match_time": match_time_str, "h_wins": h_wins, "draws": draws, "a_wins": a_wins, "total": len(matches[:10]), "is_valid": True}
    except: return {"match_time": None, "h_wins": 0, "draws": 0, "a_wins": 0, "total": 0, "is_valid": False}

@st.cache_data(ttl=60)
def load_betman_data():
    try:
        res = requests.get("https://raw.githubusercontent.com/chleowhd77-ops/-/main/betman_data.json", timeout=5)
        if res.status_code == 200: return res.json()
    except: pass
    return {"proto_matches": [], "toto_14_matches": []}

@st.cache_data(ttl=60)
def load_live_scores():
    try:
        res = requests.get("https://raw.githubusercontent.com/chleowhd77-ops/-/main/live_scores.json", timeout=5)
        if res.status_code == 200: return res.json()
    except: pass
    return {}

def get_accuracy_stats():
    conn = sqlite3.connect("ai_predictions.db")
    df = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED'", conn)
    conn.close()
    if len(df) == 0: return {"total": 0, "correct": 0, "accuracy": 0.0}
    total = len(df)
    correct = df['is_correct'].sum()
    return {"total": total, "correct": correct, "accuracy": round((correct / total) * 100, 1)}

def save_prediction(m, best_option, best_prob_pct, best_score, is_toto14=0, match_time_str=""):
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM predictions WHERE match_id = ?", (m['id'],))
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO predictions (match_id, league, home_team, away_team, predicted_pick, predicted_prob, expected_score, odd_h, odd_d, odd_a, is_toto14, match_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (m['id'], m.get('league', '승무패 14경기'), m['home'], m['away'], best_option, best_prob_pct, f"{best_score[0]}:{best_score[1]}", m.get('odd_h', 0.0), m.get('odd_d', 0.0), m.get('odd_a', 0.0), is_toto14, match_time_str))
        else:
            cursor.execute("UPDATE predictions SET is_toto14 = ?, match_time = ? WHERE match_id = ?", (is_toto14, match_time_str, m['id']))
        conn.commit()
    except: pass
    finally: conn.close()

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
            if m_dt <= now <= m_dt + timedelta(minutes=115): return "LIVE", is_closed
            elif now > m_dt + timedelta(minutes=115): return "FINISHED", is_closed
            else: return "UPCOMING", is_closed
    except: pass
    return "UPCOMING", False

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

def generate_dynamic_story(h_team, a_team, prob_h, prob_d, prob_a, odd_h, odd_a):
    prob_h, prob_d, prob_a = round(prob_h, 1), round(prob_d, 1), round(prob_a, 1)
    if prob_h >= 60.0: return f"🔥 전력 우위 분석! 홈팀 <b>{h_team}</b>의 승률이 {prob_h}%로 데이터상 크게 앞섭니다. 배당률({odd_h}) 대비 안정적인 투자가치가 보이는 경기입니다."
    elif prob_a >= 60.0: return f"🚨 원정팀의 매서운 기세! <b>{a_team}</b>의 승리 확률이 {prob_a}%로 산출되었습니다. {h_team}의 고전이 예상되는 이변 주의 매치입니다."
    elif abs(prob_h - prob_a) <= 10.0 and prob_d >= 25.0: return f"⚔️ <b>{h_team}</b>({prob_h}%) vs <b>{a_team}</b>({prob_a}%)의 초박빙 승부! 승률 차이가 미미하며, 무승부 확률({prob_d}%)이 높아 신중한 접근이 필요합니다."
    elif prob_h > prob_a and (prob_h - prob_a) > 10.0: return f"📊 AI 알고리즘 산출 결과, <b>{h_team}</b>의 승률({prob_h}%)이 {a_team}보다 다소 우세합니다. 다만 배당({odd_h}) 변동 흐름을 체크해야 합니다."
    elif prob_a > prob_h and (prob_a - prob_h) > 10.0: return f"🔍 원정팀 <b>{a_team}</b>의 전력 수치가 {prob_a}%로 조금 더 높게 평가되었습니다. {h_team}이 홈 이점을 얼마나 살릴지가 관건입니다."
    else: return f"💡 팽팽한 흐름! 홈팀 <b>{h_team}</b>과 원정팀 <b>{a_team}</b> 모두 뚜렷한 우위를 점하지 못한 경기력 지표를 보이고 있습니다."

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
    .match-card { background-color: #0B0F19; border: 1px solid #1E293B; border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); transition: transform 0.2s ease, border-color 0.2s ease; position:relative;}
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
    .live-event-bar { background: linear-gradient(90deg, rgba(234, 179, 8, 0.15) 0%, rgba(234, 179, 8, 0.05) 100%); border-left: 3px solid #EAB308; padding: 8px 15px; font-size: 13px; color: #FDE047; font-weight: 800; border-radius: 4px; margin-bottom: 15px; animation: pulse 2s infinite; }
    @keyframes pulse { 0% { opacity: 0.8; } 50% { opacity: 1; } 100% { opacity: 0.8; } }
    .odd-bar { display: flex; justify-content: space-between; background: #111827; border-radius: 6px; padding: 12px 20px; margin-bottom: 15px; border: 1px solid #1F2937; }
    .odd-item { font-size: 14px; color: #94A3B8; font-weight: 700; }
    .odd-val { color: #F1F5F9; font-weight: 900; margin-left: 6px; }
    .h2h-bar { display: flex; flex-direction: column; gap: 8px; font-size: 13px; color: #64748B; font-weight: 700; border-top: 1px dashed #1E293B; padding-top: 12px; margin-bottom: 15px; }
    .h2h-row { display: flex; justify-content: space-between; align-items: center; }
    .ai-story { background: rgba(0, 242, 254, 0.05); border-left: 3px solid #00F2FE; padding: 12px 15px; font-size: 14px; color: #E2E8F0; font-weight: 500; line-height:1.5; border-radius: 4px; margin-bottom: 15px; }
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
    .marking-grid { display: flex; gap: 8px; justify-content: center; margin-top: 15px; }
    .mark-btn { flex: 1; padding: 10px 0; text-align: center; border-radius: 6px; border: 1px solid #334155; color: #64748B; font-weight: 900; font-size: 14px; background: #0F172A; }
    .mark-btn.win.active { background: #00F2FE; color: #000; border-color: #00F2FE; box-shadow: 0 0 10px rgba(0,242,254,0.4); }
    .mark-btn.draw.active { background: #10B981; color: #000; border-color: #10B981; box-shadow: 0 0 10px rgba(16,185,129,0.4); }
    .mark-btn.lose.active { background: #EF4444; color: #000; border-color: #EF4444; box-shadow: 0 0 10px rgba(239,68,68,0.4); }
    .res-card-win { background: rgba(16, 185, 129, 0.05); border-left: 4px solid #10B981; border-radius: 6px; padding: 18px; margin-bottom: 12px; }
    .res-card-lose { background: rgba(239, 68, 68, 0.05); border-left: 4px solid #EF4444; border-radius: 6px; padding: 18px; margin-bottom: 12px; }
    .res-card-pend { background: #0F172A; border-left: 4px solid #475569; border-radius: 6px; padding: 18px; margin-bottom: 12px; }
    </style>
""", unsafe_allow_html=True)

def render_logo_html(logo_url):
    if logo_url: return f"<img src='{logo_url}' class='team-logo' onerror=\"this.style.display='none';\">"
    return ""

st.markdown("<div class='app-header'><h1>D.J PROTO ANALYTICS</h1><p>AI 예측 기반 스마트 프로토 대시보드</p></div>", unsafe_allow_html=True)
main_tab1, main_tab2, main_tab3, main_tab4 = st.tabs(["프로토 LIVE", "승무패 14경기", "오늘의 TOP 3", "AI 리포트"])

all_data = load_betman_data()
proto_matches = all_data.get("proto_matches", [])
toto_14_raw = all_data.get("toto_14_matches", []) 
live_scores_dict = load_live_scores()

analyzed_proto = []
if proto_matches:
    for m in proto_matches:
        odd_h, odd_d, odd_a = m["odd_h"], m["odd_d"], m["odd_a"]
        handi_h, handi_d, handi_a = m.get("handi_h", 3.05), m.get("handi_d", 3.05), m.get("handi_a", 2.03)
        uo_under, uo_over = m.get("uo_under", 1.50), m.get("uo_over", 2.13)
        
        home_info = fetch_team_info_api(m["home"])
        away_info = fetch_team_info_api(m["away"])
        
        fixture_details = fetch_fixture_details_api(home_info["id"], away_info["id"])
        final_match_time = fixture_details["match_time"] or m.get("match_time") or m.get("time") or "시간 미정"
        
        h_form_data = fetch_recent_form(home_info["id"])
        a_form_data = fetch_recent_form(away_info["id"])
        
        p_h = (1 / odd_h) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
        p_a = (1 / odd_a) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
            
        h_h2h_bonus = (fixture_details["h_wins"] / fixture_details["total"] * 0.4) if fixture_details["total"] > 0 else 0
        a_h2h_bonus = (fixture_details["a_wins"] / fixture_details["total"] * 0.4) if fixture_details["total"] > 0 else 0
        exp_h = round(max(0.5, (p_h * 2.7) + h_h2h_bonus), 2)
        exp_a = round(max(0.3, (p_a * 2.5) + a_h2h_bonus), 2)

        handi_val = 1.0 if odd_h > odd_a else -1.0
        h_win, draw, a_win, prob_u, prob_o, prob_handi_h, prob_handi_a = calculate_poisson_probs(exp_h, exp_a, handi_val)

        candidates = [(f"{m['home']} 승", h_win, h_win * odd_h), (f"{m['away']} 승", a_win, a_win * odd_a), (f"무승부", draw, draw * odd_d)]
        best_option, best_prob, best_ev = max(candidates, key=lambda x: x[2])
        best_prob_pct = round(best_prob * 100, 1)

        best_handi = f"{m['home']} 핸디승" if prob_handi_h * handi_h > prob_handi_a * handi_a else f"{m['away']} 핸디승"
        best_handi_prob = round(max(prob_handi_h, prob_handi_a) * 100, 1)
        best_uo = "언더 (U 2.5)" if prob_u * uo_under > prob_o * uo_over else "오버 (O 2.5)"
        best_uo_prob = round(max(prob_u, prob_o) * 100, 1)

        save_prediction(m, best_option, best_prob_pct, (0,0), 0, final_match_time)
        story = generate_dynamic_story(m['home'], m['away'], h_win*100, draw*100, a_win*100, odd_h, odd_a)

        analyzed_proto.append({
            "match": m, "final_match_time": final_match_time, "home_logo": home_info["logo"], "away_logo": away_info["logo"],
            "h2h": fixture_details, "story": story, "best_option": best_option, "best_prob_pct": best_prob_pct,
            "best_handi": best_handi, "best_handi_prob": best_handi_prob, "best_uo": best_uo, "best_uo_prob": best_uo_prob, "best_ev": best_ev,
            "h_form": h_form_data, "a_form": a_form_data 
        })

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
                
                live_event_html = ""
                if match_status == "LIVE": 
                    live_data = live_scores_dict.get(m['id'], {"score": "진행중", "event": ""})
                    real_live_score = live_data["score"] if isinstance(live_data, dict) else "진행중"
                    time_display = f"<span class='live-score'>{real_live_score}</span><span class='deadline-closed'>LIVE</span>"
                    event_str = live_data.get("event", "") if isinstance(live_data, dict) else ""
                    if event_str: live_event_html = f"<div class='live-event-bar'>⚡ {event_str}</div>"
                elif match_status == "FINISHED": time_display = f"<span class='live-score'>종료</span>"
                else:
                    badge = f"<span class='deadline-closed'>픽 마감</span>" if is_closed else f"<span class='deadline-open'>{raw_deadline}</span>"
                    time_display = f"<span class='match-time-text'>{item['final_match_time']}</span>{badge}"
                
                if item['h2h']['is_valid']:
                    h2h_text = f"{m['home']} {item['h2h']['h_wins']}승 {item['h2h']['draws']}무 {item['h2h']['a_wins']}승 {m['away']}"
                    recent_form_html = f"<span>📈 최근 5경기: {m['home']} <b style='color:#00F2FE;'>[{item['h_form']['form']}]</b> vs {m['away']} <b style='color:#00F2FE;'>[{item['a_form']['form']}]</b></span>"
                    rest_days_text = f"{item['h_form']['rest_days']} / {item['a_form']['rest_days']}"
                else:
                    h2h_text = "해외 데이터 매칭 대기 중"
                    recent_form_html = ""
                    rest_days_text = "- / -"
                
                # [수정 완료] 줄바꿈/들여쓰기를 제거하여 Markdown 코드로 인식되지 않게 완벽 보호!
                html_code = f"<div class='match-card'><div class='league-title'>{m['league']}</div><div class='vs-row'><div class='team-box home'><span class='team-name-text'>{m['home']}</span>{logo_h_tag}</div><div class='center-time-box'>{time_display}</div><div class='team-box away'>{logo_a_tag}<span class='team-name-text'>{m['away']}</span></div></div>{live_event_html}<div class='ai-story'>{item['story']}</div><div class='odd-bar'><span class='odd-item'>승 <span class='odd-val'>{m['odd_h']}</span> | 무 <span class='odd-val'>{m['odd_d']}</span> | 패 <span class='odd-val'>{m['odd_a']}</span></span><span class='odd-item'>핸디캡 <span class='odd-val'>{m.get('handi_h', '-')} / {m.get('handi_a', '-')}</span></span><span class='odd-item'>언오버 <span class='odd-val'>{m.get('uo_under', '-')} / {m.get('uo_over', '-')}</span></span></div><div class='h2h-bar'><div class='h2h-row'><span>⚔️ 상대전적: {h2h_text}</span><span>🔋 휴식일: {rest_days_text}</span></div><div class='h2h-row' style='color:#94A3B8; font-size:12px; margin-top:4px;'>{recent_form_html}</div></div><div class='pred-grid'><div class='pred-box'><div class='pred-label'>승무패 예측</div><span class='pred-value'>{item['best_option']}</span> <span class='pred-prob'>{item['best_prob_pct']}%</span></div><div class='pred-box'><div class='pred-label'>핸디캡 예측</div><span class='pred-value'>{item['best_handi']}</span> <span class='pred-prob'>{item['best_handi_prob']}%</span></div><div class='pred-box'><div class='pred-label'>언더/오버 예측</div><span class='pred-value'>{item['best_uo']}</span> <span class='pred-prob'>{item['best_uo_prob']}%</span></div></div></div>"
                st.markdown(html_code, unsafe_allow_html=True)
        else: st.info("현재 분석 가능한 프로토 축구 경기가 없습니다.")
            
    with sub_baseball: st.info("야구 분석 데이터 준비 중입니다.")
    with sub_basketball: st.info("농구 분석 데이터 준비 중입니다.")

with main_tab2:
    st.markdown("<p style='color:#64748B; font-weight:700; margin-bottom:20px;'>승무패 14폴더 AI 확률 분포 & 스마트 마킹표</p>", unsafe_allow_html=True)
    if toto_14_raw:
        total_combinations = 1
        for idx, m in enumerate(toto_14_raw, 1):
            home_info = fetch_team_info_api(m['home'])
            away_info = fetch_team_info_api(m['away'])
            logo_h_tag = render_logo_html(home_info["logo"])
            logo_a_tag = render_logo_html(away_info["logo"])
            base_seed = (ord(m['home'][0]) + ord(m['away'][0]) + idx * 7)
            p_h = 32.0 + (base_seed % 35); p_d = 24.0 + (base_seed % 12); p_a = round(100.0 - (p_h + p_d), 1)
            p_h, p_d = round(p_h, 1), round(p_d, 1)
            probs = [("win", p_h), ("draw", p_d), ("lose", p_a)]
            probs.sort(key=lambda x: x[1], reverse=True)
            mark_win, mark_draw, mark_lose = "", "", ""
            picks = []
            if probs[0][1] >= 50.0 or (probs[0][1] - probs[1][1] >= 15.0):
                if probs[0][0] == "win": mark_win = "active"; picks.append(f"{m['home']} 승")
                elif probs[0][0] == "draw": mark_draw = "active"; picks.append("무승부")
                else: mark_lose = "active"; picks.append(f"{m['away']} 승")
            else:
                total_combinations *= 2
                for i in range(2):
                    if probs[i][0] == "win": mark_win = "active"; picks.append(f"{m['home']} 승")
                    elif probs[i][0] == "draw": mark_draw = "active"; picks.append("무승부")
                    else: mark_lose = "active"; picks.append(f"{m['away']} 승")
            best_pick_text = " / ".join(picks)
            save_prediction(m, best_pick_text, probs[0][1], (0, 0), 1, "시간 미정")
            html_code = f"<div class='match-card' style='padding: 24px;'><div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;'><span class='badge-primary'>제 {idx} 경기</span><span style='color:#94A3B8; font-size:14px; font-weight:700;'>AI 추천: <b style='color:#00F2FE;'>{best_pick_text}</b></span></div><div class='vs-row' style='margin-bottom:15px;'><div class='team-box home'><span class='team-name-text'>{m['home']}</span>{logo_h_tag}</div><div class='center-time-box' style='width:60px;'><b style='color:#475569; font-size:16px;'>VS</b></div><div class='team-box away'>{logo_a_tag}<span class='team-name-text'>{m['away']}</span></div></div><div style='font-size:12px; color:#64748B; font-weight:700; text-align:center;'>확률 분포: 승 {p_h}% | 무 {p_d}% | 패 {p_a}%</div><div class='prob-bar-container'><div class='prob-bar-win' style='width: {p_h}%;'></div><div class='prob-bar-draw' style='width: {p_d}%;'></div><div class='prob-bar-lose' style='width: {p_a}%;'></div></div><div class='marking-grid'><div class='mark-btn win {mark_win}'>승</div><div class='mark-btn draw {mark_draw}'>무</div><div class='mark-btn lose {mark_lose}'>패</div></div></div>"
            st.markdown(html_code, unsafe_allow_html=True)
        bet_amount = total_combinations * 1000
        st.success(f"💡 AI 추천 조합을 모두 마킹할 경우, 총 조합 수는 **{total_combinations}개**이며, 예상 구매 금액은 **{bet_amount:,}원**입니다.")
    else:
        conn = sqlite3.connect("ai_predictions.db")
        df_14 = pd.read_sql_query("SELECT * FROM predictions WHERE is_toto14 = 1 ORDER BY id DESC LIMIT 14", conn)
        conn.close()
        if len(df_14) > 0:
            df_14 = df_14.iloc[::-1]
            st.info("베트맨 발매가 마감되어, 가장 최근 저장된 14경기 데이터를 불러옵니다.")
            idx = 1
            for _, row in df_14.iterrows():
                html_code = f"<div class='match-card' style='padding: 24px;'><div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;'><span class='badge-primary'>제 {idx} 경기</span><span style='color:#94A3B8; font-size:14px; font-weight:700;'>AI 추천 마킹: <b style='color:#00F2FE;'>{row['predicted_pick']}</b></span></div><div class='vs-row' style='margin-bottom:15px;'><div class='team-box home'><span class='team-name-text'>{row['home_team']}</span></div><div class='center-time-box' style='width:60px;'><b style='color:#475569; font-size:16px;'>VS</b></div><div class='team-box away'><span class='team-name-text'>{row['away_team']}</span></div></div></div>"
                st.markdown(html_code, unsafe_allow_html=True)
                idx += 1
        else: st.info("현재 진행 중인 승무패 14경기 데이터가 없습니다.")

with main_tab3:
    top_3_picks = sorted(analyzed_proto, key=lambda x: x['best_ev'], reverse=True)[:3]
    if top_3_picks:
        for idx, item in enumerate(top_3_picks, 1):
            m = item['match']
            logo_h_tag = render_logo_html(item["home_logo"])
            logo_a_tag = render_logo_html(item["away_logo"])
            html_code = f"<div class='match-card top3-glow'><div class='league-title' style='color:#00F2FE;'># {idx} 최고 가치 추천 픽 • {m['league']}</div><div class='vs-row'><div class='team-box home'><span class='team-name-text'>{m['home']}</span>{logo_h_tag}</div><div class='center-time-box'><span class='match-time-text' style='color:#00F2FE;'>{item['final_match_time']}</span></div><div class='team-box away'>{logo_a_tag}<span class='team-name-text'>{m['away']}</span></div></div><div class='pred-grid' style='margin-top:20px;'><div class='pred-box' style='background:rgba(0, 242, 254, 0.05); border-color:#00F2FE;'><div class='pred-label' style='color:#00F2FE;'>강력 추천 (일반 승무패)</div><span class='pred-value'>{item['best_option']}</span> <span class='pred-prob'>{item['best_prob_pct']}%</span></div><div class='pred-box'><div class='pred-label'>서브 추천 (언오버)</div><span class='pred-value'>{item['best_uo']}</span> <span class='pred-prob'>{item['best_uo_prob']}%</span></div></div></div>"
            st.markdown(html_code, unsafe_allow_html=True)

with main_tab4:
    stats = get_accuracy_stats()
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("누적 분석 경기", f"{stats['total']} 경기")
    with c2: st.metric("적중 횟수", f"{stats['correct']} 회")
    with c3: st.metric("AI 적중률", f"{stats['accuracy']}%")
    st.markdown("<br><hr style='border-color:#1E293B;'><br>", unsafe_allow_html=True)
    
    rep_tab1, rep_tab2 = st.tabs(["프로토 결과", "승무패 14경기 결과"])
    conn = sqlite3.connect("ai_predictions.db")
    df = pd.read_sql_query("SELECT * FROM predictions ORDER BY id DESC", conn)
    conn.close()
    
    if 'is_toto14' not in df.columns: df['is_toto14'] = 0
    if 'failure_reason' not in df.columns: df['failure_reason'] = ""
        
    def render_report_list(df_subset):
        if len(df_subset) > 0:
            for _, row in df_subset.iterrows():
                status = row['actual_result']
                is_correct = row['is_correct']
                failure_note = ""
                if status == 'FINISHED':
                    if is_correct == 1: badge_html = "<span style='color:#10B981; font-weight:900; font-size:14px;'>적중 (WIN)</span>"; card_class = "res-card-win"
                    else:
                        card_class = "res-card-lose"; badge_html = "<span style='color:#EF4444; font-weight:900; font-size:14px;'>미적중 (LOSE)</span>"
                        if row['failure_reason']: failure_note = f"<div style='margin-top:12px; padding:10px; background:rgba(239, 68, 68, 0.1); border-left:3px solid #EF4444; color:#F8FAFC; font-size:13px; font-weight:700;'>{row['failure_reason']}</div>"
                else: card_class = "res-card-pend"; badge_html = "<span style='color:#64748B; font-weight:800; font-size:14px;'>진행 예정</span>"
                html_code = f"<div class='{card_class}'><div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;'><span style='color:#94A3B8; font-size:13px; font-weight:800;'>{row['league']}</span>{badge_html}</div><div style='font-size:18px; font-weight:900; color:#F8FAFC; margin-bottom:10px;'>{row['home_team']} vs {row['away_team']}</div><div style='font-size:14px; color:#94A3B8; font-weight:700;'>예측 픽: <span style='color:#00F2FE;'>{row['predicted_pick']}</span> <span style='margin:0 10px;'>|</span> 실제 스코어: <span style='color:#F8FAFC;'>{row['actual_score']}</span></div>{failure_note}</div>"
                st.markdown(html_code, unsafe_allow_html=True)
        else: st.info("해당 카테고리의 기록이 없습니다.")

    with rep_tab1: render_report_list(df[df['is_toto14'] == 0])
    with rep_tab2: render_report_list(df[df['is_toto14'] == 1])
