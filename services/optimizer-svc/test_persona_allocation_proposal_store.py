from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


OPTIMIZER_DIR = Path(__file__).resolve().parent
if str(OPTIMIZER_DIR) not in sys.path:
    sys.path.insert(0, str(OPTIMIZER_DIR))

from allocation_aggregation import (  # noqa: E402
    PERSONA_ALLOCATION_PROPOSAL_RECORD_SCHEMA_VERSION,
    PersonaAllocationProposalJsonlStore,
    PersonaAllocationProposalQuery,
    PersonaAllocationProposalStoreError,
    ingest_persona_proposal,
)
from portfolio_synthesis import (  # noqa: E402
    PersonaAllocationProposal,
    PortfolioSynthesizer,
    SynthesisMethod,
)


def make_proposal(**overrides) -> PersonaAllocationProposal:
    defaults = {
        "proposal_id": "pap-001",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-paper",
        "scope_ref": "paper",
        "target_type": "asset",
        "directions": ["long"],
        "target_weights": {"2330.TW": 0.6, "2317.TW": 0.4},
        "conviction": 0.8,
        "uncertainty": 0.1,
        "rationale_ref": "memo-alpha",
        "regime_ref": "regime-neutral",
        "valid_from": "2026-05-15T14:00:00Z",
        "valid_to": "2026-05-22T14:00:00Z",
        "evidence_refs": ["evidence://pap-001"],
        "created_at": "2026-05-15T14:00:00Z",
        "reliability_score": 0.9,
        "regime_fit_score": 1.0,
        "governance_multiplier": 1.0,
        "metadata": {"strategy_family": "tw_equity"},
    }
    defaults.update(overrides)
    return PersonaAllocationProposal(**defaults)


def test_append_proposal_replays_latest_snapshot(tmp_path):
    store_path = tmp_path / "persona_allocation_proposals.jsonl"
    store = PersonaAllocationProposalJsonlStore(store_path)

    envelope = store.append_proposal(make_proposal())

    assert envelope["schema_version"] == PERSONA_ALLOCATION_PROPOSAL_RECORD_SCHEMA_VERSION
    assert envelope["record_type"] == "proposal_snapshot"
    assert envelope["proposal_id"] == "pap-001"
    stored_proposal = store.get_proposal("pap-001")
    assert stored_proposal is not None
    assert stored_proposal.persona_id == "persona-alpha"

    replayed = PersonaAllocationProposalJsonlStore(store_path)
    proposal = replayed.get_proposal("pap-001")
    assert proposal is not None
    assert proposal.target_weights == {"2330.TW": 0.6, "2317.TW": 0.4}
    assert proposal.evidence_refs == ["evidence://pap-001"]
    assert len(list(replayed.iter_records())) == 1


def test_query_filters_and_required_order(tmp_path):
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    store.append_proposal(make_proposal())
    store.append_proposal(
        make_proposal(
            proposal_id="pap-002",
            persona_id="persona-risk",
            target_weights={"0050.TW": 0.7, "CASH": 0.3},
            created_at="2026-05-15T14:01:00Z",
        )
    )
    store.append_proposal(
        make_proposal(
            proposal_id="pap-003",
            persona_id="persona-alpha",
            capital_pool_id="pool-live",
            scope_ref="live",
            target_weights={"CASH": 1.0},
            created_at="2026-05-15T14:02:00Z",
        )
    )

    paper = store.list_proposals(PersonaAllocationProposalQuery(capital_pool_id="pool-paper", scope_ref="paper"))
    assert [proposal.proposal_id for proposal in paper] == ["pap-002", "pap-001"]

    alpha = store.list_proposals(PersonaAllocationProposalQuery(persona_id="persona-alpha"))
    assert [proposal.proposal_id for proposal in alpha] == ["pap-003", "pap-001"]

    required = store.require_proposals(["pap-002", "pap-001"])
    assert [proposal.proposal_id for proposal in required] == ["pap-002", "pap-001"]


def test_duplicate_proposal_id_is_idempotent_only_for_same_payload(tmp_path):
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    proposal = make_proposal()
    first = store.append_proposal(proposal)
    second = store.append_proposal(proposal)

    assert second == first
    assert len(list(store.iter_records())) == 1

    with pytest.raises(PersonaAllocationProposalStoreError, match="already exists"):
        store.append_proposal(make_proposal(target_weights={"2330.TW": 1.0}))


def test_replay_rejects_malformed_or_unsupported_records(tmp_path):
    store_path = tmp_path / "proposals.jsonl"
    store_path.write_text('{"schema_version":"wrong","record_type":"proposal_snapshot"}\n', encoding="utf-8")

    with pytest.raises(PersonaAllocationProposalStoreError, match="schema_version"):
        PersonaAllocationProposalJsonlStore(store_path)


def test_store_outputs_feed_existing_synthesizer(tmp_path):
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    ingest_persona_proposal(make_proposal(), store=store)
    ingest_persona_proposal(
        make_proposal(
            proposal_id="pap-002",
            persona_id="persona-beta",
            target_weights={"2330.TW": 0.2, "2454.TW": 0.8},
            conviction=0.2,
            uncertainty=0.0,
            created_at="2026-05-15T14:01:00Z",
        ),
        store=store,
    )

    proposals = store.require_proposals(["pap-001", "pap-002"])
    artifact, log = PortfolioSynthesizer().synthesize_with_log(
        proposals,
        capital_pool_id="pool-paper",
        scope_ref="paper",
    )

    assert artifact.synthesis_method == SynthesisMethod.WEIGHTED_FUSION.value
    assert artifact.provenance_refs == ["pap-001", "pap-002"]
    assert log.proposal_ids == ["pap-001", "pap-002"]
    assert artifact.sponsor_persona_id == "persona-alpha"


def test_jsonl_payload_is_single_line_sorted_json(tmp_path):
    store_path = tmp_path / "proposals.jsonl"
    store = PersonaAllocationProposalJsonlStore(store_path)
    store.append_proposal(make_proposal())

    lines = store_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["payload"]["proposal"]["proposal_id"] == "pap-001"
