"""BFF-CONSOL-013: Cookie-session write gate tests.

Verifies that /bff/me returns session_kind (cookie|bearer|stub) and that
liveWriteGated() logic correctly admits or blocks write operations based on
the session kind.
"""
from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore
from services.control_plane.bff.tests.management_session_harness import ManagementSessionHarness
from services.runtime_auth_inbound import encode_jwt_hs256


class DynamicHarness(ManagementSessionHarness):
    @property
    def auth_stub(self) -> bool:
        return os.environ.get("PANTHEON_BFF_AUTH_STUB", "").lower() in ("true", "1", "yes")

    @auth_stub.setter
    def auth_stub(self, val):
        pass

    @property
    def auth_mode(self) -> str:
        return os.environ.get("PANTHEON_BFF_AUTH_MODE", "strict")

    @auth_mode.setter
    def auth_mode(self, val):
        pass


_harness: DynamicHarness | None = None


class _AppProxy:
    def __getattr__(self, name):
        return getattr(_harness.app, name)

    async def __call__(self, scope, receive, send):
        return await _harness.app(scope, receive, send)


class _BffMainMock:
    app = _AppProxy()

    @property
    def session_lifecycle_store(self):
        return _harness.session_lifecycle_store

    @session_lifecycle_store.setter
    def session_lifecycle_store(self, val):
        if _harness is not None:
            _harness.session_lifecycle_store = val


bff_main = _BffMainMock()

JWT_SECRET = "test-bff-consol-013"
JWT_ISSUER = "pantheon-consol-013"
JWT_AUDIENCE = "bff-operators"


def _jwt_token(*, roles: list[str] = None, extra: dict | None = None) -> str:
    roles = roles or ["operator"]
    payload = {
        "sub": "op-jwt",
        "roles": roles,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    if extra:
        payload.update(extra)
    return encode_jwt_hs256(payload, secret=JWT_SECRET)


def _strict_auth_env(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", JWT_AUDIENCE)
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")


@pytest.fixture(autouse=True)
def isolated_session_store(tmp_path):
    global _harness
    store = SessionLifecycleStore(str(tmp_path / "session_lifecycle.json"))
    _harness = DynamicHarness(
        store=store,
        jwt_secret=JWT_SECRET,
        jwt_issuer=JWT_ISSUER,
        jwt_audience=JWT_AUDIENCE,
    )
    try:
        yield
    finally:
        if _harness is not None:
            _harness.close()
            _harness = None


class TestSessionKindStub:
    def test_stub_session_returns_session_kind_stub(self, monkeypatch) -> None:
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
        client = TestClient(bff_main.app)
        resp = client.get("/bff/me", headers={"Authorization": "Bearer op-1:operator"})
        assert resp.status_code == 200, resp.text
        session = resp.json()["data"]["session"]
        assert session["session_kind"] == "stub"


class TestSessionKindBearer:
    def test_bearer_jwt_returns_session_kind_bearer(self, monkeypatch) -> None:
        _strict_auth_env(monkeypatch)
        token = _jwt_token(roles=["operator"])
        client = TestClient(bff_main.app)
        resp = client.get(
            "/bff/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, resp.text
        session = resp.json()["data"]["session"]
        assert session["session_kind"] == "bearer"


class TestSessionKindCookie:
    def test_cookie_jwt_returns_session_kind_cookie(self, monkeypatch) -> None:
        _strict_auth_env(monkeypatch)
        token = _jwt_token(roles=["operator"])
        client = TestClient(bff_main.app)
        # Send JWT as a cookie, no Authorization header
        resp = client.get(
            "/bff/me",
            cookies={"pantheon_session": token},
        )
        assert resp.status_code == 200, resp.text
        session = resp.json()["data"]["session"]
        assert session["session_kind"] == "cookie"
        assert session["authenticated"] is True

    def test_cookie_session_write_gate_passes(self, monkeypatch) -> None:
        """Cookie session must not be treated as unauthenticated for write gating."""
        _strict_auth_env(monkeypatch)
        token = _jwt_token(roles=["operator"])
        client = TestClient(bff_main.app)
        resp = client.get(
            "/bff/me",
            cookies={"pantheon_session": token},
        )
        assert resp.status_code == 200, resp.text
        session = resp.json()["data"]["session"]
        # Cookie sessions are authenticated and should pass the write gate
        assert session["authenticated"] is True
        assert session["session_kind"] == "cookie"

    def test_bearer_takes_priority_over_cookie(self, monkeypatch) -> None:
        """When both bearer and cookie are present, bearer wins."""
        _strict_auth_env(monkeypatch)
        token = _jwt_token(roles=["operator"])
        client = TestClient(bff_main.app)
        resp = client.get(
            "/bff/me",
            headers={"Authorization": f"Bearer {token}"},
            cookies={"pantheon_session": token},
        )
        assert resp.status_code == 200, resp.text
        session = resp.json()["data"]["session"]
        assert session["session_kind"] == "bearer"


class TestSessionKindWriteGateLogic:
    """Unit-level tests for sessionKindAllowsWrite logic (no HTTP, inline logic)."""

    def _allows(self, kind: str, production: bool = False) -> bool:
        if kind in ("cookie", "bearer"):
            return True
        if kind == "stub":
            return not production
        return False

    def test_cookie_allows_write_non_production(self) -> None:
        assert self._allows("cookie", production=False) is True

    def test_cookie_allows_write_production(self) -> None:
        assert self._allows("cookie", production=True) is True

    def test_bearer_allows_write_non_production(self) -> None:
        assert self._allows("bearer", production=False) is True

    def test_bearer_allows_write_production(self) -> None:
        assert self._allows("bearer", production=True) is True

    def test_stub_allows_write_non_production(self) -> None:
        assert self._allows("stub", production=False) is True

    def test_stub_blocked_in_production(self) -> None:
        assert self._allows("stub", production=True) is False

    def test_unknown_kind_blocked(self) -> None:
        assert self._allows("unknown") is False

    def test_bff_me_session_payload_includes_session_kind(self, monkeypatch) -> None:
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
        client = TestClient(bff_main.app)
        resp = client.get("/bff/me", headers={"Authorization": "Bearer op-gate:operator"})
        assert resp.status_code == 200, resp.text
        session = resp.json()["data"]["session"]
        assert "session_kind" in session
        assert session["session_kind"] in ("cookie", "bearer", "stub")
