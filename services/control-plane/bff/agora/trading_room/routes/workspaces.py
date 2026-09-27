"""Agora trading-room workspaces routes."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Cookie, Header, Query, Response

from .common import (
    TradingRoomRouteContext,
    _proposal_etag,
    _revision_proposal_etag,
    _version_etag,
    _workspace_etag,
)


def build_workspaces_router(ctx: TradingRoomRouteContext) -> APIRouter:
    """Trading-room workspaces, proposals, views, widgets, and layout subrouter."""
    router = APIRouter()

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room")
    def get_trading_room(
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        """Return the user-scoped Trading Room aggregate (TradingRoomAggregate)."""
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        return ctx.service.get_trading_room_aggregate(identity=identity)

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/strategies/{strategy_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/strategies/{strategy_id}")
    def get_trading_room_strategy(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> Dict[str, Any]:
        """Return per-strategy Trading Room detail (DetailEnvelope)."""
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        data, _counts = ctx.service.get_strategy_aggregate(strategy_id, identity)
        return {
            "object_ref": {"type": "trading_room_strategy", "id": strategy_id},
            "status": "active",
            "lifecycle_state": "monitoring",
            "allowedActions": {
                "record_decision": True,
                "submit_handoff": True,
                "request_shadow": True,
            },
            "meta": ctx._meta(),
            "links": {
                "decision_events": f"/bff/agora/trading-room/decision-events?strategy_id={strategy_id}",
            },
            "data": data,
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/strategies/{strategy_id}/trading-room/proposals
    # ------------------------------------------------------------------

    @router.post(
        "/bff/agora/strategies/{strategy_id}/trading-room/proposals",
        status_code=201,
    )
    def create_workspace_proposal(
        strategy_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        """Create a complete V11 TradingRoomWorkspaceProposal preview."""
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)

        proposal, meta = ctx.service.create_workspace_proposal(
            strategy_id=strategy_id,
            body=body or {},
            identity=identity,
            idempotency_key=idempotency_key,
        )
        etag = _proposal_etag(proposal)
        response.headers["ETag"] = etag
        return {
            "data": proposal,
            "meta": ctx._meta(etag=etag, strategy_id=strategy_id, generator=meta),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}")
    def get_workspace_proposal(
        strategy_id: str,
        proposal_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        proposal, generator_meta = ctx.service.get_workspace_proposal(
            strategy_id=strategy_id,
            proposal_id=proposal_id,
            identity=identity,
        )
        etag = _proposal_etag(proposal)
        response.headers["ETag"] = etag
        return {
            "data": proposal,
            "meta": ctx._meta(
                etag=etag,
                strategy_id=strategy_id,
                generator=generator_meta,
            ),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept
    # ------------------------------------------------------------------

    @router.post("/bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept")
    def accept_workspace_proposal(
        strategy_id: str,
        proposal_id: str,
        response: Response,
        body: Optional[Dict[str, Any]] = None,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)

        workspace, version = ctx.service.accept_workspace_proposal(
            strategy_id=strategy_id,
            proposal_id=proposal_id,
            body=body,
            identity=identity,
            idempotency_key=idempotency_key,
        )
        etag = _workspace_etag(workspace)
        response.headers["ETag"] = etag
        return {
            "data": {
                "workspaceId": workspace["id"],
                "workspace": workspace,
                "version": version,
            },
            "meta": ctx._meta(etag=etag, strategy_id=strategy_id, proposal_id=proposal_id),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/workspaces/lookup
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/workspaces/lookup")
    def lookup_trading_room_workspace(
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        strategy_id: Optional[str] = Query(default=None),
        strategy_version: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        workspace = ctx.service.lookup_workspace(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            identity=identity,
        )
        etag = _workspace_etag(workspace)
        response.headers["ETag"] = etag
        return {
            "data": workspace,
            "meta": ctx._meta(etag=etag, workspace_id=workspace["id"], strategy_id=strategy_id),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/strategies/{strategy_id}/workspace
    # GET /bff/agora/strategies/{strategy_id}/trading-room/workspace
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/strategies/{strategy_id}/workspace")
    @router.get("/bff/agora/strategies/{strategy_id}/trading-room/workspace")
    def get_strategy_workspace(
        strategy_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        version: Optional[str] = Query(default=None),
        strategy_version: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        target_version = version or strategy_version
        workspace = ctx.service.get_strategy_workspace(
            strategy_id=strategy_id,
            version=target_version,
            identity=identity,
        )
        etag = _workspace_etag(workspace)
        response.headers["ETag"] = etag
        return {
            "data": workspace,
            "meta": ctx._meta(etag=etag, workspace_id=workspace["id"], strategy_id=strategy_id),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/workspaces/{workspace_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/workspaces/{workspace_id}")
    def get_workspace(
        workspace_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        workspace = ctx.service.get_workspace(workspace_id=workspace_id, identity=identity)
        etag = _workspace_etag(workspace)
        response.headers["ETag"] = etag
        return {
            "data": workspace,
            "meta": ctx._meta(etag=etag, workspace_id=workspace_id),
        }

    # ------------------------------------------------------------------
    # PATCH /bff/agora/trading-room/workspaces/{workspace_id}/layout
    # ------------------------------------------------------------------

    @router.patch("/bff/agora/trading-room/workspaces/{workspace_id}/layout")
    def patch_workspace_layout(
        workspace_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)

        updated, version = ctx.service.update_workspace_layout(
            workspace_id=workspace_id,
            body=body,
            identity=identity,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": updated,
            "meta": ctx._meta(etag=etag, workspace_id=workspace_id, version_id=version["id"]),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-room/workspaces/{workspace_id}/views
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-room/workspaces/{workspace_id}/views", status_code=201)
    def add_workspace_view(
        workspace_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)

        updated, version = ctx.service.add_workspace_view(
            workspace_id=workspace_id,
            body=body,
            identity=identity,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": updated,
            "meta": ctx._meta(etag=etag, workspace_id=workspace_id, version_id=version["id"]),
        }

    # ------------------------------------------------------------------
    # PATCH /bff/agora/trading-room/workspaces/{workspace_id}/views/{view_id}
    # ------------------------------------------------------------------

    @router.patch("/bff/agora/trading-room/workspaces/{workspace_id}/views/{view_id}")
    def patch_workspace_view(
        workspace_id: str,
        view_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)

        updated, version = ctx.service.patch_workspace_view(
            workspace_id=workspace_id,
            view_id=view_id,
            body=body,
            identity=identity,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": updated,
            "meta": ctx._meta(etag=etag, workspace_id=workspace_id, version_id=version["id"]),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-room/workspaces/{workspace_id}/widgets
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-room/workspaces/{workspace_id}/widgets", status_code=201)
    def add_workspace_widget(
        workspace_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)

        updated, version = ctx.service.add_workspace_widget(
            workspace_id=workspace_id,
            body=body,
            identity=identity,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": updated,
            "meta": ctx._meta(etag=etag, workspace_id=workspace_id, version_id=version["id"]),
        }

    # ------------------------------------------------------------------
    # PATCH /bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}
    # ------------------------------------------------------------------

    @router.patch("/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}")
    def patch_workspace_widget(
        workspace_id: str,
        widget_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)

        updated, version = ctx.service.patch_workspace_widget(
            workspace_id=workspace_id,
            widget_id=widget_id,
            body=body,
            identity=identity,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": updated,
            "meta": ctx._meta(etag=etag, workspace_id=workspace_id, version_id=version["id"]),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}/revision-proposals
    # ------------------------------------------------------------------

    @router.post(
        "/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}/revision-proposals",
        status_code=201,
    )
    def create_widget_revision_proposal(
        workspace_id: str,
        widget_id: str,
        body: Dict[str, Any],
        response: Response,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)

        proposal = ctx.service.create_widget_revision_proposal(
            workspace_id=workspace_id,
            widget_id=widget_id,
            body=body,
            identity=identity,
            idempotency_key=idempotency_key,
        )
        etag = _revision_proposal_etag(proposal)
        response.headers["ETag"] = etag
        return {
            "data": proposal,
            "meta": ctx._meta(
                etag=etag,
                workspace_id=workspace_id,
                widget_id=widget_id,
                before_after_preview=True,
            ),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept")
    def accept_widget_revision_proposal(
        proposal_id: str,
        response: Response,
        body: Optional[Dict[str, Any]] = None,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)

        proposal, updated, version, applied_action, copied_widget_id = ctx.service.accept_widget_revision_proposal(
            proposal_id=proposal_id,
            body=body,
            identity=identity,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": {
                "proposal": proposal,
                "workspace": updated,
                "version": version,
                "appliedAction": applied_action,
                "copiedWidgetId": copied_widget_id,
            },
            "meta": ctx._meta(
                etag=etag,
                revision_proposal_etag=_revision_proposal_etag(proposal),
                workspace_id=proposal["workspaceId"],
                proposal_id=proposal_id,
                version_id=version["id"],
            ),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/trading-room/workspaces/{workspace_id}/versions
    # ------------------------------------------------------------------

    @router.get("/bff/agora/trading-room/workspaces/{workspace_id}/versions")
    def list_workspace_versions(
        workspace_id: str,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        versions = ctx.service.list_workspace_versions(workspace_id=workspace_id, identity=identity)
        return {
            "data": versions,
            "meta": ctx._meta(
                workspace_id=workspace_id,
                total=len(versions),
                latest_version_id=versions[-1]["id"] if versions else None,
            ),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/trading-room/workspaces/{workspace_id}/versions/{version_id}/rollback
    # ------------------------------------------------------------------

    @router.post("/bff/agora/trading-room/workspaces/{workspace_id}/versions/{version_id}/rollback")
    def rollback_workspace_version(
        workspace_id: str,
        version_id: str,
        response: Response,
        body: Optional[Dict[str, Any]] = None,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)

        updated, version, target = ctx.service.rollback_workspace_version(
            workspace_id=workspace_id,
            version_id=version_id,
            body=body,
            identity=identity,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        etag = _workspace_etag(updated)
        response.headers["ETag"] = etag
        return {
            "data": {
                "workspace": updated,
                "version": version,
                "rollbackOfVersion": target,
            },
            "meta": ctx._meta(
                etag=etag,
                workspace_id=workspace_id,
                version_id=version["id"],
                rollback_of_version_id=version_id,
                rollback_of_version_etag=_version_etag(target),
            ),
        }

    return router
