"""Research artifacts routes."""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from fastapi import APIRouter, Header, Query, Request

from .common import (
    ResearchRouteContext,
    _authorization,
    _body_parameter,
    _path,
    _signature,
    _signature_query,
)
from services.control_plane.bff.core import display_names as _display_names_mod
from services.control_plane.bff.core.display_names import DisplayNameResolver, current_resolver
from services.control_plane.bff.core.owner_reads import (
    cached_owner_call,
    request_read_scope,
    selected_tenant,
    shared_persona_directory_list,
)
from services.control_plane.bff.personas.fleet_classification import (
    classify_artifact_row_list_scope,
    classify_persona_list_scope,
)
from services.control_plane.bff.personas.service import (
    _get_persona_directory_snapshot,
    _persona_record_projected_state,
)
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
from ..service import ResearchNotFoundError, ResearchValidationError

try:
    from services.control_plane.bff.models import ErrorCode
except (ImportError, ValueError):
    from ..models import ErrorCode

log = logging.getLogger(__name__)

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





def _annotate_artifact_names(names: DisplayNameResolver, row: Any) -> Any:
    """Add strategy/persona display names; registry rows name the artifact by its strategy id."""
    if not isinstance(row, dict):
        return row
    names.annotate(row, strategy_keys=())
    strategy_id = row.get("strategy_id") or (
        row.get("name") if row.get("source_kind") == "registry" else None
    )
    if strategy_id:
        label, missing = names.strategy(strategy_id)
        row.setdefault("strategy_display_name", label)
        if missing:
            row.setdefault("strategy_name_missing", True)
    return row


def _invoke_persona_directory_snapshot(
    snapshot_fn: Callable[..., Any],
    *,
    tenant_id: str,
    snapshot_at: str,
    read_store: Optional[Any] = None,
) -> Any:
    def _call() -> Any:
        try:
            params = inspect.signature(snapshot_fn).parameters
            has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
            accepts_pos = any(
                p.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.VAR_POSITIONAL,
                )
                for p in params.values()
            )
        except (TypeError, ValueError):
            params = {}
            has_var_kw = False
            accepts_pos = True
        kwargs: Dict[str, Any] = {}
        if has_var_kw or "snapshot_at" in params:
            kwargs["snapshot_at"] = snapshot_at
        if has_var_kw or "reuse_request_read" in params:
            kwargs["reuse_request_read"] = True
        if read_store is not None and (has_var_kw or "read_store" in params):
            kwargs["read_store"] = read_store
        if accepts_pos:
            return snapshot_fn(tenant_id, **kwargs)
        if "tenant_id" in params or has_var_kw:
            kwargs["tenant_id"] = tenant_id
        return snapshot_fn(**kwargs)

    return cached_owner_call(
        ("artifact_persona_directory_snapshot", id(snapshot_fn), id(read_store), tenant_id),
        _call,
    )


def _extract_persona_scopes_from_directory(raw_directory: Any) -> Dict[str, str]:
    if (
        isinstance(raw_directory, tuple)
        and len(raw_directory) == 2
        and isinstance(raw_directory[1], Mapping)
        and "status" in raw_directory[1]
    ):
        if str(raw_directory[1].get("status") or "").strip().lower() == "unavailable":
            raise RuntimeError("persona_directory_unavailable")
        raw_directory = raw_directory[0]
    if hasattr(raw_directory, "records_by_id"):
        records_by_id = getattr(raw_directory, "records_by_id", None)
        if not isinstance(records_by_id, Mapping):
            raise RuntimeError("invalid_persona_directory_snapshot")
        records_iter: Iterable[Any] = records_by_id.values()
    elif isinstance(raw_directory, Mapping):
        records_iter = raw_directory.values()
    elif isinstance(raw_directory, Iterable) and not isinstance(raw_directory, (str, bytes)):
        records_iter = raw_directory
    else:
        raise RuntimeError("invalid_persona_directory_payload")

    scopes: Dict[str, str] = {}
    for record in records_iter:
        if not isinstance(record, Mapping):
            raise RuntimeError("invalid_persona_directory_record")
        pid = str(record.get("persona_id") or record.get("id") or "").strip()
        if pid:
            projected = _persona_record_projected_state(record)
            scopes[pid] = classify_persona_list_scope(record, projected_state=projected)
    return scopes


def _resolve_artifact_list_scope_inputs(
    *,
    service: Optional[Any],
    identity: Optional[Any],
    snapshot_at: str,
    needs_strategy_bindings: bool,
) -> Tuple[bool, Optional[str], Dict[str, str], Optional[Dict[str, List[str]]]]:
    tenant_id = str(
        getattr(identity, "tenant_id", None)
        or selected_tenant.get()
        or ""
    ).strip()
    configured_source = getattr(_display_names_mod, "_source", None)
    raw_port = (
        service.port_getter()
        if service is not None and callable(getattr(service, "port_getter", None))
        else None
    )
    service_port = (
        service._port()
        if service is not None and callable(getattr(service, "_port", None))
        else raw_port
    )
    composed_store = _display_names_mod._composed_read_store()
    snapshot_fn = getattr(service, "persona_directory_snapshot", None) if service is not None else None

    try:
        if configured_source is not None:
            if callable(getattr(configured_source, "persona_directory_snapshot", None)):
                raw_dir = _invoke_persona_directory_snapshot(
                    configured_source.persona_directory_snapshot,
                    tenant_id=tenant_id,
                    snapshot_at=snapshot_at,
                )
            elif callable(getattr(configured_source, "list_personas", None)):
                raw_dir = shared_persona_directory_list(
                    configured_source, configured_source.list_personas
                )
            else:
                raise RuntimeError("persona_directory_unconfigured")
        elif callable(snapshot_fn):
            raw_dir = _invoke_persona_directory_snapshot(
                snapshot_fn,
                tenant_id=tenant_id,
                snapshot_at=snapshot_at,
                read_store=raw_port if isinstance(raw_port, ReadSurfacePorts) else None,
            )
        elif raw_port is not None and callable(getattr(raw_port, "persona_directory_snapshot", None)):
            raw_dir = _invoke_persona_directory_snapshot(
                raw_port.persona_directory_snapshot,
                tenant_id=tenant_id,
                snapshot_at=snapshot_at,
            )
        elif isinstance(raw_port, ReadSurfacePorts):
            raw_dir = _invoke_persona_directory_snapshot(
                _get_persona_directory_snapshot,
                tenant_id=tenant_id,
                snapshot_at=snapshot_at,
                read_store=raw_port,
            )
        elif raw_port is not None and callable(getattr(raw_port, "list_personas", None)):
            raw_dir = shared_persona_directory_list(raw_port, raw_port.list_personas)
        elif composed_store is not None:
            raw_dir = _invoke_persona_directory_snapshot(
                _get_persona_directory_snapshot,
                tenant_id=tenant_id,
                snapshot_at=snapshot_at,
                read_store=composed_store,
            )
        else:
            raise RuntimeError("persona_directory_unconfigured")
        persona_scopes_by_id = _extract_persona_scopes_from_directory(raw_dir)
    except Exception as exc:  # noqa: BLE001 - directory failure degrades scope to unknown without dropping rows
        log.warning("artifact list_scope persona directory read failed: %s", exc)
        return False, f"persona_directory_read_failed:{type(exc).__name__}", {}, None

    if not needs_strategy_bindings:
        return True, None, persona_scopes_by_id, {}

    binding_fn: Optional[Callable[[], Dict[str, List[str]]]] = None
    binding_owner: Optional[Any] = None
    for candidate in (configured_source, service_port, raw_port, composed_store):
        if candidate is None:
            continue
        if callable(getattr(candidate, "get_strategy_persona_bindings", None)):
            binding_fn = candidate.get_strategy_persona_bindings
            binding_owner = candidate
            break
        nested_ks = getattr(candidate, "research_knowledge_source", None)
        if nested_ks is not None and callable(getattr(nested_ks, "get_strategy_persona_bindings", None)):
            binding_fn = nested_ks.get_strategy_persona_bindings
            binding_owner = nested_ks
            break

    if binding_fn is None:
        return True, None, persona_scopes_by_id, {}

    try:
        raw_bindings = cached_owner_call(
            ("artifact_strategy_persona_bindings", id(binding_owner), tenant_id or selected_tenant.get()),
            binding_fn,
        )
        if not isinstance(raw_bindings, Mapping):
            raise RuntimeError("invalid_strategy_persona_bindings")
        strategy_bindings = {
            str(sid): [str(pid).strip() for pid in (pids or ()) if str(pid).strip()]
            for sid, pids in raw_bindings.items()
            if isinstance(pids, (list, tuple, set))
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("artifact list_scope strategy binding read failed: %s", exc)
        return True, f"strategy_persona_bindings_failed:{type(exc).__name__}", persona_scopes_by_id, None

    return True, None, persona_scopes_by_id, strategy_bindings


@request_read_scope()
def _annotate_artifact_payload(
    payload: Dict[str, Any],
    *,
    service: Optional[Any] = None,
    identity: Optional[Any] = None,
) -> Dict[str, Any]:
    read_store = (
        service._port()
        if service is not None and callable(getattr(service, "_port", None))
        else None
    )
    names = current_resolver(read_store)
    unique_rows: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for key in ("items", "data"):
        value = payload.get(key)
        rows = value if isinstance(value, list) else [value]
        for row in rows:
            if isinstance(row, dict) and id(row) not in seen:
                seen.add(id(row))
                unique_rows.append(row)
                _annotate_artifact_names(names, row)

    if not unique_rows:
        return payload

    snapshot_at = str((payload.get("meta") or {}).get("snapshot_at") or "")
    needs_strategy_bindings = any(
        str(row.get("source_kind") or "").strip().lower() != "research_run_output"
        for row in unique_rows
    )
    persona_dir_ok, dir_reason, persona_scopes_by_id, strategy_bindings = (
        _resolve_artifact_list_scope_inputs(
            service=service,
            identity=identity,
            snapshot_at=snapshot_at,
            needs_strategy_bindings=needs_strategy_bindings,
        )
    )
    for row in unique_rows:
        row["list_scope"] = classify_artifact_row_list_scope(
            row,
            persona_directory_ok=persona_dir_ok,
            persona_scopes_by_id=persona_scopes_by_id,
            strategy_persona_bindings=strategy_bindings,
        )
    if not persona_dir_ok:
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            payload["meta"] = meta
        surfaces = meta.get("surfaces")
        if not isinstance(surfaces, dict):
            surfaces = {}
            meta["surfaces"] = surfaces
        surfaces["persona_directory"] = {
            "status": "unavailable",
            "source": "persona_directory",
            "reason": dir_reason or "persona_directory_unavailable",
        }
        meta["persona_directory_status"] = "unavailable"
    return payload


def build_artifacts_router(ctx: ResearchRouteContext) -> APIRouter:
    router = APIRouter()

    def _list_artifacts(
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

    def _get_artifact(artifact_id: str, authorization: Optional[str]) -> Dict[str, Any]:
        ctx.require_read_role(ctx.extract_identity(authorization))
        try:
            return ctx.service.get_artifact(artifact_id)
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            ctx.raise_service_error(exc)
            raise AssertionError("unreachable")

    @router.get("/api/v1/research/artifacts")
    def list_research_artifacts(
        artifact_type: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[str] = None,
        author: Optional[str] = None,
        date_range: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return _list_artifacts(
            artifact_type, status, tags, author, date_range, page_token, page_size, authorization,
        )

    @router.get("/api/v1/research/artifacts/compare")
    def compare_research_artifacts(
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
    def get_research_artifact(
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return _get_artifact(artifact_id, authorization)

    @router.get("/bff/artifacts")
    def bff_list_research_artifacts(
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        try:
            return _annotate_artifact_payload(
                ctx.service.list_artifact_catalog(
                    page_token=page_token,
                    page_size=page_size,
                ),
                service=ctx.service,
                identity=identity,
            )
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            ctx.raise_service_error(exc)
            raise AssertionError("unreachable")

    @router.get("/bff/artifacts/{artifact_id}")
    def bff_get_research_artifact(
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        try:
            return _annotate_artifact_payload(
                ctx.service.get_artifact_catalog_detail(artifact_id),
                service=ctx.service,
                identity=identity,
            )
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            ctx.raise_service_error(exc)
            raise AssertionError("unreachable")

    # Legacy inventory endpoints
    async def endpoint_compare_artifacts_api(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        try:
            return ctx.service.compare_artifacts(ctx.required_text({"artifact_ids": ctx.query(request, "artifact_ids")}, "artifact_ids"))
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            ctx.raise_service_error(exc)
            raise AssertionError("unreachable")

    async def endpoint_list_artifacts_api(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        status = _validate_artifact_status(ctx, ctx.query(request, "status"))
        return ctx.service.list_artifacts_legacy(
            experiment_id=ctx.query(request, "experiment_id"),
            ticket_id=ctx.query(request, "ticket_id"),
            lineage_id=ctx.query(request, "lineage_id"),
            status=status,
            page_token=ctx.query(request, "page_token"),
            page_size=int(ctx.query(request, "page_size") or 20),
            snapshot_at=ctx.utc_now(),
        )

    async def endpoint_get_artifact_api(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        artifact_id = str(request.path_params.get("artifact_id") or "")
        return ctx.service.get_artifact_legacy(artifact_id, snapshot_at=ctx.utc_now())

    async def endpoint_bff_patch_artifact(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request, operator=True)
        artifact_id = str(request.path_params.get("artifact_id") or "")
        ctx.service.patch_artifact_immutable(artifact_id)
        raise AssertionError("unreachable")

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
