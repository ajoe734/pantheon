"""Common definitions, context, and helpers for Research subrouters."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request

try:
    from services.control_plane.bff.models import (
        ErrorCode,
        ObjectType,
        SOURCE_TYPE_TO_EVIDENCE_KIND,
        redact_evidence_refs,
    )
except (ImportError, ValueError):
    from ..models import (
        ErrorCode,
        ObjectType,
        SOURCE_TYPE_TO_EVIDENCE_KIND,
        redact_evidence_refs,
    )

from ..service import ResearchNotFoundError, ResearchRouterService, ResearchValidationError

PageSlice = Callable[[List[Dict[str, Any]], Optional[str], int], Tuple[List[Dict[str, Any]], Optional[str]]]
SnapshotMeta = Callable[[str], Dict[str, Any]]
SurfaceStatus = Callable[..., Dict[str, Any]]
SubmitAction = Callable[..., Any]
IdentityCapabilities = Callable[[Any], Optional[List[str]]]
CrossEntitySearch = Callable[..., Any]
ConflictLogList = Callable[..., List[Dict[str, Any]]]
ConflictLogGet = Callable[[str], Optional[Dict[str, Any]]]

_RESEARCH_EXPERIMENT_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}

_KW03_LINKED_ENTITY_TYPES = {
    "memory_entry", "research_note", "insight_card", "strategy_spec", "experiment", "artifact",
}
_KW03_LINK_TYPES = {
    "supporting_evidence", "counter_evidence", "citation", "provenance", "corroboration",
}
_KW03_CREDIBILITY_TIERS = {"primary", "secondary", "tertiary", "unverified"}
_KW04_STATUSES = {"active", "superseded", "archived", "all"}
_KW04_LINKED_ENTITY_TYPES = {
    "memory_entry", "research_note", "evidence_ref", "strategy_spec", "experiment",
}
_KW04_RECENCY_VALUES = {"7d", "30d", "90d", "all"}
_KW05_LIFECYCLE_STATES = {"draft", "candidate", "approved", "retired", "all"}
_ENTITY_TYPE_EVIDENCE_KIND: Dict[str, str] = {
    "strategy_spec": "strategy",
    "strategy": "strategy",
    "persona": "persona",
    "deployment_plan": "deployment",
    "deployment": "deployment",
    "runtime": "runtime",
    "runtime_binding": "runtime",
    "alert": "alert",
    "incident": "incident",
    "job": "job",
    "audit": "audit",
    "metric": "metric",
    "policy": "policy",
    "approval": "approval",
    "artifact": "artifact",
    "signal": "signal",
    "journal": "journal",
    "postmortem": "postmortem",
}


def _default_page_slice(
    items: List[Dict[str, Any]], page_token: Optional[str], page_size: int
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Opaque numeric-offset pagination, matching main.py's ``_page_slice``."""
    try:
        start = int(page_token) if page_token else 0
    except (TypeError, ValueError):
        start = 0
    end = start + page_size
    next_page_token = str(end) if end < len(items) else None
    return items[start:end], next_page_token


def _default_snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    return {"snapshot_at": snapshot_at}


def _default_surface_status(dataset: str, *, snapshot_at: str, **_: Any) -> Dict[str, Any]:
    return {"status": "available", "dataset": dataset, "snapshot_at": snapshot_at}


def _filter_by_status_csv(records: List[Dict[str, Any]], status_csv: Optional[str]) -> List[Dict[str, Any]]:
    if not status_csv:
        return records
    requested = {s.strip().lower() for s in status_csv.split(",") if s.strip()}
    return [r for r in records if str(r.get("status") or "").lower() in requested]


def _parameter(
    name: str,
    *,
    annotation: Any,
    default: Any = inspect.Parameter.empty,
) -> inspect.Parameter:
    return inspect.Parameter(
        name,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        annotation=annotation,
        default=default,
    )


def _path(name: str) -> inspect.Parameter:
    return _parameter(name, annotation=str)


def _signature_query(
    name: str,
    *,
    annotation: Any = Optional[str],
    default: Any = None,
    **constraints: Any,
) -> inspect.Parameter:
    from fastapi import Query
    return _parameter(name, annotation=annotation, default=Query(default=default, **constraints))


def _body_parameter(*, required: bool = True) -> inspect.Parameter:
    from fastapi import Body
    body = Body(...) if required else Body(default_factory=dict)
    return _parameter("payload", annotation=Dict[str, Any], default=body)


def _authorization() -> inspect.Parameter:
    from fastapi import Header
    return _parameter("authorization", annotation=Optional[str], default=Header(default=None))


def _idempotency_key() -> inspect.Parameter:
    from fastapi import Header
    return _parameter(
        "x_idempotency_key",
        annotation=Optional[str],
        default=Header(default=None, alias="X-Idempotency-Key"),
    )


def _signature(*parameters: inspect.Parameter) -> inspect.Signature:
    return inspect.Signature((_parameter("request", annotation=Request), *parameters))


@dataclass
class ResearchRouteContext:
    get_read_store: Callable[[], Any]
    extract_identity: Callable[[Optional[str]], Any]
    require_read_role: Callable[[Any], None]
    bff_error: Callable[..., Exception]
    utc_now: Callable[[], str]
    page_slice: PageSlice = _default_page_slice
    snapshot_meta: SnapshotMeta = _default_snapshot_meta
    dataset_surface_status: SurfaceStatus = _default_surface_status
    require_operator_role: Optional[Callable[[Any], None]] = None
    submit_experiment_action: Optional[SubmitAction] = None
    build_knowledge_workbench: Optional[Callable[[], Any]] = None
    build_research_oss_readiness: Optional[Callable[..., Any]] = None
    submit_source_search_command: Optional[Callable[..., Any]] = None
    get_capabilities: Optional[IdentityCapabilities] = None
    cross_entity_search: Optional[CrossEntitySearch] = None
    list_synthesis_conflict_logs: Optional[ConflictLogList] = None
    get_synthesis_conflict_log: Optional[ConflictLogGet] = None
    service: Optional[ResearchRouterService] = None

    def __post_init__(self):
        if self.service is None:
            self.service = ResearchRouterService(
                port_getter=self.get_read_store,
                utc_now=self.utc_now,
                snapshot_meta=self.snapshot_meta,
                page_slice=self.page_slice,
            )

    def raise_service_error(self, exc: Exception) -> None:
        if isinstance(exc, ResearchNotFoundError):
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"{exc.label} not found",
                str(exc),
            ) from exc
        if isinstance(exc, ResearchValidationError):
            error_code = getattr(ErrorCode, exc.error_code, ErrorCode.VALIDATION_FAILED)
            raise self.bff_error(
                exc.status_code,
                error_code,
                str(exc),
                str(exc),
                precondition_failed=exc.field,
            ) from exc
        raise exc

    def identity(self, request: Request, *, operator: bool = False) -> Any:
        ident = self.extract_identity(request.headers.get("authorization"))
        if operator:
            if self.require_operator_role is None:
                raise self.bff_error(
                    501,
                    ErrorCode.NOT_IMPLEMENTED,
                    "Operator route is not wired",
                    "create_research_router needs require_operator_role for this route",
                )
            self.require_operator_role(ident)
        else:
            self.require_read_role(ident)
        return ident

    def query(self, request: Request, name: str, default: Optional[str] = None) -> Optional[str]:
        value = request.query_params.get(name)
        return default if value is None else value

    async def body(self, request: Request) -> Dict[str, Any]:
        try:
            return await request.json()
        except Exception:
            return {}

    def page(self, records: List[Dict[str, Any]], request: Request, default_size: int = 20) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        try:
            page_size = int(self.query(request, "page_size", str(default_size)) or default_size)
        except ValueError:
            page_size = default_size
        return self.page_slice(records, self.query(request, "page_token"), page_size)

    def meta(self, snapshot_at: str, surface_name: str, dataset: str, has_data: bool) -> Dict[str, Any]:
        port = self.get_read_store()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn(dataset) or "missing") if callable(source_fn) else "missing"
        surface = self.dataset_surface_status(
            dataset,
            snapshot_at=snapshot_at,
            source=source,
            has_data=has_data,
        )
        result = dict(self.snapshot_meta(snapshot_at))
        result["surfaces"] = {surface_name: surface}
        return result

    def port_method(self, port: Any, name: str) -> Callable[..., Any]:
        if not hasattr(port, name):
            raise self.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                f"Research store port missing {name}",
                f"Port {type(port).__name__} does not implement {name}",
            )
        return getattr(port, name)

    def call_port(self, port: Any, name: str, *args: Any, **kwargs: Any) -> Any:
        method = self.port_method(port, name)
        try:
            return method(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            raise self.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                f"Research port {name} failed",
                str(exc),
            ) from exc

    def not_found(self, label: str, identifier: str) -> None:
        raise self.bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"{label} not found",
            f"{label} {identifier} does not exist",
        )

    def required_text(self, payload: Dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"{field} is required",
                f"{field} must be a non-empty string",
                precondition_failed=field,
            )
        return value.strip()

    def required_dict(self, payload: Dict[str, Any], field: str) -> Dict[str, Any]:
        value = payload.get(field)
        if not isinstance(value, dict):
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"{field} is required",
                f"{field} must be an object",
                precondition_failed=field,
            )
        return value

    def validate_choice(self, value: Any, *, field: str, label: str, allowed: set[str]) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in allowed:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"Invalid {label}",
                f"{field} must be one of: {sorted(allowed)}",
                precondition_failed=field,
            )
        return normalized
