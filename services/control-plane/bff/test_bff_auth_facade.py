"""Tests for the BFF auth facade (SVC-BFF-AUTH-FACADE-HARDENING).

Covers:
 - valid JWT -> correct OperatorIdentity built
 - invalid JWT (bad signature, expired) -> 401
 - role denial -> 403
 - MFA denial -> 403
 - dev/test stub compatibility (PANTHEON_BFF_AUTH_STUB=true)
 - downstream header propagation sanity (auth + MFA headers reach identity)
 - OIDC/JWKS validation (kid matching, issuer/audience/expiry, cache, error sanitization)
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from services.control_plane.bff import main as bff_main
from services.control_plane.bff.main import _extract_identity, _extract_identity_jwt, _extract_identity_stub
from services.control_plane.bff.models import ErrorCode, OperatorIdentity
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


def _response_error(resp):
    body = resp.json()
    if isinstance(body.get("error"), dict):
        return body["error"]
    detail = body.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        return detail["error"]
    raise AssertionError(f"response did not contain BFF error envelope: {body}")


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

    def test_permissive_mode_rejects_plain_bearer_token(self):
        """permissive mode must not silently grant operator to arbitrary bearer strings."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(
                "Bearer definitely-invalid-no-role-token",
                env_overrides={"PANTHEON_BFF_AUTH_MODE": "permissive", "PANTHEON_BFF_JWT_SECRET": ""},
            )
        assert exc_info.value.status_code == 403

    def test_permissive_structured_token_preserves_capability_suffix(self):
        identity = self._call(
            "Bearer op-admin:admin,operator:mfa:assistant.kernel.debug,audit.read",
            env_overrides={"PANTHEON_BFF_AUTH_MODE": "permissive", "PANTHEON_BFF_JWT_SECRET": ""},
        )

        assert identity.operator_id == "op-admin"
        assert identity.mfa_verified is True
        assert identity.claims["capabilities"] == ["assistant.kernel.debug", "audit.read"]

    def test_permissive_structured_token_merges_dev_stub_capabilities(self):
        identity = self._call(
            "Bearer op-admin:admin,operator:mfa:assistant.kernel.debug",
            env_overrides={
                "PANTHEON_BFF_AUTH_MODE": "permissive",
                "PANTHEON_BFF_JWT_SECRET": "",
                "PANTHEON_BFF_STUB_CAPABILITIES": "assistant.kernel.debug,assistant.kernel.repair",
            },
        )

        assert identity.claims["capabilities"] == [
            "assistant.kernel.debug",
            "assistant.kernel.repair",
        ]

    def test_permissive_viewer_does_not_inherit_or_assert_stub_capabilities(self):
        identity = self._call(
            "Bearer pantheon-dev-browser:viewer:mfa:assistant.kernel.debug",
            env_overrides={
                "PANTHEON_BFF_AUTH_MODE": "permissive",
                "PANTHEON_BFF_JWT_SECRET": "",
                "PANTHEON_BFF_STUB_CAPABILITIES": "assistant.kernel.repair",
            },
        )

        assert identity.roles == ["viewer"]
        assert identity.claims.get("capabilities") in (None, [])


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

    def test_colon_format_accepts_explicit_stub_capabilities(self):
        identity = self._call("Bearer op-admin:admin,operator:mfa:assistant.kernel.debug,audit.read")
        assert identity.operator_id == "op-admin"
        assert identity.mfa_verified is True
        assert identity.claims["capabilities"] == ["assistant.kernel.debug", "audit.read"]

    def test_stub_capabilities_can_be_supplied_by_dev_env(self):
        with patch.dict(
            os.environ,
            {"PANTHEON_BFF_STUB_CAPABILITIES": "assistant.kernel.debug, assistant.kernel.repair"},
            clear=False,
        ):
            identity = self._call("Bearer op-admin:admin:mfa:assistant.kernel.debug")

        assert identity.claims["capabilities"] == [
            "assistant.kernel.debug",
            "assistant.kernel.repair",
        ]

    def test_viewer_stub_does_not_inherit_or_assert_capabilities(self):
        with patch.dict(
            os.environ,
            {"PANTHEON_BFF_STUB_CAPABILITIES": "assistant.kernel.repair"},
            clear=False,
        ):
            identity = self._call(
                "Bearer pantheon-dev-browser:viewer:mfa:assistant.kernel.debug"
            )

        assert identity.roles == ["viewer"]
        assert identity.claims["capabilities"] == []

    def test_colon_format_multiple_roles(self):
        identity = self._call("Bearer op-multi:operator,reviewer")
        assert "operator" in identity.roles
        assert "reviewer" in identity.roles

    def test_missing_bearer_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call("op-bare-token")
        assert exc_info.value.status_code == 401

    def test_no_colon_requires_explicit_legacy_allowlist(self):
        from fastapi import HTTPException

        with patch.dict(os.environ, {"PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS": ""}, clear=False):
            with pytest.raises(HTTPException) as exc_info:
                self._call("Bearer sometoken")
        assert exc_info.value.status_code == 403

    def test_allowlisted_no_colon_infers_operator_role(self):
        with patch.dict(os.environ, {"PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS": "sometoken"}, clear=False):
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

    def test_stub_env_accepts_valid_jwt(self):
        token = _make_jwt(sub="op-dev-login", roles=["operator", "admin"])
        with patch.dict(
            os.environ,
            {
                **_BFF_ENV,
                "PANTHEON_BFF_AUTH_STUB": "true",
                "PANTHEON_BFF_AUTH_MODE": "permissive",
            },
            clear=False,
        ):
            identity = _extract_identity(f"Bearer {token}")
        assert identity.operator_id == "op-dev-login"
        assert "operator" in identity.roles
        assert "admin" in identity.roles

    def test_stub_env_accepts_cookie_jwt(self):
        token = _make_jwt(sub="op-cookie-user", roles=["viewer"])
        with patch.dict(
            os.environ,
            {
                **_BFF_ENV,
                "PANTHEON_BFF_AUTH_STUB": "true",
                "PANTHEON_BFF_AUTH_MODE": "permissive",
            },
            clear=False,
        ):
            identity = _extract_identity(None, session_cookie=token)
        assert identity.operator_id == "op-cookie-user"
        assert identity.roles == ["viewer"]
        assert identity.token_kind == "cookie"

    def test_no_stub_env_routes_to_jwt(self):
        from fastapi import HTTPException
        with patch.dict(
            os.environ,
            {"PANTHEON_BFF_AUTH_STUB": "", "PANTHEON_BFF_AUTH_MODE": "strict"},
            clear=False,
        ):
            with pytest.raises(HTTPException) as exc_info:
                _extract_identity("Bearer op-admin:admin:mfa")
        assert exc_info.value.status_code == 401

    @pytest.mark.parametrize("bad_mode", ["strcit", "", "disabled", "strict-ish", "PERMISIVE"])
    def test_malformed_auth_mode_with_stub_true_still_routes_to_jwt(self, bad_mode):
        # A typo'd/unrecognized PANTHEON_BFF_AUTH_MODE combined with
        # PANTHEON_BFF_AUTH_STUB=true must not silently enable the dev stub —
        # unrecognized modes fail closed to strict.
        from fastapi import HTTPException
        with patch.dict(
            os.environ,
            {"PANTHEON_BFF_AUTH_STUB": "true", "PANTHEON_BFF_AUTH_MODE": bad_mode},
            clear=False,
        ):
            assert bff_main._bff_auth_mode() == "strict"
            assert bff_main._bff_auth_stub_enabled() is False
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
                from services.control_plane.bff.settings_store import SettingsStore
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
                from services.control_plane.bff.settings_store import SettingsStore
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


# ---------------------------------------------------------------------------
# OIDC / JWKS validation tests (SVC-BFF-OIDC-JWKS-AUTH-FACADE)
# ---------------------------------------------------------------------------

def _build_rsa_test_fixtures():
    """Generate a one-time RSA-2048 keypair and matching JWK for tests."""
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization
        import base64 as _b64
    except ImportError:
        return None, None, None

    priv = rsa.generate_private_key(65537, 2048, default_backend())
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_numbers = priv.public_key().public_numbers()

    def _int_to_b64url(n: int) -> str:
        byte_len = (n.bit_length() + 7) // 8
        return _b64.urlsafe_b64encode(n.to_bytes(byte_len, "big")).rstrip(b"=").decode()

    jwk = {
        "kty": "RSA",
        "kid": "test-kid-1",
        "use": "sig",
        "alg": "RS256",
        "n": _int_to_b64url(pub_numbers.n),
        "e": _int_to_b64url(pub_numbers.e),
    }
    return priv_pem, jwk, pub_numbers


def _build_ec_test_fixtures():
    """Generate a one-time P-256 keypair and matching JWK for tests."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        import base64 as _b64
    except ImportError:
        return None, None, None

    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_numbers = priv.public_key().public_numbers()

    def _int_to_b64url(n: int) -> str:
        return _b64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()

    jwk = {
        "kty": "EC",
        "kid": "test-ec-kid-1",
        "use": "sig",
        "alg": "ES256",
        "crv": "P-256",
        "x": _int_to_b64url(pub_numbers.x),
        "y": _int_to_b64url(pub_numbers.y),
    }
    return priv_pem, jwk, pub_numbers


try:
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa_mod
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False

if _CRYPTO_AVAILABLE:
    _RSA_PRIV_PEM, _TEST_JWK, _ = _build_rsa_test_fixtures()
    _EC_PRIV_PEM, _TEST_EC_JWK, _ = _build_ec_test_fixtures()
    _TEST_JWKS = [_TEST_JWK]
    _TEST_JWKS_ALT_KID = [{**_TEST_JWK, "kid": "other-kid"}]
else:
    _RSA_PRIV_PEM = _EC_PRIV_PEM = _TEST_JWK = _TEST_EC_JWK = _TEST_JWKS = _TEST_JWKS_ALT_KID = None


def _b64_json(data: dict) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        .rstrip(b"=")
        .decode("ascii")
    )


def _make_rs256_jwt(
    *,
    sub: str = "op-oidc",
    roles: list | None = None,
    issuer: str = "https://idp.example.com",
    audience: str = "bff-operators",
    kid: str | None = "test-kid-1",
    alg: str = "RS256",
    exp_offset: int = 3600,
    extra: dict | None = None,
) -> str:
    """Sign an RS256 JWT with the test RSA private key."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    payload: dict = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
    }
    if roles is not None:
        payload["roles"] = roles
    if extra:
        payload.update(extra)
    header = {"alg": alg, "typ": "JWT"}
    if kid is not None:
        header["kid"] = kid

    header_b64 = _b64_json(header)
    payload_b64 = _b64_json(payload)
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    private_key = serialization.load_pem_private_key(_RSA_PRIV_PEM, password=None)
    signature = private_key.sign(
        signing_input,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{header_b64}.{payload_b64}.{signature_b64}"


def _make_es256_jwt(
    *,
    sub: str = "op-oidc-ec",
    roles: list | None = None,
    issuer: str = "https://idp.example.com",
    audience: str = "bff-operators",
    kid: str = "test-ec-kid-1",
    exp_offset: int = 3600,
) -> str:
    """Sign an ES256 JWT with the test P-256 private key."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    payload: dict = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
    }
    if roles is not None:
        payload["roles"] = roles
    header = {"alg": "ES256", "typ": "JWT", "kid": kid}
    header_b64 = _b64_json(header)
    payload_b64 = _b64_json(payload)
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    private_key = serialization.load_pem_private_key(_EC_PRIV_PEM, password=None)
    der_signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der_signature)
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{header_b64}.{payload_b64}.{signature_b64}"


_JWKS_ENV = {
    "PANTHEON_BFF_AUTH_STUB": "",
    "PANTHEON_BFF_AUTH_MODE": "strict",
    "PANTHEON_BFF_JWT_SECRET": "",
    "PANTHEON_BFF_JWKS_URI": "https://idp.example.com/.well-known/jwks.json",
    "PANTHEON_BFF_OIDC_ISSUER": "https://idp.example.com",
    "PANTHEON_BFF_OIDC_AUDIENCE": "bff-operators",
    "PANTHEON_BFF_MFA_REQUIRED": "false",
}

_JWKS_FETCH_TARGET = "services.runtime_auth_inbound._fetch_jwks_keys"


@pytest.mark.skipif(not _CRYPTO_AVAILABLE, reason="cryptography package not installed")
class TestExtractIdentityJwks:
    """OIDC/JWKS validation tests — require cryptography."""

    def _call(
        self,
        authorization: str,
        *,
        mfa_token: str | None = None,
        env_overrides: dict | None = None,
        mock_keys: list | None = None,
    ):
        env = {**_JWKS_ENV, **(env_overrides or {})}
        keys = mock_keys if mock_keys is not None else _TEST_JWKS
        with patch(_JWKS_FETCH_TARGET, return_value=keys):
            with patch.dict(os.environ, env, clear=False):
                return _extract_identity_jwt(authorization, mfa_token=mfa_token)

    # ---- happy path ----

    def test_valid_jwks_token_returns_identity(self):
        token = _make_rs256_jwt(sub="op-oidc", roles=["operator"])
        identity = self._call(f"Bearer {token}")
        assert identity.operator_id == "op-oidc"
        assert "operator" in identity.roles

    def test_valid_jwks_token_multiple_roles(self):
        token = _make_rs256_jwt(sub="op-admin", roles=["admin", "operator"])
        identity = self._call(f"Bearer {token}")
        assert "admin" in identity.roles
        assert "operator" in identity.roles

    def test_valid_es256_jwks_token_returns_identity(self):
        token = _make_es256_jwt(sub="op-ec", roles=["operator"])
        identity = self._call(f"Bearer {token}", mock_keys=[_TEST_EC_JWK])
        assert identity.operator_id == "op-ec"
        assert "operator" in identity.roles

    def test_valid_jwks_mfa_verified_when_header_present(self):
        token = _make_rs256_jwt(sub="op-admin", roles=["admin"])
        identity = self._call(f"Bearer {token}", mfa_token="123456")
        assert identity.mfa_verified is True

    def test_role_and_mfa_claim_mapping_can_be_strict(self):
        token = _make_rs256_jwt(
            sub="op-idp-admin",
            roles=None,
            extra={"groups": ["pantheon-staging-admins"], "amr": ["pwd", "mfa"]},
        )
        identity = self._call(
            f"Bearer {token}",
            env_overrides={
                "PANTHEON_BFF_ROLE_CLAIMS": "groups",
                "PANTHEON_BFF_ROLE_MAP": "pantheon-staging-admins=admin;pantheon-staging-operators=operator",
                "PANTHEON_BFF_ROLE_MAP_MODE": "strict",
                "PANTHEON_BFF_MFA_CLAIMS": "amr",
                "PANTHEON_BFF_MFA_VALUES": "mfa",
                "PANTHEON_BFF_MFA_REQUIRED": "true",
            },
        )
        assert identity.operator_id == "op-idp-admin"
        assert identity.roles == ["admin"]
        assert identity.mfa_verified is True

    def test_supabase_app_metadata_role_maps_to_operator(self):
        """Only the admin-owned nested role claim grants operator authority."""
        token = _make_rs256_jwt(
            sub="supabase-operator",
            roles=None,
            audience="authenticated",
            issuer="https://supabase.example/auth/v1",
            extra={
                "role": "authenticated",
                "app_metadata": {"roles": ["pantheon-operator"]},
            },
        )
        identity = self._call(
            f"Bearer {token}",
            env_overrides={
                "PANTHEON_BFF_OIDC_ISSUER": "https://supabase.example/auth/v1",
                "PANTHEON_BFF_OIDC_AUDIENCE": "authenticated",
                "PANTHEON_BFF_ROLE_CLAIMS": "app_metadata.roles,roles",
                "PANTHEON_BFF_ROLE_MAP": "pantheon-operator=operator;pantheon-viewer=viewer",
                "PANTHEON_BFF_ROLE_MAP_MODE": "strict",
                "PANTHEON_BFF_DEFAULT_ROLE": "viewer",
            },
        )
        assert identity.roles == ["operator"]

    def test_supabase_authenticated_role_does_not_become_operator(self):
        """A normal Supabase login without admin metadata fails closed to viewer."""
        token = _make_rs256_jwt(
            sub="supabase-unassigned",
            roles=None,
            audience="authenticated",
            issuer="https://supabase.example/auth/v1",
            extra={"role": "authenticated"},
        )
        identity = self._call(
            f"Bearer {token}",
            env_overrides={
                "PANTHEON_BFF_OIDC_ISSUER": "https://supabase.example/auth/v1",
                "PANTHEON_BFF_OIDC_AUDIENCE": "authenticated",
                "PANTHEON_BFF_ROLE_CLAIMS": "app_metadata.roles,roles",
                "PANTHEON_BFF_ROLE_MAP": "pantheon-operator=operator;pantheon-viewer=viewer",
                "PANTHEON_BFF_ROLE_MAP_MODE": "strict",
                "PANTHEON_BFF_DEFAULT_ROLE": "viewer",
            },
        )
        assert identity.roles == ["viewer"]

    def test_mfa_required_rejects_unaccepted_idp_mfa_claim(self):
        """MFA_REQUIRED=true must not accept an IdP claim outside the configured values."""
        from fastapi import HTTPException

        token = _make_rs256_jwt(
            sub="op-idp-admin",
            roles=["admin"],
            extra={"amr": ["pwd", "sms"]},
        )
        with pytest.raises(HTTPException) as exc_info:
            self._call(
                f"Bearer {token}",
                env_overrides={
                    "PANTHEON_BFF_MFA_REQUIRED": "true",
                    "PANTHEON_BFF_MFA_CLAIMS": "amr",
                    "PANTHEON_BFF_MFA_VALUES": "mfa,webauthn",
                },
            )
        assert exc_info.value.status_code == 401
        assert "MFA_REQUIRED" in json.dumps(exc_info.value.detail)

    def test_strict_role_map_unmapped_group_denies_admin_write(self):
        """Unmapped IdP groups must not satisfy protected BFF admin routes."""
        token = _make_rs256_jwt(
            sub="op-unmapped",
            roles=None,
            extra={"groups": ["unknown-idp-group"], "amr": ["pwd", "mfa"]},
        )
        env = {
            **_JWKS_ENV,
            "PANTHEON_BFF_ROLE_CLAIMS": "groups",
            "PANTHEON_BFF_ROLE_MAP": "pantheon-staging-admins=admin",
            "PANTHEON_BFF_ROLE_MAP_MODE": "strict",
            "PANTHEON_BFF_MFA_REQUIRED": "true",
            "PANTHEON_BFF_MFA_CLAIMS": "amr",
            "PANTHEON_BFF_MFA_VALUES": "mfa",
        }
        with patch(_JWKS_FETCH_TARGET, return_value=_TEST_JWKS):
            with patch.dict(os.environ, env, clear=False):
                with tempfile.TemporaryDirectory() as td:
                    original = bff_main.settings_store
                    from services.control_plane.bff.settings_store import SettingsStore

                    bff_main.settings_store = SettingsStore(os.path.join(td, "settings.json"))
                    try:
                        client = TestClient(bff_main.app)
                        resp = client.post(
                            "/api/v1/settings",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"settings": {"general": {"theme": "dark"}}},
                        )
                    finally:
                        bff_main.settings_store = original

        assert resp.status_code == 403
        assert _response_error(resp)["details"]["precondition_failed"] == "role_check"

    # ---- kid matching ----

    def test_kid_mismatch_raises_401(self):
        """Token kid not present in JWKS keys must be rejected."""
        from fastapi import HTTPException
        token = _make_rs256_jwt(kid="unknown-kid")
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}", mock_keys=_TEST_JWKS)
        assert exc_info.value.status_code == 401

    def test_empty_jwks_keys_raises_401(self):
        """Empty JWKS key list must return 401."""
        from fastapi import HTTPException
        token = _make_rs256_jwt()
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}", mock_keys=[])
        assert exc_info.value.status_code == 401

    def test_first_key_used_when_no_kid_in_token(self):
        """When JWT has no kid header, the first JWKS key should be tried."""
        token = _make_rs256_jwt(sub="op-nokid", roles=["operator"], kid=None)
        identity = self._call(f"Bearer {token}")
        assert identity.operator_id == "op-nokid"

    def test_unsupported_jwks_alg_raises_401(self):
        """JWKS mode must reject unsupported header algorithms before verification."""
        from fastapi import HTTPException
        token = _make_rs256_jwt(alg="HS256")
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}")
        assert exc_info.value.status_code == 401

    # ---- issuer / audience / expiry ----

    def test_issuer_mismatch_raises_401(self):
        from fastapi import HTTPException
        token = _make_rs256_jwt(issuer="https://wrong-idp.example.com")
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}")
        assert exc_info.value.status_code == 401

    def test_audience_mismatch_raises_401(self):
        from fastapi import HTTPException
        token = _make_rs256_jwt(audience="wrong-audience")
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}")
        assert exc_info.value.status_code == 401

    def test_expired_jwks_token_raises_401(self):
        from fastapi import HTTPException
        token = _make_rs256_jwt(exp_offset=-60)
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}")
        assert exc_info.value.status_code == 401

    def test_missing_subject_raises_401(self):
        """JWT with blank sub must be rejected."""
        from fastapi import HTTPException
        token = _make_rs256_jwt(sub="")
        with pytest.raises(HTTPException) as exc_info:
            self._call(f"Bearer {token}")
        assert exc_info.value.status_code == 401

    # ---- JWKS fetch failure — env leak check ----

    def test_jwks_fetch_failure_returns_generic_401(self):
        """JWKS fetch error must not leak URI or config in the response."""
        from fastapi import HTTPException
        from services.runtime_auth_inbound import AuthError
        jwks_uri = "https://idp.example.com/.well-known/jwks.json"
        token = _make_rs256_jwt()
        with patch(
            _JWKS_FETCH_TARGET,
            side_effect=AuthError("JWKS_FETCH_FAILED", "JWKS endpoint unavailable", 401),
        ):
            with patch.dict(os.environ, _JWKS_ENV, clear=False):
                with pytest.raises(HTTPException) as exc_info:
                    _extract_identity_jwt(f"Bearer {token}")
        assert exc_info.value.status_code == 401
        detail_str = json.dumps(exc_info.value.detail)
        assert jwks_uri not in detail_str
        assert "JWKS_FETCH_FAILED" not in detail_str

    def test_jwks_no_matching_key_returns_generic_401(self):
        """JWKS_NO_MATCHING_KEY must not leak the code in the response."""
        from fastapi import HTTPException
        from services.runtime_auth_inbound import AuthError
        token = _make_rs256_jwt()
        with patch(
            _JWKS_FETCH_TARGET,
            side_effect=AuthError("JWKS_NO_MATCHING_KEY", "No matching JWKS key", 401),
        ):
            with patch.dict(os.environ, _JWKS_ENV, clear=False):
                with pytest.raises(HTTPException) as exc_info:
                    _extract_identity_jwt(f"Bearer {token}")
        assert exc_info.value.status_code == 401
        assert "JWKS_NO_MATCHING_KEY" not in json.dumps(exc_info.value.detail)

    def test_oidc_discovery_failure_returns_generic_401(self):
        """OIDC discovery failures must not leak discovery URL or internal code."""
        from fastapi import HTTPException
        from services.runtime_auth_inbound import AuthError
        discovery_url = "https://idp.example.com/.well-known/openid-configuration"
        token = _make_rs256_jwt()
        env = {
            **_JWKS_ENV,
            "PANTHEON_BFF_JWKS_URI": "",
            "PANTHEON_BFF_OIDC_DISCOVERY_URL": discovery_url,
        }
        with patch(
            "services.runtime_auth_inbound._fetch_oidc_metadata",
            side_effect=AuthError("OIDC_DISCOVERY_FAILED", "OIDC discovery unavailable", 401),
        ):
            with patch.dict(os.environ, env, clear=False):
                with pytest.raises(HTTPException) as exc_info:
                    _extract_identity_jwt(f"Bearer {token}")
        assert exc_info.value.status_code == 401
        detail_str = json.dumps(exc_info.value.detail)
        assert discovery_url not in detail_str
        assert "OIDC_DISCOVERY_FAILED" not in detail_str

    # ---- cache behaviour ----

    def test_jwks_cache_used_on_second_call(self):
        """_fetch_jwks_keys should call urlopen once while the TTL cache is warm."""
        from unittest.mock import MagicMock
        import services.runtime_auth_inbound as auth_mod

        original_cache = dict(auth_mod._JWKS_CACHE)
        jwks_uri = _JWKS_ENV["PANTHEON_BFF_JWKS_URI"]
        auth_mod._JWKS_CACHE.clear()
        try:
            response = MagicMock()
            response.read.return_value = json.dumps({"keys": _TEST_JWKS}).encode("utf-8")
            response.__enter__.return_value = response
            with patch("services.runtime_auth_inbound.urllib.request.urlopen", return_value=response) as urlopen:
                first = auth_mod._fetch_jwks_keys(jwks_uri, now=1000.0)
                second = auth_mod._fetch_jwks_keys(jwks_uri, now=1001.0)
            assert first == _TEST_JWKS
            assert second == _TEST_JWKS
            assert urlopen.call_count == 1
        finally:
            auth_mod._JWKS_CACHE.clear()
            auth_mod._JWKS_CACHE.update(original_cache)

    def test_jwks_cache_miss_refreshes_for_rotated_kid(self):
        """A cached JWKS kid miss should force one refresh before rejecting."""
        from unittest.mock import MagicMock
        import services.runtime_auth_inbound as auth_mod

        original_cache = dict(auth_mod._JWKS_CACHE)
        jwks_uri = _JWKS_ENV["PANTHEON_BFF_JWKS_URI"]
        token = _make_rs256_jwt(sub="op-rotated", roles=["operator"], kid="test-kid-1")
        auth_mod._JWKS_CACHE.clear()
        auth_mod._JWKS_CACHE[jwks_uri] = (_TEST_JWKS_ALT_KID, time.time())
        try:
            response = MagicMock()
            response.read.return_value = json.dumps({"keys": _TEST_JWKS}).encode("utf-8")
            response.__enter__.return_value = response
            with patch("services.runtime_auth_inbound.urllib.request.urlopen", return_value=response) as urlopen:
                with patch.dict(os.environ, _JWKS_ENV, clear=False):
                    identity = _extract_identity_jwt(f"Bearer {token}")
            assert identity.operator_id == "op-rotated"
            assert urlopen.call_count == 1
        finally:
            auth_mod._JWKS_CACHE.clear()
            auth_mod._JWKS_CACHE.update(original_cache)

    def test_oidc_discovery_url_resolves_jwks_uri(self):
        token = _make_rs256_jwt(
            sub="op-discovery",
            roles=["operator"],
            issuer="https://discovery-idp.example.com",
        )
        env = {
            **_JWKS_ENV,
            "PANTHEON_BFF_JWKS_URI": "",
            "PANTHEON_BFF_OIDC_DISCOVERY_URL": "https://discovery-idp.example.com/.well-known/openid-configuration",
            "PANTHEON_BFF_OIDC_ISSUER": "",
        }
        with patch(
            "services.runtime_auth_inbound._fetch_oidc_metadata",
            return_value={
                "issuer": "https://discovery-idp.example.com",
                "jwks_uri": "https://discovery-idp.example.com/jwks.json",
            },
        ) as discovery:
            with patch(_JWKS_FETCH_TARGET, return_value=_TEST_JWKS) as fetch:
                with patch.dict(os.environ, env, clear=False):
                    identity = _extract_identity_jwt(f"Bearer {token}")
        assert identity.operator_id == "op-discovery"
        discovery.assert_called_once()
        assert fetch.call_args.args[0] == "https://discovery-idp.example.com/jwks.json"

    # ---- role claim mapping ----

    def test_role_singular_claim_passthrough(self):
        """'role' (singular) claim is honored via default passthrough mapping."""
        token = _make_rs256_jwt(sub="op-role-single", roles=None, extra={"role": "reviewer"})
        identity = self._call(f"Bearer {token}")
        assert "reviewer" in identity.roles

    def test_default_role_fallback_when_no_role_claim(self):
        """No roles/role claim in token → PANTHEON_BFF_DEFAULT_ROLE is applied."""
        token = _make_rs256_jwt(sub="op-norole", roles=None)
        identity = self._call(
            f"Bearer {token}",
            env_overrides={"PANTHEON_BFF_DEFAULT_ROLE": "reviewer"},
        )
        assert identity.roles == ["reviewer"]

    def test_strict_role_map_rejects_unmapped_group(self):
        """Strict role map gives empty roles when the IdP group has no mapping entry."""
        token = _make_rs256_jwt(
            sub="op-unmapped",
            roles=None,
            extra={"groups": ["unknown-idp-group"]},
        )
        identity = self._call(
            f"Bearer {token}",
            env_overrides={
                "PANTHEON_BFF_ROLE_CLAIMS": "groups",
                "PANTHEON_BFF_ROLE_MAP": "pantheon-admins=admin",
                "PANTHEON_BFF_ROLE_MAP_MODE": "strict",
            },
        )
        assert not identity.roles  # unmapped group must not silently become any BFF role

    def test_role_map_passthrough_multiple_roles(self):
        """Passthrough mode keeps all claim roles that are not remapped."""
        token = _make_rs256_jwt(sub="op-multi", roles=["operator", "reviewer"])
        identity = self._call(f"Bearer {token}")
        assert "operator" in identity.roles
        assert "reviewer" in identity.roles

    # ---- MFA claim mapping ----

    def test_mfa_from_amr_claim_default_paths(self):
        """amr=[mfa] in token payload signals mfa_verified via default claim paths."""
        token = _make_rs256_jwt(sub="op-amr", roles=["operator"], extra={"amr": ["pwd", "mfa"]})
        identity = self._call(f"Bearer {token}")
        assert identity.mfa_verified is True

    def test_mfa_from_acr_claim(self):
        """acr=mfa in token payload signals mfa_verified."""
        token = _make_rs256_jwt(sub="op-acr", roles=["operator"], extra={"acr": "mfa"})
        identity = self._call(f"Bearer {token}")
        assert identity.mfa_verified is True

    def test_mfa_from_boolean_mfa_verified_claim(self):
        """Boolean mfa_verified=true claim signals mfa_verified."""
        token = _make_rs256_jwt(sub="op-mfabool", roles=["operator"], extra={"mfa_verified": True})
        identity = self._call(f"Bearer {token}")
        assert identity.mfa_verified is True

    def test_gcp_identity_totp_claim_satisfies_mfa(self):
        token = _make_rs256_jwt(
            sub="gcp-user",
            roles=["operator"],
            extra={
                "email_verified": True,
                "firebase": {
                    "sign_in_provider": "password",
                    "sign_in_second_factor": "totp",
                },
            },
        )
        identity = self._call(
            f"Bearer {token}",
            env_overrides={
                "PANTHEON_BFF_MFA_REQUIRED": "true",
                "PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED": "true",
            },
        )
        assert identity.operator_id == "gcp-user"
        assert identity.mfa_verified is True

    def test_gcp_identity_unverified_email_is_rejected(self):
        from fastapi import HTTPException

        token = _make_rs256_jwt(
            sub="gcp-user",
            roles=["viewer"],
            extra={
                "email_verified": False,
                "firebase": {"sign_in_provider": "password"},
            },
        )
        with pytest.raises(HTTPException) as exc_info:
            self._call(
                f"Bearer {token}",
                env_overrides={"PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED": "true"},
            )
        assert exc_info.value.status_code == 401
        assert "AUTH_EMAIL_UNVERIFIED" in json.dumps(exc_info.value.detail)

    def test_mfa_not_verified_without_any_claim(self):
        """Token without amr/acr/mfa claims and without X-MFA-Token header is not MFA-verified."""
        token = _make_rs256_jwt(sub="op-nomfa", roles=["operator"])
        identity = self._call(f"Bearer {token}")
        assert identity.mfa_verified is False

    # ---- HS256 backward compatibility ----

    def test_hs256_still_works_when_jwks_uri_not_set(self):
        """When JWKS_URI is absent, HS256 path must work unchanged."""
        token = _make_jwt(sub="op-hs256", roles=["operator"])
        env = {**_BFF_ENV}  # no JWKS_URI
        with patch.dict(os.environ, env, clear=False):
            identity = _extract_identity_jwt(f"Bearer {token}")
        assert identity.operator_id == "op-hs256"
        assert "operator" in identity.roles

    # ---- stub token rejected in JWKS mode ----

    def test_stub_token_rejected_in_jwks_mode(self):
        """Colon-format stub tokens must not work when JWKS mode is active."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call("Bearer op-admin:admin:mfa")
        assert exc_info.value.status_code == 401
