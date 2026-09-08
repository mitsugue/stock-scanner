"""Recovery Phase A PR B trust-boundary contract tests."""
from __future__ import annotations

import ast
import copy
from collections import Counter
import inspect
import json
from pathlib import Path
import re

import pytest
import requests as http_requests

import argus_diagnostics_contract as diagnostics
import argus_route_catalog as catalog
import scanner


FIXED_NOW = "2026-08-14T04:00:00Z"
ADMIN_HEADER = {"X-ARGUS-ADMIN-TOKEN": "boundary-test-admin"}


PUBLIC_KEYS = {
    "schemaVersion", "generatedAt", "service", "freshness", "systemHealth",
    "recovery",
}
SERVICE_KEYS = {
    "liveness", "readiness", "overall", "backendVersion", "buildSha",
}
FRESHNESS_KEYS = {"overall", "sourceCounts", "expectedDisabledCount"}
SOURCE_COUNT_KEYS = {"fresh", "aging", "stale", "unknown"}
SYSTEM_HEALTH_KEYS = {"asOf", "overall", "lamps", "noteJa"}
HEALTH_LAMP_KEYS = {"key", "labelJa", "status", "detailJa"}
RECOVERY_KEYS = {
    "mode", "measurement", "exactColdRecovery", "hardRpoClaimPermitted",
}
OPERATIONAL_KEYS = {
    "schemaVersion", "generatedAt", "service", "freshness", "storage",
    "durability", "remoteJournal", "features", "scheduler", "registry",
    "osint", "costPolicy",
}

RETIRED_PUBLIC_GET_PATHS = frozenset(
    f"/api/argus/{path}" for path in """
backup-safety/status
decision-quality/status
fire-core/status
learning-review/status
notifications/status
portfolio-strategy/status
position-exposure/status
review-pack/status
portfolio-sync/status
action-priority
action-priority/status
scenarios
scenarios/status
position-plans
position-plans/status
session-brief
session-brief/status
flow-attribution/status
supply-demand/status
decision-value/policies
decision-value/status
decision-value/summary
calibration
calibration/clock
calibration/cohorts
calibration/epochs
calibration/ops
calibration/posture
calibration/v4/status
calibration/watchlist-sync-status
caos/audit
caos/deep-research/status
caos/patrol-plan
caos/source-universe
event-analysis
event-dossier
event-log
events/cards
events/cards/<card_id>
events/<symbol>/research-mission
evidence-pack
decision-spine/status
research-missions
learning-memory
learning-memory/lesson/<lesson_id>
learning-memory/status
integrations
source-registry
source-coverage
market-depth
market-depth/proof
provider-diagnostics/public
runtime-manifest
ledger-health
institutional-intel/signals
institutional-intel/status
institutional-intelligence
institutional-intelligence/brief
institutional-intelligence/institutions
institutional-intelligence/source-health
institutional-intelligence/positioning/<symbol>
institutional-intelligence/relationship-graph
institutional-intelligence/missed
investment-universe
market-movers
jp-market-movers
attribution-history
downside-history
rotation-history
buy-candidates
macro-event-analysis/<eid>
macro-event-analysis/status
macro-events/result-status
market-confirmation
mover-causes
mover-causes/<market>/<symbol>
mover-causes/refresh-queue
mover-causes/status
news-radar
news/translation-status
osint/canary
picks/today
research-benchmark/v2
tdnet-recent
event-backbone-status
system-health
""".split()
)
assert len(RETIRED_PUBLIC_GET_PATHS) == 86


def _serialized(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _assert_public_contract(body):
    assert set(body) == PUBLIC_KEYS
    assert set(body["service"]) == SERVICE_KEYS
    assert set(body["freshness"]) == FRESHNESS_KEYS
    assert set(body["freshness"]["sourceCounts"]) == SOURCE_COUNT_KEYS
    assert set(body["systemHealth"]) == SYSTEM_HEALTH_KEYS
    assert body["systemHealth"]["overall"] in {
        "ok", "warning", "stopped", "off",
    }
    assert len(body["systemHealth"]["lamps"]) <= \
        diagnostics.PUBLIC_HEALTH_MAX_LAMPS
    for lamp in body["systemHealth"]["lamps"]:
        assert set(lamp) == HEALTH_LAMP_KEYS
        assert lamp["status"] in {"ok", "warning", "stopped", "off"}
    assert set(body["recovery"]) == RECOVERY_KEYS
    assert body["schemaVersion"] == diagnostics.PUBLIC_SCHEMA
    assert body["recovery"] == {
        "mode": "LEGACY_ONLY",
        "measurement": "SHADOW_INCOMPLETE",
        "exactColdRecovery": "NOT_PROVEN",
        "hardRpoClaimPermitted": False,
    }
    assert len(_serialized(body)) <= diagnostics.PUBLIC_MAX_BYTES


def test_route_catalog_matches_every_flask_rule_and_is_fail_closed():
    actual = frozenset(
        (rule.rule,
         tuple(sorted(set(rule.methods) - {"HEAD", "OPTIONS"})),
         rule.endpoint)
        for rule in scanner.app.url_map.iter_rules()
        if set(rule.methods) - {"HEAD", "OPTIONS"}
    )
    assert catalog.ROUTE_CATALOG_VALIDATION_ERRORS == ()
    assert catalog.route_contract_keys() == actual
    assert len(catalog.ROUTE_CATALOG) == len(actual) == 174
    assert Counter(row.trustDomain for row in catalog.ROUTE_CATALOG) == {
        "PUBLIC": 71,
        "AUTH_OPERATIONAL": 94,
        "OWNER_SYNC": 6,
        "RECOVERY_PROOF": 3,
    }
    assert not [
        row for row in catalog.ROUTE_CATALOG
        if row.trustDomain == "PUBLIC" and row.mutatesState
    ]
    moved = {
        "/api/argus/caos/investigate-now",
        "/api/argus/news/translation-request",
        "/api/argus/osint/deep-dive",
        "/api/argus/osint/terms",
        "/api/argus/osint/verify-gaps",
        "/api/argus/osint/url-verify",
        "/api/argus/mover-causes/explain-request",
        "/api/argus/vault-push",
    }
    by_route = {row.route: row for row in catalog.ROUTE_CATALOG}
    assert all(by_route[path].trustDomain == "AUTH_OPERATIONAL"
               for path in moved)
    assert all(by_route[path].authenticationPolicy == "ADMIN_TOKEN"
               for path in moved)


def test_smoke_literal_routes_are_catalogued_or_explicit_safety_negatives():
    source = Path("smoke_test.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name)
                and target.id == "EXPLICIT_NEGATIVE_PATHS"
                for target in node.targets)
    )
    negative_paths = frozenset(ast.literal_eval(assignment.value))
    assert negative_paths == {
        "/api/argus/decision-value/order",
        "/api/argus/decision-value/execute",
        "/api/argus/downside/order",
        "/api/argus/downside/execute",
    }

    endpoints = set()

    class SmokeEndpointVisitor(ast.NodeVisitor):
        @staticmethod
        def _record(value):
            if value == "/healthz" or value == "/readyz" \
                    or value.startswith("/api/"):
                endpoints.add(value)

        def visit_Constant(self, node):
            if isinstance(node.value, str):
                self._record(node.value)

        def visit_JoinedStr(self, node):
            value = "".join(
                part.value if isinstance(part, ast.Constant)
                and isinstance(part.value, str) else "__SMOKE_DYNAMIC__"
                for part in node.values
            )
            self._record(value)
            # Do not visit the component constants: they are only fragments of
            # the complete f-string endpoint collected above.

    SmokeEndpointVisitor().visit(tree)

    def catalog_matches(endpoint):
        path = endpoint.split("?", 1)[0].rstrip("/") or "/"
        path_parts = path.strip("/").split("/") if path != "/" else []
        for row in catalog.ROUTE_CATALOG:
            rule = row.route.rstrip("/") or "/"
            rule_parts = rule.strip("/").split("/") if rule != "/" else []
            if len(rule_parts) != len(path_parts):
                continue
            if all(
                    (rule_part.startswith("<") and rule_part.endswith(">"))
                    or "__SMOKE_DYNAMIC__" in path_part
                    or rule_part == path_part
                    for rule_part, path_part in zip(rule_parts, path_parts)):
                return True
        return False

    assert negative_paths <= endpoints
    assert not any(catalog_matches(path) for path in negative_paths)
    assert sorted(
        endpoint for endpoint in endpoints - negative_paths
        if not catalog_matches(endpoint)
    ) == []


def test_public_cache_only_consumer_manifest_is_exact_and_pinned():
    assert catalog.PUBLIC_CACHE_ONLY_VALIDATION_ERRORS == ()
    expected = {
        "/api/argus/action-labels",
        "/api/argus/ai-judgment",
        "/api/argus/data-quality/status",
        "/api/argus/events-active",
        "/api/argus/japan-watchlist",
        "/api/argus/us-watchlist",
        "/api/argus/visibility-guard",
    }
    assert {row.route for row in catalog.PUBLIC_CACHE_ONLY_CONSUMERS} == expected
    for row in catalog.PUBLIC_CACHE_ONLY_CONSUMERS:
        for consumer in row.consumers:
            source = Path(consumer).read_text(encoding="utf-8")
            assert row.route in source, (row.route, consumer)

def test_public_diagnostics_canonical_route_is_bounded_and_alias_is_retired():
    client = scanner.app.test_client()
    retired = client.get("/api/argus/data-quality")
    canonical = client.get("/api/argus/data-quality/status")
    assert retired.status_code == 404
    assert canonical.status_code == 200
    _assert_public_contract(canonical.get_json())


def test_system_health_is_a_closed_field_on_canonical_diagnostics(monkeypatch):
    monkeypatch.setattr(scanner, "_ai_now_iso", lambda: FIXED_NOW)
    monkeypatch.setattr(scanner, "_system_health", lambda **_kwargs: {
        "asOf": FIXED_NOW,
        "overall": "warning",
        "lamps": [{
            "key": "bridge",
            "labelJa": "ブリッジ",
            "status": "warning",
            "detailJa": "確認中",
            "futurePrivateField": "PRIVATE-LAMP-SENTINEL",
        }],
        "noteJa": "公開可能な要約",
        "futurePrivateField": "PRIVATE-HEALTH-SENTINEL",
    })
    client = scanner.app.test_client()
    response = client.get("/api/argus/data-quality/status")
    assert response.status_code == 200
    body = response.get_json()
    _assert_public_contract(body)
    assert body["systemHealth"] == {
        "asOf": FIXED_NOW,
        "overall": "warning",
        "lamps": [{
            "key": "bridge", "labelJa": "ブリッジ",
            "status": "warning", "detailJa": "確認中",
        }],
        "noteJa": "公開可能な要約",
    }
    assert "PRIVATE" not in response.get_data(as_text=True)
    assert client.get("/api/argus/system-health").status_code == 404


def test_events_active_includes_only_product_backbone_status(monkeypatch):
    monkeypatch.setattr(scanner, "_EVENTS_ACTIVE", {
        "active": {
            "eventId": "active", "severity": 4,
            "expiresAt": "2099-01-01T00:00:00Z",
        },
        "expired": {
            "eventId": "expired", "severity": 5,
            "expiresAt": "2000-01-01T00:00:00Z",
        },
    })
    monkeypatch.setattr(scanner, "_jp_market_open", lambda: True)
    monkeypatch.setattr(scanner, "_us_market_open", lambda: False)
    monkeypatch.setitem(scanner._EVENT_STATE, "lastDetectionAt", FIXED_NOW)
    monkeypatch.setitem(scanner._EVENT_STATE, "lastEventAt", FIXED_NOW)
    client = scanner.app.test_client()
    response = client.get("/api/argus/events-active")
    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {
        "enabled", "asOf", "schemaVersion", "count", "events",
        "activeCount", "ntfyConfigured", "sessionJp", "sessionUs",
        "lastDetectionAt", "lastEventAt",
    }
    assert body["count"] == 1
    assert body["activeCount"] == 2
    assert [event["eventId"] for event in body["events"]] == ["active"]
    assert body["sessionJp"] is True
    assert body["sessionUs"] is False
    assert client.get("/api/argus/event-backbone-status").status_code == 404


def test_public_liveness_and_readiness_are_minimal_and_preserve_truth(
        monkeypatch):
    monkeypatch.setattr(scanner, "_ai_now_iso", lambda: FIXED_NOW)
    monkeypatch.setitem(scanner._STARTUP, "state", "ready")
    client = scanner.app.test_client()
    health = client.get("/healthz")
    ready = client.get("/readyz")
    assert health.status_code == ready.status_code == 200
    assert set(health.get_json()) == {
        "schemaVersion", "generatedAt", "status", "backendVersion",
        "buildSha",
    }
    assert set(ready.get_json()) == {
        "schemaVersion", "generatedAt", "ready", "status", "reasonCode",
        "backendVersion", "buildSha",
    }
    monkeypatch.setitem(scanner._STARTUP, "state", "bootstrapping")
    monkeypatch.setattr(scanner, "_startup_bootstrap", lambda: None)
    blocked = client.get("/readyz")
    assert blocked.status_code == 503
    assert blocked.get_json()["ready"] is False
    assert blocked.get_json()["reasonCode"] == "BOOTSTRAPPING"


def test_operational_diagnostics_requires_auth_is_fixed_and_not_cors(
        monkeypatch):
    monkeypatch.setattr(scanner, "_ARGUS_ADMIN_TOKEN", "boundary-test-admin")
    client = scanner.app.test_client()
    denied = client.get("/api/argus/admin/diagnostics/operational")
    assert denied.status_code == 401
    assert denied.get_json() == {"error": "unauthorized"}
    response = client.get(
        "/api/argus/admin/diagnostics/operational",
        headers={**ADMIN_HEADER, "Origin": "https://mitsugue.github.io"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == OPERATIONAL_KEYS
    assert body["schemaVersion"] == diagnostics.OPERATIONAL_SCHEMA
    assert body["features"]["exactColdRecovery"] == "NOT_PROVEN"
    assert body["features"]["hardRpoClaimPermitted"] is False
    assert len(_serialized(body)) <= diagnostics.OPERATIONAL_MAX_BYTES
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "no-store" in response.headers["Cache-Control"]


@pytest.mark.parametrize("path,payload", [
    ("/api/argus/caos/investigate-now", {"symbol": "5803", "market": "JP"}),
    ("/api/argus/news/translation-request", {"context": "x", "items": []}),
    ("/api/argus/osint/deep-dive", {"symbol": "5803", "market": "JP"}),
    ("/api/argus/osint/terms", {"terms": ["semiconductor"]}),
    ("/api/argus/osint/verify-gaps", {"symbol": "5803"}),
    ("/api/argus/osint/url-verify", {"url": "https://example.com/news"}),
    ("/api/argus/mover-causes/explain-request", {"symbol": "NVDA", "market": "US"}),
    ("/api/argus/vault-push", {"vaultId": "v" * 64, "blob": "ciphertext"}),
])
def test_moved_posts_reject_unauthenticated_before_handler_and_auth_reaches_it(
        monkeypatch, path, payload):
    monkeypatch.setattr(scanner, "_ARGUS_ADMIN_TOKEN", "boundary-test-admin")
    client = scanner.app.test_client()
    denied = client.post(path, json=payload)
    assert denied.status_code == 401
    assert denied.get_json() == {"error": "unauthorized"}
    allowed = client.post(path, json=payload, headers=ADMIN_HEADER)
    assert allowed.status_code != 401
    assert allowed.get_json() != {"error": "unauthorized"}


def test_hostile_internal_and_future_fields_never_reach_public_diagnostics(
        monkeypatch):
    sentinel = "BOUNDARY-SENTINEL-7c443ca5"
    monkeypatch.setattr(scanner, "_ai_now_iso", lambda: FIXED_NOW)
    targets = (
        scanner._REMOTE_CYCLE,
        scanner._DURABLE_STATE,
        scanner._CHECKPOINT_V2_STATUS,
        scanner._AI_INTEGRITY,
        scanner._OSINT_STORE,
    )
    for target in targets:
        if isinstance(target, dict):
            monkeypatch.setitem(target, "futureSecurityField", sentinel)
    if scanner._INCIDENTS:
        monkeypatch.setitem(scanner._INCIDENTS[0], "futureOwnerImpact", sentinel)
    else:
        monkeypatch.setattr(
            scanner, "_INCIDENTS", [{"futureOwnerImpact": sentinel}])
    for name in ("_MISSIONS", "_PERIODIC_REPORTS", "_CHALLENGER_RUNS",
                 "_POSTMORTEMS"):
        value = getattr(scanner, name)
        replacement = copy.deepcopy(value)
        if isinstance(replacement, list):
            replacement.append({"futureSecurityField": sentinel})
        elif isinstance(replacement, dict):
            replacement["futureSecurityField"] = sentinel
        monkeypatch.setattr(scanner, name, replacement)
    client = scanner.app.test_client()
    for path in ("/healthz", "/readyz", "/api/argus/data-quality/status"):
        response = client.get(path)
        assert sentinel not in response.get_data(as_text=True)


class _OutboundForbidden(BaseException):
    pass


def test_named_public_status_routes_are_cache_only_even_when_cold(monkeypatch):
    """Cold public reads must not invoke any live provider/ledger refresh path."""
    def forbidden(*_args, **_kwargs):
        raise _OutboundForbidden("public GET attempted outbound refresh")

    protected_caches = (
            scanner._PROVIDER_DIAG_CACHE, scanner._MARKET_DEPTH_CACHE, scanner._VWAP_CACHE,
            scanner._VISIBILITY_CACHE, scanner._INTEGRATIONS_CACHE,
            scanner._REGIME_CACHE, scanner._LEDGER_SUMMARY_CACHE,
            scanner._RATES_CACHE, scanner._JP_CACHE, scanner._US_CACHE,
            scanner._DOWNSIDE_CACHE, scanner._TDNET_OFFICIAL_CACHE,
            scanner._TDNET_FEED_CACHE, scanner._AI_RESULT_CACHE,
            scanner._CALIB_V4_CACHE, scanner._DV_STATUS_CACHE,
            scanner._EVENT_SNAP_META)
    for cache in protected_caches:
        monkeypatch.setitem(cache, "data", None)
        if "expires" in cache:
            monkeypatch.setitem(cache, "expires", 0.0)
    monkeypatch.setitem(scanner._LEARNING_MEMORY, "doc", None)
    monkeypatch.setitem(scanner._LEARNING_MEMORY_STATE, "pathType", "ephemeral_tmp")
    monkeypatch.setattr(scanner.requests, "get", forbidden)
    monkeypatch.setattr(scanner.requests, "post", forbidden)
    for name in (
            "get_rates_snapshot", "_ai_cached_result", "_ai_try_restore",
            "_gh_private_get", "get_downside_incidents", "get_tdnet_recent",
            "_dv_shadow_public_summary", "_ai_cost_roll", "_finnhub_quote_row",
            "_edinet_filings",
            "_jquants_tdnet_fetch", "_provider_diagnostics", "_vwap_probe",
            "get_market_regime_snapshot", "_ledger_summary",
            "_learning_memory_restore_once", "_dv_shadow_phase"):
        monkeypatch.setattr(scanner, name, forbidden)
    for name in ("_dv_shadow_public_summary", "_events_restore_once",
                 "_event_snapshot_meta"):
        monkeypatch.setattr(scanner, name, forbidden)

    client = scanner.app.test_client()
    for path in (
            "/api/argus/action-labels?jp=8058&us=NVDA",
            "/api/argus/ai-judgment",
            "/api/argus/data-quality/status",
            "/api/argus/events-active",
            "/api/argus/japan-watchlist?symbols=8058",
            "/api/argus/learning-memory/snapshot",
            "/api/argus/us-watchlist?symbols=NVDA",
            "/api/argus/visibility-guard"):
        response = client.get(path)
        assert response.status_code == 200, path
    assert all(cache.get("data") is None for cache in protected_caches)


def test_public_jp_watchlist_is_read_only_and_provider_cache_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise _OutboundForbidden("JP public GET attempted provider work")

    seen_before = copy.deepcopy(scanner._JP_SEEN_SYMBOLS)
    monkeypatch.setitem(scanner._JP_CACHE, "data", None)
    monkeypatch.setitem(scanner._JP_CACHE, "expires", 0.0)
    monkeypatch.setattr(scanner, "_JP_DYN_CACHE", {})
    monkeypatch.setitem(scanner._JQ_MASTER_CACHE, "data", None)
    monkeypatch.setitem(scanner._JQ_MASTER_CACHE, "expires", 0.0)
    monkeypatch.setitem(scanner._PUSHED_QUOTES, "JP", {
        "8058": {"ts": scanner.time.time(), "row": {
            "symbol": "8058", "price": 1_234.0, "status": "live",
            "exchangeTs": scanner._ai_now_iso(),
        }},
    })
    monkeypatch.setattr(scanner, "_jq_fetch_bar_row", forbidden)
    monkeypatch.setattr(scanner, "_jquants_fetch_quote", forbidden)
    monkeypatch.setattr(scanner, "_jq_master", forbidden)
    before_cache = copy.deepcopy(scanner._JP_DYN_CACHE)

    response = scanner.app.test_client().get(
        "/api/argus/japan-watchlist?symbols=8058")
    assert response.status_code == 200
    assert [row["symbol"] for row in response.get_json()["stocks"]] == ["8058"]
    assert response.get_json()["stocks"][0]["name"] == "8058"
    assert scanner._JP_DYN_CACHE == before_cache
    assert scanner._JP_SEEN_SYMBOLS == seen_before


def test_cache_only_provider_truth_requires_recent_capability_evidence(
        monkeypatch):
    old = "2000-01-01T00:00:00Z"
    now = scanner.time.time()
    monkeypatch.setattr(scanner, "FINNHUB_API_KEY", "configured-for-test")
    monkeypatch.setattr(scanner, "_FINNHUB_QUOTE_CACHE", {})
    monkeypatch.setitem(scanner._MARKET_NEWS_CACHE, "data", None)
    monkeypatch.setitem(scanner._MARKET_NEWS_CACHE, "expires", 0.0)
    monkeypatch.setitem(scanner._MARKET_NEWS_CACHE, "lastSuccessfulPollAt", None)
    monkeypatch.setitem(scanner._INTEGRATIONS_CACHE, "data", None)
    monkeypatch.setitem(scanner._INTEGRATIONS_CACHE, "expires", 0.0)

    integrations = scanner.get_integrations_snapshot(
        allow_provider_fetch=False)
    finnhub = next(row for row in integrations["providers"]
                   if row["id"] == "finnhub")
    assert finnhub["configured"] is True
    assert finnhub["runtimeStatus"] == "requires_test"

    # A failed refresh keeps the last good payload for fallback, but the new
    # failure backoff deadline must not relabel that old payload LIVE.
    monkeypatch.setitem(scanner._MARKET_NEWS_CACHE, "data", {
        "status": "live", "stale": False, "items": []})
    monkeypatch.setitem(
        scanner._MARKET_NEWS_CACHE, "expires", now + 300)
    monkeypatch.setitem(
        scanner._MARKET_NEWS_CACHE, "lastSuccessfulPollAt", old)
    monkeypatch.setitem(
        scanner._MARKET_NEWS_CACHE, "lastErrorClass", "Timeout")
    failed_refresh = scanner.get_integrations_snapshot(
        allow_provider_fetch=False)
    failed_finnhub = next(row for row in failed_refresh["providers"]
                          if row["id"] == "finnhub")
    assert failed_finnhub["runtimeStatus"] == "stale"

    monkeypatch.setattr(scanner, "_EDINET_API_KEY", "configured-for-test")
    monkeypatch.setitem(scanner._EDINET_STATE, "lastFetchOk", True)
    monkeypatch.setitem(scanner._EDINET_STATE, "lastAt", old)
    monkeypatch.setitem(scanner._PUSHED_QUOTES, "JP", {})
    monkeypatch.setitem(scanner._PUSHED_QUOTES, "US", {
        "AAPL": {"ts": now, "row": {
            "symbol": "AAPL", "price": 200.0,
            "exchangeTs": scanner._ai_now_iso(),
        }},
    })
    registry = scanner._source_registry(allow_provider_fetch=False)
    edinet = next(row for row in registry["sources"]
                  if row["capability"] == "企業開示(EDINET)")
    flow = next(row for row in registry["sources"]
                if row["capability"] == "大口フロー(資金分布)")
    assert edinet["status"] == "requires_test"
    assert flow["status"] != "confirmed_live"

    monkeypatch.setitem(scanner._PUSHED_QUOTES["US"]["AAPL"]["row"], "flow", {
        "bigNetRatio": 0.25,
    })
    proven = scanner._source_registry(allow_provider_fetch=False)
    proven_flow = next(row for row in proven["sources"]
                       if row["capability"] == "大口フロー(資金分布)")
    assert proven_flow["status"] == "requires_test"
    assert "flow専用の取得時刻" in proven_flow["notesJa"]


def test_jp_bridge_dynamic_membership_remains_owner_synced_and_admin_gated(
        monkeypatch):
    monkeypatch.setattr(scanner, "_ARGUS_ADMIN_TOKEN", "boundary-test-admin")
    monkeypatch.setattr(scanner, "_JP_SEEN_SYMBOLS", {})
    monkeypatch.setattr(scanner, "_layer2b_read_latest", lambda: {
        "members": [
            {"market": "JP", "symbol": "6965"},
            {"market": "US", "symbol": "AAPL"},
        ]
    })
    client = scanner.app.test_client()

    assert client.get("/api/argus/jp-watchlist-codes").status_code == 401
    before = client.get(
        "/api/argus/jp-watchlist-codes", headers=ADMIN_HEADER)
    assert before.status_code == 200
    assert before.get_json()["codes"] == ["JP.6965"]

    public = client.get("/api/argus/japan-watchlist?symbols=8058")
    assert public.status_code == 200
    after = client.get(
        "/api/argus/jp-watchlist-codes", headers=ADMIN_HEADER)
    assert after.get_json()["codes"] == ["JP.6965"]


def test_public_action_labels_does_not_record_symbol_interest_or_refresh(
        monkeypatch):
    seen_before = copy.deepcopy(scanner._JP_SEEN_SYMBOLS)
    calls = []
    original_jp = scanner.get_japan_watchlist_snapshot
    original_us = scanner.get_us_watchlist_snapshot

    def jp(*args, **kwargs):
        calls.append(("jp", kwargs))
        return original_jp(*args, **kwargs)

    def us(*args, **kwargs):
        calls.append(("us", kwargs))
        return original_us(*args, **kwargs)

    monkeypatch.setattr(scanner, "get_japan_watchlist_snapshot", jp)
    monkeypatch.setattr(scanner, "get_us_watchlist_snapshot", us)
    response = scanner.app.test_client().get(
        "/api/argus/action-labels?jp=8058&us=NVDA")
    assert response.status_code == 200
    assert calls == [
        ("jp", {"allow_provider_fetch": False,
                "record_requested_symbols": False}),
        ("us", {"allow_provider_fetch": False}),
    ]
    assert scanner._JP_SEEN_SYMBOLS == seen_before


def test_public_us_watchlist_does_not_fill_from_provider_when_cache_is_cold(
        monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise _OutboundForbidden("public GET attempted provider fill")

    monkeypatch.setitem(scanner._US_CACHE, "data", None)
    monkeypatch.setitem(scanner._US_CACHE, "expires", 0.0)
    monkeypatch.setattr(scanner, "_US_DYN_CACHE", {})
    monkeypatch.setattr(scanner, "_finnhub_quote_row", forbidden)
    response = scanner.app.test_client().get(
        "/api/argus/us-watchlist?symbols=NVDA")
    assert response.status_code == 200
    assert scanner._US_CACHE.get("data") is None


def test_public_ai_judgment_does_not_restore_on_get(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise _OutboundForbidden("public GET attempted AI restore")

    monkeypatch.setattr(scanner, "_AI_JUDGE_ENABLED", True)
    monkeypatch.setattr(scanner, "_OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setitem(scanner._AI_RESULT_CACHE, "data", None)
    monkeypatch.setitem(scanner._AI_RESULT_CACHE, "expires", 0.0)
    for name in ("_ai_cached_result", "_ai_restore_local", "_ai_try_restore"):
        monkeypatch.setattr(scanner, name, forbidden)
    response = scanner.app.test_client().get("/api/argus/ai-judgment")
    assert response.status_code == 200
    assert response.get_json()["status"] == "not_run_yet"


def test_event_and_ai_product_cache_restore_runs_once_in_process_bootstrap(
        monkeypatch):
    calls = []
    startup_before = copy.deepcopy(scanner._STARTUP)
    runtime_before = copy.deepcopy(scanner._RUNTIME)
    durable_before = copy.deepcopy(scanner._DURABLE_STATE)
    try:
        monkeypatch.setattr(scanner, "_DURABILITY_PRODUCTION", False)
        monkeypatch.setattr(scanner, "_AI_JUDGE_ENABLED", True)
        monkeypatch.setattr(scanner, "_OPENAI_API_KEY", "configured-for-test")
        monkeypatch.setattr(scanner, "_validate_durable_storage", lambda: True)
        monkeypatch.setattr(
            scanner, "_osint_restore_once", lambda: calls.append("osint"))
        monkeypatch.setattr(
            scanner, "_events_restore_once", lambda: calls.append("events"))
        monkeypatch.setattr(
            scanner, "_ai_cached_result", lambda: calls.append("ai"))
        scanner._STARTUP.update({
            "state": "bootstrapping",
            "restoreStartedAt": None,
            "restoreCompletedAt": None,
            "restoreOutcome": None,
        })
        scanner._DURABLE_STATE.update({
            "lastRestoreAt": None,
            "integrityStatus": "unknown",
        })

        scanner._startup_bootstrap()
        scanner._startup_bootstrap()
        assert calls == ["osint", "events", "ai"]
        assert scanner._STARTUP["state"] == "ready"
    finally:
        scanner._STARTUP.clear()
        scanner._STARTUP.update(startup_before)
        scanner._RUNTIME.clear()
        scanner._RUNTIME.update(runtime_before)
        scanner._DURABLE_STATE.clear()
        scanner._DURABLE_STATE.update(durable_before)


def test_expired_component_evidence_is_not_restamped_live(monkeypatch):
    old = "2026-01-01T00:00:00Z"
    monkeypatch.setitem(scanner._INTEGRATIONS_CACHE, "data", None)
    monkeypatch.setitem(scanner._INTEGRATIONS_CACHE, "expires", 0.0)
    for cache in (scanner._RATES_CACHE, scanner._JP_CACHE, scanner._US_CACHE):
        monkeypatch.setitem(cache, "data", {
            "status": "live", "asOf": old, "stocks": []})
        monkeypatch.setitem(cache, "expires", 0.0)
    snapshot = scanner.get_integrations_snapshot(allow_provider_fetch=False)
    statuses = {row["id"]: row["runtimeStatus"]
                for row in snapshot["providers"]}
    assert statuses["fred"] != "live"
    assert statuses["jquants"] != "live"
    assert statuses["twelvedata"] != "live"

    monkeypatch.setitem(scanner._INTEGRATIONS_CACHE, "data", {
        "status": "live", "asOf": old, "providers": [],
        "aiJudgment": {}, "nextRecommendedApis": []})
    monkeypatch.setitem(scanner._INTEGRATIONS_CACHE, "expires", 0.0)
    assert (scanner.get_integrations_snapshot(
        allow_provider_fetch=False)["asOf"] != old)

    monkeypatch.setitem(scanner._CALIB_V4_CACHE, "data", {
        "nPredictions": 99, "updated": old})
    monkeypatch.setitem(scanner._CALIB_V4_CACHE, "expires", 0.0)
    assert scanner._calibration_v4_summary(allow_ledger_fetch=False) is None

    monkeypatch.setitem(scanner._DV_STATUS_CACHE, "data", {
        "phase": "scoring_active", "lastShadowRunAt": old})
    monkeypatch.setitem(scanner._DV_STATUS_CACHE, "expires", 0.0)
    stale_dv = scanner._dv_status_public_dict(allow_private_fetch=False)
    assert stale_dv["phase"] != "scoring_active"
    assert stale_dv["cacheFreshness"] == "stale"

    monkeypatch.setitem(scanner._MARKET_DEPTH_CACHE, "data", {
        "asOf": old,
        "capabilities": {"L2": {"status": "live", "probed": True}},
    })
    monkeypatch.setitem(scanner._MARKET_DEPTH_CACHE, "expires", 0.0)
    assert scanner._market_depth_report(allow_provider_fetch=False) is None

    monkeypatch.setitem(scanner._VISIBILITY_CACHE, "data", {
        "asOf": old, "visibilityLevel": "FULL_SENTINEL"})
    monkeypatch.setitem(scanner._VISIBILITY_CACHE, "expires", 0.0)
    assert scanner._visibility_guard(
        allow_provider_fetch=False).get("visibilityLevel") != "FULL_SENTINEL"

    monkeypatch.setitem(scanner._PROVIDER_DIAG_CACHE, "data", {
        "asOf": old,
        "items": [{"provider": "sentinel", "configured": True,
                   "runtimeStatus": "live"}],
    })
    monkeypatch.setitem(scanner._PROVIDER_DIAG_CACHE, "expires", 0.0)
    stale = scanner._provider_diagnostics_cached_only()
    assert stale["asOf"] == old
    assert stale["items"][0]["runtimeStatus"] == "stale"


@pytest.mark.parametrize(
    ("case", "jp_data", "fresh", "expected_status", "expected_cache_state",
     "expected_overall"),
    [
        ("fresh live", {"status": "live", "stocks": [
            {"symbol": "8058", "status": "live"}]}, True,
         "live", "fresh_read_only", "live"),
        ("fresh partial", {"status": "partial", "stocks": [
            {"symbol": "8058", "status": "live"}],
            "coverage": {"live": 1, "mock": 1, "total": 2}}, True,
         "partial", "fresh_read_only", "partial"),
        ("expired nonempty", {"status": "live", "stocks": [
            {"symbol": "8058", "status": "live", "delayClass": "LIVE"}]}, False,
         "delayed", "stale_read_only", "partial"),
        ("empty cold", None, False,
         "mock", "unavailable", "partial"),
    ],
)
def test_integrations_quote_cache_truth_matrix(
        monkeypatch, case, jp_data, fresh, expected_status,
        expected_cache_state, expected_overall):
    now = scanner.time.time()
    monkeypatch.setattr(scanner, "_FRED_API_KEY", "configured-for-test")
    monkeypatch.setattr(scanner, "_JQUANTS_API_KEY", "configured-for-test")
    monkeypatch.setattr(scanner, "_TWELVEDATA_API_KEY", "configured-for-test")
    monkeypatch.setitem(scanner._INTEGRATIONS_CACHE, "data", None)
    monkeypatch.setitem(scanner._INTEGRATIONS_CACHE, "expires", 0.0)
    monkeypatch.setitem(scanner._RATES_CACHE, "data", {"status": "live"})
    monkeypatch.setitem(scanner._RATES_CACHE, "expires", now + 300)
    monkeypatch.setitem(scanner._JP_CACHE, "data", jp_data)
    monkeypatch.setitem(scanner._JP_CACHE, "expires", now + 300 if fresh else 0.0)
    monkeypatch.setitem(scanner._US_CACHE, "data", {
        "status": "live", "stocks": [{"symbol": "AAPL", "status": "live"}]})
    monkeypatch.setitem(scanner._US_CACHE, "expires", now + 300)

    snapshot = scanner.get_integrations_snapshot(allow_provider_fetch=False)
    provider = next(row for row in snapshot["providers"]
                    if row["id"] == "jquants")
    assert provider["runtimeStatus"] == expected_status, case
    assert provider["cacheState"] == expected_cache_state, case
    assert snapshot["status"] == expected_overall, case
    if case == "fresh partial":
        projected = scanner._quote_cache_projection(scanner._JP_CACHE)
        assert projected["status"] == "partial"
        assert projected["coverage"] == {"live": 1, "mock": 1, "total": 2}
        assert projected["stocks"][0]["status"] == "live"


def test_expired_status_and_evidence_components_fail_conservatively(monkeypatch):
    old = "2000-01-01T00:00:00Z"
    monkeypatch.setattr(scanner, "_JQUANTS_API_KEY", "configured-for-test")
    monkeypatch.setitem(scanner._TDNET_OFFICIAL_CACHE, "data", {
        "status": "official_tdnet_live", "official": True,
        "asOf": old, "items": [{"id": "stale-tdnet"}]})
    monkeypatch.setitem(scanner._TDNET_OFFICIAL_CACHE, "expires", 0.0)
    monkeypatch.setitem(scanner._TDNET_FEED_CACHE, "data", None)
    monkeypatch.setitem(scanner._TDNET_FEED_CACHE, "expires", 0.0)
    monkeypatch.setitem(scanner._DOWNSIDE_CACHE, "data", {
        "asOf": old, "activeCount": 77})
    monkeypatch.setitem(scanner._DOWNSIDE_CACHE, "expires", 0.0)
    monkeypatch.setitem(scanner._LEDGER_SUMMARY_CACHE, "data", {
        "overall": {"days": 999, "n": 999}})
    monkeypatch.setitem(scanner._LEDGER_SUMMARY_CACHE, "expires", 0.0)
    monkeypatch.setitem(scanner._CALIB_V4_CACHE, "data", {
        "nPredictions": 99, "updated": old})
    monkeypatch.setitem(scanner._CALIB_V4_CACHE, "expires", 0.0)

    assert scanner._calibration_v4_summary(allow_ledger_fetch=False) is None
    registry = scanner._source_registry(allow_provider_fetch=False)
    tdnet = next(row for row in registry["sources"]
                 if row["capability"] == "企業開示(TDnet 公式)")
    assert tdnet["status"] != "confirmed_live"

    monkeypatch.setitem(scanner._PUSHED_QUOTES, "US", {
        "ZZZZ": {"ts": 0.0, "row": {
            "symbol": "ZZZZ", "price": 999, "date": old,
            "status": "live"}}})
    monkeypatch.setattr(scanner, "_US_DYN_CACHE", {})
    monkeypatch.setitem(scanner._US_CACHE, "data", None)
    monkeypatch.setitem(scanner._US_CACHE, "expires", 0.0)
    monkeypatch.setitem(scanner._VISIBILITY_CACHE, "data", {
        "asOf": old, "visibilityLevel": "FULL_SENTINEL"})
    monkeypatch.setitem(scanner._VISIBILITY_CACHE, "expires", 0.0)
    monkeypatch.setitem(scanner._MARKET_DEPTH_CACHE, "data", {
        "asOf": old,
        "capabilities": {"L2": {"status": "live", "probed": True}}})
    monkeypatch.setitem(scanner._MARKET_DEPTH_CACHE, "expires", 0.0)
    monkeypatch.setitem(scanner._DV_STATUS_CACHE, "data", {
        "phase": "scoring_active", "lastShadowRunAt": old})
    monkeypatch.setitem(scanner._DV_STATUS_CACHE, "expires", 0.0)
    pack = scanner._build_evidence_pack("ZZZZ", "US")
    serialized = json.dumps(pack, ensure_ascii=False)
    assert "FULL_SENTINEL" not in serialized
    assert '"price": 999' not in serialized
    markers = set(pack["missingConfirmations"])
    assert "cache:quote" in markers
    assert "cache:visibility_guard" in markers
    assert "cache:market_depth" in markers
    assert "cache:calibration:stale" in markers
    assert "cache:calibration-ledger:stale" in markers
    assert "cache:decision-value:stale" in markers


def test_learning_memory_snapshot_is_cache_only(monkeypatch):
    restore_calls = []
    monkeypatch.setitem(scanner._LEARNING_MEMORY, "doc", None)
    monkeypatch.setattr(
        scanner, "_learning_memory_restore_once",
        lambda: restore_calls.append("restore"))
    client = scanner.app.test_client()
    assert client.get("/api/argus/learning-memory/status").status_code == 404
    snapshot = client.get("/api/argus/learning-memory/snapshot")
    assert snapshot.status_code == 200
    assert restore_calls == []


def test_authenticated_and_internal_refresh_paths_remain_live_capable(monkeypatch):
    provider_calls = []
    monkeypatch.setattr(scanner, "_ARGUS_ADMIN_TOKEN", "boundary-test-admin")
    monkeypatch.setattr(
        scanner, "_provider_diagnostics",
        lambda: provider_calls.append("admin") or {
            "schemaVersion": "provider-diagnostics-v1", "items": []})
    response = scanner.app.test_client().get(
        "/api/argus/admin/provider-diagnostics", headers=ADMIN_HEADER)
    assert response.status_code == 200
    assert provider_calls == ["admin"]

    integration_calls = []
    monkeypatch.setitem(scanner._INTEGRATIONS_CACHE, "data", None)
    monkeypatch.setitem(scanner._INTEGRATIONS_CACHE, "expires", 0.0)
    monkeypatch.setattr(
        scanner, "get_rates_snapshot",
        lambda: integration_calls.append("rates") or {"status": "live"})
    monkeypatch.setattr(
        scanner, "get_japan_watchlist_snapshot",
        lambda: integration_calls.append("jp") or {"status": "live"})
    monkeypatch.setattr(
        scanner, "get_us_watchlist_snapshot",
        lambda: integration_calls.append("us") or {"status": "live"})
    scanner.get_integrations_snapshot()
    assert integration_calls == ["rates", "jp", "us"]

    refresh_calls = []
    monkeypatch.setitem(scanner._MARKET_DEPTH_CACHE, "data", None)
    monkeypatch.setitem(scanner._MARKET_DEPTH_CACHE, "expires", 0.0)
    monkeypatch.setattr(
        scanner, "_source_registry",
        lambda *, allow_provider_fetch=True: refresh_calls.append(
            ("source", allow_provider_fetch)) or {"sources": []})
    monkeypatch.setattr(
        scanner, "_vwap_probe",
        lambda: refresh_calls.append(("vwap", True)) or {
            "computed": False, "probed": True, "values": {}})
    assert scanner._market_depth_report() is not None
    assert ("source", True) in refresh_calls
    assert ("vwap", True) in refresh_calls


def _public_probe_path(rule):
    values = {
        "symbol": "SENTINELSYM", "market": "JP",
        "card_id": "missing-card", "lesson_id": "missing-lesson",
        "eid": "missing-event", "oid": "missing-official",
        "filename": "missing-static.js",
    }
    return re.sub(
        r"<(?:(?:string|path|int):)?([^>]+)>",
        lambda match: values.get(match.group(1), "missing"), rule,
    )


def test_round1_retired_public_get_contracts_are_exactly_absent(monkeypatch):
    catalog_gets = {
        row.route for row in catalog.ROUTE_CATALOG if "GET" in row.methods
    }
    assert RETIRED_PUBLIC_GET_PATHS.isdisjoint(catalog_gets)

    monkeypatch.setattr(scanner, "_ARGUS_ADMIN_TOKEN", "boundary-test-admin")
    client = scanner.app.test_client()
    for route in sorted(RETIRED_PUBLIC_GET_PATHS):
        response = client.get(_public_probe_path(route))
        expected = 405 if route == \
            "/api/argus/institutional-intelligence/missed" else 404
        assert response.status_code == expected, route

    retained = [
        row for row in catalog.ROUTE_CATALOG
        if row.route == "/api/argus/institutional-intelligence/missed"
    ]
    assert len(retained) == 1
    assert retained[0].methods == ("POST",)
    assert retained[0].trustDomain == "AUTH_OPERATIONAL"
    assert client.post(
        "/api/argus/institutional-intelligence/missed", json={}
    ).status_code == 401


def test_every_catalogued_public_route_rejects_private_domain_sentinels(
        monkeypatch):
    """Bounded route-wide hostile test required by the public-boundary RFC."""
    sentinels = {
        "remote": "REMOTE_CYCLE_PRIVATE_SENTINEL",
        "durable": "DURABLE_STATE_PRIVATE_SENTINEL",
        "incident": "INCIDENT_PRIVATE_SENTINEL",
        "osint": "OSINT_PRIVATE_SENTINEL",
        "mission": "MISSION_PRIVATE_SENTINEL",
        "v2": "V2_SECURITY_SENTINEL",
        "owner": "OWNER_DECISION_SENTINEL",
        "report": "PRIVATE_REPORT_SENTINEL",
        "postmortem": "PRIVATE_POSTMORTEM_SENTINEL",
        "model": "MODEL_OUTPUT_SENTINEL",
        "owner_data": "OWNER_DATA_SENTINEL",
    }

    def offline(*_args, **_kwargs):
        raise http_requests.exceptions.ConnectionError("public-route-test-offline")

    monkeypatch.setattr(scanner.requests, "get", offline)
    monkeypatch.setattr(scanner.requests, "post", offline)
    monkeypatch.setitem(scanner._STARTUP, "state", "ready")
    monkeypatch.setitem(scanner._NEWS_JA_STATE, "restored", True)
    monkeypatch.setitem(scanner._REMOTE_CYCLE, "errorClass", sentinels["remote"])
    monkeypatch.setitem(
        scanner._REMOTE_CYCLE, "futureSecurityField", sentinels["remote"])
    monkeypatch.setitem(
        scanner._DURABLE_STATE, "lastFailureMessage", sentinels["durable"])
    monkeypatch.setitem(
        scanner._DURABLE_STATE, "futureSecurityField", sentinels["durable"])
    monkeypatch.setitem(
        scanner._CHECKPOINT_V2_STATUS, "futureSecurityField", sentinels["v2"])
    monkeypatch.setitem(scanner._AI_INTEGRITY, "futureModelOutput", sentinels["model"])

    monkeypatch.setitem(scanner._OSINT_STORE, "SENTINELSYM", {
        "schemaVersion": "osint-investigation-v1",
        "symbol": "SENTINELSYM",
        "futureSecurityField": sentinels["osint"],
        "agentRuns": [{"provider": "gpt", "status": "ok",
                       "rawModelOutput": sentinels["model"]}],
        "queryPlan": {"queryCount": 0, "futureOwnerTerms": sentinels["owner_data"]},
        "researchPower": {"futureModelOutput": sentinels["model"]},
    })
    monkeypatch.setitem(scanner._OSINT_PROGRESS, "SENTINELSYM", {
        "stage": "complete", "futureOwnerField": sentinels["owner_data"],
    })
    monkeypatch.setattr(scanner, "_INCIDENTS", copy.deepcopy(scanner._INCIDENTS) + [{
        "id": "sentinel-incident", "component": sentinels["incident"],
        "ownerImpactJa": sentinels["owner_data"],
    }])
    monkeypatch.setattr(scanner, "_MISSIONS", copy.deepcopy(scanner._MISSIONS) + [{
        "missionId": "sentinel-mission",
        "futureSecurityField": sentinels["mission"],
    }])
    monkeypatch.setattr(
        scanner, "_CHALLENGER_RUNS", copy.deepcopy(scanner._CHALLENGER_RUNS) + [{
            "state": "done", "ownerDecision": sentinels["owner"],
        }])
    monkeypatch.setattr(
        scanner, "_PERIODIC_REPORTS", copy.deepcopy(scanner._PERIODIC_REPORTS) + [{
            "futureSecurityField": sentinels["report"],
        }])
    monkeypatch.setattr(
        scanner, "_POSTMORTEMS", copy.deepcopy(scanner._POSTMORTEMS) + [{
            "futureSecurityField": sentinels["postmortem"],
        }])
    monkeypatch.setitem(scanner._MISSION_STORE, "sentinel-event", {
        "trigger": {"eventId": "sentinel-event", "symbol": "SENTINELSYM",
                    "ownerRelevant": sentinels["owner_data"]},
        "argusView": {"synthesis": sentinels["model"]},
        "at": FIXED_NOW,
    })

    query = {
        "/api/argus/osint/investigation": "?symbol=SENTINELSYM",
        "/api/argus/chart-intelligence": "?symbol=SENTINELSYM&market=JP",
        "/api/argus/price-history": "?symbol=SENTINELSYM&market=JP",
    }
    visited = []
    client = scanner.app.test_client()
    for row in catalog.ROUTE_CATALOG:
        if row.trustDomain != "PUBLIC" or "GET" not in row.methods:
            continue
        path = _public_probe_path(row.route) + query.get(row.route, "")
        response = client.get(path)
        visited.append((row.route, response.status_code))
        assert response.status_code < 500, (path, response.status_code)
        body = response.get_data(as_text=True)
        for sentinel in sentinels.values():
            assert sentinel not in body, (path, sentinel)
    assert len(visited) == sum(
        1 for row in catalog.ROUTE_CATALOG
        if row.trustDomain == "PUBLIC" and "GET" in row.methods)


def test_public_projection_is_unchanged_by_unknown_internal_fields(monkeypatch):
    monkeypatch.setattr(scanner, "_ai_now_iso", lambda: FIXED_NOW)
    # systemHealth lamp details derive ages from the epoch clock; freeze it so
    # the before/after projections cannot differ by a minute-boundary tick
    # between the two requests (observed once in CI on an unchanged tree).
    frozen_epoch = scanner.time.time()
    monkeypatch.setattr(scanner.time, "time", lambda: frozen_epoch)
    client = scanner.app.test_client()
    before = client.get("/api/argus/data-quality/status").get_json()
    monkeypatch.setitem(scanner._REMOTE_CYCLE, "futureNested", {
        "credential": "must-never-serialize",
    })
    monkeypatch.setitem(scanner._DURABLE_STATE, "futureNested", {
        "owner": "must-never-serialize",
    })
    after = client.get("/api/argus/data-quality/status").get_json()
    assert after == before


def test_public_diagnostic_serializers_have_no_raw_state_copy_path():
    """Targeted structural guard; deliberately not a repository-wide linter."""
    builders = "\n".join(inspect.getsource(fn) for fn in (
        diagnostics.build_public_diagnostics,
        diagnostics.build_public_liveness,
        diagnostics.build_public_readiness,
        diagnostics.public_diagnostics_fallback,
    ))
    assert "**" not in builders
    for forbidden in (
            "_REMOTE_CYCLE", "_DURABLE_STATE", "_INCIDENTS", "_OSINT_STORE",
            "_MISSIONS", "_CHECKPOINT_V2_STATUS", "ownerDecision",
            "periodicReports", "postmortems"):
        assert forbidden not in builders

    routes = "\n".join(inspect.getsource(fn) for fn in (
        scanner.healthz, scanner.readyz, scanner.api_argus_data_quality_status,
    ))
    for forbidden in (
            "_data_quality_console", "_REMOTE_CYCLE", "_DURABLE_STATE",
            "_INCIDENTS", "_OSINT_STORE", "_MISSIONS",
            "_CHECKPOINT_V2_STATUS"):
        assert forbidden not in routes
    assert "jsonify(_public_diagnostics_snapshot())" in routes


def test_builder_failures_return_fixed_content_free_fallbacks(monkeypatch):
    client = scanner.app.test_client()
    monkeypatch.setattr(
        diagnostics, "build_public_diagnostics",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("PRIVATE-SENTINEL")),
    )
    public = client.get("/api/argus/data-quality/status").get_json()
    _assert_public_contract(public)
    assert "PRIVATE-SENTINEL" not in json.dumps(public)

    monkeypatch.setattr(scanner, "_ARGUS_ADMIN_TOKEN", "boundary-test-admin")
    monkeypatch.setattr(
        scanner, "_operational_diagnostics_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE-SENTINEL")),
    )
    operational = client.get(
        "/api/argus/admin/diagnostics/operational", headers=ADMIN_HEADER)
    assert operational.status_code == 503
    assert operational.get_json()["errorCode"] == \
        "OPERATIONAL_DIAGNOSTICS_UNAVAILABLE"
    assert "PRIVATE-SENTINEL" not in operational.get_data(as_text=True)


def test_frontend_has_no_admin_secret_or_moved_public_posts():
    web = Path("web")
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (web / "src").rglob("*")
        if path.suffix in {".ts", ".tsx", ".js", ".jsx"}
    )
    assert "ARGUS_ADMIN_TOKEN" not in source
    assert "X-ARGUS-ADMIN-TOKEN" not in source
    active_consumers = "\n".join(
        (web / path).read_text(encoding="utf-8")
        for path in (
            "src/hooks/useOsintInvestigation.ts",
            "src/lib/vault.ts",
            "src/routes/CommandCenter.tsx",
            "src/routes/DataQualityPage.tsx",
        )
    )
    for path in scanner._AUTH_OPERATIONAL_MUTATION_ROUTES:
        assert path not in active_consumers


# ── v13.5.38 Tachibana LIVE product wiring (Recovery-admitted seam) ──────────
# The frozen scanner gains exactly: one import, a lazy once-per-process
# ensure_started() through the existing request autostart hook, and the
# "japaneseLive" evidence document on /api/argus/decision-evidence.  These
# tests pin that the wiring is truthful when disabled, provenance-preserving
# when evidence exists, isolated on failure, and never a route or authority.

def _decision_evidence_client(monkeypatch):
    import argus_tachibana_live as live
    import socket

    def _trip(*_args, **_kwargs):
        raise AssertionError("NETWORK_ATTEMPT")
    monkeypatch.setattr(socket.socket, "connect", _trip)
    monkeypatch.setattr(socket, "create_connection", _trip)
    monkeypatch.delenv("ARGUS_TACHIBANA_ENABLED", raising=False)
    monkeypatch.setattr(scanner, "_TACHIBANA_LIVE_AUTOSTART", {"value": False})
    monkeypatch.setattr(live, "_SERVICE", live.TachibanaLiveService())
    monkeypatch.setattr(scanner, "get_japan_watchlist_snapshot",
                        lambda **kwargs: {"provider": "jquants", "stocks": []})
    scanner._DECISION_EVIDENCE_CACHE.clear()
    return scanner.app.test_client(), live


def test_decision_evidence_carries_truthful_disabled_tachibana_live(monkeypatch):
    client, live = _decision_evidence_client(monkeypatch)
    response = client.get("/api/argus/decision-evidence?symbols=1321")
    assert response.status_code == 200
    body = response.get_json()
    japanese = body["japaneseLive"]
    assert japanese["schemaVersion"] == "argus-tachibana-live-evidence-v1"
    assert japanese["provider"] == "TACHIBANA"
    assert japanese["authority"] == "SHADOW_NON_AUTHORITATIVE"
    assert japanese["status"] == "DISABLED"
    assert japanese["enabled"] is False and japanese["authoritative"] is False
    assert japanese["shadowOnly"] is True
    assert japanese["executionCapability"] is False
    assert japanese["authAttempts"] == 0 and japanese["symbols"] == {}
    # Lazy binding happened exactly once through the autostart seam and
    # started no sensor thread while disabled.
    assert scanner._TACHIBANA_LIVE_AUTOSTART["value"] is True
    import threading
    assert not [t for t in threading.enumerate() if t.name == "argus-tachibana-live"]
    # The evidence is document-level only: never an SDA input, never a subject.
    assert body["sdaAuthority"] is False and body["actionAuthority"] is False
    assert "japaneseLive" not in body["subjects"]
    text = response.get_data(as_text=True).lower()
    for forbidden in ("moomoo-rt", "sauthid", "e_api", "kabuka.e-shiten"):
        assert forbidden not in text


def test_decision_evidence_passes_tachibana_evidence_through_with_provenance(monkeypatch):
    client, live = _decision_evidence_client(monkeypatch)
    calls = []
    monkeypatch.setattr(live, "ensure_started", lambda environ=None: calls.append(1) or "DISABLED")
    evidence = {
        "schemaVersion": "argus-tachibana-live-evidence-v1", "provider": "TACHIBANA",
        "authority": "SHADOW_NON_AUTHORITATIVE", "status": "LIVE", "enabled": True,
        "shadowOnly": True, "authoritative": False, "executionCapability": False,
        "providerHealth": "AVAILABLE", "marketPhase": "AFTERNOON_OPEN",
        "lastErrorClass": None, "authAttempts": 1, "updatedAt": "2026-09-03T03:31:00+00:00",
        "asOf": "2026-09-03T03:31:02+00:00", "symbolCount": 1,
        "symbols": {"9984": {"provider": "TACHIBANA", "authority": "SHADOW_NON_AUTHORITATIVE",
                             "symbol": "9984", "price": 9000.0, "changePct": 1.12,
                             "vwap": 8975.5, "bestBid": 8999.0, "bestAsk": 9001.0,
                             "freshness": "FRESH", "marketStatus": "OPEN",
                             "sourceTimestamp": "2026-09-03T03:30:58+00:00",
                             "receivedAt": "2026-09-03T03:31:00+00:00"}},
    }
    monkeypatch.setattr(live, "current_evidence_safe", lambda now=None: evidence)
    first = client.get("/api/argus/decision-evidence?symbols=1321").get_json()
    second = client.get("/api/argus/decision-evidence?symbols=1321").get_json()
    assert calls == [1]                              # once per process, lazy
    japanese = first["japaneseLive"]
    assert japanese["status"] == "LIVE"
    assert japanese["symbols"]["9984"]["provider"] == "TACHIBANA"
    assert japanese["symbols"]["9984"]["freshness"] == "FRESH"
    assert japanese["symbols"]["9984"]["price"] == 9000.0
    assert japanese["authoritative"] is False and japanese["executionCapability"] is False
    # Live evidence changes nothing about decision authority or the subjects.
    assert first["sdaAuthority"] is False and second["sdaAuthority"] is False
    assert "moomoo" not in json.dumps(japanese).lower()


def test_tachibana_failure_cannot_take_decision_evidence_down(monkeypatch):
    client, live = _decision_evidence_client(monkeypatch)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("provider boundary exploded")
    monkeypatch.setattr(live, "ensure_started", _boom)
    monkeypatch.setattr(live, "current_evidence_safe", _boom)
    response = client.get("/api/argus/decision-evidence?symbols=1321")
    assert response.status_code == 200
    japanese = response.get_json()["japaneseLive"]
    assert japanese["status"] == "UNAVAILABLE"
    assert japanese["provider"] == "TACHIBANA"
    assert japanese["lastErrorClass"] == "RuntimeError"
    assert japanese["symbols"] == {} and japanese["executionCapability"] is False


def test_tachibana_wiring_adds_no_route_and_no_order_capability():
    rules = {rule.rule for rule in scanner.app.url_map.iter_rules()}
    assert not [rule for rule in rules if "tachibana" in rule.lower()]
    source = Path("scanner.py").read_text(encoding="utf-8")
    wiring = [line for line in source.splitlines() if "argus_tachibana_live." in line]
    # ensure_started, current_evidence_safe, and the three fixed identity
    # constants of the fail-closed fallback — nothing else.
    assert {line.strip().split("argus_tachibana_live.")[1].split("(")[0].split(",")[0]
            for line in wiring} == {"ensure_started", "current_evidence_safe",
                                    "SCHEMA", "PROVIDER", "AUTHORITY"}
    boundary = Path("argus_tachibana_live.py").read_text(encoding="utf-8")
    for forbidden in ("NewOrder", "sOrder", "CLMKabu", "Cancel", "Correct"):
        assert forbidden not in boundary
    assert "SHADOW_NON_AUTHORITATIVE" in boundary



def test_jp_realtime_lamp_follows_tachibana_boundary(monkeypatch):
    """RECOVERY_ONLY v13.5.45: the raw backend lamp is Tachibana-true, not a UI overlay."""
    import scanner
    monkeypatch.setattr(scanner.argus_tachibana_live, "current_evidence_safe",
                        lambda: {"enabled": True, "status": "CLOSED",
                                 "symbols": {"5803": {"price": 1.0}, "8058": {"price": 2.0}}})
    assert scanner._tachibana_jp_realtime_lamp() == ("ok", "Tachibana 接続確認済 · 市場クローズ(5803/8058)")
    monkeypatch.setattr(scanner.argus_tachibana_live, "current_evidence_safe",
                        lambda: {"enabled": True, "status": "AUTH_FAILED", "authBoundary": "AUTH_KEY_PARSE_FAILED",
                                 "symbols": {}})
    assert scanner._tachibana_jp_realtime_lamp() == ("warning", "Tachibana 認証失敗(AUTH_KEY_PARSE_FAILED)")
    monkeypatch.setattr(scanner.argus_tachibana_live, "current_evidence_safe",
                        lambda: {"enabled": False, "status": "DISABLED", "symbols": {}})
    assert scanner._tachibana_jp_realtime_lamp() is None            # legacy moomoo lamp keeps its truth
    monkeypatch.setattr(scanner.argus_tachibana_live, "current_evidence_safe",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert scanner._tachibana_jp_realtime_lamp() is None



def test_jp_realtime_lamp_is_emitted_in_both_bridge_branches():
    """RECOVERY_ONLY follow-up: the Tachibana lamp does not depend on a moomoo heartbeat."""
    import inspect, scanner
    source = inspect.getsource(scanner)
    assert source.count('tachibana_lamp = _tachibana_jp_realtime_lamp()') == 2
    assert source.count('L("jp_realtime", "JP realtime", tachibana_lamp[0], tachibana_lamp[1])') == 2



def test_sho_statements_feed_uses_jquants_v2_summary_path():
    """RECOVERY_ONLY v13.5.48: the V1 /fins/statements path answers 403 since 2026-06-01."""
    import inspect, scanner
    source = inspect.getsource(scanner._sho_statements_rows)
    assert '"/fins/summary"' in source and '"/fins/statements"' not in source



def test_index_chart_route_is_cached_only_and_names_the_index(monkeypatch):
    """RECOVERY_ONLY v13.5.50: real index charts from the Yahoo OHLCV cache; never fetches."""
    import datetime as _dt
    import scanner
    client = scanner.app.test_client()
    scanner._SHO_INDEX_OHLCV_CACHE.pop("^N225", None)
    cold = client.get("/api/argus/index-chart?index=N225").get_json()
    assert cold["status"] == "expected_skip" and cold["stateUpdate"]["reason"] == "index_cache_cold"
    assert client.get("/api/argus/index-chart?index=DAX").status_code == 400
    start = _dt.date(2026, 1, 5)
    rows = []
    day, i = start, 0
    while len(rows) < 120:
        if day.weekday() < 5:
            close = 40000 + i * 25 + (i % 7) * 60
            rows.append({"instrumentId": "NIKKEI_225_INDEX", "date": day.isoformat(),
                         "open": close - 50, "high": close + 120, "low": close - 130, "close": float(close),
                         "volume": 1.0e8, "availableFrom": day.isoformat() + "T07:00:00Z",
                         "adjusted": False, "sourceRef": "yahoo:chart:^N225"})
            i += 1
        day += _dt.timedelta(days=1)
    scanner._SHO_INDEX_OHLCV_CACHE["^N225"] = {"data": rows, "expires": 9e12}
    calls = []

    class _NoNetwork:
        status_code = 503
        def json(self):
            return {}
        def raise_for_status(self):
            raise RuntimeError("offline")

    def _get(url, *a, **k):
        calls.append(str(url))
        return _NoNetwork()

    monkeypatch.setattr(scanner.requests, "get", _get)
    try:
        body = client.get("/api/argus/index-chart?index=N225").get_json()
    finally:
        scanner._SHO_INDEX_OHLCV_CACHE.pop("^N225", None)
    assert not any("finance.yahoo.com" in url for url in calls)      # index rows are cached-only
    assert body["index"] == "N225" and body["displayNameJa"] == "日経平均株価(指数)"
    assert body["proxyDisclosureJa"] is None and "指数そのもの" in body["indexDisclosureJa"]
    assert body["instrumentMetadata"]["assetType"] == "INDEX"
    assert body["instrumentMetadata"]["source"] == "yahoo_index_ohlcv"
    assert body["instrumentMetadata"]["sourceSymbol"] == "^N225"
    assert len(body["indicators"]["bars"]) >= 100


def test_us_daily_bar_is_eod_evidence_not_malformed():
    """A Twelve Data daily bar carries a date-only stamp, exactly like the
    J-Quants bar beside it.  It must be declared EOD, not MALFORMED/UNKNOWN:
    the consumer boundary keys off delayClass, and UNKNOWN is never
    decision-usable, which stripped every US row of its position P&L while the
    JP row on the same kind of close stayed usable (owner report 2026-09-04)."""
    import datetime as _dt
    import argus_market_clock as _clock
    # v13.5.55: "yesterday" is not a session on Sat/Sun/Mon (and holidays) — this
    # test failed on main every weekend. A dated EOD bar is evidence for the
    # latest COMPLETED exchange session, so derive the date from the same
    # clock seam the provider adapters use.
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    day = _clock.latest_completed_session_date(_clock.US_EQUITY, now_utc).isoformat()
    jp_day = _clock.latest_completed_session_date(_clock.JP_EQUITY, now_utc).isoformat()
    row = scanner._td_parse_row(
        {"symbol": "NVDA", "name": "NVIDIA"},
        {"close": "180.5", "change": "1.5", "percent_change": "0.8",
         "volume": "1000", "datetime": day,
         "open": "179", "high": "181", "low": "178"})
    assert row is not None
    assert row["delayClass"] == "EOD"
    assert row["sourceTimeStatus"] == "PRESENT"
    assert row["sourceTimestampPrecision"] == "date_only_eod"
    # An EOD period is never realtime evidence, whatever the label says.
    assert row["realtimeEvidence"] is False and row["status"] == "delayed"
    # The declaration must SURVIVE a cache round trip. The snapshot and
    # cached-row projections re-run the canonical contract over stored rows, so
    # a fix applied only where the row is built is undone the moment the row is
    # read back — which is how a correctly declared J-Quants EOD row was also
    # being downgraded to UNKNOWN on the _quote_cached_only path.
    assert scanner._canonical_cached_quote_row_age(row)["delayClass"] == "EOD"
    assert scanner._canonical_quote_snapshot_age(
        {"status": "delayed", "stocks": [row]}, "stocks")["stocks"][0]["delayClass"] == "EOD"
    jq_row = {"symbol": "8058", "name": "三菱商事", "price": 5059.0,
              "status": "delayed", "date": jp_day, "sourceTimestamp": jp_day,
              "delayClass": "EOD", "source": "jquants", "realtimeEvidence": False}
    assert scanner._canonical_cached_quote_row_age(jq_row)["delayClass"] == "EOD"
    # An EOD close is what the daily decision path is allowed to judge on.
    for candidate, market in ((row, "US"), (jq_row, "JP")):
        usable = scanner._decision_usable_watch_quote_row(
            candidate, market, allow_delayed=True, require_latest_completed=False)
        assert usable is not None and usable["price"] is not None

    # The exact-instant contract is untouched for a real timestamp, and a
    # stale or future date still fails closed.
    assert scanner._date_only_eod_source_truth("2026-09-03T20:00:00Z") is None
    assert scanner._date_only_eod_source_truth("not-a-date") is None
    assert scanner._canonical_quote_source_age(
        "not-a-source-time")["sourceTimeStatus"] == "MALFORMED"
    assert scanner._canonical_quote_source_age(None)["sourceTimeStatus"] == "MISSING"
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)).strftime("%Y-%m-%d")
    ahead = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=3)).strftime("%Y-%m-%d")
    assert scanner._date_only_eod_source_truth(old)["delayClass"] == "UNKNOWN"
    future = scanner._date_only_eod_source_truth(ahead)
    assert future["timestampInversion"] is True and future["delayClass"] == "UNKNOWN"


def test_public_dynamic_watchlist_answers_from_per_symbol_cache(monkeypatch):
    """The public GET may not fetch, and the dynamic branch caches under the
    EXACT requested tuple — so a device whose watchlist never matched a
    previously fetched batch got {"status": "mock", "stocks": []} forever and
    every US row rendered with no price (owner report 2026-09-04).  The
    cache-only path must assemble what it already holds, per symbol, and stay
    honest about what it could not resolve."""
    import datetime as _dt
    day = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    monkeypatch.setitem(scanner._US_CACHE, "data", {
        "status": "delayed", "asOf": day, "provider": "twelvedata",
        "stocks": [{"symbol": "NVDA", "name": "NVIDIA", "price": 180.0,
                    "status": "delayed", "date": day,
                    "sourceTimestamp": day, "delayClass": "EOD"}]})
    monkeypatch.setitem(scanner._US_CACHE, "expires", scanner.time.time() + 600)
    monkeypatch.setattr(scanner, "_US_DYN_CACHE", {})
    calls = []

    class _NoNetwork:
        status_code = 503

        def json(self):
            return {}

        def raise_for_status(self):
            raise RuntimeError("offline")

    def _no_fetch(url, *a, **k):
        calls.append(str(url))
        return _NoNetwork()

    monkeypatch.setattr(scanner.requests, "get", _no_fetch)
    body = scanner.app.test_client().get(
        "/api/argus/us-watchlist?symbols=NVDA,IONQ").get_json()
    assert not calls
    symbols = [row["symbol"] for row in body["stocks"]]
    assert symbols == ["NVDA"]                       # cached row is served…
    assert body["status"] == "partial"               # …and the gap is admitted
    assert body["coverage"] == {"live": 0, "delayed": 1, "mock": 1, "total": 2}
    # Nothing cached at all still refuses to invent a quote.
    empty = scanner.get_us_watchlist_snapshot(["ZZZZ"], allow_provider_fetch=False)
    assert empty["status"] == "mock" and empty["stocks"] == []


def test_public_watchlist_names_the_symbols_the_cap_dropped(monkeypatch):
    """A bound the owner cannot see is indistinguishable from data loss.

    v13.5.54 (owner 2026-09-05): "8 credits/minute" is a REQUEST BATCH cap, not
    a universe size, so a cache-only read of nine symbols is no longer
    truncated to eight — the ninth assembles from the per-symbol caches. The
    bound the owner CAN still hit is the AUTHORIZED universe cap, and when it
    drops something the response names what it dropped, in request order, and
    stays silent when nothing was dropped."""
    import datetime as _dt
    day = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    nine = ("SPCX", "IONQ", "SOXS", "SOXL", "NVDA", "AAPL", "MU", "TSLA", "META")
    monkeypatch.setitem(scanner._US_CACHE, "data", {
        "status": "delayed", "asOf": day, "provider": "twelvedata",
        "stocks": [{"symbol": s, "name": s, "price": 100.0, "status": "delayed",
                    "date": day, "sourceTimestamp": day, "delayClass": "EOD"}
                   for s in nine]})
    monkeypatch.setitem(scanner._US_CACHE, "expires", scanner.time.time() + 600)
    monkeypatch.setattr(scanner, "_US_DYN_CACHE", {})
    monkeypatch.setattr(scanner, "_US_WARM_ROWS", {})
    monkeypatch.setattr(scanner, "_US_DYN_MAX", 8)
    monkeypatch.setattr(scanner, "_US_UNIVERSE_CAP", 24)
    client = scanner.app.test_client()

    nine_body = client.get("/api/argus/us-watchlist?symbols=" + ",".join(nine)).get_json()
    assert [r["symbol"] for r in nine_body["stocks"]] == list(nine)
    assert nine_body["coverage"]["total"] == 9 and nine_body["coverage"]["mock"] == 0
    for key in ("requestedSymbolCount", "symbolCap", "droppedSymbols", "droppedReason"):
        assert key not in nine_body, key          # nine symbols: nothing dropped

    monkeypatch.setattr(scanner, "_US_UNIVERSE_CAP", 4)
    over = client.get("/api/argus/us-watchlist?symbols=" + ",".join(nine)).get_json()
    assert over["requestedSymbolCount"] == 9
    assert over["symbolCap"] == 4                  # the AUTHORIZED universe cap
    assert over["droppedSymbols"] == list(nine[4:])  # keeps request order
    assert over["droppedReason"] == "symbol_cap"
    assert [r["symbol"] for r in over["stocks"]] == list(nine[:4])

    inside = client.get("/api/argus/us-watchlist?symbols=NVDA,AAPL").get_json()
    for key in ("requestedSymbolCount", "symbolCap", "droppedSymbols", "droppedReason"):
        assert key not in inside, key

    # Invalid symbols are rejected by the pattern, never counted as dropped.
    assert scanner._symbols_over_cap(["NVDA", "!!", "AAPL"], scanner._US_SYM_RE, 2) == []
    # Duplicates do not consume cap slots twice.
    assert scanner._symbols_over_cap(
        ["NVDA", "NVDA", "AAPL", "TSLA"], scanner._US_SYM_RE, 2) == ["TSLA"]


# ── Twelve Data Basic-plan warm scheduler glue (v13.5.54, owner 2026-09-05) ──

_TD_NINE = ["NVDA", "AAPL", "TSLA", "META", "SPCX", "IONQ", "SOXS", "SOXL", "MU"]


def _td_fresh_state(monkeypatch, *, session="REGULAR", owner=("SPCX", "IONQ", "SOXS", "SOXL", "MU")):
    import argus_td_warm
    monkeypatch.setattr(scanner, "_TD_WARM_STATE", argus_td_warm.new_state())
    monkeypatch.setattr(scanner, "_US_WARM_ROWS", {})
    monkeypatch.setattr(scanner, "_TD_WARM_UNIVERSE_CACHE", {"data": None, "expires": 0.0})
    monkeypatch.setattr(scanner, "_US_DYN_CACHE", {})
    monkeypatch.setitem(scanner._US_CACHE, "data", None)
    monkeypatch.setitem(scanner._US_CACHE, "expires", 0.0)
    monkeypatch.setattr(scanner, "_TWELVEDATA_API_KEY", "test-key")
    monkeypatch.setattr(scanner, "_TD_WARM_ENABLED", True)
    monkeypatch.setattr(scanner, "_US_DYN_MAX", 8)
    monkeypatch.setattr(scanner, "_US_UNIVERSE_CAP", 24)
    monkeypatch.setattr(scanner, "_TD_WARM_DAILY_CAP", 560)
    monkeypatch.setattr(scanner, "_td_owner_us_members", lambda: list(owner))
    monkeypatch.setattr(scanner, "_td_us_session", lambda now_utc=None: session)


def _td_fake_provider(monkeypatch, calls, *, status=200, body_status=None):
    import datetime as _dt

    class _Resp:
        def __init__(self, syms):
            self.status_code = status
            self._syms = syms

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

        def json(self):
            if body_status:
                return body_status
            stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            rows = {s: {"symbol": s, "name": s, "close": "100.5", "change": "1.0",
                        "percent_change": "1.0", "volume": "1000", "datetime": stamp}
                    for s in self._syms}
            return rows if len(self._syms) > 1 else rows[self._syms[0]]

    def _get(url, params=None, **k):
        syms = [x for x in str((params or {}).get("symbol") or "").split(",") if x]
        calls.append(syms)
        return _Resp(syms)

    monkeypatch.setattr(scanner.requests, "get", _get)


def test_unset_plan_is_basic_and_caps_are_separate_concepts():
    assert scanner._TD_PLAN_NAME in ("basic", scanner._TWELVEDATA_PLAN)
    if not scanner._TWELVEDATA_PLAN:
        assert scanner._TD_PLAN_NAME == "basic"
        assert scanner._US_DYN_MAX == 8            # request batch cap
        assert scanner._US_UNIVERSE_CAP == 24      # authorized universe cap
        assert scanner._TD_WARM_DAILY_CAP == 560   # 70% of 800, 240 in reserve


def test_warm_tick_rotates_nine_symbols_across_two_eligible_minutes(monkeypatch):
    """The ninth symbol is fetched on the next eligible minute, never dropped,
    and the public cache-only GET then serves all nine with per-symbol
    source times. No credit is spent twice on a symbol in both sets."""
    import datetime as _dt
    _td_fresh_state(monkeypatch)
    calls = []
    _td_fake_provider(monkeypatch, calls)
    # The scheduler and its cache consumer must use the same test clock.
    t0 = _dt.datetime.now(_dt.timezone.utc)
    first = scanner._td_warm_tick(now_utc=t0)
    assert first["action"] == "fetch" and first["ok"] is True
    assert calls == [_TD_NINE[:8]]                    # one request, 8 credits
    assert first["rowsStored"] == 8
    again = scanner._td_warm_tick(now_utc=t0 + _dt.timedelta(seconds=30))
    assert again["action"] == "skip" and again["reason"] == "minute_gap"
    second = scanner._td_warm_tick(now_utc=t0 + _dt.timedelta(seconds=61))
    assert second["action"] == "fetch" and calls[-1] == ["MU"]
    assert scanner._TD_WARM_STATE["usedToday"] == 9
    assert scanner._TD_WARM_STATE["warmSymbolCount"] == 9

    body = scanner.app.test_client().get(
        "/api/argus/us-watchlist?symbols=" + ",".join(_TD_NINE)).get_json()
    assert [r["symbol"] for r in body["stocks"]] == _TD_NINE
    assert "droppedSymbols" not in body
    assert all(r.get("sourceTimestamp") for r in body["stocks"])
    assert body["coverage"]["total"] == 9 and body["coverage"]["mock"] == 0
    assert len(calls) == 2                            # the GET fetched nothing


def test_curated_snapshot_is_served_from_warm_rows_without_a_second_credit(monkeypatch):
    import datetime as _dt
    _td_fresh_state(monkeypatch, owner=())
    calls = []
    _td_fake_provider(monkeypatch, calls)
    # The scheduler and its cache consumer must use the same test clock.
    t0 = _dt.datetime.now(_dt.timezone.utc)
    assert scanner._td_warm_tick(now_utc=t0)["ok"] is True
    assert calls == [["NVDA", "AAPL", "TSLA", "META"]]
    snap = scanner._get_us_watchlist_core(None, allow_provider_fetch=True)
    assert snap.get("cacheState") == "warm_scheduler"
    assert [r["symbol"] for r in snap["stocks"]] == ["NVDA", "AAPL", "TSLA", "META"]
    assert len(calls) == 1                            # no duplicate provider call
    assert scanner._quote_cached_only("META", "US")["price"] == 100.5


def test_closed_market_is_not_polled_and_budget_endpoint_lists_no_symbols(monkeypatch):
    import datetime as _dt
    _td_fresh_state(monkeypatch, session="OVERNIGHT_CLOSED")
    calls = []
    _td_fake_provider(monkeypatch, calls)
    t0 = _dt.datetime(2026, 9, 8, 6, 0, tzinfo=_dt.timezone.utc)
    # v13.5.61: with warm rows present the closed market is never polled; a
    # fresh (redeployed) process with NO rows performs one cold fill instead —
    # see test_closed_market_cold_fill_runs_one_bounded_rotation_after_a_redeploy.
    scanner._TD_WARM_STATE["warmSymbolCount"] = len(_TD_NINE)
    skipped = scanner._td_warm_tick(now_utc=t0)
    assert skipped["action"] == "skip" and skipped["reason"] == "market_closed"
    assert calls == []
    body = scanner.app.test_client().get("/api/argus/twelvedata-budget").get_json()
    assert body["plan"] == "basic" and body["planImpersonated"] is False
    assert body["requestBatchCap"] == 8 and body["authorizedUniverseCap"] == 24
    assert body["dailyBudget"] == 800 and body["warmDailyCap"] == 560
    assert body["universeSize"] == 9 and body["estimatedDailyUsage"] <= 560
    assert body["budgetWithinDailyLimit"] is True
    flat = json.dumps(body)
    for s in _TD_NINE:
        assert s not in flat, s                       # counts only, never symbols


def test_rate_limit_backs_off_and_charges_the_attempt(monkeypatch):
    import datetime as _dt
    _td_fresh_state(monkeypatch)
    calls = []
    _td_fake_provider(monkeypatch, calls, status=429)
    t0 = _dt.datetime(2026, 9, 8, 14, 0, tzinfo=_dt.timezone.utc)
    first = scanner._td_warm_tick(now_utc=t0)
    assert first["rateLimited"] is True and first["ok"] is False
    assert scanner._TD_WARM_STATE["usedToday"] == 8
    assert scanner._US_WARM_ROWS == {}
    held = scanner._td_warm_tick(now_utc=t0 + _dt.timedelta(seconds=61))
    assert held["action"] == "skip" and held["reason"] == "rate_limited_backoff"
    assert len(calls) == 1


def test_warm_tick_without_an_api_key_never_calls_the_provider(monkeypatch):
    _td_fresh_state(monkeypatch)
    monkeypatch.setattr(scanner, "_TWELVEDATA_API_KEY", None)
    calls = []
    _td_fake_provider(monkeypatch, calls)
    assert scanner._td_warm_tick()["reason"] == "no_api_key" and calls == []


def test_decision_evidence_falls_back_to_the_published_verified_snapshot(monkeypatch):
    """A cold provider cache must not read as "no market data".

    _decision_evidence_history_row sourced only the provider history cache,
    which is empty after every restart and for six hours after a session close
    (pure time TTL, no session boundary). Measured in production 2026-09-04
    19:53Z: the verified snapshots held SPY/QQQ through 2026-09-03 — the latest
    completed US session — while this function returned nothing and both
    subjects reported marketTruth MISSING, so the owner saw a chart drawn from
    data the decision claimed not to have. The verified snapshot is the same
    canonical evidence the headline bootstrap publishes; reading it adds no
    provider call and no new authority."""
    monkeypatch.setattr(scanner, "_chart_history_cached", lambda *a, **k: [])

    # Nothing published anywhere still fails closed.
    monkeypatch.setattr(scanner, "_verified_market_snapshot", lambda *a, **k: None)
    assert scanner._decision_evidence_history_row("SPY", "US") == (None, None)

    bars = [{"date": "2026-09-02", "close": 500.0},
            {"date": "2026-09-03", "close": 505.0}]
    monkeypatch.setattr(scanner, "_verified_market_snapshot",
                        lambda *a, **k: {"payload": {"indicators": {"bars": bars}}})
    quote, _ = scanner._decision_evidence_history_row("SPY", "US")
    assert quote is not None
    assert quote["date"] == "2026-09-03"          # the latest bar, not the first
    assert quote["price"] == 505.0
    assert quote["status"] == "delayed"           # never upgraded to live
    assert quote["sourceRef"].startswith("history-cache:SPY:")

    # Malformed bars are ignored rather than fabricated into a quote.
    monkeypatch.setattr(scanner, "_verified_market_snapshot",
                        lambda *a, **k: {"payload": {"indicators": {"bars": [
                            {"date": "2026-09-03"}, {"close": "x"}, "junk"]}}})
    assert scanner._decision_evidence_history_row("SPY", "US") == (None, None)

    # A STALE cache fails the same way an empty one does: on 2026-09-04 08:47Z
    # the cache still held 09-03 after the JP session of 09-04 had completed,
    # so marketTruth read STALE. Whichever source carries the LATER close wins.
    monkeypatch.setattr(scanner, "_chart_history_cached", lambda *a, **k: [
        {"date": "2026-09-02", "close": 490.0}, {"date": "2026-09-03", "close": 495.0}])
    monkeypatch.setattr(scanner, "_verified_market_snapshot",
                        lambda *a, **k: {"payload": {"indicators": {"bars": bars + [
                            {"date": "2026-09-04", "close": 510.0}]}}})
    fresher, _ = scanner._decision_evidence_history_row("SPY", "US")
    assert fresher["date"] == "2026-09-04" and fresher["price"] == 510.0

    # ...and it can never pick OLDER evidence than the cache already had.
    monkeypatch.setattr(scanner, "_verified_market_snapshot",
                        lambda *a, **k: {"payload": {"indicators": {"bars": [
                            {"date": "2026-08-01", "close": 400.0}]}}})
    kept, _ = scanner._decision_evidence_history_row("SPY", "US")
    assert kept["date"] == "2026-09-03" and kept["price"] == 495.0


def test_total_twelvedata_consumption_is_fitted_under_the_daily_limit(monkeypatch):
    """Owner 2026-09-05: TOTAL consumption across ALL call sites < daily limit
    with a reserve; reduce cadence automatically when the warm ceiling plus
    other traffic does not fit."""
    import datetime as _dt
    _td_fresh_state(monkeypatch)
    other = scanner._td_other_daily_credits(9)
    assert other > 0
    body = scanner.app.test_client().get("/api/argus/twelvedata-budget").get_json()
    assert body["minuteLimit"] == 8 and body["dailyLimit"] == 800
    assert body["otherConsumersDailyEstimate"] == other
    assert body["estimatedTotalDailyCredits"] < 800
    assert body["totalWithinReserve"] is True and body["reserveTarget"] == 80
    fit = body["cadenceFit"]
    assert fit["fitted"] is True
    assert body["cadence"]["regularSec"] == fit["regularSec"]
    assert "backoff" in body and body["backoff"]["active"] is False
    for s in _TD_NINE:
        assert s not in json.dumps(body), s


def test_warm_rows_outlive_a_closed_session_and_curated_refresh_is_ledgered(monkeypatch):
    import datetime as _dt
    _td_fresh_state(monkeypatch, owner=())
    calls = []
    _td_fake_provider(monkeypatch, calls)
    t0 = _dt.datetime(2026, 9, 8, 14, 0, tzinfo=_dt.timezone.utc)
    assert scanner._td_warm_tick(now_utc=t0)["ok"] is True
    # Closed-session TTL keeps the last row as the session's evidence.
    monkeypatch.setattr(scanner, "_td_us_session", lambda now_utc=None: "OVERNIGHT_CLOSED")
    scanner._td_warm_store([scanner._US_WARM_ROWS["NVDA"]["row"]], t0.timestamp(), "OVERNIGHT_CLOSED")
    assert scanner._US_WARM_ROWS["NVDA"]["expires"] - t0.timestamp() >= 24 * 3600 - 1
    # The curated refresh spends through the same ledger and stops at the cap.
    monkeypatch.setattr(scanner, "_US_WARM_ROWS", {})
    monkeypatch.setitem(scanner._US_CACHE, "data", None)
    monkeypatch.setitem(scanner._US_CACHE, "expires", 0.0)
    # The curated path rolls the ledger on the real clock; align the test
    # ledger day so the charge is additive rather than reset.
    import argus_td_warm as _tw
    scanner._TD_WARM_STATE["ledgerDay"] = _tw.utc_day(_dt.datetime.now(_dt.timezone.utc))
    before = scanner._TD_WARM_STATE["usedToday"]
    snap = scanner._get_us_watchlist_core(None, allow_provider_fetch=True)
    assert [r["symbol"] for r in snap["stocks"]] == ["NVDA", "AAPL", "TSLA", "META"]
    assert scanner._TD_WARM_STATE["usedToday"] == before + 4
    monkeypatch.setattr(scanner, "_TD_WARM_DAILY_CAP", scanner._TD_WARM_STATE["usedToday"])
    monkeypatch.setitem(scanner._US_CACHE, "expires", 0.0)
    n_calls = len(calls)
    capped = scanner._get_us_watchlist_core(None, allow_provider_fetch=True)
    assert len(calls) == n_calls                       # no provider call past the cap
    assert capped["stocks"]                            # last cached rows still served


# ── Released events stay in the source for the lifecycle's 72 h (v13.5.57) ──

def test_released_curated_event_stays_in_the_source_for_three_days(monkeypatch):
    """Owner 2026-09-05: 「イベントが出たばかりなので米雇用統計が出てない」, and
    after the ranking fix landed the release vanished again on day +2 — not
    from the ranking but from the SOURCE, which discarded any event whose JST
    date was more than one day old. argus_important_events keeps a completed
    release visible (RECENT/MONITORING) for 72 h; the source must not age it
    out sooner. Three days back stay; four days back are history."""
    import datetime as _dt
    today = _dt.date(2026, 9, 6)                      # Sunday JST
    dates = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05"]
    monkeypatch.setattr(scanner, "_EVENT_SPECS", [
        (dates, "08:30", "nfp", "US Employment Situation", "jobs", "US",
         "Bureau of Labor Statistics", "high", ["SPY"])])
    out = scanner._build_curated_events(today)
    kept = sorted(e["eventDate"] for e in out)
    assert kept == ["2026-09-03", "2026-09-04", "2026-09-05"], kept
    by_date = {e["eventDate"]: e for e in out}
    assert by_date["2026-09-04"]["daysUntil"] == -2
    assert by_date["2026-09-03"]["daysUntil"] == -3
    assert scanner._EVENT_RELEASED_KEEP_DAYS == 3
    # The auction builder shares the same window.
    import inspect
    src = inspect.getsource(scanner._build_auction_events)
    assert "days < -_EVENT_RELEASED_KEEP_DAYS" in src


# ── News visibility and retention follow RECEIPT time, not processing order ──

def _news_fixture_event(day, hour, subject, severity="WATCH"):
    import argus_news_intelligence as ni
    received = f"2026-09-{day:02d}T{hour:02d}:00:00Z"
    taxonomy = ni.classify_event(subject)
    materiality = ni.evaluate_materiality(
        taxonomy=taxonomy, staleness="DELAYED", source_authenticated=True,
        ai_analysis=None, corroboration={"confirmed": False}, subject=subject,
        source="NIKKEI")
    materiality = dict(materiality, severity=severity)
    identity = ni.event_identity(event_type=taxonomy["eventType"],
                                 subject=subject, day=f"2026-09-{day:02d}")
    message = {"eventIdentity": identity,
               "fingerprint": ni.source_fingerprint(message_id=f"m-{day}-{hour}",
                                                    subject=subject, url=None),
               "subject": subject, "url": None, "headlineJa": subject,
               "receivedIso": received, "publishedIso": received, "backfill": False}
    event = ni.build_news_event(
        message=message, taxonomy=taxonomy, staleness="DELAYED",
        materiality=materiality, ai_analysis=None,
        corroboration={"confirmed": False, "readings": []},
        analysis_state="DETERMINISTIC_ONLY", processed_iso=received,
        source="NIKKEI")
    return identity, event


def _news_fresh_store(monkeypatch):
    fresh = {"intakeState": {}, "events": {}, "order": [], "audit": [],
             "aiCache": {}, "observedSenders": {},
             "health": dict(scanner._NEWS_INTEL["health"], status="HEALTHY")}
    monkeypatch.setattr(scanner, "_NEWS_INTEL", fresh)
    monkeypatch.setitem(scanner._NEWS_LOADED, "value", True)
    return fresh


def test_public_news_window_is_the_most_recent_by_receipt_not_last_processed(monkeypatch):
    """Production 2026-09-06: after an owner-triggered reprocess (backfill walks
    the mailbox newest-first, so the OLDEST mail is processed LAST), the public
    list showed a batch from 08-28..09-01 and the released NFP story (09-04)
    vanished — it was still in the store, hidden behind `order[-12:]`."""
    store = _news_fresh_store(monkeypatch)
    # 13 recent events processed first (09-02..09-06), then a backfill appends
    # 12 older ones (08-25..08-30) LAST in processing order.
    recent = [_news_fixture_event(2 + i // 3, 8 + (i % 3) * 4, f"米金利 見出し {i}") for i in range(13)]
    for i, (identity, event) in enumerate(recent):
        store["events"][identity] = event; store["order"].append(identity)
    older = [_news_fixture_event(1, hour, f"旧ニュース {hour}") for hour in range(0, 12)]
    for identity, event in older:
        store["events"][identity] = event; store["order"].append(identity)
    body = scanner.app.test_client().get("/api/argus/news-intelligence").get_json()
    received = [e["sourceReceivedAt"] for e in body["events"]]
    assert len(received) == 12
    assert received == sorted(received, reverse=True)
    assert min(received) >= "2026-09-02", received       # no 09-01 batch leaks in
    assert not any("旧ニュース" in (e.get("titleOriginal") or "") for e in body["events"])


def test_news_retention_evicts_the_oldest_by_receipt_not_the_first_processed(monkeypatch):
    store = _news_fresh_store(monkeypatch)
    monkeypatch.setattr(scanner, "_NEWS_EVENT_CAP", 3)
    newest_id, newest = _news_fixture_event(6, 9, "最新の材料")
    store["events"][newest_id] = newest; store["order"].append(newest_id)
    for hour in (1, 2, 3):                       # older mail processed later
        identity, event = _news_fixture_event(1, hour, f"古い材料 {hour}")
        store["events"][identity] = event
        if identity in store["order"]:
            store["order"].remove(identity)
        store["order"].append(identity)
        while len(store["order"]) > scanner._NEWS_EVENT_CAP:
            dropped = min(store["order"], key=lambda eid: scanner._news_event_recency_epoch(store["events"].get(eid)))
            store["order"].remove(dropped); store["events"].pop(dropped, None)
    assert newest_id in store["events"], "the newest event must survive a backfill"
    assert len(store["order"]) == 3
    assert scanner._news_event_recency_epoch({"sourceReceivedAt": "not-a-date"}) == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# v13.5.60 Recovery payload — runtime truth under load (production 2026-09-07).
# The heavier public documents stalled past every client timeout while cheap
# cached routes kept answering. These pin the three responses: single-flight
# computation sharing (no thundering herd), a bounded `busy` instead of a hung
# socket, and an owner-only thread-location dump so the next stall can be
# attributed from inside the process; plus the two owner-visible truth fixes
# shipped alongside — the important-events list covers the coming month, and
# the US-only moomoo bridge is not a warning during a Tokyo-only session.
# ═══════════════════════════════════════════════════════════════════════════
import threading as _rt_threading
import time as _rt_time
threading = _rt_threading
time = _rt_time


# ── single-flight ────────────────────────────────────────────────────────────

def test_single_flight_shares_one_computation_between_concurrent_callers():
    calls = []
    gate = threading.Event()

    def compute():
        calls.append(threading.get_ident())
        gate.wait(2.0)
        return {"value": 42}

    results = {}

    def follower():
        results["follower"] = scanner._single_flight("t:shared", compute)

    leader_result = {}

    def leader():
        leader_result["value"] = scanner._single_flight("t:shared", compute)

    t1 = threading.Thread(target=leader)
    t1.start()
    time.sleep(0.05)                       # the leader is inside compute()
    t2 = threading.Thread(target=follower)
    t2.start()
    time.sleep(0.05)
    gate.set()
    t1.join(3); t2.join(3)
    assert len(calls) == 1                  # computed exactly once
    assert leader_result["value"] == ({"value": 42}, "computed")
    assert results["follower"] == ({"value": 42}, "joined")
    assert scanner._single_flight_status() == []   # nothing left in flight


def test_single_flight_follower_gets_busy_not_a_hung_socket():
    gate = threading.Event()

    def compute():
        gate.wait(5.0)
        return "late"

    t = threading.Thread(target=lambda: scanner._single_flight("t:slow", compute))
    t.start()
    time.sleep(0.05)
    result, mode = scanner._single_flight("t:slow", compute, wait_sec=0.1)
    assert (result, mode) == (None, "busy")
    status = scanner._single_flight_status()
    assert status and status[0]["key"] == "t:slow" and status[0]["ageSec"] >= 0
    gate.set(); t.join(3)


def test_single_flight_propagates_the_leader_error_and_clears_the_key():
    def compute():
        raise ValueError("boom")
    try:
        scanner._single_flight("t:err", compute)
        assert False, "expected the error to propagate"
    except ValueError:
        pass
    assert scanner._single_flight_status() == []


def test_busy_response_is_a_bounded_503_with_retry_after():
    with scanner.app.test_request_context("/api/argus/decision-evidence"):
        response, code = scanner._busy_response("decision-evidence", time.time())
        assert code == 503
        assert response.headers["Retry-After"] == "5"
        body = response.get_json()
        assert body["error"] == "busy"
        assert body["reason"] == "single_flight_wait_exceeded"
        assert body["route"] == "decision-evidence"


def test_public_documents_carry_the_flight_and_elapsed_headers(monkeypatch):
    monkeypatch.setattr(scanner, "get_events_snapshot",
                        lambda **_: {"status": "live", "events": []})
    with scanner.app.test_client() as client:
        response = client.get("/api/argus/events")
    assert response.status_code == 200
    assert response.headers["X-ARGUS-Flight"] == "computed"
    assert response.headers["X-ARGUS-Elapsed-Ms"].isdigit()


# ── important events: the coming month ───────────────────────────────────────

def _event(code, days, impact="high"):
    return {"id": f"{code}-{days}", "eventCode": code, "title": code,
            "category": "macro", "country": "US", "source": "test",
            "impact": impact, "eventTimeUtc": None, "eventDate": None,
            "localTimeJst": None, "daysUntil": days, "escalation": "normal",
            "rationaleJa": "", "linkedAssets": [], "status": "live"}


def test_important_events_list_covers_the_coming_month_not_eight_rows(monkeypatch):
    events = [_event(f"EV{d}", d) for d in range(1, 41)]   # 40 events, one per day
    monkeypatch.setattr(scanner, "get_events_snapshot",
                        lambda **_: {"status": "live", "asOf": "x", "events": events})
    monkeypatch.setattr(scanner, "_owner_symbols_cached", lambda: {})
    monkeypatch.setattr(scanner, "get_rates_snapshot", lambda: {})
    body = scanner._important_events_data()
    days = [row["daysUntil"] for row in body["events"]]
    assert len(days) > 8, "the old eight-row cap hid the rest of the month"
    assert len(days) <= scanner._IMPORTANT_EVENTS_DISPLAY_CAP
    assert max(days) <= scanner._IMPORTANT_EVENTS_HORIZON_DAYS
    assert days == sorted(days), "backend order is unchanged (single authority)"


def test_important_events_horizon_constants():
    assert scanner._IMPORTANT_EVENTS_HORIZON_DAYS == 31
    assert scanner._IMPORTANT_EVENTS_DISPLAY_CAP == 24


# ── the moomoo bridge lamp is US-only ────────────────────────────────────────

def test_legacy_bridge_lamp_is_idle_not_warning_during_a_tokyo_only_session(monkeypatch):
    monkeypatch.setitem(scanner._BRIDGE_HB, "data", None)
    monkeypatch.setitem(scanner._BRIDGE_HB, "receivedAt", 0.0)
    monkeypatch.setattr(scanner, "_jp_market_open", lambda: True)
    monkeypatch.setattr(scanner, "_us_market_open", lambda: False)
    monkeypatch.setattr(scanner, "_PUSHED_QUOTES", {"US": {}, "JP": {}})
    lamps = {l["key"]: l for l in scanner._system_health(allow_provider_fetch=False)["lamps"]}
    assert lamps["bridge"]["status"] == "off"
    assert "Tachibana" in lamps["bridge"]["detailJa"]
    assert "旧ブリッジ" in lamps["bridge"]["detailJa"]


def test_legacy_bridge_lamp_still_warns_during_a_us_session_without_pushes(monkeypatch):
    monkeypatch.setitem(scanner._BRIDGE_HB, "data", None)
    monkeypatch.setitem(scanner._BRIDGE_HB, "receivedAt", 0.0)
    monkeypatch.setattr(scanner, "_jp_market_open", lambda: False)
    monkeypatch.setattr(scanner, "_us_market_open", lambda: True)
    monkeypatch.setattr(scanner, "_PUSHED_QUOTES", {"US": {}, "JP": {}})
    lamps = {l["key"]: l for l in scanner._system_health(allow_provider_fetch=False)["lamps"]}
    assert lamps["bridge"]["status"] == "warning"
    assert "US市場時間中なのにpush無し" in lamps["bridge"]["detailJa"]


# ── owner-only thread dump ───────────────────────────────────────────────────

def test_runtime_thread_dump_rides_the_owner_only_memory_route(monkeypatch):
    monkeypatch.setattr(scanner, "_ARGUS_ADMIN_TOKEN", "tok")
    with scanner.app.test_client() as client:
        assert client.get("/api/argus/admin/memory-attribution?threads=1").status_code == 401
        plain = client.get("/api/argus/admin/memory-attribution",
                           headers={"X-ARGUS-ADMIN-TOKEN": "tok"})
        response = client.get("/api/argus/admin/memory-attribution?threads=1",
                              headers={"X-ARGUS-ADMIN-TOKEN": "tok"})
    assert plain.status_code == 200 and "runtimeThreads" not in plain.get_json()
    assert response.status_code == 200
    body = response.get_json()["runtimeThreads"]
    assert body["schemaVersion"] == "argus-runtime-thread-dump-v1"
    assert body["threadCount"] >= 1
    assert isinstance(body["singleFlight"], list)
    names = [row["name"] for row in body["threads"]]
    assert names, "at least the request thread is listed"
    for row in body["threads"]:
        for frame in row["frames"]:
            assert set(frame) == {"file", "line", "function"}
            assert "/" not in frame["file"].split("scanner.py")[0] or not frame["file"].startswith("/"), \
                "frame paths are repository-relative or basenames, never absolute"


def test_runtime_thread_dump_never_carries_values_or_environment():
    dump = scanner._runtime_thread_dump()
    text = str(dump)
    for forbidden in ("ARGUS_ADMIN_TOKEN", "JQUANTS_API_KEY", "TWELVEDATA", "locals", "argv"):
        assert forbidden not in text



# ═══════════════════════════════════════════════════════════════════════════
# v13.5.60 Recovery payload — JP daily history is re-checked once the latest
# completed Tokyo session is missing from the cache (production 2026-09-07:
# 1321 fetched at 15:43 JST held the previous session for the whole six-hour
# TTL and the market-truth reference read STALE all evening).
# ═══════════════════════════════════════════════════════════════════════════
import datetime as _fresh_dt


def _jp_history(newest):
    return {"dates": [newest, "2026-09-03"], "closes": [1.0, 1.0], "opens": [1.0, 1.0],
            "highs": [1.0, 1.0], "lows": [1.0, 1.0], "volumes": [1, 1], "adjusted": [False, False]}


def test_jp_history_behind_completed_session_is_detected_from_the_calendar():
    after_close = _fresh_dt.datetime(2026, 9, 7, 7, 0, tzinfo=_fresh_dt.timezone.utc)  # 16:00 JST Monday
    assert scanner._jq_history_behind_completed_session(_jp_history("2026-09-04"), after_close) is True
    assert scanner._jq_history_behind_completed_session(_jp_history("2026-09-07"), after_close) is False
    before_close = _fresh_dt.datetime(2026, 9, 7, 3, 0, tzinfo=_fresh_dt.timezone.utc)  # 12:00 JST, session open
    assert scanner._jq_history_behind_completed_session(_jp_history("2026-09-04"), before_close) is False
    assert scanner._jq_history_behind_completed_session({"dates": []}, after_close) is False
    assert scanner._jq_history_behind_completed_session(None, after_close) is False


def test_jp_history_cache_rechecks_every_fifteen_minutes_while_behind(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(scanner.time, "time", lambda: now)
    monkeypatch.setattr(scanner, "_jq_history_behind_completed_session", lambda data, now_utc=None: True)
    cache = {"data": _jp_history("2026-09-04"), "expires": now + 5 * 3600,
             "acquiredAt": "x", "sessionRecheckAt": now + 600}
    monkeypatch.setitem(scanner._JQ_HISTORY_CACHE, "1321", cache)
    calls = []
    monkeypatch.setattr(scanner, "_JQUANTS_API_KEY", "k")
    monkeypatch.setattr(scanner.requests, "get", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(RuntimeError("no network")))
    # inside the re-check window: the cache is served, no provider call
    assert scanner._jq_price_history("1321") is cache["data"]
    assert calls == []
    # re-check due: the provider is asked; a failed re-check keeps the last good
    # history and arms the next re-check instead of blanking the chart
    cache["sessionRecheckAt"] = now - 1
    assert scanner._jq_price_history("1321") is cache["data"]
    assert len(calls) >= 1
    assert scanner._JQ_HISTORY_CACHE["1321"]["sessionRecheckAt"] == now + scanner._JQ_HISTORY_SESSION_RECHECK_SEC


def test_jp_history_cache_with_the_completed_session_present_waits_out_the_ttl(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(scanner.time, "time", lambda: now)
    monkeypatch.setattr(scanner, "_jq_history_behind_completed_session", lambda data, now_utc=None: False)
    cache = {"data": _jp_history("2026-09-07"), "expires": now + 5 * 3600,
             "acquiredAt": "x", "sessionRecheckAt": now - 1}
    monkeypatch.setitem(scanner._JQ_HISTORY_CACHE, "1306", cache)
    monkeypatch.setattr(scanner.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")))
    assert scanner._jq_price_history("1306") is cache["data"]


# ═══════════════════════════════════════════════════════════════════════════
# v13.5.62 Recovery payload — the news intake processes a digest mail one
# article at a time (GPT review item 5: a yen headline carried a Hormuz line).
# ═══════════════════════════════════════════════════════════════════════════

def test_news_intake_processes_every_article_of_a_digest_mail(monkeypatch):
    processed = []
    monkeypatch.setattr(scanner, "_news_process_message",
                        lambda message, backfill=False: processed.append(message["messageId"]))
    monkeypatch.setattr(scanner.argus_news_intelligence, "split_digest_message",
                        lambda message: [{**message, "messageId": f"{message['messageId']}#1", "subject": "A"},
                                         {**message, "messageId": f"{message['messageId']}#2", "subject": "B"}]
                        if message["messageId"] == "digest" else [message], raising=False)
    monkeypatch.setattr(scanner.argus_gmail_intake, "run_intake_cycle",
                        lambda **kw: {"status": "HEALTHY", "state": {}, "messages": [
                            {"messageId": "digest", "subject": "日経ニュースメール 夕版", "excerpt": "◆A\n◆B"},
                            {"messageId": "single", "subject": "単独記事", "excerpt": "本文"}]})
    monkeypatch.setattr(scanner, "_news_intel_persist", lambda: None)
    monkeypatch.setattr(scanner, "_causal_memory_refresh_open", lambda: None)
    monkeypatch.setattr(scanner, "_news_intake_ready", lambda: True, raising=False)
    monkeypatch.setitem(scanner._NEWS_INTEL, "intakeState", {})
    scanner._news_intake_cycle()
    assert processed == ["digest#1", "digest#2", "single"]


def test_news_intake_without_a_splitter_processes_mails_as_before(monkeypatch):
    processed = []
    monkeypatch.setattr(scanner, "_news_process_message",
                        lambda message, backfill=False: processed.append(message["messageId"]))
    monkeypatch.delattr(scanner.argus_news_intelligence, "split_digest_message", raising=False)
    monkeypatch.setattr(scanner.argus_gmail_intake, "run_intake_cycle",
                        lambda **kw: {"status": "HEALTHY", "state": {}, "messages": [
                            {"messageId": "m1", "subject": "x", "excerpt": "◆A\n◆B"}]})
    monkeypatch.setattr(scanner, "_news_intel_persist", lambda: None)
    monkeypatch.setattr(scanner, "_causal_memory_refresh_open", lambda: None)
    monkeypatch.setitem(scanner._NEWS_INTEL, "intakeState", {})
    scanner._news_intake_cycle()
    assert processed == ["m1"]
# v13.5.61 Recovery payload — the owner's own JP names are warmed by collect,
# and the scheduled event-analysis lane carries its purpose (owner iPhone
# review 2026-09-07: 「データが取れていない銘柄がある」「AIシナリオが出ていない」).
# ═══════════════════════════════════════════════════════════════════════════

def test_owner_jp_symbols_for_warm_come_from_layer2b_and_device_requests(monkeypatch):
    monkeypatch.setattr(scanner, "_layer2b_read_latest", lambda: {"members": [
        {"market": "JP", "symbol": "7011"}, {"market": "JP", "symbol": "314A"},
        {"market": "US", "symbol": "MU"}, {"market": "JP", "symbol": "8058"}]})
    monkeypatch.setattr(scanner, "_JP_SEEN_SYMBOLS", {"6330": 1.0, "7794": 2.0, "ZZ": 3.0, "8058": 4.0})
    codes = scanner._owner_jp_symbols_for_warm()
    assert codes == ("314A", "6330", "7011", "7794")   # curated 8058 and non-codes excluded
    assert scanner._owner_jp_symbols_for_warm(limit=2) == ("314A", "6330")


def test_owner_jp_symbols_for_warm_never_raises(monkeypatch):
    monkeypatch.setattr(scanner, "_layer2b_read_latest", lambda: (_ for _ in ()).throw(RuntimeError("no ledger")))
    monkeypatch.setattr(scanner, "_JP_SEEN_SYMBOLS", {})
    assert scanner._owner_jp_symbols_for_warm() == ()


def test_collect_warms_the_owner_jp_names_with_a_provider_fetch(monkeypatch):
    monkeypatch.setattr(scanner, "_ARGUS_ADMIN_TOKEN", "tok")
    monkeypatch.setattr(scanner, "_owner_jp_symbols_for_warm", lambda limit=None: ("314A", "7011"))
    calls = []
    monkeypatch.setattr(scanner, "_get_japan_watchlist_core",
                        lambda symbols=None, allow_provider_fetch=True: calls.append((tuple(symbols or ()), allow_provider_fetch)) or {"stocks": []})
    with scanner.app.test_client() as client:
        response = client.post("/api/argus/institutional-intelligence/collect",
                               headers={"X-ARGUS-ADMIN-TOKEN": "tok"})
    assert response.status_code == 200
    assert (("314A", "7011"), True) in calls
    assert response.get_json()["supplyDemandWarm"]["ownerJp"] == 2


def test_macro_event_analysis_runs_in_the_event_analysis_lane(monkeypatch):
    captured = []
    monkeypatch.setattr(scanner, "_openai_prose",
                        lambda user, max_out=600, system=None, **kw: captured.append(kw) or {})
    monkeypatch.setattr(scanner, "_macro_important_events",
                        lambda limit=8: [{"eventId": "us-cpi-2026-09-11", "eventCode": "CPI",
                                          "eventTimeUtc": "2026-09-11T12:30:00Z", "eventDate": "2026-09-11",
                                          "daysUntil": 4, "displayImpact": "high"}])
    monkeypatch.setattr(scanner, "_macro_market_context_ja", lambda: "ctx")
    monkeypatch.setattr(scanner, "_MACRO_ANALYSIS", {})
    monkeypatch.setattr(scanner.argus_macro_event_analysis, "resolve_macro_event_phase",
                        lambda *a, **k: "pre")
    monkeypatch.setattr(scanner.argus_macro_event_analysis, "should_refresh_pre",
                        lambda rec, phase, now_iso=None: True)
    monkeypatch.setattr(scanner.argus_macro_event_analysis, "parse_pre",
                        lambda out, phase=None, now_iso=None: None)
    monkeypatch.setattr(scanner, "_osint_persist", lambda *a, **k: None, raising=False)
    scanner._generate_macro_event_analysis(limit=1)
    assert captured and captured[0]["purpose"] == "event_analysis"
    assert captured[0]["event_id"] == "us-cpi-2026-09-11" and captured[0]["event_phase"] == "pre"


def test_closed_market_cold_fill_runs_one_bounded_rotation_after_a_redeploy(monkeypatch):
    """v13.5.61: a holiday redeploy no longer leaves the owner's US symbols blank."""
    import datetime as _dt
    import argus_td_warm
    _td_fresh_state(monkeypatch, session="HOLIDAY_CLOSED")
    calls = []
    _td_fake_provider(monkeypatch, calls)
    t0 = _dt.datetime(2026, 9, 7, 12, 0, tzinfo=_dt.timezone.utc)   # Labor Day
    first = scanner._td_warm_tick(now_utc=t0)
    assert first["action"] == "fetch" and first.get("coldFill") is True
    assert len(calls) == 1 and len(first["batch"]) == 8
    second = scanner._td_warm_tick(now_utc=t0 + _dt.timedelta(seconds=61))
    assert second["action"] == "fetch" and second["cycleComplete"] is True     # the ninth symbol
    third = scanner._td_warm_tick(now_utc=t0 + _dt.timedelta(seconds=122))
    assert third["action"] == "skip" and third["reason"] == "market_closed"
    assert scanner._TD_WARM_STATE["usedToday"] == 9
    assert scanner._TD_WARM_STATE["coldFillAt"] == t0.timestamp()
    assert scanner._TD_WARM_STATE["warmSymbolCount"] == 9


# ═══════════════════════════════════════════════════════════════════════════
# v13.5.63 Recovery payload — GPT additional items 4/5/6: the event-AI lane
# states key / permission / budget / refusal separately, asks GPT-6 Astra and
# records the model that answered with its own cost, and digest mails stored
# whole before the split are dropped and re-fetched per article.
# ═══════════════════════════════════════════════════════════════════════════
import types as _types
import pytest as _pytest

# The pure-module halves ship in the v13.5.63 product PR; on an older tree the
# scanner glue degrades (getattr / TypeError fallbacks) and these checks skip.
_HAS_63_POLICY = hasattr(scanner.argus_cost_policy, "record_skip")
_HAS_63_MACRO = "ai_meta" in inspect.signature(scanner.argus_macro_event_analysis.parse_pre).parameters
_HAS_63_NEWS = hasattr(scanner.argus_news_intelligence, "digest_container_event_ids")


@_pytest.fixture
def _ai_state_restore():
    """These tests drive the cost policy / prose ledger / macro store in place;
    the later integration suites expect the module defaults back."""
    saved_policy = json.loads(json.dumps(scanner._COST_POLICY))
    saved_prose = dict(scanner._OPENAI_PROSE_LAST)
    saved_macro = dict(scanner._MACRO_ANALYSIS)
    saved_macro_state = dict(scanner._MACRO_ANALYSIS_STATE)
    saved_cost = {k: (v if k != "runs" else list(v)) for k, v in scanner._AI_COST_STATE.items()}
    yield
    scanner._COST_POLICY.clear(); scanner._COST_POLICY.update(saved_policy)
    scanner._OPENAI_PROSE_LAST.clear(); scanner._OPENAI_PROSE_LAST.update(saved_prose)
    scanner._MACRO_ANALYSIS.clear(); scanner._MACRO_ANALYSIS.update(saved_macro)
    scanner._MACRO_ANALYSIS_STATE.clear(); scanner._MACRO_ANALYSIS_STATE.update(saved_macro_state)
    for k, v in saved_cost.items():
        if k == "runs":
            scanner._AI_COST_STATE["runs"].clear(); scanner._AI_COST_STATE["runs"].extend(v)
        else:
            scanner._AI_COST_STATE[k] = v


class _FakeUsage:
    input_tokens = 1180
    output_tokens = 640


class _FakeResp:
    def __init__(self, model):
        self.model = model
        self.output_text = '{"summaryJa": "概要", "argusScenarioJa": "予想"}'
        self.usage = _FakeUsage()


class _NotFound(Exception):
    pass


_NotFound.__name__ = "NotFoundError"


def _fake_openai(available_models):
    calls = []

    class _Responses:
        def create(self, **kw):
            calls.append(kw["model"])
            if kw["model"] not in available_models:
                raise _NotFound("model `%s` does not exist or you do not have access" % kw["model"])
            return _FakeResp(kw["model"] + "-2026-08-01")

    class _Completions:
        def create(self, **kw):
            raise _NotFound("model not found")

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, api_key=None):
            self.responses = _Responses()
            self.chat = _Chat()

    module = _types.SimpleNamespace(OpenAI=_Client)
    return module, calls


def _scheduled_state(monkeypatch):
    state = scanner.argus_cost_policy.default_state("SCHEDULED_AI", event_opt_in=True)
    scanner._COST_POLICY.clear()
    scanner._COST_POLICY.update(state)
    monkeypatch.setattr(scanner, "_osint_persist", lambda: None, raising=False)


def test_prose_call_falls_back_only_when_the_model_is_unusable_and_bills_its_own_tokens(monkeypatch, _ai_state_restore):
    import sys as _sys
    _scheduled_state(monkeypatch)
    monkeypatch.setattr(scanner, "_OPENAI_API_KEY", "k")
    fake, calls = _fake_openai({"gpt-5.6-terra"})
    monkeypatch.setitem(_sys.modules, "openai", fake)
    diag = {}
    out = scanner._openai_prose("p", purpose="event_analysis", event_id="FOMC", event_phase="pre",
                                model="gpt-6-astra", fallback_model="gpt-5.6-terra", diagnostic=diag)
    assert out == {"summaryJa": "概要", "argusScenarioJa": "予想"}
    assert calls == ["gpt-6-astra", "gpt-5.6-terra"]
    assert diag["requestedModel"] == "gpt-6-astra"
    assert diag["fallbackModel"] == "gpt-5.6-terra"
    assert diag["returnedModel"] == "gpt-5.6-terra-2026-08-01"
    assert diag["inputTokens"] == 1180 and diag["outputTokens"] == 640
    # priced at the ANSWERING model's list price: 1180/1M×$2 + 640/1M×$12
    assert abs(diag["estUsd"] - (1180 * 2.0 + 640 * 12.0) / 1_000_000) < 1e-9
    last = scanner._AI_COST_STATE["lastRun"]
    assert last["rows"][0]["model"] == "gpt-5.6-terra-2026-08-01" and last["rows"][0]["fallbackUsed"] is True
    assert last["rows"][0]["inputTokens"] == 1180
    usage = scanner._COST_POLICY["usage"][-1]
    assert usage["purpose"] == "event_analysis" and abs(usage["estimatedCostUsd"] - diag["estUsd"]) < 1e-9
    assert scanner._OPENAI_PROSE_LAST["outcome"] == "ok"


def test_prose_call_uses_gpt6_when_the_project_can_and_records_the_served_model(monkeypatch, _ai_state_restore):
    import sys as _sys
    _scheduled_state(monkeypatch)
    monkeypatch.setattr(scanner, "_OPENAI_API_KEY", "k")
    fake, calls = _fake_openai({"gpt-6-astra"})
    monkeypatch.setitem(_sys.modules, "openai", fake)
    diag = {}
    assert scanner._openai_prose("p", purpose="event_analysis", event_id="CPI", event_phase="pre",
                                 model="gpt-6-astra", fallback_model="gpt-5.6-terra", diagnostic=diag)
    assert calls == ["gpt-6-astra"] and diag["fallbackModel"] is None
    assert diag["returnedModel"] == "gpt-6-astra-2026-08-01"
    assert abs(diag["estUsd"] - (1180 * 10.0 + 640 * 50.0) / 1_000_000) < 1e-9


def test_prose_call_records_why_it_did_not_run(monkeypatch, _ai_state_restore):
    scanner._COST_POLICY.clear()
    scanner._COST_POLICY.update(scanner.argus_cost_policy.default_state("DETERMINISTIC"))
    monkeypatch.setattr(scanner, "_osint_persist", lambda: None, raising=False)
    assert scanner._openai_prose("p", purpose="event_analysis", event_id="X", event_phase="pre") is None
    assert scanner._OPENAI_PROSE_LAST["outcome"] == "skipped"
    assert scanner._OPENAI_PROSE_LAST["reason"] == "deterministic_mode"
    if _HAS_63_POLICY:
        assert scanner._COST_POLICY["lastSkip"]["reason"] == "deterministic_mode"
        assert scanner._COST_POLICY["lastSkip"]["purpose"] == "event_analysis"
    _scheduled_state(monkeypatch)
    monkeypatch.setattr(scanner, "_OPENAI_API_KEY", "")
    assert scanner._openai_prose("p", purpose="event_analysis", event_id="X", event_phase="pre") is None
    assert scanner._OPENAI_PROSE_LAST["outcome"] == "no_key"


@_pytest.mark.skipif(not _HAS_63_POLICY, reason="needs the v13.5.63 cost-policy module")
def test_public_cost_policy_states_key_budget_permission_and_refusal_without_secrets(monkeypatch, _ai_state_restore):
    _scheduled_state(monkeypatch)
    monkeypatch.setattr(scanner, "_OPENAI_API_KEY", "sk-should-never-appear")
    scanner._COST_POLICY.clear()
    scanner._COST_POLICY.update(scanner.argus_cost_policy.default_state("DETERMINISTIC"))
    scanner._openai_prose("p", purpose="event_analysis", event_id="X", event_phase="pre")
    client = scanner.app.test_client()
    body = client.get("/api/argus/cost-policy").get_json()
    assert body["openaiKeyConfigured"] is True
    assert body["scheduledLane"]["dailyBudgetUsd"] == scanner._SCHEDULED_AI_DAILY_USD
    assert body["lastSkip"]["reason"] == "deterministic_mode"
    assert body["eventModel"] == "gpt-6-astra"
    assert body["lastProseCall"]["outcome"] == "skipped"
    raw = json.dumps(body)
    assert "sk-should-never-appear" not in raw and "apiKey" not in raw


def test_macro_generation_records_the_outcome_per_event(monkeypatch, _ai_state_restore):
    scanner._COST_POLICY.clear()
    scanner._COST_POLICY.update(scanner.argus_cost_policy.default_state("DETERMINISTIC"))
    monkeypatch.setattr(scanner, "_osint_persist", lambda: None, raising=False)
    monkeypatch.setattr(scanner, "_macro_analysis_restore_once", lambda: None)
    monkeypatch.setattr(scanner, "_macro_analysis_persist", lambda: None)
    monkeypatch.setattr(scanner, "_macro_market_context_ja", lambda: {})
    monkeypatch.setattr(scanner, "_macro_important_events",
                        lambda limit=8: [{"eventId": "ev-fomc", "eventCode": "FOMC",
                                          "eventTimeUtc": "2099-09-17T18:00:00Z", "eventDate": "2099-09-17",
                                          "daysUntil": 10, "displayImpact": "critical", "linkedAssets": ["SPY"]}])
    monkeypatch.setitem(scanner._MACRO_ANALYSIS, "ev-fomc", None)
    scanner._MACRO_ANALYSIS.pop("ev-fomc", None)
    result = scanner._generate_macro_event_analysis()
    assert result["pre"] == 0 and result["eventModel"] == "gpt-6-astra"
    outcome = result["events"]["ev-fomc"]
    assert outcome["outcome"] == "skipped" and outcome["reason"] == "deterministic_mode"
    assert outcome["requestedModel"] == "gpt-6-astra"
    client = scanner.app.test_client()
    body = client.get("/api/argus/macro-event-analysis?limit=5").get_json()
    assert body["lastGenerate"]["events"]["ev-fomc"]["reason"] == "deterministic_mode"
    assert body["eventModel"] == "gpt-6-astra"


@_pytest.mark.skipif(not _HAS_63_MACRO, reason="needs the v13.5.63 macro module")
def test_macro_generation_saves_the_served_model_on_the_pre_record(monkeypatch, _ai_state_restore):
    import sys as _sys
    _scheduled_state(monkeypatch)
    monkeypatch.setattr(scanner, "_OPENAI_API_KEY", "k")
    fake, calls = _fake_openai({"gpt-6-astra"})
    monkeypatch.setitem(_sys.modules, "openai", fake)
    monkeypatch.setattr(scanner, "_macro_analysis_restore_once", lambda: None)
    monkeypatch.setattr(scanner, "_macro_analysis_persist", lambda: None)
    monkeypatch.setattr(scanner, "_macro_market_context_ja", lambda: {})
    monkeypatch.setattr(scanner, "_macro_important_events",
                        lambda limit=8: [{"eventId": "ev-cpi", "eventCode": "CPI",
                                          "eventTimeUtc": "2099-09-11T12:30:00Z", "eventDate": "2099-09-11",
                                          "daysUntil": 4, "displayImpact": "critical", "linkedAssets": ["SPY"]}])
    scanner._MACRO_ANALYSIS.pop("ev-cpi", None)
    result = scanner._generate_macro_event_analysis()
    assert result["pre"] == 1 and calls == ["gpt-6-astra"]
    pre = scanner._MACRO_ANALYSIS["ev-cpi"]["pre"]
    assert pre["summaryJa"] == "概要"
    assert pre["ai"]["requestedModel"] == "gpt-6-astra"
    assert pre["ai"]["returnedModel"] == "gpt-6-astra-2026-08-01"
    assert pre["ai"]["estUsd"] > 0 and pre["ai"]["completedAt"]
    assert result["events"]["ev-cpi"]["outcome"] == "generated"


@_pytest.mark.skipif(not _HAS_63_NEWS, reason="needs the v13.5.63 news module")
def test_stored_digest_containers_are_dropped_and_their_mails_refetched_per_article(monkeypatch):
    processed = []
    captured = {}
    container = {
        "eventId": "FX|2026-09-07|c", "severity": "HIGH",
        "headlineJa": "日経ニュースメール 9/7 夕版 ━ 注目ニュース ━━━ ◆円半年ぶりに154円台に上昇 ◆ホルムズ",
        "titleOriginal": "円半年ぶりに154円台に上昇", "processedAt": "2026-09-07T09:52:00Z",
        "whyJa": "ホルムズ海峡は…"}
    monkeypatch.setitem(scanner._NEWS_INTEL, "events", {"FX|2026-09-07|c": container,
                                                        "OTHER|2026-09-06|d": {"headlineJa": "普通の記事", "titleOriginal": "普通の記事",
                                                                               "processedAt": "2026-09-06T09:00:00Z"}})
    monkeypatch.setitem(scanner._NEWS_INTEL, "order", ["OTHER|2026-09-06|d", "FX|2026-09-07|c"])
    monkeypatch.setitem(scanner._NEWS_INTEL, "messageStatus", {
        "g-digest": {"status": "ALERTED", "source": "NIKKEI", "at": "2026-09-07T09:52:05Z"},
        "g-old": {"status": "SURFACED", "source": "NIKKEI", "at": "2026-09-06T09:00:03Z"}})
    monkeypatch.setitem(scanner._NEWS_INTEL, "intakeState",
                        {"seenMessageIds": ["g-old", "g-digest"], "historyId": "h1"})
    monkeypatch.setitem(scanner._NEWS_INTEL, "audit", [])
    health = dict(scanner._NEWS_INTEL["health"]); health.pop("digestRepairAt", None); health.pop("digestRepair", None)
    monkeypatch.setitem(scanner._NEWS_INTEL, "health", health)

    def fake_cycle(**kw):
        captured.update(kw)
        return {"status": "HEALTHY", "state": dict(kw["state"]), "messages": [
            {"messageId": "g-digest", "subject": "日経ニュースメール 9/7 夕版",
             "excerpt": "━ 注目ニュース ━\n◆円半年ぶりに154円台に上昇\n本文A\n◆ホルムズ海峡で緊張\n本文B"}]}
    monkeypatch.setattr(scanner.argus_gmail_intake, "run_intake_cycle", fake_cycle)
    monkeypatch.setattr(scanner, "_news_process_message",
                        lambda message, backfill=False: processed.append((message["subject"], message.get("digestOf"))))
    monkeypatch.setattr(scanner, "_news_intel_persist", lambda: None)
    monkeypatch.setattr(scanner, "_causal_memory_refresh_open", lambda: None)
    scanner._news_intake_cycle()
    # the container is gone, the unrelated event stays
    assert "FX|2026-09-07|c" not in scanner._NEWS_INTEL["events"]
    assert "OTHER|2026-09-06|d" in scanner._NEWS_INTEL["events"]
    assert scanner._NEWS_INTEL["order"] == ["OTHER|2026-09-06|d"]
    # only the digest mail id left the seen-set; the window was re-listed
    assert captured["state"]["seenMessageIds"] == ["g-old"]
    assert "historyId" not in captured["state"]
    assert captured["reconcile_window"] == "newer_than:10d"
    # …and the digest was processed one article at a time, each naming its mail
    assert processed == [("円半年ぶりに154円台に上昇", "g-digest"), ("ホルムズ海峡で緊張", "g-digest")]
    assert scanner._NEWS_INTEL["health"]["digestRepair"] == {
        "removedEvents": 1, "refetchCandidates": 1, "eventIds": ["FX|2026-09-07|c"]}
    assert scanner._NEWS_INTEL["health"]["digestRepairAt"]
    # second cycle: nothing to repair, the ordinary incremental path runs
    captured.clear()
    scanner._news_intake_cycle()
    assert "reconcile_window" not in captured


def test_split_articles_carry_their_digest_mail_into_the_envelope():
    src = inspect.getsource(scanner._news_process_message)
    assert '"digestOf": message.get("digestOf")' in src


@_pytest.mark.skipif(not _HAS_63_NEWS, reason="needs the v13.5.63 news module")
def test_the_brief_reads_the_projected_news_not_the_stored_digest_container(monkeypatch):
    container = {
        "eventId": "FX|2026-09-07|c", "severity": "HIGH", "sourceFamily": "NIKKEI",
        "headlineJa": "日経ニュースメール 9/7 夕版 ━ 注目ニュース ━━━ ◆円半年ぶりに154円台に上昇 ◆ホルムズ",
        "titleOriginal": "円半年ぶりに154円台に上昇", "processedAt": "2026-09-07T09:52:00Z",
        "sourceReceivedAt": scanner._ai_now_iso(),
        "whyJa": "ホルムズ海峡は…"}
    monkeypatch.setitem(scanner._NEWS_INTEL, "events", {"FX|2026-09-07|c": container})
    monkeypatch.setitem(scanner._NEWS_INTEL, "order", ["FX|2026-09-07|c"])
    rows = scanner._brief_news_events()
    assert rows and rows[0]["headlineJa"].startswith("一括メール: 円半年ぶりに154円台に上昇")
    assert "ホルムズ" not in rows[0]["whyJa"] and rows[0]["severity"] == "INFO"


# ═══════════════════════════════════════════════════════════════════════════
# v13.5.66 Recovery payload — stabilization items 3 and 4: the public news
# routes answer while a cycle is inside an external AI call; a re-fetched mail
# is the same event (no duplicate article, no duplicate analysis); the cost
# ledger reserves under one lock, writes through to the durable root and
# survives a restart; a generation run is tracked running → done / failed.
# ═══════════════════════════════════════════════════════════════════════════
import threading as _threading
import time as _time


def _news_state_for_tests(monkeypatch):
    monkeypatch.setitem(scanner._NEWS_INTEL, "events", {})
    monkeypatch.setitem(scanner._NEWS_INTEL, "order", [])
    monkeypatch.setitem(scanner._NEWS_INTEL, "audit", [])
    monkeypatch.setitem(scanner._NEWS_INTEL, "aiCache", {})
    monkeypatch.setitem(scanner._NEWS_INTEL, "messageStatus", {})
    monkeypatch.setitem(scanner._NEWS_INTEL, "messageOrder", [])
    monkeypatch.setitem(scanner._NEWS_INTEL, "sources", {})
    monkeypatch.setitem(scanner._NEWS_INTEL, "observedSenders", {})
    monkeypatch.setitem(scanner._NEWS_INTEL, "intakeState", {})
    health = dict(scanner._NEWS_INTEL["health"])
    health.update({"status": "HEALTHY", "emailsSeen": 0, "duplicatesSuppressed": 0,
                   "aiAnalyses": 0, "aiCacheHits": 0, "quarantined": 0, "alertsEligible": 0,
                   "parseFailures": 0})
    monkeypatch.setitem(scanner._NEWS_INTEL, "health", health)
    monkeypatch.setattr(scanner, "_news_intel_persist", lambda: None)
    monkeypatch.setattr(scanner, "_causal_memory_refresh_open", lambda: None)
    monkeypatch.setattr(scanner, "_causal_memory_process_normalized_event", lambda event: None)
    monkeypatch.setattr(scanner, "_news_allowed_sender_domains", lambda: ["nikkei.com"])
    monkeypatch.setattr(scanner.argus_gmail_intake, "authenticate_sender",
                        lambda headers, domains: {"authenticated": True, "fromDomain": "nikkei.com",
                                                  "spf": True, "dkim": True, "quarantineReasons": []})
    monkeypatch.setattr(scanner.argus_news_intelligence, "resolve_source",
                        lambda **kw: "NIKKEI")
    monkeypatch.setattr(scanner, "_news_corroboration",
                        lambda family, polarity=None: {"confirmed": False, "readings": [], "missing": []},
                        raising=False)
    monkeypatch.setattr(scanner, "_NEWS_LOADED", {"value": True}, raising=False)


def _mail(message_id, subject, received_epoch):
    return {"messageId": message_id, "rfcMessageId": f"<{message_id}@nikkei>",
            "subject": subject, "excerpt": "7日の外国為替市場で円相場が上昇した。",
            "headers": [], "fromDisplay": "日経", "linkDomains": [],
            "receivedEpoch": received_epoch}


def test_public_news_routes_answer_while_a_cycle_waits_on_external_ai(monkeypatch):
    _news_state_for_tests(monkeypatch)
    gate = _threading.Event()

    def slow_ai(subject, excerpt, fingerprint, taxonomy=None):
        gate.wait(5.0)            # "the model is thinking"
        return None, "AI_ANALYSIS_UNAVAILABLE"
    monkeypatch.setattr(scanner, "_news_analyze_ai", slow_ai)
    monkeypatch.setattr(scanner.argus_gmail_intake, "run_intake_cycle",
                        lambda **kw: {"status": "HEALTHY", "state": {}, "messages": [
                            _mail("g1", "円相場が上昇", 1_800_000_000)]})
    monkeypatch.setattr(scanner, "_news_repair_digest_containers",
                        lambda: {"refetch": False, "state": None})
    worker = _threading.Thread(target=scanner._news_intake_cycle, daemon=True)
    worker.start()
    _time.sleep(0.2)              # the cycle is now inside the AI call
    client = scanner.app.test_client()
    started = _time.monotonic()
    health = client.get("/api/argus/news-intake/health")
    listing = client.get("/api/argus/news-intelligence")
    elapsed = _time.monotonic() - started
    assert health.status_code == 200 and listing.status_code == 200
    assert elapsed < 2.0, f"news routes blocked for {elapsed:.1f}s during the AI call"
    gate.set()
    worker.join(5.0)
    assert not worker.is_alive()
    assert scanner._NEWS_INTEL["events"], "the event was stored after the AI call"


def test_a_refetched_mail_is_the_same_event_and_never_analysed_twice(monkeypatch):
    _news_state_for_tests(monkeypatch)
    calls = []

    def counting_ai(subject, excerpt, fingerprint, taxonomy=None):
        calls.append(fingerprint)
        return {"causalPathJa": "円高", "facts": [], "entities": []}, "ANALYZED"
    monkeypatch.setattr(scanner, "_news_analyze_ai", counting_ai)
    monkeypatch.setattr(scanner.argus_news_intelligence, "validate_ai_analysis",
                        lambda raw: raw, raising=False)
    mail = _mail("g7", "円相場が一時154円台に上昇", 1_800_000_000)
    first = scanner._news_process_message(dict(mail))
    assert first is not None
    # processed again "the next day" (a backfill / repair re-fetch)
    monkeypatch.setattr(scanner, "_ai_now_iso", lambda: "2026-09-09T01:00:00Z")
    again = scanner._news_process_message(dict(mail))
    assert again is None
    assert len(scanner._NEWS_INTEL["events"]) == 1
    assert scanner._NEWS_INTEL["health"]["duplicatesSuppressed"] == 1
    assert len(calls) == 1, "the duplicate was suppressed before any AI call"


def test_reservations_count_against_the_budget_before_the_call_returns(monkeypatch):
    saved = json.loads(json.dumps(scanner._COST_POLICY))
    scanner._COST_POLICY.clear()
    scanner._COST_POLICY.update(scanner.argus_cost_policy.default_state("SCHEDULED_AI", event_opt_in=True))
    monkeypatch.setattr(scanner, "_osint_persist", lambda: None, raising=False)
    monkeypatch.setattr(scanner, "_cost_policy_persist_durable", lambda: None)
    monkeypatch.setattr(scanner, "_SCHEDULED_AI_DAILY_USD", 0.10)
    try:
        first, r1 = scanner._cost_policy_reserve("openai", "event_analysis", event_id="A",
                                                 event_phase="pre", estimated_cost_usd=0.08,
                                                 estimated_tokens=1000)
        assert first["allowed"] and r1
        # the second caller sees the reservation and is refused — the same
        # dollar is never handed out twice
        second, r2 = scanner._cost_policy_reserve("openai", "event_analysis", event_id="B",
                                                  event_phase="pre", estimated_cost_usd=0.08,
                                                  estimated_tokens=1000)
        assert not second["allowed"] and r2 is None
        assert second["reason"] == "scheduled_daily_budget_exhausted"
        # a failed call releases its reservation …
        scanner._cost_policy_settle(r1, ok=False)
        assert not [r for r in scanner._COST_POLICY["usage"] if r.get("pending")]
        third, r3 = scanner._cost_policy_reserve("openai", "event_analysis", event_id="B",
                                                 event_phase="pre", estimated_cost_usd=0.08,
                                                 estimated_tokens=1000)
        assert third["allowed"]
        # … and a successful one becomes the executed record with its real cost
        scanner._cost_policy_settle(r3, ok=True, actual_cost_usd=0.041)
        rows = [r for r in scanner._COST_POLICY["usage"] if r.get("reservationId") == r3]
        assert rows and rows[0]["pending"] is False and abs(rows[0]["estimatedCostUsd"] - 0.041) < 1e-9
        assert scanner._COST_POLICY["lastExecution"]["reservationId"] == r3
        assert scanner._COST_POLICY["events"]["B"]["phaseRuns"]["pre"] == 1
        # concurrency: many threads racing for a budget of one call
        scanner._COST_POLICY.clear()
        scanner._COST_POLICY.update(scanner.argus_cost_policy.default_state("SCHEDULED_AI", event_opt_in=True))
        granted = []

        def race(i):
            decision, rid = scanner._cost_policy_reserve(
                "openai", "event_analysis", event_id=f"E{i}", event_phase="pre",
                estimated_cost_usd=0.08, estimated_tokens=1000)
            if decision["allowed"]:
                granted.append(rid)
        threads = [_threading.Thread(target=race, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5.0)
        assert len(granted) == 1, granted
    finally:
        scanner._COST_POLICY.clear()
        scanner._COST_POLICY.update(saved)


def test_cost_ledger_writes_through_and_restores_after_a_restart(monkeypatch, tmp_path):
    saved = json.loads(json.dumps(scanner._COST_POLICY))
    monkeypatch.setattr(scanner, "_cost_policy_durable_path",
                        lambda: str(tmp_path / "cost_policy_state.json"))
    monkeypatch.setattr(scanner, "_osint_persist", lambda: None, raising=False)
    monkeypatch.setitem(scanner._COST_POLICY_DURABLE, "enabled", True)
    try:
        scanner._COST_POLICY.clear()
        scanner._COST_POLICY.update(scanner.argus_cost_policy.default_state("SCHEDULED_AI", event_opt_in=True))
        scanner._cost_policy_record("gemini", "headline_translation", estimated_cost_usd=0.02)
        scanner._cost_policy_record("openai", "event_analysis", event_id="X", event_phase="pre",
                                    estimated_cost_usd=0.04)
        assert (tmp_path / "cost_policy_state.json").exists()
        # "redeploy": the process comes back with a stale journal snapshot
        # holding only the first row
        stale = scanner.argus_cost_policy.default_state("SCHEDULED_AI", event_opt_in=True)
        stale["usage"] = [dict(scanner._COST_POLICY["usage"][0])]
        scanner._COST_POLICY.clear()
        scanner._COST_POLICY.update(stale)
        added = scanner._cost_policy_restore_durable()
        assert added == 1
        assert len(scanner._COST_POLICY["usage"]) == 2
        assert scanner._COST_POLICY["lastExecution"]["purpose"] == "event_analysis"
        assert scanner._COST_POLICY["events"]["X"]["phaseRuns"]["pre"] == 1
        # the public view names the durability facts and never a secret
        client = scanner.app.test_client()
        body = client.get("/api/argus/cost-policy").get_json()
        assert body["ledgerDurability"]["writeThrough"] is True
        assert body["ledgerDurability"]["restoredRows"] == 1
        assert "apiKey" not in json.dumps(body)
    finally:
        scanner._COST_POLICY.clear()
        scanner._COST_POLICY.update(saved)


def test_generation_run_is_single_flight_and_tracked_through_failure(monkeypatch):
    monkeypatch.setattr(scanner, "_macro_analysis_persist", lambda: None)
    monkeypatch.setattr(scanner, "_macro_analysis_restore_once", lambda: None)
    monkeypatch.setattr(scanner, "_macro_market_context_ja", lambda: {})
    saved_state = dict(scanner._MACRO_ANALYSIS_STATE)
    try:
        gate = _threading.Event()

        def slow_events(limit=8):
            gate.wait(5.0)
            return []
        monkeypatch.setattr(scanner, "_macro_important_events", slow_events)
        results = {}
        worker = _threading.Thread(
            target=lambda: results.setdefault("first", scanner._generate_macro_event_analysis()),
            daemon=True)
        worker.start()
        _time.sleep(0.2)
        assert scanner._MACRO_ANALYSIS_STATE["generateRun"]["status"] == "running"
        second = scanner._generate_macro_event_analysis()
        assert second["status"] == "already_running"
        assert second["generateRun"]["status"] == "running"
        gate.set()
        worker.join(5.0)
        assert results["first"]["generateRun"]["status"] == "done"
        assert results["first"]["generateRun"]["finishedAt"]

        def boom(limit=8):
            raise RuntimeError("provider down")
        monkeypatch.setattr(scanner, "_macro_important_events", boom)
        try:
            scanner._generate_macro_event_analysis()
        except RuntimeError:
            pass
        run = scanner._MACRO_ANALYSIS_STATE["generateRun"]
        assert run["status"] == "failed" and run["errorClass"] == "RuntimeError"
        client = scanner.app.test_client()
        body = client.get("/api/argus/macro-event-analysis?limit=1").get_json()
        assert body["generateRun"]["status"] == "failed"
    finally:
        scanner._MACRO_ANALYSIS_STATE.clear()
        scanner._MACRO_ANALYSIS_STATE.update(saved_state)


def test_prose_diagnosis_is_local_when_another_lane_is_refused(monkeypatch, _ai_state_restore):
    """A real interleaving: event request waits while the news lane is refused."""
    import sys as _sys
    _scheduled_state(monkeypatch)
    monkeypatch.setattr(scanner, "_OPENAI_API_KEY", "test")
    monkeypatch.setattr(scanner, "_cost_policy_persist_durable", lambda: None)
    monkeypatch.setattr(scanner, "_SCHEDULED_AI_DAILY_USD", 0.5)
    fake, _ = _fake_openai({"gpt-6-astra"})
    monkeypatch.setitem(_sys.modules, "openai", fake)
    entered, release = _threading.Event(), _threading.Event()

    def waiting_call(client, model, system, user):
        entered.set()
        assert release.wait(5)
        response = _FakeResp(model)
        return response, response.output_text

    monkeypatch.setattr(scanner, "_openai_prose_call", waiting_call)
    event_diag, news_diag, results = {}, {}, {}
    worker = _threading.Thread(target=lambda: results.setdefault("out", scanner._openai_prose(
        "event", purpose="event_analysis", event_id="X", event_phase="pre",
        model="gpt-6-astra", diagnostic=event_diag)), daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        assert scanner._openai_prose("news", purpose="market_brief", diagnostic=news_diag) is None
        assert news_diag["reason"] == "scheduled_daily_budget_exhausted"
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and results["out"]
    assert event_diag["outcome"] == "ok"
    assert event_diag["reason"] is None and event_diag["errorClass"] is None
    assert scanner._OPENAI_PROSE_LAST["purpose"] == "event_analysis"
    assert scanner._OPENAI_PROSE_LAST["reason"] is None
    # Saved per-call evidence stays unchanged when a later call replaces the status.
    scanner._openai_prose("later news", purpose="market_brief")
    assert event_diag["outcome"] == "ok" and event_diag["reason"] is None


def test_macro_outcome_uses_call_diagnosis_not_a_later_global_status(monkeypatch, _ai_state_restore):
    monkeypatch.setattr(scanner, "_macro_analysis_restore_once", lambda: None)
    monkeypatch.setattr(scanner, "_macro_analysis_persist", lambda: None)
    monkeypatch.setattr(scanner, "_macro_market_context_ja", lambda: {})
    monkeypatch.setattr(scanner, "_macro_important_events", lambda limit: [{
        "eventId": "local-diagnosis", "eventTimeUtc": "2099-09-11T12:30:00Z",
        "eventDate": "2099-09-11", "daysUntil": 4}])
    scanner._MACRO_ANALYSIS.pop("local-diagnosis", None)

    def prose(*args, diagnostic, **kwargs):
        diagnostic.update(outcome="ok", reason=None, errorClass=None,
                          requestedModel="gpt-6-astra", returnedModel="gpt-6-astra")
        scanner._OPENAI_PROSE_LAST.update(outcome="skipped", purpose="market_brief",
                                         reason="scheduled_daily_budget_exhausted")
        return {"summaryJa": "概要", "argusScenarioJa": "条件付き見通し"}

    monkeypatch.setattr(scanner, "_openai_prose", prose)
    outcome = scanner._generate_macro_event_analysis()["events"]["local-diagnosis"]
    assert outcome["outcome"] == "generated"
    assert outcome["reason"] is None and outcome["errorClass"] is None
    assert outcome["requestedModel"] == outcome["returnedModel"] == "gpt-6-astra"
