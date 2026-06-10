"""Conflict-resolution log for multi-persona sponsor governance.

This model is the Pantheon governance output of sponsor resolution. It accepts
the MGMT-SYN conflict-log shape and adds classifier-visible conflicts plus
``open_conflicts`` for live-binding gates. It does not synthesize allocations.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "MultiPersonaConflictResolutionLog.v1"
OPEN_CONFLICT_OWNER = "governance.multi_persona.sponsor_resolver"


class ConflictResolutionLogError(ValueError):
    """Raised when a multi-persona conflict-resolution log is invalid."""


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
class ClassifiedConflict:
    conflict_id: str
    conflict_type: str
    severity: str
    proposal_ids: tuple[str, ...]
    summary: str
    target_ref: str | None = None
    committee_trigger: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClassifiedConflict":
        payload = _mapping(data, "classified_conflicts[]")
        return cls(
            conflict_id=_required_text(payload.get("conflict_id"), "classified_conflicts[].conflict_id"),
            conflict_type=_required_text(payload.get("conflict_type"), "classified_conflicts[].conflict_type"),
            severity=_required_text(payload.get("severity"), "classified_conflicts[].severity"),
            proposal_ids=tuple(
                _required_text(item, "classified_conflicts[].proposal_ids[]")
                for item in _sequence(payload.get("proposal_ids"), "classified_conflicts[].proposal_ids")
            ),
            summary=_required_text(payload.get("summary"), "classified_conflicts[].summary"),
            target_ref=_optional_text(payload.get("target_ref")),
            committee_trigger=_optional_text(payload.get("committee_trigger")),
            details=_mapping(payload.get("details", {}), "classified_conflicts[].details"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "conflict_id": self.conflict_id,
                "conflict_type": self.conflict_type,
                "severity": self.severity,
                "proposal_ids": list(self.proposal_ids),
                "summary": self.summary,
                "target_ref": self.target_ref,
                "committee_trigger": self.committee_trigger,
                "details": dict(self.details),
            }
        )


@dataclass(frozen=True)
class OpenConflict:
    conflict_id: str
    summary: str
    owner: str = OPEN_CONFLICT_OWNER
    evidence_ref: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OpenConflict":
        payload = _mapping(data, "open_conflicts[]")
        return cls(
            conflict_id=_required_text(payload.get("conflict_id"), "open_conflicts[].conflict_id"),
            summary=_required_text(payload.get("summary"), "open_conflicts[].summary"),
            owner=_required_text(payload.get("owner", OPEN_CONFLICT_OWNER), "open_conflicts[].owner"),
            evidence_ref=_optional_text(payload.get("evidence_ref")),
        )

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
    sponsor_persona_id: str
    artifact_id: str
    synthesis_method: str
    vetoed_proposals: tuple[ConflictVetoRecord, ...] = ()
    weighting_inputs: dict[str, float] = field(default_factory=dict)
    weighting_outputs: dict[str, float] = field(default_factory=dict)
    classified_conflicts: tuple[ClassifiedConflict, ...] = ()
    committee_ref: str | None = None
    open_conflicts: tuple[OpenConflict, ...] = ()
    rejected_reason: str | None = None
    source_conflict_resolution_log_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConflictResolutionLog":
        payload = _mapping(data, "ConflictResolutionLog")
        return cls(
            log_id=_required_text(payload.get("log_id"), "log_id"),
            capital_pool_id=_required_text(payload.get("capital_pool_id"), "capital_pool_id"),
            scope_ref=_required_text(payload.get("scope_ref"), "scope_ref"),
            timestamp=_required_text(payload.get("timestamp"), "timestamp"),
            proposal_ids=tuple(
                _required_text(item, "proposal_ids[]")
                for item in _sequence(payload.get("proposal_ids"), "proposal_ids")
            ),
            sponsor_persona_id=_required_text(payload.get("sponsor_persona_id"), "sponsor_persona_id"),
            artifact_id=_required_text(payload.get("artifact_id"), "artifact_id"),
            synthesis_method=_required_text(payload.get("synthesis_method"), "synthesis_method"),
            vetoed_proposals=tuple(
                ConflictVetoRecord.from_dict(item)
                for item in _sequence(payload.get("vetoed_proposals", []), "vetoed_proposals")
            ),
            weighting_inputs=_number_mapping(payload.get("weighting_inputs", {}), "weighting_inputs"),
            weighting_outputs=_number_mapping(payload.get("weighting_outputs", {}), "weighting_outputs"),
            classified_conflicts=tuple(
                ClassifiedConflict.from_dict(item)
                for item in _sequence(payload.get("classified_conflicts", []), "classified_conflicts")
            ),
            committee_ref=_optional_text(payload.get("committee_ref")),
            open_conflicts=tuple(
                OpenConflict.from_dict(item)
                for item in _sequence(payload.get("open_conflicts", []), "open_conflicts")
            ),
            rejected_reason=_optional_text(payload.get("rejected_reason")),
            source_conflict_resolution_log_id=_optional_text(payload.get("source_conflict_resolution_log_id")),
            schema_version=_optional_text(payload.get("schema_version")) or SCHEMA_VERSION,
        )

    @property
    def open_conflict_ids(self) -> tuple[str, ...]:
        return tuple(conflict.conflict_id for conflict in self.open_conflicts)

    @property
    def has_open_conflicts(self) -> bool:
        return bool(self.open_conflicts)

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "log_id": self.log_id,
                "capital_pool_id": self.capital_pool_id,
                "scope_ref": self.scope_ref,
                "timestamp": self.timestamp,
                "proposal_ids": list(self.proposal_ids),
                "sponsor_persona_id": self.sponsor_persona_id,
                "artifact_id": self.artifact_id,
                "synthesis_method": self.synthesis_method,
                "vetoed_proposals": [record.to_dict() for record in self.vetoed_proposals],
                "weighting_inputs": dict(self.weighting_inputs),
                "weighting_outputs": dict(self.weighting_outputs),
                "classified_conflicts": [conflict.to_dict() for conflict in self.classified_conflicts],
                "committee_ref": self.committee_ref,
                "open_conflicts": [conflict.to_dict() for conflict in self.open_conflicts],
                "rejected_reason": self.rejected_reason,
                "source_conflict_resolution_log_id": self.source_conflict_resolution_log_id,
                "schema_version": self.schema_version,
            }
        )


def validate_conflict_resolution_log(
    packet: Mapping[str, Any] | ConflictResolutionLog,
) -> ConflictResolutionLog:
    """Validate a sponsor-resolution conflict log and return its model."""

    if isinstance(packet, ConflictResolutionLog):
        return packet
    return ConflictResolutionLog.from_dict(packet)
