"""Stateless HTTP security and CORS middleware for the Operator BFF.

Encapsulates CORS preflight handling, origin allowlists, security headers,
and dynamic origin evaluation.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from fastapi import Request, Response
from starlette.middleware.cors import CORSMiddleware

try:
    from ..auth.policy import is_production_strict_mode as _auth_is_production_strict_mode
except ImportError:
    from services.control_plane.bff.auth.policy import is_production_strict_mode as _auth_is_production_strict_mode


_DEFAULT_LOVABLE_CORS_ORIGINS: List[str] = [
    # Pantheon-owned self-hosted dev frontend (execute-plans). Dev-only:
    # filtered out by production-strict CORS filter below.
    "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io",
    # Lovable shared-preview and published URLs.
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
    # BFF-CONSOL-022: Pantheon Frontend Lovable project preview URLs.
    "https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com",
    "https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app",
    # BFF-B1-001: execute-plans Lovable project published preview.
    "https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com",
]

_DEV_LOOPBACK_CORS_ORIGINS: List[str] = [
    "http://127.0.0.1:4173",
    "http://localhost:4173",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]

_DEV_LOVABLE_CORS_ORIGINS: set[str] = {
    "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io",
    "https://preview--pantheon-dev.lovable.app",
    "https://preview--pantheon-ai-system-front-dev.lovable.app",
    "https://pantheon-dev.lovable.app",
    "https://pantheon-ai-system-front-dev.lovable.app",
    "https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com",
}

_LOVABLE_PREVIEW_UUIDS: str = (
    "b75d3452-f667-4cf4-893a-1061de45b347"
    "|140c41d5-9cd8-4d6b-ba02-66d5941d0dbe"
)

_LOVABLE_PREVIEW_ORIGIN_REGEX: str = (
    r"https://id-preview(?:-[a-f0-9]+)?--({})"
    r"\.lovable\.app"
).format(_LOVABLE_PREVIEW_UUIDS)

_LOVABLE_PREVIEW_ORIGIN_PATTERN: re.Pattern = re.compile(
    r"^" + _LOVABLE_PREVIEW_ORIGIN_REGEX + r"$"
)

_SECURITY_RESPONSE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)

_CORS_ALLOW_HEADERS: List[str] = [
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

_CORS_EXPOSE_HEADERS: List[str] = [
    "ETag",
    "X-BFF-Api-Version",
    "X-Correlation-Id",
    "X-Request-Id",
]


def _normalized_origin(origin: str) -> str:
    return origin.strip().rstrip("/")


def _dedupe_origins(origins: Iterable[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for origin in origins:
        cleaned = _normalized_origin(origin)
        if cleaned and cleaned not in seen:
            deduped.append(cleaned)
            seen.add(cleaned)
    return deduped


def _is_production_strict_mode() -> bool:
    return _auth_is_production_strict_mode()


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
        # Non-strict (dev/test) tiers always accept the loopback origins the
        # FE-BFF integration gate and local vite servers use.
        origins = origins + _DEV_LOOPBACK_CORS_ORIGINS
    return _dedupe_origins(origins)


def _cors_origin_allowed(
    origin: Optional[str],
    active_origins: Optional[Iterable[str]] = None,
) -> bool:
    if not origin:
        return False
    normalized = _normalized_origin(origin)
    origins = set(active_origins) if active_origins is not None else set(_cors_origins_from_env())
    if normalized in origins:
        return True
    if not _is_production_strict_mode() and _LOVABLE_PREVIEW_ORIGIN_PATTERN.fullmatch(origin):
        return True
    return False


class _PantheonCORSMiddleware(CORSMiddleware):
    def preflight_response(self, request_headers: Any) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code != 200:
            return response
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.pop("content-type", None)
        return Response(status_code=204, headers=headers)


class _SecurityHeadersMiddleware:
    """Pure-ASGI middleware that appends baseline security headers.

    Implemented at the ASGI layer (not BaseHTTPMiddleware) so it does not buffer
    or break StreamingResponse / SSE endpoints.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message: Any) -> None:
            if message.get("type") == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                for name, value in _SECURITY_RESPONSE_HEADERS:
                    if name not in present:
                        headers.append((name, value))
            await send(message)

        await self.app(scope, receive, _send)


def _with_cors_actual_response_headers(
    request: Request,
    headers: Dict[str, str],
    origin_allowed_fn: Optional[Callable[[Optional[str]], bool]] = None,
) -> Dict[str, str]:
    response_headers = dict(headers)
    origin = request.headers.get("origin")
    is_allowed = origin_allowed_fn or _cors_origin_allowed
    if not origin or not is_allowed(origin):
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
