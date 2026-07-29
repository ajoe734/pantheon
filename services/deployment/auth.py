"""Shared authenticated actor and tenant boundary for deployment services.

The deployment and promotion APIs use the repository-wide inbound token
validator, but keep service-specific environment names.  A caller must present
both a verified bearer identity and an explicit tenant header.  When a JWT
contains tenant claims, the header must be one of those claims; structured
development tokens remain tenant-bound by the required header and persisted
owner records.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from services.runtime_auth_inbound import AuthContext, AuthError, validate_request_auth


AUTHENTICATED_SERVICE_ROLES = (
    "operator",
    "admin",
    "approver",
    "governance_reviewer",
    "risk_owner",
    "governance_committee",
    "automated_gate",
    "service",
    "deployment_consumer",
    "runtime_writer",
)


class TenantBoundaryError(ValueError):
    """Raised when a request has no authoritative tenant or crosses tenants."""

    def __init__(self, message: str, *, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class AuthenticatedTenant:
    actor_id: str
    roles: frozenset[str]
    tenant_id: str
    auth: AuthContext


def _first_env(prefix: str, suffix: str, fallback_suffix: str) -> str:
    return (
        os.getenv(f"PANTHEON_{prefix}_{suffix}")
        or os.getenv(f"PANTHEON_BFF_{suffix}")
        or os.getenv(f"PANTHEON_RUNTIME_{fallback_suffix}")
        or ""
    ).strip()


def service_auth_env(prefix: str) -> dict[str, str]:
    """Map a service-specific auth configuration onto the shared validator."""

    normalized = str(prefix or "").strip().upper()
    return {
        "PANTHEON_RUNTIME_AUTH_MODE": (
            _first_env(normalized, "AUTH_MODE", "AUTH_MODE") or "permissive"
        ),
        "PANTHEON_RUNTIME_JWT_SECRET": _first_env(
            normalized, "JWT_SECRET", "JWT_SECRET"
        ),
        "PANTHEON_RUNTIME_JWT_ISSUER": _first_env(
            normalized, "JWT_ISSUER", "JWT_ISSUER"
        ),
        "PANTHEON_RUNTIME_JWT_AUDIENCE": _first_env(
            normalized, "JWT_AUDIENCE", "JWT_AUDIENCE"
        ),
        "PANTHEON_RUNTIME_DEFAULT_ROLE": (
            _first_env(normalized, "DEFAULT_ROLE", "DEFAULT_ROLE") or "operator"
        ),
        "PANTHEON_RUNTIME_MFA_REQUIRED": (
            _first_env(normalized, "MFA_REQUIRED", "MFA_REQUIRED") or "false"
        ),
        "PANTHEON_RUNTIME_JWKS_URI": _first_env(
            normalized, "JWKS_URI", "JWKS_URI"
        ),
        "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL": _first_env(
            normalized, "OIDC_DISCOVERY_URL", "OIDC_DISCOVERY_URL"
        ),
        "PANTHEON_RUNTIME_OIDC_ISSUER": _first_env(
            normalized, "OIDC_ISSUER", "OIDC_ISSUER"
        ),
        "PANTHEON_RUNTIME_OIDC_AUDIENCE": _first_env(
            normalized, "OIDC_AUDIENCE", "OIDC_AUDIENCE"
        ),
        "PANTHEON_RUNTIME_ROLE_CLAIMS": _first_env(
            normalized, "ROLE_CLAIMS", "ROLE_CLAIMS"
        ),
        "PANTHEON_RUNTIME_ROLE_MAP": _first_env(
            normalized, "ROLE_MAP", "ROLE_MAP"
        ),
        "PANTHEON_RUNTIME_ROLE_MAP_MODE": _first_env(
            normalized, "ROLE_MAP_MODE", "ROLE_MAP_MODE"
        ),
        "PANTHEON_RUNTIME_MFA_CLAIMS": _first_env(
            normalized, "MFA_CLAIMS", "MFA_CLAIMS"
        ),
        "PANTHEON_RUNTIME_MFA_VALUES": _first_env(
            normalized, "MFA_VALUES", "MFA_VALUES"
        ),
        "PANTHEON_RUNTIME_REQUIRE_EMAIL_VERIFIED": (
            _first_env(
                normalized,
                "REQUIRE_EMAIL_VERIFIED",
                "REQUIRE_EMAIL_VERIFIED",
            )
            or "false"
        ),
    }


def _claim_values(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return {str(item).strip() for item in value if str(item).strip()}
    return {str(value).strip()} if str(value).strip() else set()


def claimed_tenants(claims: Mapping[str, Any]) -> set[str]:
    tenants: set[str] = set()
    for field in ("tenant_id", "tenant", "tenant_ids", "tenants"):
        tenants.update(_claim_values(claims.get(field)))
    return tenants


def authenticate_tenant(
    *,
    authorization: str | None,
    tenant_id: str | None,
    service_prefix: str,
    required_roles: Sequence[str] = AUTHENTICATED_SERVICE_ROLES,
    mfa_header: str | None = None,
    mfa_required: bool = False,
) -> AuthenticatedTenant:
    """Authenticate the actor and bind the request to one explicit tenant."""

    auth = validate_request_auth(
        authorization=authorization,
        mfa_header=mfa_header,
        required_roles=required_roles,
        mfa_required=mfa_required,
        env=service_auth_env(service_prefix),
    )
    normalized_tenant = str(tenant_id or "").strip()
    if not normalized_tenant:
        raise TenantBoundaryError(
            "X-Tenant-Id is required for this service boundary.",
            status_code=400,
        )
    allowed_tenants = claimed_tenants(auth.claims)
    if allowed_tenants and normalized_tenant not in allowed_tenants:
        raise TenantBoundaryError(
            f"Authenticated actor is not authorized for tenant {normalized_tenant!r}."
        )
    return AuthenticatedTenant(
        actor_id=auth.actor_id,
        roles=auth.roles,
        tenant_id=normalized_tenant,
        auth=auth,
    )


__all__ = [
    "AUTHENTICATED_SERVICE_ROLES",
    "AuthError",
    "AuthenticatedTenant",
    "TenantBoundaryError",
    "authenticate_tenant",
    "claimed_tenants",
    "service_auth_env",
]
