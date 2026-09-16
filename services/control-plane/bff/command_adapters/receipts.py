"""Command submission receipts, response projections, and error envelopes.

This module encapsulates response building, dual-write receipt projection,
error normalization, and foundation error envelope construction.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from services.foundation import (
    AuditAction,
    CommandEnvelope,
    ErrorEnvelope,
    ErrorKind,
    IdempotencyRecord,
    PolicyDecision,
    PolicyDecisionValue,
    foundation_id,
)
try:
    from ..auth.policy import bff_error as _auth_bff_error
    from ..models import (
        ActionCommandStatus,
        CommandReceipt,
        CommandReceiptStatus,
        CommandResponse,
        CommandResultMeta,
        CommandRoutingPath,
        CommandStatus,
        CommandSubmissionResponse,
        CommandType,
        ErrorCode,
        OperatorIdentity,
        StalenessWarning,
    )
except (ImportError, ValueError):
    from auth.policy import bff_error as _auth_bff_error
    from models import (
        ActionCommandStatus,
        CommandReceipt,
        CommandReceiptStatus,
        CommandResponse,
        CommandResultMeta,
        CommandRoutingPath,
        CommandStatus,
        CommandSubmissionResponse,
        CommandType,
        ErrorCode,
        OperatorIdentity,
        StalenessWarning,
    )
from .contracts import foundation_route_metadata

_BFF_FOUNDATION_POLICY_VERSION = "2026-04-27"
_FOUNDATION_COMMAND_ROUTE = "POST /api/v1/operator/commands"

_COMMAND_RECEIPT_STATUS_MAP = {
    CommandStatus.SUBMITTED.value: CommandReceiptStatus.ACCEPTED,
    CommandStatus.PROCESSING.value: CommandReceiptStatus.QUEUED,
    CommandStatus.EXECUTED.value: CommandReceiptStatus.QUEUED,
    CommandStatus.FAILED.value: CommandReceiptStatus.FAILED,
    CommandStatus.TIMEOUT.value: CommandReceiptStatus.FAILED,
}

_ACTION_COMMAND_STATUS_MAP = {
    CommandStatus.SUBMITTED.value: ActionCommandStatus.ACCEPTED,
    CommandStatus.PROCESSING.value: ActionCommandStatus.QUEUED,
    CommandStatus.EXECUTED.value: ActionCommandStatus.COMPLETED,
}


def command_response_durable_meta(idempotency_key: str, *, replayed: bool) -> Dict[str, Any]:
    return {
        "durable": True,
        "liveCapitalSideEffects": False,
        "idempotency": {
            "key": idempotency_key,
            "idempotencyKey": idempotency_key,
            "replayed": replayed,
        },
    }


def command_response_dry_run_meta(idempotency_key: str) -> Dict[str, Any]:
    return {
        "dryRun": True,
        "durable": False,
        "liveCapitalSideEffects": False,
        "idempotency": {
            "key": idempotency_key,
            "idempotencyKey": idempotency_key,
            "replayed": False,
        },
    }


def command_dual_write_receipts(
    *,
    command_id: str,
    command: str,
    status: str,
    accepted_at: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    tracking_url = f"/api/v1/operator/commands/{command_id}"
    action_receipt = {
        "receipt_type": "action",
        "id": command_id,
        "receipt_id": command_id,
        "command_id": command_id,
        "status": status,
        "trackingUrl": tracking_url,
        "tracking_url": tracking_url,
    }
    command_receipt = {
        "receipt_type": "command",
        "receipt_id": command_id,
        "command_id": command_id,
        "command": command,
        "status": status,
        "trackingUrl": tracking_url,
        "tracking_url": tracking_url,
    }
    if accepted_at:
        action_receipt["accepted_at"] = accepted_at
        command_receipt["accepted_at"] = accepted_at
    return {
        "action_receipt": action_receipt,
        "command_receipt": command_receipt,
    }


def command_runtime_auth_context(
    *,
    command_id: str,
    authorization: Optional[str],
    mfa_token: Optional[str],
    identity: OperatorIdentity,
    auth_context_sink: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization[len("Bearer "):]
    effective_mfa_token = mfa_token or ("000000" if identity.mfa_verified else None)
    if (raw_token or effective_mfa_token) and auth_context_sink is not None:
        auth_context_sink[command_id] = {
            "auth_token": raw_token,
            "mfa_token": effective_mfa_token,
        }
    return {
        "token_kind": identity.token_kind,
        "bearer_token_present": bool(raw_token),
        "mfa_token_present": bool(effective_mfa_token),
    }


def expected_completion_at(accepted_at: str, estimated_processing_time_ms: int = 500) -> Optional[str]:
    if not accepted_at or estimated_processing_time_ms < 0:
        return None
    try:
        parsed = datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    completed_at = parsed + timedelta(milliseconds=estimated_processing_time_ms)
    return completed_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def project_command_submission_response(
    *,
    command_id: str,
    command: CommandType,
    accepted_at: str,
    status: CommandStatus,
    staleness_warning: Optional[StalenessWarning],
) -> CommandSubmissionResponse:
    receipt_status = _COMMAND_RECEIPT_STATUS_MAP.get(status.value, CommandReceiptStatus.FAILED)
    meta = CommandResultMeta()
    receipt = CommandReceipt(
        receipt_id=command_id,
        command_id=command_id,
        command=command.value,
        status=receipt_status,
        accepted_at=accepted_at,
        routing_path=CommandRoutingPath.DIRECT,
        expected_completion_at=expected_completion_at(
            accepted_at,
            meta.estimated_processing_time_ms,
        ),
        error_message=None,
    )
    return CommandSubmissionResponse(
        receipt_id=command_id,
        command=command.value,
        status=receipt_status,
        accepted_at=accepted_at,
        routing_path=CommandRoutingPath.DIRECT,
        expected_completion_at=receipt.expected_completion_at,
        error_message=None,
        staleness_warning=staleness_warning,
        receipt=receipt,
    )


def action_command_status_from_command_status(status: CommandStatus) -> ActionCommandStatus:
    try:
        return _ACTION_COMMAND_STATUS_MAP[status.value]
    except KeyError as exc:
        raise ValueError(
            f"Command status {status.value!r} cannot be projected as a successful CommandResponse"
        ) from exc


def project_final_command_response(
    *,
    command_id: str,
    command: CommandType,
    accepted_at: str,
    status: CommandStatus,
    staleness_warning: Optional[StalenessWarning],
    meta: Optional[Dict[str, Any]] = None,
    deprecation: Optional[Dict[str, Any]] = None,
) -> CommandResponse[Dict[str, Any]]:
    final_status = action_command_status_from_command_status(status)
    legacy_payload = project_command_submission_response(
        command_id=command_id,
        command=command,
        accepted_at=accepted_at,
        status=status,
        staleness_warning=staleness_warning,
    ).model_dump()
    legacy_payload["status"] = final_status.value
    tracking_url = f"/api/v1/operator/commands/{command_id}"
    legacy_payload["command_id"] = command_id
    legacy_payload["commandId"] = command_id
    legacy_payload["tracking_url"] = tracking_url
    legacy_payload["trackingUrl"] = tracking_url
    if isinstance(legacy_payload.get("receipt"), dict):
        legacy_payload["receipt"]["status"] = final_status.value
        legacy_payload["receipt"]["tracking_url"] = tracking_url
        legacy_payload["receipt"]["trackingUrl"] = tracking_url
    receipts = command_dual_write_receipts(
        command_id=command_id,
        command=command.value,
        status=final_status.value,
        accepted_at=accepted_at,
    )
    legacy_payload["receipt_dual_write"] = receipts
    legacy_payload["action_receipt"] = receipts["action_receipt"]
    legacy_payload["actionReceipt"] = receipts["action_receipt"]
    legacy_payload["command_receipt"] = receipts["command_receipt"]
    legacy_payload["commandReceipt"] = receipts["command_receipt"]
    final_meta = dict(meta or {})
    if deprecation:
        legacy_payload["deprecated"] = True
        legacy_payload["deprecation"] = dict(deprecation)
        if isinstance(legacy_payload.get("receipt"), dict):
            legacy_payload["receipt"]["deprecated"] = True
            legacy_payload["receipt"]["deprecation"] = dict(deprecation)
        final_meta["deprecated"] = True
        final_meta["deprecation"] = dict(deprecation)
    return CommandResponse[Dict[str, Any]](
        status=final_status,
        data=legacy_payload,
        meta=final_meta or None,
    )


def _canonical_error_code_value(code: Any, status_code: Optional[int] = None) -> str:
    raw = code.value if hasattr(code, "value") else str(code or "").strip()
    if any(raw == e.value for e in ErrorCode):
        return raw
    if status_code == 400:
        return ErrorCode.VALIDATION_FAILED.value
    if status_code == 401:
        return ErrorCode.AUTH_REQUIRED.value
    if status_code == 403:
        return ErrorCode.FORBIDDEN.value
    if status_code == 404:
        return ErrorCode.RESOURCE_NOT_FOUND.value
    if status_code == 409:
        return ErrorCode.RESOURCE_CONFLICT.value
    if status_code == 422:
        return ErrorCode.VALIDATION_FAILED.value
    if status_code == 428:
        return ErrorCode.CONFIRMATION_REQUIRED.value
    if status_code and status_code >= 500:
        return ErrorCode.INTERNAL_ERROR.value
    return ErrorCode.VALIDATION_FAILED.value


def extract_error_fields(exc: HTTPException) -> Dict[str, Any]:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error = detail.get("error") if isinstance(detail.get("error"), dict) else {}
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    details_extra = {
        key: value
        for key, value in details.items()
        if key not in {"reason", "precondition_failed", "suggestion"} and value is not None
    }
    code_value = _canonical_error_code_value(
        error.get("code") or ErrorCode.VALIDATION_FAILED.value,
        status_code=exc.status_code,
    )
    try:
        code = ErrorCode(code_value)
    except ValueError:
        code = ErrorCode.VALIDATION_FAILED
    return {
        "status_code": exc.status_code,
        "code": code,
        "message": error.get("message") or str(exc.detail),
        "reason": details.get("reason") or str(exc.detail),
        "precondition_failed": details.get("precondition_failed"),
        "suggestion": details.get("suggestion"),
        "details_extra": details_extra,
        "correlation_id": detail.get("correlationId") or details_extra.get("correlationId"),
    }


def foundation_bff_error(
    exc: HTTPException,
    *,
    foundation_context: Dict[str, Any],
) -> HTTPException:
    fields = extract_error_fields(exc)
    command_envelope: CommandEnvelope = foundation_context["command_envelope"]
    admission_route = str(foundation_context.get("admission_route") or _FOUNDATION_COMMAND_ROUTE)
    source_route = str(foundation_context.get("source_route") or "").strip() or None
    route_metadata = foundation_route_metadata(admission_route, source_route)
    if fields["status_code"] == 403:
        policy_decision = PolicyDecision.make(
            policy_id="bff.command.admission",
            policy_version=_BFF_FOUNDATION_POLICY_VERSION,
            decision=PolicyDecisionValue.DENY,
            actor_ref=command_envelope.actor_ref,
            action=command_envelope.command_type,
            target_ref=command_envelope.authority_scope.target_ref,
            environment=command_envelope.authority_scope.environment,
            trace_id=command_envelope.trace.trace_id,
            reasons=[fields["reason"]],
        )
        foundation_error = ErrorEnvelope.policy_denial(
            message=fields["message"],
            trace=command_envelope.trace,
            policy_decision_ref=policy_decision.decision_id,
            details={
                "reason": fields["reason"],
                "precondition_failed": fields["precondition_failed"],
                **fields["details_extra"],
            },
        )
        audit_action = AuditAction.record(
            actor_ref=command_envelope.actor_ref,
            action_type="bff.command.policy_denied",
            target_ref=command_envelope.authority_scope.target_ref,
            environment=command_envelope.authority_scope.environment,
            reason=fields["reason"],
            trace=command_envelope.trace,
            payload=foundation_context["request_payload"],
            policy_decision_ref=policy_decision.decision_id,
            metadata=route_metadata,
        )
        return _auth_bff_error(
            fields["status_code"],
            fields["code"],
            fields["message"],
            fields["reason"],
            precondition_failed=fields["precondition_failed"],
            suggestion=fields["suggestion"],
            details_extra=fields["details_extra"],
            correlation_id=fields["correlation_id"],
            foundation_error=foundation_error,
            policy_decision=policy_decision,
            audit_action=audit_action,
        )

    if fields["status_code"] in {400, 422}:
        foundation_error = ErrorEnvelope.validation(
            message=fields["message"],
            trace=command_envelope.trace,
            error_code=fields["code"].value,
            details={
                "reason": fields["reason"],
                "precondition_failed": fields["precondition_failed"],
                **fields["details_extra"],
            },
        )
    else:
        foundation_error = ErrorEnvelope(
            error_id=foundation_id("err"),
            error_code=fields["code"].value,
            message=fields["message"],
            error_kind=ErrorKind.INVARIANT_VIOLATION,
            trace=command_envelope.trace,
            status_code=fields["status_code"],
            details={
                "reason": fields["reason"],
                "precondition_failed": fields["precondition_failed"],
                **fields["details_extra"],
            },
        )
    audit_action = AuditAction.record(
        actor_ref=command_envelope.actor_ref,
        action_type="bff.command.rejected",
        target_ref=command_envelope.authority_scope.target_ref,
        environment=command_envelope.authority_scope.environment,
        reason=fields["reason"],
        trace=command_envelope.trace,
        payload=foundation_context["request_payload"],
        metadata=route_metadata,
    )
    return _auth_bff_error(
        fields["status_code"],
        fields["code"],
        fields["message"],
        fields["reason"],
        precondition_failed=fields["precondition_failed"],
        suggestion=fields["suggestion"],
        details_extra=fields["details_extra"],
        correlation_id=fields["correlation_id"],
        foundation_error=foundation_error,
        audit_action=audit_action,
    )


def foundation_idempotency_conflict_error(
    *,
    foundation_context: Dict[str, Any],
    existing_command_id: str,
) -> HTTPException:
    command_envelope: CommandEnvelope = foundation_context["command_envelope"]
    idempotency_record: IdempotencyRecord = foundation_context["idempotency_record"]
    admission_route = str(foundation_context.get("admission_route") or _FOUNDATION_COMMAND_ROUTE)
    source_route = str(foundation_context.get("source_route") or "").strip() or None
    message = "Idempotency key was already used with a different command payload"
    reason = (
        f"idempotency_key={idempotency_record.idempotency_key} is already bound "
        f"to command {existing_command_id}"
    )
    foundation_error = ErrorEnvelope(
        error_id=foundation_id("err"),
        error_code=ErrorCode.IDEMPOTENCY_CONFLICT.value,
        message=message,
        error_kind=ErrorKind.IDEMPOTENCY_CONFLICT,
        trace=command_envelope.trace,
        status_code=409,
        details={
            "reason": reason,
            "existing_command_id": existing_command_id,
            "idempotency_key": idempotency_record.idempotency_key,
        },
    )
    audit_action = AuditAction.record(
        actor_ref=command_envelope.actor_ref,
        action_type="bff.command.idempotency_conflict",
        target_ref=command_envelope.authority_scope.target_ref,
        environment=command_envelope.authority_scope.environment,
        reason=reason,
        trace=command_envelope.trace,
        payload=foundation_context["request_payload"],
        metadata=foundation_route_metadata(admission_route, source_route),
    )
    return _auth_bff_error(
        409,
        ErrorCode.IDEMPOTENCY_CONFLICT,
        message,
        reason,
        precondition_failed="idempotency_conflict",
        suggestion="Reuse the original payload for this key or submit with a new X-Idempotency-Key",
        foundation_error=foundation_error,
        audit_action=audit_action,
    )
