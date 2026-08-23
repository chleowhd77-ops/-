import os
import streamlit as st
import json
import requests
import sqlite3
import pandas as pd
import time
from datetime import datetime, timezone, timedelta
import re

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

GITHUB_REPO = "chleowhd77-ops/-"

# -----------------------------------------------------------------------------
# 1. 초경량 데이터 로더 (API 호출 & 수학 계산 전부 로봇에게 떠넘김)
# -----------------------------------------------------------------------------
def load_dashboard_data():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/dashboard_data.json?t={int(time.time())}"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200: return res.json()
    except: pass
    return {"proto": [], "toto14": [], "top3": []}

def load_live_scores():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/live_scores.json?t={int(time.time())}"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200: return res.json()
    except: pass
    return {}

def download_db():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/ai_predictions.db?t={int(time.time())}"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            with open("ai_predictions.db", "wb") as f:
                f.write(res.content)
    except: pass

download_db()

# -----------------------------------------------------------------------------
# 2. 디자인 (CSS) 
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
    
    .vs-row { display: flex; justify-content: space-between; align-items: center; width: 100%; margin-bottom: 15px; }
    .team-box { flex: 1; display: flex; align-items: center; gap: 15px; }
    .team-box.home { justify-content: flex-end; text-align: right; }
    .team-box.away { justify-content: flex-start; text-align: left; }
    .team-info-wrapper { display: flex; flex-direction: column; justify-content: center; }
    .team-box.home .team-info-wrapper { align-items: flex-end; }
    .team-box.away .team-info-wrapper { align-items: flex-start; }
    .team-name-text { display: block; color: #F8FAFC !important; font-size: 22px; font-weight: 900; letter-spacing: -0.5px; }
    .team-form-text { display: block; color: #64748B; font-size: 12px; font-weight: 700; letter-spacing: 1px; margin-top: 4px; }
    
    .injury-badge { display: block; color: #F87171; font-size: 11px; font-weight: 900; margin-top: 3px; background: rgba(248,113,113,0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid #F87171; }
    .fatigue-badge { display: block; color: #F59E0B; font-size: 11px; font-weight: 900; margin-top: 3px; background: rgba(245,158,11,0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid #F59E0B; }
    .rank-badge { display: block; color: #38BDF8; font-size: 11px; font-weight: 900; margin-top: 3px; background: rgba(56,189,248,0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid #38BDF8; }
    .money-badge { display: block; color: #10B981; font-size: 11px; font-weight: 900; margin-top: 3px; background: rgba(16,185,129,0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid #10B981; animation: pulse 2s infinite; }
    @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(16,185,129, 0.4); } 70% { box-shadow: 0 0 0 5px rgba(16,185,129, 0); } 100% { box-shadow: 0 0 0 0 rgba(16,185,129, 0); } }

    .team-logo { width: 55px !important; height: 55px !important; object-fit: contain; }
    
    .center-time-box { width: 140px; text-align: center; flex-shrink: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
    .match-time-text { color: #CBD5E1; font-size: 15px; font-weight: 700; display: block; margin-bottom: 4px;}
    .live-score { font-size: 32px; font-weight: 900; color: #00F2FE; display: block; margin-bottom: 4px; text-shadow: 0 0 10px rgba(0,242,254,0.6); }
    .deadline-open { color: #00F2FE; font-size: 12px; font-weight: 900; border: 1px solid #00F2FE; padding: 3px 8px; border-radius: 4px; display: inline-block;}
    .deadline-closed { color: #EF4444; font-size: 12px; font-weight: 900; background: rgba(239, 68, 68, 0.1); border: 1px solid #EF4444; padding: 3px 8px; border-radius: 4px; display: inline-block;}
    .odd-bar { display: flex; justify-content: space-between; background: #111827; border-radius: 6px; padding: 12px 20px; margin-bottom: 15px; border: 1px solid #1F2937; }
    .odd-item { font-size: 14px; color: #94A3B8; font-weight: 700; }
    .odd-val { color: #F1F5F9; font-weight: 900; margin-left: 6px; }
    .ai-story { background: rgba(0, 242, 254, 0.05); border-left: 3px solid #00F2FE; padding: 12px 15px; font-size: 14px; color: #E2E8F0; font-weight: 700; border-radius: 4px; margin-bottom: 15px; line-height: 1.6; }
    
    .pred-grid { display: flex; gap: 12px; }
    .pred-box { flex: 1; background: #0D1424; border: 1px solid #1E293B; border-radius: 8px; padding: 16px; text-align: center; display: flex; flex-direction: column; justify-content: center; align-items: center; }
    .pred-label { font-size: 12px; color: #64748B; font-weight: 900; margin-bottom: 10px; }
    
    .pred-value { display: block; font-size: 16px; color: #F8FAFC; font-weight: 900; line-height: 1.4; margin-bottom: 12px; word-break: keep-all; text-align: center; }
    .pred-prob { display: inline-block; font-size: 14px; color: #0B0F19; font-weight: 900; background-color: #10B981; padding: 4px 14px; border-radius: 20px; box-shadow: 0 2px 8px rgba(16, 185, 129, 0.3); }
    
    .prob-bar-container { display: flex; height: 12px; border-radius: 6px; overflow: hidden; margin-top: 10px; background: #1E293B;}
    .prob-bar-win { background-color: #00F2FE; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color:#000; }
    .prob-bar-draw { background-color: #10B981; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color:#000; }
    .prob-bar-lose { background-color: #EF4444; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color:#000; }
    .badge-primary { background: rgba(0, 242, 254, 0.1); color: #00F2FE; border: 1px solid #00F2FE; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 900; }

    @keyframes blink { 50% { opacity: 0.5; } }
    
    @media (max-width: 640px) {
        .vs-row { align-items: flex-start !important; }
        .team-box { flex-direction: column !important; justify-content: flex-start !important; gap: 8px !important; }
        .team-box.home { flex-direction: column-reverse !important; } 
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

# -----------------------------------------------------------------------------
# 3. 레이아웃 뼈대 생성
# -----------------------------------------------------------------------------
st.markdown("""
<div class='app-header'>
    <h1>D.J PROTO ANALYTICS</h1>
    <p>AI 예측 기반 스마트 프로토 대시보드</p>
</div>
""", unsafe_allow_html=True)

main_tab1, main_tab2, main_tab3, main_tab4 = st.tabs([
    "프로토 LIVE", "승무패 14경기", "오늘의 TOP 3", "AI 리포트"
])

dashboard_data = load_dashboard_data()
live_scores_data = load_live_scores()

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
                if dh > m_dt.hour + 12: d_dt -= timedelta(days=1)
                elif d_dt >= m_dt: d_dt = m_dt - timedelta(minutes=10)
            is_closed = now >= d_dt
            if m_dt <= now <= m_dt + timedelta(hours=2): return "LIVE", is_closed
            elif now > m_dt + timedelta(hours=2): return "FINISHED", is_closed
            else: return "UPCOMING", is_closed
    except: pass
    return "UPCOMING", False

def render_logo_html(logo_url):
    if logo_url: return f"<img src='{logo_url}' class='team-logo' onerror=\"this.style.display='none';\">"
    return ""

# -----------------------------------------------------------------------------
# 🌟 박스 렌더링 함수
# -----------------------------------------------------------------------------
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
            if prob_pct >= 65: stars = "⭐⭐⭐"
            elif prob_pct >= 50: stars = "⭐⭐"
            else: stars = "⭐"
            
            label = f"🥇 강력 추천 ({pick.get('label', '')}) {stars}" if is_top3_tab else f"🥇 {pick.get('label', '')} {stars}"
        else:
            bg_style = ""
            title_color = "color:#64748B;"
            if is_top3_tab:
                label = f"서브 추천 ({pick.get('label', '')})" 
            else:
                label = pick.get('label', '')
                
        html += f"<div class='pred-box' style='{bg_style}'><div class='pred-label' style='{title_color}'>{label}</div><span class='pred-value'>{pick.get('html_pick', '')}</span><span class='pred-prob'>{prob_pct}%</span></div>"
    return html

# -----------------------------------------------------------------------------
# [TAB 1] 프로토 LIVE
# -----------------------------------------------------------------------------
with main_tab1:
    sub_soccer, sub_baseball, sub_basketball = st.tabs(["축구", "야구", "농구"])
    with sub_soccer:
        st.markdown("<div style='background:rgba(0, 242, 254, 0.1); border:1px solid #00F2FE; color:#00F2FE; padding:12px; border-radius:8px; text-align:center; font-weight:700; margin-bottom:24px;'>💡 안내: 완전히 채점이 완료된 종료 경기는 [AI 리포트] 탭의 오답노트에 영구 보관됩니다.</div>", unsafe_allow_html=True)
        
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
            
            if selected_league != "전체 리그 보기":
                proto_list = [m for m in proto_list if m.get('league') == selected_league]
                
            if sort_urgent:
                proto_list = sorted(proto_list, key=lambda x: x.get('timestamp', 9999999999))
                
            displayed_count = 0
            for item in proto_list:
                m = item['match']
                logo_h_tag = render_logo_html(item.get("home_logo"))
                logo_a_tag = render_logo_html(item.get("away_logo"))
                raw_deadline = m.get("deadline_time", "23:00")
                match_status, is_closed = get_match_status(item.get("final_match_time", ""), raw_deadline)
                
                a_result = m.get('actual_result', 'PENDING')
                if match_status == "FINISHED" or a_result == 'FINISHED':
                    continue
                
                displayed_count += 1
                match_id_str = str(m.get('id', ''))
                
                # 🚀 [핵심 수정] 실시간 스코어 데이터에 아이디가 존재하면 시간 무시하고 무조건 라이브 강제 적용!
                is_live_now = (match_status == "LIVE") or (m.get('match_time') == '마감/진행중') or (match_id_str in live_scores_data)
                
                if is_live_now:
                    if match_id_str in live_scores_data:
                        live_info = live_scores_data[match_id_str]
                        score_text = live_info.get("score", "0:0")
                        if not score_text or score_text == "-": score_text = "0:0"
                        event_text = live_info.get("event", "")
                        
                        event_html = f"<div style='margin-bottom:6px; font-size:11px; color:#10B981; font-weight:900;'>{event_text}</div>" if event_text else ""
                        time_display = f"{event_html}<span class='live-score'>{score_text}</span><span class='deadline-closed' style='background:rgba(239, 68, 68, 0.1); border-color:#EF4444; color:#EF4444; animation: blink 2s infinite;'>🔴 LIVE</span>"
                    else:
                        time_display = f"<span class='live-score'>0:0</span><span class='deadline-closed' style='background:rgba(239, 68, 68, 0.1); border-color:#EF4444; color:#EF4444; animation: blink 2s infinite;'>🔴 LIVE</span>"
                else:
                    badge = f"<span class='deadline-closed'>픽 마감</span>" if is_closed else f"<span class='deadline-open'>{raw_deadline}</span>"
                    time_display = f"<span class='match-time-text'>{item.get('final_match_time', '')}</span>{badge}"
                
                dynamic_pred_boxes = generate_pred_boxes(item.get('ev_sorted_picks', []), is_top3_tab=False)
                
                html_code = (
                    f"<div class='match-card'>"
                    f"<div class='league-title'>{m.get('league','축구')}</div>"
                    f"<div class='vs-row'>"
                    f"<div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{item.get('home_form','')}</div>{item.get('h_rank_html','')}{item.get('h_money_html','')}{item.get('h_inj_html','')}{item.get('h_rest_html','')}{item.get('h_rot_html','')}</div>{logo_h_tag}</div>"
                    f"<div class='center-time-box'>{time_display}</div>"
                    f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{item.get('away_form','')}</div>{item.get('a_rank_html','')}{item.get('a_money_html','')}{item.get('a_inj_html','')}{item.get('a_rest_html','')}{item.get('a_rot_html','')}</div></div>"
                    f"</div>"
                    f"<div class='ai-story'>{item.get('story','')}</div>"
                    f"<div class='odd-bar'>"
                    f"<span class='odd-item'>승 <span class='odd-val'>{m.get('odd_h','-')}</span> | 무 <span class='odd-val'>{m.get('odd_d','-')}</span> | 패 <span class='odd-val'>{m.get('odd_a','-')}</span></span>"
                    f"<span class='odd-item'>핸디캡 <span class='odd-val'>{m.get('handi_h', '-')} / {m.get('handi_d', '-')} / {m.get('handi_a', '-')}</span></span>"
                    f"<span class='odd-item'>언오버 <span class='odd-val'>{m.get('uo_under', '-')} / {m.get('uo_over', '-')}</span></span>"
                    f"</div>"
                    f"<div class='pred-grid'>{dynamic_pred_boxes}</div>"
                    f"</div>"
                )
                st.markdown(html_code, unsafe_allow_html=True)
            
            if displayed_count == 0:
                st.info("조건에 맞는 경기가 없거나 모두 종료되었습니다.")
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
        
        summary_html = f"<div style='background: #111827; border: 1px solid #1E293B; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 24px; box-shadow: 0 4px 15px rgba(0,0,0,0.5);'><span style='color: #94A3B8; font-size: 14px; font-weight: 700; display: block; margin-bottom: 5px;'>AI 승무패 14경기 풀-스탯 분석 결과 (결장/순위/피로도 완벽 반영)</span><span style='color: #F8FAFC; font-size: 16px; font-weight: 700; display: block; margin-bottom: 8px;'>단통 <span style='color:#10B981;'>{single_pick_count}</span>경기 + 투마킹 <span style='color:#EF4444;'>{double_pick_count}</span>경기</span><span style='color: #F8FAFC; font-size: 24px; font-weight: 900; display: block;'>최종 <span style='color: #00F2FE;'>{total_combinations}</span> 조합 / 예상 구매 금액: <span style='color: #10B981;'>{total_price:,}</span> 원</span></div>"
        st.markdown(summary_html, unsafe_allow_html=True)

        for idx, item in enumerate(toto14_list, 1):
            m = item['match']
            logo_h_tag = render_logo_html(item.get("home_logo"))
            logo_a_tag = render_logo_html(item.get("away_logo"))
            
            match_id_str = f"TOTO14_{m['id']}"
            live_score_html = "<b style='color:#475569; font-size:16px;'>VS</b>"
            if match_id_str in live_scores_data:
                live_info = live_scores_data[match_id_str]
                score_text = live_info.get("score", "0:0")
                if not score_text or score_text == "-": score_text = "0:0"
                if score_text: live_score_html = f"<div style='color:#00F2FE; font-weight:900; font-size:18px;'>{score_text}</div><div style='color:#EF4444; font-size:10px; font-weight:900;'>LIVE</div>"

            html_code = (
                f"<div class='match-card' style='padding: 24px;'>"
                f"<div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;'><span class='badge-primary'>제 {idx} 경기</span><span style='color:#94A3B8; font-size:14px; font-weight:700;'>AI 추천 마킹: <b style='color:#00F2FE;'>{item.get('best_pick_display', '')}</b></span></div>"
                f"<div class='vs-row' style='margin-bottom:15px;'><div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{item.get('home_form','')}</div>{item.get('h_rank_html','')}{item.get('h_money_html','')}{item.get('h_inj_html','')}{item.get('h_rest_html','')}{item.get('h_rot_html','')}</div>{logo_h_tag}</div>"
                f"<div class='center-time-box' style='width:80px;'>{live_score_html}</div>"
                f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{item.get('away_form','')}</div>{item.get('a_rank_html','')}{item.get('a_money_html','')}{item.get('a_inj_html','')}{item.get('a_rest_html','')}{item.get('a_rot_html','')}</div></div></div>"
                f"<div style='font-size:12px; color:#64748B; font-weight:700; text-align:center;'>확률 분포: 승 {item.get('p_h')}% | 무 {item.get('p_d')}% | 패 {item.get('p_a')}%</div>"
                f"<div class='prob-bar-container' style='margin-bottom: 15px;'><div class='prob-bar-win' style='width: {item.get('p_h')}%;'></div><div class='prob-bar-draw' style='width: {item.get('p_d')}%;'></div><div class='prob-bar-lose' style='width: {item.get('p_a')}%;'></div></div>"
                f"<div style='display: flex; gap: 10px;'>{item.get('picks_html', '')}</div>"
                f"</div>"
            )
            st.markdown(html_code, unsafe_allow_html=True)
    else: st.info("현재 진행 중인 승무패 14경기 데이터가 없습니다. (로봇이 데이터를 수집 중입니다)")

# -----------------------------------------------------------------------------
# [TAB 3] 오늘의 TOP 3
# -----------------------------------------------------------------------------
with main_tab3:
    top3_list = dashboard_data.get("top3", [])
    
    if top3_list:
        for idx, item in enumerate(top3_list, 1):
            m = item['match']
            logo_h_tag = render_logo_html(item.get("home_logo"))
            logo_a_tag = render_logo_html(item.get("away_logo"))
            
            dynamic_top3_boxes = generate_pred_boxes(item.get('ev_sorted_picks', []), is_top3_tab=True)
            
            html_code = (
                f"<div class='match-card top3-glow'>"
                f"<div class='league-title' style='color:#00F2FE;'># {idx} 최고 가치 추천 픽 • {m.get('league','')}</div>"
                f"<div class='vs-row'><div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{item.get('home_form','')}</div>{item.get('h_rank_html','')}{item.get('h_money_html','')}{item.get('h_inj_html','')}{item.get('h_rest_html','')}{item.get('h_rot_html','')}</div>{logo_h_tag}</div>"
                f"<div class='center-time-box'><span class='match-time-text' style='color:#00F2FE;'>{item.get('final_match_time', '')}</span></div>"
                f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{item.get('away_form','')}</div>{item.get('a_rank_html','')}{item.get('a_money_html','')}{item.get('a_inj_html','')}{item.get('a_rest_html','')}{item.get('a_rot_html','')}</div></div></div>"
                f"<div class='pred-grid' style='margin-top:20px;'>{dynamic_top3_boxes}</div>"
                f"</div>"
            )
            st.markdown(html_code, unsafe_allow_html=True)
    else: 
        st.info("현재 배팅 가능한 분석 경기가 없어 추천 픽을 산출할 수 없습니다. (로봇이 데이터를 수집 중입니다)")

# -----------------------------------------------------------------------------
# [TAB 4] AI 리포트 (오답노트 포함)
# -----------------------------------------------------------------------------
with main_tab4:
    def get_accuracy_stats_lite():
        try:
            conn = sqlite3.connect("ai_predictions.db")
            df = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED' OR actual_result = 'CANCELED'", conn)
            df_history = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result IN ('FINISHED', 'CANCELED') ORDER BY id DESC LIMIT 50", conn)
            df_pending = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'PENDING'", conn)
            conn.close()
            
            scoring_list = []
            now = datetime.now(timezone(timedelta(hours=9)))
            for _, row in df_pending.iterrows():
                m_time = row['match_time']
                is_finished_time = False
                try:
                    match = re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})', m_time)
                    if match:
                        mo, d, h, m = map(int, match.groups())
                        m_dt = datetime(now.year, mo, d, h, m, tzinfo=timezone(timedelta(hours=9)))
                        if now > m_dt + timedelta(hours=2): 
                            is_finished_time = True
                except: pass
                if is_finished_time:
                    scoring_list.append(row.to_dict())
            
            df_valid = df[df['actual_result'] == 'FINISHED']
            history_list = df_history.to_dict('records')
            
            if len(df_valid) == 0: 
                return {"total": 0, "correct": 0, "accuracy": 0.0, "history": history_list, "scoring": scoring_list}
            
            correct_cnt = df_valid['is_correct'].sum()
            total_cnt = len(df_valid)
            return {"total": total_cnt, "correct": int(correct_cnt), "accuracy": round((correct_cnt / total_cnt) * 100, 1), "history": history_list, "scoring": scoring_list}
        except:
            return {"total": 0, "correct": 0, "accuracy": 0.0, "history": [], "scoring": []}

    stats = get_accuracy_stats_lite()
    
    st.markdown(f"<div style='display:flex; align-items:center; gap:20px; margin-bottom:30px; background:#0B0F19; padding:20px; border-radius:12px; border:1px solid #1E293B;'><div><span style='color:#94A3B8; font-size:14px; font-weight:700; display:block;'>전체 누적 적중률</span><span style='color:#00F2FE; font-size:40px; font-weight:900;'>{stats['accuracy']}%</span></div><div style='border-left:1px solid #334155; padding-left:20px;'><span style='color:#CBD5E1; font-size:14px; display:block;'>종료된 경기: {stats['total']} 경기</span><span style='color:#10B981; font-size:14px; display:block; margin-top:5px;'>적중: {stats['correct']} 경기</span><span style='color:#EF4444; font-size:14px; display:block; margin-top:5px;'>실패: {stats['total'] - stats['correct']} 경기</span></div></div><h4 style='color:#F8FAFC; font-weight:900; margin-bottom:10px;'>📜 최근 경기 학습(오답) 노트</h4>", unsafe_allow_html=True)
    
    scoring_data = stats.get('scoring', [])
    history_data = stats.get('history', [])
    
    if scoring_data or history_data:
        table_html = "<table style='width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; text-align: center;'><thead><tr><th style='background:#1E293B; color:#94A3B8; padding:10px;'>경기</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>예측</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>결과</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>상태</th></tr></thead><tbody>"
        
        for row in scoring_data:
            m_id_str = str(row['match_id'])
            temp_score = row.get('actual_score', '-:-')
            if temp_score == '-:-' and m_id_str in live_scores_data and live_scores_data[m_id_str].get("score"):
                temp_score = live_scores_data[m_id_str].get("score").replace(" : ", ":")
                
            result_mark = "<span style='color:#F59E0B; font-weight:900;'>채점중</span>"
            table_html += f"<tr><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; font-weight:700;'>{row.get('home_team','')} vs {row.get('away_team','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #00F2FE;'>{row.get('predicted_pick','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; font-weight:900;'>{temp_score}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B;'>{result_mark}</td></tr>"

        for row in history_data:
            if row.get('actual_result') == 'CANCELED':
                result_mark = "<span style='color:#94A3B8; font-weight:700; font-size:11px;'>취소</span><br><span style='color:#94A3B8; font-weight:900;'>무효</span>"
            elif row.get('is_correct',0) == 1:
                result_mark = "<span style='color:#94A3B8; font-weight:700; font-size:11px;'>종료</span><br><span style='color:#10B981; font-weight:900;'>적중</span>"
            else:
                result_mark = "<span style='color:#94A3B8; font-weight:700; font-size:11px;'>종료</span><br><span style='color:#EF4444; font-weight:900;'>실패</span>"
            
            table_html += f"<tr><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; font-weight:700;'>{row.get('home_team','')} vs {row.get('away_team','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #00F2FE;'>{row.get('predicted_pick','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; font-weight:900;'>{row.get('actual_score','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B;'>{result_mark}</td></tr>"
            
        table_html += "</tbody></table>"
        st.markdown(table_html, unsafe_allow_html=True)
        
        st.markdown("<h4 style='color:#F8FAFC; font-weight:900; margin-top:30px; margin-bottom:10px;'>💡 AI 학습(분석) 노트 내용</h4>", unsafe_allow_html=True)
        has_failure = False
        for row in history_data:
            if row.get('failure_reason'):
                has_failure = True
                st.markdown(f"<div style='background:#1E293B; padding:12px; border-radius:6px; margin-bottom:8px; font-size:13px; color:#CBD5E1;'><b>[{row.get('home_team','')} vs {row.get('away_team','')}]</b><br>{row.get('failure_reason','')}</div>", unsafe_allow_html=True)
        if not has_failure:
            st.info("아직 분석된 정답/오답 노트가 없습니다.")
    else:
        st.info("아직 채점이 완료된 종료 경기가 없습니다.")
