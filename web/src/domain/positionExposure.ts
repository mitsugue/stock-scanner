// V11.8.0 Position / Exposure Engine — device-local TypeScript port of
// argus_position_exposure.py. PRIVACY BY DESIGN: quantities/average costs live
// ONLY in localStorage (AssetItem.quantity/avgCost) and every computation here
// runs in the browser. Nothing is uploaded; the backend only ever sees the
// public watchlist. This is RISK REVIEW, never a trade instruction.

import type { AssetItem } from '../types/assetItem';
import { buildExposure, type ExposureSummary } from '../lib/portfolio';

export type ThemeKey =
  | 'ai_infrastructure' | 'physical_ai_robotics' | 'semiconductor_photonics'
  | 'defense_heavy_industry' | 'telecom_platform' | 'trading_commodity'
  | 'gold' | 'crypto' | 'index_core' | 'other';

export const THEME_JA: Record<ThemeKey, string> = {
  ai_infrastructure: 'AIインフラ', physical_ai_robotics: 'フィジカルAI/ロボット',
  semiconductor_photonics: '半導体/光技術', defense_heavy_industry: '防衛/重工',
  telecom_platform: '通信/プラットフォーム', trading_commodity: '商社/資源',
  gold: '金', crypto: '暗号資産', index_core: 'インデックス積立', other: 'その他',
};

// Mirror of the backend THEME_MAP (public tickers only). Unknown → other.
const THEME_MAP: Record<string, ThemeKey> = {
  NVDA: 'ai_infrastructure', AVGO: 'ai_infrastructure', TSM: 'ai_infrastructure',
  SMH: 'ai_infrastructure', MSFT: 'ai_infrastructure', AMZN: 'ai_infrastructure',
  GOOGL: 'ai_infrastructure', GOOG: 'ai_infrastructure', SMCI: 'ai_infrastructure',
  '9984': 'ai_infrastructure', '5803': 'ai_infrastructure', '5801': 'ai_infrastructure',
  '6920': 'ai_infrastructure', IONQ: 'ai_infrastructure',
  TSLA: 'physical_ai_robotics', '6954': 'physical_ai_robotics',
  '6506': 'physical_ai_robotics', '6584': 'physical_ai_robotics',
  '285A': 'semiconductor_photonics', '6965': 'semiconductor_photonics',
  '6857': 'semiconductor_photonics', '6146': 'semiconductor_photonics',
  '8035': 'semiconductor_photonics', AMD: 'semiconductor_photonics', MU: 'semiconductor_photonics',
  '7011': 'defense_heavy_industry', '7012': 'defense_heavy_industry', '7013': 'defense_heavy_industry',
  AAPL: 'telecom_platform', META: 'telecom_platform',
  '9432': 'telecom_platform', '9433': 'telecom_platform', '9434': 'telecom_platform',
  '8058': 'trading_commodity', '8001': 'trading_commodity', '8031': 'trading_commodity',
  '314A': 'gold', '1540': 'gold', GLD: 'gold',
};
const GOLD_WORDS = ['ゴールド', 'gold', '金価格', '純金'];
const INDEX_WORDS = ['s&p', 'sp500', 'オルカン', '全世界', 'all country', 'インデックス',
  'index', 'topix', '日経225', 'nasdaq100'];

export function themeOf(a: Pick<AssetItem, 'symbol' | 'market' | 'assetType' | 'displayName' | 'displayNameJa'>): ThemeKey {
  const sym = a.symbol.toUpperCase();
  if (THEME_MAP[sym]) return THEME_MAP[sym];
  if (a.market === 'CRYPTO' || a.assetType === 'crypto') return 'crypto';
  const low = `${a.displayNameJa ?? ''} ${a.displayName ?? ''}`.toLowerCase();
  if (GOLD_WORDS.some((w) => low.includes(w))) return 'gold';
  if (a.assetType === 'core_fund' || a.assetType === 'manual_fund'
    || INDEX_WORDS.some((w) => low.includes(w))) return 'index_core';
  return 'other';
}

// Explicit thresholds — keep in sync with argus_position_exposure.DEFAULT_THRESHOLDS.
export const THRESHOLDS = {
  singleNameMedium: 0.15, singleNameHigh: 0.25, singleNameCritical: 0.40,
  themeMedium: 0.25, themeHigh: 0.40,
};

export type Readiness = 'add_allowed_small' | 'add_only_on_pullback' | 'wait'
  | 'avoid_chase' | 'monitor' | 'no_action' | 'unknown';
export const READINESS_JA: Record<Readiness, string> = {
  add_allowed_small: '小さく買い増し可', add_only_on_pullback: '買い増しは押し目限定',
  wait: '見送り', avoid_chase: '追いかけ買い注意', monitor: '監視継続',
  no_action: '対応不要', unknown: '判定保留(データ未入力)',
};
export const READINESS_TONE: Record<Readiness, string> = {
  add_allowed_small: 'var(--value-positive)', add_only_on_pullback: 'var(--amber, #fbbf24)',
  wait: 'var(--amber, #fbbf24)', avoid_chase: 'var(--value-negative)',
  monitor: 'var(--text-muted)', no_action: 'var(--text-faint)', unknown: 'var(--text-faint)',
};

export interface PositionRisk {
  symbol: string;
  riskLevel: 'low' | 'medium' | 'high' | 'critical' | 'unknown';
  riskType: string;
  whyJa: string;
  checkNextJa: string;
}

export interface PositionNote {
  symbol: string;
  /** 表示名(日本株は数字+社名並記ルール用) */
  name: string;
  held: boolean;
  quantity?: number;
  avgCost?: number;
  pnlPct?: number | null;
  weightPct?: number | null;    // % of valued portfolio (JPY terms)
  themeJa: string;
  readiness: Readiness;
  readinessJa: string;
  whyJa: string;
}

export interface PortfolioExposure {
  base: ExposureSummary;
  byTheme: { key: ThemeKey; ja: string; pct: number; valueJpy: number }[];
  aiThemePct: number | null;
  goldPct: number | null;
  cryptoPct: number | null;
  jpyPct: number | null;
  usdPct: number | null;
  top1Pct: number | null;
  top1Symbol: string | null;
  singleNameRisk: 'low' | 'medium' | 'high' | 'critical' | null;
  themeRisk: 'low' | 'medium' | 'high' | null;
  risks: PositionRisk[];
  notes: Record<string, PositionNote>;   // by SYMBOL (upper) — held + watch-only
  regimeSummaryJa: string;
  headwinds: string[];
  tailwinds: string[];
  noHoldings: boolean;
  watchOnlyCount: number;
  unpriced: string[];
  provisionalNoteJa: string | null;
}

export interface ExposureCtx {
  regimeLabel?: string | null;
  riskOff?: boolean;
  /** symbol(upper) → flowClass from /flow-attribution */
  flowBySymbol?: Record<string, string>;
  /** symbols(upper) with imminent/today events */
  eventSymbols?: Set<string>;
  /** v11.10.0: symbol(upper) → 需給ランク (S/A/B/C/D/E/Unknown) */
  sdRankBySymbol?: Record<string, string>;
}

export function buildPositionExposure(
  assets: AssetItem[],
  priceOf: (a: AssetItem) => number | undefined,
  usdJpy: number | null,
  ctx: ExposureCtx = {},
): PortfolioExposure {
  const base = buildExposure(assets, priceOf, usdJpy);
  const total = base.combinedJpy;
  const toJpy = (ccy: 'JPY' | 'USD', v: number): number | null =>
    ccy === 'JPY' ? v : usdJpy != null ? v * usdJpy : null;

  const themeBySym = new Map(assets.map((a) => [a.symbol.toUpperCase(), themeOf(a)]));
  const nameBySym = new Map(assets.map((a) => [a.symbol.toUpperCase(), a.displayNameJa || a.displayName]));
  const disp = (sym: string) => (/^\d/.test(sym) && nameBySym.get(sym) ? `${sym} ${String(nameBySym.get(sym)).slice(0, 8)}` : sym);
  const weightBySym = new Map<string, number>();
  const themeAgg = new Map<ThemeKey, number>();
  if (total != null && total > 0) {
    for (const h of base.holdings) {
      const j = toJpy(h.currency, h.value);
      if (j == null) continue;
      const sym = h.symbol.toUpperCase();
      weightBySym.set(sym, j / total);
      const t = themeBySym.get(sym) ?? 'other';
      themeAgg.set(t, (themeAgg.get(t) ?? 0) + j);
    }
  }
  const byTheme = [...themeAgg.entries()]
    .map(([key, v]) => ({ key, ja: THEME_JA[key], pct: total ? (v / total) * 100 : 0, valueJpy: v }))
    .sort((a, b) => b.pct - a.pct);
  const themePct = (k: ThemeKey) => byTheme.find((t) => t.key === k)?.pct ?? 0;
  const aiThemePct = total
    ? themePct('ai_infrastructure') + themePct('physical_ai_robotics') + themePct('semiconductor_photonics')
    : null;
  const jpy = base.totals.JPY.value, usd0 = base.totals.USD.value;
  const usdJ = usdJpy != null ? usd0 * usdJpy : null;
  const jpyPct = total && usdJ != null ? (jpy / total) * 100 : total && usd0 === 0 ? 100 : null;
  const usdPct = total && usdJ != null ? (usdJ / total) * 100 : null;

  const ranked = [...weightBySym.entries()].sort((a, b) => b[1] - a[1]);
  const top1 = ranked[0] ?? null;
  const top1Pct = top1 ? top1[1] * 100 : null;
  const singleNameRisk = top1Pct == null ? null
    : top1Pct >= THRESHOLDS.singleNameCritical * 100 ? 'critical'
    : top1Pct >= THRESHOLDS.singleNameHigh * 100 ? 'high'
    : top1Pct >= THRESHOLDS.singleNameMedium * 100 ? 'medium' : 'low';
  const maxTheme = byTheme[0]?.pct ?? null;
  const themeRisk = maxTheme == null ? null
    : maxTheme >= THRESHOLDS.themeHigh * 100 ? 'high'
    : maxTheme >= THRESHOLDS.themeMedium * 100 ? 'medium' : 'low';

  const flowBy = ctx.flowBySymbol ?? {};
  const events = ctx.eventSymbols ?? new Set<string>();
  const holdingsBySym = new Map(base.holdings.map((h) => [h.symbol.toUpperCase(), h]));

  // ── held-position risks (watch-only entries never create held risk) ──
  const risks: PositionRisk[] = [];
  for (const [sym, w] of ranked) {
    if (w >= THRESHOLDS.singleNameMedium) {
      const lvl = w >= THRESHOLDS.singleNameCritical ? 'critical'
        : w >= THRESHOLDS.singleNameHigh ? 'high' : 'medium';
      risks.push({
        symbol: sym, riskLevel: lvl, riskType: 'concentration',
        whyJa: `${disp(sym)}が保有全体の約${Math.round(w * 100)}%。この1銘柄の値動きで資産全体が大きく揺れる比率です。`,
        checkNextJa: '買い増しより分散を優先するか、比率の上限を決めることを検討',
      });
    }
  }
  for (const t of byTheme) {
    if (t.pct >= THRESHOLDS.themeHigh * 100 && t.key !== 'index_core') {
      risks.push({
        symbol: t.key.toUpperCase(), riskLevel: 'high', riskType: 'theme_overcrowding',
        whyJa: `${t.ja}テーマ合計が約${Math.round(t.pct)}%と高くなっています。追加購入は押し目確認後に限定した方が安全です。`,
        checkNextJa: 'テーマ全体が同時に下がった場合の想定下落幅を確認',
      });
    }
  }
  for (const h of base.holdings) {
    const sym = h.symbol.toUpperCase();
    if (h.plPct <= -15) {
      risks.push({
        symbol: sym, riskLevel: h.plPct <= -25 ? 'high' : 'medium', riskType: 'drawdown',
        whyJa: `${disp(sym)}は取得単価から約${Math.abs(Math.round(h.plPct))}%の含み損。ナンピンの前に下落原因の確認が先です。`,
        checkNextJa: '銘柄カードの「原因の詳細」とイベント予定を確認',
      });
    }
    const fc = flowBy[sym];
    if (fc === 'panic_selling' || fc === 'distribution' || fc === 'profit_taking') {
      risks.push({
        symbol: sym, riskLevel: fc === 'panic_selling' ? 'high' : 'medium', riskType: 'held_flow_risk',
        whyJa: `保有中の${disp(sym)}に売り圧力の推定が出ています。監視銘柄なら様子見で済みますが、保有中のため優先確認対象です。`,
        checkNextJa: '翌営業日に戻りが売られるか、公式材料が出るかを確認',
      });
    }
    const sdRank = (ctx.sdRankBySymbol ?? {})[sym];
    if (sdRank === 'D' || sdRank === 'E') {
      risks.push({
        symbol: sym, riskLevel: sdRank === 'E' ? 'high' : 'medium', riskType: 'supply_demand',
        whyJa: `保有中の${disp(sym)}は需給ランク${sdRank}(${sdRank === 'E' ? '需給が悪く追いかけ買い回避' : '信用買い残が重い/戻り売りが出やすい'})。監視銘柄より優先確認対象です。`,
        checkNextJa: '週次信用残・日証金の次回更新と、戻り局面で売りが出るかを確認',
      });
    }
    if (events.has(sym)) {
      risks.push({
        symbol: sym, riskLevel: 'medium', riskType: 'event_risk',
        whyJa: `保有中の${disp(sym)}に直近の重要イベントがあります。通過までポジションを増やさないのが基本です。`,
        checkNextJa: 'イベント結果と初動反応を確認',
      });
    }
  }
  const sev = { critical: 0, high: 1, medium: 2, low: 3, unknown: 4 } as const;
  risks.sort((a, b) => sev[a.riskLevel] - sev[b.riskLevel]);

  // ── per-asset note + add-more readiness ──
  const notes: Record<string, PositionNote> = {};
  for (const a of assets) {
    const sym = a.symbol.toUpperCase();
    const h = holdingsBySym.get(sym);
    const held = (a.quantity ?? 0) > 0;
    const w = weightBySym.get(sym) ?? null;
    const theme = themeBySym.get(sym) ?? 'other';
    const tPct = themePct(theme);
    const fc = flowBy[sym];
    let readiness: Readiness; let whyJa: string;
    if (!held) {
      readiness = 'monitor';
      whyJa = '監視銘柄です(保有なし)。保有数量を入力するとポジションリスクも判定します。';
    } else if (h == null) {
      readiness = 'unknown';
      whyJa = a.avgCost == null
        ? '保有数量はありますが取得単価が未入力のため、リスク判定は暫定です。'
        : '価格が取得できないため、リスク判定は暫定です。';
    } else if (fc === 'retail_chase') {
      readiness = 'avoid_chase';
      whyJa = '個人の追随買いの型が出ており、ここからの買い増しは高値掴みリスクがあります。この上昇を追うより、保有比率とテーマ集中を先に確認すべきです。';
    } else if (events.has(sym)) {
      readiness = 'wait';
      whyJa = '重要イベント直前のため、結果を見てから判断するのが安全です。';
    } else if (w != null && w >= THRESHOLDS.singleNameHigh) {
      readiness = 'wait';
      whyJa = `既にこの1銘柄で約${Math.round(w * 100)}%と大きいため、買い増しより分散が先です。`;
    } else if (tPct >= THRESHOLDS.themeHigh * 100 && theme !== 'index_core') {
      readiness = 'add_only_on_pullback';
      whyJa = `${THEME_JA[theme]}テーマの比率が高いため、買い増すなら小さく、押し目確認後に限定した方が安全です。`;
    } else if (ctx.riskOff && (theme === 'ai_infrastructure' || theme === 'physical_ai_robotics'
      || theme === 'semiconductor_photonics' || theme === 'crypto')) {
      readiness = 'add_only_on_pullback';
      whyJa = 'リスクオフ寄りの地合いでは高グロース/高ベータの買い増しは押し目限定が安全です。';
    } else if (fc === 'panic_selling' || fc === 'distribution') {
      readiness = 'wait';
      whyJa = '売り圧力の推定が出ているため、落ち着くまで見送りが安全です。';
    } else if ((ctx.sdRankBySymbol ?? {})[sym] === 'E') {
      readiness = 'wait';
      whyJa = '需給ランクE(需給が悪い)のため、買い増しは需給リセットの確認まで見送りが安全です。';
    } else if ((ctx.sdRankBySymbol ?? {})[sym] === 'D') {
      readiness = 'add_only_on_pullback';
      whyJa = '需給ランクD(信用買い残が重い)。買い増すなら戻り売りをこなした押し目限定が安全です。';
    } else {
      readiness = 'add_allowed_small';
      whyJa = '明確なブロック要因はありません。ただし一度に大きく買わず、小さく分けるのが基本です。';
    }
    notes[sym] = {
      symbol: sym, name: a.displayNameJa || a.displayName, held,
      quantity: held ? a.quantity : undefined,
      avgCost: held ? a.avgCost : undefined,
      pnlPct: h ? h.plPct : null,
      weightPct: w != null ? w * 100 : null,
      themeJa: THEME_JA[theme],
      readiness, readinessJa: READINESS_JA[readiness], whyJa,
    };
  }

  // ── regime sensitivity ──
  const headwinds: string[] = []; const tailwinds: string[] = []; const lines: string[] = [];
  const goldPct = total ? themePct('gold') : null;
  const cryptoPct = total ? themePct('crypto') : null;
  if (aiThemePct == null) {
    lines.push('保有データ未入力のため、レジーム感応度はウォッチリスト構成からの参考値です。');
  } else {
    if (aiThemePct >= 40) {
      headwinds.push('金利上昇・AI設備投資懸念(グロース/AI比率が高い)');
      lines.push(`AI関連テーマが約${Math.round(aiThemePct)}%と高く、金利上昇やAI投資減速の局面では逆風を受けやすい構成です。`);
    }
    if (ctx.riskOff && (aiThemePct + (cryptoPct ?? 0)) >= 40) {
      headwinds.push('リスクオフ地合い(高ベータ比率が高い)');
      lines.push('今日の地合いはリスク回避寄りのため、高ベータ中心の構成には向かい風です。');
    }
    if ((goldPct ?? 0) >= 5) {
      tailwinds.push('金の保有が下落時のクッションになる');
      lines.push(`金を約${Math.round(goldPct!)}%持っており、急落時の下支えになります。`);
    }
    if (usdPct != null && usdPct >= 50) headwinds.push('円高局面では米ドル資産の円換算が目減り');
  }

  // 未入力(数量なし) と 入力済みだが価格未取得 は別物 — 混同すると
  // 「入力したのに未入力と言われる」というオーナー体験になる。
  const hasQuantities = assets.some((a) => (a.quantity ?? 0) > 0);
  const noHoldings = !hasQuantities;
  const pricedNone = hasQuantities && base.holdings.length === 0;
  return {
    base, byTheme, aiThemePct, goldPct, cryptoPct, jpyPct, usdPct,
    top1Pct, top1Symbol: top1 ? top1[0] : null, singleNameRisk, themeRisk,
    risks: risks.slice(0, 6), notes,
    regimeSummaryJa: lines.join(' ') || '現在の構成に対する明確なレジーム逆風は検出されていません。',
    headwinds, tailwinds,
    noHoldings,
    watchOnlyCount: assets.filter((a) => (a.quantity ?? 0) <= 0).length,
    unpriced: base.unpriced,
    provisionalNoteJa: noHoldings
      ? 'ポジション数量・取得単価が未入力のため、保有リスクは暫定です(テーマはウォッチリスト構成)。'
      : pricedNone
        ? '保有は入力済みですが価格が未取得のため、比率・集中度の判定を一時保留しています(価格取得後に自動計算)。'
        : base.unpriced.length > 0
          ? `価格未取得の銘柄があるため一部暫定: ${base.unpriced.slice(0, 4).join(' / ')}`
          : null,
  };
}

/** Pro Handoff / AI Review Sheet 用の実保有サマリ(端末内で生成しクリップボードにのみ入る)。 */
export function exposureSummaryText(pe: PortfolioExposure): string {
  if (pe.noHoldings) {
    return ['## Position / Exposure Summary (device-local)',
      '実保有: 未入力(ウォッチリストのみ)。保有リスクの断定は不可。'].join('\n');
  }
  const L: string[] = ['## Position / Exposure Summary (device-local)'];
  L.push(`テーマ配分: ${pe.byTheme.slice(0, 5).map((t) => `${t.ja} ${t.pct.toFixed(0)}%`).join(' / ')}`);
  if (pe.jpyPct != null && pe.usdPct != null) L.push(`通貨: JPY ${pe.jpyPct.toFixed(0)}% / USD ${pe.usdPct.toFixed(0)}%`);
  if (pe.top1Symbol && pe.top1Pct != null) L.push(`最大集中: ${pe.top1Symbol} ${pe.top1Pct.toFixed(0)}% (${pe.singleNameRisk})`);
  for (const r of pe.risks.slice(0, 3)) L.push(`リスク: [${r.riskLevel}] ${r.whyJa}`);
  const pullback = Object.values(pe.notes).filter((n) => n.readiness === 'add_only_on_pullback').map((n) => n.symbol);
  if (pullback.length) L.push(`買い増しは押し目限定: ${pullback.join(' / ')}`);
  if (pe.provisionalNoteJa) L.push(`注意: ${pe.provisionalNoteJa}`);
  L.push('反対解釈: 集中はリターン源泉でもある。比率調整は目的(FIRE計画)と相談して判断する。');
  L.push('注意: 金額・数量はこの端末内で計算されクリップボード経由でのみ共有される。売買指示ではない。');
  return L.join('\n');
}

/**
 * v13.5.62 (GPT review item 7: 「同一銘柄の複数リスクでは重大なものを保持」). The
 * risk list is sorted most-severe first; a plain Map over it kept the LAST
 * entry, i.e. the mildest. This keeps the first.
 */
export function mostSevereRiskBySymbol(risks: readonly PositionRisk[]): Map<string, PositionRisk['riskLevel']> {
  const out = new Map<string, PositionRisk['riskLevel']>();
  for (const risk of risks) if (!out.has(risk.symbol)) out.set(risk.symbol, risk.riskLevel);
  return out;
}
