#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const ts = require('typescript');

require.extensions['.ts'] = (module, filename) => {
  const source = fs.readFileSync(filename, 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    fileName: filename,
  }).outputText;
  module._compile(output, filename);
};

const authority = require(path.join(__dirname, '..', 'src', 'domain', 'liveAuthority.ts'));
const decision = require(path.join(__dirname, '..', 'src', 'domain', 'assetDecision.ts'));
const rates = require(path.join(__dirname, '..', 'src', 'domain', 'rateAuthority.ts'));
const priorities = require(path.join(__dirname, '..', 'src', 'domain', 'actionPriority.ts'));
const shares = require(path.join(__dirname, '..', 'src', 'lib', 'positionExposureShare.ts'));

let failed = 0;
function check(name, condition) {
  if (condition) console.log(`  ok  ${name}`);
  else { failed += 1; console.error(`FAIL  ${name}`); }
}

const NOW = Date.parse('2026-08-16T03:00:00.000Z');
const isoAgo = (ms) => new Date(NOW - ms).toISOString();

check('canonical Z timestamp accepted',
  authority.exactAuthorityEpoch('2026-08-16T03:00:00Z') === NOW);
check('canonical explicit offset accepted',
  authority.exactAuthorityEpoch('2026-08-16T12:00:00+09:00') === NOW);
for (const value of [null, 0, false, '', '2026-08-16',
  '2026-08-16T03:00:00Zjunk', '2026-02-30T03:00:00Z',
  '2026-04-31T03:00:00Z', '2026-08-16T24:00:00Z']) {
  check(`malformed timestamp rejected: ${JSON.stringify(value)}`,
    authority.exactAuthorityEpoch(value) === null);
}
check('future +1s fails without negative-age clamp',
  authority.liveAuthorityState(new Date(NOW + 1000).toISOString(), 'cryptoQuote', NOW) === 'invalid');
check('age boundary is inclusive and next millisecond expires',
  authority.liveAuthorityState(isoAgo(120_000), 'actionLabels', NOW) === 'fresh'
  && authority.liveAuthorityState(isoAgo(120_001), 'actionLabels', NOW) === 'expired');

const ratePoint = (overrides = {}) => ({
  latestValue: 150, previousValue: 149.5, change: .5, changeBp: 50,
  latestDate: '2026-08-16', sourceTimestamp: '2026-08-16T02:50:00Z',
  observedAt: '2026-08-16T02:50:00Z', receivedAt: '2026-08-16T02:51:00Z',
  knownAt: '2026-08-16T02:52:00Z', source: 'yahoo', selectedProvider: 'yahoo',
  status: 'live', freshness: 'FRESH', completeness: 'COMPLETE', ...overrides,
});
check('canonical fresh USDJPY is decision usable',
  rates.ratePointDecisionUsable(ratePoint(), NOW));
check('canonical delayed daily fallback stays bounded',
  rates.ratePointDecisionUsable(ratePoint({ status: 'delayed', freshness: 'DELAYED',
    latestDate: '2026-08-10', sourceTimestamp: '2026-08-10',
    observedAt: '2026-08-10T00:00:00Z', receivedAt: '2026-08-10T01:00:00Z',
    knownAt: '2026-08-10T01:00:00Z', source: 'fred', selectedProvider: 'fred' }), NOW));
check('old/future/malformed USDJPY source dates fail closed',
  !rates.ratePointDecisionUsable(ratePoint({ latestDate: '2020-01-01',
    observedAt: '2020-01-01T00:00:00Z' }), NOW)
  && !rates.ratePointDecisionUsable(ratePoint({ latestDate: '2026-08-17',
    observedAt: '2026-08-17T00:00:00Z', receivedAt: '2026-08-17T00:00:00Z',
    knownAt: '2026-08-17T00:00:00Z' }), NOW)
  && !rates.ratePointDecisionUsable(ratePoint({ latestDate: '2026-02-30' }), NOW));
check('rate values and provider fields require exact runtime types',
  !rates.ratePointDecisionUsable(ratePoint({ latestValue: '150' }), NOW)
  && !rates.ratePointDecisionUsable(ratePoint({ selectedProvider: 1 }), NOW));

// Fake-timer lifecycle: unmount cancels the old deadline; a remount after the
// evidence deadline deauthorizes synchronously before any network promise.
{
  let now = NOW;
  let nextId = 0;
  const tasks = new Map();
  const timers = {
    now: () => now,
    setTimeout: (callback, delay) => { const id = ++nextId; tasks.set(id, { callback, at: now + delay }); return id; },
    clearTimeout: (id) => tasks.delete(id),
  };
  let expired = 0;
  const asOf = isoAgo(60_000);
  const cancel = authority.scheduleLiveAuthorityExpiry(asOf, 'actionLabels',
    () => { expired += 1; }, timers);
  check('fresh retained authority arms a deadline', tasks.size === 1 && expired === 0);
  cancel();
  check('zero-subscriber cleanup cancels deadline', tasks.size === 0);
  now = NOW + 61_000;
  authority.scheduleLiveAuthorityExpiry(asOf, 'actionLabels', () => { expired += 1; }, timers);
  check('remount after expiry deauthorizes synchronously', expired === 1 && tasks.size === 0);
}

const actionSnapshot = {
  status: 'live', marketPosture: { label: 'RISK_ON', rationaleJa: '強い' },
  labels: [{ action: 'ADD', confidence: 0.9, status: 'live', reasonJa: '追加',
    supportingData: { bigFlowRatio: 0.5 },
    signal: { code: 'ADD', level: 1, permissions: { newEntry: 'ALLOWED', add: 'ALLOWED' } } }],
};
const staleAction = authority.deauthorizeActionSnapshot(actionSnapshot, 'refresh_failed');
check('refresh failure strips positive action authority',
  staleAction.status === 'partial' && staleAction.labels[0].action === 'WAIT'
  && staleAction.labels[0].supportingData.bigFlowRatio === null
  && staleAction.labels[0].signal.permissions.newEntry === 'BLOCKED'
  && staleAction.labels[0].signal.permissions.add === 'BLOCKED');

const staleFlow = authority.deauthorizeFlowRecords([{ flowClass: 'institutional_accumulation',
  flowClassJa: '大口買い', direction: 'inflow', confidence: .8, actionImplication: 'investigate',
  actionImplicationJa: '確認', ownerReadableWhyJa: '買い' }], 'snapshot_expired')[0];
check('expired positive flow becomes unknown',
  staleFlow.flowClass === 'unknown' && staleFlow.actionImplication === 'no_action');
const defensiveFlow = authority.deauthorizeFlowRecords([{ flowClass: 'distribution',
  flowClassJa: '売り抜け', direction: 'outflow', confidence: .8, actionImplication: 'caution',
  actionImplicationJa: '警戒', ownerReadableWhyJa: '売り' }], 'snapshot_expired')[0];
check('expired defensive flow remains review-only',
  defensiveFlow.flowClass === 'distribution' && defensiveFlow.decisionUsable === false);

const staleSupply = authority.deauthorizeSupplySignals([{ supplyDemandRank: 'A', rankJa: 'A',
  supplyDemandLevel: 'light', condition: 'good', conditionJa: '良好', direction: 'improving',
  confidence: .9, ownerReadableWhyJa: '良い', actionImplication: 'investigate',
  actionImplicationJa: '確認', directness: 'direct_data', directnessJa: '直接' }],
  'snapshot_expired')[0];
check('expired A-rank supply becomes unknown',
  staleSupply.supplyDemandRank === 'Unknown' && staleSupply.actionImplication === 'no_action');

const guard = authority.deauthorizeVisibilityGuard({ visibilityLevel: 'full', blockedActions: [],
  warnings: [], limitations: [], coverageLineJa: 'full', confidenceCap: null, reasonCodes: [] },
  'refresh_failed');
check('visibility failure blocks ENTER and ADD', guard.visibilityLevel === 'minimal'
  && guard.blockedActions.includes('ENTER') && guard.blockedActions.includes('ADD')
  && guard.confidenceCap === .25);

const downside = authority.deauthorizeDownsideSnapshot({ status: 'live', globalRegime: 'RISK_ON',
  jpIntradayOverlay: 'NORMAL', holderRiskOverlay: 'NONE', incidents: [], dataLimitations: [], noteJa: '',
  overlay: { globalRegime: 'RISK_ON', jpIntradayOverlay: 'NORMAL', holderRiskOverlay: 'NONE',
    displayJa: '', reasonJa: '', flags: [] } }, 'refresh_failed');
check('expired empty downside cannot prove NORMAL/NONE', downside.globalRegime === 'UNKNOWN'
  && downside.jpIntradayOverlay === 'CAUTION'
  && downside.holderRiskOverlay === 'REVIEW_REQUIRED');

const quote = { status: 'live', realtimeEvidence: true, sourceTimeStatus: 'PRESENT',
  sourceTimestamp: isoAgo(30_000) };
check('only current CoinGecko quote is decision usable',
  authority.cryptoQuoteDecisionUsable({ ...quote, source: 'coingecko' }, NOW));
check('Coinbase quote stays diagnostic-only',
  !authority.cryptoQuoteDecisionUsable({ ...quote, source: 'coinbase' }, NOW));
check('missing/future/stale crypto source time fails closed',
  !authority.cryptoQuoteDecisionUsable({ ...quote, source: 'coingecko', sourceTimestamp: null }, NOW)
  && !authority.cryptoQuoteDecisionUsable({ ...quote, source: 'coingecko', sourceTimestamp: new Date(NOW + 1).toISOString() }, NOW)
  && !authority.cryptoQuoteDecisionUsable({ ...quote, source: 'coingecko', sourceTimestamp: isoAgo(300_001) }, NOW));
// The backend serves CoinGecko from a 90 s cache and CoinGecko's own
// last_updated_at is already ~30-60 s old when it is fetched, so a quote is
// routinely ~150 s old by the time the browser evaluates it (production
// samples on 2026-09-04: 92-105 s). A budget below that band rejects every
// quote and renders every crypto row with no price — the exact owner-visible
// failure this bound exists to prevent.
const SERVER_CRYPTO_CACHE_TTL_MS = 90_000;   // scanner._CRYPTO_CACHE_TTL
const COINGECKO_SOURCE_LAG_MS = 60_000;      // observed last_updated_at lag
check('crypto budget covers server cache + source lag',
  authority.LIVE_AUTHORITY_MAX_AGE_MS.cryptoQuote
    >= SERVER_CRYPTO_CACHE_TTL_MS + COINGECKO_SOURCE_LAG_MS);
check('a quote delivered at the worst point of the cache cycle still counts',
  authority.cryptoQuoteDecisionUsable({ ...quote, source: 'coingecko',
    sourceTimestamp: isoAgo(SERVER_CRYPTO_CACHE_TTL_MS + COINGECKO_SOURCE_LAG_MS) }, NOW));

const alertCards = authority.deauthorizeActionAlerts([
  { action: 'BUY_DIP', confidence: 'high', risk: 'low', reason: '押し目' },
  { action: 'TRIM', confidence: 'high', risk: 'high', reason: '防御' },
], 'snapshot_expired');
check('ancient Core Portfolio positives become WAIT while defense remains',
  alertCards[0].action === 'WAIT' && alertCards[0].confidence === 'low'
  && alertCards[1].action === 'TRIM'
  && alertCards.every((card) => card.authorityRole === 'EVIDENCE_ONLY'
    && card.finalDecisionAuthorityActive === false));

const ai = (asOf) => ({ status: 'live', freshness: 'fresh', asOf,
  models: { primary: 'primary', checker: 'checker' },
  labels: [{ symbol: 'AAPL', aiFinalAction: 'ADD', reasonJa: 'AI', confidence: .9 }] });
check('pure AI boundary rejects ancient timestamp',
  !decision.assessAi(ai(isoAgo(72 * 60 * 60_000 + 1)), NOW).evidenceAvailable);
check('pure AI boundary rejects future timestamp',
  !decision.assessAi(ai(new Date(NOW + 1).toISOString()), NOW).evidenceAvailable);
check('pure AI boundary rejects malformed timestamp',
  !decision.assessAi(ai('2026-08-16T03:00:00Zjunk'), NOW).evidenceAvailable);
check('AI evidence never exposes a merge action API',
  typeof decision.mergeAiPrimary === 'undefined'
  && typeof decision.resolveAssetDecision === 'undefined');

const missingQuotePriority = priorities.buildItem({
  symbol: '7203', market: 'JP', assetName: 'Toyota', isHeld: false,
  readiness: 'monitor', sdRank: 'A', sdCondition: 'good',
  flowClass: 'institutional_accumulation', dataMissing: ['現在価格未確認'],
});
check('non-held missing quote cannot become an add candidate',
  missingQuotePriority.actionLabel === 'UNKNOWN'
  && missingQuotePriority.blockingReason === 'data_stale');

{
  const realNow = Date.now;
  let clock = NOW;
  Date.now = () => clock;
  const release = shares.retainDerivedSharePublisher();
  const sourceDeadline = NOW + 1_000;
  shares.publishPlans([{ currentStance: 'small_add_allowed' }], sourceDeadline);
  check('derived share is readable before its upstream deadline',
    shares.latestPlans(NOW + 999).length === 1);
  clock = NOW + 500;
  shares.publishPlans([{ currentStance: 'small_add_allowed' }], sourceDeadline);
  check('re-publish cannot renew the upstream deadline',
    shares.latestPlans(sourceDeadline + 1).length === 0);
  shares.publishPlans([{ currentStance: 'small_add_allowed' }], NOW + 60_000);
  release();
  check('last publisher unmount clears decision shares immediately',
    shares.latestPlans(NOW + 501).length === 0);
  Date.now = realNow;
}

const hookDir = path.join(__dirname, '..', 'src', 'hooks');
for (const file of ['useMarketRegime.ts', 'useActionLabels.ts', 'useAIJudgment.ts',
  'useFlowAttribution.ts', 'useSupplyDemand.ts', 'useVisibilityGuard.ts',
  'useDownsideIncidents.ts', 'useCryptoWatchlist.ts', 'useImportantEvents.ts',
  'useEventRadar.ts']) {
  const source = fs.readFileSync(path.join(hookDir, file), 'utf8');
  check(`${file} uses one shared acquisition store`, source.includes('createSharedPollingStore'));
  check(`${file} revalidates retained authority on remount`,
    source.includes('const retained = getState()'));
  check(`${file} binds authority to an evidence deadline`,
    source.includes('scheduleLiveAuthorityExpiry'));
}

const assetIntel = fs.readFileSync(path.join(hookDir, 'useAssetIntel.ts'), 'utf8');
check('unknown important-events authority blocks every plan',
  assetIntel.includes('eventPending: importantEventsUnknown || eventSyms.has(sym)'));
// v13.5.54: the partial boundary is now the union of NAMED reasons rather than
// one anonymous boolean (owner 2026-09-04: 「データ一部不足とは何か」). The
// invariant is unchanged — every authority loss below still reaches it — so
// pin the reasons themselves instead of the old expression's exact text.
check('unknown downside authority reaches the global partial boundary',
  assetIntel.includes("downsideUnknown ? 'downside_unread' : null")
  && assetIntel.includes("importantEventsUnknown ? 'important_events_unread' : null")
  && assetIntel.includes("phase === 'partial' ? 'watchlist_polling_partial' : null")
  && assetIntel.includes('const isPartial = partialReasonCodes.length > 0;')
  && assetIntel.includes("? 'REVIEW_REQUIRED' : downside?.holderRiskOverlay"));
check('every authority the boundary depends on is named, not silently folded in',
  ["flowState.authority !== 'fresh' && !flowPreviousValue ? 'flow_authority_stale' : null",
    "supplyState.authority !== 'fresh' && !supplyPreviousValue ? 'supply_demand_authority_stale' : null",
    "fxAuthorityMissing ? 'fx_authority_missing' : null",
    "sessionAuthorityMissing ? 'session_authority_missing' : null",
    "quoteAuthorityMissing ? 'quote_authority_missing' : null"]
    .every((line) => assetIntel.includes(line)));
check('downside/event authority loss deauthorizes per-asset positive cards',
  assetIntel.includes('assets, labels: cardLabels')
  && assetIntel.includes("positive.has(label.action.trim().toUpperCase()) ? 'WAIT'"));
check('quote, FX, and session absence reach all positive decision boundaries',
  assetIntel.includes('quoteAuthorityMissing')
  && assetIntel.includes('fxAuthorityMissing')
  && assetIntel.includes('sessionAuthorityMissing')
  && assetIntel.includes("missing.push('現在価格未確認')")
  && assetIntel.includes("missing.push('USDJPY未確認')"));
check('derived handoff uses source deadlines and publisher cleanup',
  assetIntel.includes('derivedAuthorityValidUntilMs')
  && assetIntel.includes('retainDerivedSharePublisher'));

const ratesHook = fs.readFileSync(path.join(hookDir, 'useRatesSnapshot.ts'), 'utf8');
check('rates use shared polling, source expiry, and immediate failure deauthority',
  ratesHook.includes('createSharedPollingStore')
  && ratesHook.includes('ratePointDecisionExpiresAt')
  && ratesHook.includes('failRefresh'));

if (failed) {
  console.error(`\n${failed} live-authority checks failed`);
  process.exit(1);
}
console.log('\nAll live-authority checks passed.');
