"""v13.5.36 — MARKET SITUATION BRIEF (NOW/WHY/NEXT) tests."""
import argus_market_brief as mb

import scanner


def _news(severity="HIGH", confirmed=False, direction=None, ja="米長期金利が上昇",
          family="US_TREASURY"):
    return {
        "severity": severity, "staleness": "FRESH_UPDATE",
        "confirmationState": "MARKET_CONFIRMED" if confirmed
        else "MARKET_CONFIRMATION_PENDING",
        "headlineJa": ja, "sourceFamily": family, "sourceLabelJa": "米財務省",
        "impactDirection": {
            "directionByTarget": direction or {"growth": "BEARISH"},
            "transmissionJa": "米長期金利↑→割引率上昇→高PER圧迫",
        },
    }


def test_compose_brief_orders_facts_and_tags_verification():
    brief = mb.compose_brief(
        now_iso="2026-08-26T00:00:00Z",
        market_view_summary={"label": "反転:混在・証拠評価5/7"},
        shock_events=[{"severity": "HIGH", "headlineJa": "米30年金利の急騰",
                       "whyJa": "財政懸念による債券売り"}],
        news_events=[_news(confirmed=True)],
        imminent_events=[{"title": "FOMC", "countdown": "D-1",
                          "displayImpact": "critical"}],
        next_events=[{"title": "米CPI", "countdown": "D-7"}])
    assert brief["schemaVersion"] == mb.BRIEF_SCHEMA
    assert brief["sdaAuthority"] is False
    assert brief["automaticAiCalls"] == 0
    priorities = [f["priority"] for f in brief["facts"]]
    assert priorities == sorted(priorities)          # P0 first, P3 last
    assert any(f["verification"] == "CORROBORATED" for f in brief["facts"])
    assert any(f["verification"] == "VERIFIED" for f in brief["facts"])
    assert brief["chips"]["nextEvent"].startswith("FOMC")
    assert "弱気材料が優勢" == brief["chips"]["news"]
    assert brief["now"] and brief["why"] and brief["next"]
    assert brief["hasCritical"] is False
    joined = str(brief)
    for banned in mb._FORBIDDEN_BRIEF_PATTERNS:
        assert banned not in joined


def test_compose_brief_is_honest_with_empty_inputs():
    brief = mb.compose_brief(now_iso="2026-08-26T00:00:00Z")
    assert "大きな新規材料は検知していません" in brief["now"]
    assert brief["chips"]["nextEvent"] == "直近の重要イベントなし"
    assert brief["chips"]["mainRisk"] == "特定の集中リスク検知なし"
    assert brief["aiText"] is None


def test_compose_brief_flags_critical_for_sol():
    brief = mb.compose_brief(
        now_iso="2026-08-26T00:00:00Z",
        news_events=[_news(severity="CRITICAL")])
    assert brief["hasCritical"] is True


def test_validate_ai_brief_rejects_invented_numbers_and_orders():
    facts = ["米長期金利が上昇（30年債 4.9%）", "FOMC D-1"]
    ok = mb.validate_ai_brief(
        {"nowJa": "金利上昇が重石。FOMC通過待ち。",
         "whyJa": "30年債 4.9%の高止まりが割引率を押し上げ。",
         "nextJa": "FOMC結果と金利の反応を確認。"}, facts)
    assert ok and "4.9" in ok["whyJa"]
    assert mb.validate_ai_brief(
        {"nowJa": "上昇確率72%とみられる。", "whyJa": "a", "nextJa": "b"},
        facts) is None
    assert mb.validate_ai_brief(
        {"nowJa": "今すぐ買いに行くべき局面。", "whyJa": "a", "nextJa": "b"},
        facts) is None
    assert mb.validate_ai_brief(
        {"nowJa": "指数は5.5%下落した。", "whyJa": "a", "nextJa": "b"},
        facts) is None                              # 5.5はfactに無い
    assert mb.validate_ai_brief({"nowJa": "x" * 300, "whyJa": "a",
                                 "nextJa": "b"}, facts) is None
    assert mb.validate_ai_brief(None, facts) is None


def test_market_brief_route_is_cached_only_and_public_safe(monkeypatch):
    monkeypatch.setattr(scanner, "_important_events_data", lambda: {
        "events": [{"title": "米CPI", "countdown": "D-7"}],
        "imminent": [{"title": "FOMC", "countdown": "D-1",
                      "displayImpact": "critical"}]})
    monkeypatch.setattr(scanner, "get_market_shock", lambda: {"events": []})
    monkeypatch.setattr(scanner, "_brief_market_view_summary",
                        lambda: {"label": "反転:混在"})
    monkeypatch.setattr(scanner, "_brief_news_events",
                        lambda: [_news(confirmed=True)])

    def _forbid_llm(*a, **k):
        raise AssertionError("public GET must never call the LLM")
    monkeypatch.setattr(scanner, "_openai_prose", _forbid_llm)
    scanner._MARKET_BRIEF["data"] = None
    scanner._MARKET_BRIEF["composedAt"] = 0.0
    scanner._MARKET_BRIEF["aiFactsHash"] = None
    try:
        response = scanner.app.test_client().get("/api/argus/market-brief")
        body = response.get_json()
        assert response.status_code == 200
        assert body["sdaAuthority"] is False
        assert body["aiText"] is None                # no LLM on public path
        assert body["chips"]["nextEvent"].startswith("FOMC")
        serialized = str(body).lower()
        for leak in ("holdings", "apikey", "x-api-key", "password"):
            assert leak not in serialized
    finally:
        scanner._MARKET_BRIEF["data"] = None
        scanner._MARKET_BRIEF["composedAt"] = 0.0


def test_market_brief_refresh_polishes_with_ai_and_caches_by_facts(monkeypatch):
    monkeypatch.setattr(scanner, "_important_events_data",
                        lambda: {"events": [], "imminent": []})
    monkeypatch.setattr(scanner, "get_market_shock", lambda: {"events": []})
    monkeypatch.setattr(scanner, "_brief_market_view_summary",
                        lambda: {"label": "反転:混在"})
    monkeypatch.setattr(scanner, "_brief_news_events",
                        lambda: [_news(confirmed=True)])
    calls = []

    def fake_prose(user, max_out=600, system=None, *, purpose="prose",
                   event_id="", event_phase="", model=None, diagnostic=None):
        calls.append((purpose, model))
        if isinstance(diagnostic, dict):
            diagnostic["requestedModel"] = model or scanner._OPENAI_MODEL
            diagnostic["returnedModel"] = "terra-served"
        return {"nowJa": "金利関連の弱気材料が重石。",
                "whyJa": "米財務省発の材料が市場確認済み。",
                "nextJa": "金利と指数の反応を確認。"}

    monkeypatch.setattr(scanner, "_openai_prose", fake_prose)
    scanner._MARKET_BRIEF["data"] = None
    scanner._MARKET_BRIEF["aiFactsHash"] = None
    try:
        brief = scanner._market_brief_refresh(allow_ai=True)
        assert brief["aiText"]["nowJa"].startswith("金利関連")
        assert brief["aiModel"] == "terra-served"
        assert calls == [("market_brief", None)]     # Terra, not Sol
        # unchanged facts → cached aiText, no second LLM call
        scanner._market_brief_refresh(allow_ai=True)
        assert len(calls) == 1
    finally:
        scanner._MARKET_BRIEF["data"] = None
        scanner._MARKET_BRIEF["aiFactsHash"] = None


def test_compose_brief_shows_placeholder_for_untranslated_material_news():
    ev = _news()
    ev["headlineJa"] = "翻訳処理中"
    brief = mb.compose_brief(now_iso="2026-08-26T00:00:00Z", news_events=[ev])
    p0 = [f["text"] for f in brief["facts"] if f["priority"] == "P0"]
    assert any("重要発表（日本語要約 処理中）" in t for t in p0)
    assert "翻訳処理中。" not in brief["now"]


def test_compose_brief_main_risk_uses_shock_headline():
    brief = mb.compose_brief(
        now_iso="2026-08-26T00:00:00Z",
        shock_events=[{"severity": "HIGH", "headlineJa": "米30年債利回り 4.98%",
                       "whyJa": "財政懸念"}])
    assert brief["chips"]["mainRisk"].startswith("米30年債利回り")


# ━━━ v13.5.36 — D05 autorefresh + brief chip enum (owner/GPT directives) ━━━

def test_investor_types_autorefresh_feeds_ledger_idempotently(monkeypatch):
    import argus_market_ledger
    old_state = dict(scanner._MARKET_LEDGER)
    scanner._MARKET_LEDGER.clear()
    scanner._MARKET_LEDGER.update(argus_market_ledger.empty_state())
    scanner._INVESTOR_TYPES_REFRESH["lastAt"] = 0.0
    calls = []
    monkeypatch.setattr(scanner, "_JQUANTS_API_KEY", "test-key")
    monkeypatch.setattr(scanner, "_jquants_paginated",
                        lambda path, params: calls.append(path) or [
                            {"PubDate": "2026-08-21",
                             "StDate": "2026-08-10", "EnDate": "2026-08-14",
                             "Section": "TokyoNagoya",
                             "FrgnBal": 123456}])
    monkeypatch.setattr(scanner, "_journal", lambda *a, **k: None)
    monkeypatch.setattr(scanner, "_osint_persist", lambda: None)
    try:
        scanner._investor_types_autorefresh()
        rows = [r for r in scanner._MARKET_LEDGER.get("observations") or []
                if r.get("seriesId") == "flow.foreign"]
        assert calls == ["/equities/investor-types"]
        assert rows, "flow.foreign rows must land in the ledger"
        assert all(r.get("availableFrom") for r in rows)   # PIT bound
        # 20h memo → second call is a no-op (no extra provider fetch)
        scanner._investor_types_autorefresh()
        assert calls == ["/equities/investor-types"]
        # memo expiry + same data → dedup keeps the ledger unchanged
        scanner._INVESTOR_TYPES_REFRESH["lastAt"] = 0.0
        before = len(scanner._MARKET_LEDGER.get("observations") or [])
        scanner._investor_types_autorefresh()
        assert len(scanner._MARKET_LEDGER.get("observations") or []) == before
    finally:
        scanner._MARKET_LEDGER.clear()
        scanner._MARKET_LEDGER.update(old_state)
        scanner._INVESTOR_TYPES_REFRESH["lastAt"] = 0.0


def test_brief_market_view_chip_counts_real_family_enum(monkeypatch):
    monkeypatch.setattr(scanner, "_sho_market_view", lambda: {
        "projection": {
            "reversal": {"reversalState": "RECOVERY_TEST",
                         "downsideState": "MIXED"},
            "families": {
                "D01": {"status": "AVAILABLE", "conditionMet": True},
                "D02": {"status": "AVAILABLE", "conditionMet": True},
                "D03": {"status": "AVAILABLE", "conditionMet": None},
                "D04": {"status": "LICENSE_BLOCKED"},
                "D05": {"status": "MISSING"},
                "D06": {"status": "MISSING"},
                "D07": {"status": "MISSING"},
            }}})
    label = scanner._brief_market_view_summary()["label"]
    assert "成立2/7" in label, label
    assert "反転:回復試験" in label and "下方:混在" in label


def test_same_day_event_keeps_its_slot_in_the_now_line():
    """Owner report 2026-09-04: Today read 「今: OFAC…。Nikkei…」 on a day whose
    US Employment Situation was at D-0. The NOW line took the first two P0
    facts in insertion order and news is appended before the calendar, so the
    single most decision-relevant fact of the day was dropped. A D/D-1 event is
    one short clause and must never lose its slot."""
    news = [{"severity": "HIGH", "sourceLabelJa": "米財務省",
             "headlineJa": "OFAC SDNリスト更新", "impactDirection": {}},
            {"severity": "HIGH", "sourceLabelJa": "Nikkei",
             "headlineJa": "国内住宅事業は資材高", "impactDirection": {}}]
    imminent = [{"title": "US Employment Situation", "countdown": "D",
                 "displayImpact": "high"}]
    upcoming = [{"title": "US Treasury 10-Year Auction", "countdown": "D-7"}]
    brief = mb.compose_brief(
        now_iso="2026-09-04T03:00:00Z", market_view_summary={"label": "混在"},
        news_events=news, imminent_events=imminent, next_events=upcoming)
    assert "US Employment Situation" in brief["now"], brief["now"]
    # The leading material headline is still reported, and nothing is invented.
    assert "OFAC" in brief["now"]
    # 次に確認 stays the forward calendar, not the same-day event.
    assert "Treasury" in brief["next"]

    # With no same-day event the previous two-headline behaviour is unchanged.
    plain = mb.compose_brief(
        now_iso="2026-09-04T03:00:00Z", market_view_summary={"label": "混在"},
        news_events=news, imminent_events=[], next_events=upcoming)
    assert "OFAC" in plain["now"] and "Nikkei" in plain["now"]


def test_two_untranslated_releases_do_not_repeat_the_same_now_sentence():
    """v13.5.54 (owner 2026-09-04). Production 「今」 read

        米財務省: 重要発表。米財務省: 重要発表

    because two DIFFERENT Treasury releases render to the same line while
    their Japanese summaries are pending. Repeating a sentence adds nothing
    and reads as a bug; collapse identical lines, keep the first, and do not
    invent a distinction the pending translation has not supplied.
    """
    pending = dict(_news(ja="重要発表（日本語要約 処理中）"), staleness="DELAYED")
    brief = mb.compose_brief(
        now_iso="2026-09-04T21:00:00Z",
        market_view_summary={"label": "反転:混在・証拠評価2/7"},
        shock_events=[],
        news_events=[dict(pending), dict(pending)],
        imminent_events=[],
        next_events=[{"title": "US Treasury 10-Year Auction",
                      "countdown": "D-7"}])
    p0 = [f for f in brief["facts"] if f["priority"] == "P0"]
    assert len(p0) == len({f["text"] for f in p0}), p0
    assert brief["now"].count("重要発表") == 1, brief["now"]


def test_distinct_headlines_are_still_both_carried():
    """Deduplication must collapse only IDENTICAL lines. Two releases whose
    Japanese summaries have landed say different things and both belong in
    the brief — the fix must not become a silent one-headline cap."""
    brief = mb.compose_brief(
        now_iso="2026-09-04T21:00:00Z",
        market_view_summary={"label": "反転:混在・証拠評価2/7"},
        shock_events=[],
        news_events=[_news(ja="米30年金利が急騰"),
                     _news(ja="対イラン制裁を追加指定")],
        imminent_events=[],
        next_events=[])
    p0_texts = [f["text"] for f in brief["facts"] if f["priority"] == "P0"]
    assert len(p0_texts) == 2, p0_texts
    assert "米30年金利が急騰" in brief["now"] and "対イラン制裁を追加指定" in brief["now"]
