"""
KillSwitchController — Emergency Fast-Path Execution for Pantheon

Implements the kill switch and safe-mode fast path defined in
KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md.

Key invariants (§2, §5)
-----------------------
1. Emergency actions bypass the normal evolution-review queue.
2. They do NOT bypass the runtime-manager.  The fast path routes commands
   directly to the runtime-manager, which retains sole write authority over
   RuntimeBinding and position state.
3. Every dispatch produces an immutable KillSwitchAuditEntry — audit trail
   is always preserved.
4. The hot path (classify + dispatch) is pure Python with no blocking I/O so
   latency can be benchmarked deterministically.

Public API
----------
EmergencyClass          — SOFT | HARD
HardTriggerReason       — enumeration of hard-trigger conditions (§6.1)
SoftTriggerReason       — enumeration of soft-trigger conditions (§6.2)
KillSwitchActionType    — PAUSE | RISK_OFF | LIQUIDATE | REPLACE | TERMINATE
SafeModeState           — NORMAL | GUARDED | RISK_OFF | PAUSED |
                          RECOVERY_TESTING | NORMAL_RESTORED
EmergencyTrigger        — incoming signal payload
KillSwitchCommand       — fast-path dispatch command to the runtime-manager
KillSwitchAuditEntry    — immutable audit record
KillSwitchOutcome       — result envelope (command + audit entry + safe-mode)
KillSwitchController    — main controller class
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


FAST_PATH_DISPATCH_CHANNEL = "runtime_manager_fast_path"
FAST_PATH_LATENCY_TARGET_MS = 5.0
FAST_PATH_BENCHMARK_ITERATIONS = 1000


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EmergencyClass(str, Enum):
    SOFT = "soft"
    HARD = "hard"


class HardTriggerReason(str, Enum):
    """Conditions that escalate directly to the hard emergency path (§6.1)."""
    SEVERITY_1_INCIDENT = "severity_1_incident"
    UNAUTHORIZED_ARTIFACT_BINDING_MISMATCH = "unauthorized_artifact_binding_mismatch"
    RUNTIME_UNEXPECTED_ORDER_PATTERN = "runtime_unexpected_order_pattern"
    BROKER_POSITION_MISMATCH_CRITICAL = "broker_position_mismatch_critical"
    DRAWDOWN_HARD_BREACH = "drawdown_hard_breach"
    OPERATOR_EMERGENCY_STOP = "operator_emergency_stop"


class SoftTriggerReason(str, Enum):
    """Conditions that route to the soft emergency path (§6.2)."""
    DRIFT_ABOVE_WARNING_THRESHOLD = "drift_above_warning_threshold"
    REPEATED_REJECT_RATE_INCREASE = "repeated_reject_rate_increase"
    SLIPPAGE_DETERIORATION = "slippage_deterioration"
    LOADER_ANOMALY_NO_BREACH = "loader_anomaly_no_breach"
    CANARY_UNDERPERFORMANCE = "canary_underperformance"


class KillSwitchActionType(str, Enum):
    """Actions the runtime-manager may execute in response to an emergency (§4)."""
    PAUSE = "pause"
    RISK_OFF = "risk_off"
    LIQUIDATE = "liquidate"
    REPLACE = "replace"
    TERMINATE = "terminate"


class SafeModeState(str, Enum):
    """Operational safe-mode state machine states (§9)."""
    NORMAL = "normal"
    GUARDED = "guarded"
    RISK_OFF = "risk_off"
    PAUSED = "paused"
    RECOVERY_TESTING = "recovery_testing"
    NORMAL_RESTORED = "normal_restored"


# ---------------------------------------------------------------------------
# Safe-mode transition table
# ---------------------------------------------------------------------------

_SAFE_MODE_TRANSITIONS: Dict[str, List[str]] = {
    SafeModeState.NORMAL.value: [
        SafeModeState.GUARDED.value,
        SafeModeState.PAUSED.value,
        SafeModeState.RISK_OFF.value,
    ],
    SafeModeState.GUARDED.value: [
        SafeModeState.PAUSED.value,
        SafeModeState.RISK_OFF.value,
        SafeModeState.NORMAL_RESTORED.value,
    ],
    SafeModeState.RISK_OFF.value: [
        SafeModeState.RECOVERY_TESTING.value,
        SafeModeState.PAUSED.value,
    ],
    SafeModeState.PAUSED.value: [
        SafeModeState.RECOVERY_TESTING.value,
    ],
    SafeModeState.RECOVERY_TESTING.value: [
        SafeModeState.NORMAL_RESTORED.value,
        SafeModeState.PAUSED.value,
    ],
    # Terminal restoration state — requires an explicit governance unlock to
    # return to NORMAL; represented here as a self-loop so callers can read it.
    SafeModeState.NORMAL_RESTORED.value: [
        SafeModeState.NORMAL.value,
    ],
}


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------

class KillSwitchError(ValueError):
    """Raised when kill-switch invariants are violated."""


@dataclass(frozen=True)
class EmergencyTrigger:
    """
    Incoming emergency signal.

    Fields
    ------
    trigger_id          : unique ID for this trigger event
    reason              : one of HardTriggerReason or SoftTriggerReason (str)
    capital_pool_id     : the pool under emergency
    binding_id          : the active RuntimeBinding being targeted (if known)
    actor_id            : operator or system component that raised the trigger
    triggered_at        : UTC ISO-8601 timestamp
    severity            : optional numeric severity (1 = highest)
    context             : arbitrary key/value metadata (broker info, metrics, etc.)
    """
    reason: str
    capital_pool_id: str
    actor_id: str
    trigger_id: str = field(default_factory=lambda: _new_id("trig"))
    triggered_at: str = field(default_factory=utc_now)
    binding_id: Optional[str] = None
    severity: Optional[int] = None
    context: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        hard_values = {r.value for r in HardTriggerReason}
        soft_values = {r.value for r in SoftTriggerReason}
        if self.reason not in hard_values | soft_values:
            raise KillSwitchError(
                f"Unknown trigger reason: {self.reason!r}. "
                f"Must be one of: {sorted(hard_values | soft_values)}"
            )
        if not self.capital_pool_id:
            raise KillSwitchError("capital_pool_id must not be empty")
        if not self.actor_id:
            raise KillSwitchError("actor_id must not be empty")

    def emergency_class(self) -> EmergencyClass:
        """Classify this trigger as SOFT or HARD."""
        hard_values = {r.value for r in HardTriggerReason}
        return EmergencyClass.HARD if self.reason in hard_values else EmergencyClass.SOFT

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "trigger_id": self.trigger_id,
            "reason": self.reason,
            "capital_pool_id": self.capital_pool_id,
            "actor_id": self.actor_id,
            "triggered_at": self.triggered_at,
            "emergency_class": self.emergency_class().value,
        }
        if self.binding_id is not None:
            d["binding_id"] = self.binding_id
        if self.severity is not None:
            d["severity"] = self.severity
        if self.context:
            d["context"] = dict(self.context)
        return d


@dataclass(frozen=True)
class KillSwitchCommand:
    """
    Fast-path dispatch command sent to the runtime-manager.

    The runtime-manager is the ONLY service authorised to execute this command.
    It must update RuntimeBinding status, position lineage, and emit audit events
    on completion.

    Fields
    ------
    command_id          : unique command identifier
    trigger_id          : the EmergencyTrigger that caused this command
    emergency_class     : SOFT | HARD
    action_type         : the action the runtime-manager must execute
    capital_pool_id     : target pool
    binding_id          : target RuntimeBinding (may be None when unknown)
    actor_id            : originating actor
    issued_at           : UTC ISO-8601
    priority            : 1 (highest) for HARD, 2 for SOFT
    dispatch_path       : fixed to runtime_manager_fast_path
    bypass_review_queue : always True for emergency fast-path dispatches
    fallback_artifact_id        : optional — required for REPLACE actions
    fallback_artifact_version   : optional — required for REPLACE actions
    metadata            : arbitrary pass-through context
    """
    command_id: str
    trigger_id: str
    emergency_class: str
    action_type: str
    capital_pool_id: str
    actor_id: str
    issued_at: str
    priority: int
    dispatch_path: str = FAST_PATH_DISPATCH_CHANNEL
    bypass_review_queue: bool = True
    binding_id: Optional[str] = None
    fallback_artifact_id: Optional[str] = None
    fallback_artifact_version: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            EmergencyClass(self.emergency_class)
        except ValueError as exc:
            raise KillSwitchError(
                f"Invalid emergency_class: {self.emergency_class!r}. "
                f"Must be one of {[e.value for e in EmergencyClass]}."
            ) from exc

        try:
            KillSwitchActionType(self.action_type)
        except ValueError as exc:
            raise KillSwitchError(
                f"Invalid action_type: {self.action_type!r}. "
                f"Must be one of {[e.value for e in KillSwitchActionType]}."
            ) from exc

        if self.priority not in {1, 2}:
            raise KillSwitchError("priority must be 1 (hard) or 2 (soft)")
        if not self.capital_pool_id:
            raise KillSwitchError("capital_pool_id must not be empty")
        if not self.actor_id:
            raise KillSwitchError("actor_id must not be empty")
        if self.dispatch_path != FAST_PATH_DISPATCH_CHANNEL:
            raise KillSwitchError(
                "dispatch_path must remain runtime_manager_fast_path for emergency commands"
            )
        if not self.bypass_review_queue:
            raise KillSwitchError(
                "emergency fast-path commands must bypass the normal review queue"
            )
        if (
            self.action_type == KillSwitchActionType.REPLACE.value
            and (not self.fallback_artifact_id or not self.fallback_artifact_version)
        ):
            raise KillSwitchError(
                "fallback_artifact_id and fallback_artifact_version are required when "
                "action_type is REPLACE"
            )

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "command_id": self.command_id,
            "trigger_id": self.trigger_id,
            "emergency_class": self.emergency_class,
            "action_type": self.action_type,
            "capital_pool_id": self.capital_pool_id,
            "actor_id": self.actor_id,
            "issued_at": self.issued_at,
            "priority": self.priority,
            "dispatch_path": self.dispatch_path,
            "bypass_review_queue": self.bypass_review_queue,
        }
        if self.binding_id is not None:
            d["binding_id"] = self.binding_id
        if self.fallback_artifact_id is not None:
            d["fallback_artifact_id"] = self.fallback_artifact_id
        if self.fallback_artifact_version is not None:
            d["fallback_artifact_version"] = self.fallback_artifact_version
        if self.metadata:
            d["metadata"] = dict(self.metadata)
        return d


@dataclass(frozen=True)
class KillSwitchAuditEntry:
    """
    Immutable audit record for every kill-switch dispatch (§5).

    These entries must be persisted by the caller (runtime-manager or
    audit subsystem) before acknowledging the command.
    """
    audit_id: str
    command_id: str
    trigger_id: str
    reason: str
    emergency_class: str
    action_type: str
    capital_pool_id: str
    actor_id: str
    audited_at: str
    safe_mode_before: str
    safe_mode_after: str
    binding_id: Optional[str] = None
    outcome_note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "audit_id": self.audit_id,
            "command_id": self.command_id,
            "trigger_id": self.trigger_id,
            "reason": self.reason,
            "emergency_class": self.emergency_class,
            "action_type": self.action_type,
            "capital_pool_id": self.capital_pool_id,
            "actor_id": self.actor_id,
            "audited_at": self.audited_at,
            "safe_mode_before": self.safe_mode_before,
            "safe_mode_after": self.safe_mode_after,
        }
        if self.binding_id is not None:
            d["binding_id"] = self.binding_id
        if self.outcome_note is not None:
            d["outcome_note"] = self.outcome_note
        return d


@dataclass(frozen=True)
class KillSwitchOutcome:
    """
    Result envelope returned from KillSwitchController.dispatch().

    Fields
    ------
    command         : the fast-path command issued to the runtime-manager
    audit_entry     : immutable audit record — must be persisted before acking
    safe_mode_after : the safe-mode state following this dispatch
    """
    command: KillSwitchCommand
    audit_entry: KillSwitchAuditEntry
    safe_mode_after: SafeModeState

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command.to_dict(),
            "audit_entry": self.audit_entry.to_dict(),
            "safe_mode_after": self.safe_mode_after.value,
        }


# ---------------------------------------------------------------------------
# Action selection matrix (§7)
# ---------------------------------------------------------------------------

def _select_action(
    trigger: EmergencyTrigger,
    emergency_class: EmergencyClass,
) -> KillSwitchActionType:
    """
    Map a trigger to the default action following the §7 action selection matrix.

    Hard path → prefer LIQUIDATE or PAUSE depending on breach type.
    Soft path → prefer PAUSE or RISK_OFF for controlled degradation.

    Caller may override the selected action via KillSwitchController.dispatch().
    """
    reason = trigger.reason

    # --- Hard triggers ---
    if reason == HardTriggerReason.SEVERITY_1_INCIDENT.value:
        return KillSwitchActionType.PAUSE

    if reason == HardTriggerReason.UNAUTHORIZED_ARTIFACT_BINDING_MISMATCH.value:
        # severe mismatch → liquidate_then_replace semantics: we emit LIQUIDATE
        # and trust the runtime-manager to trigger replace on completion.
        return KillSwitchActionType.LIQUIDATE

    if reason == HardTriggerReason.RUNTIME_UNEXPECTED_ORDER_PATTERN.value:
        return KillSwitchActionType.PAUSE

    if reason == HardTriggerReason.BROKER_POSITION_MISMATCH_CRITICAL.value:
        return KillSwitchActionType.LIQUIDATE

    if reason == HardTriggerReason.DRAWDOWN_HARD_BREACH.value:
        return KillSwitchActionType.RISK_OFF

    if reason == HardTriggerReason.OPERATOR_EMERGENCY_STOP.value:
        return KillSwitchActionType.PAUSE

    # --- Soft triggers ---
    if reason == SoftTriggerReason.DRIFT_ABOVE_WARNING_THRESHOLD.value:
        return KillSwitchActionType.RISK_OFF

    if reason == SoftTriggerReason.REPEATED_REJECT_RATE_INCREASE.value:
        return KillSwitchActionType.PAUSE

    if reason == SoftTriggerReason.SLIPPAGE_DETERIORATION.value:
        return KillSwitchActionType.PAUSE

    if reason == SoftTriggerReason.LOADER_ANOMALY_NO_BREACH.value:
        return KillSwitchActionType.REPLACE

    if reason == SoftTriggerReason.CANARY_UNDERPERFORMANCE.value:
        return KillSwitchActionType.PAUSE

    # Unreachable (EmergencyTrigger.__post_init__ validates reason).
    raise KillSwitchError(f"No action mapping for reason: {reason!r}")


def _advance_safe_mode(
    current: SafeModeState,
    action: KillSwitchActionType,
    emergency_class: EmergencyClass,
) -> SafeModeState:
    """
    Advance the safe-mode state machine based on the dispatched action (§9).

    Rules
    -----
    PAUSE → PAUSED (unless already in a more restrictive state)
    LIQUIDATE → PAUSED (positions being flattened)
    RISK_OFF → RISK_OFF (reduce exposure)
    REPLACE → GUARDED (fallback active, still monitoring)
    TERMINATE → PAUSED (process gone; recovery testing next)

    Hard-class triggers always advance to at least GUARDED.
    Soft-class triggers may remain in NORMAL if already GUARDED.
    """
    candidate: str

    if action == KillSwitchActionType.PAUSE:
        candidate = SafeModeState.PAUSED.value
    elif action == KillSwitchActionType.LIQUIDATE:
        candidate = SafeModeState.PAUSED.value
    elif action == KillSwitchActionType.RISK_OFF:
        candidate = SafeModeState.RISK_OFF.value
    elif action == KillSwitchActionType.REPLACE:
        candidate = SafeModeState.GUARDED.value
    elif action == KillSwitchActionType.TERMINATE:
        candidate = SafeModeState.PAUSED.value
    else:
        candidate = SafeModeState.GUARDED.value

    # Validate the transition is allowed; if not, find the least restrictive
    # valid next state that represents escalation.
    allowed = _SAFE_MODE_TRANSITIONS.get(current.value, [])
    if candidate in allowed:
        return SafeModeState(candidate)

    # If direct transition isn't in the table (e.g. PAUSED → RISK_OFF), keep
    # the current state — it is already at least as restrictive.
    # Special case: any hard-trigger must escalate from NORMAL to at least GUARDED.
    if (
        emergency_class == EmergencyClass.HARD
        and current == SafeModeState.NORMAL
        and SafeModeState.GUARDED.value in allowed
    ):
        return SafeModeState.GUARDED

    return current


# ---------------------------------------------------------------------------
# KillSwitchController
# ---------------------------------------------------------------------------

class KillSwitchController:
    """
    Routes emergency triggers to the runtime-manager fast path.

    Design goals
    ------------
    * No blocking I/O in dispatch() — callers can time the hot path.
    * Immutable audit entries accumulated in-memory; flushed by the caller
      (or an audit subsystem) after acknowledging the command.
    * Safe-mode state is tracked per-pool and advanced atomically with every
      dispatch so the controller always reflects the current operational state.

    Usage
    -----
    controller = KillSwitchController()

    trigger = EmergencyTrigger(
        reason=HardTriggerReason.DRAWDOWN_HARD_BREACH.value,
        capital_pool_id="pool-alpha",
        actor_id="alert-engine",
    )
    outcome = controller.dispatch(trigger)
    # outcome.command  → send to runtime-manager
    # outcome.audit_entry → persist before ack
    """

    def __init__(self) -> None:
        # pool_id → SafeModeState
        self._safe_mode: Dict[str, SafeModeState] = {}
        # chronological list of all audit entries produced this session
        self._audit_log: List[KillSwitchAuditEntry] = []

    # ---- Read helpers ----

    def safe_mode_for(self, capital_pool_id: str) -> SafeModeState:
        """Return the current safe-mode state for a pool (NORMAL if unknown)."""
        return self._safe_mode.get(capital_pool_id, SafeModeState.NORMAL)

    def audit_log(self) -> List[KillSwitchAuditEntry]:
        """Return a copy of all audit entries produced this session."""
        return list(self._audit_log)

    # ---- Core fast path ----

    def classify(self, trigger: EmergencyTrigger) -> EmergencyClass:
        """Classify a trigger as SOFT or HARD. O(1), no side effects."""
        return trigger.emergency_class()

    def dispatch(
        self,
        trigger: EmergencyTrigger,
        *,
        action_override: Optional[KillSwitchActionType] = None,
        fallback_artifact_id: Optional[str] = None,
        fallback_artifact_version: Optional[str] = None,
        issued_at: Optional[str] = None,
    ) -> KillSwitchOutcome:
        """
        Dispatch an emergency command to the runtime-manager fast path.

        Parameters
        ----------
        trigger                 : the incoming EmergencyTrigger
        action_override         : caller may force a specific action type
                                  instead of the default from §7 matrix
        fallback_artifact_id    : required when action is REPLACE
        fallback_artifact_version : required when action is REPLACE
        issued_at               : UTC timestamp override (for testing)

        Returns
        -------
        KillSwitchOutcome with an immutable command and audit entry.
        The command must be forwarded to the runtime-manager; the audit
        entry must be persisted before the command is acknowledged.
        """
        ec = self.classify(trigger)
        action = action_override if action_override is not None else _select_action(trigger, ec)

        if action == KillSwitchActionType.REPLACE and (
            not fallback_artifact_id or not fallback_artifact_version
        ):
            raise KillSwitchError(
                "fallback_artifact_id and fallback_artifact_version are required when "
                "action_type is REPLACE"
            )

        now = issued_at or utc_now()
        command_id = _new_id("ks")
        priority = 1 if ec == EmergencyClass.HARD else 2

        command = KillSwitchCommand(
            command_id=command_id,
            trigger_id=trigger.trigger_id,
            emergency_class=ec.value,
            action_type=action.value,
            capital_pool_id=trigger.capital_pool_id,
            actor_id=trigger.actor_id,
            issued_at=now,
            priority=priority,
            binding_id=trigger.binding_id,
            fallback_artifact_id=fallback_artifact_id,
            fallback_artifact_version=fallback_artifact_version,
            metadata={"trigger_reason": trigger.reason, **trigger.context},
        )

        current_mode = self.safe_mode_for(trigger.capital_pool_id)
        next_mode = _advance_safe_mode(current_mode, action, ec)
        self._safe_mode[trigger.capital_pool_id] = next_mode

        audit_entry = KillSwitchAuditEntry(
            audit_id=_new_id("audit"),
            command_id=command_id,
            trigger_id=trigger.trigger_id,
            reason=trigger.reason,
            emergency_class=ec.value,
            action_type=action.value,
            capital_pool_id=trigger.capital_pool_id,
            actor_id=trigger.actor_id,
            audited_at=now,
            safe_mode_before=current_mode.value,
            safe_mode_after=next_mode.value,
            binding_id=trigger.binding_id,
            outcome_note=(
                f"{ec.value} emergency: {trigger.reason} -> {action.value}; "
                f"safe mode {current_mode.value} -> {next_mode.value}"
            ),
        )
        self._audit_log.append(audit_entry)

        return KillSwitchOutcome(
            command=command,
            audit_entry=audit_entry,
            safe_mode_after=next_mode,
        )

    def advance_safe_mode(
        self,
        capital_pool_id: str,
        target_state: SafeModeState,
        *,
        actor_id: str,
        note: Optional[str] = None,
    ) -> SafeModeState:
        """
        Manually advance (or restore) the safe-mode state for a pool.

        Used by the governance layer to signal recovery milestones (e.g.,
        advancing from PAUSED to RECOVERY_TESTING after conditions are cleared).

        Raises KillSwitchError if the transition is not in the allowed table.
        """
        current = self.safe_mode_for(capital_pool_id)
        allowed = _SAFE_MODE_TRANSITIONS.get(current.value, [])
        if target_state.value not in allowed:
            raise KillSwitchError(
                f"Invalid safe-mode transition for pool {capital_pool_id!r}: "
                f"{current.value!r} -> {target_state.value!r}. "
                f"Allowed: {allowed}"
            )
        self._safe_mode[capital_pool_id] = target_state

        # Emit an audit entry so the transition is traceable.
        now = utc_now()
        audit_entry = KillSwitchAuditEntry(
            audit_id=_new_id("audit"),
            command_id=_new_id("manual-safe-mode"),
            trigger_id=_new_id("manual"),
            reason="manual_safe_mode_advance",
            emergency_class="manual",
            action_type="advance_safe_mode",
            capital_pool_id=capital_pool_id,
            actor_id=actor_id,
            audited_at=now,
            safe_mode_before=current.value,
            safe_mode_after=target_state.value,
            outcome_note=note,
        )
        self._audit_log.append(audit_entry)
        return target_state

    # ---- State serialization for durable persistence ----

    def dump_state(self) -> Dict[str, Any]:
        """Return a JSON-serializable snapshot of safe-mode and audit state."""
        return {
            "safe_mode": {
                pool_id: state.value
                for pool_id, state in self._safe_mode.items()
            },
            "audit_log": [e.to_dict() for e in self._audit_log],
        }

    def load_state(self, data: Dict[str, Any]) -> None:
        """Restore controller state from a snapshot produced by dump_state()."""
        self._safe_mode = {
            pool_id: SafeModeState(state_val)
            for pool_id, state_val in data.get("safe_mode", {}).items()
        }
        self._audit_log = [
            _audit_entry_from_dict(entry)
            for entry in data.get("audit_log", [])
        ]


def _audit_entry_from_dict(d: Dict[str, Any]) -> KillSwitchAuditEntry:
    return KillSwitchAuditEntry(
        audit_id=d["audit_id"],
        command_id=d["command_id"],
        trigger_id=d["trigger_id"],
        reason=d["reason"],
        emergency_class=d["emergency_class"],
        action_type=d["action_type"],
        capital_pool_id=d["capital_pool_id"],
        actor_id=d["actor_id"],
        audited_at=d["audited_at"],
        safe_mode_before=d["safe_mode_before"],
        safe_mode_after=d["safe_mode_after"],
        binding_id=d.get("binding_id"),
        outcome_note=d.get("outcome_note"),
    )
