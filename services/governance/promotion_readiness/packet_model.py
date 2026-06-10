"""PromotionReadinessPacket model.

This module captures the 2026-05-19 supplement A2.1 packet shape used by
EP5 canary-readiness work. It intentionally keeps validation limited to packet
structure and fail-closed consistency; policy-specific blocking reason
derivation belongs to the validator layer.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "PromotionReadinessPacket.v2"
LEGACY_SCHEMA_VERSION = "PromotionReadinessPacket.v1"
A2_1_PACKET_VERSION = "2026-05-19.A2.1"

PASSING_STATUSES = {"pass", "passed", "present", "satisfied", "not_applicable"}
BLOCKING_STATUSES = {"fail", "failed", "missing", "pending", "expired", "revoked", "incomplete"}
APPROVAL_STATUSES = {
    "not_required",
    "pending",
    "approved",
    "approved_with_conditions",
    "rejected",
    "expired",
    "revoked",
    "recorded",
}
PASSING_APPROVAL_STATUSES = {"approved", "approved_with_conditions", "recorded", "not_required"}
REF_FIELDS = ("artifact_ref", "deployment_ref", "runtime_ref", "pool_ref", "policy_ref")
REF_ALIASES = {"capital_pool_ref": "pool_ref"}
UNSAFE_TRUE_FLAGS = {
    "BROKER_PRODUCTION_LIVE_ENABLED",
    "CAPITAL_BINDING_LIVE_ENABLED",
    "broker_production_live_enabled",
    "capital_binding_live_enabled",
    "live_capital_side_effects",
}


class PromotionReadinessPacketError(ValueError):
    """Raised when a PromotionReadinessPacket is structurally inconsistent."""


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise PromotionReadinessPacketError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise PromotionReadinessPacketError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PromotionReadinessPacketError(f"{field_name} must be an object")
    return copy.deepcopy(dict(value))


def _sequence(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PromotionReadinessPacketError(f"{field_name} must be a list")
    return list(value)


def _compact(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


def _normalize_status(value: Any, *, default: str = "pending") -> str:
    return str(value or default).strip().lower()


def _normalize_approval_status(value: Any) -> str | None:
    if value is None:
        return None
    status = _normalize_status(value)
    if status not in APPROVAL_STATUSES:
        raise PromotionReadinessPacketError(
            "approval.status must be one of: " + ", ".join(sorted(APPROVAL_STATUSES))
        )
    return status


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _string_tuple(values: Any, field_name: str) -> tuple[str, ...]:
    return tuple(str(item) for item in _sequence(values, field_name))


def _looks_like_a2_1(payload: Mapping[str, Any]) -> bool:
    flags = payload.get("flags")
    return (
        "packet_version" in payload
        or "target_environment" in payload
        or any(field in payload for field in (*REF_FIELDS, *REF_ALIASES))
        or isinstance(payload.get("required_evidence"), Mapping)
        or (isinstance(flags, Mapping) and "can_proceed" in flags and "target" not in payload)
    )


def _ref_identifier(field_name: str, value: Any) -> str | None:
    if isinstance(value, str):
        return _optional_text(value)
    if not isinstance(value, Mapping):
        return None

    specific_keys = {
        "artifact_ref": ("artifact_id", "registry_id", "candidate_artifact_id"),
        "deployment_ref": ("deployment_id", "deployment_plan_id", "plan_id"),
        "runtime_ref": ("runtime_id", "runtime_binding_id", "binding_id"),
        "pool_ref": ("pool_id", "capital_pool_id"),
        "policy_ref": ("policy_id", "policy_version_id"),
    }
    for key in (*specific_keys.get(field_name, ()), "id", "ref", "uri", "name"):
        text = _optional_text(value.get(key))
        if text:
            return text
    return None


def _extract_refs(payload: Mapping[str, Any]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for field_name in REF_FIELDS:
        if field_name in payload and payload[field_name] is not None:
            refs[field_name] = copy.deepcopy(payload[field_name])
    for alias, canonical in REF_ALIASES.items():
        if canonical not in refs and alias in payload and payload[alias] is not None:
            refs[canonical] = copy.deepcopy(payload[alias])
    return refs


def _target_from_a2_1(payload: Mapping[str, Any], refs: Mapping[str, Any]) -> TargetRef:
    target_environment = _required_text(
        payload.get("target_environment") or payload.get("environment"),
        "target_environment",
    )
    target_type = _optional_text(payload.get("target_type"))
    target_id = _optional_text(payload.get("target_id"))

    for field_name in ("deployment_ref", "runtime_ref", "artifact_ref", "pool_ref", "policy_ref"):
        if field_name in refs:
            if target_type is None:
                target_type = field_name.removesuffix("_ref")
            if target_id is None:
                target_id = _ref_identifier(field_name, refs[field_name])
    if target_id is None:
        target_id = _optional_text(payload.get("packet_id"))

    return TargetRef(
        target_type=target_type or "promotion",
        target_id=_required_text(target_id, "target_id"),
        environment=target_environment,
        target_version=_optional_text(payload.get("target_version")),
        deployment_stage=_optional_text(payload.get("deployment_stage")),
    )


def _evidence_item_from_required_entry(key: str, value: Any) -> EvidenceItem:
    if isinstance(value, Mapping):
        payload = copy.deepcopy(dict(value))
        payload.setdefault("key", key)
        payload.setdefault("status", "present")
        if "path" not in payload:
            evidence_ref = payload.get("evidence_ref")
            if isinstance(evidence_ref, str):
                payload["path"] = evidence_ref
        if "ref_type" not in payload and "type" in payload:
            payload["ref_type"] = payload["type"]
        return EvidenceItem.from_dict(payload)
    if isinstance(value, bool):
        return EvidenceItem(key=key, status="present" if value else "missing")
    if value is None:
        return EvidenceItem(key=key, status="missing")
    text = str(value).strip()
    if not text:
        return EvidenceItem(key=key, status="missing")
    return EvidenceItem(key=key, status="present", path=text)


@dataclass(frozen=True)
class TargetRef:
    """The object and environment this readiness packet governs."""

    target_type: str
    target_id: str
    environment: str
    target_version: str | None = None
    deployment_stage: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TargetRef":
        payload = _mapping(data, "target")
        known = {"target_type", "target_id", "environment", "target_version", "deployment_stage"}
        return cls(
            target_type=_required_text(payload.get("target_type"), "target.target_type"),
            target_id=_required_text(payload.get("target_id"), "target.target_id"),
            environment=_required_text(payload.get("environment"), "target.environment"),
            target_version=_optional_text(payload.get("target_version")),
            deployment_stage=_optional_text(payload.get("deployment_stage")),
            metadata={key: value for key, value in payload.items() if key not in known},
        )

    @classmethod
    def from_legacy(cls, payload: Mapping[str, Any]) -> "TargetRef":
        deployment_stage = None
        registry_request = payload.get("registry_request")
        if isinstance(registry_request, Mapping):
            deployment_stage = _optional_text(registry_request.get("deployment_stage"))
        return cls(
            target_type=_required_text(payload.get("target_type"), "target_type"),
            target_id=_required_text(payload.get("target_id"), "target_id"),
            environment=_required_text(payload.get("environment"), "environment"),
            target_version=_optional_text(payload.get("target_version")),
            deployment_stage=deployment_stage,
        )

    def to_dict(self) -> dict[str, Any]:
        data = _compact(
            {
                "target_type": self.target_type,
                "target_id": self.target_id,
                "environment": self.environment,
                "target_version": self.target_version,
                "deployment_stage": self.deployment_stage,
            }
        )
        data.update(copy.deepcopy(self.metadata))
        return data


@dataclass(frozen=True)
class EvidenceItem:
    """A concrete evidence reference consumed by the packet."""

    key: str
    status: str
    source_task_id: str | None = None
    path: str | None = None
    ref_type: str | None = None
    description: str | None = None
    evidence_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceItem":
        payload = _mapping(data, "evidence.provided[]")
        known = {"key", "status", "source_task_id", "path", "ref_type", "description", "evidence_hash"}
        return cls(
            key=_required_text(payload.get("key"), "evidence.provided[].key"),
            status=_normalize_status(payload.get("status"), default="present"),
            source_task_id=_optional_text(payload.get("source_task_id")),
            path=_optional_text(payload.get("path")),
            ref_type=_optional_text(payload.get("ref_type")),
            description=_optional_text(payload.get("description")),
            evidence_hash=_optional_text(payload.get("evidence_hash")),
            metadata={key: value for key, value in payload.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        data = _compact(
            {
                "key": self.key,
                "status": self.status,
                "source_task_id": self.source_task_id,
                "path": self.path,
                "ref_type": self.ref_type,
                "description": self.description,
                "evidence_hash": self.evidence_hash,
            }
        )
        data.update(copy.deepcopy(self.metadata))
        return data


@dataclass(frozen=True)
class GateResult:
    """A named readiness gate result tied to evidence or policy state."""

    gate: str
    status: str
    source_ref: str | None = None
    note: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GateResult":
        payload = _mapping(data, "evidence.gate_results[]")
        known = {"gate", "status", "source_ref", "note"}
        return cls(
            gate=_required_text(payload.get("gate"), "evidence.gate_results[].gate"),
            status=_normalize_status(payload.get("status"), default="pending"),
            source_ref=_optional_text(payload.get("source_ref")),
            note=_optional_text(payload.get("note")),
            metadata={key: value for key, value in payload.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        data = _compact(
            {
                "gate": self.gate,
                "status": self.status,
                "source_ref": self.source_ref,
                "note": self.note,
            }
        )
        data.update(copy.deepcopy(self.metadata))
        return data


@dataclass(frozen=True)
class EvidenceSubtree:
    """Evidence requirements, supplied references, gaps, and gate outcomes."""

    required: tuple[str, ...] = ()
    provided: tuple[EvidenceItem, ...] = ()
    missing: tuple[str, ...] = ()
    gate_results: tuple[GateResult, ...] = ()
    required_map: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceSubtree":
        payload = _mapping(data, "evidence")
        return cls(
            required=_string_tuple(payload.get("required"), "evidence.required"),
            provided=tuple(
                EvidenceItem.from_dict(item)
                for item in _sequence(payload.get("provided"), "evidence.provided")
            ),
            missing=_string_tuple(payload.get("missing"), "evidence.missing"),
            gate_results=tuple(
                GateResult.from_dict(item) for item in _sequence(payload.get("gate_results"), "evidence.gate_results")
            ),
        )

    @classmethod
    def from_legacy(cls, payload: Mapping[str, Any]) -> "EvidenceSubtree":
        return cls(
            required=_string_tuple(payload.get("required_evidence"), "required_evidence"),
            provided=tuple(
                EvidenceItem.from_dict(item)
                for item in _sequence(payload.get("provided_evidence"), "provided_evidence")
            ),
            missing=_string_tuple(payload.get("missing_evidence"), "missing_evidence"),
            gate_results=tuple(
                GateResult.from_dict(item) for item in _sequence(payload.get("gate_results"), "gate_results")
            ),
        )

    @classmethod
    def from_a2_1(cls, payload: Mapping[str, Any]) -> "EvidenceSubtree":
        required_map = _mapping(payload.get("required_evidence"), "required_evidence")
        provided = tuple(
            _evidence_item_from_required_entry(key, value)
            for key, value in required_map.items()
        )
        missing = tuple(
            item.key
            for item in provided
            if item.status in {"missing", "pending", "incomplete", "expired", "revoked"}
        )
        gate_results = tuple(
            GateResult.from_dict(item)
            for item in _sequence(payload.get("gate_results"), "gate_results")
        )
        return cls(
            required=tuple(str(key) for key in required_map),
            provided=provided,
            missing=missing,
            gate_results=gate_results,
            required_map=required_map,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": list(self.required),
            "provided": [item.to_dict() for item in self.provided],
            "missing": list(self.missing),
            "gate_results": [item.to_dict() for item in self.gate_results],
        }

    def to_a2_1_required_evidence(self) -> dict[str, Any]:
        if self.required_map:
            return copy.deepcopy(self.required_map)
        provided = {item.key: item for item in self.provided}
        required: dict[str, Any] = {}
        for key in self.required:
            item = provided.get(key)
            if item is None:
                required[key] = {"status": "missing"}
            else:
                payload = item.to_dict()
                payload.pop("key", None)
                required[key] = payload
        return required


@dataclass(frozen=True)
class ApprovalRequirement:
    """Required human gate state for a single approval role."""

    role: str
    required: bool = False
    recorded: bool = False
    state: str | None = None
    record_id: str | None = None
    actor_id: str | None = None
    evidence_hash: str | None = None
    signed_at: str | None = None
    expires_at: str | None = None
    revoked_at: str | None = None
    source_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, role: str, data: Mapping[str, Any] | None) -> "ApprovalRequirement":
        payload = _mapping(data, f"approval.{role}") if data is not None else {}
        known = {
            "role",
            "required",
            "recorded",
            "state",
            "record_id",
            "actor_id",
            "evidence_hash",
            "signed_at",
            "expires_at",
            "revoked_at",
            "source_ref",
        }
        recorded = _as_bool(payload.get("recorded", False))
        state = _optional_text(payload.get("state"))
        if state is None:
            if recorded:
                state = "recorded"
            elif _as_bool(payload.get("required", False)):
                state = "pending"
            else:
                state = "not_required"
        return cls(
            role=_optional_text(payload.get("role")) or role,
            required=_as_bool(payload.get("required", False)),
            recorded=recorded,
            state=state,
            record_id=_optional_text(payload.get("record_id")),
            actor_id=_optional_text(payload.get("actor_id")),
            evidence_hash=_optional_text(payload.get("evidence_hash")),
            signed_at=_optional_text(payload.get("signed_at")),
            expires_at=_optional_text(payload.get("expires_at")),
            revoked_at=_optional_text(payload.get("revoked_at")),
            source_ref=_optional_text(payload.get("source_ref")),
            metadata={key: value for key, value in payload.items() if key not in known},
        )

    @classmethod
    def from_legacy(
        cls,
        role: str,
        *,
        required: Any,
        recorded: Any,
        source_ref: str | None = None,
    ) -> "ApprovalRequirement":
        is_required = _as_bool(required)
        is_recorded = _as_bool(recorded)
        if is_recorded:
            state = "recorded"
        elif is_required:
            state = "pending"
        else:
            state = "not_required"
        return cls(
            role=role,
            required=is_required,
            recorded=is_recorded,
            state=state,
            source_ref=source_ref,
        )

    def to_dict(self) -> dict[str, Any]:
        data = _compact(
            {
                "role": self.role,
                "required": self.required,
                "recorded": self.recorded,
                "state": self.state,
                "record_id": self.record_id,
                "actor_id": self.actor_id,
                "evidence_hash": self.evidence_hash,
                "signed_at": self.signed_at,
                "expires_at": self.expires_at,
                "revoked_at": self.revoked_at,
                "source_ref": self.source_ref,
            }
        )
        data.update(copy.deepcopy(self.metadata))
        return data


@dataclass(frozen=True)
class ApprovalRecord:
    """An immutable signoff record reference bound to packet evidence."""

    role: str
    record_id: str
    actor_id: str
    evidence_hash: str
    signed_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    source_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ApprovalRecord":
        payload = _mapping(data, "approval.records[]")
        known = {
            "role",
            "record_id",
            "actor_id",
            "evidence_hash",
            "signed_at",
            "expires_at",
            "revoked_at",
            "source_ref",
        }
        return cls(
            role=_required_text(payload.get("role"), "approval.records[].role"),
            record_id=_required_text(payload.get("record_id"), "approval.records[].record_id"),
            actor_id=_required_text(payload.get("actor_id"), "approval.records[].actor_id"),
            evidence_hash=_required_text(payload.get("evidence_hash"), "approval.records[].evidence_hash"),
            signed_at=_required_text(payload.get("signed_at"), "approval.records[].signed_at"),
            expires_at=_optional_text(payload.get("expires_at")),
            revoked_at=_optional_text(payload.get("revoked_at")),
            source_ref=_optional_text(payload.get("source_ref")),
            metadata={key: value for key, value in payload.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        data = _compact(
            {
                "role": self.role,
                "record_id": self.record_id,
                "actor_id": self.actor_id,
                "evidence_hash": self.evidence_hash,
                "signed_at": self.signed_at,
                "expires_at": self.expires_at,
                "revoked_at": self.revoked_at,
                "source_ref": self.source_ref,
            }
        )
        data.update(copy.deepcopy(self.metadata))
        return data


@dataclass(frozen=True)
class ApprovalSubtree:
    """Human approval requirements and recorded signature references."""

    risk_owner: ApprovalRequirement = field(default_factory=lambda: ApprovalRequirement(role="risk_owner"))
    operator: ApprovalRequirement = field(default_factory=lambda: ApprovalRequirement(role="operator"))
    records: tuple[ApprovalRecord, ...] = ()
    status: str | None = None
    approval_decision_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ApprovalSubtree":
        payload = _mapping(data, "approval")
        known = {"risk_owner", "operator", "records", "status", "approval_decision_id"}
        return cls(
            risk_owner=ApprovalRequirement.from_dict("risk_owner", payload.get("risk_owner")),
            operator=ApprovalRequirement.from_dict("operator", payload.get("operator")),
            records=tuple(
                ApprovalRecord.from_dict(item)
                for item in _sequence(payload.get("records"), "approval.records")
            ),
            status=_normalize_approval_status(payload.get("status")),
            approval_decision_id=_optional_text(payload.get("approval_decision_id")),
            metadata={key: value for key, value in payload.items() if key not in known},
        )

    @classmethod
    def from_legacy(cls, payload: Mapping[str, Any]) -> "ApprovalSubtree":
        source_refs = _legacy_approval_source_refs(payload)
        return cls(
            risk_owner=ApprovalRequirement.from_legacy(
                "risk_owner",
                required=payload.get("risk_owner_required", False),
                recorded=payload.get("risk_owner_approval_recorded", False),
                source_ref=source_refs.get("risk_owner"),
            ),
            operator=ApprovalRequirement.from_legacy(
                "operator",
                required=payload.get("operator_required", False),
                recorded=payload.get("operator_approval_recorded", False),
                source_ref=source_refs.get("operator"),
            ),
            status=_normalize_approval_status(payload.get("approval_status")),
            approval_decision_id=_optional_text(payload.get("approval_decision_id")),
        )

    def required_approvals(self) -> tuple[ApprovalRequirement, ...]:
        return tuple(item for item in (self.risk_owner, self.operator) if item.required)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "risk_owner": self.risk_owner.to_dict(),
            "operator": self.operator.to_dict(),
            "records": [item.to_dict() for item in self.records],
        }
        if self.status:
            data["status"] = self.status
        if self.approval_decision_id:
            data["approval_decision_id"] = self.approval_decision_id
        data.update(copy.deepcopy(self.metadata))
        return data


@dataclass(frozen=True)
class FlagSubtree:
    """Fail-closed and side-effect flags asserted by the packet producer."""

    values: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FlagSubtree":
        return cls(values=_mapping(data, "flags"))

    @classmethod
    def from_legacy(cls, payload: Mapping[str, Any]) -> "FlagSubtree":
        flags: dict[str, Any] = {}
        for key in ("flags", "fail_closed_assertions", "safety_assertions", "downstream_scope"):
            value = payload.get(key)
            if isinstance(value, Mapping):
                flags.update(copy.deepcopy(dict(value)))
        return cls(values=flags)

    def unsafe_true_flags(self) -> tuple[str, ...]:
        return tuple(
            key
            for key, value in self.values.items()
            if key in UNSAFE_TRUE_FLAGS and value is True
        )

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.values)


@dataclass(frozen=True)
class BlockingReason:
    """A typed reason preventing the packet from proceeding."""

    code: str
    message: str
    severity: str = "blocking"
    source_ref: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    raw_text: str | None = None

    @classmethod
    def from_value(cls, data: Any) -> "BlockingReason":
        if isinstance(data, str):
            text = _required_text(data, "blocking_reasons[]")
            return cls(code=text, message=text, raw_text=text)
        if not isinstance(data, Mapping):
            raise PromotionReadinessPacketError("blocking_reasons[] must be a string or object")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BlockingReason":
        payload = _mapping(data, "blocking_reasons[]")
        known = {"code", "message", "severity", "source_ref", "details", "raw_text"}
        details = _mapping(payload.get("details"), "blocking_reasons[].details")
        details.update({key: value for key, value in payload.items() if key not in known})
        return cls(
            code=_required_text(payload.get("code"), "blocking_reasons[].code"),
            message=_required_text(payload.get("message"), "blocking_reasons[].message"),
            severity=_optional_text(payload.get("severity")) or "blocking",
            source_ref=_optional_text(payload.get("source_ref")),
            details=details,
            raw_text=_optional_text(payload.get("raw_text")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "code": self.code,
                "message": self.message,
                "severity": self.severity,
                "source_ref": self.source_ref,
                "details": copy.deepcopy(self.details) if self.details else None,
            }
        )

    def to_a2_1_value(self) -> str:
        return self.raw_text or self.message or self.code


@dataclass(frozen=True)
class PromotionReadinessPacket:
    """Structured promotion-readiness packet with A2.1 subtrees."""

    packet_id: str
    target: TargetRef
    evidence: EvidenceSubtree
    approval: ApprovalSubtree
    flags: FlagSubtree
    blocking_reasons: tuple[BlockingReason, ...]
    can_proceed: bool
    reason: str
    schema_version: str = SCHEMA_VERSION
    generated_at: str | None = None
    generated_by: str | None = None
    source_task_id: str | None = None
    depends_on_tasks: tuple[str, ...] = ()
    extensions: dict[str, Any] = field(default_factory=dict)
    packet_version: str | None = None
    target_environment: str | None = None
    refs: dict[str, Any] = field(default_factory=dict)
    packet_format: str = "structured"

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        validate: bool = True,
    ) -> "PromotionReadinessPacket":
        payload = _mapping(data, "packet")
        if _looks_like_a2_1(payload):
            packet = cls._from_a2_1(payload)
        elif "target" in payload or "evidence" in payload or "approval" in payload or "flags" in payload:
            packet = cls._from_structured(payload)
        else:
            packet = cls._from_legacy(payload)
        if validate:
            validate_packet(packet)
        return packet

    @classmethod
    def _from_structured(cls, payload: Mapping[str, Any]) -> "PromotionReadinessPacket":
        known = {
            "packet_id",
            "schema_version",
            "target",
            "evidence",
            "approval",
            "flags",
            "blocking_reasons",
            "can_proceed",
            "reason",
            "generated_at",
            "generated_by",
            "source_task_id",
            "depends_on_tasks",
            "extensions",
        }
        extensions = _mapping(payload.get("extensions"), "extensions")
        extensions.update({key: copy.deepcopy(value) for key, value in payload.items() if key not in known})
        return cls(
            packet_id=_required_text(payload.get("packet_id"), "packet_id"),
            schema_version=_optional_text(payload.get("schema_version")) or SCHEMA_VERSION,
            target=TargetRef.from_dict(_mapping(payload.get("target"), "target")),
            evidence=EvidenceSubtree.from_dict(_mapping(payload.get("evidence"), "evidence")),
            approval=ApprovalSubtree.from_dict(_mapping(payload.get("approval"), "approval")),
            flags=FlagSubtree.from_dict(_mapping(payload.get("flags"), "flags")),
            blocking_reasons=tuple(
                BlockingReason.from_value(item)
                for item in _sequence(payload.get("blocking_reasons"), "blocking_reasons")
            ),
            can_proceed=_as_bool(payload.get("can_proceed", False)),
            reason=_required_text(payload.get("reason"), "reason"),
            generated_at=_optional_text(payload.get("generated_at")),
            generated_by=_optional_text(payload.get("generated_by")),
            source_task_id=_optional_text(payload.get("source_task_id")),
            depends_on_tasks=_string_tuple(payload.get("depends_on_tasks"), "depends_on_tasks"),
            extensions=extensions,
            target_environment=_optional_text(payload.get("target_environment")),
            refs=_extract_refs(payload),
            packet_format="structured",
        )

    @classmethod
    def _from_legacy(cls, payload: Mapping[str, Any]) -> "PromotionReadinessPacket":
        known = {
            "packet_id",
            "schema_version",
            "target_type",
            "target_id",
            "environment",
            "target_version",
            "generated_at",
            "generated_by",
            "source_task_id",
            "depends_on_tasks",
            "required_evidence",
            "provided_evidence",
            "missing_evidence",
            "gate_results",
            "risk_owner_required",
            "risk_owner_approval_recorded",
            "operator_required",
            "operator_approval_recorded",
            "approval_status",
            "can_proceed",
            "reason",
            "approval_decision_id",
            "blocking_reasons",
            "flags",
            "fail_closed_assertions",
            "safety_assertions",
            "downstream_scope",
        }
        return cls(
            packet_id=_required_text(payload.get("packet_id"), "packet_id"),
            schema_version=_optional_text(payload.get("schema_version")) or LEGACY_SCHEMA_VERSION,
            target=TargetRef.from_legacy(payload),
            evidence=EvidenceSubtree.from_legacy(payload),
            approval=ApprovalSubtree.from_legacy(payload),
            flags=FlagSubtree.from_legacy(payload),
            blocking_reasons=tuple(
                BlockingReason.from_value(item)
                for item in _sequence(payload.get("blocking_reasons"), "blocking_reasons")
            ),
            can_proceed=_as_bool(payload.get("can_proceed", False)),
            reason=_required_text(payload.get("reason"), "reason"),
            generated_at=_optional_text(payload.get("generated_at")),
            generated_by=_optional_text(payload.get("generated_by")),
            source_task_id=_optional_text(payload.get("source_task_id")),
            depends_on_tasks=_string_tuple(payload.get("depends_on_tasks"), "depends_on_tasks"),
            extensions={key: copy.deepcopy(value) for key, value in payload.items() if key not in known},
            packet_format="legacy",
        )

    @classmethod
    def _from_a2_1(cls, payload: Mapping[str, Any]) -> "PromotionReadinessPacket":
        known = {
            "packet_id",
            "packet_version",
            "schema_version",
            "generated_at",
            "generated_by",
            "source_task_id",
            "depends_on_tasks",
            "target_type",
            "target_id",
            "target_version",
            "target_environment",
            "environment",
            "deployment_stage",
            "required_evidence",
            "gate_results",
            "approval",
            "flags",
            "blocking_reasons",
            "can_proceed",
            "reason",
            *REF_FIELDS,
            *REF_ALIASES,
        }
        flags = FlagSubtree.from_dict(_mapping(payload.get("flags"), "flags"))
        can_proceed_source = flags.values.get("can_proceed", payload.get("can_proceed", False))
        refs = _extract_refs(payload)
        target = _target_from_a2_1(payload, refs)
        return cls(
            packet_id=_required_text(payload.get("packet_id"), "packet_id"),
            schema_version=_optional_text(payload.get("schema_version")) or SCHEMA_VERSION,
            packet_version=_optional_text(payload.get("packet_version")) or A2_1_PACKET_VERSION,
            target=target,
            target_environment=target.environment,
            evidence=EvidenceSubtree.from_a2_1(payload),
            approval=ApprovalSubtree.from_dict(_mapping(payload.get("approval"), "approval")),
            flags=flags,
            blocking_reasons=tuple(
                BlockingReason.from_value(item)
                for item in _sequence(payload.get("blocking_reasons"), "blocking_reasons")
            ),
            can_proceed=_as_bool(can_proceed_source),
            reason=_required_text(payload.get("reason"), "reason"),
            generated_at=_optional_text(payload.get("generated_at")),
            generated_by=_optional_text(payload.get("generated_by")),
            source_task_id=_optional_text(payload.get("source_task_id")),
            depends_on_tasks=_string_tuple(payload.get("depends_on_tasks"), "depends_on_tasks"),
            extensions={key: copy.deepcopy(value) for key, value in payload.items() if key not in known},
            refs=refs,
            packet_format="a2_1",
        )

    def to_dict(self) -> dict[str, Any]:
        if self.packet_format == "a2_1":
            return self.to_a2_1_dict()

        data = _compact(
            {
                "packet_id": self.packet_id,
                "schema_version": self.schema_version,
                "generated_at": self.generated_at,
                "generated_by": self.generated_by,
                "source_task_id": self.source_task_id,
                "reason": self.reason,
            }
        )
        data.update(
            {
                "target": self.target.to_dict(),
                "depends_on_tasks": list(self.depends_on_tasks),
                "evidence": self.evidence.to_dict(),
                "approval": self.approval.to_dict(),
                "flags": self.flags.to_dict(),
                "blocking_reasons": [item.to_dict() for item in self.blocking_reasons],
                "can_proceed": self.can_proceed,
            }
        )
        if self.extensions:
            data["extensions"] = copy.deepcopy(self.extensions)
        return data

    def to_a2_1_dict(self) -> dict[str, Any]:
        flags = self.flags.to_dict()
        flags["can_proceed"] = self.can_proceed
        data = _compact(
            {
                "packet_id": self.packet_id,
                "packet_version": self.packet_version or A2_1_PACKET_VERSION,
                "generated_at": self.generated_at,
                "generated_by": self.generated_by,
                "source_task_id": self.source_task_id,
                "target_environment": self.target_environment or self.target.environment,
                "target_type": self.target.target_type,
                "target_id": self.target.target_id,
                "target_version": self.target.target_version,
                "deployment_stage": self.target.deployment_stage,
                "reason": self.reason,
            }
        )
        if self.depends_on_tasks:
            data["depends_on_tasks"] = list(self.depends_on_tasks)
        for field_name in REF_FIELDS:
            if field_name in self.refs:
                data[field_name] = copy.deepcopy(self.refs[field_name])
        data.update(
            {
                "required_evidence": self.evidence.to_a2_1_required_evidence(),
                "approval": self.approval.to_dict(),
                "flags": flags,
                "blocking_reasons": [item.to_a2_1_value() for item in self.blocking_reasons],
            }
        )
        data.update(copy.deepcopy(self.extensions))
        return data

    def to_legacy_dict(self) -> dict[str, Any]:
        """Return the minimal v1 flat shape used by existing admission packets."""
        data = _compact(
            {
                "packet_id": self.packet_id,
                "schema_version": self.schema_version,
                "target_type": self.target.target_type,
                "target_id": self.target.target_id,
                "environment": self.target.environment,
                "generated_at": self.generated_at,
                "generated_by": self.generated_by,
                "source_task_id": self.source_task_id,
                "reason": self.reason,
            }
        )
        data.update(
            {
                "depends_on_tasks": list(self.depends_on_tasks),
                "required_evidence": list(self.evidence.required),
                "provided_evidence": [item.to_dict() for item in self.evidence.provided],
                "missing_evidence": list(self.evidence.missing),
                "gate_results": [item.to_dict() for item in self.evidence.gate_results],
                "risk_owner_required": self.approval.risk_owner.required,
                "risk_owner_approval_recorded": self.approval.risk_owner.recorded,
                "operator_required": self.approval.operator.required,
                "operator_approval_recorded": self.approval.operator.recorded,
                "blocking_reasons": [item.to_dict() for item in self.blocking_reasons],
                "can_proceed": self.can_proceed,
            }
        )
        data.update(copy.deepcopy(self.extensions))
        return data


def validate_packet(packet_or_data: PromotionReadinessPacket | Mapping[str, Any]) -> PromotionReadinessPacket:
    """Validate packet consistency and return the normalized packet.

    This is not the EP5-002 policy validator. It only rejects impossible packet
    states, especially any packet claiming `can_proceed=true` while fail-closed
    blockers are still present.
    """

    packet = (
        packet_or_data
        if isinstance(packet_or_data, PromotionReadinessPacket)
        else PromotionReadinessPacket.from_dict(packet_or_data, validate=False)
    )

    _required_text(packet.packet_id, "packet_id")
    _required_text(packet.reason, "reason")
    _required_text(packet.target.target_type, "target.target_type")
    _required_text(packet.target.target_id, "target.target_id")
    _required_text(packet.target.environment, "target.environment")

    provided_keys = {item.key for item in packet.evidence.provided}
    missing_required = tuple(key for key in packet.evidence.required if key not in provided_keys)
    blocking_provided_evidence = tuple(
        item for item in packet.evidence.provided if item.status not in PASSING_STATUSES
    )
    blocking_gate_results = tuple(item for item in packet.evidence.gate_results if item.status not in PASSING_STATUSES)
    blocking_approvals = tuple(
        item
        for item in packet.approval.required_approvals()
        if not item.recorded or item.state in {"pending", "expired", "revoked"}
    )
    approval_status = packet.approval.status
    unsafe_flags = packet.flags.unsafe_true_flags()

    if packet.can_proceed:
        errors: list[str] = []
        if packet.blocking_reasons:
            errors.append("blocking_reasons must be empty when can_proceed is true")
        if packet.evidence.missing:
            errors.append("evidence.missing must be empty when can_proceed is true")
        if missing_required:
            errors.append(
                "evidence.required must all be provided when can_proceed is true: "
                + ", ".join(missing_required)
            )
        if blocking_provided_evidence:
            errors.append(
                "provided evidence statuses must pass when can_proceed is true: "
                + ", ".join(f"{item.key}={item.status}" for item in blocking_provided_evidence)
            )
        if blocking_gate_results:
            errors.append(
                "all gate_results must pass when can_proceed is true: "
                + ", ".join(f"{item.gate}={item.status}" for item in blocking_gate_results)
            )
        if blocking_approvals:
            errors.append(
                "required approvals must be recorded when can_proceed is true: "
                + ", ".join(item.role for item in blocking_approvals)
            )
        if approval_status and approval_status not in PASSING_APPROVAL_STATUSES:
            errors.append(
                "approval.status must be approved when can_proceed is true: "
                + approval_status
            )
        if unsafe_flags:
            errors.append(
                "fail-closed live/capital side-effect flags must stay false when can_proceed is true: "
                + ", ".join(unsafe_flags)
            )
        if errors:
            raise PromotionReadinessPacketError("; ".join(errors))

    return packet


def load_packet(path: str | Path) -> PromotionReadinessPacket:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise PromotionReadinessPacketError(f"{path} must contain a JSON object")
    return PromotionReadinessPacket.from_dict(payload)


def dump_packet(path: str | Path, packet: PromotionReadinessPacket) -> None:
    validate_packet(packet)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(packet.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _legacy_approval_source_refs(payload: Mapping[str, Any]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for item in _sequence(payload.get("gate_results"), "gate_results"):
        if not isinstance(item, Mapping):
            continue
        gate = str(item.get("gate") or "")
        source_ref = _optional_text(item.get("source_ref"))
        if gate == "risk_owner_approval" and source_ref:
            refs["risk_owner"] = source_ref
        elif gate == "operator_approval" and source_ref:
            refs["operator"] = source_ref
    return refs
