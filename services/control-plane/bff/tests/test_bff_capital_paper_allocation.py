"""PAPER-ALLOCATION-QUARTERLY-RUN-20261112: evaluator trigger -> BFF route -> Capital owner -> applied paper rebalance.

Real: persona evaluator code, the BFF capital router and command adapter, the Capital owner service with its real
lineage verification, stores and apply path.  Faked: the owner HTTP hop (harness urlopen -> in-process Capital app),
the evaluator's own HTTP seam (in-process BFF client / canned upstream ranking and persona-pool reads) and the
Rankings store (one in-memory admitted-snapshot store shared by the evaluator and the owner).
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from services.control_plane.bff.tests.rebalance_authority_test_support import HEADERS, CapitalBffAuthorityHarness
from services.rankings.snapshots import snapshot_record

_AGENT = Path(__file__).resolve().parents[3] / "persona-evaluator-agent" / "persona_evaluator_agent.py"
_spec = importlib.util.spec_from_file_location("paper_cycle_persona_evaluator_agent", _AGENT)
pea = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pea
_spec.loader.exec_module(pea)

TENANT = "tenant-test"
EVALUATOR = {"Authorization": "Bearer evaluator-1:operator:tenant-test"}  # BFF operator token scoped to the tenant
POOL = "pool-paper-q"
Q4_RANK_TIME = datetime(2026, 11, 12, tzinfo=timezone.utc).timestamp()
Q1_CLOSE_TIME = datetime(2027, 1, 5, tzinfo=timezone.utc).timestamp()
Q2_CLOSE_TIME = datetime(2027, 4, 5, tzinfo=timezone.utc).timestamp()


# p1 score 90 x risk_budget 0.5 x capacity 0.75 = 33.75; p2 70 / p3 60 have no pool health (factor 1.0)
FIRST_QUARTER = {"p1": 0.20610687, "p2": 0.15, "p3": 0.08}  # shares 33.75/70/60 of 163.75, then S/A/B tier caps


def _ranking_item(persona_id: str, tier: str, score: float, **extra: Any) -> Dict[str, Any]:
    return {
        "persona_id": persona_id, "name": persona_id, "owner_lifecycle_state": "paper_owner", "stage": "paper_running",
        "score": score, "tier": tier, "eligible": True, "exclusion_codes": [], "paper_ledger_id": f"ledger-{persona_id}",
        "components": {"risk_score": 50}, "evidence_refs": [{"refId": f"ev-{persona_id}"}], **extra,
    }


class _RankingStore:
    """Admitted-snapshot store shared by the evaluator (write) and the Capital owner (read)."""

    def __init__(self) -> None:
        self.records: Dict[str, Any] = {}

    def create_ranking_snapshot(self, record: Any) -> Any:
        return self.records.setdefault(record.ranking_snapshot_id, record)

    def get_ranking_snapshot(self, snapshot_id: str) -> Any:
        record = self.records.get(snapshot_id)
        return record.to_canonical_dict() if record is not None else None


class _World:
    """The evaluator's HTTP seam: canned upstream ranking and pool reads; the BFF is the real app."""

    def __init__(self, harness: CapitalBffAuthorityHarness, items: List[Dict[str, Any]], period: str) -> None:
        self.harness, self.items, self.period = harness, items, period
        self.log: List[tuple] = []

    def fetch(self, url: str, data: Any = None, headers: Dict[str, str] | None = None, timeout: float = 20) -> Any:
        self.log.append((url, data))
        if "quarterly-ranking" in url:
            snapshot = snapshot_record(self.items, surface="quarterly", period=self.period)
            return {"data": {"ranking_snapshot_id": snapshot.ranking_snapshot_id, "items": self.items}, "meta": {"surfaces": {}}}
        if "/structured" in url:
            return {"data": {"output": {"structured_data": {"recommendations": []}}}}
        if "/strategy-pool" in url:
            if "/p1/" in url:  # the only persona whose pool has a recorded health decision
                return {"pool_version": 3, "decision": {
                    "policy": {"budget_full": 0.8},
                    "health": {"gross_utilisation": {"status": "known", "value": 0.4},
                               "idle_day_share": {"status": "known", "value": 0.25}}}}
            raise OSError("no strategy pool")
        assert "/bff/management/allocation-policy/evaluate" in url, url
        response = self.harness.client.post("/bff/management/allocation-policy/evaluate", json=data, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[-300:]}")
        return response.json()


@pytest.fixture
def world(tmp_path, monkeypatch):
    ranking = _RankingStore()
    monkeypatch.setattr(pea, "build_rankings_store", lambda: ranking)
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False, ranking_reader=ranking) as harness:
        assert harness.client.post("/bff/capital-pools", json={
            "pool_id": POOL, "name": "Paper pool", "owner_id": "org-1", "owner_type": "org", "budget": 1000.0,
            "metadata": {"internal": True, "execution_context": "paper"}},
            headers={**HEADERS, "Idempotency-Key": "create-pool-paper"}).status_code == 201
        for persona in ("p1", "p2", "p3"):
            created = harness.client.post("/api/v1/bindings", json={
                "binding_id": f"binding-{persona}", "persona_id": persona, "capital_pool_id": POOL,
                "capital_sleeve_id": None, "role": "paper_owner", "allowed_deployment_scope": "paper"},
                headers={**HEADERS, "Idempotency-Key": f"create-binding-{persona}"})
            assert created.status_code == 201, created.text
        items = [_ranking_item("p1", "tier-1", 90.0), _ranking_item("p2", "tier-2", 70.0), _ranking_item("p3", "tier-3", 60.0)]
        yield harness, _World(harness, items, "2026-Q4"), tmp_path


def _evaluate(world, tmp_path, now):
    harness, w, _ = world
    return pea.run_once(
        store=pea.Store(tmp_path / "evaluator.json"), bff_url="http://bff", bff_headers=EVALUATOR, adapter_url="http://ad",
        adapter_token="t", governance_url="http://gov", governance_token="g", tenant=TENANT, actor="evaluator",
        fetch=w.fetch, now=lambda: now, ranking_store=pea.build_rankings_store())


def _cycle(world, tmp_path, now, pool=POOL):
    harness, w, _ = world
    return pea.paper_cycle_once(
        store=pea.Store(tmp_path / "evaluator.json"), bff_url="http://bff", bff_headers=EVALUATOR,
        persona_url="http://persona", persona_token="p", tenant=TENANT, paper_pool_id=pool, fetch=w.fetch, now=lambda: now)


def _weights(harness) -> Dict[str, float]:
    return {a["persona_id"]: a["current_weight"] for a in harness.capital_client.get("/api/allocations").json()["items"]}


def test_quarter_close_runs_one_paper_cycle_across_three_personas_and_applies_it(world) -> None:
    harness, w, tmp_path = world
    assert _evaluate(world, tmp_path, Q4_RANK_TIME)["status"] == "ok"
    assert _cycle(world, tmp_path, Q4_RANK_TIME)["status"] == "waiting_for_quarter_close"  # quarter still open
    assert _weights(harness) == {}

    record = _cycle(world, tmp_path, Q1_CLOSE_TIME)
    assert record["status"] == "applied", record
    assert record["quarter"] == "2026-Q4" and record["replayed"] is False

    assert _weights(harness) == FIRST_QUARTER  # each persona's paper capital changed
    rebalances = harness.capital_client.get("/api/rebalances").json()
    assert len(rebalances) == 1
    proposal = rebalances[0]
    assert proposal["status"] == "applied" and proposal["applied"] is True
    assert proposal["allocation_policy_version"] == "persona-paper-quarterly-allocation-v1"
    assert proposal["apply_receipt"]["live_capital_side_effects"] is False
    assert proposal["apply_receipt"]["audit_ref"] and proposal["rollback_target"]["allocations"]
    assert {l["capital_scope"] for l in proposal["lines"]} == {"paper_ledger"}
    assert sum(_weights(harness).values()) <= 1.0
    lines = {l["persona_id"]: l for l in proposal["lines"]}
    # pool health: p1 has recorded health, p2/p3 stay at 1 with the reason
    assert lines["p1"]["risk_budget_factor"] == 0.5 and lines["p1"]["capacity_factor"] == 0.75
    assert lines["p2"]["risk_budget_factor"] == 1.0 and "pool health unavailable" in lines["p2"]["pool_health_factors"]["reason"]

    # the evaluator's own state shows the cycle once; a rerun never reaches the owner a second time
    calls = len([e for e in w.log if "allocation-policy/evaluate" in e[0]])
    assert _cycle(world, tmp_path, Q1_CLOSE_TIME + 900)["reused"] is True
    assert len([e for e in w.log if "allocation-policy/evaluate" in e[0]]) == calls


def test_catch_up_with_lost_evaluator_state_is_idempotent_at_the_owner(world) -> None:
    harness, w, tmp_path = world
    _evaluate(world, tmp_path, Q4_RANK_TIME)
    first = _cycle(world, tmp_path, Q1_CLOSE_TIME)
    state = pea.Store(tmp_path / "evaluator.json")
    state.update(lambda s: s.pop("paper_cycles"))  # evaluator restarted and forgot the cycle
    again = _cycle(world, tmp_path, Q1_CLOSE_TIME + 3600)
    assert again["status"] == "applied" and again["rebalance_id"] == first["rebalance_id"]
    assert len(harness.capital_client.get("/api/rebalances").json()) == 1
    assert _weights(harness) == FIRST_QUARTER


def test_management_rebalance_read_shows_the_cycle_proposal_lines_and_reasons(world) -> None:
    harness, w, tmp_path = world
    _evaluate(world, tmp_path, Q4_RANK_TIME)
    _cycle(world, tmp_path, Q1_CLOSE_TIME)
    for owner_record in harness.capital_client.get("/api/rebalances").json():  # the BFF read store is fed by owner reads
        harness.read_surface.add_authoritative_rebalance(owner_record)
    listed = harness.client.get("/bff/rebalances", headers=HEADERS).json()["items"]
    assert len(listed) == 1
    cycle = listed[0]
    assert cycle["status"] == "applied" and cycle["allocation_policy_version"] == "persona-paper-quarterly-allocation-v1"
    assert cycle["simulation"]["kind"] == "paper_quarterly_cycle" and cycle["simulation"]["quarter"] == "2026-Q4"
    assert cycle["simulation"]["paper_total"] == 1000.0 and cycle["rollback_target"]["allocations"]
    by_persona = {l["persona_id"]: l for l in cycle["lines"]}
    assert by_persona["p3"]["target_weight"] == 0.08 and "live_b_tier_cap" in by_persona["p3"]["cap_reasons"]
    assert by_persona["p1"]["risk_budget_factor"] == 0.5 and by_persona["p1"]["pool_health_factors"]["status"] == "known"
    detail = harness.client.get(f"/bff/rebalances/{cycle['rebalance_id']}", headers=HEADERS).json()["data"]
    assert detail["apply_receipt"]["live_capital_side_effects"] is False


def test_next_quarter_applies_the_increase_cap_through_the_same_path(world) -> None:
    harness, w, tmp_path = world
    _evaluate(world, tmp_path, Q4_RANK_TIME)
    _cycle(world, tmp_path, Q1_CLOSE_TIME)
    w.items, w.period = [_ranking_item("p1", "tier-1", 90.0), _ranking_item("p2", "tier-1", 80.0), _ranking_item("p3", "tier-1", 95.0)], "2027-Q1"
    _evaluate(world, tmp_path, Q1_CLOSE_TIME)
    record = _cycle(world, tmp_path, Q2_CLOSE_TIME)
    assert record["status"] == "applied" and record["quarter"] == "2027-Q1"
    weights = _weights(harness)
    assert weights["p3"] == pytest.approx(0.08 * 1.25) and weights["p2"] == pytest.approx(0.15 * 1.25)
    assert sum(weights.values()) <= 1.0
    assert len(harness.capital_client.get("/api/rebalances").json()) == 2


def test_real_capital_path_is_unchanged_and_a_paper_cycle_cannot_touch_it(world) -> None:
    harness, w, tmp_path = world
    _evaluate(world, tmp_path, Q4_RANK_TIME)
    snapshot_id = pea.Store(tmp_path / "evaluator.json").load()["latest"]["2026-Q4"]
    # pointing the cycle at the real (non-paper) pool is refused by the owner and creates nothing
    refused = _cycle(world, tmp_path, Q1_CLOSE_TIME, pool="pool-real")
    assert refused["status"] == "degraded"
    assert harness.capital_client.get("/api/rebalances").json() == []
    # a viewer cannot trigger the cycle; an operator cannot smuggle a real-scope line through it
    viewer = harness.client.post("/bff/management/allocation-policy/evaluate", json={
        "authority_mode": "governed_paper_quarterly_cycle", "quarter": "2026-Q4", "ranking_snapshot_id": snapshot_id, "capital_pool_id": POOL},
        headers={"Authorization": "Bearer viewer-1:viewer", "Idempotency-Key": "k"})
    assert viewer.status_code in (401, 403)  # the cycle needs an operator; a plain evaluation stays read-only
    assert harness.capital_client.get("/api/rebalances").json() == []
    # the real policy still excludes paper-stage rows (real evaluation is unchanged)
    from services.capital.allocation_policy import calculate_target_allocations
    line = calculate_target_allocations([{"persona_id": "p1", "stage": "paper_running", "tier": "a", "overall_score": 80.0}])[0]
    assert line["target_weight"] == 0.0 and "stage_not_real_allocation_eligible" in line["exclusions"]


def test_missing_configuration_or_snapshot_fails_closed_without_allocation(world) -> None:
    harness, w, tmp_path = world
    assert _cycle(world, tmp_path, Q1_CLOSE_TIME, pool="")["status"] == "not_configured"
    assert _cycle(world, tmp_path, Q1_CLOSE_TIME)["status"] == "waiting_for_quarter_close"  # no closed-quarter snapshot saved
    assert harness.capital_client.get("/api/rebalances").json() == [] and _weights(harness) == {}
