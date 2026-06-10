import unittest

from services.execution.lean_runtime.bootstrap_contract import (
    PANTHEON_LEAN_REMOTE,
    PANTHEON_LEAN_SOURCE_PATH,
    BootstrapContractError,
    materialize_runtime_bootstrap_request,
)


def _risk_policy_evaluation(
    *,
    decision: str = "allowed",
    capital_pool_id: str = "pool-paper-001",
) -> dict:
    return {
        "risk_policy_id": "risk-policy-paper-001",
        "risk_policy_version": "v1",
        "capital_pool_id": capital_pool_id,
        "target_type": "runtime_launch",
        "target_id": "dp-paper-001",
        "decision": decision,
        "checks": [],
        "blocking_reasons": [] if decision != "rejected" else ["blocked by policy"],
        "warnings": [],
        "evaluated_at": "2026-06-09T00:00:00Z",
        "trace_id": "trace-risk-policy-paper-001",
    }


def _deployment_plan(**overrides):
    plan = {
        "plan_id": "dp-paper-001",
        "approval_decision_id": "appr-001",
        "artifact_id": "art-alpha",
        "artifact_version": "1.0.0",
        "artifact_state": "approved",
        "artifact_checksum": "sha256:alpha",
        "strategy_id": "strat-alpha",
        "capital_pool_id": "pool-paper-001",
        "target_stage": "paper",
        "runtime_role": "paper",
        "runtime_config_ref": "/workspace/lean/Launcher/config.json",
        "runtime_config_status": "approved",
        "risk_policy_ref": "risk-policy-paper-001",
        "risk_policy_evaluation": _risk_policy_evaluation(),
    }
    plan.update(overrides)
    return plan


def _runtime_binding(**overrides):
    binding = {
        "binding_id": "rtb-paper-001",
        "runtime_id": "rt-paper-001",
        "plan_id": "dp-paper-001",
        "artifact_id": "art-alpha",
        "artifact_version": "1.0.0",
        "capital_pool_id": "pool-paper-001",
        "deployment_mode": "paper",
        "persona_capital_binding_id": "pcb-paper-001",
        "metadata": {
            "engine_bridge_repo": PANTHEON_LEAN_REMOTE,
            "engine_bridge_path": PANTHEON_LEAN_SOURCE_PATH,
            "engine_bridge_commit": "abc1234",
        },
    }
    binding.update(overrides)
    return binding


class RuntimeBootstrapContractTests(unittest.TestCase):
    def test_materialize_bootstrap_request_from_deployment_plan(self):
        request = materialize_runtime_bootstrap_request(
            deployment_plan=_deployment_plan(),
            runtime_binding=_runtime_binding(),
            request_id="rbr-test-001",
            trace_id="trace-test-001",
        )

        payload = request.to_dict()

        self.assertEqual(payload["request_id"], "rbr-test-001")
        self.assertEqual(payload["trace_id"], "trace-test-001")
        self.assertEqual(payload["runtime_binding_id"], "rtb-paper-001")
        self.assertEqual(payload["deployment_plan_id"], "dp-paper-001")
        self.assertEqual(payload["deployment_stage"], "paper")
        self.assertEqual(payload["runtime_role"], "paper")
        self.assertEqual(payload["runtime_id"], "rt-paper-001")
        self.assertEqual(payload["artifact"]["artifact_id"], "art-alpha")
        self.assertEqual(payload["artifact"]["artifact_version"], "1.0.0")
        self.assertEqual(payload["artifact"]["checksum"], "sha256:alpha")
        self.assertEqual(payload["artifact"]["strategy_id"], "strat-alpha")
        self.assertEqual(payload["capital"]["capital_pool_id"], "pool-paper-001")
        self.assertEqual(payload["capital"]["persona_capital_binding_id"], "pcb-paper-001")
        self.assertEqual(payload["bridge"]["remote"], PANTHEON_LEAN_REMOTE)
        self.assertEqual(payload["bridge"]["source_path"], PANTHEON_LEAN_SOURCE_PATH)
        self.assertEqual(payload["bridge"]["commit"], "abc1234")
        self.assertTrue(payload["runtime_config"]["paper_mode"])
        self.assertFalse(payload["runtime_config"]["live_broker_enabled"])
        self.assertFalse(payload["runtime_config"]["health_only"])
        self.assertEqual(payload["metadata"]["launch_contract"]["artifact_state"], "approved")
        self.assertEqual(payload["metadata"]["launch_contract"]["runtime_config_status"], "approved")
        self.assertEqual(
            payload["metadata"]["launch_contract"]["risk_policy_ref"],
            "risk-policy-paper-001",
        )

        env = request.to_runtime_env()
        self.assertEqual(env["PANTHEON_RUNTIME_BINDING_ID"], "rtb-paper-001")
        self.assertEqual(env["PANTHEON_DEPLOYMENT_PLAN_ID"], "dp-paper-001")
        self.assertEqual(env["PANTHEON_ENGINE_BRIDGE_COMMIT"], "abc1234")
        self.assertNotIn("PANTHEON_BROKER_SECRET", env)
        self.assertNotIn("PANTHEON_RUNTIME_MANAGER_TOKEN", env)

    def test_bootstrap_request_requires_runtime_binding_id(self):
        with self.assertRaisesRegex(BootstrapContractError, "runtime_binding_id"):
            materialize_runtime_bootstrap_request(
                deployment_plan=_deployment_plan(),
                runtime_binding=_runtime_binding(binding_id=""),
            )

    def test_bootstrap_request_requires_deployment_plan_id(self):
        with self.assertRaisesRegex(BootstrapContractError, "deployment_plan_id"):
            materialize_runtime_bootstrap_request(
                deployment_plan=_deployment_plan(plan_id=""),
                runtime_binding=_runtime_binding(plan_id=""),
            )

    def test_bootstrap_request_rejects_lean_platform_target(self):
        with self.assertRaisesRegex(BootstrapContractError, "lean-platform"):
            materialize_runtime_bootstrap_request(
                deployment_plan=_deployment_plan(),
                runtime_binding=_runtime_binding(
                    metadata={
                        "engine_bridge_repo": "ajoe734/lean-platform.git",
                        "engine_bridge_path": PANTHEON_LEAN_SOURCE_PATH,
                        "engine_bridge_commit": "abc1234",
                    }
                ),
            )

    def test_bootstrap_request_rejects_raw_secrets(self):
        with self.assertRaisesRegex(BootstrapContractError, "broker_secret"):
            materialize_runtime_bootstrap_request(
                deployment_plan=_deployment_plan(),
                runtime_binding=_runtime_binding(
                    metadata={
                        "engine_bridge_repo": PANTHEON_LEAN_REMOTE,
                        "engine_bridge_path": PANTHEON_LEAN_SOURCE_PATH,
                        "engine_bridge_commit": "abc1234",
                        "broker_secret": "plain-secret-value",
                    }
                ),
            )

        with self.assertRaisesRegex(BootstrapContractError, "api_key"):
            materialize_runtime_bootstrap_request(
                deployment_plan=_deployment_plan(metadata={"api_key": "raw-api-key"}),
                runtime_binding=_runtime_binding(),
            )

    def test_bootstrap_request_rejects_unapproved_artifact(self):
        with self.assertRaisesRegex(BootstrapContractError, "approved artifact"):
            materialize_runtime_bootstrap_request(
                deployment_plan=_deployment_plan(artifact_state="candidate"),
                runtime_binding=_runtime_binding(),
            )

    def test_bootstrap_request_rejects_unapproved_runtime_config(self):
        with self.assertRaisesRegex(BootstrapContractError, "approved runtime config"):
            materialize_runtime_bootstrap_request(
                deployment_plan=_deployment_plan(runtime_config_status="draft"),
                runtime_binding=_runtime_binding(),
            )

    def test_bootstrap_request_rejects_rejected_pool_risk_policy(self):
        with self.assertRaisesRegex(BootstrapContractError, "risk_policy_evaluation"):
            materialize_runtime_bootstrap_request(
                deployment_plan=_deployment_plan(
                    risk_policy_evaluation=_risk_policy_evaluation(decision="rejected")
                ),
                runtime_binding=_runtime_binding(),
            )

    def test_bootstrap_request_rejects_cross_pool_risk_policy_evidence(self):
        with self.assertRaisesRegex(BootstrapContractError, "capital_pool_id"):
            materialize_runtime_bootstrap_request(
                deployment_plan=_deployment_plan(
                    risk_policy_evaluation=_risk_policy_evaluation(capital_pool_id="pool-other")
                ),
                runtime_binding=_runtime_binding(),
            )

    def test_materializer_accepts_projection_metadata_checksum(self):
        plan = _deployment_plan(metadata={"checksum": "sha256:projection"})
        plan.pop("artifact_checksum")

        request = materialize_runtime_bootstrap_request(
            deployment_plan=plan,
            runtime_binding=_runtime_binding(),
        )

        self.assertEqual(request.artifact.checksum, "sha256:projection")

    def test_live_role_defaults_to_health_only(self):
        request = materialize_runtime_bootstrap_request(
            deployment_plan=_deployment_plan(target_stage="live", runtime_role="live"),
            runtime_binding=_runtime_binding(deployment_mode="live"),
        )

        self.assertEqual(request.deployment_stage, "live")
        self.assertEqual(request.runtime_role, "live")
        self.assertFalse(request.runtime_config.paper_mode)
        self.assertTrue(request.runtime_config.health_only)
        self.assertFalse(request.runtime_config.live_broker_enabled)
        self.assertEqual(request.to_runtime_env()["PANTHEON_HEALTH_ONLY"], "true")

    def test_live_broker_activation_flag_is_rejected(self):
        with self.assertRaisesRegex(BootstrapContractError, "live_broker_enabled"):
            materialize_runtime_bootstrap_request(
                deployment_plan=_deployment_plan(
                    target_stage="live",
                    runtime_role="live",
                    runtime_config={"live_broker_enabled": True},
                ),
                runtime_binding=_runtime_binding(deployment_mode="live"),
            )


if __name__ == "__main__":
    unittest.main()
