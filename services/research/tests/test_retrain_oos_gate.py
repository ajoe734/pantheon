"""Retrain end to end: real in-sample search -> real §10 ReplicationGate on the untouched OOS window.

Owner transports and the Training preview evaluation are stand-ins; the search, the
vectorbt backtest protocol, the gate (deflated Sharpe, trial counter) and the
research executor's registration logic are real.  Training's whole-dataset
"better than baseline" proof alone must never register a child.
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
TARGET = "strat-retrain-oos-v1"


@pytest.fixture()
def research(monkeypatch, tmp_path):
    service_dir = ROOT / "services" / "research"
    monkeypatch.setenv("RESEARCH_ORCHESTRATOR_DATA_DIR", tempfile.mkdtemp())
    monkeypatch.setenv("RESEARCH_REPLICATION_TRIAL_COUNTER_PATH", str(tmp_path / "family_trial_counts.json"))
    monkeypatch.setenv("ALPHA_REPLICATION_DATA_DIR", str(tmp_path / "alpha"))
    monkeypatch.setenv("RESEARCH_RETRAIN_SEARCH_BUDGET", "12")
    monkeypatch.syspath_prepend(str(service_dir))
    spec = importlib.util.spec_from_file_location("research_retrain_oos_gate", service_dir / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["research_retrain_oos_gate"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("store", None)
    return module


def _world(research, monkeypatch, *, negative_oos):
    from services.research.replication import test_gate
    from services.research.vectorbt.adapter.vectorbt_adapter import (
        build_canonical_tw_strategy_artifact,
        build_governed_tw_replication_dataset,
    )

    artifact = build_canonical_tw_strategy_artifact(
        strategy_id="strat-retrain-oos", artifact_id=TARGET, symbols=["2330", "2317"], source_strategy_spec_id="spec-1", lookback_bars=9)
    dataset = build_governed_tw_replication_dataset(symbols=["2330", "2317"], trading_days=756, negative_oos=negative_oos)
    case = test_gate.TestReplicationGate("test_audit_log")
    spec = dict(case._build_request().proposed_strategy_spec)
    spec["strategy_id"] = artifact["strategy_id"]
    registered: dict = {}
    calls: list = []

    def call(*, step, service, method, url, tenant_id, token_variable, body=None, **_):
        calls.append((method, url))
        tail = url.rsplit("/", 1)[-1]
        if method == "GET" and url.endswith(f"/strategy-artifacts/{TARGET}"):
            return {"entry": {"registry_id": TARGET, "artifact_state": "approved",
                              "metadata": {"strategy_artifact": artifact, "persona_id": "persona-1"}}}
        if method == "GET" and url.endswith("/strategy-specs/spec-1"):
            return {"entry": {"registry_id": "spec-1", "artifact_state": "approved", "owner_tenant": TENANT,
                              "metadata": {"strategy_spec": spec}}}
        if method == "GET" and url.endswith("/strategy-artifacts") and "/strategies/" in url:
            return [{"entry": {"version": artifact["version"]}}]
        if method == "GET" and "/strategy-artifacts/" in url:
            if tail in registered:
                return {"entry": {"registry_id": tail, "artifact_state": "candidate"}}
            raise research._OwnerRejection(404, "nf")
        if method == "POST" and url.endswith("/api/training/sessions"):
            return {"session_id": "sess-1"}
        if method == "GET" and "/api/training/controls/" in url:
            return {"controls": [{"parameter_key": "lookback_bars", "current_value": 2}]}
        if method == "POST" and url.endswith("/controls"):
            return {"status": "accepted"}
        if method == "POST" and url.endswith("/mutate"):
            registered[body["new_artifact_id"]] = body
            return {"entry": {"registry_id": body["new_artifact_id"], "artifact_state": "candidate"}}
        raise AssertionError(f"unexpected owner call {method} {url}")

    def evaluate(*, kind, **_):  # Training: a passed, strictly better whole-dataset proof
        sharpe = 0.5 if kind == "baseline" else 1.5
        return {"job_id": f"job-{kind}", "eval_id": f"eval-{kind}", "preview": {"evaluation_proof": {
            "status": "passed", "governance_gate_state": "passed", "proof_ref": f"proof-{kind}",
            "metrics": {"sharpe_ratio": sharpe}, "validation_errors": []}}}

    monkeypatch.setattr(research, "_retrain_call", call)
    monkeypatch.setattr(research, "_retrain_evaluate", evaluate)
    monkeypatch.setattr(research, "_RETRAIN_DATASET_LOADER", lambda parent, params: dataset)
    return registered, calls


def _run(research, decision_id):
    from fastapi.testclient import TestClient

    client = TestClient(research.app)
    task = client.post("/api/research-orchestrator/tasks", json={
        "title": decision_id, "objective": "approved retrain", "constraints": {"tenant_id": TENANT},
        "actor_id": "evolution-dispatch-worker", "idempotency_key": f"task-{decision_id}"}).json()
    run = client.post(f"/api/research-orchestrator/tasks/{task['task_id']}/runs", json={
        "parameters": {"tenant_id": TENANT, "decision_id": decision_id, "action_type": "retrain",
                       "target_artifact_id": TARGET, "target_version": "1.0.0", "work_item_id": task["task_id"]},
        "actor_id": "evolution-dispatch-worker", "idempotency_key": f"run-{decision_id}"}).json()
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        got = client.get(f"/api/research-orchestrator/runs/{run['run_id']}").json()
        if got["status"] in {"completed", "failed"}:
            return got
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_real_search_then_rejected_oos_window_registers_nothing(research, monkeypatch):
    registered, calls = _world(research, monkeypatch, negative_oos=True)
    run = _run(research, "evd-rt-reject")
    assert run["status"] == "completed", run
    assert run["search"], "the real in-sample search ran"
    assert run["outcome"] == "no_candidate" and run["outcome_reason"] == "children_failed_out_of_sample_gate", run
    gate = run["out_of_sample_gate"]
    assert gate["window"] == "out_of_sample" and gate["passed"] is False
    assert gate["trial_count_after"] > gate["trial_count_before"]
    assert registered == {} and not any(m == "POST" and u.endswith("/mutate") for m, u in calls)
    assert run["family_trial_count"] == gate["trial_count_after"] >= 5  # 4 search trials + the OOS judgement
