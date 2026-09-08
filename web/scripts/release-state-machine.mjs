import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';

export const RELEASE_ENGINE_VERSION = 'argus-v13-release-engine-v2';
export const SNAPSHOT_CONTRACT_SCHEMA = 'argus-v13-snapshot-readiness-contract-v1';
export const BUSINESS_TRIGGER_TRANSPORT_TIMEOUT_MS = 270_000;
export const BUSINESS_RECONCILIATION_DEADLINE_MS = 420_000;
export const BUSINESS_RECONCILIATION_POLL_MS = 5_000;
export const BUSINESS_READBACK_REQUEST_TIMEOUT_MS = 10_000;
export const BUSINESS_RECONCILIATION_OUTCOMES = Object.freeze([
  'COMPLETE', 'INCOMPLETE', 'WRONG_TRIGGER', 'WRONG_BUILD', 'MIXED_IDENTITY',
  'STALE', 'VERIFICATION_FAILED', 'TIMEOUT', 'UNKNOWN',
]);
export const SNAPSHOT_CLASSIFICATIONS = Object.freeze([
  'INFRA_REQUIRED', 'SEED_REQUIRED', 'EXTERNAL_DATA_GATED', 'OPTIONAL',
]);

export const RELEASE_STATES = Object.freeze([
  'R0_SAFE_PRODUCTION',
  'R1_CANDIDATE_CONSTRUCTED',
  'R2_CANDIDATE_TESTED',
  'R3_CANDIDATE_BROWSER_E2E_ACCEPTED',
  'R4_REQUIRED_CI_ACCEPTED',
  'R5_MAIN_MERGED',
  'R6_BACKEND_DEPLOYING',
  'R7_BACKEND_INFRA_READY',
  'R8_FRONTEND_DEPLOYING',
  'R9_FRONTEND_IDENTITY_CONVERGED',
  'R10_BACKEND_IDENTITY_CONVERGED',
  'R11_PRODUCT_SELECTION_READY',
  'R12_1321_SELECTED',
  'R13_5D_SELECTED',
  'R14_CANONICAL_REQUEST_OBSERVED',
  'R15_VERIFIED_SNAPSHOT_RECEIVED',
  'R16_UI_SNAPSHOT_ID_MATCHED',
  'R17_WARM_PROFILE_SEALED',
  'R18_BUSINESS_SNAPSHOT_SET_ACCEPTED',
  'R19_PUBLIC_PRODUCT_ACCEPTED',
  'R20_V13_LIVE',
]);

export const RELEASE_DEPENDENCIES = Object.freeze(Object.fromEntries(
  RELEASE_STATES.map((state, index) => [state, index === 0 ? [] : [RELEASE_STATES[index - 1]]]),
));

export const ROLLBACK_TRANSITIONS = Object.freeze(Object.fromEntries(
  RELEASE_STATES.slice(5, 20).map((state) => [state, 'R0_SAFE_PRODUCTION']),
));

const stateSet = new Set(RELEASE_STATES);
const REQUIRED_SNAPSHOT_FIELDS = Object.freeze([
  'identity', 'kind', 'market', 'instrument', 'horizon', 'producer',
  'triggeringEvent', 'requiredUpstreamEvidence', 'storageDestination',
  'freshnessPolicy', 'buildReleaseBinding', 'earliestValidReleaseState',
  'consumer', 'requiredness',
]);

const shaMatches = (expected, actual) => {
  const left = String(expected ?? '').trim().toLowerCase();
  const right = String(actual ?? '').trim().toLowerCase();
  return !!left && !!right && (left.startsWith(right) || right.startsWith(left));
};

export function snapshotIdentity(value) {
  return `${String(value.kind ?? '').toLowerCase()}:` +
    `${String(value.instrument ?? '').toUpperCase()}:` +
    `${String(value.horizon ?? '').toUpperCase()}`;
}

export function loadSnapshotContract(contractPath) {
  return JSON.parse(fs.readFileSync(contractPath, 'utf8'));
}

export function validateSnapshotContract(contract) {
  if (!contract || typeof contract !== 'object') throw new Error('snapshot_contract_malformed');
  if (contract.schemaVersion !== SNAPSHOT_CONTRACT_SCHEMA) {
    throw new Error('snapshot_contract_schema');
  }
  if (!Array.isArray(contract.snapshots)
      || contract.snapshotExpected !== 12
      || contract.snapshots.length !== contract.snapshotExpected) {
    throw new Error('snapshot_contract_expected_12');
  }
  if (JSON.stringify(contract.requirednessClassifications)
      !== JSON.stringify(SNAPSHOT_CLASSIFICATIONS)) {
    throw new Error('snapshot_contract_classifications');
  }
  if (contract.releaseTrigger?.busyOrDuplicateIsSuccess !== false
      || contract.releaseTrigger?.successStatus !== 'completed') {
    throw new Error('snapshot_contract_trigger_acknowledgement');
  }
  const identities = new Set();
  for (const [index, row] of contract.snapshots.entries()) {
    if (!row || typeof row !== 'object'
        || REQUIRED_SNAPSHOT_FIELDS.some((field) => row[field] == null)) {
      throw new Error(`snapshot_contract_row_${index}_schema`);
    }
    const identity = snapshotIdentity(row);
    if (row.identity !== identity) throw new Error(`snapshot_contract_row_${index}_identity`);
    if (identities.has(identity)) throw new Error(`snapshot_contract_duplicate:${identity}`);
    identities.add(identity);
    if (!SNAPSHOT_CLASSIFICATIONS.includes(row.requiredness)) {
      throw new Error(`snapshot_contract_row_${index}_classification`);
    }
    if (row.earliestValidReleaseState !== 'R18_BUSINESS_SNAPSHOT_SET_ACCEPTED') {
      throw new Error(`snapshot_contract_row_${index}_earliest_state`);
    }
    if (row.producer.module !== 'scanner.py'
        || row.producer.function !== '_precompute_verified_market_view') {
      throw new Error(`snapshot_contract_row_${index}_producer`);
    }
  }
  return contract;
}

export function requiredSnapshotRows(contract, classification) {
  validateSnapshotContract(contract);
  return contract.snapshots.filter((row) => row.requiredness === classification);
}

export class ReleaseStateMachine {
  constructor({ assumed = [], onTransition = () => {} } = {}) {
    this.reached = new Set();
    this.log = [];
    this.onTransition = onTransition;
    for (const state of assumed) this.transition(state, { assumed: true });
  }

  transition(state, detail = null) {
    if (!stateSet.has(state)) throw new Error(`unknown_release_state:${state}`);
    if (this.reached.has(state)) throw new Error(`duplicate_release_state:${state}`);
    const missing = RELEASE_DEPENDENCIES[state]
      .filter((dependency) => !this.reached.has(dependency));
    if (missing.length) {
      throw new Error(`release_state_dependency_missing:${state}:${missing.join(',')}`);
    }
    const event = { index: this.log.length + 1, state, detail };
    this.reached.add(state);
    this.log.push(event);
    this.onTransition(event);
    return event;
  }

  rollback(fromState, detail = null) {
    if (!this.reached.has(fromState) || !ROLLBACK_TRANSITIONS[fromState]) {
      throw new Error(`release_rollback_not_allowed:${fromState}`);
    }
    const event = {
      index: this.log.length + 1,
      state: 'ROLLBACK_TO_R0_SAFE_PRODUCTION',
      fromState,
      detail,
    };
    this.log.push(event);
    this.onTransition(event);
    return event;
  }
}

// Browser acceptance starts only after both deployed identities converge.
export function seedStateMachine(onTransition = () => {}) {
  return new ReleaseStateMachine({
    assumed: RELEASE_STATES.slice(0, 11),
    onTransition,
  });
}

export function evaluateInfrastructureReadiness(input, contract) {
  const infraRows = requiredSnapshotRows(contract, 'INFRA_REQUIRED');
  const observed = input.infraSnapshots ?? [];
  if (input.backendHealth?.status !== 'ok') return { pass: false, reason: 'backend_health' };
  if (input.backendReady?.ready !== true) return { pass: false, reason: 'backend_ready' };
  if (!shaMatches(input.expectedBuildSha, input.backendHealth?.buildSha)
      || !shaMatches(input.expectedBuildSha, input.backendReady?.buildSha)) {
    return { pass: false, reason: 'backend_identity' };
  }
  if (input.processStable !== true || input.crashLoop === true
      || input.oomKilled === true) return { pass: false, reason: 'process_stability' };
  if (input.storageValid !== true) return { pass: false, reason: 'storage' };
  if (!['restored', 'no_prior_state', 'test_mode', 'readyz_contract']
    .includes(input.restoreOutcome)) return { pass: false, reason: 'restore_outcome' };
  const expectedSet = infraRows.map((row) => row.identity).sort();
  const observedSet = observed.map(snapshotIdentity).sort();
  if (JSON.stringify(expectedSet) !== JSON.stringify(observedSet)) {
    return { pass: false, reason: 'infra_snapshot_set', expectedSet, observedSet };
  }
  return { pass: true, reason: 'accepted', expectedSet, observedSet };
}

// v13.5.64: a product build waits up to 15 minutes for Render to report ITS
// sha. When a later main merge (a Recovery change) redeploys Render inside
// that window the live backend contains the candidate but reports the newer
// sha, and the release used to time out (13.5.63 never shipped). The caller
// may supply `acceptSuccessor(observedSha)` — true only when the observed
// sha is a descendant of the candidate on the release branch — and the
// readiness is then evaluated against the observed identity, recorded as
// such in the evidence.
export async function waitForInfrastructureReadiness({
  baseUrl, contract, expectedBuildSha, timeoutSeconds = 900,
  pollSeconds = 10, fetchImpl = fetch, acceptSuccessor = null,
}) {
  const deadline = Date.now() + Number(timeoutSeconds) * 1000;
  let attempt = 0;
  let last = { pass: false, reason: 'not_checked' };
  while (Date.now() <= deadline) {
    attempt += 1;
    try {
      const base = baseUrl.replace(/\/$/, '');
      const [healthResponse, readyResponse] = await Promise.all([
        fetchImpl(`${base}/healthz`, { headers: { Accept: 'application/json' } }),
        fetchImpl(`${base}/readyz`, { headers: { Accept: 'application/json' } }),
      ]);
      const backendHealth = await healthResponse.json();
      const backendReady = await readyResponse.json();
      let evaluationSha = expectedBuildSha;
      let acceptedAsSuccessor = false;
      const observedSha = String(backendHealth?.buildSha ?? '');
      if (acceptSuccessor && observedSha && !shaMatches(expectedBuildSha, observedSha)
          && shaMatches(observedSha, backendReady?.buildSha)
          && await acceptSuccessor(observedSha) === true) {
        evaluationSha = observedSha;
        acceptedAsSuccessor = true;
      }
      last = evaluateInfrastructureReadiness({
        backendHealth, backendReady, expectedBuildSha: evaluationSha,
        processStable: healthResponse.status === 200 && readyResponse.status === 200,
        crashLoop: false, oomKilled: false, storageValid: backendReady.ready === true,
        restoreOutcome: 'readyz_contract', infraSnapshots: [],
      }, contract);
      last = { ...last, backendHealth, backendReady, attempt,
        expectedBuildSha, observedBuildSha: observedSha, acceptedAsSuccessor };
      if (last.pass) return last;
    } catch (error) {
      last = { pass: false, reason: `request:${error.name}`, attempt };
    }
    if (Date.now() + Number(pollSeconds) * 1000 > deadline) break;
    await new Promise((resolve) => setTimeout(resolve, Number(pollSeconds) * 1000));
  }
  throw new Error(`infrastructure_readiness_timeout:${last.reason}:attempt_${attempt}`);
}

const validSnapshotShape = (snapshot) => snapshot && typeof snapshot === 'object'
  && snapshot.schemaVersion === 'argus-verified-view-snapshot-v1'
  && /^vs-[0-9a-f]{32}$/.test(String(snapshot.snapshotId ?? ''))
  && snapshot.verificationStatus === 'verified'
  && typeof snapshot.datasetHash === 'string' && snapshot.datasetHash.length > 0
  && typeof snapshot.payloadHash === 'string' && snapshot.payloadHash.length > 0
  && typeof snapshot.methodVersion === 'string' && snapshot.methodVersion.length > 0
  && ['live', 'partial', 'stale'].includes(snapshot.quality)
  && snapshot.payload && typeof snapshot.payload === 'object';

export function evaluateBusinessSnapshotSet({
  contract, observed, expectedBuildSha, producerTriggerId, now = Date.now(),
}) {
  const rows = requiredSnapshotRows(contract, 'SEED_REQUIRED');
  if (!Array.isArray(observed)) return { pass: false, reason: 'observed_malformed' };
  const expectedSet = rows.map((row) => row.identity).sort();
  const observedIdentities = observed.map(snapshotIdentity);
  if (new Set(observedIdentities).size !== observedIdentities.length) {
    return { pass: false, reason: 'duplicate_snapshot', expectedSet, observedSet: observedIdentities.sort() };
  }
  const observedSet = [...observedIdentities].sort();
  if (JSON.stringify(expectedSet) !== JSON.stringify(observedSet)) {
    const missing = expectedSet.filter((identity) => !observedSet.includes(identity));
    const additional = observedSet.filter((identity) => !expectedSet.includes(identity));
    return { pass: false, reason: 'snapshot_set_mismatch', expectedSet, observedSet, missing, additional };
  }
  const rowByIdentity = new Map(rows.map((row) => [row.identity, row]));
  for (const snapshot of observed) {
    const identity = snapshotIdentity(snapshot);
    const row = rowByIdentity.get(identity);
    if (!row || !validSnapshotShape(snapshot)) {
      return { pass: false, reason: `malformed_snapshot:${identity}`, expectedSet, observedSet };
    }
    if (snapshot.instrument !== row.instrument || snapshot.horizon !== row.horizon
        || snapshot.kind !== row.kind) {
      return { pass: false, reason: `identity_substitution:${identity}`, expectedSet, observedSet };
    }
    const binding = snapshot.releaseBinding;
    if (!binding || binding.expectedBuildSha !== expectedBuildSha) {
      return { pass: false, reason: `wrong_build:${identity}`, expectedSet, observedSet };
    }
    if (binding.producerTriggerId !== producerTriggerId) {
      return { pass: false, reason: `wrong_trigger:${identity}`, expectedSet, observedSet };
    }
    const generatedAt = Date.parse(snapshot.generatedAt);
    const triggeredAt = Date.parse(binding.triggeredAt);
    if (!Number.isFinite(generatedAt) || !Number.isFinite(triggeredAt)
        || generatedAt < triggeredAt
        || now - generatedAt > row.freshnessPolicy.maxAgeSecondsAtAcceptance * 1000
        || generatedAt > now + 300_000) {
      return { pass: false, reason: `stale_snapshot:${identity}`, expectedSet, observedSet };
    }
  }
  return { pass: true, reason: 'accepted', expectedSet, observedSet };
}

export function createTriggerPlan(contract, { expectedBuildSha, producerTriggerId, actionTimestamp }) {
  return requiredSnapshotRows(contract, 'SEED_REQUIRED').map((row) => ({
    targetSnapshotIdentity: row.identity,
    triggeringAction: 'authenticated releaseSnapshotSeed producer request',
    triggeringRequest: 'POST /api/argus/admin/missions/tick releaseSnapshotSeed=true',
    actionTimestamp,
    requestTimestamp: null,
    responseStatus: null,
    snapshotAvailabilityTimestamp: null,
    freshness: null,
    buildIdentity: expectedBuildSha,
    producerTriggerId,
  }));
}

const sanitizeDiagnosticText = (value, secrets = []) => {
  let result = String(value ?? '');
  for (const secret of secrets) {
    if (secret) result = result.split(String(secret)).join('[REDACTED]');
  }
  return result
    .replace(/(authorization|cookie|x-argus-admin-token)\s*[:=]\s*[^\s,;]+/gi,
      '$1=[REDACTED]')
    .replace(/bearer\s+[^\s,;]+/gi, 'Bearer [REDACTED]')
    .slice(0, 500);
};

const describeTransportError = (error, {
  elapsedMs, phase, requestUrl, httpResponseObtained, secrets = [],
}) => {
  let hostname = null;
  let pathName = null;
  try {
    const parsed = new URL(requestUrl);
    hostname = parsed.hostname;
    pathName = parsed.pathname;
  } catch {
    pathName = '/api/argus/admin/missions/tick';
  }
  const describe = (value) => value ? {
    name: sanitizeDiagnosticText(value.name || 'Error', secrets),
    code: value.code == null ? null : sanitizeDiagnosticText(value.code, secrets),
    message: sanitizeDiagnosticText(value.message || String(value), secrets),
  } : null;
  return {
    ...describe(error),
    cause: describe(error?.cause),
    elapsedMs: Math.max(0, Math.round(Number(elapsedMs) || 0)),
    phase,
    hostname,
    path: pathName,
    httpResponseObtained: Boolean(httpResponseObtained),
  };
};

export class BusinessSnapshotTriggerError extends Error {
  constructor(reason, artifact) {
    super(`business_snapshot_trigger_failed:${reason}`);
    this.name = 'BusinessSnapshotTriggerError';
    this.reason = reason;
    this.artifact = artifact;
  }
}

const snapshotSummary = (snapshot) => ({
  identity: snapshotIdentity(snapshot),
  snapshotId: snapshot?.snapshotId ?? null,
  generatedAt: snapshot?.generatedAt ?? null,
  verificationStatus: snapshot?.verificationStatus ?? null,
  expectedBuildSha: snapshot?.releaseBinding?.expectedBuildSha ?? null,
  producerTriggerId: snapshot?.releaseBinding?.producerTriggerId ?? null,
});

export async function fetchBusinessSnapshotObservations({
  baseUrl, contract, fetchImpl = fetch,
  requestTimeoutMs = BUSINESS_READBACK_REQUEST_TIMEOUT_MS,
  deadlineAtMs = Number.POSITIVE_INFINITY, nowMs = () => Date.now(),
  setTimeoutImpl = setTimeout, clearTimeoutImpl = clearTimeout,
}) {
  const base = baseUrl.replace(/\/$/, '');
  const observations = [];
  // Verified snapshot reads stay serialized to avoid a concurrent 429 burst.
  for (const row of requiredSnapshotRows(contract, 'SEED_REQUIRED')) {
    const query = new URLSearchParams({
      scope: 'market', timeframe: 'daily', symbol: row.instrument,
      horizon: row.horizon, snapshot: 'verified',
    });
    const controller = new AbortController();
    const remainingMs = deadlineAtMs - nowMs();
    const effectiveTimeoutMs = Math.min(
      Math.max(1, Number(requestTimeoutMs)), Math.max(1, remainingMs),
    );
    const timeoutId = setTimeoutImpl(
      () => controller.abort(), effectiveTimeoutMs,
    );
    try {
      const response = await fetchImpl(
        `${base}/api/argus/chart-intelligence?${query}`,
        { headers: { Accept: 'application/json' }, signal: controller.signal },
      );
      const body = await response.json().catch(() => null);
      observations.push({
        expectedIdentity: row.identity,
        status: response.status,
        snapshot: response.status === 200 && body ? body : null,
        error: null,
      });
    } catch (error) {
      observations.push({
        expectedIdentity: row.identity,
        status: null,
        snapshot: null,
        error: {
          name: controller.signal.aborted ? 'AbortError'
            : sanitizeDiagnosticText(error?.name || 'Error'),
          code: error?.code == null ? null : sanitizeDiagnosticText(error.code),
          message: controller.signal.aborted ? 'readback request timeout'
            : sanitizeDiagnosticText(error?.message || String(error)),
        },
      });
    } finally {
      clearTimeoutImpl(timeoutId);
    }
  }
  return observations;
}

export function evaluateBusinessSnapshotObservations({
  contract, observations, expectedBuildSha, producerTriggerId, now = Date.now(),
}) {
  if (!Array.isArray(observations)) {
    return { outcome: 'UNKNOWN', reason: 'observations_malformed', snapshots: [] };
  }
  const snapshots = observations.flatMap((row) => row.snapshot ? [row.snapshot] : []);
  const missing = observations
    .filter((row) => !row.snapshot)
    .map((row) => ({ identity: row.expectedIdentity, status: row.status, error: row.error }));
  if (missing.length) {
    return { outcome: 'INCOMPLETE', reason: 'snapshot_readback_incomplete', missing, snapshots };
  }

  const triggerIds = new Set(snapshots.map((row) => row?.releaseBinding?.producerTriggerId));
  const buildIds = new Set(snapshots.map((row) => row?.releaseBinding?.expectedBuildSha));
  if (triggerIds.size > 1 || buildIds.size > 1) {
    return { outcome: 'MIXED_IDENTITY', reason: 'mixed_release_binding', snapshots };
  }
  if (triggerIds.size !== 1 || !triggerIds.has(producerTriggerId)) {
    return { outcome: 'WRONG_TRIGGER', reason: 'wrong_trigger', snapshots };
  }
  if (buildIds.size !== 1 || !buildIds.has(expectedBuildSha)) {
    return { outcome: 'WRONG_BUILD', reason: 'wrong_build', snapshots };
  }

  const evaluated = evaluateBusinessSnapshotSet({
    contract, observed: snapshots, expectedBuildSha, producerTriggerId, now,
  });
  if (evaluated.pass) {
    return { outcome: 'COMPLETE', reason: 'accepted', snapshots, evaluated };
  }
  if (evaluated.reason.startsWith('stale_snapshot:')) {
    return { outcome: 'STALE', reason: evaluated.reason, snapshots, evaluated };
  }
  if (evaluated.reason.startsWith('wrong_trigger:')) {
    return { outcome: 'WRONG_TRIGGER', reason: evaluated.reason, snapshots, evaluated };
  }
  if (evaluated.reason.startsWith('wrong_build:')) {
    return { outcome: 'WRONG_BUILD', reason: evaluated.reason, snapshots, evaluated };
  }
  if (evaluated.reason === 'snapshot_set_mismatch') {
    return { outcome: 'INCOMPLETE', reason: evaluated.reason, snapshots, evaluated };
  }
  return { outcome: 'VERIFICATION_FAILED', reason: evaluated.reason, snapshots, evaluated };
}

export async function reconcileBusinessSnapshots({
  baseUrl, contract, expectedBuildSha, producerTriggerId,
  fetchImpl = fetch, nowMs = () => Date.now(),
  sleepImpl = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  startedAtMs = nowMs(), deadlineMs = BUSINESS_RECONCILIATION_DEADLINE_MS,
  pollMs = BUSINESS_RECONCILIATION_POLL_MS,
}) {
  const deadlineAtMs = startedAtMs + Math.max(0, Number(deadlineMs));
  let attempts = 0;
  let last = { outcome: 'UNKNOWN', reason: 'not_observed', snapshots: [] };
  while (true) {
    attempts += 1;
    const observations = await fetchBusinessSnapshotObservations({
      baseUrl, contract, fetchImpl, deadlineAtMs, nowMs,
    });
    last = evaluateBusinessSnapshotObservations({
      contract, observations, expectedBuildSha, producerTriggerId, now: nowMs(),
    });
    if (last.outcome === 'COMPLETE') {
      return { ...last, attempts, deadlineExceeded: false };
    }
    const remainingMs = deadlineAtMs - nowMs();
    if (remainingMs <= 0) {
      return {
        outcome: 'TIMEOUT', reason: 'reconciliation_deadline_exceeded', attempts,
        deadlineExceeded: true,
        lastObservation: {
          outcome: last.outcome,
          reason: last.reason,
          missing: last.missing,
          evaluated: last.evaluated,
        },
        snapshots: last.snapshots,
      };
    }
    await sleepImpl(Math.min(Math.max(1, Number(pollMs)), remainingMs));
  }
}

const acknowledgementIsExact = (body, expectedBuildSha, producerTriggerId) =>
  body?.status === 'completed'
  && body?.schemaVersion === 'argus-release-snapshot-seed-v1'
  && body?.producerTriggerId === producerTriggerId
  && body?.expectedBuildSha === expectedBuildSha
  && body?.snapshotExpected === 12 && body?.snapshotReady === 12
  && body?.persistence?.verified === true
  && body?.persistence?.readBackVerified === true;

export async function triggerBusinessSnapshots({
  baseUrl, adminToken, contract, expectedBuildSha, producerTriggerId,
  fetchImpl = fetch, nowMs = () => Date.now(),
  sleepImpl = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  transportTimeoutMs = BUSINESS_TRIGGER_TRANSPORT_TIMEOUT_MS,
  reconciliationDeadlineMs = BUSINESS_RECONCILIATION_DEADLINE_MS,
  reconciliationPollMs = BUSINESS_RECONCILIATION_POLL_MS,
  setTimeoutImpl = setTimeout, clearTimeoutImpl = clearTimeout,
}) {
  validateSnapshotContract(contract);
  const startedAtMs = nowMs();
  const actionTimestamp = new Date(startedAtMs).toISOString();
  const plan = createTriggerPlan(contract, {
    expectedBuildSha, producerTriggerId, actionTimestamp,
  });
  const requestTimestamp = new Date(nowMs()).toISOString();
  const requestUrl = `${baseUrl.replace(/\/$/, '')}/api/argus/admin/missions/tick`;
  let response = null;
  let body = null;
  let transportError = null;
  // v13.5.16: Render's router briefly answers 503 while it swaps instances
  // right after a deploy — a deterministic response that provably did NOT
  // execute the seed. The trigger id makes the request idempotent (a landed
  // duplicate resolves through the 409/IDEMPOTENT path), so 5xx statuses are
  // retried with a fixed backoff instead of failing the whole producer job.
  const RETRYABLE_STATUS_ATTEMPTS = 6;
  const RETRYABLE_STATUS_BACKOFF_MS = 20_000;
  for (let attempt = 1; attempt <= RETRYABLE_STATUS_ATTEMPTS; attempt += 1) {
    response = null;
    body = null;
    transportError = null;
    const controller = new AbortController();
    const timeoutId = setTimeoutImpl(() => controller.abort(), Math.max(1, Number(transportTimeoutMs)));
    try {
      response = await fetchImpl(requestUrl, {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-ARGUS-ADMIN-TOKEN': adminToken,
        },
        body: JSON.stringify({
          triggerSource: 'manual',
          runId: producerTriggerId,
          expectedBuildSha,
          releaseSnapshotSeed: true,
        }),
        signal: controller.signal,
      });
      body = await response.json().catch(() => null);
    } catch (error) {
      transportError = describeTransportError(error, {
        elapsedMs: nowMs() - startedAtMs,
        phase: controller.signal.aborted ? 'transport_timeout' : 'transport_request',
        requestUrl,
        httpResponseObtained: Boolean(response),
        secrets: [adminToken],
      });
    } finally {
      clearTimeoutImpl(timeoutId);
    }
    if (response != null && response.status >= 500
        && attempt < RETRYABLE_STATUS_ATTEMPTS) {
      await sleepImpl(RETRYABLE_STATUS_BACKOFF_MS);
      continue;
    }
    break;
  }

  let completionMode = 'HTTP_ACKNOWLEDGED';
  if (transportError) {
    completionMode = 'RECOVERED_AFTER_TRANSPORT_FAILURE';
  } else if (response?.status === 409) {
    if (body?.producerTriggerId !== producerTriggerId) {
      const artifact = {
        schemaVersion: 'argus-v13-snapshot-trigger-plan-v1', status: 'failed',
        expectedBuildSha, producerTriggerId, actionTimestamp, requestTimestamp,
        responseStatus: response.status, outcome: 'WRONG_TRIGGER',
        reason: 'conflicting_duplicate_trigger', plan,
      };
      throw new BusinessSnapshotTriggerError(artifact.reason, artifact);
    }
    completionMode = 'IDEMPOTENT_EXISTING_RESULT';
  } else if (response && (response.status !== 200
      || !acknowledgementIsExact(body, expectedBuildSha, producerTriggerId))) {
    const artifact = {
      schemaVersion: 'argus-v13-snapshot-trigger-plan-v1', status: 'failed',
      expectedBuildSha, producerTriggerId, actionTimestamp, requestTimestamp,
      responseStatus: response.status, outcome: 'UNKNOWN',
      reason: `deterministic_http_response:http_${response.status}:` +
        sanitizeDiagnosticText(body?.status ?? 'invalid', [adminToken]) +
        (body?.errorClass ? `:${sanitizeDiagnosticText(body.errorClass, [adminToken])}` : '') +
        (body?.errorDetail ? `:${sanitizeDiagnosticText(String(body.errorDetail).slice(0, 160), [adminToken])}` : ''),
      plan,
    };
    throw new BusinessSnapshotTriggerError(artifact.reason, artifact);
  }

  const reconciliation = await reconcileBusinessSnapshots({
    baseUrl, contract, expectedBuildSha, producerTriggerId, fetchImpl, nowMs, sleepImpl,
    startedAtMs, deadlineMs: reconciliationDeadlineMs, pollMs: reconciliationPollMs,
  });
  const diagnosticReconciliation = {
    outcome: reconciliation.outcome,
    reason: reconciliation.reason,
    attempts: reconciliation.attempts,
    deadlineExceeded: reconciliation.deadlineExceeded,
    lastObservation: reconciliation.lastObservation,
    snapshots: (reconciliation.snapshots ?? []).map(snapshotSummary),
  };
  if (reconciliation.outcome !== 'COMPLETE') {
    const artifact = {
      schemaVersion: 'argus-v13-snapshot-trigger-plan-v1', status: 'failed',
      expectedBuildSha, producerTriggerId, actionTimestamp, requestTimestamp,
      responseStatus: response?.status ?? null, completionMode, transport: transportError,
      reconciliation: diagnosticReconciliation, outcome: reconciliation.outcome,
      reason: reconciliation.reason, plan,
    };
    throw new BusinessSnapshotTriggerError(reconciliation.reason, artifact);
  }

  const availability = new Map(reconciliation.snapshots.map((row) => [snapshotIdentity(row), row]));
  const completedPlan = plan.map((row) => {
    const snapshot = availability.get(row.targetSnapshotIdentity);
    return {
      ...row,
      requestTimestamp,
      responseStatus: response?.status ?? null,
      snapshotAvailabilityTimestamp: snapshot.generatedAt,
      freshness: 'trigger_bound',
      snapshotId: snapshot.snapshotId,
    };
  });
  return {
    schemaVersion: 'argus-v13-snapshot-trigger-plan-v1',
    status: 'completed',
    expectedBuildSha,
    producerTriggerId,
    triggeredAt: reconciliation.snapshots[0].releaseBinding.triggeredAt,
    completedAt: new Date(nowMs()).toISOString(),
    completionMode,
    transport: transportError,
    reconciliation: diagnosticReconciliation,
    plan: completedPlan,
  };
}

export async function fetchBusinessSnapshots({ baseUrl, contract, fetchImpl = fetch }) {
  const observations = await fetchBusinessSnapshotObservations({ baseUrl, contract, fetchImpl });
  const failure = observations.find((row) => !row.snapshot);
  if (failure) {
    throw new Error(`business_snapshot_http:${failure.expectedIdentity}:` +
      `${failure.status ?? failure.error?.name ?? 'unknown'}`);
  }
  return observations.map((row) => row.snapshot);
}

export function evaluateFailureScenario(input) {
  const statuses = input.httpStatuses ?? [200];
  if (input.initialSnapshotReady !== 0) return { pass: false, reason: 'cold_start_not_empty' };
  if (input.infrastructureHealthy === false) return { pass: false, reason: 'infrastructure' };
  if (input.frontendIdentity !== 'candidate') return { pass: false, reason: 'frontend_identity' };
  if (input.backendIdentity !== 'candidate') return { pass: false, reason: 'backend_identity' };
  if (input.oldFrontend === true) return { pass: false, reason: 'old_frontend' };
  if (input.frontendDeployedBeforeSeed !== true) return { pass: false, reason: 'deploy_seed_order' };
  if (!input.todayLoaded) return { pass: false, reason: 'today_not_loaded' };
  if (!input.selected1321) return { pass: false, reason: '1321_not_selected' };
  if (!input.selected5D) return { pass: false, reason: '5d_not_selected' };
  if (statuses.some((status) => status === 429)
      && statuses.filter((status) => status === 429).length > 2) {
    return { pass: false, reason: 'rate_limit_exhausted' };
  }
  if (!statuses.includes(200)) return { pass: false, reason: 'http_not_200' };
  if (input.verificationStatus !== 'verified') return { pass: false, reason: 'not_verified' };
  if (!input.responseSnapshotId) return { pass: false, reason: 'response_snapshot_missing' };
  if (input.responseSnapshotId !== input.uiSnapshotId) return { pass: false, reason: 'snapshot_mismatch' };
  if (!input.serviceWorkerReady || input.serviceWorkerStale) {
    return { pass: false, reason: 'service_worker' };
  }
  if (!input.indexedDbReady) return { pass: false, reason: 'indexeddb' };
  if (!input.profileValid) return { pass: false, reason: 'profile' };
  if (input.wrongBuildSnapshot) return { pass: false, reason: 'wrong_build_snapshot' };
  if (input.duplicateSnapshot) return { pass: false, reason: 'duplicate_snapshot' };
  if (input.missingSeedRequired) return { pass: false, reason: 'missing_seed_required' };
  if (!input.identityStable || input.frontendIdentityChanged
      || input.backendIdentityChanged) return { pass: false, reason: 'identity_changed' };
  return { pass: true, reason: 'accepted' };
}

export function finalizePublicAcceptance({
  businessArtifact, publicArtifact, mobileArtifact, expectedFrontendSha,
}) {
  if (businessArtifact?.status !== 'pass'
      || businessArtifact?.expectedSet?.length !== 12
      || JSON.stringify(businessArtifact.expectedSet)
        !== JSON.stringify(businessArtifact.observedSet)) {
    throw new Error('finalize_business_artifact_invalid');
  }
  if (publicArtifact?.verdict !== 'PASS'
      || publicArtifact?.frontendSha !== expectedFrontendSha) {
    throw new Error('finalize_public_artifact_invalid');
  }
  if (mobileArtifact?.verdict !== 'PASS'
      || mobileArtifact?.frontendSha !== expectedFrontendSha) {
    throw new Error('finalize_mobile_artifact_invalid');
  }
  const machine = new ReleaseStateMachine({ assumed: RELEASE_STATES.slice(0, 19) });
  machine.transition('R19_PUBLIC_PRODUCT_ACCEPTED', {
    publicVerdict: publicArtifact.verdict,
    mobileVerdict: mobileArtifact.verdict,
  });
  machine.transition('R20_V13_LIVE', { frontendSha: expectedFrontendSha });
  return { status: 'pass', engineVersion: RELEASE_ENGINE_VERSION,
    frontendSha: expectedFrontendSha, releaseStateLog: machine.log };
}

// v13.5.64: observed sha is accepted only when it is a descendant of the
// candidate AND reachable from the release branch head (fetched fresh on
// every check, so a merge that lands during the wait is seen).
export function gitSuccessorAcceptor(candidateSha, branch, exec = execFileSync) {
  return async (observedSha) => {
    const sha = String(observedSha ?? '').trim();
    if (!/^[0-9a-f]{7,40}$/i.test(sha) || !/^[A-Za-z0-9._\/-]{1,80}$/.test(String(branch))) return false;
    try {
      exec('git', ['fetch', '--quiet', 'origin', String(branch)], { stdio: 'ignore' });
      exec('git', ['merge-base', '--is-ancestor', candidateSha, sha], { stdio: 'ignore' });
      exec('git', ['merge-base', '--is-ancestor', sha, `origin/${branch}`], { stdio: 'ignore' });
      return true;
    } catch {
      return false;
    }
  };
}

const parseArgs = (argv) => {
  const result = { _: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    if (!item.startsWith('--')) result._.push(item);
    else {
      result[item.slice(2)] = argv[index + 1];
      index += 1;
    }
  }
  return result;
};

const writeJson = (outputPath, value) => {
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(value, null, 2)}\n`);
};

async function cli(argv) {
  const args = parseArgs(argv);
  const command = args._[0];
  const contract = validateSnapshotContract(loadSnapshotContract(args.contract));
  if (command === 'validate-contract') {
    const result = { status: 'pass', engineVersion: RELEASE_ENGINE_VERSION,
      snapshotExpected: contract.snapshotExpected };
    if (args.out) writeJson(args.out, result);
    else process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }
  if (command === 'infrastructure') {
    const result = await waitForInfrastructureReadiness({
      baseUrl: args['base-url'], contract, expectedBuildSha: args['expected-sha'],
      timeoutSeconds: Number(args['timeout-seconds'] ?? 900),
      pollSeconds: Number(args['poll-seconds'] ?? 10),
      acceptSuccessor: args['accept-successors-on']
        ? gitSuccessorAcceptor(args['expected-sha'], args['accept-successors-on']) : null,
    });
    const artifact = { ...result, status: 'pass', engineVersion: RELEASE_ENGINE_VERSION,
      snapshotReady: 0, snapshotExpected: 0 };
    writeJson(args.out, artifact);
    return;
  }
  if (command === 'trigger-business') {
    try {
      const result = await triggerBusinessSnapshots({
        baseUrl: args['base-url'], adminToken: process.env.ARGUS_ADMIN_TOKEN ?? '',
        contract, expectedBuildSha: args['expected-sha'],
        producerTriggerId: args['trigger-id'],
      });
      writeJson(args.out, result);
    } catch (error) {
      if (args.out && error?.artifact) writeJson(args.out, error.artifact);
      throw error;
    }
    return;
  }
  if (command === 'verify-business') {
    const trigger = JSON.parse(fs.readFileSync(args['trigger-artifact'], 'utf8'));
    const observed = await fetchBusinessSnapshots({ baseUrl: args['base-url'], contract });
    const result = evaluateBusinessSnapshotSet({
      contract, observed, expectedBuildSha: args['expected-sha'],
      producerTriggerId: trigger.producerTriggerId,
    });
    if (!result.pass) throw new Error(`business_snapshot_acceptance:${result.reason}`);
    writeJson(args.out, { ...result, status: 'pass', engineVersion: RELEASE_ENGINE_VERSION,
      producerTriggerId: trigger.producerTriggerId,
      observed: observed.map((row) => ({ identity: snapshotIdentity(row),
        snapshotId: row.snapshotId, generatedAt: row.generatedAt,
        releaseBinding: row.releaseBinding })) });
    return;
  }
  if (command === 'finalize-public') {
    const result = finalizePublicAcceptance({
      businessArtifact: JSON.parse(fs.readFileSync(args['business-artifact'], 'utf8')),
      publicArtifact: JSON.parse(fs.readFileSync(args['public-artifact'], 'utf8')),
      mobileArtifact: JSON.parse(fs.readFileSync(args['mobile-artifact'], 'utf8')),
      expectedFrontendSha: args['expected-sha'],
    });
    writeJson(args.out, result);
    return;
  }
  throw new Error(`unknown_release_engine_command:${command}`);
}

if (process.argv[1]
    && import.meta.url === pathToFileURL(process.argv[1]).href) {
  cli(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
