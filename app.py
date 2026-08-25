import streamlit as st
import pandas as pd
import requests
import json
from datetime import datetime

# ---------------------------------------------------------
# 1. 페이지 기본 설정 (와이드 모드)
# ---------------------------------------------------------
st.set_page_config(page_title="D.J PROTO ANALYTICS V4", page_icon="⚡", layout="wide")

# 커스텀 CSS (프리미엄 UI 및 역배 레이더 스타일링)
st.markdown("""
<style>
    .match-card { background-color: #1E1E24; padding: 20px; border-radius: 10px; margin-bottom: 20px; border-left: 5px solid #3399FF; }
    .upset-alert { background-color: #3b1c1c; padding: 15px; border-radius: 8px; border: 1px solid #ff4d4d; color: #ffcccc; margin-top: 15px; }
    .premium-badge { background-color: #FFD700; color: #000; font-weight: bold; padding: 3px 8px; border-radius: 5px; font-size: 12px; }
    .status-scheduled { background-color: #1a3c5b; color: #66b3ff; padding: 4px 10px; border-radius: 5px; font-weight: bold; }
    .status-done { background-color: #1a4d2e; color: #80ffaa; padding: 4px 10px; border-radius: 5px; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 2. 데이터 불러오기 (GitHub 클라우드에서 최신 JSON 연동)
# ---------------------------------------------------------
@st.cache_data(ttl=60)
def load_data():
    # 기획자님의 깃허브 JSON 주소를 여기에 유지합니다 (예시 주소)
    # url = "https://raw.githubusercontent.com/기획자님아이디/저장소/main/dashboard_data.json"
    url = "https://raw.githubusercontent.com/chleowhd77-ops/-/main/dashboard_data.json" # 임시 주소 (실제 주소로 맞춰주세요)
    try:
        response = requests.get(url)
        return response.json()
    except:
        return []

data = load_data()

# ---------------------------------------------------------
# 3. 메인 화면 구성
# ---------------------------------------------------------
st.title("⚡ D.J PROTO ANALYTICS V4 (Premium)")
st.caption("딥러닝 기반 프로토 승부식 예측 및 배당 가치(EV) 분석 엔진")

# 4개의 핵심 탭 구성
tab1, tab2, tab3, tab4 = st.tabs(["🔥 AI 리포트", "🏆 오늘의 TOP 3", "📡 프로토 LIVE", "🎯 승무패 14경기"])

with tab1:
    st.subheader("📊 전체 경기 AI 예측 리포트")
    
    if not data:
        st.warning("현재 분석된 경기 데이터가 없습니다. 로봇이 데이터를 수집 중입니다.")
    
    current_time = datetime.now()

    for match in data:
        # 경기 시간 파싱
        try:
            match_time = datetime.strptime(match.get('datetime', '2099-12-31 00:00'), '%Y-%m-%d %H:%M')
        except:
            match_time = current_time
        
        # 상태 표시 로직 (오늘 우리가 수정한 핵심 부분!)
        if match.get('status') == '종료':
            status_html = f"<span class='status-done'>종료</span>"
        elif match_time > current_time:
            status_html = f"<span class='status-scheduled'>진행 예정</span>"
        else:
            status_html = f"<span class='status-scheduled'>LIVE (진행중)</span>"

        # 카드 렌더링 시작
        with st.container():
            st.markdown(f"""
            <div class="match-card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <h4 style="margin:0;">{match.get('home_team', '홈팀')} <img src="{match.get('home_logo', '')}" width="30"> vs <img src="{match.get('away_logo', '')}" width="30"> {match.get('away_team', '원정팀')}</h4>
                    <div>{status_html}</div>
                </div>
                <p style="color: gray; font-size: 14px;">경기일시: {match.get('datetime', '미정')} | 리그: {match.get('league', 'K리그1')}</p>
            """, unsafe_allow_html=True)
            
            # --- 일반 분석 (베이직 티어용) ---
            col1, col2, col3 = st.columns(3)
            with col1:
                st.info(f"**승무패 예측**\n\n🥇 1순위: {match.get('predict_1x2', '분석중')} ({match.get('prob_1x2', '0%')})")
            with col2:
                st.success(f"**핸디캡 예측**\n\n🎯 {match.get('predict_handi', '분석중')}")
            with col3:
                st.warning(f"**언더오버 예측**\n\n📈 {match.get('predict_unover', '분석중')} (기준점: {match.get('base_unover', '2.5')})")
            
            # --- [NEW] VIP 역배 레이더 발동 영역 ---
            # 로봇이 JSON에 'upset_warning' 데이터를 담아 보내면 이 경고창이 뜹니다.
            if match.get('upset_warning'):
                st.markdown(f"""
                <div class="upset-alert">
                    <span class="premium-badge">VIP 전용</span> 🚨 <b>슈퍼 역배 주의보 포착!</b><br>
                    <span style="font-size: 14px;">{match.get('upset_reason', '역배 전조 증상이 포착되었습니다. 고배당 스나이핑 찬스!')}</span>
                </div>
                """, unsafe_allow_html=True)

            st.markdown("</div>", unsafe_allow_html=True)

with tab2:
    st.subheader("🏆 배당 가치(EV) 최상위 꿀픽 TOP 3")
    st.write("진행 예정입니다. (데이터 분석 중)")

with tab3:
    st.subheader("📡 실시간 라이브 스코어 및 채점")
    st.write("진행 예정입니다. (데이터 분석 중)")

with tab4:
    st.subheader("🎯 승무패 14경기 독식 예측")
    st.write("진행 예정입니다. (데이터 분석 중)")
