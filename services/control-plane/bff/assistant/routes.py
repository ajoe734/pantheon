from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, Header, HTTPException

from models import ErrorCode

from .context_composer import AssistantContextPolicyError
from .models import AssistantContextPack, AssistantContextPackRequest, AssistantContextPackResponse


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
) -> APIRouter:
    router = APIRouter(prefix="/bff/assistant", tags=["assistant"])

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

    return router

