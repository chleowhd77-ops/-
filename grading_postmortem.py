"""Verified, machine-readable grading postmortems.

This module deliberately separates facts from interpretation.  A missed pick is
always explained first by the final-score condition that failed.  Match-flow
observations are added only when official statistics or stored events exist.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


POSTMORTEM_SCHEMA_VERSION = "grading-postmortem.v1"

_STAT_TYPES = {
    "ball possession": ("possession", "점유율", "%"),
    "shots on goal": ("shots_on_goal", "유효슈팅", "개"),
    "total shots": ("total_shots", "전체슈팅", "개"),
    "shots off goal": ("shots_off_goal", "빗나간 슈팅", "개"),
    "blocked shots": ("blocked_shots", "차단된 슈팅", "개"),
    "shots insidebox": ("shots_inside_box", "박스 안 슈팅", "개"),
    "corner kicks": ("corners", "코너킥", "개"),
    "goalkeeper saves": ("goalkeeper_saves", "골키퍼 선방", "개"),
    "red cards": ("red_cards", "퇴장", "명"),
    "expected_goals": ("expected_goals", "기대득점(xG)", ""),
    "expected goals": ("expected_goals", "기대득점(xG)", ""),
}


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def _display_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def normalize_official_stats(raw_stats: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only provider values that were actually returned."""
    normalized: List[Dict[str, Any]] = []
    for index, team_stats in enumerate(list(raw_stats or [])[:2]):
        if not isinstance(team_stats, dict):
            continue
        metrics: Dict[str, float] = {}
        for raw in team_stats.get("statistics", []) or []:
            if not isinstance(raw, dict):
                continue
            metric = _STAT_TYPES.get(str(raw.get("type") or "").strip().casefold())
            value = _number(raw.get("value"))
            if metric and value is not None:
                metrics[metric[0]] = value
        normalized.append({
            "side": "home" if index == 0 else "away",
            "team": str((team_stats.get("team") or {}).get("name") or ("홈팀" if index == 0 else "원정팀")),
            "metrics": metrics,
        })
    return normalized


def official_stats_text(stats: Iterable[Dict[str, Any]]) -> str:
    teams = []
    labels = {value[0]: (value[1], value[2]) for value in _STAT_TYPES.values()}
    preferred = (
        "possession", "total_shots", "shots_on_goal", "expected_goals",
        "corners", "goalkeeper_saves", "red_cards",
    )
    for team in stats or []:
        metrics = team.get("metrics") or {}
        values = []
        for key in preferred:
            if key not in metrics:
                continue
            label, suffix = labels[key]
            values.append(f"{label} {_display_number(float(metrics[key]))}{suffix}")
        if values:
            teams.append(f"{team.get('team') or team.get('side')} {' · '.join(values)}")
    return " / ".join(teams)


def _team_side_from_pick(pick: str, home_team: str, away_team: str) -> Optional[str]:
    compact = re.sub(r"\s+", "", pick).casefold()
    home_key = re.sub(r"\s+", "", str(home_team or "")).casefold()
    away_key = re.sub(r"\s+", "", str(away_team or "")).casefold()
    if away_key and away_key in compact:
        return "away"
    if home_key and home_key in compact:
        return "home"
    if compact in {"승", "homewin", "home"}:
        return "home"
    if compact in {"패", "awaywin", "away"}:
        return "away"
    return None


def _miss_reason(
    pick: str,
    home_team: str,
    away_team: str,
    goals_h: int,
    goals_a: int,
) -> Dict[str, Any]:
    raw = str(pick or "").strip()
    upper = raw.upper()
    total = goals_h + goals_a
    uo = re.search(r"(?:U/O\s*)?(\d+(?:\.\d+)?)", upper)
    if ("언더" in raw or "UNDER" in upper) and uo:
        line = float(uo.group(1))
        relation = "같아" if total == line else "넘어"
        return {
            "pick_type": "under",
            "code": "TOTAL_OVER_UNDER_LINE",
            "reason": f"총 {total}골로 언더 기준 {line:g}골과 {relation} 언더 적중 조건을 충족하지 못했습니다.",
            "selected_side": None,
            "facts": {"goal_total": total, "line": line},
        }
    if ("오버" in raw or "OVER" in upper) and uo:
        line = float(uo.group(1))
        relation = "같아" if total == line else "밑돌아"
        return {
            "pick_type": "over",
            "code": "TOTAL_BELOW_OVER_LINE",
            "reason": f"총 {total}골로 오버 기준 {line:g}골과 {relation} 오버 적중 조건을 충족하지 못했습니다.",
            "selected_side": None,
            "facts": {"goal_total": total, "line": line},
        }

    handicap = (
        re.search(r"\[\s*([+-]?\d+(?:\.\d+)?)\s*\]", raw)
        or re.search(r"([+-]?\d+(?:\.\d+)?)\s*적용\s*후", raw)
    )
    if ("핸디" in raw or "적용 후" in raw) and handicap:
        line = float(handicap.group(1))
        adjusted_home = goals_h + line
        actual = "핸디승" if adjusted_home > goals_a else "핸디패" if adjusted_home < goals_a else "핸디무"
        declared = re.search(r"(?:핸디|적용\s*후)\s*(승|무|패)", raw)
        expected = f"핸디{declared.group(1)}" if declared else "선택 조건"
        return {
            "pick_type": "handicap",
            "code": "HANDICAP_RESULT_MISMATCH",
            "reason": (
                f"정규 점수 {goals_h}:{goals_a}에 홈 기준 {line:+g}를 적용한 결과가 "
                f"{actual}({adjusted_home:g}:{goals_a})로, 선택한 {expected}와 달랐습니다."
            ),
            "selected_side": _team_side_from_pick(raw, home_team, away_team),
            "facts": {"handicap": line, "adjusted_home_goals": adjusted_home, "away_goals": goals_a},
        }

    is_draw_pick = "무승부" in raw or raw == "무" or "DRAW" in upper
    if is_draw_pick:
        return {
            "pick_type": "draw",
            "code": "DRAW_RESULT_MISMATCH",
            "reason": f"무승부를 선택했지만 최종 점수 {goals_h}:{goals_a}로 승패가 갈렸습니다.",
            "selected_side": None,
            "facts": {"home_goals": goals_h, "away_goals": goals_a},
        }

    side = _team_side_from_pick(raw, home_team, away_team)
    if side:
        selected = home_team if side == "home" else away_team
        if goals_h == goals_a:
            code = "DRAW_NOT_COVERED"
            reason = f"{selected} 승을 선택했지만 최종 {goals_h}:{goals_a} 무승부로 승리 조건이 성립하지 않았습니다."
        else:
            winner = home_team if goals_h > goals_a else away_team
            code = "SELECTED_SIDE_LOST"
            reason = f"{selected} 승을 선택했지만 최종 {goals_h}:{goals_a}, 실제 승리 팀은 {winner}였습니다."
        return {
            "pick_type": "match_winner",
            "code": code,
            "reason": reason,
            "selected_side": side,
            "facts": {"home_goals": goals_h, "away_goals": goals_a},
        }

    return {
        "pick_type": "unknown",
        "code": "PICK_CONDITION_NOT_MET",
        "reason": f"선택한 픽의 적중 조건이 최종 점수 {goals_h}:{goals_a}에서 충족되지 않았습니다.",
        "selected_side": None,
        "facts": {"home_goals": goals_h, "away_goals": goals_a},
    }


def _stats_by_side(stats: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    return {
        str(team.get("side")): dict(team.get("metrics") or {})
        for team in stats or []
        if team.get("side") in {"home", "away"}
    }


def _verified_observations(
    misses: Iterable[Dict[str, Any]],
    stats: Iterable[Dict[str, Any]],
    event_timeline: Iterable[str],
) -> List[Dict[str, str]]:
    observations: List[Dict[str, str]] = []
    by_side = _stats_by_side(stats)
    compared_sides = set()
    for miss in misses:
        side = miss.get("selected_side")
        if side not in {"home", "away"} or side in compared_sides:
            continue
        compared_sides.add(side)
        opponent = "away" if side == "home" else "home"
        selected = by_side.get(side, {})
        other = by_side.get(opponent, {})
        if "shots_on_goal" in selected and "shots_on_goal" in other:
            left = int(selected["shots_on_goal"])
            right = int(other["shots_on_goal"])
            if left < right:
                observations.append({
                    "code": "ON_TARGET_DEFICIT",
                    "text": f"선택 팀의 유효슈팅이 {left}개로 상대 {right}개보다 적었습니다.",
                    "evidence": "official_statistics",
                })
            else:
                observations.append({
                    "code": "RESULT_DESPITE_ON_TARGET_EDGE",
                    "text": f"선택 팀 유효슈팅은 {left}개로 상대 {right}개보다 적지 않았지만 승리 결과로 이어지지 않았습니다.",
                    "evidence": "official_statistics",
                })

    red_events = []
    late_goals = []
    for line in event_timeline or []:
        text = str(line or "").strip()
        minute_match = re.search(r"(\d+)(?:\+(\d+))?", text)
        minute = int(minute_match.group(1)) if minute_match else -1
        if "퇴장" in text or "🟥" in text:
            red_events.append(text)
        if minute >= 80 and ("득점" in text or "⚽" in text or "골" in text):
            late_goals.append(text)
    if red_events:
        observations.append({
            "code": "RED_CARD_EVENT",
            "text": f"공식 사건 기록에서 퇴장 {len(red_events)}건이 확인됐습니다: {red_events[0]}",
            "evidence": "official_events",
        })
    if late_goals:
        observations.append({
            "code": "LATE_GOAL_EVENT",
            "text": f"80분 이후 득점 {len(late_goals)}건이 확인됐습니다: {late_goals[-1]}",
            "evidence": "official_events",
        })
    return observations


def build_postmortem(
    home_team: str,
    away_team: str,
    prob_pick: str,
    ev_pick: str,
    goals_h: int,
    goals_a: int,
    is_correct_prob: int,
    is_correct_ev: int,
    has_ev_pick: bool = True,
    official_stats: Optional[Iterable[Dict[str, Any]]] = None,
    event_timeline: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    stats = list(official_stats or [])
    events = [str(line).strip() for line in (event_timeline or []) if str(line).strip()]
    misses = []
    if int(is_correct_prob or 0) != 1:
        misses.append({"slot": "probability", "label": "최종 추천픽", "pick": str(prob_pick or ""), **_miss_reason(prob_pick, home_team, away_team, int(goals_h), int(goals_a))})
    if has_ev_pick and int(is_correct_ev or 0) != 1:
        misses.append({"slot": "alternative", "label": "기존 배당형 대안픽", "pick": str(ev_pick or ""), **_miss_reason(ev_pick, home_team, away_team, int(goals_h), int(goals_a))})

    observations = _verified_observations(misses, stats, events)
    limitations = []
    if misses and not any((team.get("metrics") or {}) for team in stats):
        limitations.append("공식 세부 경기 통계가 없어 슈팅·점유율·기대득점에 따른 경기력 원인까지는 확정하지 않았습니다.")
    if misses and not events:
        limitations.append("공식 사건 기록이 없어 퇴장·부상·후반 득점 같은 경기 중 변수는 판정하지 않았습니다.")
    return {
        "schema_version": POSTMORTEM_SCHEMA_VERSION,
        "match": {"home": str(home_team or ""), "away": str(away_team or ""), "final_score": [int(goals_h), int(goals_a)]},
        "picks": {"probability": str(prob_pick or ""), "alternative": str(ev_pick or "") if has_ev_pick else ""},
        "misses": misses,
        "verified_observations": observations,
        "official_stats": stats,
        "event_timeline": events[-8:],
        "limitations": limitations,
        "learning_tags": list(dict.fromkeys([item["code"] for item in misses] + [item["code"] for item in observations])),
    }


def postmortem_text(payload: Dict[str, Any]) -> str:
    misses = payload.get("misses") or []
    observations = payload.get("verified_observations") or []
    limitations = payload.get("limitations") or []
    sections = []
    if misses:
        lines = [f"- {item.get('label')}({item.get('pick') or '픽 정보 없음'}): {item.get('reason')}" for item in misses]
        sections.append("[미적중 원인 · 확인된 결과]\n" + "\n".join(lines))
    if observations:
        sections.append("[현실 근거 · 공식 데이터 관찰]\n" + "\n".join(f"- {item.get('text')}" for item in observations))
    tags = payload.get("learning_tags") or []
    if tags:
        sections.append("[학습 로봇 태그] " + ", ".join(tags))
    if limitations:
        sections.append("[자료 한계]\n" + "\n".join(f"- {item}" for item in limitations))
    return "\n\n".join(sections)


def postmortem_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def parse_postmortem_json(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or ""))
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def events_from_note(raw_note: Any) -> List[str]:
    raw = str(raw_note or "")
    marker = "🎬"
    if marker not in raw:
        return []
    tail = raw.split(marker, 1)[1]
    events = []
    for line in tail.splitlines():
        text = line.strip()
        if text and "사건 기록" not in text:
            events.append(text)
    return events[-8:]


def stats_from_note(raw_note: Any) -> List[Dict[str, Any]]:
    """Recover only explicit paired numbers from legacy grading notes."""
    raw = str(raw_note or "")
    paired_patterns = {
        "possession": r"점유율\s*\(\s*(\d+(?:\.\d+)?)%?\s*(?:vs|대)\s*(\d+(?:\.\d+)?)%?\s*\)",
        "shots_on_goal": r"유효슈팅\s*\(\s*(\d+(?:\.\d+)?)\s*개?\s*(?:vs|대)\s*(\d+(?:\.\d+)?)\s*개?\s*\)",
        "total_shots": r"전체슈팅\s*\(\s*(\d+(?:\.\d+)?)\s*개?\s*(?:vs|대)\s*(\d+(?:\.\d+)?)\s*개?\s*\)",
    }
    home_metrics: Dict[str, float] = {}
    away_metrics: Dict[str, float] = {}
    for key, pattern in paired_patterns.items():
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            home_metrics[key] = float(match.group(1))
            away_metrics[key] = float(match.group(2))
    if not home_metrics and not away_metrics:
        return []
    return [
        {"side": "home", "team": "홈팀", "metrics": home_metrics},
        {"side": "away", "team": "원정팀", "metrics": away_metrics},
    ]
