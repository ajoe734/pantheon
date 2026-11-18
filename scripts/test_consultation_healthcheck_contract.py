"""Contract: consultation-svc container health is liveness, not dead-letter state.

Runs the exact healthcheck command from docker-compose.yml.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def _command() -> str:
    svc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["consultation-svc"]
    test = svc["healthcheck"]["test"]
    assert test[0] == "CMD-SHELL"
    return test[1].replace("python -c", f'"{sys.executable}" -c', 1)


@pytest.fixture()
def readyz():
    state = {"status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(state["status"])
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield state, server.server_address[1]
    server.shutdown()


def _run(tmp_path, port, content, age=0.0):
    health = tmp_path / "workflow-health.json"
    if content is not None:
        health.write_text(content, encoding="utf-8")
        t = time.time() - age
        os.utime(health, (t, t))
    env = {
        **os.environ,
        "PORT": str(port),
        "CONSULTATION_WORKFLOW_EXECUTOR_HEALTH_FILE": str(health),
    }
    return subprocess.run(_command(), shell=True, env=env, capture_output=True).returncode


def _health(dead=0, blocked=0, status="ok"):
    return json.dumps(
        {
            "status": status,
            "functional_health": {
                "ready": status == "ok",
                "blocked_count": blocked,
                "dead_letter_count": dead,
            },
        }
    )


def test_healthy_when_fresh_and_ok(tmp_path, readyz):
    assert _run(tmp_path, readyz[1], _health()) == 0


def test_healthy_with_dead_letter_and_blocked(tmp_path, readyz):
    assert _run(tmp_path, readyz[1], _health(dead=1, blocked=2, status="degraded")) == 0


def test_unhealthy_when_readyz_fails(tmp_path, readyz):
    readyz[0]["status"] = 503
    assert _run(tmp_path, readyz[1], _health()) == 1


def test_unhealthy_when_file_missing(tmp_path, readyz):
    assert _run(tmp_path, readyz[1], None) == 1


def test_unhealthy_when_stale(tmp_path, readyz):
    assert _run(tmp_path, readyz[1], _health(dead=1, status="degraded"), age=120) == 1
    assert _run(tmp_path, readyz[1], _health(), age=120) == 1


def test_unhealthy_when_unparsable(tmp_path, readyz):
    assert _run(tmp_path, readyz[1], "not json") == 1
    assert _run(tmp_path, readyz[1], "[1]") == 1
