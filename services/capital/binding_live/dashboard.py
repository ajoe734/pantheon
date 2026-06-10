"""Capital binding live go/no-go dashboard read model.

Composes the 2026-05-19 capital binding live readiness packet, sponsor
responsibility evidence, conflict-resolution log, and binding lifecycle TTL
into a read-only dashboard payload. This module never records approvals,
mutates bindings, or enables live capital writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from .conflict_resolution_log import (
    CAPITAL_POOL_MISMATCH_BLOCKER,
    ConflictResolutionLog,
    ConflictResolutionLogError,
    evaluate_conflict_resolution_gate,
    validate_conflict_resolution_log,
)
from .lifecycle import BindingLifecycleError, BindingLifecycleState, evaluate_binding_lifecycle
from .readiness_model import (
    CapitalBindingLiveReadiness,
    CapitalBindingLiveReadinessError,
    validate_readiness,
)
from .sponsor_responsibility import (
    ACTIVE_RESPONSIBILITY_STATUS,
    SponsorPersonaResponsibility,
    SponsorPersonaResponsibilityError,
    validate_sponsor_responsibility,
)


DASHBOARD_VERSION = "1.0"
DASHBOARD_SOURCE = "2026-05-19 blueprint supplement capital binding go/no-go dashboard"
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
class ReadinessStatus:
    readiness_id: str | None
    binding_id: str | None
    persona_id: str | None
    capital_pool_id: str | None
    can_bind_live: bool
    approval: dict[str, str]
    ttl_hours: int | None
    blocking_reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "readiness_id": self.readiness_id,
                "binding_id": self.binding_id,
                "persona_id": self.persona_id,
                "capital_pool_id": self.capital_pool_id,
                "can_bind_live": self.can_bind_live,
                "approval": dict(self.approval),
                "ttl_hours": self.ttl_hours,
                "blocking_reasons": list(self.blocking_reasons),
                "evidence_refs": list(self.evidence_refs),
            }
        )


@dataclass(frozen=True)
class SponsorMandateStatus:
    responsibility_id: str | None
    sponsor_persona_id: str | None
    live_owner: str | None
    status: str
    escalation_levels: int
    passed: bool
    blocking_reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "responsibility_id": self.responsibility_id,
                "sponsor_persona_id": self.sponsor_persona_id,
                "live_owner": self.live_owner,
                "status": self.status,
                "escalation_levels": self.escalation_levels,
                "passed": self.passed,
                "blocking_reasons": list(self.blocking_reasons),
                "evidence_refs": list(self.evidence_refs),
            }
        )


@dataclass(frozen=True)
class ConflictLogStatus:
    conflict_resolution_log_id: str | None
    capital_pool_id: str | None
    passed: bool
    open_conflict_ids: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "conflict_resolution_log_id": self.conflict_resolution_log_id,
                "capital_pool_id": self.capital_pool_id,
                "passed": self.passed,
                "open_conflict_ids": list(self.open_conflict_ids),
                "blocking_reasons": list(self.blocking_reasons),
                "evidence_refs": list(self.evidence_refs),
            }
        )


@dataclass(frozen=True)
class TtlStatus:
    binding_id: str | None
    status: str
    admissible: bool
    ttl_hours: int | None
    evaluated_at: str | None
    expires_at: str | None
    blocking_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "binding_id": self.binding_id,
                "status": self.status,
                "admissible": self.admissible,
                "ttl_hours": self.ttl_hours,
                "evaluated_at": self.evaluated_at,
                "expires_at": self.expires_at,
                "blocking_reasons": list(self.blocking_reasons),
            }
        )


@dataclass(frozen=True)
class CapitalBindingGoNoGoDashboard:
    version: str
    source: str
    readiness_state: str
    can_bind_live: bool
    progress: DashboardProgress
    gates: tuple[DashboardGate, ...]
    blocking_reasons: tuple[str, ...]
    readiness: ReadinessStatus
    sponsor_mandate: SponsorMandateStatus
    conflict_log_status: ConflictLogStatus
    ttl_status: TtlStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "readiness_state": self.readiness_state,
            "can_bind_live": self.can_bind_live,
            "passed": self.can_bind_live,
            "progress": self.progress.to_dict(),
            "blocking_reasons": list(self.blocking_reasons),
            "gates": [gate.to_dict() for gate in self.gates],
            "readiness": self.readiness.to_dict(),
            "sponsor_mandate": self.sponsor_mandate.to_dict(),
            "conflict_log_status": self.conflict_log_status.to_dict(),
            "ttl_status": self.ttl_status.to_dict(),
        }


@dataclass(frozen=True)
class _ReadinessEvaluation:
    model: CapitalBindingLiveReadiness | None
    status: ReadinessStatus
    gate: DashboardGate


@dataclass(frozen=True)
class _SponsorEvaluation:
    status: SponsorMandateStatus
    gate: DashboardGate


@dataclass(frozen=True)
class _ConflictEvaluation:
    status: ConflictLogStatus
    gate: DashboardGate


@dataclass(frozen=True)
class _TtlEvaluation:
    status: TtlStatus
    gate: DashboardGate


def build_capital_binding_go_no_go_dashboard(
    readiness: Mapping[str, Any] | CapitalBindingLiveReadiness,
    sponsor_responsibility: Mapping[str, Any] | SponsorPersonaResponsibility,
    conflict_log: Mapping[str, Any] | ConflictResolutionLog,
    lifecycle: Mapping[str, Any] | BindingLifecycleState | None,
    *,
    at: datetime | str | None = None,
) -> CapitalBindingGoNoGoDashboard:
    """Build the read-only capital binding live go/no-go dashboard."""

    readiness_eval = _readiness_gate(readiness)
    sponsor_eval = _sponsor_gate(readiness_eval.model, sponsor_responsibility)
    conflict_eval = _conflict_gate(readiness_eval.model, conflict_log)
    ttl_eval = _ttl_gate(readiness_eval.model, lifecycle, at=at)
    gates = (
        readiness_eval.gate,
        sponsor_eval.gate,
        conflict_eval.gate,
        ttl_eval.gate,
    )
    progress = _progress(gates)
    blocking_reasons = _unique(
        reason
        for gate in gates
        for reason in gate.blocking_reasons
    )
    can_bind_live = all(gate.passed for gate in gates)

    return CapitalBindingGoNoGoDashboard(
        version=DASHBOARD_VERSION,
        source=DASHBOARD_SOURCE,
        readiness_state=GO if can_bind_live else NO_GO,
        can_bind_live=can_bind_live,
        progress=progress,
        gates=gates,
        blocking_reasons=blocking_reasons,
        readiness=readiness_eval.status,
        sponsor_mandate=sponsor_eval.status,
        conflict_log_status=conflict_eval.status,
        ttl_status=ttl_eval.status,
    )


def _readiness_gate(
    packet: Mapping[str, Any] | CapitalBindingLiveReadiness,
) -> _ReadinessEvaluation:
    try:
        readiness = validate_readiness(packet)
    except (CapitalBindingLiveReadinessError, TypeError) as exc:
        reasons = (f"readiness_packet_invalid: {exc}",)
        status = ReadinessStatus(
            readiness_id=None,
            binding_id=None,
            persona_id=None,
            capital_pool_id=None,
            can_bind_live=False,
            approval={},
            ttl_hours=None,
            blocking_reasons=reasons,
        )
        return _ReadinessEvaluation(
            model=None,
            status=status,
            gate=_binary_gate(
                gate_id="readiness_packet",
                label="Live readiness packet",
                passed=False,
                blocking_reasons=reasons,
                source="2026-05-19 blueprint supplement Part C2",
            ),
        )

    evidence_refs = _unique(readiness.required_evidence.to_dict().values())
    blocking_reasons = _unique(readiness.result.blocking_reasons)
    passed = readiness.result.can_bind_live
    status = ReadinessStatus(
        readiness_id=readiness.readiness_id,
        binding_id=readiness.binding_id,
        persona_id=readiness.persona_id,
        capital_pool_id=readiness.capital_pool_id,
        can_bind_live=passed,
        approval=readiness.approval.to_dict(),
        ttl_hours=readiness.controls.ttl_hours,
        blocking_reasons=blocking_reasons,
        evidence_refs=evidence_refs,
    )
    return _ReadinessEvaluation(
        model=readiness,
        status=status,
        gate=_binary_gate(
            gate_id="readiness_packet",
            label="Live readiness packet",
            passed=passed,
            blocking_reasons=blocking_reasons,
            evidence_refs=evidence_refs,
            source="2026-05-19 blueprint supplement Part C2",
        ),
    )


def _sponsor_gate(
    readiness: CapitalBindingLiveReadiness | None,
    packet: Mapping[str, Any] | SponsorPersonaResponsibility,
) -> _SponsorEvaluation:
    if readiness is None:
        reasons = ("readiness_packet_invalid",)
        status = SponsorMandateStatus(
            responsibility_id=None,
            sponsor_persona_id=None,
            live_owner=None,
            status=BLOCKED,
            escalation_levels=0,
            passed=False,
            blocking_reasons=reasons,
        )
        return _SponsorEvaluation(
            status=status,
            gate=_binary_gate(
                gate_id="sponsor_mandate",
                label="Sponsor mandate",
                passed=False,
                blocking_reasons=reasons,
                source="2026-05-19 blueprint supplement CBL-002",
            ),
        )

    try:
        responsibility = validate_sponsor_responsibility(packet)
    except (SponsorPersonaResponsibilityError, TypeError) as exc:
        reasons = (f"sponsor_responsibility_invalid: {exc}",)
        status = SponsorMandateStatus(
            responsibility_id=None,
            sponsor_persona_id=None,
            live_owner=None,
            status=BLOCKED,
            escalation_levels=0,
            passed=False,
            blocking_reasons=reasons,
            evidence_refs=(readiness.required_evidence.sponsor_responsibility_ref,),
        )
        return _SponsorEvaluation(
            status=status,
            gate=_binary_gate(
                gate_id="sponsor_mandate",
                label="Sponsor mandate",
                passed=False,
                blocking_reasons=reasons,
                evidence_refs=status.evidence_refs,
                source="2026-05-19 blueprint supplement CBL-002",
            ),
        )

    blockers = []
    if responsibility.status != ACTIVE_RESPONSIBILITY_STATUS:
        blockers.append(f"sponsor_responsibility_not_active:{responsibility.status}")
    if responsibility.binding_id != readiness.binding_id:
        blockers.append("sponsor_responsibility_binding_mismatch")
    if responsibility.sponsor_persona_id != readiness.roles.sponsor_persona:
        blockers.append("sponsor_persona_mismatch")
    if responsibility.live_owner.owner_id != readiness.roles.live_owner:
        blockers.append("live_owner_mismatch")
    if responsibility.capital_pool_id and responsibility.capital_pool_id != readiness.capital_pool_id:
        blockers.append("sponsor_responsibility_capital_pool_mismatch")

    blocking_reasons = _unique(blockers)
    evidence_refs = _unique(
        (
            readiness.required_evidence.sponsor_responsibility_ref,
            responsibility.live_owner.mandate_ref,
            responsibility.live_owner.contact_ref,
            *responsibility.policy_refs,
            *(step.evidence_ref for step in responsibility.escalation_chain),
        )
    )
    passed = not blocking_reasons
    status = SponsorMandateStatus(
        responsibility_id=responsibility.responsibility_id,
        sponsor_persona_id=responsibility.sponsor_persona_id,
        live_owner=responsibility.live_owner.owner_id,
        status=responsibility.status,
        escalation_levels=len(responsibility.escalation_chain),
        passed=passed,
        blocking_reasons=blocking_reasons,
        evidence_refs=evidence_refs,
    )
    return _SponsorEvaluation(
        status=status,
        gate=_binary_gate(
            gate_id="sponsor_mandate",
            label="Sponsor mandate",
            passed=passed,
            blocking_reasons=blocking_reasons,
            evidence_refs=evidence_refs,
            source="2026-05-19 blueprint supplement CBL-002",
        ),
    )


def _conflict_gate(
    readiness: CapitalBindingLiveReadiness | None,
    packet: Mapping[str, Any] | ConflictResolutionLog,
) -> _ConflictEvaluation:
    evidence_refs = (
        (readiness.required_evidence.conflict_resolution_log_ref,)
        if readiness is not None
        else ()
    )
    if readiness is None:
        reasons = ("readiness_packet_invalid",)
        status = ConflictLogStatus(
            conflict_resolution_log_id=None,
            capital_pool_id=None,
            passed=False,
            blocking_reasons=reasons,
            evidence_refs=evidence_refs,
        )
        return _ConflictEvaluation(
            status=status,
            gate=_binary_gate(
                gate_id="conflict_log",
                label="Conflict log",
                passed=False,
                blocking_reasons=reasons,
                evidence_refs=evidence_refs,
                source="2026-05-19 blueprint supplement Part C3",
            ),
        )

    try:
        log = validate_conflict_resolution_log(packet)
        gate_result = evaluate_conflict_resolution_gate(log)
    except (ConflictResolutionLogError, TypeError) as exc:
        reasons = (f"conflict_resolution_log_invalid: {exc}",)
        status = ConflictLogStatus(
            conflict_resolution_log_id=None,
            capital_pool_id=None,
            passed=False,
            blocking_reasons=reasons,
            evidence_refs=evidence_refs,
        )
        return _ConflictEvaluation(
            status=status,
            gate=_binary_gate(
                gate_id="conflict_log",
                label="Conflict log",
                passed=False,
                blocking_reasons=reasons,
                evidence_refs=evidence_refs,
                source="2026-05-19 blueprint supplement Part C3",
            ),
        )

    blockers = list(gate_result.blocking_reasons)
    if readiness.capital_pool_id != log.capital_pool_id:
        blockers.append(CAPITAL_POOL_MISMATCH_BLOCKER)
    blocking_reasons = _unique(blockers)
    passed = not blocking_reasons
    status = ConflictLogStatus(
        conflict_resolution_log_id=log.log_id,
        capital_pool_id=log.capital_pool_id,
        passed=passed,
        open_conflict_ids=gate_result.open_conflict_ids,
        blocking_reasons=blocking_reasons,
        evidence_refs=evidence_refs,
    )
    return _ConflictEvaluation(
        status=status,
        gate=_binary_gate(
            gate_id="conflict_log",
            label="Conflict log",
            passed=passed,
            blocking_reasons=blocking_reasons,
            evidence_refs=evidence_refs,
            source="2026-05-19 blueprint supplement Part C3",
        ),
    )


def _ttl_gate(
    readiness: CapitalBindingLiveReadiness | None,
    packet: Mapping[str, Any] | BindingLifecycleState | None,
    *,
    at: datetime | str | None,
) -> _TtlEvaluation:
    if readiness is None:
        reasons = ("readiness_packet_invalid",)
        status = TtlStatus(
            binding_id=None,
            status=BLOCKED,
            admissible=False,
            ttl_hours=None,
            evaluated_at=None,
            expires_at=None,
            blocking_reasons=reasons,
        )
        return _TtlEvaluation(
            status=status,
            gate=_binary_gate(
                gate_id="ttl",
                label="TTL",
                passed=False,
                blocking_reasons=reasons,
                source="2026-05-19 blueprint supplement CBL-004",
            ),
        )
    if packet is None:
        reasons = ("binding_lifecycle_missing",)
        status = TtlStatus(
            binding_id=readiness.binding_id,
            status=BLOCKED,
            admissible=False,
            ttl_hours=readiness.controls.ttl_hours,
            evaluated_at=None,
            expires_at=None,
            blocking_reasons=reasons,
        )
        return _TtlEvaluation(
            status=status,
            gate=_binary_gate(
                gate_id="ttl",
                label="TTL",
                passed=False,
                blocking_reasons=reasons,
                source="2026-05-19 blueprint supplement CBL-004",
            ),
        )

    try:
        lifecycle = packet if isinstance(packet, BindingLifecycleState) else BindingLifecycleState.from_dict(packet)
        evaluation = evaluate_binding_lifecycle(lifecycle, at=at)
    except (BindingLifecycleError, TypeError) as exc:
        reasons = (f"binding_lifecycle_invalid: {exc}",)
        status = TtlStatus(
            binding_id=readiness.binding_id,
            status=BLOCKED,
            admissible=False,
            ttl_hours=readiness.controls.ttl_hours,
            evaluated_at=None,
            expires_at=None,
            blocking_reasons=reasons,
        )
        return _TtlEvaluation(
            status=status,
            gate=_binary_gate(
                gate_id="ttl",
                label="TTL",
                passed=False,
                blocking_reasons=reasons,
                source="2026-05-19 blueprint supplement CBL-004",
            ),
        )

    blockers = list(evaluation.blocking_reasons)
    if lifecycle.binding_id != readiness.binding_id:
        blockers.append("binding_lifecycle_binding_mismatch")
    if lifecycle.ttl.ttl_hours != readiness.controls.ttl_hours:
        blockers.append("binding_ttl_hours_mismatch")
    blocking_reasons = _unique(blockers)
    passed = evaluation.admissible and not blocking_reasons
    status = TtlStatus(
        binding_id=lifecycle.binding_id,
        status=evaluation.status,
        admissible=evaluation.admissible,
        ttl_hours=lifecycle.ttl.ttl_hours,
        evaluated_at=evaluation.evaluated_at,
        expires_at=evaluation.expires_at,
        blocking_reasons=blocking_reasons,
    )
    return _TtlEvaluation(
        status=status,
        gate=_binary_gate(
            gate_id="ttl",
            label="TTL",
            passed=passed,
            blocking_reasons=blocking_reasons,
            source="2026-05-19 blueprint supplement CBL-004",
        ),
    )


def _binary_gate(
    *,
    gate_id: str,
    label: str,
    passed: bool,
    blocking_reasons: Sequence[str],
    evidence_refs: Sequence[str] = (),
    source: str | None,
) -> DashboardGate:
    return DashboardGate(
        id=gate_id,
        label=label,
        status=READY if passed else BLOCKED,
        passed=passed,
        ready_items=1 if passed else 0,
        blocked_items=0 if passed else 1,
        total_items=1,
        progress_percent=100 if passed else 0,
        blocking_reasons=_unique(blocking_reasons),
        evidence_refs=_unique(evidence_refs),
        source=source,
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


def _unique(items: Iterable[Any]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return tuple(ordered)


def _compact(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


__all__ = [
    "BLOCKED",
    "DASHBOARD_SOURCE",
    "DASHBOARD_VERSION",
    "GO",
    "NO_GO",
    "READY",
    "CapitalBindingGoNoGoDashboard",
    "ConflictLogStatus",
    "DashboardGate",
    "DashboardProgress",
    "ReadinessStatus",
    "SponsorMandateStatus",
    "TtlStatus",
    "build_capital_binding_go_no_go_dashboard",
]
