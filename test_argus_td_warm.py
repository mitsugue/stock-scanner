"""Twelve Data Basic-plan warm scheduler — policy regression matrix (v13.5.54).

Owner 2026-09-05: the real plan is Basic (8 credits/min, 800/day). The ninth
symbol must never be silently dropped, the plan must never be impersonated,
and daily usage must stay under the real limit with market-aware cadence.
"""
from datetime import datetime, timedelta, timezone

import argus_td_warm as tw

T0 = datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc)   # Monday 10:00 ET
NINE = ["NVDA", "AAPL", "TSLA", "META", "SPCX", "IONQ", "SOXS", "SOXL", "MU"]
CFG = dict(batch_cap=8, warm_daily_cap=560, regular_sec=600, extended_sec=1800)


def test_unset_plan_is_basic_and_is_never_upgraded():
    assert tw.normalize_plan(None) == "basic"
    assert tw.normalize_plan("") == "basic"
    assert tw.normalize_plan("Basic") == "basic"
    assert tw.plan_limits("") == {"creditsPerMinute": 8, "creditsPerDay": 800}
    assert tw.plan_limits("grow")["creditsPerMinute"] == 55


def test_universe_dedupes_curated_and_owner_sets_and_bounds_by_cap():
    u = tw.build_universe(curated=["NVDA", "AAPL", "TSLA", "META"],
                          owner_members=["meta", "SPCX", "IONQ", "SOXS", "SOXL", "MU", "nvda"],
                          universe_cap=24)
    assert u["symbols"] == NINE
    assert u["sourceCounts"] == {"curated": 4, "owner": 5, "hint": 0}
    assert u["dropped"] == [] and u["droppedReason"] is None
    capped = tw.build_universe(curated=NINE, universe_cap=4)
    assert capped["symbols"] == NINE[:4] and capped["dropped"] == NINE[4:]
    assert capped["droppedReason"] == "authorized_universe_cap"


def test_nine_symbols_rotate_across_two_minutes_and_nothing_is_dropped():
    state = tw.new_state()
    first = tw.plan_tick(state, now_utc=T0, session="REGULAR", universe=NINE, **CFG)
    assert first["action"] == "fetch" and first["batch"] == NINE[:8]
    assert first["credits"] == 8 and first["startsCycle"] is True
    tw.record_request(state, first, now_utc=T0, ok=True, warm_symbol_count=8)
    # Same minute: the per-minute credit cap holds.
    again = tw.plan_tick(state, now_utc=T0 + timedelta(seconds=30),
                         session="REGULAR", universe=NINE, **CFG)
    assert again["action"] == "skip" and again["reason"] == "minute_gap"
    # Next eligible minute: the ninth symbol gets its turn (rotation finishes
    # even though the 10-minute cadence is not yet due).
    t1 = T0 + timedelta(seconds=61)
    second = tw.plan_tick(state, now_utc=t1, session="REGULAR", universe=NINE, **CFG)
    assert second["action"] == "fetch" and second["batch"] == ["MU"]
    assert second["startsCycle"] is False and second["cycleComplete"] is True
    tw.record_request(state, second, now_utc=t1, ok=True, warm_symbol_count=9)
    assert state["usedToday"] == 9 and state["cursor"] == 0
    # A new cycle waits for the cadence.
    t2 = T0 + timedelta(seconds=300)
    assert tw.plan_tick(state, now_utc=t2, session="REGULAR", universe=NINE,
                        **CFG)["reason"] == "cadence_not_due"
    t3 = T0 + timedelta(seconds=601)
    assert tw.plan_tick(state, now_utc=t3, session="REGULAR", universe=NINE,
                        **CFG)["action"] == "fetch"


def test_closed_market_is_not_polled_but_a_rotation_in_progress_finishes():
    state = tw.new_state()
    assert tw.plan_tick(state, now_utc=T0, session="OVERNIGHT_CLOSED",
                        universe=NINE, **CFG)["reason"] == "market_closed"
    assert tw.plan_tick(state, now_utc=T0, session="WEEKEND_CLOSED",
                        universe=NINE, **CFG)["reason"] == "market_closed"
    first = tw.plan_tick(state, now_utc=T0, session="REGULAR", universe=NINE, **CFG)
    tw.record_request(state, first, now_utc=T0, ok=True)
    tail = tw.plan_tick(state, now_utc=T0 + timedelta(seconds=61),
                        session="OVERNIGHT_CLOSED", universe=NINE, **CFG)
    assert tail["action"] == "fetch" and tail["batch"] == ["MU"]


def test_extended_hours_use_the_lower_cadence():
    assert tw.warm_cadence_sec("PRE_MARKET", regular_sec=600, extended_sec=1800) == 1800
    assert tw.warm_cadence_sec("AFTER_HOURS", regular_sec=600, extended_sec=1800) == 1800
    assert tw.warm_cadence_sec("REGULAR", regular_sec=600, extended_sec=1800) == 600
    assert tw.warm_cadence_sec("HOLIDAY_CLOSED", regular_sec=600, extended_sec=1800) is None


def test_daily_budget_is_respected_and_resets_on_the_utc_day():
    state = tw.new_state()
    cfg = dict(CFG, warm_daily_cap=10)
    first = tw.plan_tick(state, now_utc=T0, session="REGULAR", universe=NINE, **cfg)
    tw.record_request(state, first, now_utc=T0, ok=True)
    t1 = T0 + timedelta(seconds=61)
    second = tw.plan_tick(state, now_utc=t1, session="REGULAR", universe=NINE, **cfg)
    tw.record_request(state, second, now_utc=t1, ok=True)
    assert state["usedToday"] == 9
    t2 = T0 + timedelta(seconds=700)
    blocked = tw.plan_tick(state, now_utc=t2, session="REGULAR", universe=NINE, **cfg)
    assert blocked["action"] == "skip" and blocked["reason"] == "daily_budget_exhausted"
    next_day = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    assert tw.plan_tick(state, now_utc=next_day, session="REGULAR",
                        universe=NINE, **cfg)["action"] == "fetch"
    assert state["usedToday"] == 0


def test_estimate_for_nine_symbols_stays_under_the_basic_daily_limit():
    est = tw.estimate_daily_usage(universe_size=9, regular_sec=600, extended_sec=1800)
    assert est["creditsPerCycle"] == 9
    assert est["regularCycles"] == 40 and est["extendedCycles"] == 19
    assert est["estimatedDailyCredits"] == 531
    assert est["estimatedDailyCredits"] <= 560 < 800


def test_rate_limit_arms_backoff_and_a_failed_batch_does_not_spin():
    state = tw.new_state()
    first = tw.plan_tick(state, now_utc=T0, session="REGULAR", universe=NINE, **CFG)
    tw.record_request(state, first, now_utc=T0, ok=False, rate_limited=True,
                      backoff_sec=90)
    t1 = T0 + timedelta(seconds=61)
    assert tw.plan_tick(state, now_utc=t1, session="REGULAR", universe=NINE,
                        **CFG)["reason"] == "rate_limited_backoff"
    # The attempt was charged (the provider charges attempts) and the cursor
    # did not advance past an unfetched batch on a rate limit.
    assert state["usedToday"] == 8 and state["cursor"] == 0
    t2 = T0 + timedelta(seconds=95)
    retry = tw.plan_tick(state, now_utc=t2, session="REGULAR", universe=NINE, **CFG)
    assert retry["action"] == "fetch" and retry["batch"] == NINE[:8]
    tw.record_request(state, retry, now_utc=t2, ok=False, error_class="HTTPError")
    assert state["cursor"] == 8 and state["lastReason"] == "provider_error"


def test_diagnostics_expose_budget_truth_and_never_symbols():
    state = tw.new_state()
    universe = tw.build_universe(curated=NINE[:4], owner_members=NINE[4:], universe_cap=24)
    first = tw.plan_tick(state, now_utc=T0, session="REGULAR", universe=universe["symbols"], **CFG)
    tw.record_request(state, first, now_utc=T0, ok=True, warm_symbol_count=8)
    d = tw.diagnostics(state, plan="", batch_cap=8, universe=universe,
                       warm_daily_cap=560, regular_sec=600, extended_sec=1800,
                       session="REGULAR", enabled=True, api_key_present=True)
    assert d["plan"] == "basic" and d["planImpersonated"] is False
    assert d["dailyBudget"] == 800 and d["warmDailyCap"] == 560
    assert d["reserveForOtherCalls"] == 240
    assert d["universeSize"] == 9 and d["requestBatchCap"] == 8
    assert d["estimatedDailyUsage"] == 531 and d["budgetWithinDailyLimit"] is True
    assert d["usedToday"] == 8 and d["remainingHeadroom"] == 552
    assert d["lastFetchAt"] == "2026-09-08T14:00:00Z"
    flat = repr(d)
    for s in NINE:
        assert s not in flat, s


def test_total_consumption_fits_under_the_daily_limit_by_reducing_cadence():
    """Owner 2026-09-05: TOTAL Twelve Data consumption across ALL call sites
    must stay under 800 with a meaningful reserve. Nine symbols at 10/30 min
    (531) plus ~276 of other traffic exceeds 800 - 80, so the cadence must
    step down automatically — never up."""
    fit = tw.fit_cadence(universe_size=9, other_daily_credits=276, daily_budget=800,
                         reserve=80, regular_sec=600, extended_sec=1800)
    assert fit["fitted"] is True
    # The fitter slows the cheaper-to-lose cadence first (extended hours), and
    # only steps the regular session down when that is not enough.
    assert fit["reducedFromRegularSec"] == 600 or fit["reducedFromExtendedSec"] == 1800
    assert fit["regularSec"] >= 600 and fit["extendedSec"] >= 1800
    assert fit["estimate"]["total"] <= 720 < 800
    # A configuration that already fits is left alone.
    keep = tw.fit_cadence(universe_size=4, other_daily_credits=276, daily_budget=800,
                          reserve=80, regular_sec=600, extended_sec=1800)
    assert keep["fitted"] is True and keep["regularSec"] == 600 and keep["reducedFromRegularSec"] is None
    # Never faster than configured.
    slow = tw.fit_cadence(universe_size=1, other_daily_credits=0, daily_budget=800,
                          reserve=80, regular_sec=1800, extended_sec=3600)
    assert slow["regularSec"] == 1800 and slow["extendedSec"] == 3600
    # An impossible budget is reported, not hidden.
    over = tw.fit_cadence(universe_size=24, other_daily_credits=790, daily_budget=800,
                          reserve=80, regular_sec=600, extended_sec=1800)
    assert over["fitted"] is False and over["note"]


def test_diagnostics_report_total_usage_minute_limit_and_backoff_state():
    state = tw.new_state()
    universe = tw.build_universe(curated=NINE[:4], owner_members=NINE[4:], universe_cap=24)
    fit = tw.fit_cadence(universe_size=9, other_daily_credits=276, daily_budget=800,
                         reserve=80, regular_sec=600, extended_sec=1800)
    first = tw.plan_tick(state, now_utc=T0, session="REGULAR", universe=universe["symbols"],
                         batch_cap=8, warm_daily_cap=560,
                         regular_sec=fit["regularSec"], extended_sec=fit["extendedSec"])
    tw.record_request(state, first, now_utc=T0, ok=False, rate_limited=True, backoff_sec=120)
    d = tw.diagnostics(state, plan="", batch_cap=8, universe=universe, warm_daily_cap=560,
                       regular_sec=fit["regularSec"], extended_sec=fit["extendedSec"],
                       session="REGULAR", enabled=True, api_key_present=True,
                       other_daily_credits=276, reserve=80, fit=fit,
                       now_utc=T0 + timedelta(seconds=30))
    assert d["plan"] == "basic" and d["minuteLimit"] == 8 and d["dailyLimit"] == 800
    assert d["otherConsumersDailyEstimate"] == 276
    assert d["estimatedTotalDailyCredits"] < 800 and d["totalWithinReserve"] is True
    assert d["cadenceFit"]["fitted"] is True and (
        d["cadenceFit"]["reducedFromRegularSec"] or d["cadenceFit"]["reducedFromExtendedSec"])
    assert d["backoff"]["active"] is True and d["backoff"]["until"] == "2026-09-08T14:02:00Z"
    assert d["usedToday"] == 8
    for s in NINE:
        assert s not in repr(d), s
