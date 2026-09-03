import os
import hmac
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

from grading_postmortem import (
    build_postmortem,
    parse_postmortem_json,
    postmortem_text,
    stats_from_note,
)

from member_system import (
    ROLE_ADMIN,
    ROLE_GUEST,
    ROLE_LABELS,
    ROLE_MEMBER,
    ROLE_SUPPORTER,
    authenticate_session,
    authenticate_user,
    bootstrap_admin,
    can_write_board,
    create_login_session,
    create_notice,
    create_post,
    delete_post,
    get_member_storage_status,
    get_post_image,
    init_member_db,
    list_active_notices,
    list_notices,
    list_posts,
    list_support_requests,
    list_users,
    refresh_user_access,
    register_user,
    request_supporter,
    revoke_login_session,
    review_support_request,
    set_user_role,
    set_user_status,
    set_notice_visibility,
    set_post_visibility,
    sync_member_storage_now,
    update_post,
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


def load_world_dashboard_data():
    """Load the isolated WORLD feed without affecting the main dashboard."""
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/world_dashboard.json?t={int(time.time())}"
    try:
        res = requests.get(url, headers=NO_CACHE_HEADERS, timeout=5)
        if res.status_code == 200:
            payload = res.json()
            if isinstance(payload, dict):
                return payload
    except Exception:
        pass
    return {"matches": [], "source_meta": {}, "rejected_summary": []}

def load_prediction_results(grading_snapshot=None):
    """채점 DB의 종료 상태와 최종 점수를 화면 카드에 직접 연결한다."""
    embedded_rows = []
    if isinstance(grading_snapshot, dict):
        embedded_rows = grading_snapshot.get("finished", []) or []
    if embedded_rows:
        return {
            str(row.get("match_id")): {
                "actual_score": row.get("actual_score"),
                "actual_result": row.get("actual_result"),
                "ai_note": row.get("ai_note"),
                "prob_pick": row.get("prob_pick"),
                "ev_pick": row.get("ev_pick"),
                "is_correct_prob": int(row.get("is_correct_prob") or 0),
                "is_correct_ev": int(row.get("is_correct_ev") or 0),
                "postmortem_json": row.get("postmortem_json"),
            }
            for row in embedded_rows
            if row.get("match_id") is not None
        }
    try:
        conn = sqlite3.connect("ai_predictions.db", timeout=5)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(predictions)")}
        postmortem_expr = "postmortem_json" if "postmortem_json" in columns else "'{}'"
        rows = conn.execute(
            f"""
            SELECT match_id, actual_score, actual_result, ai_note,
                   prob_pick, ev_pick, is_correct_prob, is_correct_ev,
                   {postmortem_expr}
            FROM predictions
            """
        ).fetchall()
        conn.close()
        return {
            str(match_id): {
                "actual_score": actual_score,
                "actual_result": actual_result,
                "ai_note": ai_note,
                "prob_pick": prob_pick,
                "ev_pick": ev_pick,
                "is_correct_prob": int(is_correct_prob or 0),
                "is_correct_ev": int(is_correct_ev or 0),
                "postmortem_json": postmortem_data,
            }
            for (
                match_id, actual_score, actual_result, ai_note,
                prob_pick, ev_pick, is_correct_prob, is_correct_ev,
                postmortem_data,
            ) in rows
        }
    except Exception:
        return {}


_PLACEHOLDER_TEAM_NAMES = {
    "", "-", "미정", "미확정", "tbd", "unknown", "홈팀", "원정팀",
    "home", "away", "team1", "team2",
}
_NORMALIZED_PLACEHOLDER_TEAM_NAMES = {
    re.sub(r"[\s._-]+", "", name.casefold())
    for name in _PLACEHOLDER_TEAM_NAMES
}


def _is_displayable_match_item(item):
    """Block only unidentified placeholder rows, never a real named team."""
    if not isinstance(item, dict) or not isinstance(item.get("match"), dict):
        return False
    match = item["match"]
    home = str(match.get("home") or "").strip()
    away = str(match.get("away") or "").strip()
    home_key = re.sub(r"[\s._-]+", "", home.casefold())
    away_key = re.sub(r"[\s._-]+", "", away.casefold())
    if (
        home_key in _NORMALIZED_PLACEHOLDER_TEAM_NAMES
        or away_key in _NORMALIZED_PLACEHOLDER_TEAM_NAMES
    ):
        return False
    return re.sub(r"\W+", "", home.casefold()) != re.sub(
        r"\W+", "", away.casefold()
    )

# -----------------------------------------------------------------------------
# 2. 회원·권한·게시판 DB 엔진
# -----------------------------------------------------------------------------
init_member_db()
MEMBER_STORAGE_STATUS = get_member_storage_status()


def get_private_setting(name: str) -> str:
    """Read a deployment secret without exposing it in the public repository."""
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


ADMIN_BOOTSTRAP_USERNAME = get_private_setting("DJ_ADMIN_USERNAME")
ADMIN_BOOTSTRAP_TOKEN = get_private_setting("DJ_ADMIN_BOOTSTRAP_TOKEN")


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
    .report-head { display: flex; justify-content: space-between; align-items: center; gap: 18px; margin-bottom: 15px; border-bottom: 1px solid #1E293B; padding-bottom: 15px; }
    .report-match { min-width: 0; flex: 1 1 auto; }
    .report-result { flex: 0 0 auto; text-align: right; }
    .report-picks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 15px; margin-bottom: 10px; }
    .report-pick-box { min-width: 0; background: #06080F; padding: 10px; border-radius: 6px; border: 1px solid #1E293B; }
    .pending-report-card { min-width: 0; background: #0B0F19; border: 1px solid #1E293B; border-radius: 10px; padding: 16px 20px; margin-bottom: 10px; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 18px; }
    .pending-report-match { min-width: 0; }
    .pending-report-teams { color: #F8FAFC; font-size: 16px; font-weight: 900; overflow-wrap: anywhere; word-break: keep-all; }
    .pending-report-status { min-width: 0; display: flex; align-items: center; justify-content: flex-end; gap: 15px; }
    .pending-report-score { color: #00F2FE; font-size: 24px; font-weight: 900; letter-spacing: 1px; white-space: nowrap; }
    .pending-report-badge { display: inline-flex; align-items: center; justify-content: center; max-width: 100%; font-size: 12px; font-weight: 900; padding: 4px 10px; border-radius: 6px; line-height: 1.35; text-align: center; white-space: normal; }
    .pending-report-badge.upcoming { background: rgba(56,189,248,0.15); color: #38BDF8; border: 1px solid #38BDF8; }
    .pending-report-badge.playing { background: rgba(16,185,129,0.15); color: #10B981; border: 1px solid #10B981; }
    .pending-report-badge.grading { background: rgba(245,158,11,0.15); color: #F59E0B; border: 1px solid #F59E0B; }
    .pending-report-badge.live { background: rgba(239,68,68,0.15); color: #EF4444; border: 1px solid #EF4444; }

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
    [data-testid="stSidebar"] > div:first-child { padding-top: 1rem; }
    .sidebar-brand {
        margin: 4px 0 18px;
        padding-bottom: 17px;
        border-bottom: 1px solid var(--dj-line);
    }
    .sidebar-brand strong {
        display: block;
        color: #F8FAFC;
        font-size: 20px;
        font-weight: 900;
        letter-spacing: -.45px;
        margin-bottom: 5px;
    }
    .sidebar-brand span { color: #7F8DA3; font-size: 11px; line-height: 1.55; }
    .member-profile {
        margin: 2px 0 12px;
        padding: 13px;
        border: 1px solid rgba(148, 163, 184, .14);
        border-radius: 14px;
        background: rgba(255, 255, 255, .025);
    }
    .member-profile-row { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .member-avatar {
        display: grid;
        place-items: center;
        width: 34px;
        height: 34px;
        flex: 0 0 34px;
        border: 1px solid rgba(25, 230, 242, .25);
        border-radius: 11px;
        background: rgba(25, 230, 242, .08);
        color: var(--dj-cyan);
        font-size: 13px;
        font-weight: 900;
    }
    .member-profile-copy { min-width: 0; flex: 1; }
    .member-profile-copy strong {
        display: block;
        overflow: hidden;
        color: #F8FAFC;
        font-size: 13px;
        font-weight: 850;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .member-online {
        display: flex;
        align-items: center;
        gap: 5px;
        color: #7F8DA3;
        font-size: 10px;
        margin-top: 3px;
    }
    .member-online::before {
        content: "";
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--dj-green);
        box-shadow: 0 0 0 3px rgba(23, 201, 139, .10);
    }
    .member-role {
        flex: 0 0 auto;
        padding: 4px 7px;
        border: 1px solid rgba(148, 163, 184, .18);
        border-radius: 999px;
        color: #B7C3D4;
        background: rgba(148, 163, 184, .06);
        font-size: 9px;
        font-weight: 850;
    }
    .member-role.supporter { color: #F3D995; border-color: rgba(248, 198, 92, .26); background: rgba(248, 198, 92, .07); }
    .member-role.admin { color: #F3A9B0; border-color: rgba(255, 91, 104, .28); background: rgba(255, 91, 104, .07); }
    .member-access-note {
        margin: 0 0 12px;
        padding: 10px 11px;
        border-left: 2px solid rgba(25, 230, 242, .42);
        border-radius: 8px;
        background: rgba(25, 230, 242, .035);
        color: #91A1B7;
        font-size: 10px;
        line-height: 1.6;
    }
    .sidebar-flash {
        margin: 0 0 12px;
        padding: 9px 11px;
        border: 1px solid rgba(25, 230, 242, .14);
        border-radius: 9px;
        background: rgba(25, 230, 242, .025);
        color: #9FB0C4;
        font-size: 10px;
        line-height: 1.55;
    }
    .admin-mode-label {
        display: flex;
        align-items: center;
        gap: 7px;
        color: #B8C4D5;
        font-size: 10px;
        font-weight: 800;
    }
    .admin-mode-label::before {
        content: "";
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--dj-red);
    }
    div[data-testid="stDialog"] div[role="dialog"] {
        overflow: hidden;
        border: 1px solid rgba(25, 230, 242, .26);
        border-radius: 24px;
        background:
            radial-gradient(circle at 92% 8%, rgba(25, 230, 242, .15), transparent 31%),
            linear-gradient(145deg, #0D1B31 0%, #080E19 72%);
        box-shadow: 0 30px 90px rgba(0, 0, 0, .58);
    }
    div[data-testid="stDialog"] img {
        width: 100%;
        max-height: 340px;
        object-fit: cover;
        border: 1px solid rgba(148, 163, 184, .15);
        border-radius: 17px;
    }
    .event-popup-copy { padding: 5px 2px 3px; }
    .event-popup-kicker {
        margin-bottom: 9px;
        color: var(--dj-cyan);
        font-size: 10px;
        font-weight: 900;
        letter-spacing: .16em;
    }
    .event-popup-title {
        margin-bottom: 11px;
        color: #F7FAFF;
        font-size: clamp(23px, 4vw, 31px);
        font-weight: 950;
        line-height: 1.25;
        word-break: keep-all;
    }
    .event-popup-body {
        color: #B5C1D2;
        font-size: 14px;
        line-height: 1.75;
        overflow-wrap: anywhere;
    }

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
        min-height: 0;
        padding: 0 6px;
        border-radius: 0;
        background: transparent;
        border: 0;
        box-shadow: none;
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
        min-width: 0;
        max-width: 100%;
        overflow: hidden;
    }
    .report-card *, .pending-report-card * { box-sizing: border-box; min-width: 0; }
    .report-team, .report-pick-box, .real-ai-note, .real-ai-note-fail,
    .pending-report-match, .pending-report-teams { overflow-wrap: anywhere; word-break: keep-all; white-space: normal; }

    .join-strip {
        margin: 0 0 16px;
        padding: 15px 17px;
        border: 1px solid rgba(25, 230, 242, .24);
        border-radius: 14px;
        background: linear-gradient(120deg, rgba(25, 230, 242, .07), rgba(79, 140, 255, .045));
    }
    .join-strip strong { display: block; color: #F8FAFC; font-size: 15px; margin-bottom: 4px; }
    .join-strip span { color: #9AA9BD; font-size: 12px; line-height: 1.6; }
    .notice-strip {
        margin: 0 0 14px;
        padding: 11px 14px;
        border-left: 3px solid var(--dj-gold);
        border-radius: 10px;
        background: rgba(248, 198, 92, .07);
        color: #E8D8B1;
        font-size: 12px;
        overflow-wrap: anywhere;
    }
    .board-photo-caption {
        margin: 7px 0 4px;
        color: #718096;
        font-size: 10px;
    }

    .main .stButton > button,
    [data-testid="stAppViewContainer"] > .main .stButton > button {
        width: 100%;
        min-height: 42px;
        border-radius: 11px !important;
        border: 1px solid rgba(25, 230, 242, .35) !important;
        color: #061016 !important;
        background: linear-gradient(135deg, var(--dj-cyan), #67F3C6) !important;
        font-weight: 900 !important;
    }
    [data-testid="stSidebar"] .stButton > button,
    [data-testid="stSidebar"] [data-testid="stFormSubmitButton"] > button {
        width: 100%;
        min-height: 38px;
        border: 1px solid rgba(148, 163, 184, .20) !important;
        border-radius: 9px !important;
        background: rgba(255, 255, 255, .025) !important;
        color: #C7D1DF !important;
        box-shadow: none !important;
        font-size: 12px !important;
        font-weight: 750 !important;
    }
    [data-testid="stSidebar"] .stButton > button:hover,
    [data-testid="stSidebar"] [data-testid="stFormSubmitButton"] > button:hover {
        border-color: rgba(25, 230, 242, .34) !important;
        color: #F8FAFC !important;
        background: rgba(25, 230, 242, .055) !important;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] {
        border-color: rgba(148, 163, 184, .14) !important;
        border-radius: 11px !important;
        background: rgba(255, 255, 255, .015) !important;
    }
    [data-testid="stSidebar"] [data-testid="stAlert"] {
        border-radius: 10px !important;
        background: rgba(148, 163, 184, .05) !important;
        border-color: rgba(148, 163, 184, .12) !important;
    }
    .grade-summary-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 14px;
        margin-bottom: 30px;
    }
    .grade-summary-card {
        min-width: 0;
        padding: 20px;
        border: 1px solid var(--dj-line);
        border-radius: 14px;
        background: rgba(11, 15, 25, .92);
        overflow: hidden;
    }
    .grade-summary-title { color: #94A3B8; font-size: 13px; font-weight: 900; margin-bottom: 15px; overflow-wrap: anywhere; }
    .grade-dual-row { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); align-items: center; gap: 12px; }
    .grade-metric { min-width: 0; text-align: center; }
    .grade-metric-label, .grade-metric-note { display: block; color: #9EACC0; font-size: 11px; overflow-wrap: anywhere; }
    .grade-metric-value { display: block; color: var(--dj-green); font-size: 31px; font-weight: 900; line-height: 1.15; }
    .grade-metric-value.gold { color: #F59E0B; }
    .grade-versus { color: #334155; font-size: 20px; font-weight: 700; }
    .grade-toto { display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; }
    .grade-toto .grade-metric-value { color: var(--dj-cyan); font-size: 38px; }
    .stTextInput input, [data-baseweb="select"] > div {
        border-radius: 10px !important;
        background: rgba(8, 13, 23, .92) !important;
        border-color: var(--dj-line) !important;
    }

    @media (max-width: 768px) {
        html, body, .stApp, [data-testid="stAppViewContainer"], .main { max-width: 100vw !important; overflow-x: hidden !important; }
        .block-container { padding: .65rem .72rem 3rem !important; }
        .block-container, [data-testid="stVerticalBlock"], [data-testid="stMarkdownContainer"] { min-width: 0 !important; max-width: 100% !important; }
        .brand-shell { padding: 25px 20px 22px; border-radius: 18px; margin-top: 3px; }
        .brand-row { align-items: flex-start; gap: 12px; min-width: 0; }
        .brand-mark { width: 48px; height: 48px; flex-basis: 48px; }
        .brand-title { font-size: clamp(21px, 7vw, 26px); overflow-wrap: anywhere; }
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
        .report-card { padding: 15px 13px !important; }
        .report-head { flex-direction: column; align-items: flex-start; gap: 10px; }
        .report-match, .report-result { width: 100%; text-align: left !important; }
        .report-picks { grid-template-columns: minmax(0, 1fr); gap: 8px; }
        .report-team { font-size: 14px; line-height: 1.45; }
        .report-score { font-size: 22px !important; }
        .real-ai-note, .real-ai-note-fail { padding: 12px; font-size: 12px; line-height: 1.65; }
        .pending-report-card { grid-template-columns: minmax(0, 1fr); padding: 14px; gap: 11px; }
        .pending-report-status { justify-content: flex-start; flex-wrap: wrap; gap: 9px; }
        .pending-report-teams { font-size: 14px; line-height: 1.45; }
        .pending-report-score { font-size: 21px; }
        .join-strip { padding: 13px 14px; }
        .grade-summary-grid { grid-template-columns: minmax(0, 1fr); gap: 10px; margin-bottom: 22px; }
        .grade-summary-card { padding: 15px 12px; }
        .grade-dual-row { gap: 7px; }
        .grade-metric-value { font-size: 25px; }
        .grade-toto .grade-metric-value { font-size: 31px; }
        .report-card, .pending-report-card { width: 100%; max-width: 100%; contain: inline-size; }
        .report-card *, .pending-report-card * { max-width: 100%; }
    }
    @media (max-width: 430px) {
        .brand-row { display: block; }
        .brand-mark { margin-bottom: 14px; }
        .status-cell small { font-size: 9px; }
        .status-cell strong { font-size: 12px; }
        .grade-dual-row { grid-template-columns: minmax(0, 1fr); }
        .grade-dual-row > div:nth-child(2) { display: none; }
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
if 'auth_token' not in st.session_state:
    st.session_state['auth_token'] = ""
if 'supporter_expires_at' not in st.session_state:
    st.session_state['supporter_expires_at'] = None

# 서버의 live_scores.json은 5분마다 갱신된다. 브라우저도 1분마다 조용히
# 다시 읽어야 사용자가 수동 새로고침을 하지 않아도 점수가 움직인다.
if st_autorefresh is not None:
    st_autorefresh(interval=60 * 1000, key="live-score-refresh")

st.sidebar.markdown(
    """
    <div class="sidebar-brand">
        <strong>D.J 회원 라운지</strong>
        <span>내 등급과 이용 가능한 분석을 확인하세요.</span>
    </div>
    """,
    unsafe_allow_html=True,
)


AUTH_QUERY_KEY = "dj_session"


def read_auth_query_token():
    try:
        value = st.query_params.get(AUTH_QUERY_KEY, "")
        if isinstance(value, list):
            value = value[0] if value else ""
        return str(value or "").strip()
    except Exception:
        return ""


def write_auth_query_token(token):
    if not token:
        return
    try:
        st.query_params[AUTH_QUERY_KEY] = token
    except Exception:
        pass


def clear_auth_query_token():
    try:
        if AUTH_QUERY_KEY in st.query_params:
            del st.query_params[AUTH_QUERY_KEY]
    except Exception:
        pass


def apply_user_session(user, auth_token=""):
    st.session_state['logged_in'] = True
    st.session_state['user_id'] = user['id']
    st.session_state['username'] = user['username']
    st.session_state['role'] = user['role']
    st.session_state['auth_token'] = auth_token
    st.session_state['supporter_expires_at'] = user.get('supporter_expires_at')


def format_membership_expiry(value):
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return ""


def begin_user_session(user):
    auth_token = create_login_session(int(user['id'])) or ""
    apply_user_session(user, auth_token)
    write_auth_query_token(auth_token)


# Streamlit 화면 상태가 초기화되어도 주소에 저장된 기기별 로그인 표식으로 복구한다.
if not st.session_state['logged_in']:
    saved_auth_token = read_auth_query_token()
    if saved_auth_token:
        restored_user = authenticate_session(saved_auth_token)
        if restored_user:
            apply_user_session(restored_user, saved_auth_token)
        else:
            clear_auth_query_token()

# 로그인 상태가 유지되는 동안에도 매 화면 실행마다 실제 DB 등급을 다시 확인한다.
# 따라서 관리자가 지정한 후원 만료 시각이 지나면 다음 자동 새로고침(최대 1분)에서
# 세션과 DB가 함께 일반회원으로 복귀한다.
if st.session_state['logged_in'] and st.session_state.get('user_id'):
    previous_role = st.session_state.get('role', ROLE_MEMBER)
    refreshed_user = refresh_user_access(int(st.session_state['user_id']))
    if refreshed_user:
        st.session_state['username'] = refreshed_user['username']
        st.session_state['role'] = refreshed_user['role']
        st.session_state['supporter_expires_at'] = refreshed_user.get(
            'supporter_expires_at'
        )
        if previous_role == ROLE_SUPPORTER and refreshed_user['role'] == ROLE_MEMBER:
            st.session_state['auth_flash'] = (
                "후원회원 이용기간 30일이 종료되어 일반회원으로 전환되었습니다."
            )
    else:
        clear_auth_query_token()
        st.session_state['logged_in'] = False
        st.session_state['user_id'] = None
        st.session_state['username'] = ""
        st.session_state['role'] = ROLE_GUEST
        st.session_state['auth_token'] = ""
        st.session_state['supporter_expires_at'] = None


def submit_registration(username, display_name, password, password_check,
                        adult_confirm, service_agree, privacy_agree):
    if not username.strip() or not password:
        return False, "아이디와 비밀번호를 입력해주세요."
    if password != password_check:
        return False, "비밀번호 확인이 일치하지 않습니다."
    if not adult_confirm:
        return False, "만 19세 이상 확인이 필요합니다."
    if not service_agree:
        return False, "서비스 이용 안내에 동의해주세요."
    if not privacy_agree:
        return False, "개인정보 수집·이용에 동의해주세요."
    return register_user(username, password, display_name)


def render_signup_guide():
    st.markdown("**서비스 이용 안내 (필수)**")
    st.caption(
        "경기 통계와 추천 확률은 참고 정보이며 적중·수익을 보장하지 않습니다. "
        "구매 대행·베팅 중개·불법 사이트 알선은 제공하지 않으며, 이용 판단과 책임은 본인에게 있습니다."
    )
    st.markdown("**개인정보 수집·이용 안내 (필수)**")
    st.caption(
        "수집 항목: 아이디, 표시 이름(선택), 암호화된 비밀번호 · "
        "이용 목적: 가입, 로그인, 회원 등급 및 부정 이용 관리 · "
        "보유 기간: 회원 탈퇴 또는 이용 목적 달성 시까지(관계 법령상 보존이 필요한 경우 제외). "
        "동의를 거부할 수 있으나 가입은 제한됩니다."
    )
    st.caption("도박 문제 상담: 국번 없이 1336")


if hasattr(st, "dialog"):
    @st.dialog("D.J SPORTS 로그인")
    def open_login_dialog():
        st.caption("가입한 아이디로 접속하면 회원 등급에 맞는 분석이 열립니다.")
        with st.form("dialog-login-form"):
            dialog_user = st.text_input("아이디", key="dialog-login-user")
            dialog_password = st.text_input(
                "비밀번호", type="password", key="dialog-login-password"
            )
            dialog_login = st.form_submit_button("로그인", use_container_width=True)
        if dialog_login:
            user = authenticate_user(dialog_user, dialog_password)
            if user:
                begin_user_session(user)
                st.session_state['auth_flash'] = f"환영합니다, {user['display_name']}님!"
                st.rerun()
            else:
                st.error("아이디 또는 비밀번호가 틀렸습니다.")


    @st.dialog("무료 회원가입")
    def open_signup_dialog():
        st.caption("회원가입은 무료이며 기본 등급은 일반회원입니다.")
        with st.form("dialog-signup-form"):
            dialog_new_user = st.text_input("사용할 아이디", key="dialog-signup-user")
            dialog_display_name = st.text_input(
                "표시 이름 (선택)", key="dialog-signup-display"
            )
            dialog_new_password = st.text_input(
                "비밀번호 (8자 이상)", type="password", key="dialog-signup-password"
            )
            dialog_password_check = st.text_input(
                "비밀번호 확인", type="password", key="dialog-signup-password-check"
            )
            st.markdown("---")
            render_signup_guide()
            dialog_adult = st.checkbox(
                "만 19세 이상입니다.", key="dialog-signup-adult"
            )
            dialog_service = st.checkbox(
                "서비스 이용 안내에 동의합니다.", key="dialog-signup-service"
            )
            dialog_privacy = st.checkbox(
                "개인정보 수집·이용에 동의합니다.", key="dialog-signup-privacy"
            )
            dialog_submit = st.form_submit_button("가입하기", use_container_width=True)
        if dialog_submit:
            ok, message = submit_registration(
                dialog_new_user, dialog_display_name, dialog_new_password,
                dialog_password_check, dialog_adult, dialog_service, dialog_privacy
            )
            if ok:
                st.session_state['auth_flash'] = f"{message} 이제 로그인해주세요."
                st.session_state['next_auth_menu'] = "로그인"
                st.rerun()
            else:
                st.error(message)


    @st.dialog("D.J SPORTS EVENT")
    def open_notice_popup(notice):
        notice_id = int(notice["id"])
        popup_image = get_post_image(notice_id)
        if popup_image:
            st.image(popup_image[1], use_container_width=True)
        safe_title = escape(str(notice.get('title', '서비스 공지')))
        safe_body = escape(str(notice.get("body", ""))).replace("\n", "<br>")
        st.markdown(
            f"""
            <div class="event-popup-copy">
                <div class="event-popup-kicker">D.J SPORTS · NOTICE</div>
                <div class="event-popup-title">{safe_title}</div>
                <div class="event-popup-body">{safe_body}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        notice_link = str(notice.get("notice_link") or "").strip()
        if notice_link:
            st.link_button("이벤트 자세히 보기 →", notice_link, use_container_width=True)
        if st.button("닫기", key=f"dismiss-popup-{notice_id}", use_container_width=True):
            dismissed = set(st.session_state.get("dismissed_popup_ids", []))
            dismissed.add(notice_id)
            st.session_state["dismissed_popup_ids"] = sorted(dismissed)
            st.rerun()


if st.session_state.get('next_auth_menu'):
    st.session_state['auth_menu'] = st.session_state.pop('next_auth_menu')

auth_flash = st.session_state.pop('auth_flash', None)
if auth_flash:
    st.sidebar.markdown(
        f"<div class='sidebar-flash'>✓ {escape(str(auth_flash))}</div>",
        unsafe_allow_html=True,
    )
    try:
        st.toast(auth_flash, icon="✅")
    except Exception:
        pass

if not st.session_state['logged_in']:
    menu = ["로그인", "회원가입"]
    choice = st.sidebar.selectbox("메뉴 선택", menu, key="auth_menu")

    if choice == "로그인":
        st.sidebar.subheader("접속하기")
        with st.sidebar.form("sidebar-login-form"):
            username = st.text_input("아이디", key="sidebar-login-user")
            password = st.text_input(
                "비밀번호", type='password', key="sidebar-login-password"
            )
            login_submitted = st.form_submit_button("로그인", use_container_width=True)
        if login_submitted:
            user = authenticate_user(username, password)
            if user:
                begin_user_session(user)
                st.session_state['auth_flash'] = f"환영합니다, {user['display_name']}님!"
                st.rerun()
            else:
                st.sidebar.warning("아이디 또는 비밀번호가 틀렸습니다.")

    elif choice == "회원가입":
        st.sidebar.subheader("새 계정 만들기")
        with st.sidebar.form("sidebar-signup-form"):
            new_user = st.text_input("사용할 아이디", key="sidebar-signup-user")
            new_display_name = st.text_input(
                "표시 이름 (선택)", key="sidebar-signup-display"
            )
            new_password = st.text_input(
                "비밀번호 (8자 이상)", type='password', key="sidebar-signup-password"
            )
            new_password_check = st.text_input(
                "비밀번호 확인", type='password', key="sidebar-signup-password-check"
            )
            st.markdown("---")
            render_signup_guide()
            adult_confirm = st.checkbox(
                "만 19세 이상입니다.", key="sidebar-signup-adult"
            )
            service_agree = st.checkbox(
                "서비스 이용 안내에 동의합니다.", key="sidebar-signup-service"
            )
            privacy_agree = st.checkbox(
                "개인정보 수집·이용에 동의합니다.", key="sidebar-signup-privacy"
            )
            signup_submitted = st.form_submit_button("가입하기", use_container_width=True)

        if signup_submitted:
            ok, message = submit_registration(
                new_user, new_display_name, new_password, new_password_check,
                adult_confirm, service_agree, privacy_agree
            )
            if ok:
                st.session_state['auth_flash'] = f"{message} 로그인 메뉴에서 접속해주세요."
                st.session_state['next_auth_menu'] = "로그인"
                st.rerun()
            else:
                st.sidebar.error(message)

else:
    current_role = st.session_state.get('role', ROLE_MEMBER)
    current_username = str(st.session_state.get('username', '회원'))
    safe_username = escape(current_username)
    safe_initial = escape((current_username[:1] or "D").upper())
    safe_role_label = escape(ROLE_LABELS.get(current_role, '일반회원'))
    role_class = (
        "admin" if current_role == ROLE_ADMIN
        else "supporter" if current_role == ROLE_SUPPORTER
        else "member"
    )
    st.sidebar.markdown(
        f"""
        <div class="member-profile">
            <div class="member-profile-row">
                <div class="member-avatar">{safe_initial}</div>
                <div class="member-profile-copy">
                    <strong>{safe_username}</strong>
                    <span class="member-online">접속 중</span>
                </div>
            </div>
            <span class="member-role {role_class}">{safe_role_label}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if current_role in {ROLE_SUPPORTER, ROLE_ADMIN}:
        access_note = "프로토 LIVE·전체 경기의 전체 분석과 인증 게시판 작성 권한이 열려 있습니다."
    else:
        access_note = "추천 3픽과 공개 리포트를 이용 중입니다. 후원 확인 후 전체 분석이 열립니다."
    st.sidebar.markdown(
        f"<div class='member-access-note'>{escape(access_note)}</div>",
        unsafe_allow_html=True,
    )
    if current_role == ROLE_SUPPORTER:
        expiry_label = format_membership_expiry(
            st.session_state.get('supporter_expires_at')
        )
        if expiry_label:
            st.sidebar.caption(f"후원회원 이용 만료 · {expiry_label}")

    if current_role not in {ROLE_SUPPORTER, ROLE_ADMIN}:
        with st.sidebar.expander("후원회원 전환 확인 요청"):
            depositor_name = st.text_input("확인용 입금자명", key="support-depositor")
            support_note = st.text_area("관리자에게 남길 메모", key="support-note", height=80)
            if st.button("확인 요청 보내기", key="support-request-submit"):
                ok, message = request_supporter(
                    int(st.session_state['user_id']), depositor_name, support_note
                )
                (st.success if ok else st.error)(message)

    # 서버 비밀 설정에 등록한 운영자 아이디에만 최초 관리자 인증창을 보여준다.
    # 표시 이름을 "관리자"로 적는 것만으로는 절대 관리자 권한을 얻을 수 없다.
    owner_username_matches = bool(
        ADMIN_BOOTSTRAP_USERNAME
        and hmac.compare_digest(
            st.session_state['username'].casefold(),
            ADMIN_BOOTSTRAP_USERNAME.casefold(),
        )
    )
    if current_role != ROLE_ADMIN and owner_username_matches:
        with st.sidebar.expander("🔐 운영자 권한 인증", expanded=True):
            st.caption("Streamlit 비밀 설정에 등록한 관리자 인증키를 입력해주세요.")
            with st.form("admin-bootstrap-form"):
                admin_token = st.text_input(
                    "관리자 인증키", type="password", key="admin-bootstrap-token"
                )
                admin_bootstrap_submit = st.form_submit_button(
                    "관리자 권한 열기", use_container_width=True
                )
            if admin_bootstrap_submit:
                ok, message = bootstrap_admin(
                    int(st.session_state['user_id']),
                    st.session_state['username'],
                    admin_token,
                    ADMIN_BOOTSTRAP_USERNAME,
                    ADMIN_BOOTSTRAP_TOKEN,
                )
                if ok:
                    st.session_state['role'] = ROLE_ADMIN
                    st.session_state['auth_flash'] = message
                    st.rerun()
                else:
                    st.error(message)

    if st.sidebar.button("로그아웃 →", key="sidebar-logout"):
        logout_token = st.session_state.get('auth_token') or read_auth_query_token()
        revoke_login_session(str(logout_token or ""))
        clear_auth_query_token()
        st.session_state['logged_in'] = False
        st.session_state['user_id'] = None
        st.session_state['username'] = ""
        st.session_state['role'] = ROLE_GUEST
        st.session_state['auth_token'] = ""
        st.session_state['supporter_expires_at'] = None
        st.rerun()

    # 관리자 전용 회원·권한 관리
    if current_role == ROLE_ADMIN:
        st.sidebar.markdown("---")
        storage_status = get_member_storage_status()
        storage_ok = bool(storage_status.get("persistent"))
        if not storage_ok:
            st.sidebar.warning(storage_status.get("message"))
        with st.sidebar.expander("회원 DB 저장 상태", expanded=not storage_ok):
            if storage_ok:
                st.success("S3 영구 저장 정상")
            else:
                st.caption(storage_status.get("message"))
            if st.button(
                "S3 연결 확인 · 지금 백업",
                key="admin-member-storage-sync",
                use_container_width=True,
            ):
                sync_ok, sync_message = sync_member_storage_now()
                (st.success if sync_ok else st.error)(sync_message)
                if sync_ok:
                    st.rerun()

        pending_requests = [
            request for request in list_support_requests() if request["status"] == "pending"
        ]
        with st.sidebar.expander(
            f"관리자 도구 · 확인 대기 {len(pending_requests)}건", expanded=False
        ):
            st.markdown("<div class='admin-mode-label'>ADMIN CONSOLE</div>", unsafe_allow_html=True)
            st.caption("회원 등급과 계정 상태를 관리합니다.")
            users = list_users()
            if users:
                user_df = pd.DataFrame(users).rename(columns={
                    "username": "아이디", "display_name": "표시 이름",
                    "role": "등급", "status": "상태", "created_at": "가입 시각",
                    "last_login_at": "최근 로그인", "supporter_expires_at": "후원 만료",
                })
                if "후원 만료" in user_df.columns:
                    user_df["후원 만료"] = user_df["후원 만료"].map(
                        lambda value: format_membership_expiry(value) or "-"
                    )
                visible_columns = [
                    "아이디", "표시 이름", "등급", "상태",
                    "가입 시각", "최근 로그인", "후원 만료",
                ]
                st.dataframe(
                    user_df[visible_columns], hide_index=True,
                    use_container_width=True, height=min(230, 36 * (len(user_df) + 1)),
                )

                target_username = st.selectbox(
                    "관리할 회원", [user["username"] for user in users],
                    key="admin-target-user",
                )
                target_user = next(
                    user for user in users if user["username"] == target_username
                )
                role_options = [ROLE_MEMBER, ROLE_SUPPORTER, ROLE_ADMIN]
                selected_role = st.selectbox(
                    "회원 등급", role_options,
                    index=role_options.index(target_user["role"]),
                    format_func=lambda value: ROLE_LABELS[value],
                    key="admin-target-role",
                )
                role_button_label = (
                    "후원회원 지정 · 30일 적용/연장"
                    if selected_role == ROLE_SUPPORTER else "등급 적용"
                )
                if st.button(
                    role_button_label,
                    key="admin-role-apply",
                    use_container_width=True,
                ):
                    ok, message = set_user_role(
                        int(st.session_state['user_id']), target_username, selected_role
                    )
                    (st.success if ok else st.error)(message)
                    if ok:
                        st.rerun()

                selected_status = st.selectbox(
                    "계정 상태", ["active", "suspended"],
                    index=0 if target_user["status"] == "active" else 1,
                    format_func=lambda value: "정상" if value == "active" else "정지",
                    key="admin-target-status",
                )
                if st.button("상태 적용", key="admin-status-apply", use_container_width=True):
                    ok, message = set_user_status(
                        int(st.session_state['user_id']), target_username, selected_status
                    )
                    (st.success if ok else st.error)(message)
                    if ok:
                        st.rerun()

            st.divider()
            st.markdown(f"**후원 확인 대기 {len(pending_requests)}건**")
            if pending_requests:
                for request in pending_requests:
                    st.write(f"{request['username']} · {request['depositor_name']}")
                    if request.get("note"):
                        st.caption(request["note"])
                    approve_col, reject_col = st.columns(2)
                    if approve_col.button(
                        "승인", key=f"support-approve-{request['id']}",
                        use_container_width=True,
                    ):
                        ok, message = review_support_request(
                            int(st.session_state['user_id']), request["id"], "approved"
                        )
                        (st.success if ok else st.error)(message)
                        if ok:
                            st.rerun()
                    if reject_col.button(
                        "거절", key=f"support-reject-{request['id']}",
                        use_container_width=True,
                    ):
                        ok, message = review_support_request(
                            int(st.session_state['user_id']), request["id"], "rejected"
                        )
                        (st.success if ok else st.error)(message)
                        if ok:
                            st.rerun()
                    st.divider()
            else:
                st.caption("대기 중인 요청이 없습니다.")

            st.divider()
            st.markdown("**공지·팝업 관리**")
            st.caption("상단 안내문 또는 팝업을 선택하고 공개 대상과 기간을 정할 수 있습니다.")
            with st.form("admin-notice-form", clear_on_submit=True):
                notice_form_title = st.text_input(
                    "공지 제목", max_chars=80, key="admin-notice-title"
                )
                notice_form_body = st.text_area(
                    "공지 내용", max_chars=5000, height=100,
                    key="admin-notice-body",
                )
                notice_mode = st.selectbox(
                    "표시 방식",
                    ["banner", "popup"],
                    format_func=lambda value: {
                        "banner": "화면 상단 안내문", "popup": "중요 팝업"
                    }[value],
                )
                notice_audience = st.selectbox(
                    "공개 대상",
                    ["all", "guest", "member", "supporter"],
                    format_func=lambda value: {
                        "all": "모든 방문자", "guest": "비회원",
                        "member": "일반회원", "supporter": "후원회원",
                    }[value],
                )
                notice_start = st.date_input(
                    "공개 시작일",
                    value=datetime.now(timezone(timedelta(hours=9))).date(),
                )
                notice_has_end = st.checkbox("종료일 지정")
                notice_end = st.date_input("공개 종료일", value=notice_start)
                notice_link = st.text_input(
                    "연결 주소 (선택)",
                    placeholder="https:// 로 시작하는 주소",
                )
                notice_image = st.file_uploader(
                    "팝업 대표 이미지 (선택)",
                    type=["jpg", "jpeg", "png", "webp"],
                    help="가로형 이미지를 권장합니다. 최대 2MB입니다.",
                    key="admin-notice-image",
                )
                notice_form_submit = st.form_submit_button(
                    "공지 등록", use_container_width=True
                )
            if notice_form_submit:
                ok, message = create_notice(
                    int(st.session_state['user_id']),
                    notice_form_title,
                    notice_form_body,
                    notice_mode=notice_mode,
                    notice_audience=notice_audience,
                    notice_start_at=notice_start.strftime("%Y-%m-%d"),
                    notice_end_at=(notice_end.strftime("%Y-%m-%d") if notice_has_end else None),
                    notice_link=notice_link,
                    image_bytes=(notice_image.getvalue() if notice_image else None),
                    image_mime=(str(notice_image.type) if notice_image else None),
                )
                (st.success if ok else st.error)(message)
                if ok:
                    st.rerun()

            managed_notices = list_notices(include_hidden=True, limit=8)
            if managed_notices:
                mode_labels = {"banner": "상단 안내", "popup": "팝업"}
                audience_labels = {
                    "all": "전체", "guest": "비회원", "member": "일반회원",
                    "supporter": "후원회원",
                }
                for notice in managed_notices:
                    notice_status = "공개 중" if notice["status"] == "visible" else "숨김"
                    start_label = notice.get("notice_start_at") or "즉시"
                    end_label = notice.get("notice_end_at") or "계속"
                    image_label = " · 사진 포함" if notice.get("has_image") else ""
                    st.caption(
                        f"{notice_status} · {mode_labels.get(notice.get('notice_mode'), '상단 안내')} · "
                        f"{audience_labels.get(notice.get('notice_audience'), '전체')} · "
                        f"{start_label}~{end_label}{image_label} · {notice['title']}"
                    )
                    should_show = notice["status"] != "visible"
                    action_label = "다시 공개" if should_show else "숨기기"
                    if st.button(
                        action_label,
                        key=f"notice-visibility-{notice['id']}",
                        use_container_width=True,
                    ):
                        ok, message = set_notice_visibility(
                            int(st.session_state['user_id']),
                            int(notice["id"]),
                            should_show,
                        )
                        (st.success if ok else st.error)(message)
                        if ok:
                            st.rerun()
            else:
                st.caption("등록된 공지가 없습니다.")

ACCESS_RULES = {
    ROLE_GUEST: {
        "status_label": "비회원 · 추천 3픽",
        "full_analysis": False,
        "match_limit": 3,
    },
    ROLE_MEMBER: {
        "status_label": "일반회원 · 추천 3픽",
        "full_analysis": False,
        "match_limit": 3,
    },
    ROLE_SUPPORTER: {
        "status_label": "후원회원 · 전체 이용",
        "full_analysis": True,
        "match_limit": None,
    },
    ROLE_ADMIN: {
        "status_label": "관리자 · 전체 이용",
        "full_analysis": True,
        "match_limit": None,
    },
}
active_role = st.session_state.get('role', ROLE_GUEST)
access_profile = ACCESS_RULES.get(active_role, ACCESS_RULES[ROLE_GUEST])
has_full_access = bool(access_profile["full_analysis"])
visible_match_limit = access_profile["match_limit"]

# -----------------------------------------------------------------------------
# 5. 레이아웃 뼈대 생성 (메인 콘텐츠)
# -----------------------------------------------------------------------------
dashboard_data = load_dashboard_data()
world_dashboard_data = load_world_dashboard_data()
live_scores_data = load_live_scores()
if isinstance(dashboard_data, dict):
    # 수집기가 새 버전으로 교체되기 전 남아 있는 캐시에도 같은 안전망을 적용한다.
    for collection_name in ("proto", "top3", "toto14"):
        dashboard_data[collection_name] = [
            item for item in dashboard_data.get(collection_name, [])
            if _is_displayable_match_item(item)
        ]
grading_snapshot = dashboard_data.get("grading", {})
prediction_results_data = load_prediction_results(grading_snapshot)

proto_total = len(dashboard_data.get("proto", []))
top3_total = min(3, len(dashboard_data.get("top3", [])))
proto_match_ids = {
    str(item.get("match", {}).get("id", ""))
    for item in dashboard_data.get("proto", [])
    if isinstance(item, dict) and isinstance(item.get("match"), dict)
}
live_total = sum(
    1 for match_id, value in live_scores_data.items()
    if str(match_id) in proto_match_ids
    and isinstance(value, dict)
    and value.get("is_live") is True
    and value.get("final") is not True
)
member_label = access_profile["status_label"]
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

# 사이드바가 접힌 모바일 방문자도 첫 화면에서 바로 가입·로그인할 수 있다.
if not st.session_state.get('logged_in'):
    st.markdown(
        """
        <div class='join-strip'>
            <strong>무료 추천 3픽을 먼저 확인해보세요</strong>
            <span>무료 회원가입 후 채점 기록을 함께 볼 수 있고, 후원회원 전환 시 전체 분석이 열립니다.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    signup_cta, login_cta = st.columns(2)
    if hasattr(st, "popover"):
        with signup_cta.popover("무료 회원가입", use_container_width=True):
            st.caption("간단한 계정을 만들면 채점 기록과 회원 기능을 이어서 이용할 수 있습니다.")
            with st.form("main-signup-form"):
                main_new_user = st.text_input("사용할 아이디", key="main-signup-user")
                main_display_name = st.text_input(
                    "표시 이름 (선택)", key="main-signup-display"
                )
                main_new_password = st.text_input(
                    "비밀번호 (8자 이상)", type="password", key="main-signup-password"
                )
                main_password_check = st.text_input(
                    "비밀번호 확인", type="password", key="main-signup-password-check"
                )
                render_signup_guide()
                main_adult = st.checkbox("만 19세 이상입니다.", key="main-signup-adult")
                main_service = st.checkbox(
                    "서비스 이용 안내에 동의합니다.", key="main-signup-service"
                )
                main_privacy = st.checkbox(
                    "개인정보 수집·이용에 동의합니다.", key="main-signup-privacy"
                )
                main_submit = st.form_submit_button("가입하기", use_container_width=True)
            if main_submit:
                ok, message = submit_registration(
                    main_new_user, main_display_name, main_new_password,
                    main_password_check, main_adult, main_service, main_privacy
                )
                if ok:
                    st.session_state['auth_flash'] = message
                    st.session_state['next_auth_menu'] = "로그인"
                    st.rerun()
                else:
                    st.error(message)

        with login_cta.popover("로그인", use_container_width=True):
            with st.form("main-login-form"):
                main_login_user = st.text_input("아이디", key="main-login-user")
                main_login_password = st.text_input(
                    "비밀번호", type="password", key="main-login-password"
                )
                main_login_submit = st.form_submit_button("로그인", use_container_width=True)
            if main_login_submit:
                user = authenticate_user(main_login_user, main_login_password)
                if user:
                    begin_user_session(user)
                    st.rerun()
                else:
                    st.error("아이디 또는 비밀번호를 확인해주세요.")
    else:
        if signup_cta.button("무료 회원가입", key="main-signup-cta", use_container_width=True):
            st.session_state['next_auth_menu'] = "회원가입"
            st.info("왼쪽 위의 메뉴(>>)를 열어 회원가입을 진행해주세요.")
        if login_cta.button("로그인", key="main-login-cta", use_container_width=True):
            st.session_state['next_auth_menu'] = "로그인"
            st.info("왼쪽 위의 메뉴(>>)를 열어 로그인해주세요.")

# 공개 대상과 기간에 맞는 중요 팝업은 접속 중 한 번만 보여준다.
try:
    popup_notices = list_active_notices(active_role, notice_mode="popup", limit=5)
except Exception:
    popup_notices = []

dismissed_popup_ids = set(st.session_state.get("dismissed_popup_ids", []))
next_popup = next(
    (notice for notice in popup_notices if int(notice["id"]) not in dismissed_popup_ids),
    None,
)
if next_popup and hasattr(st, "dialog") and "open_notice_popup" in globals():
    open_notice_popup(next_popup)

# 공개 대상과 기간에 맞는 최신 상단 안내문을 자동 노출한다.
try:
    visible_notices = list_active_notices(active_role, notice_mode="banner", limit=1)
    latest_notice = visible_notices[0] if visible_notices else None
except Exception:
    latest_notice = None

if latest_notice:
    notice_title = escape(str(latest_notice.get("title", "서비스 공지")))
    notice_preview = escape(str(latest_notice.get("body", ""))[:180])
    st.markdown(
        f"<div class='notice-strip'><strong>📢 {notice_title}</strong><br>{notice_preview}</div>",
        unsafe_allow_html=True,
    )
    notice_link = str(latest_notice.get("notice_link") or "").strip()
    if notice_link:
        st.link_button("공지 자세히 보기", notice_link)

# TOP3, 베트맨 전용, 해외·사설용 경기를 서로 섞지 않는다.
main_tab3, main_tab1, main_tab6, main_tab2, main_tab4, main_tab5 = st.tabs([
    "오늘의 TOP3", "프로토 LIVE", "전체 경기", "승무패 14", "채점 노트", "인증 게시판"
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


def _ui_match_datetime(match_time_str):
    if not match_time_str or match_time_str == "시간 미정":
        return None
    match = re.search(
        r'(?:(\d{2,4})\.)?(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})',
        str(match_time_str),
    )
    if not match:
        return None
    try:
        now = datetime.now(timezone(timedelta(hours=9)))
        year_text, month, day, hour, minute = match.groups()
        month, day, hour, minute = map(int, (month, day, hour, minute))
        if year_text:
            year = int(year_text)
            if year < 100:
                year += 2000
            return datetime(
                year, month, day, hour, minute,
                tzinfo=timezone(timedelta(hours=9)),
            )
        candidates = [
            datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=9)))
            for year in (now.year - 1, now.year, now.year + 1)
        ]
        return min(candidates, key=lambda value: abs((value - now).total_seconds()))
    except (TypeError, ValueError):
        return None


def _toto14_round_has_started(items, now=None):
    """Hide a pools ticket once its first match has kicked off.

    The frozen predictions remain in the dashboard payload and grading DB.  This
    helper controls only the current recommendation screen, so historical picks
    are still available to the grading note and learning pipeline.
    """
    if not isinstance(items, list):
        return False
    current = now or datetime.now(timezone(timedelta(hours=9)))
    kickoffs = []
    for item in items:
        if not isinstance(item, dict):
            continue
        match = item.get("match", {})
        if not isinstance(match, dict):
            continue
        kickoff = _ui_match_datetime(
            item.get("final_match_time")
            or match.get("match_time")
            or match.get("time")
        )
        if kickoff:
            kickoffs.append(kickoff)
    return bool(kickoffs) and current >= min(kickoffs)


def _status_value(source):
    if not isinstance(source, dict):
        return ""
    fixture = source.get("fixture")
    if isinstance(fixture, dict):
        fixture_status = fixture.get("status")
        if isinstance(fixture_status, dict):
            value = fixture_status.get("short") or fixture_status.get("long")
            if value:
                return str(value).strip()
        elif fixture_status:
            return str(fixture_status).strip()
    status = source.get("status")
    if isinstance(status, dict):
        value = status.get("short") or status.get("long") or status.get("name")
        if value:
            return str(value).strip()
    elif status:
        return str(status).strip()
    for key in ("actual_result", "match_status", "state", "status_short"):
        value = source.get(key)
        if value:
            return str(value).strip()
    return ""


def _is_final_status(status):
    normalized = str(status or "").strip().upper()
    return normalized in {
        "FT", "AET", "PEN", "FINISHED", "FINAL", "MATCH FINISHED",
        "AFTER EXTRA TIME", "AFTER PENALTIES",
    }


def _score_pair(value):
    if not isinstance(value, dict):
        return ""
    home = value.get("home")
    away = value.get("away")
    if home is not None and away is not None:
        return f"{home}:{away}"
    return ""


def _score_value(source):
    if not isinstance(source, dict):
        return ""
    for key in ("score", "final_score", "actual_score", "result", "score_text"):
        value = source.get(key)
        if isinstance(value, dict):
            for nested_key in ("fulltime", "full_time", "final"):
                nested = _score_pair(value.get(nested_key))
                if nested:
                    return nested
            pair = _score_pair(value)
            if pair:
                return pair
        elif value is not None:
            text = str(value).strip()
            if text and text not in {"-", "-:-"}:
                return text
    goals = _score_pair(source.get("goals"))
    if goals:
        return goals
    home = source.get("home_score")
    away = source.get("away_score")
    if home is not None and away is not None:
        return f"{home}:{away}"
    return ""


def _localize_event_line(value):
    """신규·기존 사건 기록의 API 영문 표기를 고객용 한글로 통일합니다."""
    line = str(value or "").strip()
    replacements = (
        (r"\bSubstitution\b", "교체"),
        (r"\bSubstitute\b", "교체"),
        (r"\bYellow Card\b", "경고"),
        (r"\bRed Card\b", "퇴장"),
        (r"\bGoal\b", "득점"),
        (r"\bInjury\b", "부상"),
        (r"\s+OUT\b", " 아웃"),
        (r"\s+IN\b", " 투입"),
    )
    for pattern, replacement in replacements:
        line = re.sub(pattern, replacement, line, flags=re.IGNORECASE)
    return line


def _event_html(*sources):
    event_rows = {}
    event_keys = ("events", "recent_events", "timeline", "incidents", "event_history", "match_events")
    wanted = ("goal", "득점", "red", "퇴장", "yellow", "경고", "injury", "부상", "substitution", "substitute", "교체", "var")
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in event_keys:
            raw_events = source.get(key)
            if not isinstance(raw_events, list):
                continue
            for raw_event in raw_events:
                if isinstance(raw_event, dict):
                    direct_text = raw_event.get("text") or raw_event.get("description")
                    if direct_text:
                        line = str(direct_text).strip()
                    else:
                        parts = [
                            raw_event.get("time") or raw_event.get("minute"),
                            raw_event.get("type"), raw_event.get("detail"),
                            raw_event.get("player"), raw_event.get("team"),
                        ]
                        line = " ".join(str(part).strip() for part in parts if part not in (None, ""))
                else:
                    line = str(raw_event or "").strip()
                line = _localize_event_line(line)
                if line and any(word in line.lower() for word in wanted):
                    signature = re.sub(r"[^0-9a-z가-힣]+", "", line.casefold())
                    if signature:
                        event_rows[signature] = line
    if not event_rows:
        return ""
    unique_rows = list(event_rows.values())[-5:]
    content = "<br>".join(escape(row) for row in unique_rows)
    return (
        "<div class='live-event-feed' style='margin:8px 0 2px;padding:8px 10px;"
        "border:1px solid rgba(16,185,129,.28);border-radius:10px;color:#10B981;"
        "font-size:12px;font-weight:800;line-height:1.55;max-width:100%;"
        f"overflow-wrap:anywhere;word-break:keep-all;white-space:normal'>{content}</div>"
    )


def _clean_grading_note(raw_note, prob_ok, ev_ok, has_ev_pick, row=None):
    """Show verified facts and a deterministic reason for every missed pick."""
    raw = str(raw_note or "").strip()
    canned_prefixes = (
        "💡 [퍼펙트 적중] AI의 분석이 경기 흐름과 정확히 일치했습니다! ",
        "💡 [확률픽 적중 / 꿀픽 실패] 안정적인 예측은 통했으나, 역배당의 기적은 일어나지 않았습니다. ",
        "💡 [꿀픽 적중 / 확률픽 실패] AI가 포착한 가치(역배/핸디)가 완벽히 들어맞아 큰 수익을 냈습니다! ",
        "💡 [전면 실패 오답노트] AI의 예상과 실제 경기 양상이 크게 엇갈렸습니다. 딥러닝 보정 데이터로 활용됩니다. ",
    )
    for prefix in canned_prefixes:
        if raw.startswith(prefix):
            raw = raw[len(prefix):].lstrip()
            break

    # 새 형식의 요약도 현재 DB 적중값으로 다시 만들기 때문에 중복하지 않습니다.
    raw = re.sub(r"^\[채점 결과\][^\n]*(?:\n|$)", "", raw).strip()
    # 구버전의 긴 영문 타임라인은 아래 한글 주요 사건 기록과 중복되므로 제거합니다.
    raw = re.sub(
        r"\s*\|\s*⏱️\s*매치 타임라인:.*?(?=\n\n🎬|\Z)",
        "",
        raw,
        flags=re.DOTALL,
    ).strip()

    main_text, marker, event_text = raw.partition("🎬")
    # The old provider-missing sentence was identical on every match and added
    # no learning signal.  The structured limitation below replaces it.
    main_text = re.sub(
        r"^\[공식 경기 통계\]\s*(?:제공된|조회된|조회되지 않아).*?(?:\n|$)",
        "",
        main_text,
        flags=re.MULTILINE,
    ).strip()
    event_lines = []
    signatures = set()
    if marker:
        for line in event_text.splitlines():
            line = _localize_event_line(line.strip())
            if not line or "사건 기록" in line:
                continue
            signature = re.sub(r"[^0-9a-z가-힣]+", "", line.casefold())
            if signature and signature not in signatures:
                signatures.add(signature)
                event_lines.append(line)
    event_lines = event_lines[-8:]

    result_parts = [f"확률픽 {'적중' if prob_ok else '미적중'}"]
    result_parts.append(
        f"배당형 대안픽 {'적중' if ev_ok else '미적중'}"
        if has_ev_pick else "배당형 대안픽 미선정"
    )
    sections = [f"[채점 결과] {' · '.join(result_parts)}."]
    if main_text.strip():
        sections.append(main_text.strip())

    # New rows carry JSON for the future learning robot.  Old rows are rebuilt
    # from their frozen picks, final score, and any facts already in the note.
    row = row if isinstance(row, dict) else {}
    payload = parse_postmortem_json(row.get("postmortem_json"))
    if payload is None and (not prob_ok or (has_ev_pick and not ev_ok)):
        score_match = re.match(
            r"^\s*(\d+)\s*:\s*(\d+)\s*$",
            str(row.get("actual_score") or ""),
        )
        if score_match:
            payload = build_postmortem(
                home_team=row.get("home_team", ""),
                away_team=row.get("away_team", ""),
                prob_pick=row.get("prob_pick", ""),
                ev_pick=row.get("ev_pick", ""),
                goals_h=int(score_match.group(1)),
                goals_a=int(score_match.group(2)),
                is_correct_prob=int(bool(prob_ok)),
                is_correct_ev=int(bool(ev_ok)),
                has_ev_pick=has_ev_pick,
                official_stats=stats_from_note(raw_note),
                event_timeline=event_lines,
            )
    if payload and "[미적중 원인 · 확인된 결과]" not in main_text:
        review = postmortem_text(payload)
        if review:
            sections.append(review)
    if event_lines:
        sections.append("[주요 사건 기록 · 최대 8건]\n" + "\n".join(event_lines))
    return escape("\n\n".join(sections)).replace("\n", "<br>")


def _pick_categories(item):
    if not isinstance(item, dict):
        return {}
    for key in ("pick_categories", "category_picks", "stored_pick_categories", "pick_category"):
        value = item.get(key)
        if isinstance(value, dict):
            return value
    categories = {}
    for category, keys in {
        "high_probability": ("high_probability_pick", "probability_pick"),
        "honey": ("honey_pick", "ai_honey_pick"),
        "vip_underdog": ("vip_underdog_pick", "vip_pick"),
    }.items():
        for key in keys:
            value = item.get(key)
            if isinstance(value, dict):
                categories[category] = value
                break
    return categories


def _detail_html(item):
    if not isinstance(item, dict):
        return ""
    detail = None
    for key in ("detailed_report", "detail_report", "analysis_detail", "analysis_rationale", "rationale", "long_reason", "report_detail"):
        if item.get(key):
            detail = item.get(key)
            break
    if detail is None:
        return ""
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, ensure_ascii=False, indent=2)
    safe_detail = escape(str(detail)).replace("\n", "<br>")
    return (
        "<details class='analysis-details' style='margin-top:10px;max-width:100%;"
        "overflow:hidden'><summary style='cursor:pointer;color:#94A3B8;font-weight:800'>"
        "상세 분석 근거 보기</summary><div style='margin-top:8px;padding:10px 12px;"
        "border-radius:10px;background:rgba(15,23,42,.55);line-height:1.65;"
        f"overflow-wrap:anywhere;word-break:keep-all;white-space:normal'>{safe_detail}</div></details>"
    )


def _analysis_data_quality_html(item):
    """Keep evidence coverage separate from adjusted analysis confidence."""
    if not isinstance(item, dict):
        return ""
    try:
        coverage = max(0.0, min(1.0, float(item.get("data_coverage") or 0)))
    except (TypeError, ValueError):
        coverage = 0.0
    if coverage <= 0:
        return ""
    percent = int(round(coverage * 100))
    try:
        confidence = max(
            0.0, min(1.0, float(item.get("analysis_confidence") or 0))
        )
    except (TypeError, ValueError):
        confidence = 0.0
    confidence_percent = int(round(confidence * 100))
    coverage_color = (
        "#10B981" if coverage >= 0.8 else
        ("#38BDF8" if coverage >= 0.65 else "#F59E0B")
    )
    confidence_color = (
        "#10B981" if confidence >= 0.8 else
        ("#38BDF8" if confidence >= 0.65 else "#F59E0B")
    )
    coverage_badge = (
        "<span style='display:inline-block;margin-left:8px;padding:3px 7px;"
        f"border:1px solid {coverage_color};border-radius:999px;color:{coverage_color};"
        f"font-size:10px;letter-spacing:0;'>데이터 확보율 {percent}%</span>"
    )
    confidence_badge = (
        "<span style='display:inline-block;margin-left:6px;padding:3px 7px;"
        f"border:1px solid {confidence_color};border-radius:999px;color:{confidence_color};"
        f"font-size:10px;letter-spacing:0;'>최종 신뢰도 {confidence_percent}%</span>"
        if confidence > 0 else ""
    )
    lineup_badge = (
        "<span style='display:inline-block;margin-left:6px;padding:3px 7px;"
        "border:1px solid #A78BFA;border-radius:999px;color:#A78BFA;"
        "font-size:10px;letter-spacing:0;'>선발 미확인 감점</span>"
        if not bool(item.get("lineup_confirmed")) else ""
    )
    return coverage_badge + confidence_badge + lineup_badge


def _has_display_odds(*values):
    """실제로 화면에 표시할 수 있는 십진수 배당인지 확인합니다.

    배당 출처 표시가 누락된 예전 대시보드 데이터라도 0, 0.0, '-'를
    실제 배당으로 잘못 보여주지 않도록 값 자체를 한 번 더 검증합니다.
    """
    if not values:
        return False
    try:
        return all(float(value) > 1.0 for value in values)
    except (TypeError, ValueError):
        return False


# 🔥 라이브 경기 판별 함수
def check_is_live(item):
    m = item.get('match', {})
    match_id_str = str(m.get('id', ''))
    live_info = live_scores_data.get(match_id_str, {})
    db_result = prediction_results_data.get(match_id_str, {})
    explicit_status = (
        _status_value(db_result) or _status_value(live_info)
        or _status_value(item) or _status_value(m)
    )
    if _is_final_status(explicit_status):
        return False
    if explicit_status.upper() in {"LIVE", "1H", "HT", "2H", "ET", "BT", "P", "INT", "BREAK"}:
        return True
    if live_info.get("is_live") is True:
        return True
    time_status, _ = get_match_status(
        item.get("final_match_time", m.get("match_time", "")),
        m.get("deadline_time", "23:00"),
    )
    return time_status == "LIVE"


def _proto_terminal_datetime(item):
    match = item.get("match", {}) if isinstance(item, dict) else {}
    live_info = live_scores_data.get(str(match.get("id", "")), {})
    terminal_at = live_info.get("terminal_at") if isinstance(live_info, dict) else None
    if terminal_at:
        try:
            parsed = datetime.fromisoformat(str(terminal_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone(timedelta(hours=9)))
        except (TypeError, ValueError):
            pass
    kickoff = _ui_match_datetime(
        item.get("final_match_time") or match.get("match_time")
    )
    return kickoff + timedelta(hours=2) if kickoff else None


def _proto_is_recent_or_active(item):
    if check_is_live(item):
        return True
    match = item.get("match", {}) if isinstance(item, dict) else {}
    match_id = str(match.get("id", ""))
    live_info = live_scores_data.get(match_id, {})
    db_result = prediction_results_data.get(match_id, {})
    explicit_status = (
        _status_value(db_result) or _status_value(live_info)
        or _status_value(item) or _status_value(match)
    )
    time_status, _ = get_match_status(
        item.get("final_match_time", match.get("match_time", "")),
        match.get("deadline_time", "23:00"),
    )
    is_final = _is_final_status(explicit_status) or time_status == "FINISHED"
    # 프로토 LIVE는 다음 픽을 보는 화면이므로 종료 확정 경기는 즉시 숨기고
    # 결과와 사건 기록은 채점 노트에서만 보존합니다.
    return not is_final


def _proto_live_sort_key(item):
    match = item.get("match", {}) if isinstance(item, dict) else {}
    kickoff = _ui_match_datetime(
        item.get("final_match_time") or match.get("match_time")
    )
    kickoff_ts = kickoff.timestamp() if kickoff else float("inf")
    if check_is_live(item):
        return (0, kickoff_ts)
    explicit_status = _status_value(
        live_scores_data.get(str(match.get("id", "")), {})
    ) or _status_value(item) or _status_value(match)
    time_status, _ = get_match_status(
        item.get("final_match_time", match.get("match_time", "")),
        match.get("deadline_time", "23:00"),
    )
    if not (_is_final_status(explicit_status) or time_status == "FINISHED"):
        return (1, kickoff_ts)
    terminal_at = _proto_terminal_datetime(item)
    terminal_ts = terminal_at.timestamp() if terminal_at else 0
    return (2, -terminal_ts)

def render_logo_html(logo_url):
    safe_logo = escape(str(logo_url or DEFAULT_TEAM_LOGO), quote=True)
    safe_fallback = escape(DEFAULT_TEAM_LOGO, quote=True)
    return (
        f'<img src="{safe_logo}" class="team-logo" '
        f'onerror="this.onerror=null;this.src=\'{safe_fallback}\';">'
    )


def _human_pick_label(raw_pick, home_team=""):
    """Turn terse Betman handicap notation into an unambiguous sentence."""
    raw = str(raw_pick or "").strip()
    matched = re.match(
        r"^\[\s*([+-]?\d+(?:\.\d+)?)\s*\]\s*(.*?)\s*핸디(승|무|패)$",
        raw,
    )
    if not matched:
        return raw
    handicap, team_name, result = matched.groups()
    team_name = team_name.strip() or str(home_team or "홈팀").strip()
    try:
        handicap = f"{float(handicap):+.1f}"
    except (TypeError, ValueError):
        pass
    return f"{team_name} {handicap} 적용 후 {result}"


def _top3_strategy_html(item):
    """Show the decision and risk summary without making users open the long report."""
    categories = _pick_categories(item)
    high = categories.get("high_probability") if isinstance(categories, dict) else None
    honey = categories.get("honey") if isinstance(categories, dict) else None
    if not isinstance(high, dict):
        return ""

    match = item.get("match", {}) if isinstance(item, dict) else {}
    home_team = str(match.get("home") or "")
    high_text = escape(_human_pick_label(high.get("raw_pick"), home_team))
    high_probability = float(high.get("prob") or 0) * 100
    fair_probability = high.get("fair_prob")
    confidence = float(item.get("analysis_confidence") or 0) * 100
    interval = high.get("probability_interval") or {}
    low = float(interval.get("low") or 0) * 100
    high_bound = float(interval.get("high") or 0) * 100

    if confidence < 60:
        risk_text = "자료 신뢰도가 낮아 단일 방향 확정보다 보수적으로 보는 경기입니다."
    elif high_bound and high_bound - low >= 18:
        risk_text = "예상 오차범위가 넓어 확률 숫자만 보고 강하게 선택하면 안 됩니다."
    else:
        risk_text = "우선 방향은 뚜렷하지만 적중을 보장하는 수치는 아닙니다."

    fair_text = (
        f"시장 공정확률 {float(fair_probability) * 100:.1f}%와 비교"
        if fair_probability is not None else "시장 비교값 없음"
    )
    if isinstance(honey, dict):
        honey_text = escape(_human_pick_label(honey.get("raw_pick"), home_team))
        if str(honey.get("raw_pick") or "") == str(high.get("raw_pick") or ""):
            support_text = f"가치 분석도 같은 방향인 {honey_text}을 가리킵니다."
        else:
            support_text = f"함께 적중 가능한 다른 시장 대안은 {honey_text}입니다."
    else:
        support_text = "함께 적중 가능한 별도 시장 대안을 계산할 수 없습니다."

    return (
        "<div class='top3-strategy' style='margin-top:14px;padding:13px 15px;"
        "border:1px solid rgba(0,242,254,.18);border-radius:10px;"
        "background:rgba(0,242,254,.035);line-height:1.65;color:#CBD5E1;'>"
        "<div style='font-weight:900;color:#00F2FE;margin-bottom:5px;'>추천 전략 한눈에 보기</div>"
        f"<div><b style='color:#F8FAFC;'>우선 방향</b> · {high_text} "
        f"(모델 {high_probability:.1f}%, {fair_text})</div>"
        f"<div><b style='color:#F8FAFC;'>가치 확인</b> · {support_text}</div>"
        f"<div><b style='color:#F8FAFC;'>주의점</b> · {risk_text} "
        f"데이터 신뢰도 {confidence:.1f}%.</div>"
        "</div>"
    )


def generate_pred_boxes(picks, is_top3_tab=False, pick_categories=None, grading=None, home_team=""):
    """확률픽, 배당형 대안픽, 대안픽의 VIP 승격 상태를 세 칸에 표시한다."""
    picks = picks or []
    categories = {
        "high_probability": None,
        "honey": None,
        "vip_underdog": None,
    }
    if isinstance(pick_categories, dict):
        for key in categories:
            value = pick_categories.get(key)
            categories[key] = value if isinstance(value, dict) else None

    for pick in picks:
        key = pick.get("category_key")
        if key in categories and categories[key] is None:
            categories[key] = pick

    # 이전 버전 데이터는 확률 높은 픽만 복원합니다. 배당형 대안픽과 VIP 역배는
    # 엄격한 신규 기준을 거치지 않았으므로 임의로 만들어 표시하지 않습니다.
    if categories["high_probability"] is None and picks:
        categories["high_probability"] = max(
            picks, key=lambda item: float(item.get("prob", 0) or 0)
        )

    slot_specs = [
        ("high_probability", "📈 확률 높은 픽", "분석 가능한 선택지가 없습니다.", "#00F2FE"),
        ("honey", "🍯 배당형 대안픽", "분석 가능한 별도 대안이 없습니다.", "#F59E0B"),
        ("vip_underdog", "💎 VIP 검증 등급", "대안픽 중 엄격 기준 통과 없음", "#FFD54A"),
    ]
    html = ""
    for key, label, empty_text, color in slot_specs:
        pick = categories[key]
        if not pick:
            html += (
                "<div class='pred-box' style='border-style:dashed;opacity:.72;'>"
                f"<div class='pred-label' style='color:{color};'>{label}</div>"
                f"<span class='pred-value' style='color:#94A3B8;'>{empty_text}</span>"
                "<span class='pred-prob' style='background:#1E293B;color:#94A3B8;'>기준 미달</span>"
                "</div>"
            )
            continue

        prob_pct = round(float(pick.get("prob", 0) or 0) * 100, 1)
        raw_pick = escape(_human_pick_label(pick.get("raw_pick", ""), home_team))
        pick_label = escape(str(pick.get("label", "")))
        honey_tier = str(pick.get("value_pick_tier") or "")
        detail = {
            "high_probability": "항상 표시",
            "honey": (
                "보수적 가치 기준 통과"
                if honey_tier == "qualified"
                else (
                    "항상 제공 · 배당 미확인 참고픽"
                    if honey_tier == "unpriced_reference"
                    else "항상 제공 · 참고 등급"
                )
            ),
            "vip_underdog": "별도 픽 아님 · 대안픽 엄격 검증 통과",
        }[key]
        meta_parts = [detail]
        if pick.get("fair_prob") is not None:
            meta_parts.append(f"공정확률 {float(pick['fair_prob']) * 100:.1f}%")
        if key == "honey" and pick.get("fair_prob") is not None:
            meta_parts.append(f"보수적 가치차 {float(pick.get('robust_edge', 0) or 0) * 100:+.1f}%p")
        if key == "vip_underdog" and pick.get("support_signals"):
            meta_parts.append(f"독립근거 {len(pick['support_signals'])}개")
        detail = " · ".join(meta_parts)
        grade_html = ""
        if isinstance(grading, dict) and grading.get("actual_result") == "FINISHED":
            grade_value = None
            if str(pick.get("raw_pick") or "") == str(grading.get("prob_pick") or ""):
                grade_value = int(grading.get("is_correct_prob") or 0)
            elif str(pick.get("raw_pick") or "") == str(grading.get("ev_pick") or ""):
                grade_value = int(grading.get("is_correct_ev") or 0)
            if grade_value is not None:
                grade_label = "적중" if grade_value == 1 else "미적중"
                grade_color = "#10B981" if grade_value == 1 else "#EF4444"
                grade_html = (
                    f"<span style='display:inline-block;margin-top:7px;padding:3px 8px;"
                    f"border:1px solid {grade_color};border-radius:999px;color:{grade_color};"
                    f"font-size:11px;font-weight:900;'>채점 {grade_label}</span>"
                )
        bg_style = (
            "background:rgba(0,242,254,.05);border-color:#00F2FE;"
            if key == "high_probability"
            else ""
        )
        html += (
            f"<div class='pred-box' style='{bg_style}'>"
            f"<div class='pred-label' style='color:{color};'>{label}</div>"
            f"<span class='pred-value'>{raw_pick}</span>"
            f"<span style='display:block;color:#64748B;font-size:11px;margin-top:5px;'>{pick_label} · {detail}</span>"
            f"{grade_html}"
            f"<span class='pred-prob'>{prob_pct}%</span>"
            "</div>"
        )
    return html

# -----------------------------------------------------------------------------
# [TAB 1] 프로토 LIVE
# -----------------------------------------------------------------------------
with main_tab1:
    sub_soccer, sub_baseball, sub_basketball = st.tabs(["축구", "야구", "농구"])
    with sub_soccer:
        st.markdown("""
        <div class='section-intro'>
            <div><h2>프로토 LIVE</h2><p>베트맨에 등록된 경기만 베트맨 사이트의 순서 그대로 확인합니다.</p></div>
        </div>
        <div style='background:rgba(25,230,242,.055); border:1px solid rgba(25,230,242,.20); color:#BEEEF2; padding:12px 14px; border-radius:12px; font-size:12px; font-weight:700; margin-bottom:20px;'>
            베트맨 이용자가 경기 순서를 그대로 따라가며 픽하기 위한 전용 화면입니다. 무료 이용자는 앞의 3경기까지 볼 수 있습니다.
        </div>
        """, unsafe_allow_html=True)
        
        # 진행 중 경기 → 예정 경기만 표시하며 종료 확정 경기는 채점 노트로 이동한다.
        proto_list = [
            item for item in dashboard_data.get("proto", [])
            if _proto_is_recent_or_active(item)
        ]
        proto_list.sort(key=_proto_live_sort_key)
        if proto_list:
            all_leagues = list(dict.fromkeys(m.get('league', '기타') for m in proto_list))
            selected_league = st.selectbox(
                "🏆 리그 필터링",
                ["전체 리그 보기"] + all_leagues,
                key="proto-league-filter",
            )
            st.caption("LIVE 우선 · 다음 예정 경기순 · 종료 확정 즉시 채점 노트로 이동")
                 
            st.markdown("<hr style='border-color: #1E293B; margin-top: 5px; margin-bottom: 25px;'>", unsafe_allow_html=True)
            
            # 필터링
            if selected_league != "전체 리그 보기": 
                proto_list = [m for m in proto_list if m.get('league') == selected_league]
                 
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
                
                displayed_count += 1

                # 🔥 페이월(Paywall) 로직: 4번째 경기부터 잠금
                if (
                    visible_match_limit is not None
                    and displayed_count > visible_match_limit
                    and not has_full_access
                ):
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

                live_info = live_scores_data.get(match_id_str, {})
                db_result = prediction_results_data.get(match_id_str, {})
                explicit_status = _status_value(live_info) or _status_value(db_result) or _status_value(item) or _status_value(m)
                score_text = _score_value(live_info) or _score_value(db_result) or _score_value(item) or _score_value(m)
                event_html = _event_html(live_info, item, m)
                is_final_now = match_status == "FINISHED" or _is_final_status(explicit_status)
                if is_live_now:
                    visible_score = escape(score_text) if score_text else "수집 대기"
                    time_display = (
                        f"<span class='live-score'>{visible_score}</span>"
                        "<span class='deadline-closed' style='background:rgba(239, 68, 68, 0.1);"
                        "border-color:#EF4444;color:#EF4444;'>🔴 LIVE</span>"
                    )
                elif is_final_now:
                    if score_text:
                        time_display = (
                            f"<span class='live-score'>{escape(score_text)}</span>"
                            "<span class='deadline-closed'>종료</span>"
                        )
                    else:
                        time_display = f"<span class='match-time-text'>{item.get('final_match_time', '')}</span><span class='deadline-closed'>결과 확인 중</span>"
                else:
                    badge = f"<span class='deadline-closed'>픽 마감</span>" if is_closed else f"<span class='deadline-open'>{raw_deadline}</span>"
                    time_display = f"<span class='match-time-text'>{item.get('final_match_time', '')}</span>{badge}"
                
                dynamic_pred_boxes = generate_pred_boxes(
                    item.get('ev_sorted_picks', []),
                    is_top3_tab=False,
                    pick_categories=_pick_categories(item),
                    grading=db_result if is_final_now else None,
                    home_team=m.get('home', ''),
                )

                odds_source = str(
                    item.get("odds_source") or m.get("odds_source") or "betman"
                )
                has_three_way_odds = _has_display_odds(
                    m.get("odd_h"), m.get("odd_d"), m.get("odd_a")
                )
                has_handicap_odds = _has_display_odds(
                    m.get("handi_h"), m.get("handi_d"), m.get("handi_a")
                )
                has_totals_odds = _has_display_odds(
                    m.get("uo_under"), m.get("uo_over")
                )

                # 출처 표시가 누락된 데이터도 실제 1X2 배당이 없으면
                # 0.0 숫자 대신 배당 대기 안내를 보여줍니다.
                if odds_source == "model_only" or not has_three_way_odds:
                    odds_bar_html = (
                        "<div class='odd-bar'><span class='odd-item'>"
                        "📊 승무패 배당 대기 · 팀 데이터 모델 선픽 제공 중"
                        "</span></div>"
                    )
                elif odds_source == "overseas_fallback":
                    odds_bar_html = (
                        "<div class='odd-bar'>"
                        f"<span class='odd-item'>🌍 해외 임시배당 · 승 <span class='odd-val'>{m.get('odd_h','-')}</span> | 무 <span class='odd-val'>{m.get('odd_d','-')}</span> | 패 <span class='odd-val'>{m.get('odd_a','-')}</span></span>"
                        "<span class='odd-item'>핸디캡·언오버는 베트맨 배당 대기</span>"
                        "</div>"
                    )
                else:
                    handicap_html = (
                        f"핸디캡 <span class='odd-val'>{m.get('handi_h')} / {m.get('handi_d')} / {m.get('handi_a')}</span>"
                        if has_handicap_odds else "핸디캡 배당 대기"
                    )
                    totals_html = (
                        f"언오버 <span class='odd-val'>{m.get('uo_under')} / {m.get('uo_over')}</span>"
                        if has_totals_odds else "언오버 배당 대기"
                    )
                    odds_bar_html = (
                        "<div class='odd-bar'>"
                        f"<span class='odd-item'>승 <span class='odd-val'>{m.get('odd_h','-')}</span> | 무 <span class='odd-val'>{m.get('odd_d','-')}</span> | 패 <span class='odd-val'>{m.get('odd_a','-')}</span></span>"
                        f"<span class='odd-item'>{handicap_html}</span>"
                        f"<span class='odd-item'>{totals_html}</span>"
                        "</div>"
                    )

                upset_html = ""
                if item.get('upset_warning'):
                    upset_html = f"<div style='background-color: #3b1c1c; border-left: 4px solid #ff4d4d; padding: 12px 15px; font-size: 13px; color:#ffcccc; border-radius:4px; margin-bottom:15px; line-height:1.6;'><span style='background-color:#FFD700; color:#000; font-weight:900; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>후원회원 전용</span>🚨 <b>슈퍼 역배 주의보 포착!</b><br>{item.get('upset_reason', '역배 전조 증상이 포착되었습니다. 고배당 스나이핑 찬스!')}</div>"
                
                html_code = (
                    f"<div class='match-card'>"
                    f"<div class='league-title'>{m.get('league','축구')}{_analysis_data_quality_html(item)}</div>"
                    f"<div class='vs-row'>"
                    f"<div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{item.get('home_form','')}</div>{item.get('h_rank_html','')}{item.get('h_inj_html','')}{item.get('h_rest_html','')}</div>{logo_h_tag}</div>"
                    f"<div class='center-time-box'>{time_display}</div>"
                    f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{item.get('away_form','')}</div>{item.get('a_rank_html','')}{item.get('a_inj_html','')}{item.get('a_rest_html','')}</div></div>"
                    f"</div>"
                    f"{event_html}"
                    f"<div class='ai-story'>{item.get('story','')}</div>"
                    f"{_detail_html(item)}"
                    f"{upset_html}"
                    f"{odds_bar_html}"
                    f"<div class='pred-grid'>{dynamic_pred_boxes}</div>"
                    f"</div>"
                )
                st.markdown(html_code, unsafe_allow_html=True)
            if displayed_count == 0: st.info("조건에 맞는 경기가 없거나 모두 종료되었습니다.")
        else: st.info("현재 분석 중입니다. 백그라운드 데이터 수집이 완료되면 화면이 표시됩니다.")
    with sub_baseball: st.info("야구 분석 데이터 준비 중입니다.")
    with sub_basketball: st.info("농구 분석 데이터 준비 중입니다.")

# -----------------------------------------------------------------------------
# [TAB 6] 전체 경기 · 해외/사설 이용자용 세계 경기
# -----------------------------------------------------------------------------
with main_tab6:
    st.markdown("""
    <div class='section-intro'>
        <div>
            <h2>전체 경기</h2>
            <p>해외·사설 사이트 이용자를 위한 전 세계 축구 경기 분석 화면입니다.</p>
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.info("프로토 LIVE와 분리된 메뉴입니다. 새 리그는 그림자 채점으로 검증한 뒤 공개됩니다.")

    legacy_world_matches = (
        dashboard_data.get("all_matches")
        or dashboard_data.get("global_matches")
        or dashboard_data.get("world_matches")
        or []
    )
    raw_world_matches = world_dashboard_data.get("matches", []) or legacy_world_matches
    world_source_meta = world_dashboard_data.get("source_meta", {}) or {}
    is_world_admin = st.session_state.get('role') == ROLE_ADMIN
    if is_world_admin:
        world_matches = [
            item for item in raw_world_matches
            if str(item.get("visibility_status") or "SHADOW").upper() != "QUARANTINED"
        ]
        rejected_summary = world_dashboard_data.get("rejected_summary", []) or []
        rejected_text = " · ".join(
            f"{entry.get('reason', entry.get('reason_code', '제외'))} {int(entry.get('count') or 0)}"
            for entry in rejected_summary
            if int(entry.get("count") or 0) > 0
        )
        st.caption(
            "관리자 세계경기 관제 · "
            f"원본 {int(world_source_meta.get('raw_fixture_count') or 0)}경기 · "
            f"그림자 후보 {int(world_source_meta.get('eligible_shadow_count') or len(world_matches))}경기 · "
            f"공개 {int(world_source_meta.get('public_count') or 0)}경기 · "
            f"WORLD API {int((world_source_meta.get('api_usage') or {}).get('world_calls') or 0)}회"
        )
        if rejected_text:
            with st.expander("세계경기 제외 사유 확인"):
                st.write(rejected_text)
    else:
        world_matches = [
            item for item in raw_world_matches
            if str(item.get("visibility_status") or "").upper() == "PUBLIC"
        ]
    if not world_matches:
        shadow_count = int(world_source_meta.get("eligible_shadow_count") or 0)
        readiness_text = (
            f"현재 {shadow_count}경기를 비공개 그림자 검증 중입니다."
            if shadow_count
            else "오늘 분석 가능한 세계 경기 일정을 확인 중입니다."
        )
        st.markdown("""
        <div class='match-card' style='text-align:center; padding:42px 20px;'>
            <h3 style='margin-bottom:12px;'>🌍 전 세계 경기 검증 중</h3>
            <p style='color:#94A3B8; line-height:1.7;'>현재 베트맨 경기와 섞지 않고 별도로 수집·채점합니다.<br>
            검증 기준을 통과한 리그부터 이 메뉴에 공개됩니다.</p>
        </div>
        """, unsafe_allow_html=True)
        st.caption(readiness_text)
    else:
        world_limit = None if has_full_access else 3
        for world_index, world_item in enumerate(world_matches):
            if world_limit is not None and world_index >= world_limit:
                st.warning("4번째 세계 경기부터는 후원회원에게 제공됩니다.")
                break
            world_match = world_item.get("match", world_item)
            world_league = escape(str(world_match.get("league", "세계 축구")))
            world_home = escape(str(world_match.get("home", "홈팀")))
            world_away = escape(str(world_match.get("away", "원정팀")))
            world_time = escape(str(
                world_item.get("final_match_time")
                or world_match.get("match_time")
                or world_match.get("date")
                or "시간 확인 중"
            ))
            visibility_status = str(world_item.get("visibility_status") or "PUBLIC").upper()
            analysis_status = str(world_item.get("analysis_status") or "").upper()
            world_status_html = ""
            if is_world_admin and visibility_status == "SHADOW":
                status_label = (
                    "일정 수집 완료 · 그림자 분석 대기"
                    if analysis_status == "PENDING_SHADOW_ANALYSIS"
                    else analysis_status.replace("_", " ")
                )
                world_status_html = (
                    "<div style='margin-top:14px; color:#F59E0B; font-weight:800;'>"
                    f"🧪 SHADOW · {escape(status_label)}</div>"
                )
            st.markdown(
                f"""
                <div class='match-card'>
                    <div class='league-title'>{world_league}</div>
                    <div class='vs-row'>
                        <div class='team-box home'><div class='team-name-text'>{world_home}</div></div>
                        <div class='center-time-box'><span class='match-time-text'>{world_time}</span></div>
                        <div class='team-box away'><div class='team-name-text'>{world_away}</div></div>
                    </div>
                    {world_status_html}
                </div>
                """,
                unsafe_allow_html=True,
            )

# -----------------------------------------------------------------------------
# [TAB 2] 승무패 14경기
# -----------------------------------------------------------------------------
with main_tab2:
    st.markdown("<p style='color:#64748B; font-weight:700; margin-bottom:20px;'>승무패 14폴더 AI 확률 분포 (복수 마킹 참고용)</p>", unsafe_allow_html=True)
    stored_toto14_list = dashboard_data.get("toto14", [])
    toto14_round_closed = _toto14_round_has_started(stored_toto14_list)
    # 화면에서만 지난 회차를 숨긴다. 동결 예측과 채점 자료는 원본에 보존된다.
    toto14_list = [] if toto14_round_closed else stored_toto14_list
    
    if toto14_list:
        total_combinations = dashboard_data.get("toto14_meta", {}).get("total_combinations", 1)
        single_pick_count = dashboard_data.get("toto14_meta", {}).get("single_pick_count", 0)
        double_pick_count = dashboard_data.get("toto14_meta", {}).get("double_pick_count", 0)
        total_price = dashboard_data.get("toto14_meta", {}).get("budget", total_combinations * 1000)
        max_budget = dashboard_data.get("toto14_meta", {}).get("max_budget", 8000)
        cap_exceeded_by_frozen = dashboard_data.get("toto14_meta", {}).get("cost_cap_exceeded_by_frozen", False)
        
        summary_html = f"<div style='background: #111827; border: 1px solid #1E293B; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 24px;'><span style='color: #94A3B8; font-size: 14px; font-weight: 700; display: block; margin-bottom: 5px;'>AI 승무패 14경기 풀-스탯 분석 결과 · 소액 상한 {max_budget:,}원</span><span style='color: #F8FAFC; font-size: 16px; font-weight: 700; display: block; margin-bottom: 8px;'>단통 <span style='color:#10B981;'>{single_pick_count}</span>경기 + 투마킹 <span style='color:#EF4444;'>{double_pick_count}</span>경기</span><span style='color: #F8FAFC; font-size: 24px; font-weight: 900; display: block;'>최종 <span style='color: #00F2FE;'>{total_combinations}</span> 조합 / 예상 구매 금액: <span style='color: #10B981;'>{total_price:,}</span> 원</span></div>"
        st.markdown(summary_html, unsafe_allow_html=True)
        if cap_exceeded_by_frozen:
            st.warning("이미 경기 직전 동결된 조합은 과거 기록 보호를 위해 바꾸지 않습니다. 새 회차부터 8,000원 상한이 적용됩니다.")

        toto_displayed = 0
        toto_paywall_shown = False

        for idx, item in enumerate(toto14_list, 1):
            toto_displayed += 1

            if (
                visible_match_limit is not None
                and toto_displayed > visible_match_limit
                and not has_full_access
            ):
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
    elif toto14_round_closed:
        st.info("이전 승무패14 회차는 첫 경기 시작과 함께 마감되어 추천 화면에서 숨겼습니다. 예측과 결과는 채점 노트에 그대로 보존됩니다. 새 회차가 수집되면 자동으로 표시됩니다.")
    else:
        st.info("현재 진행 중인 승무패 14경기 데이터가 없습니다.")

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
            dynamic_top3_boxes = generate_pred_boxes(
                item.get('ev_sorted_picks', []),
                is_top3_tab=True,
                pick_categories=_pick_categories(item),
                home_team=m.get('home', ''),
            )
            top3_odds_source = str(
                item.get("odds_source") or m.get("odds_source") or "betman"
            )
            top3_source_label = {
                "overseas_fallback": "해외배당 임시 추천 픽",
                "model_only": "팀 데이터 모델 선픽",
            }.get(top3_odds_source, "최고 가치 추천 픽")
            html_code = (
                f"<div class='match-card top3-glow'>"
                f"<div class='league-title' style='color:#00F2FE;'># {displayed_top3} {top3_source_label} • {m.get('league','')}{_analysis_data_quality_html(item)}</div>"
                f"<div class='vs-row'><div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{item.get('home_form','')}</div>{item.get('h_rank_html','')}</div>{logo_h_tag}</div>"
                f"<div class='center-time-box'><span class='match-time-text' style='color:#00F2FE;'>{item.get('final_match_time', '')}</span></div>"
                f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{item.get('away_form','')}</div>{item.get('a_rank_html','')}</div></div></div>"
                f"<div class='pred-grid' style='margin-top:20px;'>{dynamic_top3_boxes}</div>"
                f"{_top3_strategy_html(item)}"
                f"{_detail_html(item)}"
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
            finished_rows = list(grading_snapshot.get("finished", []) or [])
            pending_rows = list(grading_snapshot.get("pending", []) or [])
            if not finished_rows and not pending_rows:
                conn = sqlite3.connect("ai_predictions.db")
                conn.row_factory = sqlite3.Row
                columns = [col[1] for col in conn.execute("PRAGMA table_info(predictions)")]
                if 'ev_pick' not in columns:
                    conn.close()
                    return None
                finished_rows = [
                    dict(row) for row in conn.execute(
                        "SELECT * FROM predictions WHERE actual_result = 'FINISHED'"
                    ).fetchall()
                ]
                pending_rows = [
                    dict(row) for row in conn.execute(
                        "SELECT * FROM predictions WHERE actual_result = 'PENDING' AND is_toto14 = 0"
                    ).fetchall()
                ]
                conn.close()

            def newest_first(row):
                parsed = _ui_match_datetime(row.get("match_time", ""))
                return parsed.timestamp() if parsed else 0

            finished_rows.sort(key=newest_first, reverse=True)
            pending_rows.sort(key=newest_first, reverse=True)
            required_columns = [
                "is_toto14", "is_correct_prob", "is_correct_ev", "actual_result",
                "match_time", "home_team", "away_team", "league", "actual_score",
                "prob_pick", "ev_pick", "ai_note", "match_id", "analysis_version",
                "api_fixture_id",
            ]
            df_finished = pd.DataFrame(finished_rows)
            df_pending = pd.DataFrame(pending_rows)
            for column in required_columns:
                if column not in df_finished.columns:
                    df_finished[column] = 0 if column.startswith("is_") else ""
                if column not in df_pending.columns:
                    df_pending[column] = 0 if column.startswith("is_") else ""
            current_version = str(
                dashboard_data.get("source_meta", {}).get("analysis_version") or ""
            ).strip()
            df_proto_all = df_finished[
                df_finished['is_toto14'].fillna(0).astype(int) == 0
            ]
            df_toto_all = df_finished[
                df_finished['is_toto14'].fillna(0).astype(int) == 1
            ]
            if current_version:
                df_proto = df_proto_all[
                    df_proto_all['analysis_version'].fillna('').astype(str) == current_version
                ]
                df_toto = df_toto_all[
                    df_toto_all['analysis_version'].fillna('').astype(str) == current_version
                ]
            else:
                df_proto = df_proto_all
                df_toto = df_toto_all
            
            proto_total = len(df_proto)
            proto_prob_hit = int(pd.to_numeric(df_proto['is_correct_prob'], errors='coerce').fillna(0).sum()) if proto_total > 0 else 0
            # A/B 비교에서는 확률픽과 완전히 같은 대안픽을 두 번 센 것처럼
            # 보이지 않도록 별도 방향인 배당형 대안픽만 집계한다.
            honey_mask = (
                df_proto['ev_pick'].fillna('').astype(str).str.strip().ne('')
                & df_proto['ev_pick'].fillna('').astype(str).ne(
                    df_proto['prob_pick'].fillna('').astype(str)
                )
            )
            df_honey = df_proto[honey_mask]
            proto_ev_total = len(df_honey)
            proto_ev_hit = int(pd.to_numeric(df_honey['is_correct_ev'], errors='coerce').fillna(0).sum()) if proto_ev_total > 0 else 0
            
            proto_prob_acc = round((proto_prob_hit / proto_total) * 100, 1) if proto_total > 0 else 0.0
            proto_ev_acc = round((proto_ev_hit / proto_ev_total) * 100, 1) if proto_ev_total > 0 else 0.0

            toto_total = len(df_toto)
            toto_hit = int(pd.to_numeric(df_toto['is_correct_prob'], errors='coerce').fillna(0).sum()) if toto_total > 0 else 0
            toto_acc = round((toto_hit / toto_total) * 100, 1) if toto_total > 0 else 0.0

            today = datetime.now(timezone(timedelta(hours=9))).date()
            today_finished = []
            for row in finished_rows:
                if int(row.get("is_toto14") or 0) != 0:
                    continue
                parsed = _ui_match_datetime(row.get("match_time", ""))
                if parsed and parsed.date() == today:
                    today_finished.append(row)
            today_versions = sorted({
                str(row.get("analysis_version") or "이전 버전").strip()
                for row in today_finished
            })
            today_hit_count = sum(
                int(row.get("is_correct_prob") or 0) for row in today_finished
            )
            
            return {
                "proto": {"total": proto_total, "prob_hit": proto_prob_hit, "ev_hit": proto_ev_hit, "ev_total": proto_ev_total, "prob_acc": proto_prob_acc, "ev_acc": proto_ev_acc},
                "toto": {"total": toto_total, "hit": toto_hit, "acc": toto_acc},
                "current_version": current_version,
                "legacy_count": max(0, len(df_proto_all) - len(df_proto)) + max(0, len(df_toto_all) - len(df_toto)),
                "today_finished_count": len(today_finished),
                "today_hit_count": today_hit_count,
                "today_versions": today_versions,
                # 오답노트는 회원 등급과 관계없이 전체 기록을 공개한다.
                "history": df_proto_all.to_dict('records'),
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
        current_version_label = escape(stats.get('current_version') or '버전 정보 없음')
        prob_value = f"{p_stats['prob_acc']}%" if p_stats['total'] else "채점 대기"
        prob_note = f"({p_stats['prob_hit']}건 적중)" if p_stats['total'] else "종료 경기 결과를 기다리는 중"
        honey_value = f"{p_stats['ev_acc']}%" if p_stats['ev_total'] else "채점 대기"
        honey_note = f"({p_stats['ev_total']}건 중 {p_stats['ev_hit']}건 적중)" if p_stats['ev_total'] else "배당형 대안픽 결과를 기다리는 중"
        toto_value = f"{t_stats['acc']}%" if t_stats['total'] else "채점 대기"
        toto_note = f"총 {t_stats['total']}경기 중 {t_stats['hit']}경기 적중" if t_stats['total'] else "현재 버전의 종료 경기 없음"
        
        st.markdown(f"""
        <div class='grade-summary-grid'>
            <div class='grade-summary-card'>
                <span class='grade-summary-title'>📊 현재 버전 승부식 채점 (총 {p_stats['total']}경기)</span>
                <div class='grade-dual-row'>
                    <div class='grade-metric'>
                        <span class='grade-metric-label'>안전제일 확률픽</span>
                        <span class='grade-metric-value probability'>{prob_value}</span>
                        <span class='grade-metric-note'>{prob_note}</span>
                    </div>
                    <div class='grade-versus'>VS</div>
                    <div class='grade-metric'>
                        <span class='grade-metric-label'>배당형 대안픽</span>
                        <span class='grade-metric-value gold'>{honey_value}</span>
                        <span class='grade-metric-note'>{honey_note}</span>
                    </div>
                </div>
            </div>
            <div class='grade-summary-card grade-toto'>
                <span class='grade-summary-title'>🏆 승무패 14경기 단통 적중률</span>
                <span class='grade-metric-value toto'>{toto_value}</span>
                <span class='grade-metric-note'>{toto_note}</span>
            </div>
        </div>
        <div style='color:#64748B;font-size:12px;margin:-3px 0 20px 2px;'>
            집계 기준: {current_version_label} · 현재 버전으로 킥오프 전에 동결된 경기만 위 적중률에 포함 · 이전 버전 {stats['legacy_count']}건은 아래 기록에서 확인
        </div>
        """, unsafe_allow_html=True)

        if stats.get("today_finished_count"):
            today_versions = " · ".join(
                escape(version) for version in stats.get("today_versions", [])
            )
            st.markdown(
                "<div style='margin:0 0 20px;padding:12px 14px;border-radius:10px;"
                "border:1px solid rgba(16,185,129,.35);background:rgba(16,185,129,.07);"
                "color:#D1FAE5;font-size:13px;font-weight:800;'>"
                f"✅ 오늘 종료 경기 {stats['today_finished_count']}경기 채점 완료"
                f" · 확률픽 {stats.get('today_hit_count', 0)}/{stats['today_finished_count']} 적중"
                f"<span style='display:block;margin-top:4px;color:#94A3B8;font-size:11px;'>"
                f"당시 경기 전 동결 버전: {today_versions}. 분석 확률과 픽은 그대로 두고 결과만 연결했습니다."
                "</span></div>",
                unsafe_allow_html=True,
            )
        
        st.markdown("<h4 style='color:#F8FAFC; font-weight:900; margin-top:10px; margin-bottom:20px;'>📜 실제 데이터 채점 기록</h4>", unsafe_allow_html=True)
        
        history_data = stats['history']
        if not history_data:
            st.info("아직 채점이 완료된 종료 경기가 없습니다.")
        
        for row in history_data:
            h_team = escape(str(row.get('home_team', '')))
            a_team = escape(str(row.get('away_team', '')))
            m_time = escape(str(row.get('match_time', '')))
            score = escape(str(row.get('actual_score', '-:-')))
            league_name = escape(str(row.get('league', '')))
            row_version = escape(str(row.get('analysis_version') or '구버전 기록'))
            
            prob_pick_raw = str(row.get('prob_pick') or '')
            ev_pick_raw = str(row.get('ev_pick') or '')
            prob_pick = escape(_human_pick_label(prob_pick_raw, row.get('home_team', '')))
            ev_pick = escape(_human_pick_label(ev_pick_raw, row.get('home_team', '')))
            prob_ok = row.get('is_correct_prob', 0) == 1
            ev_ok = row.get('is_correct_ev', 0) == 1
            has_ev_pick = bool(ev_pick_raw.strip())
            same_pick = has_ev_pick and ev_pick_raw == prob_pick_raw
            note = _clean_grading_note(
                row.get('ai_note', ''), prob_ok, ev_ok, has_ev_pick, row=row
            )
            
            if row.get('actual_result') == 'CANCELED':
                score_html = "<span class='report-score' style='color:#94A3B8 !important;'>취소/무효</span>"
                note_style = "real-ai-note"
            else:
                score_html = f"<span class='report-score'>{score}</span>"
                note_style = "real-ai-note" if (prob_ok or ev_ok) else "real-ai-note-fail"

            prob_badge = "<span style='background:#10B981; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>적중</span>" if prob_ok else "<span style='background:#EF4444; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>실패</span>"
            if not has_ev_pick:
                ev_badge = "<span style='background:#475569; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>미선정</span>"
                ev_pick = "승무패·핸디캡 배당 자료 없음"
            elif same_pick:
                ev_badge = "<span style='background:#475569; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>동일픽</span>"
                ev_pick += " · A/B 별도 집계 제외"
            else:
                ev_badge = "<span style='background:#10B981; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>적중</span>" if ev_ok else "<span style='background:#EF4444; color:#fff; padding:2px 6px; border-radius:4px; font-size:11px; margin-right:5px;'>실패</span>"

            html = (
                f"<div class='report-card'>"
                f"<div class='report-head'>"
                f"<div class='report-match'>"
                f"<span style='color:#64748B; font-size:12px; display:block; margin-bottom:4px;'>{m_time} • {league_name} • {row_version}</span>"
                f"<span class='report-team'>{h_team} <span style='color:#475569;'>VS</span> {a_team}</span>"
                f"</div>"
                f"<div class='report-result'>"
                f"<span style='color:#94A3B8; font-size:11px; display:block; margin-bottom:4px;'>최종 결과</span>"
                f"{score_html}"
                f"</div>"
                f"</div>"
                f"<div class='report-picks'>"
                f"<div class='report-pick-box'>"
                f"<span style='color:#94A3B8; font-size:11px; display:block; margin-bottom:4px;'>🎯 확률픽 예측</span>"
                f"<div style='font-size:14px; font-weight:900; color:#F8FAFC;'>{prob_badge} {prob_pick}</div>"
                f"</div>"
                f"<div class='report-pick-box'>"
                f"<span style='color:#94A3B8; font-size:11px; display:block; margin-bottom:4px;'>🍯 배당형 대안픽 예측</span>"
                f"<div style='font-size:14px; font-weight:900; color:#F8FAFC;'>{ev_badge} {ev_pick}</div>"
                f"</div>"
                f"</div>"
                f"<div class='{note_style}'>{note}</div>"
                f"</div>"
            )
            st.markdown(html, unsafe_allow_html=True)
            
        pending_data = stats['pending']
        if pending_data:
            def parse_time_for_ui(t_str):
                now = datetime.now(timezone(timedelta(hours=9)))
                parsed = _ui_match_datetime(t_str)
                return parsed if parsed else now - timedelta(hours=3)

            now = datetime.now(timezone(timedelta(hours=9)))
            active_pending = []
            archived_unresolved = []
            for row in pending_data:
                match_time = str(row.get('match_time') or '')
                match_dt = parse_time_for_ui(match_time)
                missing_identity = (
                    not str(row.get('analysis_version') or '').strip()
                    and not int(row.get('api_fixture_id') or 0)
                )
                # 종료 예상 시점을 충분히 지난 미채점 기록은
                # 고유번호 유무와 관계없이 현재 경기 목록에서 분리한다.
                if now >= match_dt + timedelta(hours=5):
                    archived_row = dict(row)
                    archived_row['_archive_reason'] = (
                        '고유번호·버전 정보 없음'
                        if missing_identity else '결과 연결 미완료'
                    )
                    archived_unresolved.append(archived_row)
                else:
                    active_pending.append(row)

            if active_pending:
                st.markdown("<h4 style='color:#64748B; font-weight:900; margin-top:40px; margin-bottom:15px;'>⏳ 현재 경기 일정 및 채점 현황</h4>", unsafe_allow_html=True)
            
            displayed_pending = 0
            for row in active_pending:
                m_time_str = row.get('match_time', '')
                
                if m_time_str == "시간 미정":
                    continue
                    
                displayed_pending += 1
                m_id_str = str(row['match_id'])
                temp_score = row.get('actual_score', '-:-')
                
                m_dt = parse_time_for_ui(m_time_str)
                now = datetime.now(timezone(timedelta(hours=9)))
                
                if now < m_dt:
                    badge_html = "<span class='pending-report-badge upcoming'>진행 예정</span>"
                elif now < m_dt + timedelta(hours=2.5):
                    badge_html = "<span class='pending-report-badge playing'>경기 진행중</span>"
                else:
                    badge_html = "<span class='pending-report-badge grading'>채점 로봇 분석중</span>"

                event_html = ""
                if m_id_str in live_scores_data:
                    live_info = live_scores_data[m_id_str]
                    if live_info.get("score"):
                        temp_score = str(live_info["score"]).replace(" : ", ":")
                    event_lines = []
                    for raw_event in (live_info.get("events") or [])[-3:]:
                        if isinstance(raw_event, dict):
                            line = str(raw_event.get("text") or "").strip()
                            if line:
                                event_lines.append(line)
                    safe_event = "<br>".join(escape(line) for line in event_lines)
                    if not safe_event and live_info.get("event"):
                        safe_event = escape(str(live_info.get("event") or ""))
                    if safe_event:
                        event_html = f"<div style='font-size:11px; color:#10B981; font-weight:900; margin-top:4px; overflow-wrap:anywhere;'>{safe_event}</div>"
                    badge_html = "<span class='pending-report-badge live'>🔴 LIVE</span>"

                safe_time = escape(str(m_time_str))
                safe_home = escape(str(row.get('home_team', '')))
                safe_away = escape(str(row.get('away_team', '')))
                safe_score = escape(str(temp_score))
                 
                html_str = (
                    f"<div class='pending-report-card'>"
                    f"<div class='pending-report-match'>"
                    f"<div style='color:#64748B; font-size:12px; margin-bottom:4px; font-weight:900;'>{safe_time}</div>"
                    f"<div class='pending-report-teams'>{safe_home} <span style='color:#64748B;'>VS</span> {safe_away}</div>"
                    f"{event_html}"
                    f"</div>"
                    f"<div class='pending-report-status'>"
                    f"<span class='pending-report-score'>{safe_score}</span>"
                    f"{badge_html}"
                    f"</div>"
                    f"</div>"
                )
                st.markdown(html_str, unsafe_allow_html=True)
                
            if displayed_pending == 0:
                st.info("현재 대기 중인 향후 경기 일정이 없습니다.")

            if archived_unresolved:
                st.markdown(
                    "<h4 style='color:#64748B;font-weight:900;margin-top:32px;"
                    "margin-bottom:8px;'>🗂️ 과거 미채점·미연결 기록</h4>"
                    "<p style='color:#64748B;font-size:12px;margin-bottom:12px;'>"
                    "종료 예상 시간이 5시간 이상 지났지만 결과가 정확히 연결되지 않은 기록입니다. "
                    "현재 경기 목록과 적중률에서는 제외하고 원본은 보존합니다."
                    "</p>",
                    unsafe_allow_html=True,
                )
                with st.expander(f"과거 미연결 기록 {len(archived_unresolved)}건 확인"):
                    legacy_table = pd.DataFrame([
                        {
                            "경기 시각": row.get("match_time", ""),
                            "경기": f"{row.get('home_team', '')} vs {row.get('away_team', '')}",
                            "상태": f"{row.get('_archive_reason', '결과 미연결')} · 통계 제외",
                        }
                        for row in archived_unresolved
                    ])
                    st.dataframe(
                        legacy_table,
                        use_container_width=True,
                        hide_index=True,
                    )

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
                    ["proof", "free"],
                    format_func=lambda value: {
                        "proof": "적중 인증", "free": "자유 이야기"
                    }[value],
                )
                post_title = st.text_input("제목", max_chars=80)
                post_body = st.text_area("내용", max_chars=5000, height=150)
                post_image = st.file_uploader(
                    "인증 사진 (선택)",
                    type=["jpg", "jpeg", "png", "webp"],
                    accept_multiple_files=False,
                    help="적중 인증 글에 JPG·PNG·WEBP 사진 1장, 최대 2MB까지 첨부할 수 있습니다.",
                )
                st.caption("사진은 개인정보와 계좌번호를 가린 뒤 올려주세요. 적중 인증 분류에서만 저장됩니다.")
                proof_confirmed = st.checkbox(
                    "적중 인증 글은 경기 종료 후 작성했으며, 개인정보·계좌번호를 가렸습니다."
                )
                st.caption("경기 전 유료·선행 픽 공개, 불법 사이트 홍보, 허위 수익 인증 글은 등록할 수 없습니다.")
                submitted = st.form_submit_button("등록")
                if submitted:
                    if category == "proof" and not proof_confirmed:
                        st.error("적중 인증 작성 확인에 동의해주세요.")
                    else:
                        uploaded_bytes = post_image.getvalue() if post_image else None
                        uploaded_mime = post_image.type if post_image else None
                        ok, message = create_post(
                            int(st.session_state["user_id"]),
                            post_title,
                            post_body,
                            category,
                            image_bytes=uploaded_bytes,
                            image_mime=uploaded_mime,
                        )
                        (st.success if ok else st.error)(message)
                        if ok:
                            st.rerun()
    elif st.session_state.get("logged_in"):
        st.caption("글 작성은 후원회원 전환 후 이용할 수 있습니다.")
    else:
        st.caption("글 작성은 로그인한 후원회원만 이용할 수 있습니다.")

    category_labels = {"proof": "적중 인증", "free": "자유", "notice": "공지"}
    is_board_admin = current_role == ROLE_ADMIN
    board_posts = list_posts(limit=100, include_hidden=is_board_admin)
    if not board_posts:
        st.info("아직 등록된 글이 없습니다. 첫 인증 기록을 기다리고 있습니다.")

    for post in board_posts:
        with st.container(border=True):
            title_col, meta_col = st.columns([4, 1])
            with title_col:
                st.markdown(f"#### {escape(post['title'])}")
            with meta_col:
                st.caption(category_labels.get(post["category"], "게시글"))
                if post.get("status") == "hidden":
                    st.caption("🚫 관리자 숨김")
            st.caption(
                f"{post['display_name']} · {post['created_at'].replace('T', ' ')[:16]}"
            )
            st.write(post["body"])
            if post.get("has_image"):
                attachment = get_post_image(int(post["id"]), include_hidden=is_board_admin)
                if attachment:
                    st.markdown(
                        "<div class='board-photo-caption'>첨부된 인증 사진</div>",
                        unsafe_allow_html=True,
                    )
                    st.image(attachment[1], use_container_width=True)

            current_user_id = st.session_state.get("user_id")
            may_manage_post = bool(
                current_user_id
                and (
                    is_board_admin
                    or int(current_user_id) == int(post.get("author_id") or 0)
                )
            )
            if may_manage_post:
                with st.expander("글 관리"):
                    with st.form(f"edit-post-form-{post['id']}"):
                        edit_title = st.text_input(
                            "제목 수정", value=str(post["title"]), max_chars=80,
                            key=f"edit-post-title-{post['id']}",
                        )
                        edit_body = st.text_area(
                            "내용 수정", value=str(post["body"]), max_chars=5000,
                            height=130, key=f"edit-post-body-{post['id']}",
                        )
                        edit_submit = st.form_submit_button("수정 저장")
                    if edit_submit:
                        ok, message = update_post(
                            int(current_user_id), int(post["id"]), edit_title, edit_body
                        )
                        (st.success if ok else st.error)(message)
                        if ok:
                            st.rerun()

                    action_col, delete_col = st.columns(2)
                    if is_board_admin:
                        should_show_post = post.get("status") == "hidden"
                        action_label = "다시 공개" if should_show_post else "관리자 숨김"
                        if action_col.button(
                            action_label,
                            key=f"post-visibility-{post['id']}",
                            use_container_width=True,
                        ):
                            ok, message = set_post_visibility(
                                int(current_user_id), int(post["id"]), should_show_post
                            )
                            (st.success if ok else st.error)(message)
                            if ok:
                                st.rerun()

                    if delete_col.button(
                        "삭제", key=f"delete-post-{post['id']}", use_container_width=True
                    ):
                        ok, message = delete_post(
                            int(current_user_id), int(post["id"])
                        )
                        (st.success if ok else st.error)(message)
                        if ok:
                            st.rerun()
