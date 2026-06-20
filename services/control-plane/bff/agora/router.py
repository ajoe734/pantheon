"""Agora BFF router factory.

create_agora_router() assembles all Agora sub-routers and is included by main.py
via app.include_router(create_agora_router(...)).  Actual handlers are stubs that
return HTTP 501 until downstream AG-BE-ID-* / AG-BE-SW-* tasks fill them.

Route ownership per capability_manifest.json (frozen AG-XR-001):
  /bff/agora/me              → identity   (agora.identity.v1)
  /bff/agora/capabilities    → identity   (agora.identity.v1)
  Sub-router boundaries → see each sub-module's router.py
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Header, HTTPException

from .models import (
    AgoraCapabilityScope,
    AgoraEnvelope,
    AgoraMeta,
)
from .identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from .identity.router import create_identity_router
from .servant.router import create_servant_router
from .strategy_workshop.router import create_strategy_workshop_router
from .research.router import create_research_router
from .trading_room.router import create_trading_room_router
from .dashboard.router import create_dashboard_router
from .shadow.router import create_shadow_router
from .personalization.router import create_personalization_router
from .management_projection.router import create_management_projection_router


_CAPABILITY_MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..", "specs", "agora", "capability_manifest.json",
)


def _load_capability_manifest() -> Dict[str, Any]:
    try:
        with open(_CAPABILITY_MANIFEST_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"capabilities": [], "manifest_version": "1.0", "source": "unavailable"}


def _raise_scope_error(exc: AgoraScopeResolutionError, bff_error: Callable[..., HTTPException]) -> None:
    from models import ErrorCode  # BFF top-level models; available via sys.path in runtime

    code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
    raise bff_error(
        exc.status_code,
        code,
        exc.message,
        exc.reason,
        precondition_failed="agora_user_scope",
        details_extra=exc.details,
    )


def create_agora_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    """Return the Agora top-level APIRouter.

    Mount with:  app.include_router(create_agora_router(...))
    """
    router = APIRouter(tags=["agora"])

    # ------------------------------------------------------------------ #
    # GET /bff/agora/me  — operator identity and capability scope (§18 envelope)
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/me")
    def agora_me(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Return the operator's Agora identity scope and audience-filtered capabilities."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            scope = resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id or x_pantheon_tenant,
            )
        except AgoraScopeResolutionError as exc:
            _raise_scope_error(exc, bff_error)
        envelope = AgoraEnvelope(
            data=scope.model_dump(),
            meta=AgoraMeta(
                snapshot_at=utc_now(),
                capability="agora.identity.v1",
                audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            ),
        )
        return envelope.model_dump()

    # ------------------------------------------------------------------ #
    # GET /bff/agora/capabilities  — frozen capability manifest
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/capabilities")
    def agora_capabilities(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Return the frozen Agora v1 capability manifest filtered by audience."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        manifest = _load_capability_manifest()
        try:
            scope = resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id or x_pantheon_tenant,
            )
        except AgoraScopeResolutionError as exc:
            _raise_scope_error(exc, bff_error)
        caps = scope.granted_capabilities
        allowed = {c["name"] for c in manifest.get("capabilities", [])} & set(caps)
        manifest_filtered = {
            **manifest,
            "capabilities": [
                c for c in manifest.get("capabilities", []) if c["name"] in allowed
            ],
            "scope": {
                "scope_id": scope.scope_id,
                "tenant_id": scope.tenant_id,
                "user_id": scope.user_id,
                "read_predicate": scope.read_predicate.model_dump(),
            },
        }
        return {
            "data": manifest_filtered,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.identity.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # ------------------------------------------------------------------ #
    # Include sub-routers
    # ------------------------------------------------------------------ #
    _kw: Dict[str, Any] = dict(
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        bff_error=bff_error,
        utc_now=utc_now,
    )
    router.include_router(create_identity_router(**_kw))
    router.include_router(create_servant_router(**_kw))
    router.include_router(create_strategy_workshop_router(**_kw))
    router.include_router(create_research_router(**_kw))
    router.include_router(create_trading_room_router(**_kw))
    router.include_router(create_dashboard_router(**_kw))
    router.include_router(create_shadow_router(**_kw))
    router.include_router(create_personalization_router(**_kw))
    router.include_router(create_management_projection_router(**_kw))

    return router
