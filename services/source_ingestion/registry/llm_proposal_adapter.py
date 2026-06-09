"""LLM proposal adapter for governed source-change workflow.

This adapter is the ONLY path through which LLM agents may interact with the
source registry.  It enforces design invariant §14.5:

    LLM may draft source changes, but cannot apply them directly.

All methods produce a ``draft`` proposal only.  No method may transition a
proposal to ``submitted``, ``approved``, or ``applied``.  No method may
mutate any source registry entry directly.

Operator-level actions (submit, approve, apply) live in
``SourceChangeProposalStore`` and must be called via operator-gated API
routes, not through this adapter.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .proposals import (
    ProposalRisk,
    ProposalStatus,
    ProposalType,
    ProposedSourceInfo,
    SourceChangeProposal,
    SourceChangeProposalError,
    SourceChangeProposalStore,
    SourceKind,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _agent_actor(agent_id: str, trace_id: str | None = None) -> dict[str, Any]:
    return {
        "actor_type": "agent",
        "actor_id": str(agent_id or "llm-agent").strip() or "llm-agent",
        "trace_id": trace_id,
    }


def _make_risks(risks_data: Sequence[Mapping[str, Any]]) -> list[ProposalRisk]:
    result = []
    for r in risks_data:
        if not isinstance(r, dict):
            continue
        try:
            result.append(ProposalRisk.from_dict(r))
        except SourceChangeProposalError:
            pass
    return result


class LLMSourceProposalAdapter:
    """Adapter that constrains LLM proposal creation to ``draft`` status only.

    Typical usage in a service that receives LLM-generated proposals:

        adapter = LLMSourceProposalAdapter(proposal_store)
        proposal = adapter.propose_add_data_source(
            agent_id="research-llm",
            proposed_source={...},
            rationale="FinMind covers TW daily price; we need a backup source.",
        )
        # proposal.status == ProposalStatus.DRAFT always
        # The store has persisted the draft; no registry entry was mutated.
    """

    def __init__(self, store: SourceChangeProposalStore) -> None:
        self._store = store

    def _new_draft(
        self,
        *,
        proposal_type: ProposalType,
        source_kind: SourceKind,
        rationale: str,
        agent_id: str,
        trace_id: str | None = None,
        target_source_id: str | None = None,
        proposed_source: ProposedSourceInfo | None = None,
        expected_value: Mapping[str, Any] | None = None,
        risks: Sequence[Mapping[str, Any]] | None = None,
        evidence_refs: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceChangeProposal:
        proposal_id = f"prop-{uuid.uuid4().hex[:12]}"
        proposal = SourceChangeProposal(
            proposal_id=proposal_id,
            proposal_type=proposal_type.value,
            source_kind=source_kind.value,
            rationale=rationale,
            proposed_by=_agent_actor(agent_id, trace_id),
            status=ProposalStatus.DRAFT.value,
            target_source_id=target_source_id,
            proposed_source=proposed_source,
            expected_value=dict(expected_value or {}),
            risks=_make_risks(list(risks or [])),
            evidence_refs=list(evidence_refs or []),
            metadata=dict(metadata or {}),
        )
        return self._store.create_draft(proposal)

    def propose_add_data_source(
        self,
        *,
        agent_id: str,
        proposed_source: Mapping[str, Any],
        rationale: str,
        trace_id: str | None = None,
        expected_value: Mapping[str, Any] | None = None,
        risks: Sequence[Mapping[str, Any]] | None = None,
        evidence_refs: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceChangeProposal:
        """Propose adding a new data source.  Creates a draft only."""
        proposed_source_info = ProposedSourceInfo.from_dict({
            "source_kind": SourceKind.DATA_SOURCE.value,
            **proposed_source,
        })
        return self._new_draft(
            proposal_type=ProposalType.ADD_DATA_SOURCE,
            source_kind=SourceKind.DATA_SOURCE,
            rationale=rationale,
            agent_id=agent_id,
            trace_id=trace_id,
            proposed_source=proposed_source_info,
            expected_value=expected_value,
            risks=risks,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    def propose_add_strategy_seed_source(
        self,
        *,
        agent_id: str,
        proposed_source: Mapping[str, Any],
        rationale: str,
        trace_id: str | None = None,
        expected_value: Mapping[str, Any] | None = None,
        risks: Sequence[Mapping[str, Any]] | None = None,
        evidence_refs: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceChangeProposal:
        """Propose adding a new strategy seed source.  Creates a draft only."""
        proposed_source_info = ProposedSourceInfo.from_dict({
            "source_kind": SourceKind.STRATEGY_SEED_SOURCE.value,
            **proposed_source,
        })
        return self._new_draft(
            proposal_type=ProposalType.ADD_STRATEGY_SEED_SOURCE,
            source_kind=SourceKind.STRATEGY_SEED_SOURCE,
            rationale=rationale,
            agent_id=agent_id,
            trace_id=trace_id,
            proposed_source=proposed_source_info,
            expected_value=expected_value,
            risks=risks,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    def propose_disable_source(
        self,
        *,
        agent_id: str,
        target_source_id: str,
        source_kind: str,
        rationale: str,
        trace_id: str | None = None,
        risks: Sequence[Mapping[str, Any]] | None = None,
        evidence_refs: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceChangeProposal:
        """Propose disabling an existing source.  Creates a draft only."""
        return self._new_draft(
            proposal_type=ProposalType.DISABLE_SOURCE,
            source_kind=SourceKind(source_kind),
            rationale=rationale,
            agent_id=agent_id,
            trace_id=trace_id,
            target_source_id=target_source_id,
            risks=risks,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    def propose_retire_source(
        self,
        *,
        agent_id: str,
        target_source_id: str,
        source_kind: str,
        rationale: str,
        trace_id: str | None = None,
        risks: Sequence[Mapping[str, Any]] | None = None,
        evidence_refs: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceChangeProposal:
        """Propose retiring an existing source.  Creates a draft only."""
        return self._new_draft(
            proposal_type=ProposalType.RETIRE_SOURCE,
            source_kind=SourceKind(source_kind),
            rationale=rationale,
            agent_id=agent_id,
            trace_id=trace_id,
            target_source_id=target_source_id,
            risks=risks,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    def propose_replace_source(
        self,
        *,
        agent_id: str,
        target_source_id: str,
        source_kind: str,
        rationale: str,
        replacement_source_id: str,
        trace_id: str | None = None,
        risks: Sequence[Mapping[str, Any]] | None = None,
        evidence_refs: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceChangeProposal:
        """Propose replacing one source with another.  Creates a draft only."""
        return self._new_draft(
            proposal_type=ProposalType.REPLACE_SOURCE,
            source_kind=SourceKind(source_kind),
            rationale=rationale,
            agent_id=agent_id,
            trace_id=trace_id,
            target_source_id=target_source_id,
            metadata={"replacement_source_id": replacement_source_id, **(metadata or {})},
            risks=risks,
            evidence_refs=evidence_refs,
        )

    def propose_change_schedule(
        self,
        *,
        agent_id: str,
        target_source_id: str,
        source_kind: str,
        rationale: str,
        schedule_change: Mapping[str, Any],
        trace_id: str | None = None,
        risks: Sequence[Mapping[str, Any]] | None = None,
        evidence_refs: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceChangeProposal:
        """Propose changing the ingest schedule for a source.  Creates a draft only."""
        return self._new_draft(
            proposal_type=ProposalType.CHANGE_SCHEDULE,
            source_kind=SourceKind(source_kind),
            rationale=rationale,
            agent_id=agent_id,
            trace_id=trace_id,
            target_source_id=target_source_id,
            metadata={"schedule_change": dict(schedule_change), **(metadata or {})},
            risks=risks,
            evidence_refs=evidence_refs,
        )

    def propose_request_vendor_quote(
        self,
        *,
        agent_id: str,
        source_kind: str,
        rationale: str,
        proposed_source: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        evidence_refs: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceChangeProposal:
        """Propose requesting a vendor quote for a new source.  Creates a draft only."""
        proposed_source_info = None
        if proposed_source:
            proposed_source_info = ProposedSourceInfo.from_dict({
                "source_kind": source_kind,
                **proposed_source,
            })
        return self._new_draft(
            proposal_type=ProposalType.REQUEST_VENDOR_QUOTE,
            source_kind=SourceKind(source_kind),
            rationale=rationale,
            agent_id=agent_id,
            trace_id=trace_id,
            proposed_source=proposed_source_info,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )
