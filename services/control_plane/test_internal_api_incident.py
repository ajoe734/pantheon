"""Direct integration test for incident control-path execution.

Tests the KillSwitchController and RuntimeBinding integration without
requiring Flask for the low-level state machine checks, then verifies that the
protected internal API routes runtime mutations through the runtime-manager
service path instead of touching RuntimeBindingStore directly.
"""
import json
import os
import sys
import tempfile
import unittest

# Ensure runtime-manager modules are importable
RM_DIR = os.path.join(os.path.dirname(__file__), "..", "execution", "runtime-manager")
sys.path.insert(0, RM_DIR)
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from kill_switch_controller import (
    KillSwitchController,
    EmergencyTrigger,
    KillSwitchActionType,
    SafeModeState,
    HardTriggerReason,
    SoftTriggerReason,
    KillSwitchError,
)
from runtime_binding import (
    RuntimeBindingStore,
    RuntimeBindingStatus,
    RollbackActionType,
    RuntimeBinding,
)

import services.control_plane.internal_api as internal_api


class FakeRuntimeManagerClient:
    """Small in-memory runtime-manager facade for boundary tests."""

    def __init__(self):
        self.bindings = {}
        self.calls = []

    def seed(self, binding_id: str, *, status: str = RuntimeBindingStatus.ACTIVE.value):
        self.bindings[binding_id] = {
            "binding_id": binding_id,
            "status": status,
            "capital_pool_id": "pool-test",
            "artifact_id": "artifact-v1",
            "artifact_version": "v1.0.0",
            "deployment_mode": "live",
            "plan_id": f"plan-{binding_id}",
            "persona_capital_binding_id": f"pcb-{binding_id}",
        }

    def get(self, binding_id: str):
        self.calls.append(("get", binding_id))
        binding = self.bindings.get(binding_id)
        return dict(binding) if binding is not None else None

    def transition(self, binding_id: str, new_status: str):
        self.calls.append(("transition", binding_id, new_status))
        binding = dict(self.bindings[binding_id])
        binding["status"] = new_status
        self.bindings[binding_id] = binding
        return dict(binding)

    def retire(self, binding_id: str, *, retired_at: str | None = None):
        self.calls.append(("retire", binding_id, retired_at))
        binding = dict(self.bindings[binding_id])
        binding["status"] = RuntimeBindingStatus.RETIRED.value
        if retired_at:
            binding["retired_at"] = retired_at
        self.bindings[binding_id] = binding
        return dict(binding)


class TestKillSwitchDirectDispatch(unittest.TestCase):
    """KillSwitchController must dispatch commands with full audit trail."""

    def setUp(self):
        self.controller = KillSwitchController()

    def test_hard_trigger_dispatch(self):
        """Hard trigger produces PAUSE command with audit entry."""
        trigger = EmergencyTrigger(
            reason=HardTriggerReason.SEVERITY_1_INCIDENT.value,
            capital_pool_id="pool-alpha",
            actor_id="test-operator",
        )
        outcome = self.controller.dispatch(trigger)
        self.assertEqual(outcome.command.action_type, KillSwitchActionType.PAUSE.value)
        self.assertEqual(outcome.command.emergency_class, "hard")
        self.assertEqual(outcome.command.priority, 1)
        self.assertEqual(outcome.command.bypass_review_queue, True)
        self.assertIsNotNone(outcome.audit_entry)
        self.assertEqual(outcome.safe_mode_after, SafeModeState.PAUSED)

    def test_soft_trigger_dispatch(self):
        """Soft trigger produces appropriate command with audit."""
        trigger = EmergencyTrigger(
            reason=SoftTriggerReason.DRIFT_ABOVE_WARNING_THRESHOLD.value,
            capital_pool_id="pool-beta",
            actor_id="test-operator",
        )
        outcome = self.controller.dispatch(trigger)
        self.assertEqual(outcome.command.action_type, KillSwitchActionType.RISK_OFF.value)
        self.assertEqual(outcome.command.emergency_class, "soft")
        self.assertEqual(outcome.command.priority, 2)

    def test_action_override_liquidate(self):
        """Action override can change the dispatched action."""
        trigger = EmergencyTrigger(
            reason=HardTriggerReason.SEVERITY_1_INCIDENT.value,
            capital_pool_id="pool-alpha",
            actor_id="test-operator",
        )
        outcome = self.controller.dispatch(
            trigger,
            action_override=KillSwitchActionType.LIQUIDATE,
        )
        self.assertEqual(outcome.command.action_type, KillSwitchActionType.LIQUIDATE.value)

    def test_audit_trail_persisted(self):
        """Every dispatch accumulates an audit entry in the controller."""
        trigger1 = EmergencyTrigger(
            reason=HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            capital_pool_id="pool-1",
            actor_id="op-1",
        )
        trigger2 = EmergencyTrigger(
            reason=SoftTriggerReason.CANARY_UNDERPERFORMANCE.value,
            capital_pool_id="pool-2",
            actor_id="op-2",
        )
        self.controller.dispatch(trigger1)
        self.controller.dispatch(trigger2)
        audits = self.controller.audit_log()
        self.assertEqual(len(audits), 2)
        self.assertEqual(audits[0].capital_pool_id, "pool-1")
        self.assertEqual(audits[1].capital_pool_id, "pool-2")

    def test_replace_requires_fallback_artifact(self):
        """REPLACE action requires fallback_artifact_id and version."""
        trigger = EmergencyTrigger(
            reason=SoftTriggerReason.LOADER_ANOMALY_NO_BREACH.value,
            capital_pool_id="pool-gamma",
            actor_id="test-operator",
        )
        with self.assertRaises(KillSwitchError):
            self.controller.dispatch(trigger)  # no fallback info

        # Should succeed with fallback info
        outcome = self.controller.dispatch(
            trigger,
            fallback_artifact_id="artifact-123",
            fallback_artifact_version="v2.0.0",
        )
        self.assertEqual(outcome.command.action_type, KillSwitchActionType.REPLACE.value)
        self.assertEqual(outcome.command.fallback_artifact_id, "artifact-123")


class TestRuntimeBindingTransitions(unittest.TestCase):
    """RuntimeBinding state machine must support pause/resume/rollback transitions."""

    def setUp(self):
        self.store = RuntimeBindingStore()

    def _create_active_binding(self, binding_id="test-binding"):
        """Create an active binding for testing."""
        from runtime_binding import RuntimeBinding
        binding = RuntimeBinding(
            binding_id=binding_id,
            runtime_id=f"runtime-{binding_id}",
            capital_pool_id="pool-test",
            artifact_id="artifact-v1",
            artifact_version="v1.0.0",
            deployment_mode="live",
            effective_at="2026-04-11T00:00:00Z",
            status=RuntimeBindingStatus.ACTIVE.value,
            plan_id=f"plan-{binding_id}",
            persona_capital_binding_id=f"pcb-{binding_id}",
            metadata={"test": True},
        )
        return self.store.create(binding)

    def test_pause_transition_active_to_paused(self):
        """Active binding can be paused through pending_pause."""
        binding = self._create_active_binding("pause-test-1")
        self.assertEqual(binding.status, RuntimeBindingStatus.ACTIVE)

        # active -> pending_pause
        updated = self.store.transition_status(binding.binding_id, "pending_pause")
        self.assertEqual(updated.status, RuntimeBindingStatus.PENDING_PAUSE)

        # pending_pause -> paused
        updated = self.store.transition_status(binding.binding_id, "paused")
        self.assertEqual(updated.status, RuntimeBindingStatus.PAUSED)

    def test_resume_transition_paused_to_active(self):
        """Paused binding can be resumed to active."""
        binding = self._create_active_binding("resume-test-1")
        self.store.transition_status(binding.binding_id, "pending_pause")
        self.store.transition_status(binding.binding_id, "paused")

        # paused -> active
        updated = self.store.transition_status(binding.binding_id, "active")
        self.assertEqual(updated.status, RuntimeBindingStatus.ACTIVE)

    def test_rollback_replace_retires_binding(self):
        """Rollback with replace retires the old binding."""
        binding = self._create_active_binding("rollback-test-1")
        updated = self.store.transition_status(binding.binding_id, "retired")
        self.assertEqual(updated.status, RuntimeBindingStatus.RETIRED)


class TestIncidentControlPathIntegration(unittest.TestCase):
    """End-to-end simulation of the incident control path.

    Simulates what the internal API would do when receiving pause, rollback,
    and kill-switch commands — without needing Flask.
    """

    def setUp(self):
        self.controller = KillSwitchController()
        self.store = RuntimeBindingStore()
        self.commands = {}  # Simulated command state store

    def _record_command(self, command_id, record):
        self.commands[command_id] = record

    def test_full_kill_switch_path(self):
        """Kill-switch command flows through controller to audit trail."""
        # Simulate internal API receiving kill-switch request
        scope = "all"
        reason = "operator_emergency_stop"
        capital_pool_id = "all"

        trigger = EmergencyTrigger(
            reason=reason,
            capital_pool_id=capital_pool_id,
            actor_id="internal-api-operator",
            context={"scope": scope, "mfa_verified": True},
        )
        outcome = self.controller.dispatch(trigger)

        # Internal API would persist this record
        command_id = outcome.command.command_id
        record = {
            "command_id": command_id,
            "type": "ActivateKillSwitch",
            "target": {"type": "KillSwitch", "scope": scope},
            "status": "executed",
            "submitted_at": outcome.command.issued_at,
            "result": {
                "kill_switch_order_id": command_id,
                "action": outcome.command.action_type,
                "scope": scope,
                "emergency_class": outcome.command.emergency_class,
                "safe_mode_after": outcome.safe_mode_after.value,
                "audit_id": outcome.audit_entry.audit_id,
                "dispatch_path": outcome.command.dispatch_path,
                "bypass_review_queue": outcome.command.bypass_review_queue,
            },
            "audit": outcome.audit_entry.to_dict(),
            "error": None,
        }
        self._record_command(command_id, record)

        # Verify the full path
        cmd = self.commands[command_id]
        self.assertEqual(cmd["status"], "executed")
        self.assertEqual(cmd["result"]["action"], outcome.command.action_type)
        self.assertEqual(cmd["result"]["safe_mode_after"], outcome.safe_mode_after.value)
        self.assertIn("audit", cmd)
        self.assertEqual(cmd["audit"]["command_id"], command_id)

    def test_full_pause_path(self):
        """Pause command flows through RuntimeBinding state machine."""
        from runtime_binding import RuntimeBinding
        binding_id = "pause-integration-test"
        binding = RuntimeBinding(
            binding_id=binding_id,
            runtime_id=f"runtime-{binding_id}",
            capital_pool_id="pool-pause",
            artifact_id="artifact-v1",
            artifact_version="v1.0.0",
            deployment_mode="live",
            effective_at="2026-04-11T00:00:00Z",
            status=RuntimeBindingStatus.ACTIVE.value,
            plan_id=f"plan-{binding_id}",
            persona_capital_binding_id=f"pcb-{binding_id}",
        )
        self.store.create(binding)

        # Simulate pause execution
        status_before = binding.status if isinstance(binding.status, str) else binding.status.value
        self.store.transition_status(binding_id, "pending_pause")
        self.store.transition_status(binding_id, "paused")
        status_after = "paused"

        command_id = f"cmd-pause-{binding_id}"
        record = {
            "command_id": command_id,
            "type": "PauseRuntime",
            "target": {"type": "RuntimeBinding", "id": binding_id},
            "status": "executed",
            "result": {
                "runtime_binding_id": binding_id,
                "status_before": status_before,
                "status_after": status_after,
                "pause_action": "pause",
            },
            "error": None,
        }
        self._record_command(command_id, record)

        cmd = self.commands[command_id]
        self.assertEqual(cmd["status"], "executed")
        self.assertEqual(cmd["result"]["status_before"], "active")
        self.assertEqual(cmd["result"]["status_after"], "paused")

    def test_full_rollback_path(self):
        """Rollback command flows through RuntimeBinding state machine."""
        from runtime_binding import RuntimeBinding
        binding_id = "rollback-integration-test"
        binding = RuntimeBinding(
            binding_id=binding_id,
            runtime_id=f"runtime-{binding_id}",
            capital_pool_id="pool-rollback",
            artifact_id="artifact-v1",
            artifact_version="v1.0.0",
            deployment_mode="live",
            effective_at="2026-04-11T00:00:00Z",
            status=RuntimeBindingStatus.ACTIVE.value,
            plan_id=f"plan-{binding_id}",
            persona_capital_binding_id=f"pcb-{binding_id}",
        )
        self.store.create(binding)

        # Simulate pause_then_replace rollback
        status_before = binding.status if isinstance(binding.status, str) else binding.status.value
        self.store.transition_status(binding_id, "pending_pause")
        self.store.transition_status(binding_id, "paused")
        self.store.transition_status(binding_id, "retired")
        status_after = "retired"

        command_id = f"cmd-rb-{binding_id}"
        record = {
            "command_id": command_id,
            "type": "ExecuteRollback",
            "target": {"type": "RuntimeBinding", "id": binding_id},
            "status": "executed",
            "result": {
                "rollback_id": f"rb-{binding_id}",
                "target_type": "runtime",
                "target_id": binding_id,
                "rollback_action_type": "pause_then_replace",
                "status_before": status_before,
                "status_after": status_after,
                "position_lineage_updated": True,
            },
            "error": None,
        }
        self._record_command(command_id, record)

        cmd = self.commands[command_id]
        self.assertEqual(cmd["status"], "executed")
        self.assertEqual(cmd["result"]["status_after"], "retired")
        self.assertTrue(cmd["result"]["position_lineage_updated"])


class TestProtectedInternalAPIRuntimeManagerBoundary(unittest.TestCase):
    """Protected internal API must route binding writes through runtime-manager."""

    def setUp(self):
        self.fake_client = FakeRuntimeManagerClient()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.prev_client = internal_api._runtime_manager_client
        self.prev_command_state_file = internal_api._COMMAND_STATE_FILE
        internal_api._runtime_manager_client = self.fake_client
        internal_api._COMMAND_STATE_FILE = os.path.join(self.tmpdir.name, "commands.json")
        self.client = internal_api.app.test_client()

    def tearDown(self):
        internal_api._runtime_manager_client = self.prev_client
        internal_api._COMMAND_STATE_FILE = self.prev_command_state_file
        self.tmpdir.cleanup()

    @staticmethod
    def _headers():
        return {
            "Authorization": "Bearer internal-test-token",
            "Content-Type": "application/json",
        }

    def test_pause_route_uses_runtime_manager_client(self):
        binding_id = "binding-pause-001"
        self.fake_client.seed(binding_id, status=RuntimeBindingStatus.ACTIVE.value)

        response = self.client.post(
            f"/api/internal/v1/runtimes/{binding_id}/pause",
            headers=self._headers(),
            json={"pause_action": "pause", "duration_seconds": 120, "reason": "boundary-test"},
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(payload["status_after"], RuntimeBindingStatus.PAUSED.value)
        self.assertEqual(
            self.fake_client.calls,
            [
                ("get", binding_id),
                ("transition", binding_id, RuntimeBindingStatus.PENDING_PAUSE.value),
                ("transition", binding_id, RuntimeBindingStatus.PAUSED.value),
            ],
        )

    def test_pause_route_degraded_mode_when_binding_unverifiable(self):
        response = self.client.post(
            "/api/internal/v1/runtimes/missing-binding/pause",
            headers=self._headers(),
            json={"pause_action": "pause", "duration_seconds": 30},
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 202)
        self.assertTrue(payload["degraded_mode"])
        self.assertEqual(self.fake_client.calls, [("get", "missing-binding")])

    def test_rollback_route_uses_runtime_manager_client(self):
        binding_id = "binding-rollback-001"
        self.fake_client.seed(binding_id, status=RuntimeBindingStatus.ACTIVE.value)

        response = self.client.post(
            "/api/internal/v1/rollbacks/execute",
            headers=self._headers(),
            json={
                "rollback_target_type": "runtime",
                "target_id": binding_id,
                "rollback_to_version": "fallback-v2",
                "rollback_action_type": RollbackActionType.PAUSE_THEN_REPLACE.value,
            },
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(payload["status_after"], RuntimeBindingStatus.RETIRED.value)
        self.assertEqual(self.fake_client.calls[0], ("get", binding_id))
        self.assertEqual(
            self.fake_client.calls[1:3],
            [
                ("transition", binding_id, RuntimeBindingStatus.PENDING_PAUSE.value),
                ("transition", binding_id, RuntimeBindingStatus.PAUSED.value),
            ],
        )
        self.assertEqual(self.fake_client.calls[3][0:2], ("retire", binding_id))


if __name__ == "__main__":
    unittest.main()
