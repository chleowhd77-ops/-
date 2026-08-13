import streamlit as st
import math
import json
import requests
import sqlite3
import pandas as pd
from datetime import datetime
import pytz
from urllib.parse import quote
from bs4 import BeautifulSoup

# -----------------------------------------------------------------------------
# 0. 기본 설정 및 API-Football 셋팅
# -----------------------------------------------------------------------------
st.set_page_config(page_title="프로토 AI 스마트 픽 대시보드", page_icon="🏆", layout="wide")

# ★ 회원님의 API-Football 키를 입력하세요
API_KEY = "28b599664bba858ebf93515768741975"
API_HOST = "v3.football.api-sports.io"

headers = {
    'x-rapidapi-host': API_HOST,
    'x-rapidapi-key': API_KEY
}

TEAM_NAME_MAP = {
    "미라솔": "Mirassol",
    "LDU키토": "Liga Dep. Universitaria de Quito",
    "로사리오 센트랄": "Rosario Central",
    "SC코린티안스": "Corinthians",
    "도쿄 베르디": "Tokyo Verdy",
    "가시와 레이솔": "Kashiwa Reysol",
    "에버턴": "Everton",
    "뉴캐슬 유나이티드": "Newcastle",
    "맨체스터 유나이티드": "Manchester United",
    "파리 생제르맹": "Paris Saint Germain"
}

DIRECT_LOGO_MAP = {
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
# 1. 해외 API 연동 (팀 정보 & 한국 기준 경기시간 & 상대전적 H2H)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=86400)
def fetch_team_info_api(team_name):
    logo = DIRECT_LOGO_MAP.get(team_name)
    search_name = TEAM_NAME_MAP.get(team_name, team_name)
    url = f"https://{API_HOST}/teams"
    params = {"search": search_name}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        res_data = response.json()
        if res_data.get("response") and len(res_data["response"]) > 0:
            team_data = res_data["response"][0]["team"]
            if not logo:
                logo = team_data.get("logo")
            return {"id": team_data["id"], "logo": logo}
    except Exception:
        pass
        
    if not logo:
        clean_name = quote(team_name[:2])
        logo = f"https://ui-avatars.com/api/?name={clean_name}&background=2A2E39&color=FF4B4B&bold=true&rounded=true&size=64"
        
    return {"id": None, "logo": logo}

@st.cache_data(ttl=43200)
def fetch_fixture_details_api(home_id, away_id):
    """해외 API에서 양 팀의 실제 경기 날짜 및 시각(한국시간)과 H2H 수집"""
    if not home_id or not away_id:
        return {"match_time": None, "h_wins": 0, "draws": 0, "a_wins": 0, "total": 0}
        
    url = f"https://{API_HOST}/fixtures/headtohead"
    params = {"h2h": f"{home_id}-{away_id}"}
    
    match_time_str = None
    h_wins, draws, a_wins = 0, 0, 0
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        res_data = response.json()
        matches = res_data.get("response", [])
        
        # 가장 최근/예정 경기 시각 추출
        if len(matches) > 0:
            latest_match = matches[0]
            utc_date_str = latest_match.get("fixture", {}).get("date")
            if utc_date_str:
                # ISO 시간을 한국 시각(KST)으로 정밀 변환
                utc_dt = datetime.fromisoformat(utc_date_str.replace("Z", "+00:00"))
                kst_dt = utc_dt.astimezone(pytz.timezone("Asia/Seoul"))
                match_time_str = kst_dt.strftime("%m.%d (%a) %H:%M")
        
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
            else:
                draws += 1
                
        return {
            "match_time": match_time_str,
            "h_wins": h_wins, "draws": draws, "a_wins": a_wins, 
            "total": len(matches[:10])
        }
    except Exception:
        return {"match_time": None, "h_wins": 0, "draws": 0, "a_wins": 0, "total": 0}

@st.cache_data(ttl=3600)
def analyze_team_news_sentiment(team_name):
    keywords_negative = ["부상", "결장", "로테이션", "체력 부담", "징계", "불화", "부진", "결장 우려"]
    keywords_positive = ["복귀", "연승", "사기 충천", "주전 총출동", "대승", "호조", "완승"]
    
    score_mod = 0.0
    detected_issues = []
    
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
                        if f"⚠️ {kw}" not in detected_issues: 
                            detected_issues.append(f"⚠️ {kw}")
                for kw in keywords_positive:
                    if kw in t:
                        score_mod += 0.10
                        if f"🔥 {kw}" not in detected_issues: 
                            detected_issues.append(f"🔥 {kw}")
    except Exception:
        pass
        
    return {"mod": round(score_mod, 2), "issues": detected_issues[:2]}

# -----------------------------------------------------------------------------
# 2. GitHub 수집 데이터 로드
# -----------------------------------------------------------------------------
@st.cache_data(ttl=60) # 최신 데이터 반영을 위해 캐시 주기를 1분으로 단축
def load_betman_data():
    raw_url = "https://raw.githubusercontent.com/chleowhd77-ops/-/main/betman_data.json"
    try:
        res = requests.get(raw_url, timeout=5)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return []

def get_accuracy_stats():
    conn = sqlite3.connect("ai_predictions.db")
    df = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED'", conn)
    conn.close()
    
    if len(df) == 0:
        return {"total": 0, "correct": 0, "accuracy": 0.0}
    
    total = len(df)
    correct = df['is_correct'].sum()
    accuracy = round((correct / total) * 100, 1)
    return {"total": total, "correct": correct, "accuracy": accuracy}

def save_prediction(m, best_option, best_prob_pct, best_score):
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT OR IGNORE INTO predictions 
            (match_id, league, home_team, away_team, predicted_pick, predicted_prob, expected_score, odd_h, odd_d, odd_a)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            m['id'], m['league'], m['home'], m['away'], 
            best_option, best_prob_pct, f"{best_score[0]}:{best_score[1]}",
            m['odd_h'], m['odd_d'], m['odd_a']
        ))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

# -----------------------------------------------------------------------------
# 3. 프리미엄 CSS 스타일링
# -----------------------------------------------------------------------------
st.markdown("""
    <style>
    .stApp { background-color: #0d0f12; }
    [data-testid="stSidebar"] {
        background-color: #14171d !important;
        border-right: 1px solid #232731;
    }
    div[data-testid="stTabs"] { width: 100% !important; }
    .stTabs [data-baseweb="tab-list"], div[role="tablist"] {
        width: 100% !important; display: flex !important; justify-content: space-between !important;
        background-color: transparent !important; border: none !important; border-bottom: 1px solid #232731 !important;
        margin-bottom: 25px !important; gap: 0px !important; padding: 0px !important;
    }
    .stTabs [data-baseweb="tab"], button[role="tab"] {
        flex: 1 1 0% !important; width: 33.33% !important; height: 50px !important;
        border: none !important; background-color: transparent !important;
        color: #777777 !important; font-weight: 800 !important; font-size: 18px !important;
        cursor: pointer !important; text-align: center !important;
    }
    .stTabs [aria-selected="true"] {
        color: #ff4b4b !important; background-color: transparent !important; border: none !important; box-shadow: none !important;
    }
    .stTabs [data-baseweb="tab-border"], .stTabs [data-baseweb="tab-highlight-title"], div[data-baseweb="tab-highlight"] {
        display: none !important; background-color: transparent !important;
    }
    
    .match-card {
        background: linear-gradient(135deg, #181b22 0%, #12141a 100%);
        border: 1px solid #252934; border-radius: 12px;
        padding: 18px; margin-bottom: 18px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    }
    .top3-card {
        background: linear-gradient(135deg, #1f2430 0%, #151821 100%);
        border: 1px solid #2e3445; border-radius: 12px;
        padding: 18px; margin-bottom: 14px;
    }
    .team-name { color: #ffffff !important; font-size: 17px; font-weight: bold; display: flex; align-items: center; gap: 12px; }
    .team-name.home { justify-content: flex-end; }
    .team-name.away { justify-content: flex-start; }
    .match-time-badge { color: #2ecc71; font-size: 13px; font-weight: bold; background: #132b1d; padding: 4px 12px; border-radius: 20px; display: inline-block; }
    .deadline-badge { color: #e74c3c; font-size: 11px; margin-top: 4px; display: block; }
    .odd-info { color: #b0b5c1 !important; font-size: 13px; margin-top: 6px; }
    .h2h-info { color: #3498db !important; font-size: 12px; text-align: center; margin-top: 6px; }
    .news-info { color: #e67e22 !important; font-size: 12px; text-align: center; margin-top: 3px; }
    .value-pick {
        background-color: #12291b; color: #2ecc71; border: 1px solid #1e4d2b;
        padding: 12px 16px; border-radius: 8px; font-size: 15px; margin-top: 14px; text-align: center;
    }
    .team-logo { width: 38px; height: 38px; object-fit: contain; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 4. 복합 머신러닝 AI 분석 엔진
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("🏆 AI 프로토 센터")
    st.caption("배당 + 해외 API + 뉴스 이슈 결합 완전체 AI 엔진")
    
    menu = st.radio(
        "NAVIGATION",
        ["⚽ 프로토 LIVE", "🔥 오늘의 TOP 3 픽", "📈 AI 적중률 리포트"],
        index=0
    )
    
    st.markdown("---")
    st.markdown("🌐 **상태**: 24시간 해외 API 자동 연동 중")

live_matches = load_betman_data()
analyzed_matches = []

if live_matches:
    for m in live_matches:
        odd_h, odd_d, odd_a = m["odd_h"], m["odd_d"], m["odd_a"]
        home_team, away_team = m["home"], m["away"]
        
        home_info = fetch_team_info_api(home_team)
        away_info = fetch_team_info_api(away_team)
        
        # 해외 API로부터 정밀 KST 경기 시간 및 H2H 수집
        fixture_details = fetch_fixture_details_api(home_info["id"], away_info["id"])
        
        # 해외 API 시간 우선 적용, 없을 경우 수집기 시간 백업 적용
        final_match_time = fixture_details["match_time"] or m.get("match_time") or m.get("time") or "08.14 예정"
        
        news_h = analyze_team_news_sentiment(home_team)
        news_a = analyze_team_news_sentiment(away_team)
        
        try:
            p_h = (1 / odd_h) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
            p_a = (1 / odd_a) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
        except Exception:
            p_h, p_a = 0.33, 0.33
            
        h2h_total = fixture_details["total"]
        h_h2h_bonus = (fixture_details["h_wins"] / h2h_total * 0.4) if h2h_total > 0 else 0
        a_h2h_bonus = (fixture_details["a_wins"] / h2h_total * 0.4) if h2h_total > 0 else 0
        
        exp_h = round(max(0.5, (p_h * 2.7) + h_h2h_bonus + news_h["mod"]), 2)
        exp_a = round(max(0.3, (p_a * 2.5) + a_h2h_bonus + news_a["mod"]), 2)

        h_probs = [(math.exp(-exp_h) * (exp_h**i)) / math.factorial(i) for i in range(6)]
        a_probs = [(math.exp(-exp_a) * (exp_a**j)) / math.factorial(j) for j in range(6)]

        h_win, draw, a_win = 0.0, 0.0, 0.0
        best_score, max_p = (0, 0), 0.0

        for h in range(6):
            for a in range(6):
                p = h_probs[h] * a_probs[a]
                if h > a: h_win += p
                elif h == a: draw += p
                else: a_win += p
                if p > max_p:
                    max_p = p
                    best_score = (h, a)

        candidates = [
            (f"🏠 {home_team} 승", h_win, h_win * odd_h),
            (f"🚀 {away_team} 승", a_win, a_win * odd_a),
            (f"🤝 무승부", draw, draw * odd_d),
        ]

        best_option, best_prob, best_ev = max(candidates, key=lambda x: x[2])
        best_prob_pct = round(best_prob * 100, 1)

        save_prediction(m, best_option, best_prob_pct, best_score)

        analyzed_matches.append({
            "match": m,
            "final_match_time": final_match_time,
            "home_logo": home_info["logo"],
            "away_logo": away_info["logo"],
            "h2h": fixture_details,
            "news_h": news_h,
            "news_a": news_a,
            "best_option": best_option,
            "best_prob_pct": best_prob_pct,
            "best_ev": best_ev,
            "best_score": best_score
        })

# -----------------------------------------------------------------------------
# 5. 메인 화면 렌더링
# -----------------------------------------------------------------------------

# [메뉴 1: ⚽ 프로토 LIVE]
if menu == "⚽ 프로토 LIVE":
    st.title("⚽ 프로토 라이브 경기")
    st.caption("배당률 + 해외 API 전적 + 실시간 뉴스 이슈 통합 분석 픽")
    
    tab_soccer, tab_baseball, tab_basketball = st.tabs(["⚽ 축구 LIVE", "⚾ 야구 LIVE", "🏀 농구 LIVE"])

    with tab_soccer:
        if analyzed_matches:
            st.success(f"✅ 현재 베트맨 발매 중인 축구 {len(analyzed_matches)}경기 연동 성공!")
            for item in analyzed_matches:
                m = item['match']
                logo_h = item['home_logo']
                logo_a = item['away_logo']
                h2h = item['h2h']
                news_h = item['news_h']
                news_a = item['news_a']
                m_time = item['final_match_time']
                d_time = m.get('deadline_time', '23:00 마감')

                st.markdown(f"""
                <div class='match-card'>
                    <div style='color:#a0a0a0; font-size:13px; margin-bottom:8px;'>🏆 {m['league']}</div>
                    <div style='display:flex; justify-content:space-between; align-items:center;'>
                        <div style='flex:1; text-align:right;'>
                            <span class='team-name home'>{m['home']} <img src='{logo_h}' class='team-logo'></span>
                        </div>
                        <div style='width:180px; text-align:center;'>
                            <span class='match-time-badge'>⚽ {m_time}</span>
                            <span class='deadline-badge'>({d_time})</span>
                        </div>
                        <div style='flex:1; text-align:left;'>
                            <span class='team-name away'><img src='{logo_a}' class='team-logo'> {m['away']}</span>
                        </div>
                    </div>
                    <div class='odd-info' style='text-align:center;'><b style='color:#ffffff;'>승무패 배당률</b> | 승 {m['odd_h']} · 무 {m['odd_d']} · 패 {m['odd_a']}</div>
                """, unsafe_allow_html=True)
                
                if h2h['total'] > 0:
                    st.markdown(f"<div class='h2h-info'>📊 <b>상대 전적 (최근 {h2h['total']}경기)</b>: {m['home']} {h2h['h_wins']}승 {h2h['draws']}무 {h2h['a_wins']}승 {m['away']}</div>", unsafe_allow_html=True)
                
                all_issues = news_h['issues'] + news_a['issues']
                if all_issues:
                    st.markdown(f"<div class='news-info'>📰 <b>뉴스 이슈 감지</b>: {' / '.join(all_issues)}</div>", unsafe_allow_html=True)
                else:
                    st.markdown("<div class='news-info' style='color:#7f8c8d;'>📰 <b>뉴스 이슈</b>: 부상/결장 특이 악재 없음 (정상 특성)</div>", unsafe_allow_html=True)

                st.markdown(
                    f"<div class='value-pick'>🎯 <b>AI 최고 가치 픽</b>: <span style='color:#ffffff; font-weight:bold;'>{item['best_option']}</span> "
                    f"(예상 확률 <b>{item['best_prob_pct']}%</b>) | 예상 스코어 <b>{item['best_score'][0]}:{item['best_score'][1]}</b></div>"
                    f"</div>",
                    unsafe_allow_html=True
                )
                st.write("")

# [메뉴 2: 🔥 오늘의 TOP 3 픽]
elif menu == "🔥 오늘의 TOP 3 픽":
    st.title("🔥 오늘의 AI 추천 TOP 3 가치 픽")
    st.caption("4대 복합 분석 엔진 결합 기대가치(EV) 극대화 경기")
    
    top_3_picks = sorted(analyzed_matches, key=lambda x: x['best_ev'], reverse=True)[:3]
    
    if top_3_picks:
        for idx, item in enumerate(top_3_picks, 1):
            m = item['match']
            m_time = item['final_match_time']
            st.markdown(f"""
                <div class='top3-card'>
                    <div style='color:#ff4b4b; font-weight:bold; font-size:18px; margin-bottom:8px;'>RANK {idx}</div>
                    <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;'>
                        <span style='color:#ffffff; font-size:16px; font-weight:bold;'>{m['home']} vs {m['away']}</span>
                        <span style='color:#2ecc71; font-size:13px; font-weight:bold;'>⚽ {m_time}</span>
                    </div>
                    <div style='background-color:#12291b; color:#2ecc71; border:1px solid #1e4d2b; padding:12px; border-radius:8px; font-size:15px;'>
                        🎯 <b>추천 픽</b>: <b style='color:#ffffff;'>{item['best_option']}</b> (예상 승률 <b>{item['best_prob_pct']}%</b>) | 예상 스코어 <b>{item['best_score'][0]}:{item['best_score'][1]}</b>
                    </div>
                </div>
            """, unsafe_allow_html=True)

# [메뉴 3: 📈 AI 적중률 리포트]
elif menu == "📈 AI 적중률 리포트":
    st.title("📈 AI 머신러닝 누적 적중률 & 오답 노트")
    st.caption("DB에 기록된 경기 실제 결과와 AI 예측 픽 자동 채점 리포트입니다.")
    
    stats = get_accuracy_stats()
    
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("누적 종료 경기", f"{stats['total']} 경기")
    with c2:
        st.metric("적중 완료 경기", f"{stats['correct']} 경기")
    with c3:
        st.metric("AI 승무패 적중률", f"{stats['accuracy']}%")
        
    st.markdown("---")
    st.subheader("📋 DB 저장 예측 및 자동 채점 기록")
    
    conn = sqlite3.connect("ai_predictions.db")
    df = pd.read_sql_query("""
        SELECT id, match_id, home_team, away_team, predicted_pick, predicted_prob, 
               actual_score AS '실제스코어', actual_result AS '상태', is_correct AS '적중여부(1/0)', created_at 
        FROM predictions ORDER BY id DESC
    """, conn)
    conn.close()
    
    if len(df) > 0:
        st.dataframe(df, use_container_width=True)
    else:
        st.write("아직 DB에 저장된 예측 기록이 없습니다.")
