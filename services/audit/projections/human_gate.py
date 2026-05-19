"""Project human gate decision changes into foundation AuditAction records."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from services.foundation import (
    ActorRef,
    ActorType,
    AuditAction,
    EnvironmentName,
    EnvironmentScope,
    TraceContext,
    sha256_checksum,
)
from services.governance.human_gate.decision_model import HumanGateDecision

HUMAN_GATE_AUDIT_SCHEMA_VERSION = "HumanGateAuditProjection.v1"

ACTION_TYPES = {
    "submit": "human_gate_decision_submitted",
    "revoke": "human_gate_decision_revoked",
    "expire": "human_gate_decision_expired",
}

FINAL_STATUS_BY_CHANGE = {
    "revoke": "revoked",
    "expire": "expired",
}

DEFAULT_ACTOR = ActorRef(
    actor_type=ActorType.SYSTEM,
    actor_id="human-gate-audit-projection",
    roles=("audit_projection",),
)


class HumanGateAuditProjectionError(ValueError):
    """Raised when a human gate state change cannot be projected."""


DecisionInput = HumanGateDecision | Mapping[str, Any]


def project_human_gate_submit(
    decision: DecisionInput,
    *,
    trace_id: str,
    correlation_id: str,
    actor_ref: ActorRef | Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> AuditAction:
    """Project creation/submission of a human gate decision."""

    return project_human_gate_state_change(
        None,
        decision,
        change_type="submit",
        trace_id=trace_id,
        correlation_id=correlation_id,
        actor_ref=actor_ref,
        reason=reason,
    )


def project_human_gate_revoke(
    before: DecisionInput,
    after: DecisionInput,
    *,
    trace_id: str,
    correlation_id: str,
    actor_ref: ActorRef | Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> AuditAction:
    """Project a transition into the revoked human gate state."""

    return project_human_gate_state_change(
        before,
        after,
        change_type="revoke",
        trace_id=trace_id,
        correlation_id=correlation_id,
        actor_ref=actor_ref,
        reason=reason,
    )


def project_human_gate_expire(
    before: DecisionInput,
    after: DecisionInput,
    *,
    trace_id: str,
    correlation_id: str,
    actor_ref: ActorRef | Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> AuditAction:
    """Project a transition into the expired human gate state."""

    return project_human_gate_state_change(
        before,
        after,
        change_type="expire",
        trace_id=trace_id,
        correlation_id=correlation_id,
        actor_ref=actor_ref,
        reason=reason,
    )


def project_human_gate_state_change(
    before: DecisionInput | None,
    after: DecisionInput,
    *,
    trace_id: str,
    correlation_id: str,
    change_type: str | None = None,
    actor_ref: ActorRef | Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> AuditAction:
    """Project a submit/revoke/expire state change into an immutable audit record."""

    before_payload = _decision_payload(before) if before is not None else None
    after_payload = _decision_payload(after)
    normalized_change = _normalize_change_type(change_type, before_payload, after_payload)
    _validate_transition(normalized_change, before_payload, after_payload)

    actor = _actor_ref(actor_ref)
    environment = _environment_scope(after_payload)
    trace = TraceContext(
        trace_id=_required_text(trace_id, "trace_id"),
        correlation_id=_required_text(correlation_id, "correlation_id"),
        environment=environment,
        actor_ref=actor,
        source_system="pantheon.audit.human_gate_projection",
    )
    payload = _audit_payload(normalized_change, before_payload, after_payload)
    before_status = _optional_text(before_payload.get("status")) if before_payload else None
    after_status = _required_text(after_payload.get("status"), "after.status")

    return AuditAction.record(
        actor_ref=actor,
        action_type=ACTION_TYPES[normalized_change],
        target_ref=f"HumanGateDecision:{_decision_id(after_payload)}",
        environment=environment,
        reason=reason or _default_reason(normalized_change, after_payload),
        trace=trace,
        payload=payload,
        before_state_ref=_state_ref(before_payload) if before_payload else None,
        after_state_ref=_state_ref(after_payload),
        metadata={
            "projection_schema_version": HUMAN_GATE_AUDIT_SCHEMA_VERSION,
            "source_schema_version": after_payload.get("schema_version"),
            "source": "human_gate_decision",
            "change_type": normalized_change,
            "decision_id": _decision_id(after_payload),
            "target_type": _required_text(after_payload.get("target_type"), "after.target_type"),
            "target_id": _required_text(after_payload.get("target_id"), "after.target_id"),
            "target_environment": _required_text(
                after_payload.get("target_environment"),
                "after.target_environment",
            ),
            "before_status": before_status,
            "after_status": after_status,
        },
    )


def _decision_payload(decision: DecisionInput) -> dict[str, Any]:
    if isinstance(decision, HumanGateDecision):
        return decision.to_dict()
    if isinstance(decision, Mapping):
        return copy.deepcopy(dict(decision))
    raise HumanGateAuditProjectionError("decision must be a HumanGateDecision or mapping")


def _normalize_change_type(
    change_type: str | None,
    before_payload: Mapping[str, Any] | None,
    after_payload: Mapping[str, Any],
) -> str:
    if change_type is not None:
        normalized = str(change_type).strip().lower()
        if normalized not in ACTION_TYPES:
            allowed = ", ".join(sorted(ACTION_TYPES))
            raise HumanGateAuditProjectionError(f"change_type must be one of: {allowed}")
        return normalized

    after_status = _required_text(after_payload.get("status"), "after.status").lower()
    before_status = (
        _required_text(before_payload.get("status"), "before.status").lower()
        if before_payload is not None
        else None
    )
    if before_payload is None:
        return "submit"
    if before_status != "revoked" and after_status == "revoked":
        return "revoke"
    if before_status != "expired" and after_status == "expired":
        return "expire"
    raise HumanGateAuditProjectionError(
        "human gate state change must be submit, revoke, or expire"
    )


def _validate_transition(
    change_type: str,
    before_payload: Mapping[str, Any] | None,
    after_payload: Mapping[str, Any],
) -> None:
    _decision_id(after_payload)
    if before_payload is not None and _decision_id(before_payload) != _decision_id(after_payload):
        raise HumanGateAuditProjectionError("before and after decision_id must match")
    if change_type == "submit":
        if before_payload is not None:
            raise HumanGateAuditProjectionError("submit projection requires before=None")
        return

    if before_payload is None:
        raise HumanGateAuditProjectionError(f"{change_type} projection requires a before state")
    expected_status = FINAL_STATUS_BY_CHANGE[change_type]
    after_status = _required_text(after_payload.get("status"), "after.status").lower()
    before_status = _required_text(before_payload.get("status"), "before.status").lower()
    if after_status != expected_status:
        raise HumanGateAuditProjectionError(
            f"{change_type} projection requires after.status={expected_status}"
        )
    if before_status == after_status:
        raise HumanGateAuditProjectionError("before and after status must differ")


def _audit_payload(
    change_type: str,
    before_payload: Mapping[str, Any] | None,
    after_payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": HUMAN_GATE_AUDIT_SCHEMA_VERSION,
        "change_type": change_type,
        "decision_id": _decision_id(after_payload),
        "target": {
            "type": _required_text(after_payload.get("target_type"), "after.target_type"),
            "id": _required_text(after_payload.get("target_id"), "after.target_id"),
            "environment": _required_text(
                after_payload.get("target_environment"),
                "after.target_environment",
            ),
        },
        "before": copy.deepcopy(dict(before_payload)) if before_payload is not None else None,
        "after": copy.deepcopy(dict(after_payload)),
    }


def _state_ref(payload: Mapping[str, Any]) -> str:
    return f"HumanGateDecision:{_decision_id(payload)}@sha256:{sha256_checksum(payload)}"


def _decision_id(payload: Mapping[str, Any]) -> str:
    return _required_text(payload.get("decision_id"), "decision_id")


def _environment_scope(payload: Mapping[str, Any]) -> EnvironmentScope:
    raw_environment = _required_text(
        payload.get("target_environment"),
        "after.target_environment",
    ).lower()
    aliases = {
        "prod": "live",
        "production": "live",
    }
    environment = aliases.get(raw_environment, raw_environment)
    return EnvironmentScope(EnvironmentName(environment))


def _actor_ref(value: ActorRef | Mapping[str, Any] | None) -> ActorRef:
    if value is None:
        return DEFAULT_ACTOR
    if isinstance(value, ActorRef):
        return value
    if not isinstance(value, Mapping):
        raise HumanGateAuditProjectionError("actor_ref must be an ActorRef or mapping")
    return ActorRef(
        actor_type=value.get("actor_type", ActorType.USER),
        actor_id=_required_text(value.get("actor_id"), "actor_ref.actor_id"),
        display_name=_optional_text(value.get("display_name")),
        roles=tuple(str(role).strip() for role in value.get("roles", ()) if str(role).strip()),
        workspace_id=_optional_text(value.get("workspace_id")),
        persona_id=_optional_text(value.get("persona_id")),
        session_id=_optional_text(value.get("session_id")),
    )


def _default_reason(change_type: str, payload: Mapping[str, Any]) -> str:
    supplied_reason = _optional_text(payload.get("reason"))
    if supplied_reason:
        return supplied_reason
    if change_type == "submit":
        return "Human gate decision submitted."
    if change_type == "revoke":
        return "Human gate decision revoked."
    return "Human gate decision expired."


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise HumanGateAuditProjectionError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
