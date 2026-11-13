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
from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts, PoolAutonomyCycle, governed_dataset_loader
from services.research.research_demand import ResearchDemandStore
from services.research.tests.test_pool_admission import entries_and_members, recorded_dataset

TENANT, PID = "tenant-a", "persona-pool-cycle"


def governed_entry(artifact, dataset, *, name, tenant=TENANT):
    """Registry entry from the real gate run on ``dataset`` for ``artifact`` (its own fresh proof).

    Same path as services.evolution.test_approved_baselines.genuine_entry, but the evaluation comes from
    run_replication_backtest_protocol on the governed Taiwan dataset instead of a recorded fixture.
    No checksum, binding or threshold is altered.
    """
    from services.registry.promotion.gate import PromotionGate
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.research.replication.gate import ReplicationGate, create_promotion_request
    from services.research.replication.test_gate import TestReplicationGate
    from services.research.vectorbt.adapter.vectorbt_adapter import run_replication_backtest_protocol

    checksum = strategy_artifact_checksum(artifact)
    evaluation = run_replication_backtest_protocol(strategy_artifact=artifact, dataset=dataset, run_id="bt-" + name)
    request = TestReplicationGate("test_audit_log")._build_request()
    request.candidate_id = "cand-" + name
    request.backtest_evaluation = evaluation
    request.metadata = {"strategy_artifact_checksum": checksum, "dataset_version_id": evaluation["dataset_id"]}
    gate = ReplicationGate()
    gate.backtest_run_store.record(evaluation)
    response = gate.evaluate_candidate(request)
    assert response.passed, [(r.criterion_id, r.evidence) for r in response.results if not r.passed]
    promotion = create_promotion_request(response, request.proposed_strategy_spec)
    summary = promotion.registry_entry["evaluation_summary"]
    verified = PromotionGate.verify_admitted_replication_proof(
        promotion.replication_proof, summary, promotion.lineage, backtest_evaluation=evaluation, artifact_checksum=checksum)
    verified["owner_verified_binding"]["producer_verified"] = True  # registry owner's own post-readback flag
    return {"registry_id": "reg-" + name, "artifact_state": "approved", "checksum": checksum, "owner_tenant": tenant,
            "approval_decision_id": "dec-" + name, "evaluation_summary": summary, "lineage": promotion.lineage,
            "producer_run_id": summary["backtest_run_id"], "replication_success": promotion.registry_entry["replication_success"],
            "replication_proof": verified, "artifact_type": "execution_bundle", "metadata": {"strategy_artifact": artifact}}


class StubOwners(CycleOwners):
    """Real risk owner; Observe reads the real pool store with owner-supplied books."""

    def __init__(self, pool, books, observations):
        super().__init__(None, pool)
        self.books, self.observations = books, observations

    def observe(self, persona, *, since=None):
        pool = self.pool_store.current(PID, TENANT)
        # Like telemetry's member_virtual_books: only bound (admitted, live) members have a book.
        live = {m["artifact_ref"] for m in pool["members"] if m["stage"] != "removed"}
        books = {ref: b for ref, b in self.books.items() if ref in live}
        return {"pool": pool, "pool_health": self.pool_store.health(PID, TENANT, books, self.observations),
                "books": books, "observations": self.observations, "unavailable": [],
                "telemetry_refs": [], "incident_refs": [], "human_feedback_refs": [], "harness_version": 1}


def _two_days(tmp_path, monkeypatch, *, registry, dataset, spec_id="spec-reviewed-001"):
    """Run two daily cycles through the real schedule entry; ``registry`` holds 'current' and 'candidate'."""
    from datetime import date
    monkeypatch.syspath_prepend(str(importlib.import_module("pathlib").Path(__file__).parents[2] / "control-plane" / "persona"))
    from services.control_plane.persona.persona_strategy_pool import (
        AppendPoolVersionRequest, PersonaStrategyPoolStore, PoolMember)

    current = registry["current"]
    artifact = registry["candidate"]["metadata"]["strategy_artifact"]
    pool = PersonaStrategyPoolStore(AtomicJsonRecordStore(tmp_path / "pools.json"),
        registry_reader=lambda rid, tenant: registry[rid], owner_lister=lambda pid, tenant: [])
    pool.append(PID, TENANT, AppendPoolVersionRequest(expected_version=0, change_reason="seed", members=[PoolMember(
        artifact_ref="current", checksum=current["checksum"], stage="active", risk_weight=.7,
        baseline_ref=current["approval_decision_id"], joined_at="2026-01-01T00:00:00+00:00")]), actor_id="fixture")
    dates = [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(30)]
    books = {ref: {"checksum": e["checksum"], "source_ref": "test:virtual-book-owner", "since_inception": True,
                   "basis": "underlying_exposure_fraction_net_taiwan_costs_equal_notional", "dates": dates,
                   "returns": ([.002, -.0005] if ref == "candidate" else [.0002, -.0004]) * 15} for ref, e in registry.items()}
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
        if service == "registry" and path.endswith("/entries") and "/strategies/" in path:
            return [e for e in registry.values() if e["metadata"]["strategy_artifact"]["strategy_id"] == path.split("/")[4]]
        if service == "registry":
            return {"entry": registry[path.rsplit("/", 1)[1]]}
        if service == "memory" and method == "POST":
            memory.append(body)
            return {"created": True}
        if service == "memory":
            return {"entries": [{"content": {"tags": m["tags"]}} for m in memory]}
        raise AssertionError((method, service, path))

    # Real scheduled construction: in-process persona owner plus the governed dataset loader reading the
    # configured authority paths. Only the authority snapshot reader serves the governed dataset.
    monkeypatch.syspath_prepend(str(importlib.import_module("pathlib").Path(__file__).parents[2] / "training-session"))
    authority = importlib.import_module("evaluation_authority")
    monkeypatch.setattr(authority, "load_evaluation_authority", lambda *a, **k: SimpleNamespace(vectorbt_dataset=dataset))
    env = {"PERSONA_POOL_CANONICAL_DATASET_PATH": "/governed/dataset.json",
           "PERSONA_POOL_THRESHOLD_POLICY_PATH": "/governed/policy.json"}
    ports = HttpPoolCyclePorts(transport, pool_store=pool, dataset_loader=governed_dataset_loader(env), env=env)
    controller = PersonaCycleController(None, StubOwners(pool, books, observations), store_path=tmp_path / "packets.jsonl",
        turn_runner=lambda *a: SimpleNamespace(status="error", persona_id=PID, decision="", to_dict=lambda: {}),
        pool_cycle=PoolAutonomyCycle(ports))
    persona = {"persona_id": PID, "tenant_id": TENANT, "lifecycle_state": "paper_owner", "name": "Pool", "mandate": "Taiwan momentum",
               "required_data_sources": ["tw-margin-balance"]}

    def day(number):
        return controller.consume_run(persona, "job-1", {"runId": f"run-{number}", "runAtMs": 1794477600000 + number * 86400000,
                                                          "jobId": "job-1", "status": "ok"})

    cycle1 = day(1)["pool_autonomy"]
    assert cycle1["status"] == "completed" and cycle1["decide"]["kind"] == "open_demand"
    open_rows = demands.list(TENANT, PID, "open")
    assert len(open_rows) == 1 and open_rows[0]["short_measure"] == "gross_utilisation"
    assert open_rows[0]["mandate"] == "Taiwan momentum" and open_rows[0]["missing_datasets"] == ["tw-margin-balance"]
    tried = current["metadata"]["strategy_artifact"]["strategy_id"].lower()
    assert open_rows[0]["tried_families"] == [tried]
    assert cycle1["act"]["receipts"][0]["demand_id"] == open_rows[0]["demand_id"]

    # Research completes a reviewed-spec admission (real research-owner response shape, a StrategySpec identity);
    # the persona resolves it to the approved execution bundle and the next day decides on it.
    from services.research.tests.test_research_orchestrator_http_service import _load_service_module
    admission = {"tenant_id": TENANT, "strategy_spec_id": spec_id, "strategy_id": artifact["strategy_id"],
                 "spec_version": "1.0.0", "checksum": "sha256:" + "a" * 64, "status": "admitted"}
    supply.append(_load_service_module()._build_alpha_replication_entry(admission, {**admission, "status": "completed"}))
    clock[0] = datetime.now(timezone.utc) - timedelta(seconds=5)
    cycle2 = day(2)["pool_autonomy"]
    assert cycle2["decide"]["kind"] == "admit_trial", (cycle2["decide"], cycle2["orient"])
    assert cycle2["decide"]["candidate_ref"] == "candidate"  # resolved to the approved execution bundle, not the spec id
    assert cycle2["act"]["receipts"][0]["route"] == "persona:strategy-pool/decisions"
    assert [d["status"] for d in demands.list(TENANT, PID)] == ["open"] and len(demands.list(TENANT, PID)) == 1
    assert len(read_packets(controller.path)) == 2
    assert all(p["pool_autonomy"]["act"]["paper_only"] and not p["act"]["live_capital_side_effects"]
               for p in read_packets(controller.path).values())
    assert not any(service in ("capital", "provisioning") for _, service, _ in http)
    return SimpleNamespace(cycle2=cycle2, pool=pool, memory=memory, artifact=artifact)


def _spec_linked(entry, spec_id="spec-reviewed-001"):
    return {**entry, "lineage": {**entry["lineage"], "source_strategy_spec_id": spec_id}}


def test_two_daily_cycles_demand_then_admitted_trial_member(tmp_path, monkeypatch):
    """Acceptance 7 on the governed 2-year Taiwan dataset, unmodified policy, candidate with its own gate proof."""
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.research.vectorbt.adapter.vectorbt_adapter import (
        build_canonical_tw_strategy_artifact, build_governed_tw_replication_dataset)

    # 760 governed daily bars. The pool's current member is a thresholded momentum variant (OOS Sharpe ~2.05); the
    # candidate is the canonical unthresholded one (~8.0), corr ~0.46 < the 0.5 cutoff, improving the pool's Sharpe.
    dataset = build_governed_tw_replication_dataset(symbols=["2330", "2317"], down_drop=-0.055)
    symbols = ["2330.TW", "2317.TW"]
    base = build_canonical_tw_strategy_artifact(strategy_id="strat-tw-pool-base", symbols=symbols, momentum_threshold=0.002)
    new = build_canonical_tw_strategy_artifact(strategy_id="strat-tw-pool-momentum", symbols=symbols, source_strategy_spec_id="spec-reviewed-001")
    registry = {"current": {**governed_entry(base, dataset, name="pool-base"), "registry_id": "current"},
                "candidate": {**_spec_linked(governed_entry(new, dataset, name="pool-momentum")), "registry_id": "candidate"}}
    assert registry["candidate"]["checksum"] == strategy_artifact_checksum(new)  # nothing rewritten after the gate
    out = _two_days(tmp_path, monkeypatch, registry=registry, dataset=dataset)

    receipt = out.cycle2["act"]["receipts"][0]
    assert receipt["status"] != "refused", (receipt, out.cycle2["decide"])
    members = {m["artifact_ref"]: m for m in out.pool.current(PID, TENANT)["members"]}
    assert members["candidate"]["stage"] == "trial"
    assert members["candidate"]["checksum"] == registry["candidate"]["checksum"]
    # Full-budget admission replaces the weakest member; both the admission and the removal are learned.
    assert out.cycle2["learn"]["memory_entries"] == ["admitted", "removed"], out.cycle2["learn"]
    assert members["current"]["stage"] == "removed"
    tags = [m["tags"] for m in out.memory]
    assert any("admitted" in t for t in tags) and any("removed" in t and "tried_family:strat-tw-pool-base" in t for t in tags)


def test_two_daily_cycles_genuine_fixture_artifact_refusal_recorded(tmp_path, monkeypatch):
    """The unmodified recorded-fixture artifacts are corr ~0.90 with the current member: refusal is recorded."""
    entries, one, two = entries_and_members()
    candidate = _spec_linked(copy.deepcopy(entries[one["artifact_ref"]]))
    registry = {"current": {**entries[two["artifact_ref"]], "registry_id": "current", "artifact_type": "execution_bundle"},
                "candidate": {**candidate, "registry_id": "candidate", "artifact_type": "execution_bundle"}}
    out = _two_days(tmp_path, monkeypatch, registry=registry, dataset=recorded_dataset())
    receipt = out.cycle2["act"]["receipts"][0]
    assert receipt["status"] == "refused", receipt
    assert not any(m["artifact_ref"] == "candidate" for m in out.pool.current(PID, TENANT)["members"])
    assert out.cycle2["learn"]["memory_entries"] == ["admission_refused"], out.cycle2["learn"]
    assert any(f"tried_family:{out.artifact['strategy_id']}" in m["tags"] for m in out.memory)
