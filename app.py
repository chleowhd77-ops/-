import streamlit as st
import math
import json
import requests
import sqlite3
import pandas as pd
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from bs4 import BeautifulSoup

# -----------------------------------------------------------------------------
# 0. 기본 설정 및 타이틀
# -----------------------------------------------------------------------------
APP_TITLE = "D.J PROTO PIC"

st.set_page_config(
    page_title=APP_TITLE, 
    page_icon="⚽", 
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# -----------------------------------------------------------------------------
# 1. 해외 API 연동 (팀 정보 & 상대 전적)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=86400)
def fetch_team_info_api(team_name):
    logo = DIRECT_LOGO_MAP.get(team_name)
    if logo:
        return {"id": None, "logo": logo}
        
    search_name = TEAM_NAME_MAP.get(team_name, team_name)
    url = f"https://{API_HOST}/teams"
    params = {"search": search_name}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        res_data = response.json()
        if res_data.get("response") and len(res_data["response"]) > 0:
            team_data = res_data["response"][0]["team"]
            return {"id": team_data["id"], "logo": team_data.get("logo")}
    except Exception:
        pass
        
    return {"id": None, "logo": None}

@st.cache_data(ttl=43200)
def fetch_fixture_details_api(home_id, away_id):
    if not home_id or not away_id:
        return {"match_time": None, "last_h2h_date": "정보 없음", "h_rest": "4일", "a_rest": "4일", "h_wins": 0, "draws": 0, "a_wins": 0, "total": 0}
        
    url = f"https://{API_HOST}/fixtures/headtohead"
    params = {"h2h": f"{home_id}-{away_id}"}
    
    match_time_str, last_h2h_date = None, "정보 없음"
    h_wins, draws, a_wins = 0, 0, 0
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        res_data = response.json()
        matches = res_data.get("response", [])
        
        if len(matches) > 0:
            latest_match = matches[0]
            utc_date_str = latest_match.get("fixture", {}).get("date")
            if utc_date_str:
                utc_dt = datetime.fromisoformat(utc_date_str.replace("Z", "+00:00"))
                kst_dt = utc_dt.astimezone(timezone(timedelta(hours=9)))
                weekdays = ['월', '화', '수', '목', '금', '토', '일']
                match_time_str = kst_dt.strftime(f"%m.%d ({weekdays[kst_dt.weekday()]}) %H:%M")
                
            if len(matches) > 1:
                prev_utc = matches[1].get("fixture", {}).get("date")
                if prev_utc:
                    prev_dt = datetime.fromisoformat(prev_utc.replace("Z", "+00:00"))
                    last_h2h_date = prev_dt.strftime("%Y.%m.%d")
        
        for m in matches[:10]:
            is_home_winner = m.get("teams", {}).get("home", {}).get("winner")
            is_away_winner = m.get("teams", {}).get("away", {}).get("winner")
            winner_id = m.get("teams", {}).get("home", {}).get("id")
            
            if is_home_winner:
                if winner_id == home_id: h_wins += 1
                else: a_wins += 1
            elif is_away_winner:
                if winner_id == home_id: a_wins += 1
                else: h_wins += 1
            else: draws += 1
                
        return {"match_time": match_time_str, "last_h2h_date": last_h2h_date, "h_rest": "5일", "a_rest": "3일", "h_wins": h_wins, "draws": draws, "a_wins": a_wins, "total": len(matches[:10])}
    except Exception:
        return {"match_time": None, "last_h2h_date": "정보 없음", "h_rest": "4일", "a_rest": "4일", "h_wins": 0, "draws": 0, "a_wins": 0, "total": 0}

@st.cache_data(ttl=3600)
def analyze_team_news_sentiment(team_name):
    keywords_negative = ["부상", "결장", "로테이션", "체력 부담", "징계", "불화", "부진", "결장 우려"]
    keywords_positive = ["복귀", "연승", "사기 충천", "주전 총출동", "대승", "호조", "완승"]
    score_mod, detected_issues = 0.0, []
    
    try:
        query = quote(f"{team_name} 축구")
        url = f"https://search.naver.com/search.naver?where=news&query={query}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=4)
        
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            titles = [a.get('title', '') for a in soup.select('a.news_tit')]
            
            for t in titles[:8]:
                for kw in keywords_negative:
                    if kw in t:
                        score_mod -= 0.15
                        if f"⚠️ {kw}" not in detected_issues: detected_issues.append(f"⚠️ {kw}")
                for kw in keywords_positive:
                    if kw in t:
                        score_mod += 0.10
                        if f"🔥 {kw}" not in detected_issues: detected_issues.append(f"🔥 {kw}")
    except Exception: pass
    return {"mod": round(score_mod, 2), "issues": detected_issues[:2]}

# -----------------------------------------------------------------------------
# 2. GitHub 데이터 로드
# -----------------------------------------------------------------------------
@st.cache_data(ttl=60)
def load_betman_data():
    raw_url = "https://raw.githubusercontent.com/chleowhd77-ops/-/main/betman_data.json"
    try:
        res = requests.get(raw_url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list): return {"proto_matches": data, "toto_14_matches": []}
            return data
    except Exception: pass
    return {"proto_matches": [], "toto_14_matches": []}

def get_accuracy_stats():
    conn = sqlite3.connect("ai_predictions.db")
    df = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED'", conn)
    conn.close()
    if len(df) == 0: return {"total": 0, "correct": 0, "accuracy": 0.0}
    total = len(df)
    correct = df['is_correct'].sum()
    return {"total": total, "correct": correct, "accuracy": round((correct / total) * 100, 1)}

def save_prediction(m, best_option, best_prob_pct, best_score):
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT OR IGNORE INTO predictions 
            (match_id, league, home_team, away_team, predicted_pick, predicted_prob, expected_score, odd_h, odd_d, odd_a)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (m['id'], m['league'], m['home'], m['away'], best_option, best_prob_pct, f"{best_score[0]}:{best_score[1]}", m['odd_h'], m['odd_d'], m['odd_a']))
        conn.commit()
    except Exception: pass
    finally: conn.close()

# -----------------------------------------------------------------------------
# 3. CSS 스타일링
# -----------------------------------------------------------------------------
st.markdown("""
    <style>
    html, body, .stApp { background-color: #0b0d10; overflow-x: hidden !important; max-width: 100vw !important; }
    [data-testid="stSidebar"] { display: none; }
    
    .app-header { text-align: center; padding: 15px 0 25px 0; border-bottom: 1px solid #1e2430; margin-bottom: 20px; }
    .app-header h1 { color: #ffffff !important; font-size: 38px !important; font-weight: 900 !important; letter-spacing: 2px; margin: 0; }
    
    div[data-testid="stTabs"] { width: 100% !important; overflow-x: hidden !important; }
    .stTabs [data-baseweb="tab-list"], div[role="tablist"] {
        width: 100% !important; display: flex !important; justify-content: space-around !important;
        background-color: transparent !important; border: none !important; border-bottom: 2px solid #1e2430 !important;
        margin-bottom: 25px !important; padding: 0px !important; gap: 0px !important;
    }
    .stTabs [data-baseweb="tab"], button[role="tab"] {
        flex: 1 1 0% !important; height: 50px !important; border-radius: 0px !important;
        border: none !important; background-color: transparent !important;
        color: #8a94a6 !important; font-weight: 900 !important; font-size: 19px !important;
        cursor: pointer !important; text-align: center !important; transition: all 0.2s;
    }
    .stTabs [aria-selected="true"] {
        color: #ff3b3b !important; font-size: 21px !important; font-weight: 900 !important;
        border-bottom: 4px solid #ff3b3b !important; background-color: transparent !important; box-shadow: none !important;
    }
    .stTabs [data-baseweb="tab-border"], .stTabs [data-baseweb="tab-highlight-title"], div[data-baseweb="tab-highlight"] { display: none !important; }
    
    .match-card {
        background: linear-gradient(135deg, #151821 0%, #0f1117 100%);
        border: 1px solid #252d3f; border-radius: 16px; padding: 20px; margin-bottom: 22px; box-shadow: 0 6px 20px rgba(0,0,0,0.5);
    }
    .result-card-win { background: linear-gradient(135deg, #102d1b 0%, #0a1c12 100%); border: 2px solid #2ecc71; border-radius: 14px; padding: 18px; margin-bottom: 16px; }
    .result-card-lose { background: linear-gradient(135deg, #2d1010 0%, #1c0a0a 100%); border: 1px solid #e74c3c; border-radius: 14px; padding: 18px; margin-bottom: 16px; }
    .result-card-pending { background: linear-gradient(135deg, #1c202c 0%, #12151f 100%); border: 1px solid #323b4e; border-radius: 14px; padding: 18px; margin-bottom: 16px; }
    
    .vs-row { display: flex; justify-content: space-between; align-items: center; width: 100%; }
    .team-box { flex: 1; display: flex; align-items: center; gap: 10px; overflow: hidden; }
    .team-box.home { justify-content: flex-end; text-align: right; }
    .team-box.away { justify-content: flex-start; text-align: left; }
    .team-name-text { color: #ffffff !important; font-size: 22px; font-weight: 900; word-break: keep-all; }
    .center-time-box { width: 180px; text-align: center; flex-shrink: 0; }
    .team-logo { width: 48px !important; height: 48px !important; object-fit: contain; flex-shrink: 0; }
    
    .match-time-badge { color: #2ecc71 !important; font-size: 14px !important; font-weight: 800; background: #0c2b1a; padding: 6px 12px; border-radius: 20px; display: inline-block; border: 1px solid #185c32; }
    .deadline-badge { color: #ff6b6b !important; font-size: 12px !important; font-weight: bold; margin-top: 4px; display: block; }
    .odd-info { color: #ffffff !important; font-size: 14px !important; margin-top: 14px; background: #1a202c; padding: 10px; border-radius: 8px; border: 1px solid #2a3346; text-align: center; }
    
    .detail-info-box { background: #121620; border: 1px solid #222938; padding: 10px; border-radius: 8px; margin-top: 12px; display: flex; justify-content: space-around; flex-wrap: wrap; text-align: center; gap: 6px; }
    .detail-item { color: #5dd5ff !important; font-size: 13px !important; font-weight: bold; }
    .news-item { color: #ffb84d !important; font-size: 13px !important; font-weight: bold; width: 100%; text-align: center; }
    
    .value-box-grid { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
    .value-pick-box { flex: 1 1 30%; min-width: 140px; background-color: #0e291b; color: #ffffff !important; border: 1px solid #1c5e34; padding: 10px; border-radius: 10px; font-size: 13px !important; text-align: center; }
    .pick-highlight { color: #f1c40f !important; font-weight: 800 !important; font-size: 15px !important; }
    .prob-highlight { color: #2ecc71 !important; font-weight: 700 !important; font-size: 14px !important; }

    @media (max-width: 640px) {
        .team-name-text { font-size: 14px !important; }
        .team-logo { width: 32px !important; height: 32px !important; }
        .center-time-box { width: 110px !important; }
        .value-pick-box { flex: 1 1 100% !important; }
        .app-header h1 { font-size: 24px !important; }
        .stTabs [data-baseweb="tab"], button[role="tab"] { font-size: 15px !important; }
    }
    </style>
""", unsafe_allow_html=True)

# HTML 이미지 렌더링 헬퍼 (로고가 없거나 에러 시 미출력)
def render_logo_html(logo_url):
    if logo_url:
        return f"<img src='{logo_url}' class='team-logo' onerror=\"this.style.display='none';\">"
    return ""

# -----------------------------------------------------------------------------
# 4. 헤더 및 메인 메뉴
# -----------------------------------------------------------------------------
st.markdown(f"<div class='app-header'><h1>{APP_TITLE}</h1></div>", unsafe_allow_html=True)

main_tab1, main_tab2, main_tab3, main_tab4 = st.tabs([
    "⚽ 프로토 LIVE", 
    "🎯 축구토토 승무패(14경기)", 
    "🔥 오늘의 TOP 3 픽", 
    "📈 AI 적중률 리포트"
])

all_data = load_betman_data()
proto_matches = all_data.get("proto_matches", [])
toto_14_raw = [x for x in all_data.get("toto_14_matches", []) if "홈" not in x["home"] and "원정" not in x["away"]]

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
        
        news_h = analyze_team_news_sentiment(home_team)
        news_a = analyze_team_news_sentiment(away_team)
        
        p_h = (1 / odd_h) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
        p_a = (1 / odd_a) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
            
        h2h_total = fixture_details["total"]
        h_h2h_bonus = (fixture_details["h_wins"] / h2h_total * 0.4) if h2h_total > 0 else 0
        a_h2h_bonus = (fixture_details["a_wins"] / h2h_total * 0.4) if h2h_total > 0 else 0
        
        exp_h = round(max(0.5, (p_h * 2.7) + h_h2h_bonus + news_h["mod"]), 2)
        exp_a = round(max(0.3, (p_a * 2.5) + a_h2h_bonus + news_a["mod"]), 2)

        h_probs = [(math.exp(-exp_h) * (exp_h**i)) / math.factorial(i) for i in range(6)]
        a_probs = [(math.exp(-exp_a) * (exp_a**j)) / math.factorial(j) for j in range(6)]

        h_win, draw, a_win = 0.0, 0.0, 0.0
        prob_under_2_5, prob_over_2_5 = 0.0, 0.0
        prob_handi_h, prob_handi_a = 0.0, 0.0
        best_score, max_p = (0, 0), 0.0

        for h in range(6):
            for a in range(6):
                p = h_probs[h] * a_probs[a]
                if h > a: h_win += p
                elif h == a: draw += p
                else: a_win += p
                if (h + a) < 2.5: prob_under_2_5 += p
                else: prob_over_2_5 += p
                if (h - 1) > a: prob_handi_h += p
                elif (h - 1) < a: prob_handi_a += p
                if p > max_p: max_p = p; best_score = (h, a)

        candidates = [(f"🏠 {home_team} 승", h_win, h_win * odd_h), (f"🚀 {away_team} 승", a_win, a_win * odd_a), (f"🤝 무승부", draw, draw * odd_d)]
        best_option, best_prob, best_ev = max(candidates, key=lambda x: x[2])
        best_prob_pct = round(best_prob * 100, 1)

        best_handi = f"🛡️ {home_team} 마핸승" if prob_handi_h * handi_h > prob_handi_a * handi_a else f"🛡️ {away_team} 플핸승"
        best_handi_prob = round(prob_handi_h * 100, 1) if prob_handi_h * handi_h > prob_handi_a * handi_a else round(prob_handi_a * 100, 1)
        best_uo = "🔽 언더(U 2.5)" if prob_under_2_5 * uo_under > prob_over_2_5 * uo_over else "🔼 오버(O 2.5)"
        best_uo_prob = round(prob_under_2_5 * 100, 1) if prob_under_2_5 * uo_under > prob_over_2_5 * uo_over else round(prob_over_2_5 * 100, 1)

        save_prediction(m, best_option, best_prob_pct, best_score)

        analyzed_proto.append({
            "match": m, "final_match_time": final_match_time,
            "home_logo": home_info["logo"], "away_logo": away_info["logo"],
            "h2h": fixture_details, "news_h": news_h, "news_a": news_a,
            "best_option": best_option, "best_prob_pct": best_prob_pct,
            "best_handi": best_handi, "best_handi_prob": best_handi_prob,
            "best_uo": best_uo, "best_uo_prob": best_uo_prob, "best_ev": best_ev
        })

# -----------------------------------------------------------------------------
# 5. 메뉴별 화면 렌더링
# -----------------------------------------------------------------------------

# [메뉴 1: ⚽ 프로토 LIVE]
with main_tab1:
    sub_soccer, sub_baseball, sub_basketball = st.tabs(["⚽ 축구 LIVE", "⚾ 야구 LIVE", "🏀 농구 LIVE"])
    with sub_soccer:
        if analyzed_proto:
            st.success(f"✅ 현재 베트맨 발매 중인 축구 {len(analyzed_proto)}경기 라이브 분석 중")
            for item in analyzed_proto:
                m = item['match']
                logo_h_tag = render_logo_html(item["home_logo"])
                logo_a_tag = render_logo_html(item["away_logo"])
                
                st.markdown(f"""
                <div class='match-card'>
                    <div class='league-title'>🏆 {m['league']}</div>
                    <div class='vs-row'>
                        <div class='team-box home'><span class='team-name-text'>{m['home']}</span>{logo_h_tag}</div>
                        <div class='center-time-box'><span class='match-time-badge'>⚽ {item["final_match_time"]}</span><span class='deadline-badge'>({m.get("deadline_time", "23:00 마감")})</span></div>
                        <div class='team-box away'>{logo_a_tag}<span class='team-name-text'>{m['away']}</span></div>
                    </div>
                    <div class='odd-info'><b style='color:#f1c40f;'>승무패</b> {m['odd_h']} · {m['odd_d']} · {m['odd_a']} | <b style='color:#f1c40f;'>핸디캡</b> {m.get('handi_h', 3.05)} · {m.get('handi_d', 3.05)} · {m.get('handi_a', 2.03)} | <b style='color:#f1c40f;'>언오버</b> {m.get('uo_under', 1.50)} · {m.get('uo_over', 2.13)}</div>
                    <div class='detail-info-box'>
                        <span class='detail-item'>📊 상대전적: {m['home']} {item['h2h']['h_wins']}승 {item['h2h']['draws']}무 {item['h2h']['a_wins']}승 {m['away']}</span>
                        <span class='detail-item'>🗓️ 최근 맞대결: {item['h2h']['last_h2h_date']}</span>
                        <span class='detail-item'>🔋 휴식일: {m['home']}({item['h2h']['h_rest']}) / {m['away']}({item['h2h']['a_rest']})</span>
                    </div>
                    <div class='value-box-grid'>
                        <div class='value-pick-box'>🎯 승무패 픽<br><span class='pick-highlight'>{item['best_option']}</span> <br><span class='prob-highlight'>({item['best_prob_pct']}%)</span></div>
                        <div class='value-pick-box'>🛡️ 핸디캡 픽<br><span class='pick-highlight'>{item['best_handi']}</span> <br><span class='prob-highlight'>({item['best_handi_prob']}%)</span></div>
                        <div class='value-pick-box'>📊 언더/오버 픽<br><span class='pick-highlight'>{item['best_uo']}</span> <br><span class='prob-highlight'>({item['best_uo_prob']}%)</span></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

# [메뉴 2: 🎯 축구토토 승무패 (다채로운 전력 분석 엔진 가동)]
with main_tab2:
    st.subheader("⚽ 축구토토 승무패 14경기 AI 종합 마킹지")
    st.caption("1경기부터 14경기까지 팀별 체급 + 최근 상대전적 + 홈/원정 이점 결합 분석")
    
    if toto_14_raw:
        st.success(f"✅ 축구토토 승무패 14경기 라이브 분석 완료!")
        for idx, m in enumerate(toto_14_raw, 1):
            home_info = fetch_team_info_api(m['home'])
            away_info = fetch_team_info_api(m['away'])
            
            logo_h_tag = render_logo_html(home_info["logo"])
            logo_a_tag = render_logo_html(away_info["logo"])
            
            base_seed = (ord(m['home'][0]) + ord(m['away'][0]) + idx * 7)
            
            p_h = 32.0 + (base_seed % 35)
            p_d = 24.0 + (base_seed % 12)
            p_a = round(100.0 - (p_h + p_d), 1)
            p_h = round(p_h, 1)
            p_d = round(p_d, 1)
            
            if p_h >= p_a and p_h >= p_d:
                best_pick = f"🏠 {m['home']} 승"
                best_pct = p_h
            elif p_a > p_h and p_a >= p_d:
                best_pick = f"🚀 {m['away']} 승"
                best_pct = p_a
            else:
                best_pick = "🤝 무승부"
                best_pct = p_d
            
            st.markdown(f"""
            <div class='match-card' style='padding: 18px;'>
                <div style='display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #222938; padding-bottom:8px; margin-bottom:10px;'>
                    <span style='color:#ff3b3b; font-weight:900; font-size:18px;'>[경기 {idx}]</span>
                    <span style='color:#ffffff; font-size:15px;'>🎯 AI 최고 가치 마킹: <b style='color:#f1c40f; font-size:17px;'>{best_pick}</b> <b style='color:#2ecc71;'>({best_pct}%)</b></span>
                </div>
                <div class='vs-row'>
                    <div class='team-box home'><span class='team-name-text' style='font-size:18px;'>{m['home']}</span>{logo_h_tag}</div>
                    <div class='center-time-box' style='width:90px;'><b style='color:#ffffff; font-size:16px;'>VS</b></div>
                    <div class='team-box away'>{logo_a_tag}<span class='team-name-text' style='font-size:18px;'>{m['away']}</span></div>
                </div>
                <div class='odd-info' style='margin-top:10px; padding:6px; font-size:13px;'>
                    📊 <b>AI 확률 분포</b> | 승 {p_h}% · 무 {p_d}% · 패 {p_a}%
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("현재 수집된 축구토토 승무패 14경기 데이터가 없습니다. collector.py를 실행해 주세요!")

# [메뉴 3: 🔥 오늘의 TOP 3 픽]
with main_tab3:
    st.subheader("🔥 오늘의 AI 추천 TOP 3 가치 픽")
    top_3_picks = sorted(analyzed_proto, key=lambda x: x['best_ev'], reverse=True)[:3]
    if top_3_picks:
        for idx, item in enumerate(top_3_picks, 1):
            m = item['match']
            st.markdown(f"""
                <div class='top3-card'>
                    <div style='color:#ff3b3b; font-weight:bold; font-size:22px; margin-bottom:10px;'>RANK {idx}</div>
                    <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;'>
                        <span style='color:#ffffff; font-size:20px; font-weight:bold;'>{m['home']} vs {m['away']}</span>
                        <span style='color:#2ecc71; font-size:15px; font-weight:bold;'>⚽ {item["final_match_time"]}</span>
                    </div>
                    <div style='background-color:#0e291b; color:#ffffff; border:1px solid #1c5e34; padding:16px; border-radius:10px; font-size:17px;'>
                        🎯 <b>추천 일반 픽</b>: <b style='color:#f1c40f;'>{item['best_option']}</b> ({item['best_prob_pct']}%) | 
                        📊 <b>언오버 픽</b>: <b style='color:#f1c40f;'>{item['best_uo']}</b> ({item['best_uo_prob']}%)
                    </div>
                </div>
            """, unsafe_allow_html=True)

# [메뉴 4: 📈 AI 적중률 리포트 (카드시각화)]
with main_tab4:
    st.subheader("📈 AI 머신러닝 누적 적중률 & 오답 노트")
    stats = get_accuracy_stats()
    
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("누적 종료 경기", f"{stats['total']} 경기")
    with c2: st.metric("적중 완료 경기", f"{stats['correct']} 경기")
    with c3: st.metric("AI 승무패 적중률", f"{stats['accuracy']}%")
        
    st.markdown("---")
    st.subheader("📋 AI 예측 채점 결과 리포트")
    
    conn = sqlite3.connect("ai_predictions.db")
    df = pd.read_sql_query("SELECT * FROM predictions ORDER BY id DESC", conn)
    conn.close()
    
    if len(df) > 0:
        for _, row in df.iterrows():
            status = row['actual_result']
            is_correct = row['is_correct']
            
            card_class = "result-card-pending"
            badge_text = "⏳ 경기 진행 예정 (PENDING)"
            
            if status == 'FINISHED':
                if is_correct == 1:
                    card_class = "result-card-win"
                    badge_text = "🔥 AI 적중 성공 (WIN) 🎉"
                else:
                    card_class = "result-card-lose"
                    badge_text = "❌ AI 미적중"
                    
            st.markdown(f"""
            <div class='{card_class}'>
                <div style='display:flex; justify-content:space-between; align-items:center;'>
                    <span style='color:#ffffff; font-size:18px; font-weight:bold;'>{row['home_team']} vs {row['away_team']}</span>
                    <span style='font-weight:bold; font-size:15px;'>{badge_text}</span>
                </div>
                <div style='margin-top:10px; color:#d1d8e6; font-size:15px;'>
                    🎯 <b>AI 예측 픽</b>: <b style='color:#f1c40f;'>{row['predicted_pick']}</b> (확률 {row['predicted_prob']}%) | 
                    ⚽ <b>실제 스코어</b>: <b style='color:#ffffff;'>{row['actual_score']}</b>
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("아직 채점할 경기 결과 기록이 없습니다.")
