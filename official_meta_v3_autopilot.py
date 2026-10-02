"""Autonomous, web-visible V3 learning picks.

This is deliberately a separate challenger.  It reads the existing frozen
pre-kickoff candidate history, retrains from completed results, then freezes a
V3 learning pick for each newly seen upcoming match.  It never changes the
official pick, autonomous robot, V2 Alphago pick, source SQLite database, or
historical records.  Its only local write is its own JSON publication file.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import pickle
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import official_meta_v3 as meta


AUTOPILOT_VERSION = "official-meta-v3-autonomous-web-learning-v4-products"
OUTPUT_SCHEMA = "official-meta-v3.web-learning-picks.v1"
MINIMUM_COMPLETED_MATCHES = meta.MIN_TRAIN_MATCHES


class AutopilotNotReady(RuntimeError):
    """A safe, reportable reason not to create new V3 learning picks."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def _readonly_connection(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    if not path.exists():
        raise AutopilotNotReady(f"database not found: {path}")
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


@dataclass(frozen=True)
class PendingSnapshot:
    match_id: str
    snapshot_id: int
    created_at: str
    stage: str
    candidates: tuple[dict[str, Any], ...]
    source_kind: str = "analysis_snapshot"


def _load_pending_snapshots(database_path: str | Path) -> list[PendingSnapshot]:
    """Return the latest known pre-match snapshot for each pending prediction."""
    connection = _readonly_connection(database_path)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {"predictions", "prediction_analysis_snapshots"}
        missing = sorted(required - tables)
        if missing:
            raise AutopilotNotReady("required tables missing: " + ", ".join(missing))
        rows = connection.execute(
            """
            SELECT s.id, s.match_id, s.stage, s.created_at, p.match_time
            FROM prediction_analysis_snapshots AS s
            JOIN predictions AS p ON p.match_id = s.match_id
            WHERE COALESCE(p.actual_result, 'PENDING') = 'PENDING'
              AND (s.stage LIKE 'T-%' OR s.stage='regular')
              AND p.match_id NOT LIKE 'WORLD_%'
            ORDER BY s.match_id ASC, s.id DESC
            """
        ).fetchall()
    finally:
        connection.close()

    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        match_id = str(row["match_id"] or "")
        if match_id and match_id not in latest:
            latest[match_id] = row

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
    for match_id, row in latest.items():
        from scorecard_core import epoch, KST
        kickoff = epoch(row['match_time'], KST)
        if not 0 < epoch(row['created_at']) < kickoff or kickoff <= datetime.now(timezone.utc).timestamp():
            continue
        candidates = meta._safe_json(candidate_payloads.get(int(row['id'])), [])
        valid = tuple(candidate for candidate in candidates if isinstance(candidate, dict))
        if valid:
            pending.append(
                PendingSnapshot(
                    match_id=match_id,
                    snapshot_id=int(row["id"]),
                    created_at=str(row["created_at"] or ""),
                    stage=str(row["stage"] or ""),
                    candidates=valid,
                )
            )
    return sorted(pending, key=lambda item: (item.created_at, item.match_id))


def _future_epoch(value: Any) -> float | None:
    """Return a future UTC epoch from the card's saved kickoff timestamp.

    The dashboard collector stores this value before the match starts. A
    missing or unparseable timestamp is deliberately rejected: V3 must never
    create a retrospective pick merely to fill a web card.
    """
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return None
    if epoch > 10_000_000_000:  # tolerate a future millisecond timestamp
        epoch /= 1000.0
    return epoch if epoch > datetime.now(timezone.utc).timestamp() else None


def _load_pending_dashboard_cards(dashboard_path: str | Path) -> list[PendingSnapshot]:
    """Use saved pre-kickoff dashboard candidates under the displayed match ID.

    Fresh protocol cards can be published before the SQLite candidate snapshot
    is available. Previously this left a V3 pick keyed to an older database
    ID, while the web card was keyed by its current ``match.id``. This reader
    only consumes the already-saved, pre-kickoff card data and freezes its V3
    pick under that exact displayed ID. It never writes dashboard data or the
    source database.
    """
    payload = _read_json(Path(dashboard_path), {})
    if not isinstance(payload, dict):
        return []

    pending: dict[str, PendingSnapshot] = {}
    for collection_name in ("proto", "top3", "toto14"):
        cards = payload.get(collection_name) or []
        if not isinstance(cards, list):
            continue
        for card in cards:
            if not isinstance(card, dict):
                continue
            match = card.get("match") or {}
            if not isinstance(match, dict):
                continue
            match_id = str(match.get("id") or "").strip()
            from scorecard_core import epoch, KST
            if collection_name == "toto14":
                match_id = "TOTO14_" + match_id
            kickoff_epoch = _future_epoch(card.get("timestamp") or epoch(match.get("match_time"), KST))
            if not match_id or kickoff_epoch is None or match_id in pending:
                continue
            source_candidates = card.get("display_candidates") or []
            if collection_name == "toto14" and not card.get("probabilities_unavailable"):
                source_candidates = [_toto14_candidate(str(match.get("home") or ""),
                    str(match.get("away") or ""), side, _probability(card.get(field)),
                    _probability(card.get("analysis_confidence")))
                    for side,field in (("home","p_h"),("draw","p_d"),("away","p_a"))]
            if str(card.get("analysis_stage") or "").upper().startswith(("PENDING", "PREVIEW")):
                continue
            if not isinstance(source_candidates, list):
                continue
            candidates = tuple(
                dict(candidate)
                for candidate in source_candidates
                if isinstance(candidate, dict)
            )
            if not candidates:
                continue
            snapshot_value = card.get("public_pick_snapshot_id")
            try:
                snapshot_id = int(snapshot_value) if snapshot_value is not None else 0
            except (TypeError, ValueError):
                snapshot_id = 0
            pending[match_id] = PendingSnapshot(
                match_id=match_id,
                snapshot_id=snapshot_id,
                created_at=str(
                    card.get("display_candidates_saved_at")
                    or card.get("timestamp")
                    or ""
                ),
                stage="DASHBOARD_PREKICKOFF",
                candidates=candidates,
                source_kind="toto14_dashboard_card" if collection_name == "toto14" else "dashboard_card",
            )
    return sorted(pending.values(), key=lambda item: (item.created_at, item.match_id))


def _load_pending_world_cards(world_dashboard_path: str | Path | None) -> list[PendingSnapshot]:
    """Freeze V3 under the exact WORLD card ID after full-context analysis exists."""
    if world_dashboard_path is None:
        return []
    payload = _read_json(Path(world_dashboard_path), {})
    if not isinstance(payload, dict):
        return []
    pending: dict[str, PendingSnapshot] = {}
    for card in payload.get("matches") or []:
        if not isinstance(card, dict):
            continue
        match = card.get("match") or {}
        analysis = card.get("analysis") or {}
        if not isinstance(match, dict) or not isinstance(analysis, dict):
            continue
        stage = str(analysis.get("analysis_stage") or card.get("analysis_stage") or "")
        if not stage or stage == "market-preview":
            continue
        match_id = str(match.get("id") or "").strip()
        kickoff_epoch = _future_epoch(card.get("timestamp"))
        if not match_id or kickoff_epoch is None or match_id in pending:
            continue
        candidates = tuple(
            dict(candidate)
            for candidate in (analysis.get("candidates") or [])
            if isinstance(candidate, dict)
        )
        if not candidates:
            continue
        snapshot_value = analysis.get("public_pick_snapshot_id") or card.get("public_pick_snapshot_id")
        try:
            snapshot_id = int(snapshot_value) if snapshot_value is not None else 0
        except (TypeError, ValueError):
            snapshot_id = 0
        pending[match_id] = PendingSnapshot(
            match_id=match_id,
            snapshot_id=snapshot_id,
            created_at=str(analysis.get("analyzed_at") or card.get("analyzed_at") or card.get("timestamp") or ""),
            stage=stage,
            candidates=candidates,
            source_kind="world_dashboard_card",
        )
    return sorted(pending.values(), key=lambda item: (item.created_at, item.match_id))


def _probability(value: Any) -> float:
    """Accept the frozen Toto14 percentage or probability without inventing one."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if result > 1.0:
        result /= 100.0
    return max(0.0, min(1.0, result))


def _toto14_candidate(
    home_team: str, away_team: str, side: str, probability: float, confidence: float
) -> dict[str, Any]:
    raw_pick = (
        f"{home_team} 승" if side == "home" else
        f"{away_team} 승" if side == "away" else "무승부"
    )
    return {
        "market_key": "1x2",
        "selection_side": side,
        "raw_pick": raw_pick,
        # These values are saved in the Toto14 pre-kickoff freeze.  They are
        # inputs only; no after-result field is added to the V3 feature set.
        "model_probability": probability,
        "raw_model_probability": probability,
        "robust_probability": probability,
        "fair_probability": probability,
        "data_confidence": max(0.0, min(1.0, confidence)),
        "settlement_supported": True,
    }


def _load_pending_toto14_freezes(database_path: str | Path) -> list[PendingSnapshot]:
    """Read Toto14's own frozen W/D/L table as an independent V3 input.

    Toto14 does not share the general candidate-snapshot table.  Its frozen
    payload is still a pre-kickoff record, so it can safely become a V3
    learning pick without changing the existing Toto14 marks or its database.
    """
    connection = _readonly_connection(database_path)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {"predictions", "toto14_prediction_freezes"}
        if not required.issubset(tables):
            return []
        rows = connection.execute(
            """
            SELECT p.match_id, p.match_time, p.home_team, p.away_team,
                   f.payload_json, f.frozen_at
            FROM predictions AS p
            JOIN toto14_prediction_freezes AS f ON f.match_id = p.match_id
            WHERE COALESCE(p.actual_result, 'PENDING') = 'PENDING'
              AND COALESCE(p.is_toto14, 0) = 1
            ORDER BY f.frozen_at ASC, p.match_id ASC
            """
        ).fetchall()
    finally:
        connection.close()

    pending: list[PendingSnapshot] = []
    for row in rows:
        match_id = str(row["match_id"] or "").strip()
        payload = meta._safe_json(row["payload_json"], {})
        if not match_id or not isinstance(payload, dict):
            continue
        from scorecard_core import epoch, KST
        kickoff = epoch(row['match_time'], KST)
        if not 0 < epoch(row['frozen_at']) < kickoff or kickoff <= datetime.now(timezone.utc).timestamp():
            continue
        home_team = str(payload.get("match", {}).get("home") or row["home_team"] or "홈팀")
        away_team = str(payload.get("match", {}).get("away") or row["away_team"] or "원정팀")
        probabilities = {
            "home": _probability(payload.get("p_h")),
            "draw": _probability(payload.get("p_d")),
            "away": _probability(payload.get("p_a")),
        }
        if not any(probabilities.values()):
            continue
        confidence = _probability(payload.get("analysis_confidence"))
        candidates = tuple(
            _toto14_candidate(home_team, away_team, side, probability, confidence)
            for side, probability in probabilities.items()
        )
        pending.append(
            PendingSnapshot(
                match_id=match_id,
                snapshot_id=0,
                created_at=str(row["frozen_at"] or row["match_time"] or ""),
                stage="TOTO14_FROZEN",
                candidates=candidates,
                source_kind="toto14_freeze",
            )
        )
    return pending


def _candidate_example(snapshot: PendingSnapshot, candidate: dict[str, Any]) -> meta.CandidateExample | None:
    market_key = str(candidate.get("market_key") or "").strip()
    raw_pick = str(candidate.get("raw_pick") or "").strip()
    if not market_key or not raw_pick:
        return None
    return meta.CandidateExample(
        match_id=snapshot.match_id,
        snapshot_id=snapshot.snapshot_id,
        created_at=snapshot.created_at,
        stage=snapshot.stage,
        market_key=market_key,
        raw_pick=raw_pick,
        label=0,
        baseline_selected=False,
        baseline_fallback=False,
        features=meta._feature_dict(candidate),
    )


def _train_from_completed_history(
    database_path: str | Path,
) -> tuple[meta.ChallengerModel, meta.FrozenFeatureEncoder, dict[str, Any]]:
    completed, source = meta.load_frozen_examples(database_path)
    match_count = len({row.match_id for row in completed})
    if match_count < MINIMUM_COMPLETED_MATCHES:
        raise AutopilotNotReady(
            f"completed match history is below {MINIMUM_COMPLETED_MATCHES}: {match_count}"
        )

    exam = meta.run_chronological_exam(completed)
    selected_config = exam.get("selected_config") if isinstance(exam, dict) else None
    if not isinstance(selected_config, dict):
        selected_config = {"iterations": 72, "learning_rate": 0.05, "min_leaf": 24}
    encoder = meta.FrozenFeatureEncoder().fit(completed)
    model = meta.ChallengerModel(**selected_config).fit(
        encoder.transform(completed), [row.label for row in completed]
    )
    summary = {
        "completed_matches": match_count,
        "completed_candidates": len(completed),
        "learner_backend": model.backend,
        "training_config": selected_config,
        "source_audit": source,
        "historical_exam": {
            key: exam.get(key)
            for key in ("status", "qualification", "promotion", "target_accuracy", "segments", "final")
        },
    }
    return model, encoder, summary


def _model_from_completed_history(database_path):
    # Serving only. Training and promotion belong to the independent worker.
    from learning_state import active_entry
    root = Path(database_path).parent
    entry = active_entry(root, 'v3')
    name = entry.get('artifact')
    if name and Path(name).name == name:
        try:
            with (root/'.learning_models'/name).open('rb') as stream:
                result = pickle.load(stream)
            if not isinstance(result,tuple) or len(result)!=3:
                raise ValueError('invalid V3 artifact')
            result[2]['active_model_version'] = entry.get('active_version','unverified')
            return result
        except (OSError, ValueError, EOFError, pickle.PickleError, AttributeError, ImportError, TypeError):
            pass
    cache = root/'.v3_serving_cache.pkl'
    try:
        with cache.open('rb') as stream:
            saved = pickle.load(stream)
        result = saved['result']
        result[2]['retained_legacy_model'] = True
        result[2]['active_model_version'] = 'legacy-v3-fallback'
        return result
    except (OSError, ValueError, KeyError, EOFError, pickle.PickleError, AttributeError, ImportError):
        raise AutopilotNotReady('V3 기존 모델 없음 · 독립 학습 작업의 검증 완료 대기')


def _score_result_side(home_score: float, away_score: float) -> str:
    if home_score > away_score:
        return "home"
    if home_score < away_score:
        return "away"
    return "draw"


def _dashboard_pick_result(pick: dict[str, Any], actual_score: str) -> int | None:
    """Grade a dashboard-sourced frozen pick without inventing a later pick.

    This is limited to the original candidate's market and direction. Ties at
    an exact line are kept ungraded rather than being mislabeled as a win/loss.
    """
    try:
        home_score, away_score = (
            int(part.strip()) for part in str(actual_score).split(":", 1)
        )
    except (TypeError, ValueError):
        return None
    market = str(pick.get("market_key") or "").strip().lower()
    side = str(pick.get("selection_side") or "").strip().lower()
    if market == "1x2":
        return int(side == _score_result_side(home_score, away_score))
    if market == "totals":
        try:
            line = float(pick.get("totals_base"))
        except (TypeError, ValueError):
            return None
        total = home_score + away_score
        if total == line:
            return None
        return int(side == ("over" if total > line else "under"))
    if market == "handicap":
        try:
            line = float(pick.get("handicap_base"))
        except (TypeError, ValueError):
            return None
        return int(side == _score_result_side(home_score + line, away_score))
    return None


def _grade_frozen_picks(
    database_path: str | Path, picks: dict[str, Any]
) -> tuple[int, int]:
    """Attach grades only to already frozen V3 cards; source DB stays read-only."""
    connection = _readonly_connection(database_path)
    graded = 0
    total = 0
    try:
        for match_id, pick in picks.items():
            if not isinstance(pick, dict):
                continue
            if not pick.get('home_team') or not pick.get('kickoff_at'):
                # Repair labels only; never change the frozen answer or grade.
                from scorecard_core import epoch, KST
                identity = connection.execute('''SELECT home_team,away_team,match_time,api_fixture_id
                    FROM predictions WHERE match_id=?''',(str(match_id),)).fetchone()
                if identity:
                    ko=epoch(identity['match_time'],KST)
                    captured=epoch(pick.get('frozen_at'))
                    same_fixture=bool(pick.get('api_fixture_id')) and str(pick['api_fixture_id'])==str(identity['api_fixture_id'])
                    if same_fixture and 0<captured<ko and ko-captured<=14*86400:
                        for field,value in zip(('home_team','away_team','kickoff_at','api_fixture_id'),identity):
                            if not pick.get(field):pick[field]=value
            total += 1
            if pick.get("is_correct") in (0, 1):
                graded += 1
                continue
            if str(match_id).startswith("TOTO14_") or str(pick.get("source_kind") or "") in {"toto14_freeze", "toto14_dashboard_card"}:
                row = connection.execute(
                    """
                    SELECT actual_result, actual_score
                    FROM predictions
                    WHERE match_id = ? AND COALESCE(is_toto14, 0) = 1
                    LIMIT 1
                    """,
                    (str(match_id),),
                ).fetchone()
                score = str(row["actual_score"] or "") if row else ""
                if not row or str(row["actual_result"] or "") != "FINISHED" or ":" not in score:
                    continue
                try:
                    home_score, away_score = (int(value.strip()) for value in score.split(":", 1))
                except ValueError:
                    continue
                result_side = "home" if home_score > away_score else "away" if away_score > home_score else "draw"
                pick["is_correct"] = int(str(pick.get("selection_side") or "") == result_side)
                pick["actual_score"] = score
                pick["graded_at"] = _now()
                pick["status"] = "FINISHED"
                graded += 1
                continue
            if str(pick.get("source_kind") or "") == "dashboard_card":
                row = connection.execute(
                    """
                    SELECT actual_result, actual_score
                    FROM predictions
                    WHERE match_id = ? AND COALESCE(is_toto14, 0) = 0
                    ORDER BY rowid DESC LIMIT 1
                    """,
                    (str(match_id),),
                ).fetchone()
                score = str(row["actual_score"] or "") if row else ""
                if not row or str(row["actual_result"] or "") != "FINISHED" or ":" not in score:
                    continue
                is_correct = _dashboard_pick_result(pick, score)
                if is_correct is None:
                    continue
                pick["is_correct"] = is_correct
                pick["actual_score"] = score
                pick["graded_at"] = _now()
                pick["status"] = "FINISHED"
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
            pick["is_correct"] = int(row["is_correct"])
            pick["actual_score"] = str(row["actual_score"] or "")
            pick["graded_at"] = str(row["graded_at"] or "")
            pick["status"] = "FINISHED"
            graded += 1
    finally:
        connection.close()
    return graded, total


def _visible_pick(
    snapshot: PendingSnapshot,
    model: meta.ChallengerModel,
    encoder: meta.FrozenFeatureEncoder,
    generated_at: str,
) -> dict[str, Any] | None:
    examples = [
        example
        for candidate in snapshot.candidates
        for example in (_candidate_example(snapshot, candidate),)
        if example is not None
    ]
    if not examples:
        return None
    probabilities = model.predict_proba(encoder.transform(examples))
    selected, probability = max(
        zip(examples, probabilities),
        key=lambda pair: (float(pair[1]), pair[0].market_key, pair[0].raw_pick),
    )
    selected_candidate = next(
        (
            candidate
            for candidate in snapshot.candidates
            if str(candidate.get("market_key") or "") == selected.market_key
            and str(candidate.get("raw_pick") or "") == selected.raw_pick
        ),
        {},
    )
    return {
        "schema_version": OUTPUT_SCHEMA,
        "status": "LEARNING_SHADOW",
        "match_id": snapshot.match_id,
        "source_snapshot_id": snapshot.snapshot_id,
        "source_stage": snapshot.stage,
        "source_kind": snapshot.source_kind,
        "source_created_at": snapshot.created_at,
        "frozen_at": generated_at,
        "market_key": selected.market_key,
        "raw_pick": selected.raw_pick,
        "selection_side": str(selected_candidate.get("selection_side") or ""),
        "totals_base": selected_candidate.get("totals_base"),
        "handicap_base": selected_candidate.get("handicap_base"),
        "probability": round(float(probability), 6),
        "candidate_scores": [
            {**candidate, "v3_probability":round(float(prob),6)}
            for example,prob in zip(examples, probabilities)
            for candidate in snapshot.candidates
            if candidate.get("raw_pick") == example.raw_pick and candidate.get("market_key") == example.market_key
        ],
        "label": "V3 학습픽 · 검증 중",
        "official_pick_changed": False,
        "reason": "종료 결과가 누적될 때마다 재학습한 V3 도전자 결과입니다.",
    }


def build_autopilot_payload(
    database_path: str | Path,
    existing_payload: dict[str, Any] | None = None,
    dashboard_path: str | Path | None = None,
    world_dashboard_path: str | Path | None = None,
) -> dict[str, Any]:
    """Retrain from completed history and freeze V3 picks for pending cards."""
    generated_at = _now()
    existing_payload = existing_payload if isinstance(existing_payload, dict) else {}
    picks = {
        str(match_id): dict(pick)
        for match_id, pick in (existing_payload.get("picks") or {}).items()
        if isinstance(pick, dict)
    }
    base = {
        "schema_version": OUTPUT_SCHEMA,
        "version": AUTOPILOT_VERSION,
        "generated_at": generated_at,
        "mode": "web_visible_learning_challenger",
        "customer_official_pick_changed": False,
        "promotion": "KEEP_CURRENT_OFFICIAL_PICK",
        "picks": picks,
    }
    from learning_state import archive_json_row, revision_allowed, versions
    base['pick_revision_history'] = dict(existing_payload.get('pick_revision_history') or {})
    try:
        model, encoder, training = _model_from_completed_history(database_path)
        created = 0
        dashboard = (
            Path(dashboard_path)
            if dashboard_path is not None
            else Path(database_path).with_name("dashboard_data.json")
        )
        board = _read_json(dashboard, {})
        cards = {}
        for kind in ('proto','top3','toto14'):
            for card in board.get(kind, []):
                mid = str((card.get('match') or {}).get('id') or '')
                if kind == 'toto14': mid = 'TOTO14_' + mid
                cards.setdefault(mid, card)
        pending_snapshots = _load_pending_dashboard_cards(dashboard)
        pending_snapshots.extend(_load_pending_world_cards(world_dashboard_path))
        pending_snapshots.extend(_load_pending_snapshots(database_path))
        pending_snapshots.extend(_load_pending_toto14_freezes(database_path))
        investment_candidates, visited = {}, set()
        for snapshot in pending_snapshots:
            if snapshot.match_id in visited:
                continue
            visited.add(snapshot.match_id)
            # V3 itself may learn a new model later, but a pick already shown
            # for this match remains immutable for honest future grading.
            pick = _visible_pick(snapshot, model, encoder, generated_at)
            if pick is not None:
                card = cards.get(snapshot.match_id) or {}
                match = card.get('match') or {}
                with _readonly_connection(database_path) as guard:
                    identity = guard.execute('SELECT actual_result,match_time,home_team,away_team FROM predictions WHERE match_id=?',(snapshot.match_id,)).fetchone()
                if identity:
                    if match and (match.get('home'),match.get('away')) != (identity['home_team'],identity['away_team']):
                        continue
                    if not match:
                        match={'home':identity['home_team'],'away':identity['away_team'],'match_time':identity['match_time']}
                pick.update(learning_campaign=card.get('learning_campaign',''),
                            model_version=training.get('active_model_version','legacy-unverified'),
                            home_team=match.get('home'), away_team=match.get('away'),
                            kickoff_at=match.get('match_time'), api_fixture_id=card.get('api_fixture_id'))
                investment_candidates[snapshot.match_id] = pick
                old = picks.get(snapshot.match_id)
                if old is None or revision_allowed(old,pick,match):
                    # Recheck canonical result/time after model inference, before replacing.
                    with _readonly_connection(database_path) as guard:
                        row = guard.execute('SELECT actual_result,match_time FROM predictions WHERE match_id=?', (snapshot.match_id,)).fetchone()
                    from learning_state import before_kickoff
                    if row and (row['actual_result'] != 'PENDING' or not before_kickoff({'match_time':row['match_time']})):
                        continue
                    if match and not before_kickoff(match):
                        continue
                    if old: archive_json_row(base,snapshot.match_id,old)
                    picks[snapshot.match_id] = pick
                    created += 1
        base['investment_candidates'] = investment_candidates
        graded, total = _grade_frozen_picks(database_path, picks)
        base.update(
            {
                "status": "READY",
                "training": training,
                "newly_frozen_picks": created,
                "frozen_pick_count": total,
                "graded_pick_count": graded,
                "live_learning_accuracy": (
                    round(
                        sum(int(pick.get("is_correct") or 0) for pick in picks.values() if pick.get("is_correct") in (0, 1))
                        / graded,
                        6,
                    )
                    if graded
                    else None
                ),
            }
        )
    except (AutopilotNotReady, meta.DataReadinessError) as error:
        graded, total = _grade_frozen_picks(database_path, picks)
        base.update(
            {
                "status": "NOT_READY",
                "reason": str(error),
                "newly_frozen_picks": 0,
                "frozen_pick_count": total,
                "graded_pick_count": graded,
            }
        )
    return base


def refresh_autopilot(
    database_path: str | Path,
    output_path: str | Path,
    dashboard_path: str | Path | None = None,
    world_dashboard_path: str | Path | None = None,
) -> dict[str, Any]:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        previous = _read_json(output, {})
        payload = build_autopilot_payload(
            database_path, previous, dashboard_path, world_dashboard_path
        )
        from learning_state import guard_ledger_revisions
        guard_ledger_revisions(database_path,payload,previous)
        _write_json_atomically(output, payload)
        return payload



def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Autonomous V3 learning picks for the web; never replaces the official pick"
    )
    parser.add_argument("--db", required=True, help="existing ai_predictions.db; opened read-only")
    parser.add_argument("--output", required=True, help="V3 learning JSON written by this process")
    parser.add_argument(
        "--dashboard",
        help="saved dashboard_data.json; defaults beside --db and is read only",
    )
    parser.add_argument(
        "--world-dashboard",
        help="saved world_dashboard.json; read only, used only after full-context analysis",
    )
    args = parser.parse_args()
    payload = refresh_autopilot(
        args.db, args.output, args.dashboard, args.world_dashboard
    )
    print(
        json.dumps(
            {
                "status": payload.get("status"),
                "newly_frozen_picks": payload.get("newly_frozen_picks"),
                "frozen_pick_count": payload.get("frozen_pick_count"),
                "graded_pick_count": payload.get("graded_pick_count"),
                "promotion": payload.get("promotion"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
