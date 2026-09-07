"""ARGUS V11.3.2 — macro-event pre/post analysis tests (pure; explicit frozen times).

The release-day bug: daysUntil<=0 must NOT mean post. NFP 2026-07-02 08:30 ET
(=12:30 UTC, EDT) is PRE at 08:29 and released_pending_result at 08:31 without an
official result. Pre views are preserved; blanks never overwrite; missing pre/actual
force not_scoreable.
"""
import json
import argus_macro_event_analysis as MA
import argus_macro_event_store as MS

NFP_UTC = "2026-07-02T12:30:00Z"          # 08:30 ET (EDT)
EVENT = {"id": "ev-nfp-20260702", "eventCode": "NFP", "title": "US Employment Situation",
         "eventTimeUtc": NFP_UTC, "eventDate": "2026-07-02", "source": "BLS",
         "linkedAssets": ["SPY", "QQQ"]}
NOW = "2026-07-02T10:00:00Z"


# ── phase resolution (the core bug) ──────────────────────────────────────────
def test_nfp_0829_et_is_pre_not_post():
    ph = MA.resolve_macro_event_phase(NFP_UTC, "2026-07-02T12:29:00Z")
    assert ph == "imminent"                     # release day BEFORE release = still PRE
    assert MA.is_pre_phase(ph)


def test_nfp_0831_et_without_result_is_released_pending():
    ph = MA.resolve_macro_event_phase(NFP_UTC, "2026-07-02T12:31:00Z", actual_available=False)
    assert ph == "released_pending_result"


def test_nfp_0831_et_with_result_is_post_result():
    ph = MA.resolve_macro_event_phase(NFP_UTC, "2026-07-02T12:31:00Z", actual_available=True)
    assert ph == "post_result"


def test_phase_ladder():
    assert MA.resolve_macro_event_phase(NFP_UTC, "2026-06-25T12:00:00Z") == "pre_early"
    assert MA.resolve_macro_event_phase(NFP_UTC, "2026-06-30T12:00:00Z") == "pre_watch"
    assert MA.resolve_macro_event_phase(NFP_UTC, "2026-07-01T14:00:00Z") == "pre_final"  # <24h
    assert MA.resolve_macro_event_phase(NFP_UTC, "2026-07-02T04:00:00Z") == "pre_final"
    assert MA.resolve_macro_event_phase(NFP_UTC, "2026-07-02T09:00:00Z") == "imminent"


def test_date_only_event_never_post_on_the_day():
    # no time-of-day → the DATE alone can never mark post while the day is running
    ph = MA.resolve_macro_event_phase(None, "2026-07-02T23:00:00Z", event_date="2026-07-02")
    assert ph == "imminent"
    ph2 = MA.resolve_macro_event_phase(None, "2026-07-03T01:00:00Z", event_date="2026-07-02")
    assert ph2 == "released_pending_result"


# ── pre refresh gating + parsing ─────────────────────────────────────────────
def test_should_refresh_pre_on_missing_then_checkpoint():
    rec = MA.new_record(EVENT, now_iso=NOW)
    assert MA.should_refresh_pre(rec, "pre_watch", now_iso=NOW) is True     # no pre yet
    rec["pre"] = MA.parse_pre({"summaryJa": "雇用統計", "argusScenarioJa": "強ければ金利上"},
                              phase="pre_watch", now_iso=NOW)
    assert MA.should_refresh_pre(rec, "pre_watch", now_iso=NOW) is False    # fresh, same phase
    assert MA.should_refresh_pre(rec, "imminent", now_iso=NOW) is True      # checkpoint advanced


def test_parse_pre_rejects_blank():
    assert MA.parse_pre({"summaryJa": "", "argusScenarioJa": ""}, phase="imminent", now_iso=NOW) is None
    assert MA.parse_pre("not a dict", phase="imminent", now_iso=NOW) is None


def test_legacy_shape_salvage_and_regen():
    # prod bug (v11.3.2): default C.A.O.S. system prompt pinned the OLD
    # {summaryJa, preJa, postJa} schema — the whole pre view landed in preJa.
    p = MA.parse_pre({"summaryJa": "重要", "preJa": "強ければ金利上・株安方向", "postJa": ""},
                     phase="pre_final", now_iso=NOW)
    assert p["argusScenarioJa"] == "強ければ金利上・株安方向"
    po = MA.parse_post({"verdict": "hit", "postJa": "概ね想定通り"}, now_iso=NOW,
                       pre_exists=True, actual_available=True)
    assert po["answerCheckJa"] == "概ね想定通り"
    # a summary-only pre (incomplete) must be regenerated, not treated as done
    rec = MA.new_record(EVENT, now_iso=NOW)
    rec["pre"] = MA.parse_pre({"summaryJa": "概要だけ"}, phase="pre_final", now_iso=NOW)
    assert MA.should_refresh_pre(rec, "pre_final", now_iso=NOW) is True
    assert "MACRO_EVENT_SYSTEM_JA" in dir(MA) and "指定されたもの" in MA.MACRO_EVENT_SYSTEM_JA


def test_parse_post_gates_verdict():
    # model claims hit, but no pre → forced not_scoreable
    p = MA.parse_post({"verdict": "hit", "answerCheckJa": "当たり"}, now_iso=NOW,
                      pre_exists=False, actual_available=True)
    assert p["verdict"] == "not_scoreable"
    assert any("事前予想が保存されていない" in x for x in p["limitationsJa"])
    # no actual → not_scoreable even with pre
    p2 = MA.parse_post({"verdict": "hit"}, now_iso=NOW, pre_exists=True, actual_available=False)
    assert p2["verdict"] == "not_scoreable"
    # both present → verdict respected
    p3 = MA.parse_post({"verdict": "partial", "answerCheckJa": "部分的"}, now_iso=NOW,
                       pre_exists=True, actual_available=True)
    assert p3["verdict"] == "partial"


def test_prompts_carry_no_fabrication_rules():
    pre_p = MA.build_pre_prompt(EVENT, "実測: 金利上昇")
    post_p = MA.build_post_prompt(EVENT, {"argusScenarioJa": "x"}, {"available": False}, "")
    for p in (pre_p, post_p):
        assert "捏造しない" in p and "STRICT JSON" in p
    assert "not_scoreable" in post_p and "公式結果未取得" in post_p


# ── durable store merge ──────────────────────────────────────────────────────
def _rec_with_pre(gen_at="2026-07-02T08:00:00Z", phase="pre_final"):
    r = MA.new_record(EVENT, now_iso=NOW)
    r["phase"] = phase
    r["pre"] = MA.parse_pre({"summaryJa": "重要", "argusScenarioJa": "強ければ金利上・株安方向"},
                            phase=phase, now_iso=gen_at)
    r["updatedAt"] = gen_at
    return r


def test_blank_pre_never_overwrites_real_pre():
    real = _rec_with_pre()
    blank = MA.new_record(EVENT, now_iso="2026-07-02T13:00:00Z")
    blank["updatedAt"] = "2026-07-02T13:00:00Z"
    merged = MS.merge_record(real, blank, now_iso="2026-07-02T13:01:00Z")
    assert merged["pre"]["argusScenarioJa"] == "強ければ金利上・株安方向"


def test_pre_is_frozen_after_release():
    old = _rec_with_pre(gen_at="2026-07-02T08:00:00Z")
    late = _rec_with_pre(gen_at="2026-07-02T14:00:00Z", phase="released_pending_result")
    late["pre"]["argusScenarioJa"] = "発表後に書き換えられた予想"
    merged = MS.merge_record(old, late, now_iso="2026-07-02T14:01:00Z")
    # post-release regeneration must NOT replace the pre-release view
    assert merged["pre"]["argusScenarioJa"] == "強ければ金利上・株安方向"


def test_actual_availability_never_regresses():
    with_actual = _rec_with_pre()
    with_actual["actual"] = {"available": True, "source": "BLS", "headline": "NFP +150K",
                             "metrics": {"nfpChangeK": 150}, "releasedAt": "2026-07-02T12:35:00Z",
                             "limitationsJa": []}
    with_actual["updatedAt"] = "2026-07-02T12:35:00Z"
    without = _rec_with_pre(gen_at="2026-07-02T13:00:00Z")
    merged = MS.merge_record(with_actual, without, now_iso="2026-07-02T13:01:00Z")
    assert merged["actual"]["available"] is True


def test_snapshot_deterministic_and_safe():
    recs = [_rec_with_pre(), MA.new_record({**EVENT, "id": "ev-cpi", "eventCode": "CPI"}, now_iso=NOW)]
    a = MS.serialize_snapshot(recs, as_of=NOW)
    b = MS.serialize_snapshot(list(reversed(recs)), as_of=NOW)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["summary"]["withPre"] == 1
    dirty = {**_rec_with_pre(), "apiKey": "sk-x", "holdings": 5, "fullText": "本文"}
    blob = json.dumps(MS.serialize_snapshot([dirty], as_of=NOW), ensure_ascii=False).lower()
    for bad in ("apikey", "holdings", "fulltext", "sk-x"):
        assert bad not in blob


def test_restore_round_trip():
    recs = [_rec_with_pre()]
    snap = MS.serialize_snapshot(recs, as_of=NOW)
    back = MS.restore_from_snapshot(snap)
    assert recs[0]["eventId"] in back
    assert back[recs[0]["eventId"]]["pre"]["argusScenarioJa"]


# ── scanner integration (no LLM / no result-fetch on public GET) ─────────────
import scanner


def _seed_store():
    scanner._MACRO_ANALYSIS.clear()
    scanner._MACRO_ANALYSIS_STATE["restored"] = True
    r = _rec_with_pre()
    scanner._MACRO_ANALYSIS[r["eventId"]] = r


def test_public_macro_gets_never_call_llm_or_result_fetch(monkeypatch):
    _seed_store()
    def boom(*a, **k):
        raise AssertionError("FORBIDDEN call from public macro GET")
    for name in ("_openai_prose", "_bls_nfp_result", "_macro_result_fetch",
                 "_fetch_public_text", "_openai_judge", "_gemini_check"):
        monkeypatch.setattr(scanner, name, boom)
    with scanner.app.test_client() as c:
        assert c.get("/api/argus/macro-event-analysis").status_code == 200


def test_admin_macro_endpoints_require_token():
    with scanner.app.test_client() as c:
        r1 = c.post("/api/argus/admin/macro-event-analysis/generate")
        r2 = c.post("/api/argus/admin/macro-event-analysis/refresh-results")
    assert r1.status_code in (401, 503) and r2.status_code in (401, 503)


def test_macro_items_shape_and_no_secrets():
    _seed_store()
    with scanner.app.test_client() as c:
        d = c.get("/api/argus/macro-event-analysis").get_json()
    it = d["items"][0]
    for k in ("schemaVersion", "eventId", "eventCode", "phase", "pre", "actual"):
        assert k in it, k
    assert isinstance(it["actual"].get("available"), bool)
    blob = json.dumps(d).lower()
    for bad in ("apikey", "x-api-key", "holdings", "costbasis", "netr"):
        assert bad not in blob, bad


# ── v13.5.63 (GPT review item 6): the model that answered is recorded ────────
def test_pre_and_post_record_requested_and_returned_model_without_prompts():
    meta = {"requestedModel": "gpt-6-astra", "returnedModel": "gpt-6-astra-2026-08-01",
            "completedAt": "2026-09-07T12:35:10Z", "inputTokens": 1180, "outputTokens": 640,
            "estUsd": 0.0438, "prompt": "MUST NOT LEAK", "apiKey": "sk-xxx"}
    pre = MA.parse_pre({"summaryJa": "概要", "argusScenarioJa": "予想"}, phase="pre_watch",
                       now_iso=NOW, ai_meta=meta)
    assert pre["ai"] == {"requestedModel": "gpt-6-astra", "returnedModel": "gpt-6-astra-2026-08-01",
                         "completedAt": "2026-09-07T12:35:10Z", "inputTokens": 1180,
                         "outputTokens": 640, "estUsd": 0.0438}
    assert "prompt" not in json.dumps(pre) and "sk-xxx" not in json.dumps(pre)
    post = MA.parse_post({"answerCheckJa": "的中", "verdict": "hit"}, now_iso=NOW,
                         pre_exists=True, actual_available=True,
                         ai_meta={"requestedModel": "gpt-6-astra", "returnedModel": None,
                                  "fallbackModel": "gpt-5.6-terra"})
    assert post["ai"] == {"requestedModel": "gpt-6-astra", "fallbackModel": "gpt-5.6-terra"}
    assert MA.parse_pre({"summaryJa": "概要"}, phase="pre_watch", now_iso=NOW)["ai"] is None
    # the store keeps the ai block (it is not a forbidden key)
    rec = MS.merge_record(None, {"eventId": "e", "pre": pre}, now_iso=NOW)
    assert rec["pre"]["ai"]["returnedModel"] == "gpt-6-astra-2026-08-01"
