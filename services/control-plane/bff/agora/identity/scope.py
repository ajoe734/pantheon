"""Agora identity scope helpers.

AG-BE-ID-001 keeps Agora user-private filtering in the BFF/backend layer.
Frontend filtering may narrow presentation, but a private read model must
first pass the tenant_id + user_id predicate built here.
"""
from __future__ import annotations

import json
import os
import re
import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from ..models import (
    AGORA_CAPABILITIES,
    AGORA_REQUIRED_ROLES,
    AgoraCapabilityScope,
    AgoraReadPredicate,
    AgoraServantPolicy,
)


_TENANT_CLAIM_PATHS = [
    "tenant_id",
    "tenantId",
    "tenant.id",
    "tid",
    "org_id",
    "organization.id",
]
_ALLOWED_TENANT_CLAIM_PATHS = [
    "allowed_tenants",
    "allowedTenants",
    "tenant_ids",
    "tenantIds",
    "tenants",
    *_TENANT_CLAIM_PATHS,
]
_USER_CLAIM_PATHS = ["user_id", "userId", "sub", "operator_id", "operatorId"]
_CAPABILITY_CLAIM_PATHS = ["capabilities", "permissions", "scp", "scope"]
_AGORA_POLICY_REFS = [
    "docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md#5.1",
    "docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/C2_persona_schema_normalization_plan.md",
]


class AgoraScopeResolutionError(ValueError):
    """Raised when an Agora user-private scope cannot be built safely."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        status_code: int = 403,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.status_code = status_code
        self.details = details or {}


def _split_values(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[\s,]+", value) if part.strip()]
    if isinstance(value, dict):
        values: List[str] = []
        for key in ("id", "tenant_id", "tenantId", "user_id", "userId", "value", "name"):
            values.extend(_split_values(value.get(key)))
        return values
    if isinstance(value, (list, tuple, set)):
        values: List[str] = []
        for item in value:
            values.extend(_split_values(item))
        return values
    return [str(value).strip()]


def _claim_value(claims: Dict[str, Any], path: str) -> Any:
    cursor: Any = claims
    for part in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


def _claim_strings(claims: Dict[str, Any], paths: Iterable[str]) -> List[str]:
    values: List[str] = []
    for path in paths:
        values.extend(_split_values(_claim_value(claims, path)))
    return _dedupe_nonblank(values)


def _dedupe_nonblank(values: Iterable[Any]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _first_nonblank(*values: Any) -> str:
    for value in values:
        clean = str(value or "").strip()
        if clean:
            return clean
    return ""


def _env_csv(name: str) -> List[str]:
    return _split_values(os.getenv(name, ""))


def _claims(identity: Any) -> Dict[str, Any]:
    claims = getattr(identity, "claims", {})
    return claims if isinstance(claims, dict) else {}


def _expires_at_from_claims(claims: Dict[str, Any]) -> Optional[str]:
    raw = claims.get("exp")
    try:
        epoch = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_scope_id(*, tenant_id: str, user_id: str, operator_id: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}\0{user_id}\0{operator_id}".encode("utf-8")).hexdigest()[:16]
    return f"agora-scope-{digest}"


def _agora_capabilities_for_roles(roles: Iterable[str]) -> List[str]:
    if AGORA_REQUIRED_ROLES.intersection(set(roles)):
        return list(AGORA_CAPABILITIES)
    return []


def _agora_capabilities_from_claims(claims: Dict[str, Any]) -> List[str]:
    allowed = set(AGORA_CAPABILITIES)
    return [cap for cap in _claim_strings(claims, _CAPABILITY_CLAIM_PATHS) if cap in allowed]


def resolve_agora_user_scope(
    identity: Any,
    *,
    utc_now: Callable[[], str],
    requested_tenant_id: Optional[str] = None,
) -> AgoraCapabilityScope:
    """Build an immutable Agora scope for this request.

    The returned scope always contains non-empty tenant_id and user_id values.
    Requested tenant overrides are accepted only when the auth claims or BFF env
    list them in the caller's allowed tenant set.
    """
    claims = _claims(identity)
    operator_id = str(getattr(identity, "operator_id", "") or "").strip()
    if not operator_id:
        raise AgoraScopeResolutionError(
            "Agora scope requires an authenticated operator",
            reason="AGORA_SCOPE_OPERATOR_MISSING",
            status_code=401,
        )

    user_id = _first_nonblank(*_claim_strings(claims, _USER_CLAIM_PATHS), operator_id)
    default_tenant = _first_nonblank(
        os.getenv("PANTHEON_BFF_TENANT_ID"),
        os.getenv("PANTHEON_BFF_DEFAULT_TENANT_ID"),
        os.getenv("PANTHEON_TENANT_ID"),
        *_claim_strings(claims, _TENANT_CLAIM_PATHS),
        "pantheon-dev",
    )
    allowed_tenants = _claim_strings(claims, _ALLOWED_TENANT_CLAIM_PATHS)
    if not allowed_tenants:
        allowed_tenants = _env_csv("PANTHEON_BFF_ALLOWED_TENANTS") or [default_tenant]
    tenant_id = _first_nonblank(requested_tenant_id, default_tenant)

    if not tenant_id or not user_id:
        raise AgoraScopeResolutionError(
            "Agora scope requires tenant_id and user_id",
            reason="AGORA_SCOPE_PREDICATE_MISSING",
            status_code=403,
            details={"tenant_id_present": bool(tenant_id), "user_id_present": bool(user_id)},
        )
    if "*" not in allowed_tenants and tenant_id not in allowed_tenants:
        raise AgoraScopeResolutionError(
            "Tenant access denied for Agora scope",
            reason="AGORA_SCOPE_TENANT_DENIED",
            status_code=403,
            details={"tenantId": tenant_id, "allowedTenantIds": allowed_tenants},
        )

    roles = list(getattr(identity, "roles", []) or [])
    granted_capabilities = _dedupe_nonblank(
        [
            *_agora_capabilities_for_roles(roles),
            *_agora_capabilities_from_claims(claims),
        ]
    )
    now = utc_now()
    return AgoraCapabilityScope(
        scope_id=_stable_scope_id(tenant_id=tenant_id, user_id=user_id, operator_id=operator_id),
        tenant_id=tenant_id,
        user_id=user_id,
        operator_id=operator_id,
        granted_capabilities=granted_capabilities,
        capabilities=granted_capabilities,
        roles=roles,
        surfaces=["agora"],
        read_predicate=AgoraReadPredicate(tenant_id=tenant_id, user_id=user_id),
        servant_policy=AgoraServantPolicy(),
        created_at=now,
        expires_at=_expires_at_from_claims(claims),
        policy_refs=list(_AGORA_POLICY_REFS),
        metadata={
            "auth_mode": str(getattr(identity, "token_kind", "") or ""),
            "allowed_tenant_ids": allowed_tenants,
        },
    )


_RECORD_TENANT_KEYS = (
    "tenant_id",
    "tenantId",
    "tenant",
    "tenant_ref",
    "tenantRef",
    "org_id",
    "orgId",
    "organization_id",
    "organizationId",
    "workspace_id",
    "workspaceId",
)
_RECORD_USER_KEYS = (
    "user_id",
    "userId",
    "agora_user_id",
    "agoraUserId",
    "operator_id",
    "operatorId",
    "owner_id",
    "ownerId",
    "author_id",
    "authorId",
    "created_by",
    "createdBy",
)
_RECORD_NESTED_KEYS = (
    "metadata",
    "scope",
    "owner_ref",
    "ownerRef",
    "sender",
    "author_ref",
    "authorRef",
    "created_by",
    "createdBy",
)


def _record_scope_values(record: Dict[str, Any], keys: Iterable[str]) -> List[str]:
    values: List[str] = []
    for key in keys:
        if key in record:
            values.extend(_split_values(record.get(key)))
    for key in _RECORD_NESTED_KEYS:
        nested = record.get(key)
        if isinstance(nested, dict):
            values.extend(_record_scope_values(nested, keys))
    return _dedupe_nonblank(values)


def agora_read_predicate(scope: AgoraCapabilityScope) -> Dict[str, str]:
    """Return the mandatory backend predicate or raise if it is incomplete."""
    tenant_id = str(scope.tenant_id or "").strip()
    user_id = str(scope.user_id or "").strip()
    if not tenant_id or not user_id:
        raise AgoraScopeResolutionError(
            "Agora read predicate is incomplete",
            reason="AGORA_SCOPE_PREDICATE_MISSING",
            status_code=403,
            details={"tenant_id_present": bool(tenant_id), "user_id_present": bool(user_id)},
        )
    return {"tenant_id": tenant_id, "user_id": user_id}


def agora_record_matches_user_scope(record: Dict[str, Any], scope: AgoraCapabilityScope) -> bool:
    """Fail-closed predicate for user-private Agora read model records."""
    predicate = agora_read_predicate(scope)
    tenant_values = _record_scope_values(record, _RECORD_TENANT_KEYS)
    user_values = _record_scope_values(record, _RECORD_USER_KEYS)
    if not tenant_values or not user_values:
        return False
    return predicate["tenant_id"] in tenant_values and predicate["user_id"] in user_values


def filter_agora_user_records(
    records: Iterable[Dict[str, Any]],
    scope: AgoraCapabilityScope,
) -> List[Dict[str, Any]]:
    """Return only records that match the mandatory tenant_id + user_id scope."""
    return [
        json.loads(json.dumps(record))
        for record in records
        if isinstance(record, dict) and agora_record_matches_user_scope(record, scope)
    ]
