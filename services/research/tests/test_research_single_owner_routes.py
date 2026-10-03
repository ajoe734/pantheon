from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.research import main as research_main
from services.research.write_owner import ResearchWriteOwner


class _InMemoryStore:
    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Any]] = {}

    def put(self, key: str, payload: Dict[str, Any]) -> None:
        self._data[key] = json.loads(json.dumps(payload))

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        val = self._data.get(key)
        return json.loads(json.dumps(val)) if val else None

    def list_all(self, *, conn: Optional[Any] = None) -> List[Dict[str, Any]]:
        return [json.loads(json.dumps(v)) for v in self._data.values()]


@pytest.fixture
def mock_write_owner() -> ResearchWriteOwner:
    return ResearchWriteOwner(
        tickets_store=_InMemoryStore(),
        experiments_store=_InMemoryStore(),
        notes_store=_InMemoryStore(),
    )


@pytest.fixture
def client(mock_write_owner: ResearchWriteOwner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(
        research_main,
        "store",
        research_main.build_research_orchestrator_store(str(tmp_path / "research-owner")),
    )
    research_main.set_write_owner(mock_write_owner)
    try:
        yield TestClient(research_main.app)
    finally:
        research_main.set_write_owner(None)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            active = [
                record for record in research_main.store.list_runs()
                if str(record.get("status") or "").lower() in {"queued", "running"}
            ]
            if not active:
                break
            time.sleep(0.01)
        assert not active, f"research stage workers did not settle during teardown: {active}"


def test_research_tickets_lifecycle(client: TestClient) -> None:
    # 1. Create ticket
    res = client.post(
        "/api/research/tickets",
        json={
            "title": "Alpha Research Ticket",
            "description": "Exploration of momentum signal",
            "priority": "high",
            "owner": "quant_dev",
            "actor_id": "operator",
        },
    )
    assert res.status_code == 200, res.text
    ticket = res.json()
    ticket_id = ticket["ticket_id"]
    assert ticket["title"] == "Alpha Research Ticket"
    assert ticket["status"] == "open"
    assert ticket["priority"] == "high"

    # 2. Get ticket
    res_get = client.get(f"/api/research/tickets/{ticket_id}")
    assert res_get.status_code == 200
    assert res_get.json()["ticket_id"] == ticket_id

    # 3. List tickets
    res_list = client.get("/api/research/tickets?status=open")
    assert res_list.status_code == 200
    assert any(t["ticket_id"] == ticket_id for t in res_list.json())

    # 4. Patch ticket to in_progress
    res_patch = client.patch(
        f"/api/research/tickets/{ticket_id}",
        json={"status": "in_progress", "actor_id": "operator"},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["status"] == "in_progress"

    # 5. Patch ticket to closed
    res_close = client.patch(
        f"/api/research/tickets/{ticket_id}",
        json={"status": "closed", "actor_id": "operator"},
    )
    assert res_close.status_code == 200
    assert res_close.json()["status"] == "closed"


def test_research_experiments_lifecycle(client: TestClient) -> None:
    # 1. Create experiment
    res = client.post(
        "/api/research/experiments",
        json={
            "ticket_id": "rt-2026-001",
            "experiment_name": "Momentum Backtest 1",
            "strategy_selector": {"strategy_id": "strat-mom-01"},
            "parameter_set": {"lookback": 20},
            "run_config": {"stage": "backtest", "backend": "vectorbt"},
            "launch_context": {},
        },
    )
    assert res.status_code == 200, res.text
    exp = res.json()
    exp_id = exp["experiment_id"]
    assert exp["status"] == "queued"
    assert exp["allowedActions"]["canCancel"] is True

    # 2. Get experiment
    res_get = client.get(f"/api/research/experiments/{exp_id}")
    assert res_get.status_code == 200
    assert res_get.json()["experiment_id"] == exp_id

    # 3. List experiments
    res_list = client.get(f"/api/research/experiments?ticket_id=rt-2026-001")
    assert res_list.status_code == 200
    assert len(res_list.json()) == 1

    # 4. Cancel experiment
    res_cancel = client.post(
        f"/api/research/experiments/{exp_id}/cancel",
        json={"reason": "Operator test cancel", "actor_id": "tester"},
    )
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "canceled"
    assert res_cancel.json()["allowedActions"]["canRetry"] is True

    # 5. Retry experiment
    res_retry = client.post(
        f"/api/research/experiments/{exp_id}/retry",
        json={"actor_id": "tester"},
    )
    assert res_retry.status_code == 200
    retried_exp = res_retry.json()
    assert retried_exp["attempt_number"] == 2
    assert retried_exp["status"] == "queued"

    # 6. Archive experiment
    res_archive = client.post(
        f"/api/research/experiments/{exp_id}/archive",
        json={"actor_id": "tester"},
    )
    assert res_archive.status_code == 200
    assert res_archive.json()["is_archived"] is True


def test_research_notes_lifecycle(client: TestClient) -> None:
    # 1. Create note
    res = client.post(
        "/api/research/notes",
        json={
            "note_id": "note-test-001",
            "title": "Findings on Volatility",
            "content": {"text": "Mean reversion observed in short tenor."},
            "tags": ["volatility", "options"],
        },
    )
    assert res.status_code == 200, res.text
    note = res.json()
    assert note["note_id"] == "note-test-001"

    # 2. Get note
    res_get = client.get("/api/research/notes/note-test-001")
    assert res_get.status_code == 200
    assert res_get.json()["title"] == "Findings on Volatility"

    # 3. List notes
    res_list = client.get("/api/research/notes")
    assert res_list.status_code == 200
    assert any(n.get("note_id") == "note-test-001" for n in res_list.json())


def test_research_write_owner_unavailable_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    research_main.set_write_owner(None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RESEARCH_STORE_DSN", raising=False)
    client = TestClient(research_main.app)

    res = client.post("/api/research/tickets", json={"title": "Test Ticket"})
    assert res.status_code == 503
    assert "Research write owner unavailable" in res.json()["detail"]


def _wait_for_task_status(
    task_id: str, statuses: set[str], *, expected_count: int = 1, timeout: float = 5.0
) -> List[Dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runs = [r for r in research_main.store.list_runs() if r.get("task_id") == task_id]
        if len(runs) >= expected_count and all(str(r.get("status") or "").lower() in statuses for r in runs):
            return runs
        time.sleep(0.01)
    return [r for r in research_main.store.list_runs() if r.get("task_id") == task_id]


def _valid_multimodal_dataset() -> Dict[str, Any]:
    from datetime import date, timedelta
    records = []
    start = date(2026, 1, 1)
    for inst, base in (("AAA", 100.0), ("BBB", 50.0)):
        for i in range(35):
            d = (start + timedelta(days=i)).isoformat()
            p = base + i * 0.5
            records.append({
                "instrument": inst, "date": d,
                "open": p, "high": p + 1.0, "low": p - 0.5, "close": p + 0.2, "volume": 1000.0,
            })
    return {
        "dataset_id": "ds-combined",
        "strategy_id": "strat-dag-test",
        "source_dataset_refs": ["ds-combined"],
        "records": records,
        "price_series": {"asset1": [10.0 + i for i in range(35)], "asset2": [20.0 + i * 0.5 for i in range(35)]},
        "factor_series": {"market": [100.0 + i for i in range(35)]},
        "valuation_date": "2026-01-01",
        "option_specs": [
            {"option_id": "opt1", "style": "european", "option_type": "call", "spot": 105.0, "strike": 100.0, "volatility": 0.2, "risk_free_rate": 0.05, "dividend_yield": 0.01, "maturity_days": 180}
        ],
        "bond_specs": [
            {"instrument_id": "bond1", "face_value": 1000.0, "coupon_rate": 0.04, "market_rate": 0.045, "maturity_years": 5, "payment_frequency": 2}
        ],
        "metadata": {"governed": True},
    }


def test_research_dag_three_stage_linear_progression(client: TestClient) -> None:
    ds = _valid_multimodal_dataset()
    t_res = client.post("/api/research-orchestrator/tasks", json={"title": "Linear DAG", "objective": "3-stage chain", "source_refs": [], "constraints": {}})
    assert t_res.status_code == 201
    task_id = t_res.json()["task_id"]

    plan = {
        "plan_id": f"plan-{task_id}",
        "task_id": task_id,
        "strategy_id": "strat-dag-test",
        "stages": [
            {"stage_id": "s1", "stage_type": "prototype_backtest", "status": "ready", "dependencies": []},
            {"stage_id": "s2", "stage_type": "econometric_validation", "status": "pending", "dependencies": ["s1"]},
            {"stage_id": "s3", "stage_type": "derivatives_pricing_risk", "status": "pending", "dependencies": ["s2"]},
        ],
        "dataset": ds,
    }

    d_res = client.post(f"/api/research-orchestrator/tasks/{task_id}/runs", json={
        "adapter": "vectorbt",
        "requested_mode": "stub",
        "dispatch_mode": "stub",
        "input_refs": [{"type": "stage", "id": "s1"}],
        "parameters": {"stage": plan["stages"][0], "plan": plan, "dataset": ds},
        "idempotency_key": f"idemp-dag-linear-{task_id}",
    })
    assert d_res.status_code == 201
    assert d_res.json()["status"] == "queued"

    all_runs = _wait_for_task_status(task_id, {"completed", "failed"}, expected_count=3)
    assert len(all_runs) == 3
    run_by_stage = {r["stage_id"]: r for r in all_runs}
    assert set(run_by_stage.keys()) == {"s1", "s2", "s3"}

    # Distinct receipts and artifacts
    receipt_ids = set()
    artifact_ids = set()
    for sid in ("s1", "s2", "s3"):
        r = run_by_stage[sid]
        assert r["status"] == "completed"
        assert r.get("receipt") is not None
        receipt_ids.add(r["receipt"]["receipt_id"])
        assert len(r.get("artifact_refs") or []) >= 1
        artifact_ids.add(r["artifact_refs"][0]["artifact_id"])
    assert len(receipt_ids) == 3
    assert len(artifact_ids) == 3

    # Linkage
    assert run_by_stage["s1"]["parent_run_id"] is None
    assert run_by_stage["s2"]["parent_run_id"] == run_by_stage["s1"]["run_id"]
    assert run_by_stage["s3"]["parent_run_id"] == run_by_stage["s2"]["run_id"]


def test_research_dag_fan_in_progression(client: TestClient) -> None:
    ds = _valid_multimodal_dataset()
    t_res = client.post("/api/research-orchestrator/tasks", json={"title": "Fan-in DAG", "objective": "fan in to s3", "source_refs": [], "constraints": {}})
    assert t_res.status_code == 201
    task_id = t_res.json()["task_id"]

    plan = {
        "plan_id": f"plan-{task_id}",
        "task_id": task_id,
        "strategy_id": "strat-dag-test",
        "stages": [
            {"stage_id": "s1", "stage_type": "prototype_backtest", "status": "ready", "dependencies": []},
            {"stage_id": "s2", "stage_type": "econometric_validation", "status": "ready", "dependencies": []},
            {"stage_id": "s3", "stage_type": "derivatives_pricing_risk", "status": "pending", "dependencies": ["s1", "s2"]},
        ],
        "dataset": ds,
    }

    # Dispatch s1 only -> s3 should NOT run
    d1 = client.post(f"/api/research-orchestrator/tasks/{task_id}/runs", json={
        "adapter": "vectorbt",
        "requested_mode": "stub",
        "dispatch_mode": "stub",
        "input_refs": [{"type": "stage", "id": "s1"}],
        "parameters": {"stage": plan["stages"][0], "plan": plan, "dataset": ds},
        "idempotency_key": f"idemp-dag-fanin-1-{task_id}",
    })
    assert d1.status_code == 201
    runs_after_s1 = [r for r in research_main.store.list_runs() if r.get("task_id") == task_id]
    assert {r["stage_id"] for r in runs_after_s1} == {"s1", "s2"}
    runs_after_s1 = _wait_for_task_status(task_id, {"completed", "failed"}, expected_count=2)
    assert len(runs_after_s1) == 2
    assert all(r["status"] == "completed" for r in runs_after_s1)

    # One owner dispatch schedules both roots, then their fan-in successor.
    runs_after_s2 = _wait_for_task_status(task_id, {"completed", "failed"}, expected_count=3)
    assert len(runs_after_s2) == 3
    run_by_stage = {r["stage_id"]: r for r in runs_after_s2}
    assert set(run_by_stage.keys()) == {"s1", "s2", "s3"}
    for sid in ("s1", "s2", "s3"):
        assert run_by_stage[sid]["status"] == "completed"


def test_research_dag_fan_in_progression_is_atomic_across_repeated_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def complete_stage(_stage_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        run = research_main.store.get_run(payload["run_id"])
        run["status"] = "completed"
        research_main.store.put_run(run)
        return {"status": "succeeded"}

    monkeypatch.setattr(research_main, "execute_research_stage", complete_stage)
    for iteration in range(20):
        task = client.post("/api/research-orchestrator/tasks", json={
            "title": f"Fan-in race {iteration}", "objective": "atomic join",
            "source_refs": [], "constraints": {},
        }).json()
        task_id = task["task_id"]
        plan = {
            "plan_id": f"fan-in-{task_id}", "task_id": task_id,
            "stages": [
                {"stage_id": "left", "stage_type": "prototype_backtest", "dependencies": []},
                {"stage_id": "right", "stage_type": "econometric_validation", "dependencies": []},
                {"stage_id": "join", "stage_type": "derivatives_pricing_risk", "dependencies": ["left", "right"]},
            ],
            "dataset": {"dataset_id": f"dataset-{task_id}"},
        }
        response = client.post(f"/api/research-orchestrator/tasks/{task_id}/runs", json={
            "adapter": "vectorbt", "requested_mode": "stub", "dispatch_mode": "stub",
            "input_refs": [{"type": "stage", "id": "left"}],
            "parameters": {"stage": plan["stages"][0], "plan": plan, "dataset": plan["dataset"]},
            "idempotency_key": f"fan-in-{task_id}",
        })
        assert response.status_code == 201, response.text
        records = _wait_for_task_status(task_id, {"completed", "failed"}, expected_count=3)
        assert len(records) == 3
        assert {record["stage_id"] for record in records} == {"left", "right", "join"}


def test_research_dag_failed_stage_halts_downstream(client: TestClient) -> None:
    t_res = client.post("/api/research-orchestrator/tasks", json={"title": "Halt DAG", "objective": "fail halts", "source_refs": [], "constraints": {}})
    assert t_res.status_code == 201
    task_id = t_res.json()["task_id"]

    plan = {
        "plan_id": f"plan-{task_id}",
        "task_id": task_id,
        "strategy_id": "strat-dag-test",
        "stages": [
            {"stage_id": "s1", "stage_type": "prototype_backtest", "status": "ready", "dependencies": []},
            {"stage_id": "s2", "stage_type": "econometric_validation", "status": "pending", "dependencies": ["s1"]},
        ],
        "dataset": {"invalid": "missing required records and fields"},
    }

    d_res = client.post(f"/api/research-orchestrator/tasks/{task_id}/runs", json={
        "adapter": "vectorbt",
        "requested_mode": "stub",
        "dispatch_mode": "stub",
        "input_refs": [{"type": "stage", "id": "s1"}],
        "parameters": {"stage": plan["stages"][0], "plan": plan, "dataset": plan["dataset"]},
        "idempotency_key": f"idemp-dag-fail-{task_id}",
    })
    assert d_res.status_code == 201
    assert d_res.json()["status"] == "queued"

    all_runs = _wait_for_task_status(task_id, {"completed", "failed"})
    assert len(all_runs) == 1
    assert all_runs[0]["stage_id"] == "s1"
    assert all_runs[0]["status"] == "failed"


def test_stage_dispatch_returns_before_slow_execution_finishes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_execute(*args: Any, **kwargs: Any) -> None:
        started.set()
        assert release.wait(3)
        run_id = kwargs.get("run_id") or (args[1].get("run_id") if len(args) > 1 else None)
        if run_id:
            run = research_main.store.get_run(run_id)
            run["status"] = "completed"
            research_main.store.put_run(run)

    monkeypatch.setattr(research_main, "execute_research_stage", slow_execute)
    task = client.post("/api/research-orchestrator/tasks", json={"title": "Slow", "objective": "async", "source_refs": [], "constraints": {}}).json()
    task_id = task["task_id"]
    plan = {
        "plan_id": f"plan-{task_id}", "task_id": task_id,
        "stages": [{"stage_id": "slow", "stage_type": "prototype_backtest", "dependencies": []}],
        "dataset": {"dataset_id": "ds-slow"},
    }
    response = client.post(f"/api/research-orchestrator/tasks/{task_id}/runs", json={
        "adapter": "vectorbt", "requested_mode": "stub", "dispatch_mode": "stub",
        "input_refs": [{"type": "stage", "id": "slow"}],
        "parameters": {"stage": plan["stages"][0], "plan": plan, "dataset": plan["dataset"]},
        "idempotency_key": f"slow-{task_id}",
    })
    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert started.wait(1)
    assert research_main.store.get_run(response.json()["run_id"])["status"] == "running"
    release.set()
    _wait_for_task_status(task_id, {"completed", "failed"})


def test_owner_restart_resumes_queued_stage_without_replaying_completed_predecessor(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(research_main, "store", research_main.build_research_orchestrator_store(str(tmp_path)))
    task_id = "restart-mid-graph-task"
    research_main.store.put_task({"task_id": task_id, "title": "restart", "status": "running"})
    plan = {
        "plan_id": "restart-mid-graph-plan", "task_id": task_id,
        "stages": [
            {"stage_id": "s1", "stage_type": "prototype_backtest", "dependencies": []},
            {"stage_id": "s2", "stage_type": "econometric_validation", "dependencies": ["s1"]},
        ],
        "dataset": {"dataset_id": "restart-dataset"},
    }
    # Simulate a crash after s1 was persisted but before s2 was ever queued.
    for stage_id, status, attempt in (("s1", "completed", 1),):
        stage = next(item for item in plan["stages"] if item["stage_id"] == stage_id)
        run_id = f"restart-{stage_id}"
        research_main.store.put_run({
            "run_id": run_id, "task_id": task_id, "stage_id": stage_id,
            "attempt_number": attempt, "status": status, "adapter": stage["stage_type"],
            "requested_mode": "stub", "dispatch_mode": "stub",
            "parameters": {"stage": stage, "plan": plan, "dataset": plan["dataset"]},
            "created_at": "2026-10-03T00:00:00Z", "events": [], "artifact_refs": [],
        })

    executed = []
    def complete_stage(stage_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        executed.append(payload["stage"]["stage_id"])
        run = research_main.store.get_run(payload["run_id"])
        run["status"] = "completed"
        research_main.store.put_run(run)
        return {"status": "succeeded"}

    monkeypatch.setattr(research_main, "execute_research_stage", complete_stage)
    research_main.resume_queued_plan_stages()
    runs = _wait_for_task_status(task_id, {"completed", "failed"}, expected_count=2)
    assert {run["stage_id"]: run["status"] for run in runs} == {"s1": "completed", "s2": "completed"}
    assert executed == ["s2"]


def test_in_flight_stage_cancel_is_not_overwritten_by_late_backend_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()
    task_id = "cancel-in-flight-task"
    stage = {"stage_id": "s1", "stage_type": "prototype_backtest", "dependencies": []}
    plan = {"plan_id": "cancel-in-flight-plan", "task_id": task_id, "stages": [stage]}
    research_main.store.put_task({"task_id": task_id, "status": "running"})
    run = {
        "run_id": "cancel-in-flight-run", "task_id": task_id, "stage_id": "s1",
        "attempt_number": 1, "status": "queued", "adapter": stage["stage_type"],
        "parameters": {"stage": stage, "plan": plan}, "events": [],
    }
    research_main.store.put_run(run)

    def late_backend_error(*args: Any, **kwargs: Any) -> None:
        started.set()
        assert release.wait(3)
        raise HTTPException(status_code=409, detail="late completion fenced")

    monkeypatch.setattr(research_main, "execute_research_stage", late_backend_error)
    worker = threading.Thread(
        target=research_main._execute_plan_stage,
        args=(run, stage, plan, research_main.store, "tester"),
    )
    worker.start()
    assert started.wait(1)
    canceled = research_main.cancel_run("cancel-in-flight-run")
    assert canceled["status"] == "canceled"
    assert canceled["cancellation_fence"]
    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    persisted = research_main.store.get_run("cancel-in-flight-run")
    assert persisted["status"] == "canceled"
    assert persisted["cancellation_fence"]


def test_task_cancel_fences_stage_progression_and_restart(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()
    task_id = "task-cancel-fenced"
    stage1 = {"stage_id": "s1", "stage_type": "prototype_backtest", "dependencies": []}
    stage2 = {"stage_id": "s2", "stage_type": "econometric_validation", "dependencies": ["s1"]}
    plan = {"plan_id": f"plan-{task_id}", "task_id": task_id, "stages": [stage1, stage2]}
    research_main.store.put_task({"task_id": task_id, "status": "running"})
    run = {
        "run_id": "run-fenced-s1", "task_id": task_id, "stage_id": "s1",
        "attempt_number": 1, "status": "queued", "adapter": stage1["stage_type"],
        "parameters": {"stage": stage1, "plan": plan}, "events": [],
    }
    research_main.store.put_run(run)

    def mock_stage_exec(*args: Any, **kwargs: Any) -> None:
        started.set()
        assert release.wait(3)
        r = research_main.store.get_run("run-fenced-s1")
        r["status"] = "completed"
        research_main.store.put_run(r)

    monkeypatch.setattr(research_main, "execute_research_stage", mock_stage_exec)
    worker = threading.Thread(
        target=research_main._execute_plan_stage,
        args=(run, stage1, plan, research_main.store, "tester"),
    )
    worker.start()
    assert started.wait(1)

    cancel_res = client.post(f"/api/research-orchestrator/tasks/{task_id}/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "canceled"
    assert cancel_res.json()["cancellation_fence"]

    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()

    task_runs = [r for r in research_main.store.list_runs() if r.get("task_id") == task_id]
    assert not any(r.get("stage_id") == "s2" for r in task_runs)

    research_main.resume_queued_plan_stages()
    task_runs_after = [r for r in research_main.store.list_runs() if r.get("task_id") == task_id]
    assert not any(r.get("stage_id") == "s2" for r in task_runs_after)


def test_stage_idempotency_restart_exactly_once(client: TestClient) -> None:
    ds = _valid_multimodal_dataset()
    t_res = client.post("/api/research-orchestrator/tasks", json={"title": "Idemp Task", "objective": "idemp", "source_refs": [], "constraints": {}})
    task_id = t_res.json()["task_id"]
    plan = {
        "plan_id": f"plan-{task_id}",
        "task_id": task_id,
        "strategy_id": "strat-dag-test",
        "stages": [{"stage_id": "s1", "stage_type": "prototype_backtest", "status": "ready", "dependencies": []}],
        "dataset": ds,
    }
    idemp_key = f"idemp-exact-{task_id}"
    req = {
        "adapter": "vectorbt",
        "requested_mode": "stub",
        "dispatch_mode": "stub",
        "input_refs": [{"type": "stage", "id": "s1"}],
        "parameters": {"stage": plan["stages"][0], "plan": plan, "dataset": ds},
        "idempotency_key": idemp_key,
    }
    r1 = client.post(f"/api/research-orchestrator/tasks/{task_id}/runs", json=req)
    assert r1.status_code == 201
    r2 = client.post(f"/api/research-orchestrator/tasks/{task_id}/runs", json=req)
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    runs = [r for r in research_main.store.list_runs() if r.get("task_id") == task_id]
    assert len(runs) == 1


def test_retry_run_executes_backend_and_produces_artifacts(client: TestClient) -> None:
    ds = _valid_multimodal_dataset()
    t_res = client.post("/api/research-orchestrator/tasks", json={"title": "Retry Task", "objective": "retry test", "source_refs": [], "constraints": {}})
    task_id = t_res.json()["task_id"]
    plan = {
        "plan_id": f"plan-{task_id}",
        "task_id": task_id,
        "strategy_id": "strat-dag-test",
        "stages": [
            {"stage_id": "s1", "stage_type": "prototype_backtest", "status": "ready", "dependencies": []},
            {"stage_id": "s2", "stage_type": "econometric_validation", "status": "pending", "dependencies": ["s1"]},
        ],
        "dataset": {"invalid": "bad"},
    }
    # Initial dispatch with bad stage dataset -> fails
    d_fail = client.post(f"/api/research-orchestrator/tasks/{task_id}/runs", json={
        "adapter": "vectorbt",
        "requested_mode": "stub",
        "dispatch_mode": "stub",
        "input_refs": [{"type": "stage", "id": "s1"}],
        "parameters": {"stage": plan["stages"][0], "plan": plan, "dataset": {"invalid": "bad"}},
        "idempotency_key": f"idemp-initial-fail-{task_id}",
    })
    fail_run_id = d_fail.json()["id"]
    assert d_fail.json()["status"] == "queued"
    _wait_for_task_status(task_id, {"completed", "failed"})
    assert research_main.store.get_run(fail_run_id)["status"] == "failed"

    # Retry the failed run with repaired valid dataset
    repaired_run = research_main.store.get_run(fail_run_id)
    repaired_run["parameters"]["dataset"] = ds
    repaired_run["parameters"]["plan"]["dataset"] = ds
    if "stage" in repaired_run["parameters"]:
        repaired_run["parameters"]["stage"]["dataset"] = ds
    research_main.store.put_run(repaired_run)

    d_retry = client.post(f"/api/research-orchestrator/runs/{fail_run_id}/retry", json={
        "actor_id": "operator",
        "idempotency_key": f"idemp-retry-success-{task_id}",
    })
    assert d_retry.status_code == 201
    retried_data = d_retry.json()
    assert retried_data["attempt_number"] == 2
    assert retried_data["stage_id"] == "s1"
    assert retried_data["status"] == "queued"
    assert retried_data["parameters"]["dataset"]["dataset_id"] == "ds-combined"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        retried_data = research_main.store.get_run(retried_data["run_id"])
        if retried_data["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)
    assert retried_data["status"] == "completed"
    assert retried_data.get("receipt") is not None
    assert len(retried_data.get("artifact_refs") or []) >= 1
    latest_attempts = _wait_for_task_status(task_id, {"completed", "failed"}, expected_count=3)
    latest_by_stage = {}
    for record in latest_attempts:
        if record.get("stage_id") not in latest_by_stage or record.get("attempt_number", 1) > latest_by_stage[record["stage_id"]].get("attempt_number", 1):
            latest_by_stage[record["stage_id"]] = record
    assert latest_by_stage["s1"]["status"] == "completed"
    assert latest_by_stage["s2"]["status"] == "completed"
    assert latest_by_stage["s2"]["parent_run_id"] == retried_data["run_id"]


def test_stage_owner_keeps_stub_output_simulation_and_rejects_unknown_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PANTHEON_VECTORBT_BACKEND", "stub")
    stage = {"stage_id": "provenance-stage", "stage_type": "prototype_backtest"}
    plan = {"plan_id": "provenance-plan", "dataset": _valid_multimodal_dataset()}
    result = research_main.execute_research_stage("prototype_backtest", {
        "stage": stage, "plan": plan, "dataset": plan["dataset"],
        "run_id": "provenance-stub-run", "correlation_id": "provenance-stub-correlation",
        "requested_mode": "stub",
    })
    assert result["provenance"] == "simulation"
    assert result["receipt"]["mode"] == "simulation"

    monkeypatch.setenv("PANTHEON_VECTORBT_BACKEND", "real")
    with pytest.raises(Exception) as exc_info:
        research_main.execute_research_stage("prototype_backtest", {
            "stage": stage, "plan": plan, "dataset": plan["dataset"],
            "run_id": "provenance-unknown-run", "correlation_id": "provenance-unknown-correlation",
            "requested_mode": "unrecognized-mode",
        })
    assert getattr(exc_info.value, "status_code", None) == 400


def test_bff_import_does_not_initialize_research_store() -> None:
    import subprocess
    import sys
    code = (
        "import sys\n"
        "import services.control_plane.bff.agora.research.service\n"
        "assert 'services.research.main' not in sys.modules\n"
        "import services.control_plane.bff.agora.research.dispatcher\n"
        "assert 'services.research.main' not in sys.modules\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_bff_cancel_run_fail_closed_when_orchestrator_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from services.control_plane.bff.agora.research.service import AgoraResearchService

    monkeypatch.delenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", raising=False)
    monkeypatch.delenv("RESEARCH_ORCHESTRATOR_URL", raising=False)

    class MockStore:
        def get_run(self, run_id: str, **kwargs: Any) -> Dict[str, Any]:
            return {
                "run_id": run_id,
                "plan_id": "plan-1",
                "stage_id": "s1",
                "execution_status": "running",
                "tenant_id": "pantheon-dev",
                "user_id": "agora-user-a",
            }

    svc = AgoraResearchService(store=MockStore())
    scope = SimpleNamespace(tenant_id="pantheon-dev", user_id="agora-user-a")
    with pytest.raises(Exception) as exc_info:
        svc.cancel_run("rrun-test", scope=scope)
    assert exc_info.value.status_code == 503
    assert "UPSTREAM_UNAVAILABLE" in str(getattr(exc_info.value, "detail", ""))


def test_cancelled_run_retry_does_not_remain_permanently_queued(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = research_main.build_research_orchestrator_store(str(tmp_path / "owner"))
    monkeypatch.setattr(research_main, "store", store)
    stage = {"stage_id": "s1", "stage_type": "prototype_backtest", "routing": {"backend_mode": "stub"}}
    plan = {"plan_id": "p1", "task_id": "t1", "stages": [stage], "dataset": _valid_multimodal_dataset()}
    store.put_task({"task_id": "t1", "status": "running"})
    run = {"run_id": "r1", "task_id": "t1", "stage_id": "s1", "status": "queued", "adapter": "vectorbt", "requested_mode": "stub", "dispatch_mode": "stub", "parameters": {"stage": stage, "plan": plan, "dataset": plan["dataset"]}}
    store.put_run(run)
    monkeypatch.setenv("PANTHEON_VECTORBT_BACKEND", "stub")
    client = TestClient(research_main.app)
    cancelled = client.post("/api/research-orchestrator/runs/r1/cancel")
    assert cancelled.status_code == 200
    response = client.post("/api/research-orchestrator/runs/r1/retry", json={"actor_id": "operator", "idempotency_key": "retry-r1"})
    assert response.status_code == 201
    retried_id = response.json()["run_id"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        retried = store.get_run(retried_id)
        if retried["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)
    assert retried["status"] == "completed", "Accepted canceled-run retry must execute; inherited task fence currently prevents all progression"


def test_cancel_fence_is_not_overwritten_by_worker_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = research_main.build_research_orchestrator_store(str(tmp_path / "owner"))
    monkeypatch.setattr(research_main, "store", store)
    stage = {"stage_id": "s1", "stage_type": "prototype_backtest", "routing": {"backend_mode": "stub"}}
    plan = {"plan_id": "p1", "task_id": "t1", "stages": [stage], "dataset": _valid_multimodal_dataset()}
    store.put_task({"task_id": "t1", "status": "running"})
    run = {"run_id": "r1", "task_id": "t1", "stage_id": "s1", "status": "queued", "adapter": "vectorbt", "requested_mode": "stub", "dispatch_mode": "stub", "parameters": {"stage": stage, "plan": plan, "dataset": plan["dataset"]}}
    store.put_run(run)
    monkeypatch.setenv("PANTHEON_VECTORBT_BACKEND", "stub")
    put_run = store.put_run
    fired = []

    def interleaved_put(record: Dict[str, Any]) -> Dict[str, Any]:
        if record["run_id"] == "r1" and record.get("status") == "running" and not fired:
            fired.append(True)
            research_main.cancel_task("t1")
            assert store.get_run("r1")["status"] == "canceled"
            assert store.get_run("r1")["cancellation_fence"]
        return put_run(record)

    monkeypatch.setattr(store, "put_run", interleaved_put)
    research_main._execute_plan_stage(run, stage, plan, store, "operator")
    actual = store.get_run("r1")
    assert actual["status"] == "canceled", "A completed task cancellation must fence a stale worker-start write"
    assert actual.get("cancellation_fence") is not None
    assert len(actual.get("artifact_refs") or []) == 0


def test_concurrent_stage_executions_deduplicate_to_single_effect(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = research_main.build_research_orchestrator_store(str(tmp_path / "owner"))
    monkeypatch.setattr(research_main, "store", store)
    from services.research.quantlib.adapter import quantlib_adapter
    original = quantlib_adapter.run_quantlib_workflow
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()
    calls = []

    def synchronized_workflow(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        else:
            second_entered.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(quantlib_adapter, "run_quantlib_workflow", synchronized_workflow)
    monkeypatch.setenv("PANTHEON_QUANTLIB_BACKEND", "stub")
    client = TestClient(research_main.app)
    task = client.post("/api/research-orchestrator/tasks", json={"title": "review", "objective": "same stage"}).json()
    run = client.post(f"/api/research-orchestrator/tasks/{task['task_id']}/runs", json={
        "adapter": "quantlib", "requested_mode": "stub", "dispatch_mode": "stub",
        "input_refs": [{"type": "stage", "id": "same-stage"}],
        "idempotency_key": "registered-run",
    }).json()
    from services.research.tests.test_research_orchestrator_http_service import _make_sample_quantlib_dataset
    body = {
        "stage": {"stage_id": "same-stage", "stage_type": "derivatives_pricing_risk"},
        "plan": {"plan_id": "same-plan"},
        "dataset": _make_sample_quantlib_dataset(),
        "run_id": run["run_id"], "correlation_id": "same-correlation",
    }
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, "/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json={**body, "downstream_key": "worker-key"})
        assert entered.wait(5)
        second = pool.submit(client.post, "/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json={**body, "idempotency_key": "http-replay-key"})
        second_entered.wait(1)
        release.set()
        replies = [first.result(timeout=15), second.result(timeout=15)]
    assert [r.status_code for r in replies] == [200, 200]
    artifacts = store.list_artifacts()
    assert len(calls) == 1
    assert len(artifacts) == 1
    assert replies[0].json()["receipt"]["receipt_id"] == replies[1].json()["receipt"]["receipt_id"]
    assert replies[0].json()["artifact_id"] == replies[1].json()["artifact_id"]


def test_transport_key_conflict_rejects_different_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = research_main.build_research_orchestrator_store(str(tmp_path / "owner"))
    monkeypatch.setattr(research_main, "store", store)
    monkeypatch.setenv("PANTHEON_QUANTLIB_BACKEND", "stub")
    from services.research.tests.test_research_orchestrator_http_service import _make_sample_quantlib_dataset
    client = TestClient(research_main.app)
    body1 = {
        "stage": {"stage_id": "s1", "stage_type": "derivatives_pricing_risk"},
        "plan": {"plan_id": "p1"}, "dataset": _make_sample_quantlib_dataset(),
        "run_id": "r1", "correlation_id": "c1", "downstream_key": "shared-worker-key",
    }
    r1 = client.post("/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json=body1)
    assert r1.status_code == 200

    body2 = {
        "stage": {"stage_id": "s1", "stage_type": "derivatives_pricing_risk"},
        "plan": {"plan_id": "p1"}, "dataset": _make_sample_quantlib_dataset(),
        "run_id": "r2", "correlation_id": "c2", "downstream_key": "shared-worker-key",
    }
    r2 = client.post("/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json=body2)
    assert r2.status_code == 409
    assert "already bound" in r2.json()["detail"].lower()


def test_duplicate_request_does_not_release_unrelated_barrier(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = research_main.build_research_orchestrator_store(str(tmp_path / "owner"))
    monkeypatch.setattr(research_main, "store", store)
    monkeypatch.setenv("PANTHEON_QUANTLIB_BACKEND", "stub")
    from services.research.quantlib.adapter import quantlib_adapter
    backend_entered, release_backend, unrelated_released = threading.Event(), threading.Event(), threading.Event()
    unrelated_barrier = threading.Barrier(2, timeout=5)
    original = quantlib_adapter.run_quantlib_workflow

    def unrelated_work():
        barrier = unrelated_barrier
        try:
            barrier.wait()
            unrelated_released.set()
        except threading.BrokenBarrierError:
            pass

    def backend(*args, **kwargs):
        backend_entered.set()
        assert release_backend.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(quantlib_adapter, "run_quantlib_workflow", backend)
    from services.research.tests.test_research_orchestrator_http_service import _make_sample_quantlib_dataset
    body = {"stage": {"stage_id": "s1"}, "plan": {"plan_id": "p1"}, "run_id": "r1",
            "dataset": _make_sample_quantlib_dataset(), "correlation_id": "c1"}
    client = TestClient(research_main.app)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        unrelated = pool.submit(unrelated_work)
        first = pool.submit(client.post, "/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json=body)
        assert backend_entered.wait(5)
        second = pool.submit(client.post, "/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json=body)
        interfered = unrelated_released.wait(1)
        release_backend.set()
        unrelated_barrier.abort()
        assert [first.result(timeout=5).status_code, second.result(timeout=5).status_code] == [200, 200]
        unrelated.result(timeout=5)
    assert not interfered, "Production request released an unrelated thread barrier by scanning its stack"


def test_evidence_synthesis_resolves_artifacts_and_reaches_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient
    store = research_main.build_research_orchestrator_store(str(tmp_path / "owner"))
    monkeypatch.setattr(research_main, "store", store)
    monkeypatch.setenv("PANTHEON_OPENCLAW_BACKEND", "real")
    monkeypatch.delenv("PANTHEON_OPENCLAW_UNAVAILABLE", raising=False)
    monkeypatch.setattr(OpenClawOpsClient, "configured", property(lambda self: True))
    calls = []

    def fake_extraction(self, **kwargs):
        calls.append(kwargs)
        return {
            "data": {
                "output": {
                    "structured_data": {
                        "summary": "Synthesized evidence indicates positive risk profile",
                        "interpretation": "Strong Sharpe ratio 1.8 across historical tests",
                        "recommendation": "accept",
                        "confidence_score": 0.94,
                    }
                }
            }
        }

    monkeypatch.setattr(OpenClawOpsClient, "invoke_structured_extraction", fake_extraction)

    store.put_artifact({
        "artifact_id": "art-prior-1", "id": "art-prior-1", "run_id": "prior-run",
        "artifact_family": "prototype_backtest_artifact", "payload": {"summary": "sharpe 1.8"},
    })
    client = TestClient(research_main.app)
    task = client.post("/api/research-orchestrator/tasks", json={"title": "synth", "objective": "synthesis"}).json()
    run = client.post(f"/api/research-orchestrator/tasks/{task['task_id']}/runs", json={
        "adapter": "openclaw_result_synthesis", "requested_mode": "real", "dispatch_mode": "real",
        "input_refs": [{"type": "stage", "id": "stage-synth"}],
        "idempotency_key": "synth-run",
    }).json()

    response = client.post("/api/research-orchestrator/stages/evidence_synthesis/execute", json={
        "stage": {"stage_id": "stage-synth", "stage_type": "evidence_synthesis"},
        "plan": {"plan_id": "plan-synth", "task_id": task["task_id"]},
        "run_id": run["run_id"],
        "correlation_id": "corr-synth",
        "requested_mode": "real",
        "artifact_refs": [{"artifact_id": "art-prior-1"}],
    })
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "succeeded"
    assert res_data["provenance"] == "real"
    assert res_data["receipt"]["executor"]
    assert res_data["artifact_id"]
    art = store.get_artifact(res_data["artifact_id"])
    assert art is not None
    assert art["provenance"] == "real"
    assert art["payload"]["synthesized_by"] == "openclaw_result_synthesis"
    assert len(art["payload"]["input_artifacts"]) == 1
    assert art["payload"]["input_artifacts"][0]["artifact_id"] == "art-prior-1"
    assert len(calls) == 1
    assert "sharpe 1.8" in calls[0]["prompt"]


def test_real_synthesis_rejects_missing_persisted_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient
    store = research_main.build_research_orchestrator_store(str(tmp_path / "owner"))
    monkeypatch.setattr(research_main, "store", store)
    monkeypatch.setenv("PANTHEON_OPENCLAW_BACKEND", "real")
    monkeypatch.delenv("PANTHEON_OPENCLAW_UNAVAILABLE", raising=False)
    monkeypatch.setattr(OpenClawOpsClient, "configured", property(lambda self: True))
    calls = []

    def provider(self, **kwargs):
        calls.append(kwargs)
        return {"data": {"output": {"structured_data": {"summary": "report", "interpretation": "no evidence", "recommendation": "reject"}}}}

    monkeypatch.setattr(OpenClawOpsClient, "invoke_structured_extraction", provider)
    response = TestClient(research_main.app).post("/api/research-orchestrator/stages/evidence_synthesis/execute", json={
        "stage": {"stage_id": "s1"}, "plan": {"plan_id": "p1"}, "run_id": "r1",
        "correlation_id": "c1", "requested_mode": "real", "artifact_refs": [{"artifact_id": "does-not-exist"}],
    })
    assert response.status_code != 200, f"missing artifact accepted: {response.json()}, provider_calls={len(calls)}"
    assert len(calls) == 0


def test_evidence_synthesis_resolves_predecessor_artifacts_from_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = research_main.build_research_orchestrator_store(str(tmp_path / "owner"))
    monkeypatch.setattr(research_main, "store", store)
    task_id = "task-dag-synth"
    store.put_task({"task_id": task_id, "title": "DAG task"})
    store.put_artifact({
        "artifact_id": "art-dep-1", "id": "art-dep-1", "run_id": "r-prior", "task_id": task_id,
        "stage_id": "stage-prior", "artifact_family": "backtest_artifact", "payload": {"metric": "val"},
    })
    store.put_run({
        "run_id": "r-prior", "id": "r-prior", "task_id": task_id, "stage_id": "stage-prior",
        "status": "completed", "artifact_refs": [{"artifact_id": "art-dep-1"}],
    })
    store.put_run({
        "run_id": "r-synth", "id": "r-synth", "task_id": task_id, "stage_id": "stage-synth",
        "status": "queued", "requested_mode": "stub",
    })
    client = TestClient(research_main.app)
    response = client.post("/api/research-orchestrator/stages/evidence_synthesis/execute", json={
        "stage": {"stage_id": "stage-synth", "stage_type": "evidence_synthesis", "dependencies": ["stage-prior"]},
        "plan": {"plan_id": "plan-dag", "task_id": task_id},
        "run_id": "r-synth", "correlation_id": "c-synth", "requested_mode": "stub",
    })
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "succeeded"
    art = store.get_artifact(res_data["artifact_id"])
    assert art is not None
    assert len(art["payload"]["input_artifacts"]) == 1
    assert art["payload"]["input_artifacts"][0]["artifact_id"] == "art-dep-1"


def test_evidence_synthesis_unavailable_provider_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = research_main.build_research_orchestrator_store(str(tmp_path / "owner"))
    monkeypatch.setattr(research_main, "store", store)
    monkeypatch.setenv("PANTHEON_OPENCLAW_UNAVAILABLE", "1")
    client = TestClient(research_main.app)
    response = client.post("/api/research-orchestrator/stages/evidence_synthesis/execute", json={
        "stage": {"stage_id": "stage-synth", "stage_type": "evidence_synthesis"},
        "plan": {"plan_id": "plan-synth"},
        "run_id": "r-synth-fail",
        "correlation_id": "corr-fail",
        "artifact_refs": [{"artifact_id": "art-prior-1"}],
    })
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


def test_generated_foreign_artifact_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = research_main.build_research_orchestrator_store(str(tmp_path / "owner"))
    monkeypatch.setattr(research_main, "store", store)
    monkeypatch.setenv("PANTHEON_QUANTLIB_BACKEND", "stub")
    monkeypatch.setenv("PANTHEON_OPENCLAW_BACKEND", "real")
    monkeypatch.delenv("PANTHEON_OPENCLAW_UNAVAILABLE", raising=False)
    from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient
    monkeypatch.setattr(OpenClawOpsClient, "configured", property(lambda self: True))
    calls = []
    def provider(self, **kwargs):
        calls.append(kwargs)
        return {"data": {"output": {"structured_data": {"summary": "ok", "interpretation": "ok", "recommendation": "reject"}}}}
    monkeypatch.setattr(OpenClawOpsClient, "invoke_structured_extraction", provider)
    from services.research.tests.test_research_orchestrator_http_service import _make_sample_quantlib_dataset
    client = TestClient(research_main.app)

    store.put_run({"run_id": "r-b", "task_id": "t-b", "stage_id": "price", "tenant_id": "tenant-b", "status": "queued"})
    response = client.post("/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json={
        "stage": {"stage_id": "price"}, "plan": {"plan_id": "p-b", "task_id": "t-b", "tenant_id": "tenant-b"},
        "run_id": "r-b", "correlation_id": "c-b", "dataset": _make_sample_quantlib_dataset(),
    })
    assert response.status_code == 200, response.text
    artifact_id = response.json()["artifact_id"]

    store.put_run({"run_id": "r-a", "task_id": "t-a", "stage_id": "synth", "tenant_id": "tenant-a", "status": "queued"})
    response = client.post("/api/research-orchestrator/stages/evidence_synthesis/execute", json={
        "stage": {"stage_id": "synth"}, "plan": {"plan_id": "p-a", "task_id": "t-a", "tenant_id": "tenant-a"},
        "run_id": "r-a", "correlation_id": "c-a", "requested_mode": "real", "artifact_refs": [{"artifact_id": artifact_id}],
    })
    assert response.status_code in (400, 403, 404), f"cross-tenant artifact accepted: status={response.status_code}, provider_calls={len(calls)}"


def test_provider_wrong_field_types_are_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = research_main.build_research_orchestrator_store(str(tmp_path / "owner"))
    monkeypatch.setattr(research_main, "store", store)
    monkeypatch.setenv("PANTHEON_OPENCLAW_BACKEND", "real")
    monkeypatch.delenv("PANTHEON_OPENCLAW_UNAVAILABLE", raising=False)
    from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient
    monkeypatch.setattr(OpenClawOpsClient, "configured", property(lambda self: True))
    store.put_artifact({"artifact_id": "a", "run_id": "prior", "task_id": "t", "tenant_id": "tenant-a", "payload": {"metric": 1}})
    monkeypatch.setattr(OpenClawOpsClient, "invoke_structured_extraction", lambda self, **kw: {"data": {"output": {"structured_data": {"summary": ["not text"], "interpretation": {"not": "text"}, "recommendation": 42}}}})
    client = TestClient(research_main.app)
    response = client.post("/api/research-orchestrator/stages/evidence_synthesis/execute", json={
        "stage": {"stage_id": "synth"}, "plan": {"plan_id": "p", "tenant_id": "tenant-a"}, "run_id": "r",
        "correlation_id": "c", "requested_mode": "real", "artifact_refs": [{"artifact_id": "a"}],
    })
    assert response.status_code == 502, f"invalid schema accepted: status={response.status_code}"


def test_omitted_stage_id_cannot_bypass_owner_claim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = research_main.build_research_orchestrator_store(str(tmp_path / "owner"))
    monkeypatch.setattr(research_main, "store", store)
    monkeypatch.setenv("PANTHEON_QUANTLIB_BACKEND", "stub")
    from services.research.quantlib.adapter import quantlib_adapter
    from services.research.tests.test_research_orchestrator_http_service import _make_sample_quantlib_dataset
    store.put_run({"run_id": "r", "task_id": "t", "stage_id": "price", "status": "queued", "requested_mode": "stub"})
    entered, second_entered, release = threading.Event(), threading.Event(), threading.Event()
    calls = []
    original = quantlib_adapter.run_quantlib_workflow
    def backend(*args, **kwargs):
        calls.append(1)
        (entered if len(calls) == 1 else second_entered).set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(quantlib_adapter, "run_quantlib_workflow", backend)
    client = TestClient(research_main.app)
    body = {"plan": {"plan_id": "p"}, "run_id": "r", "correlation_id": "c", "dataset": _make_sample_quantlib_dataset()}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, "/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json={**body, "stage": {"stage_id": "price", "stage_type": "derivatives_pricing_risk"}})
        assert entered.wait(5)
        second = pool.submit(client.post, "/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json={**body, "stage": {"stage_type": "derivatives_pricing_risk"}})
        second_entered.wait(1)
        release.set()
        replies = [first.result(timeout=10), second.result(timeout=10)]
    assert len(calls) == 1, f"backend_calls={len(calls)}, status={[r.status_code for r in replies]}, artifacts={len(store.list_artifacts())}"


def _make_invariants_backend(monkeypatch):
    from services.research.quantlib.adapter import quantlib_adapter
    entered, second_entered, release = threading.Event(), threading.Event(), threading.Event()
    calls = []
    original = quantlib_adapter.run_quantlib_workflow
    def backend(*args, **kwargs):
        calls.append(1)
        (entered if len(calls) == 1 else second_entered).set()
        assert release.wait(8)
        return original(*args, **kwargs)
    monkeypatch.setattr(quantlib_adapter, "run_quantlib_workflow", backend)
    return entered, second_entered, release, calls


def test_cancellation_fences_artifact_publication(client, monkeypatch):
    from services.research.tests.test_research_orchestrator_http_service import _make_sample_quantlib_dataset
    from concurrent.futures import ThreadPoolExecutor
    store = research_main.store
    monkeypatch.setenv("PANTHEON_QUANTLIB_BACKEND", "stub")
    store.put_task({"task_id": "t", "tenant_id": "tenant-a", "status": "ready"})
    store.put_run({"run_id": "r", "task_id": "t", "stage_id": "price", "tenant_id": "tenant-a", "status": "queued", "requested_mode": "stub"})
    body = {"run_id": "r", "correlation_id": "c", "stage": {"stage_id": "price"},
            "plan": {"plan_id": "p", "task_id": "t", "tenant_id": "tenant-a"},
            "dataset": _make_sample_quantlib_dataset()}
    entered, _, release, calls = _make_invariants_backend(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, "/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json=body)
        try:
            assert entered.wait(5)
            cancelled = client.post("/api/research-orchestrator/tasks/t/cancel")
            assert cancelled.status_code == 200, cancelled.text
        finally:
            release.set()
        result = future.result(timeout=10)
    assert result.status_code == 409, result.text
    assert store.get_run("r")["status"] == "canceled"
    assert store.list_artifacts() == [], "Canceled execution published an output artifact before checking the fence"


def test_age_alone_cannot_steal_a_live_execution_claim(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from services.research.tests.test_research_orchestrator_http_service import _make_sample_quantlib_dataset
    from concurrent.futures import ThreadPoolExecutor
    store = research_main.store
    monkeypatch.setenv("PANTHEON_QUANTLIB_BACKEND", "stub")
    store.put_task({"task_id": "t", "tenant_id": "tenant-a", "status": "ready"})
    store.put_run({"run_id": "r", "task_id": "t", "stage_id": "price", "tenant_id": "tenant-a", "status": "queued", "requested_mode": "stub"})
    body = {"run_id": "r", "correlation_id": "c", "stage": {"stage_id": "price"},
            "plan": {"plan_id": "p", "task_id": "t", "tenant_id": "tenant-a"},
            "dataset": _make_sample_quantlib_dataset()}
    entered, second_entered, release, calls = _make_invariants_backend(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, "/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json=body)
        try:
            assert entered.wait(5)
            path = store.data_dir / "stage_executions.json"
            key = "agora-stage-claim:r:price"
            claim = store._get_record(path, key)
            assert claim and claim["status"] == "in_progress"
            claim["claimed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
            store._put_record(path, key, claim)
            second = pool.submit(client.post, "/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json=body)
            second_entered.wait(1)
        finally:
            release.set()
        results = [first.result(timeout=10), second.result(timeout=10)]
    assert len(calls) == 1, {"backend_calls": len(calls), "statuses": [r.status_code for r in results]}
    assert len(store.list_artifacts()) == 1


def test_unavailable_synthesis_does_not_leave_phantom_running_claim(client, monkeypatch):
    store = research_main.store
    store.put_task({"task_id": "t", "tenant_id": "tenant-a", "status": "ready"})
    store.put_run({"run_id": "s", "task_id": "t", "stage_id": "synth", "tenant_id": "tenant-a", "status": "queued", "requested_mode": "real"})
    monkeypatch.setenv("PANTHEON_OPENCLAW_UNAVAILABLE", "1")
    result = client.post("/api/research-orchestrator/stages/evidence_synthesis/execute", json={
        "run_id": "s", "correlation_id": "cs", "stage": {"stage_id": "synth"},
        "plan": {"plan_id": "p", "task_id": "t", "tenant_id": "tenant-a"}, "requested_mode": "real"})
    assert result.status_code == 503, result.text
    assert store.get_run("s")["status"] in {"failed", "rejected"}, store.get_run("s")


def test_real_provider_cannot_promote_simulated_input_evidence(client, monkeypatch):
    from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient
    from services.research.tests.test_research_orchestrator_http_service import _make_sample_quantlib_dataset
    store = research_main.store
    monkeypatch.setenv("PANTHEON_QUANTLIB_BACKEND", "stub")
    monkeypatch.setenv("PANTHEON_OPENCLAW_BACKEND", "real")
    monkeypatch.delenv("PANTHEON_OPENCLAW_UNAVAILABLE", raising=False)
    monkeypatch.setattr(OpenClawOpsClient, "configured", property(lambda self: True))
    monkeypatch.setattr(OpenClawOpsClient, "invoke_structured_extraction", lambda self, **kw: {
        "data": {"output": {"structured_data": {"summary": "Summary", "interpretation": "Interpretation", "recommendation": "hold"}}}
    })
    store.put_task({"task_id": "t", "tenant_id": "tenant-a", "status": "ready"})
    store.put_run({"run_id": "r", "task_id": "t", "stage_id": "price", "tenant_id": "tenant-a", "status": "queued", "requested_mode": "stub"})
    body = {"run_id": "r", "correlation_id": "c", "stage": {"stage_id": "price"},
            "plan": {"plan_id": "p", "task_id": "t", "tenant_id": "tenant-a"},
            "dataset": _make_sample_quantlib_dataset()}
    priced = client.post("/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json=body)
    assert priced.status_code == 200, priced.text
    assert priced.json()["provenance"] == "simulation"
    store.put_run({"run_id": "s", "task_id": "t", "stage_id": "synth", "tenant_id": "tenant-a", "status": "queued", "requested_mode": "real"})
    result = client.post("/api/research-orchestrator/stages/evidence_synthesis/execute", json={
        "run_id": "s", "correlation_id": "cs", "stage": {"stage_id": "synth"},
        "plan": {"plan_id": "p", "task_id": "t", "tenant_id": "tenant-a"}, "requested_mode": "real",
        "artifact_refs": [{"artifact_id": priced.json()["artifact_id"]}]})
    assert result.status_code in {200, 400, 409, 422}, result.text
    if result.status_code == 200:
        assert result.json()["provenance"] == "simulation", result.json()
        assert result.json()["receipt"]["mode"] == "simulation"


def test_cancel_between_fence_check_and_artifact_write(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from services.research.tests.test_research_orchestrator_http_service import _make_sample_quantlib_dataset

    store = research_main.store
    monkeypatch.setenv("PANTHEON_QUANTLIB_BACKEND", "stub")
    store.put_task({"task_id": "t-canc", "tenant_id": "a", "status": "running"})
    store.put_run({"run_id": "r-canc", "task_id": "t-canc", "stage_id": "s", "tenant_id": "a", "status": "queued", "requested_mode": "stub"})

    entered, release = threading.Event(), threading.Event()
    original = store.put_artifact

    def paused_put(artifact):
        entered.set()
        assert release.wait(5)
        return original(artifact)

    monkeypatch.setattr(store, "put_artifact", paused_put)
    body = {"run_id": "r-canc", "correlation_id": "c", "stage": {"stage_id": "s"}, "plan": {"plan_id": "p", "task_id": "t-canc"}, "dataset": _make_sample_quantlib_dataset()}
    with ThreadPoolExecutor(max_workers=2) as pool:
        execution = pool.submit(client.post, "/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json=body)
        try:
            assert entered.wait(5)
            cancellation = pool.submit(client.post, "/api/research-orchestrator/tasks/t-canc/cancel")
            canceled = cancellation.result(timeout=2)
            assert canceled.status_code == 200
            assert store.get_run("r-canc")["status"] == "canceled"
        finally:
            release.set()
        result = execution.result(timeout=10)
    assert not [a for a in store.list_artifacts() if a.get("run_id") == "r-canc"], f"cancel returned before publication, but execute={result.status_code}, run={store.get_run('r-canc')['status']}, artifacts={len(store.list_artifacts())}"


def test_synthesis_validation_failure_releases_claim(client: TestClient) -> None:
    store = research_main.store
    store.put_task({"task_id": "t-synth", "tenant_id": "a", "status": "running"})
    store.put_run({"run_id": "r-synth", "task_id": "t-synth", "stage_id": "s", "tenant_id": "a", "status": "queued", "requested_mode": "stub"})
    result = client.post("/api/research-orchestrator/stages/evidence_synthesis/execute", json={"run_id": "r-synth", "correlation_id": "c", "stage": {"stage_id": "s"}, "plan": {"plan_id": "p", "task_id": "t-synth"}, "artifact_refs": [{"artifact_id": "missing"}]})
    assert result.status_code == 400
    claim = store._get_record(store.data_dir / "stage_executions.json", "agora-stage-claim:r-synth:s")
    assert claim["status"] == "failed"
    assert store.get_run("r-synth")["status"] == "failed"


def test_owner_restart_reconciles_abandoned_execution_claim(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from services.research.tests.test_research_orchestrator_http_service import _make_sample_quantlib_dataset
    store = research_main.store
    monkeypatch.setenv("PANTHEON_QUANTLIB_BACKEND", "stub")
    store.put_task({"task_id": "t-rec", "tenant_id": "a", "status": "running"})
    stage = {"stage_id": "s", "stage_type": "derivatives_pricing_risk"}
    plan = {"plan_id": "p", "task_id": "t-rec", "stages": [stage], "dataset": _make_sample_quantlib_dataset()}
    store.put_run({"run_id": "r-rec", "task_id": "t-rec", "stage_id": "s", "tenant_id": "a", "status": "running", "adapter": "derivatives_pricing_risk", "parameters": {"stage": stage, "plan": plan, "dataset": plan["dataset"]}})
    store._put_record(store.data_dir / "stage_executions.json", "agora-stage-claim:r-rec:s", {"status": "in_progress", "claim_token": "dead-owner-process", "claimed_at": "2026-01-01T00:00:00Z", "run_id": "r-rec", "stage_id": "s"})
    research_main.resume_queued_plan_stages()
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if store.get_run("r-rec")["status"] not in {"queued", "running"}:
            break
        time.sleep(0.05)
    final = store.get_run("r-rec")
    assert final["status"] == "completed", f"restart produced {final['status']}: {final.get('error')}"


def test_artifact_only_synthesis_dispatch_runs_without_restart(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = research_main.store
    monkeypatch.setenv("PANTHEON_OPENCLAW_BACKEND", "stub")
    monkeypatch.delenv("PANTHEON_OPENCLAW_UNAVAILABLE", raising=False)
    task = client.post(
        "/api/research-orchestrator/tasks",
        json={"title": "report", "objective": "synthesize persisted evidence"},
    ).json()
    tid = task["task_id"]
    store.put_artifact(
        {
            "artifact_id": "input",
            "task_id": tid,
            "payload": {"observed": 42},
            "provenance": "simulation",
        }
    )
    stage = {
        "stage_id": "report",
        "stage_type": "evidence_synthesis",
        "artifact_refs": [{"artifact_id": "input"}],
    }
    plan = {"plan_id": "p", "task_id": tid, "stages": [stage]}
    response = client.post(
        f"/api/research-orchestrator/tasks/{tid}/runs",
        json={
            "adapter": "openclaw_result_synthesis",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "parameters": {"stage": stage, "plan": plan},
            "idempotency_key": "report-run",
        },
    )
    assert response.status_code == 201, response.text
    rid = response.json()["run_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and store.get_run(rid)["status"] in {"queued", "running"}:
        time.sleep(0.01)
    dispatch_status = store.get_run(rid)["status"]
    direct = client.post(
        "/api/research-orchestrator/stages/evidence_synthesis/execute",
        json={"run_id": rid, "correlation_id": "c", "stage": stage, "plan": plan},
    )
    assert direct.status_code == 200, direct.text
    assert store.get_run(rid)["status"] == "completed"
    assert dispatch_status == "completed", f"Normal task-runs dispatch remained {dispatch_status}; same inputs succeed via direct execute"


def _make_test_dataset(ds_id: str, tenant_id: str = "tenant-001") -> Dict[str, Any]:
    ds = dict(_valid_multimodal_dataset())
    ds["dataset_id"] = ds_id
    ds["source_dataset_refs"] = [ds_id]
    ds["tenant_id"] = tenant_id
    return ds


def test_multi_stage_dag_distinct_datasets_reach_backends(client: TestClient) -> None:
    store = research_main.store
    task = client.post(
        "/api/research-orchestrator/tasks",
        json={"title": "multi-ds", "objective": "execute distinct governed datasets", "tenant_id": "tenant-001"},
    ).json()
    tid = task["task_id"]
    ds_a = _make_test_dataset("ds-A", "tenant-001")
    ds_b = _make_test_dataset("ds-B", "tenant-001")
    s1 = {
        "stage_id": "stage-1", "stage_type": "prototype_backtest",
        "input_refs": [{"type": "dataset", "id": "ds-A"}], "dataset": ds_a,
    }
    s2 = {
        "stage_id": "stage-2", "stage_type": "prototype_backtest",
        "depends_on": ["stage-1"], "input_refs": [{"type": "dataset", "id": "ds-B"}], "dataset": ds_b,
    }
    plan = {"plan_id": "plan-distinct", "task_id": tid, "tenant_id": "tenant-001", "stages": [s1, s2]}

    resp = client.post(
        f"/api/research-orchestrator/tasks/{tid}/runs",
        json={
            "adapter": "prototype_backtest", "requested_mode": "stub", "dispatch_mode": "stub",
            "parameters": {"stage": s1, "plan": plan, "dataset": ds_a, "tenant_id": "tenant-001"},
            "input_refs": [{"type": "stage", "id": "stage-1"}, {"type": "dataset", "id": "ds-A"}],
            "idempotency_key": "distinct-ds-run-s1",
        },
    )
    assert resp.status_code == 201, resp.text

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        runs = [r for r in store.list_runs() if str(r.get("task_id")) == tid]
        if len(runs) >= 2 and all(str(r.get("status") or "").lower() == "completed" for r in runs):
            break
        time.sleep(0.05)

    all_runs = {str(r.get("stage_id")): r for r in store.list_runs() if str(r.get("task_id")) == tid}
    assert "stage-1" in all_runs and "stage-2" in all_runs
    r1, r2 = all_runs["stage-1"], all_runs["stage-2"]

    assert r1["status"] == "completed"
    assert r2["status"] == "completed"

    assert r1["parameters"]["dataset"]["dataset_id"] == "ds-A"
    assert any(ref.get("type") == "dataset" and ref.get("id") == "ds-A" for ref in r1["input_refs"] if isinstance(ref, dict))

    assert r2["parameters"]["dataset"]["dataset_id"] == "ds-B"
    assert any(ref.get("type") == "dataset" and ref.get("id") == "ds-B" for ref in r2["input_refs"] if isinstance(ref, dict))
    assert not any(ref.get("id") == "ds-A" for ref in r2["input_refs"] if isinstance(ref, dict))

    assert r1["artifact_refs"][0]["artifact_id"] != r2["artifact_refs"][0]["artifact_id"]


def test_multi_stage_dag_missing_dataset_fails_closed(client: TestClient) -> None:
    store = research_main.store
    task = client.post(
        "/api/research-orchestrator/tasks",
        json={"title": "missing-ds", "objective": "fail closed when successor dataset missing", "tenant_id": "tenant-001"},
    ).json()
    tid = task["task_id"]
    ds_a = _make_test_dataset("ds-A", "tenant-001")
    s1 = {
        "stage_id": "stage-1", "stage_type": "prototype_backtest",
        "input_refs": [{"type": "dataset", "id": "ds-A"}], "dataset": ds_a,
    }
    s2 = {
        "stage_id": "stage-2", "stage_type": "prototype_backtest",
        "depends_on": ["stage-1"], "input_refs": [{"type": "dataset", "id": "ds-B"}],
    }
    plan = {"plan_id": "plan-missing-ds", "task_id": tid, "tenant_id": "tenant-001", "stages": [s1, s2], "dataset": ds_a}

    resp = client.post(
        f"/api/research-orchestrator/tasks/{tid}/runs",
        json={
            "adapter": "prototype_backtest", "requested_mode": "stub", "dispatch_mode": "stub",
            "parameters": {"stage": s1, "plan": plan, "dataset": ds_a, "tenant_id": "tenant-001"},
            "input_refs": [{"type": "stage", "id": "stage-1"}, {"type": "dataset", "id": "ds-A"}],
            "idempotency_key": "missing-ds-run-s1",
        },
    )
    assert resp.status_code == 201, resp.text

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        runs = [r for r in store.list_runs() if str(r.get("task_id")) == tid]
        if len(runs) >= 2:
            break
        time.sleep(0.05)

    all_runs = {str(r.get("stage_id")): r for r in store.list_runs() if str(r.get("task_id")) == tid}
    assert "stage-1" in all_runs
    assert "stage-2" in all_runs
    r1, r2 = all_runs["stage-1"], all_runs["stage-2"]

    assert r1["status"] == "completed"
    assert r2["status"] == "failed"
    assert "unavailable" in str(r2.get("error") or "").lower()
    assert not r2.get("artifact_refs")

    direct_resp = client.post(
        "/api/research-orchestrator/stages/prototype_backtest/execute",
        json={
            "run_id": r2["run_id"], "correlation_id": "corr-missing-test",
            "stage": s2, "plan": plan, "dataset": ds_a,
        },
    )
    assert direct_resp.status_code == 400
    assert "mismatch" in direct_resp.text.lower()


def test_multi_stage_dag_foreign_tenant_fails_closed(client: TestClient) -> None:
    store = research_main.store
    task = client.post(
        "/api/research-orchestrator/tasks",
        json={"title": "foreign-tenant", "objective": "fail closed across tenant boundary", "tenant_id": "tenant-alpha"},
    ).json()
    tid = task["task_id"]
    ds_a = _make_test_dataset("ds-A", "tenant-alpha")
    ds_b_foreign = _make_test_dataset("ds-B", "tenant-beta")
    s1 = {
        "stage_id": "stage-1", "stage_type": "prototype_backtest",
        "input_refs": [{"type": "dataset", "id": "ds-A"}], "dataset": ds_a,
    }
    s2 = {
        "stage_id": "stage-2", "stage_type": "prototype_backtest",
        "depends_on": ["stage-1"], "input_refs": [{"type": "dataset", "id": "ds-B"}], "dataset": ds_b_foreign,
    }
    plan = {"plan_id": "plan-foreign", "task_id": tid, "tenant_id": "tenant-alpha", "stages": [s1, s2]}

    resp = client.post(
        f"/api/research-orchestrator/tasks/{tid}/runs",
        json={
            "adapter": "prototype_backtest", "requested_mode": "stub", "dispatch_mode": "stub",
            "parameters": {"stage": s1, "plan": plan, "dataset": ds_a, "tenant_id": "tenant-alpha"},
            "input_refs": [{"type": "stage", "id": "stage-1"}, {"type": "dataset", "id": "ds-A"}],
            "idempotency_key": "foreign-ds-run-s1",
        },
    )
    assert resp.status_code == 201, resp.text

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        runs = [r for r in store.list_runs() if str(r.get("task_id")) == tid]
        if len(runs) >= 2:
            break
        time.sleep(0.05)

    all_runs = {str(r.get("stage_id")): r for r in store.list_runs() if str(r.get("task_id")) == tid}
    assert "stage-1" in all_runs
    assert "stage-2" in all_runs
    r1, r2 = all_runs["stage-1"], all_runs["stage-2"]

    assert r1["status"] == "completed"
    assert r2["status"] == "failed"
    assert "tenant" in str(r2.get("error") or "").lower()

    direct_resp = client.post(
        "/api/research-orchestrator/stages/prototype_backtest/execute",
        json={
            "run_id": r2["run_id"], "correlation_id": "corr-foreign-test",
            "stage": s2, "plan": plan, "dataset": ds_b_foreign,
        },
    )
    assert direct_resp.status_code == 403
    assert "tenant" in direct_resp.text.lower()


def test_multi_stage_dag_restart_and_readback_preserves_dataset_binding(client: TestClient) -> None:
    store = research_main.store
    task = client.post(
        "/api/research-orchestrator/tasks",
        json={"title": "restart-preserves-binding", "objective": "verify restart retains dataset bindings", "tenant_id": "tenant-001"},
    ).json()
    tid = task["task_id"]
    ds_a = _make_test_dataset("ds-A", "tenant-001")
    ds_b = _make_test_dataset("ds-B", "tenant-001")
    s1 = {
        "stage_id": "stage-1", "stage_type": "prototype_backtest",
        "input_refs": [{"type": "dataset", "id": "ds-A"}], "dataset": ds_a,
    }
    s2 = {
        "stage_id": "stage-2", "stage_type": "prototype_backtest",
        "depends_on": ["stage-1"], "input_refs": [{"type": "dataset", "id": "ds-B"}], "dataset": ds_b,
    }
    plan = {"plan_id": "plan-restart", "task_id": tid, "tenant_id": "tenant-001", "stages": [s1, s2]}

    resp = client.post(
        f"/api/research-orchestrator/tasks/{tid}/runs",
        json={
            "adapter": "prototype_backtest", "requested_mode": "stub", "dispatch_mode": "stub",
            "parameters": {"stage": s1, "plan": plan, "dataset": ds_a, "tenant_id": "tenant-001"},
            "input_refs": [{"type": "stage", "id": "stage-1"}, {"type": "dataset", "id": "ds-A"}],
            "idempotency_key": "restart-binding-s1",
        },
    )
    assert resp.status_code == 201
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        runs = [r for r in store.list_runs() if str(r.get("task_id")) == tid]
        if len(runs) >= 2 and all(str(r.get("status") or "").lower() == "completed" for r in runs):
            break
        time.sleep(0.05)

    all_runs = {str(r.get("stage_id")): r for r in store.list_runs() if str(r.get("task_id")) == tid}
    r2_id = all_runs["stage-2"]["run_id"]

    research_main.resume_queued_plan_stages()

    readback = client.get(f"/api/research-orchestrator/runs/{r2_id}").json()
    assert readback["parameters"]["dataset"]["dataset_id"] == "ds-B"
    assert any(ref.get("type") == "dataset" and ref.get("id") == "ds-B" for ref in readback["input_refs"] if isinstance(ref, dict))
    assert not any(ref.get("id") == "ds-A" for ref in readback["input_refs"] if isinstance(ref, dict))

    status_readback = client.get(f"/api/research-orchestrator/runs/{r2_id}/status").json()
    assert status_readback["status"] == "completed"


@pytest.mark.parametrize("override", ["requested_mode", "dispatch_mode", "stage_routing"])
def test_http_cannot_downgrade_a_persisted_real_run_to_stub(client: TestClient, monkeypatch: pytest.MonkeyPatch, override: str) -> None:
    store = research_main.store
    monkeypatch.setenv("PANTHEON_QUANTLIB_BACKEND", "stub")
    store.put_task({"task_id": f"t-persisted-{override}", "tenant_id": "tenant-a", "status": "ready"})
    store.put_run({
        "run_id": f"r-persisted-{override}", "task_id": f"t-persisted-{override}",
        "stage_id": "price", "tenant_id": "tenant-a", "status": "queued",
        "requested_mode": "real", "dispatch_mode": "real", "adapter": "stage:quantlib",
    })
    from services.research.tests.test_research_orchestrator_http_service import _make_sample_quantlib_dataset
    payload: Dict[str, Any] = {
        "run_id": f"r-persisted-{override}", "correlation_id": "c",
        "stage": {"stage_id": "price"},
        "plan": {"plan_id": "p", "task_id": f"t-persisted-{override}", "tenant_id": "tenant-a"},
        "dataset": _make_sample_quantlib_dataset(),
    }
    if override == "stage_routing":
        payload["stage"]["routing"] = {"backend_mode": "stub"}
    else:
        payload[override] = "stub"
    response = client.post("/api/research-orchestrator/stages/derivatives_pricing_risk/execute", json=payload)
    assert response.status_code in {400, 409, 422, 503}
    assert store.get_run(f"r-persisted-{override}")["status"] != "completed"
    assert [a for a in store.list_artifacts() if a.get("run_id") == f"r-persisted-{override}"] == []



