import streamlit as st
import math
import time
import re
import sqlite3
import pandas as pd
from datetime import datetime
from urllib.parse import quote
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from webdriver_manager.microsoft import EdgeChromiumDriverManager

# -----------------------------------------------------------------------------
# 0. 데이터베이스(DB) 및 오답 노트 자동 채점 시스템
# -----------------------------------------------------------------------------
st.set_page_config(page_title="프로토 AI 스마트 픽 대시보드", page_icon="🏆", layout="wide")

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

def update_match_results(match_id, h_score, a_score, is_finished=True):
    """실시간 점수를 DB에 업데이트하고 경기 종료 시 자동 채점합니다."""
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT predicted_pick, home_team, away_team FROM predictions WHERE match_id = ?", (match_id,))
    row = cursor.fetchone()
    
    if row:
        predicted_pick, home, away = row
        actual_score = f"{h_score}:{a_score}"
        
        # 실제 결과 판정 (승/무/패)
        if h_score > a_score:
            actual_res = "HOME_WIN"
        elif h_score == a_score:
            actual_res = "DRAW"
        else:
            actual_res = "AWAY_WIN"
            
        # AI 예측 적중 여부 채점
        is_correct = 0
        if "승" in predicted_pick and home in predicted_pick and actual_res == "HOME_WIN":
            is_correct = 1
        elif "무승부" in predicted_pick and actual_res == "DRAW":
            is_correct = 1
        elif "승" in predicted_pick and away in predicted_pick and actual_res == "AWAY_WIN":
            is_correct = 1
            
        status = "FINISHED" if is_finished else "LIVE"
        
        cursor.execute("""
            UPDATE predictions 
            SET actual_score = ?, actual_result = ?, is_correct = ?
            WHERE match_id = ?
        """, (actual_score, status if is_finished else "LIVE", is_correct, match_id))
        conn.commit()
        
    conn.close()

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
# 1. 팀 로고 처리 모듈
# -----------------------------------------------------------------------------
TEAM_LOGOS = {
    "에버턴": "https://upload.wikimedia.org/wikipedia/en/thumb/7/7c/Everton_FC_logo.svg/120px-Everton_FC_logo.svg.png",
    "뉴캐슬 유나이티드": "https://upload.wikimedia.org/wikipedia/en/thumb/5/56/Newcastle_United_Logo.svg/120px-Newcastle_United_Logo.svg.png",
    "맨체스터 유나이티드": "https://upload.wikimedia.org/wikipedia/en/thumb/7/7a/Manchester_United_FC_crest.svg/120px-Manchester_United_FC_crest.svg.png",
    "리즈 유나이티드": "https://upload.wikimedia.org/wikipedia/en/thumb/5/54/Leeds_United_F.C._logo.svg/120px-Leeds_United_F.C._logo.svg.png",
    "파리 생제르맹": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a7/Paris_Saint-Germain_F.C..svg/120px-Paris_Saint-Germain_F.C..svg.png",
    "애스턴 빌라": "https://upload.wikimedia.org/wikipedia/en/thumb/9/9f/Aston_Villa_logo.svg/120px-Aston_Villa_logo.svg.png"
}

def get_team_logo(team_name):
    if team_name in TEAM_LOGOS:
        return TEAM_LOGOS[team_name]
    clean_name = quote(team_name[:2])
    return f"https://ui-avatars.com/api/?name={clean_name}&background=2A2E39&color=FF4B4B&bold=true&rounded=true&size=64"

# -----------------------------------------------------------------------------
# 2. 커스텀 CSS 스타일링
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
    
    .metric-card {
        background: linear-gradient(135deg, #1e222d 0%, #14171f 100%);
        border: 1px solid #2a2e39; border-radius: 12px; padding: 20px; margin-bottom: 20px;
    }
    .top3-card {
        background-color: #1e222d; border: 1px solid #2a2e39; border-radius: 10px;
        padding: 16px; margin-bottom: 12px;
    }
    .team-name { color: #ffffff !important; font-size: 16px; font-weight: bold; display: flex; align-items: center; gap: 10px; }
    .team-name.home { justify-content: flex-end; }
    .team-name.away { justify-content: flex-start; }
    .score-wait { color: #ffffff; font-size: 14px; font-weight: bold; }
    .score-live { color: #ff4b4b; font-size: 20px; font-weight: bold; }
    .odd-info { color: #d1d5db !important; font-size: 13px; }
    .value-pick {
        background-color: #1a3323; color: #2ecc71; border: 1px solid #276738;
        padding: 10px 14px; border-radius: 8px; font-size: 14px; margin-top: 12px;
    }
    .team-logo { width: 32px; height: 32px; object-fit: contain; border-radius: 50%; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 3. 투 트랙 수집 로봇 (베트맨 연동 + 라이브 스코어 추적)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=300)
def scrape_betman():
    url = "https://www.betman.co.kr/main/mainPage/gamebuy/gameSlip.do?gmId=G101&gmTs=260095"
    matches = []
    
    try:
        options = Options()
        options.add_argument('--log-level=3')
        service = Service(EdgeChromiumDriverManager().install())
        driver = webdriver.Edge(service=service, options=options)
        
        driver.get(url)
        time.sleep(6)
        
        for _ in range(15):
            driver.execute_script("window.scrollBy(0, 800);")
            time.sleep(1)
            
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        driver.quit() 
        
        strings = list(soup.stripped_strings)
        seen_matches = set()
        
        for i, s in enumerate(strings):
            if s.lower() in ['vs', 'v s']:
                home = strings[i-1].strip() if i > 0 else "홈"
                away = strings[i+1].strip() if i < len(strings)-1 else "원정"
                
                match_id = f"{home[:2]}vs{away[:2]}"
                if match_id in seen_matches:
                    continue
                    
                is_soccer = False
                match_time = "예정"
                
                for k in range(max(0, i-15), min(len(strings), i+15)):
                    if '축구' in strings[k]:
                        is_soccer = True
                    if re.search(r'\d{2}:\d{2}', strings[k]):
                        match_time = strings[k]
                        
                if not is_soccer:
                    continue
                    
                odds = []
                for j in range(i+1, min(i+50, len(strings))):
                    nxt = strings[j]
                    if nxt.lower() in ['vs', 'v s'] or '야구' in nxt or '농구' in nxt:
                        break
                    if '핸디캡' in nxt or '언더오버' in nxt or 'sum' in nxt.lower():
                        break
                    if re.match(r'^[1-9]\d*\.\d{2}$', nxt):
                        odds.append(float(nxt))
                        
                    if len(odds) == 3:
                        matches.append({
                            "id": match_id,
                            "league": "축구",
                            "time": match_time,
                            "home": home,
                            "away": away,
                            "odd_h": odds[0],
                            "odd_d": odds[1],
                            "odd_a": odds[2],
                            "live_score": None, # 라이브 스코어 추가 영역
                            "status": "UPCOMING" # UPCOMING, LIVE, FINISHED
                        })
                        seen_matches.add(match_id)
                        break 
                        
    except Exception:
        pass
    
    return matches

# -----------------------------------------------------------------------------
# 4. 좌측 사이드바 메뉴 생성
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("🏆 AI 프로토 센터")
    st.caption("메뉴를 선택하세요")
    
    menu = st.radio(
        "NAVIGATION",
        ["⚽ 프로토 LIVE", "🔥 오늘의 TOP 3 픽", "📈 AI 적중률 리포트"],
        index=0
    )
    
    st.markdown("---")
    st.markdown("💡 **Tip**: 경기 진행 상황 및 스코어는 실시간으로 감지되어 기록됩니다.")

live_matches = scrape_betman()
analyzed_matches = []

if live_matches:
    for m in live_matches:
        odd_h, odd_d, odd_a = m["odd_h"], m["odd_d"], m["odd_a"]
        home_team, away_team = m["home"], m["away"]
        
        try:
            p_h = (1 / odd_h) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
            p_a = (1 / odd_a) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
        except Exception:
            p_h, p_a = 0.33, 0.33
            
        exp_h = round(max(0.6, p_h * 3.0), 2)
        exp_a = round(max(0.4, p_a * 2.8), 2)

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
            "best_option": best_option,
            "best_prob_pct": best_prob_pct,
            "best_ev": best_ev,
            "best_score": best_score
        })

# -----------------------------------------------------------------------------
# 5. 메뉴별 화면 렌더링
# -----------------------------------------------------------------------------

# [메뉴 1: ⚽ 프로토 LIVE]
if menu == "⚽ 프로토 LIVE":
    st.title("⚽ 프로토 라이브 경기")
    st.caption("실시간 배당률 연동 및 라이브 스코어 / AI 스마트 픽")
    
    tab_soccer, tab_baseball, tab_basketball = st.tabs(["⚽ 축구 LIVE", "⚾ 야구 LIVE", "🏀 농구 LIVE"])

    with tab_soccer:
        if analyzed_matches:
            st.success(f"✅ 현재 베트맨 발매 중인 축구 {len(analyzed_matches)}경기 연동 성공!")
            for item in analyzed_matches:
                m = item['match']
                logo_h = get_team_logo(m['home'])
                logo_a = get_team_logo(m['away'])

                with st.container():
                    st.markdown(f"<span style='color:#a0a0a0;'>🏆 {m['league']}</span>", unsafe_allow_html=True)
                    c1, c2, c3 = st.columns([5, 3, 5])
                    
                    with c1: 
                        st.markdown(f"<div class='team-name home'>{m['home']} <img src='{logo_h}' class='team-logo'></div>", unsafe_allow_html=True)
                    
                    # 스코어 및 진행 상황 표시
                    with c2: 
                        if m['status'] == 'LIVE':
                            st.markdown(f"<div class='score-live' style='text-align: center;'>⚽ {m['live_score']}<br><span style='font-size:12px; color:#2ecc71;'>LIVE 진행 중</span></div>", unsafe_allow_html=True)
                        elif m['status'] == 'FINISHED':
                            st.markdown(f"<div class='score-wait' style='text-align: center;'>종료<br>{m['live_score']}</div>", unsafe_allow_html=True)
                        else:
                            st.markdown(f"<div class='score-wait' style='text-align: center;'>{m['time']} 마감<br>VS</div>", unsafe_allow_html=True)
                            
                    with c3: 
                        st.markdown(f"<div class='team-name away'><img src='{logo_a}' class='team-logo'> {m['away']}</div>", unsafe_allow_html=True)

                st.write("")
                st.markdown(f"<div class='odd-info' style='text-align:center;'><b style='color:#ffffff;'>승무패 배당률</b> | 승 {m['odd_h']} · 무 {m['odd_d']} · 패 {m['odd_a']}</div>", unsafe_allow_html=True)
                st.markdown(
                    f"<div class='value-pick'>🎯 <b>AI 최고 가치 픽</b>: <span style='color:#ffffff; font-weight:bold;'>{item['best_option']}</span> "
                    f"(예상 확률 <b>{item['best_prob_pct']}%</b>) | 예상 스코어 <b>{item['best_score'][0]}:{item['best_score'][1]}</b></div>",
                    unsafe_allow_html=True
                )
                st.markdown("---")
        else:
            st.warning("⚠️ 현재 발매 중인 축구 경기가 없거나 데이터를 로딩 중입니다.")

    with tab_baseball:
        st.info("⚾ 야구 프로토 모듈 준비 중...")

    with tab_basketball:
        st.info("🏀 농구 프로토 모듈 준비 중...")

# [메뉴 2: 🔥 오늘의 TOP 3 픽]
elif menu == "🔥 오늘의 TOP 3 픽":
    st.title("🔥 오늘의 AI 추천 TOP 3 가치 픽")
    st.caption("AI가 수집된 전체 경기 중 가장 기대가치(EV)가 높은 3개 경기를 엄선했습니다.")
    
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
    else:
        st.info("분석할 경기 데이터가 아직 준비되지 않았습니다.")

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