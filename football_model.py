"""Offline, bounded challenger trained only on already cached final scores.

No network, database writes or dependency on the collector. A chronological
holdout must beat the venue-shrinkage baseline on BOTH WDL Brier and log loss.
This is a promotion gate, not a claim of prospective profitability.
"""
import math
from collections import Counter

MODEL_VERSION = "time-weighted-opponent-dixon-coles-v2"
AUTONOMOUS_ROBOT_POLICY_VERSION = "self-learning-full-market-online-v2"
OFFICIAL_PICK_POLICY_VERSION = "evidence-ensemble-accuracy-first-v2"
LEGACY_V4_POLICY_VERSION = "legacy-v4-reconstructed-20260821-v1"
MIN_TRAIN = 160
MIN_VALIDATION = 40
MIN_RHO_LOW_SCORE_TRAIN = 30
ROBOT_MODEL_VERSION = "autonomous-pre-match-goals-online-v2"
ROBOT_FEATURE_SCHEMA_VERSION = "robot-features.v1"


def price_eligible(pick, confidence):
    market = pick.get("market_key") or "1x2"
    side = str(pick.get("selection_side") or "").strip().lower()
    # A single 40% floor for every 1X2 outcome silently excluded almost every
    # realistic draw.  Draws are a normal W/D/L direction, so use a draw-aware
    # probability floor while keeping the same conservative EV/edge checks.
    probability_floor = .25 if market == "1x2" and side == "draw" else (
        .4 if market == "1x2" else .5
    )
    return bool(confidence >= .5 and pick.get("settlement_supported", True)
                and float(pick.get("odd") or 0) > 1
                and pick.get("fair_prob") is not None
                and float(pick.get("robust_ev") or 0) >= 1.01
                and (float(pick.get("prob") or 0) >= probability_floor
                     or pick.get("is_qualified_underdog")))


def probability_price_choice(picks, confidence, band=0):
    """Band is a validated policy allowance, never a hard minimum price."""
    highest = max(float(p["prob"]) for p in picks)
    priced = [p for p in picks if highest-float(p["prob"]) <= band+1e-6 and price_eligible(p, confidence)]
    pool = priced or [p for p in picks if highest-float(p["prob"]) <= 1e-6]
    return max(pool, key=lambda p: (float(p["prob"]), float(p.get("robust_ev") or 0),
                                    float(p.get("robust_edge") or 0), p.get("market_key") == "1x2"))


def _selection_key(pick):
    return (float(pick.get("robust_probability") or pick.get("prob") or 0),
            float(pick.get("prob") or 0), float(pick.get("robust_edge") or 0),
            float(pick.get("robust_ev") or 0), str(pick.get("raw_pick") or ""))


def _market_neutral_probability(pick):
    """Return the natural neutral point for cross-market conviction."""
    return .5 if (pick.get("market_key") or "1x2") == "totals" else 1.0 / 3.0


def _evidence_alignment(pick):
    try:
        return max(-1.0, min(1.0, float(pick.get("context_alignment") or 0)))
    except (TypeError, ValueError):
        return 0.0


def all_evidence_choice(picks, confidence, return_reason=False):
    """Choose one official pick across every supported market.

    The old policy selected W/D/L first and allowed handicap or totals only as
    a narrow exception.  This selector puts all settlement-compatible markets
    on a comparable conviction scale, then combines probability, verified
    price value and independently attached pre-match evidence.  It always
    returns exactly one candidate and never mutates the caller's list.
    """
    available = [
        dict(pick) for pick in (picks or [])
        if isinstance(pick, dict)
        and pick.get("settlement_supported", True)
        and 0 < float(pick.get("prob") or 0) <= 1
    ]
    if not available:
        raise ValueError("no settlement-compatible candidate")

    confidence = max(0.0, min(1.0, float(confidence or 0)))
    for pick in available:
        raw_probability = max(0.0, min(1.0, float(pick.get("prob") or 0)))
        probability = max(0.0, min(1.0, float(
            pick.get("robust_probability")
            if pick.get("robust_probability") is not None
            else raw_probability
        )))
        interval = pick.get("probability_interval") or {}
        try:
            lower_bound = float(interval.get("low"))
        except (TypeError, ValueError):
            lower_bound = probability
        lower_bound = max(0.0, min(probability, lower_bound))
        uncertainty = max(0.0, raw_probability - lower_bound)
        odd = float(pick.get("odd") or 0)
        fair = pick.get("fair_prob")
        try:
            fair = float(fair) if fair is not None else None
        except (TypeError, ValueError):
            fair = None
        priced = bool(odd > 1.0 and fair is not None and 0 < fair < 1)
        edge = float(pick.get("robust_edge") or 0) if priced else 0.0
        expected_return = float(pick.get("robust_ev") or probability * odd) if priced else 1.0
        context = _evidence_alignment(pick)
        support_count = int(pick.get("independent_support_count") or 0)
        support = min(.025, support_count * .005)
        # The official answer is now an accuracy-first ensemble.  Cross-market
        # candidates are ranked by their conservative chance of settling as a
        # win, not by Kelly or a high price.  Price is retained only as a small
        # calibration-agreement and final tie-break signal.  Context is already
        # inside the coherent score distribution; the bounded term below only
        # rewards independent agreement and cannot manufacture a new forecast.
        market_confirmation = fair if priced else probability
        market_disagreement = abs(probability - fair) if priced else 0.0
        context_support = context * .035
        value_tiebreak = max(-.008, min(.008, (expected_return - 1.0) * .02)) if priced else 0.0
        score = (
            probability * .57
            + lower_bound * .25
            + raw_probability * .10
            + market_confirmation * .08
            + context_support
            + support
            + value_tiebreak
            - uncertainty * .08
            - market_disagreement * .025
        ) * (.90 + confidence * .10)
        pick.update({
            "official_score": round(score, 6),
            "official_accuracy_probability": round(probability, 6),
            "official_probability_floor": round(lower_bound, 6),
            "official_uncertainty": round(uncertainty, 6),
            "official_market_confirmation": round(market_confirmation, 6),
            "official_value_tiebreak": round(value_tiebreak, 6),
            "official_context_score": round(context, 6),
            "official_price_verified": priced,
            "official_policy_version": OFFICIAL_PICK_POLICY_VERSION,
            "selection_axis": "evidence_ensemble_accuracy_first",
        })

    chosen = max(
        available,
        key=lambda pick: (
            float(pick.get("official_score") or 0),
            float(pick.get("robust_probability") or pick.get("prob") or 0),
            float(pick.get("context_alignment") or 0),
            float(pick.get("robust_edge") or 0),
            str(pick.get("raw_pick") or ""),
        ),
    )
    chosen["recommendation_status"] = "SELECTED"
    chosen["selection_reason"] = (
        "경기 전 전체 지표가 반영된 동일 점수분포에서 승무패·3방향 핸디캡·"
        "언더오버를 모두 비교하고, 배당수익보다 보수적인 실제 적중확률과 "
        "불확실성·독립근거 합치를 우선해 한 방향을 선택했습니다."
    )
    reason = "evidence_ensemble_accuracy_first"
    return (chosen, reason) if return_reason else chosen


def _legacy_v4_numeric(features, key, default=0.0):
    return _finite_number((features or {}).get(key), default)


def build_legacy_v4_candidates(picks, features):
    """Reconstruct the aggressive 2026-08-21 V4-style pre-match forecast.

    This comparison engine intentionally keeps the old direct-addition style:
    venue rates, recent form, ranking, absences, rest, motivation and H2H can
    move expected goals materially.  It is isolated from the new official and
    robot models and is never allowed to rewrite an old public prediction.
    """
    features = features if isinstance(features, dict) else {}
    base_h = max(.20, min(4.2, _legacy_v4_numeric(
        features, "base_home_goals",
        _legacy_v4_numeric(features, "context_home_goals", 1.35),
    )))
    base_a = max(.20, min(4.2, _legacy_v4_numeric(
        features, "base_away_goals",
        _legacy_v4_numeric(features, "context_away_goals", 1.15),
    )))

    rank_h = _legacy_v4_numeric(features, "home_rank")
    rank_a = _legacy_v4_numeric(features, "away_rank")
    ranks_known = bool(
        _legacy_v4_numeric(features, "home_rank_known", rank_h > 0)
        and _legacy_v4_numeric(features, "away_rank_known", rank_a > 0)
        and rank_h > 0 and rank_a > 0
    )
    depth_h = .5 if ranks_known and rank_h <= 5 else (1.5 if rank_h >= 15 else 1.0)
    depth_a = .5 if ranks_known and rank_a <= 5 else (1.5 if rank_a >= 15 else 1.0)
    absence_h = max(0.0, _legacy_v4_numeric(features, "home_absence"))
    absence_a = max(0.0, _legacy_v4_numeric(features, "away_absence"))
    lineup_h = max(0.0, _legacy_v4_numeric(features, "home_lineup_penalty"))
    lineup_a = max(0.0, _legacy_v4_numeric(features, "away_lineup_penalty"))
    rest_h = _legacy_v4_numeric(features, "home_rest_days", 90)
    rest_a = _legacy_v4_numeric(features, "away_rest_days", 90)
    fatigue_h = .12 if 0 < rest_h <= 3 else 0.0
    fatigue_a = .12 if 0 < rest_a <= 3 else 0.0
    penalty_h = min(.60, (absence_h + lineup_h + fatigue_h) * depth_h)
    penalty_a = min(.60, (absence_a + lineup_a + fatigue_a) * depth_a)

    recent_h = _legacy_v4_numeric(features, "home_recent_strength", .5)
    recent_a = _legacy_v4_numeric(features, "away_recent_strength", .5)
    recent_h_multiplier = max(.72, min(1.28, 1.0 + (recent_h - .5) * .42))
    recent_a_multiplier = max(.72, min(1.28, 1.0 + (recent_a - .5) * .42))

    total_h2h = max(0.0, _legacy_v4_numeric(features, "h2h_total"))
    wins_h = max(0.0, _legacy_v4_numeric(features, "h2h_home_wins"))
    wins_a = max(0.0, _legacy_v4_numeric(features, "h2h_away_wins"))
    share_h = wins_h / total_h2h if total_h2h > 0 else 0.0
    share_a = wins_a / total_h2h if total_h2h > 0 else 0.0
    h2h_h = share_h * .30 + (.35 if total_h2h >= 3 and share_h >= .65 else 0.0)
    h2h_a = share_a * .30 + (.35 if total_h2h >= 3 and share_a >= .65 else 0.0)

    rank_bonus_h = rank_bonus_a = 0.0
    if ranks_known:
        rank_gap = rank_a - rank_h
        rank_bonus_h = max(-.30, min(.35, rank_gap * .025))
        rank_bonus_a = max(-.30, min(.35, -rank_gap * .025))
    title_h = .25 if ranks_known and rank_h <= 3 else 0.0
    title_a = .25 if ranks_known and rank_a <= 3 else 0.0
    survival_h = .25 if _legacy_v4_numeric(features, "home_survival_active") > 0 else 0.0
    survival_a = .25 if _legacy_v4_numeric(features, "away_survival_active") > 0 else 0.0
    manager_h = .30 if _legacy_v4_numeric(features, "home_manager_active") > 0 else 0.0
    manager_a = .30 if _legacy_v4_numeric(features, "away_manager_active") > 0 else 0.0
    vacation_h = .18 if _legacy_v4_numeric(features, "home_vacation_active") > 0 else 0.0
    vacation_a = .18 if _legacy_v4_numeric(features, "away_vacation_active") > 0 else 0.0
    market_h = .35 if _legacy_v4_numeric(features, "home_market_signal") > 0 else 0.0
    market_a = .35 if _legacy_v4_numeric(features, "away_market_signal") > 0 else 0.0

    exp_h = (
        base_h * recent_h_multiplier * (1.0 - penalty_h)
        + penalty_a * .40 + h2h_h + rank_bonus_h + title_h
        + survival_h + manager_h + market_h - vacation_h
    )
    exp_a = (
        base_a * recent_a_multiplier * (1.0 - penalty_a)
        + penalty_h * .40 + h2h_a + rank_bonus_a + title_a
        + survival_a + manager_a + market_a - vacation_a
    )
    if _legacy_v4_numeric(features, "adverse_weather") > 0:
        exp_h *= .80
        exp_a *= .80
    if _legacy_v4_numeric(features, "cup_or_international") > 0:
        exp_h *= .92
        exp_a *= .92
    exp_h = max(.30, min(4.5, exp_h))
    exp_a = max(.30, min(4.5, exp_a))
    matrix = _robot_score_matrix(exp_h, exp_a, -.15)

    result = []
    for original in picks or []:
        if not isinstance(original, dict) or not original.get("settlement_supported", True):
            continue
        pick = dict(original)
        market = str(pick.get("market_key") or "1x2")
        side = str(pick.get("selection_side") or "")
        line = (
            _legacy_v4_numeric(pick, "handicap_base") if market == "handicap"
            else _legacy_v4_numeric(pick, "totals_base", 2.5) if market == "totals"
            else 0.0
        )
        probability = _robot_market_probability(matrix, market, side, line)
        if _legacy_v4_numeric(features, "is_derby") > 0:
            if market == "1x2" and side == "draw":
                probability *= 1.15
            if market == "totals" and side == "over":
                probability *= 1.10
        odd = _legacy_v4_numeric(pick, "odd")
        fair = _legacy_v4_numeric(
            pick, "fair_prob", _legacy_v4_numeric(pick, "market_prob")
        )
        pick.update({
            "official_probability": _legacy_v4_numeric(pick, "prob"),
            "prob": probability,
            "probability": probability,
            "model_probability": probability,
            "robust_probability": probability,
            "legacy_v4_probability": probability,
            "legacy_v4_expected_goals": {
                "home": round(exp_h, 4), "away": round(exp_a, 4),
            },
            "legacy_v4_policy_version": LEGACY_V4_POLICY_VERSION,
            "legacy_v4_edge": probability - fair if 0 < fair < 1 else 0.0,
            "legacy_v4_ev": probability * odd if odd > 1 else 0.0,
            "robust_edge": probability - fair if 0 < fair < 1 else 0.0,
            "robust_ev": probability * odd if odd > 1 else 0.0,
        })
        result.append(pick)

    # The old code compared raw probabilities across every supported market.
    # Normalize alternatives inside each market after derby boosts so each
    # individual market remains a valid probability distribution.
    for market in {str(row.get("market_key") or "1x2") for row in result}:
        rows = [row for row in result if str(row.get("market_key") or "1x2") == market]
        total = sum(_legacy_v4_numeric(row, "prob") for row in rows)
        if total > 0:
            for row in rows:
                probability = _legacy_v4_numeric(row, "prob") / total
                row["prob"] = row["probability"] = row["model_probability"] = probability
                row["robust_probability"] = row["legacy_v4_probability"] = probability
                fair = _legacy_v4_numeric(
                    row, "fair_prob", _legacy_v4_numeric(row, "market_prob")
                )
                odd = _legacy_v4_numeric(row, "odd")
                row["legacy_v4_edge"] = row["robust_edge"] = (
                    probability - fair if 0 < fair < 1 else 0.0
                )
                row["legacy_v4_ev"] = row["robust_ev"] = (
                    probability * odd if odd > 1 else 0.0
                )
    return result


def legacy_v4_choice(picks, features, return_reason=False):
    """Return the reconstructed V4 engine's single highest-probability pick."""
    candidates = build_legacy_v4_candidates(picks, features)
    if not candidates:
        raise ValueError("no settlement-compatible candidate")
    chosen = max(candidates, key=lambda pick: (
        _legacy_v4_numeric(pick, "legacy_v4_probability"),
        _legacy_v4_numeric(pick, "legacy_v4_ev"),
        str(pick.get("raw_pick") or ""),
    ))
    chosen = dict(chosen)
    chosen.update({
        "recommendation_status": "SELECTED",
        "selection_axis": "legacy_v4_raw_probability",
        "legacy_v4_policy_version": LEGACY_V4_POLICY_VERSION,
        "selection_reason": (
            "복원 V4 방식으로 홈·원정, 최근 흐름, 순위, 결장·체력, 동기와 "
            "맞대결 상성을 기대득점에 직접 반영하고 전 시장에서 가장 높은 "
            "원시 적중확률 한 방향을 선택했습니다."
        ),
    })
    reason = "legacy_v4_raw_probability"
    return (chosen, reason) if return_reason else chosen


def autonomous_robot_choice(picks, confidence, return_reason=False):
    """Choose one independent pre-match pick across every supported market.

    The learned path owns both the probabilities and the final answer.  There
    is no W/D/L anchor, odds floor, value gate, minimum sample count or market
    quota: it simply selects its highest learned hit probability across every
    settlement-compatible market.  Price is retained only as a deterministic
    tie-break and for the public audit.  The function never changes old rows.
    """
    available = [
        dict(pick) for pick in (picks or [])
        if isinstance(pick, dict)
        and pick.get("settlement_supported", True)
        and 0 < float(pick.get("prob") or 0) <= 1
    ]
    if not available:
        raise ValueError("no settlement-compatible candidate")

    confidence = max(0.0, min(1.0, float(confidence or 0)))
    # The self-learning path already owns every candidate probability.  Do not
    # put a human price/value gate back in front of the answer: the robot's own
    # learned probability is the decision variable from its very first grade.
    if any(pick.get("robot_probability") is not None for pick in available):
        for pick in available:
            probability = max(0.0, min(1.0, _finite_number(
                pick.get("robot_probability"), pick.get("prob") or 0
            )))
            odd = _finite_number(pick.get("odd"))
            expected_return = probability * odd if odd > 1.0 else 0.0
            pick.update({
                "prob": probability,
                "robust_probability": probability,
                "robot_score": round(expected_return if odd > 1.0 else probability, 6),
                "robot_kelly": (
                    round((expected_return - 1.0) / max(odd - 1.0, 1e-9), 6)
                    if odd > 1.0 else None
                ),
                "robot_policy_version": AUTONOMOUS_ROBOT_POLICY_VERSION,
                "robot_price_verified": odd > 1.0,
            })
        chosen = max(available, key=lambda pick: (
            _finite_number(pick.get("robot_probability"), pick.get("prob") or 0),
            _finite_number(pick.get("robot_score")),
            _finite_number(pick.get("odd")),
            str(pick.get("raw_pick") or ""),
        ))
        reason = "robot_learned_probability"
        chosen["robot_selection_axis"] = "learned_probability_all_markets"
        chosen["robot_fallback"] = False
        chosen["recommendation_status"] = "SELECTED"
        chosen["selection_reason"] = (
            "로봇이 경기 전 원자료와 누적 채점에서 자체 득점·전 시장 확률을 만든 뒤 "
            "승무패 우선순서, 최소 표본, 배당·가치 통과선 없이 자체 적중확률이 가장 "
            "높은 한 방향을 골랐습니다."
        )
        return (chosen, reason) if return_reason else chosen

    priced = []
    for pick in available:
        probability = max(
            0.0,
            min(1.0, float(
                pick.get("robust_probability")
                or pick.get("prob")
                or 0
            )),
        )
        odd = float(pick.get("odd") or 0)
        fair = pick.get("fair_prob")
        try:
            fair = float(fair) if fair is not None else None
        except (TypeError, ValueError):
            fair = None
        if odd <= 1.0 or fair is None or not 0 < fair < 1:
            continue

        expected_return = float(pick.get("robust_ev") or probability * odd)
        edge = float(pick.get("robust_edge") or (probability - fair))
        kelly = (probability * odd - 1.0) / max(odd - 1.0, 1e-9)
        kelly = max(-0.25, min(0.50, kelly))

        diagnostics = pick.get("learning_diagnostics") or {}
        validated = bool(
            diagnostics.get("calibration_validated")
            and int(diagnostics.get("validation_fixtures") or 0) >= 30
            and diagnostics.get("baseline_brier") is not None
            and diagnostics.get("corrected_brier") is not None
            and float(diagnostics["corrected_brier"])
                < float(diagnostics["baseline_brier"])
        )
        learning_bonus = 0.0
        if validated:
            improvement = max(
                0.0,
                float(diagnostics["baseline_brier"])
                - float(diagnostics["corrected_brier"]),
            )
            learning_bonus = min(0.025, improvement * 0.50)
            samples = int(diagnostics.get("candidate_samples") or 0)
            unit_roi = diagnostics.get("unit_roi")
            if samples >= 80 and unit_roi is not None:
                learning_bonus += max(
                    -0.015, min(0.015, float(unit_roi) * 0.05)
                )

        support_bonus = min(
            0.035,
            int(pick.get("independent_support_count") or 0) * 0.008,
        )
        context_bonus = _evidence_alignment(pick) * 0.12
        # Value terms dominate. Probability is a small stability term only;
        # this makes 40%@3.20 capable of beating 85%@1.20 when its conservative
        # expected growth is genuinely better.
        score = (
            max(-0.10, kelly) * 0.44
            + max(-0.10, min(0.20, edge)) * 0.22
            + max(-0.10, min(0.50, expected_return - 1.0)) * 0.12
            + probability * 0.10
            + context_bonus
            + learning_bonus
            + support_bonus
        ) * (0.75 + confidence * 0.25)
        pick.update({
            "robot_score": round(score, 6),
            "robot_kelly": round(kelly, 6),
            "robot_learning_bonus": round(learning_bonus, 6),
            "robot_context_bonus": round(context_bonus, 6),
            "robot_learning_validated": validated,
            "robot_policy_version": AUTONOMOUS_ROBOT_POLICY_VERSION,
            "robot_selection_axis": "all_markets_all_evidence_value",
            "robot_price_verified": True,
            "robot_fallback": expected_return < 1.0,
        })
        priced.append(pick)

    if priced:
        chosen = max(
            priced,
            key=lambda pick: (
                float(pick.get("robot_score") or 0),
                float(pick.get("robot_kelly") or 0),
                float(pick.get("robust_edge") or 0),
                float(pick.get("robust_probability") or pick.get("prob") or 0),
                str(pick.get("raw_pick") or ""),
            ),
        )
        reason = "all_market_all_evidence_value"
    else:
        chosen = max(available, key=_selection_key)
        chosen.update({
            "robot_score": round(float(chosen.get("robust_probability") or chosen.get("prob") or 0), 6),
            "robot_kelly": None,
            "robot_learning_bonus": 0.0,
            "robot_learning_validated": False,
            "robot_policy_version": AUTONOMOUS_ROBOT_POLICY_VERSION,
            "robot_selection_axis": "all_markets_probability_fallback",
            "robot_price_verified": False,
            "robot_fallback": True,
        })
        reason = "no_verified_price_probability_fallback"

    chosen["recommendation_status"] = "SELECTED"
    chosen["selection_reason"] = (
        "승무패 우선 제한 없이 승무패·3방향 핸디캡·언더오버의 실제 배당과 "
        "홈·원정, 맞대결, 순위, 최근 경기력, 결장·선발, 휴식·동기 지표를 "
        "보수확률, 손익분기점, 기대수익, Kelly와 함께 독립적으로 비교했습니다."
        if reason == "all_market_all_evidence_value" else
        "검증 가능한 실배당 세트가 없어도 픽을 비우지 않고 정산 가능한 후보 중 "
        "보수확률이 가장 높은 방향을 선택했습니다."
    )
    return (chosen, reason) if return_reason else chosen


def wdl_centered_choice(picks, confidence, policy=None, return_reason=False):
    """Choose W/D/L first; cross-market probability is never a direct rank.

    Handicap and totals describe different events, so their raw probability is
    not comparable with a three-way outcome.  They may replace the W/D/L anchor
    only when no W/D/L candidate has verified price value, or when a
    chronological policy has proved a clearly larger robust edge.
    """
    available = [p for p in picks if p.get("settlement_supported", True)]
    if not available:
        raise ValueError("no settlement-compatible candidate")
    wdl = [p for p in available if (p.get("market_key") or "1x2") == "1x2"]
    alternatives = [p for p in available if (p.get("market_key") or "1x2") != "1x2"]
    wdl_priced = [p for p in wdl if price_eligible(p, confidence)]
    alt_priced = [p for p in alternatives if price_eligible(p, confidence)]
    anchor_pool = wdl_priced or wdl
    if not anchor_pool:
        pool = alt_priced or alternatives
        chosen = max(pool, key=_selection_key)
        reason = "wdl_unavailable"
        return (chosen, reason) if return_reason else chosen
    anchor = max(anchor_pool, key=_selection_key)
    if not wdl_priced and alt_priced:
        # A W/D/L direction above 50% is already more likely than the other
        # two regulation-time outcomes combined.  Do not discard that strong
        # base call merely because a composite handicap/total has a nicer
        # price.  Other markets become the fallback when the W/D/L direction
        # itself is also uncertain.
        best_alternative = max(
            alt_priced,
            key=lambda p: (
                float(p.get("robust_probability") or p.get("prob") or 0),
                float(p.get("robust_edge") or 0),
                float(p.get("robust_ev") or 0),
            ),
        )
        anchor_probability = float(
            anchor.get("robust_probability") or anchor.get("prob") or 0
        )
        alternative_probability = float(
            best_alternative.get("robust_probability")
            or best_alternative.get("prob") or 0
        )
        if anchor_probability >= .50 and anchor_probability >= alternative_probability + .03:
            reason = "wdl_probability_strong"
            return (anchor, reason) if return_reason else anchor
        chosen = max(alt_priced, key=lambda p: (
            float(p.get("robust_edge") or 0), float(p.get("robust_ev") or 0),
            float(p.get("robust_probability") or p.get("prob") or 0)))
        reason = "wdl_price_unqualified"
        return (chosen, reason) if return_reason else chosen

    policy = policy or {}
    compatible = bool(policy.get("active") and int(policy.get("validation_fixtures") or 0) >= MIN_VALIDATION)
    if compatible and wdl_priced and alt_priced:
        edge_gap = max(.01, min(.08, float(policy.get("minimum_edge_advantage") or .025)))
        ev_gap = max(0.0, min(.20, float(policy.get("minimum_ev_advantage") or .03)))
        overrides = [p for p in alt_priced
                     if float(p.get("robust_edge") or 0) >= float(anchor.get("robust_edge") or 0) + edge_gap
                     and float(p.get("robust_ev") or 0) >= float(anchor.get("robust_ev") or 0) + ev_gap]
        if overrides:
            chosen = max(overrides, key=lambda p: (
                float(p.get("robust_edge") or 0), float(p.get("robust_ev") or 0),
                float(p.get("robust_probability") or p.get("prob") or 0)))
            reason = "validated_cross_market_override"
            return (chosen, reason) if return_reason else chosen
    return (anchor, "wdl_anchor") if return_reason else anchor


def validate_price_policy(groups):
    policy = {"active": False, "maximum_probability_sacrifice": 0.0,
              "minimum_edge_advantage": None, "minimum_ev_advantage": .03,
              "reason": "승무패 우선 시장전환 검증 표본 부족",
              "validation_fixtures": 0, "method": "chronological-wdl-first-market-override-v2"}
    unique = {g["fixture"]: g for g in groups}
    ordered = sorted(unique.values(), key=lambda g: (g["kickoff"], g["fixture"]))
    if len(ordered) < MIN_TRAIN+MIN_VALIDATION:
        return policy
    boundary = ordered[max(MIN_TRAIN, int(len(ordered)*.8))]["kickoff"]
    train = [g for g in ordered if g["kickoff"] < boundary and g["known_at"] < boundary]
    validation = [g for g in ordered if g["kickoff"] >= boundary]
    if len(train) < MIN_TRAIN or len(validation) < MIN_VALIDATION:
        return policy
    def evaluate(rows, trial_policy):
        hits, profit = 0, 0.0
        for group in rows:
            pick = wdl_centered_choice(group["picks"], group["confidence"], trial_policy)
            hits += pick["outcome"]
            profit += pick["outcome"]*pick["odd"]-1
        return hits, profit/len(rows)
    baseline_policy = {"active": False}
    baseline = evaluate(train, baseline_policy)
    trials = [(gap, evaluate(train, {"active": True, "validation_fixtures": MIN_VALIDATION,
                                     "minimum_edge_advantage": gap, "minimum_ev_advantage": .03}))
              for gap in (.01, .025, .05)]
    eligible = [(gap, value) for gap, value in trials if value[0] >= baseline[0] and value[1] > baseline[1]]
    policy.update(train_fixtures=len(train), validation_fixtures=len(validation), train_before=boundary)
    if not eligible:
        policy["reason"] = "훈련 표본에서 적중 유지·가격 개선 동시 충족 없음"
        return policy
    gap, _ = max(eligible, key=lambda pair: (pair[1][1], pair[0]))
    trial_policy = {"active": True, "validation_fixtures": len(validation),
                    "minimum_edge_advantage": gap, "minimum_ev_advantage": .03}
    base, trial = evaluate(validation, baseline_policy), evaluate(validation, trial_policy)
    passed = trial[0] >= base[0] and trial[1] > base[1]
    policy.update(active=passed, minimum_edge_advantage=gap if passed else None,
                  tested_edge_advantage=gap, baseline_hits=base[0], policy_hits=trial[0],
                  baseline_roi=base[1], policy_roi=trial[1],
                  reason="시간순 검증에서 승무패 우선 대비 적중 유지·수익 개선 확인" if passed
                         else "시간순 검증 개선 미확인 · 승무패 우선 유지")
    return policy


def clean_records(fixtures, league_id, cutoff):
    records, conflicts = {}, set()
    for item in fixtures:
        try:
            fixture = item["fixture"]
            league = item["league"]
            fid, ts = int(fixture["id"]), float(fixture["timestamp"])
            home, away = int(item["teams"]["home"]["id"]), int(item["teams"]["away"]["id"])
            if (not fid or not home or not away or home == away or not math.isfinite(ts)
                    or int(league["id"]) != int(league_id) or ts >= cutoff-6*3600
                    or ts < cutoff-730*86400
                    or fixture.get("status", {}).get("short") not in {"FT", "AET", "PEN"}):
                continue
            score = (item.get("score") or {}).get("fulltime") or {}
            if score.get("home") is None or score.get("away") is None:
                if fixture["status"]["short"] != "FT":
                    continue
                score = item.get("goals") or {}
            gh, ga = float(score["home"]), float(score["away"])
            if not all(math.isfinite(x) and x.is_integer() and 0 <= x <= 20 for x in (gh, ga)):
                continue
            record = (fid, ts, home, away, int(gh), int(ga))
            if fid in records and records[fid] != record:
                conflicts.add(fid)
            records[fid] = record
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return sorted((r for fid, r in records.items() if fid not in conflicts), key=lambda r: (r[1], r[0]))


def _poisson_values(rate, limit=16):
    values = [math.exp(-rate)]
    for score in range(1, limit):
        values.append(values[-1] * rate / score)
    return values


def _rho_bounds(home_rate, away_rate):
    return (-1.0 / max(home_rate, away_rate) + 1e-9,
            min(1.0, 1.0 / (home_rate * away_rate)) - 1e-9)


def _dc_tau(home_score, away_score, home_rate, away_rate, rho):
    if home_score == 0 and away_score == 0:
        return 1.0 - home_rate * away_rate * rho
    if home_score == 1 and away_score == 0:
        return 1.0 + away_rate * rho
    if home_score == 0 and away_score == 1:
        return 1.0 + home_rate * rho
    if home_score == 1 and away_score == 1:
        return 1.0 - rho
    return 1.0


def dixon_coles_wdl(home_rate, away_rate, rho):
    hp, ap = _poisson_values(home_rate), _poisson_values(away_rate)
    masses = [0.0, 0.0, 0.0]
    total = 0.0
    for home_score, ph in enumerate(hp):
        for away_score, pa in enumerate(ap):
            value = ph * pa * _dc_tau(home_score, away_score, home_rate, away_rate, rho)
            if value <= 0:
                continue
            masses[0 if home_score > away_score else 1 if home_score == away_score else 2] += value
            total += value
    return tuple(value / total for value in masses)


def fit_rho(records, model, fallback=-.15):
    """Fit Dixon-Coles low-score dependence from cached pre-holdout scores."""
    latest = max(r[1] for r in records)
    low_score = []
    for record in records:
        if record[4] > 1 or record[5] > 1:
            continue
        rates = predict_goals(model, record[2], record[3])
        weight = 2 ** (-(latest - record[1]) / (180 * 86400))
        low_score.append((record, rates, weight))
    result = {"rho": fallback, "active": False, "low_score_samples": len(low_score),
              "method": "weighted-low-score-likelihood-grid-v1",
              "reason": "저장된 저득점 표본 부족 · 안전 기준값 유지"}
    if len(low_score) < MIN_RHO_LOW_SCORE_TRAIN:
        return result
    candidates = [index / 100 for index in range(-35, 26)]
    scored = []
    for rho in candidates:
        score = 0.0
        valid = True
        for record, rates, weight in low_score:
            lower, upper = _rho_bounds(*rates)
            if not lower < rho < upper:
                valid = False
                break
            tau = _dc_tau(record[4], record[5], rates[0], rates[1], rho)
            if tau <= 0:
                valid = False
                break
            score += weight * math.log(tau)
        if valid:
            # Weak shrinkage prevents a sparse low-score tail choosing a boundary.
            scored.append((score - 2.0 * rho * rho, rho))
    if not scored:
        result["reason"] = "허용 범위 안의 rho를 찾지 못해 안전 기준값 유지"
        return result
    rho = max(scored)[1]
    result.update(rho=rho, fitted_rho=rho, parameters_fitted=True,
                  reason="저장된 저득점 경기의 시간가중 우도로 추정")
    return result


def fit_strengths(records):
    """Regularized Poisson attack/defence, venue intercept and recency weights."""
    teams = sorted({r[i] for r in records for i in (2, 3)})
    attacks, defences = {str(t): 0.0 for t in teams}, {str(t): 0.0 for t in teams}
    latest = max(r[1] for r in records)
    weighted = [(r, 2**(-(latest-r[1])/(180*86400))) for r in records]
    weight_sum = sum(w for r, w in weighted)
    bh = math.log(max(.2, sum(r[4]*w for r, w in weighted)/weight_sum))
    ba = math.log(max(.2, sum(r[5]*w for r, w in weighted)/weight_sum))
    counts = Counter(str(r[i]) for r in records for i in (2, 3))
    ridge = 8.0  # Declared shrinkage assumption, not a fitted "optimal" constant.
    for _ in range(70):
        ag, dg = {k: ridge*v for k, v in attacks.items()}, {k: ridge*v for k, v in defences.items()}
        scale = {k: ridge for k in attacks}
        hg = ga_grad = 0.0
        for r, w in weighted:
            h, a = str(r[2]), str(r[3])
            eh = math.exp(max(-2.3, min(2.3, bh+attacks[h]+defences[a])))
            ea = math.exp(max(-2.3, min(2.3, ba+attacks[a]+defences[h])))
            rh, ra = (eh-r[4])*w, (ea-r[5])*w
            ag[h] += rh; dg[a] += rh
            ag[a] += ra; dg[h] += ra
            scale[h] += w*(eh+ea); scale[a] += w*(eh+ea)
            hg += rh; ga_grad += ra
        bh -= .15*hg/max(1, weight_sum)
        ba -= .15*ga_grad/max(1, weight_sum)
        for k in attacks:
            attacks[k] = max(-1.2, min(1.2, attacks[k]-.35*ag[k]/scale[k]))
            defences[k] = max(-1.2, min(1.2, defences[k]-.35*dg[k]/scale[k]))
    model = {"attack": attacks, "defence": defences, "home_intercept": bh,
             "away_intercept": ba, "team_samples": dict(counts), "rho": -.15}
    rho_fit = fit_rho(records, model)
    model["rho"] = float(rho_fit["rho"])
    model["rho_fit"] = rho_fit
    return model


def predict_goals(model, home, away):
    h, a = str(home), str(away)
    return tuple(max(.3, min(3.2, math.exp(
        model[venue]+model["attack"].get(team, 0)+model["defence"].get(other, 0))))
        for venue, team, other in (("home_intercept", h, a), ("away_intercept", a, h)))


def venue_baseline(records, home, away, priors):
    hr, ar = [r for r in records if r[2] == home][-20:], [r for r in records if r[3] == away][-20:]
    hgf = (sum(r[4] for r in hr)+5*priors[0])/(len(hr)+5)
    hga = (sum(r[5] for r in hr)+5*priors[1])/(len(hr)+5)
    agf = (sum(r[5] for r in ar)+5*priors[1])/(len(ar)+5)
    aga = (sum(r[4] for r in ar)+5*priors[0])/(len(ar)+5)
    return max(.3, min(3.2, hgf*aga/priors[0])), max(.3, min(3.2, agf*hga/priors[1]))


def train_challenger(records, priors, wdl_function):
    artifact = {"model_version": MODEL_VERSION, "active": False, "parameters_fitted": False,
                "samples": len(records), "validation_fixtures": 0,
                "reason": "저장된 동일 리그 종료 경기 표본 부족", "rho": -.15,
                "rho_active": False,
                "validation_scope": "chronological_holdout_vs_venue_baseline_not_live_performance"}
    if len(records) < MIN_TRAIN+MIN_VALIDATION:
        return artifact
    split = max(MIN_TRAIN, int(len(records)*.8))
    boundary = records[split][1]
    train, validation = [r for r in records if r[1] < boundary-6*3600], [r for r in records if r[1] >= boundary]
    if len(train) < MIN_TRAIN or len(validation) < MIN_VALIDATION:
        return artifact
    model = fit_strengths(train)
    learned_rho = float(model.get("rho") or -.15)
    rho_losses = {"fallback_score_log_loss": 0.0, "learned_score_log_loss": 0.0,
                  "fallback_wdl_brier": 0.0, "learned_wdl_brier": 0.0}
    for r in validation:
        rates = predict_goals(model, r[2], r[3])
        result = 0 if r[4] > r[5] else 1 if r[4] == r[5] else 2
        for label, rho in (("fallback", -.15), ("learned", learned_rho)):
            probs = dixon_coles_wdl(*rates, rho)
            rho_losses[f"{label}_wdl_brier"] += sum(
                (p - int(index == result)) ** 2 for index, p in enumerate(probs))
            hp, ap = _poisson_values(rates[0]), _poisson_values(rates[1])
            if r[4] < len(hp) and r[5] < len(ap):
                probability = hp[r[4]] * ap[r[5]] * _dc_tau(r[4], r[5], *rates, rho)
            else:
                probability = 1e-12
            rho_losses[f"{label}_score_log_loss"] -= math.log(max(1e-12, probability))
    n = len(validation)
    rho_passed = bool(
        learned_rho != -.15
        and rho_losses["learned_score_log_loss"] < rho_losses["fallback_score_log_loss"]
        and rho_losses["learned_wdl_brier"] <= rho_losses["fallback_wdl_brier"] + 1e-9
    )
    if not rho_passed:
        model["rho"] = -.15
    losses = [[0.0, 0.0], [0.0, 0.0]]
    # Fixed pre-holdout model and baseline; no validation outcomes enter either.
    for r in validation:
        result = 0 if r[4] > r[5] else 1 if r[4] == r[5] else 2
        forecasts = (predict_goals(model, r[2], r[3]), venue_baseline(train, r[2], r[3], priors))
        for i, goals in enumerate(forecasts):
            probs = dixon_coles_wdl(*goals, model["rho"] if i == 0 else -.15)
            losses[i][0] += sum((p-int(j == result))**2 for j, p in enumerate(probs))
            losses[i][1] -= math.log(max(1e-12, probs[result]))
    artifact.update(parameters_fitted=True, train_fixtures=len(train), validation_fixtures=n,
                    train_before=boundary-6*3600, available_after=max(r[1] for r in records)+6*3600,
                    fitted_brier=losses[0][0]/n, baseline_brier=losses[1][0]/n,
                    fitted_log_loss=losses[0][1]/n, baseline_log_loss=losses[1][1]/n)
    artifact.update(rho=model["rho"], rho_active=rho_passed,
                    rho_fitted_value=learned_rho,
                    rho_low_score_samples=int((model.get("rho_fit") or {}).get("low_score_samples") or 0),
                    rho_validation={key: value / n for key, value in rho_losses.items()},
                    rho_reason=("시간순 보류 표본에서 저득점 우도 개선 확인" if rho_passed
                                else "시간순 개선 미확인 · 안전 기준값 사용"))
    passed = losses[0][0] < losses[1][0] and losses[0][1] < losses[1][1]
    artifact.update(active=passed, reason="시간순 보류 표본에서 기초모형 대비 오차 감소" if passed else "시간순 검증 개선 미확인 · 기초모형 유지")
    if passed:
        final_model = fit_strengths(records)
        if not rho_passed:
            final_model["rho"] = -.15
        artifact["parameters"] = final_model
    return artifact


def _finite_number(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def build_autonomous_robot_features(
    goal_audit, context_audit, candidates, confidence, extra=None,
):
    """Freeze a numeric pre-match feature map for the autonomous learner.

    The map is deliberately wider than the current fitted model.  Feature
    coverage, usefulness and interactions are selected again at training time;
    a human-authored weight or a W/D/L-first rule is not stored here.
    """
    goal_audit = goal_audit if isinstance(goal_audit, dict) else {}
    context_audit = context_audit if isinstance(context_audit, dict) else {}
    base = context_audit.get("base_expected_goals") or goal_audit.get("expected_goals") or {}
    adjusted = context_audit.get("adjusted_expected_goals") or goal_audit.get("expected_goals") or base
    features = {
        "base_home_goals": _finite_number(base.get("home"), 1.35),
        "base_away_goals": _finite_number(base.get("away"), 1.15),
        "context_home_goals": _finite_number(adjusted.get("home"), 1.35),
        "context_away_goals": _finite_number(adjusted.get("away"), 1.15),
        "data_confidence": max(0.0, min(1.0, _finite_number(confidence))),
        "rho": max(-.75, min(.50, _finite_number(goal_audit.get("rho"), -.15))),
    }
    for side, value in (context_audit.get("side_alignment") or {}).items():
        if side in {"home", "draw", "away"}:
            features[f"alignment_{side}"] = _finite_number(value)
    for component in context_audit.get("components") or []:
        if not isinstance(component, dict):
            continue
        name = "".join(
            character if character.isalnum() else "_"
            for character in str(component.get("name") or "component").casefold()
        ).strip("_")[:48]
        if not name:
            continue
        for side in ("home", "draw", "away"):
            if component.get(side) is not None:
                features[f"context_{name}_{side}"] = _finite_number(component.get(side))

    for pick in candidates or []:
        if not isinstance(pick, dict):
            continue
        market = str(pick.get("market_key") or "")
        side = str(pick.get("selection_side") or "")
        if market not in {"1x2", "handicap", "totals"} or not side:
            continue
        prefix = f"market_{market.replace('1x2', 'wdl')}_{side}"
        features[prefix + "_odd"] = _finite_number(pick.get("odd"))
        features[prefix + "_fair"] = _finite_number(
            pick.get("fair_prob"), _finite_number(pick.get("market_prob"))
        )
        if market == "handicap":
            features["handicap_line"] = _finite_number(pick.get("handicap_base"))
        elif market == "totals":
            features["totals_line"] = _finite_number(pick.get("totals_base"), 2.5)

    for key, value in (extra or {}).items():
        if isinstance(value, bool):
            features[str(key)] = float(value)
        elif isinstance(value, (int, float)):
            features[str(key)] = _finite_number(value)
    return {
        key: round(value, 8)
        for key, value in sorted(features.items())
        if math.isfinite(value)
    }


def _robot_score_matrix(home_rate, away_rate, rho=-.15, limit=16):
    home_rate = max(.15, min(4.5, _finite_number(home_rate, 1.35)))
    away_rate = max(.15, min(4.5, _finite_number(away_rate, 1.15)))
    lower, upper = _rho_bounds(home_rate, away_rate)
    rho = max(lower, min(upper, _finite_number(rho, -.15)))
    hp, ap = _poisson_values(home_rate, limit), _poisson_values(away_rate, limit)
    matrix = []
    total = 0.0
    for home_score, ph in enumerate(hp):
        row = []
        for away_score, pa in enumerate(ap):
            value = max(
                0.0,
                ph * pa * _dc_tau(
                    home_score, away_score, home_rate, away_rate, rho
                ),
            )
            row.append(value)
            total += value
        matrix.append(row)
    if total <= 0:
        raise ValueError("robot score distribution is empty")
    return [[value / total for value in row] for row in matrix]


def _robot_market_probability(matrix, market, side, line=0.0):
    probability = 0.0
    for home_score, row in enumerate(matrix):
        for away_score, value in enumerate(row):
            if market == "1x2":
                outcome = "home" if home_score > away_score else (
                    "draw" if home_score == away_score else "away"
                )
            elif market == "handicap":
                margin = home_score + line - away_score
                outcome = "home" if margin > 1e-9 else (
                    "draw" if abs(margin) <= 1e-9 else "away"
                )
            elif market == "totals":
                total = home_score + away_score
                outcome = "under" if total < line else (
                    "over" if total > line else "push"
                )
            else:
                continue
            if outcome == side:
                probability += value
    return max(0.0, min(1.0, probability))


def _robot_expand_features(features, names, interactions):
    values = [_finite_number(features.get(name)) for name in names]
    lookup = dict(zip(names, values))
    values.extend(
        lookup.get(left, 0.0) * lookup.get(right, 0.0)
        for left, right in interactions
    )
    return values


def _robot_feature_candidates(rows):
    numeric = {}
    for row in rows:
        features = row.get("features") or {}
        for key, value in features.items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                numeric.setdefault(str(key), []).append(number)
    required = {"base_home_goals", "base_away_goals"}
    # A feature may enter from the very first clean result.  Coverage controls
    # sparse columns, but it is not a minimum learning-match gate.
    minimum_coverage = max(1, int(math.ceil(len(rows) * .50)))
    variable = []
    for key, values in numeric.items():
        if len(values) < minimum_coverage and key not in required:
            continue
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        if variance > 1e-8 or key in required:
            variable.append(key)
    return sorted(set(variable) | required)


def _robot_correlation(rows, key, target):
    pairs = []
    for row in rows:
        features = row.get("features") or {}
        if key not in features:
            continue
        x = _finite_number(features.get(key))
        y = _finite_number(row.get(target))
        pairs.append((x, y))
    if len(pairs) < 2:
        return 0.0
    mx = sum(x for x, _ in pairs) / len(pairs)
    my = sum(y for _, y in pairs) / len(pairs)
    numerator = sum((x - mx) * (y - my) for x, y in pairs)
    dx = sum((x - mx) ** 2 for x, _ in pairs)
    dy = sum((y - my) ** 2 for _, y in pairs)
    return numerator / math.sqrt(dx * dy) if dx > 0 and dy > 0 else 0.0


def _fit_robot_linear(rows, names, interactions, target, ridge, half_life_days):
    raw = [_robot_expand_features(row.get("features") or {}, names, interactions) for row in rows]
    dimension = len(raw[0]) if raw else 0
    means = [sum(vector[index] for vector in raw) / len(raw) for index in range(dimension)]
    scales = []
    for index in range(dimension):
        variance = sum((vector[index] - means[index]) ** 2 for vector in raw) / len(raw)
        scales.append(max(.05, math.sqrt(variance)))
    design = [
        [(vector[index] - means[index]) / scales[index] for index in range(dimension)]
        for vector in raw
    ]
    latest = max(_finite_number(row.get("kickoff")) for row in rows)
    sample_weights = [
        2 ** (-(latest - _finite_number(row.get("kickoff"))) / (half_life_days * 86400.0))
        for row in rows
    ]
    targets = [_finite_number(row.get(target)) for row in rows]
    intercept = sum(y * weight for y, weight in zip(targets, sample_weights)) / max(1e-9, sum(sample_weights))
    weights = [0.0] * dimension
    # The collector can run on a small EC2 instance.  A bounded optimizer keeps
    # retraining responsive while every completed sample remains in the audit DB.
    for iteration in range(160):
        grad_intercept = 0.0
        gradients = [ridge * weight for weight in weights]
        weight_sum = max(1e-9, sum(sample_weights))
        for vector, y, sample_weight in zip(design, targets, sample_weights):
            error = intercept + sum(w * x for w, x in zip(weights, vector)) - y
            grad_intercept += sample_weight * error
            for index, value in enumerate(vector):
                gradients[index] += sample_weight * error * value
        rate = .08 / math.sqrt(1.0 + iteration / 35.0)
        intercept -= rate * grad_intercept / weight_sum
        for index in range(dimension):
            weights[index] -= rate * gradients[index] / weight_sum
    return {
        "intercept": intercept, "weights": weights,
        "means": means, "scales": scales,
    }


def _predict_robot_linear(model, vector):
    standardized = [
        (value - mean) / scale
        for value, mean, scale in zip(vector, model["means"], model["scales"])
    ]
    return model["intercept"] + sum(
        weight * value for weight, value in zip(model["weights"], standardized)
    )


def _robot_rates_from_parameters(features, parameters=None):
    # Before enough results exist for promotion, use the verified full-context
    # pre-match goal prior so home/H2H/form/availability evidence is not ignored.
    # The learner then estimates its own residuals and interactions around this
    # transparent starting point; it never copies the official final W/D/L.
    base_h = max(.15, min(4.5, _finite_number(
        features.get("context_home_goals"),
        _finite_number(features.get("base_home_goals"), 1.35),
    )))
    base_a = max(.15, min(4.5, _finite_number(
        features.get("context_away_goals"),
        _finite_number(features.get("base_away_goals"), 1.15),
    )))
    if not parameters:
        return base_h, base_a
    names = parameters.get("feature_names") or []
    interactions = [tuple(pair) for pair in parameters.get("interactions") or []]
    vector = _robot_expand_features(features, names, interactions)
    residual_h = max(-1.1, min(1.1, _predict_robot_linear(parameters["home"], vector)))
    residual_a = max(-1.1, min(1.1, _predict_robot_linear(parameters["away"], vector)))
    return (
        max(.15, min(4.5, (base_h + .35) * math.exp(residual_h) - .35)),
        max(.15, min(4.5, (base_a + .35) * math.exp(residual_a) - .35)),
    )


def _robot_loss(rows, parameters=None):
    brier = log_loss = goal_mae = 0.0
    for row in rows:
        rates = _robot_rates_from_parameters(row.get("features") or {}, parameters)
        probs = dixon_coles_wdl(*rates, -.15)
        result = 0 if row["home_goals"] > row["away_goals"] else (
            1 if row["home_goals"] == row["away_goals"] else 2
        )
        brier += sum((probability - int(index == result)) ** 2 for index, probability in enumerate(probs))
        log_loss -= math.log(max(1e-12, probs[result]))
        goal_mae += (abs(rates[0] - row["home_goals"]) + abs(rates[1] - row["away_goals"])) / 2.0
    count = max(1, len(rows))
    return {
        "brier": brier / count,
        "log_loss": log_loss / count,
        "goal_mae": goal_mae / count,
    }


def _clean_robot_examples(examples):
    unique = {}
    conflicts = set()
    for example in examples or []:
        if not isinstance(example, dict):
            continue
        fixture_key = str(example.get("fixture_key") or "").strip()
        kickoff = _finite_number(example.get("kickoff"), -1)
        captured_at = _finite_number(example.get("captured_at"), kickoff + 1)
        known_at = _finite_number(example.get("known_at"), kickoff)
        home_goals = _finite_number(example.get("home_goals"), -1)
        away_goals = _finite_number(example.get("away_goals"), -1)
        features = example.get("features") or {}
        if (
            not fixture_key or kickoff <= 0 or captured_at >= kickoff
            or known_at <= kickoff or not isinstance(features, dict)
            or home_goals < 0 or away_goals < 0
            or home_goals > 20 or away_goals > 20
        ):
            continue
        row = {
            "fixture_key": fixture_key, "kickoff": kickoff,
            "features": features, "home_goals": int(home_goals),
            "away_goals": int(away_goals),
        }
        if fixture_key in unique and unique[fixture_key] != row:
            conflicts.add(fixture_key)
        unique.setdefault(fixture_key, row)
    return sorted(
        (row for key, row in unique.items() if key not in conflicts),
        key=lambda row: (row["kickoff"], row["fixture_key"]),
    )


def train_autonomous_robot(examples):
    """Update the robot from every clean completed pre-match sample.

    There is deliberately no 20/60/100-match activation gate.  The first
    result changes the residual prior by a strongly regularized non-zero
    amount; more results automatically increase the learned share and permit
    more features/interactions.  Chronological diagnostics are reported once
    two or more results exist, but never decide whether learning is allowed.
    """
    rows = _clean_robot_examples(examples)
    artifact = {
        "model_version": ROBOT_MODEL_VERSION,
        "feature_schema_version": ROBOT_FEATURE_SCHEMA_VERSION,
        "active": False,
        "samples": len(rows),
        "train_fixtures": 0,
        "validation_fixtures": 0,
        "reason": "첫 종료 경기 전 표본 대기",
        "validation_scope": "online_update_with_chronological_diagnostics",
        "history_rewrite": False,
        "uses_post_kickoff_features": False,
        "minimum_sample_gate": False,
        "learning_started_from_first_result": False,
    }
    if not rows:
        return artifact

    candidate_names = _robot_feature_candidates(rows)
    ranked_names = sorted(
        candidate_names,
        key=lambda key: max(
            abs(_robot_correlation(rows, key, "home_goals")),
            abs(_robot_correlation(rows, key, "away_goals")),
        ),
        reverse=True,
    )[:min(24, max(2, int(math.sqrt(len(rows)) * 4)))]
    for required in ("base_home_goals", "base_away_goals"):
        if required not in ranked_names:
            ranked_names.append(required)
    interaction_sources = ranked_names[:min(7, len(ranked_names))]
    all_interactions = [
        (left, right)
        for index, left in enumerate(interaction_sources)
        for right in interaction_sources[index + 1:]
    ]
    interaction_count = min(len(all_interactions), max(0, len(rows) - 2), 8)
    interactions = all_interactions[:interaction_count]
    ridge = max(.04, min(.45, .45 / math.sqrt(len(rows))))
    half_life = max(30.0, min(420.0, 60.0 * math.sqrt(len(rows))))
    # The learner's share grows continuously; even sample one has a non-zero
    # effect while a single outlier cannot fully replace the pre-match prior.
    learning_strength = len(rows) / (len(rows) + 8.0)

    def fit_parameters(source_rows, strength):
        fitted_rows = []
        for row in source_rows:
            copy = dict(row)
            base_h, base_a = _robot_rates_from_parameters(row["features"])
            copy["target_h"] = math.log(
                (row["home_goals"] + .35) / (base_h + .35)
            )
            copy["target_a"] = math.log(
                (row["away_goals"] + .35) / (base_a + .35)
            )
            fitted_rows.append(copy)
        parameters = {
            "feature_names": list(ranked_names),
            "interactions": list(interactions),
            "home": _fit_robot_linear(
                fitted_rows, ranked_names, interactions, "target_h", ridge,
                half_life,
            ),
            "away": _fit_robot_linear(
                fitted_rows, ranked_names, interactions, "target_a", ridge,
                half_life,
            ),
            "rho": -.15,
            "learning_strength": strength,
        }
        for side in ("home", "away"):
            parameters[side]["intercept"] *= strength
            parameters[side]["weights"] = [
                weight * strength for weight in parameters[side]["weights"]
            ]
        return parameters

    best_parameters = fit_parameters(rows, learning_strength)
    baseline = _robot_loss(rows)
    fitted = _robot_loss(rows, best_parameters)
    validation = []
    chronological_baseline = chronological_fitted = None
    if len(rows) >= 2:
        split = max(1, min(len(rows) - 1, int(len(rows) * .75)))
        train = rows[:split]
        validation = rows[split:]
        earlier_strength = len(train) / (len(train) + 8.0)
        chronological_parameters = fit_parameters(train, earlier_strength)
        chronological_baseline = _robot_loss(validation)
        chronological_fitted = _robot_loss(validation, chronological_parameters)

    artifact.update({
        "active": True,
        "learning_started_from_first_result": True,
        "train_fixtures": len(rows),
        "validation_fixtures": len(validation),
        "baseline_brier": round(baseline["brier"], 6),
        "fitted_brier": round(fitted["brier"], 6),
        "baseline_log_loss": round(baseline["log_loss"], 6),
        "fitted_log_loss": round(fitted["log_loss"], 6),
        "baseline_goal_mae": round(baseline["goal_mae"], 6),
        "fitted_goal_mae": round(fitted["goal_mae"], 6),
        "selected_ridge": ridge,
        "selected_half_life_days": half_life,
        "selected_interaction_count": interaction_count,
        "selected_features": list(ranked_names),
        "selected_interactions": [list(pair) for pair in best_parameters["interactions"]],
        "online_learning_strength": round(learning_strength, 6),
        "reason": (
            f"종료표본 {len(rows)}경기의 오차를 다음 경기 모형에 온라인 반영"
        ),
    })
    if chronological_baseline and chronological_fitted:
        artifact.update({
            "chronological_baseline_brier": round(chronological_baseline["brier"], 6),
            "chronological_fitted_brier": round(chronological_fitted["brier"], 6),
            "chronological_baseline_log_loss": round(chronological_baseline["log_loss"], 6),
            "chronological_fitted_log_loss": round(chronological_fitted["log_loss"], 6),
            "chronological_improved": bool(
                chronological_fitted["brier"] < chronological_baseline["brier"]
                and chronological_fitted["log_loss"] < chronological_baseline["log_loss"]
            ),
        })
    labels = ranked_names + [f"{left}×{right}" for left, right in interactions]
    importance = []
    for index, label in enumerate(labels):
        importance.append((
            abs(best_parameters["home"]["weights"][index])
            + abs(best_parameters["away"]["weights"][index]),
            label,
        ))
    artifact["feature_importance"] = [
        {"feature": label, "importance": round(value, 6)}
        for value, label in sorted(importance, reverse=True)[:15]
    ]
    artifact["parameters"] = best_parameters
    return artifact


def build_autonomous_robot_candidates(picks, features, artifact=None):
    """Calculate robot-owned probabilities for every supported market.

    Newer grading rows with frozen feature snapshots update the goal model.
    Every older honest grading row can still update probability calibration.
    Both paths start with their first available result and affect only future
    picks; no missing historical feature is fabricated.
    """
    artifact = artifact if isinstance(artifact, dict) else {}
    active = bool(artifact.get("active") and artifact.get("parameters"))
    parameters = artifact.get("parameters") if active else None
    home_rate, away_rate = _robot_rates_from_parameters(features or {}, parameters)
    rho = _finite_number((parameters or {}).get("rho"), -.15)
    matrix = _robot_score_matrix(home_rate, away_rate, rho)
    result = []
    for original in picks or []:
        if not isinstance(original, dict) or not original.get("settlement_supported", True):
            continue
        pick = dict(original)
        market = str(pick.get("market_key") or "1x2")
        side = str(pick.get("selection_side") or "")
        line = (
            _finite_number(pick.get("handicap_base")) if market == "handicap"
            else _finite_number(pick.get("totals_base"), 2.5) if market == "totals"
            else 0.0
        )
        probability = _robot_market_probability(matrix, market, side, line)
        fair = pick.get("fair_prob")
        fair = _finite_number(fair, _finite_number(pick.get("market_prob")))
        odd = _finite_number(pick.get("odd"))
        pick.update({
            "official_probability": _finite_number(pick.get("prob")),
            "prob": probability,
            "probability": probability,
            "model_probability": probability,
            "robust_probability": probability,
            "robot_probability": probability,
            "robot_expected_goals": {"home": round(home_rate, 4), "away": round(away_rate, 4)},
            "robot_model_version": ROBOT_MODEL_VERSION,
            "robot_feature_schema_version": ROBOT_FEATURE_SCHEMA_VERSION,
            "robot_model_active": active,
            "robot_training_samples": int(artifact.get("samples") or 0),
            "robot_validation_fixtures": int(artifact.get("validation_fixtures") or 0),
            "robot_learning_reason": str(artifact.get("reason") or "종료된 경기 전 표본 수집 중"),
            "robot_grading_experience_samples": int(
                artifact.get("grading_experience_samples") or 0
            ),
            "robot_edge": probability - fair if 0 < fair < 1 else 0.0,
            "robot_ev": probability * odd if odd > 1 else 0.0,
            "robust_edge": probability - fair if 0 < fair < 1 else 0.0,
            "robust_ev": probability * odd if odd > 1 else 0.0,
        })
        result.append(pick)

    # Older grading-note rows often predate the detailed robot feature schema,
    # but their frozen pre-match probability and final hit/miss are still valid
    # evidence.  Learn a market/probability-bin reliability curve with smooth
    # shrinkage.  There is no minimum-sample switch: sample one has a small,
    # non-zero effect and its influence grows naturally with repeated evidence.
    experience = artifact.get("grading_experience") or {}
    market_cells = experience.get("markets") or {}
    grouped = {}
    for pick in result:
        market = str(pick.get("market_key") or "1x2")
        line = (
            round(_finite_number(pick.get("handicap_base")), 4)
            if market == "handicap"
            else round(_finite_number(pick.get("totals_base"), 2.5), 4)
            if market == "totals"
            else 0.0
        )
        grouped.setdefault((market, line), []).append(pick)

    for (market, _line), group in grouped.items():
        adjusted = []
        for pick in group:
            base_probability = _finite_number(pick.get("robot_probability"))
            bucket = str(min(9, max(0, int(base_probability * 10))))
            cell = (market_cells.get(market) or {}).get(bucket) or {}
            samples = max(0, int(cell.get("samples") or 0))
            observed = _finite_number(cell.get("observed_rate"), base_probability)
            learning_weight = samples / (samples + 12.0) if samples else 0.0
            calibrated = (
                base_probability * (1.0 - learning_weight)
                + observed * learning_weight
            )
            adjusted.append(max(1e-9, calibrated))
            pick.update({
                "robot_pre_grading_probability": base_probability,
                "robot_grading_calibration_samples": samples,
                "robot_grading_calibration_weight": round(learning_weight, 8),
                "robot_grading_observed_rate": observed if samples else None,
                "robot_minimum_sample_gate": False,
            })
        normalization = sum(adjusted)
        if normalization <= 0:
            continue
        for pick, value in zip(group, adjusted):
            probability = value / normalization
            fair = _finite_number(
                pick.get("fair_prob"), _finite_number(pick.get("market_prob"))
            )
            odd = _finite_number(pick.get("odd"))
            pick.update({
                "prob": probability,
                "probability": probability,
                "model_probability": probability,
                "robust_probability": probability,
                "robot_probability": probability,
                "robot_edge": probability - fair if 0 < fair < 1 else 0.0,
                "robot_ev": probability * odd if odd > 1 else 0.0,
                "robust_edge": probability - fair if 0 < fair < 1 else 0.0,
                "robust_ev": probability * odd if odd > 1 else 0.0,
            })
    return result
