import { useSyncExternalStore } from 'react';
import { createSharedPollingStore } from '../lib/sharedPollingStore';
import {
  getTachibanaLiveDocument, setTachibanaLiveDocument, subscribeTachibanaLive,
} from '../domain/tachibanaLive';
import type { TachibanaLiveDocument } from '../domain/tachibanaLive';

// Decision Evidence (v13.5.13) — canonical artifact references for the device
// SDA, served by /api/argus/decision-evidence. The payload carries verified
// marketTruth / predictionLedger / sho reference dicts per subject; the
// canonicalDecisionEvidence resolver validates and registers them before any
// SDA input may use them. This hook only transports the document.

const REFRESH_INTERVAL_MS = 120_000;   // matches the backend evidence TTL
const MAX_SYMBOLS_PER_REQUEST = 8;
const HEADLINE_SYMBOLS = ['1321', '1306', 'SPY', 'QQQ'] as const;

// v13.5.61 (owner iPhone review 2026-09-07: MARKET SIGNALS read 「— / 7」 and the
// decision 「判断データ確認中」 the moment one fetch failed). The last good
// document is kept on the device and shown until the next fetch succeeds. The
// SDA resolver still verifies every reference (freshUntil / identity), so a
// stale cached subject data-gates truthfully — it can never masquerade as
// current. The document carries no owner data (headline subjects + watchlist
// symbols already stored on this device).
const LAST_GOOD_KEY = 'argus.decisionEvidence.lastGood.v1';
const LAST_GOOD_MAX_BYTES = 400_000;
type LastGood = { subjects: Record<string, unknown>; marketView: ShoMarketView | null; generatedAt: string | null };
export function readLastGoodDecisionEvidence(): LastGood | null {
  try {
    const raw = localStorage.getItem(LAST_GOOD_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as LastGood;
    if (!parsed || typeof parsed.subjects !== 'object' || parsed.subjects === null) return null;
    return parsed;
  } catch { return null; }
}
export function writeLastGoodDecisionEvidence(value: LastGood): void {
  try {
    const raw = JSON.stringify(value);
    if (raw.length > LAST_GOOD_MAX_BYTES) return;
    localStorage.setItem(LAST_GOOD_KEY, raw);
  } catch { /* storage is a convenience, never an authority */ }
}

// v13.5.36 (review item A): document-level SHO MARKET VIEW. Display-only —
// the resolver never registers it as an SDA input; actionAuthority stays
// false by construction on the backend projection.
export interface ShoMarketView {
  schemaVersion: string;
  informationCutoff: string;
  projection: {
    families?: Record<string, {
      status?: string; conditionMet?: boolean | null;
      lineage?: string; validationStatus?: string;
    }>;
    reversal?: {
      downsideState?: string; reversalState?: string;
      validationStatus?: string;
    } | null;
    // v13.5.38: owner-facing SIG-01..07 projection (server-computed x/7).
    marketSignals?: {
      schemaVersion?: string; total?: number; activeCount?: number;
      countLabel?: string; countRule?: string;
      signals?: Array<{
        id?: string; family?: string; nameEn?: string; nameJa?: string;
        state?: string; status?: string | null; conditionMet?: boolean | null;
      }>;
    } | null;
    status?: string;
    actionAuthority?: boolean;
  } | null;
  sourceStatus: Record<string, string>;
  actionAuthority: boolean;
  // v13.5.38: Tachibana LIVE evidence document (argus_tachibana_live), when
  // the backend embeds it; absent on backends without the wiring.
  japaneseLive?: Record<string, unknown> | null;
}

export interface DecisionEvidenceState {
  subjects: Record<string, unknown> | null;
  marketView: ShoMarketView | null;
  generatedAt: string | null;
  loading: boolean;
  error: string | null;
}

// The desired-symbols set is device-local (owner watchlist) and can change
// after mount; the poller reads the current set on every cycle. Headline
// subjects are always requested so the Today instruments can decide.
let desiredSymbols: string[] = [...HEADLINE_SYMBOLS];
let desiredRevision = 0;

export function requestDecisionEvidenceSymbols(symbols: readonly string[]): void {
  const merged: string[] = [...HEADLINE_SYMBOLS];
  for (const raw of symbols) {
    const sym = String(raw || '').toUpperCase();
    if (sym && !merged.includes(sym)) merged.push(sym);
    if (merged.length >= MAX_SYMBOLS_PER_REQUEST) break;
  }
  if (merged.join(',') !== desiredSymbols.join(',')) {
    desiredSymbols = merged;
    desiredRevision += 1;
  }
}

const lastGood = typeof localStorage === 'undefined' ? null : readLastGoodDecisionEvidence();
const decisionEvidenceStore = createSharedPollingStore<DecisionEvidenceState>(
  { subjects: lastGood?.subjects ?? null, marketView: lastGood?.marketView ?? null,
    generatedAt: lastGood?.generatedAt ?? null, loading: true, error: null },
  (setState) => {
    const backend = import.meta.env.VITE_ARGUS_BACKEND_URL;
    if (!backend) {
      setState({ subjects: null, marketView: null, generatedAt: null, loading: false, error: null });
      return () => {};
    }
    const base = backend.replace(/\/$/, '') + '/api/argus/decision-evidence';
    let cancelled = false;
    let fetchedRevision = -1;
    const controllers = new Set<AbortController>();

    async function fetchOnce(): Promise<void> {
      if (cancelled || document.hidden) return;
      const ctrl = new AbortController();
      controllers.add(ctrl);
      const timeout = window.setTimeout(() => ctrl.abort(), 12_000);
      const revision = desiredRevision;
      try {
        const url = `${base}?symbols=${encodeURIComponent(desiredSymbols.join(','))}`;
        const response = await fetch(url, { signal: ctrl.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        if (cancelled) return;
        const data = await response.json() as {
          schemaVersion?: string; generatedAt?: string;
          subjects?: Record<string, unknown>;
          marketView?: ShoMarketView;
          // v13.5.39: the backend publishes the Tachibana LIVE evidence at the
          // document level (beside marketView), never as an SDA subject.
          japaneseLive?: Record<string, unknown> | null;
        };
        if (data.schemaVersion !== 'argus-decision-evidence-v1'
            || typeof data.subjects !== 'object' || data.subjects === null) {
          throw new Error('decision_evidence_schema_mismatch');
        }
        fetchedRevision = revision;
        if (!cancelled) {
          const view = data.marketView;
          const japaneseLive = data.japaneseLive ?? view?.japaneseLive ?? null;
          const marketView = view
            && view.schemaVersion === 'argus-sho-market-view-v1'
            && view.actionAuthority === false ? view : null;
          // v13.5.39: publish the Tachibana LIVE evidence document for the JP
          // quote overlay (absent/invalid documents clear the store) and keep
          // it reachable as marketView.japaneseLive for the Today strip.
          setTachibanaLiveDocument(japaneseLive);
          const next = { subjects: data.subjects,
            marketView: marketView ? { ...marketView, japaneseLive } : null,
            generatedAt: typeof data.generatedAt === 'string' ? data.generatedAt : null };
          writeLastGoodDecisionEvidence(next);
          setState({ ...next, loading: false, error: null });
        }
      } catch (err: unknown) {
        if (!cancelled) {
          setState((current) => ({ ...current, loading: false,
            error: err instanceof Error ? err.message : String(err) }));
        }
      } finally {
        window.clearTimeout(timeout);
        controllers.delete(ctrl);
      }
    }

    void fetchOnce();
    const timer = window.setInterval(() => {
      // A symbol-set change is picked up on the next cycle; an unchanged set
      // simply refreshes within the backend TTL cadence.
      void fetchOnce();
    }, REFRESH_INTERVAL_MS);
    const onVisible = () => { if (!document.hidden) void fetchOnce(); };
    const revisionTimer = window.setInterval(() => {
      if (desiredRevision !== fetchedRevision) void fetchOnce();
    }, 5_000);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      cancelled = true;
      for (const controller of controllers) controller.abort();
      controllers.clear();
      window.clearInterval(timer);
      window.clearInterval(revisionTimer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  },
);

export function useDecisionEvidence(): DecisionEvidenceState {
  return useSyncExternalStore(
    decisionEvidenceStore.subscribe,
    decisionEvidenceStore.getSnapshot,
    decisionEvidenceStore.getSnapshot,
  );
}

/** v13.5.42: the latest Tachibana evidence document, re-rendering on publish. */
export function useTachibanaLiveDocument(): TachibanaLiveDocument | null {
  return useSyncExternalStore(subscribeTachibanaLive, getTachibanaLiveDocument, getTachibanaLiveDocument);
}
