"""Authenticated actor, role, service, and tenant authority for Capital writes."""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from services.runtime_auth_inbound import AuthContext, AuthError, validate_request_auth


_AUTHORITY: ContextVar[Optional["CapitalInboundAuthority"]] = ContextVar(
    "capital_inbound_authority",
    default=None,
)
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
_SERVICE_CLAIMS = ("service", "service_id", "serviceId", "client_id", "azp", "sub")


class CapitalInboundAuthorityError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


@dataclass(frozen=True)
class CapitalInboundAuthority:
    actor_id: str
    actor_service: str
    tenant_id: str
    roles: frozenset[str]
    token_kind: str
    delegated_actor_id: Optional[str] = None


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
            cleaned = _clean(candidate)
            if cleaned and cleaned not in values:
                values.append(cleaned)
    return values


def _auth_env() -> dict[str, str]:
    return {
        "PANTHEON_RUNTIME_AUTH_MODE": (
            os.getenv("CAPITAL_AUTH_MODE")
            or os.getenv("PANTHEON_RUNTIME_AUTH_MODE")
            or "strict"
        ),
        "PANTHEON_RUNTIME_JWT_SECRET": (
            os.getenv("CAPITAL_JWT_SECRET")
            or os.getenv("PANTHEON_RUNTIME_JWT_SECRET")
            or ""
        ),
        "PANTHEON_RUNTIME_JWT_ISSUER": (
            os.getenv("CAPITAL_JWT_ISSUER")
            or os.getenv("PANTHEON_RUNTIME_JWT_ISSUER")
            or ""
        ),
        "PANTHEON_RUNTIME_JWT_AUDIENCE": (
            os.getenv("CAPITAL_JWT_AUDIENCE")
            or os.getenv("PANTHEON_RUNTIME_JWT_AUDIENCE")
            or ""
        ),
        "PANTHEON_RUNTIME_JWKS_URI": (
            os.getenv("CAPITAL_JWKS_URI")
            or os.getenv("PANTHEON_RUNTIME_JWKS_URI")
            or ""
        ),
        "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL": (
            os.getenv("CAPITAL_OIDC_DISCOVERY_URL")
            or os.getenv("PANTHEON_RUNTIME_OIDC_DISCOVERY_URL")
            or ""
        ),
        "PANTHEON_RUNTIME_OIDC_ISSUER": (
            os.getenv("CAPITAL_OIDC_ISSUER")
            or os.getenv("PANTHEON_RUNTIME_OIDC_ISSUER")
            or ""
        ),
        "PANTHEON_RUNTIME_OIDC_AUDIENCE": (
            os.getenv("CAPITAL_OIDC_AUDIENCE")
            or os.getenv("PANTHEON_RUNTIME_OIDC_AUDIENCE")
            or ""
        ),
        "PANTHEON_RUNTIME_ROLE_CLAIMS": (
            os.getenv("CAPITAL_ROLE_CLAIMS")
            or os.getenv("PANTHEON_RUNTIME_ROLE_CLAIMS")
            or ""
        ),
        "PANTHEON_RUNTIME_ROLE_MAP": (
            os.getenv("CAPITAL_ROLE_MAP")
            or os.getenv("PANTHEON_RUNTIME_ROLE_MAP")
            or ""
        ),
        "PANTHEON_RUNTIME_ROLE_MAP_MODE": (
            os.getenv("CAPITAL_ROLE_MAP_MODE")
            or os.getenv("PANTHEON_RUNTIME_ROLE_MAP_MODE")
            or ""
        ),
        "PANTHEON_RUNTIME_MFA_REQUIRED": "false",
    }


def authority_configuration_health(*, persistence_enforced: bool) -> dict[str, Any]:
    disabled = _bool_env("CAPITAL_AUTH_DISABLED", default=False)
    if disabled:
        return {
            "status": "error" if persistence_enforced else "ok",
            "mode": "disabled",
            "configured": False,
            "test_override": not persistence_enforced,
        }
    env = _auth_env()
    mode = env["PANTHEON_RUNTIME_AUTH_MODE"].strip().lower()
    verifier_configured = bool(
        env["PANTHEON_RUNTIME_JWT_SECRET"]
        or env["PANTHEON_RUNTIME_JWKS_URI"]
        or env["PANTHEON_RUNTIME_OIDC_DISCOVERY_URL"]
    )
    return {
        "status": "ok" if mode == "permissive" or verifier_configured else "error",
        "mode": mode,
        "configured": mode == "permissive" or verifier_configured,
        "allowed_services": _csv(
            os.getenv("CAPITAL_ALLOWED_CALLER_SERVICES", "control-plane-bff")
        ),
    }


def authenticate_capital_request(
    *,
    authorization: Optional[str],
    tenant_id: Optional[str],
    actor_service: Optional[str],
    persistence_enforced: bool,
) -> CapitalInboundAuthority:
    if _bool_env("CAPITAL_AUTH_DISABLED", default=False):
        if persistence_enforced:
            raise CapitalInboundAuthorityError(
                "AUTH_DISABLED_FORBIDDEN",
                "Capital auth cannot be disabled in an enforced persistence posture",
                503,
            )
        return CapitalInboundAuthority(
            actor_id="capital-test",
            actor_service=_clean(actor_service) or "capital-test",
            tenant_id=_clean(tenant_id) or "tenant-test",
            roles=frozenset(
                {
                    "capital.admin",
                    "persona.admin",
                    "operator",
                    "approver",
                    "reviewer",
                    "admin",
                    "risk_owner",
                }
            ),
            token_kind="test-disabled",
        )

    clean_tenant = _clean(tenant_id)
    if not clean_tenant:
        raise CapitalInboundAuthorityError(
            "TENANT_REQUIRED",
            "X-Tenant-Id is required for Capital mutations",
            400,
        )
    clean_service = _clean(actor_service)
    if not clean_service:
        raise CapitalInboundAuthorityError(
            "ACTOR_SERVICE_REQUIRED",
            "X-Pantheon-Service is required for Capital mutations",
            400,
        )
    allowed_services = set(
        _csv(os.getenv("CAPITAL_ALLOWED_CALLER_SERVICES", "control-plane-bff"))
    )
    if clean_service not in allowed_services:
        raise CapitalInboundAuthorityError(
            "ACTOR_SERVICE_FORBIDDEN",
            "Caller service is not authorized for Capital mutations",
            403,
        )
    allowed_roles = tuple(
        _csv(
            os.getenv(
                "CAPITAL_ALLOWED_ROLES",
                "capital.admin,persona.admin,operator,approver,reviewer,admin,risk_owner",
            )
        )
    )
    auth_env = _auth_env()
    try:
        context: AuthContext = validate_request_auth(
            authorization=authorization,
            required_roles=allowed_roles,
            mfa_required=False,
            env=auth_env,
        )
    except AuthError as exc:
        raise CapitalInboundAuthorityError(exc.code, exc.message, exc.status_code) from exc

    bound_services = _claim_strings(context.claims, _SERVICE_CLAIMS)
    if context.token_kind == "structured":
        bound_services.append(context.actor_id)
    if clean_service not in bound_services:
        raise CapitalInboundAuthorityError(
            "ACTOR_SERVICE_MISMATCH",
            "X-Pantheon-Service does not match the verified token",
            403,
        )
    allowed_tenants = _claim_strings(context.claims, _TENANT_CLAIMS)
    if context.token_kind == "structured":
        allowed_tenants.extend(_csv(os.getenv("CAPITAL_PERMISSIVE_ALLOWED_TENANTS")))
    if not allowed_tenants:
        raise CapitalInboundAuthorityError(
            "TENANT_CLAIM_REQUIRED",
            "Verified caller token does not contain tenant authority",
            403,
        )
    if "*" not in allowed_tenants and clean_tenant not in allowed_tenants:
        raise CapitalInboundAuthorityError(
            "TENANT_SCOPE_FORBIDDEN",
            "Requested tenant is outside the verified caller scope",
            403,
        )
    delegated_actor_id = _clean(
        context.claims.get("delegated_actor_id")
        or context.claims.get("operator_id")
        or context.claims.get("user_id")
    ) or None
    return CapitalInboundAuthority(
        actor_id=context.actor_id,
        actor_service=clean_service,
        tenant_id=clean_tenant,
        roles=context.roles,
        token_kind=context.token_kind,
        delegated_actor_id=delegated_actor_id,
    )


def bind_capital_mutation(body: Any) -> Any:
    authority = current_authority()
    if authority.token_kind == "test-disabled":
        return body
    declared_actor_id = _clean(getattr(body, "actor_id", None))
    trusted_actor_id = authority.delegated_actor_id or authority.actor_id
    if declared_actor_id and declared_actor_id != trusted_actor_id:
        raise CapitalInboundAuthorityError(
            "ACTOR_ID_MISMATCH",
            "Mutation actor_id does not match the authenticated actor",
            403,
        )
    declared_role = _clean(getattr(body, "actor_role", None))
    if not declared_role or declared_role not in authority.roles:
        raise CapitalInboundAuthorityError(
            "ACTOR_ROLE_MISMATCH",
            "Mutation actor_role is not granted by the verified token",
            403,
        )
    return body.model_copy(update={"actor_id": trusted_actor_id})


def set_current_authority(
    authority: CapitalInboundAuthority,
) -> Token[Optional[CapitalInboundAuthority]]:
    return _AUTHORITY.set(authority)


def reset_current_authority(token: Token[Optional[CapitalInboundAuthority]]) -> None:
    _AUTHORITY.reset(token)


def current_authority() -> CapitalInboundAuthority:
    authority = _AUTHORITY.get()
    if authority is None:
        raise RuntimeError("capital inbound authority context is unavailable")
    return authority
