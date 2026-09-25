"""Prepared BFF core composition for the 30-route core assignment.

This module intentionally does not import or mutate ``main.py``.  The later
main-assembly task will inject the existing domain handlers and replace the
legacy decorators with these named routers.
"""
from __future__ import annotations

import inspect
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, get_args, get_origin, get_type_hints

from fastapi import APIRouter, Body, FastAPI, Header, HTTPException, Request
from fastapi.params import Body as BodyParam
from fastapi.params import Header as HeaderParam
from fastapi.params import Path as PathParam
from fastapi.params import Query as QueryParam
from fastapi.routing import APIRoute


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


def bff_feature_flags() -> dict[str, bool]:
    ooda = os.getenv("PANTHEON_OODA_PACKET_ENABLED")
    ooda_enabled = True if ooda is None else ooda.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    synthesis = os.getenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED")
    synthesis_enabled = True if synthesis is None else synthesis.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    return {
        "executePlansBff": True,
        "sessionAuthMe": True,
        "oodaPackets": ooda_enabled,
        "synthesisConflictLogs": synthesis_enabled,
    }


def create_capabilities_handler(
    *,
    extract_identity: Optional[IdentityExtractor] = None,
    require_read_role: Optional[RoleGuard] = None,
    utc_now: Optional[Callable[[], str]] = None,
) -> Callable[..., Any]:
    effective_extract = extract_identity
    effective_require = require_read_role
    now_fn = utc_now or _utc_now

    async def sem_bff_capabilities(authorization: Optional[str] = Header(default=None)):
        nonlocal effective_extract, effective_require
        if effective_extract is None or effective_require is None:
            from ..auth import policy as auth_policy
            if effective_extract is None:
                effective_extract = auth_policy.extract_identity
            if effective_require is None:
                effective_require = auth_policy.require_read_role
        effective_require(effective_extract(authorization))
        return {
            "data": {
                "feature_flags": bff_feature_flags(),
            },
            "meta": {"snapshot_at": now_fn()},
        }

    return sem_bff_capabilities


sem_bff_capabilities_default = create_capabilities_handler()
sem_bff_capabilities = sem_bff_capabilities_default


def create_version_handler(
    *,
    source_commit_fn: Optional[Callable[[], str]] = None,
    auth_stub_fn: Optional[Callable[[], bool]] = None,
    auth_mode_fn: Optional[Callable[[], str]] = None,
    dev_login_fn: Optional[Callable[[], bool]] = None,
    image_digest: Optional[str] = None,
    build_time: Optional[str] = None,
    environment: Optional[str] = None,
) -> Callable[[], Any]:
    """Create a version handler for GET /bff/version.

    Extracted from main.py (BFF-MAIN-DI-SEAM-AND-SCAN-INTEGRITY-001).
    Allows exercising /bff/version without importing main.py.
    """
    async def sem_bff_version():
        from ..auth import policy as auth_policy
        commit = source_commit_fn() if source_commit_fn is not None else auth_policy.bff_source_commit()
        img_digest = image_digest or os.getenv("BFF_IMAGE_DIGEST") or os.getenv("IMAGE_DIGEST") or "unknown"
        b_time = build_time or os.getenv("BFF_BUILD_TIME") or os.getenv("BUILD_TIME") or "unknown"
        env = environment or os.getenv("PANTHEON_ENV") or os.getenv("ENVIRONMENT") or "unknown"

        auth_stub = auth_stub_fn() if auth_stub_fn is not None else auth_policy.bff_auth_stub_enabled()
        auth_mode = auth_mode_fn() if auth_mode_fn is not None else auth_policy.bff_auth_mode()
        dev_login = dev_login_fn() if dev_login_fn is not None else auth_policy.dev_login_enabled()

        config_posture = {
            "auth_stub": auth_stub,
            "auth_mode": auth_mode,
            "dev_login_enabled": dev_login,
            "mfa_required": auth_policy.bool_from_env("PANTHEON_BFF_MFA_REQUIRED", default=False),
            "assistant_kernel_enabled": auth_policy.bool_from_env("PANTHEON_ASSISTANT_KERNEL_ENABLED", default=False),
            "trade_journey_reader_backend": os.getenv(
                "PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND", "postgres"
            ).strip().lower(),
            "trade_journey_projection_schema": os.getenv(
                "PANTHEON_BFF_TRADE_JOURNEY_PROJECTION_SCHEMA",
                "trade_journey_projection",
            ).strip(),
        }

        return {
            "service": "operator-bff",
            "version": "0.2.0",
            "source_commit_sha": commit,
            "commit": commit,
            "source_commit_known": bool(re.fullmatch(r"[0-9a-fA-F]{40}", commit)),
            "image_digest": img_digest,
            "build_time": b_time,
            "environment": env,
            "config_posture": config_posture,
        }

    return sem_bff_version


sem_bff_version_default = create_version_handler()
sem_bff_version = sem_bff_version_default


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
        if "sem_bff_capabilities" in handlers:
            return await _dispatch(handlers, "sem_bff_capabilities", request)
        return await _dispatch({"sem_bff_capabilities": sem_bff_capabilities_default}, "sem_bff_capabilities", request)

    return router


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


def build_bff_app(
    *,
    lifespan: Optional[Any] = None,
    dev_login_enabled: Optional[Callable[[], bool]] = None,
    origin_allowed: Optional[Callable[[Optional[str]], bool]] = None,
    validate_session: Optional[Callable[[str], Any]] = None,
    title: str = "Pantheon Operator BFF",
    version: str = "0.2.0",
) -> FastAPI:
    """Build and configure the single Operator BFF FastAPI application.

    Wires browser session middleware, CORS preflight and allowed origins,
    pure-ASGI security headers, and Pack D exception handlers.
    """
    from ..auth.browser_session import DevBrowserSessionMiddleware
    from .http_security import (
        _CORS_ALLOW_HEADERS,
        _CORS_EXPOSE_HEADERS,
        _LOVABLE_PREVIEW_ORIGIN_REGEX,
        _PantheonCORSMiddleware,
        _SecurityHeadersMiddleware,
        _cors_origin_allowed,
        _cors_origins_from_env,
        _is_production_strict_mode,
    )
    from .errors import register_error_handlers

    effective_origin_allowed = origin_allowed or _cors_origin_allowed
    cors_origins = _cors_origins_from_env()
    strict = _is_production_strict_mode()
    preview_regex = None if strict else _LOVABLE_PREVIEW_ORIGIN_REGEX

    app_kwargs: dict[str, Any] = {"title": title, "version": version}
    if lifespan is not None:
        app_kwargs["lifespan"] = lifespan

    built_app = FastAPI(**app_kwargs)

    if dev_login_enabled is not None and validate_session is not None:
        built_app.add_middleware(
            DevBrowserSessionMiddleware,
            enabled=dev_login_enabled,
            origin_allowed=effective_origin_allowed,
            validate_session=validate_session,
        )

    if cors_origins or preview_regex:
        middleware_kwargs: dict[str, Any] = dict(
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=_CORS_ALLOW_HEADERS,
            expose_headers=_CORS_EXPOSE_HEADERS,
        )
        if preview_regex:
            middleware_kwargs["allow_origin_regex"] = preview_regex
        built_app.add_middleware(_PantheonCORSMiddleware, **middleware_kwargs)

    built_app.add_middleware(_SecurityHeadersMiddleware)
    register_error_handlers(built_app, origin_allowed_fn=effective_origin_allowed)

    return built_app


_SSE_CHANNEL_CATALOG_FALLBACK: tuple[str, ...] = (
    "approval",
    "ask",
    "artifact",
    "runtime",
    "mcp",
    "skill",
    "channel",
    "tool",
    "ranking",
    "rebalance",
    "evolution",
    "research",
    "signal",
    "inbox",
    "journal",
    "postmortem",
    "loop",
    "sentinel",
    "intervention",
    "audit",
    "system",
)


def _resolve_default_dependency(name: str, app_deps: Any) -> Any:
    # 1. Auth policy
    if name == "_extract_identity":
        from ..auth.policy import extract_identity
        return extract_identity
    if name == "_require_read_role":
        from ..auth.policy import require_read_role
        return require_read_role
    if name == "_require_operator_role":
        from ..auth.policy import require_operator_role
        return require_operator_role
    if name == "_require_admin_mfa":
        from ..auth.policy import require_admin_mfa
        return require_admin_mfa
    if name == "_bff_error":
        from ..auth.policy import bff_error
        return bff_error
    if name == "_WRITE_ROLES":
        from ..auth.policy import _WRITE_ROLES
        return _WRITE_ROLES
    if name in {"_bff_auth_mode", "bff_auth_mode"}:
        from ..auth.policy import bff_auth_mode
        return bff_auth_mode
    if name in {"_bff_auth_stub_enabled", "bff_auth_stub_enabled"}:
        from ..auth.policy import bff_auth_stub_enabled
        return bff_auth_stub_enabled
    if name in {"_bff_source_commit", "bff_source_commit"}:
        from ..auth.policy import bff_source_commit
        return bff_source_commit
    if name in {"_bff_me_tenant_payload", "bff_me_tenant_payload"}:
        from ..auth.policy import bff_me_tenant_payload
        return bff_me_tenant_payload
    if name in {"_dev_login_forbidden_environment", "dev_login_forbidden_environment"}:
        from ..auth.policy import dev_login_forbidden_environment
        return dev_login_forbidden_environment
    if name in {"_dev_login_identity_registry", "dev_login_identity_registry"}:
        from ..auth.policy import dev_login_identity_registry
        return dev_login_identity_registry
    if name in {"_capabilities_for_identity", "capabilities_for_identity"}:
        from ..auth.policy import capabilities_for_identity
        return capabilities_for_identity

    # 2. HTTP Security
    if name == "_cors_origin_allowed":
        from .http_security import _cors_origin_allowed
        return _cors_origin_allowed

    # 3. Models
    if name == "utc_now":
        from ..models import utc_now
        return utc_now
    if name == "redact_evidence_refs":
        from ..models import redact_evidence_refs
        return redact_evidence_refs
    if name == "ObjectType":
        from ..models import ObjectType
        return ObjectType
    if name == "CommandType":
        from ..models import CommandType
        return CommandType

    # 4. Assistant & Management NL
    if name == "bff_management_nl_ask":
        from ..assistant.management_service import bff_management_nl_ask
        return bff_management_nl_ask
    if name == "bff_management_nl_ask_stream":
        from ..assistant.management_service import bff_management_nl_ask_stream
        return bff_management_nl_ask_stream

    # 5. Shared bounded read
    if name in {"run_management_read", "_run_management_read"}:
        from ..personas.routes.common import run_management_read
        return run_management_read

    # 6. Preconditions & execution
    if name == "_mutation_review_projection":
        from ..command_adapters.preconditions import _mutation_review_projection
        return _mutation_review_projection
    if name == "create_capital_binding":
        from ..command_executor import create_capital_binding
        return create_capital_binding
    if name == "_pm12_performance_attribution_response":
        from ..pm12.service import _pm12_performance_attribution_response
        return _pm12_performance_attribution_response
    if name == "_surface_degradation_reason":
        from ..shared.cross_domain_utils import _surface_degradation_reason
        return _surface_degradation_reason

    # 7. Personas
    if name == "_normalize_lifecycle_state":
        from ..personas.service import _normalize_lifecycle_state
        return _normalize_lifecycle_state
    if name == "_normalize_risk_level":
        from ..personas.service import _normalize_risk_level
        return _normalize_risk_level
    if name == "_get_persona_directory_snapshot":
        from ..personas.service import _get_persona_directory_snapshot
        return _get_persona_directory_snapshot
    if name == "_persona_record_tenant_id":
        from ..personas.service import _persona_record_tenant_id
        return _persona_record_tenant_id

    # 8. Constants & configuration
    if name == "SSE_CHANNELS":
        return set(_SSE_CHANNEL_CATALOG_FALLBACK)
    if name == "_GOVERNANCE_APPROVAL_QUEUE_ROUTE":
        return "/api/v1/operator/governance/approvals"
    if name == "_PM12_ATTRIBUTION_DIMENSIONS":
        return ("strategy_id", "persona_id", "model_id", "asset_class", "time_horizon")
    if name == "_REQUEST_DRY_RUN_CONTEXT":
        from contextvars import ContextVar
        return ContextVar("request_dry_run_context", default=False)

    # 9. Default Stores
    if name == "settings_store":
        return app_deps.settings_store
    if name == "session_lifecycle_store":
        from ..session_lifecycle_store import SessionLifecycleStore
        return SessionLifecycleStore(os.path.join(os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff"), "session_lifecycle.json"))
    if name == "provider_readiness_cache":
        from ..auth.service import ProviderReadinessCache
        return ProviderReadinessCache(probe=lambda: {"ready": True}, provider="openclaw")
    if name in {
        "_GOV_BFF_IDEMPOTENCY", "gov_bff_idempotency", "_AGORA_CORE_BFF_IDEMPOTENCY",
        "_STRATEGY_PERSONA_BFF_IDEMPOTENCY", "_STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY",
        "_STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY", "_ACKNOWLEDGED_ALERTS", "acknowledged_alerts",
        "idempotency_ledger", "capital_bff_idempotency_store", "_capital_bff_idempotency_store",
    }:
        return {}
    if name in {"_incident_events", "incident_events"}:
        return []
    if name in {"_incident_subscribers", "incident_subscribers"}:
        return set()
    if name in {"_sse_buffers", "sse_buffers"}:
        from collections import defaultdict
        return defaultdict(list, {ch: [] for ch in _SSE_CHANNEL_CATALOG_FALLBACK})
    if name in {"_sse_subscribers", "sse_subscribers"}:
        from collections import defaultdict
        return defaultdict(set, {ch: set() for ch in _SSE_CHANNEL_CATALOG_FALLBACK})

    # Safe callables
    if name in {"_page_slice", "page_slice_fn", "page_slice"}:
        return lambda *a, **kw: ([], 0, False)
    if name in {"_snapshot_meta", "snapshot_meta", "snapshot_meta_fn"}:
        from ..models import utc_now
        return lambda *a, **kw: {"snapshot_at": utc_now()}
    if name in {
        "_dataset_surface_status", "dataset_surface_status", "dataset_surface_status_fn",
        "_composed_surface_status", "composed_surface_status",
        "_composed_dataset_surface_status", "composed_dataset_surface_status",
    }:
        return lambda *a, **kw: "available"
    if name in {"_read_surface_meta", "read_surface_meta"}:
        return lambda *a, **kw: {}
    if name in {
        "_raise_if_read_surface_unavailable", "raise_if_read_surface_unavailable",
        "raise_if_read_surface_unavailable_fn", "_raise_if_session_logged_out",
        "_reject_body_idempotency_key", "reject_body_idempotency_key", "reject_body_idempotency_key_fn",
        "_require_ooda_packet_routes_enabled", "_require_journal_write_role",
        "_require_agora_signal_write_role", "_require_agora_bulk_feedback_role",
        "_capital_bff_idempotency_check", "capital_bff_idempotency_check",
        "_strategy_persona_idempotency_check", "strategy_persona_idempotency_check",
    }:
        return lambda *a, **kw: None
    if name in {"_resolve_final_idempotency_key", "resolve_final_idempotency_key", "resolve_final_idempotency_key_fn"}:
        return lambda k, d=None: k or d or "default-key"
    if name in {"_request_dry_run_requested", "request_dry_run_requested", "_truthy_header", "dry_run_resolver"}:
        return lambda *a, **kw: False
    if name in {"_dry_run_success_response", "dry_run_success_response"}:
        return lambda *a, **kw: {"status": "dry_run"}
    if name in {"_meta_staleness", "meta_staleness"}:
        return lambda *a, **kw: 0.0
    if name in {"_stable_json_hash", "stable_json_hash"}:
        return lambda *a, **kw: "hash"
    if name in {"_split_csv_query", "split_csv_query"}:
        return lambda v: [x.strip() for x in (v or "").split(",") if x.strip()]
    if name in {
        "_handle_sse_stream", "handle_sse_stream", "_publish_event", "publish_event",
        "publish_event_fn", "stream_generic_events",
    }:
        return lambda *a, **kw: None
    if name in {"_build_operator_alerts_payload", "build_operator_alerts_payload"}:
        return lambda s: {}
    if name in {
        "_build_management_cockpit_payload", "build_cockpit_payload",
        "_build_management_evidence_payload", "build_evidence_payload",
        "_project_operator_runtime_state_row", "_read_surface_state",
        "_ooda_packet_list_payload", "ooda_packet_list_payload",
        "_assistant_build_context_pack", "build_context_pack",
        "_assistant_provider_readiness", "provider_readiness",
        "_assistant_provider_register", "provider_register",
        "_assistant_provider_reauth", "provider_reauth",
        "_assistant_provider_reauth_status", "provider_reauth_status",
        "_assistant_provider_reauth_code", "provider_reauth_code",
        "_ensure_agora_servant_openclaw_agent", "sync_servant_agent",
        "_resolve_agora_interaction_context_ref", "canonical_context_ref_resolver",
    }:
        return lambda *a, **kw: {}
    if name in {
        "_read_management_source_connector_registry", "read_source_connector_registry",
        "_v5_intervention_records", "intervention_records_provider",
        "_list_governance_audit_events", "list_governance_audit_events",
        "_list_persona_records", "list_persona_records",
        "_list_strategy_summaries", "list_strategy_summaries",
        "_assistant_provider_list", "provider_list",
    }:
        return lambda *a, **kw: []
    if name in {
        "_gov_bff_action_command", "gov_bff_action_command",
        "_capital_bff_action_command", "capital_bff_action_command",
        "_evol_exp_bff_action_command", "submit_job_action",
        "submit_program_action", "submit_experiment_action",
        "_strategy_persona_action_command", "strategy_persona_action_command",
        "_submit_final_command_admission", "submit_command", "submit_final_command_admission",
        "_sem_command_response", "sem_command_response", "submit_sem_command",
        "_aggregate_group_surface", "aggregate_group_surface",
    }:
        return lambda *a, **kw: {}
    if name in {"_alert_target_ref", "_incident_detail_href", "_deployment_review_href"}:
        return lambda *a, **kw: ""
    if name in {"_deprecated_bff_path_response", "deprecated_bff_path_response"}:
        return lambda *a, **kw: {}
    if name in {"_management_ai_conversation_store", "conv_store"}:
        return lambda: None
    if name in {"_assistant_ask_enabled", "assistant_ask_enabled"}:
        return lambda *a, **kw: True
    if name in {"agora_audit_store", "strategy_write_owner", "loop_truth", "downstream_health_monitor"}:
        return None

    return lambda *a, **kw: None


def mount_bff_routers(
    app: FastAPI,
    app_deps: Optional[Any] = None,
    **dependencies: Any,
) -> None:
    """Mount all domain routers onto the given FastAPI application.

    This function implements the production BFF router composition seam (AC3).
    When called from ``main.py``, dependencies resolve from the active
    ``main.py`` module scope unless explicitly passed. When called standalone
    without importing ``main.py``, canonical default services and ports are used.
    """
    import sys

    if app_deps is None:
        from ..bootstrap.dependencies import AppDependencies
        app_deps = AppDependencies.create_default()

    # Production always imports this module under its fully-qualified name
    # (see the Dockerfile's `uvicorn services.control_plane.bff.main:app`),
    # so that key is checked first. A long-standing, repo-wide test
    # convention (dozens of test_*.py files under services/control-plane/bff)
    # instead does `sys.path.insert(0, os.path.dirname(__file__)); import
    # main as bff_main`, which registers the identical module object under
    # the bare name "main" in sys.modules. Falling back to that name keeps
    # such tests resolving dependencies from the real, already-imported
    # main.py module scope (its actual production functions) instead of
    # silently degrading to the standalone safe-default stubs below.
    main_mod = sys.modules.get("services.control_plane.bff.main") or sys.modules.get("main")

    def _dep(name: str, fallback_factory: Optional[Callable[[], Any]] = None) -> Any:
        if name in dependencies and dependencies[name] is not None:
            return dependencies[name]
        if main_mod is not None and hasattr(main_mod, name):
            val = getattr(main_mod, name)
            if val is not None:
                return val
        if fallback_factory is not None:
            return fallback_factory()
        return _resolve_default_dependency(name, app_deps)

    # 1-4: Governance subrules
    from ..console_gap.permissions import create_permissions_router
    from ..console_gap.memory_governance import create_memory_governance_router
    from ..console_gap.consult_rules import create_consult_rules_router
    from ..console_gap.route_policies import create_route_policies_router
    _gov_kw = dict(
        read_surface=app_deps.read_surface,
        extract_identity=_dep("_extract_identity"),
        require_read_role=_dep("_require_read_role"),
    )
    app.include_router(create_permissions_router(**_gov_kw))
    app.include_router(create_memory_governance_router(**_gov_kw))
    app.include_router(create_consult_rules_router(**_gov_kw))
    app.include_router(create_route_policies_router(**_gov_kw))

    # 5: Assistant routes
    from ..assistant.control_mode import ControlModeStore
    from ..assistant.routes import create_assistant_router
    from ..assistant.transcript_store import (
        ManagementAiAssistantSessionStore,
        ManagementAiAssistantTranscriptStore,
    )
    conv_store = _dep("_management_ai_conversation_store", lambda: lambda: None)
    asst_session_store = _dep(
        "_ASSISTANT_SESSION_STORE",
        lambda: ManagementAiAssistantSessionStore(store_factory=conv_store),
    )
    asst_transcript_store = _dep(
        "_ASSISTANT_TRANSCRIPT_STORE",
        lambda: ManagementAiAssistantTranscriptStore(store_factory=conv_store),
    )
    asst_control_mode_store = _dep(
        "_ASSISTANT_CONTROL_MODE_STORE",
        lambda: ControlModeStore(),
    )
    app.include_router(
        create_assistant_router(
            build_context_pack=_dep("_assistant_build_context_pack"),
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            bff_error=_dep("_bff_error"),
            session_store=asst_session_store,
            transcript_store=asst_transcript_store,
            control_mode_store=asst_control_mode_store,
            provider_readiness=_dep("_assistant_provider_readiness"),
            provider_list=_dep("_assistant_provider_list"),
            provider_register=_dep("_assistant_provider_register"),
            provider_reauth=_dep("_assistant_provider_reauth"),
            provider_reauth_status=_dep("_assistant_provider_reauth_status"),
            provider_reauth_code=_dep("_assistant_provider_reauth_code"),
        )
    )

    # 6: Workflows hooks
    from ..console_gap.workflows_hooks import create_workflows_hooks_router
    app.include_router(
        create_workflows_hooks_router(
            workflow_hook_port=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            snapshot_now=_dep("utc_now"),
        )
    )

    # 7: Datasources
    from ..source_management_client import SourceManagementClient
    from ..console_gap.datasources import create_datasources_router
    src_mgmt_client = _dep("source_management_client", lambda: SourceManagementClient())
    app.include_router(
        create_datasources_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            snapshot_meta=_dep("_snapshot_meta"),
            utc_now=_dep("utc_now"),
            read_source_connector_registry=_dep("_read_management_source_connector_registry"),
            get_source_management_client=lambda: _dep("source_management_client", lambda: src_mgmt_client),
            require_operator_role=_dep("_require_operator_role"),
            bff_error=_dep("_bff_error"),
        )
    )

    # 8, 9: Management read models & router
    from ..management_read_models import (
        create_management_read_models_router,
        create_management_router,
    )
    app.include_router(
        create_management_read_models_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            snapshot_meta=_dep("_snapshot_meta"),
            utc_now=_dep("utc_now"),
        )
    )
    app.include_router(
        create_management_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            snapshot_meta=_dep("_snapshot_meta"),
            utc_now=_dep("utc_now"),
            bff_error=_dep("_bff_error"),
            raise_if_session_logged_out=_dep("_raise_if_session_logged_out"),
            tenant_payload_fn=_dep("_bff_me_tenant_payload"),
            run_management_read=_dep("run_management_read"),
            build_evidence_payload=_dep("_build_management_evidence_payload"),
            build_cockpit_payload=_dep("_build_management_cockpit_payload"),
        )
    )

    # 10, 11: Trade journal & journeys
    from ..trade_journal import create_trade_journal_router
    app.include_router(
        create_trade_journal_router(
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            require_operator_role=_dep("_require_operator_role"),
        )
    )
    from ..trade_journeys import create_trade_journeys_router
    app.include_router(
        create_trade_journeys_router(
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            require_operator_role=_dep("_require_operator_role"),
            get_projection_reader=app_deps.read_surface.trade_journey_projection_reader,
        )
    )

    # 12, 13: Lineage & Alpha factory
    from ..console_gap.lineage import create_lineage_router
    app.include_router(
        create_lineage_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            snapshot_meta=_dep("_snapshot_meta"),
            utc_now=_dep("utc_now"),
        )
    )
    from ..console_gap.alpha_factory import create_alpha_factory_router
    app.include_router(
        create_alpha_factory_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            utc_now=_dep("utc_now"),
        )
    )

    # 14: Jobs
    from ..jobs.router import create_jobs_router
    from ..models import CommandType, ObjectType
    app.include_router(
        create_jobs_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            bff_error=_dep("_bff_error"),
            utc_now=_dep("utc_now"),
            page_slice=_dep("_page_slice"),
            read_surface_meta=_dep("_read_surface_meta"),
            dataset_surface_status=_dep("_dataset_surface_status"),
            raise_if_read_surface_unavailable=_dep("_raise_if_read_surface_unavailable"),
            reject_body_idempotency_key=_dep("_reject_body_idempotency_key"),
            resolve_final_idempotency_key=_dep("_resolve_final_idempotency_key"),
            submit_job_action=lambda job_id, action_id, resolved_key, identity, payload: _dep("_evol_exp_bff_action_command")(
                entity_type=ObjectType.JOB,
                entity_id=job_id,
                action_id=action_id,
                resolved_key=resolved_key,
                identity=identity,
                payload=payload,
                command_type=CommandType.JOB_ACTION,
            ),
        )
    )

    # 15: Events
    from ..events.router import create_events_router
    sse_buffers = _dep("_sse_buffers")
    sse_subscribers = _dep("_sse_subscribers")
    events_router = create_events_router(
        read_surface=app_deps.read_surface,
        command_store=app_deps.command_store,
        get_read_store=lambda: _dep("read_store", lambda: app_deps.read_surface),
        extract_identity=_dep("_extract_identity"),
        require_read_role=_dep("_require_read_role"),
        bff_error=_dep("_bff_error"),
        utc_now=_dep("utc_now"),
        snapshot_meta=_dep("_snapshot_meta"),
        sse_buffers=sse_buffers,
        sse_subscribers=sse_subscribers,
        sse_channels=_dep("SSE_CHANNELS"),
        handle_sse_stream=_dep("_handle_sse_stream"),
        include_domain_sse_aliases=False,
    )
    app.include_router(events_router)

    # 16: Evolution
    from ..evolution.router import create_evolution_router
    from ..ports.evolution_program_commands import EvolutionServiceProgramCommandPort
    from services.evolution.client import EvolutionClient
    evolution_program_commands = _dep(
        "_evolution_program_commands",
        lambda: EvolutionServiceProgramCommandPort(EvolutionClient()),
    )
    app.include_router(
        create_evolution_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            require_operator_role=_dep("_require_operator_role"),
            bff_error=_dep("_bff_error"),
            utc_now=_dep("utc_now"),
            page_slice=_dep("_page_slice"),
            snapshot_meta=_dep("_snapshot_meta"),
            dataset_surface_status=_dep("_dataset_surface_status"),
            read_surface_meta=_dep("_read_surface_meta"),
            raise_if_read_surface_unavailable=_dep("_raise_if_read_surface_unavailable"),
            meta_staleness=_dep("_meta_staleness"),
            mutation_review_projection=_dep("_mutation_review_projection"),
            program_commands=lambda: _dep("_evolution_program_commands", lambda: evolution_program_commands),
            submit_program_action=lambda entity_type, entity_id, action_id, resolved_key, identity, payload: _dep("_gov_bff_action_command")(
                ObjectType.EVOLUTION_PROGRAM,
                entity_id,
                action_id,
                _dep("_resolve_final_idempotency_key")(resolved_key, None),
                identity,
                payload or {},
                CommandType.EVOLUTION_PROGRAM_ACTION,
            ),
        )
    )

    # 17: Research
    from ..research.router import create_research_router
    app.include_router(
        create_research_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            require_operator_role=_dep("_require_operator_role"),
            bff_error=_dep("_bff_error"),
            utc_now=_dep("utc_now"),
            page_slice=_dep("_page_slice"),
            snapshot_meta=_dep("_snapshot_meta"),
            dataset_surface_status=_dep("_dataset_surface_status"),
            submit_experiment_action=lambda entity_type, entity_id, action_id, resolved_key, identity, payload: _dep("_gov_bff_action_command")(
                ObjectType.EXPERIMENT,
                entity_id,
                action_id,
                _dep("_resolve_final_idempotency_key")(resolved_key, None),
                identity,
                payload or {},
                CommandType.EXPERIMENT_ACTION,
            ),
            include_prepared_subrouters=True,
        )
    )

    # 18: Training
    from ..training.router import create_training_router
    app.include_router(
        create_training_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            bff_error=_dep("_bff_error"),
            utc_now=_dep("utc_now"),
            page_slice=_dep("_page_slice"),
            dataset_surface_status=_dep("_dataset_surface_status"),
        )
    )

    # 19: Runtime router
    from ..personas.service import PersonaService
    persona_service = _dep(
        "persona_service",
        lambda: PersonaService(
            write_owner=app_deps.persona_write_owner,
            read_store=app_deps.read_surface,
            ranking_write_owner=app_deps.ranking_write_owner,
            command_store=app_deps.command_store,
        ),
    )
    from ..runtime.router import create_runtime_router
    runtime_router = create_runtime_router(
        read_surface=app_deps.read_surface,
        dependencies={
            name: _dep(
                name,
                lambda n=name: getattr(persona_service, "build_persona_health_items")
                if n == "_build_persona_health_items"
                else _resolve_default_dependency(n, app_deps),
            )
            for name in (
                "_GOVERNANCE_APPROVAL_QUEUE_ROUTE",
                "_GOV_BFF_IDEMPOTENCY",
                "_aggregate_group_surface",
                "_alert_target_ref",
                "_bff_error",
                "_build_persona_health_items",
                "_capital_bff_idempotency_check",
                "_capital_bff_idempotency_store",
                "_composed_dataset_surface_status",
                "_composed_surface_status",
                "_dataset_surface_status",
                "_deployment_review_href",
                "_deprecated_bff_path_response",
                "_dry_run_success_response",
                "_extract_identity",
                "_gov_bff_action_command",
                "_handle_sse_stream",
                "_incident_detail_href",
                "_meta_staleness",
                "_ooda_packet_list_payload",
                "_page_slice",
                "_project_operator_runtime_state_row",
                "_publish_event",
                "_raise_if_read_surface_unavailable",
                "_read_surface_meta",
                "_reject_body_idempotency_key",
                "_request_dry_run_requested",
                "_require_ooda_packet_routes_enabled",
                "_require_operator_role",
                "_require_read_role",
                "_resolve_final_idempotency_key",
                "_snapshot_meta",
                "_split_csv_query",
                "_sse_buffers",
                "_sse_subscribers",
                "_stable_json_hash",
                "create_capital_binding",
                "utc_now",
            )
        },
    )
    app.routes.extend(runtime_router.routes)

    # 20: Deployment
    from ..deployment.router import create_deployment_router
    deployment_router = create_deployment_router(
        queries=app_deps.deployment_queries,
        commands=app_deps.deployment_commands,
        extract_identity=_dep("_extract_identity"),
        require_read_role=_dep("_require_read_role"),
        require_operator_role=_dep("_require_operator_role"),
        bff_error=_dep("_bff_error"),
        utc_now=_dep("utc_now"),
        page_slice=_dep("_page_slice"),
        snapshot_meta=_dep("_snapshot_meta"),
        dataset_surface_status=_dep("_dataset_surface_status"),
        composed_surface_status=_dep("_composed_surface_status"),
        read_surface_meta=_dep("_read_surface_meta"),
        raise_if_read_surface_unavailable=_dep("_raise_if_read_surface_unavailable"),
        aggregate_group_surface=_dep("_aggregate_group_surface"),
        split_csv_query=_dep("_split_csv_query"),
        meta_staleness=_dep("_meta_staleness"),
        stable_json_hash=_dep("_stable_json_hash"),
        resolve_final_idempotency_key=_dep("_resolve_final_idempotency_key"),
        reject_body_idempotency_key=_dep("_reject_body_idempotency_key"),
        request_dry_run_requested=_dep("_request_dry_run_requested"),
        gov_bff_idempotency=_dep("_GOV_BFF_IDEMPOTENCY"),
        publish_event=_dep("_publish_event"),
        sse_buffers=sse_buffers,
        sse_subscribers=sse_subscribers,
        gov_bff_action_command=_dep("_gov_bff_action_command"),
        deprecated_bff_path_response=_dep("_deprecated_bff_path_response"),
        sem_command_response=_dep("_sem_command_response"),
        stream_generic_events=_dep("stream_generic_events"),
        surface_degradation_reason=_dep("_surface_degradation_reason"),
    )
    app.include_router(deployment_router)

    # 21: Command adapters
    from ..command_adapters.router import create_command_adapters_router
    from ..command_adapters.service import CommandAdapterService
    command_adapter_service = _dep(
        "_command_adapter_service",
        lambda: CommandAdapterService(
            command_store=app_deps.command_store,
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_operator_role=_dep("_require_operator_role"),
            require_read_role=_dep("_require_read_role"),
            bff_error=_dep("_bff_error"),
            utc_now=_dep("utc_now"),
        ),
    )
    app.include_router(create_command_adapters_router(service=command_adapter_service))

    # 22-24: Rankings
    from ..management_read_models.ranking_router import (
        create_ranking_formulas_router,
        create_rankings_long_tail_router,
        create_performance_attribution_router,
    )
    app.include_router(
        create_ranking_formulas_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            require_operator_role=_dep("_require_operator_role"),
            bff_error=_dep("_bff_error"),
            utc_now=_dep("utc_now"),
            snapshot_meta=_dep("_snapshot_meta"),
        )
    )
    app.include_router(
        create_rankings_long_tail_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            bff_error=_dep("_bff_error"),
            utc_now=_dep("utc_now"),
            page_slice=_dep("_page_slice"),
            read_surface_meta=_dep("_read_surface_meta"),
            deprecated_bff_path_response=_dep("_deprecated_bff_path_response"),
            reject_body_idempotency_key=_dep("_reject_body_idempotency_key"),
            resolve_final_idempotency_key=_dep("_resolve_final_idempotency_key"),
            capital_bff_idempotency_check=_dep("_capital_bff_idempotency_check"),
            capital_bff_idempotency_store=_dep("_capital_bff_idempotency_store"),
            capital_bff_action_command=_dep("_capital_bff_action_command"),
            object_type=ObjectType,
            command_type=CommandType,
        )
    )
    app.include_router(
        create_performance_attribution_router(
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            bff_me_tenant_payload=_dep("_bff_me_tenant_payload"),
            pm12_performance_attribution_response=_dep("_pm12_performance_attribution_response"),
            attribution_dimensions=_dep("_PM12_ATTRIBUTION_DIMENSIONS"),
        )
    )

    # 25: Strategies
    from ..strategies.router import create_strategies_router
    app.include_router(
        create_strategies_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            require_operator_role=_dep("_require_operator_role"),
            bff_error=_dep("_bff_error"),
            utc_now=_dep("utc_now"),
            page_slice=_dep("_page_slice"),
            read_surface_meta=_dep("_read_surface_meta"),
            reject_body_idempotency_key=_dep("_reject_body_idempotency_key"),
            resolve_final_idempotency_key=_dep("_resolve_final_idempotency_key"),
            stable_json_hash=_dep("_stable_json_hash"),
            request_dry_run_requested=_dep("_request_dry_run_requested"),
            dry_run_success_response=_dep("_dry_run_success_response"),
            normalize_lifecycle_state=_dep("_normalize_lifecycle_state"),
            normalize_risk_level=_dep("_normalize_risk_level"),
            strategy_persona_idempotency_check=_dep("_strategy_persona_idempotency_check"),
            strategy_persona_action_command=_dep("_strategy_persona_action_command"),
            strategy_persona_idempotency_store=_dep("_STRATEGY_PERSONA_BFF_IDEMPOTENCY"),
            strategy_seed_replication_idempotency_store=_dep("_STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY"),
            strategy_seed_review_idempotency_store=_dep("_STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY"),
            list_governance_audit_events=_dep("_list_governance_audit_events"),
            ooda_packet_list_payload=_dep("_ooda_packet_list_payload"),
            require_ooda_packet_routes_enabled=_dep("_require_ooda_packet_routes_enabled"),
            deprecated_bff_path_response=_dep("_deprecated_bff_path_response"),
            bff_me_tenant_payload=_dep("_bff_me_tenant_payload"),
            list_persona_records=_dep("_list_persona_records"),
            list_strategy_summaries=_dep("_list_strategy_summaries"),
            strategy_write_owner=lambda: _dep("strategy_write_owner"),
        )
    )

    # 26: Incidents
    from ..incidents.router import create_incident_router
    app.include_router(
        create_incident_router(
            read_surface=app_deps.read_surface,
            command_store=app_deps.command_store,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            require_operator_role=_dep("_require_operator_role"),
            bff_error=_dep("_bff_error"),
            utc_now=_dep("utc_now"),
            page_slice=_dep("_page_slice"),
            snapshot_meta=_dep("_snapshot_meta"),
            dataset_surface_status=_dep("_dataset_surface_status"),
            meta_staleness=_dep("_meta_staleness"),
            surface_degradation_reason=_dep("_surface_degradation_reason"),
            read_surface_meta=_dep("_read_surface_meta"),
            raise_if_read_surface_unavailable=_dep("_raise_if_read_surface_unavailable"),
            resolve_final_idempotency_key=_dep("_resolve_final_idempotency_key"),
            reject_body_idempotency_key=_dep("_reject_body_idempotency_key"),
            submit_action_command=_dep("_gov_bff_action_command"),
            submit_sem_command=_dep("_sem_command_response"),
            handle_sse_stream=_dep("_handle_sse_stream"),
            run_management_read=_dep("run_management_read"),
            request_dry_run_requested=_dep("_request_dry_run_requested"),
            dry_run_success_response=_dep("_dry_run_success_response"),
            build_operator_alerts_payload=lambda s: _dep("_build_operator_alerts_payload")(s),
            list_governance_audit_events=_dep("_list_governance_audit_events"),
            incident_events=_dep("_incident_events"),
            incident_subscribers=_dep("_incident_subscribers"),
            acknowledged_alerts=_dep("_ACKNOWLEDGED_ALERTS"),
            idempotency_ledger=_dep("_GOV_BFF_IDEMPOTENCY"),
        )
    )

    # 27: Auth
    from ..auth.router import create_auth_router
    from ..auth.service import AuthFacadeService, ProviderReadinessCache
    from ..auth.handlers import create_auth_dependencies, create_auth_handlers

    auth_deps = _dep(
        "auth_deps",
        lambda: create_auth_dependencies(
            bff_error=_dep("_bff_error"),
            dev_login_forbidden_environment=_dep("_dev_login_forbidden_environment"),
            dev_login_identity_registry=_dep("_dev_login_identity_registry"),
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            raise_if_session_logged_out=_dep("_raise_if_session_logged_out"),
            session_lifecycle_store=_dep("session_lifecycle_store"),
            bff_me_tenant_payload=_dep("_bff_me_tenant_payload"),
            capabilities_for_identity=_dep("_capabilities_for_identity"),
            bff_auth_stub_enabled=_dep("_bff_auth_stub_enabled"),
            bff_auth_mode=_dep("_bff_auth_mode"),
            bff_source_commit=_dep("_bff_source_commit"),
            write_roles=frozenset(_dep("_WRITE_ROLES")),
            utc_now=_dep("utc_now"),
        ),
    )
    auth_handlers = _dep(
        "auth_handlers",
        lambda: create_auth_handlers(dependencies=auth_deps),
    )
    auth_facade_service = _dep(
        "auth_facade_service",
        lambda: AuthFacadeService(
            local_readiness=auth_handlers["bff_auth_readiness"],
            handlers=auth_handlers,
            provider_readiness_cache=_dep("provider_readiness_cache"),
        ),
    )
    app.include_router(
        create_auth_router(
            service=auth_facade_service,
            browser_origin_allowed=_dep("_cors_origin_allowed"),
        )
    )

    # 28-30: Settings, Assistant management, Core
    app.include_router(
        create_settings_router(
            settings_store=_dep("settings_store"),
            extract_identity=_dep("_extract_identity"),
            require_admin_mfa=_dep("_require_admin_mfa"),
        )
    )
    core_handlers = _dep(
        "_core_handlers",
        lambda: {
            "bff_management_nl_ask": _dep("bff_management_nl_ask"),
            "bff_management_nl_ask_stream": _dep("bff_management_nl_ask_stream"),
            "bff_management_ai_audit": _dep("bff_management_ai_audit"),
            "bff_assistant_provider_usage_summary": _dep("bff_assistant_provider_usage_summary"),
            "bff_management_ai_conversations": _dep("bff_management_ai_conversations"),
            "bff_management_ai_conversation": _dep("bff_management_ai_conversation"),
            "bff_management_ai_attachment": _dep("bff_management_ai_attachment"),
            "bff_management_readiness_ep5": _dep("bff_management_readiness_ep5"),
            "bff_management_readiness_broker_live": _dep("bff_management_readiness_broker_live"),
            "bff_management_readiness_capital_binding_live": _dep("bff_management_readiness_capital_binding_live"),
            "bff_management_readiness_bff_ha": _dep("bff_management_readiness_bff_ha"),
            "bff_management_readiness_strict_publish": _dep("bff_management_readiness_strict_publish"),
            "bff_types_compat": _dep("bff_types_compat"),
            "sem_bff_version": _dep("sem_bff_version", lambda: sem_bff_version),
            "sem_bff_health_alias": _dep("sem_bff_health_alias"),
            "sem_bff_readiness_alias": _dep("sem_bff_readiness_alias"),
            "sem_bff_capabilities": _dep("sem_bff_capabilities", lambda: sem_bff_capabilities),
        },
    )
    app.include_router(create_assistant_management_router(core_handlers))
    app.include_router(create_core_router(core_handlers))

    # 31: Personas
    from ..personas.router import create_personas_router
    app.include_router(
        create_personas_router(
            service=persona_service,
            extract_identity_fn=_dep("_extract_identity"),
            require_read_role_fn=_dep("_require_read_role"),
            require_operator_role_fn=_dep("_require_operator_role"),
            bff_error_fn=_dep("_bff_error"),
            utc_now_fn=_dep("utc_now"),
            page_slice_fn=_dep("_page_slice"),
            snapshot_meta_fn=_dep("_snapshot_meta"),
            dataset_surface_status_fn=_dep("_dataset_surface_status"),
            raise_if_read_surface_unavailable_fn=_dep("_raise_if_read_surface_unavailable"),
            reject_body_idempotency_key_fn=_dep("_reject_body_idempotency_key"),
            resolve_final_idempotency_key_fn=_dep("_resolve_final_idempotency_key"),
        )
    )

    # 32: Capital
    from ..capital.router import create_capital_router
    app.include_router(
        create_capital_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            require_operator_role=_dep("_require_operator_role"),
            utc_now=_dep("utc_now"),
            page_slice=_dep("_page_slice"),
            snapshot_meta=_dep("_snapshot_meta"),
            dataset_surface_status=_dep("_dataset_surface_status"),
            bff_error=_dep("_bff_error"),
        )
    )

    # 33: Governance
    from ..governance.router import create_governance_router
    app.include_router(
        create_governance_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            require_operator_role=_dep("_require_operator_role"),
            bff_error=_dep("_bff_error"),
            utc_now=_dep("utc_now"),
            page_slice_fn=_dep("_page_slice"),
            snapshot_meta=_dep("_snapshot_meta"),
            dataset_surface_status=_dep("_dataset_surface_status"),
            read_surface_meta=_dep("_read_surface_meta"),
            meta_staleness=_dep("_meta_staleness"),
            redact_evidence_refs=_dep("redact_evidence_refs"),
            capabilities_for_identity=_dep("_capabilities_for_identity"),
            read_surface_state=_dep("_read_surface_state"),
            submit_action=getattr(command_adapter_service, "submit_governance_action", _dep("_submit_final_command_admission")),
            publish_event=lambda event_type, data: _dep("_publish_event")(
                sse_buffers.get("audit") if isinstance(sse_buffers, dict) else [],
                sse_subscribers.get("audit") if isinstance(sse_subscribers, dict) else set(),
                event_type,
                data,
            ),
            reject_body_idempotency_key=_dep("_reject_body_idempotency_key"),
            run_management_read=_dep("run_management_read"),
        )
    )

    # 34: Postmortems
    from ..postmortems.router import create_postmortem_router
    app.include_router(
        create_postmortem_router(
            read_surface=app_deps.read_surface,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            bff_error=_dep("_bff_error"),
            meta_staleness=_dep("_meta_staleness"),
        )
    )

    # 35: Control loops
    from ..control_loops.router import create_control_loops_router
    app.include_router(
        create_control_loops_router(
            read_surface=app_deps.read_surface,
            loop_truth_adapter=_dep("loop_truth"),
            downstream_health_monitor=_dep("downstream_health_monitor"),
            intervention_records_provider=_dep("_v5_intervention_records"),
            submit_sem_command=_dep("_sem_command_response"),
            submit_final_command_admission=_dep("_submit_final_command_admission"),
            reject_body_idempotency_key=_dep("_reject_body_idempotency_key"),
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            require_operator_role=_dep("_require_operator_role"),
            bff_error=_dep("_bff_error"),
            utc_now_fn=_dep("utc_now"),
        )
    )

    # 36: Tools integrations
    from ..tools_integrations.router import create_integrations_router
    from ..openclaw_ops_client import OpenClawOpsClient
    openclaw_client = _dep("openclaw_client", lambda: OpenClawOpsClient())
    app.include_router(
        create_integrations_router(
            read_surface=app_deps.read_surface,
            openclaw_client=openclaw_client,
            extract_identity=_dep("_extract_identity"),
            require_read_role=_dep("_require_read_role"),
            require_operator_role=_dep("_require_operator_role"),
            require_mcp_tool_write_role=_dep("_require_operator_role"),
            require_openclaw_command_role=_dep("_require_operator_role"),
            bff_error=_dep("_bff_error"),
            utc_now_fn=_dep("utc_now"),
            page_slice_fn=_dep("_page_slice"),
            snapshot_meta=_dep("_snapshot_meta"),
            read_surface_meta=_dep("_read_surface_meta"),
            submit_command=_dep("_submit_final_command_admission"),
            dry_run_resolver=_dep("_truthy_header"),
            dry_run_context=_dep("_REQUEST_DRY_RUN_CONTEXT"),
        )
    )

    # 37: Agora
    from ..agora.router import create_agora_router
    agora_router = create_agora_router(
        extract_identity=_dep("_extract_identity"),
        require_read_role=_dep("_require_read_role"),
        require_write_role=_dep("_require_operator_role"),
        require_operator_role=_dep("_require_operator_role"),
        require_journal_write_role=_dep("_require_journal_write_role"),
        require_agora_signal_write_role=_dep("_require_agora_signal_write_role"),
        require_agora_bulk_feedback_role=_dep("_require_agora_bulk_feedback_role"),
        bff_error=_dep("_bff_error"),
        utc_now=_dep("utc_now"),
        read_surface=app_deps.read_surface,
        get_audit_store=lambda: _dep("agora_audit_store"),
        command_store=app_deps.command_store,
        persona_write_owner=app_deps.persona_write_owner,
        get_trade_journey_store=app_deps.read_surface.trade_journey_projection_reader,
        sync_servant_agent=lambda p: _dep("_ensure_agora_servant_openclaw_agent", lambda: lambda p_: {})(dict(p)),
        canonical_context_ref_resolver=_dep("_resolve_agora_interaction_context_ref"),
        idempotency_store=_dep("_AGORA_CORE_BFF_IDEMPOTENCY"),
        sse_buffers=sse_buffers,
        sse_subscribers=sse_subscribers,
        assistant_ask_enabled=_dep("_assistant_ask_enabled"),
        assistant_build_context_pack=_dep("_assistant_build_context_pack"),
        get_assistant_session_store=lambda: asst_session_store,
        get_assistant_transcript_store=lambda: asst_transcript_store,
        openclaw_ops_client_factory=lambda: _dep("OpenClawOpsClient", lambda: OpenClawOpsClient)(),
        handle_sse_stream=_dep("_handle_sse_stream"),
        publish_event_fn=_dep("_publish_event"),
    )
    app.include_router(agora_router)

    # Attach shared instances onto app.state
    app.state.events_router = events_router
    app.state.deployment_router = deployment_router
    app.state.agora_router = agora_router
    app.state.runtime_router = runtime_router
    app.state.interaction_lifecycle = agora_router.interaction_lifecycle
    app.state.workshop_store = agora_router.workshop_store
    app.state.proposal_store = agora_router.proposal_store
    app.state.research_store = getattr(agora_router, "research_store", None)
    app.state.research_dispatcher = getattr(agora_router, "research_dispatcher", None)
    app.state.dataset_store = getattr(agora_router, "dataset_store", None)
    app.state.assistant_session_store = asst_session_store
    app.state.assistant_transcript_store = asst_transcript_store
    app.state.assistant_control_mode_store = asst_control_mode_store
    from ..assistant.management_service import set_assistant_control_mode_store
    set_assistant_control_mode_store(asst_control_mode_store)
    app.state.source_management_client = src_mgmt_client
    app.state.persona_service = persona_service
    app.state.command_adapter_service = command_adapter_service
    app.state.auth_deps = auth_deps
    app.state.auth_handlers = auth_handlers
    app.state.auth_facade_service = auth_facade_service
    app.state.core_handlers = core_handlers


def compose_bff_app(
    app: Optional[FastAPI] = None,
    *,
    app_deps: Optional[Any] = None,
    lifespan: Optional[Any] = None,
    dev_login_enabled: Optional[Callable[[], bool]] = None,
    origin_allowed: Optional[Callable[[Optional[str]], bool]] = None,
    validate_session: Optional[Callable[[str], Any]] = None,
    title: str = "Pantheon Operator BFF",
    version: str = "0.2.0",
    **dependencies: Any,
) -> FastAPI:
    """Compose and return the fully assembled Operator BFF FastAPI application.

    This composer makes full application composition callable without importing
    ``main.py`` (AC3). If ``app`` is not provided, an application is constructed
    via ``build_bff_app``, core routes and health routes are registered, and all
    37 domain routers are mounted.
    """
    if app is None:
        app = build_bff_app(
            lifespan=lifespan,
            dev_login_enabled=dev_login_enabled,
            origin_allowed=origin_allowed,
            validate_session=validate_session,
            title=title,
            version=version,
        )

    if not any(getattr(r, "path", None) == "/livez" for r in app.routes):
        try:
            from services.foundation.health import register_fastapi_health_routes
            register_fastapi_health_routes(
                app,
                "operator-bff",
                dependencies=dependencies.get("bff_readiness_dependencies"),
                details=lambda: {"version": version, "data_dir": os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff")},
            )
        except Exception:
            pass

    if app_deps is None:
        from ..bootstrap.dependencies import AppDependencies
        app_deps = AppDependencies.create_default()

    mount_bff_routers(app, app_deps=app_deps, **dependencies)

    try:
        _ = app.openapi()
    except Exception:
        pass

    return app
