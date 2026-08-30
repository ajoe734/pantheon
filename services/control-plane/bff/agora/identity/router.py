"""Agora identity router — agora.identity.v1 + agora.session.v1."""
from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from models import CommandType, ErrorCode, ObjectType, TargetObject
from ..service import AgoraService


def create_identity_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    service: Optional[AgoraService] = None,
    require_write_role: Optional[Callable[..., None]] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    get_command_store: Optional[Callable[[], Any]] = None,
    idempotency_store: Optional[Dict[str, Any]] = None,
    sse_buffers: Optional[Dict[str, Any]] = None,
    sse_subscribers: Optional[Dict[str, Any]] = None,
    assistant_ask_enabled: Optional[Callable[[], bool]] = None,
    assistant_build_context_pack: Optional[Callable[..., Any]] = None,
    get_assistant_session_store: Optional[Callable[[], Any]] = None,
    get_assistant_transcript_store: Optional[Callable[[], Any]] = None,
    openclaw_ops_client_factory: Optional[Callable[[], Any]] = None,
) -> APIRouter:
    """Identity router — agora sessions, quick ask, messages, and handoffs."""
    router = APIRouter(tags=["agora-identity"])

    svc = service or AgoraService(
        get_read_store=get_read_store,
        get_command_store=get_command_store,
        idempotency_store=idempotency_store,
        sse_buffers=sse_buffers,
        sse_subscribers=sse_subscribers,
        assistant_ask_enabled=assistant_ask_enabled,
        assistant_build_context_pack=assistant_build_context_pack,
        get_assistant_session_store=get_assistant_session_store,
        get_assistant_transcript_store=get_assistant_transcript_store,
        openclaw_ops_client_factory=openclaw_ops_client_factory,
        utc_now=utc_now,
        bff_error=bff_error,
    )

    def _require_operator(identity: Any) -> None:
        if require_write_role is not None:
            require_write_role(identity)
            return
        roles = set(getattr(identity, "roles", []) or [])
        if not roles.intersection({"operator", "approver", "admin", "reviewer"}):
            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Operator role required",
                "Action requires operator-level access",
                precondition_failed="role_check",
            )

    @router.get("/bff/agora/committee-sessions")
    @router.get("/bff/agora/sessions")
    async def bff_agora_sessions(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: Agora ask/session list."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        snapshot_at = utc_now()
        return svc.agora_list_response(
            dataset="agora_sessions",
            surface_key="agora_session_list",
            items=svc.list_sessions(status=status),
            page_token=page_token,
            page_size=page_size,
            snapshot_at=snapshot_at,
        )

    @router.post("/bff/agora/sessions", status_code=201)
    async def bff_create_agora_session(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ):
        """BFF: create an Agora ask/session record."""
        identity = extract_identity(authorization)
        _require_operator(identity)
        svc.reject_body_idempotency_key(payload)
        resolved_key = svc.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = svc.stable_json_hash({"route": "POST /bff/agora/sessions", "payload": payload})
        dry_run = bool(x_dry_run and x_dry_run.strip().lower() in ("true", "1", "yes"))
        if not dry_run:
            cached = svc.check_idempotency(resolved_key, request_hash)
            if cached is not None:
                return cached
        snapshot_at = utc_now()
        session_id = str(payload.get("sessionId") or payload.get("session_id") or f"agora-sess-{uuid.uuid4().hex[:10]}")
        title = str(payload.get("title") or "Untitled Agora session").strip()
        if dry_run:
            return svc.dry_run_success_response(
                {
                    "id": session_id,
                    "sessionId": session_id,
                    "title": title,
                    "actorId": identity.operator_id,
                    "createdAt": snapshot_at,
                    "updatedAt": snapshot_at,
                    **payload,
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.session.create",
            )
        created = svc.create_session(
            session_id=session_id,
            title=title,
            actor_id=identity.operator_id,
            payload=payload,
            created_at=snapshot_at,
        )
        result = {
            "data": created,
            "meta": {"snapshot_at": snapshot_at},
        }
        svc.record_idempotency(resolved_key, request_hash, result)
        return result

    @router.get("/bff/agora/sessions/{sessionId}")
    async def bff_agora_session_detail(
        sessionId: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: Agora ask/session detail."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        snapshot_at = utc_now()
        session = svc.get_session(sessionId)
        if not session:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Agora session not found",
                f"Agora session {sessionId} does not exist",
                precondition_failed="session_id",
            )
        return {
            "data": session,
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {"agora_session_detail": {"status": "ok", "source": "bff_local"}},
            },
        }

    @router.get("/bff/agora/sessions/{sessionId}/messages")
    async def bff_agora_session_messages(
        sessionId: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: messages for an Agora session."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        snapshot_at = utc_now()
        messages = svc.list_session_messages(sessionId)
        if messages is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Agora session not found",
                f"Agora session {sessionId} does not exist",
                precondition_failed="session_id",
            )
        return {
            "data": messages,
            "items": messages,
            "page_info": {"next_page_token": None, "total": len(messages)},
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {"agora_session_messages": {"status": "ok", "source": "bff_local"}},
            },
        }

    @router.post("/bff/agora/sessions/{sessionId}/messages", status_code=201)
    async def bff_create_agora_session_message(
        sessionId: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ):
        """BFF: append a message to an Agora session."""
        identity = extract_identity(authorization)
        _require_operator(identity)
        svc.reject_body_idempotency_key(payload)
        content = svc.agora_required_text(payload, "content", "body", "message")
        resolved_key = svc.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = svc.stable_json_hash({
            "route": "POST /bff/agora/sessions/{sessionId}/messages",
            "sessionId": sessionId,
            "payload": payload,
        })
        dry_run = bool(x_dry_run and x_dry_run.strip().lower() in ("true", "1", "yes"))
        if not dry_run:
            cached = svc.check_idempotency(resolved_key, request_hash)
            if cached is not None:
                return cached
        session = svc.get_session(sessionId)
        if session is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Agora session not found",
                f"Agora session {sessionId} does not exist",
                precondition_failed="session_id",
            )
        snapshot_at = utc_now()
        message_id = str(payload.get("id") or payload.get("messageId") or f"agora-msg-{uuid.uuid4().hex[:10]}")
        if dry_run:
            return svc.dry_run_success_response(
                {
                    "id": message_id,
                    "messageId": message_id,
                    "sessionId": sessionId,
                    "content": content,
                    "actorId": identity.operator_id,
                    "createdAt": snapshot_at,
                    **payload,
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.session.message_create",
            )
        message = svc.append_session_message(
            sessionId,
            message_id=message_id,
            content=content,
            actor_id=identity.operator_id,
            payload=payload,
            created_at=snapshot_at,
        )
        if message is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Agora session not found",
                f"Agora session {sessionId} does not exist",
                precondition_failed="session_id",
            )
        svc.publish_sse_event(
            "ask",
            "agora.session.message_created",
            {"sessionId": sessionId, "messageId": message.get("id")},
        )
        result = {"data": message, "meta": {"snapshot_at": snapshot_at}}
        svc.record_idempotency(resolved_key, request_hash, result)
        return result

    @router.post("/bff/agora/messages/{messageId}/actions/{actionId}", status_code=202)
    async def bff_agora_message_action(
        messageId: str,
        actionId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: route an Agora message action through command admission."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        svc.reject_body_idempotency_key(payload)
        resolved_key = svc.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        store = svc.read_store
        msg_getter = getattr(store, "get_agora_message", None) if store is not None else None
        if callable(msg_getter) and not msg_getter(messageId):
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Agora message not found",
                f"Agora message {messageId} does not exist",
                precondition_failed="message_id",
            )
        return svc.submit_action_command(
            route="POST /bff/agora/messages/{messageId}/actions/{actionId}",
            entity_type=ObjectType.AGORA_MESSAGE,
            entity_id=messageId,
            action_id=actionId,
            resolved_key=resolved_key,
            identity=identity,
            payload=payload,
            command_type=CommandType.AGORA_MESSAGE_ACTION,
        )

    @router.get("/bff/agora/inbox")
    async def sem_agora_inbox(authorization: Optional[str] = Header(default=None)):
        identity = extract_identity(authorization)
        require_read_role(identity)
        return svc.sem_agora_inbox_payload()

    @router.get("/bff/agora/ask/sessions")
    async def sem_agora_ask_sessions(authorization: Optional[str] = Header(default=None)):
        identity = extract_identity(authorization)
        require_read_role(identity)
        return svc.sem_list_payload("agora_sessions", "agora_ask_sessions", filter_mode="quick_ask")

    @router.post("/bff/agora/ask/sessions", status_code=201)
    async def sem_agora_ask_create_session(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ):
        """ASK-001: create an agora ask session explicitly."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        svc.reject_body_idempotency_key(payload)
        resolved_key = svc.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = svc.stable_json_hash({"route": "POST /bff/agora/ask/sessions", "payload": payload})
        dry_run = bool(x_dry_run and x_dry_run.strip().lower() in ("true", "1", "yes"))
        if not dry_run:
            cached = svc.check_idempotency(resolved_key, request_hash)
            if cached is not None:
                return cached
        now = utc_now()
        session_id = str(payload.get("sessionId") or payload.get("session_id") or f"ask-{uuid.uuid4().hex[:10]}")
        title = str(payload.get("title") or "Agora ask session").strip()
        if dry_run:
            return svc.dry_run_success_response(
                {
                    "id": session_id,
                    "sessionId": session_id,
                    "title": title,
                    "actorId": identity.operator_id,
                    "createdAt": now,
                    "updatedAt": now,
                    "mode": "quick_ask",
                    "participants": payload.get("participants") or [{"type": "operator", "id": identity.operator_id}],
                    **dict(payload),
                },
                snapshot_at=now,
                idempotency_key=resolved_key,
                evidence_kind="agora.ask_session.create",
            )
        session = svc.create_session(
            session_id=session_id,
            title=title,
            actor_id=identity.operator_id,
            payload={
                **dict(payload),
                "mode": "quick_ask",
                "participants": payload.get("participants") or [{"type": "operator", "id": identity.operator_id}],
            },
            created_at=now,
        )
        svc.publish_sse_event(
            "ask",
            "ask.session.started",
            {"session_id": session_id, "mode": "quick_ask"},
        )
        result = {
            "data": session,
            "meta": {
                "snapshot_at": now,
                "surfaces": {"agora_ask_session_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        svc.record_idempotency(resolved_key, request_hash, result)
        return result

    @router.get("/bff/agora/ask/sessions/{sessionId}")
    async def sem_agora_ask_session_detail(
        sessionId: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """ASK-001: ask session detail — also serves as the SSE resync route for the ask channel."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        snapshot_at = utc_now()
        session = svc.get_session(sessionId)
        if session is None or str(session.get("mode") or "").strip() != "quick_ask":
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Ask session not found",
                f"Ask session {sessionId} does not exist",
                precondition_failed="session_id",
            )
        return {
            "data": session,
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {"agora_ask_session_detail": {"status": "ok", "source": "bff_local"}},
            },
        }

    @router.post("/bff/agora/ask/sessions/{sessionId}/close", status_code=200)
    async def sem_agora_ask_close_session(
        sessionId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """ASK-001: close an agora ask session."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        svc.reject_body_idempotency_key(payload)
        resolved_key = svc.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = svc.stable_json_hash({
            "route": f"POST /bff/agora/ask/sessions/{sessionId}/close",
            "sessionId": sessionId,
            "payload": payload,
        })
        cached = svc.check_idempotency(resolved_key, request_hash)
        if cached is not None:
            return cached
        now = utc_now()
        outcome = str(payload.get("outcome") or "").strip() or None
        existing = svc.get_session(sessionId)
        if existing is None or str(existing.get("mode") or "").strip() != "quick_ask":
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Ask session not found",
                f"Ask session {sessionId} does not exist",
                precondition_failed="session_id",
            )
        session = svc.close_session(sessionId, closed_at=now, outcome=outcome)
        if session is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Ask session not found",
                f"Ask session {sessionId} does not exist",
                precondition_failed="session_id",
            )
        svc.publish_sse_event(
            "ask",
            "ask.session.completed",
            {"sessionId": sessionId, "outcome": outcome},
        )
        result = {
            "data": session,
            "meta": {
                "snapshot_at": now,
                "surfaces": {"agora_ask_session_detail": {"status": "ok", "source": "bff_local"}},
            },
        }
        svc.record_idempotency(resolved_key, request_hash, result)
        return result

    @router.get("/bff/agora/incoming")
    @router.get("/bff/agora/handoffs")
    async def bff_agora_handoffs(
        status: Optional[str] = None,
        handoff_type: Optional[str] = Query(default=None, alias="handoffType"),
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: Agora handoff queue records created by workbench actions."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        snapshot_at = utc_now()
        items = svc.list_handoffs(status=status, handoff_type=handoff_type)
        return svc.agora_list_response(
            dataset="agora_handoffs",
            surface_key="agora_handoff_list",
            items=items,
            page_token=page_token,
            page_size=page_size,
            snapshot_at=snapshot_at,
        )

    @router.post("/bff/agora/ask", status_code=202)
    async def sem_agora_ask(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)
        svc.reject_body_idempotency_key(payload)
        resolved_key = svc.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = svc.stable_json_hash({"route": "POST /bff/agora/ask", "payload": payload})
        cached = svc.check_idempotency(resolved_key, request_hash)
        if cached is not None:
            return JSONResponse(status_code=202, content=cached)
        now = utc_now()
        session_id = str(payload.get("sessionId") or payload.get("session_id") or f"ask-{uuid.uuid4().hex[:10]}")
        message_id = str(payload.get("messageId") or payload.get("message_id") or f"msg-{uuid.uuid4().hex[:10]}")
        prompt = str(payload.get("prompt") or payload.get("message") or payload.get("content") or "").strip()

        session = svc.get_session(session_id)
        if session is None:
            session = svc.create_session(
                session_id=session_id,
                title=prompt[:80] or "Agora ask",
                actor_id=identity.operator_id,
                payload={
                    **dict(payload),
                    "mode": "quick_ask",
                    "participants": [{"type": "operator", "id": identity.operator_id}],
                    "messages": [],
                },
                created_at=now,
            )
        messages = svc.list_session_messages(session_id) or []
        message = next(
            (item for item in messages if isinstance(item, dict) and str(item.get("id") or "") == message_id),
            None,
        )
        if message is None:
            message = svc.append_session_message(
                session_id,
                message_id=message_id,
                content=prompt,
                actor_id=identity.operator_id,
                payload={
                    **dict(payload),
                    "sender": {"type": "operator", "id": identity.operator_id},
                    "role": "user",
                },
                created_at=now,
            )
        session = svc.get_session(session_id) or session
        command_id = f"cmd-{uuid.uuid4().hex[:16]}"
        cmd_store = svc.command_store
        if cmd_store is not None and hasattr(cmd_store, "submit_command"):
            cmd_store.submit_command(
                command_id,
                CommandType.AGORA_MESSAGE_ACTION,
                TargetObject(type=ObjectType.AGORA_MESSAGE, id=message_id),
                now,
                dict(payload),
                {"actor": identity.operator_id, "live_capital_side_effects": False},
                {"idempotency_record": {"idempotency_key": resolved_key, "request_hash": request_hash, "status": "succeeded"}},
            )

        provider_status = "disabled"
        provider_answer: Optional[str] = None
        provider_run_id: Optional[str] = None
        context_pack_id: Optional[str] = None

        if svc._assistant_ask_enabled():
            _context_pack_dict: Dict[str, Any] = {}
            if svc._assistant_build_context_pack is not None:
                try:
                    from assistant.models import AssistantContextPackRequest as _CPRequest, AssistantMode as _AMode
                    _cp_req = _CPRequest(
                        mode=_AMode.USER,
                        question=prompt or None,
                        route=str(payload.get("route") or "/"),
                    )
                    _context_pack = svc._assistant_build_context_pack(session_id, _cp_req, identity)
                    context_pack_id = getattr(_context_pack, "context_pack_id", None)
                    if hasattr(_context_pack, "model_dump"):
                        _context_pack_dict = _context_pack.model_dump(mode="json", by_alias=False)
                except Exception:  # noqa: BLE001
                    pass

            asst_store = svc._get_assistant_session_store()
            if asst_store is not None:
                from assistant.transcript_store import (
                    AssistantSession as _ASession,
                    SessionNotFoundError as _SNFError,
                    build_session as _build_session,
                )
                from assistant.models import AssistantMode as _AMode2
                try:
                    asst_store.get(session_id)
                except _SNFError:
                    _proto = _build_session(
                        mode=_AMode2.USER,
                        actor_id=identity.operator_id,
                        roles=getattr(identity, "roles", []) or [],
                        capabilities=[],
                    )
                    _asst_session = _ASession(
                        session_id=session_id,
                        mode=_proto.mode,
                        actor_id=_proto.actor_id,
                        roles=_proto.roles,
                        capabilities=_proto.capabilities,
                        created_at=_proto.created_at,
                        expires_at=_proto.expires_at,
                        status=_proto.status,
                        reason=_proto.reason,
                        ttl_seconds=_proto.ttl_seconds,
                    )
                    asst_store.create(_asst_session)

            if svc._openclaw_ops_client_factory is not None:
                try:
                    _ops_client = svc._openclaw_ops_client_factory()
                    if getattr(_ops_client, "configured", False):
                        raw = _ops_client.invoke_assistant(
                            mode=str(payload.get("mode") or "user"),
                            prompt=prompt or "?",
                            operator_id=identity.operator_id,
                            context_pack=_context_pack_dict or None,
                        )
                        _data = raw.get("data") or {}
                        _out = _data.get("output")
                        if isinstance(_out, str) and _out.strip():
                            provider_answer = _out.strip()
                            provider_status = "completed"
                            provider_run_id = f"provider-run-{message_id}"
                        else:
                            provider_status = "degraded"
                    else:
                        provider_status = "degraded"
                except Exception:  # noqa: BLE001
                    provider_status = "degraded"

            svc.publish_sse_event(
                "ask",
                "ask.message.delta",
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "delta": provider_answer or "",
                    "provider_status": provider_status,
                },
            )

            asst_tx_store = svc._get_assistant_transcript_store()
            if asst_tx_store is not None:
                from assistant.transcript_store import TurnRole, build_turn
                asst_tx_store.append(
                    build_turn(
                        session_id=session_id,
                        role=TurnRole.USER,
                        content=prompt or "",
                        context_pack_id=context_pack_id,
                    )
                )
                if provider_answer:
                    asst_tx_store.append(
                        build_turn(
                            session_id=session_id,
                            role=TurnRole.ASSISTANT,
                            content=provider_answer,
                            context_pack_id=context_pack_id,
                            provider_run_id=provider_run_id,
                        )
                    )

            if asst_store is not None and (context_pack_id or provider_run_id):
                try:
                    asst_store.update_context(
                        session_id,
                        context_pack_id=context_pack_id,
                        provider_run_id=provider_run_id,
                    )
                except Exception:  # noqa: BLE001
                    pass

            svc.publish_sse_event(
                "ask",
                "ask.message.completed",
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "status": provider_status,
                },
            )

            if provider_answer is None:
                provider_answer = svc.deterministic_ask_fallback(prompt)

        result = {
            "status": "accepted",
            "data": {
                "session": session,
                "message": message,
                "provider": {
                    "status": provider_status,
                    "answer": provider_answer,
                    "run_id": provider_run_id,
                },
            },
            "meta": {
                "snapshot_at": now,
                "command": {"command": CommandType.AGORA_MESSAGE_ACTION.value, "commandId": command_id},
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "assistant": {"enabled": svc._assistant_ask_enabled(), "provider_status": provider_status},
            },
        }
        svc.record_idempotency(resolved_key, request_hash, result)
        return JSONResponse(status_code=202, content=result)

    return router
