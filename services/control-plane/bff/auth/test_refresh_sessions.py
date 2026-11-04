"""Dev-login rotating refresh credential: store semantics and HTTP contract."""
from __future__ import annotations

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.handlers import create_auth_handlers
from services.control_plane.bff.auth.policy import create_auth_dependencies
from services.control_plane.bff.auth import refresh_sessions
from services.control_plane.bff.auth.refresh_sessions import REUSE_GRACE_SECONDS, RefreshRejected, RefreshSessionStore
from services.control_plane.bff.auth.router import create_auth_router
from services.control_plane.bff.auth.service import AuthFacadeService
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore

ORIGIN = "https://app.example.test"
LOGIN = {"grant_type": "client_credentials", "client_id": "ci-client", "client_secret": "ci-secret", "browser_session": True}
HEADERS = {"Origin": ORIGIN}


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def _build(tmp_path) -> FastAPI:
    store = SessionLifecycleStore(str(tmp_path / "session_lifecycle.json"))
    deps = create_auth_dependencies(session_lifecycle_store=store)
    handlers = create_auth_handlers(dependencies=deps)
    service = AuthFacadeService(local_readiness=handlers["bff_auth_readiness"], handlers=handlers)
    app = FastAPI()
    app.include_router(create_auth_router(service=service, browser_origin_allowed=lambda origin: origin == ORIGIN))
    register_error_handlers(app)
    return app


@pytest.fixture(autouse=True)
def dev_env(monkeypatch):
    for key, value in {
        "PANTHEON_BFF_AUTH_STUB": "", "PANTHEON_BFF_AUTH_MODE": "strict", "PANTHEON_BFF_MFA_REQUIRED": "false",
        "PANTHEON_BFF_JWT_SECRET": "refresh-sessions-test-secret", "PANTHEON_BFF_JWT_ISSUER": "pantheon-dev",
        "PANTHEON_BFF_JWT_AUDIENCE": "bff-operators", "PANTHEON_ENV": "dev", "PANTHEON_DEPLOYMENT_STAGE": "dev",
        "PANTHEON_BFF_OIDC_CLIENT_ID": "ci-client", "PANTHEON_BFF_OIDC_CLIENT_SECRET": "ci-secret",
        "PANTHEON_BFF_TENANT_ID": "tenant-alpha",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PANTHEON_BFF_DEV_LOGIN_REFRESH_TTL_SECONDS", raising=False)


def _client(tmp_path) -> TestClient:
    return TestClient(_build(tmp_path), base_url="https://testserver")


def _login(client: TestClient):
    response = client.post("/bff/auth/dev-login", json=LOGIN, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response


def _expire_access(client: TestClient) -> None:
    client.cookies.delete("pantheon_session", path="/")


# ---- store ---------------------------------------------------------------

def test_store_persists_only_hashes_and_rotation_keeps_absolute_expiry(tmp_path):
    clock = Clock()
    path = str(tmp_path / "refresh.json")
    store = RefreshSessionStore(path, clock=clock)
    first = store.issue(identity="operator", client_id="c", roles=["operator"], tenant_id="t", ttl_seconds=1000)
    assert first.token not in open(path).read()
    clock.now += 500
    second = store.rotate(first.token)
    assert second.token != first.token and second.expires_at == first.expires_at
    # Reload from disk: the rotated credential still works, the old one is reuse.
    reloaded = RefreshSessionStore(path, clock=clock)
    third = reloaded.rotate(second.token)
    assert third.expires_at == first.expires_at
    clock.now += 600
    with pytest.raises(RefreshRejected) as exc:
        reloaded.rotate(third.token)
    assert exc.value.reason == "expired"


def test_store_reuse_revokes_whole_chain(tmp_path):
    clock = Clock()
    store = RefreshSessionStore(str(tmp_path / "refresh.json"), clock=clock)
    first = store.issue(identity="operator", client_id="c", roles=["operator"], tenant_id="t")
    second = store.rotate(first.token)
    clock.now += REUSE_GRACE_SECONDS + 1
    with pytest.raises(RefreshRejected) as exc:
        store.rotate(first.token)
    assert exc.value.reason == "reuse_detected"
    with pytest.raises(RefreshRejected):
        store.rotate(second.token)
    with pytest.raises(RefreshRejected):
        store.rotate("pdr1.never-issued")


def test_store_previous_credential_within_grace_does_not_rotate_or_revoke(tmp_path):
    clock = Clock()
    path = str(tmp_path / "refresh.json")
    store = RefreshSessionStore(path, clock=clock)
    first = store.issue(identity="operator", client_id="c", roles=["operator"], tenant_id="t", ttl_seconds=1000)
    second = store.rotate(first.token)
    before = open(path).read()
    clock.now += REUSE_GRACE_SECONDS
    grace = store.rotate(first.token)
    assert grace.token is None and grace.expires_at == first.expires_at and grace.chain_id == first.chain_id
    assert open(path).read() == before
    assert store.rotate(first.token).token is None
    third = store.rotate(second.token)
    assert third.token
    # Only the immediately previous credential is tolerated.
    with pytest.raises(RefreshRejected):
        store.rotate(first.token)
    with pytest.raises(RefreshRejected):
        store.rotate(third.token)


def test_store_grace_expires_and_older_credentials_revoke(tmp_path):
    clock = Clock()
    store = RefreshSessionStore(str(tmp_path / "refresh.json"), clock=clock)
    first = store.issue(identity="operator", client_id="c", roles=["operator"], tenant_id="t")
    second = store.rotate(first.token)
    third = store.rotate(second.token)
    with pytest.raises(RefreshRejected) as exc:
        store.rotate(first.token)  # two generations old, inside the time window
    assert exc.value.reason == "reuse_detected"
    with pytest.raises(RefreshRejected):
        store.rotate(third.token)


def test_store_grace_keeps_validate_and_expiry_checks(tmp_path):
    clock = Clock()
    store = RefreshSessionStore(str(tmp_path / "refresh.json"), clock=clock)
    first = store.issue(identity="operator", client_id="c", roles=["operator"], tenant_id="t", ttl_seconds=100)
    store.rotate(first.token)

    def deny(_record):
        raise RefreshRejected("identity_changed")

    with pytest.raises(RefreshRejected) as exc:
        store.rotate(first.token, validate=deny)
    assert exc.value.reason == "identity_changed"
    clock.now += 100
    with pytest.raises(RefreshRejected) as exc:
        store.rotate(first.token)
    assert exc.value.reason == "expired"


def test_store_record_without_previous_hash_still_works(tmp_path):
    clock = Clock()
    path = tmp_path / "refresh.json"
    store = RefreshSessionStore(str(path), clock=clock)
    first = store.issue(identity="operator", client_id="c", roles=["operator"], tenant_id="t")
    second = store.rotate(first.token)
    data = json.loads(path.read_text())
    for record in data["chains"].values():
        record.pop("previous_hash", None)  # legacy on-disk shape
    path.write_text(json.dumps(data))
    reloaded = RefreshSessionStore(str(path), clock=clock)
    with pytest.raises(RefreshRejected) as exc:
        reloaded.rotate(first.token)  # unrecorded predecessor: revoke as before
    assert exc.value.reason == "reuse_detected"
    with pytest.raises(RefreshRejected):
        reloaded.rotate(second.token)

    legacy = RefreshSessionStore(str(tmp_path / "legacy.json"), clock=clock)
    one = legacy.issue(identity="operator", client_id="c", roles=["operator"], tenant_id="t")
    data = json.loads((tmp_path / "legacy.json").read_text())
    for record in data["chains"].values():
        record.pop("previous_hash", None)
    (tmp_path / "legacy.json").write_text(json.dumps(data))
    assert RefreshSessionStore(str(tmp_path / "legacy.json"), clock=clock).rotate(one.token).token


def test_store_persists_only_hash_of_previous_credential(tmp_path):
    path = tmp_path / "refresh.json"
    store = RefreshSessionStore(str(path))
    first = store.issue(identity="operator", client_id="c", roles=["operator"], tenant_id="t")
    second = store.rotate(first.token)
    text = path.read_text()
    assert first.token not in text and second.token not in text
    record = next(iter(json.loads(text)["chains"].values()))
    assert record["previous_hash"] == refresh_sessions._hash(first.token)


def test_store_file_is_json_with_chain_records(tmp_path):
    path = tmp_path / "refresh.json"
    RefreshSessionStore(str(path)).issue(identity="operator", client_id="c", roles=["operator"], tenant_id="t")
    assert len(json.loads(path.read_text())["chains"]) == 1
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


# ---- HTTP contract ---------------------------------------------------------

def test_dev_login_sets_access_and_refresh_cookies(tmp_path):
    client = _client(tmp_path)
    response = _login(client)
    cookies = response.headers.get_list("set-cookie")
    refresh = next(c for c in cookies if c.startswith("pantheon_refresh_token="))
    assert "HttpOnly" in refresh and "Secure" in refresh and "SameSite=lax" in refresh and "Path=/bff/auth" in refresh
    assert "Max-Age=2592000" in refresh or "Max-Age=2591999" in refresh
    assert any(c.startswith("pantheon_session=") for c in cookies)
    body = response.json()
    assert "access_token" not in body and "refresh_token" not in body
    assert body["refresh_expires_at"] and body["expires_at"]
    stored = list(json.loads((tmp_path / "dev_login_refresh_sessions.json").read_text())["chains"].values())
    assert stored and "refresh_token" not in json.dumps(stored)


def test_non_browser_dev_login_issues_no_refresh_cookie(tmp_path):
    client = _client(tmp_path)
    response = client.post("/bff/auth/dev-login", json={**LOGIN, "browser_session": False})
    assert response.status_code == 200
    assert "pantheon_refresh_token" not in response.headers.get("set-cookie", "")
    assert "refresh_token" not in response.json()


def test_refresh_with_expired_access_reissues_access_and_rotates(tmp_path):
    client = _client(tmp_path)
    login = _login(client)
    first_refresh = client.cookies.get("pantheon_refresh_token", path="/bff/auth")
    _expire_access(client)
    assert client.get("/bff/me").status_code == 401

    response = client.post("/bff/auth/refresh", json={}, headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["refresh_expires_at"] == login.json()["refresh_expires_at"]
    assert data["session"]["expires_at"] and data["session"]["refresh_expires_at"] == data["refresh_expires_at"]
    assert client.cookies.get("pantheon_refresh_token", path="/bff/auth") != first_refresh
    me = client.get("/bff/me")
    assert me.status_code == 200, me.text
    assert me.json()["data"]["session"]["refresh_expires_at"] == data["refresh_expires_at"]


def _patch_time(monkeypatch):
    clock = Clock()
    clock.now = refresh_sessions.time.time()
    monkeypatch.setattr(refresh_sessions.time, "time", clock)
    return clock


def _with_cookie(client, token):
    other = TestClient(client.app, base_url="https://testserver")
    other.cookies.set("pantheon_refresh_token", token, path="/bff/auth")
    return other


def test_previous_credential_within_grace_reissues_access_only(tmp_path, monkeypatch):
    clock = _patch_time(monkeypatch)
    client = _client(tmp_path)
    login = _login(client)
    old = client.cookies.get("pantheon_refresh_token", path="/bff/auth")
    assert client.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 200
    newer = client.cookies.get("pantheon_refresh_token", path="/bff/auth")
    clock.now += 59

    tab = _with_cookie(client, old)
    response = tab.post("/bff/auth/refresh", json={}, headers=HEADERS)
    assert response.status_code == 200, response.text
    cookies = response.headers.get_list("set-cookie")
    assert any(c.startswith("pantheon_session=") for c in cookies)
    assert not any(c.startswith("pantheon_refresh_token=") for c in cookies)
    body = response.json()
    assert body["meta"]["auth"]["rotated"] is False
    assert body["data"]["expires_at"] and body["data"]["refresh_expires_at"] == login.json()["refresh_expires_at"]
    assert _with_cookie(client, newer).post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 200


def test_two_tabs_with_same_credential_both_succeed(tmp_path, monkeypatch):
    _patch_time(monkeypatch)
    client = _client(tmp_path)
    _login(client)
    shared = client.cookies.get("pantheon_refresh_token", path="/bff/auth")
    first = _with_cookie(client, shared).post("/bff/auth/refresh", json={}, headers=HEADERS)
    second = _with_cookie(client, shared).post("/bff/auth/refresh", json={}, headers=HEADERS)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["meta"]["auth"]["rotated"] is True and second.json()["meta"]["auth"]["rotated"] is False
    rotated = next(c for c in first.headers.get_list("set-cookie") if c.startswith("pantheon_refresh_token="))
    token = rotated.split(";", 1)[0].split("=", 1)[1]
    assert _with_cookie(client, token).post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 200


def test_grace_path_keeps_identity_check_and_logout(tmp_path, monkeypatch):
    _patch_time(monkeypatch)
    client = _client(tmp_path)
    _login(client)
    old = client.cookies.get("pantheon_refresh_token", path="/bff/auth")
    assert client.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 200
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-other")
    assert _with_cookie(client, old).post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 401
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    # identity failure does not revoke; logout then does.
    assert _with_cookie(client, old).post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 200
    assert client.post("/bff/logout", json={}, headers=HEADERS).status_code == 200
    assert _with_cookie(client, old).post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 401


def test_reused_refresh_credential_revokes_chain(tmp_path, monkeypatch):
    clock = _patch_time(monkeypatch)
    client = _client(tmp_path)
    _login(client)
    old = client.cookies.get("pantheon_refresh_token", path="/bff/auth")
    assert client.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 200
    newer = client.cookies.get("pantheon_refresh_token", path="/bff/auth")
    clock.now += 61

    attacker = TestClient(client.app, base_url="https://testserver")
    attacker.cookies.set("pantheon_refresh_token", old, path="/bff/auth")
    assert attacker.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 401

    victim = TestClient(client.app, base_url="https://testserver")
    victim.cookies.set("pantheon_refresh_token", newer, path="/bff/auth")
    assert victim.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 401


def test_refresh_requires_allowed_origin(tmp_path):
    client = _client(tmp_path)
    _login(client)
    _expire_access(client)
    assert client.post("/bff/auth/refresh", json={}).status_code == 403
    assert client.post("/bff/auth/refresh", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 200


def test_refresh_fails_closed_when_identity_binding_changes(tmp_path, monkeypatch):
    client = _client(tmp_path)
    _login(client)
    _expire_access(client)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-other")
    assert client.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 401

    client = _client(tmp_path / "second")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    _login(client)
    _expire_access(client)
    monkeypatch.delenv("PANTHEON_BFF_OIDC_CLIENT_SECRET")
    assert client.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 401


def test_refresh_fails_after_absolute_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_DEV_LOGIN_REFRESH_TTL_SECONDS", "1")
    client = _client(tmp_path)
    _login(client)
    _expire_access(client)
    chains = tmp_path / "dev_login_refresh_sessions.json"
    data = json.loads(chains.read_text())
    for record in data["chains"].values():
        record["expires_at"] = 1
    chains.write_text(json.dumps(data))
    assert client.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 401


def test_logout_revokes_chain_and_deletes_cookies(tmp_path):
    client = _client(tmp_path)
    _login(client)
    old = client.cookies.get("pantheon_refresh_token", path="/bff/auth")
    response = client.post("/bff/logout", json={}, headers=HEADERS)
    assert response.status_code == 200, response.text
    cleared = response.headers.get_list("set-cookie")
    assert any(c.startswith("pantheon_refresh_token=") and "Max-Age=0" in c for c in cleared)
    assert any(c.startswith("pantheon_session=") and "Max-Age=0" in c for c in cleared)

    stale = TestClient(client.app, base_url="https://testserver")
    stale.cookies.set("pantheon_refresh_token", old, path="/bff/auth")
    assert stale.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 401


def test_logout_with_only_refresh_cookie_still_revokes(tmp_path):
    client = _client(tmp_path)
    _login(client)
    old = client.cookies.get("pantheon_refresh_token", path="/bff/auth")
    _expire_access(client)
    client.cookies.set("pantheon_refresh_token", old, path="/")
    assert client.post("/bff/logout", json={}, headers=HEADERS).status_code == 200
    assert client.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 401


def test_forbidden_environment_disables_dev_login_and_cookie_refresh(tmp_path, monkeypatch):
    client = _client(tmp_path)
    _login(client)
    _expire_access(client)
    monkeypatch.setenv("PANTHEON_ENV", "staging")
    assert client.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 403
    assert client.post("/bff/auth/dev-login", json=LOGIN, headers=HEADERS).status_code == 403


def test_store_survives_new_app_instance(tmp_path):
    client = _client(tmp_path)
    _login(client)
    _expire_access(client)
    restarted = TestClient(_build(tmp_path), base_url="https://testserver")
    restarted.cookies.set("pantheon_refresh_token", client.cookies.get("pantheon_refresh_token", path="/bff/auth"), path="/bff/auth")
    assert restarted.post("/bff/auth/refresh", json={}, headers=HEADERS).status_code == 200
