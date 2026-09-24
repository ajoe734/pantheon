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

from urllib.parse import quote, urlencode
import hashlib
import base64

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
from ..models import ErrorCode, OperatorIdentity, SseEventEnvelope, utc_now
from .context_composer import AssistantCollectedSource, compose_context_pack
from .control_mode import (
    CONTROL_MODE_CAPABILITY_PREFIX,
    CONTROL_MODE_ROLES,
    ControlModeError,
    ControlModeStore,
    actor_capabilities,
    actor_has_control_role,
    actor_has_kernel_capability,
    default_idle_ttl,
)
from .mode_policy import (
    DEFAULT_KERNEL_TTL_SECONDS,
    ModePolicyViolation,
    assert_kernel_allowed,
)

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
_management_ai_store_attachments = management_ai_store_attachments

from ..openclaw_ops_client import (
    OpenClawOpsClient as _DefaultOpenClawOpsClient,
    OpenClawOpsClientError as _DefaultOpenClawOpsClientError,
)

_extract_identity = auth_policy.extract_identity
_require_read_role = auth_policy.require_read_role
_bff_error = auth_policy.bff_error
_first_nonblank = auth_policy.first_nonblank
_capabilities_for_identity = auth_policy.capabilities_for_identity
from ..models import redact_evidence_refs


def _reject_body_idempotency_key(payload: Dict[str, Any]) -> None:
    for key in ("idempotency_key", "idempotencyKey"):
        if payload and key in payload and payload[key] is not None:
            raise _bff_error(
                422,
                ErrorCode.INVALID_HEADER,
                "Idempotency-Key must be supplied via HTTP header, not request body",
                precondition_failed="idempotency_header_required",
            )


def _agora_required_text(payload: Dict[str, Any], *fields: str) -> str:
    for field in fields:
        clean = str(payload.get(field) or "").strip()
        if clean:
            return clean
    label = fields[0] if fields else "value"
    raise _bff_error(
        422,
        ErrorCode.VALIDATION_FAILED,
        f"{label} is required",
        f"Agora request requires a non-empty {label}",
        precondition_failed=label,
    )


_MGMT_AI_SESSION_TTL_SECONDS = DEFAULT_MANAGEMENT_AI_SESSION_TTL_SECONDS
_MGMT_NL_HIGH_RISK_REFUSAL_FOLLOWUPS = [
    "Review recent incidents via /bff/incidents",
    "View active alerts via /bff/sse/alerts",
    "Inspect governance review queue via /bff/management/reviews",
]

_sse_buffers: Dict[str, deque] = {"ask": deque(maxlen=500), "approval": deque(maxlen=500)}
_sse_subscribers: Dict[str, list[asyncio.Queue]] = {"ask": [], "approval": []}

# ---------------------------------------------------------------------------
# DI Seams: read_store, OpenClawOpsClient, OpenClawOpsClientError
# Pattern follows set_management_ai_conversation_store at line 92.
# ---------------------------------------------------------------------------

_READ_STORE: Optional[Any] = None


def get_read_store() -> Any:
    global _READ_STORE
    if _READ_STORE is None:
        try:
            from ..ports import create_in_memory_read_surface_ports
            _READ_STORE = create_in_memory_read_surface_ports()
        except Exception:
            pass
    return _READ_STORE


def set_read_store(store: Optional[Any]) -> None:
    global _READ_STORE
    _READ_STORE = store


def reset_read_store() -> None:
    global _READ_STORE
    _READ_STORE = None


class _ReadStoreProxy:
    """Proxy object so direct module-level references to `read_store` resolve to `get_read_store()`."""

    def __getattr__(self, name: str) -> Any:
        target = get_read_store()
        if target is None:
            raise RuntimeError("read_store is not configured in management_service")
        return getattr(target, name)


read_store = _ReadStoreProxy()

_OPENCLAW_OPS_CLIENT: Optional[Any] = None


def get_openclaw_ops_client() -> Any:
    global _OPENCLAW_OPS_CLIENT
    if _OPENCLAW_OPS_CLIENT is not None:
        return _OPENCLAW_OPS_CLIENT
    return _DefaultOpenClawOpsClient


def set_openclaw_ops_client(client_or_cls: Optional[Any]) -> None:
    global _OPENCLAW_OPS_CLIENT
    _OPENCLAW_OPS_CLIENT = client_or_cls


def reset_openclaw_ops_client() -> None:
    global _OPENCLAW_OPS_CLIENT
    _OPENCLAW_OPS_CLIENT = None


class _OpenClawOpsClientProxy:
    """Proxy callable so direct `OpenClawOpsClient(...)` calls instantiate via `get_openclaw_ops_client()`."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        target = get_openclaw_ops_client()
        if callable(target):
            return target(*args, **kwargs)
        return target

    def __getattr__(self, name: str) -> Any:
        target = get_openclaw_ops_client()
        return getattr(target, name)


OpenClawOpsClient = _OpenClawOpsClientProxy()

_OPENCLAW_OPS_CLIENT_ERROR: type[Exception] = _DefaultOpenClawOpsClientError


def get_openclaw_ops_client_error() -> type[Exception]:
    global _OPENCLAW_OPS_CLIENT_ERROR
    return _OPENCLAW_OPS_CLIENT_ERROR


def set_openclaw_ops_client_error(err_cls: Optional[type[Exception]]) -> None:
    global _OPENCLAW_OPS_CLIENT_ERROR
    _OPENCLAW_OPS_CLIENT_ERROR = err_cls if err_cls is not None else _DefaultOpenClawOpsClientError


def reset_openclaw_ops_client_error() -> None:
    global _OPENCLAW_OPS_CLIENT_ERROR
    _OPENCLAW_OPS_CLIENT_ERROR = _DefaultOpenClawOpsClientError


OpenClawOpsClientError = _DefaultOpenClawOpsClientError



# ---------------------------------------------------------------------------
# Extracted Management NL Helpers & Constants (BFF-MGMT-NL-HELPER-EXTRACTION-001)
# ---------------------------------------------------------------------------

_REMAINING_MAIN_HELPERS = [
    "_mgmt_nl_validate_question_size",
    "_mgmt_nl_parse_control_command",
    "_mgmt_nl_high_risk_classify",
    "_mgmt_nl_record_high_risk_refusal",
    "_mgmt_nl_caller_tenant",
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
    "_mgmt_nl_provider_prompt",
    "_mgmt_nl_surface_confidence",
]


_MGMT_NL_VALID_FOCUS = {"cockpit", "trading_pulse", "portfolio", "persona_fleet", "all"}
_MGMT_NL_FOCUS_ALIASES = {
    "persona": "persona_fleet",
    "personas": "persona_fleet",
    "runtime": "trading_pulse",
    "runtimes": "trading_pulse",
}
_MGMT_NL_MAX_QUESTION_BYTES = 2048
_MGMT_NL_MAX_RECENT_TURNS = 12
_MGMT_NL_FE_RECENT_TURNS_CHAR_BUDGET = 32 * 1024
_MGMT_NL_PROVIDER_HISTORY_CHAR_BUDGET = 64 * 1024
_MGMT_NL_UI_ACTION_KINDS = {
    "navigate",
    "openDrawer",
    "selectEntity",
    "setFilter",
    "focusPanel",
    "refreshCurrentView",
    "runBffAction",
}
_MGMT_NL_WRITE_ACTION_KINDS = {"runBffAction"}
_MGMT_NL_CONTROL_REDACTED_QUESTION = "[CONTROL MODE COMMAND REDACTED]"
_MGMT_NL_CONTROL_ACTIVATE_PREFIXES = (
    "/control",
    "/kernel",
    "control mode",
    "kernel mode",
    "控制模式",
    "啟動控制模式",
    "启动控制模式",
    "開啟控制模式",
    "开启控制模式",
    "暗號",
    "暗号",
    "通關密語",
    "通关密语",
)
_MGMT_NL_CONTROL_SEPARATOR_ACTIVATE_PREFIXES = {
    "control mode",
    "kernel mode",
    "控制模式",
    "暗號",
    "暗号",
    "通關密語",
    "通关密语",
}
_MGMT_NL_CONTROL_STATUS_COMMANDS = {
    "/control status",
    "/kernel status",
    "control mode status",
    "kernel mode status",
    "控制模式狀態",
    "控制模式状态",
    "查看控制模式",
}
_MGMT_NL_CONTROL_DEACTIVATE_COMMANDS = {
    "/control off",
    "/control stop",
    "/control deactivate",
    "/kernel off",
    "/kernel stop",
    "/kernel deactivate",
    "control mode off",
    "kernel mode off",
    "退出控制模式",
    "關閉控制模式",
    "关闭控制模式",
    "停用控制模式",
}

_MGMT_NL_HIGH_RISK_REFUSAL_FOLLOWUPS = [
    {
        "label": "Open Human Inbox",
        "route": "/bff/management/human-inbox",
        "rel": "human_inbox",
    }
]

_MGMT_NL_HIGH_RISK_PATTERNS: List[tuple[str, List[str], str]] = [
    # (category_key, trigger_terms, safe_alternatives_hint)
    (
        "live_capital_mutation",
        [
            "allocate capital", "transfer capital", "move capital", "reallocate capital",
            "transfer funds", "move funds", "allocate funds", "withdraw funds",
            "increase allocation", "decrease allocation", "change allocation",
            "rebalance capital", "rebalance portfolio", "set capital", "add capital",
            "remove capital", "fund the pool", "capital injection", "execute trade",
            "place trade", "place order", "buy shares", "sell shares", "liquidate position",
            "配置資金", "轉移資金", "資金轉移", "調倉", "加倉", "減倉", "下單", "買入", "賣出",
        ],
        "Use POST /bff/capital-pools/{id} or the governance approval flow to mutate capital allocations.",
    ),
    (
        "broker_activation",
        [
            "enable live broker", "enable broker", "connect broker", "activate broker",
            "enable the live broker", "connect the live broker", "activate the live broker",
            "start broker", "enable shioaji", "connect shioaji", "enable ibkr",
            "connect ibkr", "enable pantheon_live_broker", "set pantheon_live_broker",
            "turn on broker", "activate live trading", "inject broker credentials",
            "啟用實盤", "開啟實盤", "啟用券商", "連接券商", "啟用 live broker",
        ],
        "Live broker activation requires operator dual-signoff via the human gate. Use PROD-WRITES-001-V2.",
    ),
    (
        "strategy_deployment",
        [
            "deploy strategy", "retire strategy", "promote strategy", "activate strategy",
            "redeploy strategy", "undeploy strategy",
            "rollback strategy", "deprecate strategy", "publish strategy",
            "make strategy live", "push strategy", "deactivate strategy",
            "部署策略", "上線策略", "發布策略", "回滾策略", "停用策略",
        ],
        "Use POST /bff/strategies/{id}/actions with a confirm-token for strategy lifecycle changes.",
    ),
    (
        "persona_activation",
        [
            "activate persona", "deploy persona", "enable persona", "launch persona",
            "start persona", "run persona", "make persona live", "promote persona",
            "deactivate persona", "disable persona", "stop persona",
            "啟用 persona", "啟動 persona", "上線 persona", "部署 persona", "停用 persona",
        ],
        "Use POST /bff/personas/{id}/actions with a confirm-token for persona lifecycle changes.",
    ),
    (
        "runtime_control",
        [
            "restart runtime", "stop runtime", "start runtime", "kill runtime",
            "pause runtime", "resume runtime", "terminate runtime", "shut down runtime",
            "shutdown runtime", "reset runtime", "reboot runtime", "bring runtime back",
            "重啟 runtime", "停止 runtime", "啟動 runtime", "暫停 runtime", "恢復 runtime",
        ],
        "Use POST /bff/runtimes/{id}/actions with appropriate governance gates for runtime control.",
    ),
    (
        "system_mutation",
        [
            "enable live", "disable live", "toggle feature", "enable feature flag",
            "disable feature flag", "set feature flag", "change feature flag",
            "modify production", "update production config", "change production",
            "enable production writes", "disable production writes",
            "set vite_bff_real_writes", "set pantheon_env",
            "切換功能", "更改 production", "修改 production", "開啟 production writes",
        ],
        "System-wide mutations require operator gate approval. Use the appropriate governance route.",
    ),
]

_ALERT_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_ALERT_CATEGORY_ORDER = {"incident": 4, "kill_switch": 3, "governance": 2, "runtime": 1}

def _max_alert_severity(values: List[Optional[str]]) -> Optional[str]:
    ranked = [v.lower() for v in values if v and v.lower() in _ALERT_SEVERITY_ORDER]
    if not ranked:
        return None
    return max(ranked, key=lambda s: _ALERT_SEVERITY_ORDER.get(s, 0))

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

def _truthy_header(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

_REQUEST_DRY_RUN_CONTEXT: ContextVar[bool] = ContextVar("request_dry_run_context", default=False)

def _request_dry_run_requested(explicit_header: Optional[str] = None) -> bool:
    return _truthy_header(explicit_header) or bool(_REQUEST_DRY_RUN_CONTEXT.get())

def _dry_run_success_response(
    data: Dict[str, Any],
    *,
    status_code: int = 200,
    snapshot_at: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    evidence_kind: Optional[str] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at or utc_now(),
        "dryRun": True,
        "durable": False,
        "liveCapitalSideEffects": False,
    }
    if idempotency_key:
        meta["idempotency"] = {
            "key": idempotency_key,
            "idempotencyKey": idempotency_key,
            "replayed": False,
        }
    if evidence_kind:
        meta["evidenceKind"] = evidence_kind
        meta["evidence_kind"] = evidence_kind
    if extra_meta:
        meta.update(extra_meta)
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder({"data": data, "meta": meta}),
        headers=headers,
    )

def _resolve_final_idempotency_key(
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str],
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

def _stable_json_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def _management_json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value))

def _management_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None

def _management_avg(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None

def _management_count_by(records: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        value = str(record.get(field) or "unknown").strip() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts

def _management_ai_href(route: str, **params: Optional[str]) -> str:
    clean_params = {
        key: str(value)
        for key, value in params.items()
        if value not in (None, "")
    }
    if not clean_params:
        return route
    return f"{route}?{urlencode(clean_params)}"

def _management_ai_audit_href(
    *,
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    message_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> str:
    return _management_ai_href(
        "/bff/management/ai/audit",
        session_id=session_id,
        trace_id=trace_id,
        message_id=message_id,
        event_type=event_type,
    )

_dedupe_nonblank_strings = auth_policy.dedupe_nonblank_strings
_bff_me_tenant_payload = auth_policy.bff_me_tenant_payload

# ---------------------------------------------------------------------------
# DI Seams for Management NL Collaborators
# ---------------------------------------------------------------------------

_AGORA_AUDIT_STORE: Optional[Any] = None

def get_agora_audit_store() -> Any:
    global _AGORA_AUDIT_STORE
    if _AGORA_AUDIT_STORE is None:
        from ..agora_audit_store import AgoraAuditStore
        _AGORA_AUDIT_STORE = AgoraAuditStore()
    return _AGORA_AUDIT_STORE

def set_agora_audit_store(store: Optional[Any]) -> None:
    global _AGORA_AUDIT_STORE
    _AGORA_AUDIT_STORE = store

def reset_agora_audit_store() -> None:
    global _AGORA_AUDIT_STORE
    _AGORA_AUDIT_STORE = None

def _record_agora_audit_event(event: Dict[str, Any]) -> Dict[str, Any]:
    legacy_writer = getattr(get_read_store(), "record_agora_audit_event", None)
    if callable(legacy_writer):
        return legacy_writer(event)
    return get_agora_audit_store().record_agora_audit_event(event)


_ASSISTANT_CONTROL_MODE_STORE: Optional[Any] = None

def get_assistant_control_mode_store() -> Optional[Any]:
    return _ASSISTANT_CONTROL_MODE_STORE

def set_assistant_control_mode_store(store: Optional[Any]) -> None:
    global _ASSISTANT_CONTROL_MODE_STORE
    _ASSISTANT_CONTROL_MODE_STORE = store

def reset_assistant_control_mode_store() -> None:
    global _ASSISTANT_CONTROL_MODE_STORE
    _ASSISTANT_CONTROL_MODE_STORE = None

def _mgmt_nl_control_store() -> Optional[Any]:
    return get_assistant_control_mode_store()


_MANAGEMENT_AI_CONTEXT_SERVICE: Optional[Any] = None

def get_management_ai_context_service() -> Any:
    global _MANAGEMENT_AI_CONTEXT_SERVICE
    if _MANAGEMENT_AI_CONTEXT_SERVICE is None:
        from ..management_read_models.service import ManagementService as _ManagementServiceForContext
        _MANAGEMENT_AI_CONTEXT_SERVICE = _ManagementServiceForContext(
            read_store=get_read_store(),
            utc_now=utc_now,
        )
    return _MANAGEMENT_AI_CONTEXT_SERVICE

def set_management_ai_context_service(service: Optional[Any]) -> None:
    global _MANAGEMENT_AI_CONTEXT_SERVICE
    _MANAGEMENT_AI_CONTEXT_SERVICE = service

def reset_management_ai_context_service() -> None:
    global _MANAGEMENT_AI_CONTEXT_SERVICE
    _MANAGEMENT_AI_CONTEXT_SERVICE = None

class _ManagementAiContextServiceProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(get_management_ai_context_service(), name)

_management_ai_context_service = _ManagementAiContextServiceProxy()


_BUILD_OPERATOR_ALERTS_PAYLOAD_FN: Optional[Callable[[str], Dict[str, Any]]] = None

def get_build_operator_alerts_payload() -> Callable[[str], Dict[str, Any]]:
    if _BUILD_OPERATOR_ALERTS_PAYLOAD_FN is not None:
        return _BUILD_OPERATOR_ALERTS_PAYLOAD_FN
    return lambda snapshot_at: {"alerts": [], "meta": {"surfaces": {}}}

def set_build_operator_alerts_payload(fn: Optional[Callable[[str], Dict[str, Any]]]) -> None:
    global _BUILD_OPERATOR_ALERTS_PAYLOAD_FN
    _BUILD_OPERATOR_ALERTS_PAYLOAD_FN = fn

def reset_build_operator_alerts_payload() -> None:
    global _BUILD_OPERATOR_ALERTS_PAYLOAD_FN
    _BUILD_OPERATOR_ALERTS_PAYLOAD_FN = None

def _build_operator_alerts_payload(snapshot_at: str) -> Dict[str, Any]:
    return get_build_operator_alerts_payload()(snapshot_at)


_BUILD_MANAGEMENT_ANOMALIES_PAYLOAD_FN: Optional[Callable[[str], Dict[str, Any]]] = None

def get_build_management_anomalies_payload() -> Callable[[str], Dict[str, Any]]:
    if _BUILD_MANAGEMENT_ANOMALIES_PAYLOAD_FN is not None:
        return _BUILD_MANAGEMENT_ANOMALIES_PAYLOAD_FN
    return lambda snapshot_at: {"items": [], "meta": {"surfaces": {}}}

def set_build_management_anomalies_payload(fn: Optional[Callable[[str], Dict[str, Any]]]) -> None:
    global _BUILD_MANAGEMENT_ANOMALIES_PAYLOAD_FN
    _BUILD_MANAGEMENT_ANOMALIES_PAYLOAD_FN = fn

def reset_build_management_anomalies_payload() -> None:
    global _BUILD_MANAGEMENT_ANOMALIES_PAYLOAD_FN
    _BUILD_MANAGEMENT_ANOMALIES_PAYLOAD_FN = None

def _build_management_anomalies_payload(snapshot_at: str) -> Dict[str, Any]:
    return get_build_management_anomalies_payload()(snapshot_at)


_HUMAN_INBOX_PAYLOAD_FN: Optional[Callable[..., Dict[str, Any]]] = None

def get_human_inbox_payload() -> Callable[..., Dict[str, Any]]:
    if _HUMAN_INBOX_PAYLOAD_FN is not None:
        return _HUMAN_INBOX_PAYLOAD_FN
    return lambda snapshot_at, **kwargs: {"items": [], "meta": {"surfaces": {}}}

def set_human_inbox_payload(fn: Optional[Callable[..., Dict[str, Any]]]) -> None:
    global _HUMAN_INBOX_PAYLOAD_FN
    _HUMAN_INBOX_PAYLOAD_FN = fn

def reset_human_inbox_payload() -> None:
    global _HUMAN_INBOX_PAYLOAD_FN
    _HUMAN_INBOX_PAYLOAD_FN = None

def _human_inbox_payload(snapshot_at: str, **kwargs: Any) -> Dict[str, Any]:
    return get_human_inbox_payload()(snapshot_at, **kwargs)


_LIST_PERSONA_RECORDS_FN: Optional[Callable[..., List[Dict[str, Any]]]] = None

def get_list_persona_records() -> Callable[..., List[Dict[str, Any]]]:
    if _LIST_PERSONA_RECORDS_FN is not None:
        return _LIST_PERSONA_RECORDS_FN
    return lambda tenant_id=None: []

def set_list_persona_records(fn: Optional[Callable[..., List[Dict[str, Any]]]]) -> None:
    global _LIST_PERSONA_RECORDS_FN
    _LIST_PERSONA_RECORDS_FN = fn

def reset_list_persona_records() -> None:
    global _LIST_PERSONA_RECORDS_FN
    _LIST_PERSONA_RECORDS_FN = None

def _list_persona_records(tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return get_list_persona_records()(tenant_id)


_PROJECT_PERSONA_FLEET_ITEM_FN: Optional[Callable[..., Tuple[Dict[str, Any], List[Dict[str, Any]]]]] = None

def get_project_persona_fleet_item() -> Callable[..., Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    if _PROJECT_PERSONA_FLEET_ITEM_FN is not None:
        return _PROJECT_PERSONA_FLEET_ITEM_FN
    def _default(persona: Dict[str, Any], **kwargs: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        item = {
            "persona_id": persona.get("persona_id") or persona.get("id"),
            "health": {"status": "healthy" if persona.get("lifecycle_state") == "active" else "degraded"},
            "bindings": [],
            "runtimeBindings": [],
            **persona,
        }
        return item, []
    return _default

def set_project_persona_fleet_item(fn: Optional[Callable[..., Tuple[Dict[str, Any], List[Dict[str, Any]]]]]) -> None:
    global _PROJECT_PERSONA_FLEET_ITEM_FN
    _PROJECT_PERSONA_FLEET_ITEM_FN = fn

def reset_project_persona_fleet_item() -> None:
    global _PROJECT_PERSONA_FLEET_ITEM_FN
    _PROJECT_PERSONA_FLEET_ITEM_FN = None

def _project_persona_fleet_item(persona: Dict[str, Any], **kwargs: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    return get_project_persona_fleet_item()(persona, **kwargs)


_PROJECT_OPERATOR_RUNTIME_STATE_ROW_FN: Optional[Callable[..., Dict[str, Any]]] = None

def get_project_operator_runtime_state_row() -> Callable[..., Dict[str, Any]]:
    if _PROJECT_OPERATOR_RUNTIME_STATE_ROW_FN is not None:
        return _PROJECT_OPERATOR_RUNTIME_STATE_ROW_FN
    return lambda binding, **kwargs: dict(binding or {})

def set_project_operator_runtime_state_row(fn: Optional[Callable[..., Dict[str, Any]]]) -> None:
    global _PROJECT_OPERATOR_RUNTIME_STATE_ROW_FN
    _PROJECT_OPERATOR_RUNTIME_STATE_ROW_FN = fn

def reset_project_operator_runtime_state_row() -> None:
    global _PROJECT_OPERATOR_RUNTIME_STATE_ROW_FN
    _PROJECT_OPERATOR_RUNTIME_STATE_ROW_FN = None

def _project_operator_runtime_state_row(binding: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    return get_project_operator_runtime_state_row()(binding, **kwargs)


_MANAGEMENT_TELEMETRY_ROLLUP_FN: Optional[Callable[..., Dict[str, Any]]] = None

def get_management_telemetry_rollup() -> Callable[..., Dict[str, Any]]:
    if _MANAGEMENT_TELEMETRY_ROLLUP_FN is not None:
        return _MANAGEMENT_TELEMETRY_ROLLUP_FN
    return lambda telemetry_list: {}

def set_management_telemetry_rollup(fn: Optional[Callable[..., Dict[str, Any]]]) -> None:
    global _MANAGEMENT_TELEMETRY_ROLLUP_FN
    _MANAGEMENT_TELEMETRY_ROLLUP_FN = fn

def reset_management_telemetry_rollup() -> None:
    global _MANAGEMENT_TELEMETRY_ROLLUP_FN
    _MANAGEMENT_TELEMETRY_ROLLUP_FN = None

def _management_telemetry_rollup(telemetry_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    return get_management_telemetry_rollup()(telemetry_list)


_DATASET_SURFACE_STATUS_FN: Optional[Callable[..., Dict[str, Any]]] = None

def get_dataset_surface_status() -> Callable[..., Dict[str, Any]]:
    if _DATASET_SURFACE_STATUS_FN is not None:
        return _DATASET_SURFACE_STATUS_FN
    def _default(dataset: str, *, snapshot_at: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        return {"status": "available", "source": "dataset_store"}
    return _default

def set_dataset_surface_status(fn: Optional[Callable[..., Dict[str, Any]]]) -> None:
    global _DATASET_SURFACE_STATUS_FN
    _DATASET_SURFACE_STATUS_FN = fn

def reset_dataset_surface_status() -> None:
    global _DATASET_SURFACE_STATUS_FN
    _DATASET_SURFACE_STATUS_FN = None

def _dataset_surface_status(dataset: str, *, snapshot_at: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    return get_dataset_surface_status()(dataset, snapshot_at=snapshot_at, **kwargs)


_ASSISTANT_COLLECT_SOURCE_FN: Optional[Callable[..., Any]] = None

def get_assistant_collect_source() -> Callable[..., Any]:
    if _ASSISTANT_COLLECT_SOURCE_FN is not None:
        return _ASSISTANT_COLLECT_SOURCE_FN
    return lambda source_id, request, snapshot_at, identity=None: None

def set_assistant_collect_source(fn: Optional[Callable[..., Any]]) -> None:
    global _ASSISTANT_COLLECT_SOURCE_FN
    _ASSISTANT_COLLECT_SOURCE_FN = fn

def reset_assistant_collect_source() -> None:
    global _ASSISTANT_COLLECT_SOURCE_FN
    _ASSISTANT_COLLECT_SOURCE_FN = None

def _assistant_collect_source(source_id: str, request: Any, snapshot_at: str, identity: Optional[Any] = None) -> Any:
    return get_assistant_collect_source()(source_id, request, snapshot_at, identity=identity)


# ---------------------------------------------------------------------------
# SSE Buffer Registration & Event Publishing
# ---------------------------------------------------------------------------

_ACTIVE_SSE_BUFFERS: List[Dict[str, deque]] = [_sse_buffers]

def register_sse_buffers(buffers: Dict[str, deque], subscribers: Optional[Dict[str, list]] = None) -> None:
    global _sse_buffers, _sse_subscribers
    _sse_buffers = buffers
    if subscribers is not None:
        _sse_subscribers = subscribers
    if buffers not in _ACTIVE_SSE_BUFFERS:
        _ACTIVE_SSE_BUFFERS.append(buffers)

def _make_event_id(prefix: str = "evt") -> str:
    return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"

def _sse_shared_replay_enabled() -> bool:
    mode = os.getenv("PANTHEON_BFF_SSE_REPLAY_STORE", "memory").strip().lower()
    return mode in {"1", "true", "file", "jsonl", "shared", "shared-file"}

def _sse_channel_for_buffer(buffer: deque) -> Optional[str]:
    for buf_dict in _ACTIVE_SSE_BUFFERS:
        for channel, candidate in buf_dict.items():
            if candidate is buffer:
                return channel
    return None

def _append_shared_sse_event(channel: Optional[str], event: dict) -> None:
    if not channel or not _sse_shared_replay_enabled():
        return
    data_dir = os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff")
    replay_dir = os.path.join(data_dir, "sse_replay")
    os.makedirs(replay_dir, exist_ok=True)
    path = os.path.join(replay_dir, f"{channel}.jsonl")
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        with open(path, "r", encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
        if len(lines) > 500:
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(lines[-500:])
    except Exception:
        pass

def _publish_event(buffer: deque, subscribers: list[asyncio.Queue], event_type: str, data: dict) -> str:
    """Publish an event to the buffer and notify all subscribers."""
    event_id = _make_event_id()
    event = SseEventEnvelope[Dict[str, Any]](
        id=event_id,
        type=event_type,
        data=dict(data or {}),
    ).model_dump(mode="json")
    buffer.append((event_id, event))
    channel = _sse_channel_for_buffer(buffer)
    if channel:
        for active_dict in _ACTIVE_SSE_BUFFERS:
            target_buffer = active_dict.get(channel)
            if target_buffer is not None and target_buffer is not buffer and (event_id, event) not in target_buffer:
                target_buffer.append((event_id, event))
    _append_shared_sse_event(channel, event)
    for q in list(subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
    return event_id

# ---------------------------------------------------------------------------
# Delegate Helpers
# ---------------------------------------------------------------------------

def _mgmt_nl_deterministic_answer(*args: Any, **kwargs: Any) -> Any:
    return _mgmt_nl_synthesize_answer(*args, **kwargs)

def _mgmt_nl_provider_enabled(*args: Any, **kwargs: Any) -> Any:
    return _mgmt_nl_provider_feature_enabled(*args, **kwargs)

async def _mgmt_nl_invoke_provider(*args: Any, **kwargs: Any) -> Any:
    return await _mgmt_nl_maybe_provider_answer(*args, **kwargs)



def _mgmt_nl_normalize_focus(value: Any) -> str:
    focus = str(value or "all").strip().lower()
    focus = _MGMT_NL_FOCUS_ALIASES.get(focus, focus)
    if focus not in _MGMT_NL_VALID_FOCUS:
        return "all"
    return focus
def _mgmt_nl_trim_text(value: Any, *, max_len: int = 4000) -> str:
    clean = re.sub(r"\s+", " ", str(value or "").strip())
    if len(clean) > max_len:
        return f"{clean[:max_len]}..."
    return clean

def _management_ai_provider_history_size(turns: List[Dict[str, Any]]) -> int:
    return len(json.dumps(turns, sort_keys=True, ensure_ascii=True))

def _management_ai_provider_history_minimal_turn(turn: Dict[str, Any]) -> Dict[str, Any]:
    text = str(turn.get("content") or turn.get("text") or "")
    trimmed_text = _mgmt_nl_trim_text(text, max_len=2048)
    return {
        "id": turn.get("id"),
        "role": turn.get("role"),
        "content": trimmed_text,
        "text": trimmed_text,
        "created_at": turn.get("created_at") or turn.get("createdAt"),
        "trace_id": turn.get("trace_id") or turn.get("traceId"),
    }

def _management_ai_provider_history_window(
    turns: List[Dict[str, Any]],
    *,
    char_budget: int = _MGMT_NL_PROVIDER_HISTORY_CHAR_BUDGET,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if _management_ai_provider_history_size(turns) <= char_budget:
        return list(turns), {
            "history_char_budget": char_budget,
            "history_estimated_chars": _management_ai_provider_history_size(turns),
            "history_truncated": False,
            "history_omitted_turn_count": 0,
        }

    selected: List[Dict[str, Any]] = []
    for turn in reversed(turns):
        candidate = [turn, *selected]
        if _management_ai_provider_history_size(candidate) > char_budget:
            if selected:
                break
            selected = [_management_ai_provider_history_minimal_turn(turn)]
            break
        selected = candidate

    return selected, {
        "history_char_budget": char_budget,
        "history_estimated_chars": _management_ai_provider_history_size(selected),
        "history_truncated": True,
        "history_omitted_turn_count": max(0, len(turns) - len(selected)),
    }

def _management_ai_server_conversation_context(
    *,
    session_id: str,
    client_hint: Dict[str, Any],
    history_window_fn: Optional[Callable] = None,
    conversation_store: Optional[Any] = None,
) -> Dict[str, Any]:
    return management_ai_server_conversation_context(
        session_id=session_id,
        client_hint=client_hint,
        history_window_fn=history_window_fn if history_window_fn is not None else _management_ai_provider_history_window,
        conversation_store=conversation_store if conversation_store is not None else get_management_ai_conversation_store(),
    )

def _mgmt_nl_normalize_conversation_context(value: Any) -> Dict[str, Any]:
    conversation = value if isinstance(value, dict) else {}
    raw_turns = conversation.get("recentTurns")
    if raw_turns is None:
        raw_turns = conversation.get("recent_turns")
    recent_turns: List[Dict[str, str]] = []
    if isinstance(raw_turns, list):
        for raw_turn in raw_turns[-_MGMT_NL_MAX_RECENT_TURNS:]:
            if not isinstance(raw_turn, dict):
                continue
            role = str(raw_turn.get("role") or "").strip().lower()
            if role not in {"user", "assistant", "system"}:
                continue
            content = _mgmt_nl_trim_text(
                raw_turn.get("content") if raw_turn.get("content") is not None else raw_turn.get("text"),
                max_len=2000,
            )
            if not content:
                continue
            recent_turns.append({"role": role, "content": content, "text": content})
    summary = _mgmt_nl_trim_text(conversation.get("summary"), max_len=4000)
    return {
        "recent_turns": recent_turns,
        "summary": summary,
        "max_recent_turns": _MGMT_NL_MAX_RECENT_TURNS,
    }
def _mgmt_nl_normalize_action_descriptor(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or value.get("type") or "").strip()
    if kind not in _MGMT_NL_UI_ACTION_KINDS:
        return None
    descriptor: Dict[str, Any] = {
        "kind": kind,
        "description": _mgmt_nl_trim_text(value.get("description"), max_len=500),
        "paramsSchema": _mgmt_nl_trim_text(
            value.get("paramsSchema") if value.get("paramsSchema") is not None else value.get("params_schema"),
            max_len=1000,
        ),
    }
    if value.get("label") is not None:
        descriptor["label"] = _mgmt_nl_trim_text(value.get("label"), max_len=120)
    return descriptor
def _mgmt_nl_normalize_available_ui_actions(value: Any) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    if not isinstance(value, list):
        return actions
    for item in value:
        descriptor = _mgmt_nl_normalize_action_descriptor(item)
        if not descriptor:
            continue
        kind = str(descriptor.get("kind") or "")
        if kind in seen:
            continue
        seen.add(kind)
        actions.append(descriptor)
    return actions
def _mgmt_nl_normalize_ui_context(value: Any, *, operator_context: str) -> Dict[str, Any]:
    ui = value if isinstance(value, dict) else {}
    current_route = str(ui.get("currentRoute") or ui.get("current_route") or "/management").strip() or "/management"
    selected_entity = ui.get("selectedEntity") if "selectedEntity" in ui else ui.get("selected_entity")
    if not isinstance(selected_entity, dict):
        selected_entity = None
    visible_panels = ui.get("visiblePanels") if "visiblePanels" in ui else ui.get("visible_panels")
    if not isinstance(visible_panels, list):
        visible_panels = []
    filters = ui.get("filters") if isinstance(ui.get("filters"), dict) else {}
    available_ui_actions = ui.get("availableUiActions")
    if available_ui_actions is None:
        available_ui_actions = ui.get("available_ui_actions")
    normalized = {
        "currentRoute": current_route,
        "current_route": current_route,
        "selectedEntity": selected_entity,
        "selected_entity": selected_entity,
        "visiblePanels": [str(item) for item in visible_panels[:20] if str(item or "").strip()],
        "visible_panels": [str(item) for item in visible_panels[:20] if str(item or "").strip()],
        "filters": filters,
        "availableUiActions": _mgmt_nl_normalize_available_ui_actions(available_ui_actions),
        "available_ui_actions": _mgmt_nl_normalize_available_ui_actions(available_ui_actions),
    }
    if operator_context:
        normalized["legacyContext"] = operator_context
        normalized["legacy_context"] = operator_context
    return normalized
def _mgmt_nl_frontend_selected_entity(ui_snapshot: Dict[str, Any], *, focus: str) -> Dict[str, Any]:
    selected = ui_snapshot.get("selectedEntity")
    route = str(ui_snapshot.get("currentRoute") or "/management")
    if isinstance(selected, dict):
        entity_type = str(selected.get("entityType") or selected.get("entity_type") or selected.get("kind") or "").strip()
        entity_id = str(selected.get("entityId") or selected.get("entity_id") or selected.get("id") or "").strip()
        if entity_type and entity_id:
            return {
                "entityType": entity_type,
                "entityId": entity_id,
                "label": str(selected.get("label") or entity_id),
                "route": route,
            }
    return {
        "entityType": "management_nl_focus",
        "entityId": focus,
        "label": focus,
        "route": route,
    }
def _mgmt_nl_allowed_action_kinds(ui_snapshot: Dict[str, Any]) -> Set[str]:
    actions = ui_snapshot.get("availableUiActions")
    if not isinstance(actions, list):
        return set()
    return {
        str(item.get("kind") or "")
        for item in actions
        if isinstance(item, dict) and str(item.get("kind") or "") in _MGMT_NL_UI_ACTION_KINDS
    }
def _mgmt_nl_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if not clean or clean[0] not in "{[":
        return None
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return None
def _mgmt_nl_find_action_values(value: Any, *, depth: int = 0) -> List[Any]:
    if depth > 8:
        return []
    parsed = _mgmt_nl_jsonish(value)
    if parsed is not None:
        return _mgmt_nl_find_action_values(parsed, depth=depth + 1)
    if isinstance(value, list):
        found: List[Any] = []
        for item in value:
            found.extend(_mgmt_nl_find_action_values(item, depth=depth + 1))
        return found
    if not isinstance(value, dict):
        return []

    found = []
    actions = value.get("actions")
    if isinstance(actions, list):
        found.extend(actions)
    for key in ("data", "output", "final", "answer", "message", "content", "text", "item", "delta", "json_events"):
        if key in value:
            found.extend(_mgmt_nl_find_action_values(value.get(key), depth=depth + 1))
    stdout = value.get("stdout")
    if isinstance(stdout, str):
        for line in stdout.splitlines():
            found.extend(_mgmt_nl_find_action_values(line, depth=depth + 1))
    return found
def _mgmt_nl_action_params_valid(kind: str, params: Dict[str, Any]) -> bool:
    if kind == "navigate":
        return bool(str(params.get("to") or params.get("route") or "").strip())
    if kind == "openDrawer":
        return bool(str(params.get("drawer") or "").strip())
    if kind == "selectEntity":
        return bool(str(params.get("kind") or "").strip() and str(params.get("id") or "").strip())
    if kind == "setFilter":
        return bool(str(params.get("key") or "").strip())
    if kind == "focusPanel":
        return bool(str(params.get("panel") or "").strip())
    if kind == "refreshCurrentView":
        return True
    if kind == "runBffAction":
        if (
            params.get("entityType") == "Runtime"
            and params.get("actionId") in {"PausePaperRuntime", "ResumePaperRuntime"}
            and str(params.get("entityId") or "").strip()
        ):
            return True
        endpoint = str(params.get("endpoint") or "").strip()
        return endpoint.startswith("/bff/") or endpoint.startswith("/api/v1/")
    return False
def _mgmt_nl_extract_provider_actions(provider_payload: Any, *, allowed_action_kinds: Set[str]) -> List[Dict[str, Any]]:
    if not allowed_action_kinds:
        return []
    actions: List[Dict[str, Any]] = []
    for index, raw_action in enumerate(_mgmt_nl_find_action_values(provider_payload), start=1):
        if not isinstance(raw_action, dict):
            continue
        kind = str(raw_action.get("kind") or raw_action.get("type") or "").strip()
        if kind not in allowed_action_kinds or kind not in _MGMT_NL_UI_ACTION_KINDS:
            continue
        params = raw_action.get("params") if isinstance(raw_action.get("params"), dict) else {}
        if not _mgmt_nl_action_params_valid(kind, params):
            continue
        requires_confirmation = bool(
            raw_action.get("requiresConfirmation")
            if "requiresConfirmation" in raw_action
            else raw_action.get("requires_confirmation")
        )
        if kind in _MGMT_NL_WRITE_ACTION_KINDS:
            requires_confirmation = True
        actions.append(
            {
                "id": str(raw_action.get("id") or f"act_{index:02d}"),
                "kind": kind,
                "label": _mgmt_nl_trim_text(raw_action.get("label") or kind, max_len=120),
                "rationale": _mgmt_nl_trim_text(
                    raw_action.get("rationale") or raw_action.get("reason") or "",
                    max_len=500,
                ),
                "params": params,
                "requiresConfirmation": requires_confirmation,
            }
        )
    return actions[:6]
def _assistant_control_mode_for_identity(
    identity: OperatorIdentity,
    *,
    management_session_id: Optional[str] = None,
    touch: bool = False,
) -> Dict[str, Any]:
    store = _ASSISTANT_CONTROL_MODE_STORE
    if store is None:
        return {
            "state": "inactive",
            "active": False,
            "reason": "control_mode_store_unavailable",
            "configured": False,
        }
    try:
        return store.status_for_actor(
            identity.operator_id,
            management_session_id=management_session_id,
            touch=touch,
        )
    except Exception:
        log.warning("Failed to read assistant control mode status", exc_info=True)
        return {
            "state": "inactive",
            "active": False,
            "reason": "control_mode_status_unavailable",
        }
def _mgmt_nl_identity_with_control_mode(
    identity: OperatorIdentity,
    control_mode: Dict[str, Any],
) -> OperatorIdentity:
    if not isinstance(control_mode, dict) or not control_mode.get("active"):
        return identity
    claims = dict(identity.claims or {})
    raw_caps = claims.get("capabilities") or claims.get("capability") or []
    if isinstance(raw_caps, str):
        raw_caps = re.split(r"[\s,]+", raw_caps)
    if not isinstance(raw_caps, list):
        raw_caps = []
    caps = _dedupe_nonblank_strings([
        *raw_caps,
        *(control_mode.get("capabilities") if isinstance(control_mode.get("capabilities"), list) else []),
        "assistant.kernel",
    ])
    claims["capabilities"] = caps
    try:
        return identity.model_copy(update={"claims": claims})
    except AttributeError:
        return OperatorIdentity(
            operator_id=identity.operator_id,
            roles=identity.roles,
            mfa_verified=identity.mfa_verified,
            claims=claims,
            token_kind=identity.token_kind,
        )
def _mgmt_nl_validate_question_size(question: str) -> None:
    question_size = len(question.encode("utf-8"))
    if question_size <= _MGMT_NL_MAX_QUESTION_BYTES:
        return
    raise _bff_error(
        413,
        ErrorCode.REQUEST_TOO_LARGE,
        "Management NL question exceeds the maximum size",
        f"question must be at most {_MGMT_NL_MAX_QUESTION_BYTES} bytes",
        precondition_failed="question_size",
        suggestion="Shorten the question and attach large context through an approved evidence route",
        details_extra={
            "maxQuestionBytes": _MGMT_NL_MAX_QUESTION_BYTES,
            "actualQuestionBytes": question_size,
        },
    )
def _mgmt_nl_control_strip_activation_prefix(clean_question: str) -> Optional[str]:
    for prefix in sorted(_MGMT_NL_CONTROL_ACTIVATE_PREFIXES, key=len, reverse=True):
        if not clean_question.lower().startswith(prefix.lower()):
            continue
        raw_remainder = clean_question[len(prefix):]
        if prefix.lower() in _MGMT_NL_CONTROL_SEPARATOR_ACTIVATE_PREFIXES:
            if not re.match(r"^\s*(?:是|為|为|:|：|=|＝)", raw_remainder):
                continue
        remainder = raw_remainder.strip()
        remainder = re.sub(r"^(?:on|activate|啟動|启动|開啟|开启)\b", "", remainder, flags=re.IGNORECASE).strip()
        remainder = re.sub(r"^(?:是|為|为|:|：|=|＝|\s)+", "", remainder).strip()
        return remainder or None
    return None
def _mgmt_nl_parse_control_command(question: str) -> Optional[Dict[str, Any]]:
    clean_question = re.sub(r"\s+", " ", str(question or "").strip())
    if not clean_question:
        return None

    lowered = clean_question.lower()
    if lowered in _MGMT_NL_CONTROL_STATUS_COMMANDS:
        return {"kind": "status", "source": "explicit"}
    if lowered in _MGMT_NL_CONTROL_DEACTIVATE_COMMANDS:
        return {"kind": "deactivate", "source": "explicit"}

    prefixed_passphrase = _mgmt_nl_control_strip_activation_prefix(clean_question)
    if prefixed_passphrase is not None:
        return {
            "kind": "activate",
            "source": "explicit",
            "passphrase": prefixed_passphrase,
        }

    store = _mgmt_nl_control_store()
    matcher = getattr(store, "matches_passphrase", None)
    if callable(matcher):
        try:
            if matcher(clean_question):
                return {
                    "kind": "activate",
                    "source": "direct_passphrase",
                    "passphrase": clean_question,
                }
        except Exception:
            log.warning("Failed to match management NL direct control passphrase", exc_info=True)
    return None
def _mgmt_nl_positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed
def _mgmt_nl_control_options(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_options = payload.get("controlMode")
    if raw_options is None:
        raw_options = payload.get("control_mode")
    options = raw_options if isinstance(raw_options, dict) else {}
    result = dict(options)
    for key in ("mode", "ttlSeconds", "ttl_seconds", "idleTtlSeconds", "idle_ttl_seconds"):
        if key in payload and key not in result:
            result[key] = payload.get(key)
    return result
def _mgmt_nl_raise_control_mode_actor_error(identity: OperatorIdentity) -> None:
    from .control_mode import (
        CONTROL_MODE_CAPABILITY_PREFIX,
        CONTROL_MODE_ROLES,
        actor_has_control_role,
        actor_has_kernel_capability,
    )

    if not actor_has_control_role(identity):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Control mode requires operator or admin role",
            "Actor does not hold a role allowed to activate control mode",
            precondition_failed="control_mode_role",
            details_extra={
                "field": "roles",
                "required_roles": sorted(CONTROL_MODE_ROLES),
            },
        )
    if not getattr(identity, "mfa_verified", False):
        raise _bff_error(
            403,
            ErrorCode.AUTH_REQUIRED,
            "Control mode requires MFA",
            "Actor must complete MFA before activating control mode",
            precondition_failed="control_mode_mfa",
            details_extra={"field": "mfa"},
        )
    if not actor_has_kernel_capability(identity):
        raise _bff_error(
            422,
            ErrorCode.BUSINESS_RULE_VIOLATION,
            "Control mode requires assistant kernel capability",
            f"Actor capabilities must include a value starting with {CONTROL_MODE_CAPABILITY_PREFIX!r}",
            precondition_failed="control_mode_capability",
            details_extra={
                "field": "capabilities",
                "required_capability_prefix": CONTROL_MODE_CAPABILITY_PREFIX,
            },
        )
def _mgmt_nl_require_mode_capability(identity: OperatorIdentity, mode: Any) -> None:
    from .control_mode import actor_capabilities

    mode_value = str(getattr(mode, "value", mode) or "").strip()
    required = f"assistant.{mode_value.replace('_', '.')}"
    if required in set(actor_capabilities(identity)):
        return
    raise _bff_error(
        403,
        ErrorCode.FORBIDDEN,
        f"Control mode {mode_value} requires {required} capability",
        "The authenticated actor does not hold the exact capability required for the requested mode.",
        precondition_failed="control_mode_capability",
        details_extra={
            "field": "capabilities",
            "reason": "mode_capability_missing",
            "required_capability": required,
        },
    )
def _mgmt_nl_raise_control_mode_error(exc: Exception) -> None:
    status_code = int(getattr(exc, "status_code", 422) or 422)
    if status_code == 403:
        code = ErrorCode.FORBIDDEN
    elif status_code == 409:
        code = ErrorCode.RESOURCE_CONFLICT
    elif status_code == 400:
        code = ErrorCode.VALIDATION_FAILED
    else:
        code = ErrorCode.BUSINESS_RULE_VIOLATION
    reason = str(getattr(exc, "reason", "") or getattr(exc, "field", "") or "control_mode_error")
    raise _bff_error(
        status_code,
        code,
        str(exc),
        reason,
        precondition_failed=f"control_mode_{reason}",
        details_extra={"field": getattr(exc, "field", None)},
    )
def _mgmt_nl_control_provider_status(command_kind: str) -> Dict[str, Any]:
    status = _mgmt_nl_provider_status(
        provider="pantheon_bff",
        enabled=True,
        status="completed",
        reason=f"control_mode_{command_kind}",
        used=True,
    )
    status["runtime"] = "management_nl_control_command_interceptor"
    status["fallback"] = None
    return status
def _mgmt_nl_control_answer(command_kind: str, control_mode: Dict[str, Any]) -> str:
    if command_kind == "activate" and control_mode.get("active"):
        return (
            "Control mode activated for this Management AI session. "
            "It will expire automatically at the configured TTL or idle timeout."
        )
    if command_kind == "deactivate":
        return "Control mode deactivated. This Management AI session is back in user mode."
    if control_mode.get("active"):
        return "Control mode is active for this Management AI session."
    return "Control mode is inactive for this Management AI session."
def _mgmt_nl_record_control_audit(
    *,
    identity: OperatorIdentity,
    command_kind: str,
    session_id: str,
    message_id: str,
    trace_id: str,
    focus: str,
    tenant_id: str,
    now: Any,
) -> Dict[str, Any]:
    audit_ref = {
        "target_type": "ManagementNLExchange",
        "target_id": message_id,
        "href": f"/bff/audit/entities/ManagementNLExchange/{message_id}",
    }
    try:
        accepted_audit = _record_agora_audit_event(
            {
                "action": f"management.nl.control_mode.{command_kind}",
                "targetType": "ManagementNLExchange",
                "targetId": message_id,
                "actorId": identity.operator_id,
                "recordedAt": now,
                "sessionId": session_id,
                "focus": focus,
                "tenantId": tenant_id,
                "traceId": trace_id,
                "question": _MGMT_NL_CONTROL_REDACTED_QUESTION,
            }
        )
    except Exception:
        log.warning("Failed to record management NL control-mode audit event", exc_info=True)
        raise _bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Management NL control-mode audit write failed",
            "control_mode_audit_write_failed",
            precondition_failed="audit_write",
            suggestion="Retry after the Agora audit store is available",
        )
    audit_ref["audit_id"] = accepted_audit.get("auditId") or accepted_audit.get("eventId")
    return audit_ref
def _management_nl_publish_completed_events(
    *,
    session_id: str,
    message_id: str,
    assistant_turn_id: str,
    trace_id: str,
    focus: str,
    provider_status: Dict[str, Any],
    action_count: int,
    audit_log_href: str,
    conversation_href: str,
    control_command: Optional[str] = None,
) -> None:
    provider_state = str(provider_status.get("status") or "unknown")
    completed_event: Dict[str, Any] = {
        "session_id": session_id,
        "message_id": message_id,
        "assistant_turn_id": assistant_turn_id,
        "trace_id": trace_id,
        "focus": focus,
        "status": "completed",
        "lifecycle_status": "completed",
        "provider_status": provider_status,
        "provider_status_state": provider_state,
        "action_count": action_count,
    }
    if control_command is not None:
        completed_event["control_command"] = control_command
    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "ask.message.completed",
        completed_event,
    )
    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "management.nl.ask.completed",
        {
            **completed_event,
            "audit_log": {"href": audit_log_href, "trace_id": trace_id},
            "conversation": {
                "href": conversation_href,
                "session_id": session_id,
                "trace_id": trace_id,
            },
        },
    )
def _mgmt_nl_handle_control_command(
    *,
    control_command: Dict[str, Any],
    payload: Dict[str, Any],
    identity: OperatorIdentity,
    caller_tenant_id: str,
    focus: str,
    ui_snapshot: Dict[str, Any],
    resolved_key: str,
    session_id: str,
    message_id: str,
    trace_id: str,
    now: Any,
) -> JSONResponse:
    from .control_mode import ControlModeError, actor_capabilities, default_idle_ttl
    from .mode_policy import DEFAULT_KERNEL_TTL_SECONDS, ModePolicyViolation, assert_kernel_allowed
    from .models import AssistantMode

    store = _mgmt_nl_control_store()
    if store is None:
        raise _bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Control mode store is unavailable",
            "control_mode_store_unavailable",
            precondition_failed="control_mode_store",
        )

    command_kind = str(control_command.get("kind") or "").strip()
    if command_kind == "activate":
        _mgmt_nl_raise_control_mode_actor_error(identity)
        options = _mgmt_nl_control_options(payload)
        mode_raw = str(options.get("mode") or AssistantMode.KERNEL_DEBUG.value)
        try:
            mode = AssistantMode(mode_raw)
        except ValueError:
            raise _bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                f"Invalid mode: {mode_raw!r}",
                "invalid_control_mode",
                precondition_failed="control_mode_mode",
                details_extra={"field": "mode"},
            )
        try:
            assert_kernel_allowed(mode)
        except ModePolicyViolation as exc:
            raise _bff_error(
                403,
                ErrorCode.FORBIDDEN,
                f"Mode policy violation: {exc}",
                str(exc),
                precondition_failed="control_mode_kernel_policy",
                details_extra={"field": exc.field},
            )
        _mgmt_nl_require_mode_capability(identity, mode)
        ttl_seconds = _mgmt_nl_positive_int(
            options.get("ttlSeconds", options.get("ttl_seconds")),
            DEFAULT_KERNEL_TTL_SECONDS,
        )
        idle_ttl_seconds = _mgmt_nl_positive_int(
            options.get("idleTtlSeconds", options.get("idle_ttl_seconds")),
            default_idle_ttl(ttl_seconds),
        )
        try:
            control_mode = store.activate(
                actor_id=identity.operator_id,
                mode=mode,
                capabilities=actor_capabilities(identity),
                reason=str(options.get("reason") or "management_nl_chat_control_command").strip(),
                passphrase=str(control_command.get("passphrase") or ""),
                ttl_seconds=ttl_seconds,
                idle_ttl_seconds=idle_ttl_seconds,
                management_session_id=session_id,
            )
        except ControlModeError as exc:
            _mgmt_nl_raise_control_mode_error(exc)
    elif command_kind == "deactivate":
        _mgmt_nl_raise_control_mode_actor_error(identity)
        control_mode = store.deactivate(identity.operator_id, reason="management_nl_chat_control_command")
    elif command_kind == "status":
        control_mode = _assistant_control_mode_for_identity(
            identity,
            management_session_id=session_id,
            touch=False,
        )
    else:
        raise _bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "Unsupported control-mode command",
            "unsupported_control_mode_command",
            precondition_failed="control_mode_command",
        )

    provider_status = _mgmt_nl_control_provider_status(command_kind)
    answer = _mgmt_nl_control_answer(command_kind, control_mode)
    assistant_turn_id = f"{message_id}-assistant"
    audit_log_href = _management_ai_audit_href(session_id=session_id, trace_id=trace_id)
    conversation_href = _management_ai_conversation_href(session_id)
    audit_ref = _mgmt_nl_record_control_audit(
        identity=identity,
        command_kind=command_kind,
        session_id=session_id,
        message_id=message_id,
        trace_id=trace_id,
        focus=focus,
        tenant_id=caller_tenant_id,
        now=now,
    )
    _management_ai_ensure_session(
        session_id=session_id,
        identity=identity,
        tenant_id=caller_tenant_id,
        now=now,
        title=_MGMT_NL_CONTROL_REDACTED_QUESTION,
    )
    _management_ai_append_turn(
        turn_id=message_id,
        session_id=session_id,
        role="user",
        text=_MGMT_NL_CONTROL_REDACTED_QUESTION,
        created_at=now,
        trace_id=trace_id,
        attachments=[],
        ui_snapshot=ui_snapshot,
    )

    redaction = {
        "question": "redacted",
        "passphrase": "not_persisted",
        "provider": "not_invoked",
    }
    _management_ai_record_event(
        {
            "event_type": "management_ai.exchange.accepted",
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "actor_id": identity.operator_id,
            "route": "POST /bff/management/nl/ask",
            "question": _MGMT_NL_CONTROL_REDACTED_QUESTION,
            "focus": focus,
            "tenant_id": caller_tenant_id,
            "confidence": "high",
            "source_keys": [],
            "control_command": command_kind,
            "control_command_source": control_command.get("source"),
            "redaction": redaction,
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "control_mode": {
                "state": control_mode.get("state"),
                "active": control_mode.get("active"),
                "mode": control_mode.get("mode"),
                "activation_id": control_mode.get("activation_id") or control_mode.get("activationId"),
            },
            "audit_ref": audit_ref,
        }
    )

    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "management.nl.ask.accepted",
        {
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "focus": focus,
            "control_command": command_kind,
        },
    )

    exchange_status = "completed"
    result = {
        "status": "accepted",
        "data": {
            "status": exchange_status,
            "lifecycle_status": exchange_status,
            "answer": answer,
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "question": _MGMT_NL_CONTROL_REDACTED_QUESTION,
            "focus": focus,
            "sources": [],
            "confidence": "high",
            "summary_context": {},
            "context_pack": None,
            "provider_status": provider_status,
            "control_mode": control_mode,
            "control_command": command_kind,
            "ui_actions": [],
            "actions": [],
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
            "evidence_refs": [],
            "redaction": redaction,
        },
        "meta": {
            "status": exchange_status,
            "lifecycle_status": exchange_status,
            "snapshot_at": now,
            "surfaces": {"management_nl_control_command": {"status": "ok", "source": "bff_interceptor"}},
            "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
            "provider_status": provider_status,
            "trace_id": trace_id,
            "context_pack_id": None,
            "redacted_evidence_count": 0,
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "control_mode": control_mode,
            "control_command": command_kind,
            "redaction": redaction,
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
            "actions": [],
            "action_count": 0,
            "session_ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
            "control_command": command_kind,
            "redaction": redaction,
            "control_mode": {
                "state": control_mode.get("state"),
                "active": control_mode.get("active"),
                "mode": control_mode.get("mode"),
                "activation_id": control_mode.get("activation_id") or control_mode.get("activationId"),
            },
            "fallback": provider_status.get("fallback"),
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
        ui_actions=[],
    )
    _management_nl_publish_completed_events(
        session_id=session_id,
        message_id=message_id,
        assistant_turn_id=assistant_turn_id,
        trace_id=trace_id,
        focus=focus,
        provider_status=provider_status,
        action_count=0,
        audit_log_href=audit_log_href,
        conversation_href=conversation_href,
        control_command=command_kind,
    )
    return JSONResponse(status_code=202, content=result)
def _mgmt_nl_normalize_question_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())
def _mgmt_nl_evasion_stripped_variants(question: str) -> List[str]:
    raw = _mgmt_nl_normalize_question_text(question)
    variants = [raw]
    evasion_prefixes = (
        "can you please ", "can you ", "please ", "help me ", "i need you to ",
        "i want you to ", "could you please ", "could you ", "would you please ",
        "would you ", "should i ", "let's ", "let me ", "go ahead and ",
        "kindly ", "請幫我", "請", "麻煩", "幫我",
    )
    for prefix in evasion_prefixes:
        if raw.startswith(prefix):
            stripped = raw[len(prefix):].strip()
            if stripped and stripped not in variants:
                variants.append(stripped)
            break
    return variants
def _mgmt_nl_term_matches(text: str, term: str) -> bool:
    clean_term = _mgmt_nl_normalize_question_text(term)
    if not clean_term:
        return False
    if re.fullmatch(r"[a-z0-9_ -]+", clean_term):
        term_pattern = r"[\s_-]+".join(re.escape(part) for part in clean_term.split())
        return bool(re.search(rf"(?<![a-z0-9_]){term_pattern}(?![a-z0-9_])", text))
    return clean_term in text
def _mgmt_nl_high_risk_classify(question: str) -> Optional[Dict[str, Any]]:
    """Classify NL questions requesting high-risk mutations before any read work."""
    variants = _mgmt_nl_evasion_stripped_variants(question)
    for category_key, trigger_terms, safe_alternatives in _MGMT_NL_HIGH_RISK_PATTERNS:
        for term in trigger_terms:
            if any(_mgmt_nl_term_matches(variant, term) for variant in variants):
                return {
                    "matched_category": category_key,
                    "matched_pattern": term,
                    "safe_alternatives": safe_alternatives,
                }
    return None
def _mgmt_nl_record_high_risk_refusal(
    *,
    identity: OperatorIdentity,
    question: str,
    risk: Dict[str, Any],
    recorded_at: str,
) -> Optional[str]:
    """Record a narrow refusal audit event without creating NL session state."""
    try:
        audit = _record_agora_audit_event(
            {
                "action": "management.nl.high_risk_refused",
                "targetType": "ManagementNLQuery",
                "targetId": f"mgmt-nl-refusal-{uuid.uuid4().hex[:12]}",
                "actorId": identity.operator_id,
                "recordedAt": recorded_at,
                "reason": "high_risk_nl_policy",
                "matchedCategory": risk.get("matched_category"),
                "matchedPattern": risk.get("matched_pattern"),
                "questionExcerpt": question[:200],
                "followups": _MGMT_NL_HIGH_RISK_REFUSAL_FOLLOWUPS,
            }
        )
        return str(audit.get("auditId") or audit.get("eventId") or "").strip() or None
    except Exception:
        log.warning("Failed to record management NL high-risk refusal audit", exc_info=True)
        return None
def _mgmt_nl_idempotency_storage_key(
    resolved_key: str,
    *,
    actor_id: str,
    tenant_id: str,
) -> str:
    material = "\x00".join(
        [
            "management-nl-v2",
            str(actor_id or "").strip(),
            str(tenant_id or "").strip(),
            str(resolved_key or "").strip(),
        ]
    )
    return f"management-nl-v2:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"
# BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: ask and ask/stream are one durable
# use case with two transports. They share this single canonical scope
# route name (not the literal per-transport HTTP path) so a client can
# switch between the JSON and SSE transports with the same Idempotency-Key
# and still get exactly-once command admission/replay.
def _mgmt_nl_surface_confidence(surfaces: Dict[str, Any]) -> str:
    statuses = [v.get("status", "unavailable") for v in surfaces.values() if isinstance(v, dict)]
    if not statuses:
        return "unavailable"
    if all(s == "ok" for s in statuses):
        return "high"
    if all(s == "unavailable" for s in statuses):
        return "unavailable"
    return "partial"
def _mgmt_nl_caller_tenant(
    identity: OperatorIdentity,
    *,
    requested_tenant: Optional[str] = None,
) -> str:
    tenant = _bff_me_tenant_payload(identity, requested_tenant=requested_tenant)
    return str(tenant.get("id") or "pantheon-dev")
def _mgmt_nl_scope_values(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[\s,]+", value) if part.strip()]
    if isinstance(value, dict):
        values: List[str] = []
        for key in ("id", "tenant_id", "tenantId", "value", "name"):
            if value.get(key) not in (None, ""):
                values.extend(_mgmt_nl_scope_values(value.get(key)))
        return values
    if isinstance(value, (list, tuple, set)):
        values: List[str] = []
        for item in value:
            values.extend(_mgmt_nl_scope_values(item))
        return values
    return [str(value).strip()]
def _mgmt_nl_record_tenant_ids(record: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    direct_keys = (
        "tenant_id",
        "tenantId",
        "tenant",
        "tenant_ref",
        "tenantRef",
        "org_id",
        "orgId",
        "organization_id",
        "organizationId",
        "workspace_id",
        "workspaceId",
    )
    for key in direct_keys:
        if key in record:
            values.extend(_mgmt_nl_scope_values(record.get(key)))
    for key in ("metadata", "scope", "sourceRecord", "source_record", "source_document", "target_ref"):
        nested = record.get(key)
        if isinstance(nested, dict):
            values.extend(_mgmt_nl_record_tenant_ids(nested))
    seen = set()
    result: List[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result
def _mgmt_nl_record_matches_tenant(record: Dict[str, Any], tenant_id: Optional[str]) -> bool:
    clean_tenant = str(tenant_id or "").strip()
    if not clean_tenant:
        return True
    record_tenants = _mgmt_nl_record_tenant_ids(record)
    if not record_tenants:
        return True
    return "*" in record_tenants or clean_tenant in record_tenants
def _mgmt_nl_filter_tenant_records(
    records: List[Dict[str, Any]],
    tenant_id: Optional[str],
) -> List[Dict[str, Any]]:
    return [
        record
        for record in records
        if isinstance(record, dict) and _mgmt_nl_record_matches_tenant(record, tenant_id)
    ]
def _mgmt_nl_add_entity(
    entities: Set[Tuple[str, str]],
    entity_type: str,
    entity_ref: Any,
) -> None:
    clean_type = str(entity_type or "").strip().lower()
    clean_ref = str(entity_ref or "").strip()
    if clean_type and clean_ref:
        entities.add((clean_type, clean_ref))
def _mgmt_nl_add_record_entities(
    entities: Set[Tuple[str, str]],
    records: List[Dict[str, Any]],
    entity_type: str,
    *keys: str,
) -> None:
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                _mgmt_nl_add_entity(entities, entity_type, value)
def _mgmt_nl_scoped_runtime_rows(
    runtime_bindings: List[Dict[str, Any]],
    entities: Set[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for binding in runtime_bindings:
        runtime_id = str(binding.get("runtime_id") or binding.get("id") or binding.get("binding_id") or "").strip()
        binding_id = str(binding.get("binding_id") or binding.get("runtime_binding_id") or binding.get("id") or "").strip()
        _mgmt_nl_add_entity(entities, "runtime", runtime_id)
        _mgmt_nl_add_entity(entities, "runtime_binding", binding_id)
        if binding.get("capital_pool_id"):
            _mgmt_nl_add_entity(entities, "capital_pool", binding.get("capital_pool_id"))
        rows.append(_project_operator_runtime_state_row(binding))
    return rows
def _mgmt_nl_trading_pulse_snippet(
    runtime_bindings: List[Dict[str, Any]],
    entities: Set[Tuple[str, str]],
) -> Dict[str, Any]:
    runtime_rows = _mgmt_nl_scoped_runtime_rows(runtime_bindings, entities)
    telemetry_rows = [
        row.get("telemetry_summary")
        for row in runtime_rows
        if isinstance(row.get("telemetry_summary"), dict)
    ]
    telemetry_observations = [
        row.get(key)
        for row in runtime_rows
        for key in ("telemetry_observation", "monitoring_observation", "rollback_observation")
        if isinstance(row.get(key), dict)
    ]
    pnl_values = [
        value
        for value in (_management_number((row.get("metrics") or {}).get("pnl")) for row in telemetry_rows)
        if value is not None
    ]
    fill_rate_values = [
        value
        for value in (_management_number((row.get("metrics") or {}).get("fill_rate")) for row in telemetry_rows)
        if value is not None
    ]
    trade_values = [
        value
        for value in (_management_number((row.get("metrics") or {}).get("total_trades")) for row in telemetry_rows)
        if value is not None
    ]
    summary = {
        "runtimeCount": len(runtime_rows),
        "runtime_count": len(runtime_rows),
        "telemetryCoverageCount": len(telemetry_rows),
        "telemetry_coverage_count": len(telemetry_rows),
        "byStatus": _management_count_by(runtime_rows, "status"),
        "by_status": _management_count_by(runtime_rows, "status"),
        "byStage": _management_count_by(runtime_rows, "deployment_stage"),
        "by_stage": _management_count_by(runtime_rows, "deployment_stage"),
        "totalPnl": round(sum(pnl_values), 6) if pnl_values else None,
        "total_pnl": round(sum(pnl_values), 6) if pnl_values else None,
        "averageFillRate": _management_avg(fill_rate_values),
        "average_fill_rate": _management_avg(fill_rate_values),
        "totalTrades": int(sum(trade_values)) if trade_values else 0,
        "total_trades": int(sum(trade_values)) if trade_values else 0,
    }
    cards = [
        {"cardId": "runtime-status", "card_id": "runtime-status", "label": "Runtime Status", "value": len(runtime_rows)},
        {"cardId": "pnl", "card_id": "pnl", "label": "P&L", "value": summary["totalPnl"]},
        {"cardId": "execution-quality", "card_id": "execution-quality", "label": "Execution Quality", "value": summary["averageFillRate"]},
    ]
    return {"summary": summary, "cards": cards, "telemetry_observations": telemetry_observations}
def _mgmt_nl_surface_owner_observation(
    surface: Optional[Dict[str, Any]],
    *,
    subject_type: str,
    owner: str,
) -> Dict[str, Any]:
    """Convert a dataset/aggregate surface-status dict into an owner
    observation shape so a cockpit source's real availability (e.g. the
    incident feed, approval queue, or sentinel findings) can be merged the
    same way as a typed context-service observation instead of being
    silently dropped because it never went through that service."""
    surface = surface if isinstance(surface, dict) else {}
    status = str(surface.get("status") or "unavailable")
    reason = surface.get("degradation_reason") or surface.get("message") or surface.get("note")
    return {
        "subject_type": subject_type,
        "subject_id": subject_type,
        "status": status,
        "owner": surface.get("owner") or owner,
        "source_kind": surface.get("source_kind") or surface.get("source") or ("live" if status == "ok" else "unavailable"),
        "source_version": surface.get("source_version"),
        "observed_at": surface.get("observed_at"),
        "freshness_seconds": surface.get("freshness_seconds"),
        "correlation_id": surface.get("correlation_id"),
        "degradation_reason": reason if status != "ok" else None,
        "contributing_observations": [],
    }
def _mgmt_nl_payload_surface_observations(
    payload: Optional[Dict[str, Any]],
    *,
    owner: str,
) -> List[Dict[str, Any]]:
    """Turn every surface entry in a payload's meta.surfaces into an owner
    observation. Every contributing surface a cockpit source reports
    (e.g. incident_feed, approval_queue, sentinel_findings), not only the
    payload's own top-level aggregate, must be preserved so a healthy
    runtime/telemetry read cannot mask one of them going unavailable."""
    surfaces = ((payload or {}).get("meta") or {}).get("surfaces") or {}
    return [
        _mgmt_nl_surface_owner_observation(surface, subject_type=key, owner=owner)
        for key, surface in surfaces.items()
        if isinstance(surface, dict)
    ]
def _mgmt_nl_merge_owner_observations(
    observations: List[Optional[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Aggregate every contributing owner observation into one surface-level
    observation instead of reporting only the runtime binding's status: a
    degraded/unavailable contributor (e.g. telemetry) must not be masked by
    another contributor's healthy status, and no contributor's provenance is
    discarded even when it did not determine the worst status."""
    status_rank = {"ok": 0, "degraded": 1, "unavailable": 2}
    present = [obs for obs in observations if isinstance(obs, dict)]
    if not present:
        return {
            "status": "unavailable",
            "owner": "management_ai_context",
            "source_kind": "unavailable",
            "degradation_reason": "no contributing owner observation was collected.",
            "contributing_observations": [],
        }
    worst = max(present, key=lambda obs: status_rank.get(str(obs.get("status")), 0))
    degradation_reasons = [
        str(obs.get("degradation_reason"))
        for obs in present
        if obs.get("degradation_reason")
    ]
    merged = dict(worst)
    merged["degradation_reason"] = "; ".join(dict.fromkeys(degradation_reasons)) or worst.get("degradation_reason")
    merged["contributing_observations"] = present
    return merged
def _mgmt_nl_collect_context(focus: str, snapshot_at: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
    """Collect management summary context for the requested focus surface(s).

    BFF-B6-001-SEC-FIX: accepts optional tenant_id to scope retrieved data.
    """
    use_all = focus in ("all", "")
    snippets: Dict[str, Any] = {}
    surfaces: Dict[str, Any] = {}
    evidence_entities: Set[Tuple[str, str]] = set()
    evidence_source_types: Set[str] = set()

    # Authorization scoping must happen before any owner/provenance is derived
    # from a record list, or a foreign tenant's owner/source_version/
    # correlation_id can leak into this tenant's observation even when the
    # authorized record count is zero. Pass this into the service so the
    # filter runs before provenance derivation, not after.
    def _tenant_record_filter(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return _mgmt_nl_filter_tenant_records(records, tenant_id)

    if use_all or focus == "cockpit":
        try:
            alerts_payload = _build_operator_alerts_payload(snapshot_at)
            alerts = _mgmt_nl_filter_tenant_records(
                list(alerts_payload.get("alerts") or []),
                tenant_id,
            )
            human_inbox_payload = _human_inbox_payload(snapshot_at, page_size=None)
            inbox_items = _mgmt_nl_filter_tenant_records(
                list(human_inbox_payload.get("items") or []),
                tenant_id,
            )
            anomalies_payload = _build_management_anomalies_payload(snapshot_at)
            anomalies = _mgmt_nl_filter_tenant_records(
                list(anomalies_payload.get("items") or []),
                tenant_id,
            )
            runtime_bindings, runtime_bindings_obs = _management_ai_context_service.get_context_runtime_bindings(
                record_filter=_tenant_record_filter
            )
            trading_pulse = _mgmt_nl_trading_pulse_snippet(runtime_bindings, evidence_entities)
            _mgmt_nl_add_record_entities(evidence_entities, alerts, "alert", "alert_id", "id")
            _mgmt_nl_add_record_entities(evidence_entities, inbox_items, "human_inbox", "id", "item_id")
            _mgmt_nl_add_record_entities(evidence_entities, anomalies, "incident", "id")
            evidence_source_types.update({
                "alert",
                "incident",
                "approval",
                "human_inbox",
                "runtime",
                "runtime_binding",
                "telemetry",
            })
            snippets["cockpit"] = {
                "trading_pulse_summary": trading_pulse.get("summary"),
                "alerts_summary": _build_alert_summary(alerts),
                "human_inbox_summary": {"total": len(inbox_items)},
                "anomalies_summary": {"total": len(anomalies)},
            }
            cockpit_owner_observation = _mgmt_nl_merge_owner_observations(
                [
                    runtime_bindings_obs,
                    *trading_pulse.get("telemetry_observations", []),
                    # Every cockpit source's own contributing surfaces, not
                    # just runtime/telemetry: an unavailable incident feed,
                    # approval queue, or sentinel-findings surface must not be
                    # masked behind a healthy runtime/telemetry status.
                    *_mgmt_nl_payload_surface_observations(alerts_payload, owner="operator_alerts"),
                    *_mgmt_nl_payload_surface_observations(human_inbox_payload, owner="human_inbox"),
                    *_mgmt_nl_payload_surface_observations(anomalies_payload, owner="management_anomalies"),
                ]
            )
            surfaces["management_cockpit"] = {
                "status": cockpit_owner_observation["status"],
                "source": "bff_composed",
                "owner_observation": cockpit_owner_observation,
            }
        except Exception:
            surfaces["management_cockpit"] = {"status": "unavailable", "source": "error"}

    if use_all or focus == "trading_pulse":
        try:
            runtime_bindings, runtime_bindings_obs = _management_ai_context_service.get_context_runtime_bindings(
                record_filter=_tenant_record_filter
            )
            pulse_data = _mgmt_nl_trading_pulse_snippet(runtime_bindings, evidence_entities)
            evidence_source_types.update({"runtime", "runtime_binding", "telemetry", "paper_live_drift"})
            snippets["trading_pulse"] = {
                "summary": pulse_data.get("summary"),
                "cards": pulse_data.get("cards"),
            }
            trading_pulse_owner_observation = _mgmt_nl_merge_owner_observations(
                [runtime_bindings_obs, *pulse_data.get("telemetry_observations", [])]
            )
            surfaces["management_trading_pulse"] = {
                "status": trading_pulse_owner_observation["status"],
                "source": "bff_composed",
                "owner_observation": trading_pulse_owner_observation,
            }
        except Exception:
            surfaces["management_trading_pulse"] = {"status": "unavailable", "source": "error"}

    if use_all or focus == "portfolio":
        try:
            pools, pools_obs = _management_ai_context_service.get_context_capital_pools(
                record_filter=_tenant_record_filter
            )
            runtime_bindings, runtime_bindings_obs = _management_ai_context_service.get_context_runtime_bindings(
                record_filter=_tenant_record_filter
            )
            _mgmt_nl_add_record_entities(evidence_entities, pools, "capital_pool", "pool_id", "id")
            _mgmt_nl_add_record_entities(evidence_entities, runtime_bindings, "runtime", "runtime_id", "id", "binding_id")
            evidence_source_types.update({"capital_pool", "runtime", "runtime_binding", "telemetry"})
            telemetry_results = [
                _management_ai_context_service.get_context_telemetry_summary(
                    str(r.get("runtime_id") or r.get("id") or r.get("binding_id") or "")
                )
                for r in runtime_bindings
                if r.get("runtime_id") or r.get("id") or r.get("binding_id")
            ]
            telemetry_values = [t for t, _obs in telemetry_results if t is not None]
            telemetry_observations = [obs for _t, obs in telemetry_results]
            portfolio_rollup = _management_telemetry_rollup(telemetry_values)
            snippets["portfolio"] = {
                "capital_pool_count": len(pools),
                "runtime_count": len(runtime_bindings),
                "total_pnl": portfolio_rollup.get("total_pnl"),
                "max_drawdown": portfolio_rollup.get("max_drawdown"),
                "average_fill_rate": portfolio_rollup.get("average_fill_rate"),
                "total_trades": portfolio_rollup.get("total_trades"),
            }
            # Aggregate every contributing owner observation instead of only
            # looking at telemetry: a runtime/pool read failure must not be
            # masked by another surface's success (e.g. pools present while
            # runtime bindings raised).
            contributing_statuses = [
                pools_obs.get("status"),
                runtime_bindings_obs.get("status"),
                *[obs.get("status") for obs in telemetry_observations],
            ]
            if any(status == "unavailable" for status in contributing_statuses):
                portfolio_status = "unavailable"
            elif any(status != "ok" for status in contributing_statuses):
                portfolio_status = "degraded"
            else:
                portfolio_status = "ok"
            surfaces["portfolio_book"] = {
                "status": portfolio_status,
                "source": "bff_composed",
                "owner_observations": [pools_obs, runtime_bindings_obs, *telemetry_observations],
            }
        except Exception:
            surfaces["portfolio_book"] = {"status": "unavailable", "source": "error"}

    if use_all or focus == "persona_fleet":
        try:
            personas, personas_obs = _management_ai_context_service.get_context_personas(
                lambda: _list_persona_records(tenant_id), record_filter=_tenant_record_filter
            )
            runtime_bindings, runtime_bindings_obs = _management_ai_context_service.get_context_runtime_bindings(
                record_filter=_tenant_record_filter
            )
            incidents, incidents_obs = _management_ai_context_service.get_context_incidents(
                record_filter=_tenant_record_filter
            )
            evolution_decisions, evolution_decisions_obs = _management_ai_context_service.get_context_evolution_decisions(
                record_filter=_tenant_record_filter
            )
            # Telemetry is read exactly once per tenant-scoped runtime
            # binding, through the same typed owner-observation query used by
            # the portfolio_book surface, and that single result is shared by
            # both the per-persona snippet items below and the surface-level
            # owner_observations aggregate. A second independent read of the
            # same runtime could observe a different outcome than the first
            # (e.g. a flaky provider that fails once and recovers), which
            # would let the snippet and the surface silently disagree about
            # the same runtime's telemetry.
            telemetry_by_runtime_id: Dict[str, Tuple[Optional[Dict[str, Any]], Dict[str, Any]]] = {}
            for runtime_binding in runtime_bindings:
                fleet_runtime_id = str(
                    runtime_binding.get("runtime_id")
                    or runtime_binding.get("id")
                    or runtime_binding.get("binding_id")
                    or ""
                )
                if not fleet_runtime_id or fleet_runtime_id in telemetry_by_runtime_id:
                    continue
                telemetry_by_runtime_id[fleet_runtime_id] = _management_ai_context_service.get_context_telemetry_summary(
                    fleet_runtime_id, record_filter=_tenant_record_filter
                )
            telemetry_observations = [obs for _summary, obs in telemetry_by_runtime_id.values()]
            # Project every persona independently: a raise from one persona's
            # bindings/telemetry/teaching-session owner must not discard the
            # personas that already projected successfully, nor the
            # runtime/incidents/evolution provenance already collected above.
            fleet_items = []
            fleet_owner_observations: List[Dict[str, Any]] = []
            for persona in personas:
                item, item_owner_observations = _project_persona_fleet_item(
                    persona,
                    all_runtime_bindings=runtime_bindings,
                    all_incidents=incidents,
                    all_evolution_decisions=evolution_decisions,
                    telemetry_by_runtime_id=telemetry_by_runtime_id,
                    tenant_id=tenant_id,
                )
                if len(fleet_items) < 20:
                    fleet_items.append(item)
                # Telemetry observations are already carried once per unique
                # runtime in telemetry_observations above; only the
                # per-persona-only owners (bindings, teaching sessions) are
                # added here to avoid duplicating the same runtime's
                # observation for every persona that happens to match it.
                fleet_owner_observations.extend(
                    observation
                    for observation in item_owner_observations
                    if observation.get("subject_type") != "telemetry"
                )
            _mgmt_nl_add_record_entities(evidence_entities, personas, "persona", "persona_id", "id")
            _mgmt_nl_add_record_entities(evidence_entities, runtime_bindings, "runtime", "runtime_id", "id", "binding_id")
            _mgmt_nl_add_record_entities(evidence_entities, incidents, "incident", "incident_id", "id")
            _mgmt_nl_add_record_entities(evidence_entities, evolution_decisions, "evolution_decision", "decision_id", "id")
            evidence_source_types.update({"persona", "runtime", "runtime_binding", "incident", "evolution_decision"})
            fleet_summary = {
                "total_personas": len(personas),
                "returned_personas": len(fleet_items),
                "critical_personas": len([item for item in fleet_items if item["health"]["status"] == "critical"]),
                "degraded_personas": len([item for item in fleet_items if item["health"]["status"] == "degraded"]),
                "healthy_personas": len([item for item in fleet_items if item["health"]["status"] == "healthy"]),
                "bound_personas": len([item for item in fleet_items if item["bindings"]]),
                "runtime_bound_personas": len([item for item in fleet_items if item["runtimeBindings"]]),
            }
            snippets["persona_fleet"] = {
                "total": len(fleet_items),
                "summary": fleet_summary,
                "items": fleet_items,
            }
            # Persona reads, per-persona bindings/teaching-session reads, and
            # per-runtime telemetry reads are all contributing owners too: a
            # healthy runtime/incidents/evolution aggregate must not mask a
            # persona, binding, teaching-session, or telemetry owner that
            # itself reported unavailable/degraded, or that owner silently
            # disappears from both the status and owner_observations.
            fleet_contributing_statuses = [
                personas_obs.get("status"),
                runtime_bindings_obs.get("status"),
                incidents_obs.get("status"),
                evolution_decisions_obs.get("status"),
                *[observation.get("status") for observation in telemetry_observations],
                *[observation.get("status") for observation in fleet_owner_observations],
            ]
            if not personas:
                fleet_status = "unavailable"
            elif any(status == "unavailable" for status in fleet_contributing_statuses):
                fleet_status = "unavailable"
            elif any(status != "ok" for status in fleet_contributing_statuses):
                fleet_status = "degraded"
            else:
                fleet_status = "ok"
            surfaces["persona_fleet"] = {
                "status": fleet_status,
                "source": "bff_composed",
                "owner_observations": [
                    personas_obs,
                    runtime_bindings_obs,
                    incidents_obs,
                    evolution_decisions_obs,
                    *telemetry_observations,
                    *fleet_owner_observations,
                ],
            }
        except Exception:
            surfaces["persona_fleet"] = {"status": "unavailable", "source": "error"}

    return {
        "snippets": snippets,
        "surfaces": surfaces,
        "evidence_entities": evidence_entities,
        "evidence_source_types": evidence_source_types,
    }
def _mgmt_nl_synthesize_answer(question: str, snippets: Dict[str, Any], focus: str) -> str:
    """
    Compose a plain-text management answer grounded in the collected snippets.
    This is a structured synthesis layer — not an external LLM call.
    """
    parts: List[str] = []

    cockpit = snippets.get("cockpit") or {}
    pulse = snippets.get("trading_pulse") or {}
    portfolio = snippets.get("portfolio") or {}
    fleet = snippets.get("persona_fleet") or {}

    if cockpit:
        alerts = cockpit.get("alerts_summary") or {}
        inbox = cockpit.get("human_inbox_summary") or {}
        anomalies = cockpit.get("anomalies_summary") or {}
        pulse_summary = cockpit.get("trading_pulse_summary") or {}
        if alerts.get("total_active") is not None:
            parts.append(f"Active alerts: {alerts['total_active']}.")
        if inbox.get("total") is not None:
            parts.append(f"Human inbox items: {inbox['total']}.")
        if anomalies.get("total") is not None:
            parts.append(f"Anomalies: {anomalies['total']}.")
        if pulse_summary.get("runtimeCount") is not None:
            parts.append(f"Runtimes in cockpit: {pulse_summary['runtimeCount']}.")

    if pulse:
        pulse_s = pulse.get("summary") or {}
        if pulse_s.get("totalPnl") is not None:
            parts.append(f"Total PnL: {pulse_s['totalPnl']:.4f}.")
        if pulse_s.get("runtimeCount") is not None:
            parts.append(f"Runtime count (trading pulse): {pulse_s['runtimeCount']}.")
        if pulse_s.get("averageFillRate") is not None:
            parts.append(f"Average fill rate: {pulse_s['averageFillRate']:.2%}.")

    if portfolio:
        if portfolio.get("capital_pool_count") is not None:
            parts.append(f"Capital pools: {portfolio['capital_pool_count']}.")
        if portfolio.get("runtime_count") is not None:
            parts.append(f"Runtime bindings: {portfolio['runtime_count']}.")
        if portfolio.get("total_pnl") is not None:
            parts.append(f"Portfolio total PnL: {portfolio['total_pnl']:.4f}.")
        if portfolio.get("max_drawdown") is not None:
            parts.append(f"Max drawdown: {portfolio['max_drawdown']:.4f}.")
        if portfolio.get("total_trades") is not None:
            parts.append(f"Total trades: {int(portfolio['total_trades'])}.")

    if fleet:
        total = fleet.get("total")
        if total is not None:
            parts.append(f"Persona fleet size: {total}.")

    if not parts:
        return (
            f"Management data is currently unavailable for the requested focus ({focus}). "
            "Please retry when management surfaces are reachable."
        )

    intro = f"Management summary for question: '{question}'. "
    return intro + " ".join(parts)
def _mgmt_nl_provider_feature_enabled() -> bool:
    for env_name in (
        "PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED",
        "PANTHEON_MGMT_NL_ASSISTANT_PROVIDER_ENABLED",
    ):
        if os.getenv(env_name) is not None:
            return auth_policy.bool_from_env(env_name)
    return auth_policy.bool_from_env("PANTHEON_ASSISTANT_ENABLED")
def _mgmt_nl_provider_name() -> str:
    return (os.getenv("PANTHEON_ASSISTANT_PROVIDER", "openclaw").strip().lower() or "openclaw")
_MGMT_NL_PROVIDER_REASON_MESSAGES = {
    "CODEX_AUTH_UNAVAILABLE": (
        "Codex service-user session expired. Re-login the dedicated Pantheon "
        "assistant Codex account; Management AI is serving deterministic "
        "fallback until the provider is healthy."
    ),
    "CLAUDE_AUTH_UNAVAILABLE": (
        "Claude service-user session is unavailable. Re-login the dedicated "
        "Pantheon assistant Claude account; Management AI is serving "
        "deterministic fallback until the provider is healthy."
    ),
    "OPENCLAW_ADAPTER_UNREACHABLE": (
        "OpenClaw adapter is unreachable. Management AI is serving "
        "deterministic fallback until the adapter is healthy."
    ),
    "OPENCLAW_ADAPTER_REQUEST_FAILED": (
        "OpenClaw adapter request failed. Management AI is serving "
        "deterministic fallback until the adapter request path is healthy."
    ),
    "OPENCLAW_ADAPTER_HTTP_ERROR": (
        "OpenClaw adapter returned an error. Management AI is serving "
        "deterministic fallback until the provider path is healthy."
    ),
    "CLAUDE_BINARY_NOT_FOUND": (
        "Claude CLI binary is unavailable in the assistant runtime. Management "
        "AI is serving deterministic fallback until the runtime is repaired."
    ),
    "ASSISTANT_PROVIDER_NOT_SUPPORTED": (
        "Configured assistant provider is not supported. Management AI is "
        "serving deterministic fallback until the provider configuration is "
        "updated."
    ),
    "PROVIDER_EMPTY_ANSWER": (
        "Assistant provider returned no answer. Management AI is serving "
        "deterministic fallback for this request."
    ),
    "UNSUPPORTED_PROVIDER": (
        "Configured assistant provider is not supported. Management AI is "
        "serving deterministic fallback until the provider configuration is "
        "updated."
    ),
    "FEATURE_DISABLED": (
        "Management AI provider is disabled by configuration. The response is "
        "deterministic fallback."
    ),
    "PROVIDER_DISABLED": (
        "Management AI provider is disabled by configuration. The response is "
        "deterministic fallback."
    ),
}
_MGMT_NL_PROVIDER_REASON_ACTIONS = {
    "CODEX_AUTH_UNAVAILABLE": "reauth_codex_service_user",
    "CLAUDE_AUTH_UNAVAILABLE": "reauth_claude_service_user",
    "OPENCLAW_ADAPTER_UNREACHABLE": "restore_openclaw_adapter",
    "OPENCLAW_ADAPTER_REQUEST_FAILED": "inspect_openclaw_adapter_request_path",
    "OPENCLAW_ADAPTER_HTTP_ERROR": "inspect_openclaw_adapter_response",
    "CLAUDE_BINARY_NOT_FOUND": "install_claude_cli",
    "ASSISTANT_PROVIDER_NOT_SUPPORTED": "configure_supported_management_ai_provider",
    "UNSUPPORTED_PROVIDER": "configure_supported_management_ai_provider",
    "FEATURE_DISABLED": "enable_management_ai_provider",
    "PROVIDER_DISABLED": "enable_management_ai_provider",
    "PROVIDER_EMPTY_ANSWER": "inspect_management_ai_provider_output",
}
def _mgmt_nl_provider_reason_key(reason: Optional[str]) -> Optional[str]:
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        return None
    return clean_reason.upper()
def _mgmt_nl_provider_status_notice(
    *,
    provider: str,
    status: str,
    reason: Optional[str],
    used: bool,
) -> Dict[str, str]:
    clean_status = str(status or "").strip().lower()
    if used or clean_status in {"completed", "ok"}:
        return {}

    reason_key = _mgmt_nl_provider_reason_key(reason)
    provider_key = str(provider or "").strip().lower()
    message = (
        _MGMT_NL_PROVIDER_REASON_MESSAGES.get(reason_key or "")
        if reason_key
        else None
    )
    action = (
        _MGMT_NL_PROVIDER_REASON_ACTIONS.get(reason_key or "")
        if reason_key
        else None
    )
    if reason_key is None and clean_status == "degraded":
        message = (
            "Assistant provider is degraded. Management AI is serving "
            "deterministic fallback until the provider is healthy."
        )
    if message is None and reason_key and "AUTH" in reason_key and "UNAVAILABLE" in reason_key:
        provider_label = "Codex" if "codex" in provider_key else "assistant"
        message = (
            f"{provider_label} service-user session is unavailable. Re-login "
            "the dedicated Pantheon assistant account; Management AI is "
            "serving deterministic fallback until the provider is healthy."
        )
        action = action or (
            "reauth_codex_service_user"
            if "codex" in provider_key
            else "reauth_assistant_service_user"
        )
    if message is None:
        message = (
            "Assistant provider is not available. Management AI is serving "
            "deterministic fallback until the provider is healthy."
        )
    return {
        "severity": "warning" if clean_status in {"degraded", "disabled"} else "info",
        "display_message": message,
        "operator_action": action or "inspect_management_ai_provider_status",
    }
def _mgmt_nl_provider_status(
    *,
    provider: str,
    enabled: bool,
    status: str,
    reason: Optional[str] = None,
    run_id: Optional[str] = None,
    used: bool = False,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "enabled": enabled,
        "provider": provider,
        "runtime": "openclaw_gateway_cli_mount",
        "status": status,
        "used": used,
        "fallback": None if used else "deterministic_synthesis",
    }
    if reason:
        payload["reason"] = reason
        payload["reason_code"] = reason
    payload.update(
        _mgmt_nl_provider_status_notice(
            provider=provider,
            status=status,
            reason=reason,
            used=used,
        )
    )
    if run_id:
        payload["run_id"] = run_id
    return payload
def _mgmt_nl_provider_supports_multimodal(provider: str) -> bool:
    return str(provider or "").strip().lower() in {"codex", "codex_cli"}
def _mgmt_nl_multimodal_attachment_payload(
    attachments: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    image_parts: List[Dict[str, Any]] = []
    attachment_summaries: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    store = _management_ai_conversation_store()
    for attachment in attachments or []:
        if not isinstance(attachment, dict):
            continue
        attachment_id = str(
            attachment.get("id")
            or attachment.get("attachmentId")
            or attachment.get("attachment_id")
            or ""
        ).strip()
        mime_type = str(attachment.get("mimeType") or attachment.get("mime_type") or "").strip().lower()
        if not attachment_id or not mime_type.startswith("image/"):
            continue
        try:
            found = store.find_attachment(attachment_id)
            if found is None:
                raise FileNotFoundError(attachment_id)
            metadata, _turn = found
            content, resolved_mime_type, filename = store.read_attachment(attachment_id, metadata)
        except Exception as exc:  # noqa: BLE001 - provider should degrade, not fail the ask.
            errors.append(
                {
                    "attachmentId": attachment_id,
                    "attachment_id": attachment_id,
                    "reason": "attachment_unavailable",
                    "error": type(exc).__name__,
                }
            )
            continue
        clean_mime_type = str(resolved_mime_type or mime_type or "application/octet-stream").strip().lower()
        data_url = f"data:{clean_mime_type};base64,{base64.b64encode(content).decode('ascii')}"
        image_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": data_url},
                "attachmentId": attachment_id,
                "attachment_id": attachment_id,
                "mimeType": clean_mime_type,
                "mime_type": clean_mime_type,
                "filename": filename or attachment.get("filename") or attachment_id,
                "sizeBytes": len(content),
                "size_bytes": len(content),
                "source": "management_ai_attachment_store",
            }
        )
        attachment_summaries.append(
            {
                "attachmentId": attachment_id,
                "attachment_id": attachment_id,
                "kind": str(attachment.get("kind") or "image"),
                "mimeType": clean_mime_type,
                "mime_type": clean_mime_type,
                "filename": filename or attachment.get("filename") or attachment_id,
                "sizeBytes": len(content),
                "size_bytes": len(content),
                "source": "management_ai_attachment_store",
            }
        )
    return image_parts, attachment_summaries, errors
def _mgmt_nl_provider_multimodal_payload(
    *,
    prompt: str,
    attachments: Optional[List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    image_parts, attachment_summaries, errors = _mgmt_nl_multimodal_attachment_payload(attachments)
    if not image_parts and not errors:
        return None
    content = [{"type": "text", "text": prompt}, *image_parts]
    return {
        "messages": [{"role": "user", "content": content}],
        "attachments": image_parts,
        "summary": {
            "attempted": bool(attachments),
            "forwarded": bool(image_parts),
            "attachment_count": len(image_parts),
            "unavailable_attachment_count": len(errors),
            "attachments": attachment_summaries,
            "errors": errors,
        },
    }
def _mgmt_nl_multimodal_unsupported_error(exc: OpenClawOpsClientError) -> bool:
    code = str(getattr(exc, "error_code", "") or "").strip().lower()
    message = str(getattr(exc, "message", "") or str(exc)).strip().lower()
    unsupported_tokens = ("multimodal", "image", "vision", "attachment")
    return (
        "unsupported" in code
        and any(token in code for token in unsupported_tokens)
    ) or (
        "unsupported" in message
        and any(token in message for token in unsupported_tokens)
    )
def _mgmt_nl_context_status(confidence: str) -> str:
    if confidence == "high":
        return "ok"
    if confidence == "partial":
        return "degraded"
    return "unavailable"
def _mgmt_nl_evidence_entities_payload(entities: Any) -> List[Dict[str, str]]:
    return [
        {"entity_type": str(entity_type), "entity_ref": str(entity_ref)}
        for entity_type, entity_ref in sorted(list(entities or set()))
    ]
def _mgmt_nl_build_context_pack(
    *,
    session_id: str,
    question: str,
    focus: str,
    identity: OperatorIdentity,
    caller_tenant_id: str,
    snippets: Dict[str, Any],
    surfaces: Dict[str, Any],
    source_keys: List[str],
    confidence: str,
    evidence_entities: Any,
    evidence_source_types: Any,
    operator_context: str,
    conversation_context: Dict[str, Any],
    ui_snapshot: Dict[str, Any],
    control_mode: Dict[str, Any],
) -> Dict[str, Any]:
    from .context_composer import AssistantCollectedSource, compose_context_pack
    from .models import AssistantContextPackRequest, AssistantMode

    frontend_route = str(ui_snapshot.get("currentRoute") or "/management")
    selected_entity = _mgmt_nl_frontend_selected_entity(ui_snapshot, focus=focus)
    assistant_mode = AssistantMode.USER
    if isinstance(control_mode, dict) and control_mode.get("active"):
        try:
            assistant_mode = AssistantMode(str(control_mode.get("mode") or AssistantMode.KERNEL_DEBUG.value))
        except ValueError:
            assistant_mode = AssistantMode.KERNEL_DEBUG
    context_identity = _mgmt_nl_identity_with_control_mode(identity, control_mode)
    management_payload = {
        "question": question,
        "focus": focus,
        "tenant_id": caller_tenant_id,
        "sources": source_keys,
        "confidence": confidence,
        "conversation": conversation_context,
        "ui": ui_snapshot,
        "control_mode": control_mode,
        "operator_context": operator_context,
        "session": {
            "session_id": session_id,
            "ttl_seconds": _MGMT_AI_SESSION_TTL_SECONDS,
        },
        "summary_context": snippets,
        "surfaces": surfaces,
        "evidence_entities": _mgmt_nl_evidence_entities_payload(evidence_entities),
        "evidence_source_types": sorted(str(item) for item in (evidence_source_types or set())),
    }

    request = AssistantContextPackRequest(
        mode=assistant_mode,
        include=["ui", "management_nl", "persona_health"],
        question=question,
        route=frontend_route,
        frontend={
            "route": frontend_route,
            "selectedEntity": selected_entity,
            "contextRefs": [
                {"kind": "management_nl_session", "id": session_id},
                {"kind": "management_nl_focus", "id": focus},
            ],
        },
        focus={
            "entityType": selected_entity.get("entityType") or "management_nl_focus",
            "entityId": selected_entity.get("entityId") or focus,
            "label": selected_entity.get("label") or focus,
            "route": frontend_route,
        },
    )

    def collect_source(source_id: str, _request: Any, snapshot_at: str) -> Any:
        if source_id == "persona_health":
            persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
            scoped_personas = _mgmt_nl_filter_tenant_records(_list_persona_records(caller_tenant_id), caller_tenant_id)
            return AssistantCollectedSource(
                source_id="persona_health",
                href="/bff/v5/execution/persona-health",
                payload={
                    "items": [
                        {
                            "id": persona.get("persona_id") or persona.get("id"),
                            "persona_id": persona.get("persona_id") or persona.get("id"),
                            "name": persona.get("name") or persona.get("persona_id"),
                            "health": "healthy"
                            if persona.get("lifecycle_state") == "active"
                            else "degraded",
                            "lifecycle_state": persona.get("lifecycle_state"),
                        }
                        for persona in scoped_personas
                    ],
                    "meta": {
                        "snapshot_at": snapshot_at,
                        "surfaces": {"persona_health": persona_surface},
                    },
                },
                status=str(persona_surface.get("status") or "ok"),
                source_kind="bff",
            )
        if source_id != "management_nl":
            return _assistant_collect_source(source_id, _request, snapshot_at)
        return AssistantCollectedSource(
            source_id="management_nl",
            href="/bff/management/nl/ask",
            payload={
                "data": management_payload,
                "meta": {
                    "snapshot_at": snapshot_at,
                    "surfaces": {"management_nl": {"status": _mgmt_nl_context_status(confidence)}},
                },
            },
            status=_mgmt_nl_context_status(confidence),
            source_kind="bff",
        )

    pack = compose_context_pack(
        session_id=session_id,
        request=request,
        actor=context_identity,
        collect_source=collect_source,
    )
    return pack.model_dump(mode="json", by_alias=False)
def _mgmt_nl_provider_mode_from_context(context_pack: Dict[str, Any]) -> str:
    mode = str(context_pack.get("mode") or "user").strip()
    if mode in {"user", "kernel_observe", "kernel_debug"}:
        return mode
    return "user"
def _mgmt_nl_provider_control_metadata(context_pack: Dict[str, Any]) -> Dict[str, Any]:
    management_context = (
        ((context_pack.get("backend") or {}).get("management_nl") or {}).get("data") or {}
        if isinstance(context_pack, dict)
        else {}
    )
    control_mode = management_context.get("controlMode") or management_context.get("control_mode")
    if not isinstance(control_mode, dict):
        return {"active": False, "mode": "user"}
    return {
        "active": bool(control_mode.get("active")),
        "state": control_mode.get("state"),
        "mode": control_mode.get("mode") or "user",
        "activation_id": control_mode.get("activation_id") or control_mode.get("activationId"),
    }
def _mgmt_nl_reject_development_payload(
    payload: Dict[str, Any],
    *,
    identity: OperatorIdentity,
    caller_tenant_id: str,
    control_mode: Dict[str, Any],
) -> None:
    has_repair_payload = bool(payload.get("repair")) or any(
        isinstance(payload.get(key), dict)
        and bool(payload[key].get("repair") or payload[key].get("task"))
        for key in ("openclaw", "openClaw")
    )
    if has_repair_payload:
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_FAILED,
            "Development tooling is not a product BFF capability",
            "Use the local development-tooling worktree and task commands; product BFF does not prepare or authorize source writes.",
            precondition_failed="development_tooling",
        )
def _mgmt_nl_provider_mode_prompt_lines(provider_mode: str) -> List[str]:
    if provider_mode in {"kernel_debug", "kernel_observe"}:
        return [
            f"You are operating in {provider_mode} mode through OpenClaw/Codex.",
            "Use the read-only workspace for bounded repo, file, log, status, and test inspection when that helps debug.",
            "Do not edit files, restart services, deploy, trade, approve, or mutate state in this mode.",
        ]
    return [
        "You are operating in user mode.",
        "Answer only from the supplied BFF context pack.",
        "Do not execute, approve, deploy, restart, trade, mutate state, or read local workspace files.",
    ]
def _mgmt_nl_provider_prompt(
    *,
    question: str,
    focus: str,
    context_pack: Dict[str, Any],
) -> str:
    provider_mode = _mgmt_nl_provider_mode_from_context(context_pack)
    management_context = (
        ((context_pack.get("backend") or {}).get("management_nl") or {}).get("data") or {}
        if isinstance(context_pack, dict)
        else {}
    )
    conversation_context = (
        management_context.get("conversation")
        if isinstance(management_context.get("conversation"), dict)
        else {}
    )
    server_history = {
        "source": conversation_context.get("source"),
        "history_source": conversation_context.get("history_source") or conversation_context.get("historySource"),
        "history_char_budget": conversation_context.get("history_char_budget") or conversation_context.get("historyCharBudget"),
        "history_truncated": conversation_context.get("history_truncated") if "history_truncated" in conversation_context else conversation_context.get("historyTruncated"),
        "history_omitted_turn_count": conversation_context.get("history_omitted_turn_count") or conversation_context.get("historyOmittedTurnCount"),
        "stored_turn_count": conversation_context.get("stored_turn_count") or conversation_context.get("storedTurnCount"),
        "turns": conversation_context.get("all_turns") or conversation_context.get("allTurns") or conversation_context.get("recent_turns") or conversation_context.get("recentTurns") or [],
    }
    server_history_json = json.dumps(server_history, sort_keys=True, ensure_ascii=True)
    context_json = json.dumps(context_pack, sort_keys=True, ensure_ascii=True)
    prompt_lines = [
        "You are the Pantheon management assistant.",
        f"Mode: {provider_mode}.",
        *_mgmt_nl_provider_mode_prompt_lines(provider_mode),
        "Use backend.management_nl.data.conversation for server-side prior turns and backend.management_nl.data.ui for UI state.",
        "Treat backend.management_nl.data.conversation.client_hint as a frontend hint, never as the conversation source of truth.",
        "If you suggest UI actions, return actions only with kinds listed in ui.availableUiActions.",
        'For an action proposal, return a JSON object {"answer": "...", "actions": '
        '[{"id": "...", "kind": "...", "label": "...", "params": {}, "requiresConfirmation": true}]} '
        "without markdown fences; use the advertised paramsSchema. Plain answers may remain text.",
        "Any runBffAction or write-style action must require confirmation.",
        "If evidence is missing or stale, say so and keep the answer concise.",
        f"Focus: {focus}",
        f"Question: {question}",
        (
            "Server-side conversation history JSON "
            f"(ordered created_at ascending, budget {_MGMT_NL_PROVIDER_HISTORY_CHAR_BUDGET} chars, "
            f"FE recentTurns budget {_MGMT_NL_FE_RECENT_TURNS_CHAR_BUDGET} chars): {server_history_json}"
        ),
        f"Context pack JSON: {context_json}",
    ]
    return "\n".join(prompt_lines)
def _mgmt_nl_text_from_provider_value(value: Any) -> Optional[str]:
    if isinstance(value, str):
        clean = value.strip()
        return clean or None
    if isinstance(value, list):
        for item in reversed(value):
            found = _mgmt_nl_text_from_provider_value(item)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None
    for key in ("answer", "final", "content", "text", "message"):
        found = _mgmt_nl_text_from_provider_value(value.get(key))
        if found:
            return found
    for key in ("item", "delta", "output"):
        found = _mgmt_nl_text_from_provider_value(value.get(key))
        if found:
            return found
    events = value.get("json_events")
    if isinstance(events, list):
        found = _mgmt_nl_text_from_provider_value(events)
        if found:
            return found
    stdout = value.get("stdout")
    if isinstance(stdout, str):
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                loaded = json.loads(line)
            except json.JSONDecodeError:
                return line
            found = _mgmt_nl_text_from_provider_value(loaded)
            if found:
                return found
    return None
def _mgmt_nl_extract_provider_answer(payload: Dict[str, Any]) -> Optional[str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        return _mgmt_nl_text_from_provider_value(data.get("output"))
    # Claude and other flat-format providers return the answer in a top-level
    # "text" field rather than nesting it under data.output.
    return _mgmt_nl_text_from_provider_value(payload)
_MGMT_NL_COMPLETED_PROVIDER_STATES = {"completed", "ok", "success", "succeeded"}
_MGMT_NL_PROVIDER_DEADLINE_DEFAULT_SECONDS = 45.0
def _mgmt_nl_provider_deadline_seconds() -> float:
    raw = os.getenv("PANTHEON_MANAGEMENT_NL_PROVIDER_DEADLINE_SECONDS")
    if raw is None or not str(raw).strip():
        return _MGMT_NL_PROVIDER_DEADLINE_DEFAULT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _MGMT_NL_PROVIDER_DEADLINE_DEFAULT_SECONDS
    return min(300.0, max(0.1, value))
def _mgmt_nl_provider_candidates(primary: str) -> List[str]:
    configured: List[str] = [str(primary or "").strip().lower()]
    for env_name in (
        "PANTHEON_MANAGEMENT_NL_ASSISTANT_FALLBACK_PROVIDERS",
        "PANTHEON_MGMT_NL_ASSISTANT_FALLBACK_PROVIDERS",
    ):
        raw = os.getenv(env_name, "")
        configured.extend(item.strip().lower() for item in raw.split(","))
    candidates: List[str] = []
    for provider in configured:
        if provider and provider not in candidates:
            candidates.append(provider)
    return candidates
def _mgmt_nl_provider_attempt_summary(status: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": status.get("provider"),
        "status": status.get("status"),
        "used": bool(status.get("used")),
        "reason": status.get("reason"),
        "run_id": status.get("run_id"),
    }
def _mgmt_nl_provider_degraded_reason(payload: Dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload, dict) else {}
    output = data.get("output") if isinstance(data, dict) else {}
    for source in (output, data, payload):
        if not isinstance(source, dict):
            continue
        for key in ("error_code", "reason", "degraded_reason", "diagnostic_reason"):
            value = str(source.get(key) or "").strip()
            if value:
                return value.upper()
    return "PROVIDER_RESPONSE_DEGRADED"
def _mgmt_nl_maybe_provider_answer(
    *,
    provider: str,
    question: str,
    focus: str,
    identity: OperatorIdentity,
    caller_tenant_id: str,
    session_id: str,
    message_id: str,
    trace_id: str,
    context_pack: Dict[str, Any],
    audit_id: Optional[str],
    allowed_action_kinds: Set[str],
    current_user_attachments: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[str], Dict[str, Any], List[Dict[str, Any]]]:
    candidates = _mgmt_nl_provider_candidates(provider)
    deadline_seconds = _mgmt_nl_provider_deadline_seconds()
    deadline = time.monotonic() + deadline_seconds
    attempts: List[Dict[str, Any]] = []
    primary_status: Optional[Dict[str, Any]] = None

    for attempt_index, candidate in enumerate(candidates):
        answer, status, actions = _mgmt_nl_attempt_provider_answer(
            provider=candidate,
            question=question,
            focus=focus,
            identity=identity,
            caller_tenant_id=caller_tenant_id,
            session_id=session_id,
            message_id=message_id,
            trace_id=trace_id,
            context_pack=context_pack,
            audit_id=audit_id,
            allowed_action_kinds=allowed_action_kinds,
            current_user_attachments=current_user_attachments,
            provider_deadline=deadline,
            provider_attempt=attempt_index,
        )
        attempt_summary = _mgmt_nl_provider_attempt_summary(status)
        attempts.append(attempt_summary)
        if primary_status is None:
            primary_status = status
        if answer and status.get("used") is True:
            status["attempted_providers"] = attempts
            status["deadline_seconds"] = deadline_seconds
            if attempt_index:
                status["fallback"] = "provider_failover"
                status["fallback_from"] = str(provider or "").strip().lower()
                status["fallback_reason"] = primary_status.get("reason") if primary_status else None
            return answer, status, actions
        if str(status.get("status") or "").lower() == "disabled":
            break
        if time.monotonic() >= deadline:
            break

    terminal_status = dict(primary_status or _mgmt_nl_provider_status(
        provider=provider,
        enabled=True,
        status="degraded",
        reason="provider_deadline_exhausted",
    ))
    terminal_status["attempted_providers"] = attempts
    terminal_status["deadline_seconds"] = deadline_seconds
    return None, terminal_status, []
def _mgmt_nl_attempt_provider_answer(
    *,
    provider: str,
    question: str,
    focus: str,
    identity: OperatorIdentity,
    caller_tenant_id: str,
    session_id: str,
    message_id: str,
    trace_id: str,
    context_pack: Dict[str, Any],
    audit_id: Optional[str],
    allowed_action_kinds: Set[str],
    current_user_attachments: Optional[List[Dict[str, Any]]] = None,
    provider_deadline: Optional[float] = None,
    provider_attempt: int = 0,
) -> Tuple[Optional[str], Dict[str, Any], List[Dict[str, Any]]]:
    enabled = _mgmt_nl_provider_feature_enabled()
    if provider in {"none", "off", "disabled", "deterministic"}:
        _management_ai_record_event(
            {
                "event_type": "management_ai.provider.skipped",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "actor_id": identity.operator_id,
                "provider": provider,
                "reason": "provider_disabled",
            }
        )
        return None, _mgmt_nl_provider_status(
            provider=provider,
            enabled=False,
            status="disabled",
            reason="provider_disabled",
        ), []
    if not enabled:
        _management_ai_record_event(
            {
                "event_type": "management_ai.provider.skipped",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "actor_id": identity.operator_id,
                "provider": provider,
                "reason": "feature_disabled",
            }
        )
        return None, _mgmt_nl_provider_status(
            provider=provider,
            enabled=False,
            status="disabled",
            reason="feature_disabled",
        ), []
    if provider not in {"codex", "codex_cli", "claude", "claude_cli", "openclaw", "openclaw_agent"}:
        _management_ai_record_event(
            {
                "event_type": "management_ai.provider.skipped",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "actor_id": identity.operator_id,
                "provider": provider,
                "reason": "unsupported_provider",
            }
        )
        return None, _mgmt_nl_provider_status(
            provider=provider,
            enabled=True,
            status="degraded",
            reason="unsupported_provider",
        ), []

    run_id = trace_id if provider_attempt == 0 else f"{trace_id}:fallback:{provider_attempt}"
    if provider_deadline is not None and time.monotonic() >= provider_deadline:
        return None, _mgmt_nl_provider_status(
            provider=provider,
            enabled=True,
            status="degraded",
            reason="provider_deadline_exhausted",
            run_id=run_id,
        ), []
    provider_mode = _mgmt_nl_provider_mode_from_context(context_pack)
    prompt = _mgmt_nl_provider_prompt(
        question=question,
        focus=focus,
        context_pack=context_pack,
    )
    multimodal_payload = _mgmt_nl_provider_multimodal_payload(
        prompt=prompt,
        attachments=current_user_attachments,
    )
    multimodal_summary = (
        multimodal_payload.get("summary")
        if isinstance(multimodal_payload, dict)
        else None
    )
    multimodal_supported = (
        bool(multimodal_payload and multimodal_summary and multimodal_summary.get("forwarded"))
        and _mgmt_nl_provider_supports_multimodal(provider)
    )
    multimodal_unsupported = bool(
        multimodal_payload
        and multimodal_summary
        and multimodal_summary.get("forwarded")
        and not multimodal_supported
    )
    if multimodal_unsupported:
        multimodal_summary = {
            **multimodal_summary,
            "forwarded": False,
            "reason": "multimodal_unsupported",
            "fallback": "text_only",
        }
    provider_started = time.monotonic()
    _management_ai_record_event(
        {
            "event_type": "management_ai.provider.started",
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "provider_run_id": run_id,
            "actor_id": identity.operator_id,
            "provider": provider,
            "route": _management_ai_provider_route(provider),
            "context_pack_id": context_pack.get("context_pack_id"),
            "mode": provider_mode,
            "prompt_bytes": len(prompt.encode("utf-8")),
            "multimodal": multimodal_summary,
        }
    )
    metadata = {
        "route": "POST /bff/management/nl/ask",
        "session_id": session_id,
        "message_id": message_id,
        "trace_id": trace_id,
        "provider_run_id": run_id,
        "tenant_id": caller_tenant_id,
        "audit_id": audit_id,
        "attachments": current_user_attachments or [],
        "multimodal": multimodal_summary,
        "control_mode": _mgmt_nl_provider_control_metadata(context_pack),
    }
    def _provider_failure(error: OpenClawOpsClientError) -> Tuple[None, Dict[str, Any], List[Dict[str, Any]]]:
        duration_ms = max(0, int((time.monotonic() - provider_started) * 1000))
        _management_ai_record_event(
            {
                "event_type": "management_ai.provider.failed",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "provider_run_id": run_id,
                "actor_id": identity.operator_id,
                "provider": provider,
                "mode": provider_mode,
                "duration_ms": duration_ms,
                "status_code": error.status_code,
                "error_code": error.error_code,
                "error_message": _management_ai_summary_value(error.message),
                "multimodal": multimodal_summary,
            }
        )
        status = _mgmt_nl_provider_status(
            provider=provider,
            enabled=True,
            status="degraded",
            reason=error.error_code,
            run_id=run_id,
        )
        status["mode"] = provider_mode
        if multimodal_summary:
            status["multimodal"] = multimodal_summary
        return None, status, []

    invoke_kwargs: Dict[str, Any] = {
        "provider": provider,
        "mode": provider_mode,
        "prompt": prompt,
        "context_pack": context_pack,
        "operator_id": identity.operator_id,
        "trace_id": run_id,
        "metadata": metadata,
    }
    if provider_deadline is not None:
        remaining_seconds = provider_deadline - time.monotonic()
        if remaining_seconds <= 0:
            return _provider_failure(
                OpenClawOpsClientError(
                    "Management AI provider deadline elapsed before invocation.",
                    status_code=504,
                    error_code="PROVIDER_DEADLINE_EXHAUSTED",
                )
            )
        invoke_kwargs["timeout_seconds"] = remaining_seconds
    if multimodal_supported and multimodal_payload:
        invoke_kwargs["messages"] = multimodal_payload.get("messages")
        invoke_kwargs["attachments"] = multimodal_payload.get("attachments")

    try:
        provider_payload = OpenClawOpsClient().invoke_assistant_provider(**invoke_kwargs)
    except OpenClawOpsClientError as exc:
        if not (multimodal_payload and multimodal_supported and _mgmt_nl_multimodal_unsupported_error(exc)):
            return _provider_failure(exc)
        multimodal_unsupported = True
        multimodal_summary = {
            **(multimodal_summary or {}),
            "forwarded": False,
            "reason": "multimodal_unsupported",
            "fallback": "text_only",
        }
        metadata["multimodal"] = multimodal_summary
        _management_ai_record_event(
            {
                "event_type": "management_ai.provider.multimodal_unsupported",
                "session_id": session_id,
                "message_id": message_id,
                "trace_id": trace_id,
                "provider_run_id": run_id,
                "actor_id": identity.operator_id,
                "provider": provider,
                "error_code": exc.error_code,
                "error_message": _management_ai_summary_value(exc.message),
                "multimodal": multimodal_summary,
            }
        )
        retry_kwargs: Dict[str, Any] = {
            "provider": provider,
            "mode": provider_mode,
            "prompt": prompt,
            "context_pack": context_pack,
            "operator_id": identity.operator_id,
            "trace_id": run_id,
            "metadata": metadata,
        }
        if provider_deadline is not None:
            remaining_seconds = provider_deadline - time.monotonic()
            if remaining_seconds <= 0:
                return _provider_failure(
                    OpenClawOpsClientError(
                        "Management AI provider deadline elapsed before multimodal retry.",
                        status_code=504,
                        error_code="PROVIDER_DEADLINE_EXHAUSTED",
                    )
                )
            retry_kwargs["timeout_seconds"] = remaining_seconds
        try:
            provider_payload = OpenClawOpsClient().invoke_assistant_provider(
                **retry_kwargs,
            )
        except OpenClawOpsClientError as retry_exc:
            return _provider_failure(retry_exc)

    data = provider_payload.get("data") if isinstance(provider_payload, dict) else {}
    provider_state = str((data or {}).get("status") or provider_payload.get("status") or "ok")
    if provider_state.strip().lower() not in _MGMT_NL_COMPLETED_PROVIDER_STATES:
        output = (data or {}).get("output") if isinstance(data, dict) else {}
        message = ""
        if isinstance(output, dict):
            message = str(output.get("message") or output.get("diagnostic_message") or "").strip()
        return _provider_failure(
            OpenClawOpsClientError(
                message or "Assistant provider returned a non-terminal answer state.",
                status_code=200,
                error_code=_mgmt_nl_provider_degraded_reason(provider_payload),
                payload=provider_payload,
            )
        )
    answer = _mgmt_nl_extract_provider_answer(provider_payload)
    actions = _mgmt_nl_extract_provider_actions(
        provider_payload,
        allowed_action_kinds=allowed_action_kinds,
    )
    duration_ms = max(0, int((time.monotonic() - provider_started) * 1000))
    _management_ai_record_event(
        {
            "event_type": "management_ai.provider.completed",
            "session_id": session_id,
            "message_id": message_id,
            "trace_id": trace_id,
            "provider_run_id": run_id,
            "actor_id": identity.operator_id,
            "provider": str((data or {}).get("provider") or provider),
            "mode": provider_mode,
            "duration_ms": duration_ms,
            "provider_state": provider_state,
            "answer_present": bool(answer),
            "action_count": len(actions),
            "allowed_action_kinds": sorted(allowed_action_kinds),
            "output_summary": _management_ai_provider_output_summary(provider_payload),
            "multimodal": multimodal_summary,
        }
    )
    if not answer:
        empty_status = _mgmt_nl_provider_status(
            provider=provider,
            enabled=True,
            status="degraded",
            reason="provider_empty_answer",
            run_id=run_id,
        )
        empty_status["mode"] = provider_mode
        if multimodal_summary:
            empty_status["multimodal"] = multimodal_summary
        return None, empty_status, []
    status = _mgmt_nl_provider_status(
        provider=str((data or {}).get("provider") or provider),
        enabled=True,
        status=provider_state if provider_state != "ok" else "completed",
        run_id=run_id,
        used=True,
    )
    status["mode"] = provider_mode
    output = (data or {}).get("output") if isinstance(data, dict) else None
    if isinstance(output, dict):
        if output.get("sandbox") is not None:
            status["sandbox"] = output.get("sandbox")
        if output.get("workspace_class") is not None:
            status["workspace_class"] = output.get("workspace_class")
    if multimodal_summary:
        status["multimodal"] = multimodal_summary
    if multimodal_unsupported:
        status["reason"] = "multimodal_unsupported"
    if isinstance(data, dict) and data.get("redaction") is not None:
        status["redaction"] = data.get("redaction")
    return answer, status, actions




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
        except (OpenClawOpsClientError, get_openclaw_ops_client_error()) as exc:
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

