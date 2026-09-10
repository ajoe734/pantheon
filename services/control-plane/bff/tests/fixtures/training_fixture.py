from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import bff_error as _policy_bff_error
from services.control_plane.bff.models import ErrorCode, OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.training.router import create_training_router

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
    return _policy_bff_error(
        status_code=status_code,
        code=code,
        message=message,
        reason=reason or message,
        details_extra=extra,
    )


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


def _default_page_slice(
    items: List[Dict[str, Any]],
    page_token: Optional[str],
    page_size: int,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    start = int(page_token) if page_token and page_token.isdigit() else 0
    end = start + page_size
    sliced = items[start:end]
    next_token = str(end) if end < len(items) else None
    return sliced, next_token


def make_dataset_surface_status(store: Any) -> Callable[..., Dict[str, Any]]:
    def _status(
        dataset: str,
        *,
        snapshot_at: Optional[str] = None,
        source: Optional[str] = None,
        has_data: Optional[bool] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        resolved_source = source
        if resolved_source is None and hasattr(store, "dataset_source"):
            resolved_source = store.dataset_source(dataset)
        if resolved_source == "missing" or has_data is False:
            return {"status": "unavailable", "source": "missing", "dataset": dataset}
        if resolved_source == "local_snapshot":
            return {"status": "degraded", "source": "local_snapshot", "dataset": dataset}
        return {"status": "ok", "source": resolved_source or "typed_store", "dataset": dataset}
    return _status


def create_training_test_app(
    read_surface: Optional[Any] = None,
    *,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[..., Any]] = None,
    dataset_surface_status: Optional[Callable[..., Any]] = None,
    **extra_router_kwargs: Any,
) -> FastAPI:
    app = FastAPI(title="Training Test App")

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Any, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail}, headers=exc.headers)

    store = read_surface if read_surface is not None else create_in_memory_read_surface_ports()

    router_kw: Dict[str, Any] = {
        "read_surface": store,
        "get_read_store": lambda: store,
        "extract_identity": extract_identity or _default_extract_identity,
        "require_read_role": require_read_role or (lambda identity: None),
        "bff_error": _default_bff_error,
        "utc_now": lambda: datetime.now(timezone.utc).isoformat(),
        "page_slice": _default_page_slice,
        "dataset_surface_status": dataset_surface_status or make_dataset_surface_status(store),
    }
    router_kw.update(extra_router_kwargs)
    app.include_router(create_training_router(**router_kw))
    try:
        from services.control_plane.bff.auth.policy import default_bff_me_tenant_payload
        from services.control_plane.bff.strategies.router import create_strategies_router
        app.include_router(create_strategies_router(
            read_surface=store,
            get_read_store=lambda: store,
            extract_identity=router_kw["extract_identity"],
            require_read_role=router_kw["require_read_role"],
            bff_error=router_kw["bff_error"],
            utc_now=router_kw["utc_now"],
            page_slice=router_kw["page_slice"],
            bff_me_tenant_payload=default_bff_me_tenant_payload,
        ))
    except Exception:
        pass
    return app


def create_training_test_client(
    read_surface: Optional[Any] = None,
    **kwargs: Any,
) -> TestClient:
    app = create_training_test_app(read_surface, **kwargs)
    return TestClient(app, raise_server_exceptions=False)
