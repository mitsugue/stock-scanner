"""Frontend/backend method-version CONTRACT (v13.5.54).

The product verifier compares ``methodVersion`` with strict equality. Two
consumer pins live in the frontend and each has a producer identity in the
backend:

* ``VERIFIED_VIEW_METHOD_VERSION`` (web/src/lib/verifiedSnapshot.ts)
  must equal ``scanner._VERIFIED_VIEW_METHOD_VERSION``.
* ``ASSET_CHART_METHOD_VERSION`` (web/src/lib/assetChartCache.ts)
  must equal ``argus_chart_intelligence.METHOD_VERSION`` — the value every
  chart-intelligence payload carries.

From v13.5.14 (scanner added a fourth segment to the verified view method) to
v13.5.53 the frontend pin was never updated. Nothing failed visibly: every
verified snapshot was rejected as ``method_incompatible``, the client cache
held zero records, ``writeAssetChart`` returned ``None``-equivalent, and each
Pages release's ``seed-warm-profile`` job timed out so the downstream
acceptance jobs were skipped. The frontend's own drift test missed it because
it re-composed the backend value from a hardcoded list of three modules.

This test reads the REAL runtime backend values and the frontend source, so a
future backend method change fails CI until the frontend pin is deliberately
updated. It never spells a method version out.
"""
import pathlib
import re

import argus_chart_intelligence
import scanner

ROOT = pathlib.Path(__file__).resolve().parent


def _exported_string_constant(relative: str, name: str) -> str:
    source = (ROOT / relative).read_text(encoding="utf-8")
    match = re.search(rf"export const {name}\s*=\s*(.*?);", source, re.S)
    assert match, f"missing export {name} in {relative}"
    expression = re.sub(r"//.*$", "", match.group(1), flags=re.M)
    expression = re.sub(r"/\*.*?\*/", "", expression, flags=re.S)
    literals = re.findall(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"", expression)
    value = "".join(a or b for a, b in literals)
    assert value, f"empty constant {name} in {relative}"
    return value


def test_frontend_verified_view_method_equals_backend_runtime_value():
    frontend = _exported_string_constant(
        "web/src/lib/verifiedSnapshot.ts", "VERIFIED_VIEW_METHOD_VERSION")
    assert frontend == scanner._VERIFIED_VIEW_METHOD_VERSION, (
        "frontend VERIFIED_VIEW_METHOD_VERSION drifted from "
        "scanner._VERIFIED_VIEW_METHOD_VERSION; every verified snapshot would "
        "be rejected as method_incompatible. Update the frontend pin "
        "deliberately (and re-verify the release acceptance chain).")


def test_frontend_asset_chart_method_equals_backend_payload_value():
    frontend = _exported_string_constant(
        "web/src/lib/assetChartCache.ts", "ASSET_CHART_METHOD_VERSION")
    assert frontend == argus_chart_intelligence.METHOD_VERSION, (
        "frontend ASSET_CHART_METHOD_VERSION drifted from the payload "
        "methodVersion; writeAssetChart would cache nothing.")


def test_backend_verified_view_method_is_composed_from_the_forecast_engine():
    # The v13.5.14 regression class: the composition grew and no consumer
    # noticed. Pin the shape so a future growth is a visible, deliberate act.
    segments = scanner._VERIFIED_VIEW_METHOD_VERSION.split(":")
    assert len(segments) == 4, segments
    assert segments[1] == argus_chart_intelligence.METHOD_VERSION
    assert "today-replay-calibration" in segments[3]


def test_frontend_drift_reader_follows_the_scanner_composition():
    # The JS-side reader (web/scripts/method-version-contract.mjs) must derive
    # the module list from scanner.py rather than hardcoding it — the exact
    # mistake that hid the v13.5.14 drift for 39 releases.
    reader = (ROOT / "web/scripts/method-version-contract.mjs").read_text(encoding="utf-8")
    assert "_VERIFIED_VIEW_METHOD_VERSION" in reader
    assert "argus_market_replay" not in reader, "module list must not be hardcoded"
    assert "argus_today_intelligence" not in reader, "module list must not be hardcoded"
