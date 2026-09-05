"""Agora trading-room workspaces routes."""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Response

from .common import (
    TradingRoomRouteContext,
    QueueSummary,
    RiskSummary,
    TradingDecisionEvent,
    TradingRoomAggregate,
    TradingRoomStrategy,
    _DATA_AVAILABILITY_VALUES,
    _VIEW_ALLOWED_FIELDS,
    _WIDGET_ALLOWED_FIELDS,
    _WINNER_BRANCH_VIEW_IDS,
    _apply_workspace_layout_ops,
    _extract_strategy_version,
    _find_view,
    _find_widget,
    _generate_workspace_proposal,
    _list_ready_strategy_projections,
    _normalize_data_availability_value,
    _normalize_view,
    _normalize_views_legacy_data_availability,
    _normalize_widget_data_availability,
    _proposal_etag,
    _revision_proposal_etag,
    _stable_hash,
    _touch_workspace,
    _validate_view,
    _validate_widget,
    _version_etag,
    _workspace_data_freshness,
    _workspace_etag,
    _workspace_from_proposal,
    _workspace_scope,
)


def build_workspaces_router(ctx: TradingRoomRouteContext) -> APIRouter:
    """Trading-room workspaces, proposals, views, widgets, and layout subrouter."""
    router = APIRouter()

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

        now = ctx.utc_now()
        page = ctx.store.list_decision_events(page_size=5)
        top_events = page["items"]
        all_events = ctx.store.list_decision_events(page_size=1000)["items"]

        queue_counts: Dict[str, int] = {"entry": 0, "add": 0, "reduce": 0, "exit": 0, "review": 0}
        for ev in all_events:
            kind = ev.get("event_kind")
            if kind in queue_counts:
                queue_counts[kind] += 1

        scope = _workspace_scope(identity)
        projections = _list_ready_strategy_projections(
            workshop_store=ctx.workshop_store,
            scope=scope,
            events=all_events,
            assessed_at=now,
        )
        aggregate = TradingRoomAggregate(
            spec_version="1.0",
            user_scope_ref=f"operator:{scope['user_id'] or 'unknown'}",
            strategies=[TradingRoomStrategy(**item["summary"]) for item in projections],
            queue_summary=QueueSummary(**queue_counts),
            top_decision_events=[TradingDecisionEvent(**e) for e in top_events],
            position_summaries=[],
            risk_summary=RiskSummary(state="normal"),
            snapshot_at=now,
            data_cutoff=now,
        )
        return aggregate.model_dump(exclude_none=True)

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
        scope = _workspace_scope(identity)

        all_events = ctx.store.list_decision_events(page_size=1000)["items"]
        events = [
            e for e in all_events
            if e.get("strategy_id") == strategy_id
        ]
        counts: Dict[str, int] = {"entry": 0, "add": 0, "reduce": 0, "exit": 0, "review": 0}
        for ev in events:
            kind = ev.get("event_kind")
            if kind in counts:
                counts[kind] += 1
        projection = next(
            (
                item for item in _list_ready_strategy_projections(
                    workshop_store=ctx.workshop_store,
                    scope=scope,
                    events=all_events,
                    assessed_at=ctx.utc_now(),
                )
                if item["summary"].get("strategy_id") == strategy_id
            ),
            None,
        )
        if projection is None and not events:
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Trading Room strategy {strategy_id!r} not found",
                "trading_room_strategy_not_found",
            )

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
            "data": {
                "strategy_id": strategy_id,
                **((projection or {}).get("detail") or {}),
                "pending_event_counts": counts,
                "readiness_state": "ready",
                "monitoring_state": "monitoring",
            },
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
        """Create a complete V11 TradingRoomWorkspaceProposal preview.

        The servant generator returns declarative WidgetSpec/ChartSpec payloads
        only. Unsupported renderers become generator metadata for fallback or a
        component task request; the route never accepts executable frontend code.
        """
        identity = ctx.extract_identity(authorization, session_cookie=pantheon_session)
        ctx.require_read_role(identity)
        ctx._check_write_auth(identity)
        request_body = body or {}
        if idempotency_key:
            ctx._check_idempotency(
                identity,
                f"POST:/bff/agora/strategies/{strategy_id}/trading-room/proposals",
                idempotency_key,
            )

        strategy_version = _extract_strategy_version(request_body)
        if not strategy_version:
            ctx._validation_failed(["strategyVersion is required"], status_code=400)

        personalization_hints = request_body.get("personalizationHints") or request_body.get("personalization_hints") or {}
        if personalization_hints and not isinstance(personalization_hints, dict):
            ctx._validation_failed(["personalizationHints must be an object"], status_code=400)

        evidence_refs = request_body.get("evidenceRefs") or request_body.get("evidence_refs") or []
        if evidence_refs and not isinstance(evidence_refs, list):
            ctx._validation_failed(["evidenceRefs must be an array"], status_code=400)

        data_freshness = request_body.get("dataFreshness") or request_body.get("data_freshness") or {}
        if data_freshness and not isinstance(data_freshness, dict):
            ctx._validation_failed(["dataFreshness must be an object keyed by data source"], status_code=400)

        trading_room_ready = request_body.get(
            "tradingRoomReady",
            request_body.get("trading_room_ready", True),
        )
        if not isinstance(trading_room_ready, bool):
            ctx._validation_failed(["tradingRoomReady must be a boolean"], status_code=400)

        now = ctx.utc_now()
        scope = _workspace_scope(identity)
        resolved_data_freshness = _workspace_data_freshness(
            store=ctx.store,
            strategy_id=strategy_id,
            evidence_refs=evidence_refs,
            reported=data_freshness,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
            workshop_store=ctx.workshop_store,
            assessed_at=now,
        )
        generation = _generate_workspace_proposal(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            proposal_id=f"trp_{uuid.uuid4().hex[:12]}",
            now=now,
            personalization_hints=personalization_hints,
            evidence_refs=evidence_refs,
            data_freshness=resolved_data_freshness,
            trading_room_ready=trading_room_ready,
        )
        if generation.status != "completed" or generation.proposal is None:
            ctx._validation_failed(
                generation.validation_errors
                or generation.blocking_reasons
                or ["workspace proposal generation failed"],
                status_code=422,
            )
        proposal = generation.proposal
        errors: List[str] = []
        if tuple(view["id"] for view in proposal["views"]) != _WINNER_BRANCH_VIEW_IDS:
            errors.append("proposal must include the full V11 Winner Branch view set")
        for view_index, view in enumerate(proposal["views"]):
            errors.extend(_validate_view(view, now=now, path=f"views[{view_index}]"))
        if errors:
            ctx._validation_failed(errors)

        ctx.store.upsert_workspace_proposal(
            proposal,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
            generation_meta=generation.meta(),
        )
        etag = _proposal_etag(proposal)
        response.headers["ETag"] = etag
        return {
            "data": proposal,
            "meta": ctx._meta(etag=etag, strategy_id=strategy_id, generator=generation.meta()),
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
        proposal, _scope = ctx._load_proposal_for_identity(
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
                generator=ctx.store.get_workspace_proposal_generation_meta(proposal_id),
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
        if idempotency_key:
            ctx._check_idempotency(
                identity,
                f"POST:/bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept",
                idempotency_key,
            )

        proposal, scope = ctx._load_proposal_for_identity(
            strategy_id=strategy_id,
            proposal_id=proposal_id,
            identity=identity,
        )
        expected_status = (body or {}).get("expectedStatus") or (body or {}).get("expected_status")
        if expected_status and expected_status != "preview":
            ctx._validation_failed(["expectedStatus must be 'preview'"], status_code=400)
        if proposal.get("status") != "preview":
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Only preview TradingRoomWorkspaceProposal resources can be accepted",
                "workspace_proposal_not_preview",
                details_extra={"proposal_status": proposal.get("status")},
            )

        workspace = _workspace_from_proposal(
            proposal=proposal,
            workspace_id=f"trw_{uuid.uuid4().hex[:12]}",
            user_id=scope["user_id"],
            now=ctx.utc_now(),
        )
        version = ctx._persist_workspace_with_version(
            workspace,
            scope=scope,
            change_summary="v1 - trading servant initial workspace proposal",
            generated_by="trading_servant",
            changed_by="trading_servant",
            reason=proposal.get("rationale"),
            affected_views=[view["id"] for view in workspace.get("views") or []],
            affected_widgets=[
                widget["id"]
                for view in workspace.get("views") or []
                for widget in view.get("widgets") or []
            ],
        )

        proposal["status"] = "accepted"
        ctx.store.upsert_workspace_proposal(
            proposal,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
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
        scope = _workspace_scope(identity)
        if not strategy_id:
            ctx._validation_failed(["strategy_id query parameter is required for workspace lookup"], status_code=400)
        workspace = ctx.store.get_workspace_for_strategy(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        if workspace is None:
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"No workspace found for strategy '{strategy_id}'",
                "workspace_not_found",
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
        scope = _workspace_scope(identity)
        target_version = version or strategy_version
        workspace = ctx.store.get_workspace_for_strategy(
            strategy_id=strategy_id,
            strategy_version=target_version,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        if workspace is None:
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"No workspace found for strategy '{strategy_id}'",
                "workspace_not_found",
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
        workspace, _scope = ctx._load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
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
        if idempotency_key:
            ctx._check_idempotency(
                identity,
                f"PATCH:/bff/agora/trading-room/workspaces/{workspace_id}/layout",
                idempotency_key,
            )
        workspace, scope = ctx._load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        ctx._require_workspace_etag(if_match, workspace)

        operations = (body or {}).get("operations") or []
        if not isinstance(operations, list) or not operations:
            ctx._validation_failed(["operations must be a non-empty array"], status_code=400)

        now = ctx.utc_now()
        updated, errors = _apply_workspace_layout_ops(workspace, operations, now=now)
        if errors or updated is None:
            ctx._validation_failed(errors)
        version = ctx._persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary="trader adjusted widget layout",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="layout operations accepted by trader",
            affected_widgets=ctx._affected_widgets_from_operations(operations),
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
        if idempotency_key:
            ctx._check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/workspaces/{workspace_id}/views",
                idempotency_key,
            )
        workspace, scope = ctx._load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        ctx._require_workspace_etag(if_match, workspace)

        view = body.get("viewSpec") or body.get("view_spec") or body
        if not isinstance(view, dict):
            ctx._validation_failed(["viewSpec must be an object"], status_code=400)
        view = _normalize_view(view)
        if _find_view(workspace, str(view.get("id") or "")):
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                f"View {view.get('id')!r} already exists",
                "workspace_view_already_exists",
            )

        now = ctx.utc_now()
        errors = _validate_view(view, now=now, require_data_availability=True)
        if errors:
            ctx._validation_failed(errors)
        updated = copy.deepcopy(workspace)
        updated.setdefault("views", []).append(view)
        updated = _touch_workspace(updated, now=now)
        version = ctx._persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"trader added view {view.get('id')}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="manual workspace view addition",
            affected_views=[str(view.get("id") or "")],
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
        if idempotency_key:
            ctx._check_idempotency(
                identity,
                f"PATCH:/bff/agora/trading-room/workspaces/{workspace_id}/views/{view_id}",
                idempotency_key,
            )
        workspace, scope = ctx._load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        ctx._require_workspace_etag(if_match, workspace)
        updated = copy.deepcopy(workspace)
        view = _find_view(updated, view_id)
        if view is None:
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"View {view_id!r} not found", "workspace_view_not_found")

        patch = body.get("patch") or body
        if not isinstance(patch, dict):
            ctx._validation_failed(["patch must be an object"], status_code=400)
        unsupported = set(patch) - (_VIEW_ALLOWED_FIELDS - {"id"})
        if unsupported:
            ctx._validation_failed([f"view patch has unsupported fields: {sorted(unsupported)}"], status_code=400)
        view.update(copy.deepcopy(patch))
        view["id"] = view_id
        view = _normalize_view(view)
        now = ctx.utc_now()
        errors = _validate_view(view, now=now, require_data_availability=True)
        if errors:
            ctx._validation_failed(errors)
        for index, current in enumerate(updated["views"]):
            if current.get("id") == view_id:
                updated["views"][index] = view
                break
        updated = _touch_workspace(updated, now=now)
        version = ctx._persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"trader updated view {view_id}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="manual workspace view update",
            affected_views=[view_id],
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
        if idempotency_key:
            ctx._check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/workspaces/{workspace_id}/widgets",
                idempotency_key,
            )
        workspace, scope = ctx._load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        ctx._require_workspace_etag(if_match, workspace)
        view_id = str(body.get("viewId") or body.get("view_id") or "").strip()
        if not view_id:
            ctx._validation_failed(["viewId is required"], status_code=400)
        widget = body.get("widgetSpec") or body.get("widget_spec") or {}
        if not isinstance(widget, dict):
            ctx._validation_failed(["widgetSpec must be an object"], status_code=400)

        updated = copy.deepcopy(workspace)
        view = _find_view(updated, view_id)
        if view is None:
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"View {view_id!r} not found", "workspace_view_not_found")
        if _find_widget(updated, str(widget.get("id") or ""))[1] is not None:
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(409, ErrorCode.RESOURCE_CONFLICT, f"Widget {widget.get('id')!r} already exists", "workspace_widget_already_exists")

        _normalize_widget_data_availability(widget)
        now = ctx.utc_now()
        errors = _validate_widget(widget, now=now, require_data_availability=True)
        if errors:
            ctx._validation_failed(errors)
        view.setdefault("widgets", []).append(copy.deepcopy(widget))
        view["widgetCount"] = len(view.get("widgets") or [])
        updated = _touch_workspace(updated, now=now)
        version = ctx._persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"trader added widget {widget.get('id')}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="manual workspace widget addition",
            affected_views=[view_id],
            affected_widgets=[str(widget.get("id") or "")],
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
        if idempotency_key:
            ctx._check_idempotency(
                identity,
                f"PATCH:/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}",
                idempotency_key,
            )
        workspace, scope = ctx._load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        ctx._require_workspace_etag(if_match, workspace)
        patch = body.get("patch") or body
        if not isinstance(patch, dict):
            ctx._validation_failed(["patch must be an object"], status_code=400)
        actor = str(patch.get("initiatedBy") or patch.get("initiated_by") or patch.get("actorType") or "").strip()
        if actor in {"servant", "trading_servant", "ai_servant"}:
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "Servant-originated widget changes must use WidgetRevisionProposal routes",
                "servant_direct_widget_patch_not_allowed",
            )

        unsupported = set(patch) - (_WIDGET_ALLOWED_FIELDS - {"id"}) - {"initiatedBy", "initiated_by", "actorType"}
        if unsupported:
            ctx._validation_failed([f"widget patch has unsupported fields: {sorted(unsupported)}"], status_code=400)
        updated = copy.deepcopy(workspace)
        _view, widget = _find_widget(updated, widget_id)
        if widget is None:
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Widget {widget_id!r} not found", "workspace_widget_not_found")
        clean_patch = {
            key: value
            for key, value in patch.items()
            if key not in {"initiatedBy", "initiated_by", "actorType"}
        }
        _normalize_widget_data_availability(clean_patch)
        widget.update(copy.deepcopy(clean_patch))
        widget["id"] = widget_id
        now = ctx.utc_now()
        errors = _validate_widget(widget, now=now, require_data_availability=True)
        if errors:
            ctx._validation_failed(errors)
        updated = _touch_workspace(updated, now=now)
        version = ctx._persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"trader updated widget {widget_id}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason="manual workspace widget update",
            affected_views=[str((_view or {}).get("id") or "")],
            affected_widgets=[widget_id],
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
        if idempotency_key:
            ctx._check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}/revision-proposals",
                idempotency_key,
            )
        workspace, scope = ctx._load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        view, before_widget = _find_widget(workspace, widget_id)
        if view is None or before_widget is None:
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Widget {widget_id!r} not found",
                "workspace_widget_not_found",
            )

        payload = body or {}
        instruction = str(payload.get("instruction") or "").strip()
        rationale = str(payload.get("rationale") or "").strip()
        data_availability = _normalize_data_availability_value(
            str(payload.get("dataAvailability") or payload.get("data_availability") or "").strip()
        )
        proposed_spec = payload.get("proposedSpec") or payload.get("proposed_spec")
        warnings = payload.get("warnings", [])
        errors: List[str] = []
        if not instruction:
            errors.append("instruction is required")
        if not rationale:
            errors.append("rationale is required")
        if data_availability not in _DATA_AVAILABILITY_VALUES:
            errors.append("dataAvailability must be full, partial, or missing")
        if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
            errors.append("warnings must be an array of strings")
        if not isinstance(proposed_spec, dict):
            errors.append("proposedSpec must be a TradingRoomWidgetSpec object")
        else:
            _normalize_widget_data_availability(proposed_spec)
            if proposed_spec.get("id") != widget_id:
                errors.append("proposedSpec.id must match widgetId; keep-copy acceptance creates a new copy id")
            errors.extend(
                _validate_widget(
                    proposed_spec,
                    now=ctx.utc_now(),
                    path="proposedSpec",
                    require_data_availability=True,
                )
            )
        supplied_view_id = str(payload.get("viewId") or payload.get("view_id") or "").strip()
        if supplied_view_id and supplied_view_id != view.get("id"):
            errors.append("viewId must match the widget's current view")
        supplied_status = str(payload.get("status") or "preview").strip()
        if supplied_status != "preview":
            errors.append("new WidgetRevisionProposal status must be preview")
        if errors:
            ctx._validation_failed(errors)

        proposal = {
            "id": f"wrp_{uuid.uuid4().hex[:12]}",
            "workspaceId": workspace_id,
            "viewId": view["id"],
            "widgetId": widget_id,
            "instruction": instruction,
            "beforeSpec": copy.deepcopy(before_widget),
            "proposedSpec": copy.deepcopy(proposed_spec),
            "rationale": rationale,
            "warnings": list(warnings),
            "dataAvailability": data_availability,
            "status": "preview",
        }
        ctx.store.upsert_widget_revision_proposal(
            proposal,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
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
        if idempotency_key:
            ctx._check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept",
                idempotency_key,
            )

        proposal, proposal_scope = ctx._load_revision_proposal_for_identity(
            proposal_id=proposal_id,
            identity=identity,
        )
        if proposal.get("status") != "preview":
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Only preview WidgetRevisionProposal resources can be accepted",
                "widget_revision_proposal_not_preview",
                details_extra={"proposal_status": proposal.get("status")},
            )

        workspace_id = proposal["workspaceId"]
        workspace, scope = ctx._load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        if scope != proposal_scope:
            ctx._raise_workspace_forbidden("widget_revision_proposal", proposal_id)
        ctx._require_workspace_etag(if_match, workspace)

        body = body or {}
        action = str(
            body.get("acceptanceAction")
            or body.get("acceptance_action")
            or body.get("action")
            or "apply"
        ).strip()
        keep_copy_actions = {
            "keep_original_add_modified_copy",
            "keep_original_and_add_modified_copy",
            "add_modified_copy",
            "keep_copy",
        }
        if action not in {"apply"} | keep_copy_actions:
            ctx._validation_failed(
                ["acceptanceAction must be apply or keep_original_add_modified_copy"],
                status_code=400,
            )

        current_view, current_widget = _find_widget(workspace, proposal["widgetId"])
        if current_view is None or current_widget is None:
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Widget {proposal['widgetId']!r} not found",
                "workspace_widget_not_found",
            )
        if current_view.get("id") != proposal.get("viewId"):
            ctx._validation_failed(["proposal viewId no longer matches the widget location"], status_code=409)
        if _stable_hash(current_widget) != _stable_hash(proposal["beforeSpec"]):
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(
                412,
                ErrorCode.PRECONDITION_FAILED,
                "Widget changed after the revision proposal preview was created.",
                "widget_revision_before_spec_mismatch",
                details_extra={"workspace_id": workspace_id, "widget_id": proposal["widgetId"]},
            )

        updated = copy.deepcopy(workspace)
        updated_view, updated_widget = _find_widget(updated, proposal["widgetId"])
        if updated_view is None or updated_widget is None:
            ctx._validation_failed(["proposal target widget no longer exists"], status_code=409)

        proposed = copy.deepcopy(proposal["proposedSpec"])
        affected_widgets = [proposal["widgetId"]]
        copied_widget_id: Optional[str] = None
        if action in keep_copy_actions:
            copied_widget_id = str(
                body.get("copyWidgetId")
                or body.get("copy_widget_id")
                or f"{proposal['widgetId']}_copy_{uuid.uuid4().hex[:6]}"
            ).strip()
            if not copied_widget_id:
                ctx._validation_failed(["copyWidgetId cannot be empty"], status_code=400)
            if _find_widget(updated, copied_widget_id)[1] is not None:
                ErrorCode = ctx._error_code_enum()
                raise ctx.bff_error(
                    409,
                    ErrorCode.RESOURCE_CONFLICT,
                    f"Widget {copied_widget_id!r} already exists",
                    "workspace_widget_already_exists",
                )
            proposed["id"] = copied_widget_id
            updated_view.setdefault("widgets", []).append(proposed)
            affected_widgets.append(copied_widget_id)
            change_summary = (
                f"accepted widget revision {proposal_id}; kept original "
                f"{proposal['widgetId']} and added modified copy {copied_widget_id}"
            )
            applied_action = "keep_original_add_modified_copy"
        else:
            proposed["id"] = proposal["widgetId"]
            for index, widget in enumerate(updated_view.get("widgets") or []):
                if widget.get("id") == proposal["widgetId"]:
                    updated_view["widgets"][index] = proposed
                    break
            change_summary = f"accepted widget revision {proposal_id} for {proposal['widgetId']}"
            applied_action = "apply"

        updated_view["widgetCount"] = len(updated_view.get("widgets") or [])
        errors = _validate_view(updated_view, now=ctx.utc_now(), path="updatedView")
        if errors:
            ctx._validation_failed(errors)

        updated = _touch_workspace(updated, now=ctx.utc_now(), generated_by="trading_servant")
        version = ctx._persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=change_summary,
            generated_by="trading_servant",
            changed_by="trading_servant",
            reason=proposal["rationale"],
            affected_views=[proposal["viewId"]],
            affected_widgets=affected_widgets,
            source_revision_proposal_id=proposal_id,
        )
        proposal["status"] = "accepted"
        ctx.store.upsert_widget_revision_proposal(
            proposal,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
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
                workspace_id=workspace_id,
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
        _workspace, scope = ctx._load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        versions = ctx.store.list_workspace_version_records(
            workspace_id,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        for version in versions:
            _normalize_views_legacy_data_availability(version.get("views") or [])
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
        if idempotency_key:
            ctx._check_idempotency(
                identity,
                f"POST:/bff/agora/trading-room/workspaces/{workspace_id}/versions/{version_id}/rollback",
                idempotency_key,
            )
        workspace, scope = ctx._load_workspace_for_identity(workspace_id=workspace_id, identity=identity)
        ctx._require_workspace_etag(if_match, workspace)
        target = ctx.store.get_workspace_version_record(
            workspace_id,
            version_id,
            tenant_id=scope["tenant_id"],
            user_id=scope["user_id"],
        )
        if target is None:
            ErrorCode = ctx._error_code_enum()
            raise ctx.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"TradingRoomDashboardVersion {version_id!r} not found",
                "workspace_version_not_found",
            )

        restored_views = _normalize_views_legacy_data_availability(
            copy.deepcopy(target.get("views") or [])
        )
        validation_errors: List[str] = []
        for view_index, view in enumerate(restored_views):
            validation_errors.extend(_validate_view(view, now=ctx.utc_now(), path=f"views[{view_index}]"))
        if validation_errors:
            ctx._validation_failed(validation_errors)

        now = ctx.utc_now()
        updated = copy.deepcopy(workspace)
        updated["views"] = restored_views
        updated["dashboardVersion"] = int(workspace.get("dashboardVersion") or 0) + 1
        updated["generatedBy"] = "user_modified"
        updated["status"] = "active"
        updated["updatedAt"] = now
        view_ids = [str(view.get("id") or "") for view in restored_views]
        if updated.get("activeViewId") not in view_ids:
            updated["activeViewId"] = view_ids[0] if view_ids else ""

        reason = str((body or {}).get("reason") or "").strip() or f"rollback to {version_id}"
        version = ctx._persist_workspace_with_version(
            updated,
            scope=scope,
            change_summary=f"rollback to dashboard version {target.get('dashboardVersion')}",
            generated_by="user_modified",
            changed_by=scope["user_id"],
            reason=reason,
            affected_views=view_ids,
            affected_widgets=[
                widget["id"]
                for view in restored_views
                for widget in view.get("widgets") or []
            ],
            rollback_of_version_id=version_id,
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
