"""Human gate decision model for promotion signoff.

The human gate binds named approval roles to the exact evidence bundle they
reviewed.  It is deliberately stricter than the generic ApprovalDecision
object: a deployment cannot proceed until the readiness packet is already
eligible, every required role has signed, and every signature is bound to the
same evidence hash.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "HumanGateDecision.v1"
DECISION_STATUSES = {"pending", "approved", "blocked", "rejected", "revoked"}
PASSING_EVIDENCE_STATUSES = {"pass", "passed", "present", "satisfied", "not_applicable"}
SIGNATURE_MEANINGS = {"approved", "approved_with_conditions", "rejected", "revoked"}


class HumanGateDecisionError(ValueError):
    """Raised when a human gate decision is structurally inconsistent."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise HumanGateDecisionError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise HumanGateDecisionError(f"{field_name} is required")
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
        raise HumanGateDecisionError(f"{field_name} must be an object")
    return copy.deepcopy(dict(value))


def _sequence(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise HumanGateDecisionError(f"{field_name} must be a list")
    return list(value)


def _string_tuple(values: Any, field_name: str) -> tuple[str, ...]:
    return tuple(_required_text(item, f"{field_name}[]") for item in _sequence(values, field_name))


def _unique_non_empty(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        text = _required_text(value, f"{field_name}[]")
        if text in seen:
            raise HumanGateDecisionError(f"{field_name} must not contain duplicate values: {text}")
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _normalize_status(value: Any, *, default: str = "pending") -> str:
    status = str(value or default).strip().lower()
    if status not in DECISION_STATUSES:
        raise HumanGateDecisionError(
            "status must be one of: " + ", ".join(sorted(DECISION_STATUSES))
        )
    return status


def _normalize_signature_meaning(value: Any) -> str:
    meaning = str(value or "approved").strip().lower()
    if meaning not in SIGNATURE_MEANINGS:
        raise HumanGateDecisionError(
            "signature.meaning must be one of: " + ", ".join(sorted(SIGNATURE_MEANINGS))
        )
    return meaning


def _normalize_hash(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not text.startswith("sha256:") or len(text) <= len("sha256:"):
        raise HumanGateDecisionError(f"{field_name} must be a sha256:<hex> value")
    return text


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compute_evidence_hash(evidence_reviewed: Sequence["EvidenceReviewed"]) -> str:
    normalized = [
        item.to_hash_payload()
        for item in sorted(evidence_reviewed, key=lambda evidence: evidence.key)
    ]
    return stable_hash(normalized)


@dataclass(frozen=True)
class EvidenceReviewed:
    """One evidence item explicitly reviewed for the human gate."""

    key: str
    evidence_hash: str
    source_ref: str
    status: str = "passed"
    reviewed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceReviewed":
        payload = _mapping(data, "evidence_reviewed[]")
        known = {"key", "evidence_hash", "source_ref", "status", "reviewed_at"}
        return cls(
            key=_required_text(payload.get("key"), "evidence_reviewed[].key"),
            evidence_hash=_normalize_hash(
                payload.get("evidence_hash"),
                "evidence_reviewed[].evidence_hash",
            ),
            source_ref=_required_text(payload.get("source_ref"), "evidence_reviewed[].source_ref"),
            status=str(payload.get("status") or "passed").strip().lower(),
            reviewed_at=_optional_text(payload.get("reviewed_at")),
            metadata={key: value for key, value in payload.items() if key not in known},
        )

    def to_hash_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "evidence_hash": self.evidence_hash,
            "source_ref": self.source_ref,
            "status": self.status,
        }

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "key": self.key,
            "evidence_hash": self.evidence_hash,
            "source_ref": self.source_ref,
            "status": self.status,
        }
        if self.reviewed_at:
            data["reviewed_at"] = self.reviewed_at
        data.update(copy.deepcopy(self.metadata))
        return data


@dataclass(frozen=True)
class CanProceedInput:
    """Readiness inputs that must already be clean before signatures can pass."""

    readiness_packet_ref: str | None = None
    readiness_packet_can_proceed: bool = False
    required_evidence: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    unsafe_true_flags: tuple[str, ...] = ()
    gate_results_blocking: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "CanProceedInput":
        payload = _mapping(data, "can_proceed_input") if data is not None else {}
        known = {
            "readiness_packet_ref",
            "readiness_packet_can_proceed",
            "required_evidence",
            "missing_evidence",
            "blocking_reasons",
            "unsafe_true_flags",
            "gate_results_blocking",
        }
        return cls(
            readiness_packet_ref=_optional_text(payload.get("readiness_packet_ref")),
            readiness_packet_can_proceed=_as_bool(payload.get("readiness_packet_can_proceed", False)),
            required_evidence=_string_tuple(payload.get("required_evidence"), "can_proceed_input.required_evidence"),
            missing_evidence=_string_tuple(payload.get("missing_evidence"), "can_proceed_input.missing_evidence"),
            blocking_reasons=_string_tuple(payload.get("blocking_reasons"), "can_proceed_input.blocking_reasons"),
            unsafe_true_flags=_string_tuple(payload.get("unsafe_true_flags"), "can_proceed_input.unsafe_true_flags"),
            gate_results_blocking=_string_tuple(
                payload.get("gate_results_blocking"),
                "can_proceed_input.gate_results_blocking",
            ),
            metadata={key: value for key, value in payload.items() if key not in known},
        )

    def base_ready(self) -> bool:
        return (
            self.readiness_packet_can_proceed
            and not self.missing_evidence
            and not self.blocking_reasons
            and not self.unsafe_true_flags
            and not self.gate_results_blocking
        )

    def blocking_summary(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if not self.readiness_packet_can_proceed:
            blockers.append("readiness_packet_can_proceed=false")
        blockers.extend(f"missing_evidence:{item}" for item in self.missing_evidence)
        blockers.extend(f"blocking_reason:{item}" for item in self.blocking_reasons)
        blockers.extend(f"unsafe_true_flag:{item}" for item in self.unsafe_true_flags)
        blockers.extend(f"gate_result_blocking:{item}" for item in self.gate_results_blocking)
        return tuple(blockers)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "readiness_packet_can_proceed": self.readiness_packet_can_proceed,
            "required_evidence": list(self.required_evidence),
            "missing_evidence": list(self.missing_evidence),
            "blocking_reasons": list(self.blocking_reasons),
            "unsafe_true_flags": list(self.unsafe_true_flags),
            "gate_results_blocking": list(self.gate_results_blocking),
        }
        if self.readiness_packet_ref:
            data["readiness_packet_ref"] = self.readiness_packet_ref
        data.update(copy.deepcopy(self.metadata))
        return data


@dataclass(frozen=True)
class HumanGateSignature:
    """A human signature over the decision's evidence hash."""

    signature_id: str
    role: str
    actor_id: str
    signed_at: str
    evidence_hash: str
    evidence_reviewed: tuple[str, ...]
    meaning: str = "approved"
    conditions: tuple[str, ...] = ()
    source_ref: str | None = None
    revoked_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HumanGateSignature":
        payload = _mapping(data, "signatures[]")
        known = {
            "signature_id",
            "role",
            "actor_id",
            "signed_at",
            "evidence_hash",
            "evidence_reviewed",
            "meaning",
            "conditions",
            "source_ref",
            "revoked_at",
        }
        return cls(
            signature_id=_required_text(payload.get("signature_id"), "signature.signature_id"),
            role=_required_text(payload.get("role"), "signature.role"),
            actor_id=_required_text(payload.get("actor_id"), "signature.actor_id"),
            signed_at=_required_text(payload.get("signed_at"), "signature.signed_at"),
            evidence_hash=_normalize_hash(payload.get("evidence_hash"), "signature.evidence_hash"),
            evidence_reviewed=_unique_non_empty(
                _string_tuple(payload.get("evidence_reviewed"), "signature.evidence_reviewed"),
                "signature.evidence_reviewed",
            ),
            meaning=_normalize_signature_meaning(payload.get("meaning")),
            conditions=_string_tuple(payload.get("conditions"), "signature.conditions"),
            source_ref=_optional_text(payload.get("source_ref")),
            revoked_at=_optional_text(payload.get("revoked_at")),
            metadata={key: value for key, value in payload.items() if key not in known},
        )

    def active_approval(self) -> bool:
        return self.revoked_at is None and self.meaning in {"approved", "approved_with_conditions"}

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "signature_id": self.signature_id,
            "role": self.role,
            "actor_id": self.actor_id,
            "signed_at": self.signed_at,
            "evidence_hash": self.evidence_hash,
            "evidence_reviewed": list(self.evidence_reviewed),
            "meaning": self.meaning,
            "conditions": list(self.conditions),
        }
        if self.source_ref:
            data["source_ref"] = self.source_ref
        if self.revoked_at:
            data["revoked_at"] = self.revoked_at
        data.update(copy.deepcopy(self.metadata))
        return data


@dataclass(frozen=True)
class HumanGateDecision:
    """Human approval gate bound to a promotion readiness evidence bundle."""

    decision_id: str
    target_type: str
    target_id: str
    target_environment: str
    required_roles: tuple[str, ...]
    evidence_reviewed: tuple[EvidenceReviewed, ...]
    can_proceed_input: CanProceedInput
    signatures: tuple[HumanGateSignature, ...] = ()
    schema_version: str = SCHEMA_VERSION
    status: str = "pending"
    created_at: str | None = None
    updated_at: str | None = None
    reason: str | None = None
    evidence_hash: str | None = None
    can_proceed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        validate: bool = True,
    ) -> "HumanGateDecision":
        payload = _mapping(data, "human_gate_decision")
        known = {
            "decision_id",
            "schema_version",
            "target_type",
            "target_id",
            "target_environment",
            "required_roles",
            "evidence_reviewed",
            "can_proceed_input",
            "signatures",
            "status",
            "created_at",
            "updated_at",
            "reason",
            "evidence_hash",
            "can_proceed",
        }
        decision = cls(
            decision_id=_required_text(payload.get("decision_id"), "decision_id"),
            schema_version=_optional_text(payload.get("schema_version")) or SCHEMA_VERSION,
            target_type=_required_text(payload.get("target_type"), "target_type"),
            target_id=_required_text(payload.get("target_id"), "target_id"),
            target_environment=_required_text(payload.get("target_environment"), "target_environment"),
            required_roles=_unique_non_empty(
                _string_tuple(payload.get("required_roles"), "required_roles"),
                "required_roles",
            ),
            evidence_reviewed=tuple(
                EvidenceReviewed.from_dict(item)
                for item in _sequence(payload.get("evidence_reviewed"), "evidence_reviewed")
            ),
            can_proceed_input=CanProceedInput.from_dict(payload.get("can_proceed_input")),
            signatures=tuple(
                HumanGateSignature.from_dict(item)
                for item in _sequence(payload.get("signatures"), "signatures")
            ),
            status=_normalize_status(payload.get("status")),
            created_at=_optional_text(payload.get("created_at")),
            updated_at=_optional_text(payload.get("updated_at")),
            reason=_optional_text(payload.get("reason")),
            evidence_hash=_optional_text(payload.get("evidence_hash")),
            can_proceed=_as_bool(payload.get("can_proceed", False)),
            metadata={key: value for key, value in payload.items() if key not in known},
        )
        if validate:
            return validate_decision(decision)
        return decision

    def active_signatures_by_role(self) -> dict[str, HumanGateSignature]:
        active: dict[str, HumanGateSignature] = {}
        for signature in self.signatures:
            if signature.active_approval():
                active[signature.role] = signature
        return active

    def missing_required_roles(self) -> tuple[str, ...]:
        active = self.active_signatures_by_role()
        return tuple(role for role in self.required_roles if role not in active)

    def rejected_roles(self) -> tuple[str, ...]:
        return tuple(
            signature.role
            for signature in self.signatures
            if signature.revoked_at is None and signature.meaning == "rejected"
        )

    def calculated_can_proceed(self) -> bool:
        return (
            self.can_proceed_input.base_ready()
            and not self.missing_required_roles()
            and not self.rejected_roles()
        )

    def calculated_status(self) -> str:
        if any(signature.revoked_at is None and signature.meaning == "revoked" for signature in self.signatures):
            return "revoked"
        if self.rejected_roles():
            return "rejected"
        if not self.can_proceed_input.base_ready():
            return "blocked"
        if self.calculated_can_proceed():
            return "approved"
        return "pending"

    def blocking_summary(self) -> tuple[str, ...]:
        blockers = list(self.can_proceed_input.blocking_summary())
        blockers.extend(f"missing_signature:{role}" for role in self.missing_required_roles())
        blockers.extend(f"rejected_signature:{role}" for role in self.rejected_roles())
        return tuple(blockers)

    def with_signature(self, signature: HumanGateSignature) -> "HumanGateDecision":
        if signature.role not in self.required_roles:
            raise HumanGateDecisionError(f"signature role is not required for this decision: {signature.role}")
        if any(existing.signature_id == signature.signature_id for existing in self.signatures):
            raise HumanGateDecisionError(f"signature_id already exists: {signature.signature_id}")
        if signature.role in self.active_signatures_by_role() and signature.active_approval():
            raise HumanGateDecisionError(f"role already has an active approval signature: {signature.role}")
        return validate_decision(replace(self, signatures=(*self.signatures, signature), updated_at=utc_now()))

    def normalized(self) -> "HumanGateDecision":
        expected_hash = compute_evidence_hash(self.evidence_reviewed)
        normalized = replace(
            self,
            evidence_hash=expected_hash,
            status=self.calculated_status(),
            can_proceed=self.calculated_can_proceed(),
        )
        if normalized.reason is None:
            if normalized.can_proceed:
                reason = "All required human gate signatures are bound to the reviewed evidence."
            else:
                reason = "Human gate is not ready: " + ", ".join(normalized.blocking_summary())
            normalized = replace(normalized, reason=reason)
        return normalized

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "decision_id": self.decision_id,
            "schema_version": self.schema_version,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "target_environment": self.target_environment,
            "required_roles": list(self.required_roles),
            "evidence_reviewed": [item.to_dict() for item in self.evidence_reviewed],
            "evidence_hash": self.evidence_hash,
            "can_proceed_input": self.can_proceed_input.to_dict(),
            "signatures": [item.to_dict() for item in self.signatures],
            "status": self.status,
            "can_proceed": self.can_proceed,
            "reason": self.reason,
        }
        if self.created_at:
            data["created_at"] = self.created_at
        if self.updated_at:
            data["updated_at"] = self.updated_at
        data.update(copy.deepcopy(self.metadata))
        return data


def validate_decision(decision_or_data: HumanGateDecision | Mapping[str, Any]) -> HumanGateDecision:
    """Validate, normalize, and return a HumanGateDecision."""

    decision = (
        decision_or_data
        if isinstance(decision_or_data, HumanGateDecision)
        else HumanGateDecision.from_dict(decision_or_data, validate=False)
    )

    _required_text(decision.decision_id, "decision_id")
    _required_text(decision.target_type, "target_type")
    _required_text(decision.target_id, "target_id")
    _required_text(decision.target_environment, "target_environment")
    if decision.schema_version != SCHEMA_VERSION:
        raise HumanGateDecisionError(f"schema_version must be {SCHEMA_VERSION}")
    if not decision.required_roles:
        raise HumanGateDecisionError("required_roles must not be empty")
    if not decision.evidence_reviewed:
        raise HumanGateDecisionError("evidence_reviewed must not be empty")

    evidence_keys = _unique_non_empty([item.key for item in decision.evidence_reviewed], "evidence_reviewed.key")
    required_evidence = set(decision.can_proceed_input.required_evidence)
    if required_evidence and not required_evidence.issubset(set(evidence_keys)):
        missing = sorted(required_evidence.difference(evidence_keys))
        raise HumanGateDecisionError(
            "can_proceed_input.required_evidence must be included in evidence_reviewed: "
            + ", ".join(missing)
        )

    blocking_evidence = tuple(
        item for item in decision.evidence_reviewed if item.status not in PASSING_EVIDENCE_STATUSES
    )
    if blocking_evidence and decision.can_proceed_input.readiness_packet_can_proceed:
        raise HumanGateDecisionError(
            "evidence_reviewed statuses must pass when readiness_packet_can_proceed is true: "
            + ", ".join(f"{item.key}={item.status}" for item in blocking_evidence)
        )

    expected_hash = compute_evidence_hash(decision.evidence_reviewed)
    if decision.evidence_hash and decision.evidence_hash != expected_hash:
        raise HumanGateDecisionError("evidence_hash does not match evidence_reviewed")

    signature_ids = _unique_non_empty([item.signature_id for item in decision.signatures], "signature.signature_id")
    if len(signature_ids) != len(decision.signatures):
        raise HumanGateDecisionError("signature_id values must be unique")

    for signature in decision.signatures:
        if signature.role not in decision.required_roles:
            raise HumanGateDecisionError(f"signature role is not required for this decision: {signature.role}")
        if signature.evidence_hash != expected_hash:
            raise HumanGateDecisionError("signature evidence_hash must match decision evidence_hash")
        unknown_evidence = sorted(set(signature.evidence_reviewed).difference(evidence_keys))
        if unknown_evidence:
            raise HumanGateDecisionError(
                "signature evidence_reviewed contains unknown evidence keys: "
                + ", ".join(unknown_evidence)
            )
        if not set(signature.evidence_reviewed).issuperset(required_evidence or set(evidence_keys)):
            raise HumanGateDecisionError(
                "signature evidence_reviewed must cover all required evidence"
            )

    normalized = decision.normalized()
    if decision.can_proceed and not normalized.can_proceed:
        raise HumanGateDecisionError(
            "can_proceed cannot be true until readiness input passes and required signatures are present"
        )
    if decision.status == "approved" and not normalized.can_proceed:
        raise HumanGateDecisionError("status cannot be approved when can_proceed is false")
    return normalized
