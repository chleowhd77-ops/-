"""Independent, administrator-only investment-pick ledger.

This worker deliberately does not alter customer official picks, robot picks,
V2 Alphago picks, V3 learning picks, Toto14 marks, or any SQLite table.  It
reads frozen pre-kickoff candidates and completed grades, freezes one separate
manager candidate per eligible future fixture in its own JSON ledger, and
grades only that frozen selection after the result is available.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import sqlite3
import tempfile
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from api_engine import ANALYSIS_VERSION

MANAGER_ENGINE_VERSION = "manager-investment-four-analysts-v3"
OUTPUT_SCHEMA = "dj-sports.manager-investment-ledger.v2"
KST = timezone(timedelta(hours=9))

# These are safety filters, not targets to fill.  A candidate must clear every
# applicable gate; an empty day is an intended and valid result.
MIN_HISTORY_ROWS = 30
MIN_DATA_CONFIDENCE = 0.35
MIN_CONSERVATIVE_EV = 0.015
MIN_MARKET_EDGE = 0.01
HIGH_ODDS_BOUNDARY = 2.20
MAX_VALUE_ODDS = 8.0
MIN_HIGH_ODDS_HISTORY_ROWS = 24
MAX_CORE_PICKS = 3
MAX_VALUE_PICKS = 3
HISTORY_PRIOR = 24.0


class ManagerNotReady(RuntimeError):
    """Raised when no safe independent manager calculation can be made."""


@dataclass(frozen=True)
class HistoricalStat:
    count: int = 0
    hits: int = 0

    @property
    def rate(self) -> float:
        return self.hits / self.count if self.count else 0.0


@dataclass(frozen=True)
class PendingSnapshot:
    match_id: str
    snapshot_id: int
    created_at: str
    kickoff_at: str
    home: str
    away: str
    analysis_version: str
    candidates: tuple[dict[str, Any], ...]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _probability(value: Any, default: float = 0.0) -> float:
    number = _number(value, default)
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, type(default)):
        return value
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _evaluate_single_pick(
    pick_str: Any, home_team: Any, away_team: Any, goals_home: int, goals_away: int
) -> int:
    """Use the same settlement semantics as the main scorer without API imports."""
    pick_text = str(pick_str or "").upper()
    home_text = str(home_team or "").upper()
    away_text = str(away_team or "").upper()
    for pick in (part.strip() for part in pick_text.split(",")):
        if "핸디" in pick or "적용 후" in pick:
            match = re.search(r"\[\s*([+-]?\d+(?:\.\d+)?)\s*\]", pick)
            if match is None:
                match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(?:적용\s*후|HANDICAP)", pick)
            if match is not None:
                adjusted_home = float(goals_home) + float(match.group(1))
                actual = "승" if adjusted_home > goals_away else ("패" if adjusted_home < goals_away else "무")
                expected = re.search(r"(?:핸디|적용\s*후)\s*(승|무|패)", pick)
                if expected and expected.group(1) == actual:
                    return 1
            continue
        if "무승부" in pick or pick == "무" or "DRAW" in pick:
            if goals_home == goals_away:
                return 1
        if "승" in pick or "WIN" in pick:
            if away_text and away_text in pick:
                if goals_home < goals_away:
                    return 1
            elif goals_home > goals_away and (not home_text or home_text in pick or pick == "승"):
                return 1
        if pick == "패" and goals_home < goals_away:
            return 1
        if "언더" in pick or "오버" in pick:
            line = re.search(r"(\d+(?:\.\d+)?)", pick)
            if line:
                threshold = float(line.group(1))
                if "언더" in pick and goals_home + goals_away < threshold:
                    return 1
                if "오버" in pick and goals_home + goals_away > threshold:
                    return 1
    return 0


def _readonly_connection(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    if not path.is_file():
        raise ManagerNotReady(f"database not found: {path}")
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _kickoff_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST)
    except ValueError:
        pass
    compact = re.sub(r"\s*\([^)]*\)", "", text).strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%y.%m.%d %H:%M"):
        try:
            return datetime.strptime(compact, pattern).replace(tzinfo=KST)
        except ValueError:
            continue
    return None


def _stage_order(stage: Any) -> int:
    stages = ("T-24-initial", "T-90", "T-60", "T-60-lineup", "T-30-final", "T-3-refresh")
    try:
        return stages.index(str(stage or ""))
    except ValueError:
        return -1


def _candidate_from_snapshot(
    candidates: list[dict[str, Any]], row: sqlite3.Row
) -> dict[str, Any]:
    market = str(row["market_key"] or "")
    raw_pick = str(row["raw_pick"] or "")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if (
            str(candidate.get("market_key") or "") == market
            and str(candidate.get("raw_pick") or "") == raw_pick
        ):
            return dict(candidate)
    return {
        "market_key": market,
        "raw_pick": raw_pick,
        "model_probability": row["model_probability"],
        "fair_probability": row["fair_probability"],
        "odd": row["odd"],
    }


def _bucket(probability: float) -> int:
    return max(0, min(9, int(probability * 10)))


def _odds_bucket(odd: float) -> str:
    if odd < 1.8:
        return "under-1.8"
    if odd < 2.2:
        return "1.8-2.19"
    if odd < 3.0:
        return "2.2-2.99"
    if odd < 5.0:
        return "3-4.99"
    return "5-7.99"


def _load_history(database_path: str | Path) -> dict[str, Any]:
    """Read only completed, frozen pre-kickoff candidates for calibration."""
    connection = _readonly_connection(database_path)
    try:
        required = {"prediction_candidate_results", "prediction_analysis_snapshots"}
        missing = required - _table_names(connection)
        if missing:
            raise ManagerNotReady("required tables missing: " + ", ".join(sorted(missing)))
        rows = connection.execute(
            """
            SELECT result.market_key, result.model_probability, result.is_correct,
                   snapshot.id AS snapshot_id, result.raw_pick, result.fair_probability,
                   result.odd
            FROM prediction_candidate_results AS result
            JOIN prediction_analysis_snapshots AS snapshot
              ON snapshot.id = result.analysis_snapshot_id
            WHERE result.is_correct IN (0, 1)
              AND COALESCE(result.actual_score, '') NOT IN ('', '-:-', 'PENDING', 'UNKNOWN')
              AND snapshot.stage LIKE 'T-%'
            ORDER BY result.graded_at ASC, result.id ASC
            LIMIT 50000
            """
        ).fetchall()
        # The same snapshot has many candidate results. Read and parse its
        # candidate list once, never once per joined result row.
        candidate_maps = {}
        ids = sorted({int(row['snapshot_id']) for row in rows})
        for start in range(0,len(ids),128):
            batch=ids[start:start+128]
            placeholders=','.join('?' for _ in batch)
            for sid,payload in connection.execute(
                f'SELECT id,candidates_json FROM prediction_analysis_snapshots WHERE id IN ({placeholders})',batch):
                mapping={}
                for candidate in _json(payload,[]):
                    if isinstance(candidate,dict):
                        key=(str(candidate.get('market_key') or ''),str(candidate.get('raw_pick') or ''))
                        mapping.setdefault(key,{k:candidate[k] for k in ('market_key','model_probability','odd') if k in candidate})
                candidate_maps[int(sid)]=mapping
    finally:
        connection.close()

    overall: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    buckets: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0, 0])
    odds_buckets: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        candidate = candidate_maps.get(int(row['snapshot_id']),{}).get(
            (str(row['market_key'] or ''),str(row['raw_pick'] or '')), {})
        market = str(candidate.get("market_key") or row["market_key"] or "")
        probability = _probability(
            candidate.get("model_probability", row["model_probability"])
        )
        if not market or not 0 < probability < 1:
            continue
        outcome = int(row["is_correct"])
        overall[market][0] += 1
        overall[market][1] += outcome
        buckets[(market, _bucket(probability))][0] += 1
        buckets[(market, _bucket(probability))][1] += outcome
        odd = _number(candidate.get("odd", row["odd"]))
        if odd > 1.0:
            odds_buckets[_odds_bucket(odd)][0] += 1
            odds_buckets[_odds_bucket(odd)][1] += outcome

    history_rows = sum(value[0] for value in overall.values())
    if history_rows < MIN_HISTORY_ROWS:
        raise ManagerNotReady(
            f"completed frozen candidate history is below {MIN_HISTORY_ROWS}: {history_rows}"
        )
    return {
        "overall": {key: HistoricalStat(value[0], value[1]) for key, value in overall.items()},
        "buckets": {key: HistoricalStat(value[0], value[1]) for key, value in buckets.items()},
        "odds_buckets": {key: HistoricalStat(value[0], value[1]) for key, value in odds_buckets.items()},
        "history_rows": history_rows,
    }


def _load_pending_snapshots(database_path: str | Path) -> list[PendingSnapshot]:
    """Return only snapshots for fixtures that have not yet kicked off."""
    connection = _readonly_connection(database_path)
    try:
        required = {"predictions", "prediction_analysis_snapshots"}
        missing = required - _table_names(connection)
        if missing:
            raise ManagerNotReady("required tables missing: " + ", ".join(sorted(missing)))
        rows = connection.execute(
            """
            SELECT s.id, s.match_id, s.stage, s.created_at,
                   s.analysis_version,
                   p.match_time, p.home_team, p.away_team
            FROM prediction_analysis_snapshots AS s
            JOIN predictions AS p ON p.match_id = s.match_id
            WHERE COALESCE(p.actual_result, 'PENDING') = 'PENDING'
              AND COALESCE(p.is_toto14, 0) = 0
              AND (s.stage LIKE 'T-%' OR s.stage = 'regular')
              AND s.analysis_version = ?
              AND p.match_id NOT LIKE 'WORLD_%'
            ORDER BY s.match_id ASC, s.id DESC
            """,
            (ANALYSIS_VERSION,),
        ).fetchall()
    finally:
        connection.close()

    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        match_id = str(row["match_id"] or "").strip()
        if not match_id:
            continue
        prior = latest.get(match_id)
        if prior is None or (
            _stage_order(row["stage"]), str(row["created_at"]), int(row["id"])
        ) > (
            _stage_order(prior["stage"]), str(prior["created_at"]), int(prior["id"])
        ):
            latest[match_id] = row

    now = datetime.now(KST)
    # Select latest metadata first; do not copy candidate blobs for every historical revision.
    candidate_payloads = {}
    connection = _readonly_connection(database_path)
    try:
        ids = [int(r['id']) for r in latest.values()]
        for start in range(0, len(ids), 128):
            batch = ids[start:start+128]
            placeholders = ','.join('?' for _ in batch)
            candidate_payloads.update((int(r[0]),r[1]) for r in connection.execute(
                f'SELECT id,candidates_json FROM prediction_analysis_snapshots WHERE id IN ({placeholders})',batch))
    finally:
        connection.close()

    pending: list[PendingSnapshot] = []
    for row in latest.values():
        kickoff_at = str(row["match_time"] or "")
        analysis_version = str(row["analysis_version"] or "").strip()
        kickoff = _kickoff_datetime(kickoff_at)
        # The manager ledger must never create an answer after a fixture starts.
        if kickoff is None or kickoff <= now or analysis_version != ANALYSIS_VERSION:
            continue
        try:
            created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if created >= kickoff:
            continue
        candidates = _json(candidate_payloads.get(int(row['id'])), [])
        valid = tuple(candidate for candidate in candidates if isinstance(candidate, dict))
        if valid:
            pending.append(
                PendingSnapshot(
                    match_id=str(row["match_id"]),
                    snapshot_id=int(row["id"]),
                    created_at=str(row["created_at"] or ""),
                    kickoff_at=kickoff.isoformat(timespec="minutes"),
                    home=str(row["home_team"] or ""),
                    away=str(row["away_team"] or ""),
                    analysis_version=analysis_version,
                    candidates=valid,
                )
            )
    return sorted(pending, key=lambda item: (item.kickoff_at, item.match_id))


def _wilson_lower(hits: float, count: float, z: float = 1.28) -> float:
    """One-sided conservative confidence bound without outside dependencies."""
    if count <= 0:
        return 0.0
    rate = max(0.0, min(1.0, hits / count))
    denominator = 1.0 + z * z / count
    center = rate + z * z / (2.0 * count)
    spread = z * math.sqrt((rate * (1.0 - rate) + z * z / (4.0 * count)) / count)
    return max(0.0, min(1.0, (center - spread) / denominator))


def _calibrate_probability(
    probability: float, market: str, history: dict[str, Any]
) -> tuple[float, float, int]:
    market_stat: HistoricalStat = history["overall"].get(market, HistoricalStat())
    bucket_stat: HistoricalStat = history["buckets"].get((market, _bucket(probability)), HistoricalStat())
    combined_count = market_stat.count + bucket_stat.count
    combined_hits = market_stat.hits + bucket_stat.hits
    if bucket_stat.count:
        # Bucket observations are more relevant; market-wide rows stabilise a
        # small bucket rather than pretending a few hits are certainty.
        empirical = (bucket_stat.hits + 0.35 * market_stat.hits) / (
            bucket_stat.count + 0.35 * market_stat.count
        )
    else:
        empirical = market_stat.rate if market_stat.count else probability
    calibrated = (probability * HISTORY_PRIOR + empirical * combined_count) / (
        HISTORY_PRIOR + combined_count
    )
    lower = _wilson_lower(
        probability * HISTORY_PRIOR + combined_hits,
        HISTORY_PRIOR + combined_count,
    )
    return max(0.0, min(1.0, calibrated)), max(0.0, min(1.0, lower)), combined_count


def _interval_width(candidate: dict[str, Any]) -> float:
    interval = candidate.get("probability_interval")
    if not isinstance(interval, dict):
        return 0.0
    low = _probability(interval.get("low"))
    high = _probability(interval.get("high"))
    return max(0.0, high - low)


def _manager_candidate(
    snapshot: PendingSnapshot, candidate: dict[str, Any], history: dict[str, Any], frozen_at: str,
    rejections: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    def reject(reason):
        if rejections is not None:
            rejections[reason] = rejections.get(reason, 0) + 1
        return None
    market = str(candidate.get("market_key") or "").strip()
    raw_pick = str(candidate.get("raw_pick") or "").strip()
    probability = _probability(candidate.get("model_probability", candidate.get("prob")))
    odd = _number(candidate.get("odd"))
    data_confidence = _probability(candidate.get("data_confidence"), 0.5)
    if (
        not market
        or not raw_pick
        or not 0.0 < probability < 1.0
        or odd <= 1.0
        or odd > MAX_VALUE_ODDS
        or data_confidence < MIN_DATA_CONFIDENCE
        or candidate.get("settlement_supported") is False
    ):
        return reject("배당·확률·자료 신뢰도 확인 필요")

    calibrated, statistical_lower, calibration_samples = _calibrate_probability(
        probability, market, history
    )
    odds_history: HistoricalStat = history.get("odds_buckets", {}).get(
        _odds_bucket(odd), HistoricalStat()
    )
    if odd >= 5.0 and odds_history.count < MIN_HIGH_ODDS_HISTORY_ROWS:
        # A longshot cannot be promoted from its price alone.  Keep gathering
        # frozen samples first; this is a deliberate no-pick, not a fallback.
        return reject("고배당 구간 채점 표본 부족")
    uncertainty = _interval_width(candidate)
    confidence_factor = 0.85 + 0.15 * data_confidence
    conservative = min(probability, calibrated, statistical_lower) * confidence_factor
    if odd >= 5.0:
        conservative = min(
            conservative,
            _wilson_lower(odds_history.hits, odds_history.count),
        )
    conservative = max(0.0, conservative - uncertainty * 0.20)
    fair_probability = _probability(candidate.get("fair_probability"))
    market_reference = fair_probability if 0.0 < fair_probability < 1.0 else 1.0 / odd
    market_edge = conservative - market_reference
    conservative_ev = conservative * odd - 1.0
    if conservative_ev < MIN_CONSERVATIVE_EV or market_edge < MIN_MARKET_EDGE:
        return reject("보수 기대값·시장 대비 확률 기준 미충족")

    # A value-first score: price only matters through conservative EV.  The
    # small confidence term resolves otherwise equal candidates without
    # rewarding high odds by themselves.
    manager_score = (
        min(conservative_ev, 0.50) * 0.65
        + conservative * 0.25
        + data_confidence * 0.08
        + min(calibration_samples / 200.0, 1.0) * 0.02
    )
    tier = "고배당 가치" if odd >= HIGH_ODDS_BOUNDARY else "핵심 투자"
    return {
        "schema_version": OUTPUT_SCHEMA,
        "status": "PENDING",
        "engine_version": MANAGER_ENGINE_VERSION,
        "model_version":candidate.get("model_version"),
        "match_id": snapshot.match_id,
        "source_snapshot_id": snapshot.snapshot_id,
        "source_created_at": snapshot.created_at,
        "analysis_version": snapshot.analysis_version,
        "kickoff_at": snapshot.kickoff_at,
        "home": snapshot.home,
        "away": snapshot.away,
        "market_key": market,
        "selection_side": str(candidate.get("selection_side") or ""),
        "raw_pick": raw_pick,
        "tier": tier,
        "probability": round(probability, 6),
        "calibrated_probability": round(calibrated, 6),
        "conservative_probability": round(conservative, 6),
        "data_confidence": round(data_confidence, 6),
        "calibration_samples": calibration_samples,
        "odds_bucket_samples": odds_history.count,
        "odd": round(odd, 6),
        "market_reference_probability": round(market_reference, 6),
        "market_edge": round(market_edge, 6),
        "conservative_ev": round(conservative_ev, 6),
        "manager_score": round(manager_score, 6),
        "frozen_at": frozen_at,
        "reason": "시작 전 저장 후보의 보정 확률·보수 기대값·시장 대비 우위를 모두 통과한 관리자 전용 선택입니다.",
    }


def _select_portfolio(
    snapshots: list[PendingSnapshot], history: dict[str, Any], frozen_at: str,
    rejections: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    per_fixture: list[dict[str, Any]] = []
    for snapshot in snapshots:
        options = [
            row
            for candidate in snapshot.candidates
            for row in (_manager_candidate(snapshot, candidate, history, frozen_at, rejections),)
            if row is not None
        ]
        if options:
            per_fixture.append(
                max(
                    options,
                    key=lambda row: (
                        float(row["manager_score"]),
                        float(row["conservative_ev"]),
                        float(row["conservative_probability"]),
                        str(row["raw_pick"]),
                    ),
                )
            )

    selected: list[dict[str, Any]] = []
    core_count = value_count = 0
    for row in sorted(
        per_fixture,
        key=lambda value: (
            -float(value["manager_score"]),
            str(value["kickoff_at"]),
            str(value["match_id"]),
        ),
    ):
        if row["tier"] == "고배당 가치":
            if value_count >= MAX_VALUE_PICKS:
                continue
            value_count += 1
        else:
            if core_count >= MAX_CORE_PICKS:
                continue
            core_count += 1
        selected.append(row)
    return selected


def _grade_frozen_picks(
    database_path: str | Path, picks: dict[str, Any]
) -> tuple[int, int, list[dict[str, str]]]:
    """Grade only frozen manager rows.  The source database stays read-only."""
    connection = _readonly_connection(database_path)
    graded = total = 0
    unresolved: list[dict[str, str]] = []
    try:
        for ledger_key, pick in picks.items():
            if not isinstance(pick, dict):
                continue
            match_id = str(pick.get("match_id") or ledger_key)
            total += 1
            if pick.get("is_correct") in (0, 1):
                graded += 1
                continue
            row = connection.execute(
                """
                SELECT is_correct, actual_score, graded_at
                FROM prediction_candidate_results
                WHERE match_id = ?
                  AND analysis_snapshot_id = ?
                  AND market_key = ?
                  AND raw_pick = ?
                  AND is_correct IN (0, 1)
                  AND COALESCE(actual_score, '') NOT IN ('', '-:-', 'PENDING', 'UNKNOWN')
                ORDER BY id DESC LIMIT 1
                """,
                (
                    str(match_id),
                    int(pick.get("source_snapshot_id") or 0),
                    str(pick.get("market_key") or ""),
                    str(pick.get("raw_pick") or ""),
                ),
            ).fetchone()
            grade_source = "candidate_result_exact"
            if row is None:
                # Some early immutable manager rows have a completed canonical
                # match result but no candidate-result mirror.  The match ID
                # was frozen with the pick, so use only that exact finished
                # result and the shared settlement evaluator.  This fills a
                # previously blank grade; it never recalculates the pick,
                # probability, odds, or selection.
                row = connection.execute(
                    """
                    SELECT actual_score, created_at AS result_at
                    FROM predictions
                    WHERE match_id = ?
                      AND actual_result = 'FINISHED'
                      AND COALESCE(actual_score, '') NOT IN ('', '-:-', 'PENDING', 'UNKNOWN')
                    ORDER BY rowid DESC LIMIT 1
                    """,
                    (str(match_id),),
                ).fetchone()
                grade_source = "canonical_finished_result_fallback"
            if row is None:
                unresolved.append({
                    "match_id": str(match_id),
                    "reason": "결과 API 또는 후보 결과 연결 대기",
                })
                continue
            if "is_correct" in row.keys():
                correct = int(row["is_correct"])
            else:
                score = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", str(row["actual_score"] or ""))
                if score is None:
                    unresolved.append({
                        "match_id": str(match_id),
                        "reason": "최종 점수 형식 확인 필요",
                    })
                    continue
                correct = int(_evaluate_single_pick(
                    str(pick.get("raw_pick") or ""),
                    str(pick.get("home") or ""),
                    str(pick.get("away") or ""),
                    int(score.group(1)), int(score.group(2)),
                ))
            odd = _number(pick.get("odd"))
            pick.update(
                {
                    "status": "FINISHED",
                    "is_correct": correct,
                    "actual_score": str(row["actual_score"] or ""),
                    "graded_at": str(
                        row["graded_at"]
                        if "graded_at" in row.keys() and row["graded_at"]
                        else (row["result_at"] if "result_at" in row.keys() and row["result_at"] else _now())
                    ),
                    # This is a comparison-only one-unit record.  It does not
                    # represent a user's cash stake or execute any bet.
                    "unit_profit": round((odd - 1.0) if correct else -1.0, 6),
                    "grade_source": grade_source,
                }
            )
            graded += 1
    finally:
        connection.close()
    return graded, total, unresolved


def _performance(picks: dict[str, Any]) -> dict[str, Any]:
    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        graded_rows = [row for row in rows if row.get("is_correct") in (0, 1)]
        stake_count = len(graded_rows)
        profit = sum(_number(row.get("unit_profit")) for row in graded_rows)
        hits = sum(int(row.get("is_correct") or 0) for row in graded_rows)
        return {
            "frozen_count": len(rows),
            "graded_count": stake_count,
            "hit_count": hits,
            "hit_rate": round(hits / stake_count, 6) if stake_count else None,
            "unit_profit": round(profit, 6),
            "unit_roi": round(profit / stake_count, 6) if stake_count else None,
        }

    rows = [row for row in picks.values() if isinstance(row, dict)]
    return {
        "overall": summarize(rows),
        "core": summarize([row for row in rows if row.get("tier") == "핵심 투자"]),
        "value": summarize([row for row in rows if row.get("tier") == "고배당 가치"]),
    }


def build_manager_payload(
    database_path: str | Path, existing_payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build the independent ledger while preserving every existing frozen row."""
    generated_at = _now()
    existing_payload = existing_payload if isinstance(existing_payload, dict) else {}
    picks = {
        str(match_id): dict(pick)
        for match_id, pick in (existing_payload.get("picks") or {}).items()
        if isinstance(pick, dict)
    }
    base: dict[str, Any] = {
        "schema_version": OUTPUT_SCHEMA,
        "engine_version": MANAGER_ENGINE_VERSION,
        "generated_at": generated_at,
        "mode": "administrator_only_independent_investment_ledger",
        "customer_official_pick_changed": False,
        "robot_pick_changed": False,
        "v2_alphago_pick_changed": False,
        "v3_learning_pick_changed": False,
        "picks": picks,
        "policy": {
            "source": "frozen_pre_kickoff_candidates_only",
            "one_pick_per_fixture": True,
            "forced_fill": False,
            "max_core_picks": MAX_CORE_PICKS,
            "max_value_picks": MAX_VALUE_PICKS,
            "min_conservative_ev": MIN_CONSERVATIVE_EV,
            "min_market_edge": MIN_MARKET_EDGE,
            "min_data_confidence": MIN_DATA_CONFIDENCE,
            "max_value_odds": MAX_VALUE_ODDS,
            "min_high_odds_history_rows": MIN_HIGH_ODDS_HISTORY_ROWS,
            "cash_staking_or_auto_betting": False,
        },
    }
    try:
        history = _load_history(database_path)
        snapshots = _load_pending_snapshots(database_path)
        rejections: dict[str, int] = {}
        from analyst_products import investment_candidates, LABELS
        dashboard = _read_json(Path(database_path).with_name("dashboard_data.json"), {})
        cards = {str((c.get("match") or {}).get("id")):c for c in dashboard.get("proto", [])}
        v3_payload = _read_json(Path(database_path).with_name("v3_learning_picks.json"), {})
        v3 = v3_payload.get("investment_candidates") or v3_payload.get("picks") or {}
        from learning_state import CAMPAIGN, archive_json_row, before_kickoff, digest
        base['pick_revision_history'] = dict(existing_payload.get('pick_revision_history') or {})
        base['reanalysis_receipts'] = dict(existing_payload.get('reanalysis_receipts') or {})
        created, engines = 0, {}
        for engine in LABELS:
            engine_snapshots, reasons = [], {}
            for snapshot in snapshots:
                card = cards.get(snapshot.match_id)
                if card and ((card.get('match') or {}).get('home'), (card.get('match') or {}).get('away')) != (snapshot.home, snapshot.away):
                    reasons['화면 경기와 저장 분석의 팀 연결 확인 필요'] = reasons.get('화면 경기와 저장 분석의 팀 연결 확인 필요', 0) + 1
                    continue
                candidates = investment_candidates(card, engine, v3.get(snapshot.match_id)) if card else (
                    list(snapshot.candidates) if engine == "official" else [])
                if candidates:
                    engine_snapshots.append(replace(snapshot, candidates=tuple(candidates)))
                else:
                    reason = {'official':'공식 분석 후보 수신 대기', 'robot':'자율 로봇의 독립 후보 수신 대기',
                              'v2':'V2 실배당·모델 확률 수신 대기', 'v3':'V3 독립 후보 생성 대기'}[engine]
                    reasons[reason] = reasons.get(reason, 0) + 1
            selected = _select_portfolio(engine_snapshots, history, generated_at, reasons)
            engine_created = 0
            # Only reviewed, freshly reanalysed scheduled cards authorize one replacement.
            for snapshot in engine_snapshots:
                card = cards.get(snapshot.match_id) or {}
                key = engine + ':' + snapshot.match_id
                revision = CAMPAIGN + ':' + digest([(card.get('learning_models') or {}).get(engine,'legacy-unverified'),
                    sorted({str(x.get('model_version') or '') for x in snapshot.candidates})])
                if (card.get('learning_campaign') == CAMPAIGN
                        and base['reanalysis_receipts'].get(key) != revision
                        and before_kickoff({'match_time':snapshot.kickoff_at})):
                    old_pick = picks.get(key)
                    if old_pick and old_pick.get('is_correct') not in (0,1) and old_pick.get('status') != 'FINISHED':
                        archive_json_row(base,key,old_pick)
                        del picks[key]
                    base['reanalysis_receipts'][key] = revision
            for pick in selected:
                card = cards.get(str(pick['match_id'])) or {}
                pick.update(learning_campaign=card.get('learning_campaign',''),
                            model_version=pick.get('model_version') or (card.get('learning_models') or {}).get(engine,'legacy-unverified'))
                if not before_kickoff({'match_time':pick.get('kickoff_at')}):
                    continue
                key = engine + ':' + str(pick['match_id'])
                if key not in picks:
                    picks[key] = {**pick, 'engine_key':engine, 'analyst_label':LABELS[engine],
                                  'product':'manager_investment', 'selection_axis':'own_model_value'}
                    created += 1
                    engine_created += 1
            engines[engine] = dict(input_snapshot_count=len(engine_snapshots),
                input_candidate_count=sum(len(x.candidates) for x in engine_snapshots),
                candidate_rejections=reasons, newly_frozen_picks=engine_created)
        base.update(input_snapshot_count=len(snapshots),
                    input_candidate_count=sum(len(row.candidates) for row in snapshots),
                    engines=engines)
        graded, total, unresolved = _grade_frozen_picks(database_path, picks)
        performance = _performance(picks)
        for engine, info in engines.items():
            own = {k:v for k,v in picks.items() if v.get('engine_key') == engine}
            info['performance'] = _performance(own)
            info['frozen_pick_count'] = len(own)
        base.update(
            {
                "status": "READY",
                "history_rows": int(history["history_rows"]),
                "newly_frozen_picks": created,
                "frozen_pick_count": total,
                "graded_pick_count": graded,
                "performance": performance,
                "settlement_pending": unresolved,
            }
        )
    except (ManagerNotReady, sqlite3.Error, OSError, ValueError) as error:
        graded, total, unresolved = _grade_frozen_picks(database_path, picks)
        base.update(
            {
                "status": "NOT_READY",
                "reason": str(error),
                "newly_frozen_picks": 0,
                "frozen_pick_count": total,
                "graded_pick_count": graded,
                "settlement_pending": unresolved,
                "performance": _performance(picks),
            }
        )
    return base


def refresh_manager_ledger(database_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    # The old six-hour timer may still run. Serialize its read/merge/write
    # with the analysis-triggered refresh so neither can lose frozen records.
    with output.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        previous = _read_json(output, {})
        payload = build_manager_payload(database_path, previous)
        now = datetime.now(KST)
        payload['current_pending_count'] = sum(
            1 for row in (payload.get('picks') or {}).values()
            if row.get('analysis_version') == ANALYSIS_VERSION
            and row.get('status') != 'FINISHED'
            and (_kickoff_datetime(row.get('kickoff_at')) or now) > now
        )
        from learning_state import guard_ledger_revisions
        guard_ledger_revisions(database_path,payload,previous)
        _write_json_atomically(output, payload)
        return payload


def _main() -> int:
    parser = argparse.ArgumentParser(description="Build the independent administrator investment ledger")
    parser.add_argument("--db", required=True, help="existing ai_predictions.db; opened read-only")
    parser.add_argument("--output", required=True, help="manager investment JSON written by this process")
    args = parser.parse_args()
    payload = refresh_manager_ledger(args.db, args.output)
    print(
        json.dumps(
            {
                "status": payload.get("status"),
                "newly_frozen_picks": payload.get("newly_frozen_picks"),
                "frozen_pick_count": payload.get("frozen_pick_count"),
                "graded_pick_count": payload.get("graded_pick_count"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
