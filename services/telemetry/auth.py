"""Inbound authority and tenant scope for the telemetry HTTP surface."""

from __future__ import annotations

import functools
import hmac
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from flask import jsonify, request

from services.runtime_auth_inbound import AuthContext, AuthError, validate_request_auth


_TENANT_CLAIM_KEYS = (
    "allowed_tenants",
    "allowedTenants",
    "tenant_ids",
    "tenantIds",
    "tenants",
    "tenant_id",
    "tenantId",
)


@dataclass(frozen=True)
class TelemetryAuthority:
    actor_id: str
    roles: frozenset[str]
    tenant_id: str
    token_kind: str


class TelemetryAuthorityError(ValueError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_response(self) -> tuple[dict[str, Any], int]:
        return {"error": {"code": self.code, "message": self.message}}, self.status_code


def _split_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [part for part in re.split(r"[\s,]+", value.strip()) if part]
    if isinstance(value, Mapping):
        values: list[str] = []
        for key in ("id", "tenant_id", "tenantId", "value", "name"):
            values.extend(_split_values(value.get(key)))
        return values
    if isinstance(value, (list, tuple, set, frozenset)):
        values: list[str] = []
        for item in value:
            values.extend(_split_values(item))
        return values
    return [str(value).strip()]


def _dedupe(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            result.append(clean)
            seen.add(clean)
    return result


def _telemetry_auth_env() -> dict[str, str]:
    """Map telemetry-specific JWT settings onto the shared validator."""

    env = dict(os.environ)
    mappings = {
        "PANTHEON_RUNTIME_AUTH_MODE": "PANTHEON_TELEMETRY_AUTH_MODE",
        "PANTHEON_RUNTIME_JWT_SECRET": "PANTHEON_TELEMETRY_JWT_SECRET",
        "PANTHEON_RUNTIME_JWT_ISSUER": "PANTHEON_TELEMETRY_JWT_ISSUER",
        "PANTHEON_RUNTIME_JWT_AUDIENCE": "PANTHEON_TELEMETRY_JWT_AUDIENCE",
        "PANTHEON_RUNTIME_JWKS_URI": "PANTHEON_TELEMETRY_JWKS_URI",
        "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL": "PANTHEON_TELEMETRY_OIDC_DISCOVERY_URL",
        "PANTHEON_RUNTIME_OIDC_ISSUER": "PANTHEON_TELEMETRY_OIDC_ISSUER",
        "PANTHEON_RUNTIME_OIDC_AUDIENCE": "PANTHEON_TELEMETRY_OIDC_AUDIENCE",
        "PANTHEON_RUNTIME_ROLE_CLAIMS": "PANTHEON_TELEMETRY_ROLE_CLAIMS",
        "PANTHEON_RUNTIME_ROLE_MAP": "PANTHEON_TELEMETRY_ROLE_MAP",
        "PANTHEON_RUNTIME_ROLE_MAP_MODE": "PANTHEON_TELEMETRY_ROLE_MAP_MODE",
        "PANTHEON_RUNTIME_DEFAULT_ROLE": "PANTHEON_TELEMETRY_DEFAULT_ROLE",
        "PANTHEON_RUNTIME_REQUIRE_EMAIL_VERIFIED": "PANTHEON_TELEMETRY_REQUIRE_EMAIL_VERIFIED",
    }
    for runtime_key, telemetry_key in mappings.items():
        if telemetry_key in os.environ:
            env[runtime_key] = os.environ[telemetry_key]
        else:
            # Telemetry is an independent authority boundary. Do not inherit a
            # runtime-manager secret, issuer, role map, or privileged default
            # merely because both services share the validator implementation.
            env.pop(runtime_key, None)
    env["PANTHEON_RUNTIME_AUTH_MODE"] = os.getenv(
        "PANTHEON_TELEMETRY_AUTH_MODE",
        "strict",
    )
    # The shared validator historically grants ``operator`` to a verified JWT
    # that omits every role claim. Telemetry must fail closed in that case.
    # An operator may deliberately configure a telemetry-specific default, but
    # an unrelated PANTHEON_RUNTIME_DEFAULT_ROLE never crosses this boundary.
    env["PANTHEON_RUNTIME_DEFAULT_ROLE"] = os.getenv(
        "PANTHEON_TELEMETRY_DEFAULT_ROLE",
        "__telemetry_role_required__",
    )
    return env


def _service_context(authorization: str) -> Optional[AuthContext]:
    configured = os.getenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", "").strip()
    if not configured or not authorization.startswith("Bearer "):
        return None
    presented = authorization.split(None, 1)[1].strip()
    if not hmac.compare_digest(configured, presented):
        return None
    allowed = _split_values(
        os.getenv("PANTHEON_TELEMETRY_SERVICE_TENANTS")
    )
    return AuthContext(
        actor_id="telemetry-service",
        roles=frozenset({"service"}),
        claims={"allowed_tenants": allowed},
        token_kind="service",
    )


def _authenticate(required_roles: Sequence[str]) -> AuthContext:
    authorization = request.headers.get("Authorization", "")
    context = _service_context(authorization)
    if context is None:
        try:
            context = validate_request_auth(
                authorization=authorization,
                mfa_header=request.headers.get("X-MFA-Token", ""),
                required_roles=required_roles,
                env=_telemetry_auth_env(),
            )
        except AuthError as exc:
            raise TelemetryAuthorityError(
                exc.code,
                exc.message,
                exc.status_code,
            ) from exc
    elif required_roles and not context.has_role(*required_roles):
        raise TelemetryAuthorityError(
            "AUTH_FORBIDDEN",
            f"Role {sorted(context.roles)} not authorized",
            403,
        )
    return context


def _requested_tenant_id() -> str:
    primary = request.headers.get("X-Tenant-Id", "").strip()
    alias = request.headers.get("X-Pantheon-Tenant", "").strip()
    if primary and alias and primary != alias:
        raise TelemetryAuthorityError(
            "TENANT_HEADER_CONFLICT",
            "X-Tenant-Id and X-Pantheon-Tenant must match",
            400,
        )
    tenant_id = primary or alias
    if not tenant_id:
        raise TelemetryAuthorityError(
            "TENANT_REQUIRED",
            "X-Tenant-Id is required",
            400,
        )
    return tenant_id


def _allowed_tenants(context: AuthContext) -> list[str]:
    values: list[str] = []
    claims = context.claims if isinstance(context.claims, Mapping) else {}
    for key in _TENANT_CLAIM_KEYS:
        values.extend(_split_values(claims.get(key)))
    if not values and context.token_kind != "service":
        values.extend(
            _split_values(os.getenv("PANTHEON_TELEMETRY_ALLOWED_TENANTS"))
        )
    return _dedupe(values)


def resolve_telemetry_authority(
    *,
    required_roles: Sequence[str],
) -> TelemetryAuthority:
    context = _authenticate(required_roles)
    tenant_id = _requested_tenant_id()
    allowed = _allowed_tenants(context)
    if not allowed:
        raise TelemetryAuthorityError(
            "TENANT_SCOPE_UNCONFIGURED",
            "Authenticated telemetry caller has no tenant authority",
            403,
        )
    if "*" not in allowed and tenant_id not in allowed:
        raise TelemetryAuthorityError(
            "TENANT_FORBIDDEN",
            f"Caller is not authorized for tenant {tenant_id!r}",
            403,
        )
    return TelemetryAuthority(
        actor_id=context.actor_id,
        roles=context.roles,
        tenant_id=tenant_id,
        token_kind=context.token_kind,
    )


def require_telemetry_authority(roles: Sequence[str]):
    """Authenticate one route and attach a single authorized tenant scope."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                authority = resolve_telemetry_authority(required_roles=roles)
            except TelemetryAuthorityError as exc:
                payload, status = exc.as_response()
                return jsonify(payload), status
            request._telemetry_authority = authority  # type: ignore[attr-defined]
            return func(*args, **kwargs)

        return wrapper

    return decorator


def request_tenant_id() -> str:
    authority = getattr(request, "_telemetry_authority", None)
    if not isinstance(authority, TelemetryAuthority):
        raise RuntimeError("Telemetry route authority was not resolved")
    return authority.tenant_id


def bind_event_tenant(
    event: Mapping[str, Any],
    tenant_id: str,
) -> dict[str, Any]:
    """Return a detached event bound to the authenticated request tenant."""

    normalized = dict(event)
    top_level = str(normalized.get("tenant_id") or "").strip()
    envelope = normalized.get("correlation_envelope")
    envelope_tenant = (
        str(envelope.get("tenant_id") or "").strip()
        if isinstance(envelope, Mapping)
        else ""
    )
    supplied = _dedupe((top_level, envelope_tenant))
    if any(value != tenant_id for value in supplied):
        raise TelemetryAuthorityError(
            "TENANT_PAYLOAD_MISMATCH",
            "Telemetry event tenant does not match the authenticated request tenant",
            403,
        )
    normalized["tenant_id"] = tenant_id
    return normalized
