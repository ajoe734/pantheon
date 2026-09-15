"""Stateless command contracts, normalization, and foundation context building.

This module defines deterministic transformations and mappings for operator
commands, ensuring single-owner admission and projection contracts without
reverse dependencies on ``main.py``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, Optional

from pydantic import ValidationError

from services.foundation import (
    ActorRef,
    ActorType,
    AuditAction,
    AuthorityScope,
    CommandEnvelope,
    EnvironmentName,
    EnvironmentScope,
    IdempotencyRecord,
    PolicyDecision,
    PolicyDecisionValue,
    TraceContext,
)
try:
    from ..auth.policy import bff_error as _bff_error
    from ..models import (
        ApproveMutationCommandPayload,
        AuditContext,
        CommandType,
        ErrorCode,
        ExecuteMutationCommandPayload,
        ObjectType,
        OperatorCommand,
        OperatorIdentity,
        RecordSponsorDecisionCommandPayload,
        RejectMutationCommandPayload,
        ReviewMutationCommandPayload,
        TargetObject,
    )
except (ImportError, ValueError):
    from auth.policy import bff_error as _bff_error
    from models import (
        ApproveMutationCommandPayload,
        AuditContext,
        CommandType,
        ErrorCode,
        ExecuteMutationCommandPayload,
        ObjectType,
        OperatorCommand,
        OperatorIdentity,
        RecordSponsorDecisionCommandPayload,
        RejectMutationCommandPayload,
        ReviewMutationCommandPayload,
        TargetObject,
    )

_BFF_FOUNDATION_POLICY_VERSION = "2026-04-27"
_FINAL_COMMAND_ROUTE = "POST /bff/v1/commands"

_HUMAN_GATE_DECISIONS_BY_COMMAND: Dict[CommandType, str] = {
    CommandType.HUMAN_GATE_APPROVE: "approve",
    CommandType.HUMAN_GATE_REJECT: "reject",
    CommandType.HUMAN_GATE_REQUEST_MORE_EVIDENCE: "request_more_evidence",
    CommandType.HUMAN_GATE_REVOKE: "revoke",
    CommandType.HUMAN_GATE_EXTEND_TTL: "extend_ttl",
}


def stable_json_hash(payload: Any) -> str:
    """Compute a deterministic SHA-256 hash for JSON-serializable payloads."""
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except Exception:
        return hashlib.sha256(str(payload).encode("utf-8")).hexdigest()


compute_request_hash = stable_json_hash


def resolve_final_idempotency_key(
    idempotency_key: Optional[str] = None,
    x_idempotency_key: Optional[str] = None,
) -> str:
    """Prefer Idempotency-Key (RFC); accept X-Idempotency-Key as a compatibility alias."""
    canonical = str(idempotency_key or "").strip()
    if canonical:
        return canonical
    alias = str(x_idempotency_key or "").strip()
    if alias:
        return alias
    raise _bff_error(
        400,
        ErrorCode.VALIDATION_FAILED,
        "Idempotency-Key is required for operator commands",
        (
            "Final contract routes require a non-empty Idempotency-Key header; "
            "X-Idempotency-Key is accepted as a temporary compatibility alias"
        ),
        precondition_failed="idempotency_key",
        suggestion="Retry with Idempotency-Key set to a stable client retry key",
    )


def _human_gate_clean_text(value: Any) -> str:
    return str(value or "").strip()


def _human_gate_source_type(item_id: str) -> Optional[str]:
    prefix = item_id.split(":", 1)[0].strip().lower() if ":" in item_id else ""
    if prefix in {"approval", "intervention"}:
        return prefix
    return None


def normalize_human_gate_command(cmd: OperatorCommand) -> OperatorCommand:
    decision = _HUMAN_GATE_DECISIONS_BY_COMMAND.get(cmd.command)
    if decision is None:
        return cmd

    params = dict(cmd.params or {})
    item_id = str(cmd.target.id or "").strip()
    provided_item_ids = [
        _human_gate_clean_text(params.get(alias))
        for alias in ("human_gate_item_id", "humanGateItemId", "item_id", "itemId")
        if _human_gate_clean_text(params.get(alias))
    ]
    for provided_item_id in provided_item_ids:
        if provided_item_id != item_id:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "HumanGate params target id does not match the command target",
                "HUMAN_GATE_TARGET_MISMATCH",
                precondition_failed="human_gate_item_id",
                suggestion="Use target.id as the authoritative HumanGate item id",
                details_extra={
                    "targetId": item_id,
                    "providedHumanGateItemId": provided_item_id,
                },
            )
    params["human_gate_item_id"] = item_id
    params["humanGateItemId"] = item_id
    params["item_id"] = item_id
    params["itemId"] = item_id
    source_type = str(params.get("source_type") or params.get("sourceType") or "").strip()
    if not source_type:
        source_type = _human_gate_source_type(item_id) or ""
    if source_type:
        params["source_type"] = source_type
        params["sourceType"] = source_type
    params["decision"] = decision
    params["action_id"] = decision
    params["actionId"] = decision
    params.setdefault("audit_event", f"human_gate.{decision}")
    params.setdefault("auditEvent", f"human_gate.{decision}")
    params.setdefault("entity_type", "human_gate_item")
    params.setdefault("entity_id", item_id)
    cmd.params = params
    return cmd


def normalize_quarterly_recommendation_command(cmd: OperatorCommand) -> OperatorCommand:
    if cmd.command != CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT:
        return cmd

    params = dict(cmd.params or {})
    recommendation_id = str(
        params.get("recommendation_id")
        or params.get("recommendationId")
        or cmd.target.id
        or ""
    ).strip()
    target_recommendation_id = str(cmd.target.id or "").strip()
    if (
        recommendation_id
        and target_recommendation_id
        and recommendation_id != target_recommendation_id
    ):
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "recommendation_id does not match the command target",
            "Use target.id as the authoritative quarterly recommendation id.",
            precondition_failed="recommendation_id",
        )
    if recommendation_id:
        params["recommendation_id"] = recommendation_id
        params["recommendationId"] = recommendation_id

    recommendation_action_id = str(
        params.get("recommendation_action_id")
        or params.get("recommendationActionId")
        or params.get("actionId")
        or params.get("action_id")
        or ""
    ).strip()
    if recommendation_action_id and recommendation_action_id != "submit_recommendation":
        params["recommendation_action_id"] = recommendation_action_id
        params["recommendationActionId"] = recommendation_action_id

    params["action_id"] = "submit_recommendation"
    params["actionId"] = "submit_recommendation"
    params.setdefault("audit_event", "quarterly_ranking.recommendation_submitted")
    params.setdefault("auditEvent", "quarterly_ranking.recommendation_submitted")
    params.setdefault("entity_type", "quarterly_ranking_recommendation")
    params.setdefault("entity_id", recommendation_id or cmd.target.id)
    cmd.params = params
    return cmd


def normalize_b5_command_payload(cmd: OperatorCommand) -> OperatorCommand:
    return normalize_quarterly_recommendation_command(
        normalize_human_gate_command(cmd)
    )


def normalize_operator_command_payload(payload: Dict[str, Any]) -> OperatorCommand:
    command_type = payload.get("command_type")
    if command_type:
        try:
            if command_type == CommandType.APPROVE_MUTATION.value:
                mutation = ApproveMutationCommandPayload.model_validate(payload)
                note = str(mutation.note or "").strip() or None
                params: Dict[str, Any] = {"decision_id": mutation.decision_id}
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.APPROVE_MUTATION,
                    target=TargetObject(type=ObjectType.EVOLUTION_DECISION, id=mutation.decision_id),
                    action="approve_mutation",
                    params=params,
                    audit_context=AuditContext(reason=note or mutation.command_type),
                )
            if command_type == CommandType.REJECT_MUTATION.value:
                mutation = RejectMutationCommandPayload.model_validate(payload)
                note = str(mutation.note or "").strip() or None
                params = {"decision_id": mutation.decision_id}
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.REJECT_MUTATION,
                    target=TargetObject(type=ObjectType.EVOLUTION_DECISION, id=mutation.decision_id),
                    action="reject_mutation",
                    params=params,
                    audit_context=AuditContext(reason=note or mutation.command_type),
                )
            if command_type == CommandType.REVIEW_MUTATION.value:
                mutation = ReviewMutationCommandPayload.model_validate(payload)
                note = str(mutation.note or "").strip() or None
                params = {
                    "decision_id": mutation.decision_id,
                    "approval_decision_id": mutation.approval_decision_id,
                }
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.REVIEW_MUTATION,
                    target=TargetObject(type=ObjectType.EVOLUTION_DECISION, id=mutation.decision_id),
                    action="review_mutation",
                    params=params,
                    audit_context=AuditContext(reason=note or mutation.command_type),
                )
            if command_type == CommandType.EXECUTE_MUTATION.value:
                mutation = ExecuteMutationCommandPayload.model_validate(payload)
                note = str(mutation.note or "").strip() or None
                params = {
                    "decision_id": mutation.decision_id,
                    "has_active_runtime": mutation.has_active_runtime,
                    "freeze_mode": mutation.freeze_mode,
                    "force_stage_freeze": mutation.force_stage_freeze,
                }
                if mutation.active_binding_id:
                    params["active_binding_id"] = mutation.active_binding_id
                if mutation.rollback_action_type:
                    params["rollback_action_type"] = mutation.rollback_action_type
                if mutation.fallback_artifact_id:
                    params["fallback_artifact_id"] = mutation.fallback_artifact_id
                if mutation.fallback_artifact_version:
                    params["fallback_artifact_version"] = mutation.fallback_artifact_version
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.EXECUTE_MUTATION,
                    target=TargetObject(type=ObjectType.EVOLUTION_DECISION, id=mutation.decision_id),
                    action="execute_mutation",
                    params=params,
                    audit_context=AuditContext(reason=note or mutation.command_type),
                )
            if command_type == CommandType.RECORD_SPONSOR_DECISION.value:
                decision = RecordSponsorDecisionCommandPayload.model_validate(payload)
                note = str(decision.note or "").strip() or None
                params = {
                    "committee_id": decision.committee_id,
                    "sponsor_decision": decision.sponsor_decision,
                    "rationale_ref": decision.rationale_ref,
                }
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.RECORD_SPONSOR_DECISION,
                    target=TargetObject(type=ObjectType.COMMITTEE_BOARD, id=decision.committee_id),
                    action="record_sponsor_decision",
                    params=params,
                    audit_context=AuditContext(reason=note or decision.command_type),
                )
        except ValidationError as exc:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"Invalid {command_type} payload",
                str(exc),
            ) from exc
        raise _bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "Unknown command_type",
            f"Unsupported command_type: {command_type}",
        )

    try:
        return normalize_b5_command_payload(OperatorCommand.model_validate(payload))
    except ValidationError as exc:
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid operator command payload",
            str(exc),
        ) from exc


def foundation_environment_scope() -> EnvironmentScope:
    raw = os.getenv("PANTHEON_ENV", "dev").strip().lower()
    if "live" in raw:
        name = EnvironmentName.LIVE
    elif "canary" in raw:
        name = EnvironmentName.CANARY
    elif "paper" in raw:
        name = EnvironmentName.PAPER
    elif "sandbox" in raw:
        name = EnvironmentName.SANDBOX
    else:
        name = EnvironmentName.DEV
    return EnvironmentScope(
        name=name,
        region=os.getenv("PANTHEON_REGION") or None,
        timezone=os.getenv("PANTHEON_TIMEZONE", "UTC"),
    )


def foundation_actor_ref(identity: OperatorIdentity) -> ActorRef:
    return ActorRef(
        actor_type=ActorType.USER,
        actor_id=identity.operator_id,
        roles=identity.roles,
    )


def foundation_route_metadata(route: str, source_route: Optional[str] = None) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"route": route}
    if source_route:
        metadata["source_route"] = source_route
    return metadata


def foundation_request_payload(
    cmd: OperatorCommand,
    raw_payload: Dict[str, Any],
    *,
    route: str = _FINAL_COMMAND_ROUTE,
    source_route: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "route": route,
        "command": cmd.command.value,
        "target": cmd.target.model_dump(),
        "params": dict(cmd.params),
        "audit_context": cmd.audit_context.model_dump(),
        "raw_payload": raw_payload,
    }
    if source_route:
        payload["source_route"] = source_route
    return payload


def foundation_idempotency_payload(request_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.loads(json.dumps(request_payload))
    payload.pop("route", None)
    payload.pop("source_route", None)
    audit_context = payload.get("audit_context")
    if isinstance(audit_context, dict):
        audit_context.pop("timestamp", None)
    raw_payload = payload.get("raw_payload")
    if isinstance(raw_payload, dict):
        raw_audit_context = raw_payload.get("audit_context")
        if isinstance(raw_audit_context, dict):
            raw_audit_context.pop("timestamp", None)
    return payload


def build_foundation_trace(
    *,
    environment: EnvironmentScope,
    actor_ref: ActorRef,
    trace_id: Optional[str],
    correlation_id: Optional[str],
    request_id: Optional[str],
    idempotency_key: Optional[str],
) -> TraceContext:
    clean_trace_id = str(trace_id or "").strip()
    if clean_trace_id:
        return TraceContext(
            trace_id=clean_trace_id,
            correlation_id=str(correlation_id or clean_trace_id).strip(),
            environment=environment,
            actor_ref=actor_ref,
            source_system="pantheon-bff",
            request_id=str(request_id or "").strip() or None,
            idempotency_key=str(idempotency_key or "").strip() or None,
        )
    return TraceContext.new(
        environment=environment,
        actor_ref=actor_ref,
        source_system="pantheon-bff",
        correlation_id=str(correlation_id or "").strip() or None,
        request_id=str(request_id or "").strip() or None,
        idempotency_key=str(idempotency_key or "").strip() or None,
    )


def build_foundation_command_context(
    *,
    cmd: OperatorCommand,
    identity: OperatorIdentity,
    raw_payload: Dict[str, Any],
    trace_id: Optional[str],
    correlation_id: Optional[str],
    request_id: Optional[str],
    idempotency_key: Optional[str],
    route: str = _FINAL_COMMAND_ROUTE,
    source_route: Optional[str] = None,
) -> Dict[str, Any]:
    environment = foundation_environment_scope()
    actor_ref = foundation_actor_ref(identity)
    route_metadata = foundation_route_metadata(route, source_route)
    authority_scope = AuthorityScope(
        action=cmd.command.value,
        target_type=cmd.target.type.value,
        target_id=cmd.target.id,
        environment=environment,
        runtime_id=cmd.target.id if cmd.target.type == ObjectType.RUNTIME else None,
        attributes=route_metadata,
    )
    req_payload = foundation_request_payload(
        cmd,
        raw_payload,
        route=route,
        source_route=source_route,
    )
    trace = build_foundation_trace(
        environment=environment,
        actor_ref=actor_ref,
        trace_id=trace_id,
        correlation_id=correlation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    command_envelope = CommandEnvelope.new(
        command_type=cmd.command.value,
        actor_ref=actor_ref,
        authority_scope=authority_scope,
        payload=req_payload,
        trace=trace,
        idempotency_key=str(idempotency_key or "").strip() or None,
    )
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=command_envelope.idempotency_key,
        operation_type=f"bff.{cmd.command.value}",
        target_ref=authority_scope.target_ref,
        request_payload=foundation_idempotency_payload(req_payload),
        trace_id=command_envelope.trace.trace_id,
    )
    policy_decision = PolicyDecision.make(
        policy_id="bff.command.admission",
        policy_version=_BFF_FOUNDATION_POLICY_VERSION,
        decision=PolicyDecisionValue.ALLOW,
        actor_ref=actor_ref,
        action=cmd.command.value,
        target_ref=authority_scope.target_ref,
        environment=environment,
        trace_id=command_envelope.trace.trace_id,
    )
    audit_action = AuditAction.record(
        actor_ref=actor_ref,
        action_type="bff.command.accepted",
        target_ref=authority_scope.target_ref,
        environment=environment,
        reason=cmd.audit_context.reason or "operator command admission",
        trace=command_envelope.trace,
        payload=req_payload,
        policy_decision_ref=policy_decision.decision_id,
        metadata=route_metadata,
    )
    return {
        "admission_route": route,
        "source_route": source_route,
        "command_envelope": command_envelope,
        "trace_context": command_envelope.trace,
        "idempotency_record": idempotency_record,
        "policy_decision": policy_decision,
        "audit_action": audit_action,
        "request_payload": req_payload,
    }


def serialize_foundation_context(context: Dict[str, Any]) -> Dict[str, Any]:
    serialized = {
        "admission_route": context.get("admission_route"),
        "trace_context": context["trace_context"].to_dict(),
        "command_envelope": context["command_envelope"].to_dict(),
        "idempotency_record": context["idempotency_record"].to_dict(),
        "policy_decision": context["policy_decision"].to_dict(),
        "audit_action": context["audit_action"].to_dict(),
    }
    if context.get("source_route"):
        serialized["source_route"] = context.get("source_route")
    return serialized
