export type MarketInstrumentSymbol = '1321' | '1306' | 'SPY' | 'QQQ';
export type MarketInstrumentMarket = 'JP' | 'US';
export type MarketHorizon = 1 | 5 | 20;

export interface MarketInstrumentDefinition {
  symbol: MarketInstrumentSymbol;
  market: MarketInstrumentMarket;
  shortLabel: string;
  fullLabel: string;
  instrumentType: 'ETF';
  underlying: 'Nikkei 225' | 'TOPIX' | 'S&P 500' | 'Nasdaq-100';
  verifiedDaily: true;
}

export const MARKET_INSTRUMENTS: readonly MarketInstrumentDefinition[] = [
  { symbol: '1321', market: 'JP', shortLabel: '1321 日経225 ETF',
    fullLabel: '1321 日経225 ETF', instrumentType: 'ETF',
    underlying: 'Nikkei 225', verifiedDaily: true },
  { symbol: '1306', market: 'JP', shortLabel: '1306 TOPIX ETF',
    fullLabel: '1306 TOPIX ETF', instrumentType: 'ETF',
    underlying: 'TOPIX', verifiedDaily: true },
  { symbol: 'SPY', market: 'US', shortLabel: 'SPY S&P 500 ETF',
    fullLabel: 'SPY S&P 500 ETF', instrumentType: 'ETF',
    underlying: 'S&P 500', verifiedDaily: true },
  { symbol: 'QQQ', market: 'US', shortLabel: 'QQQ Nasdaq 100 ETF',
    fullLabel: 'QQQ Nasdaq 100 ETF', instrumentType: 'ETF',
    underlying: 'Nasdaq-100', verifiedDaily: true },
] as const;

// v13.5.54 (owner 2026-09-04: 「日経平均などの指数がトップに表示されていない、まだ
// ETF」). SHO reasons about the Nikkei 225, not the 1321 ETF that tracks it, so
// the Today headline shows the INDEX. The verified ETF snapshot stays the
// decision anchor — an index has no licensed intraday quote here — and the
// panel says so; only the displayed series and its projection move.
export type IndexChartKey = 'N225' | 'TOPIX' | 'SPX' | 'NDX';
export const INDEX_FOR_INSTRUMENT: Record<MarketInstrumentSymbol, IndexChartKey> = {
  '1321': 'N225', '1306': 'TOPIX', SPY: 'SPX', QQQ: 'NDX',
};
export const INDEX_DISPLAY_JA: Record<IndexChartKey, string> = {
  N225: '日経平均株価', TOPIX: 'TOPIX', SPX: 'S&P 500', NDX: 'ナスダック総合',
};

export const MARKET_HORIZONS: readonly MarketHorizon[] = [1, 5, 20];
export const DEFAULT_MARKET_INSTRUMENT: Record<MarketInstrumentMarket,
  MarketInstrumentSymbol> = { JP: '1321', US: 'SPY' };

export function marketInstrument(symbol: string | null | undefined) {
  const normalized = String(symbol ?? '').toUpperCase();
  return MARKET_INSTRUMENTS.find((item) => item.symbol === normalized) ?? null;
}

export function isVerifiedMarketInstrument(
  symbol: string | null | undefined,
  timeframe: 'daily' | 'weekly' = 'daily',
) {
  return timeframe === 'daily' && marketInstrument(symbol)?.verifiedDaily === true;
}

export function normalizeMarketInstrument(
  market: MarketInstrumentMarket,
  value: string | null | undefined,
): MarketInstrumentSymbol {
  const match = marketInstrument(value);
  return match?.market === market ? match.symbol : DEFAULT_MARKET_INSTRUMENT[market];
}
