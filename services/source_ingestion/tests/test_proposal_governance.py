"""Tests for DATASTRAT-PROPOSAL-006: governed LLM source-change proposal workflow.

Acceptance criteria:
1. LLM cannot mutate the source registry directly — the adapter creates only
   draft proposals; registry entries are unchanged.
2. The approved → applied transition goes through the store's ``apply`` method,
   which records an audited change_ref.
3. A rejected proposal has no side effects; subsequent registry reads are
   unchanged.
4. Status lifecycle is enforced: invalid transitions raise SourceChangeProposalError.
5. The LLM adapter cannot bypass the draft-only invariant.
6. JSONL persistence round-trip preserves all fields.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from services.source_ingestion.registry import (
    DataSourceRegistry,
    LLMSourceProposalAdapter,
    ProposalStatus,
    ProposalType,
    SourceChangeProposal,
    SourceChangeProposalError,
    SourceChangeProposalStore,
    SourceKind,
)
from services.source_ingestion.registry.proposals import (
    ProposalRisk,
    ProposedSourceInfo,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _store() -> SourceChangeProposalStore:
    return SourceChangeProposalStore()


def _adapter(store: SourceChangeProposalStore | None = None) -> LLMSourceProposalAdapter:
    return LLMSourceProposalAdapter(store or _store())


_PROPOSED_DATA_SOURCE = {
    "source_id": "ds-polygon-us-price",
    "provider": "Polygon",
    "source_class": "market_daily",
    "license_scope": "commercial",
    "entitlement_required": True,
    "allowed_use": ["research_data", "backtest_data"],
    "expected_datasets": ["us_price_daily"],
    "update_frequency": "daily",
    "cost_notes": "~$50/month for starter plan",
}

_PROPOSED_SEED_SOURCE = {
    "source_id": "sss-ssrn-finance",
    "provider": "SSRN",
    "source_class": "paper",
    "license_scope": "restricted_access",
    "allowed_use": ["research_discovery"],
}


# ---------------------------------------------------------------------------
# ProposedSourceInfo model tests
# ---------------------------------------------------------------------------

class TestProposedSourceInfo:
    def test_requires_source_id(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="source_id"):
            ProposedSourceInfo(
                source_id="",
                source_kind="data_source",
                provider="P",
                source_class="market_daily",
                license_scope="open",
                allowed_use=["research_data"],
            )

    def test_requires_allowed_use(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="allowed_use must not be empty"):
            ProposedSourceInfo(
                source_id="ds-x",
                source_kind="data_source",
                provider="P",
                source_class="market_daily",
                license_scope="open",
                allowed_use=[],
            )

    def test_rejects_invalid_source_kind(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="source_kind must be one of"):
            ProposedSourceInfo(
                source_id="ds-x",
                source_kind="unknown_kind",
                provider="P",
                source_class="market_daily",
                license_scope="open",
                allowed_use=["research_data"],
            )

    def test_round_trip(self) -> None:
        info = ProposedSourceInfo(
            source_id="ds-polygon",
            source_kind="data_source",
            provider="Polygon",
            source_class="market_daily",
            license_scope="commercial",
            allowed_use=["research_data"],
            entitlement_required=True,
            cost_notes="$50/month",
        )
        d = info.to_dict()
        recovered = ProposedSourceInfo.from_dict(d)
        assert recovered.source_id == info.source_id
        assert recovered.entitlement_required is True
        assert recovered.cost_notes == "$50/month"


# ---------------------------------------------------------------------------
# ProposalRisk model tests
# ---------------------------------------------------------------------------

class TestProposalRisk:
    def test_rejects_invalid_risk_type(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="risk_type must be one of"):
            ProposalRisk(risk_type="unknown", severity="low", note="n")

    def test_rejects_invalid_severity(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="severity must be one of"):
            ProposalRisk(risk_type="cost", severity="critical", note="n")

    def test_rejects_empty_note(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="note must not be empty"):
            ProposalRisk(risk_type="cost", severity="high", note="")

    def test_round_trip(self) -> None:
        risk = ProposalRisk(risk_type="license", severity="medium", note="requires entitlement check")
        d = risk.to_dict()
        recovered = ProposalRisk.from_dict(d)
        assert recovered.risk_type == "license"
        assert recovered.severity == "medium"
        assert recovered.note == "requires entitlement check"


# ---------------------------------------------------------------------------
# SourceChangeProposal model tests
# ---------------------------------------------------------------------------

class TestSourceChangeProposal:
    def _draft(self, **overrides) -> SourceChangeProposal:
        defaults = dict(
            proposal_id="prop-test-001",
            proposal_type="add_data_source",
            source_kind="data_source",
            rationale="Need US daily price data from Polygon as Stooq backup.",
            proposed_by={"actor_type": "agent", "actor_id": "research-llm"},
            proposed_source=ProposedSourceInfo.from_dict({
                "source_id": "ds-polygon",
                "source_kind": "data_source",
                "provider": "Polygon",
                "source_class": "market_daily",
                "license_scope": "commercial",
                "allowed_use": ["research_data"],
            }),
        )
        defaults.update(overrides)
        return SourceChangeProposal(**defaults)

    def test_status_defaults_to_draft(self) -> None:
        p = self._draft()
        assert p.status == ProposalStatus.DRAFT

    def test_requires_proposal_id(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="proposal_id is required"):
            self._draft(proposal_id="")

    def test_requires_rationale(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="rationale is required"):
            self._draft(rationale="")

    def test_requires_proposed_by_actor_id(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="proposed_by.actor_id is required"):
            self._draft(proposed_by={"actor_type": "agent", "actor_id": ""})

    def test_requires_proposed_by_actor_type(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="proposed_by.actor_type"):
            self._draft(proposed_by={"actor_type": "robot", "actor_id": "x"})

    def test_add_type_requires_proposed_source(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="proposed_source is required"):
            SourceChangeProposal(
                proposal_id="p-001",
                proposal_type="add_data_source",
                source_kind="data_source",
                rationale="test",
                proposed_by={"actor_type": "agent", "actor_id": "llm"},
                proposed_source=None,
            )

    def test_disable_type_requires_target_source_id(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="target_source_id is required"):
            SourceChangeProposal(
                proposal_id="p-001",
                proposal_type="disable_source",
                source_kind="data_source",
                rationale="test",
                proposed_by={"actor_type": "agent", "actor_id": "llm"},
                target_source_id=None,
            )

    def test_rejects_invalid_proposal_type(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="proposal_type must be one of"):
            self._draft(proposal_type="delete_everything")

    def test_rejects_invalid_source_kind(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="source_kind must be one of"):
            self._draft(source_kind="unknown")

    def test_is_terminal_for_applied(self) -> None:
        p = self._draft()
        submitted = p.submit()
        approved = submitted.approve()
        applied = approved.apply()
        assert applied.is_terminal

    def test_is_terminal_for_rejected(self) -> None:
        p = self._draft()
        submitted = p.submit()
        rejected = submitted.reject()
        assert rejected.is_terminal

    def test_is_not_terminal_for_draft(self) -> None:
        assert not self._draft().is_terminal

    def test_lifecycle_draft_to_submitted(self) -> None:
        p = self._draft()
        submitted = p.submit()
        assert submitted.status == ProposalStatus.SUBMITTED
        assert p.status == ProposalStatus.DRAFT  # original unchanged

    def test_lifecycle_submitted_to_approved(self) -> None:
        submitted = self._draft().submit()
        approved = submitted.approve()
        assert approved.status == ProposalStatus.APPROVED

    def test_lifecycle_submitted_to_rejected(self) -> None:
        submitted = self._draft().submit()
        rejected = submitted.reject()
        assert rejected.status == ProposalStatus.REJECTED

    def test_lifecycle_approved_to_applied(self) -> None:
        applied = self._draft().submit().approve().apply()
        assert applied.status == ProposalStatus.APPLIED

    def test_apply_records_change_ref(self) -> None:
        applied = self._draft().submit().approve().apply(change_ref="ds-registry-add:ds-polygon")
        assert "ds-registry-add:ds-polygon" in applied.lineage["applied_change_refs"]

    def test_draft_to_retired(self) -> None:
        retired = self._draft().retire()
        assert retired.status == ProposalStatus.RETIRED

    def test_cannot_submit_from_rejected(self) -> None:
        rejected = self._draft().submit().reject()
        with pytest.raises(SourceChangeProposalError, match="Cannot transition"):
            rejected.submit()

    def test_cannot_approve_from_draft(self) -> None:
        with pytest.raises(SourceChangeProposalError, match="Cannot transition"):
            self._draft().approve()

    def test_cannot_apply_from_submitted(self) -> None:
        submitted = self._draft().submit()
        with pytest.raises(SourceChangeProposalError, match="Cannot transition"):
            submitted.apply()

    def test_to_dict_round_trip(self) -> None:
        p = self._draft()
        d = p.to_dict()
        assert d["schema_version"] == "source_change_proposal.v1"
        assert d["proposal_type"] == "add_data_source"
        recovered = SourceChangeProposal.from_dict(d)
        assert recovered.proposal_id == p.proposal_id
        assert recovered.status == ProposalStatus.DRAFT
        assert recovered.proposed_source is not None
        assert recovered.proposed_source.source_id == "ds-polygon"


# ---------------------------------------------------------------------------
# SourceChangeProposalStore tests
# ---------------------------------------------------------------------------

class TestSourceChangeProposalStore:
    def _draft_proposal(self, proposal_id: str = "prop-001") -> SourceChangeProposal:
        return SourceChangeProposal(
            proposal_id=proposal_id,
            proposal_type="add_data_source",
            source_kind="data_source",
            rationale="Test rationale",
            proposed_by={"actor_type": "agent", "actor_id": "test-agent"},
            proposed_source=ProposedSourceInfo.from_dict({
                "source_id": "ds-test",
                "source_kind": "data_source",
                "provider": "TestVendor",
                "source_class": "market_daily",
                "license_scope": "open",
                "allowed_use": ["research_data"],
            }),
        )

    def test_create_draft_and_get(self) -> None:
        store = _store()
        proposal = self._draft_proposal()
        store.create_draft(proposal)
        fetched = store.get(proposal.proposal_id)
        assert fetched is not None
        assert fetched.status == ProposalStatus.DRAFT

    def test_create_draft_rejects_non_draft_status(self) -> None:
        store = _store()
        proposal = self._draft_proposal()
        submitted = proposal.submit()
        with pytest.raises(SourceChangeProposalError, match="create_draft only accepts.*draft"):
            store.create_draft(submitted)

    def test_create_draft_rejects_duplicate(self) -> None:
        store = _store()
        proposal = self._draft_proposal()
        store.create_draft(proposal)
        with pytest.raises(SourceChangeProposalError, match="already exists"):
            store.create_draft(proposal)

    def test_submit(self) -> None:
        store = _store()
        store.create_draft(self._draft_proposal())
        updated = store.submit("prop-001")
        assert updated.status == ProposalStatus.SUBMITTED
        assert store.get("prop-001").status == ProposalStatus.SUBMITTED

    def test_approve(self) -> None:
        store = _store()
        store.create_draft(self._draft_proposal())
        store.submit("prop-001")
        updated = store.approve("prop-001")
        assert updated.status == ProposalStatus.APPROVED

    def test_reject(self) -> None:
        store = _store()
        store.create_draft(self._draft_proposal())
        store.submit("prop-001")
        updated = store.reject("prop-001")
        assert updated.status == ProposalStatus.REJECTED

    def test_apply(self) -> None:
        store = _store()
        store.create_draft(self._draft_proposal())
        store.submit("prop-001")
        store.approve("prop-001")
        updated = store.apply("prop-001", change_ref="registry:add:ds-test")
        assert updated.status == ProposalStatus.APPLIED
        assert "registry:add:ds-test" in updated.lineage["applied_change_refs"]

    def test_retire(self) -> None:
        store = _store()
        store.create_draft(self._draft_proposal())
        updated = store.retire("prop-001")
        assert updated.status == ProposalStatus.RETIRED

    def test_get_unknown_returns_none(self) -> None:
        assert _store().get("nonexistent") is None

    def test_list_all(self) -> None:
        store = _store()
        store.create_draft(self._draft_proposal("prop-a"))
        store.create_draft(self._draft_proposal("prop-b"))
        assert len(store.list()) == 2

    def test_list_filter_by_status(self) -> None:
        store = _store()
        store.create_draft(self._draft_proposal("prop-a"))
        store.create_draft(self._draft_proposal("prop-b"))
        store.submit("prop-b")
        drafts = store.list(status="draft")
        submitted = store.list(status="submitted")
        assert len(drafts) == 1
        assert len(submitted) == 1

    def test_list_filter_by_proposal_type(self) -> None:
        store = _store()
        store.create_draft(self._draft_proposal())
        p2 = SourceChangeProposal(
            proposal_id="prop-retire",
            proposal_type="retire_source",
            source_kind="data_source",
            rationale="Low usage",
            proposed_by={"actor_type": "agent", "actor_id": "llm"},
            target_source_id="ds-old",
        )
        store.create_draft(p2)
        add_proposals = store.list(proposal_type="add_data_source")
        retire_proposals = store.list(proposal_type="retire_source")
        assert len(add_proposals) == 1
        assert len(retire_proposals) == 1

    def test_rejected_proposal_can_be_listed(self) -> None:
        store = _store()
        store.create_draft(self._draft_proposal())
        store.submit("prop-001")
        store.reject("prop-001")
        rejected = store.list(status="rejected")
        assert len(rejected) == 1

    def test_unknown_proposal_operations_raise(self) -> None:
        store = _store()
        with pytest.raises(SourceChangeProposalError, match="Unknown proposal"):
            store.submit("nonexistent")
        with pytest.raises(SourceChangeProposalError, match="Unknown proposal"):
            store.approve("nonexistent")
        with pytest.raises(SourceChangeProposalError, match="Unknown proposal"):
            store.reject("nonexistent")

    def test_jsonl_persistence_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "proposals.jsonl"
            store1 = SourceChangeProposalStore.from_jsonl(path)
            store1.create_draft(self._draft_proposal("prop-a"))
            store1.create_draft(self._draft_proposal("prop-b"))
            store1.submit("prop-a")
            store1.approve("prop-a")

            store2 = SourceChangeProposalStore.from_jsonl(path)
            assert len(store2.list()) == 2
            assert store2.get("prop-a").status == ProposalStatus.APPROVED
            assert store2.get("prop-b").status == ProposalStatus.DRAFT

    def test_jsonl_applied_change_ref_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "proposals.jsonl"
            store1 = SourceChangeProposalStore.from_jsonl(path)
            store1.create_draft(self._draft_proposal())
            store1.submit("prop-001")
            store1.approve("prop-001")
            store1.apply("prop-001", change_ref="ds-registry-add:ds-test")

            store2 = SourceChangeProposalStore.from_jsonl(path)
            p = store2.get("prop-001")
            assert p.status == ProposalStatus.APPLIED
            assert "ds-registry-add:ds-test" in p.lineage["applied_change_refs"]


# ---------------------------------------------------------------------------
# LLMSourceProposalAdapter tests (acceptance criterion 1 and 5)
# ---------------------------------------------------------------------------

class TestLLMSourceProposalAdapter:
    """LLM adapter may only create draft proposals and cannot touch registries."""

    def test_propose_add_data_source_creates_draft(self) -> None:
        store = _store()
        adapter = _adapter(store)
        proposal = adapter.propose_add_data_source(
            agent_id="research-llm",
            proposed_source=_PROPOSED_DATA_SOURCE,
            rationale="Need Polygon as Stooq fallback for US price data.",
        )
        assert proposal.status == ProposalStatus.DRAFT
        assert proposal.proposal_type == ProposalType.ADD_DATA_SOURCE
        assert proposal.source_kind == SourceKind.DATA_SOURCE
        assert proposal.proposed_by["actor_type"] == "agent"
        assert proposal.proposed_by["actor_id"] == "research-llm"

    def test_propose_add_strategy_seed_source_creates_draft(self) -> None:
        store = _store()
        adapter = _adapter(store)
        proposal = adapter.propose_add_strategy_seed_source(
            agent_id="research-llm",
            proposed_source=_PROPOSED_SEED_SOURCE,
            rationale="SSRN has finance papers relevant to Taiwan equity strategies.",
        )
        assert proposal.status == ProposalStatus.DRAFT
        assert proposal.proposal_type == ProposalType.ADD_STRATEGY_SEED_SOURCE
        assert proposal.source_kind == SourceKind.STRATEGY_SEED_SOURCE

    def test_propose_disable_source_creates_draft(self) -> None:
        adapter = _adapter()
        proposal = adapter.propose_disable_source(
            agent_id="monitoring-llm",
            target_source_id="ds-stooq-us-price",
            source_kind="data_source",
            rationale="Stooq has been returning 503 for 7 days; 100% failure rate.",
            risks=[{"risk_type": "quality", "severity": "high", "note": "No US price fallback configured."}],
        )
        assert proposal.status == ProposalStatus.DRAFT
        assert proposal.proposal_type == ProposalType.DISABLE_SOURCE
        assert proposal.target_source_id == "ds-stooq-us-price"
        assert len(proposal.risks) == 1

    def test_propose_retire_source_creates_draft(self) -> None:
        adapter = _adapter()
        proposal = adapter.propose_retire_source(
            agent_id="governance-llm",
            target_source_id="ds-old-legacy",
            source_kind="data_source",
            rationale="No queries in 90 days; no active strategy dependencies.",
        )
        assert proposal.status == ProposalStatus.DRAFT
        assert proposal.proposal_type == ProposalType.RETIRE_SOURCE

    def test_propose_replace_source_creates_draft(self) -> None:
        adapter = _adapter()
        proposal = adapter.propose_replace_source(
            agent_id="rebalance-llm",
            target_source_id="ds-stooq",
            source_kind="data_source",
            rationale="Polygon provides more reliable US data.",
            replacement_source_id="ds-polygon",
        )
        assert proposal.status == ProposalStatus.DRAFT
        assert proposal.proposal_type == ProposalType.REPLACE_SOURCE
        assert proposal.metadata.get("replacement_source_id") == "ds-polygon"

    def test_propose_change_schedule_creates_draft(self) -> None:
        adapter = _adapter()
        proposal = adapter.propose_change_schedule(
            agent_id="scheduler-llm",
            target_source_id="ds-fred-macro",
            source_kind="data_source",
            rationale="FRED updates weekly; daily polling is wasteful.",
            schedule_change={"interval_seconds": 604800, "reason": "weekly publication cadence"},
        )
        assert proposal.status == ProposalStatus.DRAFT
        assert proposal.proposal_type == ProposalType.CHANGE_SCHEDULE
        assert "schedule_change" in proposal.metadata

    def test_propose_request_vendor_quote_creates_draft(self) -> None:
        adapter = _adapter()
        proposal = adapter.propose_request_vendor_quote(
            agent_id="cost-llm",
            source_kind="data_source",
            rationale="TEJ institutional data has high signal; need quote for full dataset.",
            proposed_source={
                "source_id": "ds-tej-institutional",
                "provider": "TEJ",
                "source_class": "taiwan_chip",
                "license_scope": "commercial_restricted",
                "entitlement_required": True,
                "allowed_use": ["research_data"],
            },
        )
        assert proposal.status == ProposalStatus.DRAFT
        assert proposal.proposal_type == ProposalType.REQUEST_VENDOR_QUOTE

    def test_llm_adapter_does_not_mutate_data_registry(self) -> None:
        """The LLM adapter must not change any registry entry (invariant §14.5)."""
        reg = DataSourceRegistry()
        proposal_store = _store()
        adapter = LLMSourceProposalAdapter(proposal_store)

        adapter.propose_add_data_source(
            agent_id="research-llm",
            proposed_source=_PROPOSED_DATA_SOURCE,
            rationale="Testing isolation from registry.",
        )
        # Registry must remain empty — adapter only wrote a draft proposal.
        assert reg.list() == []
        assert len(proposal_store.list()) == 1
        assert proposal_store.list()[0].status == ProposalStatus.DRAFT

    def test_llm_adapter_proposal_persists_to_store(self) -> None:
        store = _store()
        adapter = LLMSourceProposalAdapter(store)
        proposal = adapter.propose_add_data_source(
            agent_id="research-llm",
            proposed_source=_PROPOSED_DATA_SOURCE,
            rationale="Polygon fallback.",
        )
        fetched = store.get(proposal.proposal_id)
        assert fetched is not None
        assert fetched.proposal_id == proposal.proposal_id

    def test_adapter_cannot_submit_approved_or_apply(self) -> None:
        """The adapter only creates drafts; no escalation method exists on it."""
        adapter = _adapter()
        assert not hasattr(adapter, "submit")
        assert not hasattr(adapter, "approve")
        assert not hasattr(adapter, "apply")


# ---------------------------------------------------------------------------
# Lifecycle and side-effect isolation tests (acceptance criteria 2 and 3)
# ---------------------------------------------------------------------------

class TestLifecycleAndIsolation:
    """Approved apply records a change_ref. Rejected proposals have no side effects."""

    def _populated_store(self) -> SourceChangeProposalStore:
        store = _store()
        proposal = SourceChangeProposal(
            proposal_id="prop-lifecycle",
            proposal_type="add_data_source",
            source_kind="data_source",
            rationale="Lifecycle test",
            proposed_by={"actor_type": "agent", "actor_id": "test-llm"},
            proposed_source=ProposedSourceInfo.from_dict({
                "source_id": "ds-lifecycle-test",
                "source_kind": "data_source",
                "provider": "TestVendor",
                "source_class": "market_daily",
                "license_scope": "open",
                "allowed_use": ["research_data"],
            }),
        )
        store.create_draft(proposal)
        return store

    def test_approved_proposal_applied_records_change_ref(self) -> None:
        store = self._populated_store()
        store.submit("prop-lifecycle")
        store.approve("prop-lifecycle")
        applied = store.apply("prop-lifecycle", change_ref="ds-registry-add:ds-lifecycle-test")

        assert applied.status == ProposalStatus.APPLIED
        assert "ds-registry-add:ds-lifecycle-test" in applied.lineage["applied_change_refs"]

        data_reg = DataSourceRegistry()
        assert data_reg.list() == [], "Applying a proposal does not auto-mutate the data registry"

    def test_rejected_proposal_has_no_side_effects(self) -> None:
        store = self._populated_store()
        store.submit("prop-lifecycle")
        rejected = store.reject("prop-lifecycle")

        assert rejected.status == ProposalStatus.REJECTED
        assert rejected.lineage.get("applied_change_refs") == []
        data_reg = DataSourceRegistry()
        assert data_reg.list() == []

    def test_rejected_proposal_cannot_be_applied(self) -> None:
        store = self._populated_store()
        store.submit("prop-lifecycle")
        store.reject("prop-lifecycle")
        with pytest.raises(SourceChangeProposalError, match="Cannot transition"):
            store.apply("prop-lifecycle")

    def test_full_lifecycle_add_data_source(self) -> None:
        store = _store()
        adapter = LLMSourceProposalAdapter(store)

        # Step 1: LLM creates draft
        proposal = adapter.propose_add_data_source(
            agent_id="research-llm",
            proposed_source=_PROPOSED_DATA_SOURCE,
            rationale="Polygon as Stooq backup.",
            risks=[{"risk_type": "cost", "severity": "low", "note": "$50/month is within budget."}],
        )
        assert proposal.status == ProposalStatus.DRAFT

        # Step 2: Operator submits
        submitted = store.submit(proposal.proposal_id)
        assert submitted.status == ProposalStatus.SUBMITTED

        # Step 3: Reviewer approves
        approved = store.approve(proposal.proposal_id)
        assert approved.status == ProposalStatus.APPROVED

        # Step 4: Operator applies (registry mutation is a separate concern)
        applied = store.apply(proposal.proposal_id, change_ref="ds-registry-add:ds-polygon-us-price")
        assert applied.status == ProposalStatus.APPLIED
        assert applied.is_terminal

    def test_full_lifecycle_retire_source(self) -> None:
        store = _store()
        adapter = LLMSourceProposalAdapter(store)

        proposal = adapter.propose_retire_source(
            agent_id="monitor-llm",
            target_source_id="ds-unused-legacy",
            source_kind="data_source",
            rationale="Zero queries in 90 days. No strategy dependency.",
            evidence_refs=["usage-report-2026-06-01"],
        )
        assert proposal.status == ProposalStatus.DRAFT
        assert proposal.target_source_id == "ds-unused-legacy"
        assert "usage-report-2026-06-01" in proposal.evidence_refs

        store.submit(proposal.proposal_id)
        store.approve(proposal.proposal_id)
        applied = store.apply(proposal.proposal_id, change_ref="ds-registry-retire:ds-unused-legacy")
        assert applied.status == ProposalStatus.APPLIED
