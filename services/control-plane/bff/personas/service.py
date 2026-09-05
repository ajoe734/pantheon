"""Persona domain application service and business logic.

Part of OPGAP-BE-PERSONA-ROUTER-V2-20260830.
Zero reverse imports of main.py.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from copy import deepcopy
import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import logging
import math
import os
import re
import socket
import sys
import threading
import time
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)
import urllib.error
import urllib.parse
from urllib import error as urllib_error, request as urllib_request
from urllib.parse import quote, urlencode
import urllib.request
import uuid

from fastapi import Body, Header, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.params import Param as FastAPIParam
from starlette.responses import JSONResponse

BFF_DATA_DIR = os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff")

from ..models import (
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
    redact_evidence_refs,
    RecordSponsorDecisionCommandPayload,
    RejectMutationCommandPayload,
    ReviewMutationCommandPayload,
    ExecuteMutationCommandPayload,
    StalenessWarning,
    TargetObject,
    utc_now,
)

try:
    from services.foundation import (
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
except ImportError:
    ActorRef = Any
    ActorType = Any
    AuditAction = Any
    AuthorityScope = Any
    CommandEnvelope = Any
    EnvironmentName = Any
    EnvironmentScope = Any
    ErrorEnvelope = Any
    ErrorKind = Any
    FoundationValidationError = Exception
    IdempotencyRecord = Any
    PolicyDecision = Any
    PolicyDecisionValue = Any
    TraceContext = Any
    foundation_id = lambda: str(uuid.uuid4())
    sha256_checksum = lambda data: hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()

try:
    from services.control_plane.bff.ports.persona_capital_runtime import (
        PERSONA_OPERATIONAL_LIFECYCLE_STATES,
        create_persona_capital_runtime_port,
        create_in_memory_persona_capital_runtime_port,
    )
except ImportError:
    PERSONA_OPERATIONAL_LIFECYCLE_STATES = frozenset({"paper_trading", "live_canary", "live_active"})

try:
    from services.control_plane.bff.ports import (
        ReadSurfacePorts,
        create_persona_registry_write_owner,
        create_ranking_write_owner,
        create_read_surface_ports,
    )
except ImportError:
    try:
        from services.control_plane.bff.ports import (  # type: ignore[no-redef]
            ReadSurfacePorts,
            create_persona_registry_write_owner,
            create_ranking_write_owner,
            create_read_surface_ports,
        )
    except ImportError:
        ReadSurfacePorts = Any
        create_persona_registry_write_owner = None
        create_ranking_write_owner = None
        create_read_surface_ports = None

try:
    from services.control_plane.bff.command_queue import CommandStore
except ImportError:
    try:
        from services.control_plane.bff.command_queue import CommandStore
    except ImportError:
        CommandStore = None

try:
    from services.control_plane.bff.persona_provisioning import (
        MemoryPersonaProvisioningStore,
        ProvisioningConflict,
        ProvisioningRecord,
        TERMINAL_STATES,
        make_persona_provisioning_store,
    )
except ImportError:
    class ProvisioningConflict(ValueError):
        pass
    class MemoryPersonaProvisioningStore:
        pass
    make_persona_provisioning_store = None

try:
    from services.control_plane.bff.persona_provisioning_coordinator import (
        PersonaCronRegistrar,
        PersonaProvisioningCoordinator,
        deterministic_provisioning_ids,
    )
except ImportError:
    PersonaCronRegistrar = None
    PersonaProvisioningCoordinator = None
    deterministic_provisioning_ids = None

try:
    from services.control_plane.bff.action_catalog import get_catalog_entry
except ImportError:
    get_catalog_entry = None

from ..command_executor import _get_json, _post_json, _runtime_manager_client

from ..persona_allocation_policy import build_pm12_allocation_policy_input

try:
    from services.control_plane.bff.paper_eligibility_proof import (
        BENCHMARK_VERSION as _PPL_ALLOC_009_ELIGIBILITY_BENCHMARK_VERSION,
        EXPECTED_IDEMPOTENCY_KEY as _PPL_ALLOC_009_ELIGIBILITY_IDEMPOTENCY_KEY,
        RUN_KEY as _PPL_ALLOC_009_ELIGIBILITY_RUN_KEY,
        TASK_ID as _PPL_ALLOC_009_ELIGIBILITY_TASK_ID,
        PaperEligibilityObservationStore,
        build_telemetry_event as _ppl_alloc_009_build_telemetry_event,
    )
except ImportError:
    PaperEligibilityObservationStore = None
    _ppl_alloc_009_build_telemetry_event = None

try:
    from services.source_ingestion.strategy_seed_store import (
        SeedReviewDecision,
        StrategySpecSeedReviewError,
        StrategySpecSeedStore,
        StrategySpecSeedStoreError,
    )
except ImportError:
    StrategySpecSeedStore = None
    StrategySpecSeedStoreError = Exception
    StrategySpecSeedReviewError = Exception

try:
    # Standalone callers historically imported this module as ``personas``;
    # keep that compatibility fallback, but prefer the package-local models
    # contract so the capability-aware three-argument function is never
    # replaced by an unrelated top-level ``models`` module.
    from services.control_plane.bff.models import redact_evidence_refs as _standalone_redact_evidence_refs
except ImportError:
    _standalone_redact_evidence_refs = None
if _standalone_redact_evidence_refs is not None and not str(__package__ or "").startswith("services.control_plane"):
    redact_evidence_refs = _standalone_redact_evidence_refs

try:
    from services.control_plane.persona.persona_strategy_discovery import (
        PersonaStrategyDiscoveryService,
        extract_persona_strategy_profile,
    )
except ImportError:
    try:
        from services.control_plane.persona.persona_strategy_discovery import (
            PersonaStrategyDiscoveryService,
            extract_persona_strategy_profile,
        )
    except ImportError:
        PersonaStrategyDiscoveryService = None
        extract_persona_strategy_profile = None

try:
    from services.persona.runtime_profile import build_persona_runtime_profile
except ImportError:
    try:
        from persona.runtime_profile import build_persona_runtime_profile
    except ImportError:
        build_persona_runtime_profile = None

log = logging.getLogger(__name__)

# Standalone fallback stores are eliminated; explicit composition root injects dependencies.
persona_write_owner = None
read_store = None

# Rankings write-owner port must be configured at service/app startup;
# missing required configuration fails startup closed, never deferred to first write.
_ranking_write_owner: Optional[Any] = None


def _get_ranking_write_owner() -> Any:
    global _ranking_write_owner
    if _ranking_write_owner is None:
        raise RuntimeError("Rankings write-owner port is not configured at startup")
    return _ranking_write_owner

class _DefaultCommandStore:
    def _get_all_commands(self) -> List[Dict[str, Any]]:
        return []
    def get_all(self, *args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
        return []

command_store = (
    CommandStore(os.path.join(BFF_DATA_DIR, "commands.jsonl"))
    if CommandStore is not None
    else _DefaultCommandStore()
)

_ppl_alloc_009_eligibility_observation_store = (
    PaperEligibilityObservationStore(
        os.path.join(BFF_DATA_DIR, "ppl_alloc_009_proof_observations.sqlite3")
    )
    if PaperEligibilityObservationStore is not None
    else None
)

def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

utc_now = _utc_now_rfc3339


# --- _PERSONA_SERVICE_DIR ---
# --- extract_persona_strategy_profile ---
from services.control_plane.persona.persona_strategy_discovery import (
    PersonaStrategyDiscoveryService,
    extract_persona_strategy_profile,
)


# --- ProvisioningConflict ---
from ..persona_provisioning import (
    ProvisioningConflict,
    ProvisioningRecord,
    make_persona_provisioning_store,
)


# --- deterministic_provisioning_ids ---
from ..persona_provisioning_coordinator import (
    PersonaProvisioningCoordinationError,
    PersonaProvisioningCoordinator,
    deterministic_provisioning_ids,
)


# --- _ppl_alloc_009_eligibility_error ---
def _ppl_alloc_009_eligibility_error(
    message: str,
    detail: str,
    *,
    precondition: str,
    status_code: int = 422,
) -> HTTPException:
    return _bff_error(
        status_code,
        ErrorCode.PRECONDITION_FAILED,
        message,
        detail,
        precondition_failed=precondition,
    )


# --- _ppl_alloc_009_paper_eligibility_context ---
def _ppl_alloc_009_paper_eligibility_context(
    *,
    persona_id: str,
    identity: OperatorIdentity,
    observed_at: str,
) -> Dict[str, Any]:
    caller_tenant = str(
        _bff_me_tenant_payload(identity, requested_tenant=None)["id"]
    )
    raw = _get_persona_directory_snapshot(caller_tenant).records_by_id.get(persona_id)
    metadata = (
        raw.get("metadata")
        if isinstance(raw, dict) and isinstance(raw.get("metadata"), dict)
        else {}
    )
    if (
        not isinstance(raw, dict)
        or _persona_record_tenant_id(raw) != caller_tenant
        or caller_tenant != "tenant-dev"
        or str(raw.get("name") or "").strip()
        != f"PPL ALLOC 009 {_PPL_ALLOC_009_ELIGIBILITY_RUN_KEY}"
        or str(metadata.get("provisioning_idempotency_key") or "").strip()
        != "ppl-alloc-009-30095677466-persona-create"
    ):
        raise _ppl_alloc_009_eligibility_error(
            "Persona is outside the PPL-ALLOC-009 proof scope",
            (
                "The eligibility producer accepts only the exact tenant-dev "
                "Persona reserved by the canonical acceptance run."
            ),
            precondition="task_scoped_persona",
            status_code=404,
        )
    if (
        _persona_record_projected_state(raw) != "paper_running"
        or str(metadata.get("capital_mode") or "").strip().lower() != "paper"
        or str(metadata.get("deployment_stage") or "").strip().lower() != "paper"
        or metadata.get("live_capital_enabled") is not False
        or metadata.get("live_write_enabled") is not False
        or metadata.get("order_side_effects_allowed") is not False
        or metadata.get("capital_side_effects_allowed") is not False
    ):
        raise _ppl_alloc_009_eligibility_error(
            "Persona is not in authoritative paper-only state",
            (
                "The exact Persona must remain paper_running with every "
                "live/order/capital side-effect authority disabled."
            ),
            precondition="paper_only_persona",
        )

    league_rows = _pm12_persona_league_rows(q=persona_id, tenant_id=caller_tenant)
    matches = [
        row
        for row in league_rows
        if str(row.get("persona_id") or row.get("id") or "").strip() == persona_id
    ]
    if len(matches) != 1:
        raise _ppl_alloc_009_eligibility_error(
            "Persona ranking identity is not authoritative",
            "The task Persona must resolve to exactly one canonical league row.",
            precondition="persona_league_identity",
        )
    ranking_item = _pm12_persona_league_ranking_item(matches[0])
    runtime_ids = [
        str(value or "").strip()
        for value in ranking_item.get("runtime_ids") or []
        if str(value or "").strip()
    ]
    if (
        str(ranking_item.get("stage") or "").strip().lower() != "paper_running"
        or str(ranking_item.get("capital_scope") or "").strip().lower()
        != "paper_ledger"
        or str(ranking_item.get("runtime_resolution") or "").strip().lower()
        != "active"
        or str(ranking_item.get("session_resolution") or "").strip().lower()
        != "active"
        or not str(ranking_item.get("session_id") or "").strip()
        or len(runtime_ids) != 1
    ):
        raise _ppl_alloc_009_eligibility_error(
            "Paper runtime authority is incomplete",
            (
                "The task Persona must have one active paper runtime, one active "
                "owner monitoring session, and one isolated paper ledger."
            ),
            precondition="paper_runtime_authority",
        )

    capital = _ppl_alloc_009_paper_capital_context(
        persona_id=persona_id,
        ranking_item=ranking_item,
    )
    runtime_id = runtime_ids[0]
    declared_runtime_binding_id = str(
        metadata.get("runtime_binding_id") or ""
    ).strip()
    runtime_matches = []
    for runtime in read_store.list_runtime_bindings():
        if not isinstance(runtime, dict):
            continue
        runtime_binding_id = str(
            runtime.get("runtime_binding_id")
            or runtime.get("binding_id")
            or runtime.get("id")
            or ""
        ).strip()
        runtime_stage = str(
            runtime.get("deployment_stage")
            or runtime.get("deployment_mode")
            or runtime.get("execution_mode")
            or runtime.get("runtime_kind")
            or ""
        ).strip().lower()
        runtime_status = str(
            runtime.get("status") or runtime.get("state") or ""
        ).strip().lower()
        if (
            str(runtime.get("runtime_id") or "").strip() == runtime_id
            and runtime_binding_id == declared_runtime_binding_id
            and str(runtime.get("persona_id") or "").strip() == persona_id
            and runtime_stage == "paper"
            and runtime_status in {"active", "running", "idle"}
        ):
            runtime_matches.append(dict(runtime))
    if len(runtime_matches) != 1:
        raise _ppl_alloc_009_eligibility_error(
            "RuntimeBinding is not authoritative",
            "The task Persona must resolve to exactly one active paper RuntimeBinding.",
            precondition="paper_runtime_binding",
        )
    runtime_binding = runtime_matches[0]
    if (
        str(runtime_binding.get("capital_pool_id") or "").strip()
        != capital["capital_pool_id"]
        or str(runtime_binding.get("persona_capital_binding_id") or "").strip()
        != capital["binding_id"]
    ):
        raise _ppl_alloc_009_eligibility_error(
            "RuntimeBinding capital lineage does not match",
            (
                "RuntimeBinding must join the same internal paper pool and "
                "PersonaCapitalBinding as the canonical ranking row."
            ),
            precondition="paper_runtime_capital_lineage",
        )

    required_runtime_fields = (
        "runtime_id",
        "capital_pool_id",
        "artifact_id",
        "artifact_version",
        "persona_capital_binding_id",
    )
    missing = [
        field
        for field in required_runtime_fields
        if not str(runtime_binding.get(field) or "").strip()
    ]
    plan_id = str(
        runtime_binding.get("plan_id")
        or runtime_binding.get("deployment_plan_id")
        or ""
    ).strip()
    if not plan_id:
        missing.append("plan_id")
    plan = read_store.get_deployment_plan(plan_id) if plan_id else None
    if not isinstance(plan, dict):
        missing.append("deployment_plan")
    strategy_id = str(
        (plan or {}).get("strategy_id")
        or runtime_binding.get("strategy_id")
        or ""
    ).strip()
    if not strategy_id:
        missing.append("strategy_id")
    if missing:
        raise _ppl_alloc_009_eligibility_error(
            "RuntimeBinding telemetry identity is incomplete",
            f"Missing canonical telemetry fields: {', '.join(sorted(set(missing)))}.",
            precondition="telemetry_binding_identity",
        )

    effective_at = _audit_datetime(runtime_binding.get("effective_at"))
    observed_at_value = _audit_datetime(observed_at)
    retired_at = _audit_datetime(runtime_binding.get("retired_at"))
    if (
        observed_at_value is None
        or (effective_at is not None and observed_at_value < effective_at)
        or (retired_at is not None and observed_at_value > retired_at)
    ):
        raise _ppl_alloc_009_eligibility_error(
            "Paper benchmark timestamp is outside RuntimeBinding authority",
            "The immutable proof observation must fall within the binding window.",
            precondition="telemetry_binding_window",
        )
    return {
        "ranking_item": ranking_item,
        "runtime_binding": runtime_binding,
        "strategy_id": strategy_id,
        "paper_session_id": str(ranking_item.get("session_id") or "").strip(),
        "paper_ledger_id": str(ranking_item.get("paper_ledger_id") or "").strip(),
        "capital": capital,
    }


# --- _ppl_alloc_009_telemetry_url ---
def _ppl_alloc_009_telemetry_url(path: str) -> str:
    base = str(
        os.getenv("PANTHEON_TELEMETRY_API_URL")
        or os.getenv("PANTHEON_TELEMETRY_URL")
        or ""
    ).strip().rstrip("/")
    if not base:
        raise _ppl_alloc_009_eligibility_error(
            "Telemetry owner is unavailable",
            "PANTHEON_TELEMETRY_API_URL is required for the governed producer.",
            precondition="telemetry_owner",
            status_code=503,
        )
    return f"{base}{path}"


# --- _ppl_alloc_009_dev_proof_enabled ---
def _ppl_alloc_009_dev_proof_enabled() -> bool:
    return _bool_from_env(
        "PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED",
        default=False,
    )


# --- _ppl_alloc_009_readback_timeout_seconds ---
def _ppl_alloc_009_readback_timeout_seconds() -> float:
    raw = str(
        os.getenv("PANTHEON_PPL_ALLOC_009_READBACK_TIMEOUT_SECONDS") or "10"
    ).strip()
    try:
        return max(0.0, min(float(raw), 30.0))
    except (TypeError, ValueError):
        return 10.0


# --- _ppl_alloc_009_readback_poll_seconds ---
def _ppl_alloc_009_readback_poll_seconds() -> float:
    raw = str(
        os.getenv("PANTHEON_PPL_ALLOC_009_READBACK_POLL_SECONDS") or "0.25"
    ).strip()
    try:
        return max(0.01, min(float(raw), 2.0))
    except (TypeError, ValueError):
        return 0.25


# --- _ppl_alloc_009_accepted_event_mismatches ---
def _ppl_alloc_009_accepted_event_mismatches(
    *,
    telemetry_readback: Mapping[str, Any],
    expected_event: Mapping[str, Any],
) -> List[str]:
    mismatches: List[str] = []
    for field in sorted(set(expected_event) | set(telemetry_readback)):
        expected = expected_event.get(field)
        actual = telemetry_readback.get(field)
        if actual != expected:
            mismatches.append(f"{field} does not match the immutable event")
    return mismatches


# --- _ppl_alloc_009_wait_for_telemetry_readback ---
def _ppl_alloc_009_wait_for_telemetry_readback(
    *,
    expected_event: Mapping[str, Any],
) -> tuple[Dict[str, Any], int]:
    timeout_seconds = _ppl_alloc_009_readback_timeout_seconds()
    poll_seconds = _ppl_alloc_009_readback_poll_seconds()
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_error = "summary unavailable"
    while True:
        attempts += 1
        try:
            candidate = _get_json(
                _ppl_alloc_009_telemetry_url(
                    "/api/telemetry/events/"
                    + quote(str(expected_event.get("event_id") or ""), safe="")
                )
            )
            if not isinstance(candidate, dict):
                last_error = "telemetry event response was not an object"
            else:
                mismatches = _ppl_alloc_009_accepted_event_mismatches(
                    telemetry_readback=candidate,
                    expected_event=expected_event,
                )
                if not mismatches:
                    return dict(candidate), attempts
                last_error = "; ".join(mismatches)
        except Exception as exc:  # noqa: BLE001 - bounded owner readback
            last_error = str(exc) or exc.__class__.__name__
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_seconds, remaining))
    raise RuntimeError(
        f"telemetry owner readback timed out after {attempts} attempts: {last_error}"
    )


# --- _PERSONA_OPERATIONAL_LIFECYCLE_STATES ---
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


# --- _is_persona_lifecycle_operational ---
def _is_persona_lifecycle_operational(value: Any) -> bool:
    return str(value or "").strip().lower() in _PERSONA_OPERATIONAL_LIFECYCLE_STATES


# --- _STRATEGY_PERSONA_BFF_IDEMPOTENCY ---
_STRATEGY_PERSONA_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}


# --- _PERSONA_BFF_OVERLAY ---
_PERSONA_BFF_OVERLAY: Dict[str, Dict[str, Any]] = {}


# --- _PERSONA_PROVISIONING_STORE ---
_PERSONA_PROVISIONING_STORE = None


# --- _PERSONA_PROVISIONING_STORE_LOCK ---
_PERSONA_PROVISIONING_STORE_LOCK = threading.Lock()


# --- _PERSONA_PROVISIONING_RECONCILER_TASK ---
_PERSONA_PROVISIONING_RECONCILER_TASK: Optional[asyncio.Task[Any]] = None


# --- _PERSONA_FIRST_EVALUATION_WORKFLOW_ID ---
_PERSONA_FIRST_EVALUATION_WORKFLOW_ID = "pantheon.persona.first-evaluation"


# --- _persona_provisioning_store ---
def _persona_provisioning_store():
    """Lazily bootstrap the durable cross-replica coordination ledger."""
    global _PERSONA_PROVISIONING_STORE
    if _PERSONA_PROVISIONING_STORE is not None:
        return _PERSONA_PROVISIONING_STORE
    with _PERSONA_PROVISIONING_STORE_LOCK:
        if _PERSONA_PROVISIONING_STORE is None:
            _PERSONA_PROVISIONING_STORE = make_persona_provisioning_store()
    return _PERSONA_PROVISIONING_STORE


# --- _PersonaOwnerHttpTransport ---
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


# --- _strategy_persona_idempotency_check ---
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


# --- _strategy_persona_action_command ---
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


# --- _checkpoint_persona_provisioning_readback ---
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


# --- _append_persona_reconcile_diagnostic ---
def _append_persona_reconcile_diagnostic(
    diagnostics: Optional[List[str]],
    dependency: str,
) -> None:
    if diagnostics is not None and dependency not in diagnostics:
        diagnostics.append(dependency)


# --- _materialize_terminal_persona_provisioning_ledger ---
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

    read_store.update_persona(
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


# --- _reconcile_persona_provisioning_compensation ---
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


# --- _evaluate_persona_provisioning_status ---
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
            read_store.update_persona(
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
        read_store.update_persona(
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


# --- _project_persona_dto ---
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


# --- _strategy_routed_persona_count ---
def _strategy_routed_persona_count(strategy_id: str) -> int:
    detail = read_store.get_strategy_spec_detail(strategy_id, version_selector="current")
    if not detail:
        return 0
    persona_ids = detail.get("persona_ids") or []
    if not isinstance(persona_ids, list):
        return 0
    return len([p for p in persona_ids if str(p).strip()])


# --- _routed_strategies_for_persona ---
def _routed_strategies_for_persona(persona_id: str) -> int:
    items = read_store.list_strategy_specs(persona_id=persona_id) or []
    return len(items)


# --- _list_persona_records ---
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


# --- PersonaDirectorySnapshot ---
@dataclass(frozen=True)
class PersonaDirectorySnapshot:
    tenant_id: str
    snapshot_at: str
    records_by_id: Dict[str, Dict[str, Any]]
    catalog_defaults_by_id: Dict[str, Dict[str, Any]]


# --- _get_persona_directory_snapshot ---
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


# --- _PERSONA_STRATEGY_MATCH_ALLOWED_ACTIONS ---
_PERSONA_STRATEGY_MATCH_ALLOWED_ACTIONS = frozenset({
    "create_research_ticket",
    "promote_seed_candidate",
})


# --- _PERSONA_STRATEGY_MATCH_FORBIDDEN_ACTION_TERMS ---
_PERSONA_STRATEGY_MATCH_FORBIDDEN_ACTION_TERMS = frozenset({
    "deploy",
    "deployment",
    "broker",
    "live",
    "order",
    "runtime",
    "execute",
    "execution",
})


# --- _load_strategy_seed_match_candidates ---
def _load_strategy_seed_match_candidates(
    *,
    snapshot_at: str,
) -> Tuple[List[Any], Dict[str, Any]]:
    try:
        store = StrategySpecSeedStore()
        seeds = store.list_all()
        path_exists = store.path.exists()
        surface: Dict[str, Any] = {
            "status": "ok" if path_exists else "degraded",
            "source": "strategy_spec_seed_store" if path_exists else "strategy_spec_seed_store_missing",
            "path": str(store.path),
        }
        if not path_exists:
            surface["note"] = "StrategySpecSeed store file is absent; matching will use StrategySpec candidates only."
            surface["staleness"] = {"served_from": "empty_dev_store", "last_known_at": snapshot_at}
        return list(seeds), surface
    except Exception as exc:  # pragma: no cover - exercised by corrupt local stores.
        log.warning("StrategySpecSeed store unavailable for persona discovery: %s", exc)
        return [], {
            "status": "unavailable",
            "source": "strategy_spec_seed_store",
            "message": str(exc),
            "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
        }


# --- _list_strategy_spec_match_candidates ---
def _list_strategy_spec_match_candidates(
    *,
    include_retired: bool,
    snapshot_at: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        summaries = read_store.list_strategy_specs(
            include_retired=include_retired,
            include_fixture_pack=False,
        ) or []
        for summary in summaries:
            strategy_id = str(summary.get("strategy_id") or summary.get("id") or "").strip()
            if not strategy_id:
                continue
            detail = read_store.get_strategy_spec_detail(strategy_id, version_selector="current")
            if detail:
                items.append(detail)
    except Exception as exc:  # pragma: no cover - defensive read composition.
        log.warning("StrategySpec read surface unavailable for persona discovery: %s", exc)
        return [], {
            "status": "unavailable",
            "source": read_store.dataset_source("strategy_specs"),
            "message": str(exc),
            "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
        }

    surface = _dataset_surface_status(
        "strategy_specs",
        snapshot_at=snapshot_at,
        has_data=read_store.dataset_source("strategy_specs") != "missing",
    )
    return items, surface


# --- _persona_strategy_discovery_payload ---
def _persona_strategy_discovery_payload(
    persona_id: str,
    *,
    include_retired: bool,
    include_blocked: bool,
    snapshot_at: str,
    discovery_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    _ensure_persona_exists(persona_id)
    persona = read_store.get_persona(persona_id)
    if persona is None:
        overlay = _PERSONA_BFF_OVERLAY.get(persona_id) or {}
        persona = {
            "id": persona_id,
            "persona_id": persona_id,
            "name": overlay.get("name") or persona_id,
            "mandate": overlay.get("archetype") or overlay.get("name") or persona_id,
            "strategy_family": overlay.get("archetype"),
            "lifecycle_state": overlay.get("state") or "draft",
            "status": overlay.get("state") or "draft",
            "metadata": {
                "archetype": overlay.get("archetype"),
                "risk_level": overlay.get("risk"),
                "market_scope": overlay.get("marketScope"),
                "asset_classes": overlay.get("assetClasses"),
            },
        }
    route_policy = read_store.get_route_policy_for_persona(persona_id) or {}
    capability_snapshot = read_store.get_capability_snapshot_for_persona(persona_id) or {}
    profile = extract_persona_strategy_profile(
        persona,
        route_policy=route_policy,
        capability_snapshot=capability_snapshot,
    )
    seeds, seed_surface = _load_strategy_seed_match_candidates(snapshot_at=snapshot_at)
    strategy_specs, strategy_spec_surface = _list_strategy_spec_match_candidates(
        include_retired=include_retired,
        snapshot_at=snapshot_at,
    )
    matches = PersonaStrategyDiscoveryService().match_candidates(
        profile,
        strategy_seeds=seeds,
        strategy_specs=strategy_specs,
        created_at=snapshot_at,
        discovery_session_id=discovery_session_id,
        include_blocked=include_blocked,
    )
    match_payloads = [match.to_dict() for match in matches]
    return {
        "profile": profile.to_dict(),
        "matches": match_payloads,
        "surfaces": {
            "persona_strategy_profile": _dataset_surface_status(
                "personas",
                snapshot_at=snapshot_at,
                has_data=True,
            ),
            "strategy_spec_seeds": seed_surface,
            "strategy_specs": strategy_spec_surface,
        },
        "candidate_counts": {
            "strategy_spec_seeds": len(seeds),
            "strategy_specs": len(strategy_specs),
            "total_matches": len(match_payloads),
        },
    }


# --- _persona_strategy_matches_response ---
def _persona_strategy_matches_response(
    persona_id: str,
    *,
    include_retired: bool,
    include_blocked: bool,
    page_token: Optional[str],
    page_size: int,
    discovery_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    snapshot_at = utc_now()
    payload = _persona_strategy_discovery_payload(
        persona_id,
        include_retired=include_retired,
        include_blocked=include_blocked,
        snapshot_at=snapshot_at,
        discovery_session_id=discovery_session_id,
    )
    matches = payload["matches"]
    page_items, next_page_token = _page_slice(matches, page_token, page_size)
    return {
        "data": page_items,
        "items": page_items,
        "profile": payload["profile"],
        "page_info": {
            "next_page_token": next_page_token,
            "page_size": page_size,
            "total": len(matches),
            "has_more": next_page_token is not None,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": payload["surfaces"],
            "candidate_counts": payload["candidate_counts"],
            "research_only": True,
            "execution_route": "none",
        },
    }


# --- _strategy_discovery_page_size ---
def _strategy_discovery_page_size(value: Any) -> int:
    try:
        page_size = int(value or 20)
    except (TypeError, ValueError) as exc:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "page_size must be an integer",
            "Persona strategy discovery page_size must be an integer from 1 to 100.",
            precondition_failed="page_size",
        ) from exc
    if page_size < 1 or page_size > 100:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "page_size must be between 1 and 100",
            "Persona strategy discovery page_size must be an integer from 1 to 100.",
            precondition_failed="page_size",
        )
    return page_size


# --- _strategy_match_action_type ---
def _strategy_match_action_type(payload: Mapping[str, Any]) -> str:
    action = str(
        payload.get("action")
        or payload.get("type")
        or payload.get("recommended_action")
        or ""
    ).strip()
    if not action:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "strategy match action is required",
            "Set action to create_research_ticket or promote_seed_candidate.",
            precondition_failed="action",
        )
    normalized = action.lower().replace("-", "_")
    if (
        normalized not in _PERSONA_STRATEGY_MATCH_ALLOWED_ACTIONS
        or any(term in normalized for term in _PERSONA_STRATEGY_MATCH_FORBIDDEN_ACTION_TERMS)
    ):
        raise _bff_error(
            422,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "Strategy match action cannot deploy or execute",
            (
                "Persona strategy discovery actions are limited to research ticket "
                "creation or seed-candidate promotion review."
            ),
            precondition_failed="action",
        )
    return normalized


# --- _find_persona_strategy_match ---
def _find_persona_strategy_match(persona_id: str, match_id: str, *, snapshot_at: str) -> Dict[str, Any]:
    payload = _persona_strategy_discovery_payload(
        persona_id,
        include_retired=False,
        include_blocked=True,
        snapshot_at=snapshot_at,
    )
    for match in payload["matches"]:
        if str(match.get("match_id") or "") == match_id:
            return match
    raise _bff_error(
        404,
        ErrorCode.RESOURCE_NOT_FOUND,
        "Persona strategy match not found",
        f"Match {match_id} does not exist for persona {persona_id}",
    )


# --- _persona_strategy_match_action_response ---
def _persona_strategy_match_action_response(
    *,
    persona_id: str,
    match_id: str,
    payload: Dict[str, Any],
    identity: OperatorIdentity,
    resolved_key: str,
) -> Dict[str, Any]:
    action = _strategy_match_action_type(payload)
    snapshot_at = utc_now()
    request_hash = _stable_json_hash(
        {
            "route": "POST /api/v1/personas/{persona_id}/strategy-matches/{match_id}/actions",
            "persona_id": persona_id,
            "match_id": match_id,
            "action": action,
            "payload": payload,
        }
    )
    cached = _strategy_persona_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached

    match = _find_persona_strategy_match(persona_id, match_id, snapshot_at=snapshot_at)
    blockers = list(((match.get("metadata") or {}).get("blockers") or []))
    if blockers and action != "create_research_ticket":
        raise _bff_error(
            422,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "Blocked strategy match cannot be promoted",
            f"Hard blockers must be resolved first: {', '.join(blockers)}",
            precondition_failed="blockers",
        )
    if action == "promote_seed_candidate" and match.get("matched_object_type") != "strategy_spec_seed":
        raise _bff_error(
            422,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "Only StrategySpecSeed matches can be promoted as seed candidates",
            "Use create_research_ticket for StrategySpec matches.",
            precondition_failed="matched_object_type",
        )

    if action == "create_research_ticket":
        ticket = read_store.create_research_ticket(
            title=str(payload.get("title") or f"Research persona strategy match {match_id}"),
            description=str(
                payload.get("description")
                or (
                    f"Evaluate {match.get('matched_object_type')} {match.get('matched_object_id')} "
                    f"for persona {persona_id}. Score: {match.get('score')}."
                )
            ),
            priority=str(payload.get("priority") or "normal"),
            owner=str(payload.get("owner") or persona_id),
            actor_id=identity.operator_id,
            created_at=snapshot_at,
        )
        result = {
            "data": {
                "action": action,
                "status": "research_ticket_created",
                "match_id": match_id,
                "persona_id": persona_id,
                "ticket": ticket,
                "deployment_authority": "none",
                "registry_write_performed": False,
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "research_only": True,
                "execution_route": "none",
            },
        }
    else:
        promotion_request_id = f"seed-promotion-{match_id}-{uuid.uuid4().hex[:8]}"
        result = {
            "data": {
                "action": action,
                "status": "queued",
                "promotion_request_id": promotion_request_id,
                "match_id": match_id,
                "persona_id": persona_id,
                "matched_object_id": match.get("matched_object_id"),
                "registry_write_performed": False,
                "deployment_authority": "none",
                "next": "Submit the seed candidate to StrategySpec review before any deployment gate.",
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "research_only": True,
                "execution_route": "none",
            },
        }
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY[resolved_key] = {
        "request_hash": request_hash,
        "result": result,
    }
    return result


# --- _project_persona_fleet_health ---
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


# --- _project_persona_fleet_item ---
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


# --- _PERSONA_INTENT_SOURCE_ALIASES ---
_PERSONA_INTENT_SOURCE_ALIASES = {
    "trace": "persona_trace",
    "persona_trace": "persona_trace",
    "persona_traces": "persona_trace",
    "session": "persona_trace",
    "sessions": "persona_trace",
    "trainer": "trainer_session",
    "trainer_session": "trainer_session",
    "trainer_sessions": "trainer_session",
    "teaching": "trainer_session",
    "teaching_session": "trainer_session",
    "agora": "agora_session",
    "agora_session": "agora_session",
    "agora_sessions": "agora_session",
}


# --- _persona_intent_csv_filter ---
def _persona_intent_csv_filter(value: Optional[str]) -> Optional[set[str]]:
    if not value:
        return None
    requested = {part.strip().lower() for part in value.split(",") if part.strip()}
    return requested or None


# --- _persona_intent_source_filter ---
def _persona_intent_source_filter(value: Optional[str]) -> Optional[set[str]]:
    requested = _persona_intent_csv_filter(value)
    if not requested:
        return None
    return {
        _PERSONA_INTENT_SOURCE_ALIASES.get(source_type, source_type)
        for source_type in requested
    }


# --- _persona_intent_text ---
def _persona_intent_text(value: Any) -> str:
    return str(value or "").strip()


# --- _persona_intent_timestamp ---
def _persona_intent_timestamp(record: Dict[str, Any]) -> str:
    return str(
        _management_first_non_empty(
            record.get("updated_at"),
            record.get("updatedAt"),
            record.get("last_heartbeat_at"),
            record.get("completed_at"),
            record.get("ended_at"),
            record.get("created_at"),
            record.get("createdAt"),
            record.get("started_at"),
        )
        or ""
    )


# --- _persona_intent_persona_label ---
def _persona_intent_persona_label(persona_id: str) -> Optional[str]:
    persona = read_store.get_persona(persona_id)
    if not persona:
        return None
    return persona.get("name") or persona.get("display_name") or persona_id


# --- _persona_intent_capability_summary ---
def _persona_intent_capability_summary(session: Dict[str, Any]) -> Dict[str, Any]:
    snapshot_id = _persona_intent_text(session.get("capability_snapshot_id"))
    snapshot = read_store.get_capability_snapshot(snapshot_id) if snapshot_id else None
    if not snapshot:
        persona_id = _persona_intent_text(session.get("persona_id"))
        snapshot = read_store.get_capability_snapshot_for_persona(persona_id)
        snapshot_id = _persona_intent_text((snapshot or {}).get("snapshot_id") or snapshot_id)
    if not snapshot:
        return {
            "snapshot_id": snapshot_id or None,
            "available": False,
            "effective_tool_count": 0,
            "effective_skill_count": 0,
            "restriction_count": 0,
        }
    return {
        "snapshot_id": snapshot_id or snapshot.get("snapshot_id") or snapshot.get("id"),
        "available": True,
        "effective_tool_count": len(snapshot.get("effective_tools") or []),
        "effective_skill_count": len(snapshot.get("effective_skills") or []),
        "effective_workflow_count": len(snapshot.get("effective_workflows") or []),
        "restriction_count": len(snapshot.get("restrictions") or []),
        "generated_at": snapshot.get("generated_at"),
    }


# --- _persona_intent_redaction ---
def _persona_intent_redaction(fields: List[str]) -> Dict[str, Any]:
    return {
        "is_redacted": True,
        "redacted": True,
        "policy": "management_persona_intent_public_summary",
        "redacted_fields": fields,
    }


# --- _persona_intent_trace_item ---
def _persona_intent_trace_item(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    session_id = _management_record_id(session, "session_id", "id")
    if not session_id:
        return None
    persona_id = _persona_intent_text(session.get("persona_id"))
    session_type = _persona_intent_text(session.get("session_type") or "persona_session")
    status = _persona_intent_text(session.get("status") or "unknown").lower() or "unknown"
    occurred_at = _persona_intent_timestamp(session)
    item_id = f"persona_trace:{session_id}"
    trace = {
        "session_id": session_id,
        "trace_id": session.get("trace_id"),
        "request_id": session.get("request_id"),
        "runtime_binding_id": session.get("runtime_binding_id"),
        "deployment_stage": session.get("deployment_stage"),
        "capital_pool_id": session.get("capital_pool_id"),
        "last_heartbeat_at": session.get("last_heartbeat_at"),
        "capability_summary": _persona_intent_capability_summary(session),
    }
    return {
        "id": item_id,
        "intent_id": item_id,
        "sourceType": "persona_trace",
        "source_type": "persona_trace",
        "source_id": session_id,
        "personaId": persona_id or None,
        "persona_id": persona_id or None,
        "persona_label": _persona_intent_persona_label(persona_id) if persona_id else None,
        "intent": session_type,
        "title": f"Persona trace {session_id}",
        "summary": f"{session_type.replace('_', ' ').title()} session intent summary.",
        "status": status,
        "created_at": session.get("started_at") or session.get("created_at"),
        "updated_at": session.get("last_heartbeat_at") or session.get("updated_at"),
        "occurred_at": occurred_at,
        "trace": trace,
        "redacted": True,
        "redaction": _persona_intent_redaction(
            ["capability_snapshot", "tools_enabled", "memory_trace", "reasoning_trace"]
        ),
        "route": "/management/persona-intent?source_type=persona_trace",
        "bff_detail_path": f"/api/v1/sessions/{session_id}",
    }


# --- _persona_intent_trainer_item ---
def _persona_intent_trainer_item(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    session_id = _management_record_id(session, "session_id", "id")
    if not session_id:
        return None
    persona_id = _persona_intent_text(session.get("persona_id"))
    status = _persona_intent_text(session.get("status") or "unknown").lower() or "unknown"
    raw_events = [event for event in (session.get("events") or []) if isinstance(event, dict)]
    latest_event = None
    if raw_events:
        latest_event = sorted(
            raw_events,
            key=lambda event: int(event.get("sequence_number") or 0),
        )[-1]
    outcomes = list(session.get("outcomes") or [])
    objective = _persona_intent_text(session.get("objective") or session.get("topic"))
    occurred_at = _persona_intent_timestamp(session)
    item_id = f"trainer_session:{session_id}"
    trainer_summary = {
        "session_id": session_id,
        "objective": objective or None,
        "mode": session.get("mode") or session.get("session_type") or "trainer",
        "status": status,
        "started_at": session.get("started_at"),
        "ended_at": session.get("ended_at") or session.get("completed_at"),
        "current_control_state": session.get("current_control_state"),
        "event_count": len(raw_events),
        "outcome_count": len(outcomes),
        "latest_outcome_signal": (latest_event or {}).get("outcome_signal"),
        "artifact_count": len(session.get("session_artifacts") or session.get("artifacts") or []),
    }
    return {
        "id": item_id,
        "intent_id": item_id,
        "sourceType": "trainer_session",
        "source_type": "trainer_session",
        "source_id": session_id,
        "personaId": persona_id or None,
        "persona_id": persona_id or None,
        "persona_label": _persona_intent_persona_label(persona_id) if persona_id else None,
        "intent": trainer_summary["mode"],
        "title": f"Trainer session {session_id}",
        "summary": objective or "Trainer session intent summary.",
        "status": status,
        "created_at": session.get("started_at") or session.get("created_at"),
        "updated_at": session.get("ended_at") or session.get("completed_at") or session.get("updated_at"),
        "occurred_at": occurred_at,
        "trainer": trainer_summary,
        "redacted": True,
        "redaction": _persona_intent_redaction(["events", "message_body", "raw_control_diff"]),
        "route": "/management/persona-intent?source_type=trainer_session",
        "bff_detail_path": f"/api/v1/trainer/sessions/{session_id}",
    }


# --- _persona_intent_agora_persona_ids ---
def _persona_intent_agora_persona_ids(session: Dict[str, Any]) -> List[str]:
    persona_ids: List[str] = []
    for participant in session.get("participants") or []:
        if not isinstance(participant, dict):
            continue
        actor_id = _persona_intent_text(
            participant.get("actorId") or participant.get("actor_id") or participant.get("persona_id")
        )
        if actor_id.startswith("persona-") or actor_id.startswith("p-"):
            persona_ids.append(actor_id)
    for ref in session.get("contextRefs") or session.get("context_refs") or []:
        if not isinstance(ref, dict):
            continue
        ref_type = _persona_intent_text(ref.get("ref_type") or ref.get("type")).lower()
        ref_id = _persona_intent_text(ref.get("ref_id") or ref.get("id"))
        if ref_type == "persona" and ref_id:
            persona_ids.append(ref_id)
    seen: set[str] = set()
    ordered: List[str] = []
    for persona_id in persona_ids:
        if persona_id and persona_id not in seen:
            ordered.append(persona_id)
            seen.add(persona_id)
    return ordered


# --- _persona_intent_agora_item ---
def _persona_intent_agora_item(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    session_id = _management_record_id(session, "sessionId", "session_id", "id")
    if not session_id:
        return None
    status = _persona_intent_text(session.get("status") or "unknown").lower() or "unknown"
    mode = _persona_intent_text(session.get("mode") or session.get("sessionType") or "agora_session")
    messages = [message for message in (session.get("messages") or []) if isinstance(message, dict)]
    latest_message_at = max(
        [
            _persona_intent_text(message.get("createdAt") or message.get("created_at"))
            for message in messages
            if _persona_intent_text(message.get("createdAt") or message.get("created_at"))
        ],
        default=None,
    )
    context_refs = [
        ref for ref in (session.get("contextRefs") or session.get("context_refs") or [])
        if isinstance(ref, dict)
    ]
    persona_ids = _persona_intent_agora_persona_ids(session)
    topic = _persona_intent_text(session.get("topic") or session.get("title"))
    occurred_at = _persona_intent_timestamp(session)
    item_id = f"agora_session:{session_id}"
    agora_summary = {
        "sessionId": session_id,
        "session_id": session_id,
        "mode": mode,
        "status": status,
        "topic": topic or None,
        "participant_count": len(session.get("participants") or []),
        "context_ref_count": len(context_refs),
        "message_count": len(messages),
        "latest_message_at": latest_message_at,
        "persona_ids": persona_ids,
        "sse_topic": session.get("sse_topic"),
    }
    return {
        "id": item_id,
        "intent_id": item_id,
        "sourceType": "agora_session",
        "source_type": "agora_session",
        "source_id": session_id,
        "personaId": persona_ids[0] if persona_ids else None,
        "persona_id": persona_ids[0] if persona_ids else None,
        "persona_ids": persona_ids,
        "intent": mode,
        "title": session.get("title") or f"Agora session {session_id}",
        "summary": topic or "Agora session intent summary.",
        "status": status,
        "created_at": session.get("createdAt") or session.get("created_at"),
        "updated_at": session.get("updatedAt") or session.get("updated_at") or latest_message_at,
        "occurred_at": occurred_at,
        "agora": agora_summary,
        "redacted": True,
        "redaction": _persona_intent_redaction(["messages", "message_content", "raw_transcript"]),
        "route": "/management/persona-intent?source_type=agora_session",
        "bff_detail_path": f"/bff/agora/ask/sessions/{session_id}",
    }


# --- _persona_intent_all_items ---
def _persona_intent_all_items(tenant_id: Optional[str] = None) -> tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    personas = _list_persona_records(tenant_id)
    items: List[Dict[str, Any]] = []
    persona_sessions: List[Dict[str, Any]] = []
    trainer_sessions: List[Dict[str, Any]] = []
    for persona in personas:
        persona_id = _persona_intent_text(persona.get("persona_id") or persona.get("id"))
        for session in read_store.get_sessions_for_persona(persona_id) or []:
            persona_sessions.append(session)
            item = _persona_intent_trace_item(session)
            if item is not None:
                items.append(item)
        for session in read_store.get_teaching_sessions_for_persona(persona_id) or []:
            trainer_sessions.append(session)
            item = _persona_intent_trainer_item(session)
            if item is not None:
                items.append(item)

    visible_persona_ids = {
        _persona_intent_text(persona.get("persona_id") or persona.get("id"))
        for persona in personas
        if _persona_intent_text(persona.get("persona_id") or persona.get("id"))
    }
    agora_sessions = list(read_store.list_agora_sessions() or [])
    for session in agora_sessions:
        referenced_persona_ids = _persona_intent_agora_persona_ids(session)
        if referenced_persona_ids and not all(
            persona_id in visible_persona_ids for persona_id in referenced_persona_ids
        ):
            # Agora session context is request-facing.  Do not disclose a
            # session when any referenced Persona lacks an explicit matching
            # tenant admission in the caller's directory.
            continue
        item = _persona_intent_agora_item(session)
        if item is not None:
            items.append(item)

    items.sort(
        key=lambda item: (
            str(item.get("occurred_at") or ""),
            str(item.get("id") or ""),
        ),
        reverse=True,
    )
    return items, persona_sessions, trainer_sessions, agora_sessions


# --- _persona_intent_filter_items ---
def _persona_intent_filter_items(
    items: List[Dict[str, Any]],
    *,
    source_type: Optional[str],
    persona_id: Optional[str],
    status: Optional[str],
    intent: Optional[str],
) -> List[Dict[str, Any]]:
    source_types = _persona_intent_source_filter(source_type)
    persona_ids = _persona_intent_csv_filter(persona_id)
    statuses = _persona_intent_csv_filter(status)
    intents = _persona_intent_csv_filter(intent)
    filtered = items
    if source_types:
        filtered = [
            item for item in filtered
            if str(item.get("source_type") or item.get("sourceType") or "").lower() in source_types
        ]
    if persona_ids:
        filtered = [
            item for item in filtered
            if str(item.get("persona_id") or "").lower() in persona_ids
            or any(str(pid or "").lower() in persona_ids for pid in (item.get("persona_ids") or []))
        ]
    if statuses:
        filtered = [
            item for item in filtered
            if str(item.get("status") or "").lower() in statuses
        ]
    if intents:
        filtered = [
            item for item in filtered
            if str(item.get("intent") or "").lower() in intents
            or any(token in str(item.get("summary") or "").lower() for token in intents)
            or any(token in str(item.get("title") or "").lower() for token in intents)
        ]
    return filtered


# --- _persona_intent_summary ---
def _persona_intent_summary(items: List[Dict[str, Any]], returned_count: int) -> Dict[str, Any]:
    by_source_type = _management_count_by(items, "source_type")
    by_status = _management_count_by(items, "status")
    by_intent = _management_count_by(items, "intent")
    persona_ids = sorted(
        {
            str(pid)
            for item in items
            for pid in [item.get("persona_id"), *(item.get("persona_ids") or [])]
            if str(pid or "").strip()
        }
    )
    latest_at = max(
        [str(item.get("occurred_at") or "") for item in items if item.get("occurred_at")],
        default=None,
    )
    return {
        "total_items": len(items),
        "returned_items": returned_count,
        "persona_trace_count": by_source_type.get("persona_trace", 0),
        "trainer_session_count": by_source_type.get("trainer_session", 0),
        "agora_session_count": by_source_type.get("agora_session", 0),
        "redacted_item_count": len([item for item in items if item.get("redacted")]),
        "persona_count": len(persona_ids),
        "persona_ids": persona_ids,
        "latest_at": latest_at,
        "bySourceType": by_source_type,
        "by_source_type": by_source_type,
        "byStatus": by_status,
        "by_status": by_status,
        "byIntent": by_intent,
        "by_intent": by_intent,
    }


# --- _persona_intent_surfaces ---
def _persona_intent_surfaces(
    *,
    snapshot_at: str,
) -> Dict[str, Any]:
    source_surfaces = {
        "personas": _dataset_surface_status("personas", snapshot_at=snapshot_at),
        "persona_sessions": _dataset_surface_status("sessions", snapshot_at=snapshot_at),
        "capability_snapshots": _dataset_surface_status("capability_snapshots", snapshot_at=snapshot_at),
        "teaching_sessions": _dataset_surface_status("teaching_sessions", snapshot_at=snapshot_at),
        "agora_sessions": _dataset_surface_status("agora_sessions", snapshot_at=snapshot_at),
    }
    persona_trace_surface = _aggregate_group_surface(
        "persona_traces",
        [
            source_surfaces["personas"],
            source_surfaces["persona_sessions"],
            source_surfaces["capability_snapshots"],
        ],
        snapshot_at=snapshot_at,
        unavailable_message="Persona trace intent summaries are unavailable.",
        degraded_message="Persona trace intent summaries are degraded because one or more source surfaces are degraded.",
    )
    persona_intent_surface = _aggregate_group_surface(
        "management_persona_intent",
        [
            persona_trace_surface,
            source_surfaces["teaching_sessions"],
            source_surfaces["agora_sessions"],
        ],
        snapshot_at=snapshot_at,
        unavailable_message="Persona Intent aggregate unavailable.",
        degraded_message="Persona Intent aggregate is degraded because one or more source surfaces are degraded.",
    )
    return {
        "management_persona_intent": persona_intent_surface,
        "persona_traces": persona_trace_surface,
        **source_surfaces,
    }


# --- _filter_audit_events_by_target ---
def _filter_audit_events_by_target(events: List[Dict[str, Any]], target_id: str) -> List[Dict[str, Any]]:
    return [
        event for event in events
        if str(event.get("target_id") or event.get("subject_id") or event.get("entity_id") or "") == target_id
    ]


# --- _try_register_persona_cron ---
def _try_register_persona_cron(persona_id: str) -> Optional[Dict[str, Any]]:
    """Register WORKFLOW_CATALOG as recurring OpenClaw cron jobs for *persona_id*.

    Best-effort: returns a summary dict on success, None on any error so that
    persona creation is never blocked by gateway unavailability.
    """
    try:
        from services.control_plane.cron.persona_cron_registrar import PersonaCronRegistrar
        registrar = PersonaCronRegistrar()
        result = registrar.register_for_persona(persona_id)
        return result.to_dict()
    except Exception:  # noqa: BLE001
        return None


# --- _persona_first_evaluation_readback_poll_seconds ---
def _persona_first_evaluation_readback_poll_seconds() -> float:
    raw = os.getenv(
        "PANTHEON_PERSONA_FIRST_EVALUATION_READBACK_POLL_SECONDS",
        "1",
    ).strip()
    try:
        return max(0.05, float(raw))
    except (TypeError, ValueError):
        return 1.0


# --- _register_persona_cron_required ---
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


# --- _remove_persona_cron_required ---
def _remove_persona_cron_required(persona_id: str) -> Dict[str, Any]:
    """Remove first-evaluation owner rows and require authoritative absence."""
    from services.control_plane.cron.persona_cron_registrar import PersonaCronRegistrar

    result = PersonaCronRegistrar().remove_first_evaluation_registration(persona_id)
    if result.get("registered") is not False:
        raise RuntimeError("first-evaluation schedule removal lacks zero-owner readback")
    return result


# --- _try_bootstrap_persona_ooda_packet ---
def _try_bootstrap_persona_ooda_packet(persona_id: str) -> Optional[Dict[str, Any]]:
    """Create and persist the initial open OODA loop packet for *persona_id*.

    Best-effort: returns the packet dict on success, None on any error.
    """
    try:
        from persona_ooda_bootstrap import bootstrap_persona_ooda_packet  # type: ignore[import]
        return bootstrap_persona_ooda_packet(persona_id)
    except Exception:  # noqa: BLE001
        return None


# --- _persona_readback_snapshot ---
def _persona_readback_snapshot() -> Tuple[
    Dict[str, Dict[str, Any]],
    Optional[Set[Tuple[str, str]]],
    List[Dict[str, Any]],
]:
    """Fetch owner readbacks off the async event loop for Persona projections."""
    all_bindings: Dict[str, Dict[str, Any]] = {}
    try:
        client = _runtime_manager_client()
        for binding in client.list_all():
            binding_id = binding.get("binding_id") or binding.get("id")
            if binding_id:
                all_bindings[str(binding_id)] = binding
    except Exception as exc:
        all_bindings = {}
        log.warning("Failed to batch list runtime bindings: %s", exc)

    # A (persona_id, workflow_id) set discards duplicates and every schedule,
    # payload, target, and authority identity field.  It is therefore never a
    # lifecycle proof.  Each provisioning projection performs the registrar's
    # strict owner readback instead.
    monitoring_sessions: List[Dict[str, Any]] = []
    try:
        monitoring_sessions = (
            read_store.list_authoritative_paper_runtime_monitoring_sessions()
        )
    except Exception as exc:
        log.warning("Failed to batch list paper worker sessions: %s", exc)
    return all_bindings, None, monitoring_sessions


# --- _reconcile_persona_provisioning_once ---
def _reconcile_persona_provisioning_once() -> int:
    """Materialize provisioning lifecycle from owner readbacks off read paths."""

    all_bindings, _, monitoring_sessions = _persona_readback_snapshot()
    reconciled = 0
    for raw in _list_persona_records():
        state = str(raw.get("lifecycle_state") or raw.get("state") or "").strip()
        if state not in {"provisioning", "provisioning_failed"}:
            continue
        persona_id = str(raw.get("persona_id") or raw.get("id") or "").strip()
        if not persona_id:
            continue
        try:
            _evaluate_persona_provisioning_status(
                persona_id,
                raw,
                all_bindings=all_bindings,
                all_cron_registrations=None,
                all_monitoring_sessions=monitoring_sessions,
            )
            reconciled += 1
        except Exception as exc:
            log.warning(
                "Persona provisioning reconciliation failed for %s: %s",
                persona_id,
                exc,
            )
    return reconciled


# --- _persona_provisioning_reconciler_loop ---
async def _persona_provisioning_reconciler_loop() -> None:
    interval = max(
        1,
        int(os.getenv("PANTHEON_PERSONA_PROVISIONING_RECONCILE_SECONDS", "5")),
    )
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(_reconcile_persona_provisioning_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Persona provisioning reconciliation pass failed: %s", exc)


# --- _project_persona_list_records ---
def _project_persona_list_records(raw_personas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for raw in raw_personas:
        persona_id = str(raw.get("persona_id") or raw.get("id") or "")
        items.append(
            _project_persona_dto(
                raw,
                overlay=_PERSONA_BFF_OVERLAY.get(persona_id),
                routed_strategies=_routed_strategies_for_persona(persona_id),
                evaluate_provisioning=False,
            )
        )
    return items


# --- _persona_record_tenant_id ---
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


# --- _persona_record_projected_state ---
def _persona_record_projected_state(raw: Mapping[str, Any]) -> str:
    return _normalize_lifecycle_state(
        raw.get("lifecycle_state") or raw.get("state") or raw.get("status")
    )


# --- _persona_record_archetype ---
def _persona_record_archetype(raw: Mapping[str, Any]) -> str:
    metadata = raw.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return str(
        metadata.get("archetype")
        or raw.get("strategy_family")
        or raw.get("mandate")
        or "generalist"
    )


# --- _PERSONA_CREATE_FORBIDDEN_INITIAL_MODES ---
_PERSONA_CREATE_FORBIDDEN_INITIAL_MODES = frozenset({
    "canary",
    "canary_running",
    "live",
    "live_running",
    "prod",
    "production",
    "real",
})


# --- _persona_create_requested_capital_mode ---
def _persona_create_requested_capital_mode(payload: Dict[str, Any]) -> str:
    for key in (
        "capitalMode",
        "capital_mode",
        "capitalPoolMode",
        "capital_pool_mode",
        "deploymentStage",
        "deployment_stage",
        "executionMode",
        "execution_mode",
        "initialMode",
        "initial_mode",
    ):
        value = str(payload.get(key) or "").strip().lower()
        if value:
            return value
    return "paper"


# --- _persona_create_validate_paper_only ---
def _persona_create_validate_paper_only(payload: Dict[str, Any]) -> str:
    requested = _persona_create_requested_capital_mode(payload)
    if requested in _PERSONA_CREATE_FORBIDDEN_INITIAL_MODES:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Initial Persona capital mode must be paper",
            "New Personas start in paper trading only. Canary/live capital requires a Human Inbox promotion review.",
            precondition_failed="capital_mode",
            suggestion="Create the Persona in paper mode, then request promotion review after evidence is ready.",
        )
    risk_profile = payload.get("riskProfile") or payload.get("risk_profile")
    risk_profile = risk_profile if isinstance(risk_profile, Mapping) else {}
    requested_risk = _normalize_risk_level(
        payload.get("risk")
        or payload.get("risk_level")
        or risk_profile.get("risk_level")
        or "low"
    )
    if requested_risk != "low":
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Automatic Persona provisioning requires low risk",
            (
                "Medium/high/critical Persona admission requires a governed "
                "Human Inbox review and cannot use the automatic paper-create path."
            ),
            precondition_failed="risk_level",
            suggestion="Create a low-risk paper Persona or use the governed review workflow.",
        )
    return "paper"


# --- _normalize_persona_create_name ---
def _normalize_persona_create_name(name: str) -> str:
    return " ".join(str(name or "").casefold().split())


# --- _persona_create_identity ---
def _persona_create_identity(tenant_id: str, normalized_name: str) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}\x00{normalized_name}".encode("utf-8")
    ).hexdigest()[:20]
    return f"persona-{digest}"


# --- _persona_create_canonical_payload ---
def _persona_create_canonical_payload(
    payload: Mapping[str, Any],
    *,
    name: str,
    tenant_id: str,
    requested_by: str,
) -> Dict[str, Any]:
    canonical = json.loads(json.dumps(dict(payload)))
    canonical["name"] = name
    canonical["tenant_id"] = tenant_id
    canonical.pop("tenantId", None)
    # Caller attribution is derived only from the authenticated identity.  It
    # participates in the durable request hash, so another operator cannot
    # replay the key and silently take ownership of an in-flight Persona.
    for field in (
        "owner",
        "actor_id",
        "actorId",
        "created_by",
        "createdBy",
        "requested_by",
        "requestedBy",
    ):
        canonical.pop(field, None)
    canonical["requested_by"] = requested_by
    if "budget" not in canonical:
        canonical["budget"] = canonical.get("paperBudget") or canonical.get("paper_budget")
    if "risk_policy_ref" not in canonical:
        canonical["risk_policy_ref"] = canonical.get("riskPolicyRef")
    return canonical


def _market_persona_required_data_sources(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Project the declared source requirements for a provisioned persona."""
    market = str(item.get("market") or "").upper()
    if market == "TW":
        return [
            {
                "dataset": "tw_price_daily",
                "market": "TW",
                "cadence": "daily",
                "source_class": "live_pull",
                "connector_candidates": [
                    "tw-finmind-datasets",
                    "tw-twse-tpex-official-market",
                ],
                "policy_gates": [
                    "require_connector_approved",
                    "require_schedule_active",
                    "require_source_health_ok",
                ],
            },
            {
                "dataset": "tw_broker_top",
                "market": "TW",
                "cadence": "daily",
                "source_class": "live_push",
                "connector_candidates": [
                    "tw-finmind-broker-daily-report",
                    "tw-finmind-broker-bulk-parquet",
                ],
                "policy_gates": [
                    "require_connector_approved",
                    "require_schedule_active",
                    "require_payload_push_health",
                ],
            },
        ]
    return []


# --- _persona_create_required_data_sources ---
def _persona_create_required_data_sources(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    required = payload.get("required_data_sources") or payload.get("requiredDataSources")
    market = str(payload.get("market") or "").strip().upper()
    if not required and market:
        required = _market_persona_required_data_sources({"market": market})
    return json.loads(json.dumps(required or []))


# --- _persona_record_for_provisioning ---
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
    creator = getattr(read_store, "create_persona", None)
    updater = getattr(read_store, "update_persona", None)
    existing = read_store.get_persona(record.persona_id)
    if existing is None:
        if mutate_store and callable(creator):
            persona = creator(
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
        if mutate_store and callable(updater):
            persona = updater(
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


# --- _persona_create_response ---
def _persona_create_response(
    record: ProvisioningRecord,
    *,
    persona: Dict[str, Any],
    metadata: Dict[str, Any],
    payload: Mapping[str, Any],
    snapshot_at: str,
    ooda_packet: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ids = deterministic_provisioning_ids(record)
    overlay = _project_persona_dto(
        persona,
        overlay={
            "routedStrategies": int(payload.get("routedStrategies") or 0),
            "successRate": float(payload.get("successRate") or 0.0),
            "capitalMode": "paper",
            "paperLedgerId": metadata["paper_ledger_id"],
            "paperLedger": metadata["paper_ledger"],
            "legacyPaperCapitalPoolId": ids.capital_pool_id,
            "deploymentPlanId": ids.deployment_plan_id,
            "deploymentStage": "paper",
            "evidenceRefs": list(metadata["evidence_refs"]),
            "runtimeId": metadata.get("runtime_id"),
            "runtimeBindingId": metadata.get("runtime_binding_id"),
            "tenantId": record.tenant_id,
        },
        routed_strategies=0,
        evaluate_provisioning=False,
    )
    _PERSONA_BFF_OVERLAY[record.persona_id] = overlay
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "create_flow": "durable_owner_coordinated_provisioning",
        "provisioning_state": record.state,
        "provisioning_step": record.current_step,
        "authoritative_receipt_steps": sorted(record.references),
        "capital_mode": "paper",
        "paper_ledger_id": metadata["paper_ledger_id"],
        "legacy_paper_capital_pool_id": ids.capital_pool_id,
        "persona_capital_binding_id": ids.persona_capital_binding_id,
        "runtime_binding_id": metadata.get("runtime_binding_id"),
        "runtime_id": metadata.get("runtime_id"),
        "registry_id": ids.registry_id,
        "approval_decision_id": ids.approval_decision_id,
        "deployment_plan_id": ids.deployment_plan_id,
        "deployment_saga_id": ids.deployment_saga_id,
        "first_evaluation_workflow_id": _PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
        "dispatch_admitted": "deployment_dispatch" in record.references,
        "live_capital_side_effects": False,
        "human_review_required_for_live": True,
    }
    if ooda_packet:
        meta["ooda_packet_id"] = ooda_packet.get("packet_id")
        meta["ooda_loop_status"] = ooda_packet.get("status")
    if record.error:
        meta["terminal_error"] = deepcopy(record.error)
    if record.compensation:
        meta["compensation"] = deepcopy(record.compensation)
    return {"data": overlay, "meta": meta}


# --- _ensure_persona_ooda_packet ---
def _ensure_persona_ooda_packet(persona_id: str, capital_pool_id: str) -> Optional[Dict[str, Any]]:
    try:
        for packet in read_store.list_ooda_packets():
            if persona_id in [str(value) for value in (packet.get("persona_ids") or [])]:
                return packet
        from persona_ooda_bootstrap import bootstrap_persona_ooda_packet  # type: ignore[import]

        return bootstrap_persona_ooda_packet(
            persona_id,
            capital_pool_id=capital_pool_id,
        )
    except Exception:  # noqa: BLE001
        return None


# --- _coordinate_persona_create ---
def _coordinate_persona_create(
    record: ProvisioningRecord,
    *,
    payload: Mapping[str, Any],
    owner: str,
) -> Tuple[ProvisioningRecord, Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    store = _persona_provisioning_store()
    active, _ = store.reserve(
        tenant_id=record.tenant_id,
        idempotency_key=record.idempotency_key,
        request_hash=record.request_hash,
        normalized_name=record.normalized_name,
        persona_id=record.persona_id,
        request_payload=record.request_payload,
    )
    coordinator = PersonaProvisioningCoordinator(
        store=store,
        transport=_PersonaOwnerHttpTransport(),
        schedule_registrar=_register_persona_cron_required,
        lease_owner=f"operator-bff:{os.getenv('HOSTNAME', 'local')}:{uuid.uuid4().hex}",
        lease_seconds=max(
            30,
            int(os.getenv("PANTHEON_PERSONA_PROVISIONING_LEASE_SECONDS", "180")),
        ),
    )
    try:
        active = coordinator.coordinate(active)
    except PersonaProvisioningCoordinationError:
        # A concurrent replica may hold the lease.  Return only the durable
        # progress already visible; never manufacture downstream success.
        latest = store.get(active.tenant_id, active.idempotency_key)
        if latest is None or latest.state not in {"reserved", "provisioning"}:
            raise
        active = latest
    persona, metadata = _persona_record_for_provisioning(
        active,
        payload=payload,
        owner=owner,
        mutate_store=True,
    )
    ooda_packet = None
    if active.state not in {"failed", "compensated"}:
        ooda_packet = _ensure_persona_ooda_packet(
            active.persona_id,
            deterministic_provisioning_ids(active).capital_pool_id,
        )
    return active, persona, metadata, ooda_packet


# --- _persona_provisioning_authoritative_meta ---
def _persona_provisioning_authoritative_meta(raw: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = raw.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    readback = metadata.get("provisioning_authoritative_readback")
    readback = readback if isinstance(readback, Mapping) else {}
    schedule = readback.get("first_evaluation_schedule")
    schedule = schedule if isinstance(schedule, Mapping) else {}
    runtime_binding = readback.get("runtime_binding")
    runtime_binding = runtime_binding if isinstance(runtime_binding, Mapping) else {}
    paper_worker = readback.get("paper_worker")
    paper_worker = paper_worker if isinstance(paper_worker, Mapping) else {}

    result: Dict[str, Any] = {
        "available": bool(readback),
        "observed_at": readback.get("observed_at"),
    }
    if runtime_binding:
        result["runtime_binding"] = {
            "runtime_binding_id": (
                runtime_binding.get("runtime_binding_id")
                or runtime_binding.get("binding_id")
                or runtime_binding.get("id")
            ),
            "runtime_id": runtime_binding.get("runtime_id"),
            "plan_id": runtime_binding.get("plan_id"),
            "status": runtime_binding.get("status") or runtime_binding.get("state"),
        }
    if paper_worker:
        result["paper_worker"] = {
            "session_id": paper_worker.get("session_id") or paper_worker.get("id"),
            "runtime_id": paper_worker.get("runtime_id"),
            "runtime_binding_id": (
                paper_worker.get("runtime_binding_id")
                or paper_worker.get("binding_id")
            ),
            "status": paper_worker.get("status"),
            "last_heartbeat_at": paper_worker.get("last_heartbeat_at"),
        }
    if schedule:
        result["first_evaluation_schedule"] = {
            "persona_id": schedule.get("persona_id"),
            "workflow_id": schedule.get("workflow_id"),
            "registered": schedule.get("registered"),
            "job_id": schedule.get("job_id"),
            "job_name": schedule.get("job_name"),
            "request_id": schedule.get("request_id"),
            "runtime_id": schedule.get("runtime_id"),
            "runtime_binding_id": schedule.get("runtime_binding_id"),
            "capital_pool_id": schedule.get("capital_pool_id"),
            "persona_capital_binding_id": schedule.get("persona_capital_binding_id"),
        }
    return result


# --- _PERSONA_PATCH_SERVER_MANAGED_FIELDS ---
_PERSONA_PATCH_SERVER_MANAGED_FIELDS = frozenset({
    "owner",
    "state",
    "status",
    "lifecycle_state",
    "lifecycleState",
    "lifecycleStatus",
    "paper_runtime_state",
    "paperRuntimeState",
    "runtime_id",
    "runtimeId",
    "runtime_binding_id",
    "runtimeBindingId",
    "capital_mode",
    "capitalMode",
    "deployment_stage",
    "deploymentStage",
    "created_by",
    "createdBy",
    "actor_id",
    "actorId",
    "availableActions",
})


# --- _ensure_persona_exists ---
def _ensure_persona_exists(persona_id: str, caller_tenant: Optional[str] = None) -> None:
    directory = _get_persona_directory_snapshot(caller_tenant)
    if persona_id in directory.records_by_id:
        return
    raise _bff_error(
        404, ErrorCode.RESOURCE_NOT_FOUND,
        "Persona not found",
        f"Persona {persona_id} does not exist",
    )


# --- _retrieve_canonical_persona_memory ---
def _retrieve_canonical_persona_memory(
    persona_id: str,
    identity: OperatorIdentity,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Read persona memory without falling back to BFF snapshots or workspace files."""
    base_url = (
        os.getenv("PANTHEON_MEMORY_API_URL")
        or os.getenv("PANTHEON_MEMORY_SERVICE_URL")
        or ""
    ).strip().rstrip("/")
    source = {
        "kind": "canonical_memory_plane",
        "endpoint": "/api/memory/retrieve",
        "available": False,
        "fallback_used": False,
        "workspace_is_source_of_truth": False,
    }
    if not base_url:
        return [], source | {
            "reason": "memory_plane_unconfigured",
            "repair_action": "configure_memory_service_url",
        }

    params = urlencode(
        {
            "actor_id": identity.operator_id,
            "actor_roles": ",".join(sorted(identity.roles)),
            "session_id": f"bff-persona-memory-{persona_id}",
            "persona_id": persona_id,
            "session_persona_id": persona_id,
            "scope": "persona",
            "limit": 100,
        }
    )
    timeout = float(os.getenv("PANTHEON_MEMORY_API_TIMEOUT_SECONDS", "3"))
    try:
        with urllib_request.urlopen(
            urllib_request.Request(f"{base_url}/api/memory/retrieve?{params}", method="GET"),
            timeout=timeout,
        ) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib_error.HTTPError as exc:
        reason = "memory_plane_access_denied" if exc.code in {401, 403} else "memory_plane_http_error"
        return [], source | {"reason": reason, "http_status": exc.code, "repair_action": "check_memory_plane_health_and_authz"}
    except (urllib_error.URLError, TimeoutError, OSError):
        return [], source | {"reason": "memory_plane_unavailable", "repair_action": "check_memory_plane_health"}
    except (ValueError, json.JSONDecodeError):
        return [], source | {"reason": "memory_plane_invalid_response", "repair_action": "check_memory_plane_contract"}

    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        return [], source | {"reason": "memory_plane_invalid_response", "repair_action": "check_memory_plane_contract"}
    items = []
    for hit in hits:
        if not isinstance(hit, dict) or hit.get("type") != "persona" or not isinstance(hit.get("entry"), dict):
            continue
        entry = dict(hit["entry"])
        entry["relevance_score"] = hit.get("relevance_score")
        items.append(entry)
    return items, source | {
        "available": True,
        "reason": None,
        "authz_policy_version": (payload.get("authz") or {}).get("policy_version"),
        "returned_items": len(items),
    }


# --- _pm12_persona_route_summary ---
def _pm12_persona_route_summary(persona_id: str) -> Dict[str, Any]:
    policy = read_store.get_route_policy_for_persona(persona_id) or {}
    rules = policy.get("rules") if isinstance(policy.get("rules"), list) else []
    consult_policy = policy.get("consult_policy") if isinstance(policy.get("consult_policy"), dict) else {}
    trigger_rules = (
        consult_policy.get("trigger_rules")
        if isinstance(consult_policy.get("trigger_rules"), list)
        else []
    )
    blocking_count = 0
    for rule in list(rules) + list(trigger_rules):
        if not isinstance(rule, dict):
            continue
        mode = str(rule.get("mode") or rule.get("decision_mode") or "").lower()
        if mode in {"blocking", "block", "hard_gate", "consult_required"}:
            blocking_count += 1
    return {
        "version": policy.get("version") or consult_policy.get("version"),
        "rule_count": len(rules),
        "consult_rule_count": len(trigger_rules),
        "blocking_rule_count": blocking_count,
        "has_policy": bool(policy or consult_policy),
    }


# --- _pm12_persona_capability_summary ---
def _pm12_persona_capability_summary(persona_id: str) -> Dict[str, Any]:
    snapshot = read_store.get_capability_snapshot_for_persona(persona_id) or {}
    skills = list(snapshot.get("effective_skills") or [])
    tools = list(snapshot.get("effective_tools") or [])
    workflows = list(snapshot.get("effective_workflows") or [])
    restrictions = list(snapshot.get("restrictions") or [])
    return {
        "snapshot_id": snapshot.get("snapshot_id") or snapshot.get("id"),
        "generated_at": snapshot.get("generated_at"),
        "skill_count": len(skills),
        "tool_count": len(tools),
        "workflow_count": len(workflows),
        "restriction_count": len(restrictions),
        "source_ref_count": len(snapshot.get("source_refs") or []),
        "has_snapshot": bool(snapshot),
    }


# --- _pm12_persona_binding_summary ---
def _pm12_persona_binding_summary(
    persona_id: str,
    *,
    runtime_bindings: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    bindings = read_store.get_bindings_for_persona(persona_id) or []
    binding_ids = _pm12_compact_ids(
        bindings,
        ("persona_capital_binding_id", "binding_id", "id"),
    )
    binding_id_set = set(binding_ids)
    runtime_bindings = [
        runtime
        for runtime in (
            runtime_bindings
            if runtime_bindings is not None
            else (read_store.list_runtime_bindings() or [])
        )
        if str(runtime.get("persona_id") or "").strip() == persona_id
        or str(runtime.get("persona_capital_binding_id") or "").strip() in binding_id_set
    ]
    pool_ids = _pm12_compact_ids(bindings, ("capital_pool_id", "pool_id"))
    pool_refs: List[Dict[str, Any]] = []
    for pool_id in pool_ids:
        pool = read_store.get_capital_pool(pool_id) or {}
        pool_refs.append({
            "id": pool_id,
            "name": pool.get("name") or pool_id,
            "status": pool.get("status"),
        })
    active_count = 0
    deployment_scopes: List[str] = []
    for binding in bindings:
        status = str(binding.get("status") or binding.get("validity") or "").lower()
        if status in {"active", "ready", "bound"}:
            active_count += 1
        scope = binding.get("allowed_deployment_scope") or binding.get("deployment_stage")
        if scope not in (None, "") and str(scope) not in deployment_scopes:
            deployment_scopes.append(str(scope))
    return {
        "total": len(bindings),
        "active": active_count,
        "binding_ids": binding_ids,
        "runtime_ids": _pm12_compact_ids(runtime_bindings, ("runtime_id",)),
        "runtime_binding_ids": _pm12_compact_ids(
            runtime_bindings,
            ("runtime_binding_id", "binding_id", "id"),
        ),
        "capital_pool_ids": pool_ids,
        "capital_pools": pool_refs,
        "deployment_scopes": deployment_scopes,
        "status_counts": _pm12_status_counts(bindings),
    }


# --- _pm12_persona_evaluation_summary ---
def _pm12_persona_evaluation_summary(persona_id: str) -> Dict[str, Any]:
    teaching = read_store.get_teaching_sessions_for_persona(persona_id) or []
    completed = [
        item for item in teaching
        if str(item.get("status") or "").lower() in {"completed", "complete", "passed"}
    ]
    outcome_count = 0
    for item in teaching:
        outcomes = item.get("outcomes")
        if isinstance(outcomes, list):
            outcome_count += len(outcomes)
    return {
        "total": len(teaching),
        "completed": len(completed),
        "latest_at": _pm12_latest_timestamp(
            teaching,
            ("completed_at", "updated_at", "started_at", "created_at"),
        ),
        "outcome_count": outcome_count,
        "status_counts": _pm12_status_counts(teaching),
    }


# --- _pm12_persona_health_summary ---
def _pm12_persona_health_summary(persona: Dict[str, Any], sessions: Dict[str, Any], capabilities: Dict[str, Any]) -> Dict[str, Any]:
    state = str(persona.get("lifecycle_state") or persona.get("state") or "").lower()
    is_operational = _is_persona_lifecycle_operational(state)
    health = "healthy" if is_operational else "degraded"
    if is_operational and capabilities.get("has_snapshot") is False:
        health = "degraded"
    return {
        "health": health,
        "lifecycle_state": state or "unknown",
        "active_session_count": sessions.get("active", 0),
        "has_capability_snapshot": bool(capabilities.get("has_snapshot")),
    }


# --- _PM12_LEAGUE_SCORE_WEIGHTS ---
_PM12_LEAGUE_SCORE_WEIGHTS = {
    "pnl": 0.35,
    "risk": 0.25,
    "execution": 0.25,
    "activity": 0.15,
}


# --- _PM12_LEAGUE_RANKING_CRITERIA ---
_PM12_LEAGUE_RANKING_CRITERIA = {
    "overall": ("overall_score", "Overall"),
    "pnl": ("pnl_score", "PnL"),
    "risk": ("risk_score", "Risk"),
    "execution": ("execution_score", "Execution"),
    "activity": ("activity_score", "Activity"),
}


# --- _PM12_LEAGUE_MOVER_DIRECTIONS ---
_PM12_LEAGUE_MOVER_DIRECTIONS = {"all", "up", "down", "flat", "new"}


# --- _PM12_LEAGUE_FORMULA_VERSION ---
_PM12_LEAGUE_FORMULA_VERSION = "pm12-default-v1"


# --- _PM12_QUARTERLY_FORMULA_DOC_REF ---
_PM12_QUARTERLY_FORMULA_DOC_REF = (
    "docs/04/pantheon_bff_api_gap_2026-05-23/"
    "BFF_API_GAP_final_integration_spec.md#b34-pm-12-composition-sources"
)


# --- _PM12_QUARTERLY_FORMULA_GOVERNANCE_REF_ID ---
_PM12_QUARTERLY_FORMULA_GOVERNANCE_REF_ID = (
    "pm12-quarterly-ranking-formula-v1-governance"
)


# --- _PM12_QUARTERLY_FORMULA_EFFECTIVE_AT ---
_PM12_QUARTERLY_FORMULA_EFFECTIVE_AT = "2026-05-23T00:00:00Z"


# --- _PM12_HEATMAP_BUCKET_DELTAS ---
_PM12_HEATMAP_BUCKET_DELTAS = {
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(days=7),
}


# --- _PM12_LEAGUE_TIER_DEFINITIONS ---
_PM12_LEAGUE_TIER_DEFINITIONS = [
    {
        "id": "tier-1",
        "tier_id": "tier-1",
        "label": "League Leader",
        "min_score": 85.0,
        "max_score": 100.0,
        "governance_posture": "promotion_candidate",
    },
    {
        "id": "tier-2",
        "tier_id": "tier-2",
        "label": "Production Candidate",
        "min_score": 70.0,
        "max_score": 84.999,
        "governance_posture": "maintain_or_expand_paper",
    },
    {
        "id": "tier-3",
        "tier_id": "tier-3",
        "label": "Observation",
        "min_score": 55.0,
        "max_score": 69.999,
        "governance_posture": "continue_observation",
    },
    {
        "id": "tier-4",
        "tier_id": "tier-4",
        "label": "Incubation",
        "min_score": 0.0,
        "max_score": 54.999,
        "governance_posture": "research_only",
    },
]


# --- _pm12_persona_league_scores ---
def _pm12_persona_league_scores(row: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, float]:
    pnl = _management_number(metrics.get("pnl"))
    drawdown = _management_number(metrics.get("drawdown"))
    fill_rate = _management_number(metrics.get("fill_rate"))
    slippage = _management_number(metrics.get("avg_slippage_bps"))
    sessions = row.get("session_summary") if isinstance(row.get("session_summary"), dict) else {}
    evaluations = row.get("evaluation_summary") if isinstance(row.get("evaluation_summary"), dict) else {}
    capabilities = row.get("capability_summary") if isinstance(row.get("capability_summary"), dict) else {}
    memory = row.get("memory_summary") if isinstance(row.get("memory_summary"), dict) else {}

    pnl_score = 50.0 if pnl is None else _pm12_clamp_score(50.0 + (pnl * 100.0))
    risk_floor = {
        "low": 90.0,
        "medium": 70.0,
        "high": 45.0,
        "critical": 20.0,
        "unknown": 60.0,
    }.get(str(row.get("risk") or "unknown").lower(), 60.0)
    drawdown_score = 65.0 if drawdown is None else _pm12_clamp_score(100.0 - (drawdown * 400.0))
    risk_score = round((risk_floor + drawdown_score) / 2.0, 6)
    execution_score = 50.0
    if fill_rate is not None:
        execution_score = fill_rate * 100.0
        if slippage is not None:
            execution_score -= slippage * 2.0
    execution_score = _pm12_clamp_score(execution_score)
    activity_score = _pm12_clamp_score(
        (float(sessions.get("active") or 0) * 20.0)
        + (float(evaluations.get("completed") or 0) * 15.0)
        + (10.0 if capabilities.get("has_snapshot") else 0.0)
        + (float(row.get("routed_strategy_count") or 0) * 5.0)
        + min(float(memory.get("total") or 0) * 2.0, 10.0)
    )
    overall = (
        pnl_score * _PM12_LEAGUE_SCORE_WEIGHTS["pnl"]
        + risk_score * _PM12_LEAGUE_SCORE_WEIGHTS["risk"]
        + execution_score * _PM12_LEAGUE_SCORE_WEIGHTS["execution"]
        + activity_score * _PM12_LEAGUE_SCORE_WEIGHTS["activity"]
    )
    return {
        "overall_score": round(overall, 6),
        "pnl_score": pnl_score,
        "risk_score": risk_score,
        "execution_score": execution_score,
        "activity_score": activity_score,
    }


# --- _pm12_quarter_formula_governance_evidence_refs ---
def _pm12_quarter_formula_governance_evidence_refs() -> List[Dict[str, Any]]:
    return [
        {
            "id": _PM12_QUARTERLY_FORMULA_GOVERNANCE_REF_ID,
            "ref_id": _PM12_QUARTERLY_FORMULA_GOVERNANCE_REF_ID,
            "title": "PM-12 quarterly ranking formula governance baseline",
            "display_label": "PM-12 quarterly ranking formula governance baseline",
            "source_type": "governance_record",
            "source_ref": _PM12_QUARTERLY_FORMULA_DOC_REF,
            "captured_at": _PM12_QUARTERLY_FORMULA_EFFECTIVE_AT,
            "link_type": "formula_version_governance",
            "credibility": {
                "tier": "primary",
                "verified": True,
                "last_verified_at": _PM12_QUARTERLY_FORMULA_EFFECTIVE_AT,
                "verification_method": "task_review",
            },
            "linked_object_summary": {
                "entity_type": "ranking_formula",
                "entity_ref": "pm12-quarterly-ranking-formula",
                "display_label": "PM-12 quarterly ranking formula",
            },
            "resolved_link": {
                "availability": "available",
                "route_href": _PM12_QUARTERLY_FORMULA_DOC_REF,
                "display_label": "Open PM-12 integration spec",
                "open_in_new_tab": False,
            },
            "route_href": _PM12_QUARTERLY_FORMULA_DOC_REF,
        }
    ]


# --- _pm12_quarter_formula_version_history ---
def _pm12_quarter_formula_version_history() -> List[Dict[str, Any]]:
    evidence_ref_ids = [
        ref["ref_id"] for ref in _pm12_quarter_formula_governance_evidence_refs()
    ]
    return [
        {
            "id": f"pm12-quarterly-ranking-formula-{_PM12_LEAGUE_FORMULA_VERSION}",
            "version": _PM12_LEAGUE_FORMULA_VERSION,
            "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
            "effective_at": _PM12_QUARTERLY_FORMULA_EFFECTIVE_AT,
            "change_type": "baseline",
            "governance_evidence_refs": evidence_ref_ids,
            "description": "Baseline formula accepted for PM-12 quarterly ranking reads.",
        }
    ]


# --- _pm12_quarter_formula_payload ---
def _pm12_quarter_formula_payload() -> Dict[str, Any]:
    evidence_ref_ids = [
        ref["ref_id"] for ref in _pm12_quarter_formula_governance_evidence_refs()
    ]
    version_history = _pm12_quarter_formula_version_history()
    change_control = {
        "version_policy": "formula_version_changes_require_governance_evidence",
        "requires_governance_evidence": True,
        "governance_evidence_refs": evidence_ref_ids,
        "authority": "read_only_governance_advisory",
    }
    return {
        "id": "pm12-quarterly-ranking-formula",
        "formula_id": "pm12-quarterly-ranking-formula",
        "version": _PM12_LEAGUE_FORMULA_VERSION,
        "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
        "weights": dict(_PM12_LEAGUE_SCORE_WEIGHTS),
        "score_field": "overall_score",
        "components": [
            {"key": "pnl", "label": "PnL", "weight": _PM12_LEAGUE_SCORE_WEIGHTS["pnl"]},
            {"key": "risk", "label": "Risk", "weight": _PM12_LEAGUE_SCORE_WEIGHTS["risk"]},
            {"key": "execution", "label": "Execution", "weight": _PM12_LEAGUE_SCORE_WEIGHTS["execution"]},
            {"key": "activity", "label": "Activity", "weight": _PM12_LEAGUE_SCORE_WEIGHTS["activity"]},
        ],
        "basis": "latest_available_persona_league_metrics_with_quarter_window",
        "policy": "read_only_governance_advisory",
        "governance_evidence_refs": evidence_ref_ids,
        "version_history": version_history,
        "change_control": change_control,
    }


# --- _pm12_evidence_timestamp ---
def _pm12_evidence_timestamp(item: Dict[str, Any]) -> Optional[datetime]:
    source_document = item.get("source_document") if isinstance(item.get("source_document"), dict) else {}
    return _audit_datetime(source_document.get("captured_at") or item.get("created_at"))


# --- _pm12_quarter_evidence_refs ---
def _pm12_quarter_evidence_refs(
    evidence_refs: List[Dict[str, Any]],
    quarter_window: Dict[str, Any],
) -> List[Dict[str, Any]]:
    start_at = _audit_datetime(quarter_window.get("start_at"))
    end_exclusive_at = _audit_datetime(quarter_window.get("end_exclusive_at"))
    if start_at is None or end_exclusive_at is None:
        return []
    return [
        item
        for item in evidence_refs
        for timestamp in [_pm12_evidence_timestamp(item)]
        if timestamp is not None and start_at <= timestamp < end_exclusive_at
    ]


# --- _pm12_public_quarter_evidence_refs ---
def _pm12_public_quarter_evidence_refs(
    identity: OperatorIdentity,
    quarter_window: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, bool]:
    raw_evidence_refs = read_store.list_evidence_refs()
    evidence_dataset_available = read_store.dataset_source("evidence_refs") != "missing"
    quarter_evidence_refs = (
        _pm12_quarter_evidence_refs(raw_evidence_refs, quarter_window)
        if evidence_dataset_available
        else []
    )
    try:
        capabilities = _capabilities_for_identity(identity)
    except Exception:
        capabilities = None
    processed_evidence_refs, redacted_count = redact_evidence_refs(
        identity,
        quarter_evidence_refs,
        capabilities=capabilities,
    )
    return (
        [
            _management_evidence_public_item(item)
            for item in processed_evidence_refs
            if isinstance(item, dict)
        ],
        quarter_evidence_refs,
        redacted_count,
        evidence_dataset_available,
    )


# --- _pm12_quarterly_ranking_governance_state ---
def _pm12_quarterly_ranking_governance_state(persona_id: str, quarter: str) -> str:
    clean_quarter = str(quarter or "").strip().lower()
    clean_persona = str(persona_id or "").strip()
    if not clean_quarter or not clean_persona:
        return "recommendation"

    has_submission = False
    has_decision = False
    decision_value = None
    applied = False
    blocked = False
    expired = False

    for record in command_store._get_all_commands():
        target = record.get("target") if isinstance(record.get("target"), dict) else {}
        params = record.get("params") if isinstance(record.get("params"), dict) else {}
        cmd_type = record.get("type")
        cmd_status = record.get("status")

        if cmd_type == CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT.value:
            rec_id = str(params.get("recommendation_id") or params.get("recommendationId") or "").strip().lower()
            if rec_id.startswith(f"pm12-{clean_quarter}-{clean_persona}-"):
                has_submission = True
                if cmd_status == "failed":
                    pass
                elif cmd_status == "blocked":
                    blocked = True
                elif cmd_status == "expired":
                    expired = True
        elif target.get("type") == ObjectType.HUMAN_GATE_ITEM.value:
            target_id = str(target.get("id") or "").strip().lower()
            if target_id.startswith(f"promotion_review:pm12-{clean_quarter}-{clean_persona}-"):
                has_decision = True
                decision_value = params.get("decision")
                if cmd_status == "executed" or cmd_status == "applied" or params.get("applied") is True:
                    applied = True
                elif cmd_status == "failed":
                    pass
                elif cmd_status == "blocked":
                    blocked = True

    if applied:
        return "applied receipt"
    if has_decision:
        if decision_value == "approve" or decision_value == "approve_with_conditions":
            return "approved review"
        elif decision_value == "reject":
            return "rejected"
    if blocked:
        return "blocked"
    if expired:
        return "expired"
    if has_submission:
        return "submitted review"
    return "recommendation"


# --- _pm12_quarterly_ranking_items ---
def _pm12_quarterly_ranking_items(
    rows: List[Dict[str, Any]],
    *,
    quarter_window: Dict[str, Any],
    telemetry_cache: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    def ranking_item(row: Dict[str, Any]) -> Dict[str, Any]:
        if telemetry_cache is None:
            return _pm12_persona_league_ranking_item(row)
        return _pm12_persona_league_ranking_item(
            row,
            telemetry_cache=telemetry_cache,
        )

    ranked = sorted(
        (ranking_item(row) for row in rows),
        key=lambda item: (
            _management_number(item.get("overall_score")) or 0.0,
            str(item.get("persona_id") or ""),
        ),
        reverse=True,
    )
    items: List[Dict[str, Any]] = []
    for rank, item in enumerate(ranked, start=1):
        score = _management_number(item.get("overall_score")) or 0.0
        persona_id = str(item.get("persona_id") or "")
        quarter = quarter_window["quarter"]
        gov_state = _pm12_quarterly_ranking_governance_state(persona_id, quarter)
        ranking_item = {
            **item,
            "rank": rank,
            "score": score,
            "score_field": "overall_score",
            "quarter": quarter,
            "quarter_window": quarter_window,
            "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
            "basis": "latest_available_persona_league_metrics_with_quarter_window",
            "period": "quarter",
            "criteria": "overall",
            "governance_state": gov_state,
        }
        ranking_item["allocation_policy_input"] = build_pm12_allocation_policy_input(
            ranking_item
        )
        items.append(ranking_item)
    return items


# --- _pm12_quarterly_find_persona_item ---
def _pm12_quarterly_find_persona_item(
    ranked_items: List[Dict[str, Any]],
    persona_id: str,
) -> Optional[Dict[str, Any]]:
    clean_persona_id = str(persona_id or "").strip()
    if not clean_persona_id:
        return None
    for item in ranked_items:
        item_persona_id = str(item.get("persona_id") or item.get("personaId") or item.get("id") or "").strip()
        if item_persona_id == clean_persona_id:
            return item
    return None


# --- _pm12_quarterly_find_persona_row ---
def _pm12_quarterly_find_persona_row(
    rows: List[Dict[str, Any]],
    persona_id: str,
) -> Dict[str, Any]:
    clean_persona_id = str(persona_id or "").strip()
    for row in rows:
        row_persona_id = str(row.get("persona_id") or row.get("personaId") or row.get("id") or "").strip()
        if row_persona_id == clean_persona_id:
            return row
    return {}


# --- _pm12_quarterly_component_score ---
def _pm12_quarterly_component_score(
    components: Dict[str, Any],
    score_field: str,
) -> float:
    camel_score_fields = {
        "pnl_score": "pnlScore",
        "risk_score": "riskScore",
        "execution_score": "executionScore",
        "activity_score": "activityScore",
        "overall_score": "overallScore",
    }
    value = _management_number(components.get(score_field))
    if value is None:
        value = _management_number(components.get(camel_score_fields.get(score_field, "")))
    return value or 0.0


# --- _pm12_quarterly_drilldown_contributions ---
def _pm12_quarterly_drilldown_contributions(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    components = item.get("components") if isinstance(item.get("components"), dict) else {}
    formula = _pm12_quarter_formula_payload()
    contribution_rows: List[Dict[str, Any]] = []
    total_weighted = 0.0

    for component in formula.get("components") or []:
        key = str(component.get("key") or "").strip()
        if key not in _PM12_LEAGUE_RANKING_CRITERIA:
            continue
        score_field, label = _PM12_LEAGUE_RANKING_CRITERIA[key]
        weight = _management_number(component.get("weight")) or 0.0
        score = _pm12_quarterly_component_score(components, score_field)
        weighted = round(score * weight, 6)
        total_weighted += weighted
        contribution_rows.append({
            "id": f"{item.get('persona_id') or item.get('id')}-{key}",
            "key": key,
            "label": label,
            "score_field": score_field,
            "score": score,
            "weight": weight,
            "weighted_contribution": weighted,
            "basis": "component_score_x_formula_weight",
        })

    denominator = total_weighted if total_weighted > 0 else None
    for row in contribution_rows:
        weighted = _management_number(row.get("weighted_contribution")) or 0.0
        share = round(weighted / denominator, 6) if denominator else 0.0
        row["contribution_share"] = share

    return contribution_rows


# --- _pm12_quarterly_drilldown_payload ---
def _pm12_quarterly_drilldown_payload(
    *,
    item: Dict[str, Any],
    row: Dict[str, Any],
    quarter_window: Dict[str, Any],
    ranked_count: int,
    evidence_refs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    persona_id = str(item.get("persona_id") or item.get("personaId") or item.get("id") or "")
    formula = _pm12_quarter_formula_payload()
    contributions = _pm12_quarterly_drilldown_contributions(item)
    total_weighted = round(
        sum(_management_number(entry.get("weighted_contribution")) or 0.0 for entry in contributions),
        6,
    )
    score = _management_number(item.get("score")) or _management_number(item.get("overall_score")) or total_weighted
    route_policy = row.get("route_policy_summary") if isinstance(row.get("route_policy_summary"), dict) else {}
    capabilities = row.get("capability_summary") if isinstance(row.get("capability_summary"), dict) else {}
    bindings = row.get("binding_summary") if isinstance(row.get("binding_summary"), dict) else {}
    sessions = row.get("session_summary") if isinstance(row.get("session_summary"), dict) else {}
    evaluations = row.get("evaluation_summary") if isinstance(row.get("evaluation_summary"), dict) else {}
    memory = row.get("memory_summary") if isinstance(row.get("memory_summary"), dict) else {}
    source_breakdown = {
        "metrics": item.get("metrics") or {},
        "components": item.get("components") or {},
        "health": row.get("health_summary") or {},
        "route_policy_summary": {
            "rule_count": route_policy.get("rule_count") or route_policy.get("ruleCount") or 0,
            "consult_rule_count": route_policy.get("consult_rule_count") or route_policy.get("consultRuleCount") or 0,
            "blocking_rule_count": route_policy.get("blocking_rule_count") or route_policy.get("blockingRuleCount") or 0,
            "has_policy": bool(route_policy.get("has_policy") or route_policy.get("hasPolicy")),
        },
        "capability_count": capabilities.get("skill_count") or capabilities.get("skillCount") or 0,
        "binding_count": bindings.get("total") or 0,
        "session_count": sessions.get("total") or 0,
        "evaluation_count": evaluations.get("total") or 0,
        "memory_count": memory.get("total") or 0,
    }
    summary = {
        "quarter": quarter_window["quarter"],
        "persona_id": persona_id,
        "rank": item.get("rank"),
        "ranked_count": ranked_count,
        "score": score,
        "overall_score": item.get("overall_score") or item.get("overallScore"),
        "formula_version": item.get("formula_version") or formula["formula_version"],
        "component_count": len(contributions),
        "total_weighted_contribution": total_weighted,
        "evidence_ref_count": len(evidence_refs),
        "basis": item.get("basis") or formula["basis"],
        "ranking_snapshot_id": item.get("ranking_snapshot_id"),
    }
    return {
        "id": f"pm12-quarterly-ranking-drilldown-{quarter_window['quarter'].lower()}-{persona_id}",
        "ranking_snapshot_id": item.get("ranking_snapshot_id"),
        "quarter": quarter_window["quarter"],
        "quarter_window": quarter_window,
        "persona_id": persona_id,
        "rank": item.get("rank"),
        "score": score,
        "ranking_item": item,
        "formula": formula,
        "contributions": contributions,
        "contribution_breakdown": contributions,
        "source_breakdown": source_breakdown,
        "evidence_refs": evidence_refs,
        "summary": summary,
        "links": {
            "parent_ranking": f"/bff/management/quarterly-ranking?quarter={quarter_window['quarter']}",
            "persona": f"/bff/personas/{persona_id}",
        },
    }


# --- _pm12_merge_evidence_refs ---
def _pm12_merge_evidence_refs(*groups: Any) -> List[Any]:
    merged: List[Any] = []
    seen: Set[str] = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for ref in group:
            key = _pm12_evidence_ref_key(ref)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(json.loads(json.dumps(ref)))
    return merged


# --- _pm12_attach_ranking_evidence ---
def _pm12_attach_ranking_evidence(
    items: List[Dict[str, Any]],
    public_evidence_refs: List[Dict[str, Any]],
    *,
    canonical_evidence_refs: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    canonical_refs = canonical_evidence_refs if canonical_evidence_refs is not None else public_evidence_refs
    public_by_id = {
        _pm12_evidence_ref_key(ref): ref
        for ref in public_evidence_refs
        if _pm12_evidence_ref_key(ref)
    }
    canonical_ids_by_entity: Dict[tuple[str, str], List[str]] = {}
    for ref in canonical_refs:
        linked = (
            ref.get("linked_object_summary")
            if isinstance(ref.get("linked_object_summary"), dict)
            else {}
        )
        entity_key = (
            str(linked.get("entity_type") or "").strip().lower(),
            str(linked.get("entity_ref") or "").strip(),
        )
        ref_id = _pm12_evidence_ref_key(ref)
        if all(entity_key) and ref_id:
            canonical_ids_by_entity.setdefault(entity_key, []).append(ref_id)
    enriched: List[Dict[str, Any]] = []
    for item in items:
        linked_entities = {
            ("persona", str(item.get("persona_id") or "").strip()),
            *(('runtime', str(value).strip()) for value in item.get("runtime_ids") or []),
            *(('runtime_binding', str(value).strip()) for value in item.get("runtime_ids") or []),
            *(('persona_binding', str(value).strip()) for value in item.get("binding_ids") or []),
            *(('persona_capital_binding', str(value).strip()) for value in item.get("binding_ids") or []),
            *(('strategy', str(value).strip()) for value in item.get("strategy_ids") or []),
            *(('strategy_spec', str(value).strip()) for value in item.get("strategy_ids") or []),
            *(('artifact', str(value).strip()) for value in item.get("artifact_ids") or []),
            *(('capital_pool', str(value).strip()) for value in item.get("capital_pool_ids") or []),
            *(('capital_sleeve', str(value).strip()) for value in item.get("sleeve_ids") or []),
            ("paper_ledger", str(item.get("paper_ledger_id") or "").strip()),
        }
        linked_entities = {
            (entity_type, entity_ref)
            for entity_type, entity_ref in linked_entities
            if entity_ref
        }
        canonical_ref_ids = sorted({
            ref_id
            for entity_key in linked_entities
            for ref_id in canonical_ids_by_entity.get(entity_key, [])
        })
        telemetry_refs = list(item.get("evidence_refs") or [])
        snapshot_evidence_ref_ids = sorted({
            *(_pm12_evidence_ref_key(ref) for ref in telemetry_refs),
            *canonical_ref_ids,
        } - {""})
        visible_scoped_refs = [
            public_by_id[ref_id]
            for ref_id in canonical_ref_ids
            if ref_id in public_by_id
        ]
        visible_refs = _pm12_merge_evidence_refs(
            telemetry_refs,
            visible_scoped_refs,
        )
        enriched.append({
            **item,
            "evidence_refs": visible_refs,
            "evidence_ref_ids": sorted({
                _pm12_evidence_ref_key(ref)
                for ref in visible_refs
                if _pm12_evidence_ref_key(ref)
            }),
            "_snapshot_evidence_ref_ids": snapshot_evidence_ref_ids,
        })
    return enriched


# --- _enrich_persona_item_with_bindings ---
def _enrich_persona_item_with_bindings(
    item: Dict[str, Any],
    *,
    bindings: Optional[List[Dict[str, Any]]] = None,
    runtimes: Optional[List[Dict[str, Any]]] = None,
    persona: Optional[Dict[str, Any]] = None,
    league_entry: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    persona_id = str(item.get("persona_id") or item.get("personaId") or item.get("id") or "")
    if not persona_id:
        return item

    enriched = dict(item)
    if bindings is None:
        try:
            bindings = read_store.list_bindings(persona_id=persona_id) or []
        except Exception:
            bindings = []
    if runtimes is None:
        try:
            runtimes = read_store.list_runtime_bindings() or []
        except Exception:
            runtimes = []
    if persona is None:
        try:
            persona = read_store.get_persona(persona_id) or {}
        except Exception:
            persona = {}
    if league_entry is None:
        try:
            league_entry = read_store.get_persona_league_entry(persona_id) or {}
        except Exception:
            league_entry = {}

    raw_metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
    binding_context_item = {
        **item,
        # Session observations may locate candidate records, but they cannot
        # choose an authoritative RuntimeBinding.
        "runtime_ids": [],
        "binding_id": (
            league_entry.get("binding_id")
            or league_entry.get("persona_capital_binding_id")
            or raw_metadata.get("binding_id")
            or raw_metadata.get("persona_capital_binding_id")
            or item.get("binding_id")
        ),
        "runtime_binding_id": (
            league_entry.get("runtime_binding_id")
            or raw_metadata.get("runtime_binding_id")
            or item.get("runtime_binding_id")
        ),
        "capital_sleeve_id": (
            league_entry.get("capital_sleeve_id")
            or raw_metadata.get("capital_sleeve_id")
            or item.get("capital_sleeve_id")
        ),
        "deployment_stage": (
            league_entry.get("deployment_stage")
            or raw_metadata.get("deployment_stage")
            or raw_metadata.get("capital_mode")
            or item.get("deployment_stage")
        ),
    }
    binding, runtime, binding_resolution = _pm12_binding_runtime_context(
        persona_id=persona_id,
        item=binding_context_item,
        bindings=bindings,
        runtimes=runtimes,
    )
    runtime_mode = str(runtime.get("deployment_mode") or "").strip().lower()
    runtime_mode = {
        "paper_running": "paper",
        "canary_running": "canary",
        "live_running": "live",
    }.get(runtime_mode, runtime_mode)
    if runtime and runtime_mode in _PERSONA_FLEET_RUNNING_STAGE_STATES:
        runtime_resolution = "active"
    elif runtime:
        runtime_resolution = "invalid_deployment_mode"
    elif "runtime_ambiguous" in binding_resolution:
        runtime_resolution = "ambiguous"
    elif any(_pm12_record_freshness_issue(record) for record in runtimes):
        runtime_resolution = "stale"
    elif any(record.get("retired_at") not in (None, "") for record in runtimes):
        runtime_resolution = "retired"
    elif "runtime_inactive" in binding_resolution or (
        runtimes
        and not any(
            _pm12_record_lifecycle_is_active(
                record,
                fields=("status", "state"),
                active_values={"active", "running", "idle"},
            )
            for record in runtimes
        )
    ):
        runtime_resolution = "inactive"
    elif "mismatch" in binding_resolution:
        runtime_resolution = "identity_mismatch"
    else:
        runtime_resolution = "missing"
    runtime_session, session_resolution = _pm12_runtime_session_resolution(
        persona_id,
        runtime,
    )
    matching_runtimes = [runtime] if runtime else []

    strategy_ids = []
    binding_ids = []
    pool_ids = []
    runtime_ids: List[str] = []
    sleeve_ids = []
    artifact_ids = []
    broker_ids = []

    for b in ([binding] if binding else []):
        binding_id = str(
            b.get("binding_id")
            or b.get("persona_capital_binding_id")
            or b.get("id")
            or ""
        ).strip()
        if binding_id:
            binding_ids.append(binding_id)
        binding_strategy_id = _persona_fleet_record_value(b, "strategy_id")
        if binding_strategy_id:
            strategy_ids.append(str(binding_strategy_id))
        binding_pool_id = _persona_fleet_record_value(b, "capital_pool_id", "pool_id")
        if binding_pool_id:
            pool_ids.append(str(binding_pool_id))
        binding_sleeve_id = _persona_fleet_record_value(
            b,
            "capital_sleeve_id",
            "sleeve_id",
        )
        if binding_sleeve_id:
            sleeve_ids.append(str(binding_sleeve_id))
        binding_broker_id = _persona_fleet_record_value(b, "broker_id")
        if binding_broker_id:
            broker_ids.append(str(binding_broker_id))

    for r in matching_runtimes:
        if r.get("runtime_id"):
            runtime_ids.append(str(r["runtime_id"]))
        if r.get("strategy_id"):
            strategy_ids.append(str(r["strategy_id"]))
        elif r.get("params", {}).get("strategy_id"):
            strategy_ids.append(str(r["params"]["strategy_id"]))
        runtime_pool_id = _persona_fleet_record_value(r, "capital_pool_id", "pool_id")
        if runtime_pool_id:
            pool_ids.append(str(runtime_pool_id))
        runtime_sleeve_id = _persona_fleet_record_value(r, "capital_sleeve_id", "sleeve_id")
        if runtime_sleeve_id:
            sleeve_ids.append(str(runtime_sleeve_id))
        runtime_artifact_id = _persona_fleet_record_value(r, "artifact_id")
        if runtime_artifact_id:
            artifact_ids.append(str(runtime_artifact_id))
        runtime_broker_id = _persona_fleet_record_value(r, "broker_id")
        if runtime_broker_id:
            broker_ids.append(str(runtime_broker_id))

    deployment_stage = runtime.get("deployment_mode") or "none"
    capital_mode = {
        "paper_running": "paper",
        "canary_running": "canary",
        "live_running": "live",
    }.get(str(deployment_stage or "").strip().lower(), str(deployment_stage or "").strip().lower())
    if capital_mode not in _PERSONA_FLEET_RUNNING_STAGE_STATES:
        capital_mode = "none"
        deployment_stage = "none"
    else:
        deployment_stage = capital_mode
    source_pool_id = (
        _persona_fleet_record_value(binding, "capital_pool_id", "pool_id")
        or _persona_fleet_record_value(runtime, "capital_pool_id", "pool_id")
    )
    if capital_mode == "paper" and not source_pool_id:
        source_pool_id = raw_metadata.get("legacy_paper_capital_pool_id")
    live_pool_id = _persona_fleet_live_capital_pool_id(
        capital_mode=capital_mode,
        pool_id=source_pool_id,
        league_entry={},
        raw_metadata={},
        context_metadata={},
        binding=binding,
    )
    paper_ledger_id = _persona_fleet_paper_ledger_id(
        persona_id=persona_id,
        capital_mode=capital_mode,
        league_entry=league_entry,
        raw_metadata=raw_metadata,
        context_metadata={},
        binding=binding,
        runtime=runtime,
    )
    capital_projection = _persona_fleet_capital_binding_projection(
        persona_id=persona_id,
        capital_mode=capital_mode,
        deployment_stage=deployment_stage,
        paper_ledger_id=paper_ledger_id,
        live_pool_id=live_pool_id,
        binding=binding,
        runtime=runtime,
        league_entry={},
        raw_metadata={},
        context_metadata={},
    )
    lifecycle_state = (
        persona.get("lifecycle_state")
        or persona.get("status")
        or item.get("state")
        or item.get("stage")
        or "unknown"
    )
    normalized_lifecycle = _normalize_lifecycle_state(lifecycle_state)
    if (
        runtime
        and capital_mode in _PERSONA_FLEET_RUNNING_STAGE_STATES
        and _is_persona_lifecycle_operational(lifecycle_state)
    ):
        stage = f"{capital_mode}_running"
    elif normalized_lifecycle in {"frozen", "suspended", "retired"}:
        stage = normalized_lifecycle
    else:
        stage = "not_running"
    stage_capital_mode = {
        "paper_running": "paper",
        "canary_running": "canary",
        "live_running": "live",
    }.get(stage)
    stage_binding_mismatch = False
    binding_for_weight = binding
    runtime_for_weight = runtime
    if stage_binding_mismatch:
        binding_resolution = f"{binding_resolution}_stage_mismatch"
    binding_identity_failed = any(
        token in binding_resolution
        for token in ("ambiguous", "mismatch", "inactive")
    )
    identity_resolution_failed = (
        not runtime
        or runtime_resolution != "active"
        or binding_identity_failed
        or (capital_mode in {"canary", "live"} and not binding)
    )
    if identity_resolution_failed:
        binding_for_weight = {}
        runtime_for_weight = {}
        binding_ids = []
        pool_ids = []
        sleeve_ids = []
    if stage_capital_mode == "paper":
        capital_mode = "paper"
        live_pool_id = None
        paper_ledger_id = _persona_fleet_paper_ledger_id(
            persona_id=persona_id,
            capital_mode="paper",
            league_entry=league_entry,
            raw_metadata=raw_metadata,
            context_metadata={},
            binding=binding if not stage_binding_mismatch else {},
            runtime=runtime if not stage_binding_mismatch else {},
        )
        capital_projection = _persona_fleet_capital_binding_projection(
            persona_id=persona_id,
            capital_mode="paper",
            deployment_stage="paper",
            paper_ledger_id=paper_ledger_id,
            live_pool_id=None,
            binding={},
            runtime={},
            league_entry={},
            raw_metadata={},
            context_metadata={},
        )
        pool_ids = []
        sleeve_ids = []
    elif identity_resolution_failed and stage_capital_mode:
        capital_mode = stage_capital_mode
        live_pool_id = None
        capital_projection = _persona_fleet_capital_binding_projection(
            persona_id=persona_id,
            capital_mode=stage_capital_mode,
            deployment_stage=stage_capital_mode,
            paper_ledger_id=None,
            live_pool_id=None,
            binding={},
            runtime={},
            league_entry={},
            raw_metadata={},
            context_metadata={},
        )
    elif identity_resolution_failed:
        capital_mode = "none"
        live_pool_id = None
        paper_ledger_id = None
        capital_projection = _persona_fleet_capital_binding_projection(
            persona_id=persona_id,
            capital_mode="none",
            deployment_stage="none",
            paper_ledger_id=None,
            live_pool_id=None,
            binding={},
            runtime={},
            league_entry={},
            raw_metadata={},
            context_metadata={},
        )
    capital_projection["stage"] = stage
    capital_projection["capital_binding"]["stage"] = stage
    authoritative_current_weight = None
    authoritative_target_weight = None
    current_weight_source = "unavailable"
    for source_name, record in (
        ("persona_binding", binding_for_weight),
        ("runtime_binding", runtime_for_weight),
    ):
        value = _persona_fleet_record_value(
            record,
            "current_weight",
            "currentWeight",
            "allocation_weight",
            "weight",
        )
        if value not in (None, "") and not isinstance(value, bool):
            try:
                parsed_weight = float(value)
                if math.isfinite(parsed_weight) and 0.0 <= parsed_weight <= 1.0:
                    authoritative_current_weight = parsed_weight
                    current_weight_source = source_name
                else:
                    current_weight_source = f"{source_name}_invalid"
                break
            except (TypeError, ValueError):
                current_weight_source = f"{source_name}_invalid"
                break
    for record in (binding_for_weight, runtime_for_weight):
        value = _persona_fleet_record_value(
            record,
            "target_weight",
            "targetWeight",
            "proposed_weight",
        )
        if value not in (None, "") and not isinstance(value, bool):
            try:
                parsed_weight = float(value)
                if math.isfinite(parsed_weight) and 0.0 <= parsed_weight <= 1.0:
                    authoritative_target_weight = parsed_weight
                break
            except (TypeError, ValueError):
                break
    capital_projection["current_weight"] = authoritative_current_weight
    capital_projection["target_weight"] = authoritative_target_weight
    capital_projection["capital_binding"]["current_weight"] = authoritative_current_weight
    capital_projection["capital_binding"]["target_weight"] = authoritative_target_weight
    if stage == "paper_running":
        capital_projection["current_weight"] = None
        capital_projection["target_weight"] = None
        capital_projection["capital_binding"]["current_weight"] = None
        capital_projection["capital_binding"]["target_weight"] = None
        current_weight_source = "not_applicable_paper_ledger"

    projected_sleeve_id = capital_projection.get("capital_sleeve_id")
    if projected_sleeve_id:
        sleeve_ids.append(str(projected_sleeve_id))
    if live_pool_id:
        pool_ids.append(str(live_pool_id))
    elif capital_mode == "paper":
        pool_ids = []

    enriched["strategy_ids"] = sorted(set(strategy_ids))
    enriched["binding_ids"] = sorted(set(binding_ids))
    enriched["capital_pool_ids"] = sorted(set(pool_ids))
    enriched["runtime_ids"] = sorted(set(runtime_ids))
    enriched["sleeve_ids"] = sorted(set(sleeve_ids))
    enriched["artifact_ids"] = sorted(set(artifact_ids))
    enriched["broker_ids"] = sorted(set(broker_ids))

    if strategy_ids:
        enriched["strategy_id"] = strategy_ids[0]
    enriched["capital_pool_id"] = live_pool_id
    enriched["pool_id"] = live_pool_id
    if runtime_ids:
        enriched["runtime_id"] = runtime_ids[0]
    enriched["sleeve_id"] = projected_sleeve_id
    if artifact_ids:
        enriched["artifact_id"] = artifact_ids[0]
    if broker_ids:
        enriched["broker_id"] = broker_ids[0]

    enriched.update({
        "stage": stage,
        "deployment_stage": str(deployment_stage or "none").strip().lower() or "none",
        "capital_mode": capital_mode,
        "capital_scope": capital_projection.get("capital_scope"),
        "capital_scope_id": capital_projection.get("capital_scope_id"),
        "capital_sleeve_id": projected_sleeve_id,
        "paper_ledger_id": paper_ledger_id,
        "current_weight": capital_projection.get("current_weight"),
        "target_weight": capital_projection.get("target_weight"),
        "binding_state": capital_projection.get("binding_state"),
        "binding_resolution": binding_resolution,
        "runtime_resolution": runtime_resolution,
        "session_resolution": session_resolution,
        "session_id": (
            runtime_session.get("session_id") or runtime_session.get("id")
            if runtime_session
            else None
        ),
        "session_authority": (
            runtime_session.get("session_authority")
            if runtime_session
            else None
        ),
        "capital_binding": capital_projection.get("capital_binding"),
        "current_weight_source": current_weight_source,
    })

    return enriched


# --- _pm12_quarterly_recommendations ---
def _pm12_quarterly_recommendations(
    ranked_items: List[Dict[str, Any]],
    *,
    quarter_window: Dict[str, Any],
    evidence_refs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    recommendations: List[Dict[str, Any]] = []
    for item in ranked_items:
        for action_id in _pm12_recommendation_action_ids(item):
            recommendations.append(
                _pm12_quarterly_recommendation_item(
                    item,
                    action_id=action_id,
                    quarter_window=quarter_window,
                    evidence_refs=evidence_refs,
                )
            )
    recommendations.sort(
        key=lambda entry: (
            _HUMAN_INBOX_PRIORITY_RANK.get(str(entry.get("priority") or "unknown"), 0),
            -int(entry.get("rank") or 0),
            str(entry.get("action_id") or ""),
            str(entry.get("persona_id") or ""),
        ),
        reverse=True,
    )
    return recommendations


# --- _promotion_review_scoped_idempotency_key ---
def _promotion_review_scoped_idempotency_key(
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str],
    review_revision_id: str,
) -> str:
    client_key = _resolve_final_idempotency_key(
        idempotency_key,
        x_idempotency_key,
    )
    revision_digest = hashlib.sha256(
        _promotion_review_clean_id(review_revision_id).encode("utf-8")
    ).hexdigest()[:32]
    return f"{client_key}:promotion-review:{revision_digest}"


# --- _promotion_review_item_from_recommendation ---
def _promotion_review_item_from_recommendation(
    recommendation: Dict[str, Any],
) -> Dict[str, Any]:
    recommendation_id = str(
        recommendation.get("recommendation_id")
        or recommendation.get("id")
        or ""
    )
    review_id = _promotion_review_revision_id(
        recommendation_id,
        recommendation.get("ranking_snapshot_id"),
    )
    private_submission = _promotion_review_submission_projection(
        review_id,
        include_source_recommendation=True,
    )
    stored_source = (
        private_submission.get("source_recommendation")
        if isinstance(private_submission, dict)
        else None
    )
    if isinstance(stored_source, dict):
        recommendation = json.loads(json.dumps(stored_source))
        recommendation["evidence_refs"] = []
        recommendation["evidence_ref_ids"] = []
    submission = (
        {
            key: value
            for key, value in private_submission.items()
            if key != "source_recommendation"
        }
        if isinstance(private_submission, dict)
        else None
    )
    action_id = str(recommendation.get("action_id") or "")
    decision = _promotion_review_decision_projection(review_id)
    stage_path = _promotion_review_stage_path(recommendation)
    target_stage = str(stage_path.get("target_stage") or "governance_review")
    status = "decision_accepted" if decision else "pending_human_gate" if submission else "recommended_not_submitted"
    decision_status = str((decision or {}).get("decision_status") or "pending")
    governance = {
        "requires_human_gate_decision": True,
        "decision_status": decision_status,
        "live_capital_mutation": False,
        "direct_live_capital_mutation": False,
        "paper_to_canary_gate": stage_path.get("review_kind") == "paper_to_canary_review",
        "canary_to_live_gate": stage_path.get("review_kind") == "canary_to_live_review",
        "live_ranking_review": stage_path.get("review_kind") == "live_ranking_review",
        "live_promotion_requires_separate_human_gate": bool(stage_path.get("live_requires_separate_human_gate", True)),
        "policy": "promotion_governance_human_gate_no_direct_live_capital",
    }
    item: Dict[str, Any] = {
        "id": review_id,
        "review_id": review_id,
        "promotion_review_id": review_id,
        "recommendation_id": recommendation_id,
        "ranking_snapshot_id": recommendation.get("ranking_snapshot_id"),
        "quarter": recommendation.get("quarter"),
        "quarter_window": recommendation.get("quarter_window"),
        "persona_id": recommendation.get("persona_id"),
        "name": recommendation.get("name"),
        "owner": recommendation.get("owner"),
        "rank": recommendation.get("rank"),
        "score": recommendation.get("score"),
        "tier": recommendation.get("tier"),
        "stage": recommendation.get("stage"),
        "capital_scope": recommendation.get("capital_scope"),
        "capital_scope_id": recommendation.get("capital_scope_id"),
        "capital_pool_id": recommendation.get("capital_pool_id"),
        "capital_sleeve_id": recommendation.get("capital_sleeve_id"),
        "paper_ledger_id": recommendation.get("paper_ledger_id"),
        "current_weight": recommendation.get("current_weight"),
        "current_weight_source": recommendation.get("current_weight_source"),
        "eligible": recommendation.get("eligible"),
        "exclusion_reason": recommendation.get("exclusion_reason"),
        "exclusion_reasons": list(recommendation.get("exclusion_reasons") or []),
        "exclusion_codes": list(recommendation.get("exclusion_codes") or []),
        "action_id": action_id,
        "action_label": recommendation.get("action_label"),
        "priority": recommendation.get("priority"),
        "risk_level": recommendation.get("risk_level"),
        "status": status,
        "decision_status": decision_status,
        "submitted": bool(submission),
        "submit_status": (submission or {}).get("submit_status") if submission else "not_submitted",
        "human_inbox_id": f"{_PROMOTION_REVIEW_TARGET_PREFIX}{review_id}",
        "allowed_decisions": sorted(_PROMOTION_REVIEW_DECISIONS),
        "allowedActions": {
            "canSubmit": not bool(submission),
            "canApprove": bool(submission),
            "canApproveWithConditions": bool(submission),
            "canReject": bool(submission),
        },
        "promotion_path": stage_path,
        "review_kind": stage_path.get("review_kind"),
        "rationale": recommendation.get("rationale"),
        "rationale_codes": list(recommendation.get("rationale_codes") or []),
        "metrics": json.loads(json.dumps(recommendation.get("metrics") or {})),
        "components": json.loads(json.dumps(recommendation.get("components") or {})),
        "evidence_refs": json.loads(json.dumps(recommendation.get("evidence_refs") or [])),
        "evidence_ref_ids": list(recommendation.get("evidence_ref_ids") or []),
        "source_recommendation": json.loads(json.dumps(recommendation)),
        "governance": governance,
        "requires_human_gate_decision": True,
        "live_capital_mutation": False,
        "direct_live_capital_mutation": False,
        "policy": "promotion_governance_human_gate_no_direct_live_capital",
        "links": {
            "persona": f"/bff/personas/{recommendation.get('persona_id')}",
            "recommendation": "/bff/management/quarterly-ranking/recommendations",
            "submit": f"/bff/management/quarterly-ranking/recommendations/{quote(recommendation_id, safe='')}/submit",
            "detail": f"/bff/management/promotion-reviews/{quote(review_id, safe='')}",
            "decisions": f"/bff/management/promotion-reviews/{quote(review_id, safe='')}/decisions",
            "human_inbox": f"/bff/management/human-inbox/{quote(_promotion_review_target_id(review_id), safe='')}",
        },
    }
    if submission:
        item["submission"] = submission
    if decision:
        item["decision"] = decision
    return item


# --- _promotion_review_items ---
def _promotion_review_items(
    identity: OperatorIdentity,
    *,
    snapshot_at: str,
    quarter: Optional[str],
    state: Optional[str] = None,
    archetype: Optional[str] = None,
    q: str = "",
) -> tuple[List[Dict[str, Any]], Dict[str, Any], int, bool]:
    quarter_window = _pm12_quarter_window(quarter, snapshot_at)
    caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
    rows = _pm12_persona_league_rows(tenant_id=caller_tenant_id)
    ranked_items = _pm12_quarterly_ranking_items(rows, quarter_window=quarter_window)
    (
        public_evidence_refs,
        canonical_evidence_refs,
        redacted_count,
        evidence_dataset_available,
    ) = _pm12_public_quarter_evidence_refs(
        identity,
        quarter_window,
    )
    ranked_items = _pm12_attach_ranking_evidence(
        ranked_items,
        public_evidence_refs,
        canonical_evidence_refs=canonical_evidence_refs,
    )
    ranked_items, ranking_snapshot_id = _pm12_attach_ranking_snapshot(
        ranked_items,
        surface="quarterly",
        period=quarter_window["quarter"],
    )
    ranked_items = _pm12_filter_persona_items(
        ranked_items,
        state=state,
        archetype=archetype,
        q=q,
    )
    recommendations = _pm12_quarterly_recommendations(
        ranked_items,
        quarter_window=quarter_window,
        evidence_refs=public_evidence_refs,
    )
    reviews = [
        _promotion_review_item_from_recommendation(item)
        for item in recommendations
        if str(item.get("action_id") or "") in _PROMOTION_REVIEW_ACTION_IDS
    ]
    return reviews, quarter_window, redacted_count, evidence_dataset_available


# --- _promotion_review_surfaces ---
def _promotion_review_surfaces(
    *,
    snapshot_at: str,
    evidence_dataset_available: bool,
) -> Dict[str, Dict[str, Any]]:
    source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
    formula_surface = _composed_surface_status(snapshot_at=snapshot_at, available=True)
    evidence_surface = _dataset_surface_status(
        "evidence_refs",
        snapshot_at=snapshot_at,
        has_data=evidence_dataset_available,
        missing_message="Evidence reference read surface is unavailable.",
    )
    approval_queue_surface = _dataset_surface_status("approval_queue_items", snapshot_at=snapshot_at)
    human_gate_surface = _dataset_surface_status("approval_decisions", snapshot_at=snapshot_at)
    recommendations_surface = _aggregate_group_surface(
        "quarterly_ranking_recommendations",
        [*source_surfaces.values(), formula_surface, evidence_surface, approval_queue_surface, human_gate_surface],
        snapshot_at=snapshot_at,
        unavailable_message="Quarterly ranking recommendations aggregate unavailable.",
        degraded_message="Quarterly ranking recommendations are degraded because one or more source surfaces are degraded.",
    )
    promotion_reviews_surface = _aggregate_group_surface(
        "promotion_reviews",
        [recommendations_surface, approval_queue_surface, human_gate_surface],
        snapshot_at=snapshot_at,
        unavailable_message="Promotion review aggregate unavailable.",
        degraded_message="Promotion review aggregate is degraded because one or more governance surfaces are degraded.",
    )
    return {
        "promotion_reviews": promotion_reviews_surface,
        "quarterly_ranking_recommendations": recommendations_surface,
        "formula": formula_surface,
        "evidence_refs": evidence_surface,
        "knowledge_evidence": evidence_surface,
        "human_inbox": _composed_surface_status(snapshot_at=snapshot_at),
        "governance_queue": approval_queue_surface,
        "human_gate_decision": human_gate_surface,
        **source_surfaces,
    }


# --- _promotion_review_find ---
def _promotion_review_find(
    identity: OperatorIdentity,
    review_id: str,
    *,
    snapshot_at: str,
    quarter: Optional[str] = None,
    include_historical: bool = True,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any], int, bool]:
    clean_id = _promotion_review_clean_id(review_id)
    resolved_quarter = quarter or _promotion_review_quarter_from_id(clean_id)
    reviews, quarter_window, redacted_count, evidence_dataset_available = _promotion_review_items(
        identity,
        snapshot_at=snapshot_at,
        quarter=resolved_quarter,
    )
    for item in reviews:
        identifiers = {
            str(item.get("id") or ""),
            str(item.get("review_id") or ""),
            str(item.get("promotion_review_id") or ""),
            str(item.get("recommendation_id") or ""),
        }
        if clean_id in identifiers:
            return item, quarter_window, redacted_count, evidence_dataset_available
    if include_historical:
        for item in _submitted_promotion_review_records(
            identity,
            snapshot_at=snapshot_at,
        ):
            identifiers = {
                str(item.get("id") or ""),
                str(item.get("review_id") or ""),
                str(item.get("promotion_review_id") or ""),
            }
            if clean_id in identifiers:
                return (
                    item,
                    quarter_window,
                    redacted_count,
                    evidence_dataset_available,
                )
    return None, quarter_window, redacted_count, evidence_dataset_available


# --- _promotion_review_rationale ---
def _promotion_review_rationale(payload: Dict[str, Any]) -> str:
    return str(
        payload.get("rationale")
        or payload.get("reason")
        or payload.get("memo")
        or payload.get("rejection_reason")
        or ""
    ).strip()


# --- _promotion_review_decision_payload ---
def _promotion_review_decision_payload(
    *,
    payload: Dict[str, Any],
    review: Dict[str, Any],
    decision: str,
    rationale: str,
    identity: OperatorIdentity,
) -> Dict[str, Any]:
    command_payload = {
        **payload,
        "decision": decision,
        "review_id": review["review_id"],
        "promotion_review_id": review["promotion_review_id"],
        "recommendation_id": review["recommendation_id"],
        "ranking_snapshot_id": review.get("ranking_snapshot_id"),
        "persona_id": review.get("persona_id"),
        "action_id": review.get("action_id"),
        "promotion_stage_from": "paper",
        "promotion_stage_to": (review.get("promotion_path") or {}).get("target_stage"),
        "eventual_live_stage": "live",
        "live_promotion_requires_separate_human_gate": True,
        "requires_human_gate_decision": True,
        "live_capital_mutation": False,
        "liveCapitalMutation": False,
        "liveCapitalSideEffects": False,
        "direct_live_capital_mutation": False,
        "runtime_mutation": False,
        "audit_event": f"promotion_review.{decision}",
        "actor_id": identity.operator_id,
        "policy": "promotion_governance_human_gate_no_direct_live_capital",
    }
    if rationale:
        command_payload["rationale"] = rationale
    if decision == "reject":
        command_payload["rejection_reason"] = rationale
    if "conditions" in payload:
        command_payload["conditions"] = json.loads(json.dumps(payload.get("conditions")))
    return command_payload


# --- _promotion_review_decision_response ---
def _promotion_review_decision_response(
    command_response: JSONResponse,
    *,
    review: Dict[str, Any],
    decision: str,
    command_payload: Dict[str, Any],
    client_idempotency_key: Optional[str] = None,
) -> JSONResponse:
    content = json.loads(command_response.body.decode("utf-8") if command_response.body else "{}")
    data = content.setdefault("data", {})
    data.update(
        {
            "review_id": review["review_id"],
            "promotion_review_id": review["promotion_review_id"],
            "recommendation_id": review["recommendation_id"],
            "persona_id": review.get("persona_id"),
            "action_id": review.get("action_id"),
            "ranking_snapshot_id": review.get("ranking_snapshot_id"),
            "decision": decision,
            "decision_status": "accepted",
            "requires_human_gate_decision": True,
            "live_capital_mutation": False,
            "liveCapitalMutation": False,
            "liveCapitalSideEffects": False,
            "direct_live_capital_mutation": False,
            "runtime_mutation": False,
            "promotion_stage_from": "paper",
            "promotion_stage_to": command_payload.get("promotion_stage_to"),
            "eventual_live_stage": "live",
        }
    )
    if command_payload.get("rationale"):
        data["rationale"] = command_payload.get("rationale")
    if "conditions" in command_payload:
        data["conditions"] = json.loads(json.dumps(command_payload.get("conditions")))
    meta = content.setdefault("meta", {})
    if client_idempotency_key:
        meta["idempotency"] = {
            **(
                meta.get("idempotency")
                if isinstance(meta.get("idempotency"), dict)
                else {}
            ),
            "key": client_idempotency_key,
            "idempotencyKey": client_idempotency_key,
        }
    meta.update(
        {
            "live_capital_mutation": False,
            "liveCapitalMutation": False,
            "liveCapitalSideEffects": False,
            "direct_live_capital_mutation": False,
            "runtime_mutation": False,
            "requires_human_gate_decision": True,
            "decision_status": "accepted",
            "decision": decision,
            "governance_policy": "promotion_governance_human_gate_no_direct_live_capital",
        }
    )
    return JSONResponse(status_code=command_response.status_code, content=jsonable_encoder(content))


# --- _promotion_review_submit_response ---
def _promotion_review_submit_response(
    command_response: JSONResponse,
    *,
    review: Dict[str, Any],
    client_idempotency_key: Optional[str] = None,
) -> JSONResponse:
    content = json.loads(command_response.body.decode("utf-8") if command_response.body else "{}")
    refreshed = _promotion_review_item_from_recommendation(review["source_recommendation"])
    data = content.setdefault("data", {})
    data.update(
        {
            "review_id": refreshed["review_id"],
            "promotion_review_id": refreshed["promotion_review_id"],
            "recommendation_id": refreshed["recommendation_id"],
            "persona_id": refreshed.get("persona_id"),
            "action_id": refreshed.get("action_id"),
            "ranking_snapshot_id": refreshed.get("ranking_snapshot_id"),
            "status": refreshed.get("status"),
            "submitted": True,
            "human_inbox_id": refreshed.get("human_inbox_id"),
            "requires_human_gate_decision": True,
            "live_capital_mutation": False,
            "liveCapitalMutation": False,
            "direct_live_capital_mutation": False,
            "runtime_mutation": False,
            "review": refreshed,
            "links": refreshed.get("links") or {},
        }
    )
    meta = content.setdefault("meta", {})
    if client_idempotency_key:
        meta["idempotency"] = {
            **(
                meta.get("idempotency")
                if isinstance(meta.get("idempotency"), dict)
                else {}
            ),
            "key": client_idempotency_key,
            "idempotencyKey": client_idempotency_key,
        }
    meta.update(
        {
            "ranking_snapshot_id": refreshed.get("ranking_snapshot_id"),
            "live_capital_mutation": False,
            "liveCapitalMutation": False,
            "direct_live_capital_mutation": False,
            "runtime_mutation": False,
            "requires_human_gate_decision": True,
            "governance_policy": "promotion_governance_human_gate_no_direct_live_capital",
        }
    )
    return JSONResponse(status_code=command_response.status_code, content=jsonable_encoder(content))


# --- _project_persona_league_row ---
def _project_persona_league_row(
    raw: Dict[str, Any],
    *,
    runtime_bindings: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    persona_id = str(raw.get("persona_id") or raw.get("id") or "")
    metadata = dict(raw.get("metadata") or {}) if isinstance(raw.get("metadata"), dict) else {}
    routed = _routed_strategies_for_persona(persona_id)
    route_policy = _pm12_persona_route_summary(persona_id)
    capabilities = _pm12_persona_capability_summary(persona_id)
    bindings = _pm12_persona_binding_summary(
        persona_id,
        runtime_bindings=runtime_bindings,
    )
    sessions = _pm12_persona_session_summary(persona_id)
    evaluations = _pm12_persona_evaluation_summary(persona_id)
    memory = _pm12_persona_memory_summary(persona_id)
    health = _pm12_persona_health_summary(raw, sessions, capabilities)
    allowed_actions = read_store.get_persona_allowed_actions(persona_id) or {}
    enabled_actions = sorted(
        str(action_id)
        for action_id, enabled in allowed_actions.items()
        if enabled
    )
    archetype = str(
        metadata.get("archetype")
        or raw.get("strategy_family")
        or raw.get("mandate")
        or "generalist"
    )
    return {
        "id": persona_id,
        "persona_id": persona_id,
        "name": raw.get("name") or persona_id,
        "owner": metadata.get("owner") or raw.get("owner") or "pantheon-bff",
        "updated_at": raw.get("updated_at") or raw.get("created_at") or utc_now(),
        "state": _normalize_lifecycle_state(raw.get("lifecycle_state") or raw.get("state")),
        "risk": _normalize_risk_level(metadata.get("risk_level") or raw.get("risk")),
        "archetype": archetype,
        "routed_strategy_count": int(routed or 0),
        "success_rate": float(metadata.get("success_rate") or 0.0),
        "mandate": raw.get("mandate") or "",
        "strategy_family": raw.get("strategy_family") or "",
        "route_policy_summary": route_policy,
        "capability_summary": capabilities,
        "binding_summary": bindings,
        "session_summary": sessions,
        "evaluation_summary": evaluations,
        "memory_summary": memory,
        "health_summary": health,
        "allowed_action_summary": {
            "count": len(allowed_actions),
            "enabled_count": len(enabled_actions),
            "enabled_actions": enabled_actions,
        },
        "links": {
            "detail": f"/bff/personas/{persona_id}",
            "route_policy": f"/bff/personas/{persona_id}/route-policy",
            "capabilities": f"/bff/personas/{persona_id}/capabilities",
            "evaluations": f"/bff/personas/{persona_id}/evaluations",
            "memory": f"/bff/personas/{persona_id}/memory",
            "activity": f"/bff/personas/{persona_id}/activity",
        },
    }


# --- _pm12_filter_persona_items ---
def _pm12_filter_persona_items(
    items: List[Dict[str, Any]],
    *,
    state: Optional[str] = None,
    archetype: Optional[str] = None,
    q: str = "",
) -> List[Dict[str, Any]]:
    filtered = items
    if state:
        normalized_state = _normalize_lifecycle_state(state)
        filtered = [item for item in filtered if item.get("state") == normalized_state]
    if archetype:
        filtered = [
            item
            for item in filtered
            if str(item.get("archetype") or "") == archetype
        ]
    needle = q.strip().lower()
    if needle:
        filtered = [
            item for item in filtered
            if needle in str(item.get("id") or "").lower()
            or needle in str(item.get("name") or "").lower()
            or needle in str(item.get("owner") or "").lower()
            or needle in str(item.get("archetype") or "").lower()
        ]
    return list(filtered)


# --- _pm12_persona_league_rows ---
def _pm12_persona_league_rows(
    *,
    state: Optional[str] = None,
    archetype: Optional[str] = None,
    q: str = "",
    tenant_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    raw_records = _list_persona_records(tenant_id)
    try:
        all_bindings = [
            record
            for record in (read_store.list_bindings() or [])
            if isinstance(record, dict)
        ]
    except Exception:
        all_bindings = []
    try:
        all_runtimes = [
            record
            for record in (read_store.list_runtime_bindings() or [])
            if isinstance(record, dict)
        ]
    except Exception:
        all_runtimes = []
    try:
        all_league_entries = [
            record
            for record in (read_store.list_persona_league() or [])
            if isinstance(record, dict)
        ]
    except Exception:
        all_league_entries = []

    bindings_by_persona: Dict[str, List[Dict[str, Any]]] = {}
    for binding in all_bindings:
        binding_persona_id = str(binding.get("persona_id") or "").strip()
        if binding_persona_id:
            bindings_by_persona.setdefault(binding_persona_id, []).append(binding)
    league_by_persona = {
        str(record.get("persona_id") or record.get("id") or "").strip(): record
        for record in all_league_entries
        if str(record.get("persona_id") or record.get("id") or "").strip()
    }
    runtimes_by_persona: Dict[str, List[Dict[str, Any]]] = {}
    runtimes_by_binding: Dict[str, List[Dict[str, Any]]] = {}
    runtimes_by_identity: Dict[str, List[Dict[str, Any]]] = {}
    for runtime in all_runtimes:
        runtime_persona_id = str(runtime.get("persona_id") or "").strip()
        if runtime_persona_id:
            runtimes_by_persona.setdefault(runtime_persona_id, []).append(runtime)
        runtime_binding_id = str(
            _persona_fleet_record_value(
                runtime,
                "persona_capital_binding_id",
                "binding_id",
            )
            or ""
        ).strip()
        if runtime_binding_id:
            runtimes_by_binding.setdefault(runtime_binding_id, []).append(runtime)
        for value in (
            runtime.get("runtime_id"),
            runtime.get("runtime_binding_id"),
            runtime.get("id"),
        ):
            runtime_identity = str(value or "").strip()
            if runtime_identity:
                runtimes_by_identity.setdefault(runtime_identity, []).append(runtime)

    enriched_rows: List[Dict[str, Any]] = []
    for raw in raw_records:
        projected = _project_persona_league_row(raw)
        persona_id = str(projected.get("persona_id") or projected.get("id") or "").strip()
        persona_bindings = bindings_by_persona.get(persona_id, [])
        runtime_candidates: Dict[str, Dict[str, Any]] = {}

        def include_runtime(runtime: Dict[str, Any]) -> None:
            identity = str(
                runtime.get("runtime_id")
                or runtime.get("runtime_binding_id")
                or runtime.get("id")
                or ""
            ).strip()
            if identity:
                runtime_candidates[identity] = runtime

        for runtime in runtimes_by_persona.get(persona_id, []):
            include_runtime(runtime)
        for runtime_id in _pm12_persona_runtime_ids(projected):
            for runtime in runtimes_by_identity.get(runtime_id, []):
                include_runtime(runtime)
        for binding in persona_bindings:
            for value in (
                binding.get("id"),
                binding.get("binding_id"),
                binding.get("persona_capital_binding_id"),
            ):
                binding_id = str(value or "").strip()
                if not binding_id:
                    continue
                for runtime in runtimes_by_binding.get(binding_id, []):
                    include_runtime(runtime)

        enriched_rows.append(
            _enrich_persona_item_with_bindings(
                projected,
                bindings=persona_bindings,
                runtimes=list(runtime_candidates.values()),
                persona=raw,
                league_entry=league_by_persona.get(persona_id, {}),
            )
        )
    rows = sorted(
        enriched_rows,
        key=lambda row: str(row.get("name") or row.get("id") or ""),
    )
    return _pm12_filter_persona_items(
        rows,
        state=state,
        archetype=archetype,
        q=q,
    )


# --- _pm12_persona_league_source_surfaces ---
def _pm12_persona_league_source_surfaces(snapshot_at: str) -> Dict[str, Dict[str, Any]]:
    return {
        "personas": _dataset_surface_status("personas", snapshot_at=snapshot_at),
        "route_policies": _composed_surface_status(snapshot_at=snapshot_at),
        "capability_snapshots": _dataset_surface_status("capability_snapshots", snapshot_at=snapshot_at),
        "persona_bindings": _dataset_surface_status("persona_bindings", snapshot_at=snapshot_at),
        "runtime_bindings": _dataset_surface_status("runtime_bindings", snapshot_at=snapshot_at),
        "telemetry_summaries": _dataset_surface_status("telemetry_summaries", snapshot_at=snapshot_at),
        "persona_sessions": _dataset_surface_status("sessions", snapshot_at=snapshot_at),
        "teaching_sessions": _dataset_surface_status("teaching_sessions", snapshot_at=snapshot_at),
        "persona_memory": _composed_surface_status(snapshot_at=snapshot_at),
    }


# --- _pm12_persona_league_ranking_item ---
def _pm12_persona_league_ranking_item(
    row: Dict[str, Any],
    *,
    metrics: Optional[Dict[str, Any]] = None,
    telemetry_cache: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    metrics = (
        metrics
        if isinstance(metrics, dict)
        else _pm12_persona_telemetry_metrics(row, telemetry_cache=telemetry_cache)
    )
    scores = _pm12_persona_league_scores(row, metrics)
    tier = _pm12_tier_for_score(scores["overall_score"])
    components = dict(scores)

    state = row.get("state")
    stage = str(row.get("stage") or state or "unknown").strip().lower() or "unknown"
    telemetry_count = metrics.get("telemetry_coverage_count", 0)
    active_stages = {"paper_running", "canary_running", "live_running"}
    lifecycle_operational = _is_persona_lifecycle_operational(state)
    runtime_resolution = str(row.get("runtime_resolution") or "missing")
    session_resolution = str(row.get("session_resolution") or "missing")
    telemetry_resolution = str(
        metrics.get("telemetry_resolution") or (
            "fresh" if telemetry_count else "missing"
        )
    )

    exclusion_codes: List[str] = []
    exclusion_reasons: List[str] = []
    if not lifecycle_operational:
        exclusion_codes.append("inactive_lifecycle")
        exclusion_reasons.append(f"Inactive lifecycle state: {state}")
    if stage not in active_stages:
        exclusion_codes.append("stage_not_running")
        exclusion_reasons.append(f"Inactive governed stage: {stage}")
    if runtime_resolution == "missing":
        exclusion_codes.append("missing_runtime")
        exclusion_reasons.append("No authoritative active RuntimeBinding")
    elif runtime_resolution in {"inactive", "retired"}:
        exclusion_codes.append("inactive_runtime")
        exclusion_reasons.append(f"RuntimeBinding is {runtime_resolution}")
    elif runtime_resolution == "stale":
        exclusion_codes.append("stale_runtime")
        exclusion_reasons.append("RuntimeBinding freshness is stale or degraded")
    elif runtime_resolution == "invalid_deployment_mode":
        exclusion_codes.append("inactive_runtime")
        exclusion_reasons.append("RuntimeBinding has no authoritative deployment_mode")
    elif runtime_resolution in {"ambiguous", "identity_mismatch"}:
        exclusion_codes.append("runtime_identity_mismatch")
        exclusion_reasons.append("RuntimeBinding identity is not authoritative")
    if session_resolution in {"missing", "missing_runtime", "inactive"}:
        exclusion_codes.append("missing_active_session")
        exclusion_reasons.append("No active session is joined to the RuntimeBinding")
    elif session_resolution == "ended":
        exclusion_codes.append("ended_session")
        exclusion_reasons.append("The RuntimeBinding session has ended")
    elif session_resolution == "stale":
        exclusion_codes.append("stale_session")
        exclusion_reasons.append("The RuntimeBinding session heartbeat is stale")
    elif session_resolution == "identity_mismatch":
        exclusion_codes.append("runtime_identity_mismatch")
        exclusion_reasons.append("Session identity does not join to the RuntimeBinding")
    if telemetry_resolution == "missing":
        exclusion_codes.append("missing_telemetry")
        exclusion_reasons.append("No telemetry coverage")
    elif telemetry_resolution == "stale":
        exclusion_codes.append("stale_telemetry")
        exclusion_reasons.append("Runtime telemetry is stale")
    elif telemetry_resolution == "degraded":
        exclusion_codes.append("degraded_telemetry")
        exclusion_reasons.append("Runtime telemetry is degraded")
    elif telemetry_resolution == "identity_mismatch":
        exclusion_codes.append("runtime_identity_mismatch")
        exclusion_reasons.append("Telemetry identity does not join to the RuntimeBinding")
    if stage in {"canary_running", "live_running"} and row.get("current_weight") is None:
        exclusion_codes.append("missing_current_weight")
        exclusion_reasons.append("Missing authoritative current weight")
    if stage in {"canary_running", "live_running"} and row.get("capital_scope") == "unbound":
        exclusion_codes.append("missing_capital_binding")
        exclusion_reasons.append("Missing authoritative real-capital binding")
    binding_resolution = str(row.get("binding_resolution") or "")
    if (
        any(token in binding_resolution for token in ("ambiguous", "mismatch"))
        or (
            stage in {"canary_running", "live_running"}
            and any(token in binding_resolution for token in ("missing", "inactive"))
        )
    ):
        exclusion_codes.append("binding_mismatch")
        exclusion_reasons.append("Binding/runtime identity prevents an authoritative allocation join")
    exclusion_codes = list(dict.fromkeys(exclusion_codes))
    exclusion_reasons = list(dict.fromkeys(exclusion_reasons))
    eligible = not exclusion_reasons
    exclusion_reason = "; ".join(exclusion_reasons) if exclusion_reasons else None

    evidence_coverage = min(1.0, telemetry_count / 10.0) if telemetry_count > 0 else 0.0

    if telemetry_count == 0:
        source_confidence = "unavailable"
    elif metrics.get("pnl") is None or metrics.get("drawdown") is None:
        source_confidence = "degraded"
    else:
        source_confidence = "formal"

    telemetry_evidence_refs = list(metrics.get("telemetry_evidence_refs") or [])
    return {
        "id": row.get("id"),
        "persona_id": row.get("persona_id") or row.get("id"),
        "name": row.get("name"),
        "owner": row.get("owner"),
        "state": state,
        "stage": stage,
        "deployment_stage": row.get("deployment_stage"),
        "capital_mode": row.get("capital_mode"),
        "capital_scope": row.get("capital_scope"),
        "capital_scope_id": row.get("capital_scope_id"),
        "capital_pool_id": row.get("capital_pool_id"),
        "capital_sleeve_id": row.get("capital_sleeve_id"),
        "paper_ledger_id": row.get("paper_ledger_id"),
        "current_weight": row.get("current_weight"),
        "target_weight": row.get("target_weight"),
        "binding_state": row.get("binding_state"),
        "binding_resolution": row.get("binding_resolution"),
        "runtime_resolution": runtime_resolution,
        "session_resolution": session_resolution,
        "session_id": row.get("session_id"),
        "session_authority": row.get("session_authority"),
        "telemetry_resolution": telemetry_resolution,
        "current_weight_source": row.get("current_weight_source"),
        "binding_ids": list(row.get("binding_ids") or []),
        "strategy_ids": list(row.get("strategy_ids") or []),
        "runtime_ids": list(row.get("runtime_ids") or metrics.get("runtime_ids") or []),
        "capital_pool_ids": list(row.get("capital_pool_ids") or []),
        "sleeve_ids": list(row.get("sleeve_ids") or []),
        "artifact_ids": list(row.get("artifact_ids") or []),
        "broker_ids": list(row.get("broker_ids") or []),
        "risk": row.get("risk"),
        "archetype": row.get("archetype"),
        "tier": tier["id"],
        "tier_id": tier["id"],
        "tier_label": tier["label"],
        "overall_score": scores["overall_score"],
        "metrics": metrics,
        "components": components,
        "links": row.get("links") or {},
        "eligible": eligible,
        "exclusion_reason": exclusion_reason,
        "exclusion_reasons": exclusion_reasons,
        "exclusion_codes": exclusion_codes,
        "evidence_coverage": evidence_coverage,
        "evidence_refs": telemetry_evidence_refs,
        "evidence_ref_ids": sorted({
            _pm12_evidence_ref_key(ref)
            for ref in telemetry_evidence_refs
            if _pm12_evidence_ref_key(ref)
        }),
        "source_confidence": source_confidence,
    }


# --- _pm12_persona_league_rankings ---
def _pm12_persona_league_rankings(
    rows: List[Dict[str, Any]],
    *,
    criteria: Optional[str],
    limit: int,
    base_items: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    requested = [
        token.strip().lower()
        for token in str(criteria or "").split(",")
        if token.strip()
    ]
    criteria_keys = [
        key for key in (requested or list(_PM12_LEAGUE_RANKING_CRITERIA.keys()))
        if key in _PM12_LEAGUE_RANKING_CRITERIA
    ] or list(_PM12_LEAGUE_RANKING_CRITERIA.keys())
    if base_items is None:
        base_items = [_pm12_persona_league_ranking_item(row) for row in rows]
    blocks: List[Dict[str, Any]] = []
    for criterion in criteria_keys:
        score_key, label = _PM12_LEAGUE_RANKING_CRITERIA[criterion]
        ranked = sorted(
            base_items,
            key=lambda item: (
                _management_number((item.get("components") or {}).get(score_key)) or 0.0,
                str(item.get("persona_id") or ""),
            ),
            reverse=True,
        )[:limit]
        block_items: List[Dict[str, Any]] = []
        for rank, item in enumerate(ranked, start=1):
            score = _management_number((item.get("components") or {}).get(score_key)) or 0.0
            block_items.append({
                **item,
                "rank": rank,
                "score": score,
                "score_field": score_key,
                "period": "short_cycle",
                "criteria": criterion,
            })
        blocks.append({
            "id": f"persona-league-{criterion}",
            "ranking_id": f"persona-league-{criterion}",
            "criteria": criterion,
            "label": label,
            "formula_version": "pm12-default-v1",
            "weights": dict(_PM12_LEAGUE_SCORE_WEIGHTS),
            "items": block_items,
            "ranked_count": len(block_items),
        })
    return blocks


# --- _pm12_persona_league_tier_payload ---
def _pm12_persona_league_tier_payload(
    rows: List[Dict[str, Any]],
    ranking_items: Optional[List[Dict[str, Any]]] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    if ranking_items is None:
        ranking_items = [_pm12_persona_league_ranking_item(row) for row in rows]
    ranking_items = sorted(
        ranking_items,
        key=lambda item: (
            _management_number(item.get("overall_score")) or 0.0,
            str(item.get("persona_id") or ""),
        ),
        reverse=True,
    )
    assignments = [
        {
            "persona_id": item.get("persona_id"),
            "name": item.get("name"),
            "tier": item.get("tier"),
            "tier_id": item.get("tier_id"),
            "tier_label": item.get("tier_label"),
            "overall_score": item.get("overall_score"),
            "metrics": item.get("metrics") or {},
        }
        for item in ranking_items
    ]
    by_tier: Dict[str, int] = {}
    for assignment in assignments:
        tier_id = str(assignment.get("tier_id") or "unknown")
        by_tier[tier_id] = by_tier.get(tier_id, 0) + 1
    tiers: List[Dict[str, Any]] = []
    for definition in _PM12_LEAGUE_TIER_DEFINITIONS:
        tier_id = str(definition["id"])
        tier_assignments = [item for item in assignments if item.get("tier_id") == tier_id]
        tiers.append({
            **definition,
            "persona_count": len(tier_assignments),
            "persona_ids": [item["persona_id"] for item in tier_assignments],
            "assignments": tier_assignments,
        })
    summary = {
        "season_id": "current",
        "formula_version": "pm12-default-v1",
        "persona_count": len(assignments),
        "tier_count": len(tiers),
        "by_tier": by_tier,
    }
    return tiers, assignments, summary


# --- _pm12_persona_league_mover_items ---
def _pm12_persona_league_mover_items(
    rows: List[Dict[str, Any]],
    *,
    direction: str,
    limit: int,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    ranked_items = sorted(
        (_pm12_persona_league_ranking_item(row) for row in rows),
        key=lambda item: (
            _management_number(item.get("overall_score")) or 0.0,
            str(item.get("persona_id") or ""),
        ),
        reverse=True,
    )
    movers: List[Dict[str, Any]] = []
    direction_counts = {"up": 0, "down": 0, "flat": 0, "new": 0}
    for current_rank, item in enumerate(ranked_items, start=1):
        persona_id = str(item.get("persona_id") or item.get("id") or "")
        current_score = _management_number(item.get("overall_score")) or 0.0
        movement_direction = "new"
        direction_counts[movement_direction] += 1
        mover = {
            **item,
            "id": f"persona-league-mover-{persona_id}",
            "mover_id": f"persona-league-mover-{persona_id}",
            "current_rank": current_rank,
            "previous_rank": None,
            "rank_delta": None,
            "direction": movement_direction,
            "current_score": current_score,
            "previous_score": None,
            "score_delta": None,
            "score_delta_display": "baseline unavailable",
            "baseline_status": "unavailable",
            "basis": "current_persona_league_snapshot_no_historical_baseline",
            "rank": current_rank,
            "score": current_score,
            "score_field": "overall_score",
            "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
            "movement": {
                "direction": movement_direction,
                "rank_delta": None,
                "score_delta": None,
                "baseline_status": "unavailable",
                "basis": "current_persona_league_snapshot_no_historical_baseline",
            },
        }
        movers.append(mover)

    if direction != "all":
        movers = [item for item in movers if item.get("direction") == direction]
    movers = sorted(
        movers,
        key=lambda item: (
            item.get("baseline_status") != "unavailable",
            abs(_management_number(item.get("score_delta")) or 0.0),
            -int(item.get("current_rank") or 0),
            str(item.get("persona_id") or ""),
        ),
        reverse=True,
    )
    limited = movers[:limit]
    top_mover = limited[0] if limited else None
    summary = {
        "persona_count": len(rows),
        "mover_count": len(movers),
        "returned_count": len(limited),
        "direction": direction,
        "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
        "baseline_status": "unavailable",
        "baseline_unavailable_count": len(ranked_items),
        "up_count": direction_counts["up"],
        "down_count": direction_counts["down"],
        "flat_count": direction_counts["flat"],
        "new_count": direction_counts["new"],
        "top_mover_persona_id": (top_mover or {}).get("persona_id") if isinstance(top_mover, dict) else None,
        "basis": "current_persona_league_snapshot_no_historical_baseline",
    }
    return limited, summary


# --- _pm12_heatmap_bucket_delta ---
def _pm12_heatmap_bucket_delta(bucket: str) -> tuple[str, timedelta]:
    bucket_key = str(bucket or "").strip().lower() or "day"
    delta = _PM12_HEATMAP_BUCKET_DELTAS.get(bucket_key)
    if delta is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_heatmap_bucket",
                "message": "bucket must be one of: hour, day, week.",
                "field": "bucket",
            },
        )
    return bucket_key, delta


# --- _pm12_floor_bucket_start ---
def _pm12_floor_bucket_start(value: datetime, bucket: str) -> datetime:
    value = value.astimezone(timezone.utc)
    if bucket == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    if bucket == "week":
        day_start = value.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start - timedelta(days=day_start.weekday())
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


# --- _pm12_heatmap_bucket_label ---
def _pm12_heatmap_bucket_label(start_at: datetime, bucket: str) -> str:
    if bucket == "hour":
        return start_at.strftime("%Y-%m-%d %H:00 UTC")
    if bucket == "week":
        year, week, _weekday = start_at.isocalendar()
        return f"{year}-W{week:02d}"
    return start_at.date().isoformat()


# --- _pm12_heatmap_buckets ---
def _pm12_heatmap_buckets(
    snapshot_at: str,
    *,
    bucket: str,
    bucket_count: int,
) -> tuple[str, List[Dict[str, Any]]]:
    bucket_key, delta = _pm12_heatmap_bucket_delta(bucket)
    snapshot_dt = _audit_datetime(snapshot_at) or datetime.now(timezone.utc)
    current_bucket_start = _pm12_floor_bucket_start(snapshot_dt, bucket_key)
    first_start = current_bucket_start - (delta * (bucket_count - 1))
    buckets: List[Dict[str, Any]] = []
    for index in range(bucket_count):
        start_at = first_start + (delta * index)
        end_at = start_at + delta
        start_iso = _pm12_iso_z(start_at)
        end_iso = _pm12_iso_z(end_at)
        buckets.append({
            "id": f"{bucket_key}-{start_iso}",
            "bucket_id": f"{bucket_key}-{start_iso}",
            "index": index,
            "label": _pm12_heatmap_bucket_label(start_at, bucket_key),
            "start_at": start_iso,
            "end_at": end_iso,
            "end_exclusive_at": end_iso,
        })
    return bucket_key, buckets


# --- _pm12_records_for_heatmap_bucket ---
def _pm12_records_for_heatmap_bucket(
    records: List[Dict[str, Any]],
    *,
    start_at: datetime,
    end_at: datetime,
) -> tuple[List[Dict[str, Any]], str]:
    observed = [
        record
        for record in records
        for timestamp in [_pm12_telemetry_record_timestamp(record)]
        if timestamp is not None and start_at <= timestamp < end_at
    ]
    if observed:
        return observed, "observed"
    carried = [
        record
        for record in records
        for timestamp in [_pm12_telemetry_record_timestamp(record)]
        if timestamp is not None and timestamp < end_at
    ]
    if carried:
        latest = sorted(
            carried,
            key=lambda item: _pm12_telemetry_record_timestamp(item) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:1]
        return latest, "carried_forward"
    return [], "latest_available"


# --- _pm12_persona_league_heatmap_cell ---
def _pm12_persona_league_heatmap_cell(
    row: Dict[str, Any],
    bucket: Dict[str, Any],
    *,
    runtime_ids: List[str],
    records: List[Dict[str, Any]],
    latest_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    start_at = _audit_datetime(bucket.get("start_at") or bucket.get("startAt")) or datetime.now(timezone.utc)
    end_at = _audit_datetime(bucket.get("end_at") or bucket.get("endAt")) or start_at
    bucket_records, source = _pm12_records_for_heatmap_bucket(
        records,
        start_at=start_at,
        end_at=end_at,
    )
    metrics = (
        _pm12_telemetry_metrics_from_records(runtime_ids, bucket_records)
        if bucket_records
        else latest_metrics
    )
    scores = _pm12_persona_league_scores(row, metrics)
    overall_score = scores["overall_score"]
    components = dict(scores)
    bucket_id = str(bucket.get("bucket_id") or bucket.get("bucketId") or bucket.get("id") or "")
    persona_id = row.get("persona_id") or row.get("id")
    return {
        "id": f"{persona_id}:{bucket_id}",
        "persona_id": persona_id,
        "bucket_id": bucket_id,
        "bucket_index": bucket["index"],
        "score": overall_score,
        "composite_score": overall_score,
        "overall_score": overall_score,
        "components": components,
        "metrics": metrics,
        "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
        "source": source,
        "observed_telemetry_count": len(bucket_records),
        "latest_telemetry_at": metrics.get("latest_telemetry_at"),
    }


# --- _pm12_persona_league_heatmap_rows ---
def _pm12_persona_league_heatmap_rows(
    rows: List[Dict[str, Any]],
    buckets: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    heatmap_rows: List[Dict[str, Any]] = []
    cells: List[Dict[str, Any]] = []
    score_values: List[float] = []
    for row in rows:
        telemetry_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        runtime_ids = _pm12_persona_runtime_ids(row, telemetry_cache=telemetry_cache)
        records = _pm12_persona_telemetry_records(
            row,
            runtime_ids=runtime_ids,
            telemetry_cache=telemetry_cache,
        )
        latest_metrics = _pm12_telemetry_metrics_from_records(runtime_ids, records)
        ranking_item = _pm12_persona_league_ranking_item(row, metrics=latest_metrics)
        row_cells = [
            _pm12_persona_league_heatmap_cell(
                row,
                bucket,
                runtime_ids=runtime_ids,
                records=records,
                latest_metrics=latest_metrics,
            )
            for bucket in buckets
        ]
        for cell in row_cells:
            score = _management_number(cell.get("score"))
            if score is not None:
                score_values.append(score)
        cells.extend(row_cells)
        heatmap_rows.append({
            "id": ranking_item.get("persona_id"),
            "persona_id": ranking_item.get("persona_id"),
            "name": ranking_item.get("name"),
            "owner": ranking_item.get("owner"),
            "state": ranking_item.get("state"),
            "risk": ranking_item.get("risk"),
            "archetype": ranking_item.get("archetype"),
            "tier": ranking_item.get("tier"),
            "tier_id": ranking_item.get("tier_id"),
            "tier_label": ranking_item.get("tier_label"),
            "latest_score": ranking_item.get("overall_score"),
            "runtime_ids": runtime_ids,
            "cells": row_cells,
            "links": ranking_item.get("links") or {},
        })
    summary = {
        "persona_count": len(rows),
        "bucket_count": len(buckets),
        "cell_count": len(cells),
        "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
        "min_score": min(score_values) if score_values else None,
        "max_score": max(score_values) if score_values else None,
        "average_score": _management_avg(score_values),
    }
    return heatmap_rows, cells, summary


# --- _ADVANCE_LIFECYCLE_VALID_TARGETS ---
_ADVANCE_LIFECYCLE_VALID_TARGETS = frozenset({"paper_owner", "live_owner", "retired"})


# --- _ADVANCE_LIFECYCLE_LIVE_ROLES ---
_ADVANCE_LIFECYCLE_LIVE_ROLES = frozenset({"approver", "admin"})


# --- _merged_tool_records ---
def _merged_tool_records() -> List[Dict[str, Any]]:
    return _merge_registry_records(
        _tool_fixture_records(),
        [dict(record) for record in _TOOL_REGISTRY.values()],
        ("tool_id", "id"),
    )


# --- _merged_skill_records ---
def _merged_skill_records() -> List[Dict[str, Any]]:
    return _merge_registry_records(
        _skill_fixture_records(),
        [dict(record) for record in _SKILL_REGISTRY.values()],
        ("skill_id", "id"),
    )


# --- _persona_id ---
def _persona_id(record: Dict[str, Any]) -> str:
    return str(record.get("persona_id") or record.get("id") or "").strip()


# --- _persona_health_status ---
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


# --- _persona_fleet_context_missing ---
def _persona_fleet_context_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


# --- _persona_fleet_market_key ---
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


# --- _persona_fleet_context_defaults_by_market ---
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


# --- _persona_fleet_context_overlay ---
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


# --- _PERSONA_FLEET_INVALID_MUTATION_IDS ---
_PERSONA_FLEET_INVALID_MUTATION_IDS = {
    "",
    "n/a",
    "na",
    "nan",
    "none",
    "null",
    "undefined",
}


# --- _PERSONA_FLEET_DATE_MUTATION_ID ---
_PERSONA_FLEET_DATE_MUTATION_ID = re.compile(
    r"^\d{4}[-/]\d{2}[-/]\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)?$"
)


# --- _persona_fleet_mutation_id ---
def _persona_fleet_mutation_id(value: Any) -> Optional[str]:
    candidate = str(value or "").strip()
    if candidate.lower() in _PERSONA_FLEET_INVALID_MUTATION_IDS:
        return None
    if _PERSONA_FLEET_DATE_MUTATION_ID.fullmatch(candidate):
        return None
    return candidate


# --- _persona_fleet_mutation_projection ---
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


# --- _persona_fleet_query_filter ---
def _persona_fleet_query_filter(value: Optional[str]) -> Optional[Set[str]]:
    if not value:
        return None
    tokens = {token.strip().lower() for token in value.split(",") if token.strip()}
    return tokens or None


# --- _persona_fleet_first_binding_from_index ---
def _persona_fleet_first_binding_from_index(
    persona_id: str,
    bindings_by_persona: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    bindings = bindings_by_persona.get(persona_id) or []
    active = [
        binding
        for binding in bindings
        if str(binding.get("status") or binding.get("validity") or "").lower()
        in {"active", "ready", "bound"}
    ]
    return active[0] if active else (bindings[0] if bindings else {})


# --- _persona_fleet_count_by ---
def _persona_fleet_count_by(items: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        value = str(item.get(field) or "unknown").strip().lower() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


# --- _PERSONA_FLEET_HEALTH_RANK ---
_PERSONA_FLEET_HEALTH_RANK = {"healthy": 0, "degraded": 1, "critical": 2}


# --- _persona_fleet_worst_health ---
def _persona_fleet_worst_health(*statuses: Optional[str]) -> str:
    best_status = "healthy"
    best_rank = -1
    for status in statuses:
        normalized = str(status or "").strip().lower()
        rank = _PERSONA_FLEET_HEALTH_RANK.get(normalized)
        if rank is None:
            continue
        if rank > best_rank:
            best_status = normalized
            best_rank = rank
    return best_status


# --- _persona_fleet_active_incidents_for_row ---
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


# --- _persona_fleet_list_data_source_summary ---
def _persona_fleet_list_data_source_summary(
    *,
    metadata: Dict[str, Any],
    persona: Dict[str, Any],
    context_persona: Dict[str, Any],
) -> Dict[str, Any]:
    status = metadata.get("data_source_status") if isinstance(metadata.get("data_source_status"), dict) else {}
    sources = metadata.get("data_sources") if isinstance(metadata.get("data_sources"), list) else []
    required_sources = persona.get("required_data_sources") if isinstance(persona.get("required_data_sources"), list) else []
    if not required_sources and isinstance(context_persona.get("required_data_sources"), list):
        required_sources = context_persona.get("required_data_sources") or []

    provider_statuses = status.get("provider_statuses") if isinstance(status.get("provider_statuses"), dict) else {}
    provider_counts: Dict[str, int] = {}
    for value in provider_statuses.values():
        normalized = str(value or "unknown").strip().lower() or "unknown"
        provider_counts[normalized] = provider_counts.get(normalized, 0) + 1
    if not provider_counts:
        for source in sources:
            if not isinstance(source, dict):
                continue
            normalized = str(source.get("status") or "unknown").strip().lower() or "unknown"
            provider_counts[normalized] = provider_counts.get(normalized, 0) + 1

    healthy_states = {"ok", "read_ok", "datasource_smoke_ok", "source_health_ok"}
    degraded_count = sum(
        count for state, count in provider_counts.items()
        if state not in healthy_states
    )
    return {
        "state": str(status.get("state") or "unknown"),
        "provider_count": len(provider_statuses) or len(sources),
        "provider_status_counts": provider_counts,
        "degraded_provider_count": degraded_count,
        "required_source_count": len(required_sources),
        "configured_source_count": len(sources),
        "live_ingestion_enabled": bool(status.get("live_ingestion_enabled")),
        "source_health_source": status.get("source_health_source"),
    }


# --- _persona_fleet_list_data_sources ---
def _persona_fleet_list_data_sources(data_sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compact_sources: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for source in data_sources:
        if not isinstance(source, dict):
            continue
        provider_key = str(source.get("provider_key") or source.get("providerKey") or "").strip()
        provider = str(source.get("provider") or provider_key or "").strip()
        stable_key = provider_key or provider
        if not stable_key or stable_key in seen:
            continue
        seen.add(stable_key)
        compact_sources.append({
            "provider_key": provider_key or provider,
            "provider": provider or provider_key,
            "market": source.get("market"),
            "source_class": source.get("source_class") or source.get("sourceClass"),
            "status": str(source.get("status") or "unknown"),
            "order_capable_provider": bool(source.get("order_capable_provider") or source.get("orderCapableProvider")),
            "read_only": bool(source.get("read_only", source.get("readOnly", True))),
            "order_side_effects_allowed": bool(
                source.get("order_side_effects_allowed")
                or source.get("orderSideEffectsAllowed")
            ),
            "capital_side_effects_allowed": bool(
                source.get("capital_side_effects_allowed")
                or source.get("capitalSideEffectsAllowed")
            ),
        })
    return compact_sources


# --- _persona_fleet_list_research_summary ---
def _persona_fleet_list_research_summary(metadata: Dict[str, Any]) -> Dict[str, Any]:
    status = metadata.get("research_status") if isinstance(metadata.get("research_status"), dict) else {}
    refs = metadata.get("research_refs") if isinstance(metadata.get("research_refs"), list) else []
    projects = (
        metadata.get("current_research_projects")
        if isinstance(metadata.get("current_research_projects"), list)
        else []
    )
    frameworks = status.get("frameworks") if isinstance(status.get("frameworks"), list) else []
    framework = status.get("framework") or (frameworks[0] if frameworks else None)
    return {
        "stage": status.get("stage"),
        "framework": framework,
        "framework_count": len(frameworks) if frameworks else (1 if framework else 0),
        "experiment_id": status.get("experiment_id"),
        "artifact_id": status.get("artifact_id"),
        "registry_admission_status": status.get("registry_admission_status"),
        "can_deploy": bool(status.get("can_deploy")),
        "current_project_count": len(projects),
        "evidence_ref_count": len(refs),
    }


# --- _PERSONA_FLEET_RUNNING_STAGE_STATES ---
_PERSONA_FLEET_RUNNING_STAGE_STATES = {
    "paper": "paper_running",
    "canary": "canary_running",
    "live": "live_running",
}


# --- _PERSONA_FLEET_TERMINAL_OR_GOVERNED_STATES ---
_PERSONA_FLEET_TERMINAL_OR_GOVERNED_STATES = {
    "draft",
    "needs_human_approval",
    "canary_authorized_not_started",
    "rollback_required",
    "paused",
    "retired",
    "stopped",
    "failed",
}


# --- _persona_fleet_record_value ---
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


# --- _persona_fleet_capital_mode ---
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


# --- _persona_fleet_live_capital_pool_id ---
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


# --- _persona_fleet_paper_ledger_id ---
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


# --- _persona_fleet_paper_budget ---
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


# --- _persona_fleet_paper_ledger ---
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


# --- _persona_fleet_capital_binding_projection ---
def _persona_fleet_capital_binding_projection(
    *,
    persona_id: str,
    capital_mode: str,
    deployment_stage: Any,
    paper_ledger_id: Optional[str],
    live_pool_id: Optional[str],
    binding: Dict[str, Any],
    runtime: Dict[str, Any],
    league_entry: Dict[str, Any],
    raw_metadata: Dict[str, Any],
    context_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    stage = str(deployment_stage or capital_mode or "none").strip().lower() or "none"
    sleeve_id = None
    if capital_mode in {"canary", "live"}:
        sleeve_id = _persona_fleet_record_value(
            league_entry, "capital_sleeve_id", "capitalSleeveId", "sleeve_id", "sleeveId"
        ) or _persona_fleet_record_value(
            raw_metadata, "capital_sleeve_id", "capitalSleeveId", "sleeve_id", "sleeveId"
        ) or _persona_fleet_record_value(
            context_metadata, "capital_sleeve_id", "capitalSleeveId", "sleeve_id", "sleeveId"
        ) or _persona_fleet_record_value(
            binding, "capital_sleeve_id", "capitalSleeveId", "sleeve_id", "sleeveId"
        ) or _persona_fleet_record_value(
            runtime, "capital_sleeve_id", "capitalSleeveId", "sleeve_id", "sleeveId"
        )
    sleeve_id = str(sleeve_id or "").strip() or None

    def optional_weight(*keys: str) -> Optional[float]:
        for record in (league_entry, binding, runtime, raw_metadata, context_metadata):
            value = _persona_fleet_record_value(record, *keys)
            if value in (None, "") or isinstance(value, bool):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    current_weight = optional_weight("current_weight", "currentWeight", "allocation_weight", "weight")
    target_weight = optional_weight("target_weight", "targetWeight", "proposed_weight")
    raw_state = _persona_fleet_record_value(binding, "binding_state", "bindingState", "status", "validity")
    if not raw_state:
        raw_state = "isolated" if capital_mode == "paper" and paper_ledger_id else "missing"
    binding_state = str(raw_state).strip().lower() or "missing"
    if capital_mode == "paper":
        scope = "paper_ledger"
        scope_id = paper_ledger_id
    elif sleeve_id:
        scope = "canary_sleeve" if capital_mode == "canary" else "live_sleeve"
        scope_id = sleeve_id
    elif live_pool_id:
        scope = "capital_pool"
        scope_id = live_pool_id
    else:
        scope = "unbound"
        scope_id = None
    return {
        "stage": stage,
        "capital_scope": scope,
        "capital_scope_id": scope_id,
        "capital_sleeve_id": sleeve_id,
        "current_weight": current_weight,
        "target_weight": target_weight,
        "binding_state": binding_state,
        "capital_binding": {
            "persona_id": persona_id,
            "stage": stage,
            "scope": scope,
            "scope_id": scope_id,
            "paper_ledger_id": paper_ledger_id,
            "capital_pool_id": live_pool_id,
            "capital_sleeve_id": sleeve_id,
            "current_weight": current_weight,
            "target_weight": target_weight,
            "state": binding_state,
        },
    }


# --- _persona_fleet_runtime_binding_id ---
def _persona_fleet_runtime_binding_id(
    *,
    runtime_id: Any,
    runtime: Dict[str, Any],
    binding: Dict[str, Any],
    raw_metadata: Dict[str, Any],
) -> Optional[str]:
    for value in (
        runtime.get("runtime_binding_id"),
        runtime.get("binding_id"),
        raw_metadata.get("runtime_binding_id"),
        binding.get("runtime_binding_id"),
        binding.get("binding_id"),
        binding.get("id"),
        runtime_id,
    ):
        clean = str(value or "").strip()
        if clean:
            return clean
    return None


# --- _persona_fleet_runtime_status ---
def _persona_fleet_runtime_status(runtime: Dict[str, Any]) -> str:
    return str(runtime.get("state") or runtime.get("status") or "").strip().lower()


# --- _persona_fleet_optional_int ---
def _persona_fleet_optional_int(value: Any) -> Optional[int]:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --- _persona_fleet_lifecycle_state ---
def _persona_fleet_lifecycle_state(
    *,
    persona_status: Any,
    lifecycle_state: Any,
    capital_mode: str,
    deployment_stage: Any,
    runtime: Dict[str, Any],
    has_runtime_or_binding: bool,
) -> str:
    raw_state = str(persona_status or lifecycle_state or "unknown").strip().lower()
    normalized_lifecycle = str(lifecycle_state or "").strip().lower()
    if raw_state in _PERSONA_FLEET_RUNNING_STAGE_STATES.values():
        return raw_state
    if raw_state in _PERSONA_FLEET_TERMINAL_OR_GOVERNED_STATES:
        return raw_state
    if normalized_lifecycle in _PERSONA_FLEET_TERMINAL_OR_GOVERNED_STATES:
        return normalized_lifecycle

    runtime_status = _persona_fleet_runtime_status(runtime)
    if runtime_status in {"failed", "error"}:
        return "failed"
    if runtime_status in {"stopped", "paused"} and capital_mode == "none":
        return runtime_status

    stage = str(deployment_stage or "").strip().lower()
    for candidate in (capital_mode, stage):
        if candidate in _PERSONA_FLEET_RUNNING_STAGE_STATES:
            return _PERSONA_FLEET_RUNNING_STAGE_STATES[candidate]

    if raw_state in {"deployed", "active", "running", "ready", "paper"} and has_runtime_or_binding:
        return "paper_running"
    return raw_state or "unknown"


# --- _persona_fleet_review_projection ---
def _persona_fleet_review_projection(
    *,
    persona_id: str,
    league_entry: Dict[str, Any],
    raw_metadata: Dict[str, Any],
    binding: Dict[str, Any],
    runtime: Dict[str, Any],
    human_needed: bool,
    recommendation: Any,
) -> Dict[str, Any]:
    review_id = str(
        league_entry.get("review_id")
        or league_entry.get("reviewId")
        or league_entry.get("promotion_review_id")
        or raw_metadata.get("review_id")
        or raw_metadata.get("promotion_review_id")
        or binding.get("review_id")
        or binding.get("approval_decision_id")
        or runtime.get("review_id")
        or runtime.get("approval_decision_id")
        or ""
    ).strip()
    recommendation_text = str(recommendation or "").strip().lower()
    review_type = str(
        league_entry.get("review_type")
        or raw_metadata.get("review_type")
        or ""
    ).strip().lower()
    if not review_type and any(term in recommendation_text for term in ("promote", "canary", "live")):
        review_type = "promotion_review"
    if not review_type and human_needed:
        review_type = "human_gate_review"
    if not review_id and human_needed:
        review_id = f"readiness_blocker:persona:{persona_id}"
        review_type = review_type or "readiness_blocker"
    inbox_id = str(
        league_entry.get("inbox_id")
        or raw_metadata.get("inbox_id")
        or (f"{review_type}:{review_id}" if review_id and review_type else "")
    ).strip()
    promotion_review_id = str(
        league_entry.get("promotion_review_id")
        or raw_metadata.get("promotion_review_id")
        or (review_id if review_type == "promotion_review" else "")
    ).strip()
    status = str(
        league_entry.get("review_status")
        or raw_metadata.get("review_status")
        or ("pending" if human_needed else "none")
    ).strip().lower()
    route = "/bff/management/human-inbox"
    if inbox_id:
        route = f"/bff/management/human-inbox/{inbox_id}"
    return {
        "review_id": review_id or None,
        "review_type": review_type or None,
        "promotion_review_id": promotion_review_id or None,
        "inbox_id": inbox_id or None,
        "review_status": status,
        "review": {
            "id": review_id or None,
            "type": review_type or None,
            "status": status,
            "inbox_id": inbox_id or None,
            "route": route if review_id or human_needed else None,
            "requires_human_gate": bool(human_needed),
        },
    }


# --- _project_persona_fleet_list_row ---
def _project_persona_fleet_list_row(
    *,
    persona: Dict[str, Any],
    league_entry: Dict[str, Any],
    binding: Dict[str, Any],
    runtime: Dict[str, Any],
    active_incidents: List[Dict[str, Any]],
    telemetry_summaries: List[Dict[str, Any]],
    context_metadata: Dict[str, Any],
    context_persona: Dict[str, Any],
    snapshot_at: str,
    evolution_decisions: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    persona_id = _persona_id(persona)
    if not persona_id:
        return None

    raw_metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
    league_metrics = league_entry.get("metrics") if isinstance(league_entry.get("metrics"), dict) else {}
    performance = (
        raw_metadata.get("performance")
        if isinstance(raw_metadata.get("performance"), dict)
        else {}
    )
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
    metrics = {**performance, **league_metrics}
    if telemetry_has_performance:
        for key in (
            "pnl",
            "drawdown",
            "max_drawdown",
            "fill_rate",
            "total_trades",
            "sharpe",
            "sharpe_ratio",
        ):
            metrics.pop(key, None)
        metrics.update({key: value for key, value in telemetry_metrics.items() if value is not None})
    if telemetry_has_performance:
        performance_source = "telemetry_summaries"
    elif league_metrics:
        performance_source = "persona_league"
    elif performance:
        performance_source = "persona_registry"
    else:
        performance_source = "unavailable"
    pool_id = (
        raw_metadata.get("capital_pool_id")
        or raw_metadata.get("legacy_paper_capital_pool_id")
        or binding.get("capital_pool_id")
        or league_entry.get("capital_pool_id")
        or context_metadata.get("capital_pool_id")
    )
    runtime_id = (
        raw_metadata.get("runtime_id")
        or runtime.get("runtime_id")
        or runtime.get("id")
        or league_entry.get("runtime_id")
        or context_metadata.get("runtime_id")
        or context_metadata.get("runtime_binding_id")
        or raw_metadata.get("runtime_binding_id")
    )
    deployment_stage = (
        league_entry.get("deployment_stage")
        or runtime.get("deployment_stage")
        or runtime.get("deployment_mode")
        or raw_metadata.get("deployment_stage")
        or context_metadata.get("deployment_stage")
        or "none"
    )
    capital_mode = _persona_fleet_capital_mode(
        league_entry=league_entry,
        raw_metadata=raw_metadata,
        binding=binding,
        runtime=runtime,
        deployment_stage=deployment_stage,
    )
    live_pool_id = _persona_fleet_live_capital_pool_id(
        capital_mode=capital_mode,
        pool_id=pool_id,
        league_entry=league_entry,
        raw_metadata=raw_metadata,
        context_metadata=context_metadata,
        binding=binding,
    )
    paper_ledger_id = _persona_fleet_paper_ledger_id(
        persona_id=persona_id,
        capital_mode=capital_mode,
        league_entry=league_entry,
        raw_metadata=raw_metadata,
        context_metadata=context_metadata,
        binding=binding,
        runtime=runtime,
    )
    paper_ledger = _persona_fleet_paper_ledger(
        paper_ledger_id=paper_ledger_id,
        persona_id=persona_id,
        league_entry=league_entry,
        raw_metadata=raw_metadata,
        context_metadata=context_metadata,
    )
    market_scope = list(league_entry.get("market_scope") or context_metadata.get("market_scope") or [])
    risk_flags = list(league_entry.get("risk_flags") or context_metadata.get("risk_flags") or [])
    lifecycle_state = str(persona.get("lifecycle_state") or persona.get("status") or "unknown")
    league_health = _persona_health_status(
        lifecycle_state=lifecycle_state,
        league_entry=league_entry,
        risk_flags=risk_flags,
    )
    operational_health = _project_persona_fleet_health(
        persona=persona,
        runtime_bindings=[runtime] if runtime else [],
        telemetry_summaries=telemetry_summaries,
        active_incidents=active_incidents,
    )
    health = _persona_fleet_worst_health(league_health, operational_health.get("status"))
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
    persona_status = str(
        raw_metadata.get("persona_status")
        or league_entry.get("status")
        or persona.get("status")
        or lifecycle_state
    )
    runtime_binding_id = _persona_fleet_runtime_binding_id(
        runtime_id=runtime_id,
        runtime=runtime,
        binding=binding,
        raw_metadata=raw_metadata,
    )
    normalized_state = _persona_fleet_lifecycle_state(
        persona_status=persona_status,
        lifecycle_state=lifecycle_state,
        capital_mode=capital_mode,
        deployment_stage=deployment_stage,
        runtime=runtime,
        has_runtime_or_binding=bool(runtime or binding or pool_id),
    )
    review_projection = _persona_fleet_review_projection(
        persona_id=persona_id,
        league_entry=league_entry,
        raw_metadata=raw_metadata,
        binding=binding,
        runtime=runtime,
        human_needed=human_needed,
        recommendation=recommendation,
    )
    updated_at = (
        league_entry.get("updated_at")
        or persona.get("updated_at")
        or persona.get("last_active_at")
        or snapshot_at
    )
    ooda_stage = league_entry.get("ooda_stage") or context_metadata.get("ooda_stage")
    score = min(
        _as_float(league_entry.get("league_score") or context_metadata.get("league_score"), 75.0),
        _as_float(operational_health.get("score"), 100.0),
    )
    league_rank = _persona_fleet_optional_int(
        league_entry.get("league_rank")
        or league_entry.get("rank")
        or context_metadata.get("league_rank")
    )
    league_score = _as_float(league_entry.get("league_score") or context_metadata.get("league_score"), score)
    routed = _routed_strategies_for_persona(persona_id)
    drill_target = runtime_id or persona_id
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
    row_context_metadata = {
        **context_metadata,
        "data_source_status": data_source_status,
        "data_sources": data_sources,
        "data_source_refs": data_source_refs,
        "research_status": research_status,
        "research_refs": research_refs,
        "current_research_projects": current_research_projects,
    }

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

    mutation_projection = _persona_fleet_mutation_projection(
        persona_id=persona_id,
        updated_at=updated_at,
        evolution_decisions=(
            evolution_decisions
            if evolution_decisions is not None
            else list(read_store.list_evolution_decisions() or [])
        ),
        artifact_ids=artifact_ids,
        incident_ids=incident_ids,
    )
    capital_binding_projection = _persona_fleet_capital_binding_projection(
        persona_id=persona_id,
        capital_mode=capital_mode,
        deployment_stage=deployment_stage,
        paper_ledger_id=paper_ledger_id,
        live_pool_id=live_pool_id,
        binding=binding,
        runtime=runtime,
        league_entry=league_entry,
        raw_metadata=raw_metadata,
        context_metadata=context_metadata,
    )
    performance_total_trades = _management_as_float(metrics.get("total_trades"))

    return {
        "id": persona_id,
        "persona_id": persona_id,
        **mutation_projection,
        "name": persona.get("name") or persona_id,
        "owner": raw_metadata.get("owner") or raw_metadata.get("owner_id") or "pathreon-management",
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
        "is_market_persona_default": bool(raw_metadata.get("is_market_persona_default") or raw_metadata.get("seed_row")),
        "isMarketPersonaDefault": bool(raw_metadata.get("is_market_persona_default") or raw_metadata.get("seed_row")),
        "seed_row": bool(raw_metadata.get("seed_row")),
        "seedRow": bool(raw_metadata.get("seed_row")),
        "human_needed": human_needed,
        "last_mutation": str(updated_at)[:10],
        "state": normalized_state,
        "current_work": context_metadata.get("current_work"),
        "routed_strategies": routed,
        "open_findings": len(risk_flags) + int(metrics.get("violation_count") or 0) + len(active_incidents),
        "market_scope": market_scope,
        "asset_classes": list(context_metadata.get("asset_classes") or []),
        "capital_mode": capital_mode,
        **capital_binding_projection,
        "paper_ledger_id": paper_ledger_id,
        "paperLedgerId": paper_ledger_id,
        "paper_ledger": paper_ledger,
        "paperLedger": paper_ledger,
        "legacy_paper_capital_pool_id": pool_id if capital_mode == "paper" else None,
        "legacyPaperCapitalPoolId": pool_id if capital_mode == "paper" else None,
        "capital_pool_id": live_pool_id,
        "capitalPoolId": live_pool_id,
        "capital_pool": (
            {
                "id": live_pool_id,
                "mode": capital_mode,
                "live_capital_enabled": capital_mode == "live",
            }
            if live_pool_id
            else None
        ),
        "capitalPool": (
            {
                "id": live_pool_id,
                "mode": capital_mode,
                "liveCapitalEnabled": capital_mode == "live",
            }
            if live_pool_id
            else None
        ),
        "runtime_id": runtime_id,
        "runtime_binding_id": runtime_binding_id,
        "runtime_binding": {
            "id": runtime_binding_id,
            "runtime_id": runtime_id,
            "state": _persona_fleet_runtime_status(runtime) or None,
            "deployment_stage": deployment_stage,
            "capital_mode": capital_mode,
            "health": operational_health.get("status"),
        },
        "deployment_stage": deployment_stage,
        "ooda_stage": ooda_stage,
        "recommendation": recommendation,
        "governance_required": governance_required,
        "review_id": review_projection["review_id"],
        "review_type": review_projection["review_type"],
        "promotion_review_id": review_projection["promotion_review_id"],
        "inbox_id": review_projection["inbox_id"],
        "review_status": review_projection["review_status"],
        "review": review_projection["review"],
        "league_rank": league_rank,
        "league_score": league_score,
        "rank": {
            "league_rank": league_rank,
            "league_score": league_score,
            "basis": "persona_league",
        },
        "runtime_health": operational_health,
        "data_source_summary": _persona_fleet_list_data_source_summary(
            metadata=row_context_metadata,
            persona=persona,
            context_persona=context_persona,
        ),
        "data_sources": _persona_fleet_list_data_sources(data_sources),
        "research_summary": _persona_fleet_list_research_summary(row_context_metadata),
        "performance_summary": {
            "pnl": _management_as_float(metrics.get("pnl")),
            "sharpe": _management_as_float(metrics.get("sharpe")),
            "max_drawdown": _management_as_float(metrics.get("max_drawdown")),
            "violation_count": int(metrics.get("violation_count") or 0),
            "total_trades": (
                int(performance_total_trades)
                if performance_total_trades is not None
                else None
            ),
            "source": performance_source,
            "telemetry_runtime_count": telemetry_rollup.get("runtime_count", 0),
            "latest_telemetry_at": telemetry_rollup.get("latest_collected_at"),
        },
        "risk_flag_count": len(risk_flags),
        "active_incident_count": len(active_incidents),
        "updated_at": updated_at,
        "links": {
            "detail": f"/personas/{persona_id}",
            "runtime": f"/management/runtimes/{runtime_id}" if runtime_id else None,
            "source_health": f"/bff/v5/execution/persona-health?persona_id={persona_id}",
        },
        "drill_down": {
            "kind": "runtime" if runtime_id else "persona",
            "href": f"/management/runtimes/{drill_target}" if runtime_id else f"/personas/{persona_id}",
            "runtime_id": runtime_id,
            "persona_id": persona_id,
        },
    }


# --- _persona_fleet_slim_list_payload ---
def _persona_fleet_slim_list_payload(
    *,
    snapshot_at: str,
    tenant_id: Optional[str] = None,
    state: Optional[str],
    health: Optional[str],
    deployment_stage: Optional[str],
    market_scope: Optional[str],
    q: Optional[str],
    page_token: Optional[str],
    page_size: int,
) -> Dict[str, Any]:
    # Capture the Paper ranking projection before the broader Fleet reads. The
    # quarterly endpoint uses this same read path; doing it first prevents a
    # later degraded persona-service read from silently restoring league rank.
    quarter_window = _pm12_quarter_window(None, snapshot_at)
    telemetry_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    try:
        for telemetry in read_store.list_telemetry_summaries() or []:
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
                telemetry_cache[runtime_id] = telemetry
    except Exception:
        telemetry_cache = {}
    paper_rankings = {
        str(item.get("persona_id") or item.get("id") or "").strip(): item
        for item in _pm12_quarterly_ranking_items(
            _pm12_persona_league_rows(state=None, archetype=None, q="", tenant_id=tenant_id),
            quarter_window=quarter_window,
            telemetry_cache=telemetry_cache,
        )
        if str(item.get("persona_id") or item.get("id") or "").strip()
    }
    directory = _get_persona_directory_snapshot(tenant_id, snapshot_at=snapshot_at)
    personas = list(directory.records_by_id.values())
    canonical_total = len(directory.records_by_id)
    catalog_default_total = len(directory.catalog_defaults_by_id)
    league = read_store.list_persona_league(include_market_persona_defaults=True)
    bindings = read_store.list_bindings(include_market_persona_defaults=True)
    runtimes = read_store.list_runtime_bindings(include_market_persona_defaults=True)
    pools = read_store.list_capital_pools(include_market_persona_defaults=True)
    incidents = read_store.list_incidents()
    evolution_decisions = list(read_store.list_evolution_decisions() or [])
    context_defaults = _persona_fleet_context_defaults_by_market(personas)

    league_by_persona = {
        str(item.get("persona_id") or item.get("id") or ""): item
        for item in league
    }
    bindings_by_persona: Dict[str, List[Dict[str, Any]]] = {}
    for binding in bindings:
        persona_id = str(binding.get("persona_id") or "").strip()
        if persona_id:
            bindings_by_persona.setdefault(persona_id, []).append(binding)
    runtime_by_pool = {
        str(runtime.get("capital_pool_id") or ""): runtime
        for runtime in runtimes
        if str(runtime.get("capital_pool_id") or "").strip()
    }
    runtime_by_persona = {
        str(runtime.get("persona_id") or ""): runtime
        for runtime in runtimes
        if str(runtime.get("persona_id") or "").strip()
    }
    runtime_by_runtime_id = {
        str(runtime.get("runtime_id") or "").strip(): runtime
        for runtime in runtimes
        if str(runtime.get("runtime_id") or "").strip()
    }
    runtime_by_binding = {
        candidate: runtime
        for runtime in runtimes
        for candidate in (
            str(runtime.get("binding_id") or "").strip(),
            str(runtime.get("runtime_binding_id") or "").strip(),
            str(runtime.get("persona_capital_binding_id") or "").strip(),
            str(runtime.get("id") or "").strip(),
        )
        if candidate
    }

    rows: List[Dict[str, Any]] = []
    for persona in personas:
        persona_id = _persona_id(persona)
        if not persona_id:
            continue
        raw_metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
        context_metadata, context_persona = _persona_fleet_context_overlay(
            persona,
            raw_metadata,
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
        binding = _persona_fleet_first_binding_from_index(persona_id, bindings_by_persona)
        pool_id = (
            raw_metadata.get("capital_pool_id")
            or raw_metadata.get("legacy_paper_capital_pool_id")
            or binding.get("capital_pool_id")
            or league_entry.get("capital_pool_id")
            or context_metadata.get("capital_pool_id")
        )
        declared_runtime_id = str(raw_metadata.get("runtime_id") or "").strip()
        declared_runtime_binding_id = str(raw_metadata.get("runtime_binding_id") or "").strip()

        # Resolve all bindings for the persona to match runtimes
        p_bindings = bindings_by_persona.get(persona_id, [])
        p_binding_keys = set()
        for b in p_bindings:
            for k in ("id", "binding_id", "persona_capital_binding_id"):
                val = str(b.get(k) or "").strip()
                if val:
                    p_binding_keys.add(val)
        if binding:
            for k in ("id", "binding_id", "persona_capital_binding_id"):
                val = str(binding.get(k) or "").strip()
                if val:
                    p_binding_keys.add(val)

        # Gather all runtimes associated with this persona (handle multiple runtimes)
        persona_runtimes = []
        seen_r_ids = set()
        for r in runtimes:
            r_id = str(r.get("runtime_id") or r.get("id") or "").strip()
            is_associated = (str(r.get("persona_id") or "").strip() == persona_id)
            if is_associated and r_id and r_id not in seen_r_ids:
                persona_runtimes.append(r)
                seen_r_ids.add(r_id)

        # Choose primary runtime for row details based on activity status priority
        def runtime_priority(rt):
            status = str(rt.get("status") or "").lower()
            if status == "running":
                return 0
            if status in ("active", "bound"):
                return 1
            return 2

        sorted_runtimes = sorted(persona_runtimes, key=runtime_priority)
        runtime = sorted_runtimes[0] if sorted_runtimes else {}

        binding_ids = {
            str(b.get("id") or b.get("binding_id") or "").strip()
            for b in p_bindings
        }
        if binding:
            binding_ids.add(str(binding.get("id") or binding.get("binding_id") or "").strip())
        binding_ids.discard("")

        capital_pool_ids = {str(pool_id or "").strip()}
        capital_pool_ids.discard("")

        runtime_ids = set()
        for rt in persona_runtimes:
            val = str(rt.get("runtime_id") or "").strip()
            if val:
                runtime_ids.add(val)
        if runtime:
            val = str(runtime.get("runtime_id") or "").strip()
            if val:
                runtime_ids.add(val)
        active_incidents = _persona_fleet_active_incidents_for_row(
            incidents=incidents,
            persona_id=persona_id,
            binding_ids=binding_ids,
            capital_pool_ids=capital_pool_ids,
            runtime_ids=runtime_ids,
        )
        telemetry_summaries = []
        for runtime_id in sorted(runtime_ids):
            if runtime_id not in telemetry_cache:
                telemetry_cache[runtime_id] = read_store.get_telemetry_summary(runtime_id)
            summary = telemetry_cache.get(runtime_id)
            if summary:
                telemetry_summaries.append(summary)
        row = _project_persona_fleet_list_row(
            persona=persona,
            league_entry=league_entry,
            binding=binding,
            runtime=runtime,
            active_incidents=active_incidents,
            telemetry_summaries=telemetry_summaries,
            context_metadata=context_metadata,
            context_persona=context_persona,
            snapshot_at=snapshot_at,
            evolution_decisions=evolution_decisions,
        )
        if row is not None:
            if row.get("capital_mode") == "paper":
                paper_ranking = paper_rankings.get(persona_id)
                if paper_ranking:
                    paper_rank = _persona_fleet_optional_int(paper_ranking.get("rank"))
                    paper_score = _management_number(
                        paper_ranking.get("score") or paper_ranking.get("overall_score")
                    )
                    row["league_rank"] = paper_rank
                    row["leagueRank"] = paper_rank
                    row["league_score"] = paper_score
                    row["leagueScore"] = paper_score
                    row["rank"] = {
                        "league_rank": paper_rank,
                        "league_score": paper_score,
                        "basis": "quarterly_ranking",
                        "period": "quarter",
                        "quarter": quarter_window["quarter"],
                    }
            rows.append(row)

    rows = sorted(
        rows,
        key=lambda item: (
            -_as_float(item.get("score")),
            str(item.get("persona_id") or ""),
        ),
    )
    available_personas = len(rows)

    requested_states = _persona_fleet_query_filter(state)
    if requested_states:
        rows = [
            item for item in rows
            if str(item.get("state") or item.get("status") or "").strip().lower() in requested_states
        ]

    requested_health = _persona_fleet_query_filter(health)
    if requested_health:
        rows = [
            item for item in rows
            if str(item.get("health") or item.get("status") or "").strip().lower() in requested_health
        ]

    requested_stages = _persona_fleet_query_filter(deployment_stage)
    if requested_stages:
        rows = [
            item for item in rows
            if str(item.get("deployment_stage") or item.get("mode") or "").strip().lower() in requested_stages
        ]

    requested_markets = {
        token.strip().upper()
        for token in (market_scope.split(",") if market_scope else [])
        if token.strip()
    }
    if requested_markets:
        rows = [
            item for item in rows
            if requested_markets.intersection({str(scope).upper() for scope in item.get("market_scope") or []})
        ]

    search_text = str(q or "").strip().lower()
    if search_text:
        rows = [
            item for item in rows
            if search_text in " ".join(
                [
                    str(item.get("persona_id") or ""),
                    str(item.get("name") or ""),
                    str(item.get("owner") or ""),
                    str(item.get("current_work") or ""),
                    " ".join(str(scope) for scope in item.get("market_scope") or []),
                ]
            ).lower()
        ]

    total_personas = len(rows)
    page_items, next_page_token = _page_slice(rows, page_token, page_size)
    pending_human_gate = [
        item
        for item in league
        if item.get("governance_required")
        and str(item.get("recommendation") or "").strip()
    ]
    capital_totals = _capital_pool_totals(pools)
    execution_boundary = {
        "approved_artifacts_only": True,
        "live_capital_side_effects": False,
        "human_gate_required_for_capital_changes": True,
    }
    summary = {
        "available_personas": available_personas,
        "total_personas": total_personas,
        "canonical_total": canonical_total,
        "filtered_total": total_personas,
        "catalog_default_total": catalog_default_total,
        "returned_personas": len(page_items),
        "critical_personas": len([item for item in rows if item.get("health") == "critical"]),
        "degraded_personas": len([item for item in rows if item.get("health") == "degraded"]),
        "healthy_personas": len([item for item in rows if item.get("health") == "healthy"]),
        "human_needed_personas": len([item for item in rows if item.get("human_needed")]),
        "governance_required_personas": len([item for item in rows if item.get("governance_required")]),
        "by_deployment_stage": _persona_fleet_count_by(rows, "deployment_stage"),
        "by_capital_mode": _persona_fleet_count_by(rows, "capital_mode"),
        "by_lifecycle_state": _persona_fleet_count_by(rows, "state"),
        "by_market_scope": {
            market: sum(1 for item in rows if market in {str(scope) for scope in item.get("market_scope") or []})
            for market in sorted({str(scope) for item in rows for scope in (item.get("market_scope") or [])})
        },
        "by_data_source_state": _persona_fleet_count_by(
            [
                {"state": (item.get("data_source_summary") or {}).get("state")}
                for item in rows
            ],
            "state",
        ),
        "by_research_stage": _persona_fleet_count_by(
            [
                {"stage": (item.get("research_summary") or {}).get("stage")}
                for item in rows
            ],
            "stage",
        ),
        "capital_summary": {
            "pool_count": capital_totals["pool_count"],
            "total_nav": capital_totals["total_nav"],
            "gross_exposure": capital_totals["gross_exposure"],
            "net_exposure": capital_totals["net_exposure"],
        },
        "human_inbox_summary": {
            "pending_count": len(pending_human_gate),
        },
        "execution_boundary": execution_boundary,
    }
    page_info = {
        "next_page_token": next_page_token,
        "total": total_personas,
        "canonical_total": canonical_total,
        "filtered_total": total_personas,
        "catalog_default_total": catalog_default_total,
        "page_size": page_size,
    }
    return {
        "data": {
            "items": page_items,
            "summary": summary,
        },
        "page_info": page_info,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "persona_fleet": _composed_dataset_surface_status(
                    "persona_fleet",
                    rows,
                    snapshot_at=snapshot_at,
                    source="bff_composed_slim_list",
                ),
                "personas": _composed_dataset_surface_status(
                    "personas",
                    personas,
                    snapshot_at=snapshot_at,
                    source="composed_market_persona_defaults",
                ),
                "persona_league": _composed_dataset_surface_status(
                    "persona_league",
                    league,
                    snapshot_at=snapshot_at,
                    source="composed_market_persona_defaults",
                ),
                "capital_pools": _composed_dataset_surface_status(
                    "capital_pools",
                    pools,
                    snapshot_at=snapshot_at,
                    source="composed_market_persona_defaults",
                ),
                "runtime_bindings": _composed_dataset_surface_status(
                    "runtime_bindings",
                    runtimes,
                    snapshot_at=snapshot_at,
                    source="composed_market_persona_defaults",
                ),
                "ooda_control_room_status": _dataset_surface_status("ooda_packets", snapshot_at=snapshot_at),
            },
            "related": {
                "persona_league": {"href": "/bff/management/persona-league"},
                "capital_pools": {"href": "/bff/management/capital-pools"},
                "runtime_bindings": {"href": "/bff/management/runtime-bindings"},
                "human_inbox": {"href": "/bff/management/human-inbox"},
                "ooda_status": {"href": "/bff/v5/control-room"},
            },
        },
    }


# --- _capital_pool_totals ---
def _capital_pool_totals(pools: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "pool_count": len(pools),
        "total_nav": sum(_as_float(pool.get("nav") or pool.get("capital_allocation")) for pool in pools),
        "cash": sum(_as_float(pool.get("cash")) for pool in pools),
        "gross_exposure": sum(_as_float(pool.get("gross_exposure")) for pool in pools),
        "net_exposure": sum(_as_float(pool.get("net_exposure")) for pool in pools),
        "realized_pnl": sum(_as_float(pool.get("realized_pnl")) for pool in pools),
        "unrealized_pnl": sum(_as_float(pool.get("unrealized_pnl")) for pool in pools),
        "var_95": max((_as_float(pool.get("var_95")) for pool in pools), default=0.0),
        "drawdown": max((_as_float(pool.get("drawdown")) for pool in pools), default=0.0),
        "slippage_bps": max((_as_float(pool.get("slippage_bps")) for pool in pools), default=0.0),
        "fill_ratio": min((_as_float(pool.get("fill_ratio"), 1.0) for pool in pools), default=1.0),
        "order_reject_rate": max((_as_float(pool.get("order_reject_rate")) for pool in pools), default=0.0),
    }


# --- _persona_league_payload ---
def _persona_league_payload(
    *,
    snapshot_at: str,
    market_scope: Optional[str] = None,
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = 20,
) -> Dict[str, Any]:
    items = read_store.list_persona_league(market_scope=market_scope, status=status)
    total = len(items)
    page_items, next_page_token = _page_slice(items, page_token, page_size)
    return {
        "data": {
            "items": page_items,
            "summary": {
                "total": total,
                "returned_count": len(page_items),
            },
        },
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta(
            "persona_league",
            "persona_league",
            snapshot_at=snapshot_at,
            total=total,
        ),
    }


# --- _BFF_AUTH_STUB_ENV ---
_BFF_AUTH_STUB_ENV = "PANTHEON_BFF_AUTH_STUB"


# --- _BFF_STUB_LEGACY_BARE_TOKENS_ENV ---
_BFF_STUB_LEGACY_BARE_TOKENS_ENV = "PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS"


# --- _BFF_STUB_CAPABILITY_ROLES ---
_BFF_STUB_CAPABILITY_ROLES = frozenset({"admin", "operator"})


# --- _BFF_VALID_AUTH_MODES ---
_BFF_VALID_AUTH_MODES = frozenset({"strict", "permissive"})


# --- _bool_from_env ---
def _bool_from_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- _bff_auth_mode ---
def _bff_auth_mode() -> str:
    raw = os.getenv("PANTHEON_BFF_AUTH_MODE", "strict").strip().lower() or "strict"
    if raw not in _BFF_VALID_AUTH_MODES:
        return "strict"
    return raw


# --- _bff_auth_stub_enabled ---
def _bff_auth_stub_enabled() -> bool:
    return _bool_from_env(_BFF_AUTH_STUB_ENV) and _bff_auth_mode() != "strict"


# --- _ERROR_CODE_BY_STATUS ---
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


# --- _LEGACY_ERROR_CODE_ALIASES ---
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


# --- _PACK_D_D21_ERROR_BEHAVIOR ---
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


# --- _status_error_code ---
def _status_error_code(status_code: int) -> str:
    return _ERROR_CODE_BY_STATUS.get(status_code, ErrorCode.VALIDATION_FAILED.value)


# --- _canonical_error_code_value ---
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


# --- _pack_d_error_metadata ---
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


# --- _DEV_LOGIN_IDENTITY_DEFS ---
_DEV_LOGIN_IDENTITY_DEFS: Dict[str, Dict[str, Any]] = {
    "viewer": {"roles": ("viewer",), "subject_suffix": "viewer"},
    "operator": {"roles": ("operator",), "subject_suffix": "operator"},
    "approver": {"roles": ("approver",), "subject_suffix": "approver"},
    "risk_owner": {"roles": ("risk_owner",), "subject_suffix": "risk-owner"},
    "operator_a": {"roles": ("operator",), "subject_suffix": "operator-a"},
    "operator_b": {"roles": ("operator",), "subject_suffix": "operator-b"},
}


# --- _resolve_param ---
def _resolve_param(val: Any) -> Any:
    if isinstance(val, FastAPIParam):
        if val.default is ... or type(val.default).__name__ == "PydanticUndefined":
            return None
        return val.default
    return val


# --- _REQUEST_DRY_RUN_CONTEXT ---
_REQUEST_DRY_RUN_CONTEXT: ContextVar[bool] = ContextVar("request_dry_run_context", default=False)


# --- _auth_and_error_helpers ---
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


def _resolve_session_kind(identity: OperatorIdentity) -> str:
    """Return session_kind: cookie | bearer | stub based on how the identity was established."""
    if identity.token_kind == "stub":
        return "stub"
    if identity.token_kind == "cookie":
        return "cookie"
    return "bearer"


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


# --- _deprecation_constants ---
_FOUNDATION_COMMAND_ROUTE = "POST /api/v1/operator/commands"
_FINAL_COMMAND_ROUTE = "POST /bff/v1/commands"
_CANONICAL_ACTIONS_ROUTE = "POST /bff/actions/{type}/{id}/{action}"
_ACTIONS_TO_COMMANDS_SOURCE_ROUTE = "POST /bff/actions/{entityType}/{entityId}/{actionId}"
_ACTIONS_DEPRECATION_SINCE = "2026-05-14"
_ACTIONS_SUNSET_DATE = "2026-06-15"
_ACTIONS_SUNSET_HTTP_DATE = "Mon, 15 Jun 2026 00:00:00 GMT"
_ACTIONS_DEPRECATION_MESSAGE = (
    "/bff/actions/* is deprecated; submit the equivalent command envelope to "
    "/bff/v1/commands."
)
_PATH_DEDUPE_DEPRECATED_SINCE = "2026-05-25T08:40:02Z"
_PATH_DEDUPE_SUNSET_HTTP_DATE = "Mon, 25 May 2026 00:00:00 GMT"



# --- _foundation_audit_for_command_record ---
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


# --- _audit_datetime ---
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


# --- _list_governance_audit_events ---
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


# --- _resolve_final_idempotency_key ---
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


# --- _reject_body_idempotency_key ---
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


# --- _stable_json_hash ---
def _stable_json_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# --- _pm12_resolve_quarterly_recommendation_submit_params ---
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


# --- _validate_quarterly_ranking_recommendation_submit ---
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


# --- _role_and_me_payload_helpers ---
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


# Role -> capability mapping (best-effort). In production, prefer capability snapshots
# supplied by the auth service. This map is intentionally conservative.
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


def _bff_me_correlation_id(x_correlation_id: Optional[str]) -> str:
    return str(x_correlation_id or "").strip() or str(uuid.uuid4())


def _bff_me_error_with_correlation(exc: HTTPException, correlation_id: str) -> HTTPException:
    headers = dict(exc.headers or {})
    headers["X-Correlation-Id"] = correlation_id
    detail = exc.detail
    if isinstance(detail, dict):
        detail = dict(detail)
        detail["correlationId"] = correlation_id
        error = detail.get("error")
        if isinstance(error, dict):
            error = dict(error)
            details = error.get("details")
            if isinstance(details, dict):
                details = dict(details)
                details["correlationId"] = correlation_id
                error["details"] = details
            detail["error"] = error
    return HTTPException(status_code=exc.status_code, detail=detail, headers=headers)


def _env_csv(name: str) -> List[str]:
    return _dedupe_nonblank_strings(_split_claim_string(os.getenv(name, "")))


def _normalize_locale(raw: Any) -> Optional[str]:
    clean = str(raw or "").strip().replace("_", "-")
    if not clean:
        return None
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", clean):
        return None
    parts = clean.split("-")
    normalized: List[str] = []
    for idx, part in enumerate(parts):
        if idx == 0:
            normalized.append(part.lower())
        elif len(part) == 2:
            normalized.append(part.upper())
        elif len(part) == 4:
            normalized.append(part.title())
        else:
            normalized.append(part)
    return "-".join(normalized)


def _preferred_locale_from_accept_language(accept_language: Optional[str]) -> Optional[str]:
    for raw_part in str(accept_language or "").split(","):
        locale_part = raw_part.split(";", 1)[0].strip()
        resolved = _normalize_locale(locale_part)
        if resolved:
            return resolved
    return None


def _resolve_bff_me_locale(
    identity: OperatorIdentity,
    *,
    x_locale: Optional[str],
    accept_language: Optional[str],
) -> Dict[str, Any]:
    claim_locale = _first_nonblank(
        *_identity_claim_strings(identity, ["locale", "preferred_locale", "preferredLanguage"])
    )
    default_locale = (
        _normalize_locale(os.getenv("PANTHEON_BFF_DEFAULT_LOCALE"))
        or _normalize_locale(os.getenv("PANTHEON_LOCALE"))
        or "en-US"
    )
    requested = _normalize_locale(x_locale)
    accepted = _preferred_locale_from_accept_language(accept_language)
    resolved = requested or accepted or _normalize_locale(claim_locale) or default_locale
    return {
        "resolved": resolved,
        "requested": requested,
        "accept_language": accepted,
        "default": default_locale,
        "timezone": os.getenv("PANTHEON_TIMEZONE", "UTC"),
    }


def _flag_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    clean = str(value or "").strip()
    lowered = clean.lower()
    if lowered in {"1", "true", "yes", "on", "enabled"}:
        return True
    if lowered in {"0", "false", "no", "off", "disabled"}:
        return False
    return clean


def _parse_feature_flags(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(key): _flag_value(value) for key, value in raw.items() if str(key).strip()}
    if isinstance(raw, (list, tuple, set)):
        return {str(item).strip(): True for item in raw if str(item).strip()}
    clean = str(raw or "").strip()
    if not clean:
        return {}
    if clean.startswith("{"):
        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return _parse_feature_flags(parsed)
    flags: Dict[str, Any] = {}
    for part in clean.split(","):
        item = part.strip()
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            flags[key.strip()] = _flag_value(value)
        else:
            flags[item] = True
    return {key: value for key, value in flags.items() if key}


def _bff_me_feature_flags(identity: OperatorIdentity) -> Dict[str, Any]:
    claims = identity.claims if isinstance(identity.claims, dict) else {}
    flags = {
        "executePlansBff": True,
        "sessionAuthMe": True,
    }
    flags.update(_parse_feature_flags(_claim_path_value(claims, "feature_flags")))
    flags.update(_parse_feature_flags(_claim_path_value(claims, "features")))
    flags.update(_parse_feature_flags(os.getenv("PANTHEON_BFF_FEATURE_FLAGS")))
    return flags


def _epoch_claim_seconds(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch_to_iso(value: Any) -> Optional[str]:
    epoch = _epoch_claim_seconds(value)
    if epoch is None:
        clean = str(value or "").strip()
        return clean or None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _bff_me_session_payload(identity: OperatorIdentity, *, checked_at: str) -> Dict[str, Any]:
    claims = identity.claims if isinstance(identity.claims, dict) else {}
    exp = _epoch_claim_seconds(claims.get("exp"))
    now = time.time()
    freshness_seconds = max(0, int(exp - now)) if exp is not None else None
    session_id = _sem_session_id(identity)
    return {
        "id": session_id,
        "authenticated": True,
        "auth_mode": identity.token_kind,
        "session_kind": _resolve_session_kind(identity),
        "fresh": exp is None or exp > now,
        "freshness_seconds_remaining": freshness_seconds,
        "issued_at": _epoch_to_iso(claims.get("iat")),
        "expires_at": _epoch_to_iso(claims.get("exp")),
        "mfa_verified": identity.mfa_verified,
        "checked_at": checked_at,
    }


def _bff_me_environment_payload() -> Dict[str, Any]:
    scope = _foundation_environment_scope()
    stub_auth = _bff_auth_stub_enabled()
    auth_mode = "stub" if stub_auth else _bff_auth_mode()
    return {
        "name": scope.name.value,
        "deployment_stage": os.getenv("PANTHEON_DEPLOYMENT_STAGE", scope.name.value),
        "region": scope.region,
        "timezone": scope.timezone,
        "auth_mode": auth_mode,
        "strict_auth": not stub_auth and auth_mode == "strict",
    }


def _bff_me_user_payload(identity: OperatorIdentity) -> Dict[str, Any]:
    claims = identity.claims if isinstance(identity.claims, dict) else {}
    claim_caps = _identity_claim_strings(
        identity,
        ["capabilities", "permissions", "scp", "scope"],
    )
    capabilities = _dedupe_nonblank_strings([*claim_caps, *_capabilities_for_identity(identity)])
    display_name = _first_nonblank(
        claims.get("name"),
        claims.get("preferred_username"),
        claims.get("email"),
        identity.operator_id,
    )
    return {
        "id": identity.operator_id,
        "operator_id": identity.operator_id,
        "display_name": display_name,
        "roles": identity.roles,
        "capabilities": capabilities,
        "mfa_verified": identity.mfa_verified,
    }


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


# --- _read_surface_and_page_helpers ---
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

_KILL_SWITCH_STATUS_MAP = {
    "armed": "armed",
    "off": "armed",
    "normal": "armed",
    "triggered": "triggered",
    "guarded": "triggered",
    "risk_off": "triggered",
    "cooling_down": "cooling_down",
    "cooldown": "cooling_down",
    "paused": "cooling_down",
}

_ACTION_DRAWER_PRIMARY_ALLOWED_ACTIONS = {
    "canPause": True,
    "canRiskOff": True,
    "canLiquidateAll": False,
    "canHardRollback": False,
    "canIssueSafeMode": True,
}


def _incident_home_severity(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _INCIDENT_SEVERITY_MAP.get(str(value).strip().lower(), str(value))


def _project_incident_home_item(incident: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "incident_id": incident.get("incident_id"),
        "title": incident.get("title"),
        "severity": _incident_home_severity(incident.get("severity")),
        "status": incident.get("status"),
        "artifact_id": incident.get("artifact_id"),
        "opened_at": incident.get("opened_at") or incident.get("created_at"),
        "resolved_at": incident.get("resolved_at"),
    }


def _project_incident_detail_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "incident_id": incident.get("incident_id"),
        "title": incident.get("title"),
        "severity": _incident_home_severity(incident.get("severity")),
        "status": incident.get("status"),
        "artifact_id": incident.get("artifact_id"),
        "artifact_version": incident.get("artifact_version"),
        "runtime_id": incident.get("runtime_id"),
        "trace_id": incident.get("trace_id"),
        "opened_at": incident.get("opened_at") or incident.get("created_at"),
    }


def _project_affected_binding(
    binding: Dict[str, Any],
    incident: Dict[str, Any],
    runtime_binding: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    raw_stage = (
        incident.get("deployment_stage")
        or binding.get("stage")
        or binding.get("deployment_stage")
        or (runtime_binding or {}).get("deployment_stage")
        or binding.get("allowed_deployment_scope")
    )
    stage = str(raw_stage or "").strip().lower()
    if stage not in {"paper", "live"}:
        stage = "paper"

    return {
        "binding_id": binding.get("id") or binding.get("binding_id"),
        "persona_id": binding.get("persona_id"),
        "capital_pool_id": binding.get("capital_pool_id"),
        "stage": stage,
        "binding_status": binding.get("binding_status") or binding.get("status"),
    }


def _project_affected_bindings(
    incident: Dict[str, Any],
    runtime_binding: Optional[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], bool]:
    candidate_ids: List[str] = []
    for value in [
        incident.get("persona_capital_binding_id"),
        (runtime_binding or {}).get("persona_capital_binding_id"),
    ]:
        if value in (None, ""):
            continue
        string_value = str(value)
        if string_value not in candidate_ids:
            candidate_ids.append(string_value)

    affected_bindings: List[Dict[str, Any]] = []
    for binding_id in candidate_ids:
        binding = read_store.get_binding(binding_id)
        if not binding:
            continue
        affected_bindings.append(
            _project_affected_binding(binding, incident, runtime_binding)
        )

    return affected_bindings, bool(candidate_ids)


def _default_incident_allowed_actions() -> Dict[str, bool]:
    return {
        "canPause": False,
        "canRiskOff": False,
        "canLiquidateAll": False,
        "canHardRollback": False,
        "canIssueSafeMode": False,
        "canOpenActionDrawer": False,
    }


def _derive_incident_allowed_actions(
    identity: OperatorIdentity,
    incident: Dict[str, Any],
) -> Dict[str, bool]:
    actions = _default_incident_allowed_actions()
    incident_status = str(incident.get("status") or "").lower()
    runtime_id = incident.get("runtime_id")
    if incident_status not in {"open", "in_progress"} or not runtime_id:
        return actions

    if not {"operator", "admin"}.intersection(identity.roles):
        return actions

    actions["canPause"] = True
    actions["canRiskOff"] = True
    actions["canIssueSafeMode"] = True
    actions["canOpenActionDrawer"] = True
    return actions


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


_RUNTIME_STATE_SORT_FIELDS = {"last_updated_at", "runtime_id", "deployment_stage", "status"}


# --- _aggregate_group_surface ---
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


# --- _management_number_helpers ---
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


_TRADING_PULSE_DRIFT_BREACH_STATUSES = {"breached", "blocked", "critical", "fail", "failed"}
_TRADING_PULSE_DRIFT_WATCH_STATUSES = {"degraded", "warn", "warning", "watch"}


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



# --- _management_evidence_public_item ---
def _management_evidence_public_item(item: Dict[str, Any]) -> Dict[str, Any]:
    ref_id = str(item.get("ref_id") or item.get("id") or "").strip()
    display_label = item.get("display_label") or ref_id
    if item.get("redacted"):
        required_capability = item.get("required_capability")
        return {
            "id": ref_id,
            "ref_id": ref_id,
            "display_label": display_label,
            "kind": item.get("kind"),
            "required_capability": required_capability,
            "reason": item.get("reason"),
            "redacted": True,
        }

    source_document = item.get("source_document") if isinstance(item.get("source_document"), dict) else {}
    linked_summary = (
        item.get("linked_object_summary")
        if isinstance(item.get("linked_object_summary"), dict)
        else {}
    )
    resolved_link = item.get("resolved_link") if isinstance(item.get("resolved_link"), dict) else {}
    credibility = item.get("credibility") if isinstance(item.get("credibility"), dict) else {}
    source_type = source_document.get("source_type") or item.get("source_type") or item.get("sourceType")
    source_ref = source_document.get("source_ref") or item.get("source_ref") or item.get("sourceRef")
    captured_at = source_document.get("captured_at") or item.get("captured_at") or item.get("capturedAt")
    link_type = item.get("link_type")
    route_href = item.get("route_href") or (f"/knowledge/evidence/{ref_id}" if ref_id else None)
    title = source_document.get("title") or item.get("title") or display_label
    public_item = {
        "id": ref_id,
        "ref_id": ref_id,
        "title": title,
        "display_label": display_label,
        "source_type": source_type,
        "source_ref": source_ref,
        "captured_at": captured_at,
        "link_type": link_type,
        "credibility": json.loads(json.dumps(credibility)),
        "linked_object_summary": json.loads(json.dumps(linked_summary)),
        "resolved_link": json.loads(json.dumps(resolved_link)),
        "route_href": route_href,
        "management_href": f"/management/evidence?ref_id={ref_id}" if ref_id else None,
        "redacted": False,
    }
    artifact_manifest = item.get("artifact_manifest")
    if isinstance(artifact_manifest, dict):
        cloned_manifest = _management_json_clone(artifact_manifest)
        public_item["artifact_manifest"] = cloned_manifest
    criteria = item.get("criteria")
    if isinstance(criteria, dict):
        public_item["criteria"] = _management_json_clone(criteria)
    operator_remediation = item.get("operator_remediation")
    if isinstance(operator_remediation, dict):
        public_item["operator_remediation"] = _management_json_clone(operator_remediation)
    release_gate_summary = item.get("release_gate_summary")
    if isinstance(release_gate_summary, dict):
        public_item["release_gate_summary"] = _management_json_clone(release_gate_summary)
    if "overall" in item:
        public_item["overall"] = item.get("overall")
    return public_item


# --- _snapshot_meta ---
def _snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
    }
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness
    return meta


# --- _surface_degradation_reason ---
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


# --- _project_final_command_response ---
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


# --- _deprecated_bff_path_response ---
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


# --- _check_read_surface_state ---
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


# --- _request_dry_run_requested ---
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


# --- _ppl_alloc_009_paper_environment_guard ---
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


# --- _ppl_alloc_009_paper_capital_context ---
def _ppl_alloc_009_paper_capital_context(
    *,
    persona_id: str,
    ranking_item: Dict[str, Any],
) -> Dict[str, Any]:
    binding_ids = {
        str(value or "").strip()
        for value in ranking_item.get("binding_ids") or []
        if str(value or "").strip()
    }
    bindings = [
        binding
        for binding in read_store.list_bindings(
            persona_id=persona_id,
            role="paper_owner",
        )
        if str(binding.get("status") or binding.get("validity") or "").strip().lower()
        in {"active", "ready", "bound"}
        and str(binding.get("allowed_deployment_scope") or "").strip().lower()
        == "paper"
        and not str(binding.get("capital_sleeve_id") or "").strip()
        and (
            not binding_ids
            or str(binding.get("binding_id") or binding.get("id") or "").strip()
            in binding_ids
        )
    ]
    if len(bindings) != 1:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Paper allocation binding is not authoritative",
            (
                "The ranked Persona must resolve to exactly one active "
                "paper_owner binding with no capital sleeve."
            ),
            precondition_failed="paper_owner_binding",
        )
    binding = bindings[0]
    binding_id = str(binding.get("binding_id") or binding.get("id") or "").strip()
    pool_id = str(binding.get("capital_pool_id") or "").strip()
    pool = read_store.get_capital_pool(pool_id)
    metadata = (
        pool.get("metadata")
        if isinstance(pool, dict) and isinstance(pool.get("metadata"), dict)
        else {}
    )
    if (
        not isinstance(pool, dict)
        or str(pool.get("status") or "").strip().lower() != "active"
        or metadata.get("internal") is not True
        or str(metadata.get("execution_context") or "").strip().lower() != "paper"
        or str(metadata.get("persona_id") or "").strip() != persona_id
    ):
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Paper allocation pool is not authoritative",
            (
                "The paper_owner binding must resolve to the active internal "
                "paper pool provisioned for the same Persona."
            ),
            precondition_failed="paper_capital_pool",
        )

    allocations = read_store.list_capital_allocations(
        capital_pool_id=pool_id,
        persona_id=persona_id,
    )
    if len(allocations) > 1:
        raise _bff_error(
            409,
            ErrorCode.RESOURCE_CONFLICT,
            "Paper allocation identity is ambiguous",
            "More than one authoritative Capital allocation matched the paper identity.",
            precondition_failed="paper_allocation_identity",
        )
    current_weight = 0.0
    if allocations:
        allocation = allocations[0]
        if (
            str(allocation.get("capital_scope") or "").strip().lower()
            != "paper_ledger"
            or str(allocation.get("binding_id") or "").strip() != binding_id
            or str(allocation.get("capital_sleeve_id") or "").strip()
        ):
            raise _bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Paper allocation identity is inconsistent",
                "The existing Capital allocation does not match the governed paper binding.",
                precondition_failed="paper_allocation_identity",
            )
        try:
            current_weight = float(allocation.get("current_weight") or 0.0)
        except (TypeError, ValueError) as exc:
            raise _bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Paper allocation baseline is invalid",
                "Capital returned a non-numeric paper allocation weight.",
                precondition_failed="paper_allocation_baseline",
            ) from exc

    return {
        "capital_pool_id": pool_id,
        "binding_id": binding_id,
        "current_weight": current_weight,
    }


# --- _strategy_and_persona_overlay_helpers ---
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
_STRATEGY_BFF_OVERLAY: Dict[str, Dict[str, Any]] = {}
_PERSONA_BFF_OVERLAY: Dict[str, Dict[str, Any]] = {}
_PERSONA_PROVISIONING_STORE = None
_PERSONA_PROVISIONING_STORE_LOCK = threading.Lock()
_PERSONA_PROVISIONING_RECONCILER_TASK: Optional[asyncio.Task[Any]] = None
_PERSONA_FIRST_EVALUATION_WORKFLOW_ID = "pantheon.persona.first-evaluation"



# --- _normalize_lifecycle_state ---
def _normalize_lifecycle_state(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _STRATEGY_BFF_LIFECYCLE_MAP.get(text, "draft")


# --- _normalize_risk_level ---
def _normalize_risk_level(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _STRATEGY_BFF_RISK_MAP.get(text, "medium")


# --- _deployment_url ---
def _deployment_url(path: str) -> str:
    base = os.getenv("PANTHEON_DEPLOYMENT_API_URL", "").strip().rstrip("/")
    if not base:
        base = "http://deployment:8095"
    return f"{base}{path}"


# --- _management_record_id ---
def _management_record_id(record: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


# --- _management_as_float ---
def _management_as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --- _management_first_non_empty ---
def _management_first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


# --- _management_nested_value ---
def _management_nested_value(record: Dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


# --- _management_first_float ---
def _management_first_float(record: Dict[str, Any], *paths: str) -> Optional[float]:
    for path in paths:
        value = _management_nested_value(record, path)
        number = _management_as_float(value)
        if number is not None:
            return number
    return None


# --- _management_telemetry_rollup ---
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


# --- _sort_records_latest_first ---
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


# --- _persona_fleet_runtime_matches ---
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


# --- _human_inbox_priority_helpers ---
_HUMAN_INBOX_OPEN_SENTINEL_STATUSES = {"pending", "open", "active", "escalated"}
_HUMAN_INBOX_PRIORITY_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "unknown": 0,
}
_HIQ_BACKLOG_DEFAULT_KINDS = {"hiq_sentinel", "risk_breach"}
_HIQ_BACKLOG_DEFAULT_STATUSES = {"pending", "escalated", "open", "active", "in_progress"}


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




# --- _human_inbox_sanitize_promotion_snapshot ---
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


# --- _human_inbox_decision_recommendation_id ---
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


# --- _human_inbox_decision_projection_from_record ---
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


# --- _submitted_promotion_review_record_from_command ---
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


# --- _submitted_promotion_review_records ---
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


# --- _persona_first_evaluation_readback_timeout_seconds ---
def _persona_first_evaluation_readback_timeout_seconds() -> float:
    raw = os.getenv(
        "PANTHEON_PERSONA_FIRST_EVALUATION_READBACK_TIMEOUT_SECONDS",
        "15",
    ).strip()
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 15.0


# --- _openclaw_agent_reconcile_request ---
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


# --- _persona_provisioning_metadata ---
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


# --- _pm12_constants ---
_PM12_LEAGUE_SCORE_WEIGHTS = {
    "pnl": 0.35,
    "risk": 0.25,
    "execution": 0.25,
    "activity": 0.15,
}
_PM12_LEAGUE_RANKING_CRITERIA = {
    "overall": ("overall_score", "Overall"),
    "pnl": ("pnl_score", "PnL"),
    "risk": ("risk_score", "Risk"),
    "execution": ("execution_score", "Execution"),
    "activity": ("activity_score", "Activity"),
}
_PM12_LEAGUE_MOVER_DIRECTIONS = {"all", "up", "down", "flat", "new"}
_PM12_LEAGUE_FORMULA_VERSION = "pm12-default-v1"
_PM12_QUARTERLY_FORMULA_DOC_REF = (
    "docs/04/pantheon_bff_api_gap_2026-05-23/"
    "BFF_API_GAP_final_integration_spec.md#b34-pm-12-composition-sources"
)
_PM12_QUARTERLY_FORMULA_GOVERNANCE_REF_ID = (
    "pm12-quarterly-ranking-formula-v1-governance"
)
_PM12_QUARTERLY_FORMULA_EFFECTIVE_AT = "2026-05-23T00:00:00Z"
_PM12_QUARTER_PATTERN = re.compile(r"^(?P<year>\d{4})-Q(?P<quarter>[1-4])$", re.IGNORECASE)
_PM12_HEATMAP_BUCKET_DELTAS = {
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(days=7),
}
_PM12_TELEMETRY_HISTORY_KEYS = (
    "history",
    "samples",
    "series",
    "time_series",
    "timeSeries",
    "buckets",
    "time_buckets",
    "timeBuckets",
)
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
_PM12_LEAGUE_TIER_DEFINITIONS = [
    {
        "id": "tier-1",
        "tier_id": "tier-1",
        "label": "League Leader",
        "min_score": 85.0,
        "max_score": 100.0,
        "governance_posture": "promotion_candidate",
    },
    {
        "id": "tier-2",
        "tier_id": "tier-2",
        "label": "Production Candidate",
        "min_score": 70.0,
        "max_score": 84.999,
        "governance_posture": "maintain_or_expand_paper",
    },
    {
        "id": "tier-3",
        "tier_id": "tier-3",
        "label": "Observation",
        "min_score": 55.0,
        "max_score": 69.999,
        "governance_posture": "continue_observation",
    },
    {
        "id": "tier-4",
        "tier_id": "tier-4",
        "label": "Incubation",
        "min_score": 0.0,
        "max_score": 54.999,
        "governance_posture": "research_only",
    },
]



# --- _pm12_status_counts ---
def _pm12_status_counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        status = str(
            item.get("status")
            or item.get("state")
            or item.get("lifecycle_state")
            or "unknown"
        ).strip().lower() or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


# --- _pm12_latest_timestamp ---
def _pm12_latest_timestamp(items: List[Dict[str, Any]], keys: tuple[str, ...]) -> Optional[str]:
    values: List[str] = []
    for item in items:
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                values.append(str(value))
                break
    return max(values) if values else None


# --- _pm12_compact_ids ---
def _pm12_compact_ids(items: List[Dict[str, Any]], keys: tuple[str, ...]) -> List[str]:
    values: List[str] = []
    seen = set()
    for item in items:
        for key in keys:
            value = item.get(key)
            if value in (None, ""):
                continue
            text = str(value)
            if text not in seen:
                values.append(text)
                seen.add(text)
            break
    return values


# --- _pm12_memory_items_for_persona ---
def _pm12_memory_items_for_persona(persona_id: str) -> List[Dict[str, Any]]:
    fetcher = getattr(read_store, "list_memory_updates_for_persona", None)
    if not callable(fetcher):
        return []
    items = fetcher(persona_id) or []
    return [dict(item) for item in items if isinstance(item, dict)]


# --- _pm12_record_freshness_issue ---
def _pm12_record_freshness_issue(record: Dict[str, Any]) -> Optional[str]:
    sources = [record]
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        sources.append(metadata)
    for source in sources:
        if source.get("stale") is True or source.get("is_stale") is True:
            return "stale"
        if source.get("degraded") is True:
            return "degraded"
        for key in (
            "freshness_status",
            "heartbeat_status",
            "data_status",
            "source_status",
            "state",
            "status",
            "connectivity_status",
        ):
            status = str(source.get(key) or "").strip().lower()
            if status in {"stale", "expired", "lagging", "unavailable"}:
                return "stale"
            if status in {
                "degraded",
                "partial",
                "invalid",
                "disconnected",
                "offline",
                "failed",
                "error",
            }:
                return "degraded"
        for key in ("staleness", "freshness"):
            marker = source.get(key)
            if isinstance(marker, str):
                marker_text = marker.strip().lower()
                if marker_text in {"stale", "expired", "lagging"}:
                    return "stale"
                if marker_text in {"degraded", "partial", "invalid"}:
                    return "degraded"
            if not isinstance(marker, dict):
                continue
            marker_status = str(
                marker.get("status")
                or marker.get("state")
                or marker.get("freshness_status")
                or ""
            ).strip().lower()
            marker_reason = str(marker.get("reason") or "").strip().lower()
            if marker_status in {"stale", "expired", "lagging"} or "stale" in marker_reason:
                return "stale"
            if marker_status in {"degraded", "partial", "invalid"}:
                return "degraded"
            age = _management_number(marker.get("age_seconds"))
            threshold = _management_number(marker.get("threshold_seconds"))
            if age is not None and threshold is not None and age > threshold:
                return "stale"
    return None


# --- _pm12_runtime_identity_aliases ---
def _pm12_runtime_identity_aliases(runtime: Dict[str, Any]) -> Set[str]:
    return {
        str(value or "").strip()
        for value in (
            runtime.get("id"),
            runtime.get("runtime_id"),
            runtime.get("runtime_binding_id"),
            runtime.get("binding_id"),
        )
        if str(value or "").strip()
    }


# --- _pm12_session_runtime_aliases ---
def _pm12_session_runtime_aliases(session: Dict[str, Any]) -> Set[str]:
    return {
        str(value or "").strip()
        for value in (
            session.get("runtime_id"),
            session.get("runtime_binding_id"),
            session.get("execution_runtime_id"),
        )
        if str(value or "").strip()
    }


# --- _pm12_runtime_deployment_mode ---
def _pm12_runtime_deployment_mode(runtime: Dict[str, Any]) -> str:
    mode = str(
        runtime.get("deployment_mode")
        or runtime.get("deployment_stage")
        or runtime.get("runtime_kind")
        or ""
    ).strip().lower()
    return {
        "paper_running": "paper",
        "canary_running": "canary",
        "live_running": "live",
    }.get(mode, mode)


# --- _pm12_authoritative_paper_monitoring_sessions ---
def _pm12_authoritative_paper_monitoring_sessions(
    runtime_aliases: Set[str],
    *,
    authoritative_sessions: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    sessions: List[Dict[str, Any]] = []
    for raw_session in (
        authoritative_sessions
        if authoritative_sessions is not None
        else (
            read_store.list_authoritative_paper_runtime_monitoring_sessions()
            or []
        )
    ):
        if not isinstance(raw_session, dict):
            continue
        if (
            str(raw_session.get("session_type") or "").strip().lower()
            != "paper_runtime_monitoring"
        ):
            continue
        if (
            str(raw_session.get("deployment_stage") or "").strip().lower()
            != "paper"
        ):
            continue
        if not _pm12_session_runtime_aliases(raw_session).intersection(
            runtime_aliases
        ):
            continue
        session = dict(raw_session)
        session["session_authority"] = "runtime_manager.paper_fleet_monitoring"
        session["source_dataset"] = "paper_runtime_monitoring_sessions"
        sessions.append(session)
    return sessions


# --- _pm12_runtime_session_resolution ---
def _pm12_runtime_session_resolution(
    persona_id: str,
    runtime: Dict[str, Any],
) -> tuple[Optional[Dict[str, Any]], str]:
    if not runtime:
        return None, "missing_runtime"
    runtime_aliases = _pm12_runtime_identity_aliases(runtime)
    if _pm12_runtime_deployment_mode(runtime) == "paper":
        # Runtime Manager's paper-fleet monitoring store is the lifecycle owner
        # for paper runtimes. A local/persona session may be useful for display,
        # but it cannot prove that the canonical paper worker joined this exact
        # RuntimeBinding.
        authoritative_sessions = (
            read_store.list_authoritative_paper_runtime_monitoring_sessions() or []
        )
        sessions = _pm12_authoritative_paper_monitoring_sessions(
            runtime_aliases,
            authoritative_sessions=authoritative_sessions,
        )
        if not sessions and not authoritative_sessions:
            sessions = []
            for raw_session in read_store.get_sessions_for_persona(persona_id) or []:
                if not isinstance(raw_session, dict):
                    continue
                session = dict(raw_session)
                session.setdefault("session_authority", "persona_session_store")
                sessions.append(session)
        elif not sessions and any(
            isinstance(session, dict)
            and _pm12_session_runtime_aliases(session)
            for session in authoritative_sessions
        ):
            return None, "identity_mismatch"
    else:
        sessions = []
        for raw_session in read_store.get_sessions_for_persona(persona_id) or []:
            if not isinstance(raw_session, dict):
                continue
            session = dict(raw_session)
            session.setdefault("session_authority", "persona_session_store")
            sessions.append(session)
    matching = [
        session
        for session in sessions
        if _pm12_session_runtime_aliases(session).intersection(runtime_aliases)
    ]
    if not matching:
        if any(_pm12_session_runtime_aliases(session) for session in sessions):
            return None, "identity_mismatch"
        return None, "missing"

    ended = [
        session
        for session in matching
        if session.get("ended_at") not in (None, "")
        or str(session.get("status") or session.get("state") or "").strip().lower()
        in {"ended", "closed", "completed", "stopped", "terminated", "expired"}
    ]
    candidates = [session for session in matching if session not in ended]
    stale = [session for session in candidates if _pm12_record_freshness_issue(session)]
    candidates = [session for session in candidates if session not in stale]
    active = [
        session
        for session in candidates
        if str(session.get("status") or session.get("state") or "").strip().lower()
        in {"active", "running"}
        and session.get("active") is not False
    ]
    if len(active) == 1:
        return active[0], "active"
    if len(active) > 1:
        return None, "identity_mismatch"
    if stale:
        return None, "stale"
    if ended:
        return None, "ended"
    return None, "inactive"


# --- _pm12_persona_session_summary ---
def _pm12_persona_session_summary(persona_id: str) -> Dict[str, Any]:
    sessions = [
        dict(session)
        for session in (read_store.get_sessions_for_persona(persona_id) or [])
        if isinstance(session, dict)
    ]
    for session in sessions:
        session.setdefault("session_authority", "persona_session_store")
    persona_binding_ids = set(
        _pm12_compact_ids(
            read_store.get_bindings_for_persona(persona_id) or [],
            ("persona_capital_binding_id", "binding_id", "id"),
        )
    )
    paper_runtimes = [
        runtime
        for runtime in (read_store.list_runtime_bindings() or [])
        if _pm12_runtime_deployment_mode(runtime) == "paper"
        and (
            str(runtime.get("persona_id") or "").strip() == persona_id
            or str(runtime.get("persona_capital_binding_id") or "").strip()
            in persona_binding_ids
        )
    ]
    paper_runtime_aliases = {
        alias
        for runtime in paper_runtimes
        for alias in _pm12_runtime_identity_aliases(runtime)
    }
    if paper_runtime_aliases:
        authoritative = _pm12_authoritative_paper_monitoring_sessions(paper_runtime_aliases)
        if authoritative:
            sessions = [
                session
                for session in sessions
                if not _pm12_session_runtime_aliases(session).intersection(
                    paper_runtime_aliases
                )
            ]
            sessions.extend(authoritative)
    active = [
        session
        for session in sessions
        if str(
            session.get("status") or session.get("state") or ""
        ).strip().lower()
        in {"active", "running"}
        and session.get("ended_at") in (None, "")
        and session.get("active") is not False
        and _pm12_record_freshness_issue(session) is None
    ]
    return {
        "total": len(sessions),
        "active": len(active),
        "last_heartbeat_at": _pm12_latest_timestamp(
            sessions,
            ("last_heartbeat_at", "updated_at", "started_at"),
        ),
        "runtime_ids": _pm12_compact_ids(sessions, ("runtime_id",)),
        "runtime_binding_ids": _pm12_compact_ids(sessions, ("runtime_binding_id",)),
        "pool_scopes": _pm12_compact_ids(sessions, ("pool_scope", "capital_pool_id")),
        "status_counts": _pm12_status_counts(sessions),
    }


# --- _pm12_persona_memory_summary ---
def _pm12_persona_memory_summary(persona_id: str) -> Dict[str, Any]:
    memory = _pm12_memory_items_for_persona(persona_id)
    return {
        "total": len(memory),
        "latest_at": _pm12_latest_timestamp(memory, ("updated_at", "created_at", "recorded_at")),
        "status_counts": _pm12_status_counts(memory),
    }


# --- _pm12_clamp_score ---
def _pm12_clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 6)


# --- _pm12_persona_runtime_ids ---
def _pm12_persona_runtime_ids(
    row: Dict[str, Any],
    *,
    telemetry_cache: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
) -> List[str]:
    if "runtime_resolution" in row:
        if str(row.get("runtime_resolution") or "") != "active":
            return []
        authoritative_ids = [
            *(row.get("runtime_ids") if isinstance(row.get("runtime_ids"), list) else []),
            row.get("runtime_id"),
        ]
        return list(dict.fromkeys(
            str(value or "").strip()
            for value in authoritative_ids
            if str(value or "").strip()
        ))
    bindings = row.get("binding_summary") if isinstance(row.get("binding_summary"), dict) else {}
    sessions = row.get("session_summary") if isinstance(row.get("session_summary"), dict) else {}
    if not sessions:
        sessions = row.get("sessions") if isinstance(row.get("sessions"), dict) else {}
    raw_ids: List[Any] = []
    for source in (bindings, sessions):
        candidate_ids = source.get("runtime_ids") or source.get("runtimeIds") or []
        if isinstance(candidate_ids, list):
            raw_ids.extend(candidate_ids)
    values: List[str] = []
    seen = set()
    for raw_id in raw_ids:
        runtime_id = str(raw_id or "").strip()
        if runtime_id and runtime_id not in seen:
            values.append(runtime_id)
            seen.add(runtime_id)
    if values:
        return values
    # Older fixtures (and a small number of legacy sessions) used the session
    # binding reference as the telemetry key.  Keep that path only when the
    # exact reference has telemetry.  Stale rb-* session aliases therefore do
    # not compete with an authoritative owned execution runtime_id.
    session_binding_ids = (
        sessions.get("runtime_binding_ids")
        or sessions.get("runtimeBindingIds")
        or []
    )
    for raw_id in session_binding_ids if isinstance(session_binding_ids, list) else []:
        runtime_id = str(raw_id or "").strip()
        if runtime_id and runtime_id not in seen:
            if telemetry_cache is not None and runtime_id in telemetry_cache:
                telemetry = telemetry_cache[runtime_id]
            else:
                telemetry = read_store.get_telemetry_summary(runtime_id)
                if telemetry_cache is not None:
                    telemetry_cache[runtime_id] = telemetry
            if isinstance(telemetry, dict):
                values.append(runtime_id)
                seen.add(runtime_id)
    return values


# --- _pm12_telemetry_record_timestamp ---
def _pm12_telemetry_record_timestamp(record: Dict[str, Any]) -> Optional[datetime]:
    for key in (
        "collected_at",
        "collectedAt",
        "bucket_start",
        "bucketStart",
        "timestamp",
        "updated_at",
        "updatedAt",
        "created_at",
        "createdAt",
    ):
        parsed = _audit_datetime(record.get(key))
        if parsed is not None:
            return parsed
    return None


# --- _pm12_finite_number ---
def _pm12_finite_number(value: Any) -> Optional[float]:
    parsed = _management_number(value)
    if parsed is None or not math.isfinite(parsed):
        return None
    return parsed


# --- _pm12_telemetry_record_resolution ---
def _pm12_telemetry_record_resolution(
    record: Dict[str, Any],
    expected_runtime_id: str,
) -> str:
    declared_runtime_id = str(
        record.get("runtime_id")
        or record.get("runtimeId")
        or record.get("execution_runtime_id")
        or ""
    ).strip()
    if declared_runtime_id and declared_runtime_id != expected_runtime_id:
        return "identity_mismatch"
    freshness_issue = _pm12_record_freshness_issue(record)
    if freshness_issue is not None:
        return freshness_issue
    return "fresh"


# --- _pm12_telemetry_metrics_from_records ---
def _pm12_telemetry_metrics_from_records(
    runtime_ids: List[str],
    telemetry: List[Dict[str, Any]],
) -> Dict[str, Any]:
    telemetry = sorted(
        telemetry,
        key=lambda item: _pm12_telemetry_record_timestamp(item) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    pnl_values = [
        value
        for value in (
            _pm12_finite_number(
                _management_first_float(item, "pnl", "summary.total_pnl", "summary.pnl")
            )
            for item in telemetry
        )
        if value is not None
    ]
    drawdown_values = [
        value
        for value in (
            _pm12_finite_number(
                _management_first_float(
                    item,
                    "drawdown",
                    "max_drawdown",
                    "summary.max_drawdown",
                )
            )
            for item in telemetry
        )
        if value is not None
    ]
    fill_rate_values = [
        value
        for value in (
            _pm12_finite_number(
                _management_first_float(item, "fill_rate", "summary.fill_rate")
            )
            for item in telemetry
        )
        if value is not None
    ]
    slippage_values = [
        value
        for value in (
            _pm12_finite_number(
                _management_first_float(
                    item,
                    "avg_slippage_bps",
                    "summary.avg_slippage_bps",
                    "summary.slippage_bps",
                )
            )
            for item in telemetry
        )
        if value is not None
    ]
    trade_values = [
        value
        for value in (
            _pm12_finite_number(
                _management_first_float(item, "total_trades", "summary.total_trades")
            )
            for item in telemetry
        )
        if value is not None
    ]
    latest_timestamp = _pm12_telemetry_record_timestamp(telemetry[0]) if telemetry else None
    latest_timestamp_iso = _pm12_iso_z(latest_timestamp) if latest_timestamp else None
    telemetry_evidence_refs: List[Dict[str, Any]] = []
    for runtime_id in runtime_ids:
        runtime_records = [
            record
            for record in telemetry
            if str(record.get("runtime_id") or "").strip() == runtime_id
        ]
        if not runtime_records:
            continue
        observed_at = _pm12_latest_timestamp(
            runtime_records,
            (
                "collected_at",
                "collectedAt",
                "bucket_start",
                "bucketStart",
                "timestamp",
                "updated_at",
                "updatedAt",
                "created_at",
                "createdAt",
            ),
        )
        telemetry_evidence_refs.append({
            "ref_id": f"telemetry-summary:{runtime_id}",
            "source_type": "telemetry_summary",
            "runtime_id": runtime_id,
            "observed_at": observed_at,
        })
    return {
        "runtime_ids": runtime_ids,
        "runtime_count": len(runtime_ids),
        "telemetry_coverage_count": len(telemetry),
        "pnl": round(sum(pnl_values), 6) if pnl_values else None,
        "drawdown": max(drawdown_values) if drawdown_values else None,
        "fill_rate": _management_avg(fill_rate_values),
        "avg_slippage_bps": _management_avg(slippage_values),
        "total_trades": int(sum(trade_values)) if trade_values else 0,
        "latest_telemetry_at": latest_timestamp_iso,
        "telemetry_evidence_refs": telemetry_evidence_refs,
    }


# --- _pm12_persona_telemetry_records ---
def _pm12_persona_telemetry_records(
    row: Dict[str, Any],
    *,
    runtime_ids: Optional[List[str]] = None,
    telemetry_cache: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    seen = set()
    resolved_runtime_ids = (
        runtime_ids
        if runtime_ids is not None
        else _pm12_persona_runtime_ids(row, telemetry_cache=telemetry_cache)
    )
    for runtime_id in resolved_runtime_ids:
        if telemetry_cache is not None and runtime_id in telemetry_cache:
            summary = telemetry_cache[runtime_id]
        else:
            summary = read_store.get_telemetry_summary(runtime_id)
            if telemetry_cache is not None:
                telemetry_cache[runtime_id] = summary
        if not isinstance(summary, dict):
            continue
        if _pm12_telemetry_record_resolution(summary, runtime_id) != "fresh":
            continue
        candidates: List[Dict[str, Any]] = [dict(summary)]
        for key in _PM12_TELEMETRY_HISTORY_KEYS:
            raw_history = summary.get(key)
            if isinstance(raw_history, list):
                candidates.extend(dict(item) for item in raw_history if isinstance(item, dict))
        for candidate in candidates:
            if _pm12_telemetry_record_resolution(candidate, runtime_id) != "fresh":
                continue
            candidate.setdefault("runtime_id", runtime_id)
            dedupe_key = (
                str(candidate.get("runtime_id") or ""),
                str(
                    candidate.get("collected_at")
                    or candidate.get("collectedAt")
                    or candidate.get("bucket_start")
                    or candidate.get("bucketStart")
                    or candidate.get("timestamp")
                    or candidate.get("updated_at")
                    or candidate.get("updatedAt")
                    or candidate.get("created_at")
                    or candidate.get("createdAt")
                    or ""
                ),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            records.append(candidate)
    return records


# --- _pm12_persona_telemetry_metrics ---
def _pm12_persona_telemetry_metrics(
    row: Dict[str, Any],
    *,
    telemetry_cache: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    telemetry_cache = telemetry_cache if telemetry_cache is not None else {}
    runtime_ids = _pm12_persona_runtime_ids(row, telemetry_cache=telemetry_cache)
    records = [
        record
        for record in _pm12_persona_telemetry_records(
            row,
            runtime_ids=runtime_ids,
            telemetry_cache=telemetry_cache,
        )
        if isinstance(record, dict)
    ]
    metrics = _pm12_telemetry_metrics_from_records(
        runtime_ids,
        records,
    )
    resolutions = [
        _pm12_telemetry_record_resolution(summary, runtime_id)
        for runtime_id in runtime_ids
        for summary in [telemetry_cache.get(runtime_id)]
        if isinstance(summary, dict)
    ]
    if records:
        telemetry_resolution = "fresh"
    elif "identity_mismatch" in resolutions:
        telemetry_resolution = "identity_mismatch"
    elif "stale" in resolutions:
        telemetry_resolution = "stale"
    elif "degraded" in resolutions:
        telemetry_resolution = "degraded"
    else:
        telemetry_resolution = "missing"
    metrics["telemetry_resolution"] = telemetry_resolution
    return metrics


# --- _pm12_tier_for_score ---
def _pm12_tier_for_score(score: float) -> Dict[str, Any]:
    for tier in _PM12_LEAGUE_TIER_DEFINITIONS:
        if score >= float(tier["min_score"]):
            return tier
    return _PM12_LEAGUE_TIER_DEFINITIONS[-1]


# --- _pm12_iso_z ---
def _pm12_iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# --- _pm12_current_quarter_id ---
def _pm12_current_quarter_id(snapshot_at: str) -> str:
    timestamp = _audit_datetime(snapshot_at) or datetime.now(timezone.utc)
    quarter = ((timestamp.month - 1) // 3) + 1
    return f"{timestamp.year}-Q{quarter}"


# --- _pm12_quarter_window ---
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


# --- _pm12_add_recommendation_action ---
def _pm12_add_recommendation_action(action_ids: List[str], action_id: str) -> None:
    if action_id in _PM12_QUARTERLY_RECOMMENDATION_ACTIONS and action_id not in action_ids:
        action_ids.append(action_id)


# --- _pm12_recommendation_action_ids ---
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


# --- _pm12_record_lifecycle_is_active ---
def _pm12_record_lifecycle_is_active(
    record: Dict[str, Any],
    *,
    fields: tuple[str, ...],
    active_values: Set[str],
) -> bool:
    if _pm12_record_freshness_issue(record) is not None:
        return False
    if any(
        record.get(field) not in (None, "")
        for field in ("retired_at", "ended_at", "terminated_at", "deleted_at")
    ):
        return False
    now = datetime.now(timezone.utc)
    effective_from = _audit_datetime(record.get("effective_from"))
    effective_to = _audit_datetime(record.get("effective_to"))
    if effective_from is not None and effective_from > now:
        return False
    if effective_to is not None and effective_to <= now:
        return False
    declared = [
        str(record.get(field) or "").strip().lower()
        for field in fields
        if record.get(field) not in (None, "")
    ]
    return bool(declared) and all(value in active_values for value in declared)


# --- _pm12_binding_runtime_context ---
def _pm12_binding_runtime_context(
    *,
    persona_id: str,
    item: Dict[str, Any],
    bindings: List[Dict[str, Any]],
    runtimes: List[Dict[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any], str]:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    declared_binding_ids = {
        str(value or "").strip()
        for value in (
            item.get("binding_id"),
            item.get("persona_capital_binding_id"),
            (item.get("capital_binding") or {}).get("id")
            if isinstance(item.get("capital_binding"), dict)
            else None,
        )
        if str(value or "").strip()
    }
    declared_sleeve_id = str(
        item.get("capital_sleeve_id") or item.get("sleeve_id") or ""
    ).strip()
    declared_runtime_ids = {
        str(value or "").strip()
        for value in [
            *(item.get("runtime_ids") if isinstance(item.get("runtime_ids"), list) else []),
            *(metrics.get("runtime_ids") if isinstance(metrics.get("runtime_ids"), list) else []),
            item.get("runtime_id"),
            item.get("runtime_binding_id"),
        ]
        if str(value or "").strip()
    }
    active_bindings = [
        record
        for record in bindings
        if _pm12_record_lifecycle_is_active(
            record,
            fields=("status", "validity"),
            active_values={"active", "ready", "bound"},
        )
    ]
    active_runtimes = [
        record
        for record in runtimes
        if _pm12_record_lifecycle_is_active(
            record,
            fields=("status", "state"),
            active_values={"active", "running", "idle"},
        )
    ]
    declared_runtime_records = [
        record
        for record in runtimes
        if str(record.get("runtime_id") or record.get("runtime_binding_id") or record.get("id") or "").strip()
        in declared_runtime_ids
    ]
    declared_runtime_matches = [
        record
        for record in declared_runtime_records
        if _pm12_record_lifecycle_is_active(
            record,
            fields=("status", "state"),
            active_values={"active", "running", "idle"},
        )
    ]
    declared_runtime = declared_runtime_matches[0] if len(declared_runtime_matches) == 1 else {}
    declared_runtime_identity_record = (
        declared_runtime
        or (declared_runtime_records[0] if len(declared_runtime_records) == 1 else {})
    )
    declared_runtime_binding_id = str(
        _persona_fleet_record_value(
            declared_runtime_identity_record,
            "persona_capital_binding_id",
            "binding_id",
        )
        or ""
    ).strip()

    all_explicit_matches = [
        record
        for record in bindings
        if {
            str(record.get("id") or "").strip(),
            str(record.get("binding_id") or "").strip(),
            str(record.get("persona_capital_binding_id") or "").strip(),
        }.intersection(declared_binding_ids)
    ]
    if not all_explicit_matches and declared_sleeve_id:
        all_explicit_matches = [
            record
            for record in bindings
            if str(
                _persona_fleet_record_value(
                    record,
                    "capital_sleeve_id",
                    "capitalSleeveId",
                    "sleeve_id",
                    "sleeveId",
                )
                or ""
            ).strip()
            == declared_sleeve_id
        ]
    if not all_explicit_matches and declared_runtime_binding_id:
        all_explicit_matches = [
            record
            for record in bindings
            if declared_runtime_binding_id
            in {
                str(record.get("id") or "").strip(),
                str(record.get("binding_id") or "").strip(),
                str(record.get("persona_capital_binding_id") or "").strip(),
            }
        ]
    explicit_matches = [
        record
        for record in all_explicit_matches
        if _pm12_record_lifecycle_is_active(
            record,
            fields=("status", "validity"),
            active_values={"active", "ready", "bound"},
        )
    ]
    binding_identity_declared = bool(
        declared_binding_ids or declared_sleeve_id or declared_runtime_binding_id
    )
    if len(all_explicit_matches) > 1:
        binding = {}
        binding_resolution = "ambiguous"
    elif len(all_explicit_matches) == 1 and len(explicit_matches) == 1:
        binding = explicit_matches[0]
        binding_resolution = "explicit"
    elif all_explicit_matches:
        binding = {}
        binding_resolution = "inactive"
    elif binding_identity_declared:
        binding = {}
        binding_resolution = "binding_mismatch"
    elif len(active_bindings) == 1:
        binding = active_bindings[0]
        binding_resolution = "single"
    elif active_bindings:
        binding = {}
        binding_resolution = "ambiguous"
    elif bindings:
        binding = {}
        binding_resolution = "inactive"
    else:
        binding = {}
        binding_resolution = "missing"

    selected_binding_ids = {
        str(value or "").strip()
        for record in ([binding] if binding else [])
        for value in (
            record.get("id"),
            record.get("binding_id"),
            record.get("persona_capital_binding_id"),
        )
        if str(value or "").strip()
    }

    if len(declared_runtime_matches) > 1:
        runtime_candidates: List[Dict[str, Any]] = []
        binding_resolution = f"{binding_resolution}_runtime_ambiguous"
    elif declared_runtime:
        runtime_candidates = [declared_runtime]
    elif binding:
        runtime_candidates = [
            record
            for record in active_runtimes
            if str(
                _persona_fleet_record_value(
                    record,
                    "persona_capital_binding_id",
                    "binding_id",
                )
                or ""
            ).strip()
            in selected_binding_ids
        ]
    else:
        runtime_candidates = [
            record
            for record in active_runtimes
            if str(record.get("persona_id") or "").strip() == persona_id
        ]

    if (
        len(declared_runtime_records) > len(declared_runtime_matches)
        and "inactive" not in binding_resolution
    ):
        binding_resolution = f"{binding_resolution}_runtime_inactive"

    persona_runtime_candidates = [
        record
        for record in runtime_candidates
        if not str(record.get("persona_id") or "").strip()
        or str(record.get("persona_id") or "").strip() == persona_id
    ]
    if len(persona_runtime_candidates) == 1:
        runtime = persona_runtime_candidates[0]
    elif persona_runtime_candidates:
        runtime = {}
        if "runtime_ambiguous" not in binding_resolution:
            binding_resolution = f"{binding_resolution}_runtime_ambiguous"
    else:
        runtime = {}

    runtime_binding_id = str(
        _persona_fleet_record_value(
            runtime,
            "persona_capital_binding_id",
            "binding_id",
        )
        or ""
    ).strip()
    if binding and runtime_binding_id and runtime_binding_id not in selected_binding_ids:
        runtime = {}
        binding_resolution = "binding_mismatch"
    elif not binding and runtime_binding_id:
        active_binding_ids = {
            str(value or "").strip()
            for record in active_bindings
            for value in (
                record.get("id"),
                record.get("binding_id"),
                record.get("persona_capital_binding_id"),
            )
            if str(value or "").strip()
        }
        if runtime_binding_id not in active_binding_ids:
            runtime = {}
            binding_resolution = "binding_mismatch"
    if binding and runtime:
        binding_metadata = (
            binding.get("metadata")
            if isinstance(binding.get("metadata"), dict)
            else {}
        )
        allowed_scope = str(
            binding.get("allowed_deployment_scope")
            or binding_metadata.get("allowed_deployment_scope")
            or ""
        ).strip().lower()
        runtime_mode = str(
            runtime.get("deployment_mode") or ""
        ).strip().lower()
        scope_rank = {"paper": 1, "canary": 2, "live": 3}
        if runtime_mode in {"canary", "live"} and allowed_scope not in scope_rank:
            binding_resolution = "binding_mismatch"
        elif (
            allowed_scope in scope_rank
            and runtime_mode in scope_rank
            and scope_rank[runtime_mode] > scope_rank[allowed_scope]
        ):
            binding_resolution = "binding_mismatch"
    return binding, runtime, binding_resolution


# --- _pm12_evidence_ref_key ---
def _pm12_evidence_ref_key(ref: Any) -> str:
    if isinstance(ref, dict):
        for key in ("ref_id", "refId", "id", "source_ref", "route_href"):
            value = str(ref.get(key) or "").strip()
            if value:
                return value
        return json.dumps(ref, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return str(ref or "").strip()


# --- _pm12_ranking_snapshot_helpers ---
_PM12_RANKING_SNAPSHOT_ITEM_FIELDS = (
    "persona_id",
    "rank",
    "score",
    "overall_score",
    "tier",
    "tier_id",
    "formula_version",
    "allocation_policy_input",
    "components",
    "metrics",
    "stage",
    "deployment_stage",
    "capital_mode",
    "capital_scope",
    "capital_scope_id",
    "capital_pool_id",
    "capital_sleeve_id",
    "paper_ledger_id",
    "current_weight",
    "target_weight",
    "delta",
    "current_weight_source",
    "binding_state",
    "binding_resolution",
    "runtime_resolution",
    "session_resolution",
    "session_id",
    "session_authority",
    "telemetry_resolution",
    "binding_ids",
    "runtime_ids",
    "strategy_ids",
    "capital_pool_ids",
    "sleeve_ids",
    "artifact_ids",
    "broker_ids",
    "eligible",
    "exclusion_codes",
    "exclusion_reasons",
    "evidence_coverage",
    "evidence_ref_ids",
    "source_confidence",
)


def _pm12_ranking_snapshot_payload_items(
    items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    set_like_fields = {
        "binding_ids",
        "runtime_ids",
        "strategy_ids",
        "capital_pool_ids",
        "sleeve_ids",
        "artifact_ids",
        "broker_ids",
        "exclusion_codes",
        "exclusion_reasons",
    }
    payload_items: List[Dict[str, Any]] = []
    for item in items:
        payload_item: Dict[str, Any] = {}
        for field in _PM12_RANKING_SNAPSHOT_ITEM_FIELDS:
            if field not in item:
                continue
            if field == "evidence_ref_ids":
                payload_item[field] = sorted(
                    str(value).strip()
                    for value in (
                        item.get("_snapshot_evidence_ref_ids")
                        or item.get(field)
                        or []
                    )
                    if str(value).strip()
                )
            elif field in set_like_fields and isinstance(item.get(field), list):
                payload_item[field] = sorted(
                    item.get(field) or [],
                    key=lambda value: json.dumps(
                        value,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                )
            elif field == "metrics" and isinstance(item.get(field), dict):
                metrics = json.loads(json.dumps(item.get(field)))
                for nested_field in ("runtime_ids", "telemetry_evidence_refs"):
                    if isinstance(metrics.get(nested_field), list):
                        metrics[nested_field] = sorted(
                            metrics[nested_field],
                            key=lambda value: json.dumps(
                                value,
                                sort_keys=True,
                                separators=(",", ":"),
                                ensure_ascii=True,
                            ),
                        )
                payload_item[field] = metrics
            else:
                payload_item[field] = item.get(field)
        payload_items.append(payload_item)
    payload_items.sort(
        key=lambda item: (
            (
                int(item.get("rank"))
                if isinstance(item.get("rank"), int)
                or str(item.get("rank") or "").isdigit()
                else 10**9
            ),
            str(item.get("persona_id") or ""),
        )
    )
    return payload_items




# --- _pm12_ranking_snapshot_content ---
def _pm12_ranking_snapshot_content(
    items: List[Dict[str, Any]],
    *,
    surface: str,
    period: str,
) -> Dict[str, Any]:
    return {
        "surface": surface,
        "period": period,
        "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
        "items": _pm12_ranking_snapshot_payload_items(items),
    }


# --- _pm12_attach_ranking_snapshot ---
def _pm12_attach_ranking_snapshot(
    items: List[Dict[str, Any]],
    *,
    surface: str,
    period: str,
) -> tuple[List[Dict[str, Any]], str]:
    content = _pm12_ranking_snapshot_content(items, surface=surface, period=period)
    content_digest = _stable_json_hash(content)
    clean_period = re.sub(
        r"[^a-z0-9]+",
        "-",
        str(period or "current").strip().lower(),
    ).strip("-")
    snapshot_id = (
        f"ranking-{surface}-{clean_period or 'current'}-{content_digest[:24]}"
    )
    evidence_assertion_digests: Dict[str, List[str]] = {}
    for item in items:
        persona_id = str(item.get("persona_id") or "").strip()
        if not persona_id:
            continue
        evidence_assertion_digests.setdefault(persona_id, []).append(
            _stable_json_hash(item.get("evidence_refs") or [])
        )
    _get_ranking_write_owner().put_ranking_snapshot({
        "ranking_snapshot_id": snapshot_id,
        "surface": surface,
        "period": period,
        "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
        "content_digest": content_digest,
        "items": content["items"],
        "evidence_assertion_digests": evidence_assertion_digests,
        "created_at": utc_now(),
    })
    return (
        [
            {
                **{
                    key: value
                    for key, value in item.items()
                    if key != "_snapshot_evidence_ref_ids"
                },
                "ranking_snapshot_id": snapshot_id,
            }
            for item in items
        ],
        snapshot_id,
    )


# --- _pm12_quarterly_recommendation_item ---
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


# --- _promotion_review_constants ---
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




# --- _promotion_review_clean_id ---
def _promotion_review_clean_id(review_id: Any) -> str:
    clean_id = str(review_id or "").strip()
    if clean_id.startswith(_PROMOTION_REVIEW_ID_PREFIX):
        clean_id = clean_id[len(_PROMOTION_REVIEW_ID_PREFIX):]
    if clean_id.startswith(_PROMOTION_REVIEW_TARGET_PREFIX):
        clean_id = clean_id[len(_PROMOTION_REVIEW_TARGET_PREFIX):]
    return clean_id


# --- _promotion_review_target_id ---
def _promotion_review_target_id(review_id: Any) -> str:
    return f"{_PROMOTION_REVIEW_TARGET_PREFIX}{_promotion_review_clean_id(review_id)}"


# --- _promotion_review_revision_id ---
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


# --- _promotion_review_revision_recommendation_id ---
def _promotion_review_revision_recommendation_id(review_id: Any) -> str:
    clean_id = _promotion_review_clean_id(review_id)
    match = _PROMOTION_REVIEW_REVISION_RE.fullmatch(clean_id)
    if match is None:
        return clean_id
    return match.group("recommendation_id")


# --- _promotion_review_record_revision_id ---
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


# --- _promotion_review_quarter_from_id ---
def _promotion_review_quarter_from_id(review_id: Any) -> Optional[str]:
    match = _PROMOTION_REVIEW_ID_QUARTER_RE.search(_promotion_review_clean_id(review_id))
    if match is None:
        return None
    return match.group("quarter").upper()


# --- _promotion_review_stage_path ---
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


# --- _latest_promotion_review_submission ---
def _latest_promotion_review_submission(review_id: Any) -> Optional[Dict[str, Any]]:
    clean_id = _promotion_review_clean_id(review_id)
    for record in reversed(command_store._get_all_commands()):
        if not _human_inbox_trusted_promotion_submission(record):
            continue
        if _promotion_review_record_revision_id(record) == clean_id:
            return record
    return None


# --- _promotion_review_submission_projection ---
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


# --- _latest_promotion_review_command ---
def _latest_promotion_review_command(review_id: Any) -> Optional[Dict[str, Any]]:
    clean_id = _promotion_review_clean_id(review_id)
    for record in reversed(command_store._get_all_commands()):
        if (
            _human_inbox_decision_recommendation_id(record) == clean_id
            and _human_inbox_decision_projection_from_record(record) is not None
        ):
            return record
    return None


# --- _promotion_review_decision_projection ---
def _promotion_review_decision_projection(review_id: Any) -> Optional[Dict[str, Any]]:
    record = _latest_promotion_review_command(review_id)
    if record is None:
        return None
    return _human_inbox_decision_projection_from_record(record)


# --- _raise_if_promotion_review_direct_mutation_requested ---
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


# --- _pm12_normalize_mover_direction ---
def _pm12_normalize_mover_direction(direction: Optional[str]) -> str:
    normalized = str(direction or "all").strip().lower() or "all"
    if normalized not in _PM12_LEAGUE_MOVER_DIRECTIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_direction",
                "message": "direction must be one of all, up, down, flat, or new.",
                "field": "direction",
            },
        )
    return normalized


# --- _read_store_fixture_records ---
def _read_store_fixture_records(dataset: str) -> List[Dict[str, Any]]:
    data = getattr(read_store, "_data", {})
    raw = data.get(dataset) if isinstance(data, dict) else None
    if isinstance(raw, dict):
        return [dict(record) for record in raw.values() if isinstance(record, dict)]
    if isinstance(raw, list):
        return [dict(record) for record in raw if isinstance(record, dict)]
    return []


# --- _merge_registry_records ---
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


# --- _tool_fixture_records ---
def _tool_fixture_records() -> List[Dict[str, Any]]:
    store_records = read_store.list_tools()
    if store_records:
        return store_records
    return _read_store_fixture_records("tools")


# --- _skill_fixture_records ---
def _skill_fixture_records() -> List[Dict[str, Any]]:
    store_records = read_store.list_skills()
    if store_records:
        return store_records
    return _read_store_fixture_records("skills")


# --- _sem_command_response ---
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


# --- _as_float ---
def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --- _management_fleet_ooda_label ---
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


# --- _management_fleet_autonomy ---
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


# --- _trading_performance_delta ---
def _trading_performance_delta() -> Optional[float]:
    """Return no delta until telemetry defines a canonical trading-return field."""

    return None


# --- _source_health_overlay_helpers ---
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


def _live_source_health_by_connector() -> Dict[str, Any]:
    return {
        connector_id: payload["health"]
        for connector_id, payload in _source_ingest_truth_by_connector().items()
        if isinstance(payload.get("health"), dict)
    }


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


# ---------------------------------------------------------------------------
# PersonaService Class
# ---------------------------------------------------------------------------

class PersonaService:
    """Canonical domain application service for persona operations."""

    def __init__(
        self,
        *,
        read_store: Optional[Any] = None,
        command_store: Optional[Any] = None,
        provisioning_store: Optional[Any] = None,
        write_owner: Optional[Any] = None,
        ranking_write_owner: Optional[Any] = None,
        get_read_store: Optional[Callable[[], Any]] = None,
        get_command_store: Optional[Callable[[], Any]] = None,
        get_provisioning_store: Optional[Callable[[], Any]] = None,
        get_ranking_write_owner: Optional[Callable[[], Any]] = None,
        utc_now_fn: Optional[Callable[[], str]] = None,
        bff_error_fn: Optional[Callable[..., HTTPException]] = None,
        snapshot_meta_fn: Optional[Callable[..., Dict[str, Any]]] = None,
        dataset_surface_status_fn: Optional[Callable[..., Dict[str, Any]]] = None,
        raise_if_read_surface_unavailable_fn: Optional[Callable[..., None]] = None,
    ) -> None:
        global _ranking_write_owner
        resolved_write_owner = write_owner
        if resolved_write_owner is None:
            raise RuntimeError("Required persona write_owner is absent; failing startup closed.")

        resolved_read_store = (
            read_store
            or (get_read_store() if get_read_store is not None else None)
        )
        if resolved_read_store is None:
            raise RuntimeError("Required read_surface/read_store is absent; failing startup closed.")

        resolved_ranking_write_owner = (
            ranking_write_owner
            or (get_ranking_write_owner() if get_ranking_write_owner is not None else None)
        )
        if resolved_ranking_write_owner is None:
            raise RuntimeError("Required ranking_write_owner is absent; failing startup closed.")

        resolved_command_store = (
            command_store
            or (get_command_store() if get_command_store is not None else None)
        )
        if resolved_command_store is None:
            raise RuntimeError("Required command_store is absent; failing startup closed.")

        self._write_owner = resolved_write_owner
        self._read_store = resolved_read_store
        self._ranking_write_owner = resolved_ranking_write_owner
        _ranking_write_owner = resolved_ranking_write_owner
        globals()["read_store"] = resolved_read_store
        self._command_store = resolved_command_store
        globals()["command_store"] = resolved_command_store
        self._provisioning_store = provisioning_store
        self._get_read_store = lambda: self._read_store
        self._get_command_store = lambda: self._command_store
        self._get_provisioning_store = get_provisioning_store or (lambda: self._provisioning_store or _persona_provisioning_store())
        self._get_ranking_write_owner = lambda: self._ranking_write_owner
        self._utc_now = utc_now_fn or _utc_now_rfc3339
        self._bff_error = bff_error_fn or _bff_error
        self._snapshot_meta = snapshot_meta_fn or _snapshot_meta
        self._dataset_surface_status = dataset_surface_status_fn or _dataset_surface_status
        self._raise_if_read_surface_unavailable = (
            raise_if_read_surface_unavailable_fn or _raise_if_read_surface_unavailable
        )

    def get_read_store(self) -> Any:
        return self._get_read_store()

    def get_command_store(self) -> Any:
        return self._get_command_store()

    def get_provisioning_store(self) -> Any:
        return self._get_provisioning_store()

    def get_ranking_write_owner(self) -> Any:
        return self._get_ranking_write_owner()
