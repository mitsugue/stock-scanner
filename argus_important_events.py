"""ARGUS — Important Events priority + novice explanations (pure, v10.138).

Turns the Event Radar's schedule rows into owner-facing "why this matters" cards:
a beginner-readable explanation, an owner-relevance-aware priority, the action
that is blocked until release, and what to re-check afterward. Deterministic
templates only — NO forecasts, NO consensus, NO direction prediction, NO trading.
Event IMPACT = how strongly markets may move, NOT whether the result is good/bad.
"""
from __future__ import annotations

import argus_fastdate  # v13.5.52: lock-free strptime (no _strptime._cache_lock)
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Beginner-readable, direction-neutral explanations per event kind (en/ja).
NOVICE: Dict[str, Dict[str, str]] = {
    "pce": {
        "en": "A major US inflation release watched closely by the Federal Reserve. A result above or below market expectations can quickly move interest rates, USDJPY, growth stocks and semiconductor stocks.",
        "ja": "FRBが重視する米国のインフレ指標です。市場予想との差によって、米金利・ドル円・グロース株・半導体株が大きく動く可能性があります。",
    },
    "cpi": {
        "en": "US consumer inflation. A hotter or cooler reading shifts rate-cut expectations, which can move US rates, USDJPY and high-valuation growth stocks.",
        "ja": "米国の消費者物価(インフレ)です。強い/弱い結果で利下げ期待が変わり、米金利・ドル円・高PERのグロース株が動きやすくなります。",
    },
    "ppi": {
        "en": "US wholesale (producer) inflation — a leading indicator for CPI. It can nudge rate expectations and US yields ahead of the consumer numbers.",
        "ja": "米国の卸売物価(PPI)。CPIの先行指標で、金利期待と米金利を発表前に動かすことがあります。",
    },
    "fomc": {
        "en": "The Federal Reserve's interest-rate decision, economic projections and the Chair's press conference. One of the highest-impact events for global rates, USDJPY and equities.",
        "ja": "FRBの政策金利の決定・経済見通し・議長会見です。世界の金利・ドル円・株式に最も影響しやすいイベントの一つです。",
    },
    "boj": {
        "en": "The Bank of Japan's policy meeting. It moves Japanese rates and the yen, which in turn affect banks, exporters and Japanese growth stocks.",
        "ja": "日銀の金融政策決定会合です。日本の金利と円相場を動かし、銀行株・輸出株・日本のグロース株に波及します。",
    },
    "nfp": {
        "en": "The US monthly jobs report. Strong or weak employment changes rate-cut expectations and can move US yields, USDJPY and equities.",
        "ja": "米国の雇用統計です。雇用の強弱で利下げ期待が変わり、米金利・ドル円・株式が動く可能性があります。",
    },
    "jolts": {
        "en": "US job openings — a gauge of labor-market tightness and wage pressure. It can affect rate expectations, USDJPY and growth stocks.",
        "ja": "米国の求人件数(JOLTS)。労働需給と賃金圧力を示し、金利期待・ドル円・グロース株に影響することがあります。",
    },
    "gdp": {
        "en": "US economic growth. A strong or weak reading shifts the balance between recession worries and overheating, moving yields and equity indices.",
        "ja": "米国のGDP(成長率)です。強い/弱い結果で景気後退懸念と過熱懸念のバランスが変わり、金利・株価指数が動きます。",
    },
    "auction": {
        "en": "A US Treasury bond auction. Weak demand can push long-term yields up, which pressures rate-sensitive and high-valuation stocks (e.g. NASDAQ names).",
        "ja": "米国債の入札です。需要が弱いと長期金利が上昇し、金利に敏感な株・高PER株(NASDAQ系など)に圧力がかかります。",
    },
    "earnings": {
        "en": "A company or sector earnings release. The risk is company/sector-specific: a surprise versus expectations can move that name and its peers.",
        "ja": "企業・セクターの決算発表です。リスクは個別・セクター固有で、予想との差がその銘柄や同業に波及します。",
    },
}
_FALLBACK_NOVICE = {
    "en": "A scheduled macro event. Markets may move around the release depending on how the result compares with expectations.",
    "ja": "予定されたマクロイベントです。結果と予想の差し引きで、発表前後に市場が動く可能性があります。",
}

IMPACT_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_RANK_IMPACT = {v: k for k, v in IMPACT_RANK.items()}

# Action blocked until release, by DISPLAY impact (en/ja). Direction-neutral.
ACTION_UNTIL = {
    "critical": {"en": "NEW ENTRY BLOCKED · ADD BLOCKED", "ja": "新規購入 禁止 · 買い増し 禁止"},
    "high":     {"en": "NEW ENTRY BLOCKED · ADD BLOCKED", "ja": "新規購入 禁止 · 買い増し 禁止"},
    "medium":   {"en": "Hold new lump-sum entries; size down", "ja": "新規の一括投入は見送り・サイズは控えめに"},
    "low":      {"en": "No restriction; stay aware", "ja": "制限なし・頭の片隅に"},
}


def _proximity_score(days: Optional[int]) -> float:
    if days is None:
        return 0.3
    if days <= 0:
        return 1.0
    if days == 1:
        return 0.9
    if days <= 3:
        return 0.65
    if days <= 7:
        return 0.45
    return 0.25


# ── v13.5.51: canonical event lifecycle (one truth for Today, dashboard, brief) ──
#
# Tiers, from the owner's spec, in canonical rank order:
#   NOW        high/critical within 24h before or after release        (hero eligible)
#   NEXT       upcoming 1-7 days                                          (hero eligible)
#   RECENT     completed 24-72h with result                              (secondary only)
#   LATER      upcoming 8-30 days
#   MONITORING released, official result still missing beyond the SLA    (never hero)
#   HORIZON    upcoming 31-60 days
#   HISTORY    completed > 72h                                           (never current)
# Events therefore age out: a released PCE from a week ago is HISTORY and can
# never outrank tomorrow's NFP (NEXT).  Undated rows get the same windows from
# their calendar date; rows without any date are HISTORY.
LIFECYCLE_TIERS = ("NOW", "NEXT", "RECENT", "LATER", "MONITORING", "HORIZON", "HISTORY")
TIER_RANK = {tier: index for index, tier in enumerate(LIFECYCLE_TIERS)}
HERO_TIERS = ("NOW", "NEXT")
TIER_LABEL_JA = {"NOW": "いま", "NEXT": "次", "RECENT": "直近", "LATER": "この先",
                 "MONITORING": "結果待ち(監視)", "HORIZON": "先々", "HISTORY": "履歴"}
_HOUR = 3600.0
_DAY = 86400.0
RESULT_SLA_HOURS = 3.0
# The lifecycle windows above are stated in DAYS, and the countdown label the
# same record carries (D / D-1 / D-3 / D-7) is computed from the schedule's own
# Asia/Tokyo calendar-day distance.  The tier must be read off that same day
# count, so the two entry points below cannot drift apart.
_JST_OFFSET_SEC = 9 * 3600


def calendar_days_until(event_epoch: float, now_epoch: float) -> int:
    """Asia/Tokyo calendar-day distance — the same integer as ``daysUntil``."""
    def _day_index(epoch: float) -> int:
        return int((float(epoch) + _JST_OFFSET_SEC) // _DAY)
    return _day_index(event_epoch) - _day_index(now_epoch)


def lifecycle_tier(*, event_epoch: Optional[float], now_epoch: float,
                   importance: str, actual_available: bool = False) -> str:
    """Deterministic lifecycle tier from the event instant and the clock.

    v13.5.53 (measured 2026-09-04): the forward windows used to be cut on
    ELAPSED SECONDS here while /important-events cut the same windows on the
    schedule's calendar days, so one event landed in two different tiers on the
    two public surfaces — US CPI on 2026-09-11 was NEXT on /important-events
    and LATER on /dashboard-events while both records carried the D-7 label.
    v13.5.51 states one lifecycle for both; the forward branch therefore
    delegates to the day-granular table on the same calendar-day count the
    countdown label uses.  The released branch keeps instant precision because
    its SLA (RESULT_SLA_HOURS) is genuinely sub-daily.
    """
    if event_epoch is None:
        return "HISTORY"
    delta = float(event_epoch) - float(now_epoch)
    strong = str(importance or "").lower() in ("critical", "high")
    if delta >= 0:
        return lifecycle_tier_from_days(
            calendar_days_until(event_epoch, now_epoch),
            importance=importance, actual_available=actual_available)
    age = -delta
    if age <= _DAY:
        if strong and (actual_available or age <= RESULT_SLA_HOURS * _HOUR):
            return "NOW"
        if not actual_available:
            return "MONITORING"
        return "RECENT"
    if age <= 3 * _DAY:
        return "RECENT" if actual_available else "MONITORING"
    return "HISTORY"


def lifecycle_tier_from_days(days: int, *, importance: str, actual_available: bool = False) -> str:
    """Day-granular tier from the schedule's own daysUntil (negative = released)."""
    strong = str(importance or "").lower() in ("critical", "high")
    if days >= 0:
        if days <= 1:
            return "NOW" if strong else "NEXT"
        if days <= 7:
            return "NEXT"
        if days <= 30:
            return "LATER"
        return "HORIZON"
    age_days = -days
    if age_days <= 1:
        if strong and actual_available:
            return "NOW"
        return "MONITORING" if not actual_available else "RECENT"
    if age_days <= 3:
        return "RECENT" if actual_available else "MONITORING"
    return "HISTORY"


_IMPORTANCE_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_RELEVANCE_RANK = {"critical": 0, "high": 1, "medium": 2, "normal": 3}


def canonical_rank_key(*, tier: str, importance: str, owner_relevance: Optional[str],
                       event_epoch: Optional[float], now_epoch: float,
                       result_available: bool, event_id: str):
    """One ranking contract for every consumer.

    lifecycle tier → importance → owner relevance → time distance (upcoming
    soonest first, completed newest first) → completeness → stable id.

    v13.5.54 (owner 2026-09-04, 「イベントが出たばかりなので米雇用統計が出てない」):
    RECENT and MONITORING are the SAME event in the same 72h window — the only
    difference is whether ARGUS has parsed the official result yet, which is an
    ingestion detail, not a market fact. Ranking RECENT above LATER but
    MONITORING below it meant a high-impact release dropped off the bounded
    surface the moment it fired and stayed off until its result landed: the US
    Employment Situation vanished six hours after release while routine events
    twelve days out kept their slots. The tier LABEL stays MONITORING so the
    owner still reads 結果待ち; only the sort position joins the RECENT band.
    Ageing out is unaffected — beyond 72h the tier is HISTORY and this never
    applies, so a week-old PCE still cannot outrank tomorrow's NFP.
    """
    if event_epoch is None:
        distance = float("inf")
    else:
        distance = abs(float(event_epoch) - float(now_epoch))
    rank_tier = "RECENT" if tier == "MONITORING" else tier
    return (
        TIER_RANK.get(rank_tier, len(LIFECYCLE_TIERS)),
        _IMPORTANCE_RANK.get(str(importance or "").lower(), 9),
        _RELEVANCE_RANK.get(str(owner_relevance or "normal").lower(), 9),
        distance,
        0 if result_available else 1,
        str(event_id or ""),
    )


def lifecycle_state(days: Optional[int]) -> str:
    """UPCOMING → IMMINENT (today) → RELEASED (already passed). Result/reaction
    states are set elsewhere once verified data exists (none fabricated here)."""
    if days is None:
        return "UPCOMING"
    if days < 0:
        return "RELEASED"
    if days == 0:
        return "IMMINENT"
    return "UPCOMING"


def _owner_relevance(linked: List[str], owner_symbols: set, held_symbols: set,
                     ctx: Dict[str, Any]) -> (str, List[str]):
    reasons: List[str] = []
    linked_up = {str(a).upper() for a in (linked or [])}
    owner_hit = linked_up & {s.upper() for s in owner_symbols}
    held_hit = linked_up & {s.upper() for s in held_symbols}
    # Proxy themes: growth/semis exposure when the owner holds QQQ/SMH-like or the
    # linked set includes them (kept simple + explicit; no hidden formula).
    theme_proxy = bool(linked_up & {"QQQ", "SMH", "NVDA", "SOXX"})
    rel = "normal"
    if held_hit:
        rel = "critical"
        reasons.append("held_asset_linked")
    elif owner_hit:
        rel = "high"
        reasons.append("watchlist_asset_linked")
    elif theme_proxy:
        rel = "medium"
        reasons.append("growth_semiconductor_exposure")
    return rel, reasons


def _promote(base_impact: str, owner_rel: str, days: Optional[int]) -> str:
    """Owner relevance + proximity can raise DISPLAY impact one notch (never lower)."""
    rank = IMPACT_RANK.get(base_impact, 1)
    near = days is not None and days <= 1
    if owner_rel == "critical" and near:
        rank += 1
    elif owner_rel in ("high",) and near and rank < 4:
        rank += 1
    return _RANK_IMPACT[min(4, rank)]


def prioritize_event(event: Dict[str, Any], owner_symbols: set, held_symbols: set,
                     ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ctx = ctx or {}
    base_impact = (event.get("impact") or "low").lower()
    days = event.get("daysUntil")
    owner_rel, reasons = _owner_relevance(event.get("linkedAssets") or [],
                                          owner_symbols, held_symbols, ctx)
    display_impact = _promote(base_impact, owner_rel, days)
    # An imminent (D / D-1) high-impact macro event that is itself driving an
    # EVENT_WAIT / RISK_OFF regime is, for today's decision, effectively CRITICAL —
    # surface it as such so the single most decision-relevant event stands out.
    regime = str((ctx or {}).get("regime") or "").upper()
    if (display_impact == "high" and days is not None and days <= 1
            and regime in ("EVENT_WAIT", "RISK_OFF")):
        display_impact = "critical"
        reasons.append("imminent_event_driving_regime")

    # Direction-neutral priority score (0-100): impact + proximity + relevance +
    # macro stress. Shown to the user only as short reasons, never the raw formula.
    score = 0.0
    score += IMPACT_RANK.get(base_impact, 1) * 14           # up to 56
    score += _proximity_score(days) * 24                    # up to 24
    score += {"critical": 16, "high": 11, "medium": 6, "normal": 0}[owner_rel]
    regime = str(ctx.get("regime") or "").upper()
    if regime in ("EVENT_WAIT", "RISK_OFF"):
        score += 4
        reasons.append("event_wait_regime" if regime == "EVENT_WAIT" else "risk_off_regime")
    if ctx.get("vixElevated"):
        score += 2
        reasons.append("elevated_volatility")
    if base_impact in ("critical", "high"):
        reasons.insert(0, f"{base_impact}_impact_event")
    if days is not None and days <= 1:
        reasons.append("within_24_hours")

    return {
        "baseImpact": base_impact,
        "displayImpact": display_impact,
        "ownerRelevance": owner_rel,
        "proximity": event.get("escalation") or "normal",
        "priorityScore": int(round(min(100.0, score))),
        "priorityReasons": reasons[:4],
        "lifecycle": lifecycle_state(days),
    }


def build_important_events(events: List[Dict[str, Any]], owner_symbols=None,
                           held_symbols=None, ctx=None, limit: int = 8) -> List[Dict[str, Any]]:
    """Enrich + filter + sort events for the Today command area.

    Default visibility: CRITICAL/HIGH always; MEDIUM only when owner-relevant; LOW
    stays in the full calendar. Sorted by displayImpact, then priorityScore, then
    time. No forecast/consensus/actual is invented — those fields stay 'unavailable'
    until a verified source provides them.
    """
    owner_symbols = set(owner_symbols or [])
    held_symbols = set(held_symbols or [])
    ctx = ctx or {}
    out = []
    for e in events or []:
        kind = (e.get("kind") or "").lower()
        pr = prioritize_event(e, owner_symbols, held_symbols, ctx)
        di = pr["displayImpact"]
        # Visibility rule.
        if di in ("critical", "high"):
            pass
        elif di == "medium" and pr["ownerRelevance"] != "normal":
            pass
        else:
            continue
        novice = NOVICE.get(kind, _FALLBACK_NOVICE)
        au = ACTION_UNTIL.get(di, ACTION_UNTIL["low"])
        out.append({
            "eventId": e.get("id"),
            "eventCode": kind.upper() or "EVENT",
            "title": e.get("title"),
            "date": e.get("eventDate"),
            "jstTime": e.get("localTimeJst"),     # may be None for date-only events
            "eventTimeUtc": e.get("eventTimeUtc"),
            "countdown": e.get("escalation") or "normal",
            "daysUntil": e.get("daysUntil"),
            "baseImpact": pr["baseImpact"],
            "displayImpact": di,
            "ownerRelevance": pr["ownerRelevance"],
            "priorityScore": pr["priorityScore"],
            "priorityReasons": pr["priorityReasons"],
            "lifecycle": pr["lifecycle"],
            "noviceEn": novice["en"],
            "noviceJa": novice["ja"],
            "rationaleJa": e.get("rationaleJa"),
            "linkedAssets": e.get("linkedAssets") or [],
            "actionUntilEn": au["en"],
            "actionUntilJa": au["ja"],
            "source": e.get("source"),
            "sourceStatus": e.get("status") or "unknown",
            # Honest data state — never fabricated.
            "forecast": "UNAVAILABLE",
            "previous": "UNAVAILABLE",
            "actual": None,
            "releasedAt": None,
        })
    # v13.5.51: canonical lifecycle tier + rank key (shared with the dashboard
    # summary), applied BEFORE the presentation limit.  HISTORY rows never
    # enter the bounded owner surface.
    now_epoch = _now_epoch_from_ctx(ctx)
    for row in out:
        epoch = _row_epoch(row)
        # The schedule snapshot already carries daysUntil relative to ITS clock;
        # prefer it so the tier never depends on a caller-supplied clock drifting
        # from the snapshot (and stays deterministic for replayed snapshots).
        days = row.get("daysUntil")
        # ...but a day count cannot express "released N hours ago", which is the
        # whole distinction between NOW, MONITORING and RECENT.  Once the event
        # instant has passed, the instant is the only faithful input — and it is
        # what the dashboard summary uses, so both surfaces stay on one answer
        # (v13.5.53: a released same-day event read NOW here and MONITORING
        # there).  Forward events keep the snapshot's own day count.
        # The instant may refine the tier only when it AGREES with the day
        # count — same calendar day, and already past.  A snapshot whose
        # eventDate contradicts its own daysUntil keeps following the declared
        # schedule, so a replayed or hand-built row can never be aged out by a
        # derived date.
        has_days = isinstance(days, int) and not isinstance(days, bool)
        released = (epoch is not None and epoch < now_epoch
                    and (not has_days
                         or (days <= 0
                             and calendar_days_until(epoch, now_epoch) == days)))
        if has_days and not released:
            row["lifecycleTier"] = lifecycle_tier_from_days(
                days, importance=row["displayImpact"], actual_available=bool(row.get("actual")))
            epoch = now_epoch + days * _DAY
        else:
            row["lifecycleTier"] = lifecycle_tier(
                event_epoch=epoch, now_epoch=now_epoch, importance=row["displayImpact"],
                actual_available=bool(row.get("actual")))
        row["lifecycleTierJa"] = TIER_LABEL_JA[row["lifecycleTier"]]
        row["_rank"] = canonical_rank_key(
            tier=row["lifecycleTier"], importance=row["displayImpact"],
            owner_relevance=row.get("ownerRelevance"), event_epoch=epoch,
            now_epoch=now_epoch, result_available=bool(row.get("actual")),
            event_id=str(row.get("eventId") or ""))
    out.sort(key=lambda row: row["_rank"])
    for row in out:
        row.pop("_rank", None)
    current = [row for row in out if row["lifecycleTier"] != "HISTORY"]
    return current[:limit]


def _row_epoch(row: Dict[str, Any]) -> Optional[float]:
    """UTC epoch of the row's release instant (time when known, else the date)."""
    raw = row.get("eventTimeUtc")
    if raw:
        try:
            return argus_fastdate.strptime(str(raw)[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    date = row.get("date") or row.get("eventDate")
    if date:
        try:
            return argus_fastdate.strptime(str(date)[:10], "%Y-%m-%d").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None
    return None


def _now_epoch_from_ctx(ctx: Optional[Dict[str, Any]]) -> float:
    raw = (ctx or {}).get("nowIso") or (ctx or {}).get("now")
    if raw:
        try:
            return argus_fastdate.strptime(str(raw)[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    return datetime.now(timezone.utc).timestamp()
