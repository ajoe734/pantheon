"""Single owner for Management NL durable command admission/replay.

BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: prior to this module,
``POST /bff/management/nl/ask`` (JSON) went through a durable
admit/wait/replay/complete state machine backed by
``ManagementNlCommandIdempotencyStore``, while
``POST /bff/management/nl/ask/stream`` (SSE) skipped it entirely and called
the provider inline with no dedup, no 409-on-conflict, and no durable
replay on reconnect. There was also a second, legacy, env-flag-gated
idempotency mechanism (an in-memory dict plus
``ManagementAiConversationStore.get_idempotency``/``put_idempotency``) that
could bypass the durable store altogether.

This module is the single, real implementation of the admission/replay
decision logic both transports must share: reserve-or-replay-or-wait via
``admit``, mark a reservation ``complete`` exactly once, or
``mark_uncertain`` it on a known failure so a later retry is possible
in the future (never silently re-executed). ``main.py`` keeps thin
module-level functions (``_mgmt_nl_command_admit`` etc.) that delegate to a
single composed :class:`ManagementNlUseCase` instance so both
``bff_management_nl_ask`` and ``bff_management_nl_ask_stream`` call the
exact same code path -- no per-transport duplicate.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextvars import ContextVar
import inspect
import re
import time
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    Iterator,
    List,
    Mapping,
    NoReturn,
    Optional,
    Sequence,
    Set,
    Tuple,
)
from fastapi import Body, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse

from urllib.parse import quote

from collections import deque
import json
import uuid
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from .management_contracts import ManagementNlUseCaseDeps
from ..auth import policy as auth_policy
from ..management_ai_store import (
    ManagementAiAttachmentError,
    ManagementAiConversationStore,
)
from ..management_nl_command_idempotency import (
    DEFAULT_STORAGE_PATH as DEFAULT_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_PATH,
    ManagementNlCommandIdempotencyStore,
    ManagementNlCommandPayloadConflict,
    ManagementNlCommandRecoveryRequired,
    ManagementNlCommandReservation,
    ManagementNlCommandScope,
    ManagementNlCommandStorageError,
)
from ..models import ErrorCode, OperatorIdentity, utc_now

log = logging.getLogger(__name__)

DEFAULT_MANAGEMENT_AI_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60

_MGMT_AI_CONVERSATION_STORE: Optional[ManagementAiConversationStore] = None
_MGMT_AI_AUDIT_EVENTS: deque = deque(maxlen=500)


def get_management_ai_conversation_store() -> ManagementAiConversationStore:
    global _MGMT_AI_CONVERSATION_STORE
    if _MGMT_AI_CONVERSATION_STORE is None:
        _MGMT_AI_CONVERSATION_STORE = ManagementAiConversationStore()
    return _MGMT_AI_CONVERSATION_STORE


def set_management_ai_conversation_store(store: Optional[ManagementAiConversationStore]) -> None:
    global _MGMT_AI_CONVERSATION_STORE
    _MGMT_AI_CONVERSATION_STORE = store


def reset_management_ai_conversation_store() -> None:
    global _MGMT_AI_CONVERSATION_STORE
    _MGMT_AI_CONVERSATION_STORE = None


def management_ai_conversation_href(session_id: str, *, trace_id: Optional[str] = None) -> str:
    return f"/bff/management/ai/conversations/{quote(str(session_id or ''), safe='')}"


def management_ai_attachment_url(attachment_id: str) -> str:
    return f"/bff/management/ai/attachments/{quote(str(attachment_id or ''), safe='')}"


def management_ai_attachment_api_payload(attachment: Dict[str, Any]) -> Dict[str, Any]:
    attachment_id = str(
        attachment.get("id")
        or attachment.get("attachmentId")
        or attachment.get("attachment_id")
        or ""
    ).strip()
    mime_type = str(attachment.get("mimeType") or attachment.get("mime_type") or "application/octet-stream")
    size_bytes = int(attachment.get("sizeBytes") or attachment.get("size_bytes") or 0)
    return {
        "id": attachment_id,
        "attachment_id": attachment_id,
        "kind": str(attachment.get("kind") or "file"),
        "mime_type": mime_type,
        "filename": str(attachment.get("filename") or attachment_id or "attachment"),
        "size_bytes": size_bytes,
        "url": management_ai_attachment_url(attachment_id) if attachment_id else "",
    }


def management_ai_turn_api_payload(turn: Dict[str, Any]) -> Dict[str, Any]:
    attachments = [
        management_ai_attachment_api_payload(item)
        for item in (turn.get("attachments") or [])
        if isinstance(item, dict)
    ]
    provider_status = (
        turn.get("provider_status")
        if isinstance(turn.get("provider_status"), dict)
        else turn.get("providerStatus")
        if isinstance(turn.get("providerStatus"), dict)
        else None
    )
    ui_actions = (
        turn.get("ui_actions")
        if isinstance(turn.get("ui_actions"), list)
        else turn.get("uiActions")
        if isinstance(turn.get("uiActions"), list)
        else []
    )
    payload = {
        "id": turn.get("id"),
        "turn_id": turn.get("turn_id") or turn.get("turnId") or turn.get("id"),
        "message_id": turn.get("message_id") or turn.get("id"),
        "session_id": turn.get("session_id") or turn.get("sessionId"),
        "trace_id": turn.get("trace_id") or turn.get("traceId"),
        "role": turn.get("role"),
        "text": turn.get("text") or "",
        "content": turn.get("text") or "",
        "created_at": turn.get("created_at") or turn.get("createdAt"),
        "provider_status": provider_status,
        "attachments": attachments,
        "ui_actions": ui_actions,
        "actions": ui_actions,
    }
    ui_snapshot = (
        turn.get("ui_snapshot")
        if isinstance(turn.get("ui_snapshot"), dict)
        else turn.get("uiSnapshot")
        if isinstance(turn.get("uiSnapshot"), dict)
        else None
    )
    if ui_snapshot is not None:
        payload["ui_snapshot"] = ui_snapshot
    return payload


def management_ai_require_session_access(
    session: Dict[str, Any],
    identity: OperatorIdentity,
    *,
    tenant_id: Optional[str],
) -> None:
    owner_id = str(session.get("ownerId") or session.get("owner_id") or "").strip()
    session_tenant_id = str(session.get("tenantId") or session.get("tenant_id") or "").strip()
    clean_tenant_id = str(tenant_id or "").strip()
    if owner_id and owner_id == identity.operator_id:
        return
    if clean_tenant_id and session_tenant_id and clean_tenant_id == session_tenant_id:
        return
    raise auth_policy.bff_error(
        403,
        ErrorCode.FORBIDDEN,
        "Management AI session is not visible to this operator",
        "management_ai_session_not_visible",
        precondition_failed="management_ai_session_visibility",
    )


def management_ai_session_not_found(session_id: str) -> HTTPException:
    clean_session_id = str(session_id or "").strip()
    return auth_policy.bff_error(
        404,
        ErrorCode.RESOURCE_NOT_FOUND,
        f"Management AI session not found: {clean_session_id!r}",
        "management_ai_session_not_found",
        precondition_failed="management_ai_session",
    )


def management_ai_get_visible_session_or_404(
    session_id: str,
    identity: OperatorIdentity,
    *,
    tenant_id: Optional[str],
    conversation_store: Optional[ManagementAiConversationStore] = None,
) -> Dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    store = conversation_store if conversation_store is not None else get_management_ai_conversation_store()
    session = store.get_session(clean_session_id)
    if session is None:
        raise management_ai_session_not_found(clean_session_id)
    try:
        management_ai_require_session_access(session, identity, tenant_id=tenant_id)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise management_ai_session_not_found(clean_session_id) from exc
        raise
    return session


def management_ai_get_session_or_404(
    session_id: str,
    identity: OperatorIdentity,
    *,
    tenant_id: Optional[str],
    conversation_store: Optional[ManagementAiConversationStore] = None,
) -> Dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    store = conversation_store if conversation_store is not None else get_management_ai_conversation_store()
    session = store.get_session(clean_session_id)
    if session is None:
        raise management_ai_session_not_found(clean_session_id)
    management_ai_require_session_access(session, identity, tenant_id=tenant_id)
    return session


def management_ai_ensure_session(
    *,
    session_id: str,
    identity: OperatorIdentity,
    tenant_id: Optional[str],
    now: str,
    title: str,
    conversation_store: Optional[ManagementAiConversationStore] = None,
) -> Dict[str, Any]:
    store = conversation_store if conversation_store is not None else get_management_ai_conversation_store()
    existing = store.get_session(session_id)
    if existing is not None:
        management_ai_require_session_access(existing, identity, tenant_id=tenant_id)
    try:
        return store.upsert_session(
            session_id=session_id,
            owner_id=identity.operator_id,
            tenant_id=tenant_id,
            now=now,
            title=title,
        )
    except Exception as exc:
        log.warning("Failed to persist Management AI session", exc_info=True)
        raise auth_policy.bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Management AI session store write failed",
            str(exc),
            precondition_failed="management_ai_session_store",
        )


def management_ai_store_attachments(
    *,
    attachments: Any,
    session_id: str,
    turn_id: str,
    conversation_store: Optional[ManagementAiConversationStore] = None,
) -> List[Dict[str, Any]]:
    store = conversation_store if conversation_store is not None else get_management_ai_conversation_store()
    try:
        return store.store_attachments(
            attachments,
            session_id=session_id,
            turn_id=turn_id,
        )
    except ManagementAiAttachmentError as exc:
        status_code = int(getattr(exc, "status_code", 422) or 422)
        code = ErrorCode.REQUEST_TOO_LARGE if status_code == 413 else ErrorCode.VALIDATION_FAILED
        raise auth_policy.bff_error(
            status_code,
            code,
            (
                "Management AI attachment payload is too large"
                if status_code == 413
                else "Management AI attachment payload is invalid"
            ),
            str(exc),
            precondition_failed=getattr(exc, "precondition_failed", "management_ai_attachment"),
            details_extra=getattr(exc, "details", {}),
        )
    except ValueError as exc:
        raise auth_policy.bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "Management AI attachment payload is invalid",
            str(exc),
            precondition_failed="management_ai_attachment",
        )
    except Exception as exc:
        log.warning("Failed to persist Management AI attachment", exc_info=True)
        raise auth_policy.bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Management AI attachment store write failed",
            str(exc),
            precondition_failed="management_ai_attachment_store",
        )


def management_ai_append_turn(
    *,
    turn_id: str,
    session_id: str,
    role: str,
    text: str,
    created_at: str,
    trace_id: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    provider_status: Optional[Dict[str, Any]] = None,
    ui_snapshot: Optional[Dict[str, Any]] = None,
    ui_actions: Optional[List[Dict[str, Any]]] = None,
    conversation_store: Optional[ManagementAiConversationStore] = None,
) -> Dict[str, Any]:
    store = conversation_store if conversation_store is not None else get_management_ai_conversation_store()
    try:
        return store.append_turn(
            turn_id=turn_id,
            session_id=session_id,
            role=role,
            text=text,
            created_at=created_at,
            trace_id=trace_id,
            attachments=attachments,
            provider_status=provider_status,
            ui_snapshot=ui_snapshot,
            ui_actions=ui_actions,
        )
    except Exception as exc:
        log.warning("Failed to persist Management AI turn", exc_info=True)
        raise auth_policy.bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Management AI turn store write failed",
            str(exc),
            precondition_failed="management_ai_turn_store",
        )


def management_ai_server_conversation_context(
    *,
    session_id: str,
    client_hint: Dict[str, Any],
    conversation_store: Optional[ManagementAiConversationStore] = None,
    history_window_fn: Optional[Callable[[List[Dict[str, Any]]], Tuple[List[Dict[str, Any]], Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    store = conversation_store if conversation_store is not None else get_management_ai_conversation_store()
    stored_turns = store.list_turns(session_id)
    turns = []
    for turn in stored_turns:
        api_turn = management_ai_turn_api_payload(turn)
        turns.append(
            {
                "id": api_turn.get("id"),
                "role": api_turn.get("role"),
                "content": api_turn.get("text") or "",
                "text": api_turn.get("text") or "",
                "created_at": api_turn.get("created_at"),
                "attachments": api_turn.get("attachments") or [],
                "provider_status": api_turn.get("provider_status"),
                "trace_id": api_turn.get("trace_id"),
            }
        )
    if history_window_fn is not None:
        provider_turns, history_budget = history_window_fn(turns)
    else:
        provider_turns = turns
        history_budget = {
            "history_char_budget": 0,
            "history_estimated_chars": 0,
            "history_truncated": False,
            "history_omitted_turn_count": 0,
        }
    return {
        "recent_turns": provider_turns,
        "all_turns": provider_turns,
        "turn_count": len(provider_turns),
        "stored_turn_count": len(turns),
        "source": "server",
        "history_source": "management_ai_store",
        "history_char_budget": history_budget.get("history_char_budget", 0),
        "history_estimated_chars": history_budget.get("history_estimated_chars", 0),
        "history_truncated": history_budget.get("history_truncated", False),
        "history_omitted_turn_count": history_budget.get("history_omitted_turn_count", 0),
        "summary": client_hint.get("summary") or "",
        "client_hint": client_hint,
        "max_recent_turns": None,
    }


def management_ai_list_conversations(
    *,
    identity: OperatorIdentity,
    caller_tenant_id: Optional[str],
    limit: int = 50,
    conversation_store: Optional[ManagementAiConversationStore] = None,
    conversation_href_fn: Optional[Callable[[str], str]] = None,
    session_ttl_seconds: int = DEFAULT_MANAGEMENT_AI_SESSION_TTL_SECONDS,
) -> Dict[str, Any]:
    store = conversation_store if conversation_store is not None else get_management_ai_conversation_store()
    href_fn = conversation_href_fn or management_ai_conversation_href
    sessions = store.list_sessions(
        owner_id=identity.operator_id,
        tenant_id=caller_tenant_id,
        limit=limit,
    )
    items: List[Dict[str, Any]] = []
    for session in sessions:
        session_id = str(session.get("sessionId") or session.get("session_id") or session.get("id") or "").strip()
        if not session_id:
            continue
        try:
            management_ai_require_session_access(session, identity, tenant_id=caller_tenant_id)
        except HTTPException:
            continue
        turn_count = len(store.list_turns(session_id))
        items.append(
            {
                "id": session_id,
                "session_id": session_id,
                "title": session.get("title") or "",
                "owner_id": session.get("owner_id") or session.get("ownerId"),
                "tenant_id": session.get("tenant_id") or session.get("tenantId"),
                "created_at": session.get("created_at") or session.get("createdAt"),
                "updated_at": session.get("updated_at") or session.get("updatedAt"),
                "turn_count": turn_count,
                "href": href_fn(session_id),
            }
        )
    return {
        "data": {
            "id": "management_ai_conversations",
            "items": items,
            "summary": {
                "total_sessions": len(items),
                "returned_items": len(items),
            },
        },
        "page_info": {
            "next_page_token": None,
            "total": len(items),
            "page_size": limit,
        },
        "meta": {
            "count": len(items),
            "limit": limit,
            "session_ttl_seconds": session_ttl_seconds,
            "surfaces": {
                "management_ai_conversation_list": {
                    "status": "ok",
                    "source": "management_ai_store",
                }
            },
        },
    }


def management_ai_get_conversation(
    *,
    session_id: str,
    identity: OperatorIdentity,
    caller_tenant_id: Optional[str],
    trace_id: Optional[str] = None,
    limit: int = 500,
    conversation_store: Optional[ManagementAiConversationStore] = None,
    audit_href_fn: Optional[Callable[[str, Optional[str]], str]] = None,
    session_ttl_seconds: int = DEFAULT_MANAGEMENT_AI_SESSION_TTL_SECONDS,
) -> Dict[str, Any]:
    store = conversation_store if conversation_store is not None else get_management_ai_conversation_store()
    clean_session_id = str(session_id or "").strip()
    session = management_ai_get_visible_session_or_404(
        clean_session_id,
        identity,
        tenant_id=caller_tenant_id,
        conversation_store=store,
    )
    turns = [
        management_ai_turn_api_payload(turn)
        for turn in store.list_turns(clean_session_id)
    ][:limit]
    audit_href = (
        audit_href_fn(clean_session_id, trace_id)
        if audit_href_fn is not None
        else f"/bff/management/ai/audit?session_id={clean_session_id}" + (f"&trace_id={trace_id}" if trace_id else "")
    )
    audit_log = {
        "href": audit_href,
        "trace_id": trace_id,
    }
    return {
        "data": {
            "session_id": clean_session_id,
            "trace_id": trace_id,
            "turns": turns,
            "local_only": False,
            "missing_in_store": False,
            "owner_id": session.get("owner_id") or session.get("ownerId"),
            "tenant_id": session.get("tenant_id") or session.get("tenantId"),
            "created_at": session.get("created_at") or session.get("createdAt"),
            "updated_at": session.get("updated_at") or session.get("updatedAt"),
            "audit_log": audit_log,
            "session": {
                "session_id": clean_session_id,
                "ttl_seconds": session_ttl_seconds,
            },
        },
        "meta": {
            "count": len(turns),
            "turn_cap": limit,
            "session_ttl_seconds": session_ttl_seconds,
            "filters": {
                "session_id": clean_session_id,
                "trace_id": trace_id,
                "trace_id_ignored": trace_id is not None,
            },
            "surfaces": {
                "management_ai_conversation": {
                    "status": "ok",
                    "source": "management_ai_store",
                    "reason": None,
                }
            },
        },
    }


def management_ai_get_attachment(
    *,
    attachment_id: str,
    identity: OperatorIdentity,
    caller_tenant_id: Optional[str],
    conversation_store: Optional[ManagementAiConversationStore] = None,
) -> Tuple[bytes, str, str]:
    store = conversation_store if conversation_store is not None else get_management_ai_conversation_store()
    found = store.find_attachment(attachment_id)
    if found is None:
        raise auth_policy.bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"Management AI attachment not found: {attachment_id!r}",
            "management_ai_attachment_not_found",
            precondition_failed="management_ai_attachment",
        )
    metadata, turn = found
    management_ai_get_session_or_404(
        str(turn.get("sessionId") or turn.get("session_id") or ""),
        identity,
        tenant_id=caller_tenant_id,
        conversation_store=store,
    )
    try:
        content, mime_type, filename = store.read_attachment(attachment_id, metadata)
    except FileNotFoundError:
        raise auth_policy.bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"Management AI attachment object not found: {attachment_id!r}",
            "management_ai_attachment_object_not_found",
            precondition_failed="management_ai_attachment_object",
        )
    return content, mime_type, filename


_ADMISSION_ERRORS = (
    ManagementNlCommandPayloadConflict,
    ManagementNlCommandRecoveryRequired,
    ManagementNlCommandStorageError,
)

_MGMT_NL_COMMAND_IDEMPOTENCY_STORE: Optional[ManagementNlCommandIdempotencyStore] = None
_MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG: Optional[Tuple[str, float]] = None


def get_mgmt_nl_command_recovery_seconds() -> float:
    raw = os.getenv(
        "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_RECOVERY_SECONDS",
        "300",
    ).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 300.0
    return max(value, 0.001)


def get_mgmt_nl_command_idempotency_store(
    *,
    storage_path: Optional[str] = None,
    recovery_seconds: Optional[float] = None,
) -> ManagementNlCommandIdempotencyStore:
    global _MGMT_NL_COMMAND_IDEMPOTENCY_STORE, _MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG
    resolved_path = (
        storage_path
        if storage_path is not None
        else os.getenv(
            "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH",
            DEFAULT_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_PATH,
        ).strip()
    )
    resolved_recovery = (
        recovery_seconds
        if recovery_seconds is not None
        else get_mgmt_nl_command_recovery_seconds()
    )
    config = (resolved_path, resolved_recovery)
    if _MGMT_NL_COMMAND_IDEMPOTENCY_STORE is None or _MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG != config:
        _MGMT_NL_COMMAND_IDEMPOTENCY_STORE = ManagementNlCommandIdempotencyStore(
            resolved_path,
            recovery_seconds=config[1],
        )
        _MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG = config
    return _MGMT_NL_COMMAND_IDEMPOTENCY_STORE


def reset_mgmt_nl_command_idempotency_store() -> None:
    global _MGMT_NL_COMMAND_IDEMPOTENCY_STORE, _MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG
    _MGMT_NL_COMMAND_IDEMPOTENCY_STORE = None
    _MGMT_NL_COMMAND_IDEMPOTENCY_CONFIG = None


class ManagementNlUseCase:
    """Durable command admission/replay shared by ask and ask/stream."""

    def __init__(self, deps: ManagementNlUseCaseDeps) -> None:
        self._deps = deps

    @staticmethod
    def scope(*, actor_id: str, tenant_id: str, route: str, resolved_key: str) -> ManagementNlCommandScope:
        return ManagementNlCommandScope(
            actor_id=actor_id,
            tenant_id=tenant_id,
            route=route,
            idempotency_key=resolved_key,
        )

    async def admit(
        self,
        *,
        scope: ManagementNlCommandScope,
        request_hash: str,
        display_key: str,
    ) -> Tuple[Optional[ManagementNlCommandReservation], Optional[Dict[str, Any]]]:
        """Reserve ownership, replay a terminal result, or wait on an owner.

        Returns ``(reservation, None)`` when this call is now the owner and
        must invoke the provider; returns ``(None, result)`` when a terminal
        result already exists (fresh reservation completed inline, or a
        concurrent owner reached completion while we waited) and must be
        replayed verbatim instead of invoking the provider again.
        """
        store = self._deps.command_store()
        try:
            admission = await asyncio.to_thread(
                store.admit,
                scope,
                request_hash=request_hash,
                legacy_result=None,
                legacy_terminal=False,
            )
        except _ADMISSION_ERRORS as exc:
            self._deps.raise_admission_error(exc, display_key)

        if admission.state == "owner":
            return admission.reservation, None
        if admission.state == "complete":
            return None, admission.result
        if admission.state != "wait":
            self._deps.raise_admission_error(
                ManagementNlCommandStorageError(
                    f"Unsupported Management NL command admission state: {admission.state}"
                ),
                display_key,
            )

        deadline = asyncio.get_running_loop().time() + self._deps.wait_seconds()
        while True:
            if asyncio.get_running_loop().time() >= deadline:
                self._deps.raise_wait_timeout()
            await asyncio.sleep(self._deps.poll_seconds())
            try:
                admission = await asyncio.to_thread(
                    store.observe,
                    scope,
                    request_hash=request_hash,
                )
            except _ADMISSION_ERRORS as exc:
                self._deps.raise_admission_error(exc, display_key)
            if admission.state == "complete":
                return None, admission.result
            if admission.state != "wait":
                self._deps.raise_admission_error(
                    ManagementNlCommandStorageError(
                        f"Unsupported Management NL command observation state: {admission.state}"
                    ),
                    display_key,
                )

    async def complete(
        self,
        reservation: Optional[ManagementNlCommandReservation],
        result: Mapping[str, Any],
        *,
        display_key: str,
    ) -> None:
        """Persist the terminal result for a reservation exactly once.

        A no-op when ``reservation`` is ``None`` (a replayed/legacy-owned
        result never held a reservation of its own).
        """
        if reservation is None:
            return
        store = self._deps.command_store()
        try:
            await asyncio.to_thread(store.complete, reservation, result)
        except _ADMISSION_ERRORS as exc:
            self._deps.raise_admission_error(exc, display_key)

    async def mark_uncertain(
        self,
        reservation: Optional[ManagementNlCommandReservation],
        *,
        reason: str,
        on_failure: Optional[Any] = None,
    ) -> None:
        """Fail closed after a known owner error without releasing the key.

        A prior uncertain reservation is never silently retried; it becomes
        retryable again only once the store's recovery window elapses.
        """
        if reservation is None:
            return
        store = self._deps.command_store()
        try:
            await asyncio.to_thread(store.mark_uncertain, reservation, reason=reason)
        except Exception:  # noqa: BLE001 - best-effort; caller already failed
            if on_failure is not None:
                on_failure()


# ---------------------------------------------------------------------------
# Management AI Audit Events & Usage Metrics
# ---------------------------------------------------------------------------

_MGMT_AI_USAGE_OBSERVED_SOURCE = "management_ai_bff_audit"
_MGMT_AI_USAGE_OBSERVED_COVERAGE = "bff_observed_management_ai_only"


def _management_ai_audit_path() -> Optional[str]:
    raw = os.getenv(
        "PANTHEON_MANAGEMENT_AI_AUDIT_PATH",
        "/tmp/pantheon-bff/management-ai-audit.jsonl",
    ).strip()
    if not raw or raw.lower() in {"off", "false", "disabled", "none"}:
        return None
    return raw


def _management_ai_summary_value(value: Any, *, max_len: int = 400) -> Any:
    if isinstance(value, str):
        clean = value.strip()
        if len(clean) > max_len:
            return f"{clean[:max_len]}..."
        return clean
    return value


def _management_ai_surface_summary(surfaces: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for key, value in (surfaces or {}).items():
        if not isinstance(value, dict):
            continue
        summary[str(key)] = {
            clean_key: value.get(clean_key)
            for clean_key in ("status", "source", "reason", "message")
            if value.get(clean_key) is not None
        }
    return summary


def _management_ai_provider_output_summary(provider_payload: Any) -> Dict[str, Any]:
    data = provider_payload.get("data") if isinstance(provider_payload, dict) else {}
    output = data.get("output") if isinstance(data, dict) else {}
    if not isinstance(output, dict):
        output = {}
    events = output.get("json_events")
    if not isinstance(events, list):
        events = []
        stdout = output.get("stdout")
        if isinstance(stdout, str):
            for line in stdout.splitlines():
                clean = line.strip()
                if not clean:
                    continue
                try:
                    loaded = json.loads(clean)
                except json.JSONDecodeError:
                    continue
                if isinstance(loaded, dict):
                    events.append(loaded)

    event_types: List[str] = []
    assistant_messages: List[str] = []
    usage: Optional[Dict[str, Any]] = None
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip()
        if event_type:
            event_types.append(event_type)
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message" and item.get("text") is not None:
            assistant_messages.append(str(_management_ai_summary_value(item.get("text"))))
        if event_type == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event.get("usage")

    return {
        "provider": data.get("provider") if isinstance(data, dict) else None,
        "status": data.get("status") if isinstance(data, dict) else None,
        "returncode": output.get("returncode"),
        "duration_ms": output.get("duration_ms"),
        "json_event_count": len(events),
        "json_event_types": event_types,
        "assistant_messages": assistant_messages[:3],
        "usage": usage,
    }


def _management_ai_record_event(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = jsonable_encoder(
        {
            "event_id": event.get("event_id") or f"mgmt-ai-evt-{uuid.uuid4().hex[:16]}",
            "recorded_at": event.get("recorded_at") or utc_now(),
            **event,
        }
    )
    _MGMT_AI_AUDIT_EVENTS.append(payload)
    path = _management_ai_audit_path()
    if path:
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            log.warning("Failed to persist management AI audit event", exc_info=True)
    return payload


def _management_ai_read_audit_file(limit: int) -> List[Dict[str, Any]]:
    path = _management_ai_audit_path()
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except Exception:
        log.warning("Failed to read management AI audit log", exc_info=True)
        return []
    events: List[Dict[str, Any]] = []
    for line in lines[-max(limit * 4, limit):]:
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            events.append(loaded)
    return events


def _management_ai_event_matches(
    event: Dict[str, Any],
    *,
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    message_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> bool:
    if session_id and str(event.get("session_id") or "") != session_id:
        return False
    if trace_id and str(event.get("trace_id") or "") != trace_id:
        return False
    if message_id and str(event.get("message_id") or "") != message_id:
        return False
    if event_type and str(event.get("event_type") or "") != event_type:
        return False
    return True


def _management_ai_list_audit_events(
    *,
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    message_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    candidates = _management_ai_read_audit_file(limit) or list(_MGMT_AI_AUDIT_EVENTS)
    filtered = [
        event
        for event in candidates
        if _management_ai_event_matches(
            event,
            session_id=session_id,
            trace_id=trace_id,
            message_id=message_id,
            event_type=event_type,
        )
    ]
    return filtered[-limit:]


def _management_ai_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        clean = str(value).strip()
        return float(clean) if clean else None
    except (TypeError, ValueError):
        return None


def _management_ai_usage_number(usage: Any, *keys: str) -> Optional[float]:
    if not isinstance(usage, dict):
        return None
    for key in keys:
        value = _management_ai_number(usage.get(key))
        if value is not None:
            return value
    return None


def _management_ai_provider_key(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return clean or "unknown"


def _management_ai_provider_display(provider: str) -> str:
    labels = {
        "codex": "Codex CLI",
        "codex_cli": "Codex CLI",
        "claude": "Claude CLI",
        "claude_cli": "Claude CLI",
        "openclaw": "OpenClaw",
    }
    return labels.get(provider, provider)


def _management_ai_provider_route(provider: str, *, stream: bool = False) -> str:
    normalized = _management_ai_provider_key(provider)
    if normalized in {"claude", "claude_cli"}:
        return "POST /api/openclaw-adapter/assistant/claude/invoke"
    if normalized in {"openclaw", "openclaw_agent"}:
        suffix = "/stream" if stream else ""
        return f"POST /api/openclaw-adapter/assistant/providers/openclaw/invoke{suffix}"
    return "POST /api/openclaw-adapter/assistant/providers/codex/invoke"


def _management_ai_event_model(event: Dict[str, Any]) -> str:
    output_summary = event.get("output_summary") if isinstance(event.get("output_summary"), dict) else {}
    usage = output_summary.get("usage") if isinstance(output_summary.get("usage"), dict) else {}
    for value in (
        event.get("model"),
        event.get("model_id"),
        event.get("modelId"),
        event.get("provider_model"),
        event.get("providerModel"),
        output_summary.get("model"),
        output_summary.get("model_id"),
        output_summary.get("modelId"),
        usage.get("model"),
        usage.get("model_id"),
        usage.get("modelId"),
    ):
        clean = str(value or "").strip()
        if clean:
            return clean
    return "default"


def _management_ai_quota_snapshot(provider: Dict[str, Any]) -> Dict[str, Any]:
    usage = provider.get("usage") if isinstance(provider.get("usage"), dict) else None
    quota = provider.get("quota") if isinstance(provider.get("quota"), dict) else None
    source = usage or quota or {}
    return {
        "status": str(source.get("status") or "unknown"),
        "source": str(source.get("source") or "not_configured"),
        "remaining": source.get("remaining"),
        "remaining_percent": source.get("remaining_percent", source.get("remainingPercent")),
        "limit": source.get("limit"),
        "used": source.get("used"),
        "unit": source.get("unit"),
        "reset_at": source.get("reset_at", source.get("resetAt")),
        "updated_at": source.get("updated_at", source.get("updatedAt")),
        "checked_at": source.get("checked_at", source.get("checkedAt")),
        "reason": source.get("reason") or (
            "provider_usage_source_not_configured" if not source else None
        ),
    }


def _management_ai_empty_usage_row(provider: str) -> Dict[str, Any]:
    return {
        "provider": provider,
        "provider_name": _management_ai_provider_display(provider),
        "runtime": None,
        "ready": None,
        "auth_status": None,
        "status": "unknown",
        "live_auth": False,
        "calls": 0,
        "success_count": 0,
        "failed_count": 0,
        "started_count": 0,
        "prompt_bytes": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "duration_ms": 0,
        "source": _MGMT_AI_USAGE_OBSERVED_SOURCE,
        "coverage": _MGMT_AI_USAGE_OBSERVED_COVERAGE,
        "truth_policy": "observed_bff_events_only",
    }


management_ai_record_event = _management_ai_record_event
management_ai_list_audit_events = _management_ai_list_audit_events



_management_ai_append_turn = management_ai_append_turn
_management_ai_attachment_api_payload = management_ai_attachment_api_payload
_management_ai_conversation_href = management_ai_conversation_href
_management_ai_conversation_store = get_management_ai_conversation_store
_management_ai_ensure_session = management_ai_ensure_session
_management_ai_server_conversation_context = management_ai_server_conversation_context
_management_ai_store_attachments = management_ai_store_attachments

def _resolve_main() -> Any:
    return sys.modules.get("services.control_plane.bff.main") or sys.modules.get("main")


class _DynamicProxy:
    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        m = _resolve_main()
        if m is not None and hasattr(m, self._name):
            return getattr(m, self._name)(*args, **kwargs)
        raise RuntimeError(f"Unresolved dynamic dependency: {self._name}")

    def __getattr__(self, item: str) -> Any:
        m = _resolve_main()
        if m is not None and hasattr(m, self._name):
            target = getattr(m, self._name)
            return getattr(target, item)
        raise AttributeError(f"{self._name} has no attribute {item}")

    def __iter__(self) -> Any:
        m = _resolve_main()
        if m is not None and hasattr(m, self._name):
            return iter(getattr(m, self._name))
        return iter([])

    def __getitem__(self, key: Any) -> Any:
        m = _resolve_main()
        if m is not None and hasattr(m, self._name):
            return getattr(m, self._name)[key]
        raise KeyError(key)

    def __bool__(self) -> bool:
        m = _resolve_main()
        if m is not None and hasattr(m, self._name):
            return bool(getattr(m, self._name))
        return False


_DYNAMIC_NAMES = [
    "_extract_identity",
    "_require_read_role",
    "_reject_body_idempotency_key",
    "_agora_required_text",
    "_mgmt_nl_validate_question_size",
    "_mgmt_nl_parse_control_command",
    "_mgmt_nl_high_risk_classify",
    "_mgmt_nl_record_high_risk_refusal",
    "_mgmt_nl_caller_tenant",
    "_first_nonblank",
    "_mgmt_nl_trim_text",
    "_mgmt_nl_normalize_focus",
    "_mgmt_nl_normalize_conversation_context",
    "_mgmt_nl_normalize_ui_context",
    "_mgmt_nl_allowed_action_kinds",
    "_resolve_final_idempotency_key",
    "_stable_json_hash",
    "_request_dry_run_requested",
    "_dry_run_success_response",
    "_management_json_clone",
    "_mgmt_nl_handle_control_command",
    "_mgmt_nl_collect_context",
    "_mgmt_nl_deterministic_answer",
    "_mgmt_nl_provider_enabled",
    "_mgmt_nl_invoke_provider",
    "_mgmt_nl_provider_status",
    "_mgmt_nl_provider_name",
    "_management_nl_publish_completed_events",
    "_publish_event",
    "_record_agora_audit_event",
    "_bff_error",
    "_capabilities_for_identity",
    "_assistant_control_mode_for_identity",
    "_management_ai_audit_href",
    "_mgmt_nl_synthesize_answer",
    "_mgmt_nl_text_from_provider_value",
    "_mgmt_nl_extract_provider_actions",
    "_mgmt_nl_provider_mode_from_context",
    "_mgmt_nl_reject_development_payload",
    "_mgmt_nl_build_context_pack",
    "_mgmt_nl_jsonish",
    "_mgmt_nl_maybe_provider_answer",
    "redact_evidence_refs",
    "read_store",
    "_sse_buffers",
    "_sse_subscribers",
    "_MGMT_AI_SESSION_TTL_SECONDS",
    "_MGMT_NL_HIGH_RISK_REFUSAL_FOLLOWUPS",
    "_mgmt_nl_provider_prompt",
    "_mgmt_nl_surface_confidence",
    "OpenClawOpsClient",
    "OpenClawOpsClientError",
]
for _n in _DYNAMIC_NAMES:
    if _n not in globals():
        globals()[_n] = _DynamicProxy(_n)


_MGMT_NL_COMMAND_RESERVATION_CONTEXT: ContextVar[
    Optional[ManagementNlCommandReservation]
] = ContextVar("management_nl_command_reservation", default=None)

_MGMT_NL_COMMAND_ROUTE = "POST /bff/management/nl/ask"
def _mgmt_nl_command_scope(
    *,
    actor_id: str,
    tenant_id: str,
    resolved_key: str,
) -> ManagementNlCommandScope:
    return ManagementNlUseCase.scope(
        actor_id=actor_id,
        tenant_id=tenant_id,
        route=_MGMT_NL_COMMAND_ROUTE,
        resolved_key=resolved_key,
    )
def _mgmt_nl_result_is_terminal(result: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(result, Mapping):
        return False
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), Mapping) else {}
    states = {
        str(value or "").strip().lower()
        for value in (
            data.get("lifecycle_status"),
            data.get("lifecycleStatus"),
            data.get("status"),
            meta.get("lifecycle_status"),
            meta.get("lifecycleStatus"),
            meta.get("status"),
        )
        if str(value or "").strip()
    }
    return not states.intersection({"accepted", "processing", "pending", "queued", "in_progress"})
def _mgmt_nl_raise_command_idempotency_error(exc: Exception, *, display_key: str) -> None:
    if isinstance(exc, ManagementNlCommandPayloadConflict):
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {display_key!r} is bound to a different Management NL command",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        ) from exc
    if isinstance(exc, ManagementNlCommandRecoveryRequired):
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Management NL command outcome is uncertain",
            "The command will not be executed again until its prior outcome is reconciled.",
            precondition_failed="idempotency_recovery_required",
            suggestion="Inspect the durable conversation/provider audit and reconcile this key explicitly",
        ) from exc
    raise _bff_error(
        503,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        "Management NL command admission store is unavailable",
        str(exc),
        precondition_failed="management_nl_command_idempotency_store",
        suggestion="Restore the durable command idempotency volume before retrying",
    ) from exc
def _mgmt_nl_command_wait_seconds() -> float:
    raw = os.getenv("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_WAIT_SECONDS", "").strip()
    if raw:
        try:
            return max(float(raw), 0.01)
        except (TypeError, ValueError):
            pass
    provider_raw = os.getenv("PANTHEON_ASSISTANT_PROVIDER_TIMEOUT_SECONDS", "180").strip()
    try:
        provider_seconds = max(float(provider_raw), 0.1)
    except (TypeError, ValueError):
        provider_seconds = 180.0
    return provider_seconds + 10.0
def _mgmt_nl_command_poll_seconds() -> float:
    raw = os.getenv(
        "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_POLL_SECONDS",
        "0.05",
    ).strip()
    try:
        return min(max(float(raw), 0.005), 1.0)
    except (TypeError, ValueError):
        return 0.05
def _mgmt_nl_raise_command_wait_timeout() -> NoReturn:
    raise _bff_error(
        409,
        ErrorCode.IDEMPOTENCY_CONFLICT,
        "Management NL command is still in progress",
        "An exact concurrent request owns this idempotency key and has not reached a terminal result.",
        precondition_failed="idempotency_in_progress",
        suggestion="Retry the same payload and key after the current provider turn completes",
    )
def _mgmt_nl_use_case_admission_error(exc: Exception, display_key: str) -> NoReturn:
    _mgmt_nl_raise_command_idempotency_error(exc, display_key=display_key)
    raise AssertionError("unreachable")  # pragma: no cover - _raise always raises


def _mgmt_nl_command_idempotency_store() -> ManagementNlCommandIdempotencyStore:
    return get_mgmt_nl_command_idempotency_store()


# BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: the sole owner of Management NL
# durable command admission/replay/completion decision logic. Both
# bff_management_nl_ask and bff_management_nl_ask_stream call this single
# instance -- see services/control-plane/bff/assistant/management_service.py.
_MANAGEMENT_NL_USE_CASE = ManagementNlUseCase(
    ManagementNlUseCaseDeps(
        command_store=_mgmt_nl_command_idempotency_store,
        wait_seconds=_mgmt_nl_command_wait_seconds,
        poll_seconds=_mgmt_nl_command_poll_seconds,
        raise_admission_error=_mgmt_nl_use_case_admission_error,
        raise_wait_timeout=_mgmt_nl_raise_command_wait_timeout,
    )
)
async def _mgmt_nl_command_admit(
    *,
    scope: ManagementNlCommandScope,
    request_hash: str,
    display_key: str,
) -> tuple[Optional[ManagementNlCommandReservation], Optional[Dict[str, Any]]]:
    return await _MANAGEMENT_NL_USE_CASE.admit(
        scope=scope,
        request_hash=request_hash,
        display_key=display_key,
    )
async def _mgmt_nl_command_complete(
    reservation: Optional[ManagementNlCommandReservation],
    result: Dict[str, Any],
    *,
    display_key: str,
) -> None:
    await _MANAGEMENT_NL_USE_CASE.complete(reservation, result, display_key=display_key)
async def _mgmt_nl_command_mark_uncertain(
    reservation: Optional[ManagementNlCommandReservation],
    *,
    reason: str,
) -> None:
    await _MANAGEMENT_NL_USE_CASE.mark_uncertain(
        reservation,
        reason=reason,
        on_failure=lambda: log.exception("Failed to mark Management NL command reservation uncertain"),
    )

_MGMT_NL_PROVIDER_INLINE_GRACE_DEFAULT_SECONDS = 3.0
_MGMT_NL_STREAM_READ_TIMEOUT_DEFAULT_SECONDS = 30.0
_MGMT_NL_PROVIDER_FINALIZE_TASKS: Set["asyncio.Task[Any]"] = set()
def _mgmt_nl_provider_inline_grace_seconds() -> float:
    """Seconds POST /bff/management/nl/ask waits inline for the assistant provider
    before returning 202 with the deterministic answer and finishing the provider
    turn in the background. Override with
    PANTHEON_MANAGEMENT_NL_PROVIDER_INLINE_GRACE_SECONDS."""
    raw = os.getenv("PANTHEON_MANAGEMENT_NL_PROVIDER_INLINE_GRACE_SECONDS")
    if raw is None or not str(raw).strip():
        return _MGMT_NL_PROVIDER_INLINE_GRACE_DEFAULT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _MGMT_NL_PROVIDER_INLINE_GRACE_DEFAULT_SECONDS
    return value if value > 0 else _MGMT_NL_PROVIDER_INLINE_GRACE_DEFAULT_SECONDS
def _mgmt_nl_provider_inline_wait_seconds(_control_mode: Dict[str, Any]) -> float:
    """Product assistant turns never hold a development worktree lease."""

    return _mgmt_nl_provider_inline_grace_seconds()
def _mgmt_nl_stream_read_timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_MANAGEMENT_NL_STREAM_READ_TIMEOUT_SECONDS")
    if raw is None or not str(raw).strip():
        return _MGMT_NL_STREAM_READ_TIMEOUT_DEFAULT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _MGMT_NL_STREAM_READ_TIMEOUT_DEFAULT_SECONDS
    return value if value > 0 else _MGMT_NL_STREAM_READ_TIMEOUT_DEFAULT_SECONDS
def _mgmt_nl_sse_frame(payload: Any) -> str:
    if payload == "[DONE]":
        return "data: [DONE]\n\n"
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
def _mgmt_nl_json_response_payload(response: JSONResponse) -> Dict[str, Any]:
    raw = getattr(response, "body", b"") or b""
    if isinstance(raw, str):
        raw_text = raw
    else:
        raw_text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(raw_text) if raw_text else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
def _mgmt_nl_cached_result_sse_frames(
    cached: Optional[Dict[str, Any]],
    *,
    session_id: str,
    trace_id: str,
    message_id: str,
) -> Iterator[str]:
    """Render a durably-stored terminal Management NL result as the same
    meta/delta/done/[DONE] SSE frame shape a fresh provider turn would
    produce, for both control-command and provider-answer replays.

    BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: the SSE transport must not call
    the provider a second time for an exact-duplicate idempotency key -- a
    durable terminal result (found via ``_mgmt_nl_command_admit``) is
    replayed from here instead.
    """
    cached_data = cached.get("data") if isinstance(cached, dict) else {}
    cached_data = cached_data if isinstance(cached_data, dict) else {}
    answer = str(cached_data.get("answer") or "")
    provider_status = cached_data.get("provider_status") or cached_data.get("providerStatus") or {}
    ui_actions = cached_data.get("ui_actions") or cached_data.get("uiActions") or []
    command_kind = cached_data.get("control_command") or cached_data.get("controlCommand")
    audit_log = cached_data.get("audit_log") or cached_data.get("auditLog")
    conversation = cached_data.get("conversation")
    yield _mgmt_nl_sse_frame(
        {
            "type": "meta",
            "session_id": cached_data.get("session_id") or session_id,
            "trace_id": cached_data.get("trace_id") or trace_id,
            "message_id": cached_data.get("message_id") or message_id,
            "control_command": command_kind,
            "replayed": True,
        }
    )
    if answer:
        yield _mgmt_nl_sse_frame({"type": "delta", "text": answer})
    done_frame: Dict[str, Any] = {
        "type": "done",
        "text": answer,
        "provider_status": provider_status,
        "ui_actions": ui_actions,
        "control_command": command_kind,
        "replayed": True,
    }
    if command_kind:
        done_frame["audit_log"] = audit_log
        done_frame["conversation"] = conversation
    yield _mgmt_nl_sse_frame(done_frame)
    yield _mgmt_nl_sse_frame("[DONE]")
def _mgmt_nl_finalize_result(
    base_result: Dict[str, Any],
    *,
    answer: str,
    provider_status: Dict[str, Any],
    actions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Rewrite a processing nl/ask result into a completed one for the
    idempotency record once the provider answer is available."""
    completed_data = {
        **base_result.get("data", {}),
        "status": "completed",
        "lifecycle_status": "completed",
        "answer": answer,
        "provider_status": provider_status,
        "ui_actions": actions,
        "actions": actions,
    }
    completed_meta = {
        **base_result.get("meta", {}),
        "status": "completed",
        "lifecycle_status": "completed",
        "provider_status": provider_status,
    }
    return {**base_result, "data": completed_data, "meta": completed_meta}
async def _mgmt_nl_finalize_provider_turn(
    *,
    provider_task: "asyncio.Future[Any]",
    deterministic_answer: str,
    session_id: str,
    message_id: str,
    assistant_turn_id: str,
    trace_id: str,
    focus: str,
    resolved_key: str,
    audit_log_href: str,
    conversation_href: str,
    base_result: Dict[str, Any],
    command_reservation: Optional[ManagementNlCommandReservation] = None,
) -> None:
    """Finish a nl/ask exchange whose provider call exceeded the inline grace
    window: await the in-flight agent run, then append the assistant turn exactly
    once and rewrite the idempotency record from processing -> completed."""
    try:
        provider_answer, provider_status, actions = await provider_task
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("Management NL async provider turn failed", exc_info=True)
        provider_answer, actions = None, []
        provider_status = _mgmt_nl_provider_status(
            provider=_mgmt_nl_provider_name(),
            enabled=True,
            status="degraded",
            reason="provider_async_failed",
            run_id=trace_id,
        )
    answer = provider_answer or deterministic_answer
    try:
        _management_ai_record_event(
            {
                "event_type": "management_ai.exchange.completed",
                "session_id": session_id,
                "message_id": message_id,
                "assistant_turn_id": assistant_turn_id,
                "trace_id": trace_id,
                "route": "POST /bff/management/nl/ask",
                "answer": _management_ai_summary_value(answer),
                "provider_status": provider_status,
                "actions": actions,
                "action_count": len(actions),
                "async_finalized": True,
            }
        )
        _management_ai_append_turn(
            turn_id=assistant_turn_id,
            session_id=session_id,
            role="assistant",
            text=answer,
            created_at=utc_now(),
            trace_id=trace_id,
            provider_status=provider_status,
            ui_actions=actions,
        )
        _management_nl_publish_completed_events(
            session_id=session_id,
            message_id=message_id,
            assistant_turn_id=assistant_turn_id,
            trace_id=trace_id,
            focus=focus,
            provider_status=provider_status,
            action_count=len(actions),
            audit_log_href=audit_log_href,
            conversation_href=conversation_href,
        )
        final_result = _mgmt_nl_finalize_result(
            base_result,
            answer=answer,
            provider_status=provider_status,
            actions=actions,
        )
        await _mgmt_nl_command_complete(
            command_reservation,
            final_result,
            display_key=resolved_key,
        )
    except Exception:
        log.warning("Failed to persist async-finalised Management NL turn", exc_info=True)
        await _mgmt_nl_command_mark_uncertain(
            command_reservation,
            reason="async_provider_finalization_failed",
        )
def _mgmt_nl_schedule_provider_finalize(**kwargs: Any) -> None:
    task = asyncio.create_task(_mgmt_nl_finalize_provider_turn(**kwargs))
    _MGMT_NL_PROVIDER_FINALIZE_TASKS.add(task)
    task.add_done_callback(_MGMT_NL_PROVIDER_FINALIZE_TASKS.discard)
async def bff_management_nl_ask(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
):
    """Thin fail-closed wrapper: mark a held reservation uncertain exactly
    once if anything raises after admission granted ownership but before a
    terminal result was committed, so the key becomes retryable again only
    after the durable store's recovery window elapses instead of being
    silently dropped in a dangling ``in_progress`` state forever."""
    try:
        return await _bff_management_nl_ask_impl(
            payload=payload,
            authorization=authorization,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
            x_dry_run=x_dry_run,
        )
    except Exception:
        reservation = _MGMT_NL_COMMAND_RESERVATION_CONTEXT.get()
        if reservation is not None:
            await _mgmt_nl_command_mark_uncertain(
                reservation,
                reason="request_failed_before_terminal_commit",
            )
        raise
async def _bff_management_nl_ask_impl(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
):
    """BFF-B6-001/BFF-B6-003: POST /bff/management/nl/ask — Management NL query endpoint."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)

    question = _agora_required_text(payload, "question")
    _mgmt_nl_validate_question_size(question)
    control_command = _mgmt_nl_parse_control_command(question)

    # BFF-B6-003: high-risk refusal policy — must run before idempotency, surface
    # collection, session creation, or SSE emission.
    risk = None if control_command is not None else _mgmt_nl_high_risk_classify(question)
    if risk is not None:
        audit_id = _mgmt_nl_record_high_risk_refusal(
            identity=identity,
            question=question,
            risk=risk,
            recorded_at=utc_now(),
        )
        raise _bff_error(
            403,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "NL query matches high-risk action pattern and was refused by policy",
            (
                f"The question contains the pattern {risk['matched_pattern']!r} "
                f"which falls under the high-risk category '{risk['matched_category']}'. "
                "This endpoint is read-only and cannot execute management mutations."
            ),
            precondition_failed="high_risk_nl_policy",
            suggestion=risk["safe_alternatives"],
            details_extra={
                "refused": True,
                "matched_category": risk["matched_category"],
                "matched_pattern": risk["matched_pattern"],
                "safe_alternatives": risk["safe_alternatives"],
                "followups": _MGMT_NL_HIGH_RISK_REFUSAL_FOLLOWUPS,
                "audit_id": audit_id,
            },
        )

    # BFF-B6-001-SEC-FIX: resolve caller tenant scope before any retrieval.
    caller_tenant_id = _mgmt_nl_caller_tenant(
        identity,
        requested_tenant=_first_nonblank(x_tenant_id, x_pantheon_tenant),
    )

    operator_context = _mgmt_nl_trim_text(payload.get("context"), max_len=4000)
    focus = _mgmt_nl_normalize_focus(payload.get("focus"))
    client_conversation_hint = _mgmt_nl_normalize_conversation_context(payload.get("conversation"))
    ui_snapshot = _mgmt_nl_normalize_ui_context(payload.get("ui"), operator_context=operator_context)
    allowed_action_kinds = _mgmt_nl_allowed_action_kinds(ui_snapshot)

    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/management/nl/ask", "payload": payload})
    if _request_dry_run_requested(x_dry_run):
        return _dry_run_success_response(
            {
                "status": "accepted",
                "lifecycle_status": "accepted",
                "session_id": str(payload.get("session_id") or payload.get("sessionId") or ""),
                "message_id": "",
                "trace_id": str(payload.get("trace_id") or payload.get("traceId") or ""),
                "question": question,
                "focus": focus,
                "sources": [],
                "confidence": "dry_run",
            },
            status_code=202,
            idempotency_key=resolved_key,
            evidence_kind="ManagementNLQuery",
            extra_meta={
                "status": "accepted",
                "route": "POST /bff/management/nl/ask",
                "dry_run_mode": "compact_receipt",
            },
        )

    command_scope = _mgmt_nl_command_scope(
        actor_id=identity.operator_id,
        tenant_id=caller_tenant_id,
        resolved_key=resolved_key,
    )
    command_reservation, cached = await _mgmt_nl_command_admit(
        scope=command_scope,
        request_hash=request_hash,
        display_key=resolved_key,
    )
    _MGMT_NL_COMMAND_RESERVATION_CONTEXT.set(command_reservation)
    if cached is not None:
        cached_data = cached.get("data") if isinstance(cached, dict) else {}
        _management_ai_record_event(
            {
                "event_type": "management_ai.exchange.replayed",
                "session_id": str((cached_data or {}).get("session_id") or payload.get("session_id") or payload.get("sessionId") or ""),
                "message_id": str((cached_data or {}).get("message_id") or ""),
                "trace_id": str((cached_data or {}).get("trace_id") or (cached_data or {}).get("traceId") or ""),
                "actor_id": identity.operator_id,
                "focus": focus,
                "route": "POST /bff/management/nl/ask",
                "idempotency_key": resolved_key,
            }
        )
        return JSONResponse(status_code=202, content=_management_json_clone(cached))

    now = utc_now()
    session_id = str(payload.get("sessionId") or payload.get("session_id") or f"mgmt-nl-{uuid.uuid4().hex[:10]}")
    message_id = f"mnl-{uuid.uuid4().hex[:16]}"
    trace_id = str(payload.get("traceId") or payload.get("trace_id") or f"mnl-trace-{uuid.uuid4().hex[:12]}")
    if control_command is not None:
        control_response = _mgmt_nl_handle_control_command(
            control_command=control_command,
            payload=payload,
            identity=identity,
            caller_tenant_id=caller_tenant_id,
            focus=focus,
            ui_snapshot=ui_snapshot,
            resolved_key=resolved_key,
            session_id=session_id,
            message_id=message_id,
            trace_id=trace_id,
            now=now,
        )
        control_result = json.loads(control_response.body)
        await _mgmt_nl_command_complete(
            command_reservation,
            control_result,
            display_key=resolved_key,
        )
        return control_response

    control_mode = _assistant_control_mode_for_identity(
        identity,
        management_session_id=session_id,
        touch=True,
    )
    _mgmt_nl_reject_development_payload(
        payload,
        identity=identity,
        caller_tenant_id=caller_tenant_id,
        control_mode=control_mode,
    )
    _management_ai_ensure_session(
        session_id=session_id,
        identity=identity,
        tenant_id=caller_tenant_id,
        now=now,
        title=question,
    )
    user_attachments = _management_ai_store_attachments(
        attachments=payload.get("attachments"),
        session_id=session_id,
        turn_id=message_id,
    )
    _management_ai_append_turn(
        turn_id=message_id,
        session_id=session_id,
        role="user",
        text=question,
        created_at=now,
        trace_id=trace_id,
        attachments=user_attachments,
        ui_snapshot=ui_snapshot,
    )
    conversation_context = _management_ai_server_conversation_context(
        session_id=session_id,
        client_hint=client_conversation_hint,
    )
    current_user_attachments = [
        _management_ai_attachment_api_payload(item)
        for item in user_attachments
    ]

    # BFF-B6-001-SEC-FIX: pass tenant scope to context collection.
    # _mgmt_nl_collect_context fans out to several read surface port list_* calls,
    # each a blocking urllib HTTP request to runtime-manager (timeout 2s each). On
    # the single-worker BFF that blocks the event loop for seconds per request;
    # run it in a worker thread so concurrent requests (and the FE-BFF gate's
    # nl/ask burst) are not starved.
    context_bundle = await asyncio.to_thread(
        _mgmt_nl_collect_context, focus, now, tenant_id=caller_tenant_id
    )
    snippets = context_bundle["snippets"]
    surfaces = context_bundle["surfaces"]
    evidence_entities = context_bundle.get("evidence_entities") or set()
    evidence_source_types = context_bundle.get("evidence_source_types") or set()

    deterministic_answer = _mgmt_nl_synthesize_answer(question, snippets, focus)
    confidence = _mgmt_nl_surface_confidence(surfaces)
    source_keys = list(snippets.keys())
    context_pack = _mgmt_nl_build_context_pack(
        session_id=session_id,
        question=question,
        focus=focus,
        identity=identity,
        caller_tenant_id=caller_tenant_id,
        snippets=snippets,
        surfaces=surfaces,
        source_keys=source_keys,
        confidence=confidence,
        evidence_entities=evidence_entities,
        evidence_source_types=evidence_source_types,
        operator_context=operator_context,
        conversation_context=conversation_context,
        ui_snapshot=ui_snapshot,
        control_mode=control_mode,
    )

    try:
        nl_capabilities = _capabilities_for_identity(identity)
    except Exception:
        nl_capabilities = None
    raw_evidence_refs = list(
        await asyncio.to_thread(
            read_store.list_evidence_refs,
            tenant_id=caller_tenant_id,
            linked_entities=evidence_entities,
            source_types=evidence_source_types,
        )
        or []
    )
    for _eref in raw_evidence_refs:
        if isinstance(_eref, dict):
            _eid = str(_eref.get("ref_id") or _eref.get("id") or "").strip()
            if _eid:
                _eref.setdefault("href", f"/api/v1/knowledge/evidence/{_eid}")
    processed_evidence_refs, redacted_evidence_count = redact_evidence_refs(
        identity, raw_evidence_refs, capabilities=nl_capabilities
    )

    audit_ref = {
        "target_type": "ManagementNLExchange",
        "target_id": message_id,
        "href": f"/bff/audit/entities/ManagementNLExchange/{message_id}",
    }

    try:
        accepted_audit = await asyncio.to_thread(
            _record_agora_audit_event,
            {
                "action": "management.nl.ask.accepted",
                "targetType": "ManagementNLExchange",
                "targetId": message_id,
                "actorId": identity.operator_id,
                "recordedAt": now,
                "sessionId": session_id,
                "focus": focus,
                "tenantId": caller_tenant_id,
                "confidence": confidence,
                "sourceSurfaces": source_keys,
            },
        )
    except Exception:
        log.warning("Failed to record management NL happy-path audit event", exc_info=True)
        raise _bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Management NL audit write failed",
            "happy_path_audit_write_failed",
            precondition_failed="audit_write",
            suggestion="Retry after the Agora audit store is available",
        )

    audit_ref["audit_id"] = accepted_audit.get("auditId") or accepted_audit.get("eventId")

    _management_ai_record_event(
        {
            "event_type": "management_ai.exchange.accepted",
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "actor_id": identity.operator_id,
            "route": "POST /bff/management/nl/ask",
            "question": _management_ai_summary_value(question),
            "focus": focus,
            "tenant_id": caller_tenant_id,
            "confidence": confidence,
            "source_keys": source_keys,
            "context_pack_id": context_pack.get("context_pack_id"),
            "conversation_recent_turn_count": len(conversation_context.get("recent_turns") or []),
            "client_conversation_recent_turn_count": len(client_conversation_hint.get("recent_turns") or []),
            "conversation_summary_present": bool(conversation_context.get("summary")),
            "ui": ui_snapshot,
            "attachment_count": len(user_attachments),
            "available_ui_action_kinds": sorted(allowed_action_kinds),
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "control_mode": {
                "state": control_mode.get("state"),
                "active": control_mode.get("active"),
                "mode": control_mode.get("mode"),
                "activation_id": control_mode.get("activation_id") or control_mode.get("activationId"),
            },
            "surfaces": _management_ai_surface_summary(surfaces),
            "audit_ref": audit_ref,
        }
    )
    # _mgmt_nl_maybe_provider_answer issues a synchronous, blocking HTTP call to
    # the OpenClaw adapter (OpenClawOpsClient.invoke_assistant_provider), which
    # drives the Claude/Codex CLI agent and can take 30s+. The BFF runs a single
    # uvicorn worker, so calling it inline would block the event loop and freeze
    # every other request (reads, writes, SSE) for the whole agent turn. Offload
    # it to a worker thread so the event loop stays free to serve concurrently.
    assistant_turn_id = f"{message_id}-assistant"
    audit_log_href = _management_ai_audit_href(session_id=session_id, trace_id=trace_id)
    conversation_href = _management_ai_conversation_href(session_id)

    # The assistant-provider call (OpenClawOpsClient.invoke_assistant_provider via
    # _mgmt_nl_maybe_provider_answer) is a synchronous, blocking HTTP call that
    # drives a CLI agent and routinely takes 30s+. Run it in a worker thread and
    # wait only up to a short inline grace window. If it finishes in time we answer
    # synchronously as before; otherwise we return 202 immediately with the
    # deterministic answer and providerStatus=processing, and a background task
    # finalises the assistant turn + idempotency record once the agent completes.
    # asyncio.wait (unlike wait_for) does NOT cancel on timeout, so the in-flight
    # agent run is preserved and handed to the finaliser.
    provider_task = asyncio.create_task(
        asyncio.to_thread(
            _mgmt_nl_maybe_provider_answer,
            provider=_mgmt_nl_provider_name(),
            question=question,
            focus=focus,
            identity=identity,
            caller_tenant_id=caller_tenant_id,
            session_id=session_id,
            message_id=message_id,
            trace_id=trace_id,
            context_pack=context_pack,
            audit_id=audit_ref.get("audit_id"),
            allowed_action_kinds=allowed_action_kinds,
            current_user_attachments=current_user_attachments,
        )
    )
    done, _ = await asyncio.wait(
        {provider_task}, timeout=_mgmt_nl_provider_inline_wait_seconds(control_mode)
    )
    provider_pending = provider_task not in done
    if provider_pending:
        provider_answer, actions = None, []
        provider_status = _mgmt_nl_provider_status(
            provider=_mgmt_nl_provider_name(),
            enabled=True,
            status="processing",
            reason="provider_async_pending",
            run_id=trace_id,
        )
    else:
        # Preserve the previous inline-await exception behaviour.
        provider_answer, provider_status, actions = provider_task.result()
    answer = provider_answer or deterministic_answer

    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "management.nl.ask.accepted",
        {"session_id": session_id, "message_id": message_id, "trace_id": trace_id, "focus": focus},
    )

    exchange_status = "processing" if provider_pending else "completed"
    result = {
        "status": "accepted",
        "data": {
            "status": exchange_status,
            "lifecycle_status": exchange_status,
            "answer": answer,
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "question": question,
            "focus": focus,
            "sources": source_keys,
            "confidence": confidence,
            "summary_context": snippets,
            "context_pack": context_pack,
            "provider_status": provider_status,
            "control_mode": control_mode,
            "ui_actions": actions,
            "actions": actions,
            "audit_ref": audit_ref,
            "audit_log": {
                "href": audit_log_href,
                "trace_id": trace_id,
            },
            "conversation": {
                "href": conversation_href,
                "session_id": session_id,
                "trace_id": trace_id,
            },
            "session": {
                "session_id": session_id,
                "ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            },
            "evidence_refs": processed_evidence_refs,
        },
        "meta": {
            "status": exchange_status,
            "lifecycle_status": exchange_status,
            "snapshot_at": now,
            "surfaces": surfaces,
            "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
            "provider_status": provider_status,
            "trace_id": trace_id,
            "context_pack_id": context_pack.get("context_pack_id"),
            "redacted_evidence_count": redacted_evidence_count,
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "control_mode": control_mode,
        },
    }
    _management_ai_record_event(
        {
            "event_type": "management_ai.exchange.completed",
            "session_id": session_id,
            "message_id": message_id,
            "assistant_turn_id": assistant_turn_id,
            "trace_id": trace_id,
            "actor_id": identity.operator_id,
            "route": "POST /bff/management/nl/ask",
            "answer": _management_ai_summary_value(answer),
            "provider_status": provider_status,
            "actions": actions,
            "action_count": len(actions),
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "control_mode": {
                "state": control_mode.get("state"),
                "active": control_mode.get("active"),
                "mode": control_mode.get("mode"),
                "activation_id": control_mode.get("activation_id") or control_mode.get("activationId"),
            },
            "fallback": provider_status.get("fallback"),
        }
    )
    if not provider_pending:
        _management_ai_append_turn(
            turn_id=assistant_turn_id,
            session_id=session_id,
            role="assistant",
            text=answer,
            created_at=utc_now(),
            trace_id=trace_id,
            provider_status=provider_status,
            ui_actions=actions,
        )
    _management_nl_publish_completed_events(
        session_id=session_id,
        message_id=message_id,
        assistant_turn_id=assistant_turn_id,
        trace_id=trace_id,
        focus=focus,
        provider_status=provider_status,
        action_count=len(actions),
        audit_log_href=audit_log_href,
        conversation_href=conversation_href,
    )
    if not provider_pending:
        await _mgmt_nl_command_complete(
            command_reservation,
            result,
            display_key=resolved_key,
        )
    if provider_pending:
        # The assistant turn was intentionally NOT persisted above: the store's
        # append_turn is not an upsert, so writing a placeholder here would leave
        # a duplicate turn once the real answer lands. The finaliser appends it
        # exactly once with the real provider answer and rewrites the idempotency
        # record from processing -> completed.
        _mgmt_nl_schedule_provider_finalize(
            provider_task=provider_task,
            deterministic_answer=deterministic_answer,
            session_id=session_id,
            message_id=message_id,
            assistant_turn_id=assistant_turn_id,
            trace_id=trace_id,
            focus=focus,
            resolved_key=resolved_key,
            audit_log_href=audit_log_href,
            conversation_href=conversation_href,
            base_result=result,
            command_reservation=command_reservation,
        )
    return JSONResponse(status_code=202, content=result)
async def bff_management_nl_ask_stream(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
):
    """Thin fail-closed wrapper mirroring ``bff_management_nl_ask``: mark a
    held reservation uncertain exactly once if anything raises, while
    building the response, after admission granted ownership but before a
    terminal result was committed. (Failures once the SSE body itself is
    streaming are handled inline inside the generator.)"""
    try:
        return await _bff_management_nl_ask_stream_impl(
            payload=payload,
            authorization=authorization,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
        )
    except Exception:
        reservation = _MGMT_NL_COMMAND_RESERVATION_CONTEXT.get()
        if reservation is not None:
            await _mgmt_nl_command_mark_uncertain(
                reservation,
                reason="request_failed_before_terminal_commit",
            )
        raise
async def _bff_management_nl_ask_stream_impl(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
):
    """SSE-streaming variant of /bff/management/nl/ask.

    BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: this transport now shares the
    exact same durable command admission/replay decision logic as
    ``bff_management_nl_ask`` (via ``_mgmt_nl_command_admit`` /
    ``_MANAGEMENT_NL_USE_CASE``) -- same ordering (identity/role ->
    question validation -> control-command parse -> high-risk refusal ->
    tenant resolution -> admission -> session/context/provider), same
    canonical command scope, same fail-closed 503 on storage loss, and the
    same "exactly one provider effect per idempotency key" guarantee. A
    concurrent/duplicate request against the same key does not invoke the
    provider a second time -- it durably replays the terminal answer as SSE
    frames instead.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)

    question = _agora_required_text(payload, "question")
    _mgmt_nl_validate_question_size(question)
    control_command = _mgmt_nl_parse_control_command(question)

    risk = None if control_command is not None else _mgmt_nl_high_risk_classify(question)
    if risk is not None:
        raise _bff_error(
            403,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "NL query matches high-risk action pattern and was refused by policy",
            "This endpoint is read-only and cannot execute management mutations.",
            precondition_failed="high_risk_nl_policy",
            suggestion=risk["safe_alternatives"],
            details_extra={"refused": True, "matched_category": risk["matched_category"]},
        )

    caller_tenant_id = _mgmt_nl_caller_tenant(
        identity, requested_tenant=_first_nonblank(x_tenant_id, x_pantheon_tenant)
    )
    operator_context = _mgmt_nl_trim_text(payload.get("context"), max_len=4000)
    focus = _mgmt_nl_normalize_focus(payload.get("focus"))
    now = utc_now()
    session_id = str(payload.get("sessionId") or payload.get("session_id") or f"mgmt-nl-{uuid.uuid4().hex[:10]}")
    trace_id = str(payload.get("traceId") or payload.get("trace_id") or f"mnl-trace-{uuid.uuid4().hex[:12]}")
    message_id = f"mnl-{uuid.uuid4().hex[:12]}"
    ui_snapshot = _mgmt_nl_normalize_ui_context(payload.get("ui"), operator_context=operator_context)

    # BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: admission happens once, for both
    # the control-command and provider-answer paths, before either does any
    # work -- exactly mirroring bff_management_nl_ask's ordering.
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/management/nl/ask", "payload": payload})
    command_scope = _mgmt_nl_command_scope(
        actor_id=identity.operator_id,
        tenant_id=caller_tenant_id,
        resolved_key=resolved_key,
    )
    command_reservation, cached = await _mgmt_nl_command_admit(
        scope=command_scope,
        request_hash=request_hash,
        display_key=resolved_key,
    )
    _MGMT_NL_COMMAND_RESERVATION_CONTEXT.set(command_reservation)
    if cached is not None:
        _management_ai_record_event(
            {
                "event_type": "management_ai.exchange.replayed",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "actor_id": identity.operator_id,
                "focus": focus,
                "route": "POST /bff/management/nl/ask/stream",
                "idempotency_key": resolved_key,
            }
        )
        return StreamingResponse(
            _mgmt_nl_cached_result_sse_frames(
                cached, session_id=session_id, trace_id=trace_id, message_id=message_id
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    if control_command is not None:
        control_response = _mgmt_nl_handle_control_command(
            control_command=control_command,
            payload=payload,
            identity=identity,
            caller_tenant_id=caller_tenant_id,
            focus=focus,
            ui_snapshot=ui_snapshot,
            resolved_key=resolved_key,
            session_id=session_id,
            message_id=message_id,
            trace_id=trace_id,
            now=now,
        )
        control_result = json.loads(control_response.body)
        await _mgmt_nl_command_complete(
            command_reservation,
            control_result,
            display_key=resolved_key,
        )

        return StreamingResponse(
            _mgmt_nl_cached_result_sse_frames(
                control_result, session_id=session_id, trace_id=trace_id, message_id=message_id
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    control_mode = _assistant_control_mode_for_identity(identity, management_session_id=session_id, touch=True)
    conversation_context = _management_ai_server_conversation_context(
        session_id=session_id,
        client_hint=_mgmt_nl_normalize_conversation_context(payload.get("conversation")),
    )
    context_bundle = await asyncio.to_thread(
        _mgmt_nl_collect_context, focus, now, tenant_id=caller_tenant_id
    )
    snippets = context_bundle["snippets"]
    surfaces = context_bundle["surfaces"]
    confidence = _mgmt_nl_surface_confidence(surfaces)
    context_pack = _mgmt_nl_build_context_pack(
        session_id=session_id,
        question=question,
        focus=focus,
        identity=identity,
        caller_tenant_id=caller_tenant_id,
        snippets=snippets,
        surfaces=surfaces,
        source_keys=list(snippets.keys()),
        confidence=confidence,
        evidence_entities=context_bundle.get("evidence_entities") or set(),
        evidence_source_types=context_bundle.get("evidence_source_types") or set(),
        operator_context=operator_context,
        conversation_context=conversation_context,
        ui_snapshot=ui_snapshot,
        control_mode=control_mode,
    )
    prompt = _mgmt_nl_provider_prompt(question=question, focus=focus, context_pack=context_pack)
    provider_mode = _mgmt_nl_provider_mode_from_context(context_pack)

    _management_ai_ensure_session(
        session_id=session_id, identity=identity, tenant_id=caller_tenant_id, now=now, title=question
    )
    _management_ai_append_turn(
        turn_id=message_id, session_id=session_id, role="user", text=question, created_at=now, trace_id=trace_id
    )

    _MGMT_NL_STREAM_EXHAUSTED = object()

    async def event_stream() -> AsyncGenerator[str, None]:
        provider_run_id = trace_id
        provider_started = time.monotonic()
        _management_ai_record_event(
            {
                "event_type": "management_ai.provider.started",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "provider_run_id": provider_run_id,
                "actor_id": identity.operator_id,
                "provider": "openclaw",
                "route": _management_ai_provider_route("openclaw", stream=True),
                "context_pack_id": context_pack.get("context_pack_id"),
                "mode": provider_mode,
                "prompt_bytes": len(prompt.encode("utf-8")),
            }
        )
        yield _mgmt_nl_sse_frame(
            {
                "type": "meta", "session_id": session_id,
                "trace_id": trace_id, "message_id": message_id,
            }
        )
        chunks: List[str] = []
        final_text: Optional[str] = None
        final_event: Dict[str, Any] = {}
        had_error = False
        failure_event: Optional[Dict[str, Any]] = None
        try:
            # BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: this generator is now
            # async (so it can await the shared command-completion calls
            # exactly once below), but OpenClawOpsClient.stream_assistant_provider
            # is a synchronous, blocking generator. Drive it one item at a
            # time in a worker thread via asyncio.to_thread(next, ...) so the
            # event loop stays free between deltas instead of being blocked
            # for the whole provider turn, while preserving the exact
            # per-event streaming behaviour below.
            provider_iter = OpenClawOpsClient().stream_assistant_provider(
                mode=provider_mode,
                prompt=prompt,
                context_pack=context_pack,
                operator_id=identity.operator_id,
                trace_id=trace_id,
                session_user=session_id,
                read_timeout_seconds=_mgmt_nl_stream_read_timeout_seconds(),
            )
            while True:
                evt = await asyncio.to_thread(next, provider_iter, _MGMT_NL_STREAM_EXHAUSTED)
                if evt is _MGMT_NL_STREAM_EXHAUSTED:
                    break
                if evt.get("type") == "delta":
                    chunks.append(str(evt.get("text") or ""))
                elif evt.get("type") == "done":
                    final_text = str(evt.get("text") or "")
                    final_event = dict(evt)
                    # Only emit the BFF's filtered, persisted completion below.
                    continue
                elif evt.get("type") == "error":
                    had_error = True
                    failure_event = {
                        "event_type": "management_ai.provider.failed",
                        "session_id": session_id,
                        "message_id": message_id,
                        "trace_id": trace_id,
                        "provider_run_id": provider_run_id,
                        "actor_id": identity.operator_id,
                        "provider": "openclaw",
                        "mode": provider_mode,
                        "duration_ms": max(0, int((time.monotonic() - provider_started) * 1000)),
                        "status_code": evt.get("status_code"),
                        "error_code": evt.get("error_code") or "OPENCLAW_STREAM_ERROR",
                        "error_message": _management_ai_summary_value(evt.get("message")),
                    }
                yield _mgmt_nl_sse_frame(evt)
        except OpenClawOpsClientError as exc:
            had_error = True
            failure_event = {
                "event_type": "management_ai.provider.failed",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "provider_run_id": provider_run_id,
                "actor_id": identity.operator_id,
                "provider": "openclaw",
                "mode": provider_mode,
                "duration_ms": max(0, int((time.monotonic() - provider_started) * 1000)),
                "status_code": exc.status_code,
                "error_code": exc.error_code,
                "error_message": _management_ai_summary_value(exc.message),
            }
            yield _mgmt_nl_sse_frame(
                {"type": "error", "error_code": exc.error_code, "message": exc.message}
            )
        except Exception as exc:  # noqa: BLE001
            had_error = True
            failure_event = {
                "event_type": "management_ai.provider.failed",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "provider_run_id": provider_run_id,
                "actor_id": identity.operator_id,
                "provider": "openclaw",
                "mode": provider_mode,
                "duration_ms": max(0, int((time.monotonic() - provider_started) * 1000)),
                "status_code": 500,
                "error_code": "BFF_STREAM_ERROR",
                "error_message": _management_ai_summary_value(str(exc)[:200]),
            }
            yield _mgmt_nl_sse_frame(
                {"type": "error", "error_code": "BFF_STREAM_ERROR", "message": str(exc)[:200]}
            )
        raw_answer = (final_text or "").strip() or "".join(chunks).strip()
        answer = _mgmt_nl_text_from_provider_value(_mgmt_nl_jsonish(raw_answer)) or raw_answer
        if not final_event and not had_error:
            had_error = True
            failure_event = {
                "event_type": "management_ai.provider.failed",
                "session_id": session_id, "message_id": message_id, "trace_id": trace_id,
                "provider_run_id": provider_run_id, "actor_id": identity.operator_id,
                "provider": "openclaw", "mode": provider_mode,
                "error_code": "OPENCLAW_STREAM_INCOMPLETE",
                "error_message": "Provider stream ended without a terminal result.",
            }
            yield _mgmt_nl_sse_frame({
                "type": "error", "error_code": failure_event["error_code"],
                "message": failure_event["error_message"],
            })
        if answer and not had_error:
            actions = _mgmt_nl_extract_provider_actions(
                {**final_event, "text": raw_answer},
                allowed_action_kinds=_mgmt_nl_allowed_action_kinds(ui_snapshot),
            )
            duration_ms = max(0, int((time.monotonic() - provider_started) * 1000))
            _management_ai_record_event(
                {
                    "event_type": "management_ai.provider.completed",
                    "session_id": session_id,
                    "message_id": message_id,
                    "trace_id": trace_id,
                    "provider_run_id": provider_run_id,
                    "actor_id": identity.operator_id,
                    "provider": "openclaw",
                    "provider_state": "completed",
                    "action_count": len(actions),
                    "mode": provider_mode,
                    "duration_ms": duration_ms,
                    "output_summary": {
                        "model": "openclaw/main",
                        "transport": "responses_http",
                        "output_bytes": len(answer.encode("utf-8")),
                    },
                }
            )
            provider_status = {
                "provider": "openclaw",
                "used": True,
                "status": "completed",
                "transport": "responses_http",
            }
            _management_ai_append_turn(
                turn_id=f"{message_id}-assistant",
                session_id=session_id,
                role="assistant",
                text=answer,
                created_at=utc_now(),
                trace_id=trace_id,
                provider_status=provider_status,
                ui_actions=actions,
            )
            yield _mgmt_nl_sse_frame({
                "type": "done", "text": answer,
                "provider_status": provider_status, "ui_actions": actions,
            })
            # BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: complete the durable
            # reservation exactly once, from the one code path that actually
            # observed the terminal provider outcome, so a reconnect/retry
            # with the same Idempotency-Key durably replays this answer
            # instead of invoking the provider again.
            stream_result = {
                "status": "accepted",
                "data": {
                    "status": "completed",
                    "lifecycle_status": "completed",
                    "answer": answer,
                    "session_id": session_id,
                    "message_id": message_id,
                    "trace_id": trace_id,
                    "provider_status": provider_status,
                    "ui_actions": actions,
                    "actions": actions,
                },
                "meta": {
                    "status": "completed",
                    "lifecycle_status": "completed",
                    "provider_status": provider_status,
                    "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                },
            }
            await _mgmt_nl_command_complete(
                command_reservation,
                stream_result,
                display_key=resolved_key,
            )
        else:
            if failure_event is not None:
                _management_ai_record_event(failure_event)
            # A non-terminal/failed provider turn must not be cached as a
            # false-positive "completed" result and must not be silently
            # retried on the same key either -- mark the reservation
            # uncertain so it becomes retryable again only after the store's
            # recovery window elapses.
            await _mgmt_nl_command_mark_uncertain(
                command_reservation,
                reason=(failure_event or {}).get("error_code") or "stream_provider_incomplete",
            )
        yield _mgmt_nl_sse_frame("[DONE]")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )

# ---------------------------------------------------------------------------
# Module getattr fallback to resolve bff_main globals dynamically
# ---------------------------------------------------------------------------
def __getattr__(name: str) -> Any:
    bff_main = sys.modules.get("services.control_plane.bff.main") or sys.modules.get("main")
    if bff_main is not None and hasattr(bff_main, name):
        return getattr(bff_main, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# Public exports & aliases
# ---------------------------------------------------------------------------
MANAGEMENT_NL_COMMAND_ROUTE = _MGMT_NL_COMMAND_ROUTE
MGMT_NL_COMMAND_RESERVATION_CONTEXT = _MGMT_NL_COMMAND_RESERVATION_CONTEXT
MGMT_NL_PROVIDER_FINALIZE_TASKS = _MGMT_NL_PROVIDER_FINALIZE_TASKS
MANAGEMENT_NL_USE_CASE = _MANAGEMENT_NL_USE_CASE
mgmt_nl_command_scope = _mgmt_nl_command_scope
mgmt_nl_command_admit = _mgmt_nl_command_admit
mgmt_nl_command_complete = _mgmt_nl_command_complete
mgmt_nl_command_mark_uncertain = _mgmt_nl_command_mark_uncertain
mgmt_nl_cached_result_sse_frames = _mgmt_nl_cached_result_sse_frames
mgmt_nl_sse_frame = _mgmt_nl_sse_frame
mgmt_nl_json_response_payload = _mgmt_nl_json_response_payload
mgmt_nl_finalize_result = _mgmt_nl_finalize_result
mgmt_nl_finalize_provider_turn = _mgmt_nl_finalize_provider_turn
mgmt_nl_schedule_provider_finalize = _mgmt_nl_schedule_provider_finalize
mgmt_nl_provider_inline_grace_seconds = _mgmt_nl_provider_inline_grace_seconds
mgmt_nl_provider_inline_wait_seconds = _mgmt_nl_provider_inline_wait_seconds
mgmt_nl_stream_read_timeout_seconds = _mgmt_nl_stream_read_timeout_seconds

