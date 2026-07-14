from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field, model_validator

from ..identity.scope import resolve_agora_user_scope
from .store import ProposalConflict, ProposalStore

ProposalType = Literal[
    "strategy_patch", "condition_change", "risk_limit_recommendation",
    "research_request", "paper_candidate_request", "allocation_review_request",
    "containment_recommendation", "journal_lesson", "memory_candidate", "persona_mutation_review",
]
ProposalState = Literal["draft", "review_requested", "research_requested", "validated", "approved", "rejected", "deferred", "cancelled"]


class ProposalCreate(BaseModel):
    proposal_type: ProposalType
    target_kind: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    target_version: str = Field(min_length=1)
    current_value: Any
    proposed_value: Any
    rationale: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    expected_benefit: str = Field(min_length=1)
    adverse_scenarios: list[str] = Field(min_length=1)
    environment_ceiling: Literal["analysis", "research", "shadow", "paper", "canary", "live"]
    expires_at: datetime
    validation_plan: Dict[str, Any]
    rollback_trigger: str = Field(min_length=1)
    rollback_action: str = Field(min_length=1)
    required_permissions: list[str] = Field(min_length=1)
    required_reviewers: list[str] = Field(min_length=1)
    human_gate: bool = True
    consultation_refs: list[str] = Field(default_factory=list)
    workshop_refs: list[str] = Field(default_factory=list)
    dependency_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def governed(self):
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must include a timezone offset")
        if self.expires_at <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        if self.environment_ceiling in {"paper", "canary", "live"} and not self.human_gate:
            raise ValueError("paper/live proposals require a human gate")
        return self


class ProposalAction(BaseModel):
    action: Literal["request_review", "request_research", "modify", "validate", "approve", "reject", "defer", "cancel"]
    reason: str = Field(min_length=1)
    proposed_value: Optional[Any] = None
    evidence_refs: list[str] = Field(default_factory=list)
    approval_refs: list[str] = Field(default_factory=list)
    validation_result: Optional[Dict[str, Any]] = None


_TRANSITIONS = {
    "request_review": "review_requested", "request_research": "research_requested",
    "validate": "validated", "approve": "approved", "reject": "rejected",
    "defer": "deferred", "cancel": "cancelled",
}


def create_governance_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    store: Optional[ProposalStore] = None,
) -> APIRouter:
    from models import ErrorCode

    router = APIRouter(tags=["agora-governance"])
    proposals = store or ProposalStore()

    def scope(auth: Optional[str]):
        identity = extract_identity(auth)
        require_read_role(identity)
        return identity, resolve_agora_user_scope(identity, utc_now=utc_now)

    def envelope(row: Dict[str, Any], response: Response) -> Dict[str, Any]:
        response.headers["ETag"] = proposals.etag(row)
        return {"data": row, "meta": {"snapshot_at": utc_now(), "capability": "agora.governance.v1"}}

    @router.post("/bff/agora/proposals", status_code=201)
    def create(
        body: ProposalCreate,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ):
        identity, resolved = scope(authorization)
        require_write_role(identity)
        now = utc_now()
        row = {
            **body.model_dump(mode="json"),
            "proposal_id": f"prop_{uuid.uuid4().hex}",
            "revision": 1,
            "state": "draft",
            "tenant_id": resolved.tenant_id,
            "owner_user_id": resolved.user_id,
            "proposer": resolved.operator_id,
            "created_at": now,
            "updated_at": now,
            "audit": [{"action": "create", "actor": resolved.operator_id, "at": now}],
            "governed_action_link": None,
            "result_refs": [],
        }
        try:
            return envelope(proposals.create(row, idempotency_key), response)
        except ProposalConflict as exc:
            raise bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                str(exc),
                "AGORA_PROPOSAL_IDEMPOTENCY_CONFLICT",
            ) from exc

    @router.get("/bff/agora/proposals/{proposal_id}")
    def get(
        proposal_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
    ):
        _, resolved = scope(authorization)
        row = proposals.get(proposal_id, resolved.tenant_id, resolved.user_id)
        if not row:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Proposal not found",
                "AGORA_PROPOSAL_NOT_FOUND",
            )
        return envelope(row, response)

    @router.get("/bff/agora/proposals/{proposal_id}/revisions")
    def revisions(
        proposal_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        _, resolved = scope(authorization)
        rows = proposals.history(proposal_id, resolved.tenant_id, resolved.user_id)
        if not rows:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Proposal not found",
                "AGORA_PROPOSAL_NOT_FOUND",
            )
        return {
            "data": rows,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.governance.v1",
                "total": len(rows),
            },
        }

    @router.post("/bff/agora/proposals/{proposal_id}/actions")
    def act(
        proposal_id: str,
        body: ProposalAction,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        if_match: str = Header(alias="If-Match"),
    ):
        identity, resolved = scope(authorization)
        require_write_role(identity)
        current = proposals.get(proposal_id, resolved.tenant_id, resolved.user_id)
        if not current:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Proposal not found",
                "AGORA_PROPOSAL_NOT_FOUND",
            )
        if current["state"] in {"approved", "rejected", "cancelled"}:
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Proposal is terminal",
                "AGORA_PROPOSAL_TERMINAL_STATE",
            )

        approval_refs = [ref.strip() for ref in body.approval_refs if ref.strip()]
        if body.action == "approve" and (
            current["state"] != "validated" or not approval_refs
        ):
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Approval requires validated state and authoritative approval refs",
                "AGORA_PROPOSAL_APPROVAL_PRECONDITION_FAILED",
            )
        if body.action == "validate" and not body.validation_result:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "validation_result is required",
                "AGORA_PROPOSAL_VALIDATION_RESULT_REQUIRED",
            )
        if body.action == "modify" and body.proposed_value is None:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "modify requires proposed_value",
                "AGORA_PROPOSAL_PROPOSED_VALUE_REQUIRED",
            )
        if body.action == "approve":
            roles = {
                str(role).strip()
                for role in getattr(identity, "roles", [])
                if str(role).strip()
            }
            if not roles.intersection({"approver", "admin"}):
                raise bff_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Proposal approval requires an approver role",
                    "AGORA_PROPOSAL_APPROVER_ROLE_REQUIRED",
                )
            actor_id = str(getattr(identity, "operator_id", "") or "").strip()
            proposer_id = str(current.get("proposer") or "").strip()
            if not actor_id or not proposer_id:
                raise bff_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Proposal approval identity could not be resolved",
                    "AGORA_PROPOSAL_APPROVAL_IDENTITY_UNRESOLVED",
                    precondition_failed="distinct_approver",
                )
            if actor_id == proposer_id:
                raise bff_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "A proposal cannot be approved by its proposer",
                    "AGORA_PROPOSAL_SELF_APPROVAL_FORBIDDEN",
                    precondition_failed="distinct_approver",
                    suggestion="Route this proposal to a different approver",
                    details_extra={
                        "proposalId": proposal_id,
                        "actorId": actor_id,
                        "proposerId": proposer_id,
                    },
                )
        now = utc_now()
        next_row = {
            **current,
            "revision": current["revision"] + 1,
            "updated_at": now,
        }
        if body.action == "modify":
            next_row["proposed_value"] = body.proposed_value
            next_row["state"] = "draft"
        else:
            next_row["state"] = _TRANSITIONS[body.action]
        next_row["evidence_refs"] = list(
            dict.fromkeys(current["evidence_refs"] + body.evidence_refs)
        )
        next_row["audit"] = current["audit"] + [
            {
                "action": body.action,
                "actor": resolved.operator_id,
                "reason": body.reason,
                "at": now,
                "approval_refs": approval_refs,
            }
        ]
        if body.action == "validate":
            next_row["validation"] = body.validation_result
            next_row["governed_action_link"] = {
                "route": "/bff/actions/{type}/{id}/{action}",
                "target_type": current["target_kind"],
                "target_id": current["target_id"],
                "action": "submit_review",
                "execution_authority": "none",
            }
        try:
            saved = proposals.append(proposal_id, if_match, next_row)
        except ProposalConflict as exc:
            raise bff_error(
                412,
                ErrorCode.PRECONDITION_FAILED,
                str(exc),
                "AGORA_PROPOSAL_ETAG_STALE",
                precondition_failed="If-Match",
            ) from exc
        return envelope(saved, response)

    return router
