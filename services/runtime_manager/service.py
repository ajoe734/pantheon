"""RuntimeManagerService — pure service layer (no HTTP).

This module is the sole authorised writer for RuntimeBinding records within the
Execution Plane.  It enforces the pre-conditions documented in:

    services/runtime_manager/contract.md
    BINDING_AND_DEPLOYMENT_SEMANTICS.md  §19 (RUN-001)

Write authority
---------------
Only this service may call RuntimeBindingStore write methods.
Governance Plane, Capital Pool Plane, and BFF layers are read-only.

Pre-conditions for deploy()
----------------------------
1. DeploymentPlan exists and status ∈ {approved, executing}
2. PersonaCapitalBinding exists and status = active, with
   allowed_deployment_scope >= target_stage
3. PersonaCapitalBinding.status must equal "active" — revoked/suspended
   bindings are rejected at the service layer (caller must pass
   persona_capital_binding_status="active")
4. Loader-check proof must be present and true — caller must pass
   loader_checks_passed=True; False is rejected (RUN-001)
5. Single-runtime rule: pool must have no existing active binding
   (enforced by RuntimeBindingStore.create with single_runtime_enforced=True)
6. stage consistency: binding.deployment_mode == plan.target_stage
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional

from services.runtime_manager.runtime_binding import (
    RuntimeBinding,
    RuntimeBindingError,
    RuntimeBindingStatus,
    RuntimeBindingStore,
    DeploymentMode,
    RollbackActionType,
    validate_binding,
    utc_now,
)
from services.runtime_manager.kill_switch_controller import (
    KillSwitchController,
    KillSwitchActionType,
    KillSwitchError,
    SafeModeState,
    SoftTriggerReason,
)
from services.foundation import (  # noqa: E402
    ActorRef,
    ActorType,
    AuditAction,
    AuthorityScope,
    CommandEnvelope,
    CommandRecoveryAction,
    CommandRecoveryAudit,
    EnvironmentName,
    EnvironmentScope,
    ErrorEnvelope,
    ErrorKind,
    IdempotencyRecord,
    IdempotencyStatus,
    PolicyDecision,
    PolicyDecisionValue,
    TraceContext,
    command_recovery_entry,
    foundation_id,
    idempotency_record_from_entry,
    load_command_recovery_entries,
)
from services.capital.risk_policy import (  # noqa: E402
    RiskPolicyEvaluation,
    RiskPolicyEvaluationContext,
    RiskPolicyEvaluator,
    RiskPolicyTargetType,
    risk_policy_rejection_message,
)

__all__ = [
    "RuntimeManagerService",
    "RuntimeManagerError",
    "DeployPlanRequest",
    "ReplaceRuntimeRequest",
    "RollbackRequest",
    "KillSwitchRequest",
    "EvolutionFreezeRequest",
    "EvolutionRetrainRequest",
    "EvolutionRedeployRequest",
]

# ---------------------------------------------------------------------------
# Stage ordering — used to verify allowed_deployment_scope >= target_stage
# ---------------------------------------------------------------------------

_STAGE_ORDER = {
    "none": 0,
    "paper": 1,
    "canary": 2,
    "live": 3,
}
_ACTIVATION_GATE_STAGES = {"canary", "live"}
_CANARY_MAX_CAPITAL_SCALE_PCT = 5.0
_CANARY_MAX_GROSS_SCALE_PCT = 25.0
_COMMON_ACTIVATION_GATE_FIELDS = (
    "promotion_gate_decision_id",
    "human_gate_packet_ref",
    "broker_sandbox_smoke_ref",
    "risk_owner_approval_ref",
    "operator_approval_ref",
)
_LIVE_EXTRA_ACTIVATION_GATE_FIELDS = ("canary_observation_ref",)
_FOUNDATION_POLICY_VERSION = "2026-04-27"
_KILL_SWITCH_FOUNDATION_OPERATION = "runtime_manager.kill_switch.dispatch"
_KILL_SWITCH_TELEMETRY_ACK_VERSION = "2026-05-01"
_FORWARD_DEPLOY_ALLOWED_SAFE_MODES = {
    SafeModeState.NORMAL.value,
    SafeModeState.NORMAL_RESTORED.value,
}


def _scope_allows_stage(allowed_deployment_scope: str, target_stage: str) -> bool:
    return _STAGE_ORDER.get(allowed_deployment_scope, -1) >= _STAGE_ORDER.get(target_stage, 999)


def _nested_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _first_non_empty_from_sources(sources: List[Dict[str, Any]], key: str) -> str:
    for source in sources:
        value = source.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _activation_gate_sources(request: Dict[str, Any]) -> List[Dict[str, Any]]:
    metadata = _nested_mapping(request.get("metadata"))
    return [
        request,
        _nested_mapping(request.get("promotion_gate")),
        _nested_mapping(request.get("activation_gate")),
        _nested_mapping(metadata.get("promotion_gate")),
        _nested_mapping(metadata.get("activation_gate")),
    ]


def _float_gate_value(sources: List[Dict[str, Any]], key: str) -> Optional[float]:
    raw = _first_non_empty_from_sources(sources, key)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeManagerError(f"{key} must be numeric for canary/live activation gate") from exc


def _validate_activation_gate(
    request: Dict[str, Any],
    *,
    target_stage: str,
    persona_capital_binding_id: str,
    allowed_deployment_scope: str,
) -> Optional[Dict[str, Any]]:
    if target_stage not in _ACTIVATION_GATE_STAGES:
        return None

    if not persona_capital_binding_id:
        raise RuntimeManagerError(
            "canary/live activation requires persona_capital_binding_id as the capital binding proof."
        )
    if not _scope_allows_stage(allowed_deployment_scope, target_stage):
        raise RuntimeManagerError(
            "canary/live activation requires a capital binding whose allowed_deployment_scope permits the target stage."
        )

    sources = _activation_gate_sources(request)
    required_fields = list(_COMMON_ACTIVATION_GATE_FIELDS)
    if target_stage == "live":
        required_fields.extend(_LIVE_EXTRA_ACTIVATION_GATE_FIELDS)
    missing = [field for field in required_fields if not _first_non_empty_from_sources(sources, field)]
    if missing:
        raise RuntimeManagerError(
            f"{target_stage} activation is blocked until explicit promotion gate evidence is present: "
            + ", ".join(missing)
        )

    capital_scale_pct = _float_gate_value(sources, "capital_scale_pct")
    gross_scale_pct = _float_gate_value(sources, "gross_scale_pct")
    if target_stage == "canary":
        if capital_scale_pct is None or not (0 < capital_scale_pct <= _CANARY_MAX_CAPITAL_SCALE_PCT):
            raise RuntimeManagerError(
                "canary activation requires 0 < capital_scale_pct <= 5 in the promotion gate."
            )
        if gross_scale_pct is None or not (0 < gross_scale_pct <= _CANARY_MAX_GROSS_SCALE_PCT):
            raise RuntimeManagerError(
                "canary activation requires 0 < gross_scale_pct <= 25 in the promotion gate."
            )

    gate = {field: _first_non_empty_from_sources(sources, field) for field in required_fields}
    if capital_scale_pct is not None:
        gate["capital_scale_pct"] = capital_scale_pct
    if gross_scale_pct is not None:
        gate["gross_scale_pct"] = gross_scale_pct
    gate["target_stage"] = target_stage
    gate["persona_capital_binding_id"] = persona_capital_binding_id
    gate["allowed_deployment_scope"] = allowed_deployment_scope
    return gate


def _validate_upstream_risk_policy_evaluation(
    payload: Any,
    *,
    error_prefix: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    evaluation = RiskPolicyEvaluation.from_mapping(payload)
    if evaluation.rejected:
        raise RuntimeManagerError(risk_policy_rejection_message(error_prefix, evaluation))
    return evaluation.to_dict()


def _risk_policy_context_from_deploy_request(
    request: Dict[str, Any],
    *,
    binding_metadata: Dict[str, Any],
    target_stage: str,
    capital_pool_id: str,
    plan_id: str,
    runtime_id: str,
) -> RiskPolicyEvaluationContext:
    activation_gate = _nested_mapping(request.get("promotion_gate"))
    activation_gate.update(_nested_mapping(request.get("activation_gate")))
    activation_gate.update(_nested_mapping(binding_metadata.get("activation_gate")))
    metadata_context = _nested_mapping(binding_metadata.get("risk_policy_context"))
    request_context = _nested_mapping(request.get("risk_policy_context"))
    context = {
        **metadata_context,
        **request_context,
        "target_type": RiskPolicyTargetType.RUNTIME_BINDING.value,
        "target_id": request_context.get("target_id") or request.get("binding_id") or runtime_id or plan_id,
        "capital_pool_id": capital_pool_id,
        "stage": target_stage,
        "risk_policy_ref": _first_non_empty_from_sources(
            [request, binding_metadata, request_context, metadata_context],
            "risk_policy_ref",
        ),
        "capital_scale_pct": request_context.get(
            "capital_scale_pct",
            activation_gate.get("capital_scale_pct"),
        ),
        "gross_scale_pct": request_context.get(
            "gross_scale_pct",
            activation_gate.get("gross_scale_pct"),
        ),
        "runtime_action": request.get("runtime_action") or binding_metadata.get("runtime_action"),
        "target_weights": request_context.get("target_weights") or binding_metadata.get("target_weights") or {},
        "asset_classes": request_context.get("asset_classes") or binding_metadata.get("asset_classes") or [],
        "strategy_family": request_context.get("strategy_family") or binding_metadata.get("strategy_family"),
        "gross_exposure": request_context.get("gross_exposure") or binding_metadata.get("gross_exposure"),
        "net_exposure": request_context.get("net_exposure") or binding_metadata.get("net_exposure"),
        "leverage": request_context.get("leverage") or binding_metadata.get("leverage"),
        "turnover": request_context.get("turnover") or binding_metadata.get("turnover"),
        "sector_exposures": request_context.get("sector_exposures")
        or binding_metadata.get("sector_exposures")
        or {},
        "factor_exposures": request_context.get("factor_exposures")
        or binding_metadata.get("factor_exposures")
        or {},
        "liquidity": request_context.get("liquidity") or binding_metadata.get("liquidity") or {},
        "order_type": request_context.get("order_type") or binding_metadata.get("order_type"),
        "time_in_force": request_context.get("time_in_force") or binding_metadata.get("time_in_force"),
        "drawdown_pct": request_context.get("drawdown_pct") or binding_metadata.get("drawdown_pct"),
        "kill_switch_trigger": request_context.get("kill_switch_trigger")
        or binding_metadata.get("kill_switch_trigger"),
        "metadata": {**binding_metadata, **metadata_context, **request_context},
        "trace_id": request_context.get("trace_id") or binding_metadata.get("trace_id"),
    }
    return RiskPolicyEvaluationContext.from_mapping(context)


def _evaluate_risk_policy_for_deploy(
    request: Dict[str, Any],
    *,
    binding_metadata: Dict[str, Any],
    target_stage: str,
    capital_pool_id: str,
    plan_id: str,
    runtime_id: str,
) -> None:
    upstream = (
        request.get("risk_policy_evaluation")
        or binding_metadata.get("risk_policy_evaluation")
    )
    upstream_payload = _validate_upstream_risk_policy_evaluation(
        upstream,
        error_prefix="RuntimeBinding blocked",
    )
    if upstream_payload is not None:
        binding_metadata.setdefault("risk_policy_evaluation", upstream_payload)

    risk_policy = request.get("risk_policy") or binding_metadata.get("risk_policy")
    if risk_policy is None:
        return
    evaluation = RiskPolicyEvaluator().evaluate(
        risk_policy,
        _risk_policy_context_from_deploy_request(
            request,
            binding_metadata=binding_metadata,
            target_stage=target_stage,
            capital_pool_id=capital_pool_id,
            plan_id=plan_id,
            runtime_id=runtime_id,
        ),
    )
    binding_metadata["risk_policy_evaluation"] = evaluation.to_dict()
    if evaluation.rejected:
        raise RuntimeManagerError(risk_policy_rejection_message("RuntimeBinding blocked", evaluation))


def _foundation_environment_scope(request: Dict[str, Any]) -> EnvironmentScope:
    context = request.get("context") if isinstance(request.get("context"), dict) else {}
    raw = str(
        request.get("environment")
        or context.get("environment")
        or context.get("target_stage")
        or os.getenv("PANTHEON_ENV", "dev")
    ).strip().lower()
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


def _foundation_actor_ref(actor_id: str) -> ActorRef:
    return ActorRef(
        actor_type=ActorType.USER,
        actor_id=str(actor_id or "runtime-manager-caller").strip() or "runtime-manager-caller",
    )


def _upstream_trace_payload(request: Dict[str, Any]) -> Dict[str, Any]:
    foundation = request.get("foundation") if isinstance(request.get("foundation"), dict) else {}
    trace = foundation.get("trace_context") if isinstance(foundation.get("trace_context"), dict) else None
    if trace is None and isinstance(request.get("trace_context"), dict):
        trace = request.get("trace_context")
    return dict(trace or {})


def _foundation_trace_from_request(
    request: Dict[str, Any],
    *,
    environment: EnvironmentScope,
    actor_ref: ActorRef,
) -> TraceContext:
    upstream_trace = _upstream_trace_payload(request)
    idempotency_key = str(
        request.get("idempotency_key")
        or upstream_trace.get("idempotency_key")
        or ""
    ).strip() or None
    upstream_trace_id = str(upstream_trace.get("trace_id") or "").strip()
    correlation_id = str(upstream_trace.get("correlation_id") or "").strip() or None
    if upstream_trace_id:
        return TraceContext(
            trace_id=upstream_trace_id,
            correlation_id=correlation_id or upstream_trace_id,
            environment=environment,
            actor_ref=actor_ref,
            source_system="runtime-manager",
            request_id=str(request.get("request_id") or "").strip() or None,
            parent_span_id=str(upstream_trace.get("parent_span_id") or "").strip() or None,
            causation_id=str(upstream_trace.get("request_id") or "").strip() or None,
            idempotency_key=idempotency_key,
        )
    return TraceContext.new(
        environment=environment,
        actor_ref=actor_ref,
        source_system="runtime-manager",
        request_id=str(request.get("request_id") or "").strip() or None,
        idempotency_key=idempotency_key,
    )


def _kill_switch_foundation_payload(request: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "operation": _KILL_SWITCH_FOUNDATION_OPERATION,
        "reason": request.get("reason"),
        "capital_pool_id": request.get("capital_pool_id"),
        "binding_id": request.get("binding_id"),
        "severity": request.get("severity"),
        "action_override": request.get("action_override"),
        "fallback_artifact_id": request.get("fallback_artifact_id"),
        "fallback_artifact_version": request.get("fallback_artifact_version"),
        "context": request.get("context") or {},
    }


def _build_kill_switch_foundation_context(request: Dict[str, Any]) -> Dict[str, Any]:
    environment = _foundation_environment_scope(request)
    actor_ref = _foundation_actor_ref(str(request.get("actor_id") or ""))
    target_id = str(request.get("capital_pool_id") or "unknown-capital-pool").strip()
    authority_scope = AuthorityScope(
        action=_KILL_SWITCH_FOUNDATION_OPERATION,
        target_type="CapitalPool",
        target_id=target_id,
        environment=environment,
        capital_pool_id=target_id,
        runtime_id=str(request.get("binding_id") or "").strip() or None,
    )
    trace = _foundation_trace_from_request(
        request,
        environment=environment,
        actor_ref=actor_ref,
    )
    payload = _kill_switch_foundation_payload(request)
    command_envelope = CommandEnvelope.new(
        command_type=_KILL_SWITCH_FOUNDATION_OPERATION,
        actor_ref=actor_ref,
        authority_scope=authority_scope,
        payload=payload,
        trace=trace,
        idempotency_key=str(request.get("idempotency_key") or trace.idempotency_key or "").strip() or None,
    )
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=command_envelope.idempotency_key,
        operation_type=_KILL_SWITCH_FOUNDATION_OPERATION,
        target_ref=authority_scope.target_ref,
        request_payload=payload,
        trace_id=command_envelope.trace.trace_id,
    )
    policy_decision = PolicyDecision.make(
        policy_id="runtime-manager.kill-switch.fast-path",
        policy_version=_FOUNDATION_POLICY_VERSION,
        decision=PolicyDecisionValue.ALLOW,
        actor_ref=actor_ref,
        action=_KILL_SWITCH_FOUNDATION_OPERATION,
        target_ref=authority_scope.target_ref,
        environment=environment,
        trace_id=command_envelope.trace.trace_id,
    )
    audit_action = AuditAction.record(
        actor_ref=actor_ref,
        action_type="runtime_manager.kill_switch.accepted",
        target_ref=authority_scope.target_ref,
        environment=environment,
        reason=str(request.get("reason") or "kill-switch dispatch"),
        trace=command_envelope.trace,
        payload=payload,
        policy_decision_ref=policy_decision.decision_id,
        metadata={"path": "RuntimeManagerService.execute_kill_switch"},
    )
    return {
        "trace_context": command_envelope.trace,
        "command_envelope": command_envelope,
        "idempotency_record": idempotency_record,
        "policy_decision": policy_decision,
        "audit_action": audit_action,
        "request_payload": payload,
    }


def _serialize_foundation_context(context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "trace_context": context["trace_context"].to_dict(),
        "command_envelope": context["command_envelope"].to_dict(),
        "idempotency_record": context["idempotency_record"].to_dict(),
        "policy_decision": context["policy_decision"].to_dict(),
        "audit_action": context["audit_action"].to_dict(),
    }


def _foundation_idempotency_conflict(
    context: Dict[str, Any],
    *,
    existing_command_id: str,
) -> ErrorEnvelope:
    command_envelope: CommandEnvelope = context["command_envelope"]
    idempotency_record: IdempotencyRecord = context["idempotency_record"]
    return ErrorEnvelope(
        error_id=foundation_id("err"),
        error_code="IDEMPOTENCY_CONFLICT",
        message="Idempotency key was already used with a different kill-switch payload",
        error_kind=ErrorKind.IDEMPOTENCY_CONFLICT,
        trace=command_envelope.trace,
        status_code=409,
        details={
            "idempotency_key": idempotency_record.idempotency_key,
            "existing_command_id": existing_command_id,
        },
    )


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class RuntimeManagerError(RuntimeError):
    """Raised when a RuntimeManager pre-condition or write guard fails."""


# ---------------------------------------------------------------------------
# Input shapes (plain dicts in practice; typed classes for documentation)
# ---------------------------------------------------------------------------

class DeployPlanRequest:
    """Typed descriptor for deploy() input — passed as a plain dict in HTTP layer."""
    # Fields the caller must supply:
    #   plan_id                          str   — DeploymentPlan.plan_id
    #   plan_status                      str   — must be 'approved' or 'executing'
    #   target_stage                     str   — paper / canary / live / frozen
    #   artifact_id                      str
    #   artifact_version                 str
    #   capital_pool_id                  str
    #   persona_capital_binding_id       str   — PersonaCapitalBinding.binding_id
    #   persona_capital_binding_status   str   — must be 'active' (RUN-001)
    #   allowed_deployment_scope         str   — from PersonaCapitalBinding
    #   loader_checks_passed             bool  — must be True (RUN-001)
    #   runtime_id                       str   — optional; auto-generated if absent
    #   rollback_parent                  str   — optional; present when this is a rollback
    #   rollback_action_type             str   — optional; required when rollback_parent set


class ReplaceRuntimeRequest:
    """Typed descriptor for replace() input — passed as a plain dict.

    A forward replacement consumes the same approved DeploymentPlan descriptor
    as :meth:`deploy`, plus ``current_binding_id``.  ``runtime_id`` is required
    and must identify both the current binding and the canonical runtime route.
    The replacement is deliberately same-stage and keeps the capital pool and
    PersonaCapitalBinding unchanged.
    """


class RollbackRequest:
    """Typed descriptor for rollback() input — passed as a plain dict.

    The rollback() method creates a replacement RuntimeBinding that follows
    the action semantics from ROLLBACK_AND_POSITION_SEMANTICS.md §3.

    Fields the caller must supply
    --------------------------------
    current_binding_id                   str  — the RuntimeBinding to replace
    action_type                          str  — replace | pause_then_replace |
                                                liquidate_then_replace
    replacement_plan_id                  str  — DeploymentPlan for the replacement
    replacement_artifact_id              str  — fallback artifact to activate
    replacement_artifact_version         str
    replacement_persona_capital_binding_id  str
    replacement_allowed_deployment_scope    str

    Optional fields
    ---------------
    replacement_plan_status              str  — default 'approved'
    replacement_persona_capital_binding_status  str  — default 'active'
    replacement_deployment_mode          str  — default: inherits old binding's stage
    replacement_runtime_id               str  — auto-generated if absent
    replacement_start_paused             bool — for liquidate_then_replace, start
                                               replacement in paused/guarded mode;
                                               default False
    loader_checks_passed                 bool — default True
    opened_by_artifact_id                str  — original position opener artifact;
                                               included in position_lineage output
                                               (default: old binding's artifact_id)
    replacement_metadata                 dict — metadata carried to the rollback binding
    replacement_strategy_id              str  — strategy identity carried to metadata
    """


class KillSwitchRequest:
    """Typed descriptor for execute_kill_switch() input — passed as a plain dict.

    Fields the caller must supply
    --------------------------------
    reason       str  — HardTriggerReason or SoftTriggerReason value
    capital_pool_id  str  — pool under emergency
    actor_id     str  — operator or system component raising the trigger

    Optional fields
    ---------------
    binding_id              str  — active RuntimeBinding targeted (if known)
    severity                int  — numeric severity (1 = highest)
    action_override         str  — override default action selection from §7 matrix
    fallback_artifact_id    str  — required when action_override or default is REPLACE
    fallback_artifact_version str — required when action_override or default is REPLACE
    context                 dict — arbitrary metadata (broker info, metrics, etc.)
    """


class EvolutionFreezeRequest:
    """Typed descriptor for evolution_freeze() input — passed as a plain dict.

    Implements the runtime follow-through for an approved evolution freeze
    decision per EVOLUTION_REVIEW_AND_THRESHOLDS.md §11 and
    KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md §3.

    Fields the caller must supply
    --------------------------------
    evolution_decision_id   str  — approved EvolutionDecision.id
    binding_id              str  — active RuntimeBinding to freeze
    freeze_action           str  — freeze_binding | pause_then_freeze | liquidate_then_freeze
    actor_id                str  — reviewer/operator authorising the freeze

    Optional fields
    ---------------
    note                    str  — governance note for audit
    """


class EvolutionRetrainRequest:
    """Typed descriptor for evolution_retrain() input — passed as a plain dict.

    Records that an approved retrain/revalidate EvolutionDecision has been
    dispatched to the research plane.  The runtime-manager marks the decision
    as executed and emits a routing record — actual model retraining happens
    in the research plane, not here.

    Fields the caller must supply
    --------------------------------
    evolution_decision_id   str  — approved EvolutionDecision.id
    action_type             str  — retrain | revalidate
    artifact_id             str  — target artifact that will be retrained
    actor_id                str  — reviewer/operator dispatching retrain
    research_job_id         str  — authoritative research-plane work item id

    Optional fields
    ---------------
    note                    str
    """


class EvolutionRedeployRequest:
    """Typed descriptor for evolution_redeploy() input — passed as a plain dict.

    Records the redeploy follow-through after a retrain/revalidate/freeze-lift
    decision.  The runtime-manager creates the new RuntimeBinding from the
    approved replacement artifact.

    This is a thin wrapper over deploy() that records the evolution lineage.

    Fields the caller must supply
    --------------------------------
    evolution_decision_id   str  — approved EvolutionDecision.id
    plan_id                 str  — new DeploymentPlan.plan_id
    target_stage            str  — paper | canary | live
    artifact_id             str
    artifact_version        str
    capital_pool_id         str
    persona_capital_binding_id  str
    persona_capital_binding_status  str  — must be 'active'
    allowed_deployment_scope    str
    loader_checks_passed    bool  — must be True

    Optional fields
    ---------------
    plan_status             str  — default 'approved'
    runtime_id              str  — auto-generated if absent
    actor_id                str
    note                    str
    """


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class RuntimeManagerService:
    """
    Deployable service layer for RuntimeBinding write operations.

    Consumers (Flask routes, CLI, integration tests) call this service
    instead of touching RuntimeBindingStore directly.  This is the single
    enforcement point for all pre-conditions and write-authority rules.

    Parameters
    ----------
    store_path : Path | None
        Optional filesystem path for RuntimeBindingStore persistence.
        When None the store is in-memory only (suitable for testing).
    single_runtime_enforced : bool
        Mirror of CapitalPool.single_runtime_enforced — default True.
    """

    def __init__(
        self,
        store_path: Optional[Path] = None,
        single_runtime_enforced: bool = True,
        ks_store_path: Optional[Path] = None,
    ) -> None:
        self._store = RuntimeBindingStore(path=store_path)
        self._single_runtime_enforced = single_runtime_enforced
        self._kill_switch = KillSwitchController()
        # Serialize forward mutations and emergency containment through one
        # control boundary.  The lock is re-entrant because kill-switch and
        # rollback paths legitimately call deploy() as an internal sub-step.
        self._control_lock = threading.RLock()
        self._replace_lock = threading.RLock()
        self._foundation_idempotency: Dict[str, Dict[str, Any]] = {}
        self._foundation_recovery_audit: List[Dict[str, Any]] = []
        # Derive kill-switch store path alongside the binding store when not supplied.
        if ks_store_path is None and store_path is not None:
            ks_store_path = store_path.parent / "kill_switch.json"
        self._ks_store_path = ks_store_path
        self._load_ks_state()

    @property
    def store(self) -> RuntimeBindingStore:
        """Access the underlying RuntimeBindingStore."""
        return self._store

    def get_binding(self, binding_id: str) -> Optional[RuntimeBinding]:
        """Read a binding by ID."""
        return self._store.get(binding_id)

    def require_binding(self, binding_id: str) -> RuntimeBinding:
        """Require a binding by ID or raise RuntimeBindingError."""
        return self._store.require(binding_id)

    def get_active_binding_for_pool(self, capital_pool_id: str) -> Optional[RuntimeBinding]:
        """Return the active binding for a pool if one exists."""
        return self._store.get_active_for_pool(capital_pool_id)

    def list_bindings(self) -> List[RuntimeBinding]:
        """List all bindings in the store."""
        return self._store.list_all()

    def find_bindings_for_pool(self, capital_pool_id: str) -> List[RuntimeBinding]:
        """Find all bindings for a given capital pool ID."""
        return self._store.find_by_pool(capital_pool_id)

    # ------------------------------------------------------------------ #
    # Primary write operations (Execution Plane only)                     #
    # ------------------------------------------------------------------ #

    def deploy(
        self,
        request: Dict[str, Any],
        _allow_cutover_bypass: bool = False,
        _allow_activation_gate_bypass: bool = False,
        _allow_safe_mode_bypass: bool = False,
        _allow_non_paper_deploy: bool = False,
        _start_paused: bool = False,
        _defer_store: bool = False,
    ) -> RuntimeBinding:
        """Atomically apply safe-mode precedence and create a RuntimeBinding."""
        with self._control_lock:
            return self._deploy_once(
                request,
                _allow_cutover_bypass=_allow_cutover_bypass,
                _allow_activation_gate_bypass=_allow_activation_gate_bypass,
                _allow_safe_mode_bypass=_allow_safe_mode_bypass,
                _allow_non_paper_deploy=_allow_non_paper_deploy,
                _start_paused=_start_paused,
                _defer_store=_defer_store,
            )

    def _deploy_once(
        self,
        request: Dict[str, Any],
        _allow_cutover_bypass: bool = False,
        _allow_activation_gate_bypass: bool = False,
        _allow_safe_mode_bypass: bool = False,
        _allow_non_paper_deploy: bool = False,
        _start_paused: bool = False,
        _defer_store: bool = False,
    ) -> RuntimeBinding:
        """Create a RuntimeBinding from a validated DeploymentPlan descriptor.

        Pre-conditions (RUN-001):
        1. plan_status must be 'approved' or 'executing'
        2. persona_capital_binding_status must equal 'active'
        3. allowed_deployment_scope >= target_stage
        4. loader_checks_passed must be True
        5. stage consistency: target_stage must be a valid DeploymentMode value
        6. Single-runtime rule enforced by the store

        Optional ``strategy_id`` is preserved in RuntimeBinding.metadata so
        paper/runtime adapters can prove that order intents match the active
        governed strategy without gaining RuntimeBinding write authority.

        ``_allow_cutover_bypass`` is an internal-only flag used by the REPLACE
        rollback path to bypass the single-runtime guard for exactly this one
        binding creation during the hot-swap cutover window.  Callers outside
        this class must never set it; it avoids the race condition that arises
        when the old approach temporarily mutated the service-wide
        ``_single_runtime_enforced`` flag.

        ``_allow_activation_gate_bypass`` is also internal-only.  It is used by
        rollback replacement creation so safety actions are not blocked by the
        promotion gate intended for forward canary/live activation.

        ``_allow_safe_mode_bypass`` is reserved for runtime-manager-owned
        rollback/containment.  Forward deployment must lose to any non-normal
        kill-switch state, including a kill that raced an already queued outbox
        command.

        ``_allow_non_paper_deploy`` and ``_start_paused`` are internal-only
        containment/cutover controls.  Ordinary new deployment is deliberately
        paper-only: canary/live activation requires a separate governed path
        that can verify MFA and distinct-actor approval rather than trusting
        caller-supplied reference strings.

        ``_defer_store`` is internal-only and returns the fully validated
        immutable binding without persistence.  Replace/rollback immediately
        pass that object to ``RuntimeBindingStore.cutover`` so child creation
        and source retirement commit as one snapshot.
        """
        plan_id = request.get("plan_id", "")
        plan_status = request.get("plan_status", "")
        target_stage = request.get("target_stage", "")
        artifact_id = request.get("artifact_id", "")
        artifact_version = request.get("artifact_version", "")
        capital_pool_id = request.get("capital_pool_id", "")
        persona_capital_binding_id = request.get("persona_capital_binding_id", "")
        persona_capital_binding_status = request.get("persona_capital_binding_status", "")
        allowed_deployment_scope = request.get("allowed_deployment_scope", "")
        loader_checks_passed = request.get("loader_checks_passed")
        runtime_id = request.get("runtime_id") or f"rt-{uuid.uuid4().hex[:8]}"
        rollback_parent = request.get("rollback_parent")
        rollback_action_type = request.get("rollback_action_type")
        binding_metadata = dict(request.get("metadata") or {}) if isinstance(request.get("metadata"), dict) else {}
        execution_mode = str(request.get("execution_mode") or target_stage).strip().lower()
        if request.get("strategy_id"):
            requested_strategy_id = str(request.get("strategy_id"))
            metadata_strategy_id = str(binding_metadata.get("strategy_id") or "")
            if metadata_strategy_id and metadata_strategy_id != requested_strategy_id:
                raise RuntimeManagerError(
                    "RuntimeBinding metadata.strategy_id conflicts with the "
                    "authoritative deploy strategy_id."
                )
            binding_metadata["strategy_id"] = requested_strategy_id
        sponsor_persona_id = str(request.get("sponsor_persona_id") or "").strip()
        if sponsor_persona_id:
            metadata_persona_id = str(binding_metadata.get("persona_id") or "").strip()
            if metadata_persona_id and metadata_persona_id != sponsor_persona_id:
                raise RuntimeManagerError(
                    "RuntimeBinding metadata.persona_id conflicts with the "
                    "authoritative deploy sponsor_persona_id."
                )
            metadata_sponsor_persona_id = str(
                binding_metadata.get("sponsor_persona_id") or ""
            ).strip()
            if (
                metadata_sponsor_persona_id
                and metadata_sponsor_persona_id != sponsor_persona_id
            ):
                raise RuntimeManagerError(
                    "RuntimeBinding metadata.sponsor_persona_id conflicts with "
                    "the authoritative deploy sponsor_persona_id."
                )
            binding_metadata["persona_id"] = sponsor_persona_id
            binding_metadata["sponsor_persona_id"] = sponsor_persona_id

        safe_mode = self._kill_switch.safe_mode_for(capital_pool_id).value
        if (
            not _allow_safe_mode_bypass
            and safe_mode not in _FORWARD_DEPLOY_ALLOWED_SAFE_MODES
        ):
            raise RuntimeManagerError(
                f"Deploy is blocked by kill-switch safe_mode={safe_mode!r} for "
                f"capital_pool_id={capital_pool_id!r}; containment wins over queued deploy."
            )

        # Pre-condition 1: plan status. A rollback child may truthfully point
        # at the already-executed historical plan whose exact target is being
        # restored; forward deployments still require approved/executing.
        allowed_plan_statuses = {"approved", "executing"}
        if rollback_parent:
            allowed_plan_statuses.add("executed")
        if plan_status not in allowed_plan_statuses:
            raise RuntimeManagerError(
                f"DeploymentPlan {plan_id!r} status {plan_status!r} is not one of "
                f"{sorted(allowed_plan_statuses)!r}. A RuntimeBinding cannot be "
                "created without an admissible canonical plan state."
            )

        # Pre-condition 2: PersonaCapitalBinding must be active
        if persona_capital_binding_status != "active":
            raise RuntimeManagerError(
                f"PersonaCapitalBinding {persona_capital_binding_id!r} status "
                f"{persona_capital_binding_status!r} is not 'active'. "
                "A RuntimeBinding cannot be created against a revoked or non-active capital binding."
            )

        # Pre-condition 3: scope allows stage
        if not _scope_allows_stage(allowed_deployment_scope, target_stage):
            raise RuntimeManagerError(
                f"PersonaCapitalBinding allowed_deployment_scope={allowed_deployment_scope!r} "
                f"does not permit target_stage={target_stage!r}. "
                "Scope must be >= target stage."
            )

        # Pre-condition 4: loader-check proof must be present and true
        if loader_checks_passed is not True:
            raise RuntimeManagerError(
                "loader_checks_passed must be True. "
                "Deploy is blocked until all loader checks have passed (RUN-001)."
            )

        # Pre-condition 5: stage consistency — target_stage must be a valid DeploymentMode
        try:
            DeploymentMode(target_stage)
        except ValueError:
            raise RuntimeManagerError(
                f"target_stage={target_stage!r} is not a valid DeploymentMode. "
                f"Must be one of {[e.value for e in DeploymentMode]}."
            )
        try:
            DeploymentMode(execution_mode)
        except ValueError:
            raise RuntimeManagerError(
                f"execution_mode={execution_mode!r} is not a valid DeploymentMode. "
                f"Must be one of {[e.value for e in DeploymentMode]}."
            )
        if execution_mode != target_stage:
            raise RuntimeManagerError(
                f"execution_mode={execution_mode!r} must equal target_stage={target_stage!r}. "
                "Canary runtime bindings must not be collapsed into live."
            )

        if target_stage != DeploymentMode.PAPER.value and not _allow_non_paper_deploy:
            raise RuntimeManagerError(
                "Ordinary new RuntimeBinding deployment is paper-only. "
                f"target_stage={target_stage!r} requires an authoritative governed "
                "canary/live activation path with MFA and distinct-actor approval proof."
            )
        if _start_paused and not _allow_safe_mode_bypass:
            raise RuntimeManagerError(
                "Starting a RuntimeBinding paused is reserved for runtime-manager "
                "containment and rollback paths."
            )

        # Pre-condition 6: rollback fields consistency
        if rollback_parent and not rollback_action_type:
            raise RuntimeManagerError(
                "rollback_action_type is required when rollback_parent is set."
            )

        if _allow_activation_gate_bypass and target_stage in _ACTIVATION_GATE_STAGES:
            binding_metadata.setdefault(
                "activation_gate",
                {
                    "target_stage": target_stage,
                    "status": "bypassed_for_runtime_manager_rollback",
                    "rollback_parent": rollback_parent,
                },
            )
        else:
            activation_gate = _validate_activation_gate(
                request,
                target_stage=target_stage,
                persona_capital_binding_id=persona_capital_binding_id,
                allowed_deployment_scope=allowed_deployment_scope,
            )
            if activation_gate is not None:
                binding_metadata.setdefault("activation_gate", activation_gate)

        _evaluate_risk_policy_for_deploy(
            request,
            binding_metadata=binding_metadata,
            target_stage=target_stage,
            capital_pool_id=capital_pool_id,
            plan_id=plan_id,
            runtime_id=runtime_id,
        )

        binding_id = f"rb-{uuid.uuid4().hex}"
        binding = RuntimeBinding(
            binding_id=binding_id,
            runtime_id=runtime_id,
            capital_pool_id=capital_pool_id,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            deployment_mode=target_stage,
            execution_mode=execution_mode,
            effective_at=utc_now(),
            status=(
                RuntimeBindingStatus.PAUSED.value
                if _start_paused
                else RuntimeBindingStatus.ACTIVE.value
            ),
            plan_id=plan_id,
            persona_capital_binding_id=persona_capital_binding_id,
            rollback_parent=rollback_parent,
            rollback_action_type=rollback_action_type,
            metadata=binding_metadata,
        )

        # Semantic validation (field-level)
        errors = validate_binding(binding)
        if errors:
            raise RuntimeManagerError(f"RuntimeBinding validation failed: {errors}")

        # Store create — enforces single-runtime rule unless this specific call
        # has been granted a per-call cutover bypass by the REPLACE rollback path.
        if _defer_store:
            return binding
        effective_enforce = self._single_runtime_enforced and not _allow_cutover_bypass
        return self._store.create(
            binding,
            single_runtime_enforced=effective_enforce,
        )

    def retire(self, binding_id: str, retired_at: Optional[str] = None) -> RuntimeBinding:
        """Retire a binding (terminal transition)."""
        with self._control_lock:
            return self._store.retire(binding_id, retired_at=retired_at)

    def replace(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize and idempotently execute a forward binding cutover."""
        with self._control_lock:
            with self._replace_lock:
                return self._replace_once(request)

    def _replace_once(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Replace one runtime binding with a new artifact in the same stage.

        This is the canonical *forward* promotion cutover.  It is separate from
        :meth:`rollback` so a routine artifact promotion cannot manufacture
        rollback lineage.  The replacement keeps the existing runtime, capital
        pool, deployment stage, and PersonaCapitalBinding identities exactly.

        The replacement and retired source are persisted in one atomic store
        snapshot. Ordinary concurrent deploys continue to see the normal
        single-runtime guard.
        """
        current_binding_id = str(request.get("current_binding_id") or "").strip()
        if not current_binding_id:
            raise RuntimeManagerError("current_binding_id is required for forward replace.")

        old_binding = self._store.require(current_binding_id)

        runtime_id = str(request.get("runtime_id") or "").strip()
        if not runtime_id:
            raise RuntimeManagerError("runtime_id is required for forward replace.")
        if runtime_id != old_binding.runtime_id:
            raise RuntimeManagerError(
                f"Forward replace runtime_id={runtime_id!r} does not match current "
                f"binding runtime_id={old_binding.runtime_id!r}."
            )

        capital_pool_id = str(request.get("capital_pool_id") or "").strip()
        if capital_pool_id != old_binding.capital_pool_id:
            raise RuntimeManagerError(
                f"Forward replace capital_pool_id={capital_pool_id!r} does not match current "
                f"binding capital_pool_id={old_binding.capital_pool_id!r}."
            )

        target_stage = str(request.get("target_stage") or "").strip()
        if target_stage != old_binding.deployment_mode:
            raise RuntimeManagerError(
                f"Forward replace target_stage={target_stage!r} must remain at current "
                f"deployment_mode={old_binding.deployment_mode!r}."
            )

        persona_capital_binding_id = str(
            request.get("persona_capital_binding_id") or ""
        ).strip()
        if persona_capital_binding_id != old_binding.persona_capital_binding_id:
            raise RuntimeManagerError(
                "Forward replace persona_capital_binding_id="
                f"{persona_capital_binding_id!r} does not match current binding "
                f"persona_capital_binding_id={old_binding.persona_capital_binding_id!r}."
            )

        artifact_pair = (
            str(request.get("artifact_id") or "").strip(),
            str(request.get("artifact_version") or "").strip(),
        )
        if artifact_pair == (old_binding.artifact_id, old_binding.artifact_version):
            raise RuntimeManagerError(
                "Forward replace artifact_id and artifact_version pair must differ from "
                "the current binding."
            )

        replay_candidates = [
            binding
            for binding in self._store.find_by_pool(old_binding.capital_pool_id)
            if binding.binding_id != current_binding_id
            and binding.plan_id == str(request.get("plan_id") or "")
            and (binding.artifact_id, binding.artifact_version) == artifact_pair
            and binding.runtime_id == runtime_id
            and binding.persona_capital_binding_id == persona_capital_binding_id
            and binding.status
            in {
                RuntimeBindingStatus.ACTIVE.value,
                RuntimeBindingStatus.PAUSED.value,
            }
            and binding.metadata.get("replacement_kind") == "forward"
            and binding.metadata.get("replacement_parent_binding_id")
            == current_binding_id
        ]
        if len(replay_candidates) > 1:
            raise RuntimeManagerError(
                "Forward replace recovery found multiple matching child bindings; "
                "manual reconciliation is required."
            )
        if replay_candidates:
            new_binding = replay_candidates[0]
            if old_binding.status in {
                RuntimeBindingStatus.ACTIVE.value,
                RuntimeBindingStatus.PAUSED.value,
            }:
                cutover_at = utc_now()
                retired_old = self._store.retire(
                    current_binding_id, retired_at=cutover_at
                )
            elif old_binding.status == RuntimeBindingStatus.RETIRED.value:
                retired_old = old_binding
                cutover_at = old_binding.retired_at or new_binding.effective_at
            else:
                raise RuntimeManagerError(
                    f"Forward replace recovery cannot retire current binding in "
                    f"status={old_binding.status!r}."
                )
            return self._forward_replace_result(
                request=request,
                old_binding=retired_old,
                new_binding=new_binding,
                cutover_at=cutover_at,
                replayed=True,
            )

        if old_binding.status not in {
            RuntimeBindingStatus.ACTIVE.value,
            RuntimeBindingStatus.PAUSED.value,
        }:
            raise RuntimeManagerError(
                f"Forward replace requires current binding {current_binding_id!r} to be "
                f"active or paused; current status={old_binding.status!r}."
            )

        deploy_request = dict(request)
        metadata = (
            dict(request.get("metadata") or {})
            if isinstance(request.get("metadata"), dict)
            else {}
        )
        metadata["replacement_parent_binding_id"] = current_binding_id
        metadata["replacement_kind"] = "forward"
        deploy_request["metadata"] = metadata
        # Forward replacement lineage belongs in metadata, never rollback fields.
        deploy_request.pop("rollback_parent", None)
        deploy_request.pop("rollback_action_type", None)

        new_binding = self.deploy(
            deploy_request,
            _allow_cutover_bypass=True,
            _allow_non_paper_deploy=False,
            _defer_store=True,
        )
        cutover_at = utc_now()
        retired_old, new_binding = self._store.cutover(
            current_binding_id,
            new_binding,
            retired_at=cutover_at,
            single_runtime_enforced=self._single_runtime_enforced,
        )

        return self._forward_replace_result(
            request=request,
            old_binding=retired_old,
            new_binding=new_binding,
            cutover_at=cutover_at,
            replayed=False,
        )

    @staticmethod
    def _forward_replace_result(
        *,
        request: Dict[str, Any],
        old_binding: RuntimeBinding,
        new_binding: RuntimeBinding,
        cutover_at: str,
        replayed: bool,
    ) -> Dict[str, Any]:
        current_binding_id = old_binding.binding_id
        position_lineage = {
            "opened_by_artifact_id": request.get(
                "opened_by_artifact_id", old_binding.artifact_id
            ),
            "prev_binding_id": current_binding_id,
            "prev_artifact_id": old_binding.artifact_id,
            "new_binding_id": new_binding.binding_id,
            "new_artifact_id": new_binding.artifact_id,
            "current_managed_by_binding_id": new_binding.binding_id,
            "cutover_at": cutover_at,
            "note": (
                "Forward same-stage replacement transferred management to the new "
                "binding; opened_by_artifact_id remains immutable."
            ),
        }
        return {
            "operation": "forward_replace",
            "replayed": replayed,
            "old_binding": old_binding.to_dict(),
            "new_binding": new_binding.to_dict(),
            "cutover_at": cutover_at,
            "position_lineage": position_lineage,
        }

    def promote_stage(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Atomically promote one governed binding to the next execution stage."""
        with self._control_lock:
            with self._replace_lock:
                return self._promote_stage_once(request)

    def _promote_stage_once(self, request: Dict[str, Any]) -> Dict[str, Any]:
        current_binding_id = str(request.get("current_binding_id") or "").strip()
        if not current_binding_id:
            raise RuntimeManagerError(
                "current_binding_id is required for stage promotion."
            )
        old_binding = self._store.require(current_binding_id)
        target_stage = str(request.get("target_stage") or "").strip()
        expected_target = {
            DeploymentMode.PAPER.value: DeploymentMode.CANARY.value,
            DeploymentMode.CANARY.value: DeploymentMode.LIVE.value,
        }.get(old_binding.deployment_mode)
        if expected_target is None or target_stage != expected_target:
            raise RuntimeManagerError(
                "Stage promotion must advance exactly one step: paper -> canary "
                "or canary -> live; source is "
                f"{old_binding.deployment_mode!r} and target is {target_stage!r}."
            )

        runtime_id = str(request.get("runtime_id") or old_binding.runtime_id).strip()
        if runtime_id != old_binding.runtime_id:
            raise RuntimeManagerError(
                "Stage promotion must preserve the source runtime_id."
            )
        capital_pool_id = str(request.get("capital_pool_id") or "").strip()
        if capital_pool_id != old_binding.capital_pool_id:
            raise RuntimeManagerError(
                "Stage promotion must preserve the source capital_pool_id."
            )
        persona_binding_id = str(
            request.get("persona_capital_binding_id") or ""
        ).strip()
        if persona_binding_id != old_binding.persona_capital_binding_id:
            raise RuntimeManagerError(
                "Stage promotion must preserve the source PersonaCapitalBinding."
            )
        artifact_pair = (
            str(request.get("artifact_id") or "").strip(),
            str(request.get("artifact_version") or "").strip(),
        )
        if artifact_pair != (
            old_binding.artifact_id,
            old_binding.artifact_version,
        ):
            raise RuntimeManagerError(
                "Stage promotion must preserve the source artifact; use forward "
                "same-stage replacement for an artifact change."
            )

        source_attestation_key = (
            "authoritative_loader_attestation"
            if old_binding.deployment_mode == DeploymentMode.PAPER.value
            else "authoritative_promotion_attestation"
        )
        source_attestation = old_binding.metadata.get(source_attestation_key)
        if not isinstance(source_attestation, Mapping) or source_attestation.get(
            "status"
        ) != "passed":
            raise RuntimeManagerError(
                "Stage promotion source lacks its canonical admission attestation."
            )
        if (
            old_binding.deployment_mode == DeploymentMode.CANARY.value
            and source_attestation.get("target_stage") != DeploymentMode.CANARY.value
        ):
            raise RuntimeManagerError(
                "Canary source promotion attestation does not prove canary admission."
            )

        replay_candidates = [
            binding
            for binding in self._store.find_by_pool(old_binding.capital_pool_id)
            if binding.binding_id != current_binding_id
            and binding.plan_id == str(request.get("plan_id") or "")
            and binding.runtime_id == runtime_id
            and binding.deployment_mode == target_stage
            and binding.execution_mode == target_stage
            and binding.persona_capital_binding_id == persona_binding_id
            and (binding.artifact_id, binding.artifact_version) == artifact_pair
            and binding.status
            in {
                RuntimeBindingStatus.ACTIVE.value,
                RuntimeBindingStatus.PAUSED.value,
            }
            and binding.metadata.get("stage_promotion_parent_binding_id")
            == current_binding_id
        ]
        if len(replay_candidates) > 1:
            raise RuntimeManagerError(
                "Stage promotion recovery found multiple matching child bindings; "
                "manual reconciliation is required."
            )
        if replay_candidates:
            child = replay_candidates[0]
            if old_binding.status == RuntimeBindingStatus.RETIRED.value:
                retired_old = old_binding
                cutover_at = old_binding.retired_at or child.effective_at
            elif old_binding.status == RuntimeBindingStatus.ACTIVE.value:
                cutover_at = utc_now()
                retired_old = self._store.retire(
                    current_binding_id, retired_at=cutover_at
                )
            else:
                raise RuntimeManagerError(
                    "Stage promotion replay source must be active or retired."
                )
            return self._stage_promotion_result(
                old_binding=retired_old,
                new_binding=child,
                cutover_at=cutover_at,
                replayed=True,
            )

        if old_binding.status != RuntimeBindingStatus.ACTIVE.value:
            raise RuntimeManagerError(
                f"Stage promotion requires active source binding; got "
                f"{old_binding.status!r}."
            )

        deploy_request = dict(request)
        deploy_request["runtime_id"] = runtime_id
        metadata = (
            dict(request.get("metadata") or {})
            if isinstance(request.get("metadata"), dict)
            else {}
        )
        metadata["stage_promotion_parent_binding_id"] = current_binding_id
        metadata["stage_promotion_source_stage"] = old_binding.deployment_mode
        metadata["stage_promotion_target_stage"] = target_stage
        deploy_request["metadata"] = metadata
        deploy_request.pop("rollback_parent", None)
        deploy_request.pop("rollback_action_type", None)

        new_binding = self._deploy_once(
            deploy_request,
            _allow_cutover_bypass=True,
            _allow_non_paper_deploy=True,
            _defer_store=True,
        )
        cutover_at = utc_now()
        retired_old, new_binding = self._store.cutover(
            current_binding_id,
            new_binding,
            retired_at=cutover_at,
            single_runtime_enforced=self._single_runtime_enforced,
        )
        return self._stage_promotion_result(
            old_binding=retired_old,
            new_binding=new_binding,
            cutover_at=cutover_at,
            replayed=False,
        )

    @staticmethod
    def _stage_promotion_result(
        *,
        old_binding: RuntimeBinding,
        new_binding: RuntimeBinding,
        cutover_at: str,
        replayed: bool,
    ) -> Dict[str, Any]:
        return {
            "operation": "stage_promotion",
            "replayed": replayed,
            "source_stage": old_binding.deployment_mode,
            "target_stage": new_binding.deployment_mode,
            "old_binding": old_binding.to_dict(),
            "new_binding": new_binding.to_dict(),
            "cutover_at": cutover_at,
            "position_lineage": {
                "opened_by_artifact_id": old_binding.artifact_id,
                "prev_binding_id": old_binding.binding_id,
                "new_binding_id": new_binding.binding_id,
                "current_managed_by_binding_id": new_binding.binding_id,
                "cutover_at": cutover_at,
                "note": (
                    "Governed stage promotion preserved runtime, artifact, capital "
                    "pool, and PersonaCapitalBinding identity."
                ),
            },
        }

    def _prove_paper_rollback_target(
        self,
        request: Dict[str, Any],
        old_binding: RuntimeBinding,
    ) -> tuple[RuntimeBinding, Dict[str, Any], str]:
        """Resolve rollback only to a prior canonically admitted paper binding."""

        replacement_stage = str(
            request.get("replacement_deployment_mode")
            or old_binding.deployment_mode
        )
        if old_binding.deployment_mode != DeploymentMode.PAPER.value or replacement_stage != DeploymentMode.PAPER.value:
            raise RuntimeManagerError(
                "Rollback replacement is paper-only until a target-bound "
                "non-paper rollback authority verifier is available."
            )

        replacement_plan_id = str(request.get("replacement_plan_id") or "")
        replacement_artifact_id = str(
            request.get("replacement_artifact_id") or ""
        )
        replacement_artifact_version = str(
            request.get("replacement_artifact_version") or ""
        )
        replacement_pcb_id = str(
            request.get("replacement_persona_capital_binding_id") or ""
        )
        if replacement_pcb_id != old_binding.persona_capital_binding_id:
            raise RuntimeManagerError(
                "Rollback must preserve the current authoritative "
                "PersonaCapitalBinding identity."
            )

        candidates = [
            candidate
            for candidate in self._store.find_by_plan(replacement_plan_id)
            if candidate.binding_id != old_binding.binding_id
            and candidate.capital_pool_id == old_binding.capital_pool_id
            and candidate.artifact_id == replacement_artifact_id
            and candidate.artifact_version == replacement_artifact_version
            and candidate.deployment_mode == replacement_stage
            and candidate.execution_mode == replacement_stage
            and candidate.persona_capital_binding_id == replacement_pcb_id
            and candidate.status == RuntimeBindingStatus.RETIRED.value
        ]
        if len(candidates) != 1:
            raise RuntimeManagerError(
                "Rollback target must resolve to exactly one retired prior "
                "RuntimeBinding with matching plan/artifact/pool/stage/persona "
                f"identity; found {len(candidates)}."
            )
        prior = candidates[0]
        prior_attestation = prior.metadata.get("authoritative_loader_attestation")
        if not isinstance(prior_attestation, Mapping):
            raise RuntimeManagerError(
                "Rollback prior RuntimeBinding lacks canonical deployment authority proof."
            )
        expected = {
            "status": "passed",
            "authority": "canonical_deployment_registry_governance_capital",
            "plan_id": replacement_plan_id,
            "target_stage": replacement_stage,
            "artifact_id": replacement_artifact_id,
            "artifact_version": replacement_artifact_version,
            "capital_pool_id": old_binding.capital_pool_id,
            "persona_capital_binding_id": replacement_pcb_id,
        }
        mismatches = [
            f"{field} expected {value!r}, got {prior_attestation.get(field)!r}"
            for field, value in expected.items()
            if prior_attestation.get(field) != value
        ]
        digest_fields = (
            "deployment_plan_sha256",
            "registry_entry_sha256",
            "approval_decision_sha256",
            "capital_pool_sha256",
            "capital_admissibility_sha256",
            "persona_capital_binding_sha256",
        )
        mismatches.extend(
            f"{field} is missing or invalid"
            for field in digest_fields
            if not str(prior_attestation.get(field) or "").startswith("sha256:")
            or len(str(prior_attestation.get(field) or "")) != 71
        )
        strategy_id = str(prior_attestation.get("strategy_id") or "")
        if not strategy_id or prior.metadata.get("strategy_id") != strategy_id:
            mismatches.append("verified rollback strategy_id is missing or inconsistent")
        requested_strategy_id = str(
            request.get("replacement_strategy_id")
            or request.get("strategy_id")
            or strategy_id
        )
        if requested_strategy_id != strategy_id:
            mismatches.append(
                "replacement_strategy_id conflicts with prior authority proof"
            )
        requested_scope = str(
            request.get("replacement_allowed_deployment_scope") or ""
        )
        if requested_scope != prior_attestation.get("allowed_deployment_scope"):
            mismatches.append(
                "replacement_allowed_deployment_scope conflicts with prior authority proof"
            )

        current_attestation = request.get("replacement_authority_attestation")
        if not isinstance(current_attestation, Mapping):
            mismatches.append(
                "replacement_authority_attestation current four-owner proof is required"
            )
        else:
            current_expected = {
                **expected,
                "strategy_id": strategy_id,
                "approval_decision_id": prior_attestation.get(
                    "approval_decision_id"
                ),
                "sponsor_persona_id": prior_attestation.get(
                    "sponsor_persona_id"
                ),
                "persona_capital_binding_status": "active",
                "allowed_deployment_scope": requested_scope,
            }
            mismatches.extend(
                f"current {field} expected {value!r}, got {current_attestation.get(field)!r}"
                for field, value in current_expected.items()
                if current_attestation.get(field) != value
            )
            if current_attestation.get("plan_status") not in {
                "approved",
                "executing",
                "executed",
            }:
                mismatches.append(
                    "current plan_status must be approved/executing/executed"
                )
            mismatches.extend(
                f"current {field} is missing or invalid"
                for field in digest_fields
                if not str(current_attestation.get(field) or "").startswith(
                    "sha256:"
                )
                or len(str(current_attestation.get(field) or "")) != 71
            )
        if mismatches:
            raise RuntimeManagerError(
                "Rollback target authority mismatch: " + "; ".join(mismatches)
            )
        return prior, dict(current_attestation), strategy_id

    def rollback(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize a runtime-manager-owned containment rollback."""
        with self._control_lock:
            return self._rollback_once(request)

    def _rollback_once(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a canonical rollback through the runtime-manager.

        Implements the three strategies from ROLLBACK_AND_POSITION_SEMANTICS.md §3:

        replace
            Hot-swap: atomically retire the old binding and create the replacement.
            The existing book is inherited by the new artifact.  Old binding core
            fields are never rewritten — only the status transitions to 'retired'.

        pause_then_replace
            Drain-then-swap: transition old binding active → pending_pause → paused,
            then atomically create the replacement and retire the paused source.
            Cutover occurs only after open orders are stabilised.

        liquidate_then_replace
            Flatten-then-swap: atomically retire the source and create a guarded,
            paused replacement.  The operator confirms zero-position state before
            re-enabling entries.

        Position lineage (ROLLBACK_AND_POSITION_SEMANTICS.md §7):
            opened_by_artifact_id is immutable (carried from the original opener).
            current_managed_by_binding_id is updated to the new binding_id in the
            returned position_lineage record once cutover completes.
            For liquidate_then_replace the position_lineage note flags that
            ownership must not be transferred until zero-position confirmation.

        Returns
        -------
        dict with keys:
            action_type        : str — strategy used
            old_binding        : dict — the retired RuntimeBinding
            new_binding        : dict — the replacement RuntimeBinding
            cutover_at         : str — ISO-8601 UTC timestamp of cutover
            position_lineage   : dict — lineage record per §7 semantics
        """
        current_binding_id = request.get("current_binding_id", "")
        if not current_binding_id:
            raise RuntimeManagerError("current_binding_id is required for rollback.")

        action_type = request.get("action_type", "replace")
        try:
            RollbackActionType(action_type)
        except ValueError:
            raise RuntimeManagerError(
                f"Unknown rollback action_type={action_type!r}. "
                f"Must be one of {[e.value for e in RollbackActionType]}."
            )

        replacement_start_paused = request.get("replacement_start_paused", False)

        # Verify the source and the exact governed fallback before considering
        # either a new cutover or response-loss recovery.
        old_binding = self._store.require(current_binding_id)
        prior_binding, rollback_attestation, rollback_strategy_id = (
            self._prove_paper_rollback_target(request, old_binding)
        )

        if old_binding.is_terminal():
            if old_binding.status != RuntimeBindingStatus.RETIRED.value:
                raise RuntimeManagerError(
                    f"Cannot recover rollback for binding {current_binding_id!r}: "
                    f"terminal status={old_binding.status!r}."
                )
            recovered = [
                candidate
                for candidate in self._store.find_by_pool(
                    old_binding.capital_pool_id
                )
                if candidate.rollback_parent == current_binding_id
                and candidate.rollback_action_type == action_type
                and candidate.plan_id == prior_binding.plan_id
                and candidate.artifact_id == prior_binding.artifact_id
                and candidate.artifact_version == prior_binding.artifact_version
                and candidate.deployment_mode == prior_binding.deployment_mode
                and candidate.execution_mode == prior_binding.execution_mode
                and candidate.persona_capital_binding_id
                == prior_binding.persona_capital_binding_id
                and candidate.status
                in {
                    RuntimeBindingStatus.ACTIVE.value,
                    RuntimeBindingStatus.PAUSED.value,
                }
                and candidate.metadata.get(
                    "rollback_authority_source_binding_id"
                )
                == prior_binding.binding_id
                and candidate.metadata.get("strategy_id")
                == rollback_strategy_id
            ]
            if len(recovered) != 1:
                raise RuntimeManagerError(
                    "Rollback response-loss recovery requires exactly one "
                    f"authoritative child; found {len(recovered)}."
                )
            child = recovered[0]
            child_attestation = child.metadata.get(
                "authoritative_loader_attestation"
            )
            digest_fields = (
                "deployment_plan_sha256",
                "registry_entry_sha256",
                "approval_decision_sha256",
                "capital_pool_sha256",
                "capital_admissibility_sha256",
                "persona_capital_binding_sha256",
            )
            identity_fields = (
                "plan_id",
                "target_stage",
                "artifact_id",
                "artifact_version",
                "strategy_id",
                "approval_decision_id",
                "capital_pool_id",
                "sponsor_persona_id",
                "persona_capital_binding_id",
                "persona_capital_binding_status",
                "allowed_deployment_scope",
            )
            if not isinstance(child_attestation, Mapping) or any(
                child_attestation.get(field) != rollback_attestation.get(field)
                for field in (*identity_fields, *digest_fields)
            ):
                raise RuntimeManagerError(
                    "Rollback response-loss child authority differs from the "
                    "current canonical fallback proof."
                )
            return self._rollback_result(
                request=request,
                action_type=action_type,
                old_binding=old_binding,
                new_binding=child,
                cutover_at=old_binding.retired_at or child.effective_at,
                replacement_start_paused=(
                    child.status == RuntimeBindingStatus.PAUSED.value
                ),
                replayed=True,
            )

        safe_mode = self._kill_switch.safe_mode_for(
            old_binding.capital_pool_id
        ).value
        containment_requires_paused_replacement = (
            safe_mode not in _FORWARD_DEPLOY_ALLOWED_SAFE_MODES
        )
        replacement_start_paused = bool(
            replacement_start_paused or containment_requires_paused_replacement
        )

        cutover_at = utc_now()

        replacement_metadata = (
            dict(request.get("replacement_metadata") or {})
            if isinstance(request.get("replacement_metadata"), Mapping)
            else {}
        )
        replacement_metadata["authoritative_loader_attestation"] = (
            rollback_attestation
        )
        replacement_metadata["strategy_id"] = rollback_strategy_id
        replacement_metadata["rollback_authority_source_binding_id"] = (
            prior_binding.binding_id
        )

        # Build the replacement deploy request only from the proven prior
        # RuntimeBinding. Caller booleans/status strings never become proof.
        deploy_req: Dict[str, Any] = {
            "plan_id": prior_binding.plan_id,
            "plan_status": rollback_attestation["plan_status"],
            "target_stage": prior_binding.deployment_mode,
            "artifact_id": prior_binding.artifact_id,
            "artifact_version": prior_binding.artifact_version,
            "capital_pool_id": old_binding.capital_pool_id,
            "persona_capital_binding_id": prior_binding.persona_capital_binding_id,
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": rollback_attestation[
                "allowed_deployment_scope"
            ],
            "loader_checks_passed": True,
            "runtime_id": request.get("replacement_runtime_id"),
            "rollback_parent": current_binding_id,
            "rollback_action_type": action_type,
            "metadata": replacement_metadata,
            "strategy_id": rollback_strategy_id,
        }

        if action_type == RollbackActionType.REPLACE.value:
            # Hot-swap per L1 §3.1 and §9: validate the replacement first, then
            # persist child creation and source retirement as one snapshot.
            # Per §8, old core fields are not rewritten; only status -> retired.
            new_binding = self.deploy(
                deploy_req,
                _allow_cutover_bypass=True,
                _allow_activation_gate_bypass=True,
                _allow_safe_mode_bypass=True,
                _allow_non_paper_deploy=False,
                _start_paused=replacement_start_paused,
                _defer_store=True,
            )
            _, new_binding = self._store.cutover(
                current_binding_id,
                new_binding,
                retired_at=cutover_at,
                single_runtime_enforced=self._single_runtime_enforced,
            )

        elif action_type == RollbackActionType.PAUSE_THEN_REPLACE.value:
            # Step 1: Drain — active → pending_pause → paused
            if old_binding.status == RuntimeBindingStatus.ACTIVE.value:
                self._store.transition_status(
                    current_binding_id, RuntimeBindingStatus.PENDING_PAUSE.value
                )
                self._store.transition_status(
                    current_binding_id, RuntimeBindingStatus.PAUSED.value
                )
            elif old_binding.status == RuntimeBindingStatus.PENDING_PAUSE.value:
                self._store.transition_status(
                    current_binding_id, RuntimeBindingStatus.PAUSED.value
                )
            elif old_binding.status != RuntimeBindingStatus.PAUSED.value:
                raise RuntimeManagerError(
                    f"pause_then_replace requires the current binding to be active, "
                    f"pending_pause, or paused; current status={old_binding.status!r}."
                )
            # Step 2: Create replacement while old is paused.
            # single-runtime rule does not fire because the old binding is no longer active.
            new_binding = self.deploy(
                deploy_req,
                _allow_activation_gate_bypass=True,
                _allow_safe_mode_bypass=True,
                _allow_non_paper_deploy=False,
                _start_paused=replacement_start_paused,
                _defer_store=True,
            )
            # Step 3: Persist child + retired source as one cutover snapshot.
            _, new_binding = self._store.cutover(
                current_binding_id,
                new_binding,
                retired_at=cutover_at,
                single_runtime_enforced=self._single_runtime_enforced,
            )

        elif action_type == RollbackActionType.LIQUIDATE_THEN_REPLACE.value:
            # Build a guarded replacement, then persist it with source
            # retirement as one snapshot. No observer or restart can see a
            # partial create/retire window.
            replacement_start_paused = True
            new_binding = self.deploy(
                deploy_req,
                _allow_cutover_bypass=True,
                _allow_activation_gate_bypass=True,
                _allow_safe_mode_bypass=True,
                _allow_non_paper_deploy=False,
                _start_paused=True,
                _defer_store=True,
            )
            _, new_binding = self._store.cutover(
                current_binding_id,
                new_binding,
                retired_at=cutover_at,
                single_runtime_enforced=self._single_runtime_enforced,
            )

        else:
            # Unreachable after enum validation above, but kept for safety.
            raise RuntimeManagerError(f"Unhandled action_type: {action_type!r}")

        return self._rollback_result(
            request=request,
            action_type=action_type,
            old_binding=self._store.require(current_binding_id),
            new_binding=new_binding,
            cutover_at=cutover_at,
            replacement_start_paused=replacement_start_paused,
            replayed=False,
        )

    @staticmethod
    def _rollback_result(
        *,
        request: Dict[str, Any],
        action_type: str,
        old_binding: RuntimeBinding,
        new_binding: RuntimeBinding,
        cutover_at: str,
        replacement_start_paused: bool,
        replayed: bool,
    ) -> Dict[str, Any]:
        """Build the stable rollback receipt for first execution or replay."""
        current_binding_id = old_binding.binding_id
        opened_by_artifact_id = request.get(
            "opened_by_artifact_id", old_binding.artifact_id
        )
        lineage_note: str
        if action_type == RollbackActionType.LIQUIDATE_THEN_REPLACE.value:
            lineage_note = (
                "liquidate_then_replace: positions must be confirmed zero before "
                "current_managed_by_binding_id transfer is valid per "
                "ROLLBACK_AND_POSITION_SEMANTICS.md §7. "
                "opened_by_artifact_id is immutable."
            )
        else:
            lineage_note = (
                "current_managed_by_binding_id updated to new binding after cutover. "
                "opened_by_artifact_id is immutable per ROLLBACK_AND_POSITION_SEMANTICS.md §7."
            )

        # §7: current_managed_by_binding_id may only update once the replacement
        # binding is the active owner.  For liquidate_then_replace with
        # replacement_start_paused=True the new binding starts paused, so the
        # position is not yet under active management — keep the old binding ID
        # as the current owner until zero-position is confirmed and the operator
        # activates the new binding.
        if (
            action_type == RollbackActionType.LIQUIDATE_THEN_REPLACE.value
            and replacement_start_paused
        ):
            lineage_current_owner = current_binding_id
        else:
            lineage_current_owner = new_binding.binding_id

        position_lineage = {
            "opened_by_artifact_id": opened_by_artifact_id,
            "prev_binding_id": current_binding_id,
            "prev_artifact_id": old_binding.artifact_id,
            "new_binding_id": new_binding.binding_id,
            "new_artifact_id": new_binding.artifact_id,
            "current_managed_by_binding_id": lineage_current_owner,
            "cutover_at": cutover_at,
            "note": lineage_note,
        }

        return {
            "action_type": action_type,
            "replayed": replayed,
            "old_binding": old_binding.to_dict(),
            "new_binding": new_binding.to_dict(),
            "cutover_at": cutover_at,
            "position_lineage": position_lineage,
        }

    def transition(
        self,
        binding_id: str,
        new_status: str,
        *,
        metadata_patch: Optional[Dict[str, Any]] = None,
    ) -> RuntimeBinding:
        """Transition a binding to a new status via the allowed state machine."""
        with self._control_lock:
            binding = self._store.require(binding_id)
            if new_status == RuntimeBindingStatus.ACTIVE.value:
                safe_mode = self._kill_switch.safe_mode_for(
                    binding.capital_pool_id
                ).value
                if safe_mode not in _FORWARD_DEPLOY_ALLOWED_SAFE_MODES:
                    raise RuntimeManagerError(
                        "RuntimeBinding activation is blocked while kill-switch "
                        f"safe_mode={safe_mode!r}; complete governed safe-mode "
                        "recovery before resuming runtime."
                    )
            return self._store.transition_status(
                binding_id,
                new_status,
                metadata_patch=metadata_patch,
            )

    # ------------------------------------------------------------------ #
    # Kill-switch fast path (KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY)  #
    # ------------------------------------------------------------------ #

    def _load_ks_state(self) -> None:
        """Best-effort restore of kill-switch state from the durable snapshot."""
        if not self._ks_store_path or not self._ks_store_path.exists():
            return

        try:
            data = json.loads(self._ks_store_path.read_text())
            self._kill_switch.load_state(data)
            loaded, recovery_audits = load_command_recovery_entries(
                data.get("foundation_idempotency") or {},
                owner_service="runtime-manager",
                operation_type=_KILL_SWITCH_FOUNDATION_OPERATION,
            )
            self._foundation_idempotency = loaded
            self._foundation_recovery_audit = list(data.get("foundation_recovery_audit") or [])
            if recovery_audits:
                self._foundation_recovery_audit.extend(audit.to_dict() for audit in recovery_audits)
                self._persist_ks_state()
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, KillSwitchError):
            # Do not let a torn/corrupt sidecar brick the whole runtime-manager.
            # Quarantine the bad file and fall back to empty controller state.
            corrupt_path = self._ks_store_path.with_name(
                f"{self._ks_store_path.name}.corrupt.{uuid.uuid4().hex}.json"
            )
            try:
                self._ks_store_path.replace(corrupt_path)
            except OSError:
                pass
            self._foundation_recovery_audit.append(
                CommandRecoveryAudit.record(
                    owner_service="runtime-manager",
                    action_type=CommandRecoveryAction.QUARANTINED,
                    reason="quarantined corrupt kill-switch durable snapshot during startup",
                    metadata={"quarantine_path": str(corrupt_path)},
                ).to_dict()
            )
            self._persist_ks_state()
            return

        # Recovery performs RuntimeBinding writes. Keep it outside the corrupt
        # snapshot handler so a persistence failure prevents service startup
        # instead of quarantining a valid emergency ledger and serving with an
        # active runtime under fail-closed safe mode.
        self._recover_executing_kill_switch_actions()

    def _persist_ks_state(self) -> None:
        """Write kill-switch safe-mode and audit state to the durable store."""
        if self._ks_store_path:
            self._ks_store_path.parent.mkdir(parents=True, exist_ok=True)
            state = self._kill_switch.dump_state()
            state["foundation_idempotency"] = self._foundation_idempotency
            state["foundation_recovery_audit"] = self._foundation_recovery_audit
            payload = json.dumps(state, indent=2)
            tmp_path = self._ks_store_path.with_name(
                f"{self._ks_store_path.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                tmp_path.write_text(payload)
                os.replace(tmp_path, self._ks_store_path)
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()

    def _record_ks_recovery_audit(
        self,
        *,
        action_type: CommandRecoveryAction,
        reason: str,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        self._foundation_recovery_audit.append(
            CommandRecoveryAudit.record(
                owner_service="runtime-manager",
                action_type=action_type,
                reason=reason,
                idempotency_key=idempotency_key,
                trace_id=trace_id,
                metadata=metadata or {},
            ).to_dict()
        )

    def _store_ks_idempotency_record(
        self,
        record: IdempotencyRecord,
        *,
        result: Dict[str, Any] | None = None,
        persist: bool = True,
    ) -> None:
        self._foundation_idempotency[record.idempotency_key] = command_recovery_entry(
            record,
            result=result,
        )
        if persist:
            self._persist_ks_state()

    def _recover_executing_kill_switch_actions(self) -> None:
        """Synchronously finish durable emergency actions before serving traffic.

        Safe mode and an EXECUTING command are committed before the binding
        mutation.  A process crash in that interval must not leave a restarted
        service reporting an active runtime under paused safe mode.  Replaying
        the persisted command (never redispatching it) closes that interval and
        preserves the original Foundation identities.
        """
        executing: list[tuple[str, IdempotencyRecord, Dict[str, Any]]] = []
        for key, entry in list(self._foundation_idempotency.items()):
            record = idempotency_record_from_entry(entry)
            if record.status != IdempotencyStatus.EXECUTING:
                continue
            result = entry.get("result")
            if not isinstance(result, Mapping) or not isinstance(
                result.get("command"), Mapping
            ):
                raise RuntimeManagerError(
                    "Cannot start with an EXECUTING kill-switch record that "
                    f"lacks a durable command: idempotency_key={key!r}."
                )
            executing.append((key, record, dict(result)))

        for _, record, result in sorted(
            executing,
            key=lambda item: (str(item[1].first_seen_at), item[0]),
        ):
            self._resume_durable_kill_switch_action(record, result)

    def _resume_durable_kill_switch_action(
        self,
        record: IdempotencyRecord,
        persisted_result: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Resume one persisted EXECUTING command without changing its identity."""
        recovered = json.loads(json.dumps(dict(persisted_result)))
        command = recovered.get("command")
        foundation = recovered.get("foundation")
        if not isinstance(command, Mapping) or not isinstance(foundation, Mapping):
            raise RuntimeManagerError(
                "Durable EXECUTING kill-switch result is missing command or "
                f"Foundation context for idempotency_key={record.idempotency_key!r}."
            )

        self._record_ks_recovery_audit(
            action_type=CommandRecoveryAction.REPLAY_RESUMED,
            reason=(
                "resumed kill-switch binding follow-through from durable "
                "executing record"
            ),
            idempotency_key=record.idempotency_key,
            trace_id=record.trace_id,
            metadata={"command_id": command.get("command_id")},
        )
        binding_action = self._execute_kill_switch_binding_action(
            self._durable_kill_switch_command(dict(command))
        )
        if binding_action is None:
            nonterminal = [
                binding
                for binding in self._store.find_by_pool(
                    str(command.get("capital_pool_id") or "")
                )
                if not binding.is_terminal()
            ]
            if nonterminal:
                raise RuntimeManagerError(
                    "Durable kill-switch recovery could not contain every "
                    "non-terminal RuntimeBinding; refusing to serve traffic."
                )
            recovered["binding_action"] = None
            recovered["telemetry_ack"] = self._build_kill_switch_telemetry_ack(
                command=dict(command),
                audit_entry=dict(recovered.get("audit_entry") or {}),
                safe_mode_after=str(recovered.get("safe_mode_after") or ""),
                binding_action=None,
                foundation_context=dict(foundation),
            )
            recovered["idempotent_replay"] = True
            # No authoritative RuntimeBinding follow-through means containment
            # is not acknowledged.  Keep the command EXECUTING for a later
            # retry and durably retain the recovery audit.
            self._store_ks_idempotency_record(record, result=recovered)
            return recovered

        succeeded = record.with_status(
            IdempotencyStatus.SUCCEEDED,
            result_ref=f"kill_switch:{command.get('command_id')}",
        )
        durable_foundation = dict(foundation)
        durable_foundation["idempotency_record"] = succeeded.to_dict()
        recovered["foundation"] = durable_foundation
        recovered["binding_action"] = binding_action
        recovered["telemetry_ack"] = self._build_kill_switch_telemetry_ack(
            command=dict(command),
            audit_entry=dict(recovered.get("audit_entry") or {}),
            safe_mode_after=str(recovered.get("safe_mode_after") or ""),
            binding_action=binding_action,
            foundation_context=durable_foundation,
        )
        recovered["idempotent_replay"] = True
        self._store_ks_idempotency_record(succeeded, result=recovered)
        return recovered

    def execute_kill_switch(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize emergency containment against deploy/replace transitions."""
        with self._control_lock:
            return self._execute_kill_switch_once(request)

    def _execute_kill_switch_once(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch an emergency kill-switch command via the runtime-manager fast path.

        This is the authorised fast-path entry point defined in
        KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md §2–§8.

        The path is:
            caller → execute_kill_switch() → KillSwitchController.dispatch()
                                           → KillSwitchOutcome (command + audit)

        Every call produces an immutable KillSwitchAuditEntry regardless of
        the emergency class, preserving auditability on the fast path (§5).

        For REPLACE-type actions the caller must supply fallback_artifact_id
        and fallback_artifact_version.

        Parameters (plain dict)
        -----------------------
        reason              : str   — HardTriggerReason or SoftTriggerReason value
        capital_pool_id     : str   — pool under emergency
        actor_id            : str   — operator or system component

        Optional
        --------
        binding_id          : str   — active RuntimeBinding being targeted
        severity            : int   — numeric severity (1 = highest)
        action_override     : str   — KillSwitchActionType value; overrides §7 matrix
        fallback_artifact_id    : str   — required when action is REPLACE
        fallback_artifact_version : str — required when action is REPLACE
        context             : dict  — arbitrary metadata

        Returns
        -------
        dict with keys:
            command     : dict — KillSwitchCommand serialised
            audit_entry : dict — KillSwitchAuditEntry serialised
            safe_mode_after : str — SafeModeState after dispatch
        """
        from services.runtime_manager.kill_switch_controller import EmergencyTrigger, KillSwitchActionType as KSAT  # noqa: E402

        foundation_context = _build_kill_switch_foundation_context(request)
        idempotency_record: IdempotencyRecord = foundation_context["idempotency_record"]
        existing = self._foundation_idempotency.get(idempotency_record.idempotency_key)
        if existing:
            existing_record = idempotency_record_from_entry(existing)
            if existing_record.request_hash != idempotency_record.request_hash:
                foundation_error = _foundation_idempotency_conflict(
                    foundation_context,
                    existing_command_id=str((existing.get("result") or {}).get("command", {}).get("command_id") or ""),
                )
                raise RuntimeManagerError(json.dumps(foundation_error.to_dict(), sort_keys=True))
            existing_result = json.loads(json.dumps(existing.get("result") or {}))
            if existing_record.status == IdempotencyStatus.SUCCEEDED:
                replayed = existing_result
                replayed["idempotent_replay"] = True
                return replayed
            if existing_record.status == IdempotencyStatus.EXECUTING and existing_result.get("command"):
                return self._resume_durable_kill_switch_action(
                    existing_record, existing_result
                )
            if existing_record.status != IdempotencyStatus.RESERVED:
                self._record_ks_recovery_audit(
                    action_type=CommandRecoveryAction.QUARANTINED,
                    reason=f"quarantined unsupported kill-switch idempotency status {existing_record.status.value}",
                    idempotency_key=existing_record.idempotency_key,
                    trace_id=existing_record.trace_id,
                )
                self._foundation_idempotency.pop(existing_record.idempotency_key, None)
                self._persist_ks_state()

        self._store_ks_idempotency_record(idempotency_record)

        reason = request.get("reason", "")
        capital_pool_id = request.get("capital_pool_id", "")
        actor_id = request.get("actor_id", "")
        context = dict(request.get("context") or {})
        if request.get("action_override") == KillSwitchActionType.REPLACE.value or (
            not request.get("action_override")
            and reason == SoftTriggerReason.LOADER_ANOMALY_NO_BREACH.value
        ):
            context.update(
                {
                    "requested_action": KillSwitchActionType.REPLACE.value,
                    "replacement_disposition": "paused_fail_closed",
                    "replacement_blocked_reason": (
                        "target-bound canonical deployment authority is absent"
                    ),
                }
            )
        trace_context: TraceContext = foundation_context["trace_context"]
        context.update(
            {
                "foundation_trace_id": trace_context.trace_id,
                "foundation_correlation_id": trace_context.correlation_id,
                "foundation_command_id": foundation_context["command_envelope"].command_id,
            }
        )

        try:
            trigger = EmergencyTrigger(
                reason=reason,
                capital_pool_id=capital_pool_id,
                actor_id=actor_id,
                binding_id=request.get("binding_id"),
                severity=request.get("severity"),
                context=context,
            )
        except KillSwitchError as exc:
            raise RuntimeManagerError(f"Invalid kill-switch trigger: {exc}") from exc

        action_override = None
        if request.get("action_override"):
            try:
                action_override = KSAT(request["action_override"])
            except ValueError as exc:
                raise RuntimeManagerError(
                    f"Unknown action_override={request['action_override']!r}: {exc}"
                ) from exc

        # Emergency containment cannot manufacture a new RuntimeBinding from
        # caller-supplied fallback strings. Until kill-switch dispatch carries
        # a target-bound four-owner admission record, REPLACE is an audited
        # request for PAUSE and creates no replacement binding.
        if action_override == KSAT.REPLACE or (
            action_override is None
            and reason == SoftTriggerReason.LOADER_ANOMALY_NO_BREACH.value
        ):
            action_override = KSAT.PAUSE

        try:
            outcome = self._kill_switch.dispatch(
                trigger,
                action_override=action_override,
                fallback_artifact_id=request.get("fallback_artifact_id"),
                fallback_artifact_version=request.get("fallback_artifact_version"),
            )
        except KillSwitchError as exc:
            raise RuntimeManagerError(f"Kill-switch dispatch failed: {exc}") from exc

        executing_record = idempotency_record.with_status(IdempotencyStatus.EXECUTING)
        foundation_context["idempotency_record"] = executing_record
        executing_result = outcome.to_dict()
        executing_result["foundation"] = _serialize_foundation_context(foundation_context)
        self._store_ks_idempotency_record(executing_record, result=executing_result)

        # Execute the binding action against RuntimeBinding — runtime-manager is the
        # authoritative executor for pause / liquidate / replace / terminate.
        # (KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY §5.2)
        binding_action = self._execute_kill_switch_binding_action(outcome.command)
        if binding_action is None:
            foundation_context["idempotency_record"] = executing_record
            result = outcome.to_dict()
            result["foundation"] = _serialize_foundation_context(foundation_context)
            result["binding_action"] = None
            result["telemetry_ack"] = self._build_kill_switch_telemetry_ack(
                command=result["command"],
                audit_entry=result["audit_entry"],
                safe_mode_after=result["safe_mode_after"],
                binding_action=None,
                foundation_context=foundation_context,
            )
            self._store_ks_idempotency_record(executing_record, result=result)
            return result
        idempotency_record = executing_record.with_status(
            IdempotencyStatus.SUCCEEDED,
            result_ref=f"kill_switch:{outcome.command.command_id}",
        )
        foundation_context["idempotency_record"] = idempotency_record
        result = outcome.to_dict()
        result["foundation"] = _serialize_foundation_context(foundation_context)
        result["binding_action"] = binding_action
        result["telemetry_ack"] = self._build_kill_switch_telemetry_ack(
            command=result["command"],
            audit_entry=result["audit_entry"],
            safe_mode_after=result["safe_mode_after"],
            binding_action=binding_action,
            foundation_context=foundation_context,
        )
        # Persist audit entry, safe-mode, and idempotency before acknowledging
        # the command (contract §11.2: durable write must precede ack).
        self._store_ks_idempotency_record(idempotency_record, result=result)
        return result

    def _build_kill_switch_telemetry_ack(
        self,
        *,
        command: Dict[str, Any],
        audit_entry: Dict[str, Any],
        safe_mode_after: str,
        binding_action: Optional[Dict[str, Any]],
        foundation_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build the telemetry acknowledgement for kill-switch runtime follow-through.

        The ack is intentionally produced only after runtime-manager writes the
        RuntimeBinding side effect. If that side effect is missing, the ack is
        present but fail-closed so callers cannot mistake a UI/audit-only
        kill-switch dispatch for runtime risk-off.
        """
        binding = (binding_action or {}).get("binding") if binding_action else None
        replacement = (binding_action or {}).get("replacement_binding") if binding_action else None
        final_binding = replacement or binding
        runtime_state_recorded = bool(final_binding and final_binding.get("status"))
        capital_state_recorded = bool(
            binding_action
            and (
                binding_action.get("action")
                in {
                    KillSwitchActionType.PAUSE.value,
                    KillSwitchActionType.RISK_OFF.value,
                    KillSwitchActionType.LIQUIDATE.value,
                    KillSwitchActionType.REPLACE.value,
                    KillSwitchActionType.TERMINATE.value,
                }
            )
        )
        ack_received = bool(runtime_state_recorded and capital_state_recorded)
        command_id = str(command.get("command_id") or "")
        audit_id = str(audit_entry.get("audit_id") or "")
        foundation_trace = foundation_context["trace_context"]
        if isinstance(foundation_trace, Mapping):
            trace_id = str(foundation_trace.get("trace_id") or "")
            correlation_id = str(foundation_trace.get("correlation_id") or "")
        else:
            trace_id = foundation_trace.trace_id
            correlation_id = foundation_trace.correlation_id
        foundation_audit_action = foundation_context.get("audit_action")
        if isinstance(foundation_audit_action, Mapping):
            audit_action_ref = foundation_audit_action.get("action_id")
        else:
            audit_action_ref = (
                foundation_audit_action.action_id
                if foundation_audit_action is not None
                else None
            )
        return {
            "ack_id": f"ks-telemetry-ack:{command_id}",
            "ack_status": "acknowledged" if ack_received else "fail_closed",
            "ack_received": ack_received,
            "ack_required": True,
            "fail_closed": not ack_received,
            "event_type": "kill_switch_action",
            "telemetry_event_type": "kill_switch_action",
            "ack_version": _KILL_SWITCH_TELEMETRY_ACK_VERSION,
            "command_id": command_id,
            "audit_id": audit_id,
            "trace_id": trace_id,
            "correlation_id": correlation_id,
            "capital_pool_id": command.get("capital_pool_id"),
            "binding_id": (binding or {}).get("binding_id") or command.get("binding_id"),
            "runtime_binding_id": (final_binding or {}).get("binding_id"),
            "runtime_id": (final_binding or {}).get("runtime_id"),
            "action_type": command.get("action_type"),
            "safe_mode_after": safe_mode_after,
            "runtime_status_after": (final_binding or {}).get("status"),
            "runtime_state_recorded": runtime_state_recorded,
            "capital_state_recorded": capital_state_recorded,
            "audit_action_ref": audit_action_ref,
            "reason": (
                "runtime binding follow-through recorded before ack"
                if ack_received
                else "runtime binding follow-through missing; command remains fail-closed"
            ),
        }

    def _execute_kill_switch_binding_action(
        self, command: Any
    ) -> Optional[Dict[str, Any]]:
        """Execute the RuntimeBinding state write for a dispatched kill-switch command.

        Resolves the target binding via command.binding_id, falling back to the
        active binding for command.capital_pool_id.  Executes:
            PAUSE / RISK_OFF  → pending_pause → paused
            LIQUIDATE / TERMINATE → retire immediately
            REPLACE → fail-closed pause. New bindings require a target-bound
                      four-owner admission record and cannot be fabricated by
                      the emergency command surface.

        Returns a dict with 'action', 'binding' (old, retired/paused), and for
        REPLACE also 'replacement_binding' (new active binding).  Returns None
        when no live binding is found for the target pool.
        """
        action_type = getattr(command, "action_type", None)
        requested_binding_id = getattr(command, "binding_id", None)

        try:
            # A response-loss replay of REPLACE must recover the exact child
            # before resolving the pool's current active owner.  Otherwise the
            # existing child could be replaced a second time.
            if action_type == KillSwitchActionType.REPLACE.value:
                replacement = self._find_kill_switch_replacement_binding(
                    command_id=getattr(command, "command_id", ""),
                    old_binding_id=requested_binding_id,
                    fallback_artifact_id=getattr(
                        command, "fallback_artifact_id", ""
                    ),
                    fallback_artifact_version=getattr(
                        command, "fallback_artifact_version", ""
                    ),
                )
                if replacement is not None:
                    old = self._store.get(replacement.rollback_parent or "")
                    if old is None:
                        return None
                    if not old.is_terminal():
                        old = self._store.retire(old.binding_id, retired_at=utc_now())
                    return {
                        "action": action_type,
                        "binding": old.to_dict(),
                        "replacement_binding": replacement.to_dict(),
                    }

            b = (
                self._store.get(requested_binding_id)
                if requested_binding_id
                else None
            )
            if b is not None and b.capital_pool_id != command.capital_pool_id:
                return None

            active = self._store.get_active_for_pool(command.capital_pool_id)
            if active is not None and (
                b is None
                or b.binding_id != active.binding_id
                and b.status != RuntimeBindingStatus.ACTIVE.value
            ):
                # The requested binding may have been retired by a rollback
                # immediately before the kill acquired the control lock.  Kill
                # the pool's authoritative active owner, not the stale lineage
                # record named by the queued command.
                b = active
            elif active is None and (b is None or b.is_terminal()):
                # A rollback performed under non-normal safe mode creates its
                # replacement paused.  A durable/replayed kill may still name
                # the retired source, so resolve the sole non-terminal pool
                # owner instead of falsely reporting that no runtime followed
                # through.  Ambiguity remains fail-closed.
                nonterminal = [
                    candidate
                    for candidate in self._store.find_by_pool(
                        command.capital_pool_id
                    )
                    if candidate.status
                    in {
                        RuntimeBindingStatus.ACTIVE.value,
                        RuntimeBindingStatus.PENDING_PAUSE.value,
                        RuntimeBindingStatus.PAUSED.value,
                    }
                ]
                if not nonterminal and b is not None and b.is_terminal():
                    # A crash may happen after a terminate/liquidate write but
                    # before the success ledger.  With no non-terminal pool
                    # owner, the persisted terminal target is already a
                    # truthful, stronger containment result for every emergency
                    # action and may be acknowledged idempotently.
                    return {
                        "action": action_type,
                        "binding": b.to_dict(),
                        "already_contained": True,
                    }
                if len(nonterminal) != 1:
                    return None
                b = nonterminal[0]
            if b is None:
                return None
            binding_id = b.binding_id

            if b.is_terminal():
                return None
            if action_type in (
                KillSwitchActionType.PAUSE.value,
                KillSwitchActionType.RISK_OFF.value,
            ):
                # Drain: active → pending_pause → paused
                if b.status == RuntimeBindingStatus.ACTIVE.value:
                    self._store.transition_status(
                        binding_id, RuntimeBindingStatus.PENDING_PAUSE.value
                    )
                    b = self._store.require(binding_id)
                if b.status == RuntimeBindingStatus.PENDING_PAUSE.value:
                    updated = self._store.transition_status(
                        binding_id, RuntimeBindingStatus.PAUSED.value
                    )
                elif b.status == RuntimeBindingStatus.PAUSED.value:
                    # A prior kill may have won the race before this compensation
                    # event arrived.  Treat the authoritative paused state as an
                    # idempotent success so telemetry acknowledgement remains
                    # fail-closed and truthful instead of reporting no side effect.
                    updated = b
                else:
                    return None
                return {"action": action_type, "binding": updated.to_dict()}

            elif action_type == KillSwitchActionType.REPLACE.value:
                # Durable commands admitted by an older version may still say
                # REPLACE. Contain them without creating an unverified child.
                if b.status == RuntimeBindingStatus.ACTIVE.value:
                    self._store.transition_status(
                        binding_id, RuntimeBindingStatus.PENDING_PAUSE.value
                    )
                    b = self._store.require(binding_id)
                if b.status == RuntimeBindingStatus.PENDING_PAUSE.value:
                    contained = self._store.transition_status(
                        binding_id, RuntimeBindingStatus.PAUSED.value
                    )
                elif b.status == RuntimeBindingStatus.PAUSED.value:
                    contained = b
                else:
                    return None
                return {
                    "action": action_type,
                    "binding": contained.to_dict(),
                    "replacement_blocked_reason": (
                        "kill-switch replacement lacks target-bound canonical "
                        "deployment authority; runtime paused fail-closed"
                    ),
                }

            else:
                # LIQUIDATE, TERMINATE — retire immediately
                updated = self._store.retire(binding_id, retired_at=utc_now())
                return {"action": action_type, "binding": updated.to_dict()}

        except (RuntimeBindingError, RuntimeManagerError):
            # Binding may already be terminal; do not mask the dispatch outcome
            return None

    def _find_kill_switch_replacement_binding(
        self,
        *,
        command_id: str,
        old_binding_id: str | None,
        fallback_artifact_id: str,
        fallback_artifact_version: str,
    ) -> Optional[RuntimeBinding]:
        """Return an existing replacement for a replayed kill-switch REPLACE command."""
        plan_id = f"ks-replace-{command_id}"
        candidates = [
            candidate for candidate in self._store.find_by_plan(plan_id)
            if (old_binding_id is None or candidate.rollback_parent == old_binding_id)
            and candidate.rollback_action_type == "replace"
            and candidate.artifact_id == fallback_artifact_id
            and candidate.artifact_version == fallback_artifact_version
        ]
        active_candidates = [
            candidate for candidate in candidates
            if candidate.status == RuntimeBindingStatus.ACTIVE.value
        ]
        if active_candidates:
            return active_candidates[0]
        return candidates[0] if candidates else None

    @staticmethod
    def _durable_kill_switch_command(command: Dict[str, Any]) -> SimpleNamespace:
        """Rebuild a persisted command dict while restoring omitted optional fields."""
        durable_command = dict(command)
        durable_command.setdefault("binding_id", None)
        durable_command.setdefault("fallback_artifact_id", None)
        durable_command.setdefault("fallback_artifact_version", None)
        durable_command.setdefault("metadata", {})
        return SimpleNamespace(**durable_command)

    def get_safe_mode(self, capital_pool_id: str) -> str:
        """Return the current SafeModeState for a capital pool (NORMAL if unknown)."""
        return self._kill_switch.safe_mode_for(capital_pool_id).value

    def advance_safe_mode(
        self,
        capital_pool_id: str,
        target_state: str,
        actor_id: str,
        note: Optional[str] = None,
    ) -> str:
        """Serialize governance recovery state against deploy/rollback writes."""
        with self._control_lock:
            return self._advance_safe_mode_once(
                capital_pool_id,
                target_state,
                actor_id=actor_id,
                note=note,
            )

    def _advance_safe_mode_once(
        self,
        capital_pool_id: str,
        target_state: str,
        actor_id: str,
        note: Optional[str] = None,
    ) -> str:
        """Manually advance the safe-mode state for a pool (governance recovery path).

        Used after conditions are cleared to progress from PAUSED → RECOVERY_TESTING
        or RECOVERY_TESTING → NORMAL_RESTORED.

        Raises RuntimeManagerError if the transition is not allowed.
        """
        try:
            new_state = self._kill_switch.advance_safe_mode(
                capital_pool_id,
                SafeModeState(target_state),
                actor_id=actor_id,
                note=note,
            )
        except (KillSwitchError, ValueError) as exc:
            raise RuntimeManagerError(f"Safe-mode advance failed: {exc}") from exc
        self._persist_ks_state()
        return new_state.value

    def get_kill_switch_audit_log(self) -> List[Dict[str, Any]]:
        """Return all kill-switch audit entries for this service instance."""
        return [e.to_dict() for e in self._kill_switch.audit_log()]

    # ------------------------------------------------------------------ #
    # Evolution orchestration boundaries (EVOLUTION_REVIEW_AND_THRESHOLDS)#
    # ------------------------------------------------------------------ #

    def evolution_freeze(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the runtime follow-through for an approved evolution freeze decision.

        Per EVOLUTION_REVIEW_AND_THRESHOLDS.md §11 and §11.2:
        Governance writes the freeze decision and creates a companion
        DeploymentPlan(current_stage -> frozen).  Runtime-manager only consumes
        that plan — it does NOT accept arbitrary direct binding mutations.

        Valid plan_runtime_action values (from the DeploymentPlan):
        - freeze_binding      : transition the active binding to pending_pause → paused.
          Canonical "stop new entries, preserve book" path for live freeze.
        - pause_then_freeze   : alias for freeze_binding (drain then pause).

        liquidate_then_freeze is NOT accepted here.  Flatten / zero-exposure
        escalation must route through the rollback path or execute_kill_switch()
        per EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.2 and
        KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY §3.

        Parameters (plain dict)
        -----------------------
        evolution_decision_id : str  — approved EvolutionDecision.id
        deployment_plan_id    : str  — companion DeploymentPlan.id created by
                                       governance/promotion plane
        binding_id            : str  — active RuntimeBinding to freeze
        plan_runtime_action   : str  — freeze_binding | pause_then_freeze
        actor_id              : str  — reviewer/operator authorising the freeze

        Optional
        --------
        note                  : str

        Returns
        -------
        dict with keys:
            evolution_decision_id : str
            deployment_plan_id    : str
            plan_runtime_action   : str
            binding               : dict — updated RuntimeBinding
            actor_id              : str
            executed_at           : str — ISO-8601 UTC
            note                  : str | None
        """
        evo_id = request.get("evolution_decision_id", "")
        deployment_plan_id = request.get("deployment_plan_id", "")
        binding_id = request.get("binding_id", "")
        plan_runtime_action = request.get("plan_runtime_action", "freeze_binding")
        actor_id = request.get("actor_id", "")
        note = request.get("note")

        if not evo_id:
            raise RuntimeManagerError("evolution_decision_id is required for evolution_freeze.")
        if not deployment_plan_id:
            raise RuntimeManagerError(
                "deployment_plan_id is required for evolution_freeze. "
                "The companion DeploymentPlan must be created by the governance/promotion "
                "plane before runtime-manager can execute the freeze follow-through."
            )
        if not binding_id:
            raise RuntimeManagerError("binding_id is required for evolution_freeze.")
        if not actor_id:
            raise RuntimeManagerError("actor_id is required for evolution_freeze.")

        _VALID_PLAN_ACTIONS = {"freeze_binding", "pause_then_freeze"}
        if plan_runtime_action == "liquidate_then_freeze":
            raise RuntimeManagerError(
                "plan_runtime_action='liquidate_then_freeze' is not accepted by evolution_freeze. "
                "Flatten / zero-exposure escalation must route through execute_kill_switch() "
                "or the rollback path per EVOLUTION_REVIEW_AND_THRESHOLDS §11.2."
            )
        if plan_runtime_action not in _VALID_PLAN_ACTIONS:
            raise RuntimeManagerError(
                f"Unknown plan_runtime_action={plan_runtime_action!r}. "
                f"Must be one of {sorted(_VALID_PLAN_ACTIONS)}."
            )

        with self._control_lock:
            current_binding = self._store.require(binding_id)
            if current_binding.is_terminal():
                raise RuntimeManagerError(
                    f"Cannot freeze binding {binding_id!r}: already in terminal state "
                    f"{current_binding.status!r}."
                )

            executed_at = utc_now()

            # Drain: active → pending_pause → paused.  Serialising this with
            # deploy, rollback, and kill prevents a stale freeze read from
            # overwriting a concurrent containment decision.
            if current_binding.status == RuntimeBindingStatus.ACTIVE.value:
                self._store.transition_status(
                    binding_id, RuntimeBindingStatus.PENDING_PAUSE.value
                )
                updated = self._store.transition_status(
                    binding_id, RuntimeBindingStatus.PAUSED.value
                )
            elif current_binding.status == RuntimeBindingStatus.PENDING_PAUSE.value:
                updated = self._store.transition_status(
                    binding_id, RuntimeBindingStatus.PAUSED.value
                )
            else:
                updated = self._store.require(binding_id)

        return {
            "evolution_decision_id": evo_id,
            "deployment_plan_id": deployment_plan_id,
            "plan_runtime_action": plan_runtime_action,
            "binding": updated.to_dict(),
            "actor_id": actor_id,
            "executed_at": executed_at,
            "note": note,
        }

    def evolution_retrain(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Record dispatch of an approved retrain/revalidate EvolutionDecision.

        Per EVOLUTION_REVIEW_AND_THRESHOLDS.md §12.2: `executed` means the research
        job / work item has been governed-ly created by the research plane first.
        The caller must supply the authoritative research_job_id returned by the
        research plane when it accepted the work item.  Runtime-manager records
        this reference as the routing_ref — it does NOT fabricate synthetic receipts.

        Actual model retraining happens in the research plane.

        Parameters (plain dict)
        -----------------------
        evolution_decision_id : str  — approved EvolutionDecision.id
        action_type           : str  — retrain | revalidate
        artifact_id           : str  — target artifact
        actor_id              : str
        research_job_id       : str  — authoritative work item id from the research plane

        Optional
        --------
        note                  : str

        Returns
        -------
        dict with keys:
            evolution_decision_id : str
            action_type           : str
            artifact_id           : str
            actor_id              : str
            dispatched_at         : str — ISO-8601 UTC
            routing_ref           : str — research_job_id echoed as the authoritative ref
            note                  : str | None
        """
        evo_id = request.get("evolution_decision_id", "")
        action_type = request.get("action_type", "")
        artifact_id = request.get("artifact_id", "")
        actor_id = request.get("actor_id", "")
        research_job_id = request.get("research_job_id", "")
        note = request.get("note")

        if not evo_id:
            raise RuntimeManagerError("evolution_decision_id is required for evolution_retrain.")
        if action_type not in ("retrain", "revalidate"):
            raise RuntimeManagerError(
                f"action_type must be 'retrain' or 'revalidate'; got {action_type!r}."
            )
        if not artifact_id:
            raise RuntimeManagerError("artifact_id is required for evolution_retrain.")
        if not actor_id:
            raise RuntimeManagerError("actor_id is required for evolution_retrain.")
        if not research_job_id:
            raise RuntimeManagerError(
                "research_job_id is required for evolution_retrain. "
                "The research plane must create the governed work item first and return "
                "its job id before runtime-manager can record the dispatch receipt."
            )

        dispatched_at = utc_now()

        return {
            "evolution_decision_id": evo_id,
            "action_type": action_type,
            "artifact_id": artifact_id,
            "actor_id": actor_id,
            "dispatched_at": dispatched_at,
            "routing_ref": research_job_id,
            "note": note,
        }

    def evolution_redeploy(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute redeploy follow-through after an approved evolution decision.

        Per EVOLUTION_REVIEW_AND_THRESHOLDS.md §11 (redeploy follow-through row)
        and §12.2: the Governance/Promotion plane must first create an
        ApprovalDecision and a companion DeploymentPlan.  Runtime-manager only
        *consumes* that plan — it must not accept raw artifact/binding fields as
        a shadow command surface.

        Callers must pass the pre-created plan as a structured `deployment_plan`
        dict.  All artifact and binding fields must originate from that plan
        object, not be supplied independently by the caller.

        Parameters (plain dict)
        -----------------------
        evolution_decision_id : str  — approved EvolutionDecision.id
        deployment_plan       : dict — DeploymentPlan created by the governance/
                                       promotion plane; required fields inside:
            plan_id                       : str
            target_stage                  : str  — paper | canary | live
            artifact_id                   : str
            artifact_version              : str
            capital_pool_id               : str
            persona_capital_binding_id    : str
            persona_capital_binding_status: str  — must be 'active'
            allowed_deployment_scope      : str
            loader_checks_passed          : bool — must be True
            plan_status                   : str  — default 'approved'
            runtime_id                    : str  — optional, auto-generated if absent

        Optional
        --------
        actor_id              : str
        note                  : str

        Returns
        -------
        dict with keys:
            evolution_decision_id : str
            binding               : dict — new RuntimeBinding
            actor_id              : str | None
            redeployed_at         : str — ISO-8601 UTC
            note                  : str | None
        """
        evo_id = request.get("evolution_decision_id", "")
        if not evo_id:
            raise RuntimeManagerError("evolution_decision_id is required for evolution_redeploy.")

        deployment_plan = request.get("deployment_plan")
        if not deployment_plan or not isinstance(deployment_plan, dict):
            raise RuntimeManagerError(
                "deployment_plan is required for evolution_redeploy and must be a dict. "
                "The Governance/Promotion plane must create the ApprovalDecision and "
                "DeploymentPlan first; runtime-manager only consumes that plan."
            )

        # Validate that the plan contains the minimum required fields
        _PLAN_REQUIRED = [
            "plan_id", "target_stage", "artifact_id", "artifact_version",
            "capital_pool_id", "persona_capital_binding_id",
            "persona_capital_binding_status", "allowed_deployment_scope",
        ]
        missing_plan_fields = [f for f in _PLAN_REQUIRED if not deployment_plan.get(f)]
        if "loader_checks_passed" not in deployment_plan:
            missing_plan_fields.append("loader_checks_passed")
        if missing_plan_fields:
            raise RuntimeManagerError(
                f"deployment_plan is missing required fields: {missing_plan_fields}"
            )

        deploy_req = dict(deployment_plan)
        deploy_req.setdefault("plan_status", "approved")

        binding = self.deploy(deploy_req)
        redeployed_at = utc_now()

        return {
            "evolution_decision_id": evo_id,
            "binding": binding.to_dict(),
            "actor_id": request.get("actor_id"),
            "redeployed_at": redeployed_at,
            "note": request.get("note"),
        }

    # ------------------------------------------------------------------ #
    # Read operations (open to authorised consumers)                      #
    # ------------------------------------------------------------------ #

    def get(self, binding_id: str) -> Optional[RuntimeBinding]:
        return self._store.get(binding_id)

    def require(self, binding_id: str) -> RuntimeBinding:
        return self._store.require(binding_id)

    def list_all(self) -> List[RuntimeBinding]:
        return self._store.list_all()

    def list_by_pool(self, capital_pool_id: str) -> List[RuntimeBinding]:
        return self._store.find_by_pool(capital_pool_id)

    def get_active_for_pool(self, capital_pool_id: str) -> Optional[RuntimeBinding]:
        return self._store.get_active_for_pool(capital_pool_id)

    def list_by_plan(self, plan_id: str) -> List[RuntimeBinding]:
        return self._store.find_by_plan(plan_id)
