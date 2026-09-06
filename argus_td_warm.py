"""Twelve Data warm scheduler core — pure, provider-free (v13.5.54).

Owner decisions (2026-09-05):

* The real subscription is **Basic**: ~8 API credits per minute, 800 per day.
  The plan is never impersonated; an unset ``TWELVEDATA_PLAN`` means Basic.
* ``8 credits/minute`` is a REQUEST BATCH cap, not a universe size. A Basic
  plan can carry a universe larger than eight when requests are rotated across
  minutes and stay inside the daily budget. So the ninth symbol is never
  silently dropped: it is fetched on the next eligible minute.
* Polling is market-aware. Regular session ≈ every 10 minutes; pre/after hours
  at a lower cadence; closed markets are not polled (EOD/cached evidence
  stands). The design target is comfortably under 800 credits/day with reserve
  for the other legitimate Twelve Data calls.
* The universe is the curated set plus the owner-authorized runtime interest
  set (Layer-2B membership, market == US), deduplicated, and bounded by an
  AUTHORIZED universe cap. Private holdings are never hard-coded here.

Everything in this module is deterministic and takes its clock/session as
input, so the whole policy is fixture-testable. Diagnostics expose counts and
budgets only — never the owner's symbols.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

METHOD_VERSION = "td-warm-scheduler-v1"

# Official Twelve Data plan limits used as the architecture constraint.
# /quote costs one credit per symbol (batched symbols are charged per symbol).
QUOTE_CREDITS_PER_SYMBOL = 1
PLAN_LIMITS: Dict[str, Dict[str, int]] = {
    "basic": {"creditsPerMinute": 8, "creditsPerDay": 800},
    # Grow is listed only so a truthfully configured Grow key is not treated
    # as Basic; the owner's plan is Basic and nothing here assumes otherwise.
    "grow": {"creditsPerMinute": 55, "creditsPerDay": 5000},
}
PAID_PLANS = ("grow", "pro", "enterprise", "custom")

US_SYMBOL_RE = re.compile(r"^[A-Z][A-Z.\-]{0,9}$")

# Sessions from argus_market_clock.market_session("US_EQUITY").
REGULAR_SESSIONS = ("REGULAR",)
EXTENDED_SESSIONS = ("PRE_MARKET", "AFTER_HOURS")

# Session lengths (minutes) used ONLY for the daily-usage estimate.
REGULAR_MINUTES_PER_DAY = 390          # 09:30–16:00 ET
EXTENDED_MINUTES_PER_DAY = 330 + 240   # 04:00–09:30 + 16:00–20:00 ET

# Closed reason vocabulary for a tick decision.
SKIP_REASONS = ("market_closed", "cadence_not_due", "rate_limited_backoff",
                "daily_budget_exhausted", "universe_empty", "disabled",
                "minute_gap")


def normalize_plan(raw: Optional[str]) -> str:
    """Truthful plan vocabulary: unset/basic/free → 'basic'; paid names pass
    through lower-cased. Nothing ever *upgrades* an unset value."""
    value = (raw or "").strip().lower()
    if value in ("", "basic", "free"):
        return "basic"
    return value


def plan_limits(plan: str) -> Dict[str, int]:
    plan = normalize_plan(plan)
    if plan in PLAN_LIMITS:
        return dict(PLAN_LIMITS[plan])
    if plan in PAID_PLANS:
        return dict(PLAN_LIMITS["grow"])
    return dict(PLAN_LIMITS["basic"])


def sanitize_symbols(raw: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for item in raw or ():
        s = str(item or "").strip().upper()
        if s and US_SYMBOL_RE.match(s) and s not in out:
            out.append(s)
    return out


def build_universe(*, curated: Sequence[str], owner_members: Sequence[str] = (),
                   hints: Sequence[str] = (), universe_cap: int) -> Dict[str, Any]:
    """Curated ∪ owner-authorized ∪ trusted hints, deduplicated in that
    priority order, bounded by the AUTHORIZED universe cap. A symbol present
    in more than one set is scheduled once (no duplicate credits)."""
    cap = max(1, int(universe_cap))
    ordered: List[str] = []
    origin: Dict[str, str] = {}
    for label, group in (("curated", curated), ("owner", owner_members),
                         ("hint", hints)):
        for s in sanitize_symbols(group):
            if s not in origin:
                origin[s] = label
                ordered.append(s)
    accepted = ordered[:cap]
    dropped = ordered[cap:]
    counts = {"curated": 0, "owner": 0, "hint": 0}
    for s in accepted:
        counts[origin[s]] += 1
    return {"symbols": accepted, "dropped": dropped, "universeCap": cap,
            "sourceCounts": counts,
            "droppedReason": "authorized_universe_cap" if dropped else None}


def warm_cadence_sec(session: Optional[str], *, regular_sec: int,
                     extended_sec: int) -> Optional[int]:
    """Seconds between warm CYCLES for a session, or None when the market is
    closed (no repetitive quote polling; EOD/cached evidence stands)."""
    if session in REGULAR_SESSIONS:
        return max(60, int(regular_sec))
    if session in EXTENDED_SESSIONS:
        return max(60, int(extended_sec))
    return None


def utc_day(now_utc: datetime) -> str:
    return now_utc.astimezone(timezone.utc).date().isoformat()


def new_state() -> Dict[str, Any]:
    return {
        "ledgerDay": None, "usedToday": 0, "requestsToday": 0,
        "cursor": 0, "cycleStartedAt": None, "lastRequestAt": None,
        "lastFetchAt": None, "lastReason": None, "backoffUntil": None,
        "lastError": None, "warmSymbolCount": 0, "lastBatchSize": 0,
        "cyclesToday": 0,
    }


def _epoch(value: Optional[datetime]) -> Optional[float]:
    return value.timestamp() if isinstance(value, datetime) else None


def roll_ledger(state: Dict[str, Any], now_utc: datetime) -> None:
    day = utc_day(now_utc)
    if state.get("ledgerDay") != day:
        state.update({"ledgerDay": day, "usedToday": 0, "requestsToday": 0,
                      "cyclesToday": 0})


def estimate_daily_usage(*, universe_size: int, regular_sec: int,
                         extended_sec: int) -> Dict[str, Any]:
    """Credits/day if every eligible cycle ran (upper bound), from the real
    endpoint weight (one credit per symbol per /quote)."""
    size = max(0, int(universe_size))
    regular_cycles = math.floor(REGULAR_MINUTES_PER_DAY * 60 / max(60, regular_sec)) + 1
    extended_cycles = math.floor(EXTENDED_MINUTES_PER_DAY * 60 / max(60, extended_sec))
    credits = (regular_cycles + extended_cycles) * size * QUOTE_CREDITS_PER_SYMBOL
    return {"regularCycles": regular_cycles, "extendedCycles": extended_cycles,
            "creditsPerCycle": size * QUOTE_CREDITS_PER_SYMBOL,
            "estimatedDailyCredits": credits}


def next_batch(universe: Sequence[str], cursor: int, batch_cap: int
               ) -> Dict[str, Any]:
    """Rotate through the universe `batch_cap` symbols at a time."""
    cap = max(1, int(batch_cap))
    size = len(universe)
    if size == 0:
        return {"batch": [], "cursor": 0, "cycleComplete": True}
    start = cursor % size
    batch = list(universe[start:start + cap])
    new_cursor = start + len(batch)
    complete = new_cursor >= size
    return {"batch": batch, "cursor": 0 if complete else new_cursor,
            "cycleComplete": complete}


def plan_tick(state: Dict[str, Any], *, now_utc: datetime, session: Optional[str],
              universe: Sequence[str], batch_cap: int, warm_daily_cap: int,
              regular_sec: int, extended_sec: int, enabled: bool = True,
              min_request_gap_sec: int = 60) -> Dict[str, Any]:
    """Decide ONE tick: fetch a batch or skip, with a closed reason.

    A rotation in progress (cursor > 0) finishes at one batch per minute even
    across a cadence boundary, so every symbol in the universe gets its turn;
    a NEW cycle starts only when the session cadence is due. Budget and
    rate-limit backoff are checked before every request.
    """
    roll_ledger(state, now_utc)
    now = now_utc.timestamp()
    if not enabled:
        return _skip(state, "disabled")
    universe = list(universe)
    if not universe:
        return _skip(state, "universe_empty")
    cadence = warm_cadence_sec(session, regular_sec=regular_sec,
                               extended_sec=extended_sec)
    mid_rotation = int(state.get("cursor") or 0) > 0
    if cadence is None and not mid_rotation:
        return _skip(state, "market_closed")
    backoff = state.get("backoffUntil")
    if backoff is not None and now < float(backoff):
        return _skip(state, "rate_limited_backoff")
    last_request = state.get("lastRequestAt")
    if last_request is not None and now - float(last_request) < min_request_gap_sec:
        return _skip(state, "minute_gap")
    if not mid_rotation:
        started = state.get("cycleStartedAt")
        if started is not None and cadence is not None and now - float(started) < cadence:
            return _skip(state, "cadence_not_due")
    plan = next_batch(universe, int(state.get("cursor") or 0), batch_cap)
    credits = len(plan["batch"]) * QUOTE_CREDITS_PER_SYMBOL
    if int(state.get("usedToday") or 0) + credits > int(warm_daily_cap):
        return _skip(state, "daily_budget_exhausted")
    return {"action": "fetch", "reason": None, "batch": plan["batch"],
            "credits": credits, "cursorAfter": plan["cursor"],
            "cycleComplete": plan["cycleComplete"],
            "startsCycle": not mid_rotation}


def _skip(state: Dict[str, Any], reason: str) -> Dict[str, Any]:
    state["lastReason"] = reason
    return {"action": "skip", "reason": reason, "batch": [], "credits": 0}


def record_request(state: Dict[str, Any], decision: Mapping[str, Any], *,
                   now_utc: datetime, ok: bool, rate_limited: bool = False,
                   warm_symbol_count: Optional[int] = None,
                   backoff_sec: int = 60, error_class: Optional[str] = None
                   ) -> None:
    """Charge the ledger for an attempted request (the provider charges the
    attempt), advance the rotation on success, and arm backoff on 429."""
    roll_ledger(state, now_utc)
    now = now_utc.timestamp()
    state["lastRequestAt"] = now
    state["requestsToday"] = int(state.get("requestsToday") or 0) + 1
    state["usedToday"] = int(state.get("usedToday") or 0) + int(decision.get("credits") or 0)
    state["lastBatchSize"] = len(decision.get("batch") or [])
    if rate_limited:
        # The batch was not served: arm backoff and leave the cycle unstarted
        # so the same batch is retried right after the backoff instead of
        # waiting out a whole cadence window with nothing warmed.
        state["backoffUntil"] = now + max(1, int(backoff_sec))
        state["lastReason"] = "rate_limited"
        state["lastError"] = error_class or "rate_limited"
        return
    if decision.get("startsCycle"):
        state["cycleStartedAt"] = now
        state["cyclesToday"] = int(state.get("cyclesToday") or 0) + 1
    if not ok:
        state["lastReason"] = "provider_error"
        state["lastError"] = error_class or "provider_error"
        # Do not spin on a failing batch: move on so the rest of the
        # universe still gets its turn this cycle.
        state["cursor"] = int(decision.get("cursorAfter") or 0)
        return
    state["cursor"] = int(decision.get("cursorAfter") or 0)
    state["lastFetchAt"] = now
    state["lastReason"] = "fetched"
    state["lastError"] = None
    state["backoffUntil"] = None
    if warm_symbol_count is not None:
        state["warmSymbolCount"] = int(warm_symbol_count)


# Cadence ladder the fitter may step through (seconds). Regular never goes
# below the configured value; extended never below the configured value.
CADENCE_LADDER_REGULAR = (600, 720, 900, 1200, 1800, 3600)
CADENCE_LADDER_EXTENDED = (1800, 3600, 7200)


def total_daily_estimate(*, universe_size: int, regular_sec: int, extended_sec: int,
                         other_daily_credits: int) -> Dict[str, Any]:
    warm = estimate_daily_usage(universe_size=universe_size, regular_sec=regular_sec,
                                extended_sec=extended_sec)
    total = warm["estimatedDailyCredits"] + max(0, int(other_daily_credits))
    return {"warm": warm["estimatedDailyCredits"], "other": max(0, int(other_daily_credits)),
            "total": total, **{f"warm_{k}": v for k, v in warm.items()
                               if k != "estimatedDailyCredits"}}


def fit_cadence(*, universe_size: int, other_daily_credits: int, daily_budget: int,
                reserve: int, regular_sec: int, extended_sec: int) -> Dict[str, Any]:
    """Owner rule (2026-09-05): TOTAL Twelve Data consumption across ALL call
    sites must stay under the plan's daily limit with a meaningful reserve. If
    the configured warm cadence plus the other traffic does not fit, reduce
    the cadence automatically (never raise it above the configured rate)."""
    ceiling = max(0, int(daily_budget) - max(0, int(reserve)))
    configured = (max(60, int(regular_sec)), max(60, int(extended_sec)))
    candidates = [(r, e) for r in CADENCE_LADDER_REGULAR for e in CADENCE_LADDER_EXTENDED
                  if r >= configured[0] and e >= configured[1]]
    if configured not in candidates:
        candidates.insert(0, configured)
    candidates.sort()
    chosen = None
    for r, e in candidates:
        est = total_daily_estimate(universe_size=universe_size, regular_sec=r,
                                   extended_sec=e, other_daily_credits=other_daily_credits)
        if est["total"] <= ceiling:
            chosen = (r, e, est)
            break
    if chosen is None:
        r, e = candidates[-1]
        est = total_daily_estimate(universe_size=universe_size, regular_sec=r,
                                   extended_sec=e, other_daily_credits=other_daily_credits)
        return {"regularSec": r, "extendedSec": e, "fitted": False,
                "reducedFromRegularSec": configured[0], "reducedFromExtendedSec": configured[1],
                "ceiling": ceiling, "estimate": est,
                "note": "even_the_slowest_cadence_exceeds_the_ceiling"}
    r, e, est = chosen
    return {"regularSec": r, "extendedSec": e, "fitted": True,
            "reducedFromRegularSec": configured[0] if r != configured[0] else None,
            "reducedFromExtendedSec": configured[1] if e != configured[1] else None,
            "ceiling": ceiling, "estimate": est, "note": None}


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def diagnostics(state: Mapping[str, Any], *, plan: str, batch_cap: int,
                universe: Mapping[str, Any], warm_daily_cap: int,
                regular_sec: int, extended_sec: int, session: Optional[str],
                enabled: bool, api_key_present: bool,
                other_daily_credits: int = 0, reserve: int = 0,
                fit: Optional[Mapping[str, Any]] = None,
                now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Public-safe budget/coverage truth. Counts and budgets only — the
    owner's symbols never appear here. `regular_sec`/`extended_sec` are the
    EFFECTIVE (fitted) cadences; `fit` carries what they were reduced from."""
    limits = plan_limits(plan)
    size = len(universe.get("symbols") or [])
    estimate = estimate_daily_usage(universe_size=size, regular_sec=regular_sec,
                                    extended_sec=extended_sec)
    total = total_daily_estimate(universe_size=size, regular_sec=regular_sec,
                                 extended_sec=extended_sec,
                                 other_daily_credits=other_daily_credits)
    used = int(state.get("usedToday") or 0)
    now = (now_utc or datetime.now(timezone.utc)).timestamp()
    backoff_until = state.get("backoffUntil")
    return {
        "minuteLimit": limits["creditsPerMinute"],
        "dailyLimit": limits["creditsPerDay"],
        "otherConsumersDailyEstimate": total["other"],
        "estimatedTotalDailyCredits": total["total"],
        "reserveTarget": int(reserve),
        "totalWithinDailyLimit": total["total"] < limits["creditsPerDay"],
        "totalWithinReserve": total["total"] <= limits["creditsPerDay"] - int(reserve),
        "cadenceFit": dict(fit) if fit else None,
        "backoff": {"active": backoff_until is not None and now < float(backoff_until),
                    "until": _iso(backoff_until),
                    "lastError": state.get("lastError")},
        "schemaVersion": "argus-twelvedata-budget-v1",
        "methodVersion": METHOD_VERSION,
        "plan": normalize_plan(plan),
        "planImpersonated": False,
        "apiKeyPresent": bool(api_key_present),
        "enabled": bool(enabled),
        "quoteCreditsPerSymbol": QUOTE_CREDITS_PER_SYMBOL,
        "requestBatchCap": int(batch_cap),
        "creditsPerMinuteLimit": limits["creditsPerMinute"],
        "dailyBudget": limits["creditsPerDay"],
        "warmDailyCap": int(warm_daily_cap),
        "reserveForOtherCalls": max(0, limits["creditsPerDay"] - int(warm_daily_cap)),
        "authorizedUniverseCap": int(universe.get("universeCap") or 0),
        "universeSize": size,
        "universeSourceCounts": dict(universe.get("sourceCounts") or {}),
        "universeDroppedCount": len(universe.get("dropped") or []),
        "cadence": {"session": session,
                    "regularSec": int(regular_sec), "extendedSec": int(extended_sec),
                    "activeSec": warm_cadence_sec(session, regular_sec=regular_sec,
                                                  extended_sec=extended_sec)},
        "estimatedDailyUsage": estimate["estimatedDailyCredits"],
        "estimatedCycles": {"regular": estimate["regularCycles"],
                            "extended": estimate["extendedCycles"]},
        "usedToday": used,
        "requestsToday": int(state.get("requestsToday") or 0),
        "cyclesToday": int(state.get("cyclesToday") or 0),
        "remainingHeadroom": max(0, int(warm_daily_cap) - used),
        "ledgerDay": state.get("ledgerDay"),
        "warmSymbolCount": int(state.get("warmSymbolCount") or 0),
        "lastFetchAt": _iso(state.get("lastFetchAt")),
        "lastRequestAt": _iso(state.get("lastRequestAt")),
        "lastReason": state.get("lastReason"),
        "lastError": state.get("lastError"),
        "backoffUntil": _iso(state.get("backoffUntil")),
        "budgetWithinDailyLimit": estimate["estimatedDailyCredits"] <= int(warm_daily_cap),
    }
