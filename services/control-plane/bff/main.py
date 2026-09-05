from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from collections import deque
from copy import deepcopy
from concurrent.futures import Executor, ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import partial, wraps
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit
from urllib import error as urllib_error
from urllib import request as urllib_request

from jsonschema import Draft7Validator
from fastapi import Body, Cookie, FastAPI, HTTPException, BackgroundTasks, Header, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.params import Param as FastAPIParam
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

def _resolve_param(val: Any) -> Any:
    if isinstance(val, FastAPIParam):
        if val.default is ... or type(val.default).__name__ == "PydanticUndefined":
            return None
        return val.default
    return val

from services.foundation import (  # noqa: E402
    ActorRef,
    ActorType,
    AuditAction,
    AuthorityScope,
    CommandEnvelope,
    EnvironmentName,
    EnvironmentScope,
    ErrorEnvelope,
    ErrorKind,
    FoundationValidationError,
    IdempotencyRecord,
    PolicyDecision,
    PolicyDecisionValue,
    TraceContext,
    foundation_id,
    sha256_checksum,
)
from services.foundation.health import (  # noqa: E402
    health_payload,
    readiness_status_code,
    register_fastapi_health_routes,
)
from services.source_ingestion.strategy_seed_store import (  # noqa: E402
    StrategySpecSeedStore,
)
from services.control_plane.persona.persona_strategy_discovery import (
    PersonaStrategyDiscoveryService,
    extract_persona_strategy_profile,
)
from .models import (
    ActionCommandStatus,
    ApproveMutationCommandPayload,
    AuditContext,
    SseEventEnvelope,
    BffActionCatalogResponse,
    BffErrorEnvelope,
    BffErrorPayload,
    CommandReceipt,
    CommandReceiptStatus,
    CommandResponse,
    CommandResultMeta,
    CommandRoutingPath,
    CommandStatus,
    CommandSubmissionResponse,
    CommandStatusResponse,
    CommandType,
    DecisionJournalEntryDTO,
    ErrorCode,
    ErrorDetail,
    InterventionKind,
    InterventionListResponse,
    InterventionRecord,
    InterventionStatus,
    JournalEntryMergePatch,
    McpImportedTool,
    McpRejectedTool,
    McpToolActionData,
    McpToolActionRequest,
    McpToolActionVerb,
    McpToolDescriptor,
    McpToolImportData,
    McpToolImportRequest,
    McpToolLifecycleStatus,
    ObjectType,
    OperatorCommand,
    OperatorIdentity,
    EVIDENCE_CAPABILITY_MAP,
    SOURCE_TYPE_TO_EVIDENCE_KIND,
    RecordSponsorDecisionCommandPayload,
    RejectMutationCommandPayload,
    ReviewMutationCommandPayload,
    ExecuteMutationCommandPayload,
    StalenessWarning,
    TargetObject,
    utc_now,
)
from .action_catalog import get_action_catalog, get_catalog_entry
from .command_queue import CommandStore
from .command_executor import (
    create_capital_binding,
    create_capital_pool,
    create_capital_rebalance_proposal,
    execute_command_with_status,
    _runtime_manager_client,
    _post_json,
    _get_json,
)
from .persona_allocation_policy import (
    build_pm12_allocation_policy_input,
    calculate_paper_simulation_allocations,
    calculate_target_allocations,
    validate_emergency_lines,
)
from .paper_eligibility_proof import (
    BENCHMARK_VERSION as _PPL_ALLOC_009_ELIGIBILITY_BENCHMARK_VERSION,
    EXPECTED_IDEMPOTENCY_KEY as _PPL_ALLOC_009_ELIGIBILITY_IDEMPOTENCY_KEY,
    PaperEligibilityObservationStore,
    RUN_KEY as _PPL_ALLOC_009_ELIGIBILITY_RUN_KEY,
    TASK_ID as _PPL_ALLOC_009_ELIGIBILITY_TASK_ID,
    build_telemetry_event as _ppl_alloc_009_build_telemetry_event,
)
from .emergency_containment_policy import validate_emergency_containment
from .session_lifecycle_store import SessionLifecycleStore
from .management_ai_store import ManagementAiAttachmentError, ManagementAiAttachmentStore, ManagementAiConversationStore
from .agora_audit_store import AgoraAuditStore
from .management_nl_command_idempotency import (
    DEFAULT_STORAGE_PATH as DEFAULT_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_PATH,
    ManagementNlCommandIdempotencyStore,
    ManagementNlCommandPayloadConflict,
    ManagementNlCommandRecoveryRequired,
    ManagementNlCommandReservation,
    ManagementNlCommandScope,
    ManagementNlCommandStorageError,
)
from .openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
from .source_search_ops_client import (
    SearchIndexCommandClient,
    SourceIngestCommandClient,
    SourceSearchOpsClientError,
)
from .downstream_health_monitor import DownstreamHealthMonitor
from .loop_inventory import (
    LoopHealthDetailEnvelope,
    LoopHealthListEnvelope,
    LoopInventoryDetailEnvelope,
    LoopInventoryListEnvelope,
    get_loop_inventory_entry,
    list_loop_inventory_entries,
    loop_inventory_meta,
    truth_label_payload,
)
from .management_read_models import loop_truth
from .operations_read_model import (
    DataConfidence,
    OperationsReadModelEnvelope,
    OperationsPerformance,
    OperationsReadModelEntry,
    SourceDiagnostic,
    SourceState,
    SourceStatus,
    build_operations_identity,
    classify_confidence,
    dedupe_ids,
    diagnostic as ops_read_model_diagnostic,
    sanitize_metric as ops_read_model_sanitize_metric,
)
from .models import redact_evidence_refs
from .ports import (
    ReadSurfacePorts,
    create_persona_registry_write_owner,
    create_read_surface_ports,
)
from .settings_store import SettingsStore
from .persona_provisioning import (
    ProvisioningConflict,
    ProvisioningRecord,
    make_persona_provisioning_store,
)
from .persona_provisioning_coordinator import (
    PersonaProvisioningCoordinationError,
    PersonaProvisioningCoordinator,
    deterministic_provisioning_ids,
)
from .personas.reconciliation import (
    PersonaProvisioningReconciliationMutationPort,
    PersonaReconciliationMutationError,
)
try:
    from services.persona.runtime_profile import (
        PersonaRuntimeProfile,
        build_persona_runtime_profile,
    )
except ImportError:
    try:
        from persona.runtime_profile import (  # type: ignore[no-redef]
            PersonaRuntimeProfile,
            build_persona_runtime_profile,
        )
    except ImportError:
        build_persona_runtime_profile = None  # type: ignore[assignment]
        PersonaRuntimeProfile = None  # type: ignore[assignment,misc]
log = logging.getLogger(__name__)
def _bool_from_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
_BFF_AUTH_STUB_ENV = "PANTHEON_BFF_AUTH_STUB"
_BFF_STUB_LEGACY_BARE_TOKENS_ENV = "PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS"
_BFF_STUB_CAPABILITY_ROLES = frozenset({"admin", "operator"})
_PRODUCTION_STRICT_ENVIRONMENTS = {
    "canary",
    "live",
    "prod",
    "production",
    "staging-live",
}
_DEFAULT_LOVABLE_CORS_ORIGINS = [
    # Pantheon-owned self-hosted dev frontend (execute-plans). This replaced the
    # Lovable-hosted dev FE; it is the current dev acceptance origin. Dev-only:
    # it must be filtered out by the production-strict CORS filter below.
    "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io",
    # TODO(off-lovable): staging-live and prod FE are also migrating off Lovable
    # to self-hosted sslip.io origins. Replace the staging/prod *.lovable.app
    # entries below once the new self-hosted URLs are provisioned.
    # Lovable shared-preview and published URLs for the Pantheon UI lanes.
    "https://preview--pantheon-dev.lovable.app",
    "https://preview--pantheon-ai-system-front-dev.lovable.app",
    "https://preview--pantheon-ai-system-front-staging-live.lovable.app",
    "https://preview--pantheon.lovable.app",
    "https://preview--pantheon-ai-system-front.lovable.app",
    "https://pantheon-dev.lovable.app",
    "https://pantheon-ai-system-front-dev.lovable.app",
    "https://pantheon-ai-system-front-staging-live.lovable.app",
    "https://pantheon.lovable.app",
    "https://pantheon-ai-system-front.lovable.app",
    # BFF-CONSOL-022: Pantheon Frontend Lovable project preview URLs.
    "https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com",
    "https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app",
    # BFF-B1-001: execute-plans Lovable project (UUID 140c41d5) published preview.
    "https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com",
]
_DEV_LOOPBACK_CORS_ORIGINS = [
    "http://127.0.0.1:4173",
    "http://localhost:4173",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]
_DEV_LOVABLE_CORS_ORIGINS = {
    # Self-hosted dev FE origin is dev-only: production-strict mode must filter it.
    "https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io",
    "https://preview--pantheon-dev.lovable.app",
    "https://preview--pantheon-ai-system-front-dev.lovable.app",
    "https://pantheon-dev.lovable.app",
    "https://pantheon-ai-system-front-dev.lovable.app",
    # Pantheon Frontend Lovable project preview URLs (dev tier).
    "https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com",
    # Static id-preview URLs are Lovable-hosted strict-preview origins; keep them
    # out of the dev-only set so production-strict preflight can still succeed.
    # BFF-B1-001-DELTA: 140c41d5 published URL intentionally NOT in dev-only set —
    # it must survive the production-strict filter so live OPTIONS succeeds.
}
_LOVABLE_PREVIEW_UUIDS = (
    "b75d3452-f667-4cf4-893a-1061de45b347"
    "|140c41d5-9cd8-4d6b-ba02-66d5941d0dbe"
)
_LOVABLE_PREVIEW_ORIGIN_REGEX = (
    r"https://id-preview(?:-[a-f0-9]+)?--({})"
    r"\.lovable\.app"
).format(_LOVABLE_PREVIEW_UUIDS)
_LOVABLE_PREVIEW_ORIGIN_PATTERN = re.compile(
    r"^" + _LOVABLE_PREVIEW_ORIGIN_REGEX + r"$"
)
class _PantheonCORSMiddleware(CORSMiddleware):
    def preflight_response(self, request_headers: Any) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code != 200:
            return response
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.pop("content-type", None)
        return Response(status_code=204, headers=headers)
def _normalized_origin(origin: str) -> str:
    return origin.strip().rstrip("/")
def _dedupe_origins(origins: List[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for origin in origins:
        cleaned = _normalized_origin(origin)
        if cleaned and cleaned not in seen:
            deduped.append(cleaned)
            seen.add(cleaned)
    return deduped
_BFF_VALID_AUTH_MODES = frozenset({"strict", "permissive"})
def _bff_auth_mode() -> str:
    raw = os.getenv("PANTHEON_BFF_AUTH_MODE", "strict").strip().lower() or "strict"
    if raw not in _BFF_VALID_AUTH_MODES:
        return "strict"
    return raw
def _is_production_strict_mode() -> bool:
    env_name = os.getenv("PANTHEON_ENV", "").strip().lower()
    deployment_stage = os.getenv("PANTHEON_DEPLOYMENT_STAGE", "").strip().lower()
    return _bff_auth_mode() == "strict" and (
        env_name in _PRODUCTION_STRICT_ENVIRONMENTS
        or deployment_stage in _PRODUCTION_STRICT_ENVIRONMENTS
    )
def _bff_auth_stub_enabled() -> bool:
    return _bool_from_env(_BFF_AUTH_STUB_ENV) and _bff_auth_mode() != "strict"
def _cors_origins_from_env() -> List[str]:
    raw = os.getenv("PANTHEON_BFF_CORS_ORIGINS", "")
    origins = _dedupe_origins(raw.split(",")) if raw.strip() else list(_DEFAULT_LOVABLE_CORS_ORIGINS)
    if _is_production_strict_mode():
        origins = [
            origin
            for origin in origins
            if origin not in _DEV_LOVABLE_CORS_ORIGINS and origin != "*"
        ]
    else:
        # Non-strict (dev/test) tiers always accept the loopback origins the
        # FE-BFF integration gate and local vite servers use, regardless of the
        # deploy-time PANTHEON_BFF_CORS_ORIGINS override.
        origins = origins + _DEV_LOOPBACK_CORS_ORIGINS
    return _dedupe_origins(origins)
_SECURITY_RESPONSE_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)
class _SecurityHeadersMiddleware:
    """Pure-ASGI middleware that appends baseline security headers.

    Implemented at the ASGI layer (not BaseHTTPMiddleware) so it does not buffer
    or break StreamingResponse / SSE endpoints.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message: Any) -> None:
            if message.get("type") == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                for name, value in _SECURITY_RESPONSE_HEADERS:
                    if name not in present:
                        headers.append((name, value))
            await send(message)

        await self.app(scope, receive, _send)


def _cors_origin_allowed(origin: Optional[str]) -> bool:
    if not origin:
        return False
    normalized = _normalized_origin(origin)
    if normalized in _cors_origins:
        return True
    if not _is_production_strict_mode() and _LOVABLE_PREVIEW_ORIGIN_PATTERN.fullmatch(origin):
        return True
    return False

def _clean_correlation_id(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    return raw or None

def _error_response_correlation_id(request: Optional[Request], headers: Optional[Dict[str, Any]] = None) -> str:
    if headers:
        for key in ("X-Correlation-Id", "x-correlation-id", "correlationId", "correlation_id"):
            val = _clean_correlation_id(headers.get(key))
            if val:
                return val
    if request is not None:
        for key in ("X-Correlation-Id", "x-correlation-id", "X-Request-Id", "x-request-id"):
            val = _clean_correlation_id(request.headers.get(key))
            if val:
                return val
    return str(uuid.uuid4())

def _status_error_code(status_code: int) -> str:
    return _ERROR_CODE_BY_STATUS.get(status_code, ErrorCode.VALIDATION_FAILED.value)

def _canonical_error_code_value(code: Any, *, status_code: Optional[int] = None) -> str:
    raw = str(getattr(code, "value", code) or "").strip()
    if not raw and status_code is not None:
        return _status_error_code(status_code)
    candidate = _LEGACY_ERROR_CODE_ALIASES.get(raw, raw)
    try:
        return ErrorCode(candidate).value
    except ValueError:
        if status_code is not None:
            return _status_error_code(status_code)
        return ErrorCode.INTERNAL_ERROR.value

def _pack_d_error_metadata(code: Any, *, status_code: Optional[int] = None) -> Dict[str, Any]:
    code_value = _canonical_error_code_value(code, status_code=status_code)
    behavior = _PACK_D_D21_ERROR_BEHAVIOR.get(
        code_value,
        _PACK_D_D21_ERROR_BEHAVIOR[ErrorCode.INTERNAL_ERROR.value],
    )
    return {
        "code": code_value,
        "i18nKey": f"errors.{code_value}",
        "retryable": behavior["retryable"],
        "userActionable": behavior["userActionable"],
    }

def _status_error_message(status_code: int, fallback: Any = None) -> str:
    clean = str(fallback or "").strip()
    if clean and clean != "{}":
        return clean
    if status_code == 404:
        return "Not Found"
    if status_code == 422:
        return "Request validation failed"
    if status_code >= 500:
        return "Internal server error"
    return "Request failed"

def _error_details_without_correlation(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    return {
        key: item
        for key, item in value.items()
        if key != "correlationId"
    }

def _pack_d_error_response(
    *,
    status_code: int,
    code: Any,
    message: Any,
    correlation_id: str,
    details: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> JSONResponse:
    metadata = _pack_d_error_metadata(code, status_code=status_code)
    error_payload: Dict[str, Any] = {
        "code": metadata["code"],
        "i18nKey": metadata["i18nKey"],
        "message": str(message or _status_error_message(status_code)),
        "retryable": metadata["retryable"],
        "userActionable": metadata["userActionable"],
    }
    if details is not None:
        error_payload["details"] = details
    content: Dict[str, Any] = {
        "error": error_payload,
        "meta": {"correlationId": correlation_id},
    }
    if extra:
        content.update(extra)
    response_headers = dict(headers or {})
    response_headers["X-Correlation-Id"] = correlation_id
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(content),
        headers=response_headers,
    )

def _with_cors_actual_response_headers(request: Request, headers: Dict[str, str]) -> Dict[str, str]:
    response_headers = dict(headers)
    origin = request.headers.get("origin")
    if not origin or not _cors_origin_allowed(origin):
        return response_headers

    response_headers.setdefault("Access-Control-Allow-Origin", _normalized_origin(origin))
    response_headers.setdefault("Access-Control-Allow-Credentials", "true")
    response_headers.setdefault("Access-Control-Expose-Headers", ", ".join(_CORS_EXPOSE_HEADERS))

    vary_value = response_headers.get("Vary") or response_headers.get("vary") or ""
    vary_parts = [part.strip() for part in vary_value.split(",") if part.strip()]
    if "Origin" not in {part.title() for part in vary_parts}:
        vary_parts.append("Origin")
    if vary_parts:
        response_headers["Vary"] = ", ".join(vary_parts)
    return response_headers

def _pack_d_http_exception_response(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    headers = _with_cors_actual_response_headers(
        request,
        dict(getattr(exc, "headers", None) or {}),
    )
    correlation_id = _error_response_correlation_id(request, headers)
    detail = exc.detail
    source = detail
    if (
        isinstance(detail, dict)
        and isinstance(detail.get("detail"), dict)
        and "error" in detail["detail"]
    ):
        source = detail["detail"]

    error: Dict[str, Any] = {}
    if isinstance(source, dict) and isinstance(source.get("error"), dict):
        error = dict(source["error"])
    elif isinstance(source, dict) and source.get("error") is not None:
        error = {
            "code": source.get("error"),
            "message": source.get("message") or source.get("error"),
        }

    code = error.get("code") or _status_error_code(exc.status_code)
    message = error.get("message") or _status_error_message(exc.status_code, detail)
    details = _error_details_without_correlation(error.get("details"))
    if details is None and not isinstance(source, dict):
        details = {"reason": str(source or message)}

    extra: Dict[str, Any] = {}
    if isinstance(source, dict):
        for key, value in source.items():
            if key in {"error", "correlationId", "meta", "detail"}:
                continue
            extra[key] = value

    return _pack_d_error_response(
        status_code=exc.status_code,
        code=code,
        message=message,
        correlation_id=correlation_id,
        details=details,
        headers=headers,
        extra=extra,
    )

async def _bff_http_exception_handler(
    request: Request,
    exc: StarletteHTTPException | HTTPException,
) -> JSONResponse:
    return _pack_d_http_exception_response(request, exc)

async def _bff_request_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    correlation_id = _error_response_correlation_id(request)
    return _pack_d_error_response(
        status_code=422,
        code=ErrorCode.VALIDATION_FAILED.value,
        message="Request validation failed",
        correlation_id=correlation_id,
        details={
            "reason": "REQUEST_VALIDATION_ERROR",
            "errors": exc.errors(),
        },
    )

async def _bff_value_error_handler(
    request: Request,
    exc: ValueError,
) -> JSONResponse:
    correlation_id = _error_response_correlation_id(request)
    return _pack_d_error_response(
        status_code=400,
        code=ErrorCode.VALIDATION_FAILED.value,
        message=str(exc) or "Invalid request",
        correlation_id=correlation_id,
        details={"reason": "VALUE_ERROR"},
    )

async def _bff_unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    log.exception("Unhandled BFF request error", exc_info=True)
    correlation_id = _error_response_correlation_id(request)
    return _pack_d_error_response(
        status_code=500,
        code=ErrorCode.INTERNAL_ERROR.value,
        message="Internal server error",
        correlation_id=correlation_id,
        details={"reason": "INTERNAL_SERVER_ERROR"},
        # Starlette's outer error handler runs after the CORS middleware has
        # unwound. Preserve the same allowlist on this terminal response.
        headers=_with_cors_actual_response_headers(request, {}),
    )

def _build_bff_app() -> FastAPI:
    cors_origins = _cors_origins_from_env()
    strict = _is_production_strict_mode()
    preview_regex = None if strict else _LOVABLE_PREVIEW_ORIGIN_REGEX
    built_app = FastAPI(title="Pantheon Operator BFF", version="0.2.0")
    if cors_origins or preview_regex:
        middleware_kwargs: Dict[str, Any] = dict(
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
    built_app.add_exception_handler(HTTPException, _bff_http_exception_handler)
    built_app.add_exception_handler(StarletteHTTPException, _bff_http_exception_handler)
    built_app.add_exception_handler(RequestValidationError, _bff_request_validation_error_handler)
    built_app.add_exception_handler(ValueError, _bff_value_error_handler)
    built_app.add_exception_handler(Exception, _bff_unhandled_exception_handler)
    return built_app
_cors_origins = _cors_origins_from_env()
_CORS_ALLOW_HEADERS = [
    "Accept",
    "Accept-Language",
    "Authorization",
    "Cache-Control",
    "Content-Type",
    "If-Match",
    "X-BFF-Api-Version",
    "X-Confirm-Token",
    "Idempotency-Key",
    "Last-Event-ID",
    "X-Correlation-Id",
    "X-Dry-Run",
    "X-Idempotency-Key",
    "X-Locale",
    "X-MFA-Token",
    "X-Request-Id",
    "X-Refresh-Token",
    "X-Tenant-Id",
    "X-Trace-Id",
]
_CORS_EXPOSE_HEADERS = [
    "ETag",
    "X-BFF-Api-Version",
    "X-Correlation-Id",
    "X-Request-Id",
]
app = _build_bff_app()
_OPENAPI_HTTP_CONTEXT: ContextVar[bool] = ContextVar("openapi_http_context", default=False)
_REQUEST_DRY_RUN_CONTEXT: ContextVar[bool] = ContextVar("request_dry_run_context", default=False)
def _schema_with_legacy_action_path_for_http(schema: Dict[str, Any]) -> Dict[str, Any]:
    http_schema = json.loads(json.dumps(schema))
    paths = http_schema.setdefault("paths", {})
    canonical = paths.get("/bff/actions/{type}/{id}/{action}")
    if not isinstance(canonical, dict):
        return http_schema
    legacy_path = "/bff/actions/{entityType}/{entityId}/{actionId}"
    if legacy_path in paths:
        return http_schema
    legacy = json.loads(json.dumps(canonical))
    rename = {"type": "entityType", "id": "entityId", "action": "actionId"}
    for operation in legacy.values():
        if not isinstance(operation, dict):
            continue
        if operation.get("operationId"):
            operation["operationId"] = f"{operation['operationId']}_legacy_named"
        for parameter in operation.get("parameters") or []:
            if isinstance(parameter, dict) and parameter.get("in") == "path":
                name = str(parameter.get("name") or "")
                if name in rename:
                    parameter["name"] = rename[name]
    paths[legacy_path] = legacy
    return http_schema
def _custom_openapi() -> Dict[str, Any]:
    if app.openapi_schema is None:
        app.openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
    if _OPENAPI_HTTP_CONTEXT.get():
        return _schema_with_legacy_action_path_for_http(app.openapi_schema)
    return app.openapi_schema
app.openapi = _custom_openapi  # type: ignore[method-assign]
BFF_DATA_DIR = os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff")
def _lifecycle_projector_dependency() -> Dict[str, Any]:
    reader_backend = os.getenv(
        "PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND", "postgres"
    ).strip().lower()
    if reader_backend != "postgres":
        return {
            "ready": False,
            "status": "degraded",
            "worker_status": "error",
            "writer_backend": "disabled",
            "reader_backend": reader_backend,
            "reasons": [f"legacy_reader_retired:{reader_backend}"],
            "error_reason": f"legacy_reader_retired:{reader_backend}",
        }

    reader = read_store.trade_journey_projection_reader()
    tenant_id = os.getenv("PANTHEON_BFF_HEALTH_TENANT_ID", "default").strip()
    environment = os.getenv(
        "PANTHEON_BFF_TRADE_JOURNEY_HEALTH_ENVIRONMENT", "paper"
    ).strip()
    reasons: List[str] = []
    controller: Dict[str, Any] = {}
    try:
        if reader is None:
            raise ProjectionReadUnavailable(
                "Postgres reader selected but no projection reader was configured"
            )
        controller = dict(
            reader.controller_freshness(
                tenant_id=tenant_id,
                environment=environment,
            )
            or {}
        )
    except (ProjectionReadUnavailable, ValueError) as exc:
        reasons.append(f"projection_reader_unavailable:{exc}")
    except Exception as exc:  # noqa: BLE001 - readiness is fail-closed truth
        reasons.append(f"projection_reader_error:{type(exc).__name__}")

    raw_writer_backend = os.getenv("LIFECYCLE_PROJECTOR_WRITER_BACKEND")
    if raw_writer_backend is not None and raw_writer_backend.strip():
        writer_backend = raw_writer_backend.strip().lower()
    else:
        writer_backend = "postgres" if controller else "disabled"

    if writer_backend not in {"postgres", "shadow", "relational"}:
        reasons.append(
            f"writer_backend_mismatch:{writer_backend or 'missing'}!=postgres"
        )

    expected_sha = (
        os.getenv("BFF_COMMIT") or os.getenv("GIT_SHA") or ""
    ).strip()
    controller_sha = str(controller.get("deployment_sha") or "").strip()
    checkpoint = int(controller.get("checkpoint") or 0)
    source_high = int(controller.get("source_high_watermark") or 0)
    backlog = int(controller.get("backlog") or 0)
    quarantine_count = int(controller.get("quarantine_count") or 0)
    if not controller:
        reasons.append("controller_missing")
    if controller.get("status") != "ready":
        reasons.append(f"controller_not_ready:{controller.get('status') or 'missing'}")
    if controller.get("mode") != "live" or controller.get("accepted_live") is not True:
        reasons.append(
            "live_truth_not_accepted:"
            f"{controller.get('mode') or 'missing'}:"
            f"{str(bool(controller.get('accepted_live'))).lower()}"
        )
    if checkpoint != source_high:
        reasons.append(f"checkpoint_mismatch:{checkpoint}!={source_high}")
    if backlog != 0:
        reasons.append(f"backlog_nonzero:{backlog}")
    if quarantine_count != 0:
        reasons.append(f"quarantine_nonzero:{quarantine_count}")
    if controller.get("last_error"):
        reasons.append(f"last_error:{controller['last_error']}")
    if expected_sha and expected_sha != "unknown" and controller_sha != expected_sha:
        reasons.append(
            f"deployment_sha_mismatch:{controller_sha or 'missing'}!={expected_sha}"
        )

    last_poll_at = str(controller.get("last_poll_at") or "").strip()
    freshness_age_seconds: Optional[float] = None
    if not last_poll_at:
        reasons.append("last_poll_missing")
    else:
        try:
            last_poll = datetime.fromisoformat(last_poll_at.replace("Z", "+00:00"))
            if last_poll.tzinfo is None:
                last_poll = last_poll.replace(tzinfo=timezone.utc)
            freshness_age_seconds = max(
                0.0,
                (datetime.now(timezone.utc) - last_poll.astimezone(timezone.utc)).total_seconds(),
            )
            max_age = max(
                1.0,
                float(os.getenv("LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS", "120")),
            )
            if freshness_age_seconds > max_age:
                reasons.append(
                    f"last_poll_stale:{freshness_age_seconds:.3f}>{max_age:.3f}"
                )
        except (TypeError, ValueError):
            reasons.append("last_poll_invalid")

    ready = not reasons
    root = Path(
        os.getenv(
            "LIFECYCLE_PROJECTION_ROOT",
            str(Path(BFF_DATA_DIR) / "lifecycle-projection"),
        )
    )
    return {
        "ready": ready,
        "status": "ready" if ready else "degraded",
        "worker_status": "ready" if ready else "error",
        "writer_backend": writer_backend,
        "reader_backend": "postgres",
        "tenant_scope": tenant_id,
        "environment_scope": environment,
        "deployment_sha": controller_sha or None,
        "expected_deployment_sha": expected_sha or None,
        "checkpoint": checkpoint,
        "source_high_watermark": source_high,
        "backlog": backlog,
        "quarantine_count": quarantine_count,
        "generation": controller.get("generation"),
        "mode": controller.get("mode"),
        "accepted_live": bool(controller.get("accepted_live")),
        "last_poll_at": last_poll_at or None,
        "freshness_age_seconds": freshness_age_seconds,
        "reasons": reasons,
        "error_reason": reasons[0] if reasons else None,
        "legacy_recovery_stores": {
            "trade_journey_events": str(
                root / "current" / "trade_journey_events.json"
            ),
            "loop_runs": str(root / "current" / "loop_runs.json"),
            "preserved": True,
            "accepted_reader": False,
        },
        "controller": controller,
    }
def _bff_readiness_dependencies() -> Dict[str, Dict[str, Any]]:
    return {
        "runtime_manager": {
            "status": "ok" if os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip() else "degraded",
            "url": os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip(),
        },
        "governance": {
            "status": "ok" if os.getenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "").strip() else "degraded",
            "url": os.getenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "").strip(),
        },
        "deployment": {
            "status": "ok" if os.getenv("PANTHEON_DEPLOYMENT_API_URL", "").strip() else "degraded",
            "url": os.getenv("PANTHEON_DEPLOYMENT_API_URL", "").strip(),
        },
        "lifecycle_projector": _lifecycle_projector_dependency(),
    }


# Keep the process-liveness/readiness contract registered on the assembled
# application. The Compose healthcheck and deployment gate probe ``/livez``;
# without this registration a freshly built candidate starts successfully but
# remains unhealthy and is rolled back before exact-pair admission.
register_fastapi_health_routes(
    app,
    "operator-bff",
    dependencies=_bff_readiness_dependencies,
    details=lambda: {"version": "0.2.0", "data_dir": BFF_DATA_DIR},
)


_ERROR_CODE_BY_STATUS = {
    400: ErrorCode.VALIDATION_FAILED.value,
    401: ErrorCode.AUTH_REQUIRED.value,
    403: ErrorCode.FORBIDDEN.value,
    404: ErrorCode.RESOURCE_NOT_FOUND.value,
    409: ErrorCode.RESOURCE_CONFLICT.value,
    413: ErrorCode.REQUEST_TOO_LARGE.value,
    422: ErrorCode.VALIDATION_FAILED.value,
    428: ErrorCode.PRECONDITION_FAILED.value,
    429: ErrorCode.RATE_LIMITED.value,
    500: ErrorCode.INTERNAL_ERROR.value,
    502: ErrorCode.UPSTREAM_ERROR.value,
    503: ErrorCode.DEPENDENCY_UNAVAILABLE.value,
    504: ErrorCode.UPSTREAM_TIMEOUT.value,
}
_LEGACY_ERROR_CODE_ALIASES = {
    "INVALID_REQUEST": ErrorCode.VALIDATION_FAILED.value,
    "INVALID_PARAMS": ErrorCode.VALIDATION_FAILED.value,
    "MFA_VALIDATION_FAILED": ErrorCode.VALIDATION_FAILED.value,
    "INVALID_TOKEN": ErrorCode.AUTH_REQUIRED.value,
    "AUTH_TOKEN_FORMAT": ErrorCode.AUTH_REQUIRED.value,
    "AUTH_JWT_EXPIRED": ErrorCode.AUTH_EXPIRED.value,
    "INSUFFICIENT_ROLE": ErrorCode.FORBIDDEN.value,
    "PERMISSION_DENIED": ErrorCode.FORBIDDEN.value,
    "CAPABILITY_MISSING": ErrorCode.FORBIDDEN.value,
    "OBJECT_NOT_FOUND": ErrorCode.RESOURCE_NOT_FOUND.value,
    "NOT_FOUND": ErrorCode.RESOURCE_NOT_FOUND.value,
    "INVALID_STATE": ErrorCode.OPERATION_NOT_ALLOWED.value,
    "HIGH_RISK_QUERY_REFUSED": ErrorCode.OPERATION_NOT_ALLOWED.value,
    "CONCURRENT_MODIFICATION": ErrorCode.RESOURCE_CONFLICT.value,
    "STATE_CONFLICT": ErrorCode.RESOURCE_CONFLICT.value,
    "DOWNSTREAM_UNAVAILABLE": ErrorCode.DEPENDENCY_UNAVAILABLE.value,
    "DOWNSTREAM_TIMEOUT": ErrorCode.UPSTREAM_TIMEOUT.value,
    "COMMAND_TIMEOUT": ErrorCode.UPSTREAM_TIMEOUT.value,
    "DOWNSTREAM_ERROR": ErrorCode.UPSTREAM_ERROR.value,
    "PRECONDITION_NOT_MET": ErrorCode.PRECONDITION_FAILED.value,
    "CONFIRM_TOKEN_REQUIRED": ErrorCode.CONFIRMATION_REQUIRED.value,
    "APPROVAL_REQUIRED": ErrorCode.HUMAN_GATE_PENDING.value,
    "TWO_MAN_REQUIRED": ErrorCode.TWO_MAN_SIGNATURE_REQUIRED.value,
    "MFA_REQUIRED": ErrorCode.AUTH_REQUIRED.value,
    "SSE_REPLAY_UNAVAILABLE": ErrorCode.RESOURCE_CONFLICT.value,
}
_PACK_D_D21_ERROR_BEHAVIOR: Dict[str, Dict[str, bool]] = {
    ErrorCode.RESOURCE_NOT_FOUND.value: {"retryable": False, "userActionable": True},
    ErrorCode.AUTH_REQUIRED.value: {"retryable": False, "userActionable": True},
    ErrorCode.AUTH_EXPIRED.value: {"retryable": False, "userActionable": True},
    ErrorCode.FORBIDDEN.value: {"retryable": False, "userActionable": False},
    ErrorCode.RATE_LIMITED.value: {"retryable": True, "userActionable": True},
    ErrorCode.VALIDATION_FAILED.value: {"retryable": False, "userActionable": True},
    ErrorCode.BUSINESS_RULE_VIOLATION.value: {"retryable": False, "userActionable": True},
    ErrorCode.IDEMPOTENCY_CONFLICT.value: {"retryable": False, "userActionable": True},
    ErrorCode.PRECONDITION_FAILED.value: {"retryable": False, "userActionable": True},
    ErrorCode.CONFIRMATION_REQUIRED.value: {"retryable": False, "userActionable": True},
    ErrorCode.TWO_MAN_SIGNATURE_REQUIRED.value: {"retryable": False, "userActionable": True},
    ErrorCode.HUMAN_GATE_PENDING.value: {"retryable": False, "userActionable": True},
    ErrorCode.HUMAN_GATE_REJECTED.value: {"retryable": False, "userActionable": True},
    ErrorCode.HUMAN_GATE_EXPIRED.value: {"retryable": False, "userActionable": True},
    ErrorCode.RESOURCE_CONFLICT.value: {"retryable": False, "userActionable": True},
    ErrorCode.OPERATION_NOT_ALLOWED.value: {"retryable": False, "userActionable": True},
    ErrorCode.DEPENDENCY_UNAVAILABLE.value: {"retryable": True, "userActionable": True},
    ErrorCode.UPSTREAM_TIMEOUT.value: {"retryable": True, "userActionable": True},
    ErrorCode.UPSTREAM_ERROR.value: {"retryable": True, "userActionable": True},
    ErrorCode.INTERNAL_ERROR.value: {"retryable": False, "userActionable": False},
    ErrorCode.NOT_IMPLEMENTED.value: {"retryable": False, "userActionable": False},
    ErrorCode.MAINTENANCE_MODE.value: {"retryable": True, "userActionable": True},
    ErrorCode.KILL_SWITCH_ACTIVE.value: {"retryable": False, "userActionable": False},
    ErrorCode.SAFE_MODE_ACTIVE.value: {"retryable": False, "userActionable": False},
    ErrorCode.DEGRADED_READ_ONLY.value: {"retryable": False, "userActionable": False},
    ErrorCode.REQUEST_TOO_LARGE.value: {"retryable": False, "userActionable": True},
}
def _status_error_code(status_code: int) -> str:
    return _ERROR_CODE_BY_STATUS.get(status_code, ErrorCode.VALIDATION_FAILED.value)
def _canonical_error_code_value(code: Any, *, status_code: Optional[int] = None) -> str:
    raw = str(getattr(code, "value", code) or "").strip()
    if not raw and status_code is not None:
        return _status_error_code(status_code)
    candidate = _LEGACY_ERROR_CODE_ALIASES.get(raw, raw)
    try:
        return ErrorCode(candidate).value
    except ValueError:
        if status_code is not None:
            return _status_error_code(status_code)
        return ErrorCode.INTERNAL_ERROR.value
def _pack_d_error_metadata(code: Any, *, status_code: Optional[int] = None) -> Dict[str, Any]:
    code_value = _canonical_error_code_value(code, status_code=status_code)
    behavior = _PACK_D_D21_ERROR_BEHAVIOR.get(
        code_value,
        _PACK_D_D21_ERROR_BEHAVIOR[ErrorCode.INTERNAL_ERROR.value],
    )
    return {
        "code": code_value,
        "i18nKey": f"errors.{code_value}",
        "retryable": behavior["retryable"],
        "userActionable": behavior["userActionable"],
    }


from .bootstrap.dependencies import AppDependencies

app_deps = AppDependencies.create_default()
command_store = app_deps.command_store
session_lifecycle_store = SessionLifecycleStore(os.path.join(BFF_DATA_DIR, "session_lifecycle.json"))
agora_audit_store = AgoraAuditStore()
persona_write_owner = app_deps.persona_write_owner
ranking_write_owner = app_deps.ranking_write_owner
persona_reconciliation_mutation_port = PersonaProvisioningReconciliationMutationPort(
    persona_mutation_port=persona_write_owner,
)
read_store: ReadSurfacePorts = app_deps.read_surface


def _record_agora_audit_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Route mutation audits to the dedicated writer.

    Focused tests may provide a legacy-compatible fake read store; retaining
    that explicit seam avoids changing their fixture contract while production
    ``ReadSurfacePorts`` remains strictly read-only.
    """
    legacy_writer = getattr(read_store, "record_agora_audit_event", None)
    if callable(legacy_writer):
        return legacy_writer(event)
    return agora_audit_store.record_agora_audit_event(event)
settings_store: SettingsStore = app_deps.settings_store
_COMMAND_AUTH_CONTEXT: Dict[str, Dict[str, Optional[str]]] = {}
downstream_health_monitor = DownstreamHealthMonitor(
    state_path=os.path.join(BFF_DATA_DIR, "downstream_health.sqlite3"),
)
_RETRYABLE_CAPITAL_COMMAND_TYPES = {
    CommandType.APPROVED_APPLY.value,
    CommandType.EMERGENCY_CONTAINMENT.value,
}
def _retryable_terminal_capital_command(record: Dict[str, Any]) -> bool:
    return bool(
        record.get("type") in _RETRYABLE_CAPITAL_COMMAND_TYPES
        and record.get("status")
        in {CommandStatus.FAILED.value, CommandStatus.TIMEOUT.value}
        and isinstance(record.get("error"), dict)
        and record["error"].get("retryable") is True
    )
_BFF_FOUNDATION_POLICY_VERSION = "2026-04-27"
_DEV_LOGIN_IDENTITY_DEFS: Dict[str, Dict[str, Any]] = {
    "viewer": {"roles": ("viewer",), "subject_suffix": "viewer"},
    "operator": {"roles": ("operator",), "subject_suffix": "operator"},
    "approver": {"roles": ("approver",), "subject_suffix": "approver"},
    "risk_owner": {"roles": ("risk_owner",), "subject_suffix": "risk-owner"},
    "operator_a": {"roles": ("operator",), "subject_suffix": "operator-a"},
    "operator_b": {"roles": ("operator",), "subject_suffix": "operator-b"},
}
def _dev_login_forbidden_environment() -> bool:
    env_name = os.getenv("PANTHEON_ENV", "").strip().lower()
    deployment_stage = os.getenv("PANTHEON_DEPLOYMENT_STAGE", "").strip().lower()
    return env_name in _PRODUCTION_STRICT_ENVIRONMENTS or deployment_stage in _PRODUCTION_STRICT_ENVIRONMENTS
def _dev_login_bool_env(name: str, *, default: bool) -> bool:
    return _bool_from_env(name, default=default)
def _dev_login_identity_registry() -> Dict[str, Dict[str, Any]]:
    """Build the configured dev-login identity profiles from environment.

    Each identity requires its own dedicated ``PANTHEON_BFF_DEV_LOGIN_<NAME>_
    CLIENT_ID``/``_CLIENT_SECRET`` pair so distinct actors (e.g. operator A
    vs. operator B) never share a credential or a subject. Only the
    ``operator`` identity falls back to the legacy shared
    ``PANTHEON_BFF_DEV_LOGIN_CLIENT_ID``/``PANTHEON_BFF_OIDC_CLIENT_ID``
    credential for backward compatibility; unconfigured identities are simply
    absent from the registry (dev-login as that identity is unavailable, it
    does not fall back to a shared credential).
    """
    registry: Dict[str, Dict[str, Any]] = {}
    for name, base in _DEV_LOGIN_IDENTITY_DEFS.items():
        env_prefix = f"PANTHEON_BFF_DEV_LOGIN_{name.upper()}"
        client_id = os.getenv(f"{env_prefix}_CLIENT_ID", "").strip()
        client_secret = os.getenv(f"{env_prefix}_CLIENT_SECRET", "").strip()
        if not (client_id and client_secret) and name == "operator":
            client_id = _first_nonblank(
                os.getenv("PANTHEON_BFF_DEV_LOGIN_CLIENT_ID"),
                os.getenv("PANTHEON_BFF_OIDC_CLIENT_ID"),
            )
            client_secret = _first_nonblank(
                os.getenv("PANTHEON_BFF_DEV_LOGIN_CLIENT_SECRET"),
                os.getenv("PANTHEON_BFF_OIDC_CLIENT_SECRET"),
            )
        if not (client_id and client_secret):
            continue

        tenant_id = _first_nonblank(
            os.getenv(f"{env_prefix}_TENANT_ID"),
            os.getenv("PANTHEON_BFF_TENANT_ID"),
            os.getenv("PANTHEON_BFF_DEFAULT_TENANT_ID"),
            os.getenv("PANTHEON_TENANT_ID"),
            "tenant-dev",
        )
        allowed_tenants = _env_csv(f"{env_prefix}_ALLOWED_TENANTS") or [tenant_id]
        if tenant_id not in allowed_tenants:
            allowed_tenants = [tenant_id] + list(allowed_tenants)

        mfa_verified = _dev_login_bool_env(f"{env_prefix}_MFA_VERIFIED", default=False)

        registry[name] = {
            "identity": name,
            "client_id": client_id,
            "client_secret": client_secret,
            "roles": sorted(base["roles"]),
            "subject": f"pantheon-dev-{base['subject_suffix']}",
            "tenant_id": tenant_id,
            "allowed_tenants": allowed_tenants,
            "mfa_verified": mfa_verified,
        }
    return registry
def _dev_login_enabled() -> bool:
    if _dev_login_forbidden_environment():
        return False
    return bool(_dev_login_identity_registry())
def _extract_identity(
    authorization: Optional[str],
    mfa_token: Optional[str] = None,
    session_cookie: Optional[str] = None,
) -> OperatorIdentity:
    if _bff_auth_stub_enabled():
        if authorization and authorization.startswith("Bearer "):
            raw = authorization[len("Bearer "):].strip()
            if raw.count(".") == 2:
                try:
                    return _extract_identity_jwt(authorization, mfa_token=mfa_token)
                except Exception:
                    pass
        if not authorization and session_cookie:
            try:
                identity = _extract_identity_jwt(f"Bearer {session_cookie}", mfa_token=mfa_token)
                return identity.model_copy(update={"token_kind": "cookie"})
            except Exception:
                pass
        return _extract_identity_stub(authorization)
    # Cookie session: treat cookie value as a bearer token when no Authorization header present.
    if not authorization and session_cookie:
        identity = _extract_identity_jwt(f"Bearer {session_cookie}", mfa_token=mfa_token)
        identity = identity.model_copy(update={"token_kind": "cookie"})
        return identity
    return _extract_identity_jwt(authorization, mfa_token=mfa_token)
def _extract_identity_stub(authorization: Optional[str]) -> OperatorIdentity:
    """Legacy colon-format stub for PANTHEON_BFF_AUTH_STUB=true only."""
    if not authorization or not authorization.startswith("Bearer "):
        raise _bff_error(
            status_code=401,
            code=ErrorCode.AUTH_REQUIRED,
            message="Missing or invalid Authorization header",
            reason="Token is absent or not a Bearer token",
            suggestion="Re-authenticate and include a valid Bearer token",
        )
    token = authorization[len("Bearer "):].strip()
    if not token:
        raise _bff_error(
            status_code=401,
            code=ErrorCode.AUTH_REQUIRED,
            message="Missing or invalid Authorization header",
            reason="Token is absent or not a Bearer token",
            suggestion="Re-authenticate and include a valid Bearer token",
        )
    if ":" not in token:
        allowed_bare_tokens = set(_env_csv(_BFF_STUB_LEGACY_BARE_TOKENS_ENV))
        if token not in allowed_bare_tokens:
            raise _bff_error(
                status_code=403,
                code=ErrorCode.FORBIDDEN,
                message="Stub bearer token must include explicit roles",
                reason="AUTH_STUB_TOKEN_NO_ROLES",
                suggestion="Use Bearer <operator_id>:<comma_roles> for dev stub auth",
            )
        lowered = token.lower()
        inferred_roles = ["operator"]
        if lowered.startswith("admin_"):
            inferred_roles = ["admin"]
        elif lowered.startswith("analyst_"):
            inferred_roles = ["analyst"]
        elif lowered.startswith("viewer_"):
            inferred_roles = ["viewer"]
        capabilities = _stub_identity_capabilities([], inferred_roles)
        return OperatorIdentity(
            operator_id=token,
            roles=inferred_roles,
            mfa_verified="mfa" in lowered,
            claims={"sub": token, "roles": inferred_roles, "capabilities": capabilities},
            token_kind="stub",
        )
    parts = token.split(":")
    operator_id = parts[0] if parts else "unknown"
    roles = parts[1].split(",") if len(parts) > 1 else ["operator"]

    mfa_verified = False
    tenant_ids = None
    token_capabilities = []

    if len(parts) > 2:
        if parts[2] == "mfa":
            mfa_verified = True
            if len(parts) > 3 and parts[3]:
                token_capabilities = parts[3].split(",")
            if len(parts) > 4 and parts[4]:
                tenant_ids = parts[4].split(",")
        else:
            tenant_ids = parts[2].split(",")
            if len(parts) > 3 and parts[3]:
                token_capabilities = parts[3].split(",")

    capabilities = _stub_identity_capabilities(token_capabilities, roles)
    claims = {"sub": operator_id, "roles": roles, "capabilities": capabilities}
    if tenant_ids:
        claims["tenant_ids"] = tenant_ids
        claims["tenantIds"] = tenant_ids

    return OperatorIdentity(
        operator_id=operator_id,
        roles=roles,
        mfa_verified=mfa_verified,
        claims=claims,
        token_kind="stub",
    )
def _stub_identity_capabilities(
    token_capabilities: List[str],
    roles: List[str],
) -> List[str]:
    normalized_roles = {str(role or "").strip().lower() for role in roles}
    if not normalized_roles.intersection(_BFF_STUB_CAPABILITY_ROLES):
        return []
    return _dedupe_nonblank_strings(
        [
            *token_capabilities,
            *_env_csv("PANTHEON_BFF_STUB_CAPABILITIES"),
        ]
    )
def _with_structured_identity_capabilities(identity: OperatorIdentity) -> OperatorIdentity:
    if identity.token_kind != "structured":
        return identity
    claims = dict(identity.claims or {})
    raw_capabilities = claims.get("capabilities") or claims.get("capability") or []
    if isinstance(raw_capabilities, str):
        token_capabilities = _split_claim_string(raw_capabilities)
    elif isinstance(raw_capabilities, list):
        token_capabilities = [str(cap) for cap in raw_capabilities]
    else:
        token_capabilities = []
    capabilities = _stub_identity_capabilities(token_capabilities, identity.roles)
    if capabilities:
        claims["capabilities"] = capabilities
    else:
        claims.pop("capabilities", None)
        claims.pop("capability", None)
    try:
        return identity.model_copy(update={"claims": claims})
    except AttributeError:
        return OperatorIdentity(
            operator_id=identity.operator_id,
            roles=identity.roles,
            mfa_verified=identity.mfa_verified,
            claims=claims,
            token_kind=identity.token_kind,
        )
def _extract_identity_jwt(
    authorization: Optional[str],
    mfa_token: Optional[str] = None,
) -> OperatorIdentity:
    """JWT/RBAC auth facade for production. Validates issuer, audience, expiry, subject."""
    try:
        from services.runtime_auth_inbound import AuthError, validate_request_auth
    except ImportError:
        from runtime_auth_inbound import AuthError, validate_request_auth  # type: ignore[no-redef]

    bff_env = {
        "PANTHEON_RUNTIME_AUTH_MODE": os.getenv("PANTHEON_BFF_AUTH_MODE", "strict"),
        "PANTHEON_RUNTIME_JWT_SECRET": os.getenv("PANTHEON_BFF_JWT_SECRET", ""),
        "PANTHEON_RUNTIME_JWT_ISSUER": os.getenv("PANTHEON_BFF_JWT_ISSUER", ""),
        "PANTHEON_RUNTIME_JWT_AUDIENCE": os.getenv("PANTHEON_BFF_JWT_AUDIENCE", ""),
        "PANTHEON_RUNTIME_DEFAULT_ROLE": os.getenv("PANTHEON_BFF_DEFAULT_ROLE", "operator"),
        "PANTHEON_RUNTIME_MFA_REQUIRED": os.getenv("PANTHEON_BFF_MFA_REQUIRED", "false"),
        # OIDC/JWKS optional path — active only when JWKS_URI is set.
        "PANTHEON_RUNTIME_JWKS_URI": os.getenv("PANTHEON_BFF_JWKS_URI", ""),
        "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL": os.getenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", ""),
        "PANTHEON_RUNTIME_OIDC_ISSUER": os.getenv("PANTHEON_BFF_OIDC_ISSUER", ""),
        "PANTHEON_RUNTIME_OIDC_AUDIENCE": os.getenv("PANTHEON_BFF_OIDC_AUDIENCE", ""),
        "PANTHEON_RUNTIME_ROLE_CLAIMS": os.getenv("PANTHEON_BFF_ROLE_CLAIMS", ""),
        "PANTHEON_RUNTIME_ROLE_MAP": os.getenv("PANTHEON_BFF_ROLE_MAP", ""),
        "PANTHEON_RUNTIME_ROLE_MAP_MODE": os.getenv("PANTHEON_BFF_ROLE_MAP_MODE", ""),
        "PANTHEON_RUNTIME_MFA_CLAIMS": os.getenv("PANTHEON_BFF_MFA_CLAIMS", ""),
        "PANTHEON_RUNTIME_MFA_VALUES": os.getenv("PANTHEON_BFF_MFA_VALUES", ""),
        "PANTHEON_RUNTIME_REQUIRE_EMAIL_VERIFIED": os.getenv(
            "PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED",
            "false",
        ),
    }
    # External browser JWTs use the configured OIDC/JWKS verifier, while the
    # server-side dev-login exchange deliberately issues a short-lived HS256
    # BFF token.  Select the verifier from the signed token algorithm family so
    # enabling product OIDC does not disable governed CI/dev-login sessions.
    # This is only routing: issuer, audience and signature are still validated
    # by ``validate_request_auth`` before any claim is trusted.
    try:
        raw_token = str(authorization or "").split(None, 1)[1]
        header_segment = raw_token.split(".", 1)[0]
        header_segment += "=" * (-len(header_segment) % 4)
        unverified_alg = str(
            json.loads(base64.urlsafe_b64decode(header_segment).decode("utf-8")).get("alg")
            or ""
        ).upper()
    except Exception:
        unverified_alg = ""
    if unverified_alg == "HS256":
        bff_env["PANTHEON_RUNTIME_JWKS_URI"] = ""
        bff_env["PANTHEON_RUNTIME_OIDC_DISCOVERY_URL"] = ""
        bff_env["PANTHEON_RUNTIME_ROLE_CLAIMS"] = "roles,role"
        bff_env["PANTHEON_RUNTIME_ROLE_MAP"] = ""
        bff_env["PANTHEON_RUNTIME_ROLE_MAP_MODE"] = "passthrough"
        # Server-issued dev-login tokens are not browser identity tokens and do
        # not carry an email address. Keep the browser-only verification policy
        # on the asymmetric GCP Identity Platform path.
        bff_env["PANTHEON_RUNTIME_REQUIRE_EMAIL_VERIFIED"] = "false"

    mfa_required = bff_env["PANTHEON_RUNTIME_MFA_REQUIRED"].lower() == "true"
    try:
        ctx = validate_request_auth(
            authorization=authorization or "",
            mfa_header=mfa_token or "",
            mfa_required=mfa_required,
            env=bff_env,
        )
    except AuthError as exc:
        if exc.status_code == 403:
            code = ErrorCode.FORBIDDEN
        elif exc.code == "AUTH_JWT_EXPIRED":
            code = ErrorCode.AUTH_EXPIRED
        elif exc.code in ("MFA_REQUIRED", "MFA_VALIDATION_FAILED"):
            code = ErrorCode.AUTH_REQUIRED
        else:
            code = ErrorCode.AUTH_REQUIRED
        # Sanitize codes that would leak server config details.
        _opaque_codes = {
            "AUTH_JWT_SECRET_MISSING",
            "JWKS_FETCH_FAILED",
            "JWKS_NO_MATCHING_KEY",
            "JWKS_INVALID_KEY",
            "JWKS_LIBRARY_UNAVAILABLE",
            "OIDC_DISCOVERY_FAILED",
        }
        if exc.code in _opaque_codes:
            effective_status = 401
            effective_message = "JWT bearer token cannot be verified"
            effective_reason = "AUTH_TOKEN_UNVERIFIED"
        else:
            effective_status = exc.status_code
            effective_message = exc.message
            effective_reason = exc.code
        raise _bff_error(
            status_code=effective_status,
            code=code,
            message=effective_message,
            reason=effective_reason,
            suggestion=(
                "Re-authenticate with a valid JWT bearer token"
                if effective_status == 401
                else None
            ),
        )
    if not str(ctx.claims.get("sub") or "").strip():
        raise _bff_error(
            status_code=401,
            code=ErrorCode.AUTH_REQUIRED,
            message="JWT subject claim is required",
            reason="AUTH_JWT_SUBJECT_MISSING",
            suggestion="Re-authenticate with a valid JWT bearer token",
        )
    identity = OperatorIdentity(
        operator_id=ctx.actor_id,
        roles=sorted(ctx.roles),
        mfa_verified=ctx.mfa_verified,
        claims=dict(ctx.claims),
        token_kind=ctx.token_kind,
    )
    return _with_structured_identity_capabilities(identity)
def _bff_error(
    status_code: int,
    code: ErrorCode,
    message: str,
    reason: str,
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
    foundation_error: Optional[ErrorEnvelope] = None,
    policy_decision: Optional[PolicyDecision] = None,
    audit_action: Optional[AuditAction] = None,
) -> HTTPException:
    metadata = _pack_d_error_metadata(code, status_code=status_code)
    body = BffErrorEnvelope(
        error=BffErrorPayload(
            code=ErrorCode(metadata["code"]),
            i18nKey=metadata["i18nKey"],
            message=message,
            retryable=metadata["retryable"],
            userActionable=metadata["userActionable"],
            details=ErrorDetail(
                reason=reason,
                precondition_failed=precondition_failed,
                suggestion=suggestion,
            ),
        )
    )
    detail = body.model_dump()
    error_payload = detail.get("error") if isinstance(detail.get("error"), dict) else {}
    error_details = error_payload.get("details") if isinstance(error_payload.get("details"), dict) else None
    if error_details is not None:
        if details_extra:
            for key, value in details_extra.items():
                if value is not None:
                    error_details[key] = value
        clean_correlation_id = str(correlation_id or "").strip()
        if clean_correlation_id:
            error_details["correlationId"] = clean_correlation_id
            detail["correlationId"] = clean_correlation_id
    if foundation_error is not None:
        detail["foundation_error"] = foundation_error.to_dict()
    if policy_decision is not None:
        detail["policy_decision"] = policy_decision.to_dict()
    if audit_action is not None:
        detail["audit_action"] = audit_action.to_dict()
    return HTTPException(status_code=status_code, detail=detail)
_FOUNDATION_COMMAND_ROUTE = "POST /api/v1/operator/commands"
_FINAL_COMMAND_ROUTE = "POST /bff/v1/commands"
_PATH_DEDUPE_DEPRECATED_SINCE = "2026-05-25T08:40:02Z"
_PATH_DEDUPE_SUNSET_HTTP_DATE = "Mon, 25 May 2026 00:00:00 GMT"
def _foundation_environment_scope() -> EnvironmentScope:
    raw = os.getenv("PANTHEON_ENV", "dev").strip().lower()
    if "live" in raw:
        name = EnvironmentName.LIVE
    elif "canary" in raw:
        name = EnvironmentName.CANARY
    elif "paper" in raw:
        name = EnvironmentName.PAPER
    elif "sandbox" in raw:
        name = EnvironmentName.SANDBOX
    else:
        name = EnvironmentName.DEV
    return EnvironmentScope(
        name=name,
        region=os.getenv("PANTHEON_REGION") or None,
        timezone=os.getenv("PANTHEON_TIMEZONE", "UTC"),
    )
def _foundation_actor_ref(identity: OperatorIdentity) -> ActorRef:
    return ActorRef(
        actor_type=ActorType.USER,
        actor_id=identity.operator_id,
        roles=identity.roles,
    )
def _command_runtime_auth_context(
    *,
    command_id: str,
    authorization: Optional[str],
    mfa_token: Optional[str],
    identity: OperatorIdentity,
) -> Dict[str, Any]:
    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization[len("Bearer "):]
    effective_mfa_token = mfa_token or ("000000" if identity.mfa_verified else None)
    if raw_token or effective_mfa_token:
        _COMMAND_AUTH_CONTEXT[command_id] = {
            "auth_token": raw_token,
            "mfa_token": effective_mfa_token,
        }
    return {
        "token_kind": identity.token_kind,
        "bearer_token_present": bool(raw_token),
        "mfa_token_present": bool(effective_mfa_token),
    }
def _foundation_request_payload(
    cmd: OperatorCommand,
    raw_payload: Dict[str, Any],
    *,
    route: str = _FOUNDATION_COMMAND_ROUTE,
    source_route: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "route": route,
        "command": cmd.command.value,
        "target": cmd.target.model_dump(),
        "params": dict(cmd.params),
        "audit_context": cmd.audit_context.model_dump(),
        "raw_payload": raw_payload,
    }
    if source_route:
        payload["source_route"] = source_route
    return payload
def _foundation_idempotency_payload(request_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.loads(json.dumps(request_payload))
    audit_context = payload.get("audit_context")
    if isinstance(audit_context, dict):
        audit_context.pop("timestamp", None)
    raw_payload = payload.get("raw_payload")
    if isinstance(raw_payload, dict):
        raw_audit_context = raw_payload.get("audit_context")
        if isinstance(raw_audit_context, dict):
            raw_audit_context.pop("timestamp", None)
    return payload
def _foundation_route_metadata(route: str, source_route: Optional[str] = None) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"route": route}
    if source_route:
        metadata["source_route"] = source_route
    return metadata
def _build_foundation_trace(
    *,
    environment: EnvironmentScope,
    actor_ref: ActorRef,
    trace_id: Optional[str],
    correlation_id: Optional[str],
    request_id: Optional[str],
    idempotency_key: Optional[str],
) -> TraceContext:
    clean_trace_id = str(trace_id or "").strip()
    if clean_trace_id:
        return TraceContext(
            trace_id=clean_trace_id,
            correlation_id=str(correlation_id or clean_trace_id).strip(),
            environment=environment,
            actor_ref=actor_ref,
            source_system="pantheon-bff",
            request_id=str(request_id or "").strip() or None,
            idempotency_key=str(idempotency_key or "").strip() or None,
        )
    return TraceContext.new(
        environment=environment,
        actor_ref=actor_ref,
        source_system="pantheon-bff",
        correlation_id=str(correlation_id or "").strip() or None,
        request_id=str(request_id or "").strip() or None,
        idempotency_key=str(idempotency_key or "").strip() or None,
    )
def _build_foundation_command_context(
    *,
    cmd: OperatorCommand,
    identity: OperatorIdentity,
    raw_payload: Dict[str, Any],
    trace_id: Optional[str],
    correlation_id: Optional[str],
    request_id: Optional[str],
    idempotency_key: Optional[str],
    route: str = _FOUNDATION_COMMAND_ROUTE,
    source_route: Optional[str] = None,
) -> Dict[str, Any]:
    environment = _foundation_environment_scope()
    actor_ref = _foundation_actor_ref(identity)
    route_metadata = _foundation_route_metadata(route, source_route)
    authority_scope = AuthorityScope(
        action=cmd.command.value,
        target_type=cmd.target.type.value,
        target_id=cmd.target.id,
        environment=environment,
        runtime_id=cmd.target.id if cmd.target.type == ObjectType.RUNTIME else None,
        attributes=route_metadata,
    )
    request_payload = _foundation_request_payload(
        cmd,
        raw_payload,
        route=route,
        source_route=source_route,
    )
    trace = _build_foundation_trace(
        environment=environment,
        actor_ref=actor_ref,
        trace_id=trace_id,
        correlation_id=correlation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    command_envelope = CommandEnvelope.new(
        command_type=cmd.command.value,
        actor_ref=actor_ref,
        authority_scope=authority_scope,
        payload=request_payload,
        trace=trace,
        idempotency_key=str(idempotency_key or "").strip() or None,
    )
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=command_envelope.idempotency_key,
        operation_type=f"bff.{cmd.command.value}",
        target_ref=authority_scope.target_ref,
        request_payload=_foundation_idempotency_payload(request_payload),
        trace_id=command_envelope.trace.trace_id,
    )
    policy_decision = PolicyDecision.make(
        policy_id="bff.command.admission",
        policy_version=_BFF_FOUNDATION_POLICY_VERSION,
        decision=PolicyDecisionValue.ALLOW,
        actor_ref=actor_ref,
        action=cmd.command.value,
        target_ref=authority_scope.target_ref,
        environment=environment,
        trace_id=command_envelope.trace.trace_id,
    )
    audit_action = AuditAction.record(
        actor_ref=actor_ref,
        action_type="bff.command.accepted",
        target_ref=authority_scope.target_ref,
        environment=environment,
        reason=cmd.audit_context.reason or "operator command admission",
        trace=command_envelope.trace,
        payload=request_payload,
        policy_decision_ref=policy_decision.decision_id,
        metadata=route_metadata,
    )
    return {
        "admission_route": route,
        "source_route": source_route,
        "command_envelope": command_envelope,
        "trace_context": command_envelope.trace,
        "idempotency_record": idempotency_record,
        "policy_decision": policy_decision,
        "audit_action": audit_action,
        "request_payload": request_payload,
    }
def _serialize_foundation_context(context: Dict[str, Any]) -> Dict[str, Any]:
    serialized = {
        "admission_route": context.get("admission_route"),
        "trace_context": context["trace_context"].to_dict(),
        "command_envelope": context["command_envelope"].to_dict(),
        "idempotency_record": context["idempotency_record"].to_dict(),
        "policy_decision": context["policy_decision"].to_dict(),
        "audit_action": context["audit_action"].to_dict(),
    }
    if context.get("source_route"):
        serialized["source_route"] = context.get("source_route")
    return serialized
def _extract_error_fields(exc: HTTPException) -> Dict[str, Any]:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error = detail.get("error") if isinstance(detail.get("error"), dict) else {}
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    details_extra = {
        key: value
        for key, value in details.items()
        if key not in {"reason", "precondition_failed", "suggestion"} and value is not None
    }
    code_value = _canonical_error_code_value(
        error.get("code") or ErrorCode.VALIDATION_FAILED.value,
        status_code=exc.status_code,
    )
    try:
        code = ErrorCode(code_value)
    except ValueError:
        code = ErrorCode.VALIDATION_FAILED
    return {
        "status_code": exc.status_code,
        "code": code,
        "message": error.get("message") or str(exc.detail),
        "reason": details.get("reason") or str(exc.detail),
        "precondition_failed": details.get("precondition_failed"),
        "suggestion": details.get("suggestion"),
        "details_extra": details_extra,
        "correlation_id": detail.get("correlationId") or details_extra.get("correlationId"),
    }
def _foundation_bff_error(
    exc: HTTPException,
    *,
    foundation_context: Dict[str, Any],
) -> HTTPException:
    fields = _extract_error_fields(exc)
    command_envelope: CommandEnvelope = foundation_context["command_envelope"]
    admission_route = str(foundation_context.get("admission_route") or _FOUNDATION_COMMAND_ROUTE)
    source_route = str(foundation_context.get("source_route") or "").strip() or None
    route_metadata = _foundation_route_metadata(admission_route, source_route)
    if fields["status_code"] == 403:
        policy_decision = PolicyDecision.make(
            policy_id="bff.command.admission",
            policy_version=_BFF_FOUNDATION_POLICY_VERSION,
            decision=PolicyDecisionValue.DENY,
            actor_ref=command_envelope.actor_ref,
            action=command_envelope.command_type,
            target_ref=command_envelope.authority_scope.target_ref,
            environment=command_envelope.authority_scope.environment,
            trace_id=command_envelope.trace.trace_id,
            reasons=[fields["reason"]],
        )
        foundation_error = ErrorEnvelope.policy_denial(
            message=fields["message"],
            trace=command_envelope.trace,
            policy_decision_ref=policy_decision.decision_id,
            details={
                "reason": fields["reason"],
                "precondition_failed": fields["precondition_failed"],
                **fields["details_extra"],
            },
        )
        audit_action = AuditAction.record(
            actor_ref=command_envelope.actor_ref,
            action_type="bff.command.policy_denied",
            target_ref=command_envelope.authority_scope.target_ref,
            environment=command_envelope.authority_scope.environment,
            reason=fields["reason"],
            trace=command_envelope.trace,
            payload=foundation_context["request_payload"],
            policy_decision_ref=policy_decision.decision_id,
            metadata=route_metadata,
        )
        return _bff_error(
            fields["status_code"],
            fields["code"],
            fields["message"],
            fields["reason"],
            precondition_failed=fields["precondition_failed"],
            suggestion=fields["suggestion"],
            details_extra=fields["details_extra"],
            correlation_id=fields["correlation_id"],
            foundation_error=foundation_error,
            policy_decision=policy_decision,
            audit_action=audit_action,
        )

    if fields["status_code"] in {400, 422}:
        foundation_error = ErrorEnvelope.validation(
            message=fields["message"],
            trace=command_envelope.trace,
            error_code=fields["code"].value,
            details={
                "reason": fields["reason"],
                "precondition_failed": fields["precondition_failed"],
                **fields["details_extra"],
            },
        )
    else:
        foundation_error = ErrorEnvelope(
            error_id=foundation_id("err"),
            error_code=fields["code"].value,
            message=fields["message"],
            error_kind=ErrorKind.INVARIANT_VIOLATION,
            trace=command_envelope.trace,
            status_code=fields["status_code"],
            details={
                "reason": fields["reason"],
                "precondition_failed": fields["precondition_failed"],
                **fields["details_extra"],
            },
        )
    audit_action = AuditAction.record(
        actor_ref=command_envelope.actor_ref,
        action_type="bff.command.rejected",
        target_ref=command_envelope.authority_scope.target_ref,
        environment=command_envelope.authority_scope.environment,
        reason=fields["reason"],
        trace=command_envelope.trace,
        payload=foundation_context["request_payload"],
        metadata=route_metadata,
    )
    return _bff_error(
        fields["status_code"],
        fields["code"],
        fields["message"],
        fields["reason"],
        precondition_failed=fields["precondition_failed"],
        suggestion=fields["suggestion"],
        details_extra=fields["details_extra"],
        correlation_id=fields["correlation_id"],
        foundation_error=foundation_error,
        audit_action=audit_action,
    )
def _foundation_idempotency_conflict_error(
    *,
    foundation_context: Dict[str, Any],
    existing_command_id: str,
) -> HTTPException:
    command_envelope: CommandEnvelope = foundation_context["command_envelope"]
    idempotency_record: IdempotencyRecord = foundation_context["idempotency_record"]
    admission_route = str(foundation_context.get("admission_route") or _FOUNDATION_COMMAND_ROUTE)
    source_route = str(foundation_context.get("source_route") or "").strip() or None
    message = "Idempotency key was already used with a different command payload"
    reason = (
        f"idempotency_key={idempotency_record.idempotency_key} is already bound "
        f"to command {existing_command_id}"
    )
    foundation_error = ErrorEnvelope(
        error_id=foundation_id("err"),
        error_code=ErrorCode.IDEMPOTENCY_CONFLICT.value,
        message=message,
        error_kind=ErrorKind.IDEMPOTENCY_CONFLICT,
        trace=command_envelope.trace,
        status_code=409,
        details={
            "reason": reason,
            "existing_command_id": existing_command_id,
            "idempotency_key": idempotency_record.idempotency_key,
        },
    )
    audit_action = AuditAction.record(
        actor_ref=command_envelope.actor_ref,
        action_type="bff.command.idempotency_conflict",
        target_ref=command_envelope.authority_scope.target_ref,
        environment=command_envelope.authority_scope.environment,
        reason=reason,
        trace=command_envelope.trace,
        payload=foundation_context["request_payload"],
        metadata=_foundation_route_metadata(admission_route, source_route),
    )
    return _bff_error(
        409,
        ErrorCode.IDEMPOTENCY_CONFLICT,
        message,
        reason,
        precondition_failed="idempotency_conflict",
        suggestion="Reuse the original payload for this key or submit with a new X-Idempotency-Key",
        foundation_error=foundation_error,
        audit_action=audit_action,
    )
def _foundation_audit_for_command_record(
    *,
    identity: OperatorIdentity,
    command_type: CommandType,
    target_type: ObjectType,
    target_id: str,
    payload: Dict[str, Any],
    reason: str,
    command_id: str,
    idempotency_key: str,
    route: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> AuditAction:
    environment = _foundation_environment_scope()
    actor_ref = _foundation_actor_ref(identity)
    trace = _build_foundation_trace(
        environment=environment,
        actor_ref=actor_ref,
        trace_id=command_id,
        correlation_id=command_id,
        request_id=command_id,
        idempotency_key=idempotency_key,
    )
    audit_metadata = {
        "route": route,
        "command": command_type.value,
        "idempotency_key": idempotency_key,
    }
    if metadata:
        audit_metadata.update({key: value for key, value in metadata.items() if value is not None})
    return AuditAction.record(
        actor_ref=actor_ref,
        action_type="bff.command.accepted",
        target_ref=f"{target_type.value}:{target_id}",
        environment=environment,
        reason=reason,
        trace=trace,
        payload={
            "command": command_type.value,
            "target": {"type": target_type.value, "id": target_id},
            "payload": payload,
        },
        metadata=audit_metadata,
    )
def _command_audit_action_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
    audit_action = foundation.get("audit_action") if isinstance(foundation.get("audit_action"), dict) else None
    if audit_action:
        return dict(audit_action)
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    audit_foundation = audit.get("foundation") if isinstance(audit.get("foundation"), dict) else {}
    audit_action = (
        audit_foundation.get("audit_action")
        if isinstance(audit_foundation.get("audit_action"), dict)
        else None
    )
    return dict(audit_action or {})
def _audit_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
def _project_command_record_audit_event(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    command_id = str(record.get("command_id") or "").strip()
    if not command_id:
        return None
    target = record.get("target") if isinstance(record.get("target"), dict) else {}
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
    audit_action = _command_audit_action_from_record(record)
    idempotency_record = (
        foundation.get("idempotency_record")
        if isinstance(foundation.get("idempotency_record"), dict)
        else {}
    )
    audit_actor_ref = audit_action.get("actor_ref") if isinstance(audit_action.get("actor_ref"), dict) else {}
    metadata = audit_action.get("metadata") if isinstance(audit_action.get("metadata"), dict) else {}
    trace_context = foundation.get("trace_context") if isinstance(foundation.get("trace_context"), dict) else {}
    action_type = str(record.get("type") or metadata.get("command") or "").strip()
    target_type = str(target.get("type") or "").strip()
    target_id = str(target.get("id") or "").strip()
    timestamp = str(
        audit.get("timestamp")
        or audit_action.get("timestamp")
        or record.get("submitted_at")
        or utc_now()
    )
    reason = str(audit.get("reason") or audit_action.get("reason") or action_type or "operator command")
    event = {
        "entry_id": str(audit_action.get("action_id") or f"audit-{command_id}"),
        "actor": str(
            audit.get("operator_id")
            or audit.get("actor")
            or audit_actor_ref.get("actor_id")
            or "operator"
        ),
        "action_type": action_type,
        "target_type": target_type,
        "target_id": target_id,
        "timestamp": timestamp,
        "outcome": "accepted" if record.get("status") == CommandStatus.SUBMITTED.value else record.get("status"),
        "audit_context": {
            "reason": reason,
            "command_id": command_id,
            "receipt_id": command_id,
            "idempotency_key": (
                idempotency_record.get("idempotency_key")
                or metadata.get("idempotency_key")
                or audit.get("idempotency_key")
            ),
            "action_id": audit.get("action_id"),
            "foundation_action_type": audit_action.get("action_type"),
        },
        "evidence_refs": audit.get("evidence_refs") if isinstance(audit.get("evidence_refs"), list) else [],
        "command_ref": command_id,
        "trace_id": audit_action.get("trace_id") or trace_context.get("trace_id"),
        "correlation_id": (
            audit_action.get("correlation_id")
            or trace_context.get("correlation_id")
        ),
        "payload_checksum": audit_action.get("payload_checksum"),
        "audit_action": audit_action or None,
        "metadata": {
            "source": "command_store",
            "route": metadata.get("route"),
            "source_route": metadata.get("source_route"),
            "live_capital_side_effects": audit.get("live_capital_side_effects", False),
        },
    }
    return json.loads(json.dumps(event))
def _audit_event_matches(
    event: Dict[str, Any],
    *,
    actor: Optional[str] = None,
    action_types: Optional[List[str]] = None,
    target_type: Optional[str] = None,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
) -> bool:
    if actor and event.get("actor") != actor:
        return False
    if action_types:
        allowed = {value for value in action_types if value}
        if event.get("action_type") not in allowed:
            return False
    if target_type and event.get("target_type") != target_type:
        return False
    event_dt = _audit_datetime(event.get("timestamp"))
    if from_ts is not None and (event_dt is None or event_dt < from_ts):
        return False
    if to_ts is not None and (event_dt is None or event_dt > to_ts):
        return False
    return True
def _list_governance_audit_events(
    *,
    actor: Optional[str] = None,
    action_types: Optional[List[str]] = None,
    target_type: Optional[str] = None,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
    include_command_store: bool = True,
    include_fixture_pack: bool = True,
) -> List[Dict[str, Any]]:
    events = read_store.list_governance_audit_events(
        actor=actor,
        action_types=action_types,
        target_type=target_type,
        from_ts=from_ts,
        to_ts=to_ts,
        include_fixture_pack=include_fixture_pack,
    )
    events_by_id: Dict[str, Dict[str, Any]] = {
        str(event.get("entry_id") or event.get("auditId") or event.get("id") or index): event
        for index, event in enumerate(events)
    }
    # Agora mutation audits are owned by the dedicated append-only writer,
    # not by the read-only surface ports.  Merge them into the governance
    # audit readback so entity links and post-restart queries remain durable.
    for event in agora_audit_store.list_agora_audit_events(
        actor=actor,
        action_types=action_types,
        target_type=target_type,
        from_ts=from_ts,
        to_ts=to_ts,
    ):
        if _audit_event_matches(
            event,
            actor=actor,
            action_types=action_types,
            target_type=target_type,
            from_ts=from_ts,
            to_ts=to_ts,
        ):
            events_by_id.setdefault(
                str(event.get("entry_id") or event.get("auditId") or event.get("id")),
                event,
            )
    if include_command_store:
        for record in command_store._get_all_commands():
            event = _project_command_record_audit_event(record)
            if not event or not _audit_event_matches(
                event,
                actor=actor,
                action_types=action_types,
                target_type=target_type,
                from_ts=from_ts,
                to_ts=to_ts,
            ):
                continue
            events_by_id.setdefault(str(event.get("entry_id")), event)
    merged = list(events_by_id.values())
    merged.sort(key=lambda event: str(event.get("timestamp") or ""), reverse=True)
    return json.loads(json.dumps(merged))
_APPROVE_DEPLOYMENT_REQUIRED = {"deployment_plan_id", "approval_decision"}
_VALID_APPROVAL_DECISIONS = {"approve", "reject"}
_APPROVE_DECISION_REQUIRED = {"decision_id"}
_REJECT_DECISION_REQUIRED = {"decision_id", "rejection_reason"}
_REQUEST_APPROVAL_REVISION_REQUIRED = {"decision_id", "revision_notes"}
_ESCALATE_DIFF_REQUIRED = {"plan_id", "escalation_reason"}
_PAUSE_RUNTIME_REQUIRED = {"runtime_binding_id", "pause_action"}
_VALID_PAUSE_ACTIONS = {"pause", "resume"}
_PAUSE_EXECUTION_REQUIRED = {"pause_new_entries", "cancel_open_orders"}
_ROLLBACK_REQUIRED = {"rollback_target_type", "target_id", "rollback_to_version"}
_VALID_ROLLBACK_TARGET_TYPES = {"deployment", "runtime"}
_APPROVE_ROLLBACK_REQUIRED = {"rollback_id"}
_REJECT_ROLLBACK_REQUIRED = {"rollback_id", "rejection_reason"}
_RISK_OFF_REQUIRED = {"reduce_exposure_pct"}
_SAFE_MODE_LEVELS = {"soft"}
_DRAWER_RUNTIME_COMMANDS = {
    CommandType.PAUSE_EXECUTION,
    CommandType.ISSUE_RISK_OFF,
    CommandType.LIQUIDATE_ALL,
    CommandType.HARD_ROLLBACK,
    CommandType.ISSUE_SAFE_MODE,
}
_LIVE_BROKER_SIGNAL_KEYS = {
    "account-mode",
    "account-type",
    "broker-mode",
    "broker-scope",
    "deployment-scope",
    "deployment-stage",
    "environment",
    "execution-mode",
    "order-mode",
    "runtime-mode",
    "scope",
    "target-env",
    "target-environment",
    "target-stage",
    "venue-mode",
}
_LIVE_BROKER_SIGNAL_VALUES = {
    "ibkr-live",
    "interactive-brokers-live",
    "live",
    "live-broker",
    "prod",
    "production",
    "staging-live",
}
_KILL_SWITCH_REQUIRED = {"scope", "activate"}
_VALID_SCOPES = {"persona", "pool", "all"}
_VALID_SEVERITIES = {"critical", "high", "medium"}
_APPROVE_EVO_REQUIRED = {"evolution_decision_id", "approval_action"}
_VALID_EVO_APPROVAL_ACTIONS = {"approve", "reject"}
_APPROVE_MUTATION_REQUIRED = {"decision_id"}
_REJECT_MUTATION_REQUIRED = {"decision_id"}
_REVIEW_MUTATION_REQUIRED = {"decision_id", "approval_decision_id"}
_EXECUTE_MUTATION_REQUIRED = {"decision_id"}
_RECORD_SPONSOR_DECISION_REQUIRED = {"committee_id", "sponsor_decision", "rationale_ref"}
_VALID_SPONSOR_DECISIONS = {"approved", "rejected", "conditional"}
_REMEDIATE_SENTINEL_REQUIRED = {"intervention_id", "remediation_action"}
_VALID_REMEDIATION_ACTIONS = {"resolve", "dismiss", "escalate"}
_DECIDE_V5_INTERVENTION_REQUIRED = {"intervention_id", "decision"}
_VALID_V5_INTERVENTION_DECISIONS = {"approve", "reject", "defer", "dismiss"}
_HUMAN_GATE_DECISIONS_BY_COMMAND: Dict[CommandType, str] = {
    CommandType.HUMAN_GATE_APPROVE: "approve",
    CommandType.HUMAN_GATE_REJECT: "reject",
    CommandType.HUMAN_GATE_REQUEST_MORE_EVIDENCE: "request_more_evidence",
    CommandType.HUMAN_GATE_REVOKE: "revoke",
    CommandType.HUMAN_GATE_EXTEND_TTL: "extend_ttl",
}
_HUMAN_GATE_REQUIRED = {"human_gate_item_id", "decision"}
_VALID_HUMAN_GATE_DECISIONS = set(_HUMAN_GATE_DECISIONS_BY_COMMAND.values())
_HUMAN_GATE_APPROVER_DECISIONS = {"approve", "reject", "revoke", "extend_ttl"}
_HUMAN_GATE_SELF_APPROVAL_DECISIONS = {"approve", "reject", "revoke"}
_HUMAN_GATE_HIGH_RISK_LEVELS = {"high", "critical"}
_HUMAN_GATE_DEFAULT_MAX_TTL_SECONDS = 604800
_HUMAN_GATE_REQUESTER_FIELDS = (
    "requester_id",
    "requesterId",
    "requested_by",
    "requestedBy",
    "submitted_by",
    "submittedBy",
    "created_by",
    "createdBy",
    "created_by_id",
    "createdById",
    "actor_id",
    "actorId",
)
_HUMAN_GATE_SOURCE_ID_FIELDS = (
    "source_record_id",
    "sourceRecordId",
    "approval_decision_id",
    "approvalDecisionId",
    "intervention_id",
    "interventionId",
)
_HUMAN_GATE_RISK_FIELDS = (
    "risk_level",
    "riskLevel",
    "downstream_risk_level",
    "downstreamRiskLevel",
    "downstream_action_risk_level",
    "downstreamActionRiskLevel",
    "priority",
    "severity",
)
_HUMAN_GATE_DOWNSTREAM_EXECUTED_STATES = {
    "applied",
    "complete",
    "completed",
    "committed",
    "executed",
    "succeeded",
    "success",
}
_HUMAN_GATE_DOWNSTREAM_EXECUTED_FIELDS = (
    "downstream_effect_status",
    "downstreamEffectStatus",
    "downstream_status",
    "downstreamStatus",
    "execution_status",
    "executionStatus",
    "effect_status",
    "effectStatus",
    "result_status",
    "resultStatus",
)
_HUMAN_GATE_DOWNSTREAM_EXECUTED_AT_FIELDS = (
    "downstream_executed_at",
    "downstreamExecutedAt",
    "executed_at",
    "executedAt",
    "applied_at",
    "appliedAt",
    "committed_at",
    "committedAt",
)
_EXECUTE_EVO_REQUIRED = {"evolution_decision_id", "action_type"}
_VALID_EVO_ACTION_TYPES = {"freeze", "retrain", "revalidate", "mutate", "retire"}
_OPERATOR_INCIDENT_HOME_ROUTE = "/operator/incidents"
_OPERATOR_DEPLOYMENT_REVIEW_ROUTE = "/operator/deployment-review"
_OPERATOR_HEALTH_STATUS_ROUTE = "/operator/health-status"
_OPERATOR_RUNTIME_STATE_ROUTE = "/operator/runtime-state"
_MANAGEMENT_READINESS_BASE_ROUTE = "/management/readiness"
_GOVERNANCE_REVIEW_QUEUE_ROUTE = "/governance-review-queue"
_GOVERNANCE_APPROVAL_QUEUE_ROUTE = "/governance-approval-queue"
_MUTATION_APPROVAL_ROLES = {
    "low": {"reviewer", "approver", "admin"},
    "medium": {"operator", "approver", "admin"},
    "high": {"approver", "admin"},
}
_MUTATION_REJECTION_ROLES = {
    "low": {"reviewer", "approver", "admin"},
    "medium": {"reviewer", "operator", "approver", "admin"},
    "high": {"approver", "admin"},
}
_MUTATION_REVIEW_ROLES = {
    "low": {"reviewer", "approver", "admin"},
    "medium": {"reviewer", "approver", "admin"},
    "high": {"approver", "admin"},
}
_MUTATION_EXECUTION_ROLES = {"operator", "admin"}
def _env_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
def _value_contains_live_broker_signal(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_value_contains_live_broker_signal(child) for child in value.values())
    if isinstance(value, list):
        return any(_value_contains_live_broker_signal(child) for child in value)
    token = _env_token(value)
    if token in _LIVE_BROKER_SIGNAL_VALUES:
        return True
    return bool(
        re.search(r"(^|-)live($|-)", token)
        and ("broker" in token or "ibkr" in token or "interactive-brokers" in token)
    )
def _payload_has_live_broker_signal(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            key_token = _env_token(key)
            if (
                key_token in _LIVE_BROKER_SIGNAL_KEYS
                and _value_contains_live_broker_signal(child)
            ):
                return True
            if _payload_has_live_broker_signal(child):
                return True
    elif isinstance(value, list):
        return any(_payload_has_live_broker_signal(child) for child in value)
    return False
def _command_targets_live_runtime(cmd: OperatorCommand) -> bool:
    if cmd.target.type != ObjectType.RUNTIME:
        return False
    target_id = _env_token(cmd.target.id)
    return bool(re.search(r"(^|-)live($|-)", target_id))
def _ensure_live_broker_scope_allowed(cmd: OperatorCommand, payload: Dict[str, Any]) -> None:
    if _bool_from_env("PANTHEON_LIVE_BROKER_ENABLED", default=False):
        return
    if not (_command_targets_live_runtime(cmd) or _payload_has_live_broker_signal(payload)):
        return
    env_name = os.getenv("PANTHEON_ENV", "dev").strip() or "dev"
    raise _bff_error(
        403,
        ErrorCode.PRECONDITION_FAILED,
        "Live broker scope is disabled for this BFF",
        f"PANTHEON_ENV={env_name} has PANTHEON_LIVE_BROKER_ENABLED=false",
        precondition_failed="live_broker_scope",
        suggestion=(
            "Use the staging-live BFF only after operator auth, governance, "
            "runtime kill-switch, and broker rehearsal gates are verified"
        ),
    )
def _require_admin_mfa(identity: OperatorIdentity, command_name: str) -> None:
    if "admin" not in identity.roles:
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            f"{command_name} requires 'admin' role",
            "Operator does not hold the admin role",
            precondition_failed="role_check",
            suggestion="Escalate to an admin-role operator",
        )
    if not identity.mfa_verified:
        raise _bff_error(
            403,
            ErrorCode.AUTH_REQUIRED,
            f"{command_name} requires MFA verification",
            "Admin action requires MFA validation",
            precondition_failed="mfa_check",
            suggestion="Provide a valid MFA token in your session",
        )
def _deployment_review_href(plan_id: str) -> str:
    return f"{_OPERATOR_DEPLOYMENT_REVIEW_ROUTE}?plan={plan_id}"
def _incident_detail_href(incident_id: str) -> str:
    return f"{_OPERATOR_INCIDENT_HOME_ROUTE}/{incident_id}"
def _runtime_command_context(runtime_id: str, incident_id: Optional[str] = None) -> Dict[str, Optional[str]]:
    runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
    binding_id = None
    capital_pool_id = None
    artifact_id = None
    artifact_version = None
    plan_id = None

    if runtime_binding:
        binding_id = str(runtime_binding.get("id") or runtime_binding.get("binding_id") or runtime_id)
        capital_pool_id = runtime_binding.get("capital_pool_id")
        artifact_id = runtime_binding.get("artifact_id")
        artifact_version = runtime_binding.get("artifact_version")
        plan_id = runtime_binding.get("plan_id")

    if incident_id:
        incident = read_store.get_incident(incident_id)
        if incident and str(incident.get("runtime_id") or "") == runtime_id:
            capital_pool_id = capital_pool_id or incident.get("capital_pool_id")
            artifact_id = artifact_id or incident.get("artifact_id")
            artifact_version = artifact_version or incident.get("artifact_version")

    if plan_id and not capital_pool_id:
        plan = read_store.get_deployment_plan(plan_id)
        if plan:
            capital_pool_id = plan.get("capital_pool_id")

    return {
        "runtime_id": runtime_id,
        "runtime_binding_id": binding_id or runtime_id,
        "capital_pool_id": capital_pool_id,
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
    }
def _validate_drawer_runtime_target(cmd: OperatorCommand) -> None:
    if cmd.command not in _DRAWER_RUNTIME_COMMANDS:
        return
    if cmd.target.type != ObjectType.RUNTIME:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{cmd.command.value} requires target.type = Runtime",
            "Drawer commands only accept Runtime targets",
        )
    if not str(cmd.target.id or "").strip():
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{cmd.command.value} requires a runtime target id",
            "target.id must be a non-empty runtime id",
        )
def _validate_audit_context(cmd: OperatorCommand) -> None:
    if str(cmd.audit_context.reason or "").strip():
        return
    raise _bff_error(
        400,
        ErrorCode.VALIDATION_FAILED,
        "audit_context.reason is required",
        "audit_context.reason must be a non-empty string",
    )
def _resolve_final_idempotency_key(
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str],
) -> str:
    """Prefer Idempotency-Key (RFC); accept X-Idempotency-Key as a compatibility alias."""
    canonical = str(idempotency_key or "").strip()
    if canonical:
        return canonical
    alias = str(x_idempotency_key or "").strip()
    if alias:
        return alias
    raise _bff_error(
        400,
        ErrorCode.VALIDATION_FAILED,
        "Idempotency-Key is required for operator commands",
        (
            "Final contract routes require a non-empty Idempotency-Key header; "
            "X-Idempotency-Key is accepted as a temporary compatibility alias"
        ),
        precondition_failed="idempotency_key",
        suggestion="Retry with Idempotency-Key set to a stable client retry key",
    )
def _reject_body_idempotency_key(payload: Dict[str, Any]) -> None:
    """Reject final-contract payloads that carry idempotencyKey in the body."""
    body_key = "idempotencyKey" if "idempotencyKey" in payload else "idempotency_key" if "idempotency_key" in payload else None
    if body_key is not None:
        raise _bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            f"{body_key} must not appear in the request body",
            (
                "Final contract routes require idempotency via the Idempotency-Key header, "
                "not the request body"
            ),
            precondition_failed="body_idempotency_key",
            suggestion=f"Remove {body_key} from the body and set the Idempotency-Key header",
        )
_JOURNAL_WRITE_ROLES = {"operator", "reviewer", "approver", "admin"}
def _stable_json_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
def _require_journal_write_role(identity: OperatorIdentity) -> None:
    if _JOURNAL_WRITE_ROLES.intersection(identity.roles):
        return
    raise _bff_error(
        403,
        ErrorCode.FORBIDDEN,
        "Agora journal patch requires operator-level role",
        "Operator does not hold a role allowed to patch journal entries",
        precondition_failed="role_check",
        suggestion="Escalate to an operator, reviewer, approver, or admin",
    )
_CONFIRM_TOKEN_FIELDS = (
    "confirmToken",
    "confirm_token",
    "confirmationToken",
    "confirmation_token",
)
_APPROVAL_EVIDENCE_FIELDS = (
    "approvalId",
    "approval_id",
    "approvalDecisionId",
    "approval_decision_id",
)
_TWO_MAN_EVIDENCE_FIELDS = (
    "twoManSignatureId",
    "two_man_signature_id",
    "twoManApprovalId",
    "two_man_approval_id",
    "secondOperatorId",
    "second_operator_id",
    "secondOperatorSignature",
    "second_operator_signature",
)
def _precondition_value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True
def _precondition_value(
    payload: Dict[str, Any],
    params: Dict[str, Any],
    aliases: tuple[str, ...],
    *extra_values: Any,
) -> Optional[str]:
    for value in extra_values:
        if _precondition_value_present(value):
            return str(value).strip()
    for source in (payload, params):
        for alias in aliases:
            if alias in source and _precondition_value_present(source.get(alias)):
                return str(source.get(alias)).strip()
    return None
def _binding_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())
def _dict_first_present(mapping: Dict[str, Any], aliases: tuple[str, ...]) -> Optional[Any]:
    for alias in aliases:
        if alias in mapping and _precondition_value_present(mapping.get(alias)):
            return mapping.get(alias)
    return None
def _record_audit(record: Dict[str, Any]) -> Dict[str, Any]:
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    return audit
def _record_params(record: Dict[str, Any]) -> Dict[str, Any]:
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    return params
def _record_actor_id(record: Dict[str, Any]) -> Optional[str]:
    audit = _record_audit(record)
    for key in ("operator_id", "actor", "actor_id", "confirmed_by"):
        value = str(audit.get(key) or "").strip()
        if value:
            return value
    foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
    trace = foundation.get("trace_context") if isinstance(foundation.get("trace_context"), dict) else {}
    actor_ref = trace.get("actor_ref") if isinstance(trace.get("actor_ref"), dict) else {}
    value = str(actor_ref.get("actor_id") or "").strip()
    return value or None
_COMMAND_BINDING_FIELDS = (
    "command",
    "command_type",
    "commandType",
    "action_id",
    "actionId",
)
_TARGET_TYPE_BINDING_FIELDS = (
    "target_type",
    "targetType",
    "entity_type",
    "entityType",
    "object_type",
    "objectType",
)
_TARGET_ID_BINDING_FIELDS = (
    "target_id",
    "targetId",
    "entity_id",
    "entityId",
    "object_id",
    "objectId",
    "runtime_id",
    "runtimeId",
    "intervention_id",
    "interventionId",
)
_CALLER_BINDING_FIELDS = (
    "operator_id",
    "operatorId",
    "caller_operator_id",
    "callerOperatorId",
    "issued_for_operator_id",
    "issuedForOperatorId",
    "issued_for",
    "issuedFor",
    "actor_id",
    "actorId",
)
def _binding_sources(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    params = _record_params(record)
    audit = _record_audit(record)
    sources: List[Dict[str, Any]] = [params, audit]
    for source in (params, audit):
        target = source.get("target")
        if isinstance(target, dict):
            sources.append(target)
        preconditions = source.get("preconditions") or source.get("precondition_evidence")
        if isinstance(preconditions, dict):
            sources.append(preconditions)
    foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
    command_envelope = foundation.get("command_envelope") if isinstance(foundation.get("command_envelope"), dict) else {}
    payload = command_envelope.get("payload") if isinstance(command_envelope.get("payload"), dict) else {}
    if payload:
        sources.append(payload)
        target = payload.get("target")
        if isinstance(target, dict):
            sources.append(target)
    return sources
def _binding_has_command(record: Dict[str, Any]) -> bool:
    return any(_dict_first_present(source, _COMMAND_BINDING_FIELDS) is not None for source in _binding_sources(record))
def _binding_command_matches(record: Dict[str, Any], cmd: OperatorCommand) -> bool:
    values = [
        str(_dict_first_present(source, _COMMAND_BINDING_FIELDS) or "").strip()
        for source in _binding_sources(record)
    ]
    values = [value for value in values if value]
    if not values:
        return False
    expected = _binding_token(cmd.command.value)
    return any(_binding_token(value) == expected for value in values)
def _binding_target_values(record: Dict[str, Any]) -> tuple[List[str], List[str]]:
    target_types: List[str] = []
    target_ids: List[str] = []
    for source in _binding_sources(record):
        target = source.get("target") if isinstance(source.get("target"), dict) else None
        if target is not None:
            target_type = str(target.get("type") or "").strip()
            target_id = str(target.get("id") or "").strip()
            if target_type:
                target_types.append(target_type)
            if target_id:
                target_ids.append(target_id)
        target_type = _dict_first_present(source, _TARGET_TYPE_BINDING_FIELDS)
        target_id = _dict_first_present(source, _TARGET_ID_BINDING_FIELDS)
        if target_type is not None:
            target_types.append(str(target_type).strip())
        if target_id is not None:
            target_ids.append(str(target_id).strip())
    return [value for value in target_types if value], [value for value in target_ids if value]
def _binding_has_target(record: Dict[str, Any]) -> bool:
    target_types, target_ids = _binding_target_values(record)
    return bool(target_types or target_ids)
def _binding_target_matches(record: Dict[str, Any], cmd: OperatorCommand) -> bool:
    target_types, target_ids = _binding_target_values(record)
    if not target_types and not target_ids:
        return False
    type_ok = not target_types or any(
        _binding_token(value) == _binding_token(cmd.target.type.value)
        for value in target_types
    )
    id_ok = not target_ids or any(str(value) == cmd.target.id for value in target_ids)
    return type_ok and id_ok
def _record_bound_to_command_and_target(record: Dict[str, Any], cmd: OperatorCommand) -> bool:
    return (
        _binding_has_command(record)
        and _binding_has_target(record)
        and _binding_command_matches(record, cmd)
        and _binding_target_matches(record, cmd)
    )
def _record_bound_to_caller(record: Dict[str, Any], identity: OperatorIdentity) -> bool:
    bound_values: List[str] = []
    for source in _binding_sources(record):
        value = _dict_first_present(source, _CALLER_BINDING_FIELDS)
        if value is not None:
            bound_values.append(str(value).strip())
    if bound_values:
        return any(value == identity.operator_id for value in bound_values)
    return _record_actor_id(record) == identity.operator_id
def _approval_decision_consumed(decision: Dict[str, Any]) -> bool:
    state = _binding_token(
        decision.get("consumed_state")
        or decision.get("state")
        or decision.get("decision_state")
        or ""
    )
    return bool(
        decision.get("consumed")
        or decision.get("consumed_at")
        or state in {"consumed", "used", "redeemed", "superseded", "revoked"}
    )
def _approval_decision_approved(decision: Dict[str, Any]) -> bool:
    values = {
        _binding_token(decision.get(field))
        for field in ("outcome", "decision", "state", "decision_state", "status")
        if decision.get(field) not in (None, "")
    }
    return bool(values.intersection({"approve", "approved", "accepted"}))
_REBALANCE_EVIDENCE_PRODUCER = "bff.rebalance-evidence.v1"
_V5_TWO_MAN_EVIDENCE_PRODUCER = "bff.v5-two-man-evidence.v1"
_SERVER_MANAGED_REBALANCE_EVIDENCE_TYPES = {
    CommandType.REBALANCE_APPROVAL,
    CommandType.REBALANCE_TWO_MAN_SIGN,
}
def _trusted_rebalance_evidence_record(
    record: Dict[str, Any],
    *,
    command_type: CommandType,
) -> bool:
    foundation = (
        record.get("foundation")
        if isinstance(record.get("foundation"), dict)
        else {}
    )
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    return bool(
        record.get("type") == command_type.value
        and record.get("status") == CommandStatus.EXECUTED.value
        and foundation.get("trusted_evidence_producer")
        == _REBALANCE_EVIDENCE_PRODUCER
        and audit.get("trusted_evidence_producer")
        == _REBALANCE_EVIDENCE_PRODUCER
    )
def _trusted_v5_two_man_evidence_record(record: Dict[str, Any]) -> bool:
    foundation = (
        record.get("foundation")
        if isinstance(record.get("foundation"), dict)
        else {}
    )
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    return bool(
        record.get("type") == CommandType.V5_INTERVENTION_ACTION.value
        and record.get("status") == CommandStatus.EXECUTED.value
        and foundation.get("trusted_evidence_producer")
        == _V5_TWO_MAN_EVIDENCE_PRODUCER
        and audit.get("trusted_evidence_producer")
        == _V5_TWO_MAN_EVIDENCE_PRODUCER
    )
def _reject_server_managed_rebalance_evidence_command(cmd: OperatorCommand) -> None:
    if cmd.command not in _SERVER_MANAGED_REBALANCE_EVIDENCE_TYPES:
        return
    raise _bff_error(
        403,
        ErrorCode.FORBIDDEN,
        "Rebalance evidence commands are server-managed",
        (
            f"{cmd.command.value} can only be produced by the dedicated "
            "authenticated rebalance evidence routes"
        ),
        precondition_failed="trusted_evidence_producer",
        suggestion=(
            "Use POST /bff/rebalances/{id}/approve or "
            "POST /bff/rebalances/{id}/two-man-sign"
        ),
    )
def _rebalance_approval_decision_record(decision_id: str) -> Optional[Dict[str, Any]]:
    for record in reversed(command_store._get_all_commands()):
        if not _trusted_rebalance_evidence_record(
            record,
            command_type=CommandType.REBALANCE_APPROVAL,
        ):
            continue
        params = _record_params(record)
        candidate = str(
            params.get("approval_decision_id")
            or params.get("decision_id")
            or ""
        ).strip()
        if candidate == decision_id:
            return dict(params)
    return None
def _approval_decision_applies_to_command(decision: Dict[str, Any], decision_id: str, cmd: OperatorCommand) -> bool:
    synthetic_record = {"params": decision}
    has_command = _binding_has_command(synthetic_record)
    has_target = _binding_has_target(synthetic_record)
    if has_command and not _binding_command_matches(synthetic_record, cmd):
        return False
    if has_target:
        return _binding_target_matches(synthetic_record, cmd)
    if cmd.target.type == ObjectType.APPROVAL_DECISION:
        return decision_id == cmd.target.id
    return False
_TWO_MAN_SIGNATURE_ID_FIELDS = (
    "twoManSignatureId",
    "two_man_signature_id",
    "twoManApprovalId",
    "two_man_approval_id",
    "signature_id",
    "signatureId",
    "id",
)
_TWO_MAN_SIGNER_LIST_FIELDS = (
    "signer_operator_ids",
    "signerOperatorIds",
    "operator_ids",
    "operatorIds",
)
_TWO_MAN_SIGNER_FIELDS = (
    "first_operator_id",
    "firstOperatorId",
    "primary_operator_id",
    "primaryOperatorId",
    "second_operator_id",
    "secondOperatorId",
    "secondOperatorSignature",
    "second_operator_signature",
    "signed_by",
    "signedBy",
    "confirmed_by",
    "confirmedBy",
)
def _two_man_signature_record(
    signature_id: str,
    *,
    cmd: Optional[OperatorCommand] = None,
) -> Optional[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for record in command_store._get_all_commands():
        if cmd is None:
            continue
        if cmd.command == CommandType.APPROVED_APPLY:
            trusted = _trusted_rebalance_evidence_record(
                record,
                command_type=CommandType.REBALANCE_TWO_MAN_SIGN,
            )
        else:
            trusted = _trusted_v5_two_man_evidence_record(record)
        if not trusted:
            continue
        params = _record_params(record)
        audit = _record_audit(record)
        target = record.get("target") if isinstance(record.get("target"), dict) else {}
        candidate_values = [
            _dict_first_present(params, _TWO_MAN_SIGNATURE_ID_FIELDS),
            _dict_first_present(audit, _TWO_MAN_SIGNATURE_ID_FIELDS),
            target.get("id"),
        ]
        if any(str(value or "").strip() == signature_id for value in candidate_values):
            matches.append(record)
    if not matches:
        return None
    if cmd is not None:
        bound_matches = [
            record
            for record in matches
            if _record_bound_to_command_and_target(record, cmd)
        ]
        # Prefer exact binding matches when present. If the signature exists
        # only for another target, return that trusted record so the caller can
        # distinguish BINDING_MISMATCH from NOT_FOUND.
        if bound_matches:
            matches = bound_matches
    # Concurrent signers may each append a valid single-signer record after
    # reading the same prior state.  Project their durable evidence as one
    # signature aggregate instead of trusting only the latest append.
    combined = dict(matches[-1])
    params = dict(_record_params(combined))
    signers: List[str] = []
    for record in matches:
        signers.extend(sorted(_two_man_signers(record)))
    unique_signers = list(dict.fromkeys(value for value in signers if value))
    params.update(
        {
            "signer_operator_ids": unique_signers,
            "first_operator_id": unique_signers[0] if unique_signers else None,
            "second_operator_id": unique_signers[1] if len(unique_signers) > 1 else None,
            "complete": len(unique_signers) >= 2,
        }
    )
    combined["params"] = params
    return combined
def _two_man_signers(record: Dict[str, Any]) -> set[str]:
    params = _record_params(record)
    audit = _record_audit(record)
    signers: set[str] = set()
    for source in (params, audit):
        for field in _TWO_MAN_SIGNER_LIST_FIELDS:
            raw = source.get(field)
            if isinstance(raw, list):
                signers.update(str(value).strip() for value in raw if str(value or "").strip())
        for field in _TWO_MAN_SIGNER_FIELDS:
            value = str(source.get(field) or "").strip()
            if value:
                signers.add(value)
    actor = _record_actor_id(record)
    if actor:
        signers.add(actor)
    return signers
def _final_precondition_details(
    *,
    cmd: OperatorCommand,
    kind: str,
) -> Dict[str, Any]:
    return {
        "actionId": cmd.command.value,
        "entityType": cmd.target.type.value,
        "entityId": cmd.target.id,
        "kind": kind,
    }
def _final_precondition_error(
    *,
    cmd: OperatorCommand,
    status_code: int,
    code: ErrorCode,
    message: str,
    reason: str,
    kind: str,
    correlation_id: Optional[str],
    suggestion: str,
    details_extra: Optional[Dict[str, Any]] = None,
) -> HTTPException:
    return _bff_error(
        status_code=status_code,
        code=code,
        message=message,
        reason=reason,
        precondition_failed=kind,
        suggestion=suggestion,
        details_extra={
            **_final_precondition_details(cmd=cmd, kind=kind),
            **(details_extra or {}),
        },
        correlation_id=correlation_id,
    )
def _require_two_man_signature_evidence(
    *,
    cmd: OperatorCommand,
    signature_id: Optional[str],
    correlation_id: Optional[str],
    missing_suggestion: str = "Attach a second authorized operator signature before retrying",
) -> str:
    if not signature_id:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=409,
            code=ErrorCode.TWO_MAN_SIGNATURE_REQUIRED,
            message="Two-man authorization is required before this action can be accepted",
            reason="TWO_MAN_SIGNATURE_MISSING",
            kind="two_man",
            correlation_id=correlation_id,
            suggestion=missing_suggestion,
        )
    signature_record = _two_man_signature_record(signature_id, cmd=cmd)
    if signature_record is None:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=409,
            code=ErrorCode.TWO_MAN_SIGNATURE_REQUIRED,
            message="Two-man signature does not exist",
            reason="TWO_MAN_SIGNATURE_NOT_FOUND",
            kind="two_man",
            correlation_id=correlation_id,
            suggestion="Attach a two-man signature record created for this command and target",
            details_extra={"twoManSignatureId": signature_id},
        )
    signers = _two_man_signers(signature_record)
    if len(signers) < 2:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=409,
            code=ErrorCode.TWO_MAN_SIGNATURE_REQUIRED,
            message="Two-man signature must contain two distinct operators",
            reason="TWO_MAN_SIGNATURE_SIGNER_MISMATCH",
            kind="two_man",
            correlation_id=correlation_id,
            suggestion="Collect a signature record with two distinct operator ids",
            details_extra={"twoManSignatureId": signature_id},
        )
    if not _record_bound_to_command_and_target(signature_record, cmd):
        raise _final_precondition_error(
            cmd=cmd,
            status_code=409,
            code=ErrorCode.TWO_MAN_SIGNATURE_REQUIRED,
            message="Two-man signature is not bound to this command target",
            reason="TWO_MAN_SIGNATURE_BINDING_MISMATCH",
            kind="two_man",
            correlation_id=correlation_id,
            suggestion="Attach a two-man signature for the exact command and target being submitted",
            details_extra={"twoManSignatureId": signature_id},
        )
    return signature_id
def _require_final_command_confirm_token(
    *,
    cmd: OperatorCommand,
    payload: Dict[str, Any],
    confirm_token: Optional[str],
    identity: OperatorIdentity,
    correlation_id: Optional[str],
) -> Optional[str]:
    entry = get_catalog_entry(cmd.command.value)
    if entry is None or not getattr(entry, "requires_confirm_token", False):
        return None

    params = dict(cmd.params)
    token_id = _precondition_value(payload, params, _CONFIRM_TOKEN_FIELDS, confirm_token)
    if not token_id:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=428,
            code=ErrorCode.CONFIRMATION_REQUIRED,
            message="Confirmation token is required before this action can be accepted",
            reason="CONFIRM_TOKEN_MISSING",
            kind="confirm_token",
            correlation_id=correlation_id,
            suggestion="Retry with X-Confirm-Token or confirmToken after the operator confirmation step",
        )
    token_records = _confirm_token_records(token_id)
    create_record = next(
        (
            record
            for record in reversed(token_records)
            if record.get("type") == CommandType.CONFIRM_TOKEN_CREATE.value
        ),
        None,
    )
    token_state = _confirm_token_lifecycle_payload(token_id)
    if create_record is None or token_state.get("status") != "created":
        raise _final_precondition_error(
            cmd=cmd,
            status_code=428,
            code=ErrorCode.CONFIRMATION_REQUIRED,
            message="Confirmation token is not valid for this command",
            reason="CONFIRM_TOKEN_INVALID",
            kind="confirm_token",
            correlation_id=correlation_id,
            suggestion="Issue a fresh confirm token bound to this command, target, and operator",
            details_extra={"confirmToken": token_id, "tokenStatus": token_state.get("status")},
        )
    if not _record_bound_to_command_and_target(create_record, cmd):
        raise _final_precondition_error(
            cmd=cmd,
            status_code=428,
            code=ErrorCode.CONFIRMATION_REQUIRED,
            message="Confirmation token is not bound to this command target",
            reason="CONFIRM_TOKEN_BINDING_MISMATCH",
            kind="confirm_token",
            correlation_id=correlation_id,
            suggestion="Issue a confirm token for the exact command and target being submitted",
            details_extra={"confirmToken": token_id},
        )
    if not _record_bound_to_caller(create_record, identity):
        raise _final_precondition_error(
            cmd=cmd,
            status_code=428,
            code=ErrorCode.CONFIRMATION_REQUIRED,
            message="Confirmation token is not bound to this operator",
            reason="CONFIRM_TOKEN_CALLER_MISMATCH",
            kind="confirm_token",
            correlation_id=correlation_id,
            suggestion="Use a confirm token issued for the same authenticated operator",
            details_extra={"confirmToken": token_id},
        )
    return token_id
def _require_final_command_preconditions(
    *,
    cmd: OperatorCommand,
    payload: Dict[str, Any],
    confirm_token: Optional[str],
    identity: OperatorIdentity,
    correlation_id: Optional[str],
) -> Dict[str, str]:
    entry = get_catalog_entry(cmd.command.value)
    if entry is None:
        return {}

    evidence: Dict[str, str] = {}
    token_id = _require_final_command_confirm_token(
        cmd=cmd,
        payload=payload,
        confirm_token=confirm_token,
        identity=identity,
        correlation_id=correlation_id,
    )
    if token_id:
        evidence["confirm_token_id"] = token_id

    params = dict(cmd.params)
    paper_simulation_authority = _ppl_alloc_009_paper_rebalance_authority(cmd)
    if paper_simulation_authority and not identity.mfa_verified:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=403,
            code=ErrorCode.FORBIDDEN,
            message="Paper allocation apply requires MFA",
            reason="PAPER_SIMULATION_MFA_REQUIRED",
            kind="mfa",
            correlation_id=correlation_id,
            suggestion="Retry with the strict dev operator identity and verified MFA",
        )

    approval_decision: Optional[Dict[str, Any]] = None
    if getattr(entry, "requires_approval", False):
        approval_decision_id = _precondition_value(payload, params, _APPROVAL_EVIDENCE_FIELDS)
        if not approval_decision_id:
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.HUMAN_GATE_PENDING,
                message="Approval evidence is required before this action can be accepted",
                reason="APPROVAL_EVIDENCE_MISSING",
                kind="approval",
                correlation_id=correlation_id,
                suggestion="Attach approvalId from the governance approval flow before retrying",
            )
        approval_decision = read_store.get_approval_decision(approval_decision_id)
        if approval_decision is None and cmd.command == CommandType.APPROVED_APPLY:
            approval_decision = _rebalance_approval_decision_record(
                approval_decision_id
            )
        if approval_decision is None:
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.HUMAN_GATE_PENDING,
                message="Approval decision does not exist",
                reason="APPROVAL_DECISION_NOT_FOUND",
                kind="approval",
                correlation_id=correlation_id,
                suggestion="Attach an approvalDecisionId that exists in the governance approval store",
                details_extra={"approvalDecisionId": approval_decision_id},
            )
        if _approval_decision_consumed(approval_decision):
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.HUMAN_GATE_PENDING,
                message="Approval decision has already been consumed",
                reason="APPROVAL_DECISION_CONSUMED",
                kind="approval",
                correlation_id=correlation_id,
                suggestion="Request a fresh approval decision before retrying this command",
                details_extra={"approvalDecisionId": approval_decision_id},
            )
        if not _approval_decision_approved(approval_decision):
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.HUMAN_GATE_PENDING,
                message="Approval decision is not approved",
                reason="APPROVAL_DECISION_NOT_APPROVED",
                kind="approval",
                correlation_id=correlation_id,
                suggestion="Obtain an approved decision for this exact command and target",
                details_extra={"approvalDecisionId": approval_decision_id},
            )
        if not _approval_decision_applies_to_command(approval_decision, approval_decision_id, cmd):
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.HUMAN_GATE_PENDING,
                message="Approval decision is not bound to this command target",
                reason="APPROVAL_DECISION_BINDING_MISMATCH",
                kind="approval",
                correlation_id=correlation_id,
                suggestion="Attach approval evidence for the exact command and target being submitted",
                details_extra={"approvalDecisionId": approval_decision_id},
            )
        evidence["approval_decision_id"] = approval_decision_id
        if paper_simulation_authority:
            approval_actor = str(
                approval_decision.get("decided_by")
                or approval_decision.get("actor_id")
                or approval_decision.get("operator_id")
                or ""
            ).strip()
            if not approval_actor or approval_actor == identity.operator_id:
                raise _final_precondition_error(
                    cmd=cmd,
                    status_code=409,
                    code=ErrorCode.HUMAN_GATE_PENDING,
                    message="Paper allocation approval and apply must be distinct",
                    reason="PAPER_SIMULATION_APPROVAL_APPLY_NOT_DISTINCT",
                    kind="approval",
                    correlation_id=correlation_id,
                    suggestion=(
                        "Use an approver identity distinct from the authenticated "
                        "operator applying the paper allocation"
                    ),
                )
            evidence["paper_simulation_authority"] = (
                _PPL_ALLOC_009_PAPER_AUTHORITY_MODE
            )

    if cmd.command in _HUMAN_GATE_DECISIONS_BY_COMMAND:
        evidence.update(
            _require_human_gate_security_preconditions(
                cmd=cmd,
                payload=payload,
                identity=identity,
                correlation_id=correlation_id,
            )
        )
        return evidence

    if getattr(entry, "requires_two_man", False) and not paper_simulation_authority:
        signature_id = _precondition_value(payload, params, _TWO_MAN_EVIDENCE_FIELDS)
        evidence["two_man_signature_id"] = _require_two_man_signature_evidence(
            cmd=cmd,
            signature_id=signature_id,
            correlation_id=correlation_id,
        )

    return evidence
_FINAL_COMMAND_TARGET_TYPES: Dict[CommandType, ObjectType] = {
    CommandType.APPROVED_APPLY: ObjectType.REBALANCE,
    CommandType.HUMAN_GATE_APPROVE: ObjectType.HUMAN_GATE_ITEM,
    CommandType.HUMAN_GATE_REJECT: ObjectType.HUMAN_GATE_ITEM,
    CommandType.HUMAN_GATE_REQUEST_MORE_EVIDENCE: ObjectType.HUMAN_GATE_ITEM,
    CommandType.HUMAN_GATE_REVOKE: ObjectType.HUMAN_GATE_ITEM,
    CommandType.HUMAN_GATE_EXTEND_TTL: ObjectType.HUMAN_GATE_ITEM,
    CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT: ObjectType.RANKING,
}
def _validate_final_command_target_type(cmd: OperatorCommand) -> None:
    expected = _FINAL_COMMAND_TARGET_TYPES.get(cmd.command)
    if expected is None or cmd.target.type == expected:
        return
    raise _bff_error(
        422,
        ErrorCode.VALIDATION_FAILED,
        "Invalid command target type",
        f"{cmd.command.value} must target {expected.value}, not {cmd.target.type.value}",
        precondition_failed="target.type",
        suggestion=f"Use target.type={expected.value} for {cmd.command.value}",
    )
def _validate_capital_authority_target_binding(cmd: OperatorCommand) -> None:
    if cmd.command == CommandType.APPROVED_APPLY:
        aliases = ("rebalance_id", "rebalanceId")
        label = "rebalance"
    elif cmd.command == CommandType.EMERGENCY_CONTAINMENT:
        if cmd.target.type != ObjectType.PERSONA:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "EmergencyContainment must target a Persona",
                "Capital containment authority mutates the Persona identified by command target.id",
                precondition_failed="capital_target_type",
            )
        aliases = ("persona_id", "personaId")
        label = "persona"
    else:
        return
    supplied = {
        str(cmd.params.get(alias) or "").strip()
        for alias in aliases
        if str(cmd.params.get(alias) or "").strip()
    }
    if supplied and supplied != {cmd.target.id}:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{label}_id must match command target.id",
            (
                f"Capital owner command targets {cmd.target.id!r}, but params supplied "
                f"{sorted(supplied)!r}"
            ),
            precondition_failed="capital_target_id_mismatch",
        )
def _canonicalize_validated_precondition_evidence(
    stored_params: Dict[str, Any],
    evidence: Dict[str, str],
) -> None:
    confirm_token_id = evidence.get("confirm_token_id")
    if confirm_token_id:
        for alias in (*_CONFIRM_TOKEN_FIELDS, "confirm_token_id"):
            stored_params.pop(alias, None)
        stored_params["confirm_token_id"] = confirm_token_id

    approval_decision_id = evidence.get("approval_decision_id")
    if approval_decision_id:
        for alias in (*_APPROVAL_EVIDENCE_FIELDS, "approval_ref"):
            stored_params.pop(alias, None)
        stored_params["approval_decision_id"] = approval_decision_id
        stored_params["approval_ref"] = approval_decision_id

    signature_id = evidence.get("two_man_signature_id")
    if signature_id:
        for alias in _TWO_MAN_EVIDENCE_FIELDS:
            stored_params.pop(alias, None)
        stored_params["two_man_signature_id"] = signature_id
def _human_gate_source_type(item_id: str) -> Optional[str]:
    prefix = item_id.split(":", 1)[0].strip().lower() if ":" in item_id else ""
    if prefix in {"approval", "intervention"}:
        return prefix
    return None
def _human_gate_max_ttl_seconds() -> int:
    raw = os.getenv("PANTHEON_HUMAN_GATE_MAX_TTL_SECONDS", str(_HUMAN_GATE_DEFAULT_MAX_TTL_SECONDS)).strip()
    try:
        configured = int(raw)
    except (TypeError, ValueError):
        configured = _HUMAN_GATE_DEFAULT_MAX_TTL_SECONDS
    return max(1, configured)
def _human_gate_clean_text(value: Any) -> str:
    return str(value or "").strip()
def _human_gate_source_id_from_params(params: Dict[str, Any], item_id: str, source_type: Optional[str]) -> Optional[str]:
    explicit_source_id = _dict_first_present(params, _HUMAN_GATE_SOURCE_ID_FIELDS)
    if explicit_source_id is not None:
        return _human_gate_clean_text(explicit_source_id) or None
    if ":" in item_id:
        prefix, suffix = item_id.split(":", 1)
        if not source_type or prefix.strip().lower() == source_type:
            return suffix.strip() or None
    return None
def _human_gate_find_approval_record(source_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not source_id:
        return None
    record = read_store.get_approval_decision(source_id)
    if record is not None:
        return dict(record)
    local_data = getattr(read_store, "_data", {})
    if isinstance(local_data, dict):
        local_approvals = local_data.get("approval_decisions")
        if isinstance(local_approvals, dict) and isinstance(local_approvals.get(source_id), dict):
            return dict(local_approvals[source_id])
    for item in read_store.list_approval_queue_items() or []:
        candidate = _human_gate_clean_text(
            item.get("decision_id")
            or item.get("id")
            or item.get("approval_decision_id")
        )
        if candidate == source_id:
            return dict(item)
    return None
def _human_gate_find_intervention_record(source_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not source_id:
        return None
    getter = getattr(read_store, "get_v5_intervention", None)
    if callable(getter):
        record = getter(source_id)
        if record is not None:
            return dict(record)
    for item in _v5_intervention_records():
        candidate = _human_gate_clean_text(item.get("intervention_id") or item.get("id"))
        if candidate == source_id:
            return dict(item)
    return None
def _human_gate_source_record(params: Dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    item_id = _human_gate_clean_text(params.get("human_gate_item_id") or params.get("itemId") or params.get("item_id"))
    source_type = _human_gate_clean_text(params.get("source_type") or params.get("sourceType")).lower() or None
    if source_type not in {"approval", "intervention", None}:
        source_type = None
    if not source_type:
        source_type = _human_gate_source_type(item_id)
    source_id = _human_gate_source_id_from_params(params, item_id, source_type)
    if source_type == "approval":
        return source_type, source_id, _human_gate_find_approval_record(source_id)
    if source_type == "intervention":
        return source_type, source_id, _human_gate_find_intervention_record(source_id)
    return source_type, source_id, None
def _human_gate_actor_id(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key in ("operator_id", "operatorId", "actor_id", "actorId", "id", "user_id", "userId"):
            clean = _human_gate_clean_text(value.get(key))
            if clean:
                return clean
        return None
    clean = _human_gate_clean_text(value)
    return clean or None
def _human_gate_requester_ids(record: Optional[Dict[str, Any]]) -> set[str]:
    if not isinstance(record, dict):
        return set()
    requester_ids: set[str] = set()
    for field in _HUMAN_GATE_REQUESTER_FIELDS:
        actor_id = _human_gate_actor_id(record.get(field))
        if actor_id:
            requester_ids.add(actor_id)
    context = record.get("decision_context") if isinstance(record.get("decision_context"), dict) else {}
    for field in _HUMAN_GATE_REQUESTER_FIELDS:
        actor_id = _human_gate_actor_id(context.get(field))
        if actor_id:
            requester_ids.add(actor_id)
    return requester_ids
def _human_gate_record_risk_level(params: Dict[str, Any], record: Optional[Dict[str, Any]]) -> Optional[str]:
    sources: List[Dict[str, Any]] = [params]
    if isinstance(record, dict):
        sources.append(record)
        for nested_key in ("governance", "decision_context", "remediation_context", "metadata"):
            nested = record.get(nested_key)
            if isinstance(nested, dict):
                sources.append(nested)
    for source in sources:
        for field in _HUMAN_GATE_RISK_FIELDS:
            risk = _human_gate_clean_text(source.get(field)).lower()
            if risk:
                return _human_inbox_priority(risk, fallback=risk)
    return None
def _human_gate_requires_two_man(params: Dict[str, Any], record: Optional[Dict[str, Any]]) -> bool:
    for field in ("requires_two_man", "requiresTwoMan", "requires_second_operator", "requiresSecondOperator"):
        value = params.get(field)
        if isinstance(value, bool) and value:
            return True
        if _human_gate_clean_text(value).lower() in {"1", "true", "yes"}:
            return True
    risk_level = _human_gate_record_risk_level(params, record)
    if risk_level in _HUMAN_GATE_HIGH_RISK_LEVELS:
        return True
    live_capital = params.get("liveCapitalMutation", params.get("live_capital_mutation"))
    return isinstance(live_capital, bool) and live_capital
def _human_gate_downstream_effect_executed(record: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(record, dict):
        return False
    for field in _HUMAN_GATE_DOWNSTREAM_EXECUTED_FIELDS:
        status = _human_gate_clean_text(record.get(field)).lower()
        if status in _HUMAN_GATE_DOWNSTREAM_EXECUTED_STATES:
            return True
    for field in _HUMAN_GATE_DOWNSTREAM_EXECUTED_AT_FIELDS:
        if _human_gate_clean_text(record.get(field)):
            return True
    downstream = record.get("downstream") if isinstance(record.get("downstream"), dict) else {}
    for field in _HUMAN_GATE_DOWNSTREAM_EXECUTED_FIELDS:
        status = _human_gate_clean_text(downstream.get(field)).lower()
        if status in _HUMAN_GATE_DOWNSTREAM_EXECUTED_STATES:
            return True
    for field in _HUMAN_GATE_DOWNSTREAM_EXECUTED_AT_FIELDS:
        if _human_gate_clean_text(downstream.get(field)):
            return True
    return False
def _require_human_gate_security_preconditions(
    *,
    cmd: OperatorCommand,
    payload: Dict[str, Any],
    identity: OperatorIdentity,
    correlation_id: Optional[str],
) -> Dict[str, str]:
    params = cmd.params
    decision = _human_gate_clean_text(params.get("decision")).lower()
    source_type, source_id, source_record = _human_gate_source_record(params)

    if decision in _HUMAN_GATE_SELF_APPROVAL_DECISIONS:
        requester_ids = _human_gate_requester_ids(source_record)
        if identity.operator_id in requester_ids:
            raise _final_precondition_error(
                cmd=cmd,
                status_code=403,
                code=ErrorCode.FORBIDDEN,
                message="HumanGate decisions cannot be approved by their requester",
                reason="HUMAN_GATE_SELF_APPROVAL_FORBIDDEN",
                kind="anti_self_approval",
                correlation_id=correlation_id,
                suggestion="Route this HumanGate decision to a different approver",
                details_extra={
                    "sourceType": source_type,
                    "sourceRecordId": source_id,
                    "requesterId": identity.operator_id,
                },
            )

    if decision == "revoke":
        if source_record is None:
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.HUMAN_GATE_PENDING,
                message="HumanGateRevoke requires a readable source record",
                reason="HUMAN_GATE_SOURCE_NOT_FOUND",
                kind="human_gate_revoke",
                correlation_id=correlation_id,
                suggestion="Refresh the Human Inbox source record before retrying revoke",
                details_extra={"sourceType": source_type, "sourceRecordId": source_id},
            )
        if _human_gate_downstream_effect_executed(source_record):
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.RESOURCE_CONFLICT,
                message="HumanGateRevoke cannot revoke an already executed downstream effect",
                reason="HUMAN_GATE_REVOKE_DOWNSTREAM_EXECUTED",
                kind="human_gate_revoke",
                correlation_id=correlation_id,
                suggestion="Submit a compensating action through the downstream authority instead of revoking this HumanGate item",
                details_extra={"sourceType": source_type, "sourceRecordId": source_id},
            )

    evidence: Dict[str, str] = {}
    if decision in _HUMAN_GATE_APPROVER_DECISIONS and _human_gate_requires_two_man(params, source_record):
        signature_id = _precondition_value(payload, params, _TWO_MAN_EVIDENCE_FIELDS)
        evidence["two_man_signature_id"] = _require_two_man_signature_evidence(
            cmd=cmd,
            signature_id=signature_id,
            correlation_id=correlation_id,
            missing_suggestion="Attach a two-man signature for this high-risk HumanGate item before retrying",
        )
        params["two_man_signature_id"] = evidence["two_man_signature_id"]
        params["twoManSignatureId"] = evidence["two_man_signature_id"]

    return evidence
def _normalize_human_gate_command(cmd: OperatorCommand) -> OperatorCommand:
    decision = _HUMAN_GATE_DECISIONS_BY_COMMAND.get(cmd.command)
    if decision is None:
        return cmd

    params = dict(cmd.params or {})
    item_id = str(cmd.target.id or "").strip()
    provided_item_ids = [
        _human_gate_clean_text(params.get(alias))
        for alias in ("human_gate_item_id", "humanGateItemId", "item_id", "itemId")
        if _human_gate_clean_text(params.get(alias))
    ]
    for provided_item_id in provided_item_ids:
        if provided_item_id != item_id:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "HumanGate params target id does not match the command target",
                "HUMAN_GATE_TARGET_MISMATCH",
                precondition_failed="human_gate_item_id",
                suggestion="Use target.id as the authoritative HumanGate item id",
                details_extra={
                    "targetId": item_id,
                    "providedHumanGateItemId": provided_item_id,
                },
            )
    params["human_gate_item_id"] = item_id
    params["humanGateItemId"] = item_id
    params["item_id"] = item_id
    params["itemId"] = item_id
    source_type = str(params.get("source_type") or params.get("sourceType") or "").strip()
    if not source_type:
        source_type = _human_gate_source_type(item_id) or ""
    if source_type:
        params["source_type"] = source_type
        params["sourceType"] = source_type
    params["decision"] = decision
    params["action_id"] = decision
    params["actionId"] = decision
    params.setdefault("audit_event", f"human_gate.{decision}")
    params.setdefault("auditEvent", f"human_gate.{decision}")
    params.setdefault("entity_type", "human_gate_item")
    params.setdefault("entity_id", item_id)
    cmd.params = params
    return cmd
def _normalize_quarterly_recommendation_command(cmd: OperatorCommand) -> OperatorCommand:
    if cmd.command != CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT:
        return cmd

    params = dict(cmd.params or {})
    recommendation_id = str(
        params.get("recommendation_id")
        or params.get("recommendationId")
        or cmd.target.id
        or ""
    ).strip()
    target_recommendation_id = str(cmd.target.id or "").strip()
    if (
        recommendation_id
        and target_recommendation_id
        and recommendation_id != target_recommendation_id
    ):
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "recommendation_id does not match the command target",
            "Use target.id as the authoritative quarterly recommendation id.",
            precondition_failed="recommendation_id",
        )
    if recommendation_id:
        params["recommendation_id"] = recommendation_id
        params["recommendationId"] = recommendation_id

    recommendation_action_id = str(
        params.get("recommendation_action_id")
        or params.get("recommendationActionId")
        or params.get("actionId")
        or params.get("action_id")
        or ""
    ).strip()
    if recommendation_action_id and recommendation_action_id != "submit_recommendation":
        params["recommendation_action_id"] = recommendation_action_id
        params["recommendationActionId"] = recommendation_action_id

    params["action_id"] = "submit_recommendation"
    params["actionId"] = "submit_recommendation"
    params.setdefault("audit_event", "quarterly_ranking.recommendation_submitted")
    params.setdefault("auditEvent", "quarterly_ranking.recommendation_submitted")
    params.setdefault("entity_type", "quarterly_ranking_recommendation")
    params.setdefault("entity_id", recommendation_id or cmd.target.id)
    cmd.params = params
    return cmd
def _normalize_b5_command_payload(cmd: OperatorCommand) -> OperatorCommand:
    return _normalize_quarterly_recommendation_command(
        _normalize_human_gate_command(cmd)
    )
def _normalize_operator_command_payload(payload: Dict[str, Any]) -> OperatorCommand:
    command_type = payload.get("command_type")
    if command_type:
        try:
            if command_type == CommandType.APPROVE_MUTATION.value:
                mutation = ApproveMutationCommandPayload.model_validate(payload)
                note = str(mutation.note or "").strip() or None
                params: Dict[str, Any] = {"decision_id": mutation.decision_id}
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.APPROVE_MUTATION,
                    target=TargetObject(type=ObjectType.EVOLUTION_DECISION, id=mutation.decision_id),
                    action="approve_mutation",
                    params=params,
                    audit_context=AuditContext(reason=note or mutation.command_type),
                )
            if command_type == CommandType.REJECT_MUTATION.value:
                mutation = RejectMutationCommandPayload.model_validate(payload)
                note = str(mutation.note or "").strip() or None
                params = {"decision_id": mutation.decision_id}
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.REJECT_MUTATION,
                    target=TargetObject(type=ObjectType.EVOLUTION_DECISION, id=mutation.decision_id),
                    action="reject_mutation",
                    params=params,
                    audit_context=AuditContext(reason=note or mutation.command_type),
                )
            if command_type == CommandType.REVIEW_MUTATION.value:
                mutation = ReviewMutationCommandPayload.model_validate(payload)
                note = str(mutation.note or "").strip() or None
                params = {
                    "decision_id": mutation.decision_id,
                    "approval_decision_id": mutation.approval_decision_id,
                }
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.REVIEW_MUTATION,
                    target=TargetObject(type=ObjectType.EVOLUTION_DECISION, id=mutation.decision_id),
                    action="review_mutation",
                    params=params,
                    audit_context=AuditContext(reason=note or mutation.command_type),
                )
            if command_type == CommandType.EXECUTE_MUTATION.value:
                mutation = ExecuteMutationCommandPayload.model_validate(payload)
                note = str(mutation.note or "").strip() or None
                params = {
                    "decision_id": mutation.decision_id,
                    "has_active_runtime": mutation.has_active_runtime,
                    "freeze_mode": mutation.freeze_mode,
                    "force_stage_freeze": mutation.force_stage_freeze,
                }
                if mutation.active_binding_id:
                    params["active_binding_id"] = mutation.active_binding_id
                if mutation.rollback_action_type:
                    params["rollback_action_type"] = mutation.rollback_action_type
                if mutation.fallback_artifact_id:
                    params["fallback_artifact_id"] = mutation.fallback_artifact_id
                if mutation.fallback_artifact_version:
                    params["fallback_artifact_version"] = mutation.fallback_artifact_version
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.EXECUTE_MUTATION,
                    target=TargetObject(type=ObjectType.EVOLUTION_DECISION, id=mutation.decision_id),
                    action="execute_mutation",
                    params=params,
                    audit_context=AuditContext(reason=note or mutation.command_type),
                )
            if command_type == CommandType.RECORD_SPONSOR_DECISION.value:
                decision = RecordSponsorDecisionCommandPayload.model_validate(payload)
                note = str(decision.note or "").strip() or None
                params = {
                    "committee_id": decision.committee_id,
                    "sponsor_decision": decision.sponsor_decision,
                    "rationale_ref": decision.rationale_ref,
                }
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.RECORD_SPONSOR_DECISION,
                    target=TargetObject(type=ObjectType.COMMITTEE_BOARD, id=decision.committee_id),
                    action="record_sponsor_decision",
                    params=params,
                    audit_context=AuditContext(reason=note or decision.command_type),
                )
        except ValidationError as exc:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"Invalid {command_type} payload",
                str(exc),
            ) from exc
        raise _bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "Unknown command_type",
            f"Unsupported command_type: {command_type}",
        )

    try:
        return _normalize_b5_command_payload(OperatorCommand.model_validate(payload))
    except ValidationError as exc:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid operator command payload",
            str(exc),
        ) from exc
def _validate_pause_execution(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _PAUSE_EXECUTION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for PauseExecution",
            f"Missing fields: {sorted(missing)}",
        )
    for field in sorted(_PAUSE_EXECUTION_REQUIRED):
        if not isinstance(params.get(field), bool):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"Invalid {field} value",
                f"{field} must be a boolean",
            )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "PauseExecution requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )
def _validate_issue_risk_off(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _RISK_OFF_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for IssueRiskOff",
            f"Missing fields: {sorted(missing)}",
        )
    exposure_pct = params.get("reduce_exposure_pct")
    if not isinstance(exposure_pct, (int, float)) or exposure_pct <= 0 or exposure_pct > 100:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid reduce_exposure_pct value",
            "reduce_exposure_pct must be a number between 1 and 100",
        )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "IssueRiskOff requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )
def _validate_liquidate_all(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    if params:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "LiquidateAll does not accept params",
            "params must be an empty object for LiquidateAll",
        )
    _require_admin_mfa(identity, "LiquidateAll")
def _validate_hard_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    target_artifact_id = str(params.get("target_artifact_id") or "").strip()
    if not target_artifact_id:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for HardRollback",
            "target_artifact_id must be a non-empty string",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "HardRollback requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )
def _validate_issue_safe_mode(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    safe_mode_level = str(params.get("safe_mode_level") or "").strip().lower()
    if safe_mode_level not in _SAFE_MODE_LEVELS:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid safe_mode_level",
            f"safe_mode_level must be one of {sorted(_SAFE_MODE_LEVELS)}",
        )
    _require_admin_mfa(identity, "IssueSafeMode")
def _derive_drawer_execution_params(
    command: CommandType,
    runtime_id: str,
    params: Dict[str, Any],
    *,
    actor_id: Optional[str],
    reason: Optional[str],
    incident_id: Optional[str],
) -> Dict[str, Any]:
    context = _runtime_command_context(runtime_id, incident_id)
    base = {
        "runtime_id": runtime_id,
        "runtime_binding_id": context["runtime_binding_id"],
        "capital_pool_id": context["capital_pool_id"],
        "actor_id": actor_id or "operator-command",
        "reason": reason or "",
        "incident_id": incident_id,
    }

    if command == CommandType.PAUSE_EXECUTION:
        return {
            **base,
            "pause_action": "pause",
            "pause_new_entries": params["pause_new_entries"],
            "cancel_open_orders": params["cancel_open_orders"],
        }

    if command == CommandType.ISSUE_RISK_OFF:
        if not context["capital_pool_id"]:
            raise ValueError(
                f"Runtime {runtime_id} cannot be routed to a capital pool."
            )
        return {
            **base,
            "scope": "pool",
            "scope_id": context["capital_pool_id"],
            "action_override": "risk_off",
            "trigger_reason": "operator_emergency_stop",
            "reduce_exposure_pct": params["reduce_exposure_pct"],
        }

    if command == CommandType.LIQUIDATE_ALL:
        if not context["capital_pool_id"]:
            raise ValueError(
                f"Runtime {runtime_id} cannot be routed to a capital pool."
            )
        return {
            **base,
            "scope": "pool",
            "scope_id": context["capital_pool_id"],
            "action_override": "liquidate",
            "trigger_reason": "operator_emergency_stop",
        }

    if command == CommandType.HARD_ROLLBACK:
        return {
            **base,
            "rollback_target_type": "runtime",
            "target_id": context["runtime_binding_id"],
            "rollback_to_version": params["target_artifact_id"],
            "rollback_action_type": "pause_then_replace",
            "target_artifact_id": params["target_artifact_id"],
        }

    if not context["capital_pool_id"]:
        raise ValueError(
            f"Runtime {runtime_id} cannot be routed to a capital pool."
        )
    return {
        **base,
        "safe_mode_level": params["safe_mode_level"],
        "target_state": "guarded",
    }
def _stored_command_params(
    cmd: OperatorCommand,
    identity: OperatorIdentity,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if cmd.command in _DRAWER_RUNTIME_COMMANDS:
        return dict(cmd.params)
    params = dict(cmd.params)
    if cmd.command == CommandType.REMEDIATE_SENTINEL_INTERVENTION and raw_payload:
        # Normalize any top-level two-man alias from the raw payload into the
        # canonical params key so the executor always receives two_man_signature_id.
        if not str(params.get("two_man_signature_id") or "").strip():
            for alias in _TWO_MAN_EVIDENCE_FIELDS:
                val = str(raw_payload.get(alias) or "").strip()
                if val:
                    params["two_man_signature_id"] = val
                    break
    if cmd.command == CommandType.APPROVED_APPLY:
        params.pop("rebalanceId", None)
        params["rebalance_id"] = cmd.target.id
    elif cmd.command == CommandType.EMERGENCY_CONTAINMENT:
        params.pop("personaId", None)
        params["persona_id"] = cmd.target.id
    canonical_action_id = _HUMAN_GATE_DECISIONS_BY_COMMAND.get(
        cmd.command,
        cmd.action or cmd.params.get("action_id") or cmd.params.get("actionId") or cmd.command.value,
    )
    if cmd.command == CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT:
        canonical_action_id = "submit_recommendation"
    # The target/action/actor fields come from the validated command envelope,
    # never from caller params.  Apart from fixing null adapter receipts, this
    # prevents a caller from redirecting an admitted command after validation.
    params.update(
        {
            "entity_type": cmd.params.get("entity_type") or cmd.target.type.value,
            "entity_id": cmd.target.id,
            "action_id": canonical_action_id,
            "actionId": canonical_action_id,
            "actor_id": identity.operator_id,
            "actor_role": next(
                (
                    role
                    for role in ("admin", "approver", "reviewer", "operator")
                    if role in identity.roles
                ),
                "operator",
            ),
        }
    )
    return params
def _assert_duplicate_confirm_token_matches(
    *,
    duplicate: Dict[str, Any],
    cmd: OperatorCommand,
    payload: Dict[str, Any],
    confirm_token: Optional[str],
    foundation_context: Dict[str, Any],
) -> None:
    audit = duplicate.get("audit") if isinstance(duplicate.get("audit"), dict) else {}
    evidence = (
        audit.get("precondition_evidence")
        if isinstance(audit.get("precondition_evidence"), dict)
        else {}
    )
    stored_params = (
        duplicate.get("params") if isinstance(duplicate.get("params"), dict) else {}
    )
    stored_token_id = str(
        evidence.get("confirm_token_id")
        or stored_params.get("confirm_token_id")
        or ""
    ).strip()
    if not stored_token_id:
        return
    supplied_token_id = _precondition_value(
        payload,
        dict(cmd.params),
        _CONFIRM_TOKEN_FIELDS,
        confirm_token,
    )
    if supplied_token_id == stored_token_id:
        return
    raise _foundation_idempotency_conflict_error(
        foundation_context=foundation_context,
        existing_command_id=str(duplicate.get("command_id") or ""),
    )
def _persist_admitted_command_with_confirm_token(
    *,
    command_id: str,
    command_type: CommandType,
    target: TargetObject,
    submitted_at: str,
    params: Dict[str, Any],
    audit_context: Dict[str, Any],
    foundation_context: Dict[str, Any],
    precondition_evidence: Dict[str, str],
    identity: OperatorIdentity,
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    token_id = str(precondition_evidence.get("confirm_token_id") or "").strip()
    if not token_id:
        return command_store.submit_command_if_no_active_target(
            command_id=command_id,
            command_type=command_type,
            target=target,
            submitted_at=submitted_at,
            params=params,
            audit_context=audit_context,
            foundation_context=foundation_context,
        )

    confirmation_id = f"auto-confirm-{command_id}"
    confirmation_request = {
        "confirm_token": token_id,
        "command_id": command_id,
        "confirmation_id": confirmation_id,
        "confirmed_by": identity.operator_id,
    }
    return command_store.submit_command_with_confirm_token_redeem_if_no_active_target(
        command_id=command_id,
        command_type=command_type,
        target=target,
        submitted_at=submitted_at,
        params=params,
        audit_context=audit_context,
        foundation_context=foundation_context,
        confirm_token_id=token_id,
        confirmation_id=confirmation_id,
        confirmation_command_id=f"cmd-{uuid.uuid4().hex[:16]}",
        confirmation_idempotency_key=f"auto-confirm:{command_id}",
        confirmation_request_hash=_stable_json_hash(confirmation_request),
        operator_id=identity.operator_id,
    )
def _resolve_execution_params_for_record(record: Dict[str, Any]) -> Dict[str, Any]:
    command_type = CommandType(record["type"])
    params = dict(record.get("params") or {})
    if command_type not in _DRAWER_RUNTIME_COMMANDS:
        return params

    target = record.get("target") or {}
    audit = record.get("audit") or {}
    runtime_id = str(target.get("id") or "").strip()
    if not runtime_id:
        raise ValueError(f"{command_type.value} is missing target.id.")

    return _derive_drawer_execution_params(
        command_type,
        runtime_id,
        params,
        actor_id=audit.get("operator_id"),
        reason=audit.get("reason"),
        incident_id=audit.get("incident_id"),
    )
def _validate_approve_deployment(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_DEPLOYMENT_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for ApproveDeployment",
            f"Missing fields: {sorted(missing)}",
        )
    if params["approval_decision"] not in _VALID_APPROVAL_DECISIONS:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid approval_decision value",
            f"Must be one of {_VALID_APPROVAL_DECISIONS}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.FORBIDDEN,
            "ApproveDeployment requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )
def _validate_approve_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_DECISION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for ApproveDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "ApproveDecision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )
def _validate_reject_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REJECT_DECISION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for RejectDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("rejection_reason") or "").strip():
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "RejectDecision requires a non-empty rejection_reason",
            "rejection_reason must be a non-empty string",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "RejectDecision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )
def _validate_request_approval_revision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REQUEST_APPROVAL_REVISION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for RequestApprovalRevision",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("revision_notes") or "").strip():
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "RequestApprovalRevision requires non-empty revision_notes",
            "revision_notes must be a non-empty string",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "RequestApprovalRevision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )
def _validate_pause_runtime(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _PAUSE_RUNTIME_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for PauseRuntime",
            f"Missing fields: {sorted(missing)}",
        )
    if params["pause_action"] not in _VALID_PAUSE_ACTIONS:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid pause_action value",
            f"Must be one of {_VALID_PAUSE_ACTIONS}",
        )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.FORBIDDEN,
            "PauseRuntime requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )
def _validate_execute_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for ExecuteRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if params["rollback_target_type"] not in _VALID_ROLLBACK_TARGET_TYPES:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid rollback_target_type",
            f"Must be one of {_VALID_ROLLBACK_TARGET_TYPES}",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.FORBIDDEN,
            "ExecuteRollback requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )
def _validate_approve_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for ApproveRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.FORBIDDEN,
            "ApproveRollback requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )
def _validate_reject_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REJECT_ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for RejectRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("rejection_reason") or "").strip():
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "RejectRollback requires a non-empty rejection_reason",
            "rejection_reason must be a non-empty string",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.FORBIDDEN,
            "RejectRollback requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )
def _validate_activate_kill_switch(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _KILL_SWITCH_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for ActivateKillSwitch",
            f"Missing fields: {sorted(missing)}",
        )
    if params["scope"] not in _VALID_SCOPES:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid scope for ActivateKillSwitch",
            f"Must be one of {_VALID_SCOPES}",
        )
    severity = params.get("severity")
    if severity is not None and severity not in _VALID_SEVERITIES:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid severity for ActivateKillSwitch",
            f"Must be one of {_VALID_SEVERITIES}",
        )
    # Admin role required
    if "admin" not in identity.roles:
        raise _bff_error(
            403, ErrorCode.FORBIDDEN,
            "ActivateKillSwitch requires 'admin' role",
            "Operator does not hold the admin role",
            precondition_failed="role_check",
            suggestion="Escalate to an admin-role operator",
        )
    # MFA required for kill-switch (§3.2.3)
    if not identity.mfa_verified:
        raise _bff_error(
            403, ErrorCode.AUTH_REQUIRED,
            "ActivateKillSwitch requires MFA verification",
            "Admin action requires MFA validation",
            precondition_failed="mfa_check",
            suggestion="Provide a valid MFA token in your session",
        )
def _validate_escalate_diff(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _ESCALATE_DIFF_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for EscalateDiff",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("escalation_reason") or "").strip():
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "EscalateDiff requires a non-empty escalation_reason",
            "escalation_reason must be a non-empty string",
        )
    if not {"operator", "reviewer", "approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "EscalateDiff requires operator-level governance access",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, reviewer, approver, or admin role",
        )
def _validate_approve_evolution_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_EVO_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for ApproveEvolutionDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if params["approval_action"] not in _VALID_EVO_APPROVAL_ACTIONS:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid approval_action",
            f"Must be one of {_VALID_EVO_APPROVAL_ACTIONS}",
        )
    if not {"reviewer", "admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.FORBIDDEN,
            "ApproveEvolutionDecision requires 'reviewer', 'approver', or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with reviewer, approver, or admin role",
        )
def _validate_execute_evolution_action(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _EXECUTE_EVO_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for ExecuteEvolutionAction",
            f"Missing fields: {sorted(missing)}",
        )
    if params["action_type"] not in _VALID_EVO_ACTION_TYPES:
        raise _bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid action_type for ExecuteEvolutionAction",
            f"Must be one of {_VALID_EVO_ACTION_TYPES}",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.FORBIDDEN,
            "ExecuteEvolutionAction requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )
def _mutation_review_surface_state(
    decision: Dict[str, Any],
    approval_decision: Optional[Dict[str, Any]],
    linked_incident: Optional[Dict[str, Any]],
    linked_postmortem: Optional[Dict[str, Any]],
) -> str:
    required_sources_available = True
    decision_state = str(decision.get("decision_state") or decision.get("status") or "").lower()
    approval_decision_id = str(decision.get("approval_decision_id") or "").strip()
    linked_incident_id = str(decision.get("linked_incident_id") or "").strip()
    linked_postmortem_id = str(decision.get("linked_postmortem_id") or "").strip()

    if read_store.dataset_source("evolution_decisions") == "missing":
        required_sources_available = False
    if decision_state in {"reviewed", "approved", "executed", "rejected", "superseded"}:
        if not approval_decision_id or approval_decision is None:
            required_sources_available = False
    if linked_incident_id and linked_incident is None:
        required_sources_available = False
    if linked_postmortem_id and linked_postmortem is None:
        required_sources_available = False

    read_surface_state = _read_surface_state()
    if read_surface_state == "unavailable" or not required_sources_available:
        return "unavailable"
    if read_surface_state in {"degraded", "stale"}:
        return "stale"

    dataset_names = ["evolution_decisions"]
    if approval_decision_id:
        dataset_names.append("approval_decisions")
    if linked_incident_id:
        dataset_names.append("incidents")
    if linked_postmortem_id:
        dataset_names.append("postmortems")
    if any(read_store.dataset_source(dataset) == "local_snapshot" for dataset in dataset_names):
        return "stale"
    return "fresh"
def _mutation_review_roles_for(
    risk_level: str,
    *,
    action: str,
) -> set[str]:
    normalized_risk = str(risk_level or "").lower()
    if action == "approve":
        return _MUTATION_APPROVAL_ROLES.get(normalized_risk, {"admin"})
    if action == "review":
        return _MUTATION_REVIEW_ROLES.get(normalized_risk, {"admin"})
    return _MUTATION_REJECTION_ROLES.get(normalized_risk, {"admin"})
def _mutation_review_allowed_actions(
    decision: Dict[str, Any],
    identity: OperatorIdentity,
    surface_state: str,
) -> Dict[str, bool]:
    if surface_state == "unavailable":
        return {
            "canReviewMutation": False,
            "canApproveMutation": False,
            "canRejectMutation": False,
            "canExecuteMutation": False,
        }

    decision_state = str(decision.get("decision_state") or decision.get("status") or "").lower()
    risk_level = str(decision.get("risk_level") or "").lower()
    identity_roles = set(identity.roles)

    can_review = (
        decision_state == "proposed"
        and bool(identity_roles.intersection(_mutation_review_roles_for(risk_level, action="review")))
    )
    can_approve = (
        decision_state == "reviewed"
        and bool(identity_roles.intersection(_mutation_review_roles_for(risk_level, action="approve")))
    )
    can_reject = (
        decision_state in {"proposed", "reviewed"}
        and bool(identity_roles.intersection(_mutation_review_roles_for(risk_level, action="reject")))
    )
    can_execute = (
        decision_state == "approved"
        and bool(identity_roles.intersection(_MUTATION_EXECUTION_ROLES))
    )
    return {
        "canReviewMutation": can_review,
        "canApproveMutation": can_approve,
        "canRejectMutation": can_reject,
        "canExecuteMutation": can_execute,
    }
def _mutation_threshold_triggers(decision: Dict[str, Any]) -> List[Dict[str, Any]]:
    risk_assessment = decision.get("risk_assessment") or {}
    explicit = risk_assessment.get("threshold_triggers")
    if isinstance(explicit, list):
        return explicit

    triggers: List[Dict[str, Any]] = []
    for snapshot in decision.get("threshold_snapshots") or []:
        if not isinstance(snapshot, dict):
            continue
        triggers.append(
            {
                "trigger_type": snapshot.get("signal_type"),
                "metric": snapshot.get("metric_name"),
                "observed_value": str(snapshot.get("observed_value")),
                "threshold_value": str(snapshot.get("threshold_value")),
                "threshold_source": snapshot.get("policy_source"),
            }
        )
    return triggers
def _mutation_required_approvals(decision: Dict[str, Any]) -> List[Dict[str, Any]]:
    explicit = decision.get("required_approvals")
    if isinstance(explicit, list):
        return explicit

    risk_level = str(decision.get("risk_level") or "").lower()
    if risk_level == "low":
        required_roles = ["reviewer_on_duty"]
    elif risk_level == "medium":
        required_roles = ["reviewer", "risk_owner"]
    elif risk_level == "high":
        required_roles = ["governance_committee"]
    else:
        required_roles = []

    approvals: List[Dict[str, Any]] = []
    review_chain = decision.get("review_chain") or []
    for role in required_roles:
        matched_step = next(
            (
                step for step in review_chain
                if isinstance(step, dict)
                and str(step.get("actor_role") or "").lower() == role
                and str(step.get("step_type") or step.get("action") or "").lower() in {"reviewed", "approved"}
            ),
            None,
        )
        approvals.append(
            {
                "role": role,
                "approved_by": matched_step.get("actor_id") if matched_step else None,
                "approved_at": matched_step.get("timestamp") if matched_step else None,
                "status": "approved" if matched_step else "pending",
            }
        )
    return approvals
def _mutation_review_projection(
    decision: Dict[str, Any],
    *,
    approval_decision: Optional[Dict[str, Any]],
    linked_incident: Optional[Dict[str, Any]],
    linked_postmortem: Optional[Dict[str, Any]],
    identity: OperatorIdentity,
    snapshot_at: str,
) -> Dict[str, Any]:
    surface_state = _mutation_review_surface_state(
        decision,
        approval_decision,
        linked_incident,
        linked_postmortem,
    )
    allowed_actions = _mutation_review_allowed_actions(decision, identity, surface_state)
    proposed_changes = dict(decision.get("proposed_changes") or {})
    risk_assessment = dict(decision.get("risk_assessment") or {})
    evidence_refs = list(decision.get("evidence_refs") or [])

    if linked_incident and not any(ref.get("ref_id") == linked_incident.get("incident_id") for ref in evidence_refs if isinstance(ref, dict)):
        evidence_refs.append(
            {
                "ref_type": "incident",
                "ref_id": linked_incident.get("incident_id"),
                "summary": linked_incident.get("evidence_summary") or linked_incident.get("title"),
            }
        )
    postmortem_id = (
        linked_postmortem.get("postmortem_id")
        or linked_postmortem.get("report_id")
        or linked_postmortem.get("id")
        if linked_postmortem
        else None
    )
    if linked_postmortem and not any(ref.get("ref_id") == postmortem_id for ref in evidence_refs if isinstance(ref, dict)):
        evidence_refs.append(
            {
                "ref_type": "postmortem",
                "ref_id": postmortem_id,
                "summary": linked_postmortem.get("summary") or linked_postmortem.get("title"),
            }
        )

    if "summary" not in proposed_changes:
        proposed_changes["summary"] = decision.get("rationale") or decision.get("notes") or ""
    proposed_changes.setdefault("target_stage", decision.get("target_stage"))
    proposed_changes.setdefault("downstream_plane", (decision.get("execution_result") or {}).get("plane"))
    proposed_changes.setdefault("change_details", [])

    # Apply evidence redaction based on derived capabilities for this identity.
    try:
        capabilities = _capabilities_for_identity(identity)
    except Exception:
        capabilities = None
    evidence_refs, redacted_count = redact_evidence_refs(identity, evidence_refs, capabilities=capabilities)

    risk_assessment.setdefault(
        "risk_summary",
        decision.get("notes") or decision.get("rationale") or "",
    )
    risk_assessment.setdefault("severity", None)
    risk_assessment["threshold_triggers"] = _mutation_threshold_triggers(decision)

    review_chain = [
        {
            "action": step.get("action") or step.get("step_type"),
            "actor_role": step.get("actor_role"),
            "actor_id": step.get("actor_id"),
            "acted_at": step.get("acted_at") or step.get("timestamp"),
            "note": step.get("note"),
        }
        for step in (decision.get("review_chain") or [])
        if isinstance(step, dict)
    ]

    rollback_followthrough = decision.get("rollback_followthrough")
    if rollback_followthrough is None:
        linked_incident_id = str(decision.get("linked_incident_id") or "").strip()
        rollbacks = read_store.get_rollbacks_by_incident(linked_incident_id) if linked_incident_id else []
        if rollbacks:
            first_rollback = rollbacks[0]
            rollback_followthrough = {
                "rollback_request_ref": first_rollback.get("rollback_id") or first_rollback.get("id"),
                "rollback_action_type": first_rollback.get("action_type"),
                "followthrough_note": first_rollback.get("reason"),
            }

    meta = {**_snapshot_meta(snapshot_at), "surfaces": {"mutation_review": surface_state}}
    # Attach supporting_counts including redaction telemetry
    meta.setdefault("supporting_counts", {})
    meta["supporting_counts"]["redacted_evidence_count"] = redacted_count

    return {
        "decision_id": decision.get("decision_id") or decision.get("id"),
        "target_type": decision.get("target_type"),
        "target_id": decision.get("target_id") or decision.get("artifact_id"),
        "target_version": decision.get("target_version"),
        "action_type": decision.get("action_type"),
        "decision_state": decision.get("decision_state") or decision.get("status"),
        "risk_level": decision.get("risk_level"),
        "created_at": decision.get("created_at"),
        "approval_decision_id": decision.get("approval_decision_id"),
        "proposed_changes": proposed_changes,
        "risk_assessment": risk_assessment,
        "required_approvals": _mutation_required_approvals(decision),
        "review_chain": review_chain,
        "linked_incident_id": decision.get("linked_incident_id"),
        "linked_postmortem_id": decision.get("linked_postmortem_id"),
        "evidence_refs": evidence_refs,
        "rollback_followthrough": rollback_followthrough,
        "allowedActions": allowed_actions,
        "meta": meta,
    }
def _mutation_review_inputs(
    decision_id: str,
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    decision = read_store.get_evolution_decision_by_id(decision_id)
    if decision is None:
        return None, None, None, None

    approval_decision_id = str(decision.get("approval_decision_id") or "").strip()
    approval_decision = (
        read_store.get_approval_decision(approval_decision_id)
        if approval_decision_id
        else None
    )
    linked_incident_id = str(decision.get("linked_incident_id") or "").strip()
    linked_incident = read_store.get_incident(linked_incident_id) if linked_incident_id else None
    linked_postmortem_id = str(decision.get("linked_postmortem_id") or "").strip()
    linked_postmortem = (
        read_store.get_postmortem(linked_postmortem_id)
        if linked_postmortem_id
        else None
    )
    return decision, approval_decision, linked_incident, linked_postmortem
def _validate_record_sponsor_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    from .governance.service import GovernanceService

    missing = _RECORD_SPONSOR_DECISION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for RecordSponsorDecision",
            f"Missing fields: {sorted(missing)}",
        )
    sponsor_decision = str(params.get("sponsor_decision") or "").strip().lower()
    if sponsor_decision not in _VALID_SPONSOR_DECISIONS:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid sponsor_decision value",
            f"sponsor_decision must be one of {sorted(_VALID_SPONSOR_DECISIONS)}",
        )
    rationale_ref = str(params.get("rationale_ref") or "").strip()
    if not rationale_ref:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "RecordSponsorDecision requires a non-empty rationale_ref",
            "rationale_ref must be a non-empty string",
        )
    committee_id = str(params.get("committee_id") or "").strip()
    governance_service = GovernanceService(
        read_store,
        utc_now=utc_now,
        dataset_surface_status=_dataset_surface_status,
    )
    projection = governance_service.committee_projection(
        committee_id,
        identity=identity,
        snapshot_at=utc_now(),
    )
    if projection is None:
        raise _bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Committee board not found",
            f"Committee {committee_id} does not exist",
        )
    if projection["meta"]["surfaces"]["committee_board"] == "unavailable":
        raise _bff_error(
            409,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "RecordSponsorDecision is blocked while the committee board is unavailable",
            "Committee evidence cannot be composed reliably",
            precondition_failed="committee_board_surface",
        )
    if not projection["allowedActions"]["canRecordSponsorDecision"]:
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "RecordSponsorDecision is not allowed for this operator and committee state",
            "allowedActions.canRecordSponsorDecision is false for the current read projection",
            precondition_failed="allowedActions.canRecordSponsorDecision",
        )
def _validate_approve_mutation(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_MUTATION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for ApproveMutation",
            f"Missing fields: {sorted(missing)}",
        )
    decision_id = str(params.get("decision_id") or "").strip()
    decision, approval_decision, linked_incident, linked_postmortem = _mutation_review_inputs(decision_id)
    if decision is None:
        raise _bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Mutation review decision not found",
            f"Evolution decision {decision_id} does not exist",
        )
    projection = _mutation_review_projection(
        decision,
        approval_decision=approval_decision,
        linked_incident=linked_incident,
        linked_postmortem=linked_postmortem,
        identity=identity,
        snapshot_at=utc_now(),
    )
    if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
        raise _bff_error(
            409,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "ApproveMutation is blocked while the mutation-review surface is unavailable",
            "Mutation-review evidence cannot be composed reliably",
            precondition_failed="mutation_review_surface",
        )
    if not projection["allowedActions"]["canApproveMutation"]:
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "ApproveMutation is not allowed for this operator and decision state",
            "allowedActions.canApproveMutation is false for the current read projection",
            precondition_failed="allowedActions.canApproveMutation",
        )
def _validate_reject_mutation(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REJECT_MUTATION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for RejectMutation",
            f"Missing fields: {sorted(missing)}",
        )
    decision_id = str(params.get("decision_id") or "").strip()
    decision, approval_decision, linked_incident, linked_postmortem = _mutation_review_inputs(decision_id)
    if decision is None:
        raise _bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Mutation review decision not found",
            f"Evolution decision {decision_id} does not exist",
        )
    projection = _mutation_review_projection(
        decision,
        approval_decision=approval_decision,
        linked_incident=linked_incident,
        linked_postmortem=linked_postmortem,
        identity=identity,
        snapshot_at=utc_now(),
    )
    if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
        raise _bff_error(
            409,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "RejectMutation is blocked while the mutation-review surface is unavailable",
            "Mutation-review evidence cannot be composed reliably",
            precondition_failed="mutation_review_surface",
        )
    if not projection["allowedActions"]["canRejectMutation"]:
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "RejectMutation is not allowed for this operator and decision state",
            "allowedActions.canRejectMutation is false for the current read projection",
            precondition_failed="allowedActions.canRejectMutation",
        )
def _validate_review_mutation(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REVIEW_MUTATION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for ReviewMutation",
            f"Missing fields: {sorted(missing)}",
        )
    decision_id = str(params.get("decision_id") or "").strip()
    decision, approval_decision, linked_incident, linked_postmortem = _mutation_review_inputs(decision_id)
    if decision is None:
        raise _bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Mutation review decision not found",
            f"Evolution decision {decision_id} does not exist",
        )
    projection = _mutation_review_projection(
        decision,
        approval_decision=approval_decision,
        linked_incident=linked_incident,
        linked_postmortem=linked_postmortem,
        identity=identity,
        snapshot_at=utc_now(),
    )
    if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
        raise _bff_error(
            409,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "ReviewMutation is blocked while the mutation-review surface is unavailable",
            "Mutation-review evidence cannot be composed reliably",
            precondition_failed="mutation_review_surface",
        )
    if not projection["allowedActions"]["canReviewMutation"]:
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "ReviewMutation is not allowed for this operator and decision state",
            "allowedActions.canReviewMutation is false for the current read projection",
            precondition_failed="allowedActions.canReviewMutation",
        )
def _validate_execute_mutation(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _EXECUTE_MUTATION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for ExecuteMutation",
            f"Missing fields: {sorted(missing)}",
        )
    decision_id = str(params.get("decision_id") or "").strip()
    decision, approval_decision, linked_incident, linked_postmortem = _mutation_review_inputs(decision_id)
    if decision is None:
        raise _bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Mutation review decision not found",
            f"Evolution decision {decision_id} does not exist",
        )
    projection = _mutation_review_projection(
        decision,
        approval_decision=approval_decision,
        linked_incident=linked_incident,
        linked_postmortem=linked_postmortem,
        identity=identity,
        snapshot_at=utc_now(),
    )
    if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
        raise _bff_error(
            409,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "ExecuteMutation is blocked while the mutation-review surface is unavailable",
            "Mutation-review evidence cannot be composed reliably",
            precondition_failed="mutation_review_surface",
        )
    if not projection["allowedActions"]["canExecuteMutation"]:
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "ExecuteMutation is not allowed for this operator and decision state",
            "allowedActions.canExecuteMutation is false for the current read projection",
            precondition_failed="allowedActions.canExecuteMutation",
        )
def _validate_remediate_sentinel_intervention(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REMEDIATE_SENTINEL_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for RemediateSentinelIntervention",
            f"Missing fields: {sorted(missing)}",
        )
    remediation_action = str(params.get("remediation_action") or "").strip()
    if remediation_action not in _VALID_REMEDIATION_ACTIONS:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid remediation_action value",
            f"remediation_action must be one of {sorted(_VALID_REMEDIATION_ACTIONS)}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "RemediateSentinelIntervention requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )
def _validate_decide_v5_intervention(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _DECIDE_V5_INTERVENTION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for DecideV5Intervention",
            f"Missing fields: {sorted(missing)}",
            precondition_failed="decision",
        )
    decision = str(params.get("decision") or "").strip().lower()
    if decision not in _VALID_V5_INTERVENTION_DECISIONS:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid intervention decision value",
            f"decision must be one of {sorted(_VALID_V5_INTERVENTION_DECISIONS)}",
            precondition_failed="decision",
        )
    if not {"operator", "approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "DecideV5Intervention requires 'operator', 'approver', or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, approver, or admin role",
        )
def _validate_human_gate_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _HUMAN_GATE_REQUIRED - {key for key, value in params.items() if value not in (None, "")}
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for HumanGate command",
            f"Missing fields: {sorted(missing)}",
            precondition_failed="human_gate",
        )

    decision = str(params.get("decision") or "").strip().lower()
    if decision not in _VALID_HUMAN_GATE_DECISIONS:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid HumanGate decision value",
            f"decision must be one of {sorted(_VALID_HUMAN_GATE_DECISIONS)}",
            precondition_failed="decision",
        )

    if decision in _HUMAN_GATE_APPROVER_DECISIONS and not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "HumanGate decision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )
    if decision == "request_more_evidence" and not {"operator", "approver", "admin", "reviewer"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "HumanGate evidence request requires operator-level role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, reviewer, approver, or admin role",
        )

    if decision == "extend_ttl":
        raw_ttl = (
            params.get("ttl_seconds")
            or params.get("ttlSeconds")
            or params.get("extend_ttl_seconds")
            or params.get("extendTtlSeconds")
        )
        try:
            ttl_seconds = int(raw_ttl)
        except (TypeError, ValueError):
            ttl_seconds = 0
        if ttl_seconds <= 0:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "HumanGateExtendTtl requires a positive ttl_seconds value",
                "ttl_seconds must be a positive integer number of seconds",
                precondition_failed="ttl_seconds",
            )
        max_ttl_seconds = _human_gate_max_ttl_seconds()
        if ttl_seconds > max_ttl_seconds:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "HumanGateExtendTtl exceeds the maximum ttl_seconds cap",
                "HUMAN_GATE_TTL_EXCEEDS_CAP",
                precondition_failed="ttl_seconds",
                suggestion="Retry with a shorter HumanGate TTL extension",
                details_extra={
                    "maxTtlSeconds": max_ttl_seconds,
                    "ttlSeconds": ttl_seconds,
                    "constraint": f"ttl_seconds must be less than or equal to {max_ttl_seconds}",
                },
            )
        params["ttl_seconds"] = ttl_seconds
        params["ttlSeconds"] = ttl_seconds
def _pm12_resolve_quarterly_recommendation_submit_params(
    params: Dict[str, Any],
) -> Dict[str, Any]:
    recommendation_id = str(
        params.get("recommendation_id") or params.get("recommendationId") or ""
    ).strip()
    snapshot_id = str(params.get("ranking_snapshot_id") or "").strip()
    quarter = str(params.get("quarter") or "").strip().upper()
    if not recommendation_id or not snapshot_id or not quarter:
        return dict(params)
    snapshot = _pm12_recommendation_snapshot_record(snapshot_id)
    snapshot_quarter = str(snapshot.get("period") or "").strip().upper()
    if snapshot_quarter != quarter:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "quarter does not match the admitted ranking snapshot",
            "The submitted quarter must be the immutable snapshot period.",
            precondition_failed="quarter",
        )

    matched_item: Optional[Dict[str, Any]] = None
    matched_action_id = ""
    for item in snapshot.get("items") or []:
        if not isinstance(item, dict):
            continue
        persona_id = str(item.get("persona_id") or "").strip()
        for action_id in _pm12_recommendation_action_ids(item):
            expected_id = f"pm12-{quarter.lower()}-{persona_id}-{action_id}"
            if expected_id == recommendation_id:
                matched_item = item
                matched_action_id = action_id
                break
        if matched_item is not None:
            break
    if matched_item is None:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "recommendation is not in the admitted ranking snapshot",
            "The recommendation id/action/persona tuple was not materialized by the snapshot.",
            precondition_failed="recommendation_id",
        )
    review_revision_id = _promotion_review_revision_id(
        recommendation_id,
        snapshot_id,
    )
    for field in ("review_id", "promotion_review_id"):
        asserted_review_id = str(params.get(field) or "").strip()
        if (
            asserted_review_id
            and _promotion_review_clean_id(asserted_review_id)
            != review_revision_id
        ):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "promotion review revision assertion mismatch",
                f"{field} does not match the admitted recommendation snapshot.",
                precondition_failed=field,
            )

    asserted_action_id = str(
        params.get("recommendation_action_id")
        or params.get("recommendationActionId")
        or ""
    ).strip()
    if asserted_action_id and asserted_action_id != matched_action_id:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "recommendation action does not match the admitted snapshot",
            "The caller-supplied recommendation action is not authoritative.",
            precondition_failed="recommendation_action_id",
        )

    item = {
        **json.loads(json.dumps(matched_item)),
        "ranking_snapshot_id": snapshot_id,
        "evidence_refs": [],
    }
    quarter_window = _pm12_quarter_window(quarter, utc_now())
    source_recommendation = _pm12_quarterly_recommendation_item(
        item,
        action_id=matched_action_id,
        quarter_window=quarter_window,
        evidence_refs=[],
    )
    source_recommendation["human_review_state"] = {
        "status": "recommended_not_submitted",
        "decision_status": "pending",
        "submitted": False,
        "submit_status": "not_submitted",
        "decision": None,
        "decided_at": None,
        "decided_by": None,
    }
    stored_source = _promotion_review_stored_source(source_recommendation)
    stage_path = _promotion_review_stage_path(source_recommendation)
    canonical_assertions = {
        "persona_id": item.get("persona_id"),
        "stage": item.get("stage"),
        "deployment_stage": item.get("deployment_stage"),
        "stage_from": stage_path.get("from_stage"),
        "stage_to": stage_path.get("target_stage"),
        "review_kind": stage_path.get("review_kind"),
        "current_weight": item.get("current_weight"),
        "target_weight": item.get("target_weight"),
        "delta": item.get("delta"),
        "capital_scope": item.get("capital_scope"),
        "capital_pool_id": item.get("capital_pool_id"),
        "capital_sleeve_id": item.get("capital_sleeve_id"),
        "evidence_ref_ids": sorted(item.get("evidence_ref_ids") or []),
    }
    for field, authoritative_value in canonical_assertions.items():
        if field not in params:
            continue
        asserted_value = params.get(field)
        if field == "evidence_ref_ids":
            asserted_value = sorted(asserted_value or [])
        if not _pm12_semantic_values_match(asserted_value, authoritative_value):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "quarterly recommendation assertion mismatch",
                f"{field} does not match the admitted ranking snapshot.",
                precondition_failed=field,
            )
    if params.get("evidence_refs") not in (None, []):
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "caller evidence is not admissible",
            "Quarterly recommendation evidence is materialized server-side.",
            precondition_failed="evidence_refs",
        )
    asserted_source = params.get("source_recommendation")
    if asserted_source is not None:
        if not isinstance(asserted_source, dict):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "source recommendation assertion mismatch",
                "source_recommendation must be an object when supplied.",
                precondition_failed="source_recommendation",
            )
        nested_assertions = {
            "id": recommendation_id,
            "recommendation_id": recommendation_id,
            "review_id": review_revision_id,
            "promotion_review_id": review_revision_id,
            "ranking_snapshot_id": snapshot_id,
            "quarter": quarter,
            "persona_id": item.get("persona_id"),
            "action_id": matched_action_id,
            "recommendation_action_id": matched_action_id,
            "stage": item.get("stage"),
            "deployment_stage": item.get("deployment_stage"),
            "stage_from": stage_path.get("from_stage"),
            "stage_to": stage_path.get("target_stage"),
            "review_kind": stage_path.get("review_kind"),
            "current_weight": item.get("current_weight"),
            "target_weight": item.get("target_weight"),
            "delta": item.get("delta"),
            "capital_scope": item.get("capital_scope"),
            "capital_pool_id": item.get("capital_pool_id"),
            "capital_sleeve_id": item.get("capital_sleeve_id"),
            "evidence_ref_ids": sorted(item.get("evidence_ref_ids") or []),
        }
        for field, authoritative_value in nested_assertions.items():
            if field not in asserted_source:
                continue
            asserted_value = asserted_source.get(field)
            if field == "evidence_ref_ids":
                asserted_value = sorted(asserted_value or [])
            if not _pm12_semantic_values_match(asserted_value, authoritative_value):
                raise _bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "source recommendation assertion mismatch",
                    f"source_recommendation.{field} does not match the admitted ranking snapshot.",
                    precondition_failed=field,
                )
        if asserted_source.get("evidence_refs") not in (None, []):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "caller evidence is not admissible",
                "source_recommendation evidence is materialized server-side.",
                precondition_failed="evidence_refs",
            )

    canonical: Dict[str, Any] = {
        "quarter": quarter,
        "recommendation_id": recommendation_id,
        "recommendationId": recommendation_id,
        "review_id": review_revision_id,
        "promotion_review_id": review_revision_id,
        "recommendation_action_id": matched_action_id,
        "recommendationActionId": matched_action_id,
        "ranking_snapshot_id": snapshot_id,
        "ranking_snapshot_content_digest": snapshot.get("content_digest"),
        "ranking_item_digest": _stable_json_hash(matched_item),
        "ranking_evidence_ref_ids": sorted(item.get("evidence_ref_ids") or []),
        "persona_id": item.get("persona_id"),
        "stage": item.get("stage"),
        "deployment_stage": item.get("deployment_stage"),
        "current_weight": item.get("current_weight"),
        "target_weight": item.get("target_weight"),
        "capital_scope": item.get("capital_scope"),
        "capital_pool_id": item.get("capital_pool_id"),
        "capital_sleeve_id": item.get("capital_sleeve_id"),
        "stage_from": stage_path.get("from_stage"),
        "stage_to": stage_path.get("target_stage"),
        "review_kind": stage_path.get("review_kind"),
        "requires_human_gate_decision": True,
        "live_capital_mutation": False,
        "liveCapitalMutation": False,
        "direct_live_capital_mutation": False,
        "runtime_mutation": False,
        "source_type": "quarterly_ranking_recommendation",
        "source_record_id": recommendation_id,
        "source_recommendation": stored_source,
        "audit_event": "quarterly_ranking.recommendation_submitted",
        "policy": "promotion_governance_human_gate_no_direct_live_capital",
    }
    for field in ("reason", "note", "memo", "rationale"):
        value = str(params.get(field) or "").strip()
        if value:
            canonical[field] = value
    return canonical
def _validate_quarterly_ranking_recommendation_submit(
    params: Dict[str, Any],
    identity: OperatorIdentity,
) -> None:
    if not {"operator", "approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Quarterly ranking recommendation submission requires operator-level role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, approver, or admin role",
        )

    _raise_if_promotion_review_direct_mutation_requested(params)
    resolved = _pm12_resolve_quarterly_recommendation_submit_params(params)
    params.clear()
    params.update(resolved)

    required = {"quarter", "recommendation_id", "ranking_snapshot_id"}
    missing = required - {key for key, value in params.items() if value not in (None, "")}
    if missing:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for QuarterlyRankingRecommendationSubmit",
            f"Missing fields: {sorted(missing)}",
            precondition_failed="quarterly_ranking_recommendation",
        )
    action_id = str(
        params.get("recommendation_action_id")
        or params.get("recommendationActionId")
        or ""
    ).strip()
    if action_id and action_id not in _PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid quarterly ranking recommendation action",
            f"recommendation_action_id must be one of {list(_PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER)}",
            precondition_failed="recommendation_action_id",
        )
def _enforce_ops_console_preconditions(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    required_bindings: Optional[List[str]] = None,
) -> None:
    entity_type = str(params.get("entity_type") or params.get("entityType") or "").strip().lower()
    persona_id = ""
    runtime_id = ""

    if entity_type == "persona":
        persona_id = (
            params.get("persona_id")
            or params.get("personaId")
            or params.get("entity_id")
            or params.get("entityId")
            or ""
        ).strip()
    elif entity_type == "runtime":
        runtime_id = (
            params.get("runtime_id")
            or params.get("runtimeId")
            or params.get("entity_id")
            or params.get("entityId")
            or ""
        ).strip()

    if not persona_id:
        persona_id = (params.get("persona_id") or params.get("personaId") or "").strip()
    if not runtime_id:
        runtime_id = (params.get("runtime_id") or params.get("runtimeId") or "").strip()

    if persona_id:
        persona = read_store.get_persona(persona_id)
        if not persona:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )

        read_model = _ops_read_model_entry_for_persona(persona_id)
        if read_model:
            confidence = read_model.data_confidence
            if isinstance(confidence, str):
                confidence_str = confidence
            elif hasattr(confidence, "value"):
                confidence_str = confidence.value
            else:
                confidence_str = str(confidence)

            if confidence_str.lower() in ("unavailable", "unverifiable"):
                raise _bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    f"Action blocked due to {confidence_str} source confidence for persona {persona_id}",
                    "Source confidence must be formal, partial, fallback, or degraded",
                    precondition_failed="source_confidence",
                )

            if required_bindings:
                if "runtime" in required_bindings:
                    if not read_model.identity.runtime_ids:
                        raise _bff_error(
                            422,
                            ErrorCode.VALIDATION_FAILED,
                            f"Persona {persona_id} must have an active runtime binding",
                            "No active runtime binding found for this persona",
                            precondition_failed="runtime_binding_missing",
                        )
                if "capital" in required_bindings:
                    if not read_model.identity.capital_pool_ids and not read_model.identity.paper_ledger_ids:
                        raise _bff_error(
                            422,
                            ErrorCode.VALIDATION_FAILED,
                            f"Persona {persona_id} must have a capital pool or paper ledger binding",
                            "No active capital or ledger binding found for this persona",
                            precondition_failed="capital_binding_missing",
                        )

    runtime_id = (
        params.get("runtime_id")
        or params.get("runtimeId")
        or ""
    ).strip()
    if runtime_id:
        binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
        if not binding:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Runtime not found",
                f"Runtime {runtime_id} does not exist",
            )
        if required_bindings and "paper" in required_bindings:
            stage = str(binding.get("deployment_stage") or binding.get("stage") or "").strip().lower()
            if stage != "paper":
                raise _bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    f"Runtime {runtime_id} stage is {stage}, not paper",
                    "Action is restricted to paper runtimes only",
                    precondition_failed="stage_mismatch",
                )
def _validate_observe(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    if not {"operator", "reviewer", "approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Observe action requires operator, reviewer, approver, or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    _enforce_ops_console_preconditions(params, identity)
def _validate_request_review(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "RequestReview action requires operator or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    persona_id = params.get("persona_id") or params.get("personaId")
    if not persona_id:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing persona_id for RequestReview",
            "persona_id must be provided to request a review",
            precondition_failed="missing_persona",
        )
    _enforce_ops_console_preconditions(params, identity)
def _validate_pause_paper_runtime(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "PausePaperRuntime action requires operator or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    runtime_id = params.get("runtime_id") or params.get("runtimeId")
    if not runtime_id:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing runtime_id for PausePaperRuntime",
            "runtime_id must be provided",
            precondition_failed="missing_runtime",
        )
    _enforce_ops_console_preconditions(params, identity, required_bindings=["paper"])
def _validate_resume_paper_runtime(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    if not {"operator", "approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "ResumePaperRuntime action requires operator, approver, or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    runtime_id = params.get("runtime_id") or params.get("runtimeId")
    if not runtime_id:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing runtime_id for ResumePaperRuntime",
            "runtime_id must be provided",
            precondition_failed="missing_runtime",
        )
    _enforce_ops_console_preconditions(params, identity, required_bindings=["paper"])
def _validate_demote(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    if not {"operator", "approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Demote action requires operator, approver, or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    persona_id = params.get("persona_id") or params.get("personaId")
    if not persona_id:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing persona_id for Demote",
            "persona_id must be provided",
            precondition_failed="missing_persona",
        )
    _enforce_ops_console_preconditions(params, identity)
def _validate_promote_candidate(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    if not {"operator", "approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "PromoteCandidate action requires operator, approver, or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    persona_id = params.get("persona_id") or params.get("personaId")
    if not persona_id:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing persona_id for PromoteCandidate",
            "persona_id must be provided",
            precondition_failed="missing_persona",
        )
    _enforce_ops_console_preconditions(params, identity)
def _validate_rebalance_proposal(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "RebalanceProposal action requires operator or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    raise _bff_error(
        422,
        ErrorCode.VALIDATION_FAILED,
        "RebalanceProposal requires server-side allocation admission",
        "Submit the exact allocation evaluation through POST /bff/rebalances.",
        precondition_failed="allocation_evaluation_id",
        suggestion="Use POST /bff/management/allocation-policy/evaluate, then POST /bff/rebalances.",
    )
def _validate_approved_apply(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    if not {"operator", "approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "ApprovedApply action requires operator, approver, or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    _enforce_ops_console_preconditions(params, identity)
def _validate_emergency_containment(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    if not {"operator", "reviewer", "approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "EmergencyContainment action requires operator, reviewer, approver, or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    try:
        validate_emergency_containment(params)
    except (TypeError, ValueError) as exc:
        detail = str(exc)
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            detail[:1].upper() + detail[1:],
            detail,
            precondition_failed="emergency_containment_invalid_action",
        ) from exc

    _enforce_ops_console_preconditions(params, identity)
_VALIDATORS = {
    CommandType.APPROVE_DEPLOYMENT: _validate_approve_deployment,
    CommandType.APPROVE_DECISION: _validate_approve_decision,
    CommandType.REJECT_DECISION: _validate_reject_decision,
    CommandType.REQUEST_APPROVAL_REVISION: _validate_request_approval_revision,
    CommandType.PAUSE_RUNTIME: _validate_pause_runtime,
    CommandType.PAUSE_EXECUTION: _validate_pause_execution,
    CommandType.ESCALATE_DIFF: _validate_escalate_diff,
    CommandType.ISSUE_RISK_OFF: _validate_issue_risk_off,
    CommandType.LIQUIDATE_ALL: _validate_liquidate_all,
    CommandType.HARD_ROLLBACK: _validate_hard_rollback,
    CommandType.ISSUE_SAFE_MODE: _validate_issue_safe_mode,
    CommandType.EXECUTE_ROLLBACK: _validate_execute_rollback,
    CommandType.APPROVE_ROLLBACK: _validate_approve_rollback,
    CommandType.REJECT_ROLLBACK: _validate_reject_rollback,
    CommandType.ACTIVATE_KILL_SWITCH: _validate_activate_kill_switch,
    CommandType.APPROVE_EVOLUTION_DECISION: _validate_approve_evolution_decision,
    CommandType.EXECUTE_EVOLUTION_ACTION: _validate_execute_evolution_action,
    CommandType.APPROVE_MUTATION: _validate_approve_mutation,
    CommandType.REJECT_MUTATION: _validate_reject_mutation,
    CommandType.REVIEW_MUTATION: _validate_review_mutation,
    CommandType.EXECUTE_MUTATION: _validate_execute_mutation,
    CommandType.RECORD_SPONSOR_DECISION: _validate_record_sponsor_decision,
    CommandType.REMEDIATE_SENTINEL_INTERVENTION: _validate_remediate_sentinel_intervention,
    CommandType.DECIDE_V5_INTERVENTION: _validate_decide_v5_intervention,
    CommandType.HUMAN_GATE_APPROVE: _validate_human_gate_decision,
    CommandType.HUMAN_GATE_REJECT: _validate_human_gate_decision,
    CommandType.HUMAN_GATE_REQUEST_MORE_EVIDENCE: _validate_human_gate_decision,
    CommandType.HUMAN_GATE_REVOKE: _validate_human_gate_decision,
    CommandType.HUMAN_GATE_EXTEND_TTL: _validate_human_gate_decision,
    CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT: _validate_quarterly_ranking_recommendation_submit,
    CommandType.OBSERVE: _validate_observe,
    CommandType.REQUEST_REVIEW: _validate_request_review,
    CommandType.PAUSE_PAPER_RUNTIME: _validate_pause_paper_runtime,
    CommandType.RESUME_PAPER_RUNTIME: _validate_resume_paper_runtime,
    CommandType.DEMOTE: _validate_demote,
    CommandType.PROMOTE_CANDIDATE: _validate_promote_candidate,
    CommandType.REBALANCE_PROPOSAL: _validate_rebalance_proposal,
    CommandType.APPROVED_APPLY: _validate_approved_apply,
    CommandType.EMERGENCY_CONTAINMENT: _validate_emergency_containment,
}
_READ_ROLES = {"viewer", "view_only", "operator", "approver", "admin", "reviewer"}
_WRITE_ROLES = {"operator", "approver", "admin", "reviewer"}
def _require_read_role(identity: OperatorIdentity) -> None:
    if not _READ_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Read access requires viewer-level role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with viewer, operator, approver, admin, or reviewer role",
        )
def _require_operator_role(identity: OperatorIdentity) -> None:
    if not _WRITE_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Operator command access requires operator-level role",
            "Operator does not hold the required command role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, approver, admin, or reviewer role",
        )
_ROLE_CAPABILITY_MAP = {
    "admin": list(EVIDENCE_CAPABILITY_MAP.values()),
    "approver": [
        "approval.read",
        "postmortem.read",
        "policy.read",
    ],
    "operator": [
        "runtime.read",
        "risk.incident.read",
        "risk.alert.read",
        "artifact.read",
    ],
    "reviewer": [
        "approval.read",
        "strategy.view",
        "persona.view",
    ],
    "analyst": [
        "metric.read",
        "job.read",
        "audit.read",
    ],
    "viewer": [
        "metric.read",
        "strategy.view",
        "persona.view",
    ],
}
def _capabilities_for_identity(identity: OperatorIdentity) -> List[str]:
    """Derive a best-effort capability set from operator roles.

    This is a fallback for deployments where explicit capability claims
    are not provided by upstream auth. It is intentionally permissive for
    admin and conservative for other roles.
    """
    caps: List[str] = []
    for role in identity.roles:
        mapped = _ROLE_CAPABILITY_MAP.get(role)
        if mapped:
            caps.extend(mapped)
    # Deduplicate while preserving order
    seen = set()
    result: List[str] = []
    for c in caps:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result
def _dedupe_nonblank_strings(values: List[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result
def _split_claim_string(value: str) -> List[str]:
    clean = value.strip()
    if not clean:
        return []
    separator_pattern = r"[\s,]+" if "," not in clean else r"\s*,\s*"
    return [part.strip() for part in re.split(separator_pattern, clean) if part.strip()]
def _claim_path_value(claims: Dict[str, Any], path: str) -> Any:
    current: Any = claims
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current
def _claim_value_as_strings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _split_claim_string(value)
    if isinstance(value, dict):
        for key in ("id", "tenant_id", "tenantId", "value", "name"):
            if value.get(key):
                return [str(value[key]).strip()]
        return []
    if isinstance(value, (list, tuple, set)):
        collected: List[Any] = []
        for item in value:
            collected.extend(_claim_value_as_strings(item))
        return _dedupe_nonblank_strings(collected)
    return [str(value).strip()]
def _identity_claim_strings(identity: OperatorIdentity, paths: List[str]) -> List[str]:
    values: List[Any] = []
    claims = identity.claims if isinstance(identity.claims, dict) else {}
    for path in paths:
        values.extend(_claim_value_as_strings(_claim_path_value(claims, path)))
    return _dedupe_nonblank_strings(values)
def _first_nonblank(*values: Any) -> Optional[str]:
    for value in values:
        clean = str(value or "").strip()
        if clean:
            return clean
    return None
def _env_csv(name: str) -> List[str]:
    return _dedupe_nonblank_strings(_split_claim_string(os.getenv(name, "")))
def _parse_rfc3339(value: Any) -> Optional[datetime]:
    """Best-effort RFC3339/ISO-8601 parse; None on empty or unparseable input.

    Mirrors read_store._parse_rfc3339 so callers in this module resolve a defined
    symbol. Returning None (rather than raising) keeps malformed optional time
    filters from surfacing as 500s — an unparseable bound is simply not applied.
    """
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
def _sem_session_id(identity: OperatorIdentity) -> str:
    claims = identity.claims if isinstance(identity.claims, dict) else {}
    return _first_nonblank(
        claims.get("sid"),
        claims.get("session_id"),
        claims.get("jti"),
        os.getenv("PANTHEON_SESSION_ID"),
        f"bff-session-{identity.operator_id}",
    )
def _bff_me_tenant_payload(
    identity: OperatorIdentity,
    *,
    requested_tenant: Optional[str],
) -> Dict[str, Any]:
    claim_default = _first_nonblank(
        *_identity_claim_strings(
            identity,
            [
                "tenant_id",
                "tenantId",
                "tenant.id",
                "tid",
                "org_id",
                "organization.id",
                "tenant_ids",
                "tenantIds",
            ],
        )
    )
    default_tenant = _first_nonblank(
        os.getenv("PANTHEON_BFF_TENANT_ID"),
        os.getenv("PANTHEON_BFF_DEFAULT_TENANT_ID"),
        os.getenv("PANTHEON_TENANT_ID"),
        claim_default,
        "pantheon-dev",
    )
    claim_allowed = _identity_claim_strings(
        identity,
        [
            "allowed_tenants",
            "allowedTenants",
            "tenant_ids",
            "tenantIds",
            "tenants",
            "tenant_id",
            "tenantId",
            "tenant.id",
            "tid",
            "org_id",
        ],
    )
    allowed_tenants = claim_allowed or _env_csv("PANTHEON_BFF_ALLOWED_TENANTS") or [default_tenant]
    effective_tenant = _first_nonblank(requested_tenant, default_tenant) or "pantheon-dev"
    if "*" not in allowed_tenants and effective_tenant not in allowed_tenants:
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Tenant access denied",
            "Requested tenant is outside the caller tenant scope",
            precondition_failed="tenant_scope",
            suggestion="Switch to an allowed tenant or request access from an administrator",
            details_extra={
                "tenantId": effective_tenant,
                "allowedTenantIds": allowed_tenants,
            },
        )
    return {
        "id": effective_tenant,
        "requested_id": str(requested_tenant or "").strip() or None,
        "default_id": default_tenant,
        "allowed_ids": allowed_tenants,
        "scope": "global" if "*" in allowed_tenants else "tenant",
    }
def _sem_session_key(identity: OperatorIdentity) -> str:
    return f"operator:{identity.operator_id}:session:{_sem_session_id(identity)}"
def _sem_legacy_operator_session_key(identity: OperatorIdentity) -> str:
    return f"operator:{identity.operator_id}"
def _sem_session_state(identity: OperatorIdentity) -> Dict[str, Any]:
    state = session_lifecycle_store.get_session(_sem_session_key(identity))
    if state:
        return state
    return session_lifecycle_store.get_session(_sem_legacy_operator_session_key(identity))
def _raise_if_session_logged_out(identity: OperatorIdentity) -> None:
    state = _sem_session_state(identity)
    if state.get("state") != "logged_out":
        return
    raise _bff_error(
        401,
        ErrorCode.AUTH_REQUIRED,
        "Session has been logged out",
        "SESSION_LOGGED_OUT",
        precondition_failed="session_state",
        suggestion="Re-authenticate before calling BFF session endpoints",
        details_extra={
            "sessionState": "logged_out",
            "loggedOutAt": state.get("logged_out_at"),
        },
    )
def _read_surface_state() -> str:
    return os.getenv("BFF_READ_SURFACE_STATE", "fresh")
def _meta_staleness() -> Optional[Dict[str, Any]]:
    state = _read_surface_state()
    if state == "fresh":
        return None
    return {
        "served_from": "cache",
        "last_known_at": utc_now(),
    }
def _surface_status() -> Dict[str, Any]:
    state = _read_surface_state()
    if state == "fresh":
        return {"status": "ok"}
    if state in {"degraded", "stale"}:
        return {
            "status": "degraded",
            "staleness": _meta_staleness(),
        }
    if state == "unavailable":
        return {
            "status": "unavailable",
            "staleness": _meta_staleness(),
        }
    return {"status": "ok"}
_LEGACY_LOOP_RUN_SOURCE = "legacy_incident_backfill"
_LOOP_RUN_PROJECTION_SCHEMA = "pantheon.loop-run-projection.v1"
def _loop_run_truth_source(available: bool) -> tuple[str, str]:
    """Resolve loop-run provenance without letting incidents shadow truth."""
    canonical_source = read_store.dataset_source("loop_runs")
    if canonical_source != "missing":
        return "loop_runs", canonical_source
    incident_source = read_store.dataset_source("incidents")
    if available and incident_source != "missing":
        return "incidents", _LEGACY_LOOP_RUN_SOURCE
    return "loop_runs", "missing"
def _loop_run_projection_metadata() -> Dict[str, Any]:
    getter = getattr(read_store, "loop_run_projection_metadata", None)
    if not callable(getter):
        return {}
    try:
        metadata = getter()
    except (OSError, TypeError, ValueError):
        return {}
    return dict(metadata) if isinstance(metadata, Mapping) else {}
def _loop_run_controller_is_formal(metadata: Mapping[str, Any]) -> bool:
    if str(metadata.get("schema_version") or "") != _LOOP_RUN_PROJECTION_SCHEMA:
        return False
    controller = metadata.get("controller")
    if not isinstance(controller, Mapping):
        return False
    return (
        controller.get("accepted_live") is True
        and str(controller.get("status") or "").strip().lower() == "ready"
        and str(controller.get("mode") or "").strip().lower() == "live"
        and str(controller.get("truth_level") or "").strip().lower() == "canonical_live"
    )
def _dataset_surface_status(
    dataset: str,
    *,
    snapshot_at: Optional[str] = None,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    surface = dict(_surface_status())
    source = source or read_store.dataset_source(dataset)
    surface["source"] = source

    if source == "local_snapshot":
        if surface.get("status") == "ok":
            surface["status"] = "degraded"
        surface["note"] = "Served from local BFF snapshot fallback instead of a backend-owned read store."
        surface["staleness"] = {
            "served_from": "local_snapshot",
            "last_known_at": snapshot_at or utc_now(),
        }
    elif source == _LEGACY_LOOP_RUN_SOURCE:
        surface["status"] = "degraded"
        surface["note"] = (
            "Incident-derived loop reconstruction is a legacy backfill view; "
            "it is not canonical lifecycle-projector or live controller truth."
        )
        surface["projection_mode"] = "backfill"
        surface["accepted_live"] = False
        surface["staleness"] = {
            "served_from": _LEGACY_LOOP_RUN_SOURCE,
            "last_known_at": snapshot_at or utc_now(),
        }
    elif source == "missing":
        surface["status"] = "unavailable"
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at or utc_now()},
        )

    if has_data is False:
        if surface.get("status") == "ok":
            surface["status"] = "unavailable"
        if missing_message:
            surface["message"] = missing_message
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at or utc_now()},
        )

    return surface
def _loop_run_surface_status(
    available: bool,
    *,
    snapshot_at: Optional[str] = None,
) -> tuple[str, str, Dict[str, Any]]:
    dataset, source = _loop_run_truth_source(available)
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        source=source,
    )
    if dataset != "loop_runs" or source == "missing":
        return dataset, source, surface

    metadata = _loop_run_projection_metadata()
    controller = metadata.get("controller")
    controller = dict(controller) if isinstance(controller, Mapping) else {}
    controller_formal = _loop_run_controller_is_formal(metadata)
    surface.update(
        {
            "projection_schema_version": metadata.get("schema_version"),
            "projection_generation": metadata.get("generation"),
            "controller": controller,
            "accepted_live": controller.get("accepted_live"),
            "projection_mode": controller.get("mode"),
            "truth_level": controller.get("truth_level"),
            "truth_status": "formal" if controller_formal and surface.get("status") == "ok" else "degraded",
        }
    )
    if not controller_formal or surface.get("status") != "ok":
        surface["status"] = "degraded"
        surface["controller_note"] = (
            "Canonical loop-run records remain conclusive, but formal truth requires "
            "accepted_live=true, status=ready, mode=live, and truth_level=canonical_live."
        )
        surface.setdefault(
            "staleness",
            {
                "served_from": source,
                "last_known_at": snapshot_at or utc_now(),
            },
        )
    return dataset, source, surface
def _dataset_source_after_read(dataset: str) -> str:
    """Return source provenance without repeating a completed backend read."""
    cached_source = getattr(read_store, "dataset_source_cached", None)
    if callable(cached_source):
        return str(cached_source(dataset) or "missing")
    return str(read_store.dataset_source(dataset) or "missing")
def _composed_dataset_surface_status(
    dataset: str,
    records: Sequence[Any],
    *,
    snapshot_at: str,
    source: str,
) -> Dict[str, Any]:
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        source=_dataset_source_after_read(dataset),
    )
    if records and surface.get("source") == "missing":
        return {
            "status": "ok",
            "source": source,
            "note": "Composed from governed market-persona read-model defaults.",
        }
    return surface
def _read_surface_meta(
    dataset: str,
    surface_key: str,
    *,
    snapshot_at: Optional[str] = None,
    total: Optional[int] = None,
    surface: Optional[Dict[str, Any]] = None,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
    degraded_reason: Optional[str] = None,
    unavailable_reason: Optional[str] = None,
) -> Dict[str, Any]:
    snapshot_at = snapshot_at or utc_now()
    surface = surface or _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
        missing_message=missing_message,
    )
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            surface_key: surface,
        },
    }
    if total is not None:
        meta["total"] = total
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness
    label = surface_key.replace("_", " ")
    reason = _surface_degradation_reason(
        surface,
        degraded_reason=degraded_reason or f"{label} is degraded and may be stale.",
        unavailable_reason=unavailable_reason or f"{label} is currently unavailable.",
    )
    if reason is not None:
        meta["degradation"] = {"reason": reason}
    return meta
def _raise_if_read_surface_unavailable(
    surface: Dict[str, Any],
    *,
    label: str,
) -> None:
    if surface.get("status") != "unavailable":
        return
    raise _bff_error(
        503,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        f"{label} read surface unavailable",
        str(surface.get("message") or surface.get("note") or f"{label} downstream read source is unavailable."),
        precondition_failed="read_surface_unavailable",
        suggestion="Verify the owning service URL and health before retrying this read.",
    )
def _composed_surface_status(
    *,
    snapshot_at: Optional[str] = None,
    available: bool = True,
    missing_message: Optional[str] = None,
) -> Dict[str, Any]:
    surface = dict(_surface_status())
    surface["source"] = "bff_composed"
    if not available:
        if surface.get("status") == "ok":
            surface["status"] = "degraded"
        if missing_message:
            surface["message"] = missing_message
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at or utc_now()},
        )

    return surface
def _performance_ranking_source_surface(
    surface: Dict[str, Any],
    *,
    snapshot_at: str,
) -> Dict[str, Any]:
    """Add the cross-center confidence vocabulary without changing global envelopes."""
    normalized = dict(surface)
    source = str(normalized.get("source") or "unknown")
    status = str(normalized.get("status") or "unavailable")
    normalized["observed_time"] = snapshot_at
    normalized["freshness"] = (
        normalized.get("staleness", {}).get("served_from")
        if isinstance(normalized.get("staleness"), dict)
        else None
    ) or source
    normalized["coverage"] = 0.0 if status == "unavailable" or source == "missing" else 1.0
    normalized["missing_bindings"] = status == "unavailable" or source == "missing"
    return normalized
def _extract_ids_from_item(item: Dict[str, Any], keys: List[str]) -> List[str]:
    extracted = []
    # 檢查 root 級別
    for key in keys:
        val = item.get(key)
        if val:
            if isinstance(val, list):
                extracted.extend([str(v).strip() for v in val if v])
            else:
                extracted.append(str(val).strip())
    # 檢查是否含有 id 欄位 (可能正是這個 entity 本身)
    if "id" in item:
        entity_id = str(item["id"]).strip()
        # 看看是否符合特定 prefix 格式，例如 pool-alpha、persona-xxx 等
        for key in keys:
            if key == "persona_id" and "persona" in entity_id:
                extracted.append(entity_id)
            elif key == "capital_pool_id" and "pool" in entity_id:
                extracted.append(entity_id)
    return list(set(extracted))
def _filter_by_common_identifiers(
    items: List[Dict[str, Any]],
    *,
    persona_id: Optional[str] = None,
    persona: Optional[str] = None,
    runtime_id: Optional[str] = None,
    runtime: Optional[str] = None,
    strategy_id: Optional[str] = None,
    strategy: Optional[str] = None,
    capital_pool_id: Optional[str] = None,
    pool: Optional[str] = None,
    sleeve_id: Optional[str] = None,
    sleeve: Optional[str] = None,
    artifact_id: Optional[str] = None,
    artifact: Optional[str] = None,
    broker_id: Optional[str] = None,
    broker: Optional[str] = None,
    stage: Optional[str] = None,
    period: Optional[str] = None,
    as_of: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # 合併 query 參數值
    persona_id = _resolve_param(persona_id)
    persona = _resolve_param(persona)
    runtime_id = _resolve_param(runtime_id)
    runtime = _resolve_param(runtime)
    strategy_id = _resolve_param(strategy_id)
    strategy = _resolve_param(strategy)
    capital_pool_id = _resolve_param(capital_pool_id)
    pool = _resolve_param(pool)
    sleeve_id = _resolve_param(sleeve_id)
    sleeve = _resolve_param(sleeve)
    artifact_id = _resolve_param(artifact_id)
    artifact = _resolve_param(artifact)
    broker_id = _resolve_param(broker_id)
    broker = _resolve_param(broker)
    stage = _resolve_param(stage)
    period = _resolve_param(period)
    as_of = _resolve_param(as_of)

    p_id = persona_id or persona
    r_id = runtime_id or runtime
    s_id = strategy_id or strategy
    cp_id = capital_pool_id or pool
    sl_id = sleeve_id or sleeve
    art_id = artifact_id or artifact
    bk_id = broker_id or broker

    filtered = []
    for item in items:
        # 取出該項目內可能包含的各種 ID
        item_persona_ids = _extract_ids_from_item(item, ["persona_id", "personaId", "persona_ids", "persona"])
        item_runtime_ids = _extract_ids_from_item(item, ["runtime_id", "runtimeId", "runtime_ids", "runtime"])
        item_strategy_ids = _extract_ids_from_item(item, ["strategy_id", "strategyId", "strategy_ids", "strategy"])
        item_pool_ids = _extract_ids_from_item(item, ["capital_pool_id", "capitalPoolId", "capital_pool_ids", "pool_id", "pool_ids", "pool"])
        item_sleeve_ids = _extract_ids_from_item(item, ["sleeve_id", "sleeveId", "sleeve_ids", "sleeve"])
        item_artifact_ids = _extract_ids_from_item(item, ["artifact_id", "artifactId", "artifact_ids", "artifact"])
        item_broker_ids = _extract_ids_from_item(item, ["broker_id", "brokerId", "broker_ids", "broker"])

        # 額外支援在 source_refs, target 或 links 中查找
        source_refs = item.get("source_refs") or {}
        if isinstance(source_refs, dict):
            if "persona_ids" in source_refs:
                item_persona_ids.extend(source_refs["persona_ids"])
            if "runtime_ids" in source_refs:
                item_runtime_ids.extend(source_refs["runtime_ids"])
            if "strategy_ids" in source_refs:
                item_strategy_ids.extend(source_refs["strategy_ids"])
            if "capital_pool_ids" in source_refs:
                item_pool_ids.extend(source_refs["capital_pool_ids"])

        target = item.get("target") or {}
        if isinstance(target, dict):
            t_type = target.get("type")
            t_id = target.get("id")
            if t_type == "persona" and t_id:
                item_persona_ids.append(t_id)

        # 進行匹配 (如果 filter parameter 有給，則 item 的 ID 必須符合)
        if p_id and not any(str(p_id).strip() == str(val).strip() for val in item_persona_ids):
            continue
        if r_id and not any(str(r_id).strip() == str(val).strip() for val in item_runtime_ids):
            continue
        if s_id and not any(str(s_id).strip() == str(val).strip() for val in item_strategy_ids):
            continue
        if cp_id and not any(str(cp_id).strip() == str(val).strip() for val in item_pool_ids):
            continue
        if sl_id and not any(str(sl_id).strip() == str(val).strip() for val in item_sleeve_ids):
            continue
        if art_id and not any(str(art_id).strip() == str(val).strip() for val in item_artifact_ids):
            continue
        if bk_id and not any(str(bk_id).strip() == str(val).strip() for val in item_broker_ids):
            continue

        # stage, period, as_of 匹配
        item_stage = item.get("stage") or item.get("lifecycle_state") or item.get("status")
        if stage and str(item_stage).strip().lower() != str(stage).strip().lower():
            continue

        item_period = item.get("period")
        if period and str(item_period).strip().lower() != str(period).strip().lower():
            continue

        # as_of 可以檢查 meta 或是 item_as_of
        item_as_of = item.get("as_of") or item.get("observed_at") or item.get("collected_at")
        if as_of and str(item_as_of).strip() != str(as_of).strip():
            continue

        filtered.append(item)
    return filtered
_INCIDENT_SEVERITY_MAP = {
    "critical": "sev1",
    "high": "sev1",
    "medium": "sev2",
    "low": "sev3",
    "sev1": "sev1",
    "sev2": "sev2",
    "sev3": "sev3",
}
def _incident_home_severity(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _INCIDENT_SEVERITY_MAP.get(str(value).strip().lower(), str(value))
def _decode_page_token(page_token: Optional[str]) -> int:
    if page_token in (None, ""):
        return 0
    try:
        offset = int(page_token)
    except (TypeError, ValueError) as exc:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid page_token",
            "page_token must be a non-negative integer offset",
        ) from exc
    if offset < 0:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid page_token",
            "page_token must be a non-negative integer offset",
        )
    return offset
def _page_slice(items: List[Dict[str, Any]], page_token: Optional[str], page_size: int) -> tuple[List[Dict[str, Any]], Optional[str]]:
    start = _decode_page_token(page_token)
    end = start + page_size
    next_page_token = str(end) if end < len(items) else None
    return items[start:end], next_page_token
_ALERT_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_ALERT_CATEGORY_ORDER = {"incident": 4, "kill_switch": 3, "governance": 2, "runtime": 1}
_RUNTIME_STATUS_ALERT_SEVERITY = {
    "failed": "critical",
    "error": "critical",
    "degraded": "high",
    "paused": "medium",
}
_TELEMETRY_DRAWDOWN_THRESHOLDS = (
    (0.10, "critical"),
    (0.05, "high"),
)
_TELEMETRY_FILL_RATE_THRESHOLDS = (
    (0.90, "critical"),
    (0.95, "high"),
)
_TELEMETRY_SLIPPAGE_THRESHOLDS = (
    (4.0, "critical"),
    (3.0, "high"),
)
def _split_csv_query(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    tokens = [token.strip() for token in value.split(",") if token.strip()]
    return tokens or None
def _project_runtime_state_telemetry_summary(summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not summary:
        return None
    projected = {
        "window": summary.get("window"),
        "collected_at": summary.get("collected_at"),
        "metrics": {
            "pnl": summary.get("pnl"),
            "drawdown": summary.get("drawdown"),
            "sharpe_ratio": summary.get("sharpe_ratio"),
            "fill_rate": summary.get("fill_rate"),
            "avg_slippage_bps": summary.get("avg_slippage_bps"),
            "total_trades": summary.get("total_trades"),
        },
    }
    for key in (
        "runtime_binding_id",
        "binding_id",
        "deployment_stage",
        "state",
        "last_heartbeat_at",
        "last_event_at",
        "last_event_type",
        "engine_bridge_repo",
        "engine_bridge_commit",
        "engine_bridge_path",
        "runtime_adapter_version",
        "health_summary",
        "projection_source",
        "projection_updated_at",
        "staleness",
        "executed_trade_count",
        "position_count",
        "positions",
        "last_fill",
    ):
        if key in summary:
            projected[key] = summary.get(key)
    return projected
def _project_runtime_state_monitoring_session(session: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not session:
        return None
    projected: Dict[str, Any] = {}
    for key in (
        "session_id",
        "session_type",
        "binding_id",
        "runtime_binding_id",
        "runtime_id",
        "deployment_stage",
        "status",
        "active",
        "started_at",
        "ended_at",
        "ended_reason",
        "terminal_reason",
        "last_heartbeat_at",
        "heartbeat_status",
        "stale_after_seconds",
        "restart_count",
        "staleness",
        "last_error",
    ):
        if key in session:
            projected[key] = session.get(key)
    terminal_reason = _runtime_state_monitoring_terminal_reason(session)
    if terminal_reason and "terminal_reason" not in projected:
        projected["terminal_reason"] = terminal_reason
    return projected
def _project_runtime_state_latest_rollback(rollbacks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rollbacks:
        return None
    latest = max(
        rollbacks,
        key=lambda rollback: (
            rollback.get("completed_at")
            or rollback.get("executed_at")
            or rollback.get("initiated_at")
            or ""
        ),
    )
    return {
        "rollback_id": latest.get("rollback_id") or latest.get("id"),
        "action_type": latest.get("action_type"),
        "status": latest.get("status"),
        "from_version": latest.get("from_version"),
        "to_version": latest.get("to_version"),
        "initiated_at": latest.get("initiated_at"),
        "completed_at": latest.get("completed_at") or latest.get("executed_at"),
    }
def _runtime_state_row_health_check(
    status: str,
    *,
    source: str,
    message: Optional[str] = None,
    applies: bool = True,
) -> Dict[str, Any]:
    check: Dict[str, Any] = {
        "status": status,
        "source": source,
        "applies": applies,
    }
    if message:
        check["message"] = message
    return check
def _runtime_state_monitoring_terminal_reason(
    session: Optional[Dict[str, Any]],
) -> Optional[str]:
    if not session:
        return None
    for key in ("terminal_reason", "ended_reason"):
        value = str(session.get(key) or "").strip()
        if value:
            return value
    staleness = session.get("staleness")
    if isinstance(staleness, dict):
        reason = str(staleness.get("reason") or "").strip()
        if reason:
            return reason
        status = str(staleness.get("status") or "").strip().lower()
        if status == "stale":
            return "stale_monitoring_session"
    status = str(session.get("status") or "").strip().lower()
    if status in {"ended", "stale", "failed"}:
        return status
    return None
def _runtime_state_monitoring_health_check(
    monitoring_session: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if monitoring_session is None:
        return _runtime_state_row_health_check(
            "unavailable",
            source="paper_runtime_monitoring_sessions",
            message="Paper runtime monitoring session is unavailable for this runtime.",
        )
    terminal_reason = _runtime_state_monitoring_terminal_reason(monitoring_session)
    inactive = monitoring_session.get("active") is False
    ended = monitoring_session.get("ended_at") not in (None, "")
    if terminal_reason or inactive or ended:
        reason = terminal_reason or "inactive_monitoring_session"
        return _runtime_state_row_health_check(
            "degraded",
            source="paper_runtime_monitoring_sessions",
            message=f"Paper runtime monitoring session is terminal: {reason}.",
        )
    return _runtime_state_row_health_check(
        "ok",
        source="paper_runtime_monitoring_sessions",
    )
def _derive_runtime_state_row_health(
    *,
    binding: Dict[str, Any],
    telemetry_summary: Optional[Dict[str, Any]],
    monitoring_session: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    deployment_stage = str(
        binding.get("deployment_stage") or binding.get("deployment_mode") or ""
    ).lower()
    checks: Dict[str, Dict[str, Any]] = {
        "runtime_binding": _runtime_state_row_health_check(
            "ok",
            source="runtime_bindings",
        ),
        "telemetry_summary": (
            _runtime_state_row_health_check("ok", source="telemetry_summaries")
            if telemetry_summary is not None
            else _runtime_state_row_health_check(
                "unavailable",
                source="telemetry_summaries",
                message="Telemetry summary row is unavailable for this runtime.",
            )
        ),
    }
    if deployment_stage == "paper":
        checks["paper_runtime_monitoring"] = _runtime_state_monitoring_health_check(
            monitoring_session
        )
    else:
        checks["paper_runtime_monitoring"] = _runtime_state_row_health_check(
            "ok",
            source="not_applicable",
            applies=False,
            message="Paper runtime monitoring applies only to paper runtimes.",
        )

    degraded_checks = [
        key
        for key, check in checks.items()
        if check.get("applies", True) and check.get("status") != "ok"
    ]
    return {
        "status": "degraded" if degraded_checks else "ok",
        "checks": checks,
        "degraded_checks": degraded_checks,
    }
def _derive_runtime_state_last_updated_at(
    binding: Dict[str, Any],
    telemetry_summary: Optional[Dict[str, Any]],
    latest_rollback: Optional[Dict[str, Any]],
    monitoring_session: Optional[Dict[str, Any]],
) -> Optional[str]:
    candidates = [
        binding.get("last_updated_at"),
        binding.get("updated_at"),
        binding.get("started_at"),
        binding.get("created_at"),
        (telemetry_summary or {}).get("last_heartbeat_at"),
        (telemetry_summary or {}).get("last_event_at"),
        (telemetry_summary or {}).get("collected_at"),
        (latest_rollback or {}).get("completed_at"),
        (latest_rollback or {}).get("initiated_at"),
        (monitoring_session or {}).get("last_heartbeat_at"),
        (monitoring_session or {}).get("ended_at"),
        (monitoring_session or {}).get("started_at"),
    ]
    values = [candidate for candidate in candidates if candidate]
    if not values:
        return None
    return max(values)
def _project_operator_runtime_state_row(
    binding: Dict[str, Any],
    *,
    telemetry_summary_record: Optional[Dict[str, Any]] = None,
    monitoring_session_record: Optional[Dict[str, Any]] = None,
    prefetched: bool = False,
) -> Dict[str, Any]:
    runtime_id = str(binding.get("runtime_id") or binding.get("id") or "")
    runtime_binding_id = (
        binding.get("runtime_binding_id")
        or binding.get("binding_id")
        or binding.get("id")
    )
    raw_telemetry_summary = (
        telemetry_summary_record
        if prefetched
        else read_store.get_telemetry_summary(runtime_id)
    )
    telemetry_summary = _project_runtime_state_telemetry_summary(
        raw_telemetry_summary
    )
    raw_monitoring_session = (
        monitoring_session_record
        if prefetched
        else read_store.get_paper_runtime_monitoring_session(
            runtime_id=runtime_id,
            binding_id=str(runtime_binding_id or ""),
        )
    )
    monitoring_session = _project_runtime_state_monitoring_session(
        raw_monitoring_session
    )
    rollbacks = read_store.get_rollbacks(runtime_id)
    latest_rollback = _project_runtime_state_latest_rollback(rollbacks)
    artifact_id = binding.get("artifact_id")
    artifact_version = binding.get("artifact_version") or binding.get("version")
    plan_id = binding.get("plan_id")

    return {
        "runtime_id": runtime_id,
        "runtime_binding_id": runtime_binding_id,
        "deployment_stage": binding.get("deployment_stage") or binding.get("deployment_mode"),
        "status": binding.get("status"),
        "capital_pool_id": binding.get("capital_pool_id"),
        "plan_ref": (
            {
                "plan_id": plan_id,
                "href": _deployment_review_href(str(plan_id)),
            }
            if plan_id
            else None
        ),
        "artifact_ref": (
            {
                "artifact_id": artifact_id,
                "artifact_version": artifact_version,
            }
            if artifact_id or artifact_version
            else None
        ),
        "telemetry_summary": telemetry_summary,
        "executed_trade_count": (telemetry_summary or {}).get("executed_trade_count"),
        "total_trades": ((telemetry_summary or {}).get("metrics") or {}).get("total_trades"),
        "position_count": (telemetry_summary or {}).get("position_count"),
        "positions": (telemetry_summary or {}).get("positions"),
        "last_fill": (telemetry_summary or {}).get("last_fill"),
        "paper_runtime_monitoring": monitoring_session,
        "row_health": _derive_runtime_state_row_health(
            binding=binding,
            telemetry_summary=telemetry_summary,
            monitoring_session=monitoring_session,
        ),
        "rollback_summary": {
            "count": len(rollbacks),
            "latest": latest_rollback,
            "href": f"/api/v1/runtimes/{runtime_id}/rollbacks",
        },
        "last_updated_at": _derive_runtime_state_last_updated_at(
            binding,
            telemetry_summary,
            latest_rollback,
            monitoring_session,
        ),
    }
def _highest_ranked_value(
    values: List[Optional[str]],
    order: Dict[str, int],
) -> Optional[str]:
    best_value: Optional[str] = None
    best_rank = -1
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip().lower()
        rank = order.get(normalized)
        if rank is None:
            continue
        if rank > best_rank:
            best_rank = rank
            best_value = normalized
    return best_value
def _aggregate_group_surface(
    surface_key: str,
    source_surfaces: List[Dict[str, Any]],
    *,
    snapshot_at: str,
    unavailable_message: str,
    degraded_message: str,
) -> Dict[str, Any]:
    surface = _composed_surface_status(snapshot_at=snapshot_at, available=True)
    surface["source"] = "bff_composed"
    statuses = [entry.get("status", "ok") for entry in source_surfaces]
    if statuses and all(status == "ok" for status in statuses):
        return surface
    if statuses and all(status == "unavailable" for status in statuses):
        surface["status"] = "unavailable"
        surface["message"] = unavailable_message
        return surface
    surface["status"] = "degraded"
    surface["message"] = degraded_message
    return surface
def _alert_target_ref(
    *,
    surface_id: str,
    label: str,
    href: str,
    target_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "surface_id": surface_id,
        "label": label,
        "href": href,
    }
    if target_id not in (None, ""):
        payload["target_id"] = target_id
    return payload
def _max_alert_severity(values: List[Optional[str]]) -> Optional[str]:
    return _highest_ranked_value(values, _ALERT_SEVERITY_ORDER)
def _alert_sort_key(alert: Dict[str, Any]) -> tuple[str, int, int, str]:
    severity = str(alert.get("severity") or "").lower()
    category = str(alert.get("category") or "").lower()
    return (
        str(alert.get("raised_at") or ""),
        _ALERT_SEVERITY_ORDER.get(severity, 0),
        _ALERT_CATEGORY_ORDER.get(category, 0),
        str(alert.get("alert_id") or ""),
    )
def _alert_severity_for_incident(incident: Dict[str, Any]) -> str:
    normalized = _incident_home_severity(incident.get("severity"))
    if normalized == "sev1":
        return "critical"
    if normalized == "sev2":
        return "high"
    return "medium"
def _alert_severity_for_risk_level(
    risk_level: Optional[str],
    *,
    elevated: bool = False,
) -> str:
    mapping = {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "low": "low",
    }
    severity = mapping.get(str(risk_level or "").strip().lower(), "medium")
    if elevated and _ALERT_SEVERITY_ORDER.get(severity, 0) < _ALERT_SEVERITY_ORDER["high"]:
        return "high"
    return severity
def _build_incident_alerts(snapshot_at: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    incident_surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    if incident_surface.get("status") == "unavailable":
        return [], incident_surface

    alerts: List[Dict[str, Any]] = []
    incidents = read_store.list_incidents()
    for incident in incidents:
        incident_status = str(incident.get("status") or "").lower()
        if incident_status not in {"open", "in_progress"}:
            continue
        incident_id = str(incident.get("incident_id") or "")
        severity = _alert_severity_for_incident(incident)
        title = str(incident.get("title") or incident_id or "Unnamed incident")
        status_prefix = "Active" if incident_status == "open" else "In-progress"
        alerts.append(
            {
                "alert_id": f"alert-incident-{incident_id}",
                "severity": severity,
                "category": "incident",
                "raised_at": incident.get("opened_at") or incident.get("created_at") or snapshot_at,
                "summary": f"{status_prefix} incident: {title}.",
                "target_ref": _alert_target_ref(
                    surface_id="PKT-002",
                    label="Open incident response",
                    href=_incident_detail_href(incident_id),
                    target_id=incident_id,
                ),
            }
        )
    return alerts, incident_surface
def _build_governance_alerts(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    review_queue_surface = _dataset_surface_status(
        "governance_review_queue_items",
        snapshot_at=snapshot_at,
    )
    approval_queue_surface = _dataset_surface_status(
        "approval_queue_items",
        snapshot_at=snapshot_at,
    )
    alerts: List[Dict[str, Any]] = []

    if review_queue_surface.get("status") != "unavailable":
        for item in read_store.list_governance_review_queue_items():
            item_id = str(item.get("item_id") or "")
            status = str(item.get("status") or "").lower()
            if status not in {"pending", "in_review", "escalated"}:
                continue
            severity = _alert_severity_for_risk_level(
                item.get("risk_level"),
                elevated=status == "escalated",
            )
            item_type = str(item.get("item_type") or "Governance item")
            if status == "escalated":
                summary = f"Escalated governance review: {item_type} {item_id}."
            elif status == "in_review":
                summary = f"Governance review in progress: {item_type} {item_id}."
            else:
                summary = f"Pending governance review: {item_type} {item_id}."
            alerts.append(
                {
                    "alert_id": f"alert-governance-review-{item_id}",
                    "severity": severity,
                    "category": "governance",
                    "raised_at": item.get("submitted_at") or snapshot_at,
                    "summary": summary,
                    "target_ref": _alert_target_ref(
                        surface_id="PKT-001",
                        label="Open governance review queue",
                        href=_GOVERNANCE_REVIEW_QUEUE_ROUTE,
                        target_id=item_id,
                    ),
                }
            )

    if approval_queue_surface.get("status") != "unavailable":
        for item in read_store.list_approval_queue_items():
            decision_id = str(item.get("decision_id") or "")
            decision_state = str(item.get("decision_state") or "").lower()
            if decision_state not in {"pending", "in_review"}:
                continue
            severity = _alert_severity_for_risk_level(
                item.get("risk_level"),
                elevated=decision_state == "in_review",
            )
            decision_type = str(item.get("decision_type") or "Approval item")
            if decision_state == "in_review":
                summary = f"Approval decision in review: {decision_type} {decision_id}."
            else:
                summary = f"Approval required: {decision_type} {decision_id}."
            alerts.append(
                {
                    "alert_id": f"alert-approval-{decision_id}",
                    "severity": severity,
                    "category": "governance",
                    "raised_at": item.get("submitted_at") or snapshot_at,
                    "summary": summary,
                    "target_ref": _alert_target_ref(
                        surface_id="GV-02",
                        label="Open approval queue",
                        href=_GOVERNANCE_APPROVAL_QUEUE_ROUTE,
                        target_id=decision_id,
                    ),
                }
            )

    return alerts, {
        "review_queue": review_queue_surface,
        "approval_queue": approval_queue_surface,
    }
def _build_kill_switch_alerts(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    kill_switch_surface = _dataset_surface_status("kill_switch", snapshot_at=snapshot_at)
    if kill_switch_surface.get("status") == "unavailable":
        return [], kill_switch_surface, {}

    kill_switch = read_store.get_kill_switch_status()
    safe_mode_status = str(kill_switch.get("safe_mode_status") or "").lower()
    kill_switch_status = str(kill_switch.get("status") or "").lower()
    safe_mode_active = safe_mode_status not in {"", "off", "released", "none", "null"}
    alerts: List[Dict[str, Any]] = []

    if kill_switch.get("active") or kill_switch_status == "triggered":
        severity = "critical"
        summary = "Kill-switch active; operator intervention is required."
    elif kill_switch_status == "cooling_down":
        severity = "high"
        summary = "Kill-switch cooling down; verify runtime stability before resuming operations."
    elif safe_mode_active:
        severity = "high"
        summary = f"Safe mode active ({safe_mode_status}); use the health board to verify current restrictions."
    else:
        return [], kill_switch_surface, kill_switch

    alerts.append(
        {
            "alert_id": "alert-kill-switch-state",
            "severity": severity,
            "category": "kill_switch",
            "raised_at": kill_switch.get("last_triggered_at")
            or kill_switch.get("last_confirmed_at")
            or snapshot_at,
            "summary": summary,
            "target_ref": _alert_target_ref(
                surface_id="OC-03",
                label="Open health status board",
                href=_OPERATOR_HEALTH_STATUS_ROUTE,
                target_id=kill_switch_status or safe_mode_status or "kill-switch",
            ),
        }
    )
    return alerts, kill_switch_surface, kill_switch
def _runtime_anomaly_reasons(
    binding: Dict[str, Any],
    telemetry_summary: Optional[Dict[str, Any]],
) -> tuple[List[str], Optional[str]]:
    reasons: List[str] = []
    severities: List[Optional[str]] = []

    runtime_status = str(binding.get("status") or "").lower()
    runtime_status_severity = _RUNTIME_STATUS_ALERT_SEVERITY.get(runtime_status)
    if runtime_status_severity:
        severities.append(runtime_status_severity)
        reasons.append(f"runtime status is {runtime_status}")

    if telemetry_summary:
        drawdown = telemetry_summary.get("drawdown")
        if isinstance(drawdown, (int, float)):
            for threshold, severity in _TELEMETRY_DRAWDOWN_THRESHOLDS:
                if drawdown >= threshold:
                    severities.append(severity)
                    reasons.append(f"drawdown is {drawdown:.3f}")
                    break

        fill_rate = telemetry_summary.get("fill_rate")
        if isinstance(fill_rate, (int, float)):
            for threshold, severity in _TELEMETRY_FILL_RATE_THRESHOLDS:
                if fill_rate < threshold:
                    severities.append(severity)
                    reasons.append(f"fill rate dropped to {fill_rate:.2f}")
                    break

        avg_slippage_bps = telemetry_summary.get("avg_slippage_bps")
        if isinstance(avg_slippage_bps, (int, float)):
            for threshold, severity in _TELEMETRY_SLIPPAGE_THRESHOLDS:
                if avg_slippage_bps >= threshold:
                    severities.append(severity)
                    reasons.append(f"average slippage reached {avg_slippage_bps:.1f} bps")
                    break

    return reasons, _max_alert_severity(severities)
def _build_runtime_alerts(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    runtime_roster_surface = _dataset_surface_status(
        "runtime_bindings",
        snapshot_at=snapshot_at,
    )
    telemetry_surface = _dataset_surface_status(
        "telemetry_summaries",
        snapshot_at=snapshot_at,
    )
    if runtime_roster_surface.get("status") == "unavailable":
        return [], {
            "runtime_roster": runtime_roster_surface,
            "telemetry_summary": telemetry_surface,
        }

    alerts: List[Dict[str, Any]] = []
    bindings = read_store.list_runtime_bindings()
    missing_telemetry = False
    for binding in bindings:
        runtime_id = str(binding.get("runtime_id") or binding.get("id") or "")
        telemetry_summary = None
        if runtime_id and telemetry_surface.get("status") != "unavailable":
            telemetry_summary = read_store.get_telemetry_summary(runtime_id)
            if telemetry_summary is None:
                missing_telemetry = True
        reasons, severity = _runtime_anomaly_reasons(binding, telemetry_summary)
        if not reasons or not severity:
            continue
        alerts.append(
            {
                "alert_id": f"alert-runtime-{runtime_id}",
                "severity": severity,
                "category": "runtime",
                "raised_at": (telemetry_summary or {}).get("collected_at")
                or binding.get("updated_at")
                or binding.get("last_updated_at")
                or binding.get("started_at")
                or snapshot_at,
                "summary": f"Runtime {runtime_id} anomaly: {'; '.join(reasons[:2])}.",
                "target_ref": _alert_target_ref(
                    surface_id="OC-04",
                    label="Open runtime state board",
                    href=_OPERATOR_RUNTIME_STATE_ROUTE,
                    target_id=runtime_id,
                ),
            }
        )

    if bindings and missing_telemetry and telemetry_surface.get("status") == "ok":
        telemetry_surface["status"] = "degraded"
        telemetry_surface["message"] = "Telemetry summary missing for one or more runtimes."
        telemetry_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )

    return alerts, {
        "runtime_roster": runtime_roster_surface,
        "telemetry_summary": telemetry_surface,
    }
def _build_alert_summary(alerts: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_severity = {key: 0 for key in _ALERT_SEVERITY_ORDER}
    by_category = {key: 0 for key in _ALERT_CATEGORY_ORDER}
    for alert in alerts:
        severity = str(alert.get("severity") or "").lower()
        category = str(alert.get("category") or "").lower()
        if severity in by_severity:
            by_severity[severity] += 1
        if category in by_category:
            by_category[category] += 1
    return {
        "total_active": len(alerts),
        "highest_severity": _max_alert_severity(
            [str(alert.get("severity") or "").lower() for alert in alerts]
        ),
        "by_severity": by_severity,
        "by_category": by_category,
    }
def _build_operator_alerts_payload(snapshot_at: str) -> Dict[str, Any]:
    incident_alerts, incident_surface = _build_incident_alerts(snapshot_at)
    governance_alerts, governance_surfaces = _build_governance_alerts(snapshot_at)
    kill_switch_alerts, kill_switch_surface, _ = _build_kill_switch_alerts(snapshot_at)
    runtime_alerts, runtime_surfaces = _build_runtime_alerts(snapshot_at)

    source_surfaces = [
        incident_surface,
        governance_surfaces["review_queue"],
        governance_surfaces["approval_queue"],
        kill_switch_surface,
        runtime_surfaces["runtime_roster"],
        runtime_surfaces["telemetry_summary"],
    ]
    alerts_surface = _aggregate_group_surface(
        "alerts",
        source_surfaces,
        snapshot_at=snapshot_at,
        unavailable_message="Operator alert feed unavailable.",
        degraded_message="Operator alert feed is available, but one or more contributing surfaces are degraded.",
    )

    alerts = sorted(
        incident_alerts + governance_alerts + kill_switch_alerts + runtime_alerts,
        key=_alert_sort_key,
        reverse=True,
    )
    alerts = [
        a for a in alerts
        if str(a.get("alert_id") or a.get("id") or "") not in _ACKNOWLEDGED_ALERTS
    ]
    if alerts_surface.get("status") == "unavailable":
        alerts = []

    meta = _snapshot_meta(snapshot_at)
    meta["acknowledgement_supported"] = True
    meta["surfaces"] = {
        "alerts": alerts_surface,
        "incident_feed": incident_surface,
        "review_queue": governance_surfaces["review_queue"],
        "approval_queue": governance_surfaces["approval_queue"],
        "kill_switch": kill_switch_surface,
        "runtime_roster": runtime_surfaces["runtime_roster"],
        "telemetry_summary": runtime_surfaces["telemetry_summary"],
    }
    return {
        "alerts": alerts,
        "summary": _build_alert_summary(alerts),
        "meta": meta,
    }
def _management_record_time(record: Dict[str, Any]) -> str:
    for field in (
        "updated_at",
        "updatedAt",
        "created_at",
        "createdAt",
        "submitted_at",
        "triggered_at",
        "raised_at",
        "collected_at",
        "last_updated_at",
    ):
        value = record.get(field)
        if value not in (None, ""):
            return str(value)
    return str(record.get("id") or "")
def _management_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
def _management_avg(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None
def _management_count_by(records: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        value = str(record.get(field) or "unknown").strip() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts
def _management_json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value))
_MANAGEMENT_CAMEL_KEY_RE = re.compile(r"[A-Z]")
def _management_camel_to_snake_key(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value.lower()
def _management_prune_camel_aliases(value: Any) -> Any:
    """Keep snake_case when a dict carries both snake_case and camelCase aliases."""
    if isinstance(value, list):
        return [_management_prune_camel_aliases(item) for item in value]
    if not isinstance(value, dict):
        return value
    keys = {key for key in value if isinstance(key, str)}
    pruned: Dict[str, Any] = {}
    for key, nested in value.items():
        if isinstance(key, str) and _MANAGEMENT_CAMEL_KEY_RE.search(key):
            snake_key = _management_camel_to_snake_key(key)
            if snake_key in keys:
                continue
        pruned[key] = _management_prune_camel_aliases(nested)
    return pruned
_MANAGEMENT_RISK_LEVEL_ORDER = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}
def _build_management_anomalies_payload(snapshot_at: str) -> Dict[str, Any]:
    runtime_alerts, runtime_surfaces = _build_runtime_alerts(snapshot_at)
    sentinel_available, sentinel_findings = read_store.list_sentinel_findings()
    sentinel_anomalies: List[Dict[str, Any]] = []
    for finding in sentinel_findings:
        finding_id = str(finding.get("id") or finding.get("finding_id") or "").strip()
        if not finding_id:
            continue
        sentinel_anomalies.append(
            {
                "id": finding_id,
                "kind": finding.get("kind") or "sentinel_finding",
                "severity": finding.get("severity") or finding.get("risk_level") or "medium",
                "status": finding.get("status"),
                "summary": finding.get("title") or finding.get("summary") or finding_id,
                "created_at": finding.get("created_at"),
                "triggered_at": finding.get("triggered_at"),
                "target_ref": {
                    "label": "Open sentinel finding",
                    "href": f"/management/sentinel?finding={finding_id}",
                    "target_id": finding_id,
                },
            }
        )
    runtime_anomalies = [
        {
            "id": alert.get("alert_id"),
            "kind": "runtime_alert",
            "severity": alert.get("severity"),
            "status": "active",
            "summary": alert.get("summary"),
            "raised_at": alert.get("raised_at"),
            "target_ref": alert.get("target_ref"),
        }
        for alert in runtime_alerts
    ]
    anomalies = sorted(
        runtime_anomalies + sentinel_anomalies,
        key=_management_record_time,
        reverse=True,
    )
    incident_source = read_store.dataset_source("incidents")
    sentinel_dataset = "incidents" if incident_source != "missing" else "sentinel_findings"
    sentinel_surface = _dataset_surface_status(
        sentinel_dataset,
        snapshot_at=snapshot_at,
        source=None if sentinel_available else "missing",
    )
    anomalies_surface = _aggregate_group_surface(
        "management_anomalies",
        [
            runtime_surfaces["runtime_roster"],
            runtime_surfaces["telemetry_summary"],
            sentinel_surface,
        ],
        snapshot_at=snapshot_at,
        unavailable_message="Anomaly aggregate unavailable.",
        degraded_message="Anomaly aggregate is available, but runtime telemetry or sentinel coverage is degraded.",
    )
    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "management_anomalies": anomalies_surface,
        "runtime_roster": runtime_surfaces["runtime_roster"],
        "telemetry_summary": runtime_surfaces["telemetry_summary"],
        "sentinel_findings": sentinel_surface,
    }
    return {
        "items": anomalies,
        "summary": {
            "total": len(anomalies),
            "by_severity": _management_count_by(anomalies, "severity"),
            "by_kind": _management_count_by(anomalies, "kind"),
            "highest_severity": _highest_ranked_value(
                [str(item.get("severity") or "") for item in anomalies],
                _ALERT_SEVERITY_ORDER,
            ),
        },
        "meta": meta,
    }
_READINESS_EP5_EVIDENCE_REFS = [
    ("support/evidence/EP5-001-V2/closeout.md", "PromotionReadinessPacket schema closeout"),
    ("support/evidence/EP5-002-V2/owner-closeout.md", "Promotion readiness validator closeout"),
    ("support/evidence/EP5-003-V2/owner-closeout.md", "Human gate signoff closeout"),
    ("support/evidence/EP5-006-V2/owner-closeout.md", "EP5 dry-run API closeout"),
    ("support/evidence/EP5-007-V2/rollback-drill.json", "Rollback drill evidence"),
    ("support/evidence/EP5-008-V2/kill-switch-demo.json", "Kill-switch demo evidence"),
    (
        "docs/deployment/evidence/ep5-broker-tw-002/20260517T054748Z/evidence-packet/shioaji-sandbox-evidence-packet.json",
        "Broker sandbox evidence packet",
    ),
]
_READINESS_STRICT_PUBLISH_AUDIT = "support/evidence/lsp-final-audit/strict-publish-audit.json"
_READINESS_STRICT_PUBLISH_REPORT = "support/evidence/lsp-final-audit/strict-publish-audit.md"
_READINESS_BFF_HA_PACKET = "support/evidence/bff-ha-failover-demo/README.md"
_READINESS_BFF_HA_REVIEW = "support/evidence/bff-ha-failover-demo/review-ha-010-v2.md"
_READINESS_NO_REAL_CAPITAL_EVIDENCE = "support/evidence/MGMT-BROKER-003/no-real-capital-evidence.json"
_READINESS_BROKER_LIVE_DISABLED = (
    "docs/deployment/evidence/ep5-broker-tw-002/20260517T054748Z/sandbox-smoke/live-disabled.json"
)
def _repo_artifact_path(rel_path: str) -> str:
    return os.path.join(_REPO_ROOT, rel_path)
def _read_repo_json_artifact(rel_path: str) -> Optional[Dict[str, Any]]:
    path = _repo_artifact_path(rel_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
def _read_repo_text_artifact(rel_path: str) -> str:
    path = _repo_artifact_path(rel_path)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""
def _readiness_evidence_ref(rel_path: str, label: str) -> Dict[str, Any]:
    exists = os.path.exists(_repo_artifact_path(rel_path))
    return {
        "id": re.sub(r"[^a-z0-9]+", "-", rel_path.lower()).strip("-"),
        "label": label,
        "path": rel_path,
        "href": f"/{rel_path}",
        "exists": exists,
    }
def _readiness_artifact_surface(
    surface_key: str,
    rel_path: str,
    *,
    snapshot_at: str,
    label: str,
) -> Dict[str, Any]:
    exists = os.path.exists(_repo_artifact_path(rel_path))
    surface = dict(_surface_status())
    surface["source"] = "repo_artifact" if exists else "missing"
    surface["artifact_path"] = rel_path
    if not exists:
        surface["status"] = "unavailable"
        surface["message"] = f"{label} artifact is unavailable."
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )
    return surface
def _readiness_check(
    check_id: str,
    label: str,
    status: str,
    *,
    blocking: bool,
    message: str,
    evidence_refs: Optional[List[str]] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": check_id,
        "label": label,
        "status": status,
        "blocking": blocking,
        "message": message,
    }
    if evidence_refs:
        payload["evidence_refs"] = evidence_refs
    if details:
        payload["details"] = details
    return payload
def _readiness_summary(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_status = _management_count_by(checks, "status")
    blocking_reasons = [
        str(check.get("id"))
        for check in checks
        if bool(check.get("blocking")) and str(check.get("status") or "") != "pass"
    ]
    can_proceed = not blocking_reasons
    readiness_status = "ready" if can_proceed else "blocked"
    return {
        "readinessStatus": readiness_status,
        "readiness_status": readiness_status,
        "canProceed": can_proceed,
        "can_proceed": can_proceed,
        "checkCount": len(checks),
        "check_count": len(checks),
        "passedCheckCount": by_status.get("pass", 0),
        "passed_check_count": by_status.get("pass", 0),
        "blockingReasonCount": len(blocking_reasons),
        "blocking_reason_count": len(blocking_reasons),
        "blockingReasons": blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "byStatus": by_status,
        "by_status": by_status,
    }
def _readiness_response(
    *,
    readiness_id: str,
    title: str,
    checks: List[Dict[str, Any]],
    evidence_refs: List[Dict[str, Any]],
    source_surfaces: Dict[str, Dict[str, Any]],
    snapshot_at: str,
    details: Optional[Dict[str, Any]] = None,
    links: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    summary = _readiness_summary(checks)
    surface_key = f"management_readiness_{readiness_id.replace('-', '_')}"
    aggregate_surface = _aggregate_group_surface(
        surface_key,
        list(source_surfaces.values()) or [_composed_surface_status(snapshot_at=snapshot_at)],
        snapshot_at=snapshot_at,
        unavailable_message=f"{title} readiness aggregate unavailable.",
        degraded_message=f"{title} readiness aggregate is available, but one or more evidence surfaces are degraded.",
    )
    aggregate_surface["readiness_status"] = summary["readiness_status"]
    aggregate_surface["can_proceed"] = summary["can_proceed"]

    surfaces = {surface_key: aggregate_surface}
    surfaces.update(source_surfaces)
    data = {
        "id": readiness_id,
        "readinessId": readiness_id,
        "readiness_id": readiness_id,
        "title": title,
        "readinessStatus": summary["readinessStatus"],
        "readiness_status": summary["readiness_status"],
        "canProceed": summary["canProceed"],
        "can_proceed": summary["can_proceed"],
        "blockingReasons": summary["blockingReasons"],
        "blocking_reasons": summary["blocking_reasons"],
        "checks": checks,
        "evidenceRefs": evidence_refs,
        "evidence_refs": evidence_refs,
        "links": links or {},
        "details": details or {},
    }
    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = surfaces
    return {
        "data": data,
        "summary": summary,
        "checks": checks,
        "items": checks,
        "evidence_refs": evidence_refs,
        "meta": meta,
    }
def _build_management_strict_publish_readiness_payload() -> Dict[str, Any]:
    snapshot_at = utc_now()
    audit = _read_repo_json_artifact(_READINESS_STRICT_PUBLISH_AUDIT) or {}
    component_status = audit.get("component_status") if isinstance(audit.get("component_status"), dict) else {}
    forbidden_scan = (
        (audit.get("components") or {}).get("forbidden_path_scan")
        if isinstance(audit.get("components"), dict)
        else {}
    )
    forbidden_signals = (
        forbidden_scan.get("forbidden_signals")
        if isinstance(forbidden_scan, dict) and isinstance(forbidden_scan.get("forbidden_signals"), list)
        else []
    )
    passed = bool(audit.get("passed"))
    checked_at = audit.get("checked_at")
    evidence_refs = [
        _readiness_evidence_ref(_READINESS_STRICT_PUBLISH_AUDIT, "Strict publish audit JSON"),
        _readiness_evidence_ref(_READINESS_STRICT_PUBLISH_REPORT, "Strict publish audit report"),
    ]
    audit_surface = _readiness_artifact_surface(
        "strict_publish_audit",
        _READINESS_STRICT_PUBLISH_AUDIT,
        snapshot_at=snapshot_at,
        label="Strict publish audit",
    )
    checks = [
        _readiness_check(
            "browser_probe",
            "Browser health and /bff/me probe",
            "pass" if component_status.get("LSP-002-V2") is True else "fail",
            blocking=True,
            message="Hosted browser probe must pass before strict publish can proceed.",
            evidence_refs=[_READINESS_STRICT_PUBLISH_AUDIT],
        ),
        _readiness_check(
            "bundle_hash_capture",
            "Hosted bundle hash capture",
            "pass" if component_status.get("LSP-003-V2") is True else "fail",
            blocking=True,
            message="Hosted bundle hash capture must pass before strict publish can proceed.",
            evidence_refs=[_READINESS_STRICT_PUBLISH_AUDIT],
        ),
        _readiness_check(
            "forbidden_path_scan",
            "Forbidden mock/seed runtime path scan",
            "pass" if component_status.get("LSP-004-V2") is True else "fail",
            blocking=True,
            message="Strict publish remains blocked while deployed bundles contain forbidden mock/seed signals.",
            evidence_refs=[_READINESS_STRICT_PUBLISH_AUDIT],
            details={"forbidden_signal_count": len(forbidden_signals)},
        ),
    ]
    return _readiness_response(
        readiness_id="strict-publish",
        title="Strict Publish Audit",
        checks=checks,
        evidence_refs=evidence_refs,
        source_surfaces={"strict_publish_audit": audit_surface},
        snapshot_at=snapshot_at,
        details={
            "passed": passed,
            "checked_at": checked_at,
            "deployment_url": audit.get("deployment_url"),
            "browser_probe_base_url": audit.get("browser_probe_base_url"),
            "errors": audit.get("errors") if isinstance(audit.get("errors"), list) else [],
        },
        links={
            "self": f"/bff{_MANAGEMENT_READINESS_BASE_ROUTE}/strict-publish",
            "audit": f"/{_READINESS_STRICT_PUBLISH_REPORT}",
        },
    )
def _build_management_bff_ha_readiness_payload() -> Dict[str, Any]:
    snapshot_at = utc_now()
    packet_text = _read_repo_text_artifact(_READINESS_BFF_HA_PACKET)
    review_text = _read_repo_text_artifact(_READINESS_BFF_HA_REVIEW)
    packet_exists = bool(packet_text)
    review_approved = "Status: **approved**" in review_text or "Approved." in review_text
    evidence_refs = [
        _readiness_evidence_ref(_READINESS_BFF_HA_PACKET, "BFF HA failover demo packet"),
        _readiness_evidence_ref(_READINESS_BFF_HA_REVIEW, "BFF HA failover demo review"),
    ]
    packet_surface = _readiness_artifact_surface(
        "bff_ha_failover_demo",
        _READINESS_BFF_HA_PACKET,
        snapshot_at=snapshot_at,
        label="BFF HA failover demo",
    )
    checks = [
        _readiness_check(
            "dev_failover_demo_packet",
            "Dev failover demo packet recorded",
            "pass" if packet_exists else "fail",
            blocking=True,
            message="The BFF HA readiness page requires the dev failover demo packet.",
            evidence_refs=[_READINESS_BFF_HA_PACKET],
        ),
        _readiness_check(
            "dev_failover_demo_review",
            "Dev failover demo review approved",
            "pass" if review_approved else "fail",
            blocking=True,
            message="The dev failover demo must have reviewer approval.",
            evidence_refs=[_READINESS_BFF_HA_REVIEW],
        ),
        _readiness_check(
            "production_ha_topology",
            "Production HA topology and LB cutover",
            "blocked",
            blocking=True,
            message="Current evidence is dev-only; production BFF HA/LB topology remains a separate gate.",
            evidence_refs=[_READINESS_BFF_HA_PACKET],
            details={
                "dev_only": True,
                "production_topology_ready": False,
                "l1_policy_changed": False,
            },
        ),
    ]
    return _readiness_response(
        readiness_id="bff-ha",
        title="BFF HA Readiness",
        checks=checks,
        evidence_refs=evidence_refs,
        source_surfaces={"bff_ha_failover_demo": packet_surface},
        snapshot_at=snapshot_at,
        details={
            "dev_demo_ready": packet_exists and review_approved,
            "production_topology_ready": False,
        },
        links={
            "self": f"/bff{_MANAGEMENT_READINESS_BASE_ROUTE}/bff-ha",
            "evidence": f"/{_READINESS_BFF_HA_PACKET}",
        },
    )
def _build_management_broker_live_readiness_payload() -> Dict[str, Any]:
    snapshot_at = utc_now()
    broker_surface = read_store.get_openclaw_broker_adapter_readiness()
    service_surface = (
        broker_surface.get("service_status")
        if isinstance(broker_surface.get("service_status"), dict)
        else _composed_surface_status(snapshot_at=snapshot_at)
    )
    live_gate_enabled = _bool_from_env("PANTHEON_LIVE_BROKER_ENABLED", default=False)
    live_execution_enabled = bool(broker_surface.get("live_execution_enabled"))
    live_adapter_state = str(broker_surface.get("live_adapter_state") or "unknown").lower()
    broker_live_ready = (
        live_gate_enabled
        and live_execution_enabled
        and live_adapter_state in {"enabled", "active"}
    )
    evidence_refs = [
        _readiness_evidence_ref(
            "docs/deployment/evidence/ep5-broker-tw-002/20260517T054748Z/evidence-packet/shioaji-sandbox-evidence-packet.json",
            "Broker sandbox evidence packet",
        ),
        _readiness_evidence_ref(_READINESS_BROKER_LIVE_DISABLED, "Broker live-disabled smoke"),
    ]
    live_disabled_surface = _readiness_artifact_surface(
        "broker_live_disabled_smoke",
        _READINESS_BROKER_LIVE_DISABLED,
        snapshot_at=snapshot_at,
        label="Broker live-disabled smoke",
    )
    checks = [
        _readiness_check(
            "openclaw_broker_readiness_surface",
            "OpenClaw broker readiness surface",
            "pass" if broker_surface.get("overall_status") != "unavailable" else "fail",
            blocking=True,
            message="Broker live readiness requires the OpenClaw broker readiness surface.",
            details={"overall_status": broker_surface.get("overall_status")},
        ),
        _readiness_check(
            "live_broker_gate",
            "Live broker gate",
            "pass" if broker_live_ready else "blocked",
            blocking=True,
            message="Live broker execution is fail-closed until explicit live broker gates and adapter state are enabled.",
            evidence_refs=[_READINESS_BROKER_LIVE_DISABLED],
            details={
                "PANTHEON_LIVE_BROKER_ENABLED": live_gate_enabled,
                "live_execution_enabled": live_execution_enabled,
                "live_adapter_state": live_adapter_state,
            },
        ),
        _readiness_check(
            "no_real_capital_side_effects",
            "No real capital side effects",
            "pass"
            if broker_surface.get("is_real_capital") is False and broker_surface.get("is_real_order") is False
            else "fail",
            blocking=True,
            message="Broker readiness must not report real capital or real orders before live approval.",
            details={
                "is_real_capital": broker_surface.get("is_real_capital"),
                "is_real_order": broker_surface.get("is_real_order"),
            },
        ),
    ]
    return _readiness_response(
        readiness_id="broker-live",
        title="Broker Live Readiness",
        checks=checks,
        evidence_refs=evidence_refs,
        source_surfaces={
            "openclaw_broker_adapter_readiness": service_surface,
            "broker_live_disabled_smoke": live_disabled_surface,
        },
        snapshot_at=snapshot_at,
        details={
            "broker_readiness": broker_surface,
            "live_broker_enabled": broker_live_ready,
            "fail_closed": not broker_live_ready,
        },
        links={
            "self": f"/bff{_MANAGEMENT_READINESS_BASE_ROUTE}/broker-live",
            "operator_surface": "/api/v1/operator/openclaw/broker/adapter-readiness",
        },
    )
def _build_management_capital_binding_live_readiness_payload() -> Dict[str, Any]:
    snapshot_at = utc_now()
    bindings = read_store.list_bindings()
    runtime_bindings = read_store.list_runtime_bindings()
    active_bindings = [
        binding
        for binding in bindings
        if str(binding.get("validity") or binding.get("status") or "").lower() in {"active", "valid"}
    ]
    live_runtime_bindings = [
        binding
        for binding in runtime_bindings
        if str(binding.get("deployment_stage") or binding.get("deployment_mode") or "").lower()
        in {"canary", "live", "production", "staging-live"}
    ]
    gate_enabled = (
        _bool_from_env("OPENCLAW_CAPITAL_BINDING_ENABLED", default=False)
        or _bool_from_env("PANTHEON_CAPITAL_BINDING_LIVE_ENABLED", default=False)
    )
    evidence_refs = [
        _readiness_evidence_ref(_READINESS_NO_REAL_CAPITAL_EVIDENCE, "No real capital evidence"),
        _readiness_evidence_ref(_READINESS_BROKER_LIVE_DISABLED, "Broker live-disabled smoke"),
    ]
    capital_surface = _dataset_surface_status("persona_bindings", snapshot_at=snapshot_at)
    runtime_surface = _dataset_surface_status("runtime_bindings", snapshot_at=snapshot_at)
    no_real_capital_surface = _readiness_artifact_surface(
        "no_real_capital_evidence",
        _READINESS_NO_REAL_CAPITAL_EVIDENCE,
        snapshot_at=snapshot_at,
        label="No real capital evidence",
    )
    checks = [
        _readiness_check(
            "capital_binding_live_gate",
            "Capital binding live gate",
            "pass" if gate_enabled else "blocked",
            blocking=True,
            message="Live capital binding remains fail-closed until explicit capital-binding live gates are enabled.",
            evidence_refs=[_READINESS_NO_REAL_CAPITAL_EVIDENCE],
            details={
                "OPENCLAW_CAPITAL_BINDING_ENABLED": _bool_from_env("OPENCLAW_CAPITAL_BINDING_ENABLED", default=False),
                "PANTHEON_CAPITAL_BINDING_LIVE_ENABLED": _bool_from_env(
                    "PANTHEON_CAPITAL_BINDING_LIVE_ENABLED",
                    default=False,
                ),
            },
        ),
        _readiness_check(
            "active_persona_capital_bindings",
            "Active persona-capital binding records",
            "pass" if active_bindings else "warn",
            blocking=False,
            message="Active persona-capital bindings are visible to the BFF read surface.",
            details={
                "active_binding_count": len(active_bindings),
                "binding_count": len(bindings),
            },
        ),
        _readiness_check(
            "live_runtime_binding_absence",
            "No live runtime binding activated by this BFF",
            "pass" if not live_runtime_bindings else "fail",
            blocking=True,
            message="Readiness publication must not silently materialize live runtime bindings.",
            details={"live_runtime_binding_count": len(live_runtime_bindings)},
        ),
    ]
    return _readiness_response(
        readiness_id="capital-binding-live",
        title="Capital Binding Live Readiness",
        checks=checks,
        evidence_refs=evidence_refs,
        source_surfaces={
            "persona_bindings": capital_surface,
            "runtime_bindings": runtime_surface,
            "no_real_capital_evidence": no_real_capital_surface,
        },
        snapshot_at=snapshot_at,
        details={
            "capital_binding_live_enabled": gate_enabled,
            "active_binding_count": len(active_bindings),
            "live_runtime_binding_count": len(live_runtime_bindings),
            "fail_closed": not gate_enabled,
        },
        links={"self": f"/bff{_MANAGEMENT_READINESS_BASE_ROUTE}/capital-binding-live"},
    )
def _build_management_ep5_readiness_payload() -> Dict[str, Any]:
    snapshot_at = utc_now()
    broker = _build_management_broker_live_readiness_payload()
    capital = _build_management_capital_binding_live_readiness_payload()
    bff_ha = _build_management_bff_ha_readiness_payload()
    strict_publish = _build_management_strict_publish_readiness_payload()
    evidence_refs = [
        _readiness_evidence_ref(rel_path, label)
        for rel_path, label in _READINESS_EP5_EVIDENCE_REFS
    ]
    ep5_surfaces = {
        f"ep5_evidence_{index}": _readiness_artifact_surface(
            f"ep5_evidence_{index}",
            ref["path"],
            snapshot_at=snapshot_at,
            label=ref["label"],
        )
        for index, ref in enumerate(evidence_refs, start=1)
    }
    family_payloads = {
        "broker-live": broker,
        "capital-binding-live": capital,
        "bff-ha": bff_ha,
        "strict-publish": strict_publish,
    }
    checks = [
        _readiness_check(
            "ep5_evidence_bundle",
            "EP5 prerequisite evidence bundle",
            "pass" if all(ref.get("exists") for ref in evidence_refs) else "fail",
            blocking=True,
            message="EP5 readiness requires the prerequisite evidence bundle to be present in repo.",
            evidence_refs=[ref["path"] for ref in evidence_refs],
            details={
                "available_evidence_count": len([ref for ref in evidence_refs if ref.get("exists")]),
                "required_evidence_count": len(evidence_refs),
            },
        )
    ]
    for family_id, payload in family_payloads.items():
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        checks.append(
            _readiness_check(
                f"{family_id}-readiness",
                f"{family_id} readiness",
                "pass" if summary.get("can_proceed") is True else "blocked",
                blocking=True,
                message=f"{family_id} must be ready before EP5 can proceed.",
                details={
                    "readiness_status": summary.get("readiness_status"),
                    "blocking_reasons": summary.get("blocking_reasons"),
                },
            )
        )
    return _readiness_response(
        readiness_id="ep5",
        title="EP5 Readiness",
        checks=checks,
        evidence_refs=evidence_refs,
        source_surfaces=ep5_surfaces,
        snapshot_at=snapshot_at,
        details={
            "families": {
                family_id: {
                    "readiness_status": payload["summary"]["readiness_status"],
                    "can_proceed": payload["summary"]["can_proceed"],
                    "blocking_reasons": payload["summary"]["blocking_reasons"],
                }
                for family_id, payload in family_payloads.items()
            },
        },
        links={
            "self": f"/bff{_MANAGEMENT_READINESS_BASE_ROUTE}/ep5",
            "broker_live": f"/bff{_MANAGEMENT_READINESS_BASE_ROUTE}/broker-live",
            "capital_binding_live": f"/bff{_MANAGEMENT_READINESS_BASE_ROUTE}/capital-binding-live",
            "bff_ha": f"/bff{_MANAGEMENT_READINESS_BASE_ROUTE}/bff-ha",
            "strict_publish": f"/bff{_MANAGEMENT_READINESS_BASE_ROUTE}/strict-publish",
        },
    )
def _management_data_sources_read_timeout_seconds() -> float:
    """Bound the one Source Ingest registry read used by Management.

    Source Ingest is the canonical registry authority.  A slow or unhealthy
    registry must therefore yield a typed unavailable envelope rather than
    make the Management event loop wait for the downstream HTTP timeout.
    """
    raw = os.getenv("PANTHEON_BFF_DATA_SOURCES_READ_TIMEOUT_SECONDS", "0.75").strip()
    try:
        return max(0.05, float(raw))
    except (TypeError, ValueError):
        return 0.75
_MANAGEMENT_DATA_SOURCES_READ_SLOT_COUNT = 2
_MANAGEMENT_DATA_SOURCES_READ_SLOTS = threading.BoundedSemaphore(
    _MANAGEMENT_DATA_SOURCES_READ_SLOT_COUNT
)
_MANAGEMENT_DATA_SOURCES_READ_EXECUTOR = ThreadPoolExecutor(
    max_workers=_MANAGEMENT_DATA_SOURCES_READ_SLOT_COUNT,
    thread_name_prefix="bff-management-data-sources",
)
def _snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
    }
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness
    return meta
def _surface_degradation_reason(
    surface: Dict[str, Any],
    *,
    degraded_reason: str,
    unavailable_reason: str,
) -> Optional[str]:
    status = surface.get("status")
    if status == "ok":
        return None
    if status == "unavailable":
        return unavailable_reason
    if surface.get("message"):
        return str(surface["message"])
    if surface.get("note"):
        return str(surface["note"])
    return degraded_reason
_COMMAND_RECEIPT_STATUS_MAP = {
    CommandStatus.SUBMITTED.value: CommandReceiptStatus.ACCEPTED,
    CommandStatus.PROCESSING.value: CommandReceiptStatus.QUEUED,
    CommandStatus.EXECUTED.value: CommandReceiptStatus.QUEUED,
    CommandStatus.FAILED.value: CommandReceiptStatus.FAILED,
    CommandStatus.TIMEOUT.value: CommandReceiptStatus.FAILED,
}
_ACTION_COMMAND_STATUS_MAP = {
    CommandStatus.SUBMITTED.value: ActionCommandStatus.ACCEPTED,
    CommandStatus.PROCESSING.value: ActionCommandStatus.QUEUED,
    CommandStatus.EXECUTED.value: ActionCommandStatus.COMPLETED,
}
def _expected_completion_at(accepted_at: str, estimated_processing_time_ms: int) -> Optional[str]:
    if not accepted_at or estimated_processing_time_ms < 0:
        return None
    try:
        parsed = datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    completed_at = parsed + timedelta(milliseconds=estimated_processing_time_ms)
    return completed_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def _project_command_submission_response(
    *,
    command_id: str,
    command: CommandType,
    accepted_at: str,
    status: CommandStatus,
    staleness_warning: Optional[StalenessWarning],
) -> CommandSubmissionResponse:
    receipt_status = _COMMAND_RECEIPT_STATUS_MAP.get(status.value, CommandReceiptStatus.FAILED)
    meta = CommandResultMeta()
    receipt = CommandReceipt(
        receipt_id=command_id,
        command_id=command_id,
        command=command.value,
        status=receipt_status,
        accepted_at=accepted_at,
        routing_path=CommandRoutingPath.DIRECT,
        expected_completion_at=_expected_completion_at(
            accepted_at,
            meta.estimated_processing_time_ms,
        ),
        error_message=None,
    )
    return CommandSubmissionResponse(
        receipt_id=command_id,
        command=command.value,
        status=receipt_status,
        accepted_at=accepted_at,
        routing_path=CommandRoutingPath.DIRECT,
        expected_completion_at=receipt.expected_completion_at,
        error_message=None,
        staleness_warning=staleness_warning,
        receipt=receipt,
    )
def _command_dual_write_receipts(
    *,
    command_id: str,
    command: str,
    status: str,
    accepted_at: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    tracking_url = f"/api/v1/operator/commands/{command_id}"
    action_receipt = {
        "receipt_type": "action",
        "id": command_id,
        "receipt_id": command_id,
        "command_id": command_id,
        "status": status,
        "trackingUrl": tracking_url,
        "tracking_url": tracking_url,
    }
    command_receipt = {
        "receipt_type": "command",
        "receipt_id": command_id,
        "command_id": command_id,
        "command": command,
        "status": status,
        "trackingUrl": tracking_url,
        "tracking_url": tracking_url,
    }
    if accepted_at:
        action_receipt["accepted_at"] = accepted_at
        command_receipt["accepted_at"] = accepted_at
    return {
        "action_receipt": action_receipt,
        "command_receipt": command_receipt,
    }
def _action_command_status_from_command_status(status: CommandStatus) -> ActionCommandStatus:
    try:
        return _ACTION_COMMAND_STATUS_MAP[status.value]
    except KeyError as exc:
        raise ValueError(
            f"Command status {status.value!r} cannot be projected as a successful CommandResponse"
        ) from exc
def _project_final_command_response(
    *,
    command_id: str,
    command: CommandType,
    accepted_at: str,
    status: CommandStatus,
    staleness_warning: Optional[StalenessWarning],
    meta: Optional[Dict[str, Any]] = None,
    deprecation: Optional[Dict[str, Any]] = None,
) -> CommandResponse[Dict[str, Any]]:
    final_status = _action_command_status_from_command_status(status)
    legacy_payload = _project_command_submission_response(
        command_id=command_id,
        command=command,
        accepted_at=accepted_at,
        status=status,
        staleness_warning=staleness_warning,
    ).model_dump()
    legacy_payload["status"] = final_status.value
    tracking_url = f"/api/v1/operator/commands/{command_id}"
    legacy_payload["command_id"] = command_id
    legacy_payload["commandId"] = command_id
    legacy_payload["tracking_url"] = tracking_url
    legacy_payload["trackingUrl"] = tracking_url
    if isinstance(legacy_payload.get("receipt"), dict):
        legacy_payload["receipt"]["status"] = final_status.value
        legacy_payload["receipt"]["tracking_url"] = tracking_url
        legacy_payload["receipt"]["trackingUrl"] = tracking_url
    receipts = _command_dual_write_receipts(
        command_id=command_id,
        command=command.value,
        status=final_status.value,
        accepted_at=accepted_at,
    )
    legacy_payload["receipt_dual_write"] = receipts
    legacy_payload["action_receipt"] = receipts["action_receipt"]
    legacy_payload["actionReceipt"] = receipts["action_receipt"]
    legacy_payload["command_receipt"] = receipts["command_receipt"]
    legacy_payload["commandReceipt"] = receipts["command_receipt"]
    final_meta = dict(meta or {})
    if deprecation:
        legacy_payload["deprecated"] = True
        legacy_payload["deprecation"] = dict(deprecation)
        if isinstance(legacy_payload.get("receipt"), dict):
            legacy_payload["receipt"]["deprecated"] = True
            legacy_payload["receipt"]["deprecation"] = dict(deprecation)
        final_meta["deprecated"] = True
        final_meta["deprecation"] = dict(deprecation)
    return CommandResponse[Dict[str, Any]](
        status=final_status,
        data=legacy_payload,
        meta=final_meta or None,
    )
def _deprecated_bff_path_response(*, route: str, replacement: str) -> JSONResponse:
    message = f"{route} is deprecated; use {replacement}."
    headers = {
        "Deprecation": "true",
        "Sunset": _PATH_DEDUPE_SUNSET_HTTP_DATE,
        "Link": f'<{replacement}>; rel="successor-version"',
        "Warning": f'299 - "{message}"',
        "X-Deprecated": "true",
        "X-Deprecated-At": _PATH_DEDUPE_DEPRECATED_SINCE,
        "X-Pantheon-Deprecated-Route": route,
        "X-Pantheon-Replacement-Route": replacement,
    }
    return JSONResponse(
        status_code=410,
        headers=headers,
        content={
            "detail": {
                "error": {
                    "code": ErrorCode.OPERATION_NOT_ALLOWED.value,
                    "message": "Deprecated BFF route",
                    "details": {
                        "reason": "route_deprecated",
                        "route": route,
                        "replacement": replacement,
                        "deprecated_since": _PATH_DEDUPE_DEPRECATED_SINCE,
                    },
                }
            },
            "meta": {
                "deprecated": True,
                "deprecation": {
                    "route": route,
                    "replacement": replacement,
                    "deprecated_since": _PATH_DEDUPE_DEPRECATED_SINCE,
                },
            },
        },
    )
def _check_read_surface_state() -> Optional[StalenessWarning]:
    """
    In production, query the BFF read surface health endpoint.
    Returns a StalenessWarning when the surface is degraded or unavailable,
    or None when fresh.
    """
    state = os.getenv("BFF_READ_SURFACE_STATE", "fresh")
    if state == "fresh":
        return None
    return StalenessWarning(
        read_surface_state=state,
        message=(
            "Command submitted against stale read surface data. "
            "Verify target state via secondary control path before confirming action."
        ),
    )
def _management_read_timeout_seconds() -> float:
    """Bound for offloaded management read aggregation (MGMT-LOAD-005).

    /health and other lightweight routes must stay responsive while shell
    summary / Evidence / alerts / approvals / jobs fan out concurrently.
    Those routes run their synchronous read-store aggregation in a worker
    thread (asyncio.to_thread) instead of inline on the event loop, so a slow
    backing read cannot delay unrelated coroutines. This timeout bounds how
    long a route waits before falling back to a degraded response.
    """
    try:
        return max(0.05, float(os.getenv("PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS", "0.6")))
    except (TypeError, ValueError):
        return 0.6

class _ManagementReadTimeout(Exception):
    """Raised when a management read exceeds its bounded wait budget (MGMT-LOAD-005)."""
class _ManagementReadSaturated(Exception):
    """Raised before submission when a bounded read executor has no capacity."""
def _discard_late_management_read_result(task: "asyncio.Task[Any]") -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning("bff.management_read late worker-thread error after timeout budget: %r", exc)
async def _run_management_read(
    func: Callable[..., Any],
    *args: Any,
    timeout_seconds: Optional[float] = None,
    capacity: Optional[threading.BoundedSemaphore] = None,
    executor: Optional[Executor] = None,
    **kwargs: Any,
) -> Any:
    """Run a synchronous read-store aggregation on a worker thread, bounded by a wait budget.

    Deliberately uses asyncio.wait rather than asyncio.wait_for: once an OS
    thread has started synchronous work, Python cannot forcibly cancel it.
    For capacity-bounded calls the semaphore is acquired before executor
    submission and released by the actual concurrent future, so timed-out
    work cannot create an unbounded queue of late jobs.
    """
    budget = _management_read_timeout_seconds() if timeout_seconds is None else timeout_seconds
    if capacity is None:
        task = asyncio.ensure_future(asyncio.to_thread(func, *args, **kwargs))
    else:
        # Reserve capacity before submitting work. Acquiring inside ``func``
        # would still allow an unbounded number of timed-out jobs to collect in
        # the executor queue while earlier synchronous calls keep running.
        if not capacity.acquire(blocking=False):
            raise _ManagementReadSaturated()
        context = copy_context()
        call = partial(func, *args, **kwargs)
        try:
            worker_future = executor.submit(context.run, call) if executor else None
            if worker_future is None:
                raise RuntimeError("A bounded management read requires an executor")
        except BaseException:
            capacity.release()
            raise

        # Hold the reservation until the actual worker future finishes, not
        # merely until the asyncio wrapper times out or is cancelled.
        worker_future.add_done_callback(lambda _future: capacity.release())
        task = asyncio.wrap_future(worker_future)
    done, _pending = await asyncio.wait({task}, timeout=budget)
    if task in done:
        return task.result()
    if capacity is not None:
        # Cancels only work that has not started; a running thread keeps its
        # reservation until the concurrent future's completion callback.
        worker_future.cancel()
    task.add_done_callback(_discard_late_management_read_result)
    raise _ManagementReadTimeout()
async def _read_management_source_connector_registry(
    store: Any,
) -> Dict[str, Any]:
    """Read the canonical Source registry within the Management read budget.

    This is deliberately a bounded projection, not a second registry or a
    cache authority.  Timeout and capacity outcomes retain an explicit
    unavailable source state, so stale or missing Source Ingest truth can
    never be reported as a healthy connector list.
    """
    try:
        return await _run_management_read(
            store.get_source_connector_registry,
            timeout_seconds=_management_data_sources_read_timeout_seconds(),
            capacity=_MANAGEMENT_DATA_SOURCES_READ_SLOTS,
            executor=_MANAGEMENT_DATA_SOURCES_READ_EXECUTOR,
        )
    except _ManagementReadSaturated:
        return {
            "source": "unavailable",
            "connectors": [],
            "provider_examples": [],
            "policy_registry": None,
            "financial_data_source_catalog": None,
            "active_universe_policy": None,
            "reason": "read_capacity_saturated",
        }
    except _ManagementReadTimeout:
        return {
            "source": "unavailable",
            "connectors": [],
            "provider_examples": [],
            "policy_registry": None,
            "financial_data_source_catalog": None,
            "active_universe_policy": None,
            "reason": "read_timeout",
        }

def _openclaw_client_error(exc: OpenClawOpsClientError) -> HTTPException:
    status_code = exc.status_code or 502
    if status_code == 404:
        code = ErrorCode.RESOURCE_NOT_FOUND
    elif status_code == 409:
        code = ErrorCode.RESOURCE_CONFLICT
    elif status_code == 403:
        code = ErrorCode.PRECONDITION_FAILED
    elif status_code >= 500:
        code = ErrorCode.DEPENDENCY_UNAVAILABLE
    else:
        code = ErrorCode.VALIDATION_FAILED
    return _bff_error(
        status_code,
        code,
        exc.message,
        exc.error_code,
        precondition_failed="openclaw_adapter",
        suggestion="Inspect GET /api/v1/operator/openclaw/ops for current adapter degradation state",
    )
def _command_response_durable_meta(idempotency_key: str, *, replayed: bool) -> Dict[str, Any]:
    return {
        "durable": True,
        "liveCapitalSideEffects": False,
        "idempotency": {
            "key": idempotency_key,
            "idempotencyKey": idempotency_key,
            "replayed": replayed,
        },
    }
def _command_response_dry_run_meta(idempotency_key: str) -> Dict[str, Any]:
    return {
        "dryRun": True,
        "durable": False,
        "liveCapitalSideEffects": False,
        "idempotency": {
            "key": idempotency_key,
            "idempotencyKey": idempotency_key,
            "replayed": False,
        },
    }
def _submit_final_command_admission(
    *,
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any],
    authorization: Optional[str],
    x_mfa_token: Optional[str],
    x_trace_id: Optional[str],
    x_correlation_id: Optional[str],
    x_request_id: Optional[str],
    x_confirm_token: Optional[str],
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str],
    route: str = _FINAL_COMMAND_ROUTE,
    source_route: Optional[str] = None,
    foundation_raw_payload: Optional[Dict[str, Any]] = None,
    audit_extra: Optional[Dict[str, Any]] = None,
    extra_precondition: Optional[Callable[[OperatorIdentity, OperatorCommand], None]] = None,
    enqueue: bool = True,
    include_durable_meta: bool = False,
    response_deprecation: Optional[Dict[str, Any]] = None,
) -> CommandResponse[Dict[str, Any]]:
    """Submit a final-contract command through the shared BFF command admission path."""
    identity = _extract_identity(authorization, mfa_token=x_mfa_token)
    cmd = _normalize_operator_command_payload(payload)

    # Resolve idempotency key before building foundation context so the key is
    # present in the trace from the start.
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)

    foundation_context = _build_foundation_command_context(
        cmd=cmd,
        identity=identity,
        raw_payload=(
            foundation_raw_payload
            if foundation_raw_payload is not None
            else payload
        ),
        trace_id=x_trace_id,
        correlation_id=x_correlation_id,
        request_id=x_request_id,
        idempotency_key=resolved_key,
        route=route,
        source_route=source_route,
    )

    try:
        _reject_body_idempotency_key(payload)
        _reject_server_managed_rebalance_evidence_command(cmd)
        if extra_precondition is not None:
            extra_precondition(identity, cmd)
        _validate_audit_context(cmd)
        _validate_capital_authority_target_binding(cmd)
        _ensure_live_broker_scope_allowed(cmd, payload)
        _validate_drawer_runtime_target(cmd)
        _validate_final_command_target_type(cmd)
        validator = _VALIDATORS.get(cmd.command)
        if validator:
            validator(cmd.params, identity)
    except HTTPException as exc:
        raise _foundation_bff_error(exc, foundation_context=foundation_context) from exc

    duplicate = command_store.get_command_by_idempotency_key(
        foundation_context["idempotency_record"].idempotency_key,
        operator_id=identity.operator_id,
    )
    if duplicate:
        duplicate_record = (duplicate.get("foundation") or {}).get("idempotency_record") or {}
        if duplicate_record.get("request_hash") != foundation_context["idempotency_record"].request_hash:
            raise _foundation_idempotency_conflict_error(
                foundation_context=foundation_context,
                existing_command_id=str(duplicate.get("command_id") or ""),
            )
        _assert_duplicate_confirm_token_matches(
            duplicate=duplicate,
            cmd=cmd,
            payload=payload,
            confirm_token=x_confirm_token,
            foundation_context=foundation_context,
        )
        duplicate_status = CommandStatus(
            duplicate.get("status") or CommandStatus.SUBMITTED.value
        )
        if enqueue and _retryable_terminal_capital_command(duplicate):
            command_store.update_status(
                str(duplicate["command_id"]),
                CommandStatus.SUBMITTED,
                audit={"retry_requested_at": utc_now()},
            )
            background_tasks.add_task(
                _process_command_stub, str(duplicate["command_id"])
            )
            duplicate_status = CommandStatus.SUBMITTED
        if route == "POST /api/v1/operator/commands":
            return _project_command_submission_response(
                command_id=duplicate["command_id"],
                command=cmd.command,
                accepted_at=duplicate.get("submitted_at") or utc_now(),
                status=duplicate_status,
                staleness_warning=None,
            )
        return _project_final_command_response(
            command_id=duplicate["command_id"],
            command=cmd.command,
            accepted_at=duplicate.get("submitted_at") or utc_now(),
            status=duplicate_status,
            staleness_warning=None,
            meta=_command_response_durable_meta(resolved_key, replayed=True)
            if include_durable_meta
            else None,
            deprecation=response_deprecation,
        )

    try:
        if route == "POST /api/v1/operator/commands":
            precondition_evidence = (
                _require_final_command_preconditions(
                    cmd=cmd,
                    payload=payload,
                    confirm_token=x_confirm_token,
                    identity=identity,
                    correlation_id=foundation_context["trace_context"].correlation_id,
                )
                if cmd.command in {
                    CommandType.APPROVED_APPLY,
                    CommandType.EMERGENCY_CONTAINMENT,
                }
                else {}
            )
        else:
            precondition_evidence = _require_final_command_preconditions(
                cmd=cmd,
                payload=payload,
                confirm_token=x_confirm_token,
                identity=identity,
                correlation_id=foundation_context["trace_context"].correlation_id,
            )
    except HTTPException as exc:
        raise _foundation_bff_error(exc, foundation_context=foundation_context) from exc

    stored_params = _stored_command_params(cmd, identity, raw_payload=payload)
    stored_params["idempotency_key"] = resolved_key
    stored_params["request_hash"] = foundation_context["idempotency_record"].request_hash
    _canonicalize_validated_precondition_evidence(
        stored_params,
        precondition_evidence,
    )

    active = command_store.get_active_commands_for_target(cmd.target.type.value, cmd.target.id)
    if active:
        error = _bff_error(
            409, ErrorCode.RESOURCE_CONFLICT,
            "A command is already in flight for this target",
            f"Command {active[0]['command_id']} is currently {active[0]['status']}",
            precondition_failed="concurrent_safety",
            suggestion="Wait for the in-flight command to complete or time out before retrying",
        )
        raise _foundation_bff_error(error, foundation_context=foundation_context)

    staleness_warning = _check_read_surface_state()
    if _request_dry_run_requested():
        command_envelope: CommandEnvelope = foundation_context["command_envelope"]
        if route == "POST /api/v1/operator/commands":
            return _project_command_submission_response(
                command_id=command_envelope.command_id,
                command=cmd.command,
                accepted_at=utc_now(),
                status=CommandStatus.SUBMITTED,
                staleness_warning=staleness_warning,
            )
        return _project_final_command_response(
            command_id=command_envelope.command_id,
            command=cmd.command,
            accepted_at=utc_now(),
            status=CommandStatus.SUBMITTED,
            staleness_warning=staleness_warning,
            meta=_command_response_dry_run_meta(resolved_key),
            deprecation=response_deprecation,
        )

    command_envelope = foundation_context["command_envelope"]
    idempotency_record: IdempotencyRecord = foundation_context["idempotency_record"]
    idempotency_record = idempotency_record.with_status(
        "succeeded",
        result_ref=f"command:{command_envelope.command_id}",
    )
    foundation_context["idempotency_record"] = idempotency_record
    command_id = command_envelope.command_id
    submitted_at = utc_now()
    receipt_dual_write = _command_dual_write_receipts(
        command_id=command_id,
        command=cmd.command.value,
        status=ActionCommandStatus.ACCEPTED.value,
        accepted_at=submitted_at,
    )

    auth_context = _command_runtime_auth_context(
        command_id=command_id,
        authorization=authorization,
        mfa_token=x_mfa_token,
        identity=identity,
    )

    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "mfa_verified": identity.mfa_verified,
        "reason": cmd.audit_context.reason,
        "incident_id": cmd.audit_context.incident_id,
        "preconditions_checked": [
            "authentication", "authorization", "params_shape", "concurrent_safety"
        ],
        "timestamp": submitted_at,
        "staleness_warning": staleness_warning.model_dump() if staleness_warning else None,
        "auth": auth_context,
        "foundation": _serialize_foundation_context(foundation_context),
        "receipt_dual_write": receipt_dual_write,
    }
    if precondition_evidence:
        audit_record["precondition_evidence"] = precondition_evidence
    if audit_extra:
        audit_record.update({key: value for key, value in audit_extra.items() if value is not None})

    serialized_foundation = _serialize_foundation_context(foundation_context)
    with command_store.serialized_transaction():
        duplicate_after_precheck = command_store.get_command_by_idempotency_key(
            resolved_key,
            operator_id=identity.operator_id,
        )
        if duplicate_after_precheck:
            duplicate_record = (
                (duplicate_after_precheck.get("foundation") or {})
                .get("idempotency_record")
                or {}
            )
            if duplicate_record.get("request_hash") != foundation_context["idempotency_record"].request_hash:
                raise _foundation_idempotency_conflict_error(
                    foundation_context=foundation_context,
                    existing_command_id=str(duplicate_after_precheck.get("command_id") or ""),
                )
            _assert_duplicate_confirm_token_matches(
                duplicate=duplicate_after_precheck,
                cmd=cmd,
                payload=payload,
                confirm_token=x_confirm_token,
                foundation_context=foundation_context,
            )
            if route == "POST /api/v1/operator/commands":
                return _project_command_submission_response(
                    command_id=duplicate_after_precheck["command_id"],
                    command=cmd.command,
                    accepted_at=duplicate_after_precheck.get("submitted_at") or utc_now(),
                    status=CommandStatus(
                        duplicate_after_precheck.get("status")
                        or CommandStatus.SUBMITTED.value
                    ),
                    staleness_warning=None,
                )
            return _project_final_command_response(
                command_id=duplicate_after_precheck["command_id"],
                command=cmd.command,
                accepted_at=duplicate_after_precheck.get("submitted_at") or utc_now(),
                status=CommandStatus(
                    duplicate_after_precheck.get("status")
                    or CommandStatus.SUBMITTED.value
                ),
                staleness_warning=None,
                meta=_command_response_durable_meta(resolved_key, replayed=True)
                if include_durable_meta
                else None,
                deprecation=response_deprecation,
            )

        if precondition_evidence.get("confirm_token_id"):
            try:
                revalidated_token_id = _require_final_command_confirm_token(
                    cmd=cmd,
                    payload=payload,
                    confirm_token=x_confirm_token,
                    identity=identity,
                    correlation_id=foundation_context["trace_context"].correlation_id,
                )
            except HTTPException as exc:
                raise _foundation_bff_error(exc, foundation_context=foundation_context) from exc
            if revalidated_token_id:
                precondition_evidence["confirm_token_id"] = revalidated_token_id

        record, active_after_precheck = _persist_admitted_command_with_confirm_token(
            command_id=command_id,
            command_type=cmd.command,
            target=cmd.target,
            submitted_at=submitted_at,
            params=stored_params,
            audit_context=audit_record,
            foundation_context=serialized_foundation,
            precondition_evidence=precondition_evidence,
            identity=identity,
        )
    if active_after_precheck:
        error = _bff_error(
            409, ErrorCode.RESOURCE_CONFLICT,
            "A command is already in flight for this target",
            f"Command {active_after_precheck['command_id']} is currently {active_after_precheck['status']}",
            precondition_failed="concurrent_safety",
            suggestion="Wait for the in-flight command to complete or time out before retrying",
        )
        raise _foundation_bff_error(error, foundation_context=foundation_context)
    assert record is not None

    log.info(
        "Accepted final-contract command %s (%s) for %s:%s by operator %s",
        command_id, cmd.command.value, cmd.target.type.value, cmd.target.id, identity.operator_id,
    )

    if enqueue:
        background_tasks.add_task(_process_command_stub, command_id)

    if route == "POST /api/v1/operator/commands":
        return _project_command_submission_response(
            command_id=command_id,
            command=cmd.command,
            accepted_at=submitted_at,
            status=CommandStatus.SUBMITTED,
            staleness_warning=staleness_warning,
        )
    return _project_final_command_response(
        command_id=command_id,
        command=cmd.command,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
        meta=_command_response_durable_meta(resolved_key, replayed=False)
        if include_durable_meta
        else None,
        deprecation=response_deprecation,
    )
_AGORA_CORE_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_AGORA_SIGNAL_WRITE_ROLES = {"analyst", "operator", "approver", "admin", "reviewer"}
_AGORA_BULK_FEEDBACK_ROLES = {"analyst", "operator", "reviewer", "approver", "admin"}
def _truthy_header(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
def _request_dry_run_requested(explicit_header: Optional[str] = None) -> bool:
    return _truthy_header(explicit_header) or bool(_REQUEST_DRY_RUN_CONTEXT.get())
def _dry_run_success_response(
    data: Dict[str, Any],
    *,
    status_code: int = 200,
    snapshot_at: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    evidence_kind: Optional[str] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at or utc_now(),
        "dryRun": True,
        "durable": False,
        "liveCapitalSideEffects": False,
    }
    if idempotency_key:
        meta["idempotency"] = {
            "key": idempotency_key,
            "idempotencyKey": idempotency_key,
            "replayed": False,
        }
    if evidence_kind:
        meta["evidenceKind"] = evidence_kind
        meta["evidence_kind"] = evidence_kind
    if extra_meta:
        meta.update(extra_meta)
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder({"data": data, "meta": meta}),
        headers=headers,
    )
def _require_agora_signal_write_role(identity: OperatorIdentity) -> None:
    if not _AGORA_SIGNAL_WRITE_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Agora signal creation requires analyst-level role",
            "Operator does not hold the required analyst, operator, reviewer, approver, or admin role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with analyst-level Agora write access",
        )
def _agora_private_record_owner(record: Dict[str, Any]) -> str:
    for key in ("createdBy", "created_by", "user_id", "userId", "owner_id", "ownerId", "operator_id", "operatorId"):
        clean = str(record.get(key) or "").strip()
        if clean:
            return clean
    owner_ref = record.get("owner_ref") if isinstance(record.get("owner_ref"), dict) else {}
    return str(owner_ref.get("user_id") or owner_ref.get("owner_id") or "").strip()
def _agora_private_record_visible(record: Dict[str, Any], identity: OperatorIdentity) -> bool:
    visibility = str(record.get("visibility") or "private").strip().lower()
    owner = _agora_private_record_owner(record)
    if visibility != "private" or not owner:
        return True
    return owner == identity.operator_id
def _agora_filter_private_records(
    records: List[Dict[str, Any]],
    identity: OperatorIdentity,
) -> List[Dict[str, Any]]:
    return [
        record
        for record in records
        if isinstance(record, dict) and _agora_private_record_visible(record, identity)
    ]
def _agora_required_text(payload: Dict[str, Any], *fields: str) -> str:
    for field in fields:
        clean = str(payload.get(field) or "").strip()
        if clean:
            return clean
    label = fields[0] if fields else "value"
    raise _bff_error(
        422,
        ErrorCode.VALIDATION_FAILED,
        f"{label} is required",
        f"Agora request requires a non-empty {label}",
        precondition_failed=label,
    )
def _require_agora_bulk_feedback_role(identity: OperatorIdentity) -> None:
    if not _AGORA_BULK_FEEDBACK_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Agora feedback access requires analyst role",
            "Operator does not hold the required Agora feedback role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with analyst, operator, reviewer, approver, or admin role",
        )
_MCP_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}
_CAPITAL_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
def _capital_bff_idempotency_identity(operator_id: str, resolved_key: str) -> str:
    return f"{operator_id}\x00{resolved_key}"
def _capital_bff_idempotency_check(
    operator_id: str,
    resolved_key: str,
    request_hash: str,
) -> Optional[Dict[str, Any]]:
    """Return cached result on replay or raise 409 on conflict."""
    existing = _CAPITAL_BFF_IDEMPOTENCY.get(
        _capital_bff_idempotency_identity(operator_id, resolved_key)
    )
    if existing is None:
        return None
    if existing.get("request_hash") != request_hash:
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {resolved_key!r} is bound to a different request hash",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        )
    return existing.get("result")
def _capital_bff_idempotency_store(
    operator_id: str,
    resolved_key: str,
    request_hash: str,
    result: Any,
) -> None:
    _CAPITAL_BFF_IDEMPOTENCY[
        _capital_bff_idempotency_identity(operator_id, resolved_key)
    ] = {"request_hash": request_hash, "result": result}
def _capital_bff_action_command(
    entity_type: ObjectType,
    entity_id: str,
    action_id: str,
    resolved_key: str,
    identity: Any,
    payload: Dict[str, Any],
    command_type: CommandType,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Dict[str, Any]:
    """Submit a resource action through the command store and return the receipt."""
    request_hash = sha256_checksum({
        "entity_type": entity_type.value,
        "entity_id": entity_id,
        "action_id": action_id,
        "payload": payload,
    })
    durable = command_store.get_command_by_idempotency_key(
        resolved_key,
        operator_id=identity.operator_id,
    )
    if durable is not None:
        durable_idempotency = (durable.get("foundation") or {}).get("idempotency_record") or {}
        if durable_idempotency.get("request_hash") != request_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to command {durable.get('command_id')}",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        replay = _project_final_command_response(
            command_id=str(durable["command_id"]),
            command=CommandType(str(durable["type"])),
            accepted_at=str(durable.get("submitted_at") or utc_now()),
            status=CommandStatus(str(durable.get("status") or CommandStatus.SUBMITTED.value)),
            staleness_warning=None,
            meta=_command_response_durable_meta(resolved_key, replayed=True),
        )
        _capital_bff_idempotency_store(
            identity.operator_id, resolved_key, request_hash, replay
        )
        return replay
    cached = _capital_bff_idempotency_check(
        identity.operator_id, resolved_key, request_hash
    )
    if cached is not None:
        return cached
    catalog_entry = get_catalog_entry(command_type.value)
    staleness_warning = _check_read_surface_state()
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()
    target = TargetObject(type=entity_type, id=entity_id)
    audit_action = _foundation_audit_for_command_record(
        identity=identity,
        command_type=command_type,
        target_type=entity_type,
        target_id=entity_id,
        payload={**payload, "action_id": action_id},
        reason=str(payload.get("reason") or action_id or command_type.value),
        command_id=command_id,
        idempotency_key=resolved_key,
        route=f"POST /bff/{entity_type.value}/{entity_id}/actions/{action_id}",
        metadata={"action_id": action_id, "catalog_entry": catalog_entry.action_id if catalog_entry else None},
    )
    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "action_id": action_id,
        "preconditions_checked": ["authentication", "authorization", "idempotency"],
        "timestamp": submitted_at,
    }
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=resolved_key,
        operation_type=f"bff.{command_type.value}",
        target_ref=f"{entity_type.value}:{entity_id}",
        request_payload={
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "action_id": action_id,
            "payload": payload,
        },
        trace_id=command_id,
    )
    foundation_ctx = {
        "idempotency_record": idempotency_record.to_dict(),
        "audit_action": audit_action.to_dict(),
    }
    audit_record["foundation"] = foundation_ctx
    command_store.submit_command(
        command_id=command_id,
        command_type=command_type,
        target=target,
        submitted_at=submitted_at,
        params={
            **{
                key: value
                for key, value in payload.items()
                if key
                not in {
                    "entity_type",
                    "entity_id",
                    "action_id",
                    "actor_id",
                    "actor_role",
                    "idempotency_key",
                    "request_hash",
                }
            },
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "action_id": action_id,
            "actor_id": identity.operator_id,
            "actor_role": next(
                (
                    role
                    for role in ("admin", "approver", "reviewer", "operator")
                    if role in identity.roles
                ),
                "operator",
            ),
            "idempotency_key": resolved_key,
            "request_hash": request_hash,
        },
        audit_context=audit_record,
        foundation_context=foundation_ctx,
    )
    if background_tasks is not None:
        background_tasks.add_task(_process_command_stub, command_id)
    result = _project_final_command_response(
        command_id=command_id,
        command=command_type,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )
    _capital_bff_idempotency_store(
        identity.operator_id, resolved_key, request_hash, result
    )
    return result
_PPL_ALLOC_009_PAPER_POLICY_VERSION = "persona-paper-allocation-simulation-v1"
_PPL_ALLOC_009_PAPER_AUTHORITY_MODE = "governed_paper_simulation"
_PM12_RANKING_SNAPSHOT_DEFAULT_TTL_SECONDS = 24 * 60 * 60
_PM12_RANKING_SNAPSHOT_MAX_TTL_SECONDS = 7 * 24 * 60 * 60
_PM12_ALLOCATION_LINE_DIGEST_FIELDS = (
    "ranking_snapshot_id",
    "allocation_evaluation_id",
    "allocation_policy_version",
    "persona_id",
    "stage",
    "capital_scope",
    "capital_pool_id",
    "capital_sleeve_id",
    "current_weight",
    "target_weight",
    "delta",
    "cap_reasons",
    "evidence_refs",
)
def _pm12_allocation_line_digest(line: Dict[str, Any]) -> str:
    basis = {
        field: line.get(field)
        for field in _PM12_ALLOCATION_LINE_DIGEST_FIELDS
    }
    basis["capital_scope"] = line.get("capital_scope") or "pool"
    basis["cap_reasons"] = list(line.get("cap_reasons") or [])
    basis["evidence_refs"] = list(line.get("evidence_refs") or [])
    return _stable_json_hash(basis)
def _pm12_semantic_json_value(value: Any) -> Any:
    """Canonicalize JSON values without treating booleans as numbers."""
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, (int, float, Decimal)):
        try:
            numeric = (
                value
                if isinstance(value, Decimal)
                else Decimal(str(value))
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("allocation line contains an invalid number") from exc
        if not numeric.is_finite():
            raise ValueError("allocation line contains a non-finite number")
        if numeric == 0:
            numeric = Decimal(0)
        return ["number", format(numeric.normalize(), "f")]
    if isinstance(value, list):
        return ["array", [_pm12_semantic_json_value(item) for item in value]]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("allocation line contains a non-string object key")
        return [
            "object",
            [
                [key, _pm12_semantic_json_value(value[key])]
                for key in sorted(value)
            ],
        ]
    raise ValueError(
        f"allocation line contains unsupported JSON value {type(value).__name__}"
    )
def _pm12_semantic_values_match(asserted: Any, authoritative: Any) -> bool:
    """Compare an asserted value against its admitted authoritative value using the
    numeric/bool/order-safe semantic canonical form so benign browser JSON
    round-trips (for example 1.0 -> 1, or object key reordering) do not read as an
    assertion mismatch. Values that cannot be canonicalized stay fail-closed by
    returning False, preserving the strict-by-default posture for malformed input."""
    try:
        return (
            _pm12_semantic_json_value(asserted)
            == _pm12_semantic_json_value(authoritative)
        )
    except ValueError:
        return False
def _pm12_allocation_snapshot_record(snapshot_id: str) -> Dict[str, Any]:
    snapshot = read_store.get_ranking_snapshot(snapshot_id)
    if not isinstance(snapshot, dict):
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "unknown ranking snapshot",
            "Allocation evaluation requires a BFF-admitted quarterly ranking snapshot.",
            precondition_failed="ranking_snapshot_id",
        )
    expected_content_digest = _stable_json_hash({
        "surface": snapshot.get("surface"),
        "period": snapshot.get("period"),
        "formula_version": snapshot.get("formula_version"),
        "items": snapshot.get("items") or [],
    })
    if str(snapshot.get("content_digest") or "") != expected_content_digest:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "ranking snapshot integrity check failed",
            "The durable snapshot content no longer matches its admitted digest.",
            precondition_failed="ranking_snapshot_id",
        )
    if (
        str(snapshot.get("surface") or "") != "quarterly"
        or str(snapshot.get("formula_version") or "")
        != _PM12_LEAGUE_FORMULA_VERSION
    ):
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "ranking snapshot is not allocation eligible",
            "Only admitted PM-12 quarterly snapshots can feed allocation evaluation.",
            precondition_failed="ranking_snapshot_id",
        )
    return snapshot
def _pm12_ranking_snapshot_ttl_seconds() -> int:
    raw = os.getenv(
        "PANTHEON_PM12_RANKING_SNAPSHOT_TTL_SECONDS",
        str(_PM12_RANKING_SNAPSHOT_DEFAULT_TTL_SECONDS),
    ).strip()
    try:
        configured = int(raw)
    except (TypeError, ValueError):
        return 0
    if configured <= 0 or configured > _PM12_RANKING_SNAPSHOT_MAX_TTL_SECONDS:
        return 0
    return configured
def _pm12_recommendation_snapshot_record(snapshot_id: str) -> Dict[str, Any]:
    snapshot = _pm12_allocation_snapshot_record(snapshot_id)
    created_at = _audit_datetime(snapshot.get("created_at"))
    now = _audit_datetime(utc_now())
    ttl_seconds = _pm12_ranking_snapshot_ttl_seconds()
    if created_at is None or now is None or ttl_seconds <= 0:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "ranking snapshot admission window is invalid",
            "Recommendation submission requires a timestamped snapshot and a valid bounded TTL.",
            precondition_failed="ranking_snapshot_id",
        )
    age_seconds = (now - created_at).total_seconds()
    if age_seconds < -300 or age_seconds > ttl_seconds:
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_FAILED,
            "ranking snapshot admission window expired",
            "Fetch a current recommendation and submit its immutable admitted snapshot.",
            precondition_failed="ranking_snapshot_id",
        )
    return snapshot
def _pm12_allocation_evaluation_record(evaluation_id: str) -> Dict[str, Any]:
    evaluation = read_store.get_allocation_evaluation(evaluation_id)
    if not isinstance(evaluation, dict):
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "unknown allocation evaluation",
            "The proposal must join to a durable server-side allocation evaluation.",
            precondition_failed="allocation_evaluation_id",
        )
    lines = evaluation.get("lines")
    if not isinstance(lines, list) or not lines:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "allocation evaluation integrity check failed",
            "The durable allocation evaluation has no admitted lines.",
            precondition_failed="allocation_evaluation_id",
        )
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "allocation evaluation integrity check failed",
                f"The durable allocation line at index {index} is invalid.",
                precondition_failed="allocation_line_digest",
            )
        supplied_digest = str(line.get("allocation_line_digest") or "").strip()
        if not supplied_digest or _pm12_allocation_line_digest(line) != supplied_digest:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "allocation evaluation integrity check failed",
                f"The durable allocation line at index {index} no longer matches its digest.",
                precondition_failed="allocation_line_digest",
            )
    content_basis = {
        "ranking_snapshot_id": evaluation.get("ranking_snapshot_id"),
        "allocation_evaluation_id": evaluation.get("allocation_evaluation_id"),
        "allocation_policy_version": evaluation.get("allocation_policy_version"),
        "lines": lines,
    }
    for optional_field in ("authority_mode", "promotion_review_id"):
        if evaluation.get(optional_field) not in (None, ""):
            content_basis[optional_field] = evaluation.get(optional_field)
    expected_content_digest = _stable_json_hash(content_basis)
    if str(evaluation.get("content_digest") or "") != expected_content_digest:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "allocation evaluation integrity check failed",
            "The durable allocation evaluation no longer matches its admitted digest.",
            precondition_failed="allocation_evaluation_id",
        )
    return evaluation
def _ppl_alloc_009_paper_environment_guard() -> None:
    env_name = str(os.getenv("PANTHEON_ENV") or "").strip().lower()
    if (
        env_name != "dev"
        or _bff_auth_mode() != "strict"
        or _bool_from_env(_BFF_AUTH_STUB_ENV, default=False)
        or _bool_from_env("PANTHEON_LIVE_BROKER_ENABLED", default=False)
        or _bool_from_env("PANTHEON_CANARY_EXECUTION_ENABLED", default=False)
    ):
        raise _bff_error(
            403,
            ErrorCode.PRECONDITION_FAILED,
            "Governed paper allocation simulation is unavailable",
            (
                "The paper-only authority requires strict dev auth with both "
                "live broker and canary execution disabled."
            ),
            precondition_failed="paper_simulation_environment",
            suggestion=(
                "Use the accepted strict dev BFF with "
                "PANTHEON_LIVE_BROKER_ENABLED=false and "
                "PANTHEON_CANARY_EXECUTION_ENABLED=false"
            ),
        )
def _ppl_alloc_009_paper_rebalance_authority(
    cmd: OperatorCommand,
) -> bool:
    if cmd.command != CommandType.APPROVED_APPLY:
        return False
    rebalance = read_store.get_rebalance(cmd.target.id)
    if not isinstance(rebalance, dict):
        return False
    policy_version = str(
        rebalance.get("allocation_policy_version") or ""
    ).strip()
    if policy_version != _PPL_ALLOC_009_PAPER_POLICY_VERSION:
        return False

    _ppl_alloc_009_paper_environment_guard()
    lines = [
        line
        for line in rebalance.get("lines") or []
        if isinstance(line, dict)
    ]
    evaluation_id = str(
        rebalance.get("allocation_evaluation_id") or ""
    ).strip()
    evaluation = _pm12_allocation_evaluation_record(evaluation_id)
    expected_digests = {
        str(line.get("allocation_line_digest") or "").strip()
        for line in evaluation.get("lines") or []
        if isinstance(line, dict)
    }
    actual_digests = {
        str(line.get("allocation_line_digest") or "").strip()
        for line in lines
    }
    if (
        len(lines) != 1
        or len(expected_digests) != 1
        or actual_digests != expected_digests
        or str(evaluation.get("allocation_policy_version") or "")
        != _PPL_ALLOC_009_PAPER_POLICY_VERSION
        or str(evaluation.get("authority_mode") or "")
        != _PPL_ALLOC_009_PAPER_AUTHORITY_MODE
        or not str(evaluation.get("promotion_review_id") or "").strip()
    ):
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_FAILED,
            "Paper rebalance authority is invalid",
            "The persisted rebalance no longer matches its admitted paper evaluation.",
            precondition_failed="paper_simulation_lineage",
        )
    line = lines[0]
    pool_id = str(rebalance.get("capital_pool_id") or "").strip()
    binding_id = str(line.get("binding_id") or "").strip()
    if (
        str(line.get("stage") or "").strip().lower() != "paper_running"
        or str(line.get("capital_scope") or "").strip().lower()
        != "paper_ledger"
        or str(line.get("capital_pool_id") or "").strip() != pool_id
        or str(line.get("capital_sleeve_id") or "").strip()
        or not str(line.get("paper_ledger_id") or "").strip()
        or not binding_id
        or line.get("paper_allocation_eligible") is not True
        or line.get("live_capital_side_effects") is not False
        or str(line.get("authority_mode") or "")
        != _PPL_ALLOC_009_PAPER_AUTHORITY_MODE
        or str(line.get("promotion_review_id") or "")
        != str(evaluation.get("promotion_review_id") or "")
    ):
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_FAILED,
            "Paper rebalance scope is invalid",
            "The admitted paper rebalance contains a non-paper or unbound allocation line.",
            precondition_failed="paper_simulation_scope",
        )

    pool = read_store.get_capital_pool(pool_id)
    metadata = (
        pool.get("metadata")
        if isinstance(pool, dict) and isinstance(pool.get("metadata"), dict)
        else {}
    )
    bindings = [
        binding
        for binding in read_store.list_bindings(
            persona_id=str(line.get("persona_id") or "").strip(),
            capital_pool_id=pool_id,
            role="paper_owner",
        )
        if str(binding.get("binding_id") or binding.get("id") or "").strip()
        == binding_id
        and str(binding.get("status") or binding.get("validity") or "")
        .strip()
        .lower()
        in {"active", "ready", "bound"}
        and str(binding.get("allowed_deployment_scope") or "").strip().lower()
        == "paper"
        and not str(binding.get("capital_sleeve_id") or "").strip()
    ]
    if (
        not isinstance(pool, dict)
        or str(pool.get("status") or "").strip().lower() != "active"
        or metadata.get("internal") is not True
        or str(metadata.get("execution_context") or "").strip().lower()
        != "paper"
        or len(bindings) != 1
    ):
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_FAILED,
            "Paper rebalance authority is no longer active",
            "The internal paper pool or its unique paper_owner binding changed.",
            precondition_failed="paper_simulation_binding",
        )
    return True
_STRATEGY_BFF_LIFECYCLE_MAP = {
    "draft": "draft",
    "candidate": "review",
    "review": "review",
    "approved": "approved",
    "active": "deployed",
    "deployed": "deployed",
    "paused": "paused",
    "retired": "retired",
    "paper": "paper_running",
    "paper_running": "paper_running",
    "canary": "canary_running",
    "canary_running": "canary_running",
    "canary_authorized_not_started": "canary_authorized_not_started",
    "live": "live_running",
    "live_running": "live_running",
    "needs_human_approval": "needs_human_approval",
    "rollback_required": "rollback_required",
    "stopped": "stopped",
    "failed": "failed",
    "provisioning": "provisioning",
    "provisioning_failed": "failed",
}
_PERSONA_OPERATIONAL_LIFECYCLE_STATES = frozenset({
    "active",
    "deployed",
    "ready",
    "running",
    "paper",
    "paper_running",
    "canary",
    "canary_running",
    "live",
    "live_running",
})
def _is_persona_lifecycle_operational(value: Any) -> bool:
    return str(value or "").strip().lower() in _PERSONA_OPERATIONAL_LIFECYCLE_STATES
_STRATEGY_BFF_RISK_MAP = {
    "info": "info",
    "low": "low",
    "medium": "medium",
    "moderate": "medium",
    "high": "high",
    "critical": "critical",
}
_STRATEGY_PERSONA_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_STRATEGY_BFF_OVERLAY: Dict[str, Dict[str, Any]] = {}
_STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_PERSONA_BFF_OVERLAY: Dict[str, Dict[str, Any]] = {}
_PERSONA_PROVISIONING_STORE = None
_PERSONA_PROVISIONING_STORE_LOCK = threading.Lock()
_PERSONA_FIRST_EVALUATION_WORKFLOW_ID = "pantheon.persona.first-evaluation"
def _persona_provisioning_store():
    """Lazily bootstrap the durable cross-replica coordination ledger."""
    global _PERSONA_PROVISIONING_STORE
    if _PERSONA_PROVISIONING_STORE is not None:
        return _PERSONA_PROVISIONING_STORE
    with _PERSONA_PROVISIONING_STORE_LOCK:
        if _PERSONA_PROVISIONING_STORE is None:
            _PERSONA_PROVISIONING_STORE = make_persona_provisioning_store()
    return _PERSONA_PROVISIONING_STORE
class _PersonaOwnerHttpTransport:
    """Strict synchronous transport to canonical provisioning owner APIs."""

    _OWNER_ENVIRONMENTS = {
        "capital": ("PANTHEON_CAPITAL_API_URL", "PANTHEON_CAPITAL_SERVICE_URL"),
        "registry": ("PANTHEON_REGISTRY_API_URL", "PANTHEON_REGISTRY_URL"),
        "governance": (
            "PANTHEON_GOVERNANCE_APPROVAL_API_URL",
            "PANTHEON_GOVERNANCE_SERVICE_URL",
        ),
        "deployment": ("PANTHEON_DEPLOYMENT_API_URL", "PANTHEON_DEPLOYMENT_SERVICE_URL"),
    }

    @classmethod
    def _url(cls, owner: str, path: str) -> str:
        env_names = cls._OWNER_ENVIRONMENTS.get(owner)
        if env_names is None:
            raise RuntimeError(f"Unknown Persona provisioning owner: {owner}")
        for env_name in env_names:
            base = os.getenv(env_name, "").strip().rstrip("/")
            if base:
                return f"{base}{path}"
        raise RuntimeError(
            f"Persona provisioning owner {owner} is unconfigured; set {env_names[0]}"
        )

    def get(self, owner: str, path: str) -> Optional[Dict[str, Any]]:
        try:
            value = _get_json(self._url(owner, path))
        except urllib_error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        if not isinstance(value, dict):
            raise RuntimeError(f"{owner} GET {path} returned a non-object receipt")
        return value

    def post(self, owner: str, path: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        value = _post_json(self._url(owner, path), dict(payload))
        if not isinstance(value, dict):
            raise RuntimeError(f"{owner} POST {path} returned a non-object receipt")
        return value

    def patch(self, owner: str, path: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        request = urllib_request.Request(
            self._url(owner, path),
            data=json.dumps(dict(payload)).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="PATCH",
        )
        timeout = max(1, int(os.getenv("PANTHEON_COMMAND_TIMEOUT_SECONDS", "30")))
        with urllib_request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError(f"{owner} PATCH {path} returned a non-object receipt")
        return value
def _strategy_persona_idempotency_check(
    resolved_key: str,
    request_hash: str,
) -> Optional[Dict[str, Any]]:
    existing = _STRATEGY_PERSONA_BFF_IDEMPOTENCY.get(resolved_key)
    if existing is None:
        return None
    if existing.get("request_hash") != request_hash:
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {resolved_key!r} is bound to a different request hash",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        )
    return deepcopy(existing.get("result"))
def _strategy_persona_action_command(
    *,
    entity_type: ObjectType,
    entity_id: str,
    action_id: str,
    resolved_key: str,
    identity: OperatorIdentity,
    payload: Dict[str, Any],
    command_type: CommandType,
) -> Dict[str, Any]:
    """Submit a strategy / persona resource action through the command store
    and return the final command envelope.

    The /bff/strategies/{id}/actions/{actionId} and /bff/personas/{id}/actions/{actionId}
    endpoints accept action ids declared in the canonical action catalog
    (see action_catalog.py). Idempotency is enforced through the
    `_STRATEGY_PERSONA_BFF_IDEMPOTENCY` ledger so callers receive a stable
    receipt on safe retries.
    """
    request_hash = _stable_json_hash(
        {
            "route": f"POST /bff/{entity_type.value.lower()}/{{id}}/actions",
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "action_id": action_id,
            "payload": payload,
        }
    )
    cached = _strategy_persona_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached

    catalog_entry = get_catalog_entry(command_type.value)
    staleness_warning = _check_read_surface_state()
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()
    target = TargetObject(type=entity_type, id=entity_id)
    audit_action = _foundation_audit_for_command_record(
        identity=identity,
        command_type=command_type,
        target_type=entity_type,
        target_id=entity_id,
        payload={"action_id": action_id, **payload},
        reason=str(payload.get("reason") or action_id or command_type.value),
        command_id=command_id,
        idempotency_key=resolved_key,
        route=f"POST /bff/{entity_type.value}/{entity_id}/actions/{action_id}",
        metadata={"action_id": action_id, "catalog_entry": catalog_entry.action_id if catalog_entry else None},
    )
    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "action_id": action_id,
        "preconditions_checked": ["authentication", "authorization", "idempotency"],
        "timestamp": submitted_at,
        "idempotency_key": resolved_key,
        "request_hash": request_hash,
        "catalog_entry": catalog_entry.action_id if catalog_entry else None,
    }
    foundation_ctx = {
        "idempotency_record": {
            "idempotency_key": resolved_key,
            "request_hash": request_hash,
            "operation_type": f"bff.{command_type.value}",
            "target_ref": f"{entity_type.value}:{entity_id}",
            "trace_id": audit_action.trace_id,
        },
        "audit_action": audit_action.to_dict(),
    }
    audit_record["foundation"] = foundation_ctx
    command_store.submit_command(
        command_id=command_id,
        command_type=command_type,
        target=target,
        submitted_at=submitted_at,
        params={"action_id": action_id, **payload},
        audit_context=audit_record,
        foundation_context=foundation_ctx,
    )
    result = _project_final_command_response(
        command_id=command_id,
        command=command_type,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )
    payload_dump: Dict[str, Any]
    if hasattr(result, "model_dump"):
        payload_dump = result.model_dump(mode="json")
    elif isinstance(result, dict):
        payload_dump = result
    else:
        payload_dump = {"data": result}
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY[resolved_key] = {
        "request_hash": request_hash,
        "result": payload_dump,
    }
    return payload_dump
def _normalize_lifecycle_state(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _STRATEGY_BFF_LIFECYCLE_MAP.get(text, "draft")
def _normalize_risk_level(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _STRATEGY_BFF_RISK_MAP.get(text, "medium")
def _deployment_url(path: str) -> str:
    base = os.getenv("PANTHEON_DEPLOYMENT_API_URL", "").strip().rstrip("/")
    if not base:
        base = "http://deployment:8095"
    return f"{base}{path}"
def _checkpoint_persona_provisioning_readback(
    *,
    persona_id: str,
    metadata: Dict[str, Any],
    state: str,
    runtime_binding_id: str,
    runtime_id: str,
    authoritative_readback: Optional[Mapping[str, Any]] = None,
    failure_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist one terminal decision and return its durable replay outcome."""
    tenant_id = str(metadata.get("tenant_id") or "").strip()
    idempotency_key = str(metadata.get("provisioning_idempotency_key") or "").strip()
    if not tenant_id or not idempotency_key:
        return {"committed": False, "ledger_state": None}
    lease_owner = f"persona-readback:{uuid.uuid4().hex}"
    store = None
    record = None
    try:
        store = _persona_provisioning_store()
        record = store.acquire(
            tenant_id,
            idempotency_key,
            lease_owner=lease_owner,
            lease_seconds=max(
                60,
                int(os.getenv("PANTHEON_PERSONA_PROVISIONING_LEASE_SECONDS", "180")),
            ),
        )
        if record is None:
            return {"committed": False, "ledger_state": None}
        desired_terminal_state = {
            "paper_running": "succeeded",
            "provisioning_failed": "failed",
        }.get(state)
        if desired_terminal_state is None:
            store.release(record, lease_owner=lease_owner)
            return {"committed": False, "ledger_state": None}
        if record.state in {"succeeded", "failed", "compensated"}:
            compatible = record.state == desired_terminal_state or (
                desired_terminal_state == "failed" and record.state == "compensated"
            )
            schedule_cleanup = None
            cleanup_error = None
            if record.state in {"failed", "compensated"}:
                try:
                    schedule_cleanup = _remove_persona_cron_required(persona_id)
                    record.references["first_evaluation_schedule_cleanup"] = deepcopy(
                        schedule_cleanup
                    )
                except Exception as exc:
                    cleanup_error = str(exc) or exc.__class__.__name__
                    record.references["first_evaluation_schedule_cleanup"] = {
                        "status": "pending",
                        "registered": None,
                        "terminal_reason": cleanup_error,
                    }
            # A terminal ledger release is atomic, so its references and
            # compensation already belong to that decision.  Preserve them
            # verbatim on replay; in particular, never turn a compensated
            # record back into failed or reverse an earlier outcome.
            store.release(record, lease_owner=lease_owner)
            return {
                "committed": compatible,
                "ledger_state": record.state,
                "terminal_replay": True,
                "failure_reason": str(
                    (record.error or {}).get("terminal_reason")
                    or (record.error or {}).get("reason")
                    or ""
                ),
                "schedule_cleanup": deepcopy(schedule_cleanup),
                "schedule_cleanup_error": cleanup_error,
                "references": deepcopy(record.references),
                "result": deepcopy(record.result),
            }
        if runtime_binding_id:
            record.references["runtime_binding_id"] = runtime_binding_id
        if runtime_id:
            record.references["runtime_id"] = runtime_id
        if state == "paper_running":
            if not isinstance(authoritative_readback, Mapping):
                store.release(record, lease_owner=lease_owner)
                return {"committed": False, "ledger_state": record.state}
            record.references["authoritative_readback"] = deepcopy(
                dict(authoritative_readback)
            )
        schedule_cleanup = None
        cleanup_error = None
        if state == "provisioning_failed":
            # Destructive cleanup happens while the terminal ledger lease is
            # held.  A concurrent success decision therefore cannot race with
            # removal of the schedule it just proved authoritative.  Cleanup
            # unavailability must not erase the durable terminal decision:
            # persist a retryable cleanup receipt and let later controller
            # passes finish the fail-closed removal.
            try:
                schedule_cleanup = _remove_persona_cron_required(persona_id)
                record.references["first_evaluation_schedule_cleanup"] = deepcopy(
                    schedule_cleanup
                )
            except Exception as exc:
                cleanup_error = str(exc) or exc.__class__.__name__
                record.references["first_evaluation_schedule_cleanup"] = {
                    "status": "pending",
                    "registered": None,
                    "terminal_reason": cleanup_error,
                }
        if state == "paper_running":
            record.state = "succeeded"
            record.current_step = "authoritative_readback_complete"
            record.error = None
            record.result = {
                "status": "paper_running",
                "paper_running": True,
                "authoritative_readback": deepcopy(dict(authoritative_readback or {})),
                "recorded_at": utc_now(),
            }
        elif state == "provisioning_failed":
            record.state = "failed"
            record.current_step = "authoritative_readback_failed"
            record.error = {
                "code": "PERSONA_PROVISIONING_READBACK_FAILED",
                "reason": failure_reason or "authoritative_readback_failed",
                "failed_step": "authoritative_readback",
                "terminal_reason": failure_reason or "authoritative_readback_failed",
                "terminal": True,
                "failed_at": utc_now(),
                "recorded_at": utc_now(),
            }
            record.result = {
                "status": "provisioning_failed",
                "paper_running": False,
                "failure_reason": failure_reason or "authoritative_readback_failed",
                "recorded_at": utc_now(),
            }
        released = store.release(record, lease_owner=lease_owner)
        committed = bool(
            released.state == record.state and released.current_step == record.current_step
        )
        return {
            "committed": committed,
            "ledger_state": released.state,
            "terminal_replay": False,
            "schedule_cleanup": deepcopy(schedule_cleanup),
            "schedule_cleanup_error": cleanup_error,
            "references": deepcopy(released.references),
            "result": deepcopy(released.result),
        }
    except Exception as exc:
        # Owner lifecycle remains fail-closed; inability to persist the mirror
        # is logged and never turns missing readback into success.
        log.warning("Failed to checkpoint Persona provisioning readback: %s", exc)
        if store is not None and record is not None:
            try:
                store.release(record, lease_owner=lease_owner)
            except Exception:
                pass
        return {
            "committed": False,
            "ledger_state": None,
            "terminal_replay": False,
            "error": str(exc) or exc.__class__.__name__,
        }
def _append_persona_reconcile_diagnostic(
    diagnostics: Optional[List[str]],
    dependency: str,
) -> None:
    if diagnostics is not None and dependency not in diagnostics:
        diagnostics.append(dependency)
def _persist_persona_provisioning_terminal_transition(
    persona_id: str,
    *,
    lifecycle_state: str,
    metadata: Mapping[str, Any],
) -> Dict[str, Any]:
    """Persist a terminal provisioning projection outside the read surface."""
    return persona_reconciliation_mutation_port.persist_terminal_transition(
        persona_id,
        lifecycle_state=lifecycle_state,
        metadata=metadata,
    )
def _materialize_terminal_persona_provisioning_ledger(
    persona_id: str,
    raw: Dict[str, Any],
    *,
    diagnostics: Optional[List[str]] = None,
) -> Optional[str]:
    """Replay a durable terminal decision before consulting mutable owners.

    The ledger release and Persona projection are separate durable writes.  A
    process crash between them must not leave the Persona in ``provisioning``
    or allow newer owner observations to reverse the released decision.
    ``None`` means the ledger is not terminal; a returned lifecycle is final
    for this controller pass.
    """

    metadata = raw.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    tenant_id = str(metadata.get("tenant_id") or "").strip()
    idempotency_key = str(metadata.get("provisioning_idempotency_key") or "").strip()
    if not tenant_id or not idempotency_key:
        return None
    try:
        record = _persona_provisioning_store().get(tenant_id, idempotency_key)
    except Exception as exc:
        log.warning("Failed to read Persona provisioning ledger for %s: %s", persona_id, exc)
        _append_persona_reconcile_diagnostic(diagnostics, "provisioning_ledger")
        return None
    if record is None or record.state not in {"succeeded", "failed", "compensated"}:
        return None

    references = record.references if isinstance(record.references, dict) else {}
    desired_state = (
        "paper_running" if record.state == "succeeded" else "provisioning_failed"
    )
    checkpoint = _checkpoint_persona_provisioning_readback(
        persona_id=persona_id,
        metadata=metadata,
        state=desired_state,
        runtime_binding_id=str(references.get("runtime_binding_id") or "").strip(),
        runtime_id=str(references.get("runtime_id") or "").strip(),
        authoritative_readback=(
            references.get("authoritative_readback")
            if isinstance(references.get("authoritative_readback"), Mapping)
            else None
        ),
        failure_reason=str(
            (record.error or {}).get("terminal_reason")
            or (record.error or {}).get("reason")
            or "durable_ledger_terminal_failure"
        ),
    )
    if not checkpoint.get("committed"):
        _append_persona_reconcile_diagnostic(diagnostics, "provisioning_ledger")
        return "provisioning"

    ledger_state = str(checkpoint.get("ledger_state") or "")
    durable_references = checkpoint.get("references")
    durable_references = (
        durable_references if isinstance(durable_references, Mapping) else {}
    )
    metadata_updates: Dict[str, Any] = {}
    runtime_binding_id = str(
        durable_references.get("runtime_binding_id") or ""
    ).strip()
    runtime_id = str(durable_references.get("runtime_id") or "").strip()

    if ledger_state == "succeeded":
        durable_readback = durable_references.get("authoritative_readback")
        durable_result = checkpoint.get("result")
        if (
            not runtime_binding_id
            or not runtime_id
            or not isinstance(durable_readback, Mapping)
            or not isinstance(durable_result, Mapping)
            or durable_result.get("paper_running") is not True
            or durable_result.get("status") != "paper_running"
        ):
            _append_persona_reconcile_diagnostic(diagnostics, "provisioning_ledger")
            return "provisioning"
        new_state = "paper_running"
        metadata_updates.update(
            {
                "paper_runtime_state": "running",
                "runtime_binding_id": runtime_binding_id,
                "runtime_id": runtime_id,
                "provisioning_authoritative_readback": deepcopy(
                    dict(durable_readback)
                ),
            }
        )
    elif ledger_state in {"failed", "compensated"}:
        new_state = "provisioning_failed"
        metadata_updates["provisioning_failure_reason"] = (
            checkpoint.get("failure_reason") or "durable_ledger_terminal_failure"
        )
        schedule_cleanup = checkpoint.get("schedule_cleanup")
        if isinstance(schedule_cleanup, Mapping):
            metadata_updates["first_evaluation_schedule_cleanup"] = deepcopy(
                dict(schedule_cleanup)
            )
        elif checkpoint.get("schedule_cleanup_error"):
            _append_persona_reconcile_diagnostic(diagnostics, "persona_cron")
            metadata_updates["first_evaluation_schedule_cleanup"] = {
                "status": "pending",
                "registered": None,
                "terminal_reason": checkpoint["schedule_cleanup_error"],
            }
        compensation = _reconcile_persona_provisioning_compensation(
            {**metadata, **metadata_updates}
        )
        if compensation is not None:
            metadata_updates["provisioning_compensation"] = compensation
            if compensation.get("status") in {"failed", "pending"}:
                _append_persona_reconcile_diagnostic(
                    diagnostics, "provisioning_compensation"
                )
    else:
        _append_persona_reconcile_diagnostic(diagnostics, "provisioning_ledger")
        return "provisioning"

    _persist_persona_provisioning_terminal_transition(
        persona_id,
        lifecycle_state=new_state,
        metadata=metadata_updates,
    )
    if persona_id in _PERSONA_BFF_OVERLAY:
        _PERSONA_BFF_OVERLAY[persona_id]["state"] = _normalize_lifecycle_state(new_state)
        _PERSONA_BFF_OVERLAY[persona_id]["lifecycleStatus"] = new_state
        if runtime_binding_id:
            _PERSONA_BFF_OVERLAY[persona_id]["runtimeBindingId"] = runtime_binding_id
        if runtime_id:
            _PERSONA_BFF_OVERLAY[persona_id]["runtimeId"] = runtime_id
    raw["lifecycle_state"] = new_state
    raw["status"] = new_state
    raw.setdefault("metadata", {}).update(metadata_updates)
    raw["metadata"]["lifecycle_state"] = new_state
    return new_state
def _reconcile_persona_provisioning_compensation(
    metadata: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Resume fail-closed Deployment/Capital compensation from durable state."""

    tenant_id = str(metadata.get("tenant_id") or "").strip()
    idempotency_key = str(metadata.get("provisioning_idempotency_key") or "").strip()
    if not tenant_id or not idempotency_key:
        return None
    store = _persona_provisioning_store()
    record = store.get(tenant_id, idempotency_key)
    if record is None:
        return None
    coordinator = PersonaProvisioningCoordinator(
        store=store,
        transport=_PersonaOwnerHttpTransport(),
        schedule_registrar=_register_persona_cron_required,
        lease_owner=f"persona-compensation:{uuid.uuid4().hex}",
        lease_seconds=max(
            30,
            int(os.getenv("PANTHEON_PERSONA_PROVISIONING_LEASE_SECONDS", "180")),
        ),
    )
    try:
        reconciled = coordinator.reconcile_failure_compensation(record)
    except Exception as exc:
        log.warning("Failed to reconcile Persona provisioning compensation: %s", exc)
        return {
            "status": "pending",
            "terminal_reason": str(exc) or exc.__class__.__name__,
        }
    return {
        "ledger_state": reconciled.state,
        "current_step": reconciled.current_step,
        **deepcopy(reconciled.compensation or {"status": "not_required"}),
    }
def _evaluate_persona_provisioning_status(
    persona_id: str,
    raw: Dict[str, Any],
    *,
    all_bindings: Optional[Dict[str, Dict[str, Any]]] = None,
    all_cron_registrations: Optional[Set[Tuple[str, str]]] = None,
    all_monitoring_sessions: Optional[List[Dict[str, Any]]] = None,
    diagnostics: Optional[List[str]] = None,
) -> str:
    metadata = raw.get("metadata") or {}
    current_state = raw.get("lifecycle_state") or raw.get("state")
    if current_state == "provisioning_failed":
        terminal_updates: Dict[str, Any] = {}
        try:
            schedule_cleanup = _remove_persona_cron_required(persona_id)
            terminal_updates["first_evaluation_schedule_cleanup"] = schedule_cleanup
        except Exception as exc:
            log.warning(
                "Failed to reconcile terminal first-evaluation cleanup for %s: %s",
                persona_id,
                exc,
            )
            terminal_updates["first_evaluation_schedule_cleanup"] = {
                "status": "pending",
                "registered": None,
                "terminal_reason": str(exc) or exc.__class__.__name__,
            }
            _append_persona_reconcile_diagnostic(diagnostics, "persona_cron")
        compensation = _reconcile_persona_provisioning_compensation(metadata)
        if compensation is not None:
            terminal_updates["provisioning_compensation"] = compensation
        changed_updates = {
            key: value
            for key, value in terminal_updates.items()
            if metadata.get(key) != value
        }
        if changed_updates:
            _persist_persona_provisioning_terminal_transition(
                persona_id,
                lifecycle_state="provisioning_failed",
                metadata=changed_updates,
            )
            raw.setdefault("metadata", {}).update(changed_updates)
        return "provisioning_failed"
    if current_state not in ("provisioning", "draft", "paper_running"):
        return str(current_state or "")
    if current_state == "paper_running":
        return "paper_running"
    if current_state != "provisioning":
        return str(current_state or "")

    terminal_replay = _materialize_terminal_persona_provisioning_ledger(
        persona_id,
        raw,
        diagnostics=diagnostics,
    )
    if terminal_replay is not None:
        return terminal_replay

    # Deployment owns admission and the runtime identity.  Never infer a
    # RuntimeBinding id from the distinct PersonaCapitalBinding id.
    persona_capital_binding_id = str(
        metadata.get("persona_capital_binding_id") or metadata.get("binding_id") or ""
    ).strip()
    tenant_id = str(metadata.get("tenant_id") or "").strip()
    capital_pool_id = str(
        metadata.get("internal_paper_capital_pool_id")
        or metadata.get("legacy_paper_capital_pool_id")
        or ""
    ).strip()
    plan_id = str(metadata.get("deployment_plan_id") or "").strip()
    expected_saga_id = str(metadata.get("deployment_saga_id") or "").strip()
    binding_id = str(metadata.get("runtime_binding_id") or "").strip()
    runtime_id = str(metadata.get("runtime_id") or "").strip()
    projection: Dict[str, Any] = {}
    projection_failed = False
    if plan_id:
        try:
            candidate = _get_json(
                _deployment_url(f"/api/deployment/plans/{quote(plan_id, safe='')}/projection")
            )
            projection = candidate if isinstance(candidate, dict) else {}
        except Exception as exc:
            log.warning("Failed to query Deployment projection %s for %s: %s", plan_id, persona_id, exc)
            _append_persona_reconcile_diagnostic(diagnostics, "deployment")

    projection_saga = projection.get("deployment_saga")
    projection_saga = projection_saga if isinstance(projection_saga, dict) else {}
    projected_saga_id = str(
        projection.get("deployment_saga_id") or projection_saga.get("saga_id") or ""
    ).strip()
    projected_plan_id = str(projection.get("plan_id") or "").strip()
    projection_observed = bool(
        projection
        and projected_plan_id == plan_id
        and projected_saga_id
        and (not expected_saga_id or projected_saga_id == expected_saga_id)
    )
    projection_identity_failed = bool(projection) and bool(
        (projected_plan_id and projected_plan_id != plan_id)
        or (
            projected_saga_id
            and expected_saga_id
            and projected_saga_id != expected_saga_id
        )
    )

    saga_status = str(
        projection.get("deployment_saga_status")
        or projection_saga.get("status")
        or ""
    ).strip().lower()
    saga_progress = projection.get("deployment_saga_progress")
    saga_progress = saga_progress if isinstance(saga_progress, dict) else {}
    progress_status = str(saga_progress.get("progress_status") or "").strip().lower()
    projection_complete = (
        saga_status == "completed" and progress_status == "completed"
    )
    projection_failed = saga_status in {
        "failed",
        "aborted",
        "compensating",
        "compensated",
    } or progress_status in {
        "failed",
        "blocked",
        "compensating",
    }

    # Deployment projection proves saga admission, but Runtime Manager is the
    # sole RuntimeBinding authority. Embedded projection/file snapshots never
    # satisfy lifecycle readback.
    projected_binding = projection.get("runtime_binding")
    projected_binding = projected_binding if isinstance(projected_binding, dict) else {}
    projected_binding_id = str(
        projection.get("runtime_binding_id")
        or projected_binding.get("binding_id")
        or ""
    ).strip()
    projected_runtime_id = str(
        projection.get("runtime_id") or projected_binding.get("runtime_id") or ""
    ).strip()

    binding: Optional[Dict[str, Any]] = None
    binding_ok = False
    binding_failed = False
    authoritative_bindings: List[Dict[str, Any]] = []
    if plan_id:
        try:
            if all_bindings is not None:
                authoritative_bindings = [
                    value
                    for value in all_bindings.values()
                    if isinstance(value, dict)
                    and str(value.get("plan_id") or "") == plan_id
                ]
            else:
                client = _runtime_manager_client()
                authoritative_bindings = [
                    value
                    for value in client.list_by_plan(plan_id)
                    if isinstance(value, dict)
                ]
            active_bindings = [
                value
                for value in authoritative_bindings
                if str(value.get("state") or value.get("status") or "").lower()
                in {"active", "running", "ok"}
            ]
            if len(active_bindings) == 1:
                binding = active_bindings[0]
                authoritative_binding_id = str(
                    binding.get("binding_id") or binding.get("id") or ""
                ).strip()
                authoritative_runtime_id = str(binding.get("runtime_id") or "").strip()
                binding_metadata = binding.get("metadata")
                binding_metadata = binding_metadata if isinstance(binding_metadata, dict) else {}
                identity_matches = all((
                    bool(authoritative_binding_id),
                    authoritative_binding_id.startswith("rb-"),
                    bool(authoritative_runtime_id),
                    str(binding.get("plan_id") or "") == plan_id,
                    str(binding.get("persona_capital_binding_id") or "")
                    == persona_capital_binding_id,
                    str(binding.get("capital_pool_id") or "") == capital_pool_id,
                    str(
                        binding.get("deployment_mode")
                        or binding.get("deployment_stage")
                        or ""
                    ) == "paper",
                    str(binding_metadata.get("persona_id") or "") == persona_id,
                    str(binding_metadata.get("tenant_id") or "") == tenant_id,
                    not binding_id or binding_id == authoritative_binding_id,
                    not runtime_id or runtime_id == authoritative_runtime_id,
                    not projected_binding_id
                    or projected_binding_id == authoritative_binding_id,
                    not projected_runtime_id
                    or projected_runtime_id == authoritative_runtime_id,
                ))
                if identity_matches:
                    binding_id = authoritative_binding_id
                    runtime_id = authoritative_runtime_id
                    binding_ok = True
                else:
                    binding_failed = True
            elif len(active_bindings) > 1:
                binding_failed = True
            elif binding_id and any(
                str(value.get("binding_id") or value.get("id") or "") == binding_id
                for value in (all_bindings or {}).values()
                if isinstance(value, dict)
            ):
                # The expected binding identity exists under another plan.
                binding_failed = True
            elif any(
                str(value.get("state") or value.get("status") or "").lower()
                in {"failed", "stopped", "error"}
                for value in authoritative_bindings
            ):
                binding_failed = True
        except Exception as exc:
            log.warning(
                "Failed to query RuntimeBindings for plan %s / %s: %s",
                plan_id,
                persona_id,
                exc,
            )
            _append_persona_reconcile_diagnostic(diagnostics, "runtime_manager")

    # Require exactly one fresh, active worker joined on the complete identity.
    monitoring_sessions: List[Dict[str, Any]] = []
    worker_identity_conflict = False
    if binding_ok and runtime_id and binding_id:
        try:
            owner_sessions = (
                all_monitoring_sessions
                if all_monitoring_sessions is not None
                else read_store.list_authoritative_paper_runtime_monitoring_sessions()
            )
        except Exception as exc:
            log.warning(
                "Failed to query paper worker sessions for %s: %s",
                persona_id,
                exc,
            )
            _append_persona_reconcile_diagnostic(diagnostics, "paper_runtime_manager")
            owner_sessions = []
        for s in owner_sessions:
            # The paper-fleet reconciler owns worker sessions and joins them to
            # RuntimeBinding by runtime_id + binding_id.  It does not duplicate
            # Persona identity into the session.  Persona identity is instead
            # proven above from the authoritative RuntimeBinding metadata.  If
            # a future session does carry persona_id, treat a conflicting value
            # as fail-closed rather than ignoring it.
            s_pid = str(s.get("persona_id") or "").strip()
            s_rtid = str(s.get("runtime_id") or "").strip()
            s_bid = str(s.get("binding_id") or s.get("runtime_binding_id") or "").strip()
            s_pool_id = str(s.get("capital_pool_id") or "").strip()
            if s_rtid == runtime_id and s_bid == binding_id:
                if (s_pid and s_pid != persona_id) or s_pool_id != capital_pool_id:
                    worker_identity_conflict = True
                else:
                    monitoring_sessions.append(s)

    max_heartbeat_age = max(
        1,
        int(os.getenv("PANTHEON_PERSONA_HEARTBEAT_MAX_AGE_SECONDS", "90")),
    )
    now_dt = datetime.now(timezone.utc)
    live_sessions: List[Dict[str, Any]] = []
    startup_sessions: List[Dict[str, Any]] = []
    current_owner_sessions: List[Dict[str, Any]] = []
    for session in monitoring_sessions:
        status = str(session.get("status") or "").strip().lower()
        staleness = session.get("staleness")
        stale_marker = bool(
            isinstance(staleness, Mapping)
            and (
                str(staleness.get("status") or "").strip().lower() == "stale"
                or staleness.get("reason")
            )
        )
        heartbeat_at = _parse_rfc3339(session.get("last_heartbeat_at"))
        fresh = bool(
            heartbeat_at is not None
            and 0 <= (now_dt - heartbeat_at).total_seconds() <= max_heartbeat_age
        )
        session_id = str(session.get("session_id") or session.get("id") or "").strip()
        current_owner = (
            session_id
            and session.get("active") is not False
            and session.get("ended_at") in (None, "")
            and status not in {"failed", "ended", "error", "stale"}
            and not stale_marker
        )
        if current_owner:
            current_owner_sessions.append(session)
        startup_status = status in {
            "accepted",
            "initializing",
            "pending",
            "queued",
            "starting",
        }
        if (
            current_owner
            and (status == "running" or startup_status)
            and session.get("last_heartbeat_at") in (None, "")
        ):
            startup_sessions.append(session)
        if (
            session_id
            and status == "running"
            and session.get("active") is not False
            and session.get("ended_at") in (None, "")
            and fresh
            and not stale_marker
        ):
            live_sessions.append(session)
    heartbeat_ok = len(live_sessions) == 1
    # Historical ended/stale sessions are expected after worker replacement.
    # They cannot poison one unique fresh owner session.  No fresh successor
    # or multiple current workers is fail-closed once an owner record exists.
    # One exact running owner may briefly precede its first heartbeat; keep
    # that startup race pending and let the provisioning timeout decide if the
    # worker never becomes authoritative.
    startup_pending = (
        len(startup_sessions) == 1 and len(current_owner_sessions) == 1
    )
    heartbeat_failed = worker_identity_conflict or (
        bool(monitoring_sessions) and not heartbeat_ok and not startup_pending
    )

    # The schedule authority must contain the exact first-evaluation workflow.
    cron_ok = False
    authoritative_schedule_readback: Optional[Dict[str, Any]] = None
    try:
        schedule_discovered = all_cron_registrations is None or (
                persona_id,
                _PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
            ) in all_cron_registrations
        if schedule_discovered:
            if (
                projection_observed
                and projection_complete
                and binding_ok
                and runtime_id
                and binding_id
                and capital_pool_id
                and persona_capital_binding_id
            ):
                schedule_receipt = _register_persona_cron_required(
                    persona_id,
                    capital_pool_id,
                    persona_capital_binding_id,
                    runtime_id=runtime_id,
                    runtime_binding_id=binding_id,
                )
                authoritative = schedule_receipt.get("authoritative_readback")
                cron_ok = bool(
                    isinstance(authoritative, dict)
                    and authoritative.get("registered") is True
                    and authoritative.get("persona_id") == persona_id
                    and authoritative.get("workflow_id")
                    == _PERSONA_FIRST_EVALUATION_WORKFLOW_ID
                    and authoritative.get("runtime_id") == runtime_id
                    and authoritative.get("runtime_binding_id") == binding_id
                    and authoritative.get("capital_pool_id") == capital_pool_id
                    and authoritative.get("persona_capital_binding_id")
                    == persona_capital_binding_id
                    and isinstance(authoritative.get("job_id"), str)
                    and bool(authoritative["job_id"].strip())
                    and authoritative.get("request_id")
                    == (
                        f"persona-provisioning:{persona_id}:"
                        f"{_PERSONA_FIRST_EVALUATION_WORKFLOW_ID}"
                    )
                )
                if cron_ok:
                    authoritative_schedule_readback = deepcopy(authoritative)
    except Exception as exc:
        log.warning("Failed to query first-evaluation schedule for %s: %s", persona_id, exc)
        _append_persona_reconcile_diagnostic(diagnostics, "persona_cron")

    # A timed-out attempt is terminal even if stale evidence happens to appear
    # later; recovery must be an explicit retry that acquires the durable lease.
    is_timeout = False
    readback_started_at = metadata.get("provisioning_readback_started_at")
    if readback_started_at:
        try:
            started_at_dt = _parse_rfc3339(readback_started_at)
            timeout_seconds = max(
                1,
                int(os.getenv("PANTHEON_PERSONA_PROVISIONING_TIMEOUT_SECONDS", "600")),
            )
            is_timeout = bool(
                started_at_dt is not None
                and (now_dt - started_at_dt).total_seconds() > timeout_seconds
            )
        except (TypeError, ValueError):
            is_timeout = False

    if (
        projection_failed
        or projection_identity_failed
        or binding_failed
        or heartbeat_failed
        or is_timeout
    ):
        new_state = "provisioning_failed"
    elif (
        projection_observed
        and projection_complete
        and binding_ok
        and heartbeat_ok
        and cron_ok
    ):
        new_state = "paper_running"
    else:
        new_state = "provisioning"

    metadata_updates: Dict[str, Any] = {}
    if binding_ok and binding_id:
        metadata_updates["runtime_binding_id"] = binding_id
    if binding_ok and runtime_id:
        metadata_updates["runtime_id"] = runtime_id
    if new_state == "provisioning_failed":
        failure_reasons = []
        if projection_failed:
            failure_reasons.append("deployment_saga_failed")
        if projection_identity_failed:
            failure_reasons.append("deployment_projection_identity_mismatched")
        if binding_failed:
            failure_reasons.append("runtime_binding_failed_or_mismatched")
        if heartbeat_failed:
            failure_reasons.append("paper_worker_failed_stale_or_duplicated")
        if is_timeout:
            failure_reasons.append("provisioning_timeout")
        metadata_updates["provisioning_failure_reason"] = ",".join(failure_reasons)
    elif new_state == "paper_running":
        metadata_updates["paper_runtime_state"] = "running"
        metadata_updates.pop("provisioning_failure_reason", None)

    # The durable ledger is the release barrier for terminal Persona state.
    # If its lease is busy or storage is unavailable, leave the Persona in
    # provisioning so a later controller pass can recover with RPO=0.
    if new_state in {"paper_running", "provisioning_failed"}:
        authoritative_readback: Optional[Dict[str, Any]] = None
        if new_state == "paper_running":
            if (
                not isinstance(binding, Mapping)
                or len(live_sessions) != 1
                or authoritative_schedule_readback is None
            ):
                return "provisioning"
            authoritative_readback = {
                "observed_at": utc_now(),
                "deployment": {
                    "plan_id": plan_id,
                    "saga_id": projected_saga_id,
                    "saga_status": saga_status,
                    "progress_status": progress_status,
                },
                "runtime_binding": deepcopy(dict(binding)),
                "paper_worker": deepcopy(live_sessions[0]),
                "first_evaluation_schedule": deepcopy(
                    authoritative_schedule_readback
                ),
            }
        terminal_checkpoint = _checkpoint_persona_provisioning_readback(
            persona_id=persona_id,
            metadata={**metadata, **metadata_updates},
            state=new_state,
            runtime_binding_id=binding_id,
            runtime_id=runtime_id,
            authoritative_readback=authoritative_readback,
            failure_reason=metadata_updates.get("provisioning_failure_reason"),
        )
        ledger_state = terminal_checkpoint.get("ledger_state")
        if terminal_checkpoint.get("terminal_replay"):
            # The ledger release is the durable lifecycle decision.  A crash
            # between that release and Persona projection must recover the
            # earlier terminal state, never remain stuck in provisioning or
            # reverse the decision from newer observations.
            if ledger_state == "succeeded":
                durable_references = terminal_checkpoint.get("references")
                durable_references = (
                    durable_references
                    if isinstance(durable_references, Mapping)
                    else {}
                )
                durable_readback = durable_references.get("authoritative_readback")
                durable_result = terminal_checkpoint.get("result")
                if (
                    not isinstance(durable_readback, Mapping)
                    or not isinstance(durable_result, Mapping)
                    or durable_result.get("paper_running") is not True
                    or durable_result.get("status") != "paper_running"
                ):
                    return "provisioning"
                binding_id = str(
                    durable_references.get("runtime_binding_id") or ""
                ).strip()
                runtime_id = str(
                    durable_references.get("runtime_id") or ""
                ).strip()
                if not binding_id or not runtime_id:
                    return "provisioning"
                new_state = "paper_running"
                metadata_updates["paper_runtime_state"] = "running"
                metadata_updates["runtime_binding_id"] = binding_id
                metadata_updates["runtime_id"] = runtime_id
                metadata_updates["provisioning_authoritative_readback"] = deepcopy(
                    dict(durable_readback)
                )
                metadata_updates.pop("provisioning_failure_reason", None)
            elif ledger_state in {"failed", "compensated"}:
                new_state = "provisioning_failed"
                metadata_updates["provisioning_failure_reason"] = (
                    terminal_checkpoint.get("failure_reason")
                    or "durable_ledger_terminal_failure"
                )
        elif not terminal_checkpoint.get("committed"):
            return "provisioning"
        if new_state == "paper_running":
            durable_references = terminal_checkpoint.get("references")
            durable_readback = (
                durable_references.get("authoritative_readback")
                if isinstance(durable_references, Mapping)
                else None
            )
            if not isinstance(durable_readback, Mapping):
                return "provisioning"
            metadata_updates["provisioning_authoritative_readback"] = deepcopy(
                dict(durable_readback)
            )
        schedule_cleanup = terminal_checkpoint.get("schedule_cleanup")
        if isinstance(schedule_cleanup, Mapping):
            metadata_updates["first_evaluation_schedule_cleanup"] = deepcopy(
                dict(schedule_cleanup)
            )
        elif terminal_checkpoint.get("schedule_cleanup_error"):
            _append_persona_reconcile_diagnostic(diagnostics, "persona_cron")
            metadata_updates["first_evaluation_schedule_cleanup"] = {
                "status": "pending",
                "registered": None,
                "terminal_reason": terminal_checkpoint["schedule_cleanup_error"],
            }
        if new_state == "provisioning_failed":
            compensation = _reconcile_persona_provisioning_compensation(
                {**metadata, **metadata_updates}
            )
            if compensation is not None:
                metadata_updates["provisioning_compensation"] = compensation
                if compensation.get("status") in {"failed", "pending"}:
                    _append_persona_reconcile_diagnostic(
                        diagnostics, "provisioning_compensation"
                    )

    if new_state != current_state or metadata_updates:
        _persist_persona_provisioning_terminal_transition(
            persona_id,
            lifecycle_state=new_state,
            metadata=metadata_updates,
        )
        if persona_id in _PERSONA_BFF_OVERLAY:
            _PERSONA_BFF_OVERLAY[persona_id]["state"] = _normalize_lifecycle_state(new_state)
            _PERSONA_BFF_OVERLAY[persona_id]["lifecycleStatus"] = new_state
            if binding_id:
                _PERSONA_BFF_OVERLAY[persona_id]["runtimeBindingId"] = binding_id
            if runtime_id:
                _PERSONA_BFF_OVERLAY[persona_id]["runtimeId"] = runtime_id
        raw["lifecycle_state"] = new_state
        raw["status"] = new_state
        raw.setdefault("metadata", {}).update(metadata_updates)
        raw["metadata"]["lifecycle_state"] = new_state

    return new_state
def _project_persona_dto(
    raw: Dict[str, Any],
    *,
    overlay: Optional[Dict[str, Any]] = None,
    routed_strategies: Optional[int] = None,
    all_bindings: Optional[Dict[str, Dict[str, Any]]] = None,
    all_cron_registrations: Optional[Set[Tuple[str, str]]] = None,
    evaluate_provisioning: bool = False,
) -> Dict[str, Any]:
    """Project canonical persona data into execute-plans Persona DTO."""
    persona_id = str(raw.get("persona_id") or raw.get("id") or "")
    if persona_id and evaluate_provisioning:
        _evaluate_persona_provisioning_status(
            persona_id,
            raw,
            all_bindings=all_bindings,
            all_cron_registrations=all_cron_registrations,
        )
    metadata = dict(raw.get("metadata") or {}) if isinstance(raw.get("metadata"), dict) else {}
    archetype = str(
        metadata.get("archetype")
        or raw.get("archetype")
        or raw.get("strategy_family")
        or raw.get("mandate")
        or "generalist"
    )
    capital_mode = str(
        metadata.get("capital_mode")
        or metadata.get("capitalMode")
        or metadata.get("deployment_stage")
        or metadata.get("deploymentStage")
        or ""
    ).strip().lower()
    if capital_mode not in {"paper", "canary", "live"}:
        capital_mode = ""
    metadata_paper_ledger = (
        metadata.get("paper_ledger")
        if isinstance(metadata.get("paper_ledger"), dict)
        else {}
    )
    paper_ledger_id = (
        str(
            metadata.get("paper_ledger_id")
            or metadata.get("paperLedgerId")
            or metadata_paper_ledger.get("id")
            or ""
        ).strip()
        or (f"paper-ledger-{persona_id}" if capital_mode == "paper" and persona_id else None)
    )
    paper_ledger = None
    if paper_ledger_id:
        paper_ledger = dict(metadata_paper_ledger)
        paper_ledger.update({
            "id": paper_ledger_id,
            "mode": paper_ledger.get("mode") or "paper",
            "persona_id": paper_ledger.get("persona_id") or persona_id,
            "is_isolated": bool(paper_ledger.get("is_isolated", True)),
            "isolated": bool(paper_ledger.get("isolated", True)),
        })
    legacy_paper_capital_pool_id = None
    if capital_mode == "paper":
        legacy_paper_capital_pool_id = (
            metadata.get("legacy_paper_capital_pool_id")
            or metadata.get("capital_pool_id")
        )
    capital_pool_id = None if capital_mode == "paper" else metadata.get("capital_pool_id")
    dto: Dict[str, Any] = {
        "id": persona_id,
        "name": raw.get("name") or persona_id,
        "owner": metadata.get("owner") or raw.get("owner") or "pantheon-bff",
        "tenantId": metadata.get("tenant_id"),
        "updatedAt": raw.get("updated_at") or raw.get("created_at") or utc_now(),
        "state": _normalize_lifecycle_state(raw.get("lifecycle_state")),
        "risk": _normalize_risk_level(metadata.get("risk_level")),
        "archetype": archetype,
        "routedStrategies": int(routed_strategies if routed_strategies is not None else 0),
        "successRate": float(metadata.get("success_rate") or 0.0),
        "labelKey": f"persona.{persona_id}" if persona_id else None,
        "lifecycleStatus": str(raw.get("lifecycle_state") or ""),
        "marketScope": list(metadata.get("market_scope") or []),
        "assetClasses": list(metadata.get("asset_classes") or []),
        "paperLedgerId": paper_ledger_id,
        "paperLedger": paper_ledger,
        "legacyPaperCapitalPoolId": legacy_paper_capital_pool_id,
        "capitalPoolId": capital_pool_id,
        "capitalMode": metadata.get("capital_mode") or capital_mode or None,
        "runtimeId": metadata.get("runtime_id") or metadata.get("runtime_binding_id"),
        "runtimeBindingId": metadata.get("runtime_binding_id"),
        "deploymentPlanId": metadata.get("deployment_plan_id"),
        "deploymentStage": metadata.get("deployment_stage"),
        "oodaStage": metadata.get("ooda_stage"),
        "currentWork": metadata.get("current_work"),
        "governanceRequired": bool(metadata.get("governance_required", True)),
        "recommendedGovernanceAction": metadata.get("recommended_governance_action"),
        "riskFlags": list(metadata.get("risk_flags") or []),
        # Real persona identity + trading-character traits (drive the OpenClaw SOUL
        # and let the FE display/edit them).
        "mandate": raw.get("mandate") or "",
        "strategyFamily": raw.get("strategy_family") or "",
        "traits": metadata.get("traits") if isinstance(metadata.get("traits"), dict) else {},
    }
    if dto.get("capitalPoolId") is None:
        dto.pop("capitalPoolId", None)
    if not dto.get("paperLedgerId"):
        dto.pop("paperLedgerId", None)
        dto.pop("paperLedger", None)
    if not dto.get("legacyPaperCapitalPoolId"):
        dto.pop("legacyPaperCapitalPoolId", None)
    for optional_runtime_field in ("runtimeId", "runtimeBindingId"):
        if not dto.get(optional_runtime_field):
            dto.pop(optional_runtime_field, None)
    required_data_sources = (
        raw.get("required_data_sources")
        if isinstance(raw.get("required_data_sources"), list)
        else []
    )
    if isinstance(metadata.get("data_source_status"), dict) or isinstance(metadata.get("data_sources"), list) or required_data_sources:
        data_source_status, data_sources, source_health_bindings = _overlay_source_health_truth(
            metadata.get("data_source_status") if isinstance(metadata.get("data_source_status"), dict) else {},
            metadata.get("data_sources") if isinstance(metadata.get("data_sources"), list) else [],
            required_data_sources=required_data_sources,
        )
        metadata["data_source_status"] = data_source_status
        metadata["data_sources"] = data_sources
        metadata["source_health_bindings"] = source_health_bindings

    for source_key, dto_key in (
        ("data_source_status", "dataSourceStatus"),
        ("data_sources", "dataSources"),
        ("data_source_refs", "dataSourceRefs"),
        ("source_health_bindings", "sourceHealthBindings"),
        ("research_status", "researchStatus"),
        ("research_refs", "researchRefs"),
        ("current_research_projects", "currentResearchProjects"),
    ):
        value = metadata.get(source_key)
        if value is not None:
            dto[dto_key] = json.loads(json.dumps(value))
    if required_data_sources:
        dto["requiredDataSources"] = json.loads(json.dumps(required_data_sources))
    performance = metadata.get("performance") if isinstance(metadata.get("performance"), dict) else {}
    if performance:
        dto["metrics"] = json.loads(json.dumps(performance))
    if overlay:
        for k, v in overlay.items():
            if v is not None:
                dto[k] = v
    return dto
def _routed_strategies_for_persona(persona_id: str) -> int:
    items = read_store.list_strategy_specs(persona_id=persona_id) or []
    return len(items)
def _list_strategy_summaries() -> List[Dict[str, Any]]:
    """Combine canonical strategy_specs with overlay records created via /bff."""
    items = list(read_store.list_strategy_specs() or [])
    seen = {str(item.get("strategy_id") or "") for item in items}
    for sid, overlay in _STRATEGY_BFF_OVERLAY.items():
        if sid in seen:
            continue
        items.append({
            "strategy_id": sid,
            "title": overlay.get("name"),
            "lifecycle_state": overlay.get("state") or "draft",
            "last_modified_at": overlay.get("updatedAt"),
            "owner": overlay.get("owner"),
        })
    return items
def _list_persona_records(tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Combine canonical personas with durable store and overlay records created via /bff."""
    items = list(read_store.list_personas() or [])
    records_by_id: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or item.get("persona_id") or "").strip()
        if pid:
            records_by_id[pid] = dict(item)
    clean_tenant = str(tenant_id or "").strip()
    store = _persona_provisioning_store()
    try:
        if clean_tenant:
            prov_records = store.list_by_tenant(clean_tenant)
        else:
            prov_records = store.list_all()
    except Exception as exc:
        log.warning("Persona provisioning store list failed for dependency %s", "persona_provisioning_store")
        raise _bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Persona durable readback is unavailable",
            "Authoritative provisioning store is unreachable or degraded",
            precondition_failed="persona_provisioning_store",
            suggestion="Inspect persona provisioning persistence health before retrying",
        ) from exc

    for record in prov_records:
        persona_proj, meta_proj = _persona_record_for_provisioning(
            record,
            payload=record.request_payload,
            owner=str(record.request_payload.get("requested_by") or "pantheon-bff"),
        )
        pid = record.persona_id
        if pid not in records_by_id:
            records_by_id[pid] = persona_proj
        else:
            existing = records_by_id[pid]
            existing_meta = dict(existing.get("metadata") or {}) if isinstance(existing.get("metadata"), dict) else {}
            for k, v in meta_proj.items():
                if v is not None and (k not in existing_meta or not existing_meta[k]):
                    existing_meta[k] = v
            existing["metadata"] = existing_meta
            if record.state == "succeeded" and existing.get("lifecycle_state") in {None, "draft", "provisioning"}:
                existing["lifecycle_state"] = "paper_running"

    for pid, overlay in _PERSONA_BFF_OVERLAY.items():
        if pid not in records_by_id:
            records_by_id[pid] = {
                "id": pid,
                "persona_id": pid,
                "name": overlay.get("name"),
                "lifecycle_state": overlay.get("state") or "draft",
                "updated_at": overlay.get("updatedAt"),
                "metadata": {
                    "archetype": overlay.get("archetype"),
                    "owner": overlay.get("owner"),
                    "risk_level": overlay.get("risk"),
                    "tenant_id": overlay.get("tenantId"),
                },
            }

    result = list(records_by_id.values())
    if clean_tenant:
        # Registry provenance is not tenant ownership.  A tenant-scoped
        # read admits only an explicit matching owner tenant; tenantless
        # registry rows are catalog or malformed data and fail closed.
        result = [
            raw
            for raw in result
            if _persona_record_tenant_id(raw) == clean_tenant
        ]
    result.sort(
        key=lambda raw: (
            str(raw.get("created_at") or raw.get("updated_at") or ""),
            str(raw.get("persona_id") or raw.get("id") or ""),
        )
    )
    return result
class PersonaDirectorySnapshot:
    tenant_id: str
    snapshot_at: str
    records_by_id: Dict[str, Dict[str, Any]]
    catalog_defaults_by_id: Dict[str, Dict[str, Any]]
def _get_persona_directory_snapshot(
    tenant_id: Optional[str] = None,
    *,
    snapshot_at: Optional[str] = None,
) -> PersonaDirectorySnapshot:
    snapshot_timestamp = snapshot_at or utc_now()
    clean_tenant = str(tenant_id or "").strip()
    records_by_id: Dict[str, Dict[str, Any]] = {}
    catalog_defaults_by_id: Dict[str, Dict[str, Any]] = {}

    for raw in _list_persona_records(clean_tenant):
        if not isinstance(raw, dict):
            continue
        rec_tenant = _persona_record_tenant_id(raw)
        if clean_tenant and rec_tenant != clean_tenant:
            continue
        pid = str(raw.get("persona_id") or raw.get("id") or "").strip()
        if pid:
            records_by_id[pid] = raw

    try:
        defaults = read_store.list_personas(include_market_persona_defaults=True) or []
    except Exception:
        defaults = []

    for default_record in defaults:
        if not isinstance(default_record, dict):
            continue
        did = str(default_record.get("persona_id") or default_record.get("id") or "").strip()
        if did and did not in records_by_id:
            catalog_defaults_by_id[did] = {
                **default_record,
                "record_kind": "catalog_default",
                "detail_available": False,
                "admission_state": "not_admitted",
            }

    return PersonaDirectorySnapshot(
        tenant_id=clean_tenant,
        snapshot_at=snapshot_timestamp,
        records_by_id=records_by_id,
        catalog_defaults_by_id=catalog_defaults_by_id,
    )
def _management_record_id(record: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""
def _management_as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
def _management_first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None
def _management_dict_value(record: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None
def _management_nested_dict(record: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    for key in keys:
        value = record.get(key)
        if isinstance(value, dict):
            return value
    return {}
def _management_position_records(telemetry: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("positions", "holdings", "position_snapshots"):
        raw_items = telemetry.get(key)
        if isinstance(raw_items, list):
            items = [item for item in raw_items if isinstance(item, dict)]
            if items:
                return items
    for key in ("position", "holding"):
        raw_item = telemetry.get(key)
        if isinstance(raw_item, dict):
            return [raw_item]
    return []
def _management_nested_value(record: Dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value
def _management_first_float(record: Dict[str, Any], *paths: str) -> Optional[float]:
    for path in paths:
        value = _management_nested_value(record, path)
        number = _management_as_float(value)
        if number is not None:
            return number
    return None
def _management_latest_timestamp(items: List[Dict[str, Any]], *fields: str) -> Optional[str]:
    latest: Optional[str] = None
    for item in items:
        for field in fields:
            value = str(item.get(field) or "").strip()
            if value and (latest is None or value > latest):
                latest = value
    return latest
def _management_telemetry_rollup(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        return {
            "runtime_count": 0,
            "total_pnl": None,
            "max_drawdown": None,
            "average_fill_rate": None,
            "total_trades": 0,
            "latest_collected_at": None,
        }

    pnl_values: List[float] = []
    drawdown_values: List[float] = []
    fill_rates: List[float] = []
    total_trades = 0
    latest_collected_at: Optional[str] = None

    for record in records:
        pnl = _management_first_float(record, "pnl", "summary.total_pnl", "summary.pnl")
        drawdown = _management_first_float(
            record,
            "drawdown",
            "max_drawdown",
            "summary.max_drawdown",
        )
        fill_rate = _management_first_float(record, "fill_rate", "summary.fill_rate")
        trades = _management_first_float(record, "total_trades", "summary.total_trades")
        collected_at = str(
            record.get("collected_at")
            or record.get("collectedAt")
            or record.get("updated_at")
            or record.get("updatedAt")
            or ""
        ).strip()
        if pnl is not None:
            pnl_values.append(pnl)
        if drawdown is not None:
            drawdown_values.append(drawdown)
        if fill_rate is not None:
            fill_rates.append(fill_rate)
        if trades is not None:
            total_trades += int(trades)
        if collected_at and (latest_collected_at is None or collected_at > latest_collected_at):
            latest_collected_at = collected_at

    return {
        "runtime_count": len(records),
        "total_pnl": round(sum(pnl_values), 6) if pnl_values else None,
        "max_drawdown": max(drawdown_values) if drawdown_values else None,
        "average_fill_rate": round(sum(fill_rates) / len(fill_rates), 6) if fill_rates else None,
        "total_trades": total_trades,
        "latest_collected_at": latest_collected_at,
    }
def _management_link(path: str, record_id: Optional[str]) -> Optional[str]:
    if not record_id:
        return None
    return f"{path}/{record_id}"
_PM12_ATTRIBUTION_DIMENSIONS = ("persona", "strategy", "pool", "asset", "broker", "runtime", "regime")
def _pm12_metric_or_split(
    value: Any,
    fallback: Optional[float],
    split_count: int,
) -> Optional[float]:
    metric = _management_as_float(value)
    if metric is not None:
        return metric
    if fallback is None:
        return None
    return round(fallback / max(split_count, 1), 6)
def _pm12_dimension_key(value: Any) -> str:
    key = str(value or "").strip()
    return key if key else "unassigned"
def _pm12_attribution_dimension_label(
    dimension: str,
    key: str,
    *,
    personas_by_id: Dict[str, Dict[str, Any]],
    strategies_by_id: Dict[str, Dict[str, Any]],
    pools_by_id: Dict[str, Dict[str, Any]],
) -> str:
    if key == "unassigned":
        return "Unassigned"
    if dimension == "persona":
        persona = personas_by_id.get(key, {})
        return str(persona.get("name") or persona.get("display_name") or key)
    if dimension == "strategy":
        strategy = strategies_by_id.get(key, {})
        return str(strategy.get("title") or strategy.get("name") or key)
    if dimension == "pool":
        pool = pools_by_id.get(key, {})
        return str(pool.get("name") or key)
    return key
def _pm12_performance_attribution_sources(
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    runtime_bindings = read_store.list_runtime_bindings(include_market_persona_defaults=True) or []
    deployment_plans = read_store.list_deployment_plans() or []
    bindings = read_store.list_bindings(include_market_persona_defaults=True) or []
    capital_pools = read_store.list_capital_pools(include_market_persona_defaults=True) or []
    clean_tenant = str(tenant_id or "").strip()
    personas = _list_persona_records(clean_tenant or None)
    strategies = _list_strategy_summaries()

    plans_by_id = {
        _management_record_id(plan, "plan_id", "id"): plan
        for plan in deployment_plans
        if _management_record_id(plan, "plan_id", "id")
    }
    bindings_by_id = {
        _management_record_id(binding, "binding_id", "id", "persona_capital_binding_id"): binding
        for binding in bindings
        if _management_record_id(binding, "binding_id", "id", "persona_capital_binding_id")
    }
    pools_by_id = {
        _management_record_id(pool, "pool_id", "id"): pool
        for pool in capital_pools
        if _management_record_id(pool, "pool_id", "id")
    }
    personas_by_id = {
        _management_record_id(persona, "persona_id", "id"): persona
        for persona in personas
        if _management_record_id(persona, "persona_id", "id")
    }
    strategies_by_id = {
        _management_record_id(strategy, "strategy_id", "id"): strategy
        for strategy in strategies
        if _management_record_id(strategy, "strategy_id", "id")
    }

    # A single telemetry-list projection is the canonical bounded source for
    # this aggregate.  Do not issue one record read per runtime when that
    # projection supplied rows.  The record lookup fallback is retained for
    # older stores that expose no telemetry-list rows at all (including
    # isolated legacy fixtures that only expose the historical record lookup).
    telemetry_by_runtime_id: Dict[str, Dict[str, Any]] = {}
    try:
        telemetry_summaries = list(read_store.list_telemetry_summaries() or [])
    except Exception:
        telemetry_summaries = []
    has_bulk_telemetry_projection = bool(telemetry_summaries)
    for telemetry in telemetry_summaries:
        if not isinstance(telemetry, dict):
            continue
        runtime_id = _management_record_id(
            telemetry,
            "runtime_id",
            "runtimeId",
            "execution_runtime_id",
            "id",
        )
        if runtime_id:
            telemetry_by_runtime_id[runtime_id] = telemetry

    for runtime in runtime_bindings:
        runtime_id = _management_record_id(runtime, "runtime_id", "id", "binding_id")
        if not runtime_id:
            continue
        telemetry = telemetry_by_runtime_id.get(runtime_id)
        if telemetry is None and not has_bulk_telemetry_projection:
            telemetry = read_store.get_telemetry_summary(runtime_id)
        if telemetry is not None:
            telemetry_by_runtime_id[runtime_id] = telemetry

    return {
        "tenant_id": clean_tenant or None,
        "runtime_bindings": runtime_bindings,
        "deployment_plans": deployment_plans,
        "bindings": bindings,
        "capital_pools": capital_pools,
        "personas": personas,
        "strategies": strategies,
        "plans_by_id": plans_by_id,
        "bindings_by_id": bindings_by_id,
        "pools_by_id": pools_by_id,
        "personas_by_id": personas_by_id,
        "strategies_by_id": strategies_by_id,
        "telemetry_by_runtime_id": telemetry_by_runtime_id,
    }
def _pm12_performance_attribution_facts(sources: Dict[str, Any], period_key: str) -> List[Dict[str, Any]]:
    facts: List[Dict[str, Any]] = []
    plans_by_id = sources["plans_by_id"]
    bindings_by_id = sources["bindings_by_id"]
    pools_by_id = sources["pools_by_id"]
    telemetry_by_runtime_id = sources["telemetry_by_runtime_id"]
    scoped_tenant = str(sources.get("tenant_id") or "").strip()
    personas_by_id = sources["personas_by_id"]

    for runtime in sources["runtime_bindings"]:
        runtime_id = _management_record_id(runtime, "runtime_id", "id", "binding_id")
        runtime_binding_id = _management_record_id(runtime, "runtime_binding_id", "binding_id", "id")
        plan_id = _management_record_id(runtime, "plan_id", "deployment_plan_id")
        plan = plans_by_id.get(plan_id, {})
        plan_binding_ids = [
            str(value).strip()
            for value in (plan.get("binding_ids") or [])
            if str(value).strip()
        ]
        persona_binding_id = (
            _management_record_id(runtime, "persona_capital_binding_id")
            or (plan_binding_ids[0] if plan_binding_ids else "")
        )
        persona_binding = bindings_by_id.get(persona_binding_id, {})
        telemetry = telemetry_by_runtime_id.get(runtime_id, {})
        summary = telemetry.get("summary") if isinstance(telemetry.get("summary"), dict) else {}
        positions = _management_position_records(telemetry) or [{}]
        split_count = len(positions)

        runtime_pnl = _management_as_float(
            _management_first_non_empty(telemetry.get("pnl"), summary.get("total_pnl"))
        )
        runtime_unrealized_pnl = _management_as_float(
            _management_first_non_empty(telemetry.get("unrealized_pnl"), summary.get("unrealized_pnl"))
        )
        runtime_realized_pnl = _management_as_float(
            _management_first_non_empty(telemetry.get("realized_pnl"), summary.get("realized_pnl"))
        )
        runtime_trades = _management_as_float(
            _management_first_non_empty(telemetry.get("total_trades"), summary.get("total_trades"))
        )

        for index, position in enumerate(positions):
            instrument = _management_nested_dict(position, "instrument", "asset", "contract")
            mark = _management_nested_dict(position, "mark", "mark_price", "market_price")
            capital_pool_id = str(
                _management_first_non_empty(
                    _management_dict_value(position, "capital_pool_id", "pool_id"),
                    _management_dict_value(runtime, "capital_pool_id", "pool_id"),
                    _management_dict_value(plan, "capital_pool_id", "target_pool_id", "pool_id"),
                    _management_dict_value(persona_binding, "capital_pool_id", "pool_id"),
                )
                or ""
            )
            capital_pool = pools_by_id.get(capital_pool_id, {})
            canonical_persona_id = str(persona_binding.get("persona_id") or "").strip()
            if canonical_persona_id:
                persona_id = canonical_persona_id
            else:
                persona_id = str(runtime.get("persona_id") or "").strip()
            if scoped_tenant and persona_id and persona_id not in personas_by_id:
                # A request-scoped attribution response may only disclose
                # runtime facts whose Persona has an explicit matching tenant
                # admission in the durable directory.
                continue
            strategy_id = str(
                _management_first_non_empty(
                    _management_dict_value(position, "strategy_id", "strategy_ref"),
                    _management_dict_value(runtime, "strategy_id", "strategy_ref"),
                    _management_dict_value(plan, "strategy_id", "strategy_ref"),
                    _management_dict_value(persona_binding, "strategy_id"),
                )
                or ""
            )
            symbol = str(
                _management_first_non_empty(
                    _management_dict_value(position, "symbol", "instrument_id", "asset_id", "contract_id"),
                    _management_dict_value(instrument, "symbol", "instrument_id", "asset_id", "contract_id"),
                    _management_dict_value(telemetry, "symbol", "instrument_id", "asset_id", "contract_id"),
                )
                or ""
            )
            broker_id = str(
                _management_first_non_empty(
                    _management_dict_value(position, "broker_id", "broker", "broker_ref"),
                    _management_dict_value(telemetry, "broker_id", "broker", "broker_ref"),
                    _management_dict_value(runtime, "broker_id", "broker", "broker_ref"),
                    _management_dict_value(plan, "broker_id", "broker", "broker_ref"),
                )
                or ""
            )
            regime = str(
                _management_first_non_empty(
                    _management_dict_value(position, "regime", "market_regime", "risk_regime"),
                    _management_dict_value(telemetry, "regime", "market_regime", "risk_regime"),
                    _management_dict_value(runtime, "regime", "market_regime", "risk_regime"),
                    _management_dict_value(plan, "regime", "market_regime", "risk_regime"),
                )
                or ""
            )
            quantity = _management_as_float(
                _management_first_non_empty(
                    _management_dict_value(position, "quantity", "qty", "net_quantity", "position_quantity"),
                    _management_dict_value(telemetry, "quantity", "position_quantity"),
                    _management_dict_value(summary, "quantity", "position_quantity"),
                )
            )
            mark_price = _management_as_float(
                _management_first_non_empty(
                    _management_dict_value(position, "mark_price", "market_price", "last_price"),
                    _management_dict_value(mark, "price", "mark_price", "market_price", "last_price"),
                    _management_dict_value(telemetry, "mark_price", "market_price", "last_price"),
                    _management_dict_value(summary, "mark_price", "market_price", "last_price"),
                )
            )
            market_value = _management_as_float(
                _management_first_non_empty(
                    _management_dict_value(position, "market_value", "value"),
                    _management_dict_value(telemetry, "market_value"),
                    _management_dict_value(summary, "market_value"),
                )
            )
            if market_value is None and quantity is not None and mark_price is not None:
                market_value = round(quantity * mark_price, 6)
            notional = _management_as_float(
                _management_first_non_empty(
                    _management_dict_value(position, "notional", "gross_notional"),
                    _management_dict_value(telemetry, "notional", "gross_notional"),
                    _management_dict_value(summary, "notional", "gross_notional"),
                    market_value,
                )
            )
            if notional is not None:
                notional = abs(notional)
            exposure = _management_as_float(
                _management_first_non_empty(
                    _management_dict_value(position, "exposure", "gross_exposure"),
                    _management_dict_value(telemetry, "exposure", "gross_exposure"),
                    _management_dict_value(summary, "exposure", "gross_exposure"),
                    notional,
                )
            )
            total_pnl = _pm12_metric_or_split(
                _management_first_non_empty(
                    _management_dict_value(position, "total_pnl", "pnl"),
                    _management_dict_value(position, "realized_plus_unrealized_pnl"),
                ),
                runtime_pnl,
                split_count,
            )
            unrealized_pnl = _pm12_metric_or_split(
                _management_dict_value(position, "unrealized_pnl", "unrealized"),
                runtime_unrealized_pnl,
                split_count,
            )
            realized_pnl = _pm12_metric_or_split(
                _management_dict_value(position, "realized_pnl", "realized"),
                runtime_realized_pnl,
                split_count,
            )
            drawdown = _management_as_float(
                _management_first_non_empty(
                    _management_dict_value(position, "drawdown", "max_drawdown"),
                    _management_dict_value(telemetry, "drawdown"),
                    _management_dict_value(summary, "max_drawdown"),
                )
            )
            value_at_risk = _management_as_float(
                _management_first_non_empty(
                    _management_first_float(
                        position,
                        "value_at_risk",
                        "valueAtRisk",
                        "var",
                        "VaR",
                        "risk.value_at_risk",
                        "risk.valueAtRisk",
                        "risk.var",
                    ),
                    _management_first_float(
                        telemetry,
                        "value_at_risk",
                        "valueAtRisk",
                        "var",
                        "VaR",
                        "risk.value_at_risk",
                        "risk.valueAtRisk",
                        "risk.var",
                        "summary.value_at_risk",
                        "summary.valueAtRisk",
                        "summary.var",
                    ),
                )
            )
            fill_rate = _management_as_float(
                _management_first_non_empty(
                    _management_dict_value(position, "fill_rate"),
                    _management_dict_value(telemetry, "fill_rate"),
                    _management_dict_value(summary, "fill_rate"),
                )
            )
            avg_slippage_bps = _management_as_float(
                _management_first_non_empty(
                    _management_dict_value(position, "avg_slippage_bps", "slippage_bps"),
                    _management_dict_value(telemetry, "avg_slippage_bps", "slippage_bps"),
                    _management_dict_value(summary, "avg_slippage_bps", "slippage_bps"),
                )
            )
            total_trades = _pm12_metric_or_split(
                _management_dict_value(position, "total_trades", "trade_count", "trades"),
                runtime_trades,
                split_count,
            )
            collected_at = str(
                _management_first_non_empty(
                    _management_dict_value(position, "collected_at", "marked_at", "updated_at"),
                    _management_dict_value(telemetry, "collected_at", "updated_at"),
                    _management_dict_value(summary, "collected_at", "updated_at"),
                )
                or ""
            )

            facts.append({
                "id": f"{runtime_id or runtime_binding_id or 'runtime'}:{index}",
                "period": period_key,
                "runtime_id": runtime_id,
                "runtime_binding_id": runtime_binding_id,
                "deployment_plan_id": plan_id or _management_record_id(plan, "plan_id", "id"),
                "persona_capital_binding_id": persona_binding_id,
                "capital_pool_id": capital_pool_id,
                "capital_pool_name": capital_pool.get("name") or capital_pool_id,
                "persona_id": persona_id,
                "strategy_id": strategy_id,
                "symbol": symbol,
                "broker_id": broker_id,
                "regime": regime,
                "deployment_stage": str(
                    runtime.get("deployment_stage") or runtime.get("deployment_mode") or plan.get("target_stage") or ""
                ),
                "status": str(_management_first_non_empty(position.get("status"), runtime.get("status"), "unknown")),
                "total_pnl": total_pnl,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": realized_pnl,
                "notional": notional,
                "market_value": market_value,
                "exposure": exposure,
                "drawdown": drawdown,
                "value_at_risk": value_at_risk,
                "fill_rate": fill_rate,
                "avg_slippage_bps": avg_slippage_bps,
                "total_trades": total_trades,
                "collected_at": collected_at or None,
                "telemetry_available": runtime_id in telemetry_by_runtime_id if runtime_id else False,
                "dimensions": {
                    "persona": _pm12_dimension_key(persona_id),
                    "strategy": _pm12_dimension_key(strategy_id),
                    "pool": _pm12_dimension_key(capital_pool_id),
                    "asset": _pm12_dimension_key(symbol),
                    "broker": _pm12_dimension_key(broker_id),
                    "runtime": _pm12_dimension_key(runtime_id or runtime_binding_id),
                    "regime": _pm12_dimension_key(regime),
                },
            })

    return facts
def _pm12_metric_sum(facts: List[Dict[str, Any]], field: str) -> Optional[float]:
    values = [
        value
        for value in (_management_as_float(fact.get(field)) for fact in facts)
        if value is not None
    ]
    return round(sum(values), 6) if values else None
def _pm12_metric_avg(facts: List[Dict[str, Any]], field: str) -> Optional[float]:
    values = [
        value
        for value in (_management_as_float(fact.get(field)) for fact in facts)
        if value is not None
    ]
    return _management_avg(values)
def _pm12_attribution_metrics(facts: List[Dict[str, Any]]) -> Dict[str, Any]:
    drawdown_values = [
        value
        for value in (_management_as_float(fact.get("drawdown")) for fact in facts)
        if value is not None
    ]
    trade_total = _pm12_metric_sum(facts, "total_trades")
    runtime_ids = sorted({
        str(fact.get("runtime_id") or "")
        for fact in facts
        if str(fact.get("runtime_id") or "")
    })
    telemetry_runtime_ids = sorted({
        str(fact.get("runtime_id") or "")
        for fact in facts
        if str(fact.get("runtime_id") or "") and fact.get("telemetry_available")
    })
    return {
        "runtime_count": len(runtime_ids),
        "telemetry_runtime_count": len(telemetry_runtime_ids),
        "holding_count": len(facts),
        "total_pnl": _pm12_metric_sum(facts, "total_pnl"),
        "unrealized_pnl": _pm12_metric_sum(facts, "unrealized_pnl"),
        "realized_pnl": _pm12_metric_sum(facts, "realized_pnl"),
        "total_notional": _pm12_metric_sum(facts, "notional"),
        "total_market_value": _pm12_metric_sum(facts, "market_value"),
        "total_exposure": _pm12_metric_sum(facts, "exposure"),
        "worst_drawdown": max(drawdown_values) if drawdown_values else None,
        "average_fill_rate": _pm12_metric_avg(facts, "fill_rate"),
        "average_slippage_bps": _pm12_metric_avg(facts, "avg_slippage_bps"),
        "total_trades": int(trade_total) if trade_total is not None else 0,
        "latest_telemetry_at": _management_latest_timestamp(facts, "collected_at"),
    }
def _pm12_performance_attribution_group_entries(
    facts: List[Dict[str, Any]],
    *,
    dimensions: List[str],
) -> List[Dict[str, Any]]:
    total_metrics = _pm12_attribution_metrics(facts)
    portfolio_pnl = _management_as_float(total_metrics.get("total_pnl"))
    portfolio_notional = _management_as_float(total_metrics.get("total_notional"))
    entries: List[Dict[str, Any]] = []

    for dimension in dimensions:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for fact in facts:
            dims = fact.get("dimensions") if isinstance(fact.get("dimensions"), dict) else {}
            key = _pm12_dimension_key(dims.get(dimension))
            grouped.setdefault(key, []).append(fact)

        ranked_groups: List[tuple[str, List[Dict[str, Any]], Dict[str, Any]]] = []
        for key, group_facts in grouped.items():
            ranked_groups.append((key, group_facts, _pm12_attribution_metrics(group_facts)))
        ranked_groups.sort(
            key=lambda item: (
                _management_as_float(item[2].get("total_pnl")) is None,
                -(_management_as_float(item[2].get("total_pnl")) or 0.0),
                item[0],
            )
        )

        for rank, (key, group_facts, metrics) in enumerate(ranked_groups, start=1):
            pnl = _management_as_float(metrics.get("total_pnl"))
            notional = _management_as_float(metrics.get("total_notional"))
            pnl_contribution = None
            if pnl is not None and portfolio_pnl not in (None, 0):
                pnl_contribution = round(pnl / portfolio_pnl, 6)
            notional_weight = None
            if notional is not None and portfolio_notional not in (None, 0):
                notional_weight = round(notional / portfolio_notional, 6)
            entries.append({
                "dimension": dimension,
                "dimension_key": key,
                "group_facts": group_facts,
                "metrics": metrics,
                "notional_weight": notional_weight,
                "pnl_contribution_pct": pnl_contribution,
                "rank": rank,
            })

    return entries
def _pm12_performance_attribution_page_entries(
    facts: List[Dict[str, Any]],
    *,
    dimensions: List[str],
    page_token: Optional[str],
    page_size: int,
) -> tuple[List[Dict[str, Any]], int, Optional[str], Dict[str, Any]]:
    entries = _pm12_performance_attribution_group_entries(facts, dimensions=dimensions)
    page_entries, next_page_token = _page_slice(entries, page_token, page_size)
    return page_entries, len(entries), next_page_token, _pm12_attribution_metrics(facts)
def _pm12_attribution_data_confidence(metrics: Dict[str, Any]) -> str:
    holding_count = int(metrics.get("holding_count") or 0)
    runtime_count = int(metrics.get("runtime_count") or 0)
    telemetry_runtime_count = int(metrics.get("telemetry_runtime_count") or 0)
    if holding_count <= 0:
        return "unavailable"
    if telemetry_runtime_count <= 0:
        return "partial"
    if runtime_count and telemetry_runtime_count < runtime_count:
        return "degraded"
    if _management_as_float(metrics.get("total_pnl")) is None:
        return "partial"
    return "formal"
def _pm12_performance_attribution_rows(
    entries: List[Dict[str, Any]],
    *,
    period_key: str,
    sources: Dict[str, Any],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for entry in entries:
        dimension = str(entry.get("dimension") or "")
        key = str(entry.get("dimension_key") or "")
        group_facts = entry.get("group_facts") if isinstance(entry.get("group_facts"), list) else []
        metrics = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {}
        runtime_ids = sorted({
            str(fact.get("runtime_id") or "")
            for fact in group_facts
            if str(fact.get("runtime_id") or "")
        })
        pool_ids = sorted({
            str(fact.get("capital_pool_id") or "")
            for fact in group_facts
            if str(fact.get("capital_pool_id") or "")
        })
        persona_ids = sorted({
            str(fact.get("persona_id") or "")
            for fact in group_facts
            if str(fact.get("persona_id") or "")
        })
        strategy_ids = sorted({
            str(fact.get("strategy_id") or "")
            for fact in group_facts
            if str(fact.get("strategy_id") or "")
        })
        label = _pm12_attribution_dimension_label(
            dimension,
            key,
            personas_by_id=sources["personas_by_id"],
            strategies_by_id=sources["strategies_by_id"],
            pools_by_id=sources["pools_by_id"],
        )
        data_confidence = _pm12_attribution_data_confidence(metrics)
        rows.append({
            "id": f"pm12-performance-attribution-{dimension}-{key}",
            "dimension": dimension,
            "dimension_key": key,
            "label": label,
            "period": period_key,
            "data_confidence": data_confidence,
            "source_status": "ok" if data_confidence == "formal" else data_confidence,
            "rank": entry.get("rank"),
            "metrics": {
                **metrics,
                "data_confidence": data_confidence,
                "pnl_contribution_pct": entry.get("pnl_contribution_pct"),
                "notional_weight": entry.get("notional_weight"),
            },
            "total_pnl": metrics["total_pnl"],
            "pnl_contribution_pct": entry.get("pnl_contribution_pct"),
            "notional_weight": entry.get("notional_weight"),
            "runtime_count": metrics["runtime_count"],
            "holding_count": metrics["holding_count"],
            "source_refs": {
                "runtime_ids": runtime_ids,
                "capital_pool_ids": pool_ids,
                "persona_ids": persona_ids,
                "strategy_ids": strategy_ids,
            },
            "links": {
                "runtime": _management_link("/bff/runtimes", key) if dimension == "runtime" else None,
                "capital_pool": _management_link("/bff/capital-pools", key) if dimension == "pool" else None,
                "persona": _management_link("/bff/personas", key) if dimension == "persona" else None,
                "strategy": _management_link("/bff/strategies", key) if dimension == "strategy" else None,
            },
        })

    return rows
def _sort_records_latest_first(
    records: List[Dict[str, Any]],
    fields: tuple[str, ...],
) -> List[Dict[str, Any]]:
    return sorted(
        records,
        key=lambda item: next(
            (str(item.get(field) or "") for field in fields if item.get(field)),
            "",
        ),
        reverse=True,
    )
def _persona_fleet_runtime_matches(
    runtime_binding: Dict[str, Any],
    *,
    binding_ids: set[str],
    capital_pool_ids: set[str],
    runtime_refs: set[str],
) -> bool:
    runtime_ids = {
        str(runtime_binding.get(key) or "").strip()
        for key in ("id", "binding_id", "runtime_binding_id", "runtime_id")
    }
    runtime_ids.discard("")
    if runtime_ids.intersection(runtime_refs):
        return True

    persona_binding_id = str(runtime_binding.get("persona_capital_binding_id") or "").strip()
    if persona_binding_id and persona_binding_id in binding_ids:
        return True

    capital_pool_id = str(runtime_binding.get("capital_pool_id") or "").strip()
    if capital_pool_id and capital_pool_id in capital_pool_ids:
        return True

    plan_id = str(runtime_binding.get("plan_id") or runtime_binding.get("deployment_plan_id") or "").strip()
    if plan_id:
        plan = read_store.get_deployment_plan(plan_id) or {}
        plan_binding_ids = {
            str(value).strip()
            for value in (plan.get("binding_ids") or [])
            if str(value).strip()
        }
        if plan_binding_ids.intersection(binding_ids):
            return True
        plan_pool_id = str(plan.get("capital_pool_id") or plan.get("target_pool_id") or "").strip()
        if plan_pool_id and plan_pool_id in capital_pool_ids:
            return True

    return False
def _project_persona_fleet_health(
    *,
    persona: Dict[str, Any],
    runtime_bindings: List[Dict[str, Any]],
    telemetry_summaries: List[Dict[str, Any]],
    active_incidents: List[Dict[str, Any]],
) -> Dict[str, Any]:
    reasons: List[str] = []
    lifecycle = str(persona.get("lifecycle_state") or persona.get("state") or "").lower()
    if lifecycle and not _is_persona_lifecycle_operational(lifecycle):
        reasons.append("persona_lifecycle_not_active")
    if not runtime_bindings:
        reasons.append("no_runtime_binding")
    if active_incidents:
        reasons.append("active_incident")

    latest_telemetry = telemetry_summaries[0] if telemetry_summaries else {}
    drawdown = latest_telemetry.get("drawdown")
    pnl = latest_telemetry.get("pnl")
    try:
        if drawdown is not None and float(drawdown) >= 0.10:
            reasons.append("drawdown_threshold")
    except (TypeError, ValueError):
        pass
    try:
        if pnl is not None and float(pnl) <= -0.05:
            reasons.append("negative_pnl")
    except (TypeError, ValueError):
        pass

    runtime_statuses = {
        str(binding.get("status") or "").strip().lower()
        for binding in runtime_bindings
        if str(binding.get("status") or "").strip()
    }
    unhealthy_runtime_statuses = sorted(runtime_statuses.difference({"active", "ready", "running", "idle"}))
    if unhealthy_runtime_statuses:
        reasons.append("runtime_status_attention")

    status = "healthy"
    severity = "low"
    if active_incidents or "drawdown_threshold" in reasons:
        status = "critical"
        severity = "high"
    elif reasons:
        status = "degraded"
        severity = "medium"

    score = max(0, 100 - (35 if status == "critical" else 0) - (15 * max(len(reasons) - 1, 0)))
    return {
        "status": status,
        "severity": severity,
        "score": score,
        "reasons": reasons,
        "runtime_statuses": sorted(runtime_statuses),
        "latest_telemetry_at": latest_telemetry.get("collected_at"),
        "active_incident_count": len(active_incidents),
    }
def _project_persona_fleet_item(
    raw_persona: Dict[str, Any],
    *,
    all_runtime_bindings: List[Dict[str, Any]],
    all_incidents: List[Dict[str, Any]],
    all_evolution_decisions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    persona_id = str(raw_persona.get("persona_id") or raw_persona.get("id") or "").strip()
    overlay = _PERSONA_BFF_OVERLAY.get(persona_id)
    routed = _routed_strategies_for_persona(persona_id)
    persona_dto = _project_persona_dto(raw_persona, overlay=overlay, routed_strategies=routed)

    bindings = list(read_store.get_bindings_for_persona(persona_id) or [])
    binding_ids = {
        str(binding.get("id") or binding.get("binding_id") or "").strip()
        for binding in bindings
        if str(binding.get("id") or binding.get("binding_id") or "").strip()
    }
    capital_pool_ids = {
        str(binding.get("capital_pool_id") or "").strip()
        for binding in bindings
        if str(binding.get("capital_pool_id") or "").strip()
    }

    sessions = list(read_store.get_sessions_for_persona(persona_id) or [])
    runtime_refs = {
        str(session.get("runtime_binding_id") or session.get("runtime_id") or "").strip()
        for session in sessions
        if str(session.get("runtime_binding_id") or session.get("runtime_id") or "").strip()
    }
    runtime_bindings = [
        binding
        for binding in all_runtime_bindings
        if _persona_fleet_runtime_matches(
            binding,
            binding_ids=binding_ids,
            capital_pool_ids=capital_pool_ids,
            runtime_refs=runtime_refs,
        )
    ]
    runtime_ids = {
        str(binding.get("runtime_id") or binding.get("runtime_binding_id") or binding.get("id") or "").strip()
        for binding in runtime_bindings
        if str(binding.get("runtime_id") or binding.get("runtime_binding_id") or binding.get("id") or "").strip()
    }
    artifact_ids = {
        str(binding.get("artifact_id") or "").strip()
        for binding in runtime_bindings
        if str(binding.get("artifact_id") or "").strip()
    }

    telemetry_summaries = [
        summary
        for runtime_id in sorted(runtime_ids)
        for summary in [read_store.get_telemetry_summary(runtime_id)]
        if summary
    ]
    telemetry_summaries = _sort_records_latest_first(telemetry_summaries, ("collected_at", "updated_at", "created_at"))
    latest_telemetry = telemetry_summaries[0] if telemetry_summaries else None

    teaching_sessions = _sort_records_latest_first(
        list(read_store.get_teaching_sessions_for_persona(persona_id) or []),
        ("started_at", "created_at", "updated_at"),
    )
    latest_training = teaching_sessions[0] if teaching_sessions else None

    active_incidents = [
        incident
        for incident in all_incidents
        if str(incident.get("status") or "").lower() in {"open", "active", "investigating"}
        and (
            str(incident.get("persona_id") or "").strip() == persona_id
            or str(incident.get("persona_capital_binding_id") or "").strip() in binding_ids
            or str(incident.get("capital_pool_id") or incident.get("affected_pool_id") or "").strip() in capital_pool_ids
            or str(incident.get("runtime_id") or "").strip() in runtime_ids
        )
    ]
    incident_ids = {
        str(incident.get("incident_id") or incident.get("id") or "").strip()
        for incident in all_incidents
        if str(incident.get("incident_id") or incident.get("id") or "").strip()
        and (
            str(incident.get("persona_id") or "").strip() == persona_id
            or str(incident.get("persona_capital_binding_id") or "").strip() in binding_ids
            or str(incident.get("capital_pool_id") or incident.get("affected_pool_id") or "").strip() in capital_pool_ids
            or str(incident.get("runtime_id") or "").strip() in runtime_ids
        )
    }
    evolution_decisions = [
        decision
        for decision in all_evolution_decisions
        if str(decision.get("target_id") or "").strip() == persona_id
        or str(decision.get("artifact_id") or "").strip() in artifact_ids
        or str(decision.get("incident_ref") or decision.get("linked_incident_id") or "").strip() in incident_ids
    ]
    evolution_decisions = _sort_records_latest_first(evolution_decisions, ("updated_at", "created_at"))

    capital_pools = [
        pool
        for pool_id in sorted(capital_pool_ids)
        for pool in [read_store.get_capital_pool(pool_id)]
        if pool
    ]
    enriched_bindings = [
        {
            **binding,
            "capital_pool": read_store.get_capital_pool(str(binding.get("capital_pool_id") or "")),
        }
        for binding in bindings
    ]
    health = _project_persona_fleet_health(
        persona=raw_persona,
        runtime_bindings=runtime_bindings,
        telemetry_summaries=telemetry_summaries,
        active_incidents=active_incidents,
    )
    allowed_actions = read_store.get_persona_allowed_actions(persona_id) or {}

    telemetry_summary = {
        "latest": latest_telemetry,
        "runtime_count": len(runtime_bindings),
        "covered_runtime_count": len(telemetry_summaries),
        "summaries": telemetry_summaries,
    }
    training_summary = {
        "session_count": len(teaching_sessions),
        "active_session_count": len([
            session for session in teaching_sessions
            if str(session.get("status") or "").lower() == "active"
        ]),
        "completed_session_count": len([
            session for session in teaching_sessions
            if str(session.get("status") or "").lower() == "completed"
        ]),
        "latest_session": latest_training,
    }
    evolution_summary = {
        "decision_count": len(evolution_decisions),
        "pending_decision_count": len([
            decision for decision in evolution_decisions
            if str(decision.get("status") or decision.get("decision_state") or "").lower()
            in {"pending", "in_review", "reviewed", "under_review"}
        ]),
        "latest_decision": evolution_decisions[0] if evolution_decisions else None,
        "decisions": evolution_decisions,
    }

    return {
        "id": persona_id,
        "persona_id": persona_id,
        "persona": persona_dto,
        "health": health,
        "bindings": enriched_bindings,
        "capitalPools": capital_pools,
        "capital_pools": capital_pools,
        "runtimeBindings": runtime_bindings,
        "runtime_bindings": runtime_bindings,
        "telemetrySummary": telemetry_summary,
        "telemetry_summary": telemetry_summary,
        "training": training_summary,
        "evolution": evolution_summary,
        "sessions": sessions,
        "activeIncidents": active_incidents,
        "active_incidents": active_incidents,
        "allowedActions": allowed_actions,
    }
_HUMAN_INBOX_OPEN_APPROVAL_STATES = {
    "pending",
    "in_review",
    "under_review",
    "reviewed",
    "proposed",
}
_HUMAN_INBOX_OPEN_INTERVENTION_STATUSES = {"pending", "escalated"}
_HUMAN_INBOX_OPEN_GOVERNANCE_STATUSES = {
    "pending",
    "open",
    "in_review",
    "under_review",
    "reviewed",
}
_HUMAN_INBOX_OPEN_SENTINEL_STATUSES = {"pending", "open", "active", "escalated"}
_HUMAN_INBOX_PRIORITY_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "unknown": 0,
}
def _human_inbox_csv_filter(value: Optional[str]) -> Optional[set[str]]:
    if not value:
        return None
    requested = {part.strip().lower() for part in value.split(",") if part.strip()}
    return requested or None
def _human_inbox_priority(value: Any, *, fallback: str = "medium") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in _HUMAN_INBOX_PRIORITY_RANK:
        return normalized
    if normalized in {"sev1", "p0"}:
        return "critical"
    if normalized in {"sev2", "p1"}:
        return "high"
    if normalized in {"sev3", "p2"}:
        return "medium"
    return fallback
def _human_inbox_attach_common_fields(
    projected: Dict[str, Any],
    *,
    inbox_type: str,
    source_dataset: str,
    risk_level: str,
    created_at: Optional[str],
    updated_at: Optional[str],
    href: str,
    source_record: Dict[str, Any],
) -> Dict[str, Any]:
    projected.setdefault("kind", inbox_type)
    projected["inbox_type"] = inbox_type
    projected["sourceDataset"] = source_dataset
    projected["source_dataset"] = source_dataset
    projected["riskLevel"] = risk_level
    projected["risk_level"] = risk_level
    projected["createdAt"] = created_at
    projected["created_at"] = created_at
    projected["updatedAt"] = updated_at
    projected["updated_at"] = updated_at
    projected["href"] = href
    projected.setdefault("route", href)
    return projected
def _human_inbox_action_state(status: str, open_statuses: set[str]) -> str:
    return "pending" if status in open_statuses else "resolved"
def _human_inbox_governance_review_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    item_id = _management_record_id(item, "item_id", "id", "review_id")
    if not item_id:
        return None
    review_type = str(item.get("item_type") or item.get("review_type") or "GovernanceReview").strip()
    status = str(item.get("status") or item.get("governance_outcome") or "pending").strip().lower() or "pending"
    risk_level = str(item.get("risk_level") or "unknown").strip().lower() or "unknown"
    priority = _human_inbox_priority(item.get("priority") or risk_level, fallback="medium")
    created_at = item.get("submitted_at") or item.get("created_at")
    updated_at = item.get("updated_at") or created_at
    route = f"{_GOVERNANCE_REVIEW_QUEUE_ROUTE}?item={item_id}"
    action_state = _human_inbox_action_state(status, _HUMAN_INBOX_OPEN_GOVERNANCE_STATUSES)
    projected = {
        "id": f"governance_review:{item_id}",
        "inbox_id": f"governance_review:{item_id}",
        "inboxType": "governance_review",
        "source_type": "governance_review",
        "source_id": item_id,
        "review_item_id": item_id,
        "title": item.get("title") or f"Governance review: {review_type}",
        "summary": item.get("summary") or item.get("description") or "Governance review awaiting human action.",
        "priority": priority,
        "risk_level": risk_level,
        "status": status,
        "action_state": action_state,
        "created_at": created_at,
        "updated_at": updated_at,
        "submitted_by": item.get("submitted_by"),
        "target": {
            "type": review_type,
            "id": item.get("target_id") or item.get("plan_id") or item.get("artifact_id") or item_id,
        },
        "route": route,
        "bff_detail_path": route,
        "allowedActions": _management_json_clone(item.get("allowedActions") or {
            "canReview": action_state == "pending",
            "canRequestRevision": action_state == "pending",
        }),
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="governance_review",
        source_dataset="governance_review_queue_items",
        risk_level=risk_level,
        created_at=created_at,
        updated_at=updated_at,
        href=route,
        source_record=item,
    )
def _human_inbox_approval_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    decision_id = _management_record_id(item, "decision_id", "id", "approval_decision_id")
    if not decision_id:
        return None
    decision_type = str(item.get("decision_type") or item.get("target_type") or "ApprovalDecision").strip()
    risk_level = str(item.get("risk_level") or "unknown").strip().lower() or "unknown"
    state = str(item.get("decision_state") or item.get("state") or "pending").strip().lower() or "pending"
    context = item.get("decision_context") if isinstance(item.get("decision_context"), dict) else {}
    governance_chain = context.get("governance_chain") if isinstance(context.get("governance_chain"), dict) else {}
    priority = _human_inbox_priority(item.get("priority") or risk_level, fallback="medium")
    risk_summary = str(context.get("risk_summary") or "").strip()
    target_type = str(governance_chain.get("target_type") or decision_type or "ApprovalDecision").strip()
    target_id = str(governance_chain.get("target_id") or governance_chain.get("linked_review_item_id") or "").strip()
    action_state = "pending" if state in _HUMAN_INBOX_OPEN_APPROVAL_STATES else "resolved"
    route = f"/management/approvals?approval={decision_id}"
    created_at = item.get("submitted_at")
    updated_at = item.get("updated_at") or created_at
    projected = {
        "id": f"approval:{decision_id}",
        "inbox_id": f"approval:{decision_id}",
        "inboxType": "approval",
        "source_type": "approval",
        "source_id": decision_id,
        "approval_decision_id": decision_id,
        "title": item.get("title") or f"{decision_type} approval",
        "summary": risk_summary or "Approval decision awaiting human review.",
        "priority": priority,
        "risk_level": risk_level,
        "status": state,
        "action_state": action_state,
        "created_at": created_at,
        "updated_at": updated_at,
        "submitted_by": item.get("submitted_by"),
        "target": {
            "type": target_type,
            "id": target_id or None,
        },
        "route": route,
        "bff_detail_path": f"/bff/approvals/{decision_id}",
        "decision_context": json.loads(json.dumps(context)),
        "allowedActions": json.loads(json.dumps(item.get("allowedActions") or {})),
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="approval",
        source_dataset="approval_queue_items",
        risk_level=risk_level,
        created_at=created_at,
        updated_at=updated_at,
        href=route,
        source_record=item,
    )
def _human_inbox_intervention_item(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    intervention_id = _management_record_id(record, "intervention_id", "id")
    if not intervention_id:
        return None
    status = str(record.get("status") or "pending").strip().lower() or "pending"
    kind = str(record.get("kind") or "hiq_sentinel").strip().lower() or "hiq_sentinel"
    priority = _human_inbox_priority(
        record.get("priority") or record.get("severity") or record.get("risk_level"),
        fallback="critical" if status == "pending" and kind == "hiq_sentinel" else "high",
    )
    action_state = "pending" if status in _HUMAN_INBOX_OPEN_INTERVENTION_STATUSES else "resolved"
    raw_allowed_actions = record.get("allowedActions") if isinstance(record.get("allowedActions"), dict) else {}
    allowed_actions = {
        "canClaim": status == "pending",
        "canRelease": status == "claimed",
        "canEscalate": status == "pending",
        "canDecide": status in _HUMAN_INBOX_OPEN_INTERVENTION_STATUSES,
        "canRemediate": status in _HUMAN_INBOX_OPEN_INTERVENTION_STATUSES,
        **raw_allowed_actions,
    }
    route = f"/management/interventions?intervention={intervention_id}"
    created_at = record.get("triggered_at") or record.get("created_at")
    updated_at = record.get("remediated_at") or record.get("updated_at") or created_at
    projected = {
        "id": f"intervention:{intervention_id}",
        "inbox_id": f"intervention:{intervention_id}",
        "inboxType": "intervention",
        "source_type": "intervention",
        "source_id": intervention_id,
        "intervention_id": intervention_id,
        "title": record.get("title") or f"{kind.replace('_', ' ').title()} intervention",
        "summary": record.get("description") or "Human intervention is required before the loop can continue.",
        "priority": priority,
        "risk_level": str(record.get("risk_level") or priority).strip().lower(),
        "status": status,
        "action_state": action_state,
        "created_at": created_at,
        "updated_at": updated_at,
        "triggered_by": record.get("triggered_by"),
        "target": {
            "type": record.get("target_type"),
            "id": record.get("target_id"),
        },
        "route": route,
        "bff_detail_path": f"/bff/v5/interventions/{intervention_id}",
        "remediation_context": {
            "kind": kind,
            "remediation_action": record.get("remediation_action"),
            "two_man_signature_id": record.get("two_man_signature_id"),
            "correlation_id": record.get("correlation_id"),
        },
        "allowedActions": json.loads(json.dumps(allowed_actions)),
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="intervention",
        source_dataset="v5_interventions",
        risk_level=str(record.get("risk_level") or priority).strip().lower(),
        created_at=created_at,
        updated_at=updated_at,
        href=route,
        source_record=record,
    )
def _human_inbox_sentinel_item(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    finding_id = _management_record_id(record, "id", "finding_id", "incident_id")
    if not finding_id:
        return None
    status = str(record.get("status") or "open").strip().lower() or "open"
    if status not in _HUMAN_INBOX_OPEN_SENTINEL_STATUSES:
        return None
    kind = str(record.get("kind") or "sentinel_finding").strip().lower() or "sentinel_finding"
    risk_level = str(record.get("severity") or record.get("risk_level") or "high").strip().lower() or "high"
    priority = _human_inbox_priority(record.get("priority") or risk_level, fallback="high")
    created_at = record.get("triggered_at") or record.get("created_at") or record.get("opened_at")
    updated_at = record.get("updated_at") or record.get("last_seen_at") or created_at
    runtime_id = record.get("runtime_id") or record.get("target_id")
    persona_id = record.get("persona_id")
    target_type = "Persona" if persona_id else "Runtime" if runtime_id else record.get("target_type")
    target_id = persona_id or runtime_id or record.get("target_id") or finding_id
    route = f"/management/sentinel?finding={finding_id}"
    action_state = _human_inbox_action_state(status, _HUMAN_INBOX_OPEN_SENTINEL_STATUSES)
    projected = {
        "id": f"sentinel_finding:{finding_id}",
        "inbox_id": f"sentinel_finding:{finding_id}",
        "inboxType": "sentinel_finding",
        "source_type": "sentinel_finding",
        "source_id": finding_id,
        "finding_id": finding_id,
        "title": record.get("title") or f"Sentinel finding: {kind}",
        "summary": record.get("summary") or record.get("description") or "Sentinel finding requires operator review.",
        "priority": priority,
        "risk_level": risk_level,
        "status": status,
        "action_state": action_state,
        "created_at": created_at,
        "updated_at": updated_at,
        "target": {
            "type": target_type,
            "id": target_id,
        },
        "route": route,
        "bff_detail_path": f"/bff/v5/sentinel/findings/{finding_id}",
        "sentinel_context": {
            "kind": kind,
            "runtime_id": runtime_id,
            "persona_id": persona_id,
            "derived_from_incident_id": record.get("derived_from_incident_id"),
        },
        "allowedActions": _management_json_clone(record.get("allowedActions") or {
            "canReview": action_state == "pending",
            "canRemediate": action_state == "pending",
        }),
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="sentinel_finding",
        source_dataset="sentinel_findings",
        risk_level=risk_level,
        created_at=created_at,
        updated_at=updated_at,
        href=route,
        source_record=record,
    )
def _human_inbox_persona_blocking_reasons(row: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    current_work = str(row.get("current_work") or row.get("currentWork") or "").strip()
    if current_work:
        reasons.append(current_work)
    recommendation = str(row.get("recommendation") or "").strip()
    if recommendation:
        reasons.append(f"governance recommendation: {recommendation}")
    research_status = row.get("research_status") if isinstance(row.get("research_status"), dict) else {}
    pending_task_ids = research_status.get("pending_task_ids")
    if isinstance(pending_task_ids, list) and pending_task_ids:
        reasons.append(f"pending research tasks: {', '.join(str(task_id) for task_id in pending_task_ids)}")
    if row.get("can_deploy") is False or row.get("canDeploy") is False:
        reasons.append("deployment is blocked until human review clears")
    return reasons
def _human_inbox_persona_readiness_item(row: Dict[str, Any], *, snapshot_at: str) -> Optional[Dict[str, Any]]:
    persona_id = _management_record_id(row, "persona_id", "personaId", "id")
    if not persona_id or not bool(row.get("human_needed") or row.get("humanNeeded")):
        return None
    name = str(row.get("persona_name") or row.get("personaName") or row.get("name") or persona_id).strip()
    status = str(row.get("state") or row.get("status") or "needs_human_approval").strip().lower()
    research_status = row.get("research_status") if isinstance(row.get("research_status"), dict) else {}
    current_projects = row.get("current_research_projects") if isinstance(row.get("current_research_projects"), list) else []
    blocking_reasons = _human_inbox_persona_blocking_reasons(row)
    risk_level = "high" if status in {"critical", "needs_human_approval", "blocked"} or blocking_reasons else "medium"
    priority = _human_inbox_priority(row.get("priority") or risk_level, fallback=risk_level)
    created_at = row.get("updated_at") or row.get("lastMutation") or row.get("last_mutation") or snapshot_at
    route = f"/management/persona-fleet?persona={persona_id}"
    summary = (
        str(row.get("current_work") or row.get("currentWork") or "").strip()
        or str(research_status.get("summary") or "").strip()
        or "Persona readiness is blocked on human governance review."
    )
    projected = {
        "id": f"readiness_blocker:persona:{persona_id}",
        "inbox_id": f"readiness_blocker:persona:{persona_id}",
        "inboxType": "readiness_blocker",
        "source_type": "readiness_blocker",
        "source_id": persona_id,
        "persona_id": persona_id,
        "title": f"Persona needs review: {name}",
        "summary": summary,
        "priority": priority,
        "risk_level": risk_level,
        "status": status,
        "action_state": "pending",
        "created_at": created_at,
        "updated_at": created_at,
        "target": {
            "type": "persona",
            "id": persona_id,
        },
        "route": route,
        "bff_detail_path": f"/bff/management/human-inbox/readiness_blocker:persona:{persona_id}",
        "blocking_reasons": list(blocking_reasons),
        "can_proceed": False,
        "research_context": {
            "research_status": _management_json_clone(research_status),
            "current_research_projects": _management_json_clone(current_projects),
            "recommendation": row.get("recommendation"),
            "current_work": row.get("current_work") or row.get("currentWork"),
            "data_source_status": _management_json_clone(row.get("data_source_status") or {}),
        },
        "allowedActions": {
            "canProceed": False,
            "canDecide": False,
            "canOpenPersonaFleet": True,
            "canOpenResearch": bool(current_projects or research_status),
            "canRequestRevision": True,
        },
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="readiness_blocker",
        source_dataset="persona_fleet",
        risk_level=risk_level,
        created_at=created_at,
        updated_at=created_at,
        href=route,
        source_record=row,
    )
_HUMAN_INBOX_PROMOTION_PRODUCER = "management_quarterly_ranking_recommendation_submit"
_HUMAN_INBOX_INACTIVE_COMMAND_STATUSES = {
    "canceled",
    "cancelled",
    "expired",
    "failed",
    "timed_out",
    "timeout",
}
_HUMAN_INBOX_PROMOTION_SNAPSHOT_SCALARS = {
    "action_id",
    "action_label",
    "archetype",
    "binding_state",
    "capital_mode",
    "capital_pool_id",
    "capital_scope",
    "capital_scope_id",
    "capital_sleeve_id",
    "current_weight",
    "current_weight_source",
    "deployment_stage",
    "eligible",
    "exclusion_reason",
    "formula_version",
    "id",
    "name",
    "owner",
    "paper_ledger_id",
    "priority",
    "quarter",
    "rank",
    "ranking_snapshot_id",
    "rationale",
    "recommendation_id",
    "risk",
    "risk_level",
    "score",
    "source_confidence",
    "stage",
    "state",
    "target_weight",
    "tier",
    "tier_id",
    "tier_label",
    "persona_id",
}
_HUMAN_INBOX_PROMOTION_SNAPSHOT_STRING_LISTS = {
    "artifact_ids",
    "binding_ids",
    "broker_ids",
    "capital_pool_ids",
    "exclusion_codes",
    "exclusion_reasons",
    "rationale_codes",
    "runtime_ids",
    "sleeve_ids",
    "strategy_ids",
}
def _human_inbox_promotion_recommendation_id(command: Dict[str, Any]) -> str:
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    target = command.get("target") if isinstance(command.get("target"), dict) else {}
    return str(
        params.get("recommendation_id")
        or params.get("recommendationId")
        or params.get("review_id")
        or params.get("promotion_review_id")
        or target.get("id")
        or ""
    ).strip()
def _human_inbox_trusted_promotion_submission(command: Dict[str, Any]) -> bool:
    if command.get("type") != CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT.value:
        return False
    if str(command.get("status") or "").strip().lower() in _HUMAN_INBOX_INACTIVE_COMMAND_STATUSES:
        return False
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    target = command.get("target") if isinstance(command.get("target"), dict) else {}
    recommendation_id = _human_inbox_promotion_recommendation_id(command)
    review_revision_id = _promotion_review_record_revision_id(command)
    target_id = str(target.get("id") or "").strip()
    if (
        not recommendation_id
        or target.get("type") != ObjectType.RANKING.value
        or not review_revision_id
        or target_id not in {recommendation_id, review_revision_id}
    ):
        return False
    expected_quarter = _promotion_review_quarter_from_id(recommendation_id)
    quarter = str(params.get("quarter") or "").strip().upper()
    persona_id = str(params.get("persona_id") or "").strip()
    action_id = str(
        params.get("recommendation_action_id")
        or params.get("recommendationActionId")
        or ""
    ).strip()
    if not expected_quarter or quarter != expected_quarter or not persona_id:
        return False
    if action_id not in _PROMOTION_REVIEW_ACTION_IDS:
        return False
    ranking_snapshot_id = str(params.get("ranking_snapshot_id") or "").strip()
    if ranking_snapshot_id and review_revision_id != _promotion_review_revision_id(
        recommendation_id,
        ranking_snapshot_id,
    ):
        return False
    for flag in (
        "direct_live_capital_mutation",
        "liveCapitalMutation",
        "live_capital_mutation",
        "runtime_mutation",
    ):
        if params.get(flag) not in (None, False):
            return False

    foundation = command.get("foundation") if isinstance(command.get("foundation"), dict) else {}
    audit = command.get("audit") if isinstance(command.get("audit"), dict) else {}
    audit_foundation = audit.get("foundation") if isinstance(audit.get("foundation"), dict) else {}
    trusted_producer = (
        foundation.get("trusted_evidence_producer")
        or audit.get("trusted_evidence_producer")
        or audit_foundation.get("trusted_evidence_producer")
    )
    if trusted_producer == _HUMAN_INBOX_PROMOTION_PRODUCER:
        return True
    # Legacy submissions from the dedicated semantic route predate the
    # producer marker. Generic /bff/v1 command admission always persists an
    # admission_route and must not manufacture viewer-visible inbox rows.
    if foundation.get("admission_route"):
        return False
    if not foundation:
        # Pre-foundation command-store rows were written by the dedicated
        # semantic submit route. API-admitted generic commands always carry an
        # admission_route, so this compatibility path cannot be reached by a
        # current generic command request.
        return True
    return (
        params.get("source_type") == "quarterly_ranking_recommendation"
        and params.get("source_record_id") == recommendation_id
        and params.get("audit_event") == "quarterly_ranking.recommendation_submitted"
        and params.get("policy") == "promotion_governance_human_gate_no_direct_live_capital"
    )
def _human_inbox_sanitize_promotion_snapshot(
    command: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not _human_inbox_trusted_promotion_submission(command):
        return None
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    recommendation_id = _human_inbox_promotion_recommendation_id(command)
    expected_quarter = str(_promotion_review_quarter_from_id(recommendation_id) or "").upper()
    persona_id = str(params.get("persona_id") or "").strip()
    action_id = str(
        params.get("recommendation_action_id")
        or params.get("recommendationActionId")
        or ""
    ).strip()
    raw_snapshot = params.get("source_recommendation")
    if raw_snapshot is not None and not isinstance(raw_snapshot, dict):
        return None
    raw = raw_snapshot if isinstance(raw_snapshot, dict) else {}

    for snapshot_id in (raw.get("id"), raw.get("recommendation_id")):
        if snapshot_id not in (None, "") and str(snapshot_id).strip() != recommendation_id:
            return None
    snapshot_quarter = str(raw.get("quarter") or expected_quarter).strip().upper()
    snapshot_persona = str(raw.get("persona_id") or persona_id).strip()
    snapshot_action = str(raw.get("action_id") or action_id).strip()
    if (
        snapshot_quarter != expected_quarter
        or snapshot_persona != persona_id
        or snapshot_action != action_id
    ):
        return None
    params_snapshot_id = str(params.get("ranking_snapshot_id") or "").strip()
    raw_snapshot_id = str(raw.get("ranking_snapshot_id") or "").strip()
    if params_snapshot_id and raw_snapshot_id != params_snapshot_id:
        return None

    sanitized: Dict[str, Any] = {}
    for key in _HUMAN_INBOX_PROMOTION_SNAPSHOT_SCALARS:
        value = raw.get(key)
        if value is None or isinstance(value, (dict, list)):
            continue
        sanitized[key] = value
    for key in _HUMAN_INBOX_PROMOTION_SNAPSHOT_STRING_LISTS:
        value = raw.get(key)
        if isinstance(value, list):
            sanitized[key] = [str(item) for item in value if isinstance(item, (str, int, float))]
    for key in ("components", "metrics"):
        value = raw.get(key)
        if isinstance(value, dict):
            sanitized[key] = {
                str(metric): number
                for metric, number in value.items()
                if isinstance(number, (int, float)) and not isinstance(number, bool)
            }

    sanitized.update(
        {
            "id": recommendation_id,
            "recommendation_id": recommendation_id,
            "quarter": expected_quarter,
            "persona_id": persona_id,
            "action_id": action_id,
            "name": sanitized.get("name") or params.get("persona_name") or persona_id,
            "priority": sanitized.get("priority") or params.get("priority") or "high",
            "risk_level": sanitized.get("risk_level") or params.get("risk_level") or "high",
            "rationale": sanitized.get("rationale")
            or params.get("rationale")
            or "Submitted ranking recommendation requires Human Gate review.",
            # Evidence bodies are request-scoped and may contain privileged
            # material. Never replay arbitrary command params onto a read row.
            "evidence_refs": [],
            "evidence_ref_ids": [],
        }
    )
    if params_snapshot_id:
        sanitized["ranking_snapshot_id"] = params_snapshot_id
    review_revision_id = _promotion_review_record_revision_id(command)
    if not review_revision_id:
        return None
    sanitized["review_id"] = review_revision_id
    sanitized["promotion_review_id"] = review_revision_id
    stage_from = str(params.get("stage_from") or sanitized.get("stage") or sanitized.get("state") or "").strip()
    if stage_from:
        sanitized.setdefault("stage", stage_from)
        sanitized.setdefault("state", stage_from)
    expected_path = _promotion_review_stage_path(sanitized)
    for param_key, path_key in (
        ("stage_from", "from_stage"),
        ("stage_to", "target_stage"),
        ("review_kind", "review_kind"),
    ):
        value = str(params.get(param_key) or "").strip()
        if value and value != str(expected_path.get(path_key) or ""):
            return None
    return sanitized
def _human_inbox_submission_projection_from_record(
    command: Dict[str, Any],
    recommendation_id: str,
) -> Dict[str, Any]:
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    audit = command.get("audit") if isinstance(command.get("audit"), dict) else {}
    review_revision_id = _promotion_review_record_revision_id(command)
    return {
        "submitted": True,
        "submit_status": command.get("status"),
        "command_id": command.get("command_id"),
        "commandId": command.get("command_id"),
        "receipt_id": command.get("command_id"),
        "submitted_at": command.get("submitted_at"),
        "submitted_by": audit.get("operator_id") or audit.get("actor") or audit.get("actor_id"),
        "recommendation_id": recommendation_id,
        "review_id": review_revision_id,
        "promotion_review_id": review_revision_id,
        "recommendation_action_id": params.get("recommendation_action_id")
        or params.get("recommendationActionId"),
        "ranking_snapshot_id": params.get("ranking_snapshot_id"),
        "quarter": params.get("quarter"),
        "persona_id": params.get("persona_id"),
        "stage_from": params.get("stage_from"),
        "stage_to": params.get("stage_to"),
        "review_kind": params.get("review_kind"),
        "human_inbox_id": _promotion_review_target_id(review_revision_id),
        "live_capital_mutation": False,
        "requires_human_gate_decision": True,
    }
def _human_inbox_decision_recommendation_id(command: Dict[str, Any]) -> str:
    command_type = str(command.get("type") or "")
    if command_type not in {
        CommandType.HUMAN_GATE_APPROVE.value,
        CommandType.HUMAN_GATE_REJECT.value,
    }:
        return ""
    target = command.get("target") if isinstance(command.get("target"), dict) else {}
    if target.get("type") != ObjectType.HUMAN_GATE_ITEM.value:
        return ""
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    raw_target_id = str(target.get("id") or "").strip()
    review_revision_id = _promotion_review_clean_id(raw_target_id)
    if (
        not review_revision_id
        or raw_target_id != _promotion_review_target_id(review_revision_id)
    ):
        return ""
    recommendation_id = str(
        params.get("recommendation_id")
        or params.get("recommendationId")
        or _promotion_review_revision_recommendation_id(review_revision_id)
    ).strip()
    if (
        not recommendation_id
        or _promotion_review_revision_recommendation_id(review_revision_id)
        != recommendation_id
    ):
        return ""
    for key in (
        "human_gate_item_id",
        "humanGateItemId",
        "review_id",
        "reviewId",
        "promotion_review_id",
        "promotionReviewId",
    ):
        alias = params.get(key)
        if (
            alias not in (None, "")
            and _promotion_review_clean_id(alias) != review_revision_id
        ):
            return ""
    for key in ("recommendation_id", "recommendationId"):
        alias = params.get(key)
        if alias not in (None, "") and str(alias).strip() != recommendation_id:
            return ""
    ranking_snapshot_id = str(params.get("ranking_snapshot_id") or "").strip()
    if ranking_snapshot_id:
        if review_revision_id != _promotion_review_revision_id(
            recommendation_id,
            ranking_snapshot_id,
        ):
            return ""
    elif review_revision_id != recommendation_id:
        # A revision-aware decision without its snapshot lineage is unsafe.
        return ""
    return review_revision_id
def _human_inbox_decision_projection_from_record(command: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if str(command.get("status") or "").strip().lower() in _HUMAN_INBOX_INACTIVE_COMMAND_STATUSES:
        return None
    review_revision_id = _human_inbox_decision_recommendation_id(command)
    if not review_revision_id:
        return None
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    decision = str(params.get("decision") or "").strip().lower()
    if decision not in _PROMOTION_REVIEW_DECISIONS:
        return None
    command_type = str(command.get("type") or "")
    if command_type == CommandType.HUMAN_GATE_REJECT.value and decision != "reject":
        return None
    if command_type == CommandType.HUMAN_GATE_APPROVE.value and decision not in {
        "approve",
        "approve_with_conditions",
    }:
        return None
    audit = command.get("audit") if isinstance(command.get("audit"), dict) else {}
    projection: Dict[str, Any] = {
        "decision": decision,
        "decision_status": "accepted",
        "command_id": command.get("command_id"),
        "commandId": command.get("command_id"),
        "receipt_id": command.get("command_id"),
        "submitted_at": command.get("submitted_at"),
        "decided_at": command.get("submitted_at"),
        "decided_by": audit.get("operator_id") or audit.get("actor") or audit.get("actor_id"),
        "command_status": command.get("status"),
        "review_id": review_revision_id,
        "promotion_review_id": review_revision_id,
        "recommendation_id": params.get("recommendation_id")
        or params.get("recommendationId")
        or _promotion_review_revision_recommendation_id(
            review_revision_id
        ),
        "ranking_snapshot_id": params.get("ranking_snapshot_id"),
        "live_capital_mutation": False,
        "requires_human_gate_decision": True,
    }
    rationale = params.get("rationale") or params.get("reason") or params.get("rejection_reason") or params.get("memo")
    if rationale not in (None, ""):
        projection["rationale"] = rationale
    if "conditions" in params:
        projection["conditions"] = _management_json_clone(params.get("conditions"))
    return projection
def _human_inbox_promotion_review_from_projection(
    recommendation: Dict[str, Any],
    *,
    submission: Dict[str, Any],
    decision: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    recommendation_id = str(
        recommendation.get("recommendation_id")
        or recommendation.get("id")
        or ""
    )
    review_id = str(
        recommendation.get("promotion_review_id")
        or recommendation.get("review_id")
        or _promotion_review_revision_id(
            recommendation_id,
            recommendation.get("ranking_snapshot_id"),
        )
    )
    stage_path = _promotion_review_stage_path(recommendation)
    decision_status = "accepted" if decision else "pending"
    item: Dict[str, Any] = {
        **{
            key: _management_json_clone(value)
            for key, value in recommendation.items()
            if key not in {"id", "status"}
        },
        "id": review_id,
        "review_id": review_id,
        "promotion_review_id": review_id,
        "recommendation_id": recommendation_id,
        "status": "decision_accepted" if decision else "pending_human_gate",
        "decision_status": decision_status,
        "submitted": True,
        "submit_status": submission.get("submit_status"),
        "human_inbox_id": _promotion_review_target_id(review_id),
        "allowed_decisions": sorted(_PROMOTION_REVIEW_DECISIONS),
        "allowedActions": {
            "canSubmit": False,
            "canApprove": not bool(decision),
            "canApproveWithConditions": not bool(decision),
            "canReject": not bool(decision),
        },
        "promotion_path": stage_path,
        "review_kind": stage_path.get("review_kind"),
        "source_recommendation": _management_json_clone(recommendation),
        "submission": submission,
        "governance": {
            "requires_human_gate_decision": True,
            "decision_status": decision_status,
            "live_capital_mutation": False,
            "direct_live_capital_mutation": False,
            "policy": "promotion_governance_human_gate_no_direct_live_capital",
        },
        "requires_human_gate_decision": True,
        "live_capital_mutation": False,
        "direct_live_capital_mutation": False,
        "policy": "promotion_governance_human_gate_no_direct_live_capital",
        "links": {
            "persona": f"/bff/personas/{recommendation.get('persona_id')}",
            "recommendation": "/bff/management/quarterly-ranking/recommendations",
            "detail": f"/bff/management/promotion-reviews/{quote(review_id, safe='')}",
            "decisions": f"/bff/management/promotion-reviews/{quote(review_id, safe='')}/decisions",
            "human_inbox": f"/bff/management/human-inbox/{quote(_promotion_review_target_id(review_id), safe='')}",
        },
    }
    if decision:
        item["decision"] = decision
    return item
def _submitted_promotion_review_record_from_command(
    command: Dict[str, Any],
    *,
    decision: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Project one trusted durable submission without rebuilding PM12 reads."""
    recommendation = _human_inbox_sanitize_promotion_snapshot(command)
    if recommendation is None:
        return None
    recommendation_id = str(recommendation["recommendation_id"])
    review_id = _promotion_review_record_revision_id(command)
    if not review_id:
        return None
    return _human_inbox_promotion_review_from_projection(
        recommendation,
        submission=_human_inbox_submission_projection_from_record(command, recommendation_id),
        decision=decision,
    )
def _submitted_promotion_review_records(
    identity: OperatorIdentity,
    *,
    snapshot_at: str,
) -> List[Dict[str, Any]]:
    del identity, snapshot_at  # Projection is identity-stable; evidence is always stripped.
    submissions: Dict[str, Dict[str, Any]] = {}
    decisions: Dict[str, Dict[str, Any]] = {}
    # One command-log read per aggregate, regardless of submitted row count.
    for command in command_store._get_all_commands():
        if command.get("type") == CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT.value:
            recommendation = _human_inbox_sanitize_promotion_snapshot(command)
            if recommendation is not None:
                review_id = _promotion_review_record_revision_id(command)
                if review_id:
                    submissions[review_id] = command
            continue
        review_id = _human_inbox_decision_recommendation_id(command)
        decision = _human_inbox_decision_projection_from_record(command)
        if review_id and decision is not None:
            decisions[review_id] = decision

    records: List[Dict[str, Any]] = []
    for review_id, command in submissions.items():
        review = _submitted_promotion_review_record_from_command(
            command,
            decision=decisions.get(review_id),
        )
        if review is not None:
            records.append(review)
    return records
def _human_inbox_promotion_review_item(review: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    review_id = str(review.get("review_id") or review.get("promotion_review_id") or "").strip()
    if not review_id:
        return None
    decision_status = str(review.get("decision_status") or "pending").strip().lower() or "pending"
    status = "accepted" if decision_status == "accepted" else "pending"
    risk_level = str(review.get("risk_level") or "high").strip().lower() or "high"
    priority = _human_inbox_priority(review.get("priority") or risk_level, fallback="high")
    submission = review.get("submission") if isinstance(review.get("submission"), dict) else {}
    created_at = submission.get("submitted_at") or review.get("created_at")
    updated_at = (review.get("decision") or {}).get("decided_at") if isinstance(review.get("decision"), dict) else None
    updated_at = updated_at or created_at
    inbox_id = _promotion_review_target_id(review_id)
    route = f"/management/human-inbox/{quote(inbox_id, safe='')}"
    action_state = "pending" if status == "pending" else "resolved"
    stage_path = review.get("promotion_path") if isinstance(review.get("promotion_path"), dict) else {}
    projected = {
        "id": inbox_id,
        "inbox_id": inbox_id,
        "inboxType": "promotion_review",
        "source_type": "promotion_review",
        "source_id": review_id,
        "review_id": review_id,
        "promotion_review_id": review_id,
        "recommendation_id": review.get("recommendation_id"),
        "persona_id": review.get("persona_id"),
        "title": f"Persona governance review: {review.get('name') or review.get('persona_id')}",
        "summary": review.get("rationale") or "Persona ranking recommendation requires Human Gate approval.",
        "priority": priority,
        "risk_level": risk_level,
        "status": status,
        "action_state": action_state,
        "created_at": created_at,
        "updated_at": updated_at,
        "submitted_by": submission.get("submitted_by"),
        "target": {
            "type": "persona",
            "id": review.get("persona_id"),
        },
        "route": route,
        "bff_detail_path": f"/bff/management/promotion-reviews/{quote(review_id, safe='')}",
        "decisionHref": f"/bff/management/promotion-reviews/{quote(review_id, safe='')}/decisions",
        "detailHref": route,
        "promotion_review": _management_json_clone(review),
        "promotion_context": {
            "from_stage": stage_path.get("from_stage"),
            "target_stage": stage_path.get("target_stage"),
            "review_kind": review.get("review_kind") or stage_path.get("review_kind"),
            "action_id": review.get("action_id"),
            "ranking_snapshot_id": review.get("ranking_snapshot_id"),
            "live_capital_mutation": False,
        },
        "allowedActions": _management_json_clone(review.get("allowedActions") or {
            "canApprove": action_state == "pending",
            "canApproveWithConditions": action_state == "pending",
            "canReject": action_state == "pending",
        }),
        "requires_human_gate_decision": True,
        "live_capital_mutation": False,
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="promotion_review",
        source_dataset="promotion_reviews",
        risk_level=risk_level,
        created_at=created_at,
        updated_at=updated_at,
        href=route,
        source_record=review,
    )
def _human_inbox_project_items(
    *,
    snapshot_at: str,
    review_records: Sequence[Dict[str, Any]],
    approval_records: Sequence[Dict[str, Any]],
    intervention_records: Sequence[Dict[str, Any]],
    sentinel_records: Sequence[Dict[str, Any]],
    persona_rows: Sequence[Dict[str, Any]],
    promotion_review_records: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Project already-loaded contributors into the canonical inbox rows."""
    items: List[Dict[str, Any]] = []
    projectors: Sequence[tuple[Sequence[Dict[str, Any]], Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]]] = (
        (review_records, _human_inbox_governance_review_item),
        (approval_records, _human_inbox_approval_item),
        (intervention_records, _human_inbox_intervention_item),
        (sentinel_records, _human_inbox_sentinel_item),
        (
            persona_rows,
            lambda row: _human_inbox_persona_readiness_item(row, snapshot_at=snapshot_at),
        ),
        (promotion_review_records, _human_inbox_promotion_review_item),
    )
    for records, projector in projectors:
        for record in records:
            projected = projector(record)
            if projected is not None:
                items.append(projected)
    items.sort(
        key=lambda item: (
            _HUMAN_INBOX_PRIORITY_RANK.get(str(item.get("priority") or "unknown"), 0),
            str(item.get("created_at") or ""),
            str(item.get("id") or ""),
        ),
        reverse=True,
    )
    return items
def _human_inbox_governance_contributor(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records = list(read_store.list_governance_review_queue_items() or [])
    return records, _dataset_surface_status(
        "governance_review_queue_items",
        snapshot_at=snapshot_at,
        has_data=bool(records),
        missing_message="Governance review queue has no readable source records.",
        source=_dataset_source_after_read("governance_review_queue_items"),
    )
def _human_inbox_approval_contributor(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records = list(read_store.list_approval_queue_items() or [])
    return records, _dataset_surface_status(
        "approval_queue_items",
        snapshot_at=snapshot_at,
        has_data=bool(records),
        missing_message="Approval queue has no readable source records.",
        source=_dataset_source_after_read("approval_queue_items"),
    )
def _human_inbox_intervention_contributor(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records = list(_v5_intervention_records())
    surface = _dataset_surface_status(
        "v5_interventions",
        snapshot_at=snapshot_at,
        has_data=bool(records),
        missing_message="V5 interventions have no readable source records.",
        source=_dataset_source_after_read("v5_interventions"),
    )
    local_ids = {
        str(record.get("intervention_id") or record.get("id") or "")
        for record in _V5_INTERVENTIONS_STORE
        if isinstance(record, dict)
    }
    has_local_record = any(
        str(record.get("intervention_id") or record.get("id") or "") in local_ids
        for record in records
    )
    if has_local_record and surface.get("source") == "missing":
        surface = {**_surface_status(), "source": "bff_local_registry"}
    return records, surface
def _human_inbox_sentinel_contributor(
    snapshot_at: str,
) -> tuple[tuple[bool, List[Dict[str, Any]]], Dict[str, Any]]:
    available, raw_records = read_store.list_sentinel_findings()
    records = list(raw_records or [])
    incidents_source = _dataset_source_after_read("incidents")
    if incidents_source != "missing":
        surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    else:
        surface = _dataset_surface_status(
            "sentinel_findings",
            snapshot_at=snapshot_at,
            source=_dataset_source_after_read("sentinel_findings") if available else "missing",
        )
    return (bool(available), records), surface
def _build_persona_readiness_items(snapshot_at: str) -> List[Dict[str, Any]]:
    """Build only the persona fields consumed by Human Inbox readiness rows.

    The full Fleet projection performs per-persona binding, runtime, strategy,
    source-health, incident, and evolution reads. Human Inbox does not consume
    those fields, so using it here created a large N+1 latency chain. This
    projection deliberately performs one persona read and one league read and
    reuses the loaded personas when deriving market context defaults.
    """
    personas = list(
        read_store.list_personas(include_market_persona_defaults=True) or []
    )
    league_by_persona = {
        str(item.get("persona_id") or item.get("id") or "").strip(): item
        for item in (
            read_store.list_persona_league(
                include_market_persona_defaults=True,
            )
            or []
        )
        if str(item.get("persona_id") or item.get("id") or "").strip()
    }
    context_defaults = _persona_fleet_context_defaults_by_market(personas)
    rows: List[Dict[str, Any]] = []
    for persona in personas:
        persona_id = _persona_id(persona)
        if not persona_id:
            continue
        metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
        context_metadata, _context_persona = _persona_fleet_context_overlay(
            persona,
            metadata,
            context_defaults,
        )
        league_entry = league_by_persona.get(persona_id, {})
        governance_required = bool(
            league_entry.get("governance_required")
            if "governance_required" in league_entry
            else context_metadata.get("governance_required", True)
        )
        recommendation = (
            league_entry.get("recommendation")
            or context_metadata.get("recommended_governance_action")
            or ""
        )
        human_needed = governance_required and str(recommendation).strip().lower() not in {
            "",
            "none",
            "no_change",
        }
        research_status = (
            context_metadata.get("research_status")
            if isinstance(context_metadata.get("research_status"), dict)
            else {}
        )
        current_projects = (
            context_metadata.get("current_research_projects")
            if isinstance(context_metadata.get("current_research_projects"), list)
            else []
        )
        can_deploy = research_status.get("can_deploy")
        if can_deploy is None:
            can_deploy = context_metadata.get("can_deploy")
        rows.append(
            {
                "id": persona_id,
                "persona_id": persona_id,
                "name": persona.get("name") or persona_id,
                "persona_name": persona.get("name") or persona_id,
                "human_needed": human_needed,
                "state": str(
                    metadata.get("persona_status")
                    or league_entry.get("status")
                    or persona.get("status")
                    or persona.get("lifecycle_state")
                    or "unknown"
                ),
                "current_work": context_metadata.get("current_work"),
                "recommendation": recommendation,
                "can_deploy": can_deploy,
                "priority": league_entry.get("priority") or context_metadata.get("priority"),
                "updated_at": (
                    league_entry.get("updated_at")
                    or persona.get("updated_at")
                    or persona.get("last_active_at")
                    or snapshot_at
                ),
                "research_status": _management_json_clone(research_status),
                "current_research_projects": _management_json_clone(current_projects),
                "data_source_status": _management_json_clone(
                    context_metadata.get("data_source_status") or {}
                ),
            }
        )
    return rows
def _human_inbox_persona_contributor(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows = list(_build_persona_readiness_items(snapshot_at) or [])
    return rows, _composed_dataset_surface_status(
        "persona_fleet",
        rows,
        snapshot_at=snapshot_at,
        source="bff_composed",
    )
def _human_inbox_promotion_contributor(
    identity: OperatorIdentity,
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records = _submitted_promotion_review_records(identity, snapshot_at=snapshot_at)
    return records, {**_surface_status(), "source": "command_store"}
def _human_inbox_all_items(
    snapshot_at: Optional[str] = None,
    *,
    identity: Optional[OperatorIdentity] = None,
    source_types: Optional[set[str]] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    snapshot_at = snapshot_at or utc_now()
    include_all = not source_types
    review_records: List[Dict[str, Any]] = []
    approval_records: List[Dict[str, Any]] = []
    intervention_records: List[Dict[str, Any]] = []
    sentinel_available = False
    sentinel_records: List[Dict[str, Any]] = []
    persona_rows: List[Dict[str, Any]] = []
    promotion_review_records: List[Dict[str, Any]] = []
    surfaces: Dict[str, Dict[str, Any]] = {}
    if include_all or "governance_review" in source_types:
        review_records, surfaces["governance_review_queue"] = _human_inbox_governance_contributor(snapshot_at)
    if include_all or "approval" in source_types:
        approval_records, surfaces["approval_queue"] = _human_inbox_approval_contributor(snapshot_at)
    if include_all or "intervention" in source_types:
        intervention_records, surfaces["v5_interventions"] = _human_inbox_intervention_contributor(snapshot_at)
    if include_all or "sentinel_finding" in source_types:
        sentinel_result, surfaces["sentinel_findings"] = _human_inbox_sentinel_contributor(snapshot_at)
        sentinel_available, sentinel_records = sentinel_result
    if include_all or "readiness_blocker" in source_types:
        persona_rows, surfaces["persona_readiness"] = _human_inbox_persona_contributor(snapshot_at)
    if identity is not None and (include_all or "promotion_review" in source_types):
        promotion_review_records, surfaces["promotion_reviews"] = _human_inbox_promotion_contributor(
            identity,
            snapshot_at,
        )
    items = _human_inbox_project_items(
        snapshot_at=snapshot_at,
        review_records=review_records,
        approval_records=approval_records,
        intervention_records=intervention_records,
        sentinel_records=sentinel_records,
        persona_rows=persona_rows,
        promotion_review_records=promotion_review_records,
    )
    return items, {
        "governance_review_records": review_records,
        "approval_records": approval_records,
        "intervention_records": intervention_records,
        "sentinel_available": sentinel_available,
        "sentinel_records": sentinel_records,
        "persona_rows": persona_rows,
        "promotion_review_records": promotion_review_records,
        "surfaces": surfaces,
    }
def _human_inbox_filter_items(
    items: List[Dict[str, Any]],
    *,
    source_type: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
) -> List[Dict[str, Any]]:
    source_types = _human_inbox_csv_filter(source_type)
    statuses = _human_inbox_csv_filter(status)
    priorities = _human_inbox_csv_filter(priority)
    filtered = items
    if source_types:
        filtered = [
            item for item in filtered
            if str(item.get("source_type") or item.get("inboxType") or "").lower() in source_types
        ]
    if statuses:
        filtered = [
            item for item in filtered
            if str(item.get("status") or "").lower() in statuses
            or str(item.get("action_state") or "").lower() in statuses
        ]
    if priorities:
        filtered = [
            item for item in filtered
            if str(item.get("priority") or "").lower() in priorities
            or str(item.get("risk_level") or "").lower() in priorities
        ]
    return filtered
def _human_inbox_summary(items: List[Dict[str, Any]], returned_count: int) -> Dict[str, Any]:
    pending_items = [item for item in items if str(item.get("action_state") or "") == "pending"]
    by_type = _management_count_by(items, "inboxType")
    by_status = _management_count_by(items, "status")
    highest_risk_level = _highest_ranked_value(
        [str(item.get("riskLevel") or item.get("risk_level") or "") for item in items],
        _MANAGEMENT_RISK_LEVEL_ORDER,
    )
    return {
        "total": len(items),
        "total_items": len(items),
        "returned_items": returned_count,
        "pending_items": len(pending_items),
        "by_type": by_type,
        "by_status": by_status,
        "highest_risk_level": highest_risk_level,
        "governance_review_count": len([item for item in items if item.get("source_type") == "governance_review"]),
        "approval_count": len([item for item in items if item.get("source_type") == "approval"]),
        "intervention_count": len([item for item in items if item.get("source_type") == "intervention"]),
        "sentinel_finding_count": len([item for item in items if item.get("source_type") == "sentinel_finding"]),
        "readiness_blocker_count": len([item for item in items if item.get("source_type") == "readiness_blocker"]),
        "critical_count": len([item for item in items if item.get("priority") == "critical"]),
        "high_count": len([item for item in items if item.get("priority") == "high"]),
    }
def _human_inbox_loaded_surface(
    *,
    snapshot_at: str,
    source: str,
    available: bool = True,
    has_data: Optional[bool] = None,
    empty_is_unavailable: bool = False,
    missing_message: Optional[str] = None,
) -> Dict[str, Any]:
    surface = dict(_surface_status())
    surface["source"] = source
    if not available or (empty_is_unavailable and has_data is False):
        surface["status"] = "unavailable"
        surface["source"] = "missing" if not available else source
        if missing_message:
            surface["message"] = missing_message
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )
    return surface
def _human_inbox_surfaces(
    *,
    snapshot_at: str,
    governance_review_records: List[Dict[str, Any]],
    approval_records: List[Dict[str, Any]],
    intervention_records: List[Dict[str, Any]],
    sentinel_available: bool,
    sentinel_records: List[Dict[str, Any]],
    persona_rows: List[Dict[str, Any]],
    promotion_review_records: List[Dict[str, Any]],
    source_types: Optional[set[str]] = None,
    surface_failures: Optional[Dict[str, Dict[str, Any]]] = None,
    loaded_surfaces: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    include_all = not source_types
    failures = surface_failures or {}
    provenance = loaded_surfaces or {}
    contributor_surfaces: Dict[str, Dict[str, Any]] = {}

    if include_all or "governance_review" in source_types:
        contributor_surfaces["governance_review_queue"] = failures.get(
            "governance_review_queue"
        ) or provenance.get("governance_review_queue") or _human_inbox_loaded_surface(
            snapshot_at=snapshot_at,
            source="read_store",
            has_data=bool(governance_review_records),
            empty_is_unavailable=True,
            missing_message="Governance review queue has no readable source records.",
        )
    if include_all or "approval" in source_types:
        contributor_surfaces["approval_queue"] = failures.get(
            "approval_queue"
        ) or provenance.get("approval_queue") or _human_inbox_loaded_surface(
            snapshot_at=snapshot_at,
            source="read_store",
            has_data=bool(approval_records),
            empty_is_unavailable=True,
            missing_message="Approval queue has no readable source records.",
        )
    if include_all or "intervention" in source_types:
        local_intervention_ids = {
            str(record.get("intervention_id") or record.get("id") or "")
            for record in _V5_INTERVENTIONS_STORE
            if isinstance(record, dict)
        }
        has_local_intervention = any(
            str(record.get("intervention_id") or record.get("id") or "") in local_intervention_ids
            for record in intervention_records
        )
        contributor_surfaces["v5_interventions"] = failures.get(
            "v5_interventions"
        ) or provenance.get("v5_interventions") or _human_inbox_loaded_surface(
            snapshot_at=snapshot_at,
            source="bff_local_registry" if has_local_intervention else "read_store",
            has_data=bool(intervention_records),
            empty_is_unavailable=True,
            missing_message="V5 interventions have no readable source records.",
        )
    if include_all or "sentinel_finding" in source_types:
        contributor_surfaces["sentinel_findings"] = failures.get(
            "sentinel_findings"
        ) or provenance.get("sentinel_findings") or _human_inbox_loaded_surface(
            snapshot_at=snapshot_at,
            source="read_store" if sentinel_available else "missing",
            available=sentinel_available,
            has_data=bool(sentinel_records),
            missing_message="Sentinel findings have no readable source records.",
        )
    if include_all or "readiness_blocker" in source_types:
        contributor_surfaces["persona_readiness"] = failures.get(
            "persona_readiness"
        ) or provenance.get("persona_readiness") or _human_inbox_loaded_surface(
            snapshot_at=snapshot_at,
            source="bff_composed",
            has_data=bool(persona_rows),
        )
    if include_all or "promotion_review" in source_types:
        contributor_surfaces["promotion_reviews"] = failures.get(
            "promotion_reviews"
        ) or provenance.get("promotion_reviews") or _human_inbox_loaded_surface(
            snapshot_at=snapshot_at,
            source="command_store",
            has_data=bool(promotion_review_records),
        )

    aggregate_surface = _aggregate_group_surface(
        "human_inbox",
        list(contributor_surfaces.values()),
        snapshot_at=snapshot_at,
        unavailable_message="Human inbox aggregate unavailable.",
        degraded_message="Human inbox aggregate is available, but one or more contributing surfaces are degraded.",
    )
    return {
        "human_inbox": aggregate_surface,
        **contributor_surfaces,
    }
def _human_inbox_payload_from_loaded(
    snapshot_at: str,
    *,
    items: List[Dict[str, Any]],
    sources: Dict[str, Any],
    source_types: Optional[set[str]],
    source_type: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: Optional[int] = 20,
    surface_failures: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    filtered = _human_inbox_filter_items(
        items,
        source_type=source_type,
        status=status,
        priority=priority,
    )
    total = len(filtered)
    if page_size is None:
        page_items = filtered
        next_page_token = None
        returned_page_size = len(page_items)
    else:
        page_items, next_page_token = _page_slice(filtered, page_token, page_size)
        returned_page_size = page_size
    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = _human_inbox_surfaces(
        snapshot_at=snapshot_at,
        governance_review_records=sources["governance_review_records"],
        approval_records=sources["approval_records"],
        intervention_records=sources["intervention_records"],
        sentinel_available=bool(sources["sentinel_available"]),
        sentinel_records=sources["sentinel_records"],
        persona_rows=sources["persona_rows"],
        promotion_review_records=sources["promotion_review_records"],
        source_types=source_types,
        surface_failures=surface_failures,
        loaded_surfaces=sources.get("surfaces"),
    )
    if surface_failures:
        meta["partial"] = True
        meta["degradation"] = {
            "reason": "one_or_more_human_inbox_contributors_incomplete",
            "contributors": sorted(surface_failures),
        }
    summary = _human_inbox_summary(filtered, len(page_items))
    canonical_page_items = _management_prune_camel_aliases(page_items)
    return {
        "data": {
            "id": "management-human-inbox",
            "items": canonical_page_items,
            "summary": summary,
        },
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
            "page_size": returned_page_size,
        },
        "meta": meta,
    }
def _human_inbox_payload(
    snapshot_at: str,
    *,
    identity: Optional[OperatorIdentity] = None,
    source_type: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: Optional[int] = 20,
) -> Dict[str, Any]:
    source_types = _human_inbox_csv_filter(source_type)
    items, sources = _human_inbox_all_items(snapshot_at, identity=identity, source_types=source_types)
    return _human_inbox_payload_from_loaded(
        snapshot_at,
        items=items,
        sources=sources,
        source_types=source_types,
        source_type=source_type,
        status=status,
        priority=priority,
        page_token=page_token,
        page_size=page_size,
    )
_MGMT_NL_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_MGMT_NL_COMMAND_IDEMPOTENCY_STORE: Optional[ManagementNlCommandIdempotencyStore] = None
_MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG: Optional[Tuple[str, float]] = None
_MGMT_NL_COMMAND_RESERVATION_CONTEXT: ContextVar[
    Optional[ManagementNlCommandReservation]
] = ContextVar("management_nl_command_reservation", default=None)
_MGMT_NL_VALID_FOCUS = {"cockpit", "trading_pulse", "portfolio", "persona_fleet", "all"}
_MGMT_NL_FOCUS_ALIASES = {
    "persona": "persona_fleet",
    "personas": "persona_fleet",
    "runtime": "trading_pulse",
    "runtimes": "trading_pulse",
}
_MGMT_NL_MAX_QUESTION_BYTES = 2048
_MGMT_AI_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
_MGMT_NL_MAX_RECENT_TURNS = 12
_MGMT_NL_FE_RECENT_TURNS_CHAR_BUDGET = 32 * 1024
_MGMT_NL_PROVIDER_HISTORY_CHAR_BUDGET = 64 * 1024
_MGMT_NL_UI_ACTION_KINDS = {
    "navigate",
    "openDrawer",
    "selectEntity",
    "setFilter",
    "focusPanel",
    "refreshCurrentView",
    "runBffAction",
}
_MGMT_NL_WRITE_ACTION_KINDS = {"runBffAction"}
_MGMT_NL_CONTROL_REDACTED_QUESTION = "[CONTROL MODE COMMAND REDACTED]"
_MGMT_NL_CONTROL_ACTIVATE_PREFIXES = (
    "/control",
    "/kernel",
    "control mode",
    "kernel mode",
    "控制模式",
    "啟動控制模式",
    "启动控制模式",
    "開啟控制模式",
    "开启控制模式",
    "暗號",
    "暗号",
    "通關密語",
    "通关密语",
)
_MGMT_NL_CONTROL_SEPARATOR_ACTIVATE_PREFIXES = {
    "control mode",
    "kernel mode",
    "控制模式",
    "暗號",
    "暗号",
    "通關密語",
    "通关密语",
}
_MGMT_NL_CONTROL_STATUS_COMMANDS = {
    "/control status",
    "/kernel status",
    "control mode status",
    "kernel mode status",
    "控制模式狀態",
    "控制模式状态",
    "查看控制模式",
}
_MGMT_NL_CONTROL_DEACTIVATE_COMMANDS = {
    "/control off",
    "/control stop",
    "/control deactivate",
    "/kernel off",
    "/kernel stop",
    "/kernel deactivate",
    "control mode off",
    "kernel mode off",
    "退出控制模式",
    "關閉控制模式",
    "关闭控制模式",
    "停用控制模式",
}
_MGMT_AI_USAGE_OBSERVED_SOURCE = "management_ai_bff_audit"
_MGMT_AI_USAGE_OBSERVED_COVERAGE = "bff_observed_management_ai_only"
_MGMT_AI_USAGE_STALE_AFTER_HOURS = 24
_MGMT_NL_HIGH_RISK_REFUSAL_FOLLOWUPS = [
    {
        "label": "Open Human Inbox",
        "route": "/bff/management/human-inbox",
        "rel": "human_inbox",
    }
]
_MGMT_NL_HIGH_RISK_PATTERNS: List[tuple[str, List[str], str]] = [
    # (category_key, trigger_terms, safe_alternatives_hint)
    (
        "live_capital_mutation",
        [
            "allocate capital", "transfer capital", "move capital", "reallocate capital",
            "transfer funds", "move funds", "allocate funds", "withdraw funds",
            "increase allocation", "decrease allocation", "change allocation",
            "rebalance capital", "rebalance portfolio", "set capital", "add capital",
            "remove capital", "fund the pool", "capital injection", "execute trade",
            "place trade", "place order", "buy shares", "sell shares", "liquidate position",
            "配置資金", "轉移資金", "資金轉移", "調倉", "加倉", "減倉", "下單", "買入", "賣出",
        ],
        "Use POST /bff/capital-pools/{id} or the governance approval flow to mutate capital allocations.",
    ),
    (
        "broker_activation",
        [
            "enable live broker", "enable broker", "connect broker", "activate broker",
            "enable the live broker", "connect the live broker", "activate the live broker",
            "start broker", "enable shioaji", "connect shioaji", "enable ibkr",
            "connect ibkr", "enable pantheon_live_broker", "set pantheon_live_broker",
            "turn on broker", "activate live trading", "inject broker credentials",
            "啟用實盤", "開啟實盤", "啟用券商", "連接券商", "啟用 live broker",
        ],
        "Live broker activation requires operator dual-signoff via the human gate. Use PROD-WRITES-001-V2.",
    ),
    (
        "strategy_deployment",
        [
            "deploy strategy", "retire strategy", "promote strategy", "activate strategy",
            "redeploy strategy", "undeploy strategy",
            "rollback strategy", "deprecate strategy", "publish strategy",
            "make strategy live", "push strategy", "deactivate strategy",
            "部署策略", "上線策略", "發布策略", "回滾策略", "停用策略",
        ],
        "Use POST /bff/strategies/{id}/actions with a confirm-token for strategy lifecycle changes.",
    ),
    (
        "persona_activation",
        [
            "activate persona", "deploy persona", "enable persona", "launch persona",
            "start persona", "run persona", "make persona live", "promote persona",
            "deactivate persona", "disable persona", "stop persona",
            "啟用 persona", "啟動 persona", "上線 persona", "部署 persona", "停用 persona",
        ],
        "Use POST /bff/personas/{id}/actions with a confirm-token for persona lifecycle changes.",
    ),
    (
        "runtime_control",
        [
            "restart runtime", "stop runtime", "start runtime", "kill runtime",
            "pause runtime", "resume runtime", "terminate runtime", "shut down runtime",
            "shutdown runtime", "reset runtime", "reboot runtime", "bring runtime back",
            "重啟 runtime", "停止 runtime", "啟動 runtime", "暫停 runtime", "恢復 runtime",
        ],
        "Use POST /bff/runtimes/{id}/actions with appropriate governance gates for runtime control.",
    ),
    (
        "system_mutation",
        [
            "enable live", "disable live", "toggle feature", "enable feature flag",
            "disable feature flag", "set feature flag", "change feature flag",
            "modify production", "update production config", "change production",
            "enable production writes", "disable production writes",
            "set vite_bff_real_writes", "set pantheon_env",
            "切換功能", "更改 production", "修改 production", "開啟 production writes",
        ],
        "System-wide mutations require operator gate approval. Use the appropriate governance route.",
    ),
]
_MGMT_AI_AUDIT_EVENTS: deque = deque(maxlen=500)
def _management_ai_audit_path() -> Optional[str]:
    raw = os.getenv(
        "PANTHEON_MANAGEMENT_AI_AUDIT_PATH",
        "/tmp/pantheon-bff/management-ai-audit.jsonl",
    ).strip()
    if not raw or raw.lower() in {"off", "false", "disabled", "none"}:
        return None
    return raw
def _management_ai_summary_value(value: Any, *, max_len: int = 400) -> Any:
    if isinstance(value, str):
        clean = value.strip()
        if len(clean) > max_len:
            return f"{clean[:max_len]}..."
        return clean
    return value
def _management_ai_surface_summary(surfaces: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for key, value in (surfaces or {}).items():
        if not isinstance(value, dict):
            continue
        summary[str(key)] = {
            clean_key: value.get(clean_key)
            for clean_key in ("status", "source", "reason", "message")
            if value.get(clean_key) is not None
        }
    return summary
def _management_ai_provider_output_summary(provider_payload: Any) -> Dict[str, Any]:
    data = provider_payload.get("data") if isinstance(provider_payload, dict) else {}
    output = data.get("output") if isinstance(data, dict) else {}
    if not isinstance(output, dict):
        output = {}
    events = output.get("json_events")
    if not isinstance(events, list):
        events = []
        stdout = output.get("stdout")
        if isinstance(stdout, str):
            for line in stdout.splitlines():
                clean = line.strip()
                if not clean:
                    continue
                try:
                    loaded = json.loads(clean)
                except json.JSONDecodeError:
                    continue
                if isinstance(loaded, dict):
                    events.append(loaded)

    event_types: List[str] = []
    assistant_messages: List[str] = []
    usage: Optional[Dict[str, Any]] = None
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip()
        if event_type:
            event_types.append(event_type)
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message" and item.get("text") is not None:
            assistant_messages.append(str(_management_ai_summary_value(item.get("text"))))
        if event_type == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event.get("usage")

    return {
        "provider": data.get("provider") if isinstance(data, dict) else None,
        "status": data.get("status") if isinstance(data, dict) else None,
        "returncode": output.get("returncode"),
        "duration_ms": output.get("duration_ms"),
        "json_event_count": len(events),
        "json_event_types": event_types,
        "assistant_messages": assistant_messages[:3],
        "usage": usage,
    }
def _management_ai_record_event(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = jsonable_encoder(
        {
            "event_id": event.get("event_id") or f"mgmt-ai-evt-{uuid.uuid4().hex[:16]}",
            "recorded_at": event.get("recorded_at") or utc_now(),
            **event,
        }
    )
    _MGMT_AI_AUDIT_EVENTS.append(payload)
    path = _management_ai_audit_path()
    if path:
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            log.warning("Failed to persist management AI audit event", exc_info=True)
    return payload
def _management_ai_read_audit_file(limit: int) -> List[Dict[str, Any]]:
    path = _management_ai_audit_path()
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except Exception:
        log.warning("Failed to read management AI audit log", exc_info=True)
        return []
    events: List[Dict[str, Any]] = []
    for line in lines[-max(limit * 4, limit):]:
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            events.append(loaded)
    return events
def _management_ai_event_matches(
    event: Dict[str, Any],
    *,
    session_id: Optional[str],
    trace_id: Optional[str],
    message_id: Optional[str],
    event_type: Optional[str],
) -> bool:
    if session_id and str(event.get("session_id") or "") != session_id:
        return False
    if trace_id and str(event.get("trace_id") or "") != trace_id:
        return False
    if message_id and str(event.get("message_id") or "") != message_id:
        return False
    if event_type and str(event.get("event_type") or "") != event_type:
        return False
    return True
def _management_ai_list_audit_events(
    *,
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    message_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    candidates = _management_ai_read_audit_file(limit) or list(_MGMT_AI_AUDIT_EVENTS)
    filtered = [
        event
        for event in candidates
        if _management_ai_event_matches(
            event,
            session_id=session_id,
            trace_id=trace_id,
            message_id=message_id,
            event_type=event_type,
        )
    ]
    return filtered[-limit:]
def _management_ai_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        clean = str(value).strip()
        return float(clean) if clean else None
    except (TypeError, ValueError):
        return None
def _management_ai_usage_number(usage: Any, *keys: str) -> Optional[float]:
    if not isinstance(usage, dict):
        return None
    for key in keys:
        value = _management_ai_number(usage.get(key))
        if value is not None:
            return value
    return None
def _management_ai_provider_key(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return clean or "unknown"
def _management_ai_provider_display(provider: str) -> str:
    labels = {
        "codex": "Codex CLI",
        "codex_cli": "Codex CLI",
        "claude": "Claude CLI",
        "claude_cli": "Claude CLI",
        "openclaw": "OpenClaw",
    }
    return labels.get(provider, provider)
def _management_ai_provider_route(provider: str, *, stream: bool = False) -> str:
    normalized = _management_ai_provider_key(provider)
    if normalized in {"claude", "claude_cli"}:
        return "POST /api/openclaw-adapter/assistant/claude/invoke"
    if normalized in {"openclaw", "openclaw_agent"}:
        suffix = "/stream" if stream else ""
        return f"POST /api/openclaw-adapter/assistant/providers/openclaw/invoke{suffix}"
    return "POST /api/openclaw-adapter/assistant/providers/codex/invoke"
def _management_ai_event_model(event: Dict[str, Any]) -> str:
    output_summary = event.get("output_summary") if isinstance(event.get("output_summary"), dict) else {}
    usage = output_summary.get("usage") if isinstance(output_summary.get("usage"), dict) else {}
    for value in (
        event.get("model"),
        event.get("model_id"),
        event.get("modelId"),
        event.get("provider_model"),
        event.get("providerModel"),
        output_summary.get("model"),
        output_summary.get("model_id"),
        output_summary.get("modelId"),
        usage.get("model"),
        usage.get("model_id"),
        usage.get("modelId"),
    ):
        clean = str(value or "").strip()
        if clean:
            return clean
    return "default"
def _management_ai_quota_snapshot(provider: Dict[str, Any]) -> Dict[str, Any]:
    usage = provider.get("usage") if isinstance(provider.get("usage"), dict) else None
    quota = provider.get("quota") if isinstance(provider.get("quota"), dict) else None
    source = usage or quota or {}
    return {
        "status": str(source.get("status") or "unknown"),
        "source": str(source.get("source") or "not_configured"),
        "remaining": source.get("remaining"),
        "remaining_percent": source.get("remaining_percent", source.get("remainingPercent")),
        "limit": source.get("limit"),
        "used": source.get("used"),
        "unit": source.get("unit"),
        "reset_at": source.get("reset_at", source.get("resetAt")),
        "updated_at": source.get("updated_at", source.get("updatedAt")),
        "checked_at": source.get("checked_at", source.get("checkedAt")),
        "reason": source.get("reason") or (
            "provider_usage_source_not_configured" if not source else None
        ),
    }
def _management_ai_empty_usage_row(provider: str) -> Dict[str, Any]:
    observed_usage = {
        "source": _MGMT_AI_USAGE_OBSERVED_SOURCE,
        "coverage": _MGMT_AI_USAGE_OBSERVED_COVERAGE,
        "truth_policy": "observed_bff_events_only",
    }
    return {
        "provider": provider,
        "provider_name": _management_ai_provider_display(provider),
        "runtime": None,
        "ready": None,
        "auth_status": None,
        "status": "unknown",
        "live_auth": False,
        "calls": 0,
        "success_count": 0,
        "failed_count": 0,
        "started_count": 0,
        "prompt_bytes": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "duration_ms": 0,
        "average_duration_ms": None,
        "last_used_at": None,
        "last_status": None,
        "last_error": None,
        "quota": _management_ai_quota_snapshot({}),
        "persona_dependencies": {
            "status": "unavailable",
            "count": None,
            "personas": [],
            "source": None,
            "reason": "persona_dependency_inventory_unavailable",
        },
        "observed_usage": dict(observed_usage),
        "models": {},
    }
def _management_ai_empty_model_row(model: str) -> Dict[str, Any]:
    return {
        "model": model,
        "calls": 0,
        "success_count": 0,
        "failed_count": 0,
        "prompt_bytes": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "duration_ms": 0,
        "average_duration_ms": None,
        "last_used_at": None,
        "last_status": None,
    }
def _management_ai_touch_last(row: Dict[str, Any], event: Dict[str, Any], status: str) -> None:
    recorded_at = str(event.get("recorded_at") or "")
    current = _audit_datetime(row.get("last_used_at") or row.get("lastUsedAt"))
    candidate = _audit_datetime(recorded_at)
    if candidate is None or current is None or candidate >= current:
        row["last_used_at"] = recorded_at
        row["last_status"] = status
def _management_ai_usage_age_hours(last_used_at: Any, now_dt: datetime) -> Optional[float]:
    last_dt = _audit_datetime(last_used_at)
    if last_dt is None:
        return None
    return round(max(0.0, (now_dt - last_dt).total_seconds() / 3600), 2)
def _management_ai_finalize_usage_row(
    row: Dict[str, Any],
    *,
    now_dt: datetime,
    window_hours: Optional[int],
    event_limit: int,
    stale_after_hours: int = _MGMT_AI_USAGE_STALE_AFTER_HOURS,
) -> Dict[str, Any]:
    calls = int(row.get("calls") or 0)
    duration = int(row.get("duration_ms") or row.get("durationMs") or 0)
    avg = round(duration / calls) if calls else None
    row["average_duration_ms"] = avg
    age_hours = _management_ai_usage_age_hours(row.get("last_used_at") or row.get("lastUsedAt"), now_dt)
    stale = bool(calls > 0 and age_hours is not None and age_hours > stale_after_hours)
    observed = {
        "source": _MGMT_AI_USAGE_OBSERVED_SOURCE,
        "coverage": _MGMT_AI_USAGE_OBSERVED_COVERAGE,
        "coverage_label": "BFF observed",
        "truth_policy": "observed_bff_events_only",
        "calls": row["calls"],
        "success_count": row["success_count"],
        "failed_count": row["failed_count"],
        "prompt_bytes": row["prompt_bytes"],
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "total_tokens": row["total_tokens"],
        "last_observed_at": row.get("last_used_at"),
        "age_hours": age_hours,
        "stale": stale,
        "stale_after_hours": stale_after_hours,
        "window_hours": window_hours,
        "event_limit": event_limit,
        "message": "Only Management AI calls observed by the BFF audit stream are counted; direct provider CLI usage is not included.",
    }
    row["observed_usage"] = observed
    models = []
    for model_row in row["models"].values():
        model_calls = int(model_row.get("calls") or 0)
        model_duration = int(model_row.get("duration_ms") or model_row.get("durationMs") or 0)
        model_avg = round(model_duration / model_calls) if model_calls else None
        model_row["average_duration_ms"] = model_avg
        models.append(model_row)
    row["models"] = sorted(models, key=lambda item: (-int(item.get("calls") or 0), str(item.get("model") or "")))
    return row
def _assistant_provider_usage_summary(
    *,
    auth_probe: bool = False,
    limit: int = 500,
    window_hours: Optional[int] = 168,
) -> Dict[str, Any]:
    event_limit = min(max(limit, 1), 500)
    now_dt = datetime.now(timezone.utc)
    since_dt = (
        now_dt - timedelta(hours=max(1, int(window_hours)))
        if window_hours is not None and int(window_hours) > 0
        else None
    )
    rows: Dict[str, Dict[str, Any]] = {}

    def ensure_provider(provider_value: Any) -> Dict[str, Any]:
        provider = _management_ai_provider_key(provider_value)
        if provider not in rows:
            rows[provider] = _management_ai_empty_usage_row(provider)
        return rows[provider]

    def ensure_model(row: Dict[str, Any], model: str) -> Dict[str, Any]:
        model_key = str(model or "default")
        models = row["models"]
        if model_key not in models:
            models[model_key] = _management_ai_empty_model_row(model_key)
        return models[model_key]

    provider_list_payload = _assistant_provider_list(auth_probe=auth_probe)
    provider_items = provider_list_payload.get("data") if isinstance(provider_list_payload, dict) else []
    if not isinstance(provider_items, list):
        provider_items = []
    for item in provider_items:
        if not isinstance(item, dict):
            continue
        row = ensure_provider(item.get("provider") or item.get("provider_id") or item.get("providerName"))
        provider_name = str(item.get("provider_name") or item.get("providerName") or row["provider_name"])
        row["provider_name"] = provider_name
        row["runtime"] = item.get("runtime")
        row["ready"] = item.get("ready")
        auth_status = item.get("auth_status") or item.get("authStatus") or item.get("auth") or item.get("status")
        row["auth_status"] = auth_status
        row["status"] = item.get("status") or row["status"]
        live_auth = bool(item.get("ready") is True and str(auth_status or "").lower() in {"ready", "account_session", "authorized"})
        row["live_auth"] = live_auth
        row["quota"] = _management_ai_quota_snapshot(item)
        dependencies = item.get("persona_dependencies", item.get("personaDependencies"))
        if isinstance(dependencies, dict):
            dependency_personas = dependencies.get("personas")
            if not isinstance(dependency_personas, list):
                dependency_personas = []
            dependency_status = str(dependencies.get("status") or "available")
            row["persona_dependencies"] = {
                "status": dependency_status,
                "count": dependencies.get("count", len(dependency_personas)),
                "personas": dependency_personas,
                "source": dependencies.get("source") or "provider_inventory",
                "reason": dependencies.get("reason"),
            }
        else:
            dependency_personas = item.get("dependent_personas", item.get("dependentPersonas"))
            if isinstance(dependency_personas, list):
                row["persona_dependencies"] = {
                    "status": "available",
                    "count": len(dependency_personas),
                    "personas": dependency_personas,
                    "source": "provider_inventory",
                    "reason": None,
                }
        smoke = item.get("live_smoke") if isinstance(item.get("live_smoke"), dict) else {}
        reauth = item.get("reauth") if isinstance(item.get("reauth"), dict) else {}
        row["provider_auth"] = {
            "status": auth_status or "not_checked",
            "authenticated": str(auth_status or "").lower() in {"ready", "account_session", "authorized"},
            "source": item.get("auth_source") or item.get("authSource") or "provider_probe",
        }
        row["live_smoke"] = {
            "status": smoke.get("status") or item.get("smoke_status") or "not_checked",
            "passed": smoke.get("passed") is True,
            "checked_at": smoke.get("checked_at") or smoke.get("checkedAt") or item.get("last_live_smoke_at"),
            "reason": smoke.get("reason") or item.get("smoke_reason"),
        }
        row["reauth"] = {
            "status": reauth.get("status") or item.get("reauth_status") or "not_started",
            "code_entry_required": bool(reauth.get("code_entry_required", reauth.get("codeEntryRequired", False))),
            "readiness_recheck_required": bool(reauth.get("readiness_recheck_required", reauth.get("readinessRecheckRequired", False))),
        }
        row["readiness"] = {
            "ready": item.get("ready") is True,
            "proof": item.get("readiness_proof") or "provider_probe",
            "mount_ready_is_sufficient": False,
            "reason": item.get("reason"),
        }

    started_by_run: Dict[str, Dict[str, Any]] = {}
    events = _management_ai_list_audit_events(limit=event_limit)
    considered_events = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_dt = _audit_datetime(event.get("recorded_at"))
        if since_dt is not None and event_dt is not None and event_dt < since_dt:
            continue
        event_type = str(event.get("event_type") or "")
        if not event_type.startswith("management_ai.provider."):
            continue
        considered_events += 1
        provider = event.get("provider") or "unknown"
        run_id = str(event.get("provider_run_id") or event.get("trace_id") or event.get("message_id") or "")
        row = ensure_provider(provider)
        model = _management_ai_event_model(event)
        model_row = ensure_model(row, model)
        if event_type == "management_ai.provider.started":
            if run_id:
                started_by_run[run_id] = event
            prompt_bytes = int(_management_ai_number(event.get("prompt_bytes")) or 0)
            row["started_count"] += 1
            row["prompt_bytes"] += prompt_bytes
            model_row["prompt_bytes"] += prompt_bytes
            _management_ai_touch_last(row, event, "started")
            _management_ai_touch_last(model_row, event, "started")
            continue

        if event_type not in {"management_ai.provider.completed", "management_ai.provider.failed"}:
            continue
        source_started = started_by_run.get(run_id)
        if source_started is not None:
            prompt_bytes = int(_management_ai_number(source_started.get("prompt_bytes")) or 0)
            if row["started_count"] == 0:
                row["prompt_bytes"] += prompt_bytes
                model_row["prompt_bytes"] += prompt_bytes
        duration_ms = int(_management_ai_number(event.get("duration_ms")) or 0)
        output_summary = event.get("output_summary") if isinstance(event.get("output_summary"), dict) else {}
        usage = output_summary.get("usage") if isinstance(output_summary.get("usage"), dict) else {}
        input_tokens = int(_management_ai_usage_number(usage, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens") or 0)
        output_tokens = int(_management_ai_usage_number(usage, "output_tokens", "outputTokens", "completion_tokens", "completionTokens") or 0)
        total_tokens = int(_management_ai_usage_number(usage, "total_tokens", "totalTokens") or 0)
        if total_tokens == 0:
            total_tokens = input_tokens + output_tokens
        failed = event_type == "management_ai.provider.failed"
        status = "failed" if failed else str(event.get("provider_state") or "completed")
        for target in (row, model_row):
            target["calls"] += 1
            target["duration_ms"] += duration_ms
            target["input_tokens"] += input_tokens
            target["output_tokens"] += output_tokens
            target["total_tokens"] += total_tokens
            if failed:
                target["failed_count"] += 1
            else:
                target["success_count"] += 1
            _management_ai_touch_last(target, event, status)
        if failed:
            row["last_error"] = event.get("error_code") or event.get("error_message")

    provider_rows = [
        _management_ai_finalize_usage_row(
            row,
            now_dt=now_dt,
            window_hours=window_hours,
            event_limit=event_limit,
        )
        for row in rows.values()
    ]
    provider_rows.sort(key=lambda item: (not bool(item.get("live_auth")), -int(item.get("calls") or 0), str(item.get("provider") or "")))
    totals = {
        "providers": len(provider_rows),
        "live_auth_count": sum(1 for row in provider_rows if row.get("live_auth")),
        "calls": sum(int(row.get("calls") or 0) for row in provider_rows),
        "success_count": sum(int(row.get("success_count") or 0) for row in provider_rows),
        "failed_count": sum(int(row.get("failed_count") or 0) for row in provider_rows),
        "input_tokens": sum(int(row.get("input_tokens") or 0) for row in provider_rows),
        "output_tokens": sum(int(row.get("output_tokens") or 0) for row in provider_rows),
        "total_tokens": sum(int(row.get("total_tokens") or 0) for row in provider_rows),
    }
    return {
        "status": "ok",
        "data": {
            "providers": provider_rows,
            "totals": totals,
            "quota": {
                "truth_policy": "provider_snapshot_only",
                "missing_source_means": "quota remaining is unknown, not zero",
            },
            "usage": {
                "truth_policy": "observed_bff_events_only",
                "coverage": _MGMT_AI_USAGE_OBSERVED_COVERAGE,
                "source": _MGMT_AI_USAGE_OBSERVED_SOURCE,
                "stale_after_hours": _MGMT_AI_USAGE_STALE_AFTER_HOURS,
                "missing_source_means": "direct provider CLI usage is unknown unless a provider usage source is configured",
            },
        },
        "meta": {
            "auth_probe": auth_probe,
            "event_limit": event_limit,
            "event_count": considered_events,
            "window_hours": window_hours,
            "since": since_dt.isoformat().replace("+00:00", "Z") if since_dt is not None else None,
            "provider_snapshot_status": provider_list_payload.get("status") if isinstance(provider_list_payload, dict) else None,
        },
    }
def _management_ai_href(route: str, **params: Optional[str]) -> str:
    clean_params = {
        key: str(value)
        for key, value in params.items()
        if value not in (None, "")
    }
    if not clean_params:
        return route
    return f"{route}?{urlencode(clean_params)}"
def _management_ai_audit_href(
    *,
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    message_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> str:
    return _management_ai_href(
        "/bff/management/ai/audit",
        session_id=session_id,
        trace_id=trace_id,
        message_id=message_id,
        event_type=event_type,
    )
def _management_ai_conversation_href(session_id: str, *, trace_id: Optional[str] = None) -> str:
    route = f"/bff/management/ai/conversations/{quote(str(session_id or ''), safe='')}"
    return route
_MGMT_AI_CONVERSATION_STORE: Optional[ManagementAiConversationStore] = None


def _management_ai_conversation_store() -> ManagementAiConversationStore:
    global _MGMT_AI_CONVERSATION_STORE
    if _MGMT_AI_CONVERSATION_STORE is None:
        _MGMT_AI_CONVERSATION_STORE = ManagementAiConversationStore()
    return _MGMT_AI_CONVERSATION_STORE
def _management_ai_attachment_url(attachment_id: str) -> str:
    return f"/bff/management/ai/attachments/{quote(str(attachment_id or ''), safe='')}"
def _management_ai_attachment_api_payload(attachment: Dict[str, Any]) -> Dict[str, Any]:
    attachment_id = str(
        attachment.get("id")
        or attachment.get("attachmentId")
        or attachment.get("attachment_id")
        or ""
    ).strip()
    mime_type = str(attachment.get("mimeType") or attachment.get("mime_type") or "application/octet-stream")
    size_bytes = int(attachment.get("sizeBytes") or attachment.get("size_bytes") or 0)
    return {
        "id": attachment_id,
        "attachment_id": attachment_id,
        "kind": str(attachment.get("kind") or "file"),
        "mime_type": mime_type,
        "filename": str(attachment.get("filename") or attachment_id or "attachment"),
        "size_bytes": size_bytes,
        "url": _management_ai_attachment_url(attachment_id) if attachment_id else "",
    }
def _management_ai_turn_api_payload(turn: Dict[str, Any]) -> Dict[str, Any]:
    attachments = [
        _management_ai_attachment_api_payload(item)
        for item in (turn.get("attachments") or [])
        if isinstance(item, dict)
    ]
    provider_status = (
        turn.get("provider_status")
        if isinstance(turn.get("provider_status"), dict)
        else turn.get("providerStatus")
        if isinstance(turn.get("providerStatus"), dict)
        else None
    )
    ui_actions = (
        turn.get("ui_actions")
        if isinstance(turn.get("ui_actions"), list)
        else turn.get("uiActions")
        if isinstance(turn.get("uiActions"), list)
        else []
    )
    payload = {
        "id": turn.get("id"),
        "turn_id": turn.get("turn_id") or turn.get("turnId") or turn.get("id"),
        "message_id": turn.get("message_id") or turn.get("id"),
        "session_id": turn.get("session_id") or turn.get("sessionId"),
        "trace_id": turn.get("trace_id") or turn.get("traceId"),
        "role": turn.get("role"),
        "text": turn.get("text") or "",
        "content": turn.get("text") or "",
        "created_at": turn.get("created_at") or turn.get("createdAt"),
        "provider_status": provider_status,
        "attachments": attachments,
        "ui_actions": ui_actions,
        "actions": ui_actions,
    }
    ui_snapshot = (
        turn.get("ui_snapshot")
        if isinstance(turn.get("ui_snapshot"), dict)
        else turn.get("uiSnapshot")
        if isinstance(turn.get("uiSnapshot"), dict)
        else None
    )
    if ui_snapshot is not None:
        payload["ui_snapshot"] = ui_snapshot
    return payload
def _management_ai_require_session_access(
    session: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    tenant_id: Optional[str],
) -> None:
    owner_id = str(session.get("ownerId") or session.get("owner_id") or "").strip()
    session_tenant_id = str(session.get("tenantId") or session.get("tenant_id") or "").strip()
    clean_tenant_id = str(tenant_id or "").strip()
    if owner_id and owner_id == identity.operator_id:
        return
    if clean_tenant_id and session_tenant_id and clean_tenant_id == session_tenant_id:
        return
    raise _bff_error(
        403,
        ErrorCode.FORBIDDEN,
        "Management AI session is not visible to this operator",
        "management_ai_session_not_visible",
        precondition_failed="management_ai_session_visibility",
    )
def _management_ai_session_not_found(session_id: str) -> HTTPException:
    clean_session_id = str(session_id or "").strip()
    return _bff_error(
        404,
        ErrorCode.RESOURCE_NOT_FOUND,
        f"Management AI session not found: {clean_session_id!r}",
        "management_ai_session_not_found",
        precondition_failed="management_ai_session",
    )
def _management_ai_get_visible_session_or_404(
    session_id: str,
    identity: OperatorIdentity,
    *,
    tenant_id: Optional[str],
) -> Dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    session = _management_ai_conversation_store().get_session(clean_session_id)
    if session is None:
        raise _management_ai_session_not_found(clean_session_id)
    try:
        _management_ai_require_session_access(session, identity, tenant_id=tenant_id)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise _management_ai_session_not_found(clean_session_id) from exc
        raise
    return session
def _management_ai_get_session_or_404(
    session_id: str,
    identity: OperatorIdentity,
    *,
    tenant_id: Optional[str],
) -> Dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    session = _management_ai_conversation_store().get_session(clean_session_id)
    if session is None:
        raise _management_ai_session_not_found(clean_session_id)
    _management_ai_require_session_access(session, identity, tenant_id=tenant_id)
    return session
def _management_ai_ensure_session(
    *,
    session_id: str,
    identity: OperatorIdentity,
    tenant_id: Optional[str],
    now: str,
    title: str,
) -> Dict[str, Any]:
    store = _management_ai_conversation_store()
    existing = store.get_session(session_id)
    if existing is not None:
        _management_ai_require_session_access(existing, identity, tenant_id=tenant_id)
    try:
        return store.upsert_session(
            session_id=session_id,
            owner_id=identity.operator_id,
            tenant_id=tenant_id,
            now=now,
            title=title,
        )
    except Exception as exc:
        log.warning("Failed to persist Management AI session", exc_info=True)
        raise _bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Management AI session store write failed",
            str(exc),
            precondition_failed="management_ai_session_store",
        )
def _management_ai_store_attachments(
    *,
    attachments: Any,
    session_id: str,
    turn_id: str,
) -> List[Dict[str, Any]]:
    try:
        return _management_ai_conversation_store().store_attachments(
            attachments,
            session_id=session_id,
            turn_id=turn_id,
        )
    except ManagementAiAttachmentError as exc:
        status_code = int(getattr(exc, "status_code", 422) or 422)
        code = ErrorCode.REQUEST_TOO_LARGE if status_code == 413 else ErrorCode.VALIDATION_FAILED
        raise _bff_error(
            status_code,
            code,
            (
                "Management AI attachment payload is too large"
                if status_code == 413
                else "Management AI attachment payload is invalid"
            ),
            str(exc),
            precondition_failed=getattr(exc, "precondition_failed", "management_ai_attachment"),
            details_extra=getattr(exc, "details", {}),
        )
    except ValueError as exc:
        raise _bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "Management AI attachment payload is invalid",
            str(exc),
            precondition_failed="management_ai_attachment",
        )
    except Exception as exc:
        log.warning("Failed to persist Management AI attachment", exc_info=True)
        raise _bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Management AI attachment store write failed",
            str(exc),
            precondition_failed="management_ai_attachment_store",
        )
def _management_ai_append_turn(
    *,
    turn_id: str,
    session_id: str,
    role: str,
    text: str,
    created_at: str,
    trace_id: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    provider_status: Optional[Dict[str, Any]] = None,
    ui_snapshot: Optional[Dict[str, Any]] = None,
    ui_actions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    try:
        return _management_ai_conversation_store().append_turn(
            turn_id=turn_id,
            session_id=session_id,
            role=role,
            text=text,
            created_at=created_at,
            trace_id=trace_id,
            attachments=attachments,
            provider_status=provider_status,
            ui_snapshot=ui_snapshot,
            ui_actions=ui_actions,
        )
    except Exception as exc:
        log.warning("Failed to persist Management AI turn", exc_info=True)
        raise _bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Management AI turn store write failed",
            str(exc),
            precondition_failed="management_ai_turn_store",
        )
def _management_ai_server_conversation_context(
    *,
    session_id: str,
    client_hint: Dict[str, Any],
) -> Dict[str, Any]:
    stored_turns = _management_ai_conversation_store().list_turns(session_id)
    turns = []
    for turn in stored_turns:
        api_turn = _management_ai_turn_api_payload(turn)
        turns.append(
            {
                "id": api_turn.get("id"),
                "role": api_turn.get("role"),
                "content": api_turn.get("text") or "",
                "text": api_turn.get("text") or "",
                "created_at": api_turn.get("created_at"),
                "attachments": api_turn.get("attachments") or [],
                "provider_status": api_turn.get("provider_status"),
                "trace_id": api_turn.get("trace_id"),
            }
        )
    provider_turns, history_budget = _management_ai_provider_history_window(turns)
    return {
        "recent_turns": provider_turns,
        "all_turns": provider_turns,
        "turn_count": len(provider_turns),
        "stored_turn_count": len(turns),
        "source": "server",
        "history_source": "management_ai_store",
        "history_char_budget": history_budget["history_char_budget"],
        "history_estimated_chars": history_budget["history_estimated_chars"],
        "history_truncated": history_budget["history_truncated"],
        "history_omitted_turn_count": history_budget["history_omitted_turn_count"],
        "summary": client_hint.get("summary") or "",
        "client_hint": client_hint,
        "max_recent_turns": None,
    }
def _management_ai_provider_history_size(turns: List[Dict[str, Any]]) -> int:
    return len(json.dumps(turns, sort_keys=True, ensure_ascii=True))
def _management_ai_provider_history_window(
    turns: List[Dict[str, Any]],
    *,
    char_budget: int = _MGMT_NL_PROVIDER_HISTORY_CHAR_BUDGET,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if _management_ai_provider_history_size(turns) <= char_budget:
        return list(turns), {
            "history_char_budget": char_budget,
            "history_estimated_chars": _management_ai_provider_history_size(turns),
            "history_truncated": False,
            "history_omitted_turn_count": 0,
        }

    selected: List[Dict[str, Any]] = []
    for turn in reversed(turns):
        candidate = [turn, *selected]
        if _management_ai_provider_history_size(candidate) > char_budget:
            if selected:
                break
            selected = [_management_ai_provider_history_minimal_turn(turn)]
            break
        selected = candidate

    return selected, {
        "history_char_budget": char_budget,
        "history_estimated_chars": _management_ai_provider_history_size(selected),
        "history_truncated": True,
        "history_omitted_turn_count": max(0, len(turns) - len(selected)),
    }
def _management_ai_provider_history_minimal_turn(turn: Dict[str, Any]) -> Dict[str, Any]:
    text = str(turn.get("content") or turn.get("text") or "")
    trimmed_text = _mgmt_nl_trim_text(text, max_len=2048)
    return {
        "id": turn.get("id"),
        "role": turn.get("role"),
        "content": trimmed_text,
        "text": trimmed_text,
        "created_at": turn.get("created_at") or turn.get("createdAt"),
        "trace_id": turn.get("trace_id") or turn.get("traceId"),
    }
def _mgmt_nl_normalize_focus(value: Any) -> str:
    focus = str(value or "all").strip().lower()
    focus = _MGMT_NL_FOCUS_ALIASES.get(focus, focus)
    if focus not in _MGMT_NL_VALID_FOCUS:
        return "all"
    return focus
def _mgmt_nl_trim_text(value: Any, *, max_len: int = 4000) -> str:
    clean = re.sub(r"\s+", " ", str(value or "").strip())
    if len(clean) > max_len:
        return f"{clean[:max_len]}..."
    return clean
def _mgmt_nl_normalize_conversation_context(value: Any) -> Dict[str, Any]:
    conversation = value if isinstance(value, dict) else {}
    raw_turns = conversation.get("recentTurns")
    if raw_turns is None:
        raw_turns = conversation.get("recent_turns")
    recent_turns: List[Dict[str, str]] = []
    if isinstance(raw_turns, list):
        for raw_turn in raw_turns[-_MGMT_NL_MAX_RECENT_TURNS:]:
            if not isinstance(raw_turn, dict):
                continue
            role = str(raw_turn.get("role") or "").strip().lower()
            if role not in {"user", "assistant", "system"}:
                continue
            content = _mgmt_nl_trim_text(
                raw_turn.get("content") if raw_turn.get("content") is not None else raw_turn.get("text"),
                max_len=2000,
            )
            if not content:
                continue
            recent_turns.append({"role": role, "content": content, "text": content})
    summary = _mgmt_nl_trim_text(conversation.get("summary"), max_len=4000)
    return {
        "recent_turns": recent_turns,
        "summary": summary,
        "max_recent_turns": _MGMT_NL_MAX_RECENT_TURNS,
    }
def _mgmt_nl_normalize_action_descriptor(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or value.get("type") or "").strip()
    if kind not in _MGMT_NL_UI_ACTION_KINDS:
        return None
    descriptor: Dict[str, Any] = {
        "kind": kind,
        "description": _mgmt_nl_trim_text(value.get("description"), max_len=500),
        "paramsSchema": _mgmt_nl_trim_text(
            value.get("paramsSchema") if value.get("paramsSchema") is not None else value.get("params_schema"),
            max_len=1000,
        ),
    }
    if value.get("label") is not None:
        descriptor["label"] = _mgmt_nl_trim_text(value.get("label"), max_len=120)
    return descriptor
def _mgmt_nl_normalize_available_ui_actions(value: Any) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    if not isinstance(value, list):
        return actions
    for item in value:
        descriptor = _mgmt_nl_normalize_action_descriptor(item)
        if not descriptor:
            continue
        kind = str(descriptor.get("kind") or "")
        if kind in seen:
            continue
        seen.add(kind)
        actions.append(descriptor)
    return actions
def _mgmt_nl_normalize_ui_context(value: Any, *, operator_context: str) -> Dict[str, Any]:
    ui = value if isinstance(value, dict) else {}
    current_route = str(ui.get("currentRoute") or ui.get("current_route") or "/management").strip() or "/management"
    selected_entity = ui.get("selectedEntity") if "selectedEntity" in ui else ui.get("selected_entity")
    if not isinstance(selected_entity, dict):
        selected_entity = None
    visible_panels = ui.get("visiblePanels") if "visiblePanels" in ui else ui.get("visible_panels")
    if not isinstance(visible_panels, list):
        visible_panels = []
    filters = ui.get("filters") if isinstance(ui.get("filters"), dict) else {}
    available_ui_actions = ui.get("availableUiActions")
    if available_ui_actions is None:
        available_ui_actions = ui.get("available_ui_actions")
    normalized = {
        "currentRoute": current_route,
        "current_route": current_route,
        "selectedEntity": selected_entity,
        "selected_entity": selected_entity,
        "visiblePanels": [str(item) for item in visible_panels[:20] if str(item or "").strip()],
        "visible_panels": [str(item) for item in visible_panels[:20] if str(item or "").strip()],
        "filters": filters,
        "availableUiActions": _mgmt_nl_normalize_available_ui_actions(available_ui_actions),
        "available_ui_actions": _mgmt_nl_normalize_available_ui_actions(available_ui_actions),
    }
    if operator_context:
        normalized["legacyContext"] = operator_context
        normalized["legacy_context"] = operator_context
    return normalized
def _mgmt_nl_frontend_selected_entity(ui_snapshot: Dict[str, Any], *, focus: str) -> Dict[str, Any]:
    selected = ui_snapshot.get("selectedEntity")
    route = str(ui_snapshot.get("currentRoute") or "/management")
    if isinstance(selected, dict):
        entity_type = str(selected.get("entityType") or selected.get("entity_type") or selected.get("kind") or "").strip()
        entity_id = str(selected.get("entityId") or selected.get("entity_id") or selected.get("id") or "").strip()
        if entity_type and entity_id:
            return {
                "entityType": entity_type,
                "entityId": entity_id,
                "label": str(selected.get("label") or entity_id),
                "route": route,
            }
    return {
        "entityType": "management_nl_focus",
        "entityId": focus,
        "label": focus,
        "route": route,
    }
def _mgmt_nl_allowed_action_kinds(ui_snapshot: Dict[str, Any]) -> Set[str]:
    actions = ui_snapshot.get("availableUiActions")
    if not isinstance(actions, list):
        return set()
    return {
        str(item.get("kind") or "")
        for item in actions
        if isinstance(item, dict) and str(item.get("kind") or "") in _MGMT_NL_UI_ACTION_KINDS
    }
def _mgmt_nl_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if not clean or clean[0] not in "{[":
        return None
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return None
def _mgmt_nl_find_action_values(value: Any, *, depth: int = 0) -> List[Any]:
    if depth > 8:
        return []
    parsed = _mgmt_nl_jsonish(value)
    if parsed is not None:
        return _mgmt_nl_find_action_values(parsed, depth=depth + 1)
    if isinstance(value, list):
        found: List[Any] = []
        for item in value:
            found.extend(_mgmt_nl_find_action_values(item, depth=depth + 1))
        return found
    if not isinstance(value, dict):
        return []

    found = []
    actions = value.get("actions")
    if isinstance(actions, list):
        found.extend(actions)
    for key in ("data", "output", "final", "answer", "message", "content", "text", "item", "delta", "json_events"):
        if key in value:
            found.extend(_mgmt_nl_find_action_values(value.get(key), depth=depth + 1))
    stdout = value.get("stdout")
    if isinstance(stdout, str):
        for line in stdout.splitlines():
            found.extend(_mgmt_nl_find_action_values(line, depth=depth + 1))
    return found
def _mgmt_nl_action_params_valid(kind: str, params: Dict[str, Any]) -> bool:
    if kind == "navigate":
        return bool(str(params.get("to") or params.get("route") or "").strip())
    if kind == "openDrawer":
        return bool(str(params.get("drawer") or "").strip())
    if kind == "selectEntity":
        return bool(str(params.get("kind") or "").strip() and str(params.get("id") or "").strip())
    if kind == "setFilter":
        return bool(str(params.get("key") or "").strip())
    if kind == "focusPanel":
        return bool(str(params.get("panel") or "").strip())
    if kind == "refreshCurrentView":
        return True
    if kind == "runBffAction":
        endpoint = str(params.get("endpoint") or "").strip()
        return endpoint.startswith("/bff/") or endpoint.startswith("/api/v1/")
    return False
def _mgmt_nl_extract_provider_actions(provider_payload: Any, *, allowed_action_kinds: Set[str]) -> List[Dict[str, Any]]:
    if not allowed_action_kinds:
        return []
    actions: List[Dict[str, Any]] = []
    for index, raw_action in enumerate(_mgmt_nl_find_action_values(provider_payload), start=1):
        if not isinstance(raw_action, dict):
            continue
        kind = str(raw_action.get("kind") or raw_action.get("type") or "").strip()
        if kind not in allowed_action_kinds or kind not in _MGMT_NL_UI_ACTION_KINDS:
            continue
        params = raw_action.get("params") if isinstance(raw_action.get("params"), dict) else {}
        if not _mgmt_nl_action_params_valid(kind, params):
            continue
        requires_confirmation = bool(
            raw_action.get("requiresConfirmation")
            if "requiresConfirmation" in raw_action
            else raw_action.get("requires_confirmation")
        )
        if kind in _MGMT_NL_WRITE_ACTION_KINDS:
            requires_confirmation = True
        actions.append(
            {
                "id": str(raw_action.get("id") or f"act_{index:02d}"),
                "kind": kind,
                "label": _mgmt_nl_trim_text(raw_action.get("label") or kind, max_len=120),
                "rationale": _mgmt_nl_trim_text(
                    raw_action.get("rationale") or raw_action.get("reason") or "",
                    max_len=500,
                ),
                "params": params,
                "requiresConfirmation": requires_confirmation,
            }
        )
    return actions[:6]
def _assistant_control_mode_for_identity(
    identity: OperatorIdentity,
    *,
    management_session_id: Optional[str] = None,
    touch: bool = False,
) -> Dict[str, Any]:
    store = _ASSISTANT_CONTROL_MODE_STORE
    if store is None:
        return {
            "state": "inactive",
            "active": False,
            "reason": "control_mode_store_unavailable",
            "configured": False,
        }
    try:
        return store.status_for_actor(
            identity.operator_id,
            management_session_id=management_session_id,
            touch=touch,
        )
    except Exception:
        log.warning("Failed to read assistant control mode status", exc_info=True)
        return {
            "state": "inactive",
            "active": False,
            "reason": "control_mode_status_unavailable",
        }
def _mgmt_nl_identity_with_control_mode(
    identity: OperatorIdentity,
    control_mode: Dict[str, Any],
) -> OperatorIdentity:
    if not isinstance(control_mode, dict) or not control_mode.get("active"):
        return identity
    claims = dict(identity.claims or {})
    raw_caps = claims.get("capabilities") or claims.get("capability") or []
    if isinstance(raw_caps, str):
        raw_caps = re.split(r"[\s,]+", raw_caps)
    if not isinstance(raw_caps, list):
        raw_caps = []
    caps = _dedupe_nonblank_strings([
        *raw_caps,
        *(control_mode.get("capabilities") if isinstance(control_mode.get("capabilities"), list) else []),
        "assistant.kernel",
    ])
    claims["capabilities"] = caps
    try:
        return identity.model_copy(update={"claims": claims})
    except AttributeError:
        return OperatorIdentity(
            operator_id=identity.operator_id,
            roles=identity.roles,
            mfa_verified=identity.mfa_verified,
            claims=claims,
            token_kind=identity.token_kind,
        )
def _mgmt_nl_validate_question_size(question: str) -> None:
    question_size = len(question.encode("utf-8"))
    if question_size <= _MGMT_NL_MAX_QUESTION_BYTES:
        return
    raise _bff_error(
        413,
        ErrorCode.REQUEST_TOO_LARGE,
        "Management NL question exceeds the maximum size",
        f"question must be at most {_MGMT_NL_MAX_QUESTION_BYTES} bytes",
        precondition_failed="question_size",
        suggestion="Shorten the question and attach large context through an approved evidence route",
        details_extra={
            "maxQuestionBytes": _MGMT_NL_MAX_QUESTION_BYTES,
            "actualQuestionBytes": question_size,
        },
    )
def _mgmt_nl_control_store() -> Optional[Any]:
    store = _ASSISTANT_CONTROL_MODE_STORE
    if store is None:
        return None
    return store
def _mgmt_nl_control_strip_activation_prefix(clean_question: str) -> Optional[str]:
    for prefix in sorted(_MGMT_NL_CONTROL_ACTIVATE_PREFIXES, key=len, reverse=True):
        if not clean_question.lower().startswith(prefix.lower()):
            continue
        raw_remainder = clean_question[len(prefix):]
        if prefix.lower() in _MGMT_NL_CONTROL_SEPARATOR_ACTIVATE_PREFIXES:
            if not re.match(r"^\s*(?:是|為|为|:|：|=|＝)", raw_remainder):
                continue
        remainder = raw_remainder.strip()
        remainder = re.sub(r"^(?:on|activate|啟動|启动|開啟|开启)\b", "", remainder, flags=re.IGNORECASE).strip()
        remainder = re.sub(r"^(?:是|為|为|:|：|=|＝|\s)+", "", remainder).strip()
        return remainder or None
    return None
def _mgmt_nl_parse_control_command(question: str) -> Optional[Dict[str, Any]]:
    clean_question = re.sub(r"\s+", " ", str(question or "").strip())
    if not clean_question:
        return None

    lowered = clean_question.lower()
    if lowered in _MGMT_NL_CONTROL_STATUS_COMMANDS:
        return {"kind": "status", "source": "explicit"}
    if lowered in _MGMT_NL_CONTROL_DEACTIVATE_COMMANDS:
        return {"kind": "deactivate", "source": "explicit"}

    prefixed_passphrase = _mgmt_nl_control_strip_activation_prefix(clean_question)
    if prefixed_passphrase is not None:
        return {
            "kind": "activate",
            "source": "explicit",
            "passphrase": prefixed_passphrase,
        }

    store = _mgmt_nl_control_store()
    matcher = getattr(store, "matches_passphrase", None)
    if callable(matcher):
        try:
            if matcher(clean_question):
                return {
                    "kind": "activate",
                    "source": "direct_passphrase",
                    "passphrase": clean_question,
                }
        except Exception:
            log.warning("Failed to match management NL direct control passphrase", exc_info=True)
    return None
def _mgmt_nl_positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed
def _mgmt_nl_control_options(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_options = payload.get("controlMode")
    if raw_options is None:
        raw_options = payload.get("control_mode")
    options = raw_options if isinstance(raw_options, dict) else {}
    result = dict(options)
    for key in ("mode", "ttlSeconds", "ttl_seconds", "idleTtlSeconds", "idle_ttl_seconds"):
        if key in payload and key not in result:
            result[key] = payload.get(key)
    return result
def _mgmt_nl_raise_control_mode_actor_error(identity: OperatorIdentity) -> None:
    from .assistant.control_mode import (
        CONTROL_MODE_CAPABILITY_PREFIX,
        CONTROL_MODE_ROLES,
        actor_has_control_role,
        actor_has_kernel_capability,
    )

    if not actor_has_control_role(identity):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Control mode requires operator or admin role",
            "Actor does not hold a role allowed to activate control mode",
            precondition_failed="control_mode_role",
            details_extra={
                "field": "roles",
                "required_roles": sorted(CONTROL_MODE_ROLES),
            },
        )
    if not getattr(identity, "mfa_verified", False):
        raise _bff_error(
            403,
            ErrorCode.AUTH_REQUIRED,
            "Control mode requires MFA",
            "Actor must complete MFA before activating control mode",
            precondition_failed="control_mode_mfa",
            details_extra={"field": "mfa"},
        )
    if not actor_has_kernel_capability(identity):
        raise _bff_error(
            422,
            ErrorCode.BUSINESS_RULE_VIOLATION,
            "Control mode requires assistant kernel capability",
            f"Actor capabilities must include a value starting with {CONTROL_MODE_CAPABILITY_PREFIX!r}",
            precondition_failed="control_mode_capability",
            details_extra={
                "field": "capabilities",
                "required_capability_prefix": CONTROL_MODE_CAPABILITY_PREFIX,
            },
        )
def _mgmt_nl_require_mode_capability(identity: OperatorIdentity, mode: Any) -> None:
    from .assistant.control_mode import actor_capabilities

    mode_value = str(getattr(mode, "value", mode) or "").strip()
    required = f"assistant.{mode_value.replace('_', '.')}"
    if required in set(actor_capabilities(identity)):
        return
    raise _bff_error(
        403,
        ErrorCode.FORBIDDEN,
        f"Control mode {mode_value} requires {required} capability",
        "The authenticated actor does not hold the exact capability required for the requested mode.",
        precondition_failed="control_mode_capability",
        details_extra={
            "field": "capabilities",
            "reason": "mode_capability_missing",
            "required_capability": required,
        },
    )
def _mgmt_nl_raise_control_mode_error(exc: Exception) -> None:
    status_code = int(getattr(exc, "status_code", 422) or 422)
    if status_code == 403:
        code = ErrorCode.FORBIDDEN
    elif status_code == 409:
        code = ErrorCode.RESOURCE_CONFLICT
    elif status_code == 400:
        code = ErrorCode.VALIDATION_FAILED
    else:
        code = ErrorCode.BUSINESS_RULE_VIOLATION
    reason = str(getattr(exc, "reason", "") or getattr(exc, "field", "") or "control_mode_error")
    raise _bff_error(
        status_code,
        code,
        str(exc),
        reason,
        precondition_failed=f"control_mode_{reason}",
        details_extra={"field": getattr(exc, "field", None)},
    )
def _mgmt_nl_control_provider_status(command_kind: str) -> Dict[str, Any]:
    status = _mgmt_nl_provider_status(
        provider="pantheon_bff",
        enabled=True,
        status="completed",
        reason=f"control_mode_{command_kind}",
        used=True,
    )
    status["runtime"] = "management_nl_control_command_interceptor"
    status["fallback"] = None
    return status
def _mgmt_nl_control_answer(command_kind: str, control_mode: Dict[str, Any]) -> str:
    if command_kind == "activate" and control_mode.get("active"):
        return (
            "Control mode activated for this Management AI session. "
            "It will expire automatically at the configured TTL or idle timeout."
        )
    if command_kind == "deactivate":
        return "Control mode deactivated. This Management AI session is back in user mode."
    if control_mode.get("active"):
        return "Control mode is active for this Management AI session."
    return "Control mode is inactive for this Management AI session."
def _mgmt_nl_record_control_audit(
    *,
    identity: OperatorIdentity,
    command_kind: str,
    session_id: str,
    message_id: str,
    trace_id: str,
    focus: str,
    tenant_id: str,
    now: Any,
) -> Dict[str, Any]:
    audit_ref = {
        "target_type": "ManagementNLExchange",
        "target_id": message_id,
        "href": f"/bff/audit/entities/ManagementNLExchange/{message_id}",
    }
    try:
        accepted_audit = _record_agora_audit_event(
            {
                "action": f"management.nl.control_mode.{command_kind}",
                "targetType": "ManagementNLExchange",
                "targetId": message_id,
                "actorId": identity.operator_id,
                "recordedAt": now,
                "sessionId": session_id,
                "focus": focus,
                "tenantId": tenant_id,
                "traceId": trace_id,
                "question": _MGMT_NL_CONTROL_REDACTED_QUESTION,
            }
        )
    except Exception:
        log.warning("Failed to record management NL control-mode audit event", exc_info=True)
        raise _bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Management NL control-mode audit write failed",
            "control_mode_audit_write_failed",
            precondition_failed="audit_write",
            suggestion="Retry after the Agora audit store is available",
        )
    audit_ref["audit_id"] = accepted_audit.get("auditId") or accepted_audit.get("eventId")
    return audit_ref
def _management_nl_publish_completed_events(
    *,
    session_id: str,
    message_id: str,
    assistant_turn_id: str,
    trace_id: str,
    focus: str,
    provider_status: Dict[str, Any],
    action_count: int,
    audit_log_href: str,
    conversation_href: str,
    control_command: Optional[str] = None,
) -> None:
    provider_state = str(provider_status.get("status") or "unknown")
    completed_event: Dict[str, Any] = {
        "session_id": session_id,
        "message_id": message_id,
        "assistant_turn_id": assistant_turn_id,
        "trace_id": trace_id,
        "focus": focus,
        "status": "completed",
        "lifecycle_status": "completed",
        "provider_status": provider_status,
        "provider_status_state": provider_state,
        "action_count": action_count,
    }
    if control_command is not None:
        completed_event["control_command"] = control_command
    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "ask.message.completed",
        completed_event,
    )
    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "management.nl.ask.completed",
        {
            **completed_event,
            "audit_log": {"href": audit_log_href, "trace_id": trace_id},
            "conversation": {
                "href": conversation_href,
                "session_id": session_id,
                "trace_id": trace_id,
            },
        },
    )
def _mgmt_nl_handle_control_command(
    *,
    control_command: Dict[str, Any],
    payload: Dict[str, Any],
    identity: OperatorIdentity,
    caller_tenant_id: str,
    focus: str,
    ui_snapshot: Dict[str, Any],
    resolved_key: str,
    idempotency_storage_key: str,
    request_hash: str,
    session_id: str,
    message_id: str,
    trace_id: str,
    now: Any,
) -> JSONResponse:
    from .assistant.control_mode import ControlModeError, actor_capabilities, default_idle_ttl
    from .assistant.mode_policy import DEFAULT_KERNEL_TTL_SECONDS, ModePolicyViolation, assert_kernel_allowed
    from .assistant.models import AssistantMode

    store = _mgmt_nl_control_store()
    if store is None:
        raise _bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Control mode store is unavailable",
            "control_mode_store_unavailable",
            precondition_failed="control_mode_store",
        )

    command_kind = str(control_command.get("kind") or "").strip()
    if command_kind == "activate":
        _mgmt_nl_raise_control_mode_actor_error(identity)
        options = _mgmt_nl_control_options(payload)
        mode_raw = str(options.get("mode") or AssistantMode.KERNEL_DEBUG.value)
        try:
            mode = AssistantMode(mode_raw)
        except ValueError:
            raise _bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                f"Invalid mode: {mode_raw!r}",
                "invalid_control_mode",
                precondition_failed="control_mode_mode",
                details_extra={"field": "mode"},
            )
        try:
            assert_kernel_allowed(mode)
        except ModePolicyViolation as exc:
            raise _bff_error(
                403,
                ErrorCode.FORBIDDEN,
                f"Mode policy violation: {exc}",
                str(exc),
                precondition_failed="control_mode_kernel_policy",
                details_extra={"field": exc.field},
            )
        _mgmt_nl_require_mode_capability(identity, mode)
        ttl_seconds = _mgmt_nl_positive_int(
            options.get("ttlSeconds", options.get("ttl_seconds")),
            DEFAULT_KERNEL_TTL_SECONDS,
        )
        idle_ttl_seconds = _mgmt_nl_positive_int(
            options.get("idleTtlSeconds", options.get("idle_ttl_seconds")),
            default_idle_ttl(ttl_seconds),
        )
        try:
            control_mode = store.activate(
                actor_id=identity.operator_id,
                mode=mode,
                capabilities=actor_capabilities(identity),
                reason=str(options.get("reason") or "management_nl_chat_control_command").strip(),
                passphrase=str(control_command.get("passphrase") or ""),
                ttl_seconds=ttl_seconds,
                idle_ttl_seconds=idle_ttl_seconds,
                management_session_id=session_id,
            )
        except ControlModeError as exc:
            _mgmt_nl_raise_control_mode_error(exc)
    elif command_kind == "deactivate":
        _mgmt_nl_raise_control_mode_actor_error(identity)
        control_mode = store.deactivate(identity.operator_id, reason="management_nl_chat_control_command")
    elif command_kind == "status":
        control_mode = _assistant_control_mode_for_identity(
            identity,
            management_session_id=session_id,
            touch=False,
        )
    else:
        raise _bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "Unsupported control-mode command",
            "unsupported_control_mode_command",
            precondition_failed="control_mode_command",
        )

    provider_status = _mgmt_nl_control_provider_status(command_kind)
    answer = _mgmt_nl_control_answer(command_kind, control_mode)
    assistant_turn_id = f"{message_id}-assistant"
    audit_log_href = _management_ai_audit_href(session_id=session_id, trace_id=trace_id)
    conversation_href = _management_ai_conversation_href(session_id)
    audit_ref = _mgmt_nl_record_control_audit(
        identity=identity,
        command_kind=command_kind,
        session_id=session_id,
        message_id=message_id,
        trace_id=trace_id,
        focus=focus,
        tenant_id=caller_tenant_id,
        now=now,
    )
    _management_ai_ensure_session(
        session_id=session_id,
        identity=identity,
        tenant_id=caller_tenant_id,
        now=now,
        title=_MGMT_NL_CONTROL_REDACTED_QUESTION,
    )
    _management_ai_append_turn(
        turn_id=message_id,
        session_id=session_id,
        role="user",
        text=_MGMT_NL_CONTROL_REDACTED_QUESTION,
        created_at=now,
        trace_id=trace_id,
        attachments=[],
        ui_snapshot=ui_snapshot,
    )

    redaction = {
        "question": "redacted",
        "passphrase": "not_persisted",
        "provider": "not_invoked",
    }
    _management_ai_record_event(
        {
            "event_type": "management_ai.exchange.accepted",
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "actor_id": identity.operator_id,
            "route": "POST /bff/management/nl/ask",
            "question": _MGMT_NL_CONTROL_REDACTED_QUESTION,
            "focus": focus,
            "tenant_id": caller_tenant_id,
            "confidence": "high",
            "source_keys": [],
            "control_command": command_kind,
            "control_command_source": control_command.get("source"),
            "redaction": redaction,
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "control_mode": {
                "state": control_mode.get("state"),
                "active": control_mode.get("active"),
                "mode": control_mode.get("mode"),
                "activation_id": control_mode.get("activation_id") or control_mode.get("activationId"),
            },
            "audit_ref": audit_ref,
        }
    )

    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "management.nl.ask.accepted",
        {
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "focus": focus,
            "control_command": command_kind,
        },
    )

    exchange_status = "completed"
    result = {
        "status": "accepted",
        "data": {
            "status": exchange_status,
            "lifecycle_status": exchange_status,
            "answer": answer,
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "question": _MGMT_NL_CONTROL_REDACTED_QUESTION,
            "focus": focus,
            "sources": [],
            "confidence": "high",
            "summary_context": {},
            "context_pack": None,
            "provider_status": provider_status,
            "control_mode": control_mode,
            "control_command": command_kind,
            "ui_actions": [],
            "actions": [],
            "audit_ref": audit_ref,
            "audit_log": {
                "href": audit_log_href,
                "trace_id": trace_id,
            },
            "conversation": {
                "href": conversation_href,
                "session_id": session_id,
                "trace_id": trace_id,
            },
            "session": {
                "session_id": session_id,
                "ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            },
            "evidence_refs": [],
            "redaction": redaction,
        },
        "meta": {
            "status": exchange_status,
            "lifecycle_status": exchange_status,
            "snapshot_at": now,
            "surfaces": {"management_nl_control_command": {"status": "ok", "source": "bff_interceptor"}},
            "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
            "provider_status": provider_status,
            "trace_id": trace_id,
            "context_pack_id": None,
            "redacted_evidence_count": 0,
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "control_mode": control_mode,
            "control_command": command_kind,
            "redaction": redaction,
        },
    }
    _management_ai_record_event(
        {
            "event_type": "management_ai.exchange.completed",
            "session_id": session_id,
            "message_id": message_id,
            "assistant_turn_id": assistant_turn_id,
            "trace_id": trace_id,
            "actor_id": identity.operator_id,
            "route": "POST /bff/management/nl/ask",
            "answer": _management_ai_summary_value(answer),
            "provider_status": provider_status,
            "actions": [],
            "action_count": 0,
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "control_command": command_kind,
            "redaction": redaction,
            "control_mode": {
                "state": control_mode.get("state"),
                "active": control_mode.get("active"),
                "mode": control_mode.get("mode"),
                "activation_id": control_mode.get("activation_id") or control_mode.get("activationId"),
            },
            "fallback": provider_status.get("fallback"),
        }
    )
    _management_ai_append_turn(
        turn_id=assistant_turn_id,
        session_id=session_id,
        role="assistant",
        text=answer,
        created_at=utc_now(),
        trace_id=trace_id,
        provider_status=provider_status,
        ui_actions=[],
    )
    _management_nl_publish_completed_events(
        session_id=session_id,
        message_id=message_id,
        assistant_turn_id=assistant_turn_id,
        trace_id=trace_id,
        focus=focus,
        provider_status=provider_status,
        action_count=0,
        audit_log_href=audit_log_href,
        conversation_href=conversation_href,
        control_command=command_kind,
    )
    _mgmt_nl_idempotency_put(idempotency_storage_key, request_hash=request_hash, result=result)
    return JSONResponse(status_code=202, content=result)
def _mgmt_nl_normalize_question_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())
def _mgmt_nl_evasion_stripped_variants(question: str) -> List[str]:
    raw = _mgmt_nl_normalize_question_text(question)
    variants = [raw]
    evasion_prefixes = (
        "can you please ", "can you ", "please ", "help me ", "i need you to ",
        "i want you to ", "could you please ", "could you ", "would you please ",
        "would you ", "should i ", "let's ", "let me ", "go ahead and ",
        "kindly ", "請幫我", "請", "麻煩", "幫我",
    )
    for prefix in evasion_prefixes:
        if raw.startswith(prefix):
            stripped = raw[len(prefix):].strip()
            if stripped and stripped not in variants:
                variants.append(stripped)
            break
    return variants
def _mgmt_nl_term_matches(text: str, term: str) -> bool:
    clean_term = _mgmt_nl_normalize_question_text(term)
    if not clean_term:
        return False
    if re.fullmatch(r"[a-z0-9_ -]+", clean_term):
        term_pattern = r"[\s_-]+".join(re.escape(part) for part in clean_term.split())
        return bool(re.search(rf"(?<![a-z0-9_]){term_pattern}(?![a-z0-9_])", text))
    return clean_term in text
def _mgmt_nl_high_risk_classify(question: str) -> Optional[Dict[str, Any]]:
    """Classify NL questions requesting high-risk mutations before any read work."""
    variants = _mgmt_nl_evasion_stripped_variants(question)
    for category_key, trigger_terms, safe_alternatives in _MGMT_NL_HIGH_RISK_PATTERNS:
        for term in trigger_terms:
            if any(_mgmt_nl_term_matches(variant, term) for variant in variants):
                return {
                    "matched_category": category_key,
                    "matched_pattern": term,
                    "safe_alternatives": safe_alternatives,
                }
    return None
def _mgmt_nl_record_high_risk_refusal(
    *,
    identity: OperatorIdentity,
    question: str,
    risk: Dict[str, Any],
    recorded_at: str,
) -> Optional[str]:
    """Record a narrow refusal audit event without creating NL session state."""
    try:
        audit = _record_agora_audit_event(
            {
                "action": "management.nl.high_risk_refused",
                "targetType": "ManagementNLQuery",
                "targetId": f"mgmt-nl-refusal-{uuid.uuid4().hex[:12]}",
                "actorId": identity.operator_id,
                "recordedAt": recorded_at,
                "reason": "high_risk_nl_policy",
                "matchedCategory": risk.get("matched_category"),
                "matchedPattern": risk.get("matched_pattern"),
                "questionExcerpt": question[:200],
                "followups": _MGMT_NL_HIGH_RISK_REFUSAL_FOLLOWUPS,
            }
        )
        return str(audit.get("auditId") or audit.get("eventId") or "").strip() or None
    except Exception:
        log.warning("Failed to record management NL high-risk refusal audit", exc_info=True)
        return None
def _mgmt_nl_idempotency_storage_key(
    resolved_key: str,
    *,
    actor_id: str,
    tenant_id: str,
) -> str:
    material = "\x00".join(
        [
            "management-nl-v2",
            str(actor_id or "").strip(),
            str(tenant_id or "").strip(),
            str(resolved_key or "").strip(),
        ]
    )
    return f"management-nl-v2:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"
def _mgmt_nl_idempotency_check(
    storage_key: str,
    request_hash: str,
    *,
    display_key: str,
) -> Optional[Dict[str, Any]]:
    existing = _management_ai_conversation_store().get_idempotency(storage_key)
    if existing is None:
        existing = _MGMT_NL_IDEMPOTENCY.get(storage_key)
    if existing is None:
        return None
    if existing.get("request_hash") != request_hash:
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {display_key!r} is bound to a different management NL request hash",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        )
    return existing.get("result")
def _mgmt_nl_idempotency_put(
    storage_key: str,
    *,
    request_hash: str,
    result: Dict[str, Any],
) -> None:
    _management_ai_conversation_store().put_idempotency(
        storage_key,
        request_hash=request_hash,
        result=result,
    )
    _MGMT_NL_IDEMPOTENCY[storage_key] = {"request_hash": request_hash, "result": result}
def _mgmt_nl_command_idempotency_required() -> bool:
    return _bool_from_env("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_REQUIRED")
def _mgmt_nl_command_recovery_seconds() -> float:
    raw = os.getenv(
        "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_RECOVERY_SECONDS",
        "300",
    ).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 300.0
    return max(value, 0.001)
def _mgmt_nl_command_idempotency_store() -> ManagementNlCommandIdempotencyStore:
    global _MGMT_NL_COMMAND_IDEMPOTENCY_STORE, _MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG
    storage_path = os.getenv(
        "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH",
        DEFAULT_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_PATH,
    ).strip()
    config = (storage_path, _mgmt_nl_command_recovery_seconds())
    if _MGMT_NL_COMMAND_IDEMPOTENCY_STORE is None or _MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG != config:
        _MGMT_NL_COMMAND_IDEMPOTENCY_STORE = ManagementNlCommandIdempotencyStore(
            storage_path,
            recovery_seconds=config[1],
        )
        _MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG = config
    return _MGMT_NL_COMMAND_IDEMPOTENCY_STORE
def _mgmt_nl_command_scope(
    *,
    actor_id: str,
    tenant_id: str,
    resolved_key: str,
) -> ManagementNlCommandScope:
    return ManagementNlCommandScope(
        actor_id=actor_id,
        tenant_id=tenant_id,
        route="POST /bff/management/nl/ask",
        idempotency_key=resolved_key,
    )
def _mgmt_nl_result_is_terminal(result: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(result, Mapping):
        return False
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), Mapping) else {}
    states = {
        str(value or "").strip().lower()
        for value in (
            data.get("lifecycle_status"),
            data.get("lifecycleStatus"),
            data.get("status"),
            meta.get("lifecycle_status"),
            meta.get("lifecycleStatus"),
            meta.get("status"),
        )
        if str(value or "").strip()
    }
    return not states.intersection({"accepted", "processing", "pending", "queued", "in_progress"})
def _mgmt_nl_raise_command_idempotency_error(exc: Exception, *, display_key: str) -> None:
    if isinstance(exc, ManagementNlCommandPayloadConflict):
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {display_key!r} is bound to a different Management NL command",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        ) from exc
    if isinstance(exc, ManagementNlCommandRecoveryRequired):
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Management NL command outcome is uncertain",
            "The command will not be executed again until its prior outcome is reconciled.",
            precondition_failed="idempotency_recovery_required",
            suggestion="Inspect the durable conversation/provider audit and reconcile this key explicitly",
        ) from exc
    raise _bff_error(
        503,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        "Management NL command admission store is unavailable",
        str(exc),
        precondition_failed="management_nl_command_idempotency_store",
        suggestion="Restore the durable command idempotency volume before retrying",
    ) from exc
def _mgmt_nl_command_wait_seconds() -> float:
    raw = os.getenv("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_WAIT_SECONDS", "").strip()
    if raw:
        try:
            return max(float(raw), 0.01)
        except (TypeError, ValueError):
            pass
    provider_raw = os.getenv("PANTHEON_ASSISTANT_PROVIDER_TIMEOUT_SECONDS", "180").strip()
    try:
        provider_seconds = max(float(provider_raw), 0.1)
    except (TypeError, ValueError):
        provider_seconds = 180.0
    return provider_seconds + 10.0
def _mgmt_nl_command_poll_seconds() -> float:
    raw = os.getenv(
        "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_POLL_SECONDS",
        "0.05",
    ).strip()
    try:
        return min(max(float(raw), 0.005), 1.0)
    except (TypeError, ValueError):
        return 0.05
async def _mgmt_nl_command_admit(
    *,
    scope: ManagementNlCommandScope,
    request_hash: str,
    legacy_result: Optional[Dict[str, Any]],
    display_key: str,
) -> tuple[Optional[ManagementNlCommandReservation], Optional[Dict[str, Any]]]:
    if not _mgmt_nl_command_idempotency_required():
        return None, legacy_result

    store = _mgmt_nl_command_idempotency_store()
    try:
        admission = await asyncio.to_thread(
            store.admit,
            scope,
            request_hash=request_hash,
            legacy_result=legacy_result,
            legacy_terminal=_mgmt_nl_result_is_terminal(legacy_result),
        )
    except (
        ManagementNlCommandPayloadConflict,
        ManagementNlCommandRecoveryRequired,
        ManagementNlCommandStorageError,
    ) as exc:
        _mgmt_nl_raise_command_idempotency_error(exc, display_key=display_key)

    if admission.state == "owner":
        return admission.reservation, None
    if admission.state == "complete":
        return None, admission.result
    if admission.state != "wait":
        _mgmt_nl_raise_command_idempotency_error(
            ManagementNlCommandStorageError(
                f"Unsupported Management NL command admission state: {admission.state}"
            ),
            display_key=display_key,
        )

    deadline = asyncio.get_running_loop().time() + _mgmt_nl_command_wait_seconds()
    while True:
        if asyncio.get_running_loop().time() >= deadline:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Management NL command is still in progress",
                "An exact concurrent request owns this idempotency key and has not reached a terminal result.",
                precondition_failed="idempotency_in_progress",
                suggestion="Retry the same payload and key after the current provider turn completes",
            )
        await asyncio.sleep(_mgmt_nl_command_poll_seconds())
        try:
            admission = await asyncio.to_thread(
                store.observe,
                scope,
                request_hash=request_hash,
            )
        except (
            ManagementNlCommandPayloadConflict,
            ManagementNlCommandRecoveryRequired,
            ManagementNlCommandStorageError,
        ) as exc:
            _mgmt_nl_raise_command_idempotency_error(exc, display_key=display_key)
        if admission.state == "complete":
            return None, admission.result
        if admission.state != "wait":
            _mgmt_nl_raise_command_idempotency_error(
                ManagementNlCommandStorageError(
                    f"Unsupported Management NL command observation state: {admission.state}"
                ),
                display_key=display_key,
            )
async def _mgmt_nl_command_complete(
    reservation: Optional[ManagementNlCommandReservation],
    result: Dict[str, Any],
    *,
    display_key: str,
) -> None:
    if reservation is None:
        return
    try:
        await asyncio.to_thread(
            _mgmt_nl_command_idempotency_store().complete,
            reservation,
            result,
        )
    except (
        ManagementNlCommandPayloadConflict,
        ManagementNlCommandRecoveryRequired,
        ManagementNlCommandStorageError,
    ) as exc:
        _mgmt_nl_raise_command_idempotency_error(exc, display_key=display_key)
async def _mgmt_nl_command_mark_uncertain(
    reservation: Optional[ManagementNlCommandReservation],
    *,
    reason: str,
) -> None:
    if reservation is None:
        return
    try:
        await asyncio.to_thread(
            _mgmt_nl_command_idempotency_store().mark_uncertain,
            reservation,
            reason=reason,
        )
    except Exception:
        log.exception("Failed to mark Management NL command reservation uncertain")
def _mgmt_nl_surface_confidence(surfaces: Dict[str, Any]) -> str:
    statuses = [v.get("status", "unavailable") for v in surfaces.values() if isinstance(v, dict)]
    if not statuses:
        return "unavailable"
    if all(s == "ok" for s in statuses):
        return "high"
    if all(s == "unavailable" for s in statuses):
        return "unavailable"
    return "partial"
def _mgmt_nl_caller_tenant(
    identity: OperatorIdentity,
    *,
    requested_tenant: Optional[str] = None,
) -> str:
    tenant = _bff_me_tenant_payload(identity, requested_tenant=requested_tenant)
    return str(tenant.get("id") or "pantheon-dev")
def _mgmt_nl_scope_values(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[\s,]+", value) if part.strip()]
    if isinstance(value, dict):
        values: List[str] = []
        for key in ("id", "tenant_id", "tenantId", "value", "name"):
            if value.get(key) not in (None, ""):
                values.extend(_mgmt_nl_scope_values(value.get(key)))
        return values
    if isinstance(value, (list, tuple, set)):
        values: List[str] = []
        for item in value:
            values.extend(_mgmt_nl_scope_values(item))
        return values
    return [str(value).strip()]
def _mgmt_nl_record_tenant_ids(record: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    direct_keys = (
        "tenant_id",
        "tenantId",
        "tenant",
        "tenant_ref",
        "tenantRef",
        "org_id",
        "orgId",
        "organization_id",
        "organizationId",
        "workspace_id",
        "workspaceId",
    )
    for key in direct_keys:
        if key in record:
            values.extend(_mgmt_nl_scope_values(record.get(key)))
    for key in ("metadata", "scope", "sourceRecord", "source_record", "source_document", "target_ref"):
        nested = record.get(key)
        if isinstance(nested, dict):
            values.extend(_mgmt_nl_record_tenant_ids(nested))
    seen = set()
    result: List[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result
def _mgmt_nl_record_matches_tenant(record: Dict[str, Any], tenant_id: Optional[str]) -> bool:
    clean_tenant = str(tenant_id or "").strip()
    if not clean_tenant:
        return True
    record_tenants = _mgmt_nl_record_tenant_ids(record)
    if not record_tenants:
        return True
    return "*" in record_tenants or clean_tenant in record_tenants
def _mgmt_nl_filter_tenant_records(
    records: List[Dict[str, Any]],
    tenant_id: Optional[str],
) -> List[Dict[str, Any]]:
    return [
        record
        for record in records
        if isinstance(record, dict) and _mgmt_nl_record_matches_tenant(record, tenant_id)
    ]
def _mgmt_nl_add_entity(
    entities: Set[Tuple[str, str]],
    entity_type: str,
    entity_ref: Any,
) -> None:
    clean_type = str(entity_type or "").strip().lower()
    clean_ref = str(entity_ref or "").strip()
    if clean_type and clean_ref:
        entities.add((clean_type, clean_ref))
def _mgmt_nl_add_record_entities(
    entities: Set[Tuple[str, str]],
    records: List[Dict[str, Any]],
    entity_type: str,
    *keys: str,
) -> None:
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                _mgmt_nl_add_entity(entities, entity_type, value)
def _mgmt_nl_scoped_runtime_rows(
    runtime_bindings: List[Dict[str, Any]],
    entities: Set[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for binding in runtime_bindings:
        runtime_id = str(binding.get("runtime_id") or binding.get("id") or binding.get("binding_id") or "").strip()
        binding_id = str(binding.get("binding_id") or binding.get("runtime_binding_id") or binding.get("id") or "").strip()
        _mgmt_nl_add_entity(entities, "runtime", runtime_id)
        _mgmt_nl_add_entity(entities, "runtime_binding", binding_id)
        if binding.get("capital_pool_id"):
            _mgmt_nl_add_entity(entities, "capital_pool", binding.get("capital_pool_id"))
        rows.append(_project_operator_runtime_state_row(binding))
    return rows
def _mgmt_nl_trading_pulse_snippet(
    runtime_bindings: List[Dict[str, Any]],
    entities: Set[Tuple[str, str]],
) -> Dict[str, Any]:
    runtime_rows = _mgmt_nl_scoped_runtime_rows(runtime_bindings, entities)
    telemetry_rows = [
        row.get("telemetry_summary")
        for row in runtime_rows
        if isinstance(row.get("telemetry_summary"), dict)
    ]
    pnl_values = [
        value
        for value in (_management_number((row.get("metrics") or {}).get("pnl")) for row in telemetry_rows)
        if value is not None
    ]
    fill_rate_values = [
        value
        for value in (_management_number((row.get("metrics") or {}).get("fill_rate")) for row in telemetry_rows)
        if value is not None
    ]
    trade_values = [
        value
        for value in (_management_number((row.get("metrics") or {}).get("total_trades")) for row in telemetry_rows)
        if value is not None
    ]
    summary = {
        "runtimeCount": len(runtime_rows),
        "runtime_count": len(runtime_rows),
        "telemetryCoverageCount": len(telemetry_rows),
        "telemetry_coverage_count": len(telemetry_rows),
        "byStatus": _management_count_by(runtime_rows, "status"),
        "by_status": _management_count_by(runtime_rows, "status"),
        "byStage": _management_count_by(runtime_rows, "deployment_stage"),
        "by_stage": _management_count_by(runtime_rows, "deployment_stage"),
        "totalPnl": round(sum(pnl_values), 6) if pnl_values else None,
        "total_pnl": round(sum(pnl_values), 6) if pnl_values else None,
        "averageFillRate": _management_avg(fill_rate_values),
        "average_fill_rate": _management_avg(fill_rate_values),
        "totalTrades": int(sum(trade_values)) if trade_values else 0,
        "total_trades": int(sum(trade_values)) if trade_values else 0,
    }
    cards = [
        {"cardId": "runtime-status", "card_id": "runtime-status", "label": "Runtime Status", "value": len(runtime_rows)},
        {"cardId": "pnl", "card_id": "pnl", "label": "P&L", "value": summary["totalPnl"]},
        {"cardId": "execution-quality", "card_id": "execution-quality", "label": "Execution Quality", "value": summary["averageFillRate"]},
    ]
    return {"summary": summary, "cards": cards}
def _mgmt_nl_collect_context(focus: str, snapshot_at: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
    """Collect management summary context for the requested focus surface(s).

    BFF-B6-001-SEC-FIX: accepts optional tenant_id to scope retrieved data.
    """
    use_all = focus in ("all", "")
    snippets: Dict[str, Any] = {}
    surfaces: Dict[str, Any] = {}
    evidence_entities: Set[Tuple[str, str]] = set()
    evidence_source_types: Set[str] = set()

    if use_all or focus == "cockpit":
        try:
            alerts_payload = _build_operator_alerts_payload(snapshot_at)
            alerts = _mgmt_nl_filter_tenant_records(
                list(alerts_payload.get("alerts") or []),
                tenant_id,
            )
            human_inbox_payload = _human_inbox_payload(snapshot_at, page_size=None)
            inbox_items = _mgmt_nl_filter_tenant_records(
                list(human_inbox_payload.get("items") or []),
                tenant_id,
            )
            anomalies_payload = _build_management_anomalies_payload(snapshot_at)
            anomalies = _mgmt_nl_filter_tenant_records(
                list(anomalies_payload.get("items") or []),
                tenant_id,
            )
            runtime_bindings = _mgmt_nl_filter_tenant_records(
                list(read_store.list_runtime_bindings() or []),
                tenant_id,
            )
            trading_pulse = _mgmt_nl_trading_pulse_snippet(runtime_bindings, evidence_entities)
            _mgmt_nl_add_record_entities(evidence_entities, alerts, "alert", "alert_id", "id")
            _mgmt_nl_add_record_entities(evidence_entities, inbox_items, "human_inbox", "id", "item_id")
            _mgmt_nl_add_record_entities(evidence_entities, anomalies, "incident", "id")
            evidence_source_types.update({
                "alert",
                "incident",
                "approval",
                "human_inbox",
                "runtime",
                "runtime_binding",
                "telemetry",
            })
            snippets["cockpit"] = {
                "trading_pulse_summary": trading_pulse.get("summary"),
                "alerts_summary": _build_alert_summary(alerts),
                "human_inbox_summary": {"total": len(inbox_items)},
                "anomalies_summary": {"total": len(anomalies)},
            }
            surfaces["management_cockpit"] = {"status": "ok", "source": "bff_composed"}
        except Exception:
            surfaces["management_cockpit"] = {"status": "unavailable", "source": "error"}

    if use_all or focus == "trading_pulse":
        try:
            runtime_bindings = _mgmt_nl_filter_tenant_records(
                list(read_store.list_runtime_bindings() or []),
                tenant_id,
            )
            pulse_data = _mgmt_nl_trading_pulse_snippet(runtime_bindings, evidence_entities)
            evidence_source_types.update({"runtime", "runtime_binding", "telemetry", "paper_live_drift"})
            snippets["trading_pulse"] = {
                "summary": pulse_data.get("summary"),
                "cards": pulse_data.get("cards"),
            }
            surfaces["management_trading_pulse"] = {
                "status": "ok" if runtime_bindings else "unavailable",
                "source": "bff_composed",
            }
        except Exception:
            surfaces["management_trading_pulse"] = {"status": "unavailable", "source": "error"}

    if use_all or focus == "portfolio":
        try:
            pools = _mgmt_nl_filter_tenant_records(list(read_store.list_capital_pools() or []), tenant_id)
            runtime_bindings = _mgmt_nl_filter_tenant_records(list(read_store.list_runtime_bindings() or []), tenant_id)
            _mgmt_nl_add_record_entities(evidence_entities, pools, "capital_pool", "pool_id", "id")
            _mgmt_nl_add_record_entities(evidence_entities, runtime_bindings, "runtime", "runtime_id", "id", "binding_id")
            evidence_source_types.update({"capital_pool", "runtime", "runtime_binding", "telemetry"})
            telemetry_values = [
                read_store.get_telemetry_summary(
                    str(r.get("runtime_id") or r.get("id") or r.get("binding_id") or "")
                )
                for r in runtime_bindings
                if r.get("runtime_id") or r.get("id") or r.get("binding_id")
            ]
            telemetry_values = [t for t in telemetry_values if t is not None]
            portfolio_rollup = _management_telemetry_rollup(telemetry_values)
            snippets["portfolio"] = {
                "capital_pool_count": len(pools),
                "runtime_count": len(runtime_bindings),
                "total_pnl": portfolio_rollup.get("total_pnl"),
                "max_drawdown": portfolio_rollup.get("max_drawdown"),
                "average_fill_rate": portfolio_rollup.get("average_fill_rate"),
                "total_trades": portfolio_rollup.get("total_trades"),
            }
            portfolio_status = "ok" if pools or runtime_bindings else "unavailable"
            surfaces["portfolio_book"] = {"status": portfolio_status, "source": "bff_composed"}
        except Exception:
            surfaces["portfolio_book"] = {"status": "unavailable", "source": "error"}

    if use_all or focus == "persona_fleet":
        try:
            personas = _mgmt_nl_filter_tenant_records(_list_persona_records(tenant_id), tenant_id)
            runtime_bindings = _mgmt_nl_filter_tenant_records(list(read_store.list_runtime_bindings() or []), tenant_id)
            incidents = _mgmt_nl_filter_tenant_records(list(read_store.list_incidents() or []), tenant_id)
            evolution_decisions = _mgmt_nl_filter_tenant_records(list(read_store.list_evolution_decisions() or []), tenant_id)
            fleet_items = [
                _project_persona_fleet_item(
                    persona,
                    all_runtime_bindings=runtime_bindings,
                    all_incidents=incidents,
                    all_evolution_decisions=evolution_decisions,
                )
                for persona in personas
            ][:20]
            _mgmt_nl_add_record_entities(evidence_entities, personas, "persona", "persona_id", "id")
            _mgmt_nl_add_record_entities(evidence_entities, runtime_bindings, "runtime", "runtime_id", "id", "binding_id")
            _mgmt_nl_add_record_entities(evidence_entities, incidents, "incident", "incident_id", "id")
            _mgmt_nl_add_record_entities(evidence_entities, evolution_decisions, "evolution_decision", "decision_id", "id")
            evidence_source_types.update({"persona", "runtime", "runtime_binding", "incident", "evolution_decision"})
            fleet_summary = {
                "total_personas": len(personas),
                "returned_personas": len(fleet_items),
                "critical_personas": len([item for item in fleet_items if item["health"]["status"] == "critical"]),
                "degraded_personas": len([item for item in fleet_items if item["health"]["status"] == "degraded"]),
                "healthy_personas": len([item for item in fleet_items if item["health"]["status"] == "healthy"]),
                "bound_personas": len([item for item in fleet_items if item["bindings"]]),
                "runtime_bound_personas": len([item for item in fleet_items if item["runtimeBindings"]]),
            }
            snippets["persona_fleet"] = {
                "total": len(fleet_items),
                "summary": fleet_summary,
                "items": fleet_items,
            }
            surfaces["persona_fleet"] = {"status": "ok" if personas else "unavailable", "source": "bff_composed"}
        except Exception:
            surfaces["persona_fleet"] = {"status": "unavailable", "source": "error"}

    return {
        "snippets": snippets,
        "surfaces": surfaces,
        "evidence_entities": evidence_entities,
        "evidence_source_types": evidence_source_types,
    }
def _mgmt_nl_synthesize_answer(question: str, snippets: Dict[str, Any], focus: str) -> str:
    """
    Compose a plain-text management answer grounded in the collected snippets.
    This is a structured synthesis layer — not an external LLM call.
    """
    parts: List[str] = []

    cockpit = snippets.get("cockpit") or {}
    pulse = snippets.get("trading_pulse") or {}
    portfolio = snippets.get("portfolio") or {}
    fleet = snippets.get("persona_fleet") or {}

    if cockpit:
        alerts = cockpit.get("alerts_summary") or {}
        inbox = cockpit.get("human_inbox_summary") or {}
        anomalies = cockpit.get("anomalies_summary") or {}
        pulse_summary = cockpit.get("trading_pulse_summary") or {}
        if alerts.get("total_active") is not None:
            parts.append(f"Active alerts: {alerts['total_active']}.")
        if inbox.get("total") is not None:
            parts.append(f"Human inbox items: {inbox['total']}.")
        if anomalies.get("total") is not None:
            parts.append(f"Anomalies: {anomalies['total']}.")
        if pulse_summary.get("runtimeCount") is not None:
            parts.append(f"Runtimes in cockpit: {pulse_summary['runtimeCount']}.")

    if pulse:
        pulse_s = pulse.get("summary") or {}
        if pulse_s.get("totalPnl") is not None:
            parts.append(f"Total PnL: {pulse_s['totalPnl']:.4f}.")
        if pulse_s.get("runtimeCount") is not None:
            parts.append(f"Runtime count (trading pulse): {pulse_s['runtimeCount']}.")
        if pulse_s.get("averageFillRate") is not None:
            parts.append(f"Average fill rate: {pulse_s['averageFillRate']:.2%}.")

    if portfolio:
        if portfolio.get("capital_pool_count") is not None:
            parts.append(f"Capital pools: {portfolio['capital_pool_count']}.")
        if portfolio.get("runtime_count") is not None:
            parts.append(f"Runtime bindings: {portfolio['runtime_count']}.")
        if portfolio.get("total_pnl") is not None:
            parts.append(f"Portfolio total PnL: {portfolio['total_pnl']:.4f}.")
        if portfolio.get("max_drawdown") is not None:
            parts.append(f"Max drawdown: {portfolio['max_drawdown']:.4f}.")
        if portfolio.get("total_trades") is not None:
            parts.append(f"Total trades: {int(portfolio['total_trades'])}.")

    if fleet:
        total = fleet.get("total")
        if total is not None:
            parts.append(f"Persona fleet size: {total}.")

    if not parts:
        return (
            f"Management data is currently unavailable for the requested focus ({focus}). "
            "Please retry when management surfaces are reachable."
        )

    intro = f"Management summary for question: '{question}'. "
    return intro + " ".join(parts)
def _mgmt_nl_provider_feature_enabled() -> bool:
    for env_name in (
        "PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED",
        "PANTHEON_MGMT_NL_ASSISTANT_PROVIDER_ENABLED",
    ):
        if os.getenv(env_name) is not None:
            return _bool_from_env(env_name)
    return _bool_from_env("PANTHEON_ASSISTANT_ENABLED")
def _mgmt_nl_provider_name() -> str:
    return (os.getenv("PANTHEON_ASSISTANT_PROVIDER", "openclaw").strip().lower() or "openclaw")
_MGMT_NL_PROVIDER_REASON_MESSAGES = {
    "CODEX_AUTH_UNAVAILABLE": (
        "Codex service-user session expired. Re-login the dedicated Pantheon "
        "assistant Codex account; Management AI is serving deterministic "
        "fallback until the provider is healthy."
    ),
    "CLAUDE_AUTH_UNAVAILABLE": (
        "Claude service-user session is unavailable. Re-login the dedicated "
        "Pantheon assistant Claude account; Management AI is serving "
        "deterministic fallback until the provider is healthy."
    ),
    "OPENCLAW_ADAPTER_UNREACHABLE": (
        "OpenClaw adapter is unreachable. Management AI is serving "
        "deterministic fallback until the adapter is healthy."
    ),
    "OPENCLAW_ADAPTER_REQUEST_FAILED": (
        "OpenClaw adapter request failed. Management AI is serving "
        "deterministic fallback until the adapter request path is healthy."
    ),
    "OPENCLAW_ADAPTER_HTTP_ERROR": (
        "OpenClaw adapter returned an error. Management AI is serving "
        "deterministic fallback until the provider path is healthy."
    ),
    "CLAUDE_BINARY_NOT_FOUND": (
        "Claude CLI binary is unavailable in the assistant runtime. Management "
        "AI is serving deterministic fallback until the runtime is repaired."
    ),
    "ASSISTANT_PROVIDER_NOT_SUPPORTED": (
        "Configured assistant provider is not supported. Management AI is "
        "serving deterministic fallback until the provider configuration is "
        "updated."
    ),
    "PROVIDER_EMPTY_ANSWER": (
        "Assistant provider returned no answer. Management AI is serving "
        "deterministic fallback for this request."
    ),
    "UNSUPPORTED_PROVIDER": (
        "Configured assistant provider is not supported. Management AI is "
        "serving deterministic fallback until the provider configuration is "
        "updated."
    ),
    "FEATURE_DISABLED": (
        "Management AI provider is disabled by configuration. The response is "
        "deterministic fallback."
    ),
    "PROVIDER_DISABLED": (
        "Management AI provider is disabled by configuration. The response is "
        "deterministic fallback."
    ),
}
_MGMT_NL_PROVIDER_REASON_ACTIONS = {
    "CODEX_AUTH_UNAVAILABLE": "reauth_codex_service_user",
    "CLAUDE_AUTH_UNAVAILABLE": "reauth_claude_service_user",
    "OPENCLAW_ADAPTER_UNREACHABLE": "restore_openclaw_adapter",
    "OPENCLAW_ADAPTER_REQUEST_FAILED": "inspect_openclaw_adapter_request_path",
    "OPENCLAW_ADAPTER_HTTP_ERROR": "inspect_openclaw_adapter_response",
    "CLAUDE_BINARY_NOT_FOUND": "install_claude_cli",
    "ASSISTANT_PROVIDER_NOT_SUPPORTED": "configure_supported_management_ai_provider",
    "UNSUPPORTED_PROVIDER": "configure_supported_management_ai_provider",
    "FEATURE_DISABLED": "enable_management_ai_provider",
    "PROVIDER_DISABLED": "enable_management_ai_provider",
    "PROVIDER_EMPTY_ANSWER": "inspect_management_ai_provider_output",
}
def _mgmt_nl_provider_reason_key(reason: Optional[str]) -> Optional[str]:
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        return None
    return clean_reason.upper()
def _mgmt_nl_provider_status_notice(
    *,
    provider: str,
    status: str,
    reason: Optional[str],
    used: bool,
) -> Dict[str, str]:
    clean_status = str(status or "").strip().lower()
    if used or clean_status in {"completed", "ok"}:
        return {}

    reason_key = _mgmt_nl_provider_reason_key(reason)
    provider_key = str(provider or "").strip().lower()
    message = (
        _MGMT_NL_PROVIDER_REASON_MESSAGES.get(reason_key or "")
        if reason_key
        else None
    )
    action = (
        _MGMT_NL_PROVIDER_REASON_ACTIONS.get(reason_key or "")
        if reason_key
        else None
    )
    if reason_key is None and clean_status == "degraded":
        message = (
            "Assistant provider is degraded. Management AI is serving "
            "deterministic fallback until the provider is healthy."
        )
    if message is None and reason_key and "AUTH" in reason_key and "UNAVAILABLE" in reason_key:
        provider_label = "Codex" if "codex" in provider_key else "assistant"
        message = (
            f"{provider_label} service-user session is unavailable. Re-login "
            "the dedicated Pantheon assistant account; Management AI is "
            "serving deterministic fallback until the provider is healthy."
        )
        action = action or (
            "reauth_codex_service_user"
            if "codex" in provider_key
            else "reauth_assistant_service_user"
        )
    if message is None:
        message = (
            "Assistant provider is not available. Management AI is serving "
            "deterministic fallback until the provider is healthy."
        )
    return {
        "severity": "warning" if clean_status in {"degraded", "disabled"} else "info",
        "display_message": message,
        "operator_action": action or "inspect_management_ai_provider_status",
    }
def _mgmt_nl_provider_status(
    *,
    provider: str,
    enabled: bool,
    status: str,
    reason: Optional[str] = None,
    run_id: Optional[str] = None,
    used: bool = False,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "enabled": enabled,
        "provider": provider,
        "runtime": "openclaw_gateway_cli_mount",
        "status": status,
        "used": used,
        "fallback": None if used else "deterministic_synthesis",
    }
    if reason:
        payload["reason"] = reason
        payload["reason_code"] = reason
    payload.update(
        _mgmt_nl_provider_status_notice(
            provider=provider,
            status=status,
            reason=reason,
            used=used,
        )
    )
    if run_id:
        payload["run_id"] = run_id
    return payload
def _mgmt_nl_provider_supports_multimodal(provider: str) -> bool:
    return str(provider or "").strip().lower() in {"codex", "codex_cli"}
def _mgmt_nl_multimodal_attachment_payload(
    attachments: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    image_parts: List[Dict[str, Any]] = []
    attachment_summaries: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    store = _management_ai_conversation_store()
    for attachment in attachments or []:
        if not isinstance(attachment, dict):
            continue
        attachment_id = str(
            attachment.get("id")
            or attachment.get("attachmentId")
            or attachment.get("attachment_id")
            or ""
        ).strip()
        mime_type = str(attachment.get("mimeType") or attachment.get("mime_type") or "").strip().lower()
        if not attachment_id or not mime_type.startswith("image/"):
            continue
        try:
            found = store.find_attachment(attachment_id)
            if found is None:
                raise FileNotFoundError(attachment_id)
            metadata, _turn = found
            content, resolved_mime_type, filename = store.read_attachment(attachment_id, metadata)
        except Exception as exc:  # noqa: BLE001 - provider should degrade, not fail the ask.
            errors.append(
                {
                    "attachmentId": attachment_id,
                    "attachment_id": attachment_id,
                    "reason": "attachment_unavailable",
                    "error": type(exc).__name__,
                }
            )
            continue
        clean_mime_type = str(resolved_mime_type or mime_type or "application/octet-stream").strip().lower()
        data_url = f"data:{clean_mime_type};base64,{base64.b64encode(content).decode('ascii')}"
        image_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": data_url},
                "attachmentId": attachment_id,
                "attachment_id": attachment_id,
                "mimeType": clean_mime_type,
                "mime_type": clean_mime_type,
                "filename": filename or attachment.get("filename") or attachment_id,
                "sizeBytes": len(content),
                "size_bytes": len(content),
                "source": "management_ai_attachment_store",
            }
        )
        attachment_summaries.append(
            {
                "attachmentId": attachment_id,
                "attachment_id": attachment_id,
                "kind": str(attachment.get("kind") or "image"),
                "mimeType": clean_mime_type,
                "mime_type": clean_mime_type,
                "filename": filename or attachment.get("filename") or attachment_id,
                "sizeBytes": len(content),
                "size_bytes": len(content),
                "source": "management_ai_attachment_store",
            }
        )
    return image_parts, attachment_summaries, errors
def _mgmt_nl_provider_multimodal_payload(
    *,
    prompt: str,
    attachments: Optional[List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    image_parts, attachment_summaries, errors = _mgmt_nl_multimodal_attachment_payload(attachments)
    if not image_parts and not errors:
        return None
    content = [{"type": "text", "text": prompt}, *image_parts]
    return {
        "messages": [{"role": "user", "content": content}],
        "attachments": image_parts,
        "summary": {
            "attempted": bool(attachments),
            "forwarded": bool(image_parts),
            "attachment_count": len(image_parts),
            "unavailable_attachment_count": len(errors),
            "attachments": attachment_summaries,
            "errors": errors,
        },
    }
def _mgmt_nl_multimodal_unsupported_error(exc: OpenClawOpsClientError) -> bool:
    code = str(getattr(exc, "error_code", "") or "").strip().lower()
    message = str(getattr(exc, "message", "") or str(exc)).strip().lower()
    unsupported_tokens = ("multimodal", "image", "vision", "attachment")
    return (
        "unsupported" in code
        and any(token in code for token in unsupported_tokens)
    ) or (
        "unsupported" in message
        and any(token in message for token in unsupported_tokens)
    )
def _mgmt_nl_context_status(confidence: str) -> str:
    if confidence == "high":
        return "ok"
    if confidence == "partial":
        return "degraded"
    return "unavailable"
def _mgmt_nl_evidence_entities_payload(entities: Any) -> List[Dict[str, str]]:
    return [
        {"entity_type": str(entity_type), "entity_ref": str(entity_ref)}
        for entity_type, entity_ref in sorted(list(entities or set()))
    ]
def _mgmt_nl_build_context_pack(
    *,
    session_id: str,
    question: str,
    focus: str,
    identity: OperatorIdentity,
    caller_tenant_id: str,
    snippets: Dict[str, Any],
    surfaces: Dict[str, Any],
    source_keys: List[str],
    confidence: str,
    evidence_entities: Any,
    evidence_source_types: Any,
    operator_context: str,
    conversation_context: Dict[str, Any],
    ui_snapshot: Dict[str, Any],
    control_mode: Dict[str, Any],
) -> Dict[str, Any]:
    from .assistant.context_composer import AssistantCollectedSource, compose_context_pack
    from .assistant.models import AssistantContextPackRequest, AssistantMode

    frontend_route = str(ui_snapshot.get("currentRoute") or "/management")
    selected_entity = _mgmt_nl_frontend_selected_entity(ui_snapshot, focus=focus)
    assistant_mode = AssistantMode.USER
    if isinstance(control_mode, dict) and control_mode.get("active"):
        try:
            assistant_mode = AssistantMode(str(control_mode.get("mode") or AssistantMode.KERNEL_DEBUG.value))
        except ValueError:
            assistant_mode = AssistantMode.KERNEL_DEBUG
    context_identity = _mgmt_nl_identity_with_control_mode(identity, control_mode)
    management_payload = {
        "question": question,
        "focus": focus,
        "tenant_id": caller_tenant_id,
        "sources": source_keys,
        "confidence": confidence,
        "conversation": conversation_context,
        "ui": ui_snapshot,
        "control_mode": control_mode,
        "operator_context": operator_context,
        "session": {
            "session_id": session_id,
            "ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
        },
        "summary_context": snippets,
        "surfaces": surfaces,
        "evidence_entities": _mgmt_nl_evidence_entities_payload(evidence_entities),
        "evidence_source_types": sorted(str(item) for item in (evidence_source_types or set())),
    }

    request = AssistantContextPackRequest(
        mode=assistant_mode,
        include=["ui", "management_nl", "persona_health"],
        question=question,
        route=frontend_route,
        frontend={
            "route": frontend_route,
            "selectedEntity": selected_entity,
            "contextRefs": [
                {"kind": "management_nl_session", "id": session_id},
                {"kind": "management_nl_focus", "id": focus},
            ],
        },
        focus={
            "entityType": selected_entity.get("entityType") or "management_nl_focus",
            "entityId": selected_entity.get("entityId") or focus,
            "label": selected_entity.get("label") or focus,
            "route": frontend_route,
        },
    )

    def collect_source(source_id: str, _request: Any, snapshot_at: str) -> Any:
        if source_id == "persona_health":
            persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
            scoped_personas = _mgmt_nl_filter_tenant_records(_list_persona_records(caller_tenant_id), caller_tenant_id)
            return AssistantCollectedSource(
                source_id="persona_health",
                href="/bff/v5/execution/persona-health",
                payload={
                    "items": [
                        {
                            "id": persona.get("persona_id") or persona.get("id"),
                            "persona_id": persona.get("persona_id") or persona.get("id"),
                            "name": persona.get("name") or persona.get("persona_id"),
                            "health": "healthy"
                            if persona.get("lifecycle_state") == "active"
                            else "degraded",
                            "lifecycle_state": persona.get("lifecycle_state"),
                        }
                        for persona in scoped_personas
                    ],
                    "meta": {
                        "snapshot_at": snapshot_at,
                        "surfaces": {"persona_health": persona_surface},
                    },
                },
                status=str(persona_surface.get("status") or "ok"),
                source_kind="bff",
            )
        if source_id != "management_nl":
            return _assistant_collect_source(source_id, _request, snapshot_at)
        return AssistantCollectedSource(
            source_id="management_nl",
            href="/bff/management/nl/ask",
            payload={
                "data": management_payload,
                "meta": {
                    "snapshot_at": snapshot_at,
                    "surfaces": {"management_nl": {"status": _mgmt_nl_context_status(confidence)}},
                },
            },
            status=_mgmt_nl_context_status(confidence),
            source_kind="bff",
        )

    pack = compose_context_pack(
        session_id=session_id,
        request=request,
        actor=context_identity,
        collect_source=collect_source,
    )
    return pack.model_dump(mode="json", by_alias=False)
def _mgmt_nl_provider_mode_from_context(context_pack: Dict[str, Any]) -> str:
    mode = str(context_pack.get("mode") or "user").strip()
    if mode in {"user", "kernel_observe", "kernel_debug"}:
        return mode
    return "user"
def _mgmt_nl_provider_control_metadata(context_pack: Dict[str, Any]) -> Dict[str, Any]:
    management_context = (
        ((context_pack.get("backend") or {}).get("management_nl") or {}).get("data") or {}
        if isinstance(context_pack, dict)
        else {}
    )
    control_mode = management_context.get("controlMode") or management_context.get("control_mode")
    if not isinstance(control_mode, dict):
        return {"active": False, "mode": "user"}
    return {
        "active": bool(control_mode.get("active")),
        "state": control_mode.get("state"),
        "mode": control_mode.get("mode") or "user",
        "activation_id": control_mode.get("activation_id") or control_mode.get("activationId"),
    }
def _mgmt_nl_reject_development_payload(
    payload: Dict[str, Any],
    *,
    identity: OperatorIdentity,
    caller_tenant_id: str,
    control_mode: Dict[str, Any],
) -> None:
    has_repair_payload = bool(payload.get("repair")) or any(
        isinstance(payload.get(key), dict)
        and bool(payload[key].get("repair") or payload[key].get("task"))
        for key in ("openclaw", "openClaw")
    )
    if has_repair_payload:
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_FAILED,
            "Development tooling is not a product BFF capability",
            "Use the local development-tooling worktree and task commands; product BFF does not prepare or authorize source writes.",
            precondition_failed="development_tooling",
        )
def _mgmt_nl_provider_mode_prompt_lines(provider_mode: str) -> List[str]:
    if provider_mode in {"kernel_debug", "kernel_observe"}:
        return [
            f"You are operating in {provider_mode} mode through OpenClaw/Codex.",
            "Use the read-only workspace for bounded repo, file, log, status, and test inspection when that helps debug.",
            "Do not edit files, restart services, deploy, trade, approve, or mutate state in this mode.",
        ]
    return [
        "You are operating in user mode.",
        "Answer only from the supplied BFF context pack.",
        "Do not execute, approve, deploy, restart, trade, mutate state, or read local workspace files.",
    ]
def _mgmt_nl_provider_prompt(
    *,
    question: str,
    focus: str,
    context_pack: Dict[str, Any],
) -> str:
    provider_mode = _mgmt_nl_provider_mode_from_context(context_pack)
    management_context = (
        ((context_pack.get("backend") or {}).get("management_nl") or {}).get("data") or {}
        if isinstance(context_pack, dict)
        else {}
    )
    conversation_context = (
        management_context.get("conversation")
        if isinstance(management_context.get("conversation"), dict)
        else {}
    )
    server_history = {
        "source": conversation_context.get("source"),
        "history_source": conversation_context.get("history_source") or conversation_context.get("historySource"),
        "history_char_budget": conversation_context.get("history_char_budget") or conversation_context.get("historyCharBudget"),
        "history_truncated": conversation_context.get("history_truncated") if "history_truncated" in conversation_context else conversation_context.get("historyTruncated"),
        "history_omitted_turn_count": conversation_context.get("history_omitted_turn_count") or conversation_context.get("historyOmittedTurnCount"),
        "stored_turn_count": conversation_context.get("stored_turn_count") or conversation_context.get("storedTurnCount"),
        "turns": conversation_context.get("all_turns") or conversation_context.get("allTurns") or conversation_context.get("recent_turns") or conversation_context.get("recentTurns") or [],
    }
    server_history_json = json.dumps(server_history, sort_keys=True, ensure_ascii=True)
    context_json = json.dumps(context_pack, sort_keys=True, ensure_ascii=True)
    prompt_lines = [
        "You are the Pantheon management assistant.",
        f"Mode: {provider_mode}.",
        *_mgmt_nl_provider_mode_prompt_lines(provider_mode),
        "Use backend.management_nl.data.conversation for server-side prior turns and backend.management_nl.data.ui for UI state.",
        "Treat backend.management_nl.data.conversation.client_hint as a frontend hint, never as the conversation source of truth.",
        "If you suggest UI actions, return actions only with kinds listed in ui.availableUiActions.",
        "Any runBffAction or write-style action must require confirmation.",
        "If evidence is missing or stale, say so and keep the answer concise.",
        f"Focus: {focus}",
        f"Question: {question}",
        (
            "Server-side conversation history JSON "
            f"(ordered created_at ascending, budget {_MGMT_NL_PROVIDER_HISTORY_CHAR_BUDGET} chars, "
            f"FE recentTurns budget {_MGMT_NL_FE_RECENT_TURNS_CHAR_BUDGET} chars): {server_history_json}"
        ),
        f"Context pack JSON: {context_json}",
    ]
    return "\n".join(prompt_lines)
def _mgmt_nl_text_from_provider_value(value: Any) -> Optional[str]:
    if isinstance(value, str):
        clean = value.strip()
        return clean or None
    if isinstance(value, list):
        for item in reversed(value):
            found = _mgmt_nl_text_from_provider_value(item)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None
    for key in ("answer", "final", "content", "text", "message"):
        found = _mgmt_nl_text_from_provider_value(value.get(key))
        if found:
            return found
    for key in ("item", "delta", "output"):
        found = _mgmt_nl_text_from_provider_value(value.get(key))
        if found:
            return found
    events = value.get("json_events")
    if isinstance(events, list):
        found = _mgmt_nl_text_from_provider_value(events)
        if found:
            return found
    stdout = value.get("stdout")
    if isinstance(stdout, str):
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                loaded = json.loads(line)
            except json.JSONDecodeError:
                return line
            found = _mgmt_nl_text_from_provider_value(loaded)
            if found:
                return found
    return None
def _mgmt_nl_extract_provider_answer(payload: Dict[str, Any]) -> Optional[str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        return _mgmt_nl_text_from_provider_value(data.get("output"))
    # Claude and other flat-format providers return the answer in a top-level
    # "text" field rather than nesting it under data.output.
    return _mgmt_nl_text_from_provider_value(payload)
_MGMT_NL_COMPLETED_PROVIDER_STATES = {"completed", "ok", "success", "succeeded"}
_MGMT_NL_PROVIDER_DEADLINE_DEFAULT_SECONDS = 45.0
def _mgmt_nl_provider_deadline_seconds() -> float:
    raw = os.getenv("PANTHEON_MANAGEMENT_NL_PROVIDER_DEADLINE_SECONDS")
    if raw is None or not str(raw).strip():
        return _MGMT_NL_PROVIDER_DEADLINE_DEFAULT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _MGMT_NL_PROVIDER_DEADLINE_DEFAULT_SECONDS
    return min(300.0, max(0.1, value))
def _mgmt_nl_provider_candidates(primary: str) -> List[str]:
    configured: List[str] = [str(primary or "").strip().lower()]
    for env_name in (
        "PANTHEON_MANAGEMENT_NL_ASSISTANT_FALLBACK_PROVIDERS",
        "PANTHEON_MGMT_NL_ASSISTANT_FALLBACK_PROVIDERS",
    ):
        raw = os.getenv(env_name, "")
        configured.extend(item.strip().lower() for item in raw.split(","))
    candidates: List[str] = []
    for provider in configured:
        if provider and provider not in candidates:
            candidates.append(provider)
    return candidates
def _mgmt_nl_provider_attempt_summary(status: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": status.get("provider"),
        "status": status.get("status"),
        "used": bool(status.get("used")),
        "reason": status.get("reason"),
        "run_id": status.get("run_id"),
    }
def _mgmt_nl_provider_degraded_reason(payload: Dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload, dict) else {}
    output = data.get("output") if isinstance(data, dict) else {}
    for source in (output, data, payload):
        if not isinstance(source, dict):
            continue
        for key in ("error_code", "reason", "degraded_reason", "diagnostic_reason"):
            value = str(source.get(key) or "").strip()
            if value:
                return value.upper()
    return "PROVIDER_RESPONSE_DEGRADED"
def _mgmt_nl_maybe_provider_answer(
    *,
    provider: str,
    question: str,
    focus: str,
    identity: OperatorIdentity,
    caller_tenant_id: str,
    session_id: str,
    message_id: str,
    trace_id: str,
    context_pack: Dict[str, Any],
    audit_id: Optional[str],
    allowed_action_kinds: Set[str],
    current_user_attachments: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[str], Dict[str, Any], List[Dict[str, Any]]]:
    candidates = _mgmt_nl_provider_candidates(provider)
    deadline_seconds = _mgmt_nl_provider_deadline_seconds()
    deadline = time.monotonic() + deadline_seconds
    attempts: List[Dict[str, Any]] = []
    primary_status: Optional[Dict[str, Any]] = None

    for attempt_index, candidate in enumerate(candidates):
        answer, status, actions = _mgmt_nl_attempt_provider_answer(
            provider=candidate,
            question=question,
            focus=focus,
            identity=identity,
            caller_tenant_id=caller_tenant_id,
            session_id=session_id,
            message_id=message_id,
            trace_id=trace_id,
            context_pack=context_pack,
            audit_id=audit_id,
            allowed_action_kinds=allowed_action_kinds,
            current_user_attachments=current_user_attachments,
            provider_deadline=deadline,
            provider_attempt=attempt_index,
        )
        attempt_summary = _mgmt_nl_provider_attempt_summary(status)
        attempts.append(attempt_summary)
        if primary_status is None:
            primary_status = status
        if answer and status.get("used") is True:
            status["attempted_providers"] = attempts
            status["deadline_seconds"] = deadline_seconds
            if attempt_index:
                status["fallback"] = "provider_failover"
                status["fallback_from"] = str(provider or "").strip().lower()
                status["fallback_reason"] = primary_status.get("reason") if primary_status else None
            return answer, status, actions
        if str(status.get("status") or "").lower() == "disabled":
            break
        if time.monotonic() >= deadline:
            break

    terminal_status = dict(primary_status or _mgmt_nl_provider_status(
        provider=provider,
        enabled=True,
        status="degraded",
        reason="provider_deadline_exhausted",
    ))
    terminal_status["attempted_providers"] = attempts
    terminal_status["deadline_seconds"] = deadline_seconds
    return None, terminal_status, []
def _mgmt_nl_attempt_provider_answer(
    *,
    provider: str,
    question: str,
    focus: str,
    identity: OperatorIdentity,
    caller_tenant_id: str,
    session_id: str,
    message_id: str,
    trace_id: str,
    context_pack: Dict[str, Any],
    audit_id: Optional[str],
    allowed_action_kinds: Set[str],
    current_user_attachments: Optional[List[Dict[str, Any]]] = None,
    provider_deadline: Optional[float] = None,
    provider_attempt: int = 0,
) -> Tuple[Optional[str], Dict[str, Any], List[Dict[str, Any]]]:
    enabled = _mgmt_nl_provider_feature_enabled()
    if provider in {"none", "off", "disabled", "deterministic"}:
        _management_ai_record_event(
            {
                "event_type": "management_ai.provider.skipped",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "actor_id": identity.operator_id,
                "provider": provider,
                "reason": "provider_disabled",
            }
        )
        return None, _mgmt_nl_provider_status(
            provider=provider,
            enabled=False,
            status="disabled",
            reason="provider_disabled",
        ), []
    if not enabled:
        _management_ai_record_event(
            {
                "event_type": "management_ai.provider.skipped",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "actor_id": identity.operator_id,
                "provider": provider,
                "reason": "feature_disabled",
            }
        )
        return None, _mgmt_nl_provider_status(
            provider=provider,
            enabled=False,
            status="disabled",
            reason="feature_disabled",
        ), []
    if provider not in {"codex", "codex_cli", "claude", "claude_cli", "openclaw", "openclaw_agent"}:
        _management_ai_record_event(
            {
                "event_type": "management_ai.provider.skipped",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "actor_id": identity.operator_id,
                "provider": provider,
                "reason": "unsupported_provider",
            }
        )
        return None, _mgmt_nl_provider_status(
            provider=provider,
            enabled=True,
            status="degraded",
            reason="unsupported_provider",
        ), []

    run_id = trace_id if provider_attempt == 0 else f"{trace_id}:fallback:{provider_attempt}"
    if provider_deadline is not None and time.monotonic() >= provider_deadline:
        return None, _mgmt_nl_provider_status(
            provider=provider,
            enabled=True,
            status="degraded",
            reason="provider_deadline_exhausted",
            run_id=run_id,
        ), []
    provider_mode = _mgmt_nl_provider_mode_from_context(context_pack)
    prompt = _mgmt_nl_provider_prompt(
        question=question,
        focus=focus,
        context_pack=context_pack,
    )
    multimodal_payload = _mgmt_nl_provider_multimodal_payload(
        prompt=prompt,
        attachments=current_user_attachments,
    )
    multimodal_summary = (
        multimodal_payload.get("summary")
        if isinstance(multimodal_payload, dict)
        else None
    )
    multimodal_supported = (
        bool(multimodal_payload and multimodal_summary and multimodal_summary.get("forwarded"))
        and _mgmt_nl_provider_supports_multimodal(provider)
    )
    multimodal_unsupported = bool(
        multimodal_payload
        and multimodal_summary
        and multimodal_summary.get("forwarded")
        and not multimodal_supported
    )
    if multimodal_unsupported:
        multimodal_summary = {
            **multimodal_summary,
            "forwarded": False,
            "reason": "multimodal_unsupported",
            "fallback": "text_only",
        }
    provider_started = time.monotonic()
    _management_ai_record_event(
        {
            "event_type": "management_ai.provider.started",
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "provider_run_id": run_id,
            "actor_id": identity.operator_id,
            "provider": provider,
            "route": _management_ai_provider_route(provider),
            "context_pack_id": context_pack.get("context_pack_id"),
            "mode": provider_mode,
            "prompt_bytes": len(prompt.encode("utf-8")),
            "multimodal": multimodal_summary,
        }
    )
    metadata = {
        "route": "POST /bff/management/nl/ask",
        "session_id": session_id,
        "message_id": message_id,
        "trace_id": trace_id,
        "provider_run_id": run_id,
        "tenant_id": caller_tenant_id,
        "audit_id": audit_id,
        "attachments": current_user_attachments or [],
        "multimodal": multimodal_summary,
        "control_mode": _mgmt_nl_provider_control_metadata(context_pack),
    }
    def _provider_failure(error: OpenClawOpsClientError) -> Tuple[None, Dict[str, Any], List[Dict[str, Any]]]:
        duration_ms = max(0, int((time.monotonic() - provider_started) * 1000))
        _management_ai_record_event(
            {
                "event_type": "management_ai.provider.failed",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "provider_run_id": run_id,
                "actor_id": identity.operator_id,
                "provider": provider,
                "mode": provider_mode,
                "duration_ms": duration_ms,
                "status_code": error.status_code,
                "error_code": error.error_code,
                "error_message": _management_ai_summary_value(error.message),
                "multimodal": multimodal_summary,
            }
        )
        status = _mgmt_nl_provider_status(
            provider=provider,
            enabled=True,
            status="degraded",
            reason=error.error_code,
            run_id=run_id,
        )
        status["mode"] = provider_mode
        if multimodal_summary:
            status["multimodal"] = multimodal_summary
        return None, status, []

    invoke_kwargs: Dict[str, Any] = {
        "provider": provider,
        "mode": provider_mode,
        "prompt": prompt,
        "context_pack": context_pack,
        "operator_id": identity.operator_id,
        "trace_id": run_id,
        "metadata": metadata,
    }
    if provider_deadline is not None:
        remaining_seconds = provider_deadline - time.monotonic()
        if remaining_seconds <= 0:
            return _provider_failure(
                OpenClawOpsClientError(
                    "Management AI provider deadline elapsed before invocation.",
                    status_code=504,
                    error_code="PROVIDER_DEADLINE_EXHAUSTED",
                )
            )
        invoke_kwargs["timeout_seconds"] = remaining_seconds
    if multimodal_supported and multimodal_payload:
        invoke_kwargs["messages"] = multimodal_payload.get("messages")
        invoke_kwargs["attachments"] = multimodal_payload.get("attachments")

    try:
        provider_payload = OpenClawOpsClient().invoke_assistant_provider(**invoke_kwargs)
    except OpenClawOpsClientError as exc:
        if not (multimodal_payload and multimodal_supported and _mgmt_nl_multimodal_unsupported_error(exc)):
            return _provider_failure(exc)
        multimodal_unsupported = True
        multimodal_summary = {
            **(multimodal_summary or {}),
            "forwarded": False,
            "reason": "multimodal_unsupported",
            "fallback": "text_only",
        }
        metadata["multimodal"] = multimodal_summary
        _management_ai_record_event(
            {
                "event_type": "management_ai.provider.multimodal_unsupported",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "provider_run_id": run_id,
                "actor_id": identity.operator_id,
                "provider": provider,
                "error_code": exc.error_code,
                "error_message": _management_ai_summary_value(exc.message),
                "multimodal": multimodal_summary,
            }
        )
        retry_kwargs: Dict[str, Any] = {
            "provider": provider,
            "mode": provider_mode,
            "prompt": prompt,
            "context_pack": context_pack,
            "operator_id": identity.operator_id,
            "trace_id": run_id,
            "metadata": metadata,
        }
        if provider_deadline is not None:
            remaining_seconds = provider_deadline - time.monotonic()
            if remaining_seconds <= 0:
                return _provider_failure(
                    OpenClawOpsClientError(
                        "Management AI provider deadline elapsed before multimodal retry.",
                        status_code=504,
                        error_code="PROVIDER_DEADLINE_EXHAUSTED",
                    )
                )
            retry_kwargs["timeout_seconds"] = remaining_seconds
        try:
            provider_payload = OpenClawOpsClient().invoke_assistant_provider(
                **retry_kwargs,
            )
        except OpenClawOpsClientError as retry_exc:
            return _provider_failure(retry_exc)

    data = provider_payload.get("data") if isinstance(provider_payload, dict) else {}
    provider_state = str((data or {}).get("status") or provider_payload.get("status") or "ok")
    if provider_state.strip().lower() not in _MGMT_NL_COMPLETED_PROVIDER_STATES:
        output = (data or {}).get("output") if isinstance(data, dict) else {}
        message = ""
        if isinstance(output, dict):
            message = str(output.get("message") or output.get("diagnostic_message") or "").strip()
        return _provider_failure(
            OpenClawOpsClientError(
                message or "Assistant provider returned a non-terminal answer state.",
                status_code=200,
                error_code=_mgmt_nl_provider_degraded_reason(provider_payload),
                payload=provider_payload,
            )
        )
    answer = _mgmt_nl_extract_provider_answer(provider_payload)
    actions = _mgmt_nl_extract_provider_actions(
        provider_payload,
        allowed_action_kinds=allowed_action_kinds,
    )
    duration_ms = max(0, int((time.monotonic() - provider_started) * 1000))
    _management_ai_record_event(
        {
            "event_type": "management_ai.provider.completed",
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "provider_run_id": run_id,
            "actor_id": identity.operator_id,
            "provider": str((data or {}).get("provider") or provider),
            "mode": provider_mode,
            "duration_ms": duration_ms,
            "provider_state": provider_state,
            "answer_present": bool(answer),
            "action_count": len(actions),
            "allowed_action_kinds": sorted(allowed_action_kinds),
            "output_summary": _management_ai_provider_output_summary(provider_payload),
            "multimodal": multimodal_summary,
        }
    )
    if not answer:
        empty_status = _mgmt_nl_provider_status(
            provider=provider,
            enabled=True,
            status="degraded",
            reason="provider_empty_answer",
            run_id=run_id,
        )
        empty_status["mode"] = provider_mode
        if multimodal_summary:
            empty_status["multimodal"] = multimodal_summary
        return None, empty_status, []
    status = _mgmt_nl_provider_status(
        provider=str((data or {}).get("provider") or provider),
        enabled=True,
        status=provider_state if provider_state != "ok" else "completed",
        run_id=run_id,
        used=True,
    )
    status["mode"] = provider_mode
    output = (data or {}).get("output") if isinstance(data, dict) else None
    if isinstance(output, dict):
        if output.get("sandbox") is not None:
            status["sandbox"] = output.get("sandbox")
        if output.get("workspace_class") is not None:
            status["workspace_class"] = output.get("workspace_class")
    if multimodal_summary:
        status["multimodal"] = multimodal_summary
    if multimodal_unsupported:
        status["reason"] = "multimodal_unsupported"
    if isinstance(data, dict) and data.get("redaction") is not None:
        status["redaction"] = data.get("redaction")
    return answer, status, actions
_MGMT_NL_PROVIDER_INLINE_GRACE_DEFAULT_SECONDS = 3.0
_MGMT_NL_STREAM_READ_TIMEOUT_DEFAULT_SECONDS = 30.0
_MGMT_NL_PROVIDER_FINALIZE_TASKS: Set["asyncio.Task[Any]"] = set()
def _mgmt_nl_provider_inline_grace_seconds() -> float:
    """Seconds POST /bff/management/nl/ask waits inline for the assistant provider
    before returning 202 with the deterministic answer and finishing the provider
    turn in the background. Override with
    PANTHEON_MANAGEMENT_NL_PROVIDER_INLINE_GRACE_SECONDS."""
    raw = os.getenv("PANTHEON_MANAGEMENT_NL_PROVIDER_INLINE_GRACE_SECONDS")
    if raw is None or not str(raw).strip():
        return _MGMT_NL_PROVIDER_INLINE_GRACE_DEFAULT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _MGMT_NL_PROVIDER_INLINE_GRACE_DEFAULT_SECONDS
    return value if value > 0 else _MGMT_NL_PROVIDER_INLINE_GRACE_DEFAULT_SECONDS
def _mgmt_nl_provider_inline_wait_seconds(_control_mode: Dict[str, Any]) -> float:
    """Product assistant turns never hold a development worktree lease."""

    return _mgmt_nl_provider_inline_grace_seconds()
def _mgmt_nl_stream_read_timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_MANAGEMENT_NL_STREAM_READ_TIMEOUT_SECONDS")
    if raw is None or not str(raw).strip():
        return _MGMT_NL_STREAM_READ_TIMEOUT_DEFAULT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _MGMT_NL_STREAM_READ_TIMEOUT_DEFAULT_SECONDS
    return value if value > 0 else _MGMT_NL_STREAM_READ_TIMEOUT_DEFAULT_SECONDS
def _mgmt_nl_sse_frame(payload: Any) -> str:
    if payload == "[DONE]":
        return "data: [DONE]\n\n"
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
def _mgmt_nl_json_response_payload(response: JSONResponse) -> Dict[str, Any]:
    raw = getattr(response, "body", b"") or b""
    if isinstance(raw, str):
        raw_text = raw
    else:
        raw_text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(raw_text) if raw_text else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
def _mgmt_nl_finalize_result(
    base_result: Dict[str, Any],
    *,
    answer: str,
    provider_status: Dict[str, Any],
    actions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Rewrite a processing nl/ask result into a completed one for the
    idempotency record once the provider answer is available."""
    completed_data = {
        **base_result.get("data", {}),
        "status": "completed",
        "lifecycle_status": "completed",
        "answer": answer,
        "provider_status": provider_status,
        "ui_actions": actions,
        "actions": actions,
    }
    completed_meta = {
        **base_result.get("meta", {}),
        "status": "completed",
        "lifecycle_status": "completed",
        "provider_status": provider_status,
    }
    return {**base_result, "data": completed_data, "meta": completed_meta}
async def _mgmt_nl_finalize_provider_turn(
    *,
    provider_task: "asyncio.Future[Any]",
    deterministic_answer: str,
    session_id: str,
    message_id: str,
    assistant_turn_id: str,
    trace_id: str,
    focus: str,
    resolved_key: str,
    idempotency_storage_key: Optional[str] = None,
    request_hash: str,
    audit_log_href: str,
    conversation_href: str,
    base_result: Dict[str, Any],
    command_reservation: Optional[ManagementNlCommandReservation] = None,
) -> None:
    """Finish a nl/ask exchange whose provider call exceeded the inline grace
    window: await the in-flight agent run, then append the assistant turn exactly
    once and rewrite the idempotency record from processing -> completed."""
    try:
        provider_answer, provider_status, actions = await provider_task
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("Management NL async provider turn failed", exc_info=True)
        provider_answer, actions = None, []
        provider_status = _mgmt_nl_provider_status(
            provider=_mgmt_nl_provider_name(),
            enabled=True,
            status="degraded",
            reason="provider_async_failed",
            run_id=trace_id,
        )
    answer = provider_answer or deterministic_answer
    try:
        _management_ai_record_event(
            {
                "event_type": "management_ai.exchange.completed",
                "session_id": session_id,
                "message_id": message_id,
                "assistant_turn_id": assistant_turn_id,
                "trace_id": trace_id,
                "route": "POST /bff/management/nl/ask",
                "answer": _management_ai_summary_value(answer),
                "provider_status": provider_status,
                "actions": actions,
                "action_count": len(actions),
                "async_finalized": True,
            }
        )
        _management_ai_append_turn(
            turn_id=assistant_turn_id,
            session_id=session_id,
            role="assistant",
            text=answer,
            created_at=utc_now(),
            trace_id=trace_id,
            provider_status=provider_status,
            ui_actions=actions,
        )
        _management_nl_publish_completed_events(
            session_id=session_id,
            message_id=message_id,
            assistant_turn_id=assistant_turn_id,
            trace_id=trace_id,
            focus=focus,
            provider_status=provider_status,
            action_count=len(actions),
            audit_log_href=audit_log_href,
            conversation_href=conversation_href,
        )
        final_result = _mgmt_nl_finalize_result(
            base_result,
            answer=answer,
            provider_status=provider_status,
            actions=actions,
        )
        _mgmt_nl_idempotency_put(
            idempotency_storage_key or resolved_key,
            request_hash=request_hash,
            result=final_result,
        )
        await _mgmt_nl_command_complete(
            command_reservation,
            final_result,
            display_key=resolved_key,
        )
    except Exception:
        log.warning("Failed to persist async-finalised Management NL turn", exc_info=True)
        await _mgmt_nl_command_mark_uncertain(
            command_reservation,
            reason="async_provider_finalization_failed",
        )
def _mgmt_nl_schedule_provider_finalize(**kwargs: Any) -> None:
    task = asyncio.create_task(_mgmt_nl_finalize_provider_turn(**kwargs))
    _MGMT_NL_PROVIDER_FINALIZE_TASKS.add(task)
    task.add_done_callback(_MGMT_NL_PROVIDER_FINALIZE_TASKS.discard)
async def bff_management_nl_ask(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
):
    """BFF-B6-001/BFF-B6-003: POST /bff/management/nl/ask — Management NL query endpoint."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)

    question = _agora_required_text(payload, "question")
    _mgmt_nl_validate_question_size(question)
    control_command = _mgmt_nl_parse_control_command(question)

    # BFF-B6-003: high-risk refusal policy — must run before idempotency, surface
    # collection, session creation, or SSE emission.
    risk = None if control_command is not None else _mgmt_nl_high_risk_classify(question)
    if risk is not None:
        audit_id = _mgmt_nl_record_high_risk_refusal(
            identity=identity,
            question=question,
            risk=risk,
            recorded_at=utc_now(),
        )
        raise _bff_error(
            403,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "NL query matches high-risk action pattern and was refused by policy",
            (
                f"The question contains the pattern {risk['matched_pattern']!r} "
                f"which falls under the high-risk category '{risk['matched_category']}'. "
                "This endpoint is read-only and cannot execute management mutations."
            ),
            precondition_failed="high_risk_nl_policy",
            suggestion=risk["safe_alternatives"],
            details_extra={
                "refused": True,
                "matched_category": risk["matched_category"],
                "matched_pattern": risk["matched_pattern"],
                "safe_alternatives": risk["safe_alternatives"],
                "followups": _MGMT_NL_HIGH_RISK_REFUSAL_FOLLOWUPS,
                "audit_id": audit_id,
            },
        )

    # BFF-B6-001-SEC-FIX: resolve caller tenant scope before any retrieval.
    caller_tenant_id = _mgmt_nl_caller_tenant(
        identity,
        requested_tenant=_first_nonblank(x_tenant_id, x_pantheon_tenant),
    )

    operator_context = _mgmt_nl_trim_text(payload.get("context"), max_len=4000)
    focus = _mgmt_nl_normalize_focus(payload.get("focus"))
    client_conversation_hint = _mgmt_nl_normalize_conversation_context(payload.get("conversation"))
    ui_snapshot = _mgmt_nl_normalize_ui_context(payload.get("ui"), operator_context=operator_context)
    allowed_action_kinds = _mgmt_nl_allowed_action_kinds(ui_snapshot)

    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    idempotency_storage_key = _mgmt_nl_idempotency_storage_key(
        resolved_key,
        actor_id=identity.operator_id,
        tenant_id=caller_tenant_id,
    )
    request_hash = _stable_json_hash({"route": "POST /bff/management/nl/ask", "payload": payload})
    legacy_cached = _mgmt_nl_idempotency_check(
        idempotency_storage_key,
        request_hash,
        display_key=resolved_key,
    )
    if legacy_cached is None and _request_dry_run_requested():
        return _dry_run_success_response(
            {
                "status": "accepted",
                "lifecycle_status": "accepted",
                "session_id": str(payload.get("session_id") or payload.get("sessionId") or ""),
                "message_id": "",
                "trace_id": str(payload.get("trace_id") or payload.get("traceId") or ""),
                "question": question,
                "focus": focus,
                "sources": [],
                "confidence": "dry_run",
            },
            status_code=202,
            idempotency_key=resolved_key,
            evidence_kind="ManagementNLQuery",
            extra_meta={
                "status": "accepted",
                "route": "POST /bff/management/nl/ask",
                "dry_run_mode": "compact_receipt",
            },
        )

    command_scope = _mgmt_nl_command_scope(
        actor_id=identity.operator_id,
        tenant_id=caller_tenant_id,
        resolved_key=resolved_key,
    )
    command_reservation, cached = await _mgmt_nl_command_admit(
        scope=command_scope,
        request_hash=request_hash,
        legacy_result=legacy_cached,
        display_key=resolved_key,
    )
    _MGMT_NL_COMMAND_RESERVATION_CONTEXT.set(command_reservation)
    if cached is not None:
        cached_data = cached.get("data") if isinstance(cached, dict) else {}
        _management_ai_record_event(
            {
                "event_type": "management_ai.exchange.replayed",
                "session_id": str((cached_data or {}).get("session_id") or payload.get("session_id") or payload.get("sessionId") or ""),
                "message_id": str((cached_data or {}).get("message_id") or ""),
                "trace_id": str((cached_data or {}).get("trace_id") or (cached_data or {}).get("traceId") or ""),
                "actor_id": identity.operator_id,
                "focus": focus,
                "route": "POST /bff/management/nl/ask",
                "idempotency_key": resolved_key,
            }
        )
        return JSONResponse(status_code=202, content=_management_json_clone(cached))

    now = utc_now()
    session_id = str(payload.get("sessionId") or payload.get("session_id") or f"mgmt-nl-{uuid.uuid4().hex[:10]}")
    message_id = f"mnl-{uuid.uuid4().hex[:16]}"
    trace_id = str(payload.get("traceId") or payload.get("trace_id") or f"mnl-trace-{uuid.uuid4().hex[:12]}")
    if control_command is not None:
        control_response = _mgmt_nl_handle_control_command(
            control_command=control_command,
            payload=payload,
            identity=identity,
            caller_tenant_id=caller_tenant_id,
            focus=focus,
            ui_snapshot=ui_snapshot,
            resolved_key=resolved_key,
            idempotency_storage_key=idempotency_storage_key,
            request_hash=request_hash,
            session_id=session_id,
            message_id=message_id,
            trace_id=trace_id,
            now=now,
        )
        control_result = json.loads(control_response.body)
        await _mgmt_nl_command_complete(
            command_reservation,
            control_result,
            display_key=resolved_key,
        )
        return control_response

    control_mode = _assistant_control_mode_for_identity(
        identity,
        management_session_id=session_id,
        touch=True,
    )
    _mgmt_nl_reject_development_payload(
        payload,
        identity=identity,
        caller_tenant_id=caller_tenant_id,
        control_mode=control_mode,
    )
    _management_ai_ensure_session(
        session_id=session_id,
        identity=identity,
        tenant_id=caller_tenant_id,
        now=now,
        title=question,
    )
    user_attachments = _management_ai_store_attachments(
        attachments=payload.get("attachments"),
        session_id=session_id,
        turn_id=message_id,
    )
    _management_ai_append_turn(
        turn_id=message_id,
        session_id=session_id,
        role="user",
        text=question,
        created_at=now,
        trace_id=trace_id,
        attachments=user_attachments,
        ui_snapshot=ui_snapshot,
    )
    conversation_context = _management_ai_server_conversation_context(
        session_id=session_id,
        client_hint=client_conversation_hint,
    )
    current_user_attachments = [
        _management_ai_attachment_api_payload(item)
        for item in user_attachments
    ]

    # BFF-B6-001-SEC-FIX: pass tenant scope to context collection.
    # _mgmt_nl_collect_context fans out to several read surface port list_* calls,
    # each a blocking urllib HTTP request to runtime-manager (timeout 2s each). On
    # the single-worker BFF that blocks the event loop for seconds per request;
    # run it in a worker thread so concurrent requests (and the FE-BFF gate's
    # nl/ask burst) are not starved.
    context_bundle = await asyncio.to_thread(
        _mgmt_nl_collect_context, focus, now, tenant_id=caller_tenant_id
    )
    snippets = context_bundle["snippets"]
    surfaces = context_bundle["surfaces"]
    evidence_entities = context_bundle.get("evidence_entities") or set()
    evidence_source_types = context_bundle.get("evidence_source_types") or set()

    deterministic_answer = _mgmt_nl_synthesize_answer(question, snippets, focus)
    confidence = _mgmt_nl_surface_confidence(surfaces)
    source_keys = list(snippets.keys())
    context_pack = _mgmt_nl_build_context_pack(
        session_id=session_id,
        question=question,
        focus=focus,
        identity=identity,
        caller_tenant_id=caller_tenant_id,
        snippets=snippets,
        surfaces=surfaces,
        source_keys=source_keys,
        confidence=confidence,
        evidence_entities=evidence_entities,
        evidence_source_types=evidence_source_types,
        operator_context=operator_context,
        conversation_context=conversation_context,
        ui_snapshot=ui_snapshot,
        control_mode=control_mode,
    )

    try:
        nl_capabilities = _capabilities_for_identity(identity)
    except Exception:
        nl_capabilities = None
    raw_evidence_refs = list(
        await asyncio.to_thread(
            read_store.list_evidence_refs,
            tenant_id=caller_tenant_id,
            linked_entities=evidence_entities,
            source_types=evidence_source_types,
        )
        or []
    )
    for _eref in raw_evidence_refs:
        if isinstance(_eref, dict):
            _eid = str(_eref.get("ref_id") or _eref.get("id") or "").strip()
            if _eid:
                _eref.setdefault("href", f"/api/v1/knowledge/evidence/{_eid}")
    processed_evidence_refs, redacted_evidence_count = redact_evidence_refs(
        identity, raw_evidence_refs, capabilities=nl_capabilities
    )

    audit_ref = {
        "target_type": "ManagementNLExchange",
        "target_id": message_id,
        "href": f"/bff/audit/entities/ManagementNLExchange/{message_id}",
    }

    try:
        accepted_audit = await asyncio.to_thread(
            _record_agora_audit_event,
            {
                "action": "management.nl.ask.accepted",
                "targetType": "ManagementNLExchange",
                "targetId": message_id,
                "actorId": identity.operator_id,
                "recordedAt": now,
                "sessionId": session_id,
                "focus": focus,
                "tenantId": caller_tenant_id,
                "confidence": confidence,
                "sourceSurfaces": source_keys,
            },
        )
    except Exception:
        log.warning("Failed to record management NL happy-path audit event", exc_info=True)
        raise _bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Management NL audit write failed",
            "happy_path_audit_write_failed",
            precondition_failed="audit_write",
            suggestion="Retry after the Agora audit store is available",
        )

    audit_ref["audit_id"] = accepted_audit.get("auditId") or accepted_audit.get("eventId")

    _management_ai_record_event(
        {
            "event_type": "management_ai.exchange.accepted",
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "actor_id": identity.operator_id,
            "route": "POST /bff/management/nl/ask",
            "question": _management_ai_summary_value(question),
            "focus": focus,
            "tenant_id": caller_tenant_id,
            "confidence": confidence,
            "source_keys": source_keys,
            "context_pack_id": context_pack.get("context_pack_id"),
            "conversation_recent_turn_count": len(conversation_context.get("recent_turns") or []),
            "client_conversation_recent_turn_count": len(client_conversation_hint.get("recent_turns") or []),
            "conversation_summary_present": bool(conversation_context.get("summary")),
            "ui": ui_snapshot,
            "attachment_count": len(user_attachments),
            "available_ui_action_kinds": sorted(allowed_action_kinds),
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "control_mode": {
                "state": control_mode.get("state"),
                "active": control_mode.get("active"),
                "mode": control_mode.get("mode"),
                "activation_id": control_mode.get("activation_id") or control_mode.get("activationId"),
            },
            "surfaces": _management_ai_surface_summary(surfaces),
            "audit_ref": audit_ref,
        }
    )
    # _mgmt_nl_maybe_provider_answer issues a synchronous, blocking HTTP call to
    # the OpenClaw adapter (OpenClawOpsClient.invoke_assistant_provider), which
    # drives the Claude/Codex CLI agent and can take 30s+. The BFF runs a single
    # uvicorn worker, so calling it inline would block the event loop and freeze
    # every other request (reads, writes, SSE) for the whole agent turn. Offload
    # it to a worker thread so the event loop stays free to serve concurrently.
    assistant_turn_id = f"{message_id}-assistant"
    audit_log_href = _management_ai_audit_href(session_id=session_id, trace_id=trace_id)
    conversation_href = _management_ai_conversation_href(session_id)

    # The assistant-provider call (OpenClawOpsClient.invoke_assistant_provider via
    # _mgmt_nl_maybe_provider_answer) is a synchronous, blocking HTTP call that
    # drives a CLI agent and routinely takes 30s+. Run it in a worker thread and
    # wait only up to a short inline grace window. If it finishes in time we answer
    # synchronously as before; otherwise we return 202 immediately with the
    # deterministic answer and providerStatus=processing, and a background task
    # finalises the assistant turn + idempotency record once the agent completes.
    # asyncio.wait (unlike wait_for) does NOT cancel on timeout, so the in-flight
    # agent run is preserved and handed to the finaliser.
    provider_task = asyncio.create_task(
        asyncio.to_thread(
            _mgmt_nl_maybe_provider_answer,
            provider=_mgmt_nl_provider_name(),
            question=question,
            focus=focus,
            identity=identity,
            caller_tenant_id=caller_tenant_id,
            session_id=session_id,
            message_id=message_id,
            trace_id=trace_id,
            context_pack=context_pack,
            audit_id=audit_ref.get("audit_id"),
            allowed_action_kinds=allowed_action_kinds,
            current_user_attachments=current_user_attachments,
        )
    )
    done, _ = await asyncio.wait(
        {provider_task}, timeout=_mgmt_nl_provider_inline_wait_seconds(control_mode)
    )
    provider_pending = provider_task not in done
    if provider_pending:
        provider_answer, actions = None, []
        provider_status = _mgmt_nl_provider_status(
            provider=_mgmt_nl_provider_name(),
            enabled=True,
            status="processing",
            reason="provider_async_pending",
            run_id=trace_id,
        )
    else:
        # Preserve the previous inline-await exception behaviour.
        provider_answer, provider_status, actions = provider_task.result()
    answer = provider_answer or deterministic_answer

    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "management.nl.ask.accepted",
        {"session_id": session_id, "message_id": message_id, "trace_id": trace_id, "focus": focus},
    )

    exchange_status = "processing" if provider_pending else "completed"
    result = {
        "status": "accepted",
        "data": {
            "status": exchange_status,
            "lifecycle_status": exchange_status,
            "answer": answer,
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "question": question,
            "focus": focus,
            "sources": source_keys,
            "confidence": confidence,
            "summary_context": snippets,
            "context_pack": context_pack,
            "provider_status": provider_status,
            "control_mode": control_mode,
            "ui_actions": actions,
            "actions": actions,
            "audit_ref": audit_ref,
            "audit_log": {
                "href": audit_log_href,
                "trace_id": trace_id,
            },
            "conversation": {
                "href": conversation_href,
                "session_id": session_id,
                "trace_id": trace_id,
            },
            "session": {
                "session_id": session_id,
                "ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            },
            "evidence_refs": processed_evidence_refs,
        },
        "meta": {
            "status": exchange_status,
            "lifecycle_status": exchange_status,
            "snapshot_at": now,
            "surfaces": surfaces,
            "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
            "provider_status": provider_status,
            "trace_id": trace_id,
            "context_pack_id": context_pack.get("context_pack_id"),
            "redacted_evidence_count": redacted_evidence_count,
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "control_mode": control_mode,
        },
    }
    _management_ai_record_event(
        {
            "event_type": "management_ai.exchange.completed",
            "session_id": session_id,
            "message_id": message_id,
            "assistant_turn_id": assistant_turn_id,
            "trace_id": trace_id,
            "actor_id": identity.operator_id,
            "route": "POST /bff/management/nl/ask",
            "answer": _management_ai_summary_value(answer),
            "provider_status": provider_status,
            "actions": actions,
            "action_count": len(actions),
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "control_mode": {
                "state": control_mode.get("state"),
                "active": control_mode.get("active"),
                "mode": control_mode.get("mode"),
                "activation_id": control_mode.get("activation_id") or control_mode.get("activationId"),
            },
            "fallback": provider_status.get("fallback"),
        }
    )
    if not provider_pending:
        _management_ai_append_turn(
            turn_id=assistant_turn_id,
            session_id=session_id,
            role="assistant",
            text=answer,
            created_at=utc_now(),
            trace_id=trace_id,
            provider_status=provider_status,
            ui_actions=actions,
        )
    _management_nl_publish_completed_events(
        session_id=session_id,
        message_id=message_id,
        assistant_turn_id=assistant_turn_id,
        trace_id=trace_id,
        focus=focus,
        provider_status=provider_status,
        action_count=len(actions),
        audit_log_href=audit_log_href,
        conversation_href=conversation_href,
    )
    _mgmt_nl_idempotency_put(idempotency_storage_key, request_hash=request_hash, result=result)
    if not provider_pending:
        await _mgmt_nl_command_complete(
            command_reservation,
            result,
            display_key=resolved_key,
        )
    if provider_pending:
        # The assistant turn was intentionally NOT persisted above: the store's
        # append_turn is not an upsert, so writing a placeholder here would leave
        # a duplicate turn once the real answer lands. The finaliser appends it
        # exactly once with the real provider answer and rewrites the idempotency
        # record from processing -> completed.
        _mgmt_nl_schedule_provider_finalize(
            provider_task=provider_task,
            deterministic_answer=deterministic_answer,
            session_id=session_id,
            message_id=message_id,
            assistant_turn_id=assistant_turn_id,
            trace_id=trace_id,
            focus=focus,
            resolved_key=resolved_key,
            idempotency_storage_key=idempotency_storage_key,
            request_hash=request_hash,
            audit_log_href=audit_log_href,
            conversation_href=conversation_href,
            base_result=result,
            command_reservation=command_reservation,
        )
    return JSONResponse(status_code=202, content=result)
def bff_management_nl_ask_stream(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
):
    """SSE-streaming variant of /bff/management/nl/ask."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)

    question = _agora_required_text(payload, "question")
    _mgmt_nl_validate_question_size(question)
    control_command = _mgmt_nl_parse_control_command(question)

    risk = None if control_command is not None else _mgmt_nl_high_risk_classify(question)
    if risk is not None:
        raise _bff_error(
            403,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "NL query matches high-risk action pattern and was refused by policy",
            "This endpoint is read-only and cannot execute management mutations.",
            precondition_failed="high_risk_nl_policy",
            suggestion=risk["safe_alternatives"],
            details_extra={"refused": True, "matched_category": risk["matched_category"]},
        )

    caller_tenant_id = _mgmt_nl_caller_tenant(
        identity, requested_tenant=_first_nonblank(x_tenant_id, x_pantheon_tenant)
    )
    operator_context = _mgmt_nl_trim_text(payload.get("context"), max_len=4000)
    focus = _mgmt_nl_normalize_focus(payload.get("focus"))
    now = utc_now()
    session_id = str(payload.get("sessionId") or payload.get("session_id") or f"mgmt-nl-{uuid.uuid4().hex[:10]}")
    trace_id = str(payload.get("traceId") or payload.get("trace_id") or f"mnl-trace-{uuid.uuid4().hex[:12]}")
    message_id = f"mnl-{uuid.uuid4().hex[:12]}"
    ui_snapshot = _mgmt_nl_normalize_ui_context(payload.get("ui"), operator_context=operator_context)

    if control_command is not None:
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        idempotency_storage_key = _mgmt_nl_idempotency_storage_key(
            resolved_key,
            actor_id=identity.operator_id,
            tenant_id=caller_tenant_id,
        )
        request_hash = _stable_json_hash({"route": "POST /bff/management/nl/ask/stream", "payload": payload})
        control_response = _mgmt_nl_handle_control_command(
            control_command=control_command,
            payload=payload,
            identity=identity,
            caller_tenant_id=caller_tenant_id,
            focus=focus,
            ui_snapshot=ui_snapshot,
            resolved_key=resolved_key,
            idempotency_storage_key=idempotency_storage_key,
            request_hash=request_hash,
            session_id=session_id,
            message_id=message_id,
            trace_id=trace_id,
            now=now,
        )
        control_payload = _mgmt_nl_json_response_payload(control_response)
        control_data = control_payload.get("data") if isinstance(control_payload.get("data"), dict) else {}
        answer = str((control_data or {}).get("answer") or "")
        provider_status = (control_data or {}).get("providerStatus") or (control_data or {}).get("provider_status") or {}
        audit_log = (control_data or {}).get("auditLog") or (control_data or {}).get("audit_log") or None
        conversation = (control_data or {}).get("conversation") or None
        ui_actions = (control_data or {}).get("uiActions") or (control_data or {}).get("ui_actions") or []
        command_kind = (control_data or {}).get("controlCommand") or (control_data or {}).get("control_command")

        def control_event_stream() -> Iterator[str]:
            yield _mgmt_nl_sse_frame(
                {
                    "type": "meta",
                    "session_id": (control_data or {}).get("session_id") or session_id,
                    "trace_id": (control_data or {}).get("trace_id") or trace_id,
                    "message_id": (control_data or {}).get("message_id") or message_id,
                    "control_command": command_kind,
                }
            )
            if answer:
                yield _mgmt_nl_sse_frame({"type": "delta", "text": answer})
            yield _mgmt_nl_sse_frame(
                {
                    "type": "done",
                    "text": answer,
                    "provider_status": provider_status,
                    "audit_log": audit_log,
                    "conversation": conversation,
                    "ui_actions": ui_actions,
                    "control_command": command_kind,
                }
            )
            yield _mgmt_nl_sse_frame("[DONE]")

        return StreamingResponse(
            control_event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    control_mode = _assistant_control_mode_for_identity(identity, management_session_id=session_id, touch=True)
    conversation_context = _management_ai_server_conversation_context(
        session_id=session_id,
        client_hint=_mgmt_nl_normalize_conversation_context(payload.get("conversation")),
    )
    context_bundle = _mgmt_nl_collect_context(focus, now, tenant_id=caller_tenant_id)
    snippets = context_bundle["snippets"]
    surfaces = context_bundle["surfaces"]
    confidence = _mgmt_nl_surface_confidence(surfaces)
    context_pack = _mgmt_nl_build_context_pack(
        session_id=session_id,
        question=question,
        focus=focus,
        identity=identity,
        caller_tenant_id=caller_tenant_id,
        snippets=snippets,
        surfaces=surfaces,
        source_keys=list(snippets.keys()),
        confidence=confidence,
        evidence_entities=context_bundle.get("evidence_entities") or set(),
        evidence_source_types=context_bundle.get("evidence_source_types") or set(),
        operator_context=operator_context,
        conversation_context=conversation_context,
        ui_snapshot=ui_snapshot,
        control_mode=control_mode,
    )
    prompt = _mgmt_nl_provider_prompt(question=question, focus=focus, context_pack=context_pack)
    provider_mode = _mgmt_nl_provider_mode_from_context(context_pack)

    _management_ai_ensure_session(
        session_id=session_id, identity=identity, tenant_id=caller_tenant_id, now=now, title=question
    )
    _management_ai_append_turn(
        turn_id=message_id, session_id=session_id, role="user", text=question, created_at=now, trace_id=trace_id
    )

    def event_stream() -> Iterator[str]:
        provider_run_id = trace_id
        provider_started = time.monotonic()
        _management_ai_record_event(
            {
                "event_type": "management_ai.provider.started",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "provider_run_id": provider_run_id,
                "actor_id": identity.operator_id,
                "provider": "openclaw",
                "route": _management_ai_provider_route("openclaw", stream=True),
                "context_pack_id": context_pack.get("context_pack_id"),
                "mode": provider_mode,
                "prompt_bytes": len(prompt.encode("utf-8")),
            }
        )
        yield _mgmt_nl_sse_frame(
            {
                "type": "meta", "session_id": session_id,
                "trace_id": trace_id, "message_id": message_id,
            }
        )
        chunks: List[str] = []
        final_text: Optional[str] = None
        had_error = False
        failure_event: Optional[Dict[str, Any]] = None
        try:
            for evt in OpenClawOpsClient().stream_assistant_provider(
                mode=provider_mode,
                prompt=prompt,
                context_pack=context_pack,
                operator_id=identity.operator_id,
                trace_id=trace_id,
                session_user=session_id,
                read_timeout_seconds=_mgmt_nl_stream_read_timeout_seconds(),
            ):
                if evt.get("type") == "delta":
                    chunks.append(str(evt.get("text") or ""))
                elif evt.get("type") == "done":
                    final_text = str(evt.get("text") or "")
                elif evt.get("type") == "error":
                    had_error = True
                    failure_event = {
                        "event_type": "management_ai.provider.failed",
                        "session_id": session_id,
                        "message_id": message_id,
                        "trace_id": trace_id,
                        "provider_run_id": provider_run_id,
                        "actor_id": identity.operator_id,
                        "provider": "openclaw",
                        "mode": provider_mode,
                        "duration_ms": max(0, int((time.monotonic() - provider_started) * 1000)),
                        "status_code": evt.get("status_code"),
                        "error_code": evt.get("error_code") or "OPENCLAW_STREAM_ERROR",
                        "error_message": _management_ai_summary_value(evt.get("message")),
                    }
                yield _mgmt_nl_sse_frame(evt)
        except OpenClawOpsClientError as exc:
            had_error = True
            failure_event = {
                "event_type": "management_ai.provider.failed",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "provider_run_id": provider_run_id,
                "actor_id": identity.operator_id,
                "provider": "openclaw",
                "mode": provider_mode,
                "duration_ms": max(0, int((time.monotonic() - provider_started) * 1000)),
                "status_code": exc.status_code,
                "error_code": exc.error_code,
                "error_message": _management_ai_summary_value(exc.message),
            }
            yield _mgmt_nl_sse_frame(
                {"type": "error", "error_code": exc.error_code, "message": exc.message}
            )
        except Exception as exc:  # noqa: BLE001
            had_error = True
            failure_event = {
                "event_type": "management_ai.provider.failed",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "provider_run_id": provider_run_id,
                "actor_id": identity.operator_id,
                "provider": "openclaw",
                "mode": provider_mode,
                "duration_ms": max(0, int((time.monotonic() - provider_started) * 1000)),
                "status_code": 500,
                "error_code": "BFF_STREAM_ERROR",
                "error_message": _management_ai_summary_value(str(exc)[:200]),
            }
            yield _mgmt_nl_sse_frame(
                {"type": "error", "error_code": "BFF_STREAM_ERROR", "message": str(exc)[:200]}
            )
        answer = "".join(chunks).strip() or (final_text or "").strip()
        if answer and not had_error:
            duration_ms = max(0, int((time.monotonic() - provider_started) * 1000))
            _management_ai_record_event(
                {
                    "event_type": "management_ai.provider.completed",
                    "session_id": session_id,
                    "message_id": message_id,
                    "trace_id": trace_id,
                    "provider_run_id": provider_run_id,
                    "actor_id": identity.operator_id,
                    "provider": "openclaw",
                    "provider_state": "completed",
                    "mode": provider_mode,
                    "duration_ms": duration_ms,
                    "output_summary": {
                        "model": "openclaw/main",
                        "transport": "responses_http",
                        "output_bytes": len(answer.encode("utf-8")),
                    },
                }
            )
            provider_status = {
                "provider": "openclaw",
                "used": True,
                "status": "completed",
                "transport": "responses_http",
            }
            _management_ai_append_turn(
                turn_id=f"{message_id}-assistant",
                session_id=session_id,
                role="assistant",
                text=answer,
                created_at=utc_now(),
                trace_id=trace_id,
                provider_status=provider_status,
            )
            yield _mgmt_nl_sse_frame({"type": "done", "text": answer, "provider_status": provider_status})
        elif failure_event is not None:
            _management_ai_record_event(failure_event)
        yield _mgmt_nl_sse_frame("[DONE]")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
async def bff_management_ai_audit(
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    message_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
    authorization: Optional[str] = Header(default=None),
):
    """Read backend Management AI audit events for conversation/provider tracing."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    events = _management_ai_list_audit_events(
        session_id=session_id,
        trace_id=trace_id,
        message_id=message_id,
        event_type=event_type,
        limit=limit,
    )
    canonical_events = _management_prune_camel_aliases(events)
    return {
        "data": {
            "id": "management_ai_audit",
            "items": canonical_events,
            "summary": {
                "total_events": len(canonical_events),
                "returned_items": len(canonical_events),
            },
        },
        "page_info": {
            "next_page_token": None,
            "total": len(canonical_events),
            "page_size": limit,
        },
        "meta": {
            "count": len(canonical_events),
            "filters": {
                "session_id": session_id,
                "trace_id": trace_id,
                "message_id": message_id,
                "event_type": event_type,
            },
        },
    }
async def bff_assistant_provider_usage_summary(
    auth_probe: bool = False,
    limit: int = Query(default=500, ge=1, le=500),
    window_hours: int = Query(default=168, ge=1, le=24 * 90),
    authorization: Optional[str] = Header(default=None),
):
    """Return provider/model usage history plus provider-reported quota snapshots."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _assistant_provider_usage_summary(
        auth_probe=auth_probe,
        limit=limit,
        window_hours=window_hours,
    )
async def bff_management_ai_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
):
    """List visible server-side Management AI conversations for frontend resync."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    caller_tenant_id = _mgmt_nl_caller_tenant(
        identity,
        requested_tenant=_first_nonblank(x_tenant_id, x_pantheon_tenant),
    )
    sessions = _management_ai_conversation_store().list_sessions(
        owner_id=identity.operator_id,
        tenant_id=caller_tenant_id,
        limit=limit,
    )
    items: List[Dict[str, Any]] = []
    for session in sessions:
        session_id = str(session.get("sessionId") or session.get("session_id") or session.get("id") or "").strip()
        if not session_id:
            continue
        try:
            _management_ai_require_session_access(session, identity, tenant_id=caller_tenant_id)
        except HTTPException:
            continue
        turn_count = len(_management_ai_conversation_store().list_turns(session_id))
        items.append(
            {
                "id": session_id,
                "session_id": session_id,
                "title": session.get("title") or "",
                "owner_id": session.get("owner_id") or session.get("ownerId"),
                "tenant_id": session.get("tenant_id") or session.get("tenantId"),
                "created_at": session.get("created_at") or session.get("createdAt"),
                "updated_at": session.get("updated_at") or session.get("updatedAt"),
                "turn_count": turn_count,
                "href": _management_ai_conversation_href(session_id),
            }
        )
    return {
        "data": {
            "id": "management_ai_conversations",
            "items": items,
            "summary": {
                "total_sessions": len(items),
                "returned_items": len(items),
            },
        },
        "page_info": {
            "next_page_token": None,
            "total": len(items),
            "page_size": limit,
        },
        "meta": {
            "count": len(items),
            "limit": limit,
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "surfaces": {
                "management_ai_conversation_list": {
                    "status": "ok",
                    "source": "management_ai_store",
                }
            },
        },
    }
async def bff_management_ai_conversation(
    session_id: str,
    trace_id: Optional[str] = None,
    limit: int = Query(default=500, ge=1, le=1000),
    authorization: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
):
    """Read full Management AI session turns from the server-side conversation store."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    clean_session_id = str(session_id or "").strip()
    caller_tenant_id = _mgmt_nl_caller_tenant(
        identity,
        requested_tenant=_first_nonblank(x_tenant_id, x_pantheon_tenant),
    )
    session = _management_ai_get_visible_session_or_404(
        clean_session_id,
        identity,
        tenant_id=caller_tenant_id,
    )
    turns = [
        _management_ai_turn_api_payload(turn)
        for turn in _management_ai_conversation_store().list_turns(clean_session_id)
    ][:limit]
    audit_log = {
        "href": _management_ai_audit_href(session_id=clean_session_id, trace_id=trace_id),
        "trace_id": trace_id,
    }
    return {
        "data": {
            "session_id": clean_session_id,
            "trace_id": trace_id,
            "turns": turns,
            "local_only": False,
            "missing_in_store": False,
            "owner_id": session.get("owner_id") or session.get("ownerId"),
            "tenant_id": session.get("tenant_id") or session.get("tenantId"),
            "created_at": session.get("created_at") or session.get("createdAt"),
            "updated_at": session.get("updated_at") or session.get("updatedAt"),
            "audit_log": audit_log,
            "session": {
                "session_id": clean_session_id,
                "ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            },
        },
        "meta": {
            "count": len(turns),
            "turn_cap": limit,
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "filters": {
                "session_id": clean_session_id,
                "trace_id": trace_id,
                "trace_id_ignored": trace_id is not None,
            },
            "surfaces": {
                "management_ai_conversation": {
                    "status": "ok",
                    "source": "management_ai_store",
                    "reason": None,
                }
            },
        },
    }
async def bff_management_ai_attachment(
    attachment_id: str,
    authorization: Optional[str] = Header(default=None),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
):
    """Return a BFF-proxied Management AI attachment object for visible sessions."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    found = _management_ai_conversation_store().find_attachment(attachment_id)
    if found is None:
        raise _bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"Management AI attachment not found: {attachment_id!r}",
            "management_ai_attachment_not_found",
            precondition_failed="management_ai_attachment",
        )
    metadata, turn = found
    caller_tenant_id = _mgmt_nl_caller_tenant(
        identity,
        requested_tenant=_first_nonblank(x_tenant_id, x_pantheon_tenant),
    )
    _management_ai_get_session_or_404(
        str(turn.get("sessionId") or turn.get("session_id") or ""),
        identity,
        tenant_id=caller_tenant_id,
    )
    try:
        content, mime_type, filename = _management_ai_conversation_store().read_attachment(attachment_id, metadata)
    except FileNotFoundError:
        raise _bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"Management AI attachment object not found: {attachment_id!r}",
            "management_ai_attachment_object_not_found",
            precondition_failed="management_ai_attachment_object",
        )
    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": f"inline; filename=\"{filename}\""},
    )
async def bff_management_readiness_ep5(
    authorization: Optional[str] = Header(default=None),
):
    """BFF: compose EP5 readiness status from task evidence and live gates."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _build_management_ep5_readiness_payload()
async def bff_management_readiness_broker_live(
    authorization: Optional[str] = Header(default=None),
):
    """BFF: expose broker-live readiness while preserving fail-closed gates."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _build_management_broker_live_readiness_payload()
async def bff_management_readiness_capital_binding_live(
    authorization: Optional[str] = Header(default=None),
):
    """BFF: expose capital-binding-live readiness without enabling writes."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _build_management_capital_binding_live_readiness_payload()
async def bff_management_readiness_bff_ha(
    authorization: Optional[str] = Header(default=None),
):
    """BFF: expose BFF HA readiness evidence and production topology gap."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _build_management_bff_ha_readiness_payload()
async def bff_management_readiness_strict_publish(
    authorization: Optional[str] = Header(default=None),
):
    """BFF: expose strict-publish audit readiness and blockers."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _build_management_strict_publish_readiness_payload()
def _ooda_packet_routes_enabled() -> bool:
    raw = os.getenv("PANTHEON_OODA_PACKET_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}
def _require_ooda_packet_routes_enabled() -> None:
    if _ooda_packet_routes_enabled():
        return
    raise _bff_error(
        503,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        "OODA packet read routes disabled",
        "PANTHEON_OODA_PACKET_ENABLED is disabled for this BFF instance.",
        precondition_failed="ooda_packet_feature_flag",
        suggestion="Re-enable the OODA packet read surface before retrying this route.",
    )
def _ooda_packet_list_payload(
    packets: List[Dict[str, Any]],
    *,
    surface_key: str,
    page_token: Optional[str] = None,
    page_size: int = 20,
    related: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    snapshot_at = utc_now()
    total = len(packets)
    page_items, next_page_token = _page_slice(packets, page_token, page_size)
    meta = _read_surface_meta(
        "ooda_packets",
        surface_key,
        snapshot_at=snapshot_at,
        total=total,
    )
    if related:
        meta["related"] = related
    return {
        "data": page_items,
        "items": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": meta,
    }
def _synthesis_conflict_log_routes_enabled() -> bool:
    raw = os.getenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}
def _persona_first_evaluation_readback_timeout_seconds() -> float:
    raw = os.getenv(
        "PANTHEON_PERSONA_FIRST_EVALUATION_READBACK_TIMEOUT_SECONDS",
        "15",
    ).strip()
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 15.0
def _persona_first_evaluation_readback_poll_seconds() -> float:
    raw = os.getenv(
        "PANTHEON_PERSONA_FIRST_EVALUATION_READBACK_POLL_SECONDS",
        "1",
    ).strip()
    try:
        return max(0.05, float(raw))
    except (TypeError, ValueError):
        return 1.0
def _register_persona_cron_required(
    persona_id: str,
    capital_pool_id: str,
    binding_id: str,
    *,
    runtime_id: Optional[str] = None,
    runtime_binding_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Register and authoritatively read back the required evaluation schedule."""
    from services.control_plane.cron.persona_cron_registrar import PersonaCronRegistrar

    registrar = PersonaCronRegistrar()
    result = registrar.register_for_persona(
        persona_id,
        capital_pool_id=capital_pool_id,
        workflow_ids=[_PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
        runtime_id=runtime_id,
        runtime_binding_id=runtime_binding_id,
        persona_capital_binding_id=binding_id,
    )
    body = result.to_dict()
    if body.get("mode") != "gateway_rpc":
        raise RuntimeError("first-evaluation schedule authority is unavailable (dry-run refused)")
    if body.get("failed"):
        raise RuntimeError(f"cron registration failed: {body['failed']}")
    runtime = registrar._get_runtime()
    authoritative_job = None
    readback_attempts = 0
    last_readback_error = ""
    if runtime is not None:
        timeout_seconds = _persona_first_evaluation_readback_timeout_seconds()
        poll_seconds = _persona_first_evaluation_readback_poll_seconds()
        deadline = time.monotonic() + timeout_seconds
        while True:
            readback_attempts += 1
            try:
                authoritative_job = registrar.get_first_evaluation_registration(
                    persona_id,
                    runtime=runtime,
                    runtime_id=runtime_id,
                    runtime_binding_id=runtime_binding_id,
                    capital_pool_id=capital_pool_id,
                    persona_capital_binding_id=binding_id,
                )
            except Exception as exc:  # noqa: BLE001
                last_readback_error = str(exc) or exc.__class__.__name__
                authoritative_job = None
            if authoritative_job is not None:
                break
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                break
            time.sleep(min(poll_seconds, remaining_seconds))
    else:
        last_readback_error = "authoritative cron runtime unavailable"
    if authoritative_job is None:
        suffix = f" after {readback_attempts} attempts"
        if last_readback_error:
            suffix = f"{suffix}: {last_readback_error}"
        raise RuntimeError(
            f"first-evaluation schedule failed authoritative readback{suffix}"
        )
    authoritative_event = registrar._decode_job_event(authoritative_job) or {}
    body["authoritative_readback"] = {
        "persona_id": persona_id,
        "workflow_id": _PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
        "runtime_id": runtime_id,
        "runtime_binding_id": runtime_binding_id,
        "capital_pool_id": capital_pool_id,
        "persona_capital_binding_id": binding_id,
        "registered": True,
        "job_id": authoritative_job.get("id"),
        "job_name": authoritative_job.get("name"),
        "request_id": authoritative_event.get("request_id"),
        "schedule": deepcopy(authoritative_job.get("schedule")),
        "session_target": authoritative_job.get("sessionTarget"),
        "readback_attempts": readback_attempts,
        "observed_at": utc_now(),
    }
    return body
def _remove_persona_cron_required(persona_id: str) -> Dict[str, Any]:
    """Remove first-evaluation owner rows and require authoritative absence."""
    from services.control_plane.cron.persona_cron_registrar import PersonaCronRegistrar

    result = PersonaCronRegistrar().remove_first_evaluation_registration(persona_id)
    if result.get("registered") is not False:
        raise RuntimeError("first-evaluation schedule removal lacks zero-owner readback")
    return result
def _persona_record_tenant_id(raw: Mapping[str, Any]) -> str:
    """Return the explicit owner tenant for a Persona record.

    Tenantless records are catalog or malformed rows, never tenant-admitted
    Personas.  Read paths therefore must not treat a missing value as a
    wildcard.  The registry and provisioning projections have used both
    top-level and metadata forms over time, so normalize the supported aliases
    here before applying the exact-match boundary.
    """
    metadata = raw.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    for value in (
        raw.get("tenant_id"),
        raw.get("tenantId"),
        metadata.get("tenant_id"),
        metadata.get("tenantId"),
    ):
        tenant_id = str(value or "").strip()
        if tenant_id:
            return tenant_id
    return ""
def _openclaw_agent_reconcile_request(
    persona: Dict[str, Any],
    *,
    reason: str,
    route_policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    persona_id = str(persona.get("persona_id") or persona.get("id") or "").strip()
    request: Dict[str, Any] = {
        "status": "pending",
        "reason": reason,
        "agent_id": persona_id,
        "model_id": f"openclaw/{persona_id}" if persona_id else "",
        "consumer": "scripts/openclaw-sync-persona-agents.py",
    }
    if callable(build_persona_runtime_profile):
        try:
            profile = build_persona_runtime_profile(persona, route_policy=route_policy).to_dict()
        except ValueError as exc:
            log.warning("Validation error in runtime profile generation for %s: %s", persona_id, exc)
            request.update({
                "status": "blocked",
                "blocked_reason": "invalid_persona_runtime_profile_inputs",
                "repair_action": "fix_persona_runtime_profile",
            })
            return request
        except Exception as exc:
            log.warning("Unexpected error generating runtime profile for %s: %s", persona_id, exc)
            request.update({
                "status": "blocked",
                "blocked_reason": "runtime_profile_generation_failed",
                "repair_action": "check_persona_runtime_profile_inputs",
            })
            return request
    else:
        profile = {}
    routing = dict(profile.get("model_routing") or {})
    if routing.get("status") != "ready":
        request.update({
            "status": "blocked",
            "blocked_reason": routing.get("blocked_reason") or routing.get("reason") or "model_routing_degraded",
            "repair_action": "fix_persona_route_policy_or_provider_pool",
        })
    request.update({
        "workspace_ref": profile.get("workspace_ref"),
        "sync_generation": profile.get("sync_generation"),
        "model_routing": routing,
    })
    return request
def _persona_provisioning_metadata(
    record: ProvisioningRecord,
    *,
    ids: Any,
    payload: Mapping[str, Any],
    owner: str,
    archetype: str,
    risk: str,
    mandate: Optional[str],
    strategy_family: Optional[str],
    traits: Optional[Dict[str, Any]],
    lifecycle_state: str,
) -> Dict[str, Any]:
    paper_ledger_id = f"paper-ledger-{ids.token}"
    runtime_binding_id = str(record.references.get("runtime_binding_id") or "").strip()
    runtime_id = str(record.references.get("runtime_id") or "").strip()
    metadata: Dict[str, Any] = {
        "owner": owner,
        "archetype": archetype,
        "risk_level": risk,
        "mandate": mandate,
        "strategy_family": strategy_family,
        "description": payload.get("description"),
        "memo": payload.get("memo"),
        "tenant_id": record.tenant_id,
        "provisioning_idempotency_key": record.idempotency_key,
        "provisioning_request_hash": record.request_hash,
        "provisioning_state": record.state,
        "provisioning_step": record.current_step,
        "initial_mode": "paper",
        "execution_mode": "paper",
        "success_rate": float(payload.get("successRate") or 0.0),
        "capital_mode": "paper",
        "paper_ledger_id": paper_ledger_id,
        "paper_ledger": {
            "id": paper_ledger_id,
            "mode": "paper",
            "persona_id": record.persona_id,
            "is_isolated": True,
            "benchmark_budget": payload.get("budget"),
        },
        # Internal canonical paper pool.  Public DTO projection intentionally
        # keeps capitalPoolId empty in paper mode.
        "legacy_paper_capital_pool_id": ids.capital_pool_id,
        "internal_paper_capital_pool_id": ids.capital_pool_id,
        "persona_capital_binding_id": ids.persona_capital_binding_id,
        "registry_id": ids.registry_id,
        "approval_decision_id": ids.approval_decision_id,
        "deployment_plan_id": ids.deployment_plan_id,
        "deployment_saga_id": ids.deployment_saga_id,
        "deployment_stage": "paper",
        "paper_runtime_state": (
            "running"
            if lifecycle_state == "paper_running"
            else "failed" if lifecycle_state == "provisioning_failed" else "provisioning"
        ),
        "live_capital_enabled": False,
        "live_write_enabled": False,
        "order_side_effects_allowed": False,
        "capital_side_effects_allowed": False,
        "governance_required": True,
        "recommended_governance_action": "none",
        "data_source_status": payload.get("dataSourceStatus")
        or payload.get("data_source_status")
        or {
            "state": "paper_readback_pending",
            "provider_count": len(payload.get("dataSources") or payload.get("data_sources") or []),
            "provider_status_counts": {},
            "live_ingestion_enabled": False,
            "order_side_effects_allowed": False,
        },
        "data_sources": payload.get("dataSources") or payload.get("data_sources") or [],
        "risk_profile": payload.get("riskProfile")
        or payload.get("risk_profile")
        or {
            "risk_level": risk,
            "max_drawdown": payload.get("maxDrawdown") or payload.get("max_drawdown"),
            "daily_loss_limit": payload.get("dailyLossLimit") or payload.get("daily_loss_limit"),
        },
        "evidence_refs": [
            f"evidence://persona-create/{record.persona_id}/request",
            f"evidence://persona-create/{record.persona_id}/capital-binding",
            f"evidence://persona-create/{record.persona_id}/deployment-saga",
        ],
    }
    readback_started_at = record.references.get("provisioning_readback_started_at")
    if isinstance(readback_started_at, str) and readback_started_at.strip():
        metadata["provisioning_readback_started_at"] = readback_started_at.strip()
    if runtime_binding_id:
        metadata["runtime_binding_id"] = runtime_binding_id
    if runtime_id:
        metadata["runtime_id"] = runtime_id
    if record.error:
        metadata["provisioning_error"] = deepcopy(record.error)
    if record.compensation:
        metadata["provisioning_compensation"] = deepcopy(record.compensation)
    if traits:
        metadata["traits"] = deepcopy(traits)
    metadata["openclaw_agent_reconcile"] = _openclaw_agent_reconcile_request(
        {
            "id": record.persona_id,
            "persona_id": record.persona_id,
            "name": str(payload.get("name") or record.normalized_name),
            "mandate": mandate or archetype,
            "strategy_family": strategy_family or archetype,
            "lifecycle_state": lifecycle_state,
            "metadata": {
                **metadata,
                "owner": owner,
                "archetype": archetype,
                "risk_level": risk,
            },
        },
        reason="persona_created",
    )
    return metadata
def _persona_create_required_data_sources(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    required = payload.get("required_data_sources") or payload.get("requiredDataSources")
    market = str(payload.get("market") or "").strip().upper()
    if not required and market:
        from .personas.service import _market_persona_required_data_sources

        required = _market_persona_required_data_sources({"market": market})
    return json.loads(json.dumps(required or []))
def _persona_record_for_provisioning(
    record: ProvisioningRecord,
    *,
    payload: Mapping[str, Any],
    owner: str,
    mutate_store: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    canonical_owner = str(record.request_payload.get("requested_by") or owner).strip()
    ids = deterministic_provisioning_ids(record)
    archetype = str(payload.get("archetype") or "generalist")
    risk = _normalize_risk_level(payload.get("risk") or "low")
    mandate = str(payload.get("mandate") or "").strip() or None
    strategy_family = str(
        payload.get("strategy_family") or payload.get("strategyFamily") or ""
    ).strip() or None
    raw_traits = payload.get("traits")
    traits = {
        key: raw_traits[key]
        for key in (
            "instruments",
            "risk_appetite",
            "decision_style",
            "time_horizon",
            "hard_rules",
            "persona_voice",
        )
        if isinstance(raw_traits, dict) and raw_traits.get(key) not in (None, "")
    } or None
    if record.state == "succeeded":
        lifecycle_state = "paper_running"
    elif record.state in {"failed", "compensated"}:
        lifecycle_state = "provisioning_failed"
    else:
        lifecycle_state = "provisioning"
    metadata = _persona_provisioning_metadata(
        record,
        ids=ids,
        payload=payload,
        owner=canonical_owner,
        archetype=archetype,
        risk=risk,
        mandate=mandate,
        strategy_family=strategy_family,
        traits=traits,
        lifecycle_state=lifecycle_state,
    )
    existing = read_store.get_persona(record.persona_id)
    if existing is None:
        if mutate_store:
            persona = persona_write_owner.create_persona(
                persona_id=record.persona_id,
                name=str(payload.get("name") or record.normalized_name),
                actor_id=canonical_owner,
                created_at=record.created_at,
                archetype=archetype,
                lifecycle_state=lifecycle_state,
                risk_level=risk,
                mandate=mandate,
                strategy_family=strategy_family,
                traits=traits,
                metadata=metadata,
                required_data_sources=_persona_create_required_data_sources(payload),
            )
        else:
            persona = {
                "id": record.persona_id,
                "persona_id": record.persona_id,
                "name": str(payload.get("name") or record.normalized_name),
                "actor_id": canonical_owner,
                "created_by": canonical_owner,
                "created_at": record.created_at,
                "archetype": archetype,
                "lifecycle_state": lifecycle_state,
                "risk_level": risk,
                "mandate": mandate,
                "strategy_family": strategy_family,
                "traits": traits,
                "metadata": metadata,
                "required_data_sources": _persona_create_required_data_sources(payload),
            }
    else:
        existing_metadata = existing.get("metadata")
        existing_metadata = existing_metadata if isinstance(existing_metadata, dict) else {}
        if mutate_store and (
            str(existing.get("name") or "").strip()
            != str(payload.get("name") or record.normalized_name).strip()
            or str(existing_metadata.get("tenant_id") or record.tenant_id) != record.tenant_id
        ):
            raise ProvisioningConflict(
                "stable Persona identity is already occupied by different tenant/name semantics"
            )
        if (
            record.state == "succeeded"
            and str(existing.get("lifecycle_state") or "") == "paper_running"
        ):
            lifecycle_state = "paper_running"
        elif existing.get("lifecycle_state") and record.state == "succeeded":
            lifecycle_state = str(existing.get("lifecycle_state"))
        if mutate_store:
            persona = persona_write_owner.update_persona(
                record.persona_id,
                lifecycle_state=lifecycle_state,
                metadata=metadata,
            ) or existing
        else:
            persona = {
                **existing,
                "id": record.persona_id,
                "persona_id": record.persona_id,
                "name": str(existing.get("name") or payload.get("name") or record.normalized_name),
                "actor_id": str(existing.get("actor_id") or canonical_owner),
                "created_by": str(existing.get("created_by") or canonical_owner),
                "archetype": existing.get("archetype") or archetype,
                "lifecycle_state": lifecycle_state,
                "risk_level": existing.get("risk_level") or risk,
                "mandate": existing.get("mandate") or mandate,
                "strategy_family": existing.get("strategy_family") or strategy_family,
                "traits": existing.get("traits") or traits,
                "metadata": {**existing_metadata, **metadata},
                "required_data_sources": existing.get("required_data_sources") or _persona_create_required_data_sources(payload),
            }
    return persona, metadata
_PM12_LEAGUE_FORMULA_VERSION = "pm12-default-v1"
_PM12_QUARTER_PATTERN = re.compile(r"^(?P<year>\d{4})-Q(?P<quarter>[1-4])$", re.IGNORECASE)
_PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER = (
    "promote_to_canary_candidate",
    "increase_research_budget",
    "grant_tool_access",
    "reduce_capital_access",
    "require_retraining",
    "freeze_persona",
    "suspend_persona",
    "retire_persona",
)
_PM12_QUARTERLY_RECOMMENDATION_ACTIONS = {
    "promote_to_canary_candidate": {
        "label": "Promote to canary candidate",
        "priority": "high",
        "riskLevel": "medium",
        "risk_level": "medium",
        "rationale": "Quarterly score and risk posture support canary-review consideration.",
    },
    "increase_research_budget": {
        "label": "Increase research budget",
        "priority": "medium",
        "riskLevel": "low",
        "risk_level": "low",
        "rationale": "Quarterly score supports additional research-only budget.",
    },
    "grant_tool_access": {
        "label": "Grant tool access",
        "priority": "medium",
        "riskLevel": "low",
        "risk_level": "low",
        "rationale": "Quarterly score and execution posture support expanded tool access review.",
    },
    "reduce_capital_access": {
        "label": "Reduce capital access",
        "priority": "high",
        "riskLevel": "high",
        "risk_level": "high",
        "rationale": "Risk or overall score calls for capital-access reduction review.",
    },
    "require_retraining": {
        "label": "Require retraining",
        "priority": "medium",
        "riskLevel": "medium",
        "risk_level": "medium",
        "rationale": "Quarterly component scores indicate retraining should be reviewed.",
    },
    "freeze_persona": {
        "label": "Freeze persona",
        "priority": "critical",
        "riskLevel": "critical",
        "risk_level": "critical",
        "rationale": "Quarterly score is below the freeze-review threshold.",
    },
    "suspend_persona": {
        "label": "Suspend persona",
        "priority": "critical",
        "riskLevel": "critical",
        "risk_level": "critical",
        "rationale": "Quarterly score is below the suspension-review threshold.",
    },
    "retire_persona": {
        "label": "Retire persona",
        "priority": "critical",
        "riskLevel": "critical",
        "risk_level": "critical",
        "rationale": "Quarterly score is below the retirement-review threshold.",
    },
}
def _pm12_current_quarter_id(snapshot_at: str) -> str:
    timestamp = _audit_datetime(snapshot_at) or datetime.now(timezone.utc)
    quarter = ((timestamp.month - 1) // 3) + 1
    return f"{timestamp.year}-Q{quarter}"
def _pm12_iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
def _pm12_quarter_window(quarter: Optional[str], snapshot_at: str) -> Dict[str, Any]:
    raw_quarter = str(quarter or "").strip().upper() or _pm12_current_quarter_id(snapshot_at)
    match = _PM12_QUARTER_PATTERN.match(raw_quarter)
    if not match:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_quarter",
                "message": "quarter must use YYYY-Qn format, for example 2026-Q2.",
                "field": "quarter",
            },
        )
    year = int(match.group("year"))
    quarter_number = int(match.group("quarter"))
    start_month = ((quarter_number - 1) * 3) + 1
    start_at = datetime(year, start_month, 1, tzinfo=timezone.utc)
    if quarter_number == 4:
        end_exclusive_at = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_exclusive_at = datetime(year, start_month + 3, 1, tzinfo=timezone.utc)
    quarter_id = f"{year}-Q{quarter_number}"
    return {
        "quarter": quarter_id,
        "year": year,
        "quarter_number": quarter_number,
        "label": f"{year} Q{quarter_number}",
        "start_at": _pm12_iso_z(start_at),
        "end_exclusive_at": _pm12_iso_z(end_exclusive_at),
        "timezone": "UTC",
    }
def _pm12_add_recommendation_action(action_ids: List[str], action_id: str) -> None:
    if action_id in _PM12_QUARTERLY_RECOMMENDATION_ACTIONS and action_id not in action_ids:
        action_ids.append(action_id)
def _pm12_recommendation_action_ids(item: Dict[str, Any]) -> List[str]:
    components = item.get("components") if isinstance(item.get("components"), dict) else {}
    overall = _management_number(item.get("score")) or _management_number(item.get("overall_score")) or 0.0
    risk_score = _management_number(components.get("risk_score"))
    execution_score = _management_number(components.get("execution_score"))
    activity_score = _management_number(components.get("activity_score"))
    action_ids: List[str] = []

    if overall >= 85.0 and (risk_score is None or risk_score >= 70.0) and (
        execution_score is None or execution_score >= 65.0
    ):
        _pm12_add_recommendation_action(action_ids, "promote_to_canary_candidate")
        _pm12_add_recommendation_action(action_ids, "increase_research_budget")
        _pm12_add_recommendation_action(action_ids, "grant_tool_access")
    elif overall >= 70.0 and (risk_score is None or risk_score >= 60.0):
        _pm12_add_recommendation_action(action_ids, "increase_research_budget")
        _pm12_add_recommendation_action(action_ids, "grant_tool_access")

    if risk_score is not None and risk_score < 55.0:
        _pm12_add_recommendation_action(action_ids, "reduce_capital_access")
    if (execution_score is not None and execution_score < 55.0) or (
        activity_score is not None and activity_score < 45.0
    ):
        _pm12_add_recommendation_action(action_ids, "require_retraining")
    if overall < 55.0:
        _pm12_add_recommendation_action(action_ids, "require_retraining")
        _pm12_add_recommendation_action(action_ids, "reduce_capital_access")
    if overall < 45.0:
        _pm12_add_recommendation_action(action_ids, "freeze_persona")
    if overall < 35.0:
        _pm12_add_recommendation_action(action_ids, "suspend_persona")
    if overall < 25.0:
        _pm12_add_recommendation_action(action_ids, "retire_persona")

    if not action_ids:
        _pm12_add_recommendation_action(action_ids, "require_retraining")
    return [
        action_id
        for action_id in _PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER
        if action_id in action_ids
    ]
def _pm12_quarterly_recommendation_item(
    item: Dict[str, Any],
    *,
    action_id: str,
    quarter_window: Dict[str, Any],
    evidence_refs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    action = _PM12_QUARTERLY_RECOMMENDATION_ACTIONS[action_id]
    persona_id = str(item.get("persona_id") or item.get("personaId") or item.get("id") or "")
    score = _management_number(item.get("score")) or _management_number(item.get("overall_score")) or 0.0
    evidence_sample = list(item.get("evidence_refs") or [])[:5]
    evidence_ref_ids = [
        str(ref.get("refId") or ref.get("ref_id") or ref.get("id"))
        for ref in evidence_sample
        if ref.get("refId") or ref.get("ref_id") or ref.get("id")
    ]
    recommendation_id = f"pm12-{quarter_window['quarter'].lower()}-{persona_id}-{action_id}"
    review_id = _promotion_review_revision_id(
        recommendation_id,
        item.get("ranking_snapshot_id"),
    )
    submission = _promotion_review_submission_projection(review_id)
    decision = _promotion_review_decision_projection(review_id)

    if decision:
        review_status = "decision_accepted"
        decision_status = str((decision or {}).get("decision_status") or "accepted")
    elif submission:
        review_status = "pending_human_gate"
        decision_status = "pending"
    else:
        review_status = "recommended_not_submitted"
        decision_status = "pending"

    human_review_state = {
        "status": review_status,
        "decision_status": decision_status,
        "submitted": bool(submission),
        "submit_status": (submission or {}).get("submit_status") if submission else "not_submitted",
        "decision": (decision or {}).get("decision") if decision else None,
        "decided_at": (decision or {}).get("decided_at") if decision else None,
        "decided_by": (decision or {}).get("decided_by") if decision else None,
    }

    governance = {
        "requires_human_gate_decision": True,
        "destinations": ["human_inbox", "governance_queue", "human_gate_decision"],
        "human_inbox_route": "/bff/management/human-inbox",
        "governance_queue_route": "/api/v1/operator/governance/approval-queue",
        "decision_type": "HumanGateDecision",
        "live_capital_mutation": False,
    }
    return {
        "id": recommendation_id,
        "recommendation_id": recommendation_id,
        "review_id": review_id,
        "promotion_review_id": review_id,
        "quarter": quarter_window["quarter"],
        "quarter_window": quarter_window,
        "persona_id": persona_id,
        "ranking_snapshot_id": item.get("ranking_snapshot_id"),
        "ranking_evidence_ref": (
            f"ranking-snapshot:{item.get('ranking_snapshot_id')}"
            if item.get("ranking_snapshot_id")
            else f"ranking-evidence:{quarter_window['quarter'].lower()}-{persona_id}"
        ),
        "human_review_state": human_review_state,
        "name": item.get("name"),
        "owner": item.get("owner"),
        "archetype": item.get("archetype"),
        "state": item.get("state"),
        "stage": item.get("stage"),
        "deployment_stage": item.get("deployment_stage"),
        "capital_mode": item.get("capital_mode"),
        "capital_scope": item.get("capital_scope"),
        "capital_scope_id": item.get("capital_scope_id"),
        "capital_pool_id": item.get("capital_pool_id"),
        "capital_sleeve_id": item.get("capital_sleeve_id"),
        "paper_ledger_id": item.get("paper_ledger_id"),
        "current_weight": item.get("current_weight"),
        "target_weight": item.get("target_weight"),
        "delta": item.get("delta"),
        "current_weight_source": item.get("current_weight_source"),
        "binding_state": item.get("binding_state"),
        "binding_resolution": item.get("binding_resolution"),
        "runtime_resolution": item.get("runtime_resolution"),
        "session_resolution": item.get("session_resolution"),
        "telemetry_resolution": item.get("telemetry_resolution"),
        "binding_ids": list(item.get("binding_ids") or []),
        "strategy_ids": list(item.get("strategy_ids") or []),
        "runtime_ids": list(item.get("runtime_ids") or []),
        "capital_pool_ids": list(item.get("capital_pool_ids") or []),
        "sleeve_ids": list(item.get("sleeve_ids") or []),
        "artifact_ids": list(item.get("artifact_ids") or []),
        "broker_ids": list(item.get("broker_ids") or []),
        "eligible": item.get("eligible"),
        "exclusion_reason": item.get("exclusion_reason"),
        "exclusion_reasons": list(item.get("exclusion_reasons") or []),
        "exclusion_codes": list(item.get("exclusion_codes") or []),
        "evidence_coverage": item.get("evidence_coverage"),
        "source_confidence": item.get("source_confidence"),
        "risk": item.get("risk"),
        "rank": item.get("rank"),
        "score": score,
        "tier": item.get("tier"),
        "tier_id": item.get("tier_id"),
        "tier_label": item.get("tier_label"),
        "allocation_policy_input": json.loads(
            json.dumps(item.get("allocation_policy_input") or {})
        ),
        "formula_version": item.get("formula_version") or _PM12_LEAGUE_FORMULA_VERSION,
        "action_id": action_id,
        "action_label": action["label"],
        "recommendation_type": "governance_advisory",
        "status": "recommended",
        "priority": action["priority"],
        "risk_level": action["risk_level"],
        "target": {"type": "persona", "id": persona_id},
        "rationale": f"{action['rationale']} Score={score:.2f}; tier={item.get('tier') or 'unknown'}.",
        "rationale_codes": [
            f"tier:{item.get('tier') or 'unknown'}",
            f"action:{action_id}",
            "policy:no_direct_live_capital",
        ],
        "metrics": item.get("metrics") or {},
        "components": item.get("components") or {},
        "evidence_refs": evidence_sample,
        "evidence_ref_ids": evidence_ref_ids,
        "governance": governance,
        "requires_human_gate_decision": True,
        "live_capital_mutation": False,
        "policy": "read_only_governance_advisory",
        "links": {
            "persona": f"/bff/personas/{persona_id}",
            "human_inbox": "/bff/management/human-inbox",
            "governance_queue": "/api/v1/operator/governance/approval-queue",
        },
    }
_PROMOTION_REVIEW_ACTION_IDS: Set[str] = set(_PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER)
_PROMOTION_REVIEW_PROMOTION_ACTION_IDS: Set[str] = {"promote_to_canary_candidate"}
_PROMOTION_REVIEW_DECISIONS: Set[str] = {"approve", "approve_with_conditions", "reject"}
_PROMOTION_REVIEW_ID_PREFIX = "promotion-review:"
_PROMOTION_REVIEW_TARGET_PREFIX = "promotion_review:"
_PROMOTION_REVIEW_REVISION_MARKER = "--snapshot-"
_PROMOTION_REVIEW_REVISION_RE = re.compile(
    r"^(?P<recommendation_id>.+)--snapshot-(?P<digest>[0-9a-f]{32})$"
)
_PROMOTION_REVIEW_ID_QUARTER_RE = re.compile(r"pm12-(?P<quarter>\d{4}-q[1-4])-", re.IGNORECASE)
def _promotion_review_clean_id(review_id: Any) -> str:
    clean_id = str(review_id or "").strip()
    if clean_id.startswith(_PROMOTION_REVIEW_ID_PREFIX):
        clean_id = clean_id[len(_PROMOTION_REVIEW_ID_PREFIX):]
    if clean_id.startswith(_PROMOTION_REVIEW_TARGET_PREFIX):
        clean_id = clean_id[len(_PROMOTION_REVIEW_TARGET_PREFIX):]
    return clean_id
def _promotion_review_target_id(review_id: Any) -> str:
    return f"{_PROMOTION_REVIEW_TARGET_PREFIX}{_promotion_review_clean_id(review_id)}"
def _promotion_review_revision_id(
    recommendation_id: Any,
    ranking_snapshot_id: Any,
) -> str:
    clean_recommendation_id = _promotion_review_clean_id(recommendation_id)
    clean_snapshot_id = str(ranking_snapshot_id or "").strip()
    if not clean_recommendation_id or not clean_snapshot_id:
        return clean_recommendation_id
    digest = hashlib.sha256(
        f"{clean_recommendation_id}\x00{clean_snapshot_id}".encode("utf-8")
    ).hexdigest()[:32]
    return (
        f"{clean_recommendation_id}"
        f"{_PROMOTION_REVIEW_REVISION_MARKER}{digest}"
    )
def _promotion_review_revision_recommendation_id(review_id: Any) -> str:
    clean_id = _promotion_review_clean_id(review_id)
    match = _PROMOTION_REVIEW_REVISION_RE.fullmatch(clean_id)
    if match is None:
        return clean_id
    return match.group("recommendation_id")
def _promotion_review_record_revision_id(command: Dict[str, Any]) -> str:
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    recommendation_id = _human_inbox_promotion_recommendation_id(command)
    ranking_snapshot_id = str(params.get("ranking_snapshot_id") or "").strip()
    expected_revision_id = _promotion_review_revision_id(
        recommendation_id,
        ranking_snapshot_id,
    )
    asserted_ids = [
        str(params.get(key) or "").strip()
        for key in ("review_id", "promotion_review_id")
        if str(params.get(key) or "").strip()
    ]
    if ranking_snapshot_id:
        if asserted_ids and any(
            _promotion_review_clean_id(asserted_id) != expected_revision_id
            for asserted_id in asserted_ids
        ):
            return ""
        return expected_revision_id
    # Snapshotless legacy records predate revision identities. They remain
    # readable under the stable recommendation id but cannot authorize a
    # snapshot-bound decision or allocation.
    if asserted_ids and any(
        _promotion_review_clean_id(asserted_id) != recommendation_id
        for asserted_id in asserted_ids
    ):
        return ""
    return recommendation_id
def _promotion_review_quarter_from_id(review_id: Any) -> Optional[str]:
    match = _PROMOTION_REVIEW_ID_QUARTER_RE.search(_promotion_review_clean_id(review_id))
    if match is None:
        return None
    return match.group("quarter").upper()
def _promotion_review_stage_path(recommendation: Dict[str, Any]) -> Dict[str, Any]:
    action_id = str(recommendation.get("action_id") or "").strip()
    stage = str(
        recommendation.get("stage") or recommendation.get("state") or ""
    ).strip().lower()
    if "canary" in stage:
        from_stage = "canary"
    elif "live" in stage:
        from_stage = "live"
    else:
        from_stage = "paper"

    if action_id in _PROMOTION_REVIEW_PROMOTION_ACTION_IDS:
        if from_stage == "canary":
            target_stage = "live_candidate"
            review_kind = "canary_to_live_review"
        elif from_stage == "live":
            target_stage = "live_rebalance_review"
            review_kind = "live_ranking_review"
        else:
            target_stage = "canary_candidate"
            review_kind = "paper_to_canary_review"
    elif action_id in {"reduce_capital_access", "freeze_persona", "suspend_persona", "retire_persona"}:
        target_stage = "risk_containment_review"
        review_kind = "risk_containment_review"
    elif action_id in {"increase_research_budget", "grant_tool_access"}:
        target_stage = "resource_change_review"
        review_kind = "resource_change_review"
    else:
        target_stage = "governance_review"
        review_kind = "ranking_governance_review"

    return {
        "from_stage": from_stage,
        "target_stage": target_stage,
        "review_kind": review_kind,
        "eventual_live_stage": "live",
        "live_requires_separate_human_gate": target_stage != "risk_containment_review",
    }
def _latest_promotion_review_submission(review_id: Any) -> Optional[Dict[str, Any]]:
    clean_id = _promotion_review_clean_id(review_id)
    for record in reversed(command_store._get_all_commands()):
        if not _human_inbox_trusted_promotion_submission(record):
            continue
        if _promotion_review_record_revision_id(record) == clean_id:
            return record
    return None
def _promotion_review_submission_projection(
    review_id: Any,
    *,
    include_source_recommendation: bool = False,
) -> Optional[Dict[str, Any]]:
    record = _latest_promotion_review_submission(review_id)
    if record is None:
        return None
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    review_revision_id = _promotion_review_record_revision_id(record)
    projection = {
        "submitted": True,
        "submit_status": record.get("status"),
        "command_id": record.get("command_id"),
        "commandId": record.get("command_id"),
        "receipt_id": record.get("command_id"),
        "submitted_at": record.get("submitted_at"),
        "submitted_by": audit.get("operator_id") or audit.get("actor") or audit.get("actor_id"),
        "recommendation_id": params.get("recommendation_id") or params.get("recommendationId"),
        "review_id": review_revision_id,
        "promotion_review_id": review_revision_id,
        "recommendation_action_id": params.get("recommendation_action_id") or params.get("recommendationActionId"),
        "ranking_snapshot_id": params.get("ranking_snapshot_id"),
        "quarter": params.get("quarter"),
        "persona_id": params.get("persona_id"),
        "stage_from": params.get("stage_from"),
        "stage_to": params.get("stage_to"),
        "review_kind": params.get("review_kind"),
        "human_inbox_id": _promotion_review_target_id(review_revision_id),
        "live_capital_mutation": False,
        "requires_human_gate_decision": True,
    }
    if include_source_recommendation and isinstance(params.get("source_recommendation"), dict):
        projection["source_recommendation"] = json.loads(
            json.dumps(params.get("source_recommendation"))
        )
    return projection
def _latest_promotion_review_command(review_id: Any) -> Optional[Dict[str, Any]]:
    clean_id = _promotion_review_clean_id(review_id)
    for record in reversed(command_store._get_all_commands()):
        if (
            _human_inbox_decision_recommendation_id(record) == clean_id
            and _human_inbox_decision_projection_from_record(record) is not None
        ):
            return record
    return None
def _promotion_review_decision_projection(review_id: Any) -> Optional[Dict[str, Any]]:
    record = _latest_promotion_review_command(review_id)
    if record is None:
        return None
    return _human_inbox_decision_projection_from_record(record)
def _raise_if_promotion_review_direct_mutation_requested(payload: Dict[str, Any]) -> None:
    mutation_fields = (
        "live_capital_mutation",
        "liveCapitalMutation",
        "liveCapitalSideEffects",
        "runtime_mutation",
        "runtimeMutation",
    )
    for field in mutation_fields:
        if bool(payload.get(field)):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Promotion review decisions cannot request direct live/runtime mutation",
                f"{field} must be false or omitted; promotion requires a human-gated command receipt only.",
                precondition_failed=field,
                suggestion="Submit the promotion review decision without live/runtime mutation flags.",
            )
def _promotion_review_stored_source(
    recommendation: Dict[str, Any],
) -> Dict[str, Any]:
    stored = json.loads(json.dumps(recommendation))
    # Command params are visible on governance read surfaces. Persist the
    # Authoritative immutable ranking tuple, never submitter-supplied evidence.
    stored["evidence_refs"] = []
    stored["evidence_ref_ids"] = []
    return stored
def _pm12_performance_attribution_response(
    *,
    dimensions: List[str],
    period: str,
    page_token: Optional[str],
    page_size: int,
    data_id: str = "pm12-performance-attribution",
    surface_key: str = "performance_attribution",
    # Common filters:
    persona_id: Optional[str] = None,
    persona: Optional[str] = None,
    runtime_id: Optional[str] = None,
    runtime: Optional[str] = None,
    strategy_id: Optional[str] = None,
    strategy: Optional[str] = None,
    capital_pool_id: Optional[str] = None,
    pool: Optional[str] = None,
    sleeve_id: Optional[str] = None,
    sleeve: Optional[str] = None,
    artifact_id: Optional[str] = None,
    artifact: Optional[str] = None,
    broker_id: Optional[str] = None,
    broker: Optional[str] = None,
    stage: Optional[str] = None,
    as_of: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    snapshot_at = utc_now()
    period_key = str(period or "").strip() or "latest"
    sources = _pm12_performance_attribution_sources(tenant_id)
    facts = _pm12_performance_attribution_facts(sources, period_key)

    # Apply common filters to facts list
    facts = _filter_by_common_identifiers(
        facts,
        persona_id=persona_id, persona=persona,
        runtime_id=runtime_id, runtime=runtime,
        strategy_id=strategy_id, strategy=strategy,
        capital_pool_id=capital_pool_id, pool=pool,
        sleeve_id=sleeve_id, sleeve=sleeve,
        artifact_id=artifact_id, artifact=artifact,
        broker_id=broker_id, broker=broker,
        stage=stage, period=period_key, as_of=as_of
    )

    page_entries, total, next_page_token, aggregate_metrics = _pm12_performance_attribution_page_entries(
        facts,
        dimensions=dimensions,
        page_token=page_token,
        page_size=page_size,
    )
    page_items = _pm12_performance_attribution_rows(
        page_entries,
        period_key=period_key,
        sources=sources,
    )

    source_surfaces = {
        "runtime_bindings": _dataset_surface_status("runtime_bindings", snapshot_at=snapshot_at),
        "telemetry_summaries": _dataset_surface_status(
            "telemetry_summaries",
            snapshot_at=snapshot_at,
            has_data=bool(sources["telemetry_by_runtime_id"]) if sources["runtime_bindings"] else None,
            missing_message="Telemetry summaries unavailable for performance attribution runtimes.",
        ),
        "deployment_plans": _dataset_surface_status("deployment_plans", snapshot_at=snapshot_at),
        "persona_bindings": _dataset_surface_status("persona_bindings", snapshot_at=snapshot_at),
        "capital_pools": _dataset_surface_status("capital_pools", snapshot_at=snapshot_at),
        "personas": _dataset_surface_status("personas", snapshot_at=snapshot_at),
        "strategies": _dataset_surface_status("strategy_specs", snapshot_at=snapshot_at),
    }
    attribution_surface = _aggregate_group_surface(
        surface_key,
        list(source_surfaces.values()),
        snapshot_at=snapshot_at,
        unavailable_message="Performance attribution aggregate unavailable.",
        degraded_message="Performance attribution is degraded because one or more source surfaces are degraded.",
    )
    surfaces = {
        name: _performance_ranking_source_surface(surface, snapshot_at=snapshot_at)
        for name, surface in {
            surface_key: attribution_surface,
            **source_surfaces,
        }.items()
    }
    if surface_key != "performance_attribution":
        surfaces["performance_attribution"] = _performance_ranking_source_surface(attribution_surface, snapshot_at=snapshot_at)
    summary = {
        "period": period_key,
        "dimensions": dimensions,
        "supported_dimensions": list(_PM12_ATTRIBUTION_DIMENSIONS),
        "row_count": total,
        "returned_row_count": len(page_items),
        "runtime_count": aggregate_metrics["runtime_count"],
        "telemetry_runtime_count": aggregate_metrics["telemetry_runtime_count"],
        "holding_count": aggregate_metrics["holding_count"],
        "total_pnl": aggregate_metrics["total_pnl"],
        "total_notional": aggregate_metrics["total_notional"],
        "total_exposure": aggregate_metrics["total_exposure"],
        "worst_drawdown": aggregate_metrics["worst_drawdown"],
        "average_fill_rate": aggregate_metrics["average_fill_rate"],
        "average_slippage_bps": aggregate_metrics["average_slippage_bps"],
        "total_trades": aggregate_metrics["total_trades"],
        "latest_telemetry_at": aggregate_metrics["latest_telemetry_at"],
        "basis": "latest_runtime_telemetry_snapshot",
    }
    data = {
        "id": data_id,
        "period": period_key,
        "dimensions": dimensions,
        "items": page_items,
        "summary": summary,
    }
    return {
        "data": data,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
            "page_size": page_size,
        },
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": surfaces,
            "composition_sources": [
                "GET /api/v1/runtime-bindings",
                "GET /api/v1/telemetry/{runtime_id}/summary",
                "GET /api/v1/deployment-plans",
                "GET /api/v1/persona-capital-bindings",
                "GET /bff/capital-pools",
                "GET /bff/personas",
                "GET /bff/strategies",
            ],
            "period": period_key,
            "dimensions": dimensions,
            "policy": "read_only_performance_attribution",
        },
    }
def _ops_read_model_entry_for_persona(
    persona_id: str,
    *,
    period: str = "latest",
    tenant_id: Optional[str] = None,
) -> Optional[OperationsReadModelEntry]:
    """MGMT-OPS-001: compose the shared identity/source-confidence entry for one persona.

    Joins persona-fleet, performance-attribution, and capital-pool sources so a
    caller sees one data_confidence verdict and explicit diagnostics for any
    missing or unresolved join, instead of each page inventing its own
    fallback or rendering `nan`. See the "Read Model Contract" section of
    docs/04/pantheon_management_console_operations_workflow_2026-07-07/
    MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md.
    """
    clean_tenant = str(tenant_id or "").strip()
    persona = (
        _get_persona_directory_snapshot(clean_tenant).records_by_id.get(persona_id)
        if clean_tenant
        else read_store.get_persona(persona_id)
    )
    if persona is None:
        return None

    snapshot_at = utc_now()
    period_key = str(period or "").strip() or "latest"

    # This endpoint describes one persona.  Building the full 500-row fleet
    # just to recover its fallback identity/performance fields repeated all
    # downstream fleet fan-out.  The canonical persona and league projections
    # provide the same bounded fallback inputs without promoting them to
    # formal attribution evidence.
    league_entry = read_store.get_persona_league_entry(persona_id) or {}
    persona_metadata = (
        persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
    )
    fallback_performance = (
        league_entry.get("performance_summary")
        if isinstance(league_entry.get("performance_summary"), dict)
        else persona_metadata.get("performance")
        if isinstance(persona_metadata.get("performance"), dict)
        else {}
    )
    fleet_row = {
        "state": (
            league_entry.get("state")
            or persona.get("lifecycle_state")
            or persona.get("status")
        ),
        "performance_summary": fallback_performance,
        "runtime_id": league_entry.get("runtime_id"),
        "paper_ledger_id": league_entry.get("paper_ledger_id"),
        "capital_pool_id": league_entry.get("capital_pool_id"),
        "league_rank": league_entry.get("rank") or league_entry.get("league_rank"),
        "league_score": league_entry.get("score") or league_entry.get("league_score"),
        "perf_delta": league_entry.get("perf_delta"),
    }

    attribution_sources = _pm12_performance_attribution_sources(clean_tenant or None)
    persona_facts = [
        fact
        for fact in _pm12_performance_attribution_facts(attribution_sources, period_key)
        if str(fact.get("persona_id") or "") == persona_id
    ]
    has_formal_attribution = any(
        fact.get("telemetry_available") and ops_read_model_sanitize_metric(fact.get("total_pnl")) is not None
        for fact in persona_facts
    )
    has_partial_attribution = bool(persona_facts) and not has_formal_attribution

    sources: List[SourceStatus] = []
    diagnostics: List[SourceDiagnostic] = []

    if has_formal_attribution:
        attribution_status = SourceState.OK
    elif has_partial_attribution:
        attribution_status = SourceState.PARTIAL
    else:
        attribution_status = SourceState.UNAVAILABLE
        diagnostics.append(ops_read_model_diagnostic(
            "performance_attribution",
            "MISSING_ATTRIBUTION_MATCH",
            f"No performance-attribution row matched persona {persona_id} in period {period_key}.",
        ))
    sources.append(SourceStatus(
        source_name="performance_attribution",
        source_status=attribution_status,
        source_row_count=len(persona_facts),
        coverage_ratio=1.0 if persona_facts else 0.0,
    ))

    holdings_rows = [
        fact for fact in persona_facts
        if ops_read_model_sanitize_metric(fact.get("market_value")) is not None
    ]
    holdings_status = SourceState.OK if holdings_rows else SourceState.UNAVAILABLE
    if not holdings_rows:
        diagnostics.append(ops_read_model_diagnostic(
            "portfolio_holdings",
            "MISSING_HOLDINGS_MATCH",
            f"No holdings source returned a matching row for persona {persona_id}.",
        ))
    sources.append(SourceStatus(
        source_name="portfolio_holdings",
        source_status=holdings_status,
        source_row_count=len(holdings_rows),
    ))

    pool_ids_seen = dedupe_ids(fact.get("capital_pool_id") for fact in persona_facts)
    pools_by_id = attribution_sources.get("pools_by_id", {})
    unresolved_pool_ids = [pool_id for pool_id in pool_ids_seen if pool_id not in pools_by_id]
    if unresolved_pool_ids:
        capital_pool_status = SourceState.DEGRADED
        diagnostics.append(ops_read_model_diagnostic(
            "capital_pools",
            "CAPITAL_POOL_ID_UNRESOLVED",
            f"Capital pool id(s) {unresolved_pool_ids} referenced by attribution facts do not "
            "resolve to a capital-pool record.",
        ))
    elif pool_ids_seen:
        capital_pool_status = SourceState.OK
    else:
        capital_pool_status = SourceState.UNAVAILABLE
    sources.append(SourceStatus(
        source_name="capital_pools",
        source_status=capital_pool_status,
        source_row_count=len(pool_ids_seen),
    ))

    if fleet_row:
        sources.append(SourceStatus(
            source_name="persona_fleet_summary",
            source_status=SourceState.OK,
            source_row_count=1,
        ))
    else:
        sources.append(SourceStatus(
            source_name="persona_fleet_summary",
            source_status=SourceState.UNAVAILABLE,
        ))
        diagnostics.append(ops_read_model_diagnostic(
            "persona_fleet_summary",
            "PERSONA_NOT_IN_FLEET",
            f"Persona {persona_id} has no persona-fleet row to source a fallback summary from.",
        ))

    # Fleet now preserves missing persona-owned performance as null and exposes
    # explicit provenance.  The row itself is still a useful fallback identity
    # surface, but an unavailable performance source must never be promoted to
    # formal evidence or replaced with same-market seed values.
    fallback_has_signal = bool(fleet_row) and _is_persona_lifecycle_operational(
        persona.get("lifecycle_state") or persona.get("status")
    )
    is_fallback = not has_formal_attribution and not has_partial_attribution and fallback_has_signal
    if is_fallback:
        diagnostics.append(ops_read_model_diagnostic(
            "persona_fleet_summary",
            "FORMAL_ATTRIBUTION_MISSING_USING_FLEET_FALLBACK",
            "The persona-fleet row is the only persona-scoped summary because no formal "
            "attribution or holdings row matched this persona; preserve unavailable values "
            "and treat the row as fallback, not formal evidence.",
        ))

    has_degraded_source = any(source.source_status == SourceState.DEGRADED for source in sources)
    has_unavailable_source = any(source.source_status == SourceState.UNAVAILABLE for source in sources)

    confidence = classify_confidence(
        has_formal_match=has_formal_attribution,
        has_partial_evidence=has_partial_attribution,
        is_fallback=is_fallback,
        has_degraded_source=has_degraded_source,
        has_unavailable_source=has_unavailable_source,
    )

    if has_formal_attribution or has_partial_attribution:
        attribution_metrics = _pm12_attribution_metrics(persona_facts)
        pnl = ops_read_model_sanitize_metric(attribution_metrics.get("total_pnl"))
        drawdown = ops_read_model_sanitize_metric(attribution_metrics.get("worst_drawdown"))
        sharpe = None
    else:
        pnl = ops_read_model_sanitize_metric(fallback_performance.get("pnl"))
        drawdown = ops_read_model_sanitize_metric(fallback_performance.get("max_drawdown"))
        sharpe = ops_read_model_sanitize_metric(fallback_performance.get("sharpe"))

    rank_value = league_entry.get("rank") or league_entry.get("league_rank") or fleet_row.get("league_rank")
    score_value = ops_read_model_sanitize_metric(
        league_entry.get("score") or league_entry.get("league_score") or fleet_row.get("league_score")
    )

    stage = (
        str(fleet_row.get("state") or "").strip()
        or str(persona.get("lifecycle_state") or persona.get("status") or "").strip()
        or None
    )
    persona_label = str(persona.get("name") or "").strip() or None

    identity = build_operations_identity(
        persona_id=persona_id,
        persona_label=persona_label,
        stage=stage,
        runtime_ids=[fact.get("runtime_id") for fact in persona_facts] + [fleet_row.get("runtime_id")],
        paper_ledger_ids=[fleet_row.get("paper_ledger_id")],
        capital_pool_ids=pool_ids_seen + [fleet_row.get("capital_pool_id")],
        strategy_ids=[fact.get("strategy_id") for fact in persona_facts],
        broker_ids=[fact.get("broker_id") for fact in persona_facts],
        period=period_key,
        as_of=snapshot_at,
    )

    performance = OperationsPerformance(
        pnl=pnl,
        drawdown_pct=drawdown,
        sharpe=sharpe,
        rank=int(rank_value) if isinstance(rank_value, (int, float)) and not isinstance(rank_value, bool) else None,
        score=score_value,
        performance_delta=ops_read_model_sanitize_metric(fleet_row.get("perf_delta")),
    )

    return OperationsReadModelEntry(
        identity=identity,
        data_confidence=confidence,
        performance=performance,
        sources=sources,
        diagnostics=diagnostics,
    )
async def bff_types_compat(
    authorization: Optional[str] = Header(default=None),
):
    """
    Source-reference compatibility decision for /bff/types.

    The execute-plans repo declares its DTO universe in
    `src/lib/bff/types.ts`; the Pantheon BFF mirrors that shape for the
    surfaces it serves. This endpoint returns the canonical compatibility
    map so frontend tooling can validate that a Pantheon deployment
    advertises the expected entity DTOs without scraping route inventories.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return {
        "data": {
            "types_source": "execute-plans/src/lib/bff/types.ts",
            "exported_entities": [
                "Strategy", "Persona", "CapitalPool", "RankingFormula",
                "Rebalance", "Deployment", "Runtime", "EvolutionProgram",
                "ResearchExperiment", "Artifact", "Job", "Alert", "Incident",
                "ApprovalRequest", "AuditEvent", "SearchResult",
                "Tool", "McpServer", "McpTool", "Skill", "Channel",
                "RoutePolicy", "PolicyVersion", "PermissionMatrix",
                "MemoryUpdate", "EvolutionRun", "EvolutionCandidate",
                "FitnessFormula", "MutationRule", "AllocationSimulation",
                "PolicyViolation", "EvaluationRun", "ObjectVersion",
                "FeatureSet", "PerformanceSeries", "Watcher",
                "DecisionJournalEntry", "AllocationLimit", "PoolFreeze",
                "DeploymentStage", "McpSecret", "PromotionRecord",
                "MetricFreeze", "RebalanceOverride",
            ],
            "served_by": "pantheon-bff",
            "compatibility_decision": "execute-plans/src/lib/bff/types.ts is the canonical TypeScript declaration; the Pantheon BFF projects to it for /bff/* surfaces.",
        },
        "meta": {"snapshot_at": utc_now()},
    }
_V5_INTERVENTIONS_STORE: List[Dict[str, Any]] = []
def _v5_intervention_records(
    *,
    status: Optional[str] = None,
    kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    records_by_id: Dict[str, Dict[str, Any]] = {}
    store_lister = getattr(read_store, "list_v5_interventions", None)
    if callable(store_lister):
        for record in store_lister(status=status, kind=kind):
            if not isinstance(record, dict):
                continue
            record_id = str(record.get("intervention_id") or record.get("id") or "").strip()
            if record_id:
                records_by_id[record_id] = dict(record)

    for record in _V5_INTERVENTIONS_STORE:
        if not isinstance(record, dict):
            continue
        if status and str(record.get("status") or "") != status:
            continue
        if kind and str(record.get("kind") or "") != kind:
            continue
        record_id = str(record.get("intervention_id") or record.get("id") or "").strip()
        if record_id:
            records_by_id[record_id] = dict(record)

    return list(records_by_id.values())
async def _process_command(command_id: str):
    """
    Async command processor that dispatches to the Protected Internal API.
    Records authoritative status, result, and audit data for every execution.
    """
    import asyncio

    record = command_store.get_command(command_id)
    if not record:
        log.error("Worker: command %s not found in store", command_id)
        return

    command_type = CommandType(record["type"])
    params = record.get("params", {})
    audit = record.get("audit", {})

    # Runtime-only auth context avoids persisting bearer tokens in command audit records.
    runtime_auth = _COMMAND_AUTH_CONTEXT.pop(command_id, {})
    auth_token = runtime_auth.get("auth_token") or audit.get("auth_token")
    mfa_token = runtime_auth.get("mfa_token") or audit.get("mfa_token")

    # Mark processing
    await asyncio.sleep(0.05)  # brief yield to event loop
    command_store.update_status(command_id, CommandStatus.PROCESSING)

    try:
        execution_params = _resolve_execution_params_for_record(record)
    except Exception as exc:
        failed_at = utc_now()
        error = {
            "code": "TARGET_CONTEXT_UNAVAILABLE",
            "message": f"Unable to route command {command_id}: {exc}",
            "started_at": failed_at,
            "failed_at": failed_at,
            "suggestion": (
                "Refresh Pantheon runtime/incident read surfaces or use the secondary control path "
                "until the runtime target can be resolved."
            ),
        }
        audit["execution_completed_at"] = failed_at
        audit["executor"] = "command_executor"
        audit["failure_reason"] = error["message"]
        audit["failure_suggestion"] = error["suggestion"]
        command_store.update_status(
            command_id,
            CommandStatus.FAILED,
            error=error,
            audit=audit,
        )
        log.warning("Worker: command %s failed during routing resolution: %s", command_id, exc)
        return

    if command_type == CommandType.RECORD_SPONSOR_DECISION:
        try:
            committee_id = str(execution_params.get("committee_id") or "").strip()
            updated = read_store.record_sponsor_decision(
                committee_id,
                sponsor_decision=str(execution_params.get("sponsor_decision") or "").strip().lower(),
                rationale_ref=str(execution_params.get("rationale_ref") or "").strip(),
                actor_id=str(audit.get("operator_id") or "operator-command"),
                recorded_at=utc_now(),
            )
            if updated is None:
                raise ValueError(f"Committee {committee_id} could not be updated.")
            result = {
                "command_id": command_id,
                "committee_id": updated.get("committee_id"),
                "committee_ref": updated.get("committee_ref"),
                "sponsor_decision": updated.get("sponsor_decision"),
                "sponsor_decided_at": updated.get("sponsor_decided_at"),
                "sponsor_decided_by": updated.get("sponsor_decided_by"),
                "consensus_state": updated.get("consensus_state"),
                "rationale_ref": (updated.get("synthesis_summary") or {}).get("rationale_ref"),
                "service_handoff": updated.get("service_handoff") or {},
                "execution_completed_at": utc_now(),
            }
            audit["execution_completed_at"] = result["execution_completed_at"]
            audit["executor"] = "bff_read_store"
            audit["downstream_verified"] = True
            command_store.update_status(
                command_id,
                CommandStatus.EXECUTED,
                result=result,
                audit=audit,
            )
            log.info("Worker: command %s completed with status=%s", command_id, CommandStatus.EXECUTED.value)
            return
        except Exception as exc:
            failed_at = utc_now()
            error = {
                "code": "COMMITTEE_UPDATE_FAILED",
                "message": f"Unable to record sponsor decision: {exc}",
                "started_at": failed_at,
                "failed_at": failed_at,
                "suggestion": "Refresh the committee board projection and retry once the committee surface is available.",
            }
            audit["execution_completed_at"] = failed_at
            audit["executor"] = "bff_read_store"
            audit["failure_reason"] = error["message"]
            audit["failure_suggestion"] = error["suggestion"]
            command_store.update_status(
                command_id,
                CommandStatus.FAILED,
                error=error,
                audit=audit,
            )
            log.warning("Worker: command %s failed during committee update: %s", command_id, exc)
            return

    # Execute via real executor with propagated auth headers
    status, result, error = execute_command_with_status(
        command_id, command_type, execution_params,
        auth_token=auth_token, mfa_token=mfa_token,
    )

    # Enrich audit with execution timeline
    audit["execution_completed_at"] = result.get("execution_completed_at") if result else error.get("failed_at") if error else None
    audit["executor"] = "command_executor"
    if result:
        audit["downstream_verified"] = bool(
            result.get("downstream_verified")
            or result.get("authoritative_capital_readback")
            or result.get("dispatch_path") != "bff_action_adapter"
        )
    if error:
        audit["failure_reason"] = error.get("message", "")
        audit["failure_suggestion"] = error.get("suggestion", "")

    # Persist both result and enriched audit data
    command_store.update_status(
        command_id,
        status,
        result=result,
        error=error,
        audit=audit,
    )

    log.info(
        "Worker: command %s completed with status=%s",
        command_id, status.value,
    )
_process_command_stub = _process_command
_MAX_EVENTS = 500
SSE_CHANNEL_CATALOG = (
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
SSE_CHANNELS = set(SSE_CHANNEL_CATALOG)
_SSE_RESYNC_ROUTES: Dict[str, tuple[str, ...]] = {
    "approval": ("/bff/approvals", "/bff/v5/interventions"),
    "ask": (
        "/bff/management/ai/conversations",
        "/bff/management/ai/conversations/{id}",
        "/bff/agora/ask/sessions/{id}",
        "/bff/agora/committee/sessions/{id}",
    ),
}
class SseReplayUnavailableError(Exception):
    pass
_sse_buffers: Dict[str, deque] = {
    channel: deque(maxlen=_MAX_EVENTS) for channel in SSE_CHANNEL_CATALOG
}
_sse_subscribers: Dict[str, list[asyncio.Queue]] = {
    channel: [] for channel in SSE_CHANNEL_CATALOG
}
_incident_events = deque(maxlen=_MAX_EVENTS)
_incident_subscribers: list[asyncio.Queue] = []
def _sse_shared_replay_enabled() -> bool:
    mode = os.getenv("PANTHEON_BFF_SSE_REPLAY_STORE", "memory").strip().lower()
    return mode in {"1", "true", "file", "jsonl", "shared", "shared-file"}
def _sse_replay_store_label(channel: str) -> str:
    return "file" if channel in SSE_CHANNELS and _sse_shared_replay_enabled() else "in-memory"
def _sse_channel_for_buffer(buffer: deque) -> Optional[str]:
    for channel, candidate in _sse_buffers.items():
        if candidate is buffer:
            return channel
    return None
def _sse_shared_replay_file(channel: str) -> str:
    if channel not in SSE_CHANNELS:
        raise ValueError(f"Unknown SSE channel: {channel}")
    replay_dir = os.path.join(BFF_DATA_DIR, "sse_replay")
    os.makedirs(replay_dir, exist_ok=True)
    return os.path.join(replay_dir, f"{channel}.jsonl")
def _read_shared_sse_events(channel: str) -> list[dict]:
    path = _sse_shared_replay_file(channel)
    if not os.path.exists(path):
        return []
    events: list[dict] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise SseReplayUnavailableError("Shared SSE replay store is unreadable") from exc
            if isinstance(event, dict):
                events.append(event)
    return events[-_MAX_EVENTS:]
def _trim_shared_sse_events(path: str) -> None:
    with open(path, "r", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip()]
    if len(lines) <= _MAX_EVENTS:
        return
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(lines[-_MAX_EVENTS:])
def _append_shared_sse_event(channel: Optional[str], event: dict) -> None:
    if not channel or not _sse_shared_replay_enabled():
        return
    path = _sse_shared_replay_file(channel)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    _trim_shared_sse_events(path)
def _make_event_id(prefix: str = "evt") -> str:
    return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
def _sse_format(event: dict) -> str:
    """Format a full event dict as an SSE message block."""
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )
def _sse_replay_headers(channel: str) -> Dict[str, str]:
    headers = {
        "X-SSE-Channel": channel,
        "X-SSE-Replay-Supported": "true",
        "X-SSE-Replay-Window-Events": str(_MAX_EVENTS),
        "X-SSE-Buffer-Size": str(_MAX_EVENTS),
        "X-SSE-Replay-Store": _sse_replay_store_label(channel),
    }
    resync_routes = _SSE_RESYNC_ROUTES.get(channel, ())
    if resync_routes:
        headers["X-SSE-Resync-Routes"] = ",".join(resync_routes)
    return headers
def _publish_event(buffer: deque, subscribers: list[asyncio.Queue], event_type: str, data: dict) -> str:
    """Publish an event to the buffer and notify all subscribers."""
    event_id = _make_event_id()
    event = SseEventEnvelope[Dict[str, Any]](
        id=event_id,
        type=event_type,
        data=dict(data or {}),
    ).model_dump(mode="json")
    buffer.append((event_id, event))
    _append_shared_sse_event(_sse_channel_for_buffer(buffer), event)
    # Notify subscribers
    for q in list(subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
    return event_id
def _replay_from_events(
    events: list[dict],
    last_event_id: Optional[str],
    *,
    source_label: str,
) -> list[dict]:
    if not last_event_id:
        return list(events)
    found = False
    result: list[dict] = []
    for evt in events:
        eid = evt.get("id")
        if found:
            result.append(evt)
        elif eid == last_event_id:
            found = True
    if not found:
        raise SseReplayUnavailableError(f"Event ID {last_event_id} is no longer in the {source_label}")
    return result
def _replay_from(buffer: deque, last_event_id: Optional[str]) -> list[dict]:
    """Replay events from the buffer starting after last_event_id."""
    return _replay_from_events(
        [evt for _, evt in buffer],
        last_event_id,
        source_label="buffer",
    )
def _replay_from_channel(channel: str, buffer: deque, last_event_id: Optional[str]) -> list[dict]:
    if _sse_shared_replay_enabled() and channel in SSE_CHANNELS:
        return _replay_from_events(
            _read_shared_sse_events(channel),
            last_event_id,
            source_label="replay store",
        )
    return _replay_from(buffer, last_event_id)
async def _sse_stream(
    buffer: deque,
    subscribers: list[asyncio.Queue],
    last_event_id: Optional[str] = None,
    channel: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted events."""
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    subscribers.append(q)
    try:
        # Replay historical events first
        replayed = (
            _replay_from_channel(channel, buffer, last_event_id)
            if channel
            else _replay_from(buffer, last_event_id)
        )
        for evt in replayed:
            yield _sse_format(evt)

        # Then stream new events as they arrive
        while True:
            try:
                evt = await asyncio.wait_for(q.get(), timeout=30.0)
                yield _sse_format(evt)
            except asyncio.TimeoutError:
                # Send a comment to keep the connection alive
                yield ": heartbeat\n\n"
    finally:
        # Unsubscribe on client disconnect
        if q in subscribers:
            subscribers.remove(q)
def _handle_sse_stream(
    channel: str,
    buffer: deque,
    subscribers: list[asyncio.Queue],
    last_event_id: Optional[str],
    extra_headers: Optional[Dict[str, str]] = None,
) -> StreamingResponse:
    """Helper to create a StreamingResponse with replay error handling."""
    try:
        # Check if replay is possible before starting the stream
        _replay_from_channel(channel, buffer, last_event_id)
    except SseReplayUnavailableError as exc:
        error = _bff_error(
            status_code=409,
            code=ErrorCode.RESOURCE_CONFLICT,
            message=str(exc),
            reason="SSE_REPLAY_HISTORY_MISSING",
            suggestion="Resync canonical state via GET routes before reconnecting to the stream",
            details_extra={
                "channel": channel,
                "lastEventId": last_event_id,
                "replaySupported": True,
                "replayWindowEvents": _MAX_EVENTS,
                "replayStore": "in-memory",
                "resyncRoutes": list(_SSE_RESYNC_ROUTES.get(channel, ())),
            },
        )
        error.headers = _sse_replay_headers(channel)
        raise error from exc

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        **_sse_replay_headers(channel),
    }
    if extra_headers:
        headers.update(extra_headers)

    return StreamingResponse(
        _sse_stream(buffer, subscribers, last_event_id, channel),
        media_type="text/event-stream",
        headers=headers,
    )
async def stream_generic_events(
    channel: str,
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF-SSE: Generic Server-Sent Events stream for any channel in the catalog.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    """
    if channel not in SSE_CHANNELS:
        raise _bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            f"Unknown SSE channel: {channel}",
            f"Channel must be one of {sorted(list(SSE_CHANNELS))}",
        )

    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return _handle_sse_stream(channel, _sse_buffers[channel], _sse_subscribers[channel], last_event_id)
_EVOL_EXP_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
def _evol_exp_bff_idempotency_check(
    resolved_key: str,
    request_hash: str,
) -> Optional[Dict[str, Any]]:
    existing = _EVOL_EXP_BFF_IDEMPOTENCY.get(resolved_key)
    if existing is None:
        return None
    if existing.get("request_hash") != request_hash:
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {resolved_key!r} is bound to a different request hash",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        )
    return existing.get("result")
def _evol_exp_bff_action_command(
    entity_type: ObjectType,
    entity_id: str,
    action_id: str,
    resolved_key: str,
    identity: Any,
    payload: Dict[str, Any],
    command_type: CommandType,
) -> Dict[str, Any]:
    request_hash = _stable_json_hash({
        "entity_type": entity_type.value,
        "entity_id": entity_id,
        "action_id": action_id,
        "payload": payload,
    })
    cached = _evol_exp_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    catalog_entry = get_catalog_entry(command_type.value)
    staleness_warning = _check_read_surface_state()
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()
    target = TargetObject(type=entity_type, id=entity_id)
    audit_action = _foundation_audit_for_command_record(
        identity=identity,
        command_type=command_type,
        target_type=entity_type,
        target_id=entity_id,
        payload={"action_id": action_id, **payload},
        reason=str(payload.get("reason") or action_id or command_type.value),
        command_id=command_id,
        idempotency_key=resolved_key,
        route=f"POST /bff/{entity_type.value}/{entity_id}/actions/{action_id}",
        metadata={"action_id": action_id, "catalog_entry": catalog_entry.action_id if catalog_entry else None},
    )
    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "action_id": action_id,
        "preconditions_checked": ["authentication", "authorization", "idempotency"],
        "timestamp": submitted_at,
    }
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=resolved_key,
        operation_type=f"bff.{command_type.value}",
        target_ref=f"{entity_type.value}:{entity_id}",
        request_payload={
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "action_id": action_id,
            "payload": payload,
        },
        trace_id=command_id,
    )
    foundation_ctx = {
        "idempotency_record": idempotency_record.to_dict(),
        "audit_action": audit_action.to_dict(),
    }
    audit_record["foundation"] = foundation_ctx
    command_store.submit_command(
        command_id=command_id,
        command_type=command_type,
        target=target,
        submitted_at=submitted_at,
        params={"action_id": action_id, **payload},
        audit_context=audit_record,
        foundation_context=foundation_ctx,
    )
    result = _project_final_command_response(
        command_id=command_id,
        command=command_type,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )
    _EVOL_EXP_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result
_MCP_SERVER_REGISTRY: Dict[str, Dict[str, Any]] = {}
def _read_store_fixture_records(dataset: str) -> List[Dict[str, Any]]:
    data = getattr(read_store, "_data", {})
    raw = data.get(dataset) if isinstance(data, dict) else None
    if isinstance(raw, dict):
        return [dict(record) for record in raw.values() if isinstance(record, dict)]
    if isinstance(raw, list):
        return [dict(record) for record in raw if isinstance(record, dict)]
    return []
def _merge_registry_records(
    fixture_records: List[Dict[str, Any]],
    registry_records: List[Dict[str, Any]],
    id_keys: tuple[str, ...],
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for record in fixture_records + registry_records:
        record_id = ""
        for key in id_keys:
            value = record.get(key)
            if value not in (None, ""):
                record_id = str(value)
                break
        if record_id:
            merged[record_id] = dict(record)
    return list(merged.values())
def _mcp_server_fixture_records() -> List[Dict[str, Any]]:
    store_records = read_store.list_mcp_servers()
    if store_records:
        return store_records
    return _read_store_fixture_records("mcp_servers")
def _mcp_tool_fixture_records() -> List[Dict[str, Any]]:
    store_records = read_store.list_mcp_tools()
    if store_records:
        return store_records
    return _read_store_fixture_records("mcp_tools")
def _merged_mcp_server_records() -> List[Dict[str, Any]]:
    return _merge_registry_records(
        _mcp_server_fixture_records(),
        [dict(record) for record in _MCP_SERVER_REGISTRY.values()],
        ("server_id", "id"),
    )
def _merged_mcp_tool_records() -> List[Dict[str, Any]]:
    return _merge_registry_records(
        _mcp_tool_fixture_records(),
        [dict(record) for record in _MCP_TOOL_REGISTRY.values()],
        ("tool_id", "id"),
    )
_GOV_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_GOV_BFF_INCIDENT_OVERLAY: Dict[str, Dict[str, Any]] = {}
_ACKNOWLEDGED_ALERTS: Dict[str, Dict[str, Any]] = {}
_INCIDENT_CASE_ALIAS_FIELDS = {
    "binding_id": ("binding_id", "runtime_binding_id"),
    "deployment_stage": ("deployment_stage", "deployment_mode"),
    "deployment_plan_id": ("deployment_plan_id", "plan_id"),
    "capital_pool_id": ("capital_pool_id", "affected_pool_id"),
    "persona_capital_binding_id": ("persona_capital_binding_id",),
    "artifact_id": ("artifact_id",),
    "artifact_version": ("artifact_version",),
    "runtime_id": ("runtime_id",),
    "trace_id": ("trace_id", "correlation_id"),
}
def _first_present(payload: Dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None
def _project_bff_incident_case(incident: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(incident)
    incident_id = str(payload.get("incident_id") or payload.get("id") or "")
    if incident_id:
        payload["id"] = payload.get("id") or incident_id
        payload["incident_id"] = incident_id

    for field, aliases in _INCIDENT_CASE_ALIAS_FIELDS.items():
        value = _first_present(payload, aliases)
        if value is not None:
            payload[field] = value

    created_at = payload.get("created_at") or payload.get("opened_at")
    if created_at:
        payload["created_at"] = created_at
        payload["opened_at"] = payload.get("opened_at") or created_at

    if not payload.get("lineage_ref") and payload.get("artifact_id") and payload.get("artifact_version"):
        payload["lineage_ref"] = f"{payload['artifact_id']}@{payload['artifact_version']}"

    return payload
def _bff_incident_matches_filters(
    incident: Dict[str, Any],
    *,
    status: Optional[str],
    severity: Optional[str],
    affected_pool_id: Optional[str],
) -> bool:
    if status:
        requested_statuses = {token.strip().lower() for token in status.split(",") if token.strip()}
        if str(incident.get("status") or "").lower() not in requested_statuses:
            return False
    if severity and str(incident.get("severity") or "").lower() != severity.lower():
        return False
    if affected_pool_id and (incident.get("capital_pool_id") or incident.get("affected_pool_id")) != affected_pool_id:
        return False
    return True
def _list_bff_incidents(
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    affected_pool_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    incidents = [
        _project_bff_incident_case(incident)
        for incident in read_store.list_incidents(
            status=status,
            severity=severity,
            affected_pool_id=affected_pool_id,
        )
    ]
    seen = {str(item.get("incident_id") or item.get("id") or "") for item in incidents}
    for incident_id, incident in _GOV_BFF_INCIDENT_OVERLAY.items():
        if incident_id in seen:
            continue
        if _bff_incident_matches_filters(
            incident,
            status=status,
            severity=severity,
            affected_pool_id=affected_pool_id,
        ):
            incidents.append(_project_bff_incident_case(incident))
    anchor = [
        incident
        for incident in incidents
        if str(incident.get("incident_id") or incident.get("id") or "") == "inc-20260410-001"
    ]
    rest = [
        incident
        for incident in incidents
        if str(incident.get("incident_id") or incident.get("id") or "") != "inc-20260410-001"
    ]
    return anchor + sorted(
        rest,
        key=lambda item: str(item.get("created_at") or item.get("submitted_at") or ""),
        reverse=True,
    )
def _get_bff_incident(incident_id: str) -> Optional[Dict[str, Any]]:
    incident = read_store.get_incident(incident_id)
    if incident:
        return _project_bff_incident_case(incident)
    overlay = _GOV_BFF_INCIDENT_OVERLAY.get(incident_id)
    return _project_bff_incident_case(overlay) if overlay else None
def _gov_bff_action_command(
    entity_type: ObjectType,
    entity_id: str,
    action_id: str,
    resolved_key: str,
    identity: Any,
    payload: Dict[str, Any],
    command_type: CommandType,
) -> Dict[str, Any]:
    """Submit a governance/risk/incident resource action through the command store."""
    _reject_body_idempotency_key(payload)
    request_hash = _stable_json_hash(
        {"entity_type": entity_type.value, "entity_id": entity_id, "action_id": action_id, "payload": payload}
    )
    if _request_dry_run_requested():
        submitted_at = utc_now()
        command_id = f"dryrun-cmd-{uuid.uuid4().hex[:12]}"
        result = _project_final_command_response(
            command_id=command_id,
            command=command_type,
            accepted_at=submitted_at,
            status=CommandStatus.SUBMITTED,
            staleness_warning=_check_read_surface_state(),
            meta=_command_response_dry_run_meta(resolved_key),
        )
        return result.model_dump(mode="json")
    existing = _GOV_BFF_IDEMPOTENCY.get(resolved_key)
    if existing is not None:
        if existing.get("request_hash") != request_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        return existing["result"]

    staleness_warning = _check_read_surface_state()
    catalog_entry = get_catalog_entry(command_type.value)
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()
    target = TargetObject(type=entity_type, id=entity_id)
    audit_action = _foundation_audit_for_command_record(
        identity=identity,
        command_type=command_type,
        target_type=entity_type,
        target_id=entity_id,
        payload={"action_id": action_id, **payload},
        reason=str(payload.get("reason") or action_id or command_type.value),
        command_id=command_id,
        idempotency_key=resolved_key,
        route=f"POST /bff/{entity_type.value}/{entity_id}/actions/{action_id}",
        metadata={"action_id": action_id, "catalog_entry": catalog_entry.action_id if catalog_entry else None},
    )
    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "action_id": action_id,
        "preconditions_checked": ["authentication", "authorization", "idempotency"],
        "timestamp": submitted_at,
        "idempotency_key": resolved_key,
        "request_hash": request_hash,
        "catalog_entry": catalog_entry.action_id if catalog_entry else None,
    }
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=resolved_key,
        operation_type=f"bff.{command_type.value}",
        target_ref=f"{entity_type.value}:{entity_id}",
        request_payload={
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "action_id": action_id,
            "payload": payload,
        },
        trace_id=command_id,
    )
    foundation_ctx = {
        "idempotency_record": idempotency_record.to_dict(),
        "audit_action": audit_action.to_dict(),
    }
    audit_record["foundation"] = foundation_ctx
    command_store.submit_command(
        command_id=command_id,
        command_type=command_type,
        target=target,
        submitted_at=submitted_at,
        params={"action_id": action_id, **payload},
        audit_context=audit_record,
        foundation_context=foundation_ctx,
    )
    result = _project_final_command_response(
        command_id=command_id,
        command=command_type,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )
    _GOV_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result
_GOV_BFF_JOB_OVERLAY: Dict[str, Dict[str, Any]] = {}
def _research_experiments_surface_source(records: Sequence[Dict[str, Any]]) -> Optional[str]:
    if read_store.dataset_source("research_experiments") != "missing":
        return None
    for record in records:
        if str(record.get("experiment_id") or record.get("id") or "") == "exp-mgmt-qlib-006":
            return "composed_market_persona_defaults"
    return None
def _get_bff_job(job_id: str) -> Optional[Dict[str, Any]]:
    return read_store.get_job_bff(job_id)
def _list_bff_jobs(*, status: Optional[str] = None) -> List[Dict[str, Any]]:
    jobs = read_store.list_jobs_bff()
    if status:
        requested = {s.strip().lower() for s in status.split(",") if s.strip()}
        jobs = [j for j in jobs if str(j.get("status") or "").lower() in requested]
    return sorted(jobs, key=lambda j: str(j.get("created_at") or j.get("submitted_at") or ""), reverse=True)
_FINAL_CONTRACT_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
def _sem_command_payload_from_record(
    record: Dict[str, Any],
    *,
    idempotency_key: str,
    replayed: bool,
) -> Dict[str, Any]:
    command_id = str(record.get("command_id") or "")
    command_type = str(record.get("type") or "")
    receipts = _command_dual_write_receipts(
        command_id=command_id,
        command=command_type,
        status=ActionCommandStatus.ACCEPTED.value,
        accepted_at=str(record.get("submitted_at") or ""),
    )
    receipt = dict(receipts["command_receipt"])
    receipt["id"] = command_id
    return {
        "status": "accepted",
        "data": {
            "status": "accepted",
            "command": command_type,
            "commandId": command_id,
            "command_id": command_id,
            "receipt_id": command_id,
            "receipt": receipt,
            "receipt_dual_write": receipts,
            "action_receipt": receipts["action_receipt"],
            "actionReceipt": receipts["action_receipt"],
            "command_receipt": receipts["command_receipt"],
            "commandReceipt": receipts["command_receipt"],
        },
        "meta": {
            "durable": True,
            "liveCapitalSideEffects": False,
            "idempotency": {
                "key": idempotency_key,
                "idempotencyKey": idempotency_key,
                "replayed": replayed,
            },
        },
    }
def _sem_command_dry_run_payload(
    *,
    command_type: CommandType,
    target_type: ObjectType,
    target_id: str,
    payload: Dict[str, Any],
    identity: OperatorIdentity,
    idempotency_key: str,
) -> Dict[str, Any]:
    submitted_at = utc_now()
    command_id = f"dryrun-cmd-{uuid.uuid4().hex[:12]}"
    receipts = _command_dual_write_receipts(
        command_id=command_id,
        command=command_type.value,
        status=ActionCommandStatus.ACCEPTED.value,
        accepted_at=submitted_at,
    )
    receipt = dict(receipts["command_receipt"])
    receipt["id"] = command_id
    return {
        "status": "accepted",
        "data": {
            "status": "accepted",
            "command": command_type.value,
            "commandId": command_id,
            "command_id": command_id,
            "target": {"type": target_type.value, "id": target_id},
            "params": json.loads(json.dumps(payload)),
            "submitted_by": identity.operator_id,
            "receipt_id": command_id,
            "receipt": receipt,
            "receipt_dual_write": receipts,
            "action_receipt": receipts["action_receipt"],
            "actionReceipt": receipts["action_receipt"],
            "command_receipt": receipts["command_receipt"],
            "commandReceipt": receipts["command_receipt"],
        },
        "meta": {
            "snapshot_at": submitted_at,
            **_command_response_dry_run_meta(idempotency_key),
        },
    }
def _scoped_idempotency_cache_key(idempotency_key: str, operator_id: str) -> str:
    return f"{operator_id}\x00{idempotency_key}"
def _sem_command_response(
    *,
    command_type: CommandType,
    target_type: ObjectType,
    target_id: str,
    payload: Dict[str, Any],
    identity: OperatorIdentity,
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str] = None,
    status_code: int = 202,
    server_generated_target: bool = False,
    trusted_evidence_producer: Optional[str] = None,
    terminal_on_persist: bool = False,
) -> JSONResponse:
    payload = dict(payload or {})
    _reject_body_idempotency_key(payload)
    clean_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    # For routes that generate the target_id server-side (CREATE without a client-supplied id),
    # exclude target_id from the idempotency hash so that retries with the same Idempotency-Key
    # replay correctly rather than conflicting due to a different random id per call.
    hash_body: Dict[str, Any] = {
        "command": command_type.value,
        "target_type": target_type.value,
        "payload": payload,
    }
    if not server_generated_target:
        hash_body["target_id"] = target_id
    request_hash = _stable_json_hash(hash_body)
    cache_key = _scoped_idempotency_cache_key(clean_key, identity.operator_id)
    if _request_dry_run_requested():
        return JSONResponse(
            status_code=200,
            content=_sem_command_dry_run_payload(
                command_type=command_type,
                target_type=target_type,
                target_id=target_id,
                payload=payload,
                identity=identity,
                idempotency_key=clean_key,
            ),
        )
    existing = _FINAL_CONTRACT_IDEMPOTENCY.get(cache_key)
    if existing:
        if existing.get("request_hash") != request_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was reused with a different command payload",
                "The idempotency key already belongs to another command payload",
                precondition_failed="idempotency_key",
            )
        replay = dict(existing["result"])
        replay.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
        return JSONResponse(status_code=status_code, content=replay)
    existing_record = command_store.get_command_by_idempotency_key(
        clean_key,
        operator_id=identity.operator_id,
    )
    if existing_record:
        stored_hash = (existing_record.get("foundation") or {}).get("idempotency_record", {}).get("request_hash")
        if stored_hash and stored_hash != request_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was reused with a different command payload",
                "The idempotency key already belongs to another command payload",
                precondition_failed="idempotency_key",
            )
        response = _sem_command_payload_from_record(existing_record, idempotency_key=clean_key, replayed=True)
        return JSONResponse(status_code=status_code, content=response)

    now = utc_now()
    command_id = f"cmd-{uuid.uuid4().hex[:16]}"
    receipt_dual_write = _command_dual_write_receipts(
        command_id=command_id,
        command=command_type.value,
        status=ActionCommandStatus.ACCEPTED.value,
        accepted_at=now,
    )
    reason = str(payload.get("reason") or command_type.value)
    audit_action = _foundation_audit_for_command_record(
        identity=identity,
        command_type=command_type,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        reason=reason,
        command_id=command_id,
        idempotency_key=clean_key,
        route="POST /bff/semantic-command",
    )
    foundation_ctx = {
        "idempotency_record": {
            "idempotency_key": clean_key,
            "request_hash": request_hash,
            "status": "succeeded",
            "trace_id": audit_action.trace_id,
        },
        "audit_action": audit_action.to_dict(),
    }
    if trusted_evidence_producer:
        foundation_ctx["trusted_evidence_producer"] = trusted_evidence_producer
    audit_context = {
        "actor": identity.operator_id,
        "operator_id": identity.operator_id,
        "reason": reason,
        "live_capital_side_effects": False,
        "receipt_dual_write": receipt_dual_write,
        "foundation": foundation_ctx,
    }
    if trusted_evidence_producer:
        audit_context["trusted_evidence_producer"] = trusted_evidence_producer
    if terminal_on_persist:
        audit_context["execution_completed_at"] = now
        record, active = command_store.submit_terminal_command_if_no_active_target(
            command_id,
            command_type,
            TargetObject(type=target_type, id=target_id),
            now,
            payload,
            audit_context,
            foundation_ctx,
            {
                "command_id": command_id,
                "status": "recorded",
                "recorded_at": now,
            },
        )
    else:
        record, active = command_store.submit_command_if_no_active_target(
            command_id,
            command_type,
            TargetObject(type=target_type, id=target_id),
            now,
            payload,
            audit_context,
            foundation_ctx,
        )
    if active:
        raise _bff_error(
            409,
            ErrorCode.RESOURCE_CONFLICT,
            "A command is already in flight for this target",
            f"Command {active['command_id']} is currently {active['status']}",
            precondition_failed="concurrent_safety",
            suggestion="Wait for the in-flight command to complete or time out before retrying",
        )
    assert record is not None
    result = _sem_command_payload_from_record(record, idempotency_key=clean_key, replayed=False)
    _FINAL_CONTRACT_IDEMPOTENCY[cache_key] = {"request_hash": request_hash, "result": result}
    return JSONResponse(status_code=status_code, content=result)
def _confirm_token_records(token_id: str) -> List[Dict[str, Any]]:
    return [
        record
        for record in command_store._get_all_commands()
        if isinstance(record.get("target"), dict)
        and record["target"].get("type") == ObjectType.CONFIRM_TOKEN.value
        and record["target"].get("id") == token_id
    ]
def _confirm_token_expiry_from_record(record: Dict[str, Any]) -> Optional[datetime]:
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    absolute = params.get("expiresAt") or params.get("expires_at")
    parsed_absolute = _audit_datetime(absolute)
    if parsed_absolute is not None:
        return parsed_absolute

    raw_ttl = params.get("ttlSeconds", params.get("ttl_seconds", params.get("ttl")))
    if raw_ttl in (None, ""):
        return None
    try:
        ttl_seconds = float(raw_ttl)
    except (TypeError, ValueError):
        return None
    submitted_at = _audit_datetime(record.get("submitted_at"))
    if submitted_at is None:
        return None
    return submitted_at + timedelta(seconds=ttl_seconds)
def _guarded_command_confirm_token_id(record: Dict[str, Any]) -> Optional[str]:
    entry = get_catalog_entry(str(record.get("type") or ""))
    if entry is None or not getattr(entry, "requires_confirm_token", False):
        return None
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    evidence = (
        audit.get("precondition_evidence")
        if isinstance(audit.get("precondition_evidence"), dict)
        else {}
    )
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    token_id = str(
        evidence.get("confirm_token_id")
        or params.get("confirm_token_id")
        or ""
    ).strip()
    return token_id or None
def _confirm_token_lifecycle_payload(token_id: str) -> Dict[str, Any]:
    status = "available"
    expires_at: Optional[datetime] = None
    latest_record: Optional[Dict[str, Any]] = None
    for record in command_store._get_all_commands():
        target = record.get("target") if isinstance(record.get("target"), dict) else {}
        if (
            target.get("type") == ObjectType.CONFIRM_TOKEN.value
            and target.get("id") == token_id
        ):
            record_type = record.get("type")
            if record_type == CommandType.CONFIRM_TOKEN_CREATE.value:
                status = "created"
                expires_at = _confirm_token_expiry_from_record(record)
            elif record_type == CommandType.CONFIRM_TOKEN_REDEEM.value:
                status = "redeemed"
            elif record_type == CommandType.CONFIRM_TOKEN_DELETE.value:
                status = "deleted"
            latest_record = record
            continue

        # Before automatic redemption existed, guarded admissions persisted the
        # validated token id on the command/audit record but did not append a
        # RedeemConfirmToken record.  Treat that durable admission as consumed
        # so an upgrade cannot grant the same token one additional use.
        if (
            status == "created"
            and _guarded_command_confirm_token_id(record) == token_id
        ):
            status = "redeemed"
            latest_record = record

    expired = False
    if expires_at is not None and status == "created":
        expired = expires_at <= datetime.now(timezone.utc)
        if expired:
            status = "expired"

    payload: Dict[str, Any] = {
        "id": token_id,
        "tokenId": token_id,
        "status": status,
        "expired": expired,
    }
    if expires_at is not None:
        payload["expiresAt"] = expires_at.isoformat().replace("+00:00", "Z")
        payload["expires_at"] = payload["expiresAt"]
    if latest_record is not None:
        payload["commandId"] = latest_record.get("command_id")
        payload["command_id"] = latest_record.get("command_id")
    return payload
def _bff_source_commit() -> str:
    commit = os.environ.get("BFF_COMMIT") or os.environ.get("GIT_SHA")
    if not commit or commit == "unknown":
        git_dir = "/workspace/status-root/.git"
        if os.path.exists(git_dir):
            try:
                head_path = os.path.join(git_dir, "HEAD")
                if os.path.exists(head_path):
                    with open(head_path, "r") as f:
                        ref = f.read().strip()
                    if ref.startswith("ref: "):
                        ref_path = os.path.join(git_dir, ref[5:])
                        if os.path.exists(ref_path):
                            with open(ref_path, "r") as f:
                                commit = f.read().strip()
                        else:
                            packed_path = os.path.join(git_dir, "packed-refs")
                            if os.path.exists(packed_path):
                                ref_name = ref[5:]
                                with open(packed_path, "r") as f:
                                    for line in f:
                                        if line.startswith("#") or not line.strip():
                                            continue
                                        parts = line.strip().split()
                                        if len(parts) == 2 and parts[1] == ref_name:
                                            commit = parts[0]
                                            break
                    else:
                        commit = ref
            except Exception:
                pass
    return str(commit or "unknown")
async def sem_bff_version():
    commit = _bff_source_commit()
    image_digest = os.getenv("BFF_IMAGE_DIGEST") or os.getenv("IMAGE_DIGEST") or "unknown"
    build_time = os.getenv("BFF_BUILD_TIME") or os.getenv("BUILD_TIME") or "unknown"
    environment = os.getenv("PANTHEON_ENV") or os.getenv("ENVIRONMENT") or "unknown"

    config_posture = {
        "auth_stub": _bff_auth_stub_enabled(),
        "auth_mode": _bff_auth_mode(),
        "dev_login_enabled": _dev_login_enabled(),
        "mfa_required": _bool_from_env("PANTHEON_BFF_MFA_REQUIRED", default=False),
        "assistant_kernel_enabled": _bool_from_env("PANTHEON_ASSISTANT_KERNEL_ENABLED", default=False),
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
        "image_digest": image_digest,
        "build_time": build_time,
        "environment": environment,
        "config_posture": config_posture,
    }
def _sem_bff_health_payload() -> Dict[str, Any]:
    commit = _bff_source_commit()
    payload = health_payload(
        "operator-bff",
        dependencies=_bff_readiness_dependencies,
        details={"version": "0.2.0", "data_dir": BFF_DATA_DIR},
    )
    payload.update(
        {
            "version": "0.2.0",
            "commit": commit,
            "source_commit_sha": commit,
        }
    )
    return payload
async def sem_bff_health_alias():
    return _sem_bff_health_payload()
async def sem_bff_readiness_alias():
    payload = _sem_bff_health_payload()
    return JSONResponse(payload, status_code=readiness_status_code(payload))
async def sem_bff_capabilities(authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return {
        "data": {
            "feature_flags": {
                "executePlansBff": True,
                "sessionAuthMe": True,
                "oodaPackets": _ooda_packet_routes_enabled(),
                "synthesisConflictLogs": _synthesis_conflict_log_routes_enabled(),
            }
        },
        "meta": {"snapshot_at": utc_now()},
    }
def _sem_final_registry_meta(surface_key: str, *, snapshot_at: Optional[str] = None, total: Optional[int] = None) -> Dict[str, Any]:
    snapshot_at = snapshot_at or utc_now()
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {surface_key: {"status": "ok", "source": "bff_local_registry"}},
    }
    if total is not None:
        meta["total"] = total
    return meta
def _sem_final_list_response(
    items: List[Dict[str, Any]],
    *,
    dataset: str,
    surface_key: str,
    source: Optional[str] = None,
    surface: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    snapshot_at = utc_now()
    if source == "bff_local_registry":
        meta = _sem_final_registry_meta(surface_key, snapshot_at=snapshot_at, total=len(items))
    else:
        surface = surface or _dataset_surface_status(dataset, snapshot_at=snapshot_at, source=source)
        meta = {
            "snapshot_at": snapshot_at,
            "surfaces": {surface_key: surface},
            "total": len(items),
        }
        reason = _surface_degradation_reason(
            surface,
            degraded_reason=f"{surface_key.replace('_', ' ')} is degraded and may be stale.",
            unavailable_reason=f"{surface_key.replace('_', ' ')} is currently unavailable.",
        )
        if reason is not None:
            meta["degradation"] = {"reason": reason}
    return {
        "data": items,
        "items": items,
        "page_info": {"next_page_token": None, "total": len(items)},
        "meta": meta,
    }
def _sem_final_mcp_tool_records() -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for record in _merged_mcp_tool_records():
        tool_id = str(record.get("tool_id") or record.get("id") or "").strip()
        if not tool_id:
            continue
        records.append(
            {
                "id": tool_id,
                "tool_id": tool_id,
                "server_id": record.get("server_id"),
                "name": record.get("name") or tool_id,
                "status": record.get("status") or "imported",
                "tool_class": record.get("tool_class") or "",
                "schema_url": record.get("schema_url"),
                "action_count": record.get("action_count", 0),
            }
        )
    return sorted(records, key=lambda item: (str(item.get("server_id") or ""), str(item.get("tool_id") or "")))
def _sem_final_channel_records() -> List[Dict[str, Any]]:
    return [
        {
            "id": channel,
            "channel_id": channel,
            "name": channel,
            "status": "active",
            "replay_supported": channel in _SSE_RESYNC_ROUTES,
            "resync_routes": list(_SSE_RESYNC_ROUTES.get(channel, ())),
        }
        for channel in SSE_CHANNEL_CATALOG
    ]
_OODA_STAGE_DEFS = [
    ("observe", "Observe", "telemetry/source/search health"),
    ("orient", "Orient", "active signal/persona proposal count"),
    ("decide", "Decide", "pending approvals/interventions"),
    ("act", "Act", "paper runtime / sandbox broker state"),
    ("learn", "Learn", "evolution/postmortem/retrain state"),
]
_OODA_STAGE_STATUSES: Dict[str, List[str]] = {
    "observe": ["open", "observing"],
    "orient": ["oriented"],
    "decide": ["decided"],
    "act": ["acted"],
    "learn": ["evolving"],
}
def _build_ooda_control_room_status_card(snapshot_at: str) -> Dict[str, Any]:
    """Return the OODA stage summary card for the Control Room.

    Gated by PANTHEON_OODA_PACKET_ENABLED. Returns a fail-closed card when
    disabled. Each stage card carries an active_count (open loops at that
    stage) and a direct link to the filtered packet list.
    """
    if not _ooda_packet_routes_enabled():
        return {
            "enabled": False,
            "gate_state": "fail_closed",
            "open_loop_count": 0,
            "closed_loop_count": 0,
            "failed_loop_count": 0,
            "total_packet_count": 0,
            "stages": {
                stage: {
                    "label": label,
                    "description": desc,
                    "status": "fail_closed",
                    "active_count": 0,
                    "detail_link": f"/bff/ooda/packets?stage={stage}",
                }
                for stage, label, desc in _OODA_STAGE_DEFS
            },
            "live_capital_side_effects": False,
            "fail_closed_gate_posture": "fail_closed",
            "meta": {
                "snapshot_at": snapshot_at,
                "source": "fail_closed",
                "status": "fail_closed",
                "surface_key": "ooda_control_room_status",
            },
        }

    packets = read_store.list_ooda_packets()
    ooda_src = read_store.dataset_source("ooda_packets")

    open_statuses = {"open", "observing", "oriented", "decided", "acted", "evolving"}
    open_count = sum(
        1 for p in packets if str(p.get("status") or "").lower() in open_statuses
    )
    closed_count = sum(
        1 for p in packets if str(p.get("status") or "").lower() == "closed"
    )
    failed_count = sum(
        1 for p in packets if str(p.get("status") or "").lower() == "failed"
    )

    stage_counts: Dict[str, int] = {
        stage: sum(
            1
            for p in packets
            if str(p.get("status") or "").lower() in status_vals
        )
        for stage, status_vals in _OODA_STAGE_STATUSES.items()
    }

    # Safety assertion: no pre-activation packet should carry live capital side effects
    live_side_effects_detected = any(
        p.get("act", {}).get("live_capital_side_effects", False) is True
        for p in packets
        if str(p.get("environment") or "").lower() != "live"
    )

    if ooda_src in (None, "missing") and packets:
        ooda_src = "composed_market_persona_defaults"
    surface_status = "ok" if ooda_src not in (None, "missing") else "unavailable"
    # Propagate an unavailable backing source into the per-stage cards so the
    # card body cannot report all-green while meta.status says "unavailable".
    # A present-but-empty source (0 packets) stays "ok" with active_count 0.
    stage_status = surface_status

    return {
        "enabled": True,
        "gate_state": "enabled",
        "open_loop_count": open_count,
        "closed_loop_count": closed_count,
        "failed_loop_count": failed_count,
        "total_packet_count": len(packets),
        "stages": {
            stage: {
                "label": label,
                "description": desc,
                "status": stage_status,
                "active_count": stage_counts[stage],
                "detail_link": f"/bff/ooda/packets?stage={stage}",
            }
            for stage, label, desc in _OODA_STAGE_DEFS
        },
        "live_capital_side_effects": live_side_effects_detected,
        "fail_closed_gate_posture": "fail_closed",
        "meta": {
            "snapshot_at": snapshot_at,
            "source": ooda_src if ooda_src else "missing",
            "status": surface_status,
            "surface_key": "ooda_control_room_status",
        },
    }
def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
def _persona_id(record: Dict[str, Any]) -> str:
    return str(record.get("persona_id") or record.get("id") or "").strip()
def _first_binding_for_persona(
    persona_id: str,
    *,
    include_market_persona_defaults: bool = False,
) -> Optional[Dict[str, Any]]:
    if include_market_persona_defaults:
        bindings = read_store.list_bindings(
            persona_id=persona_id,
            include_market_persona_defaults=True,
        )
    else:
        bindings = read_store.get_bindings_for_persona(persona_id)
    if not bindings:
        return None
    active = [
        binding
        for binding in bindings
        if str(binding.get("status") or binding.get("validity") or "").lower()
        in {"active", "ready", "bound"}
    ]
    return active[0] if active else bindings[0]
def _runtime_for_pool(
    pool_id: Optional[str],
    *,
    include_market_persona_defaults: bool = False,
) -> Optional[Dict[str, Any]]:
    if not pool_id:
        return None
    for runtime in read_store.list_runtime_bindings(
        include_market_persona_defaults=include_market_persona_defaults,
    ):
        if str(runtime.get("capital_pool_id") or "") == str(pool_id):
            return runtime
    return None
def _persona_health_status(
    *,
    lifecycle_state: str,
    league_entry: Dict[str, Any],
    risk_flags: List[str],
) -> str:
    league_status = str(league_entry.get("status") or "").strip().lower()
    if league_status in {"critical", "frozen", "halted"}:
        return "critical"
    if int(league_entry.get("metrics", {}).get("violation_count") or 0) > 0:
        return "critical"
    if risk_flags or league_status in {"needs_human_approval", "degraded", "under_review"}:
        return "degraded"
    if lifecycle_state in {"frozen", "retired"}:
        return "critical"
    return "healthy"
def _management_fleet_ooda_label(value: Any) -> str:
    stage = str(value or "").strip().lower()
    return {
        "observe": "Observe",
        "oriented": "Orient",
        "orient": "Orient",
        "decided": "Decide",
        "decide": "Decide",
        "acted": "Act",
        "act": "Act",
    }.get(stage, "Observe")
def _management_fleet_autonomy(
    *,
    deployment_stage: str,
    governance_required: bool,
    human_needed: bool,
) -> str:
    stage = str(deployment_stage or "").strip().lower()
    if human_needed or governance_required:
        return "supervised"
    if stage == "live":
        return "autonomous"
    return "manual"
def _trading_performance_delta() -> Optional[float]:
    """Return no delta until telemetry defines a canonical trading-return field."""

    return None
_SOURCE_HEALTH_OVERLAY_CACHE: Dict[str, Any] = {"at": 0.0, "by_connector": None}
_SOURCE_HEALTH_OVERLAY_TTL = 60.0
_SOURCE_PROVIDER_CONNECTOR_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    "finmind": (
        "tw-finmind-datasets",
        "tw-finmind-broker-daily-report",
        "tw-finmind-broker-bulk-parquet",
    ),
    "twse": ("tw-twse-tpex-official-market",),
    "tpex": ("tw-twse-tpex-official-market",),
    "mops": ("tw-mops-official-disclosures",),
    # US research sources (SRCLIVE-005). The Yahoo chart connector was removed:
    # its terms forbid programmatic access, so no ingest path may resolve to it.
    "stooq": ("us-stooq-daily-ohlcv",),
    "sec_edgar": ("us-sec-edgar-filings",),
    "finra": ("us-finra-short-sale",),
    "fred": ("us-fred-macro",),
    "polygon": ("us-polygon-daily-ohlcv",),
    "alphavantage": ("us-alpha-vantage-daily-ohlcv",),
    # Crypto sources (SRCLIVE-003)
    "coingecko": ("crypto-coingecko-spot",),
}
def _source_ingest_truth_by_connector() -> Dict[str, Dict[str, Any]]:
    now = time.monotonic()
    cached = _SOURCE_HEALTH_OVERLAY_CACHE.get("truth_by_connector")
    if cached is not None and (now - float(_SOURCE_HEALTH_OVERLAY_CACHE.get("at") or 0.0)) < _SOURCE_HEALTH_OVERLAY_TTL:
        return cached

    truth: Dict[str, Dict[str, Any]] = {}
    try:
        registry = read_store.get_source_connector_registry()
        for connector in (registry.get("connectors") or []):
            if not isinstance(connector, dict):
                continue
            connector_id = str(connector.get("connector_id") or "").strip()
            if connector_id:
                truth.setdefault(connector_id, {})["connector"] = json.loads(json.dumps(connector))
    except Exception:  # read-only enrichment must never break persona surfaces
        pass

    try:
        snapshot = read_store.get_source_health_usage_snapshot()
        for source in (snapshot.get("sources") or []):
            if not isinstance(source, dict):
                continue
            health = source.get("health") if isinstance(source.get("health"), dict) else {}
            connector_id = str(health.get("source_id") or "").strip()
            if connector_id:
                truth.setdefault(connector_id, {})["health"] = json.loads(json.dumps(health))
                truth[connector_id]["usage_aggregate_30d"] = json.loads(
                    json.dumps(source.get("usage_aggregate_30d") or {})
                )
                if source.get("recommendation") is not None:
                    truth[connector_id]["recommendation"] = json.loads(json.dumps(source.get("recommendation")))
    except Exception:  # read-only enrichment must never break persona surfaces
        pass

    _SOURCE_HEALTH_OVERLAY_CACHE["at"] = now
    _SOURCE_HEALTH_OVERLAY_CACHE["truth_by_connector"] = truth
    _SOURCE_HEALTH_OVERLAY_CACHE["by_connector"] = {
        connector_id: payload["health"]
        for connector_id, payload in truth.items()
        if isinstance(payload.get("health"), dict)
    }
    return truth
def _connector_candidates_for_provider(source: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []
    for key in ("connector_id", "connectorId", "source_id", "sourceId"):
        value = str(source.get(key) or "").strip()
        if value:
            candidates.append(value)
    provider_key = str(source.get("provider_key") or source.get("providerKey") or "").strip().lower()
    candidates.extend(_SOURCE_PROVIDER_CONNECTOR_CANDIDATES.get(provider_key, ()))
    return list(dict.fromkeys(candidates))
def _source_failure_reason(health: Dict[str, Any], connector: Dict[str, Any]) -> Optional[str]:
    metadata = health.get("metadata") if isinstance(health.get("metadata"), dict) else {}
    health_metrics = connector.get("health_metrics") if isinstance(connector.get("health_metrics"), dict) else {}
    state = connector.get("state") if isinstance(connector.get("state"), dict) else {}
    for candidate in (
        metadata.get("source_error"),
        metadata.get("last_failure_error"),
        health_metrics.get("source_error"),
        state.get("last_error"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return None
def _provider_status_from_truth(health: Dict[str, Any], connector: Dict[str, Any]) -> str:
    status = str(health.get("status") or "").strip().lower()
    if status:
        return "read_ok" if status == "ok" else f"source_health_{status}"
    freshness = connector.get("freshness") if isinstance(connector.get("freshness"), dict) else {}
    freshness_status = str(freshness.get("status") or "").strip().lower()
    if freshness_status:
        return f"connector_{freshness_status}"
    return "connector_configured_no_health"
def _source_truth_projection(connector_id: str, truth: Dict[str, Any]) -> Dict[str, Any]:
    health = truth.get("health") if isinstance(truth.get("health"), dict) else {}
    connector = truth.get("connector") if isinstance(truth.get("connector"), dict) else {}
    schedule = connector.get("schedule") if isinstance(connector.get("schedule"), dict) else {}
    freshness = connector.get("freshness") if isinstance(connector.get("freshness"), dict) else {}
    latest_run = freshness.get("latest_run") if isinstance(freshness.get("latest_run"), dict) else {}
    health_metrics = connector.get("health_metrics") if isinstance(connector.get("health_metrics"), dict) else {}
    status = _provider_status_from_truth(health, connector)
    last_fetch_at = (
        latest_run.get("finished_at")
        or latest_run.get("started_at")
        or health.get("last_failure_at")
        or health.get("last_success_at")
        or freshness.get("last_success_at")
    )
    last_push_at = (
        health.get("last_success_at")
        or health_metrics.get("last_success_at")
        or freshness.get("last_success_at")
    )
    failure_reason = _source_failure_reason(health, connector)
    projection = {
        "schema_version": "bff_source_health_truth.v1",
        "connector_id": connector_id,
        "connectorId": connector_id,
        "health_source": "source_ingest",
        "healthSource": "source_ingest",
        "static_label": False,
        "staticLabel": False,
        "source_health_available": bool(health),
        "sourceHealthAvailable": bool(health),
        "health_status": health.get("status"),
        "healthStatus": health.get("status"),
        "connector_status": connector.get("status"),
        "connectorStatus": connector.get("status"),
        "status": status,
        "last_success_at": health.get("last_success_at"),
        "lastSuccessAt": health.get("last_success_at"),
        "last_failure_at": health.get("last_failure_at"),
        "lastFailureAt": health.get("last_failure_at"),
        "last_fetch_at": last_fetch_at,
        "lastFetchAt": last_fetch_at,
        "last_push_at": last_push_at,
        "lastPushAt": last_push_at,
        "failure_reason": failure_reason,
        "failureReason": failure_reason,
        "latest_watermark": health.get("latest_watermark") or freshness.get("last_watermark"),
        "latestWatermark": health.get("latest_watermark") or freshness.get("last_watermark"),
        "row_count_last_run": health.get("row_count_last_run"),
        "rowCountLastRun": health.get("row_count_last_run"),
        "rejected_count_last_run": health.get("rejected_count_last_run"),
        "rejectedCountLastRun": health.get("rejected_count_last_run"),
        "connector_schedule": json.loads(json.dumps(schedule)),
        "connectorSchedule": json.loads(json.dumps(schedule)),
        "connector_freshness": json.loads(json.dumps(freshness)),
        "connectorFreshness": json.loads(json.dumps(freshness)),
        "source_health": json.loads(json.dumps(health)),
        "sourceHealth": json.loads(json.dumps(health)),
    }
    if isinstance(truth.get("usage_aggregate_30d"), dict):
        projection["usage_aggregate_30d"] = json.loads(json.dumps(truth["usage_aggregate_30d"]))
        projection["usageAggregate30d"] = json.loads(json.dumps(truth["usage_aggregate_30d"]))
    if truth.get("recommendation") is not None:
        projection["recommendation"] = json.loads(json.dumps(truth["recommendation"]))
    return projection
def _select_source_truth(
    candidate_ids: List[str],
    truth_by_connector: Dict[str, Dict[str, Any]],
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    for connector_id in candidate_ids:
        truth = truth_by_connector.get(connector_id)
        if isinstance(truth, dict) and (truth.get("health") or truth.get("connector")):
            return connector_id, truth
    return None, None
def _source_health_bindings_from_requirements(
    required_data_sources: List[Dict[str, Any]],
    truth_by_connector: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    bindings: List[Dict[str, Any]] = []
    for requirement in required_data_sources:
        if not isinstance(requirement, dict):
            continue
        candidates = [
            str(candidate).strip()
            for candidate in (requirement.get("connector_candidates") or [])
            if str(candidate).strip()
        ]
        connector_id, truth = _select_source_truth(candidates, truth_by_connector)
        binding = {
            "dataset": requirement.get("dataset"),
            "market": requirement.get("market"),
            "cadence": requirement.get("cadence"),
            "source_class": requirement.get("source_class"),
            "sourceClass": requirement.get("source_class"),
            "connector_candidates": candidates,
            "connectorCandidates": candidates,
            "selected_connector_id": connector_id,
            "selectedConnectorId": connector_id,
            "health_source": "source_ingest" if truth else "unbound",
            "healthSource": "source_ingest" if truth else "unbound",
            "source_health_available": bool(truth and truth.get("health")),
            "sourceHealthAvailable": bool(truth and truth.get("health")),
        }
        if truth and connector_id:
            binding.update(_source_truth_projection(connector_id, truth))
        elif str(requirement.get("source_class") or "") == "seed_only":
            binding["health_source"] = "seed_only_not_live_binding"
            binding["healthSource"] = "seed_only_not_live_binding"
        bindings.append(binding)
    return bindings
def _data_source_ok_tone(value: Any) -> bool:
    token = str(value or "").strip().lower()
    return any(marker in token for marker in ("read_ok", "readback_ok", "smoke_ok"))
def _upgrade_all_green_data_source_state(dss: Dict[str, Any]) -> None:
    provider_statuses = dss.get("provider_statuses")
    if not isinstance(provider_statuses, dict) or not provider_statuses:
        return
    if _data_source_ok_tone(dss.get("state")):
        return
    if not all(_data_source_ok_tone(status) for status in provider_statuses.values()):
        return

    provider_count = len(provider_statuses)
    dss["state"] = "live_readback_ok"
    dss["summary"] = (
        f"All declared data-source providers ({provider_count}/{provider_count}) "
        "report readback OK after live source-health overlay."
    )
def _overlay_source_health_truth(
    data_source_status: Any,
    data_sources: Any,
    *,
    required_data_sources: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    dss = json.loads(json.dumps(data_source_status)) if isinstance(data_source_status, dict) else {}
    srcs = json.loads(json.dumps(data_sources)) if isinstance(data_sources, list) else []
    truth_by_connector = _source_ingest_truth_by_connector()
    provider_statuses = dss.get("provider_statuses")
    if not isinstance(provider_statuses, dict):
        provider_statuses = {}
        dss["provider_statuses"] = provider_statuses

    connector_health: List[Dict[str, Any]] = []
    live_connector_ids: List[str] = []
    static_source_labels: List[str] = []
    for source in srcs:
        if not isinstance(source, dict):
            continue
        provider_key = str(source.get("provider_key") or source.get("providerKey") or "").strip()
        connector_id, truth = _select_source_truth(
            _connector_candidates_for_provider(source),
            truth_by_connector,
        )
        if connector_id and truth:
            projection = _source_truth_projection(connector_id, truth)
            has_live_health = bool(projection.get("source_health_available"))
            original_status = source.get("status")
            original_reason = source.get("reason")
            original_secret_ref = source.get("secret_ref")
            source.update(projection)
            if not has_live_health:
                # Registry entry present but health-usage-snapshot has no live health;
                # preserve the honest static defaults so read_unavailable /
                # credential_unavailable are not silently overwritten.
                if original_status:
                    source["status"] = original_status
                if original_reason is not None:
                    source["reason"] = original_reason
                if original_secret_ref is not None:
                    source["secret_ref"] = original_secret_ref
            elif original_status == "credential_unavailable":
                # credential_unavailable is only upgraded when source-ingest confirms
                # health.status=ok.  A degraded/failed health snapshot (e.g. missing
                # API key reported by source-ingest) must NOT silently flip the status
                # to source_health_degraded — the operator must see credential_unavailable
                # with the secret_ref until the key is present and health is green.
                if str(projection.get("health_status") or "").strip().lower() != "ok":
                    source["status"] = original_status
                    if original_reason is not None:
                        source["reason"] = original_reason
                    if original_secret_ref is not None:
                        source["secret_ref"] = original_secret_ref
            if provider_key:
                provider_statuses[provider_key] = source["status"]
            if has_live_health:
                connector_health.append(projection)
                live_connector_ids.append(connector_id)
        else:
            source.setdefault("health_source", "static_metadata")
            source.setdefault("healthSource", "static_metadata")
            source.setdefault("static_label", True)
            source.setdefault("staticLabel", True)
            if provider_key in _SOURCE_PROVIDER_CONNECTOR_CANDIDATES:
                static_source_labels.append(provider_key)

    bindings = _source_health_bindings_from_requirements(required_data_sources or [], truth_by_connector)
    has_live_truth = bool(connector_health) or any(binding.get("health_source") == "source_ingest" for binding in bindings)
    dss["source_health_source"] = "source_ingest" if has_live_truth else "static_metadata"
    dss["sourceHealthSource"] = dss["source_health_source"]
    dss["live_ingestion_enabled"] = bool(has_live_truth)
    dss["connector_health"] = json.loads(json.dumps(connector_health))
    dss["connectorHealth"] = json.loads(json.dumps(connector_health))
    dss["live_source_connector_ids"] = list(dict.fromkeys(live_connector_ids))
    dss["liveSourceConnectorIds"] = dss["live_source_connector_ids"]
    dss["static_source_labels"] = sorted(set(static_source_labels))
    dss["staticSourceLabels"] = dss["static_source_labels"]
    dss["required_source_health"] = json.loads(json.dumps(bindings))
    dss["requiredSourceHealth"] = json.loads(json.dumps(bindings))
    _upgrade_all_green_data_source_state(dss)
    return dss, srcs, bindings
_PERSONA_FLEET_CONTEXT_METADATA_KEYS = (
    "market_scope",
    "asset_classes",
    "data_source_status",
    "data_sources",
    "data_source_refs",
    "research_status",
    "research_refs",
    "current_research_projects",
)
def _persona_fleet_context_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False
def _persona_fleet_market_key(persona: Dict[str, Any], metadata: Dict[str, Any]) -> Optional[str]:
    for value in (
        metadata.get("market"),
        persona.get("market"),
        persona.get("market_scope"),
        metadata.get("market_scope"),
    ):
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            normalized = str(candidate or "").strip().upper()
            if normalized in {"US", "TW", "CRYPTO"}:
                return normalized

    asset_classes = {
        str(value or "").strip().lower()
        for value in (metadata.get("asset_classes") or persona.get("asset_classes") or [])
    }
    if "crypto" in asset_classes:
        return "CRYPTO"

    broker_adapter = str(metadata.get("broker_adapter") or persona.get("broker_adapter") or "").lower()
    if "shioaji" in broker_adapter:
        return "TW"
    if "kraken" in broker_adapter or "crypto" in broker_adapter:
        return "CRYPTO"
    if "ibkr" in broker_adapter:
        return "US"

    name = str(persona.get("name") or persona.get("persona_name") or persona.get("id") or "").upper()
    if name.startswith("CRYPTO") or "BTC" in name:
        return "CRYPTO"
    if name.startswith("TW") or "TAIWAN" in name:
        return "TW"
    if name.startswith("US") or "U.S." in name or "UNITED STATES" in name:
        return "US"
    return None
def _persona_fleet_context_defaults_by_market(
    candidates: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    defaults: Dict[str, Dict[str, Any]] = {}
    persona_candidates = (
        candidates
        if candidates is not None
        else read_store.list_personas(include_market_persona_defaults=True)
    )
    for candidate in persona_candidates:
        if not isinstance(candidate, dict):
            continue
        metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
        if not (
            isinstance(metadata.get("data_source_status"), dict)
            and metadata.get("data_source_status")
            and isinstance(metadata.get("current_research_projects"), list)
            and metadata.get("current_research_projects")
        ):
            continue
        market = _persona_fleet_market_key(candidate, metadata)
        if market and market not in defaults:
            defaults[market] = {
                "persona": json.loads(json.dumps(candidate)),
                "metadata": json.loads(json.dumps(metadata)),
            }
    return defaults
def _persona_fleet_context_overlay(
    persona: Dict[str, Any],
    metadata: Dict[str, Any],
    defaults_by_market: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    market = _persona_fleet_market_key(persona, metadata)
    default_context = defaults_by_market.get(market or "")
    if not default_context:
        return metadata, {}

    default_metadata = default_context.get("metadata") if isinstance(default_context.get("metadata"), dict) else {}
    context_metadata = json.loads(json.dumps(metadata))
    for key in _PERSONA_FLEET_CONTEXT_METADATA_KEYS:
        if _persona_fleet_context_missing(context_metadata.get(key)) and not _persona_fleet_context_missing(default_metadata.get(key)):
            context_metadata[key] = json.loads(json.dumps(default_metadata[key]))
    return context_metadata, default_context.get("persona") if isinstance(default_context.get("persona"), dict) else {}
_PERSONA_FLEET_INVALID_MUTATION_IDS = {
    "",
    "n/a",
    "na",
    "nan",
    "none",
    "null",
    "undefined",
}
_PERSONA_FLEET_DATE_MUTATION_ID = re.compile(
    r"^\d{4}[-/]\d{2}[-/]\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)?$"
)
def _persona_fleet_mutation_id(value: Any) -> Optional[str]:
    candidate = str(value or "").strip()
    if candidate.lower() in _PERSONA_FLEET_INVALID_MUTATION_IDS:
        return None
    if _PERSONA_FLEET_DATE_MUTATION_ID.fullmatch(candidate):
        return None
    return candidate
def _persona_fleet_mutation_projection(
    *,
    persona_id: str,
    updated_at: Any,
    evolution_decisions: Sequence[Dict[str, Any]],
    artifact_ids: Set[str],
    incident_ids: Set[str],
) -> Dict[str, Any]:
    matched: List[Tuple[Dict[str, Any], str]] = []
    for decision in evolution_decisions:
        targets_persona = str(decision.get("target_id") or "").strip() == persona_id
        targets_artifact = str(decision.get("artifact_id") or "").strip() in artifact_ids
        targets_incident = (
            str(decision.get("incident_ref") or decision.get("linked_incident_id") or "").strip()
            in incident_ids
        )
        if not (targets_persona or targets_artifact or targets_incident):
            continue
        decision_id = _persona_fleet_mutation_id(decision.get("decision_id") or decision.get("id"))
        if decision_id:
            matched.append((decision, decision_id))

    ordered = _sort_records_latest_first(
        [decision for decision, _ in matched],
        ("updated_at", "created_at", "occurred_at"),
    )
    decision_ids = {id(decision): decision_id for decision, decision_id in matched}

    if ordered:
        latest = ordered[0]
        decision_id = decision_ids[id(latest)]
        changed_at = (
            latest.get("updated_at")
            or latest.get("created_at")
            or latest.get("occurred_at")
            or updated_at
        )
        label = str(changed_at)[:10] if changed_at else None
        href = (
            "/management/evolution-journal"
            f"?persona={quote(persona_id, safe='')}"
            f"&mutation_review={quote(decision_id, safe='')}"
        )
        kind = "formal_mutation"
        confidence = "formal"
        diagnostics: List[str] = []
    elif updated_at:
        decision_id = None
        changed_at = updated_at
        label = str(updated_at)[:10]
        href = (
            "/management/evolution-journal"
            f"?persona={quote(persona_id, safe='')}&source=fleet_summary"
        )
        kind = "fleet_summary"
        confidence = "fallback"
        diagnostics = ["No formal mutation entry id declared for this persona row."]
    else:
        decision_id = None
        changed_at = None
        label = None
        href = None
        kind = "unavailable"
        confidence = "unavailable"
        diagnostics = ["No recent-change data or fleet summary available for this persona."]

    return {
        "last_mutation_label": label,
        "lastMutationLabel": label,
        "last_mutation_at": changed_at,
        "lastMutationAt": changed_at,
        "last_mutation_kind": kind,
        "lastMutationKind": kind,
        "mutation_entry_id": decision_id,
        "mutationEntryId": decision_id,
        "evolution_entry_id": decision_id,
        "evolutionEntryId": decision_id,
        "evolution_href": href,
        "evolutionHref": href,
        "mutation_confidence": confidence,
        "mutationConfidence": confidence,
        "mutation_diagnostics": diagnostics,
        "mutationDiagnostics": diagnostics,
    }
def _build_persona_health_items(
    snapshot_at: str,
    *,
    include_market_persona_defaults: bool = False,
) -> List[Dict[str, Any]]:
    league_by_persona = {
        str(item.get("persona_id") or item.get("id") or ""): item
        for item in read_store.list_persona_league(
            include_market_persona_defaults=include_market_persona_defaults,
        )
    }
    context_defaults = (
        _persona_fleet_context_defaults_by_market()
        if include_market_persona_defaults
        else {}
    )
    incidents_list = list(read_store.list_incidents() or [])
    all_decisions = list(read_store.list_evolution_decisions() or [])
    all_telemetry = list(read_store.list_telemetry_summaries() or [])
    items: List[Dict[str, Any]] = []
    for persona in read_store.list_personas(
        include_market_persona_defaults=include_market_persona_defaults,
    ):
        persona_id = _persona_id(persona)
        if not persona_id:
            continue
        metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
        context_metadata, context_persona = _persona_fleet_context_overlay(
            persona,
            metadata,
            context_defaults,
        )
        is_default = persona_id in ("persona-us-equity", "persona-tw-equity", "persona-crypto")
        if not is_default:
            keys_to_strip = {
                "runtime_id", "runtime_binding_id", "legacy_paper_capital_pool_id", "capital_pool_id", "deployment_stage",
                "target_capital_pool_id", "targetCapitalPoolId", "live_capital_pool_id",
                "paper_ledger_id", "paperLedgerId", "paper_ledger", "paper_benchmark_budget", "paperBenchmarkBudget", "paper_budget",
                "league_rank", "rank", "league_score",
                "review_id", "review_type", "review", "inbox_id", "recommendation", "recommended_governance_action",
                "ooda_stage", "ooda_status", "ooda",
                "risk_flags", "risk_level", "violation_count", "risk",
                "current_work",
                "performance", "metrics", "pnl", "sharpe", "sortino", "max_drawdown", "win_rate", "trading_cost_bps", "stability_score", "human_interventions", "training_improvement_pct"
            }
            context_metadata = {k: v for k, v in context_metadata.items() if k not in keys_to_strip}
        league_entry = league_by_persona.get(persona_id, {})
        league_metrics = (
            league_entry.get("metrics")
            if isinstance(league_entry.get("metrics"), dict)
            else {}
        )
        performance = (
            metadata.get("performance")
            if isinstance(metadata.get("performance"), dict)
            else {}
        )
        metrics = {**performance, **league_metrics}
        binding = _first_binding_for_persona(
            persona_id,
            include_market_persona_defaults=include_market_persona_defaults,
        ) or {}
        pool_id = (
            league_entry.get("capital_pool_id")
            or metadata.get("capital_pool_id")
            or context_metadata.get("capital_pool_id")
            or binding.get("capital_pool_id")
        )
        runtime = _runtime_for_pool(
            pool_id,
            include_market_persona_defaults=include_market_persona_defaults,
        ) or {}
        runtime_id = (
            league_entry.get("runtime_id")
            or runtime.get("runtime_id")
            or runtime.get("id")
            or context_metadata.get("runtime_id")
            or context_metadata.get("runtime_binding_id")
            or metadata.get("runtime_binding_id")
        )
        deployment_stage = (
            league_entry.get("deployment_stage")
            or runtime.get("deployment_stage")
            or runtime.get("deployment_mode")
            or metadata.get("deployment_stage")
            or context_metadata.get("deployment_stage")
            or "none"
        )
        capital_mode = _persona_fleet_capital_mode(
            league_entry=league_entry,
            raw_metadata=metadata,
            binding=binding,
            runtime=runtime,
            deployment_stage=deployment_stage,
        )
        live_pool_id = _persona_fleet_live_capital_pool_id(
            capital_mode=capital_mode,
            pool_id=pool_id,
            league_entry=league_entry,
            raw_metadata=metadata,
            context_metadata=context_metadata,
            binding=binding,
        )
        paper_ledger_id = _persona_fleet_paper_ledger_id(
            persona_id=persona_id,
            capital_mode=capital_mode,
            league_entry=league_entry,
            raw_metadata=metadata,
            context_metadata=context_metadata,
            binding=binding,
            runtime=runtime,
        )
        paper_ledger = _persona_fleet_paper_ledger(
            paper_ledger_id=paper_ledger_id,
            persona_id=persona_id,
            league_entry=league_entry,
            raw_metadata=metadata,
            context_metadata=context_metadata,
        )
        market_scope = list(
            league_entry.get("market_scope")
            or context_metadata.get("market_scope")
            or []
        )
        asset_classes = list(context_metadata.get("asset_classes") or [])
        risk_flags = list(league_entry.get("risk_flags") or context_metadata.get("risk_flags") or [])
        lifecycle_state = str(persona.get("lifecycle_state") or persona.get("status") or "unknown")
        health = _persona_health_status(
            lifecycle_state=lifecycle_state,
            league_entry=league_entry,
            risk_flags=risk_flags,
        )
        score = _as_float(league_entry.get("league_score") or context_metadata.get("league_score"), 75.0)
        routed = _routed_strategies_for_persona(persona_id)
        open_findings = len(risk_flags) + int(metrics.get("violation_count") or 0)
        drill_target = runtime_id or persona_id
        governance_required = bool(
            league_entry.get("governance_required")
            if "governance_required" in league_entry
            else context_metadata.get("governance_required", True)
        )
        recommendation = (
            league_entry.get("recommendation")
            or context_metadata.get("recommended_governance_action")
            or ""
        )
        persona_status = str(
            metadata.get("persona_status")
            or league_entry.get("status")
            or persona.get("status")
            or lifecycle_state
        )
        data_source_status = (
            context_metadata.get("data_source_status")
            if isinstance(context_metadata.get("data_source_status"), dict)
            else {}
        )
        data_sources = (
            context_metadata.get("data_sources")
            if isinstance(context_metadata.get("data_sources"), list)
            else []
        )
        required_data_sources = (
            persona.get("required_data_sources")
            if isinstance(persona.get("required_data_sources"), list)
            else []
        )
        if not required_data_sources and isinstance(context_persona.get("required_data_sources"), list):
            required_data_sources = context_persona.get("required_data_sources") or []
        data_source_status, data_sources, source_health_bindings = _overlay_source_health_truth(
            data_source_status,
            data_sources,
            required_data_sources=required_data_sources,
        )
        data_source_refs = (
            context_metadata.get("data_source_refs")
            if isinstance(context_metadata.get("data_source_refs"), list)
            else []
        )
        research_status = (
            context_metadata.get("research_status")
            if isinstance(context_metadata.get("research_status"), dict)
            else {}
        )
        research_refs = (
            context_metadata.get("research_refs")
            if isinstance(context_metadata.get("research_refs"), list)
            else []
        )
        current_research_projects = (
            context_metadata.get("current_research_projects")
            if isinstance(context_metadata.get("current_research_projects"), list)
            else []
        )
        human_needed = governance_required and str(recommendation).strip().lower() not in {
            "",
            "none",
            "no_change",
        }
        updated_at = (
            league_entry.get("updated_at")
            or persona.get("updated_at")
            or persona.get("last_active_at")
            or snapshot_at
        )
        ooda_stage = league_entry.get("ooda_stage") or context_metadata.get("ooda_stage")

        binding_ids = {str(binding.get("id") or binding.get("binding_id") or "").strip()}
        binding_ids.discard("")
        capital_pool_ids = {str(pool_id or "").strip()}
        capital_pool_ids.discard("")
        runtime_ids = {
            str(runtime.get("runtime_id") or runtime.get("runtime_binding_id") or runtime.get("id") or "").strip()
        }
        runtime_ids.discard("")
        active_incidents = _persona_fleet_active_incidents_for_row(
            incidents=incidents_list,
            persona_id=persona_id,
            binding_ids=binding_ids,
            capital_pool_ids=capital_pool_ids,
            runtime_ids=runtime_ids,
        )

        artifact_ids = set()
        if runtime:
            art_id = str(runtime.get("artifact_id") or "").strip()
            if art_id:
                artifact_ids.add(art_id)

        incident_ids = {
            str(incident.get("incident_id") or incident.get("id") or "").strip()
            for incident in active_incidents
            if str(incident.get("incident_id") or incident.get("id") or "").strip()
        }

        telemetry_summaries = [
            t for t in all_telemetry
            if t.get("persona_id") == persona_id or t.get("runtime_id") == runtime_id
        ]
        telemetry_rollup = _management_telemetry_rollup(telemetry_summaries)
        telemetry_sharpe_values = [
            value
            for value in (
                _management_first_float(
                    summary,
                    "sharpe",
                    "sharpe_ratio",
                    "summary.sharpe",
                    "summary.sharpe_ratio",
                )
                for summary in telemetry_summaries
            )
            if value is not None
        ]
        telemetry_trade_values = [
            value
            for value in (
                _management_first_float(summary, "total_trades", "summary.total_trades")
                for summary in telemetry_summaries
            )
            if value is not None
        ]
        telemetry_metrics = {
            "pnl": telemetry_rollup.get("total_pnl"),
            "max_drawdown": telemetry_rollup.get("max_drawdown"),
            "fill_rate": telemetry_rollup.get("average_fill_rate"),
            "total_trades": int(sum(telemetry_trade_values)) if telemetry_trade_values else None,
            "sharpe": _management_avg(telemetry_sharpe_values),
        }
        telemetry_has_performance = any(value is not None for value in telemetry_metrics.values())
        is_seed_row = bool(metadata.get("is_market_persona_default") or metadata.get("seed_row"))

        mutation_projection = _persona_fleet_mutation_projection(
            persona_id=persona_id,
            updated_at=updated_at,
            evolution_decisions=all_decisions,
            artifact_ids=artifact_ids,
            incident_ids=incident_ids,
        )

        item = {
            "id": persona_id,
            "persona_id": persona_id,
            "personaId": persona_id,
            **mutation_projection,
            "name": persona.get("name") or persona_id,
            "persona_name": persona.get("name") or persona_id,
            "personaName": persona.get("name") or persona_id,
            "owner": metadata.get("owner")
            or metadata.get("owner_id")
            or "pathreon-management",
            "mode": deployment_stage,
            "status": health,
            "health": health,
            "score": score,
            "ooda": _management_fleet_ooda_label(ooda_stage),
            "autonomy": _management_fleet_autonomy(
                deployment_stage=deployment_stage,
                governance_required=governance_required,
                human_needed=human_needed,
            ),
            "perf_delta": _trading_performance_delta(),
            "perfDelta": _trading_performance_delta(),
            "has_trading_telemetry": telemetry_has_performance,
            "hasTradingTelemetry": telemetry_has_performance,
            "is_market_persona_default": is_seed_row,
            "isMarketPersonaDefault": is_seed_row,
            "seed_row": is_seed_row,
            "seedRow": is_seed_row,
            "human_needed": human_needed,
            "humanNeeded": human_needed,
            "last_mutation": str(updated_at)[:10],
            "lastMutation": str(updated_at)[:10],
            "state": persona_status,
            "current_work": context_metadata.get("current_work"),
            "currentWork": context_metadata.get("current_work"),
            "routed_strategies": routed,
            "routedStrategies": routed,
            "open_findings": open_findings,
            "openFindings": open_findings,
            "market_scope": market_scope,
            "marketScope": market_scope,
            "asset_classes": asset_classes,
            "assetClasses": asset_classes,
            "capital_mode": capital_mode,
            "capitalMode": capital_mode,
            "paper_ledger_id": paper_ledger_id,
            "paperLedgerId": paper_ledger_id,
            "paper_ledger": paper_ledger,
            "paperLedger": paper_ledger,
            "legacy_paper_capital_pool_id": pool_id if capital_mode == "paper" else None,
            "legacyPaperCapitalPoolId": pool_id if capital_mode == "paper" else None,
            "capital_pool_id": live_pool_id,
            "capitalPoolId": live_pool_id,
            "runtime_id": runtime_id,
            "runtimeId": runtime_id,
            "deployment_stage": deployment_stage,
            "deploymentStage": deployment_stage,
            "ooda_stage": ooda_stage,
            "oodaStage": ooda_stage,
            "recommendation": recommendation,
            "governance_required": governance_required,
            "governanceRequired": governance_required,
            "data_source_status": json.loads(json.dumps(data_source_status)),
            "dataSourceStatus": json.loads(json.dumps(data_source_status)),
            "data_sources": json.loads(json.dumps(data_sources)),
            "dataSources": json.loads(json.dumps(data_sources)),
            "data_source_refs": json.loads(json.dumps(data_source_refs)),
            "dataSourceRefs": json.loads(json.dumps(data_source_refs)),
            "required_data_sources": json.loads(json.dumps(required_data_sources)),
            "requiredDataSources": json.loads(json.dumps(required_data_sources)),
            "source_health_bindings": json.loads(json.dumps(source_health_bindings)),
            "sourceHealthBindings": json.loads(json.dumps(source_health_bindings)),
            "research_status": json.loads(json.dumps(research_status)),
            "researchStatus": json.loads(json.dumps(research_status)),
            "research_refs": json.loads(json.dumps(research_refs)),
            "researchRefs": json.loads(json.dumps(research_refs)),
            "current_research_projects": json.loads(json.dumps(current_research_projects)),
            "currentResearchProjects": json.loads(json.dumps(current_research_projects)),
            "metrics": {
                "pnl": _as_float(metrics.get("pnl")),
                "sharpe": _as_float(metrics.get("sharpe")),
                "sortino": _as_float(metrics.get("sortino")),
                "max_drawdown": _as_float(metrics.get("max_drawdown")),
                "win_rate": _as_float(metrics.get("win_rate")),
                "trading_cost_bps": _as_float(metrics.get("trading_cost_bps")),
                "stability_score": _as_float(metrics.get("stability_score")),
                "human_interventions": int(metrics.get("human_interventions") or 0),
                "training_improvement_pct": _as_float(metrics.get("training_improvement_pct")),
                "violation_count": int(metrics.get("violation_count") or 0),
            },
            "risk_flags": risk_flags,
            "riskFlags": risk_flags,
            "updated_at": updated_at,
            "drill_down": {
                "kind": "runtime" if runtime_id else "persona",
                "href": f"/management/runtimes/{drill_target}" if runtime_id else f"/personas/{persona_id}",
                "runtime_id": runtime_id,
                "persona_id": persona_id,
            },
            "drillDown": {
                "kind": "runtime" if runtime_id else "persona",
                "href": f"/management/runtimes/{drill_target}" if runtime_id else f"/personas/{persona_id}",
                "runtimeId": runtime_id,
                "personaId": persona_id,
            },
        }
        items.append(item)
    return sorted(
        items,
        key=lambda item: (
            -_as_float(item.get("score")),
            str(item.get("persona_id") or ""),
        ),
    )
def _persona_fleet_active_incidents_for_row(
    *,
    incidents: List[Dict[str, Any]],
    persona_id: str,
    binding_ids: Set[str],
    capital_pool_ids: Set[str],
    runtime_ids: Set[str],
) -> List[Dict[str, Any]]:
    active_statuses = {"open", "active", "investigating"}
    return [
        incident
        for incident in incidents
        if str(incident.get("status") or "").lower() in active_statuses
        and (
            str(incident.get("persona_id") or "").strip() == persona_id
            or str(incident.get("persona_capital_binding_id") or "").strip() in binding_ids
            or str(incident.get("capital_pool_id") or incident.get("affected_pool_id") or "").strip() in capital_pool_ids
            or str(incident.get("runtime_id") or "").strip() in runtime_ids
        )
    ]
_PERSONA_FLEET_RUNNING_STAGE_STATES = {
    "paper": "paper_running",
    "canary": "canary_running",
    "live": "live_running",
}
def _persona_fleet_record_value(record: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    for nested_key in ("params", "metadata"):
        nested = record.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in keys:
            value = nested.get(key)
            if value not in (None, ""):
                return value
    return None
def _persona_fleet_capital_mode(
    *,
    league_entry: Dict[str, Any],
    raw_metadata: Dict[str, Any],
    binding: Dict[str, Any],
    runtime: Dict[str, Any],
    deployment_stage: Any,
) -> str:
    for value in (
        league_entry.get("capital_mode"),
        league_entry.get("capitalMode"),
        raw_metadata.get("capital_mode"),
        raw_metadata.get("capitalMode"),
        _persona_fleet_record_value(binding, "capital_mode", "capitalMode", "allowed_deployment_scope"),
        _persona_fleet_record_value(runtime, "capital_mode", "capitalMode", "runtime_kind"),
        deployment_stage,
    ):
        normalized = str(value or "").strip().lower()
        if normalized in _PERSONA_FLEET_RUNNING_STAGE_STATES:
            return normalized
    return "none"
def _persona_fleet_live_capital_pool_id(
    *,
    capital_mode: str,
    pool_id: Any,
    league_entry: Dict[str, Any],
    raw_metadata: Dict[str, Any],
    context_metadata: Dict[str, Any],
    binding: Dict[str, Any],
) -> Optional[str]:
    if capital_mode != "paper":
        clean = str(pool_id or "").strip()
        return clean or None
    for value in (
        league_entry.get("target_capital_pool_id"),
        league_entry.get("targetCapitalPoolId"),
        raw_metadata.get("target_capital_pool_id"),
        raw_metadata.get("targetCapitalPoolId"),
        context_metadata.get("target_capital_pool_id"),
        context_metadata.get("targetCapitalPoolId"),
        league_entry.get("live_capital_pool_id"),
        raw_metadata.get("live_capital_pool_id"),
        context_metadata.get("live_capital_pool_id"),
        _persona_fleet_record_value(binding, "target_capital_pool_id", "targetCapitalPoolId", "live_capital_pool_id"),
    ):
        clean = str(value or "").strip()
        if clean:
            return clean
    return None
def _persona_fleet_paper_ledger_id(
    *,
    persona_id: str,
    capital_mode: str,
    league_entry: Dict[str, Any],
    raw_metadata: Dict[str, Any],
    context_metadata: Dict[str, Any],
    binding: Dict[str, Any],
    runtime: Dict[str, Any],
) -> Optional[str]:
    if capital_mode != "paper":
        return None
    paper_ledger = context_metadata.get("paper_ledger") if isinstance(context_metadata.get("paper_ledger"), dict) else {}
    raw_paper_ledger = raw_metadata.get("paper_ledger") if isinstance(raw_metadata.get("paper_ledger"), dict) else {}
    for value in (
        league_entry.get("paper_ledger_id"),
        league_entry.get("paperLedgerId"),
        raw_metadata.get("paper_ledger_id"),
        raw_metadata.get("paperLedgerId"),
        context_metadata.get("paper_ledger_id"),
        context_metadata.get("paperLedgerId"),
        paper_ledger.get("id"),
        raw_paper_ledger.get("id"),
        _persona_fleet_record_value(binding, "paper_ledger_id", "paperLedgerId"),
        _persona_fleet_record_value(runtime, "paper_ledger_id", "paperLedgerId"),
    ):
        clean = str(value or "").strip()
        if clean:
            return clean
    return f"paper-ledger-{persona_id}"
def _persona_fleet_paper_budget(
    *,
    league_entry: Dict[str, Any],
    raw_metadata: Dict[str, Any],
    context_metadata: Dict[str, Any],
) -> Optional[float]:
    paper_ledger = context_metadata.get("paper_ledger") if isinstance(context_metadata.get("paper_ledger"), dict) else {}
    for value in (
        league_entry.get("paper_benchmark_budget"),
        league_entry.get("paperBenchmarkBudget"),
        raw_metadata.get("paper_benchmark_budget"),
        raw_metadata.get("paperBenchmarkBudget"),
        context_metadata.get("paper_benchmark_budget"),
        context_metadata.get("paperBenchmarkBudget"),
        paper_ledger.get("benchmark_budget"),
        paper_ledger.get("benchmarkBudget"),
        raw_metadata.get("paper_budget"),
        context_metadata.get("paper_budget"),
    ):
        if value in (None, "") or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
def _persona_fleet_paper_ledger(
    *,
    paper_ledger_id: Optional[str],
    persona_id: str,
    league_entry: Dict[str, Any],
    raw_metadata: Dict[str, Any],
    context_metadata: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not paper_ledger_id:
        return None
    out: Dict[str, Any] = {
        "id": paper_ledger_id,
        "mode": "paper",
        "persona_id": persona_id,
        "is_isolated": True,
        "isolated": True,
    }
    budget = _persona_fleet_paper_budget(
        league_entry=league_entry,
        raw_metadata=raw_metadata,
        context_metadata=context_metadata,
    )
    if budget is not None:
        out["benchmark_budget"] = budget
        out["benchmarkBudget"] = budget
    return out
def _sem_final_generic_list_for_path(path: str) -> Optional[Dict[str, Any]]:
    if path == "/bff/audit":
        return _sem_final_list_response(
            _list_governance_audit_events(),
            dataset="governance_audit_events",
            surface_key="audit",
        )
    if path == "/bff/artifacts":
        return _sem_final_list_response(
            read_store.list_research_artifacts(),
            dataset="research_artifacts",
            surface_key="artifacts",
        )
    if path == "/bff/mcp-servers":
        return _sem_final_list_response(
            _merged_mcp_server_records(),
            dataset="mcp_servers",
            surface_key="mcp_servers",
            source="bff_local_registry",
        )
    if path == "/bff/mcp-tools":
        return _sem_final_list_response(
            _sem_final_mcp_tool_records(),
            dataset="mcp_tools",
            surface_key="mcp_tools",
            source="bff_local_registry",
        )
    if path == "/bff/ranking-formulas":
        return _sem_final_list_response(
            read_store.list_ranking_formulas(),
            dataset="ranking_formulas",
            surface_key="ranking_formulas",
        )
    if path == "/bff/research-experiments":
        items = read_store.list_research_experiments()
        source = _research_experiments_surface_source(items)
        return _sem_final_list_response(
            items,
            dataset="research_experiments",
            surface_key="research_experiments",
            source=source,
        )
    if path == "/bff/research-analyses":
        return _sem_final_list_response(
            read_store.list_research_analyses(),
            dataset="research_analyses",
            surface_key="research_analyses",
        )
    if path == "/bff/channels":
        return _sem_final_list_response(
            _sem_final_channel_records(),
            dataset="channels",
            surface_key="channels",
            source="bff_local_registry",
        )
    if path == "/bff/v5/loop-runs":
        available, records = read_store.list_loop_runs()
        src_dataset, source, surface = _loop_run_surface_status(available)
        return _sem_final_list_response(
            records,
            dataset=src_dataset,
            surface_key="loop_runs",
            source=source,
            surface=surface,
        )
    if path == "/bff/v5/sentinel/findings":
        available, records = read_store.list_sentinel_findings()
        src_dataset = "sentinel_findings" if available and read_store.dataset_source("incidents") == "missing" else "incidents"
        source = None if available else "missing"
        return _sem_final_list_response(records, dataset=src_dataset, surface_key="sentinel_findings", source=source)
    if path == "/bff/v5/control-room":
        snapshot_at = utc_now()
        avail_lr, loop_runs = read_store.list_loop_runs()
        avail_sf, sentinel_findings = read_store.list_sentinel_findings()
        incidents_source = read_store.dataset_source("incidents")

        def _control_room_child_surface(dataset: str, available: bool) -> Dict[str, Any]:
            if dataset == "loop_runs":
                return _loop_run_surface_status(available, snapshot_at=snapshot_at)[2]
            if incidents_source != "missing":
                return _dataset_surface_status("incidents", snapshot_at=snapshot_at)
            return _dataset_surface_status(
                dataset,
                snapshot_at=snapshot_at,
                source=None if available else "missing",
            )

        loop_surface = _control_room_child_surface("loop_runs", avail_lr)
        sentinel_surface = _control_room_child_surface("sentinel_findings", avail_sf)
        child_statuses = {
            str(loop_surface.get("status") or "ok"),
            str(sentinel_surface.get("status") or "ok"),
        }
        if child_statuses == {"ok"}:
            control_surface = {"status": "ok", "source": "composed_read_models"}
        elif child_statuses == {"unavailable"}:
            control_surface = {
                "status": "unavailable",
                "source": "missing",
                "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
            }
        else:
            control_surface = {
                "status": "degraded",
                "source": "composed_read_models",
                "staleness": {"served_from": "mixed", "last_known_at": snapshot_at},
            }
        ooda_card = _build_ooda_control_room_status_card(snapshot_at)
        return {
            "loops": {
                "items": loop_runs,
                "meta": {"snapshot_at": snapshot_at, "surfaces": {"loop_runs": loop_surface}},
            },
            "interventions": {
                "items": _v5_intervention_records(),
                "meta": {"snapshot_at": snapshot_at, "surfaces": {"interventions": {"status": "ok", "source": "bff_local_registry"}}},
            },
            "sentinel": {
                "items": sentinel_findings,
                "meta": {"snapshot_at": snapshot_at, "surfaces": {"sentinel_findings": sentinel_surface}},
            },
            "ooda_status": ooda_card,
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "control_room": control_surface,
                    "loop_runs": loop_surface,
                    "sentinel_findings": sentinel_surface,
                    "ooda_control_room_status": ooda_card["meta"],
                },
            },
        }
    if path == "/bff/v5/execution/persona-health":
        snapshot_at = utc_now()
        persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
        league_surface = _dataset_surface_status("persona_league", snapshot_at=snapshot_at)
        health_items = _build_persona_health_items(snapshot_at)
        return {
            "data": health_items,
            "items": health_items,
            "page_info": {"next_page_token": None, "total": len(health_items)},
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "persona_health": persona_surface,
                    "persona_league": league_surface,
                },
            },
        }
    if path == "/bff/v5/execution/strategy-health":
        snapshot_at = utc_now()
        strategy_surface = _dataset_surface_status("strategy_specs", snapshot_at=snapshot_at)
        strategies = read_store.list_strategy_specs()
        health_items = [
            {
                "id": s.get("strategy_id") or s.get("id"),
                "strategy_id": s.get("strategy_id") or s.get("id"),
                "name": s.get("name") or s.get("strategy_id"),
                "health": "healthy" if str(s.get("status") or "") == "active" else "degraded",
                "status": s.get("status"),
            }
            for s in strategies
        ]
        return {
            "items": health_items,
            "meta": {"snapshot_at": snapshot_at, "surfaces": {"strategy_health": strategy_surface}},
        }
    return None
def _assistant_focus_entity(
    request: Any,
) -> tuple[Optional[str], Optional[str]]:
    focus = getattr(request, "focus", None)
    if focus is not None:
        entity_type = str(getattr(focus, "entity_type", "") or "").strip()
        entity_id = str(getattr(focus, "entity_id", "") or "").strip()
        if entity_type and entity_id:
            return entity_type, entity_id

    selected = getattr(request, "selected_entity", None)
    if selected is None:
        frontend = getattr(request, "frontend", None)
        selected = getattr(frontend, "selected_entity", None) if frontend is not None else None
    if isinstance(selected, dict):
        entity_type = str(
            selected.get("entity_type")
            or selected.get("entityType")
            or selected.get("type")
            or ""
        ).strip()
        entity_id = str(
            selected.get("entity_id")
            or selected.get("entityId")
            or selected.get("id")
            or ""
        ).strip()
        if entity_type and entity_id:
            return entity_type, entity_id
    return None, None
def _assistant_source_access_meta(identity: Optional[OperatorIdentity]) -> Dict[str, Any]:
    roles = list(getattr(identity, "roles", []) or [])
    tenant: Dict[str, Any] = {
        "id": None,
        "allowed_ids": [],
        "scope": "unknown",
    }
    if identity is not None:
        try:
            tenant = _bff_me_tenant_payload(identity, requested_tenant=None)
        except HTTPException:
            tenant = {
                "id": None,
                "allowed_ids": [],
                "scope": "denied",
            }
    return {
        "rbac": {
            "enforced": True,
            "required_roles": sorted(_READ_ROLES),
            "actor_roles": roles,
        },
        "tenant": {
            "enforced": True,
            "tenant_id": tenant.get("id"),
            "allowed_tenants": list(tenant.get("allowed_ids") or []),
            "scope": tenant.get("scope") or "unknown",
        },
    }
def _assistant_attach_access_meta(
    payload: Any,
    *,
    source_id: str,
    identity: Optional[OperatorIdentity],
    snapshot_at: str,
) -> Dict[str, Any]:
    result = dict(payload) if isinstance(payload, dict) else {"data": payload}
    meta = dict(result.get("meta") if isinstance(result.get("meta"), dict) else {})
    meta.setdefault("snapshot_at", snapshot_at)
    meta.setdefault("surfaces", {source_id: {"status": "ok", "source": "bff_read"}})
    meta["access"] = _assistant_source_access_meta(identity)
    result["meta"] = meta
    return result
def _assistant_tenant_scope(identity: Optional[OperatorIdentity]) -> Dict[str, Any]:
    return _assistant_source_access_meta(identity).get("tenant", {})
def _assistant_filter_tenant_records(
    records: List[Dict[str, Any]],
    identity: Optional[OperatorIdentity],
) -> List[Dict[str, Any]]:
    tenant = _assistant_tenant_scope(identity)
    if tenant.get("scope") == "global":
        return [record for record in records if isinstance(record, dict)]
    return _mgmt_nl_filter_tenant_records(
        [record for record in records if isinstance(record, dict)],
        str(tenant.get("tenant_id") or ""),
    )
def _assistant_filter_payload_tenant(payload: Any, identity: Optional[OperatorIdentity]) -> Any:
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
    for key in ("items", "alerts", "events", "data"):
        value = result.get(key)
        if isinstance(value, list):
            result[key] = _assistant_filter_tenant_records(value, identity)
    return result
def _assistant_unavailable_source(
    source_id: str,
    *,
    href: str,
    snapshot_at: str,
    dataset: str,
    identity: Optional[OperatorIdentity] = None,
) -> Any:
    from .assistant.context_composer import AssistantCollectedSource

    surface = _dataset_surface_status(dataset, snapshot_at=snapshot_at, source="missing")
    return AssistantCollectedSource(
        source_id=source_id,
        href=href,
        payload=_assistant_attach_access_meta(
            {
                "data": None,
                "meta": {
                    "snapshot_at": snapshot_at,
                    "surfaces": {source_id: surface},
                },
            },
            source_id=source_id,
            identity=identity,
            snapshot_at=snapshot_at,
        ),
        status=str(surface.get("status") or "unavailable"),
    )
def _assistant_collect_jobs_source(
    request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
) -> Any:
    from .assistant.context_composer import AssistantCollectedSource

    entity_type, entity_id = _assistant_focus_entity(request)
    selected_job = None
    href = "/bff/jobs"
    if entity_type and entity_type.lower() in {"job", "jobs"} and entity_id:
        raw_job = _get_bff_job(entity_id)
        if isinstance(raw_job, dict):
            selected_job = next(iter(_assistant_filter_tenant_records([raw_job], identity)), None)
        href = f"/bff/jobs/{entity_id}"

    jobs = _assistant_filter_tenant_records(_list_bff_jobs(), identity)
    surface = _dataset_surface_status(
        "jobs",
        snapshot_at=snapshot_at,
        has_data=bool(jobs) or bool(selected_job) or None,
    )
    payload: Dict[str, Any] = {
        "items": jobs[:20],
        "selected": selected_job,
        "page_info": {"next_page_token": None, "total": len(jobs)},
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {"jobs": surface},
        },
    }
    if entity_id and selected_job is None:
        payload["selected_missing"] = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "reason": "job_not_found_or_not_visible",
        }
    return AssistantCollectedSource(
        source_id="jobs",
        href=href,
        payload=_assistant_attach_access_meta(
            payload,
            source_id="jobs",
            identity=identity,
            snapshot_at=snapshot_at,
        ),
        status=str(surface.get("status") or "ok"),
    )
def _assistant_collect_job_logs_source(
    request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
) -> Any:
    from .assistant.context_composer import AssistantCollectedSource

    entity_type, entity_id = _assistant_focus_entity(request)
    if not entity_id or (entity_type and entity_type.lower() not in {"job", "jobs"}):
        return None
    job = _get_bff_job(entity_id)
    if isinstance(job, dict):
        job = next(iter(_assistant_filter_tenant_records([job], identity)), None)
    if job is None:
        return _assistant_unavailable_source(
            "job_logs",
            href=f"/bff/jobs/{entity_id}/logs",
            snapshot_at=snapshot_at,
            dataset="jobs",
            identity=identity,
        )
    logs = list(job.get("logs") or [])
    surface = _dataset_surface_status("jobs", snapshot_at=snapshot_at, has_data=True)
    return AssistantCollectedSource(
        source_id="job_logs",
        href=f"/bff/jobs/{entity_id}/logs",
        payload=_assistant_attach_access_meta(
            {
                "job_id": entity_id,
                "logs": logs[:50],
                "meta": {
                    "snapshot_at": snapshot_at,
                    "surfaces": {"job_logs": surface},
                },
            },
            source_id="job_logs",
            identity=identity,
            snapshot_at=snapshot_at,
        ),
        status=str(surface.get("status") or "ok"),
    )
def _assistant_collect_audit_source(
    request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
) -> Any:
    from .assistant.context_composer import AssistantCollectedSource

    entity_type, entity_id = _assistant_focus_entity(request)
    href = "/bff/audit"
    if entity_type and entity_id:
        events = [
            event
            for event in _list_governance_audit_events(target_type=entity_type)
            if str(event.get("target_id") or event.get("entity_id") or "") == entity_id
        ]
        href = f"/bff/audit/entities/{entity_type}/{entity_id}"
    else:
        events = _list_governance_audit_events()
    events = _assistant_filter_tenant_records(events, identity)
    surface = _dataset_surface_status(
        "governance_audit_events",
        snapshot_at=snapshot_at,
        has_data=bool(events) or None,
    )
    return AssistantCollectedSource(
        source_id="audit",
        href=href,
        payload=_assistant_attach_access_meta(
            {
                "items": events[:50],
                "page_info": {"next_page_token": None, "total": len(events)},
                "meta": {
                    "snapshot_at": snapshot_at,
                    "surfaces": {"audit": surface},
                },
            },
            source_id="audit",
            identity=identity,
            snapshot_at=snapshot_at,
        ),
        status=str(surface.get("status") or "ok"),
    )
def _assistant_collect_recent_sse_source(
    _request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
) -> Any:
    from .assistant.context_composer import AssistantCollectedSource

    events = _assistant_filter_tenant_records(read_store.list_events_bff(page_size=25), identity)
    surface = _dataset_surface_status(
        "governance_audit_events",
        snapshot_at=snapshot_at,
        has_data=bool(events) or None,
    )
    return AssistantCollectedSource(
        source_id="recent_sse",
        href="/bff/events",
        payload=_assistant_attach_access_meta(
            {
                "items": events[:25],
                "page_info": {"next_page_token": None},
                "meta": {
                    "snapshot_at": snapshot_at,
                    "surfaces": {"recent_sse": surface},
                },
            },
            source_id="recent_sse",
            identity=identity,
            snapshot_at=snapshot_at,
        ),
        status=str(surface.get("status") or "ok"),
    )
_ASSISTANT_DOCS_RAG_ALLOWLIST = (
    (
        "existing_architecture_plan",
        "docs/04/pantheon_assistant_kernel_user_2026-05-31/EXISTING_ARCHITECTURE_INTEGRATION_PLAN_2026-06-03.md",
        "Pantheon Management Assistant existing architecture integration plan",
    ),
    (
        "existing_architecture_tasks",
        "docs/04/pantheon_assistant_kernel_user_2026-05-31/EXISTING_ARCHITECTURE_EXECUTION_TASKS_2026-06-03.md",
        "Existing architecture assistant integration execution tasks",
    ),
    (
        "ai_collaboration_guide",
        "AI_COLLABORATION_GUIDE.md",
        "Pantheon AI collaboration and repository workflow guide",
    ),
)
def _assistant_repo_root() -> Path:
    return Path(_REPO_ROOT)
def _assistant_doc_query_terms(request: Any) -> List[str]:
    values: List[str] = []
    for value in (
        getattr(request, "question", None),
        getattr(request, "route", None),
    ):
        if value:
            values.extend(str(value).lower().split())
    frontend = getattr(request, "frontend", None)
    if frontend is not None and getattr(frontend, "route", None):
        values.extend(str(frontend.route).lower().split("/"))
    return [value.strip(".,:;()[]{}").lower() for value in values if len(value.strip(".,:;()[]{}")) > 3]
def _assistant_doc_snippet(text: str, terms: List[str], *, limit: int = 900) -> str:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    lower = compact.lower()
    start = 0
    for term in terms:
        found = lower.find(term)
        if found >= 0:
            start = max(0, found - 160)
            break
    return compact[start:start + limit]
def _assistant_collect_docs_rag_source(
    request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
) -> Any:
    from .assistant.context_composer import AssistantCollectedSource

    root = _assistant_repo_root()
    terms = _assistant_doc_query_terms(request)
    items: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    source_refs: List[Dict[str, Any]] = []

    for slug, relative_path, title in _ASSISTANT_DOCS_RAG_ALLOWLIST:
        path = root / relative_path
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        ref_id = f"doc:{slug}"
        snippet = _assistant_doc_snippet(text, terms)
        citation = {
            "ref_id": ref_id,
            "title": title,
            "path": relative_path,
        }
        items.append({
            "ref_id": ref_id,
            "title": title,
            "path": relative_path,
            "snippet": snippet,
        })
        citations.append(citation)
        source_refs.append({
            "source_id": ref_id,
            "href": relative_path,
            "snapshot_at": snapshot_at,
            "status": "ok",
            "staleness": {
                "status": "fresh",
                "served_from": "repo_doc_allowlist",
                "last_known_at": snapshot_at,
            },
            "source_kind": "docs",
        })

    status = "ok" if items else "unavailable"
    surface = {
        "status": status,
        "source": "repo_doc_allowlist",
    }
    source_refs.insert(0, {
        "source_id": "docs_rag",
        "href": "docs://assistant/context",
        "snapshot_at": snapshot_at,
        "status": status,
        "staleness": {
            "status": "fresh" if items else "unavailable",
            "served_from": "repo_doc_allowlist",
            "last_known_at": snapshot_at,
        },
        "source_kind": "docs",
    })
    return AssistantCollectedSource(
        source_id="docs_rag",
        href="docs://assistant/context",
        payload={
            "items": items,
            "citations": citations,
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {"docs_rag": surface},
                "access": {
                    **_assistant_source_access_meta(identity),
                    "corpus": "repo_doc_allowlist",
                },
            },
        },
        status=status,
        source_kind="docs",
        source_refs=source_refs,
    )
def _assistant_collect_source(
    source_id: str,
    request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
) -> Any:
    from .assistant.context_composer import AssistantCollectedSource

    if source_id == "control_room":
        payload = _sem_final_generic_list_for_path("/bff/v5/control-room")
        if payload is None:
            return _assistant_unavailable_source(
                source_id,
                href="/bff/v5/control-room",
                snapshot_at=snapshot_at,
                dataset="incidents",
                identity=identity,
            )
        payload = _assistant_filter_payload_tenant(payload, identity)
        return AssistantCollectedSource(
            source_id=source_id,
            href="/bff/v5/control-room",
            payload=_assistant_attach_access_meta(
                payload,
                source_id=source_id,
                identity=identity,
                snapshot_at=snapshot_at,
            ),
        )
    if source_id == "jobs":
        return _assistant_collect_jobs_source(request, snapshot_at, identity)
    if source_id == "alerts":
        payload = _assistant_filter_payload_tenant(_build_operator_alerts_payload(snapshot_at), identity)
        return AssistantCollectedSource(
            source_id=source_id,
            href="/bff/alerts",
            payload=_assistant_attach_access_meta(
                payload,
                source_id=source_id,
                identity=identity,
                snapshot_at=snapshot_at,
            ),
        )
    if source_id == "audit":
        return _assistant_collect_audit_source(request, snapshot_at, identity)
    if source_id == "recent_sse":
        return _assistant_collect_recent_sse_source(request, snapshot_at, identity)
    if source_id == "persona_health":
        payload = _sem_final_generic_list_for_path("/bff/v5/execution/persona-health")
        if payload is None:
            return _assistant_unavailable_source(
                source_id,
                href="/bff/v5/execution/persona-health",
                snapshot_at=snapshot_at,
                dataset="personas",
                identity=identity,
            )
        payload = _assistant_filter_payload_tenant(payload, identity)
        return AssistantCollectedSource(
            source_id=source_id,
            href="/bff/v5/execution/persona-health",
            payload=_assistant_attach_access_meta(
                payload,
                source_id=source_id,
                identity=identity,
                snapshot_at=snapshot_at,
            ),
        )
    if source_id == "strategy_health":
        payload = _sem_final_generic_list_for_path("/bff/v5/execution/strategy-health")
        if payload is None:
            return _assistant_unavailable_source(
                source_id,
                href="/bff/v5/execution/strategy-health",
                snapshot_at=snapshot_at,
                dataset="strategy_specs",
                identity=identity,
            )
        payload = _assistant_filter_payload_tenant(payload, identity)
        return AssistantCollectedSource(
            source_id=source_id,
            href="/bff/v5/execution/strategy-health",
            payload=_assistant_attach_access_meta(
                payload,
                source_id=source_id,
                identity=identity,
                snapshot_at=snapshot_at,
            ),
        )
    if source_id == "job_logs":
        return _assistant_collect_job_logs_source(request, snapshot_at, identity)
    if source_id == "docs_rag":
        return _assistant_collect_docs_rag_source(request, snapshot_at, identity)
    return None
def _assistant_build_context_pack(session_id: str, request: Any, identity: OperatorIdentity) -> Any:
    from .assistant.context_composer import compose_context_pack

    return compose_context_pack(
        session_id=session_id,
        request=request,
        actor=identity,
        collect_source=_assistant_collect_source,
    )
_ASSISTANT_SESSION_STORE: Any = None
_ASSISTANT_TRANSCRIPT_STORE: Any = None
_ASSISTANT_CONTROL_MODE_STORE: Any = None
def _assistant_ask_enabled() -> bool:
    return os.getenv("PANTHEON_ASSISTANT_ENABLED", "").strip().lower() in {"1", "true", "yes"}
def _assistant_provider_readiness() -> Dict[str, Any]:
    provider = _mgmt_nl_provider_name()
    try:
        return OpenClawOpsClient().get_assistant_readiness(provider=provider, auth_probe=True)
    except OpenClawOpsClientError as exc:
        return {
            "provider": provider,
            "runtime": "openclaw_gateway_cli_mount",
            "ready": False,
            "status": "unavailable",
            "reason": exc.error_code,
            "message": exc.message,
            "httpStatus": exc.status_code,
        }
def _assistant_provider_list(auth_probe: bool = False) -> Dict[str, Any]:
    provider = _mgmt_nl_provider_name()
    try:
        return OpenClawOpsClient().list_assistant_providers(auth_probe=auth_probe)
    except OpenClawOpsClientError as exc:
        return {
            "status": "degraded",
            "data": [
                {
                    "provider": provider,
                    "runtime": "openclaw_gateway_cli_mount",
                    "ready": False,
                    "status": "unavailable",
                    "auth": "unavailable" if auth_probe else "not_checked",
                    "auth_status": "failed" if auth_probe else "not_checked",
                    "reason": exc.error_code,
                    "message": exc.message,
                    "httpStatus": exc.status_code,
                }
            ],
            "meta": {
                "openclawAdapterStatus": "degraded",
                "openclaw_adapter_status": "degraded",
                "reason": exc.error_code,
                "message": exc.message,
            },
        }
def _assistant_provider_register(
    payload: Dict[str, Any],
    operator_id: str,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        return OpenClawOpsClient().register_assistant_provider(
            payload=payload,
            operator_id=operator_id or "management-ai",
            trace_id=trace_id,
        )
    except OpenClawOpsClientError as exc:
        raise _openclaw_client_error(exc) from exc
def _assistant_provider_reauth(
    payload: Dict[str, Any],
    operator_id: str,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider = str(payload.get("provider") or "codex").strip() or "codex"
    try:
        return OpenClawOpsClient().start_assistant_provider_reauth(
            provider=provider,
            payload=payload,
            operator_id=operator_id or "management-ai",
            trace_id=trace_id,
        )
    except OpenClawOpsClientError as exc:
        raise _openclaw_client_error(exc) from exc
def _assistant_provider_reauth_status(
    provider: str,
    session_id: str,
    operator_id: str,
) -> Dict[str, Any]:
    try:
        return OpenClawOpsClient().get_assistant_provider_reauth_status(
            provider=provider or "codex",
            session_id=session_id,
            operator_id=operator_id or "management-ai",
        )
    except OpenClawOpsClientError as exc:
        raise _openclaw_client_error(exc) from exc
def _assistant_provider_reauth_code(
    provider: str,
    session_id: str,
    code: str,
    operator_id: str,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        return OpenClawOpsClient().submit_assistant_provider_reauth_code(
            provider=provider or "claude",
            session_id=session_id,
            code=code,
            operator_id=operator_id or "management-ai",
            trace_id=trace_id,
        )
    except OpenClawOpsClientError as exc:
        raise _openclaw_client_error(exc) from exc
def _include_governance_subrules_routes() -> None:
    from .console_gap.permissions import create_permissions_router
    from .console_gap.memory_governance import create_memory_governance_router
    from .console_gap.consult_rules import create_consult_rules_router
    from .console_gap.route_policies import create_route_policies_router
    _kw = dict(read_surface=app_deps.read_surface, extract_identity=_extract_identity, require_read_role=_require_read_role)
    app.include_router(create_permissions_router(**_kw))
    app.include_router(create_memory_governance_router(**_kw))
    app.include_router(create_consult_rules_router(**_kw))
    app.include_router(create_route_policies_router(**_kw))
_include_governance_subrules_routes()
def _include_assistant_routes() -> None:
    global _ASSISTANT_SESSION_STORE, _ASSISTANT_TRANSCRIPT_STORE, _ASSISTANT_CONTROL_MODE_STORE
    from .assistant.control_mode import ControlModeStore
    from .assistant.routes import create_assistant_router
    from .assistant.transcript_store import (
        ManagementAiAssistantSessionStore,
        ManagementAiAssistantTranscriptStore,
    )

    _ASSISTANT_SESSION_STORE = ManagementAiAssistantSessionStore(
        store_factory=_management_ai_conversation_store,
    )
    _ASSISTANT_TRANSCRIPT_STORE = ManagementAiAssistantTranscriptStore(
        store_factory=_management_ai_conversation_store,
    )
    _ASSISTANT_CONTROL_MODE_STORE = ControlModeStore()
    app.include_router(
        create_assistant_router(
            build_context_pack=_assistant_build_context_pack,
            extract_identity=_extract_identity,
            require_read_role=_require_read_role,
            bff_error=_bff_error,
            session_store=_ASSISTANT_SESSION_STORE,
            transcript_store=_ASSISTANT_TRANSCRIPT_STORE,
            control_mode_store=_ASSISTANT_CONTROL_MODE_STORE,
            provider_readiness=_assistant_provider_readiness,
            provider_list=_assistant_provider_list,
            provider_register=_assistant_provider_register,
            provider_reauth=_assistant_provider_reauth,
            provider_reauth_status=_assistant_provider_reauth_status,
            provider_reauth_code=_assistant_provider_reauth_code,
        )
    )
from .console_gap.workflows_hooks import create_workflows_hooks_router
app.include_router(
    create_workflows_hooks_router(
        workflow_hook_port=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        snapshot_now=utc_now,
    )
)
_include_assistant_routes()
from .source_management_client import SourceManagementClient  # noqa: E402
from .console_gap.datasources import create_datasources_router  # noqa: E402
source_management_client = SourceManagementClient()
app.include_router(
    create_datasources_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        snapshot_meta=_snapshot_meta,
        utc_now=utc_now,
        read_source_connector_registry=_read_management_source_connector_registry,
        get_source_management_client=lambda: source_management_client,
        require_operator_role=_require_operator_role,
        bff_error=_bff_error,
    )
)
from .management_read_models import (  # noqa: E402
    create_management_read_models_router,
    create_management_router,
)
app.include_router(
    create_management_read_models_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        snapshot_meta=_snapshot_meta,
        utc_now=utc_now,
    )
)
app.include_router(
    create_management_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        snapshot_meta=_snapshot_meta,
        utc_now=utc_now,
        bff_error=_bff_error,
        raise_if_session_logged_out=_raise_if_session_logged_out,
        tenant_payload_fn=_bff_me_tenant_payload,
    )
)
from .trade_journal import create_trade_journal_router as _create_trade_journal_router  # noqa: E402
app.include_router(_create_trade_journal_router(
    extract_identity=_extract_identity,
    require_read_role=_require_read_role,
    require_operator_role=_require_operator_role,
))
from . import trade_journeys as _trade_journeys  # noqa: E402
from .trade_journey_projection_store import InvalidPageToken, ProjectionReadUnavailable  # noqa: E402
from .trade_journeys import create_trade_journeys_router as _create_trade_journeys_router  # noqa: E402
app.include_router(_create_trade_journeys_router(
    extract_identity=_extract_identity,
    require_read_role=_require_read_role,
    require_operator_role=_require_operator_role,
    get_projection_reader=app_deps.read_surface.trade_journey_projection_reader,
))
from .console_gap.lineage import create_lineage_router  # noqa: E402
app.include_router(
    create_lineage_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        snapshot_meta=_snapshot_meta,
        utc_now=utc_now,
    )
)
from .console_gap.alpha_factory import create_alpha_factory_router as _create_alpha_factory_router  # noqa: E402
app.include_router(_create_alpha_factory_router(
    read_surface=app_deps.read_surface,
    extract_identity=_extract_identity,
    require_read_role=_require_read_role,
    utc_now=utc_now,
))
from .jobs.router import create_jobs_router as _create_jobs_router
app.include_router(
    _create_jobs_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        page_slice=_page_slice,
        read_surface_meta=_read_surface_meta,
        dataset_surface_status=_dataset_surface_status,
        raise_if_read_surface_unavailable=_raise_if_read_surface_unavailable,
        get_job_overlay=lambda: _GOV_BFF_JOB_OVERLAY,
        reject_body_idempotency_key=_reject_body_idempotency_key,
        resolve_final_idempotency_key=_resolve_final_idempotency_key,
        submit_job_action=lambda job_id, action_id, resolved_key, identity, payload: _evol_exp_bff_action_command(
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
from .events.router import create_events_router as _create_events_router
app.include_router(
    _create_events_router(
        read_surface=app_deps.read_surface,
        command_store=app_deps.command_store,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        snapshot_meta=_snapshot_meta,
        include_domain_sse_aliases=False,
    )
)
from .evolution.router import create_evolution_router as _create_evolution_router
app.include_router(
    _create_evolution_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        page_slice=_page_slice,
        snapshot_meta=_snapshot_meta,
        dataset_surface_status=_dataset_surface_status,
        read_surface_meta=_read_surface_meta,
        raise_if_read_surface_unavailable=_raise_if_read_surface_unavailable,
        meta_staleness=_meta_staleness,
        submit_program_action=lambda entity_type, entity_id, action_id, resolved_key, identity, payload: _gov_bff_action_command(
            ObjectType.EVOLUTION_PROGRAM,
            entity_id,
            action_id,
            _resolve_final_idempotency_key(resolved_key, None),
            identity,
            payload or {},
            CommandType.EVOLUTION_PROGRAM_ACTION,
        ),

    )
)
from .research.router import create_research_router as _create_research_router
app.include_router(
    _create_research_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        page_slice=_page_slice,
        snapshot_meta=_snapshot_meta,
        dataset_surface_status=_dataset_surface_status,
        submit_experiment_action=lambda entity_type, entity_id, action_id, resolved_key, identity, payload: _gov_bff_action_command(
            ObjectType.EXPERIMENT,
            entity_id,
            action_id,
            _resolve_final_idempotency_key(resolved_key, None),
            identity,
            payload or {},
            CommandType.EXPERIMENT_ACTION,
        ),
        include_prepared_subrouters=True,
    )
)
from .training.router import create_training_router as _create_training_router
app.include_router(
    _create_training_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        page_slice=_page_slice,
        dataset_surface_status=_dataset_surface_status,
    )
)
from .runtime.router import create_runtime_router as _create_runtime_router
_runtime_router = _create_runtime_router(
    read_surface=app_deps.read_surface,
    dependencies={
        name: value
        for name, value in (
            ("_GOVERNANCE_APPROVAL_QUEUE_ROUTE", _GOVERNANCE_APPROVAL_QUEUE_ROUTE),
            ("_GOV_BFF_IDEMPOTENCY", _GOV_BFF_IDEMPOTENCY),
            ("_aggregate_group_surface", _aggregate_group_surface),
            ("_alert_target_ref", _alert_target_ref),
            ("_bff_error", _bff_error),
            ("_build_persona_health_items", _build_persona_health_items),
            ("_capital_bff_idempotency_check", _capital_bff_idempotency_check),
            ("_capital_bff_idempotency_store", _capital_bff_idempotency_store),
            ("_composed_dataset_surface_status", _composed_dataset_surface_status),
            ("_composed_surface_status", _composed_surface_status),
            ("_dataset_surface_status", _dataset_surface_status),
            ("_deployment_review_href", _deployment_review_href),
            ("_deprecated_bff_path_response", _deprecated_bff_path_response),
            ("_dry_run_success_response", _dry_run_success_response),
            ("_extract_identity", _extract_identity),
            ("_gov_bff_action_command", _gov_bff_action_command),
            ("_handle_sse_stream", _handle_sse_stream),
            ("_incident_detail_href", _incident_detail_href),
            ("_meta_staleness", _meta_staleness),
            ("_ooda_packet_list_payload", _ooda_packet_list_payload),
            ("_page_slice", _page_slice),
            ("_project_operator_runtime_state_row", _project_operator_runtime_state_row),
            ("_publish_event", _publish_event),
            ("_raise_if_read_surface_unavailable", _raise_if_read_surface_unavailable),
            ("_read_surface_meta", _read_surface_meta),
            ("_reject_body_idempotency_key", _reject_body_idempotency_key),
            ("_request_dry_run_requested", _request_dry_run_requested),
            ("_require_ooda_packet_routes_enabled", _require_ooda_packet_routes_enabled),
            ("_require_operator_role", _require_operator_role),
            ("_require_read_role", _require_read_role),
            ("_resolve_final_idempotency_key", _resolve_final_idempotency_key),
            ("_snapshot_meta", _snapshot_meta),
            ("_split_csv_query", _split_csv_query),
            ("_sse_buffers", _sse_buffers),
            ("_sse_subscribers", _sse_subscribers),
            ("_stable_json_hash", _stable_json_hash),
            ("create_capital_binding", create_capital_binding),
            ("utc_now", utc_now),
        )
    },
)
app.routes.extend(_runtime_router.routes)
from .deployment.router import create_deployment_router as _create_deployment_router
app.include_router(
    _create_deployment_router(
        queries=app_deps.deployment_queries,
        commands=app_deps.deployment_commands,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        page_slice=_page_slice,
        snapshot_meta=_snapshot_meta,
        dataset_surface_status=_dataset_surface_status,
        composed_surface_status=_composed_surface_status,
        read_surface_meta=_read_surface_meta,
        raise_if_read_surface_unavailable=_raise_if_read_surface_unavailable,
        aggregate_group_surface=_aggregate_group_surface,
        split_csv_query=_split_csv_query,
        meta_staleness=_meta_staleness,
        stable_json_hash=_stable_json_hash,
        resolve_final_idempotency_key=_resolve_final_idempotency_key,
        reject_body_idempotency_key=_reject_body_idempotency_key,
        request_dry_run_requested=_request_dry_run_requested,
        gov_bff_idempotency=_GOV_BFF_IDEMPOTENCY,
        publish_event=_publish_event,
        sse_buffers=_sse_buffers,
        sse_subscribers=_sse_subscribers,
        gov_bff_action_command=_gov_bff_action_command,
        deprecated_bff_path_response=_deprecated_bff_path_response,
        sem_command_response=_sem_command_response,
        stream_generic_events=stream_generic_events,
        surface_degradation_reason=_surface_degradation_reason,
    )
)
from .command_adapters.router import (
    create_action_command_router as _create_action_command_router,
    create_command_adapters_router as _create_command_adapters_router,
)
app.include_router(
    _create_action_command_router(
        submit_command_admission=_submit_final_command_admission,
        extract_identity=_extract_identity,
        require_operator_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        command_store=app_deps.command_store,
    )
)
app.include_router(
    _create_command_adapters_router(
        command_store=app_deps.command_store,
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_operator_role=_require_operator_role,
        require_read_role=_require_read_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        submit_command_admission=_submit_final_command_admission,
        publish_event=lambda event_type, data: _publish_event(
            _sse_buffers["audit"],
            _sse_subscribers["audit"],
            event_type,
            data,
        ),
        gov_bff_idempotency=_GOV_BFF_IDEMPOTENCY,
        check_read_surface_state=_check_read_surface_state,
    )
)
from .management_read_models.ranking_router import create_ranking_formulas_router as _create_ranking_formulas_router
app.include_router(
    _create_ranking_formulas_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        snapshot_meta=_snapshot_meta,
    )
)
from .management_read_models.ranking_router import (
    create_performance_attribution_router as _create_performance_attribution_router,
)
from .management_read_models.ranking_router import (
    create_rankings_long_tail_router as _create_rankings_long_tail_router,
)
app.include_router(
    _create_rankings_long_tail_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        page_slice=_page_slice,
        read_surface_meta=_read_surface_meta,
        deprecated_bff_path_response=_deprecated_bff_path_response,
        reject_body_idempotency_key=_reject_body_idempotency_key,
        resolve_final_idempotency_key=_resolve_final_idempotency_key,
        capital_bff_idempotency_check=_capital_bff_idempotency_check,
        capital_bff_idempotency_store=_capital_bff_idempotency_store,
        capital_bff_action_command=_capital_bff_action_command,
        object_type=ObjectType,
        command_type=CommandType,
    )
)
app.include_router(
    _create_performance_attribution_router(
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        bff_me_tenant_payload=_bff_me_tenant_payload,
        pm12_performance_attribution_response=_pm12_performance_attribution_response,
        attribution_dimensions=_PM12_ATTRIBUTION_DIMENSIONS,
    )
)
from .strategies.router import create_strategies_router as _create_strategies_router
app.include_router(
    _create_strategies_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        page_slice=_page_slice,
        read_surface_meta=_read_surface_meta,
        reject_body_idempotency_key=_reject_body_idempotency_key,
        resolve_final_idempotency_key=_resolve_final_idempotency_key,
        stable_json_hash=_stable_json_hash,
        request_dry_run_requested=_request_dry_run_requested,
        dry_run_success_response=_dry_run_success_response,
        normalize_lifecycle_state=_normalize_lifecycle_state,
        normalize_risk_level=_normalize_risk_level,
        strategy_persona_idempotency_check=_strategy_persona_idempotency_check,
        strategy_persona_action_command=_strategy_persona_action_command,
        strategy_overlay=_STRATEGY_BFF_OVERLAY,
        strategy_persona_idempotency_store=_STRATEGY_PERSONA_BFF_IDEMPOTENCY,
        strategy_seed_replication_idempotency_store=_STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY,
        strategy_seed_review_idempotency_store=_STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY,
        list_governance_audit_events=_list_governance_audit_events,
        ooda_packet_list_payload=_ooda_packet_list_payload,
        require_ooda_packet_routes_enabled=_require_ooda_packet_routes_enabled,
        deprecated_bff_path_response=_deprecated_bff_path_response,
        bff_me_tenant_payload=_bff_me_tenant_payload,
        list_persona_records=_list_persona_records,
        list_strategy_summaries=_list_strategy_summaries,
    )
)
from .incidents.router import create_incident_router as _create_incident_router
app.include_router(
    _create_incident_router(
        read_surface=app_deps.read_surface,
        command_store=app_deps.command_store,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        page_slice=_page_slice,
        snapshot_meta=_snapshot_meta,
        dataset_surface_status=_dataset_surface_status,
        meta_staleness=_meta_staleness,
        surface_degradation_reason=_surface_degradation_reason,
        read_surface_meta=_read_surface_meta,
        raise_if_read_surface_unavailable=_raise_if_read_surface_unavailable,
        resolve_final_idempotency_key=_resolve_final_idempotency_key,
        reject_body_idempotency_key=_reject_body_idempotency_key,
        submit_action_command=_gov_bff_action_command,
        submit_sem_command=_sem_command_response,
        handle_sse_stream=_handle_sse_stream,
        run_management_read=_run_management_read,
        request_dry_run_requested=_request_dry_run_requested,
        dry_run_success_response=_dry_run_success_response,
        build_operator_alerts_payload=lambda s: _build_operator_alerts_payload(s),
        list_governance_audit_events=_list_governance_audit_events,
        get_bff_incident=_get_bff_incident,
        list_bff_incidents=_list_bff_incidents,
        incident_events=_incident_events,
        incident_subscribers=_incident_subscribers,
        acknowledged_alerts=_ACKNOWLEDGED_ALERTS,
        incident_overlay=_GOV_BFF_INCIDENT_OVERLAY,
        idempotency_ledger=_GOV_BFF_IDEMPOTENCY,
    )
)
def _ensure_agora_servant_openclaw_agent(persona: Dict[str, Any]) -> Dict[str, Any]:
    return OpenClawOpsClient().ensure_agora_servant_agent(persona)
def _resolve_agora_interaction_context_ref(
    *,
    kind: str,
    ref_id: str,
    ref_version: Optional[str],
    resolved: Any,
    session: Dict[str, Any],
    context_refs: List[Dict[str, Any]],
    authorization: Optional[str],
    source_route: Optional[str],
    focused_object: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve only context kinds whose existing owner can prove audience scope.

    Management positions, performance windows, and Human Inbox rows currently
    have no canonical per-user ownership contract.  They remain explicit
    dependency-unavailable sources instead of being promoted from a global
    read-model row into a user-scoped interaction receipt.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    if kind in {"position", "performance_window", "human_inbox_item"}:
        raise _bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            f"Canonical {kind} interaction scope is unavailable",
            f"{kind} does not yet expose a tenant-and-user-scoped ownership receipt",
            precondition_failed=f"{kind}_scope_unavailable",
        )

    if kind == "decision_event":
        if (
            focused_object.get("kind") == "decision_event"
            and str(focused_object.get("id") or "") == ref_id
        ):
            raise _bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Focused Decision Event interaction source is unavailable",
                "No canonical frontend Decision Event source-route owner is registered yet",
                precondition_failed="decision_event_source_route_unavailable",
            )
        from .agora.trading_room.router import _get_store as _get_trading_room_store

        event = _get_trading_room_store().get_decision_event(ref_id)
        if not isinstance(event, dict):
            return {"row": None, "audience_verified": False}
        event_strategy = str(event.get("strategy_id") or "")
        event_version = str(event.get("strategy_spec_registry_id") or "")
        scoped_strategy = str(session.get("strategy_id") or "")
        scoped_version = str(session.get("active_strategy_spec_registry_id") or "")
        audience_verified = bool(
            event_strategy
            and event_strategy == scoped_strategy
            and (not event_version or event_version == scoped_version)
        )
        return {"row": event, "audience_verified": audience_verified}

    if kind == "journal_entry":
        from services.control_plane.bff.trade_journal import _allowed as _trade_journal_allowed
        from services.control_plane.bff.trade_journal import _load as _load_trade_journal

        episodes = _load_trade_journal("PANTHEON_BFF_TRADE_EPISODES_STORE")
        matches = [
            row for row in (episodes or [])
            if str(row.get("trade_episode_id") or "") == ref_id
        ]
        if len(matches) == 1:
            episode = matches[0]
            schema_path = (
                Path(__file__).resolve().parents[2]
                / "telemetry"
                / "trade_episode_projection.schema.json"
            )
            try:
                projection_schema = json.loads(schema_path.read_text(encoding="utf-8"))
                projection_valid = Draft7Validator(projection_schema).is_valid(episode)
            except (OSError, TypeError, ValueError):
                projection_valid = False
            persona_id = str(episode.get("persona_id") or "")
            referenced_personas = {
                str(item.get("id") or "")
                for item in context_refs
                if item.get("kind") == "persona"
            }
            persona = _get_persona_directory_snapshot(
                str(resolved.tenant_id or "").strip()
            ).records_by_id.get(persona_id)
            episode_strategy = str(episode.get("strategy_id") or "")
            artifact_id = str(episode.get("artifact_id") or "")
            artifact_version = str(episode.get("artifact_version") or "")
            episode_strategy_version = str(episode.get("strategy_spec_registry_id") or "")
            scoped_strategy = str(session.get("strategy_id") or "")
            scoped_version = str(session.get("active_strategy_spec_registry_id") or "")
            source = urlsplit(str(source_route or ""))
            source_path = unquote(source.path).rstrip("/")
            source_query = parse_qs(source.query, keep_blank_values=True)
            focused_is_episode = (
                focused_object.get("kind") == "journal_entry"
                and str(focused_object.get("id") or "") == ref_id
            )
            canonical_persona_journal_route = bool(
                source_path == f"/management/personas/{persona_id}"
                and source_query.get("tab") == ["tradeJournal"]
                and not source.fragment
            )
            canonical_workshop_route = bool(
                not focused_is_episode
                and source_path == f"/agora/strategy-workshop/{session.get('workshop_id')}"
                and not source.fragment
            )
            audience_verified = bool(
                projection_valid
                and persona_id
                and episode_strategy
                and artifact_id
                and artifact_version
                and persona_id in referenced_personas
                and isinstance(persona, dict)
                and _persona_record_tenant_id(persona) == resolved.tenant_id
                and _trade_journal_allowed(identity, persona_id)
                and episode_strategy == scoped_strategy
                and (not episode_strategy_version or episode_strategy_version == scoped_version)
                and (canonical_persona_journal_route or canonical_workshop_route)
            )
            return {"row": episode, "audience_verified": audience_verified}

        journal_rows = _agora_filter_private_records(
            read_store.list_decision_journal_entries(), identity,
        )
        journal = next(
            (row for row in journal_rows if str(row.get("id") or row.get("entry_id") or "") == ref_id),
            None,
        )
        # Legacy Decision Journal rows are returned for exact not-found/error
        # semantics, but without explicit scope they are intentionally not
        # elevated to an audience-verified receipt.
        return {"row": journal, "audience_verified": False}

    raise _bff_error(
        503,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        f"Canonical {kind} readback is unavailable",
        f"{kind}_store_unavailable",
        precondition_failed=f"{kind}_store_unavailable",
    )
from .auth.router import create_auth_router
from .auth.service import AuthFacadeService
from .auth.handlers import AuthDependencies, create_auth_handlers

# Auth routes are owned by ``auth.router``; bind the concrete handlers here at
# the composition root so an unassembled facade cannot silently ship a 503 for
# every session request.  Provider readiness remains cache-only and advisory.
auth_deps = AuthDependencies(
    bff_error=_bff_error,
    dev_login_forbidden_environment=_dev_login_forbidden_environment,
    dev_login_identity_registry=_dev_login_identity_registry,
    extract_identity=_extract_identity,
    require_read_role=_require_read_role,
    raise_if_session_logged_out=_raise_if_session_logged_out,
    session_lifecycle_store=session_lifecycle_store,
    bff_me_tenant_payload=_bff_me_tenant_payload,
    capabilities_for_identity=_capabilities_for_identity,
    bff_auth_stub_enabled=_bff_auth_stub_enabled,
    bff_auth_mode=_bff_auth_mode,
    bff_source_commit=_bff_source_commit,
    write_roles=frozenset(_WRITE_ROLES),
    utc_now=utc_now,
)
auth_handlers = create_auth_handlers(dependencies=auth_deps)
auth_facade_service = AuthFacadeService(
    local_readiness=auth_handlers["bff_auth_readiness"],
    handlers=auth_handlers,
)
app.include_router(create_auth_router(service=auth_facade_service))
from .core.app_factory import (
    create_settings_router,
    create_assistant_management_router,
    create_core_router,
)
app.include_router(
    create_settings_router(
        settings_store=settings_store,
        extract_identity=_extract_identity,
        require_admin_mfa=_require_admin_mfa,
    )
)
_core_handlers = {
    "bff_management_nl_ask": bff_management_nl_ask,
    "bff_management_nl_ask_stream": bff_management_nl_ask_stream,
    "bff_management_ai_audit": bff_management_ai_audit,
    "bff_assistant_provider_usage_summary": bff_assistant_provider_usage_summary,
    "bff_management_ai_conversations": bff_management_ai_conversations,
    "bff_management_ai_conversation": bff_management_ai_conversation,
    "bff_management_ai_attachment": bff_management_ai_attachment,
    "bff_management_readiness_ep5": bff_management_readiness_ep5,
    "bff_management_readiness_broker_live": bff_management_readiness_broker_live,
    "bff_management_readiness_capital_binding_live": bff_management_readiness_capital_binding_live,
    "bff_management_readiness_bff_ha": bff_management_readiness_bff_ha,
    "bff_management_readiness_strict_publish": bff_management_readiness_strict_publish,
    "bff_types_compat": bff_types_compat,
    "sem_bff_version": sem_bff_version,
    "sem_bff_health_alias": sem_bff_health_alias,
    "sem_bff_readiness_alias": sem_bff_readiness_alias,
    "sem_bff_capabilities": sem_bff_capabilities,
}
app.include_router(create_assistant_management_router(_core_handlers))
app.include_router(create_core_router(_core_handlers))
from .personas.router import create_personas_router
from .personas.service import PersonaService
persona_service = PersonaService(
    write_owner=app_deps.persona_write_owner,
    read_store=app_deps.read_surface,
    ranking_write_owner=app_deps.ranking_write_owner,
    command_store=app_deps.command_store,
)
app.include_router(
    create_personas_router(
        service=persona_service,
        extract_identity_fn=_extract_identity,
        require_read_role_fn=_require_read_role,
        require_operator_role_fn=_require_operator_role,
        bff_error_fn=_bff_error,
        utc_now_fn=utc_now,
        page_slice_fn=_page_slice,
        snapshot_meta_fn=_snapshot_meta,
        dataset_surface_status_fn=_dataset_surface_status,
        raise_if_read_surface_unavailable_fn=_raise_if_read_surface_unavailable,
        reject_body_idempotency_key_fn=_reject_body_idempotency_key,
        resolve_final_idempotency_key_fn=_resolve_final_idempotency_key,
    )
)
from .capital.router import create_capital_router
app.include_router(
    create_capital_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_operator_role,
        utc_now=utc_now,
        page_slice=_page_slice,
        snapshot_meta=_snapshot_meta,
        dataset_surface_status=_dataset_surface_status,
        bff_error=_bff_error,
    )
)
from .governance.router import create_governance_router
app.include_router(
    create_governance_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=utc_now,
        page_slice_fn=_page_slice,
        snapshot_meta=_snapshot_meta,
        dataset_surface_status=_dataset_surface_status,
        read_surface_meta=_read_surface_meta,
        meta_staleness=_meta_staleness,
        redact_evidence_refs=redact_evidence_refs,
        capabilities_for_identity=_capabilities_for_identity,
        submit_action=lambda entity_type, entity_id, action_id, resolved_key, identity, payload: _gov_bff_action_command(
            entity_type, entity_id, action_id, resolved_key, identity, payload
        ),
        publish_event=lambda event_type, data: _publish_event(
            _sse_buffers["audit"], _sse_subscribers["audit"], event_type, data
        ),

    )
)
from .postmortems.router import create_postmortem_router
app.include_router(
    create_postmortem_router(
        read_surface=app_deps.read_surface,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        bff_error=_bff_error,
        meta_staleness=_meta_staleness,
    )
)
from .control_loops.router import create_control_loops_router
app.include_router(
    create_control_loops_router(
        read_surface=app_deps.read_surface,
        loop_truth_adapter=loop_truth,
        downstream_health_monitor=downstream_health_monitor,
        submit_sem_command=_sem_command_response,
        submit_final_command_admission=_submit_final_command_admission,
        reject_body_idempotency_key=_reject_body_idempotency_key,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now_fn=utc_now,
    )
)
from .tools_integrations.router import create_integrations_router
app.include_router(
    create_integrations_router(
        read_surface=app_deps.read_surface,
        openclaw_client=OpenClawOpsClient(),
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_operator_role,
        require_mcp_tool_write_role=_require_operator_role,
        require_openclaw_command_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now_fn=utc_now,
        page_slice_fn=_page_slice,
        snapshot_meta=_snapshot_meta,
        read_surface_meta=_read_surface_meta,
        submit_command=_submit_final_command_admission,
        dry_run_resolver=_truthy_header,
        dry_run_context=_REQUEST_DRY_RUN_CONTEXT,
    )
)
from .agora.router import create_agora_router as _create_agora_router  # noqa: E402
_agora_router = _create_agora_router(
    extract_identity=_extract_identity,
    require_read_role=_require_read_role,
    require_write_role=_require_operator_role,
    require_operator_role=_require_operator_role,
    require_journal_write_role=_require_journal_write_role,
    require_agora_signal_write_role=_require_agora_signal_write_role,
    require_agora_bulk_feedback_role=_require_agora_bulk_feedback_role,
    bff_error=_bff_error,
    utc_now=utc_now,
    read_surface=app_deps.read_surface,
    get_audit_store=lambda: agora_audit_store,
    command_store=app_deps.command_store,
    persona_write_owner=app_deps.persona_write_owner,
    get_trade_journey_store=lambda: _trade_journeys.EVENT_STORE,
    sync_servant_agent=lambda persona: _ensure_agora_servant_openclaw_agent(dict(persona)),
    canonical_context_ref_resolver=_resolve_agora_interaction_context_ref,
    idempotency_store=_AGORA_CORE_BFF_IDEMPOTENCY,
    sse_buffers=_sse_buffers,
    sse_subscribers=_sse_subscribers,
    assistant_ask_enabled=_assistant_ask_enabled,
    assistant_build_context_pack=_assistant_build_context_pack,
    get_assistant_session_store=lambda: _ASSISTANT_SESSION_STORE,
    get_assistant_transcript_store=lambda: _ASSISTANT_TRANSCRIPT_STORE,
    openclaw_ops_client_factory=lambda: OpenClawOpsClient(),
    handle_sse_stream=_handle_sse_stream,
    publish_event_fn=_publish_event,
)
app.include_router(_agora_router)
interaction_lifecycle = _agora_router.interaction_lifecycle
workshop_store = _agora_router.workshop_store
proposal_store = _agora_router.proposal_store

import types as _types
class _BffMainModule(_types.ModuleType):
    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name == "read_store" and hasattr(self, "app_deps") and hasattr(self.app_deps, "read_surface"):
            if value is not self.app_deps.read_surface:
                self.app_deps.read_surface._active_delegate = value
            else:
                self.app_deps.read_surface._active_delegate = None

import sys as _sys
_sys.modules[__name__].__class__ = _BffMainModule

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
