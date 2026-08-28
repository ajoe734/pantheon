"""Source change proposals router for Source Ingestion.

Covers listing, creating, drafting (LLM adapter), approving, rejecting, applying,
and retiring source change proposals.
"""

from __future__ import annotations

import re
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from ..api_models import (
    ApplyProposalRequest,
    CreateProposalRequest,
    LLMProposalRequest,
)
from ..registry.proposals import (
    ProposalRisk,
    ProposalStatus,
    ProposalType,
    ProposedSourceInfo,
    SourceChangeProposal,
    SourceChangeProposalError,
    SourceKind,
)

if TYPE_CHECKING:
    from ..runtime import SourceIngestionRuntime


def create_proposals_router(runtime: SourceIngestionRuntime) -> APIRouter:
    router = APIRouter(tags=["source-change-proposals"])

    @router.get("/api/source-change-proposals")
    def list_proposals(
        status: str | None = None,
        proposal_type: str | None = None,
        source_kind: str | None = None,
    ) -> dict[str, Any]:
        try:
            proposals = runtime.proposal_store.list(
                status=status if status else None,
                proposal_type=proposal_type if proposal_type else None,
                source_kind=source_kind if source_kind else None,
            )
        except (SourceChangeProposalError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"proposals": [runtime._proposal_to_response(p) for p in proposals]}

    @router.post("/api/source-change-proposals", status_code=201)
    def create_proposal(request: CreateProposalRequest) -> dict[str, Any]:
        """Create a new source-change proposal (draft only).

        Operator or LLM callers that need to enforce the draft-only restriction
        should use /api/source-change-proposals/llm-draft instead.
        """
        try:
            proposed_source = None
            if request.proposed_source is not None:
                proposed_source = ProposedSourceInfo.from_dict(request.proposed_source.model_dump())
            risks = [
                ProposalRisk(risk_type=r.risk_type, severity=r.severity, note=r.note)
                for r in request.risks
            ]
            proposal = SourceChangeProposal(
                proposal_id=f"prop-{re.sub(r'[^a-z0-9]', '-', request.rationale[:20].lower())}-{sha256(request.rationale.encode()).hexdigest()[:8]}",
                proposal_type=request.proposal_type,
                source_kind=request.source_kind,
                rationale=request.rationale,
                proposed_by=request.proposed_by,
                status=ProposalStatus.DRAFT.value,
                target_source_id=request.target_source_id,
                proposed_source=proposed_source,
                expected_value=request.expected_value,
                risks=risks,
                evidence_refs=request.evidence_refs,
                metadata=request.metadata,
            )
            created = runtime.proposal_store.create_draft(proposal)
            return runtime._proposal_to_response(created)
        except SourceChangeProposalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/source-change-proposals/llm-draft", status_code=201)
    def create_llm_draft_proposal(request: LLMProposalRequest) -> dict[str, Any]:
        """LLM-originated proposal. The adapter enforces draft-only status."""
        try:
            pt = ProposalType(request.proposal_type)
            sk = SourceKind(request.source_kind)
            proposed_source_data = request.proposed_source.model_dump() if request.proposed_source else None
            risks_data = [r.model_dump() for r in request.risks]
            if pt == ProposalType.ADD_DATA_SOURCE:
                proposal = runtime.llm_proposal_adapter.propose_add_data_source(
                    agent_id=request.agent_id,
                    proposed_source=proposed_source_data or {},
                    rationale=request.rationale,
                    trace_id=request.trace_id,
                    expected_value=request.expected_value or None,
                    risks=risks_data or None,
                    evidence_refs=request.evidence_refs or None,
                    metadata=request.metadata or None,
                )
            elif pt == ProposalType.ADD_STRATEGY_SEED_SOURCE:
                proposal = runtime.llm_proposal_adapter.propose_add_strategy_seed_source(
                    agent_id=request.agent_id,
                    proposed_source=proposed_source_data or {},
                    rationale=request.rationale,
                    trace_id=request.trace_id,
                    expected_value=request.expected_value or None,
                    risks=risks_data or None,
                    evidence_refs=request.evidence_refs or None,
                    metadata=request.metadata or None,
                )
            elif pt == ProposalType.DISABLE_SOURCE:
                proposal = runtime.llm_proposal_adapter.propose_disable_source(
                    agent_id=request.agent_id,
                    target_source_id=request.target_source_id or "",
                    source_kind=sk.value,
                    rationale=request.rationale,
                    trace_id=request.trace_id,
                    risks=risks_data or None,
                    evidence_refs=request.evidence_refs or None,
                    metadata=request.metadata or None,
                )
            elif pt == ProposalType.RETIRE_SOURCE:
                proposal = runtime.llm_proposal_adapter.propose_retire_source(
                    agent_id=request.agent_id,
                    target_source_id=request.target_source_id or "",
                    source_kind=sk.value,
                    rationale=request.rationale,
                    trace_id=request.trace_id,
                    risks=risks_data or None,
                    evidence_refs=request.evidence_refs or None,
                    metadata=request.metadata or None,
                )
            elif pt == ProposalType.REPLACE_SOURCE:
                replacement_id = str(request.metadata.get("replacement_source_id") or "")
                proposal = runtime.llm_proposal_adapter.propose_replace_source(
                    agent_id=request.agent_id,
                    target_source_id=request.target_source_id or "",
                    source_kind=sk.value,
                    rationale=request.rationale,
                    replacement_source_id=replacement_id,
                    trace_id=request.trace_id,
                    risks=risks_data or None,
                    evidence_refs=request.evidence_refs or None,
                    metadata={k: v for k, v in request.metadata.items() if k != "replacement_source_id"},
                )
            elif pt == ProposalType.CHANGE_SCHEDULE:
                schedule_change = dict(request.metadata.get("schedule_change") or {})
                proposal = runtime.llm_proposal_adapter.propose_change_schedule(
                    agent_id=request.agent_id,
                    target_source_id=request.target_source_id or "",
                    source_kind=sk.value,
                    rationale=request.rationale,
                    schedule_change=schedule_change,
                    trace_id=request.trace_id,
                    risks=risks_data or None,
                    evidence_refs=request.evidence_refs or None,
                    metadata={k: v for k, v in request.metadata.items() if k != "schedule_change"},
                )
            elif pt == ProposalType.REQUEST_VENDOR_QUOTE:
                proposal = runtime.llm_proposal_adapter.propose_request_vendor_quote(
                    agent_id=request.agent_id,
                    source_kind=sk.value,
                    rationale=request.rationale,
                    proposed_source=proposed_source_data,
                    trace_id=request.trace_id,
                    evidence_refs=request.evidence_refs or None,
                    metadata=request.metadata or None,
                )
            else:
                raise SourceChangeProposalError(f"Unsupported proposal_type for LLM draft: {pt.value}")
            return runtime._proposal_to_response(proposal)
        except SourceChangeProposalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/source-change-proposals/{proposal_id}")
    def get_proposal(proposal_id: str) -> dict[str, Any]:
        proposal = runtime.proposal_store.get(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="proposal not found")
        return runtime._proposal_to_response(proposal)

    @router.post("/api/source-change-proposals/{proposal_id}/actions/submit")
    def submit_proposal(proposal_id: str) -> dict[str, Any]:
        try:
            return runtime._proposal_to_response(runtime.proposal_store.submit(proposal_id))
        except SourceChangeProposalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/source-change-proposals/{proposal_id}/actions/approve")
    def approve_proposal(proposal_id: str) -> dict[str, Any]:
        try:
            return runtime._proposal_to_response(runtime.proposal_store.approve(proposal_id))
        except SourceChangeProposalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/source-change-proposals/{proposal_id}/actions/reject")
    def reject_proposal(proposal_id: str) -> dict[str, Any]:
        try:
            return runtime._proposal_to_response(runtime.proposal_store.reject(proposal_id))
        except SourceChangeProposalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/source-change-proposals/{proposal_id}/actions/apply")
    def apply_proposal(proposal_id: str, request: ApplyProposalRequest | None = None) -> dict[str, Any]:
        try:
            change_ref = request.change_ref if request else None
            return runtime._proposal_to_response(runtime.proposal_store.apply(proposal_id, change_ref=change_ref))
        except SourceChangeProposalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/source-change-proposals/{proposal_id}/actions/retire")
    def retire_proposal(proposal_id: str) -> dict[str, Any]:
        try:
            return runtime._proposal_to_response(runtime.proposal_store.retire(proposal_id))
        except SourceChangeProposalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
