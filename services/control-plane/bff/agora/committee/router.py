"""Agora committee routes: the calling trader's own meetings only.

Operator decision 2026-11-26: Agora is for human traders. The committee lists the
deliberative meetings the caller convened (convened_by agora_user == caller) plus
MT-4 reviews whose subject is the caller, newest first. Persona gate panels and
institutional persona meetings are served by the Management meetings center.
Any session outside that set answers the same non-enumerating 404.
"""
from typing import Optional

from fastapi import APIRouter, Query, Request

from .client import CommitteeOwnerClient
from .owner import admit, envelope

NO_TRADER_MEETINGS = "no_trader_meetings"


def create_committee_router(*, extract_identity, require_read_role, require_write_role, utc_now):
    router = APIRouter(tags=["agora-committee"])

    def owner(request: Request, write: bool = False) -> CommitteeOwnerClient:
        identity, scope = admit(request, extract_identity=extract_identity,
                                require_role=require_write_role if write else require_read_role, utc_now=utc_now)
        return CommitteeOwnerClient(scope.tenant_id, identity.operator_id, user_id=scope.user_id)

    @router.get("/bff/agora/committee/sessions")
    def sessions(
        request: Request,
        status: Optional[str] = Query(default=None),
        type: Optional[str] = Query(default=None),
        meeting_type: Optional[str] = Query(default=None),
        persona_id: Optional[str] = Query(default=None),
        cursor: Optional[str] = Query(default=None),
        limit: Optional[int] = Query(default=None, ge=1, le=200),
        session_class: Optional[str] = Query(default=None),
    ):
        # session_class is accepted for compatibility; only deliberative meetings are in scope.
        client = owner(request)
        if session_class in {"gate_panel", "persona_panel"}:
            items: list = []
        else:
            items = client.trader_meetings(
                status=status,
                meeting_type=meeting_type or type,
                persona_id=persona_id,
                cursor=cursor,
                limit=limit,
            )
        return envelope(items, owner="consultation", empty_reason=NO_TRADER_MEETINGS)

    @router.get("/bff/agora/committee/meetings")
    def meetings(
        request: Request,
        status: Optional[str] = Query(default=None),
        type: Optional[str] = Query(default=None),
        meeting_type: Optional[str] = Query(default=None),
        persona_id: Optional[str] = Query(default=None),
        cursor: Optional[str] = Query(default=None),
        limit: Optional[int] = Query(default=None, ge=1, le=200),
        include_panels: bool = Query(default=False),
    ):
        # include_panels is accepted for compatibility; gate panels are never returned here.
        items = owner(request).trader_meetings(
            status=status,
            meeting_type=meeting_type or type,
            persona_id=persona_id,
            cursor=cursor,
            limit=limit,
        )
        return envelope(items, owner="consultation", empty_reason=NO_TRADER_MEETINGS)

    @router.get("/bff/agora/committee/proposals")
    @router.get("/bff/agora/committee/meetings/proposals")
    def proposals(
        request: Request,
        status: Optional[str] = Query(default=None),
        persona_id: Optional[str] = Query(default=None),
        meeting_id: Optional[str] = Query(default=None),
    ):
        items = owner(request).trader_proposals(status=status, persona_id=persona_id, meeting_id=meeting_id)
        return envelope(items, owner="consultation", empty_reason="no_meeting_proposals")

    @router.get("/bff/agora/committee/catalogue")
    @router.get("/bff/agora/committee/meetings/catalogue")
    def catalogue(request: Request):
        return envelope(owner(request).catalogue(), owner="consultation")

    @router.get("/bff/agora/committee/meetings/{meeting_id}")
    def meeting_detail(meeting_id: str, request: Request):
        return envelope(owner(request).trader_meeting(meeting_id), owner="consultation")

    @router.get("/bff/agora/committee/meetings/{meeting_id}/proposals")
    def meeting_proposals(
        meeting_id: str,
        request: Request,
        status: Optional[str] = Query(default=None),
        persona_id: Optional[str] = Query(default=None),
    ):
        items = owner(request).trader_proposals(status=status, persona_id=persona_id, meeting_id=meeting_id)
        return envelope(items, owner="consultation", empty_reason="no_meeting_proposals")

    @router.get("/bff/agora/committee/sessions/{session_id}")
    def session(session_id: str, request: Request):
        return envelope(owner(request).trader_meeting(session_id), owner="consultation")

    @router.get("/bff/agora/committee/sessions/{session_id}/memos")
    def memos(session_id: str, request: Request):
        client = owner(request)
        memo_items = client.trader_memos(session_id)
        if memo_items and "case_file_view" not in memo_items[0]:
            view = client.case_file_view(session_id)
            memo_items = [{**m, "case_file_view": view} for m in memo_items]
        return envelope(memo_items, owner="consultation", empty_reason="no_panel_memos")

    @router.post("/bff/agora/committee/sessions/{session_id}/memos/{memo_id}/publish")
    def publish(session_id: str, memo_id: str, request: Request):
        return envelope(owner(request, write=True).trader_publish(session_id, memo_id), owner="consultation")

    return router
