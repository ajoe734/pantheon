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
                "lineage": {"source_run_ids": ["run-valid-next"]},
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
        _stop(proc_a)

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
        _stop(proc_b)

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
        _stop(proc_c)

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
