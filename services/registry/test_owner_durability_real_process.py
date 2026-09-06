"""Real OS-process durability proof for the Registry service — reviewer
findings 3 & 7.

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

This module proves:
1. Real fresh-process restart across OS processes.
2. Real two-process concurrent CAS and replay semantics.
3. Transaction rollback before commit across create, metadata, and advance,
   leaving 0 orphan rows in entries and command_receipts, and allowing
   clean retry under the same idempotency/command key.
4. Response-loss crash window with SIGKILL across create, metadata, and advance,
   proving durable command receipt recovery even after subsequent aggregate
   drift, and proving replay never mutates live state.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import threading
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


def _http(
    method: str,
    port: int,
    path: str,
    *,
    token: str,
    payload: dict | None = None,
    headers: dict | None = None,
) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers=req_headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, (json.loads(body) if body else {})
        except Exception:
            return exc.code, {"detail": body}


def _stop(proc: subprocess.Popen, *, sigkill: bool = False) -> None:
    if proc.poll() is None:
        if sigkill:
            proc.kill()
        else:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _sigkill(proc: subprocess.Popen) -> None:
    """Ungraceful OS-level kill (SIGKILL) without running any process shutdown hooks."""
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


class DroppedSocketProxy:
    """A lightweight TCP proxy that can simulate hard response loss.
    When drop_next is True, it forwards the client's request to upstream,
    waits for the upstream server to write response bytes (proving the commit
    succeeded in the database), and immediately resets/aborts the client socket
    before forwarding any response bytes.
    """
    def __init__(self, target_port: int):
        self.target_port = target_port
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("127.0.0.1", 0))
        self.server_sock.listen(10)
        self.port = self.server_sock.getsockname()[1]
        self.drop_next = False
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while self._running:
            try:
                self.server_sock.settimeout(0.5)
                client_sock, _ = self.server_sock.accept()
            except (socket.timeout, OSError):
                continue

            try:
                upstream_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                upstream_sock.connect(("127.0.0.1", self.target_port))

                client_sock.settimeout(5.0)
                req_data = b""
                while True:
                    chunk = client_sock.recv(4096)
                    if not chunk:
                        break
                    req_data += chunk
                    if b"\r\n\r\n" in req_data:
                        header_part, rest = req_data.split(b"\r\n\r\n", 1)
                        content_length = 0
                        for line in header_part.split(b"\r\n"):
                            if line.lower().startswith(b"content-length:"):
                                content_length = int(line.split(b":")[1].strip())
                        if len(rest) >= content_length:
                            break

                upstream_sock.sendall(req_data)

                if self.drop_next:
                    # Wait for upstream response bytes to prove server commit
                    upstream_sock.settimeout(5.0)
                    _ = upstream_sock.recv(4096)
                    # Reset client connection immediately (hard close via RST)
                    client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
                    client_sock.close()
                    upstream_sock.close()
                else:
                    upstream_sock.settimeout(5.0)
                    while True:
                        resp_chunk = upstream_sock.recv(4096)
                        if not resp_chunk:
                            break
                        client_sock.sendall(resp_chunk)
                    client_sock.close()
                    upstream_sock.close()
            except Exception:
                try:
                    client_sock.close()
                except Exception:
                    pass

    def close(self) -> None:
        self._running = False
        try:
            self.server_sock.close()
        except Exception:
            pass
        self._thread.join(timeout=2)


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
        _sigkill(proc_a)

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


def _spec_checksum(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def test_transaction_rollback_before_commit_leaves_no_orphan_reservation_or_state(pg_schema):
    """Reviewer finding 3 / Coordinator diagnosis note 1143:
    Prove that in real PostgreSQL, transaction rollback before commit across
    create, metadata, and advance leaves 0 orphan rows in both entries and
    command_receipts, and allows clean subsequent retry with the exact same
    command_key / Idempotency-Key.
    """
    import psycopg

    dsn, schema = pg_schema
    port = _free_port()
    proc = _spawn_registry_process(port=port, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port)
        token = _strict_jwt()

        def _count_entries(where: str = "") -> int:
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    clause = f" WHERE {where}" if where else ""
                    cur.execute(f"SELECT count(*) FROM {schema}.entries{clause}")
                    return cur.fetchone()[0]

        def _count_receipts(where: str = "") -> int:
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    clause = f" WHERE {where}" if where else ""
                    cur.execute(f"SELECT count(*) FROM {schema}.command_receipts{clause}")
                    return cur.fetchone()[0]

        # 1. Create baseline 1.0.0 entry
        from services.registry.test_service import _valid_spec

        spec_100 = _valid_spec("strat-rb-proof")
        status, init_resp = _http(
            "POST",
            port,
            "/api/registry/entries",
            token=token,
            payload={
                "artifact_type": "strategy_spec",
                "strategy_id": "strat-rb-proof",
                "version": "1.0.0",
                "artifact_state": "draft",
                "lineage": {"source_run_ids": ["run-init"]},
                "checksum": _spec_checksum(spec_100),
                "metadata": {"strategy_spec": spec_100},
            },
        )
        assert status == 200, init_resp
        reg_id = init_resp["entry"]["registry_id"]
        assert _count_entries("payload->>'strategy_id' = 'strat-rb-proof'") == 1

        # A. CREATE ROLLBACK:
        # Out-of-sequence version jump (9.9.9) with no parent linkage must fail
        # lineage check inside create_with_receipt transaction.
        spec_999 = _valid_spec("strat-rb-proof", v=999)
        status, bad_create = _http(
            "POST",
            port,
            "/api/registry/entries",
            token=token,
            headers={"Idempotency-Key": "idemp-rb-create-key"},
            payload={
                "artifact_type": "strategy_spec",
                "strategy_id": "strat-rb-proof",
                "version": "9.9.9",
                "artifact_state": "draft",
                "lineage": {"source_run_ids": ["run-out-of-sequence"]},
                "checksum": _spec_checksum(spec_999),
                "metadata": {"strategy_spec": spec_999},
            },
        )
        assert status in (400, 409), bad_create
        # Verify 0 orphan rows in entries or command_receipts
        assert _count_entries("payload->>'strategy_id' = 'strat-rb-proof' AND payload->>'version' = '9.9.9'") == 0
        assert _count_receipts("payload->>'command_key' = 'idemp-rb-create-key'") == 0

        # Subsequent clean retry with the SAME Idempotency-Key and valid next version (1.0.1) succeeds!
        spec_101 = _valid_spec("strat-rb-proof", v=101)
        status, good_create = _http(
            "POST",
            port,
            "/api/registry/entries",
            token=token,
            headers={"Idempotency-Key": "idemp-rb-create-key"},
            payload={
                "artifact_type": "strategy_spec",
                "strategy_id": "strat-rb-proof",
                "version": "1.0.1",
                "artifact_state": "draft",
                "lineage": {"source_run_ids": ["run-valid-next"], "parent_registry_ids": [reg_id]},
                "checksum": _spec_checksum(spec_101),
                "metadata": {"strategy_spec": spec_101},
            },
        )
        assert status == 200, good_create
        assert _count_entries("payload->>'strategy_id' = 'strat-rb-proof' AND payload->>'version' = '1.0.1'") == 1
        assert _count_receipts("payload->>'command_key' = 'idemp-rb-create-key'") >= 1

        # B. METADATA ROLLBACK:
        # Stale CAS expected_metadata mismatch must fail and roll back.
        status, bad_meta = _http(
            "PATCH",
            port,
            f"/api/registry/entries/{reg_id}/metadata",
            token=token,
            payload={
                "expected_metadata": {"bogus": "stale-mismatch"},
                "metadata": {"will_fail": True},
                "command_key": "cmd-rb-meta-key",
            },
        )
        assert status == 409, bad_meta
        # Verify 0 orphan receipt and metadata was not updated
        assert _count_receipts("payload->>'command_key' = 'cmd-rb-meta-key'") == 0

        # Subsequent retry with the SAME command_key and matching expected_metadata succeeds!
        current_meta = init_resp["entry"]["metadata"]
        good_meta_payload = dict(current_meta or {})
        good_meta_payload["updated_key"] = "committed-successfully"
        status, good_meta = _http(
            "PATCH",
            port,
            f"/api/registry/entries/{reg_id}/metadata",
            token=token,
            payload={
                "expected_metadata": current_meta,
                "metadata": good_meta_payload,
                "command_key": "cmd-rb-meta-key",
            },
        )
        assert status == 200, good_meta
        assert _count_receipts("payload->>'command_key' = 'cmd-rb-meta-key'") == 1

        # C. ADVANCE ROLLBACK:
        # Illegal state transition (draft -> approved) must fail and roll back.
        status, bad_adv = _http(
            "POST",
            port,
            f"/api/registry/entries/{reg_id}/advance",
            token=token,
            payload={
                "target_state": "approved",
                "expected_artifact_state": "draft",
                "command_key": "cmd-rb-adv-key",
            },
        )
        assert status in (400, 409), bad_adv
        # Verify state is still draft and receipt count is 0
        assert _count_entries(f"record_id = '{reg_id}' AND payload->>'artifact_state' = 'draft'") == 1
        assert _count_receipts("payload->>'command_key' = 'cmd-rb-adv-key'") == 0

        # Subsequent clean retry with the SAME command_key and valid transition (draft -> candidate) succeeds!
        status, good_adv = _http(
            "POST",
            port,
            f"/api/registry/entries/{reg_id}/advance",
            token=token,
            payload={
                "target_state": "candidate",
                "expected_artifact_state": "draft",
                "command_key": "cmd-rb-adv-key",
            },
        )
        assert status == 200, good_adv
        assert _count_entries(f"record_id = '{reg_id}' AND payload->>'artifact_state' = 'candidate'") == 1
        assert _count_receipts("payload->>'command_key' = 'cmd-rb-adv-key'") == 1
    finally:
        _stop(proc)


def test_response_loss_crash_window_and_replay_recovery_across_lifecycle(pg_schema):
    """Reviewer finding 3 / Coordinator diagnosis note 1143:
    Prove that in real PostgreSQL across fresh OS processes and SIGKILL crashes:
    1. A committed write survives process kill (crash window after DB commit).
    2. Replay under the original command_key against a brand new process returns
       the exact snapshot committed by that command, even after subsequent
       writes have mutated/drifted the aggregate state.
    3. The durable command receipt independently verifies via
       GET /api/registry/entries/{id}/receipts/{command_key}.
    4. Proven across create, metadata, and advance lifecycle operations.
    """
    dsn, schema = pg_schema

    # ==================== 1. CREATE CRASH WINDOW ====================
    port_a = _free_port()
    proc_a = _spawn_registry_process(port=port_a, dsn=dsn, schema=schema)
    token = _strict_jwt()
    try:
        _wait_for_health(port_a)
        from services.registry.test_service import _valid_spec

        spec_payload = _valid_spec("strat-loss-proof")
        create_body = {
            "artifact_type": "strategy_spec",
            "strategy_id": "strat-loss-proof",
            "version": "1.0.0",
            "artifact_state": "draft",
            "lineage": {"source_run_ids": ["run-init-loss"]},
            "checksum": _spec_checksum(spec_payload),
            "metadata": {"strategy_spec": spec_payload},
        }
        status, create_resp = _http(
            "POST",
            port_a,
            "/api/registry/entries",
            token=token,
            headers={"Idempotency-Key": "loss-create-001"},
            payload=create_body,
        )
        assert status == 200, create_resp
        reg_id = create_resp["entry"]["registry_id"]
    finally:
        # Crash/kill process A immediately after commit (simulating response loss)
        _sigkill(proc_a)

    # Start brand new Process B
    port_b = _free_port()
    proc_b = _spawn_registry_process(port=port_b, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port_b)
        # Induce aggregate state drift via unrelated metadata update
        drift_1_payload = dict(create_resp["entry"]["metadata"] or {})
        drift_1_payload["drift_key"] = "unrelated-drift-1"
        status, drift_meta = _http(
            "PATCH",
            port_b,
            f"/api/registry/entries/{reg_id}/metadata",
            token=token,
            payload={
                "expected_metadata": create_resp["entry"]["metadata"],
                "metadata": drift_1_payload,
                "command_key": "cmd-unrelated-drift-01",
            },
        )
        assert status == 200, drift_meta

        # Replay the original CREATE call against Process B
        status, replay_create = _http(
            "POST",
            port_b,
            "/api/registry/entries",
            token=token,
            headers={"Idempotency-Key": "loss-create-001"},
            payload=create_body,
        )
        assert status == 200, replay_create
        # Replay returns original creation snapshot, NOT the drifted metadata!
        assert replay_create["entry"]["metadata"] == create_resp["entry"]["metadata"]
        assert "drift_key" not in (replay_create["entry"]["metadata"] or {})

        # Independently verify durable receipt reload
        status, rcpt = _http(
            "GET",
            port_b,
            f"/api/registry/entries/{reg_id}/receipts/loss-create-001?command_type=create",
            token=token,
        )
        assert status == 200, rcpt
        assert rcpt["receipt"]["command_key"] == "loss-create-001"
        assert rcpt["receipt"]["committed_entry"]["registry_id"] == reg_id

        # ==================== 2. METADATA CRASH WINDOW ====================
        meta_target_payload = dict(drift_meta["entry"]["metadata"] or {})
        meta_target_payload["target_meta"] = "saved-before-crash"
        status, meta_resp = _http(
            "PATCH",
            port_b,
            f"/api/registry/entries/{reg_id}/metadata",
            token=token,
            payload={
                "expected_metadata": drift_meta["entry"]["metadata"],
                "metadata": meta_target_payload,
                "command_key": "loss-meta-002",
            },
        )
        assert status == 200, meta_resp
    finally:
        # Crash/kill process B immediately after metadata commit
        _sigkill(proc_b)

    # Start brand new Process C
    port_c = _free_port()
    proc_c = _spawn_registry_process(port=port_c, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port_c)
        # Induce aggregate state drift via another metadata update
        drift_2_payload = dict(meta_resp["entry"]["metadata"] or {})
        drift_2_payload["target_meta"] = "drifted-again"
        drift_2_payload["extra"] = 42
        status, drift_meta_2 = _http(
            "PATCH",
            port_c,
            f"/api/registry/entries/{reg_id}/metadata",
            token=token,
            payload={
                "expected_metadata": meta_resp["entry"]["metadata"],
                "metadata": drift_2_payload,
                "command_key": "cmd-unrelated-drift-02",
            },
        )
        assert status == 200, drift_meta_2

        # Replay the original METADATA update against Process C
        status, replay_meta = _http(
            "PATCH",
            port_c,
            f"/api/registry/entries/{reg_id}/metadata",
            token=token,
            payload={
                "expected_metadata": drift_meta["entry"]["metadata"],
                "metadata": meta_target_payload,
                "command_key": "loss-meta-002",
            },
        )
        assert status == 200, replay_meta
        # Replay returns snapshot as committed by loss-meta-002, NOT the drifted-again state!
        assert replay_meta["entry"]["metadata"] == meta_resp["entry"]["metadata"]
        assert replay_meta["entry"]["metadata"]["target_meta"] == "saved-before-crash"
        assert "extra" not in replay_meta["entry"]["metadata"]

        # Independently verify durable receipt reload
        status, rcpt_meta = _http(
            "GET",
            port_c,
            f"/api/registry/entries/{reg_id}/receipts/loss-meta-002?command_type=metadata",
            token=token,
        )
        assert status == 200, rcpt_meta
        assert rcpt_meta["receipt"]["command_key"] == "loss-meta-002"

        # ==================== 3. ADVANCE CRASH WINDOW ====================
        # Entry is currently in state 'draft'. Advance to 'candidate'.
        status, adv_resp = _http(
            "POST",
            port_c,
            f"/api/registry/entries/{reg_id}/advance",
            token=token,
            payload={
                "target_state": "candidate",
                "expected_artifact_state": "draft",
                "command_key": "loss-adv-003",
            },
        )
        assert status == 200, adv_resp
        assert adv_resp["entry"]["artifact_state"] == "candidate"
    finally:
        # Crash/kill process C immediately after state advance commit
        _sigkill(proc_c)

    # Start brand new Process D
    port_d = _free_port()
    proc_d = _spawn_registry_process(port=port_d, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port_d)
        # Advance state further to 'retired' under an unrelated command key
        # (candidate -> retired is a legal transition)
        status, drift_adv = _http(
            "POST",
            port_d,
            f"/api/registry/entries/{reg_id}/advance",
            token=token,
            payload={
                "target_state": "retired",
                "expected_artifact_state": "candidate",
                "command_key": "cmd-unrelated-drift-03",
            },
        )
        assert status == 200, drift_adv
        assert drift_adv["entry"]["artifact_state"] == "retired"

        # Replay the original ADVANCE call (draft -> candidate) against Process D
        status, replay_adv = _http(
            "POST",
            port_d,
            f"/api/registry/entries/{reg_id}/advance",
            token=token,
            payload={
                "target_state": "candidate",
                "expected_artifact_state": "draft",
                "command_key": "loss-adv-003",
            },
        )
        assert status == 200, replay_adv
        # Replay returns snapshot when state was 'candidate' without failing
        # with forbidden transition error against the current 'retired' state!
        assert replay_adv["entry"]["artifact_state"] == "candidate"

        # Independently verify durable receipt reload
        status, rcpt_adv = _http(
            "GET",
            port_d,
            f"/api/registry/entries/{reg_id}/receipts/loss-adv-003?command_type=advance",
            token=token,
        )
        assert status == 200, rcpt_adv
        assert rcpt_adv["receipt"]["command_key"] == "loss-adv-003"
        assert rcpt_adv["receipt"]["committed_entry"]["artifact_state"] == "candidate"

        # Verify that live entry in database is STILL 'retired' (replay did not revert live state)
        status, live_get = _http("GET", port_d, f"/api/registry/entries/{reg_id}", token=token)
        assert status == 200, live_get
        assert live_get["entry"]["artifact_state"] == "retired"
    finally:
        _stop(proc_d)


def test_tcp_proxy_response_loss_and_recovery_across_lifecycle(pg_schema):
    """P2 Reviewer finding 3: Real response-loss testing using a TCP proxy with
    dropped sockets for create, metadata, and advance lifecycle operations:
    1. CREATE: Request sent through TCP proxy with drop_next=True. Proxy forwards
       request to server, server commits entry and receipt to Postgres, then proxy
       resets client connection before response arrives. Client experiences
       transport error. Client re-queries receipt (or replays under same key),
       recovering the exact committed snapshot.
    2. METADATA: Request sent through proxy with drop_next=True. Server commits
       metadata CAS. Proxy drops connection. Client recovers via receipt readback.
    3. ADVANCE: Request sent through proxy with drop_next=True. Server commits
       state advance CAS. Proxy drops connection. Client recovers via receipt readback.
    """
    dsn, schema = pg_schema
    port = _free_port()
    proc = _spawn_registry_process(port=port, dsn=dsn, schema=schema)
    proxy = DroppedSocketProxy(target_port=port)
    token = _strict_jwt()
    try:
        _wait_for_health(port)

        # 1. CREATE THROUGH DROPPED PROXY
        from services.registry.test_service import _valid_spec
        spec_payload = _valid_spec("strat-proxy-loss")
        create_body = {
            "artifact_type": "strategy_spec",
            "strategy_id": "strat-proxy-loss",
            "version": "1.0.0",
            "artifact_state": "draft",
            "lineage": {"source_run_ids": ["run-proxy-1"]},
            "checksum": _spec_checksum(spec_payload),
            "metadata": {"strategy_spec": spec_payload},
        }

        proxy.drop_next = True
        create_failed = False
        try:
            _http(
                "POST",
                proxy.port,
                "/api/registry/entries",
                token=token,
                headers={"Idempotency-Key": "proxy-create-001"},
                payload=create_body,
            )
        except Exception:
            create_failed = True
        assert create_failed is True, "Expected TCP proxy to drop socket on create"

        # Recover CREATE via same-key replay through server (or proxy in normal mode)
        proxy.drop_next = False
        status, replay_create = _http(
            "POST",
            port,
            "/api/registry/entries",
            token=token,
            headers={"Idempotency-Key": "proxy-create-001"},
            payload=create_body,
        )
        assert status == 200, replay_create
        reg_id = replay_create["entry"]["registry_id"]
        assert replay_create["entry"]["strategy_id"] == "strat-proxy-loss"

        # Also independently verify durable receipt reload
        status, rcpt_create = _http(
            "GET",
            port,
            f"/api/registry/entries/{reg_id}/receipts/proxy-create-001?command_type=create",
            token=token,
        )
        assert status == 200, rcpt_create
        assert rcpt_create["receipt"]["command_key"] == "proxy-create-001"

        # 2. METADATA UPDATE THROUGH DROPPED PROXY
        proxy.drop_next = True
        meta_failed = False
        meta_body = {
            "expected_metadata": {"strategy_spec": spec_payload},
            "metadata": {"strategy_spec": spec_payload, "note": "committed-before-proxy-drop"},
            "command_key": "proxy-meta-002",
        }
        try:
            _http(
                "PATCH",
                proxy.port,
                f"/api/registry/entries/{reg_id}/metadata",
                token=token,
                payload=meta_body,
            )
        except Exception:
            meta_failed = True
        assert meta_failed is True, "Expected TCP proxy to drop socket on metadata update"

        # Recover via receipt readback
        proxy.drop_next = False
        status, rcpt_meta = _http(
            "GET",
            port,
            f"/api/registry/entries/{reg_id}/receipts/proxy-meta-002?command_type=metadata",
            token=token,
        )
        assert status == 200, rcpt_meta
        assert rcpt_meta["receipt"]["committed_entry"]["metadata"]["note"] == "committed-before-proxy-drop"

        # 3. ADVANCE STATE THROUGH DROPPED PROXY
        proxy.drop_next = True
        adv_failed = False
        adv_body = {
            "target_state": "candidate",
            "expected_artifact_state": "draft",
            "command_key": "proxy-adv-003",
        }
        try:
            _http(
                "POST",
                proxy.port,
                f"/api/registry/entries/{reg_id}/advance",
                token=token,
                payload=adv_body,
            )
        except Exception:
            adv_failed = True
        assert adv_failed is True, "Expected TCP proxy to drop socket on advance"

        # Recover via receipt readback
        proxy.drop_next = False
        status, rcpt_adv = _http(
            "GET",
            port,
            f"/api/registry/entries/{reg_id}/receipts/proxy-adv-003?command_type=advance",
            token=token,
        )
        assert status == 200, rcpt_adv
        assert rcpt_adv["receipt"]["committed_entry"]["artifact_state"] == "candidate"

        # Verify entry readback
        status, final_get = _http("GET", port, f"/api/registry/entries/{reg_id}", token=token)
        assert status == 200, final_get
        assert final_get["entry"]["artifact_state"] == "candidate"
        assert final_get["entry"]["metadata"]["note"] == "committed-before-proxy-drop"

    finally:
        proxy.close()
        _sigkill(proc)


def test_strategy_command_adapter_rapid_same_second_mutation_and_replay(pg_schema):
    """Review finding 1 & 2: StrategyCommandAdapter executes against real
    Postgres-backed Registry process with strict authentication, performing
    rapid same-second metadata update (no sleep) and confirming metadata_updated
    without false COMMIT_TIME_UNCHANGED rejection, followed by verified replay."""
    from unittest.mock import patch
    from services.control_plane.bff.command_adapters.strategy_adapter import StrategyCommandAdapter

    dsn, schema = pg_schema
    port = _free_port()
    proc = _spawn_registry_process(port=port, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port)
        token = _strict_jwt()
        status, draft = _http(
            "POST",
            port,
            "/api/registry/entries",
            token=token,
            payload={"name": "Rapid Adapter Integration Strategy"},
            headers={"Idempotency-Key": "rapid-adapter-create"},
        )
        assert status == 200, draft
        entry = draft["entry"]

        env = {
            "PANTHEON_BFF_AUTH_MODE": "strict",
            "PANTHEON_BFF_JWT_SECRET": _JWT_SECRET,
            "PANTHEON_BFF_JWT_ISSUER": "registry-durability-tests",
            "PANTHEON_BFF_JWT_AUDIENCE": "registry-svc",
            "PANTHEON_REGISTRY_API_URL": f"http://127.0.0.1:{port}",
        }
        with patch.dict(os.environ, env):
            adapter = StrategyCommandAdapter()
            args = {
                "entity_type": "strategy",
                "strategy_id": entry["strategy_id"],
                "registry_id": entry["registry_id"],
                "action_id": "update_params",
                "expected_metadata": entry["metadata"],
                "metadata": dict(entry["metadata"] or {}, note="rapid-same-second-success"),
            }
            # Execute rapidly without sleep
            result = adapter.execute("cmd-rapid-meta", "StrategyAction", args, auth_token=token)
            assert result["status"] == "metadata_updated"
            assert result["authoritative_readback"]["metadata"]["note"] == "rapid-same-second-success"
            assert result["idempotent_replay"] is False

            # Immediate replay under same command_key
            replay = adapter.execute("cmd-rapid-meta", "StrategyAction", args, auth_token=token)
            assert replay["status"] == "metadata_updated"
            assert replay["authoritative_readback"]["metadata"]["note"] == "rapid-same-second-success"
            assert replay["idempotent_replay"] is True
    finally:
        _stop(proc)


def test_strategy_spec_parent_lineage_and_base_checksum_enforced_in_real_process(pg_schema):
    """Review finding 3: Real Postgres process enforces parent linkage:
    rejects mismatched base_checksum (409), missing parent (400),
    version downgrade (400), and stale base parent (409)."""
    dsn, schema = pg_schema
    port = _free_port()
    proc = _spawn_registry_process(port=port, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port)
        token = _strict_jwt()
        from services.registry.test_service import _valid_spec

        sid = "strat-lineage-proof"
        first_body = {
            "strategy_id": sid,
            "registry_id": f"reg-{sid}-100",
            "version": "1.0.0",
            "lineage": {"source_run_ids": ["run-001"]},
            "strategy_spec": _valid_spec(sid),
        }
        st, first = _http("POST", port, "/api/registry/strategy-specs", token=token, payload=first_body)
        assert st == 200, first
        parent_reg_id = first["entry"]["registry_id"]
        parent_checksum = first["entry"]["checksum"]

        # 1. Reject mismatched base_checksum (409)
        bad_checksum_body = {
            "strategy_id": sid,
            "registry_id": f"reg-{sid}-101-bad-cs",
            "version": "1.0.1",
            "lineage": {"parent_registry_ids": [parent_reg_id]},
            "base_checksum": "sha256:" + "0" * 64,
            "strategy_spec": _valid_spec(sid, v=101),
        }
        st, res = _http("POST", port, "/api/registry/strategy-specs", token=token, payload=bad_checksum_body)
        assert st == 409, (st, res)
        assert "base_checksum" in res.get("detail", "")

        # 2. Reject missing parent (400)
        missing_parent_body = {
            "strategy_id": sid,
            "registry_id": f"reg-{sid}-101-missing-parent",
            "version": "1.0.1",
            "lineage": {"parent_registry_ids": ["reg-nonexistent-999"]},
            "strategy_spec": _valid_spec(sid, v=101),
        }
        st, res = _http("POST", port, "/api/registry/strategy-specs", token=token, payload=missing_parent_body)
        assert st == 400, (st, res)

        # 3. Reject version downgrade (400)
        downgrade_body = {
            "strategy_id": sid,
            "registry_id": f"reg-{sid}-090",
            "version": "0.9.0",
            "lineage": {"parent_registry_ids": [parent_reg_id]},
            "strategy_spec": _valid_spec(sid, v=90),
        }
        st, res = _http("POST", port, "/api/registry/strategy-specs", token=token, payload=downgrade_body)
        assert st == 400, (st, res)

        # 4. Valid revision with matching base_checksum succeeds (200)
        valid_101_body = {
            "strategy_id": sid,
            "registry_id": f"reg-{sid}-101",
            "version": "1.0.1",
            "lineage": {"parent_registry_ids": [parent_reg_id]},
            "base_checksum": parent_checksum,
            "strategy_spec": _valid_spec(sid, v=101),
        }
        st, rev_101 = _http("POST", port, "/api/registry/strategy-specs", token=token, payload=valid_101_body)
        assert st == 200, rev_101
        rev_101_id = rev_101["entry"]["registry_id"]
        rev_101_cs = rev_101["entry"]["checksum"]

        # 5. Reject stale parent (parenting to 1.0.0 when 1.0.1 is latest) (409)
        stale_parent_body = {
            "strategy_id": sid,
            "registry_id": f"reg-{sid}-102-stale",
            "version": "1.0.2",
            "lineage": {"parent_registry_ids": [parent_reg_id]},
            "base_checksum": parent_checksum,
            "strategy_spec": _valid_spec(sid, v=102),
        }
        st, res = _http("POST", port, "/api/registry/strategy-specs", token=token, payload=stale_parent_body)
        assert st == 409, (st, res)
        assert "stale base version" in res.get("detail", "")

        # 6. Valid revision from 1.0.1 succeeds
        valid_102_body = {
            "strategy_id": sid,
            "registry_id": f"reg-{sid}-102",
            "version": "1.0.2",
            "lineage": {"parent_registry_ids": [rev_101_id]},
            "base_checksum": rev_101_cs,
            "strategy_spec": _valid_spec(sid, v=102),
        }
        st, rev_102 = _http("POST", port, "/api/registry/strategy-specs", token=token, payload=valid_102_body)
        assert st == 200, rev_102
    finally:
        _stop(proc)


def test_concurrent_parent_linked_writers_on_typed_and_keyed_routes(pg_schema):
    """Review finding 3: Test concurrent parent-linked writers racing from the same base.
    Under the serialized advisory lock, exactly one writer succeeds in
    advancing the strategy revision from that base, while concurrent writers
    from the now-stale base are rejected with 409 Conflict."""
    dsn, schema = pg_schema
    port = _free_port()
    proc = _spawn_registry_process(port=port, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port)
        token = _strict_jwt()
        from services.registry.test_service import _valid_spec

        # Test A: Concurrent writers on typed route (POST /api/registry/strategy-specs)
        sid_typed = "strat-conc-typed"
        base_body = {
            "strategy_id": sid_typed,
            "registry_id": f"reg-{sid_typed}-100",
            "version": "1.0.0",
            "lineage": {"source_run_ids": ["run-001"]},
            "strategy_spec": _valid_spec(sid_typed),
        }
        st, base_res = _http("POST", port, "/api/registry/strategy-specs", token=token, payload=base_body)
        assert st == 200, base_res
        base_id = base_res["entry"]["registry_id"]
        base_cs = base_res["entry"]["checksum"]

        results_typed = []
        barrier = threading.Barrier(2)

        def _writer_typed(ver, suffix):
            body = {
                "strategy_id": sid_typed,
                "registry_id": f"reg-{sid_typed}-{suffix}",
                "version": ver,
                "lineage": {"parent_registry_ids": [base_id]},
                "base_checksum": base_cs,
                "strategy_spec": _valid_spec(sid_typed, v=int(ver.replace(".", ""))),
            }
            try:
                barrier.wait(timeout=5)
            except Exception:
                pass
            status, resp = _http("POST", port, "/api/registry/strategy-specs", token=token, payload=body)
            results_typed.append((status, resp))

        t1 = threading.Thread(target=_writer_typed, args=("1.0.1", "a"))
        t2 = threading.Thread(target=_writer_typed, args=("2.0.0", "b"))
        t1.start(); t2.start()
        t1.join(timeout=10); t2.join(timeout=10)

        statuses_typed = sorted([s for s, _ in results_typed])
        assert statuses_typed == [200, 409], f"Expected exactly one 200 and one 409, got {statuses_typed}"

        # Test B: Concurrent writers on generic keyed route (POST /api/registry/entries with Idempotency-Key)
        sid_keyed = "strat-conc-keyed"
        base_keyed_body = {
            "artifact_type": "strategy_spec",
            "strategy_id": sid_keyed,
            "version": "1.0.0",
            "artifact_state": "draft",
            "lineage": {"source_run_ids": ["run-001"]},
            "strategy_spec": _valid_spec(sid_keyed),
        }
        st, base_keyed_res = _http(
            "POST", port, "/api/registry/entries",
            token=token, payload=base_keyed_body,
            headers={"Idempotency-Key": f"create-{sid_keyed}"},
        )
        assert st == 200, base_keyed_res
        base_keyed_id = base_keyed_res["entry"]["registry_id"]
        base_keyed_cs = base_keyed_res["entry"]["checksum"]

        results_keyed = []
        barrier_keyed = threading.Barrier(2)

        def _writer_keyed(ver, key_suffix):
            body = {
                "artifact_type": "strategy_spec",
                "strategy_id": sid_keyed,
                "version": ver,
                "artifact_state": "draft",
                "lineage": {"parent_registry_ids": [base_keyed_id]},
                "base_checksum": base_keyed_cs,
                "strategy_spec": _valid_spec(sid_keyed, v=int(ver.replace(".", ""))),
            }
            try:
                barrier_keyed.wait(timeout=5)
            except Exception:
                pass
            status, resp = _http(
                "POST", port, "/api/registry/entries",
                token=token, payload=body,
                headers={"Idempotency-Key": f"rev-{sid_keyed}-{key_suffix}"},
            )
            results_keyed.append((status, resp))

        tk1 = threading.Thread(target=_writer_keyed, args=("1.0.1", "k1"))
        tk2 = threading.Thread(target=_writer_keyed, args=("2.0.0", "k2"))
        tk1.start(); tk2.start()
        tk1.join(timeout=10); tk2.join(timeout=10)

        statuses_keyed = sorted([s for s, _ in results_keyed])
        assert statuses_keyed == [200, 409], f"Expected exactly one 200 and one 409 on keyed route, got {statuses_keyed}"

    finally:
        _stop(proc)


@pytest.mark.parametrize("route", ["/api/registry/strategy-specs", "/api/registry/entries"])
def test_revision_requires_caller_base(pg_schema, route):
    """Prove that a noninitial StrategySpec revision without caller parent/base identity
    is rejected (400) on both dedicated /strategy-specs and generic /entries routes,
    preventing an intervening revision race from committing undetected."""
    from services.registry.test_service import _valid_spec

    dsn, schema = pg_schema
    port = _free_port()
    proc = _spawn_registry_process(port=port, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port)
        token = _strict_jwt()
        sid = f"rev-unbound-{uuid4().hex[:6]}"
        statuses = []
        for version, content in [("1.0.0", 100), ("1.0.1", 101), ("2.0.0", 200)]:
            payload = {
                "strategy_id": sid,
                "version": version,
                "artifact_type": "strategy_spec",
                "lineage": {"source_run_ids": ["run-base-100"]},
                "strategy_spec": _valid_spec(sid, v=content),
            }
            status, body = _http(
                "POST",
                port,
                route,
                token=token,
                payload=payload,
                headers={"Idempotency-Key": f"review-{sid}-{version}"},
            )
            statuses.append(status)
        assert statuses[0] == 200
        assert statuses[1] in (400, 409, 422), f"Revision accepted without caller parent/base binding: {statuses}"
        assert statuses[2] in (400, 409, 422), f"Revision accepted without caller parent/base binding: {statuses}"
    finally:
        _stop(proc)


@pytest.mark.parametrize("route", ["/api/registry/strategy-specs", "/api/registry/entries"])
def test_stale_digest_cannot_bind_a_different_revision(pg_schema, route):
    """Prove that content checksum alone is not revision CAS: submitting a revision
    with only base_checksum observed from 1.0.0 cannot adopt an intervening 1.0.1
    having identical content; caller must bind an unambiguous parent_registry_ids."""
    from services.registry.test_service import _valid_spec

    dsn, schema = pg_schema
    port = _free_port()
    proc = _spawn_registry_process(port=port, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port)
        token = _strict_jwt()
        sid = f"review-stale-{uuid4().hex[:8]}"
        content = _valid_spec(sid)

        def submit(version, **extra):
            return _http(
                "POST",
                port,
                route,
                token=token,
                payload={
                    "artifact_type": "strategy_spec",
                    "strategy_id": sid,
                    "version": version,
                    "lineage": {"source_run_ids": ["review-run"]},
                    "strategy_spec": content,
                    **extra,
                },
                headers={"Idempotency-Key": sid + version},
            )

        status, first = submit("1.0.0")
        assert status == 200, first
        digest = first["entry"]["checksum"]
        status, second = submit(
            "1.0.1",
            base_checksum=digest,
            lineage={"parent_registry_ids": [first["entry"]["registry_id"]]},
        )
        assert status == 200, second
        assert second["entry"]["checksum"] == digest
        # Request prepared against 1.0.0 before 1.0.1 committed. A digest
        # alone cannot identify which of these immutable versions was read.
        status, stale = submit("2.0.0", base_checksum=digest)
        assert status in (400, 409, 422), (
            "stale base-only revision accepted after an intervening same-content "
            f'revision: status={status}, version={stale.get("entry", {}).get("version")}'
        )
    finally:
        _stop(proc)


@pytest.mark.parametrize("keyed", [False, True])
def test_generic_reference_revision_cannot_skip_caller_base(pg_schema, keyed):
    """Prove that noninitial StrategySpec reference revisions (e.g. object_store)
    cannot bypass caller parent/base revision invariants on generic /entries
    (both keyed and unkeyed), mirroring the dedicated /strategy-specs route."""
    from services.registry.test_service import _valid_spec

    dsn, schema = pg_schema
    port = _free_port()
    proc = _spawn_registry_process(port=port, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port)
        token = _strict_jwt()
        sid = f"review-reference-{uuid4().hex[:8]}"
        status, first = _http(
            "POST",
            port,
            "/api/registry/strategy-specs",
            token=token,
            payload={
                "strategy_id": sid,
                "version": "1.0.0",
                "lineage": {"source_run_ids": ["review-run"]},
                "strategy_spec": _valid_spec(sid),
            },
        )
        assert status == 200, first
        reference = {
            "artifact_type": "strategy_spec",
            "strategy_id": sid,
            "version": "9.9.9",
            "checksum": first["entry"]["checksum"],
            "storage_ref": {"backend": "object_store", "path": "review/spec.json"},
            "lineage": {"source_run_ids": ["review-run"]},
        }
        typed_status, typed_body = _http(
            "POST",
            port,
            "/api/registry/strategy-specs",
            token=token,
            payload=reference,
        )
        assert typed_status in (400, 409, 422), typed_body
        status, second = _http(
            "POST",
            port,
            "/api/registry/entries",
            token=token,
            payload=reference,
            headers={"Idempotency-Key": sid} if keyed else {},
        )
        read_status, versions = _http(
            "GET",
            port,
            f"/api/registry/strategies/{sid}/strategy-specs",
            token=token,
        )
        assert read_status == 200, versions
        assert status in (400, 409, 422), (
            f"noninitial reference revision bypassed caller base: status={status}, "
            f'version={second.get("entry", {}).get("version")}'
        )
    finally:
        _stop(proc)


@pytest.mark.parametrize("keyed", [False, True])
@pytest.mark.parametrize("inline", [False, True])
def test_typed_revision_cannot_claim_name_only_to_skip_base(pg_schema, keyed, inline):
    """Reviewer probe regression: prove that a caller cannot pass
    metadata.draft_kind = 'name_only' on a typed StrategySpec revision
    (keyed/unkeyed, inline/storage_ref) to bypass parent/base validation."""
    from services.registry.test_service import _valid_spec

    dsn, schema = pg_schema
    port = _free_port()
    proc = _spawn_registry_process(port=port, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port)
        token = _strict_jwt()
        sid = f"review-marker-{uuid4().hex[:8]}"
        content = _valid_spec(sid)
        status, first = _http(
            "POST",
            port,
            "/api/registry/strategy-specs",
            token=token,
            payload={
                "strategy_id": sid,
                "version": "1.0.0",
                "lineage": {"source_run_ids": ["review-run"]},
                "strategy_spec": content,
            },
        )
        assert status == 200, first
        payload = {
            "artifact_type": "strategy_spec",
            "strategy_id": sid,
            "version": "9.9.9",
            "lineage": {"source_run_ids": ["review-run"]},
            "metadata": {"draft_kind": "name_only"},
        }
        if inline:
            payload["strategy_spec"] = content
        else:
            payload["checksum"] = first["entry"]["checksum"]
            payload["storage_ref"] = {"backend": "object_store", "path": "review/spec.json"}
        status, result = _http(
            "POST",
            port,
            "/api/registry/entries",
            token=token,
            payload=payload,
            headers={"Idempotency-Key": sid} if keyed else {},
        )
        if status == 200:
            read_status, persisted = _http(
                "GET",
                port,
                "/api/registry/entries/" + result["entry"]["registry_id"],
                token=token,
            )
            assert read_status == 200
            assert persisted["entry"]["version"] == "9.9.9"
        assert status in (400, 409, 422), (
            f"caller-controlled draft_kind bypassed revision base: HTTP {status}; "
            f'durably stored version={result.get("entry", {}).get("version")}; '
            f"inline={inline}, keyed={keyed}"
        )

        # Durable readback check: verify that version 9.9.9 was NOT durably written
        read_status, versions = _http(
            "GET",
            port,
            f"/api/registry/strategies/{sid}/strategy-specs",
            token=token,
        )
        assert read_status == 200, versions
        stored_versions = [v["entry"]["version"] for v in versions]
        assert "9.9.9" not in stored_versions
    finally:
        _stop(proc)


@pytest.mark.parametrize("keyed", [False, True])
def test_name_only_draft_real_process_positive_and_durable_readback(pg_schema, keyed):
    """Positive capability counterpart: prove genuine name-only draft creation
    (name alone, un-typed) succeeds with stable identity, sets draft_kind='name_only'
    server-side, and reads back durably across a brand-new OS process."""
    dsn, schema = pg_schema
    port_a = _free_port()
    proc_a = _spawn_registry_process(port=port_a, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port_a)
        token = _strict_jwt()
        draft_name = f"My Durable Idea {uuid4().hex[:6]}"
        headers = {"Idempotency-Key": f"draft-key-{uuid4().hex[:8]}"} if keyed else {}
        status, created = _http(
            "POST",
            port_a,
            "/api/registry/entries",
            token=token,
            payload={"name": draft_name},
            headers=headers,
        )
        assert status == 200, created
        entry = created["entry"]
        reg_id = entry["registry_id"]
        sid = entry["strategy_id"]
        assert sid.startswith("draft-")
        assert entry["version"] == "0.0.1"
        assert entry["artifact_state"] == "draft"
        assert entry["metadata"]["name"] == draft_name
        assert entry["metadata"]["draft_kind"] == "name_only"

        # If keyed, verify replay idempotency returns identical registry_id
        if keyed:
            status_rep, replayed = _http(
                "POST",
                port_a,
                "/api/registry/entries",
                token=token,
                payload={"name": draft_name},
                headers=headers,
            )
            assert status_rep == 200, replayed
            assert replayed["entry"]["registry_id"] == reg_id
    finally:
        _stop(proc_a)

    # Spawn fresh process and prove durable readback
    port_b = _free_port()
    proc_b = _spawn_registry_process(port=port_b, dsn=dsn, schema=schema)
    try:
        _wait_for_health(port_b)
        token = _strict_jwt()
        status, readback = _http(
            "GET",
            port_b,
            f"/api/registry/entries/{reg_id}",
            token=token,
        )
        assert status == 200, readback
        rb_entry = readback["entry"]
        assert rb_entry["registry_id"] == reg_id
        assert rb_entry["strategy_id"] == sid
        assert rb_entry["metadata"]["draft_kind"] == "name_only"
        assert rb_entry["metadata"]["name"] == draft_name
    finally:
        _stop(proc_b)


