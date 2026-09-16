"""Command Adapters domain service and orchestration helpers.

This module encapsulates command admission, validation, confirmation token
lifecycles, action catalog resolution, and domain command dispatch while
remaining completely decoupled from ``bff.main``.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
import os
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

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
    build_foundation_command_context,
    normalize_operator_command_payload,
    resolve_final_idempotency_key,
    serialize_foundation_context,
    stable_json_hash,
)
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
        self._publish_event = publish_event
        self._check_read_surface_state = check_read_surface_state
        self._validators = validators or {}
        self._process_command_task = process_command_task
        self._submit_command_admission = submit_command_admission or self.submit_command_admission

        self._final_contract_idempotency: Dict[str, Dict[str, Any]] = (
            final_contract_idempotency if final_contract_idempotency is not None else {}
        )
        self._gov_bff_idempotency: Dict[str, Dict[str, Any]] = (
            gov_bff_idempotency if gov_bff_idempotency is not None else {}
        )

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

    def confirm_token_records(self, token_id: str) -> List[Dict[str, Any]]:
        store = self.command_store
        if store is None:
            return []
        commands = getattr(store, "_get_all_commands", lambda: [])()
        return [
            record
            for record in commands
            if isinstance(record.get("target"), dict)
            and record["target"].get("type") == ObjectType.CONFIRM_TOKEN.value
            and str(record["target"].get("id") or "") == token_id
        ]

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

    def confirm_token_lifecycle_payload(self, token_id: str) -> Dict[str, Any]:
        status = "available"
        expires_at: Optional[datetime] = None
        latest_record: Optional[Dict[str, Any]] = None
        store = self.command_store
        commands = getattr(store, "_get_all_commands", lambda: [])() if store is not None else []

        for record in commands:
            target = record.get("target") if isinstance(record.get("target"), dict) else {}
            if (
                target.get("type") == ObjectType.CONFIRM_TOKEN.value
                and str(target.get("id") or "") == token_id
            ):
                record_type = record.get("type")
                if record_type == CommandType.CONFIRM_TOKEN_CREATE.value:
                    status = "created"
                    expires_at = self.confirm_token_expiry_from_record(record)
                elif record_type == CommandType.CONFIRM_TOKEN_REDEEM.value:
                    status = "redeemed"
                elif record_type == CommandType.CONFIRM_TOKEN_DELETE.value:
                    status = "deleted"
                latest_record = record
                continue

            if (
                status == "created"
                and self._guarded_command_confirm_token_id(record) == token_id
            ):
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
        if expires_at is not None:
            payload["expiresAt"] = expires_at.isoformat().replace("+00:00", "Z")
            payload["expires_at"] = payload["expiresAt"]
        if latest_record is not None:
            payload["commandId"] = latest_record.get("command_id")
            payload["command_id"] = latest_record.get("command_id")
        return payload

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

    def latest_command_confirmation_payload(self, token_id: str) -> Dict[str, Any]:
        confirmation: Dict[str, Any] = {}
        for record in self.confirm_token_records(token_id):
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
        idempotency_key: str,
        request_hash: str,
    ) -> None:
        store = self.command_store
        if store is None:
            return
        existing_record = store.get_command_by_idempotency_key(
            idempotency_key,
            operator_id=identity.operator_id,
        )
        if existing_record:
            stored_hash = (
                (existing_record.get("foundation") or {})
                .get("idempotency_record", {})
                .get("request_hash")
            )
            if stored_hash and stored_hash != request_hash:
                raise self._raise_error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key already used with a different payload",
                    f"Key {idempotency_key!r} is bound to a different confirmation request",
                    precondition_failed="idempotency_conflict",
                    suggestion="Use a new Idempotency-Key or resubmit the original confirmation unchanged",
                )
            return

        foundation_ctx = {
            "idempotency_record": {
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "status": "succeeded",
            }
        }
        store.submit_command(
            command_id=f"cmd-{uuid.uuid4().hex[:16]}",
            command_type=CommandType.CONFIRM_TOKEN_REDEEM,
            target=TargetObject(type=ObjectType.CONFIRM_TOKEN, id=token_id),
            submitted_at=confirmed_at,
            params={
                "confirm_token": token_id,
                "command_id": command_id,
                "confirmation_id": confirmation_id,
                "confirmed_at": confirmed_at,
                "confirmed_by": identity.operator_id,
            },
            audit_context={
                "actor": identity.operator_id,
                "reason": "Command confirmation",
                "command_id": command_id,
                "confirmation_id": confirmation_id,
                "confirmed_at": confirmed_at,
                "confirmed_by": identity.operator_id,
                "foundation": foundation_ctx,
            },
            foundation_context=foundation_ctx,
        )

    def sem_command_response(
        self,
        *,
        command_type: CommandType,
        target_type: ObjectType,
        target_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str] = None,
        status_code: int = 202,
        server_generated_target: bool = False,
        terminal_on_persist: bool = False,
        trusted_evidence_producer: Optional[str] = None,
    ) -> JSONResponse:
        payload = dict(payload or {})
        _reject_body_idempotency_key(payload)
        clean_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        hash_body: Dict[str, Any] = {
            "command": command_type.value,
            "target_type": target_type.value,
            "payload": payload,
        }
        if not server_generated_target:
            hash_body["target_id"] = target_id
        request_hash = _stable_json_hash(hash_body)
        cache_key = f"{identity.operator_id}\x00{clean_key}"

        existing = self._final_contract_idempotency.get(cache_key)
        if existing:
            if existing.get("request_hash") != request_hash:
                raise self._raise_error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key was reused with a different command payload",
                    "The idempotency key already belongs to another command payload",
                    precondition_failed="idempotency_key",
                )
            replay = dict(existing["result"])
            replay.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
            return JSONResponse(status_code=status_code, content=replay)

        store = self.command_store
        if store is not None:
            existing_record = store.get_command_by_idempotency_key(
                clean_key,
                operator_id=identity.operator_id,
            )
            if existing_record:
                stored_hash = (existing_record.get("foundation") or {}).get("idempotency_record", {}).get("request_hash")
                if stored_hash and stored_hash != request_hash:
                    raise self._raise_error(
                        409,
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "Idempotency key was reused with a different command payload",
                        "The idempotency key already belongs to another command payload",
                        precondition_failed="idempotency_key",
                    )
                now = self._utc_now()
                response_data = {
                    "command_id": existing_record.get("command_id"),
                    "status": "accepted",
                    "data": {
                        "command_id": existing_record.get("command_id"),
                        "commandId": existing_record.get("command_id"),
                        "command": command_type.value,
                        "target": {"type": target_type.value, "id": target_id},
                        "receipt": {
                            "receipt_id": f"rcpt-{existing_record.get('command_id', '')}",
                            "status": "accepted",
                            "command": command_type.value,
                            "target": {"type": target_type.value, "id": target_id},
                            "submitted_at": existing_record.get("submitted_at") or now,
                            "accepted_at": now,
                        },
                    },
                    "meta": {
                        "idempotency": {"idempotencyKey": clean_key, "replayed": True},
                        "snapshot_at": now,
                    },
                }
                return JSONResponse(status_code=status_code, content=response_data)

        now = self._utc_now()
        command_id = f"cmd-{uuid.uuid4().hex[:16]}"
        receipt = {
            "receipt_id": f"rcpt-{command_id}",
            "status": "accepted",
            "command": command_type.value,
            "target": {"type": target_type.value, "id": target_id},
            "submitted_at": now,
            "accepted_at": now,
        }
        foundation_ctx = {
            "idempotency_record": {
                "idempotency_key": clean_key,
                "request_hash": request_hash,
                "status": "succeeded",
            }
        }
        if trusted_evidence_producer:
            foundation_ctx["trusted_evidence_producer"] = trusted_evidence_producer
        audit_ctx = {
            "actor": identity.operator_id,
            "operator_id": identity.operator_id,
            "command_id": command_id,
            "reason": str(payload.get("reason") or command_type.value),
            "foundation": foundation_ctx,
        }
        if trusted_evidence_producer:
            audit_ctx["trusted_evidence_producer"] = trusted_evidence_producer

        if store is None:
            raise self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Command persistence is unavailable",
                "CommandStore is not configured; refusing to accept unpersisted command",
                precondition_failed="command_store_unconfigured",
            )

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
            raise self._raise_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "A command is already in flight for this target",
                f"Command {active['command_id']} is currently {active['status']}",
                precondition_failed="concurrent_safety",
                suggestion="Wait for the in-flight command to complete or time out before retrying",
            )

        result_content = {
            "command_id": command_id,
            "status": "accepted",
            "data": {
                "command_id": command_id,
                "commandId": command_id,
                "command": command_type.value,
                "target": {"type": target_type.value, "id": target_id},
                "receipt": receipt,
            },
            "meta": {
                "idempotency": {"idempotencyKey": clean_key, "replayed": False},
                "snapshot_at": now,
            },
        }
        self._final_contract_idempotency[cache_key] = {"request_hash": request_hash, "result": result_content}
        return JSONResponse(status_code=status_code, content=result_content)

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

        existing = self._gov_bff_idempotency.get(resolved_key)
        if existing is not None:
            if existing.get("request_hash") != request_hash:
                raise self._raise_error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key was already used with a different payload",
                    f"Key {resolved_key!r} is bound to a different request hash",
                    precondition_failed="idempotency_conflict",
                    suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                )
            return existing["result"]

        store = self.command_store
        if store is None:
            raise self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Command persistence is unavailable",
                "CommandStore is not configured; refusing to accept unpersisted command",
                precondition_failed="command_store_unconfigured",
            )

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
        audit_record = {
            "operator_id": identity.operator_id,
            "roles_at_submission": list(getattr(identity, "roles", []) or []),
            "action_kind": action_kind,
            "action_id": action_id,
            "timestamp": submitted_at,
            "idempotency_key": resolved_key,
            "request_hash": request_hash,
        }
        if action_kind == "approval":
            preconditions_checked.append("concurrent_safety")
            audit_record["preconditions_checked"] = preconditions_checked
            record, active = store.submit_command_if_no_active_target(
                command_id=command_id,
                command_type=command_type,
                target=target,
                submitted_at=submitted_at,
                params={"action_id": action_id, **payload},
                audit_context=audit_record,
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
                params={"action_id": action_id, **payload},
                audit_context=audit_record,
            )
        assert record is not None

        result = project_final_command_response(
            command_id=command_id,
            command=command_type,
            accepted_at=submitted_at,
            status=CommandStatus.SUBMITTED,
            staleness_warning=staleness_warning,
        )
        res_dict = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        self._gov_bff_idempotency[resolved_key] = {"request_hash": request_hash, "result": res_dict}
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
            clean_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
            store = self.command_store
            if store is not None:
                stored = store.get_command_by_idempotency_key(
                    clean_key,
                    operator_id=identity.operator_id,
                )
                if stored:
                    final_token_id = str(stored.get("target", {}).get("id") or token_id)
        content["data"]["tokenId"] = final_token_id
        content["data"]["id"] = final_token_id
        content["data"]["status"] = "created"
        return JSONResponse(status_code=201, content=content)

    def get_confirm_token(self, token_id: str, identity: OperatorIdentity) -> Dict[str, Any]:
        self.check_read_role(identity)
        self.raise_if_confirm_token_expired(token_id)
        return {
            "data": self.confirm_token_lifecycle_payload(token_id),
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

        req_hash = _stable_json_hash({"command_id": original_command_id, "confirm_token": confirm_token})
        existing = self._gov_bff_idempotency.get(resolved_key)
        if existing is not None:
            if existing.get("request_hash") != req_hash:
                raise self._raise_error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key already used with a different payload",
                    f"Key {resolved_key!r} is bound to a different confirmation request",
                    precondition_failed="idempotency_conflict",
                    suggestion="Use a new Idempotency-Key or resubmit the original confirmation unchanged",
                )
            return existing["result"]

        self.raise_if_confirm_token_expired(confirm_token)
        staleness_warning = self.check_read_surface_state()
        confirmation_id = str(uuid.uuid4())
        confirmed_at = self._utc_now()
        self.record_command_confirmation_redeem(
            token_id=confirm_token,
            command_id=original_command_id,
            confirmation_id=confirmation_id,
            confirmed_at=confirmed_at,
            identity=identity,
            idempotency_key=resolved_key,
            request_hash=req_hash,
        )
        result = {
            "confirmation_id": confirmation_id,
            "command_id": original_command_id,
            "token": confirm_token,
            "tokenId": confirm_token,
            "status": "accepted",
            "lifecycleStatus": "redeemed",
            "redeemed": True,
            "confirmed_at": confirmed_at,
            "confirmed_by": identity.operator_id,
        }
        if staleness_warning is not None:
            result["staleness_warning"] = {
                "read_surface_state": staleness_warning.read_surface_state,
                "message": staleness_warning.message,
            }
        self._gov_bff_idempotency[resolved_key] = {"request_hash": req_hash, "result": result}
        return result

    def get_command_confirmation_status(self, token: str, identity: OperatorIdentity) -> Dict[str, Any]:
        self.check_read_role(identity)
        self.raise_if_confirm_token_expired(token)
        token_state = self.confirm_token_lifecycle_payload(token)
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

        req_hash = _stable_json_hash({"command_id": command_id, "confirm_token": token})
        existing = self._gov_bff_idempotency.get(resolved_key)
        if existing is not None:
            if existing.get("request_hash") != req_hash:
                raise self._raise_error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key already used with a different payload",
                    f"Key {resolved_key!r} is bound to a different confirmation request",
                    precondition_failed="idempotency_conflict",
                    suggestion="Use a new Idempotency-Key or resubmit the original confirmation unchanged",
                    correlation_id=correlation_id,
                )
            return existing["result"]

        self.record_command_confirmation_redeem(
            token_id=token,
            command_id=command_id,
            confirmation_id=confirmation_id,
            confirmed_at=snapshot_at,
            identity=identity,
            idempotency_key=resolved_key,
            request_hash=req_hash,
        )

        if self._publish_event is not None:
            self._publish_event(
                "command.confirm",
                {
                    "commandId": command_id,
                    "tokenId": token,
                    "confirmationId": confirmation_id,
                    "confirmed_at": snapshot_at,
                    "actor": identity.operator_id,
                },
            )

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
        self._gov_bff_idempotency[resolved_key] = {"request_hash": req_hash, "result": result}
        return result

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

        duplicate = store.get_command_by_idempotency_key(
            foundation_context["idempotency_record"].idempotency_key,
            operator_id=identity.operator_id,
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
                        self._process_command_task, str(duplicate["command_id"])
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
                confirm_token_records_fn=self.confirm_token_records,
                confirm_token_lifecycle_fn=self.confirm_token_lifecycle_payload,
                read_store=self.read_store,
                command_store=store,
            )
        except HTTPException as exc:
            raise foundation_bff_error(exc, foundation_context=foundation_context) from exc

        stored_params = dict(cmd.params)
        stored_params["idempotency_key"] = resolved_key
        stored_params["request_hash"] = foundation_context["idempotency_record"].request_hash
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
        if precondition_evidence:
            audit_record["precondition_evidence"] = precondition_evidence
        if audit_extra:
            audit_record.update({key: value for key, value in audit_extra.items() if value is not None})

        serialized_foundation = serialize_foundation_context(foundation_context)
        with store.serialized_transaction():
            duplicate_after_precheck = store.get_command_by_idempotency_key(
                resolved_key,
                operator_id=identity.operator_id,
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
            background_tasks.add_task(self._process_command_task, command_id)

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
