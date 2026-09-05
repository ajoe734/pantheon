"""Research artifacts routes."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, Query, Request

from .common import (
    ResearchRouteContext,
    _authorization,
    _body_parameter,
    _path,
    _signature,
    _signature_query,
)
from ..service import ResearchNotFoundError, ResearchValidationError

try:
    from services.control_plane.bff.models import ErrorCode
except (ImportError, ValueError):
    from ..models import ErrorCode

_ARTIFACT_STATUSES = {"pending", "sealed", "superseded", "failed"}


def _validate_artifact_status(ctx: ResearchRouteContext, value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower()
    if normalized not in _ARTIFACT_STATUSES:
        raise ctx.bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "Invalid artifact status",
            f"status must be one of {sorted(_ARTIFACT_STATUSES)}",
            precondition_failed="status",
        )
    return normalized


def _legacy_artifact_reference_values(record: Dict[str, Any], field: str) -> set[str]:
    candidates: List[Any] = [record.get(field)]
    linkage = record.get("research_linkage")
    if isinstance(linkage, dict):
        candidates.extend(
            linkage.get(key)
            for key in (field, f"{field}_ref", f"linked_{field}")
        )
    if field == "experiment_id":
        candidates.append(record.get("produced_by_experiment_id"))
        candidates.append(record.get("experiment_refs"))
    if field == "lineage_id":
        lineage = record.get("lineage")
        if isinstance(lineage, dict):
            candidates.append(lineage.get("lineage_id"))

    values: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = (
                candidate.get(field)
                or candidate.get("id")
                or candidate.get("ref")
            )
        elif isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, dict):
                    item = item.get(field) or item.get("id") or item.get("ref")
                if item not in (None, ""):
                    values.add(str(item))
            continue
        if candidate not in (None, ""):
            values.add(str(candidate))
    return values


def _filter_legacy_artifacts(
    records: List[Dict[str, Any]],
    *,
    experiment_id: Optional[str],
    ticket_id: Optional[str],
    lineage_id: Optional[str],
    status: Optional[str],
) -> List[Dict[str, Any]]:
    requested = {
        "experiment_id": experiment_id,
        "ticket_id": ticket_id,
        "lineage_id": lineage_id,
    }
    filtered = list(records)
    for field, expected in requested.items():
        if expected not in (None, ""):
            filtered = [
                record
                for record in filtered
                if str(expected) in _legacy_artifact_reference_values(record, field)
            ]
    if status is not None:
        filtered = [
            record
            for record in filtered
            if str(record.get("status") or "").strip().lower() == status
        ]
    return filtered


def build_artifacts_router(ctx: ResearchRouteContext) -> APIRouter:
    router = APIRouter()

    async def _list_artifacts(
        artifact_type: Optional[str],
        status: Optional[str],
        tags: Optional[str],
        author: Optional[str],
        date_range: Optional[str],
        page_token: Optional[str],
        page_size: int,
        authorization: Optional[str],
    ) -> Dict[str, Any]:
        ctx.require_read_role(ctx.extract_identity(authorization))
        try:
            return ctx.service.list_artifacts(
                artifact_type=artifact_type,
                status=status,
                tags=tags,
                author=author,
                date_range=date_range,
                page_token=page_token,
                page_size=page_size,
            )
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            ctx.raise_service_error(exc)
            raise AssertionError("unreachable")

    async def _get_artifact(artifact_id: str, authorization: Optional[str]) -> Dict[str, Any]:
        ctx.require_read_role(ctx.extract_identity(authorization))
        try:
            return ctx.service.get_artifact(artifact_id)
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            ctx.raise_service_error(exc)
            raise AssertionError("unreachable")

    @router.get("/api/v1/research/artifacts")
    async def list_research_artifacts(
        artifact_type: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[str] = None,
        author: Optional[str] = None,
        date_range: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _list_artifacts(
            artifact_type, status, tags, author, date_range, page_token, page_size, authorization,
        )

    @router.get("/api/v1/research/artifacts/compare")
    async def compare_research_artifacts(
        artifact_ids: str = Query(..., description="Comma-separated artifact IDs to compare"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        ctx.require_read_role(ctx.extract_identity(authorization))
        try:
            return ctx.service.compare_artifacts(artifact_ids)
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            ctx.raise_service_error(exc)
            raise AssertionError("unreachable")

    @router.get("/api/v1/research/artifacts/{artifact_id}")
    async def get_research_artifact(
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _get_artifact(artifact_id, authorization)

    @router.get("/bff/artifacts")
    async def bff_list_research_artifacts(
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        result = await _list_artifacts(None, None, None, None, None, page_token, page_size, authorization)
        return {
            "data": result["artifacts"],
            "items": result["artifacts"],
            "page_info": {"next_page_token": result["next_page_token"], "total": result["total_count"]},
            "meta": result["meta"],
        }

    @router.get("/bff/artifacts/{artifact_id}")
    async def bff_get_research_artifact(
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        payload = await _get_artifact(artifact_id, authorization)
        meta = payload.pop("meta")
        return {"data": payload, "meta": meta}

    # Legacy inventory endpoints
    async def endpoint_compare_artifacts_api(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        return ctx.service.compare_artifacts(ctx.required_text({"artifact_ids": ctx.query(request, "artifact_ids")}, "artifact_ids"))

    async def endpoint_list_artifacts_api(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        experiment_id = ctx.query(request, "experiment_id")
        ticket_id = ctx.query(request, "ticket_id")
        lineage_id = ctx.query(request, "lineage_id")
        status = _validate_artifact_status(ctx, ctx.query(request, "status"))
        artifact_reader = ctx.port_method(port, "list_research_artifacts")
        try:
            records = list(artifact_reader(
                experiment_id=experiment_id,
                ticket_id=ticket_id,
                lineage_id=lineage_id,
                status=status,
            ) or [])
        except TypeError:
            records = list(artifact_reader(status=status) or [])
        records = _filter_legacy_artifacts(
            records,
            experiment_id=experiment_id,
            ticket_id=ticket_id,
            lineage_id=lineage_id,
            status=status,
        )
        items, next_token = ctx.page(records, request)
        return {"artifacts": items, "next_page_token": next_token, "total_count": len(records), "meta": ctx.meta(snapshot_at, "artifact_list", "research_artifacts", bool(records))}

    async def endpoint_get_artifact_api(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        artifact_id = str(request.path_params.get("artifact_id") or "")
        artifact = ctx.call_port(port, "get_research_artifact", artifact_id)
        if not artifact:
            ctx.not_found("Artifact", artifact_id)
        payload = dict(artifact)
        payload["meta"] = ctx.meta(snapshot_at, "artifact_detail", "research_artifacts", True)
        return payload

    async def endpoint_bff_patch_artifact(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request, operator=True)
        port = ctx.get_read_store()
        artifact_id = str(request.path_params.get("artifact_id") or "")
        if not ctx.call_port(port, "get_research_artifact", artifact_id):
            ctx.not_found("Artifact", artifact_id)
        raise ctx.bff_error(409, ErrorCode.OPERATION_NOT_ALLOWED, "Research artifacts are immutable", "Use the owning artifact pipeline; the generic BFF patch alias has no typed replacement")

    async def endpoint_bff_create_artifact(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request, operator=True)
        raise ctx.bff_error(501, ErrorCode.NOT_IMPLEMENTED, "Artifact creation is not exposed by Research", "Use the owning artifact pipeline; the generic BFF create alias has no typed replacement")

    auth = _authorization()
    endpoint_compare_artifacts_api.__signature__ = _signature(_signature_query("artifact_ids", annotation=str, default=...), auth)
    endpoint_list_artifacts_api.__signature__ = _signature(
        _signature_query("experiment_id"), _signature_query("ticket_id"), _signature_query("lineage_id"),
        _signature_query("status"), _signature_query("page_token"),
        _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
    )
    endpoint_get_artifact_api.__signature__ = _signature(_path("artifact_id"), auth)
    endpoint_bff_patch_artifact.__signature__ = _signature(_path("artifact_id"), _body_parameter(required=False), auth)
    endpoint_bff_create_artifact.__signature__ = _signature(_body_parameter(required=False), auth)

    router.add_api_route("/api/v1/artifacts/compare", endpoint_compare_artifacts_api, methods=["GET"], name="compare_artifacts_api")
    router.add_api_route("/api/v1/artifacts", endpoint_list_artifacts_api, methods=["GET"], name="list_artifacts_api")
    router.add_api_route("/api/v1/artifacts/{artifact_id}", endpoint_get_artifact_api, methods=["GET"], name="get_artifact_api")
    router.add_api_route("/bff/artifacts/{artifact_id}", endpoint_bff_patch_artifact, methods=["PATCH"], name="bff_patch_artifact")
    router.add_api_route("/bff/artifacts", endpoint_bff_create_artifact, methods=["POST"], name="bff_create_artifact")

    return router
