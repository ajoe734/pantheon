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
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import services.telemetry.main as _main
from services.runtime_auth_inbound import encode_jwt_hs256
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


def _replica_admit(ledger_path: str, event: dict, barrier, connection) -> None:
    """Child-process replica: admit one event through a shared ledger."""
    ledger = InfrastructureHealthAdmissionLedger(ledger_path)
    fingerprint = infrastructure_health_fingerprint(event)
    if barrier is not None:
        barrier.wait(timeout=30)
    try:
        outcome = ledger.admit(event["event_id"], fingerprint)
    except Exception as exc:  # noqa: BLE001
        outcome = f"error:{exc}"
    connection.send(outcome)
    connection.close()


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
            buffer_backend="memory",
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

        # The admission is durable on disk, not only in process memory.
        admitted_ids = {
            record["event_id"]
            for record in _ledger_records(self._ledger_path)
            if record.get("state") == "admitted"
        }
        self.assertIn("infra-valid-001", admitted_ids)

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
        self.assertFalse(self._svc._infrastructure_health_ledger.is_admitted(
            "infra-missing-token-001"
        ))

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

        admitted = [
            record
            for record in _ledger_records(self._ledger_path)
            if record.get("event_id") == "infra-duplicate-001"
        ]
        self.assertEqual(len(admitted), 1, "replay must not append a second record")

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


class TestInfrastructureHealthReplicaAdmission(unittest.TestCase):
    """Two telemetry replicas sharing one storage volume admit an ID once."""

    def setUp(self):
        self._storage_dir = tempfile.mkdtemp(prefix="infra-health-replica-")
        self.addCleanup(shutil.rmtree, self._storage_dir, ignore_errors=True)
        self._ledger_path = str(
            Path(self._storage_dir) / INFRASTRUCTURE_HEALTH_LEDGER_FILENAME
        )

    def _service(self) -> TelemetryIngestService:
        return TelemetryIngestService(
            schema_path=_SCHEMA_PATH,
            storage_dir=self._storage_dir,
            buffer_backend="memory",
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

    def test_concurrent_replica_processes_admit_exactly_once(self):
        """Cross-process proof: the ledger lock serialises real replicas."""
        event = _infra_event("infra-replica-race-001")
        # Materialise the ledger before forking children so every replica opens
        # the same file rather than racing on its creation.
        InfrastructureHealthAdmissionLedger(self._ledger_path)

        context = multiprocessing.get_context("fork")
        replica_count = 4
        barrier = context.Barrier(replica_count)
        processes = []
        connections = []
        for _ in range(replica_count):
            parent_conn, child_conn = context.Pipe(duplex=False)
            process = context.Process(
                target=_replica_admit,
                args=(self._ledger_path, event, barrier, child_conn),
            )
            process.start()
            child_conn.close()
            processes.append(process)
            connections.append(parent_conn)

        outcomes = []
        try:
            for connection in connections:
                self.assertTrue(
                    connection.poll(30),
                    "replica process did not report an admission outcome",
                )
                outcomes.append(connection.recv())
        finally:
            for process in processes:
                process.join(30)
            for connection in connections:
                connection.close()

        self.assertEqual(
            outcomes.count("admitted"),
            1,
            f"exactly one replica must admit the event_id, got {outcomes}",
        )
        self.assertEqual(outcomes.count("duplicate"), replica_count - 1, outcomes)

        admitted_records = [
            record
            for record in _ledger_records(self._ledger_path)
            if record.get("state") == "admitted"
        ]
        self.assertEqual(len(admitted_records), 1, admitted_records)

    def test_failed_enqueue_releases_the_reservation_for_retry(self):
        """A reservation must never outlive an enqueue that did not happen."""
        event = _infra_event("infra-release-001")

        async def run() -> tuple[dict, dict, bool]:
            svc = self._service()
            await svc.start()
            try:
                async def _full_buffer(_event, timeout=None):
                    return False

                original_put = svc._buffer.put
                svc._buffer.put = _full_buffer  # type: ignore[assignment]
                overflowed = await svc.ingest_infrastructure_health(event)
                released = not svc._infrastructure_health_ledger.is_admitted(
                    event["event_id"]
                )
                svc._buffer.put = original_put  # type: ignore[assignment]
                retried = await svc.ingest_infrastructure_health(event)
                return overflowed, retried, released
            finally:
                await svc.stop()

        overflowed, retried, released = asyncio.run(run())
        self.assertEqual(overflowed["status"], "rejected")
        self.assertEqual(overflowed["code"], "INFRA_BUFFER_OVERFLOW")
        self.assertTrue(released)
        self.assertEqual(retried["status"], "accepted")

    def test_ingestion_fails_closed_without_a_durable_ledger(self):
        async def run() -> dict:
            svc = TelemetryIngestService(
                schema_path=_SCHEMA_PATH,
                buffer_backend="memory",
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
                buffer_backend="memory",
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


if __name__ == "__main__":
    unittest.main()
