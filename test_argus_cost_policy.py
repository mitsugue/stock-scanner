import unittest

import argus_cost_policy as cp


class CostPolicyTests(unittest.TestCase):
    def test_deterministic_blocks_all_generated_ai(self):
        state = cp.default_state()
        for provider in cp.PROVIDERS:
            result = cp.authorize(
                state, provider=provider, purpose="scheduled", automatic=True,
                estimated_cost_usd=0.01, estimated_tokens=100)
            self.assertFalse(result["allowed"])
            self.assertEqual(result["classification"], "expected_skip")
            self.assertEqual(result["status"], "deterministic_mode")

    def test_event_opt_in_is_explicit_bounded_and_fail_closed(self):
        state = cp.configure(cp.default_state(), mode="EVENT_OPT_IN",
                             event_opt_in=True, event_id="CPI-1",
                             event_enabled=True, providers=["openai"],
                             event_budget_usd=0.5, event_token_limit=5000)
        allowed = cp.authorize(
            state, provider="openai", purpose="event_analysis", automatic=True,
            event_id="CPI-1", event_phase="pre", estimated_cost_usd=0.1,
            estimated_tokens=1000)
        self.assertTrue(allowed["allowed"])
        state = cp.record_execution(
            state, provider="openai", purpose="event_analysis",
            at="2026-07-20T01:00:00Z", estimated_cost_usd=0.1,
            event_id="CPI-1", event_phase="pre")
        duplicate = cp.authorize(
            state, provider="openai", purpose="event_analysis", automatic=True,
            event_id="CPI-1", event_phase="pre", estimated_cost_usd=0.1,
            estimated_tokens=1000)
        self.assertEqual(duplicate["reason"], "event_phase_already_run")
        unknown_cost = cp.authorize(
            state, provider="openai", purpose="event_analysis", automatic=True,
            event_id="CPI-1", event_phase="post", estimated_cost_usd=None,
            estimated_tokens=1000)
        self.assertEqual(unknown_cost["reason"], "cost_unknown")

    def test_manual_requires_confirmation_and_never_authorizes_automatic(self):
        state = cp.default_state("MANUAL")
        denied = cp.authorize(
            state, provider="gemini", purpose="manual_api", automatic=True,
            confirmation=True, estimated_cost_usd=0.01, estimated_tokens=100)
        self.assertEqual(denied["reason"], "manual_only")
        denied = cp.authorize(
            state, provider="gemini", purpose="manual_api", automatic=False,
            confirmation=False, estimated_cost_usd=0.01, estimated_tokens=100)
        self.assertEqual(denied["reason"], "confirmation_required")
        allowed = cp.authorize(
            state, provider="gemini", purpose="manual_api", automatic=False,
            confirmation=True, estimated_cost_usd=0.01, estimated_tokens=100)
        self.assertTrue(allowed["allowed"])

    def test_public_status_counts_providers_without_secrets(self):
        state = cp.default_state()
        state = cp.record_execution(state, provider="openai", purpose="manual_api",
                                    at="2026-07-20T01:00:00Z", estimated_cost_usd=0.02)
        view = cp.public_status(state, "2026-07-20T02:00:00Z")
        self.assertEqual(view["todayRuns"]["openai"], 1)
        self.assertFalse(view["automaticAiEnabled"])
        self.assertNotIn("apiKey", view)

    # ── v13.5.36 SCHEDULED_AI (owner 2026-08-23 「有効にして」) ──
    def test_scheduled_ai_allows_only_the_two_news_purposes(self):
        state = cp.default_state("SCHEDULED_AI")
        for purpose in cp.SCHEDULED_PURPOSES:
            result = cp.authorize(
                state, provider="gemini", purpose=purpose, automatic=True,
                now_iso="2026-08-24T01:00:00Z",
                estimated_cost_usd=0.02, estimated_tokens=3000)
            self.assertTrue(result["allowed"], purpose)
        for purpose in ("event_analysis", "ai_judgment", "osint_research",
                        "buy_candidates", "prose"):
            result = cp.authorize(
                state, provider="openai", purpose=purpose, automatic=True,
                now_iso="2026-08-24T01:00:00Z",
                estimated_cost_usd=0.02, estimated_tokens=3000)
            self.assertFalse(result["allowed"], purpose)
            self.assertEqual(result["reason"], "scheduled_scope_required")

    def test_scheduled_ai_daily_budget_is_a_hard_stop_and_resets_next_day(self):
        state = cp.default_state("SCHEDULED_AI")
        for i in range(3):
            state = cp.record_execution(
                state, provider="gemini", purpose="headline_translation",
                at="2026-08-24T0%d:00:00Z" % i, estimated_cost_usd=0.4)
        blocked = cp.authorize(
            state, provider="gemini", purpose="headline_translation",
            automatic=True, now_iso="2026-08-24T04:00:00Z",
            estimated_cost_usd=0.02, estimated_tokens=3000,
            scheduled_daily_budget_usd=1.0)
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "scheduled_daily_budget_exhausted")
        next_day = cp.authorize(
            state, provider="gemini", purpose="headline_translation",
            automatic=True, now_iso="2026-08-25T01:00:00Z",
            estimated_cost_usd=0.02, estimated_tokens=3000,
            scheduled_daily_budget_usd=1.0)
        self.assertTrue(next_day["allowed"])

    def test_scheduled_ai_still_permits_confirmed_manual_ping_only(self):
        state = cp.default_state("SCHEDULED_AI")
        ok = cp.authorize(
            state, provider="openai", purpose="manual_api", automatic=False,
            confirmation=True, now_iso="2026-08-24T01:00:00Z",
            estimated_cost_usd=0.001, estimated_tokens=100)
        self.assertTrue(ok["allowed"])
        unconfirmed = cp.authorize(
            state, provider="openai", purpose="manual_api", automatic=False,
            confirmation=False, now_iso="2026-08-24T01:00:00Z",
            estimated_cost_usd=0.001, estimated_tokens=100)
        self.assertFalse(unconfirmed["allowed"])
        self.assertEqual(unconfirmed["reason"], "confirmation_required")

    def test_scheduled_ai_public_status_reports_automatic_ai_truthfully(self):
        view = cp.public_status(cp.default_state("SCHEDULED_AI"),
                                "2026-08-24T01:00:00Z")
        self.assertTrue(view["automaticAiEnabled"])
        self.assertIn("日本語要約", view["messageJa"])
        self.assertFalse(cp.public_status(
            cp.default_state(), "2026-08-24T01:00:00Z")["automaticAiEnabled"])


if __name__ == "__main__":
    unittest.main()


class ScheduledEventOptInTests(unittest.TestCase):
    """v13.5.60 (owner 2026-09-07): event scenarios under SCHEDULED_AI are an
    explicit owner opt-in, bounded by the daily budget and a per-day run cap."""

    def _state(self, opt_in):
        return cp.default_state("SCHEDULED_AI", opt_in)

    def test_event_analysis_stays_off_without_the_opt_in(self):
        st = self._state(False)
        r = cp.authorize(st, provider="openai", purpose="event_analysis",
                         automatic=True, now_iso="2026-09-07T01:00:00Z",
                         estimated_cost_usd=0.10, estimated_tokens=8000)
        self.assertFalse(r["allowed"])
        self.assertEqual(r["reason"], "scheduled_scope_required")

    def test_event_analysis_runs_inside_the_daily_budget_when_opted_in(self):
        st = self._state(True)
        r = cp.authorize(st, provider="openai", purpose="event_analysis",
                         automatic=True, now_iso="2026-09-07T01:00:00Z",
                         estimated_cost_usd=0.10, estimated_tokens=8000)
        self.assertTrue(r["allowed"], r)
        # Budget is shared with the news lane: an exhausted day blocks events too.
        spent = self._state(True)
        for i in range(20):
            spent = cp.record_execution(spent, provider="openai", purpose="news_intel",
                                        at=f"2026-09-07T00:{i:02d}:00Z", estimated_cost_usd=0.10)
        blocked = cp.authorize(spent, provider="openai", purpose="event_analysis",
                               automatic=True, now_iso="2026-09-07T01:00:00Z",
                               estimated_cost_usd=0.10, estimated_tokens=8000)
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "scheduled_daily_budget_exhausted")

    def test_event_runs_are_capped_per_day(self):
        st = self._state(True)
        for i in range(cp.SCHEDULED_EVENT_RUNS_PER_DAY):
            st = cp.record_execution(st, provider="openai", purpose="event_analysis",
                                     at=f"2026-09-07T00:{i:02d}:00Z", estimated_cost_usd=0.01)
        r = cp.authorize(st, provider="openai", purpose="event_analysis",
                         automatic=True, now_iso="2026-09-07T01:00:00Z",
                         estimated_cost_usd=0.01, estimated_tokens=8000)
        self.assertFalse(r["allowed"])
        self.assertEqual(r["reason"], "scheduled_event_runs_exhausted")
        # The cap is per UTC day.
        nxt = cp.authorize(st, provider="openai", purpose="event_analysis",
                           automatic=True, now_iso="2026-09-08T01:00:00Z",
                           estimated_cost_usd=0.01, estimated_tokens=8000)
        self.assertTrue(nxt["allowed"], nxt)
