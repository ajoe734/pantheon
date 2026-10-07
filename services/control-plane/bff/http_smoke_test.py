from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[3]
BFF_DIR = ROOT / "services" / "control-plane" / "bff"
HOST = "127.0.0.1"

# The third stub-token segment is the verified tenant; owner reads fail closed
# without one.
OPERATOR_TOKEN = "Bearer op-2:operator:tenant-smoke"
APPROVER_TOKEN = "Bearer op-1:approver:tenant-smoke"


_DEPLOYMENT_PLAN = {
    "id": "plan-F-042",
    "plan_id": "plan-F-042",
    "stage": "paper",
    "artifact_id": "artifact-F-042",
}


_APPROVAL_DECISION = {
    "decision_id": "appr-dp-001",
    "decision": "approved",
    "decision_state": "approved",
    "command": "ApproveDeployment",
    "target_type": "DeploymentPlan",
    "target_id": "dp-001",
}

_OWNER_COLLECTIONS = {
    "/api/deployment/plans": [_DEPLOYMENT_PLAN],
    "/api/governance/approvals": [_APPROVAL_DECISION],
}


class _OwnerStub(BaseHTTPRequestHandler):
    """Stand-in Deployment and Governance owners.

    The BFF reads plans and approval evidence from the owner APIs rather than
    a local snapshot, so the smoke needs reachable owners to serve them.
    """

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        records = _OWNER_COLLECTIONS.get(self.path.split("?", 1)[0])
        found = records is not None
        body = json.dumps(records if found else {}).encode("utf-8")
        self.send_response(200 if found else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


class TestOperatorBFFHttpSmoke(unittest.TestCase):
    def test_socket_level_http_smoke(self) -> None:
        port = _free_port()
        owner = ThreadingHTTPServer((HOST, 0), _OwnerStub)
        threading.Thread(target=owner.serve_forever, daemon=True).start()
        self.addCleanup(owner.server_close)
        self.addCleanup(owner.shutdown)
        with tempfile.TemporaryDirectory(prefix="pantheon-bff-http-") as temp_dir:
            env = os.environ.copy()
            env["BFF_DATA_DIR"] = temp_dir
            env["BFF_READ_SURFACE_STATE"] = "fresh"
            env["PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK"] = "true"
            owner_url = f"http://{HOST}:{owner.server_address[1]}"
            env["PANTHEON_DEPLOYMENT_API_URL"] = owner_url
            env["PANTHEON_GOVERNANCE_APPROVAL_API_URL"] = owner_url

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
        deadline = time.time() + 60.0
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
            "/bff/v1/commands",
            headers={
                "Authorization": APPROVER_TOKEN,
                "Idempotency-Key": "http-smoke-approve-dp-001",
            },
            json={
                "command": "ApproveDeployment",
                "target": {"type": "DeploymentPlan", "id": "dp-001"},
                "action": "approve",
                "params": {
                    "deployment_plan_id": "dp-001",
                    "approval_decision": "approve",
                    "approvalId": "appr-dp-001",
                },
                "audit_context": {"reason": "HTTP smoke"},
            },
        )
        self.assertEqual(submit.status_code, 202, submit.text)
        receipt = submit.json()["data"]["receipt"]
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
