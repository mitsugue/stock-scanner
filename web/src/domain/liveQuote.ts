import { exactAuthorityEpoch } from './liveAuthority';

export type InstrumentType = 'INDEX' | 'ETF' | 'STOCK' | 'FUND' | 'CRYPTO';
export type DelayClass = 'LIVE' | '15m' | '20m' | 'EOD' | 'T-1' | 'UNKNOWN' | 'OFFLINE';
export type QuoteSession = 'PRE' | 'REGULAR' | 'AFTER' | 'CLOSED' | 'UNKNOWN';

export interface LiveQuote {
  symbol: string;
  instrumentType: InstrumentType;
  provider: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
  sourceTimestamp: string | null;
  receivedAt: string | null;
  ageSec: number | null;
  transportAgeSec: number | null;
  delayClass: DelayClass;
  session: QuoteSession;
  entitlement: string;
}

export interface RawQuoteTruth {
  symbol?: string;
  price?: number | null;
  changeAbs?: number | null;
  changePct?: number | null;
  status?: string | null;
  date?: string | null;
  provider?: string | null;
  source?: string | null;
  sourceTimestamp?: string | number | null;
  exchangeTs?: string | number | null;
  updateTime?: string | number | null;
  receivedAt?: string | null;
  ageSec?: number | null;
  transportAgeSec?: number | null;
  delayClass?: string | null;
  session?: string | null;
  entitlement?: string | null;
  realtimeEvidence?: boolean | null;
}

/** Minimal daily NAV truth accepted by valuation consumers. */
export interface DailyFundNavTruth {
  navYen?: unknown;
  date?: unknown;
  status?: unknown;
}

const DELAY_CLASSES = new Set<DelayClass>([
  'LIVE', '15m', '20m', 'EOD', 'T-1', 'UNKNOWN', 'OFFLINE',
]);
const QUOTE_SESSIONS = new Set<QuoteSession>([
  'PRE', 'REGULAR', 'AFTER', 'CLOSED', 'UNKNOWN',
]);

function finite(value: unknown): number | null {
  if (value == null || value === '' || typeof value === 'boolean') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function exactCalendarDate(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const [, year, month, day] = match;
  const epoch = Date.UTC(Number(year), Number(month) - 1, Number(day));
  const exact = new Date(epoch);
  return exact.getUTCFullYear() === Number(year)
    && exact.getUTCMonth() === Number(month) - 1
    && exact.getUTCDate() === Number(day) ? value : null;
}

function isoTimestamp(value: string | number | null | undefined): string | null {
  if (value == null || value === '') return null;
  if (typeof value === 'string') {
    const date = exactCalendarDate(value);
    if (date) return date;
    const epoch = exactAuthorityEpoch(value);
    return epoch == null ? null : new Date(epoch).toISOString();
  }
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  const raw = new Date(value < 10_000_000_000 ? value * 1000 : value);
  return Number.isNaN(raw.getTime()) ? null : raw.toISOString();
}

function exactAgeSec(timestamp: string | null, nowMs: number): number | null {
  if (!timestamp || /^\d{4}-\d{2}-\d{2}$/.test(timestamp)) return null;
  const ms = exactAuthorityEpoch(timestamp);
  if (ms == null || ms > nowMs) return null;
  // Ceil preserves exact fail-closed TTL boundaries: source+60,000ms is age
  // 60s, while source+60,001ms is already 61s (never rounded back to LIVE).
  return Math.ceil((nowMs - ms) / 1000);
}

function tokyoTradingDate(nowMs: number): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Tokyo',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(nowMs));
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${byType.year}-${byType.month}-${byType.day}`;
}

function calendarAgeDays(sourceDate: string | null, nowMs: number): number | null {
  const exact = exactCalendarDate(sourceDate);
  const today = exactCalendarDate(tokyoTradingDate(nowMs));
  if (!exact || !today) return null;
  const age = (Date.parse(`${today}T00:00:00Z`) - Date.parse(`${exact}T00:00:00Z`))
    / 86_400_000;
  return Number.isInteger(age) && age >= 0 ? age : null;
}

export function calendarDateExpiresAt(sourceDate: unknown, maxAgeDays: number): number | null {
  const exact = exactCalendarDate(sourceDate);
  if (!exact || !Number.isInteger(maxAgeDays) || maxAgeDays < 0) return null;
  // The evidence is JP-daily, so the next disallowed calendar date begins at
  // midnight JST. This is only a freshness deadline, not an exchange calendar.
  return Date.parse(`${exact}T00:00:00+09:00`) + (maxAgeDays + 1) * 86_400_000;
}

/**
 * Official fund NAV is daily delayed evidence.  A `live` transport/status label
 * is not a stronger market-time proof and is rejected so it cannot silently
 * upgrade the canonical daily contract.
 */
export function dailyFundNavDecisionUsable(
  fund: DailyFundNavTruth,
  nowMs = Date.now(),
  maxAgeDays = 7,
): boolean {
  const date = exactCalendarDate(fund.date);
  const sourceStart = date ? Date.parse(`${date}T00:00:00+09:00`) : NaN;
  const expiresAt = calendarDateExpiresAt(date, maxAgeDays);
  return fund.status === 'delayed'
    && typeof fund.navYen === 'number' && Number.isFinite(fund.navYen) && fund.navYen > 0
    && Number.isFinite(nowMs) && Number.isFinite(sourceStart) && sourceStart <= nowMs
    && expiresAt != null && nowMs < expiresAt;
}

function normalizedDelay(value: string | null | undefined): DelayClass | null {
  if (!value) return null;
  const exact = value === 'live' ? 'LIVE' : value;
  return DELAY_CLASSES.has(exact as DelayClass) ? exact as DelayClass : null;
}

function normalizedSession(value: string | null | undefined): QuoteSession {
  const upper = String(value ?? '').toUpperCase();
  return QUOTE_SESSIONS.has(upper as QuoteSession) ? upper as QuoteSession : 'UNKNOWN';
}

function classifyDelay(
  raw: RawQuoteTruth,
  provider: string,
  sourceTimestamp: string | null,
  nowMs: number,
): DelayClass {
  if (String(raw.status ?? '').toLowerCase() === 'mock') return 'OFFLINE';

  const declared = normalizedDelay(raw.delayClass);
  const providerKey = provider.toLowerCase();
  const entitlement = String(raw.entitlement ?? 'unknown').toLowerCase();
  const sourceAgeSec = exactAgeSec(sourceTimestamp, nowMs);
  const sourceDateAge = sourceTimestamp && /^\d{4}-\d{2}-\d{2}$/.test(sourceTimestamp)
    ? calendarAgeDays(sourceTimestamp, nowMs) : null;
  if (declared) {
    // A LIVE label is a claim, not evidence. Older/partial backend payloads may
    // carry the word without the source timestamp fields introduced by the
    // LiveQuote contract, so fail closed unless the complete proof is present.
    if (declared === 'LIVE') {
      return raw.realtimeEvidence === true && sourceAgeSec != null && sourceAgeSec <= 60
        ? 'LIVE'
        : 'UNKNOWN';
    }
    if (declared === 'OFFLINE' || declared === 'UNKNOWN') return declared;
    if (declared === '15m') {
      return sourceAgeSec != null && sourceAgeSec <= 30 * 60 ? declared : 'UNKNOWN';
    }
    if (declared === '20m') {
      return sourceAgeSec != null && sourceAgeSec <= 40 * 60 ? declared : 'UNKNOWN';
    }
    return (sourceDateAge != null && sourceDateAge <= 7)
      || (sourceAgeSec != null && sourceAgeSec <= 7 * 86_400)
      ? declared : 'UNKNOWN';
  }

  if (providerKey.includes('jquants') || providerKey.includes('j-quants')) {
    const date = sourceTimestamp?.slice(0, 10);
    if (!date || sourceDateAge == null || sourceDateAge > 7) return 'UNKNOWN';
    return date < tokyoTradingDate(nowMs) ? 'T-1' : 'EOD';
  }
  if (providerKey.includes('fund') || providerKey.includes('投信')) {
    return sourceDateAge != null && sourceDateAge <= 7 ? 'EOD' : 'UNKNOWN';
  }
  if (providerKey.includes('yahoo')) {
    return sourceAgeSec != null && sourceAgeSec <= 40 * 60 ? '20m' : 'UNKNOWN';
  }

  if (providerKey.includes('moomoo')) {
    if (entitlement.includes('delay') || (sourceAgeSec != null && sourceAgeSec >= 600)) {
      return '15m';
    }
    // A transport heartbeat is not a market timestamp. Runtime classification
    // must be supplied by the backend after evaluating the quote-set p95.
    if (raw.realtimeEvidence === true && sourceAgeSec != null && sourceAgeSec <= 60) {
      return 'LIVE';
    }
    return 'UNKNOWN';
  }

  if (String(raw.status ?? '').toLowerCase() === 'delayed') return 'UNKNOWN';
  return 'UNKNOWN';
}

/** Re-evaluate a normalized quote at the decision-consumer boundary. */
export function quoteDecisionUsable(quote: LiveQuote, nowMs = Date.now()): boolean {
  if (!Number.isFinite(nowMs) || quote.price == null || !Number.isFinite(quote.price)) return false;
  if (quote.delayClass === 'LIVE') {
    const age = exactAgeSec(quote.sourceTimestamp, nowMs);
    return age != null && age <= 60;
  }
  if (quote.delayClass === '15m' || quote.delayClass === '20m') {
    const age = exactAgeSec(quote.sourceTimestamp, nowMs);
    const max = quote.delayClass === '15m' ? 30 * 60 : 40 * 60;
    return age != null && age <= max;
  }
  if (quote.delayClass === 'EOD' || quote.delayClass === 'T-1') {
    const dateAge = quote.sourceTimestamp && /^\d{4}-\d{2}-\d{2}$/.test(quote.sourceTimestamp)
      ? calendarAgeDays(quote.sourceTimestamp, nowMs) : null;
    const instantAge = exactAgeSec(quote.sourceTimestamp, nowMs);
    return (dateAge != null && dateAge <= 7)
      || (instantAge != null && instantAge <= 7 * 86_400);
  }
  return false;
}

export function quoteDecisionExpiresAt(quote: LiveQuote): number | null {
  if (quote.delayClass === 'EOD' || quote.delayClass === 'T-1') {
    const calendarDeadline = calendarDateExpiresAt(quote.sourceTimestamp, 7);
    if (calendarDeadline != null) return calendarDeadline;
    const sourceEpoch = exactAuthorityEpoch(quote.sourceTimestamp);
    return sourceEpoch == null ? null : sourceEpoch + 7 * 86_400_000;
  }
  const sourceEpoch = exactAuthorityEpoch(quote.sourceTimestamp);
  if (sourceEpoch == null) return null;
  if (quote.delayClass === 'LIVE') return sourceEpoch + 60_000;
  if (quote.delayClass === '15m') return sourceEpoch + 30 * 60_000;
  if (quote.delayClass === '20m') return sourceEpoch + 40 * 60_000;
  return null;
}

export function normalizeLiveQuote(
  raw: RawQuoteTruth,
  options: {
    symbol: string;
    instrumentType: InstrumentType;
    provider?: string | null;
    receivedAt?: string | null;
    nowMs?: number;
  },
): LiveQuote {
  const nowMs = options.nowMs ?? Date.now();
  const provider = String(raw.provider ?? raw.source ?? options.provider ?? 'unknown');
  const sourceTimestamp = isoTimestamp(
    raw.sourceTimestamp ?? raw.exchangeTs ?? raw.updateTime ?? raw.date,
  );
  const sourceAgeSec = exactAgeSec(sourceTimestamp, nowMs);
  const transportAgeSec = finite(raw.transportAgeSec ?? raw.ageSec);
  const price = finite(raw.price);
  const change = finite(raw.changeAbs);
  const changePct = finite(raw.changePct);
  const previousClose = price != null && change != null ? price - change : null;

  return {
    symbol: options.symbol,
    instrumentType: options.instrumentType,
    provider,
    price,
    previousClose,
    change,
    changePct,
    sourceTimestamp,
    receivedAt: isoTimestamp(raw.receivedAt ?? options.receivedAt),
    ageSec: sourceAgeSec,
    transportAgeSec,
    delayClass: classifyDelay(raw, provider, sourceTimestamp, nowMs),
    session: normalizedSession(raw.session),
    entitlement: String(raw.entitlement ?? 'unknown'),
  };
}

export function quoteStatus(delayClass: DelayClass): 'live' | 'delayed' | 'unknown' | 'mock' {
  if (delayClass === 'LIVE') return 'live';
  if (delayClass === 'OFFLINE') return 'mock';
  if (delayClass === 'UNKNOWN') return 'unknown';
  return 'delayed';
}

export function quoteAsOf(quote: LiveQuote): string {
  if (!quote.sourceTimestamp) return 'asOf 未検証';
  if (/^\d{4}-\d{2}-\d{2}$/.test(quote.sourceTimestamp)) {
    return `asOf ${quote.sourceTimestamp} (日付のみ)`;
  }
  return `asOf ${new Date(quote.sourceTimestamp).toLocaleString('ja-JP', {
    timeZone: 'Asia/Tokyo',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })} JST`;
}

/**
 * v13.5.62 (GPT review item 2: 「取得日時・終値/遅延/リアルタイムの表示を統一」).
 * One plain line per quote: what kind of price it is, from whom, as of when.
 */
export function quoteFreshnessJa(quote: LiveQuote): string {
  const provider = quote.provider ? `（${quote.provider}）` : '';
  const stamp = quote.sourceTimestamp;
  const dateOnly = typeof stamp === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(stamp);
  const clock = stamp && !dateOnly ? new Date(stamp).toLocaleString('ja-JP', {
    timeZone: 'Asia/Tokyo', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) + ' JST' : null;
  const day = dateOnly ? `${stamp.slice(5, 7)}/${stamp.slice(8, 10)}` : null;
  if (quote.delayClass === 'LIVE') return `リアルタイム ${clock ?? '取得時刻不明'}${provider}`;
  if (quote.delayClass === '15m' || quote.delayClass === '20m') return `${quote.delayClass.replace('m', '分')}遅延 ${clock ?? '取得時刻不明'}${provider}`;
  if (quote.delayClass === 'EOD') return `終値 ${day ?? clock ?? '基準日不明'}${provider}`;
  return `取得時刻不明${provider}`;
}

export function quoteAge(quote: LiveQuote): string {
  if (quote.ageSec == null) return 'age 未検証';
  if (quote.ageSec < 60) return `age ${quote.ageSec}s`;
  return `age ${Math.floor(quote.ageSec / 60)}m`;
}
