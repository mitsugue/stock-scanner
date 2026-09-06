import type { ChartIntelligencePayload } from '../types/chartIntelligence';
import { calculatePayloadHash } from './verifiedSnapshot';

export const ASSET_CHART_SCHEMA = 'argus-asset-chart-cache-v1';
// CONTRACT: this must equal argus_chart_intelligence.METHOD_VERSION — the
// value every chart-intelligence payload carries as `methodVersion`.
// writeAssetChart returns null (caches nothing) on any mismatch, so a stale
// pin here silently disables the whole asset-chart cache. Pinned in CI by
// test_argus_method_version_contract.py and asset-chart-policy.test.cjs.
export const ASSET_CHART_METHOD_VERSION = 'chart-intelligence-phase2-v2-pit-bound';
const DB_NAME = 'argus-asset-chart-cache';
const DB_VERSION = 1;
const STORE = 'asset-chart';
const MAX_RECORDS = 32;

export type AssetChartViewState =
  | 'CACHE_READY_REVALIDATING'
  | 'NO_CACHE_LOADING'
  | 'CURRENT_READY'
  | 'RATE_LIMITED_WITH_CACHE'
  | 'RATE_LIMITED_WITHOUT_CACHE'
  | 'ERROR_WITH_CACHE'
  | 'ERROR_WITHOUT_CACHE';

export interface AssetChartUiContract {
  state: AssetChartViewState;
  errorClass: string | null;
  retryAt: number | null;
}

export type AssetChartUiEvent =
  | { type: 'http_200' }
  | { type: 'failure'; hasCache: boolean; errorClass: string;
      retryAt: number | null };

/** Exclusive chart state transition. A successful response is authoritative:
 * it clears a prior HTTP 429/error and its retry deadline atomically. */
export function assetChartUiTransition(
  _previous: AssetChartUiContract,
  event: AssetChartUiEvent,
): AssetChartUiContract {
  if (event.type === 'http_200') {
    return { state: 'CURRENT_READY', errorClass: null, retryAt: null };
  }
  const limited = event.errorClass === 'rate_limited'
    || event.errorClass === 'retry_wait';
  return {
    state: limited
      ? event.hasCache ? 'RATE_LIMITED_WITH_CACHE' : 'RATE_LIMITED_WITHOUT_CACHE'
      : event.hasCache ? 'ERROR_WITH_CACHE' : 'ERROR_WITHOUT_CACHE',
    errorClass: event.errorClass,
    retryAt: event.retryAt,
  };
}

export interface AssetChartIdentity {
  market: string;
  symbol: string;
  timeframe: 'daily' | 'weekly';
  methodVersion?: string;
}

export interface AssetChartRecord {
  key: string;
  schemaVersion: typeof ASSET_CHART_SCHEMA;
  symbol: string;
  market: string;
  timeframe: 'daily' | 'weekly';
  asOf: string;
  generatedAt: string;
  payloadHash: string;
  methodVersion: string;
  payload: ChartIntelligencePayload;
}

const memory = new Map<string, AssetChartRecord>();
let databasePromise: Promise<IDBDatabase | null> | null = null;

export function assetChartKey(identity: AssetChartIdentity) {
  return [
    'asset-chart',
    identity.market.toUpperCase(),
    identity.symbol.toUpperCase(),
    identity.timeframe,
    identity.methodVersion ?? ASSET_CHART_METHOD_VERSION,
  ].join(':');
}

export function parseRetryAfter(value: string | null, now = Date.now()) {
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return now + Math.min(seconds, 15 * 60) * 1000;
  }
  const absolute = Date.parse(value);
  if (!Number.isFinite(absolute)) return null;
  return Math.max(now, Math.min(absolute, now + 15 * 60_000));
}

export function boundedRetryAt(attempt: number, now = Date.now()) {
  const seconds = Math.min(300, 30 * (2 ** Math.max(0, Math.min(attempt, 4))));
  return now + seconds * 1000;
}

export class AssetChartRequestGate {
  private tail: Promise<void> = Promise.resolve();

  enqueue<T>(task: () => Promise<T>): Promise<T> {
    const request = this.tail.then(task, task);
    this.tail = request.then(() => undefined, () => undefined);
    return request;
  }
}

export const assetChartRequestGate = new AssetChartRequestGate();
export const verifiedChartRequestGate = new AssetChartRequestGate();

function openDatabase(): Promise<IDBDatabase | null> {
  if (databasePromise) return databasePromise;
  databasePromise = new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') { resolve(null); return; }
    let settled = false;
    try {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(STORE)) {
          request.result.createObjectStore(STORE, { keyPath: 'key' });
        }
      };
      request.onsuccess = () => {
        settled = true;
        request.result.onversionchange = () => request.result.close();
        resolve(request.result);
      };
      request.onerror = () => { settled = true; resolve(null); };
      request.onblocked = () => { if (!settled) resolve(null); };
    } catch { resolve(null); }
  });
  return databasePromise;
}

function requestValue<T>(request: IDBRequest<T>): Promise<T | null> {
  return new Promise((resolve) => {
    request.onsuccess = () => resolve(request.result ?? null);
    request.onerror = () => resolve(null);
  });
}

async function recordValid(record: AssetChartRecord | null,
                           identity: AssetChartIdentity) {
  if (!record || record.schemaVersion !== ASSET_CHART_SCHEMA
      || record.key !== assetChartKey(identity)
      || record.symbol.toUpperCase() !== identity.symbol.toUpperCase()
      || record.market.toUpperCase() !== identity.market.toUpperCase()
      || record.timeframe !== identity.timeframe
      || record.methodVersion !== (identity.methodVersion ?? ASSET_CHART_METHOD_VERSION)
      || record.payload.methodVersion !== record.methodVersion
      || record.payload.automaticAiCalls !== 0
      || !Array.isArray(record.payload.indicators?.bars)
      || record.payload.indicators.bars.length === 0) return false;
  const actual = record.payload.instrumentMetadata?.symbol ?? record.payload.symbol;
  if (actual?.toUpperCase() !== identity.symbol.toUpperCase()) return false;
  return await calculatePayloadHash(record.payload) === record.payloadHash;
}

export async function readAssetChart(identity: AssetChartIdentity) {
  const key = assetChartKey(identity);
  const current = memory.get(key) ?? null;
  if (current && await recordValid(current, identity)) return current;
  if (current) memory.delete(key);
  const db = await openDatabase();
  if (!db) return null;
  try {
    const record = await requestValue(
      db.transaction(STORE, 'readonly').objectStore(STORE).get(key),
    ) as AssetChartRecord | null;
    if (!await recordValid(record, identity)) {
      if (record) {
        try {
          db.transaction(STORE, 'readwrite').objectStore(STORE).delete(key);
        } catch { /* best-effort removal of an incompatible record */ }
      }
      return null;
    }
    memory.set(key, record!);
    return record!;
  } catch { return null; }
}

async function collectGarbage(db: IDBDatabase) {
  try {
    const store = db.transaction(STORE, 'readwrite').objectStore(STORE);
    const rows = await requestValue(store.getAll()) as AssetChartRecord[] | null;
    if (!rows || rows.length <= MAX_RECORDS) return;
    rows.sort((a, b) => b.generatedAt.localeCompare(a.generatedAt))
      .slice(MAX_RECORDS).forEach((row) => store.delete(row.key));
  } catch { /* cache pressure must not affect the chart */ }
}

export async function writeAssetChart(identity: AssetChartIdentity,
                                      payload: ChartIntelligencePayload) {
  if (payload.methodVersion !== (identity.methodVersion ?? ASSET_CHART_METHOD_VERSION)
      || payload.timeframe !== identity.timeframe
      || payload.automaticAiCalls !== 0) return null;
  const actual = payload.instrumentMetadata?.symbol ?? payload.symbol;
  if (actual?.toUpperCase() !== identity.symbol.toUpperCase()
      || !payload.indicators?.bars?.length) return null;
  const record: AssetChartRecord = {
    key: assetChartKey(identity),
    schemaVersion: ASSET_CHART_SCHEMA,
    symbol: identity.symbol.toUpperCase(),
    market: identity.market.toUpperCase(),
    timeframe: identity.timeframe,
    asOf: payload.asOf || payload.periodEnd || '',
    generatedAt: new Date().toISOString(),
    payloadHash: await calculatePayloadHash(payload),
    methodVersion: identity.methodVersion ?? ASSET_CHART_METHOD_VERSION,
    payload,
  };
  const db = await openDatabase();
  if (!db) {
    memory.set(record.key, record);
    return record;
  }
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(STORE, 'readwrite');
      transaction.objectStore(STORE).put(record);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
    const readBack = await requestValue(
      db.transaction(STORE, 'readonly').objectStore(STORE).get(record.key),
    ) as AssetChartRecord | null;
    if (!await recordValid(readBack, identity)
        || readBack?.payloadHash !== record.payloadHash) return null;
    memory.set(record.key, readBack);
    void collectGarbage(db);
    return readBack;
  } catch { return null; }
}
