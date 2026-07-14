from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from services.incident.incident import IncidentCase, IncidentStore


REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("uvicorn") is None,
    reason="real HTTP chain requires the uvicorn service runtime dependency",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(client: httpx.Client, url: str, *, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = client.get(url)
            if response.status_code == 200:
                return response
        except Exception as exc:  # pragma: no cover - diagnostic retained on timeout
            last_error = exc
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {url}: {last_error}")


def _wait_for_items(
    client: httpx.Client,
    url: str,
    *,
    expected: int,
    timeout: float = 20.0,
) -> list[dict]:
    deadline = time.monotonic() + timeout
    last_payload: object = None
    while time.monotonic() < deadline:
        response = client.get(url)
        if response.status_code == 200:
            last_payload = response.json()
            if isinstance(last_payload, list) and len(last_payload) == expected:
                return last_payload
        time.sleep(0.05)
    raise AssertionError(
        f"timed out waiting for {expected} records from {url}; last={last_payload!r}"
    )


def _start_service(module: str, port: int, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            module,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


def _stop_services(processes: list[subprocess.Popen]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive cleanup
            process.kill()
            process.wait(timeout=5)


def _only_record(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert len(payload) == 1
    return next(iter(payload.values()))


def test_real_http_resolve_publish_and_replay_chain(tmp_path):
    """Exercise three independent service processes over their HTTP routes."""

    incident_dir = tmp_path / "incidents"
    evolution_dir = tmp_path / "evolution"
    incident_dir.mkdir()
    evolution_dir.mkdir()

    incident_id = "inc-evochain-http"
    IncidentStore(incident_dir / "incidents.json").create_incident(
        IncidentCase(
            incident_id=incident_id,
            title="HTTP chain regression",
            status="open",
            severity="high",
            created_at="2026-07-14T04:00:00Z",
            binding_id="binding-evochain-http",
            deployment_stage="live",
            deployment_plan_id="plan-evochain-http",
            capital_pool_id="pool-evochain-http",
            persona_capital_binding_id="pcb-evochain-http",
            artifact_id="artifact-evochain-http",
            artifact_version="8.0.0",
            runtime_id="runtime-evochain-http",
            trace_id="trace-evochain-http",
            telemetry_event_ids=["tel-evochain-http"],
            incident_cluster_id="cluster-evochain-http",
        )
    )

    incident_port, postmortem_port, evolution_port = (
        _free_port(),
        _free_port(),
        _free_port(),
    )
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "PANTHEON_PERSISTENCE_POSTURE": "dev",
            "INCIDENT_STORE_BACKEND": "json",
            "POSTMORTEM_STORE_BACKEND": "json",
            "INCIDENTS_DATA_DIR": str(incident_dir),
            "POSTMORTEMS_DATA_DIR": str(incident_dir),
            "INCIDENT_DATA_DIR": str(incident_dir),
            "EVOLUTION_DATA_DIR": str(evolution_dir),
            "POSTMORTEMS_URL": f"http://127.0.0.1:{postmortem_port}",
            "EVOLUTION_URL": f"http://127.0.0.1:{evolution_port}",
            "INCIDENTS_OUTBOX_POLL_SECONDS": "0.05",
            "POSTMORTEMS_OUTBOX_POLL_SECONDS": "0.05",
            "INCIDENTS_OUTBOX_BACKOFF_BASE_SECONDS": "0.01",
            "POSTMORTEMS_OUTBOX_BACKOFF_BASE_SECONDS": "0.01",
        }
    )

    processes: list[subprocess.Popen] = []
    client = httpx.Client(timeout=5.0)
    try:
        processes.append(
            _start_service("services.evolution.main:app", evolution_port, env)
        )
        processes.append(
            _start_service("services.postmortems.main:app", postmortem_port, env)
        )
        processes.append(
            _start_service("services.incidents.main:app", incident_port, env)
        )

        for port in (evolution_port, postmortem_port, incident_port):
            _wait_for(client, f"http://127.0.0.1:{port}/readyz")

        resolved = client.post(
            f"http://127.0.0.1:{incident_port}/api/incidents/{incident_id}/status",
            json={"status": "resolved"},
        )
        assert resolved.status_code == 200, resolved.text

        postmortems = _wait_for_items(
            client,
            f"http://127.0.0.1:{postmortem_port}/api/postmortems?incident_id={incident_id}",
            expected=1,
        )
        postmortem_id = postmortems[0]["postmortem_id"]

        # Re-deliver the exact persisted first-hop envelope over HTTP.
        incident_delivery = _only_record(incident_dir / "incidents_outbox.json")
        first_replay = client.post(
            f"http://127.0.0.1:{postmortem_port}/api/postmortems/consume-resolved-incident",
            json={"incident_id": incident_id, "event": incident_delivery["event"]},
        )
        assert first_replay.status_code == 200, first_replay.text

        published = client.post(
            f"http://127.0.0.1:{postmortem_port}/api/postmortems/{postmortem_id}/status",
            json={"status": "published"},
        )
        assert published.status_code == 200, published.text

        proposals = _wait_for_items(
            client,
            f"http://127.0.0.1:{evolution_port}/api/evolution/proposals",
            expected=1,
        )
        proposal_id = proposals[0]["decision_id"]
        assert proposals[0]["linked_postmortem_id"] == postmortem_id
        assert proposals[0]["linked_incident_id"] == incident_id
        assert proposals[0]["target_id"] == "artifact-evochain-http"

        # Re-deliver the exact second-hop request over the real generic route.
        postmortem_delivery = _only_record(incident_dir / "postmortems_outbox.json")
        delivery_event = postmortem_delivery["event"]
        second_replay = client.post(
            f"http://127.0.0.1:{evolution_port}/api/evolution/proposals",
            json={**delivery_event["payload"]["proposal"], "delivery_event": delivery_event},
        )
        assert second_replay.status_code == 200, second_replay.text
        assert second_replay.json()["decision_id"] == proposal_id

        closed = client.post(
            f"http://127.0.0.1:{incident_port}/api/incidents/{incident_id}/status",
            json={"status": "closed"},
        )
        assert closed.status_code == 200, closed.text
        time.sleep(0.15)

        assert len(
            client.get(
                f"http://127.0.0.1:{postmortem_port}/api/postmortems?incident_id={incident_id}"
            ).json()
        ) == 1
        assert len(
            client.get(
                f"http://127.0.0.1:{evolution_port}/api/evolution/proposals"
            ).json()
        ) == 1
    finally:
        client.close()
        _stop_services(processes)
