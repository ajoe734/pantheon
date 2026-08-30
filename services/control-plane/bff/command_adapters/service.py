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

from fastapi import HTTPException
from fastapi.responses import JSONResponse

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
    CommandSubmissionResponse,
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


class CommandAdapterService:
    """Domain service managing operator commands, action adapters, and confirmation tokens."""

    def __init__(
        self,
        *,
        get_command_store: Optional[Callable[[], Any]] = None,
        get_read_store: Optional[Callable[[], Any]] = None,
        extract_identity: Optional[Callable[..., OperatorIdentity]] = None,
        require_operator_role: Optional[Callable[[OperatorIdentity], None]] = None,
        require_read_role: Optional[Callable[[OperatorIdentity], None]] = None,
        bff_error: Optional[Callable[..., Exception]] = None,
        utc_now_fn: Optional[Callable[[], str]] = None,
        submit_command_admission: Optional[Callable[..., Any]] = None,
        dispatch_command_fn: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._get_command_store = get_command_store
        self._get_read_store = get_read_store
        self._extract_identity = extract_identity
        self._require_operator_role = require_operator_role
        self._require_read_role = require_read_role
        self._bff_error = bff_error
        self._utc_now = utc_now_fn or utc_now
        self._submit_command_admission = submit_command_admission
        self._dispatch_command = dispatch_command_fn or dispatch_domain_command

        self._final_contract_idempotency: Dict[str, Dict[str, Any]] = {}
        self._gov_bff_idempotency: Dict[str, Dict[str, Any]] = {}

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
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:].strip()
            parts = token.split(":")
            actor = parts[0] if parts else "system"
            roles = [r.strip() for r in parts[1].split(",")] if len(parts) > 1 else ["operator"]
            return OperatorIdentity(
                operator_id=actor,
                roles=roles,
                auth_mode="bearer",
                has_mfa=len(parts) > 2 and parts[2] == "mfa",
            )
        return OperatorIdentity(
            operator_id="anonymous",
            roles=["viewer"],
            auth_mode="anonymous",
            has_mfa=False,
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
        audit_ctx = {
            "actor": identity.operator_id,
            "operator_id": identity.operator_id,
            "command_id": command_id,
            "reason": str(payload.get("reason") or command_type.value),
            "foundation": foundation_ctx,
        }

        if store is not None:
            if terminal_on_persist and hasattr(store, "submit_terminal_command"):
                store.submit_terminal_command(
                    command_id=command_id,
                    command_type=command_type,
                    target=TargetObject(type=target_type, id=target_id),
                    submitted_at=now,
                    params=payload,
                    audit_context=audit_ctx,
                    foundation_context=foundation_ctx,
                )
            else:
                store.submit_command(
                    command_id=command_id,
                    command_type=command_type,
                    target=TargetObject(type=target_type, id=target_id),
                    submitted_at=now,
                    params=payload,
                    audit_context=audit_ctx,
                    foundation_context=foundation_ctx,
                )

        result_content = {
            "command_id": command_id,
            "status": "accepted",
            "data": {
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
    ) -> Dict[str, Any]:
        self.check_operator_role(identity)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        correlation_id = str(x_correlation_id or "").strip() or str(uuid.uuid4())
        _reject_body_idempotency_key(payload)

        body_confirm_token = str(payload.get("confirm_token") or payload.get("confirmToken") or "").strip()
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

        self.raise_if_confirm_token_expired(token)
        confirmation_id = str(uuid.uuid4())
        confirmed_at = self._utc_now()
        req_hash = _stable_json_hash({"command_id": command_id, "confirm_token": token})

        self.record_command_confirmation_redeem(
            token_id=token,
            command_id=command_id,
            confirmation_id=confirmation_id,
            confirmed_at=confirmed_at,
            identity=identity,
            idempotency_key=resolved_key,
            request_hash=req_hash,
        )

        return {
            "status": "accepted",
            "data": {
                "command_id": command_id,
                "token": token,
                "tokenId": token,
                "confirmation_id": confirmation_id,
                "status": "accepted",
                "confirmed_at": confirmed_at,
                "confirmed_by": identity.operator_id,
            },
            "meta": {
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "correlation_id": correlation_id,
                "snapshot_at": confirmed_at,
            },
        }

    def submit_command(
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
        if self._submit_command_admission is not None:
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
                route="POST /api/v1/operator/commands",
            )
        identity = self.extract_identity(authorization, mfa_token=x_mfa_token)
        self.check_operator_role(identity)
        return self.sem_command_response(
            command_type=CommandType.CAPITAL_POOL_ACTION,
            target_type=ObjectType.CAPITAL_POOL,
            target_id=str(payload.get("target_id") or "target-1"),
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
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
        if self._submit_command_admission is not None:
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
        identity = self.extract_identity(authorization, mfa_token=x_mfa_token)
        self.check_operator_role(identity)
        return self.sem_command_response(
            command_type=CommandType.CAPITAL_POOL_ACTION,
            target_type=ObjectType.CAPITAL_POOL,
            target_id=str(payload.get("target_id") or "target-1"),
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )
