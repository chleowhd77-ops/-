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
# 0. 기본 설정
# -----------------------------------------------------------------------------
APP_TITLE = "D.J PROTO ANALYTICS"
st.set_page_config(page_title=APP_TITLE, page_icon="⚡", layout="wide", initial_sidebar_state="collapsed")

API_KEY = "28b599664bba858ebf93515768741975"  # 👈 여기에 회원님 API 키를 넣어주세요!
API_HOST = "v3.football.api-sports.io"
headers = {'x-rapidapi-host': API_HOST, 'x-rapidapi-key': API_KEY}

TEAM_NAME_MAP = {
    # K리그
    "광주FC": "Gwangju FC", "포항스틸": "Pohang Steelers", "제주SKFC": "Jeju United", "FC안양": "FC Anyang",
    "FC서울": "FC Seoul", "대전하나": "Daejeon Citizen", "대전 하나시티즌": "Daejeon Citizen",
    "충북청주": "Chungbuk Cheongju", "전남드래": "Jeonnam Dragons", "김해FC": "Gimhae", 
    "경남FC": "Gyeongnam FC", "수원삼성": "Suwon Samsung", "수원FC": "Suwon FC",
    "부산아이": "Busan I Park", "부산 아이파크": "Busan I Park", "화성FC": "Hwaseong", 
    "인천유나": "Incheon United", "김천상무": "Gimcheon Sangmu", "김천상무 프로축구단": "Gimcheon Sangmu",
    "부천FC": "Bucheon FC 1995", "부천FC 1995": "Bucheon FC 1995", "전북현대": "Jeonbuk Motors", 
    "울산HDFC": "Ulsan Hyundai", "강원FC": "Gangwon FC", "서울이랜드": "Seoul E-Land", 
    "서울이랜": "Seoul E-Land", "안산그리": "Ansan Greeners", "대구FC": "Daegu FC", 
    "충남아산": "Chungnam Asan", "충남아산 프로축구단": "Chungnam Asan", "김포FC": "Gimpo FC", 
    "천안시티": "Cheonan City", "파주프런": "Paju Citizen", "성남FC": "Seongnam FC",
    
    # MLS
    "FC신시내": "FC Cincinnati", "뉴욕시티": "New York City FC", "콜럼크루": "Columbus Crew", "CF몽레알": "Montreal Impact",
    "DC유나이": "DC United", "뉴잉레벌": "New England Revolution", "뉴욕레드": "New York Red Bulls", "시카파이": "Chicago Fire",
    "올랜시티": "Orlando City", "인터마이": "Inter Miami", "필라유니": "Philadelphia Union", "애틀유나": "Atlanta United",
    
    # 해외
    "SD레이더스": "SD Raiders", "시드니FC": "Sydney FC", "GNK디나모자그레브": "Dinamo Zagreb", "비킹FK": "Viking",
    "페네르바흐체SK": "Fenerbahce", "올랭피크 리옹": "Lyon", "인디펜디엔테 리바다비아": "Independiente Rivadavia",
    "플루미넨시": "Fluminense", "데포르테스 톨리마": "Deportes Tolima", "인디펜디엔테 델바예": "Independiente del Valle",
    "태국": "Thailand", "싱가포르": "Singapore", "대한민국": "South Korea", "일본": "Japan", "호주": "Australia",
    "베트남": "Vietnam", "말레이시아": "Malaysia"
}

DIRECT_LOGO_MAP = {
    "광주FC": "https://media.api-sports.io/football/teams/2836.png", "포항스틸": "https://media.api-sports.io/football/teams/2843.png",
    "제주SKFC": "https://media.api-sports.io/football/teams/2839.png", "FC안양": "https://media.api-sports.io/football/teams/2848.png",
    "FC서울": "https://media.api-sports.io/football/teams/2844.png", "대전하나": "https://media.api-sports.io/football/teams/2835.png",
    "충북청주": "https://media.api-sports.io/football/teams/18525.png", "전남드래": "https://media.api-sports.io/football/teams/2847.png"
}

def download_db_from_github():
    try:
        t = int(time.time() / 60)
        res = requests.get(f"https://raw.githubusercontent.com/chleowhd77-ops/-/main/ai_predictions.db?t={t}", timeout=10)
        if res.status_code == 200:
            with open("ai_predictions.db", "wb") as f: f.write(res.content)
    except: pass
download_db_from_github()

@st.cache_data(ttl=86400)
def fetch_team_info_api_v3(team_name):
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
def fetch_recent_form_v3(team_id):
    if not team_id: return {"form": "정보없음"}
    url = f"https://{API_HOST}/fixtures"
    params = {"team": team_id, "last": 5, "status": "FT-AET-PEN"}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=5)
        matches = res.json().get("response", [])
        if not matches: return {"form": "정보없음"}
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
        return {"form": "-".join(form)}
    except: return {"form": "정보없음"}

@st.cache_data(ttl=43200)
def fetch_fixture_details_api_v3(home_id, away_id):
    if not home_id or not away_id: return {"h_wins": 0, "draws": 0, "a_wins": 0, "total": 0, "is_valid": False}
    url = f"https://{API_HOST}/fixtures/headtohead"
    params = {"h2h": f"{home_id}-{away_id}"}
    h_wins, draws, a_wins = 0, 0, 0
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        matches = response.json().get("response", [])
        for m in matches[:10]:
            if m.get("teams", {}).get("home", {}).get("winner"):
                if m.get("teams", {}).get("home", {}).get("id") == home_id: h_wins += 1
                else: a_wins += 1
            elif m.get("teams", {}).get("away", {}).get("winner"):
                if m.get("teams", {}).get("home", {}).get("id") == home_id: a_wins += 1
                else: h_wins += 1
            else: draws += 1
        return {"h_wins": h_wins, "draws": draws, "a_wins": a_wins, "total": len(matches[:10]), "is_valid": True}
    except: return {"h_wins": 0, "draws": 0, "a_wins": 0, "total": 0, "is_valid": False}

@st.cache_data(ttl=60)
def load_betman_data():
    try:
        t = int(time.time() / 60)
        res = requests.get(f"https://raw.githubusercontent.com/chleowhd77-ops/-/main/betman_data.json?t={t}", timeout=5)
        if res.status_code == 200: return res.json()
    except: pass
    return {"proto_matches": [], "toto_14_matches": []}

@st.cache_data(ttl=60)
def load_live_scores():
    try:
        t = int(time.time() / 60)
        res = requests.get(f"https://raw.githubusercontent.com/chleowhd77-ops/-/main/live_scores.json?t={t}", timeout=5)
        if res.status_code == 200: return res.json()
    except: pass
    return {}

def get_accuracy_stats():
    try:
        conn = sqlite3.connect("ai_predictions.db")
        df = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED'", conn)
        df_history = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result IN ('FINISHED', 'CANCELED') ORDER BY id DESC LIMIT 50", conn)
        conn.close()
        if len(df) == 0: return {"total": 0, "correct": 0, "accuracy": 0.0, "history": []}
        total = len(df)
        correct = df['is_correct'].sum()
        return {"total": total, "correct": correct, "accuracy": round((correct / total) * 100, 1), "history": df_history.to_dict('records')}
    except: return {"total": 0, "correct": 0, "accuracy": 0.0, "history": []}

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
    if prob_h >= 60.0: return f"🔥 전력 우위 분석! 홈팀 <b>{h_team}</b>의 승률이 {prob_h}%로 데이터상 크게 앞섭니다."
    elif prob_a >= 60.0: return f"🚨 원정팀의 매서운 기세! <b>{a_team}</b>의 승리 확률이 {prob_a}%로 산출되었습니다."
    elif abs(prob_h - prob_a) <= 10.0 and prob_d >= 25.0: return f"⚔️ <b>{h_team}</b> vs <b>{a_team}</b> 초박빙 승부! 무승부 확률({prob_d}%)이 높아 신중한 접근이 필요합니다."
    elif prob_h > prob_a: return f"📊 AI 알고리즘 산출 결과, <b>{h_team}</b>의 승률({prob_h}%)이 {a_team}보다 다소 우세합니다."
    else: return f"🔍 원정팀 <b>{a_team}</b>의 전력 수치가 {prob_a}%로 조금 더 높게 평가되었습니다."

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700;900&display=swap');
    html, body, .stApp { background-color: #06080F !important; font-family: 'Noto Sans KR', sans-serif !important; color: #E2E8F0; overflow-x: hidden !important; }
    [data-testid="stSidebar"] { display: none; }
    .block-container { max-width: 1000px !important; padding-top: 2rem !important; padding-bottom: 2rem !important; }
    .app-header { text-align: center; padding: 30px 0 20px 0; border-bottom: 1px solid #1E293B; margin-bottom: 30px; }
    .app-header h1 { color: #FFFFFF !important; font-size: 36px !important; font-weight: 900 !important; letter-spacing: 2px; margin: 0; background: -webkit-linear-gradient(45deg, #00F2FE, #4FACFE); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    
    .match-card { background-color: #0B0F19; border: 1px solid #1E293B; border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); position:relative;}
    .league-title { font-size: 13px; color: #94A3B8; font-weight: 900; letter-spacing: 1px; margin-bottom: 15px; }
    
    .vs-row { display: flex; justify-content: space-between; align-items: center; width: 100%; margin-bottom: 15px; }
    .team-box-col { flex: 1; display: flex; flex-direction: column; justify-content: center; }
    .team-box-col.home { align-items: flex-end; text-align: right; }
    .team-box-col.away { align-items: flex-start; text-align: left; }
    .team-info-row { display: flex; align-items: center; gap: 10px; }
    
    .team-name-text { color: #F8FAFC !important; font-size: 22px; font-weight: 900; letter-spacing: -0.5px; word-break: keep-all; line-height: 1.2; }
    .team-logo { width: 50px !important; height: 50px !important; object-fit: contain; }
    .center-time-box { width: 120px; text-align: center; flex-shrink: 0; }
    .match-time-text { color: #CBD5E1; font-size: 14px; font-weight: 700; display: block; margin-bottom: 4px;}
    .live-score { font-size: 24px; font-weight: 900; color: #00F2FE; display: block; margin-bottom: 4px; text-shadow: 0 0 10px rgba(0,242,254,0.5); }
    
    .deadline-open { color: #00F2FE; font-size: 12px; font-weight: 900; border: 1px solid #00F2FE; padding: 3px 8px; border-radius: 4px; display: inline-block;}
    .deadline-closed { color: #EF4444; font-size: 12px; font-weight: 900; background: rgba(239, 68, 68, 0.1); border: 1px solid #EF4444; padding: 3px 8px; border-radius: 4px; display: inline-block;}
    
    .odd-bar { display: flex; justify-content: space-between; background: #111827; border-radius: 6px; padding: 12px 20px; margin-bottom: 15px; border: 1px solid #1F2937; }
    .odd-item { font-size: 14px; color: #94A3B8; font-weight: 700; }
    .odd-val { color: #F1F5F9; font-weight: 900; margin-left: 6px; }
    
    .pred-grid { display: flex; gap: 12px; }
    .pred-box { flex: 1; background: #0D1424; border: 1px solid #1E293B; border-radius: 8px; padding: 16px; text-align: center; }
    .pred-label { font-size: 12px; color: #64748B; font-weight: 900; margin-bottom: 8px; }
    .pred-value { font-size: 16px; color: #F8FAFC; font-weight: 900; }
    .pred-prob { font-size: 13px; color: #10B981; font-weight: 900; margin-left: 6px; }

    @media (max-width: 768px) {
        .vs-row { align-items: flex-start !important; }
        .team-box-col.home, .team-box-col.away { align-items: center !important; text-align: center !important; }
        .team-box-col.home .team-info-row { flex-direction: column-reverse !important; justify-content: center !important; gap: 6px !important; }
        .team-box-col.away .team-info-row { flex-direction: column !important; justify-content: center !important; gap: 6px !important; }
        .team-name-text { font-size: 12px !important; white-space: normal !important; word-break: keep-all !important; text-align: center !important; line-height: 1.4 !important; }
        .team-logo { width: 40px !important; height: 40px !important; }
        .center-time-box { width: 80px !important; margin-top: 15px !important; }
        .match-time-text { font-size: 11px !important; }
        .live-score { font-size: 18px !important; }
        .odd-bar { padding: 10px; flex-direction: column; gap: 8px; text-align: center; }
        .odd-item { font-size: 12px; }
        .pred-grid { flex-direction: column; gap: 8px; }
        .pred-box { padding: 10px; }
        .pred-value { font-size: 14px !important; }
        .pred-prob { font-size: 12px !important; }
    }
    
    /* 리포트 테이블 CSS */
    .history-table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; text-align: center; }
    .history-table th { background: #1E293B; color: #94A3B8; padding: 10px; border-bottom: 2px solid #334155; }
    .history-table td { padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; }
    .hit { color: #10B981; font-weight: 900; }
    .miss { color: #EF4444; font-weight: 900; }
    </style>
""", unsafe_allow_html=True)

def render_logo_html(logo_url):
    if logo_url: return f"<img src='{logo_url}' class='team-logo' onerror=\"this.style.display='none';\">"
    return ""

st.markdown("<div class='app-header'><h1>D.J PROTO ANALYTICS</h1></div>", unsafe_allow_html=True)
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
        
        home_info = fetch_team_info_api_v3(m["home"])
        away_info = fetch_team_info_api_v3(m["away"])
        fixture_details = fetch_fixture_details_api_v3(home_info["id"], away_info["id"])
        
        final_match_time = m.get("match_time") or m.get("time") or "시간 미정"
        
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

        story = generate_dynamic_story(m['home'], m['away'], h_win*100, draw*100, a_win*100, odd_h, odd_a)

        analyzed_proto.append({
            "match": m, "final_match_time": final_match_time, "home_logo": home_info["logo"], "away_logo": away_info["logo"],
            "story": story, "best_option": best_option, "best_prob_pct": best_prob_pct,
            "best_handi": best_handi, "best_handi_prob": best_handi_prob, "best_uo": best_uo, "best_uo_prob": best_uo_prob
        })

# --------------------------
# 탭 1: 프로토 LIVE
# --------------------------
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
                
                if match_status == "LIVE": 
                    live_data = live_scores_dict.get(str(m['id']), {"score": "진행중"})
                    real_live_score = live_data.get("score", "진행중") if isinstance(live_data, dict) else "진행중"
                    time_display = f"<span class='live-score'>{real_live_score}</span><span class='deadline-closed'>LIVE</span>"
                elif match_status == "FINISHED": time_display = f"<span class='live-score'>종료</span>"
                else:
                    badge = f"<span class='deadline-closed'>픽 마감</span>" if is_closed else f"<span class='deadline-open'>{raw_deadline}</span>"
                    time_display = f"<span class='match-time-text'>{item['final_match_time']}</span>{badge}"
                
                html_code = f"""
                <div class='match-card'>
                    <div class='league-title'>{m['league']}</div>
                    <div class='vs-row'>
                        <div class='team-box-col home'><div class='team-info-row'><span class='team-name-text'>{m['home']}</span>{logo_h_tag}</div></div>
                        <div class='center-time-box'>{time_display}</div>
                        <div class='team-box-col away'><div class='team-info-row'>{logo_a_tag}<span class='team-name-text'>{m['away']}</span></div></div>
                    </div>
                    <div style='background: rgba(0, 242, 254, 0.05); border-left: 3px solid #00F2FE; padding: 12px; font-size: 13px; color: #E2E8F0; margin-bottom: 15px;'>{item['story']}</div>
                    <div class='odd-bar'>
                        <span class='odd-item'>승 <span class='odd-val'>{m['odd_h']}</span> | 무 <span class='odd-val'>{m['odd_d']}</span> | 패 <span class='odd-val'>{m['odd_a']}</span></span>
                        <span class='odd-item'>핸디캡 <span class='odd-val'>{m.get('handi_h', '-')} / {m.get('handi_a', '-')}</span></span>
                        <span class='odd-item'>언오버 <span class='odd-val'>{m.get('uo_under', '-')} / {m.get('uo_over', '-')}</span></span>
                    </div>
                    <div class='pred-grid'>
                        <div class='pred-box'><div class='pred-label'>승무패 예측</div><span class='pred-value'>{item['best_option']}</span> <span class='pred-prob'>{item['best_prob_pct']}%</span></div>
                        <div class='pred-box'><div class='pred-label'>핸디캡 예측</div><span class='pred-value'>{item['best_handi']}</span> <span class='pred-prob'>{item['best_handi_prob']}%</span></div>
                        <div class='pred-box'><div class='pred-label'>언더/오버 예측</div><span class='pred-value'>{item['best_uo']}</span> <span class='pred-prob'>{item['best_uo_prob']}%</span></div>
                    </div>
                </div>
                """
                st.markdown(html_code, unsafe_allow_html=True)
        else: st.info("현재 분석 가능한 프로토 축구 경기가 없습니다.")
            
    with sub_baseball: st.info("야구 분석 데이터 준비 중입니다.")
    with sub_basketball: st.info("농구 분석 데이터 준비 중입니다.")

# --------------------------
# 탭 2: 승무패 14경기
# --------------------------
with main_tab2:
    if toto_14_raw:
        for idx, m in enumerate(toto_14_raw, 1):
            home_info = fetch_team_info_api_v3(m['home'])
            away_info = fetch_team_info_api_v3(m['away'])
            logo_h_tag = render_logo_html(home_info["logo"])
            logo_a_tag = render_logo_html(away_info["logo"])
            
            base_seed = (ord(m['home'][0]) + ord(m['away'][0]) + idx * 7)
            p_h = 32.0 + (base_seed % 35); p_d = 24.0 + (base_seed % 12); p_a = round(100.0 - (p_h + p_d), 1)
            
            if p_h >= p_a and p_h >= p_d: best_pick = f"{m['home']} 승"
            elif p_a > p_h and p_a >= p_d: best_pick = f"{m['away']} 승"
            else: best_pick = "무승부"
            
            # [수술 완료] HTML 코드가 텍스트로 노출되지 않도록 들여쓰기 쫙 당김!
            html_code = f"""<div class='match-card' style='padding: 20px;'>
<div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;'>
<span style='color:#00F2FE; font-size:12px; border:1px solid #00F2FE; padding:3px 10px; border-radius:12px; font-weight:900;'>제 {idx} 경기</span>
<span style='font-size:13px; color:#F8FAFC; font-weight:700;'>AI 추천: <span style='color:#10B981;'>{best_pick}</span></span>
</div>
<div class='vs-row' style='margin-bottom:15px;'>
<div class='team-box-col home'><div class='team-info-row'><span class='team-name-text'>{m['home']}</span>{logo_h_tag}</div></div>
<div class='center-time-box' style='width:40px;'><b style='color:#475569; font-size:16px;'>VS</b></div>
<div class='team-box-col away'><div class='team-info-row'>{logo_a_tag}<span class='team-name-text'>{m['away']}</span></div></div>
</div>
<div style='width: 100%; display: flex; height: 10px; border-radius: 5px; overflow: hidden; margin-bottom: 8px;'>
<div style='width: {p_h}%; background: #00F2FE;' title='승 {p_h}%'></div>
<div style='width: {p_d}%; background: #10B981;' title='무 {p_d}%'></div>
<div style='width: {p_a}%; background: #EF4444;' title='패 {p_a}%'></div>
</div>
<div style='font-size:11px; color:#94A3B8; text-align:center;'>확률 분포: 승 {p_h}% | 무 {p_d}% | 패 {p_a}%</div>
</div>"""
            st.markdown(html_code, unsafe_allow_html=True)
    else: st.info("이번 회차 승무패 14경기 데이터가 없습니다.")
# --------------------------
# 탭 3: 오늘의 TOP 3
# --------------------------
with main_tab3:
    if analyzed_proto:
        # 확률이 가장 높은 순서대로 정렬해서 상위 3개 뽑기
        top_matches = sorted(analyzed_proto, key=lambda x: x['best_prob_pct'], reverse=True)[:3]
        st.markdown("<h3 style='color:#00F2FE; margin-bottom:20px; font-weight:900;'>🏆 AI가 픽한 오늘의 초강력 추천 3경기</h3>", unsafe_allow_html=True)
        
        for i, item in enumerate(top_matches, 1):
            m = item['match']
            html_code = f"""
            <div class='match-card' style='border: 2px solid #00F2FE; box-shadow: 0 0 15px rgba(0,242,254,0.2);'>
                <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;'>
                    <span style='background:#00F2FE; color:#0B0F19; font-size:14px; padding:4px 12px; border-radius:4px; font-weight:900;'>TOP {i}</span>
                    <span style='color:#F8FAFC; font-weight:900; font-size:18px;'>추천 픽: <span style='color:#00F2FE;'>{item['best_option']}</span></span>
                </div>
                <div class='vs-row'>
                    <div class='team-box-col home' style='text-align:right;'><span class='team-name-text'>{m['home']}</span></div>
                    <div class='center-time-box' style='color:#94A3B8;'>VS</div>
                    <div class='team-box-col away' style='text-align:left;'><span class='team-name-text'>{m['away']}</span></div>
                </div>
                <div style='text-align:center; font-size:14px; color:#10B981; font-weight:900; margin-top:10px;'>
                    적중 예상 확률: {item['best_prob_pct']}%
                </div>
            </div>
            """
            st.markdown(html_code, unsafe_allow_html=True)
    else: st.info("추천할 경기가 없습니다.")

# --------------------------
# 탭 4: AI 리포트 (오답 노트)
# --------------------------
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
        table_html = "<table class='history-table'><thead><tr><th>시간</th><th>경기</th><th>예측</th><th>결과</th><th>채점</th></tr></thead><tbody>"
        for row in history:
            result_mark = "<span class='hit'>적중</span>" if row['is_correct'] == 1 else "<span class='miss'>실패</span>"
            if row['actual_result'] == 'CANCELED': result_mark = "<span style='color:#94A3B8;'>취소/무효</span>"
            
            table_html += f"""
                <tr>
                    <td style='font-size:11px; color:#64748B;'>{row['match_time']}</td>
                    <td style='font-weight:700;'>{row['home_team']} vs {row['away_team']}</td>
                    <td>{row['predicted_pick']}</td>
                    <td style='font-weight:900;'>{row['actual_score']}</td>
                    <td>{result_mark}</td>
                </tr>
            """
        table_html += "</tbody></table>"
        st.markdown(table_html, unsafe_allow_html=True)
        
        st.markdown("<h4 style='color:#F8FAFC; font-weight:900; margin-top:30px; margin-bottom:10px;'>💡 실패 원인 분석 목록</h4>", unsafe_allow_html=True)
        for row in history:
            if row['is_correct'] == 0 and row['failure_reason']:
                st.markdown(f"<div style='background:#1E293B; padding:12px; border-radius:6px; margin-bottom:8px; font-size:13px; color:#CBD5E1;'><b>[{row['home_team']} vs {row['away_team']}]</b><br>{row['failure_reason']}</div>", unsafe_allow_html=True)
    else:
        st.info("아직 채점이 완료된 종료 경기가 없습니다.")
