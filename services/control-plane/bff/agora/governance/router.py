from __future__ import annotations

import uuid
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Literal, Optional

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


_APPROVAL_ACTION_ROLES = frozenset({"approver", "reviewer", "admin"})
_APPROVED_OUTCOMES = frozenset({"approved", "approved_with_conditions"})
_DECIDED_APPROVAL_STATES = frozenset({"approved", "decided"})
_CANONICAL_TARGET_TYPES = {
    "strategy": frozenset({"strategy", "strategy_spec"}),
}
_CANONICAL_REVIEWER_ROLES = {
    "human": frozenset({"governance_reviewer", "risk_owner", "governance_committee"}),
    "risk": frozenset({"risk", "risk_owner"}),
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _approval_actor(record: Mapping[str, Any]) -> str:
    return _clean(
        record.get("reviewer")
        or record.get("actor_id")
        or record.get("decided_by")
        or record.get("approved_by")
    )


def _approval_scope(record: Mapping[str, Any]) -> tuple[str, str]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    tenant_id = _clean(
        record.get("tenant_id")
        or record.get("tenantId")
        or metadata.get("tenant_id")
        or metadata.get("tenantId")
    )
    user_id = _clean(
        record.get("owner_user_id")
        or record.get("user_id")
        or record.get("userId")
        or metadata.get("owner_user_id")
        or metadata.get("user_id")
        or metadata.get("userId")
    )
    return tenant_id, user_id


_CONTENT_DIGEST_FIELDS = (
    "proposal_type", "target_kind", "target_id", "target_version",
    "current_value", "proposed_value", "rationale", "evidence_refs",
    "environment_ceiling", "validation_plan", "rollback_trigger",
    "rollback_action", "required_permissions", "required_reviewers",
)


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def proposal_content_digest(record: Mapping[str, Any]) -> str:
    return _digest({field: record.get(field) for field in _CONTENT_DIGEST_FIELDS})


def _parse_time(value: Any) -> Optional[datetime]:
    raw = _clean(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _approval_binding_error(current: Mapping[str, Any], record: Mapping[str, Any]) -> Optional[str]:
    now = datetime.now(timezone.utc)
    proposal_expires_at = _parse_time(current.get("expires_at"))
    if proposal_expires_at is None or proposal_expires_at <= now:
        return "proposal is expired"
    if _clean(record.get("proposal_id")) != _clean(current.get("proposal_id")):
        return "authoritative approval proposal id mismatch"
    try:
        revision_matches = int(record.get("proposal_revision")) == int(current.get("revision"))
    except (TypeError, ValueError):
        revision_matches = False
    if not revision_matches:
        return "authoritative approval proposal revision mismatch"
    if _clean(record.get("proposal_content_digest")) != _clean(current.get("proposal_content_digest")):
        return "authoritative approval proposal content digest mismatch"
    if not current.get("validation") or not current.get("validation_result_digest"):
        return "proposal validation is required before approval"
    if _clean(record.get("validation_result_digest")) != _clean(current.get("validation_result_digest")):
        return "authoritative approval validation digest mismatch"
    decided_at = _parse_time(record.get("decided_at"))
    validated_at = _parse_time(current.get("validated_at"))
    if decided_at is None or validated_at is None or decided_at < validated_at:
        return "authoritative approval must be decided after validation"
    expires_at = _parse_time(record.get("expires_at"))
    if expires_at is not None and expires_at <= now:
        return "authoritative approval is expired"
    if decided_at > proposal_expires_at:
        return "authoritative approval was decided after proposal expiry"
    if record.get("superseded_by"):
        return "authoritative approval is superseded"
    if record.get("revoked_at") or _clean(record.get("decision_state") or record.get("state")).lower() == "revoked":
        return "authoritative approval is revoked"
    return None


def build_proposal_record(
    body: ProposalCreate,
    *,
    tenant_id: str,
    owner_user_id: str,
    proposer: str,
    now: str,
) -> Dict[str, Any]:
    record = {
        **body.model_dump(mode="json"),
        "proposal_id": f"prop_{uuid.uuid4().hex}",
        "revision": 1,
        "state": "draft",
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "proposer": proposer,
        "created_at": now,
        "updated_at": now,
        "audit": [{"action": "create", "actor": proposer, "at": now}],
        "governed_action_link": None,
        "result_refs": [],
        "execution_authority": "none",
        "no_capital_authority_proof": "governed_proposal_no_capital_or_order_authority",
    }
    record["proposal_content_digest"] = proposal_content_digest(record)
    return record


def authoritative_approval_availability(
    *,
    current: Mapping[str, Any],
    decisions: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Return scoped canonical refs plus collective reviewer readiness for UI."""
    if not current.get("validation") or not current.get("validation_result_digest"):
        return {
            "refs": [], "ready": False, "reason": "proposal_not_validated",
            "missing_required_reviewers": list(current.get("required_reviewers") or []),
        }
    proposer = _clean(current.get("proposer"))
    proposal_tenant_id = _clean(current.get("tenant_id"))
    proposal_user_id = _clean(current.get("owner_user_id"))
    target_kind = _clean(current.get("target_kind")).lower()
    accepted_target_types = _CANONICAL_TARGET_TYPES.get(
        target_kind,
        frozenset({target_kind}),
    )
    required_reviewers = {
        _clean(reviewer).lower()
        for reviewer in current.get("required_reviewers", [])
        if _clean(reviewer)
    }
    refs: list[str] = []
    covered_reviewers: set[str] = set()
    for record in decisions:
        if not isinstance(record, Mapping):
            continue
        decision_id = _clean(record.get("decision_id") or record.get("id"))
        if not decision_id:
            continue
        decision_tenant_id, decision_user_id = _approval_scope(record)
        if (
            not decision_tenant_id
            or not decision_user_id
            or decision_tenant_id != proposal_tenant_id
            or decision_user_id != proposal_user_id
        ):
            continue
        if _clean(record.get("outcome") or record.get("decision")).lower() not in _APPROVED_OUTCOMES:
            continue
        if _clean(record.get("state") or record.get("decision_state")).lower() not in _DECIDED_APPROVAL_STATES:
            continue
        if _clean(record.get("target_type")).lower() not in accepted_target_types:
            continue
        if _clean(record.get("target_id")) != _clean(current.get("target_id")):
            continue
        if _clean(record.get("target_version")) != _clean(current.get("target_version")):
            continue
        if _approval_binding_error(current, record) is not None:
            continue
        reviewer = _approval_actor(record)
        if not reviewer or (proposer and reviewer == proposer):
            continue
        decision_reviewers = {reviewer.lower()}
        actor_role = _clean(record.get("actor_role"))
        if actor_role:
            decision_reviewers.add(actor_role.lower())
        if required_reviewers and not any(
            _CANONICAL_REVIEWER_ROLES.get(required, frozenset({required})).intersection(
                decision_reviewers
            )
            for required in required_reviewers
        ):
            continue
        refs.append(decision_id)
        covered_reviewers.update(decision_reviewers)
    missing_reviewers = sorted(
        reviewer
        for reviewer in required_reviewers
        if not _CANONICAL_REVIEWER_ROLES.get(
            reviewer,
            frozenset({reviewer}),
        ).intersection(covered_reviewers)
    )
    refs = list(dict.fromkeys(refs))
    ready = bool(refs) and not missing_reviewers
    if missing_reviewers:
        reason = "required_authoritative_reviewers_missing"
    elif not refs:
        reason = "authoritative_approval_required"
    else:
        reason = None
    return {
        "refs": refs,
        "ready": ready,
        "reason": reason,
        "missing_required_reviewers": missing_reviewers,
    }


def _validate_authoritative_approval_refs(
    *,
    current: Mapping[str, Any],
    approval_refs: list[str],
    get_approval_decision: Callable[[str], Optional[Mapping[str, Any]]],
) -> list[str]:
    clean_refs = list(dict.fromkeys(_clean(ref) for ref in approval_refs))
    if not clean_refs or any(not ref for ref in clean_refs):
        raise HTTPException(422, detail="approval requires authoritative approval refs")

    proposer = _clean(current.get("proposer"))
    required_reviewers = {
        _clean(reviewer).lower()
        for reviewer in current.get("required_reviewers", [])
        if _clean(reviewer)
    }
    covered_reviewers: set[str] = set()

    for approval_ref in clean_refs:
        try:
            record = get_approval_decision(approval_ref)
        except Exception as exc:
            raise HTTPException(
                503,
                detail="authoritative approval store is unavailable",
            ) from exc
        if not isinstance(record, Mapping):
            raise HTTPException(
                422,
                detail=f"approval ref {approval_ref!r} is not authoritative",
            )

        record_id = _clean(record.get("decision_id") or record.get("id"))
        if record_id != approval_ref:
            raise HTTPException(422, detail="authoritative approval id mismatch")
        decision_tenant_id, decision_user_id = _approval_scope(record)
        if not decision_tenant_id or not decision_user_id:
            raise HTTPException(422, detail="authoritative approval scope is missing")
        if (
            decision_tenant_id != _clean(current.get("tenant_id"))
            or decision_user_id != _clean(current.get("owner_user_id"))
        ):
            raise HTTPException(422, detail="authoritative approval scope mismatch")
        if _clean(record.get("outcome") or record.get("decision")).lower() not in _APPROVED_OUTCOMES:
            raise HTTPException(422, detail="authoritative approval is not approved")
        if _clean(record.get("state") or record.get("decision_state")).lower() not in _DECIDED_APPROVAL_STATES:
            raise HTTPException(422, detail="authoritative approval is not decided")
        target_kind = _clean(current.get("target_kind")).lower()
        accepted_target_types = _CANONICAL_TARGET_TYPES.get(
            target_kind,
            frozenset({target_kind}),
        )
        if _clean(record.get("target_type")).lower() not in accepted_target_types:
            raise HTTPException(422, detail="authoritative approval target type mismatch")
        if _clean(record.get("target_id")) != _clean(current.get("target_id")):
            raise HTTPException(422, detail="authoritative approval target id mismatch")
        if _clean(record.get("target_version")) != _clean(current.get("target_version")):
            raise HTTPException(422, detail="authoritative approval target version mismatch")
        binding_error = _approval_binding_error(current, record)
        if binding_error is not None:
            raise HTTPException(422, detail=binding_error)

        reviewer = _approval_actor(record)
        if not reviewer:
            raise HTTPException(422, detail="authoritative approval reviewer is missing")
        if proposer and reviewer == proposer:
            raise HTTPException(403, detail="proposal self-approval is forbidden")
        covered_reviewers.add(reviewer.lower())
        actor_role = _clean(record.get("actor_role"))
        if actor_role:
            covered_reviewers.add(actor_role.lower())

    missing_reviewers = {
        reviewer
        for reviewer in required_reviewers
        if not _CANONICAL_REVIEWER_ROLES.get(
            reviewer,
            frozenset({reviewer}),
        ).intersection(covered_reviewers)
    }
    if missing_reviewers:
        raise HTTPException(
            422,
            detail=f"required authoritative reviewers missing: {sorted(missing_reviewers)}",
        )
    return clean_refs


def create_governance_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    get_approval_decision: Callable[[str], Optional[Mapping[str, Any]]],
    list_approval_decisions: Callable[[], Iterable[Mapping[str, Any]]],
    store: Optional[ProposalStore] = None,
) -> APIRouter:
    from ...models import ErrorCode

    router = APIRouter(tags=["agora-governance"])
    proposals = store or ProposalStore()

    def scope(auth: Optional[str], *, write: bool = False):
        identity = extract_identity(auth)
        require_read_role(identity)
        if write:
            require_write_role(identity)
        return identity, resolve_agora_user_scope(identity, utc_now=utc_now)

    def envelope(row: Dict[str, Any], response: Response) -> Dict[str, Any]:
        response.headers["ETag"] = proposals.etag(row)
        try:
            availability = authoritative_approval_availability(
                current=row,
                decisions=list_approval_decisions(),
            )
        except Exception:
            availability = {
                "refs": [], "ready": False,
                "reason": "authoritative_approval_store_unavailable",
                "missing_required_reviewers": list(row.get("required_reviewers") or []),
            }
        projected = {
            **row,
            "available_approval_decision_refs": availability["refs"],
            "approval_decision_refs_authority": "canonical_read_store",
            "approval_decision_readiness": {
                key: availability[key]
                for key in ("ready", "reason", "missing_required_reviewers")
            },
        }
        return {"data": projected, "meta": {"snapshot_at": utc_now(), "capability": "agora.governance.v1"}}

    @router.post("/bff/agora/proposals", status_code=201)
    def create(
        body: ProposalCreate,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ):
        _, resolved = scope(authorization, write=True)
        now = utc_now()
        row = build_proposal_record(
            body,
            tenant_id=resolved.tenant_id,
            owner_user_id=resolved.user_id,
            proposer=resolved.operator_id,
            now=now,
        )
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
        identity, resolved = scope(authorization, write=True)
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
        if body.action != "approve" and body.approval_refs:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "approval refs are only accepted for approve actions",
                "AGORA_PROPOSAL_APPROVAL_REFS_ACTION_INVALID",
            )
        authoritative_approval_refs: list[str] = []
        if body.action == "approve":
            if current["state"] != "validated":
                raise bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Approval requires validated state and authoritative approval refs",
                    "AGORA_PROPOSAL_APPROVAL_PRECONDITION_FAILED",
                )
            roles = {
                _clean(role).lower()
                for role in getattr(identity, "roles", [])
                if _clean(role)
            }
            if not roles.intersection(_APPROVAL_ACTION_ROLES):
                raise bff_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Proposal approval requires an approver, reviewer, or admin role",
                    "AGORA_PROPOSAL_APPROVER_ROLE_REQUIRED",
                )
            actor_id = _clean(getattr(identity, "operator_id", ""))
            proposer_id = _clean(current.get("proposer"))
            if not actor_id or not proposer_id:
                raise bff_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Proposal approval identity could not be resolved",
                    "AGORA_PROPOSAL_APPROVAL_IDENTITY_UNRESOLVED",
                    precondition_failed="distinct_approver",
                )
            # ProposalStore is user-private. Delegated review tokens must retain
            # the proposal's user_id scope while using a distinct operator_id.
            if actor_id == proposer_id:
                raise bff_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Proposal self-approval by its proposer is forbidden",
                    "AGORA_PROPOSAL_SELF_APPROVAL_FORBIDDEN",
                    precondition_failed="distinct_approver",
                    suggestion="Route this proposal to a different approver",
                    details_extra={
                        "proposalId": proposal_id,
                        "actorId": actor_id,
                        "proposerId": proposer_id,
                    },
                )
            authoritative_approval_refs = _validate_authoritative_approval_refs(
                current=current,
                approval_refs=body.approval_refs,
                get_approval_decision=get_approval_decision,
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
        now = utc_now()
        next_row = {
            **current,
            "revision": current["revision"] + 1,
            "updated_at": now,
        }
        if body.action == "modify":
            next_row["proposed_value"] = body.proposed_value
            next_row["state"] = "draft"
            next_row.pop("validation", None)
            next_row.pop("validated_at", None)
            next_row.pop("validation_result_digest", None)
        else:
            next_row["state"] = _TRANSITIONS[body.action]
        next_row["evidence_refs"] = list(
            dict.fromkeys(current["evidence_refs"] + body.evidence_refs)
        )
        next_row["proposal_content_digest"] = proposal_content_digest(next_row)
        next_row["audit"] = current["audit"] + [
            {
                "action": body.action,
                "actor": resolved.operator_id,
                "reason": body.reason,
                "at": now,
                "approval_refs": authoritative_approval_refs,
            }
        ]
        if body.action == "validate":
            next_row["validation"] = body.validation_result
            next_row["validated_at"] = now
            next_row["validation_result_digest"] = _digest(body.validation_result)
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
