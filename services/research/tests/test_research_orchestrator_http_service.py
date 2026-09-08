from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from services.registry.storage import reset_store


SERVICE_DIR = Path(__file__).resolve().parents[1]
VALID_SHA256 = "sha256:" + ("a" * 64)


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
            "REGISTRY_STORE_BACKEND": "memory",
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
    assert artifact["producer_mode"] == "stub"
    assert artifact["artifact_origin"] == "dev_stub"
    assert artifact["checksum_status"] == "invalid"
    assert artifact["storage_status"] == "resolvable"
    assert artifact["evidence_eligible"] is False
    assert "producer_mode_not_evidence_grade" in artifact["evidence_ineligibility_reasons"]
    assert "checksum_invalid" in artifact["evidence_ineligibility_reasons"]
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


def test_research_orchestrator_writeback_registers_completed_run_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGISTRY_STORE_BACKEND", "memory")
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
            "checksum": VALID_SHA256,
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

    candidate_result = client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/registry-writeback",
        json={
            "artifact_id": artifact["artifact_id"],
            "registry_id": "reg-exp005-candidate-blocked",
            "actor_id": "exp005-test",
            "idempotency_key": "writeback-exp005-candidate",
            "created_at": "2026-05-16T09:13:30Z",
        },
    )
    assert candidate_result.status_code == 400
    candidate_detail = candidate_result.json()["detail"]
    assert candidate_detail["reason"] == "registry_writeback_not_eligible"
    assert "producer_mode_not_candidate_grade" in candidate_detail["reasons"]
    assert "missing_source_evidence_refs" in candidate_detail["reasons"]
    assert "artifact_not_evidence_eligible" in candidate_detail["reasons"]

    result = client.post(
        f"/api/research-orchestrator/runs/{run['run_id']}/registry-writeback",
        json={
            "artifact_id": artifact["artifact_id"],
            "registry_id": "reg-exp005-model",
            "requested_artifact_state": "draft",
            "actor_id": "exp005-test",
            "idempotency_key": "writeback-exp005",
            "created_at": "2026-05-16T09:14:00Z",
        },
    )

    assert result.status_code == 201, result.text
    payload = result.json()
    assert payload["registry_id"] == "reg-exp005-model"
    assert payload["artifact_state"] == "draft"
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
            "requested_artifact_state": "draft",
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


def _make_sample_vectorbt_dataset(strategy_id: str = "strat-vbt-01") -> dict:
    from datetime import date, timedelta
    start = date(2026, 1, 1)
    records = []
    for inst, base in (("AAA", 100.0), ("BBB", 50.0)):
        for i in range(35):
            d = (start + timedelta(days=i)).isoformat()
            p = base + i * 0.5
            records.append({
                "instrument": inst,
                "date": d,
                "open": p,
                "high": p + 1.0,
                "low": p - 0.5,
                "close": p + 0.2,
                "volume": 1000.0,
            })
    return {
        "dataset_id": f"dataset:{strategy_id}",
        "strategy_id": strategy_id,
        "source_dataset_refs": [f"dataset:seed:{strategy_id}"],
        "data_frequency": "daily",
        "records": records,
    }


def _make_sample_statsmodels_dataset() -> dict:
    return {
        "price_series": {"asset_1": [100.0 + i for i in range(20)], "asset_2": [50.0 + i * 0.5 for i in range(20)]},
        "factor_series": {"factor_1": [1.0 + (i % 3) for i in range(20)]},
        "metadata": {"governed": True},
    }


def _make_sample_quantlib_dataset() -> dict:
    return {
        "dataset_id": "ds-quantlib-agora",
        "source_dataset_refs": ["ref-1"],
        "valuation_date": "2026-09-08",
        "option_specs": [
            {
                "option_id": "opt-1",
                "style": "european",
                "option_type": "call",
                "spot": 100.0,
                "strike": 100.0,
                "volatility": 0.2,
                "risk_free_rate": 0.05,
                "dividend_yield": 0.0,
                "maturity_days": 30,
            }
        ],
        "bond_specs": [
            {
                "instrument_id": "bond-1",
                "face_value": 1000.0,
                "coupon_rate": 0.05,
                "market_rate": 0.05,
                "maturity_years": 5,
            }
        ],
        "metadata": {"governed": True},
    }


def test_execute_research_stage_missing_inputs_fail_closed() -> None:
    """POST /stages/{stage_type}/execute must fail closed with 400 when required fields or inputs are missing."""
    module = _load_service_module()
    client = TestClient(module.app)

    # 1. Empty body
    res1 = client.post("/stages/prototype_backtest/execute", json={})
    assert res1.status_code == 400
    assert "Missing required execution request body" in res1.json()["detail"]

    # 2. Missing plan
    res2 = client.post(
        "/stages/prototype_backtest/execute",
        json={"stage": {"stage_id": "s1", "stage_type": "prototype_backtest"}},
    )
    assert res2.status_code == 400
    assert "Missing or invalid required execution field: 'plan'" in res2.json()["detail"]

    # 3. Missing run_id
    res3 = client.post(
        "/stages/prototype_backtest/execute",
        json={
            "stage": {"stage_id": "s1", "stage_type": "prototype_backtest"},
            "plan": {"plan_id": "p1"},
        },
    )
    assert res3.status_code == 400
    assert "Missing required execution field: 'run_id'" in res3.json()["detail"]

    # 4. Missing correlation_id
    res4 = client.post(
        "/stages/prototype_backtest/execute",
        json={
            "stage": {"stage_id": "s1", "stage_type": "prototype_backtest"},
            "plan": {"plan_id": "p1"},
            "run_id": "run-test-1",
        },
    )
    assert res4.status_code == 400
    assert "Missing required execution field: 'correlation_id'" in res4.json()["detail"]

    # 5. Non-allowlisted stage type
    res5 = client.post(
        "/stages/unauthorized_stage/execute",
        json={
            "stage": {"stage_id": "s1", "stage_type": "unauthorized_stage"},
            "plan": {"plan_id": "p1"},
            "run_id": "run-test-1",
            "correlation_id": "corr-test-1",
        },
    )
    assert res5.status_code == 400
    assert "Unknown or non-allowlisted research stage" in res5.json()["detail"]

    # 6. Arbitrary custom_* or stage_* non-allowlisted stage types must fail closed
    res6 = client.post(
        "/stages/custom_no_execution_owner/execute",
        json={
            "stage": {"stage_id": "s1", "stage_type": "custom_no_execution_owner"},
            "plan": {"plan_id": "p1"},
            "run_id": "run-test-1",
            "correlation_id": "corr-test-1",
        },
    )
    assert res6.status_code == 400
    assert "Unknown or non-allowlisted research stage" in res6.json()["detail"]

    # 7. Missing dataset for allowlisted stage
    res7 = client.post(
        "/stages/prototype_backtest/execute",
        json={
            "stage": {"stage_id": "s1", "stage_type": "prototype_backtest"},
            "plan": {"plan_id": "p1", "strategy_id": "strat-1"},
            "run_id": "run-test-no-ds",
            "correlation_id": "corr-test-no-ds",
        },
    )
    assert res7.status_code == 400
    assert "Missing required governed dataset or input" in res7.json()["detail"]

    # 8. Unimplemented allowlisted stage fails closed with 503
    res8 = client.post(
        "/stages/alpha_training/execute",
        json={
            "stage": {"stage_id": "s1", "stage_type": "alpha_training"},
            "plan": {"plan_id": "p1", "strategy_id": "strat-1"},
            "run_id": "run-test-alpha",
            "correlation_id": "corr-test-alpha",
        },
    )
    assert res8.status_code == 503
    assert "absent or not configured" in res8.json()["detail"]

    # 9. Malformed dataset fails closed with 400
    res9 = client.post(
        "/stages/prototype_backtest/execute",
        json={
            "stage": {
                "stage_id": "s1",
                "stage_type": "prototype_backtest",
                "dataset": {"dataset_id": "ds1", "records": "not-a-list"},
            },
            "plan": {"plan_id": "p1", "strategy_id": "strat-1"},
            "run_id": "run-test-malformed",
            "correlation_id": "corr-test-malformed",
        },
    )
    assert res9.status_code == 400
    assert "Governed input validation error" in res9.json()["detail"]


def test_execute_research_stage_backend_unavailable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /stages/{stage_type}/execute must fail closed with 503 when backend execution owner is marked unavailable."""
    module = _load_service_module()
    client = TestClient(module.app)

    monkeypatch.setenv("AGORA_RESEARCH_PROTOTYPE_BACKTEST_UNAVAILABLE", "1")
    res = client.post(
        "/stages/prototype_backtest/execute",
        json={
            "stage": {"stage_id": "s1", "stage_type": "prototype_backtest", "dataset": _make_sample_vectorbt_dataset()},
            "plan": {"plan_id": "p1", "strategy_id": "strat-1"},
            "run_id": "run-test-unavail",
            "correlation_id": "corr-test-unavail",
        },
    )
    assert res.status_code == 503
    assert "currently unavailable" in res.json()["detail"]


def test_execute_research_stage_simulation_execution_preserves_provenance() -> None:
    """POST /stages/{stage_type}/execute with stub backend preserves simulation provenance and receipt mode."""
    module = _load_service_module()
    client = TestClient(module.app)
    run_id = "run-vbt-sim-001"
    corr_id = "corr-vbt-sim-001"
    dataset = _make_sample_vectorbt_dataset("strat-vbt-sim")

    res = client.post(
        "/stages/prototype_backtest/execute",
        json={
            "stage": {"stage_id": "s1", "stage_type": "prototype_backtest", "dataset": dataset},
            "plan": {"plan_id": "p1", "strategy_id": "strat-vbt-sim"},
            "run_id": run_id,
            "correlation_id": corr_id,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "succeeded"
    assert data["outcome"] == "succeeded"
    assert data["provenance"] == "simulation"
    assert data["backend_reference"] == f"research-orchestrator://stages/prototype_backtest/{run_id}"

    # Verify genuine metrics from vectorbt stub runner with simulation provenance
    metric_names = {m["metric"] for m in data["metrics"]}
    assert "mean_total_return" in metric_names
    assert "mean_sharpe_ratio" in metric_names
    assert "mean_max_drawdown" in metric_names
    assert "total_trades" in metric_names
    for m in data["metrics"]:
        assert m["provenance"] == "simulation"

    # Verify receipt integrity and mode
    receipt = data["receipt"]
    assert receipt["run_id"] == run_id
    assert receipt["correlation_id"] == corr_id
    assert receipt["mode"] == "simulation"
    assert receipt["spec_version"] == "1.0"
    assert receipt["artifact_digest"] == data["artifact_digest"]
    assert receipt["completed_at"] is not None

    # Verify artifact in store
    artifacts = module.store.list_artifacts()
    matching = [a for a in artifacts if a.get("checksum") == data["artifact_digest"]]
    assert len(matching) == 1
    art = matching[0]
    assert art["run_id"] == run_id
    assert art["stage_type"] == "prototype_backtest"
    assert art["provenance"] == "simulation"


def test_execute_research_stage_real_execution_with_authentic_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /stages/{stage_type}/execute with real execution owner produces genuine real metrics and receipt."""
    from services.research.vectorbt.adapter.vectorbt_adapter import BacktestRunResult

    module = _load_service_module()
    client = TestClient(module.app)
    run_id = "run-vbt-real-001"
    corr_id = "corr-vbt-real-001"
    dataset = _make_sample_vectorbt_dataset("strat-vbt-real")

    monkeypatch.setenv("PANTHEON_VECTORBT_BACKEND", "real")
    real_run_result = BacktestRunResult(
        backend="vectorbt_portfolio",
        run_id="vbt-real-exec-001",
        per_instrument_metrics={
            "AAA": {"total_return": 0.15, "sharpe_ratio": 1.6, "max_drawdown": 0.04, "trade_count": 8, "num_bars": 35, "final_portfolio_value": 115000.0},
            "BBB": {"total_return": 0.10, "sharpe_ratio": 1.2, "max_drawdown": 0.03, "trade_count": 6, "num_bars": 35, "final_portfolio_value": 110000.0},
        },
        aggregate_metrics={
            "num_instruments": 2,
            "mean_total_return": 0.125,
            "mean_sharpe_ratio": 1.4,
            "mean_max_drawdown": 0.035,
            "total_trades": 14,
        },
        notes=("genuine real execution test",),
    )
    monkeypatch.setattr(
        "services.research.vectorbt.adapter.vectorbt_adapter.VectorbtBackend.run",
        lambda self, prepared, config: real_run_result,
    )

    res = client.post(
        "/stages/prototype_backtest/execute",
        json={
            "stage": {"stage_id": "s1", "stage_type": "prototype_backtest", "dataset": dataset},
            "plan": {"plan_id": "p1", "strategy_id": "strat-vbt-real"},
            "run_id": run_id,
            "correlation_id": corr_id,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "succeeded"
    assert data["outcome"] == "succeeded"
    assert data["provenance"] == "real"
    assert data["backend_reference"] == f"research-orchestrator://stages/prototype_backtest/{run_id}"

    # Verify metrics have provenance='real'
    for m in data["metrics"]:
        assert m["provenance"] == "real"

    receipt = data["receipt"]
    assert receipt["run_id"] == run_id
    assert receipt["mode"] == "real"
    assert receipt["artifact_digest"] == data["artifact_digest"]

    # Verify artifact in store has provenance='real'
    matching = [a for a in module.store.list_artifacts() if a.get("checksum") == data["artifact_digest"]]
    assert len(matching) == 1
    assert matching[0]["provenance"] == "real"


def test_execute_research_stage_econometric_validation_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /stages/econometric_validation/execute respects backend mode for simulation vs real provenance."""
    module = _load_service_module()
    client = TestClient(module.app)
    dataset = _make_sample_statsmodels_dataset()

    # 1. Default stub mode -> simulation provenance
    res_stub = client.post(
        "/stages/econometric_validation/execute",
        json={
            "stage": {"stage_id": "s-ev-1", "stage_type": "econometric_validation", "dataset": dataset},
            "plan": {"plan_id": "p-ev-1", "strategy_id": "strat-ev"},
            "run_id": "run-ev-sim",
            "correlation_id": "corr-ev-sim",
        },
    )
    assert res_stub.status_code == 200, res_stub.text
    data_stub = res_stub.json()
    assert data_stub["provenance"] == "simulation"
    assert data_stub["receipt"]["mode"] == "simulation"
    for m in data_stub["metrics"]:
        assert m["provenance"] == "simulation"

    # 2. Real mode -> real provenance
    monkeypatch.setenv("PANTHEON_STATSMODELS_BACKEND", "real")
    monkeypatch.setattr(
        "services.research.statsmodels.adapter.statsmodels_adapter.StatsmodelsBackend.run_cointegration",
        lambda self, ds: {"test": "engle_granger", "cointegrated": True, "p_value": 0.015, "stub": False},
    )
    monkeypatch.setattr(
        "services.research.statsmodels.adapter.statsmodels_adapter.StatsmodelsBackend.run_var_vecm",
        lambda self, ds: {"model": "VAR", "lag_order": 2, "aic": -1500.0, "stub": False},
    )

    res_real = client.post(
        "/stages/econometric_validation/execute",
        json={
            "stage": {"stage_id": "s-ev-2", "stage_type": "econometric_validation", "dataset": dataset},
            "plan": {"plan_id": "p-ev-2", "strategy_id": "strat-ev"},
            "run_id": "run-ev-real",
            "correlation_id": "corr-ev-real",
        },
    )
    assert res_real.status_code == 200, res_real.text
    data_real = res_real.json()
    assert data_real["provenance"] == "real"
    assert data_real["receipt"]["mode"] == "real"
    for m in data_real["metrics"]:
        assert m["provenance"] == "real"


def test_execute_research_stage_derivatives_pricing_risk_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /stages/derivatives_pricing_risk/execute respects backend mode for simulation vs real provenance."""
    module = _load_service_module()
    client = TestClient(module.app)
    snapshot = _make_sample_quantlib_dataset()

    # 1. Default stub mode -> simulation provenance
    res_stub = client.post(
        "/stages/derivatives_pricing_risk/execute",
        json={
            "stage": {"stage_id": "s-ql-1", "stage_type": "derivatives_pricing_risk", "dataset": snapshot},
            "plan": {"plan_id": "p-ql-1", "strategy_id": "strat-ql"},
            "run_id": "run-ql-sim",
            "correlation_id": "corr-ql-sim",
        },
    )
    assert res_stub.status_code == 200, res_stub.text
    data_stub = res_stub.json()
    assert data_stub["provenance"] == "simulation"
    assert data_stub["receipt"]["mode"] == "simulation"
    for m in data_stub["metrics"]:
        assert m["provenance"] == "simulation"

    # 2. Real mode -> real provenance
    monkeypatch.setenv("PANTHEON_QUANTLIB_BACKEND", "real")
    monkeypatch.setattr(
        "services.research.quantlib.adapter.quantlib_adapter.QuantLibBackend.price_options",
        lambda self, snap: {"opt-1": {"npv": 3.14, "delta": 0.52, "model": "black_scholes_real", "stub": False}},
    )
    monkeypatch.setattr(
        "services.research.quantlib.adapter.quantlib_adapter.QuantLibBackend.analyze_fixed_income",
        lambda self, snap: {"bond-1": {"clean_price": 1002.5, "duration": 4.5, "stub": False}},
    )

    res_real = client.post(
        "/stages/derivatives_pricing_risk/execute",
        json={
            "stage": {"stage_id": "s-ql-2", "stage_type": "derivatives_pricing_risk", "dataset": snapshot},
            "plan": {"plan_id": "p-ql-2", "strategy_id": "strat-ql"},
            "run_id": "run-ql-real",
            "correlation_id": "corr-ql-real",
        },
    )
    assert res_real.status_code == 200, res_real.text
    data_real = res_real.json()
    assert data_real["provenance"] == "real"
    assert data_real["receipt"]["mode"] == "real"
    for m in data_real["metrics"]:
        assert m["provenance"] == "real"


def test_execute_research_stage_durable_idempotency_replay() -> None:
    """POST /stages/{stage_type}/execute returns cached result on replay with same idempotency key."""
    module = _load_service_module()
    client = TestClient(module.app)
    run_id = "run-idemp-001"
    corr_id = "corr-idemp-001"
    dataset = _make_sample_vectorbt_dataset("strat-idemp")
    body = {
        "stage": {"stage_id": "s1", "stage_type": "prototype_backtest", "dataset": dataset},
        "plan": {"plan_id": "p1", "strategy_id": "strat-idemp"},
        "run_id": run_id,
        "correlation_id": corr_id,
        "downstream_key": "idemp-key-unique-42",
    }

    res1 = client.post("/stages/prototype_backtest/execute", json=body)
    assert res1.status_code == 200, res1.text
    data1 = res1.json()

    # Replay with identical body/key
    res2 = client.post("/stages/prototype_backtest/execute", json=body)
    assert res2.status_code == 200, res2.text
    data2 = res2.json()

    assert data1["receipt"]["receipt_id"] == data2["receipt"]["receipt_id"]
    assert data1["artifact_digest"] == data2["artifact_digest"]
    assert data1["receipt"]["completed_at"] == data2["receipt"]["completed_at"]
