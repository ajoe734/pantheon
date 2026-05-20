"""Simulation-only broker production-live activation walkthrough.

The simulator composes the existing criteria validator with the risk-owner and
operator gate checklists. It prepares simulated approvals only in memory, then
validates the final activation request. It never flips production flags,
dispatches Runtime Manager commands, calls broker APIs, or mutates runtime
state.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .operator_checklist import generate_operator_checklist
from .risk_owner_checklist import generate_risk_owner_checklist
from .validator import (
    PASSING_APPROVAL_STATUSES,
    validate_activation_request,
    validate_criteria_shape,
)


SIMULATION_VERSION = "1.0"
SIMULATION_SOURCE = "BLA-009-V2 broker live activation simulator"
PASS_STATUS = "passed"
BLOCKED_STATUS = "blocked"

SIMULATABLE_APPROVAL_STATUSES = {"missing", "pending"}

SAFETY_GUARDS = (
    "simulation_only",
    "no_production_flag_flip",
    "no_runtime_manager_dispatch",
    "no_runtime_binding_mutation",
    "no_broker_api_call",
    "no_order_submission",
    "no_telemetry_ingest",
    "input_not_mutated",
)

LIVE_SIDE_EFFECT_KEYS = {
    "activate_live",
    "activate_production_live",
    "apply_live_activation",
    "broker_api_call",
    "call_broker_api",
    "commit_live_activation",
    "dispatch_runtime_manager_command",
    "enable_live",
    "enable_live_trading",
    "execute_live_activation",
    "flip_production_flags",
    "ingest_telemetry",
    "live_side_effect_requested",
    "mutate_runtime_binding",
    "persist_activation",
    "place_order",
    "production_flags_changed",
    "production_live_enabled",
    "runtime_manager_dispatch",
    "send_order",
    "set_production_live_enabled",
    "submit_order",
    "write_audit",
    "write_production_config",
    "write_runtime_binding",
}


class LiveActivationSimulationError(ValueError):
    """Raised when the simulation result fails activation readiness."""


@dataclass(frozen=True)
class LiveActivationSimulationIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class LiveActivationSimulationGate:
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
class LiveActivationSimulationResult:
    version: str
    source: str
    passed: bool
    can_activate: bool
    would_activate_live: bool
    simulation_only: bool
    production_flags_changed: bool
    live_side_effects_attempted: bool
    live_side_effects_executed: bool
    safety_guards: tuple[str, ...]
    gates: tuple[LiveActivationSimulationGate, ...]
    blocking_reasons: tuple[str, ...]
    issues: tuple[LiveActivationSimulationIssue, ...]
    simulated_approvals: Mapping[str, Mapping[str, Any]]
    production_flags: Mapping[str, Any]
    final_validation: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "passed": self.passed,
            "can_activate": self.can_activate,
            "would_activate_live": self.would_activate_live,
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
            "final_validation": dict(self.final_validation),
        }


def simulate_live_activation(
    request: Mapping[str, Any],
    criteria: Mapping[str, Any] | None = None,
) -> LiveActivationSimulationResult:
    """Walk a candidate activation through criteria, dual gate, and validation.

    ``request`` is deep-copied before any simulated approval is prepared. The
    original request is never mutated and no production/live side-effect request
    is honored.
    """

    criteria_result = validate_criteria_shape(criteria)
    criteria_gate = _gate(
        "criteria",
        "Criteria JSON shape",
        criteria_result.passed,
        criteria_result.blocking_reasons,
        criteria_result.to_dict(),
    )

    if not isinstance(request, Mapping):
        issue = LiveActivationSimulationIssue(
            "invalid_activation_request",
            "$",
            "activation request must be an object",
        )
        request_gate = _gate(
            "activation_request",
            "Activation request object",
            False,
            (issue.message,),
            {"passed": False},
        )
        return _build_result(
            gates=(criteria_gate, request_gate),
            issues=(issue,),
            simulated_approvals={},
            final_validation={},
        )

    candidate = copy.deepcopy(dict(request))
    safety_issues = tuple(_live_side_effect_issues(candidate))
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

    simulated_approvals: dict[str, Mapping[str, Any]] = {}

    risk_checklist = generate_risk_owner_checklist(candidate, criteria)
    risk_reasons = _unique(
        tuple(risk_checklist.blocking_reasons)
        + _explicit_approval_blockers(candidate, "risk_owner")
    )
    risk_gate = _gate(
        "risk_owner",
        "Risk-owner gate",
        criteria_result.passed and safety_gate.passed and not risk_reasons,
        risk_reasons,
        risk_checklist.to_dict(),
    )

    operator_candidate = candidate
    if risk_gate.passed:
        operator_candidate, risk_approval = _ensure_simulated_approval(
            operator_candidate,
            "risk_owner",
        )
        if risk_approval is not None:
            simulated_approvals["risk_owner"] = risk_approval

    operator_checklist = generate_operator_checklist(operator_candidate, criteria)
    operator_reasons = _unique(
        tuple(operator_checklist.blocking_reasons)
        + _explicit_approval_blockers(candidate, "operator")
    )
    operator_gate = _gate(
        "operator",
        "Operator gate",
        criteria_result.passed
        and safety_gate.passed
        and risk_gate.passed
        and not operator_reasons,
        operator_reasons,
        operator_checklist.to_dict(),
    )

    final_candidate = operator_candidate
    if operator_gate.passed:
        final_candidate, operator_approval = _ensure_simulated_approval(
            final_candidate,
            "operator",
        )
        if operator_approval is not None:
            simulated_approvals["operator"] = operator_approval

    final_validation = validate_activation_request(final_candidate, criteria)
    final_gate = _gate(
        "activation_validation",
        "Final activation validation",
        criteria_result.passed
        and safety_gate.passed
        and risk_gate.passed
        and operator_gate.passed
        and final_validation.passed,
        final_validation.blocking_reasons,
        final_validation.to_dict(),
    )

    return _build_result(
        gates=(criteria_gate, safety_gate, risk_gate, operator_gate, final_gate),
        issues=safety_issues,
        simulated_approvals=simulated_approvals,
        final_validation=final_validation.to_dict(),
    )


def simulate_live_activation_or_raise(
    request: Mapping[str, Any],
    criteria: Mapping[str, Any] | None = None,
) -> LiveActivationSimulationResult:
    """Run the simulator and raise a compact fail-closed error on blockers."""

    result = simulate_live_activation(request, criteria)
    if not result.passed:
        raise LiveActivationSimulationError("; ".join(result.blocking_reasons))
    return result


def _gate(
    gate_id: str,
    name: str,
    passed: bool,
    blocking_reasons: Sequence[str],
    details: Mapping[str, Any],
) -> LiveActivationSimulationGate:
    return LiveActivationSimulationGate(
        id=gate_id,
        name=name,
        passed=passed,
        blocking_reasons=_unique(tuple(blocking_reasons)),
        details=dict(details),
    )


def _build_result(
    *,
    gates: Sequence[LiveActivationSimulationGate],
    issues: Sequence[LiveActivationSimulationIssue],
    simulated_approvals: Mapping[str, Mapping[str, Any]],
    final_validation: Mapping[str, Any],
) -> LiveActivationSimulationResult:
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
    return LiveActivationSimulationResult(
        version=SIMULATION_VERSION,
        source=SIMULATION_SOURCE,
        passed=passed,
        can_activate=passed,
        would_activate_live=passed,
        simulation_only=True,
        production_flags_changed=False,
        live_side_effects_attempted=bool(issue_tuple),
        live_side_effects_executed=False,
        safety_guards=SAFETY_GUARDS,
        gates=gate_tuple,
        blocking_reasons=blocking_reasons,
        issues=issue_tuple,
        simulated_approvals={
            role: dict(approval)
            for role, approval in simulated_approvals.items()
        },
        production_flags={
            "before": False,
            "after": False,
            "changed": False,
        },
        final_validation=dict(final_validation),
    )


def _ensure_simulated_approval(
    request: Mapping[str, Any],
    role: str,
) -> tuple[dict[str, Any], Mapping[str, Any] | None]:
    payload = copy.deepcopy(dict(request))
    approvals = _mapping(payload.get("approvals") or payload.get("approval"))
    status = _approval_status(approvals.get(role))
    if status in PASSING_APPROVAL_STATUSES:
        payload["approvals"] = approvals
        return payload, None

    approval = {
        "status": "approved",
        "approval_ref": f"simulation://BLA-009-V2/{role}",
        "simulated": True,
        "recorded": False,
        "side_effect": "none",
    }
    approvals[role] = approval
    payload["approvals"] = approvals
    return payload, approval


def _explicit_approval_blockers(
    request: Mapping[str, Any],
    role: str,
) -> tuple[str, ...]:
    approvals = _mapping(request.get("approvals") or request.get("approval"))
    status = _approval_status(approvals.get(role))
    if status in PASSING_APPROVAL_STATUSES or status in SIMULATABLE_APPROVAL_STATUSES:
        return ()
    return (f"cannot simulate over explicit {role} approval status: {status}",)


def _approval_status(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("status") or value.get("state")
        if value.get("recorded") is True and not raw:
            return "recorded"
        if value.get("approved") is True and not raw:
            return "approved"
        return str(raw or "missing").strip().lower()
    if isinstance(value, bool):
        return "approved" if value else "missing"
    return str(value or "missing").strip().lower()


def _live_side_effect_issues(
    value: Any,
    path: str = "$",
) -> list[LiveActivationSimulationIssue]:
    issues: list[LiveActivationSimulationIssue] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = _child_path(path, str(key))
            if _normalized_key(key) in LIVE_SIDE_EFFECT_KEYS and _requested(child):
                issues.append(
                    LiveActivationSimulationIssue(
                        "live_side_effect_requested",
                        child_path,
                        "simulator refuses live side-effect or production flag requests",
                    )
                )
            issues.extend(_live_side_effect_issues(child, child_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            issues.extend(_live_side_effect_issues(child, f"{path}[{index}]"))
    return issues


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


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)
