"""Authenticated, tenant-bound inbound authority for the imitation loop routes.

L12-IMIT-001.  Before this module the direct policy-learning routes
(``shadow-eval-tick``, the ``worker/*`` claim, replay and restart surface, and
candidate readback) were unauthenticated and took their tenant scope straight
from the request body.  Anything able to reach the service port could schedule
work inside another tenant, claim that tenant's backlog, or read back a
candidate together with its dataset lineage.

This module makes those routes *trusted* and *tenant-bound*:

* every protected route requires a verified caller — a JWT validated by the
  shared :mod:`services.runtime_auth_inbound` validator, or an in-cluster
  service token presented by the scheduler sidecar;
* the tenant is taken from the ``X-Tenant-Id`` header and checked against the
  tenant authority carried by the verified token, never from the request body;
* a body or path that names a different tenant is rejected rather than
  silently re-bound, so a caller cannot smuggle a cross-tenant scope past the
  header check.

The policy-learning JWT settings are deliberately *not* inherited from
``PANTHEON_RUNTIME_*``: policy-learning is its own authority boundary and must
not become reachable because a runtime-manager secret happens to be present in
the environment.
"""

from __future__ import annotations

import hmac
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from services.runtime_auth_inbound import AuthContext, AuthError, validate_request_auth


TENANT_HEADER = "X-Tenant-Id"
TENANT_HEADER_ALIAS = "X-Pantheon-Tenant"

SERVICE_TOKEN_ENV = "POLICY_LEARNING_SERVICE_TOKEN"
SERVICE_TENANTS_ENV = "POLICY_LEARNING_SERVICE_TENANTS"
ALLOWED_ROLES_ENV = "POLICY_LEARNING_ALLOWED_ROLES"
DEFAULT_ALLOWED_ROLES = "policy-learning-service"

_TENANT_CLAIM_KEYS = (
    "allowed_tenants",
    "allowedTenants",
    "tenant_ids",
    "tenantIds",
    "tenants",
    "tenant_id",
    "tenantId",
)

# Every policy-learning JWT setting is namespaced.  A missing namespaced value
# removes the shared key entirely instead of falling through to the
# runtime-manager value.
_AUTH_ENV_MAP = {
    "PANTHEON_RUNTIME_JWT_SECRET": "POLICY_LEARNING_JWT_SECRET",
    "PANTHEON_RUNTIME_JWT_ISSUER": "POLICY_LEARNING_JWT_ISSUER",
    "PANTHEON_RUNTIME_JWT_AUDIENCE": "POLICY_LEARNING_JWT_AUDIENCE",
    "PANTHEON_RUNTIME_JWKS_URI": "POLICY_LEARNING_JWKS_URI",
    "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL": "POLICY_LEARNING_OIDC_DISCOVERY_URL",
    "PANTHEON_RUNTIME_OIDC_ISSUER": "POLICY_LEARNING_OIDC_ISSUER",
    "PANTHEON_RUNTIME_OIDC_AUDIENCE": "POLICY_LEARNING_OIDC_AUDIENCE",
    "PANTHEON_RUNTIME_ROLE_CLAIMS": "POLICY_LEARNING_ROLE_CLAIMS",
    "PANTHEON_RUNTIME_ROLE_MAP": "POLICY_LEARNING_ROLE_MAP",
    "PANTHEON_RUNTIME_ROLE_MAP_MODE": "POLICY_LEARNING_ROLE_MAP_MODE",
}


@dataclass(frozen=True)
class PolicyLearningAuthority:
    """One authenticated caller bound to exactly one tenant."""

    actor_id: str
    roles: frozenset[str]
    tenant_id: str
    token_kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "roles": sorted(self.roles),
            "tenant_id": self.tenant_id,
            "token_kind": self.token_kind,
        }


class PolicyLearningAuthorityError(RuntimeError):
    """Fail-closed inbound authority error carrying its HTTP status."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def to_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


def _clean(value: Any) -> str:
    return str(value or "").strip()


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
        values = []
        for item in value:
            values.extend(_split_values(item))
        return values
    return [_clean(value)]


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        clean = _clean(value)
        if clean and clean not in seen:
            seen.add(clean)
            ordered.append(clean)
    return ordered


def _auth_env() -> dict[str, str]:
    env = dict(os.environ)
    for shared_key, namespaced_key in _AUTH_ENV_MAP.items():
        if namespaced_key in os.environ:
            env[shared_key] = os.environ[namespaced_key]
        else:
            env.pop(shared_key, None)
    env["PANTHEON_RUNTIME_AUTH_MODE"] = os.getenv("POLICY_LEARNING_AUTH_MODE", "strict")
    # The shared validator grants ``operator`` to a verified JWT that carries no
    # role claim at all.  Policy-learning must fail closed instead: an operator
    # who wants a default role has to name it explicitly.
    env["PANTHEON_RUNTIME_DEFAULT_ROLE"] = os.getenv(
        "POLICY_LEARNING_DEFAULT_ROLE",
        "__policy_learning_role_required__",
    )
    return env


def allowed_roles() -> tuple[str, ...]:
    return tuple(_split_values(os.getenv(ALLOWED_ROLES_ENV, DEFAULT_ALLOWED_ROLES)))


def _service_context(authorization: str) -> Optional[AuthContext]:
    """Recognize the in-cluster service token used by the scheduler sidecar."""

    configured = _clean(os.getenv(SERVICE_TOKEN_ENV))
    if not configured or not authorization.startswith("Bearer "):
        return None
    presented = authorization.split(None, 1)[1].strip()
    if not hmac.compare_digest(configured, presented):
        return None
    return AuthContext(
        actor_id="policy-learning-service",
        roles=frozenset(allowed_roles()) or frozenset({DEFAULT_ALLOWED_ROLES}),
        claims={"allowed_tenants": _split_values(os.getenv(SERVICE_TENANTS_ENV))},
        token_kind="service",
    )


def _authenticate(authorization: str) -> AuthContext:
    required = allowed_roles()
    context = _service_context(authorization)
    if context is None:
        try:
            return validate_request_auth(
                authorization=authorization,
                required_roles=required,
                env=_auth_env(),
            )
        except AuthError as exc:
            raise PolicyLearningAuthorityError(exc.code, exc.message, exc.status_code) from exc
    if required and not context.has_role(*required):
        raise PolicyLearningAuthorityError(
            "AUTH_FORBIDDEN",
            f"Service token roles {sorted(context.roles)} are not authorized",
            403,
        )
    return context


def _requested_tenant(tenant_header: str, tenant_alias: str) -> str:
    primary = _clean(tenant_header)
    alias = _clean(tenant_alias)
    if primary and alias and primary != alias:
        raise PolicyLearningAuthorityError(
            "TENANT_HEADER_CONFLICT",
            f"{TENANT_HEADER} and {TENANT_HEADER_ALIAS} must match",
            400,
        )
    tenant_id = primary or alias
    if not tenant_id:
        raise PolicyLearningAuthorityError(
            "TENANT_REQUIRED",
            f"{TENANT_HEADER} is required for policy-learning imitation routes",
            400,
        )
    return tenant_id


def _allowed_tenants(context: AuthContext) -> list[str]:
    claims = context.claims if isinstance(context.claims, Mapping) else {}
    values: list[str] = []
    for key in _TENANT_CLAIM_KEYS:
        values.extend(_split_values(claims.get(key)))
    return _dedupe(values)


def resolve_authority(
    *,
    authorization: Optional[str],
    tenant_header: Optional[str],
    tenant_alias: Optional[str] = None,
) -> PolicyLearningAuthority:
    """Authenticate one request and bind it to a single authorized tenant."""

    context = _authenticate(authorization or "")
    tenant_id = _requested_tenant(tenant_header or "", tenant_alias or "")
    allowed = _allowed_tenants(context)
    if not allowed:
        raise PolicyLearningAuthorityError(
            "TENANT_SCOPE_UNCONFIGURED",
            "Authenticated policy-learning caller carries no tenant authority",
            403,
        )
    if "*" not in allowed and tenant_id not in allowed:
        raise PolicyLearningAuthorityError(
            "TENANT_FORBIDDEN",
            f"Caller is not authorized for tenant {tenant_id!r}",
            403,
        )
    return PolicyLearningAuthority(
        actor_id=context.actor_id,
        roles=context.roles,
        tenant_id=tenant_id,
        token_kind=context.token_kind,
    )


def bind_tenant(authority: PolicyLearningAuthority, *supplied: Any) -> str:
    """Return the authenticated tenant, rejecting any conflicting value.

    A body field that names a different tenant is a cross-tenant attempt, not a
    hint: it is refused instead of being quietly overwritten with the header
    tenant, so the caller cannot learn whether the other tenant exists.
    """

    for value in supplied:
        clean = _clean(value)
        if clean and clean != authority.tenant_id:
            raise PolicyLearningAuthorityError(
                "TENANT_PAYLOAD_MISMATCH",
                "Request tenant does not match the authenticated tenant scope",
                403,
            )
    return authority.tenant_id


def authority_configuration() -> dict[str, Any]:
    """Non-secret description of how inbound authority is configured."""

    env = _auth_env()
    mode = _clean(env.get("PANTHEON_RUNTIME_AUTH_MODE")).lower() or "strict"
    verifier_configured = bool(
        env.get("PANTHEON_RUNTIME_JWT_SECRET")
        or env.get("PANTHEON_RUNTIME_JWKS_URI")
        or env.get("PANTHEON_RUNTIME_OIDC_DISCOVERY_URL")
    )
    service_token_configured = bool(_clean(os.getenv(SERVICE_TOKEN_ENV)))
    return {
        "mode": mode,
        "jwt_verifier_configured": verifier_configured,
        "service_token_configured": service_token_configured,
        "allowed_roles": list(allowed_roles()),
        "tenant_header": TENANT_HEADER,
        "inherits_runtime_manager_secret": False,
        "configured": verifier_configured or service_token_configured,
    }


__all__ = [
    "ALLOWED_ROLES_ENV",
    "PolicyLearningAuthority",
    "PolicyLearningAuthorityError",
    "SERVICE_TENANTS_ENV",
    "SERVICE_TOKEN_ENV",
    "TENANT_HEADER",
    "TENANT_HEADER_ALIAS",
    "allowed_roles",
    "authority_configuration",
    "bind_tenant",
    "resolve_authority",
]
