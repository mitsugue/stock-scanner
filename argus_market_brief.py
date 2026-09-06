# -*- coding: utf-8 -*-
"""ARGUS v13.5.36 — MARKET SITUATION BRIEF (pure composer).

Owner directive 2026-08-26 (+external review conditions): a single Today-top
card that answers 「今の市場は何が起きているか」 in three parts —
NOW / WHY / NEXT — plus four glance chips (CHART / NEWS / NEXT EVENT /
MAIN RISK).

Discipline (non-negotiable):
- The AI is the EDITOR, never the reporter: a deterministic composer selects
  and orders the facts (P0 今日これだけは知るべき → P1 相場方向 → P2 なぜ →
  P3 次に確認), and the model may only compress them into short Japanese.
- 盛らない: no number, probability or forecast may appear unless it came in
  through a fact input. validate_ai_brief() rejects AI text that introduces
  digits/percentages absent from the fact base, execution vocabulary, or
  probability claims.
- Every fact carries a verification tag: VERIFIED (measured market data) /
  CORROBORATED (market-confirmed news) / UNCONFIRMED (pending).
- This document is evidence for a human reader. sdaAuthority is False by
  construction and no SDA input reads it.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

BRIEF_SCHEMA = "argus-market-brief-v1"
PRIORITIES = ("P0", "P1", "P2", "P3")
VERIFICATIONS = ("VERIFIED", "CORROBORATED", "UNCONFIRMED")

_DIRECTION_JA = {"BULLISH": "強気", "BEARISH": "弱気", "MIXED": "強弱混在",
                 "UNCLEAR": "方向未確定"}
_IMPACT_JA = {"critical": "最重要", "high": "重要", "medium": "中", "low": "小"}

# Vocabulary the composer AND the AI polish must never emit (RC discipline:
# no execution orders, no invented probabilities/targets).
_FORBIDDEN_BRIEF_PATTERNS = (
    "買え", "売れ", "全力", "今すぐ買", "今すぐ売", "確実に", "必ず",
    "暴騰確率", "上昇確率", "%の確率", "％の確率",
)


def _digits_of(text: str) -> set:
    return set(re.findall(r"\d+(?:\.\d+)?", str(text or "")))


def validate_ai_brief(ai: Any, fact_texts: Sequence[str]) -> Optional[Dict[str, str]]:
    """Strict gate for the AI-compressed NOW/WHY/NEXT. Fails closed to None
    (the deterministic lines then render instead)."""
    if not isinstance(ai, Mapping):
        return None
    out: Dict[str, str] = {}
    allowed_digits = set()
    for t in fact_texts or []:
        allowed_digits |= _digits_of(t)
    for key in ("nowJa", "whyJa", "nextJa"):
        value = ai.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 220:
            return None
        if any(p in value for p in _FORBIDDEN_BRIEF_PATTERNS):
            return None
        # 盛らない: every number in the AI text must exist in the fact base.
        if _digits_of(value) - allowed_digits:
            return None
        out[key] = value.strip()
    return out


def _fact(text: str, priority: str, source: str,
          verification: str) -> Dict[str, str]:
    return {"text": str(text)[:160], "priority": priority, "source": source,
            "verification": verification
            if verification in VERIFICATIONS else "UNCONFIRMED"}


def _news_direction_summary(events: Sequence[Mapping[str, Any]]) -> str:
    bear = sum(1 for e in events if "BEARISH" in set(
        ((e.get("impactDirection") or {}).get("directionByTarget")
         or {}).values()))
    bull = sum(1 for e in events if set(
        ((e.get("impactDirection") or {}).get("directionByTarget")
         or {}).values()) == {"BULLISH"})
    if bear and not bull:
        return "弱気材料が優勢"
    if bull and not bear:
        return "強気材料が優勢"
    if bear and bull:
        return "強弱材料が混在"
    return "方向材料は限定的"


def compose_brief(*, now_iso: str,
                  market_view_summary: Optional[Mapping[str, Any]] = None,
                  shock_events: Sequence[Mapping[str, Any]] = (),
                  news_events: Sequence[Mapping[str, Any]] = (),
                  imminent_events: Sequence[Mapping[str, Any]] = (),
                  next_events: Sequence[Mapping[str, Any]] = (),
                  ) -> Dict[str, Any]:
    """Deterministic NOW/WHY/NEXT + chips + prioritized fact list.

    All inputs are already-verified ARGUS stores (Market Truth, trusted mail,
    official sensors, calendar). Missing inputs produce honest omissions,
    never invented content. Works fully without any AI."""
    facts: List[Dict[str, str]] = []

    # ── P0: 今日これだけは知るべき ──
    material_news = [e for e in news_events
                     if e.get("severity") in ("HIGH", "CRITICAL")
                     and str(e.get("staleness") or "").upper() != "STALE"]
    for event in material_news[:2]:
        confirmed = event.get("confirmationState") == "MARKET_CONFIRMED"
        headline = str(event.get("headlineJa") or "").strip()
        if not headline or headline == "翻訳処理中":
            headline = "重要発表（日本語要約 処理中）"
        facts.append(_fact(
            f"{event.get('sourceLabelJa') or event.get('sourceFamily') or '公式'}: "
            f"{headline[:60]}"
            f"（{'市場確認済み' if confirmed else '市場確認待ち'}）",
            "P0", "trusted_mail",
            "CORROBORATED" if confirmed else "UNCONFIRMED"))
    active_shocks = [s for s in shock_events
                     if s.get("severity") in ("HIGH", "CRITICAL")]
    for shock in active_shocks[:2]:
        facts.append(_fact(
            "市場ショック: "
            f"{str(shock.get('headlineJa') or shock.get('titleJa') or shock.get('title') or '')[:60]}",
            "P0", "official_sensor", "VERIFIED"))
    for event in list(imminent_events)[:2]:
        impact = _IMPACT_JA.get(str(event.get("displayImpact") or ""), "")
        facts.append(_fact(
            f"目前イベント: {str(event.get('title') or '')[:50]}"
            f"（{event.get('countdown') or '近日'}"
            f"{'・' + impact if impact else ''}）",
            "P0", "calendar", "VERIFIED"))

    # ── P1: 現在の相場方向 ──
    view_label = str((market_view_summary or {}).get("label") or "").strip()
    if view_label:
        facts.append(_fact(f"市場観（検証前の参考）: {view_label[:70]}",
                           "P1", "market_view", "VERIFIED"))
    news_line = _news_direction_summary(material_news or news_events)
    facts.append(_fact(f"ニュース方向: {news_line}", "P1",
                       "trusted_mail", "CORROBORATED"
                       if any(e.get("confirmationState") == "MARKET_CONFIRMED"
                              for e in material_news) else "UNCONFIRMED"))

    # ── P2: なぜそうなっているか ──
    for event in material_news[:2]:
        path = ((event.get("impactDirection") or {}).get("transmissionJa")
                or (event.get("impactDirection") or {}).get("transmission"))
        if path:
            facts.append(_fact(f"波及経路: {str(path)[:90]}", "P2",
                               "trusted_mail", "UNCONFIRMED"))
    for shock in active_shocks[:1]:
        why = shock.get("whyJa") or shock.get("noteJa")
        if why:
            facts.append(_fact(f"背景: {str(why)[:90]}", "P2",
                               "official_sensor", "VERIFIED"))

    # ── P3: 次に何を確認するか ──
    for event in list(next_events)[:2]:
        facts.append(_fact(
            f"次: {str(event.get('title') or '')[:50]}"
            f"（{event.get('countdown') or event.get('whenJa') or '予定'}）",
            "P3", "calendar", "VERIFIED"))
    if material_news:
        facts.append(_fact("次: 上記ニュースの市場確認センサー"
                           "（金利・株価指数・為替）の反応を確認", "P3",
                           "policy", "VERIFIED"))

    # v13.5.54 (owner 2026-09-04). Two DIFFERENT Treasury releases both render
    # to 「米財務省: 重要発表（日本語要約 処理中）（市場確認待ち）」 while their
    # Japanese summaries are still pending, so Today read 「今: 米財務省: 重要
    # 発表。米財務省: 重要発表」 — the same sentence twice, which reads as a bug
    # and says nothing the first sentence did not. Collapse identical rendered
    # lines, keeping the first. The event COUNT is not lost: the news surface
    # states it separately (「重大2件」), and nothing here invents a distinction
    # the pending translation has not given us yet.
    seen_fact_texts: set = set()
    deduped: List[Dict[str, str]] = []
    for fact in facts:
        if fact["text"] in seen_fact_texts:
            continue
        seen_fact_texts.add(fact["text"])
        deduped.append(fact)
    facts = deduped

    # ── deterministic NOW / WHY / NEXT（AI不在でも成立する行） ──
    p0_facts = [f for f in facts if f["priority"] == "P0"]
    # v13.5.53 (owner 2026-09-04): 「今」 took the first two P0 facts in
    # insertion order, and news is appended before the calendar. On a day with
    # two material headlines the D/D-1 event fact was silently dropped — the
    # owner's Today read 「今: OFAC…。Nikkei…」 with no mention of that day's
    # US Employment Situation, the single event most likely to move the book.
    # A same-day event is one short clause and must never lose its slot: keep
    # the first P0 fact, then guarantee the imminent-calendar fact the second.
    p0_imminent = [f for f in p0_facts if f["source"] == "calendar"]
    if p0_imminent and p0_imminent[0] not in p0_facts[:2]:
        p0_facts = [p0_facts[0], p0_imminent[0]]
    p0_texts = [f["text"] for f in p0_facts]
    now_line = ("。".join(t.split("（")[0] for t in p0_texts[:2])
                or (view_label and f"大きな新規材料なし。{view_label[:40]}")
                or "大きな新規材料は検知していません")
    p2_texts = [f["text"] for f in facts if f["priority"] == "P2"]
    why_line = ("。".join(t[:70] for t in p2_texts[:2])
                or f"ニュース方向: {news_line}")
    p3_texts = [f["text"] for f in facts if f["priority"] == "P3"]
    next_line = ("。".join(t[3:70] if t.startswith("次: ") else t[:70]
                           for t in p3_texts[:2])
                 or "新しい確定情報の到着を待って再評価")

    nearest = (list(imminent_events) or list(next_events) or [None])[0]
    chips = {
        "chart": view_label[:40] or "市場観 取得中（検証前・参考）",
        "news": news_line,
        "nextEvent": (f"{str(nearest.get('title') or '')[:26]}"
                      f" · {nearest.get('countdown') or '近日'}"
                      if nearest else "直近の重要イベントなし"),
        "mainRisk": (str((active_shocks[0].get("headlineJa")
                          or active_shocks[0].get("titleJa")
                          or active_shocks[0].get("title") or ""))[:30]
                     if active_shocks else
                     (f"{material_news[0].get('sourceLabelJa') or ''}"
                      f"関連の未確認リスク" if material_news
                      else "特定の集中リスク検知なし")),
    }

    has_critical = (any(e.get("severity") == "CRITICAL"
                        for e in material_news)
                    or any(s.get("severity") == "CRITICAL"
                           for s in active_shocks))
    return {
        "schemaVersion": BRIEF_SCHEMA,
        "generatedAt": now_iso,
        "hasCritical": has_critical,
        "now": now_line[:220], "why": why_line[:220], "next": next_line[:220],
        "aiText": None,           # scanner fills after validate_ai_brief
        "aiModel": None,
        "chips": chips,
        "facts": facts[:12],
        "priorityOrderJa": "P0 今日これだけは知るべき / P1 相場方向 / "
                           "P2 なぜ / P3 次に確認",
        "noteJa": "事実はARGUS検証済みストアの優先順位圧縮。AIは要約のみで"
                  "取材しません。数値・確率の創作は構造的に排除。"
                  "売買権限はありません。",
        "authority": "MARKET_BRIEF_EVIDENCE",
        "sdaAuthority": False,
        "automaticAiCalls": 0,
    }
