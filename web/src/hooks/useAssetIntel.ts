import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { useAIJudgment } from './useAIJudgment';
import { useActionLabels } from './useActionLabels';
import { useCryptoWatchlist } from './useCryptoWatchlist';
import { useMarketRegime } from './useMarketRegime';
import { useEventRadar } from './useEventRadar';
import { useDownsideIncidents } from './useDownsideIncidents';
import { useVisibilityGuard } from './useVisibilityGuard';
import { useEventsActive } from './useEventsActive';
import { useImportantEvents } from './useImportantEvents';
import { requestDecisionEvidenceSymbols, useDecisionEvidence } from './useDecisionEvidence';
import { resolveCanonicalArtifactReferences } from '../domain/canonicalDecisionEvidence';
import { useRatesSnapshot } from './useRatesSnapshot';
import { useJapanWatchlist } from './useJapanWatchlist';
import { overlayTachibanaLive } from '../domain/tachibanaLive';
import type { TachibanaLiveDocument } from '../domain/tachibanaLive';
import { useUSWatchlist } from './useUSWatchlist';
import { fundNavForAsset, useFundNav } from './useFundNav';
import { useFlowAttributionList } from './useFlowAttribution';
import { useSupplyDemandList } from './useSupplyDemand';
import { useMarketLedger } from './useMarketLedger';
import { coingeckoIdOf, SYMBOL_TO_COINGECKO } from '../lib/cryptoIds';
import { calendarDateExpiresAt, quoteDecisionExpiresAt,
  quoteDecisionUsable } from '../domain/liveQuote';
import { exactAuthorityEpoch, liveAuthorityExpiresAt, liveAuthorityState } from '../domain/liveAuthority';
import { ratePointDecisionExpiresAt, ratePointDecisionUsable } from '../domain/rateAuthority';
import { groupAssetCards, type LinkedEventTag, type AssetCardModel } from '../domain/assetCard';
import {
  assessAi, projectCanonicalAssetDecision,
  type AssetDecisionView, type AiMeta,
} from '../domain/assetDecision';
import { buildPositionExposure, themeOf } from '../domain/positionExposure';
import { appendDeviceLocalSdaLedger, deriveLocalOwnerRiskBands } from '../lib/sdaDeviceLocal';
import { currencyOf } from '../lib/portfolio';
import {
  publishExposure, publishActionPriorities, publishSessionBrief,
  publishScenarios, publishPlans, publishStrategy, publishFireCore,
  retainDerivedSharePublisher,
} from '../lib/positionExposureShare';
import { listDQ } from '../lib/decisionQuality';
import { buildItem as buildAPItem, rankItems as rankAPItems, type APItem } from '../domain/actionPriority';
import { buildLocalBrief } from '../domain/sessionBrief';
import { buildScenarioSet, type LocalScenarioSet } from '../domain/scenario';
import {
  buildPlan, projectPlanningSession, type LocalPlan,
  type PlanningSessionAuthority,
} from '../domain/positionPlan';
import {
  buildDataGatedInputV2, buildPredictionLedgerV2Adapter, buildRiskKernel,
  evaluateSingleDecisionAuthority, verifyDecisionEvidence,
  SINGLE_DECISION_AUTHORITY_V2_POLICY,
  type PrimaryAction, type RiskContributionV1, type SingleDecisionAuthorityResultV2,
  type PredictionLedgerSdaAdapterV2,
} from '../domain/singleDecisionAuthority';
import {
  eventKernelConstraint, eventKernelSeverity, imminentEventGate,
} from '../domain/importantEventsTier';
import { useNewsIntelligence } from './useNewsIntelligence';
import { newsKernelGate } from '../domain/newsSignalGate';
import { classifyRole, buildStrategy, type LocalStrategy } from '../domain/portfolioStrategy';
import {
  buildLocalFireCore, fireCoreMetaSnapshot, subscribeFireCoreMeta,
  type LocalFireCore,
} from '../lib/fireCore';
import { deriveTodayJudgment, combinePhase, type TodayPhase } from '../lib/todayCall';
import type { AssetItem } from '../types/assetItem';

const RISK_DISCIPLINE_POLICY = Object.freeze({
  policyId: 'argus-risk-discipline-v1',
  policySha256: '6f6d1562d53e8f978ea8e558770cb6e71f3f84e6b28fe91b85797cfd3f2333b4',
});

const exactSecondUtc = (epochMs: number) =>
  new Date(Math.floor(epochMs / 1000) * 1000).toISOString().replace('.000Z', 'Z');

const legacyFiveAction = (value: unknown): PrimaryAction | null => {
  const normalized = String(value ?? '').trim().toUpperCase().replace(/[_-]+/g, ' ');
  if (['BUY', 'BUY DIP', 'ADD', 'ENTER', 'GRADUAL ADD'].includes(normalized)) return 'BUY';
  if (['HOLD'].includes(normalized)) return 'HOLD';
  if (['WAIT', 'PREPARE', 'PAUSE'].includes(normalized)) return 'WAIT';
  if (['REDUCE', 'TRIM', 'DEFEND'].includes(normalized)) return 'REDUCE';
  if (['EXIT', 'SELL', 'SELL ALL'].includes(normalized)) return 'EXIT';
  return null;
};

const riskBand = (value: string | null | undefined):
  'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' | 'UNKNOWN' => {
  const upper = String(value ?? 'UNKNOWN').toUpperCase();
  return ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].includes(upper)
    ? upper as 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' : 'UNKNOWN';
};

// ── V12.2.12: Asset Intelligence(TodayとAsset Deskの共有データ組み立て) ──────
//
// 旧CommandCenter内にあった個別銘柄系のuseMemoパイプライン(AI優先マージ→カード
// →ポジション/優先度/シナリオ/計画/戦略/構え)をそのまま移設した唯一の正本。
// Today / Positions & Risk (publish:true)とAsset Desk(publish:false)が同じ
// フックを呼ぶため、同一銘柄の判断・件数・構えがページ間で構造的に一致する。
//
// publish副作用(Pro Handoff / AI Review / スナップショットが読む共有ストアへの
// 書き込み)はTodayとPositions & Riskの明示した呼び出しだけ:
// publish:false では一切書き込まない。
// この層は新しい投資判断を生成しない — 既存レイヤーの出力の組み立てのみ。

export interface AssetIntel {
  assets: AssetItem[];
  aiJ: ReturnType<typeof useAIJudgment>;
  al: ReturnType<typeof useActionLabels>;
  guard: ReturnType<typeof useVisibilityGuard>;
  regime: ReturnType<typeof useMarketRegime>;
  downside: ReturnType<typeof useDownsideIncidents>['data'];
  events247: ReturnType<typeof useEventsActive>['events'];
  impEvents: ReturnType<typeof useImportantEvents>['data'];
  rates: ReturnType<typeof useRatesSnapshot>;
  /** 実クオートhook(Asset Deskがname/volume/date/status付きで再利用 — 二重fetch防止)。 */
  jpQuotes: ReturnType<typeof useJapanWatchlist>;
  usQuotes: ReturnType<typeof useUSWatchlist>;
  cryptoWatch: ReturnType<typeof useCryptoWatchlist>;
  fundNav: ReturnType<typeof useFundNav>;
  /** Canonical live/EOD price map shared with contextual owner tools. */
  priceBySymbol: ReadonlyMap<string, number>;
  flowRecords: ReturnType<typeof useFlowAttributionList>['records'];
  sdSignals: ReturnType<typeof useSupplyDemandList>['signals'];
  cardGroups: ReturnType<typeof groupAssetCards>;
  cardBySym: Map<string, AssetCardModel>;
  ownerCritical: AssetCardModel[];
  positionExposure: ReturnType<typeof buildPositionExposure>;
  apItems: APItem[];
  sessionBrief: ReturnType<typeof buildLocalBrief>;
  scenarioSets: LocalScenarioSet[];
  portfolioStrategy: LocalStrategy;
  fireCore: LocalFireCore;
  positionPlans: LocalPlan[];
  phase: TodayPhase;
  judgment: ReturnType<typeof deriveTodayJudgment>;
  overlay: { globalRegime: string; jpIntradayOverlay: string; holderRiskOverlay: string };
  isPartial: boolean;
  /**
   * v13.5.54: WHY the data is partial. Closed vocabulary, ARGUS-side states
   * only — never a claim that the owner failed to supply something.
   */
  partialReasonCodes: string[];
  /** v13.5.60: informational notes that are NOT shortfalls (closed-session previous values). */
  dataQualityNotes: string[];
  /**
   * The important-events feed could not be read this cycle. Consumers must
   * render "not known" rather than asserting that no event is linked.
   */
  importantEventsUnknown: boolean;
  visLimited: boolean;
  cappedConf: number | null;
  positionRisk: { alert: boolean; ja: string };
  aiMeta: AiMeta;
  decisionBySym: Map<string, AssetDecisionView>;
  /** Sole device-local five-action result. */
  sdaBySymbol: Map<string, SingleDecisionAuthorityResultV2>;
  /** Append-only local binding; no quantity, cost basis, return or P/L. */
  sdaLedgerBindingBySymbol: Map<string, PredictionLedgerSdaAdapterV2>;
}

export function useAssetIntel(opts: {
  publish: boolean;
  /** The single AssetsProvider supplies the live protected asset state. */
  assets: AssetItem[];
}): AssetIntel {
  const publish = opts.publish;
  const assets = opts.assets;
  useEffect(() => publish ? retainDerivedSharePublisher() : undefined, [publish]);
  const fireCoreMetaRevision = useSyncExternalStore(
    subscribeFireCoreMeta,
    fireCoreMetaSnapshot,
    () => '{}',
  );
  const aiJ = useAIJudgment();
  // The engine follows the USER's actual watchlist (dynamic symbols, v9.8).
  const jpSyms = useMemo(() => assets.filter((a) => a.market === 'JP').map((a) => a.symbol), [assets]);
  const usSyms = useMemo(() => assets.filter((a) => a.market === 'US').map((a) => a.symbol), [assets]);
  const al = useActionLabels({ jp: jpSyms, us: usSyms });
  // Crypto has no Action Label; pull its live quote separately so the top-screen
  // crypto cards show the day-change like JP/US (was always "—" before).
  const cryptoAssets = useMemo(() => assets.filter((a) => a.market === 'CRYPTO'), [assets]);
  // v13.5.61 (owner: XRP/SOL blank while the backend serves both): a memo that
  // names a CoinGecko id wins, but the symbol's default id is requested too and
  // used when the memo id yields nothing — one wrong memo no longer blanks a coin.
  const cryptoIds = useMemo(() => {
    const ids = new Set<string>();
    for (const a of cryptoAssets) {
      for (const id of [coingeckoIdOf(a), SYMBOL_TO_COINGECKO[a.symbol.toUpperCase()] ?? '']) if (id) ids.add(id);
    }
    return [...ids];
  }, [cryptoAssets]);
  const cw = useCryptoWatchlist(cryptoIds);
  const cryptoQuotes = useMemo(() => {
    const m: Record<string, { price?: number | null; changePct?: number | null }> = {};
    for (const a of cryptoAssets) {
      const candidates = [coingeckoIdOf(a), SYMBOL_TO_COINGECKO[a.symbol.toUpperCase()] ?? ''].filter(Boolean);
      const q = candidates.map((id) => cw.byId?.[id]).find((row) => row);
      if (q) m[a.symbol.toUpperCase()] = { price: q.priceUsd ?? null, changePct: q.changePct ?? null };
    }
    return m;
  }, [cryptoAssets, cw.byId]);
  const regime = useMarketRegime();
  const marketLedger = useMarketLedger();
  const canonicalSessionAuthority = useMemo<PlanningSessionAuthority>(() => ({
    calendar: marketLedger.ledger?.phase3?.calendar ?? null,
    serverAsOf: marketLedger.ledger?.phase3?.asOf
      ?? marketLedger.ledger?.asOf ?? null,
    receivedAtMs: marketLedger.fetchedAtMs,
    availability: marketLedger.error ? 'refresh_failed'
      : marketLedger.loading ? 'loading'
        : marketLedger.sessionExpired ? 'expired' : 'available',
  }), [marketLedger.ledger, marketLedger.fetchedAtMs, marketLedger.error,
    marketLedger.loading, marketLedger.sessionExpired]);
  const ev = useEventRadar();
  const downsideState = useDownsideIncidents();
  const { data: downside } = downsideState;
  const guard = useVisibilityGuard();   // Visibility Risk Guard (v10.195)
  const { events: events247 } = useEventsActive();
  const importantEventsState = useImportantEvents();
  const { data: impEvents } = importantEventsState;
  const newsIntel = useNewsIntelligence().view;   // v13.5.36 news signal
  const importantEventsUnknown = importantEventsState.authority !== 'fresh';
  const downsideUnknown = downsideState.authority !== 'fresh';
  // v13.5.13: canonical artifact references (marketTruth/predictionLedger/sho)
  // from the reviewed backend resolver boundary. Held symbols first — those
  // are the decisions the owner actually needs EVALUATED.
  const decisionEvidence = useDecisionEvidence();
  useEffect(() => {
    requestDecisionEvidenceSymbols(assets
      .filter((asset) => asset.market === 'JP' || asset.market === 'US')
      .sort((left, right) =>
        Number(right.quantity != null && right.quantity > 0)
        - Number(left.quantity != null && left.quantity > 0))
      .map((asset) => asset.symbol.toUpperCase()));
  }, [assets]);

  // AI is challenge/dissent evidence only. It never replaces the canonical
  // five-action SDA result.
  const aiMeta = useMemo(() => assessAi(aiJ.data, Date.now()), [aiJ.data]);
  const cardLabels = useMemo(() => {
    const legacy = (al.data?.labels ?? []).map((label) => ({
      ...label, judgmentSource: 'rule' as const, aiReasonJa: null,
    }));
    if (!downsideUnknown && !importantEventsUnknown) return legacy;
    const positive = new Set(['BUY', 'BUY DIP', 'ADD', 'ENTER', 'PREPARE', 'GRADUAL ADD']);
    return legacy.map((label) => ({
      ...label,
      action: positive.has(label.action.trim().toUpperCase()) ? 'WAIT' : label.action,
      confidence: Math.min(label.confidence ?? 0, 0.25),
      status: 'mock' as const,
      supportingData: label.supportingData
        ? { ...label.supportingData, bigFlowRatio: null, price: null,
          changePct: undefined, volume: undefined, quoteDate: null }
        : label.supportingData,
    }));
  }, [al.data, downsideUnknown, importantEventsUnknown]);

  // Unified per-stock cards (v10.140): merge action-labels + downside + 24/7 events
  // + linked macro-event tags into ONE card per stock, grouped + sorted per market.
  const cardGroups = useMemo(() => {
    const linked: Record<string, LinkedEventTag[]> = {};
    for (const ie of impEvents?.events ?? []) {
      for (const a of ie.linkedAssets ?? []) {
        const k = String(a).toUpperCase();
        (linked[k] ??= []).push({ code: ie.eventCode, countdown: ie.countdown, impact: ie.displayImpact.toUpperCase() });
      }
    }
    return groupAssetCards({
      assets, labels: cardLabels, incidents: downside?.incidents ?? [],
      events: events247 ?? [], linked, aiFreshness: aiMeta.freshness, cryptoQuotes,
    });
  }, [assets, aiMeta.freshness, cardLabels, downside, events247, impEvents, cryptoQuotes]);
  const cardBySym = useMemo(() => new Map(
    [...cardGroups.jpWatch, ...cardGroups.usWatch, ...cardGroups.crypto]
      .map((c) => [c.symbol.toUpperCase(), c])), [cardGroups]);

  // OWNER CRITICAL (spec): a held asset on the most defensive signals (EXIT/DEFEND)
  // gets a small top banner so a held emergency is never buried below the fold.
  const ownerCritical = useMemo(() =>
    [...cardGroups.jpWatch, ...cardGroups.usWatch, ...cardGroups.crypto]
      .filter((c) => c.held && (c.signalCode === 'EXIT' || c.signalCode === 'DEFEND')),
    [cardGroups]);

  // ── V11.8.0 Position / Exposure — device-local. Prices come from the cards
  // already built for Today; quantities/costs never leave localStorage.
  const rates = useRatesSnapshot();
  const flowState = useFlowAttributionList();
  const { records: flowRecords } = flowState;
  // v12.0.6 (owner: 保有銘柄の需給が全く出ない): デバイスのウォッチリスト銘柄を
  // サーバーへ渡し、固定リスト外(6965/7011等)にも需給ランクを出す。ソート済み
  // カンマ結合で参照安定化(不要な再fetchを防ぐ)。
  const sdExtraSymbols = useMemo(() =>
    assets.filter((a) => a.market === 'JP' || a.market === 'US')
      .map((a) => a.symbol.toUpperCase()).sort().join(','),
    [assets]);
  const supplyState = useSupplyDemandList(sdExtraSymbols);   // v11.10.0 需給ランク
  const { signals: sdSignals } = supplyState;
  // Card prices vanish outside sessions (labels carry no price) — fall back to
  // the same real quote hooks Core Portfolio uses (delayed close = real data).
  const peJpRaw = useJapanWatchlist(jpSyms);
  // v13.5.39: when the backend carries current Tachibana LIVE evidence for a
  // monitored JP symbol, that observation becomes the owner-visible realtime
  // source (provider tachibana, LIVE proven by a ≤60 s exchange timestamp);
  // every other row keeps its truthfully labeled delayed source.
  const tachibanaLiveDoc = useDecisionEvidence().marketView?.japaneseLive as
    TachibanaLiveDocument | null | undefined;
  const peJp = useMemo(() => ({
    ...peJpRaw,
    data: overlayTachibanaLive(peJpRaw.data as unknown as Parameters<typeof overlayTachibanaLive>[0],
      tachibanaLiveDoc ?? null) as typeof peJpRaw.data,
  }), [peJpRaw, tachibanaLiveDoc]);
  const peUs = useUSWatchlist(usSyms);
  const fundNav = useFundNav();
  const priceBySymbol = useMemo(() => {
    const prices = new Map<string, number>();
    const usable = (quote: { status?: string; quoteTruth?: Parameters<typeof quoteDecisionUsable>[0] }) =>
      (quote.status === 'live' || quote.status === 'delayed')
      && !!quote.quoteTruth && quoteDecisionUsable(quote.quoteTruth);
    for (const quote of peJp.data?.stocks ?? []) {
      if (usable(quote) && Number.isFinite(quote.price)) {
        prices.set(quote.symbol.toUpperCase(), quote.price);
      }
    }
    for (const quote of peUs.data?.stocks ?? []) {
      if (usable(quote) && Number.isFinite(quote.price)) {
        prices.set(quote.symbol.toUpperCase(), quote.price);
      }
    }
    for (const card of [...cardGroups.jpWatch, ...cardGroups.usWatch, ...cardGroups.crypto]) {
      // JP/US valuation authority is the source-timestamped watch quote. Action
      // labels are decisions, not an alternate price feed. Crypto cards receive
      // price only from the already-authorized CoinGecko projection above.
      if (card.market === 'CRYPTO' && card.price != null && Number.isFinite(card.price)) {
        prices.set(card.symbol.toUpperCase(), card.price);
      }
    }
    for (const asset of assets) {
      const fund = fundNavForAsset(asset, fundNav.funds);
      if (fund && Number.isFinite(fund.navYen)) {
        prices.set(asset.symbol.toUpperCase(), fund.navYen);
      }
    }
    return prices;
  }, [assets, cardGroups, fundNav.funds, peJp.data, peUs.data]);

  const mixedCurrencyHeld = useMemo(() => {
    const heldCurrencies = new Set(assets.filter((asset) => (asset.quantity ?? 0) > 0)
      .map((asset) => currencyOf(asset.market)));
    return heldCurrencies.has('USD') && heldCurrencies.has('JPY');
  }, [assets]);
  const fxAuthorityMissing = mixedCurrencyHeld
    && (rates.authority !== 'fresh'
      || !ratePointDecisionUsable(rates.data?.usdJpy));
  const sessionAuthorityMissing = canonicalSessionAuthority.availability !== 'available';
  const quoteAuthorityMissing = assets.some((asset) =>
    (asset.market === 'JP' || asset.market === 'US')
    && !priceBySymbol.has(asset.symbol.toUpperCase()));

  // Device-local handoffs preserve the earliest upstream evidence deadline.
  // Re-publishing cannot renew a source timestamp; the local two-minute cap is
  // only an additional upper bound.
  const derivedAuthorityValidUntilMs = useMemo(() => {
    const nowMs = Date.now();
    const deadlines: number[] = [nowMs + 2 * 60_000];
    const addSnapshot = (authority: string, asOf: unknown,
      kind: Parameters<typeof liveAuthorityExpiresAt>[1]) => {
      if (authority !== 'fresh') return;
      const deadline = liveAuthorityExpiresAt(asOf, kind);
      if (deadline != null) deadlines.push(deadline);
    };
    addSnapshot(al.authority, al.data?.asOf, 'actionLabels');
    addSnapshot(regime.authority, regime.data?.asOf, 'marketRegime');
    addSnapshot(ev.authority, ev.data?.asOf, 'eventRadar');
    addSnapshot(downsideState.authority, downside?.asOf, 'downsideIncidents');
    addSnapshot(importantEventsState.authority, impEvents?.asOf, 'importantEvents');
    addSnapshot(flowState.authority, flowState.asOf, 'flowAttribution');
    addSnapshot(supplyState.authority, supplyState.asOf, 'supplyDemand');
    if (guard && guard.visibilityLevel !== 'minimal') {
      const deadline = liveAuthorityExpiresAt(guard.asOf, 'visibilityGuard');
      if (deadline != null) deadlines.push(deadline);
    }
    if (rates.authority === 'fresh' && rates.data?.usdJpy) {
      const deadline = ratePointDecisionExpiresAt(rates.data.usdJpy);
      if (deadline != null) deadlines.push(deadline);
    }
    if (canonicalSessionAuthority.availability === 'available') {
      for (const row of Object.values(canonicalSessionAuthority.calendar ?? {})) {
        const deadline = exactAuthorityEpoch(row.sessionValidUntil);
        if (deadline != null) deadlines.push(deadline);
      }
    }
    for (const quote of [...(peJp.data?.stocks ?? []), ...(peUs.data?.stocks ?? [])]) {
      if (!quote.quoteTruth || !quoteDecisionUsable(quote.quoteTruth, nowMs)) continue;
      const deadline = quoteDecisionExpiresAt(quote.quoteTruth);
      if (deadline != null) deadlines.push(deadline);
    }
    for (const fund of fundNav.funds) {
      const deadline = calendarDateExpiresAt(fund.date, 7);
      if (deadline != null) deadlines.push(deadline);
    }
    for (const quote of Object.values(cw.byId ?? {})) {
      const deadline = liveAuthorityExpiresAt(quote.sourceTimestamp, 'cryptoQuote');
      if (deadline != null) deadlines.push(deadline);
    }
    return Math.min(...deadlines);
  }, [al.authority, al.data, regime.authority, regime.data, ev.authority, ev.data,
    downsideState.authority, downside, importantEventsState.authority, impEvents,
    flowState.authority, flowState.asOf, supplyState.authority, supplyState.asOf,
    guard, rates.authority, rates.data, canonicalSessionAuthority,
    peJp.data, peUs.data, fundNav.funds, cw.byId]);
  const positionExposure = useMemo(() => {
    const flowBySymbol: Record<string, string> = {};
    for (const r of flowRecords) flowBySymbol[r.symbol.toUpperCase()] = r.flowClass;
    const eventSymbols = new Set<string>();
    for (const ie of impEvents?.events ?? []) {
      if (ie.countdown === 'D' || ie.countdown === 'D-1') {
        for (const a of ie.linkedAssets ?? []) eventSymbols.add(String(a).toUpperCase());
      }
    }
    if (importantEventsUnknown) {
      for (const asset of assets) eventSymbols.add(asset.symbol.toUpperCase());
    }
    const regLabel = regime.data?.regime?.label ?? null;
    const sdRankBySymbol: Record<string, string> = {};
    for (const s of sdSignals) sdRankBySymbol[s.symbol.toUpperCase()] = s.supplyDemandRank;
    const pe = buildPositionExposure(
      assets,
      (a) => priceBySymbol.get(a.symbol.toUpperCase()),
      ratePointDecisionUsable(rates.data?.usdJpy)
        ? rates.data?.usdJpy?.latestValue ?? null : null,
      { regimeLabel: regLabel, riskOff: regLabel === 'RISK_OFF' || regLabel === 'EVENT_WAIT',
        flowBySymbol, eventSymbols, sdRankBySymbol },
    );
    if (publish) publishExposure(pe, derivedAuthorityValidUntilMs);
    return pe;
  }, [assets, rates.data, flowRecords, sdSignals, impEvents, regime.data, priceBySymbol,
    publish, importantEventsUnknown, derivedAuthorityValidUntilMs]);

  // v11.12.0: ACTION PRIORITY — 全レイヤーを「今日これを見る」に統合(端末内・保有加味)。
  const apItems: APItem[] = useMemo(() => {
    const sdBySym = new Map(sdSignals.map((s) => [s.symbol.toUpperCase(), s]));
    const flowBySym = new Map(flowRecords.map((r) => [r.symbol.toUpperCase(), r]));
    const riskBySym = new Map(positionExposure.risks.map((r) => [r.symbol, r.riskLevel]));
    // v13.5.61: the basis behind the level travels with it (drawdown / concentration / …).
    const riskTypesBySym = new Map<string, string[]>();
    for (const r of positionExposure.risks) {
      riskTypesBySym.set(r.symbol, [...(riskTypesBySym.get(r.symbol) ?? []), r.riskType]);
    }
    const regLabel = regime.data?.regime?.label ?? null;
    const riskOff = regLabel === 'RISK_OFF' || regLabel === 'EVENT_WAIT';
    const eventSyms = new Map<string, string>();
    for (const ie of impEvents?.events ?? []) {
      if (ie.countdown === 'D' || ie.countdown === 'D-1') {
        for (const a of ie.linkedAssets ?? []) eventSyms.set(String(a).toUpperCase(), ie.eventCode);
      }
    }
    // DQ modifier (modest): symbols whose past avoid_chase was contradicted
    const dqContra = new Set<string>();
    const dqSupp = new Set<string>();
    for (const r of listDQ()) {
      const it = r.outcome?.outcomeInterpretation;
      if (r.decisionContext === 'avoid_chase' && it === 'contradicted') dqContra.add(r.symbol.toUpperCase());
      if (it === 'supported') dqSupp.add(r.symbol.toUpperCase());
    }
    const items = assets.map((a) => {
      const sym = a.symbol.toUpperCase();
      const note = positionExposure.notes[sym];
      const sd = sdBySym.get(sym);
      const fl = flowBySym.get(sym);
      const missing: string[] = [];
      if (note?.held && note.pnlPct == null) missing.push(a.avgCost == null ? '取得単価' : '価格');
      if (!fl) missing.push('フロー未取得');
      if (a.market === 'JP' && !sd) missing.push('需給未取得');
      if ((a.market === 'JP' || a.market === 'US') && !priceBySymbol.has(sym)) {
        missing.push('現在価格未確認');
      }
      if (a.market === 'US' && fxAuthorityMissing) missing.push('USDJPY未確認');
      if (sessionAuthorityMissing) missing.push('市場セッション未確認');
      return buildAPItem({
        symbol: sym, market: a.market, assetName: a.displayNameJa || a.displayName,
        isHeld: !!note?.held, weightPct: note?.weightPct ?? null,
        concentrationRisk: positionExposure.top1Symbol === sym ? positionExposure.singleNameRisk : null,
        positionRiskLevel: riskBySym.get(sym) ?? null,
        positionRiskTypes: riskTypesBySym.get(sym) ?? [], plPct: note?.pnlPct ?? null,
        readiness: note?.readiness ?? null,
        sdRank: sd?.supplyDemandRank ?? null, sdCondition: sd?.condition ?? null,
        flowClass: fl?.flowClass ?? null,
        eventPending: importantEventsUnknown ? null : eventSyms.has(sym),
        eventName: importantEventsUnknown ? '重要イベント情報未確認' : eventSyms.get(sym) ?? null,
        regimeRiskOff: regLabel ? riskOff : null, changePct: fl?.changePct ?? null,
        dataMissing: missing,
        dqContradictedAvoidChase: dqContra.has(sym), dqSupported: dqSupp.has(sym),
      });
    });
    const ranked = rankAPItems(items, 20);
    if (publish) publishActionPriorities(ranked, derivedAuthorityValidUntilMs);
    return ranked;
  }, [assets, positionExposure, sdSignals, flowRecords, impEvents, regime.data, publish,
    importantEventsUnknown, priceBySymbol, fxAuthorityMissing, sessionAuthorityMissing,
    derivedAuthorityValidUntilMs]);

  // v11.13.0: SESSION BRIEF — 今日の作戦(端末内合成・保有加味)。
  const sessionBrief = useMemo(() => {
    const eventNames: string[] = [];
    for (const ie of impEvents?.events ?? []) {
      if (ie.countdown === 'D' || ie.countdown === 'D-1') eventNames.push(ie.eventCode);
    }
    if (importantEventsUnknown) eventNames.push('重要イベント情報未確認');
    const regLabel = regime.data?.regime?.label ?? null;
    const missing: string[] = [];
    if (!positionExposure.noHoldings && positionExposure.unpriced.length) {
      missing.push(`価格未取得: ${positionExposure.unpriced.slice(0, 2).join('/')}`);
    }
    const b = buildLocalBrief(apItems, {
      eventNames: [...new Set(eventNames)].slice(0, 3),
      riskOff: regLabel === 'RISK_OFF' || regLabel === 'EVENT_WAIT',
      missingDataJa: missing,
      sessionAuthority: canonicalSessionAuthority,
    });
    if (publish) publishSessionBrief(b, derivedAuthorityValidUntilMs);
    return b;
  }, [apItems, impEvents, regime.data, positionExposure, importantEventsUnknown,
    canonicalSessionAuthority, publish, derivedAuthorityValidUntilMs]);

  // v11.17.0: SCENARIOS — 条件付きの分岐(端末内合成・保有加味)。単一予測ではなく
  // ベース/強気/弱気/踏み上げ失速/イベント待ちを全レイヤーから決定論合成。帯のみ。
  const scenarioSets: LocalScenarioSet[] = useMemo(() => {
    const sdBySym = new Map(sdSignals.map((s) => [s.symbol.toUpperCase(), s]));
    const flowBySym = new Map(flowRecords.map((r) => [r.symbol.toUpperCase(), r]));
    const riskBySym = new Map(positionExposure.risks.map((r) => [r.symbol, r.riskLevel]));
    const regLabel = regime.data?.regime?.label ?? null;
    const riskOff = regLabel === 'RISK_OFF' || regLabel === 'EVENT_WAIT';
    const eventSyms = new Map<string, string>();
    for (const ie of impEvents?.events ?? []) {
      if (ie.countdown === 'D' || ie.countdown === 'D-1') {
        for (const a of ie.linkedAssets ?? []) eventSyms.set(String(a).toUpperCase(), ie.eventCode);
      }
    }
    const sets = assets.map((a) => {
      const sym = a.symbol.toUpperCase();
      const sd = sdBySym.get(sym);
      const fl = flowBySym.get(sym);
      return buildScenarioSet({
        symbol: sym, market: a.market, assetName: a.displayNameJa || a.displayName,
        isHeld: !!positionExposure.notes[sym]?.held,
        sdRank: sd?.supplyDemandRank ?? null, sdCondition: sd?.condition ?? null,
        sdLevel: (sd as { supplyDemandLevel?: string } | undefined)?.supplyDemandLevel ?? null,
        sdDirection: (sd as { direction?: string } | undefined)?.direction ?? null,
        flowClass: fl?.flowClass ?? null,
        eventPending: importantEventsUnknown || eventSyms.has(sym),
        eventName: importantEventsUnknown ? '重要イベント情報未確認' : eventSyms.get(sym) ?? null,
        regimeRiskOff: riskOff, changePct: fl?.changePct ?? null,
        positionRiskLevel: riskBySym.get(sym) ?? null,
        missing: [
          ...(!fl ? ['フロー未取得'] : []),
          ...(a.market === 'JP' && !sd ? ['需給未取得'] : []),
          ...((a.market === 'JP' || a.market === 'US') && !priceBySymbol.has(sym)
            ? ['現在価格未確認'] : []),
          ...(a.market === 'US' && fxAuthorityMissing ? ['USDJPY未確認'] : []),
          ...(sessionAuthorityMissing ? ['市場セッション未確認'] : []),
        ],
      });
    });
    if (publish) publishScenarios(sets, derivedAuthorityValidUntilMs);
    return sets;
  }, [assets, positionExposure, sdSignals, flowRecords, impEvents, regime.data, publish,
    importantEventsUnknown, priceBySymbol, fxAuthorityMissing, sessionAuthorityMissing,
    derivedAuthorityValidUntilMs]);

  // v11.19.0: PORTFOLIO STRATEGY — 役割分類(コア/サテライト/戦術枠/ヘッジ)と
  // FIRE整合・リスク予算を端末内で合成。計画層(下)へ制約を供給する。
  // v12.2.12: FIRE Core(fc)はストア経由でなく戻り値で下流に渡す — Asset Deskが
  // Today未訪問でも同一値になる(publish=trueの正本画面は共有ストアにも書く)。
  const strategyAndFire = useMemo(() => {
    const eventSyms = new Set<string>();
    for (const ie of impEvents?.events ?? []) {
      if (ie.countdown === 'D' || ie.countdown === 'D-1') {
        for (const a of ie.linkedAssets ?? []) eventSyms.add(String(a).toUpperCase());
      }
    }
    const roles = assets.map((a) => {
      const sym = a.symbol.toUpperCase();
      const note = positionExposure.notes[sym];
      return classifyRole({
        symbol: sym, assetName: a.displayNameJa || a.displayName,
        theme: themeOf(a), assetType: a.assetType,
        isHeld: !!note?.held, weightPct: note?.weightPct ?? null,
        concentrationRisk: positionExposure.top1Symbol === sym ? positionExposure.singleNameRisk : null,
        eventPending: importantEventsUnknown || eventSyms.has(sym),
      });
    });
    // v11.19.1: FIRE Core(投信=本丸資産)を先に合成し、戦略へ文脈供給
    const fc = buildLocalFireCore(assets, positionExposure, roles);
    if (publish) publishFireCore(fc, derivedAuthorityValidUntilMs);
    const s = buildStrategy(positionExposure, roles, {
      eventPending: importantEventsUnknown || eventSyms.size > 0,
      recurringAccumulationKnown: fc.contributionDataStatus === 'complete',
      fireCore: { known: fc.fireCoreTotal != null,
        tacticalToCoreBand: fc.tacticalToCoreBand,
        contributionKnown: fc.contributionDataStatus === 'complete' },
    });
    if (publish) publishStrategy(s, derivedAuthorityValidUntilMs);
    return { strategy: s, fireCore: fc };
  }, [assets, positionExposure, impEvents, publish, fireCoreMetaRevision,
    importantEventsUnknown, derivedAuthorityValidUntilMs]);
  const portfolioStrategy = strategyAndFire.strategy;
  const fireCore = strategyAndFire.fireCore;

  // v11.18.0: POSITION PLAN — 「入っていいか/買い増しか/利確検討か/持ち越しか」を
  // 計画として合成(端末内・保有加味・執行語なし)。売買指示ではない。
  const positionPlans: LocalPlan[] = useMemo(() => {
    const sdBySym = new Map(sdSignals.map((s) => [s.symbol.toUpperCase(), s]));
    const flowBySym = new Map(flowRecords.map((r) => [r.symbol.toUpperCase(), r]));
    const scBySym = new Map(scenarioSets.map((s) => [s.symbol, s]));
    const apBySym = new Map(apItems.map((it) => [it.symbol, it]));
    const riskBySym = new Map(positionExposure.risks.map((r) => [r.symbol, r.riskLevel]));
    const regLabel = regime.data?.regime?.label ?? null;
    const eventSyms = new Map<string, string>();
    for (const ie of impEvents?.events ?? []) {
      if (ie.countdown === 'D' || ie.countdown === 'D-1') {
        for (const a of ie.linkedAssets ?? []) eventSyms.set(String(a).toUpperCase(), ie.eventCode);
      }
    }
    const tacticalStretched = ['stretched', 'exceeded'].includes(portfolioStrategy.tacticalBudget);
    const themeHigh = ['high', 'critical'].includes(portfolioStrategy.themeRisk);
    const roleBySym = new Map(portfolioStrategy.roles.map((r) => [r.symbol, r]));
    const plans = assets.map((a) => {
      const sym = a.symbol.toUpperCase();
      const note = positionExposure.notes[sym];
      const sd = sdBySym.get(sym);
      const fl = flowBySym.get(sym);
      const role = roleBySym.get(sym);
      const aiTheme = role ? ['ai_infrastructure', 'physical_ai_robotics', 'semiconductor_photonics'].includes(role.theme) : false;
      return buildPlan({
        symbol: sym, market: a.market, assetName: a.displayNameJa || a.displayName,
        isHeld: !!note?.held,
        sdRank: sd?.supplyDemandRank ?? null, sdCondition: sd?.condition ?? null,
        sdLevel: (sd as { supplyDemandLevel?: string } | undefined)?.supplyDemandLevel ?? null,
        flowClass: fl?.flowClass ?? null,
        scenarioDominant: scBySym.get(sym)?.dominant ?? null,
        apCategory: apBySym.get(sym)?.category ?? null,
        eventPending: importantEventsUnknown || eventSyms.has(sym),
        eventName: importantEventsUnknown ? '重要イベント情報未確認' : eventSyms.get(sym) ?? null,
        regimeRiskOff: regLabel === 'RISK_OFF' || regLabel === 'EVENT_WAIT',
        weightPct: note?.weightPct ?? null,
        concentrationRisk: positionExposure.top1Symbol === sym ? positionExposure.singleNameRisk : null,
        positionRiskLevel: riskBySym.get(sym) ?? null,
        pnlPct: note?.pnlPct ?? null,
        priorRunupPct: null,
        marketSession: projectPlanningSession(a.market, canonicalSessionAuthority),
        missing: [
          ...(!fl ? ['フロー未取得'] : []),
          ...(a.market === 'JP' && !sd ? ['需給未取得'] : []),
          ...((a.market === 'JP' || a.market === 'US') && !priceBySymbol.has(sym)
            ? ['現在価格未確認'] : []),
          ...(a.market === 'US' && fxAuthorityMissing ? ['USDJPY未確認'] : []),
          ...(sessionAuthorityMissing ? ['市場セッション未確認'] : []),
        ],
        portfolioTacticalStretched: tacticalStretched,
        themeConcentrationHigh: themeHigh && aiTheme,
      });
    });
    // v11.19.0: 戦略上の役割をカード表示用に付与(端末内のみ)
    // v11.19.1: 投信はFIRE Core注記(積立/評価額の鮮度)を追記
    const fcPos = new Map(fireCore.positions.map((x) => [x.symbol, x]));
    for (const p of plans) {
      const r = roleBySym.get(p.symbol);
      if (r) {
        let reason = r.roleReasonJa;
        const fp = fcPos.get(p.symbol);
        if (fp) {
          reason = `FIRE Core(本丸資産)。積立${fp.monthlyContribution != null ? `月${fp.monthlyContribution.toLocaleString()}円` : '未入力'}`
            + `・評価額${fp.marketValue != null ? (fp.stale ? '更新が古い' : '追跡中') : '未入力'}(${fp.accountTypeJa})`;
        }
        p.strategicRole = { roleJa: fp ? 'FIRE Core' : r.roleJa, roleReasonJa: reason,
          addPolicyJa: r.addPolicyJa, strategyFit: r.strategyFit };
      }
    }
    if (publish) publishPlans(plans, derivedAuthorityValidUntilMs);
    return plans;
  }, [assets, positionExposure, sdSignals, flowRecords, scenarioSets, apItems,
    impEvents, regime.data, portfolioStrategy, fireCore, importantEventsUnknown,
    canonicalSessionAuthority, publish, priceBySymbol, fxAuthorityMissing,
    sessionAuthorityMissing, derivedAuthorityValidUntilMs]);

  const phase = combinePhase(al.phase as TodayPhase, regime.phase as TodayPhase,
    ev.phase as TodayPhase);
  const judgment = useMemo(
    () => deriveTodayJudgment(al.data, regime.data, ev.data, Date.now()),
    [al.data, regime.data, ev.data],
  );

  // 3-layer risk overlay for the hero (v10.103): a green global regime must not
  // hide a weak Japan tape or holder risk.
  const overlay = useMemo(() => {
    // Prefer the downside engine's globalRegime, but treat "UNKNOWN"/empty as a
    // miss and fall back to the regime endpoint's label (fixes Global=UNKNOWN
    // while Market Regime=Mixed when the downside read hit a cold cache, v10.112).
    const ds = downside?.globalRegime;
    const global = (ds && ds !== 'UNKNOWN') ? ds : (regime.data?.regime?.label || ds || 'UNKNOWN');
    return {
      globalRegime: global,
      jpIntradayOverlay: downsideUnknown ? 'CAUTION' : downside?.jpIntradayOverlay || 'NORMAL',
      holderRiskOverlay: downsideUnknown
        ? 'REVIEW_REQUIRED' : downside?.holderRiskOverlay || 'NONE',
    };
  }, [downside, regime.data, downsideUnknown]);
  // Partial-data discipline: when data is incomplete, cap confidence at 0.60 and
  // flag PARTIAL so a HOLD never looks high-confidence on thin data.
  // v13.5.54 (owner 2026-09-04: 「データ一部不足とは何か？全て与えているはず」).
  // PARTIAL was a bare boolean over eight unrelated conditions, so the screen
  // could say 「一部不足」 without ever saying WHAT — and the owner reasonably
  // read it as a complaint about data they had already supplied. Every one of
  // these is an ARGUS-side freshness/authority state, not a missing owner
  // input. Name them.
  // v13.5.60 (owner 2026-09-07): while the JP exchange is closed (weekend,
  // holiday, before/after the session) there is no newer flow or supply
  // evidence to have — an "expired" budget then means "the previous value
  // from the last session", not a shortfall. Only a live session can make
  // that evidence stale. Unavailable/failed refreshes stay shortfalls.
  const jpSessionNow = canonicalSessionAuthority.availability === 'available'
    ? String(canonicalSessionAuthority.calendar?.JP?.session ?? '') : '';
  const jpExchangeOpen = ['MORNING_SESSION', 'LUNCH_BREAK', 'AFTERNOON_SESSION']
    .includes(jpSessionNow);
  const flowPreviousValue = flowState.authority === 'expired' && jpSessionNow !== '' && !jpExchangeOpen;
  const supplyPreviousValue = supplyState.authority === 'expired' && jpSessionNow !== '' && !jpExchangeOpen;
  // A feed that answered FRESH with zero records is not stale and not missing:
  // there is simply nothing attributable right now (a closed market has no new
  // flow). The hook reports that as 'unavailable' because it has no records to
  // authorize; it must not read as 「鮮度切れ」 to the owner.
  const flowEmptyFresh = flowState.authority === 'unavailable' && !flowState.error
    && !!flowState.asOf && liveAuthorityState(flowState.asOf, 'flowAttribution') === 'fresh'
    && flowState.records.length === 0;
  const dataQualityNotes = [
    flowPreviousValue ? 'flow_previous_value_closed_session' : null,
    flowEmptyFresh ? 'flow_no_records_now' : null,
    supplyPreviousValue ? 'supply_previous_value_closed_session' : null,
  ].filter((code): code is string => code !== null);
  const partialReasonCodes = [
    phase === 'partial' ? 'watchlist_polling_partial' : null,
    importantEventsUnknown ? 'important_events_unread' : null,
    downsideUnknown ? 'downside_unread' : null,
    flowState.authority !== 'fresh' && !flowPreviousValue && !flowEmptyFresh ? 'flow_authority_stale' : null,
    supplyState.authority !== 'fresh' && !supplyPreviousValue ? 'supply_demand_authority_stale' : null,
    fxAuthorityMissing ? 'fx_authority_missing' : null,
    sessionAuthorityMissing ? 'session_authority_missing' : null,
    quoteAuthorityMissing ? 'quote_authority_missing' : null,
  ].filter((code): code is string => code !== null);
  const isPartial = partialReasonCodes.length > 0;
  const baseConf = regime.data?.regime?.confidence ?? null;
  // v10.195: fold the Visibility Risk Guard cap into the SAME confidence the hero +
  // judgment log use. Intersect base, the partial-0.60, and the guard cap — ignoring
  // nulls (a naive Math.min(...,undefined) would blank the hero as NaN).
  const capCandidates = [baseConf, isPartial ? 0.60 : null, guard?.confidenceCap ?? null]
    .filter((v): v is number => typeof v === 'number');
  const cappedConf = capCandidates.length ? Math.min(...capCandidates) : baseConf;
  const visLimited = !!guard && guard.visibilityLevel !== 'full';

  // v12.0.8追補: 保有リスクチップ(市場リスクと分離) — 保有×P0/P1件数から。
  const positionRisk = useMemo(() => {
    if (positionExposure.noHoldings) return { alert: false, ja: '保有数量未入力' };
    const n = apItems.filter((it) => it.isHeld && (it.priorityRank === 'P0' || it.priorityRank === 'P1')).length
      + positionExposure.risks.filter((r) => ['high', 'critical'].includes(r.riskLevel)).length;
    return n > 0 ? { alert: true, ja: `保有銘柄に要確認あり(${n}件)` }
                 : { alert: false, ja: '明確な警報なし' };
  }, [positionExposure, apItems]);

  // Round 2: owner-private context joins public evidence exactly here. Existing
  // AP/plan/scenario/rule/AI values remain evidence, never action selectors.
  const canonicalDecision = useMemo(() => {
    const sda = new Map<string, SingleDecisionAuthorityResultV2>();
    const bindings = new Map<string, PredictionLedgerSdaAdapterV2>();
    const views = new Map<string, AssetDecisionView>();
    const ruleBySym = new Map((al.data?.labels ?? []).map((row) => [row.symbol.toUpperCase(), row]));
    const aiBySym = new Map((aiJ.data?.labels ?? []).map((row) => [row.symbol.toUpperCase(), row]));
    const riskBySym = new Map(positionExposure.risks.map((row) => [row.symbol, row.riskLevel]));
    // v13.5.36 (review item C): uncapped imminent feed + impact tiering.
    // critical/high → WAIT_REQUIRED; medium/low → BLOCK_BUY only.
    const eventGate = imminentEventGate(impEvents);
    // v13.5.36 NEWS/EVENT SIGNAL → execution constraint, PER SUBJECT
    // (external review BLOCKER 2): the gate lives in domain/newsSignalGate —
    // only a MARKET-CONFIRMED, non-stale HIGH/CRITICAL bearish signal whose
    // target actually covers the subject blocks NEW buying. PENDING advises
    // on the Today strip only; news can never SELL/EXIT/WAIT the decision.
    const newsEvents = newsIntel?.events;
    const decisionMs = Date.now();
    const decisionAt = exactSecondUtc(decisionMs);
    const validChallengeTime = (value: unknown) => {
      const epoch = exactAuthorityEpoch(value);
      return epoch != null && epoch <= decisionMs ? exactSecondUtc(epoch) : null;
    };
    const aiAsOf = validChallengeTime(aiJ.data?.asOf);
    const ruleAsOf = validChallengeTime(al.data?.asOf);

    // Today headline instruments always receive a canonical decision even
    // when the owner has not added them to the watchlist (owner context
    // UNKNOWN → honest WAIT), so Today never falls back to an unledgered stub.
    const decisionSubjects: Array<{ symbol: string; market: string;
      quantity: number | null; headlineProxy?: boolean }> = assets.map((asset) => ({
        symbol: asset.symbol, market: asset.market,
        quantity: asset.quantity ?? null }));
    for (const head of [
      { symbol: '1321', market: 'JP' }, { symbol: '1306', market: 'JP' },
      { symbol: 'SPY', market: 'US' }, { symbol: 'QQQ', market: 'US' },
    ]) {
      if (!decisionSubjects.some((row) =>
        row.symbol.toUpperCase() === head.symbol)) {
        // v13.5.60 (owner 2026-09-07): a headline index ETF the owner has not
        // added to Holdings is NOT HELD — not "unknown". Its owner context is
        // therefore complete and the SDA can evaluate instead of data-gating.
        decisionSubjects.push({ ...head, quantity: null, headlineProxy: true });
      }
    }
    for (const asset of decisionSubjects) {
      const sym = asset.symbol.toUpperCase();
      const market = ['JP', 'US', 'CRYPTO', 'FUND'].includes(asset.market)
        ? asset.market as 'JP' | 'US' | 'CRYPTO' | 'FUND' : 'FUND';
      // v13.5.13: canonical artifact references from the backend resolver
      // boundary. When present and valid, the decision binds to the evidence
      // cutoff; otherwise the data-gated stub path stays (fail closed).
      const evidenceEntry = (decisionEvidence.subjects ?? {})[sym] as unknown;
      const resolved = evidenceEntry
        ? resolveCanonicalArtifactReferences(evidenceEntry, decisionMs)
        : null;
      const cutoffAt = resolved?.informationCutoffAt ?? decisionAt;
      const cutoffMs = resolved ? Date.parse(cutoffAt) : decisionMs;
      const positionState: 'UNKNOWN' | 'HELD' | 'NOT_HELD' =
        asset.headlineProxy && asset.quantity == null ? 'NOT_HELD'
        : asset.quantity == null
        || !Number.isFinite(asset.quantity) || asset.quantity < 0 ? 'UNKNOWN'
        : asset.quantity > 0 ? 'HELD' : 'NOT_HELD';
      const ownerNote = positionExposure.notes[sym];
      const { positionRiskBand: positionBand, concentrationBand } = deriveLocalOwnerRiskBands({
        positionState,
        flaggedPositionRisk: riskBySym.get(sym) ?? null,
        positionRiskKnown: ownerNote?.held === true && ownerNote.pnlPct != null,
        concentrationWeightPct: ownerNote?.weightPct,
      });
      // A verified canonical market-truth reference is a stronger quote
      // authority than the display quote map (which only covers the owner's
      // own watchlist polling).
      const missingQuote = (market === 'JP' || market === 'US')
        && !priceBySymbol.has(sym)
        && resolved?.marketTruth.status !== 'AVAILABLE';
      const addPermission: 'UNKNOWN' | 'BLOCKED' | 'ALLOWED' = positionState === 'UNKNOWN' ? 'UNKNOWN'
        : missingQuote || sessionAuthorityMissing
          || positionBand === 'HIGH' || positionBand === 'CRITICAL'
          ? 'BLOCKED' : 'ALLOWED';
      const ownerContext = {
        schemaVersion: 'owner-decision-context-v1' as const,
        privacyClass: 'DEVICE_LOCAL' as const,
        asOf: cutoffAt,
        positionState,
        positionRiskBand: positionBand,
        concentrationBand,
        addPermission,
      };
      const input = buildDataGatedInputV2({
        subject: { kind: 'ASSET', instrumentId: sym, market, horizon: 'FIVE_DAY' },
        decisionAt,
        informationCutoffAt: cutoffAt,
        authorityPolicy: SINGLE_DECISION_AUTHORITY_V2_POLICY,
        ownerContext,
      });
      if (resolved) {
        input.marketTruth = resolved.marketTruth;
        input.predictionLedger = resolved.predictionLedger;
        input.sho = resolved.sho;
        input.quality = resolved.quality;
      }

      const contributions: RiskContributionV1[] = [];
      const row = riskBySym.get(sym);
      if (row) contributions.push({
        evidenceRef: `portfolio:risk-${sym.toLowerCase()}`,
        primitiveFactorId: 'portfolio.position_risk',
        sourceKind: 'PORTFOLIO',
        constraint: row === 'critical' ? 'EXIT_RISK'
          : row === 'high' ? 'REDUCE_RISK' : row === 'medium' ? 'BLOCK_BUY' : 'NONE',
        status: 'ACTIVE', severity: riskBand(row),
        confidenceCapBps: row === 'critical' ? 4000 : row === 'high' ? 5500 : 8000,
        observedAt: cutoffAt,
      });
      if (positionExposure.top1Symbol === sym && positionExposure.singleNameRisk) {
        contributions.push({
          evidenceRef: `concentration:single-${sym.toLowerCase()}`,
          primitiveFactorId: 'portfolio.single_name_concentration',
          sourceKind: 'CONCENTRATION',
          constraint: ['high', 'critical'].includes(positionExposure.singleNameRisk)
            ? 'BLOCK_BUY' : 'NONE',
          status: 'ACTIVE', severity: riskBand(positionExposure.singleNameRisk),
          confidenceCapBps: 7500, observedAt: cutoffAt,
        });
      }
      const subjectTheme = themeOf({ symbol: sym, market,
        assetType: market === 'JP' ? 'jp_equity' : 'us_equity',
        displayName: sym, displayNameJa: sym });
      const newsHit = newsKernelGate(newsEvents, {
        symbol: sym, market, theme: subjectTheme });
      if (newsHit) contributions.push({
        evidenceRef: `news:impact-${newsHit.matchedTarget.toLowerCase()}-${String(newsHit.eventId).slice(0, 32).toLowerCase()}`,
        primitiveFactorId: 'news.market_impact_confirmed', sourceKind: 'EVENT',
        constraint: 'BLOCK_BUY', status: 'ACTIVE',
        severity: newsHit.severity === 'CRITICAL' ? 'HIGH' : 'MEDIUM',
        confidenceCapBps: 5500, observedAt: cutoffAt,
      });
      // v13.5.36 (external review item 7): a calendar FEED outage is an
      // availability failure, not decision-critical unknowability — it must
      // not data-gate every symbol into WAIT (single point of failure).
      // Unknown calendar → BLOCK_BUY + confidence cap + diagnostic ref;
      // held-position judgment continues on verified evidence.
      const gateEntry = eventGate.get(sym);
      if (importantEventsUnknown) {
        contributions.push({
          evidenceRef: `event:calendar-unavailable-${sym.toLowerCase()}`,
          primitiveFactorId: 'event.calendar_uncertainty', sourceKind: 'EVENT',
          constraint: 'BLOCK_BUY', status: 'ACTIVE', severity: 'MEDIUM',
          confidenceCapBps: 4500, observedAt: cutoffAt,
        });
      } else if (gateEntry) {
        contributions.push({
          evidenceRef: `event:calendar-${sym.toLowerCase()}`,
          primitiveFactorId: 'event.calendar_uncertainty', sourceKind: 'EVENT',
          constraint: eventKernelConstraint(gateEntry.displayImpact),
          status: 'ACTIVE',
          severity: eventKernelSeverity(gateEntry.displayImpact),
          confidenceCapBps: 5000, observedAt: cutoffAt,
        });
      }
      // v13.5.36 (review item F): the old composite lever data-gated EVERY
      // symbol when ONE auxiliary feed (flow/supply/FX/labels) went stale.
      // Split: per-symbol decision authorities (quote, session) still gate;
      // degraded auxiliary feeds only block new adds and cap confidence —
      // held-position judgment continues on the evidence that IS verified.
      const coreAuthorityMissing = missingQuote || sessionAuthorityMissing;
      if (coreAuthorityMissing) {
        contributions.push({
          evidenceRef: `discipline:authority-${sym.toLowerCase()}`,
          primitiveFactorId: 'discipline.required_authority', sourceKind: 'DISCIPLINE',
          constraint: 'NONE', status: 'MISSING', severity: 'UNKNOWN',
          confidenceCapBps: 2500, observedAt: cutoffAt,
        });
      } else if (isPartial || visLimited) {
        contributions.push({
          evidenceRef: `discipline:degraded-feeds-${sym.toLowerCase()}`,
          primitiveFactorId: 'discipline.degraded_auxiliary_feeds', sourceKind: 'DISCIPLINE',
          constraint: 'BLOCK_BUY', status: 'ACTIVE', severity: 'MEDIUM',
          confidenceCapBps: 4000, observedAt: cutoffAt,
        });
      }
      input.riskKernel = buildRiskKernel({
        schemaVersion: 'argus-risk-discipline-input-v1',
        subject: { kind: 'ASSET', instrumentId: sym, market },
        asOf: cutoffAt, informationCutoffAt: cutoffAt,
        policy: RISK_DISCIPLINE_POLICY,
        contributions,
      });

      const challenges = [] as typeof input.challengeEvidence;
      const challengeTime = (value: string | null) =>
        value != null && Date.parse(value) <= cutoffMs ? value : null;
      const aiTime = challengeTime(aiAsOf);
      const ruleTime = challengeTime(ruleAsOf);
      const ai = aiBySym.get(sym);
      if (ai && aiTime) challenges.push({
        challengeId: `ai.${sym.toLowerCase()}`, sourceKind: 'AI', status: 'AVAILABLE',
        asOf: aiTime, proposedAction: legacyFiveAction(ai.aiFinalAction),
        dissentReasonCodes: [], evidenceRefs: [`ai:judgment-${sym.toLowerCase()}`],
      });
      const rule = ruleBySym.get(sym);
      if (rule && ruleTime) challenges.push({
        challengeId: `legacy.${sym.toLowerCase()}`, sourceKind: 'LEGACY', status: 'AVAILABLE',
        asOf: ruleTime, proposedAction: legacyFiveAction(rule.action),
        dissentReasonCodes: [], evidenceRefs: [`legacy:action-label-${sym.toLowerCase()}`],
      });
      input.challengeEvidence = challenges.sort((left, right) =>
        left.challengeId.localeCompare(right.challengeId));

      const result = evaluateSingleDecisionAuthority(verifyDecisionEvidence(input));
      const adapter = buildPredictionLedgerV2Adapter(result);
      sda.set(sym, result);
      bindings.set(sym, adapter);
      views.set(sym, projectCanonicalAssetDecision({
        symbol: sym, result, ruleLabel: rule, aiLabel: ai, meta: aiMeta,
      }));
    }
    return { sda, bindings, views };
  }, [assets, al.data, aiJ.data, aiMeta, positionExposure, impEvents, newsIntel,
    importantEventsUnknown, priceBySymbol, decisionEvidence,
    sessionAuthorityMissing, isPartial, visLimited]);
  // Ledger appends run one symbol per idle slice: the append itself verifies
  // and re-encodes the stored document, which is far too heavy to run for the
  // whole watchlist inside one main-thread task on a phone. FIFO order and
  // every append are preserved — only the scheduling changes.
  const sdaAppendQueue = useRef<Array<readonly [
    SingleDecisionAuthorityResultV2, PredictionLedgerSdaAdapterV2]>>([]);
  const sdaDrainArmed = useRef(false);
  useEffect(() => {
    for (const [symbol, result] of canonicalDecision.sda) {
      const adapter = canonicalDecision.bindings.get(symbol);
      if (adapter) sdaAppendQueue.current.push([result, adapter] as const);
    }
    const scheduleDrain = () => {
      if (sdaDrainArmed.current || sdaAppendQueue.current.length === 0) return;
      sdaDrainArmed.current = true;
      const runSlice = () => {
        sdaDrainArmed.current = false;
        const pair = sdaAppendQueue.current.shift();
        if (!pair) return;
        const [result, adapter] = pair;
        appendDeviceLocalSdaLedger(result, adapter);
        scheduleDrain();
      };
      const idle = (globalThis as {
        requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
      }).requestIdleCallback;
      if (idle) idle(runSlice, { timeout: 4_000 });
      else setTimeout(runSlice, 50);
    };
    scheduleDrain();
  }, [canonicalDecision]);
  const sdaBySymbol = canonicalDecision.sda;
  const sdaLedgerBindingBySymbol = canonicalDecision.bindings;
  const decisionBySym = canonicalDecision.views;

  return {
    assets, aiJ, al, guard, regime, downside, events247, impEvents, rates,
    jpQuotes: peJp, usQuotes: peUs, cryptoWatch: cw, fundNav, priceBySymbol,
    flowRecords, sdSignals,
    cardGroups, cardBySym, ownerCritical,
    positionExposure, apItems, sessionBrief, scenarioSets,
    portfolioStrategy, fireCore, positionPlans,
    phase, judgment, overlay, isPartial, partialReasonCodes, dataQualityNotes, visLimited, cappedConf,
    importantEventsUnknown,
    positionRisk,
    aiMeta, decisionBySym, sdaBySymbol, sdaLedgerBindingBySymbol,
  };
}
