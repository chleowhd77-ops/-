"""Independent, administrator-only investment-pick ledger.

This worker deliberately does not alter customer official picks, robot picks,
V2 Alphago picks, V3 learning picks, Toto14 marks, or any SQLite table.  It
reads frozen pre-kickoff candidates and completed grades, freezes one separate
manager candidate per eligible future fixture in its own JSON ledger, and
grades only that frozen selection after the result is available.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


MANAGER_ENGINE_VERSION = "manager-investment-independent-v1"
OUTPUT_SCHEMA = "dj-sports.manager-investment-ledger.v1"
KST = timezone(timedelta(hours=9))

# These are safety filters, not targets to fill.  A candidate must clear every
# applicable gate; an empty day is an intended and valid result.
MIN_HISTORY_ROWS = 30
MIN_DATA_CONFIDENCE = 0.35
MIN_CONSERVATIVE_EV = 0.015
MIN_MARKET_EDGE = 0.01
HIGH_ODDS_BOUNDARY = 2.20
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
                   snapshot.candidates_json, result.raw_pick, result.fair_probability,
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
    finally:
        connection.close()

    overall: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    buckets: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        candidates = _json(row["candidates_json"], [])
        candidate = _candidate_from_snapshot(candidates, row)
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

    history_rows = sum(value[0] for value in overall.values())
    if history_rows < MIN_HISTORY_ROWS:
        raise ManagerNotReady(
            f"completed frozen candidate history is below {MIN_HISTORY_ROWS}: {history_rows}"
        )
    return {
        "overall": {key: HistoricalStat(value[0], value[1]) for key, value in overall.items()},
        "buckets": {key: HistoricalStat(value[0], value[1]) for key, value in buckets.items()},
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
            SELECT s.id, s.match_id, s.stage, s.created_at, s.candidates_json,
                   p.match_time, p.home_team, p.away_team
            FROM prediction_analysis_snapshots AS s
            JOIN predictions AS p ON p.match_id = s.match_id
            WHERE COALESCE(p.actual_result, 'PENDING') = 'PENDING'
              AND COALESCE(p.is_toto14, 0) = 0
              AND s.stage LIKE 'T-%'
            ORDER BY s.match_id ASC, s.id DESC
            """
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
    pending: list[PendingSnapshot] = []
    for row in latest.values():
        kickoff_at = str(row["match_time"] or "")
        kickoff = _kickoff_datetime(kickoff_at)
        # The manager ledger must never create an answer after a fixture starts.
        if kickoff is None or kickoff <= now:
            continue
        candidates = _json(row["candidates_json"], [])
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
    snapshot: PendingSnapshot, candidate: dict[str, Any], history: dict[str, Any], frozen_at: str
) -> dict[str, Any] | None:
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
        or data_confidence < MIN_DATA_CONFIDENCE
        or candidate.get("settlement_supported") is False
    ):
        return None

    calibrated, statistical_lower, calibration_samples = _calibrate_probability(
        probability, market, history
    )
    uncertainty = _interval_width(candidate)
    confidence_factor = 0.85 + 0.15 * data_confidence
    conservative = min(probability, calibrated, statistical_lower) * confidence_factor
    conservative = max(0.0, conservative - uncertainty * 0.20)
    fair_probability = _probability(candidate.get("fair_probability"))
    market_reference = fair_probability if 0.0 < fair_probability < 1.0 else 1.0 / odd
    market_edge = conservative - market_reference
    conservative_ev = conservative * odd - 1.0
    if conservative_ev < MIN_CONSERVATIVE_EV or market_edge < MIN_MARKET_EDGE:
        return None

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
        "match_id": snapshot.match_id,
        "source_snapshot_id": snapshot.snapshot_id,
        "source_created_at": snapshot.created_at,
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
        "odd": round(odd, 6),
        "market_reference_probability": round(market_reference, 6),
        "market_edge": round(market_edge, 6),
        "conservative_ev": round(conservative_ev, 6),
        "manager_score": round(manager_score, 6),
        "frozen_at": frozen_at,
        "reason": "시작 전 저장 후보의 보정 확률·보수 기대값·시장 대비 우위를 모두 통과한 관리자 전용 선택입니다.",
    }


def _select_portfolio(
    snapshots: list[PendingSnapshot], history: dict[str, Any], frozen_at: str
) -> list[dict[str, Any]]:
    per_fixture: list[dict[str, Any]] = []
    for snapshot in snapshots:
        options = [
            row
            for candidate in snapshot.candidates
            for row in (_manager_candidate(snapshot, candidate, history, frozen_at),)
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
) -> tuple[int, int]:
    """Grade only frozen manager rows.  The source database stays read-only."""
    connection = _readonly_connection(database_path)
    graded = total = 0
    try:
        for match_id, pick in picks.items():
            if not isinstance(pick, dict):
                continue
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
            if row is None:
                continue
            correct = int(row["is_correct"])
            odd = _number(pick.get("odd"))
            pick.update(
                {
                    "status": "FINISHED",
                    "is_correct": correct,
                    "actual_score": str(row["actual_score"] or ""),
                    "graded_at": str(row["graded_at"] or _now()),
                    # This is a comparison-only one-unit record.  It does not
                    # represent a user's cash stake or execute any bet.
                    "unit_profit": round((odd - 1.0) if correct else -1.0, 6),
                }
            )
            graded += 1
    finally:
        connection.close()
    return graded, total


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
            "cash_staking_or_auto_betting": False,
        },
    }
    try:
        history = _load_history(database_path)
        selected = _select_portfolio(_load_pending_snapshots(database_path), history, generated_at)
        created = 0
        for pick in selected:
            match_id = str(pick["match_id"])
            # First public manager answer is append-only in this separate ledger.
            if match_id not in picks:
                picks[match_id] = pick
                created += 1
        graded, total = _grade_frozen_picks(database_path, picks)
        performance = _performance(picks)
        base.update(
            {
                "status": "READY",
                "history_rows": int(history["history_rows"]),
                "newly_frozen_picks": created,
                "frozen_pick_count": total,
                "graded_pick_count": graded,
                "performance": performance,
            }
        )
    except (ManagerNotReady, sqlite3.Error, OSError, ValueError) as error:
        graded, total = _grade_frozen_picks(database_path, picks)
        base.update(
            {
                "status": "NOT_READY",
                "reason": str(error),
                "newly_frozen_picks": 0,
                "frozen_pick_count": total,
                "graded_pick_count": graded,
                "performance": _performance(picks),
            }
        )
    return base


def refresh_manager_ledger(database_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    output = Path(output_path)
    payload = build_manager_payload(database_path, _read_json(output, {}))
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
