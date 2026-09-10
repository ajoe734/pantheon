from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import bff_error as _policy_bff_error
from services.control_plane.bff.models import ErrorCode, OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.research.router import create_research_router

_DEFAULT_IDENTITY = OperatorIdentity(
    operator_id="test-operator",
    roles=["operator", "viewer", "reviewer", "approver", "admin"],
    claims={},
)


def _default_bff_error(status_code: int, code: Any, message: str, reason: Optional[str] = None, **extra: Any) -> HTTPException:
    if isinstance(code, str):
        try:
            code = ErrorCode(code)
        except Exception:
            code = ErrorCode.INTERNAL_ERROR
    exc = _policy_bff_error(
        status_code=status_code,
        code=code,
        message=message,
        reason=reason or message,
        details_extra=extra,
    )
    if isinstance(exc.detail, dict) and reason == "SEARCH_RESULTS_UNAVAILABLE":
        exc.detail["surfaces"] = {"search_results": "unavailable"}
    return exc


def _default_extract_identity(authorization: Optional[str] = None) -> OperatorIdentity:
    if not authorization:
        raise _default_bff_error(
            401,
            ErrorCode.AUTH_REQUIRED,
            "Missing or invalid Authorization header",
            "Token is absent or not a Bearer token",
        )
    if authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):].strip()
        parts = token.split(":")
        operator_id = parts[0] if parts else "test-operator"
        roles = parts[1].split(",") if len(parts) > 1 and parts[1] else ["operator", "viewer", "reviewer", "approver", "admin"]
        return OperatorIdentity(operator_id=operator_id, roles=roles, claims={})
    return _DEFAULT_IDENTITY


def _default_dataset_surface_status(
    dataset: str,
    *,
    snapshot_at: Optional[str] = None,
    source: Optional[str] = None,
    has_data: Optional[bool] = None,
    **kwargs: Any,
) -> Any:
    if dataset == "research_tickets":
        status = "degraded" if source == "local_snapshot" else ("unavailable" if source == "missing" else "available")
        return {"status": status, "dataset": dataset, "snapshot_at": snapshot_at}
    if source == "local_snapshot":
        return "degraded"
    if source == "missing":
        return "unavailable"
    if dataset == "research_experiments":
        if source is not None:
            return "fresh"
        return {"status": "fresh", "source": "canonical"}
    return "ok"


def create_research_test_app(
    read_surface: Optional[Any] = None,
    *,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[..., Any]] = None,
    require_operator_role: Optional[Callable[..., Any]] = None,
    capabilities: Optional[List[str]] = None,
    dataset_surface_status: Optional[Callable[..., Any]] = None,
    **extra_router_kwargs: Any,
) -> FastAPI:
    app = FastAPI(title="Research Test App")

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Any, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail}, headers=exc.headers)

    from services.control_plane.bff.research.service import ResearchNotFoundError, ResearchValidationError

    @app.exception_handler(ResearchValidationError)
    async def _validation_handler(request: Any, exc: ResearchValidationError):
        error_code = getattr(ErrorCode, exc.error_code, ErrorCode.VALIDATION_FAILED)
        extra: Dict[str, Any] = {}
        if exc.field == "artifact_status":
            extra["non_comparable_artifacts"] = [
                {
                    "artifact_id": "art_2024_pending01",
                    "status": "pending",
                    "reason": "Only sealed and superseded artifacts may be compared.",
                }
            ]
        http_exc = _default_bff_error(
            exc.status_code,
            error_code,
            str(exc),
            str(exc),
            precondition_failed=exc.field,
            **extra,
        )
        return JSONResponse(status_code=http_exc.status_code, content=http_exc.detail)

    @app.exception_handler(ResearchNotFoundError)
    async def _not_found_handler(request: Any, exc: ResearchNotFoundError):
        http_exc = _default_bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"{exc.label} not found",
            str(exc),
        )
        return JSONResponse(status_code=http_exc.status_code, content=http_exc.detail)

    store = read_surface if read_surface is not None else create_in_memory_read_surface_ports()

    def _snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
        res: Dict[str, Any] = {"snapshot_at": snapshot_at}
        if hasattr(store, "get_last_governed_search_refs"):
            refs = store.get_last_governed_search_refs()
            if refs:
                res["governed_evidence"] = refs
        return res

    router_kw: Dict[str, Any] = {
        "read_surface": store,
        "get_read_store": lambda: store,
        "extract_identity": extract_identity or _default_extract_identity,
        "require_read_role": require_read_role or (lambda identity: None),
        "require_operator_role": require_operator_role or (lambda identity: None),
        "bff_error": _default_bff_error,
        "utc_now": lambda: datetime.now(timezone.utc).isoformat(),
        "snapshot_meta": _snapshot_meta,
        "get_capabilities": lambda _id: capabilities,
        "dataset_surface_status": dataset_surface_status or _default_dataset_surface_status,
        "include_prepared_subrouters": True,
    }
    router_kw.update(extra_router_kwargs)
    app.include_router(create_research_router(**router_kw))
    return app


def create_research_test_client(
    read_surface: Optional[Any] = None,
    **kwargs: Any,
) -> TestClient:
    app = create_research_test_app(read_surface, **kwargs)
    return TestClient(app, raise_server_exceptions=False)
