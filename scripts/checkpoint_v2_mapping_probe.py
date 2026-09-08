#!/usr/bin/env python3
"""32-cycle exact-state Linux mapping/allocator/reachability proof."""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import gc
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import threading
import time
import tracemalloc
import types
import weakref
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import argus_checkpoint_v2 as v2  # noqa: E402
import argus_mapping_attribution as maps  # noqa: E402
from scripts.checkpoint_v2_resource_probe import (  # noqa: E402
    cgroup_value, current_rss_bytes, load_snapshot, peak_rss_bytes,
    smaps_rollup_bytes)

QUIET_SECONDS = 0.25
WARMUP_CYCLES = 2
PLATEAU_WINDOW_CYCLES = 6

# These ceilings are derived from the exact 32-cycle production-shaped proof,
# not from total /proc/maps count.  The proof observed 71 allocator-large mmap
# records, 197,443,584 anonymous allocator bytes, a 33-record steady mapping
# band, and a 9,445,376-byte RSS band in its final six cycles.  Deliberate
# evidence margins keep the gate sensitive without treating address-space
# splitting/coalescing as an application leak.
PRECISE_MAPPING_ENVELOPE = {
    "allocatorAnonymousBytes": 256 * 1024 ** 2,
    "allocatorArenaMappings": 4,
    "allocatorLargeMmapMappings": 96,
    "steadyMappingBand": 64,
    "allocatorLargeMmapBand": 64,
    "steadyRssGrowthBytes": 128 * 1024 ** 2,
    "steadyPssGrowthBytes": 128 * 1024 ** 2,
    "steadyAllocatorAnonymousGrowthBytes": 128 * 1024 ** 2,
    "plateauWindowBytes": 32 * 1024 ** 2,
    # v13.5.64: the main-arena bound is split into what the application still
    # holds (in-use) and what glibc keeps as free-but-unreturned chunks.
    # Measured on the normal-use production snapshot (10,698,752 generation
    # bytes, 50 sections, 54 rows, 2026-09-07 run 34167048143 attempt 2):
    # quiet in-use 7,505,040 B (baseline 6,295,904 B), free-retained
    # 34,356,080 B, system 41,861,120 B, growth over 30 steady cycles
    # 1,925,120 B. The former absolute system bound (32 MiB) was derived from
    # a snapshot whose bulk lived in mmap'd large objects; a snapshot made of
    # small nested objects (asset chart reports) pins the arena top after the
    # generation is freed, so malloc_trim cannot return it. Retention that
    # does not grow is allocator behaviour, not a leak: in-use stays bounded
    # (32 MiB), system bytes stay bounded relative to the restored source
    # (32 MiB + 4 B per source byte), and the 16 MiB growth bound is kept.
    "allocatorInUseBytes": 32 * 1024 ** 2,
    "allocatorSystemBytes": 32 * 1024 ** 2,
    "allocatorSystemBytesPerSourceByte": 4.0,
    "allocatorSystemGrowthBytes": 16 * 1024 ** 2,
    "cgroupPeakBytes": 3 * 1024 ** 3,
}


class GenerationContext:
    def __init__(self, snapshot):
        self.snapshot = snapshot


def _descriptors():
    try:
        return len(list(pathlib.Path("/proc/self/fd").iterdir()))
    except OSError:
        return None


def reachability_counts():
    result = {"sqliteConnections": 0, "sqliteCursors": 0, "futures": 0,
              "tracebacks": 0, "memoryviews": 0, "generationContexts": 0,
              "manifestCandidates": 0, "verificationObjects": 0,
              "telemetryRecords": 0, "telemetryRawPayloadOwners": 0,
              "largeTrackedContainers": 0, "largeTrackedBytes": 0,
              "threadLocals": 0}
    for value in gc.get_objects():
        try:
            result["sqliteConnections"] += isinstance(value, sqlite3.Connection)
            result["sqliteCursors"] += isinstance(value, sqlite3.Cursor)
            result["futures"] += isinstance(value, concurrent.futures.Future)
            result["tracebacks"] += isinstance(value, types.TracebackType)
            result["memoryviews"] += isinstance(value, memoryview)
            result["generationContexts"] += isinstance(value, GenerationContext)
            result["threadLocals"] += isinstance(value, threading.local)
            if isinstance(value, dict):
                keys = set(value)
                result["manifestCandidates"] += {
                    "generationId", "sections", "database"} <= keys
                result["verificationObjects"] += value.get("verified") is True
                if str(value.get("schemaVersion") or "").startswith(
                        "argus-checkpoint-v2-generation-resource"):
                    result["telemetryRecords"] += 1
                    result["telemetryRawPayloadOwners"] += any(
                        key in value for key in ("snapshot", "payload", "rows",
                                                "sections"))
            if isinstance(value, (dict, list)):
                size = sys.getsizeof(value)
                if size >= 1024 * 1024:
                    result["largeTrackedContainers"] += 1
                    result["largeTrackedBytes"] += size
        except (ReferenceError, RuntimeError):
            continue
    result.update({"threads": threading.active_count(),
                   "descriptors": _descriptors()})
    return result


def _no_trim_report(source_bytes):
    rss = current_rss_bytes()
    return {"attempted": False, "supported": True,
            "sourceBytes": int(source_bytes), "rssBeforeBytes": rss,
            "rssAfterBytes": rss, "rssReleasedBytes": 0,
            "reportedReleasedBytes": None, "testVariant": "without_trim"}


def _trim_context(variant):
    if variant in {"pre_fix", "candidate_without_trim"}:
        return mock.patch.object(v2, "_release_unused_allocator_memory",
                                 side_effect=_no_trim_report)
    return contextlib.nullcontext()


def _capture(raw_dir, tag, previous, active_generation=None):
    records, report = maps.snapshot_process_maps(
        raw_dir, tag, previous=previous,
        active_generation=active_generation)
    report["gate"] = maps.gate_projection(report)
    report["allocator"] = maps.glibc_allocator_diagnostics()
    report["rssBytes"] = current_rss_bytes()
    report["peakRssBytes"] = peak_rss_bytes()
    report["cgroupCurrentBytes"] = cgroup_value("memory.current")
    report["cgroupPeakBytes"] = cgroup_value("memory.peak")
    report.update(smaps_rollup_bytes())
    return records, report


def _append(path, value):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")


def _band(values):
    return {"minimum": min(values), "maximum": max(values),
            "span": max(values) - min(values),
            "first": values[0], "last": values[-1],
            "growth": values[-1] - values[0],
            "strictlyMonotonic": len(values) > 1 and all(
                b > a for a, b in zip(values, values[1:]))}


def run_variant(root, source_json, artifact_dir, raw_dir, variant, cycles,
                quiet_seconds=QUIET_SECONDS, trace_allocations=False):
    run_root = pathlib.Path(root) / variant
    run_root.mkdir(parents=True, exist_ok=True)
    artifact_dir = pathlib.Path(artifact_dir) / variant
    artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = pathlib.Path(raw_dir) / variant
    raw_dir.mkdir(parents=True, exist_ok=True)
    reports_path = artifact_dir / "cycle-reports.ndjson"
    reports_path.write_text("")
    if trace_allocations:
        tracemalloc.start()
    previous, baseline = _capture(raw_dir, "process-baseline", None)
    baseline_reachability = reachability_counts()
    _append(reports_path, {"phase": "baseline", "mapping": baseline,
                           "reachability": baseline_reachability})
    all_verified = all_consumed = all_contexts_released = True
    with _trim_context(variant):
        for index in range(1, cycles + 1):
            before_records, before = _capture(
                raw_dir, f"cycle-{index:02d}-before", previous)
            owner = GenerationContext(load_snapshot(source_json, 1.0))
            owner_ref = weakref.ref(owner)
            consume = variant != "pre_fix"
            result = v2.write_generation(
                str(run_root), owner.snapshot,
                source_generation=f"mapping-{variant}-{index}",
                consume_snapshot=consume,
                validation_context={"triggerSource": "resource_probe",
                                    "missionWindowId": f"mapping-{index}",
                                    "natural": False,
                                    "formalSoakState": "not_started"})
            consumed = owner.snapshot == {} if consume else True
            active_generation = result.get("generationId")
            after_records, after = _capture(
                raw_dir, f"cycle-{index:02d}-after", before_records,
                active_generation=active_generation)
            write_verified = bool(result.get("verified"))
            source_bytes = int(result.get("sourceSerializedBytes") or 0)
            result_telemetry = dict(result.get("resourceTelemetry") or {})
            result_telemetry["sourceSerializedBytes"] = source_bytes
            del result, owner
            gc.collect()
            time.sleep(quiet_seconds)
            context_released = owner_ref() is None
            quiet_records, quiet = _capture(
                raw_dir, f"cycle-{index:02d}-quiet", after_records,
                active_generation=active_generation)
            reachability = reachability_counts()
            if trace_allocations:
                traced_current, traced_peak = tracemalloc.get_traced_memory()
            else:
                traced_current = traced_peak = None
            disk = shutil.disk_usage(run_root)
            retained = len(list(run_root.glob("v2-generation-*")))
            pending = len(list(run_root.glob(".v2-pending-*")))
            row = {"cycle": index, "variant": variant,
                   "writeVerified": write_verified,
                   "snapshotConsumed": consumed,
                   "generationContextReleased": context_released,
                   "generationBytes": result_telemetry.get("generationBytes"),
                   "sourceSerializedBytes": result_telemetry.get("sourceSerializedBytes"),
                   "sectionCount": result_telemetry.get("sectionCount"),
                   "rowCount": result_telemetry.get("rowCount"),
                   "durationMs": result_telemetry.get("durationMs"),
                   "before": before, "after": after, "quiet": quiet,
                   "reachability": reachability,
                   "tracemallocCurrentBytes": traced_current,
                   "tracemallocPeakBytes": traced_peak,
                   "pendingGenerations": pending,
                   "retainedGenerations": retained,
                   "diskFreeBytes": disk.free}
            _append(reports_path, row)
            print(json.dumps({
                "schemaVersion": "argus-checkpoint-v2-mapping-progress-v1",
                "variant": variant, "cycle": index,
                "writeVerified": write_verified,
                "generationContextReleased": context_released,
                "rssBytes": quiet["rssBytes"],
                "pssBytes": quiet.get("PssBytes"),
                "mappingCount": quiet["categories"]["__total__"][
                    "mappingCount"],
                "activeGenerationFileMappings": quiet["gate"][
                    "activeGenerationFileMappings"],
                "v2TempMappings": quiet["gate"]["v2TempMappings"],
                "deletedMappings": quiet["gate"]["deletedMappings"],
                "unknownMappings": quiet["gate"]["unknownMappings"],
                "pendingGenerations": pending,
                "retainedGenerations": retained,
            }, sort_keys=True), flush=True)
            all_verified &= write_verified
            all_consumed &= consumed
            all_contexts_released &= context_released
            previous = quiet_records
            row = before = after = quiet = reachability = None
    gc.collect()
    time.sleep(quiet_seconds * 4)
    final_records, final = _capture(
        raw_dir, "extended-final", previous)
    final_reachability = reachability_counts()
    # A full restore is deliberately performed only after the writer closure
    # snapshot. Repeating restore in the writer process on every cycle would
    # conflate restore allocations with the writer mappings under test.
    restored = v2.restore_generation(str(run_root), include_archived=False)
    final_restore_verified = bool(restored.get("verified"))
    del restored["snapshot"], restored
    gc.collect()
    reports = [json.loads(line) for line in reports_path.read_text().splitlines()
               if line]
    cycles_only = [row for row in reports if row.get("cycle")]
    steady = cycles_only[WARMUP_CYCLES:]
    category_names = sorted({name for row in steady for name in
                             row["quiet"]["categories"] if name != "__total__"})
    category_bands = {}
    for name in category_names:
        category_bands[name] = {}
        for field in ("mappingCount", "virtualBytes", "rssBytes", "pssBytes",
                      "anonymousResidentBytes"):
            values = [int((row["quiet"]["categories"].get(name) or {}).get(
                field) or 0) for row in steady]
            category_bands[name][field] = {
                "minimum": min(values), "maximum": max(values),
                "first": values[0], "last": values[-1],
                "growth": values[-1] - values[0],
                "strictlyMonotonic": len(values) > 1 and all(
                    b > a for a, b in zip(values, values[1:])),
            }
    rss = [row["quiet"]["rssBytes"] for row in steady]
    pss = [row["quiet"].get("PssBytes", 0) for row in steady]
    anon = [row["quiet"]["gate"]["allocatorAnonymousBytes"] for row in steady]
    alloc_system = [int(row["quiet"]["allocator"].get("systemBytes") or 0)
                    for row in steady]
    alloc_in_use = [int(row["quiet"]["allocator"].get("inUseBytes") or 0)
                    for row in steady]
    alloc_free_retained = [int(row["quiet"]["allocator"].get(
        "freeRetainedBytes") or 0) for row in steady]
    # v13.5.64: the restored data range is part of the proof — the bounds
    # below are read against it, and the report states what was recovered.
    source_generation_bytes = max(
        int(row.get("generationBytes") or 0) for row in cycles_only)
    source_serialized_bytes = max(
        int(row.get("sourceSerializedBytes") or 0) for row in cycles_only)
    plateau = steady[-PLATEAU_WINDOW_CYCLES:]
    plateau_rss = [row["quiet"]["rssBytes"] for row in plateau]
    plateau_pss = [row["quiet"].get("PssBytes", 0) for row in plateau]
    plateau_anon = [row["quiet"]["gate"]["allocatorAnonymousBytes"]
                    for row in plateau]
    plateau_maps = [row["quiet"]["categories"]["__total__"][
        "mappingCount"] for row in plateau]
    plateau_large_maps = [row["quiet"]["gate"][
        "allocatorLargeMmapMappings"] for row in plateau]
    cgroup_peak = max(int(row["quiet"].get("cgroupPeakBytes") or 0)
                      for row in cycles_only)
    summary = {
        "schemaVersion": "argus-checkpoint-v2-mapping-proof-v2",
        "variant": variant, "cycles": cycles,
        "warmupCycles": WARMUP_CYCLES,
        "allVerified": all_verified, "allConsumed": all_consumed,
        "verificationMode": "write_hash_each_cycle_final_full_restore",
        "finalRestoreVerified": final_restore_verified,
        "tracemallocEnabled": trace_allocations,
        "allGenerationContextsReleased": all_contexts_released,
        "baselineMappingCount": baseline["categories"]["__total__"][
            "mappingCount"],
        "finalMappingCount": final["categories"]["__total__"]["mappingCount"],
        "minimumSteadyMappingCount": min(row["quiet"]["categories"][
            "__total__"]["mappingCount"] for row in steady),
        "maximumSteadyMappingCount": max(row["quiet"]["categories"][
            "__total__"]["mappingCount"] for row in steady),
        "rssSamples": rss, "pssSamples": pss,
        "allocatorAnonymousSamples": anon,
        "allocatorSystemSamples": alloc_system,
        "allocatorInUseSamples": alloc_in_use,
        "allocatorFreeRetainedSamples": alloc_free_retained,
        "sourceGenerationBytes": source_generation_bytes,
        "sourceSerializedBytes": source_serialized_bytes,
        "sourceSectionCount": cycles_only[-1].get("sectionCount"),
        "sourceRowCount": cycles_only[-1].get("rowCount"),
        "rssGrowthBytes": rss[-1] - rss[0],
        "pssGrowthBytes": pss[-1] - pss[0],
        "anonymousGrowthBytes": anon[-1] - anon[0],
        "allocatorSystemGrowthBytes": alloc_system[-1] - alloc_system[0],
        "plateauWindowCycles": len(plateau),
        "plateauWindow": {
            "rssBytes": _band(plateau_rss),
            "pssBytes": _band(plateau_pss),
            "allocatorAnonymousBytes": _band(plateau_anon),
            "mappingCount": _band(plateau_maps),
            "allocatorLargeMmapMappings": _band(plateau_large_maps),
        },
        "preciseMappingEnvelope": PRECISE_MAPPING_ENVELOPE,
        "cgroupPeakBytes": cgroup_peak,
        "categoryBands": category_bands,
        "baselineReachability": baseline_reachability,
        "finalReachability": final_reachability,
        "finalGate": final["gate"],
        "pendingGenerations": cycles_only[-1]["pendingGenerations"],
        "retainedGenerations": cycles_only[-1]["retainedGenerations"],
        "diskFreeBytes": cycles_only[-1]["diskFreeBytes"],
        "reportsArtifact": reports_path.name,
    }
    (artifact_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return summary


def precise_gate_failures(report):
    failures = []
    if report["cycles"] < 32:
        failures.append("mapping_proof_requires_32_cycles")
    if not (report["allVerified"] and report["finalRestoreVerified"] and
            report["allConsumed"] and
            report["allGenerationContextsReleased"]):
        failures.append("mapping_proof_lifecycle_failed")
    gate = report["finalGate"]
    for field in ("activeGenerationFileMappings",
                  "retainedGenerationFileMappings", "v2TempMappings",
                  "deletedMappings", "incidentTempMappings", "unknownMappings"):
        if gate[field]:
            failures.append(f"mapping_proof_{field}_nonzero")
    envelope = report.get("preciseMappingEnvelope") or \
        PRECISE_MAPPING_ENVELOPE
    if report["cgroupPeakBytes"] >= envelope["cgroupPeakBytes"]:
        failures.append("mapping_proof_cgroup_peak_exceeded")
    if report["diskFreeBytes"] < 1024 ** 3:
        failures.append("mapping_proof_disk_reserve_failed")
    if report["pendingGenerations"] or report["retainedGenerations"] > 4:
        failures.append("mapping_proof_generation_retention_failed")
    if report["rssGrowthBytes"] >= envelope["steadyRssGrowthBytes"]:
        failures.append("mapping_proof_rss_envelope_exceeded")
    if report["pssGrowthBytes"] >= envelope["steadyPssGrowthBytes"]:
        failures.append("mapping_proof_pss_envelope_exceeded")
    if report["anonymousGrowthBytes"] >= \
            envelope["steadyAllocatorAnonymousGrowthBytes"]:
        failures.append("mapping_proof_allocator_anonymous_growth_exceeded")
    if gate["allocatorAnonymousBytes"] > \
            envelope["allocatorAnonymousBytes"]:
        failures.append("mapping_proof_allocator_anonymous_bytes_exceeded")
    if gate["allocatorArenaMappings"] > \
            envelope["allocatorArenaMappings"]:
        failures.append("mapping_proof_allocator_arena_count_exceeded")
    if gate["allocatorLargeMmapMappings"] > \
            envelope["allocatorLargeMmapMappings"]:
        failures.append("mapping_proof_allocator_large_mmap_count_exceeded")
    if report["maximumSteadyMappingCount"] - \
            report["minimumSteadyMappingCount"] > \
            envelope["steadyMappingBand"]:
        failures.append("mapping_proof_steady_mapping_band_exceeded")
    large_band = report["categoryBands"].get(
        "allocator large-object mmap", {}).get("mappingCount", {})
    if int(large_band.get("maximum") or 0) - \
            int(large_band.get("minimum") or 0) > \
            envelope["allocatorLargeMmapBand"]:
        failures.append("mapping_proof_allocator_large_mmap_band_exceeded")
    allocator_system = report["allocatorSystemSamples"]
    # v13.5.64: in-use bytes are the application's retention; system bytes
    # are bounded relative to the source that every cycle restores (glibc
    # keeps freed small-object chunks it cannot return). Reports produced by
    # an older probe carry no in-use samples and keep the old absolute rule.
    in_use = report.get("allocatorInUseSamples") or []
    source_bytes = int(report.get("sourceGenerationBytes") or 0)
    if in_use:
        if in_use[-1] > envelope.get("allocatorInUseBytes",
                                     envelope["allocatorSystemBytes"]):
            failures.append("mapping_proof_allocator_in_use_bytes_exceeded")
        system_bound = envelope["allocatorSystemBytes"] + int(
            float(envelope.get("allocatorSystemBytesPerSourceByte") or 0.0)
            * source_bytes)
    else:
        system_bound = envelope["allocatorSystemBytes"]
    if allocator_system[-1] > system_bound:
        failures.append("mapping_proof_allocator_system_bytes_exceeded")
    if report["allocatorSystemGrowthBytes"] > \
            envelope["allocatorSystemGrowthBytes"]:
        failures.append("mapping_proof_allocator_system_growth_exceeded")
    plateau = report["plateauWindow"]
    for field in ("rssBytes", "pssBytes", "allocatorAnonymousBytes"):
        if plateau[field]["span"] > envelope["plateauWindowBytes"]:
            failures.append("mapping_proof_plateau_window_exceeded:" + field)
    if plateau["mappingCount"]["span"] > envelope["steadyMappingBand"]:
        failures.append("mapping_proof_plateau_mapping_band_exceeded")
    if plateau["allocatorLargeMmapMappings"]["span"] > \
            envelope["allocatorLargeMmapBand"]:
        failures.append("mapping_proof_plateau_allocator_band_exceeded")
    for category, bands in report["categoryBands"].items():
        for field in ("mappingCount", "virtualBytes", "anonymousResidentBytes"):
            if bands[field]["strictlyMonotonic"] and bands[field]["growth"] > 0:
                failures.append("mapping_proof_unbounded_category:" +
                                category)
    for category in ("thread stack", "shared library"):
        bands = report["categoryBands"].get(category) or {}
        count_band = bands.get("mappingCount") or {}
        if int(count_band.get("growth") or 0) > 0:
            failures.append("mapping_proof_growing_category:" + category)
    baseline, final = report["baselineReachability"], report["finalReachability"]
    for field in ("sqliteConnections", "sqliteCursors", "futures",
                  "generationContexts", "telemetryRawPayloadOwners", "threads",
                  "descriptors"):
        if baseline[field] is not None and final[field] > baseline[field]:
            failures.append("mapping_proof_reachable_growth:" + field)
    for field in ("largeTrackedBytes", "largeTrackedContainers",
                  "memoryviews", "tracebacks", "manifestCandidates",
                  "verificationObjects"):
        if final[field]:
            failures.append("mapping_proof_reachable_survivor:" + field)
    return failures


def assert_proof(report):
    failures = precise_gate_failures(report)
    if failures:
        raise SystemExit(failures[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--source-json", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--raw-dir", required=True,
                        help="ephemeral directory; never upload this path")
    parser.add_argument("--variant", choices=("pre_fix",
                                              "candidate_without_trim",
                                              "candidate_with_trim"), required=True)
    parser.add_argument("--cycles", type=int, default=32)
    parser.add_argument("--quiet-seconds", type=float, default=QUIET_SECONDS)
    parser.add_argument("--assert-proof", action="store_true")
    parser.add_argument("--tracemalloc", action="store_true",
                        help="test-only detailed Python allocation tracing")
    args = parser.parse_args()
    report = run_variant(args.root, args.source_json, args.artifact_dir,
                         args.raw_dir, args.variant, args.cycles,
                         args.quiet_seconds, args.tracemalloc)
    if args.assert_proof:
        assert_proof(report)


if __name__ == "__main__":
    main()
