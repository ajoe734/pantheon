"""Separate-process authority and restart proof for L12-EVO-001.

These tests deliberately cross OS-process boundaries.  Unit tests that rebuild
two store objects in one interpreter are useful, but they do not prove the
contract the Compose sidecar depends on: two API replicas and a restarted
dispatcher must see one shared durable decision/outbox authority.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from services.evolution.dispatch_outbox import (
    EvolutionDispatchOutbox,
    build_dispatch_outbox_store,
    dispatch_identity,
)


ROOT = Path(__file__).resolve().parents[2]
TENANT_ID = "tenant-process-proof"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Tenant-Id": TENANT_ID,
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body) if body else {}


def _wait_for_api(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 20
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Evolution API exited before readiness: {process.returncode}")
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - readiness retry
            last_error = exc
        time.sleep(0.1)
    raise AssertionError(f"Evolution API did not become ready: {last_error}")


def _start_api(*, port: int, env: dict[str, str]) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "services.evolution.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    _wait_for_api(f"http://127.0.0.1:{port}", process)
    return process


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _service_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "EVOLUTION_DATA_DIR": str(tmp_path / "evolution"),
            "INCIDENT_DATA_DIR": str(tmp_path / "incidents"),
            "EVOLUTION_STORE_BACKEND": "json",
            "EVOLUTION_AUTH_MODE": "disabled",
            "EVOLUTION_DEFAULT_TENANT_ID": TENANT_ID,
            "PANTHEON_PERSISTENCE_POSTURE": "dev",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager.invalid",
            "EVOLUTION_RESEARCH_API_URL": "http://research-orchestrator.invalid",
        }
    )
    return env


def _proposal(decision_id: str, target_id: str, *, action_type: str = "retrain") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "decision_id": decision_id,
        "tenant_id": TENANT_ID,
        "target_type": "candidate_artifact",
        "target_id": target_id,
        "target_version": "1.0.0",
        "action_type": action_type,
        "rationale": "L12-EVO-001 separate-process authority proof.",
        "created_by_id": "evolution-process-proof",
        "linked_incident_id": f"inc-{decision_id}",
    }
    if action_type == "freeze":
        payload["target_stage"] = "paper"
    return payload


def _approve_freeze(base_url: str, decision_id: str) -> None:
    status, _ = _request_json(
        base_url,
        "POST",
        "/api/evolution/proposals",
        _proposal(decision_id, f"artifact-{decision_id}", action_type="freeze"),
    )
    assert status == 201
    status, _ = _request_json(
        base_url,
        "POST",
        f"/api/evolution/proposals/{decision_id}/review",
        {
            "actor_role": "risk_owner",
            "actor_id": "risk-process-proof",
            "approval_decision_id": f"approval-{decision_id}",
            "tenant_id": TENANT_ID,
        },
    )
    assert status == 200
    status, approved = _request_json(
        base_url,
        "POST",
        f"/api/evolution/proposals/{decision_id}/approve",
        {
            "actor_role": "risk_owner",
            "actor_id": "risk-process-proof",
            "tenant_id": TENANT_ID,
        },
    )
    assert status == 200
    assert approved["decision_state"] == "approved"


def _run_worker_once(*, api_url: str, env: dict[str, str], health_file: Path) -> dict[str, Any]:
    worker_env = dict(env)
    worker_env.update(
        {
            "EVOLUTION_API_URL": api_url,
            "EVOLUTION_DISPATCH_MAX_TICKS": "1",
            "EVOLUTION_DISPATCH_INTERVAL_SECONDS": "1",
            "EVOLUTION_DISPATCH_TIMEOUT_SECONDS": "2",
            "EVOLUTION_DISPATCH_HEALTH_FILE": str(health_file),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "services.evolution.dispatch_worker"],
        cwd=ROOT,
        env=worker_env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    events = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{")
    ]
    assert len(events) == 1, completed.stdout
    return events[0]


def test_two_api_processes_and_restarted_worker_share_one_authority(tmp_path: Path) -> None:
    """Two replicas race safely; a fresh dispatcher recovers and sees the same DLQ."""

    env = _service_env(tmp_path)
    first_port = _free_port()
    second_port = _free_port()
    first_url = f"http://127.0.0.1:{first_port}"
    second_url = f"http://127.0.0.1:{second_port}"
    first = _start_api(port=first_port, env=env)
    second = _start_api(port=second_port, env=env)
    try:
        # The single-active constraint is serialized by the shared owner store,
        # not by one API process's in-memory lock.
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(
                pool.map(
                    lambda args: _request_json(*args),
                    [
                        (
                            first_url,
                            "POST",
                            "/api/evolution/proposals",
                            _proposal("evo-process-race-a", "artifact-process-race"),
                        ),
                        (
                            second_url,
                            "POST",
                            "/api/evolution/proposals",
                            _proposal("evo-process-race-b", "artifact-process-race"),
                        ),
                    ],
                )
            )
        assert sorted(status for status, _ in outcomes) == [201, 422], outcomes

        # Approve a supported-by-contract but deliberately non-auto-executable
        # governance action. Rewind only the activation bit to reproduce a
        # crash after approval committed and before activation committed.
        decision_id = "evo-process-restart-freeze"
        _approve_freeze(first_url, decision_id)
        data_dir = Path(env["EVOLUTION_DATA_DIR"])
        outbox = EvolutionDispatchOutbox(build_dispatch_outbox_store(data_dir=data_dir))
        outbox_id, _, _ = dispatch_identity(TENANT_ID, decision_id)
        activated = outbox.get_by_id(outbox_id)
        assert activated is not None and activated.delivery_ready is True
        stranded = type(activated)(
            record=activated.record,
            delivery_ready=False,
            transition=activated.transition,
        )
        outbox.store.put(stranded)

        first_tick = _run_worker_once(
            api_url=second_url,
            env=env,
            health_file=tmp_path / "worker-first-health.json",
        )
        assert first_tick["result"]["reconciled"] == 1, first_tick
        assert first_tick["result"]["claimed"] == 1, first_tick
        assert first_tick["result"]["unsupported"] == 1, first_tick
        assert first_tick["result"]["dead_lettered"] == 1, first_tick

        # A brand-new worker process reads the same DLQ and does not replay the
        # action or reset its durable attempt state.
        restarted_tick = _run_worker_once(
            api_url=first_url,
            env=env,
            health_file=tmp_path / "worker-restarted-health.json",
        )
        assert restarted_tick["result"]["claimed"] == 0, restarted_tick
        assert restarted_tick["result"]["items"] == [], restarted_tick

        status, listed = _request_json(
            second_url,
            "GET",
            f"/api/evolution/dispatch-outbox?tenant_id={TENANT_ID}",
        )
        assert status == 200
        record = next(item for item in listed["records"] if item["outbox_id"] == outbox_id)
        assert record["status"] == "dead_lettered"
        assert record["delivery_attempts"] == 1

        # Both API replicas consult the same durable replay timestamp. A
        # concurrent replay trigger cannot race an in-memory cooldown on one
        # replica and revive the record through the other.
        replay_payload = {
            "actor_id": "operator-process-proof",
            "note": "Concurrent replay must respect durable cooldown.",
            "tenant_id": TENANT_ID,
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            replay_outcomes = list(
                pool.map(
                    lambda args: _request_json(*args),
                    [
                        (
                            first_url,
                            "POST",
                            f"/api/evolution/dispatch-outbox/{outbox_id}/replay",
                            replay_payload,
                        ),
                        (
                            second_url,
                            "POST",
                            f"/api/evolution/dispatch-outbox/{outbox_id}/replay",
                            replay_payload,
                        ),
                    ],
                )
            )
        assert [status for status, _ in replay_outcomes] == [409, 409], replay_outcomes
        assert all(
            "replay cooldown" in str(payload.get("detail") or "")
            for _, payload in replay_outcomes
        )
        unchanged = outbox.get_by_id(outbox_id)
        assert unchanged is not None
        assert unchanged.status.value == "dead_lettered"
        assert unchanged.delivery_attempts == 1
        assert unchanged.redrive_count == 0

        status, decision = _request_json(
            second_url,
            "GET",
            f"/api/evolution/proposals/{decision_id}",
        )
        assert status == 200
        assert decision["decision_state"] == "approved"
        assert decision["execution_result"] is None
    finally:
        _stop(second)
        _stop(first)


def test_production_posture_refuses_json_before_evolution_app_boot(tmp_path: Path) -> None:
    """A production process cannot silently start on the dev JSON backend."""

    env = _service_env(tmp_path)
    env.update(
        {
            "PANTHEON_PERSISTENCE_POSTURE": "production",
            "DATABASE_URL": "postgresql://evolution.invalid/pantheon",
            "EVOLUTION_STORE_BACKEND": "json",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", "import services.evolution.main"],
        cwd=ROOT,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert (
        "EVOLUTION_STORE_BACKEND must be postgres in staging/prod persistence posture"
        in output
    )
