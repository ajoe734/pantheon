"""Owner lineage integrity: a tampered snapshot and any forged evaluated line field are rejected.

Run unpatched (``real_lineage``); the shared conftest otherwise stubs ``verify_rebalance_lineage``.
"""
import copy

import pytest

from services.capital import allocation_lineage
from services.capital.allocation_lineage import AllocationLineageError, evaluate_allocation, verify_rebalance_lineage
from services.capital.allocation_store import allocation_line_digest
from services.rankings.snapshots import snapshot_record

pytestmark = pytest.mark.real_lineage

ITEMS = [{
    "persona_id": "p-live", "stage": "live_running", "tier": "a", "overall_score": 80.0,
    "evidence_ref_ids": ["ev-1"],
}]
ROW = {"persona_id": "p-live", "ranking_snapshot_id": None, "stage": "live_running", "tier": "a", "current_weight": 0.1}


class _Reader:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get_ranking_snapshot(self, snapshot_id):
        return copy.deepcopy(self.snapshot) if snapshot_id == self.snapshot["ranking_snapshot_id"] else None


@pytest.fixture
def snapshot(monkeypatch):
    record = snapshot_record(ITEMS, surface="quarterly", period="2026-Q3").to_canonical_dict()
    reader = _Reader(record)
    monkeypatch.setattr(allocation_lineage, "create_ranking_reader", lambda: reader)
    return reader


def _evaluate(snapshot, paper=False):
    snapshot_id = snapshot.snapshot["ranking_snapshot_id"]
    return evaluate_allocation({"ranking_snapshot_id": snapshot_id, "rows": [{**ROW, "ranking_snapshot_id": snapshot_id}]}, paper=paper)


def _proposal(evaluation):
    return {key: evaluation[key] for key in ("ranking_snapshot_id", "allocation_evaluation_id", "allocation_policy_version", "lines")}


def test_tampered_snapshot_content_is_rejected_with_an_integrity_reason(snapshot):
    snapshot.snapshot["items"][0]["stage"] = "paper_running"
    with pytest.raises(AllocationLineageError, match="integrity") as raised:
        _evaluate(snapshot)
    assert raised.value.status_code == 422


def test_honest_proposal_verifies(snapshot):
    verify_rebalance_lineage(_proposal(_evaluate(snapshot)))


@pytest.mark.parametrize("field,forged", [
    ("cap_reasons", ["forged-cap"]),
    ("requires_human_approval", True),
    ("requires_human_approval", False),
    ("delta", 0.04),
    ("stage", "paper_running"),
])
def test_forged_evaluated_line_field_is_rejected_even_with_a_valid_line_digest(snapshot, field, forged):
    proposal = _proposal(_evaluate(snapshot))
    line = dict(proposal["lines"][0])
    if line.get(field) == forged:
        forged = not forged if isinstance(forged, bool) else ["other"]
    line[field] = forged
    line["allocation_line_digest"] = allocation_line_digest(line)
    with pytest.raises(AllocationLineageError, match=field) as raised:
        verify_rebalance_lineage({**proposal, "lines": [line]})
    assert raised.value.status_code == 422


def test_context_fields_may_differ(snapshot):
    proposal = _proposal(_evaluate(snapshot))
    proposal["lines"][0]["evidence_refs"] = ["caller-supplied"]
    verify_rebalance_lineage(proposal)


def test_paper_branch_verifies_against_the_paper_simulation_evaluation(monkeypatch):
    items = [{**ITEMS[0], "stage": "paper_running", "eligible": True}]
    reader = _Reader(snapshot_record(items, surface="quarterly", period="2026-Q3").to_canonical_dict())
    monkeypatch.setattr(allocation_lineage, "create_ranking_reader", lambda: reader)
    snapshot_id = reader.snapshot["ranking_snapshot_id"]
    row = {**ROW, "stage": "paper_running", "capital_scope": "paper_ledger", "paper_ledger_id": "ledger-1",
           "capital_pool_id": "pool-paper", "binding_id": "binding-paper", "capital_sleeve_id": None,
           "ranking_snapshot_id": snapshot_id}
    proposal = _proposal(evaluate_allocation({"ranking_snapshot_id": snapshot_id, "rows": [row]}, paper=True))
    verify_rebalance_lineage(proposal)
    forged = dict(proposal["lines"][0], target_weight=0.5)
    with pytest.raises(AllocationLineageError, match="target_weight"):
        verify_rebalance_lineage({**proposal, "lines": [forged]})


def test_pool_health_factors_are_honoured_only_by_the_paper_quarterly_policy(snapshot):
    snapshot_id = snapshot.snapshot["ranking_snapshot_id"]
    row = {**ROW, "ranking_snapshot_id": snapshot_id, "capacity_factor": 0.0}
    plain = evaluate_allocation({"ranking_snapshot_id": snapshot_id, "rows": [{**ROW, "ranking_snapshot_id": snapshot_id}]})
    ignored = evaluate_allocation({"ranking_snapshot_id": snapshot_id, "rows": [row]})  # real evaluation drops it
    assert ignored["lines"][0]["target_weight"] == plain["lines"][0]["target_weight"]
    assert ignored["allocation_evaluation_id"] == plain["allocation_evaluation_id"]
    with pytest.raises(AllocationLineageError, match="paper only"):
        evaluate_allocation({"ranking_snapshot_id": snapshot_id, "rows": [row]}, quarterly=True)


def test_paper_quarterly_proposal_with_a_forged_factor_is_rejected(snapshot):
    items = [{"persona_id": "p-paper", "stage": "paper_running", "tier": "tier-1", "overall_score": 80.0, "eligible": True,
              "evidence_ref_ids": ["ev-1"]}]
    record = snapshot_record(items, surface="quarterly", period="2026-Q3").to_canonical_dict()
    snapshot.snapshot = record
    snapshot_id = record["ranking_snapshot_id"]
    row = {"persona_id": "p-paper", "ranking_snapshot_id": snapshot_id, "capital_scope": "paper_ledger", "paper_ledger_id": "l",
           "capital_pool_id": "pool", "binding_id": "b", "capital_sleeve_id": None, "current_weight": 0.0}
    evaluation = evaluate_allocation({"ranking_snapshot_id": snapshot_id, "rows": [row]}, paper=True, quarterly=True)
    proposal = _proposal(evaluation)
    verify_rebalance_lineage(proposal)
    forged = copy.deepcopy(proposal)
    forged["lines"][0]["target_weight"] = 0.9
    with pytest.raises(AllocationLineageError):
        verify_rebalance_lineage(forged)
    forged = copy.deepcopy(proposal)
    forged["lines"][0]["risk_budget_factor"] = 0.1  # context field: changes the recomputation, so the digest must fail
    with pytest.raises(AllocationLineageError):
        verify_rebalance_lineage(forged)
