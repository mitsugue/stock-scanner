#!/usr/bin/env python3
"""Fail-closed public frontend/backend identity convergence gate.

The Pages deployment API returning success is not proof that the public CDN is
serving the new release.  This gate polls the actual public index together with
the live backend health/readiness documents and authorizes the warm-profile
seed only when all release coordinates are exact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple


SCHEMA = "argus-public-candidate-release-gate-v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FRONTEND_VERSION_RE = re.compile(
    r"globalThis\.__ARGUS_VERSION__\s*=\s*[\"']([^\"']+)[\"']")
PRODUCT_VERSION_RE = re.compile(
    r"globalThis\.__ARGUS_PRODUCT_VERSION__\s*=\s*[\"']([^\"']+)[\"']")
FRONTEND_SHA_RE = re.compile(
    r"globalThis\.__ARGUS_BUILD_SHA__\s*=\s*[\"']([^\"']+)[\"']")
MODULE_ASSET_RE = re.compile(
    r"<script[^>]+type=[\"']module[\"'][^>]+src=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)


class CandidateReleaseGateError(RuntimeError):
    pass


def _exact_string(value: Any) -> Optional[str]:
    return value if type(value) is str and value else None


def parse_public_frontend(html: str, *, status: int = 200,
                          headers: Optional[Mapping[str, str]] = None) \
        -> Dict[str, Any]:
    if type(html) is not str:
        html = ""
    headers = headers or {}

    def match(pattern: re.Pattern[str]) -> Optional[str]:
        found = pattern.search(html)
        return found.group(1) if found else None

    asset = match(MODULE_ASSET_RE)
    return {
        "httpStatus": status,
        "frontendVersion": match(FRONTEND_VERSION_RE),
        "productVersion": match(PRODUCT_VERSION_RE),
        "buildSha": match(FRONTEND_SHA_RE),
        "moduleAsset": asset,
        "htmlSha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "etag": _exact_string(headers.get("etag") or headers.get("ETag")),
        "lastModified": _exact_string(
            headers.get("last-modified") or headers.get("Last-Modified")),
    }


def evaluate_candidate_release(*, expected: Mapping[str, str],
                               public_frontend: Mapping[str, Any],
                               health: Mapping[str, Any],
                               ready: Mapping[str, Any],
                               accept_backend_successor: Optional[
                                   Callable[[str], bool]] = None) -> Dict[str, Any]:
    expected_frontend_sha = expected.get("frontendSha")
    expected_backend_sha = expected.get("backendSha")
    # v13.5.64: a backend that already contains the candidate (a later main
    # merge redeployed Render during the wait) is accepted when the caller's
    # ancestry check says so; the evidence records the substitution.
    backend_accepted_as_successor = False
    observed_backend_sha = str(health.get("buildSha") or "")
    if expected_backend_sha and observed_backend_sha \
            and observed_backend_sha != expected_backend_sha \
            and accept_backend_successor is not None \
            and SHA_RE.fullmatch(observed_backend_sha) is not None \
            and ready.get("buildSha") == observed_backend_sha:
        try:
            backend_accepted_as_successor = bool(
                accept_backend_successor(observed_backend_sha))
        except Exception:
            backend_accepted_as_successor = False
        if backend_accepted_as_successor:
            expected_backend_sha = observed_backend_sha
    frontend_exact = (
        public_frontend.get("httpStatus") == 200
        and public_frontend.get("productVersion") == expected.get(
            "productVersion")
        and public_frontend.get("frontendVersion") == expected.get(
            "frontendVersion")
        and public_frontend.get("buildSha") == expected_frontend_sha
        and type(public_frontend.get("moduleAsset")) is str
        and public_frontend.get("moduleAsset", "").startswith(
            "/argus/assets/")
    )
    backend_identity_exact = (
        health.get("status") == "ok"
        and health.get("backendVersion") == expected.get("backendVersion")
        and SHA_RE.fullmatch(str(health.get("buildSha") or "")) is not None
        and ready.get("ready") is True
        and ready.get("buildSha") == health.get("buildSha")
    )
    if expected_backend_sha:
        backend_identity_exact = (
            backend_identity_exact
            and health.get("buildSha") == expected_backend_sha
        )
    if not frontend_exact:
        reason = "candidate_frontend_not_live"
    elif not backend_identity_exact:
        reason = "candidate_backend_not_live"
    else:
        reason = "candidate_release_exact"
    return {
        "status": "READY" if frontend_exact and backend_identity_exact
        else "WAIT",
        "reason": reason,
        "frontendExact": frontend_exact,
        "backendExact": backend_identity_exact,
        "backendAcceptedAsSuccessor": backend_accepted_as_successor,
        "backendShaEvaluated": expected_backend_sha or None,
        "canSeedWarmProfile": frontend_exact and backend_identity_exact,
    }


def release_transition(*, deploy_succeeded: bool, identity_ready: bool,
                       seed_succeeded: Optional[bool]) -> str:
    """Pure executable DAG contract used by release-order regression tests."""
    if not deploy_succeeded:
        return "DEPLOY_BLOCKED"
    if not identity_ready:
        return "IDENTITY_WAIT"
    if seed_succeeded is None:
        return "SEED_ALLOWED"
    return "ACCEPTANCE_ALLOWED" if seed_succeeded \
        else "ACCEPTANCE_BLOCKED"


def _get_text(url: str, *, timeout: float) \
        -> Tuple[int, str, Mapping[str, str]]:
    request = urllib.request.Request(url, headers={
        "Accept": "text/html,application/json",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
        "User-Agent": "argus-public-candidate-release-gate/1",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(8 * 1024 * 1024 + 1)
        if len(body) > 8 * 1024 * 1024:
            raise CandidateReleaseGateError("public_identity_response_too_large")
        return response.status, body.decode("utf-8"), dict(response.headers)


def _get_json(url: str, *, timeout: float) -> Tuple[int, Mapping[str, Any]]:
    status, text, _headers = _get_text(url, timeout=timeout)
    value = json.loads(text)
    if type(value) is not dict:
        raise CandidateReleaseGateError("public_identity_json_not_object")
    return status, value


def poll_candidate_release(*, public_url: str, backend_url: str,
                           expected: Mapping[str, str], timeout_seconds: int,
                           poll_seconds: int,
                           fetch_text: Callable[..., Tuple[
                               int, str, Mapping[str, str]]] = _get_text,
                           fetch_json: Callable[..., Tuple[
                               int, Mapping[str, Any]]] = _get_json,
                           clock: Callable[[], float] = time.monotonic,
                           sleeper: Callable[[float], None] = time.sleep,
                           accept_backend_successor: Optional[
                               Callable[[str], bool]] = None) \
        -> Dict[str, Any]:
    if not SHA_RE.fullmatch(expected.get("frontendSha", "")):
        raise CandidateReleaseGateError("invalid_expected_frontend_sha")
    backend_sha = expected.get("backendSha", "")
    if backend_sha and not SHA_RE.fullmatch(backend_sha):
        raise CandidateReleaseGateError("invalid_expected_backend_sha")
    if timeout_seconds < 1 or timeout_seconds > 1800 \
            or poll_seconds < 1 or poll_seconds > 60:
        raise CandidateReleaseGateError("invalid_candidate_gate_bounds")
    public_url = public_url.rstrip("/") + "/"
    backend_url = backend_url.rstrip("/")
    started = clock()
    deadline = started + timeout_seconds
    attempts = []
    attempt = 0
    last_reason = "candidate_frontend_not_live"
    while True:
        attempt += 1
        public = {"httpStatus": None}
        health: Mapping[str, Any] = {}
        ready: Mapping[str, Any] = {}
        error = None
        try:
            query = urllib.parse.urlencode({
                "argus_candidate": expected["frontendSha"],
                "attempt": attempt,
            })
            status, html, headers = fetch_text(
                f"{public_url}?{query}", timeout=30)
            public = parse_public_frontend(
                html, status=status, headers=headers)
            health_status, health = fetch_json(
                f"{backend_url}/healthz", timeout=30)
            ready_status, ready = fetch_json(
                f"{backend_url}/readyz", timeout=30)
            if health_status != 200:
                health = dict(health, _httpStatus=health_status)
            if ready_status != 200:
                ready = dict(ready, _httpStatus=ready_status)
        except (CandidateReleaseGateError, json.JSONDecodeError,
                OSError, TimeoutError, urllib.error.URLError) as exc:
            error = str(exc)[:400]
        evaluation = evaluate_candidate_release(
            expected=expected, public_frontend=public,
            health=health, ready=ready,
            accept_backend_successor=accept_backend_successor)
        last_reason = evaluation["reason"]
        attempts.append({
            "attempt": attempt,
            "atSeconds": round(clock() - started, 3),
            "error": error,
            "evaluation": evaluation,
            "publicFrontend": public,
            "backend": {
                "health": {
                    key: health.get(key) for key in (
                        "status", "backendVersion", "buildSha", "_httpStatus")
                },
                "ready": {
                    key: ready.get(key) for key in (
                        "ready", "appVersion", "buildSha", "_httpStatus")
                },
            },
        })
        if evaluation["canSeedWarmProfile"]:
            return {
                "schemaVersion": SCHEMA,
                "status": "PASS",
                "classification": "CANDIDATE_RELEASE_EXACT",
                "expected": dict(expected),
                "observed": attempts[-1],
                "attemptCount": attempt,
                "convergenceSeconds": round(clock() - started, 3),
                "attempts": attempts[-64:],
            }
        now = clock()
        if now >= deadline:
            classification = "PAGES_DEPLOYMENT_IDENTITY_FAILURE" \
                if last_reason == "candidate_frontend_not_live" \
                else "BACKEND_RELEASE_IDENTITY_FAILURE"
            return {
                "schemaVersion": SCHEMA,
                "status": "FAIL",
                "classification": classification,
                "reason": last_reason,
                "expected": dict(expected),
                "observed": attempts[-1],
                "attemptCount": attempt,
                "convergenceSeconds": round(now - started, 3),
                "attempts": attempts[-64:],
            }
        sleeper(min(poll_seconds, max(0.0, deadline - now)))


def git_successor_acceptor(candidate_sha: str, branch: str,
                           run: Callable[..., Any] = None) -> Callable[[str], bool]:
    """True only when the observed sha descends from the candidate and is
    reachable from the release branch head (fetched fresh on every call)."""
    import re as _re
    import subprocess

    runner = run or (lambda cmd: subprocess.run(
        cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    def accept(observed: str) -> bool:
        if not SHA_RE.fullmatch(str(observed or "")) \
                or not _re.fullmatch(r"[A-Za-z0-9._/-]{1,80}", str(branch or "")):
            return False
        try:
            runner(["git", "fetch", "--quiet", "origin", str(branch)])
            runner(["git", "merge-base", "--is-ancestor", candidate_sha, observed])
            runner(["git", "merge-base", "--is-ancestor", observed, f"origin/{branch}"])
            return True
        except Exception:
            return False
    return accept


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--expected-product-version", required=True)
    parser.add_argument("--expected-frontend-version", required=True)
    parser.add_argument("--expected-frontend-sha", required=True)
    parser.add_argument("--expected-backend-version", required=True)
    parser.add_argument("--expected-backend-sha", default="")
    parser.add_argument("--accept-backend-successors-on", default="",
                        help="release branch; a live backend sha that descends "
                             "from the expected sha on this branch is accepted")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    expected = {
        "productVersion": args.expected_product_version,
        "frontendVersion": args.expected_frontend_version,
        "frontendSha": args.expected_frontend_sha,
        "backendVersion": args.expected_backend_version,
        "backendSha": args.expected_backend_sha,
    }
    result = poll_candidate_release(
        public_url=args.public_url,
        backend_url=args.backend_url,
        expected=expected,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
        accept_backend_successor=(
            git_successor_acceptor(args.expected_backend_sha,
                                   args.accept_backend_successors_on)
            if args.accept_backend_successors_on and args.expected_backend_sha
            else None),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": result["status"],
        "classification": result["classification"],
        "attemptCount": result["attemptCount"],
        "convergenceSeconds": result["convergenceSeconds"],
    }, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
