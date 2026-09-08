#!/usr/bin/env python3
"""Weekly JPX two-market margin balance (信用取引現在高) → Market Ledger rows.

v13.5.65 (stabilization item 5). The committed CSV
`ops/imports/jpx_two_market_credit_20020802_20260710.csv` ends on 2026-07-10;
later weeks were meant to arrive through `/api/argus/admin/market-ledger/import`
but nothing produced them, so the conditioning engine's 45-day window dropped
the credit features (信用倍率 / 売り残高) from every forecast after August 24.

This tool fetches the official weekly workbook for each Friday after `--since`
(`https://www.jpx.co.jp/markets/statistics-equities/margin/tvdivq0000001rk9-att/
mtseisanYYYYMMDD00.xls`), reads the two-market TOTAL value columns (委託+自己,
金額, 百万円 → JPY) exactly as the committed CSV does, and emits rows in the
ledger's CSV contract. With `--import` it posts them to the admin import route
(dry run, then commit) and reads the ledger back. Weeks whose workbook is not
published (404) are reported as gaps, never filled.

Availability is conservative: a week ending Friday is stamped as available on
the following Wednesday 15:00 JST (JPX publishes Tuesday/Wednesday).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

URL_TEMPLATE = ("https://www.jpx.co.jp/markets/statistics-equities/margin/"
                "tvdivq0000001rk9-att/mtseisan{ymd}00.xls")
CSV_COLUMNS = ("seriesId", "periodEnd", "publishedAt", "availableFrom",
               "observedAt", "value", "unit", "source", "sourceKind", "status")
SERIES = {"short": "credit.short_balance", "long": "credit.long_balance"}
MILLION = 1_000_000
TITLE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")


def fridays_after(since: str, today: Optional[date] = None) -> List[str]:
    start = date.fromisoformat(since)
    end = today or date.today()
    out: List[str] = []
    day = start + timedelta(days=1)
    while day <= end:
        if day.weekday() == 4:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def parse_sheet(grid: Sequence[Sequence[Any]]) -> Dict[str, Any]:
    """Pure: the two-market TOTAL value row of the JPX weekly layout.

    grid[r][c] are cell values. The title row carries the period date; the
    header row containing 「合計」 fixes the column of the total block whose
    first two value columns are 売残高 (sales) and 買残高 (purchases) with a
    weekly-change column between them; the 二市場計 block has a 株数 row and
    a 金額 row (百万円)."""
    text = [[str(cell if cell is not None else "") for cell in row] for row in grid]
    period = None
    for row in text[:3]:
        for cell in row:
            match = TITLE_RE.search(cell)
            if match:
                period = date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
                break
        if period:
            break
    if not period:
        raise ValueError("jpx_period_not_found")
    total_col = None
    for row in text:
        for index, cell in enumerate(row):
            if "合" in cell and "計" in cell and "Total" in cell:
                total_col = index
                break
        if total_col is not None:
            break
    if total_col is None:
        raise ValueError("jpx_total_column_not_found")
    value_row = None
    for index, row in enumerate(text):
        if any("二市場計" in cell for cell in row):
            for candidate in range(index, min(index + 3, len(text))):
                if any("金額" in cell for cell in text[candidate]):
                    value_row = candidate
                    break
            break
    if value_row is None:
        raise ValueError("jpx_value_row_not_found")

    def number(cell: Any) -> float:
        value = float(str(cell).replace(",", "").replace("▲", "-").strip())
        if value <= 0:
            raise ValueError("jpx_non_positive_value")
        return value

    short_million = number(grid[value_row][total_col])
    long_million = number(grid[value_row][total_col + 2])
    return {"periodEnd": period,
            "shortJpy": int(round(short_million * MILLION)),
            "longJpy": int(round(long_million * MILLION))}


def load_workbook_grid(payload: bytes) -> List[List[Any]]:
    import xlrd  # requirements.txt

    if payload[:5] == b"<!DOC" or payload[:5] == b"<html":
        raise ValueError("jpx_not_a_workbook")
    book = xlrd.open_workbook(file_contents=payload)
    sheet = book.sheet_by_index(0)
    return [[sheet.cell_value(r, c) for c in range(sheet.ncols)]
            for r in range(sheet.nrows)]


def build_rows(parsed: Dict[str, Any], *, url: str, sha256: str,
               observed_at: str) -> List[Dict[str, Any]]:
    period = date.fromisoformat(parsed["periodEnd"])
    published = period + timedelta(days=(2 - period.weekday()) % 7 or 7)   # next Wednesday
    stamp = published.isoformat() + "T15:00:00+09:00"
    source = (f"JPX official | {url} | sha256={sha256} | "
              "publication=weekly_final")
    return [{
        "seriesId": SERIES[key], "periodEnd": parsed["periodEnd"],
        "publishedAt": stamp, "availableFrom": stamp, "observedAt": observed_at,
        "value": parsed["shortJpy"] if key == "short" else parsed["longJpy"],
        "unit": "JPY", "source": source, "sourceKind": "official", "status": "live",
    } for key in ("short", "long")]


def rows_to_csv(rows: Iterable[Dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in CSV_COLUMNS})
    return buffer.getvalue()


def fetch(url: str, timeout: int = 60) -> Optional[bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ARGUS jpx-credit-weekly)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def collect(since: str, *, today: Optional[date] = None,
            fetcher=fetch, now_iso: Optional[str] = None) -> Dict[str, Any]:
    observed = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows: List[Dict[str, Any]] = []
    gaps: List[str] = []
    fetched: List[str] = []
    for friday in fridays_after(since, today):
        url = URL_TEMPLATE.format(ymd=friday.replace("-", ""))
        payload = fetcher(url)
        if payload is None:
            gaps.append(friday)
            continue
        try:
            parsed = parse_sheet(load_workbook_grid(payload))
        except ValueError as error:
            gaps.append(f"{friday}:{error}")
            continue
        if parsed["periodEnd"] != friday:
            gaps.append(f"{friday}:period_mismatch:{parsed['periodEnd']}")
            continue
        rows.extend(build_rows(parsed, url=url, sha256=hashlib.sha256(payload).hexdigest(),
                               observed_at=observed))
        fetched.append(friday)
    return {"since": since, "fetched": fetched, "gaps": gaps, "rows": rows,
            "csv": rows_to_csv(rows), "observedAt": observed}


def post_json(url: str, body: Dict[str, Any], token: str, timeout: int = 600) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json", "X-ARGUS-ADMIN-TOKEN": token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def import_rows(csv_text: str, *, backend: str, token: str,
                expected_newest: Optional[str] = None) -> Dict[str, Any]:
    """Dry run, commit, read back. The commit rebuilds the ledger and can
    outlive the proxy's response window (2026-09-08: the rows landed but the
    client saw a transport error) — so a transport error on the commit is
    settled by the read-back: the import is a success when the ledger now
    holds the newest period we sent, and a failure otherwise."""
    endpoint = backend.rstrip("/") + "/api/argus/admin/market-ledger/import"
    dry = post_json(endpoint, {"csv": csv_text, "dryRun": True}, token)
    if not dry.get("ok") or dry.get("errors"):
        return {"ok": False, "stage": "dry_run", "errors": (dry.get("errors") or [])[:5]}
    try:
        commit = post_json(endpoint, {"csv": csv_text, "dryRun": False}, token)
    except Exception as error:                     # transport only; verified below
        commit = {"ok": None, "transportError": f"{type(error).__name__}: {str(error)[:120]}"}
    if commit.get("ok") is False or commit.get("errors"):
        return {"ok": False, "stage": "commit", "errors": (commit.get("errors") or [])[:5]}
    readback = ledger_newest_credit(backend)
    held = min((str(v.get("periodEnd") or "") for v in readback.values()), default="")
    if expected_newest and held < expected_newest:
        return {"ok": False, "stage": "readback", "expectedNewest": expected_newest,
                "ledger": readback, "transportError": commit.get("transportError")}
    return {"ok": True, "importId": commit.get("importId"),
            "rowCount": len(commit.get("preview") or []),
            "settledByReadback": commit.get("ok") is None,
            "transportError": commit.get("transportError"), "ledger": readback}


def ledger_newest_credit(backend: str) -> Dict[str, Any]:
    with urllib.request.urlopen(backend.rstrip("/") + "/api/argus/market-ledger", timeout=180) as response:
        doc = json.loads(response.read().decode("utf-8"))
    out: Dict[str, Any] = {}
    for row in doc.get("table") or []:
        if row.get("seriesId") in SERIES.values():
            out[row["seriesId"]] = {"periodEnd": row.get("periodEnd"),
                                    "latestValue": row.get("latestValue")}
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--since", default="2026-07-10", help="last period already held (exclusive)")
    parser.add_argument("--out", default=None, help="write the CSV rows here")
    parser.add_argument("--import", dest="do_import", action="store_true")
    parser.add_argument("--backend", default="https://argus-backend-3j2m.onrender.com")
    parser.add_argument("--token-env", default="ARGUS_ADMIN_TOKEN")
    parser.add_argument("--summary", default=None, help="write a JSON summary here")
    args = parser.parse_args(argv)
    result = collect(args.since)
    summary: Dict[str, Any] = {"since": args.since, "fetched": result["fetched"],
                               "gaps": result["gaps"], "rowCount": len(result["rows"]),
                               "newestPeriod": (result["fetched"] or [None])[-1]}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(result["csv"])
    if args.do_import and result["rows"]:
        token = os.environ.get(args.token_env, "")
        if not token:
            summary["import"] = {"ok": False, "stage": "token_missing"}
        else:
            summary["import"] = import_rows(result["csv"], backend=args.backend, token=token,
                                            expected_newest=(result["fetched"] or [None])[-1])
            summary["ledger"] = (summary["import"] or {}).get("ledger") or ledger_newest_credit(args.backend)
    if args.summary:
        with open(args.summary, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))
    if args.do_import and result["rows"] and not (summary.get("import") or {}).get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
