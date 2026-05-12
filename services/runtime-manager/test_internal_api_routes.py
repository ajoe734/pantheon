"""End-to-end coverage for the deployable runtime-manager command surface.

These tests prove the runtime-manager process exposes the legacy
``/api/internal/v1/...`` operator command paths that the BFF dispatches
against, sharing the in-process `RuntimeManagerService` so binding state and
kill-switch state are coherent across the canonical ``/api/...`` routes and
the legacy operator routes.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_DIR = Path(__file__).resolve().parent
EXEC_RUNTIME_DIR = REPO_ROOT / "services" / "execution" / "runtime-manager"

os.environ["PANTHEON_EXEC_RUNTIME_MANAGER_DIR"] = str(EXEC_RUNTIME_DIR)

for path in (REPO_ROOT, SERVICE_DIR, EXEC_RUNTIME_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def _valid_deploy_request(**overrides):
    request = {
        "plan_id": "plan-internal-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-internal",
        "artifact_version": "1.0.0",
        "capital_pool_id": "pool-internal-001",
        "persona_capital_binding_id": "pcb-internal-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "runtime_id": "rt-internal-001",
    }
    request.update(overrides)
    return request


def _load_main_module(store_path: Path, command_state_path: Path):
    os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = str(store_path)
    os.environ["PANTHEON_SINGLE_RUNTIME_ENFORCED"] = "true"
    os.environ["PANTHEON_COMMAND_STATE_FILE"] = str(command_state_path)
    sys.modules.pop("main", None)
    module = importlib.import_module("main")
    module._svc = None
    return module


class DeployableInternalApiTests(unittest.TestCase):
    """The deployable runtime-manager surface speaks the legacy command paths."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tempdir.name) / "bindings.json"
        self.command_state_path = Path(self.tempdir.name) / "commands.json"
        self.main = _load_main_module(self.store_path, self.command_state_path)
        self.client = self.main.app.test_client()
        self.auth = {"Authorization": "Bearer integration-test"}
        self.legacy = importlib.import_module("services.control_plane.internal.internal_api")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _deploy(self, **overrides):
        response = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(**overrides),
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()

    def test_health_route_unchanged(self):
        response = self.client.get("/__health__")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["service"], "runtime-manager")

    def test_standard_readyz_allows_optional_control_plane_urls_to_be_absent(self):
        with patch.dict(
            os.environ,
            {
                "PANTHEON_CONSULTATION_API_URL": "",
                "PANTHEON_DEPLOYMENT_API_URL": "",
                "PANTHEON_GOVERNANCE_APPROVAL_API_URL": "",
            },
        ):
            response = self.client.get("/readyz")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["service"], "runtime-manager")
        self.assertTrue(payload["ready"])
        self.assertFalse(payload["dependencies"]["deployment"]["configured"])
        self.assertTrue(payload["dependencies"]["deployment"]["optional"])

    def test_legacy_pause_route_drives_in_process_service(self):
        binding = self._deploy()
        response = self.client.post(
            f"/api/internal/v1/runtimes/{binding['binding_id']}/pause",
            json={"pause_action": "pause", "duration_seconds": 60, "reason": "smoke"},
            headers=self.auth,
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 202, payload)
        self.assertFalse(payload.get("degraded_mode", False))
        self.assertEqual(payload["status_after"], "paused")

        # Canonical readback must agree — legacy route is wired to the same store.
        readback = self.client.get(
            f"/api/runtime-bindings/{binding['binding_id']}",
            headers=self.auth,
        ).get_json()
        self.assertEqual(readback["status"], "paused")

    def test_legacy_rollback_route_retires_binding(self):
        binding = self._deploy(plan_id="plan-internal-002", capital_pool_id="pool-internal-002")
        response = self.client.post(
            "/api/internal/v1/rollbacks/execute",
            json={
                "rollback_target_type": "runtime",
                "target_id": binding["binding_id"],
                "rollback_to_version": "fallback-v1",
                "rollback_action_type": "pause_then_replace",
            },
            headers=self.auth,
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 202, payload)
        self.assertEqual(payload["status_after"], "retired")

        readback = self.client.get(
            f"/api/runtime-bindings/{binding['binding_id']}",
            headers=self.auth,
        ).get_json()
        self.assertEqual(readback["status"], "retired")

    def test_legacy_kill_switch_post_persists_state_for_canonical_readback(self):
        # Issue a soft trigger via the legacy operator path.
        response = self.client.post(
            "/api/internal/v1/kill-switch",
            headers=self.auth,
            json={
                "action": "activate",
                "scope": "pool",
                "scope_id": "pool-ks-shared",
                "reason": "drift_above_warning_threshold",
            },
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 202, payload)
        self.assertEqual(payload["status"], "executed")
        legacy_safe_mode = payload["safe_mode_after"]

        # Canonical /api/kill-switch/{pool_id}/safe-mode should reflect the same
        # in-memory state because both routes share the runtime-manager service.
        canonical = self.client.get(
            "/api/kill-switch/pool-ks-shared/safe-mode",
            headers=self.auth,
        ).get_json()
        self.assertEqual(canonical["safe_mode_state"], legacy_safe_mode)

        # Restart the service to confirm the legacy dispatch persisted to disk.
        # Reload main with the same store paths and verify safe-mode survives.
        self.main = _load_main_module(self.store_path, self.command_state_path)
        self.client = self.main.app.test_client()
        canonical_after_restart = self.client.get(
            "/api/kill-switch/pool-ks-shared/safe-mode",
            headers=self.auth,
        ).get_json()
        self.assertEqual(canonical_after_restart["safe_mode_state"], legacy_safe_mode)

    def test_legacy_command_lookup_returns_recorded_record(self):
        binding = self._deploy(plan_id="plan-internal-003", capital_pool_id="pool-internal-003")
        pause = self.client.post(
            f"/api/internal/v1/runtimes/{binding['binding_id']}/pause",
            json={"pause_action": "pause", "duration_seconds": 30},
            headers=self.auth,
        ).get_json()
        command_id = pause["command_id"]

        record = self.client.get(
            f"/api/internal/v1/commands/{command_id}",
            headers=self.auth,
        )
        self.assertEqual(record.status_code, 200)
        self.assertEqual(record.get_json()["status"], "executed")

        # Records must be on disk at the configured command-state file.
        with self.command_state_path.open() as handle:
            disk = json.load(handle)
        self.assertIn(command_id, disk)


if __name__ == "__main__":
    unittest.main()
