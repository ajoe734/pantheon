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
import os
import sys
import threading
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import services.telemetry.main as _main
from services.telemetry.ingest_svc import TelemetryIngestService
from services.telemetry.lineage_read import LineageReadService
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_KNOWN_BINDING_ID = "test-binding-001"
_KNOWN_BINDING = types.SimpleNamespace(
    binding_id=_KNOWN_BINDING_ID,
    runtime_id="lean-worker-1",
    capital_pool_id="pool-alpha",
    artifact_id="artifact-123",
    artifact_version="1.0.0",
    plan_id="plan-456",
    persona_capital_binding_id="pcb-789",
    deployment_mode="paper",
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMainRoutes(unittest.TestCase):
    """HTTP-surface integration tests using Flask's test client."""

    @classmethod
    def setUpClass(cls):
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
            runtime_summary_store=RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=120),
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
        cls.client = _main.app.test_client()

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

    # --- health ---

    def test_health_returns_200(self):
        resp = self.client.get("/__health__")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "telemetry-ingest")

    # --- ingest: happy path ---

    def test_known_binding_accepted_202(self):
        resp = self.client.post(
            "/api/telemetry/ingest",
            json=_make_event(binding_id=_KNOWN_BINDING_ID, event_id="route-known-001"),
        )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.get_json()["status"], "accepted")

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


if __name__ == "__main__":
    unittest.main()
