import React, { useEffect, useMemo, useState } from 'react';
import { PageShell } from './PageShell';
import { useLocale, tEn } from '../i18n';
import { useAssetIntel } from '../hooks/useAssetIntel';
import { latestActionPriorities, latestSessionBrief, latestFireCore, publishEventsJa, publishDataQuality, latestDataQuality } from '../lib/positionExposureShare';
import { maybeDailySnapshot } from '../lib/portfolioSync';
import { maybeUpdateOutcomes } from '../lib/decisionQuality';
import { ProHandoffButton } from '../components/dashboard/ProHandoffButton';
import { MobileStickyCommand } from '../components/dashboard/MobileStickyCommand';
import { runNotificationEngine } from '../lib/notifications';
import { assessBackupSafety } from '../lib/backupSafety';
import { listSnapshots } from '../lib/portfolioSync';
import type { RouteKey } from '../components/NavRail';
import type { SettingsSection } from '../navigation';
import '../components/dashboard/Dashboard.css';
import { ArgusTodayPanel } from '../components/today/ArgusTodayPanel';
import { buildArgusTodayView, selectTodayNews,
  selectAutoMarket, type MarketSelectionMode, type TodayMoveInput,
  type TodayPositioningRow } from '../domain/argusTodayView';
import { useTodayHeadline } from '../hooks/useTodayHeadline';
import { useDecisionEvidence } from '../hooks/useDecisionEvidence';
import { useMarketShock } from '../hooks/useMarketShock';
import { useNewsIntelligence } from '../hooks/useNewsIntelligence';
import { headlineProjectionInput,
  type TodayHeadlineEntry } from '../lib/todayHeadline';
import { useMarketLedger } from '../hooks/useMarketLedger';
import { useChartIntelligence, useIndexChart } from '../hooks/useChartIntelligence';
import { useMarketNews } from '../hooks/useMarketNews';
import type { ChartIntelligencePayload } from '../types/chartIntelligence';
import type { TodayProjectionInput } from '../domain/argusTodayView';
import {
  MARKET_INSTRUMENTS, marketInstrument, normalizeMarketInstrument,
  INDEX_FOR_INSTRUMENT, INDEX_DISPLAY_JA,
  type MarketHorizon, type MarketInstrumentSymbol,
} from '../domain/marketInstruments';
import { useAssets } from '../hooks/useAssets';
import { usePublicDiagnostics } from '../hooks/useSystemHealth';
import {
  buildDataGatedInputV2, evaluateSingleDecisionAuthority,
  SINGLE_DECISION_AUTHORITY_V2_POLICY,
  type SingleDecisionAuthorityResultV2,
} from '../domain/singleDecisionAuthority';

interface Props {
  onNavigate: (key: RouteKey) => void;
  /** V12.2.12: Asset Deskの当該銘柄カードを開いてスクロール(App.tsx state経由)。 */
  onNavigateToAsset?: (symbol: string, section?: string) => void;
  onNavigateToSettings?: (section: SettingsSection) => void;
}

// Today is a SUMMARY composed from LIVE data (action-labels + market-regime +
// events). Detail lives on the respective detail pages.
const formatDate = (iso: string) => {
  const d = new Date(`${iso}T00:00:00+09:00`);
  return d.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
  });
};

function instrumentLabel(payload: ChartIntelligencePayload): string {
  if (payload.symbol === '1321') return '日経225 ETF（1321）';
  const name = (payload.displayNameJa || payload.symbol).trim();
  return name.includes(payload.symbol) ? name : `${name}（${payload.symbol}）`;
}

function projectionInput(payload: ChartIntelligencePayload | null): TodayProjectionInput | null {
  if (!payload) return null;
  return { symbol: payload.symbol, label: instrumentLabel(payload), asOf: payload.periodEnd,
    status: payload.status, authorityState: 'current', timeframe: payload.timeframe,
    quoteState: payload.quoteState ?? 'CLOSE',
    sourceHistoryCount: payload.indicators.bars.length,
    instrumentId: payload.instrumentMetadata?.instrumentId,
    source: payload.instrumentMetadata?.source ?? 'existing_market_data_cache',
    availableFrom: payload.instrumentMetadata?.availableFrom,
    assetType: payload.instrumentMetadata?.assetType,
    proxyFor: payload.symbol === '1321'
      ? (payload.instrumentMetadata?.proxyFor ?? 'Nikkei 225') : payload.instrumentMetadata?.proxyFor,
    licenseStatus: payload.symbol === '1321'
      ? (payload.instrumentMetadata?.licenseStatus ?? 'license_unverified')
      : (payload.instrumentMetadata?.licenseStatus ?? 'not_applicable'),
    disclosureJa: (payload as { indexDisclosureJa?: string | null })
      .indexDisclosureJa ?? null,
    bars: payload.indicators.bars, zones: payload.zones,
    eventMarkers: payload.eventMarkers,
    turningPoints: payload.turningPoints,
    calibration: payload.todayIntelligence?.calibration,
    shortSelling: payload.todayIntelligence?.shortSelling ?? null,
    failedRally: payload.todayIntelligence?.failedRally ?? null,
    historyStart: payload.todayIntelligence?.historyCoverage.start ?? null,
    historyEnd: payload.todayIntelligence?.historyCoverage.end ?? null };
}

function headlineMove(entry: TodayHeadlineEntry | undefined,
  id: string): TodayMoveInput | null {
  if (!entry || entry.status !== 'ready') return null;
  const bars = (entry.bars ?? []).filter((bar) =>
    Number.isFinite(bar.close) && bar.close > 0);
  const latest = bars.at(-1), previous = bars.at(-2);
  if (!latest) return null;
  const changePct = previous && previous.close > 0
    ? (latest.close - previous.close) / previous.close * 100 : null;
  const label = instrumentLabel({ symbol: entry.instrument,
    displayNameJa: entry.displayNameJa } as ChartIntelligencePayload);
  return { id, symbol: entry.instrument,
    market: entry.market === 'JP' ? 'JP' : 'US', label,
    value: latest.close, previous: previous?.close ?? null,
    directionLabel: changePct == null ? undefined
      : `${changePct >= 0 ? '▲' : '▼'}${Math.abs(changePct).toFixed(1)}%`,
    asOf: latest.date,
    status: entry.payloadStatus === 'delayed' ? 'delayed' : 'close',
    history: bars.slice(-12).map((bar) => ({ date: bar.date, value: bar.close })) };
}

function marketMove(payload: ChartIntelligencePayload | null, id: string): TodayMoveInput | null {
  const bars = payload?.indicators.bars.filter((bar) => Number.isFinite(bar.close) && bar.close > 0) ?? [];
  const latest = bars.at(-1), previous = bars.at(-2);
  if (!latest || !payload) return null;
  const changePct = previous && previous.close > 0 ? (latest.close - previous.close) / previous.close * 100 : null;
  return { id, symbol: payload.symbol, market: payload.market === 'JP' ? 'JP' : 'US',
    label: instrumentLabel(payload), value: latest.close, previous: previous?.close ?? null,
    directionLabel: changePct == null ? undefined : `${changePct >= 0 ? '▲' : '▼'}${Math.abs(changePct).toFixed(1)}%`,
    asOf: latest.date, status: payload.status === 'delayed' ? 'delayed' : 'close',
    history: bars.slice(-12).map((bar) => ({ date: bar.date, value: bar.close })) };
}

const signed = (value: number, digits = 0) => `${value > 0 ? '+' : ''}${value.toFixed(digits)}`;
const oku = (value: number) => `${Math.round(value / 100_000_000).toLocaleString('ja-JP')}億`;

function missingTodayDecision(symbol: string, market: 'JP' | 'US', assets: ReturnType<typeof useAssets>['assets']):
  SingleDecisionAuthorityResultV2 {
  const now = new Date(Math.floor(Date.now() / 1000) * 1000).toISOString().replace('.000Z', 'Z');
  const local = assets.find((asset) => asset.symbol.toUpperCase() === symbol.toUpperCase());
  const positionState = local?.quantity == null ? 'UNKNOWN'
    : local.quantity > 0 ? 'HELD' : 'NOT_HELD';
  return evaluateSingleDecisionAuthority(buildDataGatedInputV2({
    subject: { kind: 'ASSET', instrumentId: symbol.toUpperCase(), market, horizon: 'FIVE_DAY' },
    decisionAt: now, informationCutoffAt: now,
    authorityPolicy: SINGLE_DECISION_AUTHORITY_V2_POLICY,
    ownerContext: {
      schemaVersion: 'owner-decision-context-v1', privacyClass: 'DEVICE_LOCAL', asOf: now,
      positionState, positionRiskBand: 'UNKNOWN', concentrationBand: 'UNKNOWN',
      addPermission: 'UNKNOWN',
    },
  }));
}

export const CommandCenter: React.FC<Props> = ({ onNavigate, onNavigateToAsset, onNavigateToSettings }) => {
  useLocale();   // re-render Today on locale switch
  const assetsApi = useAssets();
  // V12.2.12: 個別銘柄系のデータ組み立ては useAssetIntel(Today/Asset Desk共有の
  // 正本)へ移設。Todayは publish:true — 共有ストアへのpublish副作用(Exposure/AP/
  // Brief/Scenarios/Plans/Strategy/FireCore)は従来どおりTodayだけが実行する。
  const {
    assets, regime, impEvents, rates, events247,
    flowRecords, sdSignals, positionExposure,
    apItems, sessionBrief, scenarioSets, portfolioStrategy, positionPlans,
    judgment, isPartial, partialReasonCodes, dataQualityNotes, visLimited,
    overlay, sdaBySymbol, importantEventsUnknown, jpQuotes,
  } = useAssetIntel({ publish: true, assets: assetsApi.assets });
  // v13.5.59 (owner): every JP code is shown with its company name — the
  // Tachibana rows carry codes only, so the name comes from the JP quote rows
  // and the device asset list (long names are shortened by jpDisplay).
  const jpNameBySymbol = useMemo(() => {
    const names: Record<string, string> = {};
    for (const asset of assetsApi.assets) {
      if (asset.market === 'JP' && (asset.displayNameJa || asset.displayName)) {
        names[asset.symbol.toUpperCase()] = asset.displayNameJa || asset.displayName;
      }
    }
    for (const row of jpQuotes.data?.stocks ?? []) {
      const name = (row as { nameJa?: string; name?: string }).nameJa || row.name;
      if (name) names[String(row.symbol).toUpperCase()] = name;
    }
    return names;
  }, [assetsApi.assets, jpQuotes.data]);
  // Headline ETFs have their own backend-only quote reads. They are not added
  // to the user's watchlist and never cause a browser-side provider request.
  // v13.5.1: the selector tiles no longer render quotes, so the two
  // tile-only watchlist requests are gone from the startup path entirely.
  const marketLedger = useMarketLedger();
  const marketNews = useMarketNews();
  const { diagnostics: publicDiagnostics } = usePublicDiagnostics();
  const [marketMode, setMarketMode] = useState<MarketSelectionMode>(() => {
    try {
      const saved = localStorage.getItem('argus.today.marketSelection.v1');
      return saved === 'JP' || saved === 'US' ? saved : 'AUTO';
    } catch { return 'AUTO'; }
  });
  const changeMarketMode = (mode: MarketSelectionMode) => {
    setMarketMode(mode);
    try { localStorage.setItem('argus.today.marketSelection.v1', mode); } catch { /* device-local best effort */ }
  };
  const [selectedInstrument, setSelectedInstrument] = useState<{
    JP: MarketInstrumentSymbol; US: MarketInstrumentSymbol;
  }>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('argus.today.selectedInstrument.v1') || '{}') as Record<string, string>;
      return {
        JP: normalizeMarketInstrument('JP', saved.JP),
        US: normalizeMarketInstrument('US', saved.US),
      };
    } catch { return { JP: '1321', US: 'SPY' }; }
  });
  const changeInstrument = (market: 'JP' | 'US', symbol: string) => {
    const next = { ...selectedInstrument,
      [market]: normalizeMarketInstrument(market, symbol) };
    setSelectedInstrument(next);
    setMarketMode(market);
    try {
      localStorage.setItem('argus.today.selectedInstrument.v1', JSON.stringify(next));
      localStorage.setItem('argus.today.marketSelection.v1', market);
    } catch { /* device-local best effort */ }
  };
  const [chartHorizon, setChartHorizon] = useState<MarketHorizon>(() => {
    try {
      const saved = Number(localStorage.getItem('argus.today.chartHorizon.v1'));
      return saved === 1 || saved === 20 ? saved : 5;
    } catch { return 5; }
  });
  const changeChartHorizon = (value: MarketHorizon) => {
    setChartHorizon(value);
    try { localStorage.setItem('argus.today.chartHorizon.v1', String(value)); } catch { /* device-local */ }
  };
  const decisionCalendar = !marketLedger.error && !marketLedger.loading
    && !marketLedger.sessionExpired
    ? marketLedger.ledger?.phase3?.calendar ?? null : null;
  const effectiveMarket = marketMode === 'AUTO'
    ? selectAutoMarket(decisionCalendar)
    : marketMode;
  const selectedSymbol = selectedInstrument[effectiveMarket];
  const selectedDefinition = marketInstrument(selectedSymbol)!;
  const selectedChart = useChartIntelligence({
    scope: 'market', symbol: selectedSymbol, market: selectedDefinition.market,
    timeframe: 'daily', horizon: chartHorizon, enabled: true,
  });
  // v13.5.54 (owner 2026-09-04: 「日経平均などの指数がトップに表示されていない、
  // まだETF」). SHO reasons about the Nikkei 225, not the 1321 ETF that tracks
  // it, so the headline series is the INDEX itself. The verified ETF snapshot
  // stays the decision anchor — the index carries no licensed quote here — and
  // the panel says so; only the drawn series and its projection change.
  const headlineIndex = useIndexChart(
    INDEX_FOR_INSTRUMENT[selectedSymbol] ?? null, 'daily');
  // v13.5.0 restoration: selector summaries, index moves, and the four
  // headline mini-charts come from one compact canonical bootstrap response
  // instead of four multi-megabyte verified snapshots. Only the selected
  // instrument still loads its heavy verified snapshot (above), so the Today
  // screen no longer waits on ~13 MB of serial transport and hashing.
  const headline = useTodayHeadline();
  const marketShock = useMarketShock();
  const newsIntel = useNewsIntelligence();
  // v11.9.0/v11.17.0: one automatic LOCAL snapshot per JST day once holdings
  // price — scenarioSummary込みで「あの日ARGUSが何を言っていたか」を残す(送信なし)。
  useEffect(() => {
    try {
      const flowBySymbol: Record<string, string> = {};
      for (const r of flowRecords) flowBySymbol[r.symbol.toUpperCase()] = r.flowClass;
      const tops = latestActionPriorities().slice(0, 7).map((x) => ({
        symbol: x.symbol, rank: x.priorityRank, actionLabel: x.actionLabel, blockingReason: x.blockingReason }));
      maybeDailySnapshot(positionExposure, __APP_VERSION__, flowBySymbol,
        sdSignals.map((s) => ({ symbol: s.symbol, rank: s.supplyDemandRank, condition: s.condition,
          level: (s as { supplyDemandLevel?: string }).supplyDemandLevel } as never)), tops,
        (() => { const b = latestSessionBrief(); return b ? { headlineJa: b.headlineJa, ownerMode: b.ownerMode,
          sessionType: b.sessionType, nextChecksJa: b.nextChecksJa, whatNotToDoJa: b.whatNotToDoJa } : null; })(),
        scenarioSets.map((s) => ({ symbol: s.symbol, dominant: s.dominant, evidenceQuality: s.evidenceQuality })),
        positionPlans.map((p) => ({ symbol: p.symbol, planType: p.planType, currentStance: p.currentStance,
          blockingReasons: p.blockingReasons, evidenceQuality: p.evidenceQuality })),
        portfolioStrategy.noHoldings ? undefined : {
          strategyMode: portfolioStrategy.strategyMode, fireStatus: portfolioStrategy.fireStatus,
          corePct: portfolioStrategy.corePct, satellitePct: portfolioStrategy.satellitePct,
          tacticalPct: portfolioStrategy.tacticalPct, hedgePct: portfolioStrategy.hedgePct,
          tacticalBudget: portfolioStrategy.tacticalBudget,
          themeRisk: portfolioStrategy.themeRisk, singleNameRisk: portfolioStrategy.singleNameRisk,
          roles: portfolioStrategy.roles.filter((r) => r.weightPct != null)
            .map((r) => ({ symbol: r.symbol, role: r.role, addPolicy: r.addPolicy })) },
        (() => { const f = latestFireCore(); return f && f.positions.length ? {
          mutualFundTotal: f.mutualFundTotal, fireCoreTotal: f.fireCoreTotal,
          monthlyContributionTotal: f.monthlyContributionTotal,
          tacticalToCoreRatio: f.tacticalToCoreRatio, tacticalToCoreBand: f.tacticalToCoreBand,
          contributionDataStatus: f.contributionDataStatus,
          valuationDataStatus: f.valuationDataStatus, staleCount: f.staleCount } : undefined; })(),
        (() => { const d = latestDataQuality(); return d ? {
          overallStatus: d.overallStatus, topIssues: d.topIssuesJa.slice(0, 4),
          expectedDisabled: d.expectedDisabledJa.slice(0, 3) } : undefined; })());
    } catch { /* quota */ }
  }, [positionExposure, scenarioSets, positionPlans, portfolioStrategy, sdSignals, flowRecords]);

  // v11.14.0: 通知エンジン — 変化検知のみ(60sスロットル+dedupe+静音時間内蔵)。
  useEffect(() => {
    const t = setTimeout(() => {
      try {
        const sdBySymbol: Record<string, { rank: string; condition: string; level?: string; name?: string; isHeld?: boolean }> = {};
        for (const s of sdSignals) {
          sdBySymbol[s.symbol.toUpperCase()] = {
            rank: s.supplyDemandRank, condition: s.condition,
            level: (s as { supplyDemandLevel?: string }).supplyDemandLevel,
            name: s.name, isHeld: !!positionExposure.notes[s.symbol.toUpperCase()]?.held };
        }
        const flowBySymbol: Record<string, { flowClass: string; name?: string; isHeld?: boolean }> = {};
        for (const r of flowRecords) {
          flowBySymbol[r.symbol.toUpperCase()] = { flowClass: r.flowClass, name: r.name,
            isHeld: !!positionExposure.notes[r.symbol.toUpperCase()]?.held };
        }
        const snaps = listSnapshots();
        const age = snaps.length
          ? Math.floor((Date.now() - Date.parse(snaps[0].createdAt)) / 86_400_000) : null;
        const scenarioBySymbol: Record<string, { dominant: string; name?: string;
          isHeld?: boolean; summaryJa?: string }> = {};
        for (const s of scenarioSets) {
          scenarioBySymbol[s.symbol] = { dominant: s.dominant, name: s.assetName,
            isHeld: s.isHeld, summaryJa: s.summaryJa };
        }
        const planBySymbol: Record<string, { planType: string; currentStance: string;
          name?: string; isHeld?: boolean; summaryJa?: string }> = {};
        for (const p of positionPlans) {
          planBySymbol[p.symbol] = { planType: p.planType, currentStance: p.currentStance,
            name: p.assetName, isHeld: p.isHeld, summaryJa: p.summaryJa };
        }
        const backupSafety = assessBackupSafety(assets);
        const canonicalDecisions = Object.fromEntries([...sdaBySymbol.entries()].map(([symbol, result]) => {
          const asset = assets.find((row) => row.symbol.toUpperCase() === symbol);
          return [symbol, { action: result.primaryAction, status: result.status,
            name: asset?.displayNameJa ?? asset?.displayName, isHeld: (asset?.quantity ?? 0) > 0 }];
        }));
        runNotificationEngine({
          apItems, eventNames: [...new Set((impEvents?.events ?? [])
            .filter((ie) => ie.countdown === 'D' || ie.countdown === 'D-1')
            .map((ie) => ie.eventCode))],
          marketShockEvents: (marketShock.view?.events ?? []).map((event) => ({
            eventId: event.eventId, eventClass: event.eventClass,
            severity: event.severity,
            headlineJa: event.headlineJa, whyJa: event.whyJa,
          })),
          newsIntelEvents: (newsIntel.view?.events ?? []).map((event) => ({
            eventId: event.eventId, revision: event.revision,
            severity: event.severity, headlineJa: event.headlineJa,
            whyJa: event.whyJa, confirmationState: event.confirmationState,
            alertEligible: event.alertEligible,
            translationStatus: event.translationStatus,
          })),
          sdBySymbol, flowBySymbol, scenarioBySymbol, planBySymbol,
          strategyState: portfolioStrategy.noHoldings ? null : {
            tactical: portfolioStrategy.tacticalBudget, single: portfolioStrategy.singleNameRisk,
            theme: portfolioStrategy.themeRisk, fire: portfolioStrategy.fireStatus,
            summaryJa: portfolioStrategy.summaryJa },
          fireCoreState: (() => { const f = latestFireCore(); return f && f.positions.length ? {
            valuation: f.valuationDataStatus, contribution: f.contributionDataStatus,
            ratio: f.tacticalToCoreBand } : null; })(),
          briefSession: sessionBrief.sessionType,
          hasHoldings: !positionExposure.noHoldings,
          snapshotAgeDays: age,
          vaultConfigured: backupSafety.vaultConfigured,
          localExportAgeDays: backupSafety.exportAgeDays,
          restoreVerified: backupSafety.restoreVerified,
          canonicalDecisions,
        });
      } catch { /* never break Today */ }
    }, 12_000);
    return () => clearTimeout(t);
  }, [apItems, sdSignals, flowRecords, sessionBrief, impEvents, positionExposure,
    scenarioSets, positionPlans, portfolioStrategy, assets, sdaBySymbol,
    marketShock.view, newsIntel.view]);

  // Recovery Phase A: publish the one shared, fixed public diagnostics
  // snapshot. AppShell and Settings consume this same request-backed store.
  useEffect(() => {
    if (!publicDiagnostics) return;
    const overall = publicDiagnostics.service.overall;
    const freshness = publicDiagnostics.freshness.overall;
    publishDataQuality({
      overallStatus: overall,
      overallStatusJa: overall === 'ok' ? '稼働中' : '一部確認が必要',
      topIssuesJa: overall === 'ok' ? [] : [`公開診断: ${overall} / 鮮度 ${freshness}`],
      expectedDisabledJa: publicDiagnostics.freshness.expectedDisabledCount
        ? [`仕様上無効なソース ${publicDiagnostics.freshness.expectedDisabledCount}件`]
        : [],
    });
  }, [publicDiagnostics]);

  // v11.20.0: AI Review Pack用のイベント一行群(パック内でイベント要約は1回のみ)
  useEffect(() => {
    // v12.0.8 Part B: パックにもイベント名だけでなく日付を必ず含める
    const lines = (impEvents?.events ?? []).slice(0, 6).map((ie) =>
      `${ie.eventCode} ${ie.title} — ${ie.date ?? '日付未確認'}${ie.jstTime ? ` ${String(ie.jstTime).slice(11)}` : ''} (${ie.countdown})${ie.actual ? ` / 結果: ${ie.actual}` : ''}`);
    publishEventsJa(lines);
  }, [impEvents]);

  // v11.11.0: device-local outcome updater — once per JST day, fills
  // 「その後どうなったか」(1d/3d/5d/20d) for past decision records.
  useEffect(() => {
    const backend = import.meta.env.VITE_ARGUS_BACKEND_URL as string | undefined;
    if (!backend) return;
    const t = setTimeout(() => { void maybeUpdateOutcomes(backend); }, 8000);
    return () => clearTimeout(t);
  }, []);


  const argusToday = useMemo(() => {
    const dataQuality = isPartial || visLimited ? 'PARTIAL' as const : 'LIVE' as const;
    // v13.5.54: 「一部不足」 must be able to say what is missing. The guard's
    // reduced visibility is the ninth cause and is only known here.
    const dataQualityReasonCodes = visLimited
      ? [...partialReasonCodes, 'visibility_limited'] : partialReasonCodes;
    const summary = marketLedger.ledger?.summary ?? {};
    const factorState = (value: string | undefined): '↑' | '→' | '↓' | '△' | '—' | 'JP' | 'US' | 'HIGH' | 'LOW' => {
      if (['INFLOW', 'RISING', 'OVERHEAT_CANDIDATE'].includes(value ?? '')) return '↑';
      if (['OUTFLOW', 'FALLING', 'OVERSOLD_CANDIDATE'].includes(value ?? '')) return '↓';
      if (value === 'HIGH' || value === 'LOW') return value;
      return value && value !== 'UNKNOWN' ? '△' : '—';
    };
    const selectedJpChart = effectiveMarket === 'JP'
      ? selectedChart.decisionData : null;
    const selectedUsChart = effectiveMarket === 'US'
      ? selectedChart.decisionData : null;
    const headlineEntry = (symbol: string) => {
      const entry = headline.document?.instruments?.[symbol];
      return entry?.status === 'ready' ? entry : undefined;
    };
    // The chart projection is presentation of the verified snapshot the hook
    // currently holds (cached or settled), so the warm cache stays the visible
    // authority during background revalidation. Decision consumers keep the
    // stricter decisionData (CURRENT_READY + fresh only). Only the selected
    // market's projection is ever displayed, so only it is supplied.
    // v13.5.1: switching the selector must feel immediate. The verified heavy
    // snapshot remains the preferred source, but until it arrives for the
    // newly selected instrument/horizon the projection renders from the
    // already-verified compact headline (same canonical values for the
    // visible 30-day window) — labeled via data-projection-source.
    const headlineFallback = (symbol: string) =>
      headlineProjectionInput(headlineEntry(symbol));
    // The index is the owner-facing series; the ETF snapshot remains the
    // decision anchor and the fallback when the index cache is still cold.
    // v13.5.57: the index is DISPLAY; the heading must still name the
    // decision subject (the verified ETF) so a reader — and the release
    // acceptance contract — can see which instrument the WAIT/HOLD is
    // anchored on.
    const indexProjection = headlineIndex.data
      ? (() => {
        const base = projectionInput(headlineIndex.data);
        return base ? { ...base, label: `${base.label}・判断の正本 ${selectedInstrument[effectiveMarket]}` } : null;
      })() : null;
    const selectedJpProjection = effectiveMarket === 'JP'
      ? (indexProjection ?? (selectedChart.data ? projectionInput(selectedChart.data)
        : headlineFallback(selectedInstrument.JP))) : null;
    const selectedUsProjection = effectiveMarket === 'US'
      ? (indexProjection ?? (selectedChart.data ? projectionInput(selectedChart.data)
        : headlineFallback(selectedInstrument.US))) : null;
    const shortState = selectedJpChart?.todayIntelligence?.shortSelling;
    const jpFactors = [
      { key: 'TREND' as const, state: regime.data?.regime?.label === 'RISK_ON' ? '↑' as const : regime.data?.regime?.label === 'RISK_OFF' ? '↓' as const : '△' as const, source: 'market-regime' },
      { key: 'BREADTH' as const, state: factorState(summary.breadth), source: 'market-ledger' },
      { key: 'FLOW' as const, state: factorState(summary.foreignFlow), source: 'market-ledger' },
      { key: 'SHORT' as const, state: shortState?.latest?.previousDayDifference == null ? '—' as const
        : shortState.latest.previousDayDifference < 0 ? '↓' as const : '↑' as const,
      source: 'jquants-daily-short-ratio' },
      { key: 'CLOSE' as const, state: '—' as const, source: 'closing-window' },
    ];
    const usBars = selectedUsChart?.indicators.bars ?? [];
    const usLatest = usBars.at(-1);
    const usFactors = [
      { key: 'TREND' as const, state: usLatest?.ma?.['25'] == null ? '—' as const
        : usLatest.close >= usLatest.ma['25']! ? '↑' as const : '↓' as const,
      source: 'us-ohlcv' },
      { key: 'BREADTH' as const, state: '△' as const, source: 'market-regime' },
      { key: 'RELATIVE' as const, state: usBars.length >= 21
        ? (usBars.at(-1)!.close >= usBars.at(-21)!.close ? '↑' as const : '↓' as const)
        : '—' as const,
      source: 'spy-qqq-relative' },
      { key: 'FLOW' as const, state: '—' as const, source: 'us-volume-proxy' },
      { key: 'CLOSE' as const, state: '—' as const, source: 'closing-window' },
    ];
    // v13.5.54: an event whose announcement TIME is not published (Treasury
    // auctions, BOJ meeting days) still has a published DATE. Mapping those to
    // `at: null` dropped them from every forward-looking surface, so Today's
    // NEXT EVENT named US CPI while the market brief in the same screen said
    // the next thing to check was Monday's 10-Year auction. Anchor a date-only
    // event to the END of its JST day — it stays ahead of us for the whole day
    // it lands on, and `dateOnly` keeps the UI from inventing a clock time.
    const eventRows = (impEvents?.events ?? []).map((event) => {
      const timed = event.eventTimeUtc || (event.jstTime
        ? String(event.jstTime).replace(' JST', '').replace(' ', 'T') + ':00+09:00'
        : null);
      const dateOnly = !timed && !!event.date && /^\d{4}-\d{2}-\d{2}$/.test(event.date);
      return {
        id: event.eventId, code: event.eventCode, title: event.title,
        at: timed || (dateOnly ? `${event.date}T23:59:59+09:00` : null),
        dateOnly,
        impact: event.displayImpact, lifecycle: event.lifecycle,
        lifecycleTier: event.lifecycleTier ?? null,
        descriptionJa: event.rationaleJa,
      };
    });
    const indexMoves: TodayMoveInput[] = [];
    for (const move of [
      headlineMove(headlineEntry('1321'), 'nikkei'),
      headlineMove(headlineEntry('1306'), 'topix'),
      headlineMove(headlineEntry('SPY'), 'sp500'),
      headlineMove(headlineEntry('QQQ'), 'nasdaq'),
    ]) if (move) indexMoves.push(move);
    const macroMoves: TodayMoveInput[] = [];
    const addRate = (id: string, label: string, point: NonNullable<typeof rates.data>['us10y'] | undefined, suffix: string, direction?: string) => {
      const value = point?.latestValue;
      if ((point?.status === 'live' || point?.status === 'delayed')
          && typeof value === 'number' && Number.isFinite(value)) macroMoves.push({ id, label,
        value, previous: point.previousValue, suffix, directionLabel: direction,
        asOf: point.latestDate, status: 'close' });
    };
    addRate('usdjpy', 'USDJPY', rates.data?.usdJpy, '', (rates.data?.usdJpy?.change ?? 0) > 0 ? '円安'
      : (rates.data?.usdJpy?.change ?? 0) < 0 ? '円高' : '横ばい');
    // v13.5.61: a flat change is 「→」, not 「↓」 (VIX 14.53 → 14.53 read as a fall).
    const arrow = (change: number | null | undefined) => (change ?? 0) > 0 ? '↑' : (change ?? 0) < 0 ? '↓' : '→';
    addRate('us10y', 'US10Y', rates.data?.us10y, '%', arrow(rates.data?.us10y?.change));
    addRate('vix', 'VIX', rates.data?.vix, '', arrow(rates.data?.vix?.change));
    const attention = [
      ...(impEvents?.events ?? []).filter((event) => event.daysUntil === 0 && ['critical', 'high'].includes(event.displayImpact))
        .map((event) => ({ id: event.eventId, label: event.eventCode,
          time: event.jstTime ? String(event.jstTime).slice(11, 16) : null, severity: event.displayImpact === 'critical' ? 5 : 4 })),
      ...events247.filter((event) => event.severity >= 4)
        .map((event) => ({ id: event.eventId, label: event.nameJa || event.symbol || event.eventType,
          time: null, severity: event.severity })),
    ];
    const ledgerRows = new Map((marketLedger.ledger?.table ?? []).map((row) => [row.seriesId, row]));
    const metric = (ids: string[]) => ids.map((id) => ledgerRows.get(id)?.latestValue
      ?? marketLedger.ledger?.derivedMetrics.find((row) => row.metricId === id)?.value)
      .find((value): value is number => typeof value === 'number' && Number.isFinite(value));
    const jpPositioning: TodayPositioningRow[] = [];
    const credit = ledgerRows.get('credit.short_balance');
    if (credit?.latestValue != null) jpPositioning.push({ key: 'credit-numeric', label: '売残',
      value: oku(credit.latestValue), detail: credit.thresholdDistance == null ? undefined
        : `8千億差 ${signed(credit.thresholdDistance / 100_000_000)}億`,
      tone: credit.thresholdSide === 'above' ? 'negative' : 'neutral' });
    const dailyShort = selectedJpChart?.todayIntelligence?.shortSelling?.latest;
    if (dailyShort) jpPositioning.push({ key: 'daily-short-ratio', label: 'SHORT',
      value: `${dailyShort.totalShortRatio.toFixed(1)}%`,
      detail: dailyShort.previousDayDifference == null ? undefined
        : `${dailyShort.previousDayDifference > 0 ? '▲' : dailyShort.previousDayDifference < 0 ? '▼' : '→'}${Math.abs(dailyShort.previousDayDifference).toFixed(1)}pt`,
      tone: (dailyShort.previousDayDifference ?? 0) > 0 ? 'negative' : 'positive' });
    const foreign = ledgerRows.get('flow.foreign');
    if (foreign?.fourPeriodTotal != null) jpPositioning.push({ key: 'foreign-4w', label: '海外4週',
      value: oku(foreign.fourPeriodTotal), detail: foreign.fourPeriodDirection === 'up' ? '↑' : foreign.fourPeriodDirection === 'down' ? '↓' : '→',
      tone: foreign.fourPeriodTotal > 0 ? 'positive' : foreign.fourPeriodTotal < 0 ? 'negative' : 'neutral' });
    const ratio6 = metric(['breadth.prime.ratio6', 'breadth.ratio6']);
    const ratio25 = metric(['breadth.prime.ratio25', 'breadth.ratio25']);
    if (ratio6 != null || ratio25 != null) jpPositioning.push({ key: 'breadth-ratios', label: '騰落比率',
      value: [ratio6 == null ? null : `6日${ratio6.toFixed(0)}`, ratio25 == null ? null : `25日${ratio25.toFixed(0)}`].filter(Boolean).join(' / ') });
    const jpRs = headlineEntry('1321')?.relativeStrengthSummary?.nikkeiSp500Change20Pct
      ?? (effectiveMarket === 'JP'
        ? selectedChart.decisionData?.relativeStrength?.nikkei_sp500?.change20Pct : null);
    if (jpRs != null) jpPositioning.push({ key: 'relative-numeric', label: '日米強弱',
      value: jpRs >= 0 ? 'JP優位' : 'US優位', detail: `${signed(jpRs, 1)}pt`,
      tone: jpRs >= 0 ? 'positive' : 'negative' });

    const usPositioning: TodayPositioningRow[] = [];
    const change20 = (bars: Array<{ close: number }> | undefined) => {
      const rows = (bars ?? []).filter((bar) => bar.close > 0);
      return rows.length >= 21 ? (rows.at(-1)!.close / rows.at(-21)!.close - 1) * 100 : null;
    };
    const qqq20 = change20(headlineEntry('QQQ')?.bars);
    const spy20 = change20(headlineEntry('SPY')?.bars);
    if (qqq20 != null && spy20 != null) usPositioning.push({ key: 'us-relative-numeric', label: 'NASDAQ対SPY',
      value: `${signed(qqq20 - spy20, 1)}pt`, detail: qqq20 >= spy20 ? 'NASDAQ優位' : 'SPY優位',
      tone: qqq20 >= spy20 ? 'positive' : 'negative' });
    const usVolume = headlineEntry(selectedInstrument.US)?.bars?.at(-1)?.volumeRatio20
      ?? selectedUsChart?.indicators.bars.at(-1)?.volumeRatio20;
    if (usVolume != null) usPositioning.push({ key: 'us-volume-regime', label: '出来高',
      value: `${usVolume.toFixed(2)}×`, detail: usVolume >= 1.2 ? '増加' : usVolume <= .8 ? '低調' : '平常',
      tone: usVolume >= 1.2 ? 'positive' : 'neutral' });
    const positioning = { JP: jpPositioning, US: usPositioning };
    const news = selectTodayNews((marketNews.data?.items ?? []).map((item, index) => ({
      id: `${item.datetime ?? index}:${item.url}`, titleJa: item.displayTitleJa ?? '',
      titleOriginal: item.titleOriginal ?? item.headline, source: item.source, url: item.url,
      publishedAt: item.datetime, major: item.major, relevant: item.relevant,
      translationStatus: item.translationStatus, tier: item.tier, corroboration: item.corroboration,
      linkedSymbols: item.linkedSymbols,
    })), assets.map((asset) => asset.symbol));
    const backup = assessBackupSafety(assets);
    const ownerPriorities = [...apItems]
      .filter((item) => item.priorityRank !== 'Ignore')
      .sort((left, right) => right.priorityScore - left.priorityScore
        || left.symbol.localeCompare(right.symbol))
      .slice(0, 3)
      .map((item, rank) => {
        const canonical = sdaBySymbol.get(item.symbol.toUpperCase());
        const action = canonical?.primaryAction ?? 'WAIT';
        return {
        symbol: item.symbol,
        name: item.assetName,
        rank,
        reasonJa: item.whyJa,
        statusJa: item.priorityRankJa,
        isHeld: item.isHeld,
        impact: action === 'BUY' ? 'Good' as const
          : action === 'REDUCE' || action === 'EXIT' ? 'Bad' as const : 'Neutral' as const,
        actionJa: `${action} · 最終判断${canonical?.status === 'DATA_GATED' ? ' DATA GATED' : ''}`,
        checkNextJa: item.checkNextJa,
        whatWouldChangeJa: item.whatWouldChangeJa,
      }; });
    const selectedSymbol = selectedInstrument[effectiveMarket];
    const canonicalDecision = sdaBySymbol.get(selectedSymbol)
      ?? missingTodayDecision(selectedSymbol, effectiveMarket, assets);
    const now = new Date();
    return buildArgusTodayView({
      now, selectionMode: marketMode,
      calendar: decisionCalendar,
      dataQuality, dataQualityReasonCodes, dataQualityNotes,
      globalRisk: overlay.globalRegime,
      factors: { JP: jpFactors, US: usFactors },
      events: eventRows, eventsAuthorityUnknown: importantEventsUnknown,
      indexMoves, macroMoves, positioning, attention,
      holdings: ownerPriorities, news,
      newsCardState: {
        status: marketNews.data?.status ?? 'unavailable',
        lastChecked: marketNews.lastChecked,
        lastSuccessfulPollAt: marketNews.data?.lastSuccessfulPollAt
          ?? (marketNews.data?.status === 'live' ? marketNews.data.asOf : null),
        fetchedCount: marketNews.data?.fetchedCount ?? marketNews.data?.items.length ?? 0,
        relevantCount: news.length,
        stale: marketNews.data?.stale ?? marketNews.data?.status !== 'live',
        failureClass: marketNews.failureClass,
      },
      projection: {
        JP: selectedJpProjection,
        US: selectedUsProjection,
      },
      selectedInstrument,
      systemStatus: { data: dataQuality, backup: backup.protectionLevelJa,
        rule: `最終判断 ${canonicalDecision.status}` },
      canonicalDecision,
    });
  }, [judgment, overlay, isPartial, partialReasonCodes, dataQualityNotes, visLimited, marketLedger.ledger,
    regime.data, impEvents, rates.data, events247,
    assets, apItems, marketMode,
    headline.document, marketNews.data,
    selectedChart.data, headlineIndex.data,
    marketNews.lastChecked, marketNews.failureClass,
    selectedInstrument, effectiveMarket, selectedChart.decisionData, decisionCalendar,
    sdaBySymbol]);

  // v13.5.1: the four instruments are lightweight NAME selectors only.
  const todayInstruments = useMemo(() => MARKET_INSTRUMENTS.map((item) => {
    // v13.5.54: the tab names the INDEX the owner thinks in; the ETF that
    // anchors the decision stays visible in the full label.
    const indexJa = INDEX_DISPLAY_JA[INDEX_FOR_INSTRUMENT[item.symbol]];
    return {
      symbol: item.symbol, market: item.market,
      shortLabel: indexJa,
      fullLabel: `${indexJa}（判断の正本: ${item.shortLabel}）`,
      instrumentType: item.instrumentType, underlying: item.underlying,
    };
  }), []);

  // Which canonical source feeds the visible projection right now.
  const projectionSource = selectedChart.data ? 'verified-snapshot' as const
    : argusToday.projection ? 'headline' as const : null;

  // Truthful freshness (v13.5.36 phase 2): three separated concepts —
  // realtime status / daily-session basis / decision eligibility. A normal
  // closed market must never read as an error; a genuinely missing newer
  // bar must never hide behind 「市場終了」.
  const decisionEvidence = useDecisionEvidence();
  const freshnessNoteJa = useMemo(() => {
    const entry = headline.document?.instruments?.[selectedSymbol];
    if (!entry || entry.status !== 'ready') return null;
    // v13.5.53 (owner 2026-09-04): the JP lamp read 「JP LUNCH」 while this line
    // read 「市場終了」 in the same screenshot. LUNCH_BREAK and PRE_MARKET carry
    // tone 'standby', so keying "closed" off `tone !== 'open'` declared the
    // Tokyo session over at 12:10 — it reopens at 12:30. Only tone 'closed' is
    // actually a finished session; a standby session showing an EOD price is
    // the same situation as an open one and gets the same honest line.
    const lamp = argusToday.sessionLamps.find((row) => row.key === entry.market);
    const sessionClosed = lamp?.tone === 'closed';
    const quoteState = String(entry.quoteState ?? 'CLOSE').toUpperCase();
    const eod = quoteState === 'CLOSE' || quoteState === 'STALE';
    if (!sessionClosed && eod) {
      return `表示価格は前日終値（${entry.periodEnd ?? '基準日不明'} EOD）· `
        + 'ザラ場のリアルタイム価格ではありません';
    }
    if (!eod) return null;
    const basisDate = entry.periodEnd ?? '不明';
    const closeLabel = entry.market === 'JP'
      ? `${basisDate} 終値基準（15:30 JST）` : `${basisDate} 終値基準`;
    const evidenceEntry = decisionEvidence.subjects?.[selectedSymbol] as
      { marketTruth?: { status?: string } } | undefined;
    const truthStatus = evidenceEntry?.marketTruth?.status;
    const dailyEligible = truthStatus === 'AVAILABLE';
    const dailyDenied = truthStatus === 'STALE' || truthStatus === 'MISSING';
    if (dailyEligible) {
      return `市場終了 · ${closeLabel} · 日次判断: 利用可能`;
    }
    if (dailyDenied) {
      return `市場終了 · 最新日足を確認できません（${closeLabel}のまま）· `
        + '日次判断を保留';
    }
    return `市場終了 · ${closeLabel}`;
  }, [headline.document, selectedSymbol, argusToday.sessionLamps,
    decisionEvidence.subjects]);

  return (
    <PageShell
      title={tEn('page.today')}
      subtitle={<span>{formatDate(judgment.date)}</span>}
      className="page--today"
    >
      <ArgusTodayPanel view={argusToday} instruments={todayInstruments}
        selectedSymbol={selectedSymbol} horizon={chartHorizon}
        chartLoad={selectedChart} onMode={changeMarketMode}
        projectionSource={projectionSource} freshnessNoteJa={freshnessNoteJa}
        jpNameBySymbol={jpNameBySymbol}
        shock={{ status: marketShock.status,
          events: marketShock.view?.events ?? [] }}
        newsIntel={{ status: newsIntel.status,
          events: newsIntel.view?.events ?? [] }}
        onInstrument={changeInstrument} onHorizon={changeChartHorizon}
        onNavigate={onNavigate} onNavigateToAsset={onNavigateToAsset}
        onNavigateToSettings={onNavigateToSettings}
        aiButton={<ProHandoffButton nextEvent={argusToday.nextEvent} />} />
      <MobileStickyCommand text={argusToday.footerText} />
    </PageShell>
  );
};
