"""Deterministic contracts for Linux Checkpoint V2 mapping attribution."""
from __future__ import annotations

import json
import pathlib
from unittest import mock

import argus_mapping_attribution as mapping
from scripts.summarize_mmap_trace import summarize
from scripts.checkpoint_v2_mapping_probe import (
    PRECISE_MAPPING_ENVELOPE, precise_gate_failures)


def _row(address, perms, path="", *, size=4, rss=4, anon=4,
         flags="rd wr mr mw me ac sd"):
    start = int(address, 16)
    end = start + size * 1024
    return (f"{start:x}-{end:x} {perms} 00000000 00:00 0 {path}\n"
            f"Size: {size} kB\nRss: {rss} kB\nPss: {rss} kB\n"
            f"Private_Clean: 0 kB\nPrivate_Dirty: {rss} kB\n"
            "Shared_Clean: 0 kB\nShared_Dirty: 0 kB\n"
            f"Anonymous: {anon} kB\nSwap: 0 kB\n"
            "KernelPageSize: 4 kB\nMMUPageSize: 4 kB\n"
            f"VmFlags: {flags}\n")


def test_parse_records_preserves_required_metrics_and_stable_fingerprint():
    first = mapping.parse_smaps(_row("1000", "rw-p", "[heap]"))[0]
    moved = mapping.parse_smaps(_row("9000", "rw-p", "[heap]"))[0]
    assert first.category == "heap"
    assert first.metrics["Rss"] == 4096
    assert first.metrics["Pss"] == 4096
    assert first.metrics["Anonymous"] == 4096
    assert first.metrics["KernelPageSize"] == 4096
    assert first.vm_flags == ("rd", "wr", "mr", "mw", "me", "ac", "sd")
    assert first.fingerprint() == moved.fingerprint()


def test_every_required_mapping_class_is_explicit_and_paths_are_redacted():
    text = "".join([
        _row("1000", "r-xp", "/usr/local/bin/python"),
        _row("2000", "r-xp", "/usr/lib/libsqlite3.so.0"),
        _row("3000", "rw-p", "/tmp/v2-generation-" + "a" * 32 +
             "/checkpoint-v2.sqlite"),
        _row("4000", "rw-p", "/tmp/.v2-pending-x/checkpoint-v2.sqlite"),
        _row("5000", "rw-p", "/tmp/state.incident-1.v1338-tmp"),
        _row("6000", "rw-p", "/tmp/gone (deleted)"),
        _row("7000", "rw-p", "[stack]"),
        _row("8000", "rw-p", "[stack:7]"),
        _row("9000", "rw-s", ""),
        _row("a000", "r-xp", "/usr/lib/libc.so.6"),
        _row("b000", "r-xp", "[vdso]"),
    ])
    records = mapping.parse_smaps(text)
    categories = {row.category for row in records}
    assert categories >= {
        "Python executable or extension", "SQLite library",
        "retained SQLite generation file", "V2 temporary generation file",
        "legacy checkpoint or incident temp", "deleted file", "main stack",
        "thread stack", "anonymous shared mapping", "shared library",
        "kernel/vdso/vvar/vsyscall",
    }
    structured = [row.diagnostic_record() for row in records]
    assert all("/tmp/" not in row["pathnameClass"] for row in structured)


def test_glibc_arena_pair_and_large_mmap_are_separate_categories():
    committed_kib = 1024
    reserved_kib = 64 * 1024 - committed_kib
    text = (_row("10000000", "rw-p", size=committed_kib,
                 rss=128, anon=128) +
            _row(f"{0x10000000 + committed_kib * 1024:x}", "---p",
                 size=reserved_kib, rss=0, anon=0, flags="mr mw me nr sd") +
            _row("30000000", "rw-p", size=512, rss=256, anon=256))
    records = mapping.parse_smaps(text)
    assert [row.category for row in records[:2]] == [
        "allocator arena", "allocator arena"]
    assert records[2].category == "allocator large-object mmap"


def test_category_delta_and_precise_gate_projection():
    before = mapping.parse_smaps(_row("1000", "rw-p", "[heap]"))
    after = mapping.parse_smaps(
        _row("9000", "rw-p", "[heap]") +
        _row("b000", "rw-p", "/tmp/v2-generation-" + "b" * 32 +
             "/checkpoint-v2.sqlite"))
    summary = {"categories": mapping.category_summary(after, before)}
    assert summary["categories"]["heap"]["survivingFromEarlier"] == 1
    gate = mapping.gate_projection(summary)
    assert gate["retainedGenerationFileMappings"] == 1
    assert gate["unknownMappings"] == 0


def test_allocator_diagnostics_fail_safe_off_linux():
    with mock.patch.object(mapping.sys, "platform", "darwin"):
        report = mapping.glibc_allocator_diagnostics()
    assert report["supported"] is False
    assert report["systemBytes"] is None


def test_syscall_trace_summary_links_create_unmap_and_redacts_paths(tmp_path):
    trace = tmp_path / "mmap-trace.17"
    trace.write_text(
        "1.0 mmap(NULL, 4096, PROT_READ|PROT_WRITE, "
        "MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x1000\n"
        "2.0 mmap(NULL, 8192, PROT_READ, MAP_PRIVATE, "
        "3</work/private/checkpoint-v2.sqlite>, 0) = 0x2000\n"
        "3.0 munmap(0x1000, 4096) = 0\n")
    report = summarize([trace])
    assert report["mappingsCreated"] == 2
    assert report["mappingsUnmapped"] == 1
    assert report["persistentMappings"] == 1
    assert "/work/private" not in json.dumps(report)


def _passing_precise_report():
    zero_gate = {
        "activeGenerationFileMappings": 0,
        "retainedGenerationFileMappings": 0,
        "v2TempMappings": 0,
        "deletedMappings": 0,
        "incidentTempMappings": 0,
        "unknownMappings": 0,
        "allocatorAnonymousBytes": 200 * 1024 ** 2,
        "allocatorArenaMappings": 2,
        "allocatorLargeMmapMappings": 71,
    }
    reachability = {
        "sqliteConnections": 0, "sqliteCursors": 0, "futures": 0,
        "generationContexts": 0, "telemetryRawPayloadOwners": 0,
        "threads": 1, "descriptors": 4, "largeTrackedBytes": 0,
        "largeTrackedContainers": 0, "memoryviews": 0, "tracebacks": 0,
        "manifestCandidates": 0, "verificationObjects": 0,
    }
    band = {"minimum": 38, "maximum": 71, "first": 38, "last": 68,
            "growth": 30, "strictlyMonotonic": False}
    flat = {"minimum": 62, "maximum": 62, "first": 62, "last": 62,
            "growth": 0, "strictlyMonotonic": False}
    return {
        "cycles": 32, "allVerified": True, "finalRestoreVerified": True,
        "allConsumed": True, "allGenerationContextsReleased": True,
        "finalGate": zero_gate, "preciseMappingEnvelope": dict(
            PRECISE_MAPPING_ENVELOPE), "cgroupPeakBytes": 1400467456,
        "diskFreeBytes": 90 * 1024 ** 3, "pendingGenerations": 0,
        "retainedGenerations": 4, "rssGrowthBytes": 120922112,
        "pssGrowthBytes": 120922112, "anonymousGrowthBytes": 120938496,
        "minimumSteadyMappingCount": 275, "maximumSteadyMappingCount": 308,
        "allocatorSystemSamples": [12746752, 14667776],
        "allocatorSystemGrowthBytes": 1921024,
        "allocatorInUseSamples": [6295904, 7505040],
        "allocatorFreeRetainedSamples": [3198624, 34356080],
        "sourceGenerationBytes": 10698752,
        "sourceSerializedBytes": 10698752,
        "sourceSectionCount": 50, "sourceRowCount": 54,
        "categoryBands": {
            "allocator large-object mmap": {
                "mappingCount": band,
                "virtualBytes": {**band, "strictlyMonotonic": False},
                "anonymousResidentBytes": {
                    **band, "strictlyMonotonic": False},
            },
            "shared library": {"mappingCount": flat,
                               "virtualBytes": flat,
                               "anonymousResidentBytes": flat},
        },
        "plateauWindow": {
            "rssBytes": {"span": 9445376},
            "pssBytes": {"span": 9445376},
            "allocatorAnonymousBytes": {"span": 10448896},
            "mappingCount": {"span": 3},
            "allocatorLargeMmapMappings": {"span": 3},
        },
        "baselineReachability": dict(reachability),
        "finalReachability": dict(reachability),
    }


def test_precise_gate_accepts_observed_bounded_allocator_envelope():
    assert precise_gate_failures(_passing_precise_report()) == []


def test_precise_gate_rejects_generation_mapping_and_allocator_escape():
    report = _passing_precise_report()
    report["finalGate"]["activeGenerationFileMappings"] = 1
    report["finalGate"]["allocatorAnonymousBytes"] = 257 * 1024 ** 2
    failures = precise_gate_failures(report)
    assert "mapping_proof_activeGenerationFileMappings_nonzero" in failures
    assert "mapping_proof_allocator_anonymous_bytes_exceeded" in failures


def test_precise_gate_bounds_in_use_bytes_and_source_relative_system_bytes():
    """v13.5.64: the normal-use production snapshot (10.7 MB of small nested
    objects) leaves 34 MB of free-but-unreturned glibc chunks after every
    cycle. That is not application retention: in-use stays near baseline and
    the system bytes do not grow. The gate reads both, against the source."""
    report = _passing_precise_report()
    # measured 2026-09-07 (run 34167048143 attempt 2): system 41,861,120 B
    report["allocatorSystemSamples"] = [39936000, 41861120]
    assert precise_gate_failures(report) == []
    # the old absolute rule would have refused exactly this report
    report["allocatorInUseSamples"] = []
    assert "mapping_proof_allocator_system_bytes_exceeded" in \
        precise_gate_failures(report)
    # in-use retention above 32 MiB is still a failure
    report = _passing_precise_report()
    report["allocatorInUseSamples"] = [6295904, 33 * 1024 ** 2]
    assert "mapping_proof_allocator_in_use_bytes_exceeded" in \
        precise_gate_failures(report)
    # and system bytes far beyond what the source explains are still refused
    report = _passing_precise_report()
    report["allocatorSystemSamples"] = [39936000, 32 * 1024 ** 2 + 4 * 10698752 + 1]
    assert "mapping_proof_allocator_system_bytes_exceeded" in \
        precise_gate_failures(report)


def test_exact_public_snapshot_is_fetched_once_then_shared_by_artifact():
    workflow = pathlib.Path(
        ".github/workflows/checkpoint-v2-gate.yml").read_text(
            encoding="utf-8")
    endpoint = (
        "https://argus-backend-3j2m.onrender.com/"
        "api/argus/osint/memory-snapshot")
    assert workflow.count(endpoint) == 1
    assert "exact-public-state:" in workflow
    assert workflow.count("needs: exact-public-state") == 5
    assert workflow.count("actions/download-artifact@v5") == 5
    assert "mkdir -p artifacts/mapping-attribution" in workflow
