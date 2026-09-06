import type { ChartIntelligencePayload } from '../types/chartIntelligence';

export const VERIFIED_SNAPSHOT_SCHEMA = 'argus-verified-view-snapshot-v1';
// CONTRACT: this must equal scanner.py's `_VERIFIED_VIEW_METHOD_VERSION`
// byte for byte — the verifier compares with `!==`, and every published
// snapshot is rejected as `method_incompatible` when it does not. The value is
// composed in the backend from four module METHOD_VERSIONs; the fourth
// (today-replay-calibration) was added in v13.5.14 and this constant was not
// updated, so from v13.5.14 to v13.5.53 no verified snapshot ever reached the
// client cache and every release's seed-warm-profile job failed. The equality
// is pinned in CI by test_argus_method_version_contract.py (reads the real
// backend value) and web/scripts/verified-snapshot.test.mjs.
export const VERIFIED_VIEW_METHOD_VERSION =
  'verified-chart-view-v1:chart-intelligence-phase2-v2-pit-bound:'
  + 'market-context-replay-v3-pit-bound:today-replay-calibration-v3-sho-conditioned';
const DB_NAME = 'argus-verified-snapshots';
const DB_VERSION = 1;
const SNAPSHOT_STORE = 'snapshots';
const DRAWING_STORE = 'drawing-state';
const MAX_SNAPSHOTS = 24;

export type SnapshotQuality = 'live' | 'partial' | 'stale';
export type SnapshotViewState =
  | 'NO_CACHE_LOADING'
  | 'CACHE_READY_REVALIDATING'
  | 'CURRENT_READY'
  | 'STALE_FALLBACK'
  | 'ERROR_WITH_CACHE'
  | 'ERROR_WITHOUT_CACHE';

export interface VerifiedSnapshot<T> {
  schemaVersion: string;
  snapshotId: string;
  kind: string;
  instrument: string;
  horizon: string;
  datasetHash: string;
  payloadHash: string;
  methodVersion: string;
  asOf: string;
  generatedAt: string;
  verifiedAt: string;
  quality: SnapshotQuality;
  sourceStatus: Record<string, string>;
  releaseBinding?: {
    expectedBuildSha: string;
    producerTriggerId: string;
    triggeredAt: string;
  };
  verificationStatus: 'verified';
  payload: T;
}

export interface SnapshotExpectation {
  kind: string;
  instrument: string;
  horizon: string;
  methodVersion: string;
}

interface SnapshotRecord {
  key: string;
  schemaVersion: string;
  verifiedAt: string;
  snapshot: VerifiedSnapshot<ChartIntelligencePayload>;
}

const qualityRank: Record<SnapshotQuality, number> = {
  stale: 0, partial: 1, live: 2,
};
const memory = new Map<string, VerifiedSnapshot<ChartIntelligencePayload>>();
let databasePromise: Promise<IDBDatabase | null> | null = null;

export function snapshotKey(expectation: Pick<SnapshotExpectation,
  'kind' | 'instrument' | 'horizon'>) {
  return `${expectation.kind.toLowerCase()}:${expectation.instrument.toUpperCase()}:` +
    expectation.horizon.toUpperCase();
}

function mark(name: string) {
  try { performance.mark(`argus-snapshot:${name}`); } catch { /* instrumentation only */ }
}

function sorted(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sorted);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => [key, sorted(child)]));
  }
  return value;
}

function canonical(value: unknown) {
  return JSON.stringify(sorted(value));
}

async function sha256(value: string) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

export async function calculateSnapshotId(snapshot: Omit<
  VerifiedSnapshot<ChartIntelligencePayload>, 'snapshotId'> | VerifiedSnapshot<ChartIntelligencePayload>) {
  const value = snapshot as VerifiedSnapshot<ChartIntelligencePayload>;
  const identity = {
    schemaVersion: value.schemaVersion, kind: value.kind,
    instrument: value.instrument, horizon: value.horizon,
    datasetHash: value.datasetHash, payloadHash: value.payloadHash,
    methodVersion: value.methodVersion,
    asOf: value.asOf, generatedAt: value.generatedAt,
    verifiedAt: value.verifiedAt, quality: value.quality,
    sourceStatus: value.sourceStatus,
    verificationStatus: value.verificationStatus,
    ...(value.releaseBinding ? { releaseBinding: value.releaseBinding } : {}),
  };
  return `vs-${(await sha256(canonical(identity))).slice(0, 32)}`;
}

function portableNumber(value: unknown) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  const text = value.toFixed(8).replace(/0+$/, '').replace(/\.$/, '');
  return text === '-0' || text === '' ? '0' : text;
}

export async function calculatePayloadHash(payload: ChartIntelligencePayload) {
  const material = (payload.indicators?.bars ?? []).map((bar) => ({
    date: String(bar.date ?? ''),
    open: portableNumber(bar.open), high: portableNumber(bar.high),
    low: portableNumber(bar.low), close: portableNumber(bar.close),
    volume: bar.volume == null ? null : portableNumber(bar.volume),
    availableFrom: String(bar.availableFrom ?? ''),
  }));
  return sha256(canonical(material));
}

function chartPayloadValid(payload: unknown, expectation: SnapshotExpectation,
                           datasetHash: string): boolean {
  if (!payload || typeof payload !== 'object') return false;
  const value = payload as ChartIntelligencePayload;
  const actual = value.instrumentMetadata?.symbol ?? value.symbol;
  if (actual?.toUpperCase() !== expectation.instrument.toUpperCase()) return false;
  if (/mock/i.test(value.status ?? '') || value.automaticAiCalls !== 0) return false;
  const bars = value.indicators?.bars;
  if (!Array.isArray(bars) || bars.length === 0) return false;
  let prior = '';
  const dates = new Set<string>();
  for (const bar of bars) {
    if (!bar || typeof bar.date !== 'string' || bar.date <= prior || dates.has(bar.date)) {
      return false;
    }
    if (![bar.open, bar.high, bar.low, bar.close].every(
      (number) => Number.isFinite(number) && number > 0)) return false;
    if (bar.high < Math.max(bar.open, bar.close) ||
        bar.low > Math.min(bar.open, bar.close)) return false;
    prior = bar.date; dates.add(bar.date);
  }
  const horizon = expectation.horizon.replace(/D$/i, '');
  const context = value.marketReplay?.contexts?.[horizon];
  return !!context && context.datasetHash === datasetHash;
}

export async function verifySnapshot(
  candidate: unknown, expectation: SnapshotExpectation, now = Date.now(),
): Promise<{ ok: true; snapshot: VerifiedSnapshot<ChartIntelligencePayload> } |
  { ok: false; reason: string }> {
  if (!candidate || typeof candidate !== 'object') return { ok: false, reason: 'malformed' };
  const value = candidate as Partial<VerifiedSnapshot<ChartIntelligencePayload>>;
  const required = ['schemaVersion', 'snapshotId', 'kind', 'instrument', 'horizon',
    'datasetHash', 'payloadHash', 'methodVersion', 'asOf', 'generatedAt', 'verifiedAt', 'quality',
    'sourceStatus', 'payload'] as const;
  if (required.some((key) => value[key] == null || value[key] === '')) {
    return { ok: false, reason: 'schema_missing_field' };
  }
  if (value.schemaVersion !== VERIFIED_SNAPSHOT_SCHEMA) {
    return { ok: false, reason: 'schema_incompatible' };
  }
  if (value.verificationStatus !== 'verified') {
    return { ok: false, reason: 'readback_unverified' };
  }
  if (value.kind?.toLowerCase() !== expectation.kind.toLowerCase()) {
    return { ok: false, reason: 'kind_mismatch' };
  }
  if (value.instrument?.toUpperCase() !== expectation.instrument.toUpperCase()) {
    return { ok: false, reason: 'instrument_mismatch' };
  }
  if (value.horizon?.toUpperCase() !== expectation.horizon.toUpperCase()) {
    return { ok: false, reason: 'horizon_mismatch' };
  }
  if (value.methodVersion !== expectation.methodVersion) {
    return { ok: false, reason: 'method_incompatible' };
  }
  if (!value.quality || !(value.quality in qualityRank) ||
      !value.sourceStatus || typeof value.sourceStatus !== 'object') {
    return { ok: false, reason: 'quality_or_source_invalid' };
  }
  if (Object.values(value.sourceStatus).some((status) => /mock/i.test(status))) {
    return { ok: false, reason: 'mock_source' };
  }
  if (value.releaseBinding) {
    const binding = value.releaseBinding;
    if (JSON.stringify(Object.keys(binding).sort()) !== JSON.stringify([
      'expectedBuildSha', 'producerTriggerId', 'triggeredAt',
    ])
        || !/^[0-9a-f]{40}$/.test(binding.expectedBuildSha ?? '')
        || !binding.producerTriggerId
        || !Number.isFinite(Date.parse(binding.triggeredAt ?? ''))) {
      return { ok: false, reason: 'release_binding_invalid' };
    }
  }
  const times = [value.asOf, value.generatedAt, value.verifiedAt]
    .map((item) => Date.parse(String(item)));
  if (times.some((time) => !Number.isFinite(time)) ||
      times.some((time) => time > now + 5 * 60_000)) {
    return { ok: false, reason: 'timestamp_invalid' };
  }
  if (!chartPayloadValid(value.payload, expectation, String(value.datasetHash))) {
    return { ok: false, reason: 'payload_invalid' };
  }
  const typed = value as VerifiedSnapshot<ChartIntelligencePayload>;
  if (await calculatePayloadHash(typed.payload) !== typed.payloadHash) {
    return { ok: false, reason: 'payload_hash_mismatch' };
  }
  if (await calculateSnapshotId(typed) !== typed.snapshotId) {
    return { ok: false, reason: 'snapshot_id_mismatch' };
  }
  return { ok: true, snapshot: typed };
}

export function shouldReplaceSnapshot(
  current: VerifiedSnapshot<ChartIntelligencePayload> | null,
  candidate: VerifiedSnapshot<ChartIntelligencePayload>,
) {
  if (!current) return true;
  if (current.snapshotId === candidate.snapshotId) return false;
  if (Date.parse(candidate.generatedAt) < Date.parse(current.generatedAt)) return false;
  return qualityRank[candidate.quality] >= qualityRank[current.quality];
}

function openDatabase(): Promise<IDBDatabase | null> {
  if (databasePromise) return databasePromise;
  databasePromise = new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') { resolve(null); return; }
    let settled = false;
    try {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(SNAPSHOT_STORE)) {
          db.createObjectStore(SNAPSHOT_STORE, { keyPath: 'key' });
        }
        if (!db.objectStoreNames.contains(DRAWING_STORE)) {
          db.createObjectStore(DRAWING_STORE, { keyPath: 'key' });
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

export function memorySnapshot(expectation: SnapshotExpectation) {
  return memory.get(snapshotKey(expectation)) ?? null;
}

// v13.5.1: the exact same verifySnapshot runs in a Web Worker so that
// multi-megabyte parse/canonicalize/hash work never blocks input or paint on
// the interaction thread. Falls back to in-thread verification wherever
// workers are unavailable (older engines, some test environments).
type VerifyOutcome = Awaited<ReturnType<typeof verifySnapshot>>;
interface PendingVerify {
  resolve: (value: VerifyOutcome) => void;
}
let verifyWorker: Worker | null | undefined;
let verifyRequestSeq = 0;
const pendingVerifies = new Map<number, PendingVerify>();

async function workerInstance(): Promise<Worker | null> {
  if (verifyWorker !== undefined) return verifyWorker;
  // Worker construction (and its import.meta reference) lives in a separate
  // module so CommonJS test loaders can consume this file; environments
  // without a Worker global never load it and verify in-thread instead.
  if (typeof Worker === 'undefined') {
    verifyWorker = null;
    return null;
  }
  try {
    const { createVerifyWorker } = await import('./verifyWorkerClient');
    verifyWorker = createVerifyWorker();
    if (!verifyWorker) return null;
    verifyWorker.onmessage = (event: MessageEvent<{
      requestId: number; result: VerifyOutcome }>) => {
      const pending = pendingVerifies.get(event.data.requestId);
      if (pending) {
        pendingVerifies.delete(event.data.requestId);
        pending.resolve(event.data.result);
      }
    };
    verifyWorker.onerror = () => {
      // A broken worker must never wedge verification: fail every pending
      // request over to the in-thread path and stop using the worker.
      const pending = [...pendingVerifies.values()];
      pendingVerifies.clear();
      verifyWorker = null;
      for (const entry of pending) {
        entry.resolve({ ok: false, reason: 'worker_unavailable' });
      }
    };
  } catch {
    verifyWorker = null;
  }
  return verifyWorker;
}

async function verifyOffThread(
  payload: { rawText?: string; candidate?: unknown },
  expectation: SnapshotExpectation,
): Promise<VerifyOutcome> {
  const worker = await workerInstance();
  if (!worker) {
    const value = payload.rawText != null
      ? JSON.parse(payload.rawText) : payload.candidate;
    return verifySnapshot(value, expectation);
  }
  const requestId = ++verifyRequestSeq;
  const result = await new Promise<VerifyOutcome>((resolve) => {
    pendingVerifies.set(requestId, { resolve });
    worker.postMessage({ requestId, expectation, ...payload });
  });
  if (!result.ok && result.reason === 'worker_unavailable') {
    const value = payload.rawText != null
      ? JSON.parse(payload.rawText) : payload.candidate;
    return verifySnapshot(value, expectation);
  }
  return result;
}

/** Verify a raw HTTP response body without blocking the main thread. */
export function verifySnapshotText(
  rawText: string, expectation: SnapshotExpectation,
): Promise<VerifyOutcome> {
  return verifyOffThread({ rawText }, expectation);
}

/** Verify an already-materialized candidate without blocking the main thread. */
export function verifySnapshotOffThread(
  candidate: unknown, expectation: SnapshotExpectation,
): Promise<VerifyOutcome> {
  return verifyOffThread({ candidate }, expectation);
}

export async function readVerifiedSnapshot(expectation: SnapshotExpectation) {
  mark('cache-lookup-start');
  const key = snapshotKey(expectation);
  const cached = memory.get(key);
  if (cached) { mark('cache-found-memory'); return cached; }
  const db = await openDatabase();
  if (!db) return null;
  try {
    const record = await requestValue(db.transaction(SNAPSHOT_STORE, 'readonly')
      .objectStore(SNAPSHOT_STORE).get(key)) as SnapshotRecord | null;
    const verified = await verifySnapshotOffThread(record?.snapshot, expectation);
    if (!verified.ok) {
      if (record) {
        try { db.transaction(SNAPSHOT_STORE, 'readwrite')
          .objectStore(SNAPSHOT_STORE).delete(key); } catch { /* best effort */ }
      }
      return null;
    }
    memory.set(key, verified.snapshot);
    mark('cache-found-indexeddb');
    return verified.snapshot;
  } catch { return null; }
}

async function collectGarbage(db: IDBDatabase) {
  try {
    const store = db.transaction(SNAPSHOT_STORE, 'readwrite').objectStore(SNAPSHOT_STORE);
    const records = await requestValue(store.getAll()) as SnapshotRecord[] | null;
    if (!records) return;
    const validSchema = records.filter((record) =>
      record.schemaVersion === VERIFIED_SNAPSHOT_SCHEMA)
      .sort((left, right) => right.verifiedAt.localeCompare(left.verifiedAt));
    const keep = new Set(validSchema.slice(0, MAX_SNAPSHOTS).map((record) => record.key));
    records.forEach((record) => {
      if (!keep.has(record.key)) store.delete(record.key);
    });
  } catch { /* capacity/transaction failure must not break memory cache */ }
}

export async function writeVerifiedSnapshot(
  candidate: unknown, expectation: SnapshotExpectation,
  current: VerifiedSnapshot<ChartIntelligencePayload> | null,
) {
  const verified = await verifySnapshotOffThread(candidate, expectation);
  if (!verified.ok || !shouldReplaceSnapshot(current, verified.snapshot)) {
    return current ?? (verified.ok ? verified.snapshot : null);
  }
  const key = snapshotKey(expectation);
  const db = await openDatabase();
  if (!db) {
    memory.set(key, verified.snapshot);
    return verified.snapshot;
  }
  try {
    const record: SnapshotRecord = {
      key, schemaVersion: VERIFIED_SNAPSHOT_SCHEMA,
      verifiedAt: verified.snapshot.verifiedAt, snapshot: verified.snapshot,
    };
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(SNAPSHOT_STORE, 'readwrite');
      transaction.objectStore(SNAPSHOT_STORE).put(record);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
    const readBack = await requestValue(db.transaction(SNAPSHOT_STORE, 'readonly')
      .objectStore(SNAPSHOT_STORE).get(key)) as SnapshotRecord | null;
    const readBackResult = await verifySnapshotOffThread(
      readBack?.snapshot, expectation);
    if (!readBackResult.ok ||
        readBackResult.snapshot.snapshotId !== verified.snapshot.snapshotId) {
      return current;
    }
    memory.set(key, readBackResult.snapshot);
    void collectGarbage(db);
    return readBackResult.snapshot;
  } catch {
    // IndexedDB was available but its write/read-back failed. Keep the last
    // verified pointer; only a genuinely unavailable database may use the
    // memory-only fallback above.
    return current;
  }
}

export async function readDrawingState<T>(key: string, fallback: T): Promise<T> {
  const db = await openDatabase();
  if (!db) return fallback;
  try {
    const record = await requestValue(db.transaction(DRAWING_STORE, 'readonly')
      .objectStore(DRAWING_STORE).get(key)) as { key: string; value: T } | null;
    return record?.value ?? fallback;
  } catch { return fallback; }
}

export async function writeDrawingState<T>(key: string, value: T) {
  const db = await openDatabase();
  if (!db) return false;
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(DRAWING_STORE, 'readwrite');
      transaction.objectStore(DRAWING_STORE).put({ key, value, schemaVersion: 1 });
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
    });
    return true;
  } catch { return false; }
}

export function resetVerifiedSnapshotMemoryForTests() {
  memory.clear();
}
