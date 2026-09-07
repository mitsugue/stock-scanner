import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import v13_5_source_provenance as source


def test_restoration_allowlist_is_exact_and_core_semantics_stay_closed():
    expected = {
        ".github/workflows/caos-scan.yml",
        ".github/workflows/deploy-pages.yml",
        ".github/workflows/release-gate.yml",
        "argus_today_headline.py",
        "scanner.py",
        "test_v13_5_release_certificate.py",
        "test_caos_workflow_recovery.py",
        "web/scripts/mobile-today-acceptance.mjs",
        "web/scripts/release-fixture-target.mjs",
        "web/scripts/acceptance-runtime.test.mjs",
        "web/scripts/release-state-machine.mjs",
        "web/scripts/release-state-machine.test.mjs",
        "web/src/components/NavRail.css",
        "web/src/components/today/ArgusTodayPanel.tsx",
        "web/src/hooks/useTodayHeadline.ts",
        "web/src/lib/todayHeadline.ts",
        "web/src/routes/CommandCenter.tsx",
    }
    assert expected.issubset(source.AUTHORIZED_EXTENSION_PATHS)
    # Core investment semantics stay outside any release authorization.
    # v13.5.36 exception (owner-authorized, 2026-08-22 spec conformance):
    # web/src/domain/singleDecisionAuthority.ts is authorized ONCE for the
    # canonical-artifact resolver integration its own design comment reserved
    # for "a future, separately reviewed integration" — the AVAILABLE
    # references are still executable only via the registered resolver seam,
    # BUY stays structurally locked, and the pinned data-gated identities are
    # unchanged (single-decision-authority.test.cjs).
    for closed in ("argus_market_data_truth.py", "argus_calibration.py",
                   "argus_market_replay.py", "argus_rules.py"):
        assert closed not in source.AUTHORIZED_EXTENSION_PATHS
    assert "web/src/domain/singleDecisionAuthority.ts" \
        in source.AUTHORIZED_EXTENSION_PATHS


def test_tachibana_shadow_provider_is_authorized_as_isolated_package_only():
    tachibana = {
        "argus_providers/__init__.py",
        "argus_providers/tachibana/__init__.py",
        "argus_providers/tachibana/client.py",
        "argus_providers/tachibana/config.py",
        "argus_providers/tachibana/cross_validation.py",
        "argus_providers/tachibana/event_stream.py",
        "argus_providers/tachibana/evidence.py",
        "argus_providers/tachibana/models.py",
        "argus_providers/tachibana/normalization.py",
        "argus_providers/tachibana/redaction.py",
        "argus_providers/tachibana/runtime.py",
        "argus_providers/tachibana/sensor.py",
        "argus_providers/tachibana/session.py",
        "argus_providers/tachibana/session_truth.py",
        "argus_providers/tachibana/singleton.py",
        "docs/evidence/tachibana-v4r10-2026-09-01.md",
        "docs/operations/tachibana-live-shadow.md",
        "requirements-tachibana.txt",
        "scripts/tachibana_live_acceptance.py",
        "scripts/tachibana_live_sensor_service.py",
        "scripts/tachibana_readonly_smoke.py",
        "test_argus_tachibana_sensor.py",
    }
    assert tachibana.issubset(source.AUTHORIZED_EXTENSION_PATHS)
    # The provider stays an isolated package: no authorization is granted to a
    # product entry point, requirements.txt, or the decision core through it.
    for closed in ("requirements.txt", "wsgi.py", "gunicorn.conf.py",
                   "argus_market_data_truth.py", "argus_rules.py"):
        assert closed not in source.AUTHORIZED_EXTENSION_PATHS


def test_recovery_only_admissions_are_listed_without_product_authority():
    # Paths merged through the independent Recovery certificate route are
    # listed so the product semantic diff stays computable; the list adds no
    # Recovery payload outside scripts/recovery_admission.py's pinned set.
    from scripts import recovery_admission as recovery
    recovery_paths = set(recovery.RECOVERY_PAYLOAD_PATHS).union(
        recovery.RECOVERY_CLASSIFICATION_SUPPORT_PATHS)
    listed = {
        path for path in source.AUTHORIZED_EXTENSION_PATHS
        if path in recovery_paths
    }
    assert listed
    assert listed.issubset(recovery_paths)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True).strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Argus Test", "-c",
        "user.email=argus@example.invalid", "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def certificate(path: Path, *, candidate_sha: str, candidate_tree: str,
                accepted_sha: str, accepted_tree: str) -> None:
    value = {
        "candidate": {"commitSha": candidate_sha, "treeSha": candidate_tree},
        "acceptedV13Source": {
            "commitSha": accepted_sha, "treeSha": accepted_tree},
        "productVersion": "v13.5.62",
    }
    value["certificateDigest"] = hashlib.sha256(
        source.canonical_bytes(value)).hexdigest()
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture()
def shallow_case(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    subprocess.check_call(["git", "init", "--bare", str(origin)])
    subprocess.check_call(["git", "init", "-b", "main", str(seed)])
    write(seed / "product-version.json", json.dumps({
        "schemaVersion": "argus-product-version-v1", "productVersion": "v13"}))
    accepted_sha = commit(seed, "accepted-v13")
    accepted_tree = git(seed, "rev-parse", "HEAD^{tree}")
    for ordinal in range(3):
        write(seed / f"history-{ordinal}.txt", str(ordinal))
        commit(seed, f"history-{ordinal}")
    write(seed / "product-version.json", json.dumps({
        "schemaVersion": "argus-product-version-v1", "productVersion": "v13.5.62"}))
    write(seed / "release/v13-accepted-fix-manifest.json", json.dumps({
        "canonicalSource": {"head": accepted_sha, "tree": accepted_tree},
        "requirements": [],
    }))
    candidate_sha = commit(seed, "candidate")
    candidate_tree = git(seed, "rev-parse", "HEAD^{tree}")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "origin", "main")

    checkout = tmp_path / "checkout"
    subprocess.check_call([
        "git", "clone", "--depth", "2", "--branch", "main",
        f"file://{origin}", str(checkout)])
    cert = tmp_path / "certificate.json"
    certificate(cert, candidate_sha=candidate_sha,
                candidate_tree=candidate_tree, accepted_sha=accepted_sha,
                accepted_tree=accepted_tree)
    monkeypatch.setattr(source, "ACCEPTED_V13_SOURCE", accepted_sha)
    monkeypatch.setattr(source, "ACCEPTED_V13_TREE", accepted_tree)
    monkeypatch.setattr(source, "AUTHORIZED_EXTENSION_PATHS", frozenset({
        "product-version.json", "release/v13-accepted-fix-manifest.json",
        "history-0.txt", "history-1.txt", "history-2.txt"}))
    return {
        "origin": origin, "checkout": checkout, "certificate": cert,
        "accepted_sha": accepted_sha, "accepted_tree": accepted_tree,
        "candidate_sha": candidate_sha, "candidate_tree": candidate_tree,
    }


def acquire(case, **overrides):
    values = {
        "repo": case["checkout"], "remote": "origin",
        "accepted_source": case["accepted_sha"],
        "accepted_tree": case["accepted_tree"],
        "candidate_sha": case["candidate_sha"],
        "candidate_tree": case["candidate_tree"],
        "certificate_path": case["certificate"],
        "allow_local_remote": True,
    }
    values.update(overrides)
    return source.acquire_source(**values)


def test_original_two_commit_checkout_reproduces_absent_source_then_fetches_exact(
        shallow_case):
    case = shallow_case
    checkout = case["checkout"]
    assert git(checkout, "rev-parse", "--is-shallow-repository") == "true"
    missing = subprocess.run([
        "git", "cat-file", "-e", f"{case['accepted_sha']}^{{commit}}"],
        cwd=checkout, check=False)
    assert missing.returncode != 0
    assert git(checkout, "rev-parse", "HEAD~1") != case["accepted_sha"]

    receipt = acquire(case)
    assert receipt["status"] == "PASS"
    assert receipt["fetch"] == {
        "requestedCommitSha": case["accepted_sha"],
        "fetchHeadCommitSha": case["accepted_sha"],
        "depth": 1,
        "noTags": True,
        "sourcePresentBeforeFetch": False,
        "initialCheckoutShallow": True,
        "postFetchShallow": True,
    }
    assert receipt["acceptedSource"] == {
        "commitSha": case["accepted_sha"], "treeSha": case["accepted_tree"]}
    assert receipt["candidate"] == {
        "commitSha": case["candidate_sha"], "treeSha": case["candidate_tree"]}
    assert receipt["semanticDiff"]["status"] == "PASS"


def test_semantic_diff_fails_before_exact_source_is_acquired(shallow_case):
    with pytest.raises(ValueError, match="git_rev_parse_failed"):
        source.validate_product_semantic_diff(
            shallow_case["candidate_sha"], repo=shallow_case["checkout"])


def test_fetch_failure_is_precise_and_never_falls_back_to_shallow_ancestor(
        shallow_case, tmp_path):
    empty = tmp_path / "empty.git"
    subprocess.check_call(["git", "init", "--bare", str(empty)])
    git(shallow_case["checkout"], "remote", "set-url", "origin", str(empty))
    with pytest.raises(ValueError, match="accepted_source_fetch_failed"):
        acquire(shallow_case)


def test_fetch_head_must_resolve_to_the_exact_requested_commit(
        shallow_case, monkeypatch):
    original = source._resolve

    def hostile(repo, ref, kind):
        if ref == "FETCH_HEAD" and kind == "commit":
            return "f" * 40
        return original(repo, ref, kind)

    monkeypatch.setattr(source, "_resolve", hostile)
    with pytest.raises(ValueError, match="accepted_source_fetch_head_mismatch"):
        acquire(shallow_case)


@pytest.mark.parametrize(("field", "error"), [
    ("accepted_source", "accepted_source_authority_conflict"),
    ("accepted_tree", "accepted_source_authority_conflict"),
    ("candidate_tree", "source_provenance_certificate_candidate_mismatch"),
])
def test_wrong_source_or_candidate_authority_fails_closed(
        shallow_case, field, error):
    with pytest.raises(ValueError, match=error):
        acquire(shallow_case, **{field: "f" * 40})


def test_resolved_accepted_tree_mismatch_fails_closed(shallow_case, monkeypatch):
    wrong = "f" * 40
    monkeypatch.setattr(source, "ACCEPTED_V13_TREE", wrong)
    value = json.loads(shallow_case["certificate"].read_text())
    value["acceptedV13Source"]["treeSha"] = wrong
    value.pop("certificateDigest")
    value["certificateDigest"] = source.sha256_hex(value)
    shallow_case["certificate"].write_text(json.dumps(value))
    monkeypatch.setattr(source, "_manifest_identity", lambda _repo: {})
    with pytest.raises(ValueError, match="accepted_source_tree_mismatch"):
        acquire(shallow_case, accepted_tree=wrong)


def test_candidate_tree_is_resolved_not_trusted_from_certificate(shallow_case):
    wrong = "f" * 40
    value = json.loads(shallow_case["certificate"].read_text())
    value["candidate"]["treeSha"] = wrong
    value.pop("certificateDigest")
    value["certificateDigest"] = source.sha256_hex(value)
    shallow_case["certificate"].write_text(json.dumps(value))
    with pytest.raises(ValueError, match="candidate_tree_mismatch"):
        acquire(shallow_case, candidate_tree=wrong)


def test_missing_or_other_candidate_certificate_fails_closed(shallow_case):
    with pytest.raises(ValueError, match="source_provenance_json_invalid"):
        acquire(shallow_case, certificate_path=Path("/missing/certificate.json"))
    value = json.loads(shallow_case["certificate"].read_text())
    value["candidate"]["commitSha"] = "f" * 40
    value.pop("certificateDigest")
    value["certificateDigest"] = source.sha256_hex(value)
    shallow_case["certificate"].write_text(json.dumps(value))
    with pytest.raises(
            ValueError, match="source_provenance_certificate_candidate_mismatch"):
        acquire(shallow_case)


def test_conflicting_manifest_source_authority_fails_closed(shallow_case):
    manifest_path = shallow_case["checkout"] / \
        "release/v13-accepted-fix-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["canonicalSource"]["head"] = "f" * 40
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="accepted_source_authority_conflict"):
        acquire(shallow_case)


def test_release_merge_identity_cannot_be_substituted(shallow_case):
    with pytest.raises(ValueError, match="release_merge_tree_mismatch"):
        acquire(
            shallow_case, release_merge_sha=shallow_case["candidate_sha"],
            release_merge_tree="f" * 40)
    with pytest.raises(
            ValueError, match="release_merge_candidate_parent_mismatch"):
        acquire(
            shallow_case, release_merge_sha=shallow_case["candidate_sha"],
            release_merge_tree=shallow_case["candidate_tree"])


def test_only_canonical_github_remote_is_allowed_in_production(shallow_case):
    with pytest.raises(ValueError, match="accepted_source_remote_mismatch"):
        source.acquire_source(
            repo=shallow_case["checkout"], remote="origin",
            accepted_source=shallow_case["accepted_sha"],
            accepted_tree=shallow_case["accepted_tree"],
            candidate_sha=shallow_case["candidate_sha"],
            candidate_tree=shallow_case["candidate_tree"],
            certificate_path=shallow_case["certificate"])
