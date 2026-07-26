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
from services.telemetry.ingest_svc import TelemetryIngestService
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


if __name__ == "__main__":
    unittest.main()
