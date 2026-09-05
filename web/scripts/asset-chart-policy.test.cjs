#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const ts = require('typescript');

if (!global.crypto) global.crypto = require('crypto').webcrypto;

require.extensions['.ts'] = (module, filename) => {
  const source = fs.readFileSync(filename, 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
    fileName: filename,
  }).outputText;
  module._compile(output, filename);
};

const cache = require(path.join(__dirname, '..', 'src', 'lib', 'assetChartCache.ts'));
let failed = 0;

function check(name, condition) {
  if (condition) console.log(`  ok  ${name}`);
  else {
    failed += 1;
    console.error(`FAIL  ${name}`);
  }
}

async function main() {
  const identity = { market: 'jp', symbol: '5803', timeframe: 'daily' };
  check('C1 cache key includes market/symbol/timeframe/method version',
    cache.assetChartKey(identity)
      === `asset-chart:JP:5803:daily:${cache.ASSET_CHART_METHOD_VERSION}`);
  // v13.5.54: the pin was 'chart-intelligence-phase2-v1' while every payload
  // carried 'chart-intelligence-phase2-v2-pit-bound', so writeAssetChart
  // returned null on every chart and the cache never held a record.
  const backendChartMethod = fs.readFileSync(
    path.join(__dirname, '..', '..', 'argus_chart_intelligence.py'), 'utf8')
    .match(/^METHOD_VERSION\s*=\s*["']([^"']+)["']/m)?.[1];
  check('C1b asset-chart pin equals the backend payload methodVersion',
    !!backendChartMethod && cache.ASSET_CHART_METHOD_VERSION === backendChartMethod);

  const now = Date.parse('2026-07-26T00:00:00Z');
  check('C2 numeric Retry-After is respected',
    cache.parseRetryAfter('90', now) === now + 90_000);
  check('C3 HTTP-date Retry-After is respected',
    cache.parseRetryAfter('Sun, 26 Jul 2026 00:02:00 GMT', now) === now + 120_000);
  check('C4 Retry-After is capped at fifteen minutes',
    cache.parseRetryAfter('3600', now) === now + 15 * 60_000);
  check('C5 missing Retry-After uses bounded exponential backoff',
    cache.boundedRetryAt(0, now) === now + 30_000
      && cache.boundedRetryAt(4, now) === now + 300_000
      && cache.boundedRetryAt(99, now) === now + 300_000);

  const gate = new cache.AssetChartRequestGate();
  let active = 0;
  let maximum = 0;
  const order = [];
  const tasks = [1, 2, 3].map((id) => gate.enqueue(async () => {
    active += 1;
    maximum = Math.max(maximum, active);
    order.push(`start-${id}`);
    await new Promise((resolve) => setTimeout(resolve, 5));
    order.push(`end-${id}`);
    active -= 1;
    return id;
  }));
  await Promise.all(tasks);
  check('C6 asset chart requests are globally serialized',
    maximum === 1 && order.join(',') === 'start-1,end-1,start-2,end-2,start-3,end-3');

  const payload = {
    schemaVersion: 'argus-chart-intelligence-v1',
    methodVersion: cache.ASSET_CHART_METHOD_VERSION,
    asOf: '2026-07-25T06:00:00Z',
    symbol: '5803',
    market: 'JP',
    timeframe: 'daily',
    status: 'live',
    missingReasons: [],
    automaticAiCalls: 0,
    costPolicyMode: 'DETERMINISTIC',
    periodEnd: '2026-07-25',
    indicators: {
      bars: [{
        date: '2026-07-25', open: 100, high: 102, low: 99, close: 101,
        volume: 1000, adjusted: false, availableFrom: '2026-07-25',
        ma: {}, bollinger: null, rsi14: null, macd: null, atr14: null,
        sar: null,
        ichimoku: { conversion: null, base: null, spanA: null, spanB: null },
        volumeRatio20: null,
      }],
      status: 'live',
      missingReasons: [],
    },
    zones: [],
    turningPoints: [],
    reactionAnomalies: [],
    relationshipBreaks: [],
    eventMarkers: [],
    valuationLevels: [],
    critique: [],
    scenarios: [],
    persistence: {
      stateHash: 'fixture', verificationStatus: 'not_verified',
      lastVerifiedReadBackAt: null,
    },
    noteJa: '',
  };
  const written = await cache.writeAssetChart(identity, payload);
  const restored = await cache.readAssetChart(identity);
  check('C7 offline memory fallback preserves a verified payload',
    written?.payloadHash && restored?.payloadHash === written.payloadHash
      && restored?.payload.symbol === '5803');
  check('C8 mismatched instrument is never cached',
    await cache.writeAssetChart({ ...identity, symbol: '7203' }, payload) === null);
  const rateLimited = cache.assetChartUiTransition({
    state: 'NO_CACHE_LOADING', errorClass: null, retryAt: null,
  }, {
    type: 'failure', hasCache: false, errorClass: 'rate_limited',
    retryAt: now + 90_000,
  });
  const recovered = cache.assetChartUiTransition(rateLimited, { type: 'http_200' });
  check('C8b successful HTTP 200 atomically clears an old 429 state',
    rateLimited.state === 'RATE_LIMITED_WITHOUT_CACHE'
      && rateLimited.errorClass === 'rate_limited'
      && recovered.state === 'CURRENT_READY'
      && recovered.errorClass === null
      && recovered.retryAt === null);

  const hookSource = fs.readFileSync(path.join(__dirname, '..', 'src', 'hooks',
    'useChartIntelligence.ts'), 'utf8');
  const panelSource = fs.readFileSync(path.join(__dirname, '..', 'src', 'components',
    'chart', 'ChartIntelligencePanel.tsx'), 'utf8');
  const cardSource = fs.readFileSync(path.join(__dirname, '..', 'src', 'components',
    'assetDesk', 'AssetDecisionCard.tsx'), 'utf8');

  check('C9 closed and Overview states do not mount the chart',
    cardSource.indexOf('<ChartIntelligencePanel') > cardSource.indexOf("tab === 'chart'"));
  check('C10 inflight dedupe and race sequence guards remain active',
    hookSource.includes('legacyInflight.get(url)')
      && hookSource.includes('requestSequence !== sequence.current'));
  check('C10b timeout classification reads AbortSignal, not browser error shape',
    hookSource.includes('controller.signal.aborted')
      && hookSource.includes("controller.signal.reason === 'timeout'"));
  check('C11 persistent cache is restored before legacy network revalidation',
    hookSource.indexOf('readAssetChart(identity)')
      < hookSource.indexOf('loadLegacy(legacyUrl, options.symbol)'));
  check('C12 visibility resume is conditional rather than unconditional',
    hookSource.includes('visibilityBlocked.current')
      && !hookSource.includes("if (document.visibilityState === 'visible') setRefreshToken"));
  check('C13 Retry-After and seven exclusive chart states are wired',
    hookSource.includes("response.headers.get('Retry-After')")
      && [
        'CACHE_READY_REVALIDATING', 'NO_CACHE_LOADING', 'CURRENT_READY',
        'RATE_LIMITED_WITH_CACHE', 'RATE_LIMITED_WITHOUT_CACHE',
        'ERROR_WITH_CACHE', 'ERROR_WITHOUT_CACHE',
      ].every((state) => hookSource.includes(state)));
  check('C13a hook uses the exclusive success transition after HTTP 200',
    hookSource.includes("type: 'http_200'")
      && hookSource.includes('setLegacyError(null)')
      && hookSource.includes('setLegacyRetryAt(ready.retryAt)'));
  check('C13b expected skip is not mislabeled as an instrument mismatch',
    hookSource.indexOf("data.status === 'expected_skip'")
      < hookSource.indexOf('!matchesInstrument(data, expectedSymbol)')
      && panelSource.includes('チャートは次回更新待ち'));
  check('C14 loader and error rendering are mutually exclusive',
    panelSource.includes("snapshotState === 'NO_CACHE_LOADING'")
      && panelSource.includes('!data && (limited || failed)')
      && !panelSource.includes('初回データを準備中\\n取得失敗'));
  check('C15 retry button is disabled until the retry deadline',
    panelSource.includes('disabled={retrySeconds > 0}')
      && panelSource.includes("retrySeconds > 0 ? '待機中' : '再試行'"));
  check('C16 individual chart flow performs no AI POST',
    !hookSource.includes("method: 'POST'") && !panelSource.includes("method: 'POST'"));

  if (failed) {
    console.error(`\nasset chart policy tests: ${failed} FAILED`);
    process.exit(1);
  }
  console.log('\nasset chart policy tests: all passed');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
