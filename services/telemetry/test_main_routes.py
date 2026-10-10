"""HTTP-layer smoke tests for services.telemetry.main (Flask routes).

Verifies that the deployable Flask surface works correctly when wired with an
authoritative binding_store, covering the two review-required paths:

  1. GET /__health__ returns 200 when the service is running.
  2. POST /api/telemetry/ingest returns 202 for an event with a known binding.
  3. POST /api/telemetry/ingest returns 400 when binding_id is unknown —
     demonstrating that binding-invalid events are rejected at the HTTP surface,
     not just in unit tests that manually inject binding_store.

Run with:
    python3 -m unittest services.telemetry.test_main_routes
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import services.telemetry.main as _main
from services.runtime_auth_inbound import encode_jwt_hs256
from services.telemetry.ingest_svc import TelemetryIngestService, build_postgres_event_reader
from services.telemetry.heartbeat_service import build_telemetry_event_from_runtime_heartbeat
from services.telemetry.lineage_read import LineageReadService
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
from services.telemetry.trade_episode_projection import TradeEpisodeProjectionStore
from services.telemetry.dead_letter import TAG_WRITER_ERROR

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_KNOWN_BINDING_ID = "test-binding-001"
_TENANT_ID = "tenant-alpha"
_AUTH_HEADERS = {
    "Authorization": "Bearer telemetry-test:operator",
    "X-Tenant-Id": _TENANT_ID,
}
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
    """Returns a known binding for _KNOWN_BINDING_ID; None for all others."""

    def get_binding(self, binding_id: str):
        return _KNOWN_BINDING if binding_id == _KNOWN_BINDING_ID else None


def _make_event(
    binding_id: str = _KNOWN_BINDING_ID,
    event_id: str = "evt-001",
    *,
    event_type: str = "pnl_snapshot",
    metrics: dict | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "tenant_id": _TENANT_ID,
        "event_id": event_id,
        "event_type": event_type,
        "created_at": "2026-04-15T12:00:00Z",
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": binding_id,
        "runtime_id": "lean-worker-1",
        "capital_pool_id": "pool-alpha",
        "artifact_id": "artifact-123",
        "artifact_version": "1.0.0",
        "plan_id": "plan-456",
        "persona_capital_binding_id": "pcb-789",
        "target": {"strategy_id": "test-strategy"},
        "metrics": metrics or {"pnl": 100.0},
        "metadata": metadata or {},
    }


_LINEAGE_CORPUS = {
    "metadata": {
        "task_id": "LIN-002-HTTP",
        "projection_updated_at": "2026-04-15T12:00:00Z",
    },
    "node_sets": {
        "source_records": [
            {
                "source_id": "source-http-001",
                "created_at": "2026-04-15T11:55:00Z",
            }
        ],
        "strategy_specs": [
            {
                "strategy_id": "strategy-http-001",
                "source_id": "source-http-001",
                "created_at": "2026-04-15T11:56:00Z",
            }
        ],
        "experiment_runs": [
            {
                "run_id": "run-http-001",
                "strategy_id": "strategy-http-001",
                "created_at": "2026-04-15T11:57:00Z",
            }
        ],
        "candidate_artifacts": [
            {
                "artifact_id": "artifact-123",
                "artifact_version": "1.0.0",
                "run_id": "run-http-001",
                "created_at": "2026-04-15T11:58:00Z",
            }
        ],
        "approval_decisions": [
            {
                "decision_id": "approval-http-001",
                "target_id": "artifact-123",
                "decision_state": "approved",
                "created_at": "2026-04-15T11:59:00Z",
            }
        ],
        "capital_pools": [
            {
                "pool_id": "pool-alpha",
                "single_runtime_enforced": True,
                "created_at": "2026-04-15T12:00:00Z",
            }
        ],
        "persona_capital_bindings": [
            {
                "binding_id": "pcb-789",
                "capital_pool_id": "pool-alpha",
                "created_at": "2026-04-15T12:00:00Z",
            }
        ],
        "deployment_plans": [
            {
                "plan_id": "plan-456",
                "approval_decision_id": "approval-http-001",
                "capital_pool_id": "pool-alpha",
                "binding_id": "pcb-789",
                "artifact_id": "artifact-123",
                "artifact_version": "1.0.0",
                "created_at": "2026-04-15T12:00:00Z",
            }
        ],
        "runtime_bindings": [
            {
                "binding_id": _KNOWN_BINDING_ID,
                "runtime_id": "lean-worker-1",
                "capital_pool_id": "pool-alpha",
                "artifact_id": "artifact-123",
                "artifact_version": "1.0.0",
                "plan_id": "plan-456",
                "persona_capital_binding_id": "pcb-789",
                "status": "active",
                "effective_at": "2026-04-15T12:00:00Z",
            }
        ],
        "telemetry_events": [
            {
                "event_id": "evt-lineage-001",
                "event_type": "pnl_snapshot",
                "binding_id": _KNOWN_BINDING_ID,
                "plan_id": "plan-456",
                "capital_pool_id": "pool-alpha",
                "persona_capital_binding_id": "pcb-789",
                "artifact_id": "artifact-123",
                "artifact_version": "1.0.0",
                "runtime_id": "lean-worker-1",
                "trace_id": "trace-http-001",
                "strategy_id": "strategy-http-001",
                "registry_id": "registry-http-001",
                "event_produced_at": "2026-04-15T12:00:30Z",
            }
        ],
        "broker_order_events": [
            {
                "order_event_id": "boe-http-001",
                "order_id": "order-http-001",
                "trace_id": "trace-http-001",
                "runtime_binding_id": _KNOWN_BINDING_ID,
                "deployment_plan_id": "plan-456",
                "telemetry_event_id": "evt-lineage-001",
                "broker": "paper_broker",
                "order_status": "submitted",
                "created_at": "2026-04-15T12:00:31Z",
            }
        ],
        "evolution_decisions": [
            {
                "decision_id": "evo-http-001",
                "target_type": "candidate_artifact",
                "target_id": "artifact-123",
                "target_version": "1.0.0",
                "action_type": "observe",
                "decision_state": "approved",
                "evidence_refs": [{"ref_type": "telemetry_summary", "ref_id": "trace-http-001"}],
                "created_at": "2026-04-15T12:00:32Z",
            }
        ],
    },
    "query_families": [],
    "benchmark_cases": [],
}


def _with_tenant_scope(corpus: dict, tenant_id: str) -> dict:
    scoped = copy.deepcopy(corpus)
    for records in scoped.get("node_sets", {}).values():
        if not isinstance(records, list):
            continue
        for record in records:
            if isinstance(record, dict):
                record["tenant_id"] = tenant_id
    return scoped


_LINEAGE_CORPUS = _with_tenant_scope(_LINEAGE_CORPUS, _TENANT_ID)


class _AuthorizedClient:
    """Flask test-client wrapper that applies the normal telemetry authority."""

    def __init__(self, client):
        self._client = client

    def _call(self, method: str, *args, **kwargs):
        headers = dict(_AUTH_HEADERS)
        headers.update(kwargs.pop("headers", {}) or {})
        return getattr(self._client, method)(*args, headers=headers, **kwargs)

    def get(self, *args, **kwargs):
        return self._call("get", *args, **kwargs)

    def post(self, *args, **kwargs):
        return self._call("post", *args, **kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMainRoutes(unittest.TestCase):
    """HTTP-surface integration tests using Flask's test client."""

    @classmethod
    def setUpClass(cls):
        cls._old_runtime_manager_url = os.environ.get("PANTHEON_RUNTIME_MANAGER_URL")
        cls._old_telemetry_db_dsn = os.environ.get("TELEMETRY_DB_DSN")
        cls._old_auth_mode = os.environ.get("PANTHEON_TELEMETRY_AUTH_MODE")
        cls._old_allowed_tenants = os.environ.get("PANTHEON_TELEMETRY_ALLOWED_TENANTS")
        os.environ["PANTHEON_RUNTIME_MANAGER_URL"] = "http://runtime-manager.test"
        os.environ["PANTHEON_TELEMETRY_AUTH_MODE"] = "permissive"
        os.environ["PANTHEON_TELEMETRY_ALLOWED_TENANTS"] = _TENANT_ID
        os.environ.pop("TELEMETRY_DB_DSN", None)

        # Start a dedicated asyncio event loop in a daemon thread so the
        # TelemetryIngestService batch writer can run during the test.
        loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        t = threading.Thread(target=_run_loop, daemon=True, name="test-telemetry-loop")
        t.start()
        cls._loop = loop

        svc = TelemetryIngestService(
            batch_size=10,
            batch_interval=0.05,
            binding_store=_StubBindingStore(),
            runtime_summary_store=RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=10_000_000_000),
            trade_episode_projection_store=TradeEpisodeProjectionStore(),
        )
        asyncio.run_coroutine_threadsafe(svc.start(), loop).result(timeout=5)

        # Inject the pre-wired service and loop into the module globals so that
        # _get_service() and _run_async() work from Flask route handlers.
        _main._loop = loop
        _main._svc = svc
        lineage_svc = LineageReadService()
        lineage_svc.load_corpus(_LINEAGE_CORPUS)
        _main._lineage_svc = lineage_svc

        cls._svc = svc
        cls._lineage_svc = lineage_svc
        cls.raw_client = _main.app.test_client()
        cls.client = _AuthorizedClient(cls.raw_client)

    @classmethod
    def tearDownClass(cls):
        if cls._svc is not None:
            asyncio.run_coroutine_threadsafe(
                cls._svc.stop(graceful=True), cls._loop
            ).result(timeout=5)
        _main._svc = None
        _main._lineage_svc = None
        if cls._loop and cls._loop.is_running():
            cls._loop.call_soon_threadsafe(cls._loop.stop)
        _main._loop = None
        if cls._old_runtime_manager_url is None:
            os.environ.pop("PANTHEON_RUNTIME_MANAGER_URL", None)
        else:
            os.environ["PANTHEON_RUNTIME_MANAGER_URL"] = cls._old_runtime_manager_url
        if cls._old_telemetry_db_dsn is None:
            os.environ.pop("TELEMETRY_DB_DSN", None)
        else:
            os.environ["TELEMETRY_DB_DSN"] = cls._old_telemetry_db_dsn
        if cls._old_auth_mode is None:
            os.environ.pop("PANTHEON_TELEMETRY_AUTH_MODE", None)
        else:
            os.environ["PANTHEON_TELEMETRY_AUTH_MODE"] = cls._old_auth_mode
        if cls._old_allowed_tenants is None:
            os.environ.pop("PANTHEON_TELEMETRY_ALLOWED_TENANTS", None)
        else:
            os.environ["PANTHEON_TELEMETRY_ALLOWED_TENANTS"] = cls._old_allowed_tenants

    # --- health ---

    def test_health_returns_200(self):
        resp = self.client.get("/__health__")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "telemetry-ingest")

    # --- ingest: happy path ---

    def test_known_binding_accepted_202(self):
        event = _make_event(
            binding_id=_KNOWN_BINDING_ID,
            event_id="route-known-001",
        )
        resp = self.client.post(
            "/api/telemetry/ingest",
            json=event,
        )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.get_json()["status"], "accepted")

        readback = self.client.get("/api/telemetry/events/route-known-001")
        self.assertEqual(readback.status_code, 200)
        self.assertEqual(readback.get_json(), event)

    def test_batch_requires_at_least_one_durable_acceptance(self):
        wholly_rejected = self.client.post(
            "/api/telemetry/ingest/batch",
            json={
                "events": [
                    _make_event(
                        binding_id="missing-batch-binding",
                        event_id="route-batch-rejected-001",
                    )
                ]
            },
        )
        empty = self.client.post(
            "/api/telemetry/ingest/batch",
            json={"events": []},
        )

        self.assertEqual(wholly_rejected.status_code, 400)
        self.assertEqual(
            wholly_rejected.get_json(),
            {
                "status": "rejected",
                "ingested": 0,
                "rejected": 1,
                "error": {
                    "code": "BATCH_NOT_ACCEPTED",
                    "message": "No telemetry event received a durable acknowledgement",
                },
            },
        )
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.get_json()["error"]["code"], "EMPTY_BATCH")

    def test_mixed_batch_returns_202_with_explicit_partial_semantics(self):
        response = self.client.post(
            "/api/telemetry/ingest/batch",
            json={
                "events": [
                    _make_event(event_id="route-batch-accepted-001"),
                    _make_event(
                        binding_id="missing-batch-binding",
                        event_id="route-batch-rejected-002",
                    ),
                ]
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.get_json(),
            {
                "status": "partially_accepted",
                "ingested": 1,
                "rejected": 1,
            },
        )
        readback = self.client.get(
            "/api/telemetry/events/route-batch-accepted-001"
        )
        self.assertEqual(readback.status_code, 200)

    def test_ingest_rejects_missing_authority(self):
        resp = self.raw_client.post(
            "/api/telemetry/ingest",
            json=_make_event(event_id="route-missing-auth-001"),
            headers={"X-Tenant-Id": _TENANT_ID},
        )
        self.assertEqual(resp.status_code, 401)

    def test_strict_jwt_without_explicit_role_is_forbidden(self):
        secret = "telemetry-strict-test-secret"
        token = encode_jwt_hs256(
            {
                "sub": "roleless-telemetry-caller",
                "allowed_tenants": [_TENANT_ID],
            },
            secret=secret,
        )
        with patch.dict(
            os.environ,
            {
                "PANTHEON_TELEMETRY_AUTH_MODE": "strict",
                "PANTHEON_TELEMETRY_JWT_SECRET": secret,
                # An unrelated runtime-manager default must not grant
                # telemetry authority to a roleless JWT.
                "PANTHEON_RUNTIME_DEFAULT_ROLE": "admin",
            },
            clear=True,
        ):
            resp = self.raw_client.post(
                "/api/telemetry/ingest",
                json=_make_event(event_id="route-roleless-jwt-001"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Tenant-Id": _TENANT_ID,
                },
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()["error"]["code"], "AUTH_FORBIDDEN")

    def test_ingest_rejects_cross_tenant_header_and_payload(self):
        forbidden_scope = self.client.post(
            "/api/telemetry/ingest",
            json=_make_event(event_id="route-forbidden-scope-001"),
            headers={"X-Tenant-Id": "tenant-beta"},
        )
        self.assertEqual(forbidden_scope.status_code, 403)
        self.assertEqual(
            forbidden_scope.get_json()["error"]["code"],
            "TENANT_FORBIDDEN",
        )

        payload_mismatch = self.client.post(
            "/api/telemetry/ingest",
            json={
                **_make_event(event_id="route-payload-mismatch-001"),
                "tenant_id": "tenant-beta",
            },
        )
        self.assertEqual(payload_mismatch.status_code, 403)
        self.assertEqual(
            payload_mismatch.get_json()["error"]["code"],
            "TENANT_PAYLOAD_MISMATCH",
        )

    def test_service_token_can_ingest_only_its_tenant(self):
        with patch.dict(
            os.environ,
            {
                "PANTHEON_TELEMETRY_SERVICE_TOKEN": "telemetry-service-secret",
                "PANTHEON_TELEMETRY_SERVICE_TENANTS": _TENANT_ID,
            },
        ):
            accepted = self.raw_client.post(
                "/api/telemetry/ingest",
                json=_make_event(event_id="route-service-token-001"),
                headers={
                    "Authorization": "Bearer telemetry-service-secret",
                    "X-Tenant-Id": _TENANT_ID,
                },
            )
            forbidden = self.raw_client.post(
                "/api/telemetry/ingest",
                json={
                    **_make_event(event_id="route-service-token-beta-001"),
                    "tenant_id": "tenant-beta",
                },
                headers={
                    "Authorization": "Bearer telemetry-service-secret",
                    "X-Tenant-Id": "tenant-beta",
                },
            )

        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(
            forbidden.get_json()["error"]["code"],
            "TENANT_FORBIDDEN",
        )

    def test_service_token_without_dedicated_tenant_scope_is_forbidden(self):
        with patch.dict(
            os.environ,
            {
                "PANTHEON_TELEMETRY_SERVICE_TOKEN": "telemetry-service-secret",
                "PANTHEON_TELEMETRY_ALLOWED_TENANTS": _TENANT_ID,
            },
            clear=True,
        ):
            resp = self.raw_client.post(
                "/api/telemetry/ingest",
                json=_make_event(event_id="route-unscoped-service-token-001"),
                headers={
                    "Authorization": "Bearer telemetry-service-secret",
                    "X-Tenant-Id": _TENANT_ID,
                },
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            resp.get_json()["error"]["code"],
            "TENANT_SCOPE_UNCONFIGURED",
        )

    def test_exact_event_read_is_tenant_scoped(self):
        event_id = "route-tenant-read-001"
        accepted = self.client.post(
            "/api/telemetry/ingest",
            json=_make_event(event_id=event_id),
        )
        self.assertEqual(accepted.status_code, 202)

        with patch.dict(
            os.environ,
            {"PANTHEON_TELEMETRY_ALLOWED_TENANTS": "tenant-alpha,tenant-beta"},
        ):
            hidden = self.client.get(
                f"/api/telemetry/events/{event_id}",
                headers={"X-Tenant-Id": "tenant-beta"},
            )
        self.assertEqual(hidden.status_code, 404)

    def test_missing_accepted_event_returns_404(self):
        resp = self.client.get("/api/telemetry/events/route-missing-001")

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(
            resp.get_json()["error"]["code"],
            "TELEMETRY_EVENT_NOT_FOUND",
        )

    def test_accepted_event_readback_stays_exact_across_replays(self):
        event = _make_event(
            binding_id=_KNOWN_BINDING_ID,
            event_id="route-exact-replay-001",
        )
        first = self.client.post("/api/telemetry/ingest", json=event)
        exact_replay = self.client.post(
            "/api/telemetry/ingest",
            json=dict(event),
        )
        conflicting_replay = self.client.post(
            "/api/telemetry/ingest",
            json={
                **event,
                "metrics": {
                    **event["metrics"],
                    "pnl": 999.0,
                },
            },
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(exact_replay.status_code, 202)
        self.assertEqual(conflicting_replay.status_code, 400)
        readback = self.client.get(
            "/api/telemetry/events/route-exact-replay-001"
        )
        self.assertEqual(readback.status_code, 200)
        self.assertEqual(readback.get_json(), event)

    def test_readyz_exposes_writer_and_dlq_metrics(self):
        resp = self.client.get("/readyz")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["dependencies"]["canonical_telemetry_table"]["status"], "ok")
        self.assertEqual(payload["dependencies"]["canonical_telemetry_table"]["backend"], "memory")
        self.assertEqual(payload["dependencies"]["telemetry_writer"]["status"], "ok")
        self.assertTrue(payload["dependencies"]["telemetry_writer"]["running"])
        self.assertIn("last_successful_write_at", payload["dependencies"]["telemetry_writer"])
        self.assertIn("failure_dlq_entries", payload["dependencies"]["telemetry_writer"])
        self.assertEqual(payload["dependencies"]["dead_letter_queue"]["status"], "ok")
        self.assertIn("writer_total_written", payload["metrics"])
        self.assertIn("writer_failure_dlq_entries", payload["metrics"])
        self.assertIn("writer_seconds_since_last_successful_write", payload["metrics"])
        self.assertIn("dlq_memory_entries", payload["metrics"])
        self.assertIn("startup_dlq_loaded", payload["metrics"])

    def test_readyz_fails_when_canonical_table_missing(self):
        class _MissingTableConnection:
            async def fetchval(self, _query, table):
                self.table = table
                return None

            async def fetch(self, *_args):
                return []

            async def close(self):
                self.closed = True

        async def connect(dsn, timeout=None):
            self.assertEqual(dsn, "postgresql://example/db")
            self.assertEqual(timeout, 2.0)
            return _MissingTableConnection()

        fake_asyncpg = types.SimpleNamespace(connect=connect)
        with patch.dict(os.environ, {"TELEMETRY_DB_DSN": "postgresql://example/db"}):
            with patch.dict(sys.modules, {"asyncpg": fake_asyncpg}):
                resp = self.client.get("/readyz")

        self.assertEqual(resp.status_code, 503)
        payload = resp.get_json()
        dependency = payload["dependencies"]["canonical_telemetry_table"]
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(dependency["status"], "error")
        self.assertFalse(dependency["table_exists"])
        self.assertIn("scripts/db_migrate.sh", dependency["message"])

    def test_startup_timeout_is_env_backed_and_bounded(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEMETRY_STARTUP_TIMEOUT_SECONDS", None)
            self.assertEqual(_main._startup_timeout_seconds(), 180.0)

        with patch.dict(os.environ, {"TELEMETRY_STARTUP_TIMEOUT_SECONDS": "2.5"}):
            self.assertEqual(_main._startup_timeout_seconds(), 2.5)

        with patch.dict(os.environ, {"TELEMETRY_STARTUP_TIMEOUT_SECONDS": "0"}):
            self.assertEqual(_main._startup_timeout_seconds(), 1.0)

        with patch.dict(os.environ, {"TELEMETRY_STARTUP_TIMEOUT_SECONDS": "invalid"}):
            self.assertEqual(_main._startup_timeout_seconds(), 180.0)

    def test_replay_route_replays_write_failure_entry(self):
        _main._svc._dlq.reject(
            _make_event(
                binding_id=_KNOWN_BINDING_ID,
                event_id="route-dlq-replay-001",
            ),
            tags=[TAG_WRITER_ERROR],
            reason="simulated transient write outage",
        )

        resp = self.client.post("/api/telemetry/replay")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.get_json()["replayed"], 1)

    def test_dlq_read_and_replay_are_tenant_scoped(self):
        _main._svc._dlq.reject(
            {
                **_make_event(event_id="route-dlq-tenant-alpha"),
                "tenant_id": _TENANT_ID,
            },
            tags=[TAG_WRITER_ERROR],
            reason="tenant alpha outage",
        )
        _main._svc._dlq.reject(
            {
                **_make_event(event_id="route-dlq-tenant-beta"),
                "tenant_id": "tenant-beta",
            },
            tags=[TAG_WRITER_ERROR],
            reason="tenant beta outage",
        )

        listing = self.client.get("/api/telemetry/dlq")
        self.assertEqual(listing.status_code, 200)
        listed_ids = {
            entry["event"]["event_id"]
            for entry in listing.get_json()["entries"]
        }
        self.assertIn("route-dlq-tenant-alpha", listed_ids)
        self.assertNotIn("route-dlq-tenant-beta", listed_ids)

        replay = self.client.post("/api/telemetry/replay")
        self.assertEqual(replay.status_code, 200)
        self.assertGreaterEqual(replay.get_json()["replayed"], 1)
        self.assertIsNone(
            _main._svc.get_accepted_event(
                "route-dlq-tenant-beta",
                tenant_id=_TENANT_ID,
            )
        )

    def test_replay_rejects_service_only_role(self):
        with patch.dict(
            os.environ,
            {
                "PANTHEON_TELEMETRY_SERVICE_TOKEN": "telemetry-service-secret",
                "PANTHEON_TELEMETRY_SERVICE_TENANTS": _TENANT_ID,
            },
        ):
            resp = self.raw_client.post(
                "/api/telemetry/replay",
                headers={
                    "Authorization": "Bearer telemetry-service-secret",
                    "X-Tenant-Id": _TENANT_ID,
                },
            )
        self.assertEqual(resp.status_code, 403)

    def test_paper_heartbeat_updates_runtime_summary_route(self):
        resp = self.client.post(
            "/api/telemetry/ingest",
            json=_make_event(
                binding_id=_KNOWN_BINDING_ID,
                event_id="route-heartbeat-summary-001",
                event_type="heartbeat",
                metrics={"heartbeat": 1},
                metadata={
                    "engine_bridge_repo": "ajoe734/pantheon-lean.git",
                    "engine_bridge_path": "pantheon/lean",
                    "engine_bridge_commit": "abc1234",
                },
            ),
        )
        self.assertEqual(resp.status_code, 202)

        summary_resp = self.client.get("/api/telemetry/runtime-summaries/lean-worker-1")
        self.assertEqual(summary_resp.status_code, 200)
        summary = summary_resp.get_json()
        self.assertEqual(summary["last_heartbeat_at"], "2026-04-15T12:00:00Z")
        self.assertEqual(summary["runtime_binding_id"], _KNOWN_BINDING_ID)
        self.assertEqual(summary["deployment_stage"], "paper")
        self.assertEqual(summary["engine_bridge_repo"], "ajoe734/pantheon-lean.git")
        self.assertEqual(summary["engine_bridge_commit"], "abc1234")

        list_resp = self.client.get("/api/telemetry/runtime-summaries")
        self.assertEqual(list_resp.status_code, 200)
        self.assertGreaterEqual(list_resp.get_json()["count"], 1)

    def test_runtime_heartbeat_endpoint_accepts_payload_and_status_query(self):
        heartbeat = {
            "runtime_id": "lean-worker-1",
            "runtime_binding_id": _KNOWN_BINDING_ID,
            "capital_pool_id": "pool-alpha",
            "artifact_id": "artifact-123",
            "deployment_mode": "paper",
            "heartbeat_time": "2026-04-15T12:00:05Z",
            "connectivity_status": "connected",
            "broker_status": "ok",
            "queue_lag_ms": 7,
            "event_delivery_lag_ms": 12,
            "health_summary": {
                "runtime": "ok",
                "telemetry": "ok",
                "broker": "ok",
            },
            "target": {"strategy_id": "strategy-http-001"},
        }

        resp = self.client.post("/api/v1/telemetry/heartbeats", json=heartbeat)
        self.assertEqual(resp.status_code, 202)
        accepted = resp.get_json()
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["runtime_id"], "lean-worker-1")
        self.assertEqual(accepted["runtime_binding_id"], _KNOWN_BINDING_ID)
        self.assertEqual(accepted["heartbeat_status"]["status"], "connected")

        status_resp = self.client.get("/api/v1/telemetry/runtime/lean-worker-1/heartbeat")
        self.assertEqual(status_resp.status_code, 200)
        status = status_resp.get_json()
        self.assertEqual(status["runtime_id"], "lean-worker-1")
        self.assertEqual(status["runtime_binding_id"], _KNOWN_BINDING_ID)
        self.assertEqual(status["last_heartbeat_at"], "2026-04-15T12:00:05Z")
        self.assertEqual(status["deployment_mode"], "paper")
        self.assertEqual(status["status"], "connected")
        self.assertEqual(status["broker_status"], "ok")
        self.assertEqual(status["queue_lag_ms"], 7)
        self.assertEqual(status["event_delivery_lag_ms"], 12)

    def test_runtime_heartbeat_canary_execution_mode_remains_canary(self):
        binding = types.SimpleNamespace(
            binding_id="canary-binding-001",
            runtime_id="lean-worker-canary",
            capital_pool_id="pool-canary",
            artifact_id="artifact-canary",
            artifact_version="1.0.0",
            plan_id="plan-canary",
            persona_capital_binding_id="pcb-canary",
            deployment_mode="canary",
            execution_mode="canary",
        )
        event = build_telemetry_event_from_runtime_heartbeat(
            {
                "runtime_id": "lean-worker-canary",
                "runtime_binding_id": "canary-binding-001",
                "capital_pool_id": "pool-canary",
                "artifact_id": "artifact-canary",
                "deployment_mode": "canary",
                "heartbeat_time": "2026-04-15T12:00:06Z",
                "connectivity_status": "connected",
                "broker_status": "ok",
                "target": {"strategy_id": "strategy-canary"},
            },
            binding=binding,
        )

        self.assertEqual(event["execution_mode"], "canary")
        self.assertEqual(event["deployment_stage"], "canary")

    def test_runtime_heartbeat_endpoint_rejects_unknown_binding(self):
        heartbeat = {
            "runtime_id": "lean-worker-1",
            "runtime_binding_id": "missing-binding-001",
            "capital_pool_id": "pool-alpha",
            "artifact_id": "artifact-123",
            "deployment_mode": "paper",
            "heartbeat_time": "2026-04-15T12:00:10Z",
            "connectivity_status": "connected",
            "broker_status": "ok",
            "health_summary": {},
        }

        resp = self.client.post("/api/v1/telemetry/heartbeats", json=heartbeat)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"]["code"], "BINDING_NOT_FOUND")

    # --- ingest: binding-invalid rejection through HTTP surface ---

    def test_unknown_binding_rejected_400(self):
        """Events with a binding_id not in the binding_store must be rejected
        with HTTP 400 through the Flask surface, not just via injected unit-test stubs."""
        resp = self.client.post(
            "/api/telemetry/ingest",
            json=_make_event(binding_id="nonexistent-binding-xyz", event_id="route-bad-001"),
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data["status"], "rejected")

    # --- ingest: malformed body ---

    def test_non_object_body_returns_400(self):
        resp = self.client.post(
            "/api/telemetry/ingest",
            data=b'"just-a-string"',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    # --- lineage: runtime binding projection ---

    def test_runtime_binding_projection_returns_200(self):
        resp = self.client.get(
            f"/api/telemetry/lineage/runtime-bindings/{_KNOWN_BINDING_ID}/projection"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["target_type"], "runtime_binding")
        self.assertEqual(data["target_id"], _KNOWN_BINDING_ID)
        self.assertIs(data["derived_only"], True)
        self.assertEqual(data["binding_status"], "active")

    def test_telemetry_event_trace_returns_200(self):
        resp = self.client.get("/api/telemetry/lineage/events/evt-lineage-001/trace")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["target_type"], "telemetry_event")
        self.assertEqual(data["target_id"], "evt-lineage-001")
        self.assertEqual(data["event_type"], "pnl_snapshot")
        self.assertIn("trace-http-001", data["refs"]["trace_ids"])
        self.assertIn("strategy-http-001", data["refs"]["strategy_ids"])
        self.assertIn("registry-http-001", data["refs"]["registry_ids"])

    def test_source_runtime_telemetry_trace_returns_200(self):
        resp = self.client.get(
            "/api/telemetry/lineage/traces/trace-http-001/source-runtime-telemetry"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["target_type"], "trace")
        self.assertEqual(data["target_id"], "trace-http-001")
        self.assertIs(data["derived_only"], True)
        self.assertEqual(data["missing_edges"], [])
        self.assertEqual(data["refs"]["source_record_ids"], ["source-http-001"])
        self.assertEqual(data["refs"]["experiment_run_ids"], ["run-http-001"])
        self.assertEqual(data["refs"]["approval_decision_ids"], ["approval-http-001"])
        self.assertEqual(data["refs"]["broker_order_event_ids"], ["boe-http-001"])
        self.assertEqual(data["refs"]["evolution_decision_ids"], ["evo-http-001"])

    def test_all_lineage_routes_are_tenant_scoped_and_hide_legacy_records(self):
        urls = (
            f"/api/telemetry/lineage/runtime-bindings/{_KNOWN_BINDING_ID}/projection",
            "/api/telemetry/lineage/capital-pools/pool-alpha/projection",
            "/api/telemetry/lineage/events/evt-lineage-001/trace",
            "/api/telemetry/lineage/traces/trace-http-001/source-runtime-telemetry",
            "/api/telemetry/lineage/plans/plan-456/forensic-trace",
        )

        for url in urls:
            with self.subTest(url=url, tenant="tenant-alpha"):
                self.assertEqual(self.client.get(url).status_code, 200)

        with patch.dict(
            os.environ,
            {"PANTHEON_TELEMETRY_ALLOWED_TENANTS": "tenant-alpha,tenant-beta"},
        ):
            for url in urls:
                with self.subTest(url=url, tenant="tenant-beta"):
                    hidden = self.client.get(
                        url,
                        headers={"X-Tenant-Id": "tenant-beta"},
                    )
                    self.assertEqual(hidden.status_code, 404)
                    self.assertEqual(
                        hidden.get_json()["error"]["code"],
                        "LINEAGE_TARGET_NOT_FOUND",
                    )

        legacy_corpus = copy.deepcopy(_LINEAGE_CORPUS)
        for records in legacy_corpus["node_sets"].values():
            for record in records:
                record.pop("tenant_id", None)
        legacy_service = LineageReadService()
        legacy_service.load_corpus(legacy_corpus)
        current_service = _main._lineage_svc
        try:
            _main._lineage_svc = legacy_service
            for url in urls:
                with self.subTest(url=url, tenant="legacy-unscoped"):
                    hidden = self.client.get(url)
                    self.assertEqual(hidden.status_code, 404)
        finally:
            _main._lineage_svc = current_service

    def test_missing_lineage_target_returns_404(self):
        resp = self.client.get("/api/telemetry/lineage/events/evt-does-not-exist/trace")
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertEqual(data["error"]["code"], "LINEAGE_TARGET_NOT_FOUND")

    def test_missing_source_runtime_trace_returns_404(self):
        resp = self.client.get(
            "/api/telemetry/lineage/traces/trace-does-not-exist/source-runtime-telemetry"
        )
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertEqual(data["error"]["code"], "LINEAGE_TARGET_NOT_FOUND")

    def test_trade_episodes_list_and_get(self):
        opened_event = {
            "tenant_id": _TENANT_ID,
            "event_id": "00000000-0000-0000-0000-000000000003",
            "schema_version": "1.0",
            "event_type": "trade_episode.opened",
            "occurred_at": "2026-07-11T12:00:00Z",
            "ingested_at": "2026-07-11T12:00:05Z",
            "trace_id": "00000000-0000-0000-0000-000000000001",
            "trade_episode_id": "00000000-0000-0000-0000-000000000002",
            "persona_id": "persona-macro",
            "environment": "paper",
            "producer": "trade-journal-service",
            "sequence_number": 1,
            "payload": {
                "strategy_id": "strategy-quant-01",
                "instrument_id": "SPY",
                "side": "long",
                "thesis": "Fed meeting catalyst",
                "requested_quantity": 100.0,
            }
        }

        # Ingest the event via Flask
        resp = self.client.post("/api/telemetry/ingest", json=opened_event)
        self.assertEqual(resp.status_code, 202)

        # Verify it shows up in list
        resp_list = self.client.get("/api/telemetry/trade-episodes?persona_id=persona-macro")
        self.assertEqual(resp_list.status_code, 200)
        data = resp_list.get_json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["projections"][0]["trade_episode_id"], "00000000-0000-0000-0000-000000000002")

        # Verify it shows up in detail
        resp_detail = self.client.get("/api/telemetry/trade-episodes/00000000-0000-0000-0000-000000000002")
        self.assertEqual(resp_detail.status_code, 200)
        proj = resp_detail.get_json()
        self.assertEqual(proj["status"], "open")
        self.assertEqual(proj["instrument_id"], "SPY")


class TestTelemetryDurableLineageReadRestart(unittest.TestCase):
    """Reproduces and resolves the lineage read restart gap across telemetry restarts.

    Acceptance criteria:
    - Counterexample reproduction:
      - Event d39cabae-3276-4af1-a017-f4c171161ad4 (active binding rb-d16978f8faaa402090d9f8d3fb04936d)
      - Event 69fff59d-83c9-4cf6-b4a7-9deb5d0a5236 (paused binding rb-63be17dc01eb40b48b3302e218079b70)
    - Before wiring Postgres or with fresh in-memory service: 404 (LINEAGE_TARGET_NOT_FOUND).
    - After restart with Postgres event reader wired: 200 with resolved trace and projection.
    - Paused binding maintains read-only historical status 'paused'.
    - Unknown or foreign tenant target fails closed with 404.
    - Unavailable Postgres fails closed with 503 (LINEAGE_UNAVAILABLE / SERVICE_UNAVAILABLE).
    """

    _E1_ID = "d39cabae-3276-4af1-a017-f4c171161ad4"
    _E1_BINDING = "rb-d16978f8faaa402090d9f8d3fb04936d"
    _E2_ID = "69fff59d-83c9-4cf6-b4a7-9deb5d0a5236"
    _E2_BINDING = "rb-63be17dc01eb40b48b3302e218079b70"
    _E_UNVERIFIED_ID = "evt-monitor-negative-961-unverified"
    _E_UNVERIFIED_BINDING = "rb-monitor-negative-961-unverified"
    _TENANT = "tenant-dev"
    _FOREIGN_TENANT = "tenant-foreign"
    _container_name: str | None = None

    @classmethod
    def setUpClass(cls):
        cls._orig_auth_mode = os.environ.get("PANTHEON_TELEMETRY_AUTH_MODE")
        cls._orig_allowed_tenants = os.environ.get("PANTHEON_TELEMETRY_ALLOWED_TENANTS")
        cls._orig_svc = _main._svc
        cls._orig_lineage_svc = _main._lineage_svc

        os.environ["PANTHEON_TELEMETRY_AUTH_MODE"] = "permissive"
        os.environ["PANTHEON_TELEMETRY_ALLOWED_TENANTS"] = f"{cls._TENANT},{cls._FOREIGN_TENANT}"

        cls.dsn = cls._find_pg_dsn()
        if not cls.dsn:
            raise unittest.SkipTest("No owned disposable PostgreSQL available for durable lineage restart test")

        cls._init_db_events()
        cls.client = _main.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if cls._orig_auth_mode is not None:
            os.environ["PANTHEON_TELEMETRY_AUTH_MODE"] = cls._orig_auth_mode
        else:
            os.environ.pop("PANTHEON_TELEMETRY_AUTH_MODE", None)

        if cls._orig_allowed_tenants is not None:
            os.environ["PANTHEON_TELEMETRY_ALLOWED_TENANTS"] = cls._orig_allowed_tenants
        else:
            os.environ.pop("PANTHEON_TELEMETRY_ALLOWED_TENANTS", None)

        _main._svc = cls._orig_svc
        _main._lineage_svc = cls._orig_lineage_svc

        if cls._container_name:
            import subprocess
            subprocess.run(["docker", "rm", "-f", cls._container_name], check=False, capture_output=True)

    @classmethod
    def _find_pg_dsn(cls) -> str | None:
        explicit = os.getenv("TELEMETRY_TEST_PG_DSN")
        if explicit:
            return explicit
        import shutil, socket, subprocess, time, uuid, asyncpg
        if shutil.which("docker") is None:
            return None
        probe = subprocess.run(["docker", "info"], check=False, capture_output=True, text=True)
        if probe.returncode != 0:
            return None
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        cls._container_name = f"pantheon-lineage-qa-{uuid.uuid4().hex[:10]}"
        started = subprocess.run(
            [
                "docker", "run", "--rm", "-d",
                "--name", cls._container_name,
                "-e", "POSTGRES_PASSWORD=postgres",
                "-p", f"127.0.0.1:{port}:5432",
                "postgres:16-alpine",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if started.returncode != 0:
            return None
        dsn = f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                async def _probe():
                    conn = await asyncpg.connect(dsn, timeout=1.0)
                    await conn.close()
                asyncio.run(_probe())
                return dsn
            except Exception:
                time.sleep(0.3)
        return None

    @classmethod
    def _init_db_events(cls):
        import asyncpg
        import datetime
        import json

        async def _setup():
            conn = await asyncpg.connect(cls.dsn)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL,
                    ingested_seq BIGSERIAL,
                    ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
                )
            ''')
            ev1 = {
                "event_id": cls._E1_ID,
                "event_type": "heartbeat",
                "created_at": "2026-10-10T13:36:04Z",
                "tenant_id": cls._TENANT,
                "binding_id": cls._E1_BINDING,
                "runtime_id": "rt-3739472a",
                "artifact_id": "artifact-persona-paper-0a906806ac32538fc7df",
                "artifact_version": "1.0.0",
                "capital_pool_id": "pool-persona-paper-0a906806ac32538fc7df",
                "plan_id": "plan-persona-paper-0a906806ac32538fc7df",
                "persona_capital_binding_id": "pcb-persona-paper-0a906806ac32538fc7df",
                "deployment_mode": "paper",
                "binding_status": "active",
            }
            ev2 = {
                "event_id": cls._E2_ID,
                "event_type": "heartbeat",
                "created_at": "2026-10-10T00:00:11Z",
                "tenant_id": cls._TENANT,
                "binding_id": cls._E2_BINDING,
                "runtime_id": "rt-04c8e486",
                "artifact_id": "artifact-persona-paper-c3f293d04cd3cb396e92",
                "artifact_version": "1.0.0",
                "capital_pool_id": "pool-persona-paper-c3f293d04cd3cb396e92",
                "plan_id": "plan-persona-paper-c3f293d04cd3cb396e92",
                "persona_capital_binding_id": "pcb-persona-paper-c3f293d04cd3cb396e92",
                "deployment_mode": "paper",
                "binding_status": "paused",
                "effective_at": "2026-10-07T22:17:32Z",
            }
            ev3_unverified = {
                "event_id": cls._E_UNVERIFIED_ID,
                "event_type": "heartbeat",
                "created_at": "2026-10-10T12:00:00Z",
                "tenant_id": cls._TENANT,
                "binding_id": cls._E_UNVERIFIED_BINDING,
                "runtime_id": "rt-unverified",
            }
            for ev in [ev1, ev2, ev3_unverified]:
                dt = datetime.datetime.fromisoformat(ev["created_at"].replace("Z", "+00:00"))
                await conn.execute(f'''
                    INSERT INTO telemetry_events (event_id, event_type, created_at, payload)
                    VALUES ('{ev["event_id"]}', '{ev["event_type"]}', '{dt.isoformat()}', '{json.dumps(ev)}')
                    ON CONFLICT (event_id) DO UPDATE SET payload = EXCLUDED.payload
                ''')
            await conn.close()

        asyncio.run(_setup())


    def _auth_headers(self, tenant: str | None = None) -> dict[str, str]:
        return {
            "Authorization": "Bearer telemetry-test:operator",
            "X-Tenant-Id": tenant or self._TENANT,
        }

    def test_01_pre_restart_without_pg_reader_returns_404(self):
        """Before PG reader is wired or after memory-only cache flush, endpoints fail closed with 404."""
        _main._lineage_svc = LineageReadService()
        _main._svc = types.SimpleNamespace(get_accepted_event=lambda eid, tenant_id=None: None)

        headers = self._auth_headers()
        r_trace = self.client.get(f"/api/telemetry/lineage/events/{self._E1_ID}/trace", headers=headers)
        self.assertEqual(r_trace.status_code, 404)
        self.assertEqual(r_trace.get_json()["error"]["code"], "LINEAGE_TARGET_NOT_FOUND")

        r_proj = self.client.get(f"/api/telemetry/lineage/runtime-bindings/{self._E2_BINDING}/projection", headers=headers)
        self.assertEqual(r_proj.status_code, 404)
        self.assertEqual(r_proj.get_json()["error"]["code"], "LINEAGE_TARGET_NOT_FOUND")

        r_evt = self.client.get(f"/api/telemetry/events/{self._E1_ID}", headers=headers)
        self.assertEqual(r_evt.status_code, 404)
        self.assertEqual(r_evt.get_json()["error"]["code"], "TELEMETRY_EVENT_NOT_FOUND")

    def test_02_post_restart_with_pg_reader_resolves_event_trace_200(self):
        """After restart with PG reader wired, event trace resolves without re-ingest."""
        fetch_ev, fetch_b = build_postgres_event_reader(self.dsn, table="telemetry_events")
        _main._lineage_svc = LineageReadService(event_reader=fetch_ev, binding_events_reader=fetch_b)
        _main._svc = types.SimpleNamespace(
            get_accepted_event=lambda eid, tenant_id=None: fetch_ev(eid) if (not tenant_id or fetch_ev(eid).get("tenant_id") == tenant_id) else None
        )

        headers = self._auth_headers()
        resp = self.client.get(f"/api/telemetry/lineage/events/{self._E1_ID}/trace", headers=headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["target_id"], self._E1_ID)
        self.assertEqual(data["conflict_markers"], [])
        upstream_bindings = [node["id"] for node in data.get("upstream_chain", []) if node.get("type") == "runtime_binding"]
        self.assertIn(self._E1_BINDING, upstream_bindings)

    def test_03_post_restart_with_pg_reader_resolves_paused_binding_projection_200(self):
        """After restart with PG reader, paused binding projection resolves with status='paused'."""
        fetch_ev, fetch_b = build_postgres_event_reader(self.dsn, table="telemetry_events")
        _main._lineage_svc = LineageReadService(event_reader=fetch_ev, binding_events_reader=fetch_b)
        _main._svc = types.SimpleNamespace(
            get_accepted_event=lambda eid, tenant_id=None: fetch_ev(eid) if (not tenant_id or fetch_ev(eid).get("tenant_id") == tenant_id) else None
        )

        headers = self._auth_headers()
        resp = self.client.get(f"/api/telemetry/lineage/runtime-bindings/{self._E2_BINDING}/projection", headers=headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["target_id"], self._E2_BINDING)
        self.assertEqual(data["binding_status"], "paused")
        self.assertGreaterEqual(data.get("telemetry_event_count", 0), 1)
        downstream_events = [node["id"] for node in data.get("downstream_chain", []) if node.get("type") == "telemetry_event"]
        self.assertIn(self._E2_ID, downstream_events)

    def test_04_post_restart_with_pg_reader_resolves_accepted_event_200(self):
        """After restart with PG reader, accepted event route returns exact stored payload."""
        fetch_ev, fetch_b = build_postgres_event_reader(self.dsn, table="telemetry_events")
        _main._lineage_svc = LineageReadService(event_reader=fetch_ev, binding_events_reader=fetch_b)
        _main._svc = types.SimpleNamespace(
            get_accepted_event=lambda eid, tenant_id=None: fetch_ev(eid) if (not tenant_id or fetch_ev(eid).get("tenant_id") == tenant_id) else None
        )

        headers = self._auth_headers()
        resp = self.client.get(f"/api/telemetry/events/{self._E1_ID}", headers=headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["event_id"], self._E1_ID)
        self.assertEqual(data["binding_id"], self._E1_BINDING)
        self.assertEqual(data["tenant_id"], self._TENANT)

    def test_05_foreign_tenant_fails_closed_with_404(self):
        """Foreign tenant request cannot access stored event or binding and fails closed with 404."""
        fetch_ev, fetch_b = build_postgres_event_reader(self.dsn, table="telemetry_events")
        _main._lineage_svc = LineageReadService(event_reader=fetch_ev, binding_events_reader=fetch_b)
        _main._svc = types.SimpleNamespace(
            get_accepted_event=lambda eid, tenant_id=None: fetch_ev(eid) if (not tenant_id or fetch_ev(eid).get("tenant_id") == tenant_id) else None
        )

        foreign_headers = self._auth_headers(tenant=self._FOREIGN_TENANT)

        r_trace = self.client.get(f"/api/telemetry/lineage/events/{self._E1_ID}/trace", headers=foreign_headers)
        self.assertEqual(r_trace.status_code, 404)
        self.assertEqual(r_trace.get_json()["error"]["code"], "LINEAGE_TARGET_NOT_FOUND")

        r_proj = self.client.get(f"/api/telemetry/lineage/runtime-bindings/{self._E2_BINDING}/projection", headers=foreign_headers)
        self.assertEqual(r_proj.status_code, 404)
        self.assertEqual(r_proj.get_json()["error"]["code"], "LINEAGE_TARGET_NOT_FOUND")

        r_evt = self.client.get(f"/api/telemetry/events/{self._E1_ID}", headers=foreign_headers)
        self.assertEqual(r_evt.status_code, 404)
        self.assertEqual(r_evt.get_json()["error"]["code"], "TELEMETRY_EVENT_NOT_FOUND")

    def test_06_unavailable_postgres_fails_closed_with_503(self):
        """Unreachable Postgres causes lineage queries and event reads to fail closed with 503."""
        unreachable_dsn = "postgresql://postgres:pw@127.0.0.1:59999/postgres"
        bad_ev, bad_b = build_postgres_event_reader(unreachable_dsn)
        _main._lineage_svc = LineageReadService(event_reader=bad_ev, binding_events_reader=bad_b)
        _main._svc = types.SimpleNamespace(get_accepted_event=lambda eid, tenant_id=None: bad_ev(eid))

        headers = self._auth_headers()
        r_trace = self.client.get(f"/api/telemetry/lineage/events/{self._E1_ID}/trace", headers=headers)
        self.assertEqual(r_trace.status_code, 503)
        self.assertEqual(r_trace.get_json()["error"]["code"], "LINEAGE_UNAVAILABLE")

        r_evt = self.client.get(f"/api/telemetry/events/{self._E1_ID}", headers=headers)
        self.assertEqual(r_evt.status_code, 503)
        self.assertEqual(r_evt.get_json()["error"]["code"], "SERVICE_UNAVAILABLE")

    def test_07_unverified_binding_without_status_fails_closed_with_404_not_manufactured_active(self):
        """Unverified binding without authoritative store or persisted status fails closed with 404, not manufactured active."""
        fetch_ev, fetch_b = build_postgres_event_reader(self.dsn, table="telemetry_events")
        _main._lineage_svc = LineageReadService(event_reader=fetch_ev, binding_events_reader=fetch_b)
        _main._svc = types.SimpleNamespace(
            get_accepted_event=lambda eid, tenant_id=None: fetch_ev(eid, tenant_id=tenant_id)
        )

        headers = self._auth_headers()
        r_proj = self.client.get(f"/api/telemetry/lineage/runtime-bindings/{self._E_UNVERIFIED_BINDING}/projection", headers=headers)
        self.assertEqual(r_proj.status_code, 404)
        self.assertEqual(r_proj.get_json()["error"]["code"], "LINEAGE_TARGET_NOT_FOUND")

        r_trace = self.client.get(f"/api/telemetry/lineage/events/{self._E_UNVERIFIED_ID}/trace", headers=headers)
        self.assertEqual(r_trace.status_code, 404)
        self.assertEqual(r_trace.get_json()["error"]["code"], "LINEAGE_TARGET_NOT_FOUND")

        direct_proj = _main._lineage_svc.query("runtime_binding_projection", binding_id=self._E_UNVERIFIED_BINDING, tenant_id=self._TENANT)
        self.assertIsNone(direct_proj.get("binding_status"))
        self.assertTrue(any(m.get("code") == "node_not_found" for m in direct_proj.get("conflict_markers", [])))

    def test_08_owned_pg_exclusive_lock_fetch_deadline_fails_closed_within_bounds(self):
        """Under concurrent ACCESS EXCLUSIVE table lock, fetch fails closed within 5.5s with RuntimeError."""
        import threading, time, asyncio, asyncpg
        held = threading.Event()

        async def _hold():
            conn = await asyncpg.connect(self.dsn)
            try:
                async with conn.transaction():
                    await conn.execute("LOCK TABLE telemetry_events IN ACCESS EXCLUSIVE MODE")
                    held.set()
                    await asyncio.sleep(6.0)
            finally:
                await conn.close()

        t = threading.Thread(target=lambda: asyncio.run(_hold()))
        t.start()
        try:
            self.assertTrue(held.wait(5.0))
            fetch_ev, _ = build_postgres_event_reader(self.dsn, table="telemetry_events")
            start = time.monotonic()
            with self.assertRaises(RuntimeError):
                fetch_ev(self._E1_ID, tenant_id=self._TENANT)
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 5.5, f"fetch did not time out within bounded deadline: {elapsed}s")
            self.assertGreaterEqual(elapsed, 4.5, f"fetch deadline unexpectedly short: {elapsed}s")
        finally:
            t.join()

    def test_09_mismatched_and_missing_provenance_fails_closed_with_404(self):
        """Mismatched canonical owner tenant, runtime, artifact, or missing binding fails closed with 404."""
        fetch_ev, fetch_b = build_postgres_event_reader(self.dsn, table="telemetry_events")

        class FixtureBindingStore:
            def __init__(self, mode: str):
                self.mode = mode

            def get_binding(self, bid: str):
                if self.mode == "missing":
                    return None
                if self.mode == "foreign":
                    return {
                        "binding_id": bid,
                        "tenant_id": TestTelemetryDurableLineageReadRestart._FOREIGN_TENANT,
                        "runtime_id": "rt-3739472a",
                        "artifact_id": "artifact-persona-paper-0a906806ac32538fc7df",
                        "artifact_version": "1.0.0",
                        "status": "active",
                    }
                if self.mode == "mismatched_runtime":
                    return {
                        "binding_id": bid,
                        "tenant_id": TestTelemetryDurableLineageReadRestart._TENANT,
                        "runtime_id": "rt-different",
                        "artifact_id": "artifact-persona-paper-0a906806ac32538fc7df",
                        "artifact_version": "1.0.0",
                        "status": "active",
                    }
                if self.mode == "mismatched_artifact":
                    return {
                        "binding_id": bid,
                        "tenant_id": TestTelemetryDurableLineageReadRestart._TENANT,
                        "runtime_id": "rt-3739472a",
                        "artifact_id": "artifact-different",
                        "artifact_version": "1.0.0",
                        "status": "active",
                    }
                return None

        headers = self._auth_headers()
        for mode in ("foreign", "mismatched_runtime", "mismatched_artifact", "missing"):
            _main._lineage_svc = LineageReadService(
                event_reader=fetch_ev,
                binding_events_reader=fetch_b,
                binding_store=FixtureBindingStore(mode),
            )
            r_trace = self.client.get(f"/api/telemetry/lineage/events/{self._E1_ID}/trace", headers=headers)
            self.assertEqual(r_trace.status_code, 404, f"mode={mode} expected 404, got {r_trace.status_code}")
            self.assertEqual(r_trace.get_json()["error"]["code"], "LINEAGE_TARGET_NOT_FOUND")

            direct = _main._lineage_svc.query("telemetry_event_trace", event_id=self._E1_ID, tenant_id=self._TENANT)
            self.assertTrue(any(m.get("code") == "node_not_found" for m in direct.get("conflict_markers", [])))
            self.assertEqual(direct.get("refs", {}).get("runtime_binding_ids", []), [])

    def test_10_canonical_owner_metadata_tenant_envelope_regressions(self):
        """Owner top-level and metadata tenant envelope validation: flat-only, metadata-only, matching, conflicting, foreign, missing, and invalid."""
        fetch_ev, fetch_b = build_postgres_event_reader(self.dsn, table="telemetry_events")

        class EnvelopeBindingStore:
            def __init__(self, mode: str):
                self.mode = mode

            def get_binding(self, bid: str):
                base_data = {
                    "binding_id": bid,
                    "runtime_id": "rt-3739472a",
                    "artifact_id": "artifact-persona-paper-0a906806ac32538fc7df",
                    "artifact_version": "1.0.0",
                    "capital_pool_id": "pool-persona-paper-0a906806ac32538fc7df",
                    "plan_id": "plan-persona-paper-0a906806ac32538fc7df",
                    "status": "active",
                }
                if self.mode == "flat_only":
                    return {**base_data, "tenant_id": TestTelemetryDurableLineageReadRestart._TENANT, "metadata": {}}
                if self.mode == "metadata_only_dict":
                    return {**base_data, "metadata": {"tenant_id": TestTelemetryDurableLineageReadRestart._TENANT}}
                if self.mode == "metadata_only_namespace":
                    return types.SimpleNamespace(**base_data, metadata={"tenant_id": TestTelemetryDurableLineageReadRestart._TENANT})
                if self.mode == "both_matching":
                    return {**base_data, "tenant_id": TestTelemetryDurableLineageReadRestart._TENANT, "metadata": {"tenant_id": TestTelemetryDurableLineageReadRestart._TENANT}}
                if self.mode == "conflicting":
                    return {**base_data, "tenant_id": TestTelemetryDurableLineageReadRestart._TENANT, "metadata": {"tenant_id": TestTelemetryDurableLineageReadRestart._FOREIGN_TENANT}}
                if self.mode == "foreign_metadata":
                    return {**base_data, "metadata": {"tenant_id": TestTelemetryDurableLineageReadRestart._FOREIGN_TENANT}}
                if self.mode == "missing":
                    return {**base_data, "metadata": {}}
                if self.mode == "invalid_metadata":
                    return {**base_data, "metadata": "invalid_not_an_envelope"}
                if self.mode == "recursive_not_inferred":
                    return {**base_data, "metadata": {"runtime_context": {"tenant_id": TestTelemetryDurableLineageReadRestart._TENANT}}}
                if self.mode == "numeric_metadata":
                    return {**base_data, "metadata": {"tenant_id": 123}}
                if self.mode == "numeric_toplevel":
                    return {**base_data, "tenant_id": 123}
                if self.mode == "empty_metadata":
                    return {**base_data, "metadata": {"tenant_id": ""}}
                if self.mode == "empty_toplevel":
                    return {**base_data, "tenant_id": ""}
                if self.mode == "boolean_metadata":
                    return {**base_data, "metadata": {"tenant_id": True}}
                return None

        headers = self._auth_headers()

        # Success modes: flat-only, metadata-only dict, metadata-only namespace, both matching
        for mode in ("flat_only", "metadata_only_dict", "metadata_only_namespace", "both_matching"):
            _main._lineage_svc = LineageReadService(
                event_reader=fetch_ev,
                binding_events_reader=fetch_b,
                binding_store=EnvelopeBindingStore(mode),
            )
            r_trace = self.client.get(f"/api/telemetry/lineage/events/{self._E1_ID}/trace", headers=headers)
            self.assertEqual(r_trace.status_code, 200, f"mode={mode} expected 200, got {r_trace.status_code}")
            data = r_trace.get_json()
            self.assertEqual(data["conflict_markers"], [])
            upstream_bindings = [node["id"] for node in data.get("upstream_chain", []) if node.get("type") == "runtime_binding"]
            self.assertIn(self._E1_BINDING, upstream_bindings)

        # Fail-closed 404 modes: conflicting, foreign_metadata, missing, invalid_metadata, recursive_not_inferred, numeric/empty/boolean
        for mode in ("conflicting", "foreign_metadata", "missing", "invalid_metadata", "recursive_not_inferred", "numeric_metadata", "numeric_toplevel", "empty_metadata", "empty_toplevel", "boolean_metadata"):
            _main._lineage_svc = LineageReadService(
                event_reader=fetch_ev,
                binding_events_reader=fetch_b,
                binding_store=EnvelopeBindingStore(mode),
            )
            r_trace = self.client.get(f"/api/telemetry/lineage/events/{self._E1_ID}/trace", headers=headers)
            self.assertEqual(r_trace.status_code, 404, f"mode={mode} expected 404, got {r_trace.status_code}")
            self.assertEqual(r_trace.get_json()["error"]["code"], "LINEAGE_TARGET_NOT_FOUND")

            direct = _main._lineage_svc.query("telemetry_event_trace", event_id=self._E1_ID, tenant_id=self._TENANT)
            self.assertTrue(any(m.get("code") == "node_not_found" for m in direct.get("conflict_markers", [])))
            self.assertEqual(direct.get("refs", {}).get("runtime_binding_ids", []), [])
            if mode == "numeric_metadata":
                direct_num = _main._lineage_svc.query("telemetry_event_trace", event_id=self._E1_ID, tenant_id="123")
                self.assertTrue(any(m.get("code") == "node_not_found" for m in direct_num.get("conflict_markers", [])))
                self.assertEqual(direct_num.get("refs", {}).get("runtime_binding_ids", []), [])

    def test_11_fresh_reader_recovers_trace_and_paused_projection_with_adapter_metadata_envelope(self):
        """Fresh LineageReadService after restart recovers event trace and paused binding projection with RuntimeBindingAdapter metadata envelope without re-ingest."""
        fetch_ev, fetch_b = build_postgres_event_reader(self.dsn, table="telemetry_events")

        class SimulatedRuntimeBindingAdapter:
            """Simulates actual served537 RuntimeBindingAdapter GET responses (metadata.tenant_id with absent top-level tenant_id)."""
            def get_binding(self, bid: str):
                if bid == TestTelemetryDurableLineageReadRestart._E1_BINDING:
                    return types.SimpleNamespace(
                        binding_id=bid,
                        runtime_id="rt-3739472a",
                        artifact_id="artifact-persona-paper-0a906806ac32538fc7df",
                        artifact_version="1.0.0",
                        capital_pool_id="pool-persona-paper-0a906806ac32538fc7df",
                        plan_id="plan-persona-paper-0a906806ac32538fc7df",
                        status="active",
                        metadata={"tenant_id": TestTelemetryDurableLineageReadRestart._TENANT},
                    )
                if bid == TestTelemetryDurableLineageReadRestart._E2_BINDING:
                    return types.SimpleNamespace(
                        binding_id=bid,
                        runtime_id="rt-04c8e486",
                        artifact_id="artifact-persona-paper-c3f293d04cd3cb396e92",
                        artifact_version="1.0.0",
                        capital_pool_id="pool-persona-paper-c3f293d04cd3cb396e92",
                        plan_id="plan-persona-paper-c3f293d04cd3cb396e92",
                        status="paused",
                        effective_at="2026-10-07T22:17:32Z",
                        metadata={"tenant_id": TestTelemetryDurableLineageReadRestart._TENANT},
                    )
                return None

        # Fresh lineage service (empty graph/cache simulating service restart)
        adapter = SimulatedRuntimeBindingAdapter()
        _main._lineage_svc = LineageReadService(
            event_reader=fetch_ev,
            binding_events_reader=fetch_b,
            binding_store=adapter,
        )
        _main._svc = types.SimpleNamespace(
            get_accepted_event=lambda eid, tenant_id=None: fetch_ev(eid) if (not tenant_id or fetch_ev(eid).get("tenant_id") == tenant_id) else None
        )

        headers = self._auth_headers()

        # 1. Event trace recovers without re-ingest
        r_trace = self.client.get(f"/api/telemetry/lineage/events/{self._E1_ID}/trace", headers=headers)
        self.assertEqual(r_trace.status_code, 200)
        trace_data = r_trace.get_json()
        self.assertEqual(trace_data["target_id"], self._E1_ID)
        self.assertEqual(trace_data["conflict_markers"], [])
        upstream = [node["id"] for node in trace_data.get("upstream_chain", []) if node.get("type") == "runtime_binding"]
        self.assertIn(self._E1_BINDING, upstream)

        # 2. Paused binding projection recovers with status='paused' and downstream events
        r_proj = self.client.get(f"/api/telemetry/lineage/runtime-bindings/{self._E2_BINDING}/projection", headers=headers)
        self.assertEqual(r_proj.status_code, 200)
        proj_data = r_proj.get_json()
        self.assertEqual(proj_data["target_id"], self._E2_BINDING)
        self.assertEqual(proj_data["binding_status"], "paused")
        self.assertGreaterEqual(proj_data.get("telemetry_event_count", 0), 1)
        downstream = [node["id"] for node in proj_data.get("downstream_chain", []) if node.get("type") == "telemetry_event"]
        self.assertIn(self._E2_ID, downstream)

        # 3. Direct query checks on fresh reader
        direct_trace = _main._lineage_svc.query("telemetry_event_trace", event_id=self._E1_ID, tenant_id=self._TENANT)
        self.assertEqual(direct_trace.get("conflict_markers"), [])
        direct_proj = _main._lineage_svc.query("runtime_binding_projection", binding_id=self._E2_BINDING, tenant_id=self._TENANT)
        self.assertEqual(direct_proj.get("binding_status"), "paused")
        self.assertEqual(direct_proj.get("conflict_markers"), [])


if __name__ == "__main__":
    unittest.main()


