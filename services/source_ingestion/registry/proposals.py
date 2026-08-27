"""SourceChangeProposal model and store for governed LLM source-change workflow.

LLM agents may only create ``draft`` proposals.  No agent may apply a proposal
directly to the source registry.  The lifecycle is:

    draft → submitted → approved → applied
                     ↘ rejected
    draft → retired (withdrawn before submit)

Only an operator-approved action may transition a proposal from ``approved``
to ``applied``, and only then may the referenced source registry be mutated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .jsonl_store import JsonlRegistryStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require(value: Any, name: str) -> str:
    s = str(value or "").strip()
    if not s:
        raise SourceChangeProposalError(f"{name} is required")
    return s


class SourceChangeProposalError(ValueError):
    """Raised when SourceChangeProposal invariants are violated."""


class ProposalType(str, Enum):
    ADD_DATA_SOURCE = "add_data_source"
    ADD_STRATEGY_SEED_SOURCE = "add_strategy_seed_source"
    DISABLE_SOURCE = "disable_source"
    RETIRE_SOURCE = "retire_source"
    REPLACE_SOURCE = "replace_source"
    CHANGE_SCHEDULE = "change_schedule"
    CHANGE_UNIVERSE_POLICY = "change_universe_policy"
    REQUEST_VENDOR_QUOTE = "request_vendor_quote"


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    RETIRED = "retired"


class SourceKind(str, Enum):
    DATA_SOURCE = "data_source"
    STRATEGY_SEED_SOURCE = "strategy_seed_source"


class RiskType(str, Enum):
    LICENSE = "license"
    COST = "cost"
    QUALITY = "quality"
    STABILITY = "stability"
    PRIVACY = "privacy"
    OPERATIONAL = "operational"


class RiskSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_SCHEMA_VERSION = "source_change_proposal.v1"

_ADD_TYPES = frozenset({ProposalType.ADD_DATA_SOURCE, ProposalType.ADD_STRATEGY_SEED_SOURCE})
_TARGET_REQUIRED_TYPES = frozenset({
    ProposalType.DISABLE_SOURCE,
    ProposalType.RETIRE_SOURCE,
    ProposalType.REPLACE_SOURCE,
    ProposalType.CHANGE_SCHEDULE,
    ProposalType.CHANGE_UNIVERSE_POLICY,
})

_VALID_STATUS_TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.DRAFT: frozenset({ProposalStatus.SUBMITTED, ProposalStatus.RETIRED}),
    ProposalStatus.SUBMITTED: frozenset({ProposalStatus.APPROVED, ProposalStatus.REJECTED}),
    ProposalStatus.APPROVED: frozenset({ProposalStatus.APPLIED}),
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.APPLIED: frozenset(),
    ProposalStatus.RETIRED: frozenset(),
}


@dataclass(frozen=True)
class ProposedSourceInfo:
    """Structured description of the proposed source (for add/replace proposals)."""

    source_id: str
    source_kind: str
    provider: str
    source_class: str
    license_scope: str
    allowed_use: Sequence[str]
    homepage_url: str | None = None
    docs_url: str | None = None
    entitlement_required: bool = False
    entitlement_tags: Sequence[str] = field(default_factory=tuple)
    expected_datasets: Sequence[str] = field(default_factory=tuple)
    update_frequency: str | None = None
    cost_notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _require(self.source_id, "proposed_source.source_id"))
        object.__setattr__(self, "provider", _require(self.provider, "proposed_source.provider"))
        object.__setattr__(self, "source_class", _require(self.source_class, "proposed_source.source_class"))
        object.__setattr__(self, "license_scope", _require(self.license_scope, "proposed_source.license_scope"))
        allowed_use = tuple(str(u).strip() for u in self.allowed_use if str(u).strip())
        if not allowed_use:
            raise SourceChangeProposalError("proposed_source.allowed_use must not be empty")
        object.__setattr__(self, "allowed_use", allowed_use)
        object.__setattr__(self, "entitlement_tags", tuple(
            str(t).strip() for t in self.entitlement_tags if str(t).strip()
        ))
        object.__setattr__(self, "expected_datasets", tuple(
            str(d).strip() for d in self.expected_datasets if str(d).strip()
        ))
        try:
            SourceKind(self.source_kind.value if isinstance(self.source_kind, SourceKind) else str(self.source_kind))
        except ValueError:
            allowed = ", ".join(k.value for k in SourceKind)
            raise SourceChangeProposalError(f"proposed_source.source_kind must be one of: {allowed}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "provider": self.provider,
            "source_class": self.source_class,
            "homepage_url": self.homepage_url,
            "docs_url": self.docs_url,
            "license_scope": self.license_scope,
            "entitlement_required": self.entitlement_required,
            "entitlement_tags": list(self.entitlement_tags),
            "allowed_use": list(self.allowed_use),
            "expected_datasets": list(self.expected_datasets),
            "update_frequency": self.update_frequency,
            "cost_notes": self.cost_notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposedSourceInfo":
        return cls(
            source_id=str(data["source_id"]),
            source_kind=str(data["source_kind"]),
            provider=str(data["provider"]),
            source_class=str(data["source_class"]),
            license_scope=str(data["license_scope"]),
            allowed_use=list(data.get("allowed_use") or []),
            homepage_url=data.get("homepage_url"),
            docs_url=data.get("docs_url"),
            entitlement_required=bool(data.get("entitlement_required", False)),
            entitlement_tags=list(data.get("entitlement_tags") or []),
            expected_datasets=list(data.get("expected_datasets") or []),
            update_frequency=data.get("update_frequency"),
            cost_notes=data.get("cost_notes"),
        )


@dataclass(frozen=True)
class ProposalRisk:
    risk_type: str
    severity: str
    note: str

    def __post_init__(self) -> None:
        try:
            RiskType(self.risk_type.value if isinstance(self.risk_type, RiskType) else str(self.risk_type))
        except ValueError:
            allowed = ", ".join(r.value for r in RiskType)
            raise SourceChangeProposalError(f"risk_type must be one of: {allowed}")
        try:
            RiskSeverity(self.severity.value if isinstance(self.severity, RiskSeverity) else str(self.severity))
        except ValueError:
            allowed = ", ".join(s.value for s in RiskSeverity)
            raise SourceChangeProposalError(f"severity must be one of: {allowed}")
        if not str(self.note or "").strip():
            raise SourceChangeProposalError("risk note must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {"risk_type": self.risk_type, "severity": self.severity, "note": self.note}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalRisk":
        return cls(
            risk_type=str(data["risk_type"]),
            severity=str(data["severity"]),
            note=str(data["note"]),
        )


@dataclass(frozen=True)
class SourceChangeProposal:
    """Immutable governed proposal for a source registry change.

    An LLM or service may create a ``draft`` proposal.  Only an operator-
    approved action may advance the lifecycle through ``submitted`` →
    ``approved`` → ``applied``.  A proposal in any terminal state
    (``applied``, ``rejected``, ``retired``) cannot be mutated further.
    """

    proposal_id: str
    proposal_type: ProposalType | str
    source_kind: SourceKind | str
    rationale: str
    proposed_by: Mapping[str, Any]
    status: ProposalStatus | str = ProposalStatus.DRAFT
    target_source_id: str | None = None
    proposed_source: ProposedSourceInfo | None = None
    expected_value: Mapping[str, Any] = field(default_factory=dict)
    risks: Sequence[ProposalRisk] = field(default_factory=tuple)
    evidence_refs: Sequence[str] = field(default_factory=tuple)
    payload: Mapping[str, Any] = field(default_factory=dict)
    lineage: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _require(self.proposal_id, "proposal_id"))
        object.__setattr__(self, "rationale", _require(self.rationale, "rationale"))

        try:
            pt = ProposalType(self.proposal_type.value if isinstance(self.proposal_type, ProposalType) else str(self.proposal_type))
        except ValueError:
            allowed = ", ".join(t.value for t in ProposalType)
            raise SourceChangeProposalError(f"proposal_type must be one of: {allowed}")
        object.__setattr__(self, "proposal_type", pt)

        try:
            sk = SourceKind(self.source_kind.value if isinstance(self.source_kind, SourceKind) else str(self.source_kind))
        except ValueError:
            allowed = ", ".join(k.value for k in SourceKind)
            raise SourceChangeProposalError(f"source_kind must be one of: {allowed}")
        object.__setattr__(self, "source_kind", sk)

        try:
            ps = ProposalStatus(self.status.value if isinstance(self.status, ProposalStatus) else str(self.status))
        except ValueError:
            allowed = ", ".join(s.value for s in ProposalStatus)
            raise SourceChangeProposalError(f"status must be one of: {allowed}")
        object.__setattr__(self, "status", ps)

        proposed_by = dict(self.proposed_by)
        if not str(proposed_by.get("actor_id") or "").strip():
            raise SourceChangeProposalError("proposed_by.actor_id is required")
        if not str(proposed_by.get("actor_type") or "").strip():
            raise SourceChangeProposalError("proposed_by.actor_type is required")
        if proposed_by.get("actor_type") not in ("human", "agent", "service"):
            raise SourceChangeProposalError("proposed_by.actor_type must be human, agent, or service")
        object.__setattr__(self, "proposed_by", proposed_by)

        if pt in _TARGET_REQUIRED_TYPES and not str(self.target_source_id or "").strip():
            raise SourceChangeProposalError(
                f"target_source_id is required for proposal_type '{pt.value}'"
            )
        if pt in _ADD_TYPES and self.proposed_source is None:
            raise SourceChangeProposalError(
                f"proposed_source is required for proposal_type '{pt.value}'"
            )

        object.__setattr__(self, "risks", tuple(self.risks))
        object.__setattr__(self, "evidence_refs", tuple(
            str(r).strip() for r in self.evidence_refs if str(r).strip()
        ))
        object.__setattr__(self, "expected_value", dict(self.expected_value))
        object.__setattr__(self, "payload", dict(self.payload))
        object.__setattr__(self, "metadata", dict(self.metadata))

        lineage = dict(self.lineage)
        lineage.setdefault("evidence_refs", [])
        lineage.setdefault("proposal_trace_refs", [])
        lineage.setdefault("applied_change_refs", [])
        object.__setattr__(self, "lineage", lineage)

    @property
    def is_terminal(self) -> bool:
        return self.status in (ProposalStatus.APPLIED, ProposalStatus.REJECTED, ProposalStatus.RETIRED)

    def _transition(self, new_status: ProposalStatus, *, updated_at: str | None = None) -> "SourceChangeProposal":
        allowed = _VALID_STATUS_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise SourceChangeProposalError(
                f"Cannot transition proposal from '{self.status.value}' to '{new_status.value}'. "
                f"Allowed transitions: {[s.value for s in sorted(allowed, key=lambda x: x.value)]}"
            )
        return SourceChangeProposal(
            proposal_id=self.proposal_id,
            proposal_type=self.proposal_type.value,
            source_kind=self.source_kind.value,
            rationale=self.rationale,
            proposed_by=dict(self.proposed_by),
            status=new_status.value,
            target_source_id=self.target_source_id,
            proposed_source=self.proposed_source,
            expected_value=dict(self.expected_value),
            risks=list(self.risks),
            evidence_refs=list(self.evidence_refs),
            payload=dict(self.payload),
            lineage=dict(self.lineage),
            created_at=self.created_at,
            updated_at=updated_at or _utc_now(),
            metadata=dict(self.metadata),
        )

    def submit(self, *, updated_at: str | None = None) -> "SourceChangeProposal":
        return self._transition(ProposalStatus.SUBMITTED, updated_at=updated_at)

    def approve(self, *, updated_at: str | None = None) -> "SourceChangeProposal":
        return self._transition(ProposalStatus.APPROVED, updated_at=updated_at)

    def reject(self, *, updated_at: str | None = None) -> "SourceChangeProposal":
        return self._transition(ProposalStatus.REJECTED, updated_at=updated_at)

    def apply(
        self,
        *,
        receipt: Any | None = None,
        receipt_id: str | None = None,
        change_ref: str | None = None,
        updated_at: str | None = None,
    ) -> "SourceChangeProposal":
        if receipt is not None:
            r_status = getattr(receipt, "status", None)
            if hasattr(r_status, "value"):
                r_status = r_status.value
            elif isinstance(receipt, Mapping):
                r_status = receipt.get("status")
            if str(r_status) != "succeeded":
                raise SourceChangeProposalError(
                    f"Cannot apply proposal without a succeeded typed receipt; got status={r_status}"
                )
            r_id = getattr(receipt, "receipt_id", None) or (receipt.get("receipt_id") if isinstance(receipt, Mapping) else None)
            if r_id:
                receipt_id = str(r_id)

        effective_ref = receipt_id or change_ref
        updated = self._transition(ProposalStatus.APPLIED, updated_at=updated_at)
        if effective_ref:
            lineage = dict(updated.lineage)
            refs = list(lineage.get("applied_change_refs") or [])
            if effective_ref not in refs:
                refs.append(effective_ref)
            lineage["applied_change_refs"] = refs
            if receipt_id:
                lineage["receipt_id"] = receipt_id
            return SourceChangeProposal(
                proposal_id=updated.proposal_id,
                proposal_type=updated.proposal_type.value,
                source_kind=updated.source_kind.value,
                rationale=updated.rationale,
                proposed_by=dict(updated.proposed_by),
                status=updated.status.value,
                target_source_id=updated.target_source_id,
                proposed_source=updated.proposed_source,
                expected_value=dict(updated.expected_value),
                risks=list(updated.risks),
                evidence_refs=list(updated.evidence_refs),
                payload=dict(updated.payload),
                lineage=lineage,
                created_at=updated.created_at,
                updated_at=updated.updated_at,
                metadata=dict(updated.metadata),
            )
        return updated

    def retire(self, *, updated_at: str | None = None) -> "SourceChangeProposal":
        return self._transition(ProposalStatus.RETIRED, updated_at=updated_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "proposal_id": self.proposal_id,
            "proposal_type": self.proposal_type.value,
            "source_kind": self.source_kind.value,
            "target_source_id": self.target_source_id,
            "proposed_source": self.proposed_source.to_dict() if self.proposed_source else None,
            "expected_value": dict(self.expected_value),
            "risks": [r.to_dict() for r in self.risks],
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
            "payload": dict(self.payload),
            "lineage": dict(self.lineage),
            "proposed_by": dict(self.proposed_by),
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceChangeProposal":
        proposed_source_data = data.get("proposed_source")
        proposed_source = (
            ProposedSourceInfo.from_dict(proposed_source_data)
            if proposed_source_data
            else None
        )
        risks_data = data.get("risks") or []
        risks = [ProposalRisk.from_dict(r) for r in risks_data if isinstance(r, dict)]
        return cls(
            proposal_id=str(data["proposal_id"]),
            proposal_type=str(data["proposal_type"]),
            source_kind=str(data["source_kind"]),
            rationale=str(data["rationale"]),
            proposed_by=dict(data.get("proposed_by") or {}),
            status=str(data.get("status", ProposalStatus.DRAFT.value)),
            target_source_id=data.get("target_source_id"),
            proposed_source=proposed_source,
            expected_value=dict(data.get("expected_value") or {}),
            risks=risks,
            evidence_refs=list(data.get("evidence_refs") or []),
            payload=dict(data.get("payload") or {}),
            lineage=dict(data.get("lineage") or {}),
            created_at=str(data.get("created_at") or _utc_now()),
            updated_at=str(data.get("updated_at") or _utc_now()),
            metadata=dict(data.get("metadata") or {}),
        )


class SourceChangeProposalStore:
    """JSONL-backed store for SourceChangeProposal records.

    All mutations go through the typed transition methods (submit, approve,
    reject, apply, retire) so that no code path can silently bypass the
    lifecycle rules.
    """

    def __init__(self, store: JsonlRegistryStore | None = None) -> None:
        self._index: dict[str, SourceChangeProposal] = {}
        self._store = store
        if store is not None:
            self._load_from_store()

    def _load_from_store(self) -> None:
        for record in self._store.read_all():
            try:
                proposal = SourceChangeProposal.from_dict(record)
                self._index[proposal.proposal_id] = proposal
            except (SourceChangeProposalError, KeyError, TypeError):
                pass

    def _persist(self, proposal: SourceChangeProposal) -> None:
        self._index[proposal.proposal_id] = proposal
        if self._store is not None:
            self._store.upsert(proposal.to_dict())

    def create_draft(self, proposal: SourceChangeProposal) -> SourceChangeProposal:
        """Create a new proposal.  Status must be ``draft``."""
        if proposal.status != ProposalStatus.DRAFT:
            raise SourceChangeProposalError(
                f"create_draft only accepts proposals with status=draft; got '{proposal.status.value}'"
            )
        if proposal.proposal_id in self._index:
            raise SourceChangeProposalError(f"Proposal already exists: {proposal.proposal_id}")
        self._persist(proposal)
        return proposal

    def get(self, proposal_id: str) -> SourceChangeProposal | None:
        return self._index.get(proposal_id)

    def list(
        self,
        *,
        status: ProposalStatus | str | None = None,
        proposal_type: ProposalType | str | None = None,
        source_kind: SourceKind | str | None = None,
    ) -> list[SourceChangeProposal]:
        results = list(self._index.values())
        if status is not None:
            status_val = str(ProposalStatus(str(status)).value)
            results = [p for p in results if p.status.value == status_val]
        if proposal_type is not None:
            pt_val = str(ProposalType(str(proposal_type)).value)
            results = [p for p in results if p.proposal_type.value == pt_val]
        if source_kind is not None:
            sk_val = str(SourceKind(str(source_kind)).value)
            results = [p for p in results if p.source_kind.value == sk_val]
        return results

    def submit(self, proposal_id: str) -> SourceChangeProposal:
        proposal = self._require_proposal(proposal_id)
        updated = proposal.submit()
        self._persist(updated)
        return updated

    def approve(self, proposal_id: str) -> SourceChangeProposal:
        proposal = self._require_proposal(proposal_id)
        updated = proposal.approve()
        self._persist(updated)
        return updated

    def reject(self, proposal_id: str) -> SourceChangeProposal:
        proposal = self._require_proposal(proposal_id)
        updated = proposal.reject()
        self._persist(updated)
        return updated

    def apply(
        self,
        proposal_id: str,
        *,
        receipt: Any | None = None,
        receipt_id: str | None = None,
        change_ref: str | None = None,
    ) -> SourceChangeProposal:
        proposal = self._require_proposal(proposal_id)
        updated = proposal.apply(receipt=receipt, receipt_id=receipt_id, change_ref=change_ref)
        self._persist(updated)
        return updated

    def retire(self, proposal_id: str) -> SourceChangeProposal:
        proposal = self._require_proposal(proposal_id)
        updated = proposal.retire()
        self._persist(updated)
        return updated

    def _require_proposal(self, proposal_id: str) -> SourceChangeProposal:
        proposal = self.get(proposal_id)
        if proposal is None:
            raise SourceChangeProposalError(f"Unknown proposal: {proposal_id}")
        return proposal

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "SourceChangeProposalStore":
        store = JsonlRegistryStore(path, id_field="proposal_id")
        return cls(store=store)
