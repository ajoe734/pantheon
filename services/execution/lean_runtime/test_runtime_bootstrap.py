import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from services.execution.lean_runtime.runtime_bootstrap import (
    _SidecarHandler,
    _load_sidecar_state,
)


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
