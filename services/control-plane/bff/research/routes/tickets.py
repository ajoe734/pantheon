"""Research tickets and search routes."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request

from .common import (
    ResearchRouteContext,
    _authorization,
    _body_parameter,
    _path,
    _signature,
    _signature_query,
)

try:
    from services.control_plane.bff.models import ErrorCode
except (ImportError, ValueError):
    from ..models import ErrorCode

_TICKET_PRIORITIES = {"low", "normal", "high", "critical"}
_TICKET_STATUSES = {"open", "in_progress", "closed", "archived"}
_TICKET_STATUS_TRANSITIONS = {
    "open": {"in_progress", "closed"},
    "in_progress": {"closed"},
    "closed": {"archived"},
    "archived": set(),
}
_RESEARCH_SEARCH_MATCH_TYPES = {"all", "ticket", "experiment", "artifact"}
_RESEARCH_SEARCH_DATE_RANGES = {"24h", "7d", "30d", "90d"}


def build_tickets_router(ctx: ResearchRouteContext) -> APIRouter:
    router = APIRouter()

    def _validate_ticket_priority(value: Any) -> str:
        return ctx.validate_choice(
            value,
            field="priority",
            label="research ticket priority",
            allowed=_TICKET_PRIORITIES,
        )

    def _validate_ticket_status(value: Any) -> str:
        return ctx.validate_choice(
            value,
            field="status",
            label="research ticket status",
            allowed=_TICKET_STATUSES,
        )

    def _research_search_bad_request(field: str, reason: str) -> None:
        raise ctx.bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "Invalid research search query",
            reason,
            precondition_failed=field,
        )

    def _validate_research_search_query(value: Optional[str]) -> str:
        query = str(value or "").strip()
        if not query:
            _research_search_bad_request("q", "q is required and must be non-empty")
        return query

    def _validate_research_search_match_type(value: Optional[str]) -> str:
        match_type = str(value or "all").strip().lower()
        if match_type not in _RESEARCH_SEARCH_MATCH_TYPES:
            _research_search_bad_request(
                "match_type",
                f"match_type must be one of {sorted(_RESEARCH_SEARCH_MATCH_TYPES)}",
            )
        return match_type

    def _validate_research_search_status(value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        status = str(value).strip().lower()
        if status not in _TICKET_STATUSES:
            _research_search_bad_request(
                "status", f"status must be one of {sorted(_TICKET_STATUSES)}"
            )
        return status

    def _validate_research_search_date_range(value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        date_range = str(value).strip().lower()
        if date_range not in _RESEARCH_SEARCH_DATE_RANGES:
            _research_search_bad_request(
                "date_range",
                f"date_range must be one of {sorted(_RESEARCH_SEARCH_DATE_RANGES)}",
            )
        return date_range

    def _legacy_ticket_surface_state(
        *, snapshot_at: str, has_data: Optional[bool] = None
    ) -> str:
        port = ctx.get_read_store()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn("research_tickets") or "missing") if callable(source_fn) else "missing"
        surface = ctx.dataset_surface_status(
            "research_tickets",
            snapshot_at=snapshot_at,
            source=source,
            has_data=has_data,
        )
        if isinstance(surface, str):
            return surface
        status = str((surface or {}).get("status") or "")
        if status == "unavailable" or source == "missing":
            return "unavailable"
        if source == "local_snapshot":
            return "degraded"
        if status == "degraded":
            return "stale"
        return "fresh"

    def _validate_ticket_patch(ticket: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        allowed_fields = {"status", "title", "description", "priority", "owner"}
        unknown_fields = sorted(set(payload) - allowed_fields)
        if unknown_fields:
            raise ctx.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid research ticket patch payload",
                f"Unsupported patch fields: {unknown_fields}",
                precondition_failed="payload_shape",
            )

        patch: Dict[str, Any] = {}
        editable = bool((ticket.get("allowedActions") or {}).get("canEdit"))
        for field in ("title", "description", "owner"):
            if field not in payload:
                continue
            value = ctx.required_text(payload, field)
            if not editable:
                raise ctx.bff_error(
                    409,
                    ErrorCode.OPERATION_NOT_ALLOWED,
                    "Research ticket is not editable in its current lifecycle state",
                    f"{field} cannot be modified while allowedActions.canEdit is false.",
                    precondition_failed="allowedActions.canEdit",
                )
            patch[field] = value

        if "priority" in payload:
            if not editable:
                raise ctx.bff_error(
                    409,
                    ErrorCode.OPERATION_NOT_ALLOWED,
                    "Research ticket is not editable in its current lifecycle state",
                    "priority cannot be modified while allowedActions.canEdit is false.",
                    precondition_failed="allowedActions.canEdit",
                )
            patch["priority"] = _validate_ticket_priority(payload["priority"])

        if "status" in payload:
            current_status = str(ticket.get("status") or "").strip().lower()
            next_status = _validate_ticket_status(payload["status"])
            if next_status != current_status:
                actions = ticket.get("allowedActions") or {}
                if next_status == "closed" and not actions.get("canClose"):
                    raise ctx.bff_error(
                        409,
                        ErrorCode.OPERATION_NOT_ALLOWED,
                        "Research ticket cannot be closed in its current state",
                        "allowedActions.canClose is false for this ticket.",
                        precondition_failed="allowedActions.canClose",
                    )
                if next_status == "archived" and not actions.get("canArchive"):
                    raise ctx.bff_error(
                        409,
                        ErrorCode.OPERATION_NOT_ALLOWED,
                        "Research ticket cannot be archived in its current state",
                        "allowedActions.canArchive is false for this ticket.",
                        precondition_failed="allowedActions.canArchive",
                    )
                if next_status not in _TICKET_STATUS_TRANSITIONS.get(current_status, set()):
                    raise ctx.bff_error(
                        409,
                        ErrorCode.OPERATION_NOT_ALLOWED,
                        "Invalid research ticket lifecycle transition",
                        f"Cannot transition research ticket from {current_status} to {next_status}.",
                        precondition_failed="status_transition",
                    )
            patch["status"] = next_status

        if not patch:
            raise ctx.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Empty research ticket patch payload",
                "At least one accepted patch field is required.",
                precondition_failed="payload_shape",
            )
        return patch

    async def endpoint_create_ticket(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        payload = await ctx.body(request)
        ticket = ctx.call_port(
            port,
            "create_research_ticket",
            title=ctx.required_text(payload, "title"),
            description=ctx.required_text(payload, "description"),
            priority=_validate_ticket_priority(payload.get("priority")),
            owner=ctx.required_text(payload, "owner"),
            actor_id=str(getattr(identity, "operator_id", "")),
            created_at=snapshot_at,
        )
        return {key: ticket.get(key) for key in ("ticket_id", "status", "created_at", "allowedActions")}

    async def endpoint_list_tickets(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        statuses = [item.strip() for item in str(ctx.query(request, "status", "") or "").split(",") if item.strip()] or None
        if statuses:
            statuses = [_validate_ticket_status(status) for status in statuses]
        records = list(ctx.call_port(port, "list_research_tickets", statuses=statuses, owner=ctx.query(request, "owner"), include_fixture_pack=False) or [])
        surface_state = _legacy_ticket_surface_state(snapshot_at=snapshot_at)
        if surface_state == "unavailable":
            items, next_token, total = [], None, 0
        else:
            items, next_token, total = *ctx.page(records, request), len(records)
        meta = ctx.snapshot_meta(snapshot_at)
        meta["surfaces"] = {"ticket_list": surface_state}
        return {"data": items, "page_info": {"next_page_token": next_token, "total": total}, "meta": meta}

    async def endpoint_get_ticket(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        ticket_id = str(request.path_params.get("ticket_id") or "")
        ticket = ctx.call_port(port, "get_research_ticket", ticket_id)
        if not ticket:
            ctx.not_found("Research ticket", ticket_id)
        payload = dict(ticket)
        payload["links"] = {"self": f"/api/v1/research/tickets/{ticket_id}", "workbench_detail": f"/research/tickets/{ticket_id}"}
        payload["meta"] = {
            **ctx.snapshot_meta(snapshot_at),
            "surfaces": {
                "ticket_detail": _legacy_ticket_surface_state(
                    snapshot_at=snapshot_at, has_data=True
                ),
            },
        }
        return payload

    async def endpoint_patch_ticket(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        ticket_id = str(request.path_params.get("ticket_id") or "")
        ticket = ctx.call_port(port, "get_research_ticket", ticket_id)
        if not ticket:
            ctx.not_found("Research ticket", ticket_id)
        patch = _validate_ticket_patch(ticket, await ctx.body(request))
        updated = ctx.call_port(port, "patch_research_ticket", ticket_id, patch=patch, actor_id=str(getattr(identity, "operator_id", "")), updated_at=snapshot_at)
        if not updated:
            raise ctx.bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Research ticket store unavailable", "Research ticket update store is unavailable")
        return {key: updated.get(key) for key in ("ticket_id", "status", "updated_at", "allowedActions")}

    async def endpoint_research_search(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        query = _validate_research_search_query(ctx.query(request, "q"))
        match_type = _validate_research_search_match_type(ctx.query(request, "match_type", "all"))
        status = _validate_research_search_status(ctx.query(request, "status"))
        date_range = _validate_research_search_date_range(ctx.query(request, "date_range"))
        index = ctx.call_port(port, "get_research_search_index")
        if not index:
            raise ctx.bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Search results are unavailable", "SEARCH_RESULTS_UNAVAILABLE")
        records = list(ctx.call_port(port, "list_research_search_results", query=query, match_type=match_type, status=status, date_range=date_range) or [])
        items, next_token = ctx.page(records, request, 25)
        meta = ctx.meta(snapshot_at, "search_results", "research_search", bool(records))
        meta["index_adapter"] = index
        return {"data": items, "page_info": {"next_page_token": next_token, "total": len(records)}, "meta": meta}

    async def endpoint_source_connectors(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        registry = ctx.call_port(port, "get_source_connector_registry") or {}
        meta = ctx.meta(snapshot_at, "source_connector_registry", "source_connectors", bool(registry.get("connectors")))
        meta.update({"source": registry.get("source", "missing"), "provider_examples": list(registry.get("provider_examples") or []), "policy_registry": registry.get("policy_registry")})
        return {"data": list(registry.get("connectors") or []), "meta": meta}

    async def endpoint_source_change_proposals(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        result = ctx.call_port(port, "get_source_change_proposals", status=ctx.query(request, "status"), proposal_type=ctx.query(request, "proposal_type"), source_kind=ctx.query(request, "source_kind")) or {}
        records = list(result.get("proposals") or [])
        source = str(result.get("source") or "missing")
        meta = ctx.snapshot_meta(snapshot_at)
        meta["surfaces"] = {
            "source_change_proposals": "ok" if source == "service_client" else "unavailable"
        }
        meta["source"] = source
        return {"data": records, "meta": meta}

    auth = _authorization()

    endpoint_create_ticket.__signature__ = _signature(_body_parameter(), auth)
    endpoint_list_tickets.__signature__ = _signature(
        _signature_query("status"), _signature_query("owner"), _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=200), auth,
    )
    endpoint_get_ticket.__signature__ = _signature(_path("ticket_id"), auth)
    endpoint_patch_ticket.__signature__ = _signature(_path("ticket_id"), _body_parameter(), auth)
    endpoint_research_search.__signature__ = _signature(
        _signature_query("q", annotation=str, default=...),
        _signature_query("match_type", annotation=str, default="all"),
        _signature_query("status"),
        _signature_query("date_range"),
        _signature_query("page_token"),
        _signature_query("page_size", annotation=int, default=25, ge=1, le=100),
        auth,
    )
    endpoint_source_connectors.__signature__ = _signature(auth)
    endpoint_source_change_proposals.__signature__ = _signature(_signature_query("status"), _signature_query("proposal_type"), _signature_query("source_kind"), auth)

    router.add_api_route("/api/v1/research/tickets", endpoint_create_ticket, methods=["POST"], name="create_ticket")
    router.add_api_route("/api/v1/research/tickets", endpoint_list_tickets, methods=["GET"], name="list_tickets")
    router.add_api_route("/api/v1/research/tickets/{ticket_id}", endpoint_get_ticket, methods=["GET"], name="get_ticket")
    router.add_api_route("/api/v1/research/tickets/{ticket_id}", endpoint_patch_ticket, methods=["PATCH"], name="patch_ticket")
    router.add_api_route("/api/v1/research/search", endpoint_research_search, methods=["GET"], name="research_search")
    router.add_api_route("/api/v1/research/source-connectors", endpoint_source_connectors, methods=["GET"], name="source_connectors")
    router.add_api_route("/api/v1/research/source-change-proposals", endpoint_source_change_proposals, methods=["GET"], name="source_change_proposals")

    return router
