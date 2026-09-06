import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright';
import {
  RELEASE_ENGINE_VERSION,
  evaluateBusinessSnapshotSet,
  evaluateInfrastructureReadiness,
  fetchBusinessSnapshots,
  loadSnapshotContract,
  triggerBusinessSnapshots,
  validateSnapshotContract,
} from './release-state-machine.mjs';
import { selectCanonical1321FiveDay } from './canonical-snapshot-selection.mjs';
import { startFixtureTarget } from './release-fixture-target.mjs';

const args = Object.fromEntries(process.argv.slice(2).reduce((rows, value, index, all) => {
  if (value.startsWith('--')) rows.push([value.slice(2), all[index + 1]]);
  return rows;
}, []));
const runNumber = Number(args.run ?? 0);
const outputPath = path.resolve(args.out ?? `../artifacts/full-release-simulation-${runNumber}.json`);
const candidateSha = process.env.ARGUS_SIM_EXPECTED_SHA ?? '';
const distDir = path.resolve(process.env.ARGUS_SIM_DIST ?? 'dist');
const backendPort = Number(process.env.ARGUS_SIM_BACKEND_PORT ?? 4199);
const frontendPort = Number(process.env.ARGUS_SIM_FRONTEND_PORT ?? 4173);
const adminToken = 'simulation-release-token';
const contract = validateSnapshotContract(loadSnapshotContract(
  new URL('../../release/v13-snapshot-readiness-contract.json', import.meta.url),
));

if (!Number.isInteger(runNumber) || runNumber < 1 || runNumber > 2) {
  throw new Error('full_release_simulation_run_must_be_1_or_2');
}
if (!/^[0-9a-f]{40}$/.test(candidateSha)) throw new Error('candidate_sha_invalid');
if (!fs.existsSync(path.join(distDir, 'index.html'))) throw new Error('candidate_dist_missing');

const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), `argus-v13-sim-${runNumber}-`));
const evidence = {
  schemaVersion: 'argus-v13-full-release-simulation-v1',
  engineVersion: RELEASE_ENGINE_VERSION,
  runNumber,
  status: 'failure',
  candidateSha,
  candidateDist: distDir,
  initial: { snapshotReady: 0, snapshotExpected: 12 },
  stateLog: [], consoleErrors: [],
};
let context;
let target;

try {
  target = await startFixtureTarget({
    distDir, backendPort, frontendPort, candidateSha, contract, adminToken,
    seedSalt: String(runNumber),
  });
  const { backendUrl, publicUrl, fixture } = target;
  const health = await (await fetch(`${backendUrl}/healthz`)).json();
  const ready = await (await fetch(`${backendUrl}/readyz`)).json();
  evidence.infrastructure = evaluateInfrastructureReadiness({
    backendHealth: health, backendReady: ready, expectedBuildSha: candidateSha,
    processStable: true, crashLoop: false, oomKilled: false, storageValid: true,
    restoreOutcome: 'test_mode', infraSnapshots: [],
  }, contract);
  assert.equal(evidence.infrastructure.pass, true);
  assert.equal(fixture.snapshots.size, 0, 'infrastructure must pass with business state at 0/12');

  const index = await (await fetch(publicUrl, { cache: 'no-store' })).text();
  assert.match(index, new RegExp(candidateSha));
  evidence.frontendDeploymentEquivalent = { status: 'pass', publicUrl };

  context = await chromium.launchPersistentContext(profileDir, { headless: true,
    viewport: { width: 1280, height: 900 }, serviceWorkers: 'allow' });
  let page = context.pages()[0] ?? await context.newPage();
  page.on('console', (message) => {
    if (message.type() === 'error') evidence.consoleErrors.push(message.text());
  });
  await page.goto(publicUrl, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await page.waitForFunction((sha) => globalThis.__ARGUS_BUILD_SHA__ === sha,
    candidateSha, { timeout: 30_000 });
  assert.equal(await page.evaluate(() => globalThis.__ARGUS_PRODUCT_VERSION__), 'v13.5.56');
  evidence.identitiesConverged = true;

  const producerTriggerId = `full-release-simulation-${runNumber}-${candidateSha.slice(0, 12)}`;
  evidence.trigger = await triggerBusinessSnapshots({
    baseUrl: backendUrl, adminToken, contract, expectedBuildSha: candidateSha,
    producerTriggerId,
  });
  assert.equal(fixture.snapshots.size, 12);

  const canonicalResult = await selectCanonical1321FiveDay(page, {
    timeout: 90_000,
    onTransition: (event) => {
      if (!event.detail?.assumed) evidence.stateLog.push(event);
    },
  });
  assert.equal(canonicalResult.responseSnapshotId, canonicalResult.uiSnapshotId);
  evidence.canonical = {
    responseSnapshotId: canonicalResult.responseSnapshotId,
    uiSnapshotId: canonicalResult.uiSnapshotId,
    instrument: '1321',
    horizon: '5D',
  };

  await page.waitForFunction(async () => {
    const registration = await navigator.serviceWorker.getRegistration();
    return !!registration?.active && !!navigator.serviceWorker.controller;
  }, null, { timeout: 30_000 });
  const runtimeProof = await page.evaluate(async () => {
    const databases = await indexedDB.databases();
    const registration = await navigator.serviceWorker.getRegistration();
    return {
      databaseNames: databases.map((row) => row.name).filter(Boolean),
      serviceWorkerScript: registration?.active?.scriptURL ?? null,
      frontendSha: globalThis.__ARGUS_BUILD_SHA__,
      productVersion: globalThis.__ARGUS_PRODUCT_VERSION__,
    };
  });
  assert.ok(runtimeProof.databaseNames.includes('argus-verified-snapshots'));
  assert.equal(runtimeProof.frontendSha, candidateSha);
  assert.match(runtimeProof.serviceWorkerScript, /sw\.js$/);
  evidence.warmProfileSeal = { status: 'pass', ...runtimeProof };

  await context.close(); context = null;
  context = await chromium.launchPersistentContext(profileDir, { headless: true,
    viewport: { width: 1280, height: 900 }, serviceWorkers: 'allow' });
  page = context.pages()[0] ?? await context.newPage();
  page.on('console', (message) => {
    if (message.type() === 'error') evidence.consoleErrors.push(message.text());
  });
  await page.goto(publicUrl, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await page.waitForFunction((snapshotId) => document.querySelector(
    '[data-argus-contract="canonical-market-snapshot-v1"]',
  )?.getAttribute('data-canonical-snapshot-id') === snapshotId,
  canonicalResult.responseSnapshotId, { timeout: 90_000 });
  evidence.independentProfileReopen = { status: 'pass' };

  const snapshots = await fetchBusinessSnapshots({ baseUrl: backendUrl, contract });
  evidence.businessSnapshots = evaluateBusinessSnapshotSet({
    contract, observed: snapshots, expectedBuildSha: candidateSha,
    producerTriggerId,
  });
  assert.equal(evidence.businessSnapshots.pass, true);
  assert.deepEqual(evidence.businessSnapshots.expectedSet,
    evidence.businessSnapshots.observedSet);

  const brand = await page.locator('.shell__brand').innerText();
  assert.match(brand, /A\.R\.G\.U\.S\.\s+Pro/);
  assert.match(brand, /A\.R\.G\.U\.S\.\s+Pro\s+v13\.5\.56/);
  for (const label of ['Today', 'Holdings / Watchlist', 'Notifications', 'Settings']) {
    assert.ok(await page.getByText(label, { exact: true }).count() > 0, label);
  }
  // v13.5.39: the owner's top command block renders MARKET SIGNALS x / 7 from
  // the real projection (a truthful '— / 7' placeholder until evidence loads);
  // this is the rendered DOM on the production route, not a source string.
  const topSignals = page.locator('[data-argus-contract="market-signals-top-v1"]').first();
  assert.ok(await topSignals.count() > 0, 'top MARKET SIGNALS block rendered');
  const topSignalsText = (await topSignals.innerText()).trim();
  assert.match(topSignalsText, /^(\d|—) \/ 7$/, `top MARKET SIGNALS count: ${topSignalsText}`);
  assert.ok((await page.locator('.at-seven summary small').first().innerText()).includes('MARKET SIGNALS'),
    'top block carries the owner-facing name');
  evidence.publicProductAcceptance = { status: 'pass', brand, topSignals: topSignalsText,
    surfaces: ['Today', 'Holdings / Watchlist', 'Notifications', 'Settings'] };
  await context.close(); context = null;

  // THE isomorphism gate: the exact production acceptance engine runs against
  // the candidate target before any production mutation is possible. Same
  // script file, same assertions, same gate inventory — only the target URL
  // and expected identity inputs differ from the post-deploy invocation.
  const frontendVersion = JSON.parse(
    fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version;
  const acceptanceOut = path.resolve(
    path.dirname(outputPath), `full-release-simulation-${runNumber}-mobile`);
  // The engine must run as an async child: the fixture servers live on this
  // process's event loop, so a synchronous spawn would deadlock the child's
  // page loads against its own target.
  const engine = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['scripts/mobile-today-acceptance.mjs'], {
      cwd: path.resolve(path.dirname(new URL(import.meta.url).pathname), '..'),
      env: {
        ...process.env,
        ARGUS_PUBLIC_URL: publicUrl,
        ARGUS_EXPECTED_VERSION: frontendVersion,
        ARGUS_EXPECTED_SHA: candidateSha,
        ARGUS_MOBILE_ACCEPTANCE_OUT: acceptanceOut,
      },
      stdio: 'inherit',
    });
    const guard = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error('mobile acceptance engine exceeded 20 minutes'));
    }, 20 * 60_000);
    child.once('error', (error) => { clearTimeout(guard); reject(error); });
    child.once('exit', (code) => { clearTimeout(guard); resolve({ status: code }); });
  });
  const acceptanceReport = JSON.parse(fs.readFileSync(
    path.join(acceptanceOut, 'acceptance.json'), 'utf8'));
  evidence.mobileAcceptance = {
    status: engine.status === 0 && acceptanceReport.verdict === 'PASS'
      ? 'pass' : 'failure',
    exitCode: engine.status,
    verdict: acceptanceReport.verdict,
    publicUrl: acceptanceReport.publicUrl,
    frontendSha: acceptanceReport.frontendSha,
    frontendVersion: acceptanceReport.frontendVersion,
    combinationCount: acceptanceReport.combinationCount,
    gateInventory: acceptanceReport.gateInventory,
    failures: acceptanceReport.failures,
  };
  assert.equal(engine.status, 0, 'mobile acceptance engine must pass against the candidate');
  assert.equal(acceptanceReport.verdict, 'PASS');
  assert.equal(acceptanceReport.frontendSha, candidateSha);
  assert.equal(acceptanceReport.frontendVersion, frontendVersion);
  assert.equal(acceptanceReport.combinationCount, 12);
  assert.deepEqual(acceptanceReport.failures, []);

  const invalidatingConsoleErrors = evidence.consoleErrors.filter((message) =>
    !/Failed to load resource.*(429|503)/i.test(message));
  assert.deepEqual(invalidatingConsoleErrors, []);
  evidence.status = 'pass';
  evidence.completedAt = new Date().toISOString();
} catch (error) {
  evidence.failure = { name: error.name, message: error.message, stack: error.stack };
  throw error;
} finally {
  if (context) await context.close().catch(() => {});
  if (target) await target.close().catch(() => {});
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(evidence, null, 2)}\n`);
  fs.rmSync(profileDir, { recursive: true, force: true });
}
