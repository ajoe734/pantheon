"""Comprehensive U10B Research and Jobs Action Receipts Test Suite.

Verifies real action execution closure for the Research/Jobs domain in Pantheon BFF
and services/research (RESEARCH-JOBS-ACTIONS-CLOSURE-CORRECTIVE-001):
1. Research orchestrator run cancel, cancellation fence, and late-completion discard
2. Research orchestrator run retry, attempt lineage, and eligibility gating
3. JobCommandAdapter real dispatch, receipts, and explicit fail-closed boundaries
4. ExperimentCommandAdapter real dispatch (cancel, retry, archive, invalidate)
5. Governance promotion and follow-up composition boundaries (GOV-PROMOTE-001, etc.)
6. Truthful allowedActions affordance projections
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.command_adapters.base import ActionUnavailableError
from services.control_plane.bff.command_adapters.experiment_adapter import (
    ExperimentCommandAdapter,
)
from services.control_plane.bff.command_adapters.job_adapter import JobCommandAdapter
from services.control_plane.bff.jobs.projection import (
    calculate_job_allowed_actions,
    project_job,
)
from services.control_plane.bff.ports.research_commands import (
    ResearchCommandConflictError,
    ResearchCommandNotFoundError,
    ResearchCommandUnavailableError,
    ResearchCommandsPort,
    create_research_commands_port,
)
from services.research.main import app as research_app
from services.research.write_owner import ResearchWriteOwner


# =============================================================================
# In-Memory Store Test Double
# =============================================================================


class _InMemoryOwnerStore:
    """In-memory stand-in for PostgresJsonOwnerStore used for isolated tests."""

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Any]] = {}

    def put(self, record_id: str, payload: Dict[str, Any]) -> None:
        self._data[record_id] = json.loads(json.dumps(payload))

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        record = self._data.get(record_id)
        return json.loads(json.dumps(record)) if record else None

    def list_all(self, *, conn: Optional[Any] = None) -> List[Dict[str, Any]]:
        return [json.loads(json.dumps(v)) for v in self._data.values()]


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def research_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a test client for research orchestrator service with isolated store."""
    data_dir = tmp_path / "research_orchestrator_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("RESEARCH_ORCHESTRATOR_DATA_DIR", str(data_dir))
    from services.research import main as research_main
    from services.research.store import build_research_orchestrator_store

    new_store = build_research_orchestrator_store(data_dir)
    monkeypatch.setattr(research_main, "store", new_store)

    return TestClient(research_main.app)


@pytest.fixture
def isolated_write_owner() -> ResearchWriteOwner:
    """Create a ResearchWriteOwner backed by isolated in-memory stores for testing."""
    return ResearchWriteOwner(
        tickets_store=_InMemoryOwnerStore(),
        experiments_store=_InMemoryOwnerStore(),
        notes_store=_InMemoryOwnerStore(),
    )


# =============================================================================
# 1. Research Orchestrator Run Cancel, Fence, and Late Completion
# =============================================================================


def test_orchestrator_cancel_run_and_cancellation_fence(research_client: TestClient) -> None:
    # 1. Create a task and a run
    task_resp = research_client.post(
        "/api/research-orchestrator/tasks",
        json={"title": "Test momentum strategy", "objective": "Alpha validation", "actor_id": "researcher-1"},
    )
    assert task_resp.status_code == 201
    task_id = task_resp.json()["task_id"]

    run_resp = research_client.post(
        f"/api/research-orchestrator/tasks/{task_id}/runs",
        json={"adapter": "stub", "requested_mode": "stub", "dispatch_mode": "stub"},
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["run_id"]
    assert run_resp.json()["status"] in ("dispatched", "queued")

    # 2. Cancel the run
    cancel_resp = research_client.post(
        f"/api/research-orchestrator/runs/{run_id}/cancel",
        json={"reason": "Operator halted experiment", "actor_id": "operator-1"},
    )
    assert cancel_resp.status_code == 200
    cancel_data = cancel_resp.json()
    assert cancel_data["status"] == "canceled"
    assert cancel_data["cancellation_fence"] is not None
    fence_time = cancel_data["cancellation_fence"]

    # 3. Readback confirms canceled status and cancellation_fence
    status_resp = research_client.get(f"/api/research-orchestrator/runs/{run_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "canceled"
    assert status_resp.json()["cancellation_fence"] == fence_time

    # 4. Late worker completion arriving after cancellation_fence is rejected with 409
    late_complete_resp = research_client.post(
        f"/api/research-orchestrator/runs/{run_id}/complete",
        json={
            "status": "completed",
            "metrics": {"sharpe": 1.5},
            "completed_at": "2099-12-31T23:59:59Z",  # Far in the future
        },
    )
    assert late_complete_resp.status_code == 409
    assert "fenced" in late_complete_resp.json()["detail"].lower()

    # 5. Status remains canceled (not overwritten)
    final_status = research_client.get(f"/api/research-orchestrator/runs/{run_id}").json()
    assert final_status["status"] == "canceled"


def test_orchestrator_cancel_conflict_when_already_terminal(research_client: TestClient) -> None:
    task_resp = research_client.post(
        "/api/research-orchestrator/tasks",
        json={"title": "Test terminal cancel", "objective": "Terminal cancel validation", "actor_id": "researcher-1"},
    )
    assert task_resp.status_code == 201
    task_id = task_resp.json()["task_id"]
    run_resp = research_client.post(
        f"/api/research-orchestrator/tasks/{task_id}/runs",
        json={"adapter": "stub", "requested_mode": "stub", "dispatch_mode": "stub"},
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["run_id"]

    # Complete the run
    comp_resp = research_client.post(
        f"/api/research-orchestrator/runs/{run_id}/complete",
        json={"status": "completed", "metrics": {"sharpe": 1.2}},
    )
    assert comp_resp.status_code == 200

    # Canceling a completed run conflicts with 409
    c2 = research_client.post(f"/api/research-orchestrator/runs/{run_id}/cancel")
    assert c2.status_code == 409
    assert "cannot be canceled" in c2.json()["detail"]


# =============================================================================
# 2. Research Orchestrator Run Retry and Attempt Lineage
# =============================================================================


def test_orchestrator_retry_run_creates_linked_attempt_lineage(
    research_client: TestClient,
) -> None:
    task_resp = research_client.post(
        "/api/research-orchestrator/tasks",
        json={"title": "Test retry lineage", "objective": "Retry lineage validation", "actor_id": "researcher-1"},
    )
    assert task_resp.status_code == 201
    task_id = task_resp.json()["task_id"]
    run1_resp = research_client.post(
        f"/api/research-orchestrator/tasks/{task_id}/runs",
        json={"adapter": "stub", "requested_mode": "stub", "dispatch_mode": "stub"},
    )
    assert run1_resp.status_code == 201
    run1_id = run1_resp.json()["run_id"]

    # Active run cannot be retried (409)
    retry_active = research_client.post(f"/api/research-orchestrator/runs/{run1_id}/retry")
    assert retry_active.status_code == 409
    assert "eligible terminal states" in retry_active.json()["detail"]

    # Cancel run1 so it becomes eligible
    research_client.post(f"/api/research-orchestrator/runs/{run1_id}/cancel")

    # Retry run1
    retry_resp = research_client.post(
        f"/api/research-orchestrator/runs/{run1_id}/retry",
        json={"actor_id": "operator-2", "idempotency_key": "retry-key-1"},
    )
    assert retry_resp.status_code == 201
    run2_data = retry_resp.json()
    run2_id = run2_data["run_id"]

    assert run2_id != run1_id
    assert run2_data["status"] == "queued"
    assert run2_data["attempt_number"] == 2
    assert run2_data["parent_run_id"] == run1_id
    assert run2_data["root_run_id"] == run1_id
    assert run2_data["task_id"] == task_id

    # Original run1 status and evidence are preserved
    run1_readback = research_client.get(f"/api/research-orchestrator/runs/{run1_id}").json()
    assert run1_readback["status"] == "canceled"
    assert run1_readback["attempt_number"] == 1


# =============================================================================
# 3. JobCommandAdapter Real Execution & Fail-Closed Receipts
# =============================================================================


def test_job_command_adapter_orchestrator_cancel_receipt() -> None:
    fake_fence = "2026-09-16T12:00:00Z"

    def mock_http_post(url: str, payload: Dict[str, Any], headers: Any) -> Any:
        assert "/runs/rrun-test-001/cancel" in url
        return 200, {
            "id": "rrun-test-001",
            "run_id": "rrun-test-001",
            "status": "canceled",
            "cancellation_fence": fake_fence,
            "completed_at": fake_fence,
        }

    port = create_research_commands_port(base_url="http://mock-research", http_post=mock_http_post)
    adapter = JobCommandAdapter(research_commands_port=port)

    receipt = adapter.execute(
        command_id="cmd-cancel-1",
        command_type="JobAction",
        params={
            "action_id": "cancel",
            "job_id": "job-orchestrator-rrun-test-001",
            "reason": "Operator abort",
        },
    )

    assert receipt["status"] == "canceled"
    assert receipt["entity_type"] == "Job"
    assert receipt["entity_id"] == "job-orchestrator-rrun-test-001"
    assert receipt["dispatch_path"] == "research_orchestrator.cancel_run"
    assert receipt["authoritative_readback"]["cancellation_fence"] == fake_fence
    assert receipt["cancellation_fence"] == fake_fence


def test_job_command_adapter_orchestrator_retry_receipt() -> None:
    def mock_http_post(url: str, payload: Dict[str, Any], headers: Any) -> Any:
        assert "/runs/rrun-test-001/retry" in url
        return 201, {
            "id": "rrun-test-002",
            "run_id": "rrun-test-002",
            "status": "queued",
            "attempt_number": 2,
            "parent_run_id": "rrun-test-001",
            "root_run_id": "rrun-test-001",
        }

    port = create_research_commands_port(base_url="http://mock-research", http_post=mock_http_post)
    adapter = JobCommandAdapter(research_commands_port=port)

    receipt = adapter.execute(
        command_id="cmd-retry-1",
        command_type="JobAction",
        params={
            "action_id": "retry",
            "job_id": "job-orchestrator-rrun-test-001",
            "actor_id": "operator-test",
        },
    )

    assert receipt["status"] == "queued"
    assert receipt["entity_type"] == "Job"
    assert receipt["dispatch_path"] == "research_orchestrator.retry_run"
    assert receipt["authoritative_readback"]["job_id"] == "job-orchestrator-rrun-test-002"
    assert receipt["authoritative_readback"]["attempt_number"] == 2
    assert receipt["authoritative_readback"]["parent_run_id"] == "rrun-test-001"
    assert receipt["previous_job_id"] == "job-orchestrator-rrun-test-001"
    assert receipt["new_job_id"] == "job-orchestrator-rrun-test-002"


def test_job_command_adapter_orchestrator_archive_fails_closed_409() -> None:
    adapter = JobCommandAdapter()
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            command_id="cmd-archive-1",
            command_type="JobAction",
            params={
                "action_id": "archive",
                "job_id": "job-orchestrator-rrun-test-001",
            },
        )
    assert excinfo.value.downstream_status == 409
    assert "retention schedule" in str(excinfo.value)


def test_job_command_adapter_orchestrator_promote_fails_closed_409() -> None:
    adapter = JobCommandAdapter()
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            command_id="cmd-promote-1",
            command_type="JobAction",
            params={
                "action_id": "promote",
                "job_id": "job-orchestrator-rrun-test-001",
            },
        )
    assert excinfo.value.downstream_status == 409
    assert "governance review" in str(excinfo.value).lower()


@pytest.mark.parametrize(
    ("job_id", "task_ref"),
    [
        ("job-worker-wjob-123", "GW-STOP-FENCE-001"),
        ("job-trainer-pvjob-123", "TS-CANCEL-001"),
        ("job-ingest-ingest-123", "SI-CANCEL-001"),
        ("job-policy-plj-123", "PL-CANCEL-001"),
    ],
)
def test_job_sources_fail_closed_citing_unresolved_tasks(job_id: str, task_ref: str) -> None:
    adapter = JobCommandAdapter()
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            command_id="cmd-fail-1",
            command_type="JobAction",
            params={"action_id": "cancel", "job_id": job_id},
        )
    assert excinfo.value.downstream_status == 503
    assert task_ref in str(excinfo.value)


def test_job_openclaw_permanently_read_only() -> None:
    adapter = JobCommandAdapter()
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            command_id="cmd-claw-1",
            command_type="JobAction",
            params={"action_id": "cancel", "job_id": "job-openclaw-wf-123"},
        )
    assert excinfo.value.downstream_status == 400
    assert "read-only" in str(excinfo.value)


# =============================================================================
# 4. ExperimentCommandAdapter Real Execution & Receipts
# =============================================================================


def test_experiment_adapter_cancel_receipt(isolated_write_owner: ResearchWriteOwner) -> None:
    created = isolated_write_owner.create_research_experiment(
        ticket_id="ticket-001",
        experiment_name="Alpha backtest",
        strategy_selector={"strategy_id": "s1"},
        parameter_set={},
        run_config={"stage": "backtest"},
        launch_context={},
    )
    exp_id = created["experiment_id"]

    adapter = ExperimentCommandAdapter(research_write_owner_factory=lambda: isolated_write_owner)
    receipt = adapter.execute(
        command_id="cmd-exp-cancel-1",
        command_type="ExperimentAction",
        params={"action_id": "cancel", "experiment_id": exp_id},
    )

    assert receipt["status"] == "canceled"
    assert receipt["entity_type"] == "Experiment"
    assert receipt["entity_id"] == exp_id
    assert receipt["dispatch_path"] == "research_write_owner.cancel_research_experiment"
    assert receipt["authoritative_readback"]["cancellation_fence"] is not None


def test_experiment_adapter_retry_receipt_and_lineage(
    isolated_write_owner: ResearchWriteOwner,
) -> None:
    created = isolated_write_owner.create_research_experiment(
        ticket_id="ticket-001",
        experiment_name="Beta backtest",
        strategy_selector={"strategy_id": "s2"},
        parameter_set={},
        run_config={"stage": "backtest"},
        launch_context={},
    )
    exp_id = created["experiment_id"]

    adapter = ExperimentCommandAdapter(research_write_owner_factory=lambda: isolated_write_owner)

    # Active experiment cannot be retried (409)
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            command_id="cmd-exp-retry-active",
            command_type="ExperimentAction",
            params={"action_id": "retry", "experiment_id": exp_id},
        )
    assert excinfo.value.downstream_status == 409
    assert excinfo.value.error_code == "EXPERIMENT_NOT_RETRYABLE"

    # Cancel first
    isolated_write_owner.cancel_research_experiment(exp_id)

    # Now retry
    receipt = adapter.execute(
        command_id="cmd-exp-retry-1",
        command_type="ExperimentAction",
        params={"action_id": "retry", "experiment_id": exp_id, "actor_id": "tester"},
    )
    assert receipt["status"] == "queued"
    assert receipt["entity_type"] == "Experiment"
    assert receipt["previous_experiment_id"] == exp_id
    new_id = receipt["new_experiment_id"]
    assert new_id != exp_id
    assert receipt["authoritative_readback"]["attempt_number"] == 2
    assert receipt["authoritative_readback"]["parent_experiment_id"] == exp_id


def test_experiment_adapter_archive_receipt(isolated_write_owner: ResearchWriteOwner) -> None:
    created = isolated_write_owner.create_research_experiment(
        ticket_id="ticket-001",
        experiment_name="Gamma backtest",
        strategy_selector={"strategy_id": "s3"},
        parameter_set={},
        run_config={"stage": "backtest"},
        launch_context={},
    )
    exp_id = created["experiment_id"]
    adapter = ExperimentCommandAdapter(research_write_owner_factory=lambda: isolated_write_owner)

    # Queued experiment cannot be archived (409)
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            command_id="cmd-exp-arc-queued",
            command_type="ExperimentAction",
            params={"action_id": "archive", "experiment_id": exp_id},
        )
    assert excinfo.value.downstream_status == 409

    # Cancel it so it reaches a terminal state
    isolated_write_owner.cancel_research_experiment(exp_id)

    # Archive succeeds
    receipt = adapter.execute(
        command_id="cmd-exp-arc-1",
        command_type="ExperimentAction",
        params={"action_id": "archive", "experiment_id": exp_id},
    )
    assert receipt["status"] == "archived"
    assert receipt["authoritative_readback"]["is_archived"] is True
    assert receipt["authoritative_readback"]["archived_at"] is not None

    # Verify listing excludes archived by default
    active_list = isolated_write_owner.list_research_experiments()
    assert not any(e["experiment_id"] == exp_id for e in active_list)

    # Verify listing includes archived when requested
    all_list = isolated_write_owner.list_research_experiments(include_archived=True)
    assert any(e["experiment_id"] == exp_id for e in all_list)


def test_experiment_adapter_invalidate_receipt(isolated_write_owner: ResearchWriteOwner) -> None:
    created = isolated_write_owner.create_research_experiment(
        ticket_id="ticket-001",
        experiment_name="Delta backtest",
        strategy_selector={"strategy_id": "s4"},
        parameter_set={},
        run_config={"stage": "backtest"},
        launch_context={},
    )
    exp_id = created["experiment_id"]
    adapter = ExperimentCommandAdapter(research_write_owner_factory=lambda: isolated_write_owner)

    receipt = adapter.execute(
        command_id="cmd-exp-inval-1",
        command_type="ExperimentAction",
        params={
            "action_id": "invalidate",
            "experiment_id": exp_id,
            "reason": "Bad data split",
        },
    )
    assert receipt["status"] == "invalidated"
    assert receipt["authoritative_readback"]["status"] == "invalidated"
    assert receipt["authoritative_readback"]["invalidated_reason"] == "Bad data split"


def test_experiment_adapter_promote_fails_closed_409(
    isolated_write_owner: ResearchWriteOwner,
) -> None:
    adapter = ExperimentCommandAdapter(research_write_owner_factory=lambda: isolated_write_owner)
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            command_id="cmd-exp-prom-1",
            command_type="ExperimentAction",
            params={"action_id": "promote", "experiment_id": "exp-test-001"},
        )
    assert excinfo.value.downstream_status == 409
    assert "GOV-PROMOTE-001" in str(excinfo.value)


# =============================================================================
# 5. Truthful allowedActions Affordances Projections
# =============================================================================


def test_job_allowed_actions_truthfulness() -> None:
    # Orchestrator runs: active -> canCancel=True, canRetry=False
    active_affordances = calculate_job_allowed_actions("research_orchestrator", "running")
    assert active_affordances["canCancel"] is True
    assert active_affordances["canRetry"] is False
    assert active_affordances["canArchive"] is False
    assert active_affordances["canPromote"] is False

    # Orchestrator runs: failed/canceled -> canCancel=False, canRetry=True
    failed_affordances = calculate_job_allowed_actions("research_orchestrator", "failed")
    assert failed_affordances["canCancel"] is False
    assert failed_affordances["canRetry"] is True

    canceled_affordances = calculate_job_allowed_actions("research_orchestrator", "canceled")
    assert canceled_affordances["canCancel"] is False
    assert canceled_affordances["canRetry"] is True

    # Orchestrator runs: completed -> canCancel=False, canRetry=False
    completed_affordances = calculate_job_allowed_actions("research_orchestrator", "completed")
    assert completed_affordances["canCancel"] is False
    assert completed_affordances["canRetry"] is False

    # All other sources fail closed with all False
    for other_src in (
        "research_worker_gateway",
        "training_session",
        "source_ingestion",
        "policy_learning",
        "openclaw_gateway_adapter",
    ):
        for test_st in ("running", "failed", "completed", "canceled"):
            aff = calculate_job_allowed_actions(other_src, test_st)
            assert not any(aff.values()), f"Expected all False for {other_src} in {test_st}"


def test_experiment_allowed_actions_truthfulness(isolated_write_owner: ResearchWriteOwner) -> None:
    # Create queued experiment
    exp = isolated_write_owner.create_research_experiment(
        ticket_id="ticket-001",
        experiment_name="Affordance check",
        strategy_selector={"strategy_id": "s5"},
        parameter_set={},
        run_config={"stage": "backtest"},
        launch_context={},
    )
    exp_id = exp["experiment_id"]

    # In queued state: canCancel=True, canInvalidate=False, canRetry=False, canArchive=False
    aff = exp["allowedActions"]
    assert aff["canCancel"] is True
    assert aff["canInvalidate"] is False
    assert aff["canRetry"] is False
    assert aff["canArchive"] is False

    # After cancellation: canCancel=False, canInvalidate=False, canRetry=True, canArchive=True
    canceled = isolated_write_owner.cancel_research_experiment(exp_id)
    assert canceled is not None
    aff_canc = canceled["allowedActions"]
    assert aff_canc["canCancel"] is False
    assert aff_canc["canInvalidate"] is False
    assert aff_canc["canRetry"] is True
    assert aff_canc["canArchive"] is True

    # After archive: canArchive=False
    archived = isolated_write_owner.archive_research_experiment(exp_id)
    assert archived is not None
    assert archived["allowedActions"]["canArchive"] is False

    # In completed state: canInvalidate=True, canArchive=True, canCancel=False, canRetry=False
    exp_completed = isolated_write_owner.create_research_experiment(
        ticket_id="ticket-001",
        experiment_name="Completed check",
        strategy_selector={"strategy_id": "s6"},
        parameter_set={},
        run_config={"stage": "backtest"},
        launch_context={},
    )
    c_id = exp_completed["experiment_id"]
    record = isolated_write_owner._experiments_store.get(c_id)
    assert record is not None
    record["status"] = "completed"
    isolated_write_owner._experiments_store.put(c_id, record)
    c_detail = isolated_write_owner.get_research_experiment(c_id)
    assert c_detail is not None
    assert c_detail["allowedActions"]["canInvalidate"] is True
    assert c_detail["allowedActions"]["canArchive"] is True
    assert c_detail["allowedActions"]["canCancel"] is False
    assert c_detail["allowedActions"]["canRetry"] is False
