from __future__ import annotations

import sys
from pathlib import Path

import pytest


OPTIMIZER_DIR = Path(__file__).resolve().parent
if str(OPTIMIZER_DIR) not in sys.path:
    sys.path.insert(0, str(OPTIMIZER_DIR))

from allocation_aggregation import (  # noqa: E402
    PersonaAllocationProposalJsonlStore,
    ingest_persona_proposal,
    synthesize_allocation,
    synthesize_allocation_with_log,
)
from portfolio_synthesis import (  # noqa: E402
    AllocationPolicyArtifact,
    PersonaAllocationProposal,
    SynthesisError,
    SynthesisMethod,
)


def make_proposal(**overrides) -> PersonaAllocationProposal:
    defaults = {
        "proposal_id": "pap-alpha",
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
        "evidence_refs": ["evidence://pap-alpha"],
        "created_at": "2026-05-15T14:00:00Z",
        "reliability_score": 0.9,
        "regime_fit_score": 1.0,
        "governance_multiplier": 1.0,
        "metadata": {"strategy_family": "tw_equity"},
    }
    defaults.update(overrides)
    return PersonaAllocationProposal(**defaults)


def test_synthesize_allocation_with_log_replays_store_and_records_policy_ref(tmp_path) -> None:
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    ingest_persona_proposal(make_proposal(), store=store)
    ingest_persona_proposal(
        make_proposal(
            proposal_id="pap-beta",
            persona_id="persona-beta",
            target_weights={"2330.TW": 0.2, "2454.TW": 0.8},
            conviction=0.2,
            uncertainty=0.0,
            created_at="2026-05-15T14:01:00Z",
        ),
        store=store,
    )

    result = synthesize_allocation_with_log(
        capital_pool_id="pool-paper",
        proposal_ids=["pap-alpha", "pap-beta"],
        method="risk_first",
        risk_policy_ref="risk-policy://pool-paper/v1",
        store=store,
        constraints_bundle={"max_single_weight": 0.8},
        risk_budget=0.35,
    )

    assert result.resolved_method == SynthesisMethod.WEIGHTED_FUSION.value
    assert result.risk_policy_ref == "risk-policy://pool-paper/v1"
    assert result.artifact.synthesis_method == SynthesisMethod.WEIGHTED_FUSION.value
    assert result.artifact.sponsor_persona_id == "persona-alpha"
    assert result.artifact.provenance_refs == ["pap-alpha", "pap-beta"]
    assert result.artifact.constraints_bundle == {
        "max_single_weight": 0.8,
        "risk_policy_ref": "risk-policy://pool-paper/v1",
        "requested_synthesis_method": "risk_first",
        "resolved_synthesis_method": SynthesisMethod.WEIGHTED_FUSION.value,
    }
    assert result.artifact.risk_budget == 0.35
    assert result.conflict_resolution_log.proposal_ids == ["pap-alpha", "pap-beta"]
    assert result.conflict_resolution_log.sponsor_persona_id == "persona-alpha"


def test_synthesize_allocation_returns_artifact_convenience_shape(tmp_path) -> None:
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    ingest_persona_proposal(make_proposal(), store=store)

    artifact = synthesize_allocation(
        capital_pool_id="pool-paper",
        proposal_ids=["pap-alpha"],
        method=SynthesisMethod.SINGLE_PROPOSAL.value,
        risk_policy_ref="risk-policy://pool-paper/v1",
        store=store,
    )

    assert isinstance(artifact, AllocationPolicyArtifact)
    assert artifact.synthesis_method == SynthesisMethod.SINGLE_PROPOSAL.value
    assert artifact.target_weights == {"2330.TW": 0.6, "2317.TW": 0.4}
    assert artifact.constraints_bundle["risk_policy_ref"] == "risk-policy://pool-paper/v1"


def test_synthesize_allocation_rejects_committee_referral_without_manual_targets(tmp_path) -> None:
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    ingest_persona_proposal(make_proposal(conviction=0.92, directions=["long"]), store=store)
    ingest_persona_proposal(
        make_proposal(
            proposal_id="pap-beta",
            persona_id="persona-beta",
            directions=["short"],
            target_weights={"2330.TW": 0.1, "2454.TW": 0.9},
            conviction=0.91,
            created_at="2026-05-15T14:01:00Z",
        ),
        store=store,
    )

    with pytest.raises(SynthesisError, match="committee referral"):
        synthesize_allocation_with_log(
            capital_pool_id="pool-paper",
            proposal_ids=["pap-alpha", "pap-beta"],
            method="weighted_committee",
            risk_policy_ref="risk-policy://pool-paper/v1",
            store=store,
            constraints_bundle={"unused": False},
        )


def test_synthesize_allocation_validates_method_and_single_proposal_inputs(tmp_path) -> None:
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    ingest_persona_proposal(make_proposal(), store=store)
    ingest_persona_proposal(
        make_proposal(
            proposal_id="pap-beta",
            persona_id="persona-beta",
            target_weights={"2454.TW": 1.0},
            created_at="2026-05-15T14:01:00Z",
        ),
        store=store,
    )

    with pytest.raises(SynthesisError, match="single_proposal"):
        synthesize_allocation(
            capital_pool_id="pool-paper",
            proposal_ids=["pap-alpha", "pap-beta"],
            method=SynthesisMethod.SINGLE_PROPOSAL.value,
            risk_policy_ref="risk-policy://pool-paper/v1",
            store=store,
        )

    with pytest.raises(SynthesisError, match="Unsupported synthesis method"):
        synthesize_allocation(
            capital_pool_id="pool-paper",
            proposal_ids=["pap-alpha"],
            method="mean_variance_optimizer",
            risk_policy_ref="risk-policy://pool-paper/v1",
            store=store,
        )
