"""Production-shaped regressions for the Important Events ranking hotfix.

The fixture mirrors the 2026-08-28 production failure: result-created macro
records have official facts but lack their schedule/ranking metadata, while old
Treasury PRE rows and one upcoming NFP share the bounded dashboard response.
"""
from copy import deepcopy

import argus_dashboard_event_summary as DS
import argus_macro_event_analysis as MA
import scanner


NOW = "2026-08-28T23:56:21Z"


def _result_skeleton(event_id, event_code, *, complete=False):
    """Old result-created shape: deliberately missing date/time/impact/source."""
    rec = {
        "schemaVersion": MA.SCHEMA_VERSION,
        "analysisId": f"ma-{event_id}",
        "eventId": event_id,
        "eventCode": event_code,
        "phase": "post_result",
        "pre": {},
        "actual": {
            "available": True,
            "headline": f"{event_code} official result",
            "metrics": {},
            "source": "official",
            "releasedAt": NOW,
        },
        "post": {},
        "marketReaction": {
            "riskTone": "mixed",
            "summaryJa": "市場反応を観測済み",
        },
        "firstSeenAt": NOW,
        "updatedAt": NOW,
    }
    if complete:
        rec["pre"] = {
            "summaryJa": "保存済みの事前見通し",
            "argusScenarioJa": "金利と株価の反応を確認",
            "generatedAt": "2026-07-13T12:00:00Z",
        }
        rec["post"] = {
            "verdict": "hit",
            "answerCheckJa": "答え合わせ済み",
            "generatedAt": "2026-07-14T13:00:00Z",
        }
    return rec


def _production_records():
    rows = [
        _result_skeleton("us-cpi-2026-07-14", "CPI", complete=True),
        _result_skeleton("us-fomc-2026-07-29", "FOMC"),
        _result_skeleton("us-pce-2026-07-30", "PCE"),
        _result_skeleton("us-gdp-2026-07-30", "GDP"),
        _result_skeleton("us-nfp-2026-08-07", "NFP"),
        _result_skeleton("us-cpi-2026-08-12", "CPI"),
        # Production hero candidate: official result + reaction, but no pre/post AI.
        _result_skeleton("us-pce-2026-08-26", "PCE"),
        _result_skeleton("us-gdp-2026-08-26", "GDP"),
    ]
    for date in ("2026-07-08", "2026-07-09", "2026-07-22"):
        rows.append({
            "eventId": f"us-treasury-auction-{date}",
            "eventCode": "TREASURY_AUCTION",
            "displayImpact": "high",
            "phase": "pre_watch",
            "pre": {"summaryJa": "古い入札監視"},
            "actual": {"available": False},
            "post": {},
        })
    return rows


def _upcoming_nfp():
    # ImportantEvent's production field is `date`, not `eventDate`.
    return {
        "eventId": "us-nfp-2026-09-04",
        "eventCode": "NFP",
        "title": "US Employment Situation",
        "eventTimeUtc": "2026-09-04T12:30:00Z",
        "date": "2026-09-04",
        "displayImpact": "high",
        "source": "Bureau of Labor Statistics",
        "daysUntil": 7,
        "countdown": "D-7",
    }


def _seed_production_shape(monkeypatch):
    records = _production_records()
    monkeypatch.setattr(scanner, "_MACRO_ANALYSIS",
                        {row["eventId"]: row for row in records})
    monkeypatch.setitem(scanner._MACRO_ANALYSIS_STATE, "restored", True)
    monkeypatch.setattr(scanner, "_ai_now_iso", lambda: NOW)
    monkeypatch.setattr(scanner, "_macro_important_events",
                        lambda limit=8: [_upcoming_nfp()][:limit])


def test_result_created_lifecycle_preserves_schedule_ranking_metadata():
    event = {
        "id": "us-pce-2026-08-26",
        "eventCode": "PCE",
        "category": "inflation",
        "title": "US PCE / Personal Income & Outlays",
        "eventTimeUtc": "2026-08-26T12:30:00Z",
        "date": "2026-08-26",
        "localTimeJst": "2026-08-26 21:30 JST",
        "displayImpact": "high",
        "source": "Bureau of Economic Analysis",
        "linkedAssets": ["US10Y", "USDJPY", "QQQ"],
        "daysUntil": 0,
        "countdown": "D",
    }
    rec = MA.new_record(event, now_iso=NOW)
    rec["actual"] = {"available": True, "headline": "PCE official result"}
    rec["marketReaction"] = {"summaryJa": "市場反応を観測済み"}
    fixed = MA.rehydrate_schedule_metadata(rec, event)

    assert fixed["eventId"] == event["id"]
    assert fixed["eventCode"] == "PCE"
    assert fixed["eventFamily"] == "inflation"
    assert fixed["eventTimeUtc"] == "2026-08-26T12:30:00Z"
    assert fixed["eventDate"] == "2026-08-26"
    assert fixed["displayImpact"] == "high"
    assert fixed["source"] == "Bureau of Economic Analysis"
    assert fixed["linkedAssets"] == ["US10Y", "USDJPY", "QQQ"]
    assert fixed["actual"]["available"] is True
    assert fixed["marketReaction"]["summaryJa"]


def test_generic_rehydration_repairs_catalog_rows_and_canonical_dates():
    pce = MA.rehydrate_schedule_metadata(
        _result_skeleton("us-pce-2026-08-26", "PCE"))
    cpi = MA.rehydrate_schedule_metadata(
        _result_skeleton("us-cpi-2026-08-12", "CPI"))
    auction = MA.rehydrate_schedule_metadata({
        "eventId": "us-treasury-auction-2026-07-22",
        "eventCode": "TREASURY_AUCTION",
        "displayImpact": "high",
    })

    assert (pce["eventDate"], pce["eventTimeUtc"], pce["displayImpact"], pce["source"]) == (
        "2026-08-26", "2026-08-26T12:30:00Z", "high",
        "Bureau of Economic Analysis")
    assert (cpi["eventDate"], cpi["eventTimeUtc"], cpi["displayImpact"], cpi["source"]) == (
        "2026-08-12", "2026-08-12T12:30:00Z", "high",
        "Bureau of Labor Statistics")
    assert auction["eventDate"] == "2026-07-22"
    assert MA.canonical_date_from_event_id("event-2026-02-30") is None


def test_production_shaped_ordering_is_correct_before_limit(monkeypatch):
    _seed_production_shape(monkeypatch)

    _, full, _ = scanner._build_dashboard_events(limit=20)
    _, bounded, _ = scanner._build_dashboard_events(limit=8)
    full_ids = [item["eventId"] for item in full]
    bounded_ids = [item["eventId"] for item in bounded]

    # v13.5.51 canonical lifecycle: tomorrow-ish NFP (NEXT) leads; the 08-26
    # releases (59h old, results in) are RECENT; everything older is HISTORY
    # and leaves the current surface (a released PCE never outranks NFP).
    expected = [
        "us-nfp-2026-09-04",
        "us-gdp-2026-08-26",
        "us-pce-2026-08-26",
    ]
    assert full_ids == expected
    assert bounded_ids == full_ids == expected
    assert bounded[0]["lifecycleTier"] == "NEXT" and bounded[0]["isHero"] is True
    assert bounded[0]["importance"] == "high" and bounded[0]["state"] == "pre"
    assert bounded[1]["lifecycleTier"] == "RECENT" and bounded[1]["isHero"] is False
    assert bounded[1]["state"] == "post_result" and bounded[1]["officialResult"]["available"] is True
    assert bounded[2]["caos"]["verdict"] == "not_scoreable"
    assert "us-cpi-2026-07-14" not in bounded_ids
    assert not any("treasury-auction" in event_id for event_id in bounded_ids)
    completed_times = [item["eventTimeUtc"] or item["eventDate"]
                       for item in full if item["state"] != "pre"]
    assert completed_times == sorted(completed_times, reverse=True)


def test_optional_ai_analysis_cannot_change_importance_or_order(monkeypatch):
    _seed_production_shape(monkeypatch)
    _, before, _ = scanner._build_dashboard_events(limit=20)
    before_ids = [item["eventId"] for item in before]

    pce = deepcopy(scanner._MACRO_ANALYSIS["us-pce-2026-08-26"])
    pce["displayImpact"] = "medium"  # legacy/AI-side fallback must be raised.
    pce["post"] = {
        "verdict": "partial",
        "answerCheckJa": "optional answer check",
        "generatedAt": NOW,
    }
    scanner._MACRO_ANALYSIS["us-pce-2026-08-26"] = pce
    _, after, _ = scanner._build_dashboard_events(limit=20)

    assert [item["eventId"] for item in after] == before_ids
    pce_after = next(item for item in after if item["eventId"] == "us-pce-2026-08-26")
    assert pce_after["importance"] == "high"
    assert pce_after["state"] == "post_answer_checked"
    assert after[0]["eventId"] == "us-nfp-2026-09-04"            # AI completeness never moves the hero


def test_high_pending_result_is_fail_visible_ahead_of_old_complete_event():
    pending = {
        "eventId": "us-cpi-2026-08-28",
        "eventCode": "CPI",
        "title": "US CPI",
        "eventTimeUtc": "2026-08-28T23:30:00Z",
        "eventDate": "2026-08-28",
        "displayImpact": "high",
        "actual": {"available": False},
        "post": {},
    }
    old_complete = MA.rehydrate_schedule_metadata(
        _result_skeleton("us-cpi-2026-07-14", "CPI", complete=True))

    out = DS.build_summary(important_events=[], macro_records=[old_complete, pending],
                           now_iso=NOW, limit=1)
    assert out["items"][0]["eventId"] == "us-cpi-2026-08-28"
    assert out["items"][0]["state"] == "released_pending_result"
    assert out["items"][0]["display"]["showPendingResult"] is True


def test_undated_stale_pre_cannot_crow_a_bounded_response():
    valid = []
    for day in range(20, 28):
        valid.append({
            "eventId": f"us-valid-2026-08-{day}",
            "eventCode": f"V{day}",
            "eventDate": f"2026-08-{day}",
            "displayImpact": "high",
            "actual": {"available": True},
            "post": {},
        })
    undated = {
        "eventId": "legacy-treasury-row",
        "eventCode": "TREASURY_AUCTION",
        "displayImpact": "high",
        "phase": "pre_watch",
        "actual": {"available": False},
    }

    out = DS.build_summary(important_events=[], macro_records=[undated, *valid],
                           now_iso=NOW, limit=8)
    ids = [item["eventId"] for item in out["items"]]
    assert "legacy-treasury-row" not in ids                       # undated → HISTORY, never current
    assert ids and all(item["lifecycleTier"] != "HISTORY" for item in out["items"])
    assert ids[0] == "us-valid-2026-08-27"                        # newest completed first
    assert out["dedupe"]["historyCount"] >= 1
    assert all(item["isHero"] is False for item in out["items"])  # RECENT is secondary only



def test_lifecycle_tiers_age_out_and_hero_prefers_next_over_released():
    IE = __import__("argus_important_events")
    now = 1_000_000_000.0
    day = 86400.0
    assert IE.lifecycle_tier(event_epoch=now + 0.5 * day, now_epoch=now, importance="high") == "NOW"
    assert IE.lifecycle_tier(event_epoch=now + 0.5 * day, now_epoch=now, importance="medium") == "NEXT"
    assert IE.lifecycle_tier(event_epoch=now + 3 * day, now_epoch=now, importance="high") == "NEXT"
    assert IE.lifecycle_tier(event_epoch=now + 12 * day, now_epoch=now, importance="high") == "LATER"
    assert IE.lifecycle_tier(event_epoch=now + 45 * day, now_epoch=now, importance="high") == "HORIZON"
    assert IE.lifecycle_tier(event_epoch=now - 2 * 3600, now_epoch=now, importance="high", actual_available=True) == "NOW"
    assert IE.lifecycle_tier(event_epoch=now - 5 * 3600, now_epoch=now, importance="high", actual_available=False) == "MONITORING"
    assert IE.lifecycle_tier(event_epoch=now - 2 * day, now_epoch=now, importance="high", actual_available=True) == "RECENT"
    assert IE.lifecycle_tier(event_epoch=now - 8 * day, now_epoch=now, importance="critical", actual_available=True) == "HISTORY"
    assert IE.lifecycle_tier(event_epoch=None, now_epoch=now, importance="high") == "HISTORY"
    nfp = IE.canonical_rank_key(tier="NEXT", importance="high", owner_relevance="normal",
                                event_epoch=now + day, now_epoch=now, result_available=False, event_id="nfp")
    pce = IE.canonical_rank_key(tier="RECENT", importance="critical", owner_relevance="critical",
                                event_epoch=now - 2 * day, now_epoch=now, result_available=True, event_id="pce")
    assert nfp < pce                                               # tier precedes importance/relevance
    assert IE.lifecycle_tier_from_days(1, importance="high") == "NOW"
    assert IE.lifecycle_tier_from_days(-5, importance="high", actual_available=True) == "HISTORY"


def test_both_lifecycle_entry_points_agree_on_the_countdown_day():
    """v13.5.51 states ONE lifecycle for Today and the dashboard, but the two
    entry points cut the forward windows differently: lifecycle_tier() used
    elapsed seconds while lifecycle_tier_from_days() used the schedule's
    calendar days. Measured on 2026-09-04, US CPI on 2026-09-11 was NEXT on
    /important-events and LATER on /dashboard-events while both records carried
    the same D-7 label. The windows are stated in DAYS and the countdown label
    is computed from the Asia/Tokyo calendar-day distance, so both entry points
    must read the tier off that same integer."""
    import datetime as _dt

    ie = __import__("argus_important_events")
    jst = _dt.timezone(_dt.timedelta(hours=9))
    # 2026-09-04 14:02 JST — the exact clock that produced the mismatch.
    now = _dt.datetime(2026, 9, 4, 14, 2, tzinfo=jst).timestamp()
    # 2026-09-11 21:30 JST (CPI, 08:30 ET): 7 calendar days out, 7.31 elapsed.
    cpi = _dt.datetime(2026, 9, 11, 21, 30, tzinfo=jst).timestamp()
    assert ie.calendar_days_until(cpi, now) == 7
    assert ie.lifecycle_tier(event_epoch=cpi, now_epoch=now,
                             importance="high") == "NEXT"

    # Exhaustive agreement across the forward windows, for every hour of the
    # day, at both importance levels — a boundary can never drift again. Only
    # events that have NOT been released are compared: once the release instant
    # passes, the day count can no longer express NOW vs MONITORING vs RECENT
    # (that distinction is sub-daily, RESULT_SLA_HOURS), so the instant is the
    # only faithful input and the builder switches to it on both surfaces.
    for importance in ("high", "medium"):
        for days in range(0, 45):
            for hour in (0, 6, 9, 15, 21, 23):
                event = _dt.datetime(2026, 9, 4, tzinfo=jst) \
                    + _dt.timedelta(days=days, hours=hour)
                epoch = event.timestamp()
                if epoch < now:
                    continue
                assert ie.lifecycle_tier(
                    event_epoch=epoch, now_epoch=now, importance=importance) \
                    == ie.lifecycle_tier_from_days(
                        ie.calendar_days_until(epoch, now), importance=importance), (
                    days, hour, importance)

    # A released same-day event must read the same on both surfaces: the
    # builder hands it to the instant path exactly as the dashboard summary
    # does, so 「いま」 does not appear on one surface and 「結果待ち(監視)」 on the
    # other for the same record. The clock is pinned through the key the
    # builder actually reads (nowIso) — an unrecognised key silently falls back
    # to the real wall clock and makes this assertion time-dependent.
    now_iso = "2026-09-04T05:02:00Z"                       # 14:02 JST
    pinned = _dt.datetime(2026, 9, 4, 5, 2, tzinfo=_dt.timezone.utc).timestamp()
    released = _dt.datetime(2026, 9, 4, 3, 30, tzinfo=_dt.timezone.utc)   # 12:30 JST
    row = {"id": "us-nfp-2026-09-04", "kind": "nfp", "title": "NFP",
           "impact": "high", "daysUntil": 0, "escalation": "D",
           "linkedAssets": ["US10Y"], "eventDate": "2026-09-04",
           "eventTimeUtc": released.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rationaleJa": "x", "source": "BLS", "status": "live"}
    built = ie.build_important_events([row], ctx={"nowIso": now_iso})
    assert built, "a released same-day event must stay on the surface"
    assert built[0]["lifecycleTier"] == ie.lifecycle_tier(
        event_epoch=released.timestamp(), now_epoch=pinned, importance="high")

    # ...and once the result SLA lapses it is still reported honestly as
    # 結果待ち, while ranking in the RECENT band so the bounded surface cannot
    # drop today's release below a routine event twelve days out (owner report
    # 2026-09-04: the US Employment Situation disappeared six hours after it
    # fired).
    # The pinned clock must actually be honoured. An unrecognised key falls
    # back to the real wall clock, which is indistinguishable from a correct
    # one until the calendar moves — exactly how `nowEpoch` slipped through.
    assert ie._now_epoch_from_ctx({"nowIso": now_iso}) == pinned
    assert ie._now_epoch_from_ctx({"nowEpoch": pinned}) != pinned, (
        "nowEpoch is not a recognised clock key; tests must pin nowIso")

    # Inside the 3h result SLA a strong release is still 「いま」.
    assert built[0]["lifecycleTier"] == "NOW"

    # Past that SLA it becomes 結果待ち — and THAT is where it used to fall off
    # the bounded surface.
    lapsed = _dt.datetime(2026, 9, 3, 23, 0, tzinfo=_dt.timezone.utc)   # 6h before now
    lapsed_row = {**row, "id": "us-nfp-lapsed",
                  "eventTimeUtc": lapsed.strftime("%Y-%m-%dT%H:%M:%SZ")}
    lapsed_built = ie.build_important_events([lapsed_row], ctx={"nowIso": now_iso})
    assert lapsed_built and lapsed_built[0]["lifecycleTier"] == "MONITORING", (
        lapsed_built and lapsed_built[0]["lifecycleTier"])

    monitoring_rank = ie.canonical_rank_key(
        tier="MONITORING", importance="high", owner_relevance="normal",
        event_epoch=released.timestamp(), now_epoch=pinned,
        result_available=False, event_id="us-nfp-2026-09-04")
    later_rank = ie.canonical_rank_key(
        tier="LATER", importance="high", owner_relevance="normal",
        event_epoch=pinned + 12 * 86400, now_epoch=pinned,
        result_available=False, event_id="us-fomc-2026-09-16")
    assert monitoring_rank < later_rank, (monitoring_rank, later_rank)
    # Ageing out is untouched: beyond the 72h window the tier is HISTORY.
    assert ie.lifecycle_tier(event_epoch=pinned - 8 * 86400, now_epoch=pinned,
                             importance="high") == "HISTORY"
