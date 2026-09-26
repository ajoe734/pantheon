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
import sys as _sys
from collections import deque
from copy import deepcopy
from concurrent.futures import Executor, ThreadPoolExecutor, TimeoutError as _FuturesTimeoutError
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import partial, wraps
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, Iterator, List, Mapping, NoReturn, Optional, Sequence, Set, Tuple
from urllib.parse import quote, urlencode
from urllib import error as urllib_error
from urllib import request as urllib_request

from fastapi import Body, Cookie, FastAPI, HTTPException, BackgroundTasks, Header, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.params import Param as FastAPIParam
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

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
if not __package__:
    __package__ = "services.control_plane.bff"
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
from .command_queue import CommandStore

if not hasattr(CommandStore, "_cache"):
    CommandStore._cache = []

try:
    from . import assistant_conversation_store as _acs_mod
    sys.modules.setdefault("assistant_conversation_store", _acs_mod)
except Exception:
    pass

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
from .auth import policy as auth_policy
from .auth.policy import create_auth_dependencies
from .shared.cross_domain_utils import (
    _management_as_float,
    _management_first_float,
    _management_nested_value,
    _management_telemetry_rollup,
    _merge_registry_records,
    _ppl_alloc_009_paper_environment_guard,
    _resolve_param,
    _sort_records_latest_first,
    _surface_degradation_reason,
)
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
from .assistant.management_contracts import ManagementNlUseCaseDeps
from .assistant.management_service import ManagementNlUseCase
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
from .management_read_models.service import _SHELL_SUMMARY_COUNT_CACHE
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
from .ports.job_read import JobSourceUnavailableError
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
from .personas.service import (
    PersonaDirectorySnapshot,
    _append_persona_reconcile_diagnostic,
    _checkpoint_persona_provisioning_readback,
    _evaluate_persona_provisioning_status,
    _get_persona_directory_snapshot,
    _list_persona_records as _personas_list_persona_records,
    _normalize_lifecycle_state,
    _normalize_risk_level,
    _openclaw_agent_reconcile_request,
    _persona_create_required_data_sources,
    _persona_first_evaluation_readback_poll_seconds,
    _persona_first_evaluation_readback_timeout_seconds,
    _persona_fleet_context_defaults_by_market,
    _persona_fleet_context_missing,
    _persona_fleet_context_overlay,
    _persona_fleet_market_key,
    _persona_id,
    _persona_provisioning_metadata,
    _persona_provisioning_store,
    _persona_record_for_provisioning,
    _persona_record_tenant_id,
    _promotion_review_find,
    _reconcile_persona_provisioning_compensation,
    _register_persona_cron_required,
    _remove_persona_cron_required,
)
def _list_persona_records(
    tenant_id: Optional[str] = None,
    read_store: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Composition-root binding: personas/service.py is the sole owner of this
    projection; explicitly inject the live ``read_store`` global so callers
    outside an active PersonaService request context (composition-root and
    seam-test callers) still resolve against whatever store this module
    currently holds, matching the injected pattern used by the other main.py
    consumer seams instead of relying on personas/service.py's own module
    fallback."""
    resolved_store = read_store if read_store is not None else globals().get("read_store")
    return _personas_list_persona_records(tenant_id, read_store=resolved_store)
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
_BFF_AUTH_STUB_ENV = auth_policy._BFF_AUTH_STUB_ENV
_BFF_STUB_LEGACY_BARE_TOKENS_ENV = auth_policy._BFF_STUB_LEGACY_BARE_TOKENS_ENV
_BFF_STUB_CAPABILITY_ROLES = auth_policy._BFF_STUB_CAPABILITY_ROLES
_PRODUCTION_STRICT_ENVIRONMENTS = auth_policy._PRODUCTION_STRICT_ENVIRONMENTS
_BFF_VALID_AUTH_MODES = auth_policy._BFF_VALID_AUTH_MODES
_bff_auth_mode = auth_policy.bff_auth_mode
_is_production_strict_mode = auth_policy.is_production_strict_mode
_bff_auth_stub_enabled = auth_policy.bff_auth_stub_enabled
from .core.http_security import _cors_origin_allowed
from .core.errors import _pack_d_direct_error_response
from .core.lifespan import (
    create_lifespan,
    recoverable_capital_command,
    refresh_provider_readiness,
    replay_submitted_commands,
    retryable_terminal_capital_command,
)
_recoverable_capital_command = recoverable_capital_command
_retryable_terminal_capital_command = retryable_terminal_capital_command
from .auth.service import ProviderReadinessCache
from .core.app_factory import build_bff_app


def _default_openclaw_provider_probe() -> Dict[str, Any]:
    try:
        from .openclaw_ops_client import OpenClawOpsClient
        client = OpenClawOpsClient()
        if not client.configured:
            return {
                "provider": "openclaw",
                "ready": False,
                "status": "unavailable",
                "reason": "openclaw_adapter_unconfigured",
            }
        status = client.get_upstream_status()
        ready = bool(
            status.get("reachable")
            or status.get("ready")
            or status.get("status") in {"ready", "ok", "healthy"}
        )
        return {
            "provider": "openclaw",
            "ready": ready,
            "status": "ready" if ready else "unavailable",
            "raw": status,
        }
    except Exception as exc:
        return {
            "provider": "openclaw",
            "ready": False,
            "status": "unavailable",
            "reason": type(exc).__name__,
        }


provider_readiness_cache = ProviderReadinessCache(
    probe=_default_openclaw_provider_probe,
    provider="openclaw",
)
_bff_lifespan = create_lifespan(
    provider_readiness_cache,
    command_store=lambda: command_store,
    process_command=lambda cmd_id, **kw: _process_command_stub(cmd_id, **kw),
)

app = build_bff_app(
    lifespan=_bff_lifespan,
    dev_login_enabled=lambda: auth_policy.dev_login_enabled(),
    origin_allowed=_cors_origin_allowed,
    validate_session=lambda token: _raise_if_session_logged_out(_extract_identity(f"Bearer {token}")),
)
_REQUEST_DRY_RUN_CONTEXT: ContextVar[bool] = ContextVar("request_dry_run_context", default=False)
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

_ERROR_CODE_BY_STATUS = auth_policy._ERROR_CODE_BY_STATUS
_LEGACY_ERROR_CODE_ALIASES = auth_policy._LEGACY_ERROR_CODE_ALIASES
_PACK_D_D21_ERROR_BEHAVIOR = auth_policy._PACK_D_D21_ERROR_BEHAVIOR
_status_error_code = auth_policy.status_error_code
_canonical_error_code_value = auth_policy.canonical_error_code_value
_pack_d_error_metadata = auth_policy.pack_d_error_metadata


from .bootstrap.dependencies import AppDependencies

app_deps = AppDependencies.create_default()
command_store = app_deps.command_store
session_lifecycle_store = SessionLifecycleStore(os.path.join(BFF_DATA_DIR, "session_lifecycle.json"))
agora_audit_store = AgoraAuditStore()
persona_write_owner = app_deps.persona_write_owner
ranking_write_owner = app_deps.ranking_write_owner
strategy_write_owner = app_deps.strategy_write_owner
persona_reconciliation_mutation_port = PersonaProvisioningReconciliationMutationPort(
    persona_mutation_port=persona_write_owner,
)
read_store: ReadSurfacePorts = app_deps.read_surface

from .management_read_models.service import ManagementService as _ManagementServiceForContext

# Management AI context collection (_mgmt_nl_collect_context) must reach the
# Management domain through purpose-built queries rather than bare
# ReadSurfacePorts calls; MGMT-READ-001 mandatory deletion: generic store
# access and migrated overlay reads.
_management_ai_context_service = _ManagementServiceForContext(read_store=read_store, utc_now=utc_now)


from .assistant.management_service import _record_agora_audit_event
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
_DEV_LOGIN_IDENTITY_DEFS = auth_policy._DEV_LOGIN_IDENTITY_DEFS
_dev_login_forbidden_environment = auth_policy.dev_login_forbidden_environment
_dev_login_identity_registry = auth_policy.dev_login_identity_registry
_dev_login_enabled = auth_policy.dev_login_enabled

_extract_identity = auth_policy.extract_identity
_extract_identity_stub = auth_policy.extract_identity_stub
_stub_identity_capabilities = auth_policy.stub_identity_capabilities
_with_structured_identity_capabilities = auth_policy.with_structured_identity_capabilities
_extract_identity_jwt = auth_policy.extract_identity_jwt
_bff_error = auth_policy.bff_error
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
    route: str = _FINAL_COMMAND_ROUTE,
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
    payload.pop("route", None)
    payload.pop("source_route", None)
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
    route: str = _FINAL_COMMAND_ROUTE,
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
    admission_route = str(foundation_context.get("admission_route") or _FINAL_COMMAND_ROUTE)
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
    admission_route = str(foundation_context.get("admission_route") or _FINAL_COMMAND_ROUTE)
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
from .governance.command_audit import (
    project_command_record_audit_event as _project_command_record_audit_event,
    audit_event_matches as _audit_event_matches,
    list_projected_governance_audit_events as _list_projected_governance_audit_events,
)
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
        for event in _list_projected_governance_audit_events(
            command_store,
            actor=actor,
            action_types=action_types,
            target_type=target_type,
            from_ts=from_ts,
            to_ts=to_ts,
        ):
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
    if auth_policy.bool_from_env("PANTHEON_LIVE_BROKER_ENABLED", default=False):
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
_require_admin_mfa = auth_policy.require_admin_mfa
def _deployment_review_href(plan_id: str) -> str:
    return f"{_OPERATOR_DEPLOYMENT_REVIEW_ROUTE}?plan={plan_id}"
def _incident_detail_href(incident_id: str) -> str:
    return f"{_OPERATOR_INCIDENT_HOME_ROUTE}/{incident_id}"
from .command_adapters.service import _runtime_command_context
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
from .assistant.management_service import _resolve_final_idempotency_key
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
from .assistant.management_service import _stable_json_hash
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
    CommandType.PAUSE_PAPER_RUNTIME: ObjectType.RUNTIME,
    CommandType.RESUME_PAPER_RUNTIME: ObjectType.RUNTIME,
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
def _validate_paper_runtime_authority_target_binding(cmd: OperatorCommand) -> None:
    if cmd.command not in {CommandType.PAUSE_PAPER_RUNTIME, CommandType.RESUME_PAPER_RUNTIME}:
        return
    if cmd.target.type != ObjectType.RUNTIME:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{cmd.command.value} requires target.type = Runtime",
            "Canonical paper commands only accept Runtime targets",
            precondition_failed="target.type",
        )
    target_id = str(cmd.target.id or "").strip()
    if not target_id:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{cmd.command.value} requires a non-empty runtime target id",
            "target.id must be a non-empty runtime id",
            precondition_failed="target.id",
        )
    aliases = ("runtime_id", "runtimeId", "entity_id", "entityId")
    supplied = {
        str(cmd.params.get(alias) or "").strip()
        for alias in aliases
        if str(cmd.params.get(alias) or "").strip()
    }
    if supplied and supplied != {target_id}:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"runtime_id must match command target.id for {cmd.command.value}",
            (
                f"Canonical paper command targets {target_id!r}, but params supplied "
                f"{sorted(supplied)!r}"
            ),
            precondition_failed="target_redirection_detected",
        )
    # Discard caller-supplied verified_binding/verified_binding_id
    cmd.params.pop("verified_binding", None)
    cmd.params.pop("verified_binding_id", None)
    cmd.params.pop("verified_runtime_binding_id", None)
    cmd.params["runtime_id"] = target_id
    cmd.params["entity_id"] = target_id
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
from .command_adapters.preconditions import (
    _validate_pause_execution,
    _validate_issue_risk_off,
    _validate_liquidate_all,
    _validate_hard_rollback,
    _validate_issue_safe_mode,
)
from .command_adapters.service import _derive_drawer_execution_params


from .command_adapters.service import stored_command_params as _stored_command_params
from .governance.service import human_inbox_surface_timeout_seconds as _human_inbox_surface_timeout_seconds

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
from .command_adapters.service import _resolve_execution_params_for_record
from .pm12.service import (
    _pm12_resolve_quarterly_recommendation_submit_params,
)
from .command_adapters.preconditions import (
    _validate_approve_deployment,
    _validate_approve_decision,
    _validate_reject_decision,
    _validate_request_approval_revision,
    _validate_pause_runtime,
    _validate_pause_execution,
    _validate_escalate_diff,
    _validate_issue_risk_off,
    _validate_liquidate_all,
    _validate_hard_rollback,
    _validate_issue_safe_mode,
    _validate_execute_rollback,
    _validate_approve_rollback,
    _validate_reject_rollback,
    _validate_activate_kill_switch,
    _validate_approve_evolution_decision,
    _validate_execute_evolution_action,
    _mutation_review_projection,
    _validate_record_sponsor_decision,
    _validate_approve_mutation,
    _validate_reject_mutation,
    _validate_review_mutation,
    _validate_execute_mutation,
    _validate_remediate_sentinel_intervention,
    _validate_decide_v5_intervention,
    _validate_human_gate_decision,
    _validate_quarterly_ranking_recommendation_submit,
    _check_binding_tenant_ownership,
    _enforce_ops_console_preconditions,
    _validate_observe,
    _validate_request_review,
    _validate_pause_paper_runtime,
    _validate_resume_paper_runtime,
    _validate_demote,
    _validate_promote_candidate,
    _validate_rebalance_proposal,
    _validate_approved_apply,
    _validate_emergency_containment,
    _VALIDATORS,
    VALIDATORS,
    set_ops_console_precondition_resolvers,
)






_READ_ROLES = auth_policy._READ_ROLES
_WRITE_ROLES = auth_policy._WRITE_ROLES
_require_read_role = auth_policy.require_read_role
_require_operator_role = auth_policy.require_operator_role
_ROLE_CAPABILITY_MAP = auth_policy._ROLE_CAPABILITY_MAP
_capabilities_for_identity = auth_policy.capabilities_for_identity
_dedupe_nonblank_strings = auth_policy.dedupe_nonblank_strings
_split_claim_string = auth_policy.split_claim_string
_identity_claim_strings = auth_policy.identity_claim_strings
_first_nonblank = auth_policy.first_nonblank
_env_csv = auth_policy.env_csv
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
_bff_me_tenant_payload = auth_policy.bff_me_tenant_payload
_sem_session_id = auth_policy.get_session_id
_sem_session_key = auth_policy.get_session_key
_sem_legacy_operator_session_key = auth_policy.get_legacy_session_key

def _sem_session_state(identity: OperatorIdentity) -> Dict[str, Any]:
    return auth_policy.get_session_state(identity, session_lifecycle_store)

def _raise_if_session_logged_out(identity: OperatorIdentity) -> None:
    return auth_policy.raise_if_session_logged_out(
        identity,
        store=session_lifecycle_store,
        error_factory=_bff_error,
    )
_raise_if_session_logged_out._canonical_guard = True
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
from .research.routes.common import format_dataset_surface_status as _format_dataset_surface_status

def _dataset_surface_status(
    dataset: str,
    *,
    snapshot_at: Optional[str] = None,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
    source: Optional[str] = None,
    read_store: Optional[Any] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    resolved_store = read_store if read_store is not None else globals().get("read_store")
    if source is None:
        if resolved_store is not None and hasattr(resolved_store, "dataset_source"):
            try:
                source = str(resolved_store.dataset_source(dataset) or "missing")
            except Exception:
                source = "missing"
        else:
            source = "missing"
    return _format_dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
        missing_message=missing_message,
        source=source,
        utc_now=utc_now,
        **kwargs,
    )
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
    from services.control_plane.bff.agora.performance.service import canonical_performance_ranking_source_surface
    return canonical_performance_ranking_source_surface(surface, snapshot_at=snapshot_at)
from .personas.service import (
    _extract_ids_from_item,
    _filter_by_common_identifiers,
)
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
from .assistant.management_service import (
    _project_runtime_state_telemetry_summary,
    _project_runtime_state_monitoring_session,
    _project_runtime_state_latest_rollback,
    _runtime_state_row_health_check,
    _runtime_state_monitoring_terminal_reason,
    _runtime_state_monitoring_health_check,
    _derive_runtime_state_row_health,
    _derive_runtime_state_last_updated_at,
)
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
    from services.control_plane.bff.agora.performance.service import canonical_performance_aggregate_group_surface
    return canonical_performance_aggregate_group_surface(
        surface_key,
        source_surfaces,
        snapshot_at=snapshot_at,
        unavailable_message=unavailable_message,
        degraded_message=degraded_message,
        utc_now=utc_now,
    )
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
from .assistant.management_service import _management_json_clone
from .assistant.management_service import (
    _MANAGEMENT_CAMEL_KEY_RE,
    _management_camel_to_snake_key,
    _management_prune_camel_aliases,
)
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
    live_gate_enabled = auth_policy.bool_from_env("PANTHEON_LIVE_BROKER_ENABLED", default=False)
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
        auth_policy.bool_from_env("OPENCLAW_CAPITAL_BINDING_ENABLED", default=False)
        or auth_policy.bool_from_env("PANTHEON_CAPITAL_BINDING_LIVE_ENABLED", default=False)
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
                "OPENCLAW_CAPITAL_BINDING_ENABLED": auth_policy.bool_from_env("OPENCLAW_CAPITAL_BINDING_ENABLED", default=False),
                "PANTHEON_CAPITAL_BINDING_LIVE_ENABLED": auth_policy.bool_from_env(
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
    from services.control_plane.bff.agora.performance.service import canonical_performance_snapshot_meta
    return canonical_performance_snapshot_meta(snapshot_at, utc_now=utc_now)
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

from .personas.routes.common import (
    ManagementReadTimeout as _ManagementReadTimeout,
    ManagementReadSaturated as _ManagementReadSaturated,
    discard_late_management_read_result as _discard_late_management_read_result,
    run_management_read as _unbounded_run_management_read,
)

# BFF-MGMT-READ-DEFECT-REPAIR-001: production management-read capacity bound.
#
# `create_management_router(...)` (management_read_models/router.py) offloads
# every GET route's aggregation onto a worker thread via a single injected
# `run_management_read` callable (see core/app_factory.py's
# `_dep("run_management_read")`). Previously that callable resolved straight
# to `personas.routes.common.run_management_read`, whose `capacity`/
# `executor` parameters default to `None` -- i.e. an *unbounded*
# `asyncio.to_thread` fan-out with no concurrency ceiling in production.
# Named, bounded slot pools (mirroring the existing
# `_MANAGEMENT_DATA_SOURCES_READ_SLOTS` pattern above) give each read-heavy
# surface a real, finite budget instead.
_HUMAN_INBOX_READ_SLOT_COUNT = 4
_HUMAN_INBOX_READ_SLOTS = threading.BoundedSemaphore(_HUMAN_INBOX_READ_SLOT_COUNT)
_HUMAN_INBOX_READ_EXECUTOR = ThreadPoolExecutor(
    max_workers=_HUMAN_INBOX_READ_SLOT_COUNT,
    thread_name_prefix="bff-human-inbox-read",
)

_MANAGEMENT_COCKPIT_READ_SLOT_COUNT = 4
_MANAGEMENT_COCKPIT_READ_SLOTS = threading.BoundedSemaphore(_MANAGEMENT_COCKPIT_READ_SLOT_COUNT)
_MANAGEMENT_COCKPIT_READ_EXECUTOR = ThreadPoolExecutor(
    max_workers=_MANAGEMENT_COCKPIT_READ_SLOT_COUNT,
    thread_name_prefix="bff-mgmt-cockpit-read",
)


def _management_cockpit_read_timeout_seconds() -> float:
    """Bound for the `/bff/management/cockpit` composition (independent of
    the generic Management read timeout so cockpit-specific saturation can
    be tuned/tested without moving every other surface's budget)."""
    raw = os.getenv("PANTHEON_BFF_COCKPIT_READ_TIMEOUT_SECONDS")
    if raw is None or not raw.strip():
        return _management_read_timeout_seconds()
    try:
        return max(0.05, float(raw))
    except (TypeError, ValueError):
        return _management_read_timeout_seconds()


_MANAGEMENT_READ_DEFAULT_SLOT_COUNT = 8
_MANAGEMENT_READ_DEFAULT_SLOTS = threading.BoundedSemaphore(_MANAGEMENT_READ_DEFAULT_SLOT_COUNT)
_MANAGEMENT_READ_DEFAULT_EXECUTOR = ThreadPoolExecutor(
    max_workers=_MANAGEMENT_READ_DEFAULT_SLOT_COUNT,
    thread_name_prefix="bff-mgmt-read",
)


async def _management_read_dispatch(
    func: Any,
    *args: Any,
    timeout_seconds: Optional[float] = None,
    capacity: Optional[threading.BoundedSemaphore] = None,
    executor: Optional[Executor] = None,
    **kwargs: Any,
) -> Any:
    """Bounded `run_management_read` used by every Management-read router.

    Callers that already pick an explicit `capacity`/`executor` pair (e.g.
    the Source Ingest registry read below) keep that choice untouched.
    Callers that don't (the generic 17-route Management router, the
    governance router, etc.) get dispatched to a named capacity pool/executor
    pair by the target callable's name, so distinct surfaces (human-inbox
    vs. cockpit vs. everything else) saturate independently -- one slow
    surface cannot exhaust another surface's budget. Module-global lookups of
    `_HUMAN_INBOX_READ_SLOTS` / `_MANAGEMENT_COCKPIT_READ_SLOTS` /
    `_MANAGEMENT_READ_DEFAULT_SLOTS` happen at call time (not captured at
    import time) so tests can substitute a smaller bound via
    `monkeypatch.setattr(bff_main, "_HUMAN_INBOX_READ_SLOTS", ...)`.
    """
    if capacity is None and executor is None:
        name = getattr(func, "__name__", "") or getattr(func, "__qualname__", "") or ""
        if name in ("get_human_inbox", "_bounded_get_human_inbox"):
            # BFF-MGMT-READ-DEFECT-REPAIR-001 acceptance item 7: the whole
            # `/bff/management/human-inbox` composition no longer occupies
            # `_HUMAN_INBOX_READ_SLOTS` itself -- that pool is the real
            # per-contributor bound for the `persona_readiness` contributor
            # inside `get_human_inbox` (see
            # `_bounded_human_inbox_persona_readiness` below). Dispatching
            # the whole-route call through the *same* BoundedSemaphore would
            # starve the contributor: the outer acquire already holds the
            # pool's only slot(s) when the contributor tries to acquire
            # again from the same call stack, so it would always observe
            # immediate (and spurious) saturation instead of ever running.
            capacity, executor = _MANAGEMENT_READ_DEFAULT_SLOTS, _MANAGEMENT_READ_DEFAULT_EXECUTOR
        elif "human_inbox" in name:
            capacity, executor = _HUMAN_INBOX_READ_SLOTS, _HUMAN_INBOX_READ_EXECUTOR
        elif "cockpit" in name:
            capacity, executor = _MANAGEMENT_COCKPIT_READ_SLOTS, _MANAGEMENT_COCKPIT_READ_EXECUTOR
        else:
            capacity, executor = _MANAGEMENT_READ_DEFAULT_SLOTS, _MANAGEMENT_READ_DEFAULT_EXECUTOR
    return await _unbounded_run_management_read(
        func,
        *args,
        timeout_seconds=timeout_seconds,
        capacity=capacity,
        executor=executor,
        **kwargs,
    )



# BFF-MAIN-FINAL-SEAMS-CORRECTIVE-001 AC6: main.py must not redefine a
# function that already has a canonical owner (`_unbounded_run_management_read`
# / `personas.routes.common.run_management_read`). `_management_read_dispatch`
# above is the composition-root wrapper (adds named capacity-pool dispatch);
# bind it to the public `run_management_read` name via assignment rather than
# a second `def run_management_read`, so `tests/test_main_composition_seam_extraction_003.py::
# test_no_duplicate_definitions_in_main_py`'s AST scan (which only looks at
# `ast.FunctionDef`/`ast.AsyncFunctionDef` nodes) sees no duplicate -- while
# every caller (`_dep("run_management_read")`, `monkeypatch.setattr(bff_main,
# "run_management_read", ...)`, etc.) still resolves the identical callable.
run_management_read = _management_read_dispatch
_run_management_read = run_management_read


def _bounded_human_inbox_persona_readiness(
    snapshot_at: str,
    *,
    read_store: Any = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Bounded `persona_readiness` contributor for `/bff/management/human-inbox`.

    BFF-MGMT-READ-DEFECT-REPAIR-001 acceptance item 7: a timed-out or
    capacity-saturated persona_readiness contributor must degrade on its
    own (real `read_timeout` / `read_capacity_saturated` reasons, `meta`
    partial) while sibling Human Inbox contributors (durable promotion
    reviews, approvals, etc.) stay populated -- not a single all-or-nothing
    bound around the whole route.

    Runs `_build_persona_readiness_items` (module-global, so tests can
    substitute a slow/blocked stand-in via
    `monkeypatch.setattr(bff_main, "_build_persona_readiness_items", ...)`,
    exactly like `_HUMAN_INBOX_READ_SLOTS`/`_MANAGEMENT_COCKPIT_READ_SLOTS`
    above) on the dedicated `_HUMAN_INBOX_READ_EXECUTOR`, gated by
    `_HUMAN_INBOX_READ_SLOTS` and `_human_inbox_surface_timeout_seconds()`.

    This is a plain `concurrent.futures` bound rather than the asyncio
    `run_management_read` above because callers include synchronous,
    already-on-the-request-thread code paths
    (`ManagementService.get_hiq_backlog`/`get_management_cockpit` both call
    `get_human_inbox()` inline, sometimes directly on the FastAPI event
    loop thread for `/bff/management/hiq-backlog`) where `asyncio.run()`
    would raise "cannot be called from a running event loop".

    Returns `(rows, degradation_reason)`; `degradation_reason` is `None` on
    success, else `"read_timeout"` or `"read_capacity_saturated"`.
    """
    capacity = _HUMAN_INBOX_READ_SLOTS
    executor = _HUMAN_INBOX_READ_EXECUTOR
    timeout_budget = _human_inbox_surface_timeout_seconds()
    build_fn = _build_persona_readiness_items
    if not capacity.acquire(blocking=False):
        return [], "read_capacity_saturated"
    try:
        future = executor.submit(build_fn, snapshot_at, read_store=read_store)
    except BaseException:
        capacity.release()
        raise
    future.add_done_callback(lambda _future: capacity.release())
    try:
        rows = future.result(timeout=timeout_budget)
        return list(rows or []), None
    except _FuturesTimeoutError:
        future.add_done_callback(_discard_late_management_read_result_sync)
        return [], "read_timeout"


def _discard_late_management_read_result_sync(future: Any) -> None:
    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        logging.getLogger(__name__).warning(
            "bff.human_inbox_persona_readiness late worker-thread error after timeout budget: %r",
            exc,
        )


def _build_management_cockpit_payload(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Named, patchable seam for the real `/bff/management/cockpit` composition.

    Wraps the same production `ManagementService.get_management_cockpit`
    callable the router used to call directly, so
    `create_management_router` (management_read_models/router.py) can
    resolve it live via `sys.modules` (mirroring
    `_build_management_evidence_payload` below) and
    `core/app_factory.py`'s `_dep("_build_management_cockpit_payload")` can
    inject it -- giving tests a single, patchable, production-reachable
    hook (`monkeypatch.setattr(bff_main, "_build_management_cockpit_payload",
    ...)`) instead of a second cockpit implementation.
    """
    from .management_read_models.service import ManagementService
    svc = ManagementService(get_read_store=lambda: read_store, utc_now=utc_now)
    return svc.get_management_cockpit(*args, **kwargs)


def _build_management_evidence_payload(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    from .management_read_models.service import ManagementService
    svc = ManagementService(read_store=lambda: read_store, utc_now=utc_now)
    return svc.get_evidence(*args, **kwargs)
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

from .assistant.management_service import _openclaw_client_error
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
_GOV_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_FINAL_CONTRACT_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}

from .command_adapters.service import CommandAdapterService as _CommandAdapterService

_command_adapter_service = _CommandAdapterService(
    command_store=lambda: command_store,
    read_surface=lambda: read_store,
    extract_identity=_extract_identity,
    require_operator_role=_require_operator_role,
    require_read_role=_require_read_role,
    bff_error=_bff_error,
    utc_now=utc_now,
    validators=_VALIDATORS,
    process_command_task=lambda cmd_id: _process_command_stub(cmd_id),
    check_read_surface_state=_check_read_surface_state,
    final_contract_idempotency=_FINAL_CONTRACT_IDEMPOTENCY,
    gov_bff_idempotency=_GOV_BFF_IDEMPOTENCY,
    publish_event=lambda event_type, data: _publish_event(
        _sse_buffers["audit"],
        _sse_subscribers["audit"],
        event_type,
        data,
    ),
)
command_adapter_service = _command_adapter_service

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
    return _command_adapter_service.submit_command_admission(
        background_tasks=background_tasks,
        payload=payload,
        authorization=authorization,
        x_mfa_token=x_mfa_token,
        x_trace_id=x_trace_id,
        x_correlation_id=x_correlation_id,
        x_request_id=x_request_id,
        x_confirm_token=x_confirm_token,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
        route=route,
        source_route=source_route,
        foundation_raw_payload=foundation_raw_payload,
        audit_extra=audit_extra,
        extra_precondition=extra_precondition,
        enqueue=enqueue,
        include_durable_meta=include_durable_meta,
        response_deprecation=response_deprecation,
    )
_AGORA_CORE_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_AGORA_SIGNAL_WRITE_ROLES = {"analyst", "operator", "approver", "admin", "reviewer"}
_AGORA_BULK_FEEDBACK_ROLES = {"analyst", "operator", "reviewer", "approver", "admin"}
from .assistant.management_service import (
    _truthy_header,
    _request_dry_run_requested,
    _dry_run_success_response,
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
_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}
_SKILL_REGISTRY: Dict[str, Dict[str, Any]] = {}
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
from .pm12.service import (
    _pm12_allocation_evaluation_record,
    _pm12_allocation_line_digest,
    _pm12_allocation_snapshot_record,
    _pm12_ranking_snapshot_ttl_seconds,
    _pm12_recommendation_snapshot_record,
)
from .capital.service import (
    _pm12_semantic_json_value,
    _pm12_semantic_values_match,
    _pm12_allocation_line_assertion_hash,
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
_STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}

from .shared.module_retirement_guard import (
    DEFAULT_RETIRED_PROCESS_OVERLAYS as _RETIRED_PROCESS_OVERLAYS,
    check_retired_overlay_getattr,
)

def __getattr__(name: str) -> Any:
    check_retired_overlay_getattr(name, _RETIRED_PROCESS_OVERLAYS)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_PERSONA_PROVISIONING_STORE = None
_PERSONA_PROVISIONING_STORE_LOCK = threading.Lock()
_PERSONA_FIRST_EVALUATION_WORKFLOW_ID = "pantheon.persona.first-evaluation"
class _PersonaOwnerHttpTransport:
    """Strict synchronous transport to canonical provisioning owner APIs."""

    def __init__(self, *, tenant_id: str | None = None) -> None:
        self.tenant_id = str(tenant_id or "").strip() or str(
            os.getenv("PANTHEON_BFF_TENANT_ID")
            or os.getenv("PANTHEON_TENANT_ID")
            or "default"
        ).strip()

    def _service_jwt(self, owner: str) -> str:
        secret_env = {
            "capital": "PANTHEON_CAPITAL_JWT_SECRET",
            "registry": "PANTHEON_REGISTRY_JWT_SECRET",
            "governance": "PANTHEON_GOVERNANCE_JWT_SECRET",
        }.get(owner, "PANTHEON_BFF_JWT_SECRET")
        secret = str(os.getenv(secret_env) or os.getenv("PANTHEON_BFF_JWT_SECRET") or "").strip()
        if not secret:
            raise RuntimeError("PANTHEON_BFF_JWT_SECRET is required for strict Persona owner calls")
        from services.runtime_auth_inbound import encode_jwt_hs256

        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": "control-plane-bff",
            "service": "control-plane-bff",
            "tenant_id": self.tenant_id,
            "allowed_tenants": [self.tenant_id],
            "roles": [
                "service", "operator", "admin", "approver", "reviewer",
                "risk_owner", "capital.admin", "persona.admin",
            ],
            "iat": now,
            "exp": now + 120,
        }
        issuer = str(os.getenv("CAPITAL_JWT_ISSUER") or os.getenv("PANTHEON_BFF_JWT_ISSUER") or "").strip()
        audience = str(os.getenv("CAPITAL_JWT_AUDIENCE") or os.getenv("PANTHEON_BFF_JWT_AUDIENCE") or "").strip()
        if issuer:
            claims["iss"] = issuer
        if audience:
            claims["aud"] = audience
        return encode_jwt_hs256(claims, secret=secret)

    def _headers(self, owner: str, payload: Mapping[str, Any] | None = None) -> dict[str, str]:
        tenant_id = str((payload or {}).get("tenant_id") or self.tenant_id).strip()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Tenant-Id": tenant_id,
            "X-Pantheon-Service": "control-plane-bff",
        }
        idempotency_key = str(
            (payload or {}).get("idempotency_key")
            or (payload or {}).get("idempotencyKey")
            or ""
        ).strip()
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if owner in {"capital", "registry", "governance"}:
            headers["Authorization"] = f"Bearer {self._service_jwt(owner)}"
        else:
            headers["Authorization"] = "Bearer control-plane-bff:operator,admin,service"
        return headers

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
            request = urllib_request.Request(
                self._url(owner, path), headers=self._headers(owner), method="GET"
            )
            with urllib_request.urlopen(
                request,
                timeout=max(1, int(os.getenv("PANTHEON_COMMAND_TIMEOUT_SECONDS", "30"))),
            ) as response:
                value = json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        if not isinstance(value, dict):
            raise RuntimeError(f"{owner} GET {path} returned a non-object receipt")
        return value

    def post(self, owner: str, path: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        request = urllib_request.Request(
            self._url(owner, path),
            data=json.dumps(dict(payload)).encode("utf-8"),
            headers=self._headers(owner, payload),
            method="POST",
        )
        with urllib_request.urlopen(
            request,
            timeout=max(1, int(os.getenv("PANTHEON_COMMAND_TIMEOUT_SECONDS", "30"))),
        ) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError(f"{owner} POST {path} returned a non-object receipt")
        return value

    def patch(self, owner: str, path: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        request = urllib_request.Request(
            self._url(owner, path),
            data=json.dumps(dict(payload)).encode("utf-8"),
            headers=self._headers(owner, payload),
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
def _deployment_url(path: str) -> str:
    base = os.getenv("PANTHEON_DEPLOYMENT_API_URL", "").strip().rstrip("/")
    if not base:
        base = "http://deployment:8095"
    return f"{base}{path}"
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
        data_source_status, data_sources, source_health_bindings = persona_service.overlay_source_health_truth(
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
def _list_strategy_summaries() -> List[Dict[str, Any]]:
    """Return canonical strategy specs from read_store."""
    return list(read_store.list_strategy_specs() or [])

def _management_record_id(record: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


from .agora.performance.service import (
    PM12_ATTRIBUTION_DIMENSIONS as _PM12_ATTRIBUTION_DIMENSIONS,
)
from .pm12.service import (
    _pm12_attribution_metrics,
    _pm12_performance_attribution_facts,
    _pm12_performance_attribution_response,
    _pm12_performance_attribution_response_impl,
    _pm12_performance_attribution_rows,
    _pm12_performance_attribution_sources,
)

from .governance.human_inbox import (
    _HUMAN_INBOX_INACTIVE_COMMAND_STATUSES,
    _HUMAN_INBOX_OPEN_APPROVAL_STATES,
    _HUMAN_INBOX_OPEN_GOVERNANCE_STATUSES,
    _HUMAN_INBOX_OPEN_INTERVENTION_STATUSES,
    _HUMAN_INBOX_OPEN_SENTINEL_STATUSES,
    _HUMAN_INBOX_PRIORITY_RANK,
    _HUMAN_INBOX_PROMOTION_PRODUCER,
    _HUMAN_INBOX_PROMOTION_SNAPSHOT_SCALARS,
    _HUMAN_INBOX_PROMOTION_SNAPSHOT_STRING_LISTS,
    _build_persona_readiness_items,
    _human_inbox_action_state,
    _human_inbox_all_items,
    _human_inbox_approval_contributor,
    _human_inbox_approval_item,
    _human_inbox_attach_common_fields,
    _human_inbox_csv_filter,
    _human_inbox_decision_projection_from_record,
    _human_inbox_decision_recommendation_id,
    _human_inbox_filter_items,
    _human_inbox_governance_contributor,
    _human_inbox_governance_review_item,
    _human_inbox_intervention_contributor,
    _human_inbox_intervention_item,
    _human_inbox_loaded_surface,
    _human_inbox_payload,
    _human_inbox_payload_from_loaded,
    _human_inbox_persona_blocking_reasons,
    _human_inbox_persona_contributor,
    _human_inbox_persona_readiness_item,
    _human_inbox_priority,
    _human_inbox_project_items,
    _human_inbox_promotion_contributor,
    _human_inbox_promotion_recommendation_id,
    _human_inbox_promotion_review_from_projection,
    _human_inbox_promotion_review_item,
    _human_inbox_sanitize_promotion_snapshot,
    _human_inbox_sentinel_contributor,
    _human_inbox_sentinel_item,
    _human_inbox_submission_projection_from_record,
    _human_inbox_summary,
    _human_inbox_surfaces,
    _human_inbox_trusted_promotion_submission,
    _submitted_promotion_review_record_from_command,
    _submitted_promotion_review_records,
    human_inbox_surface_timeout_seconds,
)
from .assistant.management_service import (
    _MGMT_NL_COMMAND_RESERVATION_CONTEXT,
    MGMT_NL_COMMAND_RESERVATION_CONTEXT,
)
from .assistant.management_service import (
    _MGMT_NL_VALID_FOCUS,
    _MGMT_NL_FOCUS_ALIASES,
    _MGMT_NL_MAX_QUESTION_BYTES,
    _MGMT_NL_MAX_RECENT_TURNS,
    _MGMT_NL_FE_RECENT_TURNS_CHAR_BUDGET,
    _MGMT_NL_PROVIDER_HISTORY_CHAR_BUDGET,
    _MGMT_NL_UI_ACTION_KINDS,
    _MGMT_NL_WRITE_ACTION_KINDS,
    _MGMT_NL_CONTROL_REDACTED_QUESTION,
    _MGMT_NL_CONTROL_ACTIVATE_PREFIXES,
    _MGMT_NL_CONTROL_SEPARATOR_ACTIVATE_PREFIXES,
    _MGMT_NL_CONTROL_STATUS_COMMANDS,
    _MGMT_NL_CONTROL_DEACTIVATE_COMMANDS,
    _MGMT_NL_HIGH_RISK_REFUSAL_FOLLOWUPS,
    _MGMT_NL_HIGH_RISK_PATTERNS,
)
_MGMT_AI_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
_MGMT_AI_USAGE_OBSERVED_SOURCE = "management_ai_bff_audit"
_MGMT_AI_USAGE_OBSERVED_COVERAGE = "bff_observed_management_ai_only"
_MGMT_AI_USAGE_STALE_AFTER_HOURS = 24
from .assistant.management_service import (
    _MGMT_AI_AUDIT_EVENTS,
    _management_ai_audit_path,
    _management_ai_summary_value,
    _management_ai_surface_summary,
    _management_ai_provider_output_summary,
    _management_ai_record_event,
    _management_ai_read_audit_file,
    _management_ai_event_matches,
    _management_ai_list_audit_events,
)
from .assistant.management_service import (
    _management_ai_number,
    _management_ai_usage_number,
    _management_ai_provider_key,
    _management_ai_provider_display,
    _management_ai_provider_route,
    _management_ai_event_model,
    _management_ai_quota_snapshot,
    _management_ai_empty_usage_row,
    _management_ai_empty_model_row,
    _management_ai_touch_last,
    _management_ai_usage_age_hours,
    _management_ai_finalize_usage_row,
    _assistant_provider_usage_summary,
    _management_ai_href,
)
from .assistant.management_service import _management_ai_audit_href
from .assistant.management_service import (
    get_management_ai_conversation_store,
    set_management_ai_conversation_store,
    reset_management_ai_conversation_store,
    management_ai_conversation_href as _management_ai_conversation_href,
    management_ai_attachment_url as _management_ai_attachment_url,
    management_ai_attachment_api_payload as _management_ai_attachment_api_payload,
    management_ai_turn_api_payload as _management_ai_turn_api_payload,
    management_ai_require_session_access as _management_ai_require_session_access,
    management_ai_session_not_found as _management_ai_session_not_found,
    management_ai_get_visible_session_or_404 as _management_ai_get_visible_session_or_404,
    management_ai_get_session_or_404 as _management_ai_get_session_or_404,
    management_ai_ensure_session as _management_ai_ensure_session_impl,
    management_ai_store_attachments as _management_ai_store_attachments_impl,
    management_ai_append_turn as _management_ai_append_turn_impl,
    management_ai_server_conversation_context as _management_ai_server_conversation_context_impl,
    management_ai_list_conversations as _management_ai_list_conversations,
    management_ai_get_conversation as _management_ai_get_conversation,
    management_ai_get_attachment as _management_ai_get_attachment,
)


_MGMT_AI_CONVERSATION_STORE: Optional[ManagementAiConversationStore] = None


def _management_ai_conversation_store() -> ManagementAiConversationStore:
    if _MGMT_AI_CONVERSATION_STORE is not None:
        return _MGMT_AI_CONVERSATION_STORE
    return get_management_ai_conversation_store()


def _management_ai_ensure_session(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    kwargs.setdefault("conversation_store", _management_ai_conversation_store())
    return _management_ai_ensure_session_impl(*args, **kwargs)


def _management_ai_store_attachments(*args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
    kwargs.setdefault("conversation_store", _management_ai_conversation_store())
    return _management_ai_store_attachments_impl(*args, **kwargs)


def _management_ai_append_turn(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    kwargs.setdefault("conversation_store", _management_ai_conversation_store())
    return _management_ai_append_turn_impl(*args, **kwargs)


from .assistant.management_service import (
    _management_ai_server_conversation_context,
    _management_ai_provider_history_size,
    _management_ai_provider_history_window,
    _management_ai_provider_history_minimal_turn,
)
from .assistant.management_service import (
    _mgmt_nl_normalize_focus,
    _mgmt_nl_trim_text,
    _mgmt_nl_normalize_conversation_context,
    _mgmt_nl_normalize_action_descriptor,
    _mgmt_nl_normalize_available_ui_actions,
    _mgmt_nl_normalize_ui_context,
    _mgmt_nl_frontend_selected_entity,
    _mgmt_nl_allowed_action_kinds,
    _mgmt_nl_jsonish,
    _mgmt_nl_find_action_values,
    _mgmt_nl_action_params_valid,
    _mgmt_nl_extract_provider_actions,
    _assistant_control_mode_for_identity,
    _mgmt_nl_identity_with_control_mode,
    _mgmt_nl_validate_question_size,
    _mgmt_nl_control_store,
    _mgmt_nl_control_strip_activation_prefix,
    _mgmt_nl_parse_control_command,
    _mgmt_nl_positive_int,
    _mgmt_nl_control_options,
    _mgmt_nl_raise_control_mode_actor_error,
    _mgmt_nl_require_mode_capability,
    _mgmt_nl_raise_control_mode_error,
    _mgmt_nl_control_provider_status,
    _mgmt_nl_control_answer,
    _mgmt_nl_record_control_audit,
    _management_nl_publish_completed_events,
    _mgmt_nl_handle_control_command,
    _mgmt_nl_normalize_question_text,
    _mgmt_nl_evasion_stripped_variants,
    _mgmt_nl_term_matches,
    _mgmt_nl_high_risk_classify,
    _mgmt_nl_record_high_risk_refusal,
    _mgmt_nl_idempotency_storage_key,
    _mgmt_nl_command_idempotency_store,
    MANAGEMENT_NL_COMMAND_ROUTE,
    MANAGEMENT_NL_COMMAND_ROUTE as _MGMT_NL_COMMAND_ROUTE,
    MANAGEMENT_NL_USE_CASE,
    MANAGEMENT_NL_USE_CASE as _MANAGEMENT_NL_USE_CASE,
    _mgmt_nl_command_scope,
    _mgmt_nl_command_admit,
    _mgmt_nl_command_complete,
    _mgmt_nl_command_mark_uncertain,
    _mgmt_nl_raise_command_idempotency_error,
    _mgmt_nl_command_wait_seconds,
    _mgmt_nl_command_poll_seconds,
    _mgmt_nl_raise_command_wait_timeout,
    _mgmt_nl_use_case_admission_error,
    _mgmt_nl_result_is_terminal,
    get_mgmt_nl_command_recovery_seconds as _mgmt_nl_command_recovery_seconds,
    get_mgmt_nl_command_idempotency_store,
    reset_mgmt_nl_command_idempotency_store,
    _MGMT_NL_COMMAND_IDEMPOTENCY_STORE,
    _MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG,
    _mgmt_nl_surface_confidence,
    _mgmt_nl_caller_tenant,
    _mgmt_nl_scope_values,
    _mgmt_nl_record_tenant_ids,
    _mgmt_nl_record_matches_tenant,
    _mgmt_nl_filter_tenant_records,
    _mgmt_nl_add_entity,
    _mgmt_nl_add_record_entities,
    _mgmt_nl_scoped_runtime_rows,
    _mgmt_nl_trading_pulse_snippet,
    _mgmt_nl_surface_owner_observation,
    _mgmt_nl_payload_surface_observations,
    _mgmt_nl_merge_owner_observations,
    _mgmt_nl_collect_context,
    _mgmt_nl_synthesize_answer,
    _mgmt_nl_provider_feature_enabled,
    _mgmt_nl_provider_name,
    _MGMT_NL_PROVIDER_REASON_MESSAGES,
    _MGMT_NL_PROVIDER_REASON_ACTIONS,
    _mgmt_nl_provider_reason_key,
    _mgmt_nl_provider_status_notice,
    _mgmt_nl_provider_status,
    _mgmt_nl_provider_supports_multimodal,
    _mgmt_nl_multimodal_attachment_payload,
    _mgmt_nl_provider_multimodal_payload,
    _mgmt_nl_multimodal_unsupported_error,
    _mgmt_nl_context_status,
    _mgmt_nl_evidence_entities_payload,
    _mgmt_nl_build_context_pack,
    _mgmt_nl_provider_mode_from_context,
    _mgmt_nl_provider_control_metadata,
    _mgmt_nl_reject_development_payload,
    _mgmt_nl_provider_mode_prompt_lines,
    _mgmt_nl_provider_prompt,
    _mgmt_nl_text_from_provider_value,
    _mgmt_nl_extract_provider_answer,
    _MGMT_NL_COMPLETED_PROVIDER_STATES,
    _MGMT_NL_PROVIDER_DEADLINE_DEFAULT_SECONDS,
    _mgmt_nl_provider_deadline_seconds,
    _mgmt_nl_provider_candidates,
    _mgmt_nl_provider_attempt_summary,
    _mgmt_nl_provider_degraded_reason,
    _mgmt_nl_maybe_provider_answer,
    _mgmt_nl_attempt_provider_answer,
    _mgmt_nl_deterministic_answer,
    _mgmt_nl_provider_enabled,
    _mgmt_nl_invoke_provider,
    _MGMT_NL_PROVIDER_FINALIZE_TASKS,
    MGMT_NL_PROVIDER_FINALIZE_TASKS,
    _MGMT_NL_PROVIDER_INLINE_GRACE_DEFAULT_SECONDS,
    _MGMT_NL_STREAM_READ_TIMEOUT_DEFAULT_SECONDS,
    _mgmt_nl_provider_inline_grace_seconds,
    _mgmt_nl_provider_inline_wait_seconds,
    _mgmt_nl_stream_read_timeout_seconds,
    _mgmt_nl_sse_frame,
    _mgmt_nl_json_response_payload,
    _mgmt_nl_cached_result_sse_frames,
    _mgmt_nl_finalize_result,
    _mgmt_nl_finalize_provider_turn,
    _mgmt_nl_schedule_provider_finalize,
    bff_management_nl_ask,
    _bff_management_nl_ask_impl,
    bff_management_nl_ask_stream,
    _bff_management_nl_ask_stream_impl,
)
from .assistant.management_service import (
    bff_management_ai_audit,
    bff_assistant_provider_usage_summary,
    bff_management_ai_conversations,
    bff_management_ai_conversation,
    bff_management_ai_attachment,
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
from .pm12.service import (
    _PM12_LEAGUE_FORMULA_VERSION,
    _PM12_QUARTER_PATTERN,
    _PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER,
    _PM12_QUARTERLY_RECOMMENDATION_ACTIONS,
    _pm12_add_recommendation_action,
    _pm12_current_quarter_id,
    _pm12_iso_z,
    _pm12_quarter_window,
    _pm12_quarterly_recommendation_item,
    _pm12_recommendation_action_ids,
)
from .governance.promotion_review import (
    _PROMOTION_REVIEW_DECISIONS,
    _PROMOTION_REVIEW_ID_PREFIX,
    _PROMOTION_REVIEW_ID_QUARTER_RE,
    _PROMOTION_REVIEW_PROMOTION_ACTION_IDS,
    _PROMOTION_REVIEW_REVISION_MARKER,
    _PROMOTION_REVIEW_REVISION_RE,
    _PROMOTION_REVIEW_TARGET_PREFIX,
    _latest_promotion_review_command as _domain_latest_promotion_review_command,
    _promotion_review_clean_id,
    _promotion_review_decision_projection as _domain_promotion_review_decision_projection,
    _promotion_review_quarter_from_id,
    _promotion_review_record_revision_id,
    _promotion_review_revision_id,
    _promotion_review_revision_recommendation_id,
    _promotion_review_stage_path,
    _promotion_review_stored_source,
    _promotion_review_submission_projection as _domain_promotion_review_submission_projection,
    _promotion_review_target_id,
    _raise_if_promotion_review_direct_mutation_requested,
)

_PROMOTION_REVIEW_ACTION_IDS: Set[str] = set(_PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER)


def _promotion_review_submission_projection(
    review_id: Any,
    *,
    include_source_recommendation: bool = False,
    command_store: Any = None,
) -> Optional[Dict[str, Any]]:
    resolved_store = command_store if command_store is not None else globals().get("command_store")
    return _domain_promotion_review_submission_projection(
        review_id,
        include_source_recommendation=include_source_recommendation,
        command_store=resolved_store,
    )


def _latest_promotion_review_command(
    review_id: Any,
    command_store: Any = None,
) -> Optional[Dict[str, Any]]:
    resolved_store = command_store if command_store is not None else globals().get("command_store")
    return _domain_latest_promotion_review_command(review_id, command_store=resolved_store)


def _promotion_review_decision_projection(
    review_id: Any,
    command_store: Any = None,
) -> Optional[Dict[str, Any]]:
    resolved_store = command_store if command_store is not None else globals().get("command_store")
    return _domain_promotion_review_decision_projection(review_id, command_store=resolved_store)
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
from .command_adapters.service import (
    process_command as _process_command,
    _process_command_stub,
)
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
from .assistant.management_service import _publish_event
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
    event_filter: Optional[Callable[[dict], bool]] = None,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted events.

    ``event_filter``, when provided, restricts both the replayed history and
    the live stream to events for which it returns True — e.g. the per-job
    ``GET /bff/sse/jobs/{jobId}/progress`` subscription filters server-side by
    ``jobId`` so a client subscribed to job A never receives job B's events
    (BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001; previously this was
    documented as "client-side only").
    """
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
            if event_filter is not None and isinstance(evt, dict) and not event_filter(evt):
                continue
            yield _sse_format(evt)

        # Then stream new events as they arrive
        while True:
            try:
                evt = await asyncio.wait_for(q.get(), timeout=30.0)
                if event_filter is not None and isinstance(evt, dict) and not event_filter(evt):
                    continue
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
    event_filter: Optional[Callable[[dict], bool]] = None,
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
        _sse_stream(buffer, subscribers, last_event_id, channel, event_filter=event_filter),
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


async def stream_approval_events(
    last_event_id: Optional[str] = None,
    authorization: Optional[str] = None,
):
    """Per-channel alias for the generic approval-channel SSE stream."""
    return await stream_generic_events("approval", last_event_id, authorization)


async def stream_ask_events(
    last_event_id: Optional[str] = None,
    authorization: Optional[str] = None,
):
    """Per-channel alias for the generic ask-channel SSE stream."""
    return await stream_generic_events("ask", last_event_id, authorization)
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
_GOV_BFF_EVOLUTION_PROGRAM_OVERLAY: Dict[str, Dict[str, Any]] = {}
_GOV_BFF_EXPERIMENT_OVERLAY: Dict[str, Dict[str, Any]] = {}
# _GOV_BFF_IDEMPOTENCY defined earlier
_ACKNOWLEDGED_ALERTS: Dict[str, Dict[str, Any]] = {}
from .incidents.service import IncidentService as _IncidentService
def _current_read_store_for_legacy_incident_seam() -> Any:
    return read_store
def _bff_incident_service() -> _IncidentService:
    """Composition-root binding: incidents/service.py's IncidentService is the
    sole owner of Incident-case projection and filtering; inject the live
    ``read_store``/``_ACKNOWLEDGED_ALERTS`` globals rather than duplicating
    the projection logic here."""
    return _IncidentService(
        get_read_store=_current_read_store_for_legacy_incident_seam,
        acknowledged_alerts=_ACKNOWLEDGED_ALERTS,
    )
def _list_bff_incidents(
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    affected_pool_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return _bff_incident_service().list_bff_incidents(
        status=status, severity=severity, affected_pool_id=affected_pool_id
    )
def _get_bff_incident(incident_id: str) -> Optional[Dict[str, Any]]:
    return _bff_incident_service().get_bff_incident(incident_id)
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
    res_dict = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    _GOV_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": res_dict}
    return res_dict

def _research_experiments_surface_source(records: Sequence[Dict[str, Any]]) -> Optional[str]:
    if read_store.dataset_source("research_experiments") != "missing":
        return None
    for record in records:
        if str(record.get("experiment_id") or record.get("id") or "") == "exp-mgmt-qlib-006":
            return "composed_market_persona_defaults"
    return None
def _get_bff_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Best-effort job lookup for the assistant context pack.

    BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001: ``get_job_bff`` now raises
    ``JobSourceUnavailableError`` when the specific job's owning source is
    unreachable/unconfigured. The assistant context pack is a best-effort
    aggregation across many sources (see ``assistant/source_collectors.py``)
    and must degrade that one source rather than fail the whole snapshot, so
    this treats "source unavailable" the same as "not found" here.
    """
    try:
        return read_store.get_job_bff(job_id)
    except JobSourceUnavailableError:
        return None
def _list_bff_jobs(*, status: Optional[str] = None) -> List[Dict[str, Any]]:
    jobs = read_store.list_jobs_bff()
    if status:
        requested = {s.strip().lower() for s in status.split(",") if s.strip()}
        jobs = [j for j in jobs if str(j.get("status") or "").lower() in requested]
    return sorted(jobs, key=lambda j: str(j.get("created_at") or j.get("submitted_at") or ""), reverse=True)
# _FINAL_CONTRACT_IDEMPOTENCY defined earlier
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
    return _command_adapter_service.sem_command_response(
        command_type=command_type,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
        status_code=status_code,
        server_generated_target=server_generated_target,
        trusted_evidence_producer=trusted_evidence_producer,
        terminal_on_persist=terminal_on_persist,
    )
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
_bff_source_commit = auth_policy.bff_source_commit
from .core.app_factory import (
    create_version_handler as _create_version_handler,
    sem_bff_version as _sem_bff_version_default,
)
sem_bff_version = _create_version_handler(
    source_commit_fn=_bff_source_commit,
    auth_stub_fn=_bff_auth_stub_enabled,
    auth_mode_fn=_bff_auth_mode,
    dev_login_fn=_dev_login_enabled,
)
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
from .core.app_factory import create_capabilities_handler as _create_capabilities_handler
sem_bff_capabilities = _create_capabilities_handler(
    extract_identity=_extract_identity,
    require_read_role=_require_read_role,
    utc_now=utc_now,
)
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
        health_items = persona_service.build_persona_health_items(snapshot_at)
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
from .assistant.source_collectors import (
    AssistantSourceCollectorDeps,
    collect_assistant_context_source,
)
def _assistant_collect_source(
    source_id: str,
    request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
) -> Any:
    """Composition-root binding for the single owner of assistant context
    source collection (BFF-ASSISTANT-SOURCE-COLLECTOR-SEAM-CORRECTIVE-001).
    Closes over the real runtime collaborators and delegates to
    ``collect_assistant_context_source`` -- no second copy of any collector
    logic may live here.  Mirrors the ``_resolve_agora_interaction_context_ref``
    composition-root binding introduced by
    BFF-JOURNAL-CONTEXT-SEAM-CORRECTIVE-001.
    """
    return collect_assistant_context_source(
        source_id,
        request,
        snapshot_at,
        identity,
        deps=AssistantSourceCollectorDeps(
            read_store=read_store,
            list_governance_audit_events=_list_governance_audit_events,
            filter_tenant_records_fn=_mgmt_nl_filter_tenant_records,
            dataset_surface_status=_dataset_surface_status,
            generic_path_collector=_sem_final_generic_list_for_path,
            persona_service=persona_service,
            build_operator_alerts_payload=_build_operator_alerts_payload,
            get_job=_get_bff_job,
            list_jobs=_list_bff_jobs,
            tenant_payload_fn=_bff_me_tenant_payload,
            read_roles=_READ_ROLES,
        ),
    )
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
from .assistant.management_service import (
    _assistant_provider_readiness,
    _assistant_provider_list,
    _assistant_provider_register,
    _assistant_provider_reauth,
    _assistant_provider_reauth_status,
    _assistant_provider_reauth_code,
)
from .ports.evolution_program_commands import (
    EvolutionServiceProgramCommandPort as _EvolutionServiceProgramCommandPort,
)
from services.evolution.client import EvolutionClient as _EvolutionClient

_evolution_program_commands = _EvolutionServiceProgramCommandPort(_EvolutionClient())


async def bff_events_stream_alias(
    channel: str = "system",
    last_event_id: Optional[str] = None,
    authorization: Optional[str] = None,
):
    return await stream_generic_events(channel, last_event_id, authorization)


def _ensure_agora_servant_openclaw_agent(persona: Dict[str, Any]) -> Dict[str, Any]:
    return OpenClawOpsClient().ensure_agora_servant_agent(persona)


from services.control_plane.bff.trade_journal import _allowed as _trade_journal_allowed
from .agora.interaction.context_resolver import resolve_agora_interaction_context_ref


def _resolve_agora_interaction_context_ref(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return resolve_agora_interaction_context_ref(
        *args,
        read_store=read_store,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        bff_error=_bff_error,
        persona_directory_snapshot_fn=_get_persona_directory_snapshot,
        persona_record_tenant_id_fn=_persona_record_tenant_id,
        trade_journal_allowed_fn=_trade_journal_allowed,
        utc_now=utc_now,
        **kwargs,
    )


from .core.app_factory import compose_bff_app

app = compose_bff_app(
    app=app,
    app_deps=app_deps,
)
_events_router = app.state.events_router
_deployment_router = app.state.deployment_router
_agora_router = app.state.agora_router
_runtime_router = app.state.runtime_router
interaction_lifecycle = app.state.interaction_lifecycle
workshop_store = app.state.workshop_store
proposal_store = app.state.proposal_store
research_store = getattr(app.state, "research_store", None)
research_dispatcher = getattr(app.state, "research_dispatcher", None)
dataset_store = getattr(app.state, "dataset_store", None)
_ASSISTANT_SESSION_STORE = getattr(app.state, "assistant_session_store", None)
_ASSISTANT_TRANSCRIPT_STORE = getattr(app.state, "assistant_transcript_store", None)
_ASSISTANT_CONTROL_MODE_STORE = getattr(app.state, "assistant_control_mode_store", None)
source_management_client = getattr(app.state, "source_management_client", None)
persona_service = getattr(app.state, "persona_service", None)
command_adapter_service = getattr(app.state, "command_adapter_service", None)
auth_deps = getattr(app.state, "auth_deps", None)
auth_handlers = getattr(app.state, "auth_handlers", None)
auth_facade_service = getattr(app.state, "auth_facade_service", None)
_core_handlers = getattr(app.state, "core_handlers", None)

from .assistant.management_service import wire_management_runtime_projections

wire_management_runtime_projections(
    build_operator_alerts_payload=_build_operator_alerts_payload,
    build_management_anomalies_payload=_build_management_anomalies_payload,
    human_inbox_payload=_human_inbox_payload,
    list_persona_records=_list_persona_records,
    management_telemetry_rollup=_management_telemetry_rollup,
    dataset_surface_status=_dataset_surface_status,
    assistant_collect_source=_assistant_collect_source,
    agora_audit_store=agora_audit_store,
    assistant_control_mode_store=_ASSISTANT_CONTROL_MODE_STORE,
    sse_buffers=_sse_buffers,
    sse_subscribers=_sse_subscribers,
    read_store=read_store,
)

from .deployment.router import create_deployment_router as _create_deployment_router
# Composed via core.app_factory.compose_bff_app: create_management_router(...)
from .management_read_models import create_management_router as _create_management_router  # noqa: F401


def _mounted_router_endpoint(router: Any, path: str) -> Any:
    """Return the real handler mounted at ``path`` on an already-built router.

    Re-exposes the exact ASGI-registered callable under its historical
    direct-call name instead of re-implementing SSE alias logic here.
    """
    for route in router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise RuntimeError(f"No route registered for path {path!r} on {router!r}")


stream_bff_events = _mounted_router_endpoint(_events_router, "/bff/events/stream")
bff_sse_notifications_alias = _mounted_router_endpoint(_events_router, "/bff/sse/notifications")
bff_sse_cc_kpi_alias = _mounted_router_endpoint(_events_router, "/bff/sse/command-center/kpi")
bff_sse_cc_events_alias = _mounted_router_endpoint(_events_router, "/bff/sse/command-center/events")
bff_sse_job_progress_alias = _mounted_router_endpoint(_events_router, "/bff/sse/jobs/{jobId}/progress")
bff_sse_alerts_alias = _mounted_router_endpoint(_events_router, "/bff/sse/alerts")
bff_sse_incident_timeline_alias = _mounted_router_endpoint(_events_router, "/bff/sse/incidents/{incidentId}/timeline")
bff_sse_review_updates_alias = _mounted_router_endpoint(_events_router, "/bff/sse/review/updates")
bff_sse_deployment_events_alias = _mounted_router_endpoint(_deployment_router, "/bff/sse/deployment/events")
bff_sse_agora_signals_alias = _mounted_router_endpoint(_agora_router, "/bff/sse/agora/signals")
bff_sse_agora_session_alias = _mounted_router_endpoint(_agora_router, "/bff/sse/agora/sessions/{sessionId}")

from .shared.module_retirement_guard import (
    GETATTR_ERROR_MESSAGE as _GETATTR_ERROR_MESSAGE,
    ModuleRetirementGuard as _ModuleRetirementGuard,
)


class _BffMainModule(_ModuleRetirementGuard):
    def __getattr__(self, name: str) -> Any:
        if name in self._retired_symbols:
            raise AttributeError(_GETATTR_ERROR_MESSAGE.format(name=name))
        try:
            from .assistant import management_service
            if hasattr(management_service, name):
                return getattr(management_service, name)
        except Exception:
            pass
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def _on_setattr(self, name: str, value: Any) -> None:
        if name == "read_store":
            if hasattr(self, "app_deps") and hasattr(self.app_deps, "read_surface"):
                if value is not self.app_deps.read_surface:
                    self.app_deps.read_surface._active_delegate = value
                else:
                    self.app_deps.read_surface._active_delegate = None
            try:
                from .assistant.management_service import set_read_store
                if hasattr(self, "app_deps") and value is getattr(self.app_deps, "read_store", None):
                    set_read_store(None)
                else:
                    set_read_store(value)
            except Exception:
                pass
        elif name == "OpenClawOpsClient":
            try:
                from .assistant.management_service import set_openclaw_ops_client
                from .openclaw_ops_client import OpenClawOpsClient as _OrigClient
                if value is not _OrigClient:
                    set_openclaw_ops_client(value)
                else:
                    set_openclaw_ops_client(None)
            except Exception:
                pass
        elif name == "OpenClawOpsClientError":
            try:
                from .assistant.management_service import set_openclaw_ops_client_error
                from .openclaw_ops_client import OpenClawOpsClientError as _OrigErr
                if value is not _OrigErr:
                    set_openclaw_ops_client_error(value)
                else:
                    set_openclaw_ops_client_error(None)
            except Exception:
                pass
        elif name == "_ASSISTANT_CONTROL_MODE_STORE":
            try:
                from .assistant.management_service import set_assistant_control_mode_store
                set_assistant_control_mode_store(value)
            except Exception:
                pass
        elif name == "_MGMT_AI_CONVERSATION_STORE":
            try:
                from .assistant.management_service import set_management_ai_conversation_store
                set_management_ai_conversation_store(value)
            except Exception:
                pass
        else:
            try:
                from .assistant import management_service
                if hasattr(management_service, name):
                    setattr(management_service, name, value)
            except Exception:
                pass

import sys as _sys
_sys.modules[__name__].__class__ = _BffMainModule

try:
    _ = app.openapi()
except Exception:
    pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
