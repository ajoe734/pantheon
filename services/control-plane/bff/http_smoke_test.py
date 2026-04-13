from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[3]
BFF_DIR = ROOT / "services" / "control-plane" / "bff"
HOST = "127.0.0.1"

OPERATOR_TOKEN = "Bearer op-2:operator"
APPROVER_TOKEN = "Bearer op-1:approver"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


class TestOperatorBFFHttpSmoke(unittest.TestCase):
    def test_socket_level_http_smoke(self) -> None:
        port = _free_port()
        with tempfile.TemporaryDirectory(prefix="pantheon-bff-http-") as temp_dir:
            env = os.environ.copy()
            env["BFF_DATA_DIR"] = temp_dir
            env["BFF_READ_SURFACE_STATE"] = "fresh"

            command = [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--app-dir",
                str(BFF_DIR),
                "--host",
                HOST,
                "--port",
                str(port),
                "--log-level",
                "warning",
            ]
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            base_url = f"http://{HOST}:{port}"
            try:
                self._wait_until_ready(base_url, process)

                with httpx.Client(base_url=base_url, timeout=10.0) as client:
                    self._verify_health(client)
                    self._verify_deployment_review(client)
                    self._verify_command_roundtrip(client)
            finally:
                self._terminate(process)

    def _wait_until_ready(self, base_url: str, process: subprocess.Popen[str]) -> None:
        deadline = time.time() + 20.0
        last_error: str | None = None
        while time.time() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    "uvicorn exited before becoming ready.\n"
                    f"stdout:\n{stdout}\n"
                    f"stderr:\n{stderr}"
                )
            try:
                response = httpx.get(f"{base_url}/health", timeout=1.0)
                if response.status_code == 200:
                    return
                last_error = f"unexpected /health status {response.status_code}: {response.text}"
            except Exception as exc:  # pragma: no cover - depends on socket timing
                last_error = str(exc)
            time.sleep(0.25)
        self._terminate(process)
        stdout, stderr = process.communicate(timeout=5)
        raise AssertionError(
            "Timed out waiting for uvicorn health endpoint.\n"
            f"last_error: {last_error}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )

    def _verify_health(self, client: httpx.Client) -> None:
        response = client.get("/health")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["service"], "operator-bff")

    def _verify_deployment_review(self, client: httpx.Client) -> None:
        response = client.get(
            "/api/v1/operator/deployment-review/plan-F-042",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        data = payload.get("data", {})
        for key in ("deployment_plan", "allowedActions", "latestRun", "review"):
            self.assertIn(key, data)

    def _verify_command_roundtrip(self, client: httpx.Client) -> None:
        submit = client.post(
            "/api/v1/operator/commands",
            headers={"Authorization": APPROVER_TOKEN},
            json={
                "command": "ApproveDeployment",
                "target": {"type": "DeploymentPlan", "id": "dp-001"},
                "action": "approve",
                "params": {
                    "deployment_plan_id": "dp-001",
                    "approval_decision": "approve",
                },
                "audit_context": {"reason": "HTTP smoke"},
            },
        )
        self.assertEqual(submit.status_code, 202, submit.text)
        receipt = submit.json()["receipt"]
        command_id = receipt["command_id"]

        status = client.get(
            f"/api/v1/operator/commands/{command_id}",
            headers={"Authorization": APPROVER_TOKEN},
        )
        self.assertEqual(status.status_code, 200, status.text)
        payload = status.json()
        self.assertEqual(payload["command_id"], command_id)
        self.assertIn(payload["status"], {"submitted", "processing", "executed", "failed", "timeout"})

    def _terminate(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                process.kill()
                process.communicate(timeout=5)
        else:
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
