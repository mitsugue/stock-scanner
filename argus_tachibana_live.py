"""ARGUS v13.5.38 — Tachibana LIVE product boundary (the one adapter seam).

Tachibana v4r10 sensor -> normalized current-state -> ARGUS product consumers.

This module is the single place the ARGUS product touches the Tachibana
provider package.  It owns:

* lifecycle: one lazily-started daemon thread that runs the read-only
  ``TachibanaLiveRuntime`` inside the JPX cash session window under the host
  singleton lease (exactly one EVENT session per host), with a bounded
  reauthentication budget and no retry storm;
* state: a bounded, transient current-state snapshot (latest observation per
  configured symbol, at most three symbols, no history, no raw frames);
* projection: ``current_evidence_safe()`` — the only thing consumers read —
  a provenance-stamped (``provider = TACHIBANA``), secret-free evidence
  document with a truthful status.

Authority: SHADOW_NON_AUTHORITATIVE.  Nothing here can influence the single
decision authority; the evidence is visible, never overriding.
"""
from __future__ import annotations

import math
import os
import threading
import time as _time
from collections import deque
from datetime import datetime, time as wall_time, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

import argus_market_clock
from argus_providers.tachibana.config import TachibanaConfig
from argus_providers.tachibana.models import ErrorClass, Freshness, TachibanaError

SCHEMA = "argus-tachibana-live-evidence-v1"
PROVIDER = "TACHIBANA"
AUTHORITY = "SHADOW_NON_AUTHORITATIVE"
STATUSES = ("LIVE", "DEGRADED", "STALE", "UNAVAILABLE", "AUTH_FAILED",
            "MAINTENANCE", "DISABLED")

_TOKYO = ZoneInfo("Asia/Tokyo")
_LIVE_START = wall_time(7, 55)
_LIVE_END = wall_time(15, 31)
_POLL_SECONDS = 5.0
_HOLD_SECONDS = 300.0
# v13.5.61 (production 2026-09-07 memory climb): a live session that ended on
# a terminal error returned to the loop with NO wait, so a provider that
# failed right after start was re-authenticated in a tight loop for the whole
# live window — one core busy and allocations accumulating. Hold before the
# next attempt; the closed-window hold stays _HOLD_SECONDS.
_TERMINAL_HOLD_SECONDS = 60.0
_REAUTH_WINDOW_SECONDS = 15 * 60
_MAX_REAUTH_PER_WINDOW = 2
_DEPTH_LEVELS = 5
_AUTH_CLASSES = frozenset({
    "SECRET_MISSING", "SECRET_PERMISSIONS", "PRIVATE_KEY_INVALID",
    "AUTH_REJECTED", "AUTH_LOCAL_STATE_REJECTED", "AUTH_RESPONSE_INVALID",
    "AUTH_SERVER_REJECTED", "AUTH_SUCCESS_VIRTUAL_URLS_WITHHELD",
    "AUTH_SUCCESS_DECRYPT_FAILED", "AUTH_HTTP_FAILED", "AUTH_PROTOCOL_FAILED",
})
_FIELD_MAP = (
    ("price", "current_price"), ("previousClose", "previous_close"),
    ("changeAbs", "change_absolute"), ("changePct", "change_percent"),
    ("open", "open"), ("high", "high"), ("low", "low"),
    ("volume", "volume"), ("turnover", "turnover"), ("vwap", "vwap"),
    ("bestBid", "best_bid"), ("bestAsk", "best_ask"),
    ("bidQty", "best_bid_volume"), ("askQty", "best_ask_volume"),
)


# v13.5.42: closed-session probe (outside the JPX window) — one bounded
# AUTH → DATE → PRICE → logout so the owner sees a truthful CLOSED state
# instead of UNAVAILABLE after hours.  Re-run at most every 4 h on success
# and every 30 min after a failure (no retry storm; auth budget bounded).
_PROBE_INTERVAL_SECONDS = 4 * 3600
_PROBE_RETRY_SECONDS = 1800
_PROBE_SETTLE_SECONDS = 3.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def secret_file_diagnostics(config: Optional[TachibanaConfig]) -> Dict[str, Any]:
    """Secret-safe file facts for the two configured secret paths.

    Reports only: configured path, existence, symlink/regular kind, mode
    (octal), whether the size is positive, readability, and whether the
    resolved location is a platform-managed secret root.  Never a byte of
    content, never a hash.
    """
    if config is None:
        return {}
    import stat as _stat
    out: Dict[str, Any] = {}
    for name, path in (("authId", config.auth_id_path),
                       ("privateKey", config.private_key_path)):
        row: Dict[str, Any] = {"configuredPath": str(path), "exists": False}
        try:
            row["isSymlink"] = path.is_symlink()
            if path.exists():
                info = os.stat(path)
                row.update({
                    "exists": True,
                    "isRegular": _stat.S_ISREG(info.st_mode),
                    "modeOctal": oct(info.st_mode & 0o777),
                    "sizePositive": info.st_size > 0,
                    "readable": os.access(path, os.R_OK),
                    "platformManaged": str(Path(os.path.realpath(path))).startswith("/etc/secrets/"),
                })
                if name == "privateKey":
                    row["keyShape"] = _private_key_shape_safe(path)
        except OSError as exc:
            row["error"] = type(exc).__name__
        out[name] = row
    return out


def _private_key_shape_safe(path: Path) -> Dict[str, Any]:
    """Structural facts only (see session.private_key_shape); never contents."""
    from argus_providers.tachibana import session as _session
    try:
        key_bytes = _session._read_secret(path, ErrorClass.SECRET_MISSING)
    except TachibanaError as exc:
        return {"parsed": "UNREADABLE", "errorClass": exc.classification.value}
    except Exception as exc:                      # pragma: no cover - defensive
        return {"parsed": "UNREADABLE", "errorClass": type(exc).__name__}
    try:
        return _session.private_key_shape(key_bytes)
    except Exception as exc:                      # pragma: no cover - defensive
        return {"parsed": "FAILED", "errorClass": type(exc).__name__}


def auth_boundary(last_error_class: Optional[str],
                  auth_diagnostic: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Precise, secret-safe authentication boundary token for the owner."""
    diag = auth_diagnostic or {}
    if diag.get("classification") == "AUTH_SUCCEEDED":
        return "AUTH_SUCCESS"
    mapping = {
        "CONFIGURATION": "AUTH_CONFIG_MISSING",
        "SECRET_MISSING": "AUTH_SECRET_MISSING",
        "SECRET_PERMISSIONS": "AUTH_SECRET_UNREADABLE",
        "PRIVATE_KEY_INVALID": "AUTH_KEY_PARSE_FAILED",
        "AUTH_HTTP_FAILED": "AUTH_HTTP_FAILED",
        "AUTH_PROTOCOL_FAILED": "AUTH_PROTOCOL_FAILED",
        "AUTH_RESPONSE_INVALID": "AUTH_PROTOCOL_FAILED",
        "AUTH_SUCCESS_VIRTUAL_URLS_WITHHELD": "AUTH_SUCCESS_URLS_WITHHELD",
        "AUTH_SUCCESS_DECRYPT_FAILED": "AUTH_SUCCESS_URL_DECRYPT_FAILED",
        "SESSION_EXPIRED": "AUTH_SESSION_INVALID",
        "MAINTENANCE": "AUTH_MAINTENANCE",
        "NETWORK": "AUTH_TIMEOUT",
    }
    if last_error_class == "AUTH_SERVER_REJECTED":
        code = str(diag.get("sResultCode") or "UNKNOWN")
        return f"AUTH_SERVER_REJECTED_{code}"
    if last_error_class in mapping:
        return mapping[last_error_class]
    if isinstance(diag.get("classification"), str) and diag["classification"].startswith("AUTH_"):
        return diag["classification"]
    return None


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def in_live_window(now: datetime) -> bool:
    """JPX cash-session sensor window (07:55-15:31 JST on trading days)."""
    local = now.astimezone(_TOKYO)
    state = argus_market_clock.market_session(argus_market_clock.JP_EQUITY, local)
    if state.get("isTradingDay") is not True:
        return False
    return _LIVE_START <= local.time() < _LIVE_END


def observation_evidence(observation: Any, *, now: datetime) -> Dict[str, Any]:
    """Project one TachibanaObservation into a bounded, value-safe row."""
    fields = observation.fields
    availability = observation.field_availability
    row: Dict[str, Any] = {"provider": PROVIDER, "authority": AUTHORITY,
                           "symbol": observation.symbol}
    field_availability: Dict[str, bool] = {}
    for public, internal in _FIELD_MAP:
        available = bool(availability.get(internal)) if internal in availability else False
        value = _number(fields.get(internal)) if available else None
        row[public] = value
        field_availability[public] = value is not None
    row["fieldAvailability"] = field_availability
    bids = tuple(observation.bids)[:_DEPTH_LEVELS]
    asks = tuple(observation.asks)[:_DEPTH_LEVELS]
    row["depth"] = {
        "levels": _DEPTH_LEVELS,
        "bidLevels": len(bids), "askLevels": len(asks),
        "bidQtyTop": sum(_number(level.volume) or 0.0 for level in bids),
        "askQtyTop": sum(_number(level.volume) or 0.0 for level in asks),
    }
    fresh_until = observation.fresh_until
    freshness = observation.freshness.value
    if freshness == Freshness.FRESH.value and (
            fresh_until is None or fresh_until < now):
        freshness = Freshness.STALE.value
    row.update({
        "sourceTimestamp": _iso(observation.source_timestamp),
        "receivedAt": _iso(observation.received_timestamp),
        "freshUntil": _iso(fresh_until),
        "freshness": freshness,
        "marketStatus": observation.market_status.value,
        "realtimeClassification": str(observation.realtime_classification),
        "degradedFields": [issue.field for issue in observation.normalization_issues][:8],
    })
    return row


def derive_status(*, enabled: bool, running: bool, last_error_class: Optional[str],
                  rows: Mapping[str, Mapping[str, Any]], provider_health: Optional[str],
                  in_window: bool, closed_probe_ok: bool = False) -> str:
    """Owner-facing status from truthful inputs; never LIVE without evidence."""
    if not enabled:
        return "DISABLED"
    if last_error_class in _AUTH_CLASSES:
        return "AUTH_FAILED"
    if last_error_class == "MAINTENANCE" or provider_health == "MAINTENANCE":
        return "MAINTENANCE"
    if not in_window and closed_probe_ok and rows and not running:
        return "CLOSED"
    if not running or not rows:
        return "UNAVAILABLE"
    fresh = [row for row in rows.values()
             if row.get("freshness") == Freshness.FRESH.value and row.get("price") is not None]
    delayed = [row for row in rows.values()
               if row.get("freshness") == Freshness.DELAYED.value and row.get("price") is not None]
    if fresh and len(fresh) == len(rows) and provider_health in (None, "AVAILABLE"):
        return "LIVE"
    if fresh or delayed:
        return "DEGRADED"
    if in_window:
        return "STALE"
    return "UNAVAILABLE"


class TachibanaLiveService:
    """Bounded single-host live sensor lifecycle + evidence projection."""

    def __init__(self, *, config_loader: Callable[..., TachibanaConfig] = TachibanaConfig.from_env,
                 runtime_factory: Optional[Callable[..., Any]] = None,
                 lease_factory: Optional[Callable[[Path], Any]] = None,
                 clock: Callable[[], datetime] = _utcnow,
                 sleeper: Callable[[float], None] = _time.sleep,
                 symbols: Optional[tuple] = None) -> None:
        self._config_loader = config_loader
        self._runtime_factory = runtime_factory
        self._lease_factory = lease_factory
        self._clock = clock
        self._sleeper = sleeper
        self._symbols_override = symbols
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._config: Optional[TachibanaConfig] = None
        self._runtime: Any = None
        self._running = False
        self._last_error_class: Optional[str] = None
        self._provider_health: Optional[str] = None
        self._phase: Optional[str] = None
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._updated_at: Optional[datetime] = None
        self._auth_attempts = 0
        self._reauth_times: deque = deque()
        self._start_calls = 0
        self._auth_diagnostic: Optional[Dict[str, Any]] = None
        self._last_auth_at: Optional[datetime] = None
        self._probe_at: Optional[datetime] = None
        self._probe_result: Optional[str] = None
        self._probe_stages: Optional[Dict[str, Any]] = None

    # ── configuration ────────────────────────────────────────────────────
    def _load_config(self, environ: Optional[Mapping[str, str]]) -> Optional[TachibanaConfig]:
        try:
            return self._config_loader(environ) if environ is not None else self._config_loader()
        except (ValueError, TypeError):
            self._last_error_class = ErrorClass.CONFIGURATION.value
            return None

    def _symbols(self, environ: Mapping[str, str]) -> tuple:
        if self._symbols_override:
            return tuple(self._symbols_override)
        raw = environ.get("ARGUS_TACHIBANA_SYMBOLS", "8058,9984,5803")
        return tuple(item.strip().upper() for item in raw.split(",") if item.strip())[:3]

    # ── lifecycle ────────────────────────────────────────────────────────
    def ensure_started(self, environ: Optional[Mapping[str, str]] = None) -> str:
        """Idempotent lazy start.  Returns the lifecycle state token."""
        env = os.environ if environ is None else environ
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return "RUNNING"
            config = self._load_config(environ)
            self._config = config
            if config is None:
                return "CONFIGURATION_INVALID"
            if not config.enabled:
                return "DISABLED"
            if self._runtime_factory is None or self._lease_factory is None:
                # Production wiring binds the real runtime and lease; the
                # module never imports them unless the service is enabled.
                from argus_providers.tachibana.runtime import TachibanaLiveRuntime
                from argus_providers.tachibana.singleton import ProcessSingletonLease
                self._runtime_factory = self._runtime_factory or TachibanaLiveRuntime
                self._lease_factory = self._lease_factory or ProcessSingletonLease
            symbols = self._symbols(env)
            lock_path = Path(env.get("ARGUS_TACHIBANA_SINGLETON_PATH",
                                     "/tmp/argus-tachibana-live-sensor.lock"))
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, args=(config, symbols, lock_path),
                name="argus-tachibana-live", daemon=True)
            self._thread.start()
            return "STARTED"

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=10)

    def _consume_reauth_budget(self, now_monotonic: float) -> bool:
        while self._reauth_times and now_monotonic - self._reauth_times[0] >= _REAUTH_WINDOW_SECONDS:
            self._reauth_times.popleft()
        if len(self._reauth_times) >= _MAX_REAUTH_PER_WINDOW:
            return False
        self._reauth_times.append(now_monotonic)
        return True

    def _loop(self, config: TachibanaConfig, symbols: tuple, lock_path: Path) -> None:
        try:
            with self._lease_factory(lock_path):
                while not self._stop.is_set():
                    now = self._clock()
                    if not in_live_window(now):
                        if self._probe_due(now):
                            self._run_closed_probe(config, symbols)
                        else:
                            self._set_idle(keep_rows=self._probe_result == "PASS")
                        self._sleeper(_HOLD_SECONDS)
                        continue
                    if not self._run_session(config, symbols):
                        break
        except Exception as exc:  # lease held elsewhere, or unclassified: hold safely
            self._last_error_class = getattr(getattr(exc, "classification", None), "value",
                                             None) or "UNCLASSIFIED_SAFE_FAILURE"
            self._set_idle()

    def _run_session(self, config: TachibanaConfig, symbols: tuple) -> bool:
        """One bounded authenticated session; returns False to end the loop."""
        runtime = self._runtime_factory(config, symbols=symbols)
        self._start_calls += 1
        self._auth_attempts += 1
        self._last_auth_at = self._clock()
        try:
            runtime.start()
        except TachibanaError as exc:
            self._last_error_class = exc.classification.value
            self._capture_auth_diagnostic(runtime)
            runtime.stop()
            if exc.classification == ErrorClass.SESSION_EXPIRED and \
                    self._consume_reauth_budget(_time.monotonic()):
                self._sleeper(30.0)
                return True
            self._set_idle()
            self._sleeper(_HOLD_SECONDS)
            return exc.classification not in {ErrorClass.CONFIGURATION, ErrorClass.DISABLED}
        except Exception:
            self._last_error_class = "UNCLASSIFIED_SAFE_FAILURE"
            runtime.stop()
            self._set_idle()
            self._sleeper(_HOLD_SECONDS)
            return True
        self._runtime = runtime
        self._running = True
        self._last_error_class = None
        self._capture_auth_diagnostic(runtime)
        terminal = False
        try:
            while not self._stop.is_set() and in_live_window(self._clock()):
                self._refresh(runtime, symbols)
                if runtime.terminal_error != ErrorClass.NONE:
                    self._last_error_class = runtime.terminal_error.value
                    terminal = True
                    break
                self._sleeper(_POLL_SECONDS)
        finally:
            runtime.stop()
            self._running = False
            self._runtime = None
        if terminal and not self._stop.is_set():
            self._sleeper(_TERMINAL_HOLD_SECONDS)
        return True

    def _probe_due(self, now: datetime) -> bool:
        if self._probe_at is None:
            return True
        elapsed = (now - self._probe_at).total_seconds()
        interval = _PROBE_INTERVAL_SECONDS if self._probe_result == "PASS" else _PROBE_RETRY_SECONDS
        return elapsed >= interval

    def _run_closed_probe(self, config: TachibanaConfig, symbols: tuple) -> None:
        """One bounded AUTH → DATE → PRICE → logout outside the live window.

        Proves the production credentials and the read contract without a
        market session; retains the price baseline rows (marketStatus CLOSED)
        so the owner surface can show CLOSED truthfully.  Never streams.
        """
        runtime = self._runtime_factory(config, symbols=symbols)
        now = self._clock()
        self._start_calls += 1
        self._auth_attempts += 1
        self._last_auth_at = now
        self._probe_at = now
        stages: Dict[str, Any] = {}
        try:
            runtime.start()
        except TachibanaError as exc:
            self._last_error_class = exc.classification.value
            self._capture_auth_diagnostic(runtime)
            stages = self._probe_stage_summary(runtime)
            runtime.stop()
            with self._lock:
                self._probe_result, self._probe_stages = "FAIL", stages
            self._set_idle()
            return
        except Exception:
            self._last_error_class = "UNCLASSIFIED_SAFE_FAILURE"
            runtime.stop()
            with self._lock:
                self._probe_result, self._probe_stages = "FAIL", {}
            self._set_idle()
            return
        self._last_error_class = None
        self._capture_auth_diagnostic(runtime)
        try:
            self._sleeper(_PROBE_SETTLE_SECONDS)
            self._refresh(runtime, symbols)
            stages = self._probe_stage_summary(runtime)
        finally:
            runtime.stop()
        with self._lock:
            self._running = False
            self._runtime = None
            self._probe_result = "PASS" if self._rows else "PARTIAL"
            self._probe_stages = stages

    @staticmethod
    def _probe_stage_summary(runtime: Any) -> Dict[str, Any]:
        """Stage classifications only (PASS/error class); never provider text."""
        try:
            raw = runtime.initial_read_diagnostics_safe_dict()
        except Exception:
            return {}
        out: Dict[str, Any] = {}
        if isinstance(raw, dict):
            for name, diagnostic in list(raw.items())[:4]:
                if isinstance(diagnostic, dict):
                    out[str(name)[:32]] = {
                        "stage": str(diagnostic.get("stage"))[:48],
                        "classification": str(diagnostic.get("classification"))[:48],
                    }
        return out

    def _refresh(self, runtime: Any, symbols: tuple) -> None:
        now = self._clock()
        rows: Dict[str, Dict[str, Any]] = {}
        for symbol in symbols:
            observation = runtime.sensor.latest(symbol, now=now)
            if observation is None:
                observation = getattr(runtime, "_price_observations", {}).get(symbol)
            if observation is not None:
                rows[symbol] = observation_evidence(observation, now=now)
        try:
            snapshot = runtime.acceptance_snapshot()
            self._provider_health = str(snapshot.provider_health)
            self._phase = str(snapshot.session_phase)
        except Exception:
            self._provider_health, self._phase = None, None
        with self._lock:
            self._rows = rows
            self._updated_at = now

    def _capture_auth_diagnostic(self, runtime: Any) -> None:
        """Retain the runtime's bounded, secret-safe auth diagnostic."""
        try:
            diagnostic = runtime.session.auth_diagnostic.safe_dict()
        except Exception:
            return
        if isinstance(diagnostic, dict):
            with self._lock:
                self._auth_diagnostic = diagnostic

    def _set_idle(self, keep_rows: bool = False) -> None:
        with self._lock:
            if not keep_rows:
                self._rows = {}
            self._running = False
            self._runtime = None

    # ── projection (the only consumer surface) ───────────────────────────
    def current_evidence_safe(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        moment = now or self._clock()
        with self._lock:
            rows = {symbol: dict(row) for symbol, row in self._rows.items()}
            config = self._config
            running = self._running
            last_error = self._last_error_class
            health = self._provider_health
            phase = self._phase
            updated_at = self._updated_at
            auth_attempts = self._auth_attempts
            auth_diagnostic = dict(self._auth_diagnostic) if self._auth_diagnostic else None
            last_auth_at = self._last_auth_at
            probe_at, probe_result = self._probe_at, self._probe_result
            probe_stages = dict(self._probe_stages) if self._probe_stages else None
        for row in rows.values():
            fresh_until = row.get("freshUntil")
            if row.get("freshness") == Freshness.FRESH.value and fresh_until:
                try:
                    if datetime.fromisoformat(fresh_until) < moment:
                        row["freshness"] = Freshness.STALE.value
                except ValueError:
                    row["freshness"] = Freshness.STALE.value
        enabled = bool(config.enabled) if config is not None else False
        status = derive_status(enabled=enabled, running=running,
                               last_error_class=last_error, rows=rows,
                               provider_health=health, in_window=in_live_window(moment),
                               closed_probe_ok=probe_result == "PASS")
        return {
            "schemaVersion": SCHEMA,
            "provider": PROVIDER,
            "authority": AUTHORITY,
            "status": status,
            "enabled": enabled,
            "shadowOnly": True,
            "authoritative": False,
            "executionCapability": False,
            "providerHealth": health,
            "marketPhase": phase,
            "lastErrorClass": last_error,
            "authAttempts": auth_attempts,
            "lastAuthAt": _iso(last_auth_at),
            "authBoundary": auth_boundary(last_error, auth_diagnostic),
            "lastAuthResult": ("PASS" if (running or probe_result == "PASS") and last_error is None
                               else ("FAIL" if last_error else None)),
            "closedSessionProbe": {
                "at": _iso(probe_at), "result": probe_result, "stages": probe_stages,
                "intervalSeconds": _PROBE_INTERVAL_SECONDS,
            },
            "authDiagnostic": auth_diagnostic,
            "secretFiles": secret_file_diagnostics(config) if enabled else {},
            "updatedAt": _iso(updated_at),
            "asOf": _iso(moment),
            "symbols": rows,
            "symbolCount": len(rows),
            "productBoot": _product_boot_summary(),
        }


_SERVICE = TachibanaLiveService()


def _product_boot_summary() -> Optional[Dict[str, Any]]:
    """v13.5.45: bounded, symbol-free boot-warm summary (owner-visible truth
    about why a feed is warm or cold after a deploy).  Never raises."""
    try:
        import argus_chart_bootstrap
        return argus_chart_bootstrap.warm_status_safe()
    except Exception:
        return None


def ensure_started(environ: Optional[Mapping[str, str]] = None) -> str:
    # v13.5.42: the request autostart seam is the only product-owned boot
    # hook (scanner is Recovery-frozen), so the chart bootstrap shares it.
    try:
        import argus_chart_bootstrap
        argus_chart_bootstrap.ensure_started()
    except Exception:
        pass
    return _SERVICE.ensure_started(environ)


def current_evidence_safe(now: Optional[datetime] = None) -> Dict[str, Any]:
    return _SERVICE.current_evidence_safe(now)


__all__ = [
    "AUTHORITY", "PROVIDER", "SCHEMA", "STATUSES", "TachibanaLiveService",
    "current_evidence_safe", "derive_status", "ensure_started",
    "in_live_window", "observation_evidence",
]
