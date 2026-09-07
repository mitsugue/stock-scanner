import datetime as dt
import json
import pathlib
import sys
import types
import unittest
from contextlib import contextmanager
from unittest import mock

import argus_market_ledger as ml
import argus_today_intelligence as ti

sys.modules.setdefault("moomoo", types.SimpleNamespace(
    OpenQuoteContext=object, OpenSecTradeContext=object, RET_OK=0))
import scanner


def provider_history(count=420, start=100.0):
    rows = []
    day = dt.date(2024, 1, 1)
    value = start
    while len(rows) < count:
        if day.weekday() < 5:
            index = len(rows)
            open_ = value * (1 + ((index % 7) - 3) * .0005)
            close = value * (1 + ((index % 17) - 7) * .00035 + .0003)
            rows.append((day.isoformat(), open_, max(open_, close) * 1.01,
                         min(open_, close) * .99, close, 1_000_000 + index * 100))
            value = close
        day += dt.timedelta(days=1)
    rows.reverse()
    return {"dates": [x[0] for x in rows], "opens": [x[1] for x in rows],
            "highs": [x[2] for x in rows], "lows": [x[3] for x in rows],
            "closes": [x[4] for x in rows], "volumes": [x[5] for x in rows]}


@contextmanager
def price_cache(fake):
    # The synthetic cache has a deliberately distant expiry; bind the actual
    # provider revision to a truthful, deterministic acquisition instant so
    # strict point-in-time filtering does not interpret expiry-minus-TTL as a
    # future knowledge timestamp.
    acquired_at = f"{max(fake['dates'])}T23:59:59Z"
    fresh = {"data": fake, "expires": 9_999_999_999.0,
             "acquiredAt": acquired_at}
    jp = {code: dict(fresh) for code in ("7203", "1321", "1306", "2644", "2516")}
    us = {symbol: dict(fresh) for symbol in ("SPY", "QQQ", "USD/JPY")}
    with mock.patch.dict(scanner._JQ_HISTORY_CACHE, jp, clear=True), \
            mock.patch.dict(scanner._TD_HISTORY_CACHE, us, clear=True):
        yield


class ArgusV1310IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.today_state = ti.normalize_state(scanner._TODAY_INTELLIGENCE)
        scanner._TODAY_INTELLIGENCE.clear()
        scanner._TODAY_INTELLIGENCE.update(ti.empty_state())

    def tearDown(self):
        scanner._TODAY_INTELLIGENCE.clear()
        scanner._TODAY_INTELLIGENCE.update(self.today_state)

    def test_four_instrument_metadata_and_us_isolation(self):
        fake = provider_history()
        with price_cache(fake), \
                mock.patch.object(scanner, "_jq_price_history",
                                  side_effect=AssertionError("provider fetch")), \
                mock.patch.object(scanner, "_td_price_history",
                                  side_effect=AssertionError("provider fetch")), \
                mock.patch.object(scanner, "get_events_snapshot", return_value={"events": []}), \
                mock.patch.object(scanner, "_jp_daily_short_history", return_value=[]):
            client = scanner.app.test_client()
            cases = [("JP", "1321", "日経225 ETF"), ("JP", "1306", "TOPIX ETF"),
                     ("US", "SPY", "S&P 500 ETF"), ("US", "QQQ", "Nasdaq 100 ETF")]
            for market, symbol, name in cases:
                body = client.get(
                    f"/api/argus/chart-intelligence?scope=asset&market={market}&symbol={symbol}").get_json()
                self.assertEqual(body["instrumentMetadata"]["instrumentId"],
                                 f"{market}:{symbol}:ETF")
                self.assertEqual(body["instrumentMetadata"]["displayNameJa"], name)
                self.assertEqual(body["todayIntelligence"]["market"], market)
                self.assertEqual(body["todayIntelligence"]["symbol"], symbol)
                self.assertGreater(len(body["indicators"]["bars"]), 250)
                self.assertEqual(body["automaticAiCalls"], 0)

    def test_public_chart_get_never_refreshes_short_provider(self):
        fake = provider_history()
        with price_cache(fake), \
                mock.patch.object(scanner, "_jq_price_history",
                                  side_effect=AssertionError("provider fetch")), \
                mock.patch.object(scanner, "get_events_snapshot", return_value={"events": []}), \
                mock.patch.object(scanner, "_jp_daily_short_history", return_value=[]) as short:
            scanner.app.test_client().get(
                "/api/argus/chart-intelligence?scope=asset&market=JP&symbol=1321")
        short.assert_called_once_with(cached_only=True)

    def test_public_chart_routes_are_strictly_price_cache_only(self):
        report = {
            "symbol": "1321", "market": "JP", "displayNameJa": "日経225 ETF",
            "instrumentMetadata": {"displayNameJa": "日経225 ETF"},
        }
        with mock.patch.dict(
                scanner._MARKET_PUBLIC_REPORT_CACHE, {}, clear=True), \
                mock.patch.object(
                    scanner, "_chart_public_report", return_value=report) as build:
            client = scanner.app.test_client()
            self.assertEqual(200, client.get(
                "/api/argus/chart-intelligence?scope=market&symbol=1321"
            ).status_code)
            self.assertEqual(200, client.get(
                "/api/argus/chart-intelligence?scope=asset&market=JP&symbol=1321"
            ).status_code)
        self.assertTrue(build.call_args_list[0].kwargs["cached_only"])
        self.assertTrue(build.call_args_list[1].kwargs["cached_only"])

    def test_market_get_reuses_natural_tick_report_without_recompute(self):
        cached = {
            "symbol": "1321", "market": "JP", "displayNameJa": "日経225 ETF",
            "instrumentMetadata": {"displayNameJa": "日経225 ETF"},
            "automaticAiCalls": 0,
            # Natural generation records "updated"; public reuse must expose
            # the cache-boundary contract as "hit".
            "marketReplay": {"cacheStatus": "updated", "contexts": {}},
        }
        key = ("market", "1321", "daily")
        with mock.patch.dict(
                scanner._MARKET_PUBLIC_REPORT_CACHE, {key: cached}, clear=True), \
                mock.patch.object(
                    scanner, "_chart_public_report",
                    side_effect=AssertionError("report recompute")):
            body = scanner.app.test_client().get(
                "/api/argus/chart-intelligence?scope=market&symbol=1321"
            ).get_json()
        self.assertEqual("1321", body["symbol"])
        self.assertEqual("hit", body["marketReplay"]["cacheStatus"])
        self.assertEqual(0, body["automaticAiCalls"])

    def test_natural_tick_has_bounded_initial_short_seed(self):
        source = pathlib.Path("scanner.py").read_text()
        self.assertIn("_needs_daily_short_seed", source)
        self.assertIn("_jp_daily_short_history(cached_only=False)", source)
        self.assertIn('"initialSeed": _needs_daily_short_seed', source)

    def test_today_state_is_durable_hash_verified_and_private_safe(self):
        bars = [{"date": "2026-07-21", "open": 99, "high": 102, "low": 98,
                 "close": 101, "volume": 1000}]
        analysis = {"symbol": "SPY", "market": "US", "asOf": "2026-07-21",
                    "calibration": {}, "shortSelling": {"status": "missing"},
                    "failedRally": {"state": "NONE", "backtest": {"cases": []}}}
        state = ti.merge_analysis(ti.empty_state(), analysis, bars[-1], [],
                                  "2026-07-22T00:00:00Z")
        scanner._TODAY_INTELLIGENCE.clear()
        scanner._TODAY_INTELLIGENCE.update(state)
        old_restored = scanner._OSINT_PERSIST_STATE["restored"]
        scanner._OSINT_PERSIST_STATE["restored"] = True
        try:
            body = scanner.app.test_client().get(
                "/api/argus/osint/memory-snapshot").get_json()
        finally:
            scanner._OSINT_PERSIST_STATE["restored"] = old_restored
        self.assertEqual(body["todayIntelligenceStateHash"], ti.state_hash(state))
        self.assertTrue(ti.read_back_verified(state, body["todayIntelligence"]))
        self.assertNotIn("holdings", json.dumps(body).lower())

    def test_turning_point_page_distinguishes_total_and_return_limit(self):
        state = ml.empty_state()
        state["turningPoints"] = [{"id": f"tp-{n}", "status": "confirmed",
                                   "effectiveFrom": f"2025-01-{n % 28 + 1:02d}"}
                                  for n in range(245)]
        view = ml.public_view(state, "2026-07-23T00:00:00Z")
        self.assertEqual(view["turningPointPage"]["totalStoredCount"], 245)
        self.assertEqual(view["turningPointPage"]["apiReturnCount"], 200)
        self.assertEqual(view["turningPointPage"]["uiDisplayCount"], 200)
        self.assertIsNotNone(view["turningPointPage"]["nextCursor"])

    def test_frontend_contract_has_local_selection_and_no_ai_post(self):
        route = pathlib.Path("web/src/routes/CommandCenter.tsx").read_text()
        panel = pathlib.Path("web/src/components/today/ArgusTodayPanel.tsx").read_text()
        self.assertIn("argus.today.selectedInstrument.v1", route)
        self.assertNotIn("argus.replayContext", panel)
        self.assertIn("<ProjectionChart projection={projection}", panel)
        self.assertNotIn("onActivate={() => setDetail(true)}", panel)
        self.assertIn("([1, 5, 20] as const)", panel)
        self.assertNotIn("method: 'POST'", route + panel)

    def test_version_consistency(self):
        package = json.loads(pathlib.Path("web/package.json").read_text())
        lock = json.loads(pathlib.Path("web/package-lock.json").read_text())
        backend = json.loads(pathlib.Path("backend-version.json").read_text())
        self.assertEqual(package["version"], "13.5.63")
        self.assertEqual(backend["version"], "13.5.63")
        self.assertEqual(lock["version"], package["version"])
        self.assertEqual(lock["packages"][""]["version"], package["version"])
        self.assertEqual(scanner._semantic_app_version(), backend["version"])
        self.assertEqual(scanner._frontend_semantic_version(), package["version"])
        self.assertFalse(pathlib.Path("web/src/routes/Guide.tsx").exists())
        manifest = pathlib.Path("docs/ARGUS_B2A_DEFERRED_UI_MANIFEST.md").read_text()
        self.assertIn("Round 1 deletion completed", manifest)


if __name__ == "__main__":
    unittest.main()
