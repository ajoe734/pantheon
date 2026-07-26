"""OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001 — strict-auth infrastructure health.

These are real HTTP-surface tests against ``services.telemetry.main`` wired with
an authoritative RuntimeBinding store, the real telemetry schema file, and a
real durable admission ledger on disk. They prove the five acceptance clauses of
the task:

1. infrastructure health is admitted by its own authoritative non-trading
   schema and never requires or invents a RuntimeBinding;
2. a strict service JWT plus tenant binding plus an allowlisted producer scope
   is required, and probe-shaped events cannot bypass trading validation;
3. admission is durable and idempotent by stable event ID across retries,
   restarts, and replicas;
4. trading telemetry binding and lineage validation is unchanged and fails
   closed;
5. the strict-auth route covers valid ingest, missing token, wrong tenant,
   wrong producer, runtime-health spoof, restart, and duplicate replay.

Run with:
    python3 -m pytest services/telemetry/test_infrastructure_health_ingest.py
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import multiprocessing
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import services.telemetry.main as _main
from services.runtime_auth_inbound import encode_jwt_hs256
from services.telemetry.buffer import DurableBuffer
from services.telemetry.dead_letter import TAG_BINDING_MISMATCH, TAG_SCHEMA_VIOLATION
from services.telemetry.ingest_svc import (
    INFRASTRUCTURE_HEALTH_LEDGER_FILENAME,
    INFRASTRUCTURE_HEALTH_SCHEMA_VERSION,
    InfrastructureHealthAdmissionLedger,
    TelemetryIngestService,
    infrastructure_health_fingerprint,
)
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SCHEMA_PATH = str(Path(__file__).resolve().parent / "telemetry_event.schema.json")

_TENANT_ID = "tenant-alpha"
_OTHER_TENANT = "tenant-beta"
_PRODUCER = "control-plane-bff"
_OTHER_PRODUCER = "rogue-probe-agent"
_JWT_SECRET = "infrastructure-health-strict-test-secret"

_STRICT_ENV = {
    "PANTHEON_TELEMETRY_AUTH_MODE": "strict",
    "PANTHEON_TELEMETRY_JWT_SECRET": _JWT_SECRET,
    "PANTHEON_TELEMETRY_INFRA_PRODUCERS": _PRODUCER,
}

# Short enough to observe reservation recovery inside a test, long enough that a
# healthy admission never races its own lease.
_CRASH_LEASE_SECONDS = 2.0

_BROKER_FILENAME = "infrastructure_health_broker.jsonl"

_KNOWN_BINDING_ID = "infra-authority-binding-001"
_KNOWN_BINDING = types.SimpleNamespace(
    binding_id=_KNOWN_BINDING_ID,
    runtime_id="lean-worker-1",
    capital_pool_id="pool-alpha",
    artifact_id="artifact-123",
    artifact_version="1.0.0",
    plan_id="plan-456",
    persona_capital_binding_id="pcb-789",
    deployment_mode="paper",
    execution_mode="paper",
    effective_at="2026-01-01T00:00:00Z",
    retired_at=None,
)


class _StubBindingStore:
    """Authoritative binding store: one known binding, everything else unknown."""

    def get_binding(self, binding_id: str):
        return _KNOWN_BINDING if binding_id == _KNOWN_BINDING_ID else None


# ---------------------------------------------------------------------------
# Durable broker contract stand-in
# ---------------------------------------------------------------------------


class _DurableFileBroker(DurableBuffer):
    """Test-only stand-in for the deployed durable broker (JetStream/Redis).

    It is defined in this test module — never in ``buffer.create_buffer`` — so
    no deployment configuration can select it and it can never become a
    production volatile bypass. What it does provide is the half of the broker
    contract this task depends on, honestly:

    * ``put`` appends the event to an append-only log and ``fsync``s it before
      returning ``True``, so the return of ``put`` is a real durability point:
      a ``SIGKILL`` one instruction later still leaves the event readable by
      another process;
    * an event stays readable until the canonical writer acknowledges it, so an
      un-acked event is redelivered after a crash instead of being lost;
    * concurrent replicas serialise on a POSIX advisory lock over the same log,
      exactly like two ingest processes sharing one stream.

    ``is_durable()`` is therefore the truth, not a claim: everything a caller is
    told about admission is backed by an fsync'd record on disk.
    """

    STATE_ENQUEUED = "enqueued"
    STATE_ACKED = "acked"

    def __init__(self, path: str, maxsize: int = 100_000):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)
        self._maxsize = maxsize
        self._closed = False
        self._in_flight: list[tuple[int, dict[str, Any]]] = []

    # -- durable log primitives --

    def _append_locked(self, record: dict[str, Any]) -> None:
        with self._path.open("r+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0, os.SEEK_END)
                line = json.dumps(
                    record, separators=(",", ":"), sort_keys=True
                ).encode("utf-8")
                handle.write(line + b"\n")
                handle.flush()
                # The durability point. A crash after this fsync cannot lose the
                # event, which is exactly what a broker ACK promises.
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _records(self) -> list[dict[str, Any]]:
        return _broker_records(str(self._path))

    def _pending_records(self) -> list[dict[str, Any]]:
        return _broker_pending_records(str(self._path))

    def _next_receipt_id(self) -> int:
        return len(self._records())

    # -- DurableBuffer contract --

    async def start(self) -> None:
        return None

    async def put(self, event: dict[str, Any], timeout: Optional[float] = None) -> bool:
        if self._closed:
            return False
        if len(self._pending_records()) >= self._maxsize:
            return False
        self._append_locked(
            {
                "receipt_id": self._next_receipt_id(),
                "state": self.STATE_ENQUEUED,
                "event": event,
            }
        )
        return True

    async def get(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        deadline = time.monotonic() + (timeout if timeout is not None else 1.0)
        while True:
            claimed = {receipt for receipt, _ in self._in_flight}
            for record in self._pending_records():
                if record["receipt_id"] not in claimed:
                    self._in_flight.append((record["receipt_id"], record["event"]))
                    return record["event"]
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.01)

    async def ack(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            for index, (receipt_id, in_flight_event) in enumerate(self._in_flight):
                if in_flight_event == event:
                    self._append_locked(
                        {"receipt_id": receipt_id, "state": self.STATE_ACKED}
                    )
                    self._in_flight.pop(index)
                    break

    async def release(self, events: list[dict[str, Any]]) -> bool:
        for event in events:
            for index, (_, in_flight_event) in enumerate(self._in_flight):
                if in_flight_event == event:
                    self._in_flight.pop(index)
                    break
        return True

    def is_durable(self) -> bool:
        return True

    def size(self) -> int:
        return len(self._pending_records())

    def capacity(self) -> Optional[int]:
        return self._maxsize

    async def close(self) -> None:
        self._closed = True

    def is_closed(self) -> bool:
        return self._closed

    async def drain(self) -> list[dict[str, Any]]:
        claimed = {receipt for receipt, _ in self._in_flight}
        drained = []
        for record in self._pending_records():
            if record["receipt_id"] not in claimed:
                self._in_flight.append((record["receipt_id"], record["event"]))
                drained.append(record["event"])
        return drained


def _broker_records(path: str) -> list[dict]:
    target = Path(path)
    if not target.exists():
        return []
    return [
        json.loads(line)
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _broker_pending_records(path: str) -> list[dict]:
    """Records that reached durable storage and were never acknowledged."""
    enqueued: dict[int, dict] = {}
    for record in _broker_records(path):
        if record.get("state") == _DurableFileBroker.STATE_ENQUEUED:
            enqueued[record["receipt_id"]] = record
        elif record.get("state") == _DurableFileBroker.STATE_ACKED:
            enqueued.pop(record["receipt_id"], None)
    return list(enqueued.values())


def _broker_durable_events(path: str, event_id: str) -> list[dict]:
    """Every copy of *event_id* that ever reached durable storage."""
    return [
        record["event"]
        for record in _broker_records(path)
        if record.get("state") == _DurableFileBroker.STATE_ENQUEUED
        and record.get("event", {}).get("event_id") == event_id
    ]


def _broker_retained_events(path: str, event_id: str) -> list[dict]:
    """Copies of *event_id* still awaiting the canonical write."""
    return [
        record["event"]
        for record in _broker_pending_records(path)
        if record.get("event", {}).get("event_id") == event_id
    ]


def _service_token(
    *,
    tenants=(_TENANT_ID,),
    producers=(_PRODUCER,),
    roles=("service",),
    subject="control-plane-bff-probe",
) -> str:
    claims: dict = {"sub": subject, "roles": list(roles)}
    if tenants is not None:
        claims["allowed_tenants"] = list(tenants)
    if producers is not None:
        claims["allowed_producers"] = list(producers)
    return encode_jwt_hs256(claims, secret=_JWT_SECRET)


def _infra_event(event_id: str = "infra-health-001", **overrides) -> dict:
    event = {
        "schema_version": INFRASTRUCTURE_HEALTH_SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": "infrastructure_health",
        "created_at": "2026-07-26T12:00:00Z",
        "tenant_id": _TENANT_ID,
        "producer": _PRODUCER,
        "component": {
            "service_name": "runtime-manager",
            "component_kind": "http_service",
            "endpoint": "http://runtime-manager:8081/__health__",
        },
        "health_status": "degraded",
        "severity": "warning",
        "observation": {
            "probe_kind": "interval_probe",
            "observed_at": "2026-07-26T12:00:00Z",
            "window_seconds": 60,
            "sample_count": 10,
            "failure_count": 4,
            "consecutive_failures": 2,
            "error_rate": 0.4,
            "latency_ms": 1200.5,
        },
    }
    event.update(overrides)
    return event


def _trading_event(event_id: str = "infra-authority-trading-001", **overrides) -> dict:
    event = {
        "tenant_id": _TENANT_ID,
        "event_id": event_id,
        "event_type": "pnl_snapshot",
        "created_at": "2026-04-15T12:00:00Z",
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": _KNOWN_BINDING_ID,
        "runtime_id": "lean-worker-1",
        "capital_pool_id": "pool-alpha",
        "artifact_id": "artifact-123",
        "artifact_version": "1.0.0",
        "plan_id": "plan-456",
        "persona_capital_binding_id": "pcb-789",
        "target": {"strategy_id": "infra-authority-strategy"},
        "metrics": {"pnl": 100.0},
    }
    event.update(overrides)
    return event


def _ledger_records(path: str) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# Roles a racing replica can legitimately end up in. The distinction matters:
# the ledger answers the literal word "committed" both to the one reservation
# owner that writes the receipt and to a replica that began after that receipt
# was already durable. Only the first is an admission; the second is an
# idempotent duplicate, and a barrier cannot prevent it — it lines the replicas
# up at the start, it does not stop the OS from scheduling one of them late.
_ROLE_COMMIT_OWNER = "commit_owner"
_ROLE_POST_RECEIPT_DUPLICATE = "post_receipt_duplicate"
_ROLE_IN_FLIGHT = "in_flight"
_LOSER_ROLES = frozenset({_ROLE_POST_RECEIPT_DUPLICATE, _ROLE_IN_FLIGHT})


def _replica_admit(
    ledger_path: str, broker_path: str, event: dict, barrier, connection
) -> None:
    """Child-process replica: run one full two-phase admission of *event*.

    Reports a structured outcome rather than the bare ledger word, so the parent
    can name the single reservation owner that produced the durable receipt and
    separate it from replicas that legitimately observed that receipt afterwards.

    The owner follows the real ingest ordering: durable enqueue receipt first,
    then commit. A loser never enqueues, so the durable log is also a proof that
    exactly one admission happened.
    """
    report: dict[str, Any] = {
        "role": None,
        "begin": None,
        "commit": None,
        "token": None,
        "durable_put": False,
    }
    try:
        ledger = InfrastructureHealthAdmissionLedger(ledger_path)
        broker = _DurableFileBroker(broker_path)
        fingerprint = infrastructure_health_fingerprint(event)
        if barrier is not None:
            barrier.wait(timeout=30)
        reservation = ledger.begin(event["event_id"], fingerprint)
        report["begin"] = reservation.outcome
        if reservation.outcome == ledger.OUTCOME_RESERVED:
            report["token"] = reservation.token
            report["durable_put"] = asyncio.run(broker.put(event))
            report["commit"] = ledger.commit(
                event["event_id"], fingerprint, reservation.token
            )
            report["role"] = (
                _ROLE_COMMIT_OWNER
                if report["commit"] == ledger.OUTCOME_COMMITTED
                else f"owner_{report['commit']}"
            )
        elif reservation.outcome == ledger.OUTCOME_COMMITTED:
            report["role"] = _ROLE_POST_RECEIPT_DUPLICATE
        elif reservation.outcome == ledger.OUTCOME_IN_FLIGHT:
            report["role"] = _ROLE_IN_FLIGHT
        else:
            report["role"] = f"unexpected_{reservation.outcome}"
    except Exception as exc:  # noqa: BLE001
        report["role"] = f"error:{exc}"
    connection.send(report)
    connection.close()


def _replica_reserve_then_hang(ledger_path: str, event: dict, connection) -> None:
    """Child-process replica: reserve, report, then hang until SIGKILL.

    This reproduces a crash in the window between the durable reservation and
    the durable enqueue, so the parent can prove the claim is recoverable rather
    than a permanently stuck admission.
    """
    ledger = InfrastructureHealthAdmissionLedger(ledger_path, lease_seconds=_CRASH_LEASE_SECONDS)
    reservation = ledger.begin(
        event["event_id"], infrastructure_health_fingerprint(event)
    )
    connection.send(reservation.outcome)
    connection.close()
    while True:  # pragma: no cover - the parent SIGKILLs this process
        time.sleep(0.2)


_CRASH_BEFORE_PUT = "before_put"
_CRASH_AFTER_DURABLE_PUT = "after_durable_put"
_CRASH_AFTER_COMMIT = "after_commit"


def _crash_matrix_child(
    storage_dir: str,
    ledger_path: str,
    broker_path: str,
    event: dict,
    crash_point: str,
    connection,
) -> None:
    """Child process: run the real admission path and stop dead at *crash_point*.

    Nothing about the ingest service is faked here — only the moment the process
    stops existing is chosen, so the parent can SIGKILL it in exactly the window
    it wants to reason about. The child reports when it has reached that window;
    it never reports an outcome, because a process that dies mid-admission is
    precisely a producer that never learned whether it succeeded.
    """

    async def run() -> None:
        broker = _DurableFileBroker(broker_path)
        svc = TelemetryIngestService(
            schema_path=_SCHEMA_PATH,
            storage_dir=storage_dir,
            buffer=broker,
            infrastructure_health_ledger_path=ledger_path,
            infrastructure_health_lease_seconds=_CRASH_LEASE_SECONDS,
            # Keep the canonical writer from acknowledging inside the test
            # window, so the parent observes the broker exactly as a crash left it.
            batch_size=10_000,
            batch_interval=3600.0,
        )
        await svc.start()

        reached = asyncio.Event()
        original_put = broker.put
        ledger = svc._infrastructure_health_ledger
        assert ledger is not None

        if crash_point == _CRASH_BEFORE_PUT:

            async def _stop_before_put(pending, timeout=None):
                reached.set()
                await asyncio.Event().wait()
                return True

            broker.put = _stop_before_put  # type: ignore[assignment]
        elif crash_point == _CRASH_AFTER_DURABLE_PUT:

            async def _stop_after_put(pending, timeout=None):
                enqueued = await original_put(pending, timeout=timeout)
                reached.set()
                await asyncio.Event().wait()
                return enqueued

            broker.put = _stop_after_put  # type: ignore[assignment]
        elif crash_point == _CRASH_AFTER_COMMIT:
            original_commit = ledger.commit

            def _stop_after_commit(event_id, fingerprint, token):
                original_commit(event_id, fingerprint, token)
                # Report from here rather than from a task: this call blocks the
                # event loop, which is what a process dying at this instant does.
                connection.send(crash_point)
                connection.close()
                while True:  # pragma: no cover - the parent SIGKILLs this process
                    time.sleep(0.2)

            ledger.commit = _stop_after_commit  # type: ignore[assignment]
        else:  # pragma: no cover - guarded by the caller
            raise ValueError(f"unknown crash point: {crash_point}")

        async def _notify() -> None:
            await reached.wait()
            connection.send(crash_point)
            connection.close()

        if crash_point != _CRASH_AFTER_COMMIT:
            asyncio.ensure_future(_notify())
        await svc.ingest_infrastructure_health(event)

    asyncio.run(run())


def _crash_matrix_replica(
    storage_dir: str,
    ledger_path: str,
    broker_path: str,
    event: dict,
    barrier,
    connection,
) -> None:
    """Child process: one full real-service admission, reported terminally."""

    async def run() -> dict:
        svc = TelemetryIngestService(
            schema_path=_SCHEMA_PATH,
            storage_dir=storage_dir,
            buffer=_DurableFileBroker(broker_path),
            infrastructure_health_ledger_path=ledger_path,
            infrastructure_health_lease_seconds=_CRASH_LEASE_SECONDS,
        )
        await svc.start()
        try:
            return await svc.ingest_infrastructure_health(event)
        finally:
            await svc.stop()

    if barrier is not None:
        barrier.wait(timeout=30)
    try:
        result = asyncio.run(run())
        payload = {"status": result.get("status"), "code": result.get("code")}
    except Exception as exc:  # noqa: BLE001
        payload = {"status": "error", "code": str(exc)}
    connection.send(payload)
    connection.close()


def _service_reserve_then_hang(
    storage_dir: str,
    ledger_path: str,
    event: dict,
    connection,
) -> None:
    """Child-process replica: crash inside the real ingest path mid-admission.

    The service is fully real; only the durable enqueue is made to hang, which
    is exactly the window the two-phase reservation has to survive.
    """

    async def run() -> None:
        svc = TelemetryIngestService(
            schema_path=_SCHEMA_PATH,
            storage_dir=storage_dir,
            buffer=_DurableFileBroker(str(Path(storage_dir) / _BROKER_FILENAME)),
            infrastructure_health_ledger_path=ledger_path,
            infrastructure_health_lease_seconds=_CRASH_LEASE_SECONDS,
        )
        await svc.start()

        stalled = asyncio.Event()

        async def _hang(_event, timeout=None):
            stalled.set()
            await asyncio.Event().wait()
            return True

        svc._buffer.put = _hang  # type: ignore[assignment]

        async def _notify() -> None:
            await stalled.wait()
            connection.send("reserved")
            connection.close()

        asyncio.ensure_future(_notify())
        await svc.ingest_infrastructure_health(event)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


class TestInfrastructureHealthRoute(unittest.TestCase):
    """Strict-auth HTTP tests for POST /api/v1/telemetry/infrastructure-health."""

    ROUTE = "/api/v1/telemetry/infrastructure-health"
    TRADING_ROUTE = "/api/telemetry/ingest"

    @classmethod
    def setUpClass(cls):
        cls._storage_dir = tempfile.mkdtemp(prefix="infra-health-authority-")
        cls._ledger_path = str(
            Path(cls._storage_dir) / INFRASTRUCTURE_HEALTH_LEDGER_FILENAME
        )
        cls._broker_path = str(Path(cls._storage_dir) / _BROKER_FILENAME)

        loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(
            target=_run_loop,
            daemon=True,
            name="infra-health-test-loop",
        )
        thread.start()
        cls._loop = loop

        cls._svc = cls._build_service()
        _main._loop = loop
        _main._svc = cls._svc
        _main._lineage_svc = None
        cls.client = _main.app.test_client()

    @classmethod
    def _build_service(cls) -> TelemetryIngestService:
        svc = TelemetryIngestService(
            schema_path=_SCHEMA_PATH,
            storage_dir=cls._storage_dir,
            buffer=_DurableFileBroker(cls._broker_path),
            batch_size=10,
            batch_interval=0.05,
            binding_store=_StubBindingStore(),
            runtime_summary_store=RuntimeSummaryProjectionStore(
                heartbeat_stale_after_seconds=10_000_000_000
            ),
        )
        asyncio.run_coroutine_threadsafe(svc.start(), cls._loop).result(timeout=10)
        return svc

    @classmethod
    def tearDownClass(cls):
        if cls._svc is not None:
            asyncio.run_coroutine_threadsafe(
                cls._svc.stop(graceful=True), cls._loop
            ).result(timeout=10)
        _main._svc = None
        _main._loop = None
        if cls._loop.is_running():
            cls._loop.call_soon_threadsafe(cls._loop.stop)
        shutil.rmtree(cls._storage_dir, ignore_errors=True)

    # -- helpers --

    def _post(self, event, *, token=None, tenant=None, env=None, route=None):
        headers = {"X-Tenant-Id": tenant or _TENANT_ID}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        with patch.dict(os.environ, {**_STRICT_ENV, **(env or {})}, clear=False):
            return self.client.post(
                route or self.ROUTE,
                json=event,
                headers=headers,
            )

    def _infra_stats(self) -> dict:
        return self._svc.infrastructure_health_stats()

    def _dlq_reason(self, event_id: str, tag: str) -> str | None:
        for entry in self._svc.get_dlq_entries(tag_filter=tag, limit=1000):
            event = entry.get("event")
            if isinstance(event, dict) and event.get("event_id") == event_id:
                return entry.get("reason")
        return None

    # -- 1. valid ingest --

    def test_valid_infrastructure_health_is_admitted_without_runtime_binding(self):
        event = _infra_event("infra-valid-001")
        before = self._infra_stats()["admitted"]

        response = self._post(event, token=_service_token())

        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertEqual(payload["status"], "accepted")
        self.assertFalse(payload["duplicate"])
        self.assertEqual(payload["event_id"], "infra-valid-001")
        self.assertEqual(payload["tenant_id"], _TENANT_ID)
        self.assertEqual(payload["producer"], _PRODUCER)
        self.assertEqual(self._infra_stats()["admitted"], before + 1)

        # The admitted event is readable and carries no RuntimeBinding identity.
        with patch.dict(os.environ, _STRICT_ENV, clear=False):
            readback = self.client.get(
                "/api/telemetry/events/infra-valid-001",
                headers={
                    "Authorization": f"Bearer {_service_token()}",
                    "X-Tenant-Id": _TENANT_ID,
                },
            )
        self.assertEqual(readback.status_code, 200)
        stored = readback.get_json()
        self.assertEqual(stored["schema_version"], INFRASTRUCTURE_HEALTH_SCHEMA_VERSION)
        for field in (
            "binding_id",
            "runtime_id",
            "capital_pool_id",
            "artifact_id",
            "artifact_version",
            "deployment_stage",
            "execution_mode",
            "plan_id",
            "persona_capital_binding_id",
        ):
            self.assertNotIn(field, stored)

        # The admission is durable on disk, not only in process memory, and the
        # committed receipt is preceded by its own leased reservation.
        states = [
            record.get("state")
            for record in _ledger_records(self._ledger_path)
            if record.get("event_id") == "infra-valid-001"
        ]
        self.assertEqual(states, ["reserved", "committed"])
        self.assertTrue(
            self._svc._infrastructure_health_ledger.is_committed("infra-valid-001")
        )

    def test_infrastructure_health_is_not_projected_as_runtime_state(self):
        self._post(_infra_event("infra-no-runtime-projection-001"), token=_service_token())
        self.assertEqual(
            self._svc.list_runtime_summaries(tenant_id=_TENANT_ID),
            [],
            "infrastructure health must never create a runtime status summary",
        )

    # -- 2. missing / weak authority --

    def test_missing_token_is_rejected(self):
        response = self._post(_infra_event("infra-missing-token-001"), token=None)
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(
            self._svc._infrastructure_health_ledger.state_of("infra-missing-token-001")
        )

    def test_permissive_deployment_mode_does_not_weaken_this_route(self):
        """A permissive telemetry rollout must not open the infra channel."""
        response = self._post(
            _infra_event("infra-permissive-001"),
            token="control-plane-bff:service",
            env={"PANTHEON_TELEMETRY_AUTH_MODE": "permissive"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "AUTH_TOKEN_FORMAT",
        )

    def test_static_service_token_cannot_admit_infrastructure_health(self):
        """The shared static bearer proves no producer scope, so it is refused."""
        response = self._post(
            _infra_event("infra-static-token-001"),
            token="telemetry-service-secret",
            env={
                "PANTHEON_TELEMETRY_SERVICE_TOKEN": "telemetry-service-secret",
                "PANTHEON_TELEMETRY_SERVICE_TENANTS": _TENANT_ID,
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_non_service_role_is_rejected(self):
        response = self._post(
            _infra_event("infra-operator-role-001"),
            token=_service_token(roles=("operator", "admin")),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"]["code"], "AUTH_FORBIDDEN")

    # -- 3. tenant binding --

    def test_wrong_tenant_header_is_rejected(self):
        response = self._post(
            _infra_event("infra-wrong-tenant-001", tenant_id=_OTHER_TENANT),
            token=_service_token(),
            tenant=_OTHER_TENANT,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"]["code"], "TENANT_FORBIDDEN")

    def test_payload_tenant_must_match_authenticated_tenant(self):
        response = self._post(
            _infra_event("infra-tenant-payload-001", tenant_id=_OTHER_TENANT),
            token=_service_token(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "TENANT_PAYLOAD_MISMATCH",
        )

    def test_deployment_tenant_fallback_does_not_grant_infra_authority(self):
        """Tenant authority must come from the service token, not from env."""
        response = self._post(
            _infra_event("infra-tenant-fallback-001"),
            token=_service_token(tenants=None),
            env={"PANTHEON_TELEMETRY_ALLOWED_TENANTS": _TENANT_ID},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "TENANT_SCOPE_UNCONFIGURED",
        )

    def test_wildcard_tenant_claim_is_rejected(self):
        response = self._post(
            _infra_event("infra-tenant-wildcard-001"),
            token=_service_token(tenants=("*",)),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "TENANT_SCOPE_UNBOUNDED",
        )

    # -- 4. producer scope --

    def test_producer_outside_deployment_allowlist_is_rejected(self):
        response = self._post(
            _infra_event("infra-producer-forbidden-001", producer=_OTHER_PRODUCER),
            token=_service_token(producers=(_OTHER_PRODUCER,)),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"]["code"], "PRODUCER_FORBIDDEN")

    def test_event_producer_outside_token_scope_is_rejected(self):
        response = self._post(
            _infra_event("infra-producer-mismatch-001", producer=_OTHER_PRODUCER),
            token=_service_token(),
            env={
                "PANTHEON_TELEMETRY_INFRA_PRODUCERS": f"{_PRODUCER},{_OTHER_PRODUCER}"
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"]["code"], "PRODUCER_FORBIDDEN")

    def test_missing_producer_scope_is_rejected(self):
        no_claim = self._post(
            _infra_event("infra-producer-unscoped-001"),
            token=_service_token(producers=None),
        )
        self.assertEqual(no_claim.status_code, 403)
        self.assertEqual(
            no_claim.get_json()["error"]["code"],
            "PRODUCER_SCOPE_UNCONFIGURED",
        )

        unconfigured = self._post(
            _infra_event("infra-producer-unconfigured-001"),
            token=_service_token(),
            env={"PANTHEON_TELEMETRY_INFRA_PRODUCERS": ""},
        )
        self.assertEqual(unconfigured.status_code, 403)
        self.assertEqual(
            unconfigured.get_json()["error"]["code"],
            "PRODUCER_SCOPE_UNCONFIGURED",
        )

    def test_wildcard_producer_scope_is_rejected(self):
        response = self._post(
            _infra_event("infra-producer-wildcard-001"),
            token=_service_token(producers=("*",)),
            env={"PANTHEON_TELEMETRY_INFRA_PRODUCERS": "*"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "PRODUCER_SCOPE_UNBOUNDED",
        )

    # -- 5. no invented RuntimeBinding, no trading bypass --

    def test_binding_evidence_in_infrastructure_event_is_rejected(self):
        response = self._post(
            _infra_event("infra-binding-spoof-001", binding_id=_KNOWN_BINDING_ID),
            token=_service_token(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "INFRA_BINDING_FIELD_FORBIDDEN",
        )

    def test_nested_binding_evidence_is_rejected(self):
        response = self._post(
            _infra_event(
                "infra-binding-nested-spoof-001",
                metadata={"probe": {"binding_id": _KNOWN_BINDING_ID}},
            ),
            token=_service_token(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "INFRA_BINDING_FIELD_FORBIDDEN",
        )

    def test_binding_evidence_nested_past_the_old_depth_cap_is_rejected(self):
        """Adversarial: the scan used to stop at depth 8 and answer "clean".

        ``metadata`` is ``additionalProperties: true`` by contract, so the
        standalone schema accepts arbitrary producer context at arbitrary depth.
        That makes the ingest-path scan the only gate on RuntimeBinding evidence,
        and a gate that stops looking at some depth is not a gate: before this
        repair a ``binding_id`` nested at depth 10 was admitted.
        """
        deep: dict = {"binding_id": _KNOWN_BINDING_ID}
        for _ in range(64):
            deep = {"nested": deep}
        event = _infra_event("infra-binding-deep-spoof-001", metadata=deep)

        # The schema is not the thing rejecting this — prove that first, so the
        # test cannot pass for the wrong reason if the scan regresses again.
        import jsonschema as _jsonschema

        _jsonschema.validate(
            instance=event,
            schema=self._svc._infrastructure_health_schema,
        )

        response = self._post(event, token=_service_token())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "INFRA_BINDING_FIELD_FORBIDDEN",
        )
        self.assertIsNotNone(
            self._dlq_reason("infra-binding-deep-spoof-001", TAG_BINDING_MISMATCH)
        )

    def test_binding_evidence_deep_inside_lists_is_rejected(self):
        """The same depth escape through alternating list/object nesting."""
        deep: Any = {"runtime_id": "lean-worker-1"}
        for index in range(24):
            deep = [deep] if index % 2 else {"nested": deep}
        response = self._post(
            _infra_event("infra-binding-deep-list-spoof-001", metadata={"probe": deep}),
            token=_service_token(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "INFRA_BINDING_FIELD_FORBIDDEN",
        )

    def test_unknown_contract_fields_are_rejected(self):
        response = self._post(
            _infra_event("infra-unknown-field-001", infrastructure_probe=True),
            token=_service_token(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "INFRA_SCHEMA_VIOLATION",
        )

    def test_runtime_health_probe_shape_cannot_bypass_binding_validation(self):
        """The removed bypass: probe-shaped trading events still fail closed."""
        spoof = _trading_event(
            "infra-runtime-health-spoof-001",
            event_type="runtime_health",
            binding_id="unknown-binding-999",
            metrics={"action": "probe"},
            metadata={
                "infrastructure_probe": {"service_name": "runtime-manager"},
                "bff_health_probe": {"status": "degraded"},
            },
        )
        with patch.dict(os.environ, _STRICT_ENV, clear=False):
            response = self.client.post(
                self.TRADING_ROUTE,
                json=spoof,
                headers={
                    "Authorization": f"Bearer {_service_token()}",
                    "X-Tenant-Id": _TENANT_ID,
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(
            self._svc.get_accepted_event(
                "infra-runtime-health-spoof-001", tenant_id=_TENANT_ID
            )
        )
        reason = self._dlq_reason("infra-runtime-health-spoof-001", TAG_BINDING_MISMATCH)
        self.assertIsNotNone(
            reason,
            "the spoof must be rejected by authoritative binding validation",
        )
        self.assertIn("not found in RuntimeBinding store", reason)

    def test_infrastructure_event_cannot_enter_the_trading_ingest_path(self):
        with patch.dict(os.environ, _STRICT_ENV, clear=False):
            response = self.client.post(
                self.TRADING_ROUTE,
                json=_infra_event("infra-via-trading-route-001"),
                headers={
                    "Authorization": f"Bearer {_service_token()}",
                    "X-Tenant-Id": _TENANT_ID,
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(
            self._svc.get_accepted_event(
                "infra-via-trading-route-001", tenant_id=_TENANT_ID
            )
        )
        reason = self._dlq_reason("infra-via-trading-route-001", TAG_SCHEMA_VIOLATION)
        self.assertIsNotNone(reason)
        self.assertIn("infrastructure health authority", reason)

    def test_trading_telemetry_validation_is_unchanged(self):
        with patch.dict(os.environ, _STRICT_ENV, clear=False):
            accepted = self.client.post(
                self.TRADING_ROUTE,
                json=_trading_event("infra-authority-trading-ok-001"),
                headers={
                    "Authorization": f"Bearer {_service_token()}",
                    "X-Tenant-Id": _TENANT_ID,
                },
            )
            rejected = self.client.post(
                self.TRADING_ROUTE,
                json=_trading_event(
                    "infra-authority-trading-bad-001",
                    binding_id="unknown-binding-000",
                ),
                headers={
                    "Authorization": f"Bearer {_service_token()}",
                    "X-Tenant-Id": _TENANT_ID,
                },
            )
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(rejected.status_code, 400)

    # -- 6. durable idempotent admission --

    def test_duplicate_replay_is_idempotent(self):
        event = _infra_event("infra-duplicate-001")
        before = self._infra_stats()

        first = self._post(event, token=_service_token())
        second = self._post(event, token=_service_token())
        third = self._post(event, token=_service_token())

        self.assertEqual(first.status_code, 202)
        self.assertFalse(first.get_json()["duplicate"])
        for response in (second, third):
            self.assertEqual(response.status_code, 202)
            self.assertTrue(response.get_json()["duplicate"])
            self.assertEqual(
                response.get_json()["fingerprint"],
                first.get_json()["fingerprint"],
            )

        after = self._infra_stats()
        self.assertEqual(after["admitted"], before["admitted"] + 1)
        self.assertEqual(after["duplicates"], before["duplicates"] + 2)

        # One reservation and one committed receipt for the first admission; the
        # two replays append nothing.
        states = [
            record.get("state")
            for record in _ledger_records(self._ledger_path)
            if record.get("event_id") == "infra-duplicate-001"
        ]
        self.assertEqual(states, ["reserved", "committed"], states)

    def test_event_id_reuse_with_different_content_is_a_conflict(self):
        first = self._post(_infra_event("infra-conflict-001"), token=_service_token())
        conflicting = self._post(
            _infra_event("infra-conflict-001", health_status="unavailable"),
            token=_service_token(),
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(conflicting.status_code, 409)
        self.assertEqual(
            conflicting.get_json()["error"]["code"],
            "INFRA_EVENT_ID_CONFLICT",
        )

    def test_admission_survives_a_service_restart(self):
        event = _infra_event("infra-restart-001")
        first = self._post(event, token=_service_token())
        self.assertEqual(first.status_code, 202)
        self.assertFalse(first.get_json()["duplicate"])

        # Restart the telemetry owner process: a brand new service instance with
        # an empty in-memory dedup set over the same durable storage directory.
        asyncio.run_coroutine_threadsafe(
            type(self)._svc.stop(graceful=True), type(self)._loop
        ).result(timeout=10)
        restarted = type(self)._build_service()
        type(self)._svc = restarted
        self.__class__._svc = restarted
        _main._svc = restarted

        self.assertEqual(restarted.infrastructure_health_stats()["admitted"], 0)
        replayed = self._post(event, token=_service_token())
        self.assertEqual(replayed.status_code, 202)
        self.assertTrue(
            replayed.get_json()["duplicate"],
            "a stable event_id must not be admitted twice across a restart",
        )
        self.assertEqual(restarted.infrastructure_health_stats()["admitted"], 0)
        self.assertEqual(restarted.infrastructure_health_stats()["duplicates"], 1)


# ---------------------------------------------------------------------------
# Replica-level durability
# ---------------------------------------------------------------------------


class TestForbiddenBindingFieldScan(unittest.TestCase):
    """The RuntimeBinding evidence scan itself, at depths no producer reaches.

    The contract stated in the schema, in the ingest docstring, and in the
    evidence README is that RuntimeBinding evidence is rejected *anywhere* in an
    infrastructure health event. These assert the scan actually honours that
    rather than falling back to "found nothing" once a payload gets deep enough.
    """

    _scan = staticmethod(TelemetryIngestService._forbidden_binding_fields)

    def test_evidence_field_is_found_just_past_the_old_depth_cap(self):
        payload: Any = {"binding_id": "b-1"}
        for _ in range(10):
            payload = {"nested": payload}
        self.assertEqual(self._scan({"metadata": payload}), ["binding_id"])

    def test_evidence_field_is_found_at_a_depth_recursion_could_not_reach(self):
        """No RecursionError, and no silent miss, at 5000 levels of nesting."""
        payload: Any = {"capital_pool_id": "pool-alpha"}
        for _ in range(5000):
            payload = {"nested": [payload]}
        self.assertEqual(self._scan(payload), ["capital_pool_id"])

    def test_every_evidence_field_is_reported_across_mixed_containers(self):
        payload = {
            "metadata": {
                "a": [{"deep": {"binding_id": "b-1"}}],
                "b": ({"runtime_id": "r-1"},),
                "c": [[[[[[[[[[{"plan_id": "p-1"}]]]]]]]]]],
            }
        }
        self.assertEqual(
            sorted(set(self._scan(payload))),
            ["binding_id", "plan_id", "runtime_id"],
        )

    def test_a_clean_payload_is_reported_clean(self):
        self.assertEqual(self._scan(_infra_event("infra-scan-clean-001")), [])

    def test_a_self_referential_payload_terminates_and_still_reports(self):
        """A reused or cyclic container must not hang the scan or hide a field."""
        loop: dict[str, Any] = {"artifact_id": "artifact-123"}
        loop["self"] = loop
        shared = {"deployment_stage": "paper"}
        payload = {"metadata": {"loop": loop, "x": shared, "y": shared}}
        self.assertEqual(
            sorted(set(self._scan(payload))),
            ["artifact_id", "deployment_stage"],
        )


class TestInfrastructureHealthReplicaAdmission(unittest.TestCase):
    """Two telemetry replicas sharing one storage volume admit an ID once."""

    def setUp(self):
        self._storage_dir = tempfile.mkdtemp(prefix="infra-health-replica-")
        self.addCleanup(shutil.rmtree, self._storage_dir, ignore_errors=True)
        self._ledger_path = str(
            Path(self._storage_dir) / INFRASTRUCTURE_HEALTH_LEDGER_FILENAME
        )
        self._broker_path = str(Path(self._storage_dir) / _BROKER_FILENAME)

    def _service(self) -> TelemetryIngestService:
        # Every replica speaks to the same durable broker log, the way two
        # ingest processes share one stream.
        return TelemetryIngestService(
            schema_path=_SCHEMA_PATH,
            storage_dir=self._storage_dir,
            buffer=_DurableFileBroker(self._broker_path),
        )

    def test_second_replica_treats_a_stable_event_id_as_duplicate(self):
        event = _infra_event("infra-replica-001")

        async def run() -> tuple[dict, dict]:
            replica_a = self._service()
            replica_b = self._service()
            await replica_a.start()
            await replica_b.start()
            try:
                first = await replica_a.ingest_infrastructure_health(event)
                second = await replica_b.ingest_infrastructure_health(event)
                return first, second
            finally:
                await replica_a.stop()
                await replica_b.stop()

        first, second = asyncio.run(run())
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_replica_conflict_on_reused_event_id_with_different_content(self):
        async def run() -> dict:
            replica_a = self._service()
            replica_b = self._service()
            await replica_a.start()
            await replica_b.start()
            try:
                await replica_a.ingest_infrastructure_health(
                    _infra_event("infra-replica-conflict-001")
                )
                return await replica_b.ingest_infrastructure_health(
                    _infra_event("infra-replica-conflict-001", severity="critical")
                )
            finally:
                await replica_a.stop()
                await replica_b.stop()

        result = asyncio.run(run())
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["code"], "INFRA_EVENT_ID_CONFLICT")

    def _race_replicas(
        self, event: dict, replica_count: int, *, synchronized: bool = True
    ) -> list[dict]:
        """Fork *replica_count* real processes onto one event_id and collect them.

        ``synchronized`` releases every replica from a shared barrier, which is
        the closest a test can get to a simultaneous start. Without it the
        replicas simply run, which is how a stage that must observe an already
        durable receipt is set up deterministically.
        """
        # Materialise the ledger before forking children so every replica opens
        # the same file rather than racing on its creation.
        InfrastructureHealthAdmissionLedger(self._ledger_path)

        context = multiprocessing.get_context("fork")
        barrier = context.Barrier(replica_count) if synchronized else None
        processes = []
        connections = []
        for _ in range(replica_count):
            parent_conn, child_conn = context.Pipe(duplex=False)
            process = context.Process(
                target=_replica_admit,
                args=(
                    self._ledger_path,
                    self._broker_path,
                    event,
                    barrier,
                    child_conn,
                ),
            )
            process.start()
            child_conn.close()
            processes.append(process)
            connections.append(parent_conn)

        reports = []
        try:
            for connection in connections:
                self.assertTrue(
                    connection.poll(30),
                    "replica process did not report an admission outcome",
                )
                reports.append(connection.recv())
        finally:
            for process in processes:
                process.join(30)
            for connection in connections:
                connection.close()
        return reports

    def test_concurrent_replica_processes_admit_exactly_once(self):
        """Cross-process proof: the ledger lock serialises real replicas.

        What is asserted is the *owner*, not the ledger word. A losing replica
        may report either ``in_flight`` (the winner still held a live reservation
        when it looked) or ``post_receipt_duplicate`` (the winner had already
        published its durable receipt) — both are truthful and both are outside
        the caller's control, so keying on the string ``committed`` conflated the
        one admission with every later idempotent read of it.

        The race is repeated over independent event IDs so a single favourable
        interleaving cannot stand in for stability.
        """
        replica_count = 4
        for round_index in range(8):
            event = _infra_event(f"infra-replica-race-{round_index:03d}")
            event_id = event["event_id"]
            reports = self._race_replicas(event, replica_count)
            detail = f"round {round_index}: {reports}"

            owners = [
                report for report in reports if report["role"] == _ROLE_COMMIT_OWNER
            ]
            self.assertEqual(
                len(owners),
                1,
                f"exactly one replica may own the admission — {detail}",
            )
            owner = owners[0]
            self.assertTrue(
                owner["durable_put"],
                f"the owner must hold a durable enqueue receipt — {detail}",
            )

            for report in reports:
                if report is owner:
                    continue
                self.assertIn(
                    report["role"],
                    _LOSER_ROLES,
                    f"a losing replica must be told to retry or told duplicate — {detail}",
                )
                self.assertFalse(
                    report["durable_put"],
                    f"a losing replica must not enqueue its own copy — {detail}",
                )
                if report["role"] == _ROLE_POST_RECEIPT_DUPLICATE:
                    # Only reachable because a durable receipt already existed.
                    self.assertEqual(report["begin"], "committed", detail)

            # Exactly one committed ledger receipt, and it is the owner's.
            committed_records = [
                record
                for record in _ledger_records(self._ledger_path)
                if record.get("state") == "committed"
                and record.get("event_id") == event_id
            ]
            self.assertEqual(len(committed_records), 1, f"{committed_records} — {detail}")
            self.assertEqual(
                committed_records[0].get("token"),
                owner["token"],
                f"the single receipt must be owned by the single owner — {detail}",
            )

            # Exactly one durable admission of the event.
            self.assertEqual(
                _broker_durable_events(self._broker_path, event_id),
                [event],
                f"exactly one durable copy of the event may exist — {detail}",
            )

    def test_replica_that_begins_after_the_receipt_is_a_duplicate_not_an_owner(self):
        """Deterministic cover for the interleaving that made the race flaky.

        Under load the OS can schedule a barriered replica so late that the
        winner's durable receipt already exists by the time it calls ``begin``.
        The ledger then answers it with the same word — ``committed`` — that the
        winner's own ``commit`` returned. That replica is an idempotent duplicate
        and must never be counted as a second admission. Staging the replicas
        makes that interleaving certain instead of load-dependent, so the
        classification is proven rather than hoped for.
        """
        event = _infra_event("infra-replica-post-receipt-001")
        event_id = event["event_id"]

        owners = self._race_replicas(event, 1, synchronized=False)
        self.assertEqual([report["role"] for report in owners], [_ROLE_COMMIT_OWNER], owners)
        owner = owners[0]

        later = self._race_replicas(event, 3, synchronized=False)
        self.assertEqual(
            [report["role"] for report in later],
            [_ROLE_POST_RECEIPT_DUPLICATE] * 3,
            later,
        )
        for report in later:
            self.assertEqual(report["begin"], "committed", report)
            self.assertIsNone(report["token"], report)
            self.assertFalse(report["durable_put"], report)

        committed_records = [
            record
            for record in _ledger_records(self._ledger_path)
            if record.get("state") == "committed"
            and record.get("event_id") == event_id
        ]
        self.assertEqual(len(committed_records), 1, committed_records)
        self.assertEqual(committed_records[0].get("token"), owner["token"])
        self.assertEqual(
            _broker_durable_events(self._broker_path, event_id),
            [event],
            "a post-receipt duplicate must not add a second durable copy",
        )

    def test_concurrent_admission_loser_is_not_told_it_succeeded(self):
        """The loss window the reviewer found: a duplicate before any receipt.

        While replica A holds a live reservation and is still inside its durable
        enqueue, replica B must not be answered as an accepted duplicate — at
        that moment nothing has been persisted, and A may still fail.
        """
        event = _infra_event("infra-inflight-001")

        async def run() -> tuple[dict, dict, dict]:
            replica_a = self._service()
            replica_b = self._service()
            await replica_a.start()
            await replica_b.start()
            try:
                inside_enqueue = asyncio.Event()
                release_enqueue = asyncio.Event()
                original_put = replica_a._buffer.put

                async def _stalled_put(pending, timeout=None):
                    inside_enqueue.set()
                    await release_enqueue.wait()
                    return await original_put(pending, timeout=timeout)

                replica_a._buffer.put = _stalled_put  # type: ignore[assignment]
                a_task = asyncio.ensure_future(
                    replica_a.ingest_infrastructure_health(event)
                )
                await asyncio.wait_for(inside_enqueue.wait(), timeout=5)

                # A is reserved but has no durable receipt yet.
                contender = await replica_b.ingest_infrastructure_health(event)

                release_enqueue.set()
                admitted = await asyncio.wait_for(a_task, timeout=5)
                after_receipt = await replica_b.ingest_infrastructure_health(event)
                return contender, admitted, after_receipt
            finally:
                await replica_a.stop()
                await replica_b.stop()

        contender, admitted, after_receipt = asyncio.run(run())
        self.assertEqual(contender["status"], "rejected")
        self.assertEqual(contender["code"], "INFRA_ADMISSION_IN_FLIGHT")
        self.assertEqual(admitted["status"], "accepted")
        # Only once a durable receipt exists is the replay an idempotent success.
        self.assertEqual(after_receipt["status"], "duplicate")

    def test_crash_between_reservation_and_enqueue_is_recoverable(self):
        """SIGKILL inside the real ingest path must not strand the event_id."""
        event = _infra_event("infra-crash-recovery-001")
        InfrastructureHealthAdmissionLedger(
            self._ledger_path, lease_seconds=_CRASH_LEASE_SECONDS
        )

        context = multiprocessing.get_context("fork")
        parent_conn, child_conn = context.Pipe(duplex=False)
        process = context.Process(
            target=_service_reserve_then_hang,
            args=(self._storage_dir, self._ledger_path, event, child_conn),
        )
        process.start()
        child_conn.close()
        try:
            self.assertTrue(
                parent_conn.poll(30),
                "child did not reach the durable enqueue window",
            )
            self.assertEqual(parent_conn.recv(), "reserved")
            os.kill(process.pid, signal.SIGKILL)
        finally:
            process.join(30)
            parent_conn.close()
        self.assertEqual(process.exitcode, -signal.SIGKILL)

        ledger = InfrastructureHealthAdmissionLedger(
            self._ledger_path, lease_seconds=_CRASH_LEASE_SECONDS
        )
        # The crashed owner left a reservation and no receipt: the event is not a
        # success, and no other caller may claim it while the lease is live.
        self.assertEqual(ledger.state_of(event["event_id"]), "reserved")
        self.assertFalse(ledger.is_committed(event["event_id"]))

        async def retry() -> tuple[dict, dict]:
            svc = TelemetryIngestService(
                schema_path=_SCHEMA_PATH,
                storage_dir=self._storage_dir,
                buffer=_DurableFileBroker(self._broker_path),
                infrastructure_health_ledger_path=self._ledger_path,
                infrastructure_health_lease_seconds=_CRASH_LEASE_SECONDS,
            )
            await svc.start()
            try:
                blocked = await svc.ingest_infrastructure_health(event)
                await asyncio.sleep(_CRASH_LEASE_SECONDS + 0.3)
                recovered = await svc.ingest_infrastructure_health(event)
                return blocked, recovered
            finally:
                await svc.stop()

        blocked, recovered = asyncio.run(retry())
        self.assertEqual(blocked["status"], "rejected")
        self.assertEqual(blocked["code"], "INFRA_ADMISSION_IN_FLIGHT")
        self.assertEqual(
            recovered["status"],
            "accepted",
            "an expired reservation from a crashed replica must be recoverable",
        )
        self.assertTrue(ledger.is_committed(event["event_id"]))

    def test_fenced_owner_cannot_commit_or_release_a_recovered_reservation(self):
        """A stalled owner must not resurrect or cancel a stolen claim."""
        event = _infra_event("infra-fencing-001")
        fingerprint = infrastructure_health_fingerprint(event)
        ledger = InfrastructureHealthAdmissionLedger(
            self._ledger_path, lease_seconds=0.2
        )

        stalled = ledger.begin(event["event_id"], fingerprint)
        self.assertEqual(stalled.outcome, "reserved")
        time.sleep(0.35)

        recovered = ledger.begin(event["event_id"], fingerprint)
        self.assertEqual(recovered.outcome, "reserved")
        self.assertNotEqual(recovered.token, stalled.token)

        self.assertFalse(
            ledger.release(event["event_id"], stalled.token),
            "the fenced owner must not release the recovered reservation",
        )
        self.assertEqual(
            ledger.commit(event["event_id"], fingerprint, stalled.token),
            "fenced",
            "the fenced owner must not commit the recovered reservation",
        )
        self.assertEqual(
            ledger.commit(event["event_id"], fingerprint, recovered.token),
            "committed",
        )
        # Once the receipt is durable, even the fenced owner sees a real success
        # rather than a phantom failure, because the content is identical.
        self.assertEqual(
            ledger.commit(event["event_id"], fingerprint, stalled.token),
            "committed",
        )

    def test_reservation_is_released_when_the_enqueue_is_cancelled(self):
        event = _infra_event("infra-cancelled-001")

        async def run() -> str | None:
            svc = self._service()
            await svc.start()
            try:
                async def _never(pending, timeout=None):
                    await asyncio.Event().wait()
                    return True

                svc._buffer.put = _never  # type: ignore[assignment]
                task = asyncio.ensure_future(
                    svc.ingest_infrastructure_health(event)
                )
                await asyncio.sleep(0.2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                return svc._infrastructure_health_ledger.state_of(event["event_id"])
            finally:
                await svc.stop()

        self.assertIsNone(
            asyncio.run(run()),
            "a cancelled admission must not leave a reservation behind",
        )

    def test_failed_enqueue_releases_the_reservation_for_retry(self):
        """A reservation must never outlive an enqueue that did not happen."""
        event = _infra_event("infra-release-001")

        async def run() -> tuple[dict, dict, str | None]:
            svc = self._service()
            await svc.start()
            try:
                async def _full_buffer(_event, timeout=None):
                    return False

                original_put = svc._buffer.put
                svc._buffer.put = _full_buffer  # type: ignore[assignment]
                overflowed = await svc.ingest_infrastructure_health(event)
                state = svc._infrastructure_health_ledger.state_of(event["event_id"])
                svc._buffer.put = original_put  # type: ignore[assignment]
                retried = await svc.ingest_infrastructure_health(event)
                return overflowed, retried, state
            finally:
                await svc.stop()

        overflowed, retried, state = asyncio.run(run())
        self.assertEqual(overflowed["status"], "rejected")
        self.assertEqual(overflowed["code"], "INFRA_BUFFER_OVERFLOW")
        self.assertIsNone(state, "the reservation must be released, not left open")
        self.assertEqual(retried["status"], "accepted")

    def test_ingestion_fails_closed_without_a_durable_ledger(self):
        async def run() -> dict:
            svc = TelemetryIngestService(
                schema_path=_SCHEMA_PATH,
                buffer=_DurableFileBroker(self._broker_path),
            )
            await svc.start()
            try:
                return await svc.ingest_infrastructure_health(
                    _infra_event("infra-no-ledger-001")
                )
            finally:
                await svc.stop()

        result = asyncio.run(run())
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["code"], "INFRA_LEDGER_UNCONFIGURED")

    def test_ingestion_fails_closed_without_the_authoritative_schema(self):
        async def run() -> dict:
            svc = TelemetryIngestService(
                storage_dir=self._storage_dir,
                buffer=_DurableFileBroker(self._broker_path),
            )
            await svc.start()
            try:
                return await svc.ingest_infrastructure_health(
                    _infra_event("infra-no-schema-001")
                )
            finally:
                await svc.stop()

        result = asyncio.run(run())
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["code"], "INFRA_SCHEMA_UNAVAILABLE")


# ---------------------------------------------------------------------------
# Durability authority
# ---------------------------------------------------------------------------


class _DegradingBroker(_DurableFileBroker):
    """A broker adapter that stops being durable after its first enqueue.

    This stands in for a backend that silently falls back to a process-local
    path mid-flight. The admission must not be able to convert that enqueue into
    a committed receipt.
    """

    def __init__(self, path: str):
        super().__init__(path)
        self._durable = True

    async def put(self, event: dict[str, Any], timeout: Optional[float] = None) -> bool:
        enqueued = await super().put(event, timeout=timeout)
        self._durable = False
        return enqueued

    def is_durable(self) -> bool:
        return self._durable


class TestInfrastructureHealthDurabilityAuthority(unittest.TestCase):
    """A volatile buffer may never back an infrastructure health admission.

    AC3 says the admitted event is durable across retries and replicas. The
    admission ledger alone cannot deliver that: committing a receipt for an
    event that only ever reached process memory is worse than rejecting it,
    because the crash erases the event while the receipt turns every later
    retry into a successful ``duplicate``. So durability is proven from the
    buffer itself, before anything is reserved or committed.
    """

    def setUp(self):
        self._storage_dir = tempfile.mkdtemp(prefix="infra-health-durability-")
        self.addCleanup(shutil.rmtree, self._storage_dir, ignore_errors=True)
        self._ledger_path = str(
            Path(self._storage_dir) / INFRASTRUCTURE_HEALTH_LEDGER_FILENAME
        )
        self._broker_path = str(Path(self._storage_dir) / _BROKER_FILENAME)

    def _volatile_service(self) -> TelemetryIngestService:
        # "memory" is the only volatile backend a deployment can select, and it
        # is what the rejected implementation admitted into.
        return TelemetryIngestService(
            schema_path=_SCHEMA_PATH,
            storage_dir=self._storage_dir,
            buffer_backend="memory",
            infrastructure_health_ledger_path=self._ledger_path,
        )

    def _durable_service(self) -> TelemetryIngestService:
        return TelemetryIngestService(
            schema_path=_SCHEMA_PATH,
            storage_dir=self._storage_dir,
            buffer=_DurableFileBroker(self._broker_path),
            infrastructure_health_ledger_path=self._ledger_path,
        )

    def test_volatile_buffer_fails_closed_before_any_reservation(self):
        event = _infra_event("infra-volatile-001")

        async def run() -> tuple[dict, dict]:
            svc = self._volatile_service()
            await svc.start()
            try:
                result = await svc.ingest_infrastructure_health(event)
                return result, svc.infrastructure_health_stats()
            finally:
                await svc.stop()

        result, stats = asyncio.run(run())

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["code"], "INFRA_BUFFER_NOT_DURABLE")
        self.assertIn("volatile", result["message"])
        self.assertFalse(stats["buffer_durable"])
        self.assertEqual(stats["buffer_type"], "InMemoryBuffer")
        self.assertEqual(stats["non_durable_rejections"], 1)
        self.assertEqual(stats["admitted"], 0)

        # Nothing was reserved and nothing was committed: a later retry against a
        # correctly configured deployment is still a first admission.
        self.assertEqual(_ledger_records(self._ledger_path), [])

    def test_volatile_admission_leaves_no_committed_receipt_to_lie_with(self):
        """The exact defect: a receipt that outlives the only copy of the event."""
        event = _infra_event("infra-volatile-receipt-001")

        async def run() -> dict:
            svc = self._volatile_service()
            await svc.start()
            try:
                await svc.ingest_infrastructure_health(event)
            finally:
                await svc.stop()

            # A restart is exactly what erases a memory buffer. The producer's
            # retry must be admissible, not answered as an already-delivered
            # duplicate of an event that no longer exists anywhere.
            restarted = self._durable_service()
            await restarted.start()
            try:
                return await restarted.ingest_infrastructure_health(event)
            finally:
                await restarted.stop()

        retried = asyncio.run(run())
        self.assertEqual(
            retried["status"],
            "accepted",
            "a retry after a volatile-buffer rejection must still be admissible",
        )
        self.assertEqual(
            _broker_durable_events(self._broker_path, event["event_id"]),
            [event],
        )

    def test_no_environment_flag_can_enable_volatile_admission(self):
        """There is no production-enableable bypass of the durability gate.

        Every telemetry environment variable the service reads is set to its most
        permissive plausible value at once; the volatile buffer is still refused,
        because the gate consults the backend's own ``is_durable()`` and nothing
        else.
        """
        sources = "\n".join(
            Path(__file__).resolve().parent.joinpath(name).read_text(encoding="utf-8")
            for name in ("main.py", "ingest_svc.py", "buffer.py", "auth.py")
        )
        env_names = sorted(set(re.findall(r"PANTHEON_[A-Z0-9_]+", sources)))
        self.assertGreater(len(env_names), 5, "expected to find telemetry env knobs")

        permissive = {name: "1" for name in env_names}
        permissive.update(
            {
                "PANTHEON_TELEMETRY_AUTH_MODE": "permissive",
                "PANTHEON_TELEMETRY_BUFFER_BACKEND": "memory",
            }
        )

        async def run() -> dict:
            svc = self._volatile_service()
            await svc.start()
            try:
                return await svc.ingest_infrastructure_health(
                    _infra_event("infra-no-bypass-001")
                )
            finally:
                await svc.stop()

        with patch.dict(os.environ, permissive, clear=False):
            result = asyncio.run(run())

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["code"], "INFRA_BUFFER_NOT_DURABLE")

    def test_strict_auth_route_answers_503_when_no_durable_broker_is_configured(self):
        event = _infra_event("infra-volatile-route-001")
        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()),
            daemon=True,
            name="infra-health-durability-loop",
        )
        thread.start()
        svc = self._volatile_service()
        asyncio.run_coroutine_threadsafe(svc.start(), loop).result(timeout=10)

        previous_svc, previous_loop = _main._svc, _main._loop
        _main._svc, _main._loop = svc, loop
        try:
            client = _main.app.test_client()
            with patch.dict(os.environ, _STRICT_ENV, clear=False):
                response = client.post(
                    "/api/v1/telemetry/infrastructure-health",
                    json=event,
                    headers={
                        "Authorization": f"Bearer {_service_token()}",
                        "X-Tenant-Id": _TENANT_ID,
                    },
                )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(
                response.get_json()["error"]["code"], "INFRA_BUFFER_NOT_DURABLE"
            )
            metrics = _main._telemetry_metrics()
            self.assertEqual(metrics["infrastructure_health_buffer_durable"], 0)
            self.assertEqual(metrics["infrastructure_health_non_durable_rejections"], 1)
            self.assertEqual(metrics["infrastructure_health_admitted"], 0)
        finally:
            asyncio.run_coroutine_threadsafe(svc.stop(), loop).result(timeout=10)
            _main._svc, _main._loop = previous_svc, previous_loop
            loop.call_soon_threadsafe(loop.stop)

    def test_admission_is_refused_when_durability_is_lost_before_the_commit(self):
        """A backend that degrades mid-flight cannot buy a committed receipt."""
        event = _infra_event("infra-degraded-001")

        async def run() -> tuple[dict, Optional[str], dict]:
            svc = TelemetryIngestService(
                schema_path=_SCHEMA_PATH,
                storage_dir=self._storage_dir,
                buffer=_DegradingBroker(self._broker_path),
                infrastructure_health_ledger_path=self._ledger_path,
            )
            await svc.start()
            try:
                degraded = await svc.ingest_infrastructure_health(event)
                state = svc._infrastructure_health_ledger.state_of(event["event_id"])
            finally:
                await svc.stop()

            healthy = self._durable_service()
            await healthy.start()
            try:
                return degraded, state, await healthy.ingest_infrastructure_health(event)
            finally:
                await healthy.stop()

        degraded, state, retried = asyncio.run(run())
        self.assertEqual(degraded["status"], "rejected")
        self.assertEqual(degraded["code"], "INFRA_BUFFER_NOT_DURABLE")
        self.assertIsNone(
            state, "the reservation must be released, not committed, on degradation"
        )
        self.assertEqual(
            retried["status"],
            "accepted",
            "the producer's retry must still be admissible after a degraded attempt",
        )

    def test_durable_broker_admission_is_backed_by_an_fsynced_event(self):
        event = _infra_event("infra-durable-receipt-001")

        async def run() -> dict:
            svc = self._durable_service()
            await svc.start()
            try:
                return await svc.ingest_infrastructure_health(event)
            finally:
                await svc.stop()

        result = asyncio.run(run())
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(
            _broker_durable_events(self._broker_path, event["event_id"]),
            [event],
            "an accepted event must exist in durable storage, byte for byte",
        )
        committed = [
            record
            for record in _ledger_records(self._ledger_path)
            if record.get("event_id") == event["event_id"]
            and record.get("state") == "committed"
        ]
        self.assertEqual(len(committed), 1, committed)


# ---------------------------------------------------------------------------
# Process / broker crash matrix
# ---------------------------------------------------------------------------


class TestInfrastructureHealthCrashMatrix(unittest.TestCase):
    """SIGKILL the real ingest process in every window of the admission.

    For each window the matrix asserts the two failures that matter:

    * **no false success** — no producer is ever answered ``accepted`` or
      ``duplicate`` for an observation that is not in durable storage;
    * **no permanent loss** — a retry with the same stable event_id always ends
      with exactly one durable copy and exactly one committed receipt.
    """

    def setUp(self):
        self._storage_dir = tempfile.mkdtemp(prefix="infra-health-crash-")
        self.addCleanup(shutil.rmtree, self._storage_dir, ignore_errors=True)
        self._ledger_path = str(
            Path(self._storage_dir) / INFRASTRUCTURE_HEALTH_LEDGER_FILENAME
        )
        self._broker_path = str(Path(self._storage_dir) / _BROKER_FILENAME)
        # Materialise both durable files before forking so children share them.
        InfrastructureHealthAdmissionLedger(
            self._ledger_path, lease_seconds=_CRASH_LEASE_SECONDS
        )
        _DurableFileBroker(self._broker_path)

    # -- helpers --

    def _crash_at(self, event: dict, crash_point: str) -> None:
        context = multiprocessing.get_context("fork")
        parent_conn, child_conn = context.Pipe(duplex=False)
        process = context.Process(
            target=_crash_matrix_child,
            args=(
                self._storage_dir,
                self._ledger_path,
                self._broker_path,
                event,
                crash_point,
                child_conn,
            ),
        )
        process.start()
        child_conn.close()
        try:
            self.assertTrue(
                parent_conn.poll(30),
                f"child did not reach the {crash_point!r} window",
            )
            self.assertEqual(parent_conn.recv(), crash_point)
            os.kill(process.pid, signal.SIGKILL)
        finally:
            process.join(30)
            parent_conn.close()
        self.assertEqual(process.exitcode, -signal.SIGKILL)

    def _ledger(self) -> InfrastructureHealthAdmissionLedger:
        return InfrastructureHealthAdmissionLedger(
            self._ledger_path, lease_seconds=_CRASH_LEASE_SECONDS
        )

    def _admit(self, event: dict, *, wait_for_lease: bool = False) -> dict:
        """Run one full admission in a fresh service — the restarted replica."""

        async def run() -> dict:
            svc = TelemetryIngestService(
                schema_path=_SCHEMA_PATH,
                storage_dir=self._storage_dir,
                buffer=_DurableFileBroker(self._broker_path),
                infrastructure_health_ledger_path=self._ledger_path,
                infrastructure_health_lease_seconds=_CRASH_LEASE_SECONDS,
                batch_size=10_000,
                batch_interval=3600.0,
            )
            await svc.start()
            try:
                if wait_for_lease:
                    await asyncio.sleep(_CRASH_LEASE_SECONDS + 0.3)
                return await svc.ingest_infrastructure_health(event)
            finally:
                await svc.stop(graceful=False)

        return asyncio.run(run())

    def _committed_records(self, event_id: str) -> list[dict]:
        return [
            record
            for record in _ledger_records(self._ledger_path)
            if record.get("event_id") == event_id
            and record.get("state") == "committed"
        ]

    # -- 1. crash before the durable put --

    def test_crash_before_durable_put_never_reports_success_and_loses_nothing(self):
        event = _infra_event("infra-crash-before-put-001")
        self._crash_at(event, _CRASH_BEFORE_PUT)

        self.assertEqual(
            _broker_durable_events(self._broker_path, event["event_id"]),
            [],
            "nothing reached the broker, so nothing may look admitted",
        )
        ledger = self._ledger()
        self.assertEqual(ledger.state_of(event["event_id"]), "reserved")
        self.assertFalse(ledger.is_committed(event["event_id"]))

        blocked = self._admit(event)
        self.assertEqual(blocked["status"], "rejected")
        self.assertEqual(blocked["code"], "INFRA_ADMISSION_IN_FLIGHT")

        recovered = self._admit(event, wait_for_lease=True)
        self.assertEqual(recovered["status"], "accepted")
        self.assertEqual(
            _broker_durable_events(self._broker_path, event["event_id"]), [event]
        )
        self.assertEqual(len(self._committed_records(event["event_id"])), 1)

    # -- 2. crash after the durable put, before the commit --

    def test_crash_after_durable_put_before_commit_keeps_the_event(self):
        event = _infra_event("infra-crash-after-put-001")
        self._crash_at(event, _CRASH_AFTER_DURABLE_PUT)

        self.assertEqual(
            _broker_retained_events(self._broker_path, event["event_id"]),
            [event],
            "the fsync'd event must survive the SIGKILL",
        )
        ledger = self._ledger()
        self.assertEqual(ledger.state_of(event["event_id"]), "reserved")
        self.assertFalse(
            ledger.is_committed(event["event_id"]),
            "an uncommitted admission must not answer later retries as duplicates",
        )

        blocked = self._admit(event)
        self.assertEqual(blocked["status"], "rejected")
        self.assertEqual(blocked["code"], "INFRA_ADMISSION_IN_FLIGHT")

        recovered = self._admit(event, wait_for_lease=True)
        self.assertEqual(recovered["status"], "accepted")
        # At-least-once delivery may leave a second broker copy; both are the
        # same immutable event, and the canonical write path deduplicates by
        # event_id, so exactly one admission is recorded.
        copies = _broker_durable_events(self._broker_path, event["event_id"])
        self.assertGreaterEqual(len(copies), 1)
        self.assertTrue(all(copy == event for copy in copies), copies)
        self.assertEqual(len(self._committed_records(event["event_id"])), 1)

    # -- 3. crash after the commit --

    def test_crash_after_commit_answers_retries_with_a_truthful_duplicate(self):
        """The reviewer's scenario, now unreachable with a volatile buffer.

        The producer never sees the 202 because the process died; it retries and
        is told ``duplicate``. That answer is only honest because the event is
        still in durable storage after the crash.
        """
        event = _infra_event("infra-crash-after-commit-001")
        self._crash_at(event, _CRASH_AFTER_COMMIT)

        ledger = self._ledger()
        self.assertTrue(ledger.is_committed(event["event_id"]))
        self.assertEqual(
            _broker_retained_events(self._broker_path, event["event_id"]),
            [event],
            "a committed receipt is only truthful if the event outlived the crash",
        )

        retried = self._admit(event)
        self.assertEqual(retried["status"], "duplicate")
        self.assertEqual(len(self._committed_records(event["event_id"])), 1)
        self.assertEqual(
            _broker_durable_events(self._broker_path, event["event_id"]),
            [event],
            "an idempotent retry must not enqueue a second copy",
        )

    # -- 4. restart replay --

    def test_restart_replay_after_a_committed_admission_is_idempotent(self):
        event = _infra_event("infra-crash-restart-replay-001")

        admitted = self._admit(event)
        self.assertEqual(admitted["status"], "accepted")

        # Two further cold starts over the same durable files: fresh process
        # state, empty in-memory dedup, same answer.
        for _ in range(2):
            replayed = self._admit(event)
            self.assertEqual(replayed["status"], "duplicate")
            self.assertEqual(replayed["fingerprint"], admitted["fingerprint"])

        self.assertEqual(len(self._committed_records(event["event_id"])), 1)
        self.assertEqual(
            _broker_durable_events(self._broker_path, event["event_id"]), [event]
        )

    # -- 5. replica concurrency --

    def test_concurrent_replica_processes_produce_one_durable_admission(self):
        event = _infra_event("infra-crash-replica-race-001")
        context = multiprocessing.get_context("fork")
        replica_count = 4
        barrier = context.Barrier(replica_count)
        processes = []
        connections = []
        for _ in range(replica_count):
            parent_conn, child_conn = context.Pipe(duplex=False)
            process = context.Process(
                target=_crash_matrix_replica,
                args=(
                    self._storage_dir,
                    self._ledger_path,
                    self._broker_path,
                    event,
                    barrier,
                    child_conn,
                ),
            )
            process.start()
            child_conn.close()
            processes.append(process)
            connections.append(parent_conn)

        outcomes = []
        try:
            for connection in connections:
                self.assertTrue(
                    connection.poll(60),
                    "replica process did not report an admission outcome",
                )
                outcomes.append(connection.recv())
        finally:
            for process in processes:
                process.join(60)
            for connection in connections:
                connection.close()

        statuses = [outcome["status"] for outcome in outcomes]
        self.assertEqual(
            statuses.count("accepted"),
            1,
            f"exactly one replica may admit the event, got {outcomes}",
        )
        for outcome in outcomes:
            if outcome["status"] == "accepted":
                continue
            if outcome["status"] == "duplicate":
                # Only reachable once the winner's receipt is durable.
                continue
            self.assertEqual(outcome["status"], "rejected", outcome)
            self.assertEqual(outcome["code"], "INFRA_ADMISSION_IN_FLIGHT", outcome)

        self.assertEqual(
            _broker_durable_events(self._broker_path, event["event_id"]),
            [event],
            "losing replicas must not enqueue their own copy",
        )
        self.assertEqual(len(self._committed_records(event["event_id"])), 1)


if __name__ == "__main__":
    unittest.main()
