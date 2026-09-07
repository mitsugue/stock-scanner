#!/usr/bin/env python3
"""Walk-forward validation of the reversal-axis BUY states (v13.5.63).

GPT review item 2 (2026-09-07): 「必要データ、対象期間、合格条件、有効化までの
具体的な作業計画を示し、実行できる検証を進めてください」. This is that
runnable verification. It reconstructs the production reversal state
(``argus_sho.build_reversal_engine`` with the same MIXED background the
serving layer uses) for every trading day of a ten-year daily corpus and
measures what happened to the judgment subject (1321) after each entry into a
BUY-eligible state. Nothing here changes production: the outcome is a report
whose ``verdict`` says whether the eligibility criteria are met, and the
registry that enables BUY (``VERIFIED_SHO_BUY_ARTIFACTS``) stays empty until a
PASS report is reviewed and pinned by a code change.

Data (all daily OHLCV, complete bars only, point-in-time stamped):
  * NIKKEI_225_INDEX  — the axis' analysis instrument
  * VIX               — the second reversal axis
  * 1321              — the judgment subject whose forward returns are scored

Design rules (same family as probability-eligibility-v1):
  * entry is the CLOSE of the trading day AFTER the signal day, so a state
    computed at the end of day D (after the US close) can never see its own
    outcome; horizons are 5 and 20 trading days after entry;
  * one episode per transition INTO the eligible set, with a five-day
    cooldown (matches the replay engine);
  * in-sample = up to 2022-12-31, holdout = 2023-01-01 onward — the holdout
    is scored with the in-sample hit rates (no refitting);
  * pass criteria: n_in >= 100, n_out >= 60, Wilson 95% half-width <= 10pt,
    holdout Brier skill vs the unconditional base rate > 0 AND the Wilson
    lower bound above the base rate at both horizons, ECE over the four
    states <= 0.05, no future leakage (asserted by construction).

Usage:
  python3 scripts/reversal_buy_validation.py --data-dir <dir with y_N225.json
      y_VIX.json y_1321_T.json (Yahoo v8 chart JSON)> --out report.json
  python3 scripts/reversal_buy_validation.py --fetch --out report.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import argus_sho  # noqa: E402
import argus_single_decision  # noqa: E402

HORIZONS = (5, 20)
COOLDOWN_TRADING_DAYS = 5
WINDOW_BARS = 160          # indicator warm-up (52-bar span B + MACD) with margin
MIN_IN_SAMPLE = 100
MIN_HOLDOUT = 60
MAX_WILSON_HALF_WIDTH_PT = 10.0
MAX_ECE = 0.05
HOLDOUT_START = "2023-01-01"
ELIGIBLE = tuple(argus_single_decision.BUY_ELIGIBLE_SHO_STATES)
YAHOO = {
    "N225": ("%5EN225", "NIKKEI_225_INDEX", "T07:00:00Z"),
    "VIX": ("%5EVIX", "VIX", "T21:00:00Z"),
    "1321_T": ("1321.T", "1321", "T07:00:00Z"),
}


def _yahoo_rows(doc: Dict[str, Any], instrument: str, avail_suffix: str) -> List[Dict[str, Any]]:
    result = doc["chart"]["result"][0]
    stamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    rows: List[Dict[str, Any]] = []
    for index, stamp in enumerate(stamps):
        values = {key: quote[key][index] for key in ("open", "high", "low", "close", "volume")}
        if any(values[key] is None for key in values):
            continue                      # incomplete bar: excluded, never filled
        date = datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")
        rows.append({"instrumentId": instrument, "date": date,
                     **{k: float(values[k]) for k in ("open", "high", "low", "close")},
                     "volume": float(values["volume"]),
                     "availableFrom": date + avail_suffix})
    dedup: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        dedup[row["date"]] = row
    return [dedup[d] for d in sorted(dedup)]


def load_corpus(data_dir: Optional[str], fetch: bool) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for key, (symbol, instrument, suffix) in YAHOO.items():
        path = os.path.join(data_dir or ".", f"y_{key}.json")
        if fetch and not os.path.exists(path):
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10y&interval=1d"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if data_dir:
                with open(path, "wb") as handle:
                    handle.write(data)
            doc = json.loads(data.decode("utf-8"))
        else:
            with open(path, "r", encoding="utf-8") as handle:
                doc = json.load(handle)
        out[key] = _yahoo_rows(doc, instrument, suffix)
    return out


def daily_states(nikkei: Sequence[Dict[str, Any]], vix: Sequence[Dict[str, Any]],
                 start_index: int) -> List[Tuple[str, str, str]]:
    """(date, state, axisStatus) per Nikkei trading day from start_index."""
    vix_dates = [row["date"] for row in vix]
    out: List[Tuple[str, str, str]] = []
    for index in range(start_index, len(nikkei)):
        date = nikkei[index]["date"]
        cutoff = f"{date}T23:59:59Z"
        n_window = nikkei[max(0, index - WINDOW_BARS):index + 1]
        # VIX bars up to the same calendar date (the cutoff is after the US close).
        v_end = 0
        while v_end < len(vix_dates) and vix_dates[v_end] <= date:
            v_end += 1
        v_window = vix[max(0, v_end - WINDOW_BARS):v_end]
        artifact = argus_sho.build_reversal_engine(
            cutoff=cutoff, analysis_instrument="NIKKEI_225_INDEX",
            downside_background="MIXED", nikkei_rows=n_window, vix_rows=v_window)
        axis = artifact["reversalAxis"]
        out.append((date, str(axis["state"]), str(axis["status"])))
    return out


def _wilson(hits: int, n: int, z: float = 1.96) -> Tuple[float, float, float]:
    if n <= 0:
        return (float("nan"), float("nan"), float("nan"))
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (centre - half, centre + half, half)


def _brier(prob: float, outcomes: Sequence[int]) -> Optional[float]:
    if not outcomes:
        return None
    return sum((prob - y) ** 2 for y in outcomes) / len(outcomes)


def evaluate(states: Sequence[Tuple[str, str, str]], subject: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    closes = {row["date"]: float(row["close"]) for row in subject}
    dates = [row["date"] for row in subject]
    position = {date: index for index, date in enumerate(dates)}

    def forward(signal_date: str, horizon: int) -> Optional[Tuple[str, float]]:
        # entry = close of the next subject trading day strictly after the signal
        entry_index = None
        for index, date in enumerate(dates):
            if date > signal_date:
                entry_index = index
                break
        if entry_index is None or entry_index + horizon >= len(dates):
            return None
        entry = closes[dates[entry_index]]
        exit_ = closes[dates[entry_index + horizon]]
        assert dates[entry_index] > signal_date, "future leakage"
        return dates[entry_index], exit_ / entry - 1.0

    episodes: List[Dict[str, Any]] = []
    last_entry_index = -10 ** 9
    previous_eligible = False
    for index, (date, state, status) in enumerate(states):
        eligible = state in ELIGIBLE and status == "AVAILABLE"
        if eligible and not previous_eligible and index - last_entry_index > COOLDOWN_TRADING_DAYS:
            row: Dict[str, Any] = {"signalDate": date, "state": state}
            for horizon in HORIZONS:
                fwd = forward(date, horizon)
                if fwd is None:
                    row = {}
                    break
                row["entryDate"], row[f"r{horizon}"] = fwd
            if row:
                episodes.append(row)
                last_entry_index = index
        previous_eligible = eligible

    # unconditional base: every subject day as an entry (same horizons)
    base: Dict[str, Dict[str, List[int]]] = {"in": {}, "out": {}}
    for index, date in enumerate(dates):
        split = "out" if date >= HOLDOUT_START else "in"
        for horizon in HORIZONS:
            if index + horizon < len(dates):
                base[split].setdefault(str(horizon), []).append(
                    int(closes[dates[index + horizon]] > closes[date]))

    def split_of(episode: Dict[str, Any]) -> str:
        return "out" if episode["signalDate"] >= HOLDOUT_START else "in"

    report: Dict[str, Any] = {"episodes": len(episodes), "horizons": {}, "byState": {}}
    failures: List[str] = []
    n_in = sum(1 for e in episodes if split_of(e) == "in")
    n_out = sum(1 for e in episodes if split_of(e) == "out")
    report["sampleIn"], report["sampleOut"] = n_in, n_out
    if n_in < MIN_IN_SAMPLE:
        failures.append(f"in_sample_below_{MIN_IN_SAMPLE}")
    if n_out < MIN_HOLDOUT:
        failures.append(f"holdout_below_{MIN_HOLDOUT}")
    for horizon in HORIZONS:
        key = str(horizon)
        y_in = [int(e[f"r{horizon}"] > 0) for e in episodes if split_of(e) == "in"]
        y_out = [int(e[f"r{horizon}"] > 0) for e in episodes if split_of(e) == "out"]
        p_in = (sum(y_in) / len(y_in)) if y_in else None
        base_in = base["in"].get(key) or []
        base_out = base["out"].get(key) or []
        base_rate_in = (sum(base_in) / len(base_in)) if base_in else None
        base_rate_out = (sum(base_out) / len(base_out)) if base_out else None
        lo, hi, half = _wilson(sum(y_out), len(y_out))
        model_brier = _brier(p_in, y_out) if p_in is not None else None
        base_brier = _brier(base_rate_in, y_out) if base_rate_in is not None else None
        bss = (1 - model_brier / base_brier) if (model_brier is not None and base_brier) else None
        mean_out = (sum(e[f"r{horizon}"] for e in episodes if split_of(e) == "out") / len(y_out)) if y_out else None
        # ECE over the four eligible states: predicted = in-sample state rate,
        # realised = holdout state rate, weighted by holdout count.
        ece = 0.0
        by_state: Dict[str, Any] = {}
        for state in ELIGIBLE:
            s_in = [int(e[f"r{horizon}"] > 0) for e in episodes if split_of(e) == "in" and e["state"] == state]
            s_out = [int(e[f"r{horizon}"] > 0) for e in episodes if split_of(e) == "out" and e["state"] == state]
            pred = (sum(s_in) / len(s_in)) if s_in else None
            real = (sum(s_out) / len(s_out)) if s_out else None
            by_state[state] = {"nIn": len(s_in), "nOut": len(s_out), "hitRateIn": pred, "hitRateOut": real}
            if pred is not None and real is not None and y_out:
                ece += abs(pred - real) * len(s_out) / len(y_out)
        report["horizons"][key] = {
            "hitRateIn": p_in, "hitRateOut": (sum(y_out) / len(y_out)) if y_out else None,
            "meanReturnOut": mean_out,
            "baseRateIn": base_rate_in, "baseRateOut": base_rate_out,
            "wilsonLowOut": lo, "wilsonHighOut": hi, "wilsonHalfWidthPt": (half * 100 if half == half else None),
            "modelBrierOut": model_brier, "baseBrierOut": base_brier, "brierSkillOut": bss,
            "ece": ece, "byState": by_state,
        }
        if half != half or half * 100 > MAX_WILSON_HALF_WIDTH_PT:
            failures.append(f"h{horizon}_wilson_half_width_above_{MAX_WILSON_HALF_WIDTH_PT:g}pt")
        if bss is None or bss <= 0:
            failures.append(f"h{horizon}_no_brier_skill_over_base_rate")
        if base_rate_out is None or lo != lo or lo <= base_rate_out:
            failures.append(f"h{horizon}_wilson_lower_bound_not_above_base_rate")
        if ece > MAX_ECE:
            failures.append(f"h{horizon}_ece_above_{MAX_ECE:g}")
    report["failedCriteria"] = failures
    report["verdict"] = "PASS" if not failures else "FAIL"
    report["episodesSample"] = episodes[-8:]
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--fetch", action="store_true", help="download from Yahoo when a file is missing")
    parser.add_argument("--out", default=None, help="write the JSON report here")
    args = parser.parse_args(argv)
    corpus = load_corpus(args.data_dir, args.fetch)
    nikkei, vix, subject = corpus["N225"], corpus["VIX"], corpus["1321_T"]
    states = daily_states(nikkei, vix, start_index=min(WINDOW_BARS, len(nikkei) - 1))
    counts: Dict[str, int] = {}
    for _, state, _ in states:
        counts[state] = counts.get(state, 0) + 1
    report = evaluate(states, subject)
    report.update({
        "schemaVersion": "argus-reversal-buy-validation-v1",
        "evaluatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policyId": argus_sho.SHO_REGISTRY_VERSION,
        "policySha256": argus_sho.SHO_REGISTRY_SHA256,
        "eligibleStates": list(ELIGIBLE),
        "corpus": {"nikkei": [nikkei[0]["date"], nikkei[-1]["date"], len(nikkei)],
                   "vix": [vix[0]["date"], vix[-1]["date"], len(vix)],
                   "subject1321": [subject[0]["date"], subject[-1]["date"], len(subject)]},
        "stateDays": counts,
        "holdoutStart": HOLDOUT_START,
        "criteria": {"minInSample": MIN_IN_SAMPLE, "minHoldout": MIN_HOLDOUT,
                     "maxWilsonHalfWidthPt": MAX_WILSON_HALF_WIDTH_PT, "maxEce": MAX_ECE,
                     "brierSkillOverBaseRate": "> 0", "wilsonLowerBoundAboveBaseRate": True},
        "enablesProductionBuy": False,
        "noteJa": "この報告はBUY有効化の前提となる検証結果です。PASSでもコードレビューで"
                  "レジストリに固定されるまでBUYは構造的に無効のままです。",
    })
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1)
    print(json.dumps({k: report[k] for k in ("verdict", "failedCriteria", "episodes", "sampleIn",
                                              "sampleOut", "stateDays", "corpus")}, ensure_ascii=False))
    for horizon, row in report["horizons"].items():
        print(f"h{horizon}: hitIn={row['hitRateIn']} hitOut={row['hitRateOut']} base={row['baseRateOut']} "
              f"wilson=[{row['wilsonLowOut']:.3f},{row['wilsonHighOut']:.3f}] BSS={row['brierSkillOut']} "
              f"ECE={row['ece']:.3f} meanR={row['meanReturnOut']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
