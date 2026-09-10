from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

_DEFAULT_IDENTITY = OperatorIdentity(
    operator_id="test-operator",
    roles=["operator", "viewer", "reviewer", "approver", "admin"],
    claims={},
)


import os


def make_governance_dataset_surface_status(store: Any) -> Callable[..., Dict[str, Any]]:
    def _status(
        dataset: str,
        *,
        snapshot_at: Optional[str] = None,
        source: Optional[str] = None,
        has_data: Optional[bool] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        state = os.getenv("BFF_READ_SURFACE_STATE", "fresh")
        resolved_source = source
        if resolved_source is None and hasattr(store, "dataset_source"):
            resolved_source = store.dataset_source(dataset)
        status = "ok"
        if state in ("degraded", "stale") or resolved_source == "local_snapshot":
            status = "degraded"
        if resolved_source in ("missing", "unavailable") or has_data is False:
            status = "unavailable"
            resolved_source = "missing"
        return {
            "status": status,
            "source": resolved_source or "typed_store",
            "dataset": dataset,
        }
    return _status


def create_governance_test_app(
    read_surface: Optional[Any] = None,
    *,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[..., Any]] = None,
    require_operator_role: Optional[Callable[..., Any]] = None,
    dataset_surface_status: Optional[Callable[..., Any]] = None,
    **extra_router_kwargs: Any,
) -> FastAPI:
    app = FastAPI(title="Governance Test App")

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Any, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail}, headers=exc.headers)

    store = read_surface if read_surface is not None else create_in_memory_read_surface_ports()

    router_kw: Dict[str, Any] = {
        "read_surface": store,
        "extract_identity": extract_identity or (lambda auth=None: _DEFAULT_IDENTITY),
        "require_read_role": require_read_role or (lambda identity: None),
        "require_operator_role": require_operator_role or (lambda identity: None),
        "dataset_surface_status": dataset_surface_status or make_governance_dataset_surface_status(store),
    }
    router_kw.update(extra_router_kwargs)
    app.include_router(create_governance_router(**router_kw))
    return app


def create_governance_test_client(
    read_surface: Optional[Any] = None,
    **kwargs: Any,
) -> TestClient:
    app = create_governance_test_app(read_surface, **kwargs)
    return TestClient(app, raise_server_exceptions=False)
