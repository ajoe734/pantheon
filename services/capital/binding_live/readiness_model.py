"""CapitalBindingLiveReadiness schema model.

Implements the 2026-05-19 blueprint supplement Part C2 shape. This module is
schema and consistency validation only; it never enables capital live writes or
performs runtime/broker side effects.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "CapitalBindingLiveReadiness.v1"
PART_C2_PACKET_VERSION = "2026-05-19.C2"

APPROVAL_STATUSES = {"pending", "approved", "rejected", "revoked", "expired"}
PASSING_APPROVAL_STATUS = "approved"

REQUIRED_TOP_LEVEL_FIELDS = (
    "readiness_id",
    "binding_id",
    "persona_id",
    "capital_pool_id",
    "artifact_id",
    "runtime_id",
    "deployment_plan_id",
    "risk_policy_id",
)
REQUIRED_ROLE_FIELDS = ("sponsor_persona", "live_owner", "risk_owner", "operator")
REQUIRED_EVIDENCE_FIELDS = (
    "persona_mandate_ref",
    "sponsor_responsibility_ref",
    "conflict_resolution_log_ref",
    "pool_risk_policy_ref",
    "runtime_compatibility_ref",
    "artifact_approval_ref",
    "deployment_plan_ref",
    "rollback_target_ref",
    "telemetry_readiness_ref",
    "ep5_packet_ref",
)


class CapitalBindingLiveReadinessError(ValueError):
    """Raised when a CapitalBindingLiveReadiness packet is invalid."""


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CapitalBindingLiveReadinessError(f"{field_name} must be an object")
    return copy.deepcopy(dict(value))


def _sequence(value: Any, field_name: str) -> list[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CapitalBindingLiveReadinessError(f"{field_name} must be a list")
    return list(value)


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise CapitalBindingLiveReadinessError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise CapitalBindingLiveReadinessError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_status(value: Any, field_name: str) -> str:
    status = _required_text(value, field_name).lower()
    if status not in APPROVAL_STATUSES:
        raise CapitalBindingLiveReadinessError(
            f"{field_name} must be one of: {', '.join(sorted(APPROVAL_STATUSES))}"
        )
    return status


def _required_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise CapitalBindingLiveReadinessError(f"{field_name} must be a boolean")
    return value


def _required_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CapitalBindingLiveReadinessError(f"{field_name} must be a number")
    number = float(value)
    if number < 0 or number > 100:
        raise CapitalBindingLiveReadinessError(f"{field_name} must be between 0 and 100")
    return number


def _required_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CapitalBindingLiveReadinessError(f"{field_name} must be an integer")
    if value <= 0:
        raise CapitalBindingLiveReadinessError(f"{field_name} must be greater than 0")
    return value


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    return tuple(_required_text(item, f"{field_name}[]") for item in _sequence(value, field_name))


def _compact(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


@dataclass(frozen=True)
class CapitalBindingRoles:
    sponsor_persona: str
    live_owner: str
    risk_owner: str
    operator: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapitalBindingRoles":
        payload = _mapping(data, "roles")
        return cls(
            sponsor_persona=_required_text(payload.get("sponsor_persona"), "roles.sponsor_persona"),
            live_owner=_required_text(payload.get("live_owner"), "roles.live_owner"),
            risk_owner=_required_text(payload.get("risk_owner"), "roles.risk_owner"),
            operator=_required_text(payload.get("operator"), "roles.operator"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "sponsor_persona": self.sponsor_persona,
            "live_owner": self.live_owner,
            "risk_owner": self.risk_owner,
            "operator": self.operator,
        }


@dataclass(frozen=True)
class CapitalBindingRequiredEvidence:
    persona_mandate_ref: str
    sponsor_responsibility_ref: str
    conflict_resolution_log_ref: str
    pool_risk_policy_ref: str
    runtime_compatibility_ref: str
    artifact_approval_ref: str
    deployment_plan_ref: str
    rollback_target_ref: str
    telemetry_readiness_ref: str
    ep5_packet_ref: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapitalBindingRequiredEvidence":
        payload = _mapping(data, "required_evidence")
        return cls(
            persona_mandate_ref=_required_text(
                payload.get("persona_mandate_ref"),
                "required_evidence.persona_mandate_ref",
            ),
            sponsor_responsibility_ref=_required_text(
                payload.get("sponsor_responsibility_ref"),
                "required_evidence.sponsor_responsibility_ref",
            ),
            conflict_resolution_log_ref=_required_text(
                payload.get("conflict_resolution_log_ref"),
                "required_evidence.conflict_resolution_log_ref",
            ),
            pool_risk_policy_ref=_required_text(
                payload.get("pool_risk_policy_ref"),
                "required_evidence.pool_risk_policy_ref",
            ),
            runtime_compatibility_ref=_required_text(
                payload.get("runtime_compatibility_ref"),
                "required_evidence.runtime_compatibility_ref",
            ),
            artifact_approval_ref=_required_text(
                payload.get("artifact_approval_ref"),
                "required_evidence.artifact_approval_ref",
            ),
            deployment_plan_ref=_required_text(
                payload.get("deployment_plan_ref"),
                "required_evidence.deployment_plan_ref",
            ),
            rollback_target_ref=_required_text(
                payload.get("rollback_target_ref"),
                "required_evidence.rollback_target_ref",
            ),
            telemetry_readiness_ref=_required_text(
                payload.get("telemetry_readiness_ref"),
                "required_evidence.telemetry_readiness_ref",
            ),
            ep5_packet_ref=_required_text(payload.get("ep5_packet_ref"), "required_evidence.ep5_packet_ref"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "persona_mandate_ref": self.persona_mandate_ref,
            "sponsor_responsibility_ref": self.sponsor_responsibility_ref,
            "conflict_resolution_log_ref": self.conflict_resolution_log_ref,
            "pool_risk_policy_ref": self.pool_risk_policy_ref,
            "runtime_compatibility_ref": self.runtime_compatibility_ref,
            "artifact_approval_ref": self.artifact_approval_ref,
            "deployment_plan_ref": self.deployment_plan_ref,
            "rollback_target_ref": self.rollback_target_ref,
            "telemetry_readiness_ref": self.telemetry_readiness_ref,
            "ep5_packet_ref": self.ep5_packet_ref,
        }


@dataclass(frozen=True)
class CapitalBindingControls:
    max_budget_pct: float
    ttl_hours: int
    revocation_allowed: bool
    auto_scale_allowed: bool
    live_order_allowed: bool

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapitalBindingControls":
        payload = _mapping(data, "controls")
        controls = cls(
            max_budget_pct=_required_number(payload.get("max_budget_pct"), "controls.max_budget_pct"),
            ttl_hours=_required_int(payload.get("ttl_hours"), "controls.ttl_hours"),
            revocation_allowed=_required_bool(
                payload.get("revocation_allowed"),
                "controls.revocation_allowed",
            ),
            auto_scale_allowed=_required_bool(
                payload.get("auto_scale_allowed"),
                "controls.auto_scale_allowed",
            ),
            live_order_allowed=_required_bool(
                payload.get("live_order_allowed"),
                "controls.live_order_allowed",
            ),
        )
        controls.validate_fail_closed()
        return controls

    def validate_fail_closed(self) -> None:
        failures: list[str] = []
        if self.revocation_allowed is not True:
            failures.append("controls.revocation_allowed must be true")
        if self.auto_scale_allowed is not False:
            failures.append("controls.auto_scale_allowed must be false")
        if self.live_order_allowed is not False:
            failures.append("controls.live_order_allowed must be false")
        if failures:
            raise CapitalBindingLiveReadinessError(
                "fail-closed controls violated: " + "; ".join(failures)
            )

    def to_dict(self) -> dict[str, int | float | bool]:
        max_budget_pct: int | float = (
            int(self.max_budget_pct) if self.max_budget_pct.is_integer() else self.max_budget_pct
        )
        return {
            "max_budget_pct": max_budget_pct,
            "ttl_hours": self.ttl_hours,
            "revocation_allowed": self.revocation_allowed,
            "auto_scale_allowed": self.auto_scale_allowed,
            "live_order_allowed": self.live_order_allowed,
        }


@dataclass(frozen=True)
class CapitalBindingApproval:
    risk_owner: str
    operator: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapitalBindingApproval":
        payload = _mapping(data, "approval")
        return cls(
            risk_owner=_required_status(payload.get("risk_owner"), "approval.risk_owner"),
            operator=_required_status(payload.get("operator"), "approval.operator"),
        )

    def blocking_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.risk_owner != PASSING_APPROVAL_STATUS:
            reasons.append(f"risk_owner_approval_{self.risk_owner}")
        if self.operator != PASSING_APPROVAL_STATUS:
            reasons.append(f"operator_approval_{self.operator}")
        return tuple(reasons)

    def to_dict(self) -> dict[str, str]:
        return {
            "risk_owner": self.risk_owner,
            "operator": self.operator,
        }


@dataclass(frozen=True)
class CapitalBindingResult:
    can_bind_live: bool
    blocking_reasons: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapitalBindingResult":
        payload = _mapping(data, "result")
        return cls(
            can_bind_live=_required_bool(payload.get("can_bind_live"), "result.can_bind_live"),
            blocking_reasons=_string_tuple(
                payload.get("blocking_reasons"),
                "result.blocking_reasons",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_bind_live": self.can_bind_live,
            "blocking_reasons": list(self.blocking_reasons),
        }


@dataclass(frozen=True)
class CapitalBindingLiveReadiness:
    readiness_id: str
    binding_id: str
    persona_id: str
    capital_pool_id: str
    artifact_id: str
    runtime_id: str
    deployment_plan_id: str
    risk_policy_id: str
    roles: CapitalBindingRoles
    required_evidence: CapitalBindingRequiredEvidence
    controls: CapitalBindingControls
    approval: CapitalBindingApproval
    result: CapitalBindingResult
    schema_version: str | None = SCHEMA_VERSION
    packet_version: str | None = PART_C2_PACKET_VERSION

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapitalBindingLiveReadiness":
        payload = _mapping(data, "CapitalBindingLiveReadiness")
        packet = cls(
            readiness_id=_required_text(payload.get("readiness_id"), "readiness_id"),
            binding_id=_required_text(payload.get("binding_id"), "binding_id"),
            persona_id=_required_text(payload.get("persona_id"), "persona_id"),
            capital_pool_id=_required_text(payload.get("capital_pool_id"), "capital_pool_id"),
            artifact_id=_required_text(payload.get("artifact_id"), "artifact_id"),
            runtime_id=_required_text(payload.get("runtime_id"), "runtime_id"),
            deployment_plan_id=_required_text(payload.get("deployment_plan_id"), "deployment_plan_id"),
            risk_policy_id=_required_text(payload.get("risk_policy_id"), "risk_policy_id"),
            roles=CapitalBindingRoles.from_dict(payload.get("roles")),
            required_evidence=CapitalBindingRequiredEvidence.from_dict(payload.get("required_evidence")),
            controls=CapitalBindingControls.from_dict(payload.get("controls")),
            approval=CapitalBindingApproval.from_dict(payload.get("approval")),
            result=CapitalBindingResult.from_dict(payload.get("result")),
            schema_version=_optional_text(payload.get("schema_version")) or SCHEMA_VERSION,
            packet_version=_optional_text(payload.get("packet_version")) or PART_C2_PACKET_VERSION,
        )
        packet.validate_consistency()
        return packet

    @classmethod
    def from_json_file(cls, path: str | Path) -> "CapitalBindingLiveReadiness":
        with Path(path).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, Mapping):
            raise CapitalBindingLiveReadinessError("readiness document must be a JSON object")
        return cls.from_dict(data)

    def validate_consistency(self) -> None:
        approval_blockers = self.approval.blocking_reasons()
        result_blockers = set(self.result.blocking_reasons)

        if self.result.can_bind_live:
            if approval_blockers:
                raise CapitalBindingLiveReadinessError(
                    "can_bind_live requires approved risk_owner and operator approvals"
                )
            if result_blockers:
                raise CapitalBindingLiveReadinessError(
                    "can_bind_live cannot be true while blocking_reasons are present"
                )
        elif not result_blockers:
            raise CapitalBindingLiveReadinessError(
                "result.blocking_reasons must explain why can_bind_live is false"
            )
        else:
            missing_approval_blockers = sorted(set(approval_blockers) - result_blockers)
            if missing_approval_blockers:
                raise CapitalBindingLiveReadinessError(
                    "result.blocking_reasons must include approval blockers: "
                    + ", ".join(missing_approval_blockers)
                )

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "readiness_id": self.readiness_id,
                "binding_id": self.binding_id,
                "persona_id": self.persona_id,
                "capital_pool_id": self.capital_pool_id,
                "artifact_id": self.artifact_id,
                "runtime_id": self.runtime_id,
                "deployment_plan_id": self.deployment_plan_id,
                "risk_policy_id": self.risk_policy_id,
                "roles": self.roles.to_dict(),
                "required_evidence": self.required_evidence.to_dict(),
                "controls": self.controls.to_dict(),
                "approval": self.approval.to_dict(),
                "result": self.result.to_dict(),
                "schema_version": self.schema_version,
                "packet_version": self.packet_version,
            }
        )


def validate_readiness(packet: Mapping[str, Any] | CapitalBindingLiveReadiness) -> CapitalBindingLiveReadiness:
    """Validate a C2 readiness packet and return the normalized model."""

    if isinstance(packet, CapitalBindingLiveReadiness):
        packet.validate_consistency()
        return packet
    return CapitalBindingLiveReadiness.from_dict(packet)
