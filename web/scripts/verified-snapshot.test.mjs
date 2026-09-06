import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { methodVersionContract } from './method-version-contract.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const nodeRequire = createRequire(import.meta.url);
nodeRequire.extensions['.ts'] = (module, filename) => {
  const source = fs.readFileSync(filename, 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  }).outputText;
  module._compile(output, filename);
};

async function importTypeScriptModule(relativePath) {
  const source = fs.readFileSync(path.join(root, relativePath), 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: relativePath,
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
}

const {
  calculatePayloadHash, calculateSnapshotId, memorySnapshot, resetVerifiedSnapshotMemoryForTests,
  shouldReplaceSnapshot, verifySnapshot, writeVerifiedSnapshot, VERIFIED_VIEW_METHOD_VERSION,
} = await importTypeScriptModule('src/lib/verifiedSnapshot.ts');
const { formatSnapshotStatus, snapshotFreshness } = nodeRequire(
  path.join(root, 'src/lib/snapshotFreshness.ts'));

const expectation = {
  kind: 'market-chart', instrument: '1321', horizon: '5D',
  methodVersion: 'view-method-a',
};
// v13.5.54: the old form of this check composed the backend value from a
// hardcoded list of THREE modules and so drifted together with the constant
// when scanner.py added a fourth in v13.5.14 — production rejected every
// verified snapshot as method_incompatible for 39 releases. The composition is
// now read from scanner.py itself; no method version is spelled out here.
const contract = methodVersionContract();
assert.equal(contract.verifiedView.frontend, VERIFIED_VIEW_METHOD_VERSION,
  'the contract reader must see the same constant the module exports');
assert.ok(contract.verifiedView.modules.includes('argus_today_intelligence'),
  'scanner composition must include the forecast engine (v13.5.14)');
assert.equal(VERIFIED_VIEW_METHOD_VERSION, contract.verifiedView.backend,
  'frontend and backend canonical snapshot methods must never drift');
assert.equal(contract.chart.frontend, contract.chart.backend,
  'frontend asset-chart pin must equal the payload methodVersion the backend emits');

function payload({ instrument = '1321', hash = 'data-a', status = 'complete',
  asOf = '2026-07-23T06:00:00Z', session = 'REGULAR' } = {}) {
  return {
    schemaVersion: 'chart-intelligence-phase2-v1',
    methodVersion: 'chart-intelligence-phase2-v1',
    asOf, symbol: instrument, status, source: 'verified-provider-cache',
    automaticAiCalls: 0, instrumentMetadata: { symbol: instrument },
    indicators: { status: 'complete', bars: [
      { date: '2026-07-22', open: 100, high: 102, low: 99, close: 101, volume: 10 },
      { date: '2026-07-23', open: 101, high: 104, low: 100, close: 103, volume: 12 },
    ] },
    marketReplay: { contexts: {
      1: { datasetHash: hash }, 5: { datasetHash: hash }, 20: { datasetHash: hash },
    } },
    marketCalendar: {
      isTradingDay: !/CLOSED|HOLIDAY|WEEKEND/.test(session), session,
    },
  };
}

async function candidate(overrides = {}) {
  const value = {
    schemaVersion: 'argus-verified-view-snapshot-v1',
    snapshotId: '',
    kind: 'market-chart', instrument: '1321', horizon: '5D',
    datasetHash: 'data-a', payloadHash: '', methodVersion: 'view-method-a',
    asOf: '2026-07-23T06:00:00Z',
    generatedAt: '2026-07-23T06:01:00Z',
    verifiedAt: '2026-07-23T06:01:00Z',
    quality: 'live',
    sourceStatus: { chart: 'complete', replay: 'updated' },
    verificationStatus: 'verified',
    payload: payload(),
    ...overrides,
  };
  value.payloadHash = await calculatePayloadHash(value.payload);
  value.snapshotId = await calculateSnapshotId(value);
  return value;
}

const valid = await candidate();
assert.equal(valid.snapshotId, 'vs-f6893880a35b6aec1b84b48bd20b53f1',
  'Python and browser snapshot IDs must use the same portable material');
assert.equal((await verifySnapshot(valid, expectation,
  Date.parse('2026-07-23T06:02:00Z'))).ok, true);

const releaseBound = await candidate({ releaseBinding: {
  expectedBuildSha: 'a'.repeat(40),
  producerTriggerId: 'v13-release-browser-0001',
  triggeredAt: '2026-07-23T06:00:30Z',
} });
releaseBound.snapshotId = await calculateSnapshotId(releaseBound);
assert.notEqual(releaseBound.snapshotId, valid.snapshotId);
assert.equal((await verifySnapshot(releaseBound, expectation,
  Date.parse('2026-07-23T06:02:00Z'))).ok, true);
const releaseBoundExtra = structuredClone(releaseBound);
releaseBoundExtra.releaseBinding.unexpected = true;
releaseBoundExtra.snapshotId = await calculateSnapshotId(releaseBoundExtra);
assert.equal((await verifySnapshot(releaseBoundExtra, expectation,
  Date.parse('2026-07-23T06:02:00Z'))).reason, 'release_binding_invalid');

const invalidSchema = await candidate({ schemaVersion: 'old-v0' });
assert.equal((await verifySnapshot(invalidSchema, expectation)).ok, false);
assert.equal((await verifySnapshot(valid, { ...expectation, instrument: 'SPY' })).ok, false);
assert.equal((await verifySnapshot(valid, { ...expectation, horizon: '20D' })).ok, false);
const mock = await candidate({ payload: payload({ status: 'mock' }) });
assert.equal((await verifySnapshot(mock, expectation)).ok, false);

const newer = await candidate({
  datasetHash: 'data-b', generatedAt: '2026-07-23T06:03:00Z',
  verifiedAt: '2026-07-23T06:03:00Z',
  payload: payload({ hash: 'data-b' }),
});
newer.snapshotId = await calculateSnapshotId(newer);
assert.equal(shouldReplaceSnapshot(valid, newer), true);
assert.equal(shouldReplaceSnapshot(newer, valid), false,
  'a late old response must never roll back a new snapshot');
const lowerQuality = await candidate({
  quality: 'partial', generatedAt: '2026-07-23T06:04:00Z',
  verifiedAt: '2026-07-23T06:04:00Z',
});
lowerQuality.snapshotId = await calculateSnapshotId(lowerQuality);
assert.equal(shouldReplaceSnapshot(valid, lowerQuality), false);

resetVerifiedSnapshotMemoryForTests();
const published = await writeVerifiedSnapshot(valid, expectation, null);
assert.equal(published?.snapshotId, valid.snapshotId);
assert.equal(memorySnapshot(expectation)?.snapshotId, valid.snapshotId,
  'IndexedDB-unavailable fallback remains bounded to memory');
const invalidNetwork = await candidate({ payload: payload({ status: 'mock' }) });
assert.equal((await writeVerifiedSnapshot(invalidNetwork, expectation, valid))?.snapshotId,
  valid.snapshotId, 'invalid network data must preserve verified cache');

const openFresh = { ...valid, asOf: '2026-07-23T06:00:00Z',
  payload: payload({ session: 'REGULAR' }) };
assert.equal(snapshotFreshness(openFresh, Date.parse('2026-07-23T06:30:00Z')), 'fresh');
assert.equal(snapshotFreshness(openFresh, Date.parse('2026-07-23T08:00:00Z')), 'stale_usable');
const closedFresh = { ...valid, asOf: '2026-07-20T06:00:00Z',
  payload: payload({ session: 'HOLIDAY_CLOSED' }) };
assert.equal(snapshotFreshness(closedFresh, Date.parse('2026-07-23T06:00:00Z')), 'fresh',
  'holiday/weekend close must not create a false stale failure');
const postMarketFresh = { ...valid, asOf: '2026-07-23T06:00:00Z',
  payload: payload({ session: 'POST_MARKET' }) };
assert.equal(snapshotFreshness(postMarketFresh, Date.parse('2026-07-23T09:00:00Z')), 'fresh',
  'completed POST_MARKET sessions use the closed-session continuity window');
assert.match(formatSnapshotStatus('ERROR_WITH_CACHE', valid), /更新要確認/);
assert.match(formatSnapshotStatus('CACHE_READY_REVALIDATING', valid), /更新中/);

const hook = fs.readFileSync(path.join(root, 'src/hooks/useChartIntelligence.ts'), 'utf8');
const today = fs.readFileSync(path.join(root,
  'src/components/today/ArgusTodayPanel.tsx'), 'utf8');
const loader = fs.readFileSync(path.join(root,
  'src/components/common/TriangleStepLoader.css'), 'utf8');
const loaderComponent = fs.readFileSync(path.join(root,
  'src/components/common/TriangleStepLoader.tsx'), 'utf8');
const cacheSource = fs.readFileSync(path.join(root,
  'src/lib/verifiedSnapshot.ts'), 'utf8');

assert.ok(hook.indexOf('readVerifiedSnapshot(expectation)') <
  hook.indexOf('await networkOutcomePromise'),
  'cache lookup must precede network publication');
assert.ok(hook.indexOf('await cachePromise') <
  hook.indexOf('fetchVerifiedSnapshot(verifiedUrl'),
  'revalidation must start after the cache read so If-None-Match is supplied');
assert.match(hook, /memoryCached \?\? cached/,
  'the cached snapshot must supply the revalidation validator (ETag/304)');
assert.match(hook, /\.then\(\s*\(value\) => \(\{ ok: true as const, value \}\),[\s\S]*ok: false as const/,
  'superseded requests must resolve to an outcome, never an unhandled rejection');
assert.match(hook, /requestSequence !== sequence\.current/,
  'request sequence must reject an old instrument response');
assert.match(hook, /verifiedChartRequestGate\.enqueue/,
  'verified market requests must be serialized to avoid a concurrent 429 burst');
assert.match(hook, /key,\s*snapshot: cached,[\s\S]*ERROR_WITHOUT_CACHE/,
  'a cold network error must retain its requested key and expose retry state');
assert.match(hook, /new AbortController\(\)/);
assert.match(hook, /writeVerifiedSnapshot[\s\S]*atomic-swap-complete/);
assert.match(cacheSource,
  /IndexedDB was available[\s\S]*return current;/,
  'an IndexedDB write/read-back failure must keep the old pointer');
assert.match(hook, /225/);
assert.match(hook, /5_000/);
assert.match(today, /data-snapshot-id=\{chartLoad\.snapshotId/);
assert.match(today, /data-snapshot-state=\{chartLoad\.snapshotState/);
assert.match(today, /data-argus-contract="canonical-market-snapshot-v1"/);
assert.match(today, /data-canonical-snapshot-id=\{chartLoad\.snapshotId/);
assert.match(today, /data-canonical-verification=\{chartLoad\.snapshotId \? 'verified' : 'unverified'\}/);
assert.match(today, /data-canonical-instrument=\{projection\?\.symbol \?\? selectedSymbol\}/);
assert.match(today, /data-canonical-horizon=\{`\$\{projection\?\.horizonDays \?\? horizon\}D`\}/);
assert.match(today, /<ProjectionChart projection=\{projection\}/);
assert.match(loaderComponent, /aria-live="polite"/);
assert.match(loaderComponent, /aria-hidden="true"/);
assert.doesNotMatch(loader, /\.triangle-step-loader\s*\{[^}]*animation/s,
  'the wrapper must never rotate');
assert.doesNotMatch(loader, /rotate\(/);
assert.match(loader, /animation-duration:5\.2s/);
for (const keyframe of ['19.2308%', '25%', '44.2308%', '50%',
  '69.2308%', '75%', '94.2308%', '100%']) assert.ok(loader.includes(keyframe));
assert.match(loader, /prefers-reduced-motion:reduce/);
assert.match(loader, /animation:none/);

console.log('verified-snapshot.test: ok (cache, atomic swap, freshness, loader, Today UI)');
