import hashlib
import json
from pathlib import Path

import pytest

from scripts import v13_5_pre_mutation_rehearsal as rehearsal


CANDIDATE = {"commitSha": "a" * 40, "treeSha": "b" * 40}


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def prepare(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    dist = repo / "web/dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<script>globalThis.__ARGUS_VERSION__="13.5.60";'
        'globalThis.__ARGUS_PRODUCT_VERSION__="v13.5.60";'
        f'globalThis.__ARGUS_BUILD_SHA__="{CANDIDATE["commitSha"]}";</script>')
    (dist / "asset.js").write_text("export default 1")
    write_json(repo / "product-version.json", {
        "schemaVersion": "argus-product-version-v1", "productVersion": "v13.5.60"})
    write_json(repo / "web/package.json", {"version": "13.5.60"})
    write_json(repo / "backend-version.json", {"version": "13.5.60"})
    paths = {name: tmp_path / f"{name}.json" for name in (
        "source", "certificate", "runtime", "retrieval")}
    for path in paths.values():
        write_json(path, {})
    cert = {"certificateDigest": "c" * 64}
    runtime = {"runtimeIdentityDigest": "d" * 64}
    source = {"releaseMerge": None, "provenanceDigest": "e" * 64}
    monkeypatch.setattr(
        rehearsal.certificate, "_validate_admission_identity",
        lambda *_args: cert)
    monkeypatch.setattr(
        rehearsal.certificate, "_validate_runtime_proof",
        lambda *_args: runtime)
    monkeypatch.setattr(
        rehearsal.certificate, "_validate_retrieval_receipt",
        lambda *_args: {})
    monkeypatch.setattr(
        rehearsal.provenance, "validate_receipt",
        lambda *_args, **_kwargs: source)
    return repo, dist, paths


def test_sealed_rehearsal_is_non_mutating_and_reverifies_dist(
        tmp_path, monkeypatch):
    repo, dist, paths = prepare(tmp_path, monkeypatch)
    value = rehearsal.seal(
        repo=repo, dist=dist, source_receipt_path=paths["source"],
        certificate_path=paths["certificate"],
        runtime_proof_path=paths["runtime"],
        retrieval_receipt_path=paths["retrieval"],
        candidate_sha=CANDIDATE["commitSha"],
        candidate_tree=CANDIDATE["treeSha"],
        build_sha=CANDIDATE["commitSha"])
    assert value["status"] == "PASS"
    assert value["checks"]["pagesDeployInputConstructed"] is True
    assert value["checks"]["productionMutationPerformed"] is False
    assert value["productionMutations"] == []
    assert rehearsal.verify(
        value=value, dist=dist,
        expected_build_sha=CANDIDATE["commitSha"])["status"] == "PASS"


def test_dist_tamper_or_wrong_build_identity_fails_closed(tmp_path, monkeypatch):
    repo, dist, paths = prepare(tmp_path, monkeypatch)
    value = rehearsal.seal(
        repo=repo, dist=dist, source_receipt_path=paths["source"],
        certificate_path=paths["certificate"],
        runtime_proof_path=paths["runtime"],
        retrieval_receipt_path=paths["retrieval"],
        candidate_sha=CANDIDATE["commitSha"],
        candidate_tree=CANDIDATE["treeSha"],
        build_sha=CANDIDATE["commitSha"])
    (dist / "asset.js").write_text("tampered")
    with pytest.raises(ValueError, match="pre_mutation_rehearsal_receipt_invalid"):
        rehearsal.verify(
            value=value, dist=dist,
            expected_build_sha=CANDIDATE["commitSha"])
    with pytest.raises(ValueError, match="rehearsal_build_sha_mismatch"):
        rehearsal.seal(
            repo=repo, dist=dist, source_receipt_path=paths["source"],
            certificate_path=paths["certificate"],
            runtime_proof_path=paths["runtime"],
            retrieval_receipt_path=paths["retrieval"],
            candidate_sha=CANDIDATE["commitSha"],
            candidate_tree=CANDIDATE["treeSha"], build_sha="f" * 40)


def test_workflows_share_rehearsal_and_never_install_browser_or_os_packages():
    root = Path(__file__).resolve().parent
    release_gate = (root / ".github/workflows/release-gate.yml").read_text()
    production = (root / ".github/workflows/deploy-pages.yml").read_text()
    action = (root / ".github/actions/v13-5-pre-mutation-rehearsal/action.yml").read_text()
    assert "fetch-depth: 2" in release_gate
    assert "uses: ./.github/actions/v13-5-pre-mutation-rehearsal" in release_gate
    assert "uses: ./.github/actions/v13-5-pre-mutation-rehearsal" in production
    assert "release-merge-sha: ${{ github.sha }}" in production
    assert "release-merge-tree: ${{ steps.candidate.outputs.tree }}" in production
    assert "v13-5-predeployment-pages-${{ github.sha }}" in production
    assert "v13_5_pre_mutation_rehearsal.py verify" in production
    verify_block = action.split(
        "Verify detached certificate against the admitted source and runtime",
        1)[1].split("- uses: actions/setup-node@v5", 1)[0]
    assert verify_block.count("--release-merge-sha") == 1
    assert verify_block.count("--release-merge-tree") == 1
    assert "${{ inputs.release-merge-sha }}" in verify_block
    assert "${{ inputs.release-merge-tree }}" in verify_block
    combined = "\n".join((release_gate, production, action)).lower()
    for forbidden in (
            "npx playwright install", "playwright install --with-deps",
            "apt install", "apt-get install", "browser download",
            "chromium download"):
        assert forbidden not in combined
