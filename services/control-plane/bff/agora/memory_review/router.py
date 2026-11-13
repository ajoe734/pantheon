"""Authenticated memory review; quarantine uses the existing candidate owner."""
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..committee.owner import admit, envelope, unavailable
from .client import MemoryReviewClient


class QuarantineRequest(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
    reason: str = Field(min_length=1, max_length=4000)
    audit_receipt_id: str = Field(min_length=1, max_length=500)
    kind: Literal["lesson_candidate", "persona", "institutional"] = "lesson_candidate"


def create_memory_review_router(*, extract_identity, require_read_role, require_write_role, utc_now):
    router = APIRouter(tags=["agora-memory-review"])

    def owner(request, write=False):
        identity, scope = admit(request, extract_identity=extract_identity,
                                require_role=require_write_role if write else require_read_role, utc_now=utc_now)
        return identity, MemoryReviewClient(request.headers.get("Authorization", ""), scope.tenant_id)

    @router.get("/bff/agora/memory")
    def review(request: Request, persona_id: str | None = Query(None, min_length=1),
               scope: Literal["persona", "institutional", "both"] = "both",
               limit: int = Query(100, ge=1, le=100)):
        identity, client = owner(request)
        if scope in {"persona", "both"} and not persona_id:
            raise unavailable("memory", "persona_id_required", 422)
        # Institutional-only reads do not fetch private lesson candidates.
        data = client.review(actor_id=identity.operator_id, persona_id=persona_id if scope != "institutional" else None,
                             scope=scope, limit=limit)
        result = envelope(data, owner="memory")
        if not data["entries"] and not data["lesson_candidates"]:
            result.update(status="unavailable", reason="no_memory_records")
        return result

    @router.post("/bff/agora/memory/{memory_id}/actions/quarantine")
    def quarantine(memory_id: str, body: QuarantineRequest, request: Request):
        identity, client = owner(request, write=True)
        if body.kind != "lesson_candidate":
            raise unavailable("memory", "entry_quarantine_not_supported_by_memory_owner")
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key:
            raise unavailable("memory", "idempotency_key_required", 400)
        return envelope(client.quarantine(memory_id, actor_id=identity.operator_id, reason=body.reason,
                                          audit_receipt_id=body.audit_receipt_id, idempotency_key=key), owner="memory")

    return router
