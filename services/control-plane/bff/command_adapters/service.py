"""Command Adapters domain service and orchestration helpers.

This module encapsulates command admission, validation, confirmation token
lifecycles, action catalog resolution, and domain command dispatch while
remaining completely decoupled from ``bff.main``.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
import os
import re
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple


async def _execute_command_background_task(task_fn: Callable[[str], Any], command_id: str) -> None:
    res = task_fn(command_id)
    if asyncio.iscoroutine(res):
        await res

from fastapi import HTTPException, Response
from fastapi.responses import JSONResponse

try:
    from ..action_catalog import get_action_catalog, get_catalog_entry
    from ..models import (
        ActionCommandStatus,
        BffActionCatalogResponse,
        CommandReceipt,
        CommandReceiptStatus,
        CommandResponse,
        CommandResultMeta,
        CommandStatus,
        CommandStatusResponse,
        CommandType,
        ErrorCode,
        ObjectType,
        OperatorCommand,
        OperatorIdentity,
        StalenessWarning,
        TargetObject,
        utc_now,
    )
except (ImportError, ValueError):
    from action_catalog import get_action_catalog, get_catalog_entry
    from models import (
        ActionCommandStatus,
        BffActionCatalogResponse,
        CommandReceipt,
        CommandReceiptStatus,
        CommandResponse,
        CommandResultMeta,
        CommandStatus,
        CommandStatusResponse,
        CommandType,
        ErrorCode,
        ObjectType,
        OperatorCommand,
        OperatorIdentity,
        StalenessWarning,
        TargetObject,
        utc_now,
    )
from .base import ActionUnavailableError
from .contracts import (
    _FINAL_COMMAND_ROUTE,
    _HUMAN_GATE_DECISIONS_BY_COMMAND,
    build_foundation_command_context,
    normalize_operator_command_payload,
    resolve_final_idempotency_key,
    serialize_foundation_context,
    stable_json_hash,
)

_DRAWER_RUNTIME_COMMANDS = {
    CommandType.PAUSE_EXECUTION,
    CommandType.ISSUE_RISK_OFF,
    CommandType.LIQUIDATE_ALL,
    CommandType.HARD_ROLLBACK,
    CommandType.ISSUE_SAFE_MODE,
}

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


def stored_command_params(
    cmd: OperatorCommand,
    identity: OperatorIdentity,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if cmd.command in _DRAWER_RUNTIME_COMMANDS:
        return dict(cmd.params)
    params = dict(cmd.params)
    if cmd.command == CommandType.REMEDIATE_SENTINEL_INTERVENTION and raw_payload:
        if not str(params.get("two_man_signature_id") or "").strip():
            for alias in _TWO_MAN_EVIDENCE_FIELDS:
                val = str(raw_payload.get(alias) or "").strip()
                if val:
                    params["two_man_signature_id"] = val
                    break
    if cmd.command == CommandType.APPROVED_APPLY:
        params.pop("rebalanceId", None)
        params["rebalance_id"] = cmd.target.id
    elif cmd.command == CommandType.EMERGENCY_CONTAINMENT:
        params.pop("personaId", None)
        params["persona_id"] = cmd.target.id
    elif cmd.command in {CommandType.PAUSE_PAPER_RUNTIME, CommandType.RESUME_PAPER_RUNTIME}:
        target_rt_id = str(cmd.target.id).strip()
        params["runtime_id"] = target_rt_id
        params["entity_id"] = target_rt_id
        params.pop("runtimeId", None)
        params.pop("entityId", None)
        params.pop("verified_binding", None)
        params.pop("verified_binding_id", None)
        params.pop("verified_runtime_binding_id", None)
        if raw_payload and "bounded_duration_minutes" in raw_payload and "bounded_duration_minutes" not in params:
            params["bounded_duration_minutes"] = raw_payload["bounded_duration_minutes"]
        bdm = params.get("bounded_duration_minutes")
        if bdm is not None:
            try:
                bdm_val = int(bdm)
                if bdm_val > 0:
                    params["duration_seconds"] = bdm_val * 60
            except (ValueError, TypeError):
                pass
    canonical_action_id = _HUMAN_GATE_DECISIONS_BY_COMMAND.get(
        cmd.command,
        cmd.action or cmd.params.get("action_id") or cmd.params.get("actionId") or cmd.command.value,
    )
    if cmd.command == CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT:
        canonical_action_id = "submit_recommendation"
    canonical_paper = cmd.command in {CommandType.PAUSE_PAPER_RUNTIME, CommandType.RESUME_PAPER_RUNTIME}
    if canonical_paper:
        canonical_action_id = cmd.command.value
    params.update(
        {
            "entity_type": "Runtime" if canonical_paper else (cmd.params.get("entity_type") or cmd.target.type.value),
            "entity_id": cmd.target.id,
            "action_id": canonical_action_id,
            "actionId": canonical_action_id,
            "actor_id": identity.operator_id,
            "actor_role": next(
                (
                    role
                    for role in ("admin", "approver", "reviewer", "operator")
                    if role in identity.roles
                ),
                "operator",
            ),
        }
    )
    return params


_stored_command_params = stored_command_params

from .preconditions import (
    assert_duplicate_confirm_token_matches,
    canonicalize_validated_precondition_evidence,
    ensure_live_broker_scope_allowed,
    reject_body_idempotency_key,
    reject_server_managed_rebalance_evidence_command,
    require_final_command_preconditions,
    retryable_terminal_capital_command,
    validate_audit_context,
    validate_capital_authority_target_binding,
    validate_drawer_runtime_target,
    validate_final_command_target_type,
    validate_paper_runtime_authority_target_binding,
)
from .receipts import (
    command_dual_write_receipts,
    command_response_dry_run_meta,
    command_response_durable_meta,
    command_runtime_auth_context,
    foundation_bff_error,
    foundation_idempotency_conflict_error,
    project_final_command_response,
)
from .registry import dispatch_domain_command

log = logging.getLogger(__name__)

_OPERATOR_WRITE_ROLES = {"operator", "admin"}
_READ_ROLES = {"operator", "reviewer", "approver", "viewer", "admin"}
_CONFIRM_TOKEN_FIELDS = ("confirm_token", "confirmToken", "confirmation_token", "confirmationToken")

_COMMAND_AUTH_CONTEXT: Dict[str, Dict[str, Optional[str]]] = {}


def set_command_auth_context(command_id: str, context: Dict[str, Optional[str]]) -> None:
    _COMMAND_AUTH_CONTEXT[command_id] = dict(context)


def pop_command_auth_context(command_id: str) -> Dict[str, Optional[str]]:
    return _COMMAND_AUTH_CONTEXT.pop(command_id, {})



def _stable_json_hash(payload: Any) -> str:
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return sha256(encoded).hexdigest()
    except Exception:
        return sha256(str(payload).encode("utf-8")).hexdigest()


def _resolve_final_idempotency_key(
    idempotency_key: Optional[str] = None,
    x_idempotency_key: Optional[str] = None,
) -> str:
    key = str(idempotency_key or x_idempotency_key or "").strip()
    return key


def _reject_body_idempotency_key(payload: Optional[Dict[str, Any]]) -> None:
    if not isinstance(payload, dict):
        return
    for bad_key in ("idempotency_key", "idempotencyKey", "Idempotency-Key", "X-Idempotency-Key"):
        if bad_key in payload:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": ErrorCode.VALIDATION_FAILED.value,
                        "message": "Idempotency key must be provided via header, not body",
                        "details": {
                            "precondition_failed": "body_idempotency_key",
                            "suggestion": "Pass Idempotency-Key as an HTTP header",
                        },
                    }
                },
            )


def _audit_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _truthy_header(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    val = str(value).strip().lower()
    return val in ("1", "true", "yes", "on")


def _check_read_surface_state() -> Optional[StalenessWarning]:
    """In production, query the BFF read surface health endpoint.
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


def _extract_record_tenant(command: Dict[str, Any]) -> Optional[str]:
    audit = command.get("audit") if isinstance(command.get("audit"), dict) else {}
    for key in ("tenant_id", "tenant"):
        value = str(audit.get(key) or "").strip()
        if value:
            return value

    foundation = command.get("foundation") if isinstance(command.get("foundation"), dict) else {}
    record = foundation.get("idempotency_record") if isinstance(foundation.get("idempotency_record"), dict) else {}
    if record.get("tenant_id"):
        return str(record.get("tenant_id")).strip()

    trace = foundation.get("trace_context") if isinstance(foundation.get("trace_context"), dict) else {}
    tenant_ref = trace.get("tenant_ref") if isinstance(trace.get("tenant_ref"), dict) else {}
    value = str(tenant_ref.get("tenant_id") or trace.get("tenant_id") or "").strip()
    if value:
        return value

    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    for key in ("tenant_id", "tenant"):
        val = str(params.get(key) or "").strip()
        if val:
            return val
    return None


# Single product owner of the governance action_kind -> ObjectType and
# action_id -> CommandType mapping used by ``submit_governance_action``.
# ``governance/router.py`` only ever submits action_kind="review" (from
# POST /bff/reviews and POST /bff/reviews/{id}/actions/{id}) or
# action_kind="approval" (from POST /bff/approvals/{id}/decide and
# POST /bff/approvals/batch-decide); do not fork a second copy of this table.
_GOVERNANCE_ACTION_KIND_OBJECT_TYPES: Dict[str, "ObjectType"] = {
    "review": ObjectType.REVIEW,
    "approval": ObjectType.APPROVAL_DECISION,
}

_GOVERNANCE_DECISION_COMMAND_TYPES: Dict[str, "CommandType"] = {
    "approve": CommandType.APPROVE_DECISION,
    "reject": CommandType.REJECT_DECISION,
    "request_revision": CommandType.REQUEST_APPROVAL_REVISION,
    "request_changes": CommandType.REQUEST_APPROVAL_REVISION,
}


def resolve_governance_object_type(action_kind: str) -> ObjectType:
    return _GOVERNANCE_ACTION_KIND_OBJECT_TYPES.get(action_kind, ObjectType.REVIEW)


def resolve_governance_command_type(action_kind: str, action_id: str) -> CommandType:
    if action_kind == "approval":
        # escalate/freeze are accepted decisions without a dedicated command
        # type yet; route them through the revision-request command as a
        # pass-through until a dedicated command type is defined.
        return _GOVERNANCE_DECISION_COMMAND_TYPES.get(action_id, CommandType.REQUEST_APPROVAL_REVISION)
    return CommandType.REVIEW_ACTION


class CommandAdapterService:
    """Domain service managing operator commands, action adapters, and confirmation tokens."""

    def __init__(
        self,
        *,
        command_store: Optional[Any] = None,
        read_surface: Optional[Any] = None,
        get_command_store: Optional[Callable[[], Any]] = None,
        get_read_store: Optional[Callable[[], Any]] = None,
        extract_identity: Optional[Callable[..., OperatorIdentity]] = None,
        require_operator_role: Optional[Callable[[OperatorIdentity], None]] = None,
        require_read_role: Optional[Callable[[OperatorIdentity], None]] = None,
        bff_error: Optional[Callable[..., Exception]] = None,
        utc_now_fn: Optional[Callable[[], str]] = None,
        utc_now: Optional[Callable[[], str]] = None,
        submit_command_admission: Optional[Callable[..., Any]] = None,
        dispatch_command_fn: Optional[Callable[..., Any]] = None,
        publish_event: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        gov_bff_idempotency: Optional[Dict[str, Dict[str, Any]]] = None,
        final_contract_idempotency: Optional[Dict[str, Dict[str, Any]]] = None,
        check_read_surface_state: Optional[Callable[[], Optional[StalenessWarning]]] = None,
        validators: Optional[Dict[Any, Callable[..., None]]] = None,
        process_command_task: Optional[Callable[[str], Any]] = None,
    ) -> None:
        if command_store is not None:
            self._get_command_store = (lambda: command_store() if callable(command_store) else command_store)
        else:
            self._get_command_store = get_command_store
        if read_surface is not None:
            self._get_read_store = (lambda: read_surface() if callable(read_surface) else read_surface)
        else:
            self._get_read_store = get_read_store
        self._extract_identity = extract_identity
        self._require_operator_role = require_operator_role
        self._require_read_role = require_read_role
        self._bff_error = bff_error
        fallback_utc_now = globals()["utc_now"]
        self._utc_now = utc_now or utc_now_fn or fallback_utc_now
        self._dispatch_command = dispatch_command_fn or dispatch_domain_command
        self._check_read_surface_state = check_read_surface_state
        if validators is not None:
            self._validators = validators
        else:
            try:
                from .preconditions import build_default_validators
                self._validators = build_default_validators(
                    read_surface=self._get_read_store,
                    bff_error_fn=self._bff_error,
                    utc_now_fn=self._utc_now,
                )
            except Exception:
                self._validators = {}
        self._process_command_task = process_command_task or (lambda cmd_id: _process_command_stub(cmd_id, command_store=self.command_store, read_store=self.read_store))
        self._submit_command_admission = submit_command_admission or self.submit_command_admission

        self._final_contract_idempotency: Dict[str, Dict[str, Any]] = (
            final_contract_idempotency if final_contract_idempotency is not None else {}
        )
        self._gov_bff_idempotency: Dict[str, Dict[str, Any]] = (
            gov_bff_idempotency if gov_bff_idempotency is not None else {}
        )
        self._publish_event = publish_event

    @property
    def command_store(self) -> Any:
        if self._get_command_store is not None:
            return self._get_command_store()
        return None

    @property
    def read_store(self) -> Any:
        if self._get_read_store is not None:
            return self._get_read_store()
        return None

    def check_read_surface_state(self) -> Optional[StalenessWarning]:
        if self._check_read_surface_state is not None:
            return self._check_read_surface_state()
        return _check_read_surface_state()

    def _raise_error(
        self,
        status_code: int,
        code: ErrorCode,
        message: str,
        detail_msg: str,
        *,
        precondition_failed: Optional[str] = None,
        suggestion: Optional[str] = None,
        details_extra: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> Exception:
        if self._bff_error is not None:
            return self._bff_error(
                status_code,
                code,
                message,
                detail_msg,
                precondition_failed=precondition_failed,
                suggestion=suggestion,
                details_extra=details_extra,
                correlation_id=correlation_id,
            )
        details: Dict[str, Any] = {"message": detail_msg}
        if precondition_failed:
            details["precondition_failed"] = precondition_failed
        if suggestion:
            details["suggestion"] = suggestion
        if details_extra:
            details.update(details_extra)
        if correlation_id:
            details["correlation_id"] = correlation_id
        return HTTPException(
            status_code=status_code,
            detail={
                "error": {
                    "code": code.value if isinstance(code, ErrorCode) else str(code),
                    "message": message,
                    "details": details,
                }
            },
        )

    def extract_identity(self, authorization: Optional[str], mfa_token: Optional[str] = None) -> OperatorIdentity:
        if self._extract_identity is not None:
            return self._extract_identity(authorization, mfa_token=mfa_token)
        raise self._raise_error(
            401,
            ErrorCode.AUTH_REQUIRED,
            "Identity extraction is not configured",
            "No extract_identity policy was injected into CommandAdapterService; refusing to guess an "
            "identity from the raw token or fall back to an anonymous/viewer identity.",
            precondition_failed="extract_identity_unconfigured",
        )

    def check_operator_role(self, identity: OperatorIdentity) -> None:
        if self._require_operator_role is not None:
            self._require_operator_role(identity)
            return
        if not _OPERATOR_WRITE_ROLES.intersection(identity.roles):
            raise self._raise_error(
                403,
                ErrorCode.FORBIDDEN,
                "Operator role required",
                "Caller does not possess operator authority",
                precondition_failed="role_check",
            )

    def check_read_role(self, identity: OperatorIdentity) -> None:
        if self._require_read_role is not None:
            self._require_read_role(identity)
            return
        if not _READ_ROLES.intersection(identity.roles):
            raise self._raise_error(
                403,
                ErrorCode.FORBIDDEN,
                "Read role required",
                "Caller does not possess read access",
                precondition_failed="role_check",
            )

    def get_action_catalog(self, identity: Optional[OperatorIdentity] = None) -> BffActionCatalogResponse:
        return get_action_catalog()

    def get_command_status(self, command_id: str, identity: Optional[OperatorIdentity] = None) -> CommandStatusResponse:
        clean_id = str(command_id or "").strip()
        if not clean_id:
            raise HTTPException(status_code=404, detail="Command not found")
        store = self.command_store
        if store is None:
            raise HTTPException(status_code=404, detail=f"Command {clean_id} not found")
        record = store.get_command(clean_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Command {clean_id} not found")
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

    def confirm_token_records(self, token_id: str, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        store = self.command_store
        if store is None:
            return []
        commands = getattr(store, "_get_all_commands", lambda: [])()
        clean_tenant = str(tenant_id or "").strip() or None
        results = []
        for record in commands:
            if not (
                isinstance(record.get("target"), dict)
                and record["target"].get("type") == ObjectType.CONFIRM_TOKEN.value
                and str(record["target"].get("id") or "") == token_id
            ):
                continue
            if clean_tenant is not None:
                rec_tenant = _extract_record_tenant(record)
                if rec_tenant != clean_tenant:
                    continue
            results.append(record)
        return results

    def confirm_token_expiry_from_record(self, record: Dict[str, Any]) -> Optional[datetime]:
        params = record.get("params") if isinstance(record.get("params"), dict) else {}
        absolute = params.get("expiresAt") or params.get("expires_at")
        parsed_absolute = _audit_datetime(absolute)
        if parsed_absolute is not None:
            return parsed_absolute

        raw_ttl = params.get("ttlSeconds", params.get("ttl_seconds", params.get("ttl")))
        if raw_ttl in (None, ""):
            return None
        try:
            ttl_seconds = float(raw_ttl)
        except (TypeError, ValueError):
            return None
        submitted_at = _audit_datetime(record.get("submitted_at"))
        if submitted_at is None:
            return None
        return submitted_at + timedelta(seconds=ttl_seconds)

    def _guarded_command_confirm_token_id(self, record: Dict[str, Any]) -> Optional[str]:
        entry = get_catalog_entry(str(record.get("type") or ""))
        if entry is None or not getattr(entry, "requires_confirm_token", False):
            return None
        audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
        evidence = (
            audit.get("precondition_evidence")
            if isinstance(audit.get("precondition_evidence"), dict)
            else {}
        )
        params = record.get("params") if isinstance(record.get("params"), dict) else {}
        token_id = str(
            evidence.get("confirm_token_id")
            or params.get("confirm_token_id")
            or ""
        ).strip()
        return token_id or None

    def confirm_token_lifecycle_payload(self, token_id: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        status = "available"
        expires_at: Optional[datetime] = None
        latest_record: Optional[Dict[str, Any]] = None
        token_tenant: Optional[str] = None
        store = self.command_store
        commands = getattr(store, "_get_all_commands", lambda: [])() if store is not None else []

        clean_tenant = str(tenant_id or "").strip() or None

        for record in commands:
            target = record.get("target") if isinstance(record.get("target"), dict) else {}
            t_type = target.get("type")
            t_type_val = t_type.value if hasattr(t_type, "value") else str(t_type or "")
            if (
                t_type_val in (ObjectType.CONFIRM_TOKEN.value, "confirm_token")
                and str(target.get("id") or "") == token_id
            ):
                rec_tenant = _extract_record_tenant(record)
                if clean_tenant is not None and rec_tenant != clean_tenant:
                    continue
                record_type = record.get("type")
                record_type_val = record_type.value if hasattr(record_type, "value") else str(record_type or "")

                if record_type_val == CommandType.CONFIRM_TOKEN_CREATE.value:
                    if token_tenant is None and rec_tenant:
                        token_tenant = rec_tenant
                    elif token_tenant is not None and rec_tenant and rec_tenant != token_tenant:
                        continue
                    status = "created"
                    expires_at = self.confirm_token_expiry_from_record(record)
                    latest_record = record
                    continue

                if token_tenant is not None and rec_tenant and rec_tenant != token_tenant:
                    continue
                if rec_tenant and token_tenant is None:
                    token_tenant = rec_tenant

                if record_type_val == CommandType.CONFIRM_TOKEN_REDEEM.value:
                    status = "redeemed"
                elif record_type_val == CommandType.CONFIRM_TOKEN_DELETE.value:
                    status = "deleted"
                latest_record = record
                continue

            if (
                status == "created"
                and self._guarded_command_confirm_token_id(record) == token_id
            ):
                rec_tenant = _extract_record_tenant(record)
                if clean_tenant is not None and rec_tenant != clean_tenant:
                    continue
                if token_tenant is not None and rec_tenant and rec_tenant != token_tenant:
                    continue
                if rec_tenant and token_tenant is None:
                    token_tenant = rec_tenant
                status = "redeemed"
                latest_record = record

        expired = False
        if expires_at is not None and status == "created":
            expired = expires_at <= datetime.now(timezone.utc)
            if expired:
                status = "expired"

        payload: Dict[str, Any] = {
            "id": token_id,
            "tokenId": token_id,
            "status": status,
            "expired": expired,
        }
        if token_tenant is not None:
            payload["tenant_id"] = token_tenant
            payload["tenantId"] = token_tenant
        if expires_at is not None:
            payload["expiresAt"] = expires_at.isoformat().replace("+00:00", "Z")
            payload["expires_at"] = payload["expiresAt"]
        if latest_record is not None:
            payload["commandId"] = latest_record.get("command_id")
            payload["command_id"] = latest_record.get("command_id")
        return payload

    def check_confirm_token_tenant_authorization(
        self,
        token_id: str,
        identity: OperatorIdentity,
        token_state: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        if token_state is None:
            token_state = self.confirm_token_lifecycle_payload(token_id)
        if token_state.get("status") == "available":
            return
        token_tenant = token_state.get("tenant_id")
        caller_tenant = getattr(identity, "tenant_id", None) or getattr(identity, "tenant", None)
        clean_caller_tenant = str(caller_tenant or "").strip() or None

        if token_tenant or clean_caller_tenant:
            if not clean_caller_tenant:
                raise self._raise_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Confirm token tenant missing",
                    f"Authenticated caller has no tenant bound and cannot confirm or redeem token {token_id!r}",
                    precondition_failed="tenant_missing",
                    suggestion="Authenticate with a valid tenant identity",
                    correlation_id=correlation_id,
                )
            if not token_tenant:
                raise self._raise_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Confirm token tenant unavailable",
                    f"Confirm token {token_id!r} has no bound tenant and cannot be confirmed or redeemed",
                    precondition_failed="tenant_missing",
                    suggestion="Use a confirm token issued within the caller's tenant scope",
                    correlation_id=correlation_id,
                )
            if clean_caller_tenant != token_tenant:
                raise self._raise_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Confirm token tenant mismatch",
                    f"Confirm token {token_id!r} is bound to tenant {token_tenant!r} and cannot be confirmed or redeemed by {clean_caller_tenant!r}",
                    precondition_failed="tenant_mismatch",
                    suggestion="Use a confirm token issued within the caller's tenant scope",
                    correlation_id=correlation_id,
                )

    def _project_command_confirmation_flat_response(
        self,
        record: Dict[str, Any],
        identity: OperatorIdentity,
    ) -> Dict[str, Any]:
        params = record.get("params") if isinstance(record.get("params"), dict) else {}
        res = record.get("result") if isinstance(record.get("result"), dict) else {}
        res_data = res.get("data") if isinstance(res.get("data"), dict) else {}

        confirmation_id = (
            params.get("confirmation_id")
            or res.get("confirmation_id")
            or res_data.get("confirmationId")
            or res_data.get("confirmation_id")
            or record.get("command_id")
        )
        cmd_id = (
            params.get("command_id")
            or res.get("command_id")
            or res_data.get("commandId")
            or res_data.get("command_id")
        )
        token = (
            params.get("confirm_token")
            or res.get("token")
            or res.get("tokenId")
            or res_data.get("tokenId")
            or res_data.get("token")
            or (record.get("target") or {}).get("id")
        )
        confirmed_at = (
            params.get("confirmed_at")
            or res.get("confirmed_at")
            or res_data.get("confirmed_at")
            or record.get("submitted_at")
            or self._utc_now()
        )
        confirmed_by = (
            params.get("confirmed_by")
            or res.get("confirmed_by")
            or (record.get("audit") or {}).get("actor")
            or getattr(identity, "operator_id", None)
            or "operator"
        )

        out = {
            "confirmation_id": confirmation_id,
            "command_id": cmd_id,
            "token": token,
            "tokenId": token,
            "status": "accepted",
            "lifecycleStatus": "redeemed",
            "redeemed": True,
            "confirmed_at": confirmed_at,
            "confirmed_by": confirmed_by,
        }
        if isinstance(res, dict) and "staleness_warning" in res:
            out["staleness_warning"] = res["staleness_warning"]
        return out

    def _project_command_confirmation_envelope_response(
        self,
        record: Dict[str, Any],
        identity: OperatorIdentity,
        correlation_id: str,
        x_request_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        params = record.get("params") if isinstance(record.get("params"), dict) else {}
        res = record.get("result") if isinstance(record.get("result"), dict) else {}
        res_data = res.get("data") if isinstance(res.get("data"), dict) else {}
        res_meta = res.get("meta") if isinstance(res.get("meta"), dict) else {}

        confirmation_id = (
            params.get("confirmation_id")
            or res_data.get("confirmationId")
            or res_data.get("confirmation_id")
            or res.get("confirmation_id")
            or record.get("command_id")
        )
        cmd_id = (
            params.get("command_id")
            or res_data.get("commandId")
            or res_data.get("command_id")
            or res.get("command_id")
        )
        token = (
            params.get("confirm_token")
            or res_data.get("tokenId")
            or res_data.get("token")
            or res.get("tokenId")
            or res.get("token")
            or (record.get("target") or {}).get("id")
        )
        confirmed_at = (
            params.get("confirmed_at")
            or res_data.get("confirmed_at")
            or res.get("confirmed_at")
            or record.get("submitted_at")
            or self._utc_now()
        )

        resp_correlation_id = (
            res_meta.get("correlationId")
            or res_meta.get("correlation_id")
            or (record.get("audit") or {}).get("correlation_id")
            or (record.get("foundation") or {}).get("receipt", {}).get("correlation_id")
            or correlation_id
            or params.get("idempotency_key")
            or record.get("command_id")
        )
        resp_request_id = (
            str(x_request_id or "").strip() or None
            if x_request_id is not None
            else res_meta.get("requestId")
        )

        return {
            "data": {
                "status": "accepted",
                "commandId": cmd_id,
                "confirmed_at": confirmed_at,
                "tokenId": token,
                "confirmationId": confirmation_id,
            },
            "meta": {
                "snapshot_at": confirmed_at,
                "dryRun": dry_run,
                "correlationId": resp_correlation_id,
                "requestId": resp_request_id,
                "evidenceKind": "command.confirm",
            },
        }

    def raise_if_confirm_token_expired(self, token_id: str) -> None:
        state = self.confirm_token_lifecycle_payload(token_id)
        if state.get("status") != "expired":
            return
        raise self._raise_error(
            410,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "Confirm token expired",
            f"Confirm token {token_id} expired before it could be used",
            precondition_failed="confirm_token_expired",
            suggestion="Issue a fresh confirm token and retry the guarded command",
            details_extra={"tokenId": token_id, "expiresAt": state.get("expiresAt")},
        )

    def latest_command_confirmation_payload(self, token_id: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        confirmation: Dict[str, Any] = {}
        for record in self.confirm_token_records(token_id, tenant_id=tenant_id):
            if record.get("type") != CommandType.CONFIRM_TOKEN_REDEEM.value:
                continue
            params = record.get("params") if isinstance(record.get("params"), dict) else {}
            confirmation = {
                "confirmation_id": params.get("confirmation_id"),
                "command_id": params.get("command_id") or record.get("command_id"),
                "confirmed_at": params.get("confirmed_at") or record.get("submitted_at"),
                "confirmed_by": params.get("confirmed_by"),
            }
        return {key: value for key, value in confirmation.items() if value is not None}

    def record_command_confirmation_redeem(
        self,
        *,
        token_id: str,
        command_id: str,
        confirmation_id: str,
        confirmed_at: str,
        identity: OperatorIdentity,
        idempotency_key: Optional[str] = None,
        request_hash: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        store = self.command_store
        if store is None:
            raise self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Command persistence is unavailable",
                "CommandStore is not configured; refusing to accept unpersisted confirmation",
                precondition_failed="command_store_unconfigured",
            )
        tenant_id = getattr(identity, "tenant_id", None) or getattr(identity, "tenant", None)
        clean_tenant_id = str(tenant_id or "").strip() or None
        caller_op_id = getattr(identity, "operator_id", None) or "operator"

        existing_record = None
        if idempotency_key:
            existing_record = store.get_command_by_idempotency_key(
                idempotency_key,
                operator_id=caller_op_id,
                tenant_id=clean_tenant_id,
            )
        if existing_record:
            self._revalidate_admitted_command_record(
                existing_record,
                resolved_key=idempotency_key,
                identity=identity,
                command_type=CommandType.CONFIRM_TOKEN_REDEEM,
                entity_type=ObjectType.CONFIRM_TOKEN,
                target_id=token_id,
                request_hash=request_hash,
                server_generated_target=False,
            )
            return existing_record

        generated_command_id = f"cmd-confirm-{uuid.uuid4().hex[:16]}"
        canonical_receipt = {
            "command_id": command_id,
            "commandId": command_id,
            "aggregate_type": ObjectType.CONFIRM_TOKEN.value,
            "aggregate_id": token_id,
            "aggregate_version": 1,
            "status": "executed",
            "event_id": f"evt-{generated_command_id}",
            "correlation_id": idempotency_key or command_id or generated_command_id,
            "owner": "governance",
            "committed_at": confirmed_at,
        }

        foundation_ctx = {
            "idempotency_record": {
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "status": "succeeded",
                "tenant_id": clean_tenant_id,
                "operator_id": caller_op_id,
            },
            "trace_context": {
                "tenant_ref": {"tenant_id": clean_tenant_id} if clean_tenant_id else {},
                "tenant_id": clean_tenant_id,
            },
            "receipt": dict(canonical_receipt),
        }
        audit_ctx = {
            "actor": caller_op_id,
            "tenant_id": clean_tenant_id,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "reason": "Command confirmation",
            "command_id": command_id,
            "confirmation_id": confirmation_id,
            "confirmed_at": confirmed_at,
            "confirmed_by": caller_op_id,
            "foundation": foundation_ctx,
        }
        params = {
            "confirm_token": token_id,
            "command_id": command_id,
            "confirmation_id": confirmation_id,
            "confirmed_at": confirmed_at,
            "confirmed_by": caller_op_id,
            "tenant_id": clean_tenant_id,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
        }

        durable_result: Dict[str, Any] = {
            **canonical_receipt,
            "confirmation_id": confirmation_id,
            "confirmationId": confirmation_id,
            "command_id": command_id,
            "commandId": command_id,
            "token": token_id,
            "tokenId": token_id,
            "status": "executed",
            "lifecycleStatus": "redeemed",
            "redeemed": True,
            "confirmed_at": confirmed_at,
            "confirmed_by": caller_op_id,
            "receipt": dict(canonical_receipt),
            "data": {
                "status": "accepted",
                "commandId": command_id,
                "command_id": command_id,
                "confirmed_at": confirmed_at,
                "tokenId": token_id,
                "token": token_id,
                "confirmationId": confirmation_id,
                "confirmation_id": confirmation_id,
                **canonical_receipt,
            },
        }
        if result and isinstance(result, dict):
            if "staleness_warning" in result:
                durable_result["staleness_warning"] = result["staleness_warning"]
            if "meta" in result:
                durable_result["meta"] = result["meta"]

        if hasattr(store, "submit_terminal_command"):
            admitted = store.submit_terminal_command(
                command_id=generated_command_id,
                command_type=CommandType.CONFIRM_TOKEN_REDEEM,
                target=TargetObject(type=ObjectType.CONFIRM_TOKEN, id=token_id),
                submitted_at=confirmed_at,
                params=params,
                audit_context=audit_ctx,
                foundation_context=foundation_ctx,
                result=durable_result,
            )
        else:
            admitted = store.submit_command(
                command_id=generated_command_id,
                command_type=CommandType.CONFIRM_TOKEN_REDEEM,
                target=TargetObject(type=ObjectType.CONFIRM_TOKEN, id=token_id),
                submitted_at=confirmed_at,
                params=params,
                audit_context=audit_ctx,
                foundation_context=foundation_ctx,
                result=durable_result,
            )
        is_replayed = (admitted.get("command_id") != generated_command_id)
        if is_replayed:
            self._revalidate_admitted_command_record(
                admitted,
                resolved_key=idempotency_key or "",
                identity=identity,
                command_type=CommandType.CONFIRM_TOKEN_REDEEM,
                entity_type=ObjectType.CONFIRM_TOKEN,
                target_id=token_id,
                request_hash=request_hash,
                server_generated_target=False,
            )
            return admitted

        if not hasattr(store, "submit_terminal_command") and admitted.get("command_id"):
            store.update_status(admitted["command_id"], CommandStatus.EXECUTED, result=durable_result)
        return admitted

    def sem_command_response(
        self,
        *,
        command_type: CommandType,
        target_type: ObjectType,
        target_id: str,
        payload: Optional[Dict[str, Any]],
        identity: OperatorIdentity,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
        status_code: int = 202,
        terminal_on_persist: bool = False,
        server_generated_target: bool = False,
        trusted_evidence_producer: Optional[str] = None,
    ) -> JSONResponse:
        store = self.command_store
        if store is None:
            raise self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Command persistence is unavailable",
                "CommandStore is not configured; refusing to accept unpersisted command",
                precondition_failed="command_store_unconfigured",
            )

        payload = dict(payload or {})
        _reject_body_idempotency_key(payload)
        clean_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        hash_body: Dict[str, Any] = {
            "command": command_type.value if hasattr(command_type, "value") else str(command_type),
            "target_type": target_type.value if hasattr(target_type, "value") else str(target_type),
            "payload": payload,
        }
        if not server_generated_target:
            hash_body["target_id"] = target_id
        request_hash = _stable_json_hash(hash_body)

        tenant_id = getattr(identity, "tenant_id", None) or getattr(identity, "tenant", None)
        clean_tenant_id = str(tenant_id or "").strip() or None
        caller_op_id = getattr(identity, "operator_id", None)
        expected_cmd = command_type.value if hasattr(command_type, "value") else str(command_type)
        expected_target_type = target_type.value if hasattr(target_type, "value") else str(target_type)

        existing_record = None
        if clean_key:
            existing_record = store.get_command_by_idempotency_key(
                clean_key,
                operator_id=caller_op_id,
                tenant_id=clean_tenant_id,
            )

        if existing_record is not None:
            self._revalidate_admitted_command_record(
                existing_record,
                resolved_key=clean_key,
                identity=identity,
                command_type=command_type,
                entity_type=target_type,
                target_id=target_id,
                request_hash=request_hash,
                server_generated_target=server_generated_target,
            )
            admitted_target = existing_record.get("target") or {}
            admitted_target_id = admitted_target.get("id") or target_id
            admitted_target_type = admitted_target.get("type") or expected_target_type
            admitted_command_id = existing_record.get("command_id")
            admitted_submitted_at = existing_record.get("submitted_at") or self._utc_now()
            owner_name = "deployment" if "deployment" in str(admitted_target_type).lower() else str(admitted_target_type)

            canonical_receipt = {
                "receipt_id": f"rcpt-{admitted_command_id}",
                "command_id": admitted_command_id,
                "commandId": admitted_command_id,
                "aggregate_type": admitted_target_type,
                "aggregate_id": admitted_target_id,
                "aggregate_version": 1,
                "status": "accepted",
                "event_id": f"evt-{admitted_command_id}",
                "correlation_id": clean_key or admitted_command_id,
                "owner": owner_name,
                "committed_at": admitted_submitted_at,
                "command": expected_cmd,
                "target": {"type": admitted_target_type, "id": admitted_target_id},
                "submitted_at": admitted_submitted_at,
                "accepted_at": admitted_submitted_at,
            }
            response_data = {
                "command_id": admitted_command_id,
                "status": "accepted",
                "data": {
                    "command_id": admitted_command_id,
                    "commandId": admitted_command_id,
                    "aggregate_type": admitted_target_type,
                    "aggregate_id": admitted_target_id,
                    "aggregate_version": 1,
                    "status": "accepted",
                    "event_id": f"evt-{admitted_command_id}",
                    "correlation_id": clean_key or admitted_command_id,
                    "owner": owner_name,
                    "committed_at": admitted_submitted_at,
                    "command": expected_cmd,
                    "target": {"type": admitted_target_type, "id": admitted_target_id},
                    "receipt": canonical_receipt,
                },
                "meta": {
                    "idempotency": {"idempotencyKey": clean_key, "replayed": True},
                    "snapshot_at": self._utc_now(),
                },
            }
            if not existing_record.get("result"):
                store.update_status(
                    admitted_command_id,
                    CommandStatus.SUBMITTED,
                    result=response_data,
                    expected_status=CommandStatus.SUBMITTED,
                )
            return JSONResponse(status_code=status_code, content=response_data)

        now = self._utc_now()
        command_id = f"cmd-{uuid.uuid4().hex[:16]}"
        foundation_ctx = {
            "idempotency_record": {
                "idempotency_key": clean_key,
                "request_hash": request_hash,
                "status": "succeeded",
                "tenant_id": clean_tenant_id,
                "operator_id": caller_op_id,
            }
        }
        if trusted_evidence_producer:
            foundation_ctx["trusted_evidence_producer"] = trusted_evidence_producer
        audit_ctx = {
            "actor": caller_op_id,
            "operator_id": caller_op_id,
            "tenant_id": clean_tenant_id,
            "command_id": command_id,
            "reason": str(payload.get("reason") or expected_cmd),
            "foundation": foundation_ctx,
            "idempotency_key": clean_key,
            "request_hash": request_hash,
        }
        if trusted_evidence_producer:
            audit_ctx["trusted_evidence_producer"] = trusted_evidence_producer
        if "live_capital_mutation" in payload:
            audit_ctx["live_capital_side_effects"] = bool(payload.get("live_capital_mutation"))

        target_obj = TargetObject(type=target_type, id=target_id)
        if terminal_on_persist and hasattr(store, "submit_terminal_command_if_no_active_target"):
            audit_ctx["execution_completed_at"] = now
            record, active = store.submit_terminal_command_if_no_active_target(
                command_id=command_id,
                command_type=command_type,
                target=target_obj,
                submitted_at=now,
                params=payload,
                audit_context=audit_ctx,
                foundation_context=foundation_ctx,
            )
        elif hasattr(store, "submit_command_if_no_active_target"):
            record, active = store.submit_command_if_no_active_target(
                command_id=command_id,
                command_type=command_type,
                target=target_obj,
                submitted_at=now,
                params=payload,
                audit_context=audit_ctx,
                foundation_context=foundation_ctx,
            )
        elif terminal_on_persist and hasattr(store, "submit_terminal_command"):
            audit_ctx["execution_completed_at"] = now
            record = store.submit_terminal_command(
                command_id=command_id,
                command_type=command_type,
                target=target_obj,
                submitted_at=now,
                params=payload,
                audit_context=audit_ctx,
                foundation_context=foundation_ctx,
            )
            active = None
        else:
            record = store.submit_command(
                command_id=command_id,
                command_type=command_type,
                target=target_obj,
                submitted_at=now,
                params=payload,
                audit_context=audit_ctx,
                foundation_context=foundation_ctx,
            )
            active = None

        if active:
            is_token = str(target_type).lower() in ("confirm_token", "objecttype.confirm_token")
            msg = "Confirm token already exists" if is_token else "A command is already in flight for this target"
            details = f"Confirm token {target_id!r} already exists" if is_token else f"Command {active['command_id']} is currently {active['status']}"
            suggestion = "Use a different token ID or omit tokenId to let the system generate a unique token" if is_token else "Wait for the in-flight command to complete or time out before retrying"
            raise self._raise_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                msg,
                details,
                precondition_failed="concurrent_safety",
                suggestion=suggestion,
            )

        assert record is not None
        is_replayed = False
        if record.get("command_id") != command_id:
            is_replayed = True
            self._revalidate_admitted_command_record(
                record,
                resolved_key=clean_key,
                identity=identity,
                command_type=command_type,
                entity_type=target_type,
                target_id=target_id,
                request_hash=request_hash,
                server_generated_target=server_generated_target,
            )

        admitted_command_id = record["command_id"]
        admitted_target = record.get("target") or {}
        admitted_target_id = admitted_target.get("id") or target_id
        admitted_target_type = admitted_target.get("type") or expected_target_type
        admitted_submitted_at = record.get("submitted_at") or now
        owner_name = "deployment" if "deployment" in str(admitted_target_type).lower() else str(admitted_target_type)

        canonical_receipt = {
            "receipt_id": f"rcpt-{admitted_command_id}",
            "command_id": admitted_command_id,
            "commandId": admitted_command_id,
            "aggregate_type": admitted_target_type,
            "aggregate_id": admitted_target_id,
            "aggregate_version": 1,
            "status": "accepted",
            "event_id": f"evt-{admitted_command_id}",
            "correlation_id": clean_key or admitted_command_id,
            "owner": owner_name,
            "committed_at": admitted_submitted_at,
            "command": expected_cmd,
            "target": {"type": admitted_target_type, "id": admitted_target_id},
            "submitted_at": admitted_submitted_at,
            "accepted_at": admitted_submitted_at,
        }
        result_content = {
            "command_id": admitted_command_id,
            "status": "accepted",
            "data": {
                "command_id": admitted_command_id,
                "commandId": admitted_command_id,
                "aggregate_type": admitted_target_type,
                "aggregate_id": admitted_target_id,
                "aggregate_version": 1,
                "status": "accepted",
                "event_id": f"evt-{admitted_command_id}",
                "correlation_id": clean_key or admitted_command_id,
                "owner": owner_name,
                "committed_at": admitted_submitted_at,
                "command": expected_cmd,
                "target": {"type": admitted_target_type, "id": admitted_target_id},
                "receipt": canonical_receipt,
            },
            "meta": {
                "idempotency": {"idempotencyKey": clean_key, "replayed": is_replayed},
                "snapshot_at": now,
            },
        }
        if not is_replayed or not record.get("result"):
            store.update_status(
                admitted_command_id,
                CommandStatus.EXECUTED if terminal_on_persist else CommandStatus.SUBMITTED,
                result=result_content,
                expected_status=CommandStatus.SUBMITTED if not terminal_on_persist else None,
            )
        return JSONResponse(status_code=status_code, content=result_content)

    def _revalidate_admitted_command_record(
        self,
        record: Dict[str, Any],
        *,
        resolved_key: str,
        identity: OperatorIdentity,
        command_type: Any,
        entity_type: Any,
        target_id: str,
        request_hash: str,
        server_generated_target: bool = False,
    ) -> None:
        foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
        idem_rec = foundation.get("idempotency_record") if isinstance(foundation.get("idempotency_record"), dict) else {}
        audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}

        saved_hash = idem_rec.get("request_hash") or audit.get("request_hash")
        if saved_hash and saved_hash != request_hash:
            raise self._raise_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )

        saved_tenant = idem_rec.get("tenant_id") or audit.get("tenant_id") or (record.get("params") or {}).get("tenant_id")
        caller_tenant = getattr(identity, "tenant_id", None) or getattr(identity, "tenant", None)
        if saved_tenant != caller_tenant:
            raise self._raise_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used by a different tenant",
                f"Key {resolved_key!r} is bound to a different tenant",
                precondition_failed="tenant_mismatch",
            )

        saved_op = idem_rec.get("operator_id") or audit.get("operator_id") or (record.get("params") or {}).get("operator_id")
        if saved_op and saved_op != identity.operator_id:
            raise self._raise_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used by a different operator",
                f"Key {resolved_key!r} is bound to a different operator",
                precondition_failed="operator_mismatch",
            )

        saved_cmd_type = record.get("type") or idem_rec.get("command_type")
        expected_cmd_type = command_type.value if hasattr(command_type, "value") else str(command_type)
        if saved_cmd_type and saved_cmd_type != expected_cmd_type:
            raise self._raise_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different command",
                f"Key {resolved_key!r} is bound to a different command type",
                precondition_failed="command_mismatch",
            )

        saved_target = record.get("target") if isinstance(record.get("target"), dict) else {}
        saved_target_type = saved_target.get("type")
        saved_target_id = saved_target.get("id")
        expected_target_type = entity_type.value if hasattr(entity_type, "value") else str(entity_type)
        if saved_target_type and saved_target_type != expected_target_type:
            raise self._raise_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different target type",
                f"Key {resolved_key!r} is bound to target type {saved_target_type!r}",
                precondition_failed="target_type_mismatch",
            )
        if not server_generated_target and saved_target_id and str(saved_target_id) != str(target_id):
            raise self._raise_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different target",
                f"Key {resolved_key!r} is bound to target {saved_target_id!r}",
                precondition_failed="target_id_mismatch",
            )

    def _revalidate_governance_admitted_record(
        self,
        record: Dict[str, Any],
        *,
        resolved_key: str,
        identity: OperatorIdentity,
        command_type: Any,
        entity_type: Any,
        target_id: str,
        request_hash: str,
        server_generated_target: bool = False,
    ) -> None:
        return self._revalidate_admitted_command_record(
            record,
            resolved_key=resolved_key,
            identity=identity,
            command_type=command_type,
            entity_type=entity_type,
            target_id=target_id,
            request_hash=request_hash,
            server_generated_target=server_generated_target,
        )

    def _populate_governance_receipt_fields(
        self,
        res_dict: Dict[str, Any],
        *,
        command_id: str,
        entity_type: Any,
        target_id: str,
        action_id: str,
        idempotency_key: str,
        submitted_at: str,
    ) -> None:
        agg_type = entity_type.value if hasattr(entity_type, "value") else str(entity_type)
        receipt_fields = {
            "command_id": command_id,
            "commandId": command_id,
            "aggregate_type": agg_type,
            "aggregate_id": target_id,
            "aggregate_version": 1,
            "status": "accepted",
            "event_id": f"evt-{command_id}",
            "correlation_id": idempotency_key or command_id,
            "owner": "governance",
            "committed_at": submitted_at,
        }
        if isinstance(res_dict, dict):
            if isinstance(res_dict.get("data"), dict):
                res_dict["data"].setdefault("action", action_id)
                for k, v in receipt_fields.items():
                    res_dict["data"][k] = v
                if isinstance(res_dict["data"].get("receipt"), dict):
                    for k, v in receipt_fields.items():
                        res_dict["data"]["receipt"][k] = v
                else:
                    res_dict["data"]["receipt"] = dict(receipt_fields)
            for k, v in receipt_fields.items():
                res_dict.setdefault(k, v)

    def submit_governance_action(
        self,
        *,
        action_kind: str,
        target_id: str,
        action_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """Single owner of governance command-admission normalization,
        idempotency, concurrency-safety, and receipt projection.

        Called by ``GovernanceService.submit_governance_action`` (the only
        caller) with exactly these keyword arguments; owns the
        action_kind/action_id -> ObjectType/CommandType mapping so it is not
        forked between the composition root and tests.
        """
        _reject_body_idempotency_key(payload)
        entity_type = resolve_governance_object_type(action_kind)
        command_type = resolve_governance_command_type(action_kind, action_id)
        resolved_key = str(idempotency_key or "").strip()
        request_hash = _stable_json_hash(
            {"action_kind": action_kind, "target_id": target_id, "action_id": action_id, "payload": payload}
        )

        if _truthy_header(payload.get("dryRun") or payload.get("dry_run")) or _truthy_header(os.getenv("BFF_REQUEST_DRY_RUN")):
            submitted_at = self._utc_now()
            result = project_final_command_response(
                command_id=f"dryrun-cmd-{uuid.uuid4().hex[:12]}",
                command=command_type,
                accepted_at=submitted_at,
                status=CommandStatus.SUBMITTED,
                staleness_warning=self.check_read_surface_state(),
                meta=command_response_dry_run_meta(resolved_key),
            )
            return result.model_dump(mode="json") if hasattr(result, "model_dump") else result

        store = self.command_store
        if store is None:
            raise self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Command persistence is unavailable",
                "CommandStore is not configured; refusing to accept unpersisted command",
                precondition_failed="command_store_unconfigured",
            )

        existing_cmd = None
        if resolved_key:
            existing_cmd = store.get_command_by_idempotency_key(
                resolved_key,
                operator_id=identity.operator_id,
                tenant_id=getattr(identity, "tenant_id", None),
            )
        if existing_cmd is not None:
            self._revalidate_governance_admitted_record(
                existing_cmd,
                resolved_key=resolved_key,
                identity=identity,
                command_type=command_type,
                entity_type=entity_type,
                target_id=target_id,
                request_hash=request_hash,
            )
            if existing_cmd.get("result"):
                return existing_cmd["result"]
            admitted_command_id = existing_cmd["command_id"]
            admitted_submitted_at = existing_cmd.get("submitted_at") or self._utc_now()
            result = project_final_command_response(
                command_id=admitted_command_id,
                command=command_type,
                accepted_at=admitted_submitted_at,
                status=CommandStatus.SUBMITTED,
                staleness_warning=self.check_read_surface_state(),
            )
            res_dict = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
            self._populate_governance_receipt_fields(
                res_dict,
                command_id=admitted_command_id,
                entity_type=entity_type,
                target_id=target_id,
                action_id=action_id,
                idempotency_key=resolved_key,
                submitted_at=admitted_submitted_at,
            )
            store.update_status(
                admitted_command_id,
                CommandStatus.SUBMITTED,
                result=res_dict,
                expected_status=CommandStatus.SUBMITTED,
            )
            return res_dict

        staleness_warning = self.check_read_surface_state()
        command_id = str(uuid.uuid4())
        submitted_at = self._utc_now()
        target = TargetObject(type=entity_type, id=target_id)
        # Concurrent conflicting decisions on the *same* approval target must
        # not both be admitted (see the decide-conflict contract tests); a
        # review target, by contrast, legitimately receives a sequence of
        # distinct in-flight commands (submit, then an action) with no
        # worker in this seam marking the prior one terminal, so only the
        # approval action_kind uses the active-target admission guard.
        preconditions_checked = ["authentication", "authorization", "idempotency"]
        tenant_val = getattr(identity, "tenant_id", None) or getattr(identity, "tenant", None)
        audit_record = {
            "operator_id": identity.operator_id,
            "roles_at_submission": list(getattr(identity, "roles", []) or []),
            "action_kind": action_kind,
            "action_id": action_id,
            "timestamp": submitted_at,
            "idempotency_key": resolved_key,
            "request_hash": request_hash,
            "tenant_id": tenant_val,
        }
        foundation_record = {
            "idempotency_record": {
                "idempotency_key": resolved_key,
                "request_hash": request_hash,
                "operator_id": identity.operator_id,
                "tenant_id": tenant_val,
                "command_type": command_type.value if hasattr(command_type, "value") else str(command_type),
            }
        }
        if action_kind == "approval":
            preconditions_checked.append("concurrent_safety")
            audit_record["preconditions_checked"] = preconditions_checked
            record, active = store.submit_command_if_no_active_target(
                command_id=command_id,
                command_type=command_type,
                target=target,
                submitted_at=submitted_at,
                params={"action_id": action_id, "tenant_id": tenant_val, **payload},
                audit_context=audit_record,
                foundation_context=foundation_record,
            )
            if active is not None:
                raise self._raise_error(
                    409,
                    ErrorCode.RESOURCE_CONFLICT,
                    "A command is already in flight for this target",
                    f"Command {active['command_id']} is currently {active['status']}",
                    precondition_failed="concurrent_safety",
                    suggestion="Wait for the in-flight command to complete or time out before retrying",
                )
        else:
            audit_record["preconditions_checked"] = preconditions_checked
            record = store.submit_command(
                command_id=command_id,
                command_type=command_type,
                target=target,
                submitted_at=submitted_at,
                params={"action_id": action_id, "tenant_id": tenant_val, **payload},
                audit_context=audit_record,
                foundation_context=foundation_record,
            )
        assert record is not None

        if record.get("command_id") != command_id:
            self._revalidate_governance_admitted_record(
                record,
                resolved_key=resolved_key,
                identity=identity,
                command_type=command_type,
                entity_type=entity_type,
                target_id=target_id,
                request_hash=request_hash,
            )
            if record.get("result"):
                return record["result"]

        admitted_command_id = record["command_id"]
        admitted_submitted_at = record.get("submitted_at") or submitted_at

        result = project_final_command_response(
            command_id=admitted_command_id,
            command=command_type,
            accepted_at=admitted_submitted_at,
            status=CommandStatus.SUBMITTED,
            staleness_warning=staleness_warning,
        )
        res_dict = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        self._populate_governance_receipt_fields(
            res_dict,
            command_id=admitted_command_id,
            entity_type=entity_type,
            target_id=target_id,
            action_id=action_id,
            idempotency_key=resolved_key,
            submitted_at=admitted_submitted_at,
        )
        store.update_status(
            admitted_command_id,
            CommandStatus.SUBMITTED,
            result=res_dict,
            expected_status=CommandStatus.SUBMITTED,
        )
        return res_dict

    def create_confirm_token(
        self,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
    ) -> JSONResponse:
        self.check_read_role(identity)
        client_provided_id = str(payload.get("tokenId") or payload.get("token_id") or "").strip()
        token_id = client_provided_id or f"ct-{uuid.uuid4().hex[:12]}"
        server_generated = not bool(client_provided_id)
        hash_payload = dict(payload)
        if server_generated:
            hash_payload.pop("tokenId", None)
            hash_payload.pop("token_id", None)
        else:
            hash_payload["tokenId"] = token_id
            token_state = self.confirm_token_lifecycle_payload(token_id)
            if token_state.get("status") != "available":
                clean_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
                caller_tenant = getattr(identity, "tenant_id", None) or getattr(identity, "tenant", None)
                clean_tenant = str(caller_tenant or "").strip() or None
                store = self.command_store
                is_replay = False
                if clean_key and store is not None:
                    stored_replay = store.get_command_by_idempotency_key(
                        clean_key,
                        operator_id=getattr(identity, "operator_id", None),
                        tenant_id=clean_tenant,
                    )
                    if stored_replay is not None:
                        is_replay = True
                if not is_replay:
                    raise self._raise_error(
                        409,
                        ErrorCode.RESOURCE_CONFLICT,
                        "Confirm token already exists",
                        f"Confirm token {token_id!r} already exists and cannot be created again",
                        precondition_failed="concurrent_safety",
                        suggestion="Use a different token ID or omit tokenId to let the system generate a unique token",
                    )

        response = self.sem_command_response(
            command_type=CommandType.CONFIRM_TOKEN_CREATE,
            target_type=ObjectType.CONFIRM_TOKEN,
            target_id=token_id,
            payload=hash_payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            status_code=201,
            server_generated_target=server_generated,
            terminal_on_persist=True,
        )
        content = json.loads(response.body.decode("utf-8"))
        final_token_id = token_id
        if server_generated and content.get("meta", {}).get("idempotency", {}).get("replayed"):
            admitted_target_id = (content.get("data") or {}).get("target", {}).get("id")
            if admitted_target_id:
                final_token_id = str(admitted_target_id)
            else:
                clean_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
                store = self.command_store
                if store is not None:
                    stored = store.get_command_by_idempotency_key(
                        clean_key,
                        operator_id=getattr(identity, "operator_id", None),
                        tenant_id=getattr(identity, "tenant_id", None),
                    )
                    if stored:
                        final_token_id = str(stored.get("target", {}).get("id") or token_id)
        content["data"]["tokenId"] = final_token_id
        content["data"]["id"] = final_token_id
        content["data"]["status"] = "created"
        caller_tenant = getattr(identity, "tenant_id", None) or getattr(identity, "tenant", None)
        clean_tenant = str(caller_tenant or "").strip() or None
        if clean_tenant:
            content["data"]["tenant_id"] = clean_tenant
            content["data"]["tenantId"] = clean_tenant
        return JSONResponse(status_code=201, content=content)

    def get_confirm_token(self, token_id: str, identity: OperatorIdentity) -> Dict[str, Any]:
        self.check_read_role(identity)
        token_state = self.confirm_token_lifecycle_payload(token_id)
        self.check_confirm_token_tenant_authorization(token_id, identity, token_state=token_state)
        self.raise_if_confirm_token_expired(token_id)
        return {
            "data": token_state,
            "meta": {"contract": "BFF-LUV-SEM-002", "snapshot_at": self._utc_now()},
        }

    def redeem_confirm_token(
        self,
        token_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
    ) -> JSONResponse:
        self.check_read_role(identity)
        token_state = self.confirm_token_lifecycle_payload(token_id)
        self.check_confirm_token_tenant_authorization(token_id, identity, token_state=token_state)
        self.raise_if_confirm_token_expired(token_id)
        response = self.sem_command_response(
            command_type=CommandType.CONFIRM_TOKEN_REDEEM,
            target_type=ObjectType.CONFIRM_TOKEN,
            target_id=token_id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            terminal_on_persist=True,
        )
        content = json.loads(response.body.decode("utf-8"))
        data = content.setdefault("data", {})
        data["id"] = token_id
        data["tokenId"] = token_id
        data["status"] = "redeemed"
        data["redeemed"] = True
        return JSONResponse(status_code=202, content=content)

    def delete_confirm_token(
        self,
        token_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
    ) -> JSONResponse:
        self.check_read_role(identity)
        token_state = self.confirm_token_lifecycle_payload(token_id)
        self.check_confirm_token_tenant_authorization(token_id, identity, token_state=token_state)
        response = self.sem_command_response(
            command_type=CommandType.CONFIRM_TOKEN_DELETE,
            target_type=ObjectType.CONFIRM_TOKEN,
            target_id=token_id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            terminal_on_persist=True,
        )
        content = json.loads(response.body.decode("utf-8"))
        data = content.setdefault("data", {})
        data["id"] = token_id
        data["tokenId"] = token_id
        data["status"] = "deleted"
        data["deleted"] = True
        return JSONResponse(status_code=202, content=content)

    def submit_command_confirmation(
        self,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.check_operator_role(identity)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        _reject_body_idempotency_key(payload)

        confirm_token = str(
            payload.get("confirm_token")
            or payload.get("confirmToken")
            or payload.get("tokenId")
            or payload.get("token")
            or ""
        ).strip()
        if not confirm_token:
            raise self._raise_error(
                400,
                ErrorCode.CONFIRMATION_REQUIRED,
                "confirm_token is required",
                "Command confirmation requires a non-empty confirm_token in the request body",
                precondition_failed="confirm_token_missing",
                suggestion="Include the confirm_token issued by the original precondition error response",
            )

        original_command_id = str(payload.get("command_id") or "").strip()
        if not original_command_id:
            raise self._raise_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "command_id is required",
                "Command confirmation requires the original command_id being confirmed",
                precondition_failed="command_id_missing",
                suggestion="Include the command_id from the original command submission",
            )

        store = self.command_store
        if store is None:
            raise self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Command persistence is unavailable",
                "CommandStore is not configured; refusing to accept unpersisted confirmation",
                precondition_failed="command_store_unconfigured",
            )

        req_hash = _stable_json_hash({"command_id": original_command_id, "confirm_token": confirm_token})
        tenant_id = getattr(identity, "tenant_id", None) or getattr(identity, "tenant", None)
        clean_tenant_id = str(tenant_id or "").strip() or None
        caller_op_id = getattr(identity, "operator_id", None) or "operator"

        existing = None
        if resolved_key:
            existing = store.get_command_by_idempotency_key(
                resolved_key,
                operator_id=caller_op_id,
                tenant_id=clean_tenant_id,
            )

        if existing is not None:
            self._revalidate_admitted_command_record(
                existing,
                resolved_key=resolved_key or "",
                identity=identity,
                command_type=CommandType.CONFIRM_TOKEN_REDEEM,
                entity_type=ObjectType.CONFIRM_TOKEN,
                target_id=confirm_token,
                request_hash=req_hash,
                server_generated_target=False,
            )
            return self._project_command_confirmation_flat_response(existing, identity=identity)

        token_state = self.confirm_token_lifecycle_payload(confirm_token)
        self.check_confirm_token_tenant_authorization(confirm_token, identity, token_state=token_state)
        self.raise_if_confirm_token_expired(confirm_token)
        staleness_warning = self.check_read_surface_state()
        confirmation_id = str(uuid.uuid4())
        confirmed_at = self._utc_now()
        initial_result: Dict[str, Any] = {}
        if staleness_warning is not None:
            initial_result["staleness_warning"] = {
                "read_surface_state": staleness_warning.read_surface_state,
                "message": staleness_warning.message,
            }
        admitted = self.record_command_confirmation_redeem(
            token_id=confirm_token,
            command_id=original_command_id,
            confirmation_id=confirmation_id,
            confirmed_at=confirmed_at,
            identity=identity,
            idempotency_key=resolved_key,
            request_hash=req_hash,
            result=initial_result,
        )
        return self._project_command_confirmation_flat_response(
            admitted or {
                "params": {
                    "confirmation_id": confirmation_id,
                    "command_id": original_command_id,
                    "confirm_token": confirm_token,
                    "confirmed_at": confirmed_at,
                },
                "result": initial_result,
            },
            identity=identity,
        )

    def get_command_confirmation_status(self, token: str, identity: OperatorIdentity) -> Dict[str, Any]:
        self.check_read_role(identity)
        token_state = self.confirm_token_lifecycle_payload(token)
        self.check_confirm_token_tenant_authorization(token, identity, token_state=token_state)
        self.raise_if_confirm_token_expired(token)
        confirmation = self.latest_command_confirmation_payload(token)
        return {
            "data": {
                **confirmation,
                "token": token,
                "tokenId": token,
                "status": token_state["status"],
                "lifecycleStatus": token_state["status"],
                "redeemed": token_state["status"] == "redeemed",
                "deleted": token_state["status"] == "deleted",
            },
            "meta": {
                "contract": "BFF-B1-009",
                "snapshot_at": self._utc_now(),
            },
        }

    def confirm_command_by_token(
        self,
        token: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
        x_correlation_id: Optional[str] = None,
        x_request_id: Optional[str] = None,
        x_dry_run: Optional[str] = None,
        response: Optional[Response] = None,
    ) -> Any:
        self.check_operator_role(identity)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        correlation_id = str(x_correlation_id or "").strip() or str(uuid.uuid4())
        if response is not None:
            response.headers["X-Correlation-Id"] = correlation_id

        payload = dict(payload or {})
        _reject_body_idempotency_key(payload)

        body_confirm_token = str(
            payload.get("confirm_token") or payload.get("confirmToken") or ""
        ).strip()
        if body_confirm_token and body_confirm_token != token:
            raise self._raise_error(
                412,
                ErrorCode.PRECONDITION_FAILED,
                "confirm_token in body does not match the token in the path",
                f"Body confirm_token {body_confirm_token!r} does not match path token {token!r}",
                precondition_failed="confirm_token_invalid",
                suggestion="Ensure confirm_token in the request body matches the {token} path parameter",
                correlation_id=correlation_id,
            )

        command_id = str(payload.get("command_id") or payload.get("commandId") or "").strip()
        if not command_id:
            raise self._raise_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "command_id is required",
                "Command confirmation requires the original command_id being confirmed",
                precondition_failed="command_id_missing",
                suggestion="Include the command_id from the original command submission",
                correlation_id=correlation_id,
            )

        token_state = self.confirm_token_lifecycle_payload(token)
        if token_state.get("status") == "available":
            raise self._raise_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Confirm token not found",
                f"Confirm token {token!r} has not been issued",
                precondition_failed="confirm_token_not_found",
                suggestion="Ensure the token was issued for a guarded command and has not been redeemed",
                correlation_id=correlation_id,
            )

        self.check_confirm_token_tenant_authorization(token, identity, token_state=token_state)
        self.raise_if_confirm_token_expired(token)

        dry_run = _truthy_header(x_dry_run)
        snapshot_at = self._utc_now()
        confirmation_id = str(uuid.uuid4())

        if dry_run:
            return JSONResponse(
                status_code=200,
                content={
                    "data": {
                        "status": "accepted",
                        "commandId": command_id,
                        "confirmed_at": snapshot_at,
                        "tokenId": token,
                    },
                    "meta": {
                        "snapshot_at": snapshot_at,
                        "dryRun": True,
                        "correlationId": correlation_id,
                        "requestId": str(x_request_id or "").strip() or None,
                        "evidenceKind": "command.confirm",
                    },
                },
                headers={"X-Correlation-Id": correlation_id},
            )

        store = self.command_store
        if store is None:
            raise self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Command persistence is unavailable",
                "CommandStore is not configured; refusing to accept unpersisted confirmation",
                precondition_failed="command_store_unconfigured",
                correlation_id=correlation_id,
            )

        req_hash = _stable_json_hash({"command_id": command_id, "confirm_token": token})
        tenant_id = getattr(identity, "tenant_id", None) or getattr(identity, "tenant", None)
        clean_tenant_id = str(tenant_id or "").strip() or None
        caller_op_id = getattr(identity, "operator_id", None) or "operator"

        existing = None
        if resolved_key:
            existing = store.get_command_by_idempotency_key(
                resolved_key,
                operator_id=caller_op_id,
                tenant_id=clean_tenant_id,
            )

        if existing is not None:
            self._revalidate_admitted_command_record(
                existing,
                resolved_key=resolved_key or "",
                identity=identity,
                command_type=CommandType.CONFIRM_TOKEN_REDEEM,
                entity_type=ObjectType.CONFIRM_TOKEN,
                target_id=token,
                request_hash=req_hash,
                server_generated_target=False,
            )
            out = self._project_command_confirmation_envelope_response(
                existing,
                identity=identity,
                correlation_id=correlation_id,
                x_request_id=x_request_id,
                dry_run=False,
            )
            if response is not None and "meta" in out and "correlationId" in out["meta"]:
                response.headers["X-Correlation-Id"] = out["meta"]["correlationId"]
            return out

        result = {
            "data": {
                "status": "accepted",
                "commandId": command_id,
                "confirmed_at": snapshot_at,
                "tokenId": token,
                "confirmationId": confirmation_id,
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "dryRun": False,
                "correlationId": correlation_id,
                "requestId": str(x_request_id or "").strip() or None,
                "evidenceKind": "command.confirm",
            },
        }

        admitted = self.record_command_confirmation_redeem(
            token_id=token,
            command_id=command_id,
            confirmation_id=confirmation_id,
            confirmed_at=snapshot_at,
            identity=identity,
            idempotency_key=resolved_key,
            request_hash=req_hash,
            result=result,
        )
        is_replayed = False
        if admitted:
            admitted_params = admitted.get("params") or {}
            admitted_conf_id = admitted_params.get("confirmation_id")
            if admitted_conf_id and admitted_conf_id != confirmation_id:
                is_replayed = True
                result["data"]["confirmationId"] = admitted_conf_id

        if not is_replayed and self._publish_event is not None:
            self._publish_event(
                "command.confirm",
                {
                    "commandId": command_id,
                    "tokenId": token,
                    "confirmationId": result["data"]["confirmationId"],
                    "confirmed_at": snapshot_at,
                    "actor": identity.operator_id,
                },
            )

        envelope_record = admitted or {
            "params": {
                "confirmation_id": result["data"]["confirmationId"],
                "command_id": command_id,
                "confirm_token": token,
                "confirmed_at": snapshot_at,
            },
            "result": result,
        }
        out = self._project_command_confirmation_envelope_response(
            envelope_record,
            identity=identity,
            correlation_id=correlation_id,
            x_request_id=x_request_id,
            dry_run=False,
        )
        if response is not None and "meta" in out and "correlationId" in out["meta"]:
            response.headers["X-Correlation-Id"] = out["meta"]["correlationId"]
        return out

    def submit_command_admission(
        self,
        *,
        background_tasks: Any,
        payload: Dict[str, Any],
        authorization: Optional[str],
        x_mfa_token: Optional[str] = None,
        x_trace_id: Optional[str] = None,
        x_correlation_id: Optional[str] = None,
        x_request_id: Optional[str] = None,
        x_confirm_token: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
        route: str = _FINAL_COMMAND_ROUTE,
        source_route: Optional[str] = None,
        foundation_raw_payload: Optional[Dict[str, Any]] = None,
        audit_extra: Optional[Dict[str, Any]] = None,
        extra_precondition: Optional[Callable[[OperatorIdentity, OperatorCommand], None]] = None,
        enqueue: bool = True,
        include_durable_meta: bool = False,
        response_deprecation: Optional[Dict[str, Any]] = None,
    ) -> Any:
        identity = self.extract_identity(authorization, mfa_token=x_mfa_token)
        cmd = normalize_operator_command_payload(payload)

        candidate_key = str(idempotency_key or x_idempotency_key or "").strip() or None
        foundation_context = build_foundation_command_context(
            cmd=cmd,
            identity=identity,
            raw_payload=(
                foundation_raw_payload
                if foundation_raw_payload is not None
                else payload
            ),
            trace_id=x_trace_id,
            correlation_id=x_correlation_id,
            request_id=x_request_id,
            idempotency_key=candidate_key,
            route=route,
            source_route=source_route,
        )

        try:
            resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
            reject_body_idempotency_key(payload)
            reject_server_managed_rebalance_evidence_command(cmd)
            if extra_precondition is not None:
                extra_precondition(identity, cmd)
            validate_audit_context(cmd)
            validate_capital_authority_target_binding(cmd)
            validate_paper_runtime_authority_target_binding(cmd)
            ensure_live_broker_scope_allowed(cmd, payload)
            validate_drawer_runtime_target(cmd)
            validate_final_command_target_type(cmd)
            validator = self._validators.get(cmd.command)
            if validator:
                validator(cmd.params, identity)
        except HTTPException as exc:
            raise foundation_bff_error(exc, foundation_context=foundation_context) from exc

        store = self.command_store
        if store is None:
            raise self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Command store is unavailable",
                "CommandStore is not configured",
                precondition_failed="command_store_unconfigured",
            )

        caller_tenant = getattr(identity, "tenant_id", None) or getattr(identity, "tenant", None)
        clean_caller_tenant = str(caller_tenant or "").strip() or None
        token_id_param = str(x_confirm_token or payload.get("confirm_token") or payload.get("confirmToken") or "").strip()
        if token_id_param:
            try:
                self.check_confirm_token_tenant_authorization(token_id_param, identity)
            except HTTPException as exc:
                raise foundation_bff_error(exc, foundation_context=foundation_context) from exc

        duplicate = store.get_command_by_idempotency_key(
            foundation_context["idempotency_record"].idempotency_key,
            operator_id=identity.operator_id,
            tenant_id=clean_caller_tenant,
        )
        if duplicate:
            duplicate_record = (duplicate.get("foundation") or {}).get("idempotency_record") or {}
            if duplicate_record.get("request_hash") != foundation_context["idempotency_record"].request_hash:
                raise foundation_idempotency_conflict_error(
                    foundation_context=foundation_context,
                    existing_command_id=str(duplicate.get("command_id") or ""),
                )
            assert_duplicate_confirm_token_matches(
                duplicate=duplicate,
                cmd=cmd,
                payload=payload,
                confirm_token=x_confirm_token,
                foundation_context=foundation_context,
            )
            duplicate_status = CommandStatus(
                duplicate.get("status") or CommandStatus.SUBMITTED.value
            )
            if enqueue and retryable_terminal_capital_command(duplicate):
                store.update_status(
                    str(duplicate["command_id"]),
                    CommandStatus.SUBMITTED,
                    audit={"retry_requested_at": self._utc_now()},
                )
                if self._process_command_task and background_tasks and hasattr(background_tasks, "add_task"):
                    background_tasks.add_task(
                        _execute_command_background_task, self._process_command_task, str(duplicate["command_id"])
                    )
                duplicate_status = CommandStatus.SUBMITTED
            return project_final_command_response(
                command_id=duplicate["command_id"],
                command=cmd.command,
                accepted_at=duplicate.get("submitted_at") or self._utc_now(),
                status=duplicate_status,
                staleness_warning=None,
                meta=command_response_durable_meta(resolved_key, replayed=True)
                if include_durable_meta
                else None,
                deprecation=response_deprecation,
            )

        try:
            precondition_evidence = require_final_command_preconditions(
                cmd=cmd,
                payload=payload,
                confirm_token=x_confirm_token,
                identity=identity,
                correlation_id=foundation_context["trace_context"].correlation_id,
                confirm_token_records_fn=lambda tid: self.confirm_token_records(tid, tenant_id=clean_caller_tenant),
                confirm_token_lifecycle_fn=lambda tid: self.confirm_token_lifecycle_payload(tid, tenant_id=clean_caller_tenant),
                read_store=self.read_store,
                command_store=store,
            )
        except HTTPException as exc:
            raise foundation_bff_error(exc, foundation_context=foundation_context) from exc

        stored_params = stored_command_params(cmd, identity, payload)
        stored_params["idempotency_key"] = resolved_key
        stored_params["request_hash"] = foundation_context["idempotency_record"].request_hash
        if clean_caller_tenant:
            stored_params["tenant_id"] = clean_caller_tenant
        canonicalize_validated_precondition_evidence(
            stored_params,
            precondition_evidence,
        )

        staleness_warning = self.check_read_surface_state()
        if _truthy_header(payload.get("dryRun") or payload.get("dry_run")) or _truthy_header(os.getenv("BFF_REQUEST_DRY_RUN")):
            command_envelope = foundation_context["command_envelope"]
            return project_final_command_response(
                command_id=command_envelope.command_id,
                command=cmd.command,
                accepted_at=self._utc_now(),
                status=CommandStatus.SUBMITTED,
                staleness_warning=staleness_warning,
                meta=command_response_dry_run_meta(resolved_key),
                deprecation=response_deprecation,
            )

        command_envelope = foundation_context["command_envelope"]
        idempotency_record = foundation_context["idempotency_record"]
        idempotency_record = idempotency_record.with_status(
            "succeeded",
            result_ref=f"command:{command_envelope.command_id}",
        )
        foundation_context["idempotency_record"] = idempotency_record
        command_id = command_envelope.command_id
        submitted_at = self._utc_now()
        receipt_dual_write = command_dual_write_receipts(
            command_id=command_id,
            command=cmd.command.value,
            status=ActionCommandStatus.ACCEPTED.value,
            accepted_at=submitted_at,
        )

        auth_context = command_runtime_auth_context(
            command_id=command_id,
            authorization=authorization,
            mfa_token=x_mfa_token,
            identity=identity,
            auth_context_sink=_COMMAND_AUTH_CONTEXT,
        )

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
            "auth": auth_context,
            "foundation": serialize_foundation_context(foundation_context),
            "receipt_dual_write": receipt_dual_write,
        }
        if clean_caller_tenant:
            audit_record["tenant_id"] = clean_caller_tenant
        if resolved_key:
            audit_record["idempotency_key"] = resolved_key
        if precondition_evidence:
            audit_record["precondition_evidence"] = precondition_evidence
        if audit_extra:
            audit_record.update({key: value for key, value in audit_extra.items() if value is not None})

        serialized_foundation = serialize_foundation_context(foundation_context)
        with store.serialized_transaction():
            duplicate_after_precheck = store.get_command_by_idempotency_key(
                resolved_key,
                operator_id=identity.operator_id,
                tenant_id=clean_caller_tenant,
            )
            if duplicate_after_precheck:
                duplicate_record = (
                    (duplicate_after_precheck.get("foundation") or {})
                    .get("idempotency_record")
                    or {}
                )
                if duplicate_record.get("request_hash") != foundation_context["idempotency_record"].request_hash:
                    raise foundation_idempotency_conflict_error(
                        foundation_context=foundation_context,
                        existing_command_id=str(duplicate_after_precheck.get("command_id") or ""),
                    )
                assert_duplicate_confirm_token_matches(
                    duplicate=duplicate_after_precheck,
                    cmd=cmd,
                    payload=payload,
                    confirm_token=x_confirm_token,
                    foundation_context=foundation_context,
                )
                return project_final_command_response(
                    command_id=duplicate_after_precheck["command_id"],
                    command=cmd.command,
                    accepted_at=duplicate_after_precheck.get("submitted_at") or self._utc_now(),
                    status=CommandStatus(
                        duplicate_after_precheck.get("status")
                        or CommandStatus.SUBMITTED.value
                    ),
                    staleness_warning=None,
                    meta=command_response_durable_meta(resolved_key, replayed=True)
                    if include_durable_meta
                    else None,
                    deprecation=response_deprecation,
                )

            token_id = str(precondition_evidence.get("confirm_token_id") or "").strip()
            if token_id:
                token_state = self.confirm_token_lifecycle_payload(token_id)
                try:
                    self.check_confirm_token_tenant_authorization(token_id, identity, token_state=token_state)
                except HTTPException as exc:
                    raise foundation_bff_error(exc, foundation_context=foundation_context) from exc
                if token_state.get("status") != "created":
                    error = self._raise_error(
                        428,
                        ErrorCode.CONFIRMATION_REQUIRED,
                        "Confirmation token is not valid for this command",
                        "Confirmation token has already been consumed or is invalid",
                        precondition_failed="confirm_token",
                        suggestion="Issue a fresh confirm token bound to this command, target, and operator",
                        details_extra={"confirmToken": token_id, "tokenStatus": token_state.get("status")},
                    )
                    raise foundation_bff_error(error, foundation_context=foundation_context)
                confirmation_id = f"auto-confirm-{command_id}"
                confirmation_request = {
                    "confirm_token": token_id,
                    "command_id": command_id,
                    "confirmation_id": confirmation_id,
                    "confirmed_by": identity.operator_id,
                }
                record, active_after_precheck = store.submit_command_with_confirm_token_redeem_if_no_active_target(
                    command_id=command_id,
                    command_type=cmd.command,
                    target=cmd.target,
                    submitted_at=submitted_at,
                    params=stored_params,
                    audit_context=audit_record,
                    foundation_context=serialized_foundation,
                    confirm_token_id=token_id,
                    confirmation_id=confirmation_id,
                    confirmation_command_id=f"cmd-{uuid.uuid4().hex[:16]}",
                    confirmation_idempotency_key=f"auto-confirm:{command_id}",
                    confirmation_request_hash=stable_json_hash(confirmation_request),
                    operator_id=identity.operator_id,
                )
            else:
                record, active_after_precheck = store.submit_command_if_no_active_target(
                    command_id=command_id,
                    command_type=cmd.command,
                    target=cmd.target,
                    submitted_at=submitted_at,
                    params=stored_params,
                    audit_context=audit_record,
                    foundation_context=serialized_foundation,
                )
        if active_after_precheck:
            error = self._raise_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                "A command is already in flight for this target",
                f"Command {active_after_precheck['command_id']} is currently {active_after_precheck['status']}",
                precondition_failed="concurrent_safety",
                suggestion="Wait for the in-flight command to complete or time out before retrying",
            )
            raise foundation_bff_error(error, foundation_context=foundation_context)
        assert record is not None

        log.info(
            "Accepted final-contract command %s (%s) for %s:%s by operator %s",
            command_id, cmd.command.value, cmd.target.type.value, cmd.target.id, identity.operator_id,
        )

        if enqueue and self._process_command_task and background_tasks and hasattr(background_tasks, "add_task"):
            background_tasks.add_task(_execute_command_background_task, self._process_command_task, command_id)

        return project_final_command_response(
            command_id=command_id,
            command=cmd.command,
            accepted_at=submitted_at,
            status=CommandStatus.SUBMITTED,
            staleness_warning=staleness_warning,
            meta=command_response_durable_meta(resolved_key, replayed=False)
            if include_durable_meta
            else None,
            deprecation=response_deprecation,
        )

    def submit_final_command(
        self,
        background_tasks: Any,
        payload: Dict[str, Any],
        authorization: Optional[str] = None,
        x_mfa_token: Optional[str] = None,
        x_trace_id: Optional[str] = None,
        x_correlation_id: Optional[str] = None,
        x_request_id: Optional[str] = None,
        x_confirm_token: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
    ) -> Any:
        return self._submit_command_admission(
            background_tasks=background_tasks,
            payload=payload,
            authorization=authorization,
            x_mfa_token=x_mfa_token,
            x_trace_id=x_trace_id,
            x_correlation_id=x_correlation_id,
            x_request_id=x_request_id,
            x_confirm_token=x_confirm_token,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            route="POST /bff/v1/commands",
            include_durable_meta=True,
        )


def _runtime_command_context(
    runtime_id: str,
    incident_id: Optional[str] = None,
    *,
    read_store: Optional[Any] = None,
) -> Dict[str, Optional[str]]:
    effective_store = read_store
    if effective_store is None:
        bff_main = sys.modules.get("services.control_plane.bff.main")
        if bff_main is not None:
            effective_store = getattr(bff_main, "read_store", None)
    runtime_binding = (
        effective_store.get_runtime_binding_by_runtime_id(runtime_id)
        if effective_store and hasattr(effective_store, "get_runtime_binding_by_runtime_id")
        else None
    )
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

    if incident_id and effective_store and hasattr(effective_store, "get_incident"):
        incident = effective_store.get_incident(incident_id)
        if incident and str(incident.get("runtime_id") or "") == runtime_id:
            capital_pool_id = capital_pool_id or incident.get("capital_pool_id")
            artifact_id = artifact_id or incident.get("artifact_id")
            artifact_version = artifact_version or incident.get("artifact_version")

    return {
        "runtime_id": runtime_id,
        "runtime_binding_id": binding_id,
        "capital_pool_id": capital_pool_id,
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
        "plan_id": plan_id,
    }


def _derive_drawer_execution_params(
    command: CommandType,
    runtime_id: str,
    params: Dict[str, Any],
    *,
    actor_id: Optional[str],
    reason: Optional[str],
    incident_id: Optional[str],
    read_store: Optional[Any] = None,
) -> Dict[str, Any]:
    context = _runtime_command_context(runtime_id, incident_id, read_store=read_store)
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
            "pause_new_entries": params.get("pause_new_entries"),
            "cancel_open_orders": params.get("cancel_open_orders"),
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
            "reduce_exposure_pct": params.get("reduce_exposure_pct"),
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
            "rollback_to_version": params.get("target_artifact_id"),
            "rollback_action_type": "pause_then_replace",
            "target_artifact_id": params.get("target_artifact_id"),
        }

    if not context["capital_pool_id"]:
        raise ValueError(
            f"Runtime {runtime_id} cannot be routed to a capital pool."
        )
    return {
        **base,
        "safe_mode_level": params.get("safe_mode_level"),
        "target_state": "guarded",
    }


def _resolve_execution_params_for_record(
    record: Dict[str, Any],
    *,
    read_store: Optional[Any] = None,
) -> Dict[str, Any]:
    command_type = CommandType(record["type"])
    params = dict(record.get("params") or {})
    target = record.get("target") or {}
    target_id = str(target.get("id") or "").strip() if isinstance(target, dict) else str(getattr(target, "id", "") or "").strip()
    target_type = str(target.get("type") or "").strip() if isinstance(target, dict) else str(getattr(target, "type", "") or "").strip()
    audit = record.get("audit") or {}
    foundation = record.get("foundation") or {}
    idempotency = foundation.get("idempotency_record") or {}

    # Authoritative identities from record
    tenant_id = audit.get("tenant_id") or idempotency.get("tenant_id") or params.get("tenant_id")
    actor_id = audit.get("operator_id") or audit.get("actor") or idempotency.get("operator_id") or params.get("actor_id") or params.get("operator_id")
    idempotency_key = audit.get("idempotency_key") or idempotency.get("idempotency_key") or params.get("idempotency_key")
    request_hash = audit.get("request_hash") or idempotency.get("request_hash") or params.get("request_hash")
    command_id = record.get("command_id")

    if target_id:
        # Reject mismatched body identities before mutation
        body_exp_id = str(params.get("experiment_id") or "").strip()
        if body_exp_id and body_exp_id != target_id:
            raise ValueError(f"Body experiment_id {body_exp_id!r} does not match validated target {target_id!r}")
        body_ent_id = str(params.get("entity_id") or "").strip()
        if body_ent_id and body_ent_id != target_id:
            raise ValueError(f"Body entity_id {body_ent_id!r} does not match validated target {target_id!r}")

        params["entity_id"] = target_id
        if target_type.lower() in ("experiment", "researchexperiment", "research-experiment") or command_type == CommandType.EXPERIMENT_ACTION or "experiment" in command_type.value.lower():
            params["experiment_id"] = target_id

    if tenant_id:
        params["tenant_id"] = tenant_id
    if actor_id:
        params["actor_id"] = actor_id
        params["operator_id"] = actor_id
    if idempotency_key:
        params["idempotency_key"] = idempotency_key
    if request_hash:
        params["request_hash"] = request_hash
    if command_id:
        params["command_id"] = command_id

    if command_type not in _DRAWER_RUNTIME_COMMANDS:
        if command_type in {CommandType.PAUSE_PAPER_RUNTIME, CommandType.RESUME_PAPER_RUNTIME}:
            params.update(entity_type="Runtime", action_id=command_type.value, actionId=command_type.value)
            rt_id = target_id
            params.pop("verified_binding", None)
            params.pop("verified_binding_id", None)
            params.pop("verified_runtime_binding_id", None)
            if rt_id:
                params["runtime_id"] = rt_id
                params["entity_id"] = rt_id
                params.pop("runtimeId", None)
                params.pop("entityId", None)
        return params

    runtime_id = target_id
    if not runtime_id:
        raise ValueError(f"{command_type.value} is missing target.id.")

    return _derive_drawer_execution_params(
        command_type,
        runtime_id,
        params,
        actor_id=audit.get("operator_id"),
        reason=audit.get("reason"),
        incident_id=audit.get("incident_id"),
        read_store=read_store,
    )


async def process_command(
    command_id: str,
    *,
    command_store: Optional[Any] = None,
    read_store: Optional[Any] = None,
    resolve_execution_params: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> None:
    """Async command processor that dispatches to the Protected Internal API.
    Records authoritative status, result, and audit data for every execution.
    """
    store = command_store
    if store is None:
        bff_main = sys.modules.get("services.control_plane.bff.main")
        if bff_main is not None:
            store = getattr(bff_main, "command_store", None)
    if store is None:
        log.error("Worker: command store unavailable for command %s", command_id)
        return

    record = store.get_command(command_id)
    if not record:
        log.error("Worker: command %s not found in store", command_id)
        return

    command_type = CommandType(record["type"])
    audit = record.get("audit", {})

    runtime_auth = pop_command_auth_context(command_id)
    auth_token = runtime_auth.get("auth_token") or audit.get("auth_token")
    mfa_token = runtime_auth.get("mfa_token") or audit.get("mfa_token")

    await asyncio.sleep(0.05)
    store.update_status(command_id, CommandStatus.PROCESSING)

    resolver = resolve_execution_params or (lambda rec: _resolve_execution_params_for_record(rec, read_store=read_store))
    try:
        execution_params = resolver(record)
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
        store.update_status(
            command_id,
            CommandStatus.FAILED,
            error=error,
            audit=audit,
        )
        log.warning("Worker: command %s failed during routing resolution: %s", command_id, exc)
        return

    from ..command_executor import execute_command_with_status
    status, result, error = execute_command_with_status(
        command_id, command_type, execution_params,
        auth_token=auth_token, mfa_token=mfa_token,
    )

    audit["execution_completed_at"] = result.get("execution_completed_at") if result else error.get("failed_at") if error else None
    audit["executor"] = "command_executor"
    if result:
        audit["downstream_verified"] = bool(
            result.get("downstream_verified")
            or result.get("authoritative_capital_readback")
            or result.get("dispatch_path") != "bff_action_adapter"
        )
    if error:
        audit["failure_reason"] = error.get("message", "")
        audit["failure_suggestion"] = error.get("suggestion", "")

    store.update_status(
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


_process_command_stub = process_command

_GOV_STORE_UNSET = object()


def _gov_bff_action_command(
    entity_type: Any,
    entity_id: str,
    action_id: str,
    resolved_key: str,
    identity: Any,
    payload: Dict[str, Any],
    command_type: Any,
    *,
    command_store: Any = _GOV_STORE_UNSET,
) -> Dict[str, Any]:
    """Submit a governance/risk/research resource action through the command store."""
    payload = dict(payload or {})
    _reject_body_idempotency_key(payload)

    if isinstance(entity_type, str):
        try:
            entity_type_obj = ObjectType(entity_type)
        except ValueError:
            entity_type_obj = entity_type
    else:
        entity_type_obj = entity_type
    ent_type_str = entity_type_obj.value if hasattr(entity_type_obj, "value") else str(entity_type_obj)

    if isinstance(command_type, str):
        try:
            command_type_obj = CommandType(command_type)
        except ValueError:
            command_type_obj = command_type
    else:
        command_type_obj = command_type
    cmd_type_str = command_type_obj.value if hasattr(command_type_obj, "value") else str(command_type_obj)

    request_hash = _stable_json_hash(
        {"entity_type": ent_type_str, "entity_id": entity_id, "action_id": action_id, "payload": payload}
    )

    dry_run = False
    try:
        from ..assistant.management_service import _request_dry_run_requested
        dry_run = _request_dry_run_requested()
    except Exception:
        pass

    if dry_run:
        submitted_at = utc_now()
        command_id = f"dryrun-cmd-{uuid.uuid4().hex[:12]}"
        owner_name = "ResearchWriteOwner" if ent_type_str.lower() in ("experiment", "researchexperiment") else ent_type_str
        canonical_receipt = {
            "receipt_id": f"rcpt-{command_id}",
            "command_id": command_id,
            "commandId": command_id,
            "aggregate_type": ent_type_str,
            "aggregate_id": entity_id,
            "aggregate_version": 1,
            "status": "accepted",
            "event_id": f"evt-{command_id}",
            "correlation_id": resolved_key or command_id,
            "owner": owner_name,
            "committed_at": submitted_at,
            "command": cmd_type_str,
            "target": {"type": ent_type_str, "id": entity_id},
            "submitted_at": submitted_at,
            "accepted_at": submitted_at,
        }
        return {
            "status": "accepted",
            "data": {
                "command_id": command_id,
                "commandId": command_id,
                "aggregate_type": ent_type_str,
                "aggregate_id": entity_id,
                "aggregate_version": 1,
                "status": "accepted",
                "event_id": canonical_receipt["event_id"],
                "correlation_id": canonical_receipt["correlation_id"],
                "owner": owner_name,
                "committed_at": submitted_at,
                "command": cmd_type_str,
                "target": {"type": ent_type_str, "id": entity_id},
                "receipt": canonical_receipt,
            },
            "meta": {
                "dryRun": True,
                "durable": False,
                "liveCapitalSideEffects": False,
                "idempotency": {
                    "key": resolved_key,
                    "idempotencyKey": resolved_key,
                    "replayed": False,
                },
            },
        }

    if command_store is _GOV_STORE_UNSET:
        store = None
        bff_main = sys.modules.get("services.control_plane.bff.main")
        if bff_main is not None:
            store = getattr(bff_main, "command_store", None)
    else:
        store = command_store

    if store is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": ErrorCode.DEPENDENCY_UNAVAILABLE.value if hasattr(ErrorCode.DEPENDENCY_UNAVAILABLE, "value") else "DEPENDENCY_UNAVAILABLE",
                    "message": "Command store is unavailable",
                    "details": {
                        "message": "CommandStore is not configured",
                        "precondition_failed": "command_store_unconfigured",
                    },
                }
            },
        )

    op_id = str(getattr(identity, "operator_id", None) or getattr(identity, "actor", None) or "").strip() or "unknown"
    ten_id = str(getattr(identity, "tenant_id", None) or "").strip() or None
    owner_name = "ResearchWriteOwner" if ent_type_str.lower() in ("experiment", "researchexperiment") else ent_type_str

    durable = None
    if resolved_key:
        durable = store.get_command_by_idempotency_key(
            resolved_key,
            operator_id=op_id,
            tenant_id=ten_id,
        )

    if durable is not None:
        durable_idempotency = (durable.get("foundation") or {}).get("idempotency_record") or {}
        durable_audit = durable.get("audit") or {}
        stored_hash = (
            durable_idempotency.get("request_hash")
            or durable_audit.get("request_hash")
            or (durable.get("params") or {}).get("request_hash")
        )
        if stored_hash and stored_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": ErrorCode.IDEMPOTENCY_CONFLICT.value if hasattr(ErrorCode.IDEMPOTENCY_CONFLICT, "value") else "IDEMPOTENCY_CONFLICT",
                        "message": "Idempotency key was already used with a different payload",
                        "details": {
                            "message": f"Key {resolved_key!r} is bound to command {durable.get('command_id')}",
                            "precondition_failed": "idempotency_conflict",
                            "suggestion": "Use a new Idempotency-Key or resubmit the original payload unchanged",
                        },
                    }
                },
            )
        admitted_command_id = str(durable["command_id"])
        admitted_submitted_at = str(durable.get("submitted_at") or utc_now())
        existing_result = durable.get("result")
        if isinstance(existing_result, dict) and isinstance(existing_result.get("receipt"), dict):
            canonical_receipt = dict(existing_result["receipt"])
        else:
            canonical_receipt = {
                "receipt_id": f"rcpt-{admitted_command_id}",
                "command_id": admitted_command_id,
                "commandId": admitted_command_id,
                "aggregate_type": ent_type_str,
                "aggregate_id": entity_id,
                "aggregate_version": 1,
                "status": "accepted",
                "event_id": f"evt-{admitted_command_id}",
                "correlation_id": resolved_key or admitted_command_id,
                "owner": owner_name,
                "committed_at": admitted_submitted_at,
                "command": cmd_type_str,
                "target": {"type": ent_type_str, "id": entity_id},
                "submitted_at": admitted_submitted_at,
                "accepted_at": admitted_submitted_at,
            }

        tracking_url = f"/api/v1/operator/commands/{admitted_command_id}"
        replay_data = {
            "command_id": admitted_command_id,
            "commandId": admitted_command_id,
            "aggregate_type": ent_type_str,
            "aggregate_id": entity_id,
            "aggregate_version": 1,
            "status": "accepted",
            "event_id": canonical_receipt["event_id"],
            "correlation_id": canonical_receipt["correlation_id"],
            "owner": owner_name,
            "committed_at": admitted_submitted_at,
            "command": cmd_type_str,
            "target": {"type": ent_type_str, "id": entity_id},
            "tracking_url": tracking_url,
            "trackingUrl": tracking_url,
            "receipt": canonical_receipt,
            "action_receipt": canonical_receipt,
            "command_receipt": canonical_receipt,
        }
        if isinstance(existing_result, dict) and isinstance(existing_result.get("data"), dict):
            replay_data.update(existing_result["data"])
            replay_data["receipt"] = canonical_receipt

        return {
            "status": "accepted",
            "data": replay_data,
            "meta": {
                "durable": True,
                "liveCapitalSideEffects": False,
                "idempotency": {
                    "key": resolved_key,
                    "idempotencyKey": resolved_key,
                    "replayed": True,
                },
                "snapshot_at": admitted_submitted_at,
            },
        }

    command_id = str(uuid.uuid4())
    submitted_at = utc_now()
    target_obj = TargetObject(type=entity_type_obj, id=entity_id) if hasattr(TargetObject, "type") else {"type": ent_type_str, "id": entity_id}

    canonical_receipt = {
        "receipt_id": f"rcpt-{command_id}",
        "command_id": command_id,
        "commandId": command_id,
        "aggregate_type": ent_type_str,
        "aggregate_id": entity_id,
        "aggregate_version": 1,
        "status": "accepted",
        "event_id": f"evt-{command_id}",
        "correlation_id": resolved_key or command_id,
        "owner": owner_name,
        "committed_at": submitted_at,
        "command": cmd_type_str,
        "target": {"type": ent_type_str, "id": entity_id},
        "submitted_at": submitted_at,
        "accepted_at": submitted_at,
    }

    tracking_url = f"/api/v1/operator/commands/{command_id}"
    data_payload = {
        "command_id": command_id,
        "commandId": command_id,
        "aggregate_type": ent_type_str,
        "aggregate_id": entity_id,
        "aggregate_version": 1,
        "status": "accepted",
        "event_id": canonical_receipt["event_id"],
        "correlation_id": canonical_receipt["correlation_id"],
        "owner": owner_name,
        "committed_at": submitted_at,
        "command": cmd_type_str,
        "target": {"type": ent_type_str, "id": entity_id},
        "tracking_url": tracking_url,
        "trackingUrl": tracking_url,
        "receipt": canonical_receipt,
        "action_receipt": canonical_receipt,
        "command_receipt": canonical_receipt,
    }

    durable_result = {
        **canonical_receipt,
        "status": "accepted",
        "data": data_payload,
        "receipt": canonical_receipt,
    }

    foundation_ctx = {
        "idempotency_record": {
            "idempotency_key": resolved_key,
            "request_hash": request_hash,
            "status": "succeeded",
            "tenant_id": ten_id,
            "operator_id": op_id,
            "operation_type": f"bff.{cmd_type_str}",
            "target_ref": f"{ent_type_str}:{entity_id}",
            "trace_id": command_id,
        }
    }
    audit_record = {
        "operator_id": op_id,
        "actor": op_id,
        "tenant_id": ten_id,
        "roles_at_submission": getattr(identity, "roles", []),
        "action_id": action_id,
        "preconditions_checked": ["authentication", "authorization", "idempotency"],
        "timestamp": submitted_at,
        "idempotency_key": resolved_key,
        "request_hash": request_hash,
        "command_id": command_id,
        "foundation": foundation_ctx,
    }

    admitted_params = dict(payload)
    admitted_params.update({
        "action_id": action_id,
        "entity_type": ent_type_str,
        "target_id": entity_id,
        "tenant_id": ten_id,
        "actor_id": op_id,
        "operator_id": op_id,
        "idempotency_key": resolved_key,
        "request_hash": request_hash,
        "command_id": command_id,
    })

    record = store.submit_command(
        command_id=command_id,
        command_type=command_type_obj,
        target=target_obj,
        submitted_at=submitted_at,
        params=admitted_params,
        audit_context=audit_record,
        foundation_context=foundation_ctx,
        result=durable_result,
    )

    admitted_command_id = str((record or {}).get("command_id") or command_id)
    is_replayed = admitted_command_id != command_id
    if is_replayed:
        durable_idempotency = ((record or {}).get("foundation") or {}).get("idempotency_record") or {}
        durable_audit = (record or {}).get("audit") or {}
        stored_hash = (
            durable_idempotency.get("request_hash")
            or durable_audit.get("request_hash")
            or ((record or {}).get("params") or {}).get("request_hash")
        )
        if stored_hash and stored_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": ErrorCode.IDEMPOTENCY_CONFLICT.value if hasattr(ErrorCode.IDEMPOTENCY_CONFLICT, "value") else "IDEMPOTENCY_CONFLICT",
                        "message": "Idempotency key was already used with a different payload",
                        "details": {
                            "message": f"Key {resolved_key!r} is bound to command {admitted_command_id}",
                            "precondition_failed": "idempotency_conflict",
                            "suggestion": "Use a new Idempotency-Key or resubmit the original payload unchanged",
                        },
                    }
                },
            )
        data_payload["command_id"] = admitted_command_id
        data_payload["commandId"] = admitted_command_id
        canonical_receipt["command_id"] = admitted_command_id
        canonical_receipt["commandId"] = admitted_command_id

    return {
        "status": "accepted",
        "data": data_payload,
        "meta": {
            "durable": True,
            "liveCapitalSideEffects": False,
            "idempotency": {
                "key": resolved_key,
                "idempotencyKey": resolved_key,
                "replayed": is_replayed,
            },
            "snapshot_at": submitted_at,
        },
    }


