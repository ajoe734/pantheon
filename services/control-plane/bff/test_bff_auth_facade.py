"""Tests for the BFF auth facade (SVC-BFF-AUTH-FACADE-HARDENING).

Covers:
 - valid JWT -> correct OperatorIdentity built
 - invalid JWT (bad signature, expired) -> 401
 - role denial -> 403
 - MFA denial -> 403
 - dev/test stub compatibility (PANTHEON_BFF_AUTH_STUB=true)
 - downstream header propagation sanity (auth + MFA headers reach identity)
"""
from __future__ import annotations

import json
import os
import sys
import time
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import main as bff_main
from main import _extract_identity, _extract_identity_jwt, _extract_identity_stub
from models import ErrorCode, OperatorIdentity
from services.runtime_auth_inbound import encode_jwt_hs256

_SECRET = "test-bff-secret-1234"
_ISSUER = "pantheon-bff-test"
_AUDIENCE = "bff-operators"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jwt(
    *,
    sub: str = "op-alice",
    roles: list[str] | None = None,
    secret: str = _SECRET,
    issuer: str = _ISSUER,
    audience: str = _AUDIENCE,
    exp_offset: int = 3600,
    nbf_offset: int = 0,
    extra: dict | None = None,
) -> str:
    payload: dict = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
    }
    if nbf_offset:
        payload["nbf"] = int(time.time()) + nbf_offset
    if roles is not None:
        payload["roles"] = roles
    if extra:
        payload.update(extra)
    return encode_jwt_hs256(payload, secret=secret)


_BFF_ENV = {
    "PANTHEON_BFF_AUTH_STUB": "",
    "PANTHEON_BFF_AUTH_MODE": "strict",
    "PANTHEON_BFF_JWT_SECRET": _SECRET,
    "PANTHEON_BFF_JWT_ISSUER": _ISSUER,
    "PANTHEON_BFF_JWT_AUDIENCE": _AUDIENCE,
    "PANTHEON_BFF_MFA_REQUIRED": "false",
}


# ---------------------------------------------------------------------------
# Unit tests: _extract_identity_jwt
# ---------------------------------------------------------------------------

class TestExtractIdentityJwt:
    def _call(self, authorization: str, mfa_token: str | None = None, env_overrides: dict | None = None):
        env = {**_BFF_ENV, **(env_overrides or {})}
        with patch.dict(os.environ, env, clear=False):
            return _extract_identity_jwt(authorization, mfa_token=mfa_token)

    def test_valid_jwt_operator_role(self):
        token = _make_jwt(sub="op-alice", roles=["operator"])
        identity = self._call(f"Bearer {token}")
        assert identity.operator_id == "op-alice"
        assert "operator" in identity.roles
        assert identity.mfa_verified is False

    def test_valid_jwt_admin_role(self):
        token = _make_jwt(sub="op-bob", roles=["admin", "operator"])
        identity = self._call(f"Bearer {token}")
        assert identity.operator_id == "op-bob"
        assert "admin" in identity.roles
        assert "operator" in identity.roles

    def test_valid_jwt_reviewer_role(self):
        token = _make_jwt(sub="op-carol", roles=["reviewer"])
        identity = self._call(f"Bearer {token}")
        assert "reviewer" in identity.roles

    def test_missing_authorization_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(None)
        assert exc_info.value.status_code == 401

    def test_non_bearer_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call("Basic dXNlcjpwYXNz")
        assert exc_info.value.status_code == 401

    def test_invalid_signature_raises_401(self):
        from fastapi import HTTPException
        token = _make_jwt(secret="wrong-secret")
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}")
        assert exc_info.value.status_code == 401

    def test_expired_token_raises_401(self):
        from fastapi import HTTPException
        token = _make_jwt(exp_offset=-10)
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}")
        assert exc_info.value.status_code == 401

    def test_issuer_mismatch_raises_401(self):
        from fastapi import HTTPException
        token = _make_jwt(issuer="wrong-issuer")
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}")
        assert exc_info.value.status_code == 401

    def test_audience_mismatch_raises_401(self):
        from fastapi import HTTPException
        token = _make_jwt(audience="wrong-audience")
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}")
        assert exc_info.value.status_code == 401

    def test_blank_subject_raises_401(self):
        from fastapi import HTTPException
        token = _make_jwt(sub="", roles=["operator"])
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}")
        assert exc_info.value.status_code == 401

    def test_colon_stub_token_rejected_in_strict_mode(self):
        """In JWT mode, colon-format stub tokens must be rejected."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call("Bearer op-alice:operator:mfa")
        assert exc_info.value.status_code == 401

    def test_mfa_verified_when_header_present(self):
        token = _make_jwt(sub="op-alice", roles=["admin"])
        identity = self._call(f"Bearer {token}", mfa_token="123456")
        assert identity.mfa_verified is True

    def test_mfa_not_verified_without_header(self):
        token = _make_jwt(sub="op-alice", roles=["admin"])
        identity = self._call(f"Bearer {token}")
        assert identity.mfa_verified is False

    def test_invalid_mfa_format_raises_400(self):
        from fastapi import HTTPException
        token = _make_jwt(sub="op-alice", roles=["admin"])
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}", mfa_token="abc123")
        assert exc_info.value.status_code == 400

    def test_mfa_required_env_enforced(self):
        """When PANTHEON_BFF_MFA_REQUIRED=true, missing MFA header raises 401."""
        from fastapi import HTTPException
        token = _make_jwt(sub="op-alice", roles=["admin"])
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}", env_overrides={"PANTHEON_BFF_MFA_REQUIRED": "true"})
        assert exc_info.value.status_code == 401

    def test_no_secret_configured_raises_401(self):
        """JWT token without a configured secret must be rejected."""
        from fastapi import HTTPException
        token = _make_jwt()
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}", env_overrides={"PANTHEON_BFF_JWT_SECRET": ""})
        assert exc_info.value.status_code == 401
        assert "PANTHEON_RUNTIME_JWT_SECRET" not in json.dumps(exc_info.value.detail)

    def test_permissive_mode_accepts_structured_token(self):
        """permissive mode allows structured actor:role tokens."""
        identity = self._call(
            "Bearer op-internal:operator",
            env_overrides={"PANTHEON_BFF_AUTH_MODE": "permissive", "PANTHEON_BFF_JWT_SECRET": ""},
        )
        assert identity.operator_id == "op-internal"
        assert "operator" in identity.roles


# ---------------------------------------------------------------------------
# Unit tests: _extract_identity_stub (legacy dev mode)
# ---------------------------------------------------------------------------

class TestExtractIdentityStub:
    def _call(self, authorization: str) -> OperatorIdentity:
        return _extract_identity_stub(authorization)

    def test_colon_format_operator(self):
        identity = self._call("Bearer op-operator:operator")
        assert identity.operator_id == "op-operator"
        assert "operator" in identity.roles
        assert identity.mfa_verified is False

    def test_colon_format_admin_mfa(self):
        identity = self._call("Bearer op-admin:admin:mfa")
        assert identity.operator_id == "op-admin"
        assert "admin" in identity.roles
        assert identity.mfa_verified is True

    def test_colon_format_multiple_roles(self):
        identity = self._call("Bearer op-multi:operator,reviewer")
        assert "operator" in identity.roles
        assert "reviewer" in identity.roles

    def test_missing_bearer_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call("op-bare-token")
        assert exc_info.value.status_code == 401

    def test_no_colon_infers_operator_role(self):
        identity = self._call("Bearer sometoken")
        assert "operator" in identity.roles


# ---------------------------------------------------------------------------
# Integration tests: _extract_identity dispatch (stub vs JWT)
# ---------------------------------------------------------------------------

class TestExtractIdentityDispatch:
    def test_stub_env_routes_to_stub(self):
        with patch.dict(os.environ, {"PANTHEON_BFF_AUTH_STUB": "true"}, clear=False):
            identity = _extract_identity("Bearer op-admin:admin:mfa")
        assert identity.operator_id == "op-admin"
        assert identity.mfa_verified is True

    def test_no_stub_env_routes_to_jwt(self):
        from fastapi import HTTPException
        with patch.dict(os.environ, {"PANTHEON_BFF_AUTH_STUB": ""}, clear=False):
            with pytest.raises(HTTPException) as exc_info:
                _extract_identity("Bearer op-admin:admin:mfa")
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Integration tests: HTTP layer (settings routes)
# ---------------------------------------------------------------------------

class TestSettingsAuthIntegration:
    """Verify that the BFF settings routes enforce auth in stub mode."""

    @pytest.fixture
    def client_stub(self):
        env = {"PANTHEON_BFF_AUTH_STUB": "true"}
        with patch.dict(os.environ, env, clear=False):
            with tempfile.TemporaryDirectory() as td:
                original = bff_main.settings_store
                from settings_store import SettingsStore
                bff_main.settings_store = SettingsStore(os.path.join(td, "settings.json"))
                c = TestClient(bff_main.app)
                yield c
                bff_main.settings_store = original

    @pytest.fixture
    def client_jwt(self):
        env = {
            "PANTHEON_BFF_AUTH_STUB": "",
            "PANTHEON_BFF_AUTH_MODE": "strict",
            "PANTHEON_BFF_JWT_SECRET": _SECRET,
            "PANTHEON_BFF_JWT_ISSUER": _ISSUER,
            "PANTHEON_BFF_JWT_AUDIENCE": _AUDIENCE,
            "PANTHEON_BFF_MFA_REQUIRED": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            with tempfile.TemporaryDirectory() as td:
                original = bff_main.settings_store
                from settings_store import SettingsStore
                bff_main.settings_store = SettingsStore(os.path.join(td, "settings.json"))
                c = TestClient(bff_main.app)
                yield c
                bff_main.settings_store = original

    # ---- stub mode ----

    def test_stub_operator_can_read_settings(self, client_stub):
        resp = client_stub.get(
            "/api/v1/settings",
            headers={"Authorization": "Bearer op-operator:operator"},
        )
        assert resp.status_code == 200

    def test_stub_admin_mfa_can_update_settings(self, client_stub):
        resp = client_stub.post(
            "/api/v1/settings",
            headers={"Authorization": "Bearer op-admin:admin:mfa"},
            json={"settings": {"general": {"theme": "dark"}}},
        )
        assert resp.status_code == 200

    def test_stub_operator_cannot_update_settings(self, client_stub):
        resp = client_stub.post(
            "/api/v1/settings",
            headers={"Authorization": "Bearer op-operator:operator"},
            json={"settings": {"general": {"theme": "dark"}}},
        )
        assert resp.status_code == 403

    def test_stub_missing_token_returns_401(self, client_stub):
        resp = client_stub.get("/api/v1/settings")
        assert resp.status_code == 401

    # ---- JWT mode ----

    def test_jwt_operator_can_read_settings(self, client_jwt):
        token = _make_jwt(sub="op-alice", roles=["operator"])
        resp = client_jwt.get(
            "/api/v1/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_jwt_invalid_signature_returns_401(self, client_jwt):
        token = _make_jwt(sub="op-alice", roles=["operator"], secret="bad-secret")
        resp = client_jwt.get(
            "/api/v1/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_jwt_expired_returns_401(self, client_jwt):
        token = _make_jwt(sub="op-alice", roles=["operator"], exp_offset=-10)
        resp = client_jwt.get(
            "/api/v1/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_jwt_operator_cannot_update_settings(self, client_jwt):
        """operator role alone is not admin - role check must fail."""
        token = _make_jwt(sub="op-alice", roles=["operator"])
        resp = client_jwt.post(
            "/api/v1/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={"settings": {"general": {"theme": "dark"}}},
        )
        assert resp.status_code == 403

    def test_jwt_admin_without_mfa_cannot_update_settings(self, client_jwt):
        """admin role without X-MFA-Token: _require_admin_mfa still fires."""
        token = _make_jwt(sub="op-bob", roles=["admin"])
        resp = client_jwt.post(
            "/api/v1/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={"settings": {"general": {"theme": "dark"}}},
        )
        assert resp.status_code == 403

    def test_jwt_admin_with_mfa_can_update_settings(self, client_jwt):
        token = _make_jwt(sub="op-bob", roles=["admin"])
        resp = client_jwt.post(
            "/api/v1/settings",
            headers={
                "Authorization": f"Bearer {token}",
                "X-MFA-Token": "654321",
            },
            json={"settings": {"general": {"theme": "dark"}}},
        )
        assert resp.status_code == 200

    def test_jwt_stub_token_rejected_in_jwt_mode(self, client_jwt):
        """Colon-format stub tokens must not work when PANTHEON_BFF_AUTH_STUB is not set."""
        resp = client_jwt.get(
            "/api/v1/settings",
            headers={"Authorization": "Bearer op-operator:operator"},
        )
        assert resp.status_code == 401
