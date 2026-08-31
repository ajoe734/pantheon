from __future__ import annotations

import sys
from pathlib import Path

import pytest


OPTIMIZER_DIR = Path(__file__).resolve().parents[2] / "services" / "optimizer-svc"
if str(OPTIMIZER_DIR) not in sys.path:
    sys.path.insert(0, str(OPTIMIZER_DIR))

from allocation_aggregation import (  # noqa: E402
    PersonaAllocationProposalJsonlStore,
    ingest_persona_proposal,
    synthesize_allocation_with_log,
)
from portfolio_synthesis import (  # noqa: E402
    AllocationPolicyArtifact,
    PersonaAllocationProposal,
    PoolRiskPolicy,
    SynthesisMethod,
    VetoReason,
)
from services.governance.multi_persona.conflict_resolution_log import (  # noqa: E402
    validate_conflict_resolution_log,
)
from services.governance.multi_persona.sponsor_resolver import (  # noqa: E402
    SponsorResolverError,
    resolve_sponsor,
)


def make_proposal(**overrides) -> PersonaAllocationProposal:
    defaults = {
        "proposal_id": "pap-alpha",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-paper",
        "scope_ref": "paper",
        "target_type": "asset",
        "directions": ["long"],
        "target_weights": {"2330.TW": 0.6, "0050.TW": 0.3},
        "conviction": 0.8,
        "uncertainty": 0.1,
        "rationale_ref": "memo-alpha",
        "regime_ref": "regime-risk-on",
        "valid_from": "2026-05-15T14:00:00Z",
        "valid_to": "2026-05-22T14:00:00Z",
        "evidence_refs": ["evidence://pap-alpha"],
        "created_at": "2026-05-15T14:00:00Z",
        "reliability_score": 0.9,
        "regime_fit_score": 1.0,
        "governance_multiplier": 1.0,
        "metadata": {
            "holding_period": "swing",
            "risk_posture": "risk_on",
            "strategy_family": "tw_equity",
        },
    }
    defaults.update(overrides)
    return PersonaAllocationProposal(**defaults)


def test_resolver_consumes_mgmt_syn_artifact_and_outputs_governance_log(tmp_path) -> None:
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    ingest_persona_proposal(make_proposal(), store=store)
    ingest_persona_proposal(
        make_proposal(
            proposal_id="pap-beta",
            persona_id="persona-beta",
            target_weights={"2330.TW": 0.2, "2454.TW": 0.5},
            conviction=0.25,
            uncertainty=0.0,
            regime_ref="regime-risk-off",
            created_at="2026-05-15T14:01:00Z",
            evidence_refs=["evidence://pap-beta"],
            metadata={
                "holding_period": "position",
                "risk_posture": "risk_on",
                "strategy_family": "tw_equity",
            },
        ),
        store=store,
    )
    synthesis = synthesize_allocation_with_log(
        capital_pool_id="pool-paper",
        proposal_ids=["pap-alpha", "pap-beta"],
        method="risk_first",
        risk_policy_ref="risk-policy://pool-paper/v1",
        store=store,
        constraints_bundle={"environment": "paper"},
    )

    resolved = resolve_sponsor(
        synthesis.artifact,
        store=store,
        source_conflict_resolution_log=synthesis.conflict_resolution_log,
    )
    payload = resolved.to_dict()
    log = resolved.conflict_resolution_log

    assert resolved.sponsor_persona_id == "persona-alpha"
    assert resolved.proposal_ids == ("pap-alpha", "pap-beta")
    assert resolved.has_open_conflicts is False
    assert payload["allocation_policy_artifact"]["artifact_id"] == synthesis.artifact.artifact_id
    assert log.log_id == synthesis.artifact.conflict_resolution_log_id
    assert log.source_conflict_resolution_log_id == synthesis.conflict_resolution_log.log_id
    assert log.weighting_outputs == synthesis.conflict_resolution_log.weighting_outputs
    assert log.open_conflicts == ()
    assert "weight_conflict" in {conflict.conflict_type for conflict in log.classified_conflicts}
    assert validate_conflict_resolution_log(log.to_dict()).log_id == log.log_id


def test_resolver_rejects_sponsor_not_present_in_source_proposals(tmp_path) -> None:
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    ingest_persona_proposal(make_proposal(), store=store)
    artifact = AllocationPolicyArtifact(
        artifact_id="alloc-policy-mpo-001",
        capital_pool_id="pool-paper",
        scope_ref="paper",
        sponsor_persona_id="persona-missing",
        synthesis_method=SynthesisMethod.SINGLE_PROPOSAL.value,
        target_weights={"2330.TW": 0.6},
        created_at="2026-05-15T14:02:00Z",
        provenance_refs=["pap-alpha"],
        conflict_resolution_log_id="conflict-log-mpo-001",
    )

    with pytest.raises(SponsorResolverError, match="not present in proposal personas"):
        resolve_sponsor(artifact, store=store)


def test_resolver_surfaces_unresolved_committee_conflict_as_open_conflict(tmp_path) -> None:
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    ingest_persona_proposal(
        make_proposal(conviction=0.92, directions=["long"], target_weights={"2330.TW": 0.6}),
        store=store,
    )
    ingest_persona_proposal(
        make_proposal(
            proposal_id="pap-beta",
            persona_id="persona-beta",
            directions=["short"],
            target_weights={"2330.TW": 0.1, "2454.TW": 0.8},
            conviction=0.91,
            created_at="2026-05-15T14:01:00Z",
            evidence_refs=["evidence://pap-beta"],
        ),
        store=store,
    )
    artifact = AllocationPolicyArtifact(
        artifact_id="alloc-policy-mpo-committee",
        capital_pool_id="pool-paper",
        scope_ref="paper",
        sponsor_persona_id="persona-alpha",
        synthesis_method=SynthesisMethod.WEIGHTED_FUSION.value,
        target_weights={"2330.TW": 0.4, "2454.TW": 0.3},
        created_at="2026-05-15T14:02:00Z",
        provenance_refs=["pap-alpha", "pap-beta"],
        conflict_resolution_log_id="conflict-log-mpo-committee",
    )

    resolved = resolve_sponsor(
        artifact,
        store=store,
        conflict_evidence_ref="support/evidence/MPO-001/conflict-resolution.json",
    )

    assert resolved.has_open_conflicts is True
    assert resolved.conflict_report.requires_committee is True
    # A homogeneity_conflict check (both proposals share strategy_family
    # "tw_equity" with overlapping targets) was added after this test was
    # written and is now classified as conflict #4, shifting sponsor_ambiguity
    # from -004 to -005. Confirmed by direct reproduction: the full sequence
    # is direction_conflict-001, weight_conflict-002/003, homogeneity_conflict-004,
    # sponsor_ambiguity-005 (only the first and last are open).
    assert resolved.conflict_resolution_log.open_conflict_ids == (
        "direction_conflict-001",
        "sponsor_ambiguity-005",
    )
    assert resolved.conflict_resolution_log.open_conflicts[0].evidence_ref == (
        "support/evidence/MPO-001/conflict-resolution.json"
    )


def test_source_vetoed_proposal_closes_live_binding_conflicts(tmp_path) -> None:
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    ingest_persona_proposal(
        make_proposal(
            conviction=0.92,
            directions=["long"],
            target_weights={"2330.TW": 0.6},
        ),
        store=store,
    )
    ingest_persona_proposal(
        make_proposal(
            proposal_id="pap-beta",
            persona_id="persona-beta",
            conviction=0.91,
            directions=["short"],
            target_weights={"2330.TW": 0.1, "2454.TW": 0.8},
            created_at="2026-05-15T14:01:00Z",
            evidence_refs=["evidence://pap-beta"],
            metadata={
                "holding_period": "swing",
                "risk_posture": "risk_on",
                "strategy_family": "blocked_short",
            },
        ),
        store=store,
    )
    synthesis = synthesize_allocation_with_log(
        capital_pool_id="pool-paper",
        proposal_ids=["pap-alpha", "pap-beta"],
        method="risk_first",
        risk_policy_ref="risk-policy://pool-paper/v1",
        store=store,
        pool_risk_policy=PoolRiskPolicy(forbidden_strategy_families={"blocked_short"}),
        constraints_bundle={"environment": "paper"},
    )

    resolved = resolve_sponsor(
        synthesis.artifact,
        store=store,
        source_conflict_resolution_log=synthesis.conflict_resolution_log,
    )

    assert synthesis.artifact.synthesis_method == SynthesisMethod.SINGLE_PROPOSAL.value
    assert synthesis.conflict_resolution_log.vetoed_proposals[0].proposal_id == "pap-beta"
    assert synthesis.conflict_resolution_log.vetoed_proposals[0].reason == (
        VetoReason.FORBIDDEN_STRATEGY_FAMILY.value
    )
    assert resolved.conflict_report.requires_committee is True
    assert resolved.has_open_conflicts is False
    assert resolved.conflict_resolution_log.open_conflicts == ()
    assert {conflict.conflict_type for conflict in resolved.conflict_resolution_log.classified_conflicts} >= {
        "direction_conflict",
        "sponsor_ambiguity",
    }


def test_committee_ref_closes_classifier_committee_conflicts(tmp_path) -> None:
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    ingest_persona_proposal(
        make_proposal(conviction=0.92, directions=["long"], target_weights={"2330.TW": 0.6}),
        store=store,
    )
    ingest_persona_proposal(
        make_proposal(
            proposal_id="pap-beta",
            persona_id="persona-beta",
            directions=["short"],
            target_weights={"2330.TW": 0.1, "2454.TW": 0.8},
            conviction=0.91,
            created_at="2026-05-15T14:01:00Z",
            evidence_refs=["evidence://pap-beta"],
        ),
        store=store,
    )
    artifact = AllocationPolicyArtifact(
        artifact_id="alloc-policy-mpo-committee-resolved",
        capital_pool_id="pool-paper",
        scope_ref="paper",
        sponsor_persona_id="persona-alpha",
        synthesis_method=SynthesisMethod.COMMITTEE_OVERRIDE.value,
        target_weights={"2330.TW": 0.4, "2454.TW": 0.3},
        created_at="2026-05-15T14:02:00Z",
        provenance_refs=["pap-alpha", "pap-beta"],
        conflict_resolution_log_id="conflict-log-mpo-committee-resolved",
    )

    resolved = resolve_sponsor(
        artifact,
        store=store,
        source_conflict_resolution_log={
            "log_id": artifact.conflict_resolution_log_id,
            "capital_pool_id": "pool-paper",
            "scope_ref": "paper",
            "timestamp": "2026-05-15T14:02:00Z",
            "proposal_ids": ["pap-alpha", "pap-beta"],
            "vetoed_proposals": [],
            "weighting_inputs": {"pap-alpha": 0.8, "pap-beta": 0.8},
            "weighting_outputs": {},
            "committee_ref": "committee-risk-001",
            "sponsor_persona_id": "persona-alpha",
            "rejected_reason": None,
            "synthesis_method": SynthesisMethod.COMMITTEE_OVERRIDE.value,
        },
    )

    assert resolved.has_open_conflicts is False
    assert resolved.conflict_resolution_log.committee_ref == "committee-risk-001"
    assert resolved.conflict_resolution_log.classified_conflicts[0].committee_trigger == (
        "long_vs_short_high_conviction_conflict"
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("log_id", "unrelated-conflict-log", "log_id must match"),
        ("capital_pool_id", "pool-other", "capital_pool_id must match"),
        ("scope_ref", "live", "scope_ref must match"),
        ("proposal_ids", ["pap-alpha", "pap-other"], "proposal_ids must match"),
        ("sponsor_persona_id", "persona-beta", "sponsor_persona_id must match"),
        ("synthesis_method", SynthesisMethod.WEIGHTED_FUSION.value, "synthesis_method must match"),
        (
            "weighting_outputs",
            {"pap-other": 1.0},
            "weighting_outputs contains unknown proposal_id",
        ),
        (
            "vetoed_proposals",
            [{"proposal_id": "pap-other", "persona_id": "persona-other", "reason": "risk_policy"}],
            "vetoed_proposals.*unknown proposal_id",
        ),
    ],
)
def test_resolver_rejects_mismatched_source_conflict_log_before_closing_conflicts(
    tmp_path,
    field,
    value,
    message,
) -> None:
    store = PersonaAllocationProposalJsonlStore(tmp_path / "proposals.jsonl")
    ingest_persona_proposal(
        make_proposal(conviction=0.92, directions=["long"], target_weights={"2330.TW": 0.6}),
        store=store,
    )
    ingest_persona_proposal(
        make_proposal(
            proposal_id="pap-beta",
            persona_id="persona-beta",
            directions=["short"],
            target_weights={"2330.TW": 0.1, "2454.TW": 0.8},
            conviction=0.91,
            created_at="2026-05-15T14:01:00Z",
            evidence_refs=["evidence://pap-beta"],
        ),
        store=store,
    )
    artifact = AllocationPolicyArtifact(
        artifact_id="alloc-policy-mpo-source-validation",
        capital_pool_id="pool-paper",
        scope_ref="paper",
        sponsor_persona_id="persona-alpha",
        synthesis_method=SynthesisMethod.COMMITTEE_OVERRIDE.value,
        target_weights={"2330.TW": 0.4, "2454.TW": 0.3},
        created_at="2026-05-15T14:02:00Z",
        provenance_refs=["pap-alpha", "pap-beta"],
        conflict_resolution_log_id="conflict-log-mpo-source-validation",
    )
    source_log = {
        "log_id": artifact.conflict_resolution_log_id,
        "capital_pool_id": artifact.capital_pool_id,
        "scope_ref": artifact.scope_ref,
        "timestamp": artifact.created_at,
        "proposal_ids": list(artifact.provenance_refs),
        "vetoed_proposals": [],
        "weighting_inputs": {"pap-alpha": 0.8, "pap-beta": 0.8},
        "weighting_outputs": {},
        "committee_ref": "committee-risk-001",
        "sponsor_persona_id": artifact.sponsor_persona_id,
        "rejected_reason": None,
        "synthesis_method": artifact.synthesis_method,
    }
    source_log[field] = value

    with pytest.raises(SponsorResolverError, match=message):
        resolve_sponsor(
            artifact,
            store=store,
            source_conflict_resolution_log=source_log,
        )
