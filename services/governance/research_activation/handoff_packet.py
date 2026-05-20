"""Research activation handoff packet read model.

This module builds the read-only Management Console payload for research
activation. It composes adapter tier claims, ProductionDataProof evidence, and
candidate admission gate status without mutating registry, deployment, broker,
runtime, or capital state.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .admission_gate import AdmissionGateIssue, evaluate_candidate_admission
from .production_data_proof import (
    ACTIVATION_TIERS,
    SCHEMA_VERSION as PRODUCTION_DATA_PROOF_SCHEMA_VERSION,
    ProductionDataProof,
    ValidationIssue,
)


SCHEMA_VERSION = "ResearchActivationHandoffPacket.v1"
READ_MODEL_KIND = "research_activation_dashboard"
READY = "ready"
BLOCKED = "blocked"
MISSING = "missing"
PASSED = "passed"
FAILED = "failed"
PRESENT = "present"

REQUIRED_EVIDENCE_KEYS = (
    "production_data_proof",
    "provider",
    "entitlement",
    "freshness",
    "point_in_time",
    "durable_storage",
    "audit",
    "no_order_route",
    "adapter_evidence",
    "admission_packet",
)


@dataclass(frozen=True)
class EvidencePresence:
    key: str
    present: bool
    status: str
    ref: str | None = None
    source: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "present": self.present,
            "status": self.status,
            "ref": self.ref,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AdmissionGateStatus:
    status: str
    passed: bool
    source: str
    errors: tuple[AdmissionGateIssue, ...] = ()
    warnings: tuple[AdmissionGateIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "source": self.source,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True)
class AdapterActivationRow:
    adapter_id: str
    adapter_kind: str
    activation_tier: str
    activation_tier_rank: int
    evidence: tuple[EvidencePresence, ...]
    admission_gate: AdmissionGateStatus
    candidate_review_ready: bool
    blocking_reasons: tuple[str, ...] = ()
    warning_reasons: tuple[str, ...] = ()
    proof_id: str | None = None
    target_id: str | None = None
    artifact_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_kind": self.adapter_kind,
            "activation_tier": self.activation_tier,
            "activation_tier_rank": self.activation_tier_rank,
            "proof_id": self.proof_id,
            "target_id": self.target_id,
            "artifact_type": self.artifact_type,
            "candidate_review_ready": self.candidate_review_ready,
            "admission_gate": self.admission_gate.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "blocking_reasons": list(self.blocking_reasons),
            "warning_reasons": list(self.warning_reasons),
        }


@dataclass(frozen=True)
class ResearchActivationSummary:
    total_adapters: int
    ready_adapters: int
    blocked_adapters: int
    missing_gate_adapters: int
    tier_counts: Mapping[str, int]
    evidence_present_counts: Mapping[str, int]
    evidence_missing_counts: Mapping[str, int]
    readiness_state: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_adapters": self.total_adapters,
            "ready_adapters": self.ready_adapters,
            "blocked_adapters": self.blocked_adapters,
            "missing_gate_adapters": self.missing_gate_adapters,
            "tier_counts": dict(self.tier_counts),
            "evidence_present_counts": dict(self.evidence_present_counts),
            "evidence_missing_counts": dict(self.evidence_missing_counts),
            "readiness_state": self.readiness_state,
        }


@dataclass(frozen=True)
class ResearchActivationHandoffPacket:
    adapters: tuple[AdapterActivationRow, ...]
    summary: ResearchActivationSummary
    schema_version: str = SCHEMA_VERSION
    read_model_kind: str = READ_MODEL_KIND
    as_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "read_model_kind": self.read_model_kind,
            "as_of": self.as_of,
            "summary": self.summary.to_dict(),
            "adapters": [row.to_dict() for row in self.adapters],
        }


def build_research_activation_handoff_packet(
    adapter_records: Sequence[Mapping[str, Any]],
    *,
    as_of: str | None = None,
) -> ResearchActivationHandoffPacket:
    """Build the read-only research activation dashboard packet."""

    rows = tuple(
        sorted(
            (_build_adapter_row(record) for record in adapter_records),
            key=lambda row: (row.adapter_kind, row.adapter_id),
        )
    )
    return ResearchActivationHandoffPacket(
        adapters=rows,
        summary=_summary(rows),
        as_of=as_of,
    )


def _build_adapter_row(record: Mapping[str, Any]) -> AdapterActivationRow:
    proof_payload = _production_data_proof_payload(record)
    proof = ProductionDataProof.from_mapping(proof_payload) if proof_payload else None
    proof_result = proof.validate() if proof is not None else None

    admission_packet = _admission_packet(record)
    admission_gate = _admission_gate_status(admission_packet)

    adapter_id = (
        _optional_text(record.get("adapter_id"))
        or (proof.adapter_id if proof is not None else None)
        or _optional_text(record.get("id"))
        or _optional_text(record.get("name"))
        or "unknown-adapter"
    )
    adapter_kind = (
        _normalized_text(record.get("adapter_kind") or record.get("kind"))
        or (proof.adapter_kind if proof is not None else "")
        or _adapter_kind_from_id(adapter_id)
    )
    activation_tier, tier_issue = _activation_tier(record, proof)
    evidence = _evidence_presence(proof_payload, proof, proof_result, admission_gate)

    errors = tuple(proof_result.errors if proof_result is not None else ())
    warnings = tuple(proof_result.warnings if proof_result is not None else ())
    blocking_reasons = _unique(
        (
            [tier_issue] if tier_issue else []
        )
        + ([issue.code for issue in errors] if proof_payload else ["missing_production_data_proof"])
        + [f"admission_gate:{issue.code}" for issue in admission_gate.errors]
        + ([] if admission_gate.status != MISSING else ["missing_admission_packet"])
    )
    warning_reasons = _unique([issue.code for issue in warnings] + [f"admission_gate:{issue.code}" for issue in admission_gate.warnings])

    proof_valid = proof_result.passed if proof_result is not None else False
    target_id = _target_id(record, admission_packet)
    artifact_type = _artifact_type(record, admission_packet)

    return AdapterActivationRow(
        adapter_id=adapter_id,
        adapter_kind=adapter_kind or "unknown",
        activation_tier=activation_tier,
        activation_tier_rank=ACTIVATION_TIERS.index(activation_tier),
        proof_id=proof.proof_id if proof is not None else None,
        target_id=target_id,
        artifact_type=artifact_type,
        evidence=evidence,
        admission_gate=admission_gate,
        candidate_review_ready=proof_valid and admission_gate.passed,
        blocking_reasons=blocking_reasons,
        warning_reasons=warning_reasons,
    )


def _summary(rows: Sequence[AdapterActivationRow]) -> ResearchActivationSummary:
    tier_counts = {tier: 0 for tier in ACTIVATION_TIERS}
    present_counts = {key: 0 for key in REQUIRED_EVIDENCE_KEYS}
    missing_counts = {key: 0 for key in REQUIRED_EVIDENCE_KEYS}

    for row in rows:
        tier_counts[row.activation_tier] += 1
        for item in row.evidence:
            if item.present:
                present_counts[item.key] += 1
            else:
                missing_counts[item.key] += 1

    ready_adapters = sum(1 for row in rows if row.candidate_review_ready)
    missing_gate_adapters = sum(1 for row in rows if row.admission_gate.status == MISSING)
    return ResearchActivationSummary(
        total_adapters=len(rows),
        ready_adapters=ready_adapters,
        blocked_adapters=len(rows) - ready_adapters,
        missing_gate_adapters=missing_gate_adapters,
        tier_counts=tier_counts,
        evidence_present_counts=present_counts,
        evidence_missing_counts=missing_counts,
        readiness_state=READY if rows and ready_adapters == len(rows) else BLOCKED,
    )


def _evidence_presence(
    proof_payload: Mapping[str, Any],
    proof: ProductionDataProof | None,
    proof_result: Any,
    admission_gate: AdmissionGateStatus,
) -> tuple[EvidencePresence, ...]:
    proof_errors = tuple(proof_result.errors if proof_result is not None else ())
    return (
        _presence(
            "production_data_proof",
            bool(proof_payload),
            _status_from_issues(bool(proof_payload), proof_errors),
            ref=proof.proof_id if proof is not None else None,
            source=PRODUCTION_DATA_PROOF_SCHEMA_VERSION if proof is not None else None,
        ),
        _evidence_from_payload("provider", proof_payload, ("provider",), proof_errors),
        _evidence_from_payload("entitlement", proof_payload, ("entitlement",), proof_errors),
        _evidence_from_payload("freshness", proof_payload, ("freshness",), proof_errors),
        _evidence_from_payload("point_in_time", proof_payload, ("point_in_time", "pit"), proof_errors),
        _evidence_from_payload("durable_storage", proof_payload, ("storage",), proof_errors),
        _evidence_from_payload("audit", proof_payload, ("audit",), proof_errors),
        _evidence_from_payload("no_order_route", proof_payload, ("no_order_route", "controls"), proof_errors),
        _evidence_from_payload("adapter_evidence", proof_payload, ("adapter_evidence", "adapter_evidence_instances"), proof_errors),
        _presence(
            "admission_packet",
            admission_gate.status != MISSING,
            admission_gate.status,
            source=admission_gate.source,
        ),
    )


def _admission_gate_status(packet: Mapping[str, Any]) -> AdmissionGateStatus:
    if not packet:
        return AdmissionGateStatus(status=MISSING, passed=False, source="missing")
    result = evaluate_candidate_admission(packet)
    return AdmissionGateStatus(
        status=PASSED if result.passed else BLOCKED,
        passed=result.passed,
        source="evaluated_candidate_admission_packet",
        errors=result.errors,
        warnings=result.warnings,
    )


def _production_data_proof_payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    proof = _mapping(record.get("production_data_proof"))
    if proof:
        return proof
    if _text(record.get("schema_version")) == PRODUCTION_DATA_PROOF_SCHEMA_VERSION:
        return record
    candidate = _mapping(record.get("candidate_artifact"))
    metadata = _mapping(candidate.get("metadata"))
    return _mapping(metadata.get("production_data_proof"))


def _admission_packet(record: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("admission_packet", "candidate_admission_packet", "registry_admission_packet"):
        packet = _mapping(record.get(key))
        if packet:
            return packet
    if record.get("target_type") or record.get("registry_request") or record.get("candidate_artifact"):
        return record
    return {}


def _activation_tier(
    record: Mapping[str, Any],
    proof: ProductionDataProof | None,
) -> tuple[str, str | None]:
    raw_tier = _text(record.get("activation_tier") or record.get("tier"))
    if not raw_tier and proof is not None:
        raw_tier = proof.activation_tier
    if not raw_tier:
        return "R0", None
    tier = raw_tier.upper()
    if tier in ACTIVATION_TIERS:
        return tier, None
    return "R0", f"invalid_activation_tier:{raw_tier}"


def _target_id(record: Mapping[str, Any], admission_packet: Mapping[str, Any]) -> str | None:
    registry_request = _mapping(admission_packet.get("registry_request"))
    candidate = _mapping(admission_packet.get("candidate_artifact"))
    return (
        _optional_text(record.get("target_id"))
        or _optional_text(admission_packet.get("target_id"))
        or _optional_text(registry_request.get("registry_id") or registry_request.get("target_id"))
        or _optional_text(candidate.get("registry_id") or candidate.get("target_id"))
    )


def _artifact_type(record: Mapping[str, Any], admission_packet: Mapping[str, Any]) -> str | None:
    registry_request = _mapping(admission_packet.get("registry_request"))
    candidate = _mapping(admission_packet.get("candidate_artifact"))
    return _normalized_text(
        record.get("artifact_type")
        or candidate.get("artifact_type")
        or registry_request.get("artifact_type")
    ) or None


def _evidence_from_payload(
    key: str,
    payload: Mapping[str, Any],
    payload_keys: Sequence[str],
    issues: Sequence[ValidationIssue],
) -> EvidencePresence:
    present = any(bool(_mapping(payload.get(payload_key)) or _sequence(payload.get(payload_key))) for payload_key in payload_keys)
    return _presence(
        key,
        present,
        _status_from_issues(present, _issues_for_key(key, issues)),
    )


def _status_from_issues(present: bool, issues: Sequence[ValidationIssue]) -> str:
    if not present:
        return MISSING
    if issues:
        return FAILED
    return PRESENT


def _issues_for_key(key: str, issues: Sequence[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    prefixes = {
        "durable_storage": ("storage",),
        "point_in_time": ("point_in_time",),
    }.get(key, (key,))
    return tuple(
        issue
        for issue in issues
        if any(issue.path == prefix or issue.path.startswith(f"{prefix}.") or issue.path.startswith(f"{prefix}[") for prefix in prefixes)
    )


def _presence(
    key: str,
    present: bool,
    status: str,
    *,
    ref: str | None = None,
    source: str | None = None,
    detail: str | None = None,
) -> EvidencePresence:
    return EvidencePresence(
        key=key,
        present=present,
        status=status,
        ref=ref,
        source=source,
        detail=detail,
    )


def _adapter_kind_from_id(adapter_id: str) -> str:
    prefix = adapter_id.split("-", 1)[0].split(":", 1)[0]
    return _normalized_text(prefix) or "unknown"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _text(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return value.strip() if isinstance(value, str) else ""


def _normalized_text(value: Any) -> str:
    text = _text(value).lower()
    compact = text.replace("-", "").replace("_", "").replace(" ", "")
    if compact in {"w&b", "wandb", "weights&biases", "weightsandbiases"}:
        return "wandb"
    return text.replace("-", "_").replace(" ", "_")


def _unique(items: Sequence[str]) -> tuple[str, ...]:
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
    "FAILED",
    "MISSING",
    "PASSED",
    "PRESENT",
    "READ_MODEL_KIND",
    "READY",
    "REQUIRED_EVIDENCE_KEYS",
    "SCHEMA_VERSION",
    "AdapterActivationRow",
    "AdmissionGateStatus",
    "EvidencePresence",
    "ResearchActivationHandoffPacket",
    "ResearchActivationSummary",
    "build_research_activation_handoff_packet",
]
