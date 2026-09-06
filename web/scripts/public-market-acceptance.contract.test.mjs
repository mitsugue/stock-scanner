import assert from 'node:assert/strict';
import fs from 'node:fs';

const script = fs.readFileSync(
  new URL('./public-market-acceptance.mjs', import.meta.url), 'utf8');
const workflow = fs.readFileSync(
  new URL('../../.github/workflows/deploy-pages.yml', import.meta.url), 'utf8');
const manualWorkflow = fs.readFileSync(
  new URL('../../.github/workflows/market-public-acceptance.yml', import.meta.url), 'utf8');
const seedAction = fs.readFileSync(
  new URL('../../.github/actions/warm-profile-seed/action.yml', import.meta.url), 'utf8');
const consumerAction = fs.readFileSync(
  new URL('../../.github/actions/warm-profile-consumer/action.yml', import.meta.url), 'utf8');
const fullReleaseSimulation = fs.readFileSync(
  new URL('./full-release-simulation.mjs', import.meta.url), 'utf8');
const releaseCertificate = fs.readFileSync(
  new URL('../../scripts/v13_5_release_certificate.py', import.meta.url), 'utf8');
const seedRunner = fs.readFileSync(
  new URL('./run-warm-profile-seed.sh', import.meta.url), 'utf8');
const vite = fs.readFileSync(new URL('../vite.config.ts', import.meta.url), 'utf8');
const app = fs.readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const navigation = fs.readFileSync(
  new URL('../src/navigation.ts', import.meta.url), 'utf8');
const mobileAcceptance = fs.readFileSync(
  new URL('./mobile-today-acceptance.mjs', import.meta.url), 'utf8');
const canonicalSelection = fs.readFileSync(
  new URL('./canonical-snapshot-selection.mjs', import.meta.url), 'utf8');
const chartHook = fs.readFileSync(
  new URL('../src/hooks/useChartIntelligence.ts', import.meta.url), 'utf8');
const today = fs.readFileSync(
  new URL('../src/components/today/ArgusTodayPanel.tsx', import.meta.url), 'utf8');

for (const viewport of ['1440', '1280', '1024', '430', '390']) {
  assert.match(script, new RegExp(`width: ${viewport}`));
}
for (const value of ['1321', '1306', 'SPY', 'QQQ', '1D', '5D', '20D']) {
  assert.match(script, new RegExp(`'${value}'`));
}
for (const artifact of ['screenshots', 'acceptance.json', 'console.json',
  'network.json', 'diagnostics.json', 'computed-styles.json', 'version.json']) {
  assert.match(script + workflow, new RegExp(artifact.replace('.', '\\.')));
}
for (const field of ['frontendVersion', 'frontendSha', 'backendVersion', 'backendSha',
  'datasetHash', 'responseSnapshotId', 'blackFallbackCount',
  'horizontalOverflow', 'aiPostCount']) {
  assert.match(script, new RegExp(field));
}

assert.match(script, /#today/);
assert.doesNotMatch(script, /#market|Market Context|\.market-replay|\.mr-/);
assert.match(script, /TODAY_URL/);
for (const source of [script + canonicalSelection, mobileAcceptance + canonicalSelection]) {
  assert.match(source, /canonical-market-snapshot-v1/);
  assert.match(source, /data-canonical-verification=\"verified\"/);
  assert.match(source, /data-canonical-snapshot-id/);
  assert.doesNotMatch(source, /\.at-chart-status\[data-snapshot-id\]/,
    'acceptance must not wait on the collapsed diagnostic panel');
}
assert.match(today, /data-argus-contract="canonical-market-snapshot-v1"/);
assert.match(today, /data-canonical-snapshot-id=\{chartLoad\.snapshotId \?\? undefined\}/);
assert.match(today,
  /chartLoad\.responseSnapshotId === chartLoad\.snapshotId/,
  'response identity is published only with the exact rendered snapshot');
assert.match(today,
  /data-canonical-response-snapshot-id=\{coherentResponseSnapshotId \?\? undefined\}/);
assert.match(today,
  /data-canonical-response-verification=\{coherentResponseSnapshotId \? 'verified' : 'unverified'\}/);
assert.match(today, /data-canonical-snapshot-state=\{chartLoad\.snapshotState\}/);
assert.match(today, /data-canonical-verification=\{chartLoad\.snapshotId \? 'verified' : 'unverified'\}/);
// v13.5.57: the contract names the DECISION SUBJECT (verified ETF), not the
// drawn series — since the headline draws the index, projection.symbol is N225.
assert.match(today, /data-canonical-instrument=\{selectedSymbol\}/);
assert.match(today, /data-canonical-horizon=\{`\$\{projection\?\.horizonDays \?\? horizon\}D`\}/);
assert.match(script, /\.at-projection/);
for (const source of [script + canonicalSelection, mobileAcceptance + canonicalSelection]) {
  assert.match(source, /getByText\('根拠・市場データ・システム情報', \{ exact: true \}\)\.click\(\)/,
    'acceptance must deliberately open the collapsed evidence disclosure before chart interaction');
  assert.match(source, /details\.at-evidence/);
  assert.match(source, /\.open === true/,
    'acceptance must verify that the disclosure is actually open before chart interaction');
}
for (const source of [script, mobileAcceptance, canonicalSelection]) {
  assert.match(source, /data-argus-control/);
}
assert.match(canonicalSelection, /waitForRequest/);
assert.match(canonicalSelection, /waitForResponse/);
assert.match(canonicalSelection, /product_verified_response_contract/);
assert.match(canonicalSelection, /data-canonical-response-snapshot-id/,
  'service-worker body eviction must fall back to the verified product response contract');
assert.match(canonicalSelection,
  /selector: CANONICAL_RESPONSE_SELECTOR \}, \{ timeout \}/,
  'the verified response result must retain the shared bounded request/result budget');
assert.doesNotMatch(canonicalSelection, /Math\.min\(timeout, 5_000\)/,
  'the response-header event must not impose a shorter body-verification deadline');
assert.doesNotMatch(canonicalSelection, /response\.json\(\)/,
  'all environments must consume the same product-verified scalar response contract');
assert.match(chartHook, /setVerifiedResponseSnapshotId\(network\.snapshot\.snapshotId\)/);
assert.match(chartHook, /VERIFIED_REQUEST_TIMEOUT_MS = 75_000/,
  'the verified body producer must retain a bounded multi-megabyte verification budget');
assert.match(canonicalSelection, /CANONICAL_RESULT_TIMEOUT_MS = 90_000/,
  'the result consumer must outlive the product verified-response producer');
assert.match(canonicalSelection, /timeout = CANONICAL_RESULT_TIMEOUT_MS/);
assert.ok(chartHook.indexOf('if (!validation.ok)')
  < chartHook.lastIndexOf('setVerifiedResponseSnapshotId(network.snapshot.snapshotId)'),
  'the scalar response ID must be exposed only by the verified product path');
assert.doesNotMatch(canonicalSelection, /globalThis\.fetch\s*=/,
  'acceptance must not replace the production fetch implementation');
assert.doesNotMatch(canonicalSelection, /page\.request\.(?:get|fetch)/,
  'response recovery must not replace the observed UI request with a test-only request');
assert.match(canonicalSelection, /R12_1321_SELECTED/);
assert.match(canonicalSelection, /R13_5D_SELECTED/);
assert.match(script, /fillPaintTags\.has\(tag\)/,
  'visual acceptance must ignore default fill values on non-fillable SVG containers');
assert.match(script, /strokePaintTags\.has\(tag\)/,
  'visual acceptance must inspect only elements whose stroke can actually paint');
assert.match(script, /DATA_TIMEOUT_MS = 5_000/);
assert.match(script, /BACKEND_READY_TIMEOUT_MS = 8 \* 60_000/);
assert.match(script, /MARKET_CACHE_READY_TIMEOUT_MS = 30 \* 60_000/);
assert.match(script, /waitForMarketCache\(page\.request\)/);
assert.match(script, /market cache did not become ready/);
assert.match(script, /view\.automaticAiCalls \?\? 0/);
assert.match(script, /const view = body\.payload \|\| body/);
assert.match(script, /marketReplay\?\.contexts/);
assert.match(script, /snapshot: 'verified'/);
assert.match(script, /scope: 'market'/);
assert.match(script, /horizon: HORIZONS\[1\]/,
  'warm-profile acceptance must use the canonical 5D horizon');
assert.doesNotMatch(script, /horizon:\s*['"]5['"]/,
  'canonical verified-snapshot acceptance must never request legacy horizon=5');
assert.match(script, /launchPersistentContext\(PROFILE_DIR/,
  'the producer and consumer must use the transferred persistent profile');
assert.match(script, /stabilizeWarmProfileRuntime/);
assert.match(script, /runtime-reload/);
assert.match(script, /serviceWorkerReady/);
assert.match(script, /writeWarmProfileManifest/);
assert.match(script, /validateWarmProfile/);
assert.match(script, /todayProductStatus/);
assert.match(script, /page\.screenshot\(\{/);
assert.match(script, /fullPage: false/);
assert.match(script, /animations: 'disabled'/);
assert.match(script, /timeout: 10_000/);
assert.match(script, /process\.exitCode = 1/);
assert.doesNotMatch(script, /localStorage\./,
  'acceptance must not read protected owner data');

assert.match(workflow, /uses: \.\/\.github\/actions\/warm-profile-seed/);
assert.match(workflow, /uses: \.\/\.github\/actions\/warm-profile-consumer/);
assert.match(manualWorkflow, /uses: \.\/\.github\/actions\/warm-profile-consumer/);
assert.match(manualWorkflow, /name: zero-install-runtime-proof-1/);
assert.match(manualWorkflow, /name: zero-install-runtime-proof-2/);
assert.match(manualWorkflow, /node scripts\/full-release-simulation\.mjs --run 1/);
assert.match(manualWorkflow, /node scripts\/full-release-simulation\.mjs --run 2/);
// One acceptance authority: the pre-merge simulation must execute the exact
// production acceptance engine against the candidate target, and no admission
// certificate may exist without that engine's terminal PASS.
assert.match(fullReleaseSimulation,
  /spawn\(process\.execPath, \['scripts\/mobile-today-acceptance\.mjs'\]/);
assert.match(fullReleaseSimulation, /from '\.\/release-fixture-target\.mjs'/);
assert.match(fullReleaseSimulation, /ARGUS_PUBLIC_URL: publicUrl/);
assert.match(releaseCertificate,
  /"mobileAcceptance", \{\}\)\.get\("status"\) == "pass"/);
assert.match(releaseCertificate,
  /"mobileAcceptance", \{\}\)\.get\("verdict"\) == "PASS"/);
assert.match(seedAction, /bash scripts\/run-warm-profile-seed\.sh/);
assert.match(seedRunner, /node scripts\/public-market-acceptance\.mjs/);
assert.match(consumerAction, /node scripts\/public-market-acceptance\.mjs/);
assert.match(workflow, /node scripts\/mobile-today-acceptance\.mjs/);
assert.match(workflow, /market-public-acceptance-/);
assert.match(workflow, /release-state-machine\.mjs infrastructure/);
assert.match(workflow, /release-state-machine\.mjs trigger-business/);
assert.match(workflow, /release-state-machine\.mjs verify-business/);
assert.match(workflow,
  /--expected-backend-sha '\$\{\{ needs\.scope\.outputs\.backend_sha \}\}'/);
assert.doesNotMatch(workflow,
  /ARGUS_EXPECTED_BACKEND_SHA: \$\{\{ github\.sha \}\}/);
for (const input of ['pages_run_id', 'frontend_sha', 'backend_sha']) {
  assert.match(manualWorkflow, new RegExp(`${input}:`));
}
assert.match(manualWorkflow, /expected-backend-sha: \$\{\{ inputs\.backend_sha \}\}/);
assert.match(seedRunner, /warm-profile-contract\.mjs sanitize-validate/);
assert.match(consumerAction, /warm-profile-contract\.mjs validate/);
assert.match(workflow, /candidate-sha: \$\{\{ github\.sha \}\}/);
assert.doesNotMatch(manualWorkflow, /warm-profile-handoff:/);
assert.doesNotMatch(manualWorkflow, /warm-profile-seed-2:/);
assert.match(workflow, /needs: \[build, backend-infrastructure-readiness, acceptance-runtime-admission\]/);
assert.match(workflow, /candidate-identity:/);
assert.match(workflow, /verify_public_candidate_release\.py/);
assert.match(workflow, /needs: \[scope, deploy, backend-infrastructure-readiness\]/);
assert.match(workflow, /needs: \[scope, candidate-identity, business-snapshot-trigger, acceptance-runtime-admission\]/);
assert.match(workflow,
  /needs: \[scope, deploy, candidate-identity, seed-warm-profile, business-snapshot-acceptance, backend-infrastructure-readiness, acceptance-runtime-admission\]/);
assert.match(workflow, /enforce-public-candidate-identity: 'true'/);
assert.match(workflow,
  /expected-backend-sha: \$\{\{ needs\.candidate-identity\.outputs\.backend_sha \}\}/);
assert.doesNotMatch(workflow,
  /needs: \[build, seed-warm-profile, backend-infrastructure-readiness\]/);
assert.match(script, /candidate_frontend_not_live/);
assert.match(script, /candidate_backend_not_live/);
assert.match(script, /ARGUS_REQUIRE_LIVE_CANDIDATE/);
assert.match(seedAction, /continue-on-error: true/);
assert.match(seedAction, /Publish bounded seed evidence/);

assert.match(vite, /cleanupOutdatedCaches: true/);
assert.match(vite, /clientsClaim: true/);
assert.match(vite, /skipWaiting: true/);
assert.match(vite, /chart-intelligence[\s\S]+handler: 'NetworkOnly'/);
assert.match(app, /parseLocationHash/);
assert.match(app, /history\.pushState/);
assert.match(navigation, /export const HASH_ROUTES/);
assert.match(navigation, /export function assetDetailHash/);
assert.doesNotMatch(navigation, /#market|'regime'/);

assert.match(mobileAcceptance, /TODAY_URL/);
assert.match(mobileAcceptance, /rate-limit-cache-backoff-contract/);
assert.match(mobileAcceptance, /offline-snapshot-continuity/);
assert.match(mobileAcceptance, /responseTasks:\s*new Set\(\)/);
assert.match(mobileAcceptance, /Retry-After/);
assert.match(mobileAcceptance, /COMBINATION_PACE_MS = 1_000/);

console.log('public-market-acceptance.contract.test: ok (canonical Today evidence)');
