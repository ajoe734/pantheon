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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

from fastapi import HTTPException

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
from ..models import ErrorCode, OperatorIdentity

log = logging.getLogger(__name__)

DEFAULT_MANAGEMENT_AI_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60

_MGMT_AI_CONVERSATION_STORE: Optional[ManagementAiConversationStore] = None


def get_management_ai_conversation_store() -> ManagementAiConversationStore:
    global _MGMT_AI_CONVERSATION_STORE
    try:
        import sys
        main_mod = sys.modules.get("services.control_plane.bff.main")
        if main_mod is not None:
            active_store = getattr(main_mod, "_MGMT_AI_CONVERSATION_STORE", None)
            if active_store is not None:
                return active_store
    except Exception:
        pass
    if _MGMT_AI_CONVERSATION_STORE is None:
        _MGMT_AI_CONVERSATION_STORE = ManagementAiConversationStore()
    return _MGMT_AI_CONVERSATION_STORE


def set_management_ai_conversation_store(store: Optional[ManagementAiConversationStore]) -> None:
    global _MGMT_AI_CONVERSATION_STORE
    _MGMT_AI_CONVERSATION_STORE = store
    try:
        import sys
        main_mod = sys.modules.get("services.control_plane.bff.main")
        if main_mod is not None:
            main_mod._MGMT_AI_CONVERSATION_STORE = store
    except Exception:
        pass


def reset_management_ai_conversation_store() -> None:
    global _MGMT_AI_CONVERSATION_STORE
    _MGMT_AI_CONVERSATION_STORE = None
    try:
        import sys
        main_mod = sys.modules.get("services.control_plane.bff.main")
        if main_mod is not None:
            main_mod._MGMT_AI_CONVERSATION_STORE = None
    except Exception:
        pass


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
