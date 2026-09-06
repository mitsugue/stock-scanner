import importlib.util
from pathlib import Path


ROOT = Path(__file__).parent
PATH = ROOT / "scripts" / "verify_public_candidate_release.py"
SPEC = importlib.util.spec_from_file_location("candidate_release", PATH)
candidate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(candidate)

FRONTEND_SHA = "a" * 40
BACKEND_SHA = "b" * 40
OLD_SHA = "c" * 40
EXPECTED = {
    "productVersion": "v13.5.57",
    "frontendVersion": "13.3.6",
    "frontendSha": FRONTEND_SHA,
    "backendVersion": "13.4.13",
    "backendSha": BACKEND_SHA,
}


def html(sha=FRONTEND_SHA):
    return f'''<!doctype html><html><head><script>
globalThis.__ARGUS_VERSION__="13.3.6";
globalThis.__ARGUS_PRODUCT_VERSION__="v13.5.57";
globalThis.__ARGUS_BUILD_SHA__="{sha}";
</script><script type="module" src="/argus/assets/index-CANDIDATE.js"></script>
</head></html>'''


def health(sha=BACKEND_SHA):
    return {"status": "ok", "backendVersion": "13.4.13", "buildSha": sha}


def ready(sha=BACKEND_SHA):
    return {"ready": True, "appVersion": "13.4.13", "buildSha": sha}


def parsed(sha=FRONTEND_SHA):
    return candidate.parse_public_frontend(
        html(sha), headers={"ETag": '"candidate"',
                            "Last-Modified": "Mon, 17 Aug 2026 00:00:00 GMT"})


def test_public_identity_parser_binds_product_component_sha_and_asset():
    value = parsed()
    assert value["productVersion"] == "v13.5.57"
    assert value["frontendVersion"] == "13.3.6"
    assert value["buildSha"] == FRONTEND_SHA
    assert value["moduleAsset"] == "/argus/assets/index-CANDIDATE.js"
    assert len(value["htmlSha256"]) == 64
    assert value["lastModified"].startswith("Mon, 17 Aug")


def test_candidate_not_deployed_cannot_seed():
    value = candidate.evaluate_candidate_release(
        expected=EXPECTED,
        public_frontend=candidate.parse_public_frontend("", status=404),
        health=health(), ready=ready())
    assert value["canSeedWarmProfile"] is False
    assert value["reason"] == "candidate_frontend_not_live"
    assert candidate.release_transition(
        deploy_succeeded=False, identity_ready=False,
        seed_succeeded=None) == "DEPLOY_BLOCKED"


def test_deploy_success_with_old_public_identity_waits_and_refuses_seed():
    value = candidate.evaluate_candidate_release(
        expected=EXPECTED, public_frontend=parsed(OLD_SHA),
        health=health(), ready=ready())
    assert value["frontendExact"] is False
    assert value["backendExact"] is True
    assert value["reason"] == "candidate_frontend_not_live"
    assert candidate.release_transition(
        deploy_succeeded=True, identity_ready=False,
        seed_succeeded=None) == "IDENTITY_WAIT"


def test_exact_public_candidate_with_old_backend_refuses_seed():
    value = candidate.evaluate_candidate_release(
        expected=EXPECTED, public_frontend=parsed(),
        health=health(OLD_SHA), ready=ready(OLD_SHA))
    assert value["frontendExact"] is True
    assert value["backendExact"] is False
    assert value["reason"] == "candidate_backend_not_live"


def test_both_exact_allow_seed_and_only_success_allows_acceptance():
    value = candidate.evaluate_candidate_release(
        expected=EXPECTED, public_frontend=parsed(),
        health=health(), ready=ready())
    assert value["canSeedWarmProfile"] is True
    assert candidate.release_transition(
        deploy_succeeded=True, identity_ready=True,
        seed_succeeded=None) == "SEED_ALLOWED"
    assert candidate.release_transition(
        deploy_succeeded=True, identity_ready=True,
        seed_succeeded=True) == "ACCEPTANCE_ALLOWED"
    assert candidate.release_transition(
        deploy_succeeded=True, identity_ready=True,
        seed_succeeded=False) == "ACCEPTANCE_BLOCKED"


class Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def test_bounded_poll_waits_for_public_candidate_then_passes():
    clock = Clock()
    calls = {"page": 0}

    def fetch_text(_url, timeout):
        assert timeout == 30
        calls["page"] += 1
        observed = OLD_SHA if calls["page"] < 3 else FRONTEND_SHA
        return 200, html(observed), {"ETag": f'"{observed}"'}

    def fetch_json(url, timeout):
        assert timeout == 30
        return (200, ready()) if url.endswith("/readyz") else (200, health())

    result = candidate.poll_candidate_release(
        public_url="https://example.test/argus/",
        backend_url="https://backend.test",
        expected=EXPECTED, timeout_seconds=30, poll_seconds=5,
        fetch_text=fetch_text, fetch_json=fetch_json,
        clock=clock.now, sleeper=clock.sleep)
    assert result["status"] == "PASS"
    assert result["classification"] == "CANDIDATE_RELEASE_EXACT"
    assert result["attemptCount"] == 3
    assert result["convergenceSeconds"] == 10.0


def test_bounded_poll_classifies_stale_pages_failure():
    clock = Clock()

    def fetch_text(_url, timeout):
        return 200, html(OLD_SHA), {}

    def fetch_json(url, timeout):
        return (200, ready()) if url.endswith("/readyz") else (200, health())

    result = candidate.poll_candidate_release(
        public_url="https://example.test/argus/",
        backend_url="https://backend.test",
        expected=EXPECTED, timeout_seconds=10, poll_seconds=5,
        fetch_text=fetch_text, fetch_json=fetch_json,
        clock=clock.now, sleeper=clock.sleep)
    assert result["status"] == "FAIL"
    assert result["classification"] == "PAGES_DEPLOYMENT_IDENTITY_FAILURE"
    assert result["reason"] == "candidate_frontend_not_live"


def test_bounded_poll_classifies_backend_identity_failure():
    clock = Clock()

    def fetch_text(_url, timeout):
        return 200, html(), {}

    def fetch_json(url, timeout):
        old = ready(OLD_SHA) if url.endswith("/readyz") else health(OLD_SHA)
        return 200, old

    result = candidate.poll_candidate_release(
        public_url="https://example.test/argus/",
        backend_url="https://backend.test",
        expected=EXPECTED, timeout_seconds=5, poll_seconds=5,
        fetch_text=fetch_text, fetch_json=fetch_json,
        clock=clock.now, sleeper=clock.sleep)
    assert result["status"] == "FAIL"
    assert result["classification"] == "BACKEND_RELEASE_IDENTITY_FAILURE"
