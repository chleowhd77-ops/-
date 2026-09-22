"""Offline-only challenger for the customer-facing official pick.

The existing official pick, autonomous robot, and V2 pick are deliberately not
imported or changed here.  This program reads frozen, pre-match candidate
snapshots and completed grades, runs a chronological train/tune/final exam,
and writes a report.  It never writes to SQLite or calls a network service.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable


OFFICIAL_META_VERSION = "official-meta-v3-challenger-pre-match-v1"
FEATURE_SCHEMA_VERSION = "official-meta-v3.frozen-candidate.v1"
MIN_TRAIN_MATCHES = 240
MIN_TUNE_MATCHES = 80
MIN_FINAL_MATCHES = 100
TARGET_ACCURACY = 0.70

# This whitelist is intentional.  Result, score, postmortem, grade time, and
# text written after kickoff must never become inputs to the model.
NUMERIC_FEATURES = (
    "model_probability",
    "raw_model_probability",
    "robust_probability",
    "fair_probability",
    "odd",
    "edge",
    "robust_edge",
    "robust_ev",
    "data_confidence",
    "error_margin",
    "learning_weight",
    "market_history_samples",
    "market_hit_rate",
    "independent_support_count",
    "balanced_score",
    "safe_score",
    "recommendation_score",
)
BOOLEAN_FEATURES = (
    "is_qualified_underdog",
    "is_true_underdog",
    "settlement_supported",
)
CATEGORICAL_FEATURES = ("market_key", "selection_side")
PREMATCH_STAGES = ("T-24-initial", "T-90", "T-60", "T-60-lineup", "T-30-final", "T-3-refresh")


class DataReadinessError(RuntimeError):
    """Raised when the staged V3 exam must fail closed."""


def _safe_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _number(value: Any, default: float = 0.0) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return default
    return converted if math.isfinite(converted) else default


def _clamp(value: float, low: float = -10.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def _stage_priority(stage: Any) -> int:
    value = str(stage or "")
    try:
        return PREMATCH_STAGES.index(value)
    except ValueError:
        return -1


def _candidate_from_snapshot(candidates: list[dict[str, Any]], row: sqlite3.Row) -> dict[str, Any]:
    """Return the frozen candidate corresponding to one completed grade."""
    market = str(row["market_key"] or "")
    raw_pick = str(row["raw_pick"] or "")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("market_key") or "") == market and str(candidate.get("raw_pick") or "") == raw_pick:
            return candidate
    # Older snapshots did not keep every rich candidate property.  The values
    # below are still frozen pre-match columns, so using them is safe; missing
    # rich inputs remain explicit zeros rather than invented data.
    return {
        "market_key": market,
        "raw_pick": raw_pick,
        "model_probability": row["model_probability"],
        "fair_probability": row["fair_probability"],
        "odd": row["odd"],
    }


def _feature_dict(candidate: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key in NUMERIC_FEATURES:
        result[key] = _clamp(_number(candidate.get(key)))

    interval = candidate.get("probability_interval")
    interval = interval if isinstance(interval, dict) else {}
    low = _clamp(_number(interval.get("low")), 0.0, 1.0)
    high = _clamp(_number(interval.get("high")), 0.0, 1.0)
    result["probability_interval_low"] = low
    result["probability_interval_high"] = high
    result["probability_interval_width"] = max(0.0, high - low)

    for key in BOOLEAN_FEATURES:
        result[key] = 1.0 if bool(candidate.get(key)) else 0.0
    for key in CATEGORICAL_FEATURES:
        value = str(candidate.get(key) or "unknown").strip().lower()[:48]
        result[f"{key}={value or 'unknown'}"] = 1.0
    return result


def _selected_baseline_key(decision: dict[str, Any]) -> tuple[str, str] | None:
    market = str(decision.get("selected_market") or "")
    pick = str(decision.get("selected_pick") or "")
    return (market, pick) if market and pick else None


@dataclass(frozen=True)
class CandidateExample:
    match_id: str
    snapshot_id: int
    created_at: str
    stage: str
    market_key: str
    raw_pick: str
    label: int
    baseline_selected: bool
    baseline_fallback: bool
    features: dict[str, float]


def load_frozen_examples(database_path: str | Path) -> tuple[list[CandidateExample], dict[str, Any]]:
    """Read a single, latest pre-kickoff snapshot per completed match.

    The database is opened immutable/read-only.  Only grades that have an
    explicit completed score are accepted.  No ``regular`` or after-kickoff
    analysis is silently mixed into the exam.
    """
    path = Path(database_path)
    if not path.exists():
        raise DataReadinessError(f"database not found: {path}")
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"prediction_candidate_results", "prediction_analysis_snapshots"}
        missing = sorted(required - tables)
        if missing:
            raise DataReadinessError(f"required tables missing: {', '.join(missing)}")

        rows = connection.execute(
            """
            SELECT
                result.match_id, result.analysis_snapshot_id, result.stage AS result_stage,
                result.market_key, result.raw_pick, result.model_probability,
                result.fair_probability, result.odd, result.is_correct,
                result.actual_score, result.graded_at,
                snapshot.stage AS snapshot_stage, snapshot.created_at,
                snapshot.candidates_json, snapshot.decision_json
            FROM prediction_candidate_results AS result
            JOIN prediction_analysis_snapshots AS snapshot
              ON snapshot.id = result.analysis_snapshot_id
            WHERE result.is_correct IN (0, 1)
              AND COALESCE(result.actual_score, '') NOT IN ('', '-:-', 'PENDING', 'UNKNOWN')
              AND snapshot.stage LIKE 'T-%'
            ORDER BY snapshot.created_at ASC, result.analysis_snapshot_id ASC, result.id ASC
            """
        ).fetchall()
    finally:
        connection.close()

    snapshots: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        snapshots[(str(row["match_id"]), int(row["analysis_snapshot_id"]))].append(row)

    # A match can be analysed at T-90 and again at T-30.  Use the latest frozen
    # pre-match snapshot only; choosing between both would count one outcome
    # twice and leak a later refresh into an earlier candidate.
    best_snapshot_by_match: dict[str, tuple[str, int]] = {}
    for key, grouped_rows in snapshots.items():
        match_id, _snapshot_id = key
        representative = grouped_rows[0]
        candidate_order = (_stage_priority(representative["snapshot_stage"]), str(representative["created_at"]), key[1])
        existing_key = best_snapshot_by_match.get(match_id)
        if existing_key is None:
            best_snapshot_by_match[match_id] = key
            continue
        existing = snapshots[existing_key][0]
        existing_order = (_stage_priority(existing["snapshot_stage"]), str(existing["created_at"]), existing_key[1])
        if candidate_order > existing_order:
            best_snapshot_by_match[match_id] = key

    examples: list[CandidateExample] = []
    fallback_baseline_count = 0
    for match_id, snapshot_key in sorted(best_snapshot_by_match.items(), key=lambda item: str(snapshots[item[1]][0]["created_at"])):
        grouped_rows = snapshots[snapshot_key]
        representative = grouped_rows[0]
        candidates = _safe_json(representative["candidates_json"], [])
        decision = _safe_json(representative["decision_json"], {})
        baseline_key = _selected_baseline_key(decision)
        fallback_key: tuple[str, str] | None = None
        if baseline_key is None:
            # This fallback is reported.  It makes the baseline conservative
            # rather than pretending an old snapshot had a known official pick.
            fallback_row = max(grouped_rows, key=lambda row: _number(row["model_probability"]))
            fallback_key = (str(fallback_row["market_key"]), str(fallback_row["raw_pick"]))
            fallback_baseline_count += 1

        for row in grouped_rows:
            candidate = _candidate_from_snapshot(candidates, row)
            candidate_key = (str(row["market_key"]), str(row["raw_pick"]))
            examples.append(
                CandidateExample(
                    match_id=match_id,
                    snapshot_id=int(row["analysis_snapshot_id"]),
                    created_at=str(row["created_at"]),
                    stage=str(row["snapshot_stage"]),
                    market_key=candidate_key[0],
                    raw_pick=candidate_key[1],
                    label=int(row["is_correct"]),
                    baseline_selected=candidate_key == (baseline_key or fallback_key),
                    baseline_fallback=baseline_key is None,
                    features=_feature_dict(candidate),
                )
            )

    metadata = {
        "source_database": str(path),
        "completed_candidate_rows": len(rows),
        "usable_matches": len(best_snapshot_by_match),
        "usable_candidates": len(examples),
        "baseline_fallback_matches": fallback_baseline_count,
        "selected_stages": sorted({example.stage for example in examples}),
    }
    return examples, metadata


def audit_textbook(textbook_path: str | Path | None) -> dict[str, Any]:
    """Describe, but never train from, a user-provided V2 textbook CSV."""
    if not textbook_path:
        return {"present": False, "reason": "no textbook path supplied"}
    path = Path(textbook_path)
    if not path.exists():
        return {"present": False, "reason": "textbook file not found", "path": str(path)}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        row_count = 0
        completed_count = 0
        dates: list[str] = []
        for row in reader:
            row_count += 1
            if str(row.get("FTR") or "").strip().upper() in {"H", "D", "A"}:
                completed_count += 1
            for date_column in ("Date", "date", "match_date"):
                value = str(row.get(date_column) or "").strip()
                if value:
                    dates.append(value)
                    break
    required_v2 = {"B365H", "B365D", "B365A", "FTR"}
    high_dimensional = set(NUMERIC_FEATURES) | {"lineup", "injury", "context", "uncertainty", "market_consensus"}
    header_set = set(headers)
    return {
        "present": True,
        "path": str(path),
        "row_count": row_count,
        "completed_rows": completed_count,
        "columns": headers,
        "v2_odds_result_schema": required_v2.issubset(header_set),
        "high_dimensional_official_columns": sorted(high_dimensional & header_set),
        "has_date_column": bool(dates),
        "first_seen_date": min(dates) if dates else None,
        "last_seen_date": max(dates) if dates else None,
    }


def _group_examples(examples: Iterable[CandidateExample]) -> list[list[CandidateExample]]:
    grouped: dict[str, list[CandidateExample]] = defaultdict(list)
    for example in examples:
        grouped[example.match_id].append(example)
    return [grouped[match_id] for match_id in sorted(grouped, key=lambda key: (grouped[key][0].created_at, key))]


def chronological_split(
    examples: Iterable[CandidateExample],
    train_ratio: float = 0.60,
    tune_ratio: float = 0.20,
) -> tuple[list[list[CandidateExample]], list[list[CandidateExample]], list[list[CandidateExample]]]:
    groups = _group_examples(examples)
    if len(groups) < 3:
        raise DataReadinessError("fewer than three completed match groups")
    train_end = max(1, int(len(groups) * train_ratio))
    tune_end = max(train_end + 1, int(len(groups) * (train_ratio + tune_ratio)))
    tune_end = min(tune_end, len(groups) - 1)
    train, tune, final = groups[:train_end], groups[train_end:tune_end], groups[tune_end:]
    if not train or not tune or not final:
        raise DataReadinessError("chronological split produced an empty segment")
    return train, tune, final


def _sigmoid(value: float) -> float:
    value = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-value))


class FrozenFeatureEncoder:
    """Numeric/categorical encoder fitted only on the training segment."""

    def __init__(self) -> None:
        self.columns: list[str] = []

    def fit(self, rows: Iterable[CandidateExample]) -> "FrozenFeatureEncoder":
        keys = set(NUMERIC_FEATURES) | set(BOOLEAN_FEATURES) | {
            "probability_interval_low", "probability_interval_high", "probability_interval_width"
        }
        for row in rows:
            keys.update(key for key in row.features if "=" in key)
        self.columns = sorted(keys)
        return self

    def transform_one(self, row: CandidateExample) -> list[float]:
        return [_number(row.features.get(column)) for column in self.columns]

    def transform(self, rows: Iterable[CandidateExample]) -> list[list[float]]:
        return [self.transform_one(row) for row in rows]


class PreMatchBoostedStumps:
    """Small deterministic gradient-boosted tree fallback without packages."""

    def __init__(self, iterations: int = 64, learning_rate: float = 0.08, min_leaf: int = 20) -> None:
        self.iterations = iterations
        self.learning_rate = learning_rate
        self.min_leaf = min_leaf
        self.base_logit = 0.0
        self.stumps: list[tuple[int, float, float, float]] = []

    def fit(self, matrix: list[list[float]], labels: list[int]) -> "PreMatchBoostedStumps":
        if not matrix or not labels or len(matrix) != len(labels):
            raise DataReadinessError("empty or malformed training matrix")
        average = max(0.001, min(0.999, fmean(labels)))
        self.base_logit = math.log(average / (1.0 - average))
        scores = [self.base_logit] * len(labels)
        column_count = len(matrix[0])
        for _ in range(self.iterations):
            residuals = [label - _sigmoid(score) for label, score in zip(labels, scores)]
            best: tuple[float, int, float, float, float] | None = None
            for column in range(column_count):
                values = sorted({row[column] for row in matrix})
                if len(values) < 2:
                    continue
                quantiles = sorted({values[int((len(values) - 1) * fraction)] for fraction in (.10, .20, .30, .40, .50, .60, .70, .80, .90)})
                for threshold in quantiles:
                    left = [index for index, row in enumerate(matrix) if row[column] <= threshold]
                    right = [index for index, row in enumerate(matrix) if row[column] > threshold]
                    if len(left) < self.min_leaf or len(right) < self.min_leaf:
                        continue
                    left_value = fmean(residuals[index] for index in left)
                    right_value = fmean(residuals[index] for index in right)
                    gain = (len(left) * left_value * left_value) + (len(right) * right_value * right_value)
                    candidate = (gain, column, threshold, left_value, right_value)
                    if best is None or candidate[0] > best[0]:
                        best = candidate
            if best is None or best[0] <= 1e-12:
                break
            _gain, column, threshold, left_value, right_value = best
            self.stumps.append((column, threshold, left_value, right_value))
            for index, row in enumerate(matrix):
                scores[index] += self.learning_rate * (left_value if row[column] <= threshold else right_value)
        return self

    def predict_proba(self, matrix: list[list[float]]) -> list[float]:
        result: list[float] = []
        for row in matrix:
            score = self.base_logit
            for column, threshold, left_value, right_value in self.stumps:
                score += self.learning_rate * (left_value if row[column] <= threshold else right_value)
            result.append(_sigmoid(score))
        return result


class ChallengerModel:
    """Use sklearn tree boosting when present; otherwise use safe local stumps."""

    def __init__(self, *, iterations: int, learning_rate: float, min_leaf: int) -> None:
        self.iterations = iterations
        self.learning_rate = learning_rate
        self.min_leaf = min_leaf
        self.backend = "pre_match_boosted_stumps"
        self.model: Any = None

    def fit(self, matrix: list[list[float]], labels: list[int]) -> "ChallengerModel":
        try:
            from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore
        except ImportError:
            self.model = PreMatchBoostedStumps(self.iterations, self.learning_rate, self.min_leaf).fit(matrix, labels)
            return self
        self.backend = "sklearn_hist_gradient_boosting"
        self.model = HistGradientBoostingClassifier(
            max_iter=self.iterations,
            learning_rate=self.learning_rate,
            min_samples_leaf=self.min_leaf,
            l2_regularization=1.0,
            random_state=20260923,
        ).fit(matrix, labels)
        return self

    def predict_proba(self, matrix: list[list[float]]) -> list[float]:
        if self.model is None:
            raise DataReadinessError("model was not fitted")
        if self.backend == "sklearn_hist_gradient_boosting":
            return [float(value) for value in self.model.predict_proba(matrix)[:, 1]]
        return self.model.predict_proba(matrix)


def _flatten(groups: Iterable[list[CandidateExample]]) -> list[CandidateExample]:
    return [row for group in groups for row in group]


def _evaluate(model: ChallengerModel, encoder: FrozenFeatureEncoder, groups: list[list[CandidateExample]]) -> dict[str, Any]:
    rows = _flatten(groups)
    probabilities = model.predict_proba(encoder.transform(rows))
    predicted = {id(row): probability for row, probability in zip(rows, probabilities)}
    meta_hits: list[int] = []
    baseline_hits: list[int] = []
    meta_brier: list[float] = []
    baseline_brier: list[float] = []
    fallback_matches = 0
    for group in groups:
        selected = max(group, key=lambda row: (predicted[id(row)], row.market_key, row.raw_pick))
        baseline = next((row for row in group if row.baseline_selected), None)
        if baseline is None:
            raise DataReadinessError("baseline candidate missing from a completed match")
        fallback_matches += int(baseline.baseline_fallback)
        meta_hits.append(selected.label)
        baseline_hits.append(baseline.label)
        meta_brier.append((predicted[id(selected)] - selected.label) ** 2)
        baseline_probability = _number(baseline.features.get("robust_probability"), _number(baseline.features.get("model_probability")))
        baseline_brier.append((baseline_probability - baseline.label) ** 2)
    return {
        "matches": len(groups),
        "meta_accuracy": round(fmean(meta_hits), 6),
        "baseline_accuracy": round(fmean(baseline_hits), 6),
        "meta_brier": round(fmean(meta_brier), 6),
        "baseline_brier": round(fmean(baseline_brier), 6),
        "baseline_fallback_matches": fallback_matches,
    }


def run_chronological_exam(
    examples: list[CandidateExample],
    *,
    min_train_matches: int = MIN_TRAIN_MATCHES,
    min_tune_matches: int = MIN_TUNE_MATCHES,
    min_final_matches: int = MIN_FINAL_MATCHES,
    target_accuracy: float = TARGET_ACCURACY,
) -> dict[str, Any]:
    train_groups, tune_groups, final_groups = chronological_split(examples)
    segment_sizes = {"train_matches": len(train_groups), "tune_matches": len(tune_groups), "final_matches": len(final_groups)}
    minimums = {"train_matches": min_train_matches, "tune_matches": min_tune_matches, "final_matches": min_final_matches}
    insufficient = {key: {"actual": segment_sizes[key], "required": value} for key, value in minimums.items() if segment_sizes[key] < value}
    if insufficient:
        return {
            "status": "DATA_INSUFFICIENT",
            "reason": "minimum chronological match counts were not met",
            "segments": segment_sizes,
            "minimums": minimums,
            "shortfalls": insufficient,
            "exam_untouched": True,
        }

    train_rows = _flatten(train_groups)
    tune_rows = _flatten(tune_groups)
    configs = (
        {"iterations": 48, "learning_rate": 0.06, "min_leaf": 20},
        {"iterations": 72, "learning_rate": 0.05, "min_leaf": 24},
        {"iterations": 96, "learning_rate": 0.04, "min_leaf": 32},
    )
    encoder = FrozenFeatureEncoder().fit(train_rows)
    tuned: list[tuple[tuple[float, float], dict[str, Any], dict[str, Any]]] = []
    for config in configs:
        model = ChallengerModel(**config).fit(encoder.transform(train_rows), [row.label for row in train_rows])
        score = _evaluate(model, encoder, tune_groups)
        # Accuracy is primary, probability error breaks ties.  The final block
        # is never inspected in this loop.
        tuned.append(((score["meta_accuracy"], -score["meta_brier"]), config, score))
    _rank, selected_config, tuning = max(tuned, key=lambda item: item[0])

    train_plus_tune = train_rows + tune_rows
    final_encoder = FrozenFeatureEncoder().fit(train_plus_tune)
    final_model = ChallengerModel(**selected_config).fit(
        final_encoder.transform(train_plus_tune), [row.label for row in train_plus_tune]
    )
    final = _evaluate(final_model, final_encoder, final_groups)
    passed = bool(
        final["meta_accuracy"] >= target_accuracy
        and final["meta_accuracy"] > final["baseline_accuracy"]
        and final["meta_brier"] <= final["baseline_brier"]
        and final["baseline_fallback_matches"] == 0
    )
    return {
        "status": "EXAM_COMPLETE",
        "segments": segment_sizes,
        "minimums": minimums,
        "target_accuracy": target_accuracy,
        "feature_schema": FEATURE_SCHEMA_VERSION,
        "learner_backend": final_model.backend,
        "candidate_configs": [{"config": config, "tuning": score} for _rank, config, score in tuned],
        "selected_config": selected_config,
        "tuning": tuning,
        "final": final,
        "qualification": "PASS" if passed else "FAIL",
        "promotion": "SHADOW_ONLY" if passed else "KEEP_CURRENT_OFFICIAL_PICK",
        "exam_untouched": False,
    }


def build_report(database_path: str | Path, textbook_path: str | Path | None = None, **exam_options: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "version": OFFICIAL_META_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline_read_only_challenger",
        "database_write": False,
        "network_calls": False,
        "textbook_audit": audit_textbook(textbook_path),
    }
    try:
        examples, source = load_frozen_examples(database_path)
        report["source_audit"] = source
        report["exam"] = run_chronological_exam(examples, **exam_options)
    except DataReadinessError as error:
        report["exam"] = {
            "status": "NOT_READY",
            "reason": str(error),
            "promotion": "KEEP_CURRENT_OFFICIAL_PICK",
        }
    return report


def _main() -> int:
    parser = argparse.ArgumentParser(description="Offline V3 official-pick challenger audit and final exam")
    parser.add_argument("--db", required=True, help="existing ai_predictions.db; opened read-only")
    parser.add_argument("--textbook", help="optional master_training_data.csv to audit only")
    parser.add_argument("--report", required=True, help="JSON report output path")
    parser.add_argument("--min-train-matches", type=int, default=MIN_TRAIN_MATCHES)
    parser.add_argument("--min-tune-matches", type=int, default=MIN_TUNE_MATCHES)
    parser.add_argument("--min-final-matches", type=int, default=MIN_FINAL_MATCHES)
    parser.add_argument("--target-accuracy", type=float, default=TARGET_ACCURACY)
    args = parser.parse_args()
    report = build_report(
        args.db,
        args.textbook,
        min_train_matches=args.min_train_matches,
        min_tune_matches=args.min_tune_matches,
        min_final_matches=args.min_final_matches,
        target_accuracy=args.target_accuracy,
    )
    destination = Path(args.report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["exam"]["status"], "promotion": report["exam"].get("promotion")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
