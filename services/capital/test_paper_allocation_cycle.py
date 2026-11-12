"""Owner proof for the governed quarterly paper allocation cycle (real lineage, real stores)."""
from __future__ import annotations

import copy

import pytest

from services.capital import allocation_lineage
from services.capital.test_service import _binding_payload, _pool_payload, client  # noqa: F401
from services.rankings.snapshots import snapshot_record

pytestmark = pytest.mark.real_lineage

TENANT_BODY = {"actor_id": "operator-1", "actor_role": "operator"}


def _item(persona_id, tier, score, **extra):
    return {
        "persona_id": persona_id, "stage": "paper_running", "tier": tier, "overall_score": score, "eligible": True,
        "exclusion_codes": [], "paper_ledger_id": f"ledger-{persona_id}", "evidence_ref_ids": [f"ev-{persona_id}"], **extra,
    }


ITEMS = [_item("p1", "tier-1", 90.0), _item("p2", "tier-2", 70.0), _item("p3", "tier-3", 60.0)]


class _Reader:
    def __init__(self):
        self.snapshots = {}

    def add(self, items, period):
        record = snapshot_record(items, surface="quarterly", period=period).to_canonical_dict()
        self.snapshots[record["ranking_snapshot_id"]] = record
        return record["ranking_snapshot_id"]

    def get_ranking_snapshot(self, snapshot_id):
        return copy.deepcopy(self.snapshots.get(snapshot_id))


@pytest.fixture
def paper(client, monkeypatch):  # noqa: F811
    test_client, _ = client
    reader = _Reader()
    monkeypatch.setattr(allocation_lineage, "create_ranking_reader", lambda: reader)
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    pool = _pool_payload(pool_id="pool-paper", owner_type="org", risk_policy_ref=None, approval_decision_id=None,
                         actor_role="operator", actor_id="operator-1", budget=1000.0,
                         metadata={"internal": True, "execution_context": "paper"})
    assert test_client.post("/api/capital-pools", json=pool).status_code == 201
    for persona in ("p1", "p2", "p3"):
        response = test_client.post("/api/bindings", json=_binding_payload(
            binding_id=f"binding-{persona}", persona_id=persona, capital_pool_id="pool-paper", capital_sleeve_id=None,
            role="paper_owner", allowed_deployment_scope="paper"))
        assert response.status_code == 201, response.text
    return test_client, reader


def _run(test_client, snapshot_id, quarter="2026-Q4", **extra):
    return test_client.post("/api/paper-allocation-cycles", json={
        **TENANT_BODY, "quarter": quarter, "ranking_snapshot_id": snapshot_id, "capital_pool_id": "pool-paper", **extra})


def _weights(test_client):
    return {a["persona_id"]: a["current_weight"] for a in test_client.get("/api/allocations").json()["items"]}


def test_cycle_shares_the_paper_pool_by_score_with_tier_caps_and_applies_automatically(paper):
    test_client, reader = paper
    response = _run(test_client, reader.add(ITEMS, "2026-Q4"))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["applied"] is True and data["replayed"] is False
    rebalance = data["rebalance"]
    assert rebalance["allocation_policy_version"] == "persona-paper-quarterly-allocation-v1"
    assert rebalance["status"] == "applied" and rebalance["rollback_target"]["allocations"]
    assert data["receipt"]["live_capital_side_effects"] is False
    assert data["receipt"]["approval_ref"].startswith("paper-auto:")
    assert _weights(test_client) == {"p1": 0.25, "p2": 0.15, "p3": 0.08}  # S/A/B tier caps
    assert rebalance["simulation"]["paper_total"] == 1000.0
    assert rebalance["simulation"]["line_capital"] == {"p1": 250.0, "p2": 150.0, "p3": 80.0}
    assert sum(_weights(test_client).values()) <= 1.0
    assert all(line["requires_human_approval"] is False and line["capital_scope"] == "paper_ledger" for line in rebalance["lines"])


def test_rerun_and_catch_up_for_the_same_quarter_is_idempotent(paper):
    test_client, reader = paper
    first = _run(test_client, reader.add(ITEMS, "2026-Q4")).json()
    changed = reader.add([_item("p1", "tier-3", 10.0), *ITEMS[1:]], "2026-Q4")  # a later snapshot of the same quarter
    again = _run(test_client, changed)
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert again.json()["rebalance"]["rebalance_id"] == first["rebalance"]["rebalance_id"]
    assert len(test_client.get("/api/rebalances").json()) == 1
    assert _weights(test_client) == {"p1": 0.25, "p2": 0.15, "p3": 0.08}


def test_next_quarter_respects_the_25_percent_increase_cap_and_exclusions(paper):
    test_client, reader = paper
    _run(test_client, reader.add(ITEMS, "2026-Q4"))
    excluded = _item("p2", "tier-2", 70.0, eligible=False, exclusion_codes=["hard_risk_breach"])
    q1 = reader.add([_item("p1", "tier-1", 90.0), excluded, _item("p3", "tier-1", 95.0)], "2027-Q1")
    response = _run(test_client, q1, quarter="2027-Q1")
    assert response.status_code == 200, response.text
    weights = _weights(test_client)
    assert weights["p3"] == pytest.approx(0.08 * 1.25)  # quarterly increase cap
    assert weights["p2"] <= 0.15  # exclusion may retain or reduce, never increase
    lines = {l["persona_id"]: l for l in response.json()["rebalance"]["lines"]}
    assert "quarterly_increase_cap_25pct" in lines["p3"]["cap_reasons"]
    assert "hard_risk_breach" in lines["p2"]["exclusions"]
    assert sum(weights.values()) <= 1.0


def test_pool_health_factors_scale_scores_and_unavailable_health_stays_one_with_reason(paper):
    test_client, reader = paper
    items = [_item("p1", "tier-1", 80.0), _item("p2", "tier-1", 80.0)]
    health = {"p1": {"status": "known", "risk_budget_factor": 0.5, "capacity_factor": 1.0, "source": "strategy_pool_health"}}
    data = _run(test_client, reader.add(items, "2026-Q4"), pool_health=health).json()
    lines = {l["persona_id"]: l for l in data["rebalance"]["lines"]}
    assert lines["p1"]["risk_budget_factor"] == 0.5 and lines["p1"]["capacity_factor"] == 1.0
    assert lines["p2"]["risk_budget_factor"] == 1.0
    assert lines["p2"]["pool_health_factors"] == {"status": "unavailable", "reason": "pool_health_not_supplied"}
    assert lines["p1"]["capacity_adjusted_score"] == pytest.approx(40.0)


def test_unknown_inputs_fail_closed_and_real_scope_is_never_reachable(paper):
    test_client, reader = paper
    assert _run(test_client, "unknown-snapshot").status_code == 422
    wrong_quarter = reader.add(ITEMS, "2026-Q3")
    assert _run(test_client, wrong_quarter).status_code == 422
    live = reader.add([{**ITEMS[0], "stage": "live_running"}], "2026-Q4")
    assert _run(test_client, live).status_code == 422  # no paper persona -> no proposal
    assert test_client.get("/api/rebalances").json() == []
    bad_factor = _run(test_client, reader.add(ITEMS, "2026-Q4"), pool_health={"p1": {"risk_budget_factor": 7}})
    assert bad_factor.status_code == 422
    real_pool = _pool_payload(pool_id="pool-real", actor_role="operator", actor_id="operator-1")
    test_client.post("/api/capital-pools", json=real_pool)
    refused = test_client.post("/api/paper-allocation-cycles", json={
        **TENANT_BODY, "quarter": "2026-Q4", "ranking_snapshot_id": reader.add(ITEMS, "2026-Q4"),
        "capital_pool_id": "pool-real"})
    assert refused.status_code in (400, 403, 422)
    assert test_client.get("/api/rebalances").json() == []


def test_paper_environment_is_required(paper, monkeypatch):
    test_client, reader = paper
    monkeypatch.setenv("PANTHEON_ENV", "prod")
    assert _run(test_client, reader.add(ITEMS, "2026-Q4")).status_code in (400, 403)
    assert test_client.get("/api/rebalances").json() == []


def test_paper_quarterly_lines_cannot_be_forged_into_a_real_policy_proposal():
    from services.capital.allocation_lineage import AllocationLineageError, verify_rebalance_lineage
    with pytest.raises(AllocationLineageError):
        verify_rebalance_lineage({"ranking_snapshot_id": "x", "allocation_evaluation_id": "y",
                                  "allocation_policy_version": "persona-paper-quarterly-allocation-v1",
                                  "lines": [{"persona_id": "p1", "capital_scope": "pool"}]})
