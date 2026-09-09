"""Focused regressions for extracted BFF auth/session policy and dependency injection.

Covers Acceptance Criterion 5 of BFF-AUTH-SESSION-SEAM-PREREQUISITE-001:
1. Standalone auth assembly and policy import with no main.py dependency.
2. Two isolated SessionLifecycleStore instances with the same valid identity;
   logout in one yields typed 401 while the other remains valid.
3. Nested/concurrent clients and logout idempotency across lifecycle.
4. Session-key derivation variants (sid, session_id, jti, env, fallback) and legacy state.
5. Real cookie, bearer, and refresh flows with synthetic signing keys.
6. Authentic negative JWT signature, expiration, role restriction, and tenant scoping.
7. Composition smoke proving the default composition root factory is properly wired.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256
from ..models import ErrorCode, OperatorIdentity
from ..session_lifecycle_store import SessionLifecycleStore
from .handlers import create_auth_handlers
from .policy import (
    AuthDependencies,
    SessionLogoutGuard,
    bff_error,
    bff_me_tenant_payload,
    create_auth_dependencies,
    create_session_logout_guard,
    get_legacy_session_key,
    get_session_id,
    get_session_key,
    get_session_state,
    raise_if_session_logged_out,
)
from .router import create_auth_router
from .service import AuthFacadeService

TEST_JWT_SECRET = "synthetic-test-key-32-chars-long-xxx"
TEST_JWT_ISSUER = "synthetic-test-issuer"
TEST_JWT_AUDIENCE = "bff-operators"


def _make_jwt(
    *,
    subject: str = "test-operator",
    roles: Optional[List[str]] = None,
    secret: str = TEST_JWT_SECRET,
    issuer: str = TEST_JWT_ISSUER,
    audience: str = TEST_JWT_AUDIENCE,
    expired: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    now = int(time.time())
    exp = now - 3600 if expired else now + 3600
    payload: Dict[str, Any] = {
        "sub": subject,
        "roles": roles if roles is not None else ["operator"],
        "iss": issuer,
        "aud": audience,
        "iat": now - 60,
        "exp": exp,
    }
    if extra:
        payload.update(extra)
    return encode_jwt_hs256(payload, secret=secret)


def _build_standalone_app(
    store: SessionLifecycleStore,
    *,
    auth_mode: str = "permissive",
    auth_stub: bool = True,
    utc_now: Optional[Callable[[], str]] = None,
) -> FastAPI:
    """Build a standalone FastAPI app wired with isolated AuthDependencies."""
    deps = create_auth_dependencies(
        session_lifecycle_store=store,
        bff_auth_stub_enabled=lambda: auth_stub,
        bff_auth_mode=lambda: auth_mode,
        bff_source_commit=lambda: "test-commit-001",
        utc_now=utc_now,
    )
    handlers = create_auth_handlers(dependencies=deps)
    service = AuthFacadeService(
        local_readiness=handlers["bff_auth_readiness"],
        handlers=handlers,
    )
    app = FastAPI()
    app.include_router(create_auth_router(service=service))
    return app


def _extract_error(resp: Any) -> Dict[str, Any]:
    """Helper to extract error payload from either direct Pack-D JSON or FastAPI HTTPException detail."""
    body = resp.json()
    if isinstance(body, dict):
        if "error" in body and isinstance(body["error"], dict):
            return body["error"]
        if "detail" in body and isinstance(body["detail"], dict) and "error" in body["detail"]:
            return body["detail"]["error"]
    return {}


# ==============================================================================
# 1. Standalone Import Assertion
# ==============================================================================

def test_standalone_auth_policy_imports_no_main():
    """Prove auth.policy and auth.handlers can be loaded in an isolated process
    without importing services.control_plane.bff.main or root main.
    """
    code = (
        "import sys\n"
        "from services.control_plane.bff.auth import policy, handlers, router, service\n"
        "modules = set(sys.modules.keys())\n"
        "assert 'services.control_plane.bff.main' not in modules, (\n"
        "    f'services.control_plane.bff.main unexpectedly imported: {modules}'\n"
        ")\n"
        "assert 'main' not in modules, (\n"
        "    f'main unexpectedly imported: {modules}'\n"
        ")\n"
        "print('STANDALONE_IMPORT_CLEAN')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "STANDALONE_IMPORT_CLEAN" in result.stdout


# ==============================================================================
# 2. Two Isolated Stores Independent Logout
# ==============================================================================

def test_two_isolated_stores_independent_logout(tmp_path: Path):
    """Mount two distinct AuthDependencies/handlers with separate file-backed stores.
    Verify:
    1. Both respond 200 to the same valid identity.
    2. Logging out of instance 1 marks instance 1 401 on /bff/me, while instance 2 remains 200.
    3. Logging out of instance 2 marks instance 2 401 on /bff/me.
    """
    async def _run():
        store1 = SessionLifecycleStore(str(tmp_path / "store1.json"))
        store2 = SessionLifecycleStore(str(tmp_path / "store2.json"))

        app1 = _build_standalone_app(store1)
        app2 = _build_standalone_app(store2)

        headers = {"Authorization": "Bearer seam-operator:operator"}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app1), base_url="http://app1.test") as client1, \
                   httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://app2.test") as client2:

            # Step 1: Both start authenticated (200)
            resp1_before = await client1.get("/bff/me", headers=headers)
            resp2_before = await client2.get("/bff/me", headers=headers)
            assert resp1_before.status_code == 200
            assert resp2_before.status_code == 200

            # Step 2: Logout client 1
            logout1 = await client1.post("/bff/logout", headers=headers)
            assert logout1.status_code == 200

            # Step 3: Instance 1 is logged out (401), Instance 2 is still valid (200)
            resp1_after = await client1.get("/bff/me", headers=headers)
            resp2_after = await client2.get("/bff/me", headers=headers)

            assert resp1_after.status_code == 401
            err1 = _extract_error(resp1_after)
            assert err1.get("code") == "AUTH_REQUIRED"
            assert err1.get("details", {}).get("reason") == "SESSION_LOGGED_OUT"

            assert resp2_after.status_code == 200

            # Step 4: Logout client 2
            logout2 = await client2.post("/bff/logout", headers=headers)
            assert logout2.status_code == 200

            # Step 5: Now instance 2 is also logged out (401)
            resp2_final = await client2.get("/bff/me", headers=headers)
            assert resp2_final.status_code == 401
            err2 = _extract_error(resp2_final)
            assert err2.get("code") == "AUTH_REQUIRED"
            assert err2.get("details", {}).get("reason") == "SESSION_LOGGED_OUT"

    asyncio.run(_run())


# ==============================================================================
# 3. Nested/Concurrent Clients and Outer Idempotency
# ==============================================================================

def test_concurrent_clients_and_logout_idempotency(tmp_path: Path):
    """Test concurrent requests and ensure logout idempotency preserves consistent state."""
    async def _run():
        store = SessionLifecycleStore(str(tmp_path / "concurrent_store.json"))
        app = _build_standalone_app(store)
        headers = {"Authorization": "Bearer conc-operator:operator"}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # Concurrent /bff/me requests before logout
            tasks = [client.get("/bff/me", headers=headers) for _ in range(10)]
            results = await asyncio.gather(*tasks)
            for resp in results:
                assert resp.status_code == 200

            # First logout with explicit Idempotency-Key
            idem_headers = {**headers, "Idempotency-Key": "logout-idem-key-001"}
            logout1 = await client.post("/bff/logout", headers=idem_headers)
            assert logout1.status_code == 200
            data1 = logout1.json()
            assert data1["data"]["session"]["state"] == "logged_out"
            assert data1["meta"]["idempotency"]["replayed"] is False

            # Second logout replaying the exact idempotency key
            logout2 = await client.post("/bff/logout", headers=idem_headers)
            assert logout2.status_code == 200
            data2 = logout2.json()
            assert data2["data"]["session"]["state"] == "logged_out"
            assert data2["meta"]["idempotency"]["replayed"] is True

            # Third logout without idempotency key: still idempotently returns 200
            logout3 = await client.post("/bff/logout", headers=headers)
            assert logout3.status_code == 200
            data3 = logout3.json()
            assert data3["data"]["session"]["state"] == "logged_out"

            # Subsequent GET /bff/me fails with 401
            after = await client.get("/bff/me", headers=headers)
            assert after.status_code == 401

    asyncio.run(_run())


def test_nested_two_instance_lifecycle_outer_idempotency_survives_inner_teardown(tmp_path: Path):
    """Test nested two-instance lifecycle proving outer idempotency survives inner teardown.
    - Outer app and client are created with a shared session lifecycle store.
    - Outer issues POST /bff/logout with an idempotency key; receives initial 200 (replayed=False).
    - Inside outer client scope, an inner app and inner client instance are created with the same store.
    - Inner client performs active requests and its own logout with its own idempotency key; receives 200 (replayed=False).
    - Inner client is closed and inner instance is completely torn down.
    - Outer client replays the exact same POST /bff/logout request with its original idempotency key.
    - Outer client receives 200 with replayed=True, proving outer idempotency state survived inner instance teardown.
    - Subsequent GET /bff/me on outer client confirms session is logged out (401).
    """
    async def _run():
        store = SessionLifecycleStore(str(tmp_path / "nested_lifecycle_store.json"))
        app_outer = _build_standalone_app(store)
        app_inner = _build_standalone_app(store)

        outer_headers = {
            "Authorization": "Bearer outer-op:operator",
            "Idempotency-Key": "outer-logout-idem-001",
        }
        inner_headers = {
            "Authorization": "Bearer inner-op:operator",
            "Idempotency-Key": "inner-logout-idem-001",
        }

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_outer), base_url="http://outer.test") as client_outer:
            # 1. Outer client starts authenticated
            resp_outer_before = await client_outer.get("/bff/me", headers={"Authorization": "Bearer outer-op:operator"})
            assert resp_outer_before.status_code == 200

            # 2. Outer client performs first logout with idempotency key
            logout_outer_1 = await client_outer.post("/bff/logout", headers=outer_headers)
            assert logout_outer_1.status_code == 200
            data_outer_1 = logout_outer_1.json()
            assert data_outer_1["data"]["session"]["state"] == "logged_out"
            assert data_outer_1["meta"]["idempotency"]["replayed"] is False

            # 3. Inside outer lifecycle, spin up inner instance and inner client
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_inner), base_url="http://inner.test") as client_inner:
                resp_inner_before = await client_inner.get("/bff/me", headers={"Authorization": "Bearer inner-op:operator"})
                assert resp_inner_before.status_code == 200

                logout_inner = await client_inner.post("/bff/logout", headers=inner_headers)
                assert logout_inner.status_code == 200
                data_inner = logout_inner.json()
                assert data_inner["data"]["session"]["state"] == "logged_out"
                assert data_inner["meta"]["idempotency"]["replayed"] is False

                # Inner replay also works before teardown
                logout_inner_replay = await client_inner.post("/bff/logout", headers=inner_headers)
                assert logout_inner_replay.status_code == 200
                assert logout_inner_replay.json()["meta"]["idempotency"]["replayed"] is True

            # 4. Inner client is now closed and inner instance torn down.
            # Outer client replays its original request with its idempotency key.
            logout_outer_2 = await client_outer.post("/bff/logout", headers=outer_headers)
            assert logout_outer_2.status_code == 200
            data_outer_2 = logout_outer_2.json()
            assert data_outer_2["data"]["session"]["state"] == "logged_out"
            assert data_outer_2["meta"]["idempotency"]["replayed"] is True

            # 5. Subsequent GET /bff/me on outer client confirms 401 SESSION_LOGGED_OUT
            resp_outer_after = await client_outer.get("/bff/me", headers={"Authorization": "Bearer outer-op:operator"})
            assert resp_outer_after.status_code == 401
            err_outer = _extract_error(resp_outer_after)
            assert err_outer.get("code") == "AUTH_REQUIRED"
            assert err_outer.get("details", {}).get("reason") == "SESSION_LOGGED_OUT"

    asyncio.run(_run())


# ==============================================================================
# 4. Session-Key Variants and Legacy State
# ==============================================================================

def test_session_id_precedence_and_variants(monkeypatch: pytest.MonkeyPatch):
    """Test session ID derivation precedence: sid -> session_id -> jti -> env -> fallback."""
    # 1. sid takes top precedence
    id1 = OperatorIdentity(
        operator_id="op-1",
        roles=["operator"],
        claims={"sid": "sid-val", "session_id": "sess-val", "jti": "jti-val"},
    )
    assert get_session_id(id1) == "sid-val"

    # 2. session_id takes second precedence
    id2 = OperatorIdentity(
        operator_id="op-2",
        roles=["operator"],
        claims={"session_id": "sess-val", "jti": "jti-val"},
    )
    assert get_session_id(id2) == "sess-val"

    # 3. jti takes third precedence
    id3 = OperatorIdentity(
        operator_id="op-3",
        roles=["operator"],
        claims={"jti": "jti-val"},
    )
    assert get_session_id(id3) == "jti-val"

    # 4. PANTHEON_SESSION_ID takes fourth precedence
    monkeypatch.setenv("PANTHEON_SESSION_ID", "env-session-id")
    id4 = OperatorIdentity(operator_id="op-4", roles=["operator"], claims={})
    assert get_session_id(id4) == "env-session-id"

    # 5. fallback to bff-session-{operator_id}
    monkeypatch.delenv("PANTHEON_SESSION_ID", raising=False)
    id5 = OperatorIdentity(operator_id="op-5", roles=["operator"], claims={})
    assert get_session_id(id5) == "bff-session-op-5"


def test_session_key_and_legacy_fallback(tmp_path: Path):
    """Verify get_session_state resolves full session key first, then falls back to legacy key."""
    store = SessionLifecycleStore(str(tmp_path / "key_test.json"))

    identity = OperatorIdentity(
        operator_id="op-legacy",
        roles=["operator"],
        claims={"sid": "sess-abc"},
    )
    full_key = get_session_key(identity)
    legacy_key = get_legacy_session_key(identity)

    assert full_key == "operator:op-legacy:session:sess-abc"
    assert legacy_key == "operator:op-legacy"

    # Initially empty
    assert get_session_state(identity, store) == {}

    # Write only legacy key
    store.upsert_session(legacy_key, {"state": "logged_out", "legacy": True}, now="2026-09-09T00:00:00Z")
    state = get_session_state(identity, store)
    assert state.get("legacy") is True
    assert state.get("state") == "logged_out"

    # Write canonical key; canonical key now takes precedence
    store.upsert_session(full_key, {"state": "active", "legacy": False}, now="2026-09-09T00:00:00Z")
    state = get_session_state(identity, store)
    assert state.get("legacy") is False
    assert state.get("state") == "active"


def test_raise_if_session_logged_out_guard(tmp_path: Path):
    """Verify raise_if_session_logged_out raises typed 401 when logged out."""
    store = SessionLifecycleStore(str(tmp_path / "guard_test.json"))
    identity = OperatorIdentity(operator_id="op-guard", roles=["operator"], claims={})

    # Not logged out: does not raise
    raise_if_session_logged_out(identity, store)

    # Marked logged out: raises 401
    key = get_session_key(identity)
    store.upsert_session(key, {"state": "logged_out", "logged_out_at": "2026-09-09T00:00:00Z"}, now="2026-09-09T00:00:00Z")

    with pytest.raises(Exception) as exc_info:
        raise_if_session_logged_out(identity, store)
    exc = exc_info.value
    assert exc.status_code == 401
    assert exc.detail["error"]["code"] == "AUTH_REQUIRED"
    assert exc.detail["error"]["details"]["reason"] == "SESSION_LOGGED_OUT"


# ==============================================================================
# 5. Real Cookie, Bearer, and Refresh Flows
# ==============================================================================

def test_bearer_and_cookie_auth_flows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test strict JWT auth via Bearer header and pantheon_jwt cookie."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", TEST_JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", TEST_JWT_AUDIENCE)

    store = SessionLifecycleStore(str(tmp_path / "token_store.json"))
    app = _build_standalone_app(
        store,
        auth_mode="strict",
        auth_stub=False,
    )
    client = TestClient(app)

    token = _make_jwt(subject="jwt-operator", roles=["operator"])

    # 1. Bearer token
    resp_bearer = client.get("/bff/me", headers={"Authorization": f"Bearer {token}"})
    assert resp_bearer.status_code == 200, resp_bearer.text
    assert resp_bearer.json()["data"]["user"]["operator_id"] == "jwt-operator"

    # 2. Cookie token
    resp_cookie = client.get("/bff/me", cookies={"pantheon_session": token})
    assert resp_cookie.status_code == 200, resp_cookie.text
    assert resp_cookie.json()["data"]["user"]["operator_id"] == "jwt-operator"


def test_refresh_flow_with_bearer_and_cookie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test refresh endpoint with bearer token and pantheon_jwt cookie."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", TEST_JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", TEST_JWT_AUDIENCE)

    store = SessionLifecycleStore(str(tmp_path / "refresh_store.json"))
    app = _build_standalone_app(
        store,
        auth_mode="strict",
        auth_stub=False,
    )
    client = TestClient(app)

    token = _make_jwt(subject="refresh-operator", roles=["operator"], extra={"sid": "sess-refresh-01"})

    # Refresh via Bearer
    resp_refresh_bearer = client.post(
        "/bff/auth/refresh",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_refresh_bearer.status_code == 200, resp_refresh_bearer.text
    data = resp_refresh_bearer.json()["data"]
    assert data["session"]["authenticated"] is True
    assert data["session"]["last_refresh_credential_source"] == "bearer"

    # Refresh via Cookie
    resp_refresh_cookie = client.post(
        "/bff/auth/refresh",
        cookies={"pantheon_session": token},
    )
    assert resp_refresh_cookie.status_code == 200, resp_refresh_cookie.text
    data = resp_refresh_cookie.json()["data"]
    assert data["session"]["authenticated"] is True
    assert data["session"]["last_refresh_credential_source"] == "session_cookie"


def test_configured_session_key_refresh_readback_and_idempotency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Regression test for P1 and P2:
    With PANTHEON_SESSION_ID=configured-session, a standalone factory, and an advanceable clock:
    1. POST /bff/auth/refresh writes to canonical configured-session key in store,
       NOT the bff-session-review-op fallback key.
    2. Refresh response includes session.id = configured-session, last_refreshed_at,
       and last_refresh_credential_source.
    3. Idempotency replay with identical Idempotency-Key returns 200 with replayed=True,
       without mutating last_refreshed_at when the clock advances.
    4. Idempotency conflict with different payload returns 409 IDEMPOTENCY_CONFLICT,
       without mutating last_refreshed_at when the clock advances.
    5. Subsequent GET /bff/me reads back state using the canonical configured-session key
       and preserves initial last_refreshed_at and last_refresh_credential_source.
    6. Direct inspection of store verifies state is present under canonical key and absent
       under the unconfigured fallback key.
    """
    monkeypatch.setenv("PANTHEON_SESSION_ID", "configured-session")
    store = SessionLifecycleStore(str(tmp_path / "configured_session_store.json"))

    clock = ["2026-09-09T01:00:00Z"]
    app = _build_standalone_app(store, auth_mode="permissive", utc_now=lambda: clock[0])
    client = TestClient(app)

    headers = {
        "Authorization": "Bearer review-op:operator",
        "Idempotency-Key": "refresh-idem-configured-001",
    }

    # 1. Initial refresh at 01:00:00Z
    resp1 = client.post("/bff/auth/refresh", json={}, headers=headers)
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()["data"]
    assert data1["session"]["id"] == "configured-session"
    assert data1["session"]["session_kind"] in ("stub", "bearer")
    refreshed_at = data1["session"]["last_refreshed_at"]
    assert refreshed_at == "2026-09-09T01:00:00Z"
    assert data1["session"]["last_refresh_credential_source"] == "bearer"
    assert resp1.json()["meta"]["idempotency"]["replayed"] is False

    # 2. Idempotency replay with same key, clock advanced to 01:00:10Z
    clock[0] = "2026-09-09T01:00:10Z"
    resp2 = client.post("/bff/auth/refresh", json={}, headers=headers)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["meta"]["idempotency"]["replayed"] is True
    assert resp2.json()["data"]["session"]["last_refreshed_at"] == "2026-09-09T01:00:00Z"

    # 3. Idempotency conflict with different payload, clock advanced to 01:00:20Z
    clock[0] = "2026-09-09T01:00:20Z"
    resp_conflict = client.post("/bff/auth/refresh", json={"extra": "different"}, headers=headers)
    assert resp_conflict.status_code == 409
    err_conflict = _extract_error(resp_conflict)
    assert err_conflict.get("code") == "IDEMPOTENCY_CONFLICT"

    # 4. GET /bff/me readback with clock advanced to 01:00:30Z must report 01:00:00Z
    clock[0] = "2026-09-09T01:00:30Z"
    resp_me = client.get("/bff/me", headers={"Authorization": "Bearer review-op:operator"})
    assert resp_me.status_code == 200, resp_me.text
    me_session = resp_me.json()["data"]["session"]
    assert me_session["id"] == "configured-session"
    assert me_session["last_refreshed_at"] == "2026-09-09T01:00:00Z"
    assert me_session["last_refresh_credential_source"] == "bearer"

    # 5. Direct store inspection: canonical key must preserve 01:00:00Z, fallback key must NOT exist
    canonical_key = "operator:review-op:session:configured-session"
    fallback_key = "operator:review-op:session:bff-session-review-op"
    session_state = store.get_session(canonical_key)
    assert session_state != {}, f"Expected state at {canonical_key}"
    assert session_state.get("last_refreshed_at") == "2026-09-09T01:00:00Z"
    assert session_state.get("state") == "active"
    assert store.get_session(fallback_key) == {}, f"Fallback key {fallback_key} should not have been written"


# ==============================================================================
# 6. Authentic Negative JWT, Role, and Tenant Cases
# ==============================================================================

def test_negative_jwt_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test invalid signature, expired JWT, and malformed auth tokens."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", TEST_JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", TEST_JWT_AUDIENCE)

    store = SessionLifecycleStore(str(tmp_path / "negative_store.json"))
    app = _build_standalone_app(
        store,
        auth_mode="strict",
        auth_stub=False,
    )
    client = TestClient(app)

    # 1. Invalid signature
    bad_token = _make_jwt(secret="wrong-secret-key-32-chars-long-xxx")
    resp_bad = client.get("/bff/me", headers={"Authorization": f"Bearer {bad_token}"})
    assert resp_bad.status_code == 401
    err_bad = _extract_error(resp_bad)
    assert err_bad.get("code") == "AUTH_REQUIRED"

    # 2. Expired JWT
    expired_token = _make_jwt(expired=True)
    resp_exp = client.get("/bff/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp_exp.status_code == 401
    err_exp = _extract_error(resp_exp)
    assert err_exp.get("code") in ("AUTH_EXPIRED", "AUTH_REQUIRED")

    # 3. Missing authorization header
    resp_none = client.get("/bff/me")
    assert resp_none.status_code == 401
    err_none = _extract_error(resp_none)
    assert err_none.get("code") == "AUTH_REQUIRED"


def test_negative_role_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test caller with unpermitted role is rejected with 403 FORBIDDEN."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", TEST_JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", TEST_JWT_AUDIENCE)

    store = SessionLifecycleStore(str(tmp_path / "role_store.json"))
    app = _build_standalone_app(
        store,
        auth_mode="strict",
        auth_stub=False,
    )
    client = TestClient(app)

    # Token with an invalid role that has no read permission
    unauthorized_token = _make_jwt(roles=["untrusted_role"])
    resp = client.get("/bff/me", headers={"Authorization": f"Bearer {unauthorized_token}"})
    assert resp.status_code == 403
    err = _extract_error(resp)
    assert err.get("code") == "FORBIDDEN"


def test_tenant_scoping_and_narrowing():
    """Verify bff_me_tenant_payload enforces allowed_tenants and detects unauthorized tenants."""
    identity = OperatorIdentity(
        operator_id="op-tenant",
        roles=["operator"],
        claims={"allowed_tenants": ["tenant-a", "tenant-b"]},
    )

    # Allowed tenant
    payload = bff_me_tenant_payload(identity, requested_tenant="tenant-a")
    assert payload["id"] == "tenant-a"
    assert payload["allowed_ids"] == ["tenant-a", "tenant-b"]

    # Unauthorized tenant raises 403
    with pytest.raises(Exception) as exc_info:
        bff_me_tenant_payload(identity, requested_tenant="tenant-c")
    exc = exc_info.value
    assert exc.status_code == 403
    assert exc.detail["error"]["code"] == "FORBIDDEN"
    assert exc.detail["error"]["details"]["precondition_failed"] == "tenant_scope"


def test_real_signed_token_tenant_rejection_through_router(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify real signed-token tenant scoping and rejection through the router (HTTP endpoints):
    1. Valid signed token with allowed_tenants = ['tenant-alpha', 'tenant-beta'].
    2. GET /bff/me with header X-Tenant-Id: tenant-alpha returns 200 with tenant.id = 'tenant-alpha'.
    3. GET /bff/me with header X-Tenant-Id: tenant-gamma (unauthorized) returns 403 FORBIDDEN,
       precondition_failed = 'tenant_scope'.
    4. GET /bff/me?tenant_id=tenant-gamma (query param) returns 403 FORBIDDEN.
    5. POST /bff/switch-tenant with tenantId = 'tenant-gamma' returns 403 FORBIDDEN.
    6. POST /bff/switch-tenant with tenantId = 'tenant-beta' returns 200 OK.
    7. Subsequent GET /bff/me without explicit tenant header reads switched tenant from session.
    """
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", TEST_JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", TEST_JWT_AUDIENCE)

    store = SessionLifecycleStore(str(tmp_path / "tenant_router_store.json"))
    app = _build_standalone_app(store, auth_mode="strict", auth_stub=False)
    client = TestClient(app)

    token = _make_jwt(
        subject="router-tenant-op",
        roles=["operator"],
        extra={"allowed_tenants": ["tenant-alpha", "tenant-beta"]},
    )
    auth_headers = {"Authorization": f"Bearer {token}"}

    # 1. Allowed tenant via header
    resp_ok = client.get("/bff/me", headers={**auth_headers, "X-Tenant-Id": "tenant-alpha"})
    assert resp_ok.status_code == 200, resp_ok.text
    data = resp_ok.json()["data"]
    assert data["tenant"]["id"] == "tenant-alpha"
    assert data["tenant"]["allowed_ids"] == ["tenant-alpha", "tenant-beta"]
    assert data["tenant"]["source"] == "request"

    # 2. Unauthorized tenant via header
    resp_bad_header = client.get("/bff/me", headers={**auth_headers, "X-Tenant-Id": "tenant-gamma"})
    assert resp_bad_header.status_code == 403
    err_h = _extract_error(resp_bad_header)
    assert err_h.get("code") == "FORBIDDEN"
    assert err_h.get("details", {}).get("precondition_failed") == "tenant_scope"
    assert err_h.get("details", {}).get("tenantId") == "tenant-gamma"

    # 3. Unauthorized tenant via query param
    resp_bad_query = client.get("/bff/me?tenant_id=tenant-gamma", headers=auth_headers)
    assert resp_bad_query.status_code == 403
    err_q = _extract_error(resp_bad_query)
    assert err_q.get("code") == "FORBIDDEN"
    assert err_q.get("details", {}).get("precondition_failed") == "tenant_scope"

    # 4. POST /bff/switch-tenant with unauthorized tenant returns 403
    resp_switch_bad = client.post("/bff/switch-tenant", json={"tenantId": "tenant-gamma"}, headers=auth_headers)
    assert resp_switch_bad.status_code == 403
    err_s = _extract_error(resp_switch_bad)
    assert err_s.get("code") == "FORBIDDEN"
    assert err_s.get("details", {}).get("precondition_failed") == "tenant_scope"

    # 5. POST /bff/switch-tenant with allowed tenant returns 200
    resp_switch_ok = client.post("/bff/switch-tenant", json={"tenantId": "tenant-beta"}, headers=auth_headers)
    assert resp_switch_ok.status_code == 200, resp_switch_ok.text
    switch_data = resp_switch_ok.json()["data"]
    assert switch_data["tenant"]["id"] == "tenant-beta"
    assert switch_data["tenant"]["source"] == "session"

    # 6. Subsequent GET /bff/me without explicit tenant header reads switched tenant from session
    resp_me_switched = client.get("/bff/me", headers=auth_headers)
    assert resp_me_switched.status_code == 200, resp_me_switched.text
    me_data = resp_me_switched.json()["data"]
    assert me_data["tenant"]["id"] == "tenant-beta"
    assert me_data["tenant"]["source"] == "session"


def test_dev_login_forbidden_in_production(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify POST /bff/auth/dev-login fails with 403 in production environments."""
    monkeypatch.setenv("PANTHEON_ENV", "production")
    store = SessionLifecycleStore(str(tmp_path / "prod_store.json"))
    app = _build_standalone_app(store, auth_mode="permissive")
    client = TestClient(app)

    resp = client.post("/bff/auth/dev-login", json={"profile": "operator"})
    assert resp.status_code == 403
    err = _extract_error(resp)
    assert err.get("code") == "PRECONDITION_FAILED"


# ==============================================================================
# 7. Composition Root Smoke Test
# ==============================================================================

def test_composition_root_smoke():
    """Smoke test running in a subprocess proving default composition in main.py
    wires auth_deps, session_lifecycle_store, and guards correctly.
    """
    code = (
        "import tempfile, os\n"
        "with tempfile.TemporaryDirectory(prefix='bff-smoke-') as tmpdir:\n"
        "    os.environ['BFF_DATA_DIR'] = tmpdir\n"
        "    os.environ['PANTHEON_BFF_AUTH_STUB'] = 'true'\n"
        "    os.environ['PANTHEON_BFF_AUTH_MODE'] = 'permissive'\n"
        "    from services.control_plane.bff import main as bff_main\n"
        "    from fastapi.testclient import TestClient\n"
        "    client = TestClient(bff_main.app)\n"
        "    headers = {'Authorization': 'Bearer smoke-op:operator'}\n"
        "    r1 = client.get('/bff/me', headers=headers)\n"
        "    assert r1.status_code == 200, f'Expected 200, got {r1.status_code}'\n"
        "    r2 = client.post('/bff/logout', headers=headers)\n"
        "    assert r2.status_code == 200, f'Expected 200, got {r2.status_code}'\n"
        "    r3 = client.get('/bff/me', headers=headers)\n"
        "    assert r3.status_code == 401, f'Expected 401, got {r3.status_code}'\n"
        "    # Check guard canonical binding\n"
        "    assert getattr(bff_main.auth_deps.raise_if_session_logged_out, '_canonical_guard', False) is True\n"
        "    print('COMPOSITION_SMOKE_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "COMPOSITION_SMOKE_OK" in result.stdout
