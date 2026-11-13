"""Research demand -> existing persona strategy discovery -> research ticket, through the real entry points.

Real: PoolAutonomyCycle.run, HttpPoolCyclePorts (open_demand, discover_for_demand, execute_match_action,
record_discovery), ResearchDemandStore, the discovery scorer and the research write owner's ticket creation.
Faked: only the owner HTTP transport, which dispatches to those real owners, and the seed source reads.
"""
from __future__ import annotations

import importlib

import pytest

from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts, PoolAutonomyCycle, governed_discovery_loader
from services.research.research_demand import ResearchDemandStore
from services.research.tests.test_research_single_owner_routes import _InMemoryStore
from services.research.write_owner import ResearchWriteOwner
from services.source_ingestion.strategy_seed_builder import StrategySpecSeed, StrategySpecSeedStatus

TENANT = "t1"
PERSONA = {"persona_id": "p-mom", "tenant_id": TENANT, "name": "Momentum", "mandate": "Research TWSE equity momentum strategies",
           "strategy_family": "momentum", "lifecycle_state": "paper_owner", "status": "active", "created_at": "2026-06-10T00:00:00Z",
           "required_data_sources": ["ohlcv"],
           "metadata": {"market_scope": ["TWSE"], "asset_classes": ["equity"], "holding_periods": ["swing"],
                        "allowed_source_scopes": ["open"], "data_availability_scope": ["ohlcv", "return labels"], "risk_level": "medium"}}
HEALTH = {"demand_reasons": ["too_few_independent_bets"]}


def seed(seed_id, family="momentum", required=("ohlcv", "return labels"), negative=None, **extra):
    meta = {"strategy_family": family, "source_license_scope": "open", "access_scope": ["research", "strategy_seed"],
            "entitlement_tags": [], "source_status": "active", "execution_route": "none", "data_backfill_available": False}
    fields = dict(
        seed_id=seed_id, source_id="src-1", evidence_bundle_id="bundle-" + seed_id, hypothesis=f"TWSE equity {family} persists over swing periods.",
        asset_class=["equity"], market_scope=["TWSE"], holding_period="swing", required_data=list(required), backend_hint="qlib",
        feature_hints=[family], label_hints=["forward returns"], risk_notes=["medium"], confidence=0.86, status=StrategySpecSeedStatus.DRAFT,
        source_ids=["src-1"], evidence_item_ids=["ev-" + seed_id], citation_refs=["doi:10/x"], trace_refs=["tr-" + seed_id],
        created_at="2026-06-10T00:00:00Z", metadata=meta)
    if negative:
        fields["negative_memory_match"] = negative
    return StrategySpecSeed(**{**fields, **extra})


class World:
    def __init__(self, tmp_path, seeds, *, loader_fails=False):
        self.demands = ResearchDemandStore(tmp_path / "demands.json")
        self.tickets = ResearchWriteOwner(tickets_store=_InMemoryStore(), experiments_store=_InMemoryStore(), notes_store=_InMemoryStore())
        self.memory, self.http = [], []
        self.seeds, self.loader_fails = seeds, loader_fails
        loader = governed_discovery_loader(seeds=self._seeds, specs=lambda p: [], route_policy=lambda p: {}, capability_snapshot=lambda p: {})
        self.ports = HttpPoolCyclePorts(self.transport, pool_store=self, discovery_loader=loader, env={})
        self.cycle = PoolAutonomyCycle(self.ports)

    def _seeds(self):
        if self.loader_fails:
            raise RuntimeError("seed_store_unconfigured")
        return self.seeds

    def current(self, persona_id, tenant_id):
        return {"persona_id": persona_id, "tenant_id": tenant_id, "pool_version": 1, "members": []}

    def transport(self, method, service, path, tenant, body=None):
        self.http.append((method, service, path))
        if service == "research" and path.endswith("/actions"):
            return self.demands.record_discovery(tenant, path.split("/")[-2], body)
        if service == "research" and path.endswith("/persona-demands"):
            return self.demands.open(tenant, body)
        if service == "research" and path.endswith("/reconcile"):
            return {"demands": self.demands.reconcile(tenant, body["persona_id"], body["members"])}
        if service == "research" and path == "/api/research-orchestrator/alpha-replication/entries":
            return []
        if service == "research" and path.startswith("/api/research/tickets/"):
            found = self.tickets.get_research_ticket(path.rsplit("/", 1)[1])
            if found is None:
                raise LookupError("404")
            return found
        if service == "research" and path == "/api/research/tickets":
            return self.tickets.create_research_ticket(
                title=body["title"], description=body["description"], priority=body["priority"], owner=body["owner"],
                actor_id=body["actor_id"], ticket_id=body.get("ticket_id"))
        if service == "memory" and method == "POST":
            self.memory.append(body)
            return {"created": True}
        if service == "memory":
            return {"entries": [{"content": {"tags": m["tags"]}} for m in self.memory]}
        raise AssertionError((method, service, path))

    def run(self, packet="pk1"):
        return self.cycle.run(PERSONA, {"pool": self.current("p-mom", TENANT), "pool_health": HEALTH, "unavailable": []}, packet)


def only_demand(world):
    rows = world.demands.list(TENANT, "p-mom")
    assert len(rows) == 1
    return rows[0]


def test_cycle_demand_to_discovery_match_to_research_ticket(tmp_path):
    world = World(tmp_path, [seed("seed-good")])
    out = world.run()
    assert out["status"] == "completed" and out["decide"]["kind"] == "open_demand"
    demand = only_demand(world)
    found = out["act"]["discovery"]
    assert found[0]["recorded"] and found[0]["matched"] == 1
    action = demand["discovery"]["actions"][0]
    # a strong seed fit is promoted only for operator review; the cycle never reviews its own seeds
    assert (action["action"], action["owner"], action["status"]) == ("promote_seed_candidate", "seed-store", "requires_operator_review")
    assert demand["discovery"]["last_run"]["reason"] is None


def test_research_ticket_created_through_the_research_owner_and_recorded_with_receipt(tmp_path):
    weak = seed("seed-weak", family="carry", required=("ohlcv",), market_scope=["NYSE"], holding_period="intraday")
    world = World(tmp_path, [weak])
    out = world.run()
    demand = only_demand(world)
    tickets = [a for a in demand["discovery"]["actions"] if a["action"] == "create_research_ticket"]
    assert tickets, demand["discovery"]
    receipt = tickets[0]
    assert receipt["owner"] == "research:tickets" and receipt["status"] == "created"
    stored = world.tickets.get_research_ticket(receipt["ticket_id"])
    assert stored["owner"] == "p-mom" and demand["demand_id"] in stored["title"]
    # next day: the demand stays open, the ticket is not created twice, the receipt is unchanged
    again = world.run("pk2")
    assert again["decide"]["kind"] != "open_demand"
    assert len(world.tickets.list_research_tickets()) == 1
    assert only_demand(world)["discovery"]["actions"] == demand["discovery"]["actions"]
    assert only_demand(world)["discovery"]["run_count"] == 2


def test_tried_families_and_negative_memory_are_excluded(tmp_path):
    negative = {"warning_level": "blocking", "similarity": 0.9, "reason": "retired", "matched_memory_id": "nm-1"}
    world = World(tmp_path, [seed("seed-tried", family="momentum"), seed("seed-neg", family="value", negative=negative)])
    # the persona's own earlier failure of the momentum family arrives through memory -> demand.tried_families
    world.memory.append({"tags": ["tried_family:momentum"]})
    out = world.run()
    demand = only_demand(world)
    assert demand["tried_families"] == ["momentum"]
    reasons = {e["matched_object_id"]: e["reason"] for e in demand["discovery"]["last_run"]["excluded"]}
    assert reasons == {"seed-tried": "tried_family", "seed-neg": "negative_memory"}
    assert demand["discovery"]["last_run"]["reason"].startswith("all_candidates_excluded")
    assert out["act"]["discovery"][0]["matched"] == 0


def test_no_match_records_reason_and_requests_backfill_when_data_is_missing(tmp_path):
    world = World(tmp_path, [seed("seed-other", required=("order book",))])
    world.run()
    demand = only_demand(world)
    last = demand["discovery"]["last_run"]
    assert last["matched"] == 0 and last["reason"].startswith("all_candidates_excluded:missing_datasets_not_required")
    backfill = [a for a in demand["discovery"]["actions"] if a["action"] == "request_data_backfill"]
    assert backfill and backfill[0]["datasets"] == ["ohlcv"] and backfill[0]["status"] == "requested"


def test_unavailable_sources_fail_closed_with_recorded_reason(tmp_path):
    world = World(tmp_path, [], loader_fails=True)
    out = world.run()
    assert out["status"] == "completed"
    last = only_demand(world)["discovery"]["last_run"]
    assert last["matched"] == 0 and last["reason"].startswith("discovery_unavailable:RuntimeError")
    assert only_demand(world)["discovery"]["actions"] == []
    assert not world.tickets.list_research_tickets()


def test_ports_without_a_discovery_owner_skip_the_step():
    class Bare:
        def reconcile_demands(self, persona, members):
            raise AssertionError("must not be called")
    assert PoolAutonomyCycle(Bare())._discover(PERSONA, {"members": []}) == []


def test_record_discovery_refuses_a_closed_or_unknown_demand(tmp_path):
    store = ResearchDemandStore(tmp_path / "d.json")
    with pytest.raises(Exception, match="not found"):
        store.record_discovery(TENANT, "rd-missing", {"run_at": "2026-11-13T00:00:00Z", "actions": []})
