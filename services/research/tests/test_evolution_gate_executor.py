"""Approved mutate / revalidate decisions run through the real dispatch route to a terminal receipt.

Registry transport, the governed dataset and the §10 gate verdict are stand-ins
(the gate keeps the real, shared FamilyTrialCounterStore); the research service's
own dispatch, executor, lineage registration and run/task terminal handling are real.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TENANT = "tenant-dev"


@pytest.fixture()
def research(monkeypatch, tmp_path):
    service_dir = ROOT / "services" / "research"
    monkeypatch.setenv("RESEARCH_ORCHESTRATOR_DATA_DIR", tempfile.mkdtemp())
    monkeypatch.setenv("RESEARCH_REPLICATION_TRIAL_COUNTER_PATH", str(tmp_path / "family_trial_counts.json"))
    monkeypatch.setenv("ALPHA_REPLICATION_DATA_DIR", str(tmp_path / "alpha"))
    monkeypatch.syspath_prepend(str(service_dir))
    spec = importlib.util.spec_from_file_location("research_evolution_gate_exec", service_dir / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["research_evolution_gate_exec"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("store", None)
    return module


class World:
    def __init__(self, parent, passes):
        self.parent = parent
        self.passes = passes
        self.registered = {}
        self.foreign_versions = []  # versions registered behind this run's back
        self.calls = []


@pytest.fixture()
def world(research, monkeypatch):
    from services.registry.test_factor_rule import _sample_factor_rule_artifact
    from services.research.alpha_replication import evolution_executor as executor
    from services.research.alpha_replication import shared_stores
    from services.research.replication import gate as gate_module

    parent = _sample_factor_rule_artifact()
    parent["lineage"]["source_strategy_spec_id"] = "spec-1"
    w = World(parent, passes=True)
    path_entry = {"entry": {"registry_id": parent["artifact_id"], "artifact_state": "approved",
                            "metadata": {"strategy_artifact": parent, "persona_id": "persona-1"}}}
    spec_entry = {"entry": {"registry_id": "spec-1", "artifact_state": "approved", "owner_tenant": TENANT,
                            "metadata": {"strategy_spec": {"strategy_id": parent["strategy_id"]}}}}

    def call(*, step, service, method, url, tenant_id, token_variable, body=None, **_):
        w.calls.append((method, url))
        if method == "GET" and url.endswith(f"/strategy-artifacts/{parent['artifact_id']}"):
            return path_entry
        if method == "GET" and url.endswith("/strategy-specs/spec-1"):
            return spec_entry
        if method == "GET" and url.endswith("/strategy-artifacts") and "/strategies/" in url:
            rows = [{"entry": {"registry_id": parent["artifact_id"], "version": parent["version"]}}]
            for registry_id, posted in w.registered.items():
                rows.append({"entry": {"registry_id": registry_id, "version": posted["strategy_artifact"]["version"]}})
            return [*rows, *[{"entry": {"version": v}} for v in w.foreign_versions]]
        if method == "GET" and "/strategy-artifacts/" in url:
            child_id = url.rsplit("/", 1)[-1]
            if child_id in w.registered:
                return {"entry": {"registry_id": child_id, "artifact_state": "candidate"}}
            raise research._OwnerRejection(404, "not found")
        if method == "POST" and url.endswith("/api/registry/strategy-artifacts"):
            version = body["strategy_artifact"]["version"]
            taken = {p["strategy_artifact"]["version"] for p in w.registered.values()} | set(w.foreign_versions)
            if version in taken:  # immutable (strategy, version, type): what the real Registry does
                raise research._OwnerRejection(409, "version already registered")
            w.registered[body["registry_id"]] = body
            return {"entry": {"registry_id": body["registry_id"], "artifact_state": "candidate"}}
        raise AssertionError(f"unexpected owner call {method} {url}")

    class FakeGate:
        def __init__(self, trial_counter_path=None, backtest_run_store=None, **_):
            self.store = gate_module.FamilyTrialCounterStore(trial_counter_path)

        def get_family_trial_count(self, family):
            return self.store.get(family)

        def evaluate_candidate(self, request):
            fam = request["family"]
            self.store.increment(fam)
            ok = w.passes

            class R:
                passed = ok
                results = [type("C", (), {"criterion_id": "real_backtest_validation", "details": {
                    "out_of_sample_sharpe_ratio": 0.5 if ok else -0.2, "deflated_sharpe_threshold": 0.3,
                    "failed_checks": [] if ok else ["oos_sharpe_positive_after_costs"], "backtest_run_id": "bt-x"}})()]
            return R

    monkeypatch.setattr(research, "_retrain_call", call)
    monkeypatch.setattr(research, "_RETRAIN_DATASET_LOADER", lambda parent, params: {"dataset_id": "ds-1"})
    monkeypatch.setattr(shared_stores, "SharedReplicationGate", FakeGate)
    monkeypatch.setattr(
        executor, "build_gate_request_factory",
        lambda **kw: (lambda artifact: {"family": kw["family_id"]}),
    )
    return w


def _dispatch(research, action, decision_id, children=None):
    from fastapi.testclient import TestClient

    client = TestClient(research.app)
    task = client.post("/api/research-orchestrator/tasks", json={
        "title": decision_id, "objective": f"approved {action}", "constraints": {"tenant_id": TENANT},
        "actor_id": "evolution-dispatch-worker", "idempotency_key": f"task-{decision_id}"}).json()
    run = client.post(f"/api/research-orchestrator/tasks/{task['task_id']}/runs", json={
        "parameters": {"tenant_id": TENANT, "decision_id": decision_id, "action_type": action,
                       "target_artifact_id": "strat-tw-rev-yoy-factor-v1-001", "target_version": "1.0.0",
                       "work_item_id": task["task_id"]},
        "actor_id": "evolution-dispatch-worker", "idempotency_key": f"run-{decision_id}"}).json()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        got = client.get(f"/api/research-orchestrator/runs/{run['run_id']}").json()
        if got["status"] in {"completed", "failed"}:
            return got, client.get(f"/api/research-orchestrator/tasks/{task['task_id']}").json()
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_approved_mutate_registers_a_passing_child_with_lineage_and_a_terminal_receipt(research, world):
    run, task = _dispatch(research, "mutate", "evd-m1")
    assert run["status"] == "completed" and run["outcome"] == "candidate_created", run
    assert run["adapter"] == "evolution_oos_gate"
    assert len(world.registered) == 1
    body = next(iter(world.registered.values()))
    assert body["artifact_state"] == "candidate"
    assert body["strategy_artifact"]["lineage"]["parent_registry_ids"] == [world.parent["artifact_id"]]
    assert body["evaluation_summary"]["out_of_sample_gate"]["window"] == "out_of_sample"
    assert run["registry_writebacks"][0]["artifact_state"] == "candidate"
    assert run["family_trial_count"] == 1
    assert task["status"] == "completed"


def test_failed_out_of_sample_gate_refuses_registration(research, world):
    world.passes = False
    run, task = _dispatch(research, "mutate", "evd-m2")
    assert run["status"] == "completed" and run["outcome"] == "no_candidate", run
    assert run["outcome_reason"] == "children_failed_out_of_sample_gate"
    assert world.registered == {}
    assert run["family_trial_count"] == 1  # the failed trial still counts
    assert task["status"] == "completed"


def test_approved_revalidate_runs_the_gate_and_registers_nothing(research, world):
    run, _ = _dispatch(research, "revalidate", "evd-r1")
    assert run["status"] == "completed" and run["outcome"] == "revalidation_passed", run
    assert world.registered == {}
    world.passes = False
    run2, _ = _dispatch(research, "revalidate", "evd-r2")
    assert run2["outcome"] == "revalidation_failed" and world.registered == {}
    assert run2["family_trial_count"] == 2  # trials accumulate per family across decisions


def test_missing_governed_dataset_fails_closed(research, world, monkeypatch):
    monkeypatch.setattr(research, "_RETRAIN_DATASET_LOADER", lambda parent, params: None)
    run, _ = _dispatch(research, "mutate", "evd-m3")
    assert run["outcome"] == "evaluation_rejected" and run["outcome_reason"] == "governed_dataset_unavailable"
    assert world.registered == {}


def test_repeated_decisions_from_one_parent_register_unique_family_revisions(research, world):
    versions = []
    for n in range(3):
        run, _ = _dispatch(research, "mutate", f"evd-v{n}")
        assert run["outcome"] == "candidate_created", run
        versions.append(list(world.registered.values())[-1]["strategy_artifact"]["version"])
    assert versions == ["1.1.0", "1.2.0", "1.3.0"]
    assert len(world.registered) == 3


def test_a_lost_revision_race_reallocates_and_rejudges(research, world, monkeypatch):
    from services.research.alpha_replication import evolution_search as search

    real = search.allocate_child_version
    calls = {"n": 0}

    def stale_first(parent_version, existing=()):
        calls["n"] += 1
        if calls["n"] == 1:
            world.foreign_versions.append("1.1.0")  # another writer takes 1.1.0 after our family read
            return real(parent_version, ())
        return real(parent_version, existing)

    monkeypatch.setattr(search, "allocate_child_version", stale_first)
    run, _ = _dispatch(research, "mutate", "evd-race")
    assert run["outcome"] == "candidate_created", run
    assert [p["strategy_artifact"]["version"] for p in world.registered.values()] == ["1.2.0"]
    assert run["family_trial_count"] == 2  # the re-judged child is a second family trial
