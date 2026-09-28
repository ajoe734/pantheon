"""Common definitions, context, and helpers for Research subrouters."""
from __future__ import annotations

import inspect
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request

try:
    from services.control_plane.bff.auth.policy import resolve_identity_tenant_id
    from services.control_plane.bff.models import (
        ErrorCode,
        ObjectType,
        SOURCE_TYPE_TO_EVIDENCE_KIND,
        redact_evidence_refs,
    )
except (ImportError, ValueError):
    from ..auth.policy import resolve_identity_tenant_id
    from ..models import (
        ErrorCode,
        ObjectType,
        SOURCE_TYPE_TO_EVIDENCE_KIND,
        redact_evidence_refs,
    )


from ..service import ResearchNotFoundError, ResearchRouterService, ResearchValidationError


def _identity_tenant_id(identity: Any) -> Optional[str]:
    # ``OperatorIdentity`` carries tenant in ``identity.claims``, not a
    # top-level ``tenant_id``/``tenant`` attribute; use the canonical
    # claims-aware resolver so research route tenant scoping is not
    # silently disabled for real JWT identities.
    return resolve_identity_tenant_id(identity)

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


def format_dataset_surface_status(
    dataset: str,
    *,
    snapshot_at: Optional[str] = None,
    source: Optional[str] = None,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
    utc_now: Optional[Callable[[], str]] = None,
    **_: Any,
) -> Dict[str, Any]:
    state = os.getenv("BFF_READ_SURFACE_STATE", "fresh")
    now_fn = utc_now or (lambda: snapshot_at or "")
    now_str = now_fn() if callable(now_fn) else str(now_fn)

    if state == "fresh":
        surface: Dict[str, Any] = {"status": "ok"}
    elif state in {"degraded", "stale"}:
        surface = {
            "status": "degraded",
            "staleness": {
                "served_from": "cache",
                "last_known_at": now_str,
            },
        }
    elif state == "unavailable":
        surface = {
            "status": "unavailable",
            "staleness": {
                "served_from": "cache",
                "last_known_at": now_str,
            },
        }
    else:
        surface = {"status": "ok"}

    effective_source = source or "missing"
    surface["source"] = effective_source

    if effective_source == "local_snapshot":
        if surface.get("status") == "ok":
            surface["status"] = "degraded"
        surface["note"] = "Served from local BFF snapshot fallback instead of a backend-owned read store."
        surface["staleness"] = {
            "served_from": "local_snapshot",
            "last_known_at": snapshot_at or now_str,
        }
    elif effective_source == "legacy_incident_backfill":
        surface["status"] = "degraded"
        surface["note"] = (
            "Incident-derived loop reconstruction is a legacy backfill view; "
            "it is not canonical lifecycle-projector or live controller truth."
        )
        surface["projection_mode"] = "backfill"
        surface["accepted_live"] = False
        surface["staleness"] = {
            "served_from": "legacy_incident_backfill",
            "last_known_at": snapshot_at or now_str,
        }
    elif effective_source == "missing":
        surface["status"] = "unavailable"
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at or now_str},
        )

    if has_data is False:
        if surface.get("status") == "ok":
            surface["status"] = "unavailable"
        if missing_message:
            surface["message"] = missing_message
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at or now_str},
        )

    return surface


_default_surface_status = format_dataset_surface_status


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
    persona_reader: Optional[Callable[[Optional[str]], Optional[Dict[str, Any]]]] = None
    service: Optional[ResearchRouterService] = None

    def __post_init__(self):
        if self.service is None:
            self.service = ResearchRouterService(
                port_getter=self.get_read_store,
                utc_now=self.utc_now,
                snapshot_meta=self.snapshot_meta,
                page_slice=self.page_slice,
                bff_error=self.bff_error,
                dataset_surface_status=self.dataset_surface_status,
                get_capabilities=self.get_capabilities,
                list_synthesis_conflict_logs_reader=self.list_synthesis_conflict_logs,
                get_synthesis_conflict_log_reader=self.get_synthesis_conflict_log,
                cross_entity_search_fn=self.cross_entity_search,
                build_knowledge_workbench=self.build_knowledge_workbench,
                persona_reader=self.persona_reader,
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
            error_code = ErrorCode.__members__.get(exc.error_code, ErrorCode.VALIDATION_FAILED)
            details = getattr(exc, "details", None) or {}
            try:
                sig = inspect.signature(self.bff_error)
                has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                has_details_extra = "details_extra" in sig.parameters
            except (ValueError, TypeError):
                has_kwargs = True
                has_details_extra = False

            call_kwargs: Dict[str, Any] = {"precondition_failed": exc.field}
            if has_details_extra:
                call_kwargs["details_extra"] = details
            if has_kwargs or not has_details_extra:
                call_kwargs.update(details)

            raise self.bff_error(
                exc.status_code,
                error_code,
                str(exc),
                str(exc),
                **call_kwargs,
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

    def page(
        self,
        records: List[Dict[str, Any]],
        request: Request,
        default_size: int = 20,
        *,
        allow_limit: Optional[bool] = None,
        min_size: int = 1,
        max_size: int = 200,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if allow_limit is None:
            path = getattr(request, "url", None)
            path_str = str(getattr(path, "path", "") or "").rstrip("/")
            allow_limit = path_str.endswith("/bff/search")
        limit_val = self.query(request, "limit") if allow_limit else None
        page_size_val = self.query(request, "page_size")
        raw_size = limit_val if limit_val is not None else page_size_val
        try:
            page_size = int(raw_size if raw_size is not None else default_size)
        except (TypeError, ValueError):
            page_size = default_size
        page_size = max(min_size, min(page_size, max_size))
        return self.page_slice(records, self.query(request, "page_token"), page_size)

    def meta(self, snapshot_at: str, surface_name: str, dataset: str, has_data: bool) -> Dict[str, Any]:
        surface = self.service.dataset_surface(dataset, snapshot_at=snapshot_at, has_data=has_data)
        result = dict(self.snapshot_meta(snapshot_at))
        result["surfaces"] = {surface_name: surface}
        return result

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
