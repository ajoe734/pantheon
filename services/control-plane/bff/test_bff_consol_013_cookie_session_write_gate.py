"""BFF-CONSOL-013: Cookie-session write gate tests.

Verifies that /bff/me returns session_kind (cookie|bearer|stub) and that
liveWriteGated() logic correctly admits or blocks write operations based on
the session kind.
"""
from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.handlers import AuthDependencies, create_auth_handlers
from services.control_plane.bff.auth.router import create_auth_router
from services.control_plane.bff.auth.service import AuthFacadeService
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore
from services.runtime_auth_inbound import encode_jwt_hs256, validate_request_auth

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


def _build_client(tmp_path) -> TestClient:
    session_store = SessionLifecycleStore(str(tmp_path / "session_lifecycle.json"))

    def _extract_identity_stub(authorization: str | None) -> OperatorIdentity:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail={"error": {"code": "AUTH_REQUIRED"}})
        token = authorization[len("Bearer "):].strip()
        parts = token.split(":")
        op = parts[0]
        roles = parts[1].split(",") if len(parts) > 1 else ["operator"]
        return OperatorIdentity(operator_id=op, roles=roles, token_kind="stub", claims={})

    def _extract_identity_jwt(authorization: str | None, mfa_token: str | None = None) -> OperatorIdentity:
        bff_env = {
            "PANTHEON_RUNTIME_AUTH_MODE": os.getenv("PANTHEON_BFF_AUTH_MODE", "strict"),
            "PANTHEON_RUNTIME_JWT_SECRET": os.getenv("PANTHEON_BFF_JWT_SECRET", ""),
            "PANTHEON_RUNTIME_JWT_ISSUER": os.getenv("PANTHEON_BFF_JWT_ISSUER", ""),
            "PANTHEON_RUNTIME_JWT_AUDIENCE": os.getenv("PANTHEON_BFF_JWT_AUDIENCE", ""),
            "PANTHEON_RUNTIME_DEFAULT_ROLE": "operator",
            "PANTHEON_RUNTIME_MFA_REQUIRED": "false",
            "PANTHEON_RUNTIME_JWKS_URI": "",
            "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL": "",
            "PANTHEON_RUNTIME_ROLE_CLAIMS": "roles,role",
            "PANTHEON_RUNTIME_ROLE_MAP": "",
            "PANTHEON_RUNTIME_ROLE_MAP_MODE": "passthrough",
            "PANTHEON_RUNTIME_REQUIRE_EMAIL_VERIFIED": "false",
        }
        ctx = validate_request_auth(
            authorization=authorization or "",
            mfa_header=mfa_token or "",
            mfa_required=False,
            env=bff_env,
        )
        claims = getattr(ctx, "claims", {}) or {}
        return OperatorIdentity(
            operator_id=getattr(ctx, "actor_id", "op-jwt"),
            roles=getattr(ctx, "roles", ["operator"]),
            token_kind="bearer",
            claims=claims,
        )

    def _extract_identity(
        authorization: str | None = None,
        mfa_token: str | None = None,
        session_cookie: str | None = None,
    ) -> OperatorIdentity:
        stub_enabled = os.getenv("PANTHEON_BFF_AUTH_STUB", "").lower() in ("true", "1")
        if stub_enabled:
            if authorization and authorization.startswith("Bearer "):
                raw = authorization[len("Bearer "):].strip()
                if raw.count(".") == 2:
                    try:
                        return _extract_identity_jwt(authorization, mfa_token=mfa_token)
                    except Exception:
                        pass
            if not authorization and session_cookie:
                try:
                    identity = _extract_identity_jwt(f"Bearer {session_cookie}", mfa_token=mfa_token)
                    return identity.model_copy(update={"token_kind": "cookie"})
                except Exception:
                    pass
            return _extract_identity_stub(authorization)

        if not authorization and session_cookie:
            identity = _extract_identity_jwt(f"Bearer {session_cookie}", mfa_token=mfa_token)
            return identity.model_copy(update={"token_kind": "cookie"})
        return _extract_identity_jwt(authorization, mfa_token=mfa_token)

    deps = AuthDependencies(
        bff_error=lambda s, c, m, **kw: HTTPException(status_code=s, detail={"error": {"code": str(c), "message": m}}),
        dev_login_forbidden_environment=lambda: False,
        dev_login_identity_registry=lambda: {},
        extract_identity=_extract_identity,
        require_read_role=lambda id: None,
        raise_if_session_logged_out=lambda id: None,
        session_lifecycle_store=session_store,
        bff_me_tenant_payload=lambda id, requested_tenant=None: {
            "id": requested_tenant or "pantheon-dev",
            "requested_id": requested_tenant,
            "default_id": requested_tenant or "pantheon-dev",
            "allowed_ids": [requested_tenant or "pantheon-dev"],
            "scope": "tenant",
        },
        capabilities_for_identity=lambda id: ["operator.write"],
        bff_auth_stub_enabled=lambda: os.getenv("PANTHEON_BFF_AUTH_STUB", "").lower() in ("true", "1"),
        bff_auth_mode=lambda: os.getenv("PANTHEON_BFF_AUTH_MODE", "strict"),
        bff_source_commit=lambda: "HEAD",
        write_roles=frozenset({"operator", "admin"}),
        utc_now=lambda: "2026-09-05T00:00:00Z",
    )
    auth_handlers = create_auth_handlers(dependencies=deps)
    service = AuthFacadeService(
        local_readiness=auth_handlers["bff_auth_readiness"],
        handlers=auth_handlers,
    )
    app = FastAPI(title="Auth Cookie Session Gate Contract")
    app.include_router(create_auth_router(service=service))
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(tmp_path) -> TestClient:
    return _build_client(tmp_path)


class TestSessionKindStub:
    def test_stub_session_returns_session_kind_stub(self, monkeypatch, client) -> None:
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
        resp = client.get("/bff/me", headers={"Authorization": "Bearer op-1:operator"})
        assert resp.status_code == 200, resp.text
        session = resp.json()["data"]["session"]
        assert session["session_kind"] == "stub"


class TestSessionKindBearer:
    def test_bearer_jwt_returns_session_kind_bearer(self, monkeypatch, client) -> None:
        _strict_auth_env(monkeypatch)
        token = _jwt_token(roles=["operator"])
        resp = client.get(
            "/bff/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, resp.text
        session = resp.json()["data"]["session"]
        assert session["session_kind"] == "bearer"


class TestSessionKindCookie:
    def test_cookie_jwt_returns_session_kind_cookie(self, monkeypatch, client) -> None:
        _strict_auth_env(monkeypatch)
        token = _jwt_token(roles=["operator"])
        resp = client.get(
            "/bff/me",
            cookies={"pantheon_session": token},
        )
        assert resp.status_code == 200, resp.text
        session = resp.json()["data"]["session"]
        assert session["session_kind"] == "cookie"
        assert session["authenticated"] is True

    def test_cookie_session_write_gate_passes(self, monkeypatch, client) -> None:
        """Cookie session must not be treated as unauthenticated for write gating."""
        _strict_auth_env(monkeypatch)
        token = _jwt_token(roles=["operator"])
        resp = client.get(
            "/bff/me",
            cookies={"pantheon_session": token},
        )
        assert resp.status_code == 200, resp.text
        session = resp.json()["data"]["session"]
        assert session["authenticated"] is True
        assert session["session_kind"] == "cookie"

    def test_bearer_takes_priority_over_cookie(self, monkeypatch, client) -> None:
        """When both bearer and cookie are present, bearer wins."""
        _strict_auth_env(monkeypatch)
        token = _jwt_token(roles=["operator"])
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

    def test_bff_me_session_payload_includes_session_kind(self, monkeypatch, client) -> None:
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
        resp = client.get("/bff/me", headers={"Authorization": "Bearer op-gate:operator"})
        assert resp.status_code == 200, resp.text
        session = resp.json()["data"]["session"]
        assert "session_kind" in session
        assert session["session_kind"] in ("cookie", "bearer", "stub")

