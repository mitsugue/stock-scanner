#!/usr/bin/env python3
"""Exact-head, fail-closed admission for Formal Recovery pull requests.

This module is deliberately independent from the product release certificate.
It classifies the exact base..candidate diff, admits only the frozen Recovery
payload plus the narrow admission-plane files in this change, and creates a
deterministic certificate bound to the candidate commit, tree, classification,
and test evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Mapping, Sequence
import xml.etree.ElementTree as ET


CLASSIFICATION_SCHEMA = "argus-recovery-admission-classification-v1"
EVIDENCE_SCHEMA = "argus-recovery-admission-evidence-v1"
CERTIFICATE_SCHEMA = "argus-recovery-admission-certificate-v1"
AUTHORITY_SCHEMA = "argus-recovery-admission-authority-v1"
RETRIEVAL_SCHEMA = "argus-recovery-admission-retrieval-v1"
POLICY_SCHEMA = "argus-recovery-admission-policy-v1"
RECOVERY_WORKFLOW_PATH = ".github/workflows/market-public-acceptance.yml"
RECOVERY_CHECK_CONTEXT = "recovery-certificate"
SHA_RE = re.compile(r"[0-9a-f]{40}", re.ASCII)
TREE_RE = SHA_RE

# These are the reviewed Recovery implementation and evidence paths for the
# exact candidate. Their base..head patch bytes must retain the pinned digest
# below; a path allowlist alone is not sufficient authority.
RECOVERY_PAYLOAD_PATHS = (
    ".github/workflows/caos-scan.yml",
    ".github/workflows/caos-watchtower.yml",
    ".github/workflows/checkpoint-v2-gate.yml",
    ".github/workflows/memory-attribution.yml",
    "argus_recovery_phase_a_adapter.py",
    "argus_remote_journal.py",
    "argus_remote_receipt_queue.py",
    "argus_remote_recovery.py",
    "argus_remote_recovery_limits.py",
    "argus_route_catalog.py",
    "docs/EC2_MISSION_SCHEDULER.md",
    "docs/ops/permanent-scheduler-identity-and-soak.md",
    "docs/ops/recovery-phase-a-integration.md",
    "ops/systemd/argus-remote-journal-rearm.service",
    "ops/systemd/argus-remote-journal-rearm.timer",
    "ops/systemd/argus-watchtower-writer.service",
    "ops/systemd/argus-watchtower-writer.timer",
    "scanner.py",
    "scripts/install_argus_mission_timer.sh",
    "scripts/install_argus_remote_journal_rearm.sh",
    "scripts/install_argus_watchtower_writer.sh",
    "scripts/argus_remote_journal_rearm.py",
    "scripts/prepare_remote_journal_publish.py",
    "scripts/argus_watchtower_writer_dispatch.py",
    "scripts/remote_journal_publish_policy.py",
    "scripts/remote_receipt_drain.py",
    "scripts/workflow_http.py",
    "test_argus_checkpoint_v2_isolated.py",
    "test_argus_persistent_mission_storage.py",
    "test_argus_public_operational_boundary.py",
    "test_argus_recovery_phase_a_adapter.py",
    "test_argus_identity_installer.py",
    "test_argus_v12_3_2.py",
    "test_argus_v13_4_2_remote_receipts.py",
    "test_caos_workflow_recovery.py",
    "test_remote_journal_rearm.py",
    "test_remote_receipt_drain.py",
    "test_remote_recovery_nonce_bootstrap.py",
    "test_remote_recovery_publish.py",
    "test_remote_recovery_restore.py",
)
EXPECTED_RECOVERY_PAYLOAD_DIFF_SHA256 = (
    "617b068248bf4ca88ad0f2fa2bcad7dcf6d74e7bb560f1177eb831d1ef270229"
)

# Admission-plane files may route and prove Recovery, but are not production
# Recovery payload.  Without the pinned Recovery payload they stay on the
# existing product-certificate route; they can never self-select Recovery.
RECOVERY_ADMISSION_PATHS = (
    ".github/workflows/market-public-acceptance.yml",
    ".github/workflows/release-gate.yml",
    "scripts/recovery_admission.py",
    "test_recovery_admission.py",
)

# The complete-suite CI harness may accompany a pinned Recovery payload when
# its bounded execution contract needs correction.  It is admission evidence,
# never production Recovery payload, and cannot self-select Recovery without a
# matching pinned payload diff.
RECOVERY_CI_HARNESS_PATHS = (
    ".github/workflows/ci.yml",
)
RECOVERY_CLASSIFICATION_SUPPORT_PATHS = tuple(sorted(
    set(RECOVERY_ADMISSION_PATHS).union(RECOVERY_CI_HARNESS_PATHS)
))

AUTHORITY_ASSERTIONS = {
    "acceptanceClockStarted": False,
    "branchProtectionBypassed": False,
    "decisionAuthorityMutated": False,
    "fullGenerationAuthorityEnabled": False,
    "hardRpoClaimPermitted": False,
    "legacyRestoreAuthorityRetired": False,
    "measurementEnabled": False,
    "ownerPrivatePayloadExpanded": False,
    "productVersionChanged": False,
    "sdaAuthorityMutated": False,
    "soakStarted": False,
    "stage1Enabled": False,
    "v2RestoreAuthorityEnabled": False,
    "walV2AuthorityEnabled": False,
}


class AdmissionError(RuntimeError):
    """Stable, fail-closed admission failure."""


class _StripCrossOriginAuthorization(urllib.request.HTTPRedirectHandler):
    """Keep GitHub auth on GitHub, never forward it to signed blob URLs."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(
            req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if old.scheme == "https" and new.scheme != "https":
            raise AdmissionError("github_redirect_insecure")
        if old.netloc != new.netloc:
            for values in (redirected.headers, redirected.unredirected_hdrs):
                for name in list(values):
                    if name.lower() == "authorization":
                        del values[name]
        return redirected


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    return _digest_bytes(_canonical(value))


def _load(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdmissionError(f"json_unreadable:{path.name}") from exc
    if type(value) is not dict:
        raise AdmissionError(f"json_object_required:{path.name}")
    return value


def _write(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _git(repo: pathlib.Path, *args: str, binary: bool = False,
         check: bool = True) -> str | bytes:
    process = subprocess.run(
        ["git", "-C", str(repo), *args], check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if check and process.returncode:
        reason = process.stderr.decode("utf-8", "replace").strip()[:300]
        raise AdmissionError(f"git_command_failed:{args[0]}:{reason}")
    if binary:
        return process.stdout
    return process.stdout.decode("utf-8", "strict").strip()


def _resolve(repo: pathlib.Path, ref: str, kind: str) -> str:
    if kind not in {"commit", "tree"}:
        raise AdmissionError("git_object_kind_invalid")
    object_ref = ref + "^{" + kind + "}"
    value = str(_git(repo, "rev-parse", "--verify", object_ref))
    if not SHA_RE.fullmatch(value):
        raise AdmissionError(f"{kind}_identity_invalid")
    return value


def _path_entries(repo: pathlib.Path, base: str,
                  head: str) -> list[dict[str, str]]:
    raw = _git(
        repo, "diff", "--name-status", "-z", "--no-renames",
        base, head, binary=True,
    )
    assert isinstance(raw, bytes)
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise AdmissionError("changed_path_stream_invalid")
    rows: list[dict[str, str]] = []
    for offset in range(0, len(fields), 2):
        try:
            status = fields[offset].decode("ascii")
            path = fields[offset + 1].decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise AdmissionError("changed_path_encoding_invalid") from exc
        if status not in {"A", "M", "D"}:
            raise AdmissionError(f"changed_path_status_invalid:{status}")
        if not path or path.startswith("/") or "\x00" in path or \
                pathlib.PurePosixPath(path).is_absolute() or \
                ".." in pathlib.PurePosixPath(path).parts:
            raise AdmissionError("changed_path_invalid")
        rows.append({"status": status, "path": path})
    rows.sort(key=lambda row: (row["path"], row["status"]))
    if not rows or len({row["path"] for row in rows}) != len(rows):
        raise AdmissionError("changed_paths_empty_or_duplicate")
    return rows


def _patch_bytes(repo: pathlib.Path, base: str, head: str,
                 paths: Sequence[str]) -> bytes:
    if not paths:
        return b""
    value = _git(
        repo, "diff", "--binary", "--full-index", "--no-ext-diff",
        "--no-renames", base, head, "--", *sorted(paths), binary=True,
    )
    assert isinstance(value, bytes)
    return value


def _blob(repo: pathlib.Path, commit: str, path: str) -> bytes:
    value = _git(repo, "show", f"{commit}:{path}", binary=True)
    assert isinstance(value, bytes)
    return value


def _product_version(repo: pathlib.Path, commit: str) -> dict[str, Any]:
    try:
        value = json.loads(_blob(repo, commit, "product-version.json"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AdmissionError("product_version_invalid") from exc
    if type(value) is not dict or value.get("schemaVersion") != \
            "argus-product-version-v1" or \
            type(value.get("productVersion")) is not str:
        raise AdmissionError("product_version_invalid")
    return value


def scope_policy_document() -> dict[str, Any]:
    return {
        "schemaVersion": POLICY_SCHEMA,
        "admissionPaths": list(RECOVERY_CLASSIFICATION_SUPPORT_PATHS),
        "admissionWithoutRecoveryPolicy":
            "EXISTING_PRODUCT_CERTIFICATE_REQUIRED",
        "expectedRecoveryPayloadDiffSha256":
            EXPECTED_RECOVERY_PAYLOAD_DIFF_SHA256,
        "mixedPolicy": "DENY",
        "productPolicy": "EXISTING_PRODUCT_CERTIFICATE_REQUIRED",
        "recoveryOnlyPolicy": "EXACT_RECOVERY_CERTIFICATE_REQUIRED",
        "recoveryPayloadPaths": list(RECOVERY_PAYLOAD_PATHS),
        "unknownPathPolicy": "PRODUCT_OR_MIXED_NEVER_RECOVERY_ONLY",
    }


def _validate_digest_document(value: Mapping[str, Any], field: str) -> None:
    digest = value.get(field)
    body = dict(value)
    body.pop(field, None)
    if type(digest) is not str or len(digest) != 64 or \
            digest != _digest(body):
        raise AdmissionError(f"{field}_invalid")


def classify_repository(
        repo: pathlib.Path, base_ref: str, head_ref: str, *,
        expected_payload_digest: str =
        EXPECTED_RECOVERY_PAYLOAD_DIFF_SHA256) -> dict[str, Any]:
    repo = repo.resolve()
    base = _resolve(repo, base_ref, "commit")
    head = _resolve(repo, head_ref, "commit")
    if base == head:
        raise AdmissionError("candidate_equals_base")
    ancestor = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", base, head],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if ancestor.returncode != 0:
        raise AdmissionError("base_not_candidate_ancestor")
    base_tree = _resolve(repo, base, "tree")
    head_tree = _resolve(repo, head, "tree")
    rows = _path_entries(repo, base, head)
    paths = [row["path"] for row in rows]
    payload = sorted(set(paths).intersection(RECOVERY_PAYLOAD_PATHS))
    admission = sorted(
        set(paths).intersection(RECOVERY_CLASSIFICATION_SUPPORT_PATHS))
    other = sorted(set(paths) - set(payload) - set(admission))
    payload_patch = _patch_bytes(repo, base, head, payload)
    admission_patch = _patch_bytes(repo, base, head, admission)
    payload_digest = _digest_bytes(payload_patch) if payload else None
    admission_digest = _digest_bytes(admission_patch) if admission else None
    base_version = _product_version(repo, base)
    head_version = _product_version(repo, head)

    if other and payload:
        classification = "MIXED"
        classification_status = "REJECTED"
    elif payload:
        if payload_digest != expected_payload_digest:
            raise AdmissionError("recovery_payload_digest_mismatch")
        if base_version != head_version:
            raise AdmissionError("recovery_product_version_changed")
        classification = "RECOVERY_ONLY"
        classification_status = "PASS"
    else:
        classification = "PRODUCT"
        classification_status = "PASS"

    policy = scope_policy_document()
    result: dict[str, Any] = {
        "schemaVersion": CLASSIFICATION_SCHEMA,
        "status": classification_status,
        "classification": classification,
        "base": {"commitSha": base, "treeSha": base_tree},
        "candidate": {"commitSha": head, "treeSha": head_tree},
        "changedPathCount": len(paths),
        "changedPaths": rows,
        "recoveryPayloadPaths": payload,
        "recoveryAdmissionPaths": admission,
        "productOrUnknownPaths": other,
        "recoveryPayloadDiffSha256": payload_digest,
        "recoveryAdmissionDiffSha256": admission_digest,
        "scopePolicySha256": _digest(policy),
        "productVersion": {
            "base": base_version,
            "candidate": head_version,
            "unchanged": base_version == head_version,
        },
        "authorityAssertions": (
            dict(AUTHORITY_ASSERTIONS)
            if classification == "RECOVERY_ONLY" else None
        ),
    }
    result["classificationDigest"] = _digest(result)
    return result


def validate_classification(value: Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is not dict or value.get("schemaVersion") != \
            CLASSIFICATION_SCHEMA:
        raise AdmissionError("classification_schema_invalid")
    _validate_digest_document(value, "classificationDigest")
    classification = value.get("classification")
    if classification not in {"PRODUCT", "RECOVERY_ONLY", "MIXED"}:
        raise AdmissionError("classification_value_invalid")
    base, candidate = value.get("base"), value.get("candidate")
    if type(base) is not dict or type(candidate) is not dict or any(
            not SHA_RE.fullmatch(str(item.get("commitSha") or "")) or
            not TREE_RE.fullmatch(str(item.get("treeSha") or ""))
            for item in (base, candidate)):
        raise AdmissionError("classification_identity_invalid")
    rows = value.get("changedPaths")
    if type(rows) is not list or not rows or \
            value.get("changedPathCount") != len(rows):
        raise AdmissionError("classification_paths_invalid")
    if value.get("scopePolicySha256") != _digest(scope_policy_document()):
        raise AdmissionError("classification_scope_policy_mismatch")
    if classification == "RECOVERY_ONLY":
        if value.get("status") != "PASS" or \
                value.get("productOrUnknownPaths") != [] or \
                value.get("recoveryPayloadDiffSha256") != \
                EXPECTED_RECOVERY_PAYLOAD_DIFF_SHA256 or \
                value.get("productVersion", {}).get("unchanged") is not True or \
                value.get("authorityAssertions") != AUTHORITY_ASSERTIONS:
            raise AdmissionError("recovery_classification_contract_invalid")
    elif classification == "MIXED" and value.get("status") != "REJECTED":
        raise AdmissionError("mixed_classification_not_rejected")
    elif classification == "PRODUCT" and value.get("status") != "PASS":
        raise AdmissionError("product_classification_invalid")
    return dict(value)


def record_evidence(classification: Mapping[str, Any], junit: pathlib.Path,
                    checks: Sequence[str]) -> dict[str, Any]:
    scope = validate_classification(classification)
    if scope["classification"] != "RECOVERY_ONLY":
        raise AdmissionError("recovery_evidence_scope_invalid")
    if not checks or len(set(checks)) != len(checks) or any(
            not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,79}", check)
            for check in checks):
        raise AdmissionError("recovery_evidence_checks_invalid")
    try:
        junit_bytes = junit.read_bytes()
        root = ET.fromstring(junit_bytes)
    except (OSError, ET.ParseError) as exc:
        raise AdmissionError("recovery_evidence_junit_invalid") from exc
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise AdmissionError("recovery_evidence_junit_empty")

    def count(name: str) -> int:
        try:
            return sum(int(suite.attrib.get(name, "0")) for suite in suites)
        except ValueError as exc:
            raise AdmissionError("recovery_evidence_junit_count_invalid") from exc

    counts = {name: count(name) for name in (
        "tests", "failures", "errors", "skipped")}
    if counts["tests"] <= 0 or counts["failures"] or counts["errors"]:
        raise AdmissionError("recovery_evidence_tests_not_passed")
    result: dict[str, Any] = {
        "schemaVersion": EVIDENCE_SCHEMA,
        "status": "PASS",
        "base": scope["base"],
        "candidate": scope["candidate"],
        "classificationDigest": scope["classificationDigest"],
        "checkNames": sorted(checks),
        "junitSha256": _digest_bytes(junit_bytes),
        "testCounts": counts,
    }
    result["evidenceDigest"] = _digest(result)
    return result


def validate_evidence(value: Mapping[str, Any],
                      classification: Mapping[str, Any]) -> dict[str, Any]:
    scope = validate_classification(classification)
    if type(value) is not dict or value.get("schemaVersion") != \
            EVIDENCE_SCHEMA or value.get("status") != "PASS":
        raise AdmissionError("recovery_evidence_schema_invalid")
    _validate_digest_document(value, "evidenceDigest")
    if value.get("base") != scope["base"] or \
            value.get("candidate") != scope["candidate"] or \
            value.get("classificationDigest") != \
            scope["classificationDigest"]:
        raise AdmissionError("recovery_evidence_identity_mismatch")
    counts = value.get("testCounts")
    if type(counts) is not dict or type(counts.get("tests")) is not int or \
            counts["tests"] <= 0 or counts.get("failures") != 0 or \
            counts.get("errors") != 0:
        raise AdmissionError("recovery_evidence_tests_not_passed")
    names = value.get("checkNames")
    if type(names) is not list or not names or names != sorted(set(names)):
        raise AdmissionError("recovery_evidence_checks_invalid")
    return dict(value)


def issue_certificate(classification: Mapping[str, Any],
                      evidence: Mapping[str, Any]) -> dict[str, Any]:
    scope = validate_classification(classification)
    proof = validate_evidence(evidence, scope)
    if scope["classification"] != "RECOVERY_ONLY":
        raise AdmissionError("recovery_certificate_scope_invalid")
    result: dict[str, Any] = {
        "schemaVersion": CERTIFICATE_SCHEMA,
        "status": "PASS",
        "classification": "RECOVERY_ONLY",
        "base": scope["base"],
        "candidate": scope["candidate"],
        "changedPathCount": scope["changedPathCount"],
        "changedPaths": scope["changedPaths"],
        "classificationDigest": scope["classificationDigest"],
        "scopePolicySha256": scope["scopePolicySha256"],
        "recoveryPayloadDiffSha256":
            scope["recoveryPayloadDiffSha256"],
        "recoveryAdmissionDiffSha256":
            scope["recoveryAdmissionDiffSha256"],
        "productVersion": scope["productVersion"],
        "authorityAssertions": dict(AUTHORITY_ASSERTIONS),
        "requiredEvidence": {
            "evidenceDigest": proof["evidenceDigest"],
            "checkNames": proof["checkNames"],
            "junitSha256": proof["junitSha256"],
            "testCounts": proof["testCounts"],
        },
    }
    result["certificateDigest"] = _digest(result)
    return result


def verify_certificate(certificate: Mapping[str, Any],
                       classification: Mapping[str, Any],
                       evidence: Mapping[str, Any]) -> dict[str, Any]:
    if type(certificate) is not dict or certificate.get("schemaVersion") != \
            CERTIFICATE_SCHEMA:
        raise AdmissionError("recovery_certificate_schema_invalid")
    _validate_digest_document(certificate, "certificateDigest")
    expected = issue_certificate(classification, evidence)
    if certificate != expected:
        raise AdmissionError("recovery_certificate_content_mismatch")
    return dict(certificate)


def _token() -> str:
    value = os.environ.get("GITHUB_TOKEN", "").strip()
    if not value:
        raise AdmissionError("github_token_missing")
    return value


def _api_bytes(url: str, token: str) -> bytes:
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "argus-recovery-admission/1",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        opener = urllib.request.build_opener(
            _StripCrossOriginAuthorization())
        with opener.open(request, timeout=60) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AdmissionError("github_api_unavailable") from exc


def _api_json(url: str, token: str) -> dict[str, Any]:
    try:
        value = json.loads(_api_bytes(url, token))
    except json.JSONDecodeError as exc:
        raise AdmissionError("github_api_json_invalid") from exc
    if type(value) is not dict:
        raise AdmissionError("github_api_object_required")
    return value


def _archive_documents(archive: bytes) -> tuple[dict[str, Any],
                                                dict[str, Any],
                                                dict[str, Any]]:
    if not archive or len(archive) > 4 * 1024 * 1024:
        raise AdmissionError("recovery_admission_archive_size_invalid")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            result = []
            for name in ("certificate.json", "classification.json",
                         "evidence.json"):
                matches = [entry for entry in bundle.infolist()
                           if not entry.is_dir() and
                           pathlib.PurePosixPath(entry.filename).name == name]
                if len(matches) != 1:
                    raise AdmissionError(
                        f"recovery_admission_archive_{name}_count_invalid")
                if matches[0].file_size > 1024 * 1024:
                    raise AdmissionError(
                        f"recovery_admission_archive_{name}_size_invalid")
                value = json.loads(bundle.read(matches[0]))
                if type(value) is not dict:
                    raise AdmissionError("recovery_admission_archive_invalid")
                result.append(value)
    except (zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise AdmissionError("recovery_admission_archive_invalid") from exc
    certificate, classification, evidence = result
    verify_certificate(certificate, classification, evidence)
    return certificate, classification, evidence


def _authority_body_valid(value: Mapping[str, Any]) -> None:
    if type(value) is not dict or value.get("schemaVersion") != \
            AUTHORITY_SCHEMA:
        raise AdmissionError("recovery_authority_schema_invalid")
    _validate_digest_document(value, "authorityDigest")


def _artifact_digest(artifact: Mapping[str, Any]) -> str:
    value = artifact.get("digest")
    if type(value) is not str or not value.startswith("sha256:") or \
            not re.fullmatch(r"[0-9a-f]{64}", value[7:]):
        raise AdmissionError("recovery_authority_artifact_digest_invalid")
    return value[7:]


def collect_authority(*, repository: str, base_sha: str,
                      candidate_sha: str, candidate_tree: str,
                      classification_digest: str,
                      timeout_seconds: int) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise AdmissionError("github_repository_invalid")
    if not SHA_RE.fullmatch(base_sha) or not SHA_RE.fullmatch(candidate_sha) or \
            not TREE_RE.fullmatch(candidate_tree) or \
            not re.fullmatch(r"[0-9a-f]{64}", classification_digest):
        raise AdmissionError("recovery_authority_identity_invalid")
    token = _token()
    deadline = time.monotonic() + timeout_seconds
    selected: dict[str, Any] | None = None
    while True:
        query = urllib.parse.urlencode({"per_page": 100, "filter": "latest"})
        payload = _api_json(
            f"https://api.github.com/repos/{repository}/commits/"
            f"{candidate_sha}/check-runs?{query}", token)
        check_runs = payload.get("check_runs")
        if type(check_runs) is not list:
            raise AdmissionError("recovery_authority_checks_response_invalid")
        rows = [row for row in check_runs
                if type(row) is dict and
                row.get("name") == RECOVERY_CHECK_CONTEXT]
        rows.sort(key=lambda row: (
            str(row.get("completed_at") or row.get("started_at") or ""),
            int(row.get("id") or 0)), reverse=True)
        selected = rows[0] if rows else None
        if selected and selected.get("status") == "completed":
            if selected.get("conclusion") != "success":
                raise AdmissionError("recovery_authority_check_failed")
            break
        if time.monotonic() >= deadline:
            raise AdmissionError("recovery_authority_check_not_ready")
        time.sleep(10)

    if type(selected.get("id")) is not int or \
            selected.get("status") != "completed":
        raise AdmissionError("recovery_authority_check_invalid")
    details_url = str(selected.get("details_url") or "")
    match = re.fullmatch(
        rf"https://github\.com/{re.escape(repository)}/actions/runs/"
        r"([0-9]+)/job/([0-9]+)", details_url)
    if not match:
        raise AdmissionError("recovery_authority_details_url_invalid")
    run_id, job_id = map(int, match.groups())
    run = _api_json(
        f"https://api.github.com/repos/{repository}/actions/runs/{run_id}",
        token)
    if type(run.get("id")) is not int or run.get("id") != run_id or \
            type(run.get("run_attempt")) is not int or \
            run.get("run_attempt") <= 0 or \
            run.get("status") != "completed" or \
            run.get("head_sha") != candidate_sha or \
            run.get("event") != "pull_request" or \
            run.get("path") != RECOVERY_WORKFLOW_PATH or \
            run.get("conclusion") != "success":
        raise AdmissionError("recovery_authority_workflow_invalid")
    artifacts = _api_json(
        f"https://api.github.com/repos/{repository}/actions/runs/"
        f"{run_id}/artifacts?per_page=100", token)
    expected_name = (
        f"recovery-admission-{candidate_sha}-{run_id}-{run['run_attempt']}"
    )
    matches = [row for row in artifacts.get("artifacts", [])
               if type(row) is dict and row.get("name") == expected_name and
               row.get("expired") is False and
               row.get("workflow_run", {}).get("id") == run_id and
               row.get("workflow_run", {}).get("head_sha") == candidate_sha]
    if len(matches) != 1:
        raise AdmissionError("recovery_authority_artifact_count_invalid")
    artifact = matches[0]
    artifact_id = artifact.get("id")
    expected_archive_url = (
        f"https://api.github.com/repos/{repository}/actions/artifacts/"
        f"{artifact_id}/zip"
    )
    if type(artifact_id) is not int or artifact_id <= 0 or \
            artifact.get("archive_download_url") != expected_archive_url:
        raise AdmissionError("recovery_authority_artifact_invalid")
    artifact_digest = _artifact_digest(artifact)
    archive = _api_bytes(expected_archive_url, token)
    if _digest_bytes(archive) != artifact_digest:
        raise AdmissionError("recovery_authority_archive_mismatch")
    certificate, classification, evidence = _archive_documents(archive)
    if classification.get("base", {}).get("commitSha") != base_sha or \
            classification.get("candidate") != {
                "commitSha": candidate_sha, "treeSha": candidate_tree} or \
            classification.get("classificationDigest") != \
            classification_digest:
        raise AdmissionError("recovery_authority_candidate_mismatch")
    body: dict[str, Any] = {
        "schemaVersion": AUTHORITY_SCHEMA,
        "status": "PASS",
        "repository": repository,
        "baseSha": base_sha,
        "candidate": {"commitSha": candidate_sha,
                      "treeSha": candidate_tree},
        "classificationDigest": classification_digest,
        "check": {"name": RECOVERY_CHECK_CONTEXT,
                  "checkRunId": selected.get("id"),
                  "detailsUrl": details_url,
                  "conclusion": "success"},
        "producer": {"workflowRunId": run_id,
                     "runAttempt": run["run_attempt"], "jobId": job_id,
                     "workflowPath": RECOVERY_WORKFLOW_PATH,
                     "event": "pull_request", "headSha": candidate_sha,
                     "conclusion": "success"},
        "artifact": {"artifactId": artifact_id,
                     "name": expected_name,
                     "artifactDigest": artifact_digest},
        "certificate": {"certificateDigest":
                        certificate["certificateDigest"],
                        "evidenceDigest": evidence["evidenceDigest"]},
    }
    body["authorityDigest"] = _digest(body)
    return body


def fetch_authority(*, repository: str, authority: Mapping[str, Any],
                    certificate_out: pathlib.Path,
                    classification_out: pathlib.Path,
                    evidence_out: pathlib.Path,
                    receipt_out: pathlib.Path) -> dict[str, Any]:
    _authority_body_valid(authority)
    if authority.get("repository") != repository:
        raise AdmissionError("recovery_authority_repository_mismatch")
    token = _token()
    artifact_id = authority.get("artifact", {}).get("artifactId")
    if type(artifact_id) is not int or artifact_id <= 0:
        raise AdmissionError("recovery_authority_artifact_invalid")
    row = _api_json(
        f"https://api.github.com/repos/{repository}/actions/artifacts/"
        f"{artifact_id}", token)
    producer = authority.get("producer", {})
    expected_archive_url = (
        f"https://api.github.com/repos/{repository}/actions/artifacts/"
        f"{artifact_id}/zip"
    )
    if row.get("id") != artifact_id or \
            row.get("name") != authority.get("artifact", {}).get("name") or \
            row.get("expired") is not False or \
            row.get("archive_download_url") != expected_archive_url or \
            row.get("workflow_run", {}).get("id") != \
            producer.get("workflowRunId") or \
            row.get("workflow_run", {}).get("head_sha") != \
            authority.get("candidate", {}).get("commitSha") or \
            _artifact_digest(row) != authority.get("artifact", {}).get(
                "artifactDigest"):
        raise AdmissionError("recovery_authority_artifact_pointer_mismatch")
    archive = _api_bytes(expected_archive_url, token)
    if _digest_bytes(archive) != authority.get("artifact", {}).get(
            "artifactDigest"):
        raise AdmissionError("recovery_authority_archive_mismatch")
    certificate, classification, evidence = _archive_documents(archive)
    if certificate.get("certificateDigest") != authority.get(
            "certificate", {}).get("certificateDigest") or \
            evidence.get("evidenceDigest") != authority.get(
            "certificate", {}).get("evidenceDigest") or \
            classification.get("base", {}).get("commitSha") != \
            authority.get("baseSha") or \
            classification.get("candidate") != authority.get("candidate") or \
            classification.get("classificationDigest") != authority.get(
                "classificationDigest"):
        raise AdmissionError("recovery_authority_content_mismatch")
    _write(certificate_out, certificate)
    _write(classification_out, classification)
    _write(evidence_out, evidence)
    receipt: dict[str, Any] = {
        "schemaVersion": RETRIEVAL_SCHEMA,
        "status": "PASS",
        "authorityDigest": authority["authorityDigest"],
        "archiveSha256": _digest_bytes(archive),
        "certificateDigest": certificate["certificateDigest"],
        "classificationDigest": classification["classificationDigest"],
        "evidenceDigest": evidence["evidenceDigest"],
    }
    receipt["retrievalDigest"] = _digest(receipt)
    _write(receipt_out, receipt)
    return receipt


def _github_outputs(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    fields = {
        "base_sha": value["base"]["commitSha"],
        "candidate_sha": value["candidate"]["commitSha"],
        "candidate_tree": value["candidate"]["treeSha"],
        "changed_path_count": value["changedPathCount"],
        "classification": value["classification"],
        "classification_digest": value["classificationDigest"],
    }
    with path.open("a", encoding="utf-8") as handle:
        for key, item in fields.items():
            handle.write(f"{key}={item}\n")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    classify = sub.add_parser("classify")
    classify.add_argument("--repo", default=".", type=pathlib.Path)
    classify.add_argument("--base-sha", required=True)
    classify.add_argument("--head-sha", required=True)
    classify.add_argument("--out", required=True, type=pathlib.Path)
    classify.add_argument("--github-output", type=pathlib.Path)
    evidence = sub.add_parser("record-evidence")
    evidence.add_argument("--classification", required=True, type=pathlib.Path)
    evidence.add_argument("--junit", required=True, type=pathlib.Path)
    evidence.add_argument("--check", action="append", required=True)
    evidence.add_argument("--out", required=True, type=pathlib.Path)
    issue = sub.add_parser("issue")
    issue.add_argument("--classification", required=True, type=pathlib.Path)
    issue.add_argument("--evidence", required=True, type=pathlib.Path)
    issue.add_argument("--out", required=True, type=pathlib.Path)
    verify = sub.add_parser("verify")
    verify.add_argument("--certificate", required=True, type=pathlib.Path)
    verify.add_argument("--classification", required=True, type=pathlib.Path)
    verify.add_argument("--evidence", required=True, type=pathlib.Path)
    collect = sub.add_parser("collect-authority")
    collect.add_argument("--repository", required=True)
    collect.add_argument("--base-sha", required=True)
    collect.add_argument("--candidate-sha", required=True)
    collect.add_argument("--candidate-tree", required=True)
    collect.add_argument("--classification-digest", required=True)
    collect.add_argument("--timeout-seconds", type=int, default=1500)
    collect.add_argument("--out", required=True, type=pathlib.Path)
    fetch = sub.add_parser("fetch-authority")
    fetch.add_argument("--repository", required=True)
    fetch.add_argument("--authority", required=True, type=pathlib.Path)
    fetch.add_argument("--certificate-out", required=True, type=pathlib.Path)
    fetch.add_argument("--classification-out", required=True,
                       type=pathlib.Path)
    fetch.add_argument("--evidence-out", required=True, type=pathlib.Path)
    fetch.add_argument("--receipt-out", required=True, type=pathlib.Path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "classify":
        value = classify_repository(args.repo, args.base_sha, args.head_sha)
        _write(args.out, value)
        if args.github_output:
            _github_outputs(args.github_output, value)
    elif args.command == "record-evidence":
        _write(args.out, record_evidence(
            _load(args.classification), args.junit, args.check))
    elif args.command == "issue":
        _write(args.out, issue_certificate(
            _load(args.classification), _load(args.evidence)))
    elif args.command == "verify":
        verify_certificate(
            _load(args.certificate), _load(args.classification),
            _load(args.evidence))
        print("recovery-admission: PASS")
    elif args.command == "collect-authority":
        if not 1 <= args.timeout_seconds <= 1800:
            raise AdmissionError("authority_timeout_invalid")
        _write(args.out, collect_authority(
            repository=args.repository, base_sha=args.base_sha,
            candidate_sha=args.candidate_sha,
            candidate_tree=args.candidate_tree,
            classification_digest=args.classification_digest,
            timeout_seconds=args.timeout_seconds))
    elif args.command == "fetch-authority":
        fetch_authority(
            repository=args.repository, authority=_load(args.authority),
            certificate_out=args.certificate_out,
            classification_out=args.classification_out,
            evidence_out=args.evidence_out, receipt_out=args.receipt_out)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AdmissionError as exc:
        raise SystemExit(f"recovery-admission: FAIL: {exc}") from exc
