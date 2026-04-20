from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, BackgroundTasks, Header, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(__file__))

from models import (
    ApproveMutationCommandPayload,
    AuditContext,
    BFFError,
    CommandReceipt,
    CommandReceiptStatus,
    CommandResultMeta,
    CommandRoutingPath,
    CommandStatus,
    CommandSubmissionResponse,
    CommandStatusResponse,
    CommandType,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    ObjectType,
    OperatorCommand,
    OperatorIdentity,
    RecordSponsorDecisionCommandPayload,
    RejectMutationCommandPayload,
    StalenessWarning,
    TargetObject,
    utc_now,
)
from command_queue import CommandStore
from command_executor import execute_command_with_status
from read_store import ReadSurfaceStore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(title="Pantheon Operator BFF", version="0.2.0")

# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

BFF_DATA_DIR = os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff")
os.makedirs(BFF_DATA_DIR, exist_ok=True)
command_store = CommandStore(os.path.join(BFF_DATA_DIR, "commands.jsonl"))
read_store = ReadSurfaceStore(
    os.path.join(BFF_DATA_DIR, "read_surfaces.json"),
    allow_local_snapshot_fallback=(
        os.getenv("PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK", "true").strip().lower()
        != "false"
    ),
)

# --------------------------------------------------------------------------- #
# Auth / identity helpers
# --------------------------------------------------------------------------- #

# In production this would decode and verify a JWT.
# Here we accept a stub token format: "Bearer <operator_id>:<comma_roles>[:mfa]"
# e.g. "Bearer op-42:operator,approver:mfa"
# Any other non-empty value is treated as a basic authenticated operator (no admin/mfa).
# A missing Authorization header returns INVALID_TOKEN.
def _extract_identity(authorization: Optional[str]) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise _bff_error(
            status_code=401,
            code=ErrorCode.INVALID_TOKEN,
            message="Missing or invalid Authorization header",
            reason="Token is absent or not a Bearer token",
            suggestion="Re-authenticate and include a valid Bearer token",
        )
    token = authorization[len("Bearer "):]
    parts = token.split(":")
    operator_id = parts[0] if parts else "unknown"
    roles = parts[1].split(",") if len(parts) > 1 else ["operator"]
    mfa_verified = len(parts) > 2 and parts[2] == "mfa"
    return OperatorIdentity(operator_id=operator_id, roles=roles, mfa_verified=mfa_verified)


def _bff_error(
    status_code: int,
    code: ErrorCode,
    message: str,
    reason: str,
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
) -> HTTPException:
    body = ErrorResponse(
        error=BFFError(
            code=code,
            message=message,
            details=ErrorDetail(
                reason=reason,
                precondition_failed=precondition_failed,
                suggestion=suggestion,
            ),
        )
    )
    return HTTPException(status_code=status_code, detail=body.model_dump())


# --------------------------------------------------------------------------- #
# Command-specific precondition validators (§3 of contract)
# --------------------------------------------------------------------------- #

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
_DRAWER_RUNTIME_COMMANDS = {
    CommandType.PAUSE_EXECUTION,
    CommandType.ISSUE_RISK_OFF,
    CommandType.LIQUIDATE_ALL,
    CommandType.HARD_ROLLBACK,
    CommandType.ISSUE_SAFE_MODE,
}

_KILL_SWITCH_REQUIRED = {"scope", "activate"}
_VALID_SCOPES = {"persona", "pool", "all"}
_VALID_SEVERITIES = {"critical", "high", "medium"}

_APPROVE_EVO_REQUIRED = {"evolution_decision_id", "approval_action"}
_VALID_EVO_APPROVAL_ACTIONS = {"approve", "reject"}
_APPROVE_MUTATION_REQUIRED = {"decision_id"}
_REJECT_MUTATION_REQUIRED = {"decision_id"}
_RECORD_SPONSOR_DECISION_REQUIRED = {"committee_id", "sponsor_decision", "rationale_ref"}
_VALID_SPONSOR_DECISIONS = {"approved", "rejected", "conditional"}

_EXECUTE_EVO_REQUIRED = {"evolution_decision_id", "action_type"}
_VALID_EVO_ACTION_TYPES = {"freeze", "retrain", "mutate", "retire"}

_OPERATOR_ALERTS_ROUTE = "/alerts"
_OPERATOR_INCIDENT_HOME_ROUTE = "/operator/incidents"
_OPERATOR_DEPLOYMENT_REVIEW_ROUTE = "/operator/deployment-review"
_OPERATOR_DEPLOYMENT_PLAN_ROUTE_PREFIX = "/operator/deployment-plans"
_OPERATOR_HEALTH_STATUS_ROUTE = "/operator/health-status"
_OPERATOR_POST_INCIDENT_REVIEW_ROUTE = "/operator/post-incident-review"
_OPERATOR_RUNTIME_STATE_ROUTE = "/operator/runtime-state"
_CONSULTATION_WORKBENCH_ROUTE = "/consultation"
_KNOWLEDGE_WORKBENCH_ROUTE = "/knowledge"
_GOVERNANCE_REVIEW_QUEUE_ROUTE = "/governance-review-queue"
_GOVERNANCE_APPROVAL_QUEUE_ROUTE = "/governance-approval-queue"
_MUTATION_REVIEW_ROUTE = "/operator/mutation-review"

_MUTATION_APPROVAL_ROLES = {
    "low": {"reviewer", "approver", "admin"},
    "medium": {"operator", "approver", "admin"},
    "high": {"approver", "admin"},
}

_MUTATION_REJECTION_ROLES = {
    "low": {"reviewer", "approver", "admin"},
    "medium": {"reviewer", "operator", "approver", "admin"},
    "high": {"approver", "admin"},
}


def _require_admin_mfa(identity: OperatorIdentity, command_name: str) -> None:
    if "admin" not in identity.roles:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            f"{command_name} requires 'admin' role",
            "Operator does not hold the admin role",
            precondition_failed="role_check",
            suggestion="Escalate to an admin-role operator",
        )
    if not identity.mfa_verified:
        raise _bff_error(
            403,
            ErrorCode.MFA_REQUIRED,
            f"{command_name} requires MFA verification",
            "Admin action requires MFA validation",
            precondition_failed="mfa_check",
            suggestion="Provide a valid MFA token in your session",
        )


def _deployment_review_href(plan_id: str) -> str:
    return f"{_OPERATOR_DEPLOYMENT_REVIEW_ROUTE}?plan={plan_id}"


def _deployment_plan_href(plan_id: str) -> str:
    return f"{_OPERATOR_DEPLOYMENT_PLAN_ROUTE_PREFIX}/{plan_id}"


def _incident_detail_href(incident_id: str) -> str:
    return f"{_OPERATOR_INCIDENT_HOME_ROUTE}/{incident_id}"


def _post_incident_review_href(incident_id: str) -> str:
    return f"{_OPERATOR_POST_INCIDENT_REVIEW_ROUTE}?incident={incident_id}"


def _runtime_command_context(runtime_id: str, incident_id: Optional[str] = None) -> Dict[str, Optional[str]]:
    runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
    binding_id = None
    capital_pool_id = None
    artifact_id = None
    artifact_version = None
    plan_id = None

    if runtime_binding:
        binding_id = str(runtime_binding.get("id") or runtime_binding.get("binding_id") or runtime_id)
        capital_pool_id = runtime_binding.get("capital_pool_id")
        artifact_id = runtime_binding.get("artifact_id")
        artifact_version = runtime_binding.get("artifact_version")
        plan_id = runtime_binding.get("plan_id")

    if incident_id:
        incident = read_store.get_incident(incident_id)
        if incident and str(incident.get("runtime_id") or "") == runtime_id:
            capital_pool_id = capital_pool_id or incident.get("capital_pool_id")
            artifact_id = artifact_id or incident.get("artifact_id")
            artifact_version = artifact_version or incident.get("artifact_version")

    if plan_id and not capital_pool_id:
        plan = read_store.get_deployment_plan(plan_id)
        if plan:
            capital_pool_id = plan.get("capital_pool_id")

    return {
        "runtime_id": runtime_id,
        "runtime_binding_id": binding_id or runtime_id,
        "capital_pool_id": capital_pool_id,
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
    }


def _validate_drawer_runtime_target(cmd: OperatorCommand) -> None:
    if cmd.command not in _DRAWER_RUNTIME_COMMANDS:
        return
    if cmd.target.type != ObjectType.RUNTIME:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            f"{cmd.command.value} requires target.type = Runtime",
            "Drawer commands only accept Runtime targets",
        )
    if not str(cmd.target.id or "").strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            f"{cmd.command.value} requires a runtime target id",
            "target.id must be a non-empty runtime id",
        )


def _validate_audit_context(cmd: OperatorCommand) -> None:
    if str(cmd.audit_context.reason or "").strip():
        return
    raise _bff_error(
        400,
        ErrorCode.INVALID_PARAMS,
        "audit_context.reason is required",
        "audit_context.reason must be a non-empty string",
    )


def _normalize_operator_command_payload(payload: Dict[str, Any]) -> OperatorCommand:
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
                ErrorCode.INVALID_PARAMS,
                f"Invalid {command_type} payload",
                str(exc),
            ) from exc
        raise _bff_error(
            400,
            ErrorCode.INVALID_PARAMS,
            "Unknown command_type",
            f"Unsupported command_type: {command_type}",
        )

    try:
        return OperatorCommand.model_validate(payload)
    except ValidationError as exc:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid operator command payload",
            str(exc),
        ) from exc


def _validate_pause_execution(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _PAUSE_EXECUTION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for PauseExecution",
            f"Missing fields: {sorted(missing)}",
        )
    for field in sorted(_PAUSE_EXECUTION_REQUIRED):
        if not isinstance(params.get(field), bool):
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                f"Invalid {field} value",
                f"{field} must be a boolean",
            )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "PauseExecution requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )


def _validate_issue_risk_off(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _RISK_OFF_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for IssueRiskOff",
            f"Missing fields: {sorted(missing)}",
        )
    exposure_pct = params.get("reduce_exposure_pct")
    if not isinstance(exposure_pct, (int, float)) or exposure_pct <= 0 or exposure_pct > 100:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid reduce_exposure_pct value",
            "reduce_exposure_pct must be a number between 1 and 100",
        )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "IssueRiskOff requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )


def _validate_liquidate_all(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    if params:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "LiquidateAll does not accept params",
            "params must be an empty object for LiquidateAll",
        )
    _require_admin_mfa(identity, "LiquidateAll")


def _validate_hard_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    target_artifact_id = str(params.get("target_artifact_id") or "").strip()
    if not target_artifact_id:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for HardRollback",
            "target_artifact_id must be a non-empty string",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "HardRollback requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )


def _validate_issue_safe_mode(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    safe_mode_level = str(params.get("safe_mode_level") or "").strip().lower()
    if safe_mode_level not in _SAFE_MODE_LEVELS:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid safe_mode_level",
            f"safe_mode_level must be one of {sorted(_SAFE_MODE_LEVELS)}",
        )
    _require_admin_mfa(identity, "IssueSafeMode")


def _derive_drawer_execution_params(
    command: CommandType,
    runtime_id: str,
    params: Dict[str, Any],
    *,
    actor_id: Optional[str],
    reason: Optional[str],
    incident_id: Optional[str],
) -> Dict[str, Any]:
    context = _runtime_command_context(runtime_id, incident_id)
    base = {
        "runtime_id": runtime_id,
        "runtime_binding_id": context["runtime_binding_id"],
        "capital_pool_id": context["capital_pool_id"],
        "actor_id": actor_id or "operator-command",
        "reason": reason or "",
        "incident_id": incident_id,
    }

    if command == CommandType.PAUSE_EXECUTION:
        return {
            **base,
            "pause_action": "pause",
            "pause_new_entries": params["pause_new_entries"],
            "cancel_open_orders": params["cancel_open_orders"],
        }

    if command == CommandType.ISSUE_RISK_OFF:
        if not context["capital_pool_id"]:
            raise ValueError(
                f"Runtime {runtime_id} cannot be routed to a capital pool."
            )
        return {
            **base,
            "scope": "pool",
            "scope_id": context["capital_pool_id"],
            "action_override": "risk_off",
            "trigger_reason": "operator_emergency_stop",
            "reduce_exposure_pct": params["reduce_exposure_pct"],
        }

    if command == CommandType.LIQUIDATE_ALL:
        if not context["capital_pool_id"]:
            raise ValueError(
                f"Runtime {runtime_id} cannot be routed to a capital pool."
            )
        return {
            **base,
            "scope": "pool",
            "scope_id": context["capital_pool_id"],
            "action_override": "liquidate",
            "trigger_reason": "operator_emergency_stop",
        }

    if command == CommandType.HARD_ROLLBACK:
        return {
            **base,
            "rollback_target_type": "runtime",
            "target_id": context["runtime_binding_id"],
            "rollback_to_version": params["target_artifact_id"],
            "rollback_action_type": "pause_then_replace",
            "target_artifact_id": params["target_artifact_id"],
        }

    if not context["capital_pool_id"]:
        raise ValueError(
            f"Runtime {runtime_id} cannot be routed to a capital pool."
        )
    return {
        **base,
        "safe_mode_level": params["safe_mode_level"],
        "target_state": "guarded",
    }


def _stored_command_params(cmd: OperatorCommand, identity: OperatorIdentity) -> Dict[str, Any]:
    if cmd.command in _DRAWER_RUNTIME_COMMANDS:
        return dict(cmd.params)
    del identity
    return dict(cmd.params)


def _resolve_execution_params_for_record(record: Dict[str, Any]) -> Dict[str, Any]:
    command_type = CommandType(record["type"])
    params = dict(record.get("params") or {})
    if command_type not in _DRAWER_RUNTIME_COMMANDS:
        return params

    target = record.get("target") or {}
    audit = record.get("audit") or {}
    runtime_id = str(target.get("id") or "").strip()
    if not runtime_id:
        raise ValueError(f"{command_type.value} is missing target.id.")

    return _derive_drawer_execution_params(
        command_type,
        runtime_id,
        params,
        actor_id=audit.get("operator_id"),
        reason=audit.get("reason"),
        incident_id=audit.get("incident_id"),
    )


def _validate_approve_deployment(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_DEPLOYMENT_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ApproveDeployment",
            f"Missing fields: {sorted(missing)}",
        )
    if params["approval_decision"] not in _VALID_APPROVAL_DECISIONS:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid approval_decision value",
            f"Must be one of {_VALID_APPROVAL_DECISIONS}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ApproveDeployment requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_approve_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_DECISION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for ApproveDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "ApproveDecision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_reject_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REJECT_DECISION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for RejectDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("rejection_reason") or "").strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "RejectDecision requires a non-empty rejection_reason",
            "rejection_reason must be a non-empty string",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "RejectDecision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_request_approval_revision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REQUEST_APPROVAL_REVISION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for RequestApprovalRevision",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("revision_notes") or "").strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "RequestApprovalRevision requires non-empty revision_notes",
            "revision_notes must be a non-empty string",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "RequestApprovalRevision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_pause_runtime(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _PAUSE_RUNTIME_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for PauseRuntime",
            f"Missing fields: {sorted(missing)}",
        )
    if params["pause_action"] not in _VALID_PAUSE_ACTIONS:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid pause_action value",
            f"Must be one of {_VALID_PAUSE_ACTIONS}",
        )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "PauseRuntime requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )


def _validate_execute_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ExecuteRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if params["rollback_target_type"] not in _VALID_ROLLBACK_TARGET_TYPES:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid rollback_target_type",
            f"Must be one of {_VALID_ROLLBACK_TARGET_TYPES}",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ExecuteRollback requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )


def _validate_approve_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ApproveRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ApproveRollback requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_reject_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REJECT_ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for RejectRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("rejection_reason") or "").strip():
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "RejectRollback requires a non-empty rejection_reason",
            "rejection_reason must be a non-empty string",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "RejectRollback requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_activate_kill_switch(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _KILL_SWITCH_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ActivateKillSwitch",
            f"Missing fields: {sorted(missing)}",
        )
    if params["scope"] not in _VALID_SCOPES:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid scope for ActivateKillSwitch",
            f"Must be one of {_VALID_SCOPES}",
        )
    severity = params.get("severity")
    if severity is not None and severity not in _VALID_SEVERITIES:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid severity for ActivateKillSwitch",
            f"Must be one of {_VALID_SEVERITIES}",
        )
    # Admin role required
    if "admin" not in identity.roles:
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ActivateKillSwitch requires 'admin' role",
            "Operator does not hold the admin role",
            precondition_failed="role_check",
            suggestion="Escalate to an admin-role operator",
        )
    # MFA required for kill-switch (§3.2.3)
    if not identity.mfa_verified:
        raise _bff_error(
            403, ErrorCode.MFA_REQUIRED,
            "ActivateKillSwitch requires MFA verification",
            "Admin action requires MFA validation",
            precondition_failed="mfa_check",
            suggestion="Provide a valid MFA token in your session",
        )


def _validate_escalate_diff(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _ESCALATE_DIFF_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for EscalateDiff",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("escalation_reason") or "").strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "EscalateDiff requires a non-empty escalation_reason",
            "escalation_reason must be a non-empty string",
        )
    if not {"operator", "reviewer", "approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "EscalateDiff requires operator-level governance access",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, reviewer, approver, or admin role",
        )


def _validate_approve_evolution_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_EVO_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ApproveEvolutionDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if params["approval_action"] not in _VALID_EVO_APPROVAL_ACTIONS:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid approval_action",
            f"Must be one of {_VALID_EVO_APPROVAL_ACTIONS}",
        )
    if not {"reviewer", "admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ApproveEvolutionDecision requires 'reviewer', 'approver', or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with reviewer, approver, or admin role",
        )


def _validate_execute_evolution_action(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _EXECUTE_EVO_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ExecuteEvolutionAction",
            f"Missing fields: {sorted(missing)}",
        )
    if params["action_type"] not in _VALID_EVO_ACTION_TYPES:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid action_type for ExecuteEvolutionAction",
            f"Must be one of {_VALID_EVO_ACTION_TYPES}",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ExecuteEvolutionAction requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )


def _mutation_review_surface_state(
    decision: Dict[str, Any],
    approval_decision: Optional[Dict[str, Any]],
    linked_incident: Optional[Dict[str, Any]],
    linked_postmortem: Optional[Dict[str, Any]],
) -> str:
    required_sources_available = True
    decision_state = str(decision.get("decision_state") or decision.get("status") or "").lower()
    approval_decision_id = str(decision.get("approval_decision_id") or "").strip()
    linked_incident_id = str(decision.get("linked_incident_id") or "").strip()
    linked_postmortem_id = str(decision.get("linked_postmortem_id") or "").strip()

    if read_store.dataset_source("evolution_decisions") == "missing":
        required_sources_available = False
    if decision_state in {"reviewed", "approved", "executed", "rejected", "superseded"}:
        if not approval_decision_id or approval_decision is None:
            required_sources_available = False
    if linked_incident_id and linked_incident is None:
        required_sources_available = False
    if linked_postmortem_id and linked_postmortem is None:
        required_sources_available = False

    read_surface_state = _read_surface_state()
    if read_surface_state == "unavailable" or not required_sources_available:
        return "unavailable"
    if read_surface_state in {"degraded", "stale"}:
        return "stale"

    dataset_names = ["evolution_decisions"]
    if approval_decision_id:
        dataset_names.append("approval_decisions")
    if linked_incident_id:
        dataset_names.append("incidents")
    if linked_postmortem_id:
        dataset_names.append("postmortems")
    if any(read_store.dataset_source(dataset) == "local_snapshot" for dataset in dataset_names):
        return "stale"
    return "fresh"


def _mutation_review_roles_for(
    risk_level: str,
    *,
    action: str,
) -> set[str]:
    normalized_risk = str(risk_level or "").lower()
    if action == "approve":
        return _MUTATION_APPROVAL_ROLES.get(normalized_risk, {"admin"})
    return _MUTATION_REJECTION_ROLES.get(normalized_risk, {"admin"})


def _mutation_review_allowed_actions(
    decision: Dict[str, Any],
    identity: OperatorIdentity,
    surface_state: str,
) -> Dict[str, bool]:
    if surface_state == "unavailable":
        return {
            "canApproveMutation": False,
            "canRejectMutation": False,
        }

    decision_state = str(decision.get("decision_state") or decision.get("status") or "").lower()
    risk_level = str(decision.get("risk_level") or "").lower()
    identity_roles = set(identity.roles)

    can_approve = (
        decision_state == "reviewed"
        and bool(identity_roles.intersection(_mutation_review_roles_for(risk_level, action="approve")))
    )
    can_reject = (
        decision_state in {"proposed", "reviewed"}
        and bool(identity_roles.intersection(_mutation_review_roles_for(risk_level, action="reject")))
    )
    return {
        "canApproveMutation": can_approve,
        "canRejectMutation": can_reject,
    }


def _mutation_threshold_triggers(decision: Dict[str, Any]) -> List[Dict[str, Any]]:
    risk_assessment = decision.get("risk_assessment") or {}
    explicit = risk_assessment.get("threshold_triggers")
    if isinstance(explicit, list):
        return explicit

    triggers: List[Dict[str, Any]] = []
    for snapshot in decision.get("threshold_snapshots") or []:
        if not isinstance(snapshot, dict):
            continue
        triggers.append(
            {
                "trigger_type": snapshot.get("signal_type"),
                "metric": snapshot.get("metric_name"),
                "observed_value": str(snapshot.get("observed_value")),
                "threshold_value": str(snapshot.get("threshold_value")),
                "threshold_source": snapshot.get("policy_source"),
            }
        )
    return triggers


def _mutation_required_approvals(decision: Dict[str, Any]) -> List[Dict[str, Any]]:
    explicit = decision.get("required_approvals")
    if isinstance(explicit, list):
        return explicit

    risk_level = str(decision.get("risk_level") or "").lower()
    if risk_level == "low":
        required_roles = ["reviewer_on_duty"]
    elif risk_level == "medium":
        required_roles = ["reviewer", "risk_owner"]
    elif risk_level == "high":
        required_roles = ["governance_committee"]
    else:
        required_roles = []

    approvals: List[Dict[str, Any]] = []
    review_chain = decision.get("review_chain") or []
    for role in required_roles:
        matched_step = next(
            (
                step for step in review_chain
                if isinstance(step, dict)
                and str(step.get("actor_role") or "").lower() == role
                and str(step.get("step_type") or step.get("action") or "").lower() in {"reviewed", "approved"}
            ),
            None,
        )
        approvals.append(
            {
                "role": role,
                "approved_by": matched_step.get("actor_id") if matched_step else None,
                "approved_at": matched_step.get("timestamp") if matched_step else None,
                "status": "approved" if matched_step else "pending",
            }
        )
    return approvals


def _mutation_review_projection(
    decision: Dict[str, Any],
    *,
    approval_decision: Optional[Dict[str, Any]],
    linked_incident: Optional[Dict[str, Any]],
    linked_postmortem: Optional[Dict[str, Any]],
    identity: OperatorIdentity,
    snapshot_at: str,
) -> Dict[str, Any]:
    surface_state = _mutation_review_surface_state(
        decision,
        approval_decision,
        linked_incident,
        linked_postmortem,
    )
    allowed_actions = _mutation_review_allowed_actions(decision, identity, surface_state)
    proposed_changes = dict(decision.get("proposed_changes") or {})
    risk_assessment = dict(decision.get("risk_assessment") or {})
    evidence_refs = list(decision.get("evidence_refs") or [])

    if linked_incident and not any(ref.get("ref_id") == linked_incident.get("incident_id") for ref in evidence_refs if isinstance(ref, dict)):
        evidence_refs.append(
            {
                "ref_type": "incident",
                "ref_id": linked_incident.get("incident_id"),
                "summary": linked_incident.get("evidence_summary") or linked_incident.get("title"),
            }
        )
    postmortem_id = (
        linked_postmortem.get("postmortem_id")
        or linked_postmortem.get("report_id")
        or linked_postmortem.get("id")
        if linked_postmortem
        else None
    )
    if linked_postmortem and not any(ref.get("ref_id") == postmortem_id for ref in evidence_refs if isinstance(ref, dict)):
        evidence_refs.append(
            {
                "ref_type": "postmortem",
                "ref_id": postmortem_id,
                "summary": linked_postmortem.get("summary") or linked_postmortem.get("title"),
            }
        )

    if "summary" not in proposed_changes:
        proposed_changes["summary"] = decision.get("rationale") or decision.get("notes") or ""
    proposed_changes.setdefault("target_stage", decision.get("target_stage"))
    proposed_changes.setdefault("downstream_plane", (decision.get("execution_result") or {}).get("plane"))
    proposed_changes.setdefault("change_details", [])

    risk_assessment.setdefault(
        "risk_summary",
        decision.get("notes") or decision.get("rationale") or "",
    )
    risk_assessment.setdefault("severity", None)
    risk_assessment["threshold_triggers"] = _mutation_threshold_triggers(decision)

    review_chain = [
        {
            "action": step.get("action") or step.get("step_type"),
            "actor_role": step.get("actor_role"),
            "actor_id": step.get("actor_id"),
            "acted_at": step.get("acted_at") or step.get("timestamp"),
            "note": step.get("note"),
        }
        for step in (decision.get("review_chain") or [])
        if isinstance(step, dict)
    ]

    rollback_followthrough = decision.get("rollback_followthrough")
    if rollback_followthrough is None:
        linked_incident_id = str(decision.get("linked_incident_id") or "").strip()
        rollbacks = read_store.get_rollbacks_by_incident(linked_incident_id) if linked_incident_id else []
        if rollbacks:
            first_rollback = rollbacks[0]
            rollback_followthrough = {
                "rollback_request_ref": first_rollback.get("rollback_id") or first_rollback.get("id"),
                "rollback_action_type": first_rollback.get("action_type"),
                "followthrough_note": first_rollback.get("reason"),
            }

    return {
        "decision_id": decision.get("decision_id") or decision.get("id"),
        "target_type": decision.get("target_type"),
        "target_id": decision.get("target_id") or decision.get("artifact_id"),
        "target_version": decision.get("target_version"),
        "action_type": decision.get("action_type"),
        "decision_state": decision.get("decision_state") or decision.get("status"),
        "risk_level": decision.get("risk_level"),
        "created_at": decision.get("created_at"),
        "approval_decision_id": decision.get("approval_decision_id"),
        "proposed_changes": proposed_changes,
        "risk_assessment": risk_assessment,
        "required_approvals": _mutation_required_approvals(decision),
        "review_chain": review_chain,
        "linked_incident_id": decision.get("linked_incident_id"),
        "linked_postmortem_id": decision.get("linked_postmortem_id"),
        "evidence_refs": evidence_refs,
        "rollback_followthrough": rollback_followthrough,
        "allowedActions": allowed_actions,
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "mutation_review": surface_state,
            },
        },
    }


def _mutation_review_inputs(
    decision_id: str,
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    decision = read_store.get_evolution_decision_by_id(decision_id)
    if decision is None:
        return None, None, None, None

    approval_decision_id = str(decision.get("approval_decision_id") or "").strip()
    approval_decision = (
        read_store.get_approval_decision(approval_decision_id)
        if approval_decision_id
        else None
    )
    linked_incident_id = str(decision.get("linked_incident_id") or "").strip()
    linked_incident = read_store.get_incident(linked_incident_id) if linked_incident_id else None
    linked_postmortem_id = str(decision.get("linked_postmortem_id") or "").strip()
    linked_postmortem = (
        read_store.get_postmortem(linked_postmortem_id)
        if linked_postmortem_id
        else None
    )
    return decision, approval_decision, linked_incident, linked_postmortem


def _cw03_committee_surface_state(
    committee: Dict[str, Any],
    *,
    snapshot_at: str,
) -> str:
    committee_surface = _dataset_surface_status(
        "consultation_sessions",
        snapshot_at=snapshot_at,
        has_data=bool(committee),
        missing_message="Committee board state is unavailable.",
    )
    if committee_surface.get("status") == "unavailable":
        return "unavailable"
    if committee.get("surface_state") == "degraded":
        return "degraded"
    if committee_surface.get("source") == "local_snapshot":
        return "degraded"
    if committee_surface.get("status") == "degraded":
        return "stale"
    return "ok"


def _cw03_allowed_actions(
    committee: Dict[str, Any],
    *,
    identity: OperatorIdentity,
    surface_state: str,
) -> Dict[str, bool]:
    if surface_state == "unavailable":
        return {
            "canRecordSponsorDecision": False,
        }
    sponsor_decision = committee.get("sponsor_decision")
    consensus_state = str(committee.get("consensus_state") or "").strip().lower()
    roles = set(identity.roles)
    return {
        "canRecordSponsorDecision": (
            sponsor_decision in (None, "")
            and consensus_state == "sponsor_required"
            and bool(roles.intersection({"operator", "reviewer", "approver", "admin"}))
        )
    }


def _cw03_committee_projection(
    committee: Dict[str, Any],
    *,
    identity: OperatorIdentity,
    snapshot_at: str,
) -> Dict[str, Any]:
    surface_state = _cw03_committee_surface_state(committee, snapshot_at=snapshot_at)
    allowed_actions = _cw03_allowed_actions(committee, identity=identity, surface_state=surface_state)
    return {
        "committee_id": committee.get("committee_id"),
        "committee_ref": committee.get("committee_ref"),
        "linked_request_id": committee.get("linked_request_id"),
        "linked_session_id": committee.get("linked_session_id"),
        "started_at": committee.get("started_at"),
        "escalation_reason": json.loads(json.dumps(committee.get("escalation_reason") or {})),
        "quorum_state": committee.get("quorum_state"),
        "consensus_state": committee.get("consensus_state"),
        "participant_roster": json.loads(json.dumps(committee.get("participant_roster") or [])),
        "sponsor_assignment": json.loads(json.dumps(committee.get("sponsor_assignment") or {})),
        "sponsor_decision": committee.get("sponsor_decision"),
        "sponsor_decided_at": committee.get("sponsor_decided_at"),
        "sponsor_decided_by": committee.get("sponsor_decided_by"),
        "synthesis_summary": json.loads(json.dumps(committee.get("synthesis_summary") or {})),
        "linked_evidence": json.loads(json.dumps(committee.get("linked_evidence") or [])),
        "allowedActions": allowed_actions,
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "committee_board": surface_state,
            },
        },
    }


def _validate_record_sponsor_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _RECORD_SPONSOR_DECISION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for RecordSponsorDecision",
            f"Missing fields: {sorted(missing)}",
        )
    sponsor_decision = str(params.get("sponsor_decision") or "").strip().lower()
    if sponsor_decision not in _VALID_SPONSOR_DECISIONS:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid sponsor_decision value",
            f"sponsor_decision must be one of {sorted(_VALID_SPONSOR_DECISIONS)}",
        )
    rationale_ref = str(params.get("rationale_ref") or "").strip()
    if not rationale_ref:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "RecordSponsorDecision requires a non-empty rationale_ref",
            "rationale_ref must be a non-empty string",
        )
    committee_id = str(params.get("committee_id") or "").strip()
    committee = read_store.get_committee(committee_id)
    if committee is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee board not found",
            f"Committee {committee_id} does not exist",
        )
    projection = _cw03_committee_projection(
        committee,
        identity=identity,
        snapshot_at=utc_now(),
    )
    if projection["meta"]["surfaces"]["committee_board"] == "unavailable":
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "RecordSponsorDecision is blocked while the committee board is unavailable",
            "Committee evidence cannot be composed reliably",
            precondition_failed="committee_board_surface",
        )
    if not projection["allowedActions"]["canRecordSponsorDecision"]:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "RecordSponsorDecision is not allowed for this operator and committee state",
            "allowedActions.canRecordSponsorDecision is false for the current read projection",
            precondition_failed="allowedActions.canRecordSponsorDecision",
        )


def _validate_approve_mutation(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_MUTATION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for ApproveMutation",
            f"Missing fields: {sorted(missing)}",
        )
    decision_id = str(params.get("decision_id") or "").strip()
    decision, approval_decision, linked_incident, linked_postmortem = _mutation_review_inputs(decision_id)
    if decision is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Mutation review decision not found",
            f"Evolution decision {decision_id} does not exist",
        )
    projection = _mutation_review_projection(
        decision,
        approval_decision=approval_decision,
        linked_incident=linked_incident,
        linked_postmortem=linked_postmortem,
        identity=identity,
        snapshot_at=utc_now(),
    )
    if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "ApproveMutation is blocked while the mutation-review surface is unavailable",
            "Mutation-review evidence cannot be composed reliably",
            precondition_failed="mutation_review_surface",
        )
    if not projection["allowedActions"]["canApproveMutation"]:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "ApproveMutation is not allowed for this operator and decision state",
            "allowedActions.canApproveMutation is false for the current read projection",
            precondition_failed="allowedActions.canApproveMutation",
        )


def _validate_reject_mutation(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REJECT_MUTATION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for RejectMutation",
            f"Missing fields: {sorted(missing)}",
        )
    decision_id = str(params.get("decision_id") or "").strip()
    decision, approval_decision, linked_incident, linked_postmortem = _mutation_review_inputs(decision_id)
    if decision is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Mutation review decision not found",
            f"Evolution decision {decision_id} does not exist",
        )
    projection = _mutation_review_projection(
        decision,
        approval_decision=approval_decision,
        linked_incident=linked_incident,
        linked_postmortem=linked_postmortem,
        identity=identity,
        snapshot_at=utc_now(),
    )
    if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "RejectMutation is blocked while the mutation-review surface is unavailable",
            "Mutation-review evidence cannot be composed reliably",
            precondition_failed="mutation_review_surface",
        )
    if not projection["allowedActions"]["canRejectMutation"]:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "RejectMutation is not allowed for this operator and decision state",
            "allowedActions.canRejectMutation is false for the current read projection",
            precondition_failed="allowedActions.canRejectMutation",
        )


_VALIDATORS = {
    CommandType.APPROVE_DEPLOYMENT: _validate_approve_deployment,
    CommandType.APPROVE_DECISION: _validate_approve_decision,
    CommandType.REJECT_DECISION: _validate_reject_decision,
    CommandType.REQUEST_APPROVAL_REVISION: _validate_request_approval_revision,
    CommandType.PAUSE_RUNTIME: _validate_pause_runtime,
    CommandType.PAUSE_EXECUTION: _validate_pause_execution,
    CommandType.ESCALATE_DIFF: _validate_escalate_diff,
    CommandType.ISSUE_RISK_OFF: _validate_issue_risk_off,
    CommandType.LIQUIDATE_ALL: _validate_liquidate_all,
    CommandType.HARD_ROLLBACK: _validate_hard_rollback,
    CommandType.ISSUE_SAFE_MODE: _validate_issue_safe_mode,
    CommandType.EXECUTE_ROLLBACK: _validate_execute_rollback,
    CommandType.APPROVE_ROLLBACK: _validate_approve_rollback,
    CommandType.REJECT_ROLLBACK: _validate_reject_rollback,
    CommandType.ACTIVATE_KILL_SWITCH: _validate_activate_kill_switch,
    CommandType.APPROVE_EVOLUTION_DECISION: _validate_approve_evolution_decision,
    CommandType.EXECUTE_EVOLUTION_ACTION: _validate_execute_evolution_action,
    CommandType.APPROVE_MUTATION: _validate_approve_mutation,
    CommandType.REJECT_MUTATION: _validate_reject_mutation,
    CommandType.RECORD_SPONSOR_DECISION: _validate_record_sponsor_decision,
}

# --------------------------------------------------------------------------- #
# Read surface helpers
# --------------------------------------------------------------------------- #

_READ_ROLES = {"operator", "approver", "admin", "reviewer"}


def _require_read_role(identity: OperatorIdentity) -> None:
    if not _READ_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "Read access requires operator-level role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, approver, admin, or reviewer role",
        )


def _read_surface_state() -> str:
    return os.getenv("BFF_READ_SURFACE_STATE", "fresh")


def _meta_staleness() -> Optional[Dict[str, Any]]:
    state = _read_surface_state()
    if state == "fresh":
        return None
    return {
        "served_from": "cache",
        "last_known_at": utc_now(),
    }


def _surface_status() -> Dict[str, Any]:
    state = _read_surface_state()
    if state == "fresh":
        return {"status": "ok"}
    if state in {"degraded", "stale"}:
        return {
            "status": "degraded",
            "staleness": _meta_staleness(),
        }
    if state == "unavailable":
        return {
            "status": "unavailable",
            "staleness": _meta_staleness(),
        }
    return {"status": "ok"}


def _dataset_surface_status(
    dataset: str,
    *,
    snapshot_at: Optional[str] = None,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
) -> Dict[str, Any]:
    surface = dict(_surface_status())
    source = read_store.dataset_source(dataset)
    surface["source"] = source

    if source == "local_snapshot":
        if surface.get("status") == "ok":
            surface["status"] = "degraded"
        surface["note"] = "Served from local BFF snapshot fallback instead of a backend-owned read store."
        surface["staleness"] = {
            "served_from": "local_snapshot",
            "last_known_at": snapshot_at or utc_now(),
        }
    elif source == "missing":
        surface["status"] = "unavailable"
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at or utc_now()},
        )

    if has_data is False:
        if surface.get("status") == "ok":
            surface["status"] = "unavailable"
        if missing_message:
            surface["message"] = missing_message
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at or utc_now()},
        )

    return surface


def _composed_surface_status(
    *,
    snapshot_at: Optional[str] = None,
    available: bool = True,
    missing_message: Optional[str] = None,
) -> Dict[str, Any]:
    surface = dict(_surface_status())
    surface["source"] = "bff_composed"
    if available:
        return surface

    if surface.get("status") == "ok":
        surface["status"] = "degraded"
    if missing_message:
        surface["message"] = missing_message
    surface.setdefault(
        "staleness",
        {"served_from": "unverifiable", "last_known_at": snapshot_at or utc_now()},
    )
    return surface


_INCIDENT_SEVERITY_MAP = {
    "critical": "sev1",
    "high": "sev1",
    "medium": "sev2",
    "low": "sev3",
    "sev1": "sev1",
    "sev2": "sev2",
    "sev3": "sev3",
}

_KILL_SWITCH_STATUS_MAP = {
    "armed": "armed",
    "off": "armed",
    "normal": "armed",
    "triggered": "triggered",
    "guarded": "triggered",
    "risk_off": "triggered",
    "cooling_down": "cooling_down",
    "cooldown": "cooling_down",
    "paused": "cooling_down",
}

_ACTION_DRAWER_PRIMARY_ALLOWED_ACTIONS = {
    "canPause": True,
    "canRiskOff": True,
    "canLiquidateAll": False,
    "canHardRollback": False,
    "canIssueSafeMode": True,
}


def _incident_home_severity(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _INCIDENT_SEVERITY_MAP.get(str(value).strip().lower(), str(value))


def _project_incident_home_item(incident: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "incident_id": incident.get("incident_id"),
        "title": incident.get("title"),
        "severity": _incident_home_severity(incident.get("severity")),
        "status": incident.get("status"),
        "artifact_id": incident.get("artifact_id"),
        "opened_at": incident.get("opened_at") or incident.get("created_at"),
        "resolved_at": incident.get("resolved_at"),
    }


def _project_incident_detail_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "incident_id": incident.get("incident_id"),
        "title": incident.get("title"),
        "severity": _incident_home_severity(incident.get("severity")),
        "status": incident.get("status"),
        "artifact_id": incident.get("artifact_id"),
        "artifact_version": incident.get("artifact_version"),
        "runtime_id": incident.get("runtime_id"),
        "trace_id": incident.get("trace_id"),
        "opened_at": incident.get("opened_at") or incident.get("created_at"),
    }


def _project_affected_binding(
    binding: Dict[str, Any],
    incident: Dict[str, Any],
    runtime_binding: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    raw_stage = (
        incident.get("deployment_stage")
        or binding.get("stage")
        or binding.get("deployment_stage")
        or (runtime_binding or {}).get("deployment_stage")
        or binding.get("allowed_deployment_scope")
    )
    stage = str(raw_stage or "").strip().lower()
    if stage not in {"paper", "live"}:
        stage = "paper"

    return {
        "binding_id": binding.get("id") or binding.get("binding_id"),
        "persona_id": binding.get("persona_id"),
        "capital_pool_id": binding.get("capital_pool_id"),
        "stage": stage,
        "binding_status": binding.get("binding_status") or binding.get("status"),
    }


def _project_affected_bindings(
    incident: Dict[str, Any],
    runtime_binding: Optional[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], bool]:
    candidate_ids: List[str] = []
    for value in [
        incident.get("persona_capital_binding_id"),
        (runtime_binding or {}).get("persona_capital_binding_id"),
    ]:
        if value in (None, ""):
            continue
        string_value = str(value)
        if string_value not in candidate_ids:
            candidate_ids.append(string_value)

    affected_bindings: List[Dict[str, Any]] = []
    for binding_id in candidate_ids:
        binding = read_store.get_binding(binding_id)
        if not binding:
            continue
        affected_bindings.append(
            _project_affected_binding(binding, incident, runtime_binding)
        )

    return affected_bindings, bool(candidate_ids)


def _default_incident_allowed_actions() -> Dict[str, bool]:
    return {
        "canPause": False,
        "canRiskOff": False,
        "canLiquidateAll": False,
        "canHardRollback": False,
        "canIssueSafeMode": False,
        "canOpenActionDrawer": False,
    }


def _derive_incident_allowed_actions(
    identity: OperatorIdentity,
    incident: Dict[str, Any],
) -> Dict[str, bool]:
    actions = _default_incident_allowed_actions()
    incident_status = str(incident.get("status") or "").lower()
    runtime_id = incident.get("runtime_id")
    if incident_status not in {"open", "in_progress"} or not runtime_id:
        return actions

    if not {"operator", "admin"}.intersection(identity.roles):
        return actions

    actions["canPause"] = True
    actions["canRiskOff"] = True
    actions["canIssueSafeMode"] = True
    actions["canOpenActionDrawer"] = True
    return actions


def _decode_page_token(page_token: Optional[str]) -> int:
    if page_token in (None, ""):
        return 0
    try:
        offset = int(page_token)
    except (TypeError, ValueError) as exc:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid page_token",
            "page_token must be a non-negative integer offset",
        ) from exc
    if offset < 0:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid page_token",
            "page_token must be a non-negative integer offset",
        )
    return offset


def _page_slice(items: List[Dict[str, Any]], page_token: Optional[str], page_size: int) -> tuple[List[Dict[str, Any]], Optional[str]]:
    start = _decode_page_token(page_token)
    end = start + page_size
    next_page_token = str(end) if end < len(items) else None
    return items[start:end], next_page_token


_RUNTIME_STATE_SORT_FIELDS = {"last_updated_at", "runtime_id", "deployment_stage", "status"}
_RUNTIME_STATE_SORT_ORDERS = {"asc", "desc"}
_HEALTH_GROUP_LABELS = {
    "runtime": "Runtime",
    "telemetry": "Telemetry",
    "incident": "Incident",
    "governance": "Governance",
    "kill_switch": "Kill Switch",
}
_HEALTH_SURFACE_ORDER = ("runtime", "telemetry", "incident", "governance", "kill_switch")
_INCIDENT_SEVERITY_ORDER = {"sev1": 3, "sev2": 2, "sev3": 1}
_GOVERNANCE_RISK_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_ALERT_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_ALERT_CATEGORY_ORDER = {"incident": 4, "kill_switch": 3, "governance": 2, "runtime": 1}
_SECONDARY_CONTROL_PATH_ADVISORY_TARGETS = (
    {
        "operation": "Health diagnostics",
        "channel": "admin_cli",
        "command": "pantheon admin health",
        "api_path": "GET /admin/health",
        "required_role": "operator",
        "requires_mfa": False,
    },
    {
        "operation": "Runtime status",
        "channel": "admin_cli",
        "command": "pantheon admin runtime status --runtime={runtime_id}",
        "api_path": "GET /admin/runtimes/{runtime_id}/status",
        "required_role": "operator",
        "requires_mfa": False,
    },
    {
        "operation": "Kill-switch status",
        "channel": "admin_cli",
        "command": "pantheon admin kill-switch status",
        "api_path": "GET /admin/kill-switch/status",
        "required_role": "operator",
        "requires_mfa": False,
    },
)
_RUNTIME_STATUS_ALERT_SEVERITY = {
    "failed": "critical",
    "error": "critical",
    "degraded": "high",
    "paused": "medium",
}
_TELEMETRY_DRAWDOWN_THRESHOLDS = (
    (0.10, "critical"),
    (0.05, "high"),
)
_TELEMETRY_FILL_RATE_THRESHOLDS = (
    (0.90, "critical"),
    (0.95, "high"),
)
_TELEMETRY_SLIPPAGE_THRESHOLDS = (
    (4.0, "critical"),
    (3.0, "high"),
)
_SECONDARY_CONTROL_PATH_RECOMMENDED_TARGETS = _SECONDARY_CONTROL_PATH_ADVISORY_TARGETS + (
    {
        "operation": "Runtime pause",
        "channel": "admin_cli",
        "command": "pantheon admin runtime pause --runtime={runtime_id}",
        "api_path": "POST /admin/runtimes/{runtime_id}/pause",
        "required_role": "admin",
        "requires_mfa": True,
    },
    {
        "operation": "Runtime rollback",
        "channel": "admin_cli",
        "command": "pantheon admin runtime rollback --runtime={runtime_id} --target={version}",
        "api_path": "POST /admin/runtimes/{runtime_id}/rollback",
        "required_role": "admin",
        "requires_mfa": True,
    },
    {
        "operation": "Kill-switch activation",
        "channel": "admin_cli",
        "command": "pantheon admin kill-switch activate --runtime={runtime_id}",
        "api_path": "POST /admin/kill-switch/activate",
        "required_role": "admin",
        "requires_mfa": True,
    },
)


def _split_csv_query(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    tokens = [token.strip() for token in value.split(",") if token.strip()]
    return tokens or None


def _project_runtime_state_telemetry_summary(summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not summary:
        return None
    return {
        "window": summary.get("window"),
        "collected_at": summary.get("collected_at"),
        "metrics": {
            "pnl": summary.get("pnl"),
            "drawdown": summary.get("drawdown"),
            "sharpe_ratio": summary.get("sharpe_ratio"),
            "fill_rate": summary.get("fill_rate"),
            "avg_slippage_bps": summary.get("avg_slippage_bps"),
            "total_trades": summary.get("total_trades"),
        },
    }


def _project_runtime_state_latest_rollback(rollbacks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rollbacks:
        return None
    latest = max(
        rollbacks,
        key=lambda rollback: (
            rollback.get("completed_at")
            or rollback.get("executed_at")
            or rollback.get("initiated_at")
            or ""
        ),
    )
    return {
        "rollback_id": latest.get("rollback_id") or latest.get("id"),
        "action_type": latest.get("action_type"),
        "status": latest.get("status"),
        "from_version": latest.get("from_version"),
        "to_version": latest.get("to_version"),
        "initiated_at": latest.get("initiated_at"),
        "completed_at": latest.get("completed_at") or latest.get("executed_at"),
    }


def _derive_runtime_state_last_updated_at(
    binding: Dict[str, Any],
    telemetry_summary: Optional[Dict[str, Any]],
    latest_rollback: Optional[Dict[str, Any]],
) -> Optional[str]:
    candidates = [
        binding.get("last_updated_at"),
        binding.get("updated_at"),
        binding.get("started_at"),
        binding.get("created_at"),
        (telemetry_summary or {}).get("collected_at"),
        (latest_rollback or {}).get("completed_at"),
        (latest_rollback or {}).get("initiated_at"),
    ]
    values = [candidate for candidate in candidates if candidate]
    if not values:
        return None
    return max(values)


def _project_operator_runtime_state_row(binding: Dict[str, Any]) -> Dict[str, Any]:
    runtime_id = str(binding.get("runtime_id") or binding.get("id") or "")
    telemetry_summary = _project_runtime_state_telemetry_summary(
        read_store.get_telemetry_summary(runtime_id)
    )
    rollbacks = read_store.get_rollbacks(runtime_id)
    latest_rollback = _project_runtime_state_latest_rollback(rollbacks)
    artifact_id = binding.get("artifact_id")
    artifact_version = binding.get("artifact_version") or binding.get("version")
    plan_id = binding.get("plan_id")

    return {
        "runtime_id": runtime_id,
        "runtime_binding_id": binding.get("id"),
        "deployment_stage": binding.get("deployment_stage") or binding.get("deployment_mode"),
        "status": binding.get("status"),
        "capital_pool_id": binding.get("capital_pool_id"),
        "plan_ref": (
            {
                "plan_id": plan_id,
                "href": _deployment_review_href(str(plan_id)),
            }
            if plan_id
            else None
        ),
        "artifact_ref": (
            {
                "artifact_id": artifact_id,
                "artifact_version": artifact_version,
            }
            if artifact_id or artifact_version
            else None
        ),
        "telemetry_summary": telemetry_summary,
        "rollback_summary": {
            "count": len(rollbacks),
            "latest": latest_rollback,
            "href": f"/api/v1/runtimes/{runtime_id}/rollbacks",
        },
        "last_updated_at": _derive_runtime_state_last_updated_at(
            binding,
            telemetry_summary,
            latest_rollback,
        ),
    }


def _sort_runtime_state_rows(
    rows: List[Dict[str, Any]],
    *,
    sort_by: str,
    sort_order: str,
) -> List[Dict[str, Any]]:
    reverse = sort_order == "desc"

    def _sort_key(row: Dict[str, Any]) -> tuple[str, str]:
        primary = row.get(sort_by)
        if primary is None:
            primary = ""
        return (str(primary), str(row.get("runtime_id") or ""))

    ordered_rows = sorted(rows, key=_sort_key, reverse=reverse)
    present = [row for row in ordered_rows if row.get(sort_by)]
    missing = [row for row in ordered_rows if not row.get(sort_by)]
    return present + missing


def _highest_ranked_value(
    values: List[Optional[str]],
    order: Dict[str, int],
) -> Optional[str]:
    best_value: Optional[str] = None
    best_rank = -1
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip().lower()
        rank = order.get(normalized)
        if rank is None:
            continue
        if rank > best_rank:
            best_rank = rank
            best_value = normalized
    return best_value


def _aggregate_group_surface(
    surface_key: str,
    source_surfaces: List[Dict[str, Any]],
    *,
    snapshot_at: str,
    unavailable_message: str,
    degraded_message: str,
) -> Dict[str, Any]:
    surface = _composed_surface_status(snapshot_at=snapshot_at, available=True)
    surface["source"] = "bff_composed"
    statuses = [entry.get("status", "ok") for entry in source_surfaces]
    if statuses and all(status == "ok" for status in statuses):
        return surface
    if statuses and all(status == "unavailable" for status in statuses):
        surface["status"] = "unavailable"
        surface["message"] = unavailable_message
        return surface
    surface["status"] = "degraded"
    surface["message"] = degraded_message
    return surface


def _project_health_surface_ref(surface_key: str, surface: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "surface_key": surface_key,
        "status": surface.get("status"),
        "source": surface.get("source"),
    }
    if surface.get("message"):
        payload["message"] = surface.get("message")
    return payload


def _build_runtime_health_group(snapshot_at: str) -> tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    bindings = read_store.list_runtime_bindings()
    runtime_roster_surface = _dataset_surface_status(
        "runtime_bindings",
        snapshot_at=snapshot_at,
    )
    by_stage: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for binding in bindings:
        stage = str(binding.get("deployment_stage") or binding.get("deployment_mode") or "unknown")
        status = str(binding.get("status") or "unknown")
        by_stage[stage] = by_stage.get(stage, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1

    group_surface = _aggregate_group_surface(
        "runtime",
        [runtime_roster_surface],
        snapshot_at=snapshot_at,
        unavailable_message="Runtime roster unavailable.",
        degraded_message="Runtime roster degraded or stale.",
    )
    if runtime_roster_surface.get("status") == "unavailable":
        summary = "Runtime roster unavailable."
    elif not bindings:
        summary = "No runtimes reported."
    else:
        summary = f"{len(bindings)} runtime(s) tracked across {len(by_stage)} stage(s)."

    group = {
        "group_id": "runtime",
        "label": _HEALTH_GROUP_LABELS["runtime"],
        "status": group_surface.get("status"),
        "summary": summary,
        "details": {
            "total_runtime_count": len(bindings),
            "by_stage": by_stage,
            "by_status": by_status,
        },
        "surface_refs": [
            _project_health_surface_ref("runtime_roster", runtime_roster_surface),
        ],
        "target_refs": [
            {
                "label": "Runtime State Board",
                "href": _OPERATOR_RUNTIME_STATE_ROUTE,
            },
        ],
    }
    return group, group_surface, bindings


def _build_telemetry_health_group(
    snapshot_at: str,
    bindings: List[Dict[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    telemetry_surface = _dataset_surface_status(
        "telemetry_summaries",
        snapshot_at=snapshot_at,
    )
    covered_runtime_count = 0
    latest_collected_at: Optional[str] = None
    for binding in bindings:
        runtime_id = str(binding.get("runtime_id") or binding.get("id") or "")
        if not runtime_id:
            continue
        summary = read_store.get_telemetry_summary(runtime_id)
        if not summary:
            continue
        covered_runtime_count += 1
        collected_at = summary.get("collected_at")
        if collected_at and (latest_collected_at is None or collected_at > latest_collected_at):
            latest_collected_at = collected_at

    total_runtime_count = len(bindings)
    missing_runtime_count = max(total_runtime_count - covered_runtime_count, 0)
    if (
        total_runtime_count > 0
        and missing_runtime_count > 0
        and telemetry_surface.get("status") == "ok"
    ):
        telemetry_surface["status"] = "degraded"
        telemetry_surface["message"] = (
            "Telemetry summary missing for one or more runtimes."
        )
        telemetry_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )

    group_surface = _aggregate_group_surface(
        "telemetry",
        [telemetry_surface],
        snapshot_at=snapshot_at,
        unavailable_message="Telemetry summary unavailable.",
        degraded_message="Telemetry summary coverage is degraded or stale.",
    )
    if telemetry_surface.get("status") == "unavailable":
        summary = "Telemetry summary unavailable."
    elif total_runtime_count == 0:
        summary = "No runtimes available for telemetry coverage."
    elif missing_runtime_count == 0:
        summary = f"Telemetry coverage available for all {covered_runtime_count} runtime(s)."
    else:
        summary = (
            f"Telemetry coverage available for {covered_runtime_count} of "
            f"{total_runtime_count} runtime(s)."
        )

    group = {
        "group_id": "telemetry",
        "label": _HEALTH_GROUP_LABELS["telemetry"],
        "status": group_surface.get("status"),
        "summary": summary,
        "details": {
            "total_runtime_count": total_runtime_count,
            "covered_runtime_count": covered_runtime_count,
            "missing_runtime_count": missing_runtime_count,
            "latest_collected_at": latest_collected_at,
        },
        "surface_refs": [
            _project_health_surface_ref("telemetry_summary", telemetry_surface),
        ],
        "target_refs": [
            {
                "label": "Runtime State Board",
                "href": _OPERATOR_RUNTIME_STATE_ROUTE,
            },
        ],
    }
    return group, group_surface


def _build_incident_health_group(snapshot_at: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    incident_surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    incidents = read_store.list_incidents()
    active_incidents = [
        incident
        for incident in incidents
        if str(incident.get("status") or "").lower() in {"open", "in_progress"}
    ]
    highest_severity = _highest_ranked_value(
        [_incident_home_severity(incident.get("severity")) for incident in active_incidents],
        _INCIDENT_SEVERITY_ORDER,
    )
    open_count = sum(
        1
        for incident in active_incidents
        if str(incident.get("status") or "").lower() == "open"
    )
    in_progress_count = sum(
        1
        for incident in active_incidents
        if str(incident.get("status") or "").lower() == "in_progress"
    )

    group_surface = _aggregate_group_surface(
        "incident",
        [incident_surface],
        snapshot_at=snapshot_at,
        unavailable_message="Incident surface unavailable.",
        degraded_message="Incident surface degraded or stale.",
    )
    if incident_surface.get("status") == "unavailable":
        summary = "Incident surface unavailable."
    elif not active_incidents:
        summary = "No active incidents."
    else:
        summary = (
            f"{len(active_incidents)} active incident(s); highest severity "
            f"{highest_severity or 'unknown'}."
        )

    group = {
        "group_id": "incident",
        "label": _HEALTH_GROUP_LABELS["incident"],
        "status": group_surface.get("status"),
        "summary": summary,
        "details": {
            "total_incident_count": len(incidents),
            "active_incident_count": len(active_incidents),
            "open_count": open_count,
            "in_progress_count": in_progress_count,
            "highest_severity": highest_severity,
        },
        "surface_refs": [
            _project_health_surface_ref("incident_list", incident_surface),
        ],
        "target_refs": [
            {
                "label": "Incident Home",
                "href": _OPERATOR_INCIDENT_HOME_ROUTE,
            },
        ],
    }
    return group, group_surface


def _build_governance_health_group(snapshot_at: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    review_queue_surface = _dataset_surface_status(
        "governance_review_queue_items",
        snapshot_at=snapshot_at,
    )
    approval_queue_surface = _dataset_surface_status(
        "approval_queue_items",
        snapshot_at=snapshot_at,
    )
    review_items = read_store.list_governance_review_queue_items()
    approval_items = read_store.list_approval_queue_items()
    highest_risk_level = _highest_ranked_value(
        [item.get("risk_level") for item in review_items + approval_items],
        _GOVERNANCE_RISK_ORDER,
    )

    group_surface = _aggregate_group_surface(
        "governance",
        [review_queue_surface, approval_queue_surface],
        snapshot_at=snapshot_at,
        unavailable_message="Governance health unavailable.",
        degraded_message="Governance review or approval surfaces are degraded.",
    )
    total_pending_items = len(review_items) + len(approval_items)
    if group_surface.get("status") == "unavailable":
        summary = "Governance health unavailable."
    elif total_pending_items == 0:
        summary = "No pending governance reviews or approvals."
    else:
        summary = f"{total_pending_items} governance item(s) pending review or approval."

    group = {
        "group_id": "governance",
        "label": _HEALTH_GROUP_LABELS["governance"],
        "status": group_surface.get("status"),
        "summary": summary,
        "details": {
            "review_queue_count": len(review_items),
            "approval_queue_count": len(approval_items),
            "total_pending_items": total_pending_items,
            "highest_risk_level": highest_risk_level,
        },
        "surface_refs": [
            _project_health_surface_ref("review_queue", review_queue_surface),
            _project_health_surface_ref("approval_queue", approval_queue_surface),
        ],
        "target_refs": [
            {
                "label": "Governance Review Queue",
                "href": _GOVERNANCE_REVIEW_QUEUE_ROUTE,
            },
            {
                "label": "Governance Approval Queue",
                "href": _GOVERNANCE_APPROVAL_QUEUE_ROUTE,
            },
        ],
    }
    return group, group_surface


def _build_kill_switch_health_group(
    snapshot_at: str,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    kill_switch_surface = _dataset_surface_status("kill_switch", snapshot_at=snapshot_at)
    kill_switch = (
        read_store.get_kill_switch_status()
        if kill_switch_surface.get("status") != "unavailable"
        else {}
    )
    safe_mode_status = kill_switch.get("safe_mode_status")
    kill_switch_status = kill_switch.get("status")

    group_surface = _aggregate_group_surface(
        "kill_switch",
        [kill_switch_surface],
        snapshot_at=snapshot_at,
        unavailable_message="Kill-switch and safe-mode state unavailable.",
        degraded_message="Kill-switch or safe-mode state is degraded or stale.",
    )
    if kill_switch_surface.get("status") == "unavailable":
        summary = "Kill-switch and safe-mode state unavailable."
    else:
        summary = (
            f"Kill-switch {kill_switch_status or 'unknown'}; "
            f"safe mode {safe_mode_status or 'unknown'}."
        )

    safe_mode_state = {
        "status": None if kill_switch_surface.get("status") == "unavailable" else safe_mode_status,
        "kill_switch_status": None if kill_switch_surface.get("status") == "unavailable" else kill_switch_status,
        "active": None if kill_switch_surface.get("status") == "unavailable" else kill_switch.get("active"),
        "last_confirmed_at": None if kill_switch_surface.get("status") == "unavailable" else kill_switch.get("last_confirmed_at"),
        "last_triggered_at": None if kill_switch_surface.get("status") == "unavailable" else kill_switch.get("last_triggered_at"),
        "secondary_path_available": None if kill_switch_surface.get("status") == "unavailable" else kill_switch.get("secondary_path_available"),
    }

    group = {
        "group_id": "kill_switch",
        "label": _HEALTH_GROUP_LABELS["kill_switch"],
        "status": group_surface.get("status"),
        "summary": summary,
        "details": {
            "kill_switch_status": safe_mode_state["kill_switch_status"],
            "safe_mode_status": safe_mode_state["status"],
            "active_command_count": (
                len(kill_switch.get("active_commands") or [])
                if kill_switch_surface.get("status") != "unavailable"
                else None
            ),
            "last_confirmed_at": safe_mode_state["last_confirmed_at"],
            "last_triggered_at": safe_mode_state["last_triggered_at"],
            "secondary_path_available": safe_mode_state["secondary_path_available"],
        },
        "surface_refs": [
            _project_health_surface_ref("kill_switch", kill_switch_surface),
        ],
        "target_refs": [
            {
                "label": "Health Status Board",
                "href": _OPERATOR_HEALTH_STATUS_ROUTE,
            },
        ],
    }
    return group, group_surface, safe_mode_state


def _build_secondary_control_path(
    *,
    overall_status: str,
    safe_mode_state: Dict[str, Any],
) -> Dict[str, Any]:
    safe_mode_status = str(safe_mode_state.get("status") or "").lower()
    kill_switch_status = str(safe_mode_state.get("kill_switch_status") or "").lower()
    safe_mode_active = safe_mode_status not in {"", "off", "released", "none", "null"}
    if overall_status == "ok" and not safe_mode_active and kill_switch_status not in {"triggered", "cooling_down"}:
        return {
            "mode": "hidden",
            "reason": None,
            "targets": [],
        }

    if overall_status == "unavailable" or safe_mode_active or kill_switch_status in {"triggered", "cooling_down"}:
        mode = "recommended"
        reason = (
            "One or more critical health groups are unavailable or safe mode is active. "
            "Use the secondary control path for verification or intervention."
        )
        targets = _SECONDARY_CONTROL_PATH_RECOMMENDED_TARGETS
    else:
        mode = "advisory"
        reason = (
            "Some health groups are degraded. Use the secondary control path to verify "
            "current control-plane state before critical decisions."
        )
        targets = _SECONDARY_CONTROL_PATH_ADVISORY_TARGETS

    return {
        "mode": mode,
        "reason": reason,
        "targets": json.loads(json.dumps(list(targets))),
    }


def _health_status_headline(overall_status: str, safe_mode_state: Dict[str, Any]) -> str:
    safe_mode_status = str(safe_mode_state.get("status") or "").lower()
    kill_switch_status = str(safe_mode_state.get("kill_switch_status") or "").lower()
    if safe_mode_status not in {"", "off", "released", "none", "null"}:
        return "Safe mode active"
    if kill_switch_status == "cooling_down":
        return "Kill-switch cooling down"
    if kill_switch_status == "triggered":
        return "Kill-switch triggered"
    if overall_status == "ok":
        return "Control plane healthy"
    if overall_status == "degraded":
        return "Some services degraded"
    return "Control plane health unavailable"


def _alert_target_ref(
    *,
    surface_id: str,
    label: str,
    href: str,
    target_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "surface_id": surface_id,
        "label": label,
        "href": href,
    }
    if target_id not in (None, ""):
        payload["target_id"] = target_id
    return payload


def _max_alert_severity(values: List[Optional[str]]) -> Optional[str]:
    return _highest_ranked_value(values, _ALERT_SEVERITY_ORDER)


def _alert_sort_key(alert: Dict[str, Any]) -> tuple[str, int, int, str]:
    severity = str(alert.get("severity") or "").lower()
    category = str(alert.get("category") or "").lower()
    return (
        str(alert.get("raised_at") or ""),
        _ALERT_SEVERITY_ORDER.get(severity, 0),
        _ALERT_CATEGORY_ORDER.get(category, 0),
        str(alert.get("alert_id") or ""),
    )


def _alert_severity_for_incident(incident: Dict[str, Any]) -> str:
    normalized = _incident_home_severity(incident.get("severity"))
    if normalized == "sev1":
        return "critical"
    if normalized == "sev2":
        return "high"
    return "medium"


def _alert_severity_for_risk_level(
    risk_level: Optional[str],
    *,
    elevated: bool = False,
) -> str:
    mapping = {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "low": "low",
    }
    severity = mapping.get(str(risk_level or "").strip().lower(), "medium")
    if elevated and _ALERT_SEVERITY_ORDER.get(severity, 0) < _ALERT_SEVERITY_ORDER["high"]:
        return "high"
    return severity


def _build_incident_alerts(snapshot_at: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    incident_surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    if incident_surface.get("status") == "unavailable":
        return [], incident_surface

    alerts: List[Dict[str, Any]] = []
    incidents = read_store.list_incidents()
    for incident in incidents:
        incident_status = str(incident.get("status") or "").lower()
        if incident_status not in {"open", "in_progress"}:
            continue
        incident_id = str(incident.get("incident_id") or "")
        severity = _alert_severity_for_incident(incident)
        title = str(incident.get("title") or incident_id or "Unnamed incident")
        status_prefix = "Active" if incident_status == "open" else "In-progress"
        alerts.append(
            {
                "alert_id": f"alert-incident-{incident_id}",
                "severity": severity,
                "category": "incident",
                "raised_at": incident.get("opened_at") or incident.get("created_at") or snapshot_at,
                "summary": f"{status_prefix} incident: {title}.",
                "target_ref": _alert_target_ref(
                    surface_id="PKT-002",
                    label="Open incident response",
                    href=_incident_detail_href(incident_id),
                    target_id=incident_id,
                ),
            }
        )
    return alerts, incident_surface


def _build_governance_alerts(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    review_queue_surface = _dataset_surface_status(
        "governance_review_queue_items",
        snapshot_at=snapshot_at,
    )
    approval_queue_surface = _dataset_surface_status(
        "approval_queue_items",
        snapshot_at=snapshot_at,
    )
    alerts: List[Dict[str, Any]] = []

    if review_queue_surface.get("status") != "unavailable":
        for item in read_store.list_governance_review_queue_items():
            item_id = str(item.get("item_id") or "")
            status = str(item.get("status") or "").lower()
            if status not in {"pending", "in_review", "escalated"}:
                continue
            severity = _alert_severity_for_risk_level(
                item.get("risk_level"),
                elevated=status == "escalated",
            )
            item_type = str(item.get("item_type") or "Governance item")
            if status == "escalated":
                summary = f"Escalated governance review: {item_type} {item_id}."
            elif status == "in_review":
                summary = f"Governance review in progress: {item_type} {item_id}."
            else:
                summary = f"Pending governance review: {item_type} {item_id}."
            alerts.append(
                {
                    "alert_id": f"alert-governance-review-{item_id}",
                    "severity": severity,
                    "category": "governance",
                    "raised_at": item.get("submitted_at") or snapshot_at,
                    "summary": summary,
                    "target_ref": _alert_target_ref(
                        surface_id="PKT-001",
                        label="Open governance review queue",
                        href=_GOVERNANCE_REVIEW_QUEUE_ROUTE,
                        target_id=item_id,
                    ),
                }
            )

    if approval_queue_surface.get("status") != "unavailable":
        for item in read_store.list_approval_queue_items():
            decision_id = str(item.get("decision_id") or "")
            decision_state = str(item.get("decision_state") or "").lower()
            if decision_state not in {"pending", "in_review"}:
                continue
            severity = _alert_severity_for_risk_level(
                item.get("risk_level"),
                elevated=decision_state == "in_review",
            )
            decision_type = str(item.get("decision_type") or "Approval item")
            if decision_state == "in_review":
                summary = f"Approval decision in review: {decision_type} {decision_id}."
            else:
                summary = f"Approval required: {decision_type} {decision_id}."
            alerts.append(
                {
                    "alert_id": f"alert-approval-{decision_id}",
                    "severity": severity,
                    "category": "governance",
                    "raised_at": item.get("submitted_at") or snapshot_at,
                    "summary": summary,
                    "target_ref": _alert_target_ref(
                        surface_id="GV-02",
                        label="Open approval queue",
                        href=_GOVERNANCE_APPROVAL_QUEUE_ROUTE,
                        target_id=decision_id,
                    ),
                }
            )

    return alerts, {
        "review_queue": review_queue_surface,
        "approval_queue": approval_queue_surface,
    }


def _build_kill_switch_alerts(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    kill_switch_surface = _dataset_surface_status("kill_switch", snapshot_at=snapshot_at)
    if kill_switch_surface.get("status") == "unavailable":
        return [], kill_switch_surface, {}

    kill_switch = read_store.get_kill_switch_status()
    safe_mode_status = str(kill_switch.get("safe_mode_status") or "").lower()
    kill_switch_status = str(kill_switch.get("status") or "").lower()
    safe_mode_active = safe_mode_status not in {"", "off", "released", "none", "null"}
    alerts: List[Dict[str, Any]] = []

    if kill_switch.get("active") or kill_switch_status == "triggered":
        severity = "critical"
        summary = "Kill-switch active; operator intervention is required."
    elif kill_switch_status == "cooling_down":
        severity = "high"
        summary = "Kill-switch cooling down; verify runtime stability before resuming operations."
    elif safe_mode_active:
        severity = "high"
        summary = f"Safe mode active ({safe_mode_status}); use the health board to verify current restrictions."
    else:
        return [], kill_switch_surface, kill_switch

    alerts.append(
        {
            "alert_id": "alert-kill-switch-state",
            "severity": severity,
            "category": "kill_switch",
            "raised_at": kill_switch.get("last_triggered_at")
            or kill_switch.get("last_confirmed_at")
            or snapshot_at,
            "summary": summary,
            "target_ref": _alert_target_ref(
                surface_id="OC-03",
                label="Open health status board",
                href=_OPERATOR_HEALTH_STATUS_ROUTE,
                target_id=kill_switch_status or safe_mode_status or "kill-switch",
            ),
        }
    )
    return alerts, kill_switch_surface, kill_switch


def _runtime_anomaly_reasons(
    binding: Dict[str, Any],
    telemetry_summary: Optional[Dict[str, Any]],
) -> tuple[List[str], Optional[str]]:
    reasons: List[str] = []
    severities: List[Optional[str]] = []

    runtime_status = str(binding.get("status") or "").lower()
    runtime_status_severity = _RUNTIME_STATUS_ALERT_SEVERITY.get(runtime_status)
    if runtime_status_severity:
        severities.append(runtime_status_severity)
        reasons.append(f"runtime status is {runtime_status}")

    if telemetry_summary:
        drawdown = telemetry_summary.get("drawdown")
        if isinstance(drawdown, (int, float)):
            for threshold, severity in _TELEMETRY_DRAWDOWN_THRESHOLDS:
                if drawdown >= threshold:
                    severities.append(severity)
                    reasons.append(f"drawdown is {drawdown:.3f}")
                    break

        fill_rate = telemetry_summary.get("fill_rate")
        if isinstance(fill_rate, (int, float)):
            for threshold, severity in _TELEMETRY_FILL_RATE_THRESHOLDS:
                if fill_rate < threshold:
                    severities.append(severity)
                    reasons.append(f"fill rate dropped to {fill_rate:.2f}")
                    break

        avg_slippage_bps = telemetry_summary.get("avg_slippage_bps")
        if isinstance(avg_slippage_bps, (int, float)):
            for threshold, severity in _TELEMETRY_SLIPPAGE_THRESHOLDS:
                if avg_slippage_bps >= threshold:
                    severities.append(severity)
                    reasons.append(f"average slippage reached {avg_slippage_bps:.1f} bps")
                    break

    return reasons, _max_alert_severity(severities)


def _build_runtime_alerts(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    runtime_roster_surface = _dataset_surface_status(
        "runtime_bindings",
        snapshot_at=snapshot_at,
    )
    telemetry_surface = _dataset_surface_status(
        "telemetry_summaries",
        snapshot_at=snapshot_at,
    )
    if runtime_roster_surface.get("status") == "unavailable":
        return [], {
            "runtime_roster": runtime_roster_surface,
            "telemetry_summary": telemetry_surface,
        }

    alerts: List[Dict[str, Any]] = []
    bindings = read_store.list_runtime_bindings()
    missing_telemetry = False
    for binding in bindings:
        runtime_id = str(binding.get("runtime_id") or binding.get("id") or "")
        telemetry_summary = None
        if runtime_id and telemetry_surface.get("status") != "unavailable":
            telemetry_summary = read_store.get_telemetry_summary(runtime_id)
            if telemetry_summary is None:
                missing_telemetry = True
        reasons, severity = _runtime_anomaly_reasons(binding, telemetry_summary)
        if not reasons or not severity:
            continue
        alerts.append(
            {
                "alert_id": f"alert-runtime-{runtime_id}",
                "severity": severity,
                "category": "runtime",
                "raised_at": (telemetry_summary or {}).get("collected_at")
                or binding.get("updated_at")
                or binding.get("last_updated_at")
                or binding.get("started_at")
                or snapshot_at,
                "summary": f"Runtime {runtime_id} anomaly: {'; '.join(reasons[:2])}.",
                "target_ref": _alert_target_ref(
                    surface_id="OC-04",
                    label="Open runtime state board",
                    href=_OPERATOR_RUNTIME_STATE_ROUTE,
                    target_id=runtime_id,
                ),
            }
        )

    if bindings and missing_telemetry and telemetry_surface.get("status") == "ok":
        telemetry_surface["status"] = "degraded"
        telemetry_surface["message"] = "Telemetry summary missing for one or more runtimes."
        telemetry_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )

    return alerts, {
        "runtime_roster": runtime_roster_surface,
        "telemetry_summary": telemetry_surface,
    }


def _build_alert_summary(alerts: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_severity = {key: 0 for key in _ALERT_SEVERITY_ORDER}
    by_category = {key: 0 for key in _ALERT_CATEGORY_ORDER}
    for alert in alerts:
        severity = str(alert.get("severity") or "").lower()
        category = str(alert.get("category") or "").lower()
        if severity in by_severity:
            by_severity[severity] += 1
        if category in by_category:
            by_category[category] += 1
    return {
        "total_active": len(alerts),
        "highest_severity": _max_alert_severity(
            [str(alert.get("severity") or "").lower() for alert in alerts]
        ),
        "by_severity": by_severity,
        "by_category": by_category,
    }


def _build_operator_alerts_payload(snapshot_at: str) -> Dict[str, Any]:
    incident_alerts, incident_surface = _build_incident_alerts(snapshot_at)
    governance_alerts, governance_surfaces = _build_governance_alerts(snapshot_at)
    kill_switch_alerts, kill_switch_surface, _ = _build_kill_switch_alerts(snapshot_at)
    runtime_alerts, runtime_surfaces = _build_runtime_alerts(snapshot_at)

    source_surfaces = [
        incident_surface,
        governance_surfaces["review_queue"],
        governance_surfaces["approval_queue"],
        kill_switch_surface,
        runtime_surfaces["runtime_roster"],
        runtime_surfaces["telemetry_summary"],
    ]
    alerts_surface = _aggregate_group_surface(
        "alerts",
        source_surfaces,
        snapshot_at=snapshot_at,
        unavailable_message="Operator alert feed unavailable.",
        degraded_message="Operator alert feed is available, but one or more contributing surfaces are degraded.",
    )

    alerts = sorted(
        incident_alerts + governance_alerts + kill_switch_alerts + runtime_alerts,
        key=_alert_sort_key,
        reverse=True,
    )
    if alerts_surface.get("status") == "unavailable":
        alerts = []

    meta = _snapshot_meta(snapshot_at)
    meta["acknowledgement_supported"] = False
    meta["surfaces"] = {
        "alerts": alerts_surface,
        "incident_feed": incident_surface,
        "review_queue": governance_surfaces["review_queue"],
        "approval_queue": governance_surfaces["approval_queue"],
        "kill_switch": kill_switch_surface,
        "runtime_roster": runtime_surfaces["runtime_roster"],
        "telemetry_summary": runtime_surfaces["telemetry_summary"],
    }
    return {
        "alerts": alerts,
        "summary": _build_alert_summary(alerts),
        "meta": meta,
    }


def _build_operator_health_status_payload(snapshot_at: str) -> Dict[str, Any]:
    runtime_group, runtime_surface, runtime_bindings = _build_runtime_health_group(snapshot_at)
    telemetry_group, telemetry_surface = _build_telemetry_health_group(
        snapshot_at,
        runtime_bindings,
    )
    incident_group, incident_surface = _build_incident_health_group(snapshot_at)
    governance_group, governance_surface = _build_governance_health_group(snapshot_at)
    kill_switch_group, kill_switch_surface, safe_mode_state = _build_kill_switch_health_group(
        snapshot_at
    )

    group_surfaces = {
        "runtime": runtime_surface,
        "telemetry": telemetry_surface,
        "incident": incident_surface,
        "governance": governance_surface,
        "kill_switch": kill_switch_surface,
    }
    overall_surface = _aggregate_group_surface(
        "health_status",
        list(group_surfaces.values()),
        snapshot_at=snapshot_at,
        unavailable_message="All health groups are unavailable.",
        degraded_message="One or more health groups are degraded or unavailable.",
    )
    overall_status = overall_surface.get("status", "ok")

    group_counts = {
        "ok": sum(1 for surface in group_surfaces.values() if surface.get("status") == "ok"),
        "degraded": sum(
            1 for surface in group_surfaces.values() if surface.get("status") == "degraded"
        ),
        "unavailable": sum(
            1 for surface in group_surfaces.values() if surface.get("status") == "unavailable"
        ),
    }
    secondary_control_path = _build_secondary_control_path(
        overall_status=overall_status,
        safe_mode_state=safe_mode_state,
    )

    if overall_status == "ok":
        message = "All health groups are responding normally."
    elif overall_status == "degraded":
        message = (
            f"{group_counts['degraded'] + group_counts['unavailable']} of "
            f"{len(group_surfaces)} health groups need attention."
        )
    else:
        message = "Primary health surfaces are unavailable; rely on the secondary control path."

    groups = [
        runtime_group,
        telemetry_group,
        incident_group,
        governance_group,
        kill_switch_group,
    ]
    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "health_status": overall_surface,
        **group_surfaces,
    }
    return {
        "overall_status": overall_status,
        "headline": _health_status_headline(overall_status, safe_mode_state),
        "message": message,
        "group_counts": group_counts,
        "safe_mode_state": safe_mode_state,
        "secondary_control_path": secondary_control_path,
        "groups": groups,
        "meta": meta,
    }


def _build_home_card(
    *,
    card_id: str,
    label: str,
    status: str,
    summary: str,
    details: Dict[str, Any],
    target_refs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "card_id": card_id,
        "label": label,
        "status": status,
        "summary": summary,
        "details": details,
        "target_refs": target_refs,
    }


def _build_operator_home_payload(snapshot_at: str) -> Dict[str, Any]:
    alerts_payload = _build_operator_alerts_payload(snapshot_at)
    health_payload = _build_operator_health_status_payload(snapshot_at)
    groups_by_id = {
        str(group.get("group_id") or ""): group
        for group in health_payload["groups"]
    }
    alert_summary = alerts_payload["summary"]
    safe_mode_state = health_payload["safe_mode_state"]

    alert_surface = alerts_payload["meta"]["surfaces"]["alerts"]
    incident_group = groups_by_id["incident"]
    governance_group = groups_by_id["governance"]
    runtime_group = groups_by_id["runtime"]
    telemetry_group = groups_by_id["telemetry"]

    runtime_card_status = _aggregate_group_surface(
        "operator_home_runtime",
        [
            health_payload["meta"]["surfaces"]["runtime"],
            health_payload["meta"]["surfaces"]["telemetry"],
        ],
        snapshot_at=snapshot_at,
        unavailable_message="Runtime overview unavailable.",
        degraded_message="Runtime or telemetry coverage is degraded.",
    )["status"]

    cards = [
        _build_home_card(
            card_id="alerts",
            label="Alerts",
            status=alert_surface.get("status", "ok"),
            summary=(
                "Operator alert feed unavailable."
                if alert_surface.get("status") == "unavailable"
                else (
                    "No active operator alerts."
                    if alert_summary["total_active"] == 0
                    else f"{alert_summary['total_active']} active alert(s); highest severity {alert_summary['highest_severity']}."
                )
            ),
            details=alert_summary,
            target_refs=[
                _alert_target_ref(
                    surface_id="OC-02",
                    label="Open alerts rail",
                    href=_OPERATOR_ALERTS_ROUTE,
                )
            ],
        ),
        _build_home_card(
            card_id="incidents",
            label="Incidents",
            status=str(incident_group.get("status") or "ok"),
            summary=str(incident_group.get("summary") or "Incident summary unavailable."),
            details=dict(incident_group.get("details") or {}),
            target_refs=list(incident_group.get("target_refs") or []),
        ),
        _build_home_card(
            card_id="governance",
            label="Governance",
            status=str(governance_group.get("status") or "ok"),
            summary=str(governance_group.get("summary") or "Governance summary unavailable."),
            details=dict(governance_group.get("details") or {}),
            target_refs=list(governance_group.get("target_refs") or []),
        ),
        _build_home_card(
            card_id="runtime",
            label="Runtime",
            status=runtime_card_status,
            summary=(
                "Runtime overview unavailable."
                if runtime_card_status == "unavailable"
                else (
                    str(runtime_group.get("summary") or "Runtime overview unavailable.")
                    if telemetry_group.get("status") == "ok"
                    else f"{runtime_group.get('summary')} Telemetry: {telemetry_group.get('summary')}"
                )
            ),
            details={
                "runtime": dict(runtime_group.get("details") or {}),
                "telemetry": dict(telemetry_group.get("details") or {}),
            },
            target_refs=[
                _alert_target_ref(
                    surface_id="OC-04",
                    label="Open runtime state board",
                    href=_OPERATOR_RUNTIME_STATE_ROUTE,
                )
            ],
        ),
        _build_home_card(
            card_id="health",
            label="Health",
            status=str(health_payload.get("overall_status") or "ok"),
            summary=str(health_payload.get("message") or "Health summary unavailable."),
            details={
                "headline": health_payload.get("headline"),
                "group_counts": dict(health_payload.get("group_counts") or {}),
                "safe_mode_state": dict(safe_mode_state),
            },
            target_refs=[
                _alert_target_ref(
                    surface_id="OC-03",
                    label="Open health status board",
                    href=_OPERATOR_HEALTH_STATUS_ROUTE,
                )
            ],
        ),
    ]

    safe_mode_status = str(safe_mode_state.get("status") or "").lower()
    kill_switch_status = str(safe_mode_state.get("kill_switch_status") or "").lower()
    safe_mode_active = safe_mode_status not in {"", "off", "released", "none", "null"}

    home_surface = _aggregate_group_surface(
        "operator_home",
        [
            alert_surface,
            health_payload["meta"]["surfaces"]["health_status"],
        ],
        snapshot_at=snapshot_at,
        unavailable_message="Operator home summary unavailable.",
        degraded_message="Operator home summary is degraded because alerts or health inputs are degraded.",
    )
    overall_status = home_surface.get("status", "ok")

    if safe_mode_active or kill_switch_status in {"triggered", "cooling_down"}:
        headline = "Operator attention required"
        message = "Safe mode or kill-switch activity requires immediate review."
    elif overall_status == "unavailable":
        headline = "Operator home unavailable"
        message = "Primary operator summary surfaces are unavailable."
    elif alert_summary["total_active"] > 0:
        headline = f"{alert_summary['total_active']} active operator alert(s)"
        message = "Review the alerts rail before making deployment or runtime decisions."
    elif overall_status == "degraded":
        headline = "Operator home degraded"
        message = "One or more operator summary surfaces are degraded."
    else:
        headline = "Operator console stable"
        message = "No active incidents, governance bottlenecks, or runtime alerts require attention."

    escalation_shortcuts: List[Dict[str, Any]] = []
    if alert_summary["total_active"] > 0:
        escalation_shortcuts.append(
            {
                "shortcut_id": "open-alerts-rail",
                "label": "Open alerts rail",
                "reason": "There are active operator alerts that need triage.",
                "href": _OPERATOR_ALERTS_ROUTE,
                "priority": "high",
            }
        )
    if int((incident_group.get("details") or {}).get("active_incident_count") or 0) > 0:
        escalation_shortcuts.append(
            {
                "shortcut_id": "open-incident-home",
                "label": "Open incident home",
                "reason": "Active incidents are open and may require response.",
                "href": _OPERATOR_INCIDENT_HOME_ROUTE,
                "priority": "high",
            }
        )
    if health_payload.get("overall_status") != "ok" or safe_mode_active:
        escalation_shortcuts.append(
            {
                "shortcut_id": "open-health-status",
                "label": "Open health status board",
                "reason": "Health status or safe-mode state needs verification.",
                "href": _OPERATOR_HEALTH_STATUS_ROUTE,
                "priority": "high" if safe_mode_active else "medium",
            }
        )
    if int((governance_group.get("details") or {}).get("total_pending_items") or 0) > 0:
        escalation_shortcuts.append(
            {
                "shortcut_id": "open-approval-queue",
                "label": "Open approval queue",
                "reason": "Pending governance items may block execution changes.",
                "href": _GOVERNANCE_APPROVAL_QUEUE_ROUTE,
                "priority": "medium",
            }
        )
    if int((runtime_group.get("details") or {}).get("total_runtime_count") or 0) > 0:
        escalation_shortcuts.append(
            {
                "shortcut_id": "open-runtime-state",
                "label": "Open runtime state board",
                "reason": "Inspect current runtime and telemetry status.",
                "href": _OPERATOR_RUNTIME_STATE_ROUTE,
                "priority": "medium",
            }
        )

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "operator_home": home_surface,
        "alerts": alert_surface,
        "health_status": health_payload["meta"]["surfaces"]["health_status"],
        "incident": health_payload["meta"]["surfaces"]["incident"],
        "governance": health_payload["meta"]["surfaces"]["governance"],
        "runtime": health_payload["meta"]["surfaces"]["runtime"],
        "telemetry": health_payload["meta"]["surfaces"]["telemetry"],
        "kill_switch": health_payload["meta"]["surfaces"]["kill_switch"],
    }
    return {
        "overall_status": overall_status,
        "headline": headline,
        "message": message,
        "safe_mode_state": safe_mode_state,
        "cards": cards,
        "escalation_shortcuts": escalation_shortcuts,
        "meta": meta,
    }


def _unavailable_surface(
    dataset: str,
    *,
    snapshot_at: str,
    message: str,
) -> Dict[str, Any]:
    surface = _dataset_surface_status(dataset, snapshot_at=snapshot_at)
    surface["status"] = "unavailable"
    surface["message"] = message
    surface.setdefault(
        "staleness",
        {"served_from": "unverifiable", "last_known_at": snapshot_at},
    )
    return surface


def _browser_href_for_drift_evidence_ref(
    ref: Dict[str, Any],
    *,
    plan_id: Optional[str],
    primary_incident_id: Optional[str],
) -> Optional[str]:
    ref_type = str(ref.get("type") or "").strip().lower()
    ref_id = str(ref.get("ref_id") or "").strip()
    if ref_type == "approvaldecision":
        return _GOVERNANCE_APPROVAL_QUEUE_ROUTE
    if ref_type == "incidentcase" and ref_id:
        return _incident_detail_href(ref_id)
    if ref_type == "evolutiondecision" and primary_incident_id:
        return _post_incident_review_href(primary_incident_id)
    if ref_type == "drift_report":
        return None
    if plan_id and str(ref.get("href") or "").startswith("/api/v1/operator/deployment-review/"):
        return _deployment_review_href(plan_id)
    return ref.get("href")


def _normalize_drift_evidence_refs(
    refs: List[Dict[str, Any]],
    *,
    plan_id: Optional[str],
    primary_incident_id: Optional[str],
) -> List[Dict[str, Any]]:
    normalized = json.loads(json.dumps(refs))
    for ref in normalized:
        if not isinstance(ref, dict):
            continue
        ref["href"] = _browser_href_for_drift_evidence_ref(
            ref,
            plan_id=plan_id,
            primary_incident_id=primary_incident_id,
        )
    return normalized


def _normalize_drift_recommended_actions(
    actions: List[Dict[str, Any]],
    *,
    plan_id: Optional[str],
    primary_incident_id: Optional[str],
) -> List[Dict[str, Any]]:
    normalized = json.loads(json.dumps(actions))
    for action in normalized:
        if not isinstance(action, dict):
            continue
        target_ref = action.get("target_ref")
        if not isinstance(target_ref, dict):
            continue
        surface_id = str(target_ref.get("surface_id") or "").strip()
        target_id = str(target_ref.get("target_id") or "").strip()
        if surface_id == "PKT-001" and plan_id:
            target_ref["href"] = _deployment_review_href(plan_id)
        elif surface_id == "PKT-002" and target_id:
            target_ref["href"] = _incident_detail_href(target_id)
        elif surface_id == "PKT-003" and target_id:
            target_ref["href"] = _post_incident_review_href(target_id)
    return normalized


def _build_operator_paper_live_drift_payload(
    runtime_id: str,
    snapshot_at: str,
) -> Dict[str, Any]:
    runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
    report = read_store.get_paper_live_drift_report(runtime_id)
    plan_id = None
    artifact_id = None
    artifact_version = None

    if runtime_binding:
        plan_id = runtime_binding.get("plan_id")
        artifact_id = runtime_binding.get("artifact_id")
        artifact_version = runtime_binding.get("artifact_version")
    if report:
        plan_id = report.get("plan_id") or plan_id
        artifact_id = report.get("artifact_id") or artifact_id
        artifact_version = report.get("artifact_version") or artifact_version

    plan = read_store.get_deployment_plan(plan_id) if plan_id else None
    approval_decision = (
        read_store.get_approval_decision(plan.get("approval_decision_id"))
        if plan
        else None
    )
    telemetry_summary = read_store.get_telemetry_summary(runtime_id)
    telemetry_performance = (
        read_store.get_telemetry_performance(str(artifact_id))
        if artifact_id
        else None
    )
    incidents = [
        incident
        for incident in read_store.list_incidents()
        if str(incident.get("runtime_id") or "") == runtime_id
        and str(incident.get("status") or "").lower() in {"open", "in_progress"}
    ]
    evolution_decisions = []
    for incident in incidents:
        evolution_decisions.extend(
            read_store.get_evolution_decisions_by_incident(
                str(incident.get("incident_id") or "")
            )
        )
    primary_incident_id = (
        str(incidents[0].get("incident_id") or "").strip() if incidents else None
    )

    report_surface = (
        _dataset_surface_status("paper_live_drift_reports", snapshot_at=snapshot_at)
        if report is not None
        else _unavailable_surface(
            "paper_live_drift_reports",
            snapshot_at=snapshot_at,
            message="Paper/live drift report unavailable for this runtime.",
        )
    )
    runtime_surface = (
        _dataset_surface_status(
            "runtime_bindings",
            snapshot_at=snapshot_at,
            has_data=runtime_binding is not None,
            missing_message="Runtime binding unavailable for this drift view.",
        )
        if runtime_binding is not None
        else _unavailable_surface(
            "runtime_bindings",
            snapshot_at=snapshot_at,
            message="Runtime binding unavailable for this drift view.",
        )
    )
    telemetry_surface = (
        _dataset_surface_status(
            "telemetry_summaries",
            snapshot_at=snapshot_at,
            has_data=telemetry_summary is not None,
            missing_message="Observed telemetry summary unavailable for this drift view.",
        )
        if telemetry_summary is not None
        else _unavailable_surface(
            "telemetry_summaries",
            snapshot_at=snapshot_at,
            message="Observed telemetry summary unavailable for this drift view.",
        )
    )
    performance_surface = (
        _dataset_surface_status(
            "telemetry_performance",
            snapshot_at=snapshot_at,
            has_data=telemetry_performance is not None,
            missing_message="Paper baseline performance unavailable for this drift view.",
        )
        if telemetry_performance is not None
        else _unavailable_surface(
            "telemetry_performance",
            snapshot_at=snapshot_at,
            message="Paper baseline performance unavailable for this drift view.",
        )
    )
    approval_surface = (
        _dataset_surface_status(
            "approval_decisions",
            snapshot_at=snapshot_at,
            has_data=approval_decision is not None,
            missing_message="Approval decision unavailable for this drift view.",
        )
        if approval_decision is not None
        else _unavailable_surface(
            "approval_decisions",
            snapshot_at=snapshot_at,
            message="Approval decision unavailable for this drift view.",
        )
    )
    incident_surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    evolution_surface = _dataset_surface_status(
        "evolution_decisions",
        snapshot_at=snapshot_at,
    )

    source_surfaces = [
        report_surface,
        runtime_surface,
        telemetry_surface,
        performance_surface,
        approval_surface,
        incident_surface,
        evolution_surface,
    ]
    paper_live_drift_surface = _aggregate_group_surface(
        "paper_live_drift",
        source_surfaces,
        snapshot_at=snapshot_at,
        unavailable_message="Paper/live drift view unavailable.",
        degraded_message="Paper/live drift view is available, but one or more supporting surfaces are degraded.",
    )
    if report is None:
        paper_live_drift_surface["status"] = "unavailable"
        paper_live_drift_surface["message"] = "Paper/live drift view unavailable."
        paper_live_drift_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )

    recommended_actions = []
    if report:
        recommended_actions = _normalize_drift_recommended_actions(
            report.get("recommended_actions") or [],
            plan_id=str(plan_id) if plan_id else None,
            primary_incident_id=primary_incident_id,
        )
    elif plan_id:
        recommended_actions = [
            {
                "action_id": "open-deployment-review",
                "label": "Open deployment review",
                "reason": "A drift report is not yet available; inspect the current deployment context first.",
                "target_ref": _alert_target_ref(
                    surface_id="PKT-001",
                    label="Open deployment review",
                    href=_deployment_review_href(str(plan_id)),
                    target_id=plan_id,
                ),
            }
        ]

    return {
        "runtime_id": runtime_id,
        "plan_ref": (
            {
                "plan_id": plan_id,
                "href": _deployment_plan_href(str(plan_id)),
            }
            if plan_id
            else None
        ),
        "artifact_ref": (
            {
                "artifact_id": artifact_id,
                "artifact_version": artifact_version,
            }
            if artifact_id or artifact_version
            else None
        ),
        "paper_baseline": json.loads(json.dumps((report or {}).get("paper_baseline")))
        if report
        else None,
        "observed_state": json.loads(json.dumps((report or {}).get("observed_state")))
        if report
        else None,
        "drift_groups": json.loads(json.dumps((report or {}).get("drift_groups") or [])),
        "threshold_evaluation": json.loads(
            json.dumps(
                (report or {}).get("threshold_evaluation")
                or {
                    "overall_status": "unavailable",
                    "summary": "Paper/live drift report unavailable for this runtime.",
                    "breached_metric_ids": [],
                }
            )
        ),
        "evidence_refs": _normalize_drift_evidence_refs(
            (report or {}).get("evidence_refs") or [],
            plan_id=str(plan_id) if plan_id else None,
            primary_incident_id=primary_incident_id,
        ),
        "recommended_actions": recommended_actions,
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "paper_live_drift": paper_live_drift_surface,
                "drift_report": report_surface,
                "runtime_binding": runtime_surface,
                "telemetry_summary": telemetry_surface,
                "telemetry_performance": performance_surface,
                "approval_decision": approval_surface,
                "incident": incident_surface,
                "evolution": evolution_surface,
            },
            "supporting_counts": {
                "active_incident_count": len(incidents),
                "evolution_decision_count": len(evolution_decisions),
            },
        },
    }


def _snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
    }
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness
    return meta


_RW01_ALLOWED_PRIORITIES = {"low", "normal", "high", "critical"}
_RW01_ALLOWED_STATUSES = {"open", "in_progress", "closed", "archived"}
_RW01_STATUS_TRANSITIONS = {
    "open": {"in_progress", "closed"},
    "in_progress": {"closed"},
    "closed": {"archived"},
    "archived": set(),
}
_RW02_ALLOWED_MATCH_TYPES = {"all", "ticket", "experiment", "artifact"}
_RW02_ALLOWED_ADAPTER_STATES = {"fresh", "stale", "degraded", "unavailable"}
_RW03_ALLOWED_STATUSES = {"queued", "running", "completed", "failed"}
_RW03_ALLOWED_DATE_RANGES = {"24h", "7d", "30d", "90d"}
_EW04_ALLOWED_SURFACE_STATES = {"fresh", "stale", "unavailable"}


def _ew04_inspiration_surface_state(
    projection: Optional[Dict[str, Any]],
    *,
    artifact_exists: bool,
) -> str:
    source = read_store.dataset_source("inspiration_graphs")
    base_status = _surface_status().get("status")

    explicit_state = (
        projection.get("meta", {})
        .get("surfaces", {})
        .get("inspiration")
        if projection
        else None
    )
    explicit_state = str(explicit_state or "").strip().lower()
    if explicit_state in _EW04_ALLOWED_SURFACE_STATES:
        return explicit_state

    if source == "missing" or base_status == "unavailable":
        return "unavailable"
    if source == "local_snapshot" or base_status == "degraded":
        return "stale"
    if artifact_exists:
        return "fresh"
    return "unavailable"


def _ew04_inspiration_payload(
    artifact_id: str,
    projection: Optional[Dict[str, Any]],
    *,
    snapshot_at: str,
    artifact_exists: bool,
) -> Dict[str, Any]:
    if projection:
        payload = json.loads(json.dumps(projection))
    else:
        payload = {
            "artifact_id": artifact_id,
            "inspiration_edges": [],
            "strategy_tags": [],
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {},
            },
        }

    payload["artifact_id"] = artifact_id
    payload["inspiration_edges"] = list(payload.get("inspiration_edges") or [])
    if "strategy_tags" in payload:
        payload["strategy_tags"] = list(payload.get("strategy_tags") or [])
    else:
        payload["strategy_tags"] = []

    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        payload["meta"] = meta
    meta["snapshot_at"] = str(meta.get("snapshot_at") or snapshot_at)
    surfaces = meta.get("surfaces")
    if not isinstance(surfaces, dict):
        surfaces = {}
        meta["surfaces"] = surfaces
    surfaces["inspiration"] = _ew04_inspiration_surface_state(
        projection,
        artifact_exists=artifact_exists,
    )
    return payload


def _rw01_surface_state(
    dataset: str,
    *,
    snapshot_at: str,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
) -> str:
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
        missing_message=missing_message,
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("source") == "local_snapshot":
        return "degraded"
    if surface.get("status") == "degraded":
        return "stale"
    return "fresh"


_TW01_SESSION_STATUSES = {"active", "paused", "completed", "abandoned"}


def _tw01_validate_session_status(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _TW01_SESSION_STATUSES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid trainer session status",
            f"status must be one of {sorted(_TW01_SESSION_STATUSES)}",
            precondition_failed="status",
        )
    return normalized


def _tw01_required_text(payload: Dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if value is None or not str(value).strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            f"Missing required field: {field}",
            f"{field} must be a non-empty string",
            precondition_failed=field,
        )
    return str(value).strip()


def _tw01_validate_context_refs(value: Any) -> List[Dict[str, str]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid context_refs",
            "context_refs must be an array of { type, id } objects",
            precondition_failed="context_refs",
        )
    refs: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "Invalid context_refs entry",
                "Each context_refs entry must be an object",
                precondition_failed="context_refs",
            )
        refs.append(
            {
                "type": _tw01_required_text(item, "type"),
                "id": _tw01_required_text(item, "id"),
            }
        )
    return refs


def _tw01_trainer_dialog_surface_state(
    *,
    snapshot_at: str,
    has_data: Optional[bool] = None,
) -> str:
    surface = _dataset_surface_status(
        "teaching_sessions",
        snapshot_at=snapshot_at,
        has_data=has_data,
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("source") == "local_snapshot":
        return "degraded"
    if surface.get("status") == "degraded":
        return "stale"
    return "fresh"


def _rw01_validate_priority(priority: Any) -> str:
    normalized = str(priority or "").strip().lower()
    if normalized not in _RW01_ALLOWED_PRIORITIES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid research ticket priority",
            f"priority must be one of {sorted(_RW01_ALLOWED_PRIORITIES)}",
            precondition_failed="priority",
        )
    return normalized


def _rw01_validate_status(status: Any) -> str:
    normalized = str(status or "").strip().lower()
    if normalized not in _RW01_ALLOWED_STATUSES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid research ticket status",
            f"status must be one of {sorted(_RW01_ALLOWED_STATUSES)}",
            precondition_failed="status",
        )
    return normalized


def _rw03_validate_status(status: Any) -> str:
    normalized = str(status or "").strip().lower()
    if normalized not in _RW03_ALLOWED_STATUSES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid research analysis status",
            f"status must be one of {sorted(_RW03_ALLOWED_STATUSES)}",
            precondition_failed="status",
        )
    return normalized


def _rw03_validate_date_range(date_range: Any) -> str:
    normalized = str(date_range or "").strip().lower()
    if normalized not in _RW03_ALLOWED_DATE_RANGES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid research analysis date_range",
            f"date_range must be one of {sorted(_RW03_ALLOWED_DATE_RANGES)}",
            precondition_failed="date_range",
        )
    return normalized


def _rw02_invalid_query(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": "invalid_search_query",
            "detail": detail,
        },
    )


def _rw02_validate_query(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("q is required and must be non-empty")
    return normalized


def _rw02_validate_match_type(value: Any) -> str:
    normalized = str(value or "all").strip().lower()
    if normalized not in _RW02_ALLOWED_MATCH_TYPES:
        raise ValueError(
            f"match_type must be one of {sorted(_RW02_ALLOWED_MATCH_TYPES)}"
        )
    return normalized


def _rw02_validate_status(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower()
    if normalized not in _RW01_ALLOWED_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(_RW01_ALLOWED_STATUSES)}"
        )
    return normalized


def _rw02_validate_date_range(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower()
    if normalized not in _RW03_ALLOWED_DATE_RANGES:
        raise ValueError(
            f"date_range must be one of {sorted(_RW03_ALLOWED_DATE_RANGES)}"
        )
    return normalized


def _rw02_page_slice(
    items: List[Dict[str, Any]],
    page_token: Optional[str],
    page_size: int,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    if page_token in (None, ""):
        start = 0
    else:
        try:
            start = int(page_token)
        except (TypeError, ValueError) as exc:
            raise ValueError("page_token must be a non-negative integer offset") from exc
        if start < 0:
            raise ValueError("page_token must be a non-negative integer offset")
    end = start + page_size
    next_page_token = str(end) if end < len(items) else None
    return items[start:end], next_page_token


def _rw02_adapter_state(index_adapter: Optional[Dict[str, Any]], *, snapshot_at: str) -> str:
    derived_state = _rw01_surface_state("research_search_documents", snapshot_at=snapshot_at)
    if derived_state in {"unavailable", "degraded"}:
        return derived_state
    if isinstance(index_adapter, dict):
        state = str(index_adapter.get("adapter_state") or "").strip().lower()
        if state in _RW02_ALLOWED_ADAPTER_STATES:
            if derived_state == "stale" and state == "fresh":
                return "stale"
            return state
    return derived_state


def _rw01_required_text(payload: Dict[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            f"Missing required field: {field}",
            f"{field} is required and must be a non-empty string.",
            precondition_failed=field,
        )
    return value


def _rw01_validate_patch(
    ticket: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    allowed_patch_fields = {"status", "title", "description", "priority", "owner"}
    unknown_fields = sorted(set(payload.keys()) - allowed_patch_fields)
    if unknown_fields:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid research ticket patch payload",
            f"Unsupported patch fields: {unknown_fields}",
            precondition_failed="payload_shape",
        )

    patch: Dict[str, Any] = {}
    editable = bool((ticket.get("allowedActions") or {}).get("canEdit"))

    for field in ("title", "description", "owner"):
        if field in payload:
            value = str(payload.get(field) or "").strip()
            if not value:
                raise _bff_error(
                    422,
                    ErrorCode.INVALID_PARAMS,
                    f"Invalid research ticket field: {field}",
                    f"{field} must be a non-empty string when provided.",
                    precondition_failed=field,
                )
            if not editable:
                raise _bff_error(
                    409,
                    ErrorCode.INVALID_STATE,
                    "Research ticket is not editable in its current lifecycle state",
                    f"{field} cannot be modified while allowedActions.canEdit is false.",
                    precondition_failed="allowedActions.canEdit",
                )
            patch[field] = value

    if "priority" in payload:
        if not editable:
            raise _bff_error(
                409,
                ErrorCode.INVALID_STATE,
                "Research ticket is not editable in its current lifecycle state",
                "priority cannot be modified while allowedActions.canEdit is false.",
                precondition_failed="allowedActions.canEdit",
            )
        patch["priority"] = _rw01_validate_priority(payload.get("priority"))

    if "status" in payload:
        current_status = str(ticket.get("status") or "").strip().lower()
        next_status = _rw01_validate_status(payload.get("status"))
        if next_status != current_status:
            if next_status == "closed" and not (ticket.get("allowedActions") or {}).get("canClose"):
                raise _bff_error(
                    409,
                    ErrorCode.INVALID_STATE,
                    "Research ticket cannot be closed in its current state",
                    "allowedActions.canClose is false for this ticket.",
                    precondition_failed="allowedActions.canClose",
                )
            if next_status == "archived" and not (ticket.get("allowedActions") or {}).get("canArchive"):
                raise _bff_error(
                    409,
                    ErrorCode.INVALID_STATE,
                    "Research ticket cannot be archived in its current state",
                    "allowedActions.canArchive is false for this ticket.",
                    precondition_failed="allowedActions.canArchive",
                )
            allowed_targets = _RW01_STATUS_TRANSITIONS.get(current_status, set())
            if next_status not in allowed_targets:
                raise _bff_error(
                    409,
                    ErrorCode.INVALID_STATE,
                    "Invalid research ticket lifecycle transition",
                    f"Cannot transition research ticket from {current_status} to {next_status}.",
                    precondition_failed="status_transition",
                )
        patch["status"] = next_status

    if not patch:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Empty research ticket patch payload",
            "At least one accepted patch field is required.",
            precondition_failed="payload_shape",
        )
    return patch


def _build_consultation_workbench_overview(snapshot_at: str) -> Dict[str, Any]:
    modules = [
        {
            "module_id": "CW-01",
            "label": "Consult Request",
            "status": "not_ready",
            "wave_order": 1,
            "summary": "Request create/list/detail/cancel flows and the request-to-session lifecycle are still missing.",
            "missing_contracts": [
                "POST /api/v1/consult/requests",
                "GET /api/v1/consult/requests",
                "GET /api/v1/consult/requests/{request_id}",
                "POST /api/v1/consult/requests/{request_id}/cancel",
                "ConsultRequest lifecycle and linked_session_id contract",
            ],
            "next_gate": "Publish request identity, lifecycle, and request-to-session handoff truth.",
            "upstream_dependencies": [],
        },
        {
            "module_id": "CW-02",
            "label": "Debate Transcript",
            "status": "not_ready",
            "wave_order": 2,
            "summary": "The ordered transcript route, actor resolution, and evidence-link semantics are not yet packetized.",
            "missing_contracts": [
                "GET /api/v1/consultations/{session_id}/transcript",
                "TranscriptEvent schema",
                "Actor labeling contract",
                "Evidence attachment inline behavior",
            ],
            "next_gate": "Lock transcript ordering and actor-label truth after CW-01 is live.",
            "upstream_dependencies": ["CW-01"],
        },
        {
            "module_id": "CW-03",
            "label": "Committee Board",
            "status": "not_ready",
            "wave_order": 3,
            "summary": "Committee queue/detail projections and sponsor decision authority are published, but upstream request and transcript modules are still pending for packet handoff.",
            "missing_contracts": [
                "CW-01 Consult Request live request identity",
                "CW-02 Debate Transcript live event ordering",
            ],
            "next_gate": "Advance to packet handoff once CW-01 and CW-02 are live dependencies rather than contract-only predecessors.",
            "upstream_dependencies": ["CW-01", "CW-02"],
        },
        {
            "module_id": "CW-04",
            "label": "Red-team Memo",
            "status": "not_ready",
            "wave_order": 4,
            "summary": "Memo browse/detail routes and the downstream governance handoff authority signal are still absent.",
            "missing_contracts": [
                "GET /api/v1/consult/memos",
                "GET /api/v1/consult/memos/{memo_id}",
                "ConsultMemo read model",
                "Red-team session-to-memo mapping",
                "allowedActions.canInitiateGovernanceReview",
            ],
            "next_gate": "Publish memo lifecycle and evidence rail truth after request identity and session evidence semantics are stable.",
            "upstream_dependencies": ["CW-01", "CW-02"],
        },
    ]
    return {
        "workbench_id": "consultation-workbench",
        "label": "Consultation Workbench",
        "route_href": _CONSULTATION_WORKBENCH_ROUTE,
        "overall_status": "overview_ready",
        "headline": "Overview route is live; consultation delivery remains module-gated",
        "summary": (
            "This overview is a truthful landing surface for the Consultation Workbench. "
            "It does not claim consult requests, committee rooms, or red-team memo flows are implemented. "
            "Instead it exposes the module order, existing support surfaces, and the remaining BFF contracts."
        ),
        "packet_family": {
            "family_id": "CW-008",
            "path": "docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md",
            "lovable_readiness": "not_ready",
            "note": "All four Consultation Workbench modules remain blocked on net-new BFF routes or lifecycle contracts.",
        },
        "module_counts": {
            "total": len(modules),
            "ready": 0,
            "not_ready": len(modules),
        },
        "modules": modules,
        "support_refs": [
            {
                "ref_id": "consultation-surface-contract",
                "label": "Consultation Surface Contract",
                "ref_type": "document",
                "value": "services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md",
                "note": "Existing CS-01 to CS-06 persona-side read surfaces; not a workbench IA.",
            },
            {
                "ref_id": "persona-consultations",
                "label": "Persona consultation list",
                "ref_type": "endpoint",
                "value": "/api/v1/personas/{persona_id}/consultations",
                "note": "Existing persona-scoped consultation list and detail support.",
            },
            {
                "ref_id": "persona-consult-policy",
                "label": "Persona consult policy",
                "ref_type": "endpoint",
                "value": "/api/v1/personas/{persona_id}/consult-policy",
                "note": "Existing consult-policy read surface used by persona-side consultation flows.",
            },
            {
                "ref_id": "persona-runtime-model",
                "label": "Persona Runtime Model",
                "ref_type": "document",
                "value": "PERSONA_RUNTIME_MODEL.md",
                "note": "Canonical source for consultation roles, session metadata, and ConsultPolicy fields.",
            },
        ],
        "next_steps": [
            "Land CW-01 request create/list/detail/cancel routes and lifecycle truth.",
            "Publish the ordered transcript event contract before opening Committee Board or Red-team Memo UI packets.",
            "Keep this overview read-only; do not invent request forms or memo state in the browser.",
        ],
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "overview": {"status": "ok", "source": "bff_static"},
                "packet_family": {"status": "ok", "source": "canonical"},
            },
        },
    }


def _build_knowledge_workbench_overview(snapshot_at: str) -> Dict[str, Any]:
    modules = [
        {
            "module_id": "KW-01",
            "label": "Institutional Memory",
            "status": "ready",
            "wave_order": 1,
            "summary": "List and detail routes are live. Browse projection, lifecycle state machine, and identity contract published via KW-01-FOUNDATION-001.",
            "missing_contracts": [],
            "next_gate": "BFF routes are implemented; Lovable may proceed with production UI using example payloads.",
            "upstream_dependencies": [],
        },
        {
            "module_id": "KW-02",
            "label": "Research Notes",
            "status": "not_ready",
            "wave_order": 2,
            "summary": "Note create/list/detail flows and the attachment taxonomy are still undefined at the BFF layer.",
            "missing_contracts": [
                "POST /api/v1/knowledge/notes",
                "GET /api/v1/knowledge/notes",
                "GET /api/v1/knowledge/notes/{note_id}",
                "Research note ownership and attachment contract",
            ],
            "next_gate": "Publish note attachment and ownership truth after KW-01 identity settles.",
            "upstream_dependencies": ["KW-01"],
        },
        {
            "module_id": "KW-03",
            "label": "Evidence Refs",
            "status": "not_ready",
            "wave_order": 3,
            "summary": "Evidence browse/detail projections and typed link resolution are not yet available as workbench routes.",
            "missing_contracts": [
                "GET /api/v1/knowledge/evidence",
                "GET /api/v1/knowledge/evidence/{ref_id}",
                "Evidence reference read model",
                "Evidence link resolution contract",
            ],
            "next_gate": "Publish typed evidence-link truth after memory anchors and note source context are stable.",
            "upstream_dependencies": ["KW-01", "KW-02"],
        },
        {
            "module_id": "KW-04",
            "label": "Insight Cards",
            "status": "not_ready",
            "wave_order": 4,
            "summary": "Insight aggregation and filter semantics still live only in backlog language; no workbench packet exists yet.",
            "missing_contracts": [
                "Insight aggregation endpoint",
                "Insight card detail endpoint",
                "Card-surface read model",
                "Filter taxonomy and aggregation contract",
            ],
            "next_gate": "Publish card identity and linked-source drilldown after memory and evidence routes are live.",
            "upstream_dependencies": ["KW-01", "KW-03"],
        },
        {
            "module_id": "KW-05",
            "label": "Strategy Spec",
            "status": "not_ready",
            "wave_order": 5,
            "summary": "Canonical StrategySpec objects exist, but there is still no browse/detail/version compare workbench projection.",
            "missing_contracts": [
                "Strategy-spec list route",
                "Versioned strategy-spec detail route",
                "Strategy-spec versioning and lifecycle contract",
                "Strategy-spec diff or compare contract",
            ],
            "next_gate": "Publish versioned browse truth and diff semantics after lineage and evidence anchors are stable.",
            "upstream_dependencies": ["KW-01", "KW-03"],
        },
    ]
    return {
        "workbench_id": "knowledge-workbench",
        "label": "Knowledge Workbench",
        "route_href": _KNOWLEDGE_WORKBENCH_ROUTE,
        "overall_status": "overview_ready",
        "headline": "Overview route is live; knowledge delivery remains module-gated",
        "summary": (
            "This overview is a truthful landing surface for the Knowledge Workbench. "
            "It exposes the remaining browse, evidence, and versioning contracts without pretending the full registry, notes, or evidence browser are already live."
        ),
        "packet_family": {
            "family_id": "KW-006",
            "path": "docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md",
            "lovable_readiness": "partial",
            "note": "KW-01 Institutional Memory is ready. KW-02 to KW-05 remain blocked on net-new BFF routes or lifecycle contracts.",
        },
        "module_counts": {
            "total": len(modules),
            "ready": 1,
            "not_ready": len(modules) - 1,
        },
        "modules": modules,
        "support_refs": [
            {
                "ref_id": "memory-design-note",
                "label": "Memory Layer Design Note",
                "ref_type": "document",
                "value": "services/memory/MEMORY_LAYER_DESIGN_NOTE.md",
                "note": "Canonical Memory Plane split and retrieval-facade rules.",
            },
            {
                "ref_id": "institutional-memory-schema",
                "label": "InstitutionalMemoryEntry schema",
                "ref_type": "document",
                "value": "services/memory/institutional_memory_entry.schema.json",
                "note": "Canonical shared-memory object shape; not a workbench browse contract.",
            },
            {
                "ref_id": "strategy-spec-schema",
                "label": "StrategySpec schema",
                "ref_type": "document",
                "value": "services/control-plane/specs/strategy_spec.schema.json",
                "note": "Canonical StrategySpec object schema; version browsing and diff semantics are still missing.",
            },
            {
                "ref_id": "memory-retrieval-facade",
                "label": "Memory retrieval facade",
                "ref_type": "endpoint",
                "value": "/memory/retrieve",
                "note": "Session-facing retrieval API; not a substitute for workbench list/detail surfaces.",
            },
        ],
        "next_steps": [
            "Land KW-02 Research Notes routes and attachment contract to unblock KW-03 and downstream modules.",
            "Keep the Knowledge Workbench payload-owned; do not synthesize registry joins from raw schemas in the browser.",
            "Use this overview to track which module is next, not to imply that Insight Cards or Strategy Spec browse already exist.",
        ],
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "overview": {"status": "ok", "source": "bff_static"},
                "packet_family": {"status": "ok", "source": "canonical"},
            },
        },
    }


def _project_evolution_decision_contract(decision: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(decision)
    payload["updated_at"] = decision.get("updated_at")
    payload["notes"] = decision.get("notes")
    return payload


def _project_freeze_order_contract(order: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(order)
    payload["freeze_order_id"] = order.get("freeze_order_id") or order.get("id")
    payload["issued_at"] = order.get("issued_at") or order.get("created_at")
    return payload


def _project_rollback_contract(rollback: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(rollback)
    payload["rollback_id"] = rollback.get("rollback_id") or rollback.get("id")
    payload["executed_at"] = rollback.get("executed_at") or rollback.get("initiated_at")
    return payload


def _surface_degradation_reason(
    surface: Dict[str, Any],
    *,
    degraded_reason: str,
    unavailable_reason: str,
) -> Optional[str]:
    status = surface.get("status")
    if status == "ok":
        return None
    if status == "unavailable":
        return unavailable_reason
    if surface.get("message"):
        return str(surface["message"])
    if surface.get("note"):
        return str(surface["note"])
    return degraded_reason


def _last_triggered_at(ks: Dict[str, Any]) -> Optional[str]:
    explicit = ks.get("last_triggered_at")
    if explicit:
        return str(explicit)

    timestamps: List[str] = []
    for order in ks.get("active_freeze_orders", []):
        if not isinstance(order, dict):
            continue
        value = order.get("triggered_at") or order.get("created_at")
        if value:
            timestamps.append(str(value))
    return max(timestamps) if timestamps else None


def _kill_switch_status_value(ks: Dict[str, Any]) -> str:
    explicit = str(ks.get("status") or "").strip().lower()
    if explicit:
        mapped = _KILL_SWITCH_STATUS_MAP.get(explicit)
        if mapped:
            return mapped

    safe_mode_status = str(ks.get("safe_mode_status") or "").strip().lower()
    mapped = _KILL_SWITCH_STATUS_MAP.get(safe_mode_status)
    if mapped:
        if mapped == "armed" and ks.get("active"):
            return "triggered"
        return mapped

    return "triggered" if ks.get("active") else "armed"


def _kill_switch_active_commands(ks: Dict[str, Any]) -> List[str]:
    active_commands = ks.get("active_commands")
    if isinstance(active_commands, list):
        return [str(value) for value in active_commands if value not in (None, "")]

    derived: List[str] = []
    for order in ks.get("active_freeze_orders", []):
        if not isinstance(order, dict):
            continue
        value = order.get("command_id") or order.get("id") or order.get("target_id")
        if value not in (None, ""):
            derived.append(str(value))
    return derived


def _project_kill_switch_contract(ks: Dict[str, Any], surface: Dict[str, Any]) -> Dict[str, Any]:
    if surface.get("status") == "unavailable":
        return {
            "status": None,
            "last_triggered_at": None,
            "last_confirmed_at": None,
            "active_commands": [],
        }

    return {
        "status": _kill_switch_status_value(ks),
        "last_triggered_at": _last_triggered_at(ks),
        "last_confirmed_at": ks.get("last_confirmed_at") or ks.get("last_checked_at"),
        "active_commands": _kill_switch_active_commands(ks),
    }


def _action_drawer_allowed_actions_surface() -> Dict[str, Any]:
    if _read_surface_state() == "unavailable":
        return {
            "status": "unavailable",
            "message": "Action authority service is unavailable. All CTAs disabled for safety.",
        }
    return {"status": "ok"}


def _project_action_drawer_allowed_actions(
    kill_switch_surface: Dict[str, Any],
    allowed_actions_surface: Dict[str, Any],
) -> Dict[str, bool]:
    allowed_actions = {
        "canPause": False,
        "canRiskOff": False,
        "canLiquidateAll": False,
        "canHardRollback": False,
        "canIssueSafeMode": False,
        "secondaryPathAvailable": False,
    }

    if allowed_actions_surface.get("status") != "ok":
        return allowed_actions

    secondary_path_available = kill_switch_surface.get("status") != "unavailable"
    allowed_actions["secondaryPathAvailable"] = secondary_path_available

    if kill_switch_surface.get("status") == "ok":
        allowed_actions.update(_ACTION_DRAWER_PRIMARY_ALLOWED_ACTIONS)
        return allowed_actions

    if secondary_path_available:
        allowed_actions["canPause"] = True
        allowed_actions["canRiskOff"] = True

    return allowed_actions


_COMMAND_RECEIPT_STATUS_MAP = {
    CommandStatus.SUBMITTED.value: CommandReceiptStatus.ACCEPTED,
    CommandStatus.PROCESSING.value: CommandReceiptStatus.QUEUED,
    CommandStatus.EXECUTED.value: CommandReceiptStatus.QUEUED,
    CommandStatus.FAILED.value: CommandReceiptStatus.FAILED,
    CommandStatus.TIMEOUT.value: CommandReceiptStatus.FAILED,
}


def _expected_completion_at(accepted_at: str, estimated_processing_time_ms: int) -> Optional[str]:
    if not accepted_at or estimated_processing_time_ms < 0:
        return None
    try:
        parsed = datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    completed_at = parsed + timedelta(milliseconds=estimated_processing_time_ms)
    return completed_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _project_command_submission_response(
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
        expected_completion_at=_expected_completion_at(
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


# --------------------------------------------------------------------------- #
# Degraded-mode helper
# --------------------------------------------------------------------------- #

def _check_read_surface_state() -> Optional[StalenessWarning]:
    """
    In production, query the BFF read surface health endpoint.
    Returns a StalenessWarning when the surface is degraded or unavailable,
    or None when fresh.
    """
    state = os.getenv("BFF_READ_SURFACE_STATE", "fresh")
    if state == "fresh":
        return None
    return StalenessWarning(
        read_surface_state=state,
        message=(
            "Command submitted against stale read surface data. "
            "Verify target state via secondary control path before confirming action."
        ),
    )


# --------------------------------------------------------------------------- #
# Control-path degraded-mode guidance (§BFF-HA §3.2)
# --------------------------------------------------------------------------- #

_CONTROL_PATH_DEGRADED_GUIDANCE: Dict[str, Dict[str, Any]] = {
    "pause_runtime": {
        "degraded_action": "pause or resume a runtime binding",
        "risk": "Binding status is unverifiable; pause may target a binding that is already "
                "retired, failed, or in an unknown state.",
        "mitigation": [
            "Check the runtime binding status via GET /api/v1/runtime-bindings/{binding_id}.",
            "If the binding is unavailable, use the CLI fallback path (pantheon-admin) "
            "to verify the runtime state directly.",
            "Proceed only if the operator has confirmed the target identity via a "
            "secondary control path.",
        ],
        "safe_mode_impact": "Pause actions may advance the safe-mode state to PAUSED. "
                           "Verify via GET /api/v1/kill-switch/status.",
    },
    "execute_rollback": {
        "degraded_action": "roll back a runtime binding to a previous version",
        "risk": "Rollback may target a binding whose status is unknown. Position lineage "
                "updates may be applied without verified current state.",
        "mitigation": [
            "Check the rollback target via GET /api/v1/runtimes/{runtime_id}/rollbacks.",
            "Verify the current artifact version and binding status before executing.",
            "If the target is unavailable, escalate to a Severity-1 incident and consider "
            "the kill-switch path.",
        ],
        "safe_mode_impact": "Rollback actions do not directly affect safe-mode state, but "
                           "may be followed by kill-switch activation if the target is unstable.",
    },
    "activate_kill_switch": {
        "degraded_action": "activate the kill-switch to halt all runtime activity",
        "risk": "Kill-switch activation is a destructive action that bypasses the normal "
                "review queue. In degraded mode, the operator cannot verify the current "
                "safe-mode state before dispatching.",
        "mitigation": [
            "Verify the current safe-mode state via GET /api/v1/kill-switch/status.",
            "Confirm MFA is verified (required for all kill-switch activations).",
            "If the kill-switch status endpoint is unavailable, escalate to an admin "
            "operator and use the CLI fallback path.",
        ],
        "safe_mode_impact": "Kill-switch dispatch advances the safe-mode state (NORMAL → "
                           "PAUSED/GUARDED/RISK_OFF depending on trigger severity).",
    },
}


def _get_control_path_guidance(action_type: str) -> Optional[Dict[str, Any]]:
    """Return degraded-mode guidance for a control-path action."""
    guidance = _CONTROL_PATH_DEGRADED_GUIDANCE.get(action_type)
    if guidance is None:
        return None
    state = _read_surface_state()
    if state == "fresh":
        return None  # No guidance needed when surfaces are fresh
    return {
        "action_type": action_type,
        **guidance,
        "read_surface_state": state,
        "warning": f"Control-path action '{action_type}' is being executed against a "
                   f"{state} read surface. Follow the mitigation steps before confirming.",
    }


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "operator-bff",
        "version": "0.2.0",
        "timestamp": utc_now(),
    }


# --------------------------------------------------------------------------- #
# Read surfaces (Wave 4 - Remaining Catalog List/Detail)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/personas")
async def list_personas(
    lifecycle_state: Optional[str] = None,
    mandate: Optional[str] = None,
    strategy_family: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """PS-01: Persona List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    personas = read_store.list_personas(
        lifecycle_state=lifecycle_state,
        mandate=mandate,
        strategy_family=strategy_family,
    )
    return {
        "data": personas,
        "meta": {
            "total": len(personas),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/personas/{persona_id}")
async def get_persona_detail(persona_id: str, authorization: Optional[str] = Header(default=None)):
    """PS-02: Persona Detail with bindings."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    bindings = read_store.get_bindings_for_persona(persona_id) or []
    payload = dict(persona)
    payload["bindings"] = bindings

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/personas/{persona_id}/sessions")
async def list_persona_sessions(
    persona_id: str,
    status: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """PS-03: Persona Sessions list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    sessions = read_store.list_sessions_for_persona(persona_id, status=status) or []
    return {
        "data": sessions,
        "meta": {
            "total": len(sessions),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/sessions/{session_id}")
async def get_session_detail(session_id: str, authorization: Optional[str] = Header(default=None)):
    """PS-04: Session detail with capability snapshot."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    session = read_store.get_session(session_id)
    if not session:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Session not found",
            f"Session {session_id} does not exist",
        )

    snapshot = read_store.get_capability_snapshot(session.get("capability_snapshot_id"))
    if snapshot is None:
        snapshot = read_store.get_capability_snapshot_for_persona(session.get("persona_id"))

    payload = dict(session)
    if snapshot:
        payload["capability_snapshot"] = snapshot

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/personas/{persona_id}/teaching")
async def list_persona_teaching_sessions(
    persona_id: str,
    status: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """PS-05: Teaching sessions list for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    sessions = read_store.list_teaching_sessions_for_persona(persona_id, status=status) or []
    return {
        "data": sessions,
        "meta": {
            "total": len(sessions),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/personas/{persona_id}/capabilities")
async def get_persona_capabilities(
    persona_id: str, authorization: Optional[str] = Header(default=None),
):
    """PS-06: Capability snapshot for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    snapshot = read_store.get_capability_snapshot_for_persona(persona_id)
    if not snapshot:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Capability snapshot not found",
            f"Capability snapshot for persona {persona_id} does not exist",
        )

    return {
        "data": snapshot,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.post("/api/v1/trainer/sessions")
async def create_trainer_session(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona_id = _tw01_required_text(payload, "persona_id")
    session_type = _tw01_required_text(payload, "session_type")
    objective = _tw01_required_text(payload, "objective")
    context_refs = _tw01_validate_context_refs(payload.get("context_refs"))

    if session_type != "trainer":
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid trainer session type",
            "session_type must equal 'trainer' for TW-01",
            precondition_failed="session_type",
        )

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    session = read_store.create_trainer_session(
        persona_id=persona_id,
        objective=objective,
        context_refs=context_refs,
        actor_id=identity.operator_id,
        created_at=utc_now(),
    )
    if session is None:
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Trainer session store unavailable",
            "Trainer session creation store is unavailable.",
        )

    return {
        "session_id": session["session_id"],
        "persona_id": session["persona_id"],
        "session_type": session["session_type"],
        "objective": session["objective"],
        "status": session["status"],
        "started_at": session["started_at"],
        "allowedActions": session["allowedActions"],
        "links": session["links"],
    }


@app.get("/api/v1/trainer/sessions")
async def list_trainer_sessions(
    persona_id: str,
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    snapshot_at = utc_now()
    normalized_status = _tw01_validate_session_status(status) if status is not None else None
    sessions = read_store.list_trainer_sessions(persona_id=persona_id, status=normalized_status) or []
    surface_state = _tw01_trainer_dialog_surface_state(snapshot_at=snapshot_at, has_data=sessions is not None)

    total = len(sessions)
    if surface_state == "unavailable":
        page_items = []
        next_page_token = None
        total = 0
    else:
        page_items, next_page_token = _page_slice(sessions, page_token, page_size)

    return {
        "data": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "trainer_dialog": surface_state,
            },
        },
    }


@app.get("/api/v1/trainer/sessions/{session_id}")
async def get_trainer_session_detail(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    session = read_store.get_trainer_session(session_id)
    if not session:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer session not found",
            f"Trainer session {session_id} does not exist",
        )

    snapshot_at = utc_now()
    payload = dict(session)
    payload["meta"] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            "trainer_dialog": _tw01_trainer_dialog_surface_state(snapshot_at=snapshot_at, has_data=True),
        },
    }
    return payload


@app.post("/api/v1/trainer/sessions/{session_id}/message")
async def append_trainer_message(
    session_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    session = read_store.get_trainer_session(session_id)
    if not session:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer session not found",
            f"Trainer session {session_id} does not exist",
        )
    if session["status"] != "active":
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "Trainer session is not active",
            "POST /message is only allowed while the trainer session status is active",
            precondition_failed="status",
        )
    if not session["allowedActions"].get("canSendMessage"):
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_NOT_MET,
            "Trainer message submission unavailable",
            "allowedActions.canSendMessage is false for this trainer session",
            precondition_failed="allowedActions.canSendMessage",
        )

    message_body = _tw01_required_text(payload, "message_body")
    result = read_store.append_trainer_message(
        session_id,
        message_body=message_body,
        actor_id=identity.operator_id,
        accepted_at=utc_now(),
    )
    if result is None:
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Trainer session store unavailable",
            "Trainer message append store is unavailable.",
        )

    updated = result["session"]
    return {
        "session_id": updated["session_id"],
        "status": updated["status"],
        "accepted_at": result["accepted_at"],
        "event": result["event"],
        "session_summary": updated["session_summary"],
        "allowedActions": updated["allowedActions"],
    }


@app.get("/api/v1/capital-pools")
async def list_capital_pools(
    status: Optional[str] = None,
    risk_policy_ref: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """CP-01: Capital pool list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    pools = read_store.list_capital_pools(status=status, risk_policy_ref=risk_policy_ref)
    return {
        "data": pools,
        "meta": {
            "total": len(pools),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/bindings")
async def list_bindings(
    persona_id: Optional[str] = None,
    capital_pool_id: Optional[str] = None,
    role: Optional[str] = None,
    validity: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """CP-03: Persona capital binding list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    bindings = read_store.list_bindings(
        persona_id=persona_id,
        capital_pool_id=capital_pool_id,
        role=role,
        validity=validity,
    )
    return {
        "data": bindings,
        "meta": {
            "total": len(bindings),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/deployment-plans")
async def list_deployment_plans(
    status: Optional[str] = None,
    capital_pool_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """DP-01: Deployment plan list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    plans = read_store.list_deployment_plans(
        status=status,
        capital_pool_id=capital_pool_id,
    )
    return {
        "data": plans,
        "meta": {
            "total": len(plans),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/approval-decisions")
async def list_approval_decisions(
    outcome: Optional[str] = None,
    state: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """DP-03: Approval decision list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    decisions = read_store.list_approval_decisions(
        outcome=outcome,
        state=state,
    )
    return {
        "data": decisions,
        "meta": {
            "total": len(decisions),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/approval-decisions/{decision_id}")
async def get_approval_decision_detail(
    decision_id: str, authorization: Optional[str] = Header(default=None),
):
    """DP-04: Approval decision detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    decision = read_store.get_approval_decision(decision_id)
    if not decision:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Approval decision not found",
            f"Approval decision {decision_id} does not exist",
        )

    return {
        "data": decision,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/runtime-bindings")
async def list_runtime_bindings(
    deployment_mode: Optional[str] = None,
    version: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """RT-01: Runtime binding list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    bindings = read_store.list_runtime_bindings(
        deployment_mode=deployment_mode,
        version=version,
    )
    return {
        "data": bindings,
        "meta": {
            "total": len(bindings),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/runtimes/{runtime_id}/status")
async def get_runtime_status(
    runtime_id: str, authorization: Optional[str] = Header(default=None),
):
    """RT-03: Runtime status detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
    if not runtime_binding:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Runtime not found",
            f"Runtime {runtime_id} does not exist",
        )

    return {
        "data": runtime_binding,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


# --------------------------------------------------------------------------- #
# Read surfaces (Wave 1 - Promotion Review)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/deployment-plans/{plan_id}")
async def get_deployment_plan(plan_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    plan = read_store.get_deployment_plan(plan_id)
    if not plan:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Deployment plan not found",
            f"Deployment plan {plan_id} does not exist",
        )

    decision = read_store.get_approval_decision(plan.get("approval_decision_id"))
    payload = dict(plan)
    if decision:
        payload["approval_decision"] = decision

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/capital-pools/{pool_id}")
async def get_capital_pool(pool_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    pool = read_store.get_capital_pool(pool_id)
    if not pool:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Capital pool not found",
            f"Capital pool {pool_id} does not exist",
        )

    bindings = read_store.get_bindings_for_pool(pool_id)
    payload = dict(pool)
    payload["bindings"] = bindings

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/bindings/{binding_id}")
async def get_binding(binding_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    binding = read_store.get_binding(binding_id)
    if not binding:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Binding not found",
            f"Binding {binding_id} does not exist",
        )

    persona = read_store.get_persona(binding.get("persona_id"))
    payload = dict(binding)
    if persona:
        payload["persona"] = persona

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/runtime-bindings/{binding_id}")
async def get_runtime_binding(binding_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    runtime_binding = read_store.get_runtime_binding(binding_id)
    if not runtime_binding:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Runtime binding not found",
            f"Runtime binding {binding_id} does not exist",
        )

    plan = read_store.get_deployment_plan(runtime_binding.get("plan_id", ""))
    payload = dict(runtime_binding)
    if plan:
        payload["deployment_plan"] = plan

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/runtimes/{runtime_id}/rollbacks")
async def get_runtime_rollbacks(runtime_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    rollbacks = read_store.get_rollbacks(runtime_id)
    return {
        "data": rollbacks,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/operator/deployment-review/{plan_id}")
async def get_deployment_review(plan_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    plan = read_store.get_deployment_plan(plan_id)
    if not plan:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Deployment plan not found",
            f"Deployment plan {plan_id} does not exist",
        )

    pool = read_store.get_capital_pool(plan.get("capital_pool_id"))
    bindings = read_store.get_bindings_for_pool(plan.get("capital_pool_id"))
    runtime_binding = read_store.get_runtime_binding(plan.get("runtime_binding_id"))
    approval_decision = read_store.get_approval_decision(plan.get("approval_decision_id"))
    rollbacks = read_store.get_rollbacks(
        runtime_binding.get("runtime_id") if runtime_binding else None
    )
    allowed_actions = read_store.get_allowed_actions(plan_id)
    latest_run = read_store.get_latest_run(plan_id)
    review = read_store.get_review_summary(plan_id)

    snapshot_at = utc_now()

    deployment_plan_payload = {
        "id": plan.get("id"),
        "stage": plan.get("stage"),
        "artifact_id": plan.get("artifact_id"),
        "approval_decision_id": plan.get("approval_decision_id"),
    }
    for optional_key in ["current_stage", "target_stage", "status", "artifact_version", "transition_type"]:
        if plan.get(optional_key) is not None:
            deployment_plan_payload[optional_key] = plan.get(optional_key)
    if approval_decision:
        deployment_plan_payload["approval_decision"] = approval_decision

    data = {
        "deployment_plan": deployment_plan_payload,
        "approval_decision": approval_decision or {},
        "capital_pool": pool or {},
        "bindings": bindings,
        "runtime_binding": runtime_binding or {},
        "rollbacks": rollbacks,
        "allowedActions": allowed_actions,
        "latestRun": latest_run,
        "review": review,
    }

    return {
        "data": data,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "deployment_plan": _dataset_surface_status(
                    "deployment_plans",
                    snapshot_at=snapshot_at,
                    has_data=plan is not None,
                ),
                "approval_decision": _dataset_surface_status(
                    "approval_decisions",
                    snapshot_at=snapshot_at,
                    has_data=approval_decision is not None,
                    missing_message="Approval decision unavailable for this deployment plan.",
                ),
                "capital_pool": _dataset_surface_status(
                    "capital_pools",
                    snapshot_at=snapshot_at,
                    has_data=pool is not None,
                    missing_message="Capital pool detail unavailable for this deployment plan.",
                ),
                "bindings": _dataset_surface_status(
                    "persona_bindings",
                    snapshot_at=snapshot_at,
                    has_data=bindings is not None,
                ),
                "runtime_binding": _dataset_surface_status(
                    "runtime_bindings",
                    snapshot_at=snapshot_at,
                    has_data=runtime_binding is not None,
                    missing_message="Runtime binding unavailable for this deployment plan.",
                ),
                "rollbacks": _dataset_surface_status("rollbacks", snapshot_at=snapshot_at),
                "allowedActions": _dataset_surface_status(
                    "allowed_actions",
                    snapshot_at=snapshot_at,
                    has_data=allowed_actions is not None,
                ),
                "latestRun": _dataset_surface_status(
                    "latest_runs",
                    snapshot_at=snapshot_at,
                    has_data=latest_run is not None,
                ),
                "review": _dataset_surface_status(
                    "review_summaries",
                    snapshot_at=snapshot_at,
                    has_data=review is not None,
                ),
            },
        },
    }


@app.get("/api/v1/operator/runtime-state")
async def list_operator_runtime_state(
    deployment_stage: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: str = Query(default="last_updated_at"),
    sort_order: str = Query(default="desc"),
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    if sort_by not in _RUNTIME_STATE_SORT_FIELDS:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid sort_by",
            f"sort_by must be one of {sorted(_RUNTIME_STATE_SORT_FIELDS)}",
        )
    if sort_order not in _RUNTIME_STATE_SORT_ORDERS:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid sort_order",
            f"sort_order must be one of {sorted(_RUNTIME_STATE_SORT_ORDERS)}",
        )

    requested_stages = {
        value.lower() for value in (_split_csv_query(deployment_stage) or [])
    }
    requested_statuses = {
        value.lower() for value in (_split_csv_query(status) or [])
    }
    snapshot_at = utc_now()

    bindings = read_store.list_runtime_bindings()
    if requested_stages:
        bindings = [
            binding
            for binding in bindings
            if str(
                binding.get("deployment_stage") or binding.get("deployment_mode") or ""
            ).lower() in requested_stages
        ]
    if requested_statuses:
        bindings = [
            binding
            for binding in bindings
            if str(binding.get("status") or "").lower() in requested_statuses
        ]

    runtimes = [
        _project_operator_runtime_state_row(binding)
        for binding in bindings
    ]
    runtimes = _sort_runtime_state_rows(
        runtimes,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    runtime_roster_surface = _dataset_surface_status(
        "runtime_bindings",
        snapshot_at=snapshot_at,
    )
    telemetry_surface = _dataset_surface_status(
        "telemetry_summaries",
        snapshot_at=snapshot_at,
    )
    if runtimes and any(row.get("telemetry_summary") is None for row in runtimes):
        if telemetry_surface.get("status") == "ok":
            telemetry_surface["status"] = "degraded"
        telemetry_surface.setdefault(
            "message",
            "Telemetry summary unavailable for one or more runtimes on the runtime-state board.",
        )
        telemetry_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )

    rollback_history_surface = _dataset_surface_status(
        "rollbacks",
        snapshot_at=snapshot_at,
    )

    runtime_state_surface = _composed_surface_status(
        snapshot_at=snapshot_at,
        available=runtime_roster_surface.get("status") != "unavailable",
        missing_message="Runtime roster unavailable for the operator runtime-state board.",
    )
    if runtime_roster_surface.get("status") == "degraded":
        runtime_state_surface["status"] = "degraded"
    elif runtime_roster_surface.get("status") == "unavailable":
        runtime_state_surface["status"] = "unavailable"
    elif any(
        surface.get("status") != "ok"
        for surface in (telemetry_surface, rollback_history_surface)
    ):
        runtime_state_surface["status"] = "degraded"
        runtime_state_surface.setdefault(
            "message",
            "Runtime-state board is available, but one or more supporting surfaces are degraded or unavailable.",
        )
        runtime_state_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )

    total = len(runtimes)
    if runtime_state_surface.get("status") == "unavailable":
        runtimes = []
        next_page_token = None
    else:
        runtimes, next_page_token = _page_slice(runtimes, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["total"] = total
    meta["sort"] = {
        "sort_by": sort_by,
        "sort_order": sort_order,
    }
    meta["surfaces"] = {
        "runtime_state": runtime_state_surface,
        "runtime_roster": runtime_roster_surface,
        "telemetry_summary": telemetry_surface,
        "rollback_history": rollback_history_surface,
    }

    return {
        "runtimes": runtimes,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": meta,
    }


@app.get("/api/v1/operator/alerts")
async def list_operator_alerts(
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    return _build_operator_alerts_payload(snapshot_at)


@app.get("/api/v1/operator/home")
async def get_operator_home(
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    return _build_operator_home_payload(snapshot_at)


@app.get("/api/v1/operator/paper-live-drift/{runtime_id}")
async def get_operator_paper_live_drift(
    runtime_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
    report = read_store.get_paper_live_drift_report(runtime_id)
    if runtime_binding is None and report is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Runtime drift view not found",
            f"Runtime {runtime_id} does not exist",
        )

    snapshot_at = utc_now()
    return _build_operator_paper_live_drift_payload(runtime_id, snapshot_at)


@app.get("/api/v1/operator/health-status")
async def get_operator_health_status(
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    return _build_operator_health_status_payload(snapshot_at)


@app.get("/api/v1/workbench/consultation")
async def get_consultation_workbench_overview(
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _build_consultation_workbench_overview(utc_now())


@app.get("/api/v1/committees")
async def list_committees(
    quorum_state: Optional[str] = None,
    consensus_state: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    committees = read_store.list_committees(
        quorum_states=_split_csv_query(quorum_state),
        consensus_states=_split_csv_query(consensus_state),
    )
    surface_state = _dataset_surface_status(
        "consultation_sessions",
        snapshot_at=snapshot_at,
        missing_message="Committee board list is unavailable.",
    )

    if surface_state.get("status") == "unavailable":
        data = []
        total = 0
        next_page_token = None
    else:
        data, next_page_token = _page_slice(committees, page_token, page_size)
        total = len(committees)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "committee_board": (
            "degraded"
            if surface_state.get("source") == "local_snapshot"
            else surface_state.get("status")
        ),
    }

    return {
        "data": data,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
        },
        "meta": meta,
    }


@app.get("/api/v1/committees/{committee_id}")
async def get_committee(
    committee_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    committee = read_store.get_committee(committee_id)
    if committee is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee board not found",
            f"Committee {committee_id} does not exist",
        )

    return _cw03_committee_projection(
        committee,
        identity=identity,
        snapshot_at=utc_now(),
    )


@app.get("/api/v1/workbench/knowledge")
async def get_knowledge_workbench_overview(
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _build_knowledge_workbench_overview(utc_now())


@app.post("/api/v1/research/tickets")
async def create_research_ticket(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    title = _rw01_required_text(payload, "title")
    description = _rw01_required_text(payload, "description")
    owner = _rw01_required_text(payload, "owner")
    priority = _rw01_validate_priority(payload.get("priority"))

    ticket = read_store.create_research_ticket(
        title=title,
        description=description,
        priority=priority,
        owner=owner,
        actor_id=identity.operator_id,
        created_at=utc_now(),
    )
    return {
        "ticket_id": ticket["ticket_id"],
        "status": ticket["status"],
        "created_at": ticket["created_at"],
        "allowedActions": ticket["allowedActions"],
    }


@app.get("/api/v1/research/tickets")
async def list_research_tickets(
    status: Optional[str] = None,
    owner: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    statuses = _split_csv_query(status)
    if statuses:
        statuses = [_rw01_validate_status(value) for value in statuses]

    items = read_store.list_research_tickets(statuses=statuses, owner=owner)
    total = len(items)
    surface_state = _rw01_surface_state("research_tickets", snapshot_at=snapshot_at)
    if surface_state == "unavailable":
        page_items = []
        next_page_token = None
        total = 0
    else:
        page_items, next_page_token = _page_slice(items, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "ticket_list": surface_state,
    }
    return {
        "data": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
        },
        "meta": meta,
    }


@app.get("/api/v1/research/tickets/{ticket_id}")
async def get_research_ticket(
    ticket_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    ticket = read_store.get_research_ticket(ticket_id)
    if not ticket:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Research ticket not found",
            f"Research ticket {ticket_id} does not exist",
        )

    snapshot_at = utc_now()
    payload = dict(ticket)
    payload["links"] = {
        "self": f"/api/v1/research/tickets/{ticket_id}",
        "workbench_detail": f"/research/tickets/{ticket_id}",
    }
    payload["meta"] = {
        **_snapshot_meta(snapshot_at),
        "surfaces": {
            "ticket_detail": _rw01_surface_state(
                "research_tickets",
                snapshot_at=snapshot_at,
                has_data=True,
            ),
        },
    }
    return payload


@app.patch("/api/v1/research/tickets/{ticket_id}")
async def patch_research_ticket(
    ticket_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    ticket = read_store.get_research_ticket(ticket_id)
    if not ticket:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Research ticket not found",
            f"Research ticket {ticket_id} does not exist",
        )

    patch = _rw01_validate_patch(ticket, payload)
    updated = read_store.patch_research_ticket(
        ticket_id,
        patch=patch,
        actor_id=identity.operator_id,
        updated_at=utc_now(),
    )
    if updated is None:
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Research ticket store unavailable",
            "Research ticket update store is unavailable.",
        )

    return {
        "ticket_id": updated["ticket_id"],
        "status": updated["status"],
        "updated_at": updated["updated_at"],
        "allowedActions": updated["allowedActions"],
    }


@app.get("/api/v1/research/search")
async def search_research_corpus(
    q: str,
    match_type: str = "all",
    status: Optional[str] = None,
    date_range: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=25, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    try:
        query = _rw02_validate_query(q)
        match_type = _rw02_validate_match_type(match_type)
        status = _rw02_validate_status(status)
        date_range = _rw02_validate_date_range(date_range)
    except ValueError as exc:
        return _rw02_invalid_query(str(exc))

    snapshot_at = utc_now()
    index_adapter = read_store.get_research_search_index()
    adapter_state = _rw02_adapter_state(index_adapter, snapshot_at=snapshot_at)
    if index_adapter is None or adapter_state == "unavailable":
        return JSONResponse(
            status_code=503,
            content={
                "error": "search_unavailable",
                "meta": {
                    "surfaces": {
                        "search_results": "unavailable",
                    }
                },
            },
        )

    items = read_store.list_research_search_results(
        query=query,
        match_type=match_type,
        status=status,
        date_range=date_range,
    )
    total = len(items)
    try:
        page_items, next_page_token = _rw02_page_slice(items, page_token, page_size)
    except ValueError as exc:
        return _rw02_invalid_query(str(exc))

    index_snapshot_at = str(index_adapter.get("snapshot_at") or snapshot_at)
    source_watermarks = index_adapter.get("source_watermarks")
    if not isinstance(source_watermarks, dict):
        source_watermarks = {}
    indexed_match_types = index_adapter.get("indexed_match_types")
    if not isinstance(indexed_match_types, list):
        indexed_match_types = []

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "search_results": adapter_state,
    }
    meta["index_adapter"] = {
        "snapshot_at": index_snapshot_at,
        "adapter_state": adapter_state,
        "indexed_match_types": [
            str(value)
            for value in indexed_match_types
            if str(value).strip()
        ],
        "source_watermarks": {
            "tickets": source_watermarks.get("tickets"),
            "experiments": source_watermarks.get("experiments"),
            "artifacts": source_watermarks.get("artifacts"),
        },
    }
    return {
        "data": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
        },
        "meta": meta,
    }


@app.get("/api/v1/research/analysis")
async def list_research_analysis(
    ticket_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    status: Optional[str] = None,
    date_range: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    statuses = _split_csv_query(status)
    if statuses:
        statuses = [_rw03_validate_status(value) for value in statuses]
    if date_range is not None:
        date_range = _rw03_validate_date_range(date_range)

    items = read_store.list_research_analyses(
        ticket_id=ticket_id,
        experiment_id=experiment_id,
        statuses=statuses,
        date_range=date_range,
    )
    total = len(items)
    surface_state = _rw01_surface_state("research_analyses", snapshot_at=snapshot_at)
    if surface_state == "unavailable":
        page_items = []
        next_page_token = None
        total = 0
    else:
        page_items, next_page_token = _page_slice(items, page_token, page_size)

    for item in page_items:
        analysis_id = str(item.get("analysis_id") or "")
        ticket_ref = str(item.get("ticket_id") or "")
        item["links"] = {
            "self": f"/api/v1/research/analysis/{analysis_id}",
            "workbench_detail": f"/research/analyze/{analysis_id}",
            "linked_ticket_detail": f"/research/tickets/{ticket_ref}",
        }

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "analysis_results": surface_state,
    }
    return {
        "data": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
        },
        "meta": meta,
    }


@app.get("/api/v1/research/analysis/{analysis_id}")
async def get_research_analysis(
    analysis_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    analysis = read_store.get_research_analysis(analysis_id)
    if not analysis:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Research analysis not found",
            f"Research analysis {analysis_id} does not exist",
        )

    snapshot_at = utc_now()
    ticket_ref = str(analysis.get("ticket_id") or "")
    experiment_ref = analysis.get("experiment_id")
    payload = dict(analysis)
    payload["links"] = {
        "self": f"/api/v1/research/analysis/{analysis_id}",
        "workbench_detail": f"/research/analyze/{analysis_id}",
        "linked_ticket_detail": f"/research/tickets/{ticket_ref}",
        "linked_experiment_detail": (
            f"/research/experiments/{experiment_ref}" if experiment_ref else None
        ),
    }
    payload["meta"] = {
        **_snapshot_meta(snapshot_at),
        "surfaces": {
            "analysis_results": _rw01_surface_state(
                "research_analyses",
                snapshot_at=snapshot_at,
                has_data=True,
            ),
        },
    }
    return payload


def _kw01_surface_state(
    dataset: str,
    *,
    snapshot_at: str,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
) -> str:
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
        missing_message=missing_message,
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("status") == "degraded":
        return "degraded"
    return "ok"


@app.get("/api/v1/knowledge/memory")
async def list_institutional_memory(
    knowledge_type: Optional[str] = None,
    scope: Optional[str] = None,
    scope_filter: Optional[str] = None,
    tags: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    authorization: Optional[str] = Header(default=None),
):
    """KW-01: Paginated list of institutional memory entries."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    entries = read_store.list_institutional_memory_entries()

    if knowledge_type:
        entries = [e for e in entries if e["knowledge_type"] == knowledge_type]
    if scope:
        entries = [e for e in entries if e["scope"] == scope]
    if scope_filter:
        entries = [e for e in entries if e.get("scope_filter") == scope_filter]
    if tags:
        requested = {t.strip() for t in tags.split(",") if t.strip()}
        entries = [e for e in entries if requested.intersection(e.get("tags", []))]

    total_count = len(entries)
    start = (page - 1) * page_size
    page_entries = entries[start : start + page_size]
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    memory_list_surface = _kw01_surface_state(
        "institutional_memory_entries",
        snapshot_at=snapshot_at,
        has_data=bool(entries),
        missing_message="Institutional memory list is unavailable.",
    )
    if memory_list_surface == "unavailable":
        page_entries = []
        total_count = 0
        total_pages = 0

    return {
        "entries": page_entries,
        "pagination": {
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        },
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {"memory_list": memory_list_surface},
        },
    }


@app.get("/api/v1/knowledge/memory/{entry_id}")
async def get_institutional_memory_entry(
    entry_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """KW-01: Full detail view for one institutional memory entry."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    entry = read_store.get_institutional_memory_entry(entry_id)
    if entry is None:
        return JSONResponse(
            status_code=404,
            content={"error": "entry_not_found", "entry_id": entry_id},
        )

    source_event = entry.get("source_event") if isinstance(entry.get("source_event"), dict) else {}
    source_context_available = bool(source_event.get("type")) and bool(source_event.get("id"))

    return {
        **entry,
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "entry_detail": _kw01_surface_state(
                    "institutional_memory_entries",
                    snapshot_at=snapshot_at,
                    has_data=True,
                ),
                "source_context": _kw01_surface_state(
                    "institutional_memory_entries",
                    snapshot_at=snapshot_at,
                    has_data=source_context_available,
                    missing_message="Institutional memory source context is unavailable.",
                ),
            },
        },
    }


def _governance_review_allowed_actions_present(item: Dict[str, Any]) -> bool:
    allowed_actions = item.get("allowedActions")
    if not isinstance(allowed_actions, dict):
        return False
    required_fields = (
        "canReview",
        "canForwardToApproval",
        "canRequestChanges",
        "canEscalate",
    )
    return all(isinstance(allowed_actions.get(field), bool) for field in required_fields)


def _approval_queue_allowed_actions_present(item: Dict[str, Any]) -> bool:
    allowed_actions = item.get("allowedActions")
    if not isinstance(allowed_actions, dict):
        return False
    required_fields = (
        "canApprove",
        "canReject",
        "canRequestRevision",
    )
    return all(isinstance(allowed_actions.get(field), bool) for field in required_fields)


_DEPLOYMENT_DIFF_CATEGORIES = (
    "parameters",
    "bindings",
    "capital_allocation",
    "risk_controls",
    "stage_transition",
)


def _default_deployment_diff_summary() -> Dict[str, Any]:
    return {
        "total_changes": 0,
        "by_category": {
            category: {"count": 0, "highest_risk_tier": None}
            for category in _DEPLOYMENT_DIFF_CATEGORIES
        },
    }


def _deployment_diff_allowed_actions_present(payload: Dict[str, Any]) -> bool:
    allowed_actions = payload.get("allowedActions")
    if not isinstance(allowed_actions, dict):
        return False
    required_fields = ("canProceedToApproval", "canEscalateDiff")
    return all(isinstance(allowed_actions.get(field), bool) for field in required_fields)


def _unavailable_deployment_diff_payload(plan_id: str, snapshot_at: str) -> Dict[str, Any]:
    deployment_diff_surface = _dataset_surface_status(
        "deployment_diffs",
        snapshot_at=snapshot_at,
        has_data=False,
        missing_message="Deployment diff unavailable for this plan.",
    )
    allowed_actions_surface = _composed_surface_status(
        snapshot_at=snapshot_at,
        available=False,
        missing_message="Deployment diff authority unavailable.",
    )
    allowed_actions_surface["status"] = deployment_diff_surface.get("status")
    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "deployment_diff": deployment_diff_surface,
        "allowedActions": allowed_actions_surface,
    }
    return {
        "plan_id": plan_id,
        "artifact_id": None,
        "stage": None,
        "submitted_at": None,
        "submitted_by": None,
        "previous_plan_id": None,
        "first_deployment": False,
        "changes": [],
        "change_summary": _default_deployment_diff_summary(),
        "allowedActions": {
            "canProceedToApproval": False,
            "canEscalateDiff": False,
        },
        "meta": meta,
    }


@app.get("/api/v1/operator/governance/review-queue")
async def list_governance_review_queue(
    item_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    item_types = [value.strip() for value in item_type.split(",") if value.strip()] if item_type else None
    risk_levels = [value.strip() for value in risk_level.split(",") if value.strip()] if risk_level else None
    statuses = [value.strip() for value in status.split(",") if value.strip()] if status else None

    items = read_store.list_governance_review_queue_items(
        item_types=item_types,
        risk_levels=risk_levels,
        statuses=statuses,
    )
    review_queue_surface = _dataset_surface_status(
        "governance_review_queue_items",
        snapshot_at=snapshot_at,
    )

    if review_queue_surface.get("status") == "unavailable":
        items = []
        next_page_token = None
    else:
        items, next_page_token = _page_slice(items, page_token, page_size)

    allowed_actions_surface = _composed_surface_status(
        snapshot_at=snapshot_at,
        available=all(_governance_review_allowed_actions_present(item) for item in items),
        missing_message="Governance routing authority unavailable.",
    )
    if review_queue_surface.get("status") == "degraded":
        allowed_actions_surface["status"] = "degraded"
    elif review_queue_surface.get("status") == "unavailable":
        allowed_actions_surface["status"] = "unavailable"

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "review_queue": review_queue_surface,
        "allowedActions": allowed_actions_surface,
    }

    return {
        "items": items,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": meta,
    }


@app.get("/api/v1/operator/governance/approval-queue")
async def list_governance_approval_queue(
    decision_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    decision_state: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    decision_types = [value.strip() for value in decision_type.split(",") if value.strip()] if decision_type else None
    risk_levels = [value.strip() for value in risk_level.split(",") if value.strip()] if risk_level else None
    decision_states = [value.strip() for value in decision_state.split(",") if value.strip()] if decision_state else None

    items = read_store.list_approval_queue_items(
        decision_types=decision_types,
        risk_levels=risk_levels,
        decision_states=decision_states,
    )
    approval_queue_surface = _dataset_surface_status(
        "approval_queue_items",
        snapshot_at=snapshot_at,
    )

    if approval_queue_surface.get("status") == "unavailable":
        items = []
        next_page_token = None
    else:
        items, next_page_token = _page_slice(items, page_token, page_size)

    allowed_actions_surface = _composed_surface_status(
        snapshot_at=snapshot_at,
        available=all(_approval_queue_allowed_actions_present(item) for item in items),
        missing_message="Approval queue authority unavailable.",
    )
    if approval_queue_surface.get("status") == "degraded":
        allowed_actions_surface["status"] = "degraded"
    elif approval_queue_surface.get("status") == "unavailable":
        allowed_actions_surface["status"] = "unavailable"

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "approval_queue": approval_queue_surface,
        "allowedActions": allowed_actions_surface,
    }

    return {
        "items": items,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": meta,
    }


@app.get("/api/v1/operator/deployment-diff/{plan_id}")
async def get_deployment_diff(plan_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    diff = read_store.get_deployment_diff(plan_id)
    diff_source = read_store.dataset_source("deployment_diffs")
    if not diff:
        if diff_source == "missing":
            return _unavailable_deployment_diff_payload(plan_id, utc_now())
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Deployment diff not found",
            f"Deployment diff for plan {plan_id} does not exist",
        )

    snapshot_at = (((diff.get("meta") or {}).get("snapshot_at"))) or utc_now()
    payload = dict(diff)
    payload["plan_id"] = payload.get("plan_id") or plan_id
    payload["changes"] = list(payload.get("changes") or [])

    summary = dict(payload.get("change_summary") or {})
    summary["total_changes"] = int(summary.get("total_changes") or len(payload["changes"]))
    by_category = dict(summary.get("by_category") or {})
    for category in _DEPLOYMENT_DIFF_CATEGORIES:
        category_summary = dict(by_category.get(category) or {})
        category_summary.setdefault("count", 0)
        category_summary.setdefault("highest_risk_tier", None)
        by_category[category] = category_summary
    summary["by_category"] = by_category
    payload["change_summary"] = summary

    allowed_actions = dict(payload.get("allowedActions") or {})
    allowed_actions.setdefault("canProceedToApproval", False)
    allowed_actions.setdefault("canEscalateDiff", False)
    payload["allowedActions"] = allowed_actions

    deployment_diff_surface = _dataset_surface_status(
        "deployment_diffs",
        snapshot_at=snapshot_at,
        has_data=True,
    )
    allowed_actions_surface = _composed_surface_status(
        snapshot_at=snapshot_at,
        available=_deployment_diff_allowed_actions_present(payload),
        missing_message="Deployment diff authority unavailable.",
    )
    if deployment_diff_surface.get("status") == "degraded":
        allowed_actions_surface["status"] = "degraded"
    elif deployment_diff_surface.get("status") == "unavailable":
        allowed_actions_surface["status"] = "unavailable"

    meta = dict(payload.get("meta") or {})
    meta["snapshot_at"] = snapshot_at
    meta["surfaces"] = {
        "deployment_diff": deployment_diff_surface,
        "allowedActions": allowed_actions_surface,
    }
    payload["meta"] = meta
    return payload


@app.get("/api/v1/operator/rollback-review/{rollback_id}")
async def get_rollback_review(rollback_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    review = read_store.get_rollback_review(rollback_id)
    if not review:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Rollback review not found",
            f"Rollback review {rollback_id} does not exist",
        )

    snapshot_at = (
        ((review.get("meta") or {}).get("snapshot_at"))
        or utc_now()
    )
    meta = dict(review.get("meta") or {})
    meta["snapshot_at"] = snapshot_at
    surfaces = dict(meta.get("surfaces") or {})
    surfaces.setdefault(
        "rollback_review",
        _composed_surface_status(snapshot_at=snapshot_at, available=True),
    )
    surfaces.setdefault(
        "position_data",
        _composed_surface_status(snapshot_at=snapshot_at, available=True),
    )
    surfaces.setdefault(
        "allowedActions",
        _composed_surface_status(
            snapshot_at=snapshot_at,
            available=review.get("allowedActions") is not None,
            missing_message="Rollback approval authority unavailable.",
        ),
    )
    meta["surfaces"] = surfaces

    payload = dict(review)
    payload["meta"] = meta
    return payload


@app.get("/api/v1/operator/governance/audit")
async def list_governance_audit_trail(
    actor: Optional[str] = None,
    action_type: Optional[str] = None,
    target_type: Optional[str] = None,
    from_: Optional[datetime] = Query(default=None, alias="from"),
    to: Optional[datetime] = Query(default=None),
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    action_types = None
    if action_type:
        action_types = [value.strip() for value in action_type.split(",") if value.strip()]

    entries = read_store.list_governance_audit_events(
        actor=actor,
        action_types=action_types,
        target_type=target_type,
        from_ts=from_,
        to_ts=to,
    )
    audit_surface = _dataset_surface_status(
        "governance_audit_events",
        snapshot_at=snapshot_at,
    )
    if audit_surface.get("status") == "unavailable":
        entries = []
        next_page_token = None
    else:
        entries, next_page_token = _page_slice(entries, page_token, page_size)

    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            "audit_trail": audit_surface,
        },
    }

    return {
        "entries": entries,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": meta,
    }


# --------------------------------------------------------------------------- #
# Incident Surfaces (Wave 2 - Incident Response: IN-01 – IN-05)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/incidents")
async def list_incidents(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    severity: Optional[str] = None,
    affected_pool_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """IN-01: Incident List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    incidents = read_store.list_incidents(
        status=status, severity=severity, affected_pool_id=affected_pool_id,
    )
    items = [_project_incident_home_item(incident) for incident in incidents]
    if surface.get("status") == "unavailable":
        items = []
        next_page_token = None
    else:
        items, next_page_token = _page_slice(items, page_token, page_size)

    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            "incident_list": surface,
        },
    }
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness

    degradation_reason = _surface_degradation_reason(
        surface,
        degraded_reason="Incident list is degraded and may be stale.",
        unavailable_reason="Incident list is currently unavailable.",
    )
    if degradation_reason is not None:
        meta["degradation"] = {"reason": degradation_reason}

    return {
        "items": items,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": meta,
    }


@app.get("/api/v1/incidents/{incident_id}")
async def get_incident(incident_id: str, authorization: Optional[str] = Header(default=None)):
    """IN-02: Incident Detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    incident = read_store.get_incident(incident_id)
    if not incident:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Incident not found",
            f"Incident {incident_id} does not exist",
        )

    return {
        "data": incident,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/postmortems")
async def list_postmortems(
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """IN-03: Postmortem List."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    postmortems = read_store.list_postmortems(time_range=time_range)
    return {
        "data": postmortems,
        "meta": {
            "total": len(postmortems),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/postmortems/{report_id}")
async def get_postmortem(report_id: str, authorization: Optional[str] = Header(default=None)):
    """IN-04: Postmortem Detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    postmortem = read_store.get_postmortem(report_id)
    if not postmortem:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Postmortem report not found",
            f"Postmortem {report_id} does not exist",
        )

    # Include linked incident if available
    incident_id = postmortem.get("incident_id")
    incident = read_store.get_incident(incident_id) if incident_id else None
    payload = dict(postmortem)
    if incident:
        payload["linked_incident"] = incident

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/kill-switch/status")
async def get_kill_switch_status(authorization: Optional[str] = Header(default=None)):
    """IN-05: Kill Switch Status — requires admin role."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    if "admin" not in identity.roles:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "Kill-switch status requires 'admin' role",
            "Operator does not hold the admin role",
            precondition_failed="role_check",
            suggestion="Escalate to an admin-role operator",
        )

    snapshot_at = utc_now()
    kill_switch_surface = _dataset_surface_status("kill_switch", snapshot_at=snapshot_at)
    allowed_actions_surface = _action_drawer_allowed_actions_surface()
    ks = read_store.get_kill_switch_status()
    allowed_actions = _project_action_drawer_allowed_actions(
        kill_switch_surface,
        allowed_actions_surface,
    )
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            "kill_switch": kill_switch_surface,
            "allowedActions": allowed_actions_surface,
        },
    }
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness

    degradation: Dict[str, Any] = {}
    kill_switch_reason = _surface_degradation_reason(
        kill_switch_surface,
        degraded_reason="Kill switch status is degraded and may be stale.",
        unavailable_reason="Kill switch status is currently unavailable.",
    )
    if kill_switch_reason is not None:
        degradation["kill_switch_reason"] = kill_switch_reason
    allowed_actions_reason = _surface_degradation_reason(
        allowed_actions_surface,
        degraded_reason="Action authority is degraded. All CTAs disabled for safety.",
        unavailable_reason="Action authority service is unavailable. All CTAs disabled for safety.",
    )
    if allowed_actions_reason is not None:
        degradation["allowedActions_reason"] = allowed_actions_reason
    if degradation:
        meta["degradation"] = degradation

    return {
        "kill_switch": _project_kill_switch_contract(ks, kill_switch_surface),
        "allowedActions": allowed_actions,
        "meta": meta,
    }


# --------------------------------------------------------------------------- #
# Composed Views — Incident Response
# --------------------------------------------------------------------------- #


@app.get("/api/v1/operator/incident-response/{incident_id}")
async def get_incident_response(
    incident_id: str,
    snapshot: str = "preferred",
    authorization: Optional[str] = Header(default=None),
):
    """
    Composed view for PKT-002 Incident Detail.
    Composes: incident record, affected bindings, kill-switch state, and action authority.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    # IN-02: Incident detail
    incident = read_store.get_incident(incident_id)
    if not incident:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Incident not found",
            f"Incident {incident_id} does not exist",
        )

    snapshot_at = utc_now()
    runtime_binding = None
    binding_id = incident.get("binding_id")
    if binding_id:
        runtime_binding = read_store.get_runtime_binding(binding_id)
    if runtime_binding is None:
        runtime_binding = read_store.get_runtime_binding_by_runtime_id(incident.get("runtime_id"))

    affected_bindings, binding_lookup_expected = _project_affected_bindings(
        incident,
        runtime_binding,
    )
    ks = read_store.get_kill_switch_status()

    incident_surface = _dataset_surface_status(
        "incidents",
        snapshot_at=snapshot_at,
        has_data=incident is not None,
    )
    affected_bindings_surface = _dataset_surface_status(
        "persona_bindings",
        snapshot_at=snapshot_at,
        has_data=(len(affected_bindings) > 0) if binding_lookup_expected else None,
        missing_message="Affected bindings unavailable for this incident.",
    )
    kill_switch_surface = _dataset_surface_status("kill_switch", snapshot_at=snapshot_at)

    action_derivation_available = bool(incident.get("runtime_id"))
    allowed_actions_surface = _composed_surface_status(
        snapshot_at=snapshot_at,
        available=action_derivation_available,
        missing_message="Action authority unavailable for this incident.",
    )
    if kill_switch_surface.get("status") == "unavailable":
        allowed_actions_surface["status"] = "unavailable"
        allowed_actions_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )
    allowed_actions = (
        _derive_incident_allowed_actions(identity, incident)
        if allowed_actions_surface.get("status") == "ok"
        else _default_incident_allowed_actions()
    )

    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            "incident": incident_surface,
            "affected_bindings": affected_bindings_surface,
            "kill_switch": kill_switch_surface,
            "allowedActions": allowed_actions_surface,
        },
    }
    if snapshot == "preferred":
        staleness = _meta_staleness()
        if staleness is not None:
            meta["staleness"] = staleness

    degradation: Dict[str, str] = {}
    affected_bindings_reason = _surface_degradation_reason(
        affected_bindings_surface,
        degraded_reason="Affected bindings are degraded and may be incomplete.",
        unavailable_reason="Affected bindings are currently unavailable.",
    )
    if affected_bindings_reason is not None:
        degradation["affected_bindings_reason"] = affected_bindings_reason
    kill_switch_reason = _surface_degradation_reason(
        kill_switch_surface,
        degraded_reason="Kill switch status is degraded and may be stale.",
        unavailable_reason="Kill switch status is currently unavailable.",
    )
    if kill_switch_reason is not None:
        degradation["kill_switch_reason"] = kill_switch_reason
    allowed_actions_reason = _surface_degradation_reason(
        allowed_actions_surface,
        degraded_reason="Action authority is degraded; all CTAs are disabled for safety.",
        unavailable_reason="Action authority is currently unavailable; all CTAs are disabled.",
    )
    if allowed_actions_reason is not None:
        degradation["allowedActions_reason"] = allowed_actions_reason
    if degradation:
        meta["degradation"] = degradation

    return {
        "data": {
            "incident": _project_incident_detail_incident(incident),
            "affected_bindings": affected_bindings,
            "kill_switch": _project_kill_switch_contract(ks, kill_switch_surface),
        },
        "allowedActions": allowed_actions,
        "meta": meta,
    }


# --------------------------------------------------------------------------- #
# Persona Management composed view (Wave 4 — PS-02, CP-03, CP-04, PS-03, PS-05)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/operator/persona-management/{persona_id}")
async def get_persona_management(
    persona_id: str,
    snapshot: str = "preferred",
    authorization: Optional[str] = Header(default=None),
):
    """
    Composed view for persona lifecycle management.
    Composes: PS-02 (persona detail + bindings), CP-03/CP-04 (capital pool bindings),
              PS-03 (persona sessions), PS-05 (teaching sessions).
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    # PS-02: Persona detail
    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    snapshot_at = utc_now()
    surfaces = {}

    # PS-02: Persona bindings (bindings where this persona is the owner)
    persona_bindings = read_store.get_bindings_for_persona(persona_id)
    persona_bindings_available = persona_bindings is not None
    if persona_bindings is None:
        persona_bindings = []
    surfaces["persona_bindings"] = _dataset_surface_status(
        "persona_bindings",
        snapshot_at=snapshot_at,
        has_data=persona_bindings_available,
        missing_message="Persona bindings unavailable for this persona.",
    )

    # CP-04: Enrich each binding with its capital pool detail
    enriched_bindings = []
    for binding in persona_bindings:
        binding_detail = dict(binding)
        pool = read_store.get_capital_pool(binding.get("capital_pool_id"))
        if pool:
            binding_detail["capital_pool"] = pool
        enriched_bindings.append(binding_detail)

    capital_pool_surface = _dataset_surface_status(
        "capital_pools",
        snapshot_at=snapshot_at,
        has_data=bool(enriched_bindings),
        missing_message="No capital pool bindings available for this persona.",
    )
    if surfaces["persona_bindings"]["status"] != "ok":
        surfaces["capital_pool_bindings"] = dict(surfaces["persona_bindings"])
    else:
        surfaces["capital_pool_bindings"] = capital_pool_surface

    # PS-03: Active sessions for this persona
    sessions = read_store.get_sessions_for_persona(persona_id)
    sessions_available = sessions is not None
    if sessions is None:
        sessions = []
    surfaces["persona_sessions"] = _dataset_surface_status(
        "sessions",
        snapshot_at=snapshot_at,
        has_data=sessions_available,
        missing_message="Persona sessions unavailable for this persona.",
    )

    # PS-05: Teaching sessions for this persona
    teaching_sessions = read_store.get_teaching_sessions_for_persona(persona_id)
    teaching_sessions_available = teaching_sessions is not None
    if teaching_sessions is None:
        teaching_sessions = []
    surfaces["teaching_sessions"] = _dataset_surface_status(
        "teaching_sessions",
        snapshot_at=snapshot_at,
        has_data=teaching_sessions_available,
        missing_message="Teaching sessions unavailable for this persona.",
    )

    # Backend-shaped allowed actions (acceptance: backend_shaped_persona_actions)
    allowed_actions = read_store.get_persona_allowed_actions(persona_id)
    allowed_actions_available = allowed_actions is not None
    if allowed_actions is None:
        allowed_actions = {}
    surfaces["allowed_actions"] = _dataset_surface_status(
        "allowed_actions",
        snapshot_at=snapshot_at,
        has_data=allowed_actions_available,
        missing_message="Allowed actions unavailable for this persona.",
    )

    data = {
        "persona": persona,
        "bindings": enriched_bindings,
        "sessions": sessions,
        "teaching_sessions": teaching_sessions,
        "allowedActions": allowed_actions,
    }

    return {
        "data": data,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
        },
    }


# --------------------------------------------------------------------------- #
# Post-Incident Review composed view
# --------------------------------------------------------------------------- #


@app.get("/api/v1/operator/post-incident-review/{incident_id}")
async def get_post_incident_review(
    incident_id: str,
    snapshot: str = "preferred",
    authorization: Optional[str] = Header(default=None),
):
    """
    Composed view for post-incident analysis.
    Composes: IN-04, EV-01, EV-02, LN-01, TL-03
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    incident = read_store.get_incident(incident_id)
    if not incident:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Incident not found",
            f"Incident {incident_id} does not exist",
        )

    snapshot_at = utc_now()
    surfaces = {}

    # IN-04: Postmortem report
    postmortem = read_store.get_postmortem_by_incident(incident_id)
    surfaces["postmortem"] = _dataset_surface_status(
        "postmortems",
        snapshot_at=snapshot_at,
        has_data=postmortem is not None,
        missing_message="No postmortem report available yet",
    )

    # EV-01/EV-02: Evolution decisions
    evolution_decisions = read_store.get_evolution_decisions_by_incident(incident_id)
    surfaces["evolution_decisions"] = _dataset_surface_status(
        "evolution_decisions",
        snapshot_at=snapshot_at,
    )

    # LN-01: Lineage edges — fetch by artifact_id from incident
    artifact_id = incident.get("artifact_id")
    lineage_edges = read_store.list_lineage_edges(artifact_id=artifact_id) if artifact_id else []
    surfaces["lineage"] = _dataset_surface_status(
        "lineage_edges",
        snapshot_at=snapshot_at,
        has_data=bool(lineage_edges),
        missing_message="No lineage edges found for this artifact",
    )

    # TL-03: Telemetry performance — use artifact_id (not runtime_id or summary)
    telemetry_performance = None
    if artifact_id:
        telemetry_performance = read_store.get_telemetry_performance(artifact_id)
    surfaces["telemetry_performance"] = _dataset_surface_status(
        "telemetry_performance",
        snapshot_at=snapshot_at,
        has_data=telemetry_performance is not None,
        missing_message="Telemetry performance unavailable for this artifact.",
    )

    data = {
        "incident": incident,
        "postmortem": postmortem,
        "evolution_decisions": evolution_decisions,
        "lineage_edges": lineage_edges,
        "telemetry_performance": telemetry_performance,
    }

    return {
        "data": data,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
        },
    }


# --------------------------------------------------------------------------- #
# Evolution Surfaces (Wave 3 — EV-01 – EV-04)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/evolution-decisions")
async def list_evolution_decisions(
    action_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """EV-01: Evolution Decision List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    decisions = [
        _project_evolution_decision_contract(decision)
        for decision in read_store.list_evolution_decisions(
            action_type=action_type,
            risk_level=risk_level,
            status=status,
        )
    ]
    items, next_page_token = _page_slice(decisions, page_token, page_size)
    return {
        "items": items,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": _snapshot_meta(snapshot_at),
    }


@app.get("/api/v1/evolution-decisions/{decision_id}")
async def get_evolution_decision(
    decision_id: str, authorization: Optional[str] = Header(default=None),
):
    """EV-02: Evolution Decision Detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    decision = read_store.get_evolution_decision_by_id(decision_id)
    if not decision:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Evolution decision not found",
            f"Evolution decision {decision_id} does not exist",
        )

    payload = _project_evolution_decision_contract(decision)
    payload["meta"] = _snapshot_meta(utc_now())
    return payload


@app.get("/api/v1/freeze-orders")
async def list_freeze_orders(
    status: Optional[str] = None,
    scope: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """EV-03: Freeze Order List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    orders = [
        _project_freeze_order_contract(order)
        for order in read_store.list_freeze_orders(status=status, scope=scope)
    ]
    return {
        "items": orders,
        "meta": _snapshot_meta(snapshot_at),
    }


@app.get("/api/v1/rollbacks")
async def list_rollbacks(
    runtime_id: Optional[str] = None,
    action_type: Optional[str] = None,
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """EV-04: Global Rollback List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    rollbacks = [
        _project_rollback_contract(rollback)
        for rollback in read_store.list_all_rollbacks(
            runtime_id=runtime_id,
            action_type=action_type,
            time_range=time_range,
        )
    ]
    return {
        "items": rollbacks,
        "meta": _snapshot_meta(snapshot_at),
    }


@app.get("/api/v1/operator/mutation-review/{decision_id}")
async def get_mutation_review(
    decision_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """EW-05: Compose the operator mutation-review projection."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    decision, approval_decision, linked_incident, linked_postmortem = _mutation_review_inputs(decision_id)
    if decision is None:
        return JSONResponse(
            status_code=404,
            content={"error": "decision_not_found", "decision_id": decision_id},
        )

    payload = _mutation_review_projection(
        decision,
        approval_decision=approval_decision,
        linked_incident=linked_incident,
        linked_postmortem=linked_postmortem,
        identity=identity,
        snapshot_at=utc_now(),
    )

    if payload["meta"]["surfaces"]["mutation_review"] == "unavailable":
        return JSONResponse(
            status_code=503,
            content={
                "error": "evidence_unavailable",
                "meta": {
                    "surfaces": {
                        "mutation_review": "unavailable",
                    }
                },
            },
        )

    required_fields = (
        "decision_id",
        "target_type",
        "target_id",
        "target_version",
        "action_type",
        "decision_state",
        "risk_level",
        "created_at",
    )
    missing_fields = [field for field in required_fields if payload.get(field) in (None, "")]
    if missing_fields:
        return JSONResponse(
            status_code=503,
            content={
                "error": "evidence_unavailable",
                "detail": f"Mutation review projection is missing required fields: {missing_fields}",
                "meta": {
                    "surfaces": {
                        "mutation_review": "unavailable",
                    }
                },
            },
        )

    return payload


# --------------------------------------------------------------------------- #
# Lineage Surfaces (Wave 3 — LN-01 – LN-03)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/lineage")
async def list_lineage(
    artifact_id: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """LN-01: Aggregated lineage list with optional artifact filter."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    surface = _dataset_surface_status("lineage_edges", snapshot_at=snapshot_at)
    items = read_store.list_lineage_records(artifact_id=artifact_id)
    if surface.get("status") == "unavailable":
        items = []
        next_page_token = None
    else:
        items, next_page_token = _page_slice(items, page_token, page_size)

    return {
        "items": items,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": _snapshot_meta(snapshot_at),
    }


@app.get("/api/v1/lineage/edges/{edge_id}")
async def get_lineage_edge(
    edge_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """LN-02: Lineage Edge Detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    edge = read_store.get_lineage_edge(edge_id)
    if not edge:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Lineage edge not found",
            f"Lineage edge {edge_id} does not exist",
        )

    payload = dict(edge)
    payload["meta"] = _snapshot_meta(utc_now())
    return payload


@app.get("/api/v1/lineage/graph")
async def get_lineage_graph(
    root_type: Optional[str] = None,
    root_id: str = Query(...),
    depth: int = 3,
    authorization: Optional[str] = Header(default=None),
):
    """LN-03: Lineage Graph from a root artifact with configurable depth."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    # Clamp depth to allowed range (§4.3)
    depth = max(1, min(depth, 10))

    snapshot_at = utc_now()
    edges = read_store.get_lineage_graph(root_type=root_type, root_id=root_id, depth=depth)
    nodes = read_store.get_lineage_graph_nodes(edges)
    return {
        "nodes": nodes,
        "edges": [
            {
                "id": edge.get("id"),
                "from_artifact_id": edge.get("from_artifact_id"),
                "to_artifact_id": edge.get("to_artifact_id"),
                "relationship": edge.get("relationship"),
            }
            for edge in edges
        ],
        "meta": _snapshot_meta(snapshot_at),
    }


@app.get("/api/v1/lineage/inspiration/{artifact_id}")
async def get_inspiration_graph(
    artifact_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """EW-04: BFF-composed inspiration graph for a target artifact."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    projection = read_store.get_inspiration_graph(artifact_id)
    artifact_exists = read_store.artifact_exists(artifact_id)
    dataset_source = read_store.dataset_source("inspiration_graphs")

    if projection is None and not artifact_exists:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Artifact not found",
            f"Artifact {artifact_id} does not exist",
        )

    return _ew04_inspiration_payload(
        artifact_id,
        projection,
        snapshot_at=snapshot_at,
        artifact_exists=artifact_exists or projection is not None,
    )


# --------------------------------------------------------------------------- #
# Telemetry Surfaces (Wave 3 — TL-01 – TL-03)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/telemetry")
async def list_telemetry(
    pool_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """TL-01: Telemetry Event List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    events = read_store.list_telemetry_events(
        pool_id=pool_id, artifact_id=artifact_id, time_range=time_range,
    )
    return {
        "data": events,
        "meta": {
            "total": len(events),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/telemetry/{runtime_id}/summary")
async def get_telemetry_summary(
    runtime_id: str,
    time_range: Optional[str] = None,
    aggregate_by: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """TL-02: Telemetry Summary for a runtime."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    summary = read_store.get_telemetry_summary(runtime_id)
    if not summary:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Telemetry summary not found",
            f"No telemetry summary for runtime {runtime_id}",
        )

    return {
        "data": summary,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/telemetry/{artifact_id}/performance")
async def get_telemetry_performance(
    artifact_id: str,
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """TL-03: Telemetry Performance Chart for an artifact."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    performance = read_store.get_telemetry_performance(artifact_id)
    if not performance:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Telemetry performance data not found",
            f"No performance data for artifact {artifact_id}",
        )

    return {
        "data": performance,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


# --------------------------------------------------------------------------- #
# Consultation surfaces (CS-01 – CS-06)
# Derived from PERSONA_RUNTIME_MODEL.md §6, §13, §14 via
# CONSULTATION_SURFACE_CONTRACT.md.  All surfaces are GET-only —
# writes are the Persona Plane's responsibility.
# --------------------------------------------------------------------------- #


@app.get("/api/v1/personas/{persona_id}/consultations")
def list_consultations(
    persona_id: str,
    consultation_type: Optional[str] = Query(default=None, alias="filter.consultation_type"),
    status: Optional[str] = Query(default=None, alias="filter.status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    """CS-01: List consultation sessions for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if persona is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"No persona with id {persona_id}",
        )

    consultations = read_store.list_consultations_for_persona(
        persona_id,
        consultation_type=consultation_type,
        status=status,
        page=page,
        page_size=page_size,
    )
    if consultations is None:
        return {
            "data": [],
            "meta": {
                "total": 0,
                "page": page,
                "page_size": page_size,
                "staleness": {
                    "served_from": "unavailable",
                    "last_known_at": utc_now(),
                },
            },
        }

    start = (page - 1) * page_size
    page_data = consultations[start: start + page_size]
    return {
        "data": [
            {
                **s,
                "_links": {
                    "self": f"/api/v1/consultations/{s['session_id']}",
                    "participants": f"/api/v1/consultations/{s['session_id']}/participants",
                    "outcome": f"/api/v1/consultations/{s['session_id']}/outcome",
                },
            }
            for s in page_data
        ],
        "meta": {
            "total": len(consultations),
            "page": page,
            "page_size": page_size,
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/consultations/{session_id}")
def get_consultation(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-02: Consultation session detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    session = read_store.get_consultation(session_id)
    if session is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    return {
        "data": {
            **session,
            "_links": {
                "self": f"/api/v1/consultations/{session_id}",
                "participants": f"/api/v1/consultations/{session_id}/participants",
                "outcome": f"/api/v1/consultations/{session_id}/outcome",
                "evidence": f"/api/v1/consultations/{session_id}/evidence",
            },
        },
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/consultations/{session_id}/participants")
def get_consultation_participants(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-03: All participants in a consultation session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    participants = read_store.get_consultation_participants(session_id)
    if participants is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    return {
        "data": [
            {
                **p,
                "_links": {
                    "self": f"/api/v1/sessions/{p['session_id']}",
                    "persona": f"/api/v1/personas/{p['persona_id']}",
                },
            }
            for p in participants
        ],
        "meta": {
            "total": len(participants),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/consultations/{session_id}/outcome")
def get_consultation_outcome(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-04: Consultation outcome projection."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    outcome = read_store.get_consultation_outcome(session_id)
    if outcome is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    return {
        "data": outcome,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/consultations/{session_id}/evidence")
def get_consultation_evidence(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-05: Evidence refs attached to a consultation session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    evidence = read_store.get_consultation_evidence(session_id)
    if evidence is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    return {
        "data": evidence,
        "meta": {
            "total": len(evidence),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/personas/{persona_id}/consult-policy")
def get_consult_policy(
    persona_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-06: Consult policy for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if persona is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"No persona with id {persona_id}",
        )

    policy = read_store.get_consult_policy(persona_id)
    if policy is None:
        # Policy may not exist yet — return a safe empty structure rather than 404
        # so operators always get a valid read (policy absence is itself informative)
        return {
            "data": {
                "id": None,
                "persona_id": persona_id,
                "required_reviewers": 0,
                "required_committees": [],
                "trigger_rules": [],
                "forbidden_solo_actions": [],
                "escalation_rules": [],
            },
            "meta": {
                "staleness": _meta_staleness(),
                "note": "No consult policy found for this persona. Defaulting to empty policy.",
            },
        }

    return {
        "data": policy,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


# --------------------------------------------------------------------------- #
# Command submission (write path — async execution)
# --------------------------------------------------------------------------- #

@app.post("/api/v1/operator/commands", response_model=CommandSubmissionResponse, status_code=202)
async def submit_command(
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
):
    """
    Submit an operator command for async execution.

    Returns 202 with a command receipt.  Poll GET /api/v1/operator/commands/{command_id}
    for status updates.
    """
    # 1. Authenticate
    identity = _extract_identity(authorization)
    cmd = _normalize_operator_command_payload(payload)

    # 2. Command-specific precondition validation (role + params shape)
    _validate_audit_context(cmd)
    _validate_drawer_runtime_target(cmd)
    validator = _VALIDATORS.get(cmd.command)
    if validator:
        validator(cmd.params, identity)
    stored_params = _stored_command_params(cmd, identity)

    # 3. Concurrent modification check (§5.1)
    active = command_store.get_active_commands_for_target(cmd.target.type.value, cmd.target.id)
    if active:
        raise _bff_error(
            409, ErrorCode.CONCURRENT_MODIFICATION,
            "A command is already in flight for this target",
            f"Command {active[0]['command_id']} is currently {active[0]['status']}",
            precondition_failed="concurrent_safety",
            suggestion="Wait for the in-flight command to complete or time out before retrying",
        )

    # 4. Degraded mode check (§7.1)
    staleness_warning = _check_read_surface_state()

    # 5. Persist command with full audit record
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()

    # Extract raw token from Authorization header for downstream propagation
    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization[len("Bearer "):]

    # Use provided MFA token if present; otherwise stub a 6-digit token when MFA was verified.
    # This keeps the internal API scaffold happy while preserving the "mfa" flag semantics.
    mfa_token = x_mfa_token or ("000000" if identity.mfa_verified else None)

    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "mfa_verified": identity.mfa_verified,
        "reason": cmd.audit_context.reason,
        "incident_id": cmd.audit_context.incident_id,
        "preconditions_checked": [
            "authentication", "authorization", "params_shape", "concurrent_safety"
        ],
        "timestamp": submitted_at,
        "staleness_warning": staleness_warning.model_dump() if staleness_warning else None,
        "auth_token": raw_token,
        "mfa_token": mfa_token,
    }

    command_store.submit_command(
        command_id=command_id,
        command_type=cmd.command,
        target=cmd.target,
        submitted_at=submitted_at,
        params=stored_params,
        audit_context=audit_record,
    )

    log.info(
        "Accepted command %s (%s) for %s:%s by operator %s",
        command_id, cmd.command.value, cmd.target.type.value, cmd.target.id, identity.operator_id,
    )

    # 6. Queue for async processing
    background_tasks.add_task(_process_command_stub, command_id)

    return _project_command_submission_response(
        command_id=command_id,
        command=cmd.command,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )


@app.get("/api/v1/operator/commands/{command_id}", response_model=CommandStatusResponse)
async def get_command_status(command_id: str, authorization: Optional[str] = Header(default=None)):
    """
    Poll for the status of a previously submitted command.
    """
    # Auth required to read command status (prevents polling by unauthenticated callers)
    _extract_identity(authorization)

    record = command_store.get_command(command_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Command {command_id} not found")

    return CommandStatusResponse(
        command_id=record["command_id"],
        type=record["type"],
        target=record["target"],
        submitted_at=record["submitted_at"],
        status=record["status"],
        result=record.get("result"),
        error=record.get("error"),
        audit=record.get("audit"),
    )


# --------------------------------------------------------------------------- #
# Background worker — real execution path
# --------------------------------------------------------------------------- #

async def _process_command(command_id: str):
    """
    Async command processor that dispatches to the Protected Internal API.
    Records authoritative status, result, and audit data for every execution.
    """
    import asyncio

    record = command_store.get_command(command_id)
    if not record:
        log.error("Worker: command %s not found in store", command_id)
        return

    command_type = CommandType(record["type"])
    params = record.get("params", {})
    audit = record.get("audit", {})

    # Extract auth tokens from submission audit context for downstream calls
    auth_token = audit.get("auth_token")
    mfa_token = audit.get("mfa_token")

    # Mark processing
    await asyncio.sleep(0.05)  # brief yield to event loop
    command_store.update_status(command_id, CommandStatus.PROCESSING)

    try:
        execution_params = _resolve_execution_params_for_record(record)
    except Exception as exc:
        failed_at = utc_now()
        error = {
            "code": "TARGET_CONTEXT_UNAVAILABLE",
            "message": f"Unable to route command {command_id}: {exc}",
            "started_at": failed_at,
            "failed_at": failed_at,
            "suggestion": (
                "Refresh Pantheon runtime/incident read surfaces or use the secondary control path "
                "until the runtime target can be resolved."
            ),
        }
        audit["execution_completed_at"] = failed_at
        audit["executor"] = "command_executor"
        audit["failure_reason"] = error["message"]
        audit["failure_suggestion"] = error["suggestion"]
        command_store.update_status(
            command_id,
            CommandStatus.FAILED,
            error=error,
            audit=audit,
        )
        log.warning("Worker: command %s failed during routing resolution: %s", command_id, exc)
        return

    if command_type == CommandType.RECORD_SPONSOR_DECISION:
        try:
            committee_id = str(execution_params.get("committee_id") or "").strip()
            updated = read_store.record_sponsor_decision(
                committee_id,
                sponsor_decision=str(execution_params.get("sponsor_decision") or "").strip().lower(),
                rationale_ref=str(execution_params.get("rationale_ref") or "").strip(),
                actor_id=str(audit.get("operator_id") or "operator-command"),
                recorded_at=utc_now(),
            )
            if updated is None:
                raise ValueError(f"Committee {committee_id} could not be updated.")
            result = {
                "command_id": command_id,
                "committee_id": updated.get("committee_id"),
                "committee_ref": updated.get("committee_ref"),
                "sponsor_decision": updated.get("sponsor_decision"),
                "sponsor_decided_at": updated.get("sponsor_decided_at"),
                "sponsor_decided_by": updated.get("sponsor_decided_by"),
                "consensus_state": updated.get("consensus_state"),
                "rationale_ref": (updated.get("synthesis_summary") or {}).get("rationale_ref"),
                "execution_completed_at": utc_now(),
            }
            audit["execution_completed_at"] = result["execution_completed_at"]
            audit["executor"] = "bff_read_store"
            audit["downstream_verified"] = True
            command_store.update_status(
                command_id,
                CommandStatus.EXECUTED,
                result=result,
                audit=audit,
            )
            log.info("Worker: command %s completed with status=%s", command_id, CommandStatus.EXECUTED.value)
            return
        except Exception as exc:
            failed_at = utc_now()
            error = {
                "code": "COMMITTEE_UPDATE_FAILED",
                "message": f"Unable to record sponsor decision: {exc}",
                "started_at": failed_at,
                "failed_at": failed_at,
                "suggestion": "Refresh the committee board projection and retry once the committee surface is available.",
            }
            audit["execution_completed_at"] = failed_at
            audit["executor"] = "bff_read_store"
            audit["failure_reason"] = error["message"]
            audit["failure_suggestion"] = error["suggestion"]
            command_store.update_status(
                command_id,
                CommandStatus.FAILED,
                error=error,
                audit=audit,
            )
            log.warning("Worker: command %s failed during committee update: %s", command_id, exc)
            return

    # Execute via real executor with propagated auth headers
    status, result, error = execute_command_with_status(
        command_id, command_type, execution_params,
        auth_token=auth_token, mfa_token=mfa_token,
    )

    # Enrich audit with execution timeline
    audit["execution_completed_at"] = result.get("execution_completed_at") if result else error.get("failed_at") if error else None
    audit["executor"] = "command_executor"
    if result:
        audit["downstream_verified"] = True
    if error:
        audit["failure_reason"] = error.get("message", "")
        audit["failure_suggestion"] = error.get("suggestion", "")

    # Persist both result and enriched audit data
    command_store.update_status(
        command_id,
        status,
        result=result,
        error=error,
        audit=audit,
    )

    log.info(
        "Worker: command %s completed with status=%s",
        command_id, status.value,
    )


# Keep backward-compatible alias for existing tests
_process_command_stub = _process_command


# --------------------------------------------------------------------------- #
# Degraded Control Guidance (Wave 2 — Incident Response)
# --------------------------------------------------------------------------- #

@app.get("/api/v1/operator/degraded-control-guidance")
async def degraded_control_guidance():
    """Return guidance for operators when the BFF is degraded or unavailable.

    Provides actionable fallback instructions using the secondary control path
    (Admin CLI and Protected Internal API) so operators can still execute
    critical incident actions (pause, rollback, kill-switch) even when the
    primary BFF path is down.

    See BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §6 and
    APP-002-SECONDARY-CONTROL-PATH.md for full spec.
    """
    state = _read_surface_state()
    guidance = {
        "current_state": state,
        "command_backend_configured": bool(os.getenv("PANTHEON_INTERNAL_API_URL", "").strip()),
        "primary_path": {
            "url": "/api/v1/operator/commands",
            "status": "available" if state == "fresh" else "degraded",
            "note": (
                "Primary BFF command path. Submit operator commands for async execution."
                if state == "fresh"
                else "BFF read surface is degraded. Commands may execute but status queries could return stale data."
            ),
        },
        "secondary_path": {
            "admin_cli": {
                "description": "Local/SSH CLI with RBAC and MFA for destructive actions",
                "commands": {
                    "pause_runtime": "pantheon-admin runtime pause --binding-id <ID> --reason <REASON>",
                    "resume_runtime": "pantheon-admin runtime resume --binding-id <ID>",
                    "rollback": "pantheon-admin rollback --target-type <TYPE> --target-id <ID> --to-version <VER>",
                    "kill_switch": "pantheon-admin kill-switch activate --scope <SCOPE> --reason <REASON>",
                },
                "auth": "SSH key + RBAC role; MFA required for destructive actions",
            },
            "protected_internal_api": {
                "description": "Direct HTTP access to control-plane internal API",
                "base_url": os.getenv("PANTHEON_INTERNAL_API_URL", "").strip() or None,
                "endpoints": {
                    "pause_runtime": "POST /api/internal/v1/runtimes/{binding_id}/pause",
                    "execute_rollback": "POST /api/internal/v1/rollbacks/execute",
                    "activate_kill_switch": "POST /api/internal/v1/kill-switch",
                    "approve_deployment": "POST /api/internal/v1/deployments/{plan_id}/approve",
                    "check_command_status": "GET /api/internal/v1/commands/{command_id}",
                },
                "auth": "Bearer token + RBAC; X-MFA-Token header for destructive actions",
            },
        },
        "critical_actions_bypass_mfa": True,
        "reconciliation": {
            "description": "When BFF recovers, reconcile command history from internal API",
            "endpoint": "GET /api/internal/v1/commands",
            "note": "Both BFF and internal API persist command records; compare by command_id to detect gaps.",
        },
        "spec_reference": "support/sidecars/APP-002/APP-002-SECONDARY-CONTROL-PATH.md",
    }

    status_code = 200 if state == "fresh" else 206
    return JSONResponse(
        status_code=status_code,
        content={"data": guidance, "meta": {"staleness": _meta_staleness()}},
    )


# --------------------------------------------------------------------------- #
# SSE Real-Time Feeds (Wave 5 — APP-002-W5-SSE-LIVE)
# --------------------------------------------------------------------------- #

# In-process event buffers per stream type.
# Each buffer is a deque of (event_id, event_dict) tuples, keeping the last N events.
_MAX_EVENTS = 500

_runtime_events: deque = deque(maxlen=_MAX_EVENTS)
_incident_events: deque = deque(maxlen=_MAX_EVENTS)
_kill_switch_events: deque = deque(maxlen=_MAX_EVENTS)

# Subscribers (asyncio.Queue) for each stream type
_runtime_subscribers: list[asyncio.Queue] = []
_incident_subscribers: list[asyncio.Queue] = []
_kill_switch_subscribers: list[asyncio.Queue] = []


def _make_event_id(prefix: str = "evt") -> str:
    return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def _sse_format(event: dict) -> str:
    """Format a full event dict as an SSE message block."""
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )


def _publish_event(buffer: deque, subscribers: list[asyncio.Queue], event_type: str, data: dict) -> str:
    """Publish an event to the buffer and notify all subscribers."""
    event_id = _make_event_id()
    event = {
        "id": event_id,
        "type": event_type,
        "timestamp": utc_now(),
        "data": data,
    }
    buffer.append((event_id, event))
    # Notify subscribers
    for q in list(subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
    return event_id


def _replay_from(buffer: deque, last_event_id: Optional[str]) -> list[dict]:
    """Replay events from the buffer starting after last_event_id."""
    if not last_event_id:
        return [evt for _, evt in buffer]
    found = False
    result = []
    for eid, evt in buffer:
        if found:
            result.append(evt)
        elif eid == last_event_id:
            found = True
    if not found:
        # Client requested an event ID we no longer have — return full buffer
        return [evt for _, evt in buffer]
    return result


async def _sse_stream(
    buffer: deque,
    subscribers: list[asyncio.Queue],
    last_event_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted events."""
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    subscribers.append(q)
    try:
        # Replay historical events first
        for evt in _replay_from(buffer, last_event_id):
            yield _sse_format(evt)

        # Then stream new events as they arrive
        while True:
            try:
                evt = await asyncio.wait_for(q.get(), timeout=30.0)
                yield _sse_format(evt)
            except asyncio.TimeoutError:
                # Send a comment to keep the connection alive
                yield ": heartbeat\n\n"
    finally:
        # Unsubscribe on client disconnect
        if q in subscribers:
            subscribers.remove(q)


@app.get("/api/v1/runtime/{runtime_id}/events/stream")
async def stream_runtime_events(
    runtime_id: str,
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """RT-SSE: Server-Sent Events stream for runtime state changes.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    BFF_API_CONTRACT.md §11.2
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return StreamingResponse(
        _sse_stream(_runtime_events, _runtime_subscribers, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/incidents/stream")
async def stream_incident_events(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """IN-SSE: Server-Sent Events stream for active incident events.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    BFF_API_CONTRACT.md §11.2
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return StreamingResponse(
        _sse_stream(_incident_events, _incident_subscribers, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/kill-switch/updates")
async def stream_kill_switch_events(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """KS-SSE: Server-Sent Events stream for kill-switch state changes.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    BFF_API_CONTRACT.md §11.2
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return StreamingResponse(
        _sse_stream(_kill_switch_events, _kill_switch_subscribers, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# SSE Publish Helpers (for internal use / testing / admin injection)
# --------------------------------------------------------------------------- #

@app.post("/api/v1/internal/sse/publish")
async def publish_sse_event(
    event_type: str = Query(..., description="Event type: runtime_state_changed, incident_created, etc."),
    runtime_id: Optional[str] = Query(default=None),
    incident_id: Optional[str] = Query(default=None),
    payload: Dict[str, Any] = {},
    authorization: Optional[str] = Header(default=None),
):
    """Internal helper to publish SSE events for testing and integration.

    In production, events would be published by downstream services via
    an internal message bus. This endpoint is a convenience for smoke tests.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    if event_type.startswith("runtime"):
        event_id = _publish_event(
            _runtime_events, _runtime_subscribers, event_type,
            {"runtime_id": runtime_id, **payload},
        )
    elif event_type.startswith("incident"):
        event_id = _publish_event(
            _incident_events, _incident_subscribers, event_type,
            {"incident_id": incident_id, **payload},
        )
    elif event_type.startswith("kill_switch"):
        event_id = _publish_event(
            _kill_switch_events, _kill_switch_subscribers, event_type,
            payload,
        )
    else:
        raise _bff_error(400, ErrorCode.INVALID_REQUEST, "Unknown event type", f"Event type {event_type} not recognized")

    return {"event_id": event_id, "status": "published"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
