#!/usr/bin/env python3
"""Seal and re-verify the exact Pages deploy input built before mutation."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from typing import Any, Dict, Mapping

try:
    from scripts import v13_5_release_certificate as certificate
    from scripts import v13_5_source_provenance as provenance
except ModuleNotFoundError:  # Direct execution from scripts/.
    import v13_5_release_certificate as certificate  # type: ignore
    import v13_5_source_provenance as provenance  # type: ignore


SCHEMA = "argus-v13-5-pre-mutation-rehearsal-v1"
ROOT = pathlib.Path(__file__).resolve().parents[1]
MAX_FILES = 4096
MAX_BYTES = 64 * 1024 * 1024


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def _digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(raw).hexdigest()


def _load(path: pathlib.Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"rehearsal_json_invalid:{path}") from exc
    if type(value) is not dict:
        raise ValueError(f"rehearsal_json_object_required:{path}")
    return value


def _write(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")


def _dist_manifest(dist: pathlib.Path) -> Dict[str, Any]:
    if not dist.is_dir() or dist.is_symlink():
        raise ValueError("pages_dist_not_directory")
    rows = []
    total = 0
    for path in sorted(dist.rglob("*")):
        if path.is_symlink():
            raise ValueError("pages_dist_symlink_forbidden")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("pages_dist_special_file_forbidden")
        relative = path.relative_to(dist).as_posix()
        raw = path.read_bytes()
        total += len(raw)
        rows.append({"path": relative, "bytes": len(raw),
                     "sha256": _digest(raw)})
        if len(rows) > MAX_FILES or total > MAX_BYTES:
            raise ValueError("pages_dist_resource_bound_exceeded")
    if not rows or not any(row["path"] == "index.html" for row in rows):
        raise ValueError("pages_dist_index_missing")
    return {"fileCount": len(rows), "totalBytes": total,
            "files": rows, "digest": _digest(rows)}


def _identity_files(repo: pathlib.Path) -> Dict[str, str]:
    product = _load(repo / "product-version.json")
    package = _load(repo / "web/package.json")
    backend = _load(repo / "backend-version.json")
    if product != {"schemaVersion": "argus-product-version-v1",
                   "productVersion": "v13.5.65"}:
        raise ValueError("rehearsal_product_version_invalid")
    if package.get("version") != "13.5.65" \
            or backend.get("version") != "13.5.65":
        raise ValueError("rehearsal_component_version_invalid")
    return {"productVersion": product["productVersion"],
            "frontendVersion": package["version"],
            "backendVersion": backend["version"]}


def seal(*, repo: pathlib.Path, dist: pathlib.Path,
         source_receipt_path: pathlib.Path, certificate_path: pathlib.Path,
         runtime_proof_path: pathlib.Path, retrieval_receipt_path: pathlib.Path,
         candidate_sha: str, candidate_tree: str, build_sha: str,
         release_merge_sha: str = "", release_merge_tree: str = "") -> Dict[str, Any]:
    cert = certificate._validate_admission_identity(
        _load(certificate_path),
        {"commitSha": candidate_sha, "treeSha": candidate_tree})
    runtime = certificate._validate_runtime_proof(
        runtime_proof_path,
        {"commitSha": candidate_sha, "treeSha": candidate_tree})
    certificate._validate_retrieval_receipt(
        retrieval_receipt_path, cert,
        {"commitSha": candidate_sha, "treeSha": candidate_tree})
    source = provenance.validate_receipt(
        _load(source_receipt_path), candidate_sha=candidate_sha,
        candidate_tree=candidate_tree,
        certificate_digest=cert["certificateDigest"],
        release_merge_sha=release_merge_sha or None,
        release_merge_tree=release_merge_tree or None, repo=repo)
    expected_build = release_merge_sha or candidate_sha
    if build_sha != expected_build:
        raise ValueError("rehearsal_build_sha_mismatch")
    index = (dist / "index.html").read_text(encoding="utf-8")
    for marker in (
            f'__ARGUS_BUILD_SHA__="{build_sha}"',
            '__ARGUS_PRODUCT_VERSION__="v13.5.65"',
            '__ARGUS_VERSION__="13.5.65"'):
        if marker not in index:
            raise ValueError(f"rehearsal_index_identity_missing:{marker}")
    identities = _identity_files(repo)
    manifest = _dist_manifest(dist)
    body: Dict[str, Any] = {
        "schemaVersion": SCHEMA,
        "status": "PASS",
        "candidate": {"commitSha": candidate_sha, "treeSha": candidate_tree},
        "releaseMerge": source["releaseMerge"],
        "buildSha": build_sha,
        "identities": identities,
        "certificateDigest": cert["certificateDigest"],
        "runtimeIdentityDigest": runtime["runtimeIdentityDigest"],
        "sourceProvenanceDigest": source["provenanceDigest"],
        "deployInput": manifest,
        "checks": {
            "acceptedSourceResolved": True,
            "acceptedSourceTreeVerified": True,
            "candidateTreeVerified": True,
            "releaseTreeBoundWhenApplicable": True,
            "certificateVerified": True,
            "runtimeVerified": True,
            "productSemanticDiffPassed": True,
            "productVersionVerified": True,
            "frontendBuilt": True,
            "backendPackageValidated": True,
            "pagesDeployInputConstructed": True,
            "productionMutationPerformed": False,
        },
        "productionMutations": [],
    }
    body["rehearsalDigest"] = _digest(body)
    return body


def verify(*, value: Mapping[str, Any], dist: pathlib.Path,
           expected_build_sha: str) -> Dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("rehearsal_receipt_object_required")
    receipt = dict(value)
    digest = receipt.pop("rehearsalDigest", None)
    expected_keys = {
        "schemaVersion", "status", "candidate", "releaseMerge", "buildSha",
        "identities", "certificateDigest", "runtimeIdentityDigest",
        "sourceProvenanceDigest", "deployInput", "checks",
        "productionMutations",
    }
    checks = receipt.get("checks")
    if set(receipt) != expected_keys or type(digest) is not str \
            or len(digest) != 64 or digest != _digest(receipt) \
            or receipt.get("schemaVersion") != SCHEMA \
            or receipt.get("status") != "PASS" \
            or receipt.get("buildSha") != expected_build_sha \
            or type(checks) is not dict or set(checks) != {
                "acceptedSourceResolved", "acceptedSourceTreeVerified",
                "candidateTreeVerified", "releaseTreeBoundWhenApplicable",
                "certificateVerified", "runtimeVerified",
                "productSemanticDiffPassed", "productVersionVerified",
                "frontendBuilt", "backendPackageValidated",
                "pagesDeployInputConstructed", "productionMutationPerformed"} \
            or any(value is not True for key, value in checks.items()
                   if key != "productionMutationPerformed") \
            or checks.get("productionMutationPerformed") is not False \
            or receipt.get("productionMutations") != [] \
            or receipt.get("deployInput") != _dist_manifest(dist):
        raise ValueError("pre_mutation_rehearsal_receipt_invalid")
    receipt["rehearsalDigest"] = digest
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("seal")
    for name in ("dist", "source-receipt", "certificate", "runtime-proof",
                 "retrieval-receipt", "candidate-sha", "candidate-tree",
                 "build-sha", "out"):
        create.add_argument(f"--{name}", required=True)
    create.add_argument("--repo-root", default=str(ROOT))
    create.add_argument("--release-merge-sha", default="")
    create.add_argument("--release-merge-tree", default="")
    check = sub.add_parser("verify")
    check.add_argument("--receipt", required=True)
    check.add_argument("--dist", required=True)
    check.add_argument("--expected-build-sha", required=True)
    args = parser.parse_args()
    if args.command == "seal":
        result = seal(
            repo=pathlib.Path(args.repo_root), dist=pathlib.Path(args.dist),
            source_receipt_path=pathlib.Path(args.source_receipt),
            certificate_path=pathlib.Path(args.certificate),
            runtime_proof_path=pathlib.Path(args.runtime_proof),
            retrieval_receipt_path=pathlib.Path(args.retrieval_receipt),
            candidate_sha=args.candidate_sha,
            candidate_tree=args.candidate_tree, build_sha=args.build_sha,
            release_merge_sha=args.release_merge_sha,
            release_merge_tree=args.release_merge_tree)
        _write(pathlib.Path(args.out), result)
    else:
        result = verify(
            value=_load(pathlib.Path(args.receipt)),
            dist=pathlib.Path(args.dist),
            expected_build_sha=args.expected_build_sha)
    print(f"V13_5_PRE_MUTATION_REHEARSAL_{args.command.upper()}=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
