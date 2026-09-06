#!/usr/bin/env python3
"""Create, fetch, and verify the detached V13.5 production certificate."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Dict, Mapping

try:
    from scripts import v13_5_source_provenance as source_provenance
except ModuleNotFoundError:  # Direct execution from scripts/.
    import v13_5_source_provenance as source_provenance  # type: ignore


SCHEMA = "argus-v13-5-release-proof-certificate-v1"
CHECKS_SCHEMA = "argus-current-required-checks-v1"
RUNTIME_PROOF_SCHEMA = "argus-zero-install-runtime-proof-v1"
ADMISSION_SCHEMA = "argus-v13-5-premerge-admission-certificate-v1"
RETRIEVAL_SCHEMA = "argus-v13-5-detached-certificate-retrieval-v1"
AUTHORITY_SCHEMA = "argus-v13-5-authorized-producer-binding-v1"
PRODUCT_VERSION = "v13.5.59"
ACCEPTED_V13_SOURCE = source_provenance.ACCEPTED_V13_SOURCE
ACCEPTED_V13_TREE = source_provenance.ACCEPTED_V13_TREE
ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY_INPUTS = (
    "product-version.json",
    "release/v13-acceptance-runtime.json",
    "release/v13-snapshot-readiness-contract.json",
    "release/v13-accepted-fix-manifest.json",
    ".github/actions/acceptance-runtime-preflight/action.yml",
    ".github/actions/v13-5-pre-mutation-rehearsal/action.yml",
    ".github/actions/warm-profile-seed/action.yml",
    ".github/actions/warm-profile-consumer/action.yml",
    ".github/workflows/deploy-pages.yml",
    ".github/workflows/market-public-acceptance.yml",
    ".github/workflows/release-gate.yml",
    "scripts/v13_5_release_certificate.py",
    "scripts/v13_5_source_provenance.py",
    "scripts/v13_5_pre_mutation_rehearsal.py",
    "web/scripts/acceptance-runtime.mjs",
    "web/scripts/release-state-machine.mjs",
    "web/scripts/full-release-simulation.mjs",
    "web/scripts/release-fixture-target.mjs",
    "web/scripts/mobile-today-acceptance.mjs",
    "web/scripts/canonical-snapshot-selection.mjs",
)
AUTHORIZED_EXTENSION_PATHS = source_provenance.AUTHORIZED_EXTENSION_PATHS


class _StripCrossOriginAuthorization(urllib.request.HTTPRedirectHandler):
    """Keep GitHub auth on GitHub, never forward it to signed blob URLs."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(
            req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if old.scheme == "https" and new.scheme != "https":
            raise ValueError("github_redirect_insecure")
        if old.netloc != new.netloc:
            for values in (redirected.headers, redirected.unredirected_hdrs):
                for name in list(values):
                    if name.lower() == "authorization":
                        del values[name]
        return redirected


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_file(path: pathlib.Path) -> str:
    return _digest_bytes(path.read_bytes())


def _is_lower_hex(value: Any, length: int) -> bool:
    return type(value) is str and len(value) == length \
        and all(character in "0123456789abcdef" for character in value)


def _git(value: str) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", value], cwd=ROOT, text=True).strip()


def _ensure_clean_candidate() -> None:
    changed = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT, text=True).strip()
    if changed:
        raise ValueError("candidate_worktree_not_clean")


def _load(path: pathlib.Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError(f"json_object_required:{path}")
    return value


def _write(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")


def _candidate(ref: str) -> Dict[str, str]:
    return {"commitSha": _git(f"{ref}^{{commit}}"),
            "treeSha": _git(f"{ref}^{{tree}}")}


def _validate_manifest() -> Dict[str, Any]:
    manifest = _load(ROOT / "release/v13-accepted-fix-manifest.json")
    rows = manifest.get("requirements")
    if not isinstance(rows, list) or len(rows) < 25:
        raise ValueError("accepted_fix_manifest_incomplete")
    names = set()
    for row in rows:
        if type(row) is not dict or set(row) != {
                "requirement", "implementation", "test", "status"}:
            raise ValueError("accepted_fix_manifest_row_shape")
        if row.get("status") != "PRESENT" or type(row.get("requirement")) is not str \
                or row["requirement"] in names:
            raise ValueError("accepted_fix_missing_or_duplicate")
        names.add(row["requirement"])
        for relative in row["implementation"] + row["test"]:
            if type(relative) is not str or not (ROOT / relative).exists():
                raise ValueError(f"accepted_fix_path_missing:{relative}")
    if not {
        "productVersion = v13.5",
        "immutable zero-install acceptance runtime",
        "two fresh-runner zero-install proofs",
        "pre-merge detached runtime admission",
        "GitHub artifact JSON media and safe redirect transport",
        "exact accepted-source reachability and tree admission",
        "shared pre-mutation production rehearsal",
        "mobile Today canonical projection-state acceptance",
        "warm cached projection semantic revalidation",
        "rollback restore has no browser dependency",
    }.issubset(names):
        raise ValueError("accepted_fix_manifest_release_rows_missing")
    return manifest


def _validate_contract() -> Dict[str, Any]:
    contract = _load(ROOT / "release/v13-snapshot-readiness-contract.json")
    rows = contract.get("snapshots")
    identities = [row.get("identity") for row in rows or []]
    if contract.get("snapshotExpected") != 12 or len(identities) != 12 \
            or len(set(identities)) != 12 \
            or any(row.get("requiredness") != "SEED_REQUIRED" for row in rows):
        raise ValueError("snapshot_contract_not_exact_12")
    return contract


def _validate_product_semantic_diff(candidate_ref: str) -> Dict[str, Any]:
    return source_provenance.validate_product_semantic_diff(
        candidate_ref, repo=ROOT)


def _validate_simulation(path: pathlib.Path, ordinal: int,
                         candidate: Mapping[str, str]) -> Dict[str, Any]:
    value = _load(path)
    checks = (
        value.get("schemaVersion") == "argus-v13-full-release-simulation-v1",
        value.get("runNumber") == ordinal, value.get("status") == "pass",
        value.get("candidateSha") == candidate["commitSha"],
        value.get("initial") == {"snapshotReady": 0, "snapshotExpected": 12},
        value.get("infrastructure", {}).get("pass") is True,
        value.get("trigger", {}).get("status") == "completed",
        len(value.get("trigger", {}).get("plan") or []) == 12,
        value.get("businessSnapshots", {}).get("pass") is True,
        len(value.get("businessSnapshots", {}).get("expectedSet") or []) == 12,
        value.get("businessSnapshots", {}).get("expectedSet")
        == value.get("businessSnapshots", {}).get("observedSet"),
        value.get("canonical", {}).get("instrument") == "1321",
        value.get("canonical", {}).get("horizon") == "5D",
        value.get("canonical", {}).get("responseSnapshotId")
        == value.get("canonical", {}).get("uiSnapshotId"),
        value.get("warmProfileSeal", {}).get("status") == "pass",
        value.get("warmProfileSeal", {}).get("productVersion") == PRODUCT_VERSION,
        value.get("independentProfileReopen", {}).get("status") == "pass",
        value.get("publicProductAcceptance", {}).get("status") == "pass",
        # The exact production acceptance engine must have reached terminal
        # PASS against this candidate before any certificate can exist. A
        # certificate without this proof would readmit the class of defect
        # that production alone kept discovering.
        value.get("mobileAcceptance", {}).get("status") == "pass",
        value.get("mobileAcceptance", {}).get("verdict") == "PASS",
        value.get("mobileAcceptance", {}).get("exitCode") == 0,
        value.get("mobileAcceptance", {}).get("frontendSha")
        == candidate["commitSha"],
        value.get("mobileAcceptance", {}).get("combinationCount") == 12,
        value.get("mobileAcceptance", {}).get("failures") == [],
        len(value.get("mobileAcceptance", {}).get("gateInventory") or []) >= 14,
    )
    if not all(checks):
        raise ValueError(f"full_release_simulation_{ordinal}_invalid")
    return value


def _validate_runtime_proof(path: pathlib.Path,
                            candidate: Mapping[str, str]) -> Dict[str, Any]:
    value = _load(path)
    digest = value.pop("proofDigest", None)
    identity, checks = value.get("runtimeIdentity"), value.get("checks")
    if digest != _digest_bytes(_canonical(value)) \
            or value.get("schemaVersion") != RUNTIME_PROOF_SCHEMA \
            or value.get("status") != "PASS" \
            or value.get("candidate") != dict(candidate) \
            or type(checks) is not dict or not checks \
            or any(item is not True for item in checks.values()) \
            or type(identity) is not dict \
            or identity.get("candidate") != dict(candidate) \
            or value.get("runtimeIdentityDigest") != _digest_bytes(_canonical(identity)) \
            or value.get("noDynamicProvisioningAudit", {}).get("pass") is not True \
            or value.get("noDynamicProvisioningAudit", {}).get("matches") != []:
        raise ValueError("runtime_proof_invalid")
    value["proofDigest"] = digest
    return value


def _validate_required_checks(path: pathlib.Path,
                              candidate_sha: str) -> Dict[str, Any]:
    value = _load(path)
    required, rows = value.get("requiredContexts"), value.get("checks")
    if value.get("schemaVersion") != CHECKS_SCHEMA \
            or value.get("candidateSha") != candidate_sha \
            or value.get("status") != "SUCCESS" \
            or not isinstance(required, list) or not required \
            or not isinstance(rows, list) or len(rows) != len(required) \
            or {row.get("name") for row in rows} != set(required) \
            or any(row.get("conclusion") != "success" for row in rows):
        raise ValueError("current_required_checks_invalid")
    return value


def generate(args: argparse.Namespace) -> Dict[str, Any]:
    _ensure_clean_candidate()
    candidate = _candidate(args.candidate_ref)
    manifest, contract = _validate_manifest(), _validate_contract()
    semantic = _validate_product_semantic_diff(args.candidate_ref)
    if _load(ROOT / "product-version.json") != {
            "schemaVersion": "argus-product-version-v1",
            "productVersion": PRODUCT_VERSION}:
        raise ValueError("product_version_not_v13_5")
    simulation_paths = [pathlib.Path(args.simulation_one),
                        pathlib.Path(args.simulation_two)]
    simulations = [_validate_simulation(path, ordinal, candidate)
                   for ordinal, path in enumerate(simulation_paths, 1)]
    runtime_paths = [pathlib.Path(args.runtime_proof_one),
                     pathlib.Path(args.runtime_proof_two)]
    runtimes = [_validate_runtime_proof(path, candidate) for path in runtime_paths]
    if runtimes[0]["runtimeIdentityDigest"] != runtimes[1]["runtimeIdentityDigest"]:
        raise ValueError("fresh_runner_runtime_identity_mismatch")
    required = _validate_required_checks(
        pathlib.Path(args.required_checks), candidate["commitSha"])
    runtime = runtimes[0]["runtimeIdentity"]
    body: Dict[str, Any] = {
        "schemaVersion": SCHEMA, "status": "PASS", "candidate": candidate,
        "productVersion": PRODUCT_VERSION,
        "acceptedV13Source": {"commitSha": ACCEPTED_V13_SOURCE,
                              "treeSha": ACCEPTED_V13_TREE},
        "acceptedFixManifestDigest": _digest_bytes(_canonical(manifest)),
        "acceptanceRuntime": {
            "identityDigest": runtimes[0]["runtimeIdentityDigest"],
            "specDigest": runtime["specDigest"],
            "seedImplementationDigest": runtime["seedImplementationDigest"],
            "container": runtime["container"], "browser": runtime["browser"],
            "nodeVersion": runtime["nodeVersion"],
            "playwrightVersion": runtime["playwrightVersion"]},
        "zeroInstallProofs": [{
            "runNumber": ordinal,
            "runtimeProofSha256": _digest_file(runtime_paths[ordinal - 1]),
            "simulationSha256": _digest_file(simulation_paths[ordinal - 1]),
            "runtimeIdentityDigest": runtimes[ordinal - 1]["runtimeIdentityDigest"],
            "initialSnapshotReady": 0,
            "snapshotReady": len(simulations[ordinal - 1]["businessSnapshots"]["observedSet"]),
            "responseSnapshotId": simulations[ordinal - 1]["canonical"]["responseSnapshotId"],
            "uiSnapshotId": simulations[ordinal - 1]["canonical"]["uiSnapshotId"],
            "status": "PASS"} for ordinal in (1, 2)],
        "noPostDeployInstall": True, "requiredChecks": required,
        "productSemanticDiff": semantic,
        "sourceDigests": {relative: _digest_file(ROOT / relative)
                          for relative in POLICY_INPUTS},
        "snapshotContractDigest": _digest_file(
            ROOT / "release/v13-snapshot-readiness-contract.json"),
        "stateMachineDigest": _digest_file(ROOT / "web/scripts/release-state-machine.mjs"),
        "tachibana": {"status": "PENDING", "authority": "NON_AUTHORITATIVE",
                       "dataStatus": "DATA_GATED", "blocking": False},
        "externalDataGates": ["tachibana", "direct_nikkei_topix", "1570",
                              "nikkei_valuation", "durable_vix_history",
                              "foreign_flow_archive", "earnings_sector_history",
                              "seven_sign_production_calibration"],
        "recovery": {"acceptance": "NOT_STARTED", "authoritative": False,
                     "acceptanceClockStarted": False},
        "policy": {"snapshotExpected": contract["snapshotExpected"],
                   "productionMutationAllowedOnlyAfterCertificate": True,
                   "oneProductionAttempt": True}}
    body["certificateDigest"] = _digest_bytes(_canonical(body))
    return body


def generate_admission(args: argparse.Namespace) -> Dict[str, Any]:
    """Build the pre-merge certificate without depending on its consumer gate."""
    _ensure_clean_candidate()
    candidate = _candidate(args.candidate_ref)
    manifest, contract = _validate_manifest(), _validate_contract()
    semantic = _validate_product_semantic_diff(args.candidate_ref)
    if _load(ROOT / "product-version.json") != {
            "schemaVersion": "argus-product-version-v1",
            "productVersion": PRODUCT_VERSION}:
        raise ValueError("product_version_not_v13_5")
    simulation_paths = [pathlib.Path(args.simulation_one),
                        pathlib.Path(args.simulation_two)]
    simulations = [_validate_simulation(path, ordinal, candidate)
                   for ordinal, path in enumerate(simulation_paths, 1)]
    runtime_paths = [pathlib.Path(args.runtime_proof_one),
                     pathlib.Path(args.runtime_proof_two)]
    runtimes = [_validate_runtime_proof(path, candidate) for path in runtime_paths]
    if runtimes[0]["runtimeIdentityDigest"] != runtimes[1]["runtimeIdentityDigest"]:
        raise ValueError("fresh_runner_runtime_identity_mismatch")
    runtime = runtimes[0]["runtimeIdentity"]
    body: Dict[str, Any] = {
        "schemaVersion": ADMISSION_SCHEMA,
        "status": "PASS",
        "candidate": candidate,
        "productVersion": PRODUCT_VERSION,
        "acceptedV13Source": {"commitSha": ACCEPTED_V13_SOURCE,
                              "treeSha": ACCEPTED_V13_TREE},
        "acceptedFixManifestDigest": _digest_bytes(_canonical(manifest)),
        "acceptanceRuntime": {
            "identityDigest": runtimes[0]["runtimeIdentityDigest"],
            "specDigest": runtime["specDigest"],
            "seedImplementationDigest": runtime["seedImplementationDigest"],
            "container": runtime["container"],
            "browser": runtime["browser"],
            "nodeVersion": runtime["nodeVersion"],
            "playwrightVersion": runtime["playwrightVersion"],
        },
        "zeroInstallProofs": [{
            "runNumber": ordinal,
            "runtimeProofSha256": _digest_file(runtime_paths[ordinal - 1]),
            "simulationSha256": _digest_file(simulation_paths[ordinal - 1]),
            "runtimeIdentityDigest": runtimes[ordinal - 1]["runtimeIdentityDigest"],
            "initialSnapshotReady": 0,
            "snapshotReady": len(
                simulations[ordinal - 1]["businessSnapshots"]["observedSet"]),
            "responseSnapshotId": simulations[ordinal - 1]["canonical"][
                "responseSnapshotId"],
            "uiSnapshotId": simulations[ordinal - 1]["canonical"]["uiSnapshotId"],
            "status": "PASS",
        } for ordinal in (1, 2)],
        "noPostDeployInstall": True,
        "productSemanticDiff": semantic,
        "sourceDigests": {relative: _digest_file(ROOT / relative)
                          for relative in POLICY_INPUTS},
        "snapshotContractDigest": _digest_file(
            ROOT / "release/v13-snapshot-readiness-contract.json"),
        "stateMachineDigest": _digest_file(
            ROOT / "web/scripts/release-state-machine.mjs"),
        "tachibana": {"status": "PENDING", "authority": "NON_AUTHORITATIVE",
                       "dataStatus": "DATA_GATED", "blocking": False},
        "recovery": {"acceptance": "NOT_STARTED", "authoritative": False,
                     "acceptanceClockStarted": False},
        "policy": {
            "snapshotExpected": contract["snapshotExpected"],
            "preMergeAdmissionRequired": True,
            "productionMutationAllowedOnlyAfterAdmission": True,
            "oneProductionAttempt": True,
        },
    }
    body["certificateDigest"] = _digest_bytes(_canonical(body))
    return body


def _validate_admission_identity(value: Mapping[str, Any],
                                 candidate: Mapping[str, str]) -> Dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("admission_certificate_json_object_required")
    certificate = dict(value)
    digest = certificate.pop("certificateDigest", None)
    runtime = certificate.get("acceptanceRuntime", {})
    proofs = certificate.get("zeroInstallProofs")
    expected_keys = {
        "schemaVersion", "status", "candidate", "productVersion",
        "acceptedV13Source", "acceptedFixManifestDigest", "acceptanceRuntime",
        "zeroInstallProofs", "noPostDeployInstall", "productSemanticDiff",
        "sourceDigests", "snapshotContractDigest", "stateMachineDigest",
        "tachibana", "recovery", "policy",
    }
    runtime_keys = {
        "identityDigest", "specDigest", "seedImplementationDigest", "container",
        "browser", "nodeVersion", "playwrightVersion",
    }
    proof_keys = {
        "runNumber", "runtimeProofSha256", "simulationSha256",
        "runtimeIdentityDigest", "initialSnapshotReady", "snapshotReady",
        "responseSnapshotId", "uiSnapshotId", "status",
    }
    proof_ordinals = [row.get("runNumber") if type(row) is dict else None
                      for row in proofs] if type(proofs) is list else []
    runtime_identity_digest = runtime.get("identityDigest") \
        if type(runtime) is dict else None
    valid_proofs = type(proofs) is list and len(proofs) == 2 \
        and proof_ordinals == [1, 2] \
        and all(
            type(row) is dict
            and set(row) == proof_keys
            and row.get("status") == "PASS"
            and row.get("initialSnapshotReady") == 0
            and row.get("snapshotReady") == 12
            and type(row.get("responseSnapshotId")) is str
            and bool(row.get("responseSnapshotId"))
            and row.get("responseSnapshotId") == row.get("uiSnapshotId")
            and _is_lower_hex(row.get("runtimeProofSha256"), 64)
            and _is_lower_hex(row.get("simulationSha256"), 64)
            and row.get("runtimeIdentityDigest") == runtime_identity_digest
            for row in proofs)
    if set(certificate) != expected_keys \
            or not _is_lower_hex(digest, 64) \
            or digest != _digest_bytes(_canonical(certificate)) \
            or certificate.get("schemaVersion") != ADMISSION_SCHEMA \
            or certificate.get("status") != "PASS" \
            or certificate.get("candidate") != dict(candidate) \
            or set(certificate.get("candidate", {})) != {"commitSha", "treeSha"} \
            or certificate.get("productVersion") != PRODUCT_VERSION \
            or certificate.get("acceptedV13Source") != {
                "commitSha": ACCEPTED_V13_SOURCE, "treeSha": ACCEPTED_V13_TREE} \
            or not _is_lower_hex(certificate.get("acceptedFixManifestDigest"), 64) \
            or type(runtime) is not dict or set(runtime) != runtime_keys \
            or not _is_lower_hex(runtime.get("identityDigest"), 64) \
            or not _is_lower_hex(runtime.get("specDigest"), 64) \
            or not _is_lower_hex(runtime.get("seedImplementationDigest"), 64) \
            or type(runtime.get("container")) is not dict \
            or type(runtime.get("browser")) is not dict \
            or type(runtime.get("nodeVersion")) is not str \
            or type(runtime.get("playwrightVersion")) is not str \
            or certificate.get("noPostDeployInstall") is not True \
            or certificate.get("policy", {}).get("preMergeAdmissionRequired") is not True \
            or certificate.get("policy", {}).get(
                "productionMutationAllowedOnlyAfterAdmission") is not True \
            or certificate.get("policy", {}).get("snapshotExpected") != 12 \
            or certificate.get("policy", {}).get("oneProductionAttempt") is not True \
            or not valid_proofs:
        raise ValueError("admission_certificate_identity_or_status")
    certificate["certificateDigest"] = digest
    return certificate


def _validate_retrieval_receipt(path: pathlib.Path,
                                certificate: Mapping[str, Any],
                                candidate: Mapping[str, str]) -> Dict[str, Any]:
    receipt = _load(path)
    digest = receipt.pop("receiptDigest", None)
    producer, artifact = receipt.get("producer"), receipt.get("artifact")
    if set(receipt) != {
            "schemaVersion", "status", "repository", "candidate",
            "certificateDigest", "transportAccept", "artifact", "producer",
            "consumerRunId", "consumerRunAttempt"} \
            or not _is_lower_hex(digest, 64) \
            or digest != _digest_bytes(_canonical(receipt)) \
            or receipt.get("schemaVersion") != RETRIEVAL_SCHEMA \
            or receipt.get("status") != "PASS" \
            or type(receipt.get("repository")) is not str \
            or not receipt.get("repository") \
            or receipt.get("candidate") != dict(candidate) \
            or receipt.get("certificateDigest") != certificate.get(
                "certificateDigest") \
            or receipt.get("transportAccept") != "application/vnd.github+json" \
            or type(producer) is not dict or type(artifact) is not dict \
            or set(producer) != {
                "workflowRunId", "runAttempt", "workflowPath", "event",
                "headSha", "conclusion"} \
            or set(artifact) != {
                "artifactId", "name", "artifactDigest"} \
            or producer.get("workflowPath") != \
                ".github/workflows/market-public-acceptance.yml" \
            or producer.get("event") != "pull_request" \
            or producer.get("headSha") != candidate["commitSha"] \
            or producer.get("conclusion") != "success" \
            or type(producer.get("workflowRunId")) is not int \
            or producer.get("workflowRunId") <= 0 \
            or type(producer.get("runAttempt")) is not int \
            or producer.get("runAttempt") <= 0 \
            or type(receipt.get("consumerRunId")) is not str \
            or not receipt.get("consumerRunId") \
            or type(receipt.get("consumerRunAttempt")) is not int \
            or receipt.get("consumerRunAttempt") <= 0 \
            or str(producer.get("workflowRunId")) == receipt.get("consumerRunId") \
            or type(artifact.get("artifactId")) is not int \
            or artifact.get("artifactId") <= 0 \
            or artifact.get("name") != (
                f"v13-5-premerge-admission-{candidate['commitSha']}-"
                f"{producer.get('workflowRunId')}-{producer.get('runAttempt')}") \
            or not _is_lower_hex(artifact.get("artifactDigest"), 64):
        raise ValueError("detached_certificate_retrieval_receipt_invalid")
    receipt["receiptDigest"] = digest
    return receipt


def verify_admission(args: argparse.Namespace) -> Dict[str, Any]:
    _ensure_clean_candidate()
    candidate = _candidate(args.candidate_ref)
    certificate = _validate_admission_identity(
        _load(pathlib.Path(args.certificate)), candidate)
    manifest = _validate_manifest()
    _validate_contract()
    source_path = getattr(args, "source_provenance", "")
    if not source_path:
        raise ValueError("source_provenance_receipt_required")
    source = source_provenance.validate_receipt(
        _load(pathlib.Path(source_path)),
        candidate_sha=candidate["commitSha"],
        candidate_tree=candidate["treeSha"],
        certificate_digest=certificate["certificateDigest"],
        release_merge_sha=getattr(args, "release_merge_sha", "") or None,
        release_merge_tree=getattr(args, "release_merge_tree", "") or None,
        repo=ROOT)
    semantic = source["semanticDiff"]
    if certificate.get("acceptedFixManifestDigest") != _digest_bytes(
            _canonical(manifest)):
        raise ValueError("admission_certificate_manifest_digest_mismatch")
    if certificate.get("sourceDigests") != {
            relative: _digest_file(ROOT / relative) for relative in POLICY_INPUTS}:
        raise ValueError("admission_certificate_policy_digest_mismatch")
    if certificate.get("snapshotContractDigest") != _digest_file(
            ROOT / "release/v13-snapshot-readiness-contract.json") \
            or certificate.get("stateMachineDigest") != _digest_file(
                ROOT / "web/scripts/release-state-machine.mjs"):
        raise ValueError("admission_certificate_release_digest_mismatch")
    if certificate.get("productSemanticDiff") != semantic:
        raise ValueError("admission_certificate_semantic_diff_mismatch")
    runtime = _validate_runtime_proof(pathlib.Path(args.runtime_proof), candidate)
    accepted = certificate.get("acceptanceRuntime", {})
    observed = runtime.get("runtimeIdentity", {})
    expected_runtime = {
        "identityDigest": runtime.get("runtimeIdentityDigest"),
        "specDigest": observed.get("specDigest"),
        "seedImplementationDigest": observed.get("seedImplementationDigest"),
        "container": observed.get("container"),
        "browser": observed.get("browser"),
        "nodeVersion": observed.get("nodeVersion"),
        "playwrightVersion": observed.get("playwrightVersion"),
    }
    if accepted != expected_runtime:
        raise ValueError("admission_certificate_runtime_identity_mismatch")
    if args.retrieval_receipt:
        _validate_retrieval_receipt(
            pathlib.Path(args.retrieval_receipt), certificate, candidate)
    return certificate


def verify(args: argparse.Namespace) -> Dict[str, Any]:
    _ensure_clean_candidate()
    candidate = _candidate(args.candidate_ref)
    certificate = _load(pathlib.Path(args.certificate))
    digest = certificate.pop("certificateDigest", None)
    if digest != _digest_bytes(_canonical(certificate)) \
            or certificate.get("schemaVersion") != SCHEMA \
            or certificate.get("status") != "PASS" \
            or certificate.get("candidate") != candidate \
            or certificate.get("productVersion") != PRODUCT_VERSION \
            or certificate.get("noPostDeployInstall") is not True:
        raise ValueError("certificate_identity_or_status")
    manifest = _validate_manifest()
    _validate_contract()
    _validate_product_semantic_diff(args.candidate_ref)
    if certificate.get("acceptedFixManifestDigest") != _digest_bytes(_canonical(manifest)):
        raise ValueError("certificate_manifest_digest_mismatch")
    if certificate.get("sourceDigests") != {
            relative: _digest_file(ROOT / relative) for relative in POLICY_INPUTS}:
        raise ValueError("certificate_policy_digest_mismatch")
    checks = certificate.get("requiredChecks", {})
    if checks.get("status") != "SUCCESS" or any(
            row.get("conclusion") != "success" for row in checks.get("checks", [])):
        raise ValueError("certificate_required_checks")
    if args.runtime_proof:
        runtime = _validate_runtime_proof(pathlib.Path(args.runtime_proof), candidate)
        accepted = certificate.get("acceptanceRuntime", {})
        if runtime.get("runtimeIdentityDigest") != accepted.get("identityDigest") \
                or runtime.get("runtimeIdentity", {}).get("seedImplementationDigest") \
                != accepted.get("seedImplementationDigest"):
            raise ValueError("certificate_runtime_identity_mismatch")
    certificate["certificateDigest"] = digest
    return certificate


def _api(url: str, token: str, *, accept: str = "application/vnd.github+json") -> bytes:
    headers = {"Accept": accept, "User-Agent": "argus-v13-5-release-control/1",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        opener = urllib.request.build_opener(_StripCrossOriginAuthorization())
        with opener.open(urllib.request.Request(url, headers=headers),
                         timeout=60) as response:
            body = response.read(64 * 1024 * 1024 + 1)
            if len(body) > 64 * 1024 * 1024:
                raise ValueError("github_api_response_too_large")
            return body
    except urllib.error.HTTPError as exc:
        detail = exc.read(4097)
        if len(detail) > 4096:
            detail = detail[:4096]
        try:
            message = json.loads(detail).get("message", "")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            message = detail.decode("utf-8", "replace")
        raise ValueError(f"github_http_error:{exc.code}:{str(message)[:240]}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"github_transport_error:{str(exc.reason)[:240]}") from exc


def _api_json(url: str, token: str) -> Any:
    return json.loads(_api(url, token))


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise ValueError("github_token_missing")
    return token


def _artifact_digest(row: Mapping[str, Any]) -> str:
    value = row.get("digest")
    if type(value) is not str or not value.startswith("sha256:") \
            or not _is_lower_hex(value[7:], 64):
        raise ValueError("detached_certificate_artifact_digest_invalid")
    return value[7:]


def _certificate_binding(certificate: Mapping[str, Any]) -> Dict[str, Any]:
    runtime = certificate.get("acceptanceRuntime", {})
    proofs = certificate.get("zeroInstallProofs", [])
    simulations = [{
        "runNumber": row.get("runNumber"),
        "runtimeProofSha256": row.get("runtimeProofSha256"),
        "simulationSha256": row.get("simulationSha256"),
        "runtimeIdentityDigest": row.get("runtimeIdentityDigest"),
    } for row in proofs if type(row) is dict]
    if not _is_lower_hex(certificate.get("certificateDigest"), 64) \
            or not _is_lower_hex(runtime.get("identityDigest"), 64) \
            or len(simulations) != 2 \
            or [row.get("runNumber") for row in simulations] != [1, 2] \
            or any(not _is_lower_hex(row.get("runtimeProofSha256"), 64)
                   or not _is_lower_hex(row.get("simulationSha256"), 64)
                   or row.get("runtimeIdentityDigest") != runtime.get("identityDigest")
                   for row in simulations):
        raise ValueError("detached_certificate_binding_invalid")
    return {
        "certificateDigest": certificate["certificateDigest"],
        "runtimeIdentityDigest": runtime["identityDigest"],
        "simulationBindings": simulations,
    }


def _download_admission_certificate(
        *, repo: str, token: str, artifact: Mapping[str, Any],
        candidate: Mapping[str, str]) -> tuple[Dict[str, Any], bytes]:
    artifact_id, archive_url = artifact.get("id"), artifact.get(
        "archive_download_url")
    expected_url = f"https://api.github.com/repos/{repo}/actions/artifacts/" \
        f"{artifact_id}/zip"
    if type(artifact_id) is not int or type(archive_url) is not str \
            or archive_url != expected_url:
        raise ValueError("detached_certificate_artifact_identity_invalid")
    expected_digest = _artifact_digest(artifact)
    archive = _api(archive_url, token)
    if not archive:
        raise ValueError("detached_certificate_archive_empty")
    if _digest_bytes(archive) != expected_digest:
        raise ValueError("detached_certificate_artifact_digest_mismatch")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            matches = [entry for entry in bundle.infolist()
                       if not entry.is_dir()
                       and pathlib.PurePosixPath(entry.filename).name
                       == "certificate.json"]
            if len(matches) != 1:
                raise ValueError(
                    f"detached_certificate_archive_shape:{len(matches)}")
            if matches[0].file_size > 1024 * 1024:
                raise ValueError("detached_certificate_payload_too_large")
            raw_certificate = bundle.read(matches[0])
    except zipfile.BadZipFile as exc:
        raise ValueError("detached_certificate_archive_invalid") from exc
    if not raw_certificate:
        raise ValueError("detached_certificate_payload_empty")
    try:
        value = json.loads(raw_certificate)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("detached_certificate_payload_malformed") from exc
    return _validate_admission_identity(value, candidate), archive


def _run_id_from_details_url(details_url: Any, repo: str) -> int:
    if type(details_url) is not str:
        raise ValueError("detached_certificate_authority_details_url_invalid")
    parsed = urllib.parse.urlsplit(details_url)
    parts = parsed.path.strip("/").split("/")
    owner_repo = repo.split("/", 1)
    if parsed.scheme != "https" or parsed.netloc != "github.com" \
            or len(owner_repo) != 2 or len(parts) != 7 \
            or parts[:4] != [*owner_repo, "actions", "runs"] \
            or parts[5] != "job" or not parts[4].isdigit() \
            or not parts[6].isdigit() or parsed.fragment:
        raise ValueError("detached_certificate_authority_details_url_invalid")
    return int(parts[4])


def _validate_producer_run(
        producer: Any, *, run_id: int, candidate_sha: str,
        expected_workflow: str) -> Dict[str, Any]:
    if type(producer) is not dict \
            or producer.get("id") != run_id \
            or type(producer.get("run_attempt")) is not int \
            or producer.get("run_attempt") <= 0 \
            or producer.get("status") != "completed" \
            or producer.get("conclusion") != "success" \
            or producer.get("event") != "pull_request" \
            or producer.get("head_sha") != candidate_sha \
            or producer.get("path") != expected_workflow:
        raise ValueError("detached_certificate_producer_run_invalid")
    return producer


def _artifact_for_run(
        *, repo: str, token: str, producer: Mapping[str, Any],
        candidate_sha: str) -> Dict[str, Any]:
    run_id, run_attempt = producer["id"], producer["run_attempt"]
    name = f"v13-5-premerge-admission-{candidate_sha}-{run_id}-{run_attempt}"
    query = urllib.parse.urlencode({"name": name, "per_page": 100})
    payload = _api_json(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts?{query}",
        token)
    if type(payload) is not dict or type(payload.get("artifacts")) is not list:
        raise ValueError("detached_certificate_artifact_response_invalid")
    exact = [row for row in payload["artifacts"] if type(row) is dict
             and row.get("name") == name and row.get("expired") is False
             and row.get("workflow_run", {}).get("id") == run_id
             and row.get("workflow_run", {}).get("head_sha") == candidate_sha]
    if len(exact) != 1:
        raise ValueError(f"detached_certificate_bound_artifact_count:{len(exact)}")
    _artifact_digest(exact[0])
    return exact[0]


def collect_authority(args: argparse.Namespace) -> Dict[str, Any]:
    """Resolve one check-run authority into a content-addressed producer pointer."""
    token = _token()
    candidate = {"commitSha": args.candidate_sha, "treeSha": args.candidate_tree}
    if not _is_lower_hex(args.candidate_sha, 40) \
            or not _is_lower_hex(args.candidate_tree, 40):
        raise ValueError("detached_certificate_candidate_identity_invalid")
    check = None
    if args.required_checks:
        current = _validate_required_checks(
            pathlib.Path(args.required_checks), args.candidate_sha)
        matches = [row for row in current["checks"]
                   if row.get("name") == args.authority_context]
        if len(matches) != 1:
            raise ValueError(
                f"detached_certificate_authority_check_count:{len(matches)}")
        row = matches[0]
        check = {
            "id": row.get("checkRunId"),
            "status": row.get("status"),
            "conclusion": row.get("conclusion"),
            "details_url": row.get("detailsUrl"),
        }
    else:
        deadline = time.monotonic() + args.timeout_seconds
        while True:
            query = urllib.parse.urlencode({"per_page": 100, "filter": "latest"})
            payload = _api_json(
                f"https://api.github.com/repos/{args.repo}/commits/"
                f"{args.candidate_sha}/check-runs?{query}", token)
            rows = [row for row in payload.get("check_runs", [])
                    if type(row) is dict
                    and row.get("name") == args.authority_context]
            rows.sort(key=lambda row: (
                str(row.get("completed_at") or row.get("started_at") or ""),
                int(row.get("id") or 0)), reverse=True)
            if rows and rows[0].get("status") == "completed" \
                    and rows[0].get("conclusion") == "success":
                check = rows[0]
                break
            if time.monotonic() >= deadline:
                raise ValueError("detached_certificate_authority_check_not_ready")
            time.sleep(args.poll_seconds)
    if type(check) is not dict or type(check.get("id")) is not int \
            or check.get("status") != "completed" \
            or check.get("conclusion") != "success":
        raise ValueError("detached_certificate_authority_check_invalid")
    details_url = check.get("detailsUrl", check.get("details_url"))
    run_id = _run_id_from_details_url(details_url, args.repo)
    producer = _validate_producer_run(_api_json(
        f"https://api.github.com/repos/{args.repo}/actions/runs/{run_id}", token),
        run_id=run_id, candidate_sha=args.candidate_sha,
        expected_workflow=args.expected_producer_workflow)
    artifact = _artifact_for_run(
        repo=args.repo, token=token, producer=producer,
        candidate_sha=args.candidate_sha)
    certificate, archive = _download_admission_certificate(
        repo=args.repo, token=token, artifact=artifact, candidate=candidate)
    value: Dict[str, Any] = {
        "schemaVersion": AUTHORITY_SCHEMA,
        "status": "PASS",
        "repository": args.repo,
        "candidate": candidate,
        "check": {
            "context": args.authority_context,
            "checkRunId": check["id"],
            "detailsUrl": details_url,
            "conclusion": check["conclusion"],
        },
        "producer": {
            "workflowRunId": producer["id"],
            "runAttempt": producer["run_attempt"],
            "workflowPath": producer["path"],
            "event": producer["event"],
            "headSha": producer["head_sha"],
            "conclusion": producer["conclusion"],
        },
        "artifact": {
            "artifactId": artifact["id"],
            "name": artifact["name"],
            "artifactDigest": _digest_bytes(archive),
        },
        "certificate": _certificate_binding(certificate),
    }
    value["authorityDigest"] = _digest_bytes(_canonical(value))
    return value


def collect_checks(args: argparse.Namespace) -> Dict[str, Any]:
    token = _token()
    rules = _api_json(f"https://api.github.com/repos/{args.repo}/rules/branches/main", token)
    required, ruleset_ids = [], set()
    for rule in rules:
        if rule.get("type") == "required_status_checks":
            ruleset_ids.add(rule.get("ruleset_id"))
            required.extend(row.get("context") for row in
                            rule.get("parameters", {}).get("required_status_checks", []))
    required = sorted({value for value in required if type(value) is str and value})
    if not required:
        raise ValueError("current_required_contexts_empty")
    deadline, selected = time.monotonic() + args.timeout_seconds, {}
    while True:
        query = urllib.parse.urlencode({"per_page": 100, "filter": "latest"})
        payload = _api_json(f"https://api.github.com/repos/{args.repo}/commits/"
                            f"{args.candidate_sha}/check-runs?{query}", token)
        grouped: Dict[str, list[Mapping[str, Any]]] = {}
        for row in payload.get("check_runs", []):
            if row.get("name") in required:
                grouped.setdefault(row["name"], []).append(row)
        selected = {}
        for name, rows in grouped.items():
            rows.sort(key=lambda row: (str(row.get("completed_at")
                                           or row.get("started_at") or ""),
                                       int(row.get("id") or 0)), reverse=True)
            selected[name] = rows[0]
        if all(selected.get(name, {}).get("status") == "completed"
               and selected[name].get("conclusion") == "success" for name in required):
            break
        if time.monotonic() >= deadline:
            raise ValueError("current_required_not_success:" + json.dumps({
                name: {"status": selected.get(name, {}).get("status", "missing"),
                       "conclusion": selected.get(name, {}).get("conclusion")}
                for name in required}, sort_keys=True))
        time.sleep(10)
    return {"schemaVersion": CHECKS_SCHEMA, "status": "SUCCESS",
            "candidateSha": args.candidate_sha, "requiredContexts": required,
            "rulesetIds": sorted(value for value in ruleset_ids if isinstance(value, int)),
            "checks": [{"name": name, "status": selected[name]["status"],
                        "conclusion": selected[name]["conclusion"],
                        "checkRunId": selected[name]["id"],
                        "detailsUrl": selected[name].get("details_url"),
                        "completedAt": selected[name].get("completed_at")}
                       for name in required]}


def fetch(args: argparse.Namespace) -> Dict[str, Any]:
    token, name = _token(), f"v13-5-release-proof-{args.candidate_sha}"
    query = urllib.parse.urlencode({"name": name, "per_page": 100})
    payload = _api_json(f"https://api.github.com/repos/{args.repo}/actions/artifacts?{query}", token)
    rows = [row for row in payload.get("artifacts", [])
            if row.get("name") == name and row.get("expired") is False
            and row.get("workflow_run", {}).get("head_sha") == args.candidate_sha]
    if len(rows) != 1:
        raise ValueError(f"exact_release_certificate_artifact_count:{len(rows)}")
    archive = _api(rows[0]["archive_download_url"], token)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        matches = [entry for entry in bundle.namelist()
                   if pathlib.PurePosixPath(entry).name == "certificate.json"]
        if len(matches) != 1:
            raise ValueError("release_certificate_archive_shape")
        value = json.loads(bundle.read(matches[0]))
    if type(value) is not dict or value.get("candidate", {}).get("commitSha") \
            != args.candidate_sha:
        raise ValueError("release_certificate_artifact_candidate_mismatch")
    return value


def _validate_authority(path: pathlib.Path,
                        candidate: Mapping[str, str]) -> Dict[str, Any]:
    value = _load(path)
    digest = value.pop("authorityDigest", None)
    producer, artifact = value.get("producer"), value.get("artifact")
    check, bound = value.get("check"), value.get("certificate")
    if set(value) != {
            "schemaVersion", "status", "repository", "candidate", "check",
            "producer", "artifact", "certificate"} \
            or not _is_lower_hex(digest, 64) \
            or digest != _digest_bytes(_canonical(value)) \
            or value.get("schemaVersion") != AUTHORITY_SCHEMA \
            or value.get("status") != "PASS" \
            or type(value.get("repository")) is not str \
            or value.get("candidate") != dict(candidate) \
            or type(check) is not dict or set(check) != {
                "context", "checkRunId", "detailsUrl", "conclusion"} \
            or check.get("context") != "proof-certificate" \
            or type(check.get("checkRunId")) is not int \
            or check.get("checkRunId") <= 0 \
            or check.get("conclusion") != "success" \
            or type(producer) is not dict or set(producer) != {
                "workflowRunId", "runAttempt", "workflowPath", "event",
                "headSha", "conclusion"} \
            or producer.get("workflowPath") != \
                ".github/workflows/market-public-acceptance.yml" \
            or producer.get("event") != "pull_request" \
            or producer.get("headSha") != candidate["commitSha"] \
            or producer.get("conclusion") != "success" \
            or type(producer.get("workflowRunId")) is not int \
            or producer.get("workflowRunId") <= 0 \
            or type(producer.get("runAttempt")) is not int \
            or producer.get("runAttempt") <= 0 \
            or _run_id_from_details_url(check.get("detailsUrl"),
                                        value["repository"]) != \
                producer.get("workflowRunId") \
            or type(artifact) is not dict or set(artifact) != {
                "artifactId", "name", "artifactDigest"} \
            or type(artifact.get("artifactId")) is not int \
            or artifact.get("artifactId") <= 0 \
            or artifact.get("name") != (
                f"v13-5-premerge-admission-{candidate['commitSha']}-"
                f"{producer.get('workflowRunId')}-{producer.get('runAttempt')}") \
            or not _is_lower_hex(artifact.get("artifactDigest"), 64) \
            or type(bound) is not dict \
            or set(bound) != {
                "certificateDigest", "runtimeIdentityDigest",
                "simulationBindings"}:
        raise ValueError("detached_certificate_authority_invalid")
    simulations = bound.get("simulationBindings")
    if not _is_lower_hex(bound.get("certificateDigest"), 64) \
            or not _is_lower_hex(bound.get("runtimeIdentityDigest"), 64) \
            or type(simulations) is not list or len(simulations) != 2 \
            or [row.get("runNumber") for row in simulations
                if type(row) is dict] != [1, 2] \
            or any(type(row) is not dict or set(row) != {
                "runNumber", "runtimeProofSha256", "simulationSha256",
                "runtimeIdentityDigest"}
                or not _is_lower_hex(row.get("runtimeProofSha256"), 64)
                or not _is_lower_hex(row.get("simulationSha256"), 64)
                or row.get("runtimeIdentityDigest") !=
                bound.get("runtimeIdentityDigest") for row in simulations):
        raise ValueError("detached_certificate_authority_binding_invalid")
    value["authorityDigest"] = digest
    return value


def fetch_admission(args: argparse.Namespace) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Fetch only the artifact named by an explicit producer authority."""
    token = _token()
    candidate = {"commitSha": args.candidate_sha, "treeSha": args.candidate_tree}
    if not _is_lower_hex(args.candidate_sha, 40) \
            or not _is_lower_hex(args.candidate_tree, 40) \
            or not args.consumer_run_id \
            or type(args.consumer_run_attempt) is not int \
            or args.consumer_run_attempt <= 0:
        raise ValueError("detached_certificate_candidate_identity_invalid")
    if not args.producer_authority:
        raise ValueError("detached_certificate_explicit_authority_required")
    authority = _validate_authority(
        pathlib.Path(args.producer_authority), candidate)
    producer_pointer, artifact_pointer = (
        authority["producer"], authority["artifact"])
    run_id = producer_pointer["workflowRunId"]
    producer = _validate_producer_run(_api_json(
        f"https://api.github.com/repos/{args.repo}/actions/runs/{run_id}", token),
        run_id=run_id, candidate_sha=args.candidate_sha,
        expected_workflow=args.expected_producer_workflow)
    if producer["run_attempt"] != producer_pointer["runAttempt"]:
        raise ValueError("detached_certificate_producer_attempt_mismatch")
    if str(producer["id"]) == args.consumer_run_id:
        raise ValueError("detached_certificate_not_detached")
    artifact_id = artifact_pointer["artifactId"]
    row = _api_json(
        f"https://api.github.com/repos/{args.repo}/actions/artifacts/{artifact_id}",
        token)
    if type(row) is not dict \
            or row.get("id") != artifact_id \
            or row.get("name") != artifact_pointer["name"] \
            or row.get("expired") is not False \
            or row.get("workflow_run", {}).get("id") != run_id \
            or row.get("workflow_run", {}).get("head_sha") != args.candidate_sha:
        raise ValueError("detached_certificate_artifact_not_bound_to_producer")
    if _artifact_digest(row) != artifact_pointer["artifactDigest"]:
        raise ValueError("detached_certificate_artifact_digest_mismatch")
    certificate, archive = _download_admission_certificate(
        repo=args.repo, token=token, artifact=row, candidate=candidate)
    if _digest_bytes(archive) != artifact_pointer["artifactDigest"]:
        raise ValueError("detached_certificate_artifact_digest_mismatch")
    if _certificate_binding(certificate) != authority["certificate"]:
        raise ValueError("detached_certificate_authorized_binding_mismatch")
    receipt: Dict[str, Any] = {
        "schemaVersion": RETRIEVAL_SCHEMA,
        "status": "PASS",
        "repository": args.repo,
        "candidate": candidate,
        "certificateDigest": certificate["certificateDigest"],
        "transportAccept": "application/vnd.github+json",
        "artifact": {
            "artifactId": artifact_id,
            "name": row["name"],
            "artifactDigest": _digest_bytes(archive),
        },
        "producer": {
            "workflowRunId": producer["id"],
            "runAttempt": producer["run_attempt"],
            "workflowPath": producer["path"],
            "event": producer["event"],
            "headSha": producer["head_sha"],
            "conclusion": producer["conclusion"],
        },
        "consumerRunId": args.consumer_run_id,
        "consumerRunAttempt": args.consumer_run_attempt,
    }
    receipt["receiptDigest"] = _digest_bytes(_canonical(receipt))
    return certificate, receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("generate")
    for name in ("simulation-one", "simulation-two", "runtime-proof-one",
                 "runtime-proof-two", "required-checks", "out"):
        create.add_argument(f"--{name}", required=True)
    create.add_argument("--candidate-ref", default="HEAD")
    create_admission = sub.add_parser("generate-admission")
    for name in ("simulation-one", "simulation-two", "runtime-proof-one",
                 "runtime-proof-two", "out"):
        create_admission.add_argument(f"--{name}", required=True)
    create_admission.add_argument("--candidate-ref", default="HEAD")
    check = sub.add_parser("verify")
    check.add_argument("--certificate", required=True)
    check.add_argument("--candidate-ref", default="HEAD")
    check.add_argument("--runtime-proof", default="")
    admit = sub.add_parser("verify-admission")
    admit.add_argument("--certificate", required=True)
    admit.add_argument("--candidate-ref", default="HEAD")
    admit.add_argument("--runtime-proof", required=True)
    admit.add_argument("--retrieval-receipt", default="")
    admit.add_argument("--source-provenance", required=True)
    admit.add_argument("--release-merge-sha", default="")
    admit.add_argument("--release-merge-tree", default="")
    collect = sub.add_parser("collect-checks")
    collect.add_argument("--repo", required=True)
    collect.add_argument("--candidate-sha", required=True)
    collect.add_argument("--timeout-seconds", type=int, default=1500)
    collect.add_argument("--out", required=True)
    authority = sub.add_parser("collect-authority")
    authority.add_argument("--repo", required=True)
    authority.add_argument("--candidate-sha", required=True)
    authority.add_argument("--candidate-tree", required=True)
    authority.add_argument("--authority-context", default="proof-certificate")
    authority.add_argument("--expected-producer-workflow", default=
                           ".github/workflows/market-public-acceptance.yml")
    authority.add_argument("--required-checks", default="")
    authority.add_argument("--timeout-seconds", type=int, default=1500)
    authority.add_argument("--poll-seconds", type=int, default=10)
    authority.add_argument("--out", required=True)
    get = sub.add_parser("fetch")
    get.add_argument("--repo", required=True)
    get.add_argument("--candidate-sha", required=True)
    get.add_argument("--out", required=True)
    get_admission = sub.add_parser("fetch-admission")
    get_admission.add_argument("--repo", required=True)
    get_admission.add_argument("--candidate-sha", required=True)
    get_admission.add_argument("--candidate-tree", required=True)
    get_admission.add_argument("--consumer-run-id", required=True)
    get_admission.add_argument("--consumer-run-attempt", type=int, required=True)
    get_admission.add_argument("--producer-authority", required=True)
    get_admission.add_argument("--expected-producer-workflow", default=
                               ".github/workflows/market-public-acceptance.yml")
    get_admission.add_argument("--timeout-seconds", type=int, default=1500)
    get_admission.add_argument("--poll-seconds", type=int, default=10)
    get_admission.add_argument("--out", required=True)
    get_admission.add_argument("--receipt-out", required=True)
    args = parser.parse_args()
    if args.command == "generate":
        _write(pathlib.Path(args.out), generate(args))
    elif args.command == "generate-admission":
        _write(pathlib.Path(args.out), generate_admission(args))
    elif args.command == "verify":
        verify(args)
    elif args.command == "verify-admission":
        verify_admission(args)
    elif args.command == "collect-checks":
        if not 1 <= args.timeout_seconds <= 3600:
            raise ValueError("invalid_required_checks_timeout")
        _write(pathlib.Path(args.out), collect_checks(args))
    elif args.command == "collect-authority":
        if not 1 <= args.timeout_seconds <= 3600 \
                or not 1 <= args.poll_seconds <= 60:
            raise ValueError("invalid_detached_certificate_authority_timing")
        _write(pathlib.Path(args.out), collect_authority(args))
    elif args.command == "fetch":
        _write(pathlib.Path(args.out), fetch(args))
    else:
        if not 1 <= args.timeout_seconds <= 3600:
            raise ValueError("invalid_detached_certificate_fetch_timing")
        certificate, receipt = fetch_admission(args)
        _write(pathlib.Path(args.out), certificate)
        _write(pathlib.Path(args.receipt_out), receipt)
    print(f"V13_5_RELEASE_CERTIFICATE_{args.command.upper().replace('-', '_')}=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
