#!/usr/bin/env python3
"""Acquire and admit the exact accepted V13 source for V13.5 release control.

The release starts from an intentionally shallow checkout.  This module always
asks the configured remote for the exact accepted commit, binds FETCH_HEAD to
that request, verifies its tree, and then performs the product semantic diff.
Pre-merge and production call this same implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import urllib.parse
from typing import Any, Dict, Mapping, Optional


SCHEMA = "argus-v13-5-source-provenance-v1"
PRODUCT_VERSION = "v13.5.57"
ACCEPTED_V13_SOURCE = "f79548bb274c5c5acc4075c181195834c252d54d"
ACCEPTED_V13_TREE = "bdba7c970872b92b88bc6e7cc7b0b8afe4785a96"
CANONICAL_REMOTE = "https://github.com/mitsugue/argus.git"
ROOT = pathlib.Path(__file__).resolve().parents[1]

AUTHORIZED_EXTENSION_PATHS = frozenset({
    # v13.5.36 owner-functional correction: compact iPhone navigation, concise
    # Japanese news projection, source-diverse market evidence, and semantic
    # de-duplication of recurring long-end-rate conditions.  Investment and
    # calibration authority stay outside this list and remain fail-closed.
    # owner-authorized path set for interaction performance (off-thread
    # verification, idle-sliced device ledger appends, keep-mounted Today),
    # the name-selector Today UX, the compact Seven Sign surface, the
    # market-shock (Major News) pipeline, the Prediction Ledger workflow
    # correction (canonical steps before private-store extras + precise
    # diagnostics), the checkpoint-v2 capacity budgets, and the v13.5.x
    # identity. The accepted baseline is the LIVE v13.5.0 release
    # (f79548bb…); anything outside this list fails the release closed.
    # v13.5.36 SCHEDULED_AI enablement (owner 「有効にして」 2026-08-23/24):
    # bounded cost-policy mode + quarantine review. Decision authority remains
    # deterministic and fail-closed.
    "argus_cost_policy.py",
    "smoke_test.py",
    "argus_market_clock.py",
    "test_argus_daily_authority_calendar.py",
    "test_argus_d07_production_supply.py",
    "web/src/domain/glossary.ts",
    "web/src/components/common/GlossaryTip.tsx",
    "web/scripts/glossary.test.cjs",
    "web/src/routes/CommandCenter.tsx",
    "argus_sho.py",
    "test_etf_ts_cache.py",
    "test_jp_mover_tiers.py",
    "test_legacy_provider_source_authority.py",
    "test_argus_single_decision.py",
    "test_argus_market_truth_scanner.py",
    "argus_single_decision.py",
    "web/src/domain/singleDecisionAuthority.ts",
    "argus_market_brief.py",
    "test_argus_market_brief.py",
    "test_argus_market_brief_non_authority.py",
    "web/scripts/brief-non-authority.test.cjs",
    "web/src/hooks/useMarketBrief.ts",
    # v13.5.53 (owner 2026-09-04: 「仮想通貨も何も表示できていない」). The client's
    # crypto freshness budget was shorter than the delivery chain it had to
    # cover (a 90 s server cache over a source that is already ~30-60 s behind),
    # so every CoinGecko quote was rejected and every crypto row rendered with
    # no price. Correcting that budget — and the test that now pins it to the
    # server cache TTL plus the source lag — needs these two display-authority
    # paths. Investment and calibration authority stay outside this list.
    "web/src/domain/liveAuthority.ts",
    "web/scripts/live-authority.test.cjs",
    # v13.5.54 (owner 2026-09-05: Twelve Data plan is BASIC, 8 credits/min,
    # 800/day; the ninth US symbol must not be silently dropped and the plan
    # must never be impersonated). Pure, provider-free warm-scheduler core:
    # rotation under the request batch cap, UTC-day credit ledger, market-aware
    # cadence, owner-authorized universe assembly, and symbol-free budget
    # diagnostics. scanner.py wiring travels separately through Recovery.
    "argus_td_warm.py",
    "test_argus_td_warm.py",
    # v13.5.54 (production measurement 2026-09-04). The verifier compares
    # methodVersion with strict equality; the frontend pin stopped at three
    # segments when scanner.py grew a fourth in v13.5.14, so every verified
    # snapshot was rejected as method_incompatible, the client cache held
    # zero records, and each release's seed-warm-profile job timed out,
    # skipping the downstream acceptance jobs. The asset-chart pin had the
    # same drift. These paths correct the two consumer pins to the producer
    # identities and pin the equality in CI (reading the real backend value)
    # so a future method change fails until the frontend is deliberately
    # updated. Verification is not weakened; no prefix matching.
    "web/src/lib/assetChartCache.ts",
    "web/scripts/verified-snapshot.test.mjs",
    "web/scripts/asset-chart-policy.test.cjs",
    "web/scripts/method-version-contract.mjs",
    "test_argus_method_version_contract.py",
    # v13.5.53 (owner 2026-09-04: 「イベントが何もないことはないはず」). The asset
    # card asserted 「直近の関連イベント・材料の紐付けはありません」 and EVENT
    # EXPOSURE 「直近紐付けなし」 whenever the important-events feed had not been
    # read, turning an unread feed into a claim about the calendar. These three
    # display paths carry the "not known" distinction. Investment and
    # calibration authority stay outside this list.
    "web/src/components/assetDesk/types.ts",
    "web/src/components/assetDesk/AssetEventsPanel.tsx",
    "web/src/components/assetDesk/AssetPositionPanel.tsx",
    "web/src/components/assetDesk/AssetEvidenceSummary.tsx",
    # v13.5.54 (owner 2026-09-04: 「日経平均などの指数がトップに表示されていない、
    # まだETF」). The Today headline draws the index the owner reasons in; the
    # verified ETF snapshot remains the decision anchor and the panel discloses
    # it. Display authority only — investment and calibration authority stay
    # outside this list.
    "web/src/domain/marketInstruments.ts",
    "test_argus_cost_policy.py",
    "test_argus_foundation_jobs.py",
    "test_argus_research_benchmark.py",
    "test_argus_v12_3_0.py",
    "web/src/types/marketLedger.ts",
    ".github/actions/v13-5-pre-mutation-rehearsal/action.yml",
    ".github/workflows/caos-scan.yml",
    ".github/workflows/deploy-pages.yml",
    ".github/workflows/market-public-acceptance.yml",
    ".github/workflows/news-intake-ops.yml",
    ".github/workflows/prediction-ledger.yml",
    ".github/workflows/release-gate.yml",
    "argus_causal_event_memory.py",
    "argus_checkpoint_v2.py",
    "argus_gmail_intake.py",
    "argus_market_shock.py",
    "argus_news_i18n.py",
    "argus_news_intelligence.py",
    "argus_route_catalog.py",
    "argus_today_headline.py",
    "backend-version.json",
    "bridge/moomoo_push.py",
    "docs/ARGUS_V13_5_4_CAUSAL_EVENT_MEMORY.md",
    "product-version.json",
    "release/v13-accepted-fix-manifest.json",
    "scanner.py",
    "scripts/checkpoint_v2_isolated_probe.py",
    "scripts/news_gmail_authorize.py",
    "scripts/normalized_hash_resource_probe.py",
    "scripts/checkpoint_v2_resource_probe.py",
    "scripts/release_gate.sh",
    "scripts/v13_5_pre_mutation_rehearsal.py",
    "scripts/v13_5_release_certificate.py",
    "scripts/v13_5_source_provenance.py",
    "scripts/workflow_http.py",
    "test_argus_deploy_scope.py",
    "test_argus_causal_event_memory.py",
    "test_argus_causal_event_memory_backend.py",
    "test_argus_bridge_v1157.py",
    "test_argus_mission_tick_durability.py",
    "test_argus_sho_non_regression.py",
    "test_argus_gmail_intake.py",
    "test_argus_market_shock.py",
    "test_argus_news_i18n.py",
    "test_argus_news_intelligence.py",
    "test_argus_news_pipeline.py",
    "test_argus_notification_eligibility.py",
    "test_argus_public_operational_boundary.py",
    "test_argus_release_identity.py",
    "test_argus_v12_2_12.py",
    "test_argus_v12_4_0.py",
    "test_argus_v13_1_0.py",
    "test_caos_workflow_recovery.py",
    "test_remote_journal_rearm.py",
    "test_v13_5_release_certificate.py",
    "test_v13_5_pre_mutation_rehearsal.py",
    "test_v13_5_source_provenance.py",
    "test_verify_public_candidate_release.py",
    "web/package-lock.json",
    "web/package.json",
    "web/scripts/full-release-simulation.mjs",
    "web/scripts/argus-engine.test.cjs",
    "web/scripts/asset-desk.test.cjs",
    "web/scripts/causal-event-memory.test.mjs",
    "web/scripts/iphone-profile.mjs",
    "web/scripts/market-data-truth.test.cjs",
    "web/scripts/market-system-integrity.test.cjs",
    "web/scripts/mobile-today-acceptance.mjs",
    "web/scripts/mobile-today-integrity.test.mjs",
    "web/scripts/owner-functional-ui.test.mjs",
    "web/scripts/release-state-machine.mjs",
    "web/scripts/release-state-machine.test.mjs",
    "web/scripts/release-fixture-target.mjs",
    "web/scripts/acceptance-runtime.test.mjs",
    "web/scripts/round3-product-final.test.mjs",
    "web/scripts/runtime-version-truth.test.mjs",
    "web/scripts/today-benchmark.mjs",
    "web/src/App.tsx",
    "web/index.html",
    "web/src/main.tsx",
    "web/vite.config.ts",
    "web/src/components/dashboard/MobileStickyCommand.css",
    "web/src/components/NavRail.css",
    "web/src/components/assetDesk/AssetDecisionCard.tsx",
    "web/src/components/assetDesk/AssetDecisionDetails.tsx",
    "web/src/components/assetDesk/AssetDecisionSummary.tsx",
    "web/src/components/assetDesk/AssetDesk.css",
    "web/src/components/assetDesk/AssetDeskList.tsx",
    "web/src/components/chart/ChartIntelligencePanel.css",
    "web/src/components/chart/ChartIntelligencePanel.tsx",
    "web/src/components/today/ArgusToday.css",
    "web/src/components/today/ArgusTodayPanel.tsx",
    "web/src/hooks/useAssetIntel.ts",
    # v13.5.36 (external-review conformance batch): SHO CORE production
    # wiring (item B), MARKET VIEW/ACTION separation display (item A), event
    # constraint tiering + uncapped imminent feed (item C), and the
    # degraded-feed kernel split (item F).
    "web/src/domain/importantEventsTier.ts",
    "web/src/domain/newsSignalGate.ts",
    "web/scripts/news-signal-gate.test.cjs",
    # v13.5.36 owner directive: internal engine names removed from every
    # user-visible surface (jargon-free UI sweep).
    "web/src/components/dashboard/DownsideIncidentCard.tsx",
    "web/src/domain/assetDecision.ts",
    "web/src/routes/CorePortfolio.tsx",
    "web/src/hooks/useImportantEvents.ts",
    "web/scripts/important-events-tier.test.cjs",
    "web/src/hooks/useChartIntelligence.ts",
    "web/src/hooks/useMarketNews.ts",
    "web/src/components/settings/NewsIntakePanel.tsx",
    "web/src/hooks/useMarketShock.ts",
    "web/src/hooks/useNewsIntelligence.ts",
    "web/src/hooks/useTodayHeadline.ts",
    "web/src/domain/assetDesk.ts",
    "web/src/domain/argusTodayView.ts",
    "web/src/lib/notifications.ts",
    "web/src/routes/Settings.tsx",
    "web/src/lib/sdaDeviceLocal.ts",
    "web/src/lib/todayHeadline.ts",
    "web/src/lib/verifiedSnapshot.ts",
    # v13.5.57: the canonical contract names the DECISION SUBJECT (verified
    # ETF), not the drawn index series. These acceptance-contract test lanes
    # pinned the old attribute expression and now pin the subject form.
    "web/scripts/market-replay.test.mjs",
    "web/scripts/public-market-acceptance.contract.test.mjs",
    "web/src/lib/verify.worker.ts",
    "web/src/lib/verifyWorkerClient.ts",
    "web/src/routes/CommandCenter.tsx",
    "web/src/routes/PageShell.tsx",
    "web/src/routes/Watchlist.tsx",
    "web/src/types/assetItem.ts",
    # v13.5.36 owner spec conformance (2026-08-22): news-risk ⊥ market
    # confirmation, tri-state Action Priority context, honest probability-truth
    # evidence, the canonical artifact resolver boundary (backend
    # decision-evidence route + device resolver + SDA registration seam),
    # canonical candidateAction in issued decisions, and the encrypted-vault
    # ride-along for the append-only device SDA ledger.
    "argus_action_priority.py",
    "argus_single_decision.py",
    "argus_today_intelligence.py",
    "test_argus_action_priority.py",
    "test_argus_sho_conditioning.py",
    "test_argus_decision_evidence.py",
    "test_argus_market_truth_scanner.py",
    "test_argus_v12_rc.py",
    "web/scripts/backup-protection-contract.test.cjs",
    "web/scripts/canonical-decision-evidence.test.cjs",
    "web/scripts/device-local-sda-ledger.test.cjs",
    "web/src/domain/actionPriority.ts",
    "web/src/domain/canonicalDecisionEvidence.ts",
    "web/src/domain/probabilityTruth.ts",
    "web/src/main.tsx",
    "web/src/domain/singleDecisionAuthority.ts",
    "web/src/hooks/useDecisionEvidence.ts",
    "web/src/lib/backup.ts",
    # Recovery-only merges (PR #235-#251) were admitted to protected main
    # through the independent Recovery certificate route
    # (scripts/recovery_admission.py, pinned payload digest), not through this
    # product certificate.  They are already on main; this PR authors no change
    # to any of them.  Listing them keeps the product semantic diff against the
    # accepted v13.5.0 source closed and computable for later product
    # candidates.  Recovery authority, Soak, and acceptance clock stay untouched.
    ".github/workflows/caos-watchtower.yml",
    ".github/workflows/checkpoint-v2-gate.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/memory-attribution.yml",
    "argus_recovery_phase_a_adapter.py",
    "argus_remote_journal.py",
    "argus_remote_receipt_queue.py",
    "argus_remote_recovery.py",
    "argus_remote_recovery_limits.py",
    "docs/EC2_MISSION_SCHEDULER.md",
    "docs/ops/permanent-scheduler-identity-and-soak.md",
    "docs/ops/recovery-phase-a-integration.md",
    "ops/systemd/argus-remote-journal-rearm.service",
    "ops/systemd/argus-remote-journal-rearm.timer",
    "ops/systemd/argus-watchtower-writer.service",
    "ops/systemd/argus-watchtower-writer.timer",
    "scripts/argus_remote_journal_rearm.py",
    "scripts/argus_watchtower_writer_dispatch.py",
    "scripts/install_argus_mission_timer.sh",
    "scripts/install_argus_remote_journal_rearm.sh",
    "scripts/install_argus_watchtower_writer.sh",
    "scripts/prepare_remote_journal_publish.py",
    "scripts/recovery_admission.py",
    "scripts/remote_journal_publish_policy.py",
    "scripts/remote_receipt_drain.py",
    "test_argus_checkpoint_v2_isolated.py",
    "test_argus_identity_installer.py",
    "test_argus_persistent_mission_storage.py",
    "test_argus_recovery_phase_a_adapter.py",
    "test_argus_v12_3_2.py",
    "test_argus_v13_4_2_remote_receipts.py",
    "test_recovery_admission.py",
    "test_remote_receipt_drain.py",
    "test_remote_recovery_nonce_bootstrap.py",
    "test_remote_recovery_publish.py",
    "test_remote_recovery_restore.py",
    # Tachibana e-Branch v4r10 READ-ONLY SHADOW market-data provider, disabled
    # by default (candidate a6648da1, tree ec101b16).  A new isolated package
    # with no scanner import, no public route, no order/amend/cancel surface,
    # and no path to SDA authority; enable flags default to
    # ENABLED=false / SHADOW_ONLY=true / AUTHORITATIVE=false.
    "argus_providers/__init__.py",
    "argus_providers/tachibana/__init__.py",
    "argus_providers/tachibana/client.py",
    "argus_providers/tachibana/config.py",
    "argus_providers/tachibana/cross_validation.py",
    "argus_providers/tachibana/event_stream.py",
    "argus_providers/tachibana/evidence.py",
    "argus_providers/tachibana/models.py",
    "argus_providers/tachibana/normalization.py",
    "argus_providers/tachibana/redaction.py",
    "argus_providers/tachibana/runtime.py",
    "argus_providers/tachibana/sensor.py",
    "argus_providers/tachibana/session.py",
    "argus_providers/tachibana/session_truth.py",
    "argus_providers/tachibana/singleton.py",
    "docs/evidence/tachibana-v4r10-2026-09-01.md",
    "docs/operations/tachibana-live-shadow.md",
    "requirements-tachibana.txt",
    "scripts/tachibana_live_acceptance.py",
    "scripts/tachibana_live_sensor_service.py",
    "scripts/tachibana_readonly_smoke.py",
    "test_argus_tachibana_sensor.py",
    # v13.5.38 Tachibana LIVE product integration: the single product-owned
    # adapter boundary (argus_tachibana_live), the owner-facing MARKET SIGNALS
    # SIG-01..07 projection (argus_market_signals, embedded in the SHO market
    # view), and the Today surfaces that render them.  No scanner/route
    # change is authored here (those stay under the Recovery admission pin).
    "argus_market_signals.py",
    "argus_tachibana_live.py",
    "test_argus_market_signals.py",
    "test_argus_tachibana_live.py",
    "web/src/domain/marketSignals.ts",
    "web/src/domain/tachibanaLive.ts",
    "web/src/components/assetDesk/deskFormat.ts",
    "web/src/hooks/useSystemHealth.ts",
    "web/src/lib/assetStrategy.ts",
    "argus_chart_bootstrap.py",
    "argus_japan_valuation.py",
    "argus_important_events.py",
    "test_important_events.py",
    "test_argus_dashboard_event_summary.py",
    "web/src/lib/dashboardEventState.ts",
    "web/src/hooks/useDashboardEvents.ts",
    "web/src/domain/eventTitleJa.ts",
    "web/src/components/dashboard/ImportantEventsCard.tsx",
    "web/src/components/dashboard/ImportantEventsCard.css",
    "web/scripts/live-intelligence-cache.test.cjs",
    "web/scripts/event-title-ja.test.cjs",
    "test_argus_japan_valuation.py",
    "test_argus_sho.py",
    "web/src/hooks/useJapanWatchlist.ts",
    "web/src/domain/jpWatchFallback.ts",
    "web/scripts/jp-watch-fallback.test.cjs",
    "test_argus_chart_bootstrap.py",
    "argus_dashboard_event_summary.py",
    "argus_macro_event_analysis.py",
    "test_argus_important_events_product_correctness.py",
    "argus_news_freshness.py",
    "argus_mover_cause.py",
    "argus_chart_intelligence.py",
    "argus_decision_evidence_bundle.py",
    "argus_scheduler.py",
    "argus_verified_snapshot.py",
    "argus_risk_discipline.py",
    "argus_research.py",
    "argus_fastdate.py",
    "test_argus_fastdate.py",
    "test_argus_dashboard_events_backend.py",
    "test_argus_macro_v115_backend.py",
    "web/scripts/frontend-market-event-truth.test.cjs",
    "web/scripts/market-signals.test.cjs",
    "web/scripts/tachibana-live.test.cjs",
    "docs/operations/tachibana-live-shadow.md",
})


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def sha256_hex(value: Any) -> str:
    raw = value if isinstance(value, bytes) else canonical_bytes(value)
    return hashlib.sha256(raw).hexdigest()


def _is_sha(value: Any) -> bool:
    return type(value) is str and len(value) == 40 \
        and all(character in "0123456789abcdef" for character in value)


def _load(path: pathlib.Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"source_provenance_json_invalid:{path}") from exc
    if type(value) is not dict:
        raise ValueError(f"source_provenance_json_object_required:{path}")
    return value


def _write(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")


def _git(repo: pathlib.Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().replace("\n", " ")
        raise ValueError(f"git_{args[0].replace('-', '_')}_failed:{detail[:300]}")
    return result.stdout.strip() if result.returncode == 0 else ""


def _resolve(repo: pathlib.Path, ref: str, kind: str) -> str:
    if kind not in {"commit", "tree"}:
        raise ValueError("source_provenance_internal_kind")
    value = _git(repo, "rev-parse", "--verify", f"{ref}^{{{kind}}}")
    if not _is_sha(value):
        raise ValueError(f"{kind}_identity_invalid")
    return value


def _sanitize_remote(url: str) -> str:
    if url.startswith("git@github.com:"):
        return "https://github.com/" + url.split(":", 1)[1]
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme in {"http", "https"}:
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return urllib.parse.urlunsplit(
            (parsed.scheme, host + port, parsed.path, "", ""))
    return url


def _validate_remote(url: str, *, allow_local_remote: bool) -> str:
    clean = _sanitize_remote(url)
    normalized = clean[:-1] if clean.endswith("/") else clean
    canonical = CANONICAL_REMOTE[:-4] if CANONICAL_REMOTE.endswith(".git") \
        else CANONICAL_REMOTE
    candidate = normalized[:-4] if normalized.endswith(".git") else normalized
    if not allow_local_remote and candidate != canonical:
        raise ValueError(f"accepted_source_remote_mismatch:{clean}")
    return clean


def _certificate_identity(path: pathlib.Path) -> Dict[str, Any]:
    value = _load(path)
    digest = value.get("certificateDigest")
    body = dict(value)
    body.pop("certificateDigest", None)
    if type(digest) is not str or len(digest) != 64 \
            or digest != sha256_hex(body):
        raise ValueError("source_provenance_certificate_digest_invalid")
    return value


def _manifest_identity(repo: pathlib.Path) -> Dict[str, Any]:
    manifest = _load(repo / "release/v13-accepted-fix-manifest.json")
    source = manifest.get("canonicalSource")
    if type(source) is not dict or source.get("head") != ACCEPTED_V13_SOURCE \
            or source.get("tree") != ACCEPTED_V13_TREE:
        raise ValueError("accepted_source_authority_conflict")
    return manifest


def validate_product_semantic_diff(
        candidate_ref: str, *, repo: pathlib.Path = ROOT) -> Dict[str, Any]:
    accepted_commit = _resolve(repo, ACCEPTED_V13_SOURCE, "commit")
    accepted_tree = _resolve(repo, accepted_commit, "tree")
    if accepted_commit != ACCEPTED_V13_SOURCE:
        raise ValueError("accepted_v13_source_commit_mismatch")
    if accepted_tree != ACCEPTED_V13_TREE:
        raise ValueError("accepted_v13_source_tree_mismatch")
    candidate_commit = _resolve(repo, candidate_ref, "commit")
    changed = _git(repo, "diff", "--name-only", accepted_commit,
                   candidate_commit).splitlines()
    if len(changed) != len(set(changed)):
        raise ValueError("product_semantic_diff_duplicate_path")
    unauthorized = sorted(set(changed) - AUTHORIZED_EXTENSION_PATHS)
    if unauthorized:
        raise ValueError(
            "product_semantic_change_required:" + ",".join(unauthorized))
    return {
        "status": "PASS",
        "acceptedSource": accepted_commit,
        "acceptedTree": accepted_tree,
        "changedPaths": sorted(changed),
        "productSemanticChange": False,
    }


def acquire_source(
        *, repo: pathlib.Path, remote: str, accepted_source: str,
        accepted_tree: str, candidate_sha: str, candidate_tree: str,
        certificate_path: pathlib.Path, release_merge_sha: Optional[str] = None,
        release_merge_tree: Optional[str] = None,
        allow_local_remote: bool = False) -> Dict[str, Any]:
    repo = repo.resolve()
    if accepted_source != ACCEPTED_V13_SOURCE \
            or accepted_tree != ACCEPTED_V13_TREE:
        raise ValueError("accepted_source_authority_conflict")
    if not _is_sha(candidate_sha) or not _is_sha(candidate_tree):
        raise ValueError("candidate_identity_invalid")
    if (release_merge_sha is None) != (release_merge_tree is None):
        raise ValueError("release_merge_identity_incomplete")
    if release_merge_sha is not None \
            and (not _is_sha(release_merge_sha)
                 or not _is_sha(release_merge_tree)):
        raise ValueError("release_merge_identity_invalid")

    _manifest_identity(repo)
    product = _load(repo / "product-version.json")
    if product != {"schemaVersion": "argus-product-version-v1",
                   "productVersion": PRODUCT_VERSION}:
        raise ValueError("product_version_not_v13_5")
    certificate = _certificate_identity(certificate_path)
    if certificate.get("candidate") != {
            "commitSha": candidate_sha, "treeSha": candidate_tree}:
        raise ValueError("source_provenance_certificate_candidate_mismatch")
    if certificate.get("acceptedV13Source") != {
            "commitSha": accepted_source, "treeSha": accepted_tree}:
        raise ValueError("source_provenance_certificate_source_mismatch")
    if certificate.get("productVersion") != PRODUCT_VERSION:
        raise ValueError("source_provenance_certificate_product_mismatch")

    remote_url = _validate_remote(
        _git(repo, "remote", "get-url", remote),
        allow_local_remote=allow_local_remote)
    shallow_before = _git(repo, "rev-parse", "--is-shallow-repository") == "true"
    present_before = bool(_git(
        repo, "rev-parse", "--verify", f"{accepted_source}^{{commit}}",
        check=False))

    fetch = subprocess.run([
        "git", "fetch", "--force", "--no-tags", "--no-recurse-submodules",
        "--depth=1", remote, accepted_source,
    ], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
       check=False)
    if fetch.returncode != 0:
        detail = (fetch.stderr or fetch.stdout).strip().replace("\n", " ")
        raise ValueError(f"accepted_source_fetch_failed:{detail[:300]}")
    fetched_commit = _resolve(repo, "FETCH_HEAD", "commit")
    if fetched_commit != accepted_source:
        raise ValueError("accepted_source_fetch_head_mismatch")
    resolved_commit = _resolve(repo, accepted_source, "commit")
    if resolved_commit != accepted_source:
        raise ValueError("accepted_source_commit_mismatch")
    resolved_tree = _resolve(repo, resolved_commit, "tree")
    if resolved_tree != accepted_tree:
        raise ValueError("accepted_source_tree_mismatch")

    resolved_candidate = _resolve(repo, candidate_sha, "commit")
    resolved_candidate_tree = _resolve(repo, resolved_candidate, "tree")
    if resolved_candidate != candidate_sha:
        raise ValueError("candidate_commit_mismatch")
    if resolved_candidate_tree != candidate_tree:
        raise ValueError("candidate_tree_mismatch")

    release: Optional[Dict[str, Any]] = None
    if release_merge_sha is not None:
        resolved_release = _resolve(repo, release_merge_sha, "commit")
        resolved_release_tree = _resolve(repo, resolved_release, "tree")
        if resolved_release != release_merge_sha:
            raise ValueError("release_merge_commit_mismatch")
        if resolved_release_tree != release_merge_tree:
            raise ValueError("release_merge_tree_mismatch")
        if resolved_release_tree != candidate_tree:
            raise ValueError("release_merge_candidate_tree_mismatch")
        parents = _git(
            repo, "rev-list", "--parents", "-n", "1", resolved_release).split()
        if len(parents) != 3 or parents[0] != resolved_release \
                or parents[2] != candidate_sha:
            raise ValueError("release_merge_candidate_parent_mismatch")
        release = {"commitSha": resolved_release,
                   "treeSha": resolved_release_tree,
                   "candidateParentSha": parents[2]}

    semantic = validate_product_semantic_diff(candidate_sha, repo=repo)
    manifest = _manifest_identity(repo)
    body: Dict[str, Any] = {
        "schemaVersion": SCHEMA,
        "status": "PASS",
        "remote": {"name": remote, "url": remote_url},
        "fetch": {
            "requestedCommitSha": accepted_source,
            "fetchHeadCommitSha": fetched_commit,
            "depth": 1,
            "noTags": True,
            "sourcePresentBeforeFetch": present_before,
            "initialCheckoutShallow": shallow_before,
            "postFetchShallow": _git(
                repo, "rev-parse", "--is-shallow-repository") == "true",
        },
        "acceptedSource": {"commitSha": resolved_commit,
                           "treeSha": resolved_tree},
        "candidate": {"commitSha": resolved_candidate,
                      "treeSha": resolved_candidate_tree},
        "releaseMerge": release,
        "productVersion": PRODUCT_VERSION,
        "certificateDigest": certificate["certificateDigest"],
        "acceptedFixManifestDigest": sha256_hex(manifest),
        "semanticDiff": semantic,
    }
    body["provenanceDigest"] = sha256_hex(body)
    return body


def validate_receipt(
        value: Mapping[str, Any], *, candidate_sha: str, candidate_tree: str,
        certificate_digest: str, release_merge_sha: Optional[str] = None,
        release_merge_tree: Optional[str] = None,
        repo: pathlib.Path = ROOT) -> Dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("source_provenance_receipt_object_required")
    receipt = dict(value)
    digest = receipt.pop("provenanceDigest", None)
    expected_keys = {
        "schemaVersion", "status", "remote", "fetch", "acceptedSource",
        "candidate", "releaseMerge", "productVersion", "certificateDigest",
        "acceptedFixManifestDigest", "semanticDiff",
    }
    if set(receipt) != expected_keys or type(digest) is not str \
            or len(digest) != 64 or digest != sha256_hex(receipt) \
            or receipt.get("schemaVersion") != SCHEMA \
            or receipt.get("status") != "PASS" \
            or receipt.get("acceptedSource") != {
                "commitSha": ACCEPTED_V13_SOURCE,
                "treeSha": ACCEPTED_V13_TREE} \
            or receipt.get("candidate") != {
                "commitSha": candidate_sha, "treeSha": candidate_tree} \
            or receipt.get("productVersion") != PRODUCT_VERSION \
            or receipt.get("certificateDigest") != certificate_digest:
        raise ValueError("source_provenance_receipt_invalid")
    fetch = receipt.get("fetch")
    remote = receipt.get("remote")
    if type(fetch) is not dict or set(fetch) != {
            "requestedCommitSha", "fetchHeadCommitSha", "depth", "noTags",
            "sourcePresentBeforeFetch", "initialCheckoutShallow",
            "postFetchShallow"} \
            or fetch.get("requestedCommitSha") != ACCEPTED_V13_SOURCE \
            or fetch.get("fetchHeadCommitSha") != ACCEPTED_V13_SOURCE \
            or fetch.get("depth") != 1 or fetch.get("noTags") is not True \
            or type(fetch.get("sourcePresentBeforeFetch")) is not bool \
            or type(fetch.get("initialCheckoutShallow")) is not bool \
            or type(fetch.get("postFetchShallow")) is not bool \
            or type(remote) is not dict or set(remote) != {"name", "url"} \
            or remote.get("name") != "origin" \
            or _validate_remote(remote.get("url", ""), allow_local_remote=False) \
            != remote.get("url"):
        raise ValueError("source_provenance_fetch_receipt_invalid")
    expected_release = None
    if release_merge_sha is not None or release_merge_tree is not None:
        if release_merge_sha is None or release_merge_tree is None:
            raise ValueError("release_merge_identity_incomplete")
        expected_release = {"commitSha": release_merge_sha,
                            "treeSha": release_merge_tree,
                            "candidateParentSha": candidate_sha}
    if receipt.get("releaseMerge") != expected_release:
        raise ValueError("source_provenance_release_merge_mismatch")
    semantic = validate_product_semantic_diff(candidate_sha, repo=repo)
    if receipt.get("semanticDiff") != semantic:
        raise ValueError("source_provenance_semantic_diff_mismatch")
    if receipt.get("acceptedFixManifestDigest") != sha256_hex(
            _manifest_identity(repo)):
        raise ValueError("source_provenance_manifest_mismatch")
    receipt["provenanceDigest"] = digest
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--accepted-source", required=True)
    parser.add_argument("--accepted-tree", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--candidate-tree", required=True)
    parser.add_argument("--release-merge-sha", default="")
    parser.add_argument("--release-merge-tree", default="")
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    receipt = acquire_source(
        repo=pathlib.Path(args.repo_root), remote=args.remote,
        accepted_source=args.accepted_source, accepted_tree=args.accepted_tree,
        candidate_sha=args.candidate_sha, candidate_tree=args.candidate_tree,
        certificate_path=pathlib.Path(args.certificate),
        release_merge_sha=args.release_merge_sha or None,
        release_merge_tree=args.release_merge_tree or None)
    _write(pathlib.Path(args.out), receipt)
    print("V13_5_ACCEPTED_SOURCE_PROVENANCE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
