import os
import json
import time
import schedule
import requests
import sqlite3
import base64
import re
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config import *
from api_engine import *

def download_latest_db_from_github():
    print(f"\n[🔄 {time.strftime('%Y-%m-%d %H:%M:%S')}] 기존 기록 보호를 위해 GitHub에서 최신 DB를 가져옵니다...")
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/ai_predictions.db?t={int(time.time())}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            with open("ai_predictions.db", "wb") as f: f.write(res.content)
            print("✅ 기존 DB 다운로드 완료! (기록 덮어쓰기 방지 성공)")
        else: print("⚠️ GitHub에 DB가 없거나 통신 에러 (새로 생성합니다)")
    except Exception as e: print(f"❌ DB 다운로드 에러: {e}")

def upload_to_github(file_path):
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
        git_headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        sha = None
        r_get = requests.get(url, headers=git_headers, timeout=10)
        if r_get.status_code == 200: sha = r_get.json().get("sha")
        with open(file_path, "rb") as f: content = f.read()
        b64_content = base64.b64encode(content).decode("utf-8")
        data = {"message": f"Auto update {file_path}", "content": b64_content}
        if sha: data["sha"] = sha
        r_put = requests.put(url, headers=git_headers, json=data, timeout=10)
        if r_put.status_code in [200, 201]: print(f"✅ GitHub 동기화 완료: {file_path}")
        else: print(f"❌ GitHub 동기화 실패 ({file_path}): {r_put.json()}")
    except Exception as e: print(f"❌ [관제 봇 떡밥] GitHub 업로드 에러: {e}")

def save_dual_predictions_to_local_db(m_id, league, home_team, away_team, prob_pick, prob_val, ev_pick, ev_val, odd_h, odd_d, odd_a, match_time, is_toto14, fixture_id, analysis_stage="regular", confidence=0.0):
    conn = sqlite3.connect("ai_predictions.db")
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT api_fixture_id FROM predictions WHERE match_id = ?", (m_id,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("""
                INSERT INTO predictions 
                (match_id, league, home_team, away_team, prob_pick, prob_pick_prob, ev_pick, ev_pick_prob, odd_h, odd_d, odd_a, match_time, is_toto14, api_fixture_id) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (m_id, league, home_team, away_team, prob_pick, prob_val, ev_pick, ev_val, odd_h, odd_d, odd_a, match_time, is_toto14, fixture_id))
        else:
            existing_fix_id = row[0]
            final_fix_id = existing_fix_id if existing_fix_id and existing_fix_id > 0 else fixture_id
            cursor.execute("""
                UPDATE predictions 
                SET api_fixture_id = ?, match_time = ?, league = ?, prob_pick = ?, prob_pick_prob = ?, ev_pick = ?, ev_pick_prob = ? 
                WHERE match_id = ?
            """, (final_fix_id, match_time, league, prob_pick, prob_val, ev_pick, ev_val, m_id))

        cursor.execute("""
            SELECT stage, confidence, prob_pick, prob_pick_prob, ev_pick, ev_pick_prob
            FROM prediction_snapshots
            WHERE match_id = ?
            ORDER BY id DESC LIMIT 1
        """, (str(m_id),))
        previous = cursor.fetchone()
        current = (
            str(analysis_stage), round(float(confidence or 0), 4), str(prob_pick),
            round(float(prob_val or 0), 2), str(ev_pick), round(float(ev_val or 0), 2)
        )
        previous_normalized = None
        if previous:
            previous_normalized = (
                str(previous[0]), round(float(previous[1] or 0), 4), str(previous[2]),
                round(float(previous[3] or 0), 2), str(previous[4]), round(float(previous[5] or 0), 2)
            )
        if previous_normalized != current:
            cursor.execute("""
                INSERT INTO prediction_snapshots (
                    match_id, analysis_version, stage, confidence,
                    prob_pick, prob_pick_prob, ev_pick, ev_pick_prob,
                    odd_h, odd_d, odd_a, api_fixture_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(m_id), ANALYSIS_VERSION, analysis_stage, float(confidence or 0),
                prob_pick, prob_val, ev_pick, ev_val, odd_h, odd_d, odd_a, int(fixture_id or 0)
            ))
        conn.commit()
    except Exception as e: print(f"⚠️ [DB 에러] 듀얼 예측 저장 실패: {e}")
    finally: conn.close()

# === 🧠 [V3/V4 통합 엔진] 주전 선발 & 전력 누수(WAR) 계산기 ===
POSITION_WEIGHTS = {
    "Goalkeeper": 1.5,
    "Defender": 1.3,
    "Midfielder": 1.1,
    "Attacker": 1.0
}
SQUAD_CACHE = {} 

def fetch_team_squad_cached(team_id):
    if not team_id: return []
    if team_id in SQUAD_CACHE: return SQUAD_CACHE[team_id]
    try:
        res = api_get("/players/squads", params={"team": team_id}, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data.get("response"):
                players = data["response"][0]["players"]
                SQUAD_CACHE[team_id] = players
                return players
    except: pass
    return []

def calculate_war_penalty(team_name, ace_names, total_inj_count, team_id):
    squad = fetch_team_squad_cached(team_id)
    war_penalty = 0.0
    details = []
    missing_aces_found = 0
    
    for ace in ace_names:
        pos = "Attacker" 
        for p in squad:
            if p.get("name") and (ace.lower() in p["name"].lower() or p["name"].lower() in ace.lower()):
                pos = p.get("position", "Attacker")
                break
        
        weight = POSITION_WEIGHTS.get(pos, 1.0)
        gap = 1.5 
        drop = gap * weight
        war_penalty += drop
        
        pos_kr = {"Goalkeeper": "GK", "Defender": "DF", "Midfielder": "MF", "Attacker": "FW"}.get(pos, "FW")
        details.append(f"{ace}({pos_kr})")
        missing_aces_found += 1
        
    regular_injuries = max(0, total_inj_count - missing_aces_found)
    effective_bench_injuries = min(regular_injuries, 3) 
    
    if effective_bench_injuries > 0:
        drop = effective_bench_injuries * 0.3
        war_penalty += drop
        if regular_injuries >= 4:
            details.append("서브 결장 다수")
        elif effective_bench_injuries >= 2:
            details.append(f"기타 서브 {effective_bench_injuries}명")
        elif effective_bench_injuries == 1:
            details.append("서브 1명")

    scaled_penalty = min(0.35, war_penalty * 0.08) 
    return scaled_penalty, details, war_penalty


def get_expected_core_players(team_id, league_id, season):
    """득점/도움 상위 선수와 보호 선수 중 현재 해당 팀 소속인 선수만 반환한다."""
    if not team_id:
        return []
    core_players = []
    key_players = fetch_league_key_players(league_id, season) if league_id and season else {}
    for name, stats in key_players.items():
        if stats.get("team_id") == team_id and name not in core_players:
            core_players.append(name)

    for player in fetch_team_squad_cached(team_id):
        actual_name = player.get("name", "")
        if actual_name and find_protected_star(actual_name) and actual_name not in core_players:
            core_players.append(actual_name)
    return core_players[:8]


def find_missing_core_players(core_players, starters):
    normalized_starters = [_normalize_player_name(name) for name in starters]
    missing = []
    for core in core_players:
        target = _normalize_player_name(core)
        found = any(
            target == starter or (len(target) >= 6 and (target in starter or starter in target))
            for starter in normalized_starters if starter
        )
        if not found:
            missing.append(core)
    return missing


def calculate_data_confidence(home_info, away_info, fixture_id, h_stand, a_stand, h_long, a_long,
                              h_recent, a_recent, h_stats, a_stats, h_inj_data, a_inj_data,
                              diff_hours, lineup_confirmed):
    """데이터가 부족한 경기는 확률을 낮춰 보이게 하고 TOP3 우선순위도 낮춘다."""
    score = 0.35
    if home_info.get("id") and away_info.get("id"):
        score += 0.10
    if fixture_id:
        score += 0.10
    if min(h_long.get("home_total", 0), a_long.get("away_total", 0)) >= 5:
        score += 0.12
    if h_stand.get("rank", 99) != 99 and a_stand.get("rank", 99) != 99:
        score += 0.08
    if min(h_recent.get("matches", 0), a_recent.get("matches", 0)) >= 4:
        score += 0.08
    if min(h_stats.get("sample_size", 0), a_stats.get("sample_size", 0)) >= 1:
        score += 0.04
    if h_inj_data.get("available") and a_inj_data.get("available"):
        score += 0.05
    if diff_hours > 1.5:
        score += 0.03
    elif lineup_confirmed:
        score += 0.08
    return round(max(0.35, min(0.95, score)), 3)


def prediction_stage(diff_hours, lineup_confirmed=False):
    if diff_hours <= 0:
        return "locked"
    if lineup_confirmed or diff_hours <= 0.5:
        return "T-30-final"
    if diff_hours <= 1.0:
        return "T-60"
    if diff_hours <= 1.5:
        return "T-90"
    return "regular"


def annotate_pick_metrics(picks, confidence):
    for pick in picks:
        probability = float(pick.get("prob", 0) or 0)
        market_probability = float(pick.get("market_prob", 0) or 0)
        odd = float(pick.get("odd", 0) or 0)
        edge = probability - market_probability if market_probability > 0 else 0.0
        pick["edge"] = round(edge, 4)
        pick["safe_score"] = round(probability * confidence, 4)
        pick["value_score"] = round(max(0.0, edge) * confidence, 4)
        pick["is_qualified_underdog"] = bool(
            2.20 <= odd <= 6.00 and probability >= 0.24 and edge >= 0.04 and confidence >= 0.65
        )
        pick["recommendation_score"] = round(
            (probability * 0.72) + (confidence * 0.20) + (min(max(edge, 0.0), 0.15) * 0.55), 4
        )
    return picks

def build_dashboard_data():
    print(f"\n[🧠 {time.strftime('%Y-%m-%d %H:%M:%S')}] 대시보드 {ANALYSIS_VERSION} 신뢰도 보정 엔진 가동 중...")
    try:
        with open("betman_data.json", "r", encoding="utf-8") as f: betman_data = json.load(f)
    except: return
     
    proto_matches = betman_data.get("proto_matches", [])
    toto_14_matches = betman_data.get("toto_14_matches", [])
    dashboard_proto = []
    dashboard_toto14 = []
      
    for m in proto_matches:
        home_team, away_team = m["home"], m["away"]
        final_match_time = m.get("match_time") or m.get("time") or "시간 미정"
        m_dt = parse_match_time(final_match_time)
        home_info = fetch_team_info_api(home_team)
        away_info = fetch_team_info_api(away_team)

        odd_h = float(m.get("odd_h") or 0)
        odd_d = float(m.get("odd_d") or 0)
        odd_a = float(m.get("odd_a") or 0)
        if min(odd_h, odd_d, odd_a) <= 1.0:
            # 배당이 잠시 비어도 베트맨 원본 경기 자체는 화면에서 누락시키지 않는다.
            dashboard_proto.append({
                "match": m,
                "final_match_time": final_match_time,
                "timestamp": m_dt.timestamp(),
                "league": m.get("league", "축구"),
                "home_logo": home_info.get("logo"),
                "away_logo": away_info.get("logo"),
                "story": "⏳ 베트맨 경기 확인 완료. 현재 승무패 배당이 준비되지 않아 분석 결과를 기다리는 중입니다.",
                "ev_sorted_picks": [],
                "home_form": "",
                "away_form": "",
                "analysis_version": ANALYSIS_VERSION,
                "analysis_confidence": 0.0,
                "analysis_stage": "waiting_odds",
                "reliability_score": 0.0,
                "h_inj_html": "",
                "a_inj_html": "",
                "h_rest_html": "",
                "a_rest_html": "",
                "h_rank_html": "",
                "a_rank_html": "",
            })
            print(f"⚠️ 실제 승무패 배당 대기 중(경기는 유지): {home_team} vs {away_team}")
            continue
        handi_h = float(m.get("handi_h") or 0)
        handi_d = float(m.get("handi_d") or 0)
        handi_a = float(m.get("handi_a") or 0)
        handi_base = float(m.get("handi_base") or 0)
        uo_under = float(m.get("uo_under") or 0)
        uo_over = float(m.get("uo_over") or 0)
        uo_base = float(m.get("uo_base") or 2.5)

        # 🔥 '시간 미정' 경기들 살리기 위해 예외처리(continue) 삭제!
        now = datetime.now(timezone(timedelta(hours=9)))
        diff_hours = (m_dt - now).total_seconds() / 3600.0

        heavy_ttl = 24
        inj_ttl = 0.5 if diff_hours <= 1.5 else 12
        odds_ttl = 0.5 if diff_hours <= 2 else 4
        lineup_ttl = 0.25 if diff_hours <= 1.5 else 12
         
        os_data = fetch_overseas_odds_and_fixture_api(home_info.get("id"), away_info.get("id"), odds_ttl, final_match_time)
        api_fixture_id = os_data.get("fixture_id", 0) if os_data else 0
        referee = os_data.get("referee") if os_data else None
        city = os_data.get("city") if os_data else None
        weather_condition = fetch_weather_api(city, odds_ttl)
         
        fixture_details = fetch_fixture_details_api(home_info["id"], away_info["id"], heavy_ttl)
        h_stand = fetch_team_standing_api(home_info.get("id"), heavy_ttl)
        a_stand = fetch_team_standing_api(away_info.get("id"), heavy_ttl)
        h_rank, a_rank = h_stand["rank"], a_stand["rank"]
        
        h_manager = fetch_new_manager_status(home_info.get("id"), heavy_ttl)
        a_manager = fetch_new_manager_status(away_info.get("id"), heavy_ttl)
        h_manager_buff = 0.08 if h_manager.get("is_new_manager") else 0.0
        a_manager_buff = 0.08 if a_manager.get("is_new_manager") else 0.0
        is_derby = check_derby_match(home_team, away_team)
         
        h_market_bonus, a_market_bonus = 0.0, 0.0
        if os_data and os_data.get("odd_h") and odd_h > 1.0 and odd_a > 1.0:
            if os_data["odd_h"] < odd_h - 0.15: h_market_bonus = 0.05
            if os_data["odd_a"] < odd_a - 0.15: a_market_bonus = 0.05
             
        rank_diff_bonus_h = max(-0.18, min(0.18, (a_rank - h_rank) * 0.012)) if 99 not in (h_rank, a_rank) else 0.0
        rank_diff_bonus_a = max(-0.18, min(0.18, (h_rank - a_rank) * 0.012)) if 99 not in (h_rank, a_rank) else 0.0
        
        h_desperation = 0.06 if h_rank >= 15 and h_rank != 99 else 0.0
        a_desperation = 0.06 if a_rank >= 15 and a_rank != 99 else 0.0
        
        h_title_buff = 0.06 if 1 <= h_rank <= 3 else 0.0
        a_title_buff = 0.06 if 1 <= a_rank <= 3 else 0.0
        
        h_played = h_stand.get("played", 0)
        h_total_teams = h_stand.get("total_teams", 20)
        h_team_goals = h_stand.get("team_goals", 0)
        is_late_season_h = h_played > 0 and (h_played / max(1, (h_total_teams - 1) * 2)) >= 0.75
        h_vacation = 0.08 if (is_late_season_h and 6 <= h_rank <= max(10, h_total_teams - 4)) else 0.0
        
        a_played = a_stand.get("played", 0)
        a_total_teams = a_stand.get("total_teams", 20)
        a_team_goals = a_stand.get("team_goals", 0)
        is_late_season_a = a_played > 0 and (a_played / max(1, (a_total_teams - 1) * 2)) >= 0.75
        a_vacation = 0.08 if (is_late_season_a and 6 <= a_rank <= max(10, a_total_teams - 4)) else 0.0
         
        h_inj_data = fetch_team_injuries_api(home_info.get("id"), h_stand.get("league_id"), h_stand.get("season"), inj_ttl, api_fixture_id)
        a_inj_data = fetch_team_injuries_api(away_info.get("id"), a_stand.get("league_id"), a_stand.get("season"), inj_ttl, api_fixture_id)
        h_inj_count, a_inj_count = h_inj_data["count"], a_inj_data["count"]
        
        h_missing_goals = h_inj_data.get("missing_goals", 0)
        h_goal_dep_ratio = (h_missing_goals / h_team_goals) if h_team_goals > 0 else 0
        h_oneman_penalty = 0.15 if h_goal_dep_ratio >= 0.25 else 0.0
        
        a_missing_goals = a_inj_data.get("missing_goals", 0)
        a_goal_dep_ratio = (a_missing_goals / a_team_goals) if a_team_goals > 0 else 0
        a_oneman_penalty = 0.15 if a_goal_dep_ratio >= 0.25 else 0.0
        
        h_war_pct, h_war_details, h_war_score = calculate_war_penalty(home_team, h_inj_data["ace_names"], h_inj_count, home_info.get("id"))
        a_war_pct, a_war_details, a_war_score = calculate_war_penalty(away_team, a_inj_data["ace_names"], a_inj_count, away_info.get("id"))

        h_lineup_penalty, a_lineup_penalty = 0.0, 0.0
        h_lineup_msg, a_lineup_msg = "", ""
        lineup_confirmed = False
        if diff_hours <= 1.5:
            lineup_data = fetch_lineups_api(api_fixture_id, lineup_ttl)
            h_starters = lineup_data.get(str(home_info.get("id")), [])
            a_starters = lineup_data.get(str(away_info.get("id")), [])
            lineup_confirmed = bool(lineup_data.get("confirmed"))

            if lineup_confirmed:
                h_core = get_expected_core_players(home_info.get("id"), h_stand.get("league_id"), h_stand.get("season"))
                a_core = get_expected_core_players(away_info.get("id"), a_stand.get("league_id"), a_stand.get("season"))
                h_missing = find_missing_core_players(sorted(set(h_core + h_inj_data.get("ace_names", []))), h_starters)
                a_missing = find_missing_core_players(sorted(set(a_core + a_inj_data.get("ace_names", []))), a_starters)

                h_injury_names = {_normalize_player_name(name) for name in h_inj_data.get("ace_names", [])}
                a_injury_names = {_normalize_player_name(name) for name in a_inj_data.get("ace_names", [])}
                h_unexpected = [name for name in h_missing if _normalize_player_name(name) not in h_injury_names]
                a_unexpected = [name for name in a_missing if _normalize_player_name(name) not in a_injury_names]
                if h_missing:
                    h_lineup_penalty = 0.12 if len(h_unexpected) == 1 else (0.20 if len(h_unexpected) >= 2 else 0.0)
                    h_lineup_msg = f"🚨 [선발 확인] 홈팀 핵심 {', '.join(h_missing[:3])} 선발 제외"
                if a_missing:
                    a_lineup_penalty = 0.12 if len(a_unexpected) == 1 else (0.20 if len(a_unexpected) >= 2 else 0.0)
                    a_lineup_msg = f"🚨 [선발 확인] 원정팀 핵심 {', '.join(a_missing[:3])} 선발 제외"

        h_last_data = fetch_team_last_match_date_api(home_info.get("id"), heavy_ttl)
        a_last_data = fetch_team_last_match_date_api(away_info.get("id"), heavy_ttl)
        h_rest_days = calculate_rest_days(h_last_data.get("date"), final_match_time)
        a_rest_days = calculate_rest_days(a_last_data.get("date"), final_match_time)
        h_extreme_fatigue, a_extreme_fatigue = h_last_data.get("is_extreme_fatigue"), a_last_data.get("is_extreme_fatigue")
         
        h_next = fetch_team_next_fixture_api(home_info.get("id"), heavy_ttl)
        a_next = fetch_team_next_fixture_api(away_info.get("id"), heavy_ttl)
        h_long = fetch_team_long_term_stats_api(home_info.get("id"), heavy_ttl)
        a_long = fetch_team_long_term_stats_api(away_info.get("id"), heavy_ttl)
        h_recent = fetch_team_recent_form_metrics(home_info.get("id"), heavy_ttl)
        a_recent = fetch_team_recent_form_metrics(away_info.get("id"), heavy_ttl)
         
        league_n = m.get('league', '')
        AVG_H_GF, AVG_A_GF = get_league_averages(league_n)
        AVG_H_GA, AVG_A_GA = AVG_A_GF, AVG_H_GF
         
        HAS = (h_long["home_gf"] / h_long["home_total"]) / AVG_H_GF if h_long["home_total"] > 0 else 1.0
        HDS = (h_long["home_ga"] / h_long["home_total"]) / AVG_H_GA if h_long["home_total"] > 0 else 1.0
        AAS = (a_long["away_gf"] / a_long["away_total"]) / AVG_A_GF if a_long["away_total"] > 0 else 1.0
        ADS = (a_long["away_ga"] / a_long["away_total"]) / AVG_A_GA if a_long["away_total"] > 0 else 1.0
         
        home_adv = 1.08
        if abs(h_rank - a_rank) <= 3 and h_rank != 99:
            home_adv = 1.12
            
        math_exp_h = (HAS * ADS * AVG_H_GF * home_adv) * (0.8 if weather_condition in ["Rain", "Snow"] else 1.0)
        math_exp_a = (AAS * HDS * AVG_A_GF) * (0.8 if weather_condition in ["Rain", "Snow"] else 1.0)
        math_exp_h *= 1.0 + h_recent.get("strength", 0.0)
        math_exp_a *= 1.0 + a_recent.get("strength", 0.0)

        is_cup_or_intl = any(kw in league_n.lower() for kw in ["cup", "컵", "챔피언스", "유로파", "컨퍼런스", "월드컵", "친선", "fa", "코파", "afc", "네이션스"])
        if is_cup_or_intl:
            math_exp_h *= 0.92
            math_exp_a *= 0.92
         
        h_stats = fetch_recent_team_stats_api(home_info.get("id"), heavy_ttl)
        a_stats = fetch_recent_team_stats_api(away_info.get("id"), heavy_ttl)
        
        h_corners = h_stats.get('corners', 4.5)
        h_cards = h_stats.get('yellow_cards', 1.5)
        a_corners = a_stats.get('corners', 4.5)
        a_cards = a_stats.get('yellow_cards', 1.5)

        h_xg_multi = max(0.82, min(1.22, 1.0 + ((h_stats.get('possession',50) - 50) * 0.008) + ((h_stats.get('shots_on_goal',4.0) - 4.0) * 0.05) + ((h_corners - 4.5) * 0.012) - ((h_cards - 1.5) * 0.015)))
        a_xg_multi = max(0.82, min(1.22, 1.0 + ((a_stats.get('possession',50) - 50) * 0.008) + ((a_stats.get('shots_on_goal',4.0) - 4.0) * 0.05) + ((a_corners - 4.5) * 0.012) - ((a_cards - 1.5) * 0.015)))
         
        base_exp_h = (math_exp_h * h_xg_multi * 0.85) + (((1/odd_h) / ((1/odd_h)+(1/odd_d)+(1/odd_a)) * 2.8) * 0.15)
        base_exp_a = (math_exp_a * a_xg_multi * 0.85) + (((1/odd_a) / ((1/odd_h)+(1/odd_d)+(1/odd_a)) * 2.8) * 0.15)

        h_depth_factor = 0.5 if h_rank <= 5 else (1.5 if h_rank >= 15 and h_rank != 99 else 1.0)
        a_depth_factor = 0.5 if a_rank <= 5 else (1.5 if a_rank >= 15 and a_rank != 99 else 1.0)

        # 🔥 확률 떡락 방지 (최대 2개 악재만 반영하여 35% 캡 적용)
        h_fatigue_pct = (0.15 if h_extreme_fatigue else 0.08) if h_rest_days <= 3 else 0.0
        a_fatigue_pct = (0.15 if a_extreme_fatigue else 0.08) if a_rest_days <= 3 else 0.0
        h_rot_pct = 0.10 if h_next["is_important"] and h_next["days_until_next"] <= 4 else 0.0
        a_rot_pct = 0.10 if a_next["is_important"] and a_next["days_until_next"] <= 4 else 0.0

        # 주심 성향은 양 팀 전력 패널티가 아니라 카드/변동성 정보로만 사용한다.
        h_penalties = [h_war_pct, h_fatigue_pct, h_rot_pct, h_lineup_penalty, h_vacation, h_oneman_penalty]
        a_penalties = [a_war_pct, a_fatigue_pct, a_rot_pct, a_lineup_penalty, a_vacation, a_oneman_penalty]
        h_penalties.sort(reverse=True)
        a_penalties.sort(reverse=True)
        
        h_total_penalty = min(0.30, sum(h_penalties[:2]) * h_depth_factor)
        a_total_penalty = min(0.30, sum(a_penalties[:2]) * a_depth_factor)
        
        cross_boost_a = h_total_penalty * 0.4
        cross_boost_h = a_total_penalty * 0.4

        fixture_details = fetch_fixture_details_api(home_info["id"], away_info["id"], heavy_ttl)
        h2h_total = fixture_details.get("total", 0)
        h_wins = fixture_details.get("h_wins", 0)
        a_wins = fixture_details.get("a_wins", 0)
        h_h2h_bonus = max(-0.10, min(0.10, ((h_wins - a_wins) / h2h_total) * 0.12)) if h2h_total > 0 else 0
        a_h2h_bonus = -h_h2h_bonus
        
        h_kryptonite, a_kryptonite = 0.0, 0.0
        h_matchup_msg, a_matchup_msg = "", ""
        if h2h_total >= 3:
            if (h_wins / h2h_total) >= 0.65:
                h_kryptonite = 0.04
                h_matchup_msg = f"⚔️ 천적 상성 ({h_wins}승/{h2h_total}전)"
            elif (a_wins / h2h_total) >= 0.65:
                a_kryptonite = 0.04
                a_matchup_msg = f"⚔️ 천적 상성 ({a_wins}승/{h2h_total}전)"

        exp_h = round(max(0.3, min(3.2, (base_exp_h * (1 - h_total_penalty) + cross_boost_h) + h_h2h_bonus + h_kryptonite + rank_diff_bonus_h + h_desperation + h_title_buff + h_market_bonus + h_manager_buff)), 2)
        exp_a = round(max(0.3, min(3.2, (base_exp_a * (1 - a_total_penalty) + cross_boost_a) + a_h2h_bonus + a_kryptonite + rank_diff_bonus_a + a_desperation + a_title_buff + a_market_bonus + a_manager_buff)), 2)

        analysis_confidence = calculate_data_confidence(
            home_info, away_info, api_fixture_id, h_stand, a_stand, h_long, a_long,
            h_recent, a_recent, h_stats, a_stats, h_inj_data, a_inj_data,
            diff_hours, lineup_confirmed,
        )
         
        h_win, draw, a_win, prob_u, prob_o, prob_handi_h, prob_handi_d, prob_handi_a = calculate_poisson_probs(exp_h, exp_a, handi_base, uo_base)

        if is_derby:
            draw = min(0.55, draw * 1.15)
            prob_o = min(0.99, prob_o * 1.10)
            prob_u = 1.0 - prob_o

        is_low_score_league = any(kw in league_n.lower() for kw in ["k1", "k리그1", "k2", "k리그2", "j1", "j리그", "j2"])
        if is_low_score_league or is_cup_or_intl:
            prob_u = min(0.90, prob_u * 1.08)
            prob_o = 1.0 - prob_u
            draw = min(0.55, draw * 1.10) 

        h_win, draw, a_win = calibrate_three_way_probabilities(
            [h_win, draw, a_win], [odd_h, odd_d, odd_a], analysis_confidence
        )
        if uo_under > 1.0 and uo_over > 1.0:
            prob_u, prob_o = calibrate_two_way_probabilities(
                [prob_u, prob_o], [uo_under, uo_over], analysis_confidence
            )
        if min(handi_h, handi_d, handi_a) > 1.0:
            prob_handi_h, prob_handi_d, prob_handi_a = calibrate_three_way_probabilities(
                [prob_handi_h, prob_handi_d, prob_handi_a],
                [handi_h, handi_d, handi_a], analysis_confidence,
            )

        wdl_market = normalize_probabilities([1 / odd_h, 1 / odd_d, 1 / odd_a])
        wdl_cands = [
            {"label": "일반 승무패 예측", "sort_id": 3, "raw_pick": f"{home_team} 승", "html_pick": f"{home_team} 승", "prob": h_win, "ev": h_win * odd_h, "odd": odd_h, "market_prob": wdl_market[0]},
            {"label": "일반 승무패 예측", "sort_id": 3, "raw_pick": "무승부", "html_pick": "무승부", "prob": draw, "ev": draw * odd_d, "odd": odd_d, "market_prob": wdl_market[1]},
            {"label": "일반 승무패 예측", "sort_id": 3, "raw_pick": f"{away_team} 승", "html_pick": f"{away_team} 승", "prob": a_win, "ev": a_win * odd_a, "odd": odd_a, "market_prob": wdl_market[2]}
        ]
         
        handi_str_raw = f"[{handi_base:+1.1f}] "
        if handi_base < 0:
            h_label = f"🔥 [마핸승] {home_team} ({handi_base:+1.1f})" 
            a_label = f"🛡️ [핸디패] {home_team} 패배 ({handi_base:+1.1f})" 
        else:
            h_label = f"🛡️ [플핸승] {home_team} ({handi_base:+1.1f})"
            a_label = f"🔥 [핸디패] {home_team} 패배 ({handi_base:+1.1f})" 
             
        h_html = f"<span style='color:#00F2FE; font-size:14px; font-weight:900;'>{h_label}</span><br>"
        a_html = f"<span style='color:#00F2FE; font-size:14px; font-weight:900;'>{a_label}</span><br>"
        d_html = f"<span style='color:#00F2FE; font-size:14px; font-weight:900;'>핸디무 ({handi_base:+1.1f})</span><br>"

        handi_cands = []
        handi_market = normalize_probabilities([1 / handi_h, 1 / handi_d, 1 / handi_a]) if min(handi_h, handi_d, handi_a) > 1.0 else [0, 0, 0]
        if handi_h > 1.0: handi_cands.append({"label": "핸디캡 예측", "sort_id": 2, "raw_pick": f"{handi_str_raw}{home_team} 핸디승", "html_pick": h_html, "prob": prob_handi_h, "ev": prob_handi_h * handi_h, "odd": handi_h, "market_prob": handi_market[0]})
        if handi_d > 1.0: handi_cands.append({"label": "핸디캡 예측", "sort_id": 2, "raw_pick": f"{handi_str_raw}핸디무", "html_pick": d_html, "prob": prob_handi_d, "ev": prob_handi_d * handi_d, "odd": handi_d, "market_prob": handi_market[1]})
        if handi_a > 1.0: handi_cands.append({"label": "핸디캡 예측", "sort_id": 2, "raw_pick": f"{handi_str_raw}{home_team} 핸디패", "html_pick": a_html, "prob": prob_handi_a, "ev": prob_handi_a * handi_a, "odd": handi_a, "market_prob": handi_market[2]}) 
         
        uo_str_raw = f"(U/O {uo_base})"
        uo_str_html = f"<span style='color:#00F2FE; font-size:14px; font-weight:900;'>[기준 {uo_base}]</span><br>"
        uo_cands = []
        if uo_under > 1.0 and uo_over > 1.0:
            uo_market = normalize_probabilities([1 / uo_under, 1 / uo_over])
            uo_cands = [
                {"label": "언더 예측", "sort_id": 1, "raw_pick": f"언더 {uo_str_raw}", "html_pick": f"{uo_str_html}⬇️ 언더", "prob": prob_u, "ev": prob_u * uo_under, "odd": uo_under, "market_prob": uo_market[0]},
                {"label": "오버 예측", "sort_id": 1, "raw_pick": f"오버 {uo_str_raw}", "html_pick": f"{uo_str_html}⬆️ 오버", "prob": prob_o, "ev": prob_o * uo_over, "odd": uo_over, "market_prob": uo_market[1]}
            ]
         
        base_wdl_pick = max(wdl_cands, key=lambda x: x["prob"])["raw_pick"]
        valid_all_picks = wdl_cands + uo_cands
        for pick in handi_cands:
            is_contra = False
            p_name = pick["raw_pick"]
            if "무승부" in base_wdl_pick and ("핸디무" in p_name or (handi_base < 0 and home_team in p_name) or (handi_base > 0 and "패" in p_name)): is_contra = True 
            elif home_team in base_wdl_pick and (handi_base < 0 and "패" in p_name): is_contra = True 
            elif away_team in base_wdl_pick and (handi_base > 0 and home_team in p_name and "핸디승" in p_name): is_contra = True 
            if not is_contra: valid_all_picks.append(pick)
             
        annotate_pick_metrics(valid_all_picks, analysis_confidence)
        highest_prob_pick = max(valid_all_picks, key=lambda x: (x["safe_score"], x["prob"]))
        qualified_value_picks = [pick for pick in valid_all_picks if pick.get("is_qualified_underdog")]
        highest_ev_pick = max(
            qualified_value_picks or valid_all_picks,
            key=lambda x: (x["value_score"], x["recommendation_score"], x["ev"]),
        )
         
        for p in valid_all_picks:
            badges = []
            is_highest_prob = (p["raw_pick"] == highest_prob_pick["raw_pick"])
            is_highest_ev = (p["raw_pick"] == highest_ev_pick["raw_pick"])
            
            pick_odd = float(p.get("odd", 1.0) or 1.0)

            is_vip = False
            if is_highest_prob and is_highest_ev and p.get("is_qualified_underdog"):
                is_vip = True
            elif is_highest_ev and p.get("is_qualified_underdog"):
                is_vip = True

            if is_vip:
                badges.append("<span style='background: linear-gradient(to right, #FFD700, #F59E0B); color:#000; padding:3px 8px; border-radius:4px; font-size:12px; font-weight:900; margin-right:4px; box-shadow: 0 0 5px rgba(255,215,0,0.5);'>💎 VIP 역배 꿀픽</span>")
            else:
                if is_highest_prob: badges.append("<span style='background:#10B981; color:#fff; padding:3px 8px; border-radius:4px; font-size:12px; font-weight:bold; margin-right:4px;'>🎯 최고 확률</span>")
                if is_highest_ev: badges.append("<span style='background:#F59E0B; color:#fff; padding:3px 8px; border-radius:4px; font-size:12px; font-weight:bold; margin-right:4px;'>🍯 AI 꿀픽</span>")

            if badges: p["html_pick"] = "<div style='margin-bottom:6px;'>" + "".join(badges) + "</div>" + p["html_pick"]

        top3_picks = [highest_prob_pick]
        if highest_ev_pick["raw_pick"] != highest_prob_pick["raw_pick"]: top3_picks.append(highest_ev_pick)
             
        sorted_by_prob = sorted(valid_all_picks, key=lambda x: x["prob"], reverse=True)
        for p in sorted_by_prob:
            if p not in top3_picks and len(top3_picks) < 3: top3_picks.append(p)
                 
        ev_sorted_picks = top3_picks
        analysis_stage = prediction_stage(diff_hours, lineup_confirmed)
        reliability_score = round(
            (highest_prob_pick.get("safe_score", 0) * 0.75)
            + (highest_prob_pick.get("recommendation_score", 0) * 0.25), 4
        )
        underdog_signal = highest_ev_pick.get("raw_pick") if highest_ev_pick.get("is_qualified_underdog") else ""
         
        save_dual_predictions_to_local_db(
            m['id'], league_n, home_team, away_team, 
            highest_prob_pick["raw_pick"], round(highest_prob_pick["prob"] * 100, 1), 
            highest_ev_pick["raw_pick"], round(highest_ev_pick["prob"] * 100, 1),
            odd_h, odd_d, odd_a, final_match_time, 0, api_fixture_id,
            analysis_stage, analysis_confidence,
        )

        h_form = fetch_team_form_api(home_info.get("id"), heavy_ttl)
        a_form = fetch_team_form_api(away_info.get("id"), heavy_ttl)
        story = generate_match_story(highest_prob_pick["raw_pick"], highest_ev_pick["raw_pick"], math_exp_h, math_exp_a, h_win*100, draw*100, a_win*100, h2h_total, 0, home_team, away_team, odd_h, odd_a, h_form, a_form, h_long, a_long, h_inj_data, a_inj_data, h_rest_days, a_rest_days, h_next, a_next, h_rank, a_rank, h_market_bonus, a_market_bonus, h_stats, a_stats, referee, weather_condition, h_extreme_fatigue, a_extreme_fatigue, h_lineup_msg, a_lineup_msg)
        
        if is_derby: story += " ⚔️ [로컬 더비 매치] 양 팀의 자존심이 걸린 치열한 라이벌전으로, 통계를 뛰어넘는 혈투와 변수(카드/극장골)가 예상됩니다."
        if h_manager_buff > 0: story += f" 👔 [경질 버프] {home_team}은(는) 새 감독 부임 이후 선수들의 주전 경쟁과 동기부여가 극에 달해 있습니다."
        if a_manager_buff > 0: story += f" 👔 [경질 버프] 원정팀 {away_team}은(는) 최근 감독 교체로 인한 '허니문 효과'가 강력하게 발동될 타이밍입니다."
        if h_vacation > 0 or a_vacation > 0: story += " 🏖️ [휴가 모드 주의] 시즌 막판 동기부여가 떨어진 중위권 팀의 안일한 경기력이 이변을 만들 수 있습니다."

        h_inj_html = ""
        if h_war_score > 0:
            h_war_text = " / ".join(h_war_details)
            if h_oneman_penalty > 0: h_inj_html += f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 득점루트 붕괴: 팀 득점의 {int(h_goal_dep_ratio*100)}% 이탈</div>"
            elif h_inj_data['ace_missing']: h_inj_html += f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 전력누수(-{h_war_score:.1f}점): {h_war_text}</div>"
            else: h_inj_html += f"<div class='injury-badge'>🏥 전력누수(-{h_war_score:.1f}점): {h_war_text}</div>"
        if h_lineup_msg: h_inj_html += f"<div class='injury-badge' style='background: #EF4444; color: #fff;'>{h_lineup_msg}</div>"
        if h_title_buff > 0: h_inj_html += f"<div class='injury-badge' style='background: rgba(16, 185, 129, 0.2); border-color: #10B981; color: #10B981;'>🏆 우승 경쟁 버프</div>"
        if h_manager_buff > 0: h_inj_html += f"<div class='injury-badge' style='background: rgba(245, 158, 11, 0.2); border-color: #F59E0B; color: #F59E0B;'>👔 새 감독 버프 (부임 {h_manager['days_since_hired']}일차)</div>"
        if h_vacation > 0: h_inj_html += f"<div class='injury-badge' style='background: rgba(100, 116, 139, 0.2); border-color: #64748B; color: #64748B;'>🏖️ 동기부여 상실 (휴가 모드)</div>"
        if h_matchup_msg: h_inj_html += f"<div class='injury-badge' style='background: rgba(59, 130, 246, 0.2); border-color: #3B82F6; color: #3B82F6;'>{h_matchup_msg}</div>"
        if is_derby: h_inj_html += f"<div class='injury-badge' style='background: rgba(239, 68, 68, 0.2); border-color: #EF4444; color: #EF4444;'>⚔️ 치열한 로컬 더비 매치</div>"

        a_inj_html = ""
        if a_war_score > 0:
            a_war_text = " / ".join(a_war_details)
            if a_oneman_penalty > 0: a_inj_html += f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 득점루트 붕괴: 팀 득점의 {int(a_goal_dep_ratio*100)}% 이탈</div>"
            elif a_inj_data['ace_missing']: a_inj_html += f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 전력누수(-{a_war_score:.1f}점): {a_war_text}</div>"
            else: a_inj_html += f"<div class='injury-badge'>🏥 전력누수(-{a_war_score:.1f}점): {a_war_text}</div>"
        if a_lineup_msg: a_inj_html += f"<div class='injury-badge' style='background: #EF4444; color: #fff;'>{a_lineup_msg}</div>"
        if a_title_buff > 0: a_inj_html += f"<div class='injury-badge' style='background: rgba(16, 185, 129, 0.2); border-color: #10B981; color: #10B981;'>🏆 우승 경쟁 버프</div>"
        if a_manager_buff > 0: a_inj_html += f"<div class='injury-badge' style='background: rgba(245, 158, 11, 0.2); border-color: #F59E0B; color: #F59E0B;'>👔 새 감독 버프 (부임 {a_manager['days_since_hired']}일차)</div>"
        if a_vacation > 0: a_inj_html += f"<div class='injury-badge' style='background: rgba(100, 116, 139, 0.2); border-color: #64748B; color: #64748B;'>🏖️ 동기부여 상실 (휴가 모드)</div>"
        if a_matchup_msg: a_inj_html += f"<div class='injury-badge' style='background: rgba(59, 130, 246, 0.2); border-color: #3B82F6; color: #3B82F6;'>{a_matchup_msg}</div>"
        if is_derby: a_inj_html += f"<div class='injury-badge' style='background: rgba(239, 68, 68, 0.2); border-color: #EF4444; color: #EF4444;'>⚔️ 치열한 로컬 더비 매치</div>"
         
        dashboard_proto.append({
            "match": m, "final_match_time": final_match_time, "timestamp": m_dt.timestamp(), "league": league_n,
            "home_logo": home_info.get("logo"), "away_logo": away_info.get("logo"),
            "story": story, "ev_sorted_picks": ev_sorted_picks, "home_form": h_form, "away_form": a_form,
            "analysis_version": ANALYSIS_VERSION, "analysis_confidence": analysis_confidence,
            "analysis_stage": analysis_stage, "reliability_score": reliability_score,
            "underdog_signal": underdog_signal,
            "h_inj_html": h_inj_html, "a_inj_html": a_inj_html, 
            "h_rest_html": f"<div class='fatigue-badge'>💦 체력 방전</div>" if h_rest_days <= 3 else "", "a_rest_html": f"<div class='fatigue-badge'>💦 체력 방전</div>" if a_rest_days <= 3 else "",
            "h_rank_html": f"<div class='rank-badge'>🏆 순위: {h_rank}위</div>" if h_rank != 99 else "", "a_rank_html": f"<div class='rank-badge'>🏆 순위: {a_rank}위</div>" if a_rank != 99 else ""
        })

    double_pick_count = 0
    total_combinations = 1
     
    for idx, m in enumerate(toto_14_matches, 1):
        home_team, away_team = m["home"], m["away"]
        home_info = fetch_team_info_api(home_team)
        away_info = fetch_team_info_api(away_team)
         
        now = datetime.now(timezone(timedelta(hours=9)))
        m_dt = parse_match_time(m.get("match_time") or "시간 미정") 
        diff_hours = (m_dt - now).total_seconds() / 3600.0
        
        heavy_ttl = 24
        # 경기 직전에는 결장 정보가 자주 바뀌므로 짧게 갱신한다.
        inj_ttl = 0.5 if diff_hours <= 1.5 else 12
        odds_ttl = 0.5 if diff_hours <= 2 else 4
        lineup_ttl = 0.25 if diff_hours <= 1.5 else 12
         
        os_data = fetch_overseas_odds_and_fixture_api(home_info.get("id"), away_info.get("id"), odds_ttl, m.get("match_time") or "시간 미정")
        api_fixture_id = os_data.get("fixture_id", 0) if os_data else 0
        referee = os_data.get("referee") if os_data else None
        city = os_data.get("city") if os_data else None
        weather_condition = fetch_weather_api(city, odds_ttl) 
         
        h_stand = fetch_team_standing_api(home_info.get("id"), heavy_ttl)
        a_stand = fetch_team_standing_api(away_info.get("id"), heavy_ttl)
        h_rank, a_rank = h_stand["rank"], a_stand["rank"]
        
        h_manager = fetch_new_manager_status(home_info.get("id"), heavy_ttl)
        a_manager = fetch_new_manager_status(away_info.get("id"), heavy_ttl)
        h_manager_buff = 0.08 if h_manager.get("is_new_manager") else 0.0
        a_manager_buff = 0.08 if a_manager.get("is_new_manager") else 0.0
        is_derby = check_derby_match(home_team, away_team)
        
        if h_rank != 99 and a_rank != 99:
            rank_diff_bonus_h = max(-0.18, min(0.18, (a_rank - h_rank) * 0.012))
            rank_diff_bonus_a = -rank_diff_bonus_h
        else:
            rank_diff_bonus_h = rank_diff_bonus_a = 0.0
        h_desperation = 0.06 if h_rank >= 15 and h_rank != 99 else 0.0
        a_desperation = 0.06 if a_rank >= 15 and a_rank != 99 else 0.0
        h_title_buff = 0.06 if 1 <= h_rank <= 3 else 0.0
        a_title_buff = 0.06 if 1 <= a_rank <= 3 else 0.0
        
        h_played = h_stand.get("played", 0)
        h_total_teams = h_stand.get("total_teams", 20)
        h_team_goals = h_stand.get("team_goals", 0)
        is_late_season_h = h_played > 0 and (h_played / max(1, (h_total_teams - 1) * 2)) >= 0.75
        h_vacation = 0.08 if (is_late_season_h and 6 <= h_rank <= max(10, h_total_teams - 4)) else 0.0
        
        a_played = a_stand.get("played", 0)
        a_total_teams = a_stand.get("total_teams", 20)
        a_team_goals = a_stand.get("team_goals", 0)
        is_late_season_a = a_played > 0 and (a_played / max(1, (a_total_teams - 1) * 2)) >= 0.75
        a_vacation = 0.08 if (is_late_season_a and 6 <= a_rank <= max(10, a_total_teams - 4)) else 0.0

        h_inj_data = fetch_team_injuries_api(home_info.get("id"), h_stand.get("league_id"), h_stand.get("season"), inj_ttl, api_fixture_id)
        a_inj_data = fetch_team_injuries_api(away_info.get("id"), a_stand.get("league_id"), a_stand.get("season"), inj_ttl, api_fixture_id)
        h_inj_count, a_inj_count = h_inj_data["count"], a_inj_data["count"]
        
        h_missing_goals = h_inj_data.get("missing_goals", 0)
        h_goal_dep_ratio = (h_missing_goals / h_team_goals) if h_team_goals > 0 else 0
        h_oneman_penalty = 0.15 if h_goal_dep_ratio >= 0.25 else 0.0
        
        a_missing_goals = a_inj_data.get("missing_goals", 0)
        a_goal_dep_ratio = (a_missing_goals / a_team_goals) if a_team_goals > 0 else 0
        a_oneman_penalty = 0.15 if a_goal_dep_ratio >= 0.25 else 0.0
        
        h_war_pct, _, _ = calculate_war_penalty(home_team, h_inj_data["ace_names"], h_inj_count, home_info.get("id"))
        a_war_pct, _, _ = calculate_war_penalty(away_team, a_inj_data["ace_names"], a_inj_count, away_info.get("id"))

        h_lineup_penalty, a_lineup_penalty = 0.0, 0.0
        lineup_confirmed = False
        if 0 < diff_hours <= 1.5 and api_fixture_id:
            lineup_data = fetch_lineups_api(api_fixture_id, lineup_ttl)
            lineup_confirmed = bool(lineup_data.get("confirmed"))
            if lineup_confirmed:
                h_starters = lineup_data.get(str(home_info.get("id")), [])
                a_starters = lineup_data.get(str(away_info.get("id")), [])
                h_core = get_expected_core_players(home_info.get("id"), h_stand.get("league_id"), h_stand.get("season"))
                a_core = get_expected_core_players(away_info.get("id"), a_stand.get("league_id"), a_stand.get("season"))
                h_missing = find_missing_core_players(sorted(set(h_core + h_inj_data.get("ace_names", []))), h_starters)
                a_missing = find_missing_core_players(sorted(set(a_core + a_inj_data.get("ace_names", []))), a_starters)
                h_injury_names = {_normalize_player_name(name) for name in h_inj_data.get("ace_names", [])}
                a_injury_names = {_normalize_player_name(name) for name in a_inj_data.get("ace_names", [])}
                h_unexpected = [name for name in h_missing if _normalize_player_name(name) not in h_injury_names]
                a_unexpected = [name for name in a_missing if _normalize_player_name(name) not in a_injury_names]
                h_lineup_penalty = 0.12 if len(h_unexpected) == 1 else (0.20 if len(h_unexpected) >= 2 else 0.0)
                a_lineup_penalty = 0.12 if len(a_unexpected) == 1 else (0.20 if len(a_unexpected) >= 2 else 0.0)

        h_last_data = fetch_team_last_match_date_api(home_info.get("id"), heavy_ttl)
        a_last_data = fetch_team_last_match_date_api(away_info.get("id"), heavy_ttl)
        h_rest_days = calculate_rest_days(h_last_data.get("date"), m.get("match_time"))
        a_rest_days = calculate_rest_days(a_last_data.get("date"), m.get("match_time"))
        h_extreme_fatigue, a_extreme_fatigue = h_last_data.get("is_extreme_fatigue"), a_last_data.get("is_extreme_fatigue")

        h_next = fetch_team_next_fixture_api(home_info.get("id"), heavy_ttl)
        a_next = fetch_team_next_fixture_api(away_info.get("id"), heavy_ttl)
        h_long = fetch_team_long_term_stats_api(home_info.get("id"), heavy_ttl)
        a_long = fetch_team_long_term_stats_api(away_info.get("id"), heavy_ttl)
        h_recent = fetch_team_recent_form_metrics(home_info.get("id"), heavy_ttl)
        a_recent = fetch_team_recent_form_metrics(away_info.get("id"), heavy_ttl)
         
        league_n_14 = m.get('league', '')
        AVG_H_GF_14, AVG_A_GF_14 = get_league_averages(league_n_14)
        AVG_H_GA_14, AVG_A_GA_14 = AVG_A_GF_14, AVG_H_GF_14

        HAS = (h_long["home_gf"] / h_long["home_total"]) / AVG_H_GF_14 if h_long["home_total"] > 0 else 1.0
        HDS = (h_long["home_ga"] / h_long["home_total"]) / AVG_H_GA_14 if h_long["home_total"] > 0 else 1.0
        AAS = (a_long["away_gf"] / a_long["away_total"]) / AVG_A_GF_14 if a_long["away_total"] > 0 else 1.0
        ADS = (a_long["away_ga"] / a_long["away_total"]) / AVG_A_GA_14 if a_long["away_total"] > 0 else 1.0
         
        home_adv = 1.08
        if abs(h_rank - a_rank) <= 3 and h_rank != 99:
            home_adv = 1.12
            
        math_exp_h = (HAS * ADS * AVG_H_GF_14 * home_adv) * (0.8 if weather_condition in ["Rain", "Snow"] else 1.0)
        math_exp_a = (AAS * HDS * AVG_A_GF_14) * (0.8 if weather_condition in ["Rain", "Snow"] else 1.0)
        math_exp_h *= 1.0 + h_recent.get("strength", 0.0)
        math_exp_a *= 1.0 + a_recent.get("strength", 0.0)

        is_cup_or_intl_14 = any(kw in league_n_14.lower() for kw in ["cup", "컵", "챔피언스", "유로파", "컨퍼런스", "월드컵", "친선", "fa", "코파", "afc", "네이션스"])
        if is_cup_or_intl_14:
            math_exp_h *= 0.92
            math_exp_a *= 0.92

        h_depth_factor = 0.5 if h_rank <= 5 else (1.5 if h_rank >= 15 and h_rank != 99 else 1.0)
        a_depth_factor = 0.5 if a_rank <= 5 else (1.5 if a_rank >= 15 and a_rank != 99 else 1.0)
        
        # 중복 악재의 이중 계산을 막고 가장 큰 두 항목만 제한적으로 반영한다.
        h_inj_pct = h_war_pct
        a_inj_pct = a_war_pct
        h_fatigue_pct = (0.15 if h_extreme_fatigue else 0.08) if h_rest_days <= 3 else 0.0
        a_fatigue_pct = (0.15 if a_extreme_fatigue else 0.08) if a_rest_days <= 3 else 0.0
        h_rot_pct = 0.10 if h_next["is_important"] and h_next["days_until_next"] <= 4 else 0.0
        a_rot_pct = 0.10 if a_next["is_important"] and a_next["days_until_next"] <= 4 else 0.0

        h_penalties = [h_inj_pct, h_fatigue_pct, h_rot_pct, h_lineup_penalty, h_vacation, h_oneman_penalty]
        a_penalties = [a_inj_pct, a_fatigue_pct, a_rot_pct, a_lineup_penalty, a_vacation, a_oneman_penalty]
        h_penalties.sort(reverse=True)
        a_penalties.sort(reverse=True)
        
        h_total_penalty = min(0.30, sum(h_penalties[:2]) * h_depth_factor)
        a_total_penalty = min(0.30, sum(a_penalties[:2]) * a_depth_factor)
        
        cross_boost_a = h_total_penalty * 0.4
        cross_boost_h = a_total_penalty * 0.4

        fixture_details = fetch_fixture_details_api(home_info["id"], away_info["id"], heavy_ttl)
        h2h_total = fixture_details.get("total", 0)
        h_wins = fixture_details.get("h_wins", 0)
        a_wins = fixture_details.get("a_wins", 0)
        h_h2h_bonus = max(-0.10, min(0.10, ((h_wins - a_wins) / h2h_total) * 0.12)) if h2h_total > 0 else 0.0
        a_h2h_bonus = -h_h2h_bonus
        
        h_kryptonite, a_kryptonite = 0.0, 0.0
        if h2h_total >= 3:
            if (h_wins / h2h_total) >= 0.65: h_kryptonite = 0.04
            elif (a_wins / h2h_total) >= 0.65: a_kryptonite = 0.04
         
        p_h, p_d, p_a = 0.34, 0.33, 0.33
        if os_data and os_data.get("odd_h"):
            total_o = (1/os_data["odd_h"]) + (1/os_data["odd_d"]) + (1/os_data["odd_a"])
            p_h = (1/os_data["odd_h"]) / total_o
            p_a = (1/os_data["odd_a"]) / total_o
         
        odds_exp_h = p_h * 2.8
        odds_exp_a = p_a * 2.8
         
        exp_h = round(max(0.3, min(3.2, ((math_exp_h * 0.75) + (odds_exp_h * 0.25)) * (1 - h_total_penalty) + cross_boost_h + h_h2h_bonus + h_kryptonite + rank_diff_bonus_h + h_desperation + h_title_buff + h_manager_buff)), 2)
        exp_a = round(max(0.3, min(3.2, ((math_exp_a * 0.75) + (odds_exp_a * 0.25)) * (1 - a_total_penalty) + cross_boost_a + a_h2h_bonus + a_kryptonite + rank_diff_bonus_a + a_desperation + a_title_buff + a_manager_buff)), 2)

        analysis_confidence = calculate_data_confidence(
            home_info, away_info, api_fixture_id, h_stand, a_stand, h_long, a_long,
            h_recent, a_recent, {}, {}, h_inj_data, a_inj_data,
            diff_hours, lineup_confirmed,
        )
         
        h_win, draw, a_win, _, _, _, _, _ = calculate_poisson_probs(exp_h, exp_a)
        
        if is_derby: draw = min(0.55, draw * 1.15)
        if is_cup_or_intl_14: draw = min(0.55, draw * 1.10)

        market_odds = []
        if os_data and min(float(os_data.get("odd_h") or 0), float(os_data.get("odd_d") or 0), float(os_data.get("odd_a") or 0)) > 1.0:
            market_odds = [os_data["odd_h"], os_data["odd_d"], os_data["odd_a"]]
        h_win, draw, a_win = calibrate_three_way_probabilities(
            [h_win, draw, a_win], market_odds, analysis_confidence
        )

        # 베트맨 투표율은 정답이 아닌 대중 심리라서, 배당을 못 찾았을 때만 8% 참고한다.
        vote_values = [m.get("vote_h"), m.get("vote_d"), m.get("vote_a")]
        if not market_odds and all(value is not None for value in vote_values):
            vote_probs = normalize_probabilities(vote_values)
            h_win, draw, a_win = normalize_probabilities([
                h_win * 0.92 + vote_probs[0] * 0.08,
                draw * 0.92 + vote_probs[1] * 0.08,
                a_win * 0.92 + vote_probs[2] * 0.08,
            ])

        pct_h = round(h_win * 100, 1)
        pct_d = round(draw * 100, 1)
        pct_a = round(100.0 - pct_h - pct_d, 1)

        probs_dict = {"승": pct_h, "무": pct_d, "패": pct_a}
        sorted_probs = sorted(probs_dict.items(), key=lambda x: x[1], reverse=True)
        first_pick, first_pct = sorted_probs[0]
        second_pick, second_pct = sorted_probs[1]
         
        picks = []
        if first_pct - second_pct <= 7.0:
            if set([first_pick, second_pick]) == set(["승", "패"]): second_pick = "무"
            picks = [first_pick, second_pick]
            total_combinations *= 2
            double_pick_count += 1
        else: picks = [first_pick]
             
        disp_texts = []
        for p in picks:
            if p == "승": disp_texts.append(f"{m['home']} 승")
            elif p == "패": disp_texts.append(f"{m['away']} 승")
            else: disp_texts.append("무승부")
        best_pick_display = ", ".join(disp_texts)
         
        is_h, is_d, is_a = "승" in picks, "무" in picks, "패" in picks
        style_h = "background: #00F2FE; color: #0B0F19; font-weight: 900; border: 1px solid #00F2FE;" if is_h else "background: transparent; color: #64748B; border: 1px solid #1E293B;"
        style_d = "background: #10B981; color: #0B0F19; font-weight: 900; border: 1px solid #10B981;" if is_d else "background: transparent; color: #64748B; border: 1px solid #1E293B;"
        style_a = "background: #EF4444; color: #0B0F19; font-weight: 900; border: 1px solid #EF4444;" if is_a else "background: transparent; color: #64748B; border: 1px solid #1E293B;"
        picks_html = f"<div style='flex: 1; text-align: center; padding: 12px; border-radius: 6px; font-size: 14px; {style_h}'>승</div><div style='flex: 1; text-align: center; padding: 12px; border-radius: 6px; font-size: 14px; {style_d}'>무</div><div style='flex: 1; text-align: center; padding: 12px; border-radius: 6px; font-size: 14px; {style_a}'>패</div>"
         
        analysis_stage = prediction_stage(diff_hours, lineup_confirmed)
        save_dual_predictions_to_local_db(
            f"TOTO14_{m['id']}", '승무패 14경기', home_team, away_team, 
            best_pick_display, first_pct, best_pick_display, first_pct, 
            0, 0, 0, m.get("match_time") or '시간 미정', 1, api_fixture_id,
            analysis_stage, analysis_confidence,
        )

        dashboard_toto14.append({
            "match": m, "home_logo": home_info.get("logo"), "away_logo": away_info.get("logo"),
            "best_pick_display": best_pick_display, "p_h": pct_h, "p_d": pct_d, "p_a": pct_a,
            "analysis_version": ANALYSIS_VERSION, "analysis_confidence": analysis_confidence,
            "analysis_stage": analysis_stage,
            "picks_html": picks_html, "h_rank_html": f"<div class='rank-badge'>🏆 리그 순위: {h_rank}위</div>" if h_rank != 99 else "", "a_rank_html": f"<div class='rank-badge'>🏆 리그 순위: {a_rank}위</div>" if a_rank != 99 else "",
            "home_form": fetch_team_form_api(home_info.get("id"), heavy_ttl), "away_form": fetch_team_form_api(away_info.get("id"), heavy_ttl)
        })

    # 종료된 예전 경기가 TOP 3에 다시 등장하지 않도록 아직 시작하지 않은 경기만 선정한다.
    now_ts = datetime.now(timezone(timedelta(hours=9))).timestamp()
    upcoming_proto = [
        item for item in dashboard_proto
        if item.get("timestamp", 0) > now_ts and item.get("ev_sorted_picks")
    ]
    top_3_picks = sorted(
        upcoming_proto,
        key=lambda x: (x.get("reliability_score", 0), x.get("analysis_confidence", 0)),
        reverse=True,
    )[:3]

    final_output = {
        "proto": dashboard_proto, "toto14": dashboard_toto14,
        "toto14_meta": {"total_combinations": total_combinations, "single_pick_count": len(toto_14_matches) - double_pick_count, "double_pick_count": double_pick_count, "budget": total_combinations * 1000},
        "top3": top_3_picks,
        "source_meta": {
            "analysis_version": ANALYSIS_VERSION,
            "betman_proto_count": len(proto_matches),
            "display_proto_count": len(dashboard_proto),
            "betman_toto14_count": len(toto_14_matches),
            "display_toto14_count": len(dashboard_toto14),
            "proto_parity_ok": len(proto_matches) == len(dashboard_proto),
            "toto14_parity_ok": len(toto_14_matches) == len(dashboard_toto14),
            "api_usage": get_api_usage_status(),
            "generated_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        },
    }
    with open("dashboard_data.json", "w", encoding="utf-8") as f: json.dump(final_output, f, ensure_ascii=False)
    if len(proto_matches) != len(dashboard_proto):
        print(f"❌ 경기 수 불일치: 베트맨 {len(proto_matches)}경기 / 화면 데이터 {len(dashboard_proto)}경기")
    else:
        print(f"✅ 경기 수 일치 확인: 베트맨 = 화면 데이터 {len(dashboard_proto)}경기")
    print(f"✅ 대시보드 데이터 패키징 완료! ({ANALYSIS_VERSION} 과신 방지·핵심선수·신뢰도 적용)")

def auto_score_matches():
    print(f"\n[🤖 {time.strftime('%Y-%m-%d %H:%M:%S')}] 🔥 불도저 채점 엔진 가동 (정밀 API 고유 ID 추적)...")
    try:
        conn = sqlite3.connect("ai_predictions.db")
        cursor = conn.cursor()

        cursor.execute("SELECT match_id, home_team, away_team, prob_pick, ev_pick, match_time, api_fixture_id FROM predictions WHERE actual_result = 'PENDING' AND api_fixture_id > 0")
        pending_matches = cursor.fetchall()
        api_call_count = 0
        now = datetime.now(timezone(timedelta(hours=9)))

        # 과거 임시 데이터의 '시간 미정' 항목을 매 주기마다 전부 조회하던 문제를 차단한다.
        # 새 수집 데이터에는 실제 시간이 있으므로 시작된 경기만 채점 대상으로 삼는다.
        due_matches = []
        for row in pending_matches:
            match_id, h_team, a_team, prob_pick, ev_pick, match_time_str, fixture_id = row
            if not match_time_str or match_time_str in ["시간 미정", "마감/진행중"]:
                continue
            m_dt = parse_match_time(match_time_str)
            if now >= m_dt:
                due_matches.append(row)

        # API-Football의 ids 묶음 조회(최대 20개)를 사용한다. 99경기면
        # 99회가 아니라 최대 5회만 사용한다.
        for offset in range(0, len(due_matches), 20):
            batch = due_matches[offset:offset + 20]
            fixture_ids = sorted({str(int(row[6])) for row in batch if row[6]})
            if not fixture_ids:
                continue
            res = api_get(
                "/fixtures",
                params={"ids": "-".join(fixture_ids), "timezone": "Asia/Seoul"},
                timeout=8,
                purpose="scoring",
            )
            api_call_count += 1
            payload = res.json() if res.status_code == 200 else {}
            if payload.get("errors"):
                print(f"⚠️ 묶음 채점 API 오류: {payload.get('errors')}")
                continue
            fixture_map = {
                int(item.get("fixture", {}).get("id") or 0): item
                for item in payload.get("response", [])
            }

            for row in batch:
                match_id, h_team, a_team, prob_pick, ev_pick, match_time_str, fixture_id = row
                match_info = fixture_map.get(int(fixture_id))
                if not match_info:
                    continue
                status = match_info.get('fixture', {}).get('status', {}).get('short', '')

                if status in ['FT', 'AET', 'PEN']:
                    gh = match_info.get('goals', {}).get('home')
                    ga = match_info.get('goals', {}).get('away')
                    gh = int(gh) if gh is not None else 0
                    ga = int(ga) if ga is not None else 0
                    score_str = f"{gh}:{ga}"
                    is_corr_prob = evaluate_single_pick(prob_pick, h_team, a_team, gh, ga)
                    is_corr_ev = evaluate_single_pick(ev_pick, h_team, a_team, gh, ga)
                    ai_note = generate_real_ai_note(fixture_id, gh, ga, is_corr_prob, is_corr_ev)
                    cursor.execute("""
                        UPDATE predictions 
                        SET actual_score = ?, actual_result = 'FINISHED', is_correct_prob = ?, is_correct_ev = ?, ai_note = ? 
                        WHERE match_id = ?
                    """, (score_str, is_corr_prob, is_corr_ev, ai_note, match_id))
                    print(f"  ✨ [정밀 채점 완료] {h_team} vs {a_team} ({score_str})")
                elif status in ['CANC', 'PSTP', 'ABD', 'AWD', 'WO']:
                    cursor.execute("UPDATE predictions SET actual_result = 'CANCELED', ai_note = '💡 경기 취소/연기/몰수로 인한 무효 처리' WHERE match_id = ?", (match_id,))
                    print(f"  ⚠️ [경기 취소/연기 처리] {h_team} vs {a_team}")

        conn.commit()
        print(f"✅ 스마트 채점 사이클 종료 (묶음 조회 API 소모량: {api_call_count}회)")
    except Exception as e: 
        print(f"❌ [관제 봇 떡밥] 채점 중 오류: {e}")
    finally:
        if 'conn' in locals(): conn.close()

def _api_fixture_datetime(match_info):
    raw_date = match_info.get("fixture", {}).get("date")
    if not raw_date:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=9)))
    except (TypeError, ValueError):
        return None


def _request_fixture_board(params):
    """일시적인 API 지연에는 한 번 재시도하고 오류 응답은 데이터로 쓰지 않는다."""
    last_error = None
    for attempt in range(2):
        try:
            response = api_get(
                "/fixtures",
                params=params,
                timeout=12,
                purpose="live",
            )
            if response.status_code != 200:
                raise RuntimeError(f"API HTTP {response.status_code}")
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError(str(payload.get("errors")))
            return payload.get("response", [])
        except Exception as error:
            last_error = error
            if attempt == 0:
                time.sleep(1.5)
    raise RuntimeError(str(last_error or "라이브 API 응답 없음"))


def update_live_scores():
    print(f"\n[📡 {time.strftime('%Y-%m-%d %H:%M:%S')}] 실시간 라이브 데이터 업데이트 (5분 주기)...")
    try:
        conn = sqlite3.connect("ai_predictions.db")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT match_id, api_fixture_id, home_team, away_team, match_time
            FROM predictions
            WHERE actual_result = 'PENDING'
        """)
        pending_matches = cursor.fetchall()
        conn.close()

        # 한 API 경기가 프로토와 승무패14에 동시에 들어오더라도 둘 다 보존한다.
        pending_by_fixture = {}
        for row in pending_matches:
            fixture_id = int(row[1] or 0)
            if fixture_id:
                pending_by_fixture.setdefault(fixture_id, []).append(row)

        live_statuses = {'1H', 'HT', '2H', 'ET', 'BT', 'P', 'PEN', 'SUSP', 'INT', 'LIVE'}
        board = _request_fixture_board({"live": "all", "timezone": "Asia/Seoul"})
        live_fixtures = [
            item for item in board
            if item.get("fixture", {}).get("status", {}).get("short", "") in live_statuses
        ]

        # 일부 요금제/대회에서 live=all 응답이 비는 경우만 오늘 경기판을 한 번 확인한다.
        now_kst = datetime.now(timezone(timedelta(hours=9)))
        has_near_kickoff = any(
            abs((parse_match_time(row[4]) - now_kst).total_seconds()) <= 4 * 3600
            for row in pending_matches if row[4]
        )
        if not live_fixtures and has_near_kickoff:
            date_board = _request_fixture_board({
                "date": now_kst.strftime("%Y-%m-%d"),
                "timezone": "Asia/Seoul",
            })
            live_fixtures = [
                item for item in date_board
                if item.get("fixture", {}).get("status", {}).get("short", "") in live_statuses
            ]

        live_data_dict = {}
        score_updates = []
        fixture_updates = []
        recovered_links = 0
        used_match_ids = set()

        for match_info in live_fixtures:
            fixture = match_info.get("fixture", {})
            fixture_id = int(fixture.get("id") or 0)
            status_info = fixture.get("status", {})
            status = status_info.get("short", "")
            api_teams = match_info.get("teams", {})
            api_home = api_teams.get("home", {})
            api_away = api_teams.get("away", {})
            api_dt = _api_fixture_datetime(match_info)

            matched_rows = list(pending_by_fixture.get(fixture_id, []))
            matched_ids = {str(row[0]) for row in matched_rows}

            # fixture_id가 0이었던 경기는 팀 ID/영문명과 시작 시간을 함께 비교해 복구한다.
            for row in pending_matches:
                match_id, old_fixture_id, home_team, away_team, match_time = row
                match_id = str(match_id)
                if match_id in matched_ids or match_id in used_match_ids:
                    continue
                if api_dt is None or not match_time:
                    continue
                local_dt = parse_match_time(match_time)
                if abs((local_dt - api_dt).total_seconds()) > 8 * 3600:
                    continue
                if not team_matches_api(home_team, api_home.get("name"), api_home.get("id")):
                    continue
                if not team_matches_api(away_team, api_away.get("name"), api_away.get("id")):
                    continue
                matched_rows.append(row)
                matched_ids.add(match_id)
                if int(old_fixture_id or 0) != fixture_id:
                    fixture_updates.append((fixture_id, match_id))
                    recovered_links += 1

            if not matched_rows:
                continue

            goals_h = match_info.get('goals', {}).get('home')
            goals_a = match_info.get('goals', {}).get('away')
            goals_h = 0 if goals_h is None else goals_h
            goals_a = 0 if goals_a is None else goals_a
            score_str = f"{goals_h}:{goals_a}"
            event_str = ""

            # 점수판 1회 호출만으로 모든 경기 스코어를 갱신한다. 득점자/카드
            # 이벤트는 경기당 추가 호출이 필요해 기본 OFF로 둔다.
            if os.getenv("ENABLE_LIVE_EVENTS", "0") == "1":
                try:
                    evt_res = api_get(
                        "/fixtures/events",
                        params={"fixture": fixture_id},
                        timeout=7,
                        purpose="live",
                    )
                    events = evt_res.json().get("response", []) if evt_res.status_code == 200 else []
                    if events:
                        events.sort(key=lambda x: int(x.get('time', {}).get('elapsed') or 0), reverse=True)
                        latest_evt = events[0]
                        e_time = latest_evt.get('time', {}).get('elapsed', 0)
                        e_type = latest_evt.get('type', '')
                        e_detail = latest_evt.get('detail', '')
                        e_player = latest_evt.get('player', {}).get('name', '')
                        if e_type == "Goal": event_str = f"⚽ {e_time}' 득점! ({e_player})"
                        elif e_type == "Card" and "Red" in e_detail: event_str = f"🟥 {e_time}' 퇴장! ({e_player})"
                        elif e_type == "subst": event_str = f"🔄 {e_time}' 교체 - {e_player} OUT"
                except Exception as event_error:
                    print(f"⚠️ 라이브 이벤트 조회 실패({fixture_id}): {event_error}")

            for row in matched_rows:
                match_id = str(row[0])
                used_match_ids.add(match_id)
                live_data_dict[match_id] = {
                    "score": score_str.replace(":", " : "),
                    "event": event_str,
                    "is_live": True,
                    "status": status,
                    "elapsed": int(status_info.get("elapsed") or 0),
                    "fixture_id": fixture_id,
                }
                score_updates.append((score_str, match_id))

        if score_updates or fixture_updates:
            conn_tmp = sqlite3.connect("ai_predictions.db")
            cur_tmp = conn_tmp.cursor()
            if score_updates:
                cur_tmp.executemany("UPDATE predictions SET actual_score = ? WHERE match_id = ?", score_updates)
            if fixture_updates:
                cur_tmp.executemany("UPDATE predictions SET api_fixture_id = ? WHERE match_id = ?", fixture_updates)
            conn_tmp.commit()
            conn_tmp.close()

        with open("live_scores.json", "w", encoding="utf-8") as f:
            json.dump(live_data_dict, f, ensure_ascii=False)
        if recovered_links:
            print(f"✅ 라이브 팀명/시간 자동 연결 복구: {recovered_links}건")
        print(f"✅ 라이브 업데이트 완료! (현재 실제 진행 기록: {len(live_data_dict)}개)")
    except Exception as e:
        # 실패한 빈 응답으로 정상 스코어 파일을 덮어쓰지 않는다.
        print(f"❌ [관제 봇 떡밥] 라이브 스코어 에러: {e} (마지막 정상 파일 보존)")

def _parse_odd_buttons(market):
    values = []
    for button in market.select("button.btnChk"):
        odd_node = button.select_one(".db")
        if not odd_node:
            continue
        match = re.search(r'\d+(?:\.\d+)?', odd_node.get_text(" ", strip=True).replace(',', ''))
        if match:
            values.append(float(match.group(0)))
    return values


def parse_betman_proto_html(html):
    """베트맨 프로토 페이지에서 축구 한 경기당 하나의 실제 시장을 추출한다."""
    soup = BeautifulSoup(html, 'html.parser')
    parsed, seen = [], set()

    for row in soup.select(".box-data-group [data-rowname]"):
        row_name = row.get("data-rowname", "")
        if not row_name or row_name in seen:
            continue
        seen.add(row_name)

        team_nodes = row.select(".teams .team")
        if len(team_nodes) < 2:
            continue
        home = team_nodes[0].get_text(" ", strip=True)
        away = team_nodes[1].get_text(" ", strip=True)
        if not home or not away:
            continue

        row_text = row.get_text(" ", strip=True)
        time_match = re.search(r'\d{2}\.\d{2}\s*\([^)]+\)\s*\d{2}:\d{2}', row_text)
        if not time_match:
            continue
        match_time = re.sub(r'\s+', ' ', time_match.group(0)).strip()
        league_node = row.select_one(".competition")
        league = league_node.get_text(" ", strip=True) if league_node else "축구"

        market_root = row.select_one(".accordion-content")
        if market_root is None:
            market_root = row.find_next_sibling(class_=lambda value: value and "accordion-content" in value)
        if market_root is None:
            parent = row.parent
            market_root = parent.find_next_sibling(class_=lambda value: value and "accordion-content" in value) if parent else None
        if market_root is None:
            parent = row.parent
            market_root = parent.select_one(".accordion-content") if parent else None
        if market_root is None:
            continue

        deadline_match = re.search(r'(\d{2}:\d{2})\s*마감', market_root.get_text(" ", strip=True))
        deadline_time = f"{deadline_match.group(1)} 마감" if deadline_match else ""

        main_market = None
        handicap_market = None
        under_over_market = None
        for market in market_root.select("ul.list-proto-detail > li[data-matchseq]"):
            game_node = market.select_one(".competition-detail .game")
            game_name = re.sub(r'\s+', ' ', game_node.get_text(" ", strip=True)) if game_node else ""
            if game_name == "축구 승무패" and main_market is None:
                main_market = market
            elif game_name == "축구 핸디캡" and handicap_market is None:
                handicap_market = market
            elif game_name == "축구 언더오버" and under_over_market is None:
                under_over_market = market

        if main_market is None:
            continue
        odds_1x2 = _parse_odd_buttons(main_market)
        if len(odds_1x2) < 3 or min(odds_1x2[:3]) <= 1.0:
            continue

        odds_handi = _parse_odd_buttons(handicap_market) if handicap_market else []
        odds_uo = _parse_odd_buttons(under_over_market) if under_over_market else []
        handicap_text = handicap_market.get_text(" ", strip=True) if handicap_market else ""
        under_over_text = under_over_market.get_text(" ", strip=True) if under_over_market else ""
        handicap_match = re.search(r'H\s*([+-]?\d+(?:\.\d+)?)', handicap_text)
        under_over_match = re.search(r'U/O\s*(\d+(?:\.\d+)?)', under_over_text)
        match_id = str(main_market.get("data-matchseq") or row_name)

        parsed.append({
            "id": match_id,
            "league": league,
            "time": match_time,
            "match_time": match_time,
            "deadline_time": deadline_time,
            "home": home,
            "away": away,
            "odd_h": odds_1x2[0],
            "odd_d": odds_1x2[1],
            "odd_a": odds_1x2[2],
            "handi_h": odds_handi[0] if len(odds_handi) >= 3 else 0.0,
            "handi_d": odds_handi[1] if len(odds_handi) >= 3 else 0.0,
            "handi_a": odds_handi[2] if len(odds_handi) >= 3 else 0.0,
            "handi_base": float(handicap_match.group(1)) if handicap_match else 0.0,
            "uo_under": odds_uo[0] if len(odds_uo) >= 2 else 0.0,
            "uo_over": odds_uo[1] if len(odds_uo) >= 2 else 0.0,
            "uo_base": float(under_over_match.group(1)) if under_over_match else 0.0,
        })
    return parsed


def parse_betman_toto14_html(html, round_id="current"):
    """축구 승무패 표의 공식 행 번호, 팀, 시간, 투표율을 그대로 읽는다."""
    soup = BeautifulSoup(html, 'html.parser')
    parsed = []
    for row in soup.select("table#grid_victory tbody#grid_victory_tbody > tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 6:
            continue
        num_match = re.search(r'(\d+)\s*경기', cells[0].get_text(" ", strip=True))
        if not num_match:
            continue
        num = int(num_match.group(1))

        raw_time = cells[1].get_text(" ", strip=True)
        time_match = re.search(r'(?:\d{2}\.)?(\d{2}\.\d{2}\s*\([^)]+\)\s*\d{2}:\d{2})', raw_time)
        match_time = re.sub(r'\s+', ' ', time_match.group(1)).strip() if time_match else "시간 미정"

        teams_box = cells[2].select_one(".vsDIv") or cells[2]
        team_parts = teams_box.find_all("div", recursive=False)
        if len(team_parts) < 2:
            continue
        home = team_parts[0].get_text(" ", strip=True)
        away = re.sub(r'^\s*v\s*s\s*', '', team_parts[1].get_text(" ", strip=True), flags=re.IGNORECASE).strip()
        if not home or not away:
            continue

        vote_values = []
        for cell in cells[3:6]:
            vote_match = re.search(r'(\d+(?:\.\d+)?)\s*%', cell.get_text(" ", strip=True))
            vote_values.append(float(vote_match.group(1)) if vote_match else None)

        parsed.append({
            "id": f"{round_id}_{num}",
            "round_id": str(round_id),
            "num": num,
            "league": "축구 승무패",
            "home": home,
            "away": away,
            "match_time": match_time,
            "vote_h": vote_values[0],
            "vote_d": vote_values[1],
            "vote_a": vote_values[2],
        })
    return sorted(parsed, key=lambda item: item["num"])


def _accept_alert(driver):
    try:
        driver.switch_to.alert.accept()
    except Exception:
        pass


def scrape_betman():
    print(f"\n[🔄 {time.strftime('%Y-%m-%d %H:%M:%S')}] 베트맨 실제 경기/배당 수집 가동...")
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-software-rasterizer')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-background-networking')
    options.add_argument('--disable-component-update')
    options.add_argument('--disable-default-apps')
    options.add_argument('--disable-sync')
    options.add_argument('--metrics-recording-only')
    options.add_argument('--no-first-run')
    options.add_argument('--renderer-process-limit=2')
    options.add_argument('--disk-cache-size=1')
    options.add_argument('--media-cache-size=1')
    options.add_argument('--js-flags=--max-old-space-size=256')
    options.add_argument('--window-size=1365,900')
    options.add_argument('--blink-settings=imagesEnabled=false')
    options.add_argument('--disable-features=Translate,BackForwardCache,OptimizationHints,MediaRouter')
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0')
    options.page_load_strategy = 'eager'

    driver = None
    matches, matches_14 = [], []
    hub_url = "https://www.betman.co.kr/main/mainPage/gamebuy/buyableGameList.do"

    try:
        service = Service(EdgeChromiumDriverManager().install())
        driver = webdriver.Edge(service=service, options=options)
        driver.set_page_load_timeout(60)

        try:
            driver.get(hub_url)
            _accept_alert(driver)
            proto_btn = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((
                By.XPATH,
                "//a[contains(normalize-space(.), '프로토 승부식') and contains(normalize-space(.), '회차')]",
            )))
            driver.execute_script("arguments[0].click();", proto_btn)
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".box-data-group [data-rowname]"))
            )
            time.sleep(3)
            for _ in range(12):
                visible_more = [button for button in driver.find_elements(
                    By.XPATH,
                    "//*[self::button or self::a][contains(normalize-space(.), '더보기')]",
                ) if button.is_displayed()]
                if not visible_more:
                    break
                driver.execute_script("arguments[0].click();", visible_more[0])
                time.sleep(1)
            all_proto_matches = parse_betman_proto_html(driver.page_source)
            # 운영 환경에서는 베트맨에 표시된 축구 경기 전부를 사용한다.
            # 개발자가 명시적으로 허용한 경우에만 테스트용 개수 제한을 켤 수 있다.
            proto_limit = 0
            if os.getenv("ALLOW_PROTO_LIMIT", "0") == "1":
                try:
                    proto_limit = max(0, int(os.getenv("MAX_PROTO_MATCHES", "0")))
                except ValueError:
                    proto_limit = 0
            matches = all_proto_matches[:proto_limit] if proto_limit else all_proto_matches
            print(f"✅ 프로토 실제 축구 경기 발견: {len(all_proto_matches)}경기 / 분석 대상: {len(matches)}경기")
        except Exception as error:
            print(f"❌ 프로토 승부식 수집 실패: {type(error).__name__}: {error}")

        try:
            driver.get(hub_url)
            _accept_alert(driver)
            toto_btn = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((
                By.XPATH,
                "//a[contains(normalize-space(.), '축구 승무패') and contains(normalize-space(.), '회차')]",
            )))
            driver.execute_script("arguments[0].click();", toto_btn)
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#grid_victory_tbody > tr"))
            )
            time.sleep(3)
            round_match = re.search(r'gmTs=(\d+)', driver.current_url)
            round_id = round_match.group(1) if round_match else "current"
            matches_14 = parse_betman_toto14_html(driver.page_source, round_id)
            print(f"✅ 축구 승무패 {round_id}회차 추출: {len(matches_14)}경기")
        except Exception as error:
            print(f"❌ 축구 승무패 수집 실패: {type(error).__name__}: {error}")

    except Exception as error:
        print(f"❌ 크롤링 브라우저 시작 실패: {type(error).__name__}: {error}")
    finally:
        if driver:
            try:
                driver.quit()
                print("🧹 엣지 브라우저 정상 종료 완료.")
            except Exception:
                pass

    old_data = {}
    try:
        with open("betman_data.json", "r", encoding="utf-8") as file:
            old_data = json.load(file)
    except Exception:
        pass

    # 한쪽 페이지만 일시 실패하면 마지막 정상본을 보존하되, 과거 경기를 새 목록에
    # 합쳐 넣지는 않는다. 이것이 114개 가짜/중복 경기가 다시 살아나는 것을 막는다.
    final_proto = matches if matches else old_data.get("proto_matches", [])
    final_toto14 = matches_14 if matches_14 else old_data.get("toto_14_matches", [])
    if not matches:
        print("⚠️ 이번 프로토 수집이 비어 있어 마지막 파일을 보존합니다.")
    if not matches_14:
        print("⚠️ 이번 승무패 14경기 수집이 비어 있어 마지막 파일을 보존합니다.")
    if not matches and not matches_14:
        print("❌ 새 데이터가 전혀 없어 대시보드 갱신을 중단합니다.")
        return False

    result = {
        "proto_matches": final_proto,
        "toto_14_matches": final_toto14,
        "collected_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
    }
    with open("betman_data.json", "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=4)
    print(f"✅ 수집 완료: 프로토 {len(final_proto)}경기 / 승무패 {len(final_toto14)}경기")
    return True

def run_live_score_job():
    update_live_scores()
    upload_to_github("live_scores.json")

def run_master_job():
    init_cache_db() 
    if not scrape_betman():
        return
    build_dashboard_data()
    upload_to_github("dashboard_data.json")
    
    auto_score_matches()
    upload_to_github("ai_predictions.db")

if __name__ == "__main__":
    download_latest_db_from_github()
    
    run_master_job() 
    run_live_score_job()
    
    schedule.every(20).minutes.do(run_master_job) 
    schedule.every(5).minutes.do(run_live_score_job) 
    
    print("\n🚀 [마스터 스케줄러] 무거운 수집 20분 / 실시간 라이브 스코어 5분 주기 분리 적용 완료!")
    while True:
        schedule.run_pending()
        time.sleep(10)
