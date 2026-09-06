// Hermetic candidate target: serves the exact built candidate dist plus a
// fixture backend whose verified snapshots satisfy the product's strict
// verifier. This is the ONE local stand-in production target, shared by the
// full release simulation and any direct candidate-mode acceptance run, so the
// pre-mutation lane and the production lane exercise the same acceptance
// engine against isomorphic surfaces.
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { snapshotIdentity } from './release-state-machine.mjs';
import { backendChartMethodVersion, backendVerifiedViewMethodVersion }
  from './method-version-contract.mjs';

// The fixture must satisfy the SAME verifier the product runs, so it carries
// the backend's real method identities rather than copies that can go stale.
const CHART_METHOD_VERSION = backendChartMethodVersion();
const VERIFIED_VIEW_METHOD_VERSION = backendVerifiedViewMethodVersion();

const sorted = (value) => {
  if (Array.isArray(value)) return value.map(sorted);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, child]) => [key, sorted(child)]));
  }
  return value;
};
const canonical = (value) => JSON.stringify(sorted(value));
const sha256 = (value) => crypto.createHash('sha256').update(value).digest('hex');
const portableNumber = (value) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  const text = value.toFixed(8).replace(/0+$/, '').replace(/\.$/, '');
  return text === '-0' || text === '' ? '0' : text;
};

const payloadFor = (row, datasetHash, generatedAt) => {
  // Production-equivalent OHLCV depth: the Today projection contract requires
  // at least 20 filtered bars plus a finite positive atr14 before the
  // 'available' projection state is reachable, so the candidate target must
  // provide the same shape the live backend serves. Bars are deterministic
  // functions of their index — no randomness, replay-stable.
  const periodEnd = generatedAt.slice(0, 10);
  const endDate = new Date(`${periodEnd}T00:00:00Z`);
  const dates = [];
  for (let cursor = new Date(endDate); dates.length < 30;
    cursor.setUTCDate(cursor.getUTCDate() - 1)) {
    const day = cursor.getUTCDay();
    if (day !== 0 && day !== 6) dates.unshift(cursor.toISOString().slice(0, 10));
  }
  const bars = dates.map((date, index) => {
    const drift = Math.sin(index / 4) * 3 + index * 0.15;
    const open = 100 + drift;
    const close = open + Math.cos(index / 3);
    const high = Math.max(open, close) + 1.2;
    const low = Math.min(open, close) - 1.1;
    const round = (value) => Math.round(value * 100) / 100;
    return {
      date, open: round(open), high: round(high), low: round(low),
      close: round(close), volume: 1000 + index * 10, adjusted: false,
      availableFrom: date, ma: {}, bollinger: null, rsi14: null, macd: null,
      atr14: 2.3, sar: null,
      ichimoku: { conversion: null, base: null, spanA: null, spanB: null },
      volumeRatio20: null,
    };
  });
  return {
    schemaVersion: 'chart-intelligence-phase2-v1',
    methodVersion: CHART_METHOD_VERSION,
    reportId: `simulation-${row.instrument}`,
    symbol: row.instrument,
    market: row.market,
    displayNameJa: row.instrument,
    timeframe: 'daily',
    status: 'complete',
    missingReasons: [],
    source: row.market === 'JP' ? 'jquants' : 'twelvedata',
    asOf: generatedAt,
    periodEnd: generatedAt.slice(0, 10),
    automaticAiCalls: 0,
    costPolicyMode: 'automatic-ai-zero',
    instrumentMetadata: {
      instrumentId: `${row.market}:${row.instrument}:ETF`,
      symbol: row.instrument, market: row.market, assetType: 'ETF',
      displayNameJa: row.instrument, source: 'simulation-provider-cache',
      availableFrom: '2026-08-17', observedAt: generatedAt, revision: 1,
    },
    indicators: { status: 'complete', missingReasons: [], bars },
    zones: [], turningPoints: [], reactionAnomalies: [], relationshipBreaks: [],
    eventMarkers: [], valuationLevels: [], critique: [], scenarios: [],
    persistence: { stateHash: datasetHash, verificationStatus: 'verified',
      lastVerifiedReadBackAt: generatedAt },
    marketReplay: {
      cacheStatus: 'updated',
      contexts: Object.fromEntries(['1', '5', '20'].map((horizon) => [horizon, {
        datasetHash, asOf: generatedAt,
        methodVersion: 'market-context-replay-v3-pit-bound',
      }])),
    },
    relativeStrength: {}, rotationMap: [],
    todayIntelligence: {
      schemaVersion: 'today-intelligence-fixture-v1',
      symbol: row.instrument, market: row.market, asOf: generatedAt,
      automaticAiCalls: 0,
      calibration: {
        schemaVersion: 'calibration-fixture-v1',
        calibrationVersion: 'fixture-v1', methodVersion: 'fixture',
        historyStart: bars[0].date, historyEnd: bars[bars.length - 1].date,
        historyCount: bars.length,
        horizons: Object.fromEntries(['1', '5', '20'].map((horizon) => [horizon, {
          horizon: Number(horizon),
          directionProbabilities: null,
          referenceDirectionProbabilities: { UP: 40, RANGE: 30, DOWN: 30 },
          probabilities: null,
          returnDistribution: { q10: -0.03, q25: -0.015, median: 0.001,
            q75: 0.018, q90: 0.04 },
          probabilityEligibility: { eligible: false, reasonCodes: ['fixture'] },
          effectiveSampleCount: 100, episodeCount: 30, rawOccurrenceCount: 100,
          modelBrier: 0.66, baselineBrier: 0.65, brierSkill: -0.02,
          calibrationIntegrity: 'PASS', calibrationStatus: 'calibrated',
          calibrationDatasetHash: `fixture-${row.instrument}`,
          calibrationVersion: 'fixture-v1', signalFamily: 'baseline',
          expectedValue: 0.001,
        }])),
      },
      shortSelling: null, failedRally: null,
      historyCoverage: { start: bars[0].date, end: bars[bars.length - 1].date },
    },
    noteJa: 'simulation fixture',
  };
};

export const buildFixtureSnapshot = (row, releaseBinding, generatedAt, index, seedSalt) => {
  const datasetHash = sha256(`simulation-dataset:${row.instrument}:${seedSalt}`);
  const payload = payloadFor(row, datasetHash, generatedAt);
  const material = payload.indicators.bars.map((bar) => ({
    date: String(bar.date ?? ''), open: portableNumber(bar.open),
    high: portableNumber(bar.high), low: portableNumber(bar.low),
    close: portableNumber(bar.close), volume: portableNumber(bar.volume),
    availableFrom: String(bar.availableFrom ?? ''),
  }));
  const snapshot = {
    schemaVersion: 'argus-verified-view-snapshot-v1',
    snapshotId: '',
    kind: row.kind,
    instrument: row.instrument,
    horizon: row.horizon,
    datasetHash,
    payloadHash: sha256(canonical(material)),
    methodVersion: VERIFIED_VIEW_METHOD_VERSION,
    asOf: generatedAt,
    generatedAt,
    verifiedAt: generatedAt,
    quality: 'live',
    sourceStatus: { chart: 'complete', indicators: 'complete', replay: 'updated',
      durableReadBack: 'verified' },
    verificationStatus: 'verified',
    payload,
    releaseBinding,
  };
  const identity = {
    schemaVersion: snapshot.schemaVersion, kind: snapshot.kind,
    instrument: snapshot.instrument, horizon: snapshot.horizon,
    datasetHash: snapshot.datasetHash, payloadHash: snapshot.payloadHash,
    methodVersion: snapshot.methodVersion, asOf: snapshot.asOf,
    generatedAt: snapshot.generatedAt, verifiedAt: snapshot.verifiedAt,
    quality: snapshot.quality, sourceStatus: snapshot.sourceStatus,
    verificationStatus: snapshot.verificationStatus,
    releaseBinding: snapshot.releaseBinding,
  };
  snapshot.snapshotId = `vs-${sha256(canonical(identity)).slice(0, 32)}`;
  snapshot.simulationOrdinal = index;
  return snapshot;
};

const readBody = async (request) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
};
const json = (response, status, value, headers = {}) => {
  response.writeHead(status, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': '*',
    'Cache-Control': 'no-store',
    ...headers,
  });
  response.end(JSON.stringify(value));
};

export async function startFixtureTarget({
  distDir, backendPort, frontendPort, candidateSha, contract, adminToken,
  seedSalt = 'fixture',
}) {
  const backendUrl = `http://127.0.0.1:${backendPort}`;
  const publicUrl = `http://127.0.0.1:${frontendPort}/argus/`;
  const fixture = { snapshots: new Map(), trigger: null };

  const backendServer = http.createServer(async (request, response) => {
    if (request.method === 'OPTIONS') {
      response.writeHead(204, { 'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,OPTIONS' });
      response.end();
      return;
    }
    const url = new URL(request.url, backendUrl);
    if (url.pathname === '/healthz') {
      json(response, 200, { status: 'ok', backendVersion: '13.4.13', buildSha: candidateSha });
      return;
    }
    if (url.pathname === '/readyz') {
      json(response, 200, { status: 'ready', ready: true, reasonCode: 'READY',
        backendVersion: '13.4.13', buildSha: candidateSha });
      return;
    }
    if (url.pathname === '/api/argus/admin/missions/tick' && request.method === 'POST') {
      if (request.headers['x-argus-admin-token'] !== adminToken) {
        json(response, 401, { status: 'failed', error: 'unauthorized' }); return;
      }
      const body = await readBody(request);
      if (body.releaseSnapshotSeed !== true || body.expectedBuildSha !== candidateSha) {
        json(response, 400, { status: 'failed', error: 'invalid_release_seed' }); return;
      }
      const triggeredAt = new Date().toISOString();
      const generatedAt = new Date(Date.parse(triggeredAt) + 1).toISOString();
      const releaseBinding = { expectedBuildSha: candidateSha,
        producerTriggerId: body.runId, triggeredAt };
      fixture.snapshots.clear();
      contract.snapshots.forEach((row, index) => {
        fixture.snapshots.set(row.identity,
          buildFixtureSnapshot(row, releaseBinding, generatedAt, index, seedSalt));
      });
      fixture.trigger = releaseBinding;
      json(response, 200, {
        ok: true, status: 'completed', schemaVersion: 'argus-release-snapshot-seed-v1',
        producer: 'scanner._precompute_verified_market_view',
        producerTriggerId: body.runId, expectedBuildSha: candidateSha,
        triggeredAt, completedAt: generatedAt, snapshotExpected: 12, snapshotReady: 12,
        snapshots: [...fixture.snapshots.values()].map((snapshot) => ({
          identity: snapshotIdentity(snapshot), market: contract.snapshots.find(
            (row) => row.identity === snapshotIdentity(snapshot)).market,
          instrument: snapshot.instrument, horizon: snapshot.horizon,
          snapshotId: snapshot.snapshotId, generatedAt: snapshot.generatedAt,
          verificationStatus: snapshot.verificationStatus,
          releaseBinding: snapshot.releaseBinding,
        })),
        persistence: { verified: true, readBackVerified: true },
        recoveryAuthorityChanged: false,
      });
      return;
    }
    if (url.pathname === '/api/argus/today-headline') {
      // Mirror of argus_today_headline.build_today_headline over the fixture
      // snapshots — same shape the production backend serves.
      const symbols = ['1321', '1306', 'SPY', 'QQQ'];
      const instruments = Object.fromEntries(symbols.map((symbol) => {
        const snapshot = fixture.snapshots.get(`market-chart:${symbol}:5D`);
        if (!snapshot) {
          return [symbol, { status: 'unavailable', instrument: symbol,
            reason: 'verified_snapshot_missing' }];
        }
        const payload = snapshot.payload;
        const today = payload.todayIntelligence ?? {};
        return [symbol, {
          status: 'ready', instrument: symbol,
          market: ['1321', '1306'].includes(symbol) ? 'JP' : 'US',
          parentSnapshotId: snapshot.snapshotId,
          parentPayloadHash: snapshot.payloadHash,
          parentDatasetHash: snapshot.datasetHash,
          verificationStatus: 'verified',
          methodVersion: snapshot.methodVersion,
          quality: snapshot.quality,
          asOf: snapshot.asOf, generatedAt: snapshot.generatedAt,
          verifiedAt: snapshot.verifiedAt,
          displayNameJa: payload.displayNameJa,
          instrumentMetadata: payload.instrumentMetadata,
          periodEnd: payload.periodEnd, payloadStatus: payload.status,
          quoteState: payload.quoteState ?? 'CLOSE',
          marketCalendar: payload.marketCalendar ?? null,
          bars: (payload.indicators?.bars ?? []).slice(-31),
          zones: (payload.zones ?? []).filter((zone) =>
            ['active', 'reclaimed'].includes(zone.status)),
          turningPoints: (payload.turningPoints ?? []).filter((point) =>
            ['confirmed', 'candidate'].includes(point.status)).slice(-3),
          eventMarkers: (payload.eventMarkers ?? []).slice(-8),
          calibration: today.calibration ?? null,
          shortSelling: today.shortSelling ?? null,
          failedRally: today.failedRally ?? null,
          historyCoverage: today.historyCoverage ?? null,
          relativeStrengthSummary: null,
          automaticAiCalls: 0,
          headlineHash: 'fixture',
        }];
      }));
      const ready = Object.values(instruments)
        .filter((entry) => entry.status === 'ready');
      json(response, ready.length ? 200 : 503, {
        schemaVersion: 'argus-today-headline-v1',
        generatedAt: new Date().toISOString(),
        automaticAiCalls: 0,
        readyCount: ready.length, instrumentCount: symbols.length,
        headlineSetId: `th-fixture-${ready.length}`,
        instruments,
      }, { ETag: `"th-fixture-${ready.length}"` });
      return;
    }
    if (url.pathname === '/api/argus/chart-intelligence') {
      const identity = `market-chart:${url.searchParams.get('symbol')}:` +
        `${url.searchParams.get('horizon')}`;
      const snapshot = fixture.snapshots.get(identity);
      if (!snapshot) { json(response, 503, { status: 'not_ready', reason: 'verified_snapshot_missing' }); return; }
      json(response, 200, snapshot, {
        ETag: `"${snapshot.snapshotId}"`,
        'X-ARGUS-Compute-Mode': 'read-only',
        'X-ARGUS-Snapshot-Id': snapshot.snapshotId,
      });
      return;
    }
    // Support surfaces are deliberately data-gated in the hermetic simulation.
    // A non-200 response exercises the product's existing fail-closed hook
    // paths; a made-up 200 shape would be a false business-data fixture.
    json(response, 503, { status: 'DATA_GATED', error: 'simulation_data_gated',
      backendVersion: '13.4.13', buildSha: candidateSha, automaticAiCalls: 0 });
  });

  const mime = new Map([
    ['.html', 'text/html; charset=utf-8'], ['.js', 'text/javascript; charset=utf-8'],
    ['.css', 'text/css; charset=utf-8'], ['.json', 'application/json'],
    ['.svg', 'image/svg+xml'], ['.webmanifest', 'application/manifest+json'],
    ['.map', 'application/json'],
  ]);
  const frontendServer = http.createServer((request, response) => {
    const url = new URL(request.url, publicUrl);
    let relative = decodeURIComponent(url.pathname).replace(/^\/argus\/?/, '');
    if (!relative || !path.extname(relative)) relative = relative || 'index.html';
    let target = path.resolve(distDir, relative);
    if (!target.startsWith(`${distDir}${path.sep}`) && target !== path.join(distDir, 'index.html')) {
      response.writeHead(403); response.end(); return;
    }
    if (!fs.existsSync(target) || fs.statSync(target).isDirectory()) target = path.join(distDir, 'index.html');
    response.writeHead(200, { 'Content-Type': mime.get(path.extname(target)) ?? 'application/octet-stream',
      'Cache-Control': target.endsWith('index.html') ? 'no-store' : 'public, max-age=60' });
    fs.createReadStream(target).pipe(response);
  });

  const listen = (server, port) => new Promise((resolve, reject) => {
    server.once('error', reject); server.listen(port, '127.0.0.1', resolve);
  });
  const closeServer = (server) => new Promise((resolve) => server.close(resolve));
  await listen(backendServer, backendPort);
  await listen(frontendServer, frontendPort);

  return {
    backendUrl,
    publicUrl,
    fixture,
    close: async () => {
      await Promise.all([
        frontendServer.listening ? closeServer(frontendServer).catch(() => {}) : Promise.resolve(),
        backendServer.listening ? closeServer(backendServer).catch(() => {}) : Promise.resolve(),
      ]);
    },
  };
}
