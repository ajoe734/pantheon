"""Route unsponsored persona-meeting proposals into the existing Agora candidate-decision approval path.

Per SD-PERSONA-MEETINGS-20261115 §11.2 (A4, A5) and C1/C2 parallel contract:
- Proposals without a trading-persona sponsor (`sponsor_persona_id is None` or
  `kind == "agora_candidate_decision"`) become Agora candidate decisions on the
  existing v1.9 candidate-decision lifecycle (`draft -> review_requested ->
  validated -> formal_approval_linked`).
- Proposals with a trading-persona sponsor are refused on this path and must
  use the M5 governed owner entry points under their sponsor's identity.
- Nothing from Agora trades, moves capital, or writes canonical state.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Response
from pydantic import BaseModel, Field

from ...candidate_decisions.adapters import (
    CANONICAL_VALIDATOR_ID,
    CandidateBindingValidationAdapter,
    ReadStoreApprovalAdapter,
)
from ...candidate_decisions.models import (
    CandidateDecisionCommand,
    CandidateFromMeasureCommand,
    CandidateValidationCommand,
    FormalApprovalLinkCommand,
    canonical_sha256,
)
from ...candidate_decisions.router import (
    create_candidate_decision_router as _create_base_candidate_decision_router,
)
from ...candidate_decisions.service import CandidateDecisionService
from ...candidate_decisions.source import candidate_digest
from ...candidate_decisions.store import (
    CandidateDecisionConflict,
    CandidateDecisionStore,
    StoredMutation,
)
from ...committee.client import CommitteeOwnerClient
from ...identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from ..provider import authority_boundary, recommended_measure_sha256

_ORIG_FROM_GOVERNANCE_STORE = CandidateDecisionStore.from_governance_store


def _shared_from_governance_store(cls: Any, governance_store: Any) -> CandidateDecisionStore:
    if governance_store is not None:
        cached = getattr(governance_store, "_candidate_decision_store", None)
        if isinstance(cached, CandidateDecisionStore):
            return cached
    created = _ORIG_FROM_GOVERNANCE_STORE(governance_store)
    if governance_store is not None:
        try:
            setattr(governance_store, "_candidate_decision_store", created)
        except Exception:
            pass
    return created


if getattr(CandidateDecisionStore.from_governance_store, "__name__", "") != "_shared_from_governance_store":
    CandidateDecisionStore.from_governance_store = classmethod(_shared_from_governance_store)  # type: ignore[assignment]


_KIND_TO_PROPOSAL_TYPE = {
    "agora_candidate_decision": "research_request",
    "research_demand": "research_request",
    "research_request": "research_request",
    "evolution_proposal": "strategy_patch",
    "strategy_patch": "strategy_patch",
    "strategy_parameter_change": "strategy_patch",
    "condition_change": "condition_change",
    "risk_limit_recommendation": "risk_limit_recommendation",
    "harness_patch": "persona_mutation_review",
    "persona_mutation_review": "persona_mutation_review",
    "lesson_candidate": "memory_candidate",
    "memory_candidate": "memory_candidate",
    "journal_lesson": "journal_lesson",
    "lesson_rule": "condition_change",
    "pool_decision": "paper_candidate_request",
    "paper_candidate_request": "paper_candidate_request",
    "allocation_review_request": "allocation_review_request",
    "containment_recommendation": "containment_recommendation",
    "governance_approval": "risk_limit_recommendation",
}

_NON_TRADING_ORIGINS = frozenset({"agora_advisor", "private_servant"})
_NON_TRADING_ROLES = frozenset({
    "agora_advisor",
    "private_servant",
    "advisor",
    "servant",
    "risk",
    "risk_officer",
    "red_team",
    "devil_advocate",
})
_FORBIDDEN_CAPITAL_AUTHORITY_KEYS = frozenset({
    "submit_order",
    "order_submitted",
    "broker_called",
    "capital_changed",
    "move_capital",
    "write_canonical_state",
    "canonical_state_written",
    "live_execution",
})


class CandidateFromMeetingProposalCommand(BaseModel):
    model_config = {"extra": "forbid"}

    meeting_id: Optional[str] = Field(default=None, min_length=1)
    proposal_id: Optional[str] = Field(default=None, min_length=1)
    workshop_id: Optional[str] = Field(default=None, min_length=1)
    proposal: Optional[Dict[str, Any]] = None


def _is_private_or_non_trading_persona(record: Mapping[str, Any]) -> bool:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    for container in (record, metadata):
        pclass = str(container.get("persona_class") or container.get("archetype") or "").strip().lower()
        if pclass in {"agora_servant", "agora_advisor", "consult_panel", "risk_officer", "red_team"}:
            return True
        scope = str(
            container.get("owner_scope")
            or container.get("visibility_scope")
            or container.get("memory_scope")
            or ""
        ).strip().lower()
        if scope in {"user_private", "private", "private_user"}:
            return True
    return False


def is_trading_persona_sponsor(
    sponsor_persona_id: Any,
    *,
    meeting: Optional[Mapping[str, Any]] = None,
    read_store: Optional[Any] = None,
) -> bool:
    """Return True if ``sponsor_persona_id`` names an institutional trading persona."""
    clean = str(sponsor_persona_id or "").strip()
    if not clean:
        return False
    if clean in {"persona-risk-officer", "persona-red-team"} or clean.startswith("agora-servant-"):
        return False
    if isinstance(meeting, Mapping):
        for participant in meeting.get("participants") or []:
            if not isinstance(participant, Mapping):
                continue
            if str(participant.get("persona_id") or "").strip() != clean:
                continue
            origin = str(participant.get("origin") or "").strip().lower()
            role = str(participant.get("role") or "").strip().lower()
            if origin in _NON_TRADING_ORIGINS or role in _NON_TRADING_ROLES:
                return False
    if read_store is not None and callable(getattr(read_store, "list_personas", None)):
        try:
            personas = read_store.list_personas(include_market_persona_defaults=True) or []
        except Exception:
            personas = []
        for persona in personas:
            if not isinstance(persona, Mapping):
                continue
            pid = str(persona.get("persona_id") or persona.get("id") or "").strip()
            if pid == clean and _is_private_or_non_trading_persona(persona):
                return False
    return True


def _assert_no_execution_or_capital_authority(payload: Any) -> None:
    if not isinstance(payload, Mapping):
        return
    if payload.get("execution_authority") not in (None, "none"):
        raise ValueError("Agora candidate decisions cannot carry execution authority")
    env = str(payload.get("environment") or payload.get("environment_ceiling") or "").strip().lower()
    if env in {"canary", "live", "production"}:
        raise ValueError("Agora candidate decisions cannot target canary or live environments")
    for key in _FORBIDDEN_CAPITAL_AUTHORITY_KEYS:
        if payload.get(key) is True:
            raise ValueError(f"Agora candidate decisions forbid {key}")
    authority = payload.get("authority")
    if isinstance(authority, Mapping) and authority != authority_boundary():
        raise ValueError("Agora candidate decision authority must match the exact no-authority boundary")


def build_candidate_from_meeting_proposal(
    *,
    meeting_id: str,
    proposal: Mapping[str, Any],
    tenant_id: str,
    owner_user_id: str,
    proposer_id: str,
    now: datetime,
    expires_at: datetime,
    meeting: Optional[Mapping[str, Any]] = None,
    read_store: Optional[Any] = None,
) -> dict[str, Any]:
    """Freeze an unsponsored persona-meeting proposal as candidate revision 1."""
    clean_meeting_id = str(meeting_id or proposal.get("meeting_id") or "").strip()
    clean_proposal_id = str(proposal.get("proposal_id") or proposal.get("id") or "").strip()
    if not clean_meeting_id or not clean_proposal_id:
        raise ValueError("meeting_id and proposal_id are required")
    if not tenant_id or not owner_user_id or not proposer_id:
        raise ValueError("tenant_id, owner_user_id, and proposer_id are required")
    if now.tzinfo is None or expires_at.tzinfo is None or expires_at <= now:
        raise ValueError("candidate expiry must be a future timezone-aware timestamp")

    sponsor = proposal.get("sponsor_persona_id")
    if is_trading_persona_sponsor(sponsor, meeting=meeting, read_store=read_store):
        raise ValueError(
            "proposal has a trading-persona sponsor; trading-persona sponsored proposals "
            "must follow M5 governed owner entry points, not Agora candidate decisions"
        )

    _assert_no_execution_or_capital_authority(proposal)
    raw_payload = proposal.get("payload") if isinstance(proposal.get("payload"), Mapping) else {}
    _assert_no_execution_or_capital_authority(raw_payload)

    raw_kind = str(
        raw_payload.get("measure_type")
        or raw_payload.get("proposal_type")
        or proposal.get("proposal_type")
        or proposal.get("kind")
        or "agora_candidate_decision"
    ).strip()
    proposal_type = _KIND_TO_PROPOSAL_TYPE.get(raw_kind, "research_request")

    target_raw = raw_payload.get("target") if isinstance(raw_payload.get("target"), Mapping) else {}
    target_kind = str(
        target_raw.get("kind")
        or raw_payload.get("target_kind")
        or proposal.get("target_kind")
        or "strategy"
    ).strip()
    subject_ref = str(
        (meeting or {}).get("subject_ref")
        or proposal.get("subject_ref")
        or raw_payload.get("strategy_id")
        or "strategy-workshop"
    ).strip()
    target_id = str(
        target_raw.get("id")
        or raw_payload.get("target_id")
        or proposal.get("target_id")
        or subject_ref
    ).strip()
    target_version = str(
        target_raw.get("version")
        or raw_payload.get("target_version")
        or proposal.get("target_version")
        or "v1"
    ).strip()
    target_path = (
        str(target_raw.get("path") or raw_payload.get("target_path") or "").strip() or None
    )

    citations = [
        str(c).strip()
        for c in (proposal.get("citations") or raw_payload.get("citations") or [f"meeting:{clean_meeting_id}"])
        if str(c).strip()
    ]
    if not citations:
        citations = [f"meeting:{clean_meeting_id}"]

    now_iso = now.isoformat()
    evidence_refs = [
        {
            "ref_type": "consultation_meeting",
            "ref_id": ref,
            "version": "v1",
            "observed_at": now_iso,
            "data_cutoff": now_iso,
            "freshness": "fresh",
            "summary": f"Cited in deliberative meeting {clean_meeting_id}",
        }
        for ref in citations
    ]

    proposed_value = copy.deepcopy(
        raw_payload.get("proposed_value")
        if "proposed_value" in raw_payload
        else proposal.get("proposed_value")
        if "proposed_value" in proposal
        else raw_payload or {"summary": proposal.get("summary") or proposal.get("title") or clean_proposal_id}
    )
    rationale = str(
        raw_payload.get("rationale")
        or proposal.get("rationale")
        or proposal.get("summary")
        or f"Deliberative meeting {clean_meeting_id} unsponsored candidate proposal {clean_proposal_id}"
    ).strip()
    expected_benefit = str(
        raw_payload.get("expected_benefit")
        or proposal.get("expected_benefit")
        or "Evaluate the unsponsored meeting proposal through governed Agora validation and review before any action."
    ).strip()
    adverse_scenarios = list(
        raw_payload.get("adverse_scenarios")
        or proposal.get("adverse_scenarios")
        or ["The meeting hypothesis may not hold under out-of-sample validation."]
    )
    confidence_raw = raw_payload.get("confidence", proposal.get("confidence", 0.7))
    try:
        confidence = max(0.0, min(1.0, float(confidence_raw)))
    except (TypeError, ValueError):
        confidence = 0.7

    rollback_trigger = str(
        raw_payload.get("rollback_trigger")
        or proposal.get("rollback_trigger")
        or "Governed validation fails or operator review rejects the candidate."
    ).strip()
    rollback_action = str(
        raw_payload.get("rollback_action")
        or proposal.get("rollback_action")
        or "Discard the candidate decision and retain current state."
    ).strip()

    validation_plan = {
        "validator": CANONICAL_VALIDATOR_ID,
        "required_checks": [
            "source_binding",
            "evidence_freshness",
            "target_version",
            "authority_boundary",
            "rollback_plan",
        ],
    }

    measure = {
        "measure_id": clean_proposal_id,
        "measure_type": "research_request" if proposal_type == "research_request" else "strategy_parameter_change",
        "target": {
            "kind": target_kind,
            "id": target_id,
            "version": target_version,
            "path": target_path,
        },
        "current_value": copy.deepcopy(raw_payload.get("current_value")),
        "proposed_value": proposed_value,
        "rationale": rationale,
        "expected_benefit": expected_benefit,
        "adverse_scenarios": adverse_scenarios,
        "confidence": confidence,
        "evidence_refs": evidence_refs,
        "environment_ceiling": "paper",
        "validation_plan": validation_plan,
        "rollback_trigger": rollback_trigger,
        "rollback_action": rollback_action,
        "authority": authority_boundary(),
    }
    measure_sha = recommended_measure_sha256(measure)
    opinion_id = f"meeting-synthesis:{clean_meeting_id}"
    opinion_sha = canonical_sha256({
        "meeting_id": clean_meeting_id,
        "proposal_id": clean_proposal_id,
        "measure_sha256": measure_sha,
    })
    persona_id = str(
        (meeting or {}).get("chair_persona_id")
        or proposal.get("author_persona_id")
        or "agora-committee"
    ).strip()

    record: dict[str, Any] = {
        "proposal_id": clean_proposal_id,
        "revision": 1,
        "state": "draft",
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "proposer_id": proposer_id,
        "meeting_id": clean_meeting_id,
        "sponsor_persona_id": None,
        "approval_path": "agora_candidate_decision",
        "interaction_id": clean_meeting_id,
        "opinion_id": opinion_id,
        "opinion_sha256": opinion_sha,
        "provider_invocation_id": f"consultation-meeting:{clean_meeting_id}",
        "persona_id": persona_id,
        "persona_version": "meeting-v1",
        "measure_id": clean_proposal_id,
        "measure_sha256": measure_sha,
        "source_measure": copy.deepcopy(measure),
        "proposal_type": proposal_type,
        "target_kind": target_kind,
        "target_id": target_id,
        "target_version": target_version,
        "target_path": target_path,
        "current_value": copy.deepcopy(raw_payload.get("current_value")),
        "proposed_value": proposed_value,
        "rationale": rationale,
        "expected_benefit": expected_benefit,
        "adverse_scenarios": adverse_scenarios,
        "confidence": confidence,
        "evidence_refs": evidence_refs,
        "environment_ceiling": "paper",
        "validation_plan": validation_plan,
        "rollback_trigger": rollback_trigger,
        "rollback_action": rollback_action,
        "created_at": now_iso,
        "updated_at": now_iso,
        "expires_at": expires_at.isoformat(),
        "execution_authority": "none",
        "authority": authority_boundary(),
        "audit": [
            {
                "audit_ref": f"audit_{uuid.uuid4().hex}",
                "action": "created_from_meeting_proposal",
                "actor_id": proposer_id,
                "at": now_iso,
                "meeting_id": clean_meeting_id,
                "proposal_id": clean_proposal_id,
                "sponsor_persona_id": None,
                "opinion_id": opinion_id,
                "opinion_sha256": opinion_sha,
                "measure_id": clean_proposal_id,
                "measure_sha256": measure_sha,
                "execution_authority": "none",
            }
        ],
    }
    record["proposal_digest"] = candidate_digest(record)
    return record


def create_candidate_from_meeting_proposal(
    *,
    service: CandidateDecisionService,
    meeting_id: str,
    proposal: Mapping[str, Any],
    tenant_id: str,
    owner_user_id: str,
    proposer_id: str,
    idempotency_key: str,
    meeting: Optional[Mapping[str, Any]] = None,
    read_store: Optional[Any] = None,
) -> StoredMutation:
    now = service.clock()
    expires_at = now + timedelta(days=7)
    record = build_candidate_from_meeting_proposal(
        meeting_id=meeting_id,
        proposal=proposal,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        proposer_id=proposer_id,
        now=now,
        expires_at=expires_at,
        meeting=meeting,
        read_store=read_store,
    )
    fingerprint = canonical_sha256({
        "meeting_id": record["meeting_id"],
        "proposal_id": record["proposal_id"],
        "measure_sha256": record["measure_sha256"],
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "proposer_id": proposer_id,
    })
    replay = service.store.replay(
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        operation="create",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )
    if replay is not None:
        return replay
    history = service.store.history(record["proposal_id"], tenant_id, owner_user_id)
    if history:
        first = history[0]
        if (
            first.get("meeting_id") == record["meeting_id"]
            and first.get("measure_sha256") == record["measure_sha256"]
        ):
            return StoredMutation(history[-1], True)
    return service.store.create_candidate(
        record,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )


def dispatch_agora_candidate_decision(
    proposal: Mapping[str, Any],
    *,
    service: CandidateDecisionService,
    meeting_id: Optional[str] = None,
    meeting: Optional[Mapping[str, Any]] = None,
    tenant_id: str = "pantheon-dev",
    owner_user_id: str = "agora-user",
    proposer_id: str = "agora-user",
    idempotency_key: Optional[str] = None,
    read_store: Optional[Any] = None,
) -> dict[str, Any]:
    """Route a C2 ``agora_candidate_decision`` proposal into ``CandidateDecisionStore``."""
    mid = str(meeting_id or proposal.get("meeting_id") or (meeting or {}).get("meeting_id") or "").strip()
    pid = str(proposal.get("proposal_id") or proposal.get("id") or "").strip()
    key = idempotency_key or f"c2-agora-candidate:{mid}:{pid}"
    try:
        mutation = create_candidate_from_meeting_proposal(
            service=service,
            meeting_id=mid,
            proposal=proposal,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            proposer_id=proposer_id,
            idempotency_key=key,
            meeting=meeting,
            read_store=read_store,
        )
    except (ValueError, CandidateDecisionConflict) as exc:
        return {
            "proposal_id": pid,
            "meeting_id": mid,
            "downstream_target": "agora_candidate_decision",
            "downstream_id": None,
            "status": "failed",
            "error": str(exc),
            "execution_authority": "none",
        }
    resource = mutation.resource
    return {
        "proposal_id": pid,
        "meeting_id": mid,
        "downstream_target": "agora_candidate_decision",
        "downstream_id": resource["proposal_id"],
        "status": resource["state"],
        "error": None,
        "replayed": mutation.replayed,
        "execution_authority": "none",
    }


def route_meeting_candidate_decisions(
    meeting_or_proposals: Any,
    *,
    service: CandidateDecisionService,
    tenant_id: str,
    owner_user_id: str,
    proposer_id: str,
    meeting_id: Optional[str] = None,
    read_store: Optional[Any] = None,
    idempotency_prefix: str = "meeting-c2",
) -> list[dict[str, Any]]:
    """Route all unsponsored proposals from a meeting or C2 list into candidate decisions."""
    meeting_ctx: Optional[Mapping[str, Any]] = None
    if isinstance(meeting_or_proposals, Mapping):
        meeting_ctx = meeting_or_proposals
        mid = str(meeting_id or meeting_or_proposals.get("meeting_id") or "").strip()
        synthesis = (
            meeting_or_proposals.get("synthesis")
            if isinstance(meeting_or_proposals.get("synthesis"), Mapping)
            else {}
        )
        raw_proposals = list(synthesis.get("proposals") or meeting_or_proposals.get("proposals") or [])
    elif isinstance(meeting_or_proposals, list):
        mid = str(meeting_id or "").strip()
        raw_proposals = list(meeting_or_proposals)
    else:
        raise ValueError("meeting_or_proposals must be a meeting dict or proposal list")

    results: list[dict[str, Any]] = []
    for item in raw_proposals:
        if not isinstance(item, Mapping):
            continue
        sponsor = item.get("sponsor_persona_id")
        kind = str(item.get("kind") or "").strip()
        if is_trading_persona_sponsor(sponsor, meeting=meeting_ctx, read_store=read_store) and kind != "agora_candidate_decision":
            continue
        pid = str(item.get("proposal_id") or item.get("id") or "").strip()
        item_mid = str(item.get("meeting_id") or mid).strip()
        results.append(
            dispatch_agora_candidate_decision(
                item,
                service=service,
                meeting_id=item_mid,
                meeting=meeting_ctx,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                proposer_id=proposer_id,
                idempotency_key=f"{idempotency_prefix}:{item_mid}:{pid}",
                read_store=read_store,
            )
        )
    return results


def read_downstream_status(
    proposal_id: str,
    *,
    service: CandidateDecisionService,
    tenant_id: str,
    owner_user_id: str,
) -> dict[str, Any]:
    """Read back C2 downstream status for an ``agora_candidate_decision`` proposal."""
    detail = service.readback(
        proposal_id=proposal_id,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
    )
    candidate = detail["candidate"]
    return {
        "status": candidate["state"],
        "downstream_id": candidate["proposal_id"],
        "updated_at": candidate["updated_at"],
        "revision": candidate["revision"],
        "proposal_digest": candidate["proposal_digest"],
        "execution_authority": "none",
    }


def _resolve_owner_meeting_and_proposal(
    *,
    meeting_id: str,
    proposal_id: str,
    tenant_id: str,
    operator_id: str,
    user_id: str,
    command_proposal: Optional[Mapping[str, Any]] = None,
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    client = CommitteeOwnerClient(tenant_id, operator_id, user_id=user_id)
    meeting: Optional[dict[str, Any]] = None
    matched_proposal: Optional[dict[str, Any]] = None

    if client.client.is_configured():
        try:
            meeting = client.meeting(meeting_id)
        except HTTPException as exc:
            if exc.status_code != 404 or command_proposal is None:
                raise
        if isinstance(meeting, Mapping):
            synthesis = meeting.get("synthesis") if isinstance(meeting.get("synthesis"), Mapping) else {}
            for raw in list(synthesis.get("proposals") or meeting.get("proposals") or []):
                if isinstance(raw, Mapping) and str(raw.get("proposal_id") or raw.get("id") or "") == proposal_id:
                    matched_proposal = dict(raw)
                    break
        if matched_proposal is None:
            try:
                owner_proposals = client.proposals(meeting_id=meeting_id)
                for raw in owner_proposals:
                    if isinstance(raw, Mapping) and str(raw.get("proposal_id") or raw.get("id") or "") == proposal_id:
                        matched_proposal = dict(raw)
                        break
            except HTTPException:
                if meeting is None and command_proposal is None:
                    raise

    if matched_proposal is None:
        if isinstance(command_proposal, Mapping):
            matched_proposal = dict(command_proposal)
        else:
            raise HTTPException(404, detail=f"meeting proposal {proposal_id!r} not found in meeting {meeting_id!r}")

    if isinstance(command_proposal, Mapping):
        merged = dict(command_proposal)
        merged.update(matched_proposal)
        if "payload" in command_proposal and "payload" not in matched_proposal:
            merged["payload"] = command_proposal["payload"]
        matched_proposal = merged

    matched_proposal["proposal_id"] = proposal_id
    matched_proposal["meeting_id"] = meeting_id
    return meeting, matched_proposal


def create_meeting_candidate_decision_router(
    *,
    service: CandidateDecisionService,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    get_read_store: Optional[Callable[[], Any]] = None,
) -> APIRouter:
    """Create the sub-router that turns unsponsored meeting proposals into candidate decisions."""
    router = APIRouter(tags=["agora-candidate-decisions"])

    def scope(auth: Optional[str], tenant: Optional[str], *, write: bool = False) -> Any:
        identity = extract_identity(auth)
        require_read_role(identity)
        if write:
            require_write_role(identity)
        try:
            resolved = resolve_agora_user_scope(
                identity, utc_now=utc_now, requested_tenant_id=tenant
            )
        except AgoraScopeResolutionError as exc:
            from services.control_plane.bff.models import ErrorCode

            code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
            raise bff_error(exc.status_code, code, exc.message, exc.reason) from exc
        if "agora.workshop.v1" not in resolved.granted_capabilities:
            from services.control_plane.bff.models import ErrorCode

            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Agora candidate decision capability denied",
                "capability_missing",
            )
        return resolved

    def require_idempotency(value: Optional[str]) -> str:
        if not value or not value.strip():
            raise HTTPException(400, detail="Idempotency-Key header is required")
        return value.strip()

    def envelope(readback_data: dict[str, Any]) -> dict[str, Any]:
        return {
            "data": readback_data,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.persona.candidate.v1.9",
                "spec_version": "1.9",
            },
        }

    def readback(proposal_id: str, resolved: Any, response: Response) -> dict[str, Any]:
        try:
            value = service.readback(
                proposal_id=proposal_id,
                tenant_id=resolved.tenant_id,
                owner_user_id=resolved.user_id,
            )
        except CandidateDecisionConflict as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        response.headers["ETag"] = value["etag"]
        return envelope(value)

    @router.post(
        "/bff/agora/meetings/{meeting_id}/proposals/{proposal_id}/candidates",
        status_code=201,
    )
    @router.post(
        "/bff/agora/committee/meetings/{meeting_id}/proposals/{proposal_id}/candidates",
        status_code=201,
    )
    def create_candidate_from_meeting(
        meeting_id: str,
        proposal_id: str,
        response: Response,
        body: Optional[CandidateFromMeetingProposalCommand] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        resolved = scope(authorization, x_tenant_id, write=True)
        idem_key = require_idempotency(idempotency_key)
        if body is not None:
            if body.meeting_id and body.meeting_id != meeting_id:
                raise HTTPException(409, detail="candidate path and body meeting_id mismatch")
            if body.proposal_id and body.proposal_id != proposal_id:
                raise HTTPException(409, detail="candidate path and body proposal_id mismatch")
        meeting, proposal = _resolve_owner_meeting_and_proposal(
            meeting_id=meeting_id,
            proposal_id=proposal_id,
            tenant_id=resolved.tenant_id,
            operator_id=resolved.operator_id,
            user_id=resolved.user_id,
            command_proposal=body.proposal if body else None,
        )
        read_store = get_read_store() if callable(get_read_store) else None
        try:
            result = create_candidate_from_meeting_proposal(
                service=service,
                meeting_id=meeting_id,
                proposal=proposal,
                tenant_id=resolved.tenant_id,
                owner_user_id=resolved.user_id,
                proposer_id=resolved.operator_id,
                idempotency_key=idem_key,
                meeting=meeting,
                read_store=read_store,
            )
        except CandidateDecisionConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        if result.replayed:
            response.status_code = 200
        return readback(result.resource["proposal_id"], resolved, response)

    @router.post(
        "/bff/agora/meetings/{meeting_id}/candidate-decisions",
        status_code=201,
    )
    def route_unsponsored_meeting_proposals(
        meeting_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        resolved = scope(authorization, x_tenant_id, write=True)
        idem_key = require_idempotency(idempotency_key)
        client = CommitteeOwnerClient(
            resolved.tenant_id,
            resolved.operator_id,
            user_id=resolved.user_id,
        )
        meeting = client.meeting(meeting_id)
        read_store = get_read_store() if callable(get_read_store) else None
        dispatched = route_meeting_candidate_decisions(
            meeting,
            service=service,
            tenant_id=resolved.tenant_id,
            owner_user_id=resolved.user_id,
            proposer_id=resolved.operator_id,
            meeting_id=meeting_id,
            read_store=read_store,
            idempotency_prefix=idem_key,
        )
        if dispatched and all(item.get("replayed") for item in dispatched):
            response.status_code = 200
        return {
            "data": {
                "meeting_id": meeting_id,
                "items": dispatched,
                "execution_authority": "none",
                "environment_ceiling": "paper",
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.persona.candidate.v1.9",
                "spec_version": "1.9",
            },
        }

    return router


def create_candidate_decision_router(
    *,
    service: CandidateDecisionService,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    get_read_store: Optional[Callable[[], Any]] = None,
) -> APIRouter:
    """Create the complete candidate decision router (base routes + meeting proposal routes)."""
    router = _create_base_candidate_decision_router(
        service=service,
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        require_write_role=require_write_role,
        bff_error=bff_error,
        utc_now=utc_now,
    )
    router.include_router(
        create_meeting_candidate_decision_router(
            service=service,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_write_role=require_write_role,
            bff_error=bff_error,
            utc_now=utc_now,
            get_read_store=get_read_store,
        )
    )
    return router


__all__ = [
    "CandidateBindingValidationAdapter",
    "CandidateDecisionCommand",
    "CandidateDecisionConflict",
    "CandidateDecisionService",
    "CandidateDecisionStore",
    "CandidateFromMeasureCommand",
    "CandidateFromMeetingProposalCommand",
    "CandidateValidationCommand",
    "FormalApprovalLinkCommand",
    "ReadStoreApprovalAdapter",
    "build_candidate_from_meeting_proposal",
    "create_candidate_decision_router",
    "create_candidate_from_meeting_proposal",
    "create_meeting_candidate_decision_router",
    "dispatch_agora_candidate_decision",
    "is_trading_persona_sponsor",
    "read_downstream_status",
    "route_meeting_candidate_decisions",
]
