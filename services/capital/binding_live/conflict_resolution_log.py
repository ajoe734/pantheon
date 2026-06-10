"""ConflictResolutionLog schema model and capital-binding live gate.

This module is schema and consistency validation only. It does not enable
capital live writes, mutate runtime bindings, or call broker/order paths.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .readiness_model import CapitalBindingLiveReadiness, CapitalBindingResult, validate_readiness


SCHEMA_VERSION = "ConflictResolutionLog.v1"
PART_C3_PACKET_VERSION = "2026-05-19.C3"
OPEN_CONFLICT_BLOCKER = "conflict_resolution_log_has_open_conflicts"
CAPITAL_POOL_MISMATCH_BLOCKER = "conflict_resolution_log_capital_pool_mismatch"

REQUIRED_TOP_LEVEL_FIELDS = (
    "log_id",
    "capital_pool_id",
    "scope_ref",
    "timestamp",
    "proposal_ids",
    "vetoed_proposals",
    "weighting_inputs",
    "weighting_outputs",
    "open_conflicts",
)


class ConflictResolutionLogError(ValueError):
    """Raised when a ConflictResolutionLog packet or gate is invalid."""


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConflictResolutionLogError(f"{field_name} must be an object")
    return copy.deepcopy(dict(value))


def _sequence(value: Any, field_name: str) -> list[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ConflictResolutionLogError(f"{field_name} must be a list")
    return list(value)


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise ConflictResolutionLogError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise ConflictResolutionLogError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_tuple(value: Any, field_name: str, *, allow_empty: bool) -> tuple[str, ...]:
    items = tuple(_required_text(item, f"{field_name}[]") for item in _sequence(value, field_name))
    if not allow_empty and not items:
        raise ConflictResolutionLogError(f"{field_name} must contain at least one item")
    return items


def _number_mapping(value: Any, field_name: str) -> dict[str, float]:
    payload = _mapping(value, field_name)
    parsed: dict[str, float] = {}
    for raw_key, raw_value in payload.items():
        key = _required_text(raw_key, f"{field_name}.key")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ConflictResolutionLogError(f"{field_name}.{key} must be a number")
        number = float(raw_value)
        if not math.isfinite(number) or number < 0:
            raise ConflictResolutionLogError(f"{field_name}.{key} must be a finite non-negative number")
        parsed[key] = number
    return parsed


def _compact(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


def _dedupe(items: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return tuple(ordered)


@dataclass(frozen=True)
class ConflictVetoRecord:
    proposal_id: str
    persona_id: str
    reason: str
    detail: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConflictVetoRecord":
        payload = _mapping(data, "vetoed_proposals[]")
        return cls(
            proposal_id=_required_text(payload.get("proposal_id"), "vetoed_proposals[].proposal_id"),
            persona_id=_required_text(payload.get("persona_id"), "vetoed_proposals[].persona_id"),
            reason=_required_text(payload.get("reason"), "vetoed_proposals[].reason"),
            detail=_optional_text(payload.get("detail")),
        )

    def to_dict(self) -> dict[str, str]:
        return _compact(
            {
                "proposal_id": self.proposal_id,
                "persona_id": self.persona_id,
                "reason": self.reason,
                "detail": self.detail,
            }
        )


@dataclass(frozen=True)
class OpenConflict:
    conflict_id: str
    summary: str
    owner: str | None = None
    evidence_ref: str | None = None

    @classmethod
    def from_value(cls, data: Any) -> "OpenConflict":
        if isinstance(data, Mapping):
            payload = _mapping(data, "open_conflicts[]")
            return cls(
                conflict_id=_required_text(payload.get("conflict_id"), "open_conflicts[].conflict_id"),
                summary=_required_text(payload.get("summary"), "open_conflicts[].summary"),
                owner=_optional_text(payload.get("owner")),
                evidence_ref=_optional_text(payload.get("evidence_ref")),
            )
        text = _required_text(data, "open_conflicts[]")
        return cls(conflict_id=text, summary=text)

    def to_dict(self) -> dict[str, str]:
        return _compact(
            {
                "conflict_id": self.conflict_id,
                "summary": self.summary,
                "owner": self.owner,
                "evidence_ref": self.evidence_ref,
            }
        )


@dataclass(frozen=True)
class ConflictResolutionLog:
    log_id: str
    capital_pool_id: str
    scope_ref: str
    timestamp: str
    proposal_ids: tuple[str, ...]
    vetoed_proposals: tuple[ConflictVetoRecord, ...]
    weighting_inputs: dict[str, float]
    weighting_outputs: dict[str, float]
    open_conflicts: tuple[OpenConflict, ...]
    committee_ref: str | None = None
    sponsor_persona_id: str | None = None
    rejected_reason: str | None = None
    synthesis_method: str | None = None
    schema_version: str | None = SCHEMA_VERSION
    packet_version: str | None = PART_C3_PACKET_VERSION

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConflictResolutionLog":
        payload = _mapping(data, "ConflictResolutionLog")
        vetoed_proposals = tuple(
            ConflictVetoRecord.from_dict(item)
            for item in _sequence(payload.get("vetoed_proposals"), "vetoed_proposals")
        )
        open_conflicts = tuple(
            OpenConflict.from_value(item)
            for item in _sequence(payload.get("open_conflicts"), "open_conflicts")
        )
        return cls(
            log_id=_required_text(payload.get("log_id"), "log_id"),
            capital_pool_id=_required_text(payload.get("capital_pool_id"), "capital_pool_id"),
            scope_ref=_required_text(payload.get("scope_ref"), "scope_ref"),
            timestamp=_required_text(payload.get("timestamp"), "timestamp"),
            proposal_ids=_string_tuple(payload.get("proposal_ids"), "proposal_ids", allow_empty=False),
            vetoed_proposals=vetoed_proposals,
            weighting_inputs=_number_mapping(payload.get("weighting_inputs"), "weighting_inputs"),
            weighting_outputs=_number_mapping(payload.get("weighting_outputs"), "weighting_outputs"),
            open_conflicts=open_conflicts,
            committee_ref=_optional_text(payload.get("committee_ref")),
            sponsor_persona_id=_optional_text(payload.get("sponsor_persona_id")),
            rejected_reason=_optional_text(payload.get("rejected_reason")),
            synthesis_method=_optional_text(payload.get("synthesis_method")),
            schema_version=_optional_text(payload.get("schema_version")) or SCHEMA_VERSION,
            packet_version=_optional_text(payload.get("packet_version")) or PART_C3_PACKET_VERSION,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "ConflictResolutionLog":
        with Path(path).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, Mapping):
            raise ConflictResolutionLogError("conflict resolution document must be a JSON object")
        return cls.from_dict(data)

    @property
    def open_conflict_ids(self) -> tuple[str, ...]:
        return tuple(conflict.conflict_id for conflict in self.open_conflicts)

    def has_open_conflicts(self) -> bool:
        return bool(self.open_conflicts)

    def blocking_reasons(self) -> tuple[str, ...]:
        if self.has_open_conflicts():
            return (OPEN_CONFLICT_BLOCKER,)
        return ()

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "log_id": self.log_id,
                "capital_pool_id": self.capital_pool_id,
                "scope_ref": self.scope_ref,
                "timestamp": self.timestamp,
                "proposal_ids": list(self.proposal_ids),
                "vetoed_proposals": [record.to_dict() for record in self.vetoed_proposals],
                "weighting_inputs": dict(self.weighting_inputs),
                "weighting_outputs": dict(self.weighting_outputs),
                "open_conflicts": [conflict.to_dict() for conflict in self.open_conflicts],
                "committee_ref": self.committee_ref,
                "sponsor_persona_id": self.sponsor_persona_id,
                "rejected_reason": self.rejected_reason,
                "synthesis_method": self.synthesis_method,
                "schema_version": self.schema_version,
                "packet_version": self.packet_version,
            }
        )


@dataclass(frozen=True)
class ConflictResolutionGateResult:
    conflict_resolution_log_id: str
    can_bind_live: bool
    blocking_reasons: tuple[str, ...] = ()
    open_conflict_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_resolution_log_id": self.conflict_resolution_log_id,
            "can_bind_live": self.can_bind_live,
            "blocking_reasons": list(self.blocking_reasons),
            "open_conflict_ids": list(self.open_conflict_ids),
        }


def validate_conflict_resolution_log(
    packet: Mapping[str, Any] | ConflictResolutionLog,
) -> ConflictResolutionLog:
    """Validate a conflict-resolution packet and return the normalized model."""

    if isinstance(packet, ConflictResolutionLog):
        return packet
    return ConflictResolutionLog.from_dict(packet)


def evaluate_conflict_resolution_gate(
    conflict_log: Mapping[str, Any] | ConflictResolutionLog,
) -> ConflictResolutionGateResult:
    """Return the capital-binding live gate result for one conflict log."""

    log = validate_conflict_resolution_log(conflict_log)
    blockers = log.blocking_reasons()
    return ConflictResolutionGateResult(
        conflict_resolution_log_id=log.log_id,
        can_bind_live=not blockers,
        blocking_reasons=blockers,
        open_conflict_ids=log.open_conflict_ids,
    )


def _evaluate_readiness_bound_gate(
    readiness_model: CapitalBindingLiveReadiness,
    conflict_log: Mapping[str, Any] | ConflictResolutionLog,
) -> ConflictResolutionGateResult:
    log = validate_conflict_resolution_log(conflict_log)
    blockers = list(log.blocking_reasons())
    if readiness_model.capital_pool_id != log.capital_pool_id:
        blockers.append(CAPITAL_POOL_MISMATCH_BLOCKER)
    deduped_blockers = _dedupe(blockers)
    return ConflictResolutionGateResult(
        conflict_resolution_log_id=log.log_id,
        can_bind_live=not deduped_blockers,
        blocking_reasons=deduped_blockers,
        open_conflict_ids=log.open_conflict_ids,
    )


def apply_conflict_resolution_gate(
    readiness: Mapping[str, Any] | CapitalBindingLiveReadiness,
    conflict_log: Mapping[str, Any] | ConflictResolutionLog,
) -> CapitalBindingResult:
    """Combine a C2 readiness result with the C3 conflict-resolution gate."""

    readiness_model = validate_readiness(readiness)
    gate = _evaluate_readiness_bound_gate(readiness_model, conflict_log)
    blocking_reasons = _dedupe((*readiness_model.result.blocking_reasons, *gate.blocking_reasons))
    return CapitalBindingResult(
        can_bind_live=readiness_model.result.can_bind_live and gate.can_bind_live,
        blocking_reasons=blocking_reasons,
    )


def validate_conflict_resolution_gate(
    readiness: Mapping[str, Any] | CapitalBindingLiveReadiness,
    conflict_log: Mapping[str, Any] | ConflictResolutionLog,
) -> ConflictResolutionGateResult:
    """Validate that a live-binding readiness packet respects the conflict gate."""

    readiness_model = validate_readiness(readiness)
    gate = _evaluate_readiness_bound_gate(readiness_model, conflict_log)
    missing_blockers = sorted(set(gate.blocking_reasons) - set(readiness_model.result.blocking_reasons))

    if readiness_model.result.can_bind_live and gate.blocking_reasons:
        detail = ", ".join(gate.blocking_reasons)
        if OPEN_CONFLICT_BLOCKER in gate.blocking_reasons:
            detail = "open conflicts: " + ", ".join(gate.open_conflict_ids)
        raise ConflictResolutionLogError(
            "can_bind_live cannot be true while conflict_resolution_log gate is blocked: " + detail
        )
    if not readiness_model.result.can_bind_live and missing_blockers:
        raise ConflictResolutionLogError(
            "result.blocking_reasons must include conflict resolution blockers: "
            + ", ".join(missing_blockers)
        )
    return gate
