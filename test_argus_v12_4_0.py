import datetime as dt
import pathlib
import sys
import types
import unittest
from contextlib import contextmanager
from unittest import mock

import argus_chart_intelligence as ci
import argus_market_replay as mr

# The SDK creates a user-home rotating log during import.  Tests exercise only
# public Flask/data functions, so use the same inert process-local stub as CI.
sys.modules.setdefault("moomoo", types.SimpleNamespace(
    OpenQuoteContext=object, OpenSecTradeContext=object, RET_OK=0))
import scanner


def history(count=260, start=100.0, step=0.2):
    dates, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    day = dt.date(2024, 1, 1)
    value = start
    while len(dates) < count:
        if day.weekday() < 5:
            dates.append(day.isoformat()); opens.append(value - .2)
            highs.append(value + 1); lows.append(value - 1); closes.append(value)
            volumes.append(1000 + len(dates)); value += step
        day += dt.timedelta(days=1)
    # Provider caches are newest-first.
    return {"dates": dates[::-1], "opens": opens[::-1], "highs": highs[::-1],
            "lows": lows[::-1], "closes": closes[::-1], "volumes": volumes[::-1]}


@contextmanager
def price_cache(fake):
    # A synthetic provider revision needs an explicit acquisition instant.
    # Deriving knowledge from the intentionally-far-future expiry would place
    # these bars in 2286 and the strict PIT boundary must reject them.
    acquired_at = f"{max(fake['dates'])}T23:59:59Z"
    fresh = {"data": fake, "expires": 9_999_999_999.0,
             "acquiredAt": acquired_at}
    jp = {code: dict(fresh) for code in ("7203", "1321", "1306", "2644", "2516")}
    us = {symbol: dict(fresh) for symbol in ("SPY", "QQQ", "USD/JPY")}
    with mock.patch.dict(scanner._JQ_HISTORY_CACHE, jp, clear=True), \
            mock.patch.dict(scanner._TD_HISTORY_CACHE, us, clear=True):
        yield


class ArgusV1240IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.chart_state = ci.normalize_state(scanner._CHART_INTELLIGENCE)
        scanner._CHART_INTELLIGENCE.clear()
        scanner._CHART_INTELLIGENCE.update(ci.empty_state())
        self.replay_state = mr.normalize_state(scanner._MARKET_REPLAY)
        scanner._MARKET_REPLAY.clear()
        scanner._MARKET_REPLAY.update(mr.empty_state())

    def tearDown(self):
        scanner._CHART_INTELLIGENCE.clear()
        scanner._CHART_INTELLIGENCE.update(self.chart_state)
        scanner._MARKET_REPLAY.clear()
        scanner._MARKET_REPLAY.update(self.replay_state)

    def test_asset_chart_get_is_deterministic_and_provider_ai_zero(self):
        fake = history()
        with price_cache(fake), \
                mock.patch.object(scanner, "_jq_price_history",
                                  side_effect=AssertionError("provider fetch")), \
                mock.patch.object(scanner, "_td_price_history",
                                  side_effect=AssertionError("provider fetch")), \
                mock.patch.object(scanner, "get_events_snapshot", return_value={"events": []}), \
                mock.patch.object(scanner, "_execute_ai_judgment") as ai:
            response = scanner.app.test_client().get(
                "/api/argus/chart-intelligence?scope=asset&symbol=7203&market=JP")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["schemaVersion"], ci.SCHEMA_VERSION)
        self.assertEqual(body["automaticAiCalls"], 0)
        self.assertEqual(body["costPolicyMode"], "SCHEDULED_AI")
        self.assertEqual(body["displayNameJa"], "トヨタ自動車")
        self.assertTrue(body["zones"])
        self.assertLessEqual(len(body["critique"]), 5)
        serialized = str(body).lower()
        self.assertNotIn("x-api-key", serialized)
        self.assertNotIn("holdings", serialized)
        self.assertNotIn("providerresponse", serialized)
        ai.assert_not_called()

    def test_market_chart_has_relative_rotation_and_proxy_disclosure(self):
        fake = history()
        with price_cache(fake), \
                mock.patch.object(scanner, "_jq_price_history",
                                  side_effect=AssertionError("provider fetch")), \
                mock.patch.object(scanner, "_td_price_history",
                                  side_effect=AssertionError("provider fetch")), \
                mock.patch.object(scanner, "get_events_snapshot", return_value={"events": []}):
            body = scanner.app.test_client().get(
                "/api/argus/chart-intelligence?scope=market").get_json()
        self.assertIn("relativeStrength", body)
        self.assertIn("rotationMap", body)
        self.assertIn("ledgerTurningPoints", body)
        self.assertIn("proxyDisclosureJa", body)
        self.assertEqual(body["displayNameJa"], "日経225 ETF")
        self.assertEqual(body["relativeStrength"]["nikkei_sp500"]["classification"],
                         "argus_heuristic")

    def test_asset_chart_reuses_cached_earnings_without_provider_fetch(self):
        fake = history()
        event_date = fake["dates"][10]
        cached = {"items": [{"symbol": "7203", "earnings": {
            "date": event_date, "epsEstimate": 10, "epsActual": 12},
            "filings": [], "disclosures": []}]}
        with price_cache(fake), \
                mock.patch.dict(scanner._CAT_CACHE, {"data": cached}, clear=False), \
                mock.patch.object(scanner, "_jq_price_history",
                                  side_effect=AssertionError("provider fetch")), \
                mock.patch.object(scanner, "get_events_snapshot", return_value={"events": []}), \
                mock.patch.object(scanner, "get_catalysts_snapshot") as provider_path:
            body = scanner.app.test_client().get(
                "/api/argus/chart-intelligence?scope=asset&symbol=7203&market=JP").get_json()
        self.assertTrue(any(x["kind"] == "earnings" for x in body["eventMarkers"]))
        provider_path.assert_not_called()

    def test_weekly_switch_and_invalid_timeframe(self):
        fake = history()
        with price_cache(fake), \
                mock.patch.object(scanner, "_jq_price_history",
                                  side_effect=AssertionError("provider fetch")), \
                mock.patch.object(scanner, "get_events_snapshot", return_value={"events": []}):
            client = scanner.app.test_client()
            daily = client.get("/api/argus/chart-intelligence?scope=asset&symbol=7203&market=JP&timeframe=daily")
            weekly = client.get("/api/argus/chart-intelligence?scope=asset&symbol=7203&market=JP&timeframe=weekly")
            invalid = client.get("/api/argus/chart-intelligence?scope=asset&symbol=7203&market=JP&timeframe=minute")
        self.assertEqual(weekly.status_code, 200)
        self.assertNotEqual(daily.get_json()["reportId"], weekly.get_json()["reportId"])
        self.assertEqual(weekly.get_json()["timeframe"], "weekly")
        self.assertLess(len(weekly.get_json()["indicators"]["bars"]), 80)
        self.assertEqual(invalid.status_code, 400)

    def test_scheduled_replay_precompute_refreshes_cold_price_cache(self):
        fake = history()
        with mock.patch.object(scanner, "_ai_now_iso",
                               return_value="2026-07-21T15:00:00+09:00"), \
                mock.patch.dict(scanner._JQ_HISTORY_CACHE, {}, clear=True), \
                mock.patch.dict(scanner._TD_HISTORY_CACHE, {}, clear=True), \
                mock.patch.object(scanner, "_jq_price_history",
                                  return_value=fake) as jp_provider, \
                mock.patch.object(scanner, "_td_price_history",
                                  return_value=fake) as us_provider, \
                mock.patch.object(scanner, "get_events_snapshot",
                                  return_value={"events": []}):
            body = scanner._chart_public_report(
                "1321", "JP", market_scope=True, cached_only=False,
                precompute_replay=True)
        jp_provider.assert_called()
        us_provider.assert_called()
        self.assertEqual(body["stateUpdate"]["status"], "updated")
        self.assertEqual({"1", "5", "20"},
                         set(body["marketReplay"]["contexts"]))
        self.assertEqual(body["automaticAiCalls"], 0)

    def test_scheduled_replay_precompute_is_idempotent_for_same_dataset(self):
        fake = history()
        patches = (
            mock.patch.object(scanner, "_ai_now_iso",
                              return_value="2026-07-21T15:00:00+09:00"),
            mock.patch.object(scanner, "_jq_price_history", return_value=fake),
            mock.patch.object(scanner, "_td_price_history", return_value=fake),
            mock.patch.object(scanner, "get_events_snapshot",
                              return_value={"events": []}),
        )
        # Pin the provider revision identity just as a real successful fetch
        # does.  The test mocks the fetch functions themselves, so without the
        # cache metadata it would inherit unrelated suite-global cache state.
        with price_cache(fake), patches[0], patches[1], patches[2], patches[3]:
            first = scanner._chart_public_report(
                "1321", "JP", market_scope=True, cached_only=False,
                precompute_replay=True)
            first_hash = first["marketReplay"]["stateHash"]
            with mock.patch.object(
                    mr, "build_context",
                    side_effect=AssertionError("duplicate replay computation")):
                second = scanner._chart_public_report(
                    "1321", "JP", market_scope=True, cached_only=False,
                    precompute_replay=True)
        self.assertEqual("updated", first["marketReplay"]["cacheStatus"])
        self.assertEqual("hit", second["marketReplay"]["cacheStatus"])
        self.assertEqual(first_hash, second["marketReplay"]["stateHash"])
        self.assertEqual(3, len(scanner._MARKET_REPLAY["contexts"]))

    def test_scheduled_chart_tick_uses_only_fresh_price_caches(self):
        fake = history()
        fresh = {"data": fake, "expires": 9_999_999_999.0}
        jq_cache = {code: dict(fresh) for code in
                    ("1321", "1306", "2644", "2516")}
        td_cache = {symbol: dict(fresh) for symbol in ("SPY", "USD/JPY")}
        with mock.patch.object(scanner, "_ai_now_iso",
                               return_value="2026-07-21T15:00:00+09:00"), \
                mock.patch.dict(scanner._JQ_HISTORY_CACHE, jq_cache, clear=True), \
                mock.patch.dict(scanner._TD_HISTORY_CACHE, td_cache, clear=True), \
                mock.patch.object(scanner, "_jq_price_history",
                                  side_effect=AssertionError("network fetch")), \
                mock.patch.object(scanner, "_td_price_history",
                                  side_effect=AssertionError("network fetch")), \
                mock.patch.object(scanner, "get_events_snapshot",
                                  return_value={"events": []}):
            body = scanner._chart_public_report(
                "1321", "JP", market_scope=True, cached_only=True,
                precompute_replay=True)
        self.assertEqual(body["stateUpdate"]["status"], "updated")
        self.assertIn("relativeStrength", body)
        self.assertEqual({"1", "5", "20"},
                         set(body["marketReplay"]["contexts"]))
        self.assertEqual(0, body["marketReplay"]["automaticAiCalls"])
        self.assertEqual(3, len(scanner._MARKET_REPLAY["contexts"]))

    def test_durable_v3_snapshot_contains_chart_state_and_hash(self):
        old_restored = scanner._OSINT_PERSIST_STATE["restored"]
        scanner._OSINT_PERSIST_STATE["restored"] = True
        try:
            body = scanner.app.test_client().get("/api/argus/osint/memory-snapshot").get_json()
        finally:
            scanner._OSINT_PERSIST_STATE["restored"] = old_restored
        self.assertEqual(body["schemaVersion"], "argus-durable-v3")
        self.assertIn("chartIntelligence", body)
        self.assertIn("chartIntelligenceStateHash", body)
        self.assertIn("marketReplay", body)
        self.assertIn("marketReplayStateHash", body)
        self.assertNotIn("holdings", str(body).lower())

    def test_market_endpoint_supports_four_isolated_instruments(self):
        fake = history()
        with price_cache(fake), \
                mock.patch.object(scanner, "_jq_price_history",
                                  side_effect=AssertionError("provider fetch")), \
                mock.patch.object(scanner, "_td_price_history",
                                  side_effect=AssertionError("provider fetch")), \
                mock.patch.object(scanner, "get_events_snapshot",
                                  return_value={"events": []}):
            client = scanner.app.test_client()
            for symbol, market in (("1321", "JP"), ("1306", "JP"),
                                   ("SPY", "US"), ("QQQ", "US")):
                body = client.get(
                    f"/api/argus/chart-intelligence?scope=market&symbol={symbol}"
                ).get_json()
                self.assertEqual(symbol, body["symbol"])
                self.assertEqual(market, body["market"])
                self.assertEqual(f"{market}:{symbol}:ETF",
                                 body["marketReplay"]["instrumentId"])
            invalid = client.get(
                "/api/argus/chart-intelligence?scope=market&symbol=INVALID")
        self.assertEqual(400, invalid.status_code)

    def test_frontend_uses_get_shared_cache_hidden_pause_and_no_ai_post(self):
        hook = pathlib.Path("web/src/hooks/useChartIntelligence.ts").read_text()
        panel = pathlib.Path("web/src/components/chart/ChartIntelligencePanel.tsx").read_text()
        self.assertIn("method: 'GET'", hook)
        self.assertIn("visibilityState === 'hidden'", hook)
        self.assertIn("const inflight", hook)
        self.assertNotIn("method: 'POST'", hook + panel)
        self.assertIn("slice(0, 3)", panel)
        self.assertIn("AI API 0", panel)

    def test_runtime_version_matches_release(self):
        self.assertEqual(scanner._semantic_app_version(), "13.5.61")


if __name__ == "__main__":
    unittest.main()
