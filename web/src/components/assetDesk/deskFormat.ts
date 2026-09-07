// V12.2.12 Asset Desk — 表示語彙ヘルパー(旧UnifiedAssetCard/AssetStrategySectionから移設)。
import type { AssetStrategy } from '../../lib/assetStrategy';
import type { QuoteLite } from '../../lib/assetStrategy';
import type { DeskEventTag } from './types';

// LINKED EVENT tag — Japanese (event code + proximity + impact, 読める日本語).
const EVENT_CODE_JA: Record<string, string> = {
  BOJ: '日銀', FOMC: 'FOMC', PCE: '米PCE', CPI: '米CPI', PPI: '米PPI', NFP: '米雇用統計',
  JOLTS: 'JOLTS', GDP: 'GDP', AUCTION: '国債入札', EARNINGS: '決算', BOE: '英中銀', ECB: '欧中銀',
};
const ESC_JA: Record<string, string> = { 'D-7': '7日前', 'D-3': '3日前', 'D-1': '前日', D: '当日', 'D+1': '翌日' };
const IMPACT_JA: Record<string, string> = { CRITICAL: '影響:重大', HIGH: '影響:大', MEDIUM: '影響:中', LOW: '影響:小' };
export const linkedTagJa = (le: DeskEventTag) =>
  [EVENT_CODE_JA[le.code] ?? le.code, ESC_JA[le.countdown], IMPACT_JA[le.impact] ?? le.impact]
    .filter(Boolean).join(' · ');

export function fmtPrice(market: string, v?: number | null): string {
  if (v == null) return '—';
  if (market === 'JP' || market === 'CORE' || market === 'FUND') return `¥${Math.round(v).toLocaleString('en-US')}`;
  if (market === 'US') return `$${v.toFixed(2)}`;
  if (market === 'CRYPTO') {
    return v >= 1000 ? `$${Math.round(v).toLocaleString('en-US')}` : `$${v.toFixed(2)}`;
  }
  return String(v);
}

export function fmtAgeMin(ts: number, nowMs: number): string {
  const m = Math.max(0, Math.round((nowMs - ts) / 60000));
  return m < 1 ? 'just now' : `${m}m ago`;
}

const STATUS_COLOR: Record<string, string> = {
  live: 'var(--green)', partial: 'var(--amber)', mock: 'var(--amber)', manual: 'var(--text-muted)',
};

// ── Data-freshness honesty(旧AssetStrategySectionから移設) ──
// J-Quants free plan lags ~12 weeks: a quote can be "live" (really fetched)
// yet months old. Surface that as an amber "delayed Xw" instead of a green
// "live" — an investment app must never dress stale data as fresh.
function lagDays(date?: string | null): number | null {
  if (!date) return null;
  const t = Date.parse(`${date}T00:00:00+09:00`);
  if (!Number.isFinite(t)) return null;
  return Math.max(0, Math.floor((Date.now() - t) / 86_400_000));
}

export function freshnessOf(strat: AssetStrategy, quote?: QuoteLite): { text: string; color: string } {
  if (quote?.quoteTruth) {
    const delay = quote.quoteTruth.delayClass;
    const color = delay === 'LIVE' ? 'var(--green)'
      : delay === 'OFFLINE' || delay === 'UNKNOWN' ? 'var(--amber)'
        : 'var(--text-muted)';
    // v13.5.63 (GPT review item 3): one vocabulary for price kind everywhere
    // (リアルタイム / n分遅延 / 終値 / 取得時刻不明), the same words the
    // freshness line uses.
    const text = delay === 'LIVE' ? 'リアルタイム'
      : delay === '15m' || delay === '20m' ? `${delay.replace('m', '分')}遅延`
        : delay === 'EOD' ? '終値'
          : delay === 'OFFLINE' ? 'オフライン' : '取得時刻不明';
    return { text, color };
  }
  if (strat.status === 'manual') return { text: 'manual', color: STATUS_COLOR.manual };
  // v13.5.40: the owner never sees the word "mock" — an absent quote is 未取得.
  if (strat.status === 'mock')   return { text: '未取得', color: STATUS_COLOR.mock };
  const lag = lagDays(strat.date);
  if (lag != null && lag > 7) {
    const text = lag >= 14 ? `delayed ${Math.round(lag / 7)}w` : `delayed ${lag}d`;
    return { text, color: 'var(--amber)' };
  }
  return { text: strat.status, color: STATUS_COLOR[strat.status] ?? 'var(--text-muted)' };
}
