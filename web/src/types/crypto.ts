// Mirrors the backend /api/argus/crypto-watchlist shape (CoinGecko, keyless).
// Quotes are keyed by CoinGecko id (e.g. "bitcoin") — the asset's memo stores
// the mapping as "coingecko:<id>". changePct is the 24h change.

export type CryptoQuoteStatus = 'live' | 'delayed' | 'partial'
  | 'unavailable' | 'mock';

export interface CryptoQuote {
  id: string;            // coingecko id
  priceUsd: number;
  changePct: number;     // 24h %
  volume: number;        // 24h USD volume
  date: string | null;   // YYYY-MM-DD (last update)
  status: CryptoQuoteStatus;
  source?: 'coingecko' | 'coinbase' | string;
  sourceTimestamp?: string | null;
  receivedAt?: string | null;
  /** Age of `sourceTimestamp` as the backend measured it at send time. */
  ageSec?: number | null;
  /** Backend's declared delay class — a claim the client still verifies. */
  delayClass?: string | null;
  realtimeEvidence?: boolean;
  sourceTimeStatus?: 'PRESENT' | 'MISSING' | 'FUTURE' | 'MALFORMED' | string;
  freshness?: 'fresh' | 'delayed' | string;
  decisionUsable?: boolean;
}

export interface CryptoWatchlistSnapshot {
  status: 'live' | 'delayed' | 'partial' | 'mock';
  asOf: string | null;
  provider: 'coingecko' | 'coinbase' | string;
  quotes: CryptoQuote[];
}
