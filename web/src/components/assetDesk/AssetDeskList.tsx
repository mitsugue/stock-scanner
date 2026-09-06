import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  DndContext, closestCenter, PointerSensor, KeyboardSensor, useSensor, useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy, arrayMove, useSortable,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { AssetIntel } from '../../hooks/useAssetIntel';
import { useCatalysts } from '../../hooks/useCatalysts';
import { fundNavForAsset } from '../../hooks/useFundNav';
import { coingeckoIdOf } from '../../lib/cryptoIds';
import { deriveStrategy, type QuoteLite } from '../../lib/assetStrategy';
import { GENRES, genreOf, type AssetItem } from '../../types/assetItem';
import type { ActionLabel } from '../../types/actionLabels';
import type { CatalystItem } from '../../types/catalysts';
import type { DownsideIncident } from '../../hooks/useDownsideIncidents';
import {
  buildDecisionFirstView, deskRank,
  type DeskRankInput, type DeskGenre,
} from '../../domain/assetDesk';
import type { DeskCardData, DeskEventTag, DeskSection } from './types';
import { sectionAnchorId, DESK_SECTIONS } from './types';
import { AssetDecisionCard } from './AssetDecisionCard';
import { fmtPrice, freshnessOf } from './deskFormat';
import { bestAssetName } from '../../lib/assetStrategy';
import { DownsideIncidentQueue } from '../dashboard/DownsideIncidentCard';
import { t } from '../../i18n';
import { normalizeLiveQuote } from '../../domain/liveQuote';
import './AssetDesk.css';

// V12.2.12 — Asset Deskリスト(旧AssetStrategySectionの後継)。
// データ組み立てはHoldings所有の共有Asset Intelをpropsで受け取り、
// domain/assetDecision経由でTodayと構造的に同一の判断を表示する。
// 並び: 資産区分を固定し、区分内の端末保存順を長押しで変更する。

export interface AssetFocusIntent { symbol: string; section?: string; nonce: number }

interface Props {
  assets: AssetItem[];
  intel: AssetIntel;
  onReorder: (orderedIds: string[]) => void;
  onRemove: (id: string) => void;
  onUpdateHolding: (id: string, h: { quantity?: number | null; avgCost?: number | null }) => void;
  focus?: AssetFocusIntent | null;
  toolbar?: React.ReactNode;
  /** Lean v13 contextual detail: render only this asset, fully expanded. */
  detailSymbol?: string;
  /** List rows open the contextual Asset Detail route instead of duplicating it inline. */
  onOpenAsset?: (symbol: string, section?: string) => void;
}

// 手動順モードの行(DnDハンドル+カード)
const SortableCardRow: React.FC<{
  id: string; children: (handle: React.ReactNode) => React.ReactNode;
}> = ({ id, children }) => {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const style: React.CSSProperties = { transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.6 : 1 };
  return (
    <div ref={setNodeRef} style={style}>
      {children(
        <button className="ad-handle" aria-label={`${id}を長押しして並べ替え`} {...attributes} {...listeners}>長押し</button>,
      )}
    </div>
  );
};

export const AssetDeskList: React.FC<Props> = ({
  assets, intel, onReorder, onRemove, onUpdateHolding, focus, toolbar, detailSymbol, onOpenAsset,
}) => {
  const cat = useCatalysts();
  const navFunds = intel.fundNav.funds;
  const mountTs = useMemo(() => Date.now(), []);
  const [nowMs] = useState(() => Date.now());
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [localFocus, setLocalFocus] = useState<AssetFocusIntent | null>(null);
  const [filter, setFilter] = useState<
    'all' | 'risk' | 'held' | 'exit-watch' | 'inspect' | 'hold' | 'new-stop'
  >('all');
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { delay: 450, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  // ── quotes/labels/cats/incidents(旧AssetStrategySection.mapsを移設) ──
  const maps = useMemo(() => {
    const quotes = new Map<string, QuoteLite>();
    for (const s of intel.jpQuotes.data?.stocks ?? []) quotes.set(s.symbol, {
      price: s.price, changePct: s.changePct, volume: s.volume, date: s.date,
      status: s.status, flow: s.flow ?? null, name: s.name, quoteTruth: s.quoteTruth,
      tachibana: (s as { tachibana?: QuoteLite['tachibana'] }).tachibana ?? null,
    });
    for (const s of intel.usQuotes.data?.stocks ?? []) quotes.set(s.symbol, {
      price: s.price, changePct: s.changePct, volume: s.volume, date: s.date,
      status: s.status, flow: s.flow ?? null, name: s.name, quoteTruth: s.quoteTruth,
    });
    for (const a of assets) {
      if (a.market !== 'CRYPTO') continue;
      const id = coingeckoIdOf(a);
      const q = id ? intel.cryptoWatch.byId[id] : undefined;
      if (q) quotes.set(a.symbol, {
        price: q.priceUsd, changePct: q.changePct, volume: q.volume,
        date: q.date, status: q.status,
        // v13.5.54: the CoinGecko payload already carries the full source-time
        // evidence (sourceTimestamp / receivedAt / ageSec / realtimeEvidence).
        // Dropping it here made the ONE genuinely 24h-live feed render as
        // "asOf <date> (日付のみ) · age 未検証" — an understatement of what we
        // actually know. Pass the evidence through and let classifyDelay judge
        // it; the LIVE claim still has to clear its own age proof.
        quoteTruth: normalizeLiveQuote({
          symbol: a.symbol, price: q.priceUsd, changePct: q.changePct,
          date: q.date, status: q.status, provider: 'CoinGecko',
          sourceTimestamp: q.sourceTimestamp, receivedAt: q.receivedAt,
          ageSec: q.ageSec, delayClass: q.delayClass,
          realtimeEvidence: q.realtimeEvidence,
        }, { symbol: a.symbol, instrumentType: 'CRYPTO', provider: 'CoinGecko' }),
      });
    }
    const labels = new Map<string, ActionLabel>();
    for (const l of intel.al.data?.labels ?? []) labels.set(l.symbol, l);
    const cats = new Map<string, CatalystItem>();
    for (const c of cat.data?.items ?? []) cats.set(c.symbol, c);
    const downsideBySym = new Map<string, DownsideIncident>();
    for (const inc of intel.downside?.incidents ?? []) downsideBySym.set(inc.symbol, inc);
    // 投信(基準価額): fund資産をカタログNAVへ名寄せ(旧実装のまま)
    for (const a of assets) {
      if (genreOf(a) === 'funds') {
        const f = fundNavForAsset(a, navFunds);
        if (f) quotes.set(a.symbol, {
          price: f.navYen, changePct: f.changePct ?? 0, volume: 0,
          date: f.date, status: 'delayed',
          quoteTruth: normalizeLiveQuote({
            symbol: a.symbol, price: f.navYen, changePct: f.changePct ?? 0,
            date: f.date, status: 'delayed', provider: '投信総合ライブラリー',
            delayClass: 'EOD',
          }, {
            symbol: a.symbol, instrumentType: 'FUND',
            provider: '投信総合ライブラリー',
          }),
        });
      }
    }
    return { quotes, labels, cats, downsideBySym };
  }, [intel.jpQuotes.data, intel.usQuotes.data, intel.cryptoWatch.byId, intel.al.data, cat.data, intel.downside, navFunds, assets]);

  // イベントタグ(全countdown — 閉じたカードは先頭2件のみ表示)
  const eventTagsBySym = useMemo(() => {
    const m = new Map<string, DeskEventTag[]>();
    for (const ie of intel.impEvents?.events ?? []) {
      for (const a of ie.linkedAssets ?? []) {
        const k = String(a).toUpperCase();
        const arr = m.get(k) ?? [];
        arr.push({ code: ie.eventCode, countdown: ie.countdown, impact: ie.displayImpact.toUpperCase() });
        m.set(k, arr);
      }
    }
    return m;
  }, [intel.impEvents]);

  // ── カードデータ束の組み立て(表示専用・判断は生成しない) ──
  const rows = useMemo(() => {
    const aiBySym = new Map((intel.aiJ.data?.labels ?? []).map((l) => [l.symbol.toUpperCase(), l]));
    const sdBySym = new Map(intel.sdSignals.map((s) => [s.symbol.toUpperCase(), s]));
    const apBySym = new Map(intel.apItems.map((it) => [it.symbol, it]));
    const scBySym = new Map(intel.scenarioSets.map((s) => [s.symbol, s]));
    const plBySym = new Map(intel.positionPlans.map((p) => [p.symbol, p]));
    const riskBySym = new Map(intel.positionExposure.risks.map((r) => [r.symbol, r.riskLevel]));
    return assets.map((a) => {
      const sym = a.symbol.toUpperCase();
      const genre = genreOf(a) as DeskGenre;
      const quote = maps.quotes.get(a.symbol);
      const strat = deriveStrategy(a, maps.labels.get(a.symbol), quote, maps.cats.get(a.symbol), mountTs);
      const incident = maps.downsideBySym.get(a.symbol);
      const card = intel.cardBySym.get(sym);
      const decision = intel.decisionBySym.get(sym);
      const sda = intel.sdaBySymbol.get(sym);
      const apx = apBySym.get(sym);
      const pn = intel.positionExposure.notes[sym];
      const themeConcentrationPct = pn
        ? intel.positionExposure.byTheme.find((theme) => theme.ja === pn.themeJa)?.pct ?? null
        : null;
      const eventTags = eventTagsBySym.get(sym) ?? [];
      const held = !!pn?.held || (a.quantity ?? 0) > 0;
      const rankInput: DeskRankInput = {
        symbol: sym, genre, held,
        signalCode: card?.signalCode ?? null,
        apRank: apx?.priorityRank ?? null,
        positionRiskLevel: riskBySym.get(sym) ?? null,
        hasIncident: !!incident,
        aiRuleDisagree: !!decision?.rule.disagreementJa,
        eventSoon: eventTags.some((e) => e.countdown === 'D' || e.countdown === 'D-1'),
      };
      const rank = deskRank(rankInput);
      const name = bestAssetName(a, quote?.name ?? card?.name);
      const priceShown = strat.status === 'mock' ? null : (strat.price ?? card?.price);
      const changePct = strat.status === 'mock' ? null : (strat.changePct ?? card?.changePct);
      const decisionFirst = buildDecisionFirstView({
        symbol: sym, name, market: a.market, held,
        canonicalPrimaryAction: sda?.primaryAction ?? 'WAIT',
        canonicalDecisionId: sda?.decisionId ?? null,
        canonicalDecisionStatus: sda?.status ?? 'DATA_GATED',
        canonicalConfidenceBps: sda?.confidence.valueBps ?? 0,
        sevenSignStatus: sda?.sevenSign.status ?? 'DATA_GATED',
        sevenSignLevel: sda?.sevenSign.candidateLevel ?? null,
        targets: sda?.targets ?? [],
        invalidation: sda?.invalidation ?? null,
        freshness: sda?.freshness ?? 'UNKNOWN',
        priceText: fmtPrice(a.market, priceShown),
        changePct, pnlPct: pn?.pnlPct ?? null,
        priority: apx?.priorityRank && apx.priorityRank !== 'Ignore'
          ? apx.priorityRank : rank <= 0 ? 'P0' : rank <= 2 ? 'P1'
          : rank <= 5 ? 'P2' : 'WATCH',
        dataStatus: freshnessOf(strat, quote).text,
        asOf: quote?.quoteTruth?.sourceTimestamp ?? quote?.date ?? null,
        quoteTruth: quote?.quoteTruth ?? null,
        rank,
        whyCandidates: [
          decision?.reasonJa, incident?.moverCause?.bestLeadJa, incident?.reasonJa,
          card?.causeOneLineJa, strat.reasonJa,
        ],
        nextCandidates: [
          sda?.nextReviewConditionCodes?.[0],
          incident?.moverCause?.nextChecksJa?.[0], incident?.nextConditionJa,
          card?.nextJa, plBySym.get(sym)?.nextChecksJa?.[0],
          decision?.rule.nextConditionJa, strat.nextConditionJa,
        ],
        changeCandidates: [
          plBySym.get(sym)?.invalidationJa?.[0], apx?.whatWouldChangeJa,
          scBySym.get(sym)?.whatWouldChangeJa?.[0], strat.whatChangesJa,
        ],
      });
      const d: DeskCardData = {
        asset: a, genre, rank,
        card, decision, strat, quote,
        liveName: quote?.name ?? null,
        incident,
        pn,
        sdg: sdBySym.get(sym),
        apx,
        scn: scBySym.get(sym),
        ppl: plBySym.get(sym),
        aiLabel: aiBySym.get(sym),
        aiAgeMin: intel.aiMeta.ageMin,
        aiMeta: intel.aiMeta,
        eventTags,
        eventsAuthorityUnknown: intel.importantEventsUnknown,
        decisionFirst,
        themeConcentrationPct,
      };
      return { d, rankInput };
    });
  }, [assets, maps, intel.cardBySym, intel.decisionBySym, intel.sdaBySymbol, intel.aiJ.data, intel.sdSignals,
      intel.apItems, intel.scenarioSets, intel.positionPlans, intel.positionExposure,
      intel.aiMeta, eventTagsBySym, mountTs]);

  const riskCount = useMemo(() => rows.filter((r) => !!r.d.incident).length, [rows]);
  const keep = (r: { d: DeskCardData }) => filter === 'all' ? true
    : filter === 'risk' ? !!r.d.incident
    : filter === 'held' ? (r.d.asset.quantity ?? 0) > 0 || !!r.d.pn?.held
    : r.d.decisionFirst.bucket === filter;

  // 優先順(デフォルト・決定論): rank昇順→symbol昇順。
  const prioritized = useMemo(() =>
    rows.slice().sort((a, b) => a.d.rank - b.d.rank
      || (a.d.asset.symbol < b.d.asset.symbol ? -1 : a.d.asset.symbol > b.d.asset.symbol ? 1 : 0)),
    [rows]);
  // 区分内の順序は端末保存済みsortOrderを唯一の表示順にする。
  const manualGroups = useMemo(() => {
    const bySym = new Map(rows.map((r) => [r.d.asset.id, r]));
    return GENRES.map((g) => ({
      ...g,
      items: assets.filter((a) => genreOf(a) === g.key)
        .slice().sort((a, b) => a.sortOrder - b.sortOrder)
        .map((a) => bySym.get(a.id)!).filter(Boolean),
    })).filter((g) => g.items.length > 0);
  }, [rows, assets]);

  // ── Deep-link(Todayから): 展開+スクロール(即時+700ms settle再固定) ──
  const lastNonce = useRef<number>(0);
  const activeFocus = localFocus ?? focus;
  useEffect(() => {
    if (!activeFocus || activeFocus.nonce === lastNonce.current) return;
    const row = rows.find((r) =>
      r.d.asset.symbol.toUpperCase() === activeFocus.symbol.toUpperCase());
    if (!row) return;   // 未登録銘柄: 何もしない(捏造スクロールなし)
    lastNonce.current = activeFocus.nonce;
    setExpandedId(row.d.asset.id);
    const section = activeFocus.section
      && (DESK_SECTIONS as readonly string[]).includes(activeFocus.section)
      ? activeFocus.section as DeskSection : undefined;
    const scroll = () => {
      const el = document.getElementById(sectionAnchorId(activeFocus.symbol, section))
        ?? document.getElementById(sectionAnchorId(activeFocus.symbol));
      el?.scrollIntoView({ block: 'start' });
    };
    // 展開レンダー後に即時スクロール→遅延ロードで高さが変わるため700msで再固定
    window.setTimeout(scroll, 50);
    window.setTimeout(scroll, 750);
  }, [activeFocus, rows]);

  function onDragEnd(groupIds: string[]) {
    return (e: DragEndEvent) => {
      if (filter !== 'all') return;
      const { active, over } = e;
      if (!over || active.id === over.id) return;
      const from = groupIds.indexOf(String(active.id));
      const to = groupIds.indexOf(String(over.id));
      if (from < 0 || to < 0) return;
      onReorder(arrayMove(groupIds, from, to));
    };
  }

  if (assets.length === 0) {
    return <div className="card asset-list"><div className="asset-empty">資産がありません。「+ Add Asset」で追加できます。</div></div>;
  }

  const connecting = intel.jpQuotes.phase === 'connecting' && intel.usQuotes.phase === 'connecting';

  const renderCard = (r: { d: DeskCardData }, handle?: React.ReactNode) => (
    <AssetDecisionCard
      key={r.d.asset.id}
      d={r.d}
      open={detailSymbol
        ? r.d.asset.symbol.toUpperCase() === detailSymbol.toUpperCase()
        : expandedId === r.d.asset.id}
      onToggle={() => {
        if (detailSymbol) return;
        if (onOpenAsset && expandedId !== r.d.asset.id) {
          onOpenAsset(r.d.asset.symbol);
          return;
        }
        setLocalFocus(null);
        setExpandedId((cur) => (cur === r.d.asset.id ? null : r.d.asset.id));
      }}
      onRemove={(id) => { setExpandedId((cur) => (cur === id ? null : cur)); onRemove(id); }}
      onUpdateHolding={onUpdateHolding}
      nowMs={nowMs}
      dragHandle={handle}
      focusSection={activeFocus?.symbol.toUpperCase() === r.d.asset.symbol.toUpperCase()
        ? activeFocus.section : undefined}
      collapsible={!detailSymbol}
    />
  );

  if (detailSymbol) {
    const detail = prioritized.find((row) =>
      row.d.asset.symbol.toUpperCase() === detailSymbol.toUpperCase());
    return detail ? (
      <div className="asset-groups asset-groups--detail">
        <div className="card asset-list ad-list">{renderCard(detail)}</div>
      </div>
    ) : (
      <div className="card asset-list">
        <div className="asset-empty">{detailSymbol} はHoldings / Watchlistに登録されていません。</div>
      </div>
    );
  }

  return (
    <div className="asset-groups">
      <DownsideIncidentQueue
        data={intel.downside}
        maxItems={4}
        onFocus={(symbol) => {
          if (onOpenAsset) { onOpenAsset(symbol, 'why-downside'); return; }
          const row = rows.find((item) =>
            item.d.asset.symbol.toUpperCase() === symbol.toUpperCase());
          if (!row) return;
          setLocalFocus({ symbol, section: 'why-downside', nonce: Date.now() });
        }}
      />
      {toolbar}
      {connecting && <div className="asset-empty asset-empty--card">connecting… 最新の判断を取得中</div>}
      <div className="asset-filter">
        <button className={`asset-filter__chip${filter === 'all' ? ' is-active' : ''}`}
                aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>{t('wl.filterAll')}</button>
        <button className={`asset-filter__chip asset-filter__chip--risk${filter === 'risk' ? ' is-active' : ''}`}
                aria-pressed={filter === 'risk'} onClick={() => setFilter('risk')}>
          {t('wl.filterDanger')}{riskCount > 0 ? ` (${riskCount})` : ''}
        </button>
        <button className={`asset-filter__chip${filter === 'held' ? ' is-active' : ''}`}
                aria-pressed={filter === 'held'} onClick={() => setFilter('held')}>{t('wl.filterHeld')}</button>
        {filter !== 'all' && <span className="asset-filter__note">全件表示で並べ替えできます</span>}
      </div>

      {manualGroups.map((g) => {
        const shown = g.items.filter(keep);
        if (shown.length === 0) return null;
        const ids = g.items.map((r) => r.d.asset.id);
        return (
          <section className="asset-group" key={g.key}>
            <div className="asset-group__title">{g.title}<span className="asset-group__count">{shown.length}</span><span className="asset-group__hint">長押しで並べ替え・自動保存</span></div>
            <div className="card asset-list ad-list">
              <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd(ids)}>
                <SortableContext items={ids} strategy={verticalListSortingStrategy}>
                  {shown.map((r) => (
                    <SortableCardRow key={r.d.asset.id} id={r.d.asset.id}>
                      {(handle) => renderCard(r, filter === 'all' ? handle : undefined)}
                    </SortableCardRow>
                  ))}
                </SortableContext>
              </DndContext>
            </div>
          </section>
        );
      })}
    </div>
  );
};
