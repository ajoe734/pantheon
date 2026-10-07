"""Runtime wiring and cold-start healthcheck regressions for the Agora interaction worker."""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from services.control_plane.bff.agora.interaction.persona_client import (
    PersonaReadPort,
    build_canonical_persona_client,
)

ROOT = Path(__file__).resolve().parents[4]
LAUNCHER = ROOT / "scripts" / "run_agora_interaction_worker.py"


class _MockPersonaHandler(http.server.BaseHTTPRequestHandler):
    personas = [
        {
            "persona_id": "persona-alpha",
            "name": "Alpha Trader",
            "lifecycle_state": "active",
        }
    ]

    def do_GET(self) -> None:
        if self.path.startswith("/api/personas"):
            body = json.dumps(self.personas).encode("utf-8")
            status = 200
        else:
            body = b'{"detail": "Not found"}'
            status = 404
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def persona_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _MockPersonaHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def _run_worker(
    *args: str,
    env: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy() if env is None else env
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *args],
        cwd=str(ROOT),
        env=run_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_healthcheck_cold_start_under_5s_deadline(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    heartbeat.touch()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["AGORA_WORKER_HEARTBEAT_PATH"] = str(heartbeat)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    start = time.monotonic()
    result = _run_worker("--healthcheck", env=env, timeout=6.0)
    elapsed = time.monotonic() - start

    assert result.returncode == 0, f"Healthcheck failed: {result.stderr}"
    assert "Healthcheck OK" in result.stdout + result.stderr
    assert elapsed < 5.0, f"Cold-start healthcheck took {elapsed:.2f}s, exceeding 5.0s budget"


def test_healthcheck_fails_closed_when_heartbeat_missing(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["AGORA_WORKER_HEARTBEAT_PATH"] = str(tmp_path / "missing_heartbeat")
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = _run_worker("--healthcheck", env=env, timeout=6.0)

    assert result.returncode != 0, "Missing heartbeat must fail closed"
    assert "Healthcheck OK" not in result.stdout + result.stderr
    assert "Healthcheck failed" in result.stdout + result.stderr


def test_healthcheck_fails_closed_when_heartbeat_stale(tmp_path: Path) -> None:
    heartbeat = tmp_path / "stale_heartbeat"
    heartbeat.touch()
    stale_time = time.time() - 600
    os.utime(heartbeat, (stale_time, stale_time))

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["AGORA_WORKER_HEARTBEAT_PATH"] = str(heartbeat)
    env["AGORA_WORKER_HEARTBEAT_MAX_AGE_SECONDS"] = "300"
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = _run_worker("--healthcheck", env=env, timeout=6.0)

    assert result.returncode != 0, "Stale heartbeat must fail closed"
    assert "Healthcheck OK" not in result.stdout + result.stderr
    assert "Healthcheck failed" in result.stdout + result.stderr


def test_healthcheck_does_not_import_runtime_stores(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    heartbeat.touch()
    probe_script = (
        "import os, sys\n"
        "sys.path.insert(0, 'scripts')\n"
        "import run_agora_interaction_worker\n"
        "sys.argv = ['run_agora_interaction_worker.py', '--healthcheck']\n"
        "rc = run_agora_interaction_worker.main()\n"
        "assert rc == 0\n"
        "assert 'agora.governance.store' not in sys.modules\n"
        "assert 'agora.strategy_workshop.store' not in sys.modules\n"
    )
    env = os.environ.copy()
    env["AGORA_WORKER_HEARTBEAT_PATH"] = str(heartbeat)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            probe_script,
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=10.0,
    )
    assert result.returncode == 0, f"Runtime store import probe failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"


def test_worker_once_initializes_and_processes(persona_server: str) -> None:
    env = os.environ.copy()
    env["PERSONA_URL"] = persona_server
    env["AGORA_WORKSHOP_STORE_BACKEND"] = "memory"
    env["AGORA_GOVERNANCE_STORE_BACKEND"] = "memory"
    env["AGORA_RESEARCH_STORE_BACKEND"] = "memory"
    env["AGORA_DATASET_STORE_BACKEND"] = "memory"

    result = _run_worker("--once", env=env, timeout=10.0)
    assert result.returncode == 0, f"Worker --once execution failed: {result.stderr}"
    assert "Processed 0 interaction(s)" in result.stdout + result.stderr


def test_canonical_persona_client_wiring_with_persona_server(persona_server: str) -> None:
    env = {
        "PERSONA_URL": persona_server,
        "PANTHEON_PERSONA_SERVICE_TOKEN": "test-service-token",
    }
    orig = os.environ.copy()
    try:
        os.environ.update(env)
        client = build_canonical_persona_client()
        assert isinstance(client, PersonaReadPort)
        diag = getattr(client, "get_surface_status", lambda: {})()
        pcr = diag.get("persona_capital_runtime") or {}
        persona_status = pcr.get("persona") or {}
        assert persona_status.get("status") == "ok"
        assert persona_status.get("source") == "store"
        personas = client.list_personas()
        assert isinstance(personas, list)
        assert len(personas) == 1
        assert personas[0]["persona_id"] == "persona-alpha"
    finally:
        os.environ.clear()
        os.environ.update(orig)


def test_canonical_persona_client_unconfigured_fails_closed() -> None:
    orig = os.environ.copy()
    try:
        for k in ("PERSONA_URL", "PANTHEON_PERSONA_URL", "PANTHEON_PERSONA_API_URL"):
            os.environ.pop(k, None)
        client = build_canonical_persona_client()
        diag = getattr(client, "get_surface_status", lambda: {})()
        pcr = diag.get("persona_capital_runtime") or {}
        persona_status = pcr.get("persona") or {}
        assert persona_status.get("status") == "unavailable"
        assert persona_status.get("source") == "missing"
    finally:
        os.environ.clear()
        os.environ.update(orig)
