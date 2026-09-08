"""Offline, bounded challenger trained only on already cached final scores.

No network, database writes or dependency on the collector. A chronological
holdout must beat the venue-shrinkage baseline on BOTH WDL Brier and log loss.
This is a promotion gate, not a claim of prospective profitability.
"""
import math
from collections import Counter

MODEL_VERSION = "time-weighted-opponent-dixon-coles-v2"
AUTONOMOUS_ROBOT_POLICY_VERSION = "independent-all-evidence-kelly-v2"
OFFICIAL_PICK_POLICY_VERSION = "all-evidence-best-one-v1"
MIN_TRAIN = 160
MIN_VALIDATION = 40
MIN_RHO_LOW_SCORE_TRAIN = 30


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
    verified_value_exists = any(
        float(pick.get("odd") or 0) > 1.0
        and pick.get("fair_prob") is not None
        and float(pick.get("robust_edge") or 0) > 0
        and float(
            pick.get("robust_ev")
            or float(pick.get("robust_probability") or pick.get("prob") or 0)
            * float(pick.get("odd") or 0)
        ) >= 1.0
        for pick in available
    )
    for pick in available:
        probability = max(0.0, min(1.0, float(
            pick.get("robust_probability") or pick.get("prob") or 0
        )))
        neutral = _market_neutral_probability(pick)
        conviction = max(-1.0, min(1.0, (probability - neutral) / (1.0 - neutral)))
        odd = float(pick.get("odd") or 0)
        fair = pick.get("fair_prob")
        try:
            fair = float(fair) if fair is not None else None
        except (TypeError, ValueError):
            fair = None
        priced = bool(odd > 1.0 and fair is not None and 0 < fair < 1)
        edge = float(pick.get("robust_edge") or 0) if priced else 0.0
        expected_return = float(pick.get("robust_ev") or probability * odd) if priced else 1.0
        kelly = ((probability * odd - 1.0) / max(odd - 1.0, 1e-9)) if priced else 0.0
        kelly = max(-.25, min(.50, kelly))
        edge_standardized = (
            edge / max((fair * (1.0 - fair)) ** .5, .10)
            if priced else 0.0
        )
        edge_standardized = max(-.50, min(.50, edge_standardized))
        ev_margin = max(-.50, min(.50, expected_return - 1.0)) if priced else 0.0
        price_score = (
            kelly * .50
            + edge_standardized * .25
            + ev_margin * .25
        )
        context = _evidence_alignment(pick)
        support = min(.06, int(pick.get("independent_support_count") or 0) * .012)
        if priced and verified_value_exists:
            # When at least one candidate has a verified positive conservative
            # return, compare every market on value, hit probability and full
            # pre-match context.  No market receives a hard first right.
            score = (
                probability * .34
                + conviction * .10
                + price_score * .38
                + context * .24
                + support
            )
        elif priced:
            # A mandatory answer is still required when every available price
            # is unattractive.  In that case protect hit probability instead of
            # sacrificing it merely to choose the least-bad negative return.
            score = (
                probability * .55
                + conviction * .15
                + price_score * .08
                + context * .24
                + support
            )
        else:
            score = (
                probability * .52
                + conviction * .20
                + context * .28
                + support
            )
        score *= (.78 + confidence * .22)
        pick.update({
            "official_score": round(score, 6),
            "official_conviction": round(conviction, 6),
            "official_price_score": round(price_score, 6),
            "official_kelly": round(kelly, 6) if priced else None,
            "official_standardized_edge": round(edge_standardized, 6),
            "official_context_score": round(context, 6),
            "official_price_verified": priced,
            "official_positive_value_pool": verified_value_exists,
            "official_policy_version": OFFICIAL_PICK_POLICY_VERSION,
            "selection_axis": "all_evidence_best_one",
        })

    chosen = max(
        available,
        key=lambda pick: (
            float(pick.get("official_score") or 0),
            float(pick.get("context_alignment") or 0),
            float(pick.get("robust_probability") or pick.get("prob") or 0),
            float(pick.get("robust_edge") or 0),
            str(pick.get("raw_pick") or ""),
        ),
    )
    chosen["recommendation_status"] = "SELECTED"
    chosen["selection_reason"] = (
        "승무패를 먼저 고정하지 않고 승무패·3방향 핸디캡·언더오버를 같은 "
        "확신도 척도로 비교한 뒤, 경기 전 전체 지표와 보수확률·실제 배당가치를 "
        "함께 반영해 가장 강한 한 방향을 선택했습니다."
    )
    reason = "all_evidence_best_one"
    return (chosen, reason) if return_reason else chosen


def autonomous_robot_choice(picks, confidence, return_reason=False):
    """Choose one independent pre-match pick across every supported market.

    Unlike the official W/D/L-centred selector, this challenger is allowed to
    choose a regulation-time W/D/L outcome, a three-way handicap outcome or a
    total. Cross-market comparison is based on conservative price value
    (Kelly/edge/EV), not on raw probability alone, so a two-way total does not
    win merely because it naturally has a larger percentage.

    Only already attached, pre-kickoff learning diagnostics may influence the
    tie-break. Their effect is bounded and requires a chronological calibration
    improvement. The function never changes code or old rows.
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
