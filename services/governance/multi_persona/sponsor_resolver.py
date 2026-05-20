"""Sponsor resolver for multi-persona governance.

The resolver consumes MGMT-SYN proposal snapshots and an already-produced
AllocationPolicyArtifact. It does not run allocation synthesis; it validates
the sponsor boundary and produces a governance conflict-resolution log.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[3]
_OPTIMIZER_DIR = _REPO_ROOT / "services" / "optimizer-svc"
if str(_OPTIMIZER_DIR) not in sys.path:
    sys.path.insert(0, str(_OPTIMIZER_DIR))

from allocation_aggregation import PersonaAllocationProposalJsonlStore  # noqa: E402
from portfolio_synthesis import (  # noqa: E402
    AllocationConflictReport,
    AllocationPolicyArtifact,
    ConflictResolutionLog as MgmtSynConflictResolutionLog,
    PersonaAllocationProposal,
    SynthesisError,
    classify_allocation_conflicts,
)

from .conflict_resolution_log import (
    ClassifiedConflict,
    ConflictResolutionLog,
    ConflictVetoRecord,
    OpenConflict,
)


class SponsorResolverError(ValueError):
    """Raised when a sponsor resolution request is invalid or unresolved."""


@dataclass(frozen=True)
class SponsorResolvedProposal:
    """Sponsor-resolved governance view of one MGMT-SYN artifact."""

    allocation_policy_artifact: AllocationPolicyArtifact
    conflict_resolution_log: ConflictResolutionLog
    conflict_report: AllocationConflictReport

    @property
    def sponsor_persona_id(self) -> str:
        return self.allocation_policy_artifact.sponsor_persona_id

    @property
    def proposal_ids(self) -> tuple[str, ...]:
        return self.conflict_resolution_log.proposal_ids

    @property
    def has_open_conflicts(self) -> bool:
        return self.conflict_resolution_log.has_open_conflicts

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocation_policy_artifact": self.allocation_policy_artifact.to_dict(),
            "sponsor_persona_id": self.sponsor_persona_id,
            "proposal_ids": list(self.proposal_ids),
            "conflict_resolution_log": self.conflict_resolution_log.to_dict(),
            "conflict_report": self.conflict_report.to_dict(),
        }


class SponsorResolver:
    """Resolve sponsor governance for a MGMT-SYN AllocationPolicyArtifact."""

    def __init__(self, store: PersonaAllocationProposalJsonlStore | None = None) -> None:
        self._store = store or PersonaAllocationProposalJsonlStore()

    def resolve(
        self,
        allocation_policy_artifact: AllocationPolicyArtifact | Mapping[str, Any],
        *,
        proposal_ids: Sequence[str] | None = None,
        source_conflict_resolution_log: MgmtSynConflictResolutionLog | Mapping[str, Any] | None = None,
        resolved_conflict_ids: Sequence[str] = (),
        conflict_evidence_ref: str | None = None,
    ) -> SponsorResolvedProposal:
        """Validate sponsor resolution and produce a governance log.

        ``allocation_policy_artifact`` is the MGMT-SYN-005 output. Proposal
        snapshots are replayed from the MGMT-SYN store, then classified using
        the existing conflict classifier.
        """

        artifact = _normalize_artifact(allocation_policy_artifact)
        proposal_id_list = _proposal_ids_from_artifact(artifact, proposal_ids)
        proposals = self._store.require_proposals(proposal_id_list)
        _validate_sponsor_persona(artifact, proposals)

        conflict_report = classify_allocation_conflicts(
            proposals,
            capital_pool_id=artifact.capital_pool_id,
            scope_ref=artifact.scope_ref,
        )
        source_log = _normalize_source_log(source_conflict_resolution_log)
        log = _build_conflict_resolution_log(
            artifact=artifact,
            proposals=proposals,
            conflict_report=conflict_report,
            source_log=source_log,
            resolved_conflict_ids=resolved_conflict_ids,
            conflict_evidence_ref=conflict_evidence_ref,
        )
        return SponsorResolvedProposal(
            allocation_policy_artifact=artifact,
            conflict_resolution_log=log,
            conflict_report=conflict_report,
        )


def resolve_sponsor(
    allocation_policy_artifact: AllocationPolicyArtifact | Mapping[str, Any],
    *,
    proposal_ids: Sequence[str] | None = None,
    store: PersonaAllocationProposalJsonlStore | None = None,
    source_conflict_resolution_log: MgmtSynConflictResolutionLog | Mapping[str, Any] | None = None,
    resolved_conflict_ids: Sequence[str] = (),
    conflict_evidence_ref: str | None = None,
) -> SponsorResolvedProposal:
    """Convenience wrapper for one sponsor resolution run."""

    return SponsorResolver(store=store).resolve(
        allocation_policy_artifact,
        proposal_ids=proposal_ids,
        source_conflict_resolution_log=source_conflict_resolution_log,
        resolved_conflict_ids=resolved_conflict_ids,
        conflict_evidence_ref=conflict_evidence_ref,
    )


def _normalize_artifact(
    artifact: AllocationPolicyArtifact | Mapping[str, Any],
) -> AllocationPolicyArtifact:
    if isinstance(artifact, AllocationPolicyArtifact):
        return artifact
    if not isinstance(artifact, Mapping):
        raise SponsorResolverError("allocation_policy_artifact must be an object")
    try:
        return AllocationPolicyArtifact(**dict(artifact))
    except (SynthesisError, TypeError) as exc:
        raise SponsorResolverError(f"invalid allocation_policy_artifact: {exc}") from exc


def _proposal_ids_from_artifact(
    artifact: AllocationPolicyArtifact,
    proposal_ids: Sequence[str] | None,
) -> list[str]:
    raw_ids = proposal_ids if proposal_ids is not None else artifact.provenance_refs
    if isinstance(raw_ids, (str, bytes)) or not isinstance(raw_ids, Sequence):
        raise SponsorResolverError("proposal_ids must be a sequence of proposal id strings")
    normalized = [_require_text(proposal_id, "proposal_id") for proposal_id in raw_ids]
    if not normalized:
        raise SponsorResolverError("proposal_ids must contain at least one proposal id")
    if len(set(normalized)) != len(normalized):
        raise SponsorResolverError("proposal_ids entries must be unique")
    artifact_refs = list(artifact.provenance_refs)
    if normalized != artifact_refs:
        raise SponsorResolverError(
            "proposal_ids must match allocation_policy_artifact.provenance_refs in order"
        )
    return normalized


def _validate_sponsor_persona(
    artifact: AllocationPolicyArtifact,
    proposals: Sequence[PersonaAllocationProposal],
) -> None:
    sponsor = _require_text(artifact.sponsor_persona_id, "sponsor_persona_id")
    proposal_personas = {proposal.persona_id for proposal in proposals}
    if sponsor not in proposal_personas:
        raise SponsorResolverError(
            f"sponsor_persona_id {sponsor!r} is not present in proposal personas: {sorted(proposal_personas)}"
        )


def _normalize_source_log(
    source_log: MgmtSynConflictResolutionLog | Mapping[str, Any] | None,
) -> MgmtSynConflictResolutionLog | None:
    if source_log is None:
        return None
    if isinstance(source_log, MgmtSynConflictResolutionLog):
        return source_log
    if not isinstance(source_log, Mapping):
        raise SponsorResolverError("source_conflict_resolution_log must be an object")
    try:
        return MgmtSynConflictResolutionLog(
            log_id=_require_text(source_log.get("log_id"), "source_conflict_resolution_log.log_id"),
            capital_pool_id=_require_text(
                source_log.get("capital_pool_id"),
                "source_conflict_resolution_log.capital_pool_id",
            ),
            scope_ref=_require_text(source_log.get("scope_ref"), "source_conflict_resolution_log.scope_ref"),
            timestamp=_require_text(source_log.get("timestamp"), "source_conflict_resolution_log.timestamp"),
            proposal_ids=list(source_log.get("proposal_ids", [])),
            vetoed_proposals=list(source_log.get("vetoed_proposals", [])),
            weighting_inputs=dict(source_log.get("weighting_inputs", {})),
            weighting_outputs=dict(source_log.get("weighting_outputs", {})),
            committee_ref=source_log.get("committee_ref"),
            sponsor_persona_id=source_log.get("sponsor_persona_id"),
            rejected_reason=source_log.get("rejected_reason"),
            synthesis_method=source_log.get("synthesis_method"),
        )
    except TypeError as exc:
        raise SponsorResolverError(f"invalid source_conflict_resolution_log: {exc}") from exc


def _build_conflict_resolution_log(
    *,
    artifact: AllocationPolicyArtifact,
    proposals: Sequence[PersonaAllocationProposal],
    conflict_report: AllocationConflictReport,
    source_log: MgmtSynConflictResolutionLog | None,
    resolved_conflict_ids: Sequence[str],
    conflict_evidence_ref: str | None,
) -> ConflictResolutionLog:
    resolved_ids = {_require_text(item, "resolved_conflict_ids[]") for item in resolved_conflict_ids}
    classified_conflicts = tuple(
        ClassifiedConflict(
            conflict_id=conflict.conflict_id,
            conflict_type=conflict.conflict_type,
            severity=conflict.severity,
            proposal_ids=tuple(conflict.proposal_ids),
            summary=conflict.summary,
            target_ref=conflict.target_ref,
            committee_trigger=conflict.committee_trigger,
            details=dict(conflict.details),
        )
        for conflict in conflict_report.conflicts
    )
    committee_ref = source_log.committee_ref if source_log else None
    open_conflicts = _open_conflicts_from_report(
        classified_conflicts,
        resolved_conflict_ids=resolved_ids,
        has_committee_resolution=bool(committee_ref),
        evidence_ref=conflict_evidence_ref,
    )

    return ConflictResolutionLog(
        log_id=artifact.conflict_resolution_log_id,
        capital_pool_id=artifact.capital_pool_id,
        scope_ref=artifact.scope_ref,
        timestamp=(source_log.timestamp if source_log else artifact.created_at),
        proposal_ids=tuple(proposal.proposal_id for proposal in proposals),
        sponsor_persona_id=artifact.sponsor_persona_id,
        artifact_id=artifact.artifact_id,
        synthesis_method=artifact.synthesis_method,
        vetoed_proposals=_vetoed_proposals(source_log),
        weighting_inputs=_weighting_inputs(proposals, source_log),
        weighting_outputs=_weighting_outputs(proposals, source_log),
        classified_conflicts=classified_conflicts,
        committee_ref=committee_ref,
        open_conflicts=open_conflicts,
        rejected_reason=source_log.rejected_reason if source_log else None,
        source_conflict_resolution_log_id=source_log.log_id if source_log else None,
    )


def _open_conflicts_from_report(
    classified_conflicts: Sequence[ClassifiedConflict],
    *,
    resolved_conflict_ids: set[str],
    has_committee_resolution: bool,
    evidence_ref: str | None,
) -> tuple[OpenConflict, ...]:
    if has_committee_resolution:
        return ()
    open_conflicts: list[OpenConflict] = []
    for conflict in classified_conflicts:
        if conflict.conflict_id in resolved_conflict_ids:
            continue
        if conflict.severity != "committee" and not conflict.committee_trigger:
            continue
        open_conflicts.append(
            OpenConflict(
                conflict_id=conflict.conflict_id,
                summary=conflict.summary,
                evidence_ref=evidence_ref,
            )
        )
    return tuple(open_conflicts)


def _vetoed_proposals(
    source_log: MgmtSynConflictResolutionLog | None,
) -> tuple[ConflictVetoRecord, ...]:
    if source_log is None:
        return ()
    vetoed: list[ConflictVetoRecord] = []
    for record in source_log.vetoed_proposals:
        if isinstance(record, Mapping):
            payload = record
            vetoed.append(
                ConflictVetoRecord(
                    proposal_id=_require_text(payload.get("proposal_id"), "vetoed_proposals[].proposal_id"),
                    persona_id=_require_text(payload.get("persona_id"), "vetoed_proposals[].persona_id"),
                    reason=_require_text(payload.get("reason"), "vetoed_proposals[].reason"),
                    detail=_optional_text(payload.get("detail")),
                )
            )
            continue
        vetoed.append(
            ConflictVetoRecord(
                proposal_id=record.proposal_id,
                persona_id=record.persona_id,
                reason=record.reason,
                detail=record.detail or None,
            )
        )
    return tuple(vetoed)


def _weighting_inputs(
    proposals: Sequence[PersonaAllocationProposal],
    source_log: MgmtSynConflictResolutionLog | None,
) -> dict[str, float]:
    if source_log is not None:
        return {str(key): float(value) for key, value in source_log.weighting_inputs.items()}
    return {proposal.proposal_id: proposal.effective_weight for proposal in proposals}


def _weighting_outputs(
    proposals: Sequence[PersonaAllocationProposal],
    source_log: MgmtSynConflictResolutionLog | None,
) -> dict[str, float]:
    if source_log is not None:
        return {str(key): float(value) for key, value in source_log.weighting_outputs.items()}
    raw_weights = {proposal.proposal_id: proposal.effective_weight for proposal in proposals}
    total = sum(raw_weights.values())
    if total == 0:
        equal = 1.0 / len(proposals)
        return {proposal.proposal_id: equal for proposal in proposals}
    return {proposal_id: weight / total for proposal_id, weight in raw_weights.items()}


def _require_text(value: Any, field_name: str) -> str:
    if value is None:
        raise SponsorResolverError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise SponsorResolverError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "SponsorResolvedProposal",
    "SponsorResolver",
    "SponsorResolverError",
    "resolve_sponsor",
]
