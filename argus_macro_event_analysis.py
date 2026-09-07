"""ARGUS V11.3.2 — C.A.O.S. macro-event pre/post intelligence (pure).

Fixes the release-day bug: `daysUntil <= 0` treated NFP as "post" the moment the DATE
arrived, hours before the 08:30 ET release. The canonical phase resolver uses the real
eventTimeUtc, and the pre-event view is PRESERVED so the post-event answer-check can
compare against what ARGUS actually said beforehand.

Pure: no network, no LLM. Prompt builders return strings; the scanner owns the calls.
Discipline: never fabricate an official result or a consensus; a missing result is
"unavailable"; a missing pre makes the post verdict not_scoreable.
"""
import argus_fastdate  # v13.5.52: lock-free strptime (no _strptime._cache_lock)
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "macro-event-analysis-v1"

TRACKED_CODES = {"NFP", "CPI", "FOMC", "BOJ", "PCE", "GDP", "JOLTS", "PPI",
                 "TREASURY_AUCTION", "AUCTION", "ISM", "RETAIL_SALES"}

PHASES = ["pre_early", "pre_watch", "pre_final", "imminent",
          "released_pending_result", "post_result", "post_followup"]
_PRE_PHASES = {"pre_early", "pre_watch", "pre_final", "imminent"}

_IMPACT_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
_CANONICAL_DATE_SUFFIX = re.compile(r"-(\d{4}-\d{2}-\d{2})$")
_CANONICAL_EVENT_TYPES = {
    "FOMC": ("14:00", "central_bank", "Federal Reserve", "high",
             "FOMC Rate Decision", ["USDJPY", "US10Y", "US2Y", "QQQ", "NVDA"]),
    "CPI": ("08:30", "inflation", "Bureau of Labor Statistics", "high",
            "US CPI (Consumer Price Index)", ["US10Y", "USDJPY", "QQQ", "SPY"]),
    "NFP": ("08:30", "jobs", "Bureau of Labor Statistics", "high",
            "US Employment Situation", ["US10Y", "USDJPY", "SPY", "QQQ"]),
    "JOLTS": ("10:00", "jobs", "Bureau of Labor Statistics", "medium",
              "US JOLTS Job Openings", ["US10Y", "USDJPY", "SPY", "QQQ"]),
    "PPI": ("08:30", "inflation", "Bureau of Labor Statistics", "medium",
            "US PPI (Producer Price Index)", ["US10Y", "QQQ"]),
    "PCE": ("08:30", "inflation", "Bureau of Economic Analysis", "high",
            "US PCE / Personal Income & Outlays", ["US10Y", "USDJPY", "QQQ"]),
    "GDP": ("08:30", "growth", "Bureau of Economic Analysis", "high",
            "US GDP", ["US10Y", "SPY", "USDJPY"]),
    "BOJ": (None, "central_bank", "Bank of Japan", "high",
            "BOJ Monetary Policy Meeting", ["USDJPY", "JP10Y", "9984", "8058"]),
    "TREASURY_AUCTION": (None, "rates", "TreasuryDirect", "high",
                         "US Treasury Auction", ["US10Y", "QQQ"]),
    "AUCTION": (None, "rates", "TreasuryDirect", "high",
                "US Treasury Auction", ["US10Y", "QQQ"]),
}


def _parse_utc(iso: Optional[str]) -> Optional[datetime]:
    if not iso or not isinstance(iso, str):
        return None
    s = iso.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def resolve_macro_event_phase(event_time_utc: Optional[str], now_utc: str, *,
                              actual_available: bool = False,
                              event_date: Optional[str] = None) -> str:
    """Canonical phase from the REAL release time. On release day BEFORE the release
    the event is still PRE (imminent) — never post. Without a time-of-day, the date
    alone can never mark post until the date has fully ended (UTC end-of-day)."""
    now = _parse_utc(now_utc)
    dt = _parse_utc(event_time_utc)
    if now is None:
        return "pre_watch"
    if dt is not None:
        if now < dt - timedelta(hours=72):
            return "pre_early"
        if now < dt - timedelta(hours=24):
            return "pre_watch"
        if now < dt - timedelta(hours=6):
            return "pre_final"
        if now < dt:
            return "imminent"
        return "post_result" if actual_available else "released_pending_result"
    if event_date:
        try:
            d = argus_fastdate.strptime(str(event_date)[:10], "%Y-%m-%d").date()
        except ValueError:
            return "pre_watch"
        if now.date() < d:
            return "pre_watch" if (d - now.date()).days > 1 else "pre_final"
        if now.date() == d:
            return "imminent"                      # date not ended → NEVER post
        return "post_result" if actual_available else "released_pending_result"
    return "pre_watch"


def is_pre_phase(phase: str) -> bool:
    return phase in _PRE_PHASES


def canonical_date_from_event_id(event_id: Any) -> Optional[str]:
    """Recover only an ISO date that is already part of a canonical event ID.

    This is identity metadata, not a guessed schedule.  It lets old date-only
    records fail visibly as historical/stale instead of remaining PRE forever.
    """
    match = _CANONICAL_DATE_SUFFIX.search(str(event_id or ""))
    if not match:
        return None
    value = match.group(1)
    try:
        argus_fastdate.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return value


def _catalog_metadata(event_id: str, event_code: Any,
                      event_date: Optional[str]) -> Dict[str, Any]:
    """Canonical type metadata for legacy rows outside the live join horizon."""
    code = str(event_code or "").upper()
    spec = _CANONICAL_EVENT_TYPES.get(code)
    if not spec or not event_date:
        return {}
    et_time, family, source, impact, title, assets = spec
    event_time_utc = local_time_jst = None
    if et_time:
        local_et = argus_fastdate.strptime(
            f"{event_date} {et_time}", "%Y-%m-%d %H:%M").replace(
                tzinfo=ZoneInfo("America/New_York"))
        event_time_utc = local_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        local_time_jst = local_et.astimezone(ZoneInfo("Asia/Tokyo")).strftime(
            "%Y-%m-%d %H:%M JST")
    return {
        "eventId": event_id,
        "eventCode": code,
        "eventFamily": family,
        "title": title,
        "eventTimeUtc": event_time_utc,
        "eventDate": event_date,
        "localTimeJst": local_time_jst,
        "source": source,
        "baseImpact": impact,
        "displayImpact": impact,
        "linkedAssets": assets,
    }


def rehydrate_schedule_metadata(record: Dict[str, Any],
                                schedule_event: Optional[Dict[str, Any]] = None
                                ) -> Dict[str, Any]:
    """Return a record with deterministic ranking metadata restored.

    The official schedule/catalog is authoritative for identity, family, time,
    date, source, and base impact.  Existing stronger owner-specific impact is
    preserved, while a missing/weaker fallback can be raised to the catalog
    impact.  No AI analysis is required and no date is fabricated.
    """
    out = dict(record or {})
    supplied_event = schedule_event or {}
    record_id = str(out.get("eventId") or "")
    schedule_id = str(supplied_event.get("eventId") or supplied_event.get("id") or "")
    if schedule_id and record_id and schedule_id != record_id:
        return out
    event_id = record_id or schedule_id
    if event_id:
        out["eventId"] = event_id

    event_date = (supplied_event.get("eventDate") or supplied_event.get("date")
                  or out.get("eventDate")
                  or canonical_date_from_event_id(event_id))
    event_code = (supplied_event.get("eventCode") or supplied_event.get("kind")
                  or out.get("eventCode"))
    event = _catalog_metadata(event_id, event_code, event_date)
    event.update({key: value for key, value in supplied_event.items()
                  if value is not None})
    if not out.get("eventDate") and event_date:
        out["eventDate"] = event_date

    scalar_sources = {
        "eventCode": event.get("eventCode") or event.get("kind"),
        "eventFamily": (event.get("eventFamily") or event.get("family")
                        or event.get("category")),
        "title": event.get("title"),
        "eventTimeUtc": event.get("eventTimeUtc"),
        "localTimeJst": event.get("localTimeJst") or event.get("jstTime"),
        "source": event.get("source"),
        "baseImpact": event.get("baseImpact") or event.get("impact"),
        "daysUntil": event.get("daysUntil"),
        "countdown": event.get("countdown") or event.get("escalation"),
        "lifecycle": event.get("lifecycle"),
    }
    for key, value in scalar_sources.items():
        if out.get(key) is None and value is not None:
            out[key] = value
    if not out.get("linkedAssets") and event.get("linkedAssets"):
        out["linkedAssets"] = list(event.get("linkedAssets") or [])[:8]

    existing_impact = str(out.get("displayImpact") or "").lower()
    catalog_impact = str(event.get("displayImpact") or event.get("importance")
                         or event.get("impact") or "").lower()
    if (_IMPACT_RANK.get(catalog_impact, 0)
            > _IMPACT_RANK.get(existing_impact, 0)):
        out["displayImpact"] = catalog_impact
    return out


def new_record(event: Dict[str, Any], *, now_iso: str) -> Dict[str, Any]:
    """Skeleton analysis record for one scheduled macro event. Deterministic."""
    eid = str(event.get("id") or event.get("eventId") or event.get("eventCode") or "")
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "analysisId": f"ma-{eid}",
        "eventId": eid,
        "eventCode": event.get("eventCode") or event.get("category") or "",
        "title": (event.get("title") or "")[:120],
        "eventTimeUtc": event.get("eventTimeUtc"),
        "eventDate": event.get("eventDate"),
        "phase": "pre_watch",
        "source": event.get("source"),
        "linkedAssets": list(event.get("linkedAssets") or [])[:8],
        "pre": {},
        "actual": {"available": False, "source": None, "releasedAt": None,
                   "headline": None, "metrics": {}, "limitationsJa": ["公式結果未取得"]},
        "post": {"generatedAt": None, "verdict": "not_available", "answerCheckJa": "",
                 "marketReactionJa": "", "portfolioImpactJa": "", "whatChangedJa": "",
                 "limitationsJa": []},
        "marketReaction": {"us10yMoveBp": None, "usdJpyMovePct": None, "spyMovePct": None,
                           "qqqMovePct": None, "vixMovePct": None, "window": None,
                           "limitationsJa": ["反応ウィンドウ計測は未実装（コメントは実測地合いに基づく）"]},
        "recordRefs": {"eventCardId": None, "evidencePackId": None, "ledgerRef": None},
        "firstSeenAt": now_iso,
        "updatedAt": now_iso,
    }
    return rehydrate_schedule_metadata(record, event)


_PHASE_CHECKPOINT = {"pre_early": 0, "pre_watch": 1, "pre_final": 2, "imminent": 3}


def should_refresh_pre(record: Dict[str, Any], phase: str, *, now_iso: str,
                       ttl_hours: float = 6.0) -> bool:
    """Refresh the pre view only when: no pre yet, the phase checkpoint advanced, or
    the existing pre is older than the TTL. Keeps LLM cost bounded."""
    if not is_pre_phase(phase):
        return False
    pre = record.get("pre") or {}
    if not pre.get("argusScenarioJa"):
        return True    # no scenario = missing or incomplete (e.g. summary-only legacy salvage)
    if _PHASE_CHECKPOINT.get(phase, 0) > _PHASE_CHECKPOINT.get(pre.get("phaseAtGeneration"), -1):
        return True
    gen, now = _parse_utc(pre.get("generatedAt")), _parse_utc(now_iso)
    if gen and now and (now - gen) > timedelta(hours=ttl_hours):
        return True
    return False


# ── prompt builders (pure strings; the scanner owns the LLM call) ────────────
_NO_FABRICATION = (
    "禁止事項: 公式結果を捏造しない。コンセンサス数値を捏造しない（出典付きコンセンサスが与えられて"
    "いない限り数値予想を作らない — 定性シナリオのみ）。機関投資家が売買したと断定しない（公開見解は"
    "見解であって売買ではない）。実データが無い項目は『未取得』と明記する。STRICT JSONのみを返す。")

# System prompt for macro pre/post calls. The scanner's default C.A.O.S. system
# prompt pins an OLD 3-key schema ("exactly these keys: summaryJa/preJa/postJa")
# which overrides the user-prompt keys and blanks the whole pre view — always
# pass THIS system with build_pre_prompt/build_post_prompt.
MACRO_EVENT_SYSTEM_JA = (
    "あなたはARGUSのマクロイベント調査デスクです。個人投資家向けに、指定された1つの予定イベントを"
    "日本語で簡潔に分析します。正直さは絶対: 結果・コンセンサス・機関売買を捏造しない。売買指示は"
    "出さない（判断支援のみ）。出力はSTRICT JSONのみで、キーはユーザーメッセージで指定されたものを"
    "正確に使うこと（別のキー名に置き換えない）。")


def build_pre_prompt(event: Dict[str, Any], market_context_ja: str = "") -> str:
    return (
        "あなたはARGUSのマクロイベント事前分析役です。以下の予定イベントについて、発表前の読みを作成して"
        "ください。これは売買指示ではなく、発表時に何を見るべきかの整理です。" + _NO_FABRICATION +
        "\nキー: summaryJa(このイベントが今回特に重要な理由・1-2文), "
        "argusScenarioJa(ARGUSのAIシナリオ: 強め/弱めに出た場合それぞれ市場がどう動きやすいか・1-2文), "
        "marketPricingJa(市場が何を織り込んでいるように見えるか — 実測の金利/VIX/地合いから読める範囲のみ・1文), "
        "whatWouldSurpriseJa(サプライズになる条件と、その時に最初に確認すべきもの・1文), "
        "assetsToWatch(見るべき資産ティッカー最大5個の配列), "
        "confidence(0-1の数値・このシナリオ整理の確度), limitationsJa(不足データの配列)。"
        f"\nイベント: {event.get('title')} ({event.get('eventCode')}) "
        f"発表時刻UTC: {event.get('eventTimeUtc') or '不明(日付のみ)'}"
        + (f"\n実測の市場文脈: {market_context_ja}" if market_context_ja else ""))


def build_post_prompt(event: Dict[str, Any], pre: Dict[str, Any],
                      actual: Dict[str, Any], market_context_ja: str = "") -> str:
    import json as _json
    pre_txt = _json.dumps({k: pre.get(k) for k in
                           ("summaryJa", "argusScenarioJa", "marketPricingJa",
                            "whatWouldSurpriseJa")}, ensure_ascii=False)
    actual_txt = (_json.dumps({k: actual.get(k) for k in ("headline", "metrics", "source")},
                              ensure_ascii=False)
                  if actual.get("available") else "公式結果未取得")
    return (
        "あなたはARGUSのマクロイベント事後検証役です。発表前に保存されたARGUSの事前予想と、公式結果・"
        "実測の市場文脈を突き合わせ、答え合わせをしてください。" + _NO_FABRICATION +
        "\n追加規則: 公式結果が『公式結果未取得』の場合、答え合わせはverdict=not_scoreable。"
        "事前予想が空の場合もverdict=not_scoreable（『事前予想が保存されていないため答え合わせ不可』と明記）。"
        "\nキー: verdict(hit|partial|miss|not_scoreable), "
        "answerCheckJa(事前予想と結果の照合・当たり外れの理由・1-2文), "
        "marketReactionJa(実測文脈から読める市場の反応・1文・数値の捏造禁止), "
        "portfolioImpactJa(ウォッチリスト/ポートフォリオ視点の影響コメント・1文・売買指示ではない), "
        "whatChangedJa(この結果で何の見方が変わるか・1文), limitationsJa(配列)。"
        f"\nイベント: {event.get('title')} ({event.get('eventCode')})"
        f"\n事前予想(保存済み): {pre_txt}"
        f"\n公式結果: {actual_txt}"
        + (f"\n実測の市場文脈: {market_context_ja}" if market_context_ja else ""))


def ai_meta_record(meta: Any) -> Optional[Dict[str, Any]]:
    """v13.5.63 (GPT review item 6): the model that was ASKED, the model that
    ANSWERED, when, and what it cost — bounded, public-safe, no prompt text."""
    if not isinstance(meta, dict):
        return None
    out: Dict[str, Any] = {}
    for key in ("requestedModel", "returnedModel", "completedAt", "fallbackModel"):
        value = meta.get(key)
        if isinstance(value, str) and value:
            out[key] = value[:60]
    for key in ("inputTokens", "outputTokens"):
        value = meta.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            out[key] = int(value)
    value = meta.get("estUsd")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        out["estUsd"] = round(float(value), 6)
    return out or None


def parse_pre(out: Any, *, phase: str, now_iso: str,
              ai_meta: Any = None) -> Optional[Dict[str, Any]]:
    """Defensive normalizer for the pre-LLM output. None if unusable (caller keeps old)."""
    if not isinstance(out, dict):
        return None
    # legacy-shape salvage: a model pinned to the old {summaryJa, preJa, postJa}
    # schema puts the whole pre view in preJa — recover it instead of dropping it
    scenario = str(out.get("argusScenarioJa") or out.get("preJa") or "")[:300]
    summary = str(out.get("summaryJa") or "")[:300]
    if not scenario and not summary:
        return None                                   # blank must never overwrite a real pre
    conf = out.get("confidence")
    return {
        "generatedAt": now_iso, "phaseAtGeneration": phase,
        "summaryJa": summary, "argusScenarioJa": scenario,
        "marketPricingJa": str(out.get("marketPricingJa") or "")[:300],
        "whatWouldSurpriseJa": str(out.get("whatWouldSurpriseJa") or "")[:300],
        "assetsToWatch": [str(a)[:12] for a in (out.get("assetsToWatch") or [])][:5],
        "confidence": (round(float(conf), 2) if isinstance(conf, (int, float)) else None),
        "limitationsJa": [str(x)[:120] for x in (out.get("limitationsJa") or [])][:5],
        "ai": ai_meta_record(ai_meta),
    }


def parse_post(out: Any, *, now_iso: str, pre_exists: bool, ai_meta: Any = None,
               actual_available: bool) -> Dict[str, Any]:
    """Defensive normalizer for the post-LLM output. Enforces the scoring gates even
    if the model ignores them."""
    o = out if isinstance(out, dict) else {}
    verdict = o.get("verdict")
    if verdict not in ("hit", "partial", "miss", "not_scoreable"):
        verdict = "not_scoreable"
    lims = [str(x)[:120] for x in (o.get("limitationsJa") or [])][:5]
    if not pre_exists:
        verdict = "not_scoreable"
        lims.append("事前予想が保存されていないため答え合わせ不可")
    if not actual_available:
        verdict = "not_scoreable"
        lims.append("公式結果未取得")
    return {
        "generatedAt": now_iso, "verdict": verdict,
        # postJa = legacy-shape salvage (see parse_pre)
        "answerCheckJa": str(o.get("answerCheckJa") or o.get("postJa") or "")[:300],
        "marketReactionJa": str(o.get("marketReactionJa") or "")[:300],
        "portfolioImpactJa": str(o.get("portfolioImpactJa") or "")[:300],
        "whatChangedJa": str(o.get("whatChangedJa") or "")[:300],
        "limitationsJa": sorted(set(lims)),
        "ai": ai_meta_record(ai_meta),
    }
