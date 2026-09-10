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


def create_governance_test_app(
    read_surface: Optional[Any] = None,
    *,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[..., Any]] = None,
    require_operator_role: Optional[Callable[..., Any]] = None,
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
