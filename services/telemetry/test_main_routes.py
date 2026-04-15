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


def _make_event(binding_id: str = _KNOWN_BINDING_ID, event_id: str = "evt-001") -> dict:
    return {
        "event_id": event_id,
        "event_type": "pnl_snapshot",
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
        "metrics": {"pnl": 100.0},
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
        )
        asyncio.run_coroutine_threadsafe(svc.start(), loop).result(timeout=5)

        # Inject the pre-wired service and loop into the module globals so that
        # _get_service() and _run_async() work from Flask route handlers.
        _main._loop = loop
        _main._svc = svc

        cls._svc = svc
        cls.client = _main.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if cls._svc is not None:
            asyncio.run_coroutine_threadsafe(
                cls._svc.stop(graceful=True), cls._loop
            ).result(timeout=5)
        _main._svc = None
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


if __name__ == "__main__":
    unittest.main()
