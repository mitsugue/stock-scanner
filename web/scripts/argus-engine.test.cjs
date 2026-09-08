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
  formatEventTime,
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
// v13.5.60: the COMING 30D row carries the coming month (bounded), not three rows.
check('Today bounds coming events to the month and deduplicates owner priorities',
  view.comingEvents.length >= 3 && view.comingEvents.length <= 12
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

// v13.5.54 (owner 2026-09-04). Treasury auctions and BOJ meeting days publish a
// DATE but no announcement time. Mapping those to `at: null` dropped them from
// every forward-looking surface, so Today named CPI as the next event while the
// market brief on the same screen named Monday's 10-Year auction. A date-only
// event is anchored to the END of its JST day so it stays ahead of us for the
// whole day, and it must never be rendered with an invented clock time.
{
  const base = { selectionMode: 'AUTO', dataQuality: 'LIVE',
    calendar: { JP: state('JP', 'MORNING_SESSION'), US: state('US', 'CLOSED') },
    canonicalDecision: canonical('WAIT', 'EVALUATED', null) };
  const dateOnlyAt = '2026-07-22T23:59:59+09:00';
  const auction = { id: 'auction', code: 'AUCTION', title: 'US Treasury 10-Year Auction',
    at: dateOnlyAt, dateOnly: true, impact: 'high', lifecycleTier: 'NEXT' };
  const cpi = { id: 'cpi', code: 'CPI', title: 'US CPI', impact: 'high',
    lifecycleTier: 'NEXT', at: '2026-07-25T12:30:00Z' };
  // 2026-07-22T00:00Z is 09:00 JST on the auction's own day — mid-day, after a
  // start-of-day anchor would already have expired.
  const sameDay = buildArgusTodayView({ ...base, now, events: [auction, cpi] });
  check('a date-only event stays visible on the day it lands',
    sameDay.nextEvent?.code === 'AUCTION');
  check('a date-only event never renders an invented clock time',
    formatEventTime(dateOnlyAt, true) === '7/22'
    && formatEventTime(dateOnlyAt, false).includes(':'));
  const nextDay = buildArgusTodayView({ ...base, events: [auction, cpi],
    now: new Date('2026-07-23T00:00:00Z') });
  check('a date-only event drops out once its day has passed',
    nextDay.nextEvent?.code === 'CPI');
  const commandCenter = fs.readFileSync(
    path.join(root, 'src/routes/CommandCenter.tsx'), 'utf8');
  check('the event mapping anchors a date-only row instead of discarding it',
    commandCenter.includes('T23:59:59+09:00') && commandCenter.includes('dateOnly'));
}

// v13.5.54 (owner 2026-09-04: 「データ一部不足とは何か？全て与えているはず」).
// PARTIAL was a bare boolean over eight unrelated ARGUS-side conditions, so
// the screen could say 「一部不足」 without ever saying what. The reasons now
// travel with the status, and a LIVE status carries none.
{
  const base = { now, selectionMode: 'AUTO', events: [],
    calendar: { JP: state('JP', 'MORNING_SESSION'), US: state('US', 'CLOSED') },
    canonicalDecision: canonical('WAIT', 'EVALUATED', null) };
  const partial = buildArgusTodayView({ ...base, dataQuality: 'PARTIAL',
    dataQualityReasonCodes: ['fx_authority_missing', 'visibility_limited'] });
  check('a partial data status carries the reasons it is partial',
    partial.dataStatus.label === '一部不足'
    && partial.dataQualityReasonCodes.join(',')
      === 'fx_authority_missing,visibility_limited');
  const live = buildArgusTodayView({ ...base, dataQuality: 'LIVE',
    dataQualityReasonCodes: ['fx_authority_missing'] });
  check('a LIVE status never carries a shortfall reason',
    live.dataStatus.label === '正常' && live.dataQualityReasonCodes.length === 0);
  check('every partial reason has Japanese the owner can act on',
    ['watchlist_polling_partial', 'important_events_unread', 'downside_unread',
      'flow_authority_stale', 'supply_demand_authority_stale',
      'fx_authority_missing', 'session_authority_missing',
      'quote_authority_missing', 'visibility_limited']
      .every((code) => panel.includes(`${code}:`)));
  const intel = fs.readFileSync(path.join(root, 'src/hooks/useAssetIntel.ts'), 'utf8');
  check('the partial flag is derived from the named reasons, not beside them',
    intel.includes('const isPartial = partialReasonCodes.length > 0;'));
}

// v13.5.57: display series = index, decision subject = verified ETF. The
// canonical contract attribute and the projection heading must both carry the
// SUBJECT (1321/1306/SPY/QQQ), or the release acceptance — which selects each
// subject and checks heading/instrument — cannot bind the verified snapshot.
{
  const commandCenter = fs.readFileSync(path.join(root, 'src/routes/CommandCenter.tsx'), 'utf8');
  check('the canonical contract names the decision subject, not the drawn series',
    panel.includes('data-canonical-instrument={selectedSymbol}')
    && !panel.includes('data-canonical-instrument={projection?.symbol'));
  check('the index heading still names the decision subject',
    commandCenter.includes('label: `${base.label}・判断の正本 ${selectedInstrument[effectiveMarket]}`'));
}

// v13.5.59 (owner iPhone review). Reading order top-down, one MARKET SIGNALS
// block, company names on the Tachibana rows, event tap jumps to the events
// summary, released events are not 「この先」, and the AI-scenario line says
// what is actually the case instead of "waiting".
{
  const kpis = panel.indexOf('className="at-kpis"');
  const seven = panel.indexOf('<details className="at-seven"');
  const nextEvent = panel.indexOf('aria-label="NEXT EVENT"');
  const market = panel.indexOf('className="at-market card"');
  const context = panel.indexOf('className="at-event card at-context"');
  const evidence = panel.indexOf('<details className="at-evidence card">');
  check('confidence and data status sit under the decision, before the signals',
    kpis > 0 && seven > 0 && kpis < seven);
  check('reading order: next event → market → reference view → evidence',
    nextEvent < market && market < context && context < evidence);
  check('the market view and news axis left the hero article',
    !/<MarketBriefCard \/>\s*<MarketViewStrip \/>/.test(panel));
  // v13.5.60 (owner): Today's Tachibana line names the company, never the code,
  // and the per-symbol rows moved to Holdings.
  check('Tachibana line names the company and never renders a code list',
    panel.includes("jpNames?.[mover.symbol] ?? '保有銘柄'") && !panel.includes('jpDisplay(')
    && !panel.includes('mv-tachibana__rows'));
  check('tapping an event jumps to the events summary',
    panel.includes("document.getElementById('important-events')"));
  const css = fs.readFileSync(path.join(root, 'src/components/today/ArgusToday.css'), 'utf8');
  check('the signals header never wraps on a phone',
    css.includes('.at-seven summary small, .at-seven summary > b { white-space:nowrap;'));
  const events = fs.readFileSync(path.join(root, 'src/components/dashboard/ImportantEventsCard.tsx'), 'utf8');
  check('a released event is never listed as upcoming',
    events.includes("RELEASED_TIERS = new Set(['MONITORING', 'RECENT', 'HISTORY'])")
    && events.includes('!RELEASED_TIERS.has(String(e.lifecycleTier'));
  const { eventAiScenarioNote } = require(path.join(root, 'src/lib/eventAiScenarioNote.ts'));
  check('the AI-scenario line names the cost policy when event AI is off',
    eventAiScenarioNote({ eventOptIn: false, mode: 'SCHEDULED_AI' }).includes('コスト方針')
    && eventAiScenarioNote({ eventOptIn: true, mode: 'SCHEDULED_AI' }).includes('次のcron予定枠（計算値）')
    && eventAiScenarioNote(null) === 'AIシナリオ 生成待ち…');
}

// v13.5.60 (owner iPhone review 2026-09-07). News and market risk sit directly
// under the decision as one tappable block of up to five rows; DATA reasons
// open on tap; MACRO colours follow the value direction; JP context (Tachibana
// line + 需給) stays together; the Alerts page has three named anchors.
{
  const hero = panel.indexOf('aria-label="A.R.G.U.S. Primary Action"');
  const newsTop = panel.indexOf('className="at-event card at-news-top"');
  const nextEvent = panel.indexOf('aria-label="NEXT EVENT"');
  check('news and market risk sit between the decision and NEXT EVENT',
    hero > 0 && newsTop > hero && newsTop < nextEvent);
  check('the news block lists up to five rows and each row jumps to its Alerts anchor',
    panel.includes('const NEWS_ROWS_CAP = 5;') && panel.includes('newsRows.slice(0, NEWS_ROWS_CAP)')
    && panel.includes("openNewsDetails(`news-${row.id}`)")
    && panel.includes("document.getElementById('news-intel')"));
  check('the old single-item risk and news cards are gone',
    !panel.includes('title="市場リスク"') && !panel.includes('title="重大ニュース"'));
  check('DATA reasons open on tap instead of occupying the decision area',
    panel.includes('<details className="at-data-detail">'));
  check('MACRO colour follows the value direction',
    panel.includes('className={macroTone(move)}') && panel.includes("move.value > move.previous ? 'is-positive' : 'is-negative'"));
  check('需給 sits with the market view of the same market',
    panel.indexOf('className="at-positioning"') > panel.indexOf('className="at-event card at-context"')
    && !panel.includes('title={`${view.selectedMarket} 需給`}'));
  check('UNCLEAR news direction is named as a verdict',
    panel.includes("UNCLEAR: '方向判定不能'") && panel.includes('このニュースからは上下を決めない'));
  const alerts = fs.readFileSync(path.join(root, 'src/routes/NotificationsPage.tsx'), 'utf8');
  const newsPanel = fs.readFileSync(path.join(root, 'src/components/notifications/NewsAlertsPanel.tsx'), 'utf8');
  const notifPanel = fs.readFileSync(path.join(root, 'src/components/NotificationPanel.tsx'), 'utf8');
  check('the Alerts page is three named sections with stable anchors',
    alerts.indexOf('<NewsAlertsPanel />') < alerts.indexOf('<NotificationPanel />')
    && alerts.indexOf('<NotificationPanel />') < alerts.indexOf('<ImportantEventsCard />')
    && newsPanel.includes("NEWS_ALERTS_SECTION_ID = 'news-intel'")
    && notifPanel.includes('id="asset-alerts"') && notifPanel.includes('銘柄・判断の変化')
    && !notifPanel.includes('端末内の変化'));
  const viewSrc = fs.readFileSync(path.join(root, 'src/domain/argusTodayView.ts'), 'utf8');
  check('COMING 30D is bounded by the month, not truncated to three',
    viewSrc.includes('export const COMING_EVENTS_CAP = 12') && !viewSrc.includes('.slice(0, 3).map((x) => x.event)'));
  const eventsCard = fs.readFileSync(path.join(root, 'src/components/dashboard/ImportantEventsCard.tsx'), 'utf8');
  check('the Alerts upcoming list covers the coming month',
    eventsCard.includes('e.daysUntil <= 31') && !eventsCard.includes('.slice(0, 4);'));
}

// v13.5.60 (owner 2026-09-07). A headline index ETF that is not in Holdings is
// NOT HELD (owner context complete), so the SDA evaluates instead of data-
// gating; and while the JP exchange is closed an expired flow/supply budget
// is the previous session's value, not a shortfall.
{
  const intel = fs.readFileSync(path.join(root, 'src/hooks/useAssetIntel.ts'), 'utf8');
  check('headline proxies outside Holdings are NOT_HELD, not UNKNOWN',
    intel.includes("decisionSubjects.push({ ...head, quantity: null, headlineProxy: true })")
    && intel.includes("asset.headlineProxy && asset.quantity == null ? 'NOT_HELD'"));
  check('closed-session previous values are notes, not shortfalls',
    intel.includes("flowState.authority === 'expired' && jpSessionNow !== '' && !jpExchangeOpen")
    && intel.includes("'flow_previous_value_closed_session'"));
  const base = { now, selectionMode: 'AUTO', events: [],
    calendar: { JP: state('JP', 'MORNING_SESSION'), US: state('US', 'CLOSED') },
    canonicalDecision: canonical('WAIT', 'EVALUATED', null) };
  const noted = buildArgusTodayView({ ...base, dataQuality: 'LIVE',
    dataQualityNotes: ['flow_previous_value_closed_session'] });
  check('a note travels with a LIVE status without becoming a shortfall',
    noted.dataStatus.label === '正常' && noted.dataQualityReasonCodes.length === 0
    && noted.dataQualityNotes.join(',') === 'flow_previous_value_closed_session');
  check('the note has Japanese the owner can read',
    panel.includes("flow_previous_value_closed_session: '資金フローは休場中のため前回値'")
    && panel.includes("flow_no_records_now:"));
  check('a fresh but empty flow feed is a note, not 「鮮度切れ」',
    intel.includes("const flowEmptyFresh = flowState.authority === 'unavailable' && !flowState.error")
    && intel.includes("!flowPreviousValue && !flowEmptyFresh ? 'flow_authority_stale'"));
}

if (failed) process.exit(1);
console.log('argus-engine.test: all checks passed');

// v13.5.61 (owner iPhone review 2026-09-07, second pass). No codes on Today
// (names in words), a Japan block and a US block with MACRO inside the US one,
// the risk basis named, the BUY gate spelled out, a flat MACRO change is 「→」,
// the last good decision evidence survives a failed fetch, and digest mail
// headlines are shown by their first item.
{
  check('the decision subject is named in words, never by code',
    panel.includes("subjectDisplayName(view.canonicalDecision.subject?.instrumentId")
    && panel.includes('<small className="at-index-type">連動ETF</small>')
    && !panel.includes('<small className="at-index-type">{instrument.symbol}</small>'));
  check('owner priorities show the company name',
    panel.includes("<b>{item.name?.trim() || item.symbol}</b>"));
  const jpBlock = panel.indexOf('data-market="JP"');
  const usBlock = panel.indexOf('data-market="US"');
  const macroInUs = panel.indexOf('className="at-rows at-macro-rows"');
  check('Japan block precedes the US block and MACRO lives in the US block',
    jpBlock > 0 && usBlock > jpBlock && macroInUs > usBlock && !panel.includes('<Compact title="MACRO">'));
  check('the BUY gate is spelled out for the owner',
    panel.includes('BUYが出る条件') && panel.includes('検証済み買い成立レジストリ'));
  const viewSrc2 = fs.readFileSync(path.join(root, 'src/domain/argusTodayView.ts'), 'utf8');
  check('the view carries both markets\' positioning and the subject names',
    viewSrc2.includes('positioningByMarket') && viewSrc2.includes("'1321': '日経225 ETF'"));
  const ap = fs.readFileSync(path.join(root, 'src/domain/actionPriority.ts'), 'utf8');
  check('the held-risk reason names its basis',
    ap.includes('positionRiskBasisJa(i)') && ap.includes("drawdown: (i) => `含み損"));
  const cc = fs.readFileSync(path.join(root, 'src/routes/CommandCenter.tsx'), 'utf8');
  check('a flat MACRO change is an arrow to the right',
    cc.includes("(change ?? 0) < 0 ? '↓' : '→'") && cc.includes("'横ばい'"));
  const de = fs.readFileSync(path.join(root, 'src/hooks/useDecisionEvidence.ts'), 'utf8');
  check('the last good decision evidence is kept on the device',
    de.includes("LAST_GOOD_KEY = 'argus.decisionEvidence.lastGood.v1'")
    && de.includes('writeLastGoodDecisionEvidence(next)') && de.includes('readLastGoodDecisionEvidence()'));
  const { displayNewsHeadline, isDigestHeadline } = require(path.join(root, 'src/lib/newsHeadline.ts'));
  check('a digest mail headline is shown by its first item',
    displayNewsHeadline('日経ニュースメール 9/7 夕版 ━ 注目ニュース ━━━━━━━ ◆円半年ぶりに154円台に上昇 円安抑止へ思惑（有料会員限定） ◆次の記事') === '円半年ぶりに154円台に上昇 円安抑止へ思惑'
    && displayNewsHeadline('緩和的な財政政策') === '緩和的な財政政策'
    && isDigestHeadline('日経ニュースメール 9/7 夕版 ━ ◆x') === true && isDigestHeadline('普通の見出し') === false);
  const ai = fs.readFileSync(path.join(root, 'src/hooks/useAssetIntel.ts'), 'utf8');
  check('crypto quotes try the memo id and the symbol default id',
    ai.includes('SYMBOL_TO_COINGECKO[a.symbol.toUpperCase()]') && ai.includes("positionRiskTypes: riskTypesBySym.get(sym) ?? []"));
}

// v13.5.62 (GPT review 2026-09-07). Every registered symbol gets decision
// evidence (batches of eight), the WAIT kind and the confidence basis are
// named, the projection says 頻度 not 確率 and states its conditioning, the
// brief's 成立x/7 comes from the same document as the header, the AI note
// gives the real reason and the next slot, event times share one format, the
// most severe risk per symbol wins, and quotes carry one plain freshness line.
{
  const de = fs.readFileSync(path.join(root, 'src/hooks/useDecisionEvidence.ts'), 'utf8');
  check('decision evidence is requested for every registered symbol in batches of eight',
    !de.includes('MAX_SYMBOLS_TOTAL') && de.includes("from '../lib/decisionEvidenceBatches'")
    && de.includes('for (const batch of decisionEvidenceBatches(desiredSymbols))'));
  const { decisionEvidenceBatches } = require(path.join(root, 'src/lib/decisionEvidenceBatches.ts'));
  const batches = decisionEvidenceBatches(Array.from({ length: 19 }, (_, i) => `S${i}`));
  check('batches are eight wide and cover every symbol',
    batches.length === 3 && batches[0].length === 8 && batches[2].length === 3);
  const { waitKindJa, confidenceBasisJa, formatEventWhenJa } = require(path.join(root, 'src/domain/argusTodayView.ts'));
  check('the three WAITs are named',
    waitKindJa({ status: 'DATA_GATED', primaryAction: 'WAIT' }).includes('データ不足')
    && waitKindJa({ status: 'EVALUATED', primaryAction: 'WAIT', guidance: { riskConstraint: 'BLOCK_BUY' } }).includes('リスク制約')
    && waitKindJa({ status: 'EVALUATED', primaryAction: 'WAIT', guidance: { riskConstraint: 'NONE' } }).includes('買い条件が未成立')
    && waitKindJa({ status: 'EVALUATED', primaryAction: 'HOLD' }) === null);
  check('the confidence percentage states its basis',
    confidenceBasisJa({ status: 'DATA_GATED', primaryAction: 'WAIT', confidence: { valueBps: 2500 } }).includes('データ不足時の上限')
    && confidenceBasisJa({ status: 'EVALUATED', primaryAction: 'WAIT', confidence: { valueBps: 4000 } }).includes('リスク上限 40%')
    && confidenceBasisJa({ status: 'EVALUATED', primaryAction: 'WAIT', confidence: { valueBps: 4500 } }).includes('基準値'));
  const when = formatEventWhenJa('2026-09-11T12:30:00Z', false, new Date('2026-09-07T10:00:00Z'));
  const dateOnly = formatEventWhenJa('2026-09-09T23:59:59+09:00', true, new Date('2026-09-07T10:00:00Z'));
  check('one event-time format for Today and Alerts',
    when.whenJa === '9/11 21:30 JST' && when.relativeJa === 'あと4日' && dateOnly.whenJa === '9/9（時刻未定）');
  const events = fs.readFileSync(path.join(root, 'src/components/dashboard/ImportantEventsCard.tsx'), 'utf8');
  check('the Alerts card uses the shared event-time format',
    events.includes("formatEventWhenJa(utc, false)") && !events.includes("時刻未確認`"));
  check('the projection is labelled as frequency and opens its method detail on tap',
    panel.includes('類似局面の頻度 — 検証済み確率ではない') && panel.includes('<details className="at-proj-detail"')
    && !panel.includes("'実測と校正済み根拠'"));
  const viewSrc3 = fs.readFileSync(path.join(root, 'src/domain/argusTodayView.ts'), 'utf8');
  check('a fallback to the unconditioned analog search is stated as such',
    viewSrc3.includes('需給・トレンド条件なし（無条件の類似局面）') && viewSrc3.includes('無条件の類似局面へ切替中'));
  check('the brief chart chip is rendered from the market-view document with its cutoff',
    panel.includes('const chartChip = signals ?') && panel.includes('<MarketBriefCard signals={topSignals'));
  const { eventAiScenarioNote, nextEventAiSlot } = require(path.join(root, 'src/lib/eventAiScenarioNote.ts'));
  const slot = nextEventAiSlot(new Date('2026-09-07T10:40:00Z'));
  check('the AI note names the real reason and the next scheduled slot',
    slot.toISOString() === '2026-09-07T12:35:00.000Z'
    && eventAiScenarioNote({ eventOptIn: true, mode: 'SCHEDULED_AI', lastExecutionReason: 'headline_translation' }, new Date('2026-09-07T10:40:00Z')).includes('21:35')
    && eventAiScenarioNote({ eventOptIn: true, mode: 'SCHEDULED_AI', lastExecutionReason: 'headline_translation' }, new Date('2026-09-07T10:40:00Z')).includes('headline_translation'));
  const { mostSevereRiskBySymbol } = require(path.join(root, 'src/domain/positionExposure.ts'));
  const worst = mostSevereRiskBySymbol([
    { symbol: '5803', riskLevel: 'high', riskType: 'drawdown', whyJa: '', checkNextJa: '' },
    { symbol: '5803', riskLevel: 'medium', riskType: 'event_risk', whyJa: '', checkNextJa: '' }]);
  check('the most severe risk per symbol wins', worst.get('5803') === 'high');
  const ai = fs.readFileSync(path.join(root, 'src/hooks/useAssetIntel.ts'), 'utf8');
  check('every risk map in the intel hook uses the most severe entry',
    (ai.match(/mostSevereRiskBySymbol\(positionExposure\.risks\)/g) || []).length === 3
    && !ai.includes('new Map(positionExposure.risks.map((r) => [r.symbol, r.riskLevel]))'));
  // ── v13.5.63 (GPT additional items 1/3/4) ──
  const richNote = eventAiScenarioNote({ eventOptIn: true, mode: 'SCHEDULED_AI', lastExecutionPurpose: 'headline_translation',
    lastExecutionAt: '2026-09-07T03:00:00Z', openaiKeyConfigured: true,
    lastSkip: { purpose: 'event_analysis', reason: 'scheduled_daily_budget_exhausted', at: '2026-09-07T04:35:00Z' },
    scheduledLane: { dailyBudgetUsd: 2, spentTodayUsd: 1.6, eventRemainingUsd: 0.4, eventRunsToday: 0, eventRunsPerDay: 6, eventLaneOpen: true } },
  new Date('2026-09-07T10:40:00Z'));
  check('the AI note separates key, permission, budget, last run, last refusal and the computed slot',
    richNote.includes('鍵 設定済') && richNote.includes('実行許可 ON') && richNote.includes('残$0.40（本日$1.60/$2.00）')
    && richNote.includes('直近のAI実行: headline_translation 9/7 12:00 JST') && richNote.includes('日次予算を使い切った')
    && richNote.includes('次のcron予定枠（計算値） 9/7 21:35 JST'));
  const { eventAiRunMetaJa } = require(path.join(root, 'src/lib/eventAiScenarioNote.ts'));
  check('a saved scenario names the model that answered and its cost',
    eventAiRunMetaJa('2026-09-07T12:35:10Z', { requestedModel: 'gpt-6-astra', returnedModel: 'gpt-6-astra', estUsd: 0.0438, inputTokens: 1180, outputTokens: 640 })
      === '生成 9/7 21:35 JST · モデル gpt-6-astra（応答一致） · 推定 $0.0438 · 1180+640 tok'
    && eventAiRunMetaJa('2026-07-08T14:57:31Z', null).includes('記録なし'));
  const noSho = [panel, viewSrc3].every((src) => !/SHO[条状買]/.test(src));
  check('no personal method name in the owner-visible Today strings', noSho);
  check('MARKET SIGNALS names the Japanese inputs and the US conditioning when US is selected',
    panel.includes("const usSelected = view.selectedMarket === 'US'") && panel.includes('日本市場の値')
    && panel.includes('米国の条件付けはVIX水準・VIX10日変化・対SPY相対力') && panel.includes("市場状態で条件付け".length ? '' : '')
    && viewSrc3.includes("vixLevel: 'VIX水準', vixChange10: 'VIX10日変化', rs20: '対SPY相対力'"));
  check('the brief chart chip does not borrow the Japanese count for the US market',
    panel.includes("market === 'US' ? '米国: 7条件は適用外") && panel.includes('signals={topSignals && !usSelected ?'));
  const { deskCoverage, deskCoverageJa, deskCoverageDetailJa } = require(path.join(root, 'src/domain/deskCoverage.ts'));
  const cov = deskCoverage({
    assets: [{ symbol: '5803', market: 'JP' }, { symbol: '1321', market: 'JP' }, { symbol: 'NVDA', market: 'US' }, { symbol: 'BTC', market: 'CRYPTO' }],
    pricedSymbols: new Set(['5803', '1321', 'BTC']), evidenceSubjects: { '1321': {}, '5803': {} },
    displayedSymbols: new Set(['5803', '1321', 'NVDA', 'BTC']), requestedEvidence: ['1321', '5803'] });
  check('registered / priced / evidenced / shown are reconciled per symbol',
    deskCoverageJa(cov) === '登録 4 · 価格 3/4 · 判断根拠 2/3 · 表示 4/4' && cov.complete === false
    && deskCoverageDetailJa(cov).join('|').includes('価格未取得: NVDA') && deskCoverageDetailJa(cov).join('|').includes('未要求（取得キューの外）: NVDA'));
  const hookSrc = fs.readFileSync(path.join(root, 'src/hooks/useDecisionEvidence.ts'), 'utf8');
  check('no device-side cap on the evidence queue', !hookSrc.includes('MAX_SYMBOLS_TOTAL') && hookSrc.includes('requested: [...desiredSymbols]'));
  const wl = fs.readFileSync(path.join(root, 'src/routes/Watchlist.tsx'), 'utf8');
  check('the desk header carries the reconciliation line', wl.includes('data-argus-contract="desk-coverage-v1"') && wl.includes('deskCoverageDetailJa(coverage)'));
  // ── v13.5.65 (stabilization item 5) ──
  const { shoInputsJa } = require(path.join(root, 'src/domain/argusTodayView.ts'));
  check('each conditioning input names its joined period or the reason it did not join',
    shoInputsJa({ credit: { status: 'joined', periodEnd: '2026-08-28', availableFrom: '2026-09-02' }, vix: { status: 'joined', date: '2026-09-05' }, us: { status: 'joined', date: '2026-09-04' } }, false)
      === '信用残 8/28週分（9/2公表）・VIX 9/5・対SPY 9/4'
    && shoInputsJa({ credit: { status: 'stale_beyond_window', periodEnd: '2026-07-10', ageDays: 59, maxDays: 45 } }, false).includes('59日前で結合窓（45日）外')
    && shoInputsJa({ credit: { status: 'not_yet_available', newestPeriodEnd: '2026-09-04', availableFrom: '2026-09-09' } }, false).includes('9/9公表予定（更新間隔）')
    && shoInputsJa({ credit: { status: 'no_rows' } }, false).includes('取込失敗または未設定')
    && shoInputsJa({ credit: { status: 'not_applicable' }, vix: { status: 'joined', date: '2026-09-05' } }, true) === '信用残 米国は対象外・VIX 9/5'
    && shoInputsJa(null, false) === null);
  const { deskCoverageJa: coverageJa2 } = require(path.join(root, 'src/domain/deskCoverage.ts'));
  const cov2 = { registered: 3, priced: 3, evidence: 2, evidenceApplicable: 2, displayed: 3, missingPrice: [], missingEvidence: [], notRequested: [], notDisplayed: [], complete: true };
  check('the desk header says when the stored evidence is on screen during the first fetch',
    coverageJa2(cov2, { loading: true, generatedAt: '2026-09-07T22:00:00Z' }).includes('保存分 9/8 07:00 を表示中（更新取得中）')
    && coverageJa2(cov2, { loading: true, generatedAt: null }).endsWith('初回取得中')
    && coverageJa2(cov2, { loading: false, generatedAt: '2026-09-07T22:00:00Z' }) === '登録 3 · 価格 3/3 · 判断根拠 2/2 · 表示 3/3');
  check('Today shows the stored evidence time while the first fetch runs',
    panel.includes('data-argus-contract="stored-evidence-note-v1"') && panel.includes('decisionEvidence.loading && decisionEvidence.generatedAt'));
  const { quoteFreshnessJa } = require(path.join(root, 'src/domain/liveQuote.ts'));
  check('one plain freshness line per quote',
    quoteFreshnessJa({ delayClass: 'EOD', provider: 'jquants', sourceTimestamp: '2026-09-07' }) === '終値 09/07（jquants）'
    && quoteFreshnessJa({ delayClass: 'LIVE', provider: 'TACHIBANA', sourceTimestamp: '2026-09-07T01:00:00Z' }).startsWith('リアルタイム')
    && quoteFreshnessJa({ delayClass: 'UNKNOWN', provider: 'CoinGecko', sourceTimestamp: null }) === '取得時刻不明（CoinGecko）');
}
