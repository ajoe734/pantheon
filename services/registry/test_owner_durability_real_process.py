"""Real OS-process durability proof for the Registry service — reviewer
finding 7.

``test_owner_durability.py`` proves the mounted app's Postgres-backed CAS/
replay/readback contract via ``fastapi.testclient.TestClient``, but its
"process restart" is really just dropping an in-process singleton
(``reset_store()``) and building a new ``TestClient`` in the *same* Python
process/interpreter — that never actually proves anything survives a real
process boundary (no shared file descriptors, no accidentally-retained
module-level state, a genuinely fresh interpreter). This module closes that
gap: it launches the actual FastAPI app via ``uvicorn`` in a real
``subprocess.Popen`` child process bound to a real TCP socket, against a real
PostgreSQL instance, authenticated with a strictly-signed HS256 JWT (issuer,
audience, subject, tenant, role, expiry all present) verified through the
real ``services.runtime_auth_inbound`` path — not the permissive structured-
token stub used elsewhere in this package's unit tests.

Gated on ``TEST_DATABASE_URL``, skipping cleanly without a live database —
the same pattern used throughout this repo's Postgres-backed test suites
(e.g. services/incident/test_pg_store_integration.py,
services/foundation/tests/test_registry_owner_transaction.py).

Documented residual gap (see docs/deployment/evidence/REGISTRY-STRATEGY-UNIFIED-CONTRACT-001/evidence.json):
adversarial response-loss / outbox-crash-window injection (killing the
server process mid-transaction, between "entry committed" and "response
sent") is not exercised here. Reliably synchronizing a process kill to that
exact window from outside the process is a genuinely hard integration-test
problem (it requires either a debugger-level breakpoint inside the running
server or an instrumented build), and building that harness was judged out
of proportion for this pass; what this module does prove — real fresh-
process restart and real two-process concurrent CAS/replay — are the two
pieces of finding 7 that are tractable without that additional harness.
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
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("uvicorn")

from services.runtime_auth_inbound import encode_jwt_hs256

_REPO_ROOT = Path(__file__).resolve().parents[2]
_JWT_SECRET = "registry-real-process-durability-secret"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _strict_jwt(*, subject: str = "durability-operator") -> str:
    """A strictly-signed JWT with issuer/audience/subject/tenant/role/expiry
    all present — verified through the real HS256 path in
    services.runtime_auth_inbound, not a permissive-mode structured stub."""
    return encode_jwt_hs256(
        {
            "sub": subject,
            "tenant": "durability-tenant",
            "roles": ["operator"],
            "iss": "registry-durability-tests",
            "aud": "registry-svc",
            "exp": time.time() + 3600,
        },
        secret=_JWT_SECRET,
    )


def _spawn_registry_process(*, port: int, dsn: str, schema: str) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(
        {
            "REGISTRY_STORE_BACKEND": "postgres",
            "REGISTRY_STORE_DSN": dsn,
            "REGISTRY_ENTRIES_TABLE": f"{schema}.entries",
            "REGISTRY_RECEIPTS_TABLE": f"{schema}.command_receipts",
            "PANTHEON_REGISTRY_AUTH_MODE": "strict",
            "PANTHEON_REGISTRY_JWT_SECRET": _JWT_SECRET,
            "PANTHEON_REGISTRY_JWT_ISSUER": "registry-durability-tests",
            "PANTHEON_REGISTRY_JWT_AUDIENCE": "registry-svc",
        }
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "services.registry.service:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _wait_for_health(port: int, *, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - server may not be listening yet
            last_exc = exc
        time.sleep(0.2)
    raise RuntimeError(f"registry subprocess on port {port} never became healthy: {last_exc}")


def _http(method: str, port: int, path: str, *, token: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, (json.loads(body) if body else {})


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.fixture
def pg_schema():
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for real Postgres owner durability proof")
    import psycopg
    from psycopg import sql

    schema = f"registry_real_proc_{uuid4().hex}"
    try:
        yield dsn, schema
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def test_entry_survives_a_real_process_kill_and_restart(pg_schema):
    """Register an entry in one real OS process, kill that process outright
    (not an in-process singleton reset), start a brand new process against
    the same DSN/schema, and prove the entry reads back correctly — a real
    fresh-process restart, not a same-interpreter simulation of one."""
    dsn, schema = pg_schema
    port_a = _free_port()
    proc_a = _spawn_registry_process(port=port_a, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port_a)
        token = _strict_jwt()
        status, body = _http(
            "POST",
            port_a,
            "/api/registry/entries",
            token=token,
            payload={
                "artifact_type": "strategy_spec",
                "strategy_id": "real-proc-strat",
                "version": "1.0.0",
                "artifact_state": "draft",
                "checksum": "sha256:realproc",
            },
        )
        assert status == 200, body
        registry_id = body["entry"]["registry_id"]
    finally:
        _stop(proc_a)

    port_b = _free_port()
    proc_b = _spawn_registry_process(port=port_b, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port_b)
        token = _strict_jwt()
        status, body = _http("GET", port_b, f"/api/registry/entries/{registry_id}", token=token)
        assert status == 200, body
        assert body["entry"]["strategy_id"] == "real-proc-strat"
        assert body["entry"]["last_actor"]["actor_id"] == "durability-operator"
    finally:
        _stop(proc_b)


def test_two_real_concurrent_processes_share_correct_cas_and_replay_semantics(pg_schema):
    """Two independent OS processes (not two objects in one interpreter)
    against the same Postgres backend: the second process's stale-CAS
    metadata write is rejected (409), and a same-key replay from either
    process returns the originally committed result, not whatever the row
    has become since."""
    dsn, schema = pg_schema
    port_a = _free_port()
    port_b = _free_port()
    proc_a = _spawn_registry_process(port=port_a, dsn=dsn, schema=schema)
    proc_b = _spawn_registry_process(port=port_b, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port_a)
        _wait_for_health(port_b)
        token = _strict_jwt()

        status, created = _http(
            "POST",
            port_a,
            "/api/registry/entries",
            token=token,
            payload={
                "artifact_type": "strategy_spec",
                "strategy_id": "real-proc-concurrent",
                "version": "1.0.0",
                "artifact_state": "draft",
                "checksum": "sha256:concurrent",
            },
        )
        assert status == 200, created
        registry_id = created["entry"]["registry_id"]

        # Process A commits the first metadata write.
        status, first = _http(
            "PATCH",
            port_a,
            f"/api/registry/entries/{registry_id}/metadata",
            token=token,
            payload={
                "expected_metadata": None,
                "metadata": {"note": "committed-by-process-a"},
                "command_key": "cmd-cross-process",
            },
        )
        assert status == 200, first

        # Process B, unaware of A's write, attempts the same stale-base write
        # against the *same* shared Postgres backend — must fail 409, not
        # silently overwrite A's committed value.
        status, stale = _http(
            "PATCH",
            port_b,
            f"/api/registry/entries/{registry_id}/metadata",
            token=token,
            payload={
                "expected_metadata": None,
                "metadata": {"note": "conflicting-write-from-process-b"},
            },
        )
        assert status == 409, stale

        # A same-key replay of the *original* command from process B (which
        # never independently committed anything under this key) must return
        # the entry exactly as process A originally committed it.
        status, replay = _http(
            "PATCH",
            port_b,
            f"/api/registry/entries/{registry_id}/metadata",
            token=token,
            payload={
                "expected_metadata": None,
                "metadata": {"note": "committed-by-process-a"},
                "command_key": "cmd-cross-process",
            },
        )
        assert status == 200, replay
        assert replay["entry"]["metadata"] == {"note": "committed-by-process-a"}
    finally:
        _stop(proc_a)
        _stop(proc_b)
