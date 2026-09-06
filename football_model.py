"""Offline, bounded challenger trained only on already cached final scores.

No network, database writes or dependency on the collector. A chronological
holdout must beat the venue-shrinkage baseline on BOTH WDL Brier and log loss.
This is a promotion gate, not a claim of prospective profitability.
"""
import math
from collections import Counter

MODEL_VERSION = "time-weighted-opponent-poisson-v1"
MIN_TRAIN = 160
MIN_VALIDATION = 40


def price_eligible(pick, confidence):
    market = pick.get("market_key") or "1x2"
    return bool(confidence >= .5 and pick.get("settlement_supported", True)
                and float(pick.get("odd") or 0) > 1
                and pick.get("fair_prob") is not None
                and float(pick.get("robust_ev") or 0) >= 1.01
                and (float(pick.get("prob") or 0) >= (.4 if market == "1x2" else .5)
                     or pick.get("is_qualified_underdog")))


def probability_price_choice(picks, confidence, band=0):
    """Band is a validated policy allowance, never a hard minimum price."""
    highest = max(float(p["prob"]) for p in picks)
    priced = [p for p in picks if highest-float(p["prob"]) <= band+1e-6 and price_eligible(p, confidence)]
    pool = priced or [p for p in picks if highest-float(p["prob"]) <= 1e-6]
    return max(pool, key=lambda p: (float(p["prob"]), float(p.get("robust_ev") or 0),
                                    float(p.get("robust_edge") or 0), p.get("market_key") == "1x2"))


def validate_price_policy(groups):
    policy = {"active": False, "maximum_probability_sacrifice": 0.0,
              "reason": "호환 모형의 경기 단위 가격선택 검증 표본 부족",
              "validation_fixtures": 0, "method": "chronological-price-policy-v1"}
    unique = {g["fixture"]: g for g in groups}
    ordered = sorted(unique.values(), key=lambda g: (g["kickoff"], g["fixture"]))
    if len(ordered) < MIN_TRAIN+MIN_VALIDATION:
        return policy
    boundary = ordered[max(MIN_TRAIN, int(len(ordered)*.8))]["kickoff"]
    train = [g for g in ordered if g["kickoff"] < boundary and g["known_at"] < boundary]
    validation = [g for g in ordered if g["kickoff"] >= boundary]
    if len(train) < MIN_TRAIN or len(validation) < MIN_VALIDATION:
        return policy
    def evaluate(rows, band):
        hits, profit = 0, 0.0
        for group in rows:
            pick = probability_price_choice(group["picks"], group["confidence"], band)
            hits += pick["outcome"]
            profit += pick["outcome"]*pick["odd"]-1
        return hits, profit/len(rows)
    baseline = evaluate(train, 0)
    trials = [(band, evaluate(train, band)) for band in (.01, .025, .05)]
    eligible = [(band, value) for band, value in trials if value[0] >= baseline[0] and value[1] > baseline[1]]
    policy.update(train_fixtures=len(train), validation_fixtures=len(validation), train_before=boundary)
    if not eligible:
        policy["reason"] = "훈련 표본에서 적중 유지·가격 개선 동시 충족 없음"
        return policy
    band, _ = max(eligible, key=lambda pair: (pair[1][1], -pair[0]))
    base, trial = evaluate(validation, 0), evaluate(validation, band)
    passed = trial[0] >= base[0] and trial[1] > base[1]
    policy.update(active=passed, maximum_probability_sacrifice=band if passed else 0.0,
                  tested_band=band, baseline_hits=base[0], policy_hits=trial[0],
                  baseline_roi=base[1], policy_roi=trial[1],
                  reason="시간순 검증에서 적중 유지·가격 개선 확인" if passed else "시간순 검증 개선 미확인 · 확률 희생 없음")
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
    return {"attack": attacks, "defence": defences, "home_intercept": bh,
            "away_intercept": ba, "team_samples": dict(counts), "rho": -.15}


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
                "validation_scope": "chronological_holdout_vs_venue_baseline_not_live_performance"}
    if len(records) < MIN_TRAIN+MIN_VALIDATION:
        return artifact
    split = max(MIN_TRAIN, int(len(records)*.8))
    boundary = records[split][1]
    train, validation = [r for r in records if r[1] < boundary-6*3600], [r for r in records if r[1] >= boundary]
    if len(train) < MIN_TRAIN or len(validation) < MIN_VALIDATION:
        return artifact
    model = fit_strengths(train)
    losses = [[0.0, 0.0], [0.0, 0.0]]
    # Fixed pre-holdout model and baseline; no validation outcomes enter either.
    for r in validation:
        result = 0 if r[4] > r[5] else 1 if r[4] == r[5] else 2
        forecasts = (predict_goals(model, r[2], r[3]), venue_baseline(train, r[2], r[3], priors))
        for i, goals in enumerate(forecasts):
            probs = wdl_function(*goals)[:3]
            losses[i][0] += sum((p-int(j == result))**2 for j, p in enumerate(probs))
            losses[i][1] -= math.log(max(1e-12, probs[result]))
    n = len(validation)
    artifact.update(parameters_fitted=True, train_fixtures=len(train), validation_fixtures=n,
                    train_before=boundary-6*3600, available_after=max(r[1] for r in records)+6*3600,
                    fitted_brier=losses[0][0]/n, baseline_brier=losses[1][0]/n,
                    fitted_log_loss=losses[0][1]/n, baseline_log_loss=losses[1][1]/n)
    passed = losses[0][0] < losses[1][0] and losses[0][1] < losses[1][1]
    artifact.update(active=passed, reason="시간순 보류 표본에서 기초모형 대비 오차 감소" if passed else "시간순 검증 개선 미확인 · 기초모형 유지")
    if passed:
        artifact["parameters"] = fit_strengths(records)
    return artifact
