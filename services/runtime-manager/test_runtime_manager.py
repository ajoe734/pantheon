"""Unit tests for the runtime-manager service, client, and HTTP surface."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_DIR = Path(__file__).resolve().parent
EXEC_RUNTIME_DIR = REPO_ROOT / "services" / "execution" / "runtime-manager"

os.environ["PANTHEON_EXEC_RUNTIME_MANAGER_DIR"] = str(EXEC_RUNTIME_DIR)

for path in (SERVICE_DIR, EXEC_RUNTIME_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from runtime_manager_client import RuntimeManagerClient
from service import RuntimeManagerError, RuntimeManagerService


def _valid_deploy_request(**overrides):
    request = {
        "plan_id": "plan-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-alpha",
        "artifact_version": "1.0.0",
        "capital_pool_id": "pool-001",
        "persona_capital_binding_id": "pcb-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "runtime_id": "rt-001",
    }
    request.update(overrides)
    return request


def _load_main_module(store_path: Path):
    os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = str(store_path)
    os.environ["PANTHEON_SINGLE_RUNTIME_ENFORCED"] = "true"
    sys.modules.pop("main", None)
    module = importlib.import_module("main")
    module._svc = None
    return module


class RuntimeManagerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tempdir.name) / "bindings.json"
        self.service = RuntimeManagerService(store_path=self.store_path, single_runtime_enforced=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_deploy_creates_binding_and_list_by_plan(self):
        binding = self.service.deploy(_valid_deploy_request())

        self.assertEqual(binding.plan_id, "plan-001")
        self.assertEqual(binding.deployment_mode, "paper")
        self.assertEqual(binding.status, "active")
        self.assertEqual(binding.persona_capital_binding_id, "pcb-001")

        plan_bindings = self.service.list_by_plan("plan-001")
        self.assertEqual([item.binding_id for item in plan_bindings], [binding.binding_id])

    def test_deploy_rejects_rollback_parent_without_action_type(self):
        with self.assertRaisesRegex(RuntimeManagerError, "rollback_action_type is required"):
            self.service.deploy(
                _valid_deploy_request(
                    plan_id="plan-rollback-invalid",
                    rollback_parent="rb-parent",
                    runtime_id="rt-invalid",
                )
            )

    def test_rollback_replace_creates_replacement_and_retires_old_binding(self):
        original = self.service.deploy(_valid_deploy_request())

        result = self.service.rollback(
            {
                "current_binding_id": original.binding_id,
                "action_type": "replace",
                "replacement_plan_id": "plan-002",
                "replacement_artifact_id": "artifact-beta",
                "replacement_artifact_version": "2.0.0",
                "replacement_persona_capital_binding_id": "pcb-002",
                "replacement_allowed_deployment_scope": "live",
                "replacement_runtime_id": "rt-002",
            }
        )

        self.assertEqual(result["action_type"], "replace")
        self.assertEqual(result["old_binding"]["status"], "retired")
        self.assertEqual(result["new_binding"]["status"], "active")
        self.assertEqual(result["new_binding"]["rollback_parent"], original.binding_id)
        self.assertEqual(
            result["position_lineage"]["current_managed_by_binding_id"],
            result["new_binding"]["binding_id"],
        )
        self.assertEqual(self.service.get_active_for_pool("pool-001").binding_id, result["new_binding"]["binding_id"])

    def test_rollback_liquidate_then_replace_start_paused_keeps_old_owner_until_confirmed(self):
        original = self.service.deploy(_valid_deploy_request())

        result = self.service.rollback(
            {
                "current_binding_id": original.binding_id,
                "action_type": "liquidate_then_replace",
                "replacement_plan_id": "plan-003",
                "replacement_artifact_id": "artifact-gamma",
                "replacement_artifact_version": "3.0.0",
                "replacement_persona_capital_binding_id": "pcb-003",
                "replacement_allowed_deployment_scope": "live",
                "replacement_runtime_id": "rt-003",
                "replacement_start_paused": True,
            }
        )

        self.assertEqual(result["old_binding"]["status"], "retired")
        self.assertEqual(result["new_binding"]["status"], "paused")
        self.assertEqual(
            result["position_lineage"]["current_managed_by_binding_id"],
            original.binding_id,
        )
        self.assertIn("confirmed zero", result["position_lineage"]["note"])


class RuntimeManagerClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tempdir.name) / "bindings.json"
        os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = str(self.store_path)
        os.environ["PANTHEON_SINGLE_RUNTIME_ENFORCED"] = "true"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_local_client_dispatches_deploy_and_transition_commands(self):
        client = RuntimeManagerClient(base_url=None)

        binding = client.deploy(_valid_deploy_request())
        transitioned = client.transition(binding["binding_id"], "pending_pause")

        self.assertEqual(binding["status"], "active")
        self.assertEqual(transitioned["status"], "pending_pause")
        self.assertIsNone(client.get_active_for_pool("pool-001"))
        self.assertEqual(client.list_by_pool("pool-001")[0]["binding_id"], binding["binding_id"])


class RuntimeManagerHttpRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tempdir.name) / "bindings.json"
        self.main = _load_main_module(self.store_path)
        self.client = self.main.app.test_client()
        self.auth = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_deploy_route_requires_loader_checks_field(self):
        body = _valid_deploy_request()
        body.pop("loader_checks_passed")

        response = self.client.post("/api/runtimes/deploy", json=body, headers=self.auth)

        self.assertEqual(response.status_code, 400)
        self.assertIn("loader_checks_passed", response.get_json()["error"]["message"])

    def test_transition_route_requires_new_status(self):
        binding = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(),
            headers=self.auth,
        ).get_json()

        response = self.client.post(
            f"/api/runtime-bindings/{binding['binding_id']}/transition",
            json={},
            headers=self.auth,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "MISSING_FIELDS")

    def test_rollback_route_returns_replacement_binding_payload(self):
        created = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(),
            headers=self.auth,
        ).get_json()

        response = self.client.post(
            "/api/rollback",
            json={
                "current_binding_id": created["binding_id"],
                "action_type": "replace",
                "replacement_plan_id": "plan-004",
                "replacement_artifact_id": "artifact-delta",
                "replacement_artifact_version": "4.0.0",
                "replacement_persona_capital_binding_id": "pcb-004",
                "replacement_allowed_deployment_scope": "live",
                "replacement_runtime_id": "rt-004",
            },
            headers=self.auth,
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(payload["action_type"], "replace")
        self.assertEqual(payload["old_binding"]["status"], "retired")
        self.assertEqual(payload["new_binding"]["artifact_id"], "artifact-delta")


if __name__ == "__main__":
    unittest.main()
