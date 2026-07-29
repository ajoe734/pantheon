"""Authenticated service/tenant authority for the Persona Teaching API."""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from services.runtime_auth_inbound import (
    AuthContext,
    AuthError,
    has_claim_bound_mfa,
    validate_request_auth,
)


_AUTHORITY_CONTEXT: ContextVar[Optional["TrainingInboundAuthority"]] = ContextVar(
    "training_session_inbound_authority",
    default=None,
)
_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_MFA_PATH_SUFFIXES = ("/commit", "/discard")
_TENANT_CLAIMS = (
    "allowed_tenants",
    "allowedTenants",
    "tenant_ids",
    "tenantIds",
    "tenants",
    "tenant_id",
    "tenantId",
    "tenant.id",
    "tid",
    "org_id",
)
_SERVICE_CLAIMS = (
    "service",
    "service_id",
    "serviceId",
    "client_id",
    "azp",
    "sub",
)


class TrainingInboundAuthorityError(RuntimeError):
    """Fail-closed inbound authority error suitable for an HTTP response."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }


@dataclass(frozen=True)
class TrainingInboundAuthority:
    actor_id: str
    actor_service: str
    tenant_id: str
    roles: frozenset[str]
    mfa_verified: bool
    token_kind: str
    delegated_actor_id: Optional[str] = None

    def actor_context(self) -> dict[str, Any]:
        return {
            "authenticated_actor_id": self.actor_id,
            "actor_service": self.actor_service,
            "delegated_actor_id": self.delegated_actor_id,
            "roles": sorted(self.roles),
            "tenant_id": self.tenant_id,
            "mfa_verified": self.mfa_verified,
            "token_kind": self.token_kind,
        }


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _csv(value: Any) -> list[str]:
    return [item.strip() for item in _clean(value).split(",") if item.strip()]


def _bool_env(name: str, *, default: bool) -> bool:
    value = _clean(os.getenv(name))
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _claim_value(claims: Mapping[str, Any], path: str) -> Any:
    current: Any = claims
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _claim_strings(claims: Mapping[str, Any], paths: Sequence[str]) -> list[str]:
    values: list[str] = []
    for path in paths:
        value = _claim_value(claims, path)
        if value is None:
            continue
        if isinstance(value, str):
            candidates = value.split(",")
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
            candidates = value
        else:
            candidates = [value]
        for candidate in candidates:
            clean = _clean(candidate)
            if clean and clean not in values:
                values.append(clean)
    return values


def _auth_env() -> dict[str, str]:
    return {
        "PANTHEON_RUNTIME_AUTH_MODE": (
            os.getenv("TRAINING_SESSION_AUTH_MODE")
            or os.getenv("PANTHEON_RUNTIME_AUTH_MODE")
            or "strict"
        ),
        "PANTHEON_RUNTIME_JWT_SECRET": (
            os.getenv("TRAINING_SESSION_JWT_SECRET")
            or os.getenv("PANTHEON_RUNTIME_JWT_SECRET")
            or ""
        ),
        "PANTHEON_RUNTIME_JWT_ISSUER": (
            os.getenv("TRAINING_SESSION_JWT_ISSUER")
            or os.getenv("PANTHEON_RUNTIME_JWT_ISSUER")
            or ""
        ),
        "PANTHEON_RUNTIME_JWT_AUDIENCE": (
            os.getenv("TRAINING_SESSION_JWT_AUDIENCE")
            or os.getenv("PANTHEON_RUNTIME_JWT_AUDIENCE")
            or ""
        ),
        "PANTHEON_RUNTIME_JWKS_URI": (
            os.getenv("TRAINING_SESSION_JWKS_URI")
            or os.getenv("PANTHEON_RUNTIME_JWKS_URI")
            or ""
        ),
        "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL": (
            os.getenv("TRAINING_SESSION_OIDC_DISCOVERY_URL")
            or os.getenv("PANTHEON_RUNTIME_OIDC_DISCOVERY_URL")
            or ""
        ),
        "PANTHEON_RUNTIME_OIDC_ISSUER": (
            os.getenv("TRAINING_SESSION_OIDC_ISSUER")
            or os.getenv("PANTHEON_RUNTIME_OIDC_ISSUER")
            or ""
        ),
        "PANTHEON_RUNTIME_OIDC_AUDIENCE": (
            os.getenv("TRAINING_SESSION_OIDC_AUDIENCE")
            or os.getenv("PANTHEON_RUNTIME_OIDC_AUDIENCE")
            or ""
        ),
        "PANTHEON_RUNTIME_ROLE_CLAIMS": (
            os.getenv("TRAINING_SESSION_ROLE_CLAIMS")
            or os.getenv("PANTHEON_RUNTIME_ROLE_CLAIMS")
            or ""
        ),
        "PANTHEON_RUNTIME_ROLE_MAP": (
            os.getenv("TRAINING_SESSION_ROLE_MAP")
            or os.getenv("PANTHEON_RUNTIME_ROLE_MAP")
            or ""
        ),
        "PANTHEON_RUNTIME_ROLE_MAP_MODE": (
            os.getenv("TRAINING_SESSION_ROLE_MAP_MODE")
            or os.getenv("PANTHEON_RUNTIME_ROLE_MAP_MODE")
            or ""
        ),
        "PANTHEON_RUNTIME_MFA_CLAIMS": (
            os.getenv("TRAINING_SESSION_MFA_CLAIMS")
            or os.getenv("PANTHEON_RUNTIME_MFA_CLAIMS")
            or ""
        ),
        "PANTHEON_RUNTIME_MFA_VALUES": (
            os.getenv("TRAINING_SESSION_MFA_VALUES")
            or os.getenv("PANTHEON_RUNTIME_MFA_VALUES")
            or ""
        ),
        "PANTHEON_RUNTIME_MFA_REQUIRED": (
            os.getenv("TRAINING_SESSION_MFA_REQUIRED")
            or os.getenv("PANTHEON_RUNTIME_MFA_REQUIRED")
            or "true"
        ),
    }


def authority_configuration_health(*, persistence_enforced: bool) -> dict[str, Any]:
    disabled = _bool_env("TRAINING_SESSION_AUTH_DISABLED", default=False)
    if disabled:
        return {
            "status": "error" if persistence_enforced else "ok",
            "mode": "disabled",
            "configured": False,
            "reason": "auth_disabled",
            "test_override": not persistence_enforced,
        }
    env = _auth_env()
    mode = env["PANTHEON_RUNTIME_AUTH_MODE"].strip().lower()
    verifier_configured = bool(
        env["PANTHEON_RUNTIME_JWT_SECRET"]
        or env["PANTHEON_RUNTIME_JWKS_URI"]
        or env["PANTHEON_RUNTIME_OIDC_DISCOVERY_URL"]
    )
    if mode != "permissive" and not verifier_configured:
        return {
            "status": "error",
            "mode": mode or "strict",
            "configured": False,
            "reason": "jwt_verifier_missing",
        }
    return {
        "status": "ok",
        "mode": mode,
        "configured": True,
        "mfa_required": env["PANTHEON_RUNTIME_MFA_REQUIRED"].lower() == "true",
        "allowed_services": _csv(
            os.getenv(
                "TRAINING_SESSION_ALLOWED_CALLER_SERVICES",
                "control-plane-bff,training-session-preview-worker",
            )
        ),
    }


def _disabled_test_authority(
    *,
    tenant_id: Optional[str],
    actor_service: Optional[str],
    persistence_enforced: bool,
) -> Optional[TrainingInboundAuthority]:
    if not _bool_env("TRAINING_SESSION_AUTH_DISABLED", default=False):
        return None
    if persistence_enforced:
        raise TrainingInboundAuthorityError(
            "AUTH_DISABLED_FORBIDDEN",
            "Training-session auth cannot be disabled in an enforced persistence posture",
            503,
        )
    return TrainingInboundAuthority(
        actor_id="training-session-test",
        actor_service=_clean(actor_service) or "training-session-test",
        tenant_id=_clean(tenant_id)
        or _clean(os.getenv("TRAINING_SESSION_TEST_TENANT_ID"))
        or "tenant-test",
        roles=frozenset({"training-service"}),
        mfa_verified=True,
        token_kind="test-disabled",
    )


def authenticate_training_request(
    *,
    authorization: Optional[str],
    mfa_token: Optional[str],
    tenant_id: Optional[str],
    actor_service: Optional[str],
    method: str,
    path: str,
    persistence_enforced: bool,
) -> TrainingInboundAuthority:
    """Authenticate and bind one request to its service actor and tenant."""

    disabled = _disabled_test_authority(
        tenant_id=tenant_id,
        actor_service=actor_service,
        persistence_enforced=persistence_enforced,
    )
    if disabled is not None:
        return disabled

    clean_tenant = _clean(tenant_id)
    if not clean_tenant:
        raise TrainingInboundAuthorityError(
            "TENANT_REQUIRED",
            "X-Tenant-Id is required",
            400,
        )
    clean_service = _clean(actor_service)
    if not clean_service:
        raise TrainingInboundAuthorityError(
            "ACTOR_SERVICE_REQUIRED",
            "X-Pantheon-Service is required",
            400,
        )
    allowed_services = set(
        _csv(
            os.getenv(
                "TRAINING_SESSION_ALLOWED_CALLER_SERVICES",
                "control-plane-bff,training-session-preview-worker",
            )
        )
    )
    if clean_service not in allowed_services:
        raise TrainingInboundAuthorityError(
            "ACTOR_SERVICE_FORBIDDEN",
            "Caller service is not authorized for Persona Teaching",
            403,
        )

    mutation = method.upper() in _MUTATION_METHODS
    mfa_required = mutation and any(path.rstrip("/").endswith(suffix) for suffix in _MFA_PATH_SUFFIXES)
    required_roles = tuple(
        _csv(os.getenv("TRAINING_SESSION_ALLOWED_ROLES", "training-service"))
    )
    auth_env = _auth_env()
    try:
        context: AuthContext = validate_request_auth(
            authorization=authorization,
            mfa_header=mfa_token,
            required_roles=required_roles,
            mfa_required=mfa_required,
            env=auth_env,
        )
    except AuthError as exc:
        raise TrainingInboundAuthorityError(exc.code, exc.message, exc.status_code) from exc
    authoritative_mfa = has_claim_bound_mfa(context, env=auth_env)
    if (
        mfa_required
        and auth_env["PANTHEON_RUNTIME_MFA_REQUIRED"].lower() == "true"
        and not authoritative_mfa
    ):
        raise TrainingInboundAuthorityError(
            "MFA_NOT_VERIFIED",
            "Verified caller token does not contain authoritative MFA proof",
            401,
        )

    bound_services = _claim_strings(context.claims, _SERVICE_CLAIMS)
    if context.token_kind == "structured":
        bound_services.append(context.actor_id)
    if clean_service not in bound_services:
        raise TrainingInboundAuthorityError(
            "ACTOR_SERVICE_MISMATCH",
            "X-Pantheon-Service does not match the verified token",
            403,
        )

    allowed_tenants = _claim_strings(context.claims, _TENANT_CLAIMS)
    if context.token_kind == "structured":
        allowed_tenants.extend(
            _csv(os.getenv("TRAINING_SESSION_PERMISSIVE_ALLOWED_TENANTS"))
        )
    if not allowed_tenants:
        raise TrainingInboundAuthorityError(
            "TENANT_CLAIM_REQUIRED",
            "Verified caller token does not contain tenant authority",
            403,
        )
    if "*" not in allowed_tenants and clean_tenant not in allowed_tenants:
        raise TrainingInboundAuthorityError(
            "TENANT_SCOPE_FORBIDDEN",
            "Requested tenant is outside the verified caller scope",
            403,
        )

    delegated_actor_id = _clean(
        context.claims.get("delegated_actor_id")
        or context.claims.get("operator_id")
        or context.claims.get("user_id")
    ) or None
    return TrainingInboundAuthority(
        actor_id=context.actor_id,
        actor_service=clean_service,
        tenant_id=clean_tenant,
        roles=context.roles,
        mfa_verified=authoritative_mfa,
        token_kind=context.token_kind,
        delegated_actor_id=delegated_actor_id,
    )


def set_current_authority(authority: TrainingInboundAuthority) -> Token[Optional[TrainingInboundAuthority]]:
    return _AUTHORITY_CONTEXT.set(authority)


def reset_current_authority(token: Token[Optional[TrainingInboundAuthority]]) -> None:
    _AUTHORITY_CONTEXT.reset(token)


def current_authority() -> TrainingInboundAuthority:
    authority = _AUTHORITY_CONTEXT.get()
    if authority is None:
        raise RuntimeError("training-session inbound authority context is unavailable")
    return authority


__all__ = [
    "TrainingInboundAuthority",
    "TrainingInboundAuthorityError",
    "authenticate_training_request",
    "authority_configuration_health",
    "current_authority",
    "reset_current_authority",
    "set_current_authority",
]
