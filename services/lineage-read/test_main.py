from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from services.telemetry.lineage_read import LineageReadService


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


_MODULE_DIR = Path(__file__).resolve().parent
lineage_main = _load_module("lineage_read_main_test_module", _MODULE_DIR / "main.py")
_ORIGINAL_STORE_PATH = lineage_main.STORE_PATH
_ORIGINAL_LINEAGE_SERVICE = lineage_main._lineage_service

_LINEAGE_CORPUS = {
    "metadata": {
        "task_id": "LINEAGE-READ-HTTP-TEST",
        "projection_updated_at": "2026-04-28T00:00:00Z",
    },
    "node_sets": {
        "source_records": [
            {"source_id": "src-http-1", "created_at": "2026-04-28T00:00:00Z"}
        ],
        "strategy_specs": [
            {
                "strategy_id": "strategy-http-1",
                "source_id": "src-http-1",
                "created_at": "2026-04-28T00:01:00Z",
            }
        ],
        "experiment_runs": [
            {
                "run_id": "run-http-1",
                "strategy_id": "strategy-http-1",
                "created_at": "2026-04-28T00:02:00Z",
            }
        ],
        "candidate_artifacts": [
            {
                "artifact_id": "artifact-http-1",
                "artifact_version": "1.0.0",
                "run_id": "run-http-1",
                "created_at": "2026-04-28T00:03:00Z",
            }
        ],
        "approval_decisions": [
            {
                "decision_id": "approval-http-1",
                "target_id": "artifact-http-1",
                "decision_state": "approved",
                "created_at": "2026-04-28T00:04:00Z",
            }
        ],
        "capital_pools": [
            {
                "pool_id": "pool-http-1",
                "single_runtime_enforced": True,
                "created_at": "2026-04-28T00:05:00Z",
            }
        ],
        "persona_capital_bindings": [
            {
                "binding_id": "pcb-http-1",
                "capital_pool_id": "pool-http-1",
                "created_at": "2026-04-28T00:06:00Z",
            }
        ],
        "deployment_plans": [
            {
                "plan_id": "plan-http-1",
                "approval_decision_id": "approval-http-1",
                "artifact_id": "artifact-http-1",
                "artifact_version": "1.0.0",
                "capital_pool_id": "pool-http-1",
                "binding_id": "pcb-http-1",
                "created_at": "2026-04-28T00:07:00Z",
            }
        ],
        "runtime_bindings": [
            {
                "binding_id": "rb-http-1",
                "runtime_id": "runtime-http-1",
                "capital_pool_id": "pool-http-1",
                "artifact_id": "artifact-http-1",
                "artifact_version": "1.0.0",
                "plan_id": "plan-http-1",
                "persona_capital_binding_id": "pcb-http-1",
                "status": "active",
                "effective_at": "2026-04-28T00:08:00Z",
            }
        ],
        "telemetry_events": [
            {
                "event_id": "evt-http-1",
                "event_type": "pnl_snapshot",
                "binding_id": "rb-http-1",
                "runtime_id": "runtime-http-1",
                "capital_pool_id": "pool-http-1",
                "artifact_id": "artifact-http-1",
                "artifact_version": "1.0.0",
                "plan_id": "plan-http-1",
                "persona_capital_binding_id": "pcb-http-1",
                "trace_id": "trace-http-1",
                "strategy_id": "strategy-http-1",
                "event_produced_at": "2026-04-28T00:09:00Z",
            }
        ],
    },
    "query_families": [],
    "benchmark_cases": [],
}


def _record(record_id: str, **overrides) -> dict:
    payload = {
        "id": record_id,
        "source_type": "SourceRecord",
        "source_id": f"src-{record_id}",
        "target_type": "ExperimentRun",
        "target_id": f"tgt-{record_id}",
        "artifact_id": None,
        "run_id": None,
        "strategy_id": None,
        "metadata": None,
        "created_at": "2026-04-20T06:00:00Z",
    }
    payload.update(overrides)
    return payload


class TestLineageReadService(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self._tempdir.name) / "lineage.json"
        lineage_main.STORE_PATH = self.store_path
        service = LineageReadService()
        service.load_corpus(_LINEAGE_CORPUS)
        lineage_main._lineage_service = service
        lineage_main._lineage_load_error = None
        self.client = TestClient(lineage_main.app)

    def tearDown(self) -> None:
        lineage_main.STORE_PATH = _ORIGINAL_STORE_PATH
        lineage_main._lineage_service = _ORIGINAL_LINEAGE_SERVICE
        self._tempdir.cleanup()

    def test_health(self) -> None:
        response = self.client.get("/__health__")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["service"], "lineage-read")
        self.assertIs(response.json()["lineage_model_loaded"], True)

    def test_runtime_binding_projection_route_uses_lin002_engine(self) -> None:
        response = self.client.get("/api/v1/lineage/runtime-bindings/rb-http-1/projection")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["target_type"], "runtime_binding")
        self.assertEqual(payload["target_id"], "rb-http-1")
        self.assertIs(payload["derived_only"], True)
        self.assertEqual(payload["binding_status"], "active")

    def test_telemetry_event_trace_route_uses_lin002_engine(self) -> None:
        response = self.client.get("/api/v1/lineage/events/evt-http-1/trace")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["target_type"], "telemetry_event")
        self.assertEqual(payload["target_id"], "evt-http-1")
        self.assertEqual(payload["event_type"], "pnl_snapshot")
        self.assertIn("trace-http-1", payload["refs"]["trace_ids"])

    def test_missing_lineage_projection_returns_404(self) -> None:
        response = self.client.get("/api/v1/lineage/events/missing-event/trace")

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "LINEAGE_TARGET_NOT_FOUND")

    def test_create_lineage_persists_and_can_be_fetched(self) -> None:
        payload = {
            "source_type": "SourceRecord",
            "source_id": "src-001",
            "target_type": "ExperimentRun",
            "target_id": "run-001",
            "artifact_id": "artifact-001",
            "run_id": "run-001",
            "strategy_id": "strategy-001",
            "metadata": {"origin": "unit-test"},
        }

        with patch.object(lineage_main.uuid, "uuid4", return_value=UUID("11111111-1111-1111-1111-111111111111")):
            with patch.object(lineage_main, "_utc_now", return_value="2026-04-20T06:20:00Z"):
                create_response = self.client.post("/api/v1/lineage", json=payload)

        self.assertEqual(create_response.status_code, 201, create_response.text)
        created = create_response.json()
        self.assertEqual(created["id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(created["created_at"], "2026-04-20T06:20:00Z")
        self.assertEqual(created["metadata"], {"origin": "unit-test"})

        persisted = json.loads(self.store_path.read_text(encoding="utf-8"))
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["artifact_id"], "artifact-001")

        get_response = self.client.get(f"/api/v1/lineage/{created['id']}")
        self.assertEqual(get_response.status_code, 200, get_response.text)
        self.assertEqual(get_response.json(), created)

    def test_list_lineage_supports_combined_filters(self) -> None:
        lineage_main._save(
            [
                _record("rec-1", artifact_id="artifact-001", run_id="run-001", strategy_id="strategy-001"),
                _record("rec-2", artifact_id="artifact-001", run_id="run-002", strategy_id="strategy-002"),
                _record("rec-3", artifact_id="artifact-003", run_id="run-003", strategy_id="strategy-001"),
            ]
        )

        list_response = self.client.get("/api/v1/lineage")
        self.assertEqual(list_response.status_code, 200, list_response.text)
        self.assertEqual(len(list_response.json()), 3)

        artifact_response = self.client.get("/api/v1/lineage", params={"artifact_id": "artifact-001"})
        self.assertEqual(artifact_response.status_code, 200, artifact_response.text)
        self.assertEqual([item["id"] for item in artifact_response.json()], ["rec-1", "rec-2"])

        combined_response = self.client.get(
            "/api/v1/lineage",
            params={
                "artifact_id": "artifact-001",
                "run_id": "run-001",
                "strategy_id": "strategy-001",
            },
        )
        self.assertEqual(combined_response.status_code, 200, combined_response.text)
        self.assertEqual([item["id"] for item in combined_response.json()], ["rec-1"])

    def test_get_lineage_missing_record_returns_404(self) -> None:
        response = self.client.get("/api/v1/lineage/missing-record")

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Lineage record 'missing-record' not found")

    def test_create_lineage_validates_required_fields(self) -> None:
        response = self.client.post(
            "/api/v1/lineage",
            json={
                "source_type": "SourceRecord",
                "source_id": "src-001",
                "target_type": "ExperimentRun",
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertFalse(self.store_path.exists())

    def test_list_lineage_returns_empty_when_store_is_corrupted(self) -> None:
        self.store_path.write_text("{not-valid-json", encoding="utf-8")

        response = self.client.get("/api/v1/lineage")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])


if __name__ == "__main__":
    unittest.main()
