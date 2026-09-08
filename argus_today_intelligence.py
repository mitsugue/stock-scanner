# -*- coding: utf-8 -*-
"""Deterministic Today forecast replay, calibration, and failed-rally facts.

The module is deliberately provider-agnostic and stdlib-only.  It never calls an
AI API, never sees owner holdings, and never treats daily short-selling turnover
as weekly credit balance or reported institutional short interest.
"""
from __future__ import annotations

import hashlib
import json
import bisect
import math
from datetime import date as dtdate
import random
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import argus_market_data_truth


SCHEMA_VERSION = "argus-today-intelligence-v1"
METHOD_VERSION = "today-replay-calibration-v3-sho-conditioned"
CALIBRATION_VERSION = "sho-conditioned-knn-v1"

# ── SHO conditioning (owner spec 2026-08-22) ────────────────────────────────
# The chart forecast is driven by SHO's thinking routine: the analog search is
# conditioned on the point-in-time market state SHO reads — two-market margin
# credit (D01 axis), VIX regime (D06 axis), and Japan-vs-US relative strength
# (D03 axis) — on top of the price-action features. Every value is joined
# with an explicit knowledge lag (a JP session close cannot see that same
# evening's US prints), and days without the full context are compared only
# against days with the same feature set — absence is never scored as a value.
SHO_FEATURE_SCALES = {
    "creditRatio": 1.2,    # 信用倍率 (long/short margin balance)
    "creditShortTn": 0.35, # two-market short margin balance, ¥tn (D01)
    "vixLevel": 8.0,       # VIX regime level (D06)
    "vixChange10": 4.5,    # VIX 10-session change (D06 momentum)
    "rs20": 0.05,          # 20-session return vs S&P500 proxy (D03)
}
PROBABILITY_ELIGIBILITY_VERSION = "probability-eligibility-v1"
MIN_EFFECTIVE_SAMPLES = 30
HORIZONS = (1, 5, 20)
UNIFORM_MULTICLASS_BRIER = 2.0 / 3.0
MAX_BRIER_DEGRADATION = 0.02
STATE_LIMITS = {"snapshots": 1024, "shortSellingHistory": 3000,
                "failedRallyOutcomes": 5000}


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _hash(value: Any, length: int = 24) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _quantile(values: Sequence[float], q: float) -> Optional[float]:
    ordered = sorted(values)
    if not ordered:
        return None
    pos = max(0.0, min(1.0, q)) * (len(ordered) - 1)
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


def normalize_bars(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return ascending, valid OHLCV rows without inventing missing values."""
    out: Dict[str, Dict[str, Any]] = {}
    for raw in rows or []:
        date = str(raw.get("date") or raw.get("Date") or "")[:10]
        open_ = _number(raw.get("open", raw.get("O")))
        high = _number(raw.get("high", raw.get("H")))
        low = _number(raw.get("low", raw.get("L")))
        close = _number(raw.get("close", raw.get("C")))
        if len(date) != 10 or min(open_ or 0, high or 0, low or 0, close or 0) <= 0:
            continue
        volume = _number(raw.get("volume", raw.get("Vo")))
        out[date] = {
            "date": date, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume,
            "source": raw.get("source") or "existing_market_data_cache",
            "availableFrom": str(raw.get("availableFrom") or date),
            "knownAt": raw.get("knownAt"),
            "observedAt": raw.get("observedAt"),
            "sourceId": raw.get("sourceId"),
            "datasetId": raw.get("datasetId"),
            "revision": int(raw.get("revision") or 0),
            "adjusted": bool(raw.get("adjusted", True)),
        }
    return [out[key] for key in sorted(out)]


def normalize_short_history(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize the JPX/J-Quants *daily turnover* series (not a balance)."""
    by_date: Dict[str, Dict[str, Any]] = {}
    for raw in rows or []:
        date = str(raw.get("date") or raw.get("Date") or "")[:10]
        sell_ex = _number(raw.get("sellingExcludingShortValue", raw.get("SellExShortVa")))
        regulated = _number(raw.get("regulatedShortValue", raw.get("ShrtWithResVa")))
        non_regulated = _number(raw.get("nonRegulatedShortValue", raw.get("ShrtNoResVa")))
        # normalize_short_history is intentionally idempotent so restored
        # durable rows can pass through the same validator as provider rows.
        if regulated is None:
            regulated = _number(raw.get("regulatedShortValue"))
        if non_regulated is None:
            non_regulated = _number(raw.get("nonRegulatedShortValue"))
        if sell_ex is None:
            total_existing = _number(raw.get("totalTradingValue"))
            short_existing = _number(raw.get("totalShortSellingValue"))
            if total_existing is not None and short_existing is not None:
                sell_ex = total_existing - short_existing
        if len(date) != 10 or sell_ex is None or regulated is None or non_regulated is None:
            continue
        total = sell_ex + regulated + non_regulated
        short_value = regulated + non_regulated
        if total <= 0:
            continue
        by_date[date] = {
            "date": date,
            "totalTradingValue": total,
            "totalShortSellingValue": short_value,
            "totalShortRatio": short_value / total * 100.0,
            "regulatedShortValue": regulated,
            "nonRegulatedShortValue": non_regulated,
            "source": raw.get("source") or "J-Quants /markets/short-ratio S33=0050",
            "publishedAt": raw.get("publishedAt") or date,
            "availableFrom": raw.get("availableFrom") or date,
            "knownAt": raw.get("knownAt"),
            "observedAt": raw.get("observedAt"),
            "sourceId": raw.get("sourceId"),
            "datasetId": raw.get("datasetId"),
            "revision": int(raw.get("revision") or 0),
            "unit": "JPY",
        }
    ordered = [by_date[key] for key in sorted(by_date)]
    ratios: List[float] = []
    for index, row in enumerate(ordered):
        ratio = float(row["totalShortRatio"])
        ratios.append(ratio)
        previous = ratios[index - 1] if index else None
        history = ratios[:index + 1]
        row["previousDayDifference"] = None if previous is None else ratio - previous
        row["average5"] = _mean(history[-5:]) if len(history) >= 5 else None
        row["average20"] = _mean(history[-20:]) if len(history) >= 20 else None
        row["rollingPercentile"] = (100.0 * sum(1 for value in history[-1300:] if value <= ratio)
                                    / len(history[-1300:]))
        for key in ("totalShortRatio", "previousDayDifference", "average5",
                    "average20", "rollingPercentile"):
            row[key] = _round(row.get(key), 3)
    return ordered


def short_selling_summary(rows: Iterable[Dict[str, Any]], as_of: Optional[str] = None) -> Dict[str, Any]:
    history = normalize_short_history(rows)
    if as_of:
        history = [row for row in history if row["date"] <= as_of[:10]]
    if not history:
        return {
            "schemaVersion": "argus-daily-short-selling-v1",
            "status": "missing", "latest": None, "historyStart": None,
            "historyCount": 0, "missingReason": "daily_short_ratio_unavailable",
            "seriesType": "daily_short_selling_turnover",
        }
    latest = dict(history[-1])
    return {
        "schemaVersion": "argus-daily-short-selling-v1",
        "status": "live", "latest": latest,
        "historyStart": history[0]["date"], "historyCount": len(history),
        "latestDate": latest["date"], "publicationTiming": "JPX after daily close",
        "freshness": "close", "coverage": "TSE auction-market aggregate S33=0050",
        "seriesType": "daily_short_selling_turnover",
        "weeklyCreditShortIsSeparate": True,
        "institutionalShortIsSeparate": True,
        "missingReason": None,
    }


def _feature(bars: Sequence[Dict[str, Any]], index: int) -> Optional[Dict[str, float]]:
    if index < 24:
        return None
    close = float(bars[index]["close"])
    closes20 = [float(row["close"]) for row in bars[index - 19:index + 1]]
    ma20 = sum(closes20) / 20
    tr: List[float] = []
    for pos in range(index - 13, index + 1):
        prev_close = float(bars[pos - 1]["close"]) if pos else float(bars[pos]["close"])
        row = bars[pos]
        tr.append(max(float(row["high"]) - float(row["low"]),
                      abs(float(row["high"]) - prev_close),
                      abs(float(row["low"]) - prev_close)))
    atr_pct = (sum(tr) / len(tr)) / close
    high = float(bars[index]["high"])
    low = float(bars[index]["low"])
    location = (close - low) / (high - low) if high > low else .5
    momentum5 = close / float(bars[index - 5]["close"]) - 1
    trend = close / ma20 - 1
    volume_values = [_number(row.get("volume")) for row in bars[index - 19:index + 1]]
    clean_volume = [value for value in volume_values if value is not None and value > 0]
    volume_ratio = 1.0
    current_volume = _number(bars[index].get("volume"))
    if current_volume and clean_volume:
        volume_ratio = current_volume / (sum(clean_volume) / len(clean_volume))
    return {"trend20": trend, "momentum5": momentum5, "atrPct": atr_pct,
            "closeLocation": location, "volumeRatio": volume_ratio}


def _signal_family(feature: Dict[str, float]) -> str:
    if feature["trend20"] >= .01 and feature["momentum5"] >= 0:
        base = "trend_up"
    elif feature["trend20"] <= -.01 and feature["momentum5"] <= 0:
        base = "trend_down"
    else:
        base = "range"
    # SHO reads the same price shape differently under heavy vs light credit
    # (信用倍率) — the analog pool splits on that regime when it is known.
    ratio = feature.get("creditRatio")
    if isinstance(ratio, (int, float)):
        band = ("credit_heavy" if ratio >= 5.0
                else "credit_light" if ratio <= 2.5 else "credit_mid")
        return f"{base}|{band}"
    return base


def _distance(left: Dict[str, float], right: Dict[str, float]) -> float:
    scales = {"trend20": .05, "momentum5": .04, "atrPct": .015,
              "closeLocation": .5, "volumeRatio": 1.0}
    total = sum(((left[key] - right[key]) / scales[key]) ** 2
                for key in scales)
    # SHO dims participate only when BOTH days actually knew the value; the
    # candidate pool is already restricted to matching feature sets, so this
    # never silently compares a known value against an absent one.
    for key, scale in SHO_FEATURE_SCALES.items():
        if key in left and key in right:
            total += ((left[key] - right[key]) / scale) ** 2
    return math.sqrt(total)


def _sho_daily_features(bars: Sequence[Dict[str, Any]],
                        sho_context: Optional[Mapping[str, Any]],
                        market: Optional[str]) -> List[Optional[Dict[str, float]]]:
    """Point-in-time SHO state per bar.

    Knowledge lags are explicit: a JP session close (15:30 JST) happens before
    that calendar day's US session, so JP analogs may only use VIX / S&P
    values from strictly EARLIER dates; US symbols close together with those
    prints and may use same-date values. Credit balances use their published
    availableFrom. A day missing a value simply lacks that key.
    """
    if not sho_context or not bars:
        return [None] * len(bars)
    us_lag_exclusive = (market == "JP")

    credit: List[Tuple[str, float, float]] = []   # (availableFrom, short, long)
    by_period: Dict[str, Dict[str, float]] = {}
    for row in sho_context.get("creditRows") or []:
        if not isinstance(row, Mapping):
            continue
        series = str(row.get("seriesId") or "")
        value = _number(row.get("value"))
        available = str(row.get("availableFrom") or "")[:10]
        period = str(row.get("periodEnd") or "")[:10]
        if value is None or len(available) != 10 or len(period) != 10:
            continue
        bucket = by_period.setdefault(period, {"availableFrom": available})
        if available > bucket["availableFrom"]:
            bucket["availableFrom"] = available
        if series == "credit.short_balance":
            bucket["short"] = value
        elif series == "credit.long_balance":
            bucket["long"] = value
    for period in sorted(by_period):
        bucket = by_period[period]
        if "short" in bucket and "long" in bucket and bucket["short"] > 0:
            credit.append((str(bucket["availableFrom"]),
                           float(bucket["short"]), float(bucket["long"])))
    credit.sort(key=lambda item: item[0])

    vix: List[Tuple[str, float]] = []
    for row in sho_context.get("vixRows") or []:
        if not isinstance(row, Mapping):
            continue
        date = str(row.get("date") or "")[:10]
        value = _number(row.get("value"))
        if len(date) == 10 and value is not None and value > 0:
            vix.append((date, float(value)))
    vix.sort(key=lambda item: item[0])
    vix_dates = [item[0] for item in vix]

    us_closes: List[Tuple[str, float]] = []
    for row in normalize_bars(list(sho_context.get("usRows") or [])):
        us_closes.append((str(row["date"])[:10], float(row["close"])))
    us_dates = [item[0] for item in us_closes]

    def latest_at(dates: List[str], limit: str, *, exclusive: bool) -> int:
        index = bisect.bisect_left(dates, limit) if exclusive \
            else bisect.bisect_right(dates, limit)
        return index - 1

    def _within(newer: str, older: str, max_days: int) -> bool:
        try:
            gap = (dtdate.fromisoformat(newer) - dtdate.fromisoformat(older)).days
        except ValueError:
            return False
        return 0 <= gap <= max_days

    out: List[Optional[Dict[str, float]]] = []
    credit_dates = [item[0] for item in credit]
    for index, bar in enumerate(bars):
        date = str(bar["date"])[:10]
        features: Dict[str, float] = {}
        credit_pos = bisect.bisect_right(credit_dates, date) - 1
        # A weekly balance is CURRENT state for ~a publication cycle only —
        # an old print never silently impersonates today's credit regime.
        if credit_pos >= 0 and _within(date, credit[credit_pos][0], CREDIT_JOIN_MAX_DAYS):
            _, short_balance, long_balance = credit[credit_pos]
            features["creditRatio"] = long_balance / short_balance
            features["creditShortTn"] = short_balance / 1e12
        vix_pos = latest_at(vix_dates, date, exclusive=us_lag_exclusive)
        if vix_pos >= 10 and _within(date, vix[vix_pos][0], 10):
            features["vixLevel"] = vix[vix_pos][1]
            features["vixChange10"] = vix[vix_pos][1] - vix[vix_pos - 10][1]
        us_pos = latest_at(us_dates, date, exclusive=us_lag_exclusive)
        if us_pos >= 20 and index >= 20 \
                and _within(date, us_closes[us_pos][0], 10):
            own_return = float(bar["close"]) / float(bars[index - 20]["close"]) - 1
            us_return = us_closes[us_pos][1] / us_closes[us_pos - 20][1] - 1
            features["rs20"] = own_return - us_return
        out.append(features or None)
    return out


def _direction(return_pct: float, atr_pct: float, horizon: int) -> str:
    threshold = max(.003, atr_pct * math.sqrt(horizon) * .35)
    return "UP" if return_pct > threshold else "DOWN" if return_pct < -threshold else "RANGE"


def _episodes(candidates: Sequence[Dict[str, Any]], cooldown: int) -> List[Dict[str, Any]]:
    """Keep one best occurrence per family and non-overlapping trading window."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        family = str(candidate.get("family") or candidate.get("state") or "default")
        grouped.setdefault(family, []).append(candidate)
    selected: List[Dict[str, Any]] = []
    for family_rows in grouped.values():
        family_selected: List[Dict[str, Any]] = []
        for candidate in sorted(family_rows, key=lambda row: (row["index"], row["distance"])):
            if family_selected and candidate["index"] - family_selected[-1]["index"] <= cooldown:
                if candidate["distance"] < family_selected[-1]["distance"]:
                    family_selected[-1] = candidate
                continue
            family_selected.append(candidate)
        selected.extend(family_selected)
    return sorted(selected, key=lambda row: (row["index"], row.get("distance", 0.0)))


def _integer_probabilities(values: Dict[str, float]) -> Dict[str, int]:
    raw = {key: max(0.0, value) * 100 for key, value in values.items()}
    base = {key: int(math.floor(value)) for key, value in raw.items()}
    remaining = 100 - sum(base.values())
    order = sorted(raw, key=lambda key: (raw[key] - base[key], key), reverse=True)
    for key in order[:remaining]:
        base[key] += 1
    return base


def _walk_forward_brier(
    episodes: Sequence[Dict[str, Any]],
    baseline_episodes: Sequence[Dict[str, Any]],
    seed: str,
) -> Dict[str, Any]:
    """Compare the episode model with a strictly past-only climatology.

    Both predictions are made before the row's label is observed.  The model
    learns from prior similar, cooldown-separated episodes; the baseline learns
    from every prior cooldown-separated market episode.  This preserves an
    apples-to-apples out-of-sample Brier Skill Score.
    """
    labels = ("UP", "RANGE", "DOWN")
    model_counts = {label: 0 for label in labels}
    baseline_counts = {label: 0 for label in labels}
    score_pairs: List[Tuple[float, float]] = []
    confidence_rows: List[Tuple[float, bool]] = []
    baseline_pos = 0
    ordered_baseline = sorted(baseline_episodes, key=lambda row: row["index"])
    prior_weight = 12.0
    prior = 1.0 / len(labels)
    for model_pos, episode in enumerate(sorted(episodes, key=lambda row: row["index"])):
        while (baseline_pos < len(ordered_baseline) and
               ordered_baseline[baseline_pos]["index"] < episode["index"]):
            baseline_counts[ordered_baseline[baseline_pos]["direction"]] += 1
            baseline_pos += 1
        baseline_n = sum(baseline_counts.values())
        if model_pos >= 10 and baseline_n >= 10:
            model_denominator = model_pos + prior_weight
            baseline_denominator = baseline_n + prior_weight
            prediction = {
                label: (model_counts[label] + prior_weight * prior) / model_denominator
                for label in labels
            }
            baseline_prediction = {
                label: (baseline_counts[label] + prior_weight * prior) / baseline_denominator
                for label in labels
            }
            actual = episode["direction"]
            model_score = sum(
                (prediction[label] - (1.0 if label == actual else 0.0)) ** 2
                for label in labels
            )
            baseline_score = sum(
                (baseline_prediction[label] - (1.0 if label == actual else 0.0)) ** 2
                for label in labels
            )
            score_pairs.append((model_score, baseline_score))
            predicted = max(labels, key=lambda label: prediction[label])
            confidence_rows.append((prediction[predicted], predicted == actual))
        model_counts[episode["direction"]] += 1

    model_brier = _mean([pair[0] for pair in score_pairs])
    baseline_brier = _mean([pair[1] for pair in score_pairs])
    skill = (None if model_brier is None or baseline_brier is None or baseline_brier <= 0
             else 1.0 - model_brier / baseline_brier)
    calibration_error = None
    if confidence_rows:
        weighted_error = 0.0
        for low in (0.0, .2, .4, .6, .8):
            bucket = [row for row in confidence_rows if low <= row[0] < low + .2 or
                      (low == .8 and row[0] == 1.0)]
            if bucket:
                avg_confidence = sum(row[0] for row in bucket) / len(bucket)
                accuracy = sum(1 for row in bucket if row[1]) / len(bucket)
                weighted_error += len(bucket) / len(confidence_rows) * abs(avg_confidence - accuracy)
        calibration_error = weighted_error

    skill_samples: List[float] = []
    if score_pairs and baseline_brier and baseline_brier > 0:
        rng = random.Random(seed)
        for _ in range(500):
            sample = [score_pairs[rng.randrange(len(score_pairs))] for _ in score_pairs]
            sample_model = sum(pair[0] for pair in sample) / len(sample)
            sample_baseline = sum(pair[1] for pair in sample) / len(sample)
            if sample_baseline > 0:
                skill_samples.append(1.0 - sample_model / sample_baseline)
    return {
        "modelBrier": model_brier,
        "baselineBrier": baseline_brier,
        "brierSkill": skill,
        "calibrationError": calibration_error,
        "evaluationSampleCount": len(score_pairs),
        "confidenceInterval": ({
            "low": _quantile(skill_samples, .025),
            "high": _quantile(skill_samples, .975),
        } if skill_samples else None),
    }


def _confidence_interval(probability: float, effective_n: int) -> Dict[str, float]:
    # Normal approximation over a shrunk posterior.  Stored as a quality range,
    # not presented as a guaranteed market interval.
    n = effective_n + 12
    delta = 1.96 * math.sqrt(max(0.0, probability * (1 - probability) / n))
    return {"low": round(max(0.0, probability - delta) * 100, 1),
            "high": round(min(1.0, probability + delta) * 100, 1)}


def probability_eligibility(
    calibration: Dict[str, Any],
    probabilities: Optional[Dict[str, int]] = None,
    *,
    evaluated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the sole server-side contract for publishing direction percentages."""
    candidate = probabilities if probabilities is not None else (
        calibration.get("directionProbabilities") or calibration.get("probabilities")
    )
    candidate_values = (
        [_number(candidate.get(key)) for key in ("UP", "RANGE", "DOWN")]
        if isinstance(candidate, dict) else []
    )
    probability_sum = (
        sum(value for value in candidate_values if value is not None)
        if len(candidate_values) == 3 and all(value is not None for value in candidate_values)
        else None
    )
    effective_sample_number = _number(calibration.get("effectiveSampleCount"))
    effective_sample = max(0, int(effective_sample_number or 0))
    model_brier = _number(calibration.get("modelBrier"))
    baseline_brier = _number(calibration.get("baselineBrier"))
    brier_skill = _number(calibration.get("brierSkill"))
    integrity = str(calibration.get("calibrationIntegrity") or "UNKNOWN")
    no_future_leakage = calibration.get("noFutureLeakage") is True
    reasons: List[str] = []
    if effective_sample < MIN_EFFECTIVE_SAMPLES:
        reasons.append("effective_sample_below_30")
    if brier_skill is None or brier_skill <= 0:
        reasons.append("brier_skill_non_positive")
    if model_brier is None or baseline_brier is None or model_brier >= baseline_brier:
        reasons.append("model_not_better_than_baseline")
    if integrity != "PASS":
        reasons.append("calibration_integrity_failed")
    if probability_sum != 100:
        reasons.append("probability_sum_not_100")
    if not no_future_leakage:
        reasons.append("future_leakage_not_excluded")
    return {
        "eligible": not reasons,
        "reasonCodes": reasons,
        "effectiveSample": effective_sample,
        "modelBrier": _round(model_brier, 4),
        "baselineBrier": _round(baseline_brier, 4),
        "brierSkill": _round(brier_skill, 4),
        "calibrationIntegrity": integrity,
        "probabilitySum": probability_sum,
        "calibrationVersion": calibration.get("calibrationVersion") or CALIBRATION_VERSION,
        "datasetHash": calibration.get("calibrationDatasetHash"),
        "evaluatedAt": evaluated_at or calibration.get("calibrationDatasetFixedAt"),
        "contractVersion": PROBABILITY_ELIGIBILITY_VERSION,
    }


def _probability_truth_evidence(*, eligible: bool,
                                oos_effective_n: Optional[int],
                                rule_effective_n: Optional[int],
                                model_brier: Optional[float],
                                baseline_brier: Optional[float],
                                calibration_error: Optional[float],
                                probabilities: Optional[Mapping[str, Any]],
                                ) -> Dict[str, Any]:
    """Honest display-gate evidence (probability-truth contract v1).

    Every field is either measured here or explicitly None — nothing is
    fabricated to pass a gate. Independent holdout windows and a momentum
    baseline do not exist yet, so they are reported absent. breadthLag /
    partition integrity are injected by the serving layer, which is the only
    place that can actually measure them (this engine has no ledger access).
    """
    top_share: Optional[float] = None
    if isinstance(probabilities, Mapping) and probabilities:
        try:
            top_share = max(float(v) for v in probabilities.values()) / 100.0
        except (TypeError, ValueError):
            top_share = None
    oos = oos_effective_n if isinstance(oos_effective_n, int) else None
    wilson: Optional[float] = None
    if top_share is not None and isinstance(oos, int) and oos > 0:
        z = 1.96
        half = z * math.sqrt(top_share * (1.0 - top_share) / oos
                             + z * z / (4.0 * oos * oos))
        wilson = round(half / (1.0 + z * z / oos) * 100.0, 2)
    beats: Optional[bool] = None
    if isinstance(model_brier, float) and isinstance(baseline_brier, float):
        beats = model_brier < baseline_brier
    return {
        "serverEligible": bool(eligible),
        "oosEffectiveN": oos,
        "ruleEffectiveN": (rule_effective_n
                           if isinstance(rule_effective_n, int) else None),
        "holdouts": [],
        "beatsUnconditional": beats,
        "beatsMomentum": None,
        "wilsonHalfWidthPt": wilson,
        "ece": (round(calibration_error, 4)
                if isinstance(calibration_error, float) else None),
        "breadthLagTradingDays": None,
        "unresolvedPartitionCount": None,
        "duplicateCount": None,
    }


def _insufficient_calibration(horizon: int) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "horizon": horizon, "calibrationStatus": "insufficient_history",
        "rawOccurrenceCount": 0, "episodeCount": 0, "effectiveSampleCount": 0,
        "probabilities": None, "directionProbabilities": None,
        "referenceDirectionProbabilities": None,
        "modelBrier": None, "baselineBrier": None, "brierSkill": None,
        "calibrationIntegrity": "UNKNOWN", "noFutureLeakage": True,
        "calibrationVersion": CALIBRATION_VERSION,
        "calibrationDatasetHash": None,
    }
    row["probabilityEligibility"] = probability_eligibility(row)
    row["probabilityTruthEvidence"] = _probability_truth_evidence(
        eligible=False, oos_effective_n=0, rule_effective_n=0,
        model_brier=None, baseline_brier=None, calibration_error=None,
        probabilities=None)
    return row


def calibrate_horizon(bars: Sequence[Dict[str, Any]], horizon: int,
                      sho_daily: Optional[Sequence[Optional[Dict[str, float]]]] = None,
                      ) -> Dict[str, Any]:
    normalized = normalize_bars(bars)
    if len(normalized) < 80 + horizon:
        return _insufficient_calibration(horizon)
    daily = list(sho_daily) if sho_daily is not None else [None] * len(normalized)
    if len(daily) != len(normalized):
        daily = [None] * len(normalized)

    def merged_feature(index: int) -> Optional[Dict[str, float]]:
        feature = _feature(normalized, index)
        if feature is None:
            return None
        extra = daily[index]
        if isinstance(extra, dict):
            feature = {**feature, **extra}
        return feature

    current_feature = merged_feature(len(normalized) - 1)
    if current_feature is None:
        return _insufficient_calibration(horizon)
    # SHO conditioning: a day is comparable only against days that KNEW the
    # same state dimensions — absence is a different situation, not a zero.
    current_sho_keys = frozenset(
        key for key in SHO_FEATURE_SCALES if key in current_feature)
    family = _signal_family(current_feature)
    all_rows: List[Dict[str, Any]] = []
    sho_covered = 0
    for index in range(24, len(normalized) - horizon):
        feature = merged_feature(index)
        if feature is None:
            continue
        row_sho_keys = frozenset(
            key for key in SHO_FEATURE_SCALES if key in feature)
        if row_sho_keys >= current_sho_keys:
            if current_sho_keys:
                sho_covered += 1
            comparable = {key: value for key, value in feature.items()
                          if key not in SHO_FEATURE_SCALES
                          or key in current_sho_keys}
        else:
            continue
        start = float(normalized[index]["close"])
        end = float(normalized[index + horizon]["close"])
        future = normalized[index + 1:index + horizon + 1]
        high_return = max(float(row["high"]) for row in future) / start - 1
        low_return = min(float(row["low"]) for row in future) / start - 1
        final_return = end / start - 1
        all_rows.append({
            "index": index, "date": normalized[index]["date"],
            "distance": _distance(comparable, current_feature),
            "family": _signal_family(feature), "atrPct": feature["atrPct"],
            "return": final_return, "mfe": high_return, "mae": low_return,
            "direction": _direction(final_return, feature["atrPct"], horizon),
        })
    if len(all_rows) < 80:
        return _insufficient_calibration(horizon)
    family_rows = [row for row in all_rows if row["family"] == family]
    pool = family_rows if len(family_rows) >= 60 else all_rows
    nearest = sorted(pool, key=lambda row: (row["distance"], row["date"]))[:240]
    episodes = _episodes(nearest, max(1, horizon))
    labels = ("UP", "RANGE", "DOWN")
    base_candidates = _episodes(all_rows, max(1, horizon))
    base_counts = {label: sum(1 for row in base_candidates if row["direction"] == label)
                   for label in labels}
    base_total = max(1, sum(base_counts.values()))
    base = {label: base_counts[label] / base_total for label in labels}
    counts = {label: sum(1 for row in episodes if row["direction"] == label) for label in labels}
    n = len(episodes)
    prior_weight = 12.0
    posterior = {label: (counts[label] + prior_weight * base[label]) / (n + prior_weight)
                 for label in labels}
    probabilities = _integer_probabilities(posterior)
    dataset_hash = _hash([
        (row["date"], row["open"], row["high"], row["low"], row["close"], row.get("volume"))
        for row in normalized
    ], 32)
    brier = _walk_forward_brier(episodes, base_candidates, dataset_hash)
    model_brier = brier["modelBrier"]
    baseline_brier = brier["baselineBrier"]
    brier_skill = brier["brierSkill"]
    status = ("insufficient_sample" if n < MIN_EFFECTIVE_SAMPLES else
              "poor_calibration" if model_brier is None or baseline_brier is None or
              brier_skill is None or brier_skill <= 0 else
              "calibrated")
    returns = [row["return"] for row in episodes]
    mfes = [row["mfe"] for row in episodes]
    maes = [row["mae"] for row in episodes]
    q10, q25, q50, q75, q90 = (_quantile(returns, q) for q in (.10, .25, .50, .75, .90))
    upper_touch = (sum(1 for row in episodes if q75 is not None and row["mfe"] >= q75) / n
                   if n else None)
    lower_touch = (sum(1 for row in episodes if q25 is not None and row["mae"] <= q25) / n
                   if n else None)
    close_in_band = (sum(1 for row in episodes if q25 is not None and q75 is not None
                         and q25 <= row["return"] <= q75) / n if n else None)
    invalidation_touch = (sum(1 for row in episodes if q10 is not None and row["mae"] <= q10) / n
                          if n else None)
    ci = {label: _confidence_interval(posterior[label], n) for label in labels}
    positive_returns = [value for value in returns if value > 0]
    negative_returns = [value for value in returns if value < 0]
    expected_upside = _mean(positive_returns)
    expected_downside = abs(_mean(negative_returns) or 0.0) if negative_returns else None
    reward_risk = (expected_upside / expected_downside
                   if expected_upside is not None and expected_downside else None)
    result = {
        "horizon": horizon, "signalFamily": family,
        "rawOccurrenceCount": len(pool), "episodeCount": n,
        "effectiveSampleCount": n, "cooldownTradingDays": max(1, horizon),
        "calibrationStatus": status,
        "unroundedProbabilities": ({key: round(value * 100, 3) for key, value in posterior.items()}
                                   if status == "calibrated" else None),
        "baseRates": {key: round(value * 100, 2) for key, value in base.items()},
        "brierScore": _round(model_brier, 4),
        "modelBrier": _round(model_brier, 4),
        "baselineBrier": _round(baseline_brier, 4),
        "baseRateBrierScore": _round(baseline_brier, 4),
        "brierSkill": _round(brier_skill, 4),
        "brierSkillConfidenceInterval": ({
            "low": _round(brier["confidenceInterval"]["low"], 4),
            "high": _round(brier["confidenceInterval"]["high"], 4),
        } if brier["confidenceInterval"] else None),
        "calibrationError": _round(brier["calibrationError"], 4),
        "evaluationSampleCount": brier["evaluationSampleCount"],
        "uniformBaselineBrierScore": _round(UNIFORM_MULTICLASS_BRIER, 4),
        "brierGateMaximum": _round(UNIFORM_MULTICLASS_BRIER + MAX_BRIER_DEGRADATION, 4),
        "confidenceInterval": ci if status == "calibrated" else None,
        "noFutureLeakage": True, "walkForward": True,
        "calibrationIntegrity": "PASS",
        "calibrationDatasetFixedAt": normalized[-1]["date"],
        "calibrationDatasetHash": dataset_hash,
        "calibrationVersion": CALIBRATION_VERSION,
        "methodVersion": METHOD_VERSION,
        "returnDistribution": {
            "q10": _round(q10), "q25": _round(q25), "median": _round(q50),
            "q75": _round(q75), "q90": _round(q90),
            "meanMfe": _round(_mean(mfes)), "meanMae": _round(_mean(maes)),
        },
        "expectedValue": {
            "horizon": horizon,
            "expectedReturn": _round(_mean(returns)),
            "medianReturn": _round(q50),
            "q10": _round(q10),
            "q90": _round(q90),
            "expectedUpside": _round(expected_upside),
            "expectedDownside": _round(expected_downside),
            "rewardRisk": _round(reward_risk),
        },
        "levelProbabilities": ({
            "upperTargetTouch": round(upper_touch * 100, 1) if upper_touch is not None else None,
            "baseRangeClose": round(close_in_band * 100, 1) if close_in_band is not None else None,
            "lowerTargetTouch": round(lower_touch * 100, 1) if lower_touch is not None else None,
            "invalidationTouch": round(invalidation_touch * 100, 1) if invalidation_touch is not None else None,
        } if status == "calibrated" else None),
        # Compatibility alias for Market Context consumers during the v13.1.1
        # transition. Today only renders directionProbabilities.
        "targetProbabilities": ({
            "upperTargetTouch": round(upper_touch * 100, 1) if upper_touch is not None else None,
            "baseRangeClose": round(close_in_band * 100, 1) if close_in_band is not None else None,
            "lowerTargetTouch": round(lower_touch * 100, 1) if lower_touch is not None else None,
            "invalidationTouch": round(invalidation_touch * 100, 1) if invalidation_touch is not None else None,
        } if status == "calibrated" else None),
        "averageReactionDelay": _round(_mean([
            next((day for day in range(1, horizon + 1)
                  if ((normalized[row["index"] + day]["close"] /
                       normalized[row["index"]]["close"] - 1) > 0) ==
                  (row["return"] > 0)), horizon)
            for row in episodes
        ]), 2),
    }
    eligibility = probability_eligibility(
        result, probabilities,
        evaluated_at=f"{normalized[-1]['date']}T00:00:00Z",
    )
    visible = probabilities if eligibility["eligible"] else None
    result["probabilities"] = visible
    result["directionProbabilities"] = visible
    # Preserve a useful but explicitly non-authoritative distribution when the
    # strict publication gate is not yet met.  Consumers must label this field
    # as reference/unverified; it never promotes to the verified contract.
    result["referenceDirectionProbabilities"] = (
        None if eligibility["eligible"] else probabilities
    )
    result["probabilityEligibility"] = eligibility
    result["probabilityTruthEvidence"] = _probability_truth_evidence(
        eligible=bool(eligibility.get("eligible")),
        oos_effective_n=brier["evaluationSampleCount"],
        rule_effective_n=n,
        model_brier=model_brier, baseline_brier=baseline_brier,
        calibration_error=brier["calibrationError"],
        probabilities=probabilities)
    return result


CREDIT_JOIN_MAX_DAYS = 45
VIX_JOIN_MAX_DAYS = 10


def sho_input_freshness(sho_context: Optional[Mapping[str, Any]],
                        as_of_date: str, market: Optional[str]) -> Dict[str, Any]:
    """v13.5.65 (stabilization item 5): what the latest bar could join, and
    why not. Per input: the newest period whose availability precedes the bar,
    whether it is inside the join window, and one of
    joined / stale_beyond_window / not_yet_available / no_rows /
    not_applicable — so the screen can tell an update interval from a fetch
    failure from a market where the input does not exist."""
    date = str(as_of_date or "")[:10]
    out: Dict[str, Any] = {}
    if not sho_context:
        return {"credit": {"status": "no_rows"}, "vix": {"status": "no_rows"},
                "us": {"status": "no_rows"}}
    # credit (weekly, JP only)
    if market != "JP":
        out["credit"] = {"status": "not_applicable"}
    else:
        periods: Dict[str, Dict[str, Any]] = {}
        for row in sho_context.get("creditRows") or []:
            if not isinstance(row, Mapping):
                continue
            period = str(row.get("periodEnd") or "")[:10]
            available = str(row.get("availableFrom") or "")[:10]
            series = str(row.get("seriesId") or "")
            if len(period) != 10 or len(available) != 10 or _number(row.get("value")) is None:
                continue
            bucket = periods.setdefault(period, {"availableFrom": available, "series": set()})
            bucket["availableFrom"] = max(bucket["availableFrom"], available)
            bucket["series"].add(series)
        complete = {p: b for p, b in periods.items()
                    if {"credit.short_balance", "credit.long_balance"} <= b["series"]}
        if not complete:
            out["credit"] = {"status": "no_rows"}
        else:
            newest = max(complete)
            usable = [p for p, b in complete.items() if b["availableFrom"] <= date]
            if not usable:
                out["credit"] = {"status": "not_yet_available", "newestPeriodEnd": newest,
                                 "availableFrom": complete[newest]["availableFrom"]}
            else:
                period = max(usable)
                try:
                    gap = (dtdate.fromisoformat(date) - dtdate.fromisoformat(period)).days
                except ValueError:
                    gap = None
                joined = gap is not None and 0 <= gap <= CREDIT_JOIN_MAX_DAYS
                out["credit"] = {"status": "joined" if joined else "stale_beyond_window",
                                 "periodEnd": period,
                                 "availableFrom": complete[period]["availableFrom"],
                                 "ageDays": gap, "maxDays": CREDIT_JOIN_MAX_DAYS,
                                 "newestPeriodEnd": newest}
    # vix (daily)
    vix_dates = sorted(str(r.get("date") or "")[:10] for r in (sho_context.get("vixRows") or [])
                       if isinstance(r, Mapping) and _number(r.get("value")) is not None)
    if not vix_dates:
        out["vix"] = {"status": "no_rows"}
    else:
        limit = [d for d in vix_dates if (d < date if market == "JP" else d <= date)]
        if not limit:
            out["vix"] = {"status": "not_yet_available", "newestDate": vix_dates[-1]}
        else:
            used = limit[-1]
            try:
                gap = (dtdate.fromisoformat(date) - dtdate.fromisoformat(used)).days
            except ValueError:
                gap = None
            joined = gap is not None and 0 <= gap <= VIX_JOIN_MAX_DAYS
            out["vix"] = {"status": "joined" if joined else "stale_beyond_window",
                          "date": used, "ageDays": gap, "maxDays": VIX_JOIN_MAX_DAYS}
    # us comparison closes (daily)
    us_dates = sorted(str(r.get("date") or "")[:10] for r in (sho_context.get("usRows") or [])
                      if isinstance(r, Mapping) and _number(r.get("close")) is not None)
    if not us_dates:
        out["us"] = {"status": "no_rows" if market == "JP" else "not_applicable"}
    else:
        limit = [d for d in us_dates if (d < date if market == "JP" else d <= date)]
        if not limit:
            out["us"] = {"status": "not_yet_available", "newestDate": us_dates[-1]}
        else:
            used = limit[-1]
            try:
                gap = (dtdate.fromisoformat(date) - dtdate.fromisoformat(used)).days
            except ValueError:
                gap = None
            joined = gap is not None and 0 <= gap <= VIX_JOIN_MAX_DAYS
            out["us"] = {"status": "joined" if joined else "stale_beyond_window",
                         "date": used, "ageDays": gap, "maxDays": VIX_JOIN_MAX_DAYS}
    return out


def calibrate_forecast(rows: Iterable[Dict[str, Any]],
                       sho_context: Optional[Mapping[str, Any]] = None,
                       market: Optional[str] = None) -> Dict[str, Any]:
    bars = normalize_bars(rows)
    sho_daily = _sho_daily_features(bars, sho_context, market)
    coverage_days = sum(1 for row in sho_daily if row)
    current_keys = sorted((sho_daily[-1] or {}).keys()) if sho_daily else []
    result = {str(horizon): calibrate_horizon(bars, horizon, sho_daily)
              for horizon in HORIZONS}
    return {
        "schemaVersion": "argus-forecast-calibration-v1",
        "methodVersion": METHOD_VERSION,
        "calibrationVersion": CALIBRATION_VERSION,
        "historyStart": bars[0]["date"] if bars else None,
        "historyEnd": bars[-1]["date"] if bars else None,
        "historyCount": len(bars), "horizons": result,
        # SHO conditioning transparency: which state dimensions the CURRENT
        # day actually knows, and how many corpus days carry SHO state. This
        # is measurement, not a claim of skill — skill stays with the
        # walk-forward Brier machinery.
        "shoConditioning": {
            "requested": bool(sho_context),
            "currentFeatureKeys": current_keys,
            "coverageDays": coverage_days,
            # v13.5.65: per input — the period/date the latest bar joined,
            # or why it could not (interval vs failure vs not applicable).
            "inputs": sho_input_freshness(
                sho_context, bars[-1]["date"] if bars else "", market),
            # v13.5.36 (external review): a provider MISCONFIGURATION (e.g.
            # missing FRED key) must not be indistinguishable from an honest
            # data gap — the serving layer names the broken source here and
            # the UI displays it. Empty when every configured source is fine.
            "sourceIssues": sorted(
                str(issue)[:60] for issue in
                ((sho_context or {}).get("sourceIssues") or [])
                if issue),
        },
        "automaticAiCalls": 0,
    }


def failed_rally_state(previous: Dict[str, Any], current: Dict[str, Any], *,
                       short_change: Optional[float] = None,
                       breadth_divergence: bool = False) -> Dict[str, Any]:
    prev_close = _number(previous.get("close"))
    open_ = _number(current.get("open")); high = _number(current.get("high"))
    low = _number(current.get("low")); close = _number(current.get("close"))
    if None in (prev_close, open_, high, low, close) or min(prev_close, open_, high, low, close) <= 0:
        return {"state": "NONE", "facts": [], "metrics": {}}
    daily_range = max(1e-12, high - low)
    gap = (open_ / prev_close - 1) * 100
    high_to_close = (high / close - 1) * 100
    close_vs_previous = (close / prev_close - 1) * 100
    location = (close - low) / daily_range
    upper_wick = (high - max(open_, close)) / daily_range
    volume_ratio = _number(current.get("volumeRatio20"))
    conditions = {
        "gapUp": gap >= .5,
        "highToCloseDecline": high_to_close >= 1.0,
        "closeBelowPrevious": close_vs_previous < 0,
        "weakCloseLocation": location <= .35,
        "upperWick": upper_wick >= .35,
        "volumeConfirmed": volume_ratio is not None and volume_ratio >= 1.1,
        "shortRatioFell": short_change is not None and short_change <= -1.5,
        "breadthDivergence": bool(breadth_divergence),
    }
    core = conditions["gapUp"] and conditions["highToCloseDecline"]
    confirmed = (core and conditions["closeBelowPrevious"] and conditions["weakCloseLocation"]
                 and any(conditions[key] for key in
                         ("volumeConfirmed", "shortRatioFell", "breadthDivergence", "upperWick")))
    watch = core and sum(1 for value in conditions.values() if value) >= 3
    state = "CONFIRMED" if confirmed else "WATCH" if watch else "NONE"
    labels = {
        "gapUp": "寄り付きギャップ高", "highToCloseDecline": "高値から終値へ失速",
        "closeBelowPrevious": "終値が前日比マイナス", "weakCloseLocation": "日中安値圏引け",
        "upperWick": "長い上ヒゲ", "volumeConfirmed": "出来高増",
        "shortRatioFell": "日次SHORT比率が低下", "breadthDivergence": "指数間の方向乖離",
    }
    return {
        "state": state, "facts": [labels[key] for key, value in conditions.items() if value],
        "conditions": conditions,
        "metrics": {"gapUpPct": round(gap, 2), "highToClosePct": round(high_to_close, 2),
                    "closeVsPreviousPct": round(close_vs_previous, 2),
                    "closeLocation": round(location, 3), "upperWickRatio": round(upper_wick, 3),
                    "volumeRatio20": volume_ratio, "shortRatioChangePt": short_change},
    }


def _comparison_divergence(primary: Sequence[Dict[str, Any]], comparison: Sequence[Dict[str, Any]],
                           date: str) -> bool:
    by_primary = {row["date"]: row for row in primary}
    by_comparison = {row["date"]: row for row in comparison}
    dates = sorted(set(by_primary) & set(by_comparison))
    if date not in dates:
        return False
    index = dates.index(date)
    if index < 1:
        return False
    previous = dates[index - 1]
    p = float(by_primary[date]["close"]) / float(by_primary[previous]["close"]) - 1
    c = float(by_comparison[date]["close"]) / float(by_comparison[previous]["close"]) - 1
    return p * c < 0


def failed_rally_backtest(rows: Iterable[Dict[str, Any]], *,
                          short_history: Iterable[Dict[str, Any]] = (),
                          comparison_rows: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    bars = normalize_bars(rows)
    comparison = normalize_bars(comparison_rows)
    short_rows = normalize_short_history(short_history)
    short_by_date = {row["date"]: row for row in short_rows}
    cases: List[Dict[str, Any]] = []
    for index in range(1, len(bars) - 20):
        current = dict(bars[index])
        volume_window = [_number(row.get("volume")) for row in bars[max(0, index - 19):index + 1]]
        clean = [value for value in volume_window if value and value > 0]
        if clean and current.get("volume"):
            current["volumeRatio20"] = float(current["volume"]) / (sum(clean) / len(clean))
        short = short_by_date.get(current["date"])
        state = failed_rally_state(
            bars[index - 1], current,
            short_change=_number((short or {}).get("previousDayDifference")),
            breadth_divergence=_comparison_divergence(bars, comparison, current["date"]),
        )
        if state["state"] == "NONE":
            continue
        start = float(current["close"])
        outcomes = {str(h): round((float(bars[index + h]["close"]) / start - 1) * 100, 3)
                    for h in HORIZONS}
        future = bars[index + 1:index + 21]
        cases.append({"index": index, "date": current["date"], "state": state["state"],
                      "facts": state["facts"], "outcomes": outcomes,
                      "mfe20Pct": round((max(float(row["high"]) for row in future) / start - 1) * 100, 3),
                      "mae20Pct": round((min(float(row["low"]) for row in future) / start - 1) * 100, 3)})
    effective = _episodes([{**row, "distance": 0.0} for row in cases], 5)
    summary: Dict[str, Any] = {}
    for horizon in HORIZONS:
        values = [row["outcomes"][str(horizon)] for row in effective]
        summary[str(horizon)] = {
            "averageReturnPct": _round(_mean(values), 3),
            "declineRatePct": _round(100 * sum(1 for value in values if value < 0) / len(values), 1)
            if values else None,
        }
    observed_decline_rate = summary["5"]["declineRatePct"]
    return {
        "rawOccurrenceCount": len(cases), "episodeCount": len(effective),
        "effectiveSampleCount": len(effective), "cooldownTradingDays": 5,
        "outcomes": summary,
        "meanMfe20Pct": _round(_mean([row["mfe20Pct"] for row in effective]), 3),
        "meanMae20Pct": _round(_mean([row["mae20Pct"] for row in effective]), 3),
        # This is an observed conditional rate, not a calibrated forward
        # probability. It remains separate until a past-only baseline and
        # positive out-of-sample skill are both available.
        "observedDeclineRatePct": observed_decline_rate,
        "probability": None,
        "calibrationStatus": "forward_skill_not_evaluated",
        "forwardSkill": {
            "eligible": False,
            "reasonCodes": ["baseline_comparison_not_available"],
            "modelBrier": None, "baselineBrier": None, "brierSkill": None,
            "effectiveSample": len(effective),
            "noFutureLeakage": True,
        },
        "cases": [{key: row[key] for key in ("date", "state", "outcomes", "mfe20Pct", "mae20Pct")}
                  for row in effective[-40:]],
        "noFutureLeakage": True, "methodVersion": METHOD_VERSION,
    }


def analyze(rows: Iterable[Dict[str, Any]], *, symbol: str, market: str,
            short_history: Iterable[Dict[str, Any]] = (),
            comparison_rows: Iterable[Dict[str, Any]] = (),
            sho_context: Optional[Mapping[str, Any]] = None,
            as_of: Optional[str] = None) -> Dict[str, Any]:
    source_rows = list(rows or [])
    source_short = list(short_history or [])
    source_comparison = list(comparison_rows or [])
    context = {key: list((sho_context or {}).get(key) or [])
               for key in ("creditRows", "vixRows", "usRows")} \
        if sho_context else None
    pit_proofs: Dict[str, Any] = {}
    if as_of:
        source_rows, pit_proofs["bars"] = \
            argus_market_data_truth.point_in_time_rows(source_rows, as_of)
        source_short, pit_proofs["shortSelling"] = \
            argus_market_data_truth.point_in_time_rows(source_short, as_of)
        source_comparison, pit_proofs["comparison"] = \
            argus_market_data_truth.point_in_time_rows(
                source_comparison, as_of)
        if context is not None:
            # SHO context obeys the PIT cutoff: anything first known after
            # as_of is dropped here, and the per-day knowledge LAGS (a JP
            # close cannot see same-evening US prints) plus staleness windows
            # are enforced inside _sho_daily_features. The generic row-proof
            # machinery is bar-shaped (one row per date), so multi-series
            # weekly credit uses this explicit clamp instead.
            as_of_date = str(as_of)[:10]

            def _known_by_cutoff(row: Mapping[str, Any]) -> bool:
                known = str(row.get("availableFrom")
                            or row.get("date") or "")[:10]
                return len(known) == 10 and known <= as_of_date

            for context_key in ("creditRows", "vixRows", "usRows"):
                context[context_key] = [row for row in context[context_key]
                                        if isinstance(row, Mapping)
                                        and _known_by_cutoff(row)]
        for proof in pit_proofs.values():
            valid, reason = \
                argus_market_data_truth.verify_point_in_time_proof(proof)
            if not valid:
                raise ValueError(f"point_in_time_proof_invalid:{reason}")
    bars = normalize_bars(source_rows)
    short_rows = normalize_short_history(source_short)
    short_summary = short_selling_summary(short_rows, as_of)
    comparison = normalize_bars(source_comparison)
    current_failed = {"state": "NONE", "facts": [], "metrics": {}}
    if len(bars) >= 2:
        current = dict(bars[-1])
        volumes = [_number(row.get("volume")) for row in bars[-20:]]
        clean = [value for value in volumes if value and value > 0]
        if clean and current.get("volume"):
            current["volumeRatio20"] = float(current["volume"]) / (sum(clean) / len(clean))
        current_failed = failed_rally_state(
            bars[-2], current,
            short_change=_number(((short_summary.get("latest") or {}).get("previousDayDifference"))),
            breadth_divergence=_comparison_divergence(bars, comparison, bars[-1]["date"]),
        )
    calibration = calibrate_forecast(bars, sho_context=context, market=market)
    backtest = failed_rally_backtest(bars, short_history=short_rows,
                                     comparison_rows=comparison)
    return {
        "schemaVersion": SCHEMA_VERSION, "methodVersion": METHOD_VERSION,
        "symbol": symbol, "market": market,
        "asOf": as_of or (bars[-1]["date"] if bars else None),
        "pointInTime": {
            "policyId": argus_market_data_truth.PIT_POLICY_ID,
            "requested": bool(as_of),
            "verified": bool(as_of) and len(pit_proofs) == 3,
            "proofs": pit_proofs,
        },
        "historyCoverage": {"start": bars[0]["date"] if bars else None,
                            "end": bars[-1]["date"] if bars else None,
                            "count": len(bars)},
        "calibration": calibration, "shortSelling": short_summary,
        "failedRally": {**current_failed, "backtest": backtest,
                        "probability": (backtest.get("probability")
                                        if current_failed.get("state") != "NONE" else None)},
        "automaticAiCalls": 0,
    }


def empty_state() -> Dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, "snapshots": [],
            "shortSellingHistory": [], "failedRallyOutcomes": [],
            "lastUpdatedAt": None, "methodVersion": METHOD_VERSION}


def normalize_state(value: Any) -> Dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    out = empty_state()
    for key in ("snapshots", "shortSellingHistory", "failedRallyOutcomes"):
        rows = [row for row in source.get(key, []) if isinstance(row, dict)]
        rows.sort(key=lambda row: str(
            row.get("date") or row.get("asOf") or row.get("id") or ""))
        out[key] = rows[-STATE_LIMITS[key]:]
    out["lastUpdatedAt"] = source.get("lastUpdatedAt")
    return out


def merge_state(local: Dict[str, Any], remote: Dict[str, Any]) -> Dict[str, Any]:
    out = normalize_state(local)
    incoming = normalize_state(remote)
    identities = {"snapshots": "id", "shortSellingHistory": "date",
                  "failedRallyOutcomes": "id"}
    for key, identity in identities.items():
        by_identity = {row.get(identity): row for row in out[key] if row.get(identity) is not None}
        for row in incoming[key]:
            row_id = row.get(identity)
            if row_id not in by_identity:
                out[key].append(row)
                by_identity[row_id] = row
            elif key == "shortSellingHistory" and \
                    int(row.get("revision") or 0) > int(by_identity[row_id].get("revision") or 0):
                by_identity[row_id].update(row)
        out[key].sort(key=lambda row: str(row.get("date") or row.get("asOf") or row.get("id") or ""))
    out["lastUpdatedAt"] = max(str(out.get("lastUpdatedAt") or ""),
                               str(incoming.get("lastUpdatedAt") or "")) or None
    return out


def merge_analysis(state: Dict[str, Any], analysis: Dict[str, Any],
                   latest_bar: Optional[Dict[str, Any]],
                   short_history: Iterable[Dict[str, Any]], now_iso: str) -> Dict[str, Any]:
    out = normalize_state(state)
    for row in normalize_short_history(short_history):
        existing = next((item for item in out["shortSellingHistory"]
                         if item.get("date") == row["date"]), None)
        if existing is None:
            out["shortSellingHistory"].append(row)
        elif int(row.get("revision") or 0) > int(existing.get("revision") or 0):
            existing.update(row)
    snapshot_body = {
        "symbol": analysis.get("symbol"), "market": analysis.get("market"),
        "asOf": analysis.get("asOf"), "ohlcv": latest_bar,
        "calibration": analysis.get("calibration"),
        "shortSelling": analysis.get("shortSelling"),
        "failedRally": analysis.get("failedRally"),
        "methodVersion": METHOD_VERSION,
    }
    snapshot = {**snapshot_body, "id": "today-" + _hash(snapshot_body)}
    if snapshot["id"] not in {row.get("id") for row in out["snapshots"]}:
        out["snapshots"].append(snapshot)
    for row in (((analysis.get("failedRally") or {}).get("backtest") or {}).get("cases") or []):
        if not isinstance(row, dict) or not row.get("date"):
            continue
        body = {**row, "symbol": analysis.get("symbol"), "market": analysis.get("market"),
                "methodVersion": METHOD_VERSION}
        item = {**body, "id": "failed-rally-" + _hash(body)}
        if item["id"] not in {x.get("id") for x in out["failedRallyOutcomes"]}:
            out["failedRallyOutcomes"].append(item)
    out["snapshots"].sort(key=lambda row: str(row.get("asOf") or row.get("id") or ""))
    out["shortSellingHistory"] = sorted(out["shortSellingHistory"],
                                         key=lambda row: row.get("date") or "")
    out["failedRallyOutcomes"].sort(key=lambda row: str(row.get("date") or row.get("id") or ""))
    out["lastUpdatedAt"] = now_iso
    return out


def state_hash(state: Dict[str, Any]) -> str:
    normalized = normalize_state(state)
    return _hash({key: normalized[key] for key in
                  ("snapshots", "shortSellingHistory", "failedRallyOutcomes")}, 32)


def read_back_verified(local: Dict[str, Any], remote: Dict[str, Any]) -> bool:
    return state_hash(local) == state_hash(remote)


def data_source_audit(short_summary: Dict[str, Any], institutional_status: str) -> List[Dict[str, Any]]:
    latest = short_summary.get("latest") or {}
    return [
        {"seriesId": "weekly_credit_short_balance", "labelJa": "二市場合計信用売り残",
         "frequency": "weekly", "source": "JPX official / Market Ledger",
         "endpointOrFile": "Market Ledger credit.short_balance",
         "publicationTiming": "JPX weekly publication",
         "unit": "JPY", "schema": "market-ledger credit.short_balance",
         "isDailyShortRatio": False},
        {"seriesId": "daily_short_selling_activity", "labelJa": "日次空売り売買代金・比率",
         "frequency": "daily", "source": latest.get("source"), "unit": latest.get("unit"),
         "endpointOrFile": "J-Quants v2 /markets/short-ratio?s33=0050",
         "publicationTiming": short_summary.get("publicationTiming"),
         "latestDate": short_summary.get("latestDate"),
         "historyStart": short_summary.get("historyStart"),
         "coverage": short_summary.get("coverage"), "freshness": short_summary.get("freshness"),
         "status": short_summary.get("status"), "missingReason": short_summary.get("missingReason"),
         "schema": short_summary.get("schemaVersion"), "isWeeklyCreditBalance": False},
        {"seriesId": "reported_institutional_short_positions", "labelJa": "公表大口空売り残高",
         "frequency": "daily disclosures when threshold reports exist",
         "source": "JPX official short-position reports", "unit": "shares / ratio",
         "endpointOrFile": "JPX reported short-position files / existing entry-scout adapter",
         "publicationTiming": "calculation date and publication date retained separately",
         "status": institutional_status, "schema": "entry-scout shortDisclosed",
         "isDailyShortRatio": False, "isWeeklyCreditBalance": False},
    ]
