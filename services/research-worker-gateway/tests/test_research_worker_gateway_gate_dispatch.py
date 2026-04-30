"""Gate-aware offline dispatch kernel tests for research-worker-gateway.

Tests that:
- Closed gate (default): offline dispatch mode is rejected; production modes remain rejected.
- Open gate (PANTHEON_OFFLINE_GATE_ENABLED=true): workers with declared entrypoints execute
  via subprocess and stdout/stderr/exit_code are persisted in job state.
- Open gate: paper/canary/live modes remain fail-closed regardless of gate state.
- Capabilities surface reflects gate state correctly.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_gateway_module(
    data_dir: str | None = None,
    max_active_jobs: str = "4",
    offline_gate: str = "false",
    repo_root: str | None = None,
):
    with mock.patch.dict(
        "os.environ",
        {
            "RESEARCH_WORKER_GATEWAY_DATA_DIR": data_dir or tempfile.mkdtemp(),
            "RESEARCH_WORKER_GATEWAY_MAX_ACTIVE_JOBS": max_active_jobs,
            "RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS": "false",
            "RESEARCH_ORCHESTRATOR_URL": "http://research-orchestrator-svc:8101",
            "PANTHEON_OFFLINE_GATE_ENABLED": offline_gate,
            **({"PANTHEON_REPO_ROOT": repo_root} if repo_root else {}),
        },
    ):
        sys.modules.pop("store", None)
        sys.path.insert(0, str(SERVICE_DIR))
        try:
            spec = importlib.util.spec_from_file_location("rwg_gate_test_main", SERVICE_DIR / "main.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["rwg_gate_test_main"] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.modules.pop("store", None)
            try:
                sys.path.remove(str(SERVICE_DIR))
            except ValueError:
                pass


def test_closed_gate_rejects_offline_dispatch_mode() -> None:
    """Without PANTHEON_OFFLINE_GATE_ENABLED, dispatch_mode=offline is rejected."""
    module = _load_gateway_module()
    client = TestClient(module.app)

    result = client.post(
        "/api/research-worker-gateway/jobs",
        json={
            "worker": "qlib",
            "requested_mode": "offline",
            "dispatch_mode": "offline",
            "objective": "Run qlib offline research.",
            "idempotency_key": "closed-gate-offline",
            "requested_at": "2026-04-30T05:00:00Z",
        },
    )
    assert result.status_code == 201
    payload = result.json()
    assert payload["status"] == "rejected"
    # With gate closed, offline dispatch_mode is not in SAFE_DISPATCH_MODES.
    assert payload["rejection"]["reason"] == "dispatch_mode_disabled"
    assert payload["production_activation"] == "disabled"


def test_closed_gate_production_modes_still_rejected() -> None:
    """Gate closed: production/paper/canary/live modes are rejected for all workers."""
    module = _load_gateway_module()
    client = TestClient(module.app)

    for worker, mode in (("qlib", "production"), ("trl", "paper"), ("finrl", "canary"), ("rllib", "live")):
        result = client.post(
            "/api/research-worker-gateway/jobs",
            json={
                "worker": worker,
                "requested_mode": mode,
                "dispatch_mode": "stub",
                "idempotency_key": f"closed-gate-{worker}-{mode}",
                "requested_at": "2026-04-30T05:01:00Z",
            },
        )
        assert result.status_code == 201
        assert result.json()["status"] == "rejected"
        assert result.json()["rejection"]["reason"] == "production_adapter_disabled"


def test_open_gate_offline_worker_executes_and_persists_output() -> None:
    """Open gate: offline worker dispatches via subprocess and stdout/stderr/exit_code are stored."""
    module = _load_gateway_module(offline_gate="true")
    client = TestClient(module.app)

    fake_execution = {"stdout": '{"result": "ok"}', "stderr": "", "exit_code": 0}
    with mock.patch.object(module, "_execute_worker", return_value=fake_execution):
        result = client.post(
            "/api/research-worker-gateway/jobs",
            json={
                "worker": "qlib",
                "requested_mode": "offline",
                "dispatch_mode": "offline",
                "objective": "Run qlib offline research with activation gate open.",
                "input_refs": [{"type": "dataset", "id": "ds-001"}],
                "parameters": {"QLIB_BACKEND": "stub"},
                "idempotency_key": "open-gate-qlib-offline",
                "requested_at": "2026-04-30T05:10:00Z",
            },
        )
    assert result.status_code == 201
    job = result.json()
    assert job["status"] == "completed"
    assert job["stdout"] == '{"result": "ok"}'
    assert job["stderr"] == ""
    assert job["exit_code"] == 0
    assert job["production_activation"] == "disabled"
    assert any(e["event_type"] == "job_dispatched" for e in job["events"])
    assert any(e["event_type"] == "job_completed" for e in job["events"])
    assert job["output_refs"][0]["output_type"] == "offline_execution_output"


def test_open_gate_worker_execution_requires_requested_mode_offline() -> None:
    """Open gate: dispatch_mode=offline alone is not enough to execute a worker."""
    module = _load_gateway_module(offline_gate="true")
    client = TestClient(module.app)

    with mock.patch.object(module, "_execute_worker") as execute_worker:
        result = client.post(
            "/api/research-worker-gateway/jobs",
            json={
                "worker": "qlib",
                "requested_mode": "stub",
                "dispatch_mode": "offline",
                "objective": "Invalid mixed-mode offline dispatch.",
                "idempotency_key": "open-gate-qlib-stub-offline",
                "requested_at": "2026-04-30T05:10:30Z",
            },
        )
    assert result.status_code == 201
    payload = result.json()
    assert payload["status"] == "rejected"
    assert payload["rejection"]["reason"] == "offline_mode_required"
    assert payload["stdout"] is None
    assert payload["exit_code"] is None
    execute_worker.assert_not_called()


def test_open_gate_failed_worker_records_failure() -> None:
    """Open gate: worker returning non-zero exit_code sets job status to failed."""
    module = _load_gateway_module(offline_gate="true")
    client = TestClient(module.app)

    fake_execution = {"stdout": "", "stderr": "dependency missing", "exit_code": 1}
    with mock.patch.object(module, "_execute_worker", return_value=fake_execution):
        result = client.post(
            "/api/research-worker-gateway/jobs",
            json={
                "worker": "finrl",
                "requested_mode": "offline",
                "dispatch_mode": "offline",
                "objective": "Run finrl with missing dep.",
                "idempotency_key": "open-gate-finrl-fail",
                "requested_at": "2026-04-30T05:11:00Z",
            },
        )
    assert result.status_code == 201
    job = result.json()
    assert job["status"] == "failed"
    assert job["exit_code"] == 1
    assert job["stderr"] == "dependency missing"
    assert any(e["event_type"] == "job_failed" for e in job["events"])


def test_open_gate_production_modes_remain_fail_closed() -> None:
    """Open gate: paper/canary/live modes are always rejected even when gate is open."""
    module = _load_gateway_module(offline_gate="true")
    client = TestClient(module.app)

    for worker, mode in (("qlib", "production"), ("trl", "paper"), ("finrl", "canary"), ("rllib", "live")):
        result = client.post(
            "/api/research-worker-gateway/jobs",
            json={
                "worker": worker,
                "requested_mode": mode,
                "dispatch_mode": "offline",
                "idempotency_key": f"open-gate-blocked-{worker}-{mode}",
                "requested_at": "2026-04-30T05:12:00Z",
            },
        )
        assert result.status_code == 201
        payload = result.json()
        assert payload["status"] == "rejected"
        assert payload["rejection"]["reason"] == "production_adapter_disabled", f"Expected production_adapter_disabled for {worker}/{mode}, got {payload['rejection']['reason']}"


def test_open_gate_worker_without_entrypoint_rejected() -> None:
    """Open gate: workers without a declared entrypoint (openclaw, wandb) are not offline-dispatchable."""
    module = _load_gateway_module(offline_gate="true")
    client = TestClient(module.app)

    for worker in ("openclaw", "wandb"):
        result = client.post(
            "/api/research-worker-gateway/jobs",
            json={
                "worker": worker,
                "requested_mode": "offline",
                "dispatch_mode": "offline",
                "idempotency_key": f"open-gate-no-entrypoint-{worker}",
                "requested_at": "2026-04-30T05:13:00Z",
            },
        )
        assert result.status_code == 201
        assert result.json()["status"] == "rejected"
        assert result.json()["rejection"]["reason"] == "offline_dispatch_not_available"


def test_open_gate_stub_dispatch_still_works() -> None:
    """Open gate: stub workers via stub dispatch mode continue to work normally."""
    module = _load_gateway_module(offline_gate="true")
    client = TestClient(module.app)

    result = client.post(
        "/api/research-worker-gateway/jobs",
        json={
            "worker": "stub",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "objective": "Stub dispatch still works with open gate.",
            "idempotency_key": "open-gate-stub-ok",
            "requested_at": "2026-04-30T05:14:00Z",
        },
    )
    assert result.status_code == 201
    assert result.json()["status"] == "queued"
    assert result.json()["production_activation"] == "disabled"


def test_capabilities_reflect_activation_ready_when_gate_open() -> None:
    """Capabilities surface shows activation_ready for offline workers when gate is open."""
    closed_module = _load_gateway_module(offline_gate="false")
    open_module = _load_gateway_module(offline_gate="true")

    closed_client = TestClient(closed_module.app)
    open_client = TestClient(open_module.app)

    closed_caps = closed_client.get("/api/research-worker-gateway/capabilities").json()
    open_caps = open_client.get("/api/research-worker-gateway/capabilities").json()

    assert closed_caps["offline_gate"] == "disabled"
    assert open_caps["offline_gate"] == "enabled"

    closed_workers = {e["worker"]: e for e in closed_caps["capabilities"]}
    open_workers = {e["worker"]: e for e in open_caps["capabilities"]}

    # Workers with explicit gate_state in registry show fail_closed when gate is closed
    # and activation_ready when gate is open.
    gated_workers = ("qlib", "finrl", "rllib", "ray_tune", "trl")
    for worker in gated_workers:
        assert closed_workers[worker]["gate_state"] == "fail_closed", f"{worker} should be fail_closed"
        assert open_workers[worker]["gate_state"] == "activation_ready", f"{worker} should be activation_ready"
        assert open_workers[worker]["offline_dispatch"] == "enabled", f"{worker} should have offline_dispatch enabled"

    # All offline workers (including those without explicit gate_state) get activation_ready when gate opens.
    for worker in ("vectorbt", "statsmodels", "quantlib"):
        assert open_workers[worker]["gate_state"] == "activation_ready", f"{worker} should be activation_ready"
        assert open_workers[worker]["offline_dispatch"] == "enabled"

    # Workers without entrypoints stay fail_closed even when gate is open.
    assert open_workers["openclaw"]["gate_state"] == "fail_closed"
    assert open_workers["wandb"]["gate_state"] == "fail_closed"


def test_open_gate_job_status_endpoint_includes_stdout_stderr() -> None:
    """Job status endpoint returns stdout/stderr/exit_code for offline-dispatched jobs."""
    module = _load_gateway_module(offline_gate="true")
    client = TestClient(module.app)

    fake_execution = {"stdout": "output line\n", "stderr": "warn\n", "exit_code": 0}
    with mock.patch.object(module, "_execute_worker", return_value=fake_execution):
        created = client.post(
            "/api/research-worker-gateway/jobs",
            json={
                "worker": "rllib",
                "requested_mode": "offline",
                "dispatch_mode": "offline",
                "objective": "RLlib offline sweep.",
                "idempotency_key": "open-gate-rllib-status",
                "requested_at": "2026-04-30T05:15:00Z",
            },
        )
    job_id = created.json()["job_id"]
    status = client.get(f"/api/research-worker-gateway/jobs/{job_id}/status")
    assert status.status_code == 200
    body = status.json()
    assert body["stdout"] == "output line\n"
    assert body["stderr"] == "warn\n"
    assert body["exit_code"] == 0
    assert body["status"] == "completed"
