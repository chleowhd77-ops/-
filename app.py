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
# 0. 기본 설정 및 타이틀
# -----------------------------------------------------------------------------
APP_TITLE = "D.J PROTO ANALYTICS"

st.set_page_config(
    page_title=APP_TITLE, 
    page_icon="⚡", 
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
    "광주FC": "Gwangju FC", "포항스틸": "Pohang Steelers", "제주SKFC": "Jeju United", "제주 SKFC": "Jeju United", "FC안양": "FC Anyang", "FC 안양": "FC Anyang",
    "FC서울": "FC Seoul", "대전하나": "Daejeon Citizen", "대전 하나시티즌": "Daejeon Citizen", "충북청주": "Chungbuk Cheongju", "전남드래": "Jeonnam Dragons",
    "김해FC": "Gimhae", "경남FC": "Gyeongnam FC", "수원삼성": "Suwon Samsung", "수원FC": "Suwon FC",
    "부산아이": "Busan I Park", "부산 아이파크": "Busan I Park", "화성FC": "Hwaseong", "인천유나": "Incheon United", "김천상무": "Gimcheon Sangmu", "김천상무 프로축구단": "Gimcheon Sangmu",
    "부천FC": "Bucheon FC 1995", "부천FC 1995": "Bucheon FC 1995", "전북현대": "Jeonbuk Motors", "울산HDFC": "Ulsan Hyundai", "강원FC": "Gangwon FC",
    "서울이랜드": "Seoul E-Land", "안산그리": "Ansan Greeners", "대구FC": "Daegu FC", 
    "충남아산": "Chungnam Asan", "충남아산 프로축구단": "Chungnam Asan", "김포FC": "Gimpo FC", "천안시티": "Cheonan City", "파주프런": "Paju Citizen", "성남FC": "Seongnam FC",
    "APIA 라이카트": "APIA Leichhardt", "멜버른 빅토리": "Melbourne Victory",
    "포츠머스": "Portsmouth", "퀸즈파크 레인저스": "Queens Park Rangers", "노리치 시티": "Norwich City",
    "웨스트브로미치 앨비언": "West Bromwich Albion", "스토크 시티": "Stoke City", "스완지 시티": "Swansea City",
    "SD레이더스": "SD Raiders", "시드니FC": "Sydney FC", "말레이시아": "Malaysia", "베트남": "Vietnam",
    "SK슬로반 브라티슬라바": "Slovan Bratislava", "NK첼레": "Celje", "하포엘 베르셰바": "Hapoel Beer Sheva", "사바FK": "Sabah",
    "NEC네이메헌": "NEC Nijmegen", "FK보되 글림트": "Bodo/Glimt", "세로 포르테뇨": "Cerro Porteno", "SE파우메이라스": "Palmeiras",
    "LDU키토": "LDU Quito", "미라솔": "Mirassol", "SC코린티안스": "Corinthians", "로사리오 센트랄": "Rosario Central",
    "아틀레티코 마드리드": "Atletico Madrid", "말라가": "Malaga", "셀틱": "Celtic", "라요 바예카노": "Rayo Vallecano", "알라베스": "Alaves", "LASK": "LASK",
    "코킴보 우니도": "Coquimbo Unido", "CA플라텐세": "CA Platense", "CR플라멩구": "Flamengo", "크루제이루EC": "Cruzeiro",
    "카이라트 알마티": "Kairat Almaty", "RSC안더레흐트": "Anderlecht", "야기엘로니아 비아위스토크": "Jagiellonia Bialystok", "이베리아1999 트빌리시": "Iberia 1999",
    "미엘뷔AIF": "Mjallby", "잘츠부르크": "Red Bull Salzburg", "트라브존스포르": "Trabzonspor", "페렌츠바로시TC": "Ferencvarosi TC",
    "우니베르시타테아 크라이오바": "Universitatea Craiova", "아라라트 아르메니아": "Ararat-Armenia", "KF에그나티아": "KF Egnatia", "릴레스트룀SK": "Lillestrom",
    "레흐 포즈난": "Lech Poznan", "FC툰": "Thun", "베식타시": "Besiktas", "카우노 잘기리스": "Kauno Zalgiris",
    "신트 트라위던VV": "Sint-Truiden", "AC오모니아": "Omonia Nicosia", "FK츠르베나 즈베즈다": "Crvena Zvezda", "빅토리아 플젠": "Viktoria Plzen",
    "OFI크레타": "OFI Crete", "CSKA소피아": "CSKA Sofia", "SL벤피카": "Benfica", "AGF오르후스": "Aarhus",
    "가시와 레이솔": "Kashiwa Reysol", "V바렌 나가사키": "V-Varen Nagasaki", "FC도쿄": "FC Tokyo", "제프 유나이티드": "JEF United Chiba",
    "FC신시내": "FC Cincinnati", "FC신시내티": "FC Cincinnati", "뉴욕시티": "New York City FC", "뉴욕 시티FC": "New York City FC",
    "콜럼크루": "Columbus Crew", "콜럼버스 크루": "Columbus Crew", "CF몽레알": "CF Montreal",
    "DC유나이": "DC United", "DC유나이티드": "DC United", "뉴잉레벌": "New England Revolution", "뉴잉글랜드 레벌루션": "New England Revolution",
    "뉴욕레드": "New York Red Bulls", "뉴욕 레드불스": "New York Red Bulls", "내슈빌SC": "Nashville SC",
    "올랜시티": "Orlando City", "올랜도 시티SC": "Orlando City", "시카파이": "Chicago Fire", "시카고 파이어FC": "Chicago Fire",
    "토론토FC": "Toronto FC", "샬럿FC": "Charlotte", "필라델피아 유니언": "Philadelphia Union", "인터 마이애미CF": "Inter Miami CF",
    "스포캔자": "Sporting Kansas City", "스포팅 캔자스시티": "Sporting Kansas City", "세인시티": "St. Louis City", "세인트루이스 시티": "St. Louis City", "세인트루이스 시티SC": "St. Louis City",
    "미네유나": "Minnesota United", "미네소타 유나이티드FC": "Minnesota United", "애틀유나": "Atlanta United", "애틀랜타 유나이티드FC": "Atlanta United",
    "콜로래피": "Colorado Rapids", "콜로라도 래피즈": "Colorado Rapids", "LAFC": "Los Angeles FC", "레알솔트": "Real Salt Lake", "레알 솔트레이크": "Real Salt Lake", "FC댈러스": "FC Dallas",
    "시애사운": "Seattle Sounders", "시애틀 사운더스FC": "Seattle Sounders", "시애틀 사운더스": "Seattle Sounders",
    "LA갤럭시": "Los Angeles Galaxy", "LA 갤럭시": "Los Angeles Galaxy", 
    "포틀팀버": "Portland Timbers", "포틀랜드 팀버스": "Portland Timbers",
    "샌디에FC": "San Diego FC", "샌디에이고FC": "San Diego FC",
    "밴쿠화이": "Vancouver Whitecaps", "밴쿠버 화이트캡스FC": "Vancouver Whitecaps", "휴스다이": "Houston Dynamo", "휴스턴 다이너모FC": "Houston Dynamo"
}

DIRECT_LOGO_MAP = {}

DIRECT_TEAM_INFO = {
    "오스틴FC": {"id": 16133, "logo": "https://media.api-sports.io/football/teams/16133.png"},
    "새너제이 어스퀘이크스": {"id": 16055, "logo": "https://media.api-sports.io/football/teams/16055.png"},
    "새너어스": {"id": 16055, "logo": "https://media.api-sports.io/football/teams/16055.png"},
    "LDU키토": {"id": 2939, "logo": "https://media.api-sports.io/football/teams/2939.png"},
    "신트 트라위던VV": {"id": 742, "logo": "https://media.api-sports.io/football/teams/742.png"},
    "OFI크레타": {"id": 354, "logo": "https://media.api-sports.io/football/teams/354.png"},
    "V바렌 나가사키": {"id": 300, "logo": "https://media.api-sports.io/football/teams/300.png"},
    "야기엘로니아 비아위스토크": {"id": 338, "logo": "https://media.api-sports.io/football/teams/338.png"},
    "아라라트 아르메니아": {"id": 5316, "logo": "https://media.api-sports.io/football/teams/5316.png"},
    "KF에그나티아": {"id": 9789, "logo": "https://tmssl.akamaized.net/images/wappen/big/19491.png"},
    "이베리아1999 트빌리시": {"id": 2280, "logo": "https://media.api-sports.io/football/teams/2280.png"},
    "샌디에이고FC": {"id": 22054, "logo": "https://media.api-sports.io/football/teams/22054.png"}
}

# -----------------------------------------------------------------------------
# 영구 DB 캐싱 엔진
# -----------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT UNIQUE, league TEXT, home_team TEXT, away_team TEXT,
            predicted_pick TEXT, predicted_prob REAL, expected_score TEXT, odd_h REAL, odd_d REAL, odd_a REAL,
            actual_score TEXT DEFAULT '-:-', actual_result TEXT DEFAULT 'PENDING', is_correct INTEGER DEFAULT 0,
            failure_reason TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_toto14 INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_cache (
            cache_key TEXT PRIMARY KEY,
            cache_value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try: cursor.execute("ALTER TABLE predictions ADD COLUMN is_toto14 INTEGER DEFAULT 0")
    except: pass 
    try: cursor.execute("ALTER TABLE predictions ADD COLUMN failure_reason TEXT DEFAULT ''")
    except: pass
    conn.commit()
    conn.close()

init_db()

def get_db_cache(key, ttl_hours):
    try:
        conn = sqlite3.connect("ai_predictions.db")
        cursor = conn.cursor()
        cursor.execute("SELECT cache_value, updated_at FROM api_cache WHERE cache_key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        if row:
            val, updated_at = row
            updated_time = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - updated_time < timedelta(hours=ttl_hours):
                return json.loads(val)
    except: pass
    return None

def set_db_cache(key, value):
    try:
        conn = sqlite3.connect("ai_predictions.db")
        cursor = conn.cursor()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT OR REPLACE INTO api_cache (cache_key, cache_value, updated_at)
            VALUES (?, ?, ?)
        """, (key, json.dumps(value), now_str))
        conn.commit()
        conn.close()
    except: pass

@st.cache_data(ttl=86400)
def fetch_team_info_api(team_name):
    if not team_name: return {"id": None, "logo": None}
    if team_name in DIRECT_TEAM_INFO: return DIRECT_TEAM_INFO[team_name]
    cache_key = f"team_info_{team_name}"
    cached_data = get_db_cache(cache_key, 8760) 
    if cached_data: return cached_data
    search_name = TEAM_NAME_MAP.get(team_name, team_name)
    try:
        response = requests.get(f"https://{API_HOST}/teams", headers=headers, params={"search": search_name}, timeout=5)
        res_data = response.json()
        if res_data.get("response") and len(res_data["response"]) > 0:
            res = {"id": res_data["response"][0]["team"]["id"], "logo": res_data["response"][0]["team"].get("logo")}
            set_db_cache(cache_key, res)
            return res
        clean_name = re.sub(r'(프로축구단|하나시티즌|FC|유나이티드|아이파크|스틸러스|드래곤즈|시티즌|모터스|이랜드|그리너스|시티|프런티어|1995|SK|NK|FK|SC|NEC|SE)', '', team_name).strip()
        if clean_name and clean_name != team_name:
            search_name_clean = TEAM_NAME_MAP.get(clean_name, clean_name)
            res2 = requests.get(f"https://{API_HOST}/teams", headers=headers, params={"search": search_name_clean}, timeout=5)
            res2_data = res2.json()
            if res2_data.get("response") and len(res2_data["response"]) > 0:
                res = {"id": res2_data["response"][0]["team"]["id"], "logo": res2_data["response"][0]["team"].get("logo")}
                set_db_cache(cache_key, res)
                return res
    except: pass
    return {"id": None, "logo": None}

@st.cache_data(ttl=43200)
def fetch_team_form_api(team_id):
    if not team_id: return ""
    cache_key = f"form_{team_id}"
    cached_data = get_db_cache(cache_key, 12)
    if cached_data is not None: return cached_data
    try:
        response = requests.get(f"https://{API_HOST}/fixtures", headers=headers, params={"team": team_id, "last": 5}, timeout=5)
        data = response.json().get("response", [])
        form_list = []
        for m in data:
            home_id = m["teams"]["home"]["id"]
            home_win = m["teams"]["home"]["winner"]
            away_win = m["teams"]["away"]["winner"]
            if home_id == team_id:
                if home_win is True: form_list.append("승")
                elif home_win is False and away_win is True: form_list.append("패")
                else: form_list.append("무")
            else:
                if away_win is True: form_list.append("승")
                elif away_win is False and home_win is True: form_list.append("패")
                else: form_list.append("무")
        form_list.reverse()
        res = "-".join(form_list) if form_list else ""
        set_db_cache(cache_key, res)
        return res
    except: return ""

@st.cache_data(ttl=86400)
def fetch_team_long_term_stats_api(team_id):
    default_res = {"home_wins": 0, "home_total": 0, "home_gf": 0, "home_ga": 0, "away_wins": 0, "away_total": 0, "away_gf": 0, "away_ga": 0}
    if not team_id: return default_res
    cache_key = f"stats_v3_{team_id}"
    cached_data = get_db_cache(cache_key, 24)
    if cached_data: return cached_data
    try:
        res = requests.get(f"https://{API_HOST}/fixtures", headers=headers, params={"team": team_id, "last": 40}, timeout=5)
        data = res.json().get("response", [])
        for m in data:
            home_id = m["teams"]["home"]["id"]
            away_id = m["teams"]["away"]["id"]
            winner_home = m["teams"]["home"]["winner"]
            winner_away = m["teams"]["away"]["winner"]
            goals_h = m.get("goals", {}).get("home")
            goals_a = m.get("goals", {}).get("away")
            gh = int(goals_h) if goals_h is not None else 0
            ga = int(goals_a) if goals_a is not None else 0
            
            if home_id == team_id:
                default_res["home_total"] += 1
                default_res["home_gf"] += gh
                default_res["home_ga"] += ga
                if winner_home is True: default_res["home_wins"] += 1
            elif away_id == team_id:
                default_res["away_total"] += 1
                default_res["away_gf"] += ga
                default_res["away_ga"] += gh
                if winner_away is True: default_res["away_wins"] += 1
        set_db_cache(cache_key, default_res)
        return default_res
    except: return default_res

@st.cache_data(ttl=86400)
def fetch_team_standing_api(team_id):
    if not team_id: return {"rank": 99, "points": 0, "league_id": None, "season": None}
    cache_key = f"standing_v2_{team_id}"
    cached_data = get_db_cache(cache_key, 24)
    if cached_data: return cached_data
    try:
        year = datetime.now().year
        res = requests.get(f"https://{API_HOST}/standings", headers=headers, params={"team": team_id, "season": year}, timeout=5)
        data = res.json().get("response", [])
        if not data:
            res = requests.get(f"https://{API_HOST}/standings", headers=headers, params={"team": team_id, "season": year-1}, timeout=5)
            data = res.json().get("response", [])
        if data:
            for league_data in data:
                league_id = league_data.get("league", {}).get("id")
                season = league_data.get("league", {}).get("season")
                standings_list = league_data.get("league", {}).get("standings", [])
                for group in standings_list:
                    for s in group:
                        if s["team"]["id"] == team_id:
                            res_val = {"rank": s["rank"], "points": s["points"], "league_id": league_id, "season": season}
                            set_db_cache(cache_key, res_val)
                            return res_val
    except: pass
    res_val = {"rank": 99, "points": 0, "league_id": None, "season": None}
    set_db_cache(cache_key, res_val)
    return res_val

@st.cache_data(ttl=604800)
def fetch_league_top_scorers(league_id, season):
    if not league_id or not season: return []
    cache_key = f"topscorers_{league_id}_{season}"
    cached_data = get_db_cache(cache_key, 168)
    if cached_data: return cached_data
    try:
        res = requests.get(f"https://{API_HOST}/players/topscorers", headers=headers, params={"league": league_id, "season": season}, timeout=5)
        data = res.json().get("response", [])
        names = [p["player"]["name"] for p in data]
        set_db_cache(cache_key, names)
        return names
    except: return []

@st.cache_data(ttl=43200)
def fetch_team_injuries_api(team_id, league_id, season):
    default_res = {"count": 0, "ace_missing": False, "ace_names": []}
    if not team_id: return default_res
    cache_key = f"inj_v2_{team_id}_{league_id}_{season}"
    cached_data = get_db_cache(cache_key, 12)
    if cached_data is not None: return cached_data
    try:
        fix_res = requests.get(f"https://{API_HOST}/fixtures", headers=headers, params={"team": team_id, "last": 1}, timeout=5)
        last_fix = fix_res.json().get("response", [])
        if last_fix:
            fix_id = last_fix[0]["fixture"]["id"]
            inj_res = requests.get(f"https://{API_HOST}/injuries", headers=headers, params={"fixture": fix_id}, timeout=5)
            inj_data = inj_res.json().get("response", [])
            
            injured_names = [x.get("player", {}).get("name", "") for x in inj_data if x.get("team", {}).get("id") == team_id]
            count = len(injured_names)
            ace_missing = False
            ace_names = []
            
            if count > 0 and league_id and season:
                top_scorers = fetch_league_top_scorers(league_id, season)
                for name in injured_names:
                    if not name: continue
                    n_lower = name.lower()
                    for ts in top_scorers:
                        ts_lower = ts.lower()
                        if n_lower in ts_lower or ts_lower in n_lower:
                            ace_missing = True
                            if name not in ace_names: ace_names.append(name)
                            
            res_val = {"count": count, "ace_missing": ace_missing, "ace_names": list(set(ace_names))}
            set_db_cache(cache_key, res_val)
            return res_val
        return default_res
    except: return default_res

@st.cache_data(ttl=43200)
def fetch_team_last_match_date_api(team_id):
    if not team_id: return None
    cache_key = f"last_match_{team_id}"
    cached_data = get_db_cache(cache_key, 12)
    if cached_data: return cached_data
    try:
        res = requests.get(f"https://{API_HOST}/fixtures", headers=headers, params={"team": team_id, "last": 1}, timeout=5)
        data = res.json().get("response", [])
        if data:
            date_val = data[0]["fixture"]["date"]
            set_db_cache(cache_key, date_val)
            return date_val
    except: pass
    return None

@st.cache_data(ttl=43200)
def fetch_team_next_fixture_api(team_id):
    default_res = {"days_until_next": 99, "is_important": False, "league_name": ""}
    if not team_id: return default_res
    cache_key = f"next_fix_v4_{team_id}"
    cached_data = get_db_cache(cache_key, 12)
    if cached_data: return cached_data
    try:
        res = requests.get(f"https://{API_HOST}/fixtures", headers=headers, params={"team": team_id, "next": 3}, timeout=5)
        data = res.json().get("response", [])
        for next_fix in data:
            next_date_str = next_fix["fixture"]["date"]
            league_name = next_fix["league"]["name"]
            
            next_dt = datetime.fromisoformat(next_date_str.replace('Z', '+00:00'))
            now = datetime.now(timezone(timedelta(hours=9)))
            diff_hours = (next_dt - now).total_seconds() / 3600.0
            
            if diff_hours > 40:
                days_until = max(1, int(diff_hours / 24))
                important_keywords = ["Champions League", "Europa", "Cup", "Copa", "Sudamericana", "Libertadores", "AFC", "FA Cup"]
                is_important = any(kw.lower() in league_name.lower() for kw in important_keywords)
                
                res_val = {"days_until_next": days_until, "is_important": is_important, "league_name": league_name}
                set_db_cache(cache_key, res_val)
                return res_val
    except: pass
    return default_res

@st.cache_data(ttl=43200)
def fetch_recent_team_stats_api(team_id):
    default_res = {"possession": 50, "shots_on_goal": 4.0}
    if not team_id: return default_res
    cache_key = f"recent_stats_{team_id}"
    cached_data = get_db_cache(cache_key, 12)
    if cached_data: return cached_data
    try:
        res = requests.get(f"https://{API_HOST}/fixtures", headers=headers, params={"team": team_id, "last": 2}, timeout=5)
        fixtures = res.json().get("response", [])
        total_possession = 0
        total_sog = 0
        valid_matches = 0
        for f in fixtures:
            fix_id = f["fixture"]["id"]
            stat_res = requests.get(f"https://{API_HOST}/fixtures/statistics", headers=headers, params={"fixture": fix_id}, timeout=5)
            stats_data = stat_res.json().get("response", [])
            for team_stat in stats_data:
                if team_stat["team"]["id"] == team_id:
                    pos_val = 50
                    sog_val = 4.0
                    for s in team_stat["statistics"]:
                        if s["type"] == "Ball Possession" and s["value"]:
                            pos_val = int(str(s["value"]).replace('%', ''))
                        if s["type"] == "Shots on Goal" and s["value"]:
                            sog_val = float(s["value"])
                    total_possession += pos_val
                    total_sog += sog_val
                    valid_matches += 1
                    break
        if valid_matches > 0:
            res_val = {"possession": round(total_possession / valid_matches, 1), "shots_on_goal": round(total_sog / valid_matches, 1)}
        else: res_val = default_res
        set_db_cache(cache_key, res_val)
        return res_val
    except: pass
    return default_res

def calculate_rest_days(last_date_iso, match_time_str):
    if not last_date_iso or not match_time_str or match_time_str == "시간 미정": return 99
    try:
        last_dt = datetime.fromisoformat(last_date_iso.replace('Z', '+00:00'))
        now = datetime.now(timezone(timedelta(hours=9)))
        year = now.year
        match = re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})', match_time_str)
        if match:
            mo, d, h, m = map(int, match.groups())
            curr_dt = datetime(year, mo, d, h, m, tzinfo=timezone(timedelta(hours=9)))
            diff = curr_dt - last_dt
            return max(0, diff.days)
    except: pass
    return 99

@st.cache_data(ttl=43200)
def fetch_h2h_api(home_id, away_id):
    if not home_id or not away_id: return {"h_wins": 0, "a_wins": 0, "total": 0}
    cache_key = f"coll_h2h_{home_id}_{away_id}"
    cached_data = get_db_cache(cache_key, 24)
    if cached_data: return cached_data
    try:
        res = requests.get(f"https://{API_HOST}/fixtures/headtohead", headers=headers, params={"h2h": f"{home_id}-{away_id}"}, timeout=5)
        matches = res.json().get("response", [])
        h_wins, a_wins = 0, 0
        for m in matches[:10]:
            if m.get("teams", {}).get("home", {}).get("winner"):
                if m.get("teams", {}).get("home", {}).get("id") == home_id: h_wins += 1
                else: a_wins += 1
            elif m.get("teams", {}).get("away", {}).get("winner"):
                if m.get("teams", {}).get("home", {}).get("id") == home_id: a_wins += 1
                else: h_wins += 1
        res_data = {"h_wins": h_wins, "a_wins": a_wins, "total": len(matches[:10])}
        set_db_cache(cache_key, res_data)
        return res_data
    except: return {"h_wins": 0, "a_wins": 0, "total": 0}

def load_betman_data():
    raw_url = f"https://raw.githubusercontent.com/chleowhd77-ops/-/main/betman_data.json?t={int(time.time())}"
    try:
        res = requests.get(raw_url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list): return {"proto_matches": data, "toto_14_matches": []}
            return data
    except: pass
    return {"proto_matches": [], "toto_14_matches": []}

def load_live_scores():
    raw_url = f"https://raw.githubusercontent.com/chleowhd77-ops/-/main/live_scores.json?t={int(time.time())}"
    try:
        res = requests.get(raw_url, timeout=5)
        if res.status_code == 200: return res.json()
    except: pass
    return {}

def get_accuracy_stats():
    conn = sqlite3.connect("ai_predictions.db")
    df = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED'", conn)
    df_history = pd.read_sql_query("SELECT * FROM predictions WHERE actual_result = 'FINISHED' ORDER BY id DESC LIMIT 50", conn)
    conn.close()
    if len(df) == 0: return {"total": 0, "correct": 0, "accuracy": 0.0, "history": []}
    return {"total": len(df), "correct": df['is_correct'].sum(), "accuracy": round((df['is_correct'].sum() / len(df)) * 100, 1), "history": df_history.to_dict('records')}

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
            if m_dt <= now <= m_dt + timedelta(hours=2): return "LIVE", is_closed
            elif now > m_dt + timedelta(hours=2): return "FINISHED", is_closed
            else: return "UPCOMING", is_closed
    except: pass
    return "UPCOMING", False

# -----------------------------------------------------------------------------
# CSS 스타일링 
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
    .pred-box { flex: 1; background: #0D1424; border: 1px solid #1E293B; border-radius: 8px; padding: 16px; text-align: center; }
    .pred-label { font-size: 12px; color: #64748B; font-weight: 900; margin-bottom: 8px; }
    .pred-value { font-size: 18px; color: #F8FAFC; font-weight: 900; }
    .pred-prob { font-size: 14px; color: #10B981; font-weight: 900; margin-left: 6px; }
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

def render_logo_html(logo_url):
    if logo_url: return f"<img src='{logo_url}' class='team-logo' onerror=\"this.style.display='none';\">"
    return ""

st.markdown("""
<div class='app-header'>
    <h1>D.J PROTO ANALYTICS</h1>
    <p>AI 예측 기반 스마트 프로토 대시보드</p>
</div>
""", unsafe_allow_html=True)

main_tab1, main_tab2, main_tab3, main_tab4 = st.tabs([
    "프로토 LIVE", "승무패 14경기", "오늘의 TOP 3", "AI 리포트"
])

all_data = load_betman_data()
proto_matches = all_data.get("proto_matches", [])
live_scores_data = load_live_scores()

try:
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT match_id, league, home_team, away_team, odd_h, odd_d, odd_a, match_time, actual_result, actual_score 
        FROM predictions 
        WHERE is_toto14 = 0 
        AND (actual_result = 'PENDING' OR (actual_result = 'FINISHED' AND created_at >= datetime('now', '-1 day')))
    """)
    db_matches = cursor.fetchall()
    conn.close()
    
    # 👑 [스코어 누락 완벽 패치] DB 데이터를 딕셔너리로 만들어서 기존 매치에 강제 주입!
    db_dict = {str(row[0]): row for row in db_matches}
    json_match_ids = [str(m['id']) for m in proto_matches]
    
    for m in proto_matches:
        m_id_str = str(m['id'])
        if m_id_str in db_dict:
            m['actual_result'] = db_dict[m_id_str][8]
            m['actual_score'] = db_dict[m_id_str][9]
            
    for row in db_matches:
        m_id, league, h_team, a_team, odd_h, odd_d, odd_a, m_time, a_result, a_score = row
        if str(m_id) not in json_match_ids:
            proto_matches.append({
                'id': m_id, 'league': league, 'home': h_team, 'away': a_team,
                'odd_h': odd_h, 'odd_d': odd_d, 'odd_a': odd_a, 'match_time': m_time,
                'actual_result': a_result, 'actual_score': a_score
            })
except: pass

toto_14_raw = []
for x in all_data.get("toto_14_matches", []):
    h_name, a_name = x.get("home", ""), x.get("away", "")
    if h_name in ['홈팀', '홈'] or a_name in ['원정팀', '원정']: continue
    if any(word in h_name or word in a_name for word in ["전체조합수", "구매하기", "바로가기"]): continue
    toto_14_raw.append(x)

analyzed_proto = []

if proto_matches:
    for m in proto_matches:
        analyzed_proto.append({
            "match": m, 
            "final_match_time": m.get("match_time") or m.get("time") or "시간 미정", 
            "home_logo": "", "away_logo": "",
            "story": "...", 
            "best_option": "분석중", "best_prob_pct": 0.0,
            "best_handi": "-", "best_handi_prob": 0.0, 
            "best_uo": "-", "best_uo_prob": 0.0,
            "home_form": "", "away_form": "",
            "h_inj_data": {'count': 0, 'ace_missing': False}, 
            "a_inj_data": {'count': 0, 'ace_missing': False},
            "h_rest": 99, "a_rest": 99,
            "h_next": {"is_important": False, "days_until_next": 99}, 
            "a_next": {"is_important": False, "days_until_next": 99},
            "h_rank": 99, "a_rank": 99,
            "h_market": 0.0, "a_market": 0.0
        })

# -----------------------------------------------------------------------------
# [TAB 1] 프로토 LIVE
# -----------------------------------------------------------------------------
with main_tab1:
    sub_soccer, sub_baseball, sub_basketball = st.tabs(["축구", "야구", "농구"])
    with sub_soccer:
        if analyzed_proto:
            for item in analyzed_proto:
                m = item['match']
                raw_deadline = m.get("deadline_time", "23:00")
                match_status, is_closed = get_match_status(item["final_match_time"], raw_deadline)
                
                a_result = m.get('actual_result', 'PENDING')
                a_score = m.get('actual_score', '')
                
                # 👑 [스코어 강제 출력 & 채점 대기 패치]
                if a_result == 'FINISHED' and a_score and a_score != '-:-':
                    time_display = f"<span class='live-score' style='color:#F8FAFC;'>{a_score}</span><span class='deadline-closed' style='background:#475569; border-color:#475569;'>종료</span>"
                elif match_status == "LIVE" or m.get('match_time') == '마감/진행중':
                    match_id_str = str(m.get('id', ''))
                    if match_id_str in live_scores_data:
                        live_info = live_scores_data[match_id_str]
                        score_text = live_info.get("score", "진행중")
                        event_text = live_info.get("event", "")
                        
                        event_html = f"<div style='margin-bottom:6px; font-size:11px; color:#10B981; font-weight:900;'>{event_text}</div>" if event_text else ""
                        time_display = f"{event_html}<span class='live-score'>{score_text}</span><span class='deadline-closed' style='background:rgba(239, 68, 68, 0.1); border-color:#EF4444; color:#EF4444; animation: blink 2s infinite;'>🔴 LIVE</span>"
                    else:
                        time_display = f"<span class='live-score'>진행중</span><span class='deadline-closed' style='background:rgba(239, 68, 68, 0.1); border-color:#EF4444; color:#EF4444; animation: blink 2s infinite;'>🔴 LIVE</span>"
                elif match_status == "FINISHED": 
                    # 경기는 끝났는데 DB 채점이 아직 안된 경우
                    match_id_str = str(m.get('id', ''))
                    if match_id_str in live_scores_data and live_scores_data[match_id_str].get("score"):
                        temp_score = live_scores_data[match_id_str].get("score")
                        time_display = f"<span class='live-score' style='font-size:24px; color:#CBD5E1;'>{temp_score}</span><span class='deadline-closed' style='background:#475569; border-color:#475569;'>채점 진행중</span>"
                    else:
                        time_display = f"<span class='live-score' style='font-size:20px; color:#94A3B8;'>채점 대기</span><span class='deadline-closed' style='background:#475569; border-color:#475569;'>종료</span>"
                else:
                    badge = f"<span class='deadline-closed'>픽 마감</span>" if is_closed else f"<span class='deadline-open'>{raw_deadline}</span>"
                    time_display = f"<span class='match-time-text'>{item['final_match_time']}</span>{badge}"
                
                o_h_disp = m.get('odd_h') if m.get('odd_h') not in [None, 0.0, '', '-'] else '-'
                o_d_disp = m.get('odd_d') if m.get('odd_d') not in [None, 0.0, '', '-'] else '-'
                o_a_disp = m.get('odd_a') if m.get('odd_a') not in [None, 0.0, '', '-'] else '-'
                
                html_code = (
                    f"<div class='match-card'>"
                    f"<div class='league-title'>{m.get('league','축구')}</div>"
                    f"<div class='vs-row'>"
                    f"<div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div></div></div>"
                    f"<div class='center-time-box'>{time_display}</div>"
                    f"<div class='team-box away'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div></div></div>"
                    f"</div>"
                    f"<div class='odd-bar'><span class='odd-item'>승 <span class='odd-val'>{o_h_disp}</span> | 무 <span class='odd-val'>{o_d_disp}</span> | 패 <span class='odd-val'>{o_a_disp}</span></span></div>"
                    f"</div>"
                )
                st.markdown(html_code, unsafe_allow_html=True)
        else: st.info("현재 분석 가능한 프로토 축구 경기가 없습니다.")
    with sub_baseball: st.info("야구 분석 데이터 준비 중입니다.")
    with sub_basketball: st.info("농구 분석 데이터 준비 중입니다.")

# -----------------------------------------------------------------------------
# [TAB 4] AI 리포트 (오답노트 포함)
# -----------------------------------------------------------------------------
with main_tab4:
    stats = get_accuracy_stats()
    st.markdown(f"<div style='display:flex; align-items:center; gap:20px; margin-bottom:30px; background:#0B0F19; padding:20px; border-radius:12px; border:1px solid #1E293B;'><div><span style='color:#94A3B8; font-size:14px; font-weight:700; display:block;'>전체 누적 적중률</span><span style='color:#00F2FE; font-size:40px; font-weight:900;'>{stats['accuracy']}%</span></div><div style='border-left:1px solid #334155; padding-left:20px;'><span style='color:#CBD5E1; font-size:14px; display:block;'>종료된 경기: {stats['total']} 경기</span><span style='color:#10B981; font-size:14px; display:block; margin-top:5px;'>적중: {stats['correct']} 경기</span><span style='color:#EF4444; font-size:14px; display:block; margin-top:5px;'>실패: {stats['total'] - stats['correct']} 경기</span></div></div><h4 style='color:#F8FAFC; font-weight:900; margin-bottom:10px;'>📜 최근 경기 학습(오답) 노트</h4>", unsafe_allow_html=True)
    history = stats.get('history', [])
    if history:
        table_html = "<table style='width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; text-align: center;'><thead><tr><th style='background:#1E293B; color:#94A3B8; padding:10px;'>경기</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>예측</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>결과</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>채점</th></tr></thead><tbody>"
        for row in history:
            result_mark = "<span style='color:#10B981; font-weight:900;'>적중</span>" if row.get('is_correct',0) == 1 else "<span style='color:#EF4444; font-weight:900;'>실패</span>"
            table_html += f"<tr><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; font-weight:700;'>{row.get('home_team','')} vs {row.get('away_team','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #00F2FE;'>{row.get('predicted_pick','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; font-weight:900;'>{row.get('actual_score','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B;'>{result_mark}</td></tr>"
        table_html += "</tbody></table>"
        st.markdown(table_html, unsafe_allow_html=True)
        st.markdown("<h4 style='color:#F8FAFC; font-weight:900; margin-top:30px; margin-bottom:10px;'>💡 AI 실패 원인 분석 목록</h4>", unsafe_allow_html=True)
        has_failure = False
        for row in history:
            if row.get('is_correct',0) == 0 and row.get('failure_reason'):
                has_failure = True
                st.markdown(f"<div style='background:#1E293B; padding:12px; border-radius:6px; margin-bottom:8px; font-size:13px; color:#CBD5E1;'><b>[{row.get('home_team','')} vs {row.get('away_team','')}]</b><br>{row.get('failure_reason','')}</div>", unsafe_allow_html=True)
        if not has_failure: st.info("실패한 경기나 분석된 오답 노트가 아직 없습니다.")
    else: st.info("아직 채점이 완료된 종료 경기가 없습니다.")
