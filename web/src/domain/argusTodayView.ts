import type { DataQuality } from './actionLevel';
import type { MarketCalendarState } from '../types/marketLedger';
import type { ChartBar, PriceZone } from '../types/chartIntelligence';
import {
  evaluateProbabilityTruth,
  unavailableProbabilityEvidence,
  type ProbabilityTruthEvidence,
  type ProbabilityTruthResult,
} from './probabilityTruth';
import { exactAuthorityEpoch } from './liveAuthority';
import { calendarDateExpiresAt, exactCalendarDate } from './liveQuote';
import { projectPlanningSession } from './positionPlan';
import type {
  PrimaryAction, SingleDecisionAuthorityResultV2,
} from './singleDecisionAuthority';

export type MarketSelectionMode = 'AUTO' | ArgusMarket;
export type ArgusMarket = 'JP' | 'US';
export interface ArgusFactor {
  key: string;
  state: '↑' | '→' | '↓' | '△' | '—' | 'JP' | 'US' | 'HIGH' | 'LOW';
  source?: string;
}

export interface TodayEventInput {
  id: string; code: string; title: string; at: string | null;
  impact: string; lifecycle?: string; descriptionJa?: string | null;
  /** v13.5.51 canonical lifecycle tier from the backend (NOW/NEXT/…/HISTORY). */
  lifecycleTier?: string | null;
  /**
   * v13.5.54: the source published a DATE but no announcement time, so `at`
   * is the end of that JST day and only the date may be displayed. Rendering
   * a clock time here would invent precision the source never gave us.
   */
  dateOnly?: boolean;
}
export interface TodayHoldingInput {
  symbol: string; name: string; rank: number; reasonJa: string; statusJa: string;
  isHeld?: boolean;
  impact?: 'Good' | 'Bad' | 'Neutral';
  actionJa?: string;
  checkNextJa?: string;
  whatWouldChangeJa?: string;
}
export interface TodayMoveInput {
  id: string; label: string; value: number; previous?: number | null;
  symbol?: string; market?: ArgusMarket;
  suffix?: string; directionLabel?: string; asOf?: string | null;
  status?: 'realtime' | 'delayed' | 'close' | string;
  history?: Array<{ date: string; value: number }>;
}
export interface TodayAttentionInput { id: string; label: string; time?: string | null; severity: number }
export interface TodayNewsInput {
  id: string; titleJa: string; source: string; url: string; publishedAt?: number | null;
  corroboration?: string;
}
export interface TodayNewsCardState {
  status: 'live' | 'unavailable' | 'missing_key';
  lastChecked: string | null; lastSuccessfulPollAt: string | null;
  fetchedCount: number; relevantCount: number; stale: boolean; failureClass?: string | null;
}
export interface TodayNewsCandidate extends TodayNewsInput {
  major: boolean; relevant?: boolean; translationStatus?: string;
  tier?: string; corroboration?: string; titleOriginal?: string;
  linkedSymbols?: string[]; scope?: 'holding' | 'watchlist' | 'index' | 'global' | 'other';
}
export interface TodayProjectionInput {
  symbol: string; label: string; asOf: string | null; status: string;
  authorityState?: 'current' | 'stale' | 'unavailable';
  bars: ChartBar[]; zones: PriceZone[]; timeframe?: 'daily' | 'weekly';
  quoteState?: 'RT' | 'D20' | 'CLOSE' | 'STALE'
    | 'realtime' | 'delayed' | 'close' | 'stale';
  sourceHistoryCount?: number;
  historyStart?: string | null; historyEnd?: string | null;
  instrumentId?: string; source?: string; availableFrom?: string | null;
  assetType?: string; proxyFor?: string | null; licenseStatus?: string;
  /** Index series disclosure — names the instrument the decision still anchors on. */
  disclosureJa?: string | null;
  eventMarkers?: Array<{ id: string; date: string; labelJa: string; kind: string }>;
  turningPoints?: Array<{ id: string; effectiveFrom: string; status: string; direction: string; facts: string[] }>;
  calibration?: { historyCount: number; calibrationVersion: string; horizons: Record<string, TodayCalibrationInput>;
    shoConditioning?: { requested?: boolean; currentFeatureKeys?: string[]; coverageDays?: number; sourceIssues?: string[] } | null };
  shortSelling?: TodayShortSellingSummary | null;
  failedRally?: TodayFailedRally | null;
}
export interface TodayCalibrationInput {
  horizon: number; rawOccurrenceCount: number; episodeCount: number; effectiveSampleCount: number;
  calibrationStatus: string; probabilities: { UP: number; RANGE: number; DOWN: number } | null;
  directionProbabilities?: { UP: number; RANGE: number; DOWN: number } | null;
  referenceDirectionProbabilities?: { UP: number; RANGE: number; DOWN: number } | null;
  levelProbabilities?: { upperTargetTouch: number | null; baseRangeClose: number | null;
    lowerTargetTouch: number | null; invalidationTouch: number | null } | null;
  brierScore?: number | null; confidenceInterval?: Record<string, { low: number; high: number }> | null;
  modelBrier?: number | null; baselineBrier?: number | null; brierSkill?: number | null;
  brierSkillConfidenceInterval?: { low: number | null; high: number | null } | null;
  calibrationError?: number | null; calibrationIntegrity?: string;
  calibrationDatasetHash?: string; calibrationVersion?: string;
  probabilityEligibility?: ProbabilityEligibility;
  probabilityTruthEvidence?: ProbabilityTruthEvidence;
  averageReactionDelay?: number | null;
  returnDistribution?: { q10: number | null; q25: number | null; median: number | null;
    q75: number | null; q90: number | null; meanMfe: number | null; meanMae: number | null };
  targetProbabilities?: { upperTargetTouch: number | null; baseRangeClose: number | null;
    lowerTargetTouch: number | null; invalidationTouch: number | null } | null;
  expectedValue?: { horizon: number; expectedReturn: number | null; medianReturn: number | null;
    q10: number | null; q90: number | null; expectedUpside: number | null;
    expectedDownside: number | null; rewardRisk: number | null };
}
export interface ProbabilityEligibility {
  eligible: boolean; reasonCodes: string[]; effectiveSample: number;
  modelBrier: number | null; baselineBrier: number | null; brierSkill: number | null;
  calibrationIntegrity: string; probabilitySum: number | null;
  calibrationVersion: string; datasetHash: string | null; evaluatedAt: string | null;
  contractVersion: string;
}
export interface TodayShortSellingSummary {
  status: string; historyStart: string | null; historyCount: number; latestDate?: string;
  missingReason?: string | null; latest: null | { date: string; totalShortRatio: number;
    previousDayDifference: number | null; average5: number | null; average20: number | null;
    rollingPercentile: number | null; source: string; availableFrom: string };
}
export interface TodayFailedRally {
  state: 'NONE' | 'WATCH' | 'CONFIRMED'; facts: string[]; probability: number | null;
  metrics: Record<string, number | null>;
  backtest: { rawOccurrenceCount: number; episodeCount: number; effectiveSampleCount: number;
    calibrationStatus: string; probability: number | null; outcomes: Record<string, unknown> };
}
export interface TodayProjection {
  symbol: string; instrumentId: string; label: string; asOf: string | null; current: number;
  history: Array<{ date: string; value: number; open: number; high: number; low: number;
    volume: number | null }>;
  baseLow: number; baseHigh: number; upside: number; downside: number;
  invalidation: number; support: { low: number; high: number; status: PriceZone['status'] } | null;
  resistance: { low: number; high: number; status: PriceZone['status'] } | null;
  horizon: string; horizonDays: 1 | 5 | 20; directionLabel: string;
  confidenceLabel: '低' | '中' | '高'; directionProbabilities: { UP: number; RANGE: number; DOWN: number } | null;
  referenceDirectionProbabilities: { UP: number; RANGE: number; DOWN: number } | null;
  calibrationStatus: string; rawSampleCount: number; episodeCount: number; effectiveSampleCount: number;
  modelBrier: number | null; baselineBrier: number | null; brierSkill: number | null;
  brierSkillConfidenceInterval: { low: number | null; high: number | null } | null;
  calibrationError: number | null; calibrationVersion: string | null; datasetHash: string | null;
  probabilityEligibility: ProbabilityEligibility;
  probabilityTruth: ProbabilityTruthResult;
  expectedValue: TodayCalibrationInput['expectedValue'] | null;
  levelProbabilities: TodayCalibrationInput['levelProbabilities']; reactionDelay: number | null;
  methodLabel: string; timeframeLabel: string; quoteState: string; sourceHistoryCount: number;
  historyStart: string | null; historyEnd: string | null;
  /** SHO conditioning transparency — which state dimensions conditioned today's analogs. */
  shoConditioningJa: string | null;
  source: string; availableFrom: string | null;
  assetType: string; proxyFor: string | null; licenseStatus: string;
  disclosureJa: string | null;
  forecastId: string; signalEpisodeIds: string[]; supportResistanceIds: string[]; eventIds: string[];
  eventMarkers: Array<{ id: string; date: string; labelJa: string; kind: string }>;
  turningPointMarkers: Array<{ id: string; date: string; direction: string; label: string }>;
  activeTurningPoint: { id: string; date: string; direction: string; label: string } | null;
  shortSelling: TodayShortSellingSummary | null; failedRally: TodayFailedRally | null;
}
export interface TodayPositioningRow {
  key: string; label: string; value: string; detail?: string;
  tone?: 'positive' | 'negative' | 'neutral';
}

export interface ArgusTodayInput {
  now: Date;
  selectionMode: MarketSelectionMode;
  calendar?: Record<string, MarketCalendarState> | null;
  dataQuality: DataQuality;
  /**
   * v13.5.54: WHY the data is partial, as a closed vocabulary of ARGUS-side
   * freshness/authority states. Never a claim about owner-supplied input.
   */
  dataQualityReasonCodes?: string[];
  globalRisk?: string | null;
  factors?: Partial<Record<ArgusMarket, ArgusFactor[]>>;
  events?: TodayEventInput[];
  /** True when the important-events feed could not be read this cycle. */
  eventsAuthorityUnknown?: boolean;
  marketMoves?: TodayMoveInput[];
  indexMoves?: TodayMoveInput[];
  macroMoves?: TodayMoveInput[];
  positioning?: Partial<Record<ArgusMarket, TodayPositioningRow[]>>;
  news?: TodayNewsInput[];
  newsCardState?: TodayNewsCardState;
  projection?: Partial<Record<ArgusMarket, TodayProjectionInput | null>>;
  selectedInstrument?: Partial<Record<ArgusMarket, string>>;
  attention?: TodayAttentionInput[];
  holdings?: TodayHoldingInput[];
  systemStatus?: { data: string; backup: string; rule: string };
  conciseAction?: string | null;
  conciseAvoid?: string | null;
  /** Sole final-action authority. Legacy market synthesis remains evidence only. */
  canonicalDecision: SingleDecisionAuthorityResultV2;
}

export interface ArgusTodayView {
  selectedMarket: ArgusMarket;
  selectionMode: MarketSelectionMode;
  sessionLamps: Array<{ key: string; label: string; active: boolean; tone: 'open' | 'standby' | 'closed' }>;
  nextEvent: TodayEventInput | null;
  /** Most recent release still inside its 72h lifecycle window, if any. */
  releasedEvent: TodayEventInput | null;
  comingEvents: TodayEventInput[];
  /**
   * The important-events feed could not be read, so an empty schedule
   * means "not known", never "nothing is scheduled".
   */
  eventsAuthorityUnknown: boolean;
  finalAction: PrimaryAction;
  /** Seven Sign candidate; null remains visibly DATA_GATED and is never imputed. */
  actionScore: number | null;
  confidence: number | null;
  dataStatus: { code: DataQuality; label: string; tone: 'ok' | 'warn' | 'bad' };
  /** v13.5.54: the specific reasons behind a non-LIVE dataStatus. */
  dataQualityReasonCodes: string[];
  globalRisk: string | null;
  marketPrice: number | null;
  range: { low: number; high: number } | null;
  invalidation: number | null;
  projection: TodayProjection | null;
  projectionsByHorizon: Partial<Record<'1D' | '5D' | '20D', TodayProjection>>;
  selectedInstrument: { symbol: string; instrumentId: string; label: string } | null;
  shortSellingSummary: TodayShortSellingSummary | null;
  failedRallyState: TodayFailedRally | null;
  factors: ArgusFactor[];
  permissions: { newEntry: boolean; add: boolean; hold: boolean };
  conciseAction: string | null;
  conciseAvoid: string | null;
  indexMoves: TodayMoveInput[];
  macroMoves: TodayMoveInput[];
  positioning: TodayPositioningRow[];
  news: TodayNewsInput[];
  newsCardState: TodayNewsCardState;
  directionProbabilities: TodayProjection['directionProbabilities'] | null;
  probabilityHorizon: number | null;
  brierSkill: number | null; baselineBrier: number | null; modelBrier: number | null;
  effectiveSample: number | null; expectedReturn: number | null;
  downsidePercentile: number | null; rewardRisk: number | null;
  priceLevels: { upper: number; baseLow: number; baseHigh: number; lower: number; invalidation: number } | null;
  nikkeiLicenseStatus: string;
  evidenceCoverage: { overall: 'HIGH' | 'MEDIUM' | 'LOW'; price: string; breadth: string;
    flow: string; short: string; macro: string; replay: string };
  attention: TodayAttentionInput[];
  holdingsReview: TodayHoldingInput[];
  systemStatus: { data: string; backup: string; rule: string };
  canonicalDecision: SingleDecisionAuthorityResultV2;
  footerText: string;
}

const OPEN_JP = new Set(['MORNING_SESSION', 'AFTERNOON_SESSION']);
const ACTIVE_US = new Set(['PRE_MARKET', 'REGULAR']);

export function quoteDisplayLabel(state: string): string {
  if (state === 'RT') return '現在 RT';
  if (state === 'D20') return '現在 D20';
  if (state === 'STALE' || state === 'stale') return '最終値 STALE';
  if (state === 'realtime') return 'リアルタイム';
  if (state === 'delayed') return '遅延値';
  return '終値';
}

function sessionLabel(market: ArgusMarket, state?: MarketCalendarState): string {
  if (!state) return `${market} CLOSED`;
  if (!state.isTradingDay) return `${market} HOLIDAY`;
  const labels: Record<string, string> = market === 'JP'
    ? { PRE_MARKET: 'JP PRE', MORNING_SESSION: 'JP OPEN', LUNCH_BREAK: 'JP LUNCH',
      AFTERNOON_SESSION: 'JP OPEN', CLOSED: 'JP CLOSED', HOLIDAY_CLOSED: 'JP HOLIDAY' }
    : { PRE_MARKET: 'US PRE', REGULAR: 'US OPEN', AFTER_HOURS: 'US AFTER',
      CLOSED: 'US CLOSED', HOLIDAY_CLOSED: 'US HOLIDAY' };
  return labels[state.session] ?? `${market} CLOSED`;
}

function projectCurrentCalendarState(
  market: string,
  state: MarketCalendarState | undefined,
  nowMs: number,
) {
  if (!state) return null;
  const observedAt = exactAuthorityEpoch(state.sessionObservedAt);
  if (observedAt == null) return null;
  return projectPlanningSession(market, {
    calendar: { [market]: state },
    serverAsOf: state.sessionObservedAt,
    receivedAtMs: observedAt,
    availability: 'available',
  }, nowMs);
}

function currentCalendarState(
  market: string,
  state: MarketCalendarState | undefined,
  nowMs: number,
) {
  const projection = projectCurrentCalendarState(market, state, nowMs);
  return projection?.state === 'open' || projection?.state === 'closed'
    ? state : undefined;
}

export function currentDecisionCalendar(
  calendar: Record<string, MarketCalendarState> | null | undefined,
  nowMs = Date.now(),
): Record<string, MarketCalendarState> | null {
  if (!Number.isFinite(nowMs)) return null;
  const current = Object.fromEntries(Object.entries(calendar ?? {})
    .map(([market, state]) => [market, currentCalendarState(market, state, nowMs)] as const)
    .filter((entry): entry is readonly [string, MarketCalendarState] => !!entry[1]));
  return Object.keys(current).length ? current : null;
}

export function selectAutoMarket(calendar?: Record<string, MarketCalendarState> | null,
  nowMs = Date.now()): ArgusMarket {
  const current = currentDecisionCalendar(calendar, nowMs);
  const jp = current?.JP, us = current?.US;
  if (jp && OPEN_JP.has(jp.session)) return 'JP';
  if (us && ACTIVE_US.has(us.session)) return 'US';
  if (jp?.session === 'LUNCH_BREAK') return 'JP';
  if (us?.session === 'AFTER_HOURS') return 'JP';
  if (jp?.isTradingDay && jp.session === 'PRE_MARKET') return 'JP';
  if (us?.isTradingDay && us.session === 'PRE_MARKET') return 'US';
  const jn = jp?.nextTradingDay ?? '9999-12-31';
  const un = us?.nextTradingDay ?? '9999-12-31';
  return jn <= un ? 'JP' : 'US';
}

function eventEpoch(event: TodayEventInput): number | null {
  if (!event.at) return null;
  const t = Date.parse(event.at);
  return Number.isFinite(t) ? t : null;
}

function dataStatus(code: DataQuality): ArgusTodayView['dataStatus'] {
  if (code === 'LIVE') return { code, label: '正常', tone: 'ok' };
  if (['PARTIAL', 'DELAYED', 'STALE'].includes(code)) return { code, label: '一部不足', tone: 'warn' };
  return { code, label: '要確認', tone: 'bad' };
}

export function buildArgusTodayView(input: ArgusTodayInput): ArgusTodayView {
  const calendar = currentDecisionCalendar(input.calendar, input.now.getTime());
  const selectedMarket = input.selectionMode === 'AUTO'
    ? selectAutoMarket(calendar, input.now.getTime()) : input.selectionMode;
  const canonical = input.canonicalDecision;
  const canonicalAction = canonical.primaryAction;
  const nowMs = input.now.getTime();
  // v13.5.51: one canonical event truth. The backend orders /important-events
  // by the shared lifecycle tier (NOW → NEXT → …); Today's next event is the
  // first upcoming item in that canonical order, and HISTORY/RECENT rows can
  // never become the Today hero.  Rows without a tier (older backend) fall
  // back to the upcoming-soonest rule.
  const tiered = (input.events ?? []).some((event) => !!event.lifecycleTier);
  const future = [...(input.events ?? [])]
    .map((event, index) => ({ event, at: eventEpoch(event), index }))
    .filter((x): x is { event: TodayEventInput; at: number; index: number } => x.at != null && x.at >= nowMs
      && !['RELEASED', 'RESOLVED'].includes(x.event.lifecycle ?? '')
      && (!tiered || ['NOW', 'NEXT', 'LATER', 'HORIZON'].includes(x.event.lifecycleTier ?? '')))
    .sort((a, b) => (tiered ? a.index - b.index : 0) || a.at - b.at || a.event.id.localeCompare(b.event.id));
  const nextEvent = future[0]?.event ?? null;
  // v13.5.54 (owner 2026-09-04: 「イベントが出たばかりなので米雇用統計が出てない」).
  // The filter above is strictly forward-looking, so the instant a release
  // fires it leaves the Today surface entirely — exactly when the owner most
  // wants it. The lifecycle model already names that state (NOW inside the
  // result SLA, MONITORING while the official result is still missing, RECENT
  // once it lands), so surface the most recent one alongside, newest first.
  // HISTORY is excluded, so this ages out on its own after 72h.
  const releasedEvent = [...(input.events ?? [])]
    .map((event) => ({ event, at: eventEpoch(event) }))
    .filter((x): x is { event: TodayEventInput; at: number } => x.at != null && x.at < nowMs
      && ['NOW', 'MONITORING', 'RECENT'].includes(x.event.lifecycleTier ?? ''))
    .sort((a, b) => b.at - a.at || a.event.id.localeCompare(b.event.id))[0]?.event ?? null;
  const limit30d = nowMs + 30 * 86_400_000;
  const comingEvents = future.slice(1).filter((x) => x.at <= limit30d).slice(0, 3).map((x) => x.event);
  const projectionInput = input.projection?.[selectedMarket] ?? null;
  const projectionsByHorizon: ArgusTodayView['projectionsByHorizon'] = {};
  for (const days of [1, 5, 20] as const) {
    const built = buildTodayProjection(
      projectionInput, canonicalAction, days, input.now.getTime());
    if (built) projectionsByHorizon[`${days}D` as const] = built;
  }
  const projection = projectionsByHorizon['5D'] ?? null;
  const actionScore = canonical.sevenSign.candidateLevel;
  const permissions = {
    newEntry: canonical.status === 'EVALUATED' && canonicalAction === 'BUY',
    add: canonical.status === 'EVALUATED' && canonicalAction === 'BUY',
    hold: canonicalAction !== 'EXIT',
  };
  const evidenceCoverage = selectedMarket === 'JP'
    ? { overall: 'HIGH' as const, price: 'HIGH', breadth: 'HIGH', flow: 'HIGH',
      short: 'HIGH', macro: 'MEDIUM', replay: projection ? 'HIGH' : 'LOW' }
    : { overall: 'MEDIUM' as const, price: 'HIGH', breadth: 'MEDIUM', flow: 'LOW',
      short: 'NONE', macro: 'HIGH', replay: projection ? 'HIGH' : 'LOW' };
  const eventTag = nextEvent ? `${nextEvent.code} ${formatEventTime(nextEvent.at, nextEvent.dateOnly)}` : `DATA ${dataStatus(input.dataQuality).label}`;
  return {
    selectedMarket, selectionMode: input.selectionMode,
    sessionLamps: [
      { key: 'JP', label: sessionLabel('JP', calendar?.JP),
        active: !!calendar?.JP?.isTradingDay && OPEN_JP.has(calendar.JP.session),
        tone: calendar?.JP?.isTradingDay && OPEN_JP.has(calendar.JP.session) ? 'open'
          : calendar?.JP?.isTradingDay && ['PRE_MARKET', 'LUNCH_BREAK'].includes(calendar.JP.session) ? 'standby' : 'closed' },
      { key: 'US', label: sessionLabel('US', calendar?.US),
        active: !!calendar?.US?.isTradingDay && calendar.US.session === 'REGULAR',
        tone: calendar?.US?.isTradingDay && calendar.US.session === 'REGULAR' ? 'open'
          : calendar?.US?.isTradingDay && ['PRE_MARKET', 'AFTER_HOURS'].includes(calendar.US.session) ? 'standby' : 'closed' },
      { key: 'FX', label: 'FX 24H', active: true, tone: 'open' },
      { key: 'CRYPTO', label: 'CRYPTO 24H', active: true, tone: 'open' },
    ],
    nextEvent, releasedEvent, comingEvents,
    eventsAuthorityUnknown: !!input.eventsAuthorityUnknown,
    finalAction: canonicalAction, actionScore,
    confidence: canonical.confidence.valueBps / 10_000,
    dataStatus: dataStatus(input.dataQuality),
    dataQualityReasonCodes: input.dataQuality === 'LIVE'
      ? [] : [...(input.dataQualityReasonCodes ?? [])],
    globalRisk: input.globalRisk && input.globalRisk !== 'normal'
      ? input.globalRisk.toUpperCase() : null,
    marketPrice: projection?.current ?? null,
    range: projection ? { low: projection.baseLow, high: projection.baseHigh } : null,
    invalidation: projection?.invalidation ?? null,
    projection, projectionsByHorizon,
    selectedInstrument: projection ? { symbol: projection.symbol,
      instrumentId: projection.instrumentId, label: projection.label } : null,
    shortSellingSummary: projection?.shortSelling ?? null,
    failedRallyState: projection?.failedRally ?? null,
    factors: (input.factors?.[selectedMarket] ?? []).slice(0, 5), permissions,
    conciseAction: input.conciseAction ? input.conciseAction.slice(0, 32) : null,
    conciseAvoid: input.conciseAvoid ? input.conciseAvoid.slice(0, 32) : null,
    indexMoves: (input.indexMoves ?? input.marketMoves ?? []).slice(0, 4),
    macroMoves: (input.macroMoves ?? (input.marketMoves ?? []).slice(4)).slice(0, 3),
    positioning: (input.positioning?.[selectedMarket] ?? [])
      .filter((row) => row.value.trim() !== '—').slice(0, 5),
    news: dedupeNews(input.news ?? []),
    newsCardState: input.newsCardState ?? {
      status: 'unavailable', lastChecked: null, lastSuccessfulPollAt: null,
      fetchedCount: 0, relevantCount: 0, stale: true, failureClass: 'not_checked',
    },
    directionProbabilities: projection?.directionProbabilities ?? null,
    probabilityHorizon: projection?.horizonDays ?? null,
    brierSkill: projection?.brierSkill ?? null,
    baselineBrier: projection?.baselineBrier ?? null,
    modelBrier: projection?.modelBrier ?? null,
    effectiveSample: projection?.effectiveSampleCount ?? null,
    expectedReturn: projection?.expectedValue?.expectedReturn ?? null,
    downsidePercentile: projection?.expectedValue?.q10 ?? null,
    rewardRisk: projection?.expectedValue?.rewardRisk ?? null,
    priceLevels: projection ? { upper: projection.upside, baseLow: projection.baseLow,
      baseHigh: projection.baseHigh, lower: projection.downside, invalidation: projection.invalidation } : null,
    nikkeiLicenseStatus: projection?.licenseStatus ?? 'not_applicable',
    evidenceCoverage,
    attention: [...(input.attention ?? [])]
      .filter((row) => row.id !== nextEvent?.id)
      .sort((a, b) => b.severity - a.severity || a.id.localeCompare(b.id)).slice(0, 3),
    holdingsReview: dedupeHoldings(input.holdings ?? []),
    systemStatus: input.systemStatus ?? { data: dataStatus(input.dataQuality).label, backup: '確認', rule: 'DETERMINISTIC' },
    canonicalDecision: canonical,
    footerText: `${canonicalAction} · ${canonical.status === 'DATA_GATED'
      ? '判断に必要なデータを確認中' : '確認済みの根拠に基づく判断'} · ${eventTag}`,
  };
}

/** Todayの予測図は、実測OHLCVとサーバー側walk-forward校正結果だけを描く。 */
export function buildTodayProjection(input: TodayProjectionInput | null,
  action: PrimaryAction, horizonDays: 1 | 5 | 20 = 5,
  nowMs = Date.now()): TodayProjection | null {
  if (!todayProjectionDecisionUsable(input, nowMs)) return null;
  const bars = input.bars.filter((bar) => Number.isFinite(bar.close) && bar.close > 0).slice(-30);
  const last = bars.at(-1);
  const atr = last?.atr14;
  if (!last || bars.length < 20 || atr == null || !Number.isFinite(atr) || atr <= 0) return null;
  const current = last.close;
  const zones = input.zones.filter((zone) => ['active', 'reclaimed'].includes(zone.status)
    && Number.isFinite(zone.lower) && Number.isFinite(zone.upper));
  // A zone must sit wholly on the correct side of the observed close. A band
  // crossing the close is neither resistance nor support at that instant.
  const below = zones.filter((zone) => zone.upper < current)
    .sort((a, b) => b.upper - a.upper)[0];
  const above = zones.filter((zone) => zone.lower > current)
    .sort((a, b) => a.lower - b.lower)[0];
  const calibrated = input.calibration?.horizons[String(horizonDays)];
  const distribution = calibrated?.returnDistribution;
  const priceAt = (value: number | null | undefined, fallback: number) =>
    value == null || !Number.isFinite(value) ? fallback : Math.max(0.000001, current * (1 + value));
  const horizonAtr = atr * Math.sqrt(horizonDays / 5);
  const baseLow = priceAt(distribution?.q25, Math.max(0.000001, current - horizonAtr));
  const baseHigh = priceAt(distribution?.q75, current + horizonAtr);
  const downside = Math.min(below?.center ?? current,
    priceAt(distribution?.q25, Math.max(0.000001, current - 2 * horizonAtr)));
  const upside = Math.max(above?.center ?? current,
    priceAt(distribution?.q75, current + 2 * horizonAtr));
  const invalidation = action === 'EXIT' || action === 'REDUCE'
    ? priceAt(distribution?.q90, above?.upper ?? current + 2 * horizonAtr)
    : priceAt(distribution?.q10, below?.lower ?? Math.max(0.000001, current - 2 * horizonAtr));
  const activePoint = [...(input.turningPoints ?? [])].reverse()
    .find((point) => point.status === 'confirmed' || point.status === 'candidate');
  const candidateProbabilities = calibrated?.directionProbabilities
    ?? calibrated?.referenceDirectionProbabilities
    ?? calibrated?.probabilities
    ?? null;
  const probabilityEligibility: ProbabilityEligibility = calibrated?.probabilityEligibility ?? {
    eligible: false, reasonCodes: ['server_eligibility_unavailable'],
    effectiveSample: calibrated?.effectiveSampleCount ?? 0,
    modelBrier: calibrated?.modelBrier ?? null, baselineBrier: calibrated?.baselineBrier ?? null,
    brierSkill: calibrated?.brierSkill ?? null,
    calibrationIntegrity: calibrated?.calibrationIntegrity ?? 'UNKNOWN',
    probabilitySum: null, calibrationVersion: calibrated?.calibrationVersion
      ?? input.calibration?.calibrationVersion ?? 'unknown',
    datasetHash: calibrated?.calibrationDatasetHash ?? null,
    evaluatedAt: input.asOf, contractVersion: 'unavailable',
  };
  const probabilityTruth = evaluateProbabilityTruth(
    calibrated?.probabilityTruthEvidence ?? unavailableProbabilityEvidence({
      serverEligible: probabilityEligibility.eligible,
      oosEffectiveN: calibrated?.effectiveSampleCount ?? null,
      ruleEffectiveN: calibrated?.episodeCount ?? null,
    }),
    candidateProbabilities,
  );
  const probabilities = probabilityTruth.exactPercentageAllowed
    ? candidateProbabilities : null;
  const referenceProbabilities = !probabilityTruth.exactPercentageAllowed
    && probabilityTruth.directionalLean !== 'UNRESOLVED'
    ? candidateProbabilities : null;
  const turningPointMarkers = [...(input.turningPoints ?? [])].reverse()
    .filter((point) => point.status === 'confirmed' || point.status === 'candidate')
    .slice(0, 3).map((point) => ({ id: point.id, date: point.effectiveFrom,
      direction: point.direction, label: point.facts[0] ?? 'Turning Point' }));
  const eventMarkers = (input.eventMarkers ?? []).filter((event) =>
    bars.some((bar) => bar.date === event.date)).slice(-2);
  const instrumentId = input.instrumentId ?? input.symbol;
  const forecastId = [
    'forecast', instrumentId, horizonDays, input.asOf ?? 'unknown',
    calibrated?.calibrationDatasetHash?.slice(0, 12) ?? 'uncalibrated',
  ].join(':');
  return {
    symbol: input.symbol, instrumentId,
    label: input.label, asOf: input.asOf, current,
    history: bars.map((bar) => ({ date: bar.date, value: bar.close,
      open: bar.open, high: bar.high, low: bar.low, volume: bar.volume })),
    baseLow: Math.min(baseLow, baseHigh), baseHigh: Math.max(baseLow, baseHigh),
    upside, downside, invalidation,
    support: below ? { low: below.lower, high: below.upper, status: below.status } : null,
    resistance: above ? { low: above.lower, high: above.upper, status: above.status } : null,
    horizon: `${horizonDays}営業日`, horizonDays,
    directionLabel: candidateProbabilities
      ? probabilityTruth.directionalLeanJa
      : action === 'BUY' ? '上方向優勢'
        : action === 'REDUCE' || action === 'EXIT' ? '下方向警戒' : '本線内で待機',
    confidenceLabel: probabilityTruth.exactPercentageAllowed ? '高'
      : input.status === 'live' && bars.length >= 25 ? '中' : '低',
    directionProbabilities: probabilities,
    referenceDirectionProbabilities: referenceProbabilities,
    calibrationStatus: calibrated?.calibrationStatus ?? 'not_available',
    rawSampleCount: calibrated?.rawOccurrenceCount ?? 0,
    episodeCount: calibrated?.episodeCount ?? 0,
    effectiveSampleCount: calibrated?.effectiveSampleCount ?? 0,
    modelBrier: calibrated?.modelBrier ?? calibrated?.brierScore ?? null,
    baselineBrier: calibrated?.baselineBrier ?? null,
    brierSkill: calibrated?.brierSkill ?? null,
    brierSkillConfidenceInterval: calibrated?.brierSkillConfidenceInterval ?? null,
    calibrationError: calibrated?.calibrationError ?? null,
    probabilityEligibility,
    probabilityTruth,
    calibrationVersion: calibrated?.calibrationVersion ?? input.calibration?.calibrationVersion ?? null,
    datasetHash: calibrated?.calibrationDatasetHash ?? null,
    expectedValue: calibrated?.expectedValue ?? null,
    levelProbabilities: calibrated?.levelProbabilities ?? calibrated?.targetProbabilities ?? null,
    reactionDelay: calibrated?.averageReactionDelay ?? null,
    methodLabel: `実測OHLCV + 類似局面 + ATR14 + 支持抵抗 · ${input.calibration?.calibrationVersion ?? '未校正'}`,
    timeframeLabel: input.timeframe === 'weekly' ? '週足' : '日足',
    // Legacy chart payloads do not prove intraday freshness.  They therefore
    // fall back to CLOSE; RT/D20 are rendered only when explicitly supplied.
    quoteState: input.quoteState ?? (input.status === 'stale' ? 'STALE' : 'CLOSE'),
    sourceHistoryCount: input.sourceHistoryCount ?? input.bars.length,
    historyStart: input.historyStart ?? null,
    historyEnd: input.historyEnd ?? input.asOf,
    shoConditioningJa: (() => {
      const meta = input.calibration?.shoConditioning;
      const keys = meta?.currentFeatureKeys ?? [];
      // v13.5.36: a provider MISCONFIGURATION is named, never silently
      // identical to an honest data gap (external review item 4).
      const issueJa: Record<string, string> = {
        vix_provider_key_missing: 'VIX供給が設定障害（プロバイダ鍵未設定）',
      };
      const issues = (meta?.sourceIssues ?? [])
        .map((issue) => issueJa[issue] ?? `供給設定障害: ${issue}`);
      if (!meta?.requested || keys.length === 0) {
        return issues.length ? `⚠ ${issues.join('・')}` : null;
      }
      const names: Record<string, string> = {
        creditRatio: '信用倍率', creditShortTn: '売り残高',
        vixLevel: 'VIX', vixChange10: 'VIX変化', rs20: '日米強弱',
      };
      const labels = [...new Set(keys.map((key) => names[key] ?? key))];
      const head = labels.length ? `市場状態条件付き: ${labels.join('・')}` : null;
      if (issues.length) {
        return head ? `${head} · ⚠ ${issues.join('・')}` : `⚠ ${issues.join('・')}`;
      }
      return head;
    })(),
    source: input.source ?? 'existing_market_data_cache',
    availableFrom: input.availableFrom ?? null,
    assetType: input.assetType ?? 'UNKNOWN',
    proxyFor: input.proxyFor ?? null,
    disclosureJa: input.disclosureJa ?? null,
    licenseStatus: input.licenseStatus ?? 'not_applicable',
    forecastId,
    signalEpisodeIds: turningPointMarkers.map((point) => point.id),
    supportResistanceIds: [below?.id, above?.id].filter((id): id is string => !!id),
    eventIds: eventMarkers.map((event) => event.id),
    eventMarkers,
    turningPointMarkers,
    activeTurningPoint: activePoint ? { id: activePoint.id, date: activePoint.effectiveFrom,
      direction: activePoint.direction, label: activePoint.facts[0] ?? 'Turning Point' } : null,
    shortSelling: input.shortSelling ?? null,
    failedRally: input.failedRally ?? null,
  };
}

export function todayProjectionDecisionUsable(
  input: TodayProjectionInput | null,
  nowMs = Date.now(),
): input is TodayProjectionInput {
  if (!input || !Number.isFinite(nowMs) || input.authorityState === 'stale'
      || input.authorityState === 'unavailable') return false;
  const date = exactCalendarDate(input.asOf);
  if (date) {
    const sourceStart = Date.parse(`${date}T00:00:00Z`);
    const deadline = calendarDateExpiresAt(date, 7);
    return Number.isFinite(sourceStart) && sourceStart <= nowMs
      && deadline != null && nowMs < deadline;
  }
  const asOf = exactAuthorityEpoch(input.asOf);
  return asOf != null && asOf <= nowMs && nowMs - asOf <= 5 * 24 * 60 * 60_000;
}

const INDEX_NEWS = /(日経|nikkei|topix|s&p|nasdaq|dow|wall st|株式市場|stock market|equity market)/i;
const GLOBAL_NEWS = /(戦争|侵攻|制裁|金融危機|銀行破綻|緊急利上げ|緊急利下げ|緊急決定|war|invasion|sanction|financial crisis|bank failure|emergency rate)/i;

/** Today用ニュースは、処理済みかつ判断変更に関係するものだけを最大3件にする。 */
export function selectTodayNews(candidates: TodayNewsCandidate[], symbols: string[]): TodayNewsInput[] {
  const universe = symbols.map((value) => value.trim().toUpperCase()).filter(Boolean);
  const seen = new Set<string>();
  const sourceCounts = new Map<string, number>();
  const eligible = candidates.filter((item) => {
    if (!item.major || item.relevant === false || !item.titleJa.trim() || !item.source || !item.url) return false;
    if (!['translated', 'summarized', 'not_needed'].includes(item.translationStatus ?? '')) return false;
    // A trusted wire headline may be shown as clearly unconfirmed evidence.
    // It never becomes decision-usable merely by being visible.
    if (!['official', 'corroborated'].includes(item.corroboration ?? '')
        && !(item.tier === 'wire' && item.corroboration === 'single')) return false;
    const text = `${item.titleJa} ${item.titleOriginal ?? ''}`;
    const linked = (item.linkedSymbols ?? []).map((value) => value.toUpperCase());
    const universeMatch = linked.some((symbol) => universe.includes(symbol))
      || universe.some((symbol) => symbol.length >= 3 && text.toUpperCase().includes(symbol));
    const allowedScope = item.scope === 'holding' || item.scope === 'watchlist'
      || item.scope === 'index' || item.scope === 'global';
    if (!allowedScope && !universeMatch && !INDEX_NEWS.test(text) && !GLOBAL_NEWS.test(text)) return false;
    const key = `${item.source}:${item.titleJa}`.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((a, b) => {
    const rank = (row: TodayNewsCandidate) => row.corroboration === 'official' ? 3
      : row.corroboration === 'corroborated' ? 2 : 1;
    return rank(b) - rank(a) || (b.publishedAt ?? 0) - (a.publishedAt ?? 0);
  });
  const selected = eligible.filter((item) => {
    const count = sourceCounts.get(item.source) ?? 0;
    if (count >= 2) return false;
    sourceCounts.set(item.source, count + 1);
    return true;
  }).slice(0, 3);
  return selected.map(({ id, titleJa, source, url, publishedAt, corroboration }) => ({
    id, titleJa, source, url, publishedAt, corroboration,
  }));
}

function dedupeHoldings(rows: TodayHoldingInput[]): TodayHoldingInput[] {
  const seen = new Set<string>();
  return [...rows].sort((a, b) => a.rank - b.rank || a.symbol.localeCompare(b.symbol))
    .filter((row) => { const key = row.symbol.toUpperCase(); if (seen.has(key)) return false; seen.add(key); return true; })
    .slice(0, 3);
}

function dedupeNews(rows: TodayNewsInput[]): TodayNewsInput[] {
  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = `${row.source}:${row.titleJa}`.toLowerCase();
    if (!row.titleJa.trim() || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 3);
}

export function formatEventTime(value: string | null, dateOnly = false): string {
  if (!value) return '';
  const t = Date.parse(value);
  if (!Number.isFinite(t)) return '';
  if (dateOnly) {
    return new Date(t).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric' });
  }
  return new Date(t).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
