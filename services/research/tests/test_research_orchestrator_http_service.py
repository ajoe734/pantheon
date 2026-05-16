from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from services.registry.storage import reset_store


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_service_module(
    max_active_runs: str = "8",
    production_adapters_enabled: str = "false",
    offline_gate: str = "false",
):
    with mock.patch.dict(
        "os.environ",
        {
            "RESEARCH_ORCHESTRATOR_DATA_DIR": tempfile.mkdtemp(),
            "RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS": max_active_runs,
            "RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS": production_adapters_enabled,
            "PANTHEON_OFFLINE_GATE_ENABLED": offline_gate,
            "RESEARCH_WORKER_GATEWAY_URL": "http://research-worker-gateway-svc:8103",
        },
    ):
        sys.modules.pop("store", None)
        sys.path.insert(0, str(SERVICE_DIR))
        try:
            spec = importlib.util.spec_from_file_location("research_orchestrator_test_main", SERVICE_DIR / "main.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["research_orchestrator_test_main"] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.modules.pop("store", None)
            try:
                sys.path.remove(str(SERVICE_DIR))
            except ValueError:
                pass


def test_research_orchestrator_lifecycle_handoff_is_idempotent() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    capabilities = client.get("/api/research-orchestrator/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["production_activation"] == "disabled"
    capability_map = {entry["adapter"]: entry for entry in capabilities.json()["capabilities"]}
    for adapter in ("openclaw", "qlib", "trl", "finrl", "rllib", "ray_tune", "wandb"):
        assert capability_map[adapter]["gate_state"] == "fail_closed"
        assert capability_map[adapter]["allowed_scope"] == "capability_metadata_read_only"

    created = client.post(
        "/api/research-orchestrator/tasks",
        json={
            "title": "Evaluate research signal",
            "objective": "Normalize a governed research note into a registry-ready draft.",
            "source_refs": [{"type": "search_result", "id": "search-1"}],
            "idempotency_key": "task-key-1",
            "created_at": "2026-04-28T20:00:00Z",
        },
    )
    assert created.status_code == 201
    task = created.json()
    replayed_task = client.post(
        "/api/research-orchestrator/tasks",
        json={
            "title": "Different title ignored by idempotency",
            "objective": "Same task.",
            "idempotency_key": "task-key-1",
            "created_at": "2026-04-28T20:01:00Z",
        },
    )
    assert replayed_task.status_code == 201
    assert replayed_task.json()["task_id"] == task["task_id"]
    assert replayed_task.json()["title"] == "Evaluate research signal"

    run_result = client.post(
        f"/api/research-orchestrator/tasks/{task['task_id']}/runs",
        json={
            "adapter": "stub",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "idempotency_key": "run-key-1",
            "requested_at": "2026-04-28T20:02:00Z",
        },
    )
    assert run_result.status_code == 201
    run = run_result.json()
    assert run["status"] == "queued"
    assert run["events"][0]["sequence_number"] == 1

    replayed_run = client.post(
        f"/api/research-orchestrator/tasks/{task['task_id']}/runs",
        json={
            "adapter": "stub",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "idempotency_key": "run-key-1",
            "requested_at": "2026-04-28T20:03:00Z",
        },
    )
    assert replayed_run.status_code == 201
    assert replayed_run.json()["run_id"] == run["run_id"]

    artifact_result = client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/artifacts",
        json={
            "artifact_type": "strategy_spec",
            "artifact_family": "research_signal",
            "title": "Draft research strategy spec",
            "storage_ref": "memory://research/spec-1",
            "checksum": "sha256:test",
            "idempotency_key": "artifact-key-1",
            "created_at": "2026-04-28T20:04:00Z",
        },
    )
    assert artifact_result.status_code == 201
    artifact = artifact_result.json()
    assert artifact["artifact_state"] == "draft"
    assert artifact["deployment_stage"] == "none"
    assert artifact["governance"]["direct_live_influence"] is False
    assert artifact["registry_projection"]["lineage"] == [{"type": "research_run", "id": run["run_id"]}]

    replayed_artifact = client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/artifacts",
        json={
            "artifact_type": "strategy_spec",
            "title": "Ignored by idempotency",
            "storage_ref": "memory://research/spec-2",
            "idempotency_key": "artifact-key-1",
        },
    )
    assert replayed_artifact.status_code == 201
    assert replayed_artifact.json()["artifact_id"] == artifact["artifact_id"]

    proposal_result = client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/proposals",
        json={
            "proposal_type": "registry_candidate",
            "target_ref": {"artifact_id": artifact["artifact_id"]},
            "rationale": "Ready for registry candidate review.",
            "evidence_refs": [{"type": "artifact", "id": artifact["artifact_id"]}],
            "idempotency_key": "proposal-key-1",
            "proposed_at": "2026-04-28T20:05:00Z",
        },
    )
    assert proposal_result.status_code == 201
    proposal = proposal_result.json()
    assert proposal["status"] == "proposed"
    assert proposal["production_activation"] == "disabled"

    completed = client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/complete",
        json={"completed_at": "2026-04-28T20:06:00Z"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"

    status = client.get(f"/api/research-orchestrator/runs/{run['run_id']}/status")
    assert status.status_code == 200
    payload = status.json()
    assert payload["artifact_refs"] == [{"artifact_id": artifact["artifact_id"], "artifact_type": "strategy_spec"}]
    assert payload["proposal_refs"] == [{"proposal_id": proposal["proposal_id"], "proposal_type": "registry_candidate"}]


def test_research_orchestrator_writeback_registers_completed_run_artifact() -> None:
    reset_store()
    module = _load_service_module()
    client = TestClient(module.app)
    task = client.post(
        "/api/research-orchestrator/tasks",
        json={
            "title": "EXP-005 writeback",
            "objective": "Register a completed experiment artifact.",
            "created_at": "2026-05-16T09:10:00Z",
        },
    ).json()
    run = client.post(
        f"/api/research-orchestrator/tasks/{task['task_id']}/runs",
        json={
            "adapter": "stub",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "input_refs": [{"type": "dataset", "id": "dataset-exp005-v1"}],
            "parameters": {
                "strategy_id": "strat-exp005-alpha",
                "strategy_spec_version": "1.2.0",
                "dataset_version_id": "dataset-exp005-v1",
                "code_version": "git:exp005",
                "version": "1.2.1",
            },
            "requested_at": "2026-05-16T09:11:00Z",
        },
    ).json()
    artifact = client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/artifacts",
        json={
            "artifact_type": "model_artifact",
            "artifact_family": "experiment_candidate",
            "title": "EXP-005 candidate model",
            "storage_ref": "object://experiments/exp005/model.pkl",
            "checksum": "sha256:exp005",
            "registry_hints": {
                "artifact_type": "model_artifact",
                "artifact_state": "candidate",
                "version": "1.2.1",
                "source_strategy_spec_id": "reg-strategy-spec-exp005",
                "source_dataset_refs": ["dataset-exp005-v1"],
            },
            "idempotency_key": "artifact-exp005",
            "created_at": "2026-05-16T09:12:00Z",
        },
    ).json()
    completed = client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/complete",
        json={"completed_at": "2026-05-16T09:13:00Z"},
    )
    assert completed.status_code == 200

    result = client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/registry-writeback",
        json={
            "artifact_id": artifact["artifact_id"],
            "registry_id": "reg-exp005-model",
            "actor_id": "exp005-test",
            "idempotency_key": "writeback-exp005",
            "created_at": "2026-05-16T09:14:00Z",
        },
    )

    assert result.status_code == 201, result.text
    payload = result.json()
    assert payload["registry_id"] == "reg-exp005-model"
    assert payload["artifact_state"] == "candidate"
    assert payload["deployment_stage"] == "none"
    assert payload["producer_run_id"] == run["run_id"]
    assert payload["lineage"]["source_run_ids"] == [run["run_id"]]
    assert payload["lineage"]["source_strategy_spec_id"] == "reg-strategy-spec-exp005"
    assert payload["lineage"]["source_dataset_refs"] == ["dataset-exp005-v1"]
    assert payload["registry_view"]["entry"]["metadata"]["registry_write_authority"] == "research_orchestrator_controlled_writeback"

    replay = client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/registry-writeback",
        json={
            "artifact_id": artifact["artifact_id"],
            "registry_id": "ignored-by-idempotency",
            "actor_id": "exp005-test",
            "idempotency_key": "writeback-exp005",
        },
    )
    assert replay.status_code == 201
    assert replay.json()["registry_id"] == "reg-exp005-model"

    status = client.get(f"/api/research-orchestrator/runs/{run['run_id']}/status")
    assert status.status_code == 200
    assert status.json()["registry_writebacks"][0]["registry_id"] == "reg-exp005-model"
    reset_store()


def test_research_orchestrator_writeback_requires_completed_run() -> None:
    reset_store()
    module = _load_service_module()
    client = TestClient(module.app)
    task = client.post(
        "/api/research-orchestrator/tasks",
        json={
            "title": "EXP-005 incomplete writeback",
            "objective": "Reject writeback before completion.",
            "created_at": "2026-05-16T09:20:00Z",
        },
    ).json()
    run = client.post(
        f"/api/research-orchestrator/tasks/{task['task_id']}/runs",
        json={
            "adapter": "stub",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "parameters": {
                "strategy_id": "strat-exp005-alpha",
                "strategy_spec_version": "1.2.0",
                "dataset_version_id": "dataset-exp005-v1",
                "code_version": "git:exp005",
                "version": "1.2.1",
            },
            "requested_at": "2026-05-16T09:21:00Z",
        },
    ).json()
    artifact = client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/artifacts",
        json={
            "artifact_type": "model_artifact",
            "title": "Incomplete run artifact",
            "storage_ref": "object://experiments/exp005/incomplete.pkl",
            "checksum": "sha256:incomplete",
            "registry_hints": {"artifact_type": "model_artifact", "version": "1.2.1"},
        },
    ).json()

    result = client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/registry-writeback",
        json={"artifact_id": artifact["artifact_id"]},
    )

    assert result.status_code == 409
    reset_store()


def test_research_orchestrator_blocks_production_adapters_and_bounds_dispatch() -> None:
    module = _load_service_module(max_active_runs="1")
    client = TestClient(module.app)
    task = client.post(
        "/api/research-orchestrator/tasks",
        json={
            "title": "Bounded dispatch",
            "objective": "Verify queue bound.",
            "created_at": "2026-04-28T21:00:00Z",
        },
    ).json()

    first_run = client.post(
        f"/api/research-orchestrator/tasks/{task['task_id']}/runs",
        json={
            "adapter": "stub",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "requested_at": "2026-04-28T21:01:00Z",
        },
    )
    assert first_run.status_code == 201
    assert first_run.json()["status"] == "queued"

    bounded = client.post(
        f"/api/research-orchestrator/tasks/{task['task_id']}/runs",
        json={
            "adapter": "stub",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "requested_at": "2026-04-28T21:02:00Z",
        },
    )
    assert bounded.status_code == 429

    for index, (adapter, mode) in enumerate(
        (
            ("openclaw", "stub"),
            ("qlib", "production"),
            ("trl", "paper"),
            ("rllib", "canary"),
            ("ray_tune", "stub"),
            ("wandb", "live"),
        ),
        start=3,
    ):
        rejected = client.post(
            f"/api/research-orchestrator/tasks/{task['task_id']}/runs",
            json={
                "adapter": adapter,
                "requested_mode": mode,
                "dispatch_mode": "stub",
                "requested_at": f"2026-04-28T21:{index:02d}:00Z",
            },
        )
        assert rejected.status_code == 201
        payload = rejected.json()
        assert payload["status"] == "rejected"
        assert payload["rejection"]["reason"] == "production_adapter_disabled"
        assert payload["production_activation"] == "disabled"


def test_research_orchestrator_open_gate_routes_offline_adapter_to_gateway() -> None:
    module = _load_service_module(offline_gate="true")
    client = TestClient(module.app)
    task = client.post(
        "/api/research-orchestrator/tasks",
        json={
            "title": "Offline qlib route",
            "objective": "Run qlib offline research through the gateway.",
            "created_at": "2026-04-30T06:30:00Z",
        },
    ).json()

    captured = {}

    def fake_route(adapter, task_id, run_id, objective, input_refs, parameters, actor_id, timestamp):
        captured.update(
            {
                "adapter": adapter,
                "task_id": task_id,
                "run_id": run_id,
                "objective": objective,
                "input_refs": input_refs,
                "parameters": parameters,
                "actor_id": actor_id,
                "timestamp": timestamp,
            }
        )
        return {"job_id": "wjob-20260430-010", "status": "completed"}

    with mock.patch.object(module, "_route_to_gateway", side_effect=fake_route):
        result = client.post(
            f"/api/research-orchestrator/tasks/{task['task_id']}/runs",
            json={
                "adapter": "qlib",
                "requested_mode": "offline",
                "dispatch_mode": "offline",
                "input_refs": [{"type": "dataset", "id": "ds-001"}],
                "parameters": {"QLIB_BACKEND": "stub"},
                "actor_id": "tester",
                "requested_at": "2026-04-30T06:31:00Z",
            },
        )

    assert result.status_code == 201
    run = result.json()
    assert run["status"] == "dispatched"
    assert run["gateway_ref"] == {"gateway_job_id": "wjob-20260430-010", "gateway": "research-worker-gateway"}
    assert run["production_activation"] == "disabled"
    assert captured["objective"] == "Run qlib offline research through the gateway."
    assert captured["input_refs"] == [{"type": "dataset", "id": "ds-001"}]
    assert captured["parameters"] == {"QLIB_BACKEND": "stub"}

    status = client.get(f"/api/research-orchestrator/runs/{run['run_id']}/status")
    assert status.status_code == 200
    assert status.json()["gateway_ref"]["gateway_job_id"] == "wjob-20260430-010"


def test_research_orchestrator_open_gate_requires_explicit_offline_modes() -> None:
    module = _load_service_module(offline_gate="true")
    client = TestClient(module.app)
    task = client.post(
        "/api/research-orchestrator/tasks",
        json={
            "title": "Offline mode guard",
            "objective": "Verify open gate remains offline-only.",
            "created_at": "2026-04-30T06:40:00Z",
        },
    ).json()

    cases = [
        {"requested_mode": "offline", "dispatch_mode": "not_a_mode"},
        {"requested_mode": "stub", "dispatch_mode": "offline"},
    ]
    with mock.patch.object(module, "_route_to_gateway") as route:
        for index, body in enumerate(cases, start=1):
            result = client.post(
                f"/api/research-orchestrator/tasks/{task['task_id']}/runs",
                json={
                    "adapter": "qlib",
                    **body,
                    "requested_at": f"2026-04-30T06:4{index}:00Z",
                },
            )
            assert result.status_code == 201
            payload = result.json()
            assert payload["status"] == "rejected"
            assert payload["rejection"]["reason"] == "offline_mode_required"
            assert "gateway_ref" not in payload
        route.assert_not_called()


def test_research_orchestrator_rejects_write_paths_and_unknown_adapters() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    task = client.post(
        "/api/research-orchestrator/tasks",
        json={
            "title": "Fail closed policy",
            "objective": "Verify write-denial policy.",
            "created_at": "2026-04-29T01:00:00Z",
        },
    ).json()

    cases = [
        ({"adapter": "stub", "parameters": {"direct_registry_write": True}}, "registry_write_disabled"),
        ({"adapter": "stub", "parameters": {"governance_stage": "approved"}}, "governance_write_disabled"),
        ({"adapter": "mystery", "requested_mode": "stub", "dispatch_mode": "stub"}, "unknown_adapter"),
    ]
    for index, (body, reason) in enumerate(cases, start=1):
        body.setdefault("requested_mode", "stub")
        body.setdefault("dispatch_mode", "stub")
        body["requested_at"] = f"2026-04-29T01:0{index}:00Z"
        result = client.post(f"/api/research-orchestrator/tasks/{task['task_id']}/runs", json=body)
        assert result.status_code == 201
        payload = result.json()
        assert payload["status"] == "rejected"
        assert payload["rejection"]["reason"] == reason


def test_research_orchestrator_dormant_dispatch_stays_fail_closed_when_legacy_env_is_enabled() -> None:
    module = _load_service_module(production_adapters_enabled="true")
    client = TestClient(module.app)
    task = client.post(
        "/api/research-orchestrator/tasks",
        json={
            "title": "Legacy env fail closed",
            "objective": "Verify legacy production env does not activate dormant dispatch.",
            "created_at": "2026-04-29T01:30:00Z",
        },
    ).json()

    capabilities = client.get("/api/research-orchestrator/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["production_activation"] == "disabled"

    result = client.post(
        f"/api/research-orchestrator/tasks/{task['task_id']}/runs",
        json={
            "adapter": "qlib",
            "requested_mode": "stub",
            "dispatch_mode": "stub",
            "requested_at": "2026-04-29T01:31:00Z",
        },
    )
    assert result.status_code == 201
    payload = result.json()
    assert payload["status"] == "rejected"
    assert payload["rejection"]["reason"] == "production_adapter_disabled"
    assert payload["production_activation"] == "disabled"
