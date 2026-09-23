"""Command preconditions, target binding, and confirmation token validation.

This module encapsulates validation rules, authority bindings, and confirmation
checks for operator commands.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException

try:
    from ..action_catalog import get_catalog_entry
    from ..auth.policy import (
        bff_error as _auth_bff_error,
        bff_me_tenant_payload as _auth_bff_me_tenant_payload,
        require_admin_mfa as _auth_require_admin_mfa,
    )
    from ..models import (
        CommandStatus,
        CommandType,
        ErrorCode,
        ObjectType,
        OperatorCommand,
        OperatorIdentity,
    )
except (ImportError, ValueError):
    from action_catalog import get_catalog_entry
    from auth.policy import (
        bff_error as _auth_bff_error,
        bff_me_tenant_payload as _auth_bff_me_tenant_payload,
        require_admin_mfa as _auth_require_admin_mfa,
    )
    from models import (
        CommandStatus,
        CommandType,
        ErrorCode,
        ObjectType,
        OperatorCommand,
        OperatorIdentity,
    )
from .contracts import (
    _HUMAN_GATE_DECISIONS_BY_COMMAND,
    _human_gate_clean_text,
    _human_gate_source_type,
    stable_json_hash,
)
from .receipts import foundation_idempotency_conflict_error

_CONFIRM_TOKEN_FIELDS = (
    "confirmToken",
    "confirm_token",
    "confirmationToken",
    "confirmation_token",
)
_APPROVAL_EVIDENCE_FIELDS = (
    "approvalId",
    "approval_id",
    "approvalDecisionId",
    "approval_decision_id",
)
_TWO_MAN_EVIDENCE_FIELDS = (
    "twoManSignatureId",
    "two_man_signature_id",
    "twoManApprovalId",
    "two_man_approval_id",
    "secondOperatorId",
    "second_operator_id",
    "secondOperatorSignature",
    "second_operator_signature",
)
_TWO_MAN_SIGNATURE_ID_FIELDS = (
    "twoManSignatureId",
    "two_man_signature_id",
    "twoManApprovalId",
    "two_man_approval_id",
    "signature_id",
    "signatureId",
    "id",
)
_TWO_MAN_SIGNER_LIST_FIELDS = (
    "signer_operator_ids",
    "signerOperatorIds",
    "operator_ids",
    "operatorIds",
)
_TWO_MAN_SIGNER_FIELDS = (
    "first_operator_id",
    "firstOperatorId",
    "primary_operator_id",
    "primaryOperatorId",
    "second_operator_id",
    "secondOperatorId",
    "secondOperatorSignature",
    "second_operator_signature",
    "signed_by",
    "signedBy",
    "confirmed_by",
    "confirmedBy",
)

_COMMAND_BINDING_FIELDS = (
    "command",
    "command_type",
    "commandType",
    "action_id",
    "actionId",
)
_TARGET_TYPE_BINDING_FIELDS = (
    "target_type",
    "targetType",
    "entity_type",
    "entityType",
    "object_type",
    "objectType",
)
_TARGET_ID_BINDING_FIELDS = (
    "target_id",
    "targetId",
    "entity_id",
    "entityId",
    "object_id",
    "objectId",
    "runtime_id",
    "runtimeId",
    "intervention_id",
    "interventionId",
)
_CALLER_BINDING_FIELDS = (
    "operator_id",
    "operatorId",
    "caller_operator_id",
    "callerOperatorId",
    "issued_for_operator_id",
    "issuedForOperatorId",
    "issued_for",
    "issuedFor",
    "actor_id",
    "actorId",
)

_HUMAN_GATE_DEFAULT_MAX_TTL_SECONDS = 604800
_HUMAN_GATE_APPROVER_DECISIONS = {"approve", "reject", "revoke", "extend_ttl"}
_HUMAN_GATE_SELF_APPROVAL_DECISIONS = {"approve", "reject", "revoke"}
_HUMAN_GATE_HIGH_RISK_LEVELS = {"high", "critical"}
_HUMAN_GATE_REQUESTER_FIELDS = (
    "requester_id",
    "requesterId",
    "requested_by",
    "requestedBy",
    "submitted_by",
    "submittedBy",
    "created_by",
    "createdBy",
    "created_by_id",
)
_HUMAN_GATE_SOURCE_ID_FIELDS = (
    "source_id",
    "sourceId",
    "decision_id",
    "decisionId",
    "intervention_id",
    "interventionId",
    "approval_id",
    "approvalId",
)
_HUMAN_GATE_RISK_FIELDS = (
    "risk_level",
    "riskLevel",
    "risk",
    "severity",
    "priority",
)
_HUMAN_GATE_DOWNSTREAM_EXECUTED_STATES = {
    "executed",
    "applied",
    "committed",
    "completed",
    "success",
}
_HUMAN_GATE_DOWNSTREAM_EXECUTED_FIELDS = (
    "downstream_effect_status",
    "downstreamEffectStatus",
    "downstream_status",
    "downstreamStatus",
    "execution_status",
    "executionStatus",
    "effect_status",
    "effectStatus",
    "result_status",
    "resultStatus",
)
_HUMAN_GATE_DOWNSTREAM_EXECUTED_AT_FIELDS = (
    "downstream_executed_at",
    "downstreamExecutedAt",
    "executed_at",
    "executedAt",
    "applied_at",
    "appliedAt",
    "committed_at",
    "committedAt",
)

_DRAWER_RUNTIME_COMMANDS = {
    CommandType.PAUSE_EXECUTION,
    CommandType.ISSUE_RISK_OFF,
    CommandType.LIQUIDATE_ALL,
    CommandType.HARD_ROLLBACK,
    CommandType.ISSUE_SAFE_MODE,
}
_LIVE_BROKER_SIGNAL_KEYS = {
    "account-mode",
    "account-type",
    "broker-mode",
    "broker-scope",
    "deployment-scope",
    "deployment-stage",
    "environment",
    "execution-mode",
    "order-mode",
    "runtime-mode",
    "scope",
    "target-env",
    "target-environment",
    "target-stage",
    "venue-mode",
}
_LIVE_BROKER_SIGNAL_VALUES = {
    "ibkr-live",
    "interactive-brokers-live",
    "live",
    "live-broker",
    "prod",
    "production",
}

_FINAL_COMMAND_TARGET_TYPES: Dict[CommandType, ObjectType] = {
    CommandType.APPROVED_APPLY: ObjectType.REBALANCE,
    CommandType.HUMAN_GATE_APPROVE: ObjectType.HUMAN_GATE_ITEM,
    CommandType.HUMAN_GATE_REJECT: ObjectType.HUMAN_GATE_ITEM,
    CommandType.HUMAN_GATE_REQUEST_MORE_EVIDENCE: ObjectType.HUMAN_GATE_ITEM,
    CommandType.HUMAN_GATE_REVOKE: ObjectType.HUMAN_GATE_ITEM,
    CommandType.HUMAN_GATE_EXTEND_TTL: ObjectType.HUMAN_GATE_ITEM,
    CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT: ObjectType.RANKING,
    CommandType.PAUSE_PAPER_RUNTIME: ObjectType.RUNTIME,
    CommandType.RESUME_PAPER_RUNTIME: ObjectType.RUNTIME,
}

_SERVER_MANAGED_REBALANCE_EVIDENCE_TYPES = {
    CommandType.REBALANCE_APPROVAL,
    CommandType.REBALANCE_TWO_MAN_SIGN,
}
_REBALANCE_EVIDENCE_PRODUCER = "bff.rebalance-evidence.v1"
_V5_TWO_MAN_EVIDENCE_PRODUCER = "bff.v5-two-man-evidence.v1"
_PPL_ALLOC_009_PAPER_AUTHORITY_MODE = "paper_simulation_v5"
_RETRYABLE_CAPITAL_COMMAND_TYPES = {CommandType.APPROVED_APPLY}


def reject_body_idempotency_key(payload: Optional[Dict[str, Any]]) -> None:
    if not isinstance(payload, dict):
        return
    for bad_key in ("idempotency_key", "idempotencyKey", "Idempotency-Key", "X-Idempotency-Key"):
        if bad_key in payload:
            raise _auth_bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                f"{bad_key} must not appear in the request body",
                (
                    "Final contract routes require idempotency via the Idempotency-Key header, "
                    "not the request body"
                ),
                precondition_failed="body_idempotency_key",
                suggestion=f"Remove {bad_key} from the body and set the Idempotency-Key header",
            )


def validate_audit_context(cmd: OperatorCommand) -> None:
    if str(cmd.audit_context.reason or "").strip():
        return
    raise _auth_bff_error(
        400,
        ErrorCode.VALIDATION_FAILED,
        "audit_context.reason is required",
        "audit_context.reason must be a non-empty string",
    )


def validate_capital_authority_target_binding(cmd: OperatorCommand) -> None:
    if cmd.command == CommandType.APPROVED_APPLY:
        aliases = ("rebalance_id", "rebalanceId")
        label = "rebalance"
    elif cmd.command == CommandType.EMERGENCY_CONTAINMENT:
        if cmd.target.type != ObjectType.PERSONA:
            raise _auth_bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "EmergencyContainment must target a Persona",
                "Capital containment authority mutates the Persona identified by command target.id",
                precondition_failed="capital_target_type",
            )
        aliases = ("persona_id", "personaId")
        label = "persona"
    else:
        return
    supplied = {
        str(cmd.params.get(alias) or "").strip()
        for alias in aliases
        if str(cmd.params.get(alias) or "").strip()
    }
    if supplied and supplied != {cmd.target.id}:
        raise _auth_bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{label}_id must match command target.id",
            (
                f"Capital owner command targets {cmd.target.id!r}, but params supplied "
                f"{sorted(supplied)!r}"
            ),
            precondition_failed="capital_target_id_mismatch",
        )


def validate_paper_runtime_authority_target_binding(cmd: OperatorCommand) -> None:
    if cmd.command not in {CommandType.PAUSE_PAPER_RUNTIME, CommandType.RESUME_PAPER_RUNTIME}:
        return
    if cmd.target.type != ObjectType.RUNTIME:
        raise _auth_bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{cmd.command.value} requires target.type = Runtime",
            "Canonical paper commands only accept Runtime targets",
            precondition_failed="target.type",
        )
    target_id = str(cmd.target.id or "").strip()
    if not target_id:
        raise _auth_bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{cmd.command.value} requires a non-empty runtime target id",
            "target.id must be a non-empty runtime id",
            precondition_failed="target.id",
        )
    aliases = ("runtime_id", "runtimeId", "entity_id", "entityId")
    supplied = {
        str(cmd.params.get(alias) or "").strip()
        for alias in aliases
        if str(cmd.params.get(alias) or "").strip()
    }
    if supplied and supplied != {target_id}:
        raise _auth_bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"runtime_id must match command target.id for {cmd.command.value}",
            (
                f"Canonical paper command targets {target_id!r}, but params supplied "
                f"{sorted(supplied)!r}"
            ),
            precondition_failed="target_redirection_detected",
        )
    cmd.params.pop("verified_binding", None)
    cmd.params.pop("verified_binding_id", None)
    cmd.params.pop("verified_runtime_binding_id", None)
    cmd.params["runtime_id"] = target_id
    cmd.params["entity_id"] = target_id


def _env_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def _value_contains_live_broker_signal(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_value_contains_live_broker_signal(child) for child in value.values())
    if isinstance(value, list):
        return any(_value_contains_live_broker_signal(child) for child in value)
    token = _env_token(value)
    if token in _LIVE_BROKER_SIGNAL_VALUES:
        return True
    return bool(
        re.search(r"(^|-)live($|-)", token)
        and ("broker" in token or "ibkr" in token or "interactive-brokers" in token)
    )


def _payload_has_live_broker_signal(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            key_token = _env_token(key)
            if key_token in _LIVE_BROKER_SIGNAL_KEYS and _value_contains_live_broker_signal(child):
                return True
            if _payload_has_live_broker_signal(child):
                return True
    elif isinstance(value, list):
        return any(_payload_has_live_broker_signal(child) for child in value)
    return False


def _command_targets_live_runtime(cmd: OperatorCommand) -> bool:
    if cmd.target.type != ObjectType.RUNTIME:
        return False
    target_id = _env_token(cmd.target.id)
    return bool(re.search(r"(^|-)live($|-)", target_id))


def ensure_live_broker_scope_allowed(cmd: OperatorCommand, payload: Dict[str, Any]) -> None:
    raw_enabled = os.getenv("PANTHEON_LIVE_BROKER_ENABLED", "false").strip().lower()
    if raw_enabled in {"1", "true", "yes", "on"}:
        return
    if not (_command_targets_live_runtime(cmd) or _payload_has_live_broker_signal(payload)):
        return
    env_name = os.getenv("PANTHEON_ENV", "dev").strip() or "dev"
    raise _auth_bff_error(
        403,
        ErrorCode.PRECONDITION_FAILED,
        "Live broker scope is disabled for this BFF",
        f"PANTHEON_ENV={env_name} has PANTHEON_LIVE_BROKER_ENABLED=false",
        precondition_failed="live_broker_scope",
        suggestion=(
            "Use the staging-live BFF only after operator auth, governance, "
            "runtime kill-switch, and broker rehearsal gates are verified"
        ),
    )


def validate_drawer_runtime_target(cmd: OperatorCommand) -> None:
    if cmd.command not in _DRAWER_RUNTIME_COMMANDS:
        return
    if cmd.target.type != ObjectType.RUNTIME:
        raise _auth_bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{cmd.command.value} requires target.type = Runtime",
            "Drawer commands only accept Runtime targets",
        )
    if not str(cmd.target.id or "").strip():
        raise _auth_bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{cmd.command.value} requires a runtime target id",
            "target.id must be a non-empty runtime id",
        )


def validate_final_command_target_type(cmd: OperatorCommand) -> None:
    expected = _FINAL_COMMAND_TARGET_TYPES.get(cmd.command)
    if expected is None or cmd.target.type == expected:
        return
    raise _auth_bff_error(
        422,
        ErrorCode.VALIDATION_FAILED,
        "Invalid command target type",
        f"{cmd.command.value} must target {expected.value}, not {cmd.target.type.value}",
        precondition_failed="target.type",
        suggestion=f"Use target.type={expected.value} for {cmd.command.value}",
    )


def reject_server_managed_rebalance_evidence_command(cmd: OperatorCommand) -> None:
    if cmd.command not in _SERVER_MANAGED_REBALANCE_EVIDENCE_TYPES:
        return
    raise _auth_bff_error(
        403,
        ErrorCode.FORBIDDEN,
        "Rebalance evidence commands are server-managed",
        (
            f"{cmd.command.value} can only be produced by the dedicated "
            "authenticated rebalance evidence routes"
        ),
        precondition_failed="trusted_evidence_producer",
        suggestion=(
            "Use POST /bff/rebalances/{id}/approve or "
            "POST /bff/rebalances/{id}/two-man-sign"
        ),
    )


def canonicalize_validated_precondition_evidence(
    stored_params: Dict[str, Any],
    evidence: Dict[str, str],
) -> None:
    confirm_token_id = evidence.get("confirm_token_id")
    if confirm_token_id:
        for alias in (*_CONFIRM_TOKEN_FIELDS, "confirm_token_id"):
            stored_params.pop(alias, None)
        stored_params["confirm_token_id"] = confirm_token_id

    approval_decision_id = evidence.get("approval_decision_id")
    if approval_decision_id:
        for alias in (*_APPROVAL_EVIDENCE_FIELDS, "approval_ref"):
            stored_params.pop(alias, None)
        stored_params["approval_decision_id"] = approval_decision_id
        stored_params["approval_ref"] = approval_decision_id

    signature_id = evidence.get("two_man_signature_id")
    if signature_id:
        for alias in _TWO_MAN_EVIDENCE_FIELDS:
            stored_params.pop(alias, None)
        stored_params["two_man_signature_id"] = signature_id


def retryable_terminal_capital_command(record: Dict[str, Any]) -> bool:
    return bool(
        record.get("type") in _RETRYABLE_CAPITAL_COMMAND_TYPES
        and record.get("status") in {CommandStatus.FAILED.value, CommandStatus.TIMEOUT.value}
        and isinstance(record.get("error"), dict)
        and record["error"].get("retryable") is True
    )


def _precondition_value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _precondition_value(
    payload: Dict[str, Any],
    params: Dict[str, Any],
    aliases: tuple[str, ...],
    *extra_values: Any,
) -> Optional[str]:
    for value in extra_values:
        if _precondition_value_present(value):
            return str(value).strip()
    for source in (payload, params):
        for alias in aliases:
            if alias in source and _precondition_value_present(source.get(alias)):
                return str(source.get(alias)).strip()
    return None


def _binding_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _dict_first_present(mapping: Dict[str, Any], aliases: tuple[str, ...]) -> Optional[Any]:
    for alias in aliases:
        if alias in mapping and _precondition_value_present(mapping.get(alias)):
            return mapping.get(alias)
    return None


def _record_audit(record: Dict[str, Any]) -> Dict[str, Any]:
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    return audit


def _record_params(record: Dict[str, Any]) -> Dict[str, Any]:
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    return params


def _record_actor_id(record: Dict[str, Any]) -> Optional[str]:
    audit = _record_audit(record)
    for key in ("operator_id", "actor", "actor_id", "confirmed_by"):
        value = str(audit.get(key) or "").strip()
        if value:
            return value
    foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
    trace = foundation.get("trace_context") if isinstance(foundation.get("trace_context"), dict) else {}
    actor_ref = trace.get("actor_ref") if isinstance(trace.get("actor_ref"), dict) else {}
    value = str(actor_ref.get("actor_id") or "").strip()
    return value or None


def _binding_sources(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    params = _record_params(record)
    audit = _record_audit(record)
    sources: List[Dict[str, Any]] = [params, audit]
    for source in (params, audit):
        target = source.get("target")
        if isinstance(target, dict):
            sources.append(target)
        preconditions = source.get("preconditions") or source.get("precondition_evidence")
        if isinstance(preconditions, dict):
            sources.append(preconditions)
    foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
    command_envelope = foundation.get("command_envelope") if isinstance(foundation.get("command_envelope"), dict) else {}
    payload = command_envelope.get("payload") if isinstance(command_envelope.get("payload"), dict) else {}
    if payload:
        sources.append(payload)
        target = payload.get("target")
        if isinstance(target, dict):
            sources.append(target)
    return sources


def _binding_has_command(record: Dict[str, Any]) -> bool:
    return any(_dict_first_present(source, _COMMAND_BINDING_FIELDS) is not None for source in _binding_sources(record))


def _binding_command_matches(record: Dict[str, Any], cmd: OperatorCommand) -> bool:
    values = [
        str(_dict_first_present(source, _COMMAND_BINDING_FIELDS) or "").strip()
        for source in _binding_sources(record)
    ]
    values = [value for value in values if value]
    if not values:
        return False
    expected = _binding_token(cmd.command.value)
    return any(_binding_token(value) == expected for value in values)


def _binding_target_values(record: Dict[str, Any]) -> tuple[List[str], List[str]]:
    target_types: List[str] = []
    target_ids: List[str] = []
    for source in _binding_sources(record):
        target = source.get("target") if isinstance(source.get("target"), dict) else None
        if target is not None:
            target_type = str(target.get("type") or "").strip()
            target_id = str(target.get("id") or "").strip()
            if target_type:
                target_types.append(target_type)
            if target_id:
                target_ids.append(target_id)
        target_type = _dict_first_present(source, _TARGET_TYPE_BINDING_FIELDS)
        target_id = _dict_first_present(source, _TARGET_ID_BINDING_FIELDS)
        if target_type is not None:
            target_types.append(str(target_type).strip())
        if target_id is not None:
            target_ids.append(str(target_id).strip())
    return [value for value in target_types if value], [value for value in target_ids if value]


def _binding_has_target(record: Dict[str, Any]) -> bool:
    target_types, target_ids = _binding_target_values(record)
    return bool(target_types or target_ids)


def _binding_target_matches(record: Dict[str, Any], cmd: OperatorCommand) -> bool:
    target_types, target_ids = _binding_target_values(record)
    if not target_types and not target_ids:
        return False
    type_ok = not target_types or any(
        _binding_token(value) == _binding_token(cmd.target.type.value)
        for value in target_types
    )
    id_ok = not target_ids or any(str(value) == cmd.target.id for value in target_ids)
    return type_ok and id_ok


def _record_bound_to_command_and_target(record: Dict[str, Any], cmd: OperatorCommand) -> bool:
    return (
        _binding_has_command(record)
        and _binding_has_target(record)
        and _binding_command_matches(record, cmd)
        and _binding_target_matches(record, cmd)
    )


def _record_bound_to_caller(record: Dict[str, Any], identity: OperatorIdentity) -> bool:
    bound_values: List[str] = []
    for source in _binding_sources(record):
        value = _dict_first_present(source, _CALLER_BINDING_FIELDS)
        if value is not None:
            bound_values.append(str(value).strip())
    if bound_values:
        return any(value == identity.operator_id for value in bound_values)
    return _record_actor_id(record) == identity.operator_id


def assert_duplicate_confirm_token_matches(
    *,
    duplicate: Dict[str, Any],
    cmd: OperatorCommand,
    payload: Dict[str, Any],
    confirm_token: Optional[str],
    foundation_context: Dict[str, Any],
) -> None:
    audit = duplicate.get("audit") if isinstance(duplicate.get("audit"), dict) else {}
    evidence = audit.get("precondition_evidence") if isinstance(audit.get("precondition_evidence"), dict) else {}
    stored_params = duplicate.get("params") if isinstance(duplicate.get("params"), dict) else {}
    stored_token_id = str(
        evidence.get("confirm_token_id")
        or stored_params.get("confirm_token_id")
        or ""
    ).strip()
    if not stored_token_id:
        return
    supplied_token_id = _precondition_value(
        payload,
        dict(cmd.params),
        _CONFIRM_TOKEN_FIELDS,
        confirm_token,
    )
    if supplied_token_id == stored_token_id:
        return
    raise foundation_idempotency_conflict_error(
        foundation_context=foundation_context,
        existing_command_id=str(duplicate.get("command_id") or ""),
    )


def _final_precondition_details(
    *,
    cmd: OperatorCommand,
    kind: str,
) -> Dict[str, Any]:
    return {
        "actionId": cmd.command.value,
        "entityType": cmd.target.type.value,
        "entityId": cmd.target.id,
        "kind": kind,
    }


def _final_precondition_error(
    *,
    cmd: OperatorCommand,
    status_code: int,
    code: ErrorCode,
    message: str,
    reason: str,
    kind: str,
    correlation_id: Optional[str],
    suggestion: str,
    details_extra: Optional[Dict[str, Any]] = None,
) -> HTTPException:
    return _auth_bff_error(
        status_code=status_code,
        code=code,
        message=message,
        reason=reason,
        precondition_failed=kind,
        suggestion=suggestion,
        details_extra={
            **_final_precondition_details(cmd=cmd, kind=kind),
            **(details_extra or {}),
        },
        correlation_id=correlation_id,
    )


def require_final_command_confirm_token(
    *,
    cmd: OperatorCommand,
    payload: Dict[str, Any],
    confirm_token: Optional[str],
    identity: OperatorIdentity,
    correlation_id: Optional[str],
    confirm_token_records_fn: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
    confirm_token_lifecycle_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> Optional[str]:
    entry = get_catalog_entry(cmd.command.value)
    if entry is None or not getattr(entry, "requires_confirm_token", False):
        return None

    params = dict(cmd.params)
    token_id = _precondition_value(payload, params, _CONFIRM_TOKEN_FIELDS, confirm_token)
    if not token_id:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=428,
            code=ErrorCode.CONFIRMATION_REQUIRED,
            message="Confirmation token is required before this action can be accepted",
            reason="CONFIRM_TOKEN_MISSING",
            kind="confirm_token",
            correlation_id=correlation_id,
            suggestion="Retry with X-Confirm-Token or confirmToken after the operator confirmation step",
        )
    token_records = confirm_token_records_fn(token_id) if confirm_token_records_fn else []
    create_record = next(
        (
            record
            for record in reversed(token_records)
            if record.get("type") == CommandType.CONFIRM_TOKEN_CREATE.value
        ),
        None,
    )
    token_state = confirm_token_lifecycle_fn(token_id) if confirm_token_lifecycle_fn else {"status": "created"}
    if create_record is None or token_state.get("status") != "created":
        raise _final_precondition_error(
            cmd=cmd,
            status_code=428,
            code=ErrorCode.CONFIRMATION_REQUIRED,
            message="Confirmation token is not valid for this command",
            reason="CONFIRM_TOKEN_INVALID",
            kind="confirm_token",
            correlation_id=correlation_id,
            suggestion="Issue a fresh confirm token bound to this command, target, and operator",
            details_extra={"confirmToken": token_id, "tokenStatus": token_state.get("status")},
        )
    if not _record_bound_to_command_and_target(create_record, cmd):
        raise _final_precondition_error(
            cmd=cmd,
            status_code=428,
            code=ErrorCode.CONFIRMATION_REQUIRED,
            message="Confirmation token is not bound to this command target",
            reason="CONFIRM_TOKEN_BINDING_MISMATCH",
            kind="confirm_token",
            correlation_id=correlation_id,
            suggestion="Issue a confirm token for the exact command and target being submitted",
            details_extra={"confirmToken": token_id},
        )
    if not _record_bound_to_caller(create_record, identity):
        raise _final_precondition_error(
            cmd=cmd,
            status_code=428,
            code=ErrorCode.CONFIRMATION_REQUIRED,
            message="Confirmation token is not bound to this operator",
            reason="CONFIRM_TOKEN_CALLER_MISMATCH",
            kind="confirm_token",
            correlation_id=correlation_id,
            suggestion="Use a confirm token issued for the same authenticated operator",
            details_extra={"confirmToken": token_id},
        )
    return token_id


def _trusted_rebalance_evidence_record(
    record: Dict[str, Any],
    *,
    command_type: CommandType,
) -> bool:
    foundation = (
        record.get("foundation")
        if isinstance(record.get("foundation"), dict)
        else {}
    )
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    return bool(
        record.get("type") == command_type.value
        and record.get("status") == CommandStatus.EXECUTED.value
        and foundation.get("trusted_evidence_producer") == _REBALANCE_EVIDENCE_PRODUCER
        and audit.get("trusted_evidence_producer") == _REBALANCE_EVIDENCE_PRODUCER
    )


_V5_TWO_MAN_EVIDENCE_PRODUCERS = {
    "bff.v5-two-man-evidence.v1",
    "bff.v5.intervention.two-man-sign",
}


def _trusted_v5_two_man_evidence_record(record: Dict[str, Any]) -> bool:
    foundation = (
        record.get("foundation")
        if isinstance(record.get("foundation"), dict)
        else {}
    )
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    f_producer = foundation.get("trusted_evidence_producer")
    a_producer = audit.get("trusted_evidence_producer")
    return bool(
        record.get("type") == CommandType.V5_INTERVENTION_ACTION.value
        and record.get("status") == CommandStatus.EXECUTED.value
        and (f_producer in _V5_TWO_MAN_EVIDENCE_PRODUCERS or a_producer in _V5_TWO_MAN_EVIDENCE_PRODUCERS)
    )


def _two_man_signers(record: Dict[str, Any]) -> set[str]:
    params = _record_params(record)
    audit = _record_audit(record)
    signers: set[str] = set()
    for source in (params, audit):
        for field in _TWO_MAN_SIGNER_LIST_FIELDS:
            raw = source.get(field)
            if isinstance(raw, list):
                signers.update(str(value).strip() for value in raw if str(value or "").strip())
        for field in _TWO_MAN_SIGNER_FIELDS:
            value = str(source.get(field) or "").strip()
            if value:
                signers.add(value)
    actor = _record_actor_id(record)
    if actor:
        signers.add(actor)
    return signers


def _two_man_signature_record(
    signature_id: str,
    *,
    cmd: Optional[OperatorCommand] = None,
    command_store: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    records = []
    if command_store is not None:
        if hasattr(command_store, "_get_all_commands"):
            records = command_store._get_all_commands()
        elif hasattr(command_store, "get_all_commands"):
            records = command_store.get_all_commands()
    for record in records:
        if cmd is None:
            continue
        if cmd.command == CommandType.APPROVED_APPLY:
            trusted = _trusted_rebalance_evidence_record(
                record,
                command_type=CommandType.REBALANCE_TWO_MAN_SIGN,
            )
        else:
            trusted = _trusted_v5_two_man_evidence_record(record)
        if not trusted:
            continue
        params = _record_params(record)
        audit = _record_audit(record)
        target = record.get("target") if isinstance(record.get("target"), dict) else {}
        candidate_values = [
            _dict_first_present(params, _TWO_MAN_SIGNATURE_ID_FIELDS),
            _dict_first_present(audit, _TWO_MAN_SIGNATURE_ID_FIELDS),
            target.get("id"),
        ]
        if any(str(value or "").strip() == signature_id for value in candidate_values):
            matches.append(record)
    if not matches:
        return None
    if cmd is not None:
        bound_matches = [
            record
            for record in matches
            if _record_bound_to_command_and_target(record, cmd)
        ]
        if bound_matches:
            matches = bound_matches
    combined = dict(matches[-1])
    params = dict(_record_params(combined))
    signers: List[str] = []
    for record in matches:
        signers.extend(sorted(_two_man_signers(record)))
    unique_signers = list(dict.fromkeys(value for value in signers if value))
    params.update(
        {
            "signer_operator_ids": unique_signers,
            "first_operator_id": unique_signers[0] if unique_signers else None,
            "second_operator_id": unique_signers[1] if len(unique_signers) > 1 else None,
            "complete": len(unique_signers) >= 2,
        }
    )
    combined["params"] = params
    return combined


def _require_two_man_signature_evidence(
    *,
    cmd: OperatorCommand,
    signature_id: Optional[str],
    correlation_id: Optional[str],
    command_store: Optional[Any] = None,
    missing_suggestion: str = "Attach a second authorized operator signature before retrying",
) -> str:
    if not signature_id:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=409,
            code=ErrorCode.TWO_MAN_SIGNATURE_REQUIRED,
            message="Two-man authorization is required before this action can be accepted",
            reason="TWO_MAN_SIGNATURE_MISSING",
            kind="two_man",
            correlation_id=correlation_id,
            suggestion=missing_suggestion,
        )
    signature_record = _two_man_signature_record(signature_id, cmd=cmd, command_store=command_store)
    if signature_record is None:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=409,
            code=ErrorCode.TWO_MAN_SIGNATURE_REQUIRED,
            message="Two-man signature does not exist",
            reason="TWO_MAN_SIGNATURE_NOT_FOUND",
            kind="two_man",
            correlation_id=correlation_id,
            suggestion="Attach a two-man signature record created for this command and target",
            details_extra={"twoManSignatureId": signature_id},
        )
    signers = _two_man_signers(signature_record)
    if len(signers) < 2:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=409,
            code=ErrorCode.TWO_MAN_SIGNATURE_REQUIRED,
            message="Two-man signature must contain two distinct operators",
            reason="TWO_MAN_SIGNATURE_SIGNER_MISMATCH",
            kind="two_man",
            correlation_id=correlation_id,
            suggestion="Collect a signature record with two distinct operator ids",
            details_extra={"twoManSignatureId": signature_id},
        )
    if not _record_bound_to_command_and_target(signature_record, cmd):
        raise _final_precondition_error(
            cmd=cmd,
            status_code=409,
            code=ErrorCode.TWO_MAN_SIGNATURE_REQUIRED,
            message="Two-man signature is not bound to this command target",
            reason="TWO_MAN_SIGNATURE_BINDING_MISMATCH",
            kind="two_man",
            correlation_id=correlation_id,
            suggestion="Attach a two-man signature for the exact command and target being submitted",
            details_extra={"twoManSignatureId": signature_id},
        )
    return signature_id


def _approval_decision_consumed(decision: Dict[str, Any]) -> bool:
    state = _binding_token(
        decision.get("consumed_state")
        or decision.get("state")
        or decision.get("decision_state")
        or ""
    )
    return bool(
        decision.get("consumed")
        or decision.get("consumed_at")
        or state in {"consumed", "used", "redeemed", "superseded", "revoked"}
    )


def _approval_decision_approved(decision: Dict[str, Any]) -> bool:
    values = {
        _binding_token(decision.get(field))
        for field in ("outcome", "decision", "state", "decision_state", "status")
        if decision.get(field) not in (None, "")
    }
    return bool(values.intersection({"approve", "approved", "accepted"}))


def _rebalance_approval_decision_record(
    decision_id: str,
    command_store: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    if command_store is None:
        return None
    records = []
    if hasattr(command_store, "_get_all_commands"):
        records = command_store._get_all_commands()
    elif hasattr(command_store, "get_all_commands"):
        records = command_store.get_all_commands()
    for record in reversed(records):
        if not _trusted_rebalance_evidence_record(
            record,
            command_type=CommandType.REBALANCE_APPROVAL,
        ):
            continue
        params = _record_params(record)
        candidate = str(
            params.get("approval_decision_id")
            or params.get("decision_id")
            or ""
        ).strip()
        if candidate == decision_id:
            return dict(params)
    return None


def _approval_decision_applies_to_command(decision: Dict[str, Any], decision_id: str, cmd: OperatorCommand) -> bool:
    synthetic_record = {"params": decision}
    has_command = _binding_has_command(synthetic_record)
    has_target = _binding_has_target(synthetic_record)
    if has_command and not _binding_command_matches(synthetic_record, cmd):
        return False
    if has_target:
        return _binding_target_matches(synthetic_record, cmd)
    if cmd.target.type == ObjectType.APPROVAL_DECISION:
        return decision_id == cmd.target.id
    return False


_HUMAN_INBOX_PRIORITIES = {"low", "medium", "high", "critical"}


def _human_inbox_priority(value: Any, *, fallback: str = "medium") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in _HUMAN_INBOX_PRIORITIES:
        return normalized
    if normalized in {"sev1", "p0"}:
        return "critical"
    if normalized in {"sev2", "p1"}:
        return "high"
    if normalized in {"sev3", "p2"}:
        return "medium"
    return fallback


def _human_gate_source_id_from_params(params: Dict[str, Any], item_id: str, source_type: Optional[str]) -> Optional[str]:
    explicit_source_id = _dict_first_present(params, _HUMAN_GATE_SOURCE_ID_FIELDS)
    if explicit_source_id is not None:
        return _human_gate_clean_text(explicit_source_id) or None
    if ":" in item_id:
        prefix, suffix = item_id.split(":", 1)
        if not source_type or prefix.strip().lower() == source_type:
            return suffix.strip() or None
    return None


def _human_gate_find_approval_record(source_id: Optional[str], read_store: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    if not source_id:
        return None
    if read_store is not None:
        getter = getattr(read_store, "get_approval_decision", None)
        if callable(getter):
            record = getter(source_id)
            if record is not None:
                return dict(record)
        local_data = getattr(read_store, "_data", {})
        if isinstance(local_data, dict):
            local_approvals = local_data.get("approval_decisions")
            if isinstance(local_approvals, dict) and isinstance(local_approvals.get(source_id), dict):
                return dict(local_approvals[source_id])
        queue_lister = getattr(read_store, "list_approval_queue_items", None)
        if callable(queue_lister):
            for item in queue_lister() or []:
                candidate = _human_gate_clean_text(
                    item.get("decision_id")
                    or item.get("id")
                    or item.get("approval_decision_id")
                )
                if candidate == source_id:
                    return dict(item)
    return None


def _human_gate_find_intervention_record(source_id: Optional[str], read_store: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    if not source_id:
        return None
    if read_store is not None:
        getter = getattr(read_store, "get_v5_intervention", None)
        if callable(getter):
            record = getter(source_id)
            if record is not None:
                return dict(record)
        store_lister = getattr(read_store, "list_v5_interventions", None)
        if callable(store_lister):
            for item in store_lister():
                if isinstance(item, dict):
                    candidate = _human_gate_clean_text(item.get("intervention_id") or item.get("id"))
                    if candidate == source_id:
                        return dict(item)
    return None


def _human_gate_source_record(
    params: Dict[str, Any],
    read_store: Optional[Any] = None,
) -> tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    item_id = _human_gate_clean_text(params.get("human_gate_item_id") or params.get("itemId") or params.get("item_id"))
    source_type = _human_gate_clean_text(params.get("source_type") or params.get("sourceType")).lower() or None
    if source_type not in {"approval", "intervention", None}:
        source_type = None
    if not source_type:
        source_type = _human_gate_source_type(item_id)
    source_id = _human_gate_source_id_from_params(params, item_id, source_type)
    if source_type == "approval":
        return source_type, source_id, _human_gate_find_approval_record(source_id, read_store=read_store)
    if source_type == "intervention":
        return source_type, source_id, _human_gate_find_intervention_record(source_id, read_store=read_store)
    return source_type, source_id, None


def _human_gate_actor_id(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key in ("operator_id", "operatorId", "actor_id", "actorId", "id", "user_id", "userId"):
            clean = _human_gate_clean_text(value.get(key))
            if clean:
                return clean
        return None
    clean = _human_gate_clean_text(value)
    return clean or None


def _human_gate_requester_ids(record: Optional[Dict[str, Any]]) -> set[str]:
    if not isinstance(record, dict):
        return set()
    requester_ids: set[str] = set()
    for field in _HUMAN_GATE_REQUESTER_FIELDS:
        actor_id = _human_gate_actor_id(record.get(field))
        if actor_id:
            requester_ids.add(actor_id)
    context = record.get("decision_context") if isinstance(record.get("decision_context"), dict) else {}
    for field in _HUMAN_GATE_REQUESTER_FIELDS:
        actor_id = _human_gate_actor_id(context.get(field))
        if actor_id:
            requester_ids.add(actor_id)
    return requester_ids


def _human_gate_record_risk_level(params: Dict[str, Any], record: Optional[Dict[str, Any]]) -> Optional[str]:
    sources: List[Dict[str, Any]] = [params]
    if isinstance(record, dict):
        sources.append(record)
        for nested_key in ("governance", "decision_context", "remediation_context", "metadata"):
            nested = record.get(nested_key)
            if isinstance(nested, dict):
                sources.append(nested)
    for source in sources:
        for field in _HUMAN_GATE_RISK_FIELDS:
            risk = _human_gate_clean_text(source.get(field)).lower()
            if risk:
                return _human_inbox_priority(risk, fallback=risk)
    return None


def _human_gate_requires_two_man(params: Dict[str, Any], record: Optional[Dict[str, Any]]) -> bool:
    for field in ("requires_two_man", "requiresTwoMan", "requires_second_operator", "requiresSecondOperator"):
        value = params.get(field)
        if isinstance(value, bool) and value:
            return True
        if _human_gate_clean_text(value).lower() in {"1", "true", "yes"}:
            return True
    risk_level = _human_gate_record_risk_level(params, record)
    if risk_level in _HUMAN_GATE_HIGH_RISK_LEVELS:
        return True
    live_capital = params.get("liveCapitalMutation", params.get("live_capital_mutation"))
    return isinstance(live_capital, bool) and live_capital


def _human_gate_downstream_effect_executed(record: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(record, dict):
        return False
    for field in _HUMAN_GATE_DOWNSTREAM_EXECUTED_FIELDS:
        status = _human_gate_clean_text(record.get(field)).lower()
        if status in _HUMAN_GATE_DOWNSTREAM_EXECUTED_STATES:
            return True
    for field in _HUMAN_GATE_DOWNSTREAM_EXECUTED_AT_FIELDS:
        if _human_gate_clean_text(record.get(field)):
            return True
    downstream = record.get("downstream") if isinstance(record.get("downstream"), dict) else {}
    for field in _HUMAN_GATE_DOWNSTREAM_EXECUTED_FIELDS:
        status = _human_gate_clean_text(downstream.get(field)).lower()
        if status in _HUMAN_GATE_DOWNSTREAM_EXECUTED_STATES:
            return True
    for field in _HUMAN_GATE_DOWNSTREAM_EXECUTED_AT_FIELDS:
        if _human_gate_clean_text(downstream.get(field)):
            return True
    return False


def _require_human_gate_security_preconditions(
    *,
    cmd: OperatorCommand,
    payload: Dict[str, Any],
    identity: OperatorIdentity,
    correlation_id: Optional[str],
    read_store: Optional[Any] = None,
    command_store: Optional[Any] = None,
) -> Dict[str, str]:
    params = cmd.params
    decision = _human_gate_clean_text(params.get("decision")).lower()
    source_type, source_id, source_record = _human_gate_source_record(params, read_store=read_store)

    if decision in _HUMAN_GATE_SELF_APPROVAL_DECISIONS:
        requester_ids = _human_gate_requester_ids(source_record)
        if identity.operator_id in requester_ids:
            raise _final_precondition_error(
                cmd=cmd,
                status_code=403,
                code=ErrorCode.FORBIDDEN,
                message="HumanGate decisions cannot be approved by their requester",
                reason="HUMAN_GATE_SELF_APPROVAL_FORBIDDEN",
                kind="anti_self_approval",
                correlation_id=correlation_id,
                suggestion="Route this HumanGate decision to a different approver",
                details_extra={
                    "sourceType": source_type,
                    "sourceRecordId": source_id,
                    "requesterId": identity.operator_id,
                },
            )

    if decision == "revoke":
        if source_record is None:
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.HUMAN_GATE_PENDING,
                message="HumanGateRevoke requires a readable source record",
                reason="HUMAN_GATE_SOURCE_NOT_FOUND",
                kind="human_gate_revoke",
                correlation_id=correlation_id,
                suggestion="Refresh the Human Inbox source record before retrying revoke",
                details_extra={"sourceType": source_type, "sourceRecordId": source_id},
            )
        if _human_gate_downstream_effect_executed(source_record):
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.RESOURCE_CONFLICT,
                message="HumanGateRevoke cannot revoke an already executed downstream effect",
                reason="HUMAN_GATE_REVOKE_DOWNSTREAM_EXECUTED",
                kind="human_gate_revoke",
                correlation_id=correlation_id,
                suggestion="Submit a compensating action through the downstream authority instead of revoking this HumanGate item",
                details_extra={"sourceType": source_type, "sourceRecordId": source_id},
            )

    evidence: Dict[str, str] = {}
    if decision in _HUMAN_GATE_APPROVER_DECISIONS and _human_gate_requires_two_man(params, source_record):
        signature_id = _precondition_value(payload, params, _TWO_MAN_EVIDENCE_FIELDS)
        evidence["two_man_signature_id"] = _require_two_man_signature_evidence(
            cmd=cmd,
            signature_id=signature_id,
            correlation_id=correlation_id,
            command_store=command_store,
            missing_suggestion="Attach a two-man signature for this high-risk HumanGate item before retrying",
        )
        params["two_man_signature_id"] = evidence["two_man_signature_id"]
        params["twoManSignatureId"] = evidence["two_man_signature_id"]

    return evidence


def require_final_command_preconditions(
    *,
    cmd: OperatorCommand,
    payload: Dict[str, Any],
    confirm_token: Optional[str],
    identity: OperatorIdentity,
    correlation_id: Optional[str],
    confirm_token_records_fn: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
    confirm_token_lifecycle_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
    read_store: Optional[Any] = None,
    command_store: Optional[Any] = None,
) -> Dict[str, str]:
    entry = get_catalog_entry(cmd.command.value)
    if entry is None:
        return {}

    params = dict(cmd.params)
    paper_simulation_authority = bool(
        cmd.command == CommandType.APPROVED_APPLY
        and (
            params.get("authority_mode") == _PPL_ALLOC_009_PAPER_AUTHORITY_MODE
            or params.get("target_environment") == "paper"
            or str(params.get("environment") or "").strip().lower() == "paper"
        )
    )
    is_human_gate = cmd.command in _HUMAN_GATE_DECISIONS_BY_COMMAND

    # ------------------------------------------------------------------ #
    # Phase 1: Presence checks for required evidence fields
    # ------------------------------------------------------------------ #
    token_id = _precondition_value(payload, params, _CONFIRM_TOKEN_FIELDS, confirm_token)
    if getattr(entry, "requires_confirm_token", False) and not token_id:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=428,
            code=ErrorCode.CONFIRMATION_REQUIRED,
            message="Confirmation token is required before this action can be accepted",
            reason="CONFIRM_TOKEN_MISSING",
            kind="confirm_token",
            correlation_id=correlation_id,
            suggestion="Retry with X-Confirm-Token or confirmToken after the operator confirmation step",
        )

    approval_decision_id = _precondition_value(payload, params, _APPROVAL_EVIDENCE_FIELDS)
    if getattr(entry, "requires_approval", False) and not approval_decision_id:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=409,
            code=ErrorCode.HUMAN_GATE_PENDING,
            message="Approval evidence is required before this action can be accepted",
            reason="APPROVAL_EVIDENCE_MISSING",
            kind="approval",
            correlation_id=correlation_id,
            suggestion="Attach approvalId from the governance approval flow before retrying",
        )

    two_man_sig_id = _precondition_value(payload, params, _TWO_MAN_EVIDENCE_FIELDS)
    if not is_human_gate and getattr(entry, "requires_two_man", False) and not paper_simulation_authority and not two_man_sig_id:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=409,
            code=ErrorCode.TWO_MAN_SIGNATURE_REQUIRED,
            message="Two-man authorization is required before this action can be accepted",
            reason="TWO_MAN_SIGNATURE_MISSING",
            kind="two_man",
            correlation_id=correlation_id,
            suggestion="Attach a second authorized operator signature before retrying",
        )

    evidence: Dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Phase 2: Full validation of tokens, approvals, two-man, and gates
    # ------------------------------------------------------------------ #
    if getattr(entry, "requires_confirm_token", False):
        validated_token_id = require_final_command_confirm_token(
            cmd=cmd,
            payload=payload,
            confirm_token=confirm_token,
            identity=identity,
            correlation_id=correlation_id,
            confirm_token_records_fn=confirm_token_records_fn,
            confirm_token_lifecycle_fn=confirm_token_lifecycle_fn,
        )
        if validated_token_id:
            evidence["confirm_token_id"] = validated_token_id

    if paper_simulation_authority and not identity.mfa_verified:
        raise _final_precondition_error(
            cmd=cmd,
            status_code=403,
            code=ErrorCode.FORBIDDEN,
            message="Paper allocation apply requires MFA",
            reason="PAPER_SIMULATION_MFA_REQUIRED",
            kind="mfa",
            correlation_id=correlation_id,
            suggestion="Retry with the strict dev operator identity and verified MFA",
        )

    if getattr(entry, "requires_approval", False) and approval_decision_id:
        approval_decision = (
            read_store.get_approval_decision(approval_decision_id)
            if read_store and hasattr(read_store, "get_approval_decision")
            else None
        )
        if approval_decision is None and cmd.command == CommandType.APPROVED_APPLY:
            approval_decision = _rebalance_approval_decision_record(
                approval_decision_id,
                command_store=command_store,
            )
        if approval_decision is None:
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.HUMAN_GATE_PENDING,
                message="Approval decision does not exist",
                reason="APPROVAL_DECISION_NOT_FOUND",
                kind="approval",
                correlation_id=correlation_id,
                suggestion="Attach an approvalDecisionId that exists in the governance approval store",
                details_extra={"approvalDecisionId": approval_decision_id},
            )
        if _approval_decision_consumed(approval_decision):
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.HUMAN_GATE_PENDING,
                message="Approval decision has already been consumed",
                reason="APPROVAL_DECISION_CONSUMED",
                kind="approval",
                correlation_id=correlation_id,
                suggestion="Request a fresh approval decision before retrying this command",
                details_extra={"approvalDecisionId": approval_decision_id},
            )
        if not _approval_decision_approved(approval_decision):
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.HUMAN_GATE_PENDING,
                message="Approval decision is not approved",
                reason="APPROVAL_DECISION_NOT_APPROVED",
                kind="approval",
                correlation_id=correlation_id,
                suggestion="Obtain an approved decision for this exact command and target",
                details_extra={"approvalDecisionId": approval_decision_id},
            )
        if not _approval_decision_applies_to_command(approval_decision, approval_decision_id, cmd):
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.HUMAN_GATE_PENDING,
                message="Approval decision is not bound to this command target",
                reason="APPROVAL_DECISION_BINDING_MISMATCH",
                kind="approval",
                correlation_id=correlation_id,
                suggestion="Attach approval evidence for the exact command and target being submitted",
                details_extra={"approvalDecisionId": approval_decision_id},
            )
        evidence["approval_decision_id"] = approval_decision_id
        if paper_simulation_authority:
            approval_actor = str(
                approval_decision.get("decided_by")
                or approval_decision.get("actor_id")
                or approval_decision.get("operator_id")
                or ""
            ).strip()
            if not approval_actor or approval_actor == identity.operator_id:
                raise _final_precondition_error(
                    cmd=cmd,
                    status_code=409,
                    code=ErrorCode.HUMAN_GATE_PENDING,
                    message="Paper allocation approval and apply must be distinct",
                    reason="PAPER_SIMULATION_APPROVAL_APPLY_NOT_DISTINCT",
                    kind="approval",
                    correlation_id=correlation_id,
                    suggestion=(
                        "Use an approver identity distinct from the authenticated "
                        "operator applying the paper allocation"
                    ),
                )
            evidence["paper_simulation_authority"] = (
                _PPL_ALLOC_009_PAPER_AUTHORITY_MODE
            )

    if is_human_gate:
        evidence.update(
            _require_human_gate_security_preconditions(
                cmd=cmd,
                payload=payload,
                identity=identity,
                correlation_id=correlation_id,
                read_store=read_store,
                command_store=command_store,
            )
        )
        return evidence

    if getattr(entry, "requires_two_man", False) and not paper_simulation_authority and two_man_sig_id:
        evidence["two_man_signature_id"] = _require_two_man_signature_evidence(
            cmd=cmd,
            signature_id=two_man_sig_id,
            correlation_id=correlation_id,
            command_store=command_store,
        )

    return evidence


# ---------------------------------------------------------------------------
# Ops Console Preconditions & Command Validator Graph
# ---------------------------------------------------------------------------

_APPROVE_DEPLOYMENT_REQUIRED = {"deployment_plan_id", "approval_decision"}
_VALID_APPROVAL_DECISIONS = {"approve", "reject"}
_APPROVE_DECISION_REQUIRED = {"decision_id"}
_REJECT_DECISION_REQUIRED = {"decision_id", "rejection_reason"}
_REQUEST_APPROVAL_REVISION_REQUIRED = {"decision_id", "revision_notes"}
_ESCALATE_DIFF_REQUIRED = {"plan_id", "escalation_reason"}
_PAUSE_RUNTIME_REQUIRED = {"runtime_binding_id", "pause_action"}
_VALID_PAUSE_ACTIONS = {"pause", "resume"}
_PAUSE_EXECUTION_REQUIRED = {"pause_new_entries", "cancel_open_orders"}
_ROLLBACK_REQUIRED = {"rollback_target_type", "target_id", "rollback_to_version"}
_VALID_ROLLBACK_TARGET_TYPES = {"deployment", "runtime"}
_APPROVE_ROLLBACK_REQUIRED = {"rollback_id"}
_REJECT_ROLLBACK_REQUIRED = {"rollback_id", "rejection_reason"}
_RISK_OFF_REQUIRED = {"reduce_exposure_pct"}
_SAFE_MODE_LEVELS = {"soft"}
_KILL_SWITCH_REQUIRED = {"scope", "activate"}
_VALID_SCOPES = {"persona", "pool", "all"}
_VALID_SEVERITIES = {"critical", "high", "medium"}
_APPROVE_EVO_REQUIRED = {"evolution_decision_id", "approval_action"}
_VALID_EVO_APPROVAL_ACTIONS = {"approve", "reject"}
_EXECUTE_EVO_REQUIRED = {"evolution_decision_id", "action_type"}
_VALID_EVO_ACTION_TYPES = {"freeze", "retrain", "revalidate", "mutate", "retire"}
_APPROVE_MUTATION_REQUIRED = {"decision_id"}
_REJECT_MUTATION_REQUIRED = {"decision_id"}
_REVIEW_MUTATION_REQUIRED = {"decision_id", "approval_decision_id"}
_EXECUTE_MUTATION_REQUIRED = {"decision_id"}
_RECORD_SPONSOR_DECISION_REQUIRED = {"committee_id", "sponsor_decision", "rationale_ref"}
_VALID_SPONSOR_DECISIONS = {"approved", "rejected", "conditional"}
_REMEDIATE_SENTINEL_REQUIRED = {"intervention_id", "remediation_action"}
_VALID_REMEDIATION_ACTIONS = {"resolve", "dismiss", "escalate"}
_DECIDE_V5_INTERVENTION_REQUIRED = {"intervention_id", "decision"}
_VALID_V5_INTERVENTION_DECISIONS = {"approve", "reject", "defer", "dismiss"}
_HUMAN_GATE_REQUIRED = {"human_gate_item_id", "decision"}
_VALID_HUMAN_GATE_DECISIONS = set(_HUMAN_GATE_DECISIONS_BY_COMMAND.values())
_HUMAN_GATE_APPROVER_DECISIONS = {"approve", "reject", "revoke", "extend_ttl"}
_HUMAN_GATE_SELF_APPROVAL_DECISIONS = {"approve", "reject", "revoke"}
_HUMAN_GATE_HIGH_RISK_LEVELS = {"high", "critical"}
_HUMAN_GATE_DEFAULT_MAX_TTL_SECONDS = 604800

_OPS_CONSOLE_READ_SURFACE_RESOLVER: Optional[Callable[[], Any]] = None
_OPS_CONSOLE_OPS_READ_MODEL_RESOLVER: Optional[Callable[[str], Any]] = None
_OPS_CONSOLE_BFF_ERROR_RESOLVER: Optional[Callable[..., Any]] = None
_OPS_CONSOLE_UTC_NOW_RESOLVER: Optional[Callable[[], str]] = None


def set_ops_console_precondition_resolvers(
    *,
    read_surface: Optional[Callable[[], Any]] = None,
    ops_read_model: Optional[Callable[[str], Any]] = None,
    bff_error: Optional[Callable[..., Any]] = None,
    utc_now: Optional[Callable[[], str]] = None,
) -> None:
    global _OPS_CONSOLE_READ_SURFACE_RESOLVER, _OPS_CONSOLE_OPS_READ_MODEL_RESOLVER
    global _OPS_CONSOLE_BFF_ERROR_RESOLVER, _OPS_CONSOLE_UTC_NOW_RESOLVER
    if read_surface is not None:
        _OPS_CONSOLE_READ_SURFACE_RESOLVER = read_surface
    if ops_read_model is not None:
        _OPS_CONSOLE_OPS_READ_MODEL_RESOLVER = ops_read_model
    if bff_error is not None:
        _OPS_CONSOLE_BFF_ERROR_RESOLVER = bff_error
    if utc_now is not None:
        _OPS_CONSOLE_UTC_NOW_RESOLVER = utc_now


def _resolve_read_surface(provided: Optional[Any] = None) -> Any:
    if provided is not None:
        return provided() if callable(provided) else provided
    if _OPS_CONSOLE_READ_SURFACE_RESOLVER is not None:
        return _OPS_CONSOLE_READ_SURFACE_RESOLVER()
    import sys
    main_mod = sys.modules.get("services.control_plane.bff.main") or sys.modules.get("main")
    if main_mod is not None and hasattr(main_mod, "read_store"):
        return getattr(main_mod, "read_store")
    return None


def _resolve_ops_read_model(persona_id: str, provided: Optional[Callable[[str], Any]] = None) -> Any:
    if provided is not None:
        return provided(persona_id)
    if _OPS_CONSOLE_OPS_READ_MODEL_RESOLVER is not None:
        return _OPS_CONSOLE_OPS_READ_MODEL_RESOLVER(persona_id)
    import sys
    main_mod = sys.modules.get("services.control_plane.bff.main") or sys.modules.get("main")
    if main_mod is not None and hasattr(main_mod, "_ops_read_model_entry_for_persona"):
        return getattr(main_mod, "_ops_read_model_entry_for_persona")(persona_id)
    return None


def _resolve_bff_error(provided: Optional[Callable[..., Any]] = None) -> Callable[..., Any]:
    if provided is not None:
        return provided
    if _OPS_CONSOLE_BFF_ERROR_RESOLVER is not None:
        return _OPS_CONSOLE_BFF_ERROR_RESOLVER
    import sys
    main_mod = sys.modules.get("services.control_plane.bff.main") or sys.modules.get("main")
    if main_mod is not None and hasattr(main_mod, "_bff_error"):
        return getattr(main_mod, "_bff_error")
    return _auth_bff_error


def _resolve_utc_now(provided: Optional[Callable[[], str]] = None) -> str:
    if provided is not None:
        return provided()
    if _OPS_CONSOLE_UTC_NOW_RESOLVER is not None:
        return _OPS_CONSOLE_UTC_NOW_RESOLVER()
    import sys
    main_mod = sys.modules.get("services.control_plane.bff.main") or sys.modules.get("main")
    if main_mod is not None and hasattr(main_mod, "utc_now"):
        return getattr(main_mod, "utc_now")()
    return datetime.now(timezone.utc).isoformat()


def _human_gate_max_ttl_seconds() -> int:
    raw = os.getenv("PANTHEON_HUMAN_GATE_MAX_TTL_SECONDS", str(_HUMAN_GATE_DEFAULT_MAX_TTL_SECONDS)).strip()
    try:
        configured = int(raw)
    except (TypeError, ValueError):
        configured = _HUMAN_GATE_DEFAULT_MAX_TTL_SECONDS
    return max(1, configured)


def _check_binding_tenant_ownership(
    binding: Any,
    identity: OperatorIdentity,
    *,
    bff_me_tenant_payload_fn: Optional[Callable[..., Any]] = None,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> str:
    _err = bff_error_fn or _resolve_bff_error()
    binding_tenant = ""
    metadata = binding.get("metadata") if isinstance(binding, dict) else getattr(binding, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("tenant_id", "tenantId", "tenant"):
            val = metadata.get(key)
            if val is not None and str(val).strip():
                binding_tenant = str(val).strip()
                break
    if not binding_tenant:
        for key in ("tenant_id", "tenantId", "tenant"):
            val = binding.get(key) if isinstance(binding, dict) else getattr(binding, key, None)
            if val is not None and str(val).strip():
                binding_tenant = str(val).strip()
                break
    if not binding_tenant:
        raise _err(403, ErrorCode.FORBIDDEN, "Runtime tenant is unavailable", "Cannot determine the runtime owner tenant", precondition_failed="cross_tenant")

    _tenant_fn = bff_me_tenant_payload_fn or _auth_bff_me_tenant_payload
    try:
        _tenant_fn(identity, requested_tenant=binding_tenant)
    except HTTPException as exc:
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "Cross-tenant access forbidden",
            f"Caller cannot operate on runtime binding in tenant '{binding_tenant}'",
            precondition_failed="cross_tenant",
        ) from exc
    return binding_tenant


def _enforce_ops_console_preconditions(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    required_bindings: Optional[List[str]] = None,
    *,
    read_surface: Optional[Any] = None,
    ops_read_model_fn: Optional[Callable[[str], Any]] = None,
    check_binding_tenant_ownership_fn: Optional[Callable[[Any, OperatorIdentity], str]] = None,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    _read_store = _resolve_read_surface(read_surface)
    _check_tenant = check_binding_tenant_ownership_fn or _check_binding_tenant_ownership

    entity_type = str(params.get("entity_type") or params.get("entityType") or "").strip().lower()
    persona_id = ""
    runtime_id = ""

    if entity_type == "persona":
        persona_id = (
            params.get("persona_id")
            or params.get("personaId")
            or params.get("entity_id")
            or params.get("entityId")
            or ""
        ).strip()
    elif entity_type in ("runtime", "paper-runtime"):
        runtime_id = (
            params.get("runtime_id")
            or params.get("runtimeId")
            or params.get("entity_id")
            or params.get("entityId")
            or ""
        ).strip()

    if not persona_id:
        persona_id = (params.get("persona_id") or params.get("personaId") or "").strip()
    if not runtime_id:
        runtime_id = (params.get("runtime_id") or params.get("runtimeId") or "").strip()

    if persona_id and _read_store:
        persona = _read_store.get_persona(persona_id)
        if not persona:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )

        read_model = _resolve_ops_read_model(persona_id, ops_read_model_fn)
        if read_model:
            confidence = getattr(read_model, "data_confidence", None)
            if isinstance(confidence, str):
                confidence_str = confidence
            elif hasattr(confidence, "value"):
                confidence_str = confidence.value
            else:
                confidence_str = str(confidence or "")

            if confidence_str.lower() in ("unavailable", "unverifiable"):
                raise _err(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    f"Action blocked due to {confidence_str} source confidence for persona {persona_id}",
                    "Source confidence must be formal, partial, fallback, or degraded",
                    precondition_failed="source_confidence",
                )

            if required_bindings:
                identity_obj = getattr(read_model, "identity", None)
                if "runtime" in required_bindings:
                    runtime_ids = getattr(identity_obj, "runtime_ids", None) if identity_obj else None
                    if not runtime_ids:
                        raise _err(
                            422,
                            ErrorCode.VALIDATION_FAILED,
                            f"Persona {persona_id} must have an active runtime binding",
                            "No active runtime binding found for this persona",
                            precondition_failed="runtime_binding_missing",
                        )
                if "capital" in required_bindings:
                    pool_ids = getattr(identity_obj, "capital_pool_ids", None) if identity_obj else None
                    ledger_ids = getattr(identity_obj, "paper_ledger_ids", None) if identity_obj else None
                    if not pool_ids and not ledger_ids:
                        raise _err(
                            422,
                            ErrorCode.VALIDATION_FAILED,
                            f"Persona {persona_id} must have a capital pool or paper ledger binding",
                            "No active capital or ledger binding found for this persona",
                            precondition_failed="capital_binding_missing",
                        )

    is_runtime_target = (
        entity_type in ("runtime", "paper-runtime")
        or (required_bindings and "paper" in required_bindings)
        or str(params.get("target_type") or "").strip().lower() in ("runtime", "paper-runtime")
    )
    if not runtime_id and is_runtime_target:
        runtime_id = (
            params.get("runtime_id")
            or params.get("runtimeId")
            or params.get("entity_id")
            or params.get("entityId")
            or ""
        ).strip()
    if runtime_id and _read_store:
        binding = _read_store.get_runtime_binding_by_runtime_id(runtime_id)
        if not binding:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Runtime not found",
                f"Runtime {runtime_id} does not exist",
            )
        resolved_rt_id = (
            binding.get("runtime_id") or binding.get("runtimeId")
            if isinstance(binding, dict)
            else getattr(binding, "runtime_id", getattr(binding, "runtimeId", None))
        )
        resolved_rt_id = str(resolved_rt_id or "").strip()
        if resolved_rt_id and resolved_rt_id != runtime_id:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Runtime ID mismatch",
                f"Binding runtime ID '{resolved_rt_id}' does not match requested runtime ID '{runtime_id}'",
                precondition_failed="runtime_id_mismatch",
            )
        payload_binding_id = str(
            params.get("binding_id")
            or params.get("bindingId")
            or params.get("runtime_binding_id")
            or params.get("runtimeBindingId")
            or ""
        ).strip()
        actual_binding_id = (
            binding.get("binding_id") or binding.get("id") or binding.get("bindingId")
            if isinstance(binding, dict)
            else getattr(binding, "binding_id", getattr(binding, "id", getattr(binding, "bindingId", None)))
        )
        actual_binding_id = str(actual_binding_id or "").strip()
        if payload_binding_id and actual_binding_id and payload_binding_id != actual_binding_id:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Binding ID mismatch",
                f"Payload binding ID '{payload_binding_id}' does not match resolved binding '{actual_binding_id}'",
                precondition_failed="binding_mismatch",
            )
        params["tenant_id"] = _check_tenant(binding, identity)
        if required_bindings and "paper" in required_bindings:
            stage = (
                binding.get("deployment_mode")
                or binding.get("deployment_stage")
                or binding.get("stage")
                if isinstance(binding, dict)
                else getattr(binding, "deployment_mode", getattr(binding, "deployment_stage", getattr(binding, "stage", "")))
            )
            stage = str(stage or "").strip().lower()
            if stage != "paper":
                raise _err(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    f"Runtime {runtime_id} stage is {stage}, not paper",
                    "Action is restricted to paper runtimes only",
                    precondition_failed="stage_mismatch",
                )
        params.pop("verified_binding", None)
        params.pop("verified_binding_id", None)
        params.pop("verified_runtime_binding_id", None)
        if actual_binding_id:
            params["runtime_binding_id"] = actual_binding_id


enforce_ops_console_preconditions = _enforce_ops_console_preconditions


def _validate_pause_execution(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _PAUSE_EXECUTION_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for PauseExecution",
            f"Missing fields: {sorted(missing)}",
        )
    for field in sorted(_PAUSE_EXECUTION_REQUIRED):
        if not isinstance(params.get(field), bool):
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"Invalid {field} value",
                f"{field} must be a boolean",
            )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "PauseExecution requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )


def _validate_issue_risk_off(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _RISK_OFF_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for IssueRiskOff",
            f"Missing fields: {sorted(missing)}",
        )
    exposure_pct = params.get("reduce_exposure_pct")
    if not isinstance(exposure_pct, (int, float)) or exposure_pct <= 0 or exposure_pct > 100:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid reduce_exposure_pct value",
            "reduce_exposure_pct must be a number between 1 and 100",
        )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "IssueRiskOff requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )


def _validate_liquidate_all(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    if params:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "LiquidateAll does not accept params",
            "params must be an empty object for LiquidateAll",
        )
    _auth_require_admin_mfa(identity, "LiquidateAll")


def _validate_hard_rollback(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    target_artifact_id = str(params.get("target_artifact_id") or "").strip()
    if not target_artifact_id:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for HardRollback",
            "target_artifact_id must be a non-empty string",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "HardRollback requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )


def _validate_issue_safe_mode(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    safe_mode_level = str(params.get("safe_mode_level") or "").strip().lower()
    if safe_mode_level not in _SAFE_MODE_LEVELS:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid safe_mode_level",
            f"safe_mode_level must be one of {sorted(_SAFE_MODE_LEVELS)}",
        )
    _auth_require_admin_mfa(identity, "IssueSafeMode")


def _validate_approve_deployment(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _APPROVE_DEPLOYMENT_REQUIRED - params.keys()
    if missing:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for ApproveDeployment",
            f"Missing fields: {sorted(missing)}",
        )
    if params["approval_decision"] not in _VALID_APPROVAL_DECISIONS:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid approval_decision value",
            f"Must be one of {_VALID_APPROVAL_DECISIONS}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _err(
            403, ErrorCode.FORBIDDEN,
            "ApproveDeployment requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_approve_decision(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _APPROVE_DECISION_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for ApproveDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "ApproveDecision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_reject_decision(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _REJECT_DECISION_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for RejectDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("rejection_reason") or "").strip():
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "RejectDecision requires a non-empty rejection_reason",
            "rejection_reason must be a non-empty string",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "RejectDecision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_request_approval_revision(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _REQUEST_APPROVAL_REVISION_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for RequestApprovalRevision",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("revision_notes") or "").strip():
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "RequestApprovalRevision requires non-empty revision_notes",
            "revision_notes must be a non-empty string",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "RequestApprovalRevision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_pause_runtime(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _PAUSE_RUNTIME_REQUIRED - params.keys()
    if missing:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for PauseRuntime",
            f"Missing fields: {sorted(missing)}",
        )
    if params["pause_action"] not in _VALID_PAUSE_ACTIONS:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid pause_action value",
            f"Must be one of {_VALID_PAUSE_ACTIONS}",
        )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _err(
            403, ErrorCode.FORBIDDEN,
            "PauseRuntime requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )


def _validate_execute_rollback(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for ExecuteRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if params["rollback_target_type"] not in _VALID_ROLLBACK_TARGET_TYPES:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid rollback_target_type",
            f"Must be one of {_VALID_ROLLBACK_TARGET_TYPES}",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _err(
            403, ErrorCode.FORBIDDEN,
            "ExecuteRollback requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )


def _validate_approve_rollback(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _APPROVE_ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for ApproveRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _err(
            403, ErrorCode.FORBIDDEN,
            "ApproveRollback requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_reject_rollback(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _REJECT_ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for RejectRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("rejection_reason") or "").strip():
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "RejectRollback requires a non-empty rejection_reason",
            "rejection_reason must be a non-empty string",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _err(
            403, ErrorCode.FORBIDDEN,
            "RejectRollback requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_activate_kill_switch(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _KILL_SWITCH_REQUIRED - params.keys()
    if missing:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for ActivateKillSwitch",
            f"Missing fields: {sorted(missing)}",
        )
    if params["scope"] not in _VALID_SCOPES:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid scope for ActivateKillSwitch",
            f"Must be one of {_VALID_SCOPES}",
        )
    severity = params.get("severity")
    if severity is not None and severity not in _VALID_SEVERITIES:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid severity for ActivateKillSwitch",
            f"Must be one of {_VALID_SEVERITIES}",
        )
    if "admin" not in identity.roles:
        raise _err(
            403, ErrorCode.FORBIDDEN,
            "ActivateKillSwitch requires 'admin' role",
            "Operator does not hold the admin role",
            precondition_failed="role_check",
            suggestion="Escalate to an admin-role operator",
        )
    # MFA required for kill-switch (§3.2.3)
    if not identity.mfa_verified:
        raise _err(
            403, ErrorCode.AUTH_REQUIRED,
            "ActivateKillSwitch requires MFA verification",
            "Admin action requires MFA validation",
            precondition_failed="mfa_check",
            suggestion="Provide a valid MFA token in your session",
        )


def _validate_escalate_diff(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _ESCALATE_DIFF_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for EscalateDiff",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("escalation_reason") or "").strip():
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "EscalateDiff requires a non-empty escalation_reason",
            "escalation_reason must be a non-empty string",
        )
    if not {"operator", "reviewer", "approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "EscalateDiff requires operator-level governance access",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, reviewer, approver, or admin role",
        )


def _validate_approve_evolution_decision(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _APPROVE_EVO_REQUIRED - params.keys()
    if missing:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for ApproveEvolutionDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if params["approval_action"] not in _VALID_EVO_APPROVAL_ACTIONS:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid approval_action",
            f"Must be one of {_VALID_EVO_APPROVAL_ACTIONS}",
        )
    if not {"reviewer", "admin", "approver"}.intersection(identity.roles):
        raise _err(
            403, ErrorCode.FORBIDDEN,
            "ApproveEvolutionDecision requires 'reviewer', 'approver', or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with reviewer, approver, or admin role",
        )


def _validate_execute_evolution_action(params: Dict[str, Any], identity: OperatorIdentity, *, bff_error_fn: Optional[Callable[..., Any]] = None) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _EXECUTE_EVO_REQUIRED - params.keys()
    if missing:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Missing required params for ExecuteEvolutionAction",
            f"Missing fields: {sorted(missing)}",
        )
    if params["action_type"] not in _VALID_EVO_ACTION_TYPES:
        raise _err(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid action_type for ExecuteEvolutionAction",
            f"Must be one of {_VALID_EVO_ACTION_TYPES}",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _err(
            403, ErrorCode.FORBIDDEN,
            "ExecuteEvolutionAction requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )


def _mutation_review_projection(
    decision_id: str,
    *,
    identity: OperatorIdentity,
    snapshot_at: str,
    read_surface: Optional[Any] = None,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        from ..governance.service import GovernanceService
    except (ImportError, ValueError):
        from governance.service import GovernanceService
    store = _resolve_read_surface(read_surface)
    now_fn = utc_now_fn or _resolve_utc_now
    service = GovernanceService(
        store,
        utc_now=now_fn,
    )
    return service.mutation_review_projection(decision_id, identity=identity, snapshot_at=snapshot_at)


def _validate_record_sponsor_decision(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    bff_error_fn: Optional[Callable[..., Any]] = None,
    read_surface: Optional[Any] = None,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    try:
        from ..governance.service import GovernanceService
    except (ImportError, ValueError):
        from governance.service import GovernanceService

    missing = _RECORD_SPONSOR_DECISION_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for RecordSponsorDecision",
            f"Missing fields: {sorted(missing)}",
        )
    sponsor_decision = str(params.get("sponsor_decision") or "").strip().lower()
    if sponsor_decision not in _VALID_SPONSOR_DECISIONS:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid sponsor_decision value",
            f"sponsor_decision must be one of {sorted(_VALID_SPONSOR_DECISIONS)}",
        )
    rationale_ref = str(params.get("rationale_ref") or "").strip()
    if not rationale_ref:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "RecordSponsorDecision requires a non-empty rationale_ref",
            "rationale_ref must be a non-empty string",
        )
    committee_id = str(params.get("committee_id") or "").strip()
    store = _resolve_read_surface(read_surface)
    now_fn = utc_now_fn or _resolve_utc_now
    governance_service = GovernanceService(
        store,
        utc_now=now_fn,
    )
    projection = governance_service.committee_projection(
        committee_id,
        identity=identity,
        snapshot_at=now_fn(),
    )
    if projection is None:
        raise _err(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Committee board not found",
            f"Committee {committee_id} does not exist",
        )
    if projection["meta"]["surfaces"]["committee_board"] == "unavailable":
        raise _err(
            409,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "RecordSponsorDecision is blocked while the committee board is unavailable",
            "Committee evidence cannot be composed reliably",
            precondition_failed="committee_board_surface",
        )
    if not projection["allowedActions"]["canRecordSponsorDecision"]:
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "RecordSponsorDecision is not allowed for this operator and committee state",
            "allowedActions.canRecordSponsorDecision is false for the current read projection",
            precondition_failed="allowedActions.canRecordSponsorDecision",
        )


def _validate_approve_mutation(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    bff_error_fn: Optional[Callable[..., Any]] = None,
    read_surface: Optional[Any] = None,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    now_fn = utc_now_fn or _resolve_utc_now
    missing = _APPROVE_MUTATION_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for ApproveMutation",
            f"Missing fields: {sorted(missing)}",
        )
    decision_id = str(params.get("decision_id") or "").strip()
    projection = _mutation_review_projection(decision_id, identity=identity, snapshot_at=now_fn(), read_surface=read_surface, utc_now_fn=now_fn)
    if projection is None:
        raise _err(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Mutation review decision not found",
            f"Evolution decision {decision_id} does not exist",
        )
    if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
        raise _err(
            409,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "ApproveMutation is blocked while the mutation-review surface is unavailable",
            "Mutation-review evidence cannot be composed reliably",
            precondition_failed="mutation_review_surface",
        )
    if not projection["allowedActions"]["canApproveMutation"]:
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "ApproveMutation is not allowed for this operator and decision state",
            "allowedActions.canApproveMutation is false for the current read projection",
            precondition_failed="allowedActions.canApproveMutation",
        )


def _validate_reject_mutation(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    bff_error_fn: Optional[Callable[..., Any]] = None,
    read_surface: Optional[Any] = None,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    now_fn = utc_now_fn or _resolve_utc_now
    missing = _REJECT_MUTATION_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for RejectMutation",
            f"Missing fields: {sorted(missing)}",
        )
    decision_id = str(params.get("decision_id") or "").strip()
    projection = _mutation_review_projection(decision_id, identity=identity, snapshot_at=now_fn(), read_surface=read_surface, utc_now_fn=now_fn)
    if projection is None:
        raise _err(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Mutation review decision not found",
            f"Evolution decision {decision_id} does not exist",
        )
    if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
        raise _err(
            409,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "RejectMutation is blocked while the mutation-review surface is unavailable",
            "Mutation-review evidence cannot be composed reliably",
            precondition_failed="mutation_review_surface",
        )
    if not projection["allowedActions"]["canRejectMutation"]:
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "RejectMutation is not allowed for this operator and decision state",
            "allowedActions.canRejectMutation is false for the current read projection",
            precondition_failed="allowedActions.canRejectMutation",
        )


def _validate_review_mutation(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    bff_error_fn: Optional[Callable[..., Any]] = None,
    read_surface: Optional[Any] = None,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    now_fn = utc_now_fn or _resolve_utc_now
    missing = _REVIEW_MUTATION_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for ReviewMutation",
            f"Missing fields: {sorted(missing)}",
        )
    decision_id = str(params.get("decision_id") or "").strip()
    projection = _mutation_review_projection(decision_id, identity=identity, snapshot_at=now_fn(), read_surface=read_surface, utc_now_fn=now_fn)
    if projection is None:
        raise _err(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Mutation review decision not found",
            f"Evolution decision {decision_id} does not exist",
        )
    if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
        raise _err(
            409,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "ReviewMutation is blocked while the mutation-review surface is unavailable",
            "Mutation-review evidence cannot be composed reliably",
            precondition_failed="mutation_review_surface",
        )
    if not projection["allowedActions"]["canReviewMutation"]:
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "ReviewMutation is not allowed for this operator and decision state",
            "allowedActions.canReviewMutation is false for the current read projection",
            precondition_failed="allowedActions.canReviewMutation",
        )


def _validate_execute_mutation(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    bff_error_fn: Optional[Callable[..., Any]] = None,
    read_surface: Optional[Any] = None,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    now_fn = utc_now_fn or _resolve_utc_now
    missing = _EXECUTE_MUTATION_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for ExecuteMutation",
            f"Missing fields: {sorted(missing)}",
        )
    decision_id = str(params.get("decision_id") or "").strip()
    projection = _mutation_review_projection(decision_id, identity=identity, snapshot_at=now_fn(), read_surface=read_surface, utc_now_fn=now_fn)
    if projection is None:
        raise _err(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Mutation review decision not found",
            f"Evolution decision {decision_id} does not exist",
        )
    if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
        raise _err(
            409,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "ExecuteMutation is blocked while the mutation-review surface is unavailable",
            "Mutation-review evidence cannot be composed reliably",
            precondition_failed="mutation_review_surface",
        )
    if not projection["allowedActions"]["canExecuteMutation"]:
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "ExecuteMutation is not allowed for this operator and decision state",
            "allowedActions.canExecuteMutation is false for the current read projection",
            precondition_failed="allowedActions.canExecuteMutation",
        )


def _validate_remediate_sentinel_intervention(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _REMEDIATE_SENTINEL_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for RemediateSentinelIntervention",
            f"Missing fields: {sorted(missing)}",
        )
    remediation_action = str(params.get("remediation_action") or "").strip()
    if remediation_action not in _VALID_REMEDIATION_ACTIONS:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid remediation_action value",
            f"remediation_action must be one of {sorted(_VALID_REMEDIATION_ACTIONS)}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "RemediateSentinelIntervention requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_decide_v5_intervention(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _DECIDE_V5_INTERVENTION_REQUIRED - params.keys()
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for DecideV5Intervention",
            f"Missing fields: {sorted(missing)}",
            precondition_failed="decision",
        )
    decision = str(params.get("decision") or "").strip().lower()
    if decision not in _VALID_V5_INTERVENTION_DECISIONS:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid intervention decision value",
            f"decision must be one of {sorted(_VALID_V5_INTERVENTION_DECISIONS)}",
            precondition_failed="decision",
        )
    if not {"operator", "approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "DecideV5Intervention requires 'operator', 'approver', or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, approver, or admin role",
        )


def _validate_human_gate_decision(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    missing = _HUMAN_GATE_REQUIRED - {key for key, value in params.items() if value not in (None, "")}
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for HumanGate command",
            f"Missing fields: {sorted(missing)}",
            precondition_failed="human_gate",
        )

    decision = str(params.get("decision") or "").strip().lower()
    if decision not in _VALID_HUMAN_GATE_DECISIONS:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid HumanGate decision value",
            f"decision must be one of {sorted(_VALID_HUMAN_GATE_DECISIONS)}",
            precondition_failed="decision",
        )

    if decision in _HUMAN_GATE_APPROVER_DECISIONS and not {"approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "HumanGate decision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )
    if decision == "request_more_evidence" and not {"operator", "approver", "admin", "reviewer"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "HumanGate evidence request requires operator-level role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, reviewer, approver, or admin role",
        )

    if decision == "extend_ttl":
        raw_ttl = (
            params.get("ttl_seconds")
            or params.get("ttlSeconds")
            or params.get("extend_ttl_seconds")
            or params.get("extendTtlSeconds")
        )
        try:
            ttl_seconds = int(raw_ttl)
        except (TypeError, ValueError):
            ttl_seconds = 0
        if ttl_seconds <= 0:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "HumanGateExtendTtl requires a positive ttl_seconds value",
                "ttl_seconds must be a positive integer number of seconds",
                precondition_failed="ttl_seconds",
            )
        max_ttl_seconds = _human_gate_max_ttl_seconds()
        if ttl_seconds > max_ttl_seconds:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "HumanGateExtendTtl exceeds the maximum ttl_seconds cap",
                "HUMAN_GATE_TTL_EXCEEDS_CAP",
                precondition_failed="ttl_seconds",
                suggestion="Retry with a shorter HumanGate TTL extension",
                details_extra={
                    "maxTtlSeconds": max_ttl_seconds,
                    "ttlSeconds": ttl_seconds,
                    "constraint": f"ttl_seconds must be less than or equal to {max_ttl_seconds}",
                },
            )
        params["ttl_seconds"] = ttl_seconds
        params["ttlSeconds"] = ttl_seconds


def _validate_quarterly_ranking_recommendation_submit(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    if not {"operator", "approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "Quarterly ranking recommendation submission requires operator-level role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, approver, or admin role",
        )
    try:
        from ..governance.promotion_review import _raise_if_promotion_review_direct_mutation_requested
        from ..pm12.service import _pm12_resolve_quarterly_recommendation_submit_params, _PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER
    except (ImportError, ValueError):
        from governance.promotion_review import _raise_if_promotion_review_direct_mutation_requested
        from pm12.service import _pm12_resolve_quarterly_recommendation_submit_params, _PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER

    _raise_if_promotion_review_direct_mutation_requested(params)
    resolved = _pm12_resolve_quarterly_recommendation_submit_params(params)
    params.clear()
    params.update(resolved)

    required = {"quarter", "recommendation_id", "ranking_snapshot_id"}
    missing = required - {key for key, value in params.items() if value not in (None, "")}
    if missing:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing required params for QuarterlyRankingRecommendationSubmit",
            f"Missing fields: {sorted(missing)}",
            precondition_failed="quarterly_ranking_recommendation",
        )
    action_id = str(
        params.get("recommendation_action_id")
        or params.get("recommendationActionId")
        or ""
    ).strip()
    if action_id and action_id not in _PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid quarterly ranking recommendation action",
            f"recommendation_action_id must be one of {list(_PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER)}",
            precondition_failed="recommendation_action_id",
        )


def _validate_observe(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    read_surface: Optional[Any] = None,
    ops_read_model_fn: Optional[Callable[[str], Any]] = None,
    check_binding_tenant_ownership_fn: Optional[Callable[[Any, OperatorIdentity], str]] = None,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    if not {"operator", "reviewer", "approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "Observe action requires operator, reviewer, approver, or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    _enforce_ops_console_preconditions(
        params,
        identity,
        read_surface=read_surface,
        ops_read_model_fn=ops_read_model_fn,
        check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn,
        bff_error_fn=_err,
    )


def _validate_request_review(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    read_surface: Optional[Any] = None,
    ops_read_model_fn: Optional[Callable[[str], Any]] = None,
    check_binding_tenant_ownership_fn: Optional[Callable[[Any, OperatorIdentity], str]] = None,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "RequestReview action requires operator or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    persona_id = params.get("persona_id") or params.get("personaId")
    if not persona_id:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing persona_id for RequestReview",
            "persona_id must be provided to request a review",
            precondition_failed="missing_persona",
        )
    _enforce_ops_console_preconditions(
        params,
        identity,
        read_surface=read_surface,
        ops_read_model_fn=ops_read_model_fn,
        check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn,
        bff_error_fn=_err,
    )


def _validate_pause_paper_runtime(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    read_surface: Optional[Any] = None,
    ops_read_model_fn: Optional[Callable[[str], Any]] = None,
    check_binding_tenant_ownership_fn: Optional[Callable[[Any, OperatorIdentity], str]] = None,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "PausePaperRuntime action requires operator or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    runtime_id = (
        params.get("runtime_id")
        or params.get("runtimeId")
        or params.get("entity_id")
        or params.get("entityId")
    )
    if not runtime_id or not str(runtime_id).strip():
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing runtime_id for PausePaperRuntime",
            "runtime_id must be provided",
            precondition_failed="missing_runtime",
        )
    if "bounded_duration_minutes" in params and params["bounded_duration_minutes"] is not None:
        val = params["bounded_duration_minutes"]
        valid = False
        if isinstance(val, int) and not isinstance(val, bool) and val > 0:
            valid = True
        elif isinstance(val, str) and val.strip().isdigit() and int(val.strip()) > 0:
            valid = True
        if not valid:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid bounded_duration_minutes",
                "bounded_duration_minutes must be a positive integer",
                precondition_failed="bounded_duration_minutes",
            )
    _enforce_ops_console_preconditions(
        params,
        identity,
        required_bindings=["paper"],
        read_surface=read_surface,
        ops_read_model_fn=ops_read_model_fn,
        check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn,
        bff_error_fn=_err,
    )


def _validate_resume_paper_runtime(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    read_surface: Optional[Any] = None,
    ops_read_model_fn: Optional[Callable[[str], Any]] = None,
    check_binding_tenant_ownership_fn: Optional[Callable[[Any, OperatorIdentity], str]] = None,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    if not {"operator", "approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "ResumePaperRuntime action requires operator, approver, or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    runtime_id = (
        params.get("runtime_id")
        or params.get("runtimeId")
        or params.get("entity_id")
        or params.get("entityId")
    )
    if not runtime_id or not str(runtime_id).strip():
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing runtime_id for ResumePaperRuntime",
            "runtime_id must be provided",
            precondition_failed="missing_runtime",
        )
    _enforce_ops_console_preconditions(
        params,
        identity,
        required_bindings=["paper"],
        read_surface=read_surface,
        ops_read_model_fn=ops_read_model_fn,
        check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn,
        bff_error_fn=_err,
    )


def _validate_demote(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    read_surface: Optional[Any] = None,
    ops_read_model_fn: Optional[Callable[[str], Any]] = None,
    check_binding_tenant_ownership_fn: Optional[Callable[[Any, OperatorIdentity], str]] = None,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    if not {"operator", "approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "Demote action requires operator, approver, or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    persona_id = params.get("persona_id") or params.get("personaId")
    if not persona_id:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing persona_id for Demote",
            "persona_id must be provided",
            precondition_failed="missing_persona",
        )
    _enforce_ops_console_preconditions(
        params,
        identity,
        read_surface=read_surface,
        ops_read_model_fn=ops_read_model_fn,
        check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn,
        bff_error_fn=_err,
    )


def _validate_promote_candidate(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    read_surface: Optional[Any] = None,
    ops_read_model_fn: Optional[Callable[[str], Any]] = None,
    check_binding_tenant_ownership_fn: Optional[Callable[[Any, OperatorIdentity], str]] = None,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    if not {"operator", "approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "PromoteCandidate action requires operator, approver, or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    persona_id = params.get("persona_id") or params.get("personaId")
    if not persona_id:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Missing persona_id for PromoteCandidate",
            "persona_id must be provided",
            precondition_failed="missing_persona",
        )
    _enforce_ops_console_preconditions(
        params,
        identity,
        read_surface=read_surface,
        ops_read_model_fn=ops_read_model_fn,
        check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn,
        bff_error_fn=_err,
    )


def _validate_rebalance_proposal(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "RebalanceProposal action requires operator or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    raise _err(
        422,
        ErrorCode.VALIDATION_FAILED,
        "RebalanceProposal requires server-side allocation admission",
        "Submit the exact allocation evaluation through POST /bff/rebalances.",
        precondition_failed="allocation_evaluation_id",
        suggestion="Use POST /bff/management/allocation-policy/evaluate, then POST /bff/rebalances.",
    )


def _validate_approved_apply(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    read_surface: Optional[Any] = None,
    ops_read_model_fn: Optional[Callable[[str], Any]] = None,
    check_binding_tenant_ownership_fn: Optional[Callable[[Any, OperatorIdentity], str]] = None,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    if not {"operator", "approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "ApprovedApply action requires operator, approver, or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    _enforce_ops_console_preconditions(
        params,
        identity,
        read_surface=read_surface,
        ops_read_model_fn=ops_read_model_fn,
        check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn,
        bff_error_fn=_err,
    )


def _validate_emergency_containment(
    params: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    read_surface: Optional[Any] = None,
    ops_read_model_fn: Optional[Callable[[str], Any]] = None,
    check_binding_tenant_ownership_fn: Optional[Callable[[Any, OperatorIdentity], str]] = None,
    bff_error_fn: Optional[Callable[..., Any]] = None,
) -> None:
    _err = bff_error_fn or _resolve_bff_error()
    if not {"operator", "reviewer", "approver", "admin"}.intersection(identity.roles):
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "EmergencyContainment action requires operator, reviewer, approver, or admin role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )
    try:
        try:
            from ..emergency_containment_policy import validate_emergency_containment
        except (ImportError, ValueError):
            from emergency_containment_policy import validate_emergency_containment
        validate_emergency_containment(params)
    except (TypeError, ValueError) as exc:
        detail = str(exc)
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            detail[:1].upper() + detail[1:],
            detail,
            precondition_failed="emergency_containment_invalid_action",
        ) from exc

    _enforce_ops_console_preconditions(
        params,
        identity,
        read_surface=read_surface,
        ops_read_model_fn=ops_read_model_fn,
        check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn,
        bff_error_fn=_err,
    )


def build_default_validators(
    *,
    read_surface: Optional[Any] = None,
    ops_read_model_fn: Optional[Callable[[str], Any]] = None,
    check_binding_tenant_ownership_fn: Optional[Callable[[Any, OperatorIdentity], str]] = None,
    bff_error_fn: Optional[Callable[..., Any]] = None,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> Dict[CommandType, Callable[..., None]]:
    return {
        CommandType.APPROVE_DEPLOYMENT: lambda p, i: _validate_approve_deployment(p, i, bff_error_fn=bff_error_fn),
        CommandType.APPROVE_DECISION: lambda p, i: _validate_approve_decision(p, i, bff_error_fn=bff_error_fn),
        CommandType.REJECT_DECISION: lambda p, i: _validate_reject_decision(p, i, bff_error_fn=bff_error_fn),
        CommandType.REQUEST_APPROVAL_REVISION: lambda p, i: _validate_request_approval_revision(p, i, bff_error_fn=bff_error_fn),
        CommandType.PAUSE_RUNTIME: lambda p, i: _validate_pause_runtime(p, i, bff_error_fn=bff_error_fn),
        CommandType.PAUSE_EXECUTION: lambda p, i: _validate_pause_execution(p, i, bff_error_fn=bff_error_fn),
        CommandType.ESCALATE_DIFF: lambda p, i: _validate_escalate_diff(p, i, bff_error_fn=bff_error_fn),
        CommandType.ISSUE_RISK_OFF: lambda p, i: _validate_issue_risk_off(p, i, bff_error_fn=bff_error_fn),
        CommandType.LIQUIDATE_ALL: lambda p, i: _validate_liquidate_all(p, i, bff_error_fn=bff_error_fn),
        CommandType.HARD_ROLLBACK: lambda p, i: _validate_hard_rollback(p, i, bff_error_fn=bff_error_fn),
        CommandType.ISSUE_SAFE_MODE: lambda p, i: _validate_issue_safe_mode(p, i, bff_error_fn=bff_error_fn),
        CommandType.EXECUTE_ROLLBACK: lambda p, i: _validate_execute_rollback(p, i, bff_error_fn=bff_error_fn),
        CommandType.APPROVE_ROLLBACK: lambda p, i: _validate_approve_rollback(p, i, bff_error_fn=bff_error_fn),
        CommandType.REJECT_ROLLBACK: lambda p, i: _validate_reject_rollback(p, i, bff_error_fn=bff_error_fn),
        CommandType.ACTIVATE_KILL_SWITCH: lambda p, i: _validate_activate_kill_switch(p, i, bff_error_fn=bff_error_fn),
        CommandType.APPROVE_EVOLUTION_DECISION: lambda p, i: _validate_approve_evolution_decision(p, i, bff_error_fn=bff_error_fn),
        CommandType.EXECUTE_EVOLUTION_ACTION: lambda p, i: _validate_execute_evolution_action(p, i, bff_error_fn=bff_error_fn),
        CommandType.APPROVE_MUTATION: lambda p, i: _validate_approve_mutation(p, i, bff_error_fn=bff_error_fn, read_surface=read_surface, utc_now_fn=utc_now_fn),
        CommandType.REJECT_MUTATION: lambda p, i: _validate_reject_mutation(p, i, bff_error_fn=bff_error_fn, read_surface=read_surface, utc_now_fn=utc_now_fn),
        CommandType.REVIEW_MUTATION: lambda p, i: _validate_review_mutation(p, i, bff_error_fn=bff_error_fn, read_surface=read_surface, utc_now_fn=utc_now_fn),
        CommandType.EXECUTE_MUTATION: lambda p, i: _validate_execute_mutation(p, i, bff_error_fn=bff_error_fn, read_surface=read_surface, utc_now_fn=utc_now_fn),
        CommandType.RECORD_SPONSOR_DECISION: lambda p, i: _validate_record_sponsor_decision(p, i, bff_error_fn=bff_error_fn, read_surface=read_surface, utc_now_fn=utc_now_fn),
        CommandType.REMEDIATE_SENTINEL_INTERVENTION: lambda p, i: _validate_remediate_sentinel_intervention(p, i, bff_error_fn=bff_error_fn),
        CommandType.DECIDE_V5_INTERVENTION: lambda p, i: _validate_decide_v5_intervention(p, i, bff_error_fn=bff_error_fn),
        CommandType.HUMAN_GATE_APPROVE: lambda p, i: _validate_human_gate_decision(p, i, bff_error_fn=bff_error_fn),
        CommandType.HUMAN_GATE_REJECT: lambda p, i: _validate_human_gate_decision(p, i, bff_error_fn=bff_error_fn),
        CommandType.HUMAN_GATE_REQUEST_MORE_EVIDENCE: lambda p, i: _validate_human_gate_decision(p, i, bff_error_fn=bff_error_fn),
        CommandType.HUMAN_GATE_REVOKE: lambda p, i: _validate_human_gate_decision(p, i, bff_error_fn=bff_error_fn),
        CommandType.HUMAN_GATE_EXTEND_TTL: lambda p, i: _validate_human_gate_decision(p, i, bff_error_fn=bff_error_fn),
        CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT: lambda p, i: _validate_quarterly_ranking_recommendation_submit(p, i, bff_error_fn=bff_error_fn),
        CommandType.OBSERVE: lambda p, i: _validate_observe(p, i, read_surface=read_surface, ops_read_model_fn=ops_read_model_fn, check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn, bff_error_fn=bff_error_fn),
        CommandType.REQUEST_REVIEW: lambda p, i: _validate_request_review(p, i, read_surface=read_surface, ops_read_model_fn=ops_read_model_fn, check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn, bff_error_fn=bff_error_fn),
        CommandType.PAUSE_PAPER_RUNTIME: lambda p, i: _validate_pause_paper_runtime(p, i, read_surface=read_surface, ops_read_model_fn=ops_read_model_fn, check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn, bff_error_fn=bff_error_fn),
        CommandType.RESUME_PAPER_RUNTIME: lambda p, i: _validate_resume_paper_runtime(p, i, read_surface=read_surface, ops_read_model_fn=ops_read_model_fn, check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn, bff_error_fn=bff_error_fn),
        CommandType.DEMOTE: lambda p, i: _validate_demote(p, i, read_surface=read_surface, ops_read_model_fn=ops_read_model_fn, check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn, bff_error_fn=bff_error_fn),
        CommandType.PROMOTE_CANDIDATE: lambda p, i: _validate_promote_candidate(p, i, read_surface=read_surface, ops_read_model_fn=ops_read_model_fn, check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn, bff_error_fn=bff_error_fn),
        CommandType.REBALANCE_PROPOSAL: lambda p, i: _validate_rebalance_proposal(p, i, bff_error_fn=bff_error_fn),
        CommandType.APPROVED_APPLY: lambda p, i: _validate_approved_apply(p, i, read_surface=read_surface, ops_read_model_fn=ops_read_model_fn, check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn, bff_error_fn=bff_error_fn),
        CommandType.EMERGENCY_CONTAINMENT: lambda p, i: _validate_emergency_containment(p, i, read_surface=read_surface, ops_read_model_fn=ops_read_model_fn, check_binding_tenant_ownership_fn=check_binding_tenant_ownership_fn, bff_error_fn=bff_error_fn),
    }


_VALIDATORS = build_default_validators()
VALIDATORS = _VALIDATORS
