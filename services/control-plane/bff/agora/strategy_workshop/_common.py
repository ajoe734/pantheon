"""Shared internal primitives for the strategy_workshop route modules.

Not a public package API -- imported only by sibling modules inside
strategy_workshop/ (router.py, readiness.py, routes/*). Moved verbatim out
of router.py so the route-group modules and readiness.py can share one
implementation instead of duplicating it.
"""
from __future__ import annotations

import re
import uuid
from types import SimpleNamespace
from typing import Any, Callable, Dict

from fastapi import HTTPException


class _StrategyVersionProjectionError(RuntimeError):
    def __init__(self, reason: str, *, status_code: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def _parse_etag_lock_version(if_match: str, workshop_id: str) -> int:
    """Return the lock_version encoded in the ETag, or 0 if the header is malformed.

    Expected format: W/"workshop:{workshop_id}:v{N}"
    Returns 0 so a malformed header is guaranteed to conflict (lock_version >= 1).
    """
    prefix = f'W/"workshop:{workshop_id}:v'
    if if_match.startswith(prefix) and if_match.endswith('"'):
        try:
            return int(if_match[len(prefix):-1])
        except ValueError:
            pass
    return 0


def _raise_cross_user_forbidden(
    *,
    bff_error: Callable[..., HTTPException],
    resource: str,
    resource_id: str,
) -> None:
    from services.control_plane.bff.models import ErrorCode

    raise bff_error(
        403,
        ErrorCode.FORBIDDEN,
        "Agora resource is outside the current user scope",
        "CROSS_USER_ACCESS_FORBIDDEN",
        precondition_failed="agora_user_scope",
        details_extra={"resource": resource, "resource_id": resource_id},
    )


def _identity_for_scope(identity: Any) -> Any:
    """Normalize test-injected dict identities to the OperatorIdentity shape."""
    if not isinstance(identity, dict):
        return identity
    claims = identity.get("claims") if isinstance(identity.get("claims"), dict) else dict(identity)
    operator_id = (
        identity.get("operator_id")
        or identity.get("operatorId")
        or identity.get("sub")
        or claims.get("operator_id")
        or claims.get("operatorId")
        or claims.get("sub")
        or claims.get("user_id")
        or claims.get("userId")
        or ""
    )
    roles = identity.get("roles") or claims.get("roles") or ["operator"]
    if isinstance(roles, str):
        roles = [part.strip() for part in re.split(r"[\s,]+", roles) if part.strip()]
    return SimpleNamespace(
        operator_id=str(operator_id),
        roles=list(roles or []),
        claims=claims,
        token_kind=identity.get("token_kind", identity.get("tokenKind", "test")),
        mfa_verified=bool(
            identity.get("mfa_verified", identity.get("mfaVerified", False))
            or claims.get("mfa_verified", claims.get("mfaVerified", False))
        ),
    )


def _clean_optional(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != [] and item != {}
    }


def _safe_card_id(*parts: Any) -> str:
    raw = "_".join(str(part or "") for part in parts)
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return clean or uuid.uuid4().hex
