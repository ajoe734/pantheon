"""Unit tests for the runtime-manager service, client, and HTTP surface."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
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

EXEC_RUNTIME_DIR_STR = str(EXEC_RUNTIME_DIR)
if EXEC_RUNTIME_DIR_STR not in sys.path:
    sys.path.insert(0, EXEC_RUNTIME_DIR_STR)

from kill_switch_controller import (  # noqa: E402
    FAST_PATH_BENCHMARK_ITERATIONS,
    FAST_PATH_LATENCY_TARGET_MS,
    EmergencyTrigger,
    HardTriggerReason,
    KillSwitchActionType,
    KillSwitchController,
    SafeModeState,
    SoftTriggerReason,
)


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


def _valid_activation_gate(**overrides):
    gate = {
        "promotion_gate_decision_id": "gate-appr-001",
        "human_gate_packet_ref": "docs/deployment/evidence/execution-sandbox-canary-ready/human-gate.json",
        "broker_sandbox_smoke_ref": "docs/deployment/evidence/execution-sandbox-canary-ready/broker-smoke",
        "risk_owner_approval_ref": "risk-owner-approval-001",
        "operator_approval_ref": "operator-approval-001",
        "capital_scale_pct": 5.0,
        "gross_scale_pct": 25.0,
    }
    gate.update(overrides)
    return gate


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
        self.assertEqual(binding.execution_mode, "paper")
        self.assertEqual(binding.status, "active")
        self.assertEqual(binding.persona_capital_binding_id, "pcb-001")

        plan_bindings = self.service.list_by_plan("plan-001")
        self.assertEqual([item.binding_id for item in plan_bindings], [binding.binding_id])

    def test_deploy_requires_deployment_plan_reference(self):
        with self.assertRaisesRegex(RuntimeManagerError, "DeploymentPlan"):
            self.service.deploy(_valid_deploy_request(plan_id=""))

    def test_runtime_binding_stage_matches_deployment_plan_target(self):
        binding = self.service.deploy(
            _valid_deploy_request(
                plan_id="plan-canary-001",
                target_stage="canary",
                allowed_deployment_scope="live",
                runtime_id="rt-canary-001",
                promotion_gate=_valid_activation_gate(),
            )
        )

        self.assertEqual(binding.plan_id, "plan-canary-001")
        self.assertEqual(binding.deployment_mode, "canary")
        self.assertEqual(binding.execution_mode, "canary")
        self.assertEqual(binding.to_dict()["deployment_mode"], "canary")
        self.assertEqual(binding.to_dict()["execution_mode"], "canary")
        self.assertEqual(binding.metadata["activation_gate"]["broker_sandbox_smoke_ref"], "docs/deployment/evidence/execution-sandbox-canary-ready/broker-smoke")

    def test_deploy_rejects_execution_mode_mismatch(self):
        with self.assertRaisesRegex(RuntimeManagerError, "execution_mode"):
            self.service.deploy(
                _valid_deploy_request(
                    plan_id="plan-canary-exec-mode-mismatch",
                    target_stage="canary",
                    execution_mode="live",
                    allowed_deployment_scope="live",
                    runtime_id="rt-canary-exec-mode-mismatch",
                    promotion_gate=_valid_activation_gate(),
                )
            )

    def test_canary_deploy_requires_explicit_activation_gate(self):
        with self.assertRaisesRegex(RuntimeManagerError, "activation is blocked"):
            self.service.deploy(
                _valid_deploy_request(
                    plan_id="plan-canary-no-gate",
                    target_stage="canary",
                    allowed_deployment_scope="live",
                    runtime_id="rt-canary-no-gate",
                )
            )

    def test_canary_deploy_requires_policy_scale_in_gate(self):
        with self.assertRaisesRegex(RuntimeManagerError, "capital_scale_pct"):
            self.service.deploy(
                _valid_deploy_request(
                    plan_id="plan-canary-scale-bad",
                    target_stage="canary",
                    allowed_deployment_scope="live",
                    runtime_id="rt-canary-scale-bad",
                    promotion_gate=_valid_activation_gate(capital_scale_pct=12.0),
                )
            )

    def test_live_deploy_requires_canary_observation_gate(self):
        with self.assertRaisesRegex(RuntimeManagerError, "canary_observation_ref"):
            self.service.deploy(
                _valid_deploy_request(
                    plan_id="plan-live-no-observation",
                    target_stage="live",
                    allowed_deployment_scope="live",
                    runtime_id="rt-live-no-observation",
                    promotion_gate=_valid_activation_gate(capital_scale_pct=100.0, gross_scale_pct=100.0),
                )
            )

    def test_deploy_records_allowed_risk_policy_evaluation(self):
        binding = self.service.deploy(
            _valid_deploy_request(
                risk_policy_ref="risk-main",
                risk_policy={
                    "risk_policy_id": "risk-main",
                    "allowed_stages": ["paper"],
                    "max_single_name_weight": 0.7,
                },
                metadata={"target_weights": {"AAPL": 0.6}},
            )
        )

        self.assertEqual(binding.metadata["risk_policy_evaluation"]["decision"], "allowed")
        self.assertEqual(binding.metadata["risk_policy_evaluation"]["risk_policy_id"], "risk-main")

    def test_deploy_rejects_risk_policy_identity_mismatch(self):
        with self.assertRaisesRegex(RuntimeManagerError, "RiskPolicy"):
            self.service.deploy(
                _valid_deploy_request(
                    risk_policy_ref="risk-other",
                    risk_policy={"risk_policy_id": "risk-main"},
                )
            )

    def test_deploy_preserves_strategy_id_in_metadata_for_runtime_readers(self):
        binding = self.service.deploy(_valid_deploy_request(strategy_id="strat-001"))

        self.assertEqual(binding.metadata["strategy_id"], "strat-001")

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

    def test_execute_kill_switch_emits_foundation_context_and_replays_idempotently(self):
        request = {
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-foundation-ks",
            "actor_id": "operator-foundation",
            "idempotency_key": "idmp-runtime-ks-001",
            "foundation": {
                "trace_context": {
                    "trace_id": "trace-runtime-upstream-001",
                    "correlation_id": "corr-runtime-upstream-001",
                    "idempotency_key": "idmp-runtime-ks-001",
                }
            },
            "context": {"source": "unit-test"},
        }

        result = self.service.execute_kill_switch(request)

        self.assertEqual(
            result["foundation"]["trace_context"]["trace_id"],
            "trace-runtime-upstream-001",
        )
        self.assertEqual(
            result["command"]["metadata"]["foundation_trace_id"],
            "trace-runtime-upstream-001",
        )
        self.assertEqual(
            result["foundation"]["idempotency_record"]["idempotency_key"],
            "idmp-runtime-ks-001",
        )
        self.assertEqual(result["foundation"]["idempotency_record"]["status"], "succeeded")
        self.assertEqual(result["foundation"]["policy_decision"]["decision"], "allow")
        self.assertEqual(result["foundation"]["audit_action"]["trace_id"], "trace-runtime-upstream-001")
        self.assertEqual(result["telemetry_ack"]["ack_status"], "fail_closed")
        self.assertTrue(result["telemetry_ack"]["ack_required"])
        self.assertFalse(result["telemetry_ack"]["ack_received"])
        self.assertEqual(result["telemetry_ack"]["event_type"], "kill_switch_action")
        self.assertEqual(len(self.service.get_kill_switch_audit_log()), 1)

        replayed = self.service.execute_kill_switch(request)

        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(replayed["command"]["command_id"], result["command"]["command_id"])
        self.assertEqual(replayed["telemetry_ack"]["ack_status"], "fail_closed")
        self.assertEqual(len(self.service.get_kill_switch_audit_log()), 1)


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

    def test_rt004_action_lane_deploy_pause_replace_and_history(self):
        created_response = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(
                plan_id="plan-rt004-001",
                capital_pool_id="pool-rt004",
                runtime_id="rt-rt004-001",
            ),
            headers=self.auth,
        )
        created = created_response.get_json()

        self.assertEqual(created_response.status_code, 201, created)
        self.assertEqual(created["status"], "active")

        pending_pause = self.client.post(
            f"/api/runtime-bindings/{created['binding_id']}/transition",
            json={"new_status": "pending_pause"},
            headers=self.auth,
        )
        paused = self.client.post(
            f"/api/runtime-bindings/{created['binding_id']}/transition",
            json={"new_status": "paused"},
            headers=self.auth,
        )

        self.assertEqual(pending_pause.status_code, 200, pending_pause.get_json())
        self.assertEqual(pending_pause.get_json()["status"], "pending_pause")
        self.assertEqual(paused.status_code, 200, paused.get_json())
        self.assertEqual(paused.get_json()["status"], "paused")

        replace_response = self.client.post(
            "/api/rollback",
            json={
                "current_binding_id": created["binding_id"],
                "action_type": "replace",
                "replacement_plan_id": "plan-rt004-002",
                "replacement_artifact_id": "artifact-rt004-replacement",
                "replacement_artifact_version": "2.0.0",
                "replacement_persona_capital_binding_id": "pcb-rt004-replacement",
                "replacement_allowed_deployment_scope": "live",
                "replacement_runtime_id": "rt-rt004-002",
            },
            headers=self.auth,
        )
        replaced = replace_response.get_json()

        self.assertEqual(replace_response.status_code, 201, replaced)
        self.assertEqual(replaced["action_type"], "replace")
        self.assertEqual(replaced["old_binding"]["status"], "retired")
        self.assertEqual(replaced["new_binding"]["status"], "active")
        self.assertEqual(replaced["new_binding"]["rollback_parent"], created["binding_id"])
        self.assertEqual(
            replaced["position_lineage"]["current_managed_by_binding_id"],
            replaced["new_binding"]["binding_id"],
        )

        history = self.client.get(
            "/api/rollback/history?pool_id=pool-rt004",
            headers=self.auth,
        )
        history_payload = history.get_json()

        self.assertEqual(history.status_code, 200, history_payload)
        self.assertEqual(history_payload["count"], 1)
        self.assertEqual(history_payload["rollbacks"][0]["rollback_action_type"], "replace")

    def test_runtime_fleet_desired_state_route_returns_active_and_excluded(self):
        paper = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(
                plan_id="plan-fleet-paper",
                capital_pool_id="pool-fleet-paper",
                runtime_id="rt-fleet-paper",
            ),
            headers=self.auth,
        ).get_json()
        canary = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(
                plan_id="plan-fleet-canary",
                target_stage="canary",
                capital_pool_id="pool-fleet-canary",
                runtime_id="rt-fleet-canary",
                promotion_gate=_valid_activation_gate(),
            ),
            headers=self.auth,
        ).get_json()
        paused = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(
                plan_id="plan-fleet-paused",
                capital_pool_id="pool-fleet-paused",
                runtime_id="rt-fleet-paused",
            ),
            headers=self.auth,
        ).get_json()
        self.client.post(
            f"/api/runtime-bindings/{paused['binding_id']}/transition",
            json={"new_status": "pending_pause"},
            headers=self.auth,
        )
        self.client.post(
            f"/api/runtime-bindings/{paused['binding_id']}/transition",
            json={"new_status": "paused"},
            headers=self.auth,
        )

        response = self.client.get(
            "/api/runtime-fleet/desired-state?include_excluded=true",
            headers=self.auth,
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["active_count"], 2)
        self.assertEqual(
            {binding["binding_id"] for binding in payload["bindings"]},
            {paper["binding_id"], canary["binding_id"]},
        )
        canary_binding = next(
            binding
            for binding in payload["bindings"]
            if binding["binding_id"] == canary["binding_id"]
        )
        self.assertEqual(canary_binding["policy_envelope"]["stage"], "canary")
        self.assertEqual(
            canary_binding["policy_envelope"]["allowed_deployment_scope"],
            "live",
        )
        self.assertEqual(payload["excluded_count"], 1)
        self.assertEqual(payload["excluded"][0]["binding_id"], paused["binding_id"])
        self.assertEqual(payload["excluded"][0]["exclusion_reason"], "draining")

    def test_runtime_fleet_desired_state_route_rejects_invalid_stage(self):
        response = self.client.get(
            "/api/runtime-fleet/desired-state?stage=live",
            headers=self.auth,
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 400, payload)
        self.assertEqual(payload["error"]["code"], "INVALID_STAGE")
        self.assertIn("paper", payload["error"]["message"])
        self.assertIn("canary", payload["error"]["message"])


class KillSwitchControllerUnitTests(unittest.TestCase):
    """Pure unit tests for KillSwitchController — no I/O."""

    def setUp(self) -> None:
        self.controller = KillSwitchController()

    def _hard_trigger(self, **kwargs):
        defaults = {
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-ks-001",
            "actor_id": "operator-1",
        }
        defaults.update(kwargs)
        return EmergencyTrigger(**defaults)

    def _soft_trigger(self, **kwargs):
        defaults = {
            "reason": SoftTriggerReason.DRIFT_ABOVE_WARNING_THRESHOLD.value,
            "capital_pool_id": "pool-ks-002",
            "actor_id": "alert-engine",
        }
        defaults.update(kwargs)
        return EmergencyTrigger(**defaults)

    # --- fast-path dispatch ---

    def test_hard_trigger_bypasses_review_queue(self):
        outcome = self.controller.dispatch(self._hard_trigger())
        self.assertTrue(outcome.command.bypass_review_queue)
        self.assertEqual(outcome.command.dispatch_path, "runtime_manager_fast_path")
        self.assertEqual(outcome.command.emergency_class, "hard")
        self.assertEqual(outcome.command.priority, 1)

    def test_soft_trigger_bypasses_review_queue(self):
        outcome = self.controller.dispatch(self._soft_trigger())
        self.assertTrue(outcome.command.bypass_review_queue)
        self.assertEqual(outcome.command.emergency_class, "soft")
        self.assertEqual(outcome.command.priority, 2)

    def test_action_override_respected(self):
        outcome = self.controller.dispatch(
            self._soft_trigger(),
            action_override=KillSwitchActionType.LIQUIDATE,
        )
        self.assertEqual(outcome.command.action_type, "liquidate")

    def test_replace_action_requires_fallback_artifact(self):
        from kill_switch_controller import KillSwitchError
        with self.assertRaises(KillSwitchError):
            self.controller.dispatch(
                self._hard_trigger(),
                action_override=KillSwitchActionType.REPLACE,
            )

    def test_replace_action_succeeds_with_fallback_artifact(self):
        outcome = self.controller.dispatch(
            self._hard_trigger(),
            action_override=KillSwitchActionType.REPLACE,
            fallback_artifact_id="artifact-fallback",
            fallback_artifact_version="1.0.0",
        )
        self.assertEqual(outcome.command.action_type, "replace")
        self.assertEqual(outcome.command.fallback_artifact_id, "artifact-fallback")

    # --- audit trail ---

    def test_dispatch_creates_audit_entry(self):
        self.controller.dispatch(self._hard_trigger())
        entries = self.controller.audit_log()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e.reason, HardTriggerReason.OPERATOR_EMERGENCY_STOP.value)
        self.assertIsNotNone(e.audit_id)
        self.assertIsNotNone(e.audited_at)
        self.assertEqual(e.safe_mode_before, SafeModeState.NORMAL.value)

    def test_multiple_dispatches_accumulate_audit_entries(self):
        self.controller.dispatch(self._hard_trigger())
        self.controller.dispatch(self._soft_trigger())
        self.assertEqual(len(self.controller.audit_log()), 2)

    def test_audit_entry_records_safe_mode_transition(self):
        outcome = self.controller.dispatch(self._hard_trigger())
        entry = self.controller.audit_log()[0]
        self.assertEqual(entry.safe_mode_after, outcome.safe_mode_after.value)
        self.assertNotEqual(entry.safe_mode_before, SafeModeState.PAUSED.value)

    def test_manual_safe_mode_advance_emits_audit_entry(self):
        pool = "pool-recovery"
        # NORMAL → PAUSED via dispatch
        self.controller.dispatch(self._hard_trigger(capital_pool_id=pool))
        # PAUSED → RECOVERY_TESTING via governance advance
        self.controller.advance_safe_mode(
            pool, SafeModeState.RECOVERY_TESTING, actor_id="governance-gate"
        )
        entries = self.controller.audit_log()
        self.assertEqual(len(entries), 2)
        adv_entry = entries[1]
        self.assertEqual(adv_entry.safe_mode_before, SafeModeState.PAUSED.value)
        self.assertEqual(adv_entry.safe_mode_after, SafeModeState.RECOVERY_TESTING.value)
        self.assertEqual(adv_entry.actor_id, "governance-gate")

    # --- safe-mode state machine ---

    def test_soft_trigger_advances_safe_mode_to_risk_off(self):
        outcome = self.controller.dispatch(self._soft_trigger())
        self.assertEqual(outcome.safe_mode_after, SafeModeState.RISK_OFF)

    def test_hard_operator_stop_advances_safe_mode_to_paused(self):
        outcome = self.controller.dispatch(self._hard_trigger())
        self.assertEqual(outcome.safe_mode_after, SafeModeState.PAUSED)


class KillSwitchServiceTests(unittest.TestCase):
    """Service-layer kill-switch fast-path tests."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tempdir.name) / "bindings.json"
        self.service = RuntimeManagerService(store_path=self.store_path, single_runtime_enforced=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _deploy_active_binding(self, pool_id="pool-ks-svc"):
        return self.service.deploy(_valid_deploy_request(
            capital_pool_id=pool_id,
            runtime_id=f"rt-ks-{pool_id}",
        ))

    def test_execute_kill_switch_pause_transitions_active_binding(self):
        binding = self._deploy_active_binding()
        result = self.service.execute_kill_switch({
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-ks-svc",
            "actor_id": "operator-1",
            "binding_id": binding.binding_id,
        })
        self.assertIn("command", result)
        self.assertIn("audit_entry", result)
        self.assertEqual(result["command"]["action_type"], "pause")
        self.assertEqual(result["command"]["bypass_review_queue"], True)
        ba = result["binding_action"]
        self.assertEqual(ba["binding"]["status"], "paused")
        ack = result["telemetry_ack"]
        self.assertEqual(ack["ack_status"], "acknowledged")
        self.assertTrue(ack["ack_received"])
        self.assertFalse(ack["fail_closed"])
        self.assertEqual(ack["command_id"], result["command"]["command_id"])
        self.assertEqual(ack["audit_id"], result["audit_entry"]["audit_id"])
        self.assertEqual(ack["binding_id"], binding.binding_id)
        self.assertEqual(ack["runtime_binding_id"], binding.binding_id)
        self.assertEqual(ack["runtime_status_after"], "paused")
        self.assertEqual(ack["telemetry_event_type"], "kill_switch_action")

    def test_execute_kill_switch_populates_audit_trail(self):
        self._deploy_active_binding()
        self.service.execute_kill_switch({
            "reason": HardTriggerReason.DRAWDOWN_HARD_BREACH.value,
            "capital_pool_id": "pool-ks-svc",
            "actor_id": "risk-monitor",
        })
        log = self.service.get_kill_switch_audit_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["reason"], HardTriggerReason.DRAWDOWN_HARD_BREACH.value)
        self.assertIn("audit_id", log[0])
        self.assertIn("audited_at", log[0])

    def test_execute_kill_switch_soft_trigger_risk_off(self):
        self._deploy_active_binding()
        result = self.service.execute_kill_switch({
            "reason": SoftTriggerReason.DRIFT_ABOVE_WARNING_THRESHOLD.value,
            "capital_pool_id": "pool-ks-svc",
            "actor_id": "drift-detector",
        })
        self.assertEqual(result["command"]["action_type"], "risk_off")
        self.assertEqual(result["safe_mode_after"], SafeModeState.RISK_OFF.value)
        self.assertEqual(result["binding_action"]["binding"]["status"], "paused")
        self.assertEqual(result["telemetry_ack"]["ack_status"], "acknowledged")
        self.assertEqual(result["telemetry_ack"]["action_type"], "risk_off")

    def test_execute_kill_switch_without_runtime_ack_fails_closed(self):
        result = self.service.execute_kill_switch({
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-without-active-runtime",
            "actor_id": "operator-1",
        })

        self.assertIsNone(result["binding_action"])
        self.assertEqual(result["safe_mode_after"], SafeModeState.PAUSED.value)
        self.assertEqual(result["telemetry_ack"]["ack_status"], "fail_closed")
        self.assertTrue(result["telemetry_ack"]["fail_closed"])
        self.assertFalse(result["telemetry_ack"]["runtime_state_recorded"])
        self.assertFalse(result["telemetry_ack"]["capital_state_recorded"])

    def test_get_safe_mode_returns_normal_for_unknown_pool(self):
        state = self.service.get_safe_mode("pool-unknown")
        self.assertEqual(state, SafeModeState.NORMAL.value)

    def test_advance_safe_mode_follows_allowed_transition(self):
        pool = "pool-recovery-svc"
        # Drive to PAUSED via kill-switch
        self.service.execute_kill_switch({
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": pool,
            "actor_id": "operator-1",
        })
        self.assertEqual(self.service.get_safe_mode(pool), SafeModeState.PAUSED.value)
        # Advance to RECOVERY_TESTING
        new_state = self.service.advance_safe_mode(
            pool, SafeModeState.RECOVERY_TESTING.value, actor_id="governance"
        )
        self.assertEqual(new_state, SafeModeState.RECOVERY_TESTING.value)

    def test_invalid_kill_switch_reason_raises_error(self):
        with self.assertRaises(RuntimeManagerError):
            self.service.execute_kill_switch({
                "reason": "not_a_valid_reason",
                "capital_pool_id": "pool-ks-svc",
                "actor_id": "operator-1",
            })


class KillSwitchHttpRouteTests(unittest.TestCase):
    """HTTP route tests for kill-switch endpoints."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tempdir.name) / "bindings.json"
        self.main = _load_main_module(self.store_path)
        self.client = self.main.app.test_client()
        self.auth = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _deploy(self, pool_id="pool-ks-http"):
        return self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(capital_pool_id=pool_id, runtime_id="rt-ks-http"),
            headers=self.auth,
        ).get_json()

    def test_kill_switch_dispatch_pause_hard_trigger(self):
        binding = self._deploy()
        response = self.client.post(
            "/api/kill-switch/dispatch",
            json={
                "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
                "capital_pool_id": "pool-ks-http",
                "actor_id": "operator-1",
                "binding_id": binding["binding_id"],
            },
            headers=self.auth,
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIn("command", payload)
        self.assertIn("audit_entry", payload)
        self.assertTrue(payload["command"]["bypass_review_queue"])
        self.assertEqual(payload["command"]["action_type"], "pause")

    def test_kill_switch_dispatch_requires_reason(self):
        response = self.client.post(
            "/api/kill-switch/dispatch",
            json={"capital_pool_id": "pool-ks-http", "actor_id": "op"},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("reason", response.get_json()["error"]["message"])

    def test_kill_switch_dispatch_requires_bearer_token(self):
        response = self.client.post(
            "/api/kill-switch/dispatch",
            json={
                "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
                "capital_pool_id": "pool-ks-http",
                "actor_id": "op",
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_get_safe_mode_returns_normal_initially(self):
        response = self.client.get(
            "/api/kill-switch/pool-ks-http/safe-mode",
            headers=self.auth,
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["safe_mode_state"], SafeModeState.NORMAL.value)

    def test_get_safe_mode_reflects_dispatch(self):
        self._deploy()
        self.client.post(
            "/api/kill-switch/dispatch",
            json={
                "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
                "capital_pool_id": "pool-ks-http",
                "actor_id": "op",
            },
            headers=self.auth,
        )
        response = self.client.get(
            "/api/kill-switch/pool-ks-http/safe-mode",
            headers=self.auth,
        )
        self.assertEqual(response.get_json()["safe_mode_state"], SafeModeState.PAUSED.value)

    def test_advance_safe_mode_via_post(self):
        # Drive to PAUSED first
        self._deploy()
        self.client.post(
            "/api/kill-switch/dispatch",
            json={
                "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
                "capital_pool_id": "pool-ks-http",
                "actor_id": "op",
            },
            headers=self.auth,
        )
        response = self.client.post(
            "/api/kill-switch/pool-ks-http/safe-mode",
            json={"target_state": "recovery_testing", "actor_id": "governance"},
            headers=self.auth,
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["safe_mode_state"], SafeModeState.RECOVERY_TESTING.value)

    def test_audit_log_endpoint_returns_entries(self):
        self._deploy()
        self.client.post(
            "/api/kill-switch/dispatch",
            json={
                "reason": SoftTriggerReason.CANARY_UNDERPERFORMANCE.value,
                "capital_pool_id": "pool-ks-http",
                "actor_id": "canary-monitor",
            },
            headers=self.auth,
        )
        response = self.client.get("/api/kill-switch/audit-log", headers=self.auth)
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["count"], 1)
        entry = payload["entries"][0]
        self.assertIn("audit_id", entry)
        self.assertIn("audited_at", entry)
        self.assertEqual(entry["reason"], SoftTriggerReason.CANARY_UNDERPERFORMANCE.value)


class KillSwitchDurabilityTests(unittest.TestCase):
    """Regression tests: safe-mode and audit log survive service restart."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tempdir.name) / "bindings.json"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _svc(self) -> RuntimeManagerService:
        return RuntimeManagerService(store_path=self.store_path, single_runtime_enforced=True)

    @property
    def ks_store_path(self) -> Path:
        return self.store_path.parent / "kill_switch.json"

    def test_safe_mode_survives_restart(self):
        svc1 = self._svc()
        svc1.execute_kill_switch({
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-dur",
            "actor_id": "op",
        })
        self.assertEqual(svc1.get_safe_mode("pool-dur"), SafeModeState.PAUSED.value)

        svc2 = self._svc()
        self.assertEqual(
            svc2.get_safe_mode("pool-dur"),
            SafeModeState.PAUSED.value,
            "safe-mode state must be readable from a new service instance using the same store",
        )

    def test_audit_log_survives_restart(self):
        svc1 = self._svc()
        svc1.execute_kill_switch({
            "reason": HardTriggerReason.DRAWDOWN_HARD_BREACH.value,
            "capital_pool_id": "pool-dur2",
            "actor_id": "risk-monitor",
        })
        self.assertEqual(len(svc1.get_kill_switch_audit_log()), 1)

        svc2 = self._svc()
        log = svc2.get_kill_switch_audit_log()
        self.assertEqual(
            len(log),
            1,
            "audit log must be readable from a new service instance using the same store",
        )
        self.assertEqual(log[0]["reason"], HardTriggerReason.DRAWDOWN_HARD_BREACH.value)

    def test_safe_mode_advance_survives_restart(self):
        pool = "pool-dur3"
        svc1 = self._svc()
        svc1.execute_kill_switch({
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": pool,
            "actor_id": "op",
        })
        svc1.advance_safe_mode(pool, SafeModeState.RECOVERY_TESTING.value, actor_id="gov")
        self.assertEqual(svc1.get_safe_mode(pool), SafeModeState.RECOVERY_TESTING.value)

        svc2 = self._svc()
        self.assertEqual(
            svc2.get_safe_mode(pool),
            SafeModeState.RECOVERY_TESTING.value,
            "manual safe-mode advance must persist across restarts",
        )
        # Two audit entries: dispatch + manual advance
        self.assertEqual(len(svc2.get_kill_switch_audit_log()), 2)

    def test_corrupt_kill_switch_snapshot_is_quarantined_on_boot(self):
        self.ks_store_path.write_text("{bad json")

        svc = self._svc()

        self.assertEqual(
            svc.get_safe_mode("pool-corrupt"),
            SafeModeState.NORMAL.value,
            "service should start with empty kill-switch state when snapshot is corrupt",
        )
        self.assertTrue(self.ks_store_path.exists(), "clean recovery snapshot should be written after quarantine")
        quarantined = list(self.store_path.parent.glob("kill_switch.json.corrupt.*.json"))
        self.assertEqual(len(quarantined), 1)
        recovery_snapshot = json.loads(self.ks_store_path.read_text())
        self.assertEqual(
            recovery_snapshot["foundation_recovery_audit"][0]["action_type"],
            "foundation.command_recovery.quarantined",
        )

        svc.execute_kill_switch({
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-corrupt",
            "actor_id": "op",
        })
        restored = json.loads(self.ks_store_path.read_text())
        self.assertEqual(
            restored["safe_mode"]["pool-corrupt"],
            SafeModeState.PAUSED.value,
        )

    def test_kill_switch_replay_after_mid_binding_crash_does_not_duplicate_side_effect(self):
        svc1 = self._svc()
        binding = svc1.deploy(_valid_deploy_request(
            capital_pool_id="pool-crash-replay",
            runtime_id="rt-crash-replay",
        ))
        request = {
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-crash-replay",
            "actor_id": "op",
            "binding_id": binding.binding_id,
            "idempotency_key": "idmp-crash-replay-001",
        }
        original_binding_action = svc1._execute_kill_switch_binding_action

        def crash_after_binding(command):
            original_binding_action(command)
            raise RuntimeError("simulated crash before success ledger persist")

        svc1._execute_kill_switch_binding_action = crash_after_binding

        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            svc1.execute_kill_switch(request)

        crash_snapshot = json.loads(self.ks_store_path.read_text())
        crash_entry = crash_snapshot["foundation_idempotency"]["idmp-crash-replay-001"]
        self.assertEqual(crash_entry["idempotency_record"]["status"], "executing")
        self.assertEqual(len(crash_snapshot["audit_log"]), 1)

        svc2 = self._svc()
        replayed = svc2.execute_kill_switch(request)

        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(
            replayed["command"]["command_id"],
            crash_entry["result"]["command"]["command_id"],
        )
        self.assertEqual(len(svc2.get_kill_switch_audit_log()), 1)
        self.assertEqual(svc2.get_safe_mode("pool-crash-replay"), SafeModeState.PAUSED.value)
        self.assertEqual(
            svc2._store.get(binding.binding_id).status,
            "paused",
        )
        recovered_snapshot = json.loads(self.ks_store_path.read_text())
        recovered_entry = recovered_snapshot["foundation_idempotency"]["idmp-crash-replay-001"]
        self.assertEqual(recovered_entry["idempotency_record"]["status"], "succeeded")
        self.assertEqual(
            recovered_snapshot["foundation_recovery_audit"][-1]["action_type"],
            "foundation.command_recovery.replay_resumed",
        )

    def test_kill_switch_replace_replay_after_replacement_create_does_not_duplicate_fallback(self):
        svc1 = self._svc()
        binding = svc1.deploy(_valid_deploy_request(
            capital_pool_id="pool-replace-crash-replay",
            runtime_id="rt-replace-crash-replay",
        ))
        request = {
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-replace-crash-replay",
            "actor_id": "op",
            "binding_id": binding.binding_id,
            "idempotency_key": "idmp-replace-crash-replay-001",
            "action_override": KillSwitchActionType.REPLACE.value,
            "fallback_artifact_id": "artifact-fallback",
            "fallback_artifact_version": "1.0.0",
        }
        original_retire = svc1._store.retire

        def crash_before_old_binding_retire(binding_id, retired_at=None):
            raise RuntimeError("simulated crash after replacement create before retire")

        svc1._store.retire = crash_before_old_binding_retire

        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            svc1.execute_kill_switch(request)

        svc1._store.retire = original_retire
        after_crash = self._svc()
        after_crash_active_fallbacks = [
            item for item in after_crash.list_by_pool("pool-replace-crash-replay")
            if item.status == "active" and item.artifact_id == "artifact-fallback"
        ]
        self.assertEqual(len(after_crash_active_fallbacks), 1)
        self.assertEqual(after_crash.get(binding.binding_id).status, "active")

        replayed = after_crash.execute_kill_switch(request)

        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(replayed["binding_action"]["replacement_binding"]["artifact_id"], "artifact-fallback")
        self.assertEqual(after_crash.get(binding.binding_id).status, "retired")
        after_replay_active_fallbacks = [
            item for item in after_crash.list_by_pool("pool-replace-crash-replay")
            if item.status == "active" and item.artifact_id == "artifact-fallback"
        ]
        self.assertEqual(len(after_replay_active_fallbacks), 1)
        recovered_snapshot = json.loads(self.ks_store_path.read_text())
        recovered_entry = recovered_snapshot["foundation_idempotency"]["idmp-replace-crash-replay-001"]
        self.assertEqual(recovered_entry["idempotency_record"]["status"], "succeeded")

    def test_kill_switch_replace_replay_without_binding_id_recovers_old_binding_from_replacement(self):
        svc1 = self._svc()
        binding = svc1.deploy(_valid_deploy_request(
            capital_pool_id="pool-replace-crash-replay-optional-binding",
            runtime_id="rt-replace-crash-replay-optional-binding",
        ))
        request = {
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-replace-crash-replay-optional-binding",
            "actor_id": "op",
            "idempotency_key": "idmp-replace-crash-replay-optional-binding-001",
            "action_override": KillSwitchActionType.REPLACE.value,
            "fallback_artifact_id": "artifact-fallback",
            "fallback_artifact_version": "1.0.0",
        }
        original_retire = svc1._store.retire

        def crash_before_old_binding_retire(binding_id, retired_at=None):
            raise RuntimeError("simulated crash after replacement create before retire")

        svc1._store.retire = crash_before_old_binding_retire

        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            svc1.execute_kill_switch(request)

        svc1._store.retire = original_retire
        after_crash = self._svc()
        active_bindings = [
            item for item in after_crash.list_by_pool("pool-replace-crash-replay-optional-binding")
            if item.status == "active"
        ]
        self.assertEqual(len(active_bindings), 2)

        crash_snapshot = json.loads(self.ks_store_path.read_text())
        crash_command = crash_snapshot["foundation_idempotency"][
            "idmp-replace-crash-replay-optional-binding-001"
        ]["result"]["command"]
        self.assertNotIn("binding_id", crash_command)

        replayed = after_crash.execute_kill_switch(request)

        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(replayed["binding_action"]["binding"]["binding_id"], binding.binding_id)
        self.assertEqual(after_crash.get(binding.binding_id).status, "retired")
        active_fallbacks = [
            item for item in after_crash.list_by_pool("pool-replace-crash-replay-optional-binding")
            if item.status == "active" and item.artifact_id == "artifact-fallback"
        ]
        self.assertEqual(len(active_fallbacks), 1)
        recovered_snapshot = json.loads(self.ks_store_path.read_text())
        recovered_entry = recovered_snapshot["foundation_idempotency"][
            "idmp-replace-crash-replay-optional-binding-001"
        ]
        self.assertEqual(recovered_entry["idempotency_record"]["status"], "succeeded")
        self.assertEqual(
            recovered_snapshot["foundation_recovery_audit"][-1]["action_type"],
            "foundation.command_recovery.replay_resumed",
        )

    def test_corrupt_foundation_idempotency_entry_is_quarantined_on_boot(self):
        self.ks_store_path.write_text(json.dumps({
            "safe_mode": {"pool-partial": SafeModeState.PAUSED.value},
            "audit_log": [],
            "foundation_idempotency": {
                "bad-entry": {
                    "idempotency_record": {
                        "idempotency_key": "bad-entry",
                        "operation_type": "runtime_manager.kill_switch.dispatch",
                        "target_ref": "CapitalPool:pool-partial",
                        "request_hash": "hash",
                        "first_seen_at": "2026-04-28T00:00:00Z",
                        "last_seen_at": "2026-04-27T00:00:00Z",
                        "status": "reserved",
                        "trace_id": "trace-partial",
                    }
                }
            },
        }))

        svc = self._svc()

        self.assertEqual(svc.get_safe_mode("pool-partial"), SafeModeState.PAUSED.value)
        recovered_snapshot = json.loads(self.ks_store_path.read_text())
        self.assertEqual(recovered_snapshot["foundation_idempotency"], {})
        self.assertEqual(
            recovered_snapshot["foundation_recovery_audit"][-1]["action_type"],
            "foundation.command_recovery.quarantined",
        )


class KillSwitchLatencyBenchmarkTests(unittest.TestCase):
    """Latency benchmark for the kill-switch fast path.

    Verifies that the pure-Python hot path (classify + dispatch with no I/O)
    meets FAST_PATH_LATENCY_TARGET_MS per iteration over FAST_PATH_BENCHMARK_ITERATIONS.

    KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY §8 requires the fast path to be
    measurably lower latency than the normal governance review queue.
    """

    def test_dispatch_hot_path_meets_latency_target(self):
        controller = KillSwitchController()
        trigger = EmergencyTrigger(
            reason=HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            capital_pool_id="pool-bench",
            actor_id="benchmark",
        )

        # Warm up — ensure no first-call JIT penalties skew the measurement.
        for _ in range(10):
            controller.dispatch(trigger)

        # Re-create controller so the warm-up audit entries don't accumulate.
        controller = KillSwitchController()

        start = time.perf_counter()
        for _ in range(FAST_PATH_BENCHMARK_ITERATIONS):
            controller.dispatch(trigger)
        elapsed_s = time.perf_counter() - start

        total_ms = elapsed_s * 1000.0
        per_iter_ms = total_ms / FAST_PATH_BENCHMARK_ITERATIONS
        budget_ms = FAST_PATH_LATENCY_TARGET_MS * FAST_PATH_BENCHMARK_ITERATIONS

        self.assertLessEqual(
            total_ms,
            budget_ms,
            msg=(
                f"Kill-switch fast path too slow: {per_iter_ms:.4f} ms/iter "
                f"(target {FAST_PATH_LATENCY_TARGET_MS} ms/iter, "
                f"total {total_ms:.1f} ms over {FAST_PATH_BENCHMARK_ITERATIONS} iterations)"
            ),
        )

    def test_audit_log_grows_with_each_dispatch(self):
        """Audit entries are accumulated — verify count matches dispatches."""
        controller = KillSwitchController()
        trigger = EmergencyTrigger(
            reason=SoftTriggerReason.CANARY_UNDERPERFORMANCE.value,
            capital_pool_id="pool-audit-bench",
            actor_id="benchmark",
        )
        n = 50
        for _ in range(n):
            controller.dispatch(trigger)
        self.assertEqual(len(controller.audit_log()), n)


if __name__ == "__main__":
    unittest.main()
