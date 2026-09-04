"""Prepared BFF core composition for the 30-route core assignment.

This module intentionally does not import or mutate ``main.py``.  The later
main-assembly task will inject the existing domain handlers and replace the
legacy decorators with these named routers.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, get_args, get_origin, get_type_hints

from fastapi import APIRouter, Body, FastAPI, Header, HTTPException, Request
from fastapi.params import Body as BodyParam
from fastapi.params import Header as HeaderParam
from fastapi.params import Path as PathParam
from fastapi.params import Query as QueryParam
from fastapi.routing import APIRoute

from auth.router import create_auth_router
from auth.service import AuthFacadeService
from core.lifespan import create_lifespan


RouteHandler = Callable[[Request], Any]
IdentityExtractor = Callable[..., Any]
RoleGuard = Callable[..., None]

ROUTE_ASSIGNMENTS: tuple[tuple[str, str, str], ...] = (
    ("POST", "/bff/auth/dev-login", "bff_auth_dev_login"),
    ("GET", "/bff/me", "bff_me"),
    ("GET", "/bff/auth/readiness", "bff_auth_readiness"),
    ("POST", "/bff/auth/refresh", "bff_auth_refresh"),
    ("POST", "/bff/logout", "bff_logout"),
    ("POST", "/bff/switch-tenant", "bff_switch_tenant"),
    ("PATCH", "/bff/me/locale", "bff_update_locale"),
    ("GET", "/health", "health"),
    ("GET", "/api/v1/settings", "get_settings"),
    ("POST", "/api/v1/settings", "update_settings"),
    ("GET", "/api/v1/settings/export", "export_settings"),
    ("POST", "/api/v1/settings/import", "import_settings"),
    ("POST", "/bff/management/nl/ask", "bff_management_nl_ask"),
    ("POST", "/bff/management/nl/ask/stream", "bff_management_nl_ask_stream"),
    ("GET", "/bff/management/ai/audit", "bff_management_ai_audit"),
    ("GET", "/bff/assistant/providers/usage-summary", "bff_assistant_provider_usage_summary"),
    ("GET", "/bff/management/ai/conversations", "bff_management_ai_conversations"),
    ("GET", "/bff/management/ai/conversations/{session_id}", "bff_management_ai_conversation"),
    ("GET", "/bff/management/ai/attachments/{attachment_id}", "bff_management_ai_attachment"),
    ("GET", "/bff/management/readiness/ep5", "bff_management_readiness_ep5"),
    ("GET", "/bff/management/readiness/broker-live", "bff_management_readiness_broker_live"),
    ("GET", "/bff/management/readiness/capital-binding-live", "bff_management_readiness_capital_binding_live"),
    ("GET", "/bff/management/readiness/bff-ha", "bff_management_readiness_bff_ha"),
    ("GET", "/bff/management/readiness/strict-publish", "bff_management_readiness_strict_publish"),
    ("GET", "/bff/types", "bff_types_compat"),
    ("GET", "/bff/version", "sem_bff_version"),
    ("GET", "/bff/healthz", "sem_bff_health_alias"),
    ("GET", "/bff/readyz", "sem_bff_readiness_alias"),
    ("GET", "/bff/capabilities", "sem_bff_capabilities"),
    ("GET", "/bff/feature-flags", "sem_bff_capabilities"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _missing_handler(name: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": {
                "code": "DEPENDENCY_UNAVAILABLE",
                "message": f"Core handler {name!r} is not assembled",
            }
        },
    )


_MISSING = object()


def _coerce_request_value(value: Any, annotation: Any) -> Any:
    """Apply the small set of scalar conversions FastAPI normally performs.

    The assembled legacy handlers retain their FastAPI ``Header``/``Query``/
    ``Body`` defaults.  Core-router wrappers receive a ``Request`` instead, so
    this bridge resolves those defaults explicitly before invoking the handler.
    """
    if value is None or annotation is inspect.Parameter.empty:
        return value
    origin = get_origin(annotation)
    if origin is not None:
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if args:
            return _coerce_request_value(value, args[0])
        return value
    try:
        if annotation is bool and isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if annotation is int and not isinstance(value, int):
            return int(value)
        if annotation is float and not isinstance(value, float):
            return float(value)
    except (TypeError, ValueError):
        return value
    return value


async def _dispatch(
    handlers: Mapping[str, RouteHandler],
    name: str,
    request: Request,
    **path_params: Any,
) -> Any:
    handler = handlers.get(name)
    if handler is None:
        raise _missing_handler(name)

    # The core router is intentionally a thin compatibility layer around the
    # existing handlers in ``main.py``.  Those handlers were originally
    # FastAPI endpoints, so their arguments are declared with dependency
    # markers (Header/Query/Body) rather than a Request parameter.  Calling
    # them positionally with the wrapper Request silently turns the Request
    # into the JSON payload (and made zero-argument handlers raise TypeError).
    # Resolve the declared inputs here so both old and newly assembled handlers
    # receive the same values they would get from FastAPI's injector.
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        value = handler(request)
    else:
        try:
            type_hints = get_type_hints(handler)
        except (NameError, TypeError, ValueError):
            type_hints = {}
        body: Any = _MISSING
        kwargs: dict[str, Any] = {}
        for parameter in signature.parameters.values():
            name_ = parameter.name
            annotation = type_hints.get(name_, parameter.annotation)
            default = parameter.default

            if name_ in path_params:
                kwargs[name_] = _coerce_request_value(path_params[name_], annotation)
                continue
            if name_ == "request" or annotation is Request:
                kwargs[name_] = request
                continue

            if isinstance(default, BodyParam):
                if body is _MISSING:
                    try:
                        body = await request.json()
                    except Exception:
                        body = _MISSING
                if body is not _MISSING:
                    kwargs[name_] = body
                elif getattr(default, "default_factory", None) is not None:
                    kwargs[name_] = default.default_factory()
                elif getattr(default, "default", _MISSING) is not _MISSING:
                    kwargs[name_] = default.default
                continue

            if isinstance(default, HeaderParam):
                alias = getattr(default, "alias", None) or name_.replace("_", "-")
                raw = request.headers.get(alias)
                if raw is None:
                    raw = getattr(default, "default", None)
                kwargs[name_] = _coerce_request_value(raw, annotation)
                continue

            if isinstance(default, (QueryParam, PathParam)):
                alias = getattr(default, "alias", None) or name_
                raw = request.query_params.get(alias)
                if raw is not None:
                    kwargs[name_] = _coerce_request_value(raw, annotation)
                elif getattr(default, "default", _MISSING) is not _MISSING:
                    kwargs[name_] = default.default
                continue

            # Plain optional arguments on the legacy endpoints are query
            # parameters.  Leave absent values to the function default.
            raw = request.query_params.get(name_)
            if raw is not None:
                kwargs[name_] = _coerce_request_value(raw, annotation)

        value = handler(**kwargs)
    if inspect.isawaitable(value):
        return await value
    return value


def create_settings_router(
    *,
    settings_store: Any,
    extract_identity: IdentityExtractor,
    require_admin_mfa: RoleGuard,
) -> APIRouter:
    """Create settings routes with no provider-readiness dependency."""
    router = APIRouter(tags=["settings"])

    @router.get("/api/v1/settings")
    async def get_settings(authorization: Optional[str] = Header(default=None)):
        extract_identity(authorization)
        return settings_store.get()

    @router.post("/api/v1/settings")
    async def update_settings(
        body: dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    ):
        identity = extract_identity(authorization, mfa_token=x_mfa_token)
        require_admin_mfa(identity, "update_settings")
        try:
            settings = settings_store.update(body.get("settings", body))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"settings": settings}

    @router.get("/api/v1/settings/export")
    async def export_settings(authorization: Optional[str] = Header(default=None)):
        extract_identity(authorization)
        return {"jsonData": settings_store.export_json()}

    @router.post("/api/v1/settings/import")
    async def import_settings(
        body: dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    ):
        identity = extract_identity(authorization, mfa_token=x_mfa_token)
        require_admin_mfa(identity, "import_settings")
        json_data = body.get("jsonData")
        if not isinstance(json_data, str):
            raise HTTPException(status_code=400, detail="jsonData must be a string")
        try:
            settings = settings_store.import_json(json_data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"settings": settings}

    return router


def create_assistant_management_router(handlers: Mapping[str, RouteHandler]) -> APIRouter:
    router = APIRouter(tags=["assistant-management"])

    @router.post("/bff/management/nl/ask")
    async def bff_management_nl_ask(request: Request):
        return await _dispatch(handlers, "bff_management_nl_ask", request)

    @router.post("/bff/management/nl/ask/stream")
    async def bff_management_nl_ask_stream(request: Request):
        return await _dispatch(handlers, "bff_management_nl_ask_stream", request)

    @router.get("/bff/management/ai/audit")
    async def bff_management_ai_audit(request: Request):
        return await _dispatch(handlers, "bff_management_ai_audit", request)

    @router.get("/bff/assistant/providers/usage-summary")
    async def bff_assistant_provider_usage_summary(request: Request):
        return await _dispatch(handlers, "bff_assistant_provider_usage_summary", request)

    @router.get("/bff/management/ai/conversations")
    async def bff_management_ai_conversations(request: Request):
        return await _dispatch(handlers, "bff_management_ai_conversations", request)

    @router.get("/bff/management/ai/conversations/{session_id}")
    async def bff_management_ai_conversation(session_id: str, request: Request):
        return await _dispatch(handlers, "bff_management_ai_conversation", request, session_id=session_id)

    @router.get("/bff/management/ai/attachments/{attachment_id}")
    async def bff_management_ai_attachment(attachment_id: str, request: Request):
        return await _dispatch(handlers, "bff_management_ai_attachment", request, attachment_id=attachment_id)

    return router


def create_core_router(handlers: Mapping[str, RouteHandler]) -> APIRouter:
    router = APIRouter(tags=["core"])

    @router.get("/health")
    async def health(request: Request):
        handler = handlers.get("health")
        if handler is not None:
            return await _dispatch(handlers, "health", request)
        return {"status": "ok", "service": "operator-bff", "version": "0.2.0", "timestamp": _utc_now()}

    @router.get("/bff/management/readiness/ep5")
    async def bff_management_readiness_ep5(request: Request):
        return await _dispatch(handlers, "bff_management_readiness_ep5", request)

    @router.get("/bff/management/readiness/broker-live")
    async def bff_management_readiness_broker_live(request: Request):
        return await _dispatch(handlers, "bff_management_readiness_broker_live", request)

    @router.get("/bff/management/readiness/capital-binding-live")
    async def bff_management_readiness_capital_binding_live(request: Request):
        return await _dispatch(handlers, "bff_management_readiness_capital_binding_live", request)

    @router.get("/bff/management/readiness/bff-ha")
    async def bff_management_readiness_bff_ha(request: Request):
        return await _dispatch(handlers, "bff_management_readiness_bff_ha", request)

    @router.get("/bff/management/readiness/strict-publish")
    async def bff_management_readiness_strict_publish(request: Request):
        return await _dispatch(handlers, "bff_management_readiness_strict_publish", request)

    @router.get("/bff/types")
    async def bff_types_compat(request: Request):
        return await _dispatch(handlers, "bff_types_compat", request)

    @router.get("/bff/version")
    async def sem_bff_version(request: Request):
        return await _dispatch(handlers, "sem_bff_version", request)

    @router.get("/bff/healthz")
    async def sem_bff_health_alias(request: Request):
        return await _dispatch(handlers, "sem_bff_health_alias", request)

    @router.get("/bff/readyz")
    async def sem_bff_readiness_alias(request: Request):
        return await _dispatch(handlers, "sem_bff_readiness_alias", request)

    @router.get("/bff/capabilities")
    @router.get("/bff/feature-flags")
    async def sem_bff_capabilities(request: Request):
        return await _dispatch(handlers, "sem_bff_capabilities", request)

    return router


def _default_extract_identity(_authorization: Optional[str], **_kwargs: Any) -> object:
    return object()


def _default_require_admin_mfa(_identity: Any, _action: str) -> None:
    return None


def create_app(
    *,
    auth_service: Optional[AuthFacadeService] = None,
    settings_store: Any = None,
    extract_identity: IdentityExtractor = _default_extract_identity,
    require_admin_mfa: RoleGuard = _default_require_admin_mfa,
    handlers: Optional[Mapping[str, RouteHandler]] = None,
    provider_refresh_interval_seconds: float = 30.0,
    enable_provider_refresh: bool = True,
) -> FastAPI:
    """Build the standalone core slice without importing the legacy app."""
    service = auth_service or AuthFacadeService()
    lifespan = (
        create_lifespan(
            service.provider_readiness_cache,
            interval_seconds=provider_refresh_interval_seconds,
        )
        if enable_provider_refresh
        else None
    )
    app = FastAPI(title="Pantheon Operator BFF Core", version="0.2.0", lifespan=lifespan)
    route_handlers = dict(handlers or {})
    app.include_router(create_auth_router(service=service))
    if settings_store is None:
        settings_store = _UnavailableSettingsStore()
    app.include_router(
        create_settings_router(
            settings_store=settings_store,
            extract_identity=extract_identity,
            require_admin_mfa=require_admin_mfa,
        )
    )
    app.include_router(create_assistant_management_router(route_handlers))
    app.include_router(create_core_router(route_handlers))
    _assert_route_assignment(app)
    return app


create_core_app = create_app


class _UnavailableSettingsStore:
    def _raise(self, *_args: Any, **_kwargs: Any):
        raise _missing_handler("settings_store")

    get = update = export_json = import_json = _raise


def _assert_route_assignment(app: FastAPI) -> None:
    actual: set[tuple[str, str]] = set()
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            pending.extend(included_router.routes)
            continue
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method != "HEAD":
                actual.add((method, route.path))
    expected = {(method, path) for method, path, _ in ROUTE_ASSIGNMENTS}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RuntimeError(f"core route assignment mismatch: missing={missing}, extra={extra}")
