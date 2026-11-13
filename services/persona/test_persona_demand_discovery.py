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
        self.registry_specs, self.ticket_failures = [], 0
        self.seeds, self.loader_fails = seeds, loader_fails
        loader = governed_discovery_loader(seeds=self._seeds, route_policy=lambda p: {}, capability_snapshot=lambda p: {})
        self.ports = HttpPoolCyclePorts(self.transport, pool_store=self, discovery_loader=loader, env={})
        self.cycle = PoolAutonomyCycle(self.ports)

    def _seeds(self, persona):
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
        if service == "registry" and path == "/api/registry/strategy-specs":
            return self.registry_specs
        if service == "research" and path == "/api/research/tickets":
            if self.ticket_failures:
                self.ticket_failures -= 1
                raise ConnectionError("research owner down")
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
    assert (action["action"], action["owner"], action["status"]) == ("promote_seed_candidate", "research:tickets", "review_requested")
    ticket = world.tickets.get_research_ticket(action["ticket_id"])
    assert "reviewer decision required" in ticket["title"]  # the approval gate stays with the seed reviewer
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
    assert backfill and backfill[0]["datasets"] == ["ohlcv"] and backfill[0]["status"] == "backfill_requested"
    assert world.tickets.get_research_ticket(backfill[0]["ticket_id"])["title"].startswith("Data backfill request")


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


def test_failed_action_is_retried_and_the_success_receipt_replaces_it_with_history(tmp_path):
    weak = seed("seed-weak", family="carry", required=("ohlcv",), market_scope=["NYSE"], holding_period="intraday")
    world = World(tmp_path, [weak])
    world.ticket_failures = 1
    world.run()
    failed = only_demand(world)["discovery"]["actions"][0]
    assert failed["status"] == "failed" and "ticket_id" not in failed
    world.run("pk2")
    action = only_demand(world)["discovery"]["actions"][0]
    assert action["status"] == "created" and action["ticket_id"]
    assert [a["status"] for a in action["attempts"]] == ["failed", "created"]
    assert len(world.tickets.list_research_tickets()) == 1
    world.run("pk3")  # a completed action is neither re-executed nor rewritten
    assert only_demand(world)["discovery"]["actions"] == [action]
    assert len(world.tickets.list_research_tickets()) == 1


def test_spec_match_requests_rapid_eval_from_registry_owner_specs(tmp_path):
    world = World(tmp_path, [])
    world.registry_specs = [{"entry": {"registry_id": "reg-1", "strategy_id": "spec-mom", "artifact_state": "approved",
                                       "metadata": {"strategy_family": "momentum"}}}]
    assert world.ports.read_strategy_specs(PERSONA)[0]["strategy_spec_registry_id"] == "reg-1"
    receipt = world.ports.execute_match_action(PERSONA, {"demand_id": "d1", "short_measure": "m"}, {
        "match_id": "m1", "matched_object_id": "spec-mom", "matched_object_type": "strategy_spec", "score": 0.9,
        "recommended_action": {"type": "run_rapid_eval", "reason": "fits"}})
    assert receipt["status"] == "eval_requested" and receipt["owner"] == "research:tickets"
    assert world.tickets.get_research_ticket(receipt["ticket_id"])["title"].startswith("Rapid evaluation request")
    refused = world.ports.execute_match_action(PERSONA, {"demand_id": "d1", "short_measure": "m"}, {
        "match_id": "m2", "recommended_action": {"type": "delete_everything"}})
    assert refused["status"] == "refused"


def test_lifespan_seed_reader_binds_persona_tenant_and_service_credential(tmp_path, monkeypatch):
    """The real lifespan wiring (no injected seed loader) reads seeds with the background-bound identity."""
    from services.control_plane.bff import source_management_client as smc
    seen = []

    def fake_list_all(self):
        from services.control_plane.bff.core.owner_reads import authorization, selected_tenant
        seen.append((authorization.get(), selected_tenant.get()))
        return []
    monkeypatch.setattr(smc.SourceManagementClient, "list_all", fake_list_all)
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_API_URL", "http://source-ingest.invalid")
    monkeypatch.setenv("PERSONA_SOURCE_INGEST_SERVICE_TOKEN", "svc-token")
    monkeypatch.setenv("PERSONA_DAILY_CYCLE_ENABLED", "true")
    captured = {}
    import services.persona.pool_autonomy_cycle as pac
    real = pac.HttpPoolCyclePorts
    monkeypatch.setattr(pac, "HttpPoolCyclePorts", lambda *a, **k: captured.setdefault("ports", real(*a, **k)))
    monkeypatch.setattr("services.persona.persona_cycle_controller.run_controller", lambda *a, **k: asyncio.sleep(0))
    import asyncio
    import importlib.util
    import sys
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "control-plane/persona/main.py"
    spec = importlib.util.spec_from_file_location("persona_demand_lifespan_test", path)
    main = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, main)
    monkeypatch.syspath_prepend(str(path.parent))
    spec.loader.exec_module(main)

    async def boot():
        async with main.persona_lifespan(main.app):
            pass
    asyncio.run(boot())
    loader = captured["ports"].discovery_loader
    loader(PERSONA)
    assert seen == [("Bearer svc-token", TENANT)]
    monkeypatch.delenv("PERSONA_SOURCE_INGEST_SERVICE_TOKEN")
    with pytest.raises(RuntimeError, match="seed_store_credential_unconfigured"):
        loader(PERSONA)


def _registry_view(strategy_id, registry_id, family, deps=("ohlcv", "return labels")):
    spec = {"strategy_id": strategy_id, "title": f"TWSE {family} rotation", "hypothesis": f"{family} persists on TWSE equities.",
            "market_scope": {"symbols": ["2330"], "venues": ["TWSE"], "asset_classes": ["equity"], "frequency": "daily"},
            "data_dependencies": [{"ref": d, "kind": "dataset"} for d in deps],
            "execution_profile": {"signal_schema_version": "research-signal-v1", "quantity_type": "PERCENT_PORTFOLIO",
                                  "execution_mode_hint": "paper", "rebalance_cadence": "swing"},
            "governance": {"approval_required": True, "risk_profile": "medium"},
            "provenance": {"source_kind": "workflow", "source_refs": ["n"], "created_at": "2026-06-09T00:00:00Z"},
            "citation_bundle": {"evidence_refs": [{"ref_id": "evref-1"}]}}
    return {"entry": {"registry_id": registry_id, "strategy_id": strategy_id, "artifact_state": "approved", "artifact_type": "strategy_spec",
                      "metadata": {"strategy_family": family, "strategy_spec": spec, "mechanism_status": "tested", "mechanism_support": 1.0},
                      "lineage": {}}, "deployment_stage": None}


def test_registry_shaped_specs_flow_through_cycle_with_dataset_and_tried_family_filters(tmp_path):
    world = World(tmp_path, [])
    world.registry_specs = [_registry_view("spec-mom", "reg-mom", "momentum"),
                            _registry_view("spec-carry", "reg-carry", "carry"),
                            _registry_view("spec-book", "reg-book", "value", deps=("order book",))]
    world.memory.append({"tags": ["tried_family:momentum"]})
    world.run()
    demand = only_demand(world)
    assert demand["discovery"]["last_run"]["matched"] >= 1
    excluded = {e["matched_object_id"]: e["reason"] for e in demand["discovery"]["last_run"]["excluded"]}
    assert excluded.get("spec-mom") == "tried_family" and excluded.get("spec-book") == "missing_datasets_not_required"
    rapid = [a for a in demand["discovery"]["actions"] if a["action"] == "run_rapid_eval"]
    assert rapid and rapid[0]["status"] == "eval_requested"
    assert world.tickets.get_research_ticket(rapid[0]["ticket_id"])["title"].startswith("Rapid evaluation request")


def test_transient_ticket_read_failure_never_overwrites_an_existing_ticket(tmp_path):
    world = World(tmp_path, [])
    demand = {"demand_id": "d1", "short_measure": "m"}
    match = {"match_id": "m1", "matched_object_id": "s", "matched_object_type": "strategy_spec", "score": 0.9,
             "recommended_action": {"type": "run_rapid_eval", "reason": "fits"}}
    first = world.ports.execute_match_action(PERSONA, demand, match)
    before = world.tickets.get_research_ticket(first["ticket_id"])
    real = world.transport

    def flaky(method, service, path, tenant, body=None):
        if method == "GET" and path.startswith("/api/research/tickets/"):
            raise ConnectionError("transient")
        return real(method, service, path, tenant, body)
    world.ports.transport = flaky
    with pytest.raises(ConnectionError):
        world.ports.execute_match_action(PERSONA, demand, match)  # fail closed: no POST
    world.ports.transport = real
    assert world.tickets.get_research_ticket(first["ticket_id"]) == before
    assert world.ports.execute_match_action(PERSONA, demand, match)["status"] == "already_exists"


def _boot_lifespan(monkeypatch, tmp_path, surface):
    """Boot the real persona lifespan; the owner read surface is the only double (the BFF read owner)."""
    import asyncio
    import importlib.util
    import sys
    from pathlib import Path
    from services.control_plane.bff import source_management_client as smc
    monkeypatch.setattr(smc.SourceManagementClient, "list_all", lambda self: [seed("s-qlib")])
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_API_URL", "http://source-ingest.invalid")
    monkeypatch.setenv("PERSONA_SOURCE_INGEST_SERVICE_TOKEN", "svc-token")
    monkeypatch.setenv("PERSONA_DAILY_CYCLE_ENABLED", "true")
    captured = {}
    import services.persona.pool_autonomy_cycle as pac
    monkeypatch.setattr(pac, "HttpPoolCyclePorts", lambda *a, **k: captured.setdefault("ports", HttpPoolCyclePorts(*a, **k)))
    monkeypatch.setattr("services.persona.persona_cycle_controller.run_controller", lambda *a, **k: asyncio.sleep(0))
    path = Path(__file__).resolve().parents[1] / "control-plane/persona/main.py"
    spec = importlib.util.spec_from_file_location("persona_demand_owner_reads_test", path)
    main = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, main)
    monkeypatch.syspath_prepend(str(path.parent))
    spec.loader.exec_module(main)
    main._OWNER_READ_SURFACE = surface

    async def boot():
        async with main.persona_lifespan(main.app):
            pass
    asyncio.run(boot())
    return captured["ports"]


class _Surface:
    def __init__(self, policy=None, snapshot=None, fail=False):
        self.policy, self.snapshot, self.fail, self.tenants = policy, snapshot, fail, []

    def list_route_policies(self):
        from services.control_plane.bff.core.owner_reads import selected_tenant
        self.tenants.append(selected_tenant.get())
        if self.fail:
            raise ConnectionError("route policy owner down")
        return [self.policy] if self.policy else []

    def get_capability_snapshot_for_persona(self, persona_id):
        return self.snapshot


def test_lifespan_daily_loader_resolves_reference_shaped_persona_policy_and_blocks_backend(monkeypatch, tmp_path):
    referenced = {**PERSONA, "route_policy_id": "rp-block-qlib"}
    surface = _Surface(policy={"policy_id": "rp-block-qlib", "persona_id": "p-mom", "blocked_backends": ["qlib"]})
    ports = _boot_lifespan(monkeypatch, tmp_path, surface)
    sources = ports.discovery_loader(referenced)
    assert sources["route_policy"]["blocked_backends"] == ["qlib"]
    assert surface.tenants == [TENANT]
    demand = {"demand_id": "d1", "persona_id": "p-mom", "tenant_id": TENANT, "status": "open", "missing_families": [],
              "missing_datasets": [], "tried_families": [], "reasons": ["too_few_independent_bets"]}
    ports.read_strategy_specs = lambda persona: []
    result = ports.discover_for_demand(referenced, demand)
    assert not result.get("matches")


def test_lifespan_daily_loader_fails_closed_when_referenced_policy_owner_is_unavailable(monkeypatch, tmp_path):
    referenced = {**PERSONA, "route_policy_id": "rp-block-qlib"}
    ports = _boot_lifespan(monkeypatch, tmp_path, _Surface(fail=True))
    with pytest.raises(ConnectionError):
        ports.discovery_loader(referenced)
    ports = _boot_lifespan(monkeypatch, tmp_path, _Surface(policy=None))
    with pytest.raises(RuntimeError, match="route_policy_unavailable:rp-block-qlib"):
        ports.discovery_loader(referenced)


def test_lifespan_daily_loader_without_the_policy_would_have_recommended_the_blocked_seed(monkeypatch, tmp_path):
    referenced = {**PERSONA, "route_policy_id": "rp-open"}
    ports = _boot_lifespan(monkeypatch, tmp_path, _Surface(policy={"policy_id": "rp-open", "persona_id": "p-mom"}))
    ports.read_strategy_specs = lambda persona: []
    demand = {"demand_id": "d1", "persona_id": "p-mom", "tenant_id": TENANT, "status": "open", "missing_families": [],
              "missing_datasets": [], "tried_families": [], "reasons": ["too_few_independent_bets"]}
    assert ports.discover_for_demand(referenced, demand).get("matches")


def _real_surface(policies):
    """Real ReadSurfacePorts whose only double is the in-memory owner catalog of route policies."""
    from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts

    class _Catalog:
        def list_route_policies(self):
            return list(policies)
    return ReadSurfacePorts(operations_consultation=_Catalog())


def _demand():
    return {"demand_id": "d1", "persona_id": "p-mom", "tenant_id": TENANT, "status": "open", "missing_families": [],
            "missing_datasets": [], "tried_families": [], "reasons": ["too_few_independent_bets"]}


def test_lifespan_loader_resolves_shared_policy_by_policy_id_through_real_read_surface(monkeypatch, tmp_path):
    referenced = {**PERSONA, "route_policy_id": "rp-block-qlib"}
    surface = _real_surface([{"policy_id": "rp-block-qlib", "blocked_backends": ["qlib"]}])  # shared: no persona_id
    ports = _boot_lifespan(monkeypatch, tmp_path, surface)
    assert ports.discovery_loader(referenced)["route_policy"]["blocked_backends"] == ["qlib"]
    ports.read_strategy_specs = lambda persona: []
    assert not ports.discover_for_demand(referenced, _demand()).get("matches")


def test_lifespan_loader_ignores_stale_persona_scoped_row_and_uses_the_referenced_policy(monkeypatch, tmp_path):
    referenced = {**PERSONA, "route_policy_id": "rp-block-qlib"}
    surface = _real_surface([{"policy_id": "rp-old-open", "persona_id": "p-mom"},
                             {"policy_id": "rp-block-qlib", "blocked_backends": ["qlib"]}])
    ports = _boot_lifespan(monkeypatch, tmp_path, surface)
    assert ports.discovery_loader(referenced)["route_policy"]["policy_id"] == "rp-block-qlib"
    ports.read_strategy_specs = lambda persona: []
    assert not ports.discover_for_demand(referenced, _demand()).get("matches")


def test_lifespan_loader_fails_closed_when_only_a_mismatched_persona_row_exists(monkeypatch, tmp_path):
    referenced = {**PERSONA, "route_policy_id": "rp-block-qlib"}
    ports = _boot_lifespan(monkeypatch, tmp_path, _real_surface([{"policy_id": "rp-old-open", "persona_id": "p-mom"}]))
    with pytest.raises(RuntimeError, match="route_policy_unavailable:rp-block-qlib"):
        ports.discovery_loader(referenced)
