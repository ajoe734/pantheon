from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_service_module(
    data_dir: str | None = None,
    max_active_jobs: str = "4",
    production_adapters_enabled: str = "false",
):
    with mock.patch.dict(
        "os.environ",
        {
            "RESEARCH_WORKER_GATEWAY_DATA_DIR": data_dir or tempfile.mkdtemp(),
            "RESEARCH_WORKER_GATEWAY_MAX_ACTIVE_JOBS": max_active_jobs,
            "RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS": production_adapters_enabled,
            "RESEARCH_ORCHESTRATOR_URL": "http://research-orchestrator-svc:8101",
        },
    ):
        sys.modules.pop("store", None)
        sys.path.insert(0, str(SERVICE_DIR))
        try:
            spec = importlib.util.spec_from_file_location("research_worker_gateway_test_main", SERVICE_DIR / "main.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["research_worker_gateway_test_main"] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.modules.pop("store", None)
            try:
                sys.path.remove(str(SERVICE_DIR))
            except ValueError:
                pass


def test_gateway_lifecycle_status_cancel_and_idempotency() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "research-worker-gateway"
    assert health.json()["production_adapters_enabled"] is False

    capabilities = client.get("/api/research-worker-gateway/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["production_activation"] == "disabled"
    assert any(item["worker"] == "stub" and item["status"] == "available" for item in capabilities.json()["capabilities"])

    created = client.post(
        "/api/research-worker-gateway/jobs",
        json={
            "worker": "stub",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "objective": "Validate gateway dispatch.",
            "task_id": "rtask-1",
            "run_id": "rrun-1",
            "input_refs": [{"type": "research_run", "id": "rrun-1"}],
            "idempotency_key": "worker-job-key-1",
            "requested_at": "2026-04-28T22:00:00Z",
        },
    )
    assert created.status_code == 201
    job = created.json()
    assert job["status"] == "queued"
    assert job["events"][0]["sequence_number"] == 1
    assert job["output_refs"] == [{"output_id": "wgout-20260428-001", "output_type": "dispatch_stub"}]

    replayed = client.post(
        "/api/research-worker-gateway/jobs",
        json={
            "worker": "manual",
            "requested_mode": "manual",
            "dispatch_mode": "manual",
            "objective": "Ignored by idempotency.",
            "idempotency_key": "worker-job-key-1",
            "requested_at": "2026-04-28T22:01:00Z",
        },
    )
    assert replayed.status_code == 201
    assert replayed.json()["job_id"] == job["job_id"]
    assert replayed.json()["worker"] == "stub"

    status = client.get(f"/api/research-worker-gateway/jobs/{job['job_id']}/status")
    assert status.status_code == 200
    assert status.json()["production_activation"] == "disabled"
    assert status.json()["events"][0]["event_type"] == "job_queued"

    canceled = client.post(
        f"/api/research-worker-gateway/jobs/{job['job_id']}/cancel",
        json={"reason": "operator_test_cancel", "canceled_at": "2026-04-28T22:02:00Z"},
    )
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"
    assert canceled.json()["events"][-1]["sequence_number"] == 2

    listed = client.get("/api/research-worker-gateway/jobs", params={"task_id": "rtask-1", "run_id": "rrun-1"})
    assert listed.status_code == 200
    assert [item["job_id"] for item in listed.json()] == [job["job_id"]]

    missing = client.get("/api/research-worker-gateway/jobs/missing-job")
    assert missing.status_code == 404


def test_gateway_status_replays_from_durable_store_after_reload() -> None:
    data_dir = tempfile.mkdtemp()
    first_module = _load_service_module(data_dir=data_dir)
    first_client = TestClient(first_module.app)
    job = first_client.post(
        "/api/research-worker-gateway/jobs",
        json={
            "worker": "handoff_only",
            "requested_mode": "stub",
            "dispatch_mode": "handoff_only",
            "idempotency_key": "reload-key",
            "requested_at": "2026-04-28T22:10:00Z",
        },
    ).json()

    second_module = _load_service_module(data_dir=data_dir)
    second_client = TestClient(second_module.app)
    replay = second_client.get(f"/api/research-worker-gateway/jobs/{job['job_id']}/status")
    assert replay.status_code == 200
    assert replay.json()["job_id"] == job["job_id"]
    assert replay.json()["events"][0]["sequence_number"] == 1
