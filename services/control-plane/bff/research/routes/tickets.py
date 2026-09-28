"""Research tickets and search routes."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request

from .common import (
    ResearchRouteContext,
    _authorization,
    _body_parameter,
    _identity_tenant_id,
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

    async def endpoint_create_ticket(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request, operator=True)
        payload = await ctx.body(request)
        idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
        actor_id = (
            getattr(identity, "operator_id", None)
            or getattr(identity, "user_id", None)
            or getattr(identity, "actor_id", None)
            or str(identity)
        )
        tenant_id = _identity_tenant_id(identity)
        try:
            return ctx.service.create_research_ticket(
                title=ctx.required_text(payload, "title"),
                description=ctx.required_text(payload, "description"),
                priority=_validate_ticket_priority(payload.get("priority")),
                owner=ctx.required_text(payload, "owner"),
                actor_id=str(actor_id).strip() if actor_id else "",
                tenant_id=str(tenant_id).strip() if tenant_id else None,
                idempotency_key=str(idempotency_key).strip() if idempotency_key else None,
                payload=payload,
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_list_tickets(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request)
        tenant_id = _identity_tenant_id(identity)
        statuses = [item.strip() for item in str(ctx.query(request, "status", "") or "").split(",") if item.strip()] or None
        if statuses:
            statuses = [_validate_ticket_status(status) for status in statuses]
        try:
            page_size = int(ctx.query(request, "page_size", "20") or 20)
        except (TypeError, ValueError):
            page_size = 20
        try:
            return ctx.service.list_research_tickets(
                statuses=statuses,
                owner=ctx.query(request, "owner"),
                tenant_id=str(tenant_id).strip() if tenant_id else None,
                page_token=ctx.query(request, "page_token"),
                page_size=page_size,
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_get_ticket(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request)
        tenant_id = _identity_tenant_id(identity)
        ticket_id = str(request.path_params.get("ticket_id") or "")
        try:
            return ctx.service.get_research_ticket(
                ticket_id,
                tenant_id=str(tenant_id).strip() if tenant_id else None,
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_patch_ticket(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request, operator=True)
        ticket_id = str(request.path_params.get("ticket_id") or "")
        payload = await ctx.body(request)
        idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
        actor_id = (
            getattr(identity, "operator_id", None)
            or getattr(identity, "user_id", None)
            or getattr(identity, "actor_id", None)
            or str(identity)
        )
        tenant_id = _identity_tenant_id(identity)
        try:
            return ctx.service.patch_research_ticket(
                ticket_id,
                payload,
                actor_id=str(actor_id).strip() if actor_id else "",
                tenant_id=str(tenant_id).strip() if tenant_id else None,
                idempotency_key=str(idempotency_key).strip() if idempotency_key else None,
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_research_search(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        query = _validate_research_search_query(ctx.query(request, "q"))
        match_type = _validate_research_search_match_type(ctx.query(request, "match_type", "all"))
        status = _validate_research_search_status(ctx.query(request, "status"))
        date_range = _validate_research_search_date_range(ctx.query(request, "date_range"))
        try:
            page_size = int(ctx.query(request, "page_size", "25") or 25)
        except (TypeError, ValueError):
            page_size = 25
        try:
            return ctx.service.search_research(
                query=query,
                match_type=match_type,
                status=status,
                date_range=date_range,
                page_token=ctx.query(request, "page_token"),
                page_size=page_size,
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_source_connectors(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        try:
            return ctx.service.get_source_connectors()
        except Exception as exc:
            ctx.raise_service_error(exc)

    async def endpoint_source_change_proposals(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        try:
            return ctx.service.get_source_change_proposals(
                status=ctx.query(request, "status"),
                proposal_type=ctx.query(request, "proposal_type"),
                source_kind=ctx.query(request, "source_kind"),
            )
        except Exception as exc:
            ctx.raise_service_error(exc)

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
