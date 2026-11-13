"""Restored committee routes: tenant-scoped panel reads and owner publishing."""
from fastapi import APIRouter, Request

from .client import CommitteeOwnerClient
from .owner import admit, envelope


def create_committee_router(*, extract_identity, require_read_role, require_write_role, utc_now):
    router = APIRouter(tags=["agora-committee"])

    def owner(request, write=False):
        identity, scope = admit(request, extract_identity=extract_identity,
                                require_role=require_write_role if write else require_read_role, utc_now=utc_now)
        return CommitteeOwnerClient(scope.tenant_id, identity.operator_id)

    @router.get("/bff/agora/committee/sessions")
    def sessions(request: Request):
        return envelope(owner(request).panels(), owner="consultation", empty_reason="no_persona_panels")

    @router.get("/bff/agora/committee/sessions/{session_id}")
    def session(session_id: str, request: Request):
        client = owner(request)
        return envelope({**client.panel(session_id), "case_file_view": client.case_file_view(session_id)},
                        owner="consultation")

    @router.get("/bff/agora/committee/sessions/{session_id}/memos")
    def memos(session_id: str, request: Request):
        client = owner(request)
        memos = client.memos(session_id)
        view = client.case_file_view(session_id) if memos else None
        return envelope([{**m, "case_file_view": view} for m in memos], owner="consultation",
                        empty_reason="no_panel_memos")

    @router.post("/bff/agora/committee/sessions/{session_id}/memos/{memo_id}/publish")
    def publish(session_id: str, memo_id: str, request: Request):
        return envelope(owner(request, write=True).publish(session_id, memo_id), owner="consultation")

    return router
