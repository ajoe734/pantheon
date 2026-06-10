from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.execution.lean_runtime.bootstrap_contract import (
    PANTHEON_LEAN_REMOTE,
    PANTHEON_LEAN_SOURCE_PATH,
    PANTHEON_LEAN_RUNTIME_PATH,
    materialize_runtime_bootstrap_request,
)
from services.execution.lean_runtime.runtime_context import (
    PantheonRuntimeContext,
    RuntimeContextError,
    RuntimeContextSource,
)


def _manifest(**overrides):
    payload = {
        "request_id": "rbr-paper-001",
        "trace_id": "trace-paper-001",
        "runtime_binding_id": "rtb-paper-001",
        "runtime_id": "rt-paper-001",
        "deployment_plan_id": "dp-paper-001",
        "deployment_stage": "paper",
        "runtime_role": "paper",
        "artifact": {
            "artifact_id": "art-alpha",
            "artifact_version": "1.0.0",
            "artifact_checksum": "sha256:alpha",
            "strategy_id": "strat-alpha",
        },
        "capital": {
            "capital_pool_id": "pool-paper-001",
            "persona_capital_binding_id": "pcb-paper-001",
        },
        "bridge": {
            "repo": PANTHEON_LEAN_REMOTE,
            "path": PANTHEON_LEAN_SOURCE_PATH,
            "commit": "abc1234",
            "runtime_adapter_version": "0.1.0",
        },
    }
    payload.update(overrides)
    return payload


def _env(**overrides):
    payload = {
        "PANTHEON_RUNTIME_BINDING_ID": "rtb-paper-001",
        "PANTHEON_RUNTIME_ID": "rt-paper-001",
        "PANTHEON_DEPLOYMENT_PLAN_ID": "dp-paper-001",
        "PANTHEON_DEPLOYMENT_STAGE": "paper",
        "PANTHEON_RUNTIME_ROLE": "paper",
        "PANTHEON_ARTIFACT_ID": "art-alpha",
        "PANTHEON_ARTIFACT_VERSION": "1.0.0",
        "PANTHEON_ARTIFACT_CHECKSUM": "sha256:alpha",
        "PANTHEON_STRATEGY_ID": "strat-alpha",
        "PANTHEON_CAPITAL_POOL_ID": "pool-paper-001",
        "PANTHEON_PERSONA_CAPITAL_BINDING_ID": "pcb-paper-001",
        "PANTHEON_ENGINE_BRIDGE_REMOTE": PANTHEON_LEAN_REMOTE,
        "PANTHEON_ENGINE_BRIDGE_SOURCE_PATH": PANTHEON_LEAN_SOURCE_PATH,
        "PANTHEON_ENGINE_BRIDGE_COMMIT": "abc1234",
        "PANTHEON_TRACE_ID": "trace-paper-001",
        "PANTHEON_REQUEST_ID": "rbr-paper-001",
    }
    payload.update(overrides)
    return payload


def _deployment_plan(**overrides):
    payload = {
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
        "risk_policy_evaluation": {
            "risk_policy_id": "risk-policy-paper-001",
            "risk_policy_version": "v1",
            "capital_pool_id": "pool-paper-001",
            "target_type": "runtime_launch",
            "target_id": "dp-paper-001",
            "decision": "allowed",
            "checks": [],
            "blocking_reasons": [],
            "warnings": [],
            "evaluated_at": "2026-06-09T00:00:00Z",
            "trace_id": "trace-risk-policy-paper-001",
        },
    }
    payload.update(overrides)
    return payload


def _runtime_binding(**overrides):
    payload = {
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
    payload.update(overrides)
    return payload


class PantheonRuntimeContextTests(unittest.TestCase):
    def test_runtime_context_loads_from_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "launch-manifest.json"
            manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")

            context = PantheonRuntimeContext.from_manifest(manifest_path, expected_stage="paper")

        self.assertEqual(context.context_source, RuntimeContextSource.LAUNCH_MANIFEST)
        self.assertEqual(context.runtime_binding_id, "rtb-paper-001")
        self.assertEqual(context.deployment_plan_id, "dp-paper-001")
        self.assertEqual(context.artifact.artifact_id, "art-alpha")
        self.assertEqual(context.artifact.artifact_checksum, "sha256:alpha")
        self.assertEqual(context.capital.capital_pool_id, "pool-paper-001")
        self.assertEqual(context.bridge.repo, PANTHEON_LEAN_REMOTE)
        self.assertEqual(context.bridge.path, PANTHEON_LEAN_SOURCE_PATH)
        self.assertEqual(context.bridge.commit, "abc1234")
        self.assertEqual(context.trace.trace_id, "trace-paper-001")
        self.assertEqual(context.trace.correlation_id, "rbr-paper-001")

    def test_runtime_context_loads_from_bootstrap_request_manifest(self):
        request = materialize_runtime_bootstrap_request(
            deployment_plan=_deployment_plan(),
            runtime_binding=_runtime_binding(),
            request_id="rbr-test-001",
            trace_id="trace-test-001",
        )

        context = PantheonRuntimeContext.from_mapping(
            request.to_dict(),
            source=RuntimeContextSource.LAUNCH_MANIFEST,
            expected_stage="paper",
        )

        self.assertEqual(request.bridge.path, PANTHEON_LEAN_RUNTIME_PATH)
        self.assertEqual(request.bridge.source_path, PANTHEON_LEAN_SOURCE_PATH)
        self.assertEqual(context.bridge.path, PANTHEON_LEAN_SOURCE_PATH)
        self.assertEqual(context.bridge.repo, PANTHEON_LEAN_REMOTE)
        self.assertEqual(context.trace.trace_id, "trace-test-001")
        self.assertEqual(context.trace.correlation_id, "rbr-test-001")

    def test_runtime_context_loads_from_env_for_dev_smoke(self):
        context = PantheonRuntimeContext.from_env(_env(), expected_stage="paper")

        self.assertEqual(context.context_source, RuntimeContextSource.ENV_VARS)
        self.assertEqual(context.runtime_id, "rt-paper-001")
        self.assertEqual(context.runtime_role, "paper")
        self.assertEqual(context.artifact.strategy_id, "strat-alpha")
        self.assertEqual(context.capital.persona_capital_binding_id, "pcb-paper-001")

    def test_runtime_context_rejects_missing_binding_in_managed_runtime(self):
        with self.assertRaisesRegex(RuntimeContextError, "runtime_binding_id"):
            PantheonRuntimeContext.from_env(_env(PANTHEON_RUNTIME_BINDING_ID=""))

    def test_runtime_context_rejects_stage_mismatch(self):
        with self.assertRaisesRegex(RuntimeContextError, "does not match expected stage"):
            PantheonRuntimeContext.from_mapping(
                _manifest(deployment_stage="paper"),
                source=RuntimeContextSource.LAUNCH_MANIFEST,
                expected_stage="live",
            )

    def test_runtime_context_rejects_raw_secrets(self):
        with self.assertRaisesRegex(RuntimeContextError, "broker_secret"):
            PantheonRuntimeContext.from_mapping(
                _manifest(broker_secret="plain-secret"),
                source=RuntimeContextSource.LAUNCH_MANIFEST,
            )

        with self.assertRaisesRegex(RuntimeContextError, "PANTHEON_BROKER_SECRET"):
            PantheonRuntimeContext.from_env(_env(PANTHEON_BROKER_SECRET="plain-secret"))

    def test_runtime_context_rejects_wrapper_manifest_raw_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "launch-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "runtime_context": _manifest(),
                        "broker_secret": "plain-secret",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeContextError, "broker_secret"):
                PantheonRuntimeContext.from_manifest(manifest_path)

    def test_runtime_context_rejects_raw_secret_plural_keys(self):
        with self.assertRaisesRegex(RuntimeContextError, "api_keys"):
            PantheonRuntimeContext.from_mapping(
                _manifest(api_keys=["plain-secret"]),
                source=RuntimeContextSource.LAUNCH_MANIFEST,
            )

        with self.assertRaisesRegex(RuntimeContextError, "PANTHEON_API_KEYS"):
            PantheonRuntimeContext.from_env(_env(PANTHEON_API_KEYS="plain-secret"))

    def test_runtime_context_rejects_secret_like_path_inputs(self):
        with self.assertRaisesRegex(RuntimeContextError, "private_key_path"):
            PantheonRuntimeContext.from_mapping(
                _manifest(private_key_path="/var/run/plain-secret.pem"),
                source=RuntimeContextSource.LAUNCH_MANIFEST,
            )

        with self.assertRaisesRegex(RuntimeContextError, "PANTHEON_PRIVATE_KEY_PATH"):
            PantheonRuntimeContext.from_env(
                _env(PANTHEON_PRIVATE_KEY_PATH="/var/run/plain-secret.pem")
            )

    def test_runtime_context_allows_explicit_secret_references(self):
        context = PantheonRuntimeContext.from_mapping(
            _manifest(
                required_secret_keys=["broker-api-key"],
                secret_material_path_ref="runtime-secret://paper/rtb-paper-001",
            ),
            source=RuntimeContextSource.LAUNCH_MANIFEST,
        )

        self.assertEqual(context.runtime_binding_id, "rtb-paper-001")

    def test_runtime_context_rejects_wrong_bridge(self):
        with self.assertRaisesRegex(RuntimeContextError, "bridge.repo"):
            PantheonRuntimeContext.from_mapping(
                _manifest(
                    bridge={
                        "repo": "ajoe734/lean-platform.git",
                        "path": PANTHEON_LEAN_SOURCE_PATH,
                        "commit": "abc1234",
                    }
                ),
                source=RuntimeContextSource.LAUNCH_MANIFEST,
            )


if __name__ == "__main__":
    unittest.main()
