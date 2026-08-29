import os
import streamlit as st
import json
import requests
import sqlite3
import pandas as pd
import time
from datetime import datetime, timezone, timedelta
import re
import base64
from pathlib import Path
from html import escape

from member_system import (
    ROLE_ADMIN,
    ROLE_GUEST,
    ROLE_LABELS,
    ROLE_MEMBER,
    ROLE_SUPPORTER,
    authenticate_user,
    can_write_board,
    create_post,
    delete_post,
    init_member_db,
    list_posts,
    list_support_requests,
    list_users,
    register_user,
    request_supporter,
    set_user_role,
    set_user_status,
)

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

# -----------------------------------------------------------------------------
# 0. 기본 설정 및 타이틀
# -----------------------------------------------------------------------------
APP_TITLE = "D.J SPORTS ANALYTICS"
APP_DIR = Path(__file__).resolve().parent
BRAND_LOGO_PATH = APP_DIR / "assets" / "dj-analytics-logo.svg"

st.set_page_config(
    page_title=APP_TITLE, 
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="auto" 
)

GITHUB_REPO = "chleowhd77-ops/-"
DEFAULT_TEAM_LOGO = "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/Soccerball.svg/120px-Soccerball.svg.png"

NO_CACHE_HEADERS = {
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0'
}

# -----------------------------------------------------------------------------
# 1. 초경량 데이터 로더
# -----------------------------------------------------------------------------
def load_dashboard_data():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/dashboard_data.json?t={int(time.time())}"
    try:
        res = requests.get(url, headers=NO_CACHE_HEADERS, timeout=5)
        if res.status_code == 200: return res.json()
    except: pass
    return {"proto": [], "toto14": [], "top3": []}

def load_live_scores():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/live_scores.json?t={int(time.time())}"
    try:
        res = requests.get(url, headers=NO_CACHE_HEADERS, timeout=5)
        if res.status_code == 200: return res.json()
    except: pass
    return {}

def download_db():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/ai_predictions.db?t={int(time.time())}"
    try:
        res = requests.get(url, headers=NO_CACHE_HEADERS, timeout=5)
        if res.status_code == 200:
            with open("ai_predictions.db", "wb") as f:
                f.write(res.content)
    except: pass

download_db()

# -----------------------------------------------------------------------------
# 2. 회원·권한·게시판 DB 엔진
# -----------------------------------------------------------------------------
init_member_db()


def image_data_uri(path: Path) -> str:
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    mime_type = "image/svg+xml" if path.suffix.lower() == ".svg" else "image/png"
    return f"data:{mime_type};base64,{encoded}"


BRAND_LOGO_URI = image_data_uri(BRAND_LOGO_PATH)

# -----------------------------------------------------------------------------
# 3. 디자인 (CSS)
# -----------------------------------------------------------------------------
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700;900&display=swap');
    html, body, .stApp { background-color: #06080F !important; font-family: 'Noto Sans KR', sans-serif !important; color: #E2E8F0; overflow-x: hidden !important; }
    
    .block-container { 
        max-width: 1000px !important; 
        padding-top: 2rem !important; 
        padding-bottom: 2rem !important; 
        margin: 0 auto !important; 
    }
    
    .app-header { text-align: center; padding: 30px 0 20px 0; border-bottom: 1px solid #1E293B; margin-bottom: 30px; }
    .app-header h1 { color: #FFFFFF !important; font-size: 36px !important; font-weight: 900 !important; letter-spacing: 2px; margin: 0; background: -webkit-linear-gradient(45deg, #00F2FE, #4FACFE); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .app-header p { color: #64748B; font-size: 14px; font-weight: 700; letter-spacing: 1px; margin-top: 5px; }
    .stTabs [data-baseweb="tab-list"] { background-color: transparent !important; border-bottom: 1px solid #1E293B !important; gap: 30px !important; justify-content: center !important; }
    .stTabs [data-baseweb="tab"] { color: #64748B !important; font-weight: 900 !important; font-size: 18px !important; padding: 14px 0px !important; border: none !important; }
    .stTabs [aria-selected="true"] {
        color: #FF4D5D !important;
        background: transparent !important;
        border: 0 !important;
        box-shadow: none !important;
    }
    .match-card { background-color: #0B0F19; border: 1px solid #1E293B; border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); }
    .top3-glow { border: 2px solid #00F2FE !important; box-shadow: 0 0 20px rgba(0, 242, 254, 0.15) !important; background: linear-gradient(135deg, #0A192F 0%, #06080F 100%) !important; }
    .league-title { font-size: 13px; color: #94A3B8; font-weight: 900; letter-spacing: 1px; margin-bottom: 15px; }
    
    .vs-row { display: flex; justify-content: space-between; align-items: center; width: 100%; margin-bottom: 15px; }
    .team-box { flex: 1; display: flex; align-items: center; gap: 15px; }
    .team-box.home { justify-content: flex-end; text-align: right; }
    .team-box.away { justify-content: flex-start; text-align: left; }
    .team-info-wrapper { display: flex; flex-direction: column; justify-content: center; }
    .team-box.home .team-info-wrapper { align-items: flex-end; }
    .team-box.away .team-info-wrapper { align-items: flex-start; }
    .team-name-text { display: block; color: #F8FAFC !important; font-size: 20px; font-weight: 900; letter-spacing: -0.5px; }
    .team-form-text { display: block; color: #64748B; font-size: 12px; font-weight: 700; letter-spacing: 1px; margin-top: 4px; }
    
    .injury-badge { display: block; color: #F87171; font-size: 11px; font-weight: 900; margin-top: 3px; background: rgba(248,113,113,0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid #F87171; }
    .fatigue-badge { display: block; color: #F59E0B; font-size: 11px; font-weight: 900; margin-top: 3px; background: rgba(245,158,11,0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid #F59E0B; }
    .rank-badge { display: block; color: #38BDF8; font-size: 11px; font-weight: 900; margin-top: 3px; background: rgba(56,189,248,0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid #38BDF8; }

    .team-logo { width: 50px !important; height: 50px !important; object-fit: contain; }
    
    .center-time-box { width: 120px; text-align: center; flex-shrink: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
    .match-time-text { color: #CBD5E1; font-size: 14px; font-weight: 700; display: block; margin-bottom: 4px;}
    .live-score { font-size: 28px; font-weight: 900; color: #00F2FE; display: block; margin-bottom: 4px; text-shadow: 0 0 10px rgba(0,242,254,0.6); }
    .deadline-open { color: #00F2FE; font-size: 11px; font-weight: 900; border: 1px solid #00F2FE; padding: 3px 8px; border-radius: 4px; display: inline-block;}
    .deadline-closed { color: #EF4444; font-size: 11px; font-weight: 900; background: rgba(239, 68, 68, 0.1); border: 1px solid #EF4444; padding: 3px 8px; border-radius: 4px; display: inline-block;}
    
    .odd-bar { display: flex; justify-content: space-between; background: #111827; border-radius: 6px; padding: 12px 20px; margin-bottom: 15px; border: 1px solid #1F2937; }
    .odd-item { font-size: 13px; color: #94A3B8; font-weight: 700; }
    .odd-val { color: #F1F5F9; font-weight: 900; margin-left: 6px; }
    
    .ai-story { background: rgba(0, 242, 254, 0.05); border-left: 3px solid #00F2FE; padding: 12px 15px; font-size: 13px; color: #E2E8F0; font-weight: 700; border-radius: 4px; margin-bottom: 15px; line-height: 1.6; }
    
    .pred-grid { display: flex; gap: 12px; }
    .pred-box { flex: 1; background: #0D1424; border: 1px solid #1E293B; border-radius: 8px; padding: 16px; text-align: center; display: flex; flex-direction: column; justify-content: center; align-items: center; }
    .pred-label { font-size: 12px; color: #64748B; font-weight: 900; margin-bottom: 10px; }
    .pred-value { display: block; font-size: 15px; color: #F8FAFC; font-weight: 900; line-height: 1.4; margin-bottom: 12px; word-break: keep-all; text-align: center; }
    .pred-prob { display: inline-block; font-size: 13px; color: #0B0F19; font-weight: 900; background-color: #10B981; padding: 4px 14px; border-radius: 20px; }
    
    .prob-bar-container { display: flex; height: 12px; border-radius: 6px; overflow: hidden; margin-top: 10px; background: #1E293B;}
    .prob-bar-win { background-color: #00F2FE; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color:#000; }
    .prob-bar-draw { background-color: #10B981; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color:#000; }
    .prob-bar-lose { background-color: #EF4444; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color:#000; }
    .badge-primary { background: rgba(0, 242, 254, 0.1); color: #00F2FE; border: 1px solid #00F2FE; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 900; }

    .report-card { background: #0F172A; border: 1px solid #334155; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
    .report-score { font-size: 26px !important; font-weight: 900 !important; color: #F8FAFC !important; letter-spacing: 2px; }
    .report-team { font-size: 16px; font-weight: 900; color: #CBD5E1; }
    .real-ai-note { background: rgba(16, 185, 129, 0.05); border-left: 4px solid #10B981; padding: 15px; font-size: 13px; color: #E2E8F0; margin-top: 15px; border-radius: 4px; line-height: 1.6; font-weight: 700; }
    .real-ai-note-fail { background: rgba(239, 68, 68, 0.05); border-left: 4px solid #EF4444; padding: 15px; font-size: 13px; color: #E2E8F0; margin-top: 15px; border-radius: 4px; line-height: 1.6; font-weight: 700; }

    /* ------------------------------------------------------------------
       UI/UX REDESIGN · 분석과 권한 로직은 그대로 두고 표현만 개선
       ------------------------------------------------------------------ */
    :root {
        --dj-bg: #060912;
        --dj-surface: #0D1320;
        --dj-surface-2: #111A2B;
        --dj-line: rgba(148, 163, 184, 0.16);
        --dj-text: #F8FAFC;
        --dj-muted: #8B9AB1;
        --dj-cyan: #19E6F2;
        --dj-blue: #4F8CFF;
        --dj-green: #17C98B;
        --dj-red: #FF5B68;
        --dj-gold: #F8C65C;
    }

    html, body, .stApp {
        background:
            radial-gradient(circle at 50% -15%, rgba(30, 98, 180, 0.20), transparent 34rem),
            radial-gradient(circle at 92% 12%, rgba(25, 230, 242, 0.08), transparent 24rem),
            var(--dj-bg) !important;
        color: var(--dj-text) !important;
    }

    [data-testid="stHeader"] { background: rgba(6, 9, 18, 0.72) !important; }
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0B1120 0%, #070B13 100%) !important;
        border-right: 1px solid var(--dj-line) !important;
    }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stSidebar"] label { color: #CFD8E6 !important; }

    .block-container {
        max-width: 1120px !important;
        padding: 1.2rem 1.5rem 4rem !important;
    }

    .brand-shell {
        position: relative;
        overflow: hidden;
        padding: 34px 36px 28px;
        margin: 8px 0 18px;
        border: 1px solid rgba(79, 140, 255, 0.24);
        border-radius: 24px;
        background: linear-gradient(135deg, rgba(15, 28, 50, 0.96), rgba(7, 13, 25, 0.94));
        box-shadow: 0 24px 60px rgba(0, 0, 0, 0.30);
    }
    .brand-shell::after {
        content: "";
        position: absolute;
        width: 230px;
        height: 230px;
        right: -70px;
        top: -115px;
        border-radius: 50%;
        background: rgba(25, 230, 242, 0.10);
        filter: blur(4px);
    }
    .brand-row {
        position: relative;
        z-index: 1;
        display: flex;
        align-items: center;
        gap: 19px;
    }
    .brand-mark {
        width: 68px;
        height: 68px;
        object-fit: contain;
        flex: 0 0 68px;
        filter: drop-shadow(0 10px 24px rgba(25, 230, 242, .20));
    }
    .brand-kicker {
        color: var(--dj-cyan);
        font-size: 11px;
        font-weight: 900;
        letter-spacing: 2.4px;
        margin-bottom: 12px;
    }
    .brand-title {
        color: #FFFFFF;
        font-size: 34px;
        font-weight: 900;
        letter-spacing: -1px;
        line-height: 1.15;
        margin-bottom: 9px;
    }
    .brand-title span { color: var(--dj-cyan); }
    .brand-copy {
        color: var(--dj-muted);
        font-size: 14px;
        font-weight: 700;
        line-height: 1.6;
    }
    .brand-trust {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 20px;
    }
    .brand-trust span {
        color: #CAD5E4;
        font-size: 11px;
        font-weight: 800;
        padding: 7px 10px;
        border: 1px solid var(--dj-line);
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.035);
    }

    .status-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 10px;
        margin: 0 0 22px;
    }
    .status-cell {
        border: 1px solid var(--dj-line);
        background: rgba(13, 19, 32, 0.82);
        border-radius: 15px;
        padding: 14px 16px;
    }
    .status-cell small {
        display: block;
        color: var(--dj-muted);
        font-size: 10px;
        font-weight: 800;
        margin-bottom: 4px;
    }
    .status-cell strong { color: #FFFFFF; font-size: 17px; font-weight: 900; }
    .status-cell strong.accent { color: var(--dj-cyan); }

    .section-intro {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 16px;
        padding: 12px 2px 18px;
    }
    .section-intro h2 {
        color: #FFFFFF;
        font-size: 22px;
        font-weight: 900;
        letter-spacing: -0.6px;
        margin: 0 0 5px;
    }
    .section-intro p { color: var(--dj-muted); font-size: 12px; font-weight: 700; margin: 0; }

    /* 메뉴는 버튼처럼 채우지 않고 선택된 글자만 빨간색으로 표시한다. */
    .stTabs [data-baseweb="tab-list"] {
        justify-content: flex-start !important;
        gap: 24px !important;
        padding: 0 !important;
        margin-bottom: 18px !important;
        background: transparent !important;
        background-color: transparent !important;
        border: 0 !important;
        border-bottom: 1px solid var(--dj-line) !important;
        border-radius: 0 !important;
        box-shadow: none !important;
    }
    .stTabs [data-baseweb="tab"],
    .stTabs button[role="tab"] {
        min-height: 42px !important;
        padding: 9px 0 12px !important;
        color: #A8B3C4 !important;
        background: transparent !important;
        background-color: transparent !important;
        background-image: none !important;
        border: 0 !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        font-size: 13px !important;
    }
    .stTabs [data-baseweb="tab"]:hover,
    .stTabs button[role="tab"]:hover {
        color: #FFFFFF !important;
        background: transparent !important;
        background-color: transparent !important;
    }
    .stTabs [data-baseweb="tab"][aria-selected="true"],
    .stTabs button[role="tab"][aria-selected="true"] {
        color: #FF4D5D !important;
        background: transparent !important;
        background-color: transparent !important;
        background-image: none !important;
        border: 0 !important;
        box-shadow: none !important;
        outline: 0 !important;
    }
    .stTabs button[role="tab"]::before,
    .stTabs button[role="tab"]::after,
    .stTabs [data-baseweb="tab"][aria-selected="true"]::before,
    .stTabs [data-baseweb="tab"][aria-selected="true"]::after {
        content: none !important;
        display: none !important;
        background: transparent !important;
        border: 0 !important;
    }
    .stTabs [data-baseweb="tab"] > div,
    .stTabs button[role="tab"] > div,
    .stTabs [data-baseweb="tab"] p {
        color: inherit !important;
        background: transparent !important;
        background-color: transparent !important;
        border: 0 !important;
        box-shadow: none !important;
    }
    .stTabs [data-baseweb="tab"][aria-selected="true"] > div,
    .stTabs button[role="tab"][aria-selected="true"] > div,
    .stTabs button[role="tab"][aria-selected="true"] p {
        color: #FF4D5D !important;
        background: transparent !important;
        background-color: transparent !important;
    }
    .stTabs [data-baseweb="tab-highlight"],
    [data-testid="stTabs"] [data-baseweb="tab-highlight"] {
        display: none !important;
        width: 0 !important;
        height: 0 !important;
        background: transparent !important;
        border: 0 !important;
    }

    .match-card {
        position: relative;
        overflow: hidden;
        background: linear-gradient(145deg, rgba(15, 23, 38, 0.98), rgba(9, 14, 25, 0.98)) !important;
        border: 1px solid var(--dj-line) !important;
        border-radius: 20px !important;
        padding: 25px !important;
        margin-bottom: 16px !important;
        box-shadow: 0 16px 36px rgba(0, 0, 0, 0.20) !important;
    }
    .match-card::before {
        content: "";
        position: absolute;
        left: 0;
        top: 20px;
        bottom: 20px;
        width: 3px;
        border-radius: 0 4px 4px 0;
        background: linear-gradient(var(--dj-cyan), var(--dj-blue));
        opacity: 0.72;
    }
    .top3-glow {
        border-color: rgba(25, 230, 242, 0.42) !important;
        background: linear-gradient(135deg, rgba(10, 33, 50, 0.98), rgba(8, 14, 26, 0.98)) !important;
        box-shadow: 0 18px 44px rgba(2, 180, 196, 0.10) !important;
    }
    .league-title {
        display: inline-flex;
        align-items: center;
        min-height: 25px;
        color: #A9B7CB !important;
        font-size: 11px !important;
        letter-spacing: .3px !important;
        padding: 5px 9px;
        margin-bottom: 17px !important;
        border: 1px solid var(--dj-line);
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.03);
    }
    .team-name-text { font-size: 19px !important; line-height: 1.25; }
    .team-form-text { color: #7F8DA3 !important; letter-spacing: .2px !important; }
    .team-logo {
        width: 58px !important;
        height: 58px !important;
        padding: 6px;
        border-radius: 16px;
        background: rgba(255, 255, 255, 0.94);
        box-shadow: 0 8px 20px rgba(0, 0, 0, .25);
    }
    .center-time-box {
        min-height: 72px;
        padding: 8px 6px;
        border-radius: 14px;
        background: rgba(6, 9, 18, 0.58);
        border: 1px solid rgba(148, 163, 184, 0.10);
    }
    .match-time-text { color: #D7E1EF !important; font-size: 12px !important; }
    .deadline-open { border-radius: 999px !important; padding: 4px 9px !important; }
    .deadline-closed { border-radius: 999px !important; padding: 4px 9px !important; }

    .ai-story {
        color: #CFD8E6 !important;
        font-size: 12px !important;
        font-weight: 650 !important;
        line-height: 1.75 !important;
        padding: 15px 17px !important;
        border: 1px solid rgba(25, 230, 242, 0.12);
        border-left: 3px solid var(--dj-cyan) !important;
        border-radius: 10px !important;
        background: rgba(25, 230, 242, 0.045) !important;
    }
    .odd-bar {
        padding: 11px 15px !important;
        border-radius: 11px !important;
        background: rgba(6, 9, 18, 0.62) !important;
        border-color: var(--dj-line) !important;
    }
    .pred-grid { display: grid !important; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px !important; }
    .pred-box {
        min-height: 122px;
        padding: 15px 12px !important;
        border-radius: 13px !important;
        background: rgba(17, 26, 43, 0.84) !important;
        border-color: var(--dj-line) !important;
    }
    .pred-prob { background: var(--dj-green) !important; padding: 5px 13px !important; }
    .report-card {
        background: linear-gradient(145deg, rgba(15, 23, 38, .98), rgba(9, 14, 25, .98)) !important;
        border-color: var(--dj-line) !important;
        border-radius: 18px !important;
        box-shadow: 0 14px 32px rgba(0, 0, 0, .18);
    }

    .stButton > button {
        width: 100%;
        min-height: 42px;
        border-radius: 11px !important;
        border: 1px solid rgba(25, 230, 242, .35) !important;
        color: #061016 !important;
        background: linear-gradient(135deg, var(--dj-cyan), #67F3C6) !important;
        font-weight: 900 !important;
    }
    .stTextInput input, [data-baseweb="select"] > div {
        border-radius: 10px !important;
        background: rgba(8, 13, 23, .92) !important;
        border-color: var(--dj-line) !important;
    }

    @media (max-width: 768px) {
        .block-container { padding: .65rem .72rem 3rem !important; }
        .brand-shell { padding: 25px 20px 22px; border-radius: 18px; margin-top: 3px; }
        .brand-row { align-items: flex-start; gap: 12px; }
        .brand-mark { width: 48px; height: 48px; flex-basis: 48px; }
        .brand-title { font-size: 26px; }
        .brand-copy { font-size: 12px; padding-right: 18px; }
        .brand-trust { gap: 6px; margin-top: 15px; }
        .brand-trust span { font-size: 10px; padding: 6px 8px; }
        .status-grid { gap: 6px; margin-bottom: 14px; }
        .status-cell { padding: 11px 9px; border-radius: 12px; }
        .status-cell strong { font-size: 14px; }
        .stTabs [data-baseweb="tab-list"] { gap: 18px !important; overflow-x: auto !important; flex-wrap: nowrap !important; justify-content: flex-start !important; }
        .stTabs [data-baseweb="tab"] { flex: 0 0 auto !important; font-size: 11px !important; padding: 8px 0 10px !important; }
        .match-card { padding: 18px 14px !important; border-radius: 16px !important; }
        .vs-row { align-items: flex-start !important; gap: 5px; }
        .team-box { width: 40%; flex: none !important; flex-direction: column !important; justify-content: flex-start !important; text-align: center !important; gap: 8px !important; }
        .team-box.home { flex-direction: column-reverse !important; }
        .team-box.away { flex-direction: column !important; }
        .team-box.home .team-info-wrapper, .team-box.away .team-info-wrapper { align-items: center !important; text-align: center !important; width: 100% !important; }
        .team-logo { width: 48px !important; height: 48px !important; margin: 0 auto; border-radius: 13px; }
        .team-name-text { font-size: 14px !important; word-break: keep-all !important; white-space: normal !important; line-height: 1.3; margin-top: 5px; }
        .center-time-box { width: 20%; margin-top: 5px; }
        .live-score { font-size: 20px !important; }
        .match-time-text { font-size: 11px !important; }
        .odd-bar { flex-direction: column; align-items: center; gap: 8px; text-align: center; }
        .pred-grid { grid-template-columns: 1fr !important; }
        .pred-box { min-height: 104px; }
        .section-intro { align-items: flex-start; flex-direction: column; gap: 5px; }
        .report-card > div:first-child { flex-direction: column; text-align: center; gap: 15px; }
        .report-card > div:first-child > div { text-align: center !important; }
        .report-card > div:nth-child(2) { flex-direction: column; }
    }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 4. 사이드바 (로그인 / 회원가입 / 멤버십)
# -----------------------------------------------------------------------------
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'user_id' not in st.session_state:
    st.session_state['user_id'] = None
if 'username' not in st.session_state:
    st.session_state['username'] = ""
if 'role' not in st.session_state:
    st.session_state['role'] = ROLE_GUEST

# 서버의 live_scores.json은 5분마다 갱신된다. 브라우저도 1분마다 조용히
# 다시 읽어야 사용자가 수동 새로고침을 하지 않아도 점수가 움직인다.
if st_autorefresh is not None:
    st_autorefresh(interval=60 * 1000, key="live-score-refresh")

st.sidebar.title("D.J 회원 라운지")
st.sidebar.caption("로그인하면 내 등급과 이용 가능한 분석을 확인할 수 있습니다.")

if not st.session_state['logged_in']:
    menu = ["로그인", "회원가입"]
    choice = st.sidebar.selectbox("메뉴 선택", menu)

    if choice == "로그인":
        st.sidebar.subheader("접속하기")
        username = st.sidebar.text_input("아이디")
        password = st.sidebar.text_input("비밀번호", type='password')
        if st.sidebar.button("로그인", use_container_width=False):
            user = authenticate_user(username, password)
            if user:
                st.session_state['logged_in'] = True
                st.session_state['user_id'] = user['id']
                st.session_state['username'] = user['username']
                st.session_state['role'] = user['role']
                st.sidebar.success(f"환영합니다, {user['display_name']}님!")
                st.rerun()
            else:
                st.sidebar.warning("아이디 또는 비밀번호가 틀렸습니다.")

    elif choice == "회원가입":
        st.sidebar.subheader("새 계정 만들기")
        new_user = st.sidebar.text_input("사용할 아이디")
        new_display_name = st.sidebar.text_input("표시 이름 (선택)")
        new_password = st.sidebar.text_input("비밀번호", type='password')
        new_password_check = st.sidebar.text_input("비밀번호 확인", type='password')
        
        st.sidebar.markdown("---")
        st.sidebar.markdown("**[필수] 이용 안내**")
        st.sidebar.caption("분석 확률과 추천은 참고 자료이며 경기 적중이나 수익을 보장하지 않습니다. 가입 정보는 로그인과 회원 등급 관리에만 사용합니다.")
        agree = st.sidebar.checkbox("이용 안내와 개인정보 처리에 동의합니다.")
        
        if st.sidebar.button("가입하기", use_container_width=False):
            if not agree:
                st.sidebar.error("이용 안내에 동의해주세요.")
            elif new_user == "" or new_password == "":
                st.sidebar.error("아이디와 비밀번호를 입력해주세요.")
            elif new_password != new_password_check:
                st.sidebar.error("비밀번호 확인이 일치하지 않습니다.")
            else:
                ok, message = register_user(new_user, new_password, new_display_name)
                if ok:
                    st.sidebar.success(f"{message} 로그인 메뉴에서 접속해주세요.")
                else:
                    st.sidebar.error(message)

else:
    current_role = st.session_state.get('role', ROLE_MEMBER)
    st.sidebar.success(f"👤 {st.session_state['username']} 님 접속 중")
    st.sidebar.markdown(f"**등급: {ROLE_LABELS.get(current_role, '일반회원')}**")
    if current_role in {ROLE_SUPPORTER, ROLE_ADMIN}:
        st.sidebar.info("전체 경기 분석과 후원회원 게시판 작성 권한이 열려 있습니다.")
    else:
        st.sidebar.info("현재 무료 베타 운영 중입니다. 후원회원 전환은 확인 요청 후 관리자가 처리합니다.")
        with st.sidebar.expander("후원회원 전환 확인 요청"):
            depositor_name = st.text_input("확인용 입금자명", key="support-depositor")
            support_note = st.text_area("관리자에게 남길 메모", key="support-note", height=80)
            if st.button("확인 요청 보내기", key="support-request-submit"):
                ok, message = request_supporter(
                    int(st.session_state['user_id']), depositor_name, support_note
                )
                (st.success if ok else st.error)(message)

    if st.sidebar.button("로그아웃"):
        st.session_state['logged_in'] = False
        st.session_state['user_id'] = None
        st.session_state['username'] = ""
        st.session_state['role'] = ROLE_GUEST
        st.rerun()

    # 관리자 전용 회원·권한 관리
    if current_role == ROLE_ADMIN:
        st.sidebar.markdown("---")
        st.sidebar.error("관리자 전용 모드")
        users = list_users()
        if users:
            user_df = pd.DataFrame(users).rename(columns={
                "username": "아이디", "display_name": "표시 이름",
                "role": "등급", "status": "상태", "created_at": "가입 시각",
                "last_login_at": "최근 로그인",
            })
            visible_columns = ["아이디", "표시 이름", "등급", "상태", "가입 시각", "최근 로그인"]
            st.sidebar.dataframe(user_df[visible_columns], hide_index=True, use_container_width=True)

            target_username = st.sidebar.selectbox(
                "관리할 회원", [user["username"] for user in users], key="admin-target-user"
            )
            target_user = next(user for user in users if user["username"] == target_username)
            role_options = [ROLE_MEMBER, ROLE_SUPPORTER, ROLE_ADMIN]
            selected_role = st.sidebar.selectbox(
                "회원 등급",
                role_options,
                index=role_options.index(target_user["role"]),
                format_func=lambda value: ROLE_LABELS[value],
                key="admin-target-role",
            )
            if st.sidebar.button("등급 적용"):
                ok, message = set_user_role(
                    int(st.session_state['user_id']), target_username, selected_role
                )
                (st.sidebar.success if ok else st.sidebar.error)(message)
                if ok:
                    st.rerun()

            selected_status = st.sidebar.selectbox(
                "계정 상태",
                ["active", "suspended"],
                index=0 if target_user["status"] == "active" else 1,
                format_func=lambda value: "정상" if value == "active" else "정지",
                key="admin-target-status",
            )
            if st.sidebar.button("상태 적용"):
                ok, message = set_user_status(
                    int(st.session_state['user_id']), target_username, selected_status
                )
                (st.sidebar.success if ok else st.sidebar.error)(message)
                if ok:
                    st.rerun()

        pending_requests = [
            request for request in list_support_requests() if request["status"] == "pending"
        ]
        with st.sidebar.expander(f"후원 확인 대기 {len(pending_requests)}건"):
            if pending_requests:
                for request in pending_requests:
                    st.write(f"{request['username']} · {request['depositor_name']}")
                    if request.get("note"):
                        st.caption(request["note"])
            else:
                st.caption("대기 중인 요청이 없습니다.")

has_full_access = bool(
    st.session_state.get('role') in {ROLE_SUPPORTER, ROLE_ADMIN}
)

# -----------------------------------------------------------------------------
# 5. 레이아웃 뼈대 생성 (메인 콘텐츠)
# -----------------------------------------------------------------------------
dashboard_data = load_dashboard_data()
live_scores_data = load_live_scores()

proto_total = len(dashboard_data.get("proto", []))
top3_total = min(3, len(dashboard_data.get("top3", [])))
live_total = sum(1 for value in live_scores_data.values() if value.get("is_live") is True)
member_label = "후원회원 전체 이용" if has_full_access else "무료 3픽 이용"
brand_mark_html = (
    f"<img class='brand-mark' src='{BRAND_LOGO_URI}' alt='D.J SPORTS ANALYTICS 로고'>"
    if BRAND_LOGO_URI else "<div class='brand-mark' aria-label='DJ'>DJ</div>"
)

st.markdown(f"""
<section class='brand-shell'>
    <div class='brand-row'>
        {brand_mark_html}
        <div>
            <div class='brand-kicker'>MULTI-SPORT DATA INTELLIGENCE</div>
            <div class='brand-title'>D.J SPORTS <span>ANALYTICS</span></div>
            <div class='brand-copy'>축구를 시작으로 야구·농구까지 확장하는 스포츠 데이터 분석 플랫폼<br>확률은 참고 지표이며 적중이나 수익을 보장하지 않습니다.</div>
            <div class='brand-trust'>
                <span>PROTO 분석</span>
                <span>데이터 기반 확률</span>
                <span>경기 종료 후 자동 채점</span>
            </div>
        </div>
    </div>
</section>
<div class='status-grid'>
    <div class='status-cell'><small>오늘 분석</small><strong>{proto_total}경기</strong></div>
    <div class='status-cell'><small>현재 LIVE</small><strong class='accent'>{live_total}경기</strong></div>
    <div class='status-cell'><small>이용 상태</small><strong>{member_label}</strong></div>
</div>
""", unsafe_allow_html=True)

# 방문자가 가장 먼저 무료 추천을 보도록 탭의 표시 순서만 변경한다.
main_tab3, main_tab1, main_tab2, main_tab4, main_tab5 = st.tabs([
    f"오늘의 {top3_total or 3}픽", "전체 경기", "승무패 14", "채점 노트", "인증 게시판"
])

if st.session_state.get('role') == ROLE_ADMIN:
    source_meta = dashboard_data.get("source_meta", {})
    if source_meta:
        proto_source = source_meta.get("betman_proto_count", 0)
        proto_display = source_meta.get("display_proto_count", 0)
        toto_source = source_meta.get("betman_toto14_count", 0)
        toto_display = source_meta.get("display_toto14_count", 0)
        st.caption(f"관리자 수집 확인 · 프로토 {proto_display}/{proto_source} · 승무패 {toto_display}/{toto_source}")
        if not source_meta.get("proto_parity_ok", True) or not source_meta.get("toto14_parity_ok", True):
            st.error("베트맨 원본 경기 수와 화면 데이터 수가 다릅니다. 수집 로그를 확인해주세요.")
        api_usage = source_meta.get("api_usage", {})
        if api_usage.get("quota_exhausted"):
            st.warning("오늘 API 사용량이 소진되어 기존 정상 캐시로 분석 중입니다. 일일 한도 초기화 후 자동으로 최신 자료를 보강합니다.")

def get_match_status(match_time_str, deadline_str):
    if not match_time_str or match_time_str == "시간 미정": return "TBD", False
    try:
        now = datetime.now(timezone(timedelta(hours=9)))
        match = re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})', match_time_str)
        if match:
            mo, d, h, m = map(int, match.groups())
            # 연말/연초에도 가장 가까운 실제 날짜를 선택한다.
            candidates = [
                datetime(year, mo, d, h, m, tzinfo=timezone(timedelta(hours=9)))
                for year in (now.year - 1, now.year, now.year + 1)
            ]
            m_dt = min(candidates, key=lambda value: abs((value - now).total_seconds()))
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

# 🔥 라이브 경기 판별 함수 (정렬을 위해 추가됨!)
def check_is_live(item):
    m = item.get('match', {})
    match_id_str = str(m.get('id', ''))
    live_info = live_scores_data.get(match_id_str, {})
    if live_info.get("is_live") is True:
        return True
    time_status, _ = get_match_status(
        item.get("final_match_time", m.get("match_time", "")),
        m.get("deadline_time", "23:00"),
    )
    return time_status == "LIVE"

def render_logo_html(logo_url):
    safe_logo = escape(str(logo_url or DEFAULT_TEAM_LOGO), quote=True)
    safe_fallback = escape(DEFAULT_TEAM_LOGO, quote=True)
    return (
        f'<img src="{safe_logo}" class="team-logo" '
        f'onerror="this.onerror=null;this.src=\'{safe_fallback}\';">'
    )

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
            stars = "⭐⭐⭐" if prob_pct >= 65 else ("⭐⭐" if prob_pct >= 50 else "⭐")
            label = f"🥇 강력 추천 ({pick.get('label', '')}) {stars}" if is_top3_tab else f"🥇 {pick.get('label', '')} {stars}"
        else:
            bg_style = ""
            title_color = "color:#64748B;"
            label = f"서브 추천 ({pick.get('label', '')})" if is_top3_tab else pick.get('label', '')
            
        html += f"<div class='pred-box' style='{bg_style}'><div class='pred-label' style='{title_color}'>{label}</div><span class='pred-value'>{pick.get('html_pick', '')}</span><span class='pred-prob'>{prob_pct}%</span></div>"
    return html

# -----------------------------------------------------------------------------
# [TAB 1] 프로토 LIVE
# -----------------------------------------------------------------------------
with main_tab1:
    sub_soccer, sub_baseball, sub_basketball = st.tabs(["축구", "야구", "농구"])
    with sub_soccer:
        st.markdown("""
        <div class='section-intro'>
            <div><h2>전체 경기 분석</h2><p>LIVE 경기를 먼저 보여주며 리그별로 빠르게 확인할 수 있습니다.</p></div>
        </div>
        <div style='background:rgba(25,230,242,.055); border:1px solid rgba(25,230,242,.20); color:#BEEEF2; padding:12px 14px; border-radius:12px; font-size:12px; font-weight:700; margin-bottom:20px;'>
            무료 이용자는 오늘의 추천 3경기와 채점 노트를 볼 수 있습니다. 전체 경기 상세 분석은 후원회원에게 열립니다.
        </div>
        """, unsafe_allow_html=True)
        
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
            
            # 필터링
            if selected_league != "전체 리그 보기": 
                proto_list = [m for m in proto_list if m.get('league') == selected_league]
            
            # 🔥 핵심: 정렬 로직 완벽 적용 (LIVE를 최상단 0순위로 강제 고정!)
            proto_list = sorted(
                proto_list, 
                key=lambda x: (
                    not check_is_live(x), # LIVE인 애들은 0(위로), 아닌 애들은 1(밑으로)
                    x.get('timestamp', 9999999999) if sort_urgent else 0 # 그다음 시간순 정렬
                )
            )
                
            displayed_count = 0
            paywall_shown = False

            for item in proto_list:
                m = item['match']
                logo_h_tag = render_logo_html(item.get("home_logo"))
                logo_a_tag = render_logo_html(item.get("away_logo"))
                raw_deadline = m.get("deadline_time", "23:00")
                match_status, is_closed = get_match_status(item.get("final_match_time", ""), raw_deadline)
                
                match_id_str = str(m.get('id', ''))
                is_live_now = check_is_live(item)
                
                if match_status == "FINISHED" and not is_live_now and not has_full_access: continue
                
                displayed_count += 1

                # 🔥 페이월(Paywall) 로직: 4번째 경기부터 잠금
                if displayed_count > 3 and not has_full_access:
                    if not paywall_shown:
                        st.markdown("""
                        <div class='match-card' style='text-align: center; padding: 50px 20px; background: rgba(15, 23, 42, 0.6); backdrop-filter: blur(8px); border: 1px solid #F59E0B;'>
                            <h2 style='color: #F59E0B; font-weight: 900; letter-spacing: 1px;'>🔒 후원회원 전용 분석</h2>
                            <p style='color: #94A3B8; font-weight: 700; font-size: 16px; margin-top: 15px;'>4번째 경기부터는 후원회원에게만 제공됩니다.<br>숨겨진 역배 분석과 전체 데이터를 확인할 수 있습니다.</p>
                            <p style='color: #38BDF8; font-size: 14px; margin-top: 25px; background: rgba(56,189,248,0.1); display: inline-block; padding: 8px 15px; border-radius: 8px;'>좌측 회원 라운지에서 로그인 후 전환 확인을 요청해주세요.</p>
                        </div>
                        """, unsafe_allow_html=True)
                        paywall_shown = True
                    continue 

                if is_live_now:
                    if match_id_str in live_scores_data:
                        live_info = live_scores_data[match_id_str]
                        score_text = live_info.get("score", "0:0")
                        if not score_text or score_text == "-": score_text = "0:0"
                        event_text = live_info.get("event", "")
                        event_html = f"<div style='margin-bottom:6px; font-size:11px; color:#10B981; font-weight:900;'>{event_text}</div>" if event_text else ""
                        time_display = f"{event_html}<span class='live-score'>{score_text}</span><span class='deadline-closed' style='background:rgba(239, 68, 68, 0.1); border-color:#EF4444; color:#EF4444;'>🔴 LIVE</span>"
                    else:
                        time_display = f"<span class='match-time-text'>스코어 확인 중</span><span class='deadline-closed' style='background:rgba(239, 68, 68, 0.1); border-color:#EF4444; color:#EF4444;'>🔴 LIVE</span>"
                else:
                    badge = f"<span class='deadline-closed'>픽 마감</span>" if is_closed else f"<span class='deadline-open'>{raw_deadline}</span>"
                    time_display = f"<span class='match-time-text'>{item.get('final_match_time', '')}</span>{badge}"
                
                dynamic_pred_boxes = generate_pred_boxes(item.get('ev_sorted_picks', []), is_top3_tab=False)

                upset_html = ""
                if item.get('upset_warning'):
                    upset_html = f"<div style='background-color: #3b1c1c; border-left: 4px solid #ff4d4d; padding: 12px 15px; font-size: 13px; color:#ffcccc; border-radius:4px; margin-bottom:15px; line-height:1.6;'><span style='background-color:#FFD700; color:#000; font-weight:900; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>후원회원 전용</span>🚨 <b>슈퍼 역배 주의보 포착!</b><br>{item.get('upset_reason', '역배 전조 증상이 포착되었습니다. 고배당 스나이핑 찬스!')}</div>"
                
                html_code = (
                    f"<div class='match-card'>"
                    f"<div class='league-title'>{m.get('league','축구')}</div>"
                    f"<div class='vs-row'>"
                    f"<div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{item.get('home_form','')}</div>{item.get('h_rank_html','')}{item.get('h_inj_html','')}{item.get('h_rest_html','')}</div>{logo_h_tag}</div>"
                    f"<div class='center-time-box'>{time_display}</div>"
                    f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{item.get('away_form','')}</div>{item.get('a_rank_html','')}{item.get('a_inj_html','')}{item.get('a_rest_html','')}</div></div>"
                    f"</div>"
                    f"<div class='ai-story'>{item.get('story','')}</div>"
                    f"{upset_html}"
                    f"<div class='odd-bar'>"
                    f"<span class='odd-item'>승 <span class='odd-val'>{m.get('odd_h','-')}</span> | 무 <span class='odd-val'>{m.get('odd_d','-')}</span> | 패 <span class='odd-val'>{m.get('odd_a','-')}</span></span>"
                    f"<span class='odd-item'>핸디캡 <span class='odd-val'>{m.get('handi_h', '-')} / {m.get('handi_d', '-')} / {m.get('handi_a', '-')}</span></span>"
                    f"<span class='odd-item'>언오버 <span class='odd-val'>{m.get('uo_under', '-')} / {m.get('uo_over', '-')}</span></span>"
                    f"</div>"
                    f"<div class='pred-grid'>{dynamic_pred_boxes}</div>"
                    f"</div>"
                )
                st.markdown(html_code, unsafe_allow_html=True)
            if displayed_count == 0: st.info("조건에 맞는 경기가 없거나 모두 종료되었습니다.")
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
        
        summary_html = f"<div style='background: #111827; border: 1px solid #1E293B; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 24px;'><span style='color: #94A3B8; font-size: 14px; font-weight: 700; display: block; margin-bottom: 5px;'>AI 승무패 14경기 풀-스탯 분석 결과</span><span style='color: #F8FAFC; font-size: 16px; font-weight: 700; display: block; margin-bottom: 8px;'>단통 <span style='color:#10B981;'>{single_pick_count}</span>경기 + 투마킹 <span style='color:#EF4444;'>{double_pick_count}</span>경기</span><span style='color: #F8FAFC; font-size: 24px; font-weight: 900; display: block;'>최종 <span style='color: #00F2FE;'>{total_combinations}</span> 조합 / 예상 구매 금액: <span style='color: #10B981;'>{total_price:,}</span> 원</span></div>"
        st.markdown(summary_html, unsafe_allow_html=True)

        toto_displayed = 0
        toto_paywall_shown = False

        for idx, item in enumerate(toto14_list, 1):
            toto_displayed += 1

            if toto_displayed > 3 and not has_full_access:
                if not toto_paywall_shown:
                    st.markdown("""
                    <div class='match-card' style='text-align: center; padding: 50px 20px; background: rgba(15, 23, 42, 0.6); backdrop-filter: blur(8px); border: 1px solid #F59E0B;'>
                        <h2 style='color: #F59E0B; font-weight: 900; letter-spacing: 1px;'>🔒 승무패 14경기 전체 보기 잠금</h2>
                        <p style='color: #94A3B8; font-weight: 700; font-size: 16px; margin-top: 15px;'>4번째 경기부터의 마킹 전략은 후원회원에게만 공개됩니다.</p>
                        <p style='color: #38BDF8; font-size: 14px; margin-top: 25px; background: rgba(56,189,248,0.1); display: inline-block; padding: 8px 15px; border-radius: 8px;'>좌측 회원 라운지에서 후원회원 전환 확인을 요청해주세요.</p>
                    </div>
                    """, unsafe_allow_html=True)
                    toto_paywall_shown = True
                continue

            m = item['match']
            logo_h_tag = render_logo_html(item.get("home_logo"))
            logo_a_tag = render_logo_html(item.get("away_logo"))
            
            match_id_str = f"TOTO14_{m['id']}"
            toto_match_time = m.get("match_time", "시간 미정")
            live_score_html = f"<span class='match-time-text'>{toto_match_time}</span><b style='color:#475569; font-size:16px;'>VS</b>"
            live_info = live_scores_data.get(match_id_str, {})
            if live_info.get("is_live") is True:
                score_text = live_info.get("score", "0:0")
                if not score_text or score_text == "-": score_text = "0:0"
                if score_text: live_score_html = f"<div style='color:#00F2FE; font-weight:900; font-size:18px;'>{score_text}</div><div style='color:#EF4444; font-size:10px; font-weight:900;'>LIVE</div>"

            html_code = (
                f"<div class='match-card' style='padding: 24px;'>"
                f"<div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;'><span class='badge-primary'>제 {idx} 경기</span><span style='color:#94A3B8; font-size:14px; font-weight:700;'>AI 추천 마킹: <b style='color:#00F2FE;'>{item.get('best_pick_display', '')}</b></span></div>"
                f"<div class='vs-row' style='margin-bottom:15px;'><div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{item.get('home_form','')}</div>{item.get('h_rank_html','')}{item.get('h_inj_html','')}</div>{logo_h_tag}</div>"
                f"<div class='center-time-box' style='width:80px;'>{live_score_html}</div>"
                f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{item.get('away_form','')}</div>{item.get('a_rank_html','')}{item.get('a_inj_html','')}</div></div></div>"
                f"<div style='font-size:12px; color:#64748B; font-weight:700; text-align:center;'>확률 분포: 승 {item.get('p_h')}% | 무 {item.get('p_d')}% | 패 {item.get('p_a')}%</div>"
                f"<div class='prob-bar-container' style='margin-bottom: 15px;'><div class='prob-bar-win' style='width: {item.get('p_h')}%;'></div><div class='prob-bar-draw' style='width: {item.get('p_d')}%;'></div><div class='prob-bar-lose' style='width: {item.get('p_a')}%;'></div></div>"
                f"<div style='display: flex; gap: 10px;'>{item.get('picks_html', '')}</div>"
                f"</div>"
            )
            st.markdown(html_code, unsafe_allow_html=True)
    else: st.info("현재 진행 중인 승무패 14경기 데이터가 없습니다.")

# -----------------------------------------------------------------------------
# [TAB 3] 오늘의 TOP 3
# -----------------------------------------------------------------------------
with main_tab3:
    st.markdown("""
    <div class='section-intro'>
        <div><h2>오늘의 추천 3픽</h2><p>확률·배당 가치·데이터 신뢰도를 함께 검토한 오늘의 우선 분석입니다.</p></div>
    </div>
    """, unsafe_allow_html=True)
    top3_list = dashboard_data.get("top3", [])
    if top3_list:
        displayed_top3 = 0
        for idx, item in enumerate(top3_list, 1):
            if item.get("final_match_time", "") == "시간 미정" or item.get("match", {}).get("match_time", "") == "시간 미정":
                continue
                
            displayed_top3 += 1
            m = item['match']
            logo_h_tag = render_logo_html(item.get("home_logo"))
            logo_a_tag = render_logo_html(item.get("away_logo"))
            dynamic_top3_boxes = generate_pred_boxes(item.get('ev_sorted_picks', []), is_top3_tab=True)
            html_code = (
                f"<div class='match-card top3-glow'>"
                f"<div class='league-title' style='color:#00F2FE;'># {displayed_top3} 최고 가치 추천 픽 • {m.get('league','')}</div>"
                f"<div class='vs-row'><div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{item.get('home_form','')}</div>{item.get('h_rank_html','')}</div>{logo_h_tag}</div>"
                f"<div class='center-time-box'><span class='match-time-text' style='color:#00F2FE;'>{item.get('final_match_time', '')}</span></div>"
                f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{item.get('away_form','')}</div>{item.get('a_rank_html','')}</div></div></div>"
                f"<div class='pred-grid' style='margin-top:20px;'>{dynamic_top3_boxes}</div>"
                f"</div>"
            )
            st.markdown(html_code, unsafe_allow_html=True)
        if displayed_top3 == 0: st.info("현재 배팅 가능한 추천 분석 경기가 없습니다.")
    else: st.info("현재 배팅 가능한 분석 경기가 없습니다.")

# -----------------------------------------------------------------------------
# [TAB 4] 🔥 AI 리포트
# -----------------------------------------------------------------------------
with main_tab4:
    def get_v3_accuracy_stats():
        try:
            conn = sqlite3.connect("ai_predictions.db")
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(predictions)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'ev_pick' not in columns:
                conn.close()
                return None
                
            df_finished = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED' ORDER BY match_time DESC", conn)
            df_proto = df_finished[df_finished['is_toto14'] == 0]
            df_toto = df_finished[df_finished['is_toto14'] == 1]
            
            df_pending = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'PENDING' AND is_toto14 = 0 ORDER BY match_time DESC", conn)
            conn.close()
            
            proto_total = len(df_proto)
            proto_prob_hit = int(df_proto['is_correct_prob'].sum()) if proto_total > 0 else 0
            proto_ev_hit = int(df_proto['is_correct_ev'].sum()) if proto_total > 0 else 0
            
            proto_prob_acc = round((proto_prob_hit / proto_total) * 100, 1) if proto_total > 0 else 0.0
            proto_ev_acc = round((proto_ev_hit / proto_total) * 100, 1) if proto_total > 0 else 0.0

            toto_total = len(df_toto)
            toto_hit = int(df_toto['is_correct_prob'].sum()) if toto_total > 0 else 0
            toto_acc = round((toto_hit / toto_total) * 100, 1) if toto_total > 0 else 0.0
            
            return {
                "proto": {"total": proto_total, "prob_hit": proto_prob_hit, "ev_hit": proto_ev_hit, "prob_acc": proto_prob_acc, "ev_acc": proto_ev_acc},
                "toto": {"total": toto_total, "hit": toto_hit, "acc": toto_acc},
                # 오답노트는 회원 등급과 관계없이 전체 기록을 공개한다.
                "history": df_proto.to_dict('records'),
                "pending": df_pending.to_dict('records')
            }
        except Exception as e:
            return None

    stats = get_v3_accuracy_stats()
    
    if stats is None:
        st.warning("⚠️ 백그라운드 로봇이 V3 듀얼 엔진으로 업그레이드 중입니다. 잠시 후 다시 확인해주세요!")
    else:
        p_stats = stats['proto']
        t_stats = stats['toto']
        
        st.markdown(f"""
        <div style='display:flex; gap:20px; margin-bottom:30px;'>
            <div style='flex:1; background:#0B0F19; padding:20px; border-radius:12px; border:1px solid #1E293B;'>
                <span style='color:#94A3B8; font-size:14px; font-weight:900; display:block; margin-bottom:15px;'>📊 라이브 승부식 A/B 채점 (총 {p_stats['total']}경기)</span>
                <div style='display:flex; justify-content:space-between; align-items:center;'>
                    <div style='text-align:center;'>
                        <span style='color:#CBD5E1; font-size:12px; display:block;'>안전제일 확률픽</span>
                        <span style='color:#10B981; font-size:32px; font-weight:900;'>{p_stats['prob_acc']}%</span>
                        <span style='color:#64748B; font-size:12px; display:block;'>({p_stats['prob_hit']}건 적중)</span>
                    </div>
                    <div style='color:#334155; font-size:24px; font-weight:100;'>VS</div>
                    <div style='text-align:center;'>
                        <span style='color:#CBD5E1; font-size:12px; display:block;'>역배수익 꿀픽</span>
                        <span style='color:#F59E0B; font-size:32px; font-weight:900;'>{p_stats['ev_acc']}%</span>
                        <span style='color:#64748B; font-size:12px; display:block;'>({p_stats['ev_hit']}건 적중)</span>
                    </div>
                </div>
            </div>
            <div style='flex:1; background:#0B0F19; padding:20px; border-radius:12px; border:1px solid #1E293B; display:flex; flex-direction:column; justify-content:center; align-items:center;'>
                <span style='color:#94A3B8; font-size:14px; font-weight:900; display:block; margin-bottom:5px;'>🏆 승무패 14경기 단통 적중률</span>
                <span style='color:#00F2FE; font-size:40px; font-weight:900;'>{t_stats['acc']}%</span>
                <span style='color:#64748B; font-size:13px;'>총 {t_stats['total']}경기 중 {t_stats['hit']}경기 적중</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown("<h4 style='color:#F8FAFC; font-weight:900; margin-top:10px; margin-bottom:20px;'>📜 딥러닝 리얼 오답노트 피드</h4>", unsafe_allow_html=True)
        
        history_data = stats['history']
        if not history_data:
            st.info("아직 채점이 완료된 종료 경기가 없습니다. 로봇이 V3 모드로 열심히 경기 결과를 감시 중입니다!")
        
        for row in history_data:
            h_team = row.get('home_team', '')
            a_team = row.get('away_team', '')
            m_time = row.get('match_time', '')
            score = row.get('actual_score', '-:-')
            note = row.get('ai_note', '')
            
            prob_pick = row.get('prob_pick', '')
            ev_pick = row.get('ev_pick', '')
            prob_ok = row.get('is_correct_prob', 0) == 1
            ev_ok = row.get('is_correct_ev', 0) == 1
            
            if row.get('actual_result') == 'CANCELED':
                score_html = "<span class='report-score' style='color:#94A3B8 !important;'>취소/무효</span>"
                note_style = "real-ai-note"
            else:
                score_html = f"<span class='report-score'>{score}</span>"
                note_style = "real-ai-note" if (prob_ok or ev_ok) else "real-ai-note-fail"

            prob_badge = "<span style='background:#10B981; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>적중</span>" if prob_ok else "<span style='background:#EF4444; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>실패</span>"
            ev_badge = "<span style='background:#10B981; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>적중</span>" if ev_ok else "<span style='background:#EF4444; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>실패</span>"

            html = f"""
            <div class='report-card'>
                <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px; border-bottom:1px solid #1E293B; padding-bottom:15px;'>
                    <div>
                        <span style='color:#64748B; font-size:12px; display:block; margin-bottom:4px;'>{m_time} • {row.get('league','')}</span>
                        <span class='report-team'>{h_team} <span style='color:#475569;'>VS</span> {a_team}</span>
                    </div>
                    <div style='text-align:right;'>
                        <span style='color:#94A3B8; font-size:11px; display:block; margin-bottom:4px;'>최종 결과</span>
                        {score_html}
                    </div>
                </div>
                <div style='display:flex; gap:15px; margin-bottom:10px;'>
                    <div style='flex:1; background:#06080F; padding:10px; border-radius:6px; border:1px solid #1E293B;'>
                        <span style='color:#94A3B8; font-size:11px; display:block; margin-bottom:4px;'>🎯 확률픽 예측</span>
                        <div style='font-size:14px; font-weight:900; color:#F8FAFC;'>{prob_badge} {prob_pick}</div>
                    </div>
                    <div style='flex:1; background:#06080F; padding:10px; border-radius:6px; border:1px solid #1E293B;'>
                        <span style='color:#94A3B8; font-size:11px; display:block; margin-bottom:4px;'>🍯 꿀픽 예측</span>
                        <div style='font-size:14px; font-weight:900; color:#F8FAFC;'>{ev_badge} {ev_pick}</div>
                    </div>
                </div>
                <div class='{note_style}'>{note}</div>
            </div>
            """
            st.markdown(html, unsafe_allow_html=True)
            
        pending_data = stats['pending']
        if pending_data:
            st.markdown("<h4 style='color:#64748B; font-weight:900; margin-top:40px; margin-bottom:15px;'>⏳ 향후 경기 일정 및 분석 현황</h4>", unsafe_allow_html=True)
            
            def parse_time_for_ui(t_str):
                now = datetime.now(timezone(timedelta(hours=9)))
                if not t_str or t_str in ["시간 미정", "마감/진행중"]: return now - timedelta(hours=3)
                try:
                    m = re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})', t_str)
                    if m:
                        mo, d, h, mn = map(int, m.groups())
                        yr = now.year if mo <= now.month + 1 else now.year - 1
                        return datetime(yr, mo, d, h, mn, tzinfo=timezone(timedelta(hours=9)))
                except: pass
                return now - timedelta(hours=3)

            displayed_pending = 0
            for row in pending_data:
                m_time_str = row.get('match_time', '')
                
                if m_time_str == "시간 미정":
                    continue
                    
                displayed_pending += 1
                m_id_str = str(row['match_id'])
                temp_score = row.get('actual_score', '-:-')
                
                m_dt = parse_time_for_ui(m_time_str)
                now = datetime.now(timezone(timedelta(hours=9)))
                
                if now < m_dt:
                    badge_html = "<span style='font-size:12px; font-weight:900; background:rgba(56,189,248,0.15); color:#38BDF8; border:1px solid #38BDF8; padding:4px 10px; border-radius:6px;'>진행 예정</span>"
                elif now < m_dt + timedelta(hours=2.5):
                    badge_html = "<span style='font-size:12px; font-weight:900; background:rgba(16,185,129,0.15); color:#10B981; border:1px solid #10B981; padding:4px 10px; border-radius:6px;'>경기 진행중</span>"
                else:
                    badge_html = "<span style='font-size:12px; font-weight:900; background:rgba(245,158,11,0.15); color:#F59E0B; border:1px solid #F59E0B; padding:4px 10px; border-radius:6px;'>채점 로봇 분석중</span>"

                event_html = ""
                if m_id_str in live_scores_data:
                    live_info = live_scores_data[m_id_str]
                    if live_info.get("score"):
                        temp_score = live_info["score"].replace(" : ", ":")
                    if live_info.get("event"):
                        event_html = f"<div style='font-size:11px; color:#10B981; font-weight:900; margin-top:4px;'>{live_info['event']}</div>"
                    badge_html = "<span style='font-size:12px; font-weight:900; background:rgba(239,68,68,0.15); color:#EF4444; border:1px solid #EF4444; padding:4px 10px; border-radius:6px;'>🔴 LIVE</span>"
                
                html_str = f"<div style='background:#0B0F19; border:1px solid #1E293B; border-radius:10px; padding:16px 20px; margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;'><div><div style='color:#64748B; font-size:12px; margin-bottom:4px; font-weight:900;'>{m_time_str}</div><div style='color:#F8FAFC; font-size:16px; font-weight:900;'>{row.get('home_team')} <span style='color:#64748B;'>VS</span> {row.get('away_team')}</div>{event_html}</div><div style='display:flex; align-items:center; gap:15px;'><span style='color:#00F2FE; font-size:24px; font-weight:900; letter-spacing:1px;'>{temp_score}</span>{badge_html}</div></div>"
                st.markdown(html_str, unsafe_allow_html=True)
                
            if displayed_pending == 0:
                st.info("현재 대기 중인 향후 경기 일정이 없습니다.")

# -----------------------------------------------------------------------------
# [TAB 5] 인증 게시판 · 모두 열람, 후원회원/관리자 작성
# -----------------------------------------------------------------------------
with main_tab5:
    st.markdown("""
    <div class='section-intro'>
        <div>
            <h2>회원 인증 게시판</h2>
            <p>분석 활용 후기와 적중 인증을 함께 확인하는 공간입니다.</p>
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.info("모든 방문자가 글을 읽을 수 있으며 후원회원과 관리자만 작성할 수 있습니다.")

    current_role = st.session_state.get("role", ROLE_GUEST)
    if can_write_board(current_role):
        with st.expander("새 글 작성"):
            with st.form("board-write-form", clear_on_submit=True):
                category = st.selectbox(
                    "분류",
                    ["proof", "free", "notice"] if current_role == ROLE_ADMIN else ["proof", "free"],
                    format_func=lambda value: {
                        "proof": "적중 인증", "free": "자유 이야기", "notice": "공지"
                    }[value],
                )
                post_title = st.text_input("제목", max_chars=80)
                post_body = st.text_area("내용", max_chars=5000, height=150)
                submitted = st.form_submit_button("등록")
                if submitted:
                    ok, message = create_post(
                        int(st.session_state["user_id"]), post_title, post_body, category
                    )
                    (st.success if ok else st.error)(message)
                    if ok:
                        st.rerun()
    elif st.session_state.get("logged_in"):
        st.caption("글 작성은 후원회원 전환 후 이용할 수 있습니다.")
    else:
        st.caption("글 작성은 로그인한 후원회원만 이용할 수 있습니다.")

    category_labels = {"proof": "적중 인증", "free": "자유", "notice": "공지"}
    board_posts = list_posts(limit=100)
    if not board_posts:
        st.info("아직 등록된 글이 없습니다. 첫 인증 기록을 기다리고 있습니다.")

    for post in board_posts:
        with st.container(border=True):
            title_col, meta_col = st.columns([4, 1])
            with title_col:
                st.markdown(f"#### {escape(post['title'])}")
            with meta_col:
                st.caption(category_labels.get(post["category"], "게시글"))
            st.caption(
                f"{post['display_name']} · {post['created_at'].replace('T', ' ')[:16]}"
            )
            st.write(post["body"])

            may_delete = (
                st.session_state.get("role") == ROLE_ADMIN
                or st.session_state.get("username") == post["username"]
            )
            if may_delete and st.button("삭제", key=f"delete-post-{post['id']}"):
                ok, message = delete_post(
                    int(st.session_state["user_id"]), int(post["id"])
                )
                (st.success if ok else st.error)(message)
                if ok:
                    st.rerun()
