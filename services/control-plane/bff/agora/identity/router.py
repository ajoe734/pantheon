"""Agora identity router — agora.identity.v1 + agora.session.v1."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException


_MAIN_SYMBOL_NAMES = (
    "_agora_list_response",
    "_require_operator_role",
    "_reject_body_idempotency_key",
    "_resolve_final_idempotency_key",
    "_stable_json_hash",
    "_agora_core_idempotency_check",
    "_request_dry_run_requested",
    "_dry_run_success_response",
    "_read_surface_meta",
    "_agora_required_text",
    "_publish_event",
    "ErrorCode",
    "ObjectType",
    "CommandType",
    "TargetObject",
    "_sem_list_payload",
    "_sem_agora_inbox_payload",
    "_assistant_ask_enabled",
    "_assistant_build_context_pack",
    "_agora_ask_deterministic_fallback",
    "_agora_action_command",
)


def create_identity_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    main_module: Any = None,
) -> APIRouter:
    """Identity router — placeholder until AG-BE-ID-* migrate routes from main.py.

    ``main_module`` should be the caller's own ``sys.modules[__name__]`` (see
    main.py's ``_create_agora_router(...)`` call). Resolving these symbols off
    an explicitly passed module, instead of a bare ``import main``, avoids a
    real collision: this repo has one ``main.py`` per service, and a bare
    ``import main`` returns whichever one last claimed that name in
    ``sys.modules`` — harmless across separate production containers, but a
    silent wrong-module bug the moment more than one service's main.py is
    loaded in the same interpreter (as multiple bff-ha tests under
    tests/bff/ do). Falls back to the old ambient import when no module is
    passed, for any caller that hasn't been updated yet.
    """
    router = APIRouter(tags=["agora-identity"])

    if main_module is None:
        # Local import to avoid circular dependency
        import main as main_module
    # The route handlers below reference `main.<attr>` directly (main.read_store,
    # main._sse_buffers, main.command_store, ...) throughout, in addition to
    # the names unpacked below — keep that working unchanged.
    main = main_module
    (
        _agora_list_response,
        _require_operator_role,
        _reject_body_idempotency_key,
        _resolve_final_idempotency_key,
        _stable_json_hash,
        _agora_core_idempotency_check,
        _request_dry_run_requested,
        _dry_run_success_response,
        _read_surface_meta,
        _agora_required_text,
        _publish_event,
        ErrorCode,
        ObjectType,
        CommandType,
        TargetObject,
        _sem_list_payload,
        _sem_agora_inbox_payload,
        _assistant_ask_enabled,
        _assistant_build_context_pack,
        _agora_ask_deterministic_fallback,
        _agora_action_command,
    ) = (getattr(main_module, name) for name in _MAIN_SYMBOL_NAMES)
    import uuid
    import json
    from fastapi import Body, Header, Query
    from fastapi.responses import JSONResponse

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
        return _agora_list_response(
            dataset="agora_sessions",
            surface_key="agora_session_list",
            items=main.read_store.list_agora_sessions(status=status),
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
    ):
        """BFF: create an Agora ask/session record."""
        identity = extract_identity(authorization)
        _require_operator_role(identity)
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = _stable_json_hash({"route": "POST /bff/agora/sessions", "payload": payload})
        dry_run = _request_dry_run_requested()
        if not dry_run:
            cached = _agora_core_idempotency_check(resolved_key, request_hash)
            if cached is not None:
                return cached
        snapshot_at = utc_now()
        session_id = str(payload.get("sessionId") or payload.get("session_id") or f"agora-sess-{uuid.uuid4().hex[:10]}")
        title = str(payload.get("title") or "Untitled Agora session").strip()
        if dry_run:
            return _dry_run_success_response(
                {
                    "id": session_id,
                    "sessionId": session_id,
                    "title": title,
                    "mode": payload.get("mode") or payload.get("sessionType") or "quick_ask",
                    "status": payload.get("status") or "active",
                    "participants": json.loads(json.dumps(payload.get("participants") or [])),
                    "messages": json.loads(json.dumps(payload.get("messages") or [])),
                    "createdBy": identity.operator_id,
                    "createdAt": snapshot_at,
                    "updatedAt": snapshot_at,
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.session.create",
            )
        result = {
            "data": main.read_store.create_agora_session(
                session_id=session_id,
                title=title,
                actor_id=identity.operator_id,
                payload=payload,
                created_at=snapshot_at,
            ),
            "meta": {"snapshot_at": snapshot_at},
        }
        main._AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
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
        session = main.read_store.get_agora_session(sessionId)
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
            "meta": _read_surface_meta("agora_sessions", "agora_session_detail", snapshot_at=snapshot_at),
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
        messages = main.read_store.list_agora_session_messages(sessionId)
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
            "meta": _read_surface_meta("agora_sessions", "agora_session_messages", snapshot_at=snapshot_at),
        }

    @router.post("/bff/agora/sessions/{sessionId}/messages", status_code=201)
    async def bff_create_agora_session_message(
        sessionId: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: append a message to an Agora session."""
        identity = extract_identity(authorization)
        _require_operator_role(identity)
        _reject_body_idempotency_key(payload)
        content = _agora_required_text(payload, "content", "body", "message")
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = _stable_json_hash({
            "route": "POST /bff/agora/sessions/{sessionId}/messages",
            "sessionId": sessionId,
            "payload": payload,
        })
        dry_run = _request_dry_run_requested()
        if not dry_run:
            cached = _agora_core_idempotency_check(resolved_key, request_hash)
            if cached is not None:
                return cached
        snapshot_at = utc_now()
        message_id = str(payload.get("id") or payload.get("messageId") or f"agora-msg-{uuid.uuid4().hex[:10]}")
        if dry_run:
            if not main.read_store.get_agora_session(sessionId):
                raise bff_error(
                    404,
                    ErrorCode.RESOURCE_NOT_FOUND,
                    "Agora session not found",
                    f"Agora session {sessionId} does not exist",
                    precondition_failed="session_id",
                )
            return _dry_run_success_response(
                {
                    "id": message_id,
                    "sessionId": sessionId,
                    "sender": payload.get("sender") or {"type": "operator", "id": identity.operator_id},
                    "role": payload.get("role") or "user",
                    "content": content,
                    "language": payload.get("language") or "zh-TW",
                    "createdAt": snapshot_at,
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.session_message.create",
            )
        message = main.read_store.append_agora_session_message(
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
        _publish_event(
            main._sse_buffers["ask"],
            main._sse_subscribers["ask"],
            "agora.session.message_created",
            {"sessionId": sessionId, "messageId": message.get("id")},
        )
        result = {"data": message, "meta": {"snapshot_at": snapshot_at}}
        main._AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
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
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        if not main.read_store.get_agora_message(messageId):
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Agora message not found",
                f"Agora message {messageId} does not exist",
                precondition_failed="message_id",
            )
        return _agora_action_command(
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
        require_read_role(extract_identity(authorization))
        return _sem_agora_inbox_payload()

    @router.get("/bff/agora/ask/sessions")
    async def sem_agora_ask_sessions(authorization: Optional[str] = Header(default=None)):
        require_read_role(extract_identity(authorization))
        return _sem_list_payload("agora_sessions", "agora_ask_sessions", filter_mode="quick_ask")

    @router.post("/bff/agora/ask/sessions", status_code=201)
    async def sem_agora_ask_create_session(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """ASK-001: create an agora ask session explicitly."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = _stable_json_hash({"route": "POST /bff/agora/ask/sessions", "payload": payload})
        cached = _agora_core_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        now = utc_now()
        session_id = str(payload.get("sessionId") or payload.get("session_id") or f"ask-{uuid.uuid4().hex[:10]}")
        title = str(payload.get("title") or "Agora ask session").strip()
        session = main.read_store.create_agora_session(
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
        _publish_event(
            main._sse_buffers["ask"],
            main._sse_subscribers["ask"],
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
        main._AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
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
        session = main.read_store.get_agora_session(sessionId)
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
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = _stable_json_hash({
            "route": f"POST /bff/agora/ask/sessions/{sessionId}/close",
            "sessionId": sessionId,
            "payload": payload,
        })
        cached = _agora_core_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        now = utc_now()
        outcome = str(payload.get("outcome") or "").strip() or None
        existing = main.read_store.get_agora_session(sessionId)
        if existing is None or str(existing.get("mode") or "").strip() != "quick_ask":
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Ask session not found",
                f"Ask session {sessionId} does not exist",
                precondition_failed="session_id",
            )
        session = main.read_store.close_agora_session(sessionId, closed_at=now, outcome=outcome)
        if session is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Ask session not found",
                f"Ask session {sessionId} does not exist",
                precondition_failed="session_id",
            )
        _publish_event(
            main._sse_buffers["ask"],
            main._sse_subscribers["ask"],
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
        main._AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
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
        items = main.read_store.list_agora_handoffs(status=status, handoff_type=handoff_type)
        return _agora_list_response(
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
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = _stable_json_hash({"route": "POST /bff/agora/ask", "payload": payload})
        cached = _agora_core_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return JSONResponse(status_code=202, content=cached)
        now = utc_now()
        session_id = str(payload.get("sessionId") or payload.get("session_id") or f"ask-{uuid.uuid4().hex[:10]}")
        message_id = str(payload.get("messageId") or payload.get("message_id") or f"msg-{uuid.uuid4().hex[:10]}")
        prompt = str(payload.get("prompt") or payload.get("message") or payload.get("content") or "").strip()
        session = main.read_store.get_agora_session(session_id)
        if session is None:
            session = main.read_store.create_agora_session(
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
        messages = main.read_store.list_agora_session_messages(session_id) or []
        message = next(
            (item for item in messages if isinstance(item, dict) and str(item.get("id") or "") == message_id),
            None,
        )
        if message is None:
            message = main.read_store.append_agora_session_message(
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
        session = main.read_store.get_agora_session(session_id) or session
        command_id = f"cmd-{uuid.uuid4().hex[:16]}"
        main.command_store.submit_command(
            command_id,
            CommandType.AGORA_MESSAGE_ACTION,
            TargetObject(type=ObjectType.AGORA_MESSAGE, id=message_id),
            now,
            dict(payload),
            {"actor": identity.operator_id, "live_capital_side_effects": False},
            {"idempotency_record": {"idempotency_key": resolved_key, "request_hash": request_hash, "status": "succeeded"}},
        )

        # --- Assistant provider integration (ASST-BFF-001) ---
        provider_status = "disabled"
        provider_answer: Optional[str] = None
        provider_run_id: Optional[str] = None
        context_pack_id: Optional[str] = None

        if main._assistant_ask_enabled():
            # Build context pack for provider invocation and transcript source refs.
            _context_pack_dict: Dict[str, Any] = {}
            try:
                from assistant.models import AssistantContextPackRequest as _CPRequest, AssistantMode as _AMode
                _cp_req = _CPRequest(
                    mode=_AMode.USER,
                    question=prompt or None,
                    route=str(payload.get("route") or "/"),
                )
                _context_pack = main._assistant_build_context_pack(session_id, _cp_req, identity)
                context_pack_id = _context_pack.context_pack_id
                _context_pack_dict = _context_pack.model_dump(mode="json", by_alias=False)
            except Exception:  # noqa: BLE001
                pass

            # Ensure an assistant session lifecycle entry exists for this agora session.
            if main._ASSISTANT_SESSION_STORE is not None:
                from assistant.transcript_store import (
                    AssistantSession as _ASession,
                    SessionNotFoundError as _SNFError,
                    build_session as _build_session,
                )
                from assistant.models import AssistantMode as _AMode2
                try:
                    main._ASSISTANT_SESSION_STORE.get(session_id)
                except _SNFError:
                    _proto = _build_session(
                        mode=_AMode2.USER,
                        actor_id=identity.operator_id,
                        roles=getattr(identity, "roles", []) or [],
                        capabilities=[],
                    )
                    # Co-key assistant session with agora session_id for transcript correlation.
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
                    main._ASSISTANT_SESSION_STORE.create(_asst_session)

            try:
                _ops_client = main.OpenClawOpsClient()
                if _ops_client.configured:
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
            except (main.OpenClawOpsClientError, Exception):  # noqa: BLE001
                provider_status = "degraded"

            # Emit ask.message.delta — includes provider answer or empty string when degraded
            _publish_event(
                main._sse_buffers["ask"],
                main._sse_subscribers["ask"],
                "ask.message.delta",
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "delta": provider_answer or "",
                    "provider_status": provider_status,
                },
            )

            # Record turns in assistant transcript store with context_pack_id for source readback.
            if main._ASSISTANT_TRANSCRIPT_STORE is not None:
                from assistant.transcript_store import TurnRole, build_turn
                main._ASSISTANT_TRANSCRIPT_STORE.append(
                    build_turn(
                        session_id=session_id,
                        role=TurnRole.USER,
                        content=prompt or "",
                        context_pack_id=context_pack_id,
                    )
                )
                if provider_answer:
                    main._ASSISTANT_TRANSCRIPT_STORE.append(
                        build_turn(
                            session_id=session_id,
                            role=TurnRole.ASSISTANT,
                            content=provider_answer,
                            context_pack_id=context_pack_id,
                            provider_run_id=provider_run_id,
                        )
                    )

            # Update session context with context_pack_id and provider_run_id.
            if main._ASSISTANT_SESSION_STORE is not None and (context_pack_id or provider_run_id):
                try:
                    main._ASSISTANT_SESSION_STORE.update_context(
                        session_id,
                        context_pack_id=context_pack_id,
                        provider_run_id=provider_run_id,
                    )
                except Exception:  # noqa: BLE001
                    pass

            # Emit ask.message.completed
            _publish_event(
                main._sse_buffers["ask"],
                main._sse_subscribers["ask"],
                "ask.message.completed",
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "status": provider_status,
                },
            )

            # Deterministic fallback when provider is degraded
            if provider_answer is None:
                provider_answer = _agora_ask_deterministic_fallback(prompt)

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
                "assistant": {"enabled": main._assistant_ask_enabled(), "provider_status": provider_status},
            },
        }
        main._AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
        return JSONResponse(status_code=202, content=result)

    return router
