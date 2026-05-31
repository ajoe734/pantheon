from __future__ import annotations

from typing import Any, Callable, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException

from models import ErrorCode

from .context_composer import AssistantContextPolicyError
from .mode_policy import ModePolicyViolation, check_session_active, create_session, session_to_dict, turn_to_dict
from .models import AssistantContextPack, AssistantContextPackRequest, AssistantContextPackResponse, AssistantMode
from .transcript_store import (
    InMemorySessionStore,
    InMemoryTranscriptStore,
    SessionNotFoundError,
    SessionRejectedError,
    TurnRole,
    build_turn,
)


BuildContextPack = Callable[[str, AssistantContextPackRequest, Any], AssistantContextPack]
ExtractIdentity = Callable[[Optional[str]], Any]
RequireReadRole = Callable[[Any], None]
BffErrorFactory = Callable[..., HTTPException]


def create_assistant_router(
    *,
    build_context_pack: BuildContextPack,
    extract_identity: ExtractIdentity,
    require_read_role: RequireReadRole,
    bff_error: Optional[BffErrorFactory] = None,
    session_store: Optional[Any] = None,
    transcript_store: Optional[Any] = None,
) -> APIRouter:
    router = APIRouter(prefix="/bff/assistant", tags=["assistant"])

    _session_store = session_store if session_store is not None else InMemorySessionStore()
    _transcript_store = transcript_store if transcript_store is not None else InMemoryTranscriptStore()

    @router.post("/sessions/{session_id}/context", status_code=201)
    async def build_session_context_pack(
        session_id: str,
        payload: AssistantContextPackRequest = Body(default_factory=AssistantContextPackRequest),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        try:
            pack = build_context_pack(session_id, payload, identity)
        except AssistantContextPolicyError as exc:
            if bff_error is not None:
                raise bff_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Assistant context source is not allowed for this mode",
                    str(exc),
                    precondition_failed="assistant_context_mode_policy",
                    details_extra={"denied_sources": exc.denied_sources},
                )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": ErrorCode.FORBIDDEN.value,
                        "message": "Assistant context source is not allowed for this mode",
                        "details": {
                            "reason": str(exc),
                            "denied_sources": exc.denied_sources,
                        },
                    }
                },
            ) from exc

        response = AssistantContextPackResponse(
            data=pack,
            meta={
                "snapshot_at": pack.snapshot_at,
                "requested_sources": payload.include,
                "included_sources": [source.source_id for source in pack.sources],
                "omitted_sources": [
                    source.model_dump(mode="json", by_alias=False) for source in pack.omitted_sources
                ],
            },
        )
        return response.model_dump(mode="json", by_alias=False)

    # ------------------------------------------------------------------
    # Session management routes
    # ------------------------------------------------------------------

    @router.post("/sessions", status_code=201)
    async def create_assistant_session(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        mode_raw = payload.get("mode", AssistantMode.USER.value)
        try:
            mode = AssistantMode(mode_raw)
        except ValueError:
            _raise_error(bff_error, 400, ErrorCode.VALIDATION_FAILED, f"Invalid mode: {mode_raw!r}")

        reason: Optional[str] = payload.get("reason")
        ttl_seconds: Optional[int] = payload.get("ttl_seconds")

        try:
            session = create_session(
                mode=mode,
                actor=identity,
                reason=reason,
                ttl_seconds=ttl_seconds,
            )
        except ModePolicyViolation as exc:
            _raise_error(
                bff_error, 422, ErrorCode.BUSINESS_RULE_VIOLATION,
                f"Mode policy violation: {exc}",
                str(exc),
                field=exc.field,
            )

        _session_store.create(session)
        return {"data": session_to_dict(session)}

    @router.get("/sessions/{session_id}")
    async def get_assistant_session(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            session = _session_store.get(session_id)
        except SessionNotFoundError:
            _raise_error(bff_error, 404, ErrorCode.RESOURCE_NOT_FOUND, f"Session not found: {session_id!r}")

        return {"data": session_to_dict(session)}

    @router.post("/sessions/{session_id}/revoke", status_code=200)
    async def revoke_assistant_session(
        session_id: str,
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            session = _session_store.revoke(session_id, reason=payload.get("reason"))
        except SessionNotFoundError:
            _raise_error(bff_error, 404, ErrorCode.RESOURCE_NOT_FOUND, f"Session not found: {session_id!r}")

        return {"data": session_to_dict(session)}

    @router.get("/sessions/{session_id}/transcript")
    async def get_session_transcript(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            _session_store.get(session_id)
        except SessionNotFoundError:
            _raise_error(bff_error, 404, ErrorCode.RESOURCE_NOT_FOUND, f"Session not found: {session_id!r}")

        turns = _transcript_store.list_turns(session_id)
        return {"data": [turn_to_dict(t) for t in turns], "meta": {"count": len(turns)}}

    @router.post("/sessions/{session_id}/transcript", status_code=201)
    async def append_transcript_turn(
        session_id: str,
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            session = _session_store.get(session_id)
        except SessionNotFoundError:
            _raise_error(bff_error, 404, ErrorCode.RESOURCE_NOT_FOUND, f"Session not found: {session_id!r}")

        try:
            check_session_active(session)
        except SessionRejectedError as exc:
            _raise_error(bff_error, 409, ErrorCode.RESOURCE_CONFLICT, str(exc))

        role_raw = payload.get("role", TurnRole.USER.value)
        try:
            role = TurnRole(role_raw)
        except ValueError:
            _raise_error(bff_error, 400, ErrorCode.VALIDATION_FAILED, f"Invalid role: {role_raw!r}")

        content: str = str(payload.get("content", ""))
        context_pack_id: Optional[str] = payload.get("context_pack_id")
        provider_run_id: Optional[str] = payload.get("provider_run_id")
        source_refs: List[Any] = payload.get("source_refs") or []

        turn = build_turn(
            session_id=session_id,
            role=role,
            content=content,
            context_pack_id=context_pack_id,
            provider_run_id=provider_run_id,
            source_refs=source_refs,
        )
        _transcript_store.append(turn)

        if context_pack_id or provider_run_id:
            _session_store.update_context(
                session_id,
                context_pack_id=context_pack_id,
                provider_run_id=provider_run_id,
            )

        return {"data": turn_to_dict(turn)}

    return router


def _raise_error(
    bff_error: Optional[BffErrorFactory],
    status_code: int,
    error_code: Any,
    message: str,
    detail: str = "",
    **kwargs: Any,
) -> None:
    if bff_error is not None:
        raise bff_error(status_code, error_code, message, detail or message, **kwargs)
    raise HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": error_code.value if hasattr(error_code, "value") else str(error_code),
                "message": message,
                "details": kwargs if kwargs else {},
            }
        },
    )

