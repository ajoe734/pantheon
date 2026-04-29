from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_service_module():
    with mock.patch.dict(
        "os.environ",
        {
            "POLICY_LEARNING_DATA_DIR": tempfile.mkdtemp(),
            "POLICY_LEARNING_ENABLE_PRODUCTION_ADAPTERS": "false",
        },
    ):
        sys.modules.pop("store", None)
        sys.path.insert(0, str(SERVICE_DIR))
        try:
            spec = importlib.util.spec_from_file_location("policy_learning_test_main", SERVICE_DIR / "main.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["policy_learning_test_main"] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.modules.pop("store", None)
            try:
                sys.path.remove(str(SERVICE_DIR))
            except ValueError:
                pass


def test_policy_learning_stub_lifecycle_is_replayable() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    capabilities = client.get("/api/policy-learning/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["production_activation"] == "disabled"

    created = client.post(
        "/api/policy-learning/jobs",
        json={
            "policy_id": "persona-alpha-routing",
            "objective": "Evaluate a non-production candidate policy update.",
            "adapter": "stub",
            "requested_mode": "stub",
            "source_refs": [{"type": "training_session", "id": "trn-1"}],
            "proposed_at": "2026-04-28T19:00:00Z",
        },
    )
    assert created.status_code == 201
    job = created.json()
    assert job["status"] == "proposed"
    assert job["events"][0]["sequence_number"] == 1

    status = client.get(f"/api/policy-learning/jobs/{job['job_id']}/status")
    assert status.status_code == 200
    assert status.json()["job_id"] == job["job_id"]
    assert status.json()["production_activation"] == "disabled"

    rejected = client.post(
        f"/api/policy-learning/jobs/{job['job_id']}/reject",
        json={"reason": "operator_closed_stub", "rejected_at": "2026-04-28T19:05:00Z"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["events"][-1]["sequence_number"] == 2

    replay = client.get(f"/api/policy-learning/jobs/{job['job_id']}")
    assert replay.status_code == 200
    assert replay.json()["rejection"]["reason"] == "operator_closed_stub"


def test_production_adapters_are_rejected_by_default() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    for adapter, mode in (("qlib", "production"), ("trl", "paper"), ("rl", "canary"), ("wandb", "live")):
        result = client.post(
            "/api/policy-learning/jobs",
            json={
                "policy_id": f"policy-{adapter}",
                "objective": "Attempt production activation.",
                "adapter": adapter,
                "requested_mode": mode,
                "proposed_at": "2026-04-28T19:10:00Z",
            },
        )
        assert result.status_code == 201
        payload = result.json()
        assert payload["status"] == "rejected"
        assert payload["rejection"]["reason"] == "production_adapter_disabled"
        assert payload["production_activation"] == "disabled"
