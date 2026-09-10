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
from starlette.requests import Request

import os
import re
from typing import Any, List, Optional

from fastapi import FastAPI, Response
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from services.control_plane.bff.auth.policy import (
    bff_auth_stub_enabled as _bff_auth_stub_enabled,
    extract_identity as _extract_identity,
    extract_identity_jwt as _extract_identity_jwt,
    is_production_strict_mode as _is_production_strict_mode,
)

_DEFAULT_LOVABLE_CORS_ORIGINS = [
    "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io",
    "https://preview--pantheon-dev.lovable.app",
    "https://preview--pantheon-ai-system-front-dev.lovable.app",
    "https://preview--pantheon-ai-system-front-staging-live.lovable.app",
    "https://preview--pantheon.lovable.app",
    "https://preview--pantheon-ai-system-front.lovable.app",
    "https://pantheon-dev.lovable.app",
    "https://pantheon-ai-system-front-dev.lovable.app",
    "https://pantheon-ai-system-front-staging-live.lovable.app",
    "https://pantheon.lovable.app",
    "https://pantheon-ai-system-front.lovable.app",
    "https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com",
    "https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app",
    "https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com",
]
_DEV_LOOPBACK_CORS_ORIGINS = [
    "http://127.0.0.1:4173",
    "http://localhost:4173",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]
_DEV_LOVABLE_CORS_ORIGINS = {
    "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io",
    "https://preview--pantheon-dev.lovable.app",
    "https://preview--pantheon-ai-system-front-dev.lovable.app",
    "https://pantheon-dev.lovable.app",
    "https://pantheon-ai-system-front-dev.lovable.app",
    "https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com",
}
_LOVABLE_PREVIEW_UUIDS = (
    "b75d3452-f667-4cf4-893a-1061de45b347"
    "|140c41d5-9cd8-4d6b-ba02-66d5941d0dbe"
)
_LOVABLE_PREVIEW_ORIGIN_REGEX = (
    r"https://id-preview(?:-[a-f0-9]+)?--({})"
    r"\.lovable\.app"
).format(_LOVABLE_PREVIEW_UUIDS)
_LOVABLE_PREVIEW_ORIGIN_PATTERN = re.compile(
    r"^" + _LOVABLE_PREVIEW_ORIGIN_REGEX + r"$"
)

_CORS_ALLOW_HEADERS = [
    "Accept",
    "Accept-Language",
    "Authorization",
    "Cache-Control",
    "Content-Type",
    "If-Match",
    "X-BFF-Api-Version",
    "X-Confirm-Token",
    "Idempotency-Key",
    "Last-Event-ID",
    "X-Correlation-Id",
    "X-Dry-Run",
    "X-Idempotency-Key",
    "X-Locale",
    "X-MFA-Token",
    "X-Request-Id",
    "X-Refresh-Token",
    "X-Tenant-Id",
    "X-Trace-Id",
]
_CORS_EXPOSE_HEADERS = [
    "ETag",
    "X-BFF-Api-Version",
    "X-Correlation-Id",
    "X-Request-Id",
]


def _normalized_origin(origin: str) -> str:
    return origin.strip().rstrip("/")


def _dedupe_origins(origins: List[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for origin in origins:
        cleaned = _normalized_origin(origin)
        if cleaned and cleaned not in seen:
            deduped.append(cleaned)
            seen.add(cleaned)
    return deduped


def _cors_origins_from_env() -> List[str]:
    raw = os.getenv("PANTHEON_BFF_CORS_ORIGINS", "")
    origins = _dedupe_origins(raw.split(",")) if raw.strip() else list(_DEFAULT_LOVABLE_CORS_ORIGINS)
    if _is_production_strict_mode():
        origins = [
            origin
            for origin in origins
            if origin not in _DEV_LOVABLE_CORS_ORIGINS and origin != "*"
        ]
    else:
        origins = origins + _DEV_LOOPBACK_CORS_ORIGINS
    return _dedupe_origins(origins)


def _cors_origin_allowed(origin: Optional[str]) -> bool:
    if not origin:
        return False
    normalized = _normalized_origin(origin)
    if normalized in _cors_origins_from_env():
        return True
    if not _is_production_strict_mode() and _LOVABLE_PREVIEW_ORIGIN_PATTERN.fullmatch(origin):
        return True
    return False


def _with_cors_actual_response_headers(request: Request, headers: dict[str, str]) -> dict[str, str]:
    response_headers = dict(headers)
    origin = request.headers.get("origin")
    if not origin or not _cors_origin_allowed(origin):
        return response_headers

    response_headers.setdefault("Access-Control-Allow-Origin", _normalized_origin(origin))
    response_headers.setdefault("Access-Control-Allow-Credentials", "true")
    response_headers.setdefault("Access-Control-Expose-Headers", ", ".join(_CORS_EXPOSE_HEADERS))

    vary_value = response_headers.get("Vary") or response_headers.get("vary") or ""
    vary_parts = [part.strip() for part in vary_value.split(",") if part.strip()]
    if "Origin" not in {part.title() for part in vary_parts}:
        vary_parts.append("Origin")
    if vary_parts:
        response_headers["Vary"] = ", ".join(vary_parts)
    return response_headers


def _pack_d_http_exception_response(
    request: Request,
    exc: HTTPException,
) -> JSONResponse:
    headers = _with_cors_actual_response_headers(
        request,
        dict(getattr(exc, "headers", None) or {}),
    )
    detail = exc.detail
    content: dict[str, Any] = detail if isinstance(detail, dict) else {"detail": detail}
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers=headers,
    )


class _PantheonCORSMiddleware(CORSMiddleware):
    def preflight_response(self, request_headers: Any) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code != 200:
            return response
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.pop("content-type", None)
        return Response(status_code=204, headers=headers)


def _build_bff_app() -> FastAPI:
    cors_origins = _cors_origins_from_env()
    strict = _is_production_strict_mode()
    preview_regex = None if strict else _LOVABLE_PREVIEW_ORIGIN_REGEX
    built_app = FastAPI(title="Pantheon Operator BFF", version="0.2.0")
    if cors_origins or preview_regex:
        middleware_kwargs: dict[str, Any] = dict(
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=_CORS_ALLOW_HEADERS,
            expose_headers=_CORS_EXPOSE_HEADERS,
        )
        if preview_regex:
            middleware_kwargs["allow_origin_regex"] = preview_regex
        built_app.add_middleware(_PantheonCORSMiddleware, **middleware_kwargs)

    @built_app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def _catchall(path: str):
        return {"ok": True}

    return built_app

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
    client = TestClient(_build_bff_app())
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

    origins = _cors_origins_from_env()

    assert "https://preview--pantheon-dev.lovable.app" in origins
    assert "https://pantheon-dev.lovable.app" in origins
    assert "https://pantheon.lovable.app" in origins
    assert "https://pantheon-ai-system-front-staging-live.lovable.app" in origins


def test_dev_loopback_cors_origins_present_with_explicit_override(monkeypatch) -> None:
    # The deploy-time override never lists the CI/local vite origins; non-strict
    # mode must still accept them so the FE-BFF integration gate's browser
    # EventSource handshake is not blocked by CORS.
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://pantheon-dev.lovable.app")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    origins = _cors_origins_from_env()

    assert "http://127.0.0.1:4173" in origins
    assert "http://localhost:4173" in origins
    assert _cors_origin_allowed("http://127.0.0.1:4173")

    allowed = _cors_preflight("http://127.0.0.1:4173")
    assert allowed.status_code == 204
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:4173"


def test_production_strict_mode_excludes_dev_loopback_origins(monkeypatch) -> None:
    monkeypatch.setenv(
        "PANTHEON_BFF_CORS_ORIGINS",
        "https://pantheon-ai-system-front-staging-live.lovable.app",
    )
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "production")

    origins = _cors_origins_from_env()

    assert "http://127.0.0.1:4173" not in origins
    assert not _cors_origin_allowed("http://127.0.0.1:4173")


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

    client = TestClient(_build_bff_app())
    response = client.get("/any-route", headers={"Origin": "https://pantheon-dev.lovable.app"})

    exposed = {
        header.strip()
        for header in response.headers["access-control-expose-headers"].split(",")
    }
    assert exposed == set(_CORS_EXPOSE_HEADERS)
    assert "ETag" in exposed


def test_pack_d_http_exception_response_preserves_cors_for_allowed_origin(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/bff/agora/trading-room",
            "headers": [
                (b"origin", b"https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io"),
            ],
        }
    )
    response = _pack_d_http_exception_response(
        request,
        HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Tenant access denied",
                    "details": {"precondition_failed": "tenant_scope"},
                }
            },
        ),
    )

    assert response.status_code == 403
    assert (
        response.headers["access-control-allow-origin"]
        == "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io"
    )
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-expose-headers"] == ", ".join(_CORS_EXPOSE_HEADERS)
    assert "Origin" in response.headers["vary"]


def test_pack_d_http_exception_response_does_not_add_cors_for_unlisted_origin(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://pantheon-dev.lovable.app")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/bff/agora/trading-room",
            "headers": [(b"origin", b"https://evil.example.com")],
        }
    )
    response = _pack_d_http_exception_response(
        request,
        HTTPException(status_code=403, detail={"error": "FORBIDDEN", "message": "Nope"}),
    )

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_lovable_cors_preflight_accepts_bff_client_headers(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_CORS_ORIGINS", "https://pantheon-dev.lovable.app")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    client = TestClient(_build_bff_app())
    response = client.options(
        "/bff/me",
        headers={
            "Origin": "https://pantheon-dev.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": (
                "Authorization, Accept, Accept-Language, Content-Type, "
                "If-Match, "
                "X-BFF-Api-Version, X-Correlation-Id, X-Request-Id, "
                "X-Idempotency-Key, Idempotency-Key, X-Confirm-Token, "
                "X-Locale, X-MFA-Token, X-Tenant-Id, Last-Event-ID"
            ),
        },
    )

    allowed = {
        header.strip().lower()
        for header in response.headers["access-control-allow-headers"].split(",")
    }
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://pantheon-dev.lovable.app"
    assert {header.lower() for header in _CORS_ALLOW_HEADERS}.issubset(allowed)


def test_production_strict_mode_filters_dev_cors_override(monkeypatch) -> None:
    monkeypatch.setenv(
        "PANTHEON_BFF_CORS_ORIGINS",
        "https://pantheon-dev.lovable.app,https://pantheon-ai-system-front-staging-live.lovable.app,*",
    )
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "production")

    origins = _cors_origins_from_env()

    assert "https://pantheon-dev.lovable.app" not in origins
    assert "*" not in origins
    assert origins == ["https://pantheon-ai-system-front-staging-live.lovable.app"]


def test_dev_stub_is_disabled_in_strict_mode(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")

    assert _bff_auth_stub_enabled() is False
    with pytest.raises(HTTPException) as exc_info:
        _extract_identity("Bearer op-dev:operator")
    assert exc_info.value.status_code == 401

    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    identity = _extract_identity("Bearer op-dev:operator")
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
        identity = _extract_identity_jwt(f"Bearer {token}")

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
            _extract_identity_jwt(f"Bearer {token}")

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
            _extract_identity_jwt(f"Bearer {token}")

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
        identity = _extract_identity_jwt(f"Bearer {token}")

    assert identity.operator_id == "op-rotated"
    assert fetch.call_count == 2
    assert fetch.call_args_list[1].kwargs["force_refresh"] is True


# BFF-B1-001: CORS fix for Lovable preview and published origins
# BFF-B1-001-DELTA: regression — execute-plans published URL must survive production-strict filter


def test_execute_plans_lovableproject_in_default_origins(monkeypatch) -> None:
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    origins = _cors_origins_from_env()

    assert "https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com" in origins


def test_execute_plans_lovableproject_survives_production_strict_filter(monkeypatch) -> None:
    """BFF-B1-001-DELTA regression: 140c41d5 published URL is NOT dev-only and must remain
    in the CORS allowlist when PANTHEON_ENV=production strict mode is active."""
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "production")

    origins = _cors_origins_from_env()

    assert "https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com" in origins


def test_self_hosted_dev_fe_origin_in_default_origins(monkeypatch) -> None:
    """off-lovable: the Pantheon-owned self-hosted dev FE origin is the current dev
    acceptance origin and must be present in the default CORS allowlist."""
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    origins = _cors_origins_from_env()

    assert "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io" in origins


def test_self_hosted_dev_fe_origin_filtered_in_production_strict(monkeypatch) -> None:
    """off-lovable: the self-hosted dev FE origin is dev-only and must be filtered out
    of the CORS allowlist when production-strict mode is active."""
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "production")

    origins = _cors_origins_from_env()

    assert "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io" not in origins


def test_static_id_preview_survives_production_strict_filter(monkeypatch) -> None:
    """BFF-B1-001-DELTA-2 regression: static id-preview origins must remain
    exact-match allowlisted when production strict mode filters dev-only origins."""
    monkeypatch.delenv("PANTHEON_BFF_CORS_ORIGINS", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_ENV", "production")

    origin = "https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app"
    origins = _cors_origins_from_env()
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

    assert _cors_origin_allowed(
        "https://id-preview-a7067bd5--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app"
    )
    assert not _cors_origin_allowed(
        "https://id-preview-a7067bd5--aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.lovable.app"
    )
