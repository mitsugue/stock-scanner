"""ARGUS AI cost ledger (v10.50) — pure, stdlib, unit-tested.

Application-side cost accounting + HARD budget stops for the AI judge. The point
(GPT Pro cost-control patch): do NOT rely on the OpenAI prepaid balance or a
provider-side project budget as the stop — enforce a deterministic ARGUS-side
daily/monthly USD ceiling so a loop or a bad config can never run the bill up.

All money values are ESTIMATES from a configurable per-token price table — token
counts come from the providers' own usage metadata, prices are env-overridable
(real list prices change; we never hard-code a number we can't let the owner fix).

Nothing here does I/O or imports Flask: scanner owns the in-memory accumulator,
the persistence to the ledger branch, and the env wiring; this module is the math.
"""

# Default unit prices in USD per 1,000,000 tokens. Override per-model in scanner
# from env (OPENAI_PRICE_INPUT_PER_1M, etc.) — these are conservative placeholders,
# NOT authoritative list prices. Cost is always surfaced as "estimated".
DEFAULT_PRICING = {
    "gpt-5.5":           {"in": 1.25, "out": 10.00},
    # v13.5.63: official list prices (developers.openai.com/api/docs/pricing,
    # read 2026-09-07). GPT-6 Astra is the event-analysis default; the 5.6
    # rows keep the fallback priced. cachedIn = cached-input rate.
    "gpt-6-astra":       {"in": 10.00, "out": 50.00, "cachedIn": 1.00},
    "gpt-5.6-terra":     {"in": 2.00, "out": 12.00, "cachedIn": 0.20},
    "gpt-5.6-sol":       {"in": 4.00, "out": 20.00, "cachedIn": 0.40},
    "gemini-2.5-pro":    {"in": 1.25, "out": 10.00},
    "gemini-2.5-flash":  {"in": 0.30, "out": 2.50},
}
# A Google-Search grounding call carries its own per-request charge on some tiers.
DEFAULT_GROUNDING_USD = 0.035


def _num(x, default=0.0):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else default


def estimate_cost(model, input_tokens, output_tokens, pricing=None,
                  grounding=False, grounding_usd=DEFAULT_GROUNDING_USD):
    """USD estimate for one model call. Unknown model → 0-priced (still counted
    as a run, just $0 estimate — better an honest 0 than a fabricated number).
    output_tokens should already INCLUDE reasoning/thinking tokens (they bill as
    output). Always returns a non-negative float rounded to 6 dp."""
    table = pricing or DEFAULT_PRICING
    p = table.get(model) or {}
    cost = (_num(input_tokens) / 1_000_000.0) * _num(p.get("in"))
    cost += (_num(output_tokens) / 1_000_000.0) * _num(p.get("out"))
    if grounding:
        cost += _num(grounding_usd, DEFAULT_GROUNDING_USD)
    return round(max(0.0, cost), 6)


def month_key(dt):
    """'YYYY-MM' for a datetime (the monthly budget bucket)."""
    return dt.strftime("%Y-%m")


def day_key(dt):
    """'YYYY-MM-DD' for a datetime (the daily budget bucket)."""
    return dt.strftime("%Y-%m-%d")


def budget_check(day_spent, month_spent, day_budget, month_budget,
                 reserve_usd=0.0, force=False):
    """Pure HARD-STOP decision evaluated BEFORE a run (so it weighs ALREADY-spent
    vs the ceiling — a single run is cents and can't blow past materially).

    Returns (allowed: bool, reason: str|None, usedReserve: bool).
      - Normal stop: block once day_spent >= day_budget or month_spent >= month_budget.
      - Emergency reserve: a manual force=True may dip ABOVE the ceiling but only up
        to budget + reserve_usd, and only the MONTHLY reserve is honored (the daily
        cap is advisory under force). This is the 'small manual emergency reserve'.
    A budget of 0 or negative is treated as 'unlimited' (disabled stop)."""
    d_lim = day_budget if isinstance(day_budget, (int, float)) and day_budget > 0 else None
    m_lim = month_budget if isinstance(month_budget, (int, float)) and month_budget > 0 else None
    reserve = max(0.0, _num(reserve_usd))

    if m_lim is not None and month_spent >= m_lim:
        if force and month_spent < (m_lim + reserve):
            return True, None, True
        ceil = m_lim + (reserve if force else 0.0)
        return False, (f"monthly AI budget reached: ${month_spent:.2f} / ${m_lim:.2f}"
                       + (f" (+${reserve:.2f} reserve exhausted)" if force else "")), False
    if d_lim is not None and day_spent >= d_lim and not force:
        return False, f"daily AI budget reached: ${day_spent:.2f} / ${d_lim:.2f}", False
    return True, None, False
