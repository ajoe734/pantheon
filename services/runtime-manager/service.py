"""RuntimeManagerService — pure service layer (no HTTP).

This module is the sole authorised writer for RuntimeBinding records within the
Execution Plane.  It enforces the pre-conditions documented in:

    services/execution/runtime-manager/contract.md
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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Locate and import the execution-plane runtime_binding module
# ---------------------------------------------------------------------------

_EXEC_RM_DIR = os.getenv(
    "PANTHEON_EXEC_RUNTIME_MANAGER_DIR",
    str(Path(__file__).resolve().parent.parent.parent
        / "services" / "execution" / "runtime-manager"),
)
_REPO_ROOT = str(Path(__file__).resolve().parents[2])

for _path in (_EXEC_RM_DIR, _REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from runtime_binding import (  # noqa: E402
    RuntimeBinding,
    RuntimeBindingError,
    RuntimeBindingStatus,
    RuntimeBindingStore,
    DeploymentMode,
    RollbackActionType,
    validate_binding,
    utc_now,
)
from kill_switch_controller import (  # noqa: E402
    KillSwitchController,
    KillSwitchActionType,
    KillSwitchError,
    SafeModeState,
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

__all__ = [
    "RuntimeManagerService",
    "RuntimeManagerError",
    "DeployPlanRequest",
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
_FOUNDATION_POLICY_VERSION = "2026-04-27"
_KILL_SWITCH_FOUNDATION_OPERATION = "runtime_manager.kill_switch.dispatch"


def _scope_allows_stage(allowed_deployment_scope: str, target_stage: str) -> bool:
    return _STAGE_ORDER.get(allowed_deployment_scope, -1) >= _STAGE_ORDER.get(target_stage, 999)


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
        self._foundation_idempotency: Dict[str, Dict[str, Any]] = {}
        self._foundation_recovery_audit: List[Dict[str, Any]] = []
        # Derive kill-switch store path alongside the binding store when not supplied.
        if ks_store_path is None and store_path is not None:
            ks_store_path = store_path.parent / "kill_switch.json"
        self._ks_store_path = ks_store_path
        self._load_ks_state()

    # ------------------------------------------------------------------ #
    # Primary write operations (Execution Plane only)                     #
    # ------------------------------------------------------------------ #

    def deploy(
        self,
        request: Dict[str, Any],
        _allow_cutover_bypass: bool = False,
    ) -> RuntimeBinding:
        """Create a RuntimeBinding from a validated DeploymentPlan descriptor.

        Pre-conditions (RUN-001):
        1. plan_status must be 'approved' or 'executing'
        2. persona_capital_binding_status must equal 'active'
        3. allowed_deployment_scope >= target_stage
        4. loader_checks_passed must be True
        5. stage consistency: target_stage must be a valid DeploymentMode value
        6. Single-runtime rule enforced by the store

        ``_allow_cutover_bypass`` is an internal-only flag used by the REPLACE
        rollback path to bypass the single-runtime guard for exactly this one
        binding creation during the hot-swap cutover window.  Callers outside
        this class must never set it; it avoids the race condition that arises
        when the old approach temporarily mutated the service-wide
        ``_single_runtime_enforced`` flag.
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

        # Pre-condition 1: plan status
        if plan_status not in ("approved", "executing"):
            raise RuntimeManagerError(
                f"DeploymentPlan {plan_id!r} status {plan_status!r} is not 'approved' or 'executing'. "
                "A RuntimeBinding cannot be created without an approved or executing plan."
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

        # Pre-condition 6: rollback fields consistency
        if rollback_parent and not rollback_action_type:
            raise RuntimeManagerError(
                "rollback_action_type is required when rollback_parent is set."
            )

        binding_id = f"rb-{uuid.uuid4().hex}"
        binding = RuntimeBinding(
            binding_id=binding_id,
            runtime_id=runtime_id,
            capital_pool_id=capital_pool_id,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            deployment_mode=target_stage,
            effective_at=utc_now(),
            status=RuntimeBindingStatus.ACTIVE.value,
            plan_id=plan_id,
            persona_capital_binding_id=persona_capital_binding_id,
            rollback_parent=rollback_parent,
            rollback_action_type=rollback_action_type,
        )

        # Semantic validation (field-level)
        errors = validate_binding(binding)
        if errors:
            raise RuntimeManagerError(f"RuntimeBinding validation failed: {errors}")

        # Store create — enforces single-runtime rule unless this specific call
        # has been granted a per-call cutover bypass by the REPLACE rollback path.
        effective_enforce = self._single_runtime_enforced and not _allow_cutover_bypass
        return self._store.create(
            binding,
            single_runtime_enforced=effective_enforce,
        )

    def retire(self, binding_id: str, retired_at: Optional[str] = None) -> RuntimeBinding:
        """Retire a binding (terminal transition)."""
        return self._store.retire(binding_id, retired_at=retired_at)

    def rollback(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a canonical rollback through the runtime-manager.

        Implements the three strategies from ROLLBACK_AND_POSITION_SEMANTICS.md §3:

        replace
            Hot-swap: retire the old binding then create the replacement.
            The existing book is inherited by the new artifact.  Old binding core
            fields are never rewritten — only the status transitions to 'retired'.

        pause_then_replace
            Drain-then-swap: transition old binding active → pending_pause → paused,
            create replacement binding (single-runtime rule does not fire because the
            old binding is no longer active), then retire the paused old binding.
            Cutover occurs after open orders are stabilised.

        liquidate_then_replace
            Flatten-then-swap: retire old binding with liquidation metadata, then
            create replacement binding.  When replacement_start_paused=True the new
            binding starts in guarded / paused mode, letting the operator confirm
            zero-position state before re-enabling entries.

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

        # Verify the current binding exists and is not already terminal
        old_binding = self._store.require(current_binding_id)
        if old_binding.is_terminal():
            raise RuntimeManagerError(
                f"Cannot roll back binding {current_binding_id!r}: "
                f"already in terminal state {old_binding.status!r}."
            )

        cutover_at = utc_now()

        # Build the replacement deploy request
        deploy_req: Dict[str, Any] = {
            "plan_id": request.get("replacement_plan_id", ""),
            "plan_status": request.get("replacement_plan_status", "approved"),
            "target_stage": request.get(
                "replacement_deployment_mode", old_binding.deployment_mode
            ),
            "artifact_id": request.get("replacement_artifact_id", ""),
            "artifact_version": request.get("replacement_artifact_version", ""),
            "capital_pool_id": old_binding.capital_pool_id,
            "persona_capital_binding_id": request.get(
                "replacement_persona_capital_binding_id", ""
            ),
            "persona_capital_binding_status": request.get(
                "replacement_persona_capital_binding_status", "active"
            ),
            "allowed_deployment_scope": request.get("replacement_allowed_deployment_scope", ""),
            "loader_checks_passed": request.get("loader_checks_passed", True),
            "runtime_id": request.get("replacement_runtime_id"),
            "rollback_parent": current_binding_id,
            "rollback_action_type": action_type,
        }

        if action_type == RollbackActionType.REPLACE.value:
            # Hot-swap per L1 §3.1 and §9: replacement binding must exist before the
            # old binding is retired (cutover boundary = create new + retire old, in
            # that order).  The single-runtime guard is bypassed only for this specific
            # deploy() call via the per-call _allow_cutover_bypass flag so that concurrent
            # deploy() calls on other threads still see the full guard.
            # Per §8: old binding core fields are not rewritten; only status -> retired.
            new_binding = self.deploy(deploy_req, _allow_cutover_bypass=True)
            self._store.retire(current_binding_id, retired_at=cutover_at)

        elif action_type == RollbackActionType.PAUSE_THEN_REPLACE.value:
            # Step 1: Drain — active → pending_pause → paused
            if old_binding.status == RuntimeBindingStatus.ACTIVE.value:
                self._store.transition_status(
                    current_binding_id, RuntimeBindingStatus.PENDING_PAUSE.value
                )
                self._store.transition_status(
                    current_binding_id, RuntimeBindingStatus.PAUSED.value
                )
            elif old_binding.status not in (
                RuntimeBindingStatus.PENDING_PAUSE.value,
                RuntimeBindingStatus.PAUSED.value,
            ):
                raise RuntimeManagerError(
                    f"pause_then_replace requires the current binding to be active, "
                    f"pending_pause, or paused; current status={old_binding.status!r}."
                )
            # Step 2: Create replacement while old is paused.
            # single-runtime rule does not fire because the old binding is no longer active.
            new_binding = self.deploy(deploy_req)
            # Step 3: Retire old paused binding post-cutover.
            self._store.retire(current_binding_id, retired_at=cutover_at)

        elif action_type == RollbackActionType.LIQUIDATE_THEN_REPLACE.value:
            # Step 1: Retire old binding (real position flattening is the execution
            # layer's responsibility; this service records the cutover boundary).
            # Per §3.3 and §9: liquidation / cancel telemetry remains on the old
            # binding/artifact until the runtime is confirmed flat.
            self._store.retire(current_binding_id, retired_at=cutover_at)
            # Step 2: Create replacement, optionally starting in guarded / paused mode.
            new_binding = self.deploy(deploy_req)
            if replacement_start_paused:
                self._store.transition_status(
                    new_binding.binding_id, RuntimeBindingStatus.PENDING_PAUSE.value
                )
                new_binding = self._store.transition_status(
                    new_binding.binding_id, RuntimeBindingStatus.PAUSED.value
                )

        else:
            # Unreachable after enum validation above, but kept for safety.
            raise RuntimeManagerError(f"Unhandled action_type: {action_type!r}")

        # Re-fetch the retired old binding for its final state.
        retired_old = self._store.require(current_binding_id)

        # Position lineage record per ROLLBACK_AND_POSITION_SEMANTICS.md §7.
        # opened_by_artifact_id: immutable — reflects original position opener.
        # current_managed_by_binding_id: updated to new binding after cutover.
        opened_by_artifact_id = request.get("opened_by_artifact_id", old_binding.artifact_id)
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
            "old_binding": retired_old.to_dict(),
            "new_binding": new_binding.to_dict(),
            "cutover_at": cutover_at,
            "position_lineage": position_lineage,
        }

    def transition(self, binding_id: str, new_status: str) -> RuntimeBinding:
        """Transition a binding to a new status via the allowed state machine."""
        return self._store.transition_status(binding_id, new_status)

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

    def execute_kill_switch(self, request: Dict[str, Any]) -> Dict[str, Any]:
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
        from kill_switch_controller import EmergencyTrigger, KillSwitchActionType as KSAT  # noqa: E402

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
                self._record_ks_recovery_audit(
                    action_type=CommandRecoveryAction.REPLAY_RESUMED,
                    reason="resumed kill-switch binding follow-through from durable executing record",
                    idempotency_key=existing_record.idempotency_key,
                    trace_id=existing_record.trace_id,
                    metadata={"command_id": existing_result["command"].get("command_id")},
                )
                binding_action = self._execute_kill_switch_binding_action(
                    self._durable_kill_switch_command(existing_result["command"])
                )
                succeeded = existing_record.with_status(
                    IdempotencyStatus.SUCCEEDED,
                    result_ref=f"kill_switch:{existing_result['command'].get('command_id')}",
                )
                foundation_context["idempotency_record"] = succeeded
                recovered = existing_result
                recovered["foundation"] = _serialize_foundation_context(foundation_context)
                recovered["binding_action"] = binding_action
                recovered["idempotent_replay"] = True
                self._store_ks_idempotency_record(succeeded, result=recovered)
                return recovered
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
        idempotency_record = executing_record.with_status(
            IdempotencyStatus.SUCCEEDED,
            result_ref=f"kill_switch:{outcome.command.command_id}",
        )
        foundation_context["idempotency_record"] = idempotency_record
        result = outcome.to_dict()
        result["foundation"] = _serialize_foundation_context(foundation_context)
        result["binding_action"] = binding_action
        # Persist audit entry, safe-mode, and idempotency before acknowledging
        # the command (contract §11.2: durable write must precede ack).
        self._store_ks_idempotency_record(idempotency_record, result=result)
        return result

    def _execute_kill_switch_binding_action(
        self, command: Any
    ) -> Optional[Dict[str, Any]]:
        """Execute the RuntimeBinding state write for a dispatched kill-switch command.

        Resolves the target binding via command.binding_id, falling back to the
        active binding for command.capital_pool_id.  Executes:
            PAUSE / RISK_OFF  → pending_pause → paused
            LIQUIDATE / TERMINATE → retire immediately
            REPLACE → hot-swap: create fallback replacement binding first, then
                      retire the current binding.  Uses old binding metadata
                      (persona_capital_binding_id, deployment_mode) combined with
                      command.fallback_artifact_id / fallback_artifact_version.
                      persona_capital_binding_status=active and
                      allowed_deployment_scope=live are assumed on the emergency
                      fast path (KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY §5.2).

        Returns a dict with 'action', 'binding' (old, retired/paused), and for
        REPLACE also 'replacement_binding' (new active binding).  Returns None
        when no live binding is found for the target pool.
        """
        action_type = getattr(command, "action_type", None)
        binding_id = getattr(command, "binding_id", None)
        if not binding_id and action_type == KillSwitchActionType.REPLACE.value:
            replacement = self._find_kill_switch_replacement_binding(
                command_id=getattr(command, "command_id", ""),
                old_binding_id=None,
                fallback_artifact_id=getattr(command, "fallback_artifact_id", ""),
                fallback_artifact_version=getattr(command, "fallback_artifact_version", ""),
            )
            if replacement is not None:
                binding_id = replacement.rollback_parent
        if not binding_id:
            active = self._store.get_active_for_pool(command.capital_pool_id)
            if active:
                binding_id = active.binding_id

        if not binding_id:
            return None

        try:
            b = self._store.get(binding_id)
            if b is None:
                return None

            if b.is_terminal() and action_type != KillSwitchActionType.REPLACE.value:
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
                updated = self._store.transition_status(
                    binding_id, RuntimeBindingStatus.PAUSED.value
                )
                return {"action": action_type, "binding": updated.to_dict()}

            elif action_type == KillSwitchActionType.REPLACE.value:
                # Hot-swap fast path per KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY §4.4 and §5.2.
                # Create the fallback replacement binding BEFORE retiring the current one so
                # the pool is never left without a live runtime (same ordering as rollback REPLACE).
                # The single-runtime guard is bypassed only for this specific call via
                # _allow_cutover_bypass so concurrent deploy() calls on other threads are unaffected.
                cutover_at = utc_now()
                replacement = self._find_kill_switch_replacement_binding(
                    command_id=command.command_id,
                    old_binding_id=binding_id,
                    fallback_artifact_id=command.fallback_artifact_id,
                    fallback_artifact_version=command.fallback_artifact_version,
                )
                if replacement is None:
                    deploy_req: Dict[str, Any] = {
                        "plan_id": f"ks-replace-{command.command_id}",
                        "plan_status": "approved",
                        "target_stage": b.deployment_mode,
                        "artifact_id": command.fallback_artifact_id,
                        "artifact_version": command.fallback_artifact_version,
                        "capital_pool_id": command.capital_pool_id,
                        "persona_capital_binding_id": b.persona_capital_binding_id,
                        # Emergency assumption: the existing PCB is still active and the
                        # scope is sufficient (live is the maximum and covers all stages).
                        "persona_capital_binding_status": "active",
                        "allowed_deployment_scope": "live",
                        "loader_checks_passed": True,
                        "rollback_parent": binding_id,
                        "rollback_action_type": "replace",
                    }
                    replacement = self.deploy(deploy_req, _allow_cutover_bypass=True)

                if b.is_terminal():
                    retired = b
                else:
                    retired = self._store.retire(binding_id, retired_at=cutover_at)
                return {
                    "action": action_type,
                    "binding": retired.to_dict(),
                    "replacement_binding": replacement.to_dict(),
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

        current_binding = self._store.require(binding_id)
        if current_binding.is_terminal():
            raise RuntimeManagerError(
                f"Cannot freeze binding {binding_id!r}: already in terminal state "
                f"{current_binding.status!r}."
            )

        executed_at = utc_now()

        # Drain: active → pending_pause → paused (per DeploymentPlan runtime_action).
        # Tolerate bindings already paused or pending_pause by a prior kill-switch or rollback
        # path — the freeze intent is satisfied if the binding ends up paused regardless of
        # which path got it there.  This prevents a spurious RuntimeBindingError when the
        # kill-switch fast path reached PAUSED before the governance freeze arrived.
        if current_binding.status == RuntimeBindingStatus.ACTIVE.value:
            self._store.transition_status(binding_id, RuntimeBindingStatus.PENDING_PAUSE.value)
            updated = self._store.transition_status(binding_id, RuntimeBindingStatus.PAUSED.value)
        elif current_binding.status == RuntimeBindingStatus.PENDING_PAUSE.value:
            # Kill-switch already drained to pending_pause; complete the drain step.
            updated = self._store.transition_status(binding_id, RuntimeBindingStatus.PAUSED.value)
        else:
            # Already paused (e.g. paused by kill-switch or rollback) — idempotent re-fetch.
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
