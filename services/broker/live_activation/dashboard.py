"""Broker production-live go/no-go dashboard read model.

Composes the 2026-05-19 broker live activation criteria, risk-owner
checklist, and operator checklist into a read-only dashboard payload. This
module never records approvals, dispatches runtime commands, or enables broker
live flags.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .operator_checklist import OperatorChecklist, generate_operator_checklist
from .risk_owner_checklist import RiskOwnerChecklist, generate_risk_owner_checklist
from .validator import ValidationResult, validate_activation_request


DASHBOARD_VERSION = "1.0"
DASHBOARD_SOURCE = "2026-05-19 blueprint supplement broker go/no-go dashboard"
READY = "ready"
BLOCKED = "blocked"
GO = "go"
NO_GO = "no_go"


@dataclass(frozen=True)
class DashboardGate:
    id: str
    label: str
    status: str
    passed: bool
    ready_items: int
    blocked_items: int
    total_items: int
    progress_percent: int
    blocking_reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "passed": self.passed,
            "ready_items": self.ready_items,
            "blocked_items": self.blocked_items,
            "total_items": self.total_items,
            "progress_percent": self.progress_percent,
            "blocking_reasons": list(self.blocking_reasons),
            "evidence_refs": list(self.evidence_refs),
            "source": self.source,
        }


@dataclass(frozen=True)
class DashboardProgress:
    ready_gates: int
    blocked_gates: int
    total_gates: int
    ready_items: int
    blocked_items: int
    total_items: int
    progress_percent: int

    def to_dict(self) -> dict[str, int]:
        return {
            "ready_gates": self.ready_gates,
            "blocked_gates": self.blocked_gates,
            "total_gates": self.total_gates,
            "ready_items": self.ready_items,
            "blocked_items": self.blocked_items,
            "total_items": self.total_items,
            "progress_percent": self.progress_percent,
        }


@dataclass(frozen=True)
class BrokerGoNoGoDashboard:
    version: str
    source: str
    readiness_state: str
    can_activate: bool
    progress: DashboardProgress
    gates: tuple[DashboardGate, ...]
    blocking_reasons: tuple[str, ...]
    activation_criteria: ValidationResult
    risk_owner_checklist: RiskOwnerChecklist
    operator_checklist: OperatorChecklist

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "readiness_state": self.readiness_state,
            "can_activate": self.can_activate,
            "passed": self.can_activate,
            "progress": self.progress.to_dict(),
            "blocking_reasons": list(self.blocking_reasons),
            "gates": [gate.to_dict() for gate in self.gates],
            "activation_criteria": self.activation_criteria.to_dict(),
            "risk_owner_checklist": self.risk_owner_checklist.to_dict(),
            "operator_checklist": self.operator_checklist.to_dict(),
        }


def build_broker_go_no_go_dashboard(
    request: Mapping[str, Any],
    criteria: Mapping[str, Any] | None = None,
) -> BrokerGoNoGoDashboard:
    """Build the read-only broker live activation go/no-go dashboard."""

    activation_criteria = validate_activation_request(request, criteria=criteria)
    risk_owner_checklist = generate_risk_owner_checklist(request, criteria=criteria)
    operator_checklist = generate_operator_checklist(request, criteria=criteria)

    gates = (
        _criteria_gate(activation_criteria),
        _risk_owner_gate(risk_owner_checklist),
        _operator_gate(operator_checklist),
    )
    progress = _progress(gates)
    blocking_reasons = _unique(
        reason
        for gate in gates
        for reason in gate.blocking_reasons
    )
    can_activate = all(gate.passed for gate in gates)

    return BrokerGoNoGoDashboard(
        version=DASHBOARD_VERSION,
        source=DASHBOARD_SOURCE,
        readiness_state=GO if can_activate else NO_GO,
        can_activate=can_activate,
        progress=progress,
        gates=gates,
        blocking_reasons=blocking_reasons,
        activation_criteria=activation_criteria,
        risk_owner_checklist=risk_owner_checklist,
        operator_checklist=operator_checklist,
    )


def _criteria_gate(result: ValidationResult) -> DashboardGate:
    return DashboardGate(
        id="activation_criteria",
        label="Activation criteria",
        status=READY if result.passed else BLOCKED,
        passed=result.passed,
        ready_items=1 if result.passed else 0,
        blocked_items=0 if result.passed else 1,
        total_items=1,
        progress_percent=100 if result.passed else 0,
        blocking_reasons=result.blocking_reasons,
        source="2026-05-19 blueprint supplement Part B2",
    )


def _risk_owner_gate(checklist: RiskOwnerChecklist) -> DashboardGate:
    return _checklist_gate(
        gate_id="risk_owner_checklist",
        label="Risk-owner checklist",
        passed=checklist.can_sign_off,
        checklist_source=checklist.source,
        items=tuple(item.to_dict() for item in checklist.items),
        blocking_reasons=checklist.blocking_reasons,
    )


def _operator_gate(checklist: OperatorChecklist) -> DashboardGate:
    return _checklist_gate(
        gate_id="operator_checklist",
        label="Operator checklist",
        passed=checklist.can_sign_off,
        checklist_source=checklist.source,
        items=tuple(item.to_dict() for item in checklist.items),
        blocking_reasons=checklist.blocking_reasons,
    )


def _checklist_gate(
    *,
    gate_id: str,
    label: str,
    passed: bool,
    checklist_source: str,
    items: Sequence[Mapping[str, Any]],
    blocking_reasons: Sequence[str],
) -> DashboardGate:
    total_items = len(items)
    blocked_items = sum(1 for item in items if item.get("status") == BLOCKED)
    ready_items = total_items - blocked_items
    return DashboardGate(
        id=gate_id,
        label=label,
        status=READY if passed else BLOCKED,
        passed=passed,
        ready_items=ready_items,
        blocked_items=blocked_items,
        total_items=total_items,
        progress_percent=_percent(ready_items, total_items),
        blocking_reasons=_unique(blocking_reasons),
        evidence_refs=_unique(
            ref
            for item in items
            for ref in _string_items(item.get("evidence_refs"))
        ),
        source=checklist_source,
    )


def _progress(gates: Sequence[DashboardGate]) -> DashboardProgress:
    total_gates = len(gates)
    ready_gates = sum(1 for gate in gates if gate.passed)
    total_items = sum(gate.total_items for gate in gates)
    ready_items = sum(gate.ready_items for gate in gates)
    blocked_items = sum(gate.blocked_items for gate in gates)
    return DashboardProgress(
        ready_gates=ready_gates,
        blocked_gates=total_gates - ready_gates,
        total_gates=total_gates,
        ready_items=ready_items,
        blocked_items=blocked_items,
        total_items=total_items,
        progress_percent=_percent(ready_items, total_items),
    )


def _percent(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return round((numerator / denominator) * 100)


def _string_items(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _unique(items: Any) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return tuple(ordered)


__all__ = [
    "BLOCKED",
    "DASHBOARD_SOURCE",
    "DASHBOARD_VERSION",
    "GO",
    "NO_GO",
    "READY",
    "BrokerGoNoGoDashboard",
    "DashboardGate",
    "DashboardProgress",
    "build_broker_go_no_go_dashboard",
]
