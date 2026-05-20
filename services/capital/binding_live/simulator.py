"""Simulation-only capital binding live activation walkthrough.

The simulator composes the capital binding live readiness packet with sponsor
responsibility, conflict-resolution, and lifecycle gates. It may prepare
risk-owner and operator approvals in memory, but it never flips production
flags, mutates capital/runtime binding state, dispatches runtime manager
commands, calls broker APIs, submits orders, or writes evidence.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from .dashboard import build_capital_binding_go_no_go_dashboard
from .readiness_model import (
    PASSING_APPROVAL_STATUS,
    CapitalBindingLiveReadiness,
    CapitalBindingLiveReadinessError,
    validate_readiness,
)


SIMULATION_VERSION = "1.0"
SIMULATION_SOURCE = "CBL-005-V2 capital binding live simulator"
PASS_STATUS = "passed"
BLOCKED_STATUS = "blocked"

SIMULATABLE_APPROVAL_STATUSES = {"pending", "missing"}

READINESS_KEYS = (
    "readiness",
    "readiness_packet",
    "capital_binding_live_readiness",
)
SPONSOR_RESPONSIBILITY_KEYS = (
    "sponsor_responsibility",
    "sponsor_mandate",
    "responsibility",
)
CONFLICT_LOG_KEYS = (
    "conflict_log",
    "conflict_resolution_log",
)
LIFECYCLE_KEYS = (
    "lifecycle",
    "binding_lifecycle",
    "binding_lifecycle_state",
)

SAFETY_GUARDS = (
    "simulation_only",
    "no_production_flag_flip",
    "no_capital_binding_mutation",
    "no_runtime_manager_dispatch",
    "no_runtime_binding_mutation",
    "no_broker_api_call",
    "no_order_submission",
    "no_evidence_write",
    "input_not_mutated",
)

LIVE_SIDE_EFFECT_KEYS = {
    "activate_capital_binding_live",
    "activate_live",
    "activate_live_binding",
    "apply_capital_binding_live",
    "apply_live_binding",
    "broker_api_call",
    "call_broker_api",
    "capital_binding_live_enabled",
    "commit_capital_binding_live",
    "commit_live_binding",
    "dispatch_runtime_manager_command",
    "enable_capital_binding_live",
    "enable_live",
    "enable_live_binding",
    "enable_live_trading",
    "execute_live_binding",
    "flip_production_flags",
    "ingest_telemetry",
    "live_binding_enabled",
    "live_order_allowed",
    "live_side_effect_requested",
    "mutate_capital_binding",
    "mutate_runtime_binding",
    "persist_activation",
    "persist_live_binding",
    "place_order",
    "production_flags_changed",
    "runtime_manager_dispatch",
    "send_order",
    "set_capital_binding_live_enabled",
    "side_effects_allowed",
    "submit_order",
    "write_audit",
    "write_capital_binding",
    "write_evidence",
    "write_production_config",
    "write_runtime_binding",
}

FALSE_MUST_BLOCK_KEYS = {
    "dry_run",
    "simulation_only",
}


class BindingLiveActivationSimulationError(ValueError):
    """Raised when the binding live simulation cannot pass activation gates."""


@dataclass(frozen=True)
class BindingLiveActivationSimulationIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


@dataclass(frozen=True)
class BindingLiveActivationSimulationGate:
    id: str
    name: str
    passed: bool
    blocking_reasons: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return PASS_STATUS if self.passed else BLOCKED_STATUS

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "passed": self.passed,
            "blocking_reasons": list(self.blocking_reasons),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class BindingLiveActivationSimulationResult:
    version: str
    source: str
    passed: bool
    can_bind_live: bool
    would_bind_live: bool
    simulation_only: bool
    production_flags_changed: bool
    live_side_effects_attempted: bool
    live_side_effects_executed: bool
    safety_guards: tuple[str, ...]
    gates: tuple[BindingLiveActivationSimulationGate, ...]
    blocking_reasons: tuple[str, ...]
    issues: tuple[BindingLiveActivationSimulationIssue, ...]
    simulated_approvals: Mapping[str, Mapping[str, Any]]
    production_flags: Mapping[str, Any]
    readiness_preview: Mapping[str, Any]
    dashboard_preview: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "passed": self.passed,
            "can_bind_live": self.can_bind_live,
            "would_bind_live": self.would_bind_live,
            "simulation_only": self.simulation_only,
            "production_flags_changed": self.production_flags_changed,
            "live_side_effects_attempted": self.live_side_effects_attempted,
            "live_side_effects_executed": self.live_side_effects_executed,
            "safety_guards": list(self.safety_guards),
            "blocking_reasons": list(self.blocking_reasons),
            "issues": [issue.to_dict() for issue in self.issues],
            "gates": [gate.to_dict() for gate in self.gates],
            "simulated_approvals": {
                role: dict(approval)
                for role, approval in self.simulated_approvals.items()
            },
            "production_flags": dict(self.production_flags),
            "readiness_preview": dict(self.readiness_preview),
            "dashboard_preview": dict(self.dashboard_preview),
        }


def simulate_binding_live_activation(
    packet: Mapping[str, Any],
    *,
    at: datetime | str | None = None,
) -> BindingLiveActivationSimulationResult:
    """Walk a capital binding live packet through simulation-only gates.

    ``packet`` is deep-copied before any simulated approval is prepared. The
    original packet is never mutated and live side-effect requests are reported
    as blockers instead of being honored.
    """

    if not isinstance(packet, Mapping):
        issue = BindingLiveActivationSimulationIssue(
            "invalid_simulation_packet",
            "$",
            "binding live simulation packet must be an object",
        )
        request_gate = _gate(
            "simulation_packet",
            "Simulation packet object",
            False,
            (issue.message,),
            {"passed": False},
        )
        return _build_result(
            gates=(request_gate,),
            issues=(issue,),
            simulated_approvals={},
            production_flags=_production_flags({}),
            readiness_preview={},
            dashboard_preview={},
        )

    payload = copy.deepcopy(dict(packet))
    safety_issues = tuple(_live_side_effect_issues(payload))
    safety_gate = _gate(
        "side_effect_guard",
        "Simulation side-effect guard",
        not safety_issues,
        tuple(issue.message for issue in safety_issues),
        {
            "passed": not safety_issues,
            "blocked_paths": [issue.path for issue in safety_issues],
            "safety_guards": list(SAFETY_GUARDS),
        },
    )

    readiness_gate, readiness = _readiness_gate(_first_present(payload, READINESS_KEYS))
    if readiness is None:
        return _build_result(
            gates=(readiness_gate, safety_gate),
            issues=safety_issues,
            simulated_approvals={},
            production_flags=_production_flags(payload),
            readiness_preview={},
            dashboard_preview={},
        )

    sponsor_responsibility = _first_present(payload, SPONSOR_RESPONSIBILITY_KEYS)
    conflict_log = _first_present(payload, CONFLICT_LOG_KEYS)
    lifecycle = _first_present(payload, LIFECYCLE_KEYS)
    working_readiness = readiness.to_dict()
    initial_non_approval_blockers = _non_approval_blockers(readiness)
    simulated_approvals: dict[str, Mapping[str, Any]] = {}

    risk_blockers = []
    if not readiness_gate.passed:
        risk_blockers.append("readiness_packet_invalid")
    if not safety_gate.passed:
        risk_blockers.append("side_effect_guard_blocked")
    risk_status = _approval_status(working_readiness, "risk_owner")
    risk_blockers.extend(_explicit_approval_blockers("risk_owner", risk_status))
    risk_gate = _gate(
        "risk_owner",
        "Risk-owner approval gate",
        not risk_blockers,
        risk_blockers,
        {
            "input_status": risk_status,
            "simulatable_statuses": sorted(SIMULATABLE_APPROVAL_STATUSES),
        },
    )
    if risk_gate.passed:
        working_readiness, risk_approval = _ensure_simulated_approval(
            working_readiness,
            "risk_owner",
        )
        if risk_approval is not None:
            simulated_approvals["risk_owner"] = risk_approval

    operator_blockers = []
    if not risk_gate.passed:
        operator_blockers.append("risk_owner_gate_blocked")
    if not safety_gate.passed:
        operator_blockers.append("side_effect_guard_blocked")
    operator_status = _approval_status(working_readiness, "operator")
    operator_blockers.extend(_explicit_approval_blockers("operator", operator_status))
    operator_gate = _gate(
        "operator",
        "Operator approval gate",
        not operator_blockers,
        operator_blockers,
        {
            "input_status": operator_status,
            "simulatable_statuses": sorted(SIMULATABLE_APPROVAL_STATUSES),
        },
    )
    if operator_gate.passed:
        working_readiness, operator_approval = _ensure_simulated_approval(
            working_readiness,
            "operator",
        )
        if operator_approval is not None:
            simulated_approvals["operator"] = operator_approval

    if not operator_gate.passed:
        activation_gate = _gate(
            "activation_preview",
            "Capital binding live activation preview",
            False,
            ("operator_gate_blocked",),
            {"readiness_preview": {}, "dashboard_preview": {}},
        )
        return _build_result(
            gates=(readiness_gate, safety_gate, risk_gate, operator_gate, activation_gate),
            issues=safety_issues,
            simulated_approvals=simulated_approvals,
            production_flags=_production_flags(payload),
            readiness_preview={},
            dashboard_preview={},
        )

    readiness_preview, dashboard_preview, activation_blockers = _build_activation_preview(
        working_readiness,
        sponsor_responsibility,
        conflict_log,
        lifecycle,
        initial_non_approval_blockers=initial_non_approval_blockers,
        at=at,
    )
    activation_gate = _gate(
        "activation_preview",
        "Capital binding live activation preview",
        not activation_blockers,
        activation_blockers,
        {
            "readiness_preview": readiness_preview,
            "dashboard_preview": dashboard_preview,
        },
    )
    return _build_result(
        gates=(readiness_gate, safety_gate, risk_gate, operator_gate, activation_gate),
        issues=safety_issues,
        simulated_approvals=simulated_approvals,
        production_flags=_production_flags(payload),
        readiness_preview=readiness_preview,
        dashboard_preview=dashboard_preview,
    )


def simulate_binding_live_activation_or_raise(
    packet: Mapping[str, Any],
    *,
    at: datetime | str | None = None,
) -> BindingLiveActivationSimulationResult:
    """Run the simulator and raise a compact fail-closed error on blockers."""

    result = simulate_binding_live_activation(packet, at=at)
    if not result.passed:
        raise BindingLiveActivationSimulationError("; ".join(result.blocking_reasons))
    return result


def simulate_live_binding_activation(
    packet: Mapping[str, Any],
    *,
    at: datetime | str | None = None,
) -> BindingLiveActivationSimulationResult:
    """Compatibility alias for callers that use the natural-language order."""

    return simulate_binding_live_activation(packet, at=at)


def simulate_live_binding_activation_or_raise(
    packet: Mapping[str, Any],
    *,
    at: datetime | str | None = None,
) -> BindingLiveActivationSimulationResult:
    """Compatibility alias for callers that use the natural-language order."""

    return simulate_binding_live_activation_or_raise(packet, at=at)


def _readiness_gate(
    packet: Any,
) -> tuple[BindingLiveActivationSimulationGate, CapitalBindingLiveReadiness | None]:
    try:
        readiness = validate_readiness(packet)
    except (CapitalBindingLiveReadinessError, TypeError) as exc:
        reason = f"readiness_packet_invalid: {exc}"
        return (
            _gate(
                "readiness_packet",
                "Capital binding live readiness packet",
                False,
                (reason,),
                {"passed": False},
            ),
            None,
        )
    return (
        _gate(
            "readiness_packet",
            "Capital binding live readiness packet",
            True,
            (),
            {
                "readiness_id": readiness.readiness_id,
                "binding_id": readiness.binding_id,
                "initial_can_bind_live": readiness.result.can_bind_live,
                "initial_blocking_reasons": list(readiness.result.blocking_reasons),
            },
        ),
        readiness,
    )


def _build_activation_preview(
    readiness_payload: Mapping[str, Any],
    sponsor_responsibility: Any,
    conflict_log: Any,
    lifecycle: Any,
    *,
    initial_non_approval_blockers: Sequence[str],
    at: datetime | str | None,
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    try:
        provisional_payload = copy.deepcopy(dict(readiness_payload))
        provisional_payload["result"] = {
            "can_bind_live": True,
            "blocking_reasons": [],
        }
        provisional_readiness = validate_readiness(provisional_payload)
        provisional_dashboard = build_capital_binding_go_no_go_dashboard(
            provisional_readiness,
            sponsor_responsibility,
            conflict_log,
            lifecycle,
            at=at,
        )
        final_blockers = _unique(
            tuple(initial_non_approval_blockers)
            + tuple(provisional_dashboard.blocking_reasons)
        )
        readiness_preview = provisional_readiness.to_dict()
        readiness_preview["result"] = {
            "can_bind_live": not final_blockers,
            "blocking_reasons": list(final_blockers),
        }
        final_readiness = validate_readiness(readiness_preview)
        dashboard = build_capital_binding_go_no_go_dashboard(
            final_readiness,
            sponsor_responsibility,
            conflict_log,
            lifecycle,
            at=at,
        )
        dashboard_preview = dashboard.to_dict()
        activation_blockers = _unique(
            final_blockers
            + tuple(dashboard.blocking_reasons)
        )
        if not dashboard.can_bind_live and not activation_blockers:
            activation_blockers = ("capital_binding_live_dashboard_blocked",)
        return final_readiness.to_dict(), dashboard_preview, activation_blockers
    except Exception as exc:  # pragma: no cover - defensive fail-closed boundary
        reason = f"activation_preview_invalid: {exc}"
        return {}, {}, (reason,)


def _ensure_simulated_approval(
    readiness_payload: Mapping[str, Any],
    role: str,
) -> tuple[dict[str, Any], Mapping[str, Any] | None]:
    payload = copy.deepcopy(dict(readiness_payload))
    approvals = dict(payload.get("approval") or {})
    status = _status_value(approvals.get(role))
    if status == PASSING_APPROVAL_STATUS:
        payload["approval"] = approvals
        return payload, None

    approval = {
        "status": PASSING_APPROVAL_STATUS,
        "approval_ref": f"simulation://CBL-005-V2/{role}",
        "simulated": True,
        "recorded": False,
        "side_effect": "none",
    }
    approvals[role] = PASSING_APPROVAL_STATUS
    payload["approval"] = approvals
    return payload, approval


def _explicit_approval_blockers(role: str, status: str) -> tuple[str, ...]:
    if status == PASSING_APPROVAL_STATUS or status in SIMULATABLE_APPROVAL_STATUSES:
        return ()
    return (f"cannot simulate over explicit {role} approval status: {status}",)


def _non_approval_blockers(
    readiness: CapitalBindingLiveReadiness,
) -> tuple[str, ...]:
    approval_blockers = {
        _approval_blocker("risk_owner", readiness.approval.risk_owner),
        _approval_blocker("operator", readiness.approval.operator),
    }
    return tuple(
        reason
        for reason in readiness.result.blocking_reasons
        if reason not in approval_blockers
    )


def _approval_blocker(role: str, status: str) -> str:
    return f"{role}_approval_{status}"


def _approval_status(readiness_payload: Mapping[str, Any], role: str) -> str:
    approvals = readiness_payload.get("approval") or {}
    if not isinstance(approvals, Mapping):
        return "missing"
    return _status_value(approvals.get(role))


def _status_value(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("status") or value.get("state")
        if value.get("approved") is True and not raw:
            return PASSING_APPROVAL_STATUS
        return str(raw or "missing").strip().lower()
    if isinstance(value, bool):
        return PASSING_APPROVAL_STATUS if value else "missing"
    return str(value or "missing").strip().lower()


def _gate(
    gate_id: str,
    name: str,
    passed: bool,
    blocking_reasons: Sequence[str],
    details: Mapping[str, Any],
) -> BindingLiveActivationSimulationGate:
    return BindingLiveActivationSimulationGate(
        id=gate_id,
        name=name,
        passed=passed,
        blocking_reasons=_unique(tuple(blocking_reasons)),
        details=dict(details),
    )


def _build_result(
    *,
    gates: Sequence[BindingLiveActivationSimulationGate],
    issues: Sequence[BindingLiveActivationSimulationIssue],
    simulated_approvals: Mapping[str, Mapping[str, Any]],
    production_flags: Mapping[str, Any],
    readiness_preview: Mapping[str, Any],
    dashboard_preview: Mapping[str, Any],
) -> BindingLiveActivationSimulationResult:
    gate_tuple = tuple(gates)
    issue_tuple = tuple(issues)
    passed = all(gate.passed for gate in gate_tuple) and not issue_tuple
    blocking_reasons = _unique(
        tuple(issue.message for issue in issue_tuple)
        + tuple(
            reason
            for gate in gate_tuple
            if not gate.passed
            for reason in gate.blocking_reasons
        )
    )
    return BindingLiveActivationSimulationResult(
        version=SIMULATION_VERSION,
        source=SIMULATION_SOURCE,
        passed=passed,
        can_bind_live=passed,
        would_bind_live=passed,
        simulation_only=True,
        production_flags_changed=False,
        live_side_effects_attempted=any(
            issue.code == "live_side_effect_requested" for issue in issue_tuple
        ),
        live_side_effects_executed=False,
        safety_guards=SAFETY_GUARDS,
        gates=gate_tuple,
        blocking_reasons=blocking_reasons,
        issues=issue_tuple,
        simulated_approvals={
            role: dict(approval)
            for role, approval in simulated_approvals.items()
        },
        production_flags=dict(production_flags),
        readiness_preview=dict(readiness_preview),
        dashboard_preview=dict(dashboard_preview),
    )


def _live_side_effect_issues(
    value: Any,
    path: str = "$",
) -> list[BindingLiveActivationSimulationIssue]:
    issues: list[BindingLiveActivationSimulationIssue] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_key(key)
            child_path = _child_path(path, str(key))
            if normalized in LIVE_SIDE_EFFECT_KEYS and _requested(child):
                issues.append(
                    BindingLiveActivationSimulationIssue(
                        "live_side_effect_requested",
                        child_path,
                        "simulator refuses live side-effect or production flag requests",
                    )
                )
            if normalized in FALSE_MUST_BLOCK_KEYS and child is False:
                issues.append(
                    BindingLiveActivationSimulationIssue(
                        "live_side_effect_requested",
                        child_path,
                        "simulator refuses non-dry-run or non-simulation requests",
                    )
                )
            issues.extend(_live_side_effect_issues(child, child_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            issues.extend(_live_side_effect_issues(child, f"{path}[{index}]"))
    return issues


def _first_present(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _production_flags(payload: Mapping[str, Any]) -> dict[str, bool]:
    before = _production_flag_enabled(payload)
    return {
        "before": before,
        "after": before,
        "changed": False,
    }


def _production_flag_enabled(payload: Mapping[str, Any]) -> bool:
    flags = payload.get("production_flags")
    if not isinstance(flags, Mapping):
        return False
    for key, value in flags.items():
        if _normalized_key(key) in {
            "capital_binding_live_enabled",
            "live_binding_enabled",
            "production_live_enabled",
        }:
            return _requested(value)
    return False


def _normalized_key(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_")


def _child_path(parent: str, key: str) -> str:
    return key if parent == "$" else f"{parent}.{key}"


def _requested(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "none", "no"}
    if isinstance(value, (Sequence, Mapping)):
        return bool(value)
    return True


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)
