#!/usr/bin/env python3
"""Plain-language summary of the owner-only runtime snapshot (v13.5.61).

Input: the JSON returned by /api/argus/admin/memory-attribution?threads=1.
Output: Markdown for the GitHub run page. Locations and counters only — the
document never carries values, environment, or owner data, and this script
adds nothing of its own beyond formatting.
"""
from __future__ import annotations

import json
import sys
from typing import Any


def _frames(thread: dict, limit: int) -> str:
    rows = thread.get("frames") or []
    tail = rows[-limit:] if rows else []
    if not tail:
        return "(no Python frame)"
    return " ← ".join(f"{f.get('file')}:{f.get('line')} {f.get('function')}" for f in reversed(tail))


def summarize(doc: dict[str, Any]) -> str:
    out: list[str] = ["# ARGUS runtime diagnostics", ""]
    threads = doc.get("runtimeThreads") or {}
    out.append(f"- generatedAt: `{threads.get('generatedAt')}`")
    out.append(f"- threads alive: **{threads.get('threadCount')}**")
    out.append(f"- decision-evidence cache size: {threads.get('decisionEvidenceCacheSize')}")
    flight = threads.get("singleFlight") or []
    out.append(f"- single-flight in progress: {len(flight)}"
               + (" — " + ", ".join(f"`{row.get('key')}` {row.get('ageSec')}s" for row in flight) if flight else ""))
    out.append("")
    out.append("## Threads (innermost frame first)")
    out.append("")
    rows = threads.get("threads") or []
    request_threads = [t for t in rows if str(t.get("name", "")).startswith("Thread-")]
    named = [t for t in rows if not str(t.get("name", "")).startswith("Thread-")]
    out.append(f"request threads: {len(request_threads)} · named threads: {len(named)}")
    out.append("")
    for thread in named + request_threads[:24]:
        out.append(f"- **{thread.get('name')}**"
                   f"{' (daemon)' if thread.get('daemon') else ''}: {_frames(thread, 4)}")
    if len(request_threads) > 24:
        out.append(f"- … {len(request_threads) - 24} more request threads (see artifact)")
    out.append("")
    out.append("## Memory attribution (scalar history)")
    out.append("")
    for key in ("historyCount", "completedCount", "droppedCount", "activeCount", "historyLimit"):
        if key in doc:
            out.append(f"- {key}: {doc.get(key)}")
    operations = doc.get("operationAttribution") or {}
    for key in ("observedCount", "qualifiedCount", "droppedCount", "thresholdBytes"):
        if key in operations:
            out.append(f"- operations.{key}: {operations.get(key)}")
    active = doc.get("active") or []
    if active:
        out.append(f"- active phases: {len(active)}")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: runtime_diagnostics_summary.py <snapshot.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        print("snapshot is not an object", file=sys.stderr)
        return 1
    sys.stdout.write(summarize(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
