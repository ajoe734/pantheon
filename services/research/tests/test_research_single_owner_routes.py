from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
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
def client(mock_write_owner: ResearchWriteOwner) -> TestClient:
    research_main.set_write_owner(mock_write_owner)
    try:
        yield TestClient(research_main.app)
    finally:
        research_main.set_write_owner(None)


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

    # Dispatch s2 -> s3 is now unblocked and automatically progresses
    d2 = client.post(f"/api/research-orchestrator/tasks/{task_id}/runs", json={
        "adapter": "statsmodels",
        "requested_mode": "stub",
        "dispatch_mode": "stub",
        "input_refs": [{"type": "stage", "id": "s2"}],
        "parameters": {"stage": plan["stages"][1], "plan": plan, "dataset": ds},
        "idempotency_key": f"idemp-dag-fanin-2-{task_id}",
    })
    assert d2.status_code == 201
    runs_after_s2 = [r for r in research_main.store.list_runs() if r.get("task_id") == task_id]
    runs_after_s2 = _wait_for_task_status(task_id, {"completed", "failed"}, expected_count=3)
    assert len(runs_after_s2) == 3
    run_by_stage = {r["stage_id"]: r for r in runs_after_s2}
    assert set(run_by_stage.keys()) == {"s1", "s2", "s3"}
    for sid in ("s1", "s2", "s3"):
        assert run_by_stage[sid]["status"] == "completed"


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
        "stages": [{"stage_id": "s1", "stage_type": "prototype_backtest", "status": "ready", "dependencies": []}],
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
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        retried_data = research_main.store.get_run(retried_data["run_id"])
        if retried_data["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)
    assert retried_data["status"] == "completed"
    assert retried_data.get("receipt") is not None
    assert len(retried_data.get("artifact_refs") or []) >= 1


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
