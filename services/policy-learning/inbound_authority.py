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
  header check;
* under a staging or production persistence posture, no credential this
  repository publishes — the compose default, the ``.env.example`` placeholder,
  or any other unedited fill-me-in value — counts as configured, so a
  deployment that never minted its own secret fails closed rather than
  authenticating anyone holding a checkout.

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

from services.foundation.persistence_posture import validate_persistence_posture
from services.runtime_auth_inbound import AuthContext, AuthError, validate_request_auth


TENANT_HEADER = "X-Tenant-Id"
TENANT_HEADER_ALIAS = "X-Pantheon-Tenant"

SERVICE_TOKEN_ENV = "POLICY_LEARNING_SERVICE_TOKEN"
SERVICE_TENANTS_ENV = "POLICY_LEARNING_SERVICE_TENANTS"
ALLOWED_ROLES_ENV = "POLICY_LEARNING_ALLOWED_ROLES"
DEFAULT_ALLOWED_ROLES = "policy-learning-service"

# ``docker-compose.yml`` hands the API and the scheduler sidecar this value so
# the local product loop runs end to end without an operator having to mint a
# secret first.  Because it is published in the repository it is a development
# convenience and nothing more: under a staging or production persistence
# posture it is treated as *unconfigured*, so a deployment that forgot to set
# its own ``POLICY_LEARNING_SERVICE_TOKEN`` fails closed instead of accepting a
# credential anyone can read off GitHub.
LOCAL_DEV_SERVICE_TOKEN = "pantheon-local-policy-learning-service"

# ``.env.example`` ships this fill-me-in value for the same variable.  Copying
# the example file into a deployment without editing that line is the ordinary
# way a stack ends up running on a credential that is published in the
# repository, so it is refused on an enforced posture exactly like the compose
# default above.  The compose default at least still authenticates a developer
# laptop; this one authenticates nothing anywhere.
ENV_EXAMPLE_SERVICE_TOKEN = "replace-me-policy-learning-service-token"

# Every value for ``POLICY_LEARNING_SERVICE_TOKEN`` this repository publishes.
# ``tests/test_l12_imit_001_default_compose_loop.py`` pins this tuple to what
# the tracked ``docker-compose.yml`` and ``.env.example`` actually contain, so
# a newly published default cannot silently become an accepted deployment
# secret.
PUBLISHED_SERVICE_TOKENS = (LOCAL_DEV_SERVICE_TOKEN, ENV_EXAMPLE_SERVICE_TOKEN)

# Fill-me-in shapes an operator leaves behind when a template is copied but not
# completed.  Matched against the token lowercased with ``_``/whitespace folded
# to ``-``, so ``REPLACE_ME_TOKEN`` is refused as readily as the exact example
# value.  This is deliberately a *fail-closed* rule: a deployment whose real
# secret happens to start "example-" is told it is unconfigured, which is the
# safe direction to be wrong in.
_PLACEHOLDER_TOKEN_PREFIXES = (
    "replace-me",
    "replaceme",
    "change-me",
    "changeme",
    "placeholder",
    "example",
    "sample",
    "dummy",
    "todo",
    "your-",
)

# Tokens that are nothing but the name of the thing they are standing in for.
_PLACEHOLDER_TOKENS = frozenset(
    {"secret", "token", "password", "changeit", "test", "none", "unset", "tbd"}
)

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


def _enforced_posture() -> bool:
    """True when this process is running as a staging or production deployment.

    Resolved through the shared posture helper rather than a second reading of
    ``PANTHEON_PERSISTENCE_POSTURE`` here, so "this is a real deployment" has
    one definition across the service.
    """

    return validate_persistence_posture("policy-learning", require_object_store=False).enforced


def _normalized_token(value: str) -> str:
    return re.sub(r"[\s_]+", "-", value.strip().lower())


def is_published_service_token(value: Any) -> bool:
    """True when ``value`` is a credential anyone with a checkout already has.

    That covers both values this repository publishes verbatim
    (:data:`PUBLISHED_SERVICE_TOKENS`) and the fill-me-in shapes an operator
    leaves behind when a template is copied but never completed.  Such a value
    is not a weak secret, it is a *public* one, so on an enforced posture it is
    treated as no credential at all rather than as a credential to warn about.
    """

    token = _clean(value)
    if not token:
        return False
    published = False
    for known in PUBLISHED_SERVICE_TOKENS:
        # Compared without an early return so the check does not reveal which
        # published value was supplied.  Both are public, but the habit is the
        # one the rest of this module keeps.
        published |= hmac.compare_digest(token, known)
    if published:
        return True
    normalized = _normalized_token(token)
    if normalized in _PLACEHOLDER_TOKENS:
        return True
    return normalized.startswith(_PLACEHOLDER_TOKEN_PREFIXES)


def service_token() -> str:
    """The usable in-cluster service token, or ``""`` when there is none.

    Any repository-published or placeholder value is deliberately downgraded to
    "no token" on a staging or production posture: see
    :func:`is_published_service_token`.
    """

    configured = _clean(os.getenv(SERVICE_TOKEN_ENV))
    if configured and is_published_service_token(configured) and _enforced_posture():
        return ""
    return configured


def _service_context(authorization: str) -> Optional[AuthContext]:
    """Recognize the in-cluster service token used by the scheduler sidecar."""

    configured = service_token()
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
    resolved_token = service_token()
    service_token_configured = bool(resolved_token)
    if not resolved_token:
        token_scope = "none"
    elif hmac.compare_digest(resolved_token, LOCAL_DEV_SERVICE_TOKEN):
        token_scope = "local_dev_default"
    elif is_published_service_token(resolved_token):
        # Reachable only on a dev/lenient posture: an unedited template value is
        # still a working credential locally, but it is named for what it is so
        # a readiness check can see it before the deployment posture flips.
        token_scope = "published_placeholder"
    else:
        token_scope = "deployment_secret"
    return {
        "mode": mode,
        "jwt_verifier_configured": verifier_configured,
        "service_token_configured": service_token_configured,
        "service_token_scope": token_scope,
        "service_tenants_configured": bool(_split_values(os.getenv(SERVICE_TENANTS_ENV))),
        "allowed_roles": list(allowed_roles()),
        "tenant_header": TENANT_HEADER,
        "inherits_runtime_manager_secret": False,
        "configured": verifier_configured or service_token_configured,
    }


__all__ = [
    "ALLOWED_ROLES_ENV",
    "ENV_EXAMPLE_SERVICE_TOKEN",
    "LOCAL_DEV_SERVICE_TOKEN",
    "PUBLISHED_SERVICE_TOKENS",
    "PolicyLearningAuthority",
    "PolicyLearningAuthorityError",
    "SERVICE_TENANTS_ENV",
    "SERVICE_TOKEN_ENV",
    "TENANT_HEADER",
    "TENANT_HEADER_ALIAS",
    "allowed_roles",
    "authority_configuration",
    "bind_tenant",
    "is_published_service_token",
    "resolve_authority",
    "service_token",
]
