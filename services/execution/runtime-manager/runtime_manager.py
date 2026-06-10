"""
RuntimeManager — Execution Plane Orchestrator (RT-002)

Sole writer of RuntimeBinding records. Translates approved DeploymentPlans into
live RuntimeBindings, enforces the single-runtime rule, manages lifecycle
transitions, and routes emergency actions from the KillSwitchController.

Authority boundary (contract.md §2)
------------------------------------
Only this class may call RuntimeBindingStore write methods. No Governance Plane,
Capital Pool Plane, or BFF code may write RuntimeBinding records.

Lifecycle (contract.md §5)
---------------------------
active → pending_pause → paused → active  (resume)
active → retired                           (replace / rollback / decommission)
active → failed                            (error / governance veto)
paused → retired                           (replace after pause completes)
paused → failed                            (veto or error)
pending_pause → failed                     (drain timeout)

Kill-switch fast path (contract.md §11)
----------------------------------------
KillSwitchController classifies & issues a KillSwitchCommand; RuntimeManager
executes the command and remains the sole writer of binding state.

Import note
-----------
This module lives in services/execution/runtime-manager/ (hyphenated path).
It loads sibling modules via importlib.util so the hyphen does not break imports.

Run tests:
    python3 -m pytest services/execution/runtime-manager/test_runtime_manager.py -q
"""
from __future__ import annotations

import importlib.util as _ilu
import sys as _sys
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Load sibling modules via importlib (hyphenated directory workaround)
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))


def _load_sibling(name: str) -> Any:
    if name in _sys.modules:
        return _sys.modules[name]
    spec = _ilu.spec_from_file_location(name, _HERE / f"{name}.py")
    mod = _ilu.module_from_spec(spec)
    _sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_rb_mod = _load_sibling("runtime_binding")
_ks_mod = _load_sibling("kill_switch_controller")

from services.capital.risk_policy import (  # noqa: E402
    RiskPolicyEvaluation,
    RiskPolicyEvaluationContext,
    RiskPolicyEvaluator,
    RiskPolicyTargetType,
    risk_policy_rejection_message,
)

RuntimeBinding = _rb_mod.RuntimeBinding
RuntimeBindingError = _rb_mod.RuntimeBindingError
RuntimeBindingStatus = _rb_mod.RuntimeBindingStatus
DeploymentMode = _rb_mod.DeploymentMode
RollbackActionType = _rb_mod.RollbackActionType
RuntimeBindingStore = _rb_mod.RuntimeBindingStore
validate_binding = _rb_mod.validate_binding
utc_now = _rb_mod.utc_now

KillSwitchActionType = _ks_mod.KillSwitchActionType
KillSwitchCommand = _ks_mod.KillSwitchCommand
KillSwitchController = _ks_mod.KillSwitchController
KillSwitchError = _ks_mod.KillSwitchError
EmergencyTrigger = _ks_mod.EmergencyTrigger


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class RuntimeManagerError(ValueError):
    """Raised when the Runtime Manager rejects a request."""


# ---------------------------------------------------------------------------
# Accepted plan statuses
# ---------------------------------------------------------------------------

_APPROVED_PLAN_STATUSES = {"approved", "executing"}

# ---------------------------------------------------------------------------
# Promotion gate — required fields per stage (contract.md §3.6)
# ---------------------------------------------------------------------------

_CANARY_GATE_REQUIRED = {
    "promotion_gate_decision_id",
    "human_gate_packet_ref",
    "broker_sandbox_smoke_ref",
    "risk_owner_approval_ref",
    "operator_approval_ref",
}
_LIVE_EXTRA_REQUIRED = {"canary_observation_ref"}
_CANARY_SCALE_BOUNDS = {"capital_scale_pct": (0.0, 5.0), "gross_scale_pct": (0.0, 25.0)}


# ---------------------------------------------------------------------------
# Request dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PromotionGateEvidence:
    """
    Promotion gate proof required for canary and live deployments.

    For canary: all five base fields plus scale bounds within (0, 5] / (0, 25].
    For live: additionally requires canary_observation_ref.
    """
    promotion_gate_decision_id: str
    human_gate_packet_ref: str
    broker_sandbox_smoke_ref: str
    risk_owner_approval_ref: str
    operator_approval_ref: str
    canary_observation_ref: Optional[str] = None
    capital_scale_pct: Optional[float] = None
    gross_scale_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class DeployRuntimeRequest:
    """
    Input to RuntimeManager.deploy().

    Caller must supply a plan in approved or executing status.
    For canary/live deployments, promotion_gate is required.
    Rollback replacement may bypass the promotion gate — use
    RollbackRuntimeRequest which sets rollback_parent directly.
    """
    plan_id: str
    persona_capital_binding_id: str
    runtime_id: str
    capital_pool_id: str
    artifact_id: str
    artifact_version: str
    deployment_mode: str
    plan_status: str
    binding_id: Optional[str] = None
    promotion_gate: Optional[PromotionGateEvidence] = None
    single_runtime_enforced: bool = True
    risk_policy: Optional[Any] = None
    risk_policy_ref: Optional[str] = None
    risk_policy_evaluation: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.plan_status not in _APPROVED_PLAN_STATUSES:
            raise RuntimeManagerError(
                f"plan_status must be one of {sorted(_APPROVED_PLAN_STATUSES)!r}, "
                f"got {self.plan_status!r}"
            )
        try:
            DeploymentMode(self.deployment_mode)
        except ValueError:
            raise RuntimeManagerError(
                f"Invalid deployment_mode: {self.deployment_mode!r}. "
                f"Must be one of {[e.value for e in DeploymentMode]}."
            )


@dataclass(frozen=True)
class ReplaceRuntimeRequest:
    """
    Input to RuntimeManager.replace() — hot-swap or post-pause swap.

    old_binding_id : binding being replaced (must be active or paused)
    new_request    : the replacement binding to create
    """
    old_binding_id: str
    new_request: DeployRuntimeRequest


@dataclass(frozen=True)
class RollbackRuntimeRequest:
    """
    Input to RuntimeManager.rollback().

    rollback_action_type : replace | pause_then_replace | liquidate_then_replace
    old_binding_id       : binding being rolled back
    new_request          : replacement binding; rollback_parent is set automatically
    """
    old_binding_id: str
    rollback_action_type: str
    new_request: DeployRuntimeRequest

    def __post_init__(self) -> None:
        try:
            RollbackActionType(self.rollback_action_type)
        except ValueError:
            raise RuntimeManagerError(
                f"Invalid rollback_action_type: {self.rollback_action_type!r}. "
                f"Must be one of {[e.value for e in RollbackActionType]}."
            )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class RuntimeEventType(str, Enum):
    BINDING_CREATED = "RuntimeBindingCreated"
    BINDING_PAUSED = "RuntimeBindingPaused"
    BINDING_RESUMED = "RuntimeBindingResumed"
    BINDING_RETIRED = "RuntimeBindingRetired"
    BINDING_FAILED = "RuntimeBindingFailed"
    ROLLBACK_INITIATED = "RuntimeRollbackInitiated"
    KILL_SWITCH_DISPATCHED = "RuntimeKillSwitchDispatched"
    PROMOTION_GATE_PASSED = "RuntimePromotionGatePassed"


@dataclass(frozen=True)
class RuntimeManagerEvent:
    """Execution event emitted after every RuntimeManager write operation."""
    event_id: str
    event_type: str
    binding_id: str
    capital_pool_id: str
    deployment_mode: str
    emitted_at: str
    plan_id: Optional[str] = None
    note: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "binding_id": self.binding_id,
            "capital_pool_id": self.capital_pool_id,
            "deployment_mode": self.deployment_mode,
            "emitted_at": self.emitted_at,
        }
        if self.plan_id is not None:
            d["plan_id"] = self.plan_id
        if self.note is not None:
            d["note"] = self.note
        if self.metadata:
            d["metadata"] = dict(self.metadata)
        return d


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuntimeManagerOutcome:
    """Result returned by every RuntimeManager write operation."""
    binding: Any  # RuntimeBinding (typed as Any to avoid circular import issues)
    events: List[RuntimeManagerEvent] = field(default_factory=list)
    retired_binding: Optional[Any] = None  # RuntimeBinding | None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"binding": self.binding.to_dict()}
        if self.retired_binding is not None:
            d["retired_binding"] = self.retired_binding.to_dict()
        d["events"] = [e.to_dict() for e in self.events]
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_binding_id() -> str:
    return f"rtb-{uuid.uuid4().hex[:12]}"


def _new_event_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


def _make_event(
    event_type: RuntimeEventType,
    binding: Any,
    note: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> RuntimeManagerEvent:
    return RuntimeManagerEvent(
        event_id=_new_event_id(),
        event_type=event_type.value,
        binding_id=binding.binding_id,
        capital_pool_id=binding.capital_pool_id,
        deployment_mode=binding.deployment_mode,
        plan_id=binding.plan_id,
        emitted_at=utc_now(),
        note=note,
        metadata=metadata or {},
    )


def _validate_promotion_gate(mode: str, gate: Optional[PromotionGateEvidence]) -> None:
    """Enforce promotion gate requirements for canary and live deployments."""
    if mode not in {DeploymentMode.CANARY.value, DeploymentMode.LIVE.value}:
        return

    if gate is None:
        raise RuntimeManagerError(
            f"deployment_mode {mode!r} requires a promotion_gate "
            "(contract.md §3.6 — canary/live activation gate present)"
        )

    gate_dict = gate.to_dict()
    missing = _CANARY_GATE_REQUIRED - set(gate_dict.keys())
    if missing:
        raise RuntimeManagerError(
            f"promotion_gate is missing required fields for {mode!r}: {sorted(missing)}"
        )

    if mode == DeploymentMode.LIVE.value:
        live_missing = _LIVE_EXTRA_REQUIRED - set(gate_dict.keys())
        if live_missing:
            raise RuntimeManagerError(
                f"live deployment requires promotion_gate fields: {sorted(live_missing)}"
            )

    if mode == DeploymentMode.CANARY.value:
        for fname, (lo, hi) in _CANARY_SCALE_BOUNDS.items():
            val = gate_dict.get(fname)
            if val is None or not (lo < float(val) <= hi):
                raise RuntimeManagerError(
                    f"canary promotion_gate.{fname} must be in ({lo}, {hi}], got {val!r}"
                )


def _validate_upstream_risk_policy_evaluation(payload: Any, *, error_prefix: str) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    evaluation = RiskPolicyEvaluation.from_mapping(payload)
    if evaluation.rejected:
        raise RuntimeManagerError(risk_policy_rejection_message(error_prefix, evaluation))
    return evaluation.to_dict()


def _risk_policy_context_from_deploy_request(
    request: DeployRuntimeRequest,
    *,
    metadata: Dict[str, Any],
) -> RiskPolicyEvaluationContext:
    raw_context = metadata.get("risk_policy_context")
    policy_context = dict(raw_context) if isinstance(raw_context, dict) else {}
    gate = request.promotion_gate.to_dict() if request.promotion_gate else {}
    return RiskPolicyEvaluationContext.from_mapping(
        {
            **policy_context,
            "target_type": RiskPolicyTargetType.RUNTIME_LAUNCH.value,
            "target_id": policy_context.get("target_id") or request.binding_id or request.runtime_id,
            "capital_pool_id": request.capital_pool_id,
            "stage": request.deployment_mode,
            "risk_policy_ref": request.risk_policy_ref or metadata.get("risk_policy_ref") or policy_context.get("risk_policy_ref"),
            "capital_scale_pct": policy_context.get("capital_scale_pct", gate.get("capital_scale_pct")),
            "gross_scale_pct": policy_context.get("gross_scale_pct", gate.get("gross_scale_pct")),
            "runtime_action": metadata.get("runtime_action"),
            "target_weights": policy_context.get("target_weights") or metadata.get("target_weights") or {},
            "asset_classes": policy_context.get("asset_classes") or metadata.get("asset_classes") or [],
            "strategy_family": policy_context.get("strategy_family") or metadata.get("strategy_family"),
            "gross_exposure": policy_context.get("gross_exposure") or metadata.get("gross_exposure"),
            "net_exposure": policy_context.get("net_exposure") or metadata.get("net_exposure"),
            "leverage": policy_context.get("leverage") or metadata.get("leverage"),
            "turnover": policy_context.get("turnover") or metadata.get("turnover"),
            "sector_exposures": policy_context.get("sector_exposures") or metadata.get("sector_exposures") or {},
            "factor_exposures": policy_context.get("factor_exposures") or metadata.get("factor_exposures") or {},
            "liquidity": policy_context.get("liquidity") or metadata.get("liquidity") or {},
            "order_type": policy_context.get("order_type") or metadata.get("order_type"),
            "time_in_force": policy_context.get("time_in_force") or metadata.get("time_in_force"),
            "drawdown_pct": policy_context.get("drawdown_pct") or metadata.get("drawdown_pct"),
            "kill_switch_trigger": policy_context.get("kill_switch_trigger") or metadata.get("kill_switch_trigger"),
            "metadata": {**metadata, **policy_context},
            "trace_id": policy_context.get("trace_id") or metadata.get("trace_id"),
        }
    )


def _evaluate_risk_policy_for_deploy(request: DeployRuntimeRequest, metadata: Dict[str, Any]) -> None:
    upstream = request.risk_policy_evaluation or metadata.get("risk_policy_evaluation")
    upstream_payload = _validate_upstream_risk_policy_evaluation(
        upstream,
        error_prefix="Runtime launch blocked",
    )
    if upstream_payload is not None:
        metadata.setdefault("risk_policy_evaluation", upstream_payload)

    risk_policy = request.risk_policy or metadata.get("risk_policy")
    if risk_policy is None:
        return
    evaluation = RiskPolicyEvaluator().evaluate(
        risk_policy,
        _risk_policy_context_from_deploy_request(request, metadata=metadata),
    )
    metadata["risk_policy_evaluation"] = evaluation.to_dict()
    if evaluation.rejected:
        raise RuntimeManagerError(risk_policy_rejection_message("Runtime launch blocked", evaluation))


# ---------------------------------------------------------------------------
# RuntimeManager
# ---------------------------------------------------------------------------

class RuntimeManager:
    """
    Execution Plane orchestrator — sole writer of RuntimeBinding records.

    Design
    ------
    * No blocking I/O in the hot path — state lives in RuntimeBindingStore.
    * Emits RuntimeManagerEvents after every write for downstream consumers.
    * Integrates with KillSwitchController for emergency fast-path dispatch.

    Usage
    -----
    store = RuntimeBindingStore()
    manager = RuntimeManager(store)

    request = DeployRuntimeRequest(
        plan_id="plan-001",
        persona_capital_binding_id="pcb-001",
        runtime_id="rt-001",
        capital_pool_id="pool-alpha",
        artifact_id="art-001",
        artifact_version="1.0.0",
        deployment_mode="paper",
        plan_status="approved",
    )
    outcome = manager.deploy(request)
    # outcome.binding  → active RuntimeBinding
    # outcome.events   → [RuntimeBindingCreated event]
    """

    def __init__(
        self,
        store: Any,  # RuntimeBindingStore
        *,
        kill_switch: Optional[Any] = None,  # KillSwitchController
    ) -> None:
        self._store = store
        self._kill_switch = kill_switch if kill_switch is not None else KillSwitchController()

    # ---- Read access ----

    def get_binding(self, binding_id: str) -> Optional[Any]:
        return self._store.get(binding_id)

    def require_binding(self, binding_id: str) -> Any:
        return self._store.require(binding_id)

    def get_active_binding_for_pool(self, capital_pool_id: str) -> Optional[Any]:
        return self._store.get_active_for_pool(capital_pool_id)

    def list_bindings(self) -> List[Any]:
        return self._store.list_all()

    def find_bindings_for_pool(self, capital_pool_id: str) -> List[Any]:
        return self._store.find_by_pool(capital_pool_id)

    # ---- Deploy ----

    def deploy(self, request: DeployRuntimeRequest) -> RuntimeManagerOutcome:
        """
        Create a RuntimeBinding from an approved DeploymentPlan.

        Pre-conditions (contract.md §3)
        --------------------------------
        1. plan_status must be approved or executing.
        2. persona_capital_binding_id must be provided.
        3. Single-runtime rule: no existing active binding for the pool (unless
           single_runtime_enforced=False on the request).
        4. Canary/live deployments require a valid PromotionGateEvidence.

        For replace/rollback flows, use replace() or rollback() which retire the
        old binding before creating the replacement.
        """
        _validate_promotion_gate(request.deployment_mode, request.promotion_gate)

        if not request.persona_capital_binding_id:
            raise RuntimeManagerError(
                "persona_capital_binding_id is required — "
                "a PersonaCapitalBinding reference is needed for governance admissibility proof"
            )

        metadata = dict(request.metadata)
        _evaluate_risk_policy_for_deploy(request, metadata)

        binding_id = request.binding_id or _new_binding_id()
        binding = RuntimeBinding(
            binding_id=binding_id,
            runtime_id=request.runtime_id,
            capital_pool_id=request.capital_pool_id,
            artifact_id=request.artifact_id,
            artifact_version=request.artifact_version,
            deployment_mode=request.deployment_mode,
            effective_at=utc_now(),
            status=RuntimeBindingStatus.ACTIVE.value,
            plan_id=request.plan_id,
            persona_capital_binding_id=request.persona_capital_binding_id,
            metadata=metadata,
        )

        created = self._store.create(
            binding,
            single_runtime_enforced=request.single_runtime_enforced,
        )

        events: List[RuntimeManagerEvent] = [
            _make_event(RuntimeEventType.BINDING_CREATED, created)
        ]
        if request.deployment_mode in {DeploymentMode.CANARY.value, DeploymentMode.LIVE.value}:
            events.append(
                _make_event(
                    RuntimeEventType.PROMOTION_GATE_PASSED,
                    created,
                    note=f"Promotion gate verified for {request.deployment_mode} deployment",
                    metadata=request.promotion_gate.to_dict() if request.promotion_gate else {},
                )
            )

        return RuntimeManagerOutcome(binding=created, events=events)

    # ---- Lifecycle transitions ----

    def pause(self, binding_id: str) -> RuntimeManagerOutcome:
        """
        Begin draining orders: active → pending_pause.

        Caller is responsible for sending a pause command to the LEAN runtime.
        When the drain is complete, call complete_pause().
        """
        updated = self._store.transition_status(
            binding_id,
            RuntimeBindingStatus.PENDING_PAUSE.value,
        )
        return RuntimeManagerOutcome(
            binding=updated,
            events=[_make_event(
                RuntimeEventType.BINDING_PAUSED,
                updated,
                note="pending_pause: draining orders",
            )],
        )

    def complete_pause(self, binding_id: str) -> RuntimeManagerOutcome:
        """
        Confirm drain complete: pending_pause → paused.

        Called after all orders have filled or cancelled.
        """
        updated = self._store.transition_status(
            binding_id,
            RuntimeBindingStatus.PAUSED.value,
        )
        return RuntimeManagerOutcome(
            binding=updated,
            events=[_make_event(
                RuntimeEventType.BINDING_PAUSED,
                updated,
                note="paused: drain complete",
            )],
        )

    def resume(self, binding_id: str) -> RuntimeManagerOutcome:
        """Resume a paused binding: paused → active."""
        updated = self._store.transition_status(
            binding_id,
            RuntimeBindingStatus.ACTIVE.value,
        )
        return RuntimeManagerOutcome(
            binding=updated,
            events=[_make_event(RuntimeEventType.BINDING_RESUMED, updated)],
        )

    def record_failure(self, binding_id: str, reason: str) -> RuntimeManagerOutcome:
        """Transition a binding to failed state (terminal)."""
        updated = self._store.transition_status(
            binding_id,
            RuntimeBindingStatus.FAILED.value,
        )
        return RuntimeManagerOutcome(
            binding=updated,
            events=[_make_event(RuntimeEventType.BINDING_FAILED, updated, note=reason)],
        )

    # ---- Replace ----

    def replace(self, request: ReplaceRuntimeRequest) -> RuntimeManagerOutcome:
        """
        Replace an existing binding with a new one (hot-swap or post-pause swap).

        Rollback action matrix §2 — `replace`:
        1. Retire the old binding.
        2. Create the new binding (single_runtime_enforced respected by new_request).

        The old binding must be active or paused. Active = hot-swap.
        Paused = pause_then_replace (caller already called pause + complete_pause).
        """
        old = self._store.require(request.old_binding_id)
        if old.status not in {RuntimeBindingStatus.ACTIVE.value, RuntimeBindingStatus.PAUSED.value}:
            raise RuntimeManagerError(
                f"Cannot replace binding {request.old_binding_id!r} in status {old.status!r}. "
                "Binding must be active or paused."
            )

        retired = self._store.retire(request.old_binding_id)
        new_outcome = self.deploy(request.new_request)

        events = [
            _make_event(RuntimeEventType.BINDING_RETIRED, retired, note="replaced"),
            *new_outcome.events,
        ]
        return RuntimeManagerOutcome(
            binding=new_outcome.binding,
            events=events,
            retired_binding=retired,
        )

    # ---- Rollback ----

    def rollback(self, request: RollbackRuntimeRequest) -> RuntimeManagerOutcome:
        """
        Execute a rollback: retire old binding, create replacement with lineage.

        Rollback action matrix §2:
        - replace             → hot-swap, inherit book
        - pause_then_replace  → caller must have called pause/complete_pause first
        - liquidate_then_replace → caller responsible for liquidation; binding must be paused

        The rollback binding carries rollback_parent and rollback_action_type so
        position lineage attribution (current_managed_by_binding_id) is auditable.
        Rollback bypasses the promotion gate check.
        """
        old = self._store.require(request.old_binding_id)
        if old.is_terminal():
            raise RuntimeManagerError(
                f"Cannot rollback binding {request.old_binding_id!r} — "
                f"already in terminal state {old.status!r}"
            )

        action = RollbackActionType(request.rollback_action_type)

        if action in {RollbackActionType.PAUSE_THEN_REPLACE, RollbackActionType.LIQUIDATE_THEN_REPLACE}:
            if old.status != RuntimeBindingStatus.PAUSED.value:
                raise RuntimeManagerError(
                    f"Rollback action {action.value!r} requires the binding to be paused first. "
                    f"Current status: {old.status!r}. Call pause() and complete_pause() first."
                )

        retired = self._store.retire(request.old_binding_id)

        binding_id = request.new_request.binding_id or _new_binding_id()
        new_binding = RuntimeBinding(
            binding_id=binding_id,
            runtime_id=request.new_request.runtime_id,
            capital_pool_id=request.new_request.capital_pool_id,
            artifact_id=request.new_request.artifact_id,
            artifact_version=request.new_request.artifact_version,
            deployment_mode=request.new_request.deployment_mode,
            effective_at=utc_now(),
            status=RuntimeBindingStatus.ACTIVE.value,
            plan_id=request.new_request.plan_id,
            persona_capital_binding_id=request.new_request.persona_capital_binding_id,
            rollback_parent=request.old_binding_id,
            rollback_action_type=request.rollback_action_type,
            metadata=dict(request.new_request.metadata),
        )

        created = self._store.create(new_binding, single_runtime_enforced=True)

        events = [
            _make_event(
                RuntimeEventType.ROLLBACK_INITIATED,
                retired,
                note=f"rollback {action.value}",
                metadata={"rollback_parent": request.old_binding_id, "action": action.value},
            ),
            _make_event(RuntimeEventType.BINDING_RETIRED, retired, note=f"retired by rollback {action.value}"),
            _make_event(RuntimeEventType.BINDING_CREATED, created, note=f"rollback replacement {action.value}"),
        ]
        return RuntimeManagerOutcome(
            binding=created,
            events=events,
            retired_binding=retired,
        )

    # ---- Kill-switch fast path ----

    def handle_kill_switch_command(
        self,
        command: Any,  # KillSwitchCommand
    ) -> RuntimeManagerOutcome:
        """
        Execute a KillSwitchCommand issued by KillSwitchController (contract.md §11).

        The controller classifies and dispatches; the Runtime Manager executes and
        remains the sole writer of RuntimeBinding state.

        Action mapping
        ---------------
        PAUSE       → active → pending_pause
        RISK_OFF    → active → pending_pause (caller manages risk reduction externally)
        LIQUIDATE   → active → pending_pause (caller responsible for position liquidation)
        REPLACE     → returns current binding unchanged; caller must follow up with replace()
        TERMINATE   → active/paused → failed (terminal)
        """
        if command.dispatch_path != "runtime_manager_fast_path":
            raise RuntimeManagerError(
                f"KillSwitchCommand must target runtime_manager_fast_path, "
                f"got {command.dispatch_path!r}"
            )

        action = KillSwitchActionType(command.action_type)
        binding_id = command.binding_id
        pool_id = command.capital_pool_id

        if binding_id is None:
            active = self._store.get_active_for_pool(pool_id)
            if active is None:
                raise RuntimeManagerError(
                    f"No active RuntimeBinding found for pool {pool_id!r}. "
                    "Cannot execute kill-switch command."
                )
            binding_id = active.binding_id

        binding = self._store.require(binding_id)
        if binding.is_terminal():
            raise RuntimeManagerError(
                f"Binding {binding_id!r} is already in terminal state {binding.status!r}. "
                "Kill-switch command cannot be applied."
            )

        ks_meta: Dict[str, Any] = {
            "command_id": command.command_id,
            "trigger_id": command.trigger_id,
            "emergency_class": command.emergency_class,
            "priority": command.priority,
        }

        if action in {KillSwitchActionType.PAUSE, KillSwitchActionType.RISK_OFF, KillSwitchActionType.LIQUIDATE}:
            note_map = {
                KillSwitchActionType.PAUSE: "kill-switch PAUSE: binding transitioning to pending_pause",
                KillSwitchActionType.RISK_OFF: "kill-switch RISK_OFF: binding pausing for risk reduction",
                KillSwitchActionType.LIQUIDATE: "kill-switch LIQUIDATE: binding pausing pending position liquidation",
            }
            updated = self._store.transition_status(binding_id, RuntimeBindingStatus.PENDING_PAUSE.value)
            event = _make_event(
                RuntimeEventType.KILL_SWITCH_DISPATCHED,
                updated,
                note=note_map[action],
                metadata=ks_meta,
            )
            return RuntimeManagerOutcome(binding=updated, events=[event])

        if action == KillSwitchActionType.TERMINATE:
            updated = self._store.transition_status(binding_id, RuntimeBindingStatus.FAILED.value)
            event = _make_event(
                RuntimeEventType.KILL_SWITCH_DISPATCHED,
                updated,
                note="kill-switch TERMINATE: binding terminated",
                metadata=ks_meta,
            )
            return RuntimeManagerOutcome(binding=updated, events=[event])

        if action == KillSwitchActionType.REPLACE:
            # REPLACE: return current binding — caller must call replace() with the fallback artifact.
            event = _make_event(
                RuntimeEventType.KILL_SWITCH_DISPATCHED,
                binding,
                note=(
                    f"kill-switch REPLACE: caller must call replace() with fallback "
                    f"artifact {command.fallback_artifact_id}:{command.fallback_artifact_version}"
                ),
                metadata={
                    **ks_meta,
                    "fallback_artifact_id": command.fallback_artifact_id,
                    "fallback_artifact_version": command.fallback_artifact_version,
                },
            )
            return RuntimeManagerOutcome(binding=binding, events=[event])

        raise RuntimeManagerError(f"Unhandled KillSwitchActionType: {action.value!r}")

    # ---- Kill-switch dispatch convenience ----

    def dispatch_emergency(
        self,
        trigger_reason: str,
        capital_pool_id: str,
        actor_id: str,
        *,
        binding_id: Optional[str] = None,
        action_override: Optional[Any] = None,
        fallback_artifact_id: Optional[str] = None,
        fallback_artifact_version: Optional[str] = None,
    ) -> RuntimeManagerOutcome:
        """
        Convenience: classify trigger via KillSwitchController and execute immediately.
        """
        trigger = EmergencyTrigger(
            reason=trigger_reason,
            capital_pool_id=capital_pool_id,
            actor_id=actor_id,
            binding_id=binding_id,
        )
        ks_outcome = self._kill_switch.dispatch(
            trigger,
            action_override=action_override,
            fallback_artifact_id=fallback_artifact_id,
            fallback_artifact_version=fallback_artifact_version,
        )
        return self.handle_kill_switch_command(ks_outcome.command)
