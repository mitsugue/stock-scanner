import assert from 'node:assert/strict';
import fs from 'node:fs';

const today = fs.readFileSync(new URL('../src/components/today/ArgusTodayPanel.tsx', import.meta.url), 'utf8');
const command = fs.readFileSync(new URL('../src/routes/CommandCenter.tsx', import.meta.url), 'utf8');
const app = fs.readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const navigation = fs.readFileSync(new URL('../src/navigation.ts', import.meta.url), 'utf8');
const chartHook = fs.readFileSync(new URL('../src/hooks/useChartIntelligence.ts', import.meta.url), 'utf8');
const snapshot = fs.readFileSync(new URL('../src/lib/verifiedSnapshot.ts', import.meta.url), 'utf8');
const types = fs.readFileSync(new URL('../src/types/chartIntelligence.ts', import.meta.url), 'utf8');
const instruments = fs.readFileSync(new URL('../src/domain/marketInstruments.ts', import.meta.url), 'utf8');

for (const symbol of ['1321', '1306', 'SPY', 'QQQ']) {
  assert.match(instruments, new RegExp(symbol));
}
assert.match(instruments, /MarketHorizon = 1 \| 5 \| 20/);
assert.match(instruments, /MARKET_HORIZONS[^=]*= \[1, 5, 20\]/);
assert.match(today, /\(\[1, 5, 20\] as const\)\.map/);
assert.match(command, /horizon: chartHorizon/);
assert.match(chartHook, /method: 'GET', cache: 'no-store'/);
assert.doesNotMatch(chartHook, /method:\s*'POST'/);
assert.match(chartHook, /view\.key === expectedKey \? view\.snapshot : null/,
  'instrument switches fail closed instead of relabeling stale data');
assert.match(chartHook, /instrument_mismatch/);
assert.match(types, /marketReplay\?:/);
assert.match(types, /contexts: Record<string, MarketReplayContext>/);
assert.match(snapshot, /value\.marketReplay\?\.contexts\?\.\[horizon\]/,
  'verified historical replay context remains part of the background engine');
assert.match(types, /derivedMetricMigration/);

assert.match(command, /useChartIntelligence/);
assert.match(today, /ProjectionChart/);
assert.match(today, /data-snapshot-id=\{chartLoad\.snapshotId/);
assert.match(today, /data-argus-contract="canonical-market-snapshot-v1"/);
assert.match(today, /data-canonical-snapshot-id=\{chartLoad\.snapshotId/);
// v13.5.57: the contract names the DECISION SUBJECT (verified ETF), not the
// drawn series — since the headline draws the index, projection.symbol is N225.
assert.match(today, /data-canonical-instrument=\{selectedSymbol\}/);
assert.match(today, /data-canonical-horizon=\{`\$\{projection\?\.horizonDays \?\? horizon\}D`\}/);
assert.match(today, /effectiveSampleCount/);
assert.match(today, /<ProjectionChart projection=\{projection\}/);
assert.doesNotMatch(today, /onActivate=\{\(\) => (?:setDetail\(true\)|undefined)\}/);
assert.doesNotMatch(today, /argus\.replayContext|onNavigate\('regime'\)/);
assert.doesNotMatch(app + navigation, /MarketRegime|#market|'regime'/);
assert.equal(fs.existsSync(new URL('../src/components/marketReplay/MarketContextReplay.tsx', import.meta.url)), false);
assert.equal(fs.existsSync(new URL('../src/routes/MarketRegime.tsx', import.meta.url)), false);

console.log('market-replay.test: ok (engine preserved, standalone surface retired)');
