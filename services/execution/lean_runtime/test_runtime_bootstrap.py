import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from services.execution.lean_runtime.bootstrap_contract import (
    PANTHEON_LEAN_REMOTE,
    PANTHEON_LEAN_SOURCE_PATH,
)
from services.execution.lean_runtime.runtime_context import (
    RuntimeContextError,
    RuntimeContextSource,
)
from services.execution.lean_runtime.runtime_bootstrap import (
    _SidecarHandler,
    _load_runtime_context,
    _load_sidecar_state,
)


def _manifest(**overrides):
    payload = {
        "request_id": "request-paper-ctx",
        "trace_id": "trace-paper-ctx",
        "runtime_binding_id": "rtb-paper-ctx",
        "runtime_id": "rt-paper-ctx",
        "deployment_plan_id": "dp-paper-ctx",
        "deployment_stage": "paper",
        "runtime_role": "paper",
        "artifact": {
            "artifact_id": "artifact-paper-ctx",
            "artifact_version": "1.0.0",
            "artifact_checksum": "sha256:paperctx",
            "strategy_id": "strategy-paper-ctx",
        },
        "capital": {
            "capital_pool_id": "pool-paper-ctx",
            "persona_capital_binding_id": "pcb-paper-ctx",
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
        "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
        "PANTHEON_RUNTIME_MODE": "paper",
        "PANTHEON_RUNTIME_BINDING_ID": "rtb-paper-env",
        "PANTHEON_RUNTIME_ID": "rt-paper-env",
        "PANTHEON_DEPLOYMENT_PLAN_ID": "dp-paper-env",
        "PANTHEON_DEPLOYMENT_STAGE": "paper",
        "PANTHEON_ARTIFACT_ID": "artifact-paper-env",
        "PANTHEON_ARTIFACT_VERSION": "1.0.0",
        "PANTHEON_ARTIFACT_CHECKSUM": "sha256:paperenv",
        "PANTHEON_STRATEGY_ID": "strategy-paper-env",
        "PANTHEON_CAPITAL_POOL_ID": "pool-paper-env",
        "PANTHEON_PERSONA_CAPITAL_BINDING_ID": "pcb-paper-env",
        "PANTHEON_ENGINE_BRIDGE_REMOTE": PANTHEON_LEAN_REMOTE,
        "PANTHEON_ENGINE_BRIDGE_SOURCE_PATH": PANTHEON_LEAN_SOURCE_PATH,
        "PANTHEON_ENGINE_BRIDGE_COMMIT": "abc1234",
        "PANTHEON_TRACE_ID": "trace-paper-env",
        "PANTHEON_REQUEST_ID": "request-paper-env",
    }
    payload.update(overrides)
    return payload


class RuntimeBootstrapContextTests(unittest.TestCase):
    def test_paper_context_loads_from_launch_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "launch-manifest.json"
            manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
                    "PANTHEON_RUNTIME_MODE": "paper",
                },
                clear=True,
            ):
                context = _load_runtime_context(
                    launch_manifest=str(manifest_path),
                    role="pantheon-paper-execution-runtime",
                )

        self.assertIsNotNone(context)
        self.assertEqual(context.context_source, RuntimeContextSource.LAUNCH_MANIFEST)
        self.assertEqual(context.runtime_binding_id, "rtb-paper-ctx")
        self.assertEqual(context.deployment_stage, "paper")

    def test_paper_context_loads_from_env_fallback(self):
        with patch.dict(os.environ, _env(), clear=True):
            context = _load_runtime_context(
                launch_manifest=None,
                role="pantheon-paper-execution-runtime",
            )

        self.assertIsNotNone(context)
        self.assertEqual(context.context_source, RuntimeContextSource.ENV_VARS)
        self.assertEqual(context.runtime_binding_id, "rtb-paper-env")
        self.assertEqual(context.artifact.artifact_id, "artifact-paper-env")

    def test_staging_paper_runtime_missing_context_fails_closed(self):
        with patch.dict(
            os.environ,
            {
                "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
                "PANTHEON_RUNTIME_MODE": "staging",
                "PANTHEON_DEPLOYMENT_STAGE": "staging",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeContextError, "runtime context is required"):
                _load_runtime_context(
                    launch_manifest=None,
                    role="pantheon-paper-execution-runtime",
                )

    def test_live_sidecar_does_not_require_runtime_context(self):
        with patch.dict(
            os.environ,
            {
                "PANTHEON_RUNTIME_ROLE": "live",
                "PANTHEON_RUNTIME_MODE": "live",
            },
            clear=True,
        ):
            context = _load_runtime_context(launch_manifest=None, role="live")

        self.assertIsNone(context)


class RuntimeBootstrapLiveGuardTests(unittest.TestCase):
    def test_live_sidecar_health_reports_not_activated(self):
        env = {
            "PANTHEON_RUNTIME_ROLE": "live",
            "PANTHEON_RUNTIME_MODE": "live",
            "PANTHEON_LIVE_RUNTIME_ID": "live-runtime-001",
            "PANTHEON_REQUIRED_SECRET_KEYS": "BROKER_API_KEY",
            "BROKER_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=False):
            payload = _load_sidecar_state("live")

        self.assertEqual(payload["runtime_role"], "live")
        self.assertTrue(payload["health_only"])
        self.assertEqual(payload["activation_status"], "not_activated")
        self.assertFalse(payload["live_broker_enabled"])
        self.assertFalse(payload["broker_connect_allowed"])
        self.assertFalse(payload["order_placement_allowed"])
        self.assertFalse(payload["bracket_order_submission_allowed"])
        self.assertEqual(payload["required_secret_status"][0]["env"], "BROKER_API_KEY")
        self.assertFalse(payload["required_secret_status"][0]["configured"])

    def test_live_sidecar_blocks_broker_connect_and_order_posts(self):
        env = {
            "PANTHEON_RUNTIME_ROLE": "live",
            "PANTHEON_RUNTIME_MODE": "live",
            "PANTHEON_LIVE_RUNTIME_ID": "live-runtime-001",
            "PANTHEON_LIVE_BROKER_ENABLED": "true",
        }

        with patch.dict(os.environ, env, clear=False):
            server = ThreadingHTTPServer(("127.0.0.1", 0), _SidecarHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(f"{base_url}/healthz", timeout=2) as response:
                    health_payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(health_payload["activation_status"], "not_activated")
                self.assertTrue(health_payload["health_only"])
                self.assertFalse(health_payload["live_broker_enabled"])
                self.assertTrue(health_payload["requested_live_broker_enabled"])

                broker_payload = self._post_blocked(base_url, "/api/broker/connect")
                order_payload = self._post_blocked(base_url, "/api/orders")

                self.assertEqual(broker_payload["action"], "broker_connect")
                self.assertEqual(order_payload["action"], "order_placement")
                for payload in (broker_payload, order_payload):
                    self.assertEqual(payload["status"], "blocked")
                    self.assertEqual(payload["activation_status"], "not_activated")
                    self.assertFalse(payload["broker_connect_allowed"])
                    self.assertFalse(payload["order_placement_allowed"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def _post_blocked(self, base_url: str, path: str) -> dict:
        request = urllib.request.Request(
            f"{base_url}{path}",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(raised.exception.code, 403)
        return json.loads(raised.exception.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
