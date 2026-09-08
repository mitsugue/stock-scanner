"""v13.5.65 — weekly JPX two-market credit rows for the Market Ledger."""
import csv
import io
from datetime import date

import scripts.jpx_credit_weekly as jw

GRID = [
    ["信用取引現在高（2026/8/28現在）"] + [""] * 14,
    [""] * 15, [""] * 15, [""] * 15,
    ["", "", "", "委　　託 Customer", "", "", "", "自　　己 Proprietary", "", "", "", "合　　計 Total", "", "", ""],
    ["", "", "", "売残高\nSales", "前週比", "買残高\nPurchases", "前週比", "売残高", "前週比", "買残高", "前週比", "売残高\nSales", "前週比", "買残高\nPurchases", "前週比"],
    ["", "二市場計\nTotal", "株数Shs.", 352942.0, -40978.0, 3478719.0, -25502.0, 108556.0, -41.0, 1267.0, -82.0, 461498.0, -41019.0, 3479986.0, -25584.0],
    ["", "", "金額Val.", 669333.0, -75990.0, 6526845.0, 46607.0, 155713.0, 4855.0, 2518.0, 213.0, 825046.0, -71135.0, 6529363.0, 46820.0],
]


def test_parse_reads_the_two_market_total_value_columns_in_yen():
    parsed = jw.parse_sheet(GRID)
    assert parsed == {"periodEnd": "2026-08-28", "shortJpy": 825_046_000_000,
                      "longJpy": 6_529_363_000_000}


def test_rows_follow_the_ledger_csv_contract_with_wednesday_availability():
    rows = jw.build_rows(jw.parse_sheet(GRID), url="https://example.test/x.xls",
                         sha256="ab" * 32, observed_at="2026-09-08T00:00:00Z")
    assert [r["seriesId"] for r in rows] == ["credit.short_balance", "credit.long_balance"]
    assert rows[0]["periodEnd"] == "2026-08-28"
    assert rows[0]["availableFrom"] == rows[0]["publishedAt"] == "2026-09-02T15:00:00+09:00"
    assert rows[0]["value"] == 825_046_000_000 and rows[1]["value"] == 6_529_363_000_000
    assert rows[0]["unit"] == "JPY" and rows[0]["sourceKind"] == "official" and rows[0]["status"] == "live"
    assert "sha256=" + "ab" * 32 in rows[0]["source"] and "publication=weekly_final" in rows[0]["source"]
    text = jw.rows_to_csv(rows)
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert list(parsed[0].keys()) == list(jw.CSV_COLUMNS)


def test_fridays_after_and_gaps_are_reported_not_filled():
    assert jw.fridays_after("2026-07-10", today=date(2026, 7, 31)) == ["2026-07-17", "2026-07-24", "2026-07-31"]
    served = {"2026-07-31"}

    def fetcher(url):
        for day in served:
            if day.replace("-", "") in url:
                return b"workbook"
        return None
    grid_by_url = {}

    def loader(payload):
        return GRID_0731
    GRID_0731 = [row[:] for row in GRID]
    GRID_0731[0][0] = "信用取引現在高（2026/7/31現在）"
    original = jw.load_workbook_grid
    jw.load_workbook_grid = loader
    try:
        result = jw.collect("2026-07-10", today=date(2026, 7, 31), fetcher=fetcher,
                            now_iso="2026-09-08T00:00:00Z")
    finally:
        jw.load_workbook_grid = original
    assert result["fetched"] == ["2026-07-31"]
    assert result["gaps"] == ["2026-07-17", "2026-07-24"]
    assert len(result["rows"]) == 2 and result["rows"][0]["periodEnd"] == "2026-07-31"


def test_parse_refuses_a_workbook_without_the_total_block():
    broken = [row[:] for row in GRID]
    broken[4] = [""] * 15
    try:
        jw.parse_sheet(broken)
    except ValueError as error:
        assert str(error) == "jpx_total_column_not_found"
    else:
        raise AssertionError("expected jpx_total_column_not_found")


def test_a_transport_error_on_commit_is_settled_by_the_ledger_read_back(monkeypatch):
    calls = []

    def fake_post(url, body, token, timeout=600):
        calls.append(body["dryRun"])
        if body["dryRun"]:
            return {"ok": True, "errors": [], "preview": [1, 2]}
        raise TimeoutError("proxy closed the connection")
    monkeypatch.setattr(jw, "post_json", fake_post)
    monkeypatch.setattr(jw, "ledger_newest_credit",
                        lambda backend: {"credit.short_balance": {"periodEnd": "2026-08-28"},
                                         "credit.long_balance": {"periodEnd": "2026-08-28"}})
    result = jw.import_rows("csv", backend="https://x", token="t", expected_newest="2026-08-28")
    assert result["ok"] is True and result["settledByReadback"] is True
    assert "TimeoutError" in result["transportError"]
    # …but not when the ledger does not hold what we sent
    monkeypatch.setattr(jw, "ledger_newest_credit",
                        lambda backend: {"credit.short_balance": {"periodEnd": "2026-07-10"},
                                         "credit.long_balance": {"periodEnd": "2026-07-10"}})
    result = jw.import_rows("csv", backend="https://x", token="t", expected_newest="2026-08-28")
    assert result["ok"] is False and result["stage"] == "readback"
