import streamlit as st
import math
import json
import requests
import sqlite3
import pandas as pd
from urllib.parse import quote
from bs4 import BeautifulSoup

# -----------------------------------------------------------------------------
# 0. 기본 설정 및 API-Football 셋팅
# -----------------------------------------------------------------------------
st.set_page_config(page_title="프로토 AI 스마트 픽 대시보드", page_icon="🏆", layout="wide")

# ★ 회원님의 API-Football 키를 입력하세요
API_KEY = "YOUR_API_KEY_HERE"
API_HOST = "v3.football.api-sports.io"

headers = {
    'x-rapidapi-host': API_HOST,
    'x-rapidapi-key': API_KEY
}

# 베트맨 한글 팀명 ➔ 해외 API 영문 팀명 매핑 사전
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

# 차단 방지용 직접 매핑
DIRECT_LOGO_MAP = {
    "LDU키토": "https://media.api-sports.io/football/teams/1148.png"
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
# 1. 해외 API 및 뉴스 감성 분석(Sentiment) 엔진
# -----------------------------------------------------------------------------
@st.cache_data(ttl=86400)
def fetch_team_info_api(team_name):
    search_name = TEAM_NAME_MAP.get(team_name, team_name)
    url = f"https://{API_HOST}/teams"
    params = {"search": search_name}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        res_data = response.json()
        if res_data.get("response") and len(res_data["response"]) > 0:
            team_data = res_data["response"][0]["team"]
            logo = DIRECT_LOGO_MAP.get(team_name, team_data.get("logo"))
            return {"id": team_data["id"], "logo": logo}
    except Exception:
        pass
        
    clean_name = quote(team_name[:2])
    fallback_logo = f"https://ui-avatars.com/api/?name={clean_name}&background=2A2E39&color=FF4B4B&bold=true&rounded=true&size=64"
    return {"id": None, "logo": DIRECT_LOGO_MAP.get(team_name, fallback_logo)}

@st.cache_data(ttl=43200)
def fetch_head_to_head_api(home_id, away_id):
    if not home_id or not away_id:
        return {"h_wins": 0, "draws": 0, "a_wins": 0, "total": 0}
        
    url = f"https://{API_HOST}/fixtures/headtohead"
    params = {"h2h": f"{home_id}-{away_id}"}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        res_data = response.json()
        matches = res_data.get("response", [])
        
        h_wins, draws, a_wins = 0, 0, 0
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
                
        return {"h_wins": h_wins, "draws": draws, "a_wins": a_wins, "total": len(matches[:10])}
    except Exception:
        return {"h_wins": 0, "draws": 0, "a_wins": 0, "total": 0}

@st.cache_data(ttl=3600) # 1시간마다 뉴스 분석
def analyze_team_news_sentiment(team_name):
    """네이버 스포츠 뉴스 헤드라인 기반 부상/로테이션/체력 감성 분석"""
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
                        score_mod -= 0.15 # 악재 1개당 기대득점 -0.15 감산
                        if kw not in detected_issues: detected_issues.append(f"⚠️ {kw}")
                for kw in keywords_positive:
                    if kw in t:
                        score_mod += 0.10 # 호재 1개당 기대득점 +0.10 가산
                        if kw not in detected_issues: detected_issues.append(f"🔥 {kw}")
    except Exception:
        pass
        
    return {"mod": round(score_mod, 2), "issues": detected_issues[:2]}

# -----------------------------------------------------------------------------
# 2. GitHub 수집 데이터 로드 및 DB 관리
# -----------------------------------------------------------------------------
@st.cache_data(ttl=300)
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
# 3. CSS 스타일링
# -----------------------------------------------------------------------------
st.markdown("""
    <style>
    .stApp { background-color: #121418; }
    [data-testid="stSidebar"] {
        background-color: #1a1d24 !important;
        border-right: 1px solid #2a2e39;
    }
    div[data-testid="stTabs"] { width: 100% !important; }
    .stTabs [data-baseweb="tab-list"], div[role="tablist"] {
        width: 100% !important; display: flex !important; justify-content: space-between !important;
        background-color: transparent !important; border: none !important; border-bottom: 1px solid #333333 !important;
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
    .top3-card {
        background-color: #1e222d; border: 1px solid #2a2e39; border-radius: 10px;
        padding: 16px; margin-bottom: 12px;
    }
    .team-name { color: #ffffff !important; font-size: 16px; font-weight: bold; display: flex; align-items: center; gap: 10px; }
    .team-name.home { justify-content: flex-end; }
    .team-name.away { justify-content: flex-start; }
    .score-wait { color: #ffffff; font-size: 14px; font-weight: bold; }
    .odd-info { color: #d1d5db !important; font-size: 13px; }
    .h2h-info { color: #3498db !important; font-size: 12px; text-align: center; margin-top: 4px; }
    .news-info { color: #e67e22 !important; font-size: 12px; text-align: center; margin-top: 2px; }
    .value-pick {
        background-color: #1a3323; color: #2ecc71; border: 1px solid #276738;
        padding: 10px 14px; border-radius: 8px; font-size: 14px; margin-top: 12px;
    }
    .team-logo { width: 36px; height: 36px; object-fit: contain; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 4. 복합 머신러닝 AI 분석 엔진 (배당 + 상대전적 + 뉴스 감성 분석)
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("🏆 AI 프로토 센터")
    st.caption("배당 + 상대전적 + 뉴스 이슈 결합 완전체 AI 엔진")
    
    menu = st.radio(
        "NAVIGATION",
        ["⚽ 프로토 LIVE", "🔥 오늘의 TOP 3 픽", "📈 AI 적중률 리포트"],
        index=0
    )
    
    st.markdown("---")
    st.markdown("🤖 **AI 모델**: 뉴스 감성 분석 모드 가동 중")

live_matches = load_betman_data()
analyzed_matches = []

if live_matches:
    for m in live_matches:
        odd_h, odd_d, odd_a = m["odd_h"], m["odd_d"], m["odd_a"]
        home_team, away_team = m["home"], m["away"]
        
        # 1. 해외 API 데이터 (팀 ID & 상대전적 H2H)
        home_info = fetch_team_info_api(home_team)
        away_info = fetch_team_info_api(away_team)
        h2h = fetch_head_to_head_api(home_info["id"], away_info["id"])
        
        # 2. 뉴스 감성 분석 (부상/로테이션 이슈 감지)
        news_h = analyze_team_news_sentiment(home_team)
        news_a = analyze_team_news_sentiment(away_team)
        
        # 3. 배당률 기반 승률 계산
        try:
            p_h = (1 / odd_h) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
            p_a = (1 / odd_a) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
        except Exception:
            p_h, p_a = 0.33, 0.33
            
        # 4. 상대전적 + 뉴스 이슈 가중치 합산 (4대 엔진 결합 공식)
        h_h2h_bonus = (h2h["h_wins"] / h2h["total"] * 0.4) if h2h["total"] > 0 else 0
        a_h2h_bonus = (h2h["a_wins"] / h2h["total"] * 0.4) if h2h["total"] > 0 else 0
        
        exp_h = round(max(0.5, (p_h * 2.7) + h_h2h_bonus + news_h["mod"]), 2)
        exp_a = round(max(0.3, (p_a * 2.5) + a_h2h_bonus + news_a["mod"]), 2)

        # 5. 포아송 스코어 확률 계산
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
            "home_logo": home_info["logo"],
            "away_logo": away_info["logo"],
            "h2h": h2h,
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

                with st.container():
                    st.markdown(f"<span style='color:#a0a0a0;'>🏆 {m['league']}</span>", unsafe_allow_html=True)
                    c1, c2, c3 = st.columns([5, 3, 5])
                    
                    with c1: 
                        st.markdown(f"<div class='team-name home'>{m['home']} <img src='{logo_h}' class='team-logo'></div>", unsafe_allow_html=True)
                    with c2: 
                        st.markdown(f"<div class='score-wait' style='text-align: center;'>{m['time']} 마감<br>VS</div>", unsafe_allow_html=True)
                    with c3: 
                        st.markdown(f"<div class='team-name away'><img src='{logo_a}' class='team-logo'> {m['away']}</div>", unsafe_allow_html=True)

                st.write("")
                st.markdown(f"<div class='odd-info' style='text-align:center;'><b style='color:#ffffff;'>승무패 배당률</b> | 승 {m['odd_h']} · 무 {m['odd_d']} · 패 {m['odd_a']}</div>", unsafe_allow_html=True)
                
                # 상대 전적 요약
                if h2h['total'] > 0:
                    st.markdown(f"<div class='h2h-info'>📊 <b>상대 전적 (최근 {h2h['total']}경기)</b>: {m['home']} {h2h['h_wins']}승 {h2h['draws']}무 {h2h['a_wins']}승 {m['away']}</div>", unsafe_allow_html=True)
                
                # 뉴스 감성 분석 요약
                all_issues = news_h['issues'] + news_a['issues']
                if all_issues:
                    st.markdown(f"<div class='news-info'>📰 <b>뉴스 이슈 감지</b>: {' / '.join(all_issues)}</div>", unsafe_allow_html=True)
                else:
                    st.markdown("<div class='news-info' style='color:#7f8c8d;'>📰 <b>뉴스 이슈</b>: 부상/결장 특이 악재 없음 (정상 특성)</div>", unsafe_allow_html=True)

                st.markdown(
                    f"<div class='value-pick'>🎯 <b>AI 최고 가치 픽</b>: <span style='color:#ffffff; font-weight:bold;'>{item['best_option']}</span> "
                    f"(예상 확률 <b>{item['best_prob_pct']}%</b>) | 예상 스코어 <b>{item['best_score'][0]}:{item['best_score'][1]}</b></div>",
                    unsafe_allow_html=True
                )
                st.markdown("---")

# [메뉴 2: 🔥 오늘의 TOP 3 픽]
elif menu == "🔥 오늘의 TOP 3 픽":
    st.title("🔥 오늘의 AI 추천 TOP 3 가치 픽")
    st.caption("4대 복합 분석 엔진 결합 기대가치(EV) 극대화 경기")
    
    top_3_picks = sorted(analyzed_matches, key=lambda x: x['best_ev'], reverse=True)[:3]
    
    if top_3_picks:
        for idx, item in enumerate(top_3_picks, 1):
            m = item['match']
            st.markdown(f"""
                <div class='top3-card'>
                    <div style='color:#ff4b4b; font-weight:bold; font-size:18px; margin-bottom:8px;'>RANK {idx}</div>
                    <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;'>
                        <span style='color:#ffffff; font-size:16px; font-weight:bold;'>{m['home']} vs {m['away']}</span>
                        <span style='color:#a0a0a0; font-size:13px;'>{m['time']} 마감</span>
                    </div>
                    <div style='background-color:#1a3323; color:#2ecc71; border:1px solid #276738; padding:12px; border-radius:8px; font-size:15px;'>
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
