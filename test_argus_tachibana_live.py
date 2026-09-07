"""v13.5.38 — Tachibana LIVE product boundary (argus_tachibana_live) tests.

No network: the runtime, lease and clock are injected fakes.  A socket
tripwire proves the disabled path never touches the provider.
"""
from __future__ import annotations

import json
import socket
import threading
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

import argus_tachibana_live as live
from argus_providers.tachibana.config import TachibanaConfig
from argus_providers.tachibana.models import (
    ErrorClass, Freshness, MarketStatus, QuoteLevel, TachibanaError,
    TachibanaObservation,
)

TOKYO = ZoneInfo("Asia/Tokyo")
TRADING_NOW = datetime(2026, 9, 3, 12, 40, tzinfo=TOKYO).astimezone(timezone.utc)


def _observation(symbol="8058", *, now, fresh=True, price=3500.0):
    fields = {
        "current_price": price, "previous_close": 3450.0, "change_absolute": 50.0,
        "change_percent": 1.45, "open": 3460.0, "high": 3520.0, "low": 3440.0,
        "volume": 1_200_000.0, "turnover": 4.2e9, "vwap": 3490.5,
        "best_bid": 3499.0, "best_ask": 3501.0, "best_bid_volume": 400.0,
        "best_ask_volume": 300.0,
    }
    return TachibanaObservation(
        provider="TACHIBANA", endpoint_category="EVENT", symbol=symbol,
        source_timestamp=now - timedelta(seconds=2), source_timestamp_precision="SECOND",
        received_timestamp=now, fresh_until=now + timedelta(seconds=15),
        freshness=Freshness.FRESH, market_status=MarketStatus.OPEN,
        realtime_classification="REALTIME",
        fields=MappingProxyType(fields),
        field_availability=MappingProxyType({key: True for key in fields}),
        market_data_timestamp=now - timedelta(seconds=2),
        market_data_date_verified=True,
        bids=(QuoteLevel(3499.0, 400.0), QuoteLevel(3498.0, 200.0)),
        asks=(QuoteLevel(3501.0, 300.0),),
    )


class _FakeSensor:
    def __init__(self, rows):
        self.rows = rows

    def latest(self, symbol, *, now=None):
        return self.rows.get(symbol)


class _FakeSnapshot:
    provider_health = "AVAILABLE"
    session_phase = "AFTERNOON_OPEN"


class _FakeRuntime:
    instances = []

    def __init__(self, config, *, symbols, fail=None):
        self.config, self.symbols = config, symbols
        self.sensor = _FakeSensor({})
        self.started = self.stopped = 0
        self.terminal_error = ErrorClass.NONE
        self.fail = fail
        _FakeRuntime.instances.append(self)

    def start(self):
        self.started += 1
        if self.fail is not None:
            raise TachibanaError(self.fail)

    def stop(self):
        self.stopped += 1
        return True

    def acceptance_snapshot(self, *, cross_validate=False):
        return _FakeSnapshot()


class _FakeLease:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _network_tripwire(monkeypatch):
    def trip(*a, **k):
        raise AssertionError("NETWORK_ATTEMPT")
    monkeypatch.setattr(socket.socket, "connect", trip)
    monkeypatch.setattr(socket, "create_connection", trip)
    _FakeRuntime.instances.clear()


def test_disabled_by_default_never_starts_or_touches_provider():
    factory_calls = []
    service = live.TachibanaLiveService(
        config_loader=lambda env=None: TachibanaConfig.from_env(env or {}),
        runtime_factory=lambda *a, **k: factory_calls.append(1),
        lease_factory=_FakeLease, clock=lambda: TRADING_NOW)
    assert service.ensure_started({}) == "DISABLED"
    evidence = service.current_evidence_safe()
    assert evidence["status"] == "DISABLED"
    assert evidence["enabled"] is False and evidence["authoritative"] is False
    assert evidence["executionCapability"] is False
    assert evidence["authAttempts"] == 0 and factory_calls == []
    assert evidence["symbols"] == {}


def test_live_evidence_is_provenance_stamped_bounded_and_secret_free():
    config = TachibanaConfig.from_env({"ARGUS_TACHIBANA_ENABLED": "true"})
    service = live.TachibanaLiveService(
        config_loader=lambda env=None: config, runtime_factory=_FakeRuntime,
        lease_factory=_FakeLease, clock=lambda: TRADING_NOW, symbols=("8058", "9984"))
    runtime = _FakeRuntime(config, symbols=("8058", "9984"))
    runtime.sensor = _FakeSensor({"8058": _observation("8058", now=TRADING_NOW),
                                  "9984": _observation("9984", now=TRADING_NOW, price=9000.0)})
    service._config = config
    service._running = True
    service._refresh(runtime, ("8058", "9984"))
    evidence = service.current_evidence_safe(TRADING_NOW)
    assert evidence["status"] == "LIVE"
    assert evidence["provider"] == "TACHIBANA" and evidence["authority"] == "SHADOW_NON_AUTHORITATIVE"
    row = evidence["symbols"]["8058"]
    assert row["provider"] == "TACHIBANA" and row["price"] == 3500.0
    assert row["previousClose"] == 3450.0 and row["changePct"] == 1.45
    assert row["vwap"] == 3490.5 and row["bestBid"] == 3499.0 and row["bestAsk"] == 3501.0
    assert row["bidQty"] == 400.0 and row["askQty"] == 300.0
    assert row["depth"] == {"levels": 5, "bidLevels": 2, "askLevels": 1,
                            "bidQtyTop": 600.0, "askQtyTop": 300.0}
    assert row["freshness"] == "FRESH" and row["marketStatus"] == "OPEN"
    assert row["fieldAvailability"]["price"] is True
    serialized = json.dumps(evidence).lower()
    # Secret VALUES never appear; configured secret PATHS are operational facts.
    for forbidden in ("url", "sauthid", "begin private", "private-material", "token"):
        assert forbidden not in serialized
    assert evidence["symbolCount"] == 2


def test_missing_fields_are_reported_not_fabricated():
    config = TachibanaConfig.from_env({"ARGUS_TACHIBANA_ENABLED": "true"})
    obs = _observation("8058", now=TRADING_NOW)
    partial = TachibanaObservation(
        **{**obs.__dict__, "fields": MappingProxyType({"current_price": 100.0, "vwap": None}),
           "field_availability": MappingProxyType({"current_price": True, "vwap": False})})
    row = live.observation_evidence(partial, now=TRADING_NOW)
    assert row["price"] == 100.0 and row["vwap"] is None and row["volume"] is None
    assert row["fieldAvailability"]["vwap"] is False and row["fieldAvailability"]["volume"] is False


def test_freshness_expires_into_stale_then_degrades_status():
    config = TachibanaConfig.from_env({"ARGUS_TACHIBANA_ENABLED": "true"})
    service = live.TachibanaLiveService(
        config_loader=lambda env=None: config, runtime_factory=_FakeRuntime,
        lease_factory=_FakeLease, clock=lambda: TRADING_NOW, symbols=("8058",))
    runtime = _FakeRuntime(config, symbols=("8058",))
    runtime.sensor = _FakeSensor({"8058": _observation("8058", now=TRADING_NOW)})
    service._config, service._running = config, True
    service._refresh(runtime, ("8058",))
    assert service.current_evidence_safe(TRADING_NOW)["status"] == "LIVE"
    later = service.current_evidence_safe(TRADING_NOW + timedelta(seconds=60))
    assert later["symbols"]["8058"]["freshness"] == "STALE"
    assert later["status"] == "STALE"


def test_auth_failure_is_truthful_and_does_not_storm():
    config = TachibanaConfig.from_env({"ARGUS_TACHIBANA_ENABLED": "true"})
    sleeps = []
    service = live.TachibanaLiveService(
        config_loader=lambda env=None: config,
        runtime_factory=lambda cfg, *, symbols: _FakeRuntime(cfg, symbols=symbols, fail=ErrorClass.AUTH_REJECTED),
        lease_factory=_FakeLease, clock=lambda: TRADING_NOW,
        sleeper=lambda seconds: sleeps.append(seconds), symbols=("8058",))
    service._config = config
    assert service._run_session(config, ("8058",)) is True
    evidence = service.current_evidence_safe(TRADING_NOW)
    assert evidence["status"] == "AUTH_FAILED"
    assert evidence["lastErrorClass"] == "AUTH_REJECTED"
    assert evidence["authAttempts"] == 1
    assert sleeps == [300.0]                      # bounded hold, no tight loop
    assert _FakeRuntime.instances[-1].stopped == 1


def test_maintenance_and_outside_window_states():
    config = TachibanaConfig.from_env({"ARGUS_TACHIBANA_ENABLED": "true"})
    assert live.derive_status(enabled=True, running=False, last_error_class="MAINTENANCE",
                              rows={}, provider_health=None, in_window=True) == "MAINTENANCE"
    assert live.derive_status(enabled=True, running=False, last_error_class=None,
                              rows={}, provider_health=None, in_window=False) == "UNAVAILABLE"
    night = datetime(2026, 9, 3, 22, 0, tzinfo=TOKYO)
    assert live.in_live_window(night) is False
    assert live.in_live_window(datetime(2026, 9, 3, 9, 5, tzinfo=TOKYO)) is True
    assert live.in_live_window(datetime(2026, 9, 5, 9, 5, tzinfo=TOKYO)) is False  # Saturday


def test_degraded_when_only_some_symbols_are_fresh():
    rows = {"8058": {"freshness": "FRESH", "price": 1.0},
            "9984": {"freshness": "DELAYED", "price": 2.0}}
    assert live.derive_status(enabled=True, running=True, last_error_class=None,
                              rows=rows, provider_health="AVAILABLE", in_window=True) == "DEGRADED"
    assert live.derive_status(enabled=True, running=True, last_error_class=None,
                              rows={"8058": {"freshness": "FRESH", "price": None}},
                              provider_health="AVAILABLE", in_window=True) == "STALE"


def test_ensure_started_is_idempotent_and_runs_one_thread(monkeypatch):
    config = TachibanaConfig.from_env({"ARGUS_TACHIBANA_ENABLED": "true"})
    started = threading.Event()

    class _Runtime(_FakeRuntime):
        def start(self):
            super().start()
            started.set()

    service = live.TachibanaLiveService(
        config_loader=lambda env=None: config, runtime_factory=_Runtime,
        lease_factory=_FakeLease, clock=lambda: TRADING_NOW,
        sleeper=lambda seconds: None, symbols=("8058",))
    env = {"ARGUS_TACHIBANA_SINGLETON_PATH": "/tmp/argus-tachibana-live-test.lock"}
    assert service.ensure_started(env) == "STARTED"
    assert service.ensure_started(env) == "RUNNING"
    assert started.wait(5)
    service.stop()
    assert len({t.name for t in threading.enumerate() if t.name == "argus-tachibana-live"}) <= 1
    assert _FakeRuntime.instances and _FakeRuntime.instances[0].started >= 1


def test_secret_file_diagnostics_are_facts_only_never_contents(tmp_path):
    auth = tmp_path / "e_api_authid.txt"
    auth.write_text("ID-PLACEHOLDER\n")
    auth.chmod(0o600)
    missing_key = tmp_path / "missing.pem"
    config = TachibanaConfig.from_env({
        "ARGUS_TACHIBANA_ENABLED": "true",
        "ARGUS_TACHIBANA_AUTH_ID_PATH": str(auth),
        "ARGUS_TACHIBANA_PRIVATE_KEY_PATH": str(missing_key),
    })
    facts = live.secret_file_diagnostics(config)
    assert facts["authId"]["exists"] is True and facts["authId"]["isRegular"] is True
    assert facts["authId"]["modeOctal"] == "0o600" and facts["authId"]["sizePositive"] is True
    assert facts["authId"]["readable"] is True and facts["authId"]["isSymlink"] is False
    assert facts["privateKey"] == {"configuredPath": str(missing_key), "exists": False,
                                   "isSymlink": False}
    serialized = json.dumps(facts)
    assert "ID-PLACEHOLDER" not in serialized
    assert live.secret_file_diagnostics(None) == {}


def test_auth_boundary_is_precise_and_never_collapses_downstream_failures():
    assert live.auth_boundary("SECRET_MISSING", None) == "AUTH_SECRET_MISSING"
    assert live.auth_boundary("SECRET_PERMISSIONS", None) == "AUTH_SECRET_UNREADABLE"
    assert live.auth_boundary("PRIVATE_KEY_INVALID", None) == "AUTH_KEY_PARSE_FAILED"
    assert live.auth_boundary("AUTH_SERVER_REJECTED", {"sResultCode": "12345"}) == "AUTH_SERVER_REJECTED_12345"
    assert live.auth_boundary("AUTH_SUCCESS_DECRYPT_FAILED", None) == "AUTH_SUCCESS_URL_DECRYPT_FAILED"
    assert live.auth_boundary("NETWORK", {"classification": "AUTH_SUCCEEDED"}) == "AUTH_SUCCESS"
    # A PROVIDER failure after a successful login is not an auth failure.
    assert live.auth_boundary("PROVIDER", {"classification": "AUTH_SUCCEEDED"}) == "AUTH_SUCCESS"
    assert live.auth_boundary("PROVIDER", None) is None


def test_evidence_carries_auth_boundary_diagnostic_and_secret_facts(tmp_path):
    config = TachibanaConfig.from_env({
        "ARGUS_TACHIBANA_ENABLED": "true",
        "ARGUS_TACHIBANA_AUTH_ID_PATH": str(tmp_path / "absent.txt"),
        "ARGUS_TACHIBANA_PRIVATE_KEY_PATH": str(tmp_path / "absent.pem"),
    })

    class _Diag:
        def safe_dict(self):
            return {"classification": "AUTH_IN_PROGRESS", "boundary": "NOT_COMPLETED",
                    "httpStatus": None, "sCLMID": None, "sResultCode": None}

    class _Session:
        auth_diagnostic = _Diag()

    class _Runtime(_FakeRuntime):
        session = _Session()

    service = live.TachibanaLiveService(
        config_loader=lambda env=None: config,
        runtime_factory=lambda cfg, *, symbols: _Runtime(cfg, symbols=symbols, fail=ErrorClass.SECRET_MISSING),
        lease_factory=_FakeLease, clock=lambda: TRADING_NOW,
        sleeper=lambda seconds: None, symbols=("8058",))
    service._config = config
    service._run_session(config, ("8058",))
    evidence = service.current_evidence_safe(TRADING_NOW)
    assert evidence["status"] == "AUTH_FAILED"
    assert evidence["authBoundary"] == "AUTH_SECRET_MISSING"
    assert evidence["authDiagnostic"]["classification"] == "AUTH_IN_PROGRESS"
    assert evidence["secretFiles"]["authId"]["exists"] is False
    assert evidence["lastAuthAt"] is not None
    text = json.dumps(evidence).lower()
    for forbidden in ("url", "sauthid", "private-material", "begin private"):
        assert forbidden not in text


def test_module_surface_has_no_order_capability():
    import inspect
    source = inspect.getsource(live)
    for forbidden in ("NewOrder", "Cancel", "Correct", "sOrder", "CLMKabu"):
        assert forbidden not in source
    assert "SHADOW_NON_AUTHORITATIVE" in source


def test_secret_file_diagnostics_report_key_shape_without_contents(tmp_path):
    import json as _json
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    from cryptography.hazmat.primitives import serialization as _ser
    key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(_ser.Encoding.PEM, _ser.PrivateFormat.PKCS8, _ser.NoEncryption())
    auth = tmp_path / "e_api_authid.txt"; auth.write_bytes(b"abc123\n"); auth.chmod(0o600)
    pk = tmp_path / "e_api_private_key.pem"; pk.write_bytes(pem.replace(b"\n", b"\r\n")); pk.chmod(0o600)
    config = TachibanaConfig.from_env({
        "ARGUS_TACHIBANA_ENABLED": "true",
        "ARGUS_TACHIBANA_AUTH_ID_PATH": str(auth),
        "ARGUS_TACHIBANA_PRIVATE_KEY_PATH": str(pk),
    })
    diag = live.secret_file_diagnostics(config)
    shape = diag["privateKey"]["keyShape"]
    assert shape["parsed"] == "PEM" and shape["keySize"] == 2048 and shape["crlf"] is True
    assert "keyShape" not in diag["authId"]
    serialized = _json.dumps(diag)
    body = b"".join(l for l in pem.splitlines() if not l.startswith(b"-----")).decode()
    assert body[:16] not in serialized and "abc123" not in serialized
    pk.write_bytes(b"garbage")
    assert live.secret_file_diagnostics(config)["privateKey"]["keyShape"]["parsed"] == "FAILED"


# ── v13.5.42: closed-session probe + CLOSED status ─────────────────────────
CLOSED_NOW = datetime(2026, 9, 3, 16, 30, tzinfo=TOKYO).astimezone(timezone.utc)


class _ProbeRuntime(_FakeRuntime):
    def __init__(self, config, *, symbols, fail=None):
        super().__init__(config, symbols=symbols, fail=fail)
        self.sensor = _FakeSensor({})
        self._price_observations = {}

    def start(self):
        super().start()
        obs = _observation("8058", now=CLOSED_NOW)
        closed = TachibanaObservation(**{**obs.__dict__, "market_status": MarketStatus.CLOSED,
                                         "freshness": Freshness.DELAYED})
        self._price_observations = {"8058": closed}

    def initial_read_diagnostics_safe_dict(self):
        return {"providerDate": {"stage": "PROVIDER_DATE", "classification": "PASS", "sCLMID": "x"},
                "priceBaseline": {"stage": "PRICE_BASELINE_NORMALIZE", "classification": "PASS"}}


def test_closed_session_probe_proves_auth_date_price_and_reports_closed():
    config = TachibanaConfig.from_env({"ARGUS_TACHIBANA_ENABLED": "true"})
    sleeps = []
    service = live.TachibanaLiveService(
        config_loader=lambda env=None: config, runtime_factory=_ProbeRuntime,
        lease_factory=_FakeLease, clock=lambda: CLOSED_NOW,
        sleeper=lambda seconds: sleeps.append(seconds), symbols=("8058",))
    service._config = config
    assert live.in_live_window(CLOSED_NOW) is False
    assert service._probe_due(CLOSED_NOW) is True
    service._run_closed_probe(config, ("8058",))
    runtime = _FakeRuntime.instances[-1]
    assert runtime.started == 1 and runtime.stopped == 1          # logout after the probe
    evidence = service.current_evidence_safe(CLOSED_NOW)
    assert evidence["status"] == "CLOSED"
    assert evidence["lastAuthResult"] == "PASS" and evidence["authAttempts"] == 1
    assert evidence["authBoundary"] == "AUTH_SUCCESS" or evidence["lastErrorClass"] is None
    probe = evidence["closedSessionProbe"]
    assert probe["result"] == "PASS" and probe["stages"]["providerDate"]["classification"] == "PASS"
    assert "sCLMID" not in json.dumps(probe)                      # names/classes only
    row = evidence["symbols"]["8058"]
    assert row["price"] == 3500.0 and row["marketStatus"] == "CLOSED"
    # not due again for 4h on success; the idle path keeps the closed rows
    assert service._probe_due(CLOSED_NOW + timedelta(hours=1)) is False
    assert service._probe_due(CLOSED_NOW + timedelta(hours=5)) is True
    service._set_idle(keep_rows=True)
    assert service.current_evidence_safe(CLOSED_NOW)["status"] == "CLOSED"
    service._set_idle()
    assert service.current_evidence_safe(CLOSED_NOW)["status"] == "UNAVAILABLE"


def test_closed_session_probe_failure_is_truthful_and_bounded():
    config = TachibanaConfig.from_env({"ARGUS_TACHIBANA_ENABLED": "true"})
    service = live.TachibanaLiveService(
        config_loader=lambda env=None: config,
        runtime_factory=lambda cfg, *, symbols: _ProbeRuntime(cfg, symbols=symbols, fail=ErrorClass.AUTH_REJECTED),
        lease_factory=_FakeLease, clock=lambda: CLOSED_NOW, sleeper=lambda s: None, symbols=("8058",))
    service._config = config
    service._run_closed_probe(config, ("8058",))
    evidence = service.current_evidence_safe(CLOSED_NOW)
    assert evidence["status"] == "AUTH_FAILED" and evidence["lastAuthResult"] == "FAIL"
    assert evidence["closedSessionProbe"]["result"] == "FAIL"
    assert service._probe_due(CLOSED_NOW + timedelta(minutes=10)) is False   # 30 min retry hold
    assert service._probe_due(CLOSED_NOW + timedelta(minutes=31)) is True
    assert _FakeRuntime.instances[-1].stopped == 1


def test_derive_status_closed_requires_probe_and_rows_outside_window():
    rows = {"8058": {"freshness": "DELAYED", "price": 1.0}}
    assert live.derive_status(enabled=True, running=False, last_error_class=None, rows=rows,
                              provider_health="AVAILABLE", in_window=False, closed_probe_ok=True) == "CLOSED"
    assert live.derive_status(enabled=True, running=False, last_error_class=None, rows=rows,
                              provider_health="AVAILABLE", in_window=False) == "UNAVAILABLE"
    assert live.derive_status(enabled=True, running=False, last_error_class=None, rows={},
                              provider_health="AVAILABLE", in_window=False, closed_probe_ok=True) == "UNAVAILABLE"
    assert live.derive_status(enabled=True, running=True, last_error_class=None, rows=rows,
                              provider_health="AVAILABLE", in_window=True, closed_probe_ok=True) == "DEGRADED"


def test_session_ended_by_a_terminal_error_holds_before_reconnecting():
    """v13.5.61: no tight re-authentication loop inside the live window."""
    config = TachibanaConfig.from_env({"ARGUS_TACHIBANA_ENABLED": "true"})
    sleeps = []

    class _TerminalRuntime(_FakeRuntime):
        def __init__(self, cfg, *, symbols, fail=None):
            super().__init__(cfg, symbols=symbols, fail=fail)
            self.terminal_error = ErrorClass.AUTH_REJECTED

    service = live.TachibanaLiveService(
        config_loader=lambda env=None: config, runtime_factory=_TerminalRuntime,
        lease_factory=_FakeLease, clock=lambda: TRADING_NOW,
        sleeper=lambda seconds: sleeps.append(seconds), symbols=("8058",))
    assert service._run_session(config, ("8058",)) is True
    assert _FakeRuntime.instances[-1].stopped == 1
    assert sleeps and sleeps[-1] == live._TERMINAL_HOLD_SECONDS
    assert service.current_evidence_safe()["lastErrorClass"] == ErrorClass.AUTH_REJECTED.value
