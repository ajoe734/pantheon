"""Shared admission and failure envelopes for restored owner-backed views."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request

from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope

log = logging.getLogger(__name__)


def unavailable(owner: str, reason: str, status: int = 503) -> HTTPException:
    log.warning("Agora %s unavailable: %s", owner, reason)
    return HTTPException(status, detail={"status": "unavailable", "owner": owner, "reason": reason,
                                        "fallback_used": False})


def admit(request: Request, *, extract_identity, require_role, utc_now):
    identity = extract_identity(request.headers.get("Authorization"),
                                session_cookie=request.cookies.get("pantheon_session"))
    require_role(identity)
    try:
        scope = resolve_agora_user_scope(identity, utc_now=utc_now,
                                        requested_tenant_id=request.headers.get("X-Tenant-Id"))
    except AgoraScopeResolutionError as exc:
        raise unavailable("identity", exc.reason, exc.status_code) from exc
    return identity, scope


def envelope(data: Any, *, owner: str, empty_reason: str = "owner_has_no_records") -> dict:
    empty = data == []
    return {"status": "unavailable" if empty else "ok", "reason": empty_reason if empty else None,
            "owner": owner, "data": data, "fallback_used": False,
            "environment_ceiling": "paper", "execution_authority": "none"}
