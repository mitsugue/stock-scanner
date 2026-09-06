#!/usr/bin/env node
'use strict';
const fs = require('fs');
const path = require('path');
const ts = require('typescript');
require.extensions['.ts'] = (mod, filename) => {
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  }).outputText;
  mod._compile(output, filename);
};

const root = path.join(__dirname, '..');
const {
  buildArgusTodayView, buildTodayProjection, selectAutoMarket, selectTodayNews,
} = require(path.join(root, 'src/domain/argusTodayView.ts'));
let failed = 0;
function check(name, condition) {
  if (condition) console.log(`  ok  ${name}`);
  else { failed += 1; console.error(`FAIL  ${name}`); }
}

const canonical = (action = 'WAIT', status = 'EVALUATED', level = null) => ({
  schemaVersion: 'single-decision-authority-v2', decisionId: `sda-${'1'.repeat(64)}`,
  status, primaryAction: action, confidence: { valueBps: 4200, status: 'BOUNDED' },
  targets: [], invalidation: null, nextReviewConditionCodes: ['evidence_refresh'],
  freshness: status === 'EVALUATED' ? 'FRESH' : 'UNKNOWN', missingReasonCodes: [],
  conflictReasonCodes: [], dissentReasonCodes: [], evidenceRefs: [], primitiveFactorIds: [],
  guidance: { position: 'NO_ACTION', riskConstraint: 'WAIT_REQUIRED' },
  identities: { authorityPolicyId: 'argus-single-decision-authority-v2',
    marketTruth: { status: 'MISSING' }, predictionLedger: { status: 'MISSING' },
    sho: { status: 'MISSING' }, risk: { status: 'DATA_GATED' } },
  sevenSign: { status: level == null ? 'DATA_GATED' : 'SHADOW', candidateLevel: level,
    productionLevel: null, reasonCodes: [] },
});
const state = (market, session, trading = true, next = '2026-07-23') => ({
  market: market === 'JP' ? 'JP_EQUITY' : 'US_EQUITY', marketDate: '2026-07-22',
  isTradingDay: trading, session, holidayName: trading ? null : 'Holiday', nextTradingDay: next,
  timezone: market === 'JP' ? 'Asia/Tokyo' : 'America/New_York',
  calendarVersion: 'argus-market-calendar-2026.2',
  officialCalendar: market === 'JP' ? 'JPX_TSE' : 'NYSE_NASDAQ',
  sessionObservedAt: '2026-07-21T23:55:00Z', sessionValidUntil: '2026-07-22T00:15:00Z',
});

const now = new Date('2026-07-22T00:00:00Z');
const view = buildArgusTodayView({ now, selectionMode: 'AUTO', dataQuality: 'LIVE',
  calendar: { JP: state('JP', 'MORNING_SESSION'), US: state('US', 'CLOSED') },
  canonicalDecision: canonical('WAIT', 'EVALUATED', null),
  events: [1, 2, 3, 4].map((n) => ({ id: String(n), code: `E${n}`, title: `Event ${n}`,
    at: `2026-07-${22 + n}T00:00:00Z`, impact: 'high' })),
  holdings: [{ symbol: 'AAA', name: 'A', rank: 2, reasonJa: 'x', statusJa: 'watch' },
    { symbol: 'AAA', name: 'A', rank: 1, reasonJa: 'y', statusJa: 'review' }],
});
check('canonical SDA is the sole Today action', view.finalAction === 'WAIT'
  && view.canonicalDecision.decisionId.startsWith('sda-')
  && !Object.prototype.hasOwnProperty.call(view, 'decisions'));
check('Seven Sign null remains visibly data-gated', view.actionScore === null
  && view.canonicalDecision.sevenSign.status === 'DATA_GATED');
check('Today caps events and deduplicates owner priorities', view.comingEvents.length === 3
  && view.holdingsReview.length === 1 && view.holdingsReview[0].reasonJa === 'y');
check('legacy-looking extra inputs cannot override canonical action',
  buildArgusTodayView({ ...view, now, selectionMode: 'JP', dataQuality: 'LIVE',
    baseSignal: 'ENTER', aiFinalAction: 'BUY', canonicalDecision: canonical('EXIT', 'EVALUATED', 1) }).finalAction === 'EXIT');

const CAL_NOW = Date.parse('2026-07-22T00:00:00Z');
check('AUTO delegates to canonical JP open state',
  selectAutoMarket({ JP: state('JP', 'MORNING_SESSION'), US: state('US', 'PRE_MARKET') }, CAL_NOW) === 'JP');
check('AUTO delegates to canonical US open state',
  selectAutoMarket({ JP: state('JP', 'CLOSED'), US: state('US', 'REGULAR') }, CAL_NOW) === 'US');
check('holiday sessions never become open',
  buildArgusTodayView({ now, selectionMode: 'JP', dataQuality: 'LIVE',
    calendar: { JP: state('JP', 'HOLIDAY_CLOSED', false) }, canonicalDecision: canonical() })
    .sessionLamps.find((row) => row.key === 'JP').active === false);

const bars = Array.from({ length: 25 }, (_, index) => ({
  date: `2026-06-${String(index + 1).padStart(2, '0')}`, open: 100 + index,
  high: 102 + index, low: 99 + index, close: 100 + index, volume: 1000, atr14: 2,
}));
const projection = buildTodayProjection({ symbol: '1321', label: '日経225 ETF',
  asOf: '2026-07-21', status: 'live', bars, zones: [
    { id: 'support', center: 95, lower: 94, upper: 96, status: 'active' },
    { id: 'resistance', center: 130, lower: 129, upper: 131, status: 'active' },
  ] }, 'WAIT', 5, Date.parse('2026-07-22T12:00:00Z'));
check('projection is evidence only and uses observed bars', projection.current === 124
  && projection.upside === 130 && projection.downside === 95
  && projection.directionProbabilities === null);

const newsBase = { source: 'official', url: 'https://example.test/item', publishedAt: 1,
  major: true, relevant: true, translationStatus: 'translated', corroboration: 'official' };
check('Today news is relevant, corroborated and capped', selectTodayNews([
  { ...newsBase, id: 'held', titleJa: 'NVDA 重大開示', linkedSymbols: ['NVDA'], scope: 'holding' },
  { ...newsBase, id: 'noise', titleJa: '無関係な開示', scope: 'other' },
], ['NVDA']).map((row) => row.id).join(',') === 'held');
const diverseNews = selectTodayNews([
  { ...newsBase, id: 'official', titleJa: '日銀が政策金利を据え置き', source: '日銀', scope: 'global' },
  { ...newsBase, id: 'wire1', titleJa: '米株は国債利回りを受け反発', source: 'Reuters',
    scope: 'index', tier: 'wire', corroboration: 'single', translationStatus: 'summarized' },
  { ...newsBase, id: 'wire2', titleJa: 'イラン制裁で原油供給懸念', source: 'Reuters',
    scope: 'global', tier: 'wire', corroboration: 'single', translationStatus: 'summarized' },
  { ...newsBase, id: 'wire3', titleJa: '米株市場の別記事', source: 'Reuters',
    scope: 'index', tier: 'wire', corroboration: 'single', translationStatus: 'summarized' },
  { ...newsBase, id: 'english', titleJa: 'translation pending', source: 'CNBC',
    scope: 'index', tier: 'wire', corroboration: 'single', translationStatus: 'pending' },
], []);
check('Today admits labeled wire evidence while enforcing translation and source diversity',
  diverseNews.map((row) => row.id).join(',') === 'official,wire1,wire2'
  && diverseNews.filter((row) => row.source === 'Reuters').length === 2);

const panel = fs.readFileSync(path.join(root, 'src/components/today/ArgusTodayPanel.tsx'), 'utf8');
const heroAt = panel.indexOf('at-primary-hero');
const evidenceAt = panel.indexOf('at-evidence card');
check('Primary Action is rendered before collapsed evidence', heroAt >= 0 && evidenceAt > heroAt
  && panel.includes('<details className="at-evidence card">'));
check('data-gated Seven Sign is not imputed to level four',
  panel.includes("view.actionScore == null ? '— / 7'")
  && !panel.includes('candidateLevel ?? 4'));
check('Today contains no legacy market decision projection',
  !panel.includes('view.decisions') && !fs.existsSync(path.join(root, 'src/domain/argusEngine.ts'))
  && !fs.existsSync(path.join(root, 'src/domain/commandSummary.ts')));

// v13.5.53 (owner 2026-09-04: 「イベントが何もないことはないはず」). An empty
// schedule has two causes — nothing is scheduled, or the important-events feed
// never returned — and on that day it was always the second. The panel must
// carry the distinction rather than asserting an empty calendar.
const emptyEventsInput = { now, selectionMode: 'AUTO', dataQuality: 'LIVE',
  calendar: { JP: state('JP', 'MORNING_SESSION'), US: state('US', 'CLOSED') },
  canonicalDecision: canonical('WAIT', 'EVALUATED', null), events: [] };
const unknownView = buildArgusTodayView({ ...emptyEventsInput, eventsAuthorityUnknown: true });
const knownView = buildArgusTodayView({ ...emptyEventsInput, eventsAuthorityUnknown: false });
check('an unread event feed is distinguishable from an empty calendar',
  unknownView.eventsAuthorityUnknown === true && knownView.eventsAuthorityUnknown === false
  && unknownView.nextEvent === null && knownView.nextEvent === null);
check('Today never claims an empty calendar it could not read',
  panel.includes('view.eventsAuthorityUnknown')
  && panel.includes('予定がないという意味ではありません'));

// v13.5.54 (owner 2026-09-04: 「イベントが出たばかりなので米雇用統計が出てない」).
// The forward filter drops a release the instant it fires, which is exactly
// when the owner needs it. A release still inside its 72h lifecycle window is
// surfaced separately; HISTORY ages out on its own.
{
  const base = { now, selectionMode: 'AUTO', dataQuality: 'LIVE',
    calendar: { JP: state('JP', 'MORNING_SESSION'), US: state('US', 'CLOSED') },
    canonicalDecision: canonical('WAIT', 'EVALUATED', null) };
  const fired = new Date(now.getTime() - 6 * 3600_000).toISOString();
  const ahead = new Date(now.getTime() + 4 * 86_400_000).toISOString();
  const view = buildArgusTodayView({ ...base, events: [
    { id: 'nfp', code: 'NFP', title: 'US Employment Situation', at: fired,
      impact: 'high', lifecycleTier: 'MONITORING' },
    { id: 'auction', code: 'AUCTION', title: 'US Treasury 10-Year Auction',
      at: ahead, impact: 'high', lifecycleTier: 'NEXT' },
  ] });
  check('a release that just fired stays on Today',
    view.releasedEvent?.code === 'NFP' && view.nextEvent?.code === 'AUCTION');
  const aged = buildArgusTodayView({ ...base, events: [
    { id: 'pce', code: 'PCE', title: 'US PCE', impact: 'high',
      at: new Date(now.getTime() - 8 * 86_400_000).toISOString(),
      lifecycleTier: 'HISTORY' },
  ] });
  check('an aged-out release is not resurrected', aged.releasedEvent === null);
  check('Today renders the released release', panel.includes('view.releasedEvent'));
}

if (failed) process.exit(1);
console.log('argus-engine.test: all checks passed');
