"""v13.5.14 decision-evidence route — canonical artifact references for the
device-side SDA.

Contract under test (owner spec 2026-08-22):
  * a FRESH live quote yields AVAILABLE marketTruth + predictionLedger + sho
    references with quality COMPLETE/FRESH — the reviewed backend half of the
    frontend's canonical_artifact_resolver_unavailable boundary;
  * a merely DELAYED/stale selection degrades to an honest STALE reference
    (identity kept, authority withheld) — never a fabricated AVAILABLE;
  * absent inputs fail closed to MISSING with reasons, HTTP 200, no network;
  * SHO stays UNVALIDATED / shoBuyEligible False (BUY remains locked).
"""
from datetime import datetime, timedelta, timezone

import scanner
import argus_single_decision


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fresh_jp_row(now):
    return {
        "symbol": "1321", "name": "日経225連動型上場投資信託",
        "status": "live", "price": 68330.0, "changePct": -1.2,
        "volume": 123456.0,
        "receivedAt": _iso(now - timedelta(seconds=30)),
        "sourceTimestamp": _iso(now - timedelta(seconds=60)),
        "source": "jquants",
    }


def _client():
    scanner._DECISION_EVIDENCE_CACHE.clear()
    return scanner.app.test_client()


def test_fresh_live_quote_yields_full_available_chain(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setattr(scanner, "get_japan_watchlist_snapshot",
                        lambda **kwargs: {"provider": "jquants",
                                          "stocks": [_fresh_jp_row(now)]})
    body = _client().get(
        "/api/argus/decision-evidence?symbols=1321").get_json()
    entry = body["subjects"]["1321"]
    assert entry["marketTruth"]["status"] == "AVAILABLE"
    assert entry["predictionLedger"]["status"] == "AVAILABLE"
    assert entry["predictionLedger"]["mode"] == "FORWARD_LIVE"
    assert entry["sho"]["status"] == "AVAILABLE"
    # BUY stays locked: nothing here may claim a validated SHO state.
    assert entry["sho"]["validationStatus"] in ("UNVALIDATED", "DATA_GATED")
    assert entry["shoBuyEligible"] is False
    assert entry["quality"] == {"status": "COMPLETE", "freshness": "FRESH",
                                "missingReasonCodes": [],
                                "conflictReasonCodes": []}
    # The references must be byte-reproducible by the canonical wrapper —
    # AVAILABLE identity fields are complete.
    for key in ("schemaVersion", "snapshotId", "observationId",
                "observedAt", "knownAt", "policyId", "policySha256"):
        assert entry["marketTruth"][key], key
    assert body["sdaAuthority"] is False
    assert body["actionAuthority"] is False


def test_latest_session_close_is_daily_authority_available(monkeypatch):
    """SHOの日次思考: 最新完了セッションの公式終値は「現在の事実」— 週末や
    連休の壁時計経過でSTALE扱いにしない(owner spec 2026-08-22)。"""
    import argus_market_clock
    now = datetime.now(timezone.utc)
    session = argus_market_clock.latest_completed_session_date(
        argus_market_clock.JP_EQUITY, now)
    row = dict(_fresh_jp_row(now), status="delayed",
               sourceTimestamp=f"{session.isoformat()}T15:30:00+09:00")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setattr(scanner, "get_japan_watchlist_snapshot",
                        lambda **kwargs: {"provider": "jquants", "stocks": [row]})
    body = _client().get(
        "/api/argus/decision-evidence?symbols=1321").get_json()
    entry = body["subjects"]["1321"]
    assert entry["marketTruth"]["status"] == "AVAILABLE"
    assert entry["predictionLedger"]["status"] == "AVAILABLE"
    assert entry["quality"] == {"status": "COMPLETE", "freshness": "FRESH",
                                "missingReasonCodes": [],
                                "conflictReasonCodes": []}


def test_old_session_close_degrades_to_stale_reference_not_available(monkeypatch):
    import argus_market_clock
    now = datetime.now(timezone.utc)
    session = argus_market_clock.latest_completed_session_date(
        argus_market_clock.JP_EQUITY, now)
    old_session = session - timedelta(days=7)
    row = dict(_fresh_jp_row(now), status="delayed",
               sourceTimestamp=f"{old_session.isoformat()}T15:30:00+09:00",
               receivedAt=_iso(now - timedelta(hours=1)))
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setattr(scanner, "get_japan_watchlist_snapshot",
                        lambda **kwargs: {"provider": "jquants", "stocks": [row]})
    body = _client().get(
        "/api/argus/decision-evidence?symbols=1321").get_json()
    entry = body["subjects"]["1321"]
    assert entry["marketTruth"]["status"] == "STALE"
    # identity survives, authority does not
    assert entry["marketTruth"]["snapshotId"]
    assert entry["predictionLedger"]["status"] == "MISSING"
    assert entry["quality"]["status"] == "PARTIAL"
    assert entry["quality"]["freshness"] == "STALE"
    assert "market_truth_stale" in entry["quality"]["missingReasonCodes"]
    assert entry["verificationFailures"]["marketTruth"] == \
        "subject_selection_not_fresh"


def test_absent_inputs_fail_closed_to_missing_http_200(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setattr(scanner, "get_japan_watchlist_snapshot",
                        lambda **kwargs: {"provider": "jquants", "stocks": []})
    response = _client().get("/api/argus/decision-evidence?symbols=1321")
    assert response.status_code == 200
    entry = response.get_json()["subjects"]["1321"]
    assert entry["marketTruth"]["status"] == "MISSING"
    assert entry["marketTruth"]["snapshotId"] is None
    assert entry["verificationFailures"]["marketTruth"] == \
        "quote_row_unavailable"
    assert entry["quality"]["status"] in ("PARTIAL", "MISSING")


def test_references_match_python_authority_resolver(monkeypatch):
    """The published references must be exactly what verify_decision_evidence
    accepts — same builders, same seals, no drift."""
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setattr(scanner, "get_japan_watchlist_snapshot",
                        lambda **kwargs: {"provider": "jquants",
                                          "stocks": [_fresh_jp_row(now)]})
    scanner._DECISION_EVIDENCE_CACHE.clear()
    cutoff = scanner._ai_now_iso()
    build_identity = "a" * 40
    artifact, reason = scanner._decision_evidence_market_artifact(
        "1321", "JP", cutoff, build_identity)
    assert artifact is not None and reason is None
    prediction, p_reason = scanner._decision_evidence_prediction_artifact(
        "1321", "JP", cutoff, artifact, build_identity)
    assert prediction is not None and p_reason is None
    sho_artifact, s_reason = scanner._decision_evidence_sho_artifact(
        "1321", cutoff)
    assert sho_artifact is not None and s_reason is None
    references = argus_single_decision.canonical_artifact_references(
        subject={"kind": "ASSET", "instrumentId": "1321", "market": "JP"},
        cutoff=cutoff,
        market_truth_artifact=artifact,
        prediction_ledger_artifact=prediction,
        sho_artifact=sho_artifact)
    assert references["marketTruth"]["status"] == "AVAILABLE"
    assert references["predictionLedger"]["contextId"] == prediction["id"]
    assert references["sho"]["artifactId"] == sho_artifact["artifactId"]
    assert references["verificationFailures"] == {}


# ━━━ v13.5.36 — SHO CORE production wiring + MARKET VIEW (review items A/B/C) ━━━

def _reset_sho_memos():
    scanner._SHO_PIT_INPUT_MEMO.update({"ts": 0.0, "data": None})
    scanner._SHO_MARKET_VIEW_MEMO.update({"ts": 0.0, "view": None})
    scanner._SHO_INDEX_OHLCV_CACHE.clear()


def _fixture_index_bars(instrument_id, days, *, base, step, volume,
                        next_day_available):
    now = datetime.now(timezone.utc)
    rows = []
    for offset in range(days, 0, -1):
        date = (now - timedelta(days=offset)).date()
        value = base + (days - offset) * step
        if next_day_available:
            available = (date + timedelta(days=1)).isoformat() + "T00:00:00Z"
        else:
            available = date.isoformat() + "T07:00:00Z"
        rows.append({
            "instrumentId": instrument_id, "date": date.isoformat(),
            "open": value, "high": value + 1.0, "low": max(value - 1.0, 0.5),
            "close": value, "volume": volume,
            "availableFrom": available, "adjusted": False,
            "sourceRef": "fixture",
        })
    return rows


def _seed_sho_inputs():
    now = datetime.now(timezone.utc)
    credit = []
    for week in range(6, 0, -1):
        period = (now - timedelta(days=7 * week)).date()
        available = (period + timedelta(days=4)).isoformat() + "T00:00:00Z"
        credit.append({"seriesId": "credit.short_balance",
                       "periodEnd": period.isoformat(),
                       "availableFrom": available, "value": 6.5e11})
        credit.append({"seriesId": "credit.long_balance",
                       "periodEnd": period.isoformat(),
                       "availableFrom": available, "value": 3.4e12})
    margin_date = (now - timedelta(days=10)).date()
    rs_date = (now - timedelta(days=2)).date()
    data = {
        "creditRows": credit,
        "margin1570Rows": [{
            "instrumentId": "1570", "field": "margin_ratio",
            "date": margin_date.isoformat(), "value": 1.42,
            "availableFrom": (margin_date + timedelta(days=7)).isoformat()
            + "T00:00:00Z"}],
        "rsProxy": {"instrumentId": "1321",
                    "seriesId": "relative_strength_20d",
                    "date": rs_date.isoformat(), "value": 0.0123,
                    "availableFrom": rs_date.isoformat() + "T07:00:00Z"},
        "flowRows": [],
        "vixRows": _fixture_index_bars("VIX", 45, base=16.0, step=0.1,
                                       volume=0.0, next_day_available=True),
        "nikkeiRows": _fixture_index_bars("NIKKEI_225_INDEX", 45,
                                          base=64000.0, step=40.0,
                                          volume=150000000.0,
                                          next_day_available=False),
        "sourceStatus": {"credit": "csv_ledger", "margin1570": "jquants_weekly",
                         "relativeStrength": "etf_proxy_20d",
                         "foreignFlow": "missing", "vix": "yahoo_ohlcv",
                         "nikkei": "yahoo_ohlcv"},
    }
    scanner._SHO_PIT_INPUT_MEMO.update(
        {"ts": scanner.time.time(), "data": data})


def test_market_view_projects_real_family_states(monkeypatch):
    """Review item B: D01-D07 evaluate the wired production feeds, and the
    document-level MARKET VIEW (item A) projects them with zero authority."""
    _reset_sho_memos()
    scanner._SHO_MARKET_VIEW_MEMO.update({"ts": 0.0, "view": None})
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setattr(scanner, "get_japan_watchlist_snapshot",
                        lambda **kwargs: {"provider": "jquants", "stocks": []})
    _seed_sho_inputs()
    try:
        body = _client().get(
            "/api/argus/decision-evidence?symbols=1321").get_json()
        view = body["marketView"]
        assert view["schemaVersion"] == "argus-sho-market-view-v1"
        assert view["actionAuthority"] is False
        projection = view["projection"]
        assert projection["actionAuthority"] is False
        assert projection["action"] is None
        families = projection["families"]
        assert families["D01"]["status"] == "AVAILABLE"
        assert families["D01"]["conditionMet"] is True          # 6.5e11 < 8e11
        assert families["D02"]["status"] == "AVAILABLE"
        assert families["D03"]["status"] == "AVAILABLE"
        assert families["D03"]["lineage"] == "ARGUS_CANDIDATE"  # ETF proxy lane
        assert families["D05"]["status"] == "MISSING"           # no flow feed
        assert families["D06"]["status"] == "AVAILABLE"
        assert families["D07"]["status"] == "MISSING"
        # Nothing here may claim a validated state or a probability.
        for family in families.values():
            assert family["validationStatus"] == "UNVALIDATED"
        reversal = projection["reversal"]
        assert reversal is not None
        assert reversal["reversalState"], "real bars must classify an axis"
        assert view["sourceStatus"]["vix"] == "yahoo_ohlcv"
    finally:
        _reset_sho_memos()


def test_market_view_cold_inputs_stay_missing_not_fabricated(monkeypatch):
    """Cold caches must yield MISSING families — never invented evidence."""
    _reset_sho_memos()
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setattr(scanner, "get_japan_watchlist_snapshot",
                        lambda **kwargs: {"provider": "jquants", "stocks": []})
    monkeypatch.setattr(scanner, "_jpx_credit_rows_effective", lambda: [])
    monkeypatch.setattr(scanner, "_chart_history_cached",
                        lambda symbol, market: [])
    try:
        body = _client().get(
            "/api/argus/decision-evidence?symbols=1321").get_json()
        families = body["marketView"]["projection"]["families"]
        for name in ("D01", "D02", "D03", "D05", "D06", "D07"):
            assert families[name]["status"] in ("MISSING", "LICENSE_BLOCKED")
            assert families[name]["conditionMet"] is None
    finally:
        _reset_sho_memos()


def test_important_events_imminent_feed_is_uncapped(monkeypatch):
    """Review item C: the D/D-1 constraint feed must not depend on the 8-item
    display cap (event #9+ silently produced no constraint)."""
    events = [{
        "id": f"ev-{index}", "kind": f"macro{index}",
        "title": f"Macro event {index}", "impact": "high",
        "escalation": "D-1", "daysUntil": 1,
        "eventDate": "2026-08-23", "linkedAssets": ["QQQ"],
        "status": "scheduled",
    } for index in range(12)]
    monkeypatch.setattr(scanner, "get_events_snapshot",
                        lambda: {"status": "ok", "asOf": "now",
                                 "events": events})
    body = scanner.app.test_client().get(
        "/api/argus/important-events").get_json()
    # v13.5.60: the display list covers the coming month once the Recovery
    # payload lands (cap `_IMPORTANT_EVENTS_DISPLAY_CAP`); before that it is
    # eight rows. Either way the imminent feed stays uncapped.
    assert len(body["events"]) <= getattr(scanner, "_IMPORTANT_EVENTS_DISPLAY_CAP", 8)
    assert len(body["imminent"]) >= 12
    for row in body["imminent"]:
        assert row["countdown"] in ("D", "D-1")
        assert row["displayImpact"]
        assert isinstance(row["linkedAssets"], list)


def test_yahoo_index_mapper_drops_incomplete_bars(monkeypatch):
    """The OHLCV mapper passes source values through exactly (volume 0 for
    ^VIX is the reported value) and DROPS bars with any null component —
    components are never filled."""
    _reset_sho_memos()
    canned = {
        "chart": {"result": [{
            "meta": {"gmtoffset": -18000},
            "timestamp": [1787122800, 1787209200, 1787295600],
            "indicators": {"quote": [{
                "open": [15.9, 14.9, None], "high": [16.0, 16.1, 15.9],
                "low": [14.7, 14.9, 15.0], "close": [14.9, 16.0, 15.1],
                "volume": [0, 0, 0],
            }]},
        }]}}

    class _Resp:
        status_code = 200
        def json(self):
            return canned
    monkeypatch.setattr(scanner.requests, "get",
                        lambda *args, **kwargs: _Resp())
    try:
        rows = scanner._yahoo_index_ohlcv("^VIX", "VIX", fetch=True,
                                          next_day_available=True)
        assert len(rows) == 2                     # null-open bar dropped
        assert all(row["volume"] == 0.0 for row in rows)
        assert all(row["instrumentId"] == "VIX" for row in rows)
        for row in rows:
            assert row["availableFrom"] > row["date"]
    finally:
        _reset_sho_memos()


def test_history_fallback_selects_latest_bar_regardless_of_row_order(monkeypatch):
    """v13.5.36 本番バグ再現: 本番キャッシュは新しい順で届き、closes[-1]が
    最古バー(2016年)を掴んで日次権限が正しく拒否→全夕方判断がdata-gated。
    並び順に依存せず最新日付の終値を選ぶこと。"""
    import argus_market_clock
    now = datetime.now(timezone.utc)
    session = argus_market_clock.latest_completed_session_date(
        argus_market_clock.JP_EQUITY, now)
    prev = session - timedelta(days=1)
    descending = [
        {"date": session.isoformat(), "close": 68220.0},
        {"date": prev.isoformat(), "close": 68000.0},
        {"date": "2016-09-06", "close": 12000.0},
    ]
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setattr(scanner, "get_japan_watchlist_snapshot",
                        lambda **kwargs: {"provider": "jquants", "stocks": []})
    monkeypatch.setattr(scanner, "_chart_history_cached",
                        lambda symbol, market: descending)
    scanner._DECISION_EVIDENCE_CACHE.clear()
    body = _client().get(
        "/api/argus/decision-evidence?symbols=1321").get_json()
    entry = body["subjects"]["1321"]
    assert str(entry["marketTruth"]["observedAt"])[:10] == session.isoformat()
    assert entry["marketTruth"]["status"] == "AVAILABLE"
    assert entry["predictionLedger"]["status"] == "AVAILABLE"
    scanner._DECISION_EVIDENCE_CACHE.clear()
