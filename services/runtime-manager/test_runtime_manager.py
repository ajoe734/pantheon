"""Unit tests for the runtime-manager service, client, and HTTP surface."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_DIR = Path(__file__).resolve().parent
EXEC_RUNTIME_DIR = REPO_ROOT / "services" / "execution" / "runtime-manager"

os.environ["PANTHEON_EXEC_RUNTIME_MANAGER_DIR"] = str(EXEC_RUNTIME_DIR)

for path in (SERVICE_DIR, EXEC_RUNTIME_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from runtime_manager_client import RuntimeManagerClient, RuntimeManagerClientError
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
        "strategy_id": "strategy-alpha",
        "approval_decision_id": "approval-alpha",
        "sponsor_persona_id": "persona-alpha",
        "capital_pool_id": "pool-001",
        "persona_capital_binding_id": "pcb-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "runtime_id": "rt-001",
    }
    request.update(overrides)
    return request


def _canonical_authority_report(request):
    return {
        "status": "passed",
        "authority": "canonical_deployment_registry_governance_capital",
        "plan_id": request["plan_id"],
        "plan_status": request["plan_status"],
        "target_stage": request["target_stage"],
        "artifact_id": request["artifact_id"],
        "artifact_version": request["artifact_version"],
        "strategy_id": request["strategy_id"],
        "approval_decision_id": request["approval_decision_id"],
        "capital_pool_id": request["capital_pool_id"],
        "sponsor_persona_id": request["sponsor_persona_id"],
        "persona_capital_binding_id": request["persona_capital_binding_id"],
        "persona_capital_binding_status": request[
            "persona_capital_binding_status"
        ],
        "allowed_deployment_scope": request["allowed_deployment_scope"],
        "deployment_plan_sha256": "sha256:" + "0" * 64,
        "registry_entry_sha256": "sha256:" + "1" * 64,
        "approval_decision_sha256": "sha256:" + "2" * 64,
        "capital_pool_sha256": "sha256:" + "3" * 64,
        "capital_admissibility_sha256": "sha256:" + "4" * 64,
        "persona_capital_binding_sha256": "sha256:" + "5" * 64,
    }


def _seed_retired_rollback_target(
    service,
    *,
    old_binding,
    plan_id,
    artifact_id,
    artifact_version,
    strategy_id="strategy-alpha",
    allowed_deployment_scope="paper",
):
    request = _valid_deploy_request(
        plan_id=plan_id,
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        strategy_id=strategy_id,
        capital_pool_id=old_binding.capital_pool_id,
        persona_capital_binding_id=old_binding.persona_capital_binding_id,
        allowed_deployment_scope=allowed_deployment_scope,
        runtime_id=f"rt-prior-{plan_id}",
    )
    request["metadata"] = {
        "strategy_id": strategy_id,
        "authoritative_loader_attestation": _canonical_authority_report(request),
    }
    prior = service.deploy(request, _allow_cutover_bypass=True)
    return service.retire(prior.binding_id)


def _valid_replace_request(current_binding_id, **overrides):
    request = _valid_deploy_request(
        current_binding_id=current_binding_id,
        plan_id="plan-002",
        artifact_id="artifact-beta",
        # A different artifact may legitimately have the same semantic version.
        artifact_version="1.0.0",
    )
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
        self.assertEqual(binding.metadata["persona_id"], "persona-alpha")
        self.assertEqual(binding.metadata["sponsor_persona_id"], "persona-alpha")

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
            ),
            _allow_non_paper_deploy=True,
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
                ),
                _allow_non_paper_deploy=True,
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
                ),
                _allow_non_paper_deploy=True,
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
                ),
                _allow_non_paper_deploy=True,
            )

    def test_ordinary_canary_deploy_rejects_caller_supplied_gate_references(self):
        with self.assertRaisesRegex(RuntimeManagerError, "paper-only"):
            self.service.deploy(
                _valid_deploy_request(
                    plan_id="plan-canary-fake-authority",
                    target_stage="canary",
                    runtime_id="rt-canary-fake-authority",
                    promotion_gate=_valid_activation_gate(),
                )
            )
        self.assertEqual(self.service.list_by_plan("plan-canary-fake-authority"), [])

    def test_ordinary_live_deploy_rejects_even_complete_string_references(self):
        gate = _valid_activation_gate(
            canary_observation_ref="caller-controlled-canary-observation"
        )
        with self.assertRaisesRegex(RuntimeManagerError, "distinct-actor approval"):
            self.service.deploy(
                _valid_deploy_request(
                    plan_id="plan-live-fake-authority",
                    target_stage="live",
                    runtime_id="rt-live-fake-authority",
                    promotion_gate=gate,
                )
            )
        self.assertEqual(self.service.list_by_plan("plan-live-fake-authority"), [])

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

    def test_kill_switch_safe_mode_wins_over_queued_forward_deploy(self):
        pool_id = "pool-kill-wins"
        outcome = self.service.execute_kill_switch(
            {
                "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
                "capital_pool_id": pool_id,
                "actor_id": "operator-kill-wins",
                "action_override": KillSwitchActionType.PAUSE.value,
            }
        )
        self.assertEqual(outcome["safe_mode_after"], SafeModeState.PAUSED.value)

        with self.assertRaisesRegex(RuntimeManagerError, "containment wins"):
            self.service.deploy(
                _valid_deploy_request(
                    plan_id="plan-after-kill",
                    capital_pool_id=pool_id,
                    runtime_id="rt-after-kill",
                )
            )

        self.assertEqual(self.service.list_by_pool(pool_id), [])

    def test_kill_first_forces_paused_replacement_for_every_rollback_action(self):
        for action_type in (
            "replace",
            "pause_then_replace",
            "liquidate_then_replace",
        ):
            with self.subTest(action_type=action_type):
                suffix = action_type.replace("_", "-")
                pool_id = f"pool-kill-rollback-{suffix}"
                original = self.service.deploy(
                    _valid_deploy_request(
                        plan_id=f"plan-before-kill-{suffix}",
                        capital_pool_id=pool_id,
                        runtime_id=f"rt-before-kill-{suffix}",
                    )
                )
                prior = _seed_retired_rollback_target(
                    self.service,
                    old_binding=original,
                    plan_id=f"plan-safe-fallback-{suffix}",
                    artifact_id=f"artifact-safe-fallback-{suffix}",
                    artifact_version="1.0.0",
                )
                self.service.execute_kill_switch(
                    {
                        "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
                        "capital_pool_id": pool_id,
                        "binding_id": original.binding_id,
                        "actor_id": "operator-kill-rollback",
                        "action_override": KillSwitchActionType.PAUSE.value,
                    }
                )

                result = self.service.rollback(
                    {
                        "current_binding_id": original.binding_id,
                        "action_type": action_type,
                        "replacement_plan_id": f"plan-safe-fallback-{suffix}",
                        "replacement_artifact_id": f"artifact-safe-fallback-{suffix}",
                        "replacement_artifact_version": "1.0.0",
                        "replacement_persona_capital_binding_id": "pcb-001",
                        "replacement_allowed_deployment_scope": "paper",
                        "replacement_authority_attestation": prior.metadata[
                            "authoritative_loader_attestation"
                        ],
                    }
                )

                self.assertEqual(result["old_binding"]["status"], "retired")
                self.assertEqual(result["new_binding"]["status"], "paused")
                self.assertIsNone(self.service.get_active_for_pool(pool_id))

    def test_deploy_first_race_is_contained_before_kill_returns(self):
        pool_id = "pool-deploy-first-kill-race"
        create_entered = threading.Event()
        release_create = threading.Event()
        kill_started = threading.Event()
        deploy_result = {}
        kill_result = {}
        failures = []
        real_create = self.service._store.create

        def delayed_create(*args, **kwargs):
            create_entered.set()
            if not release_create.wait(timeout=2):
                raise RuntimeError("test timed out waiting to release binding create")
            return real_create(*args, **kwargs)

        def run_deploy():
            try:
                deploy_result["binding"] = self.service.deploy(
                    _valid_deploy_request(
                        plan_id="plan-deploy-first-race",
                        capital_pool_id=pool_id,
                        runtime_id="rt-deploy-first-race",
                    )
                )
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        def run_kill():
            kill_started.set()
            try:
                kill_result["outcome"] = self.service.execute_kill_switch(
                    {
                        "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
                        "capital_pool_id": pool_id,
                        "actor_id": "operator-race",
                        "action_override": KillSwitchActionType.PAUSE.value,
                    }
                )
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        with mock.patch.object(self.service._store, "create", side_effect=delayed_create):
            deploy_thread = threading.Thread(target=run_deploy)
            deploy_thread.start()
            self.assertTrue(create_entered.wait(timeout=2))

            kill_thread = threading.Thread(target=run_kill)
            kill_thread.start()
            self.assertTrue(kill_started.wait(timeout=2))
            release_create.set()
            deploy_thread.join(timeout=2)
            kill_thread.join(timeout=2)

        self.assertFalse(deploy_thread.is_alive())
        self.assertFalse(kill_thread.is_alive())
        self.assertEqual(failures, [])
        binding_id = deploy_result["binding"].binding_id
        self.assertEqual(self.service.require(binding_id).status, "paused")
        self.assertEqual(
            kill_result["outcome"]["safe_mode_after"], SafeModeState.PAUSED.value
        )

    def test_forward_replace_preserves_runtime_and_records_non_rollback_lineage(self):
        original = self.service.deploy(
            _valid_deploy_request(strategy_id="strategy-alpha")
        )

        result = self.service.replace(
            _valid_replace_request(
                original.binding_id,
                strategy_id="strategy-beta",
                metadata={"promotion_receipt_id": "receipt-001"},
            )
        )

        self.assertEqual(result["operation"], "forward_replace")
        self.assertEqual(result["old_binding"]["status"], "retired")
        self.assertEqual(result["new_binding"]["status"], "active")
        self.assertEqual(result["new_binding"]["runtime_id"], original.runtime_id)
        self.assertEqual(result["new_binding"]["artifact_id"], "artifact-beta")
        self.assertNotIn("rollback_parent", result["new_binding"])
        self.assertNotIn("rollback_action_type", result["new_binding"])
        self.assertEqual(
            result["new_binding"]["metadata"]["replacement_parent_binding_id"],
            original.binding_id,
        )
        self.assertEqual(
            result["new_binding"]["metadata"]["replacement_kind"], "forward"
        )
        self.assertEqual(
            result["new_binding"]["metadata"]["strategy_id"], "strategy-beta"
        )
        self.assertEqual(
            result["position_lineage"]["current_managed_by_binding_id"],
            result["new_binding"]["binding_id"],
        )
        self.assertEqual(
            self.service.get_active_for_pool("pool-001").binding_id,
            result["new_binding"]["binding_id"],
        )

    def test_forward_replace_accepts_paused_current_binding(self):
        original = self.service.deploy(_valid_deploy_request())
        self.service.transition(original.binding_id, "pending_pause")
        self.service.transition(original.binding_id, "paused")

        result = self.service.replace(_valid_replace_request(original.binding_id))

        self.assertEqual(result["old_binding"]["status"], "retired")
        self.assertEqual(result["new_binding"]["status"], "active")

    def test_forward_replace_rejects_mismatched_runtime_without_writes(self):
        original = self.service.deploy(_valid_deploy_request())

        with self.assertRaisesRegex(RuntimeManagerError, "does not match current"):
            self.service.replace(
                _valid_replace_request(original.binding_id, runtime_id="rt-other")
            )

        self.assertEqual(self.service.require(original.binding_id).status, "active")
        self.assertEqual(len(self.service.list_by_pool("pool-001")), 1)

    def test_forward_replace_rejects_identical_artifact_pair(self):
        original = self.service.deploy(_valid_deploy_request())

        with self.assertRaisesRegex(RuntimeManagerError, "pair must differ"):
            self.service.replace(
                _valid_replace_request(
                    original.binding_id,
                    artifact_id=original.artifact_id,
                    artifact_version=original.artifact_version,
                )
            )

        self.assertEqual(self.service.require(original.binding_id).status, "active")

    def test_forward_replace_cutover_failure_leaves_only_source_active(self):
        original = self.service.deploy(_valid_deploy_request())
        request = _valid_replace_request(original.binding_id)

        with mock.patch.object(
            self.service._store,
            "_save",
            side_effect=RuntimeError("injected cutover interruption"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected cutover"):
                self.service.replace(request)

        after_interruption = self.service.list_by_pool("pool-001")
        self.assertEqual(len(after_interruption), 1)
        self.assertEqual(after_interruption[0].binding_id, original.binding_id)
        self.assertEqual(after_interruption[0].status, "active")

        recovered = self.service.replace(request)

        self.assertFalse(recovered["replayed"])
        self.assertEqual(recovered["old_binding"]["status"], "retired")
        self.assertEqual(len(self.service.list_by_pool("pool-001")), 2)
        self.assertEqual(
            self.service.get_active_for_pool("pool-001").binding_id,
            recovered["new_binding"]["binding_id"],
        )

    def test_forward_replace_cutover_is_restart_atomic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "runtime-bindings.json"
            service = RuntimeManagerService(store_path=store_path)
            original = service.deploy(_valid_deploy_request())
            request = _valid_replace_request(original.binding_id)
            real_write = service._store._write_records

            def interrupt_canonical_write(path, records):
                if path == store_path:
                    raise RuntimeError("injected canonical snapshot interruption")
                return real_write(path, records)

            with mock.patch.object(
                service._store,
                "_write_records",
                side_effect=interrupt_canonical_write,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "canonical snapshot interruption"
                ):
                    service.replace(request)

            restarted = RuntimeManagerService(store_path=store_path)
            visible = restarted.list_by_pool("pool-001")
            self.assertEqual(len(visible), 1)
            self.assertEqual(visible[0].binding_id, original.binding_id)
            self.assertEqual(visible[0].status, "active")

    def test_forward_replace_response_loss_restart_replays_exact_child(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "runtime-bindings.json"
            service = RuntimeManagerService(store_path=store_path)
            original = service.deploy(_valid_deploy_request())
            request = _valid_replace_request(original.binding_id)

            first = service.replace(request)
            restarted = RuntimeManagerService(store_path=store_path)
            replay = restarted.replace(request)

            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            self.assertEqual(
                replay["new_binding"]["binding_id"],
                first["new_binding"]["binding_id"],
            )
            self.assertEqual(len(restarted.list_by_pool("pool-001")), 2)
            self.assertEqual(
                restarted.get_active_for_pool("pool-001").binding_id,
                first["new_binding"]["binding_id"],
            )

    def test_rollback_replace_creates_replacement_and_retires_old_binding(self):
        original = self.service.deploy(_valid_deploy_request())

        prior = _seed_retired_rollback_target(
            self.service,
            old_binding=original,
            plan_id="plan-002",
            artifact_id="artifact-beta",
            artifact_version="2.0.0",
        )

        result = self.service.rollback(
            {
                "current_binding_id": original.binding_id,
                "action_type": "replace",
                "replacement_plan_id": "plan-002",
                "replacement_artifact_id": "artifact-beta",
                "replacement_artifact_version": "2.0.0",
                "replacement_persona_capital_binding_id": "pcb-001",
                "replacement_allowed_deployment_scope": "paper",
                "replacement_authority_attestation": prior.metadata[
                    "authoritative_loader_attestation"
                ],
                "replacement_runtime_id": "rt-002",
                "replacement_metadata": {"rollback_receipt_id": "rollback-001"},
                "replacement_strategy_id": "strategy-alpha",
            }
        )

        self.assertEqual(result["action_type"], "replace")
        self.assertEqual(result["old_binding"]["status"], "retired")
        self.assertEqual(result["new_binding"]["status"], "active")
        self.assertEqual(result["new_binding"]["rollback_parent"], original.binding_id)
        self.assertEqual(
            result["new_binding"]["metadata"]["rollback_receipt_id"], "rollback-001"
        )
        self.assertEqual(
            result["new_binding"]["metadata"]["strategy_id"], "strategy-alpha"
        )
        self.assertEqual(
            result["position_lineage"]["current_managed_by_binding_id"],
            result["new_binding"]["binding_id"],
        )
        self.assertEqual(self.service.get_active_for_pool("pool-001").binding_id, result["new_binding"]["binding_id"])

    def test_rollback_response_loss_restart_replays_exact_child(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "runtime-bindings.json"
            service = RuntimeManagerService(store_path=store_path)
            original = service.deploy(_valid_deploy_request())
            prior = _seed_retired_rollback_target(
                service,
                old_binding=original,
                plan_id="plan-restart-fallback",
                artifact_id="artifact-restart-fallback",
                artifact_version="0.9.0",
            )
            request = {
                "current_binding_id": original.binding_id,
                "action_type": "replace",
                "replacement_plan_id": prior.plan_id,
                "replacement_artifact_id": prior.artifact_id,
                "replacement_artifact_version": prior.artifact_version,
                "replacement_persona_capital_binding_id": (
                    prior.persona_capital_binding_id
                ),
                "replacement_allowed_deployment_scope": "paper",
                "replacement_authority_attestation": prior.metadata[
                    "authoritative_loader_attestation"
                ],
                "replacement_runtime_id": "rt-restart-fallback",
                "replacement_strategy_id": "strategy-alpha",
            }

            first = service.rollback(request)
            restarted = RuntimeManagerService(store_path=store_path)
            replay = restarted.rollback(request)

            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            self.assertEqual(
                replay["new_binding"]["binding_id"],
                first["new_binding"]["binding_id"],
            )
            active = [
                binding
                for binding in restarted.list_by_pool("pool-001")
                if binding.status == "active"
            ]
            self.assertEqual(
                [binding.binding_id for binding in active],
                [first["new_binding"]["binding_id"]],
            )

    def test_rollback_rejects_non_paper_source_before_target_resolution(self):
        original = self.service.deploy(
            _valid_deploy_request(
                target_stage="canary",
                promotion_gate=_valid_activation_gate(),
            ),
            _allow_non_paper_deploy=True,
        )

        with self.assertRaisesRegex(RuntimeManagerError, "paper-only"):
            self.service.rollback(
                {
                    "current_binding_id": original.binding_id,
                    "action_type": "replace",
                    "replacement_plan_id": "plan-non-paper-target",
                    "replacement_artifact_id": "artifact-non-paper-target",
                    "replacement_artifact_version": "2.0.0",
                    "replacement_persona_capital_binding_id": "pcb-001",
                    "replacement_allowed_deployment_scope": "canary",
                    "replacement_deployment_mode": "canary",
                }
            )

        self.assertEqual(self.service.require(original.binding_id).status, "active")

    def test_rollback_liquidate_then_replace_start_paused_keeps_old_owner_until_confirmed(self):
        original = self.service.deploy(_valid_deploy_request())

        prior = _seed_retired_rollback_target(
            self.service,
            old_binding=original,
            plan_id="plan-003",
            artifact_id="artifact-gamma",
            artifact_version="3.0.0",
        )

        result = self.service.rollback(
            {
                "current_binding_id": original.binding_id,
                "action_type": "liquidate_then_replace",
                "replacement_plan_id": "plan-003",
                "replacement_artifact_id": "artifact-gamma",
                "replacement_artifact_version": "3.0.0",
                "replacement_persona_capital_binding_id": "pcb-001",
                "replacement_allowed_deployment_scope": "paper",
                "replacement_authority_attestation": prior.metadata[
                    "authoritative_loader_attestation"
                ],
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
        self.assertEqual(result["foundation"]["idempotency_record"]["status"], "executing")
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
        client = RuntimeManagerClient(base_url=None, allow_local=True)

        binding = client.deploy(_valid_deploy_request())
        transitioned = client.transition(binding["binding_id"], "pending_pause")

        self.assertEqual(binding["status"], "active")
        self.assertEqual(transitioned["status"], "pending_pause")
        self.assertIsNone(client.get_active_for_pool("pool-001"))
        self.assertEqual(client.list_by_pool("pool-001")[0]["binding_id"], binding["binding_id"])
        self.assertEqual(client.list_by_plan("plan-001")[0]["binding_id"], binding["binding_id"])

    def test_client_refuses_implicit_local_runtime_fallback(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PANTHEON_RUNTIME_MANAGER_URL", None)
            with self.assertRaisesRegex(
                RuntimeManagerClientError, "PANTHEON_RUNTIME_MANAGER_URL is required"
            ):
                RuntimeManagerClient()

    def test_local_client_dispatches_forward_replace_by_runtime_id(self):
        client = RuntimeManagerClient(base_url=None, allow_local=True)
        original = client.deploy(_valid_deploy_request())

        replaced = client.replace(
            original["runtime_id"],
            _valid_replace_request(original["binding_id"], runtime_id=None),
        )

        self.assertEqual(replaced["operation"], "forward_replace")
        self.assertEqual(replaced["new_binding"]["runtime_id"], original["runtime_id"])
        self.assertEqual(replaced["old_binding"]["status"], "retired")


class RuntimeManagerHttpRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tempdir.name) / "bindings.json"
        self.main = _load_main_module(self.store_path)
        self.authority_patcher = mock.patch.object(
            self.main,
            "verify_deploy_authorities",
            side_effect=lambda request, **_kwargs: _canonical_authority_report(
                request
            ),
        )
        self.authority_patcher.start()
        self.client = self.main.app.test_client()
        self.auth = {"Authorization": "Bearer test-token:operator"}

    def tearDown(self) -> None:
        self.authority_patcher.stop()
        self.tempdir.cleanup()

    def test_deploy_route_uses_authority_report_not_caller_loader_boolean(self):
        body = _valid_deploy_request()
        body["loader_checks_passed"] = False

        response = self.client.post("/api/runtimes/deploy", json=body, headers=self.auth)

        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(
            response.get_json()["metadata"]["authoritative_loader_attestation"],
            _canonical_authority_report(body),
        )

    def test_deploy_route_fails_closed_when_authority_rejects(self):
        self.main.verify_deploy_authorities.side_effect = self.main.DeployAuthorityError(
            "registry artifact is not approved"
        )
        response = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(plan_id="plan-authority-rejected"),
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.get_json()["error"]["code"], "DEPLOY_AUTHORITY_REJECTED"
        )
        self.assertEqual(
            self.main._get_service().list_by_plan("plan-authority-rejected"), []
        )

    def test_deploy_route_retries_when_authority_is_unavailable(self):
        self.main.verify_deploy_authorities.side_effect = (
            self.main.DeployAuthorityUnavailableError("registry unavailable")
        )
        response = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(plan_id="plan-authority-unavailable"),
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["error"]["code"], "DEPLOY_AUTHORITY_UNAVAILABLE"
        )
        self.assertEqual(
            self.main._get_service().list_by_plan("plan-authority-unavailable"), []
        )

    def test_deploy_route_requires_mfa_when_runtime_policy_enables_it(self):
        with mock.patch.dict(
            os.environ, {"PANTHEON_RUNTIME_MFA_REQUIRED": "true"}
        ):
            response = self.client.post(
                "/api/runtimes/deploy",
                json=_valid_deploy_request(plan_id="plan-paper-missing-mfa"),
                headers=self.auth,
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "MFA_REQUIRED")
        self.assertEqual(self.main._get_service().list_by_plan("plan-paper-missing-mfa"), [])

    def test_internal_token_and_fake_gate_cannot_create_live_binding(self):
        response = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(
                plan_id="plan-http-live-fake-gate",
                target_stage="live",
                runtime_id="rt-http-live-fake-gate",
                promotion_gate=_valid_activation_gate(
                    canary_observation_ref="caller-controlled-observation"
                ),
            ),
            headers={"Authorization": "Bearer runtime-control-internal"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("paper-only", response.get_json()["error"]["message"])
        self.assertEqual(
            self.main._get_service().list_by_plan("plan-http-live-fake-gate"), []
        )

    def test_operator_and_fake_gate_cannot_create_canary_binding(self):
        response = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(
                plan_id="plan-http-canary-fake-gate",
                target_stage="canary",
                runtime_id="rt-http-canary-fake-gate",
                promotion_gate=_valid_activation_gate(),
            ),
            headers=self.auth,
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("paper-only", response.get_json()["error"]["message"])
        self.assertEqual(
            self.main._get_service().list_by_plan("plan-http-canary-fake-gate"), []
        )

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
        old_binding = self.main._get_service().require(created["binding_id"])
        _seed_retired_rollback_target(
            self.main._get_service(),
            old_binding=old_binding,
            plan_id="plan-004",
            artifact_id="artifact-delta",
            artifact_version="4.0.0",
        )

        response = self.client.post(
            "/api/rollback",
            json={
                "current_binding_id": created["binding_id"],
                "action_type": "replace",
                "replacement_plan_id": "plan-004",
                "replacement_plan_status": "approved",
                "replacement_artifact_id": "artifact-delta",
                "replacement_artifact_version": "4.0.0",
                "replacement_persona_capital_binding_id": "pcb-001",
                "replacement_allowed_deployment_scope": "paper",
                "replacement_runtime_id": "rt-004",
            },
            headers=self.auth,
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(payload["action_type"], "replace")
        self.assertEqual(payload["old_binding"]["status"], "retired")
        self.assertEqual(payload["new_binding"]["artifact_id"], "artifact-delta")

    def test_rollback_route_rejects_arbitrary_artifact_without_prior_binding(self):
        created = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(plan_id="plan-rollback-source"),
            headers=self.auth,
        ).get_json()

        response = self.client.post(
            "/api/rollback",
            json={
                "current_binding_id": created["binding_id"],
                "action_type": "replace",
                "replacement_plan_id": "plan-never-admitted",
                "replacement_plan_status": "approved",
                "replacement_artifact_id": "artifact-never-admitted",
                "replacement_artifact_version": "9.9.9",
                "replacement_persona_capital_binding_id": "pcb-001",
                "replacement_allowed_deployment_scope": "paper",
            },
            headers=self.auth,
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("exactly one retired prior", response.get_json()["error"]["message"])
        self.assertEqual(
            self.main._get_service().require(created["binding_id"]).status,
            "active",
        )
        self.assertEqual(len(self.main._get_service().list_all()), 1)

    def test_rollback_route_rechecks_current_capital_authority_before_mutation(self):
        created = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(plan_id="plan-rollback-recheck-source"),
            headers=self.auth,
        ).get_json()
        _seed_retired_rollback_target(
            self.main._get_service(),
            old_binding=self.main._get_service().require(created["binding_id"]),
            plan_id="plan-rollback-recheck-target",
            artifact_id="artifact-rollback-recheck-target",
            artifact_version="2.0.0",
        )
        self.main.verify_deploy_authorities.side_effect = (
            self.main.DeployAuthorityError("PersonaCapitalBinding is revoked")
        )

        response = self.client.post(
            "/api/rollback",
            json={
                "current_binding_id": created["binding_id"],
                "action_type": "replace",
                "replacement_plan_id": "plan-rollback-recheck-target",
                "replacement_plan_status": "executed",
                "replacement_artifact_id": "artifact-rollback-recheck-target",
                "replacement_artifact_version": "2.0.0",
                "replacement_persona_capital_binding_id": "pcb-001",
                "replacement_allowed_deployment_scope": "paper",
            },
            headers=self.auth,
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.get_json()["error"]["code"], "DEPLOY_AUTHORITY_REJECTED"
        )
        self.assertEqual(
            self.main._get_service().require(created["binding_id"]).status,
            "active",
        )
        self.assertEqual(len(self.main._get_service().list_all()), 2)

    def test_forward_replace_route_preserves_path_runtime_and_lineage_boundary(self):
        created = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(),
            headers=self.auth,
        ).get_json()

        response = self.client.post(
            f"/api/runtimes/{created['runtime_id']}/replace",
            json=_valid_replace_request(created["binding_id"], runtime_id=None),
            headers=self.auth,
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 201, payload)
        self.assertEqual(payload["operation"], "forward_replace")
        self.assertEqual(payload["new_binding"]["runtime_id"], created["runtime_id"])
        self.assertEqual(payload["old_binding"]["status"], "retired")
        self.assertNotIn("rollback_parent", payload["new_binding"])
        self.assertEqual(
            payload["new_binding"]["metadata"]["replacement_parent_binding_id"],
            created["binding_id"],
        )

        history = self.client.get("/api/rollback/history", headers=self.auth).get_json()
        self.assertEqual(history["count"], 0)

    def test_forward_replace_route_rejects_body_path_runtime_mismatch(self):
        created = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(),
            headers=self.auth,
        ).get_json()

        response = self.client.post(
            f"/api/runtimes/{created['runtime_id']}/replace",
            json=_valid_replace_request(
                created["binding_id"], runtime_id="rt-conflicting-body"
            ),
            headers=self.auth,
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error"]["code"], "PRECONDITION_FAILED")
        current = self.client.get(
            f"/api/runtime-bindings/{created['binding_id']}", headers=self.auth
        ).get_json()
        self.assertEqual(current["status"], "active")

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

        _seed_retired_rollback_target(
            self.main._get_service(),
            old_binding=self.main._get_service().require(created["binding_id"]),
            plan_id="plan-rt004-002",
            artifact_id="artifact-rt004-replacement",
            artifact_version="2.0.0",
        )

        replace_response = self.client.post(
            "/api/rollback",
            json={
                "current_binding_id": created["binding_id"],
                "action_type": "replace",
                "replacement_plan_id": "plan-rt004-002",
                "replacement_plan_status": "approved",
                "replacement_artifact_id": "artifact-rt004-replacement",
                "replacement_artifact_version": "2.0.0",
                "replacement_persona_capital_binding_id": "pcb-001",
                "replacement_allowed_deployment_scope": "paper",
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
        fleet_metadata = {
            "strategy_id": "strategy-alpha",
            "symbol": "2330.TW",
            "market_data_policy": {
                "owner": "source-ingest",
                "contract": "latest_stored_normalized",
            },
            "object_store": {
                "openclaw/registry/strategy-alpha/1.0.0/metadata.json": {
                    "registry_id": "artifact-alpha",
                    "strategy_id": "strategy-alpha",
                    "version": "1.0.0",
                    "checksum": "sha256:" + "a" * 64,
                }
            },
        }
        paper = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(
                plan_id="plan-fleet-paper",
                capital_pool_id="pool-fleet-paper",
                runtime_id="rt-fleet-paper",
                metadata=fleet_metadata,
            ),
            headers=self.auth,
        ).get_json()
        canary = self.main._get_service().deploy(
            _valid_deploy_request(
                plan_id="plan-fleet-canary",
                target_stage="canary",
                capital_pool_id="pool-fleet-canary",
                runtime_id="rt-fleet-canary",
                promotion_gate=_valid_activation_gate(),
                metadata=fleet_metadata,
            ),
            _allow_non_paper_deploy=True,
        ).to_dict()
        paused = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(
                plan_id="plan-fleet-paused",
                capital_pool_id="pool-fleet-paused",
                runtime_id="rt-fleet-paused",
                metadata=fleet_metadata,
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

    def test_execute_kill_switch_acknowledges_already_paused_binding(self):
        binding = self._deploy_active_binding()
        self.service.transition(binding.binding_id, "pending_pause")
        self.service.transition(binding.binding_id, "paused")

        result = self.service.execute_kill_switch({
            "reason": HardTriggerReason.SEVERITY_1_INCIDENT.value,
            "capital_pool_id": "pool-ks-svc",
            "actor_id": "deployment-outbox-consumer",
            "binding_id": binding.binding_id,
            "action_override": "pause",
            "idempotency_key": "compensation-paused-binding-001",
        })

        self.assertEqual(result["binding_action"]["binding"]["status"], "paused")
        self.assertEqual(result["safe_mode_after"], SafeModeState.PAUSED.value)
        self.assertEqual(result["telemetry_ack"]["ack_status"], "acknowledged")
        self.assertTrue(result["telemetry_ack"]["ack_received"])

    def test_kill_safe_mode_blocks_direct_binding_reactivation(self):
        binding = self._deploy_active_binding(pool_id="pool-no-reactivate")
        self.service.execute_kill_switch(
            {
                "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
                "capital_pool_id": "pool-no-reactivate",
                "actor_id": "operator-1",
                "binding_id": binding.binding_id,
            }
        )

        with self.assertRaisesRegex(RuntimeManagerError, "activation is blocked"):
            self.service.transition(binding.binding_id, "active")

        self.assertEqual(self.service.require(binding.binding_id).status, "paused")
        self.assertEqual(
            self.service.get_safe_mode("pool-no-reactivate"),
            SafeModeState.PAUSED.value,
        )

    def test_stale_kill_resolves_sole_paused_rollback_replacement(self):
        original = self._deploy_active_binding(pool_id="pool-paused-child")
        prior = _seed_retired_rollback_target(
            self.service,
            old_binding=original,
            plan_id="plan-paused-child",
            artifact_id="artifact-paused-child",
            artifact_version="2.0.0",
        )
        self.service.execute_kill_switch(
            {
                "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
                "capital_pool_id": "pool-paused-child",
                "actor_id": "operator-first-kill",
                "binding_id": original.binding_id,
                "idempotency_key": "kill-paused-child-first",
            }
        )
        rollback = self.service.rollback(
            {
                "current_binding_id": original.binding_id,
                "action_type": "replace",
                "replacement_plan_id": "plan-paused-child",
                "replacement_artifact_id": "artifact-paused-child",
                "replacement_artifact_version": "2.0.0",
                "replacement_persona_capital_binding_id": "pcb-001",
                "replacement_allowed_deployment_scope": "paper",
                "replacement_authority_attestation": prior.metadata[
                    "authoritative_loader_attestation"
                ],
            }
        )
        child_id = rollback["new_binding"]["binding_id"]
        self.assertEqual(rollback["new_binding"]["status"], "paused")
        self.assertEqual(rollback["old_binding"]["status"], "retired")

        replay = self.service.execute_kill_switch(
            {
                "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
                "capital_pool_id": "pool-paused-child",
                "actor_id": "operator-stale-kill",
                "binding_id": original.binding_id,
                "action_override": KillSwitchActionType.PAUSE.value,
                "idempotency_key": "kill-paused-child-stale",
            }
        )

        self.assertEqual(replay["telemetry_ack"]["ack_status"], "acknowledged")
        self.assertEqual(replay["telemetry_ack"]["runtime_binding_id"], child_id)
        self.assertEqual(self.service.require(child_id).status, "paused")

    def test_rollback_first_then_stale_binding_kill_contains_current_owner(self):
        original = self._deploy_active_binding(pool_id="pool-stale-kill")
        prior = _seed_retired_rollback_target(
            self.service,
            old_binding=original,
            plan_id="plan-before-stale-kill",
            artifact_id="artifact-before-stale-kill",
            artifact_version="2.0.0",
        )
        rollback = self.service.rollback(
            {
                "current_binding_id": original.binding_id,
                "action_type": "replace",
                "replacement_plan_id": "plan-before-stale-kill",
                "replacement_artifact_id": "artifact-before-stale-kill",
                "replacement_artifact_version": "2.0.0",
                "replacement_persona_capital_binding_id": "pcb-001",
                "replacement_allowed_deployment_scope": "paper",
                "replacement_authority_attestation": prior.metadata[
                    "authoritative_loader_attestation"
                ],
            }
        )
        replacement_id = rollback["new_binding"]["binding_id"]
        self.assertEqual(rollback["old_binding"]["status"], "retired")
        self.assertEqual(rollback["new_binding"]["status"], "active")

        result = self.service.execute_kill_switch(
            {
                "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
                "capital_pool_id": "pool-stale-kill",
                # The command was queued before rollback and names the retired
                # lineage record rather than the new pool owner.
                "binding_id": original.binding_id,
                "actor_id": "operator-stale-kill",
                "action_override": KillSwitchActionType.PAUSE.value,
                "idempotency_key": "kill-after-rollback-stale-binding",
            }
        )

        self.assertEqual(result["telemetry_ack"]["ack_status"], "acknowledged")
        self.assertEqual(result["telemetry_ack"]["runtime_binding_id"], replacement_id)
        self.assertEqual(self.service.require(replacement_id).status, "paused")
        self.assertIsNone(self.service.get_active_for_pool("pool-stale-kill"))

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
            "idempotency_key": "kill-without-active-runtime",
        })

        self.assertIsNone(result["binding_action"])
        self.assertEqual(result["safe_mode_after"], SafeModeState.PAUSED.value)
        self.assertEqual(result["telemetry_ack"]["ack_status"], "fail_closed")
        self.assertTrue(result["telemetry_ack"]["fail_closed"])
        self.assertFalse(result["telemetry_ack"]["runtime_state_recorded"])
        self.assertFalse(result["telemetry_ack"]["capital_state_recorded"])
        self.assertEqual(
            result["foundation"]["idempotency_record"]["status"], "executing"
        )

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
        self.authority_patcher = mock.patch.object(
            self.main,
            "verify_deploy_authorities",
            return_value={
                "status": "passed",
                "authority": "test-canonical",
                "persona_capital_binding_status": "active",
                "allowed_deployment_scope": "live",
            },
        )
        self.authority_patcher.start()
        self.client = self.main.app.test_client()
        self.auth = {"Authorization": "Bearer test-token:operator"}

    def tearDown(self) -> None:
        self.authority_patcher.stop()
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

    def test_kill_switch_pre_action_crash_is_contained_during_startup(self):
        svc1 = self._svc()
        binding = svc1.deploy(_valid_deploy_request(
            capital_pool_id="pool-pre-action-crash",
            runtime_id="rt-pre-action-crash",
        ))
        request = {
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-pre-action-crash",
            "actor_id": "op",
            "binding_id": binding.binding_id,
            "idempotency_key": "idmp-pre-action-crash-001",
        }

        svc1._execute_kill_switch_binding_action = mock.Mock(
            side_effect=RuntimeError("simulated crash before binding action")
        )
        with self.assertRaisesRegex(RuntimeError, "before binding action"):
            svc1.execute_kill_switch(request)

        crash_snapshot = json.loads(self.ks_store_path.read_text())
        crash_entry = crash_snapshot["foundation_idempotency"][
            "idmp-pre-action-crash-001"
        ]
        crash_foundation = crash_entry["result"]["foundation"]
        self.assertEqual(crash_entry["idempotency_record"]["status"], "executing")
        self.assertEqual(svc1.get(binding.binding_id).status, "active")

        svc2 = self._svc()

        self.assertEqual(svc2.get(binding.binding_id).status, "paused")
        recovered_snapshot = json.loads(self.ks_store_path.read_text())
        recovered_entry = recovered_snapshot["foundation_idempotency"][
            "idmp-pre-action-crash-001"
        ]
        recovered_foundation = recovered_entry["result"]["foundation"]
        self.assertEqual(recovered_entry["idempotency_record"]["status"], "succeeded")
        self.assertTrue(recovered_entry["result"]["telemetry_ack"]["ack_received"])
        self.assertEqual(len(recovered_snapshot["audit_log"]), 1)
        self.assertEqual(
            recovered_snapshot["foundation_recovery_audit"][-1]["action_type"],
            "foundation.command_recovery.replay_resumed",
        )
        for section, identity_field in (
            ("trace_context", "trace_id"),
            ("command_envelope", "command_id"),
            ("policy_decision", "decision_id"),
            ("audit_action", "action_id"),
        ):
            self.assertEqual(
                recovered_foundation[section][identity_field],
                crash_foundation[section][identity_field],
            )

    def test_kill_switch_post_terminate_crash_recovers_terminal_receipt(self):
        svc1 = self._svc()
        binding = svc1.deploy(_valid_deploy_request(
            capital_pool_id="pool-post-terminate-crash",
            runtime_id="rt-post-terminate-crash",
        ))
        request = {
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-post-terminate-crash",
            "actor_id": "op",
            "binding_id": binding.binding_id,
            "action_override": KillSwitchActionType.TERMINATE.value,
            "idempotency_key": "idmp-post-terminate-crash-001",
        }
        original_action = svc1._execute_kill_switch_binding_action

        def crash_after_terminate(command):
            original_action(command)
            raise RuntimeError("simulated crash after terminate")

        svc1._execute_kill_switch_binding_action = crash_after_terminate
        with self.assertRaisesRegex(RuntimeError, "after terminate"):
            svc1.execute_kill_switch(request)

        svc2 = self._svc()

        self.assertEqual(svc2.get(binding.binding_id).status, "retired")
        recovered_snapshot = json.loads(self.ks_store_path.read_text())
        recovered_entry = recovered_snapshot["foundation_idempotency"][
            "idmp-post-terminate-crash-001"
        ]
        self.assertEqual(recovered_entry["idempotency_record"]["status"], "succeeded")
        self.assertTrue(
            recovered_entry["result"]["binding_action"]["already_contained"]
        )
        self.assertTrue(recovered_entry["result"]["telemetry_ack"]["ack_received"])

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

    def test_kill_switch_replace_request_replays_fail_closed_pause_without_fallback(self):
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
        original_action = svc1._execute_kill_switch_binding_action

        def crash_after_pause(command):
            original_action(command)
            raise RuntimeError("simulated crash after fail-closed pause")

        svc1._execute_kill_switch_binding_action = crash_after_pause

        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            svc1.execute_kill_switch(request)

        after_crash = self._svc()
        self.assertEqual(after_crash.get(binding.binding_id).status, "paused")
        self.assertEqual(len(after_crash.list_by_pool("pool-replace-crash-replay")), 1)

        replayed = after_crash.execute_kill_switch(request)

        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(replayed["command"]["action_type"], "pause")
        self.assertEqual(
            replayed["binding_action"]["binding"]["binding_id"],
            binding.binding_id,
        )
        self.assertEqual(after_crash.get(binding.binding_id).status, "paused")
        self.assertEqual(len(after_crash.list_by_pool("pool-replace-crash-replay")), 1)
        recovered_snapshot = json.loads(self.ks_store_path.read_text())
        recovered_entry = recovered_snapshot["foundation_idempotency"]["idmp-replace-crash-replay-001"]
        self.assertEqual(recovered_entry["idempotency_record"]["status"], "succeeded")

    def test_kill_switch_replace_without_binding_id_pauses_only_pool_owner(self):
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
        result = svc1.execute_kill_switch(request)

        self.assertEqual(result["command"]["action_type"], "pause")
        self.assertNotIn("binding_id", result["command"])
        self.assertEqual(
            result["command"]["metadata"]["requested_action"], "replace"
        )
        self.assertEqual(
            result["binding_action"]["binding"]["binding_id"], binding.binding_id
        )
        self.assertEqual(svc1.get(binding.binding_id).status, "paused")
        self.assertEqual(
            len(svc1.list_by_pool("pool-replace-crash-replay-optional-binding")),
            1,
        )

        replayed = svc1.execute_kill_switch(request)
        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(replayed["command"]["command_id"], result["command"]["command_id"])

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
