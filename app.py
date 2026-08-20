import os
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
GITHUB_REPO = "chleowhd77-ops/-"

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
    "우니베르시타테아 크라이오바": "Universitatea Craiova", "Ararat-Armenia": "Ararat-Armenia", "KF에그나티아": "KF Egnatia", "릴레스트룀SK": "Lillestrom",
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
    "시애사운": "Seattle Sounders", "오스틴FC": "Austin FC", "LA갤럭시": "Los Angeles Galaxy", 
    "새너어스": "San Jose Earthquakes", "새너제이 어스퀘이크스": "San Jose Earthquakes", "새너제이 어스케이크스": "San Jose Earthquakes",
    "포틀팀버": "Portland Timbers", "포틀랜드 팀버스": "Portland Timbers",
    "샌디에FC": "San Diego FC", "밴쿠화이": "Vancouver Whitecaps", "휴스다이": "Houston Dynamo"
}

# 👑 [API 제한 방지 패치] 확실한 진짜 공식 ID만 남기고, 모르는 건 로봇이 직접 찾게 함!
DIRECT_TEAM_INFO = {
    "새너제이 어스퀘이크스": {"id": 52, "logo": "https://media.api-sports.io/football/teams/52.png"},
    "새너제이 어스케이크스": {"id": 52, "logo": "https://media.api-sports.io/football/teams/52.png"},
    "새너어스": {"id": 52, "logo": "https://media.api-sports.io/football/teams/52.png"},
    "LDU키토": {"id": 2939, "logo": "https://media.api-sports.io/football/teams/2939.png"},
    "신트 트라위던VV": {"id": 742, "logo": "https://media.api-sports.io/football/teams/742.png"},
    "OFI크레타": {"id": 354, "logo": "https://media.api-sports.io/football/teams/354.png"},
    "V바렌 나가사키": {"id": 300, "logo": "https://media.api-sports.io/football/teams/300.png"},
    "야기엘로니아 비아위스토크": {"id": 338, "logo": "https://media.api-sports.io/football/teams/338.png"},
    "아라라트 아르메니아": {"id": 5316, "logo": "https://media.api-sports.io/football/teams/5316.png"},
    "KF에그나티아": {"id": 9789, "logo": "https://tmssl.akamaized.net/images/wappen/big/19491.png"},
    "이베리아1999 트빌리시": {"id": 2280, "logo": "https://media.api-sports.io/football/teams/2280.png"},
    "콜로라도 래피즈": {"id": 43, "logo": "https://media.api-sports.io/football/teams/43.png"},
    "LAFC": {"id": 41, "logo": "https://media.api-sports.io/football/teams/41.png"},
    "레알 솔트레이크": {"id": 45, "logo": "https://media.api-sports.io/football/teams/45.png"},
    "레알솔트레이크": {"id": 45, "logo": "https://media.api-sports.io/football/teams/45.png"},
    "FC댈러스": {"id": 46, "logo": "https://media.api-sports.io/football/teams/46.png"},
    "시애틀 사운더스FC": {"id": 48, "logo": "https://media.api-sports.io/football/teams/48.png"},
    "LA 갤럭시": {"id": 42, "logo": "https://media.api-sports.io/football/teams/42.png"},
    "LA갤럭시": {"id": 42, "logo": "https://media.api-sports.io/football/teams/42.png"},
    "포틀랜드 팀버스": {"id": 55, "logo": "https://media.api-sports.io/football/teams/55.png"},
    "밴쿠버 화이트캡스FC": {"id": 44, "logo": "https://media.api-sports.io/football/teams/44.png"},
    "휴스턴 다이너모FC": {"id": 49, "logo": "https://media.api-sports.io/football/teams/49.png"}
}
# 👑 [기획 패치] 웹사이트 구동 시, 로봇이 깃허브에 올려둔 '진짜' DB를 멱살 잡고 강제로 덮어씌움!
def download_db_from_github():
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/ai_predictions.db?t={int(time.time())}"
    try:
        res = requests.get(raw_url, timeout=5)
        if res.status_code == 200:
            with open("ai_predictions.db", "wb") as f:
                f.write(res.content)
    except: pass

download_db_from_github()

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
    
    try: cursor.execute("DELETE FROM api_cache WHERE cache_key LIKE 'team_info_%' AND cache_value LIKE '%null%'")
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
    if cached_data and cached_data.get("id") is not None: return cached_data
    
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
def fetch_overseas_odds_api(team_id):
    if not team_id: return None
    cache_key = f"odds_{team_id}"
    cached_data = get_db_cache(cache_key, 6)
    if cached_data: return cached_data
    try:
        res = requests.get(f"https://{API_HOST}/fixtures", headers=headers, params={"team": team_id, "next": 1}, timeout=5)
        data = res.json().get("response", [])
        if data:
            fix_id = data[0]["fixture"]["id"]
            odds_res = requests.get(f"https://{API_HOST}/odds", headers=headers, params={"fixture": fix_id}, timeout=5)
            odds_data = odds_res.json().get("response", [])
            if odds_data:
                bookmakers = odds_data[0].get("bookmakers", [])
                if bookmakers:
                    bets = bookmakers[0].get("bets", [])
                    for b in bets:
                        if b["name"] == "Match Winner":
                            vals = b["values"]
                            res_val = {"odd_h": float(vals[0]["odd"]), "odd_d": float(vals[1]["odd"]), "odd_a": float(vals[2]["odd"])}
                            set_db_cache(cache_key, res_val)
                            return res_val
    except: pass
    return None

@st.cache_data(ttl=43200)
def fetch_fixture_details_api(home_id, away_id):
    default_res = {"match_time": None, "last_h2h_date": "-", "h_rest": "-", "a_rest": "-", "h_wins": 0, "draws": 0, "a_wins": 0, "total": 0}
    if not home_id or not away_id: return default_res
    cache_key = f"app_h2h_{home_id}_{away_id}"
    cached_data = get_db_cache(cache_key, 24)
    if cached_data: return cached_data
    try:
        response = requests.get(f"https://{API_HOST}/fixtures/headtohead", headers=headers, params={"h2h": f"{home_id}-{away_id}"}, timeout=5)
        matches = response.json().get("response", [])
        h_wins, draws, a_wins = 0, 0, 0
        for m in matches[:10]:
            if m.get("teams", {}).get("home", {}).get("winner"):
                if m.get("teams", {}).get("home", {}).get("id") == home_id: h_wins += 1
                else: a_wins += 1
            elif m.get("teams", {}).get("away", {}).get("winner"):
                if m.get("teams", {}).get("home", {}).get("id") == home_id: a_wins += 1
                else: h_wins += 1
            else: draws += 1
        res_val = {"match_time": None, "last_h2h_date": "-", "h_rest": "-", "a_rest": "-", "h_wins": h_wins, "draws": draws, "a_wins": a_wins, "total": len(matches[:10])}
        set_db_cache(cache_key, res_val)
        return res_val
    except: return default_res

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
            
    history_list = df_history.to_dict('records')
    
    if len(df) == 0: return {"total": 0, "correct": 0, "accuracy": 0.0, "history": history_list, "scoring": scoring_list}
    return {"total": len(df), "correct": df['is_correct'].sum(), "accuracy": round((df['is_correct'].sum() / len(df)) * 100, 1), "history": history_list, "scoring": scoring_list}

def save_prediction(m, best_option, best_prob_pct, best_score, is_toto14=0):
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM predictions WHERE match_id = ?", (m['id'],))
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO predictions (match_id, league, home_team, away_team, predicted_pick, predicted_prob, expected_score, odd_h, odd_d, odd_a, match_time, is_toto14)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (m['id'], m.get('league', '승무패 14경기'), m['home'], m['away'], best_option, best_prob_pct, f"{best_score[0]}:{best_score[1]}", m.get('odd_h', 0.0), m.get('odd_d', 0.0), m.get('odd_a', 0.0), m.get('match_time', ''), is_toto14))
        else: cursor.execute("UPDATE predictions SET is_toto14 = ? WHERE match_id = ?", (is_toto14, m['id']))
        conn.commit()
    except: pass
    finally: conn.close()

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

def generate_match_story(best_option, math_exp_h, math_exp_a, prob_h, prob_d, prob_a, h2h_h, h2h_a, home, away, odd_h, odd_a, h_form, a_form, h_long, a_long, h_inj_data, a_inj_data, h_rest, a_rest, h_next, a_next, h_rank, a_rank, h_market, a_market, h_stats, a_stats):
    story_parts = []
    
    story_parts.append(f"📈 [포아송 수학 모델] 양 팀의 공격/수비 지수를 환산한 결과, 예상 정규시간 득점은 {home} <b style='color:#00F2FE;'>{math_exp_h:.1f}골</b>, {away} <b style='color:#EF4444;'>{math_exp_a:.1f}골</b>로 산출되었습니다.")
    
    if h_next["is_important"] and h_next["days_until_next"] <= 4:
        story_parts.append(f"⚠️ [로테이션 경보] {home}은(는) 불과 {h_next['days_until_next']}일 뒤에 열리는 '{h_next['league_name']}' 대회를 대비해 핵심 선수들을 대거 쉬게 할(힘 빼기) 확률이 매우 높습니다. 강팀의 뜬금없는 이변(함정 픽)에 각별히 주의하세요!")
    if a_next["is_important"] and a_next["days_until_next"] <= 4:
        story_parts.append(f"⚠️ [로테이션 경보] 원정팀 {away} 측에 {a_next['days_until_next']}일 뒤 '{a_next['league_name']}' 중요 일정이 겹쳐 있어, 정상적인 100% 전력 가동이 불투명합니다. 역배당 이변의 희생양이 될 수 있습니다.")

    if h_market > 0: story_parts.append(f"💸 [마켓 알럿] 글로벌 도박사들의 거액 자금이 {home} 승리 쪽으로 몰리며 배당이 폭락 중입니다.")
    elif a_market > 0: story_parts.append(f"💸 [마켓 알럿] 시장 흐름상 {away} 승리에 자금이 쏠리며 원정팀 배당 가치가 급락하고 있습니다.")

    if h_rank <= 3 and a_rank >= 10: story_parts.append(f"🏆 {home}은(는) 상위권(리그 {h_rank}위)을 질주 중인 반면, {away}은(는) {a_rank}위로 체급 차이가 명확합니다.")
    elif h_rank >= 15 and h_rank != 99: story_parts.append(f"🔥 {home}은(는) 현재 {h_rank}위로 강등권 위기에 처해 있어 사활을 건 '생존 버프'가 발동될 수 있습니다.")
    elif a_rank >= 15 and a_rank != 99: story_parts.append(f"🔥 원정팀 {away}({a_rank}위)은(는) 벼랑 끝에 몰린 강등권으로 끈질긴 저항이 우려됩니다.")
    
    h_inj = h_inj_data['count']
    if h_inj_data['ace_missing']: story_parts.append(f"🚨 [초비상] {home}의 전력 핵심이자 주득점원({', '.join(h_inj_data['ace_names'])})의 결장이 의심되어 득점력에 치명적인 타격이 예상됩니다.")
    elif h_inj >= 6: story_parts.append(f"🚨 삐용삐용! {home}에 다수의 결장 의심 선수가 발생해 전력 누수가 매우 심각합니다.")
    elif h_inj >= 1: story_parts.append(f"🏥 {home}에 {h_inj}명의 부상자가 있으나, 로테이션 자원 위주라 베스트 11 전력에는 큰 누수가 없습니다.")
        
    a_inj = a_inj_data['count']
    if a_inj_data['ace_missing']: story_parts.append(f"🚨 [초비상] 원정팀 {away} 측 핵심 주득점원({', '.join(a_inj_data['ace_names'])})의 결장이 의심되어 고전이 예상됩니다.")
    elif a_inj >= 6: story_parts.append(f"🚨 삐용삐용! 원정팀 {away}에 다수의 결장 의심 선수가 확인되어 정상적인 경기 운영이 어렵습니다.")
    elif a_inj >= 1: story_parts.append(f"🏥 원정팀 {away} 측에 {a_inj}명의 부상자가 있지만 치명적인 전력 누수는 피했습니다.")

    if h_rest <= 3: story_parts.append(f"💦 {home}은(는) 휴식일이 3일 이하로 짧아 후반전 체력 방전이 우려됩니다.")
    if a_rest <= 3: story_parts.append(f"💦 원정팀 {away}은(는) 빡빡한 일정 탓에 체력적인 부담을 안고 경기에 임합니다.")
    
    if h_stats['possession'] >= 60.0: story_parts.append(f"📊 [경기력 지표] {home}은(는) 최근 평균 60% 이상의 압도적인 점유율로 경기를 지배하고 있습니다.")
    elif h_stats['possession'] <= 35.0 and "승" in h_form: story_parts.append(f"⚠️ [위험 경보] {home}은(는) 최근 승리는 있지만 점유율이 밀리고 있어 폼 거품일 확률이 존재합니다.")
    if h_stats['shots_on_goal'] >= 6.0: story_parts.append(f"🎯 [xG 데이터] {home}은(는) 매 경기 날카로운 유효 슈팅을 창출하며 득점 기대값(xG)이 매우 높습니다.")
    
    if a_stats['possession'] >= 60.0: story_parts.append(f"📊 [경기력 지표] 원정팀 {away} 역시 강한 압박과 높은 점유율을 바탕으로 주도권을 쥐는 데 능합니다.")
    if a_stats['shots_on_goal'] >= 6.0: story_parts.append(f"🎯 [xG 데이터] {away}의 원정 유효 슈팅 창출력이 매서워 역습에 의한 실점을 경계해야 합니다.")

    if "승-승" in h_form: story_parts.append(f"🔥 단기 폼 측면에서 {home}이(가) 최근 쾌조의 연승으로 최고조에 달했습니다.")
    elif "패-패" in h_form: story_parts.append(f"💧 {home}은(는) 최근 연패의 늪에 빠져 수비 정비가 시급합니다.")
    if "승-승" in a_form: story_parts.append(f"🚀 원정팀 {away} 역시 매서운 연승 기세를 보여주고 있어 방심할 수 없는 상대입니다.")
    
    total_h2h = h2h_h + h2h_a
    if total_h2h >= 3:
        if h2h_h > h2h_a + 2: story_parts.append(f"⚔️ 상대전적에서도 {home}이(가) 확실한 천적 관계를 형성하며 자신감을 보이고 있습니다.")
        elif h2h_a > h2h_h + 2: story_parts.append(f"⚔️ {away}이(가) 원정임에도 상대전적에서 압도적인 우위를 점하고 있습니다.")
            
    if best_option == "무승부":
        story_parts.append(f"🤖 결론적으로 AI는 팽팽한 흐름 속에 진흙탕 무승부 가능성을 가장 높게 예측합니다.")
    elif best_option == f"{home} 승":
        if prob_h >= 50: story_parts.append(f"🤖 결론적으로 AI는 각종 지표의 우위를 바탕으로 {home}의 무난한 승리를 예측합니다.")
        else: story_parts.append(f"🤖 결론적으로 AI는 치열한 접전 끝에 {home}의 신승(진땀승)을 예측합니다.")
    elif best_option == f"{away} 승":
        if prob_a >= 50: story_parts.append(f"🤖 결론적으로 AI는 전력차를 바탕으로 원정팀 {away}의 승리 가능성을 높게 평가합니다.")
        else: story_parts.append(f"🤖 결론적으로 AI는 팽팽한 승부 속 원정팀 {away}의 짜릿한 신승을 예측합니다.")
        
    return " ".join(story_parts)

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
        try: o_h = float(m.get("odd_h", 0.0))
        except: o_h = 0.0
        odd_h = o_h if o_h > 0.0 else 2.0
        
        try: o_d = float(m.get("odd_d", 0.0))
        except: o_d = 0.0
        odd_d = o_d if o_d > 0.0 else 3.0
        
        try: o_a = float(m.get("odd_a", 0.0))
        except: o_a = 0.0
        odd_a = o_a if o_a > 0.0 else 2.0
        
        handi_h = float(m.get("handi_h")) if m.get("handi_h") is not None else 3.05
        handi_a = float(m.get("handi_a")) if m.get("handi_a") is not None else 2.03
        uo_under = float(m.get("uo_under")) if m.get("uo_under") is not None else 1.50
        uo_over = float(m.get("uo_over")) if m.get("uo_over") is not None else 2.13

        home_team, away_team = m["home"], m["away"]
        home_info = fetch_team_info_api(home_team)
        away_info = fetch_team_info_api(away_team)
        fixture_details = fetch_fixture_details_api(home_info["id"], away_info["id"])
        final_match_time = m.get("match_time") or m.get("time") or "시간 미정"
        
        h_stand = fetch_team_standing_api(home_info.get("id"))
        a_stand = fetch_team_standing_api(away_info.get("id"))
        h_rank, a_rank = h_stand["rank"], a_stand["rank"]
        
        os_odds = fetch_overseas_odds_api(home_info.get("id"))
        h_market_bonus, a_market_bonus = 0.0, 0.0
        if os_odds and odd_h > 1.0 and odd_a > 1.0:
            if os_odds["odd_h"] < odd_h - 0.2: h_market_bonus = 0.25
            if os_odds["odd_a"] < odd_a - 0.2: a_market_bonus = 0.25
            
        rank_diff_bonus_h = max(-0.3, min(0.3, (a_rank - h_rank) * 0.02))
        rank_diff_bonus_a = max(-0.3, min(0.3, (h_rank - a_rank) * 0.02))
        h_desperation = 0.15 if h_rank >= 15 and h_rank != 99 else 0.0
        a_desperation = 0.15 if a_rank >= 15 and a_rank != 99 else 0.0
        
        h_inj_data = fetch_team_injuries_api(home_info.get("id"), h_stand.get("league_id"), h_stand.get("season"))
        a_inj_data = fetch_team_injuries_api(away_info.get("id"), a_stand.get("league_id"), a_stand.get("season"))
        
        h_inj_count = h_inj_data["count"]
        a_inj_count = a_inj_data["count"]
        
        if h_inj_data["ace_missing"]: h_injury_penalty = 0.60
        else: h_injury_penalty = 0.40 if h_inj_count >= 6 else (0.20 if h_inj_count >= 3 else (0.10 if h_inj_count >= 1 else 0.0))
        
        if a_inj_data["ace_missing"]: a_injury_penalty = 0.60
        else: a_injury_penalty = 0.40 if a_inj_count >= 6 else (0.20 if a_inj_count >= 3 else (0.10 if a_inj_count >= 1 else 0.0))
        
        h_last_date = fetch_team_last_match_date_api(home_info.get("id"))
        a_last_date = fetch_team_last_match_date_api(away_info.get("id"))
        h_rest_days = calculate_rest_days(h_last_date, final_match_time)
        a_rest_days = calculate_rest_days(a_last_date, final_match_time)
        h_fatigue_penalty = 0.15 if h_rest_days <= 3 else 0.0
        a_fatigue_penalty = 0.15 if a_rest_days <= 3 else 0.0
        
        h_next = fetch_team_next_fixture_api(home_info.get("id"))
        a_next = fetch_team_next_fixture_api(away_info.get("id"))
        
        h_rot_penalty = 0.3 if h_next["is_important"] and h_next["days_until_next"] <= 4 else 0.0
        a_rot_penalty = 0.3 if a_next["is_important"] and a_next["days_until_next"] <= 4 else 0.0
        
        h_long = fetch_team_long_term_stats_api(home_info.get("id"))
        a_long = fetch_team_long_term_stats_api(away_info.get("id"))
        
        AVG_H_GF, AVG_A_GF = 1.5, 1.2
        AVG_H_GA, AVG_A_GA = 1.2, 1.5
        
        h_tot = max(1, h_long["home_total"])
        a_tot = max(1, a_long["away_total"])
        
        HAS = (h_long["home_gf"] / h_tot) / AVG_H_GF
        HDS = (h_long["home_ga"] / h_tot) / AVG_H_GA
        AAS = (a_long["away_gf"] / a_tot) / AVG_A_GF
        ADS = (a_long["away_ga"] / a_tot) / AVG_A_GA
        
        math_exp_h = HAS * ADS * AVG_H_GF
        math_exp_a = AAS * HDS * AVG_A_GF

        h_stats = fetch_recent_team_stats_api(home_info.get("id"))
        a_stats = fetch_recent_team_stats_api(away_info.get("id"))
        
        h_xg_bonus = max(-0.2, min(0.3, ((h_stats['possession'] - 50) * 0.01) + ((h_stats['shots_on_goal'] - 4.0) * 0.05)))
        a_xg_bonus = max(-0.2, min(0.3, ((a_stats['possession'] - 50) * 0.01) + ((a_stats['shots_on_goal'] - 4.0) * 0.05)))
        
        p_h = (1 / odd_h) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
        p_a = (1 / odd_a) / ((1 / odd_h) + (1 / odd_d) + (1 / odd_a))
        
        h2h_total = fixture_details.get("total", 0)
        h_h2h_bonus = (fixture_details.get("h_wins", 0) / h2h_total * 0.3) if h2h_total > 0 else 0
        a_h2h_bonus = (fixture_details.get("a_wins", 0) / h2h_total * 0.3) if h2h_total > 0 else 0
        
        odds_exp_h = p_h * 2.8
        odds_exp_a = p_a * 2.8
        
        exp_h = round(max(0.3, (math_exp_h * 0.6) + (odds_exp_h * 0.4) + h_h2h_bonus + rank_diff_bonus_h + h_desperation + h_market_bonus + h_xg_bonus - h_injury_penalty - h_fatigue_penalty - h_rot_penalty), 2)
        exp_a = round(max(0.3, (math_exp_a * 0.6) + (odds_exp_a * 0.4) + a_h2h_bonus - a_injury_penalty - a_fatigue_penalty - a_rot_penalty + rank_diff_bonus_a + a_desperation + a_market_bonus + a_xg_bonus), 2)
        
        handi_val = 1.0 if odd_h > odd_a else -1.0
        h_win, draw, a_win, prob_u, prob_o, prob_handi_h, prob_handi_a = calculate_poisson_probs(exp_h, exp_a, handi_val)

        candidates = [(f"{home_team} 승", h_win, h_win * odd_h), (f"{away_team} 승", a_win, a_win * odd_a), (f"무승부", draw, draw * odd_d)]
        best_option, best_prob, best_ev = max(candidates, key=lambda x: x[2])
        best_prob_pct = round(best_prob * 100, 1)
        best_handi = f"{home_team} 핸디승" if prob_handi_h * handi_h > prob_handi_a * handi_a else f"{away_team} 핸디승"
        best_handi_prob = round(max(prob_handi_h, prob_handi_a) * 100, 1)
        best_uo = "언더 (U 2.5)" if prob_u * uo_under > prob_o * uo_over else "오버 (O 2.5)"
        best_uo_prob = round(max(prob_u, prob_o) * 100, 1)

        save_prediction(m, best_option, best_prob_pct, (0,0), 0)
        h_form = fetch_team_form_api(home_info.get("id"))
        a_form = fetch_team_form_api(away_info.get("id"))

        story = generate_match_story(best_option, math_exp_h, math_exp_a, h_win*100, draw*100, a_win*100, fixture_details.get('h_wins', 0), fixture_details.get('a_wins', 0), home_team, away_team, odd_h, odd_a, h_form, a_form, h_long, a_long, h_inj_data, a_inj_data, h_rest_days, a_rest_days, h_next, a_next, h_rank, a_rank, h_market_bonus, a_market_bonus, h_stats, a_stats)

        analyzed_proto.append({
            "match": m, "final_match_time": final_match_time, "home_logo": home_info.get("logo"), "away_logo": away_info.get("logo"),
            "h2h": fixture_details, "story": story, "best_option": best_option, "best_prob_pct": best_prob_pct,
            "best_handi": best_handi, "best_handi_prob": best_handi_prob, "best_uo": best_uo, "best_uo_prob": best_uo_prob, "best_ev": best_ev,
            "home_form": h_form, "away_form": a_form,
            "h_inj_data": h_inj_data, "a_inj_data": a_inj_data,
            "h_rest": h_rest_days, "a_rest": a_rest_days,
            "h_next": h_next, "a_next": a_next,
            "h_rank": h_rank, "a_rank": a_rank,
            "h_market": h_market_bonus, "a_market": a_market_bonus
        })

# -----------------------------------------------------------------------------
# [TAB 1] 프로토 LIVE
# -----------------------------------------------------------------------------
with main_tab1:
    sub_soccer, sub_baseball, sub_basketball = st.tabs(["축구", "야구", "농구"])
    with sub_soccer:
        st.markdown("<div style='background:rgba(0, 242, 254, 0.1); border:1px solid #00F2FE; color:#00F2FE; padding:12px; border-radius:8px; text-align:center; font-weight:700; margin-bottom:24px;'>💡 안내: 완전히 채점이 완료된 종료 경기는 [AI 리포트] 탭의 오답노트에 영구 보관됩니다.</div>", unsafe_allow_html=True)
        
        if analyzed_proto:
            displayed_count = 0
            for item in analyzed_proto:
                m = item['match']
                logo_h_tag = render_logo_html(item.get("home_logo"))
                logo_a_tag = render_logo_html(item.get("away_logo"))
                raw_deadline = m.get("deadline_time", "23:00")
                match_status, is_closed = get_match_status(item["final_match_time"], raw_deadline)
                
                a_result = m.get('actual_result', 'PENDING')
                
                if match_status == "FINISHED" or a_result == 'FINISHED':
                    continue
                
                displayed_count += 1
                match_id_str = str(m.get('id', ''))
                
                if match_status == "LIVE" or m.get('match_time') == '마감/진행중':
                    if match_id_str in live_scores_data:
                        live_info = live_scores_data[match_id_str]
                        score_text = live_info.get("score", "진행중")
                        event_text = live_info.get("event", "")
                        
                        event_html = f"<div style='margin-bottom:6px; font-size:11px; color:#10B981; font-weight:900;'>{event_text}</div>" if event_text else ""
                        time_display = f"{event_html}<span class='live-score'>{score_text}</span><span class='deadline-closed' style='background:rgba(239, 68, 68, 0.1); border-color:#EF4444; color:#EF4444; animation: blink 2s infinite;'>🔴 LIVE</span>"
                    else:
                        time_display = f"<span class='live-score'>진행중</span><span class='deadline-closed' style='background:rgba(239, 68, 68, 0.1); border-color:#EF4444; color:#EF4444; animation: blink 2s infinite;'>🔴 LIVE</span>"
                else:
                    badge = f"<span class='deadline-closed'>픽 마감</span>" if is_closed else f"<span class='deadline-open'>{raw_deadline}</span>"
                    time_display = f"<span class='match-time-text'>{item['final_match_time']}</span>{badge}"
                
                o_h_disp = m.get('odd_h') if m.get('odd_h') not in [None, 0.0, '', '-'] else '-'
                o_d_disp = m.get('odd_d') if m.get('odd_d') not in [None, 0.0, '', '-'] else '-'
                o_a_disp = m.get('odd_a') if m.get('odd_a') not in [None, 0.0, '', '-'] else '-'
                
                h_form = item.get('home_form', '')
                a_form = item.get('away_form', '')
                
                h_inj_data = item.get('h_inj_data', {'count': 0, 'ace_missing': False})
                a_inj_data = item.get('a_inj_data', {'count': 0, 'ace_missing': False})
                h_inj = h_inj_data['count']
                a_inj = a_inj_data['count']
                
                if h_inj_data['ace_missing']: h_inj_html = f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 에이스 결장 비상!(-0.6)</div>"
                elif h_inj > 0: h_inj_html = f"<div class='injury-badge'>🏥 부상 페널티: {'3단계(MAX)' if h_inj >= 6 else ('2단계(-0.2)' if h_inj >= 3 else '1단계(-0.1)')}</div>"
                else: h_inj_html = ""

                if a_inj_data['ace_missing']: a_inj_html = f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 에이스 결장 비상!(-0.6)</div>"
                elif a_inj > 0: a_inj_html = f"<div class='injury-badge'>🏥 부상 페널티: {'3단계(MAX)' if a_inj >= 6 else ('2단계(-0.2)' if a_inj >= 3 else '1단계(-0.1)')}</div>"
                else: a_inj_html = ""
                
                h_rest = item.get('h_rest', 99)
                a_rest = item.get('a_rest', 99)
                h_rest_html = f"<div class='fatigue-badge'>💦 체력 페널티: 방전 (-0.15)</div>" if h_rest <= 3 else ""
                a_rest_html = f"<div class='fatigue-badge'>💦 체력 페널티: 방전 (-0.15)</div>" if a_rest <= 3 else ""

                h_next = item.get('h_next', {"is_important": False, "days_until_next": 99})
                a_next = item.get('a_next', {"is_important": False, "days_until_next": 99})
                h_rot_html = f"<div class='fatigue-badge' style='background: rgba(245,158,11,0.2); border-color: #F59E0B; color: #F59E0B;'>⚠️ 로테이션 경보!(-0.3)</div>" if h_next["is_important"] and h_next["days_until_next"] <= 4 else ""
                a_rot_html = f"<div class='fatigue-badge' style='background: rgba(245,158,11,0.2); border-color: #F59E0B; color: #F59E0B;'>⚠️ 로테이션 경보!(-0.3)</div>" if a_next["is_important"] and a_next["days_until_next"] <= 4 else ""

                h_rank, a_rank = item.get('h_rank', 99), item.get('a_rank', 99)
                h_rank_html = f"<div class='rank-badge'>🏆 리그 순위: {h_rank}위 {'(🔥강등버프)' if h_rank >= 15 else ''}</div>" if h_rank != 99 else ""
                a_rank_html = f"<div class='rank-badge'>🏆 리그 순위: {a_rank}위 {'(🔥강등버프)' if a_rank >= 15 else ''}</div>" if a_rank != 99 else ""
                
                h_market, a_market = item.get('h_market', 0.0), item.get('a_market', 0.0)
                h_money_html = f"<div class='money-badge'>💸 해외 마켓 몰림 (홈 승)</div>" if h_market > 0 else ""
                a_money_html = f"<div class='money-badge'>💸 해외 마켓 몰림 (원정 승)</div>" if a_market > 0 else ""
                
                html_code = (
                    f"<div class='match-card'>"
                    f"<div class='league-title'>{m.get('league','축구')}</div>"
                    f"<div class='vs-row'>"
                    f"<div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{h_form}</div>{h_rank_html}{h_money_html}{h_inj_html}{h_rest_html}{h_rot_html}</div>{logo_h_tag}</div>"
                    f"<div class='center-time-box'>{time_display}</div>"
                    f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{a_form}</div>{a_rank_html}{a_money_html}{a_inj_html}{a_rest_html}{a_rot_html}</div></div>"
                    f"</div>"
                    f"<div class='ai-story'>{item.get('story','')}</div>"
                    f"<div class='odd-bar'><span class='odd-item'>승 <span class='odd-val'>{o_h_disp}</span> | 무 <span class='odd-val'>{o_d_disp}</span> | 패 <span class='odd-val'>{o_a_disp}</span></span><span class='odd-item'>핸디캡 <span class='odd-val'>{m.get('handi_h', '-')} / {m.get('handi_a', '-')}</span></span><span class='odd-item'>언오버 <span class='odd-val'>{m.get('uo_under', '-')} / {m.get('uo_over', '-')}</span></span></div>"
                    f"<div class='pred-grid'><div class='pred-box'><div class='pred-label'>승무패 예측</div><span class='pred-value'>{item.get('best_option','')}</span> <span class='pred-prob'>{item.get('best_prob_pct','0')}%</span></div><div class='pred-box'><div class='pred-label'>핸디캡 예측</div><span class='pred-value'>{item.get('best_handi','')}</span> <span class='pred-prob'>{item.get('best_handi_prob','0')}%</span></div><div class='pred-box'><div class='pred-label'>언더/오버 예측</div><span class='pred-value'>{item.get('best_uo','')}</span> <span class='pred-prob'>{item.get('best_uo_prob','0')}%</span></div></div>"
                    f"</div>"
                )
                st.markdown(html_code, unsafe_allow_html=True)
            
            if displayed_count == 0:
                st.info("현재 진행 중이거나 예정된 경기가 없습니다. 종료된 경기는 [AI 리포트] 탭을 확인해주세요.")
        else: st.info("현재 진행 중이거나 예정된 축구 경기가 없습니다. 종료된 경기는 [AI 리포트] 탭을 확인해주세요.")
    with sub_baseball: st.info("야구 분석 데이터 준비 중입니다.")
    with sub_basketball: st.info("농구 분석 데이터 준비 중입니다.")

# -----------------------------------------------------------------------------
# [TAB 2] 승무패 14경기
# -----------------------------------------------------------------------------
with main_tab2:
    st.markdown("<p style='color:#64748B; font-weight:700; margin-bottom:20px;'>승무패 14폴더 AI 확률 분포 (복수 마킹 참고용)</p>", unsafe_allow_html=True)
    if toto_14_raw:
        total_combinations = 1
        double_pick_count = 0
        match_html_list = []
        for idx, m in enumerate(toto_14_raw, 1):
            home_info = fetch_team_info_api(m['home'])
            away_info = fetch_team_info_api(m['away'])
            logo_h_tag = render_logo_html(home_info.get("logo"))
            logo_a_tag = render_logo_html(away_info.get("logo"))
            
            h_form = fetch_team_form_api(home_info.get("id"))
            a_form = fetch_team_form_api(away_info.get("id"))
            
            h_stand = fetch_team_standing_api(home_info.get("id"))
            a_stand = fetch_team_standing_api(away_info.get("id"))
            h_rank, a_rank = h_stand["rank"], a_stand["rank"]
            
            os_odds = fetch_overseas_odds_api(home_info.get("id"))
            h_market_bonus, a_market_bonus = 0.0, 0.0
            if os_odds:
                if os_odds["odd_h"] <= 1.8: h_market_bonus = 0.25
                if os_odds["odd_a"] <= 1.8: a_market_bonus = 0.25
                
            rank_diff_bonus_h = max(-0.3, min(0.3, (a_rank - h_rank) * 0.02))
            rank_diff_bonus_a = max(-0.3, min(0.3, (h_rank - a_rank) * 0.02))
            h_desperation = 0.15 if h_rank >= 15 and h_rank != 99 else 0.0
            a_desperation = 0.15 if a_rank >= 15 and a_rank != 99 else 0.0
            
            h_long = fetch_team_long_term_stats_api(home_info.get("id"))
            a_long = fetch_team_long_term_stats_api(away_info.get("id"))
            
            h_last_date = fetch_team_last_match_date_api(home_info.get("id"))
            a_last_date = fetch_team_last_match_date_api(away_info.get("id"))
            h_rest_days = calculate_rest_days(h_last_date, "시간 미정")
            a_rest_days = calculate_rest_days(a_last_date, "시간 미정")
            h_fatigue_penalty = 0.15 if h_rest_days <= 3 else 0.0
            a_fatigue_penalty = 0.15 if a_rest_days <= 3 else 0.0

            h_next = fetch_team_next_fixture_api(home_info.get("id"))
            a_next = fetch_team_next_fixture_api(away_info.get("id"))
            h_rot_penalty = 0.3 if h_next["is_important"] and h_next["days_until_next"] <= 4 else 0.0
            a_rot_penalty = 0.3 if a_next["is_important"] and a_next["days_until_next"] <= 4 else 0.0

            h_inj_data = fetch_team_injuries_api(home_info.get("id"), h_stand.get("league_id"), h_stand.get("season"))
            a_inj_data = fetch_team_injuries_api(away_info.get("id"), a_stand.get("league_id"), a_stand.get("season"))
            h_inj_count = h_inj_data["count"]
            a_inj_count = a_inj_data["count"]
            
            if h_inj_data["ace_missing"]: h_injury_penalty = 0.60
            else: h_injury_penalty = 0.40 if h_inj_count >= 6 else (0.20 if h_inj_count >= 3 else (0.10 if h_inj_count >= 1 else 0.0))
            
            if a_inj_data["ace_missing"]: a_injury_penalty = 0.60
            else: a_injury_penalty = 0.40 if a_inj_count >= 6 else (0.20 if a_inj_count >= 3 else (0.10 if a_inj_count >= 1 else 0.0))

            AVG_H_GF, AVG_A_GF = 1.5, 1.2
            AVG_H_GA, AVG_A_GA = 1.2, 1.5
            
            h_tot = max(1, h_long["home_total"])
            a_tot = max(1, a_long["away_total"])
            
            HAS = (h_long["home_gf"] / h_tot) / AVG_H_GF
            HDS = (h_long["home_ga"] / h_tot) / AVG_H_GA
            AAS = (a_long["away_gf"] / a_tot) / AVG_A_GF
            ADS = (a_long["away_ga"] / a_tot) / AVG_A_GA
            
            math_exp_h = HAS * ADS * AVG_H_GF
            math_exp_a = AAS * HDS * AVG_A_GF
            
            h_stats = fetch_recent_team_stats_api(home_info.get("id"))
            a_stats = fetch_recent_team_stats_api(away_info.get("id"))
            
            h_xg_bonus = max(-0.2, min(0.3, ((h_stats['possession'] - 50) * 0.01) + ((h_stats['shots_on_goal'] - 4.0) * 0.05)))
            a_xg_bonus = max(-0.2, min(0.3, ((a_stats['possession'] - 50) * 0.01) + ((a_stats['shots_on_goal'] - 4.0) * 0.05)))

            fixture_details = fetch_fixture_details_api(home_info.get("id"), away_info.get("id"))
            h2h_total = fixture_details.get("total", 0)
            h_h2h_bonus = (fixture_details.get("h_wins", 0) / h2h_total * 0.4) if h2h_total > 0 else 0.15
            a_h2h_bonus = (fixture_details.get("a_wins", 0) / h2h_total * 0.4) if h2h_total > 0 else 0.15
            
            p_h, p_d, p_a = 0.34, 0.33, 0.33 # Default
            if os_odds:
                total_o = (1/os_odds["odd_h"]) + (1/os_odds["odd_d"]) + (1/os_odds["odd_a"])
                p_h = (1/os_odds["odd_h"]) / total_o
                p_a = (1/os_odds["odd_a"]) / total_o
            
            odds_exp_h = p_h * 2.8
            odds_exp_a = p_a * 2.8
            
            exp_h = round(max(0.3, (math_exp_h * 0.6) + (odds_exp_h * 0.4) + h_h2h_bonus - h_injury_penalty - h_fatigue_penalty - h_rot_penalty + rank_diff_bonus_h + h_desperation + h_market_bonus + h_xg_bonus), 2)
            exp_a = round(max(0.3, (math_exp_a * 0.6) + (odds_exp_a * 0.4) + a_h2h_bonus - a_injury_penalty - a_fatigue_penalty - a_rot_penalty + rank_diff_bonus_a + a_desperation + a_market_bonus + a_xg_bonus), 2)
            
            h_win, draw, a_win, _, _, _, _ = calculate_poisson_probs(exp_h, exp_a)
            total_p = h_win + draw + a_win
            if total_p > 0:
                p_h = round((h_win / total_p) * 100, 1)
                p_d = round((draw / total_p) * 100, 1)
                p_a = round(100.0 - p_h - p_d, 1)
            else:
                p_h, p_d, p_a = 34.0, 33.0, 33.0
            
            if h_inj_data['ace_missing']: h_inj_html = f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 에이스 결장 비상!(-0.6)</div>"
            elif h_inj_count > 0: h_inj_html = f"<div class='injury-badge'>🏥 부상 페널티: {'3단계(MAX)' if h_inj_count >= 6 else ('2단계(-0.2)' if h_inj_count >= 3 else '1단계(-0.1)')}</div>"
            else: h_inj_html = ""

            if a_inj_data['ace_missing']: a_inj_html = f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 에이스 결장 비상!(-0.6)</div>"
            elif a_inj_count > 0: a_inj_html = f"<div class='injury-badge'>🏥 부상 페널티: {'3단계(MAX)' if a_inj_count >= 6 else ('2단계(-0.2)' if a_inj_count >= 3 else '1단계(-0.1)')}</div>"
            else: a_inj_html = ""
            
            h_rest_html = f"<div class='fatigue-badge'>💦 체력 페널티: 방전 (-0.15)</div>" if h_rest_days <= 3 else ""
            a_rest_html = f"<div class='fatigue-badge'>💦 체력 페널티: 방전 (-0.15)</div>" if a_rest_days <= 3 else ""
            
            h_rot_html = f"<div class='fatigue-badge' style='background: rgba(245,158,11,0.2); border-color: #F59E0B; color: #F59E0B;'>⚠️ 로테이션 경보!(-0.3)</div>" if h_next["is_important"] and h_next["days_until_next"] <= 4 else ""
            a_rot_html = f"<div class='fatigue-badge' style='background: rgba(245,158,11,0.2); border-color: #F59E0B; color: #F59E0B;'>⚠️ 로테이션 경보!(-0.3)</div>" if a_next["is_important"] and a_next["days_until_next"] <= 4 else ""

            h_rank_html = f"<div class='rank-badge'>🏆 리그 순위: {h_rank}위 {'(🔥강등버프)' if h_rank >= 15 else ''}</div>" if h_rank != 99 else ""
            a_rank_html = f"<div class='rank-badge'>🏆 리그 순위: {a_rank}위 {'(🔥강등버프)' if a_rank >= 15 else ''}</div>" if a_rank != 99 else ""
            h_market_bonus, a_market_bonus = 0.0, 0.0 # reset for display
            h_money_html = f"<div class='money-badge'>💸 해외 마켓 몰림 (홈 승)</div>" if h_market_bonus > 0 else ""
            a_money_html = f"<div class='money-badge'>💸 해외 마켓 몰림 (원정 승)</div>" if a_market_bonus > 0 else ""
            
            probs = {"승": p_h, "무": p_d, "패": p_a}
            sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)
            first_pick, first_pct = sorted_probs[0]
            second_pick, second_pct = sorted_probs[1]
            
            picks = []
            if first_pct - second_pct <= 7.0:
                if set([first_pick, second_pick]) == set(["승", "패"]):
                    second_pick = "무"
                picks = [first_pick, second_pick]
                total_combinations *= 2
                double_pick_count += 1
            else:
                picks = [first_pick]
            
            disp_texts = []
            for p in picks:
                if p == "승": disp_texts.append(f"{m['home']} 승")
                elif p == "패": disp_texts.append(f"{m['away']} 승")
                else: disp_texts.append("무승부")
            best_pick_display = ", ".join(disp_texts)
            
            is_h = "승" in picks
            is_d = "무" in picks
            is_a = "패" in picks
            style_h = "background: #00F2FE; color: #0B0F19; font-weight: 900; border: 1px solid #00F2FE;" if is_h else "background: transparent; color: #64748B; border: 1px solid #1E293B;"
            style_d = "background: #10B981; color: #0B0F19; font-weight: 900; border: 1px solid #10B981;" if is_d else "background: transparent; color: #64748B; border: 1px solid #1E293B;"
            style_a = "background: #EF4444; color: #0B0F19; font-weight: 900; border: 1px solid #EF4444;" if is_a else "background: transparent; color: #64748B; border: 1px solid #1E293B;"

            # 승무패 14경기 라이브 스코어 연동 패치
            match_id_str = f"TOTO14_{m['id']}"
            live_score_html = "<b style='color:#475569; font-size:16px;'>VS</b>"
            if match_id_str in live_scores_data:
                live_info = live_scores_data[match_id_str]
                score_text = live_info.get("score", "")
                if score_text: live_score_html = f"<div style='color:#00F2FE; font-weight:900; font-size:18px;'>{score_text}</div><div style='color:#EF4444; font-size:10px; font-weight:900;'>LIVE</div>"

            save_prediction({'id': match_id_str, 'league': '승무패 14경기', 'home': m['home'], 'away': m['away']}, best_pick_display, first_pct, (0, 0), 1)
            
            html_code = (
                f"<div class='match-card' style='padding: 24px;'>"
                f"<div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;'><span class='badge-primary'>제 {idx} 경기</span><span style='color:#94A3B8; font-size:14px; font-weight:700;'>AI 추천 마킹: <b style='color:#00F2FE;'>{best_pick_display}</b></span></div>"
                f"<div class='vs-row' style='margin-bottom:15px;'><div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{h_form}</div>{h_rank_html}{h_money_html}{h_inj_html}{h_rest_html}{h_rot_html}</div>{logo_h_tag}</div>"
                f"<div class='center-time-box' style='width:80px;'>{live_score_html}</div>"
                f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{a_form}</div>{a_rank_html}{a_money_html}{a_inj_html}{a_rest_html}{a_rot_html}</div></div></div>"
                f"<div style='font-size:12px; color:#64748B; font-weight:700; text-align:center;'>확률 분포: 승 {p_h}% | 무 {p_d}% | 패 {p_a}%</div>"
                f"<div class='prob-bar-container' style='margin-bottom: 15px;'><div class='prob-bar-win' style='width: {p_h}%;'></div><div class='prob-bar-draw' style='width: {p_d}%;'></div><div class='prob-bar-lose' style='width: {p_a}%;'></div></div>"
                f"<div style='display: flex; gap: 10px;'><div style='flex: 1; text-align: center; padding: 12px; border-radius: 6px; font-size: 14px; {style_h}'>승</div><div style='flex: 1; text-align: center; padding: 12px; border-radius: 6px; font-size: 14px; {style_d}'>무</div><div style='flex: 1; text-align: center; padding: 12px; border-radius: 6px; font-size: 14px; {style_a}'>패</div></div>"
                f"</div>"
            )
            match_html_list.append(html_code)
            
        total_price = total_combinations * 1000
        single_pick_count = len(toto_14_raw) - double_pick_count
        
        summary_html = f"<div style='background: #111827; border: 1px solid #1E293B; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 24px; box-shadow: 0 4px 15px rgba(0,0,0,0.5);'><span style='color: #94A3B8; font-size: 14px; font-weight: 700; display: block; margin-bottom: 5px;'>AI 승무패 최종 분석 결과 (현재 수집된 {len(toto_14_raw)}경기 기준)</span><span style='color: #F8FAFC; font-size: 16px; font-weight: 700; display: block; margin-bottom: 8px;'>단통 <span style='color:#10B981;'>{single_pick_count}</span>경기 + 투마킹 <span style='color:#EF4444;'>{double_pick_count}</span>경기</span><span style='color: #F8FAFC; font-size: 24px; font-weight: 900; display: block;'>최종 <span style='color: #00F2FE;'>{total_combinations}</span> 조합 / 예상 구매 금액: <span style='color: #10B981;'>{total_price:,}</span> 원</span></div>"
        st.markdown(summary_html, unsafe_allow_html=True)
        for html in match_html_list: st.markdown(html, unsafe_allow_html=True)
    else: st.info("현재 진행 중인 승무패 14경기 데이터가 없습니다.")

# -----------------------------------------------------------------------------
# [TAB 3] 오늘의 TOP 3
# -----------------------------------------------------------------------------
with main_tab3:
    valid_top3 = []
    for item in analyzed_proto:
        m = item['match']
        status, is_closed = get_match_status(item["final_match_time"], m.get("deadline_time", "23:00"))
        if status == "UPCOMING" and not is_closed and m.get('match_time') != '마감/진행중':
            valid_top3.append(item)
    top_3_picks = sorted(valid_top3, key=lambda x: x['best_prob_pct'], reverse=True)[:3]
    if top_3_picks:
        for idx, item in enumerate(top_3_picks, 1):
            m = item['match']
            logo_h_tag = render_logo_html(item.get("home_logo"))
            logo_a_tag = render_logo_html(item.get("away_logo"))
            
            h_form = item.get('home_form', '')
            a_form = item.get('away_form', '')
            
            h_inj_data = item.get('h_inj_data', {'count': 0, 'ace_missing': False})
            a_inj_data = item.get('a_inj_data', {'count': 0, 'ace_missing': False})
            h_inj = h_inj_data['count']
            a_inj = a_inj_data['count']
            
            if h_inj_data['ace_missing']: h_inj_html = f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 에이스 결장 비상!(-0.6)</div>"
            elif h_inj > 0: h_inj_html = f"<div class='injury-badge'>🏥 부상 페널티: {'3단계(MAX)' if h_inj >= 6 else ('2단계(-0.2)' if h_inj >= 3 else '1단계(-0.1)')}</div>"
            else: h_inj_html = ""

            if a_inj_data['ace_missing']: a_inj_html = f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 에이스 결장 비상!(-0.6)</div>"
            elif a_inj > 0: a_inj_html = f"<div class='injury-badge'>🏥 부상 페널티: {'3단계(MAX)' if a_inj >= 6 else ('2단계(-0.2)' if a_inj >= 3 else '1단계(-0.1)')}</div>"
            else: a_inj_html = ""
            
            h_rest = item.get('h_rest', 99)
            a_rest = item.get('a_rest', 99)
            h_rest_html = f"<div class='fatigue-badge'>💦 체력 페널티: 방전 (-0.15)</div>" if h_rest <= 3 else ""
            a_rest_html = f"<div class='fatigue-badge'>💦 체력 페널티: 방전 (-0.15)</div>" if a_rest <= 3 else ""
            
            h_next = item.get('h_next', {"is_important": False, "days_until_next": 99})
            a_next = item.get('a_next', {"is_important": False, "days_until_next": 99})
            h_rot_html = f"<div class='fatigue-badge' style='background: rgba(245,158,11,0.2); border-color: #F59E0B; color: #F59E0B;'>⚠️ 로테이션 경보!(-0.3)</div>" if h_next["is_important"] and h_next["days_until_next"] <= 4 else ""
            a_rot_html = f"<div class='fatigue-badge' style='background: rgba(245,158,11,0.2); border-color: #F59E0B; color: #F59E0B;'>⚠️ 로테이션 경보!(-0.3)</div>" if a_next["is_important"] and a_next["days_until_next"] <= 4 else ""

            h_rank, a_rank = item.get('h_rank', 99), item.get('a_rank', 99)
            h_rank_html = f"<div class='rank-badge'>🏆 리그 순위: {h_rank}위 {'(🔥강등버프)' if h_rank >= 15 else ''}</div>" if h_rank != 99 else ""
            a_rank_html = f"<div class='rank-badge'>🏆 리그 순위: {a_rank}위 {'(🔥강등버프)' if a_rank >= 15 else ''}</div>" if a_rank != 99 else ""
            h_market, a_market = item.get('h_market', 0.0), item.get('a_market', 0.0)
            h_money_html = f"<div class='money-badge'>💸 해외 마켓 몰림 (홈 승)</div>" if h_market > 0 else ""
            a_money_html = f"<div class='money-badge'>💸 해외 마켓 몰림 (원정 승)</div>" if a_market > 0 else ""

            html_code = (
                f"<div class='match-card top3-glow'>"
                f"<div class='league-title' style='color:#00F2FE;'># {idx} 최고 가치 추천 픽 • {m.get('league','')}</div>"
                f"<div class='vs-row'><div class='team-box home'><div class='team-info-wrapper'><div class='team-name-text'>{m.get('home','')}</div><div class='team-form-text'>{h_form}</div>{h_rank_html}{h_money_html}{h_inj_html}{h_rest_html}{h_rot_html}</div>{logo_h_tag}</div>"
                f"<div class='center-time-box'><span class='match-time-text' style='color:#00F2FE;'>{item['final_match_time']}</span></div>"
                f"<div class='team-box away'>{logo_a_tag}<div class='team-info-wrapper'><div class='team-name-text'>{m.get('away','')}</div><div class='team-form-text'>{a_form}</div>{a_rank_html}{a_money_html}{a_inj_html}{a_rest_html}{a_rot_html}</div></div></div>"
                f"<div class='pred-grid' style='margin-top:20px;'><div class='pred-box' style='background:rgba(0, 242, 254, 0.05); border-color:#00F2FE;'><div class='pred-label' style='color:#00F2FE;'>강력 추천 (일반 승무패)</div><span class='pred-value'>{item.get('best_option','')}</span> <span class='pred-prob'>{item.get('best_prob_pct','0')}%</span></div><div class='pred-box'><div class='pred-label'>서브 추천 (언오버)</div><span class='pred-value'>{item.get('best_uo','')}</span> <span class='pred-prob'>{item.get('best_uo_prob','0')}%</span></div></div>"
                f"</div>"
            )
            st.markdown(html_code, unsafe_allow_html=True)
    else: st.info("현재 배팅 가능한 분석 경기가 없어 추천 픽을 산출할 수 없습니다.")

# -----------------------------------------------------------------------------
# [TAB 4] AI 리포트 (오답노트 포함)
# -----------------------------------------------------------------------------
with main_tab4:
    stats = get_accuracy_stats()
    st.markdown(f"<div style='display:flex; align-items:center; gap:20px; margin-bottom:30px; background:#0B0F19; padding:20px; border-radius:12px; border:1px solid #1E293B;'><div><span style='color:#94A3B8; font-size:14px; font-weight:700; display:block;'>전체 누적 적중률</span><span style='color:#00F2FE; font-size:40px; font-weight:900;'>{stats['accuracy']}%</span></div><div style='border-left:1px solid #334155; padding-left:20px;'><span style='color:#CBD5E1; font-size:14px; display:block;'>종료된 경기: {stats['total']} 경기</span><span style='color:#10B981; font-size:14px; display:block; margin-top:5px;'>적중: {stats['correct']} 경기</span><span style='color:#EF4444; font-size:14px; display:block; margin-top:5px;'>실패: {stats['total'] - stats['correct']} 경기</span></div></div><h4 style='color:#F8FAFC; font-weight:900; margin-bottom:10px;'>📜 최근 경기 학습(오답) 노트</h4>", unsafe_allow_html=True)
    
    scoring_data = stats.get('scoring', [])
    history_data = stats.get('history', [])
    
    if scoring_data or history_data:
        table_html = "<table style='width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; text-align: center;'><thead><tr><th style='background:#1E293B; color:#94A3B8; padding:10px;'>경기</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>예측</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>결과</th><th style='background:#1E293B; color:#94A3B8; padding:10px;'>상태</th></tr></thead><tbody>"
        
        # 👑 [기획 패치] 승무패 14경기들도 리포트에서 -:- 안 뜨게 파일에서 직접 가져옴!
        for row in scoring_data:
            m_id_str = str(row['match_id'])
            
            temp_score = row.get('actual_score', '-:-')
            if temp_score == '-:-' and m_id_str in live_scores_data and live_scores_data[m_id_str].get("score"):
                temp_score = live_scores_data[m_id_str].get("score").replace(" : ", ":")
                
            result_mark = "<span style='color:#F59E0B; font-weight:900;'>채점중</span>"
            table_html += f"<tr><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; font-weight:700;'>{row.get('home_team','')} vs {row.get('away_team','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #00F2FE;'>{row.get('predicted_pick','')}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B; color: #F8FAFC; font-weight:900;'>{temp_score}</td><td style='padding: 12px 10px; border-bottom: 1px solid #1E293B;'>{result_mark}</td></tr>"

        # 완전히 종료되고 오답/정답 노트까지 쓰여진 경기 노출
        for row in history_data:
            if row.get('is_correct',0) == 1:
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
