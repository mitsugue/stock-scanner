import assert from 'node:assert/strict';
import fs from 'node:fs';
import {
  BUSINESS_RECONCILIATION_DEADLINE_MS,
  BUSINESS_TRIGGER_TRANSPORT_TIMEOUT_MS,
  RELEASE_DEPENDENCIES,
  RELEASE_STATES,
  ReleaseStateMachine,
  evaluateBusinessSnapshotSet,
  evaluateFailureScenario,
  evaluateInfrastructureReadiness,
  finalizePublicAcceptance,
  loadSnapshotContract,
  seedStateMachine,
  snapshotIdentity,
  triggerBusinessSnapshots,
  validateSnapshotContract,
} from './release-state-machine.mjs';

const contract = validateSnapshotContract(loadSnapshotContract(
  new URL('../../release/v13-snapshot-readiness-contract.json', import.meta.url),
));
assert.equal(contract.snapshotExpected, 12);
assert.equal(new Set(contract.snapshots.map((row) => row.identity)).size, 12);
assert.ok(contract.snapshots.every((row) => row.requiredness === 'SEED_REQUIRED'));

assert.equal(RELEASE_STATES.length, 21);
assert.deepEqual(RELEASE_STATES, Array.from({ length: 21 }, (_, index) =>
  `R${index}_${[
    'SAFE_PRODUCTION', 'CANDIDATE_CONSTRUCTED', 'CANDIDATE_TESTED',
    'CANDIDATE_BROWSER_E2E_ACCEPTED', 'REQUIRED_CI_ACCEPTED', 'MAIN_MERGED',
    'BACKEND_DEPLOYING', 'BACKEND_INFRA_READY', 'FRONTEND_DEPLOYING',
    'FRONTEND_IDENTITY_CONVERGED', 'BACKEND_IDENTITY_CONVERGED',
    'PRODUCT_SELECTION_READY', '1321_SELECTED', '5D_SELECTED',
    'CANONICAL_REQUEST_OBSERVED', 'VERIFIED_SNAPSHOT_RECEIVED',
    'UI_SNAPSHOT_ID_MATCHED', 'WARM_PROFILE_SEALED',
    'BUSINESS_SNAPSHOT_SET_ACCEPTED', 'PUBLIC_PRODUCT_ACCEPTED', 'V13_LIVE',
  ][index]}`));
for (const [state, dependencies] of Object.entries(RELEASE_DEPENDENCIES)) {
  const stateIndex = RELEASE_STATES.indexOf(state);
  assert.ok(stateIndex >= 0);
  for (const dependency of dependencies) {
    assert.ok(RELEASE_STATES.indexOf(dependency) < stateIndex,
      `${state} must depend only on an earlier producer state`);
  }
}

const impossible = new ReleaseStateMachine();
assert.throws(() => impossible.transition('R1_CANDIDATE_CONSTRUCTED'),
  /release_state_dependency_missing/);
assert.throws(() => impossible.transition('R99_UNKNOWN'), /unknown_release_state/);
impossible.transition('R0_SAFE_PRODUCTION');
assert.throws(() => impossible.transition('R0_SAFE_PRODUCTION'), /duplicate_release_state/);

const complete = new ReleaseStateMachine();
for (const state of RELEASE_STATES) complete.transition(state);
assert.deepEqual(complete.log.map((event) => event.state), RELEASE_STATES);
assert.equal(complete.rollback('R19_PUBLIC_PRODUCT_ACCEPTED').state,
  'ROLLBACK_TO_R0_SAFE_PRODUCTION');

const seed = seedStateMachine();
for (const state of RELEASE_STATES.slice(11, 18)) seed.transition(state);
assert.deepEqual(seed.log.map((event) => event.state), RELEASE_STATES.slice(0, 18));

const buildSha = 'a'.repeat(40);
const triggerId = 'release-simulation-1';
const triggeredAt = '2026-08-18T00:00:00.000Z';
const generatedAt = '2026-08-18T00:00:01.000Z';
const observed = contract.snapshots.map((row, index) => ({
  schemaVersion: 'argus-verified-view-snapshot-v1',
  snapshotId: `vs-${String(index).padStart(32, '0')}`,
  kind: row.kind,
  market: row.market,
  instrument: row.instrument,
  horizon: row.horizon,
  datasetHash: `dataset-${index}`,
  payloadHash: `payload-${index}`,
  methodVersion: 'verified-chart-view-v1:test',
  asOf: triggeredAt,
  generatedAt,
  verifiedAt: generatedAt,
  quality: 'live',
  sourceStatus: { chart: 'complete' },
  verificationStatus: 'verified',
  releaseBinding: {
    expectedBuildSha: buildSha,
    producerTriggerId: triggerId,
    triggeredAt,
  },
  payload: { automaticAiCalls: 0 },
}));
assert.deepEqual(evaluateInfrastructureReadiness({
  backendHealth: { status: 'ok', buildSha },
  backendReady: { ready: true, buildSha },
  expectedBuildSha: buildSha,
  processStable: true,
  crashLoop: false,
  oomKilled: false,
  storageValid: true,
  restoreOutcome: 'test_mode',
  infraSnapshots: [],
}, contract), { pass: true, reason: 'accepted', expectedSet: [], observedSet: [] });
const exact = evaluateBusinessSnapshotSet({
  contract, observed, expectedBuildSha: buildSha, producerTriggerId: triggerId,
  now: Date.parse(generatedAt) + 1000,
});
assert.equal(exact.pass, true);
assert.deepEqual(exact.expectedSet, exact.observedSet);
assert.equal(evaluateBusinessSnapshotSet({
  contract, observed: [...observed, observed[0]], expectedBuildSha: buildSha,
  producerTriggerId: triggerId, now: Date.parse(generatedAt) + 1000,
}).reason, 'duplicate_snapshot');
assert.equal(evaluateBusinessSnapshotSet({
  contract, observed: observed.slice(1), expectedBuildSha: buildSha,
  producerTriggerId: triggerId, now: Date.parse(generatedAt) + 1000,
}).reason, 'snapshot_set_mismatch');
const wrongBuild = structuredClone(observed);
wrongBuild[0].releaseBinding.expectedBuildSha = 'b'.repeat(40);
assert.match(evaluateBusinessSnapshotSet({
  contract, observed: wrongBuild, expectedBuildSha: buildSha,
  producerTriggerId: triggerId, now: Date.parse(generatedAt) + 1000,
}).reason, /^wrong_build:/);
assert.equal(snapshotIdentity(observed[0]), contract.snapshots[0].identity);
// v13.5.66: cold-start margins (see the constants' comment for the measurement)
assert.equal(BUSINESS_TRIGGER_TRANSPORT_TIMEOUT_MS, 600_000);
assert.equal(BUSINESS_RECONCILIATION_DEADLINE_MS, 900_000);

const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});
const exactAcknowledgement = {
  schemaVersion: 'argus-release-snapshot-seed-v1',
  status: 'completed', producerTriggerId: triggerId, expectedBuildSha: buildSha,
  snapshotExpected: 12, snapshotReady: 12,
  persistence: { verified: true, readBackVerified: true },
};
const snapshotFetch = ({
  post = () => jsonResponse(exactAcknowledgement), snapshots = observed,
  onRead = null,
} = {}) => {
  const byIdentity = new Map(snapshots.map((row) => [snapshotIdentity(row), row]));
  let readCount = 0;
  return async (url, options = {}) => {
    if (options.method === 'POST') return post(url, options);
    readCount += 1;
    if (onRead) {
      const override = await onRead({ url, options, readCount });
      if (override) return override;
    }
    const parsed = new URL(url);
    const identity = `market-chart:${parsed.searchParams.get('symbol')}:` +
      parsed.searchParams.get('horizon');
    const snapshot = byIdentity.get(identity);
    return snapshot ? jsonResponse(snapshot) : jsonResponse({ error: 'not_found' }, 404);
  };
};
const triggerOptions = (fetchImpl, overrides = {}) => ({
  baseUrl: 'https://example.invalid', adminToken: 'unit-test-secret', contract,
  expectedBuildSha: buildSha, producerTriggerId: triggerId, fetchImpl,
  nowMs: () => Date.parse(generatedAt) + 1000,
  reconciliationDeadlineMs: 0,
  ...overrides,
});
const captureRejection = async (promise) => {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  assert.fail('expected promise to reject');
};

// 1. Normal 200 completion is accepted only after exact durable readback.
const normalTrigger = await triggerBusinessSnapshots(triggerOptions(snapshotFetch()));
assert.equal(normalTrigger.status, 'completed');
assert.equal(normalTrigger.completionMode, 'HTTP_ACKNOWLEDGED');
assert.equal(normalTrigger.reconciliation.outcome, 'COMPLETE');
assert.equal(normalTrigger.plan.length, 12);

// 2. A slow but acknowledged request remains valid inside the explicit observation boundary.
let slowClock = Date.parse(generatedAt) + 1000;
const slowTrigger = await triggerBusinessSnapshots(triggerOptions(snapshotFetch({
  post: () => {
    slowClock += 250_000;
    return jsonResponse(exactAcknowledgement);
  },
}), { nowMs: () => slowClock }));
assert.equal(slowTrigger.reconciliation.outcome, 'COMPLETE');

// 3. A client transport timeout is UNKNOWN until exact 12/12 durable readback recovers it.
const timedOutFetch = snapshotFetch({
  post: (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => {
      const error = new Error('explicit transport observation timeout');
      error.name = 'AbortError';
      reject(error);
    }, { once: true });
  }),
});
const recovered = await triggerBusinessSnapshots(triggerOptions(timedOutFetch, {
  transportTimeoutMs: 1,
}));
assert.equal(recovered.completionMode, 'RECOVERED_AFTER_TRANSPORT_FAILURE');
assert.equal(recovered.transport.phase, 'transport_timeout');
assert.equal(recovered.reconciliation.outcome, 'COMPLETE');

// 4. Timeout plus 11/12 cannot be accepted.
const incompleteError = await captureRejection(triggerBusinessSnapshots(triggerOptions(snapshotFetch({
  post: () => { throw new TypeError('fetch failed'); },
  snapshots: observed.slice(0, 11),
}))));
assert.equal(incompleteError.artifact.reconciliation.outcome, 'TIMEOUT');
assert.equal(incompleteError.artifact.reconciliation.lastObservation.outcome, 'INCOMPLETE');

// 5. Complete data for the wrong trigger fails closed.
const wrongTriggerSnapshots = structuredClone(observed);
for (const row of wrongTriggerSnapshots) row.releaseBinding.producerTriggerId = 'other-trigger';
const wrongTriggerError = await captureRejection(triggerBusinessSnapshots(triggerOptions(snapshotFetch({
  post: () => { throw new TypeError('fetch failed'); },
  snapshots: wrongTriggerSnapshots,
}))));
assert.equal(wrongTriggerError.artifact.reconciliation.lastObservation.outcome, 'WRONG_TRIGGER');

// 6. A mixed trigger/build set fails closed.
const mixedSnapshots = structuredClone(observed);
mixedSnapshots[0].releaseBinding.producerTriggerId = 'other-trigger';
const mixedError = await captureRejection(triggerBusinessSnapshots(triggerOptions(snapshotFetch({
  post: () => { throw new TypeError('fetch failed'); }, snapshots: mixedSnapshots,
}))));
assert.equal(mixedError.artifact.reconciliation.lastObservation.outcome, 'MIXED_IDENTITY');

// 7. Exact duplicate 409 is idempotent only when canonical 12/12 is complete.
const duplicate = await triggerBusinessSnapshots(triggerOptions(snapshotFetch({
  post: () => jsonResponse({
    status: 'duplicate', producerTriggerId: triggerId,
    snapshotExpected: 12, snapshotReady: 12,
  }, 409),
})));
assert.equal(duplicate.completionMode, 'IDEMPOTENT_EXISTING_RESULT');
assert.equal(duplicate.reconciliation.outcome, 'COMPLETE');
const duplicateIncompleteError = await captureRejection(triggerBusinessSnapshots(
  triggerOptions(snapshotFetch({
    post: () => jsonResponse({
      status: 'duplicate', producerTriggerId: triggerId,
      snapshotExpected: 12, snapshotReady: 11,
    }, 409),
    snapshots: observed.slice(0, 11),
  })),
));
assert.equal(duplicateIncompleteError.artifact.completionMode, 'IDEMPOTENT_EXISTING_RESULT');
assert.equal(duplicateIncompleteError.artifact.reconciliation.lastObservation.outcome,
  'INCOMPLETE');

// 8. A conflicting duplicate trigger is terminal and is not reconciled as success.
const conflictError = await captureRejection(triggerBusinessSnapshots(triggerOptions(snapshotFetch({
  post: () => jsonResponse({ status: 'duplicate', producerTriggerId: 'other-trigger' }, 409),
}))));
assert.equal(conflictError.artifact.outcome, 'WRONG_TRIGGER');

// 9. Nested transport causes survive as sanitized structured diagnostics.
const nested = new TypeError('fetch failed unit-test-secret');
nested.cause = Object.assign(new Error('headers timeout unit-test-secret'), {
  name: 'HeadersTimeoutError', code: 'UND_ERR_HEADERS_TIMEOUT',
});
const nestedRecovered = await triggerBusinessSnapshots(triggerOptions(snapshotFetch({
  post: () => { throw nested; },
})));
assert.equal(nestedRecovered.transport.cause.code, 'UND_ERR_HEADERS_TIMEOUT');
assert.equal(nestedRecovered.transport.cause.name, 'HeadersTimeoutError');
assert.doesNotMatch(JSON.stringify(nestedRecovered), /unit-test-secret/);

// 10. Backend unavailable before the route is reached remains a failed unknown state.
const unavailable = snapshotFetch({
  post: () => { throw Object.assign(new TypeError('fetch failed'), { code: 'ECONNREFUSED' }); },
  onRead: () => { throw Object.assign(new Error('connection refused'), { code: 'ECONNREFUSED' }); },
});
const unavailableError = await captureRejection(
  triggerBusinessSnapshots(triggerOptions(unavailable)),
);
assert.equal(unavailableError.artifact.reconciliation.lastObservation.outcome, 'INCOMPLETE');

// 11. A deterministic HTTP error is terminal even if old snapshots happen to exist.
const httpError = await captureRejection(triggerBusinessSnapshots(triggerOptions(snapshotFetch({
  post: () => jsonResponse({ status: 'error' }, 503),
}))));
assert.match(httpError.artifact.reason, /^deterministic_http_response:http_503/);

// 12. Semantic polling can converge before the bounded reconciliation deadline.
let pollClock = Date.parse(generatedAt) + 1000;
let pollRound = 0;
const pollingFetch = snapshotFetch({
  post: () => { throw new TypeError('fetch failed'); },
  onRead: ({ readCount }) => {
    if (readCount % 12 === 1) pollRound += 1;
    return pollRound < 2 ? jsonResponse({ status: 'pending' }, 404) : null;
  },
});
const polled = await triggerBusinessSnapshots(triggerOptions(pollingFetch, {
  nowMs: () => pollClock,
  reconciliationDeadlineMs: 10_000,
  reconciliationPollMs: 1_000,
  sleepImpl: async (milliseconds) => { pollClock += milliseconds; },
}));
assert.equal(polled.reconciliation.outcome, 'COMPLETE');
assert.equal(polled.reconciliation.attempts, 2);

// 13. A durable but unverified snapshot cannot satisfy the canonical gate.
const unverifiedSnapshots = structuredClone(observed);
unverifiedSnapshots[0].verificationStatus = 'pending';
const verificationError = await captureRejection(triggerBusinessSnapshots(triggerOptions(snapshotFetch({
  post: () => { throw new TypeError('fetch failed'); }, snapshots: unverifiedSnapshots,
}))));
assert.equal(verificationError.artifact.reconciliation.lastObservation.outcome,
  'VERIFICATION_FAILED');

// 14. Failure artifacts never contain the admin token or authorization material.
assert.doesNotMatch(JSON.stringify(incompleteError.artifact),
  /unit-test-secret|X-ARGUS-ADMIN-TOKEN|Bearer\s/i);
const finalized = finalizePublicAcceptance({
  businessArtifact: { status: 'pass', expectedSet: exact.expectedSet,
    observedSet: exact.observedSet },
  publicArtifact: { verdict: 'PASS', frontendSha: buildSha },
  mobileArtifact: { verdict: 'PASS', frontendSha: buildSha },
  expectedFrontendSha: buildSha,
});
assert.deepEqual(finalized.releaseStateLog.slice(-2).map((row) => row.state), [
  'R19_PUBLIC_PRODUCT_ACCEPTED', 'R20_V13_LIVE',
]);
assert.throws(() => finalizePublicAcceptance({
  businessArtifact: { status: 'pass', expectedSet: exact.expectedSet,
    observedSet: exact.observedSet.slice(1) },
  publicArtifact: { verdict: 'PASS', frontendSha: buildSha },
  mobileArtifact: { verdict: 'PASS', frontendSha: buildSha },
  expectedFrontendSha: buildSha,
}), /finalize_business_artifact_invalid/);

const accepted = {
  initialSnapshotReady: 0,
  infrastructureHealthy: true,
  frontendIdentity: 'candidate', backendIdentity: 'candidate', oldFrontend: false,
  frontendDeployedBeforeSeed: true,
  todayLoaded: true, selected1321: true, selected5D: true, httpStatuses: [200],
  verificationStatus: 'verified', responseSnapshotId: 'mts-a', uiSnapshotId: 'mts-a',
  serviceWorkerReady: true, serviceWorkerStale: false,
  indexedDbInitiallyEmpty: true, indexedDbReady: true, profileValid: true,
  identityStable: true, externalDataGatedAbsent: true,
};
const matrix = {
  A: [accepted, true, 'accepted'],
  B: [{ ...accepted, infrastructureHealthy: true }, true, 'accepted'],
  C: [{ ...accepted, infrastructureHealthy: false }, false, 'infrastructure'],
  D: [{ ...accepted, oldFrontend: true }, false, 'old_frontend'],
  E: [{ ...accepted, frontendIdentity: 'stale' }, false, 'frontend_identity'],
  F: [{ ...accepted, backendIdentity: 'stale' }, false, 'backend_identity'],
  G: [{ ...accepted, frontendDeployedBeforeSeed: true }, true, 'accepted'],
  H: [{ ...accepted, httpStatuses: [400] }, false, 'http_not_200'],
  I: [{ ...accepted, httpStatuses: [429, 200] }, true, 'accepted'],
  J: [{ ...accepted, httpStatuses: [429, 429, 429] }, false, 'rate_limit_exhausted'],
  K: [{ ...accepted, verificationStatus: 'unverified' }, false, 'not_verified'],
  L: [{ ...accepted, uiSnapshotId: 'mts-b' }, false, 'snapshot_mismatch'],
  M: [{ ...accepted, serviceWorkerStale: true }, false, 'service_worker'],
  N: [{ ...accepted, indexedDbInitiallyEmpty: true }, true, 'accepted'],
  O: [{ ...accepted, profileValid: false }, false, 'profile'],
  P: [{ ...accepted, wrongBuildSnapshot: true }, false, 'wrong_build_snapshot'],
  Q: [{ ...accepted, duplicateSnapshot: true }, false, 'duplicate_snapshot'],
  R: [{ ...accepted, missingSeedRequired: true }, false, 'missing_seed_required'],
  S: [{ ...accepted, externalDataGatedAbsent: true }, true, 'accepted'],
  T: [{ ...accepted, frontendIdentityChanged: true }, false, 'identity_changed'],
};
for (const [scenario, [input, pass, reason]] of Object.entries(matrix)) {
  assert.deepEqual(evaluateFailureScenario(input), { pass, reason }, scenario);
}

const selection = fs.readFileSync(
  new URL('./canonical-snapshot-selection.mjs', import.meta.url), 'utf8');
const requestRegistration = selection.indexOf('const requestPromise = page.waitForRequest');
const responseRegistration = selection.indexOf('const responsePromise = page.waitForResponse');
const select1321 = selection.indexOf('R12_1321_SELECTED');
const select5D = selection.indexOf('R13_5D_SELECTED');
const revalidationTrigger = selection.indexOf(
  "await page.reload({ waitUntil: 'domcontentloaded', timeout })",
);
const awaitRequestAndResponse = selection.indexOf(
  'return Promise.all([requestPromise, responsePromise])',
);
const retryLoop = selection.indexOf('for (let attempt = 1; attempt <= 3; attempt += 1)');
const awaitUi = selection.indexOf('await page.waitForFunction(({ selector, snapshotId })');
assert.ok(requestRegistration >= 0 && responseRegistration > requestRegistration);
assert.ok(select1321 >= 0 && select5D > select1321);
assert.ok(revalidationTrigger > responseRegistration
  && awaitRequestAndResponse > revalidationTrigger
  && retryLoop > select5D && awaitUi > retryLoop,
  'selection must trigger before waits and UI equality must follow the verified response');
assert.match(selection, /attempt <= 3/);
assert.match(selection, /observedResponse\.status\(\) !== 429/);

const acceptance = fs.readFileSync(
  new URL('./public-market-acceptance.mjs', import.meta.url), 'utf8');
const seedProbe = acceptance.slice(acceptance.indexOf("if (MODE === 'seed')"));
assert.ok(seedProbe.indexOf('selectCanonical1321FiveDay')
  < seedProbe.indexOf('probeProfileRuntime'));
assert.ok(seedProbe.indexOf('writeWarmProfileManifest')
  < seedProbe.indexOf("transition('R17_WARM_PROFILE_SEALED'"));

console.log('release-state-machine.test: ok (R0-R20, exact 12-set, failure matrix A-T)');

// ── v13.5.64: a live backend that already CONTAINS the candidate is accepted ──
{
  const { waitForInfrastructureReadiness, gitSuccessorAcceptor } = await import(
    new URL('./release-state-machine.mjs', import.meta.url).href);
  const candidate = 'ce330bf183d401f101936f6e281149deaef8887a';
  const successor = 'eb6f89ed7668cfdcc0417f3f7ad5a4fb4080f139';
  const responses = (sha) => async (url) => ({
    status: 200,
    json: async () => (url.endsWith('/healthz') ? { status: 'ok', buildSha: sha } : { ready: true, buildSha: sha }),
  });
  const accepted = await waitForInfrastructureReadiness({
    baseUrl: 'https://example.test', contract, expectedBuildSha: candidate,
    timeoutSeconds: 1, pollSeconds: 1, fetchImpl: responses(successor),
    acceptSuccessor: async (sha) => sha === successor,
  });
  assert.equal(accepted.pass, true);
  assert.equal(accepted.acceptedAsSuccessor, true);
  assert.equal(accepted.observedBuildSha, successor);
  assert.equal(accepted.expectedBuildSha, candidate);
  await assert.rejects(waitForInfrastructureReadiness({
    baseUrl: 'https://example.test', contract, expectedBuildSha: candidate,
    timeoutSeconds: 1, pollSeconds: 1, fetchImpl: responses(successor),
    acceptSuccessor: async () => false,
  }), /infrastructure_readiness_timeout:backend_identity/);
  await assert.rejects(waitForInfrastructureReadiness({
    baseUrl: 'https://example.test', contract, expectedBuildSha: candidate,
    timeoutSeconds: 1, pollSeconds: 1, fetchImpl: responses(successor),
  }), /infrastructure_readiness_timeout:backend_identity/);
  const calls = [];
  const acceptor = gitSuccessorAcceptor(candidate, 'main', (cmd, args) => { calls.push(args.join(' ')); });
  assert.equal(await acceptor(successor), true);
  assert.deepEqual(calls, ['fetch --quiet origin main',
    `merge-base --is-ancestor ${candidate} ${successor}`,
    `merge-base --is-ancestor ${successor} origin/main`]);
  assert.equal(await acceptor('not-a-sha'), false);
  const refusing = gitSuccessorAcceptor(candidate, 'main', () => { throw new Error('not ancestor'); });
  assert.equal(await refusing(successor), false);
  console.log('release-state-machine: successor acceptance ok');
}
