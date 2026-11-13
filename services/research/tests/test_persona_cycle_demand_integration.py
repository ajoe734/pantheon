"""Two consecutive daily cycles through the real persona schedule entry.

Real: PersonaCycleController.consume_run, CycleOwners.risk, PoolAutonomyCycle,
HttpPoolCyclePorts, PersonaStrategyPoolStore (policy + recorded-TWSE admission
replay) and ResearchDemandStore. Faked: only the owner HTTP transport, which
dispatches to those real owners, plus the OpenClaw turn.
"""
from __future__ import annotations

import copy
import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.foundation.reliable_delivery import AtomicJsonRecordStore
from services.persona.persona_cycle_controller import CycleOwners, PersonaCycleController, read_packets
from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts, PoolAutonomyCycle
from services.research.research_demand import ResearchDemandStore
from services.research.tests.test_pool_admission import entries_and_members, recorded_dataset

TENANT, PID = "tenant-a", "persona-pool-cycle"


class StubOwners(CycleOwners):
    """Real risk owner; Observe reads the real pool store with owner-supplied books."""

    def __init__(self, pool, books, observations):
        super().__init__(None, pool)
        self.books, self.observations = books, observations

    def observe(self, persona, *, since=None):
        pool = self.pool_store.current(PID, TENANT)
        return {"pool": pool, "pool_health": self.pool_store.health(PID, TENANT, self.books, self.observations),
                "books": self.books, "observations": self.observations, "unavailable": [],
                "telemetry_refs": [], "incident_refs": [], "human_feedback_refs": [], "harness_version": 1}


def test_two_daily_cycles_demand_then_admitted_trial_member(tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.syspath_prepend(str(importlib.import_module("pathlib").Path(__file__).parents[2] / "control-plane" / "persona"))
    from services.control_plane.persona.persona_strategy_pool import (
        AppendPoolVersionRequest, PersonaStrategyPoolStore, PoolMember)
    from services.registry.strategy_artifact import strategy_artifact_checksum

    entries, one, two = entries_and_members()
    current = entries[two["artifact_ref"]]
    candidate = copy.deepcopy(entries[one["artifact_ref"]])
    artifact = candidate["metadata"]["strategy_artifact"]
    artifact["strategy_logic"].update(positive_action="BUY", non_positive_action="BUY")
    artifact["parameters"]["zero_momentum_action"] = "BUY"
    candidate["checksum"] = strategy_artifact_checksum(artifact)
    candidate["replication_proof"]["owner_verified_binding"]["artifact_checksum"] = candidate["checksum"]
    registry = {"current": {**current, "registry_id": "current"}, "candidate": {**candidate, "registry_id": "candidate"}}
    pool = PersonaStrategyPoolStore(AtomicJsonRecordStore(tmp_path / "pools.json"),
        registry_reader=lambda rid, tenant: registry[rid], owner_lister=lambda pid, tenant: [])
    pool.append(PID, TENANT, AppendPoolVersionRequest(expected_version=0, change_reason="seed", members=[PoolMember(
        artifact_ref="current", checksum=current["checksum"], stage="active", risk_weight=.7,
        baseline_ref=current["approval_decision_id"], joined_at="2026-01-01T00:00:00+00:00")]), actor_id="fixture")
    dates = [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(30)]
    books = {ref: {"checksum": e["checksum"], "source_ref": "test:virtual-book-owner", "since_inception": True,
                   "basis": "underlying_exposure_fraction_net_taiwan_costs_equal_notional", "dates": dates,
                   "returns": ([.002, -.0005] if ref == "candidate" else [.001, -.0005]) * 15} for ref, e in registry.items()}
    observations = [{"date": d, "source_ref": "test:risk-owner", "gross_notional": 20, "gross_limit": 100,
                     "drawdown": .01, "drawdown_limit": .1} for d in dates[-20:]]

    clock = [datetime.now(timezone.utc) - timedelta(days=1)]
    demands = ResearchDemandStore(tmp_path / "demands.json", now=lambda: clock[0])
    memory, supply, http = [], [], []

    def transport(method, service, path, tenant, body=None):
        http.append((method, service, path))
        assert tenant == TENANT
        if service == "research" and path.endswith("/persona-demands"):
            return demands.open(tenant, body)
        if service == "research" and path.endswith("/persona-demands/reconcile"):
            return {"demands": demands.reconcile(tenant, body["persona_id"], body["members"])}
        if service == "research" and "alpha-replication" in path:
            return list(supply)
        if service == "registry":
            return {"entry": registry[path.rsplit("/", 1)[1]]}
        if service == "persona" and path.endswith("/strategy-pool"):
            return pool.current(PID, TENANT)
        if service == "persona" and path.endswith("/strategy-pool/decisions"):
            module = importlib.import_module("services.control_plane.persona.persona_strategy_pool")
            return pool.decide(PID, TENANT, module.PoolDecisionRequest.model_validate(body), actor_id="persona-pool-cycle")
        if service == "memory" and method == "POST":
            memory.append(body)
            return {"created": True}
        if service == "memory":
            return {"entries": [{"content": {"tags": m["tags"]}} for m in memory]}
        raise AssertionError((method, service, path))

    ports = HttpPoolCyclePorts(transport, dataset_loader=lambda persona, cand: recorded_dataset())
    controller = PersonaCycleController(None, StubOwners(pool, books, observations), store_path=tmp_path / "packets.jsonl",
        turn_runner=lambda *a: SimpleNamespace(status="error", persona_id=PID, decision="", to_dict=lambda: {}),
        pool_cycle=PoolAutonomyCycle(ports))
    persona = {"persona_id": PID, "tenant_id": TENANT, "lifecycle_state": "paper_owner", "name": "Pool", "mandate": "Taiwan momentum",
               "required_data_sources": ["tw-margin-balance"]}

    def day(number):
        return controller.consume_run(persona, "job-1", {"runId": f"run-{number}", "runAtMs": 1794477600000 + number * 86400000,
                                                          "jobId": "job-1", "status": "ok"})

    day1 = day(1)
    cycle1 = day1["pool_autonomy"]
    assert cycle1["status"] == "completed" and cycle1["decide"]["kind"] == "open_demand"
    open_rows = demands.list(TENANT, PID, "open")
    assert len(open_rows) == 1 and open_rows[0]["short_measure"] == "gross_utilisation"
    assert open_rows[0]["mandate"] == "Taiwan momentum" and open_rows[0]["missing_datasets"] == ["tw-margin-balance"]
    assert open_rows[0]["tried_families"] == [artifact["strategy_id"].lower()]
    assert cycle1["act"]["receipts"][0]["demand_id"] == open_rows[0]["demand_id"]

    # Research supplies a gate-passed candidate; the next day the persona admits it as a trial member.
    supply.append({"registry_id": "candidate", "status": "admitted", "checksum": candidate["checksum"],
                   "family": artifact["strategy_id"]})
    clock[0] = datetime.now(timezone.utc) - timedelta(seconds=5)
    day2 = day(2)
    cycle2 = day2["pool_autonomy"]
    assert cycle2["decide"]["kind"] == "admit_trial", cycle2
    receipt = cycle2["act"]["receipts"][0]
    assert receipt["status"] == "applied" and receipt["route"] == "persona:strategy-pool/decisions"
    members = pool.current(PID, TENANT)["members"]
    trial = next(m for m in members if m["artifact_ref"] == "candidate")
    assert trial["stage"] == "trial"
    assert cycle2["learn"]["memory_entries"] == ["admitted"] and memory[0]["source_event_type"] == "pool_outcome"
    # The member joined after the demand: the next reconcile satisfies it; no duplicate was opened.
    assert [d["status"] for d in demands.reconcile(TENANT, PID, members) if d["demand_id"] == open_rows[0]["demand_id"]] == ["satisfied"]
    assert len(demands.list(TENANT, PID)) == 1
    assert len(read_packets(controller.path)) == 2
    assert all(p["pool_autonomy"]["act"]["paper_only"] and not p["act"]["live_capital_side_effects"]
               for p in read_packets(controller.path).values())
    assert not any(service in ("capital", "provisioning") for _, service, _ in http)
