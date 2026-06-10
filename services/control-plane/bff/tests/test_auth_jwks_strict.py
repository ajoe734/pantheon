"""BFF-CONSOL-014: Lovable CORS and JWKS strict-mode regression tests."""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

BFF_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BFF_DIR))
sys.path.insert(0, str(REPO_ROOT))

import main as bff_main

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


ISSUER = "https://idp.example.com"
AUDIENCE = "bff-operators"
JWKS_URI = "https://idp.example.com/.well-known/jwks.json"
JWKS_ENV = {
    "PANTHEON_BFF_AUTH_STUB": "true",
    "PANTHEON_BFF_AUTH_MODE": "strict",
    "PANTHEON_BFF_JWT_SECRET": "",
    "PANTHEON_BFF_JWKS_URI": JWKS_URI,
    "PANTHEON_BFF_OIDC_ISSUER": ISSUER,
    "PANTHEON_BFF_OIDC_AUDIENCE": AUDIENCE,
    "PANTHEON_BFF_MFA_REQUIRED": "false",
}


def _cors_preflight(origin: str):
    client = TestClient(bff_main._build_bff_app())
    return client.options(
        "/any-route",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )


def test_default_lovable_cors_origins_include_preview_dev_and_prod(monkeypatch) -> None:
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    origins = bff_main._cors_origins_from_env()

    assert "https://preview--pantheon-dev.lovable.app" in origins
    assert "https://pantheon-dev.lovable.app" in origins
    assert "https://pantheon.lovable.app" in origins
    assert "https://pantheon-ai-system-front-staging-live.lovable.app" in origins


def test_strict_cors_rejects_unlisted_origin(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://pantheon-dev.lovable.app")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    allowed = _cors_preflight("https://pantheon-dev.lovable.app")
    rejected = _cors_preflight("https://evil.example.com")

    assert allowed.status_code == 204
    assert allowed.headers["access-control-allow-origin"] == "https://pantheon-dev.lovable.app"
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


def test_cors_exposes_bff_client_response_headers(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://pantheon-dev.lovable.app")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    client = TestClient(bff_main._build_bff_app())
    response = client.get("/any-route", headers={"Origin": "https://pantheon-dev.lovable.app"})

    exposed = {
        header.strip()
        for header in response.headers["access-control-expose-headers"].split(",")
    }
    assert exposed == set(bff_main._CORS_EXPOSE_HEADERS)


def test_lovable_cors_preflight_accepts_bff_client_headers(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://pantheon-dev.lovable.app")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    client = TestClient(bff_main._build_bff_app())
    response = client.options(
        "/bff/me",
        headers={
            "Origin": "https://pantheon-dev.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": (
                "Authorization, Accept, Accept-Language, Content-Type, "
                "X-BFF-Api-Version, X-Correlation-Id, X-Request-Id, "
                "X-Idempotency-Key, Idempotency-Key, X-Confirm-Token, "
                "X-MFA-Token, Last-Event-ID"
            ),
        },
    )

    allowed = {
        header.strip().lower()
        for header in response.headers["access-control-allow-headers"].split(",")
    }
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://pantheon-dev.lovable.app"
    assert {header.lower() for header in bff_main._CORS_ALLOW_HEADERS}.issubset(allowed)


def test_production_strict_mode_filters_dev_cors_override(monkeypatch) -> None:
    monkeypatch.setenv(
        "PANTHEON_BFF_CORS_ORIGINS",
        "https://pantheon-dev.lovable.app,https://pantheon-ai-system-front-staging-live.lovable.app,*",
    )
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "production")

    origins = bff_main._cors_origins_from_env()

    assert "https://pantheon-dev.lovable.app" not in origins
    assert "*" not in origins
    assert origins == ["https://pantheon-ai-system-front-staging-live.lovable.app"]


def test_dev_stub_is_disabled_in_strict_mode(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")

    assert bff_main._bff_auth_stub_enabled() is False
    with pytest.raises(HTTPException) as exc_info:
        bff_main._extract_identity("Bearer op-dev:operator")
    assert exc_info.value.status_code == 401

    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    identity = bff_main._extract_identity("Bearer op-dev:operator")
    assert identity.operator_id == "op-dev"


def _int_to_b64url(n: int) -> str:
    byte_len = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(byte_len, "big")).rstrip(b"=").decode()


def _b64_json(data: dict) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        .rstrip(b"=")
        .decode("ascii")
    )


def _rsa_fixture(kid: str):
    private_key = rsa.generate_private_key(65537, 2048, default_backend())
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_numbers = private_key.public_key().public_numbers()
    return private_pem, {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _int_to_b64url(public_numbers.n),
        "e": _int_to_b64url(public_numbers.e),
    }


def _make_rs256_jwt(
    private_pem: bytes,
    *,
    kid: str,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    sub: str = "op-jwks",
) -> str:
    payload = {
        "sub": sub,
        "roles": ["operator"],
        "iss": issuer,
        "aud": audience,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    header_b64 = _b64_json(header)
    payload_b64 = _b64_json(payload)
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{header_b64}.{payload_b64}.{signature_b64}"


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package not installed")
def test_jwks_strict_accepts_configured_issuer_and_audience(monkeypatch) -> None:
    private_pem, jwk = _rsa_fixture("kid-current")
    token = _make_rs256_jwt(private_pem, kid="kid-current")
    for name, value in JWKS_ENV.items():
        monkeypatch.setenv(name, value)

    with patch("services.runtime_auth_inbound._fetch_jwks_keys", return_value=[jwk]):
        identity = bff_main._extract_identity_jwt(f"Bearer {token}")

    assert identity.operator_id == "op-jwks"
    assert "operator" in identity.roles


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package not installed")
def test_jwks_strict_rejects_issuer_mismatch(monkeypatch) -> None:
    private_pem, jwk = _rsa_fixture("kid-current")
    token = _make_rs256_jwt(private_pem, kid="kid-current", issuer="https://wrong-idp.example.com")
    for name, value in JWKS_ENV.items():
        monkeypatch.setenv(name, value)

    with patch("services.runtime_auth_inbound._fetch_jwks_keys", return_value=[jwk]):
        with pytest.raises(HTTPException) as exc_info:
            bff_main._extract_identity_jwt(f"Bearer {token}")

    assert exc_info.value.status_code == 401
    assert "AUTH_JWT_ISSUER_MISMATCH" in json.dumps(exc_info.value.detail)


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package not installed")
def test_jwks_strict_rejects_audience_mismatch(monkeypatch) -> None:
    private_pem, jwk = _rsa_fixture("kid-current")
    token = _make_rs256_jwt(private_pem, kid="kid-current", audience="wrong-audience")
    for name, value in JWKS_ENV.items():
        monkeypatch.setenv(name, value)

    with patch("services.runtime_auth_inbound._fetch_jwks_keys", return_value=[jwk]):
        with pytest.raises(HTTPException) as exc_info:
            bff_main._extract_identity_jwt(f"Bearer {token}")

    assert exc_info.value.status_code == 401
    assert "AUTH_JWT_AUDIENCE_MISMATCH" in json.dumps(exc_info.value.detail)


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package not installed")
def test_jwks_strict_refreshes_once_for_rotated_kid(monkeypatch) -> None:
    _old_private_pem, old_jwk = _rsa_fixture("kid-old")
    new_private_pem, new_jwk = _rsa_fixture("kid-new")
    token = _make_rs256_jwt(new_private_pem, kid="kid-new", sub="op-rotated")
    for name, value in JWKS_ENV.items():
        monkeypatch.setenv(name, value)

    with patch(
        "services.runtime_auth_inbound._fetch_jwks_keys",
        side_effect=[[old_jwk], [new_jwk]],
    ) as fetch:
        identity = bff_main._extract_identity_jwt(f"Bearer {token}")

    assert identity.operator_id == "op-rotated"
    assert fetch.call_count == 2
    assert fetch.call_args_list[1].kwargs["force_refresh"] is True


# BFF-B1-001: CORS fix for Lovable preview and published origins
# BFF-B1-001-DELTA: regression — execute-plans published URL must survive production-strict filter


def test_execute_plans_lovableproject_in_default_origins(monkeypatch) -> None:
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    origins = bff_main._cors_origins_from_env()

    assert "https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com" in origins


def test_execute_plans_lovableproject_survives_production_strict_filter(monkeypatch) -> None:
    """BFF-B1-001-DELTA regression: 140c41d5 published URL is NOT dev-only and must remain
    in the CORS allowlist when PANTHEON_ENV=production strict mode is active."""
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "production")

    origins = bff_main._cors_origins_from_env()

    assert "https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com" in origins


def test_self_hosted_dev_fe_origin_in_default_origins(monkeypatch) -> None:
    """off-lovable: the Pantheon-owned self-hosted dev FE origin is the current dev
    acceptance origin and must be present in the default CORS allowlist."""
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    origins = bff_main._cors_origins_from_env()

    assert "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io" in origins


def test_self_hosted_dev_fe_origin_filtered_in_production_strict(monkeypatch) -> None:
    """off-lovable: the self-hosted dev FE origin is dev-only and must be filtered out
    of the CORS allowlist when production-strict mode is active."""
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "production")

    origins = bff_main._cors_origins_from_env()

    assert "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io" not in origins


def test_static_id_preview_survives_production_strict_filter(monkeypatch) -> None:
    """BFF-B1-001-DELTA-2 regression: static id-preview origins must remain
    exact-match allowlisted when production strict mode filters dev-only origins."""
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "production")

    origin = "https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app"
    origins = bff_main._cors_origins_from_env()
    resp = _cors_preflight(origin)

    assert origin in origins
    assert resp.status_code == 204
    assert resp.headers.get("access-control-allow-origin") == origin


def test_execute_plans_options_preflight_succeeds_in_production_strict_mode(monkeypatch) -> None:
    """BFF-B1-001-DELTA regression: OPTIONS from the execute-plans live origin must return
    204 even when PANTHEON_ENV=production (strict mode) is active."""
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "production")

    origin = "https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com"
    resp = _cors_preflight(origin)

    assert resp.status_code == 204
    assert resp.headers.get("access-control-allow-origin") == origin


def test_preview_regex_allows_known_uuid_with_optional_commit_hash(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://pantheon-dev.lovable.app")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    preview_origins = [
        "https://id-preview-a7067bd5--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app",
        "https://id-preview--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app",
    ]

    for preview_origin in preview_origins:
        resp = _cors_preflight(preview_origin)

        assert resp.status_code == 204
        assert resp.headers.get("access-control-allow-origin") == preview_origin


def test_preview_regex_allows_old_project_uuid_with_commit_hash(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://pantheon-dev.lovable.app")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    preview_origin = "https://id-preview-deadbeef--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app"
    resp = _cors_preflight(preview_origin)

    assert resp.status_code == 204
    assert resp.headers.get("access-control-allow-origin") == preview_origin


def test_preview_regex_rejects_unknown_uuid(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://pantheon-dev.lovable.app")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    evil_origin = "https://id-preview-deadbeef--aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.lovable.app"
    resp = _cors_preflight(evil_origin)

    assert "access-control-allow-origin" not in resp.headers


def test_preview_regex_rejects_non_hex_commit_prefix(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://pantheon-dev.lovable.app")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    invalid_origin = "https://id-preview-main--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app"
    resp = _cors_preflight(invalid_origin)

    assert "access-control-allow-origin" not in resp.headers


def test_preview_regex_blocked_in_production_strict_mode(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://pantheon-ai-system-front-staging-live.lovable.app")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "production")

    preview_origin = "https://id-preview-a7067bd5--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app"
    resp = _cors_preflight(preview_origin)

    assert "access-control-allow-origin" not in resp.headers


def test_cors_origin_allowed_includes_preview_regex(monkeypatch) -> None:
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    assert bff_main._cors_origin_allowed(
        "https://id-preview-a7067bd5--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app"
    )
    assert not bff_main._cors_origin_allowed(
        "https://id-preview-a7067bd5--aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.lovable.app"
    )
