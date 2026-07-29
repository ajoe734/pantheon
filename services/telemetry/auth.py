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

# Claims that may carry the infrastructure-health producer scope of a service
# JWT. The scope is an allowlist of producer identities the caller may emit as;
# it is intersected with the deployment allowlist, never unioned with it.
_PRODUCER_CLAIM_KEYS = (
    "allowed_producers",
    "allowedProducers",
    "telemetry_producers",
    "telemetryProducers",
    "producers",
    "producer",
)

# Infrastructure health is a machine-to-machine channel. Human operator and
# admin roles are deliberately not accepted here.
_INFRASTRUCTURE_HEALTH_ROLES = ("service",)


@dataclass(frozen=True)
class TelemetryAuthority:
    actor_id: str
    roles: frozenset[str]
    tenant_id: str
    token_kind: str
    # Only populated for the infrastructure health authority: the producer
    # identities this caller is allowed to emit as on this deployment.
    allowed_producers: frozenset[str] = frozenset()


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


# ---------------------------------------------------------------------------
# Infrastructure health authority (OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001)
# ---------------------------------------------------------------------------
#
# Infrastructure health telemetry is non-trading: it carries no RuntimeBinding
# and therefore cannot be validated against the authoritative binding store.
# Its only admission authority is the caller's identity, so that identity is
# held to a stricter standard than the rest of the telemetry surface:
#
#   * a verified service JWT is always required, even when the deployment runs
#     the rest of telemetry in permissive mode;
#   * the tenant must be bound by the token's own claims — the deployment-wide
#     PANTHEON_TELEMETRY_ALLOWED_TENANTS fallback does not apply here;
#   * the producer identity must be inside both the deployment allowlist and
#     the token's producer scope, and wildcards are refused on both sides.


def _infrastructure_auth_env() -> dict[str, str]:
    """Telemetry JWT settings pinned to strict mode for this channel."""

    env = _telemetry_auth_env()
    # A permissive telemetry rollout must never turn the non-trading
    # infrastructure channel into an unauthenticated ingest sink.
    env["PANTHEON_RUNTIME_AUTH_MODE"] = "strict"
    return env


def _token_tenants(context: AuthContext) -> list[str]:
    claims = context.claims if isinstance(context.claims, Mapping) else {}
    values: list[str] = []
    for key in _TENANT_CLAIM_KEYS:
        values.extend(_split_values(claims.get(key)))
    return _dedupe(values)


def _token_producers(context: AuthContext) -> list[str]:
    claims = context.claims if isinstance(context.claims, Mapping) else {}
    values: list[str] = []
    for key in _PRODUCER_CLAIM_KEYS:
        values.extend(_split_values(claims.get(key)))
    return _dedupe(values)


def configured_infrastructure_producers() -> list[str]:
    """Deployment-side allowlist of admissible infrastructure producers."""

    return _dedupe(_split_values(os.getenv("PANTHEON_TELEMETRY_INFRA_PRODUCERS")))


def resolve_infrastructure_health_authority() -> TelemetryAuthority:
    """Authenticate one infrastructure health request and bind its scope."""

    authorization = request.headers.get("Authorization", "")
    try:
        context = validate_request_auth(
            authorization=authorization,
            mfa_header=request.headers.get("X-MFA-Token", ""),
            required_roles=_INFRASTRUCTURE_HEALTH_ROLES,
            env=_infrastructure_auth_env(),
        )
    except AuthError as exc:
        raise TelemetryAuthorityError(
            exc.code,
            exc.message,
            exc.status_code,
        ) from exc

    if context.token_kind != "jwt":
        raise TelemetryAuthorityError(
            "INFRA_SERVICE_JWT_REQUIRED",
            "Infrastructure health ingestion requires a verified service JWT",
            401,
        )

    tenant_id = _requested_tenant_id()
    token_tenants = _token_tenants(context)
    if not token_tenants:
        raise TelemetryAuthorityError(
            "TENANT_SCOPE_UNCONFIGURED",
            "Infrastructure health service token carries no tenant authority",
            403,
        )
    if "*" in token_tenants:
        raise TelemetryAuthorityError(
            "TENANT_SCOPE_UNBOUNDED",
            "Infrastructure health service token must bind explicit tenants",
            403,
        )
    if tenant_id not in token_tenants:
        raise TelemetryAuthorityError(
            "TENANT_FORBIDDEN",
            f"Caller is not authorized for tenant {tenant_id!r}",
            403,
        )

    configured = configured_infrastructure_producers()
    token_producers = _token_producers(context)
    if not configured or not token_producers:
        raise TelemetryAuthorityError(
            "PRODUCER_SCOPE_UNCONFIGURED",
            "Infrastructure health ingestion requires an allowlisted producer scope",
            403,
        )
    if "*" in configured or "*" in token_producers:
        raise TelemetryAuthorityError(
            "PRODUCER_SCOPE_UNBOUNDED",
            "Infrastructure health producer scope must be an explicit allowlist",
            403,
        )
    allowed_producers = frozenset(configured).intersection(token_producers)
    if not allowed_producers:
        raise TelemetryAuthorityError(
            "PRODUCER_FORBIDDEN",
            "Service token producer scope is not allowlisted on this deployment",
            403,
        )

    return TelemetryAuthority(
        actor_id=context.actor_id,
        roles=context.roles,
        tenant_id=tenant_id,
        token_kind=context.token_kind,
        allowed_producers=allowed_producers,
    )


def require_infrastructure_health_authority():
    """Authenticate one infrastructure health route under strict service auth."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                authority = resolve_infrastructure_health_authority()
            except TelemetryAuthorityError as exc:
                payload, status = exc.as_response()
                return jsonify(payload), status
            request._telemetry_authority = authority  # type: ignore[attr-defined]
            return func(*args, **kwargs)

        return wrapper

    return decorator


def request_authority() -> TelemetryAuthority:
    authority = getattr(request, "_telemetry_authority", None)
    if not isinstance(authority, TelemetryAuthority):
        raise RuntimeError("Telemetry route authority was not resolved")
    return authority


def bind_event_producer(
    event: Mapping[str, Any],
    authority: TelemetryAuthority,
) -> dict[str, Any]:
    """Return a detached event whose producer is proven by the caller's scope."""

    normalized = dict(event)
    producer = str(normalized.get("producer") or "").strip()
    if not producer:
        raise TelemetryAuthorityError(
            "PRODUCER_REQUIRED",
            "Infrastructure health event must declare its producer",
            400,
        )
    if producer not in authority.allowed_producers:
        raise TelemetryAuthorityError(
            "PRODUCER_FORBIDDEN",
            f"Caller is not authorized to emit as producer {producer!r}",
            403,
        )
    normalized["producer"] = producer
    return normalized
