"""Tests for the openclaw-gateway-adapter boundary service."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported without installed deps
# ---------------------------------------------------------------------------

def _stub_foundation():
    """Inject a minimal services.foundation.health stub if not present."""
    if "services.foundation.health" in sys.modules:
        return
    try:
        import services.foundation.health  # noqa: F401
        return
    except ImportError:
        pass
    # Build the package hierarchy
    for pkg in ("services", "services.foundation"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)

    health_mod = types.ModuleType("services.foundation.health")

    def health_payload(service, *, live=True, ready=True, dependencies=None, metrics=None, details=None):
        dep = {}
        if dependencies:
            resolved = dependencies() if callable(dependencies) else dependencies
            for k, v in resolved.items():
                dep[k] = v() if callable(v) else v
        dependency_statuses = [str(v.get("status", "ok")).lower() for v in dep.values() if isinstance(v, dict)]
        status = "ok" if live and ready else "error"
        if status == "ok" and any(value in {"degraded", "error", "unavailable", "failed"} for value in dependency_statuses):
            status = "degraded"
        return {"status": status, "service": service, "live": live, "ready": ready, "dependencies": dep}

    def readiness_status_code(payload):
        return 200 if payload.get("status") == "ok" else 503

    def register_fastapi_health_routes(app, service, *, dependencies=None, metrics=None, details=None):
        from fastapi.responses import JSONResponse

        async def healthz():
            return health_payload(service, dependencies=dependencies, details=details)

        async def livez():
            return health_payload(service, ready=True)

        async def readyz():
            payload = health_payload(service, dependencies=dependencies, details=details)
            return JSONResponse(payload, status_code=readiness_status_code(payload))

        async def metrics_route():
            return {"service": service}

        app.add_api_route("/healthz", healthz, methods=["GET"])
        app.add_api_route("/livez", livez, methods=["GET"])
        app.add_api_route("/readyz", readyz, methods=["GET"])
        app.add_api_route("/metrics", metrics_route, methods=["GET"])

    health_mod.health_payload = health_payload
    health_mod.readiness_status_code = readiness_status_code
    health_mod.register_fastapi_health_routes = register_fastapi_health_routes
    sys.modules["services.foundation.health"] = health_mod
    sys.modules["services.foundation"] = sys.modules.get("services.foundation") or types.ModuleType("services.foundation")


_stub_foundation()

# Add the adapter directory to path so we can import main directly
_ADAPTER_DIR = os.path.join(os.path.dirname(__file__))
if _ADAPTER_DIR not in sys.path:
    sys.path.insert(0, _ADAPTER_DIR)

import main as adapter_main  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(adapter_main.app)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


class TestHealthEndpoints(unittest.TestCase):
    def _patch_upstream(self, reachable: bool):
        result = {"reachable": reachable}
        if not reachable:
            result["reason"] = "connection refused"
        return patch.object(adapter_main, "_probe_upstream", return_value=result)

    def test_livez_always_ok(self):
        with self._patch_upstream(False):
            resp = client.get("/livez")
        self.assertEqual(resp.status_code, 200)

    def test_healthz_ok_when_upstream_reachable(self):
        with self._patch_upstream(True):
            resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("service", body)

    def test_healthz_degrades_when_upstream_absent(self):
        with self._patch_upstream(False):
            resp = client.get("/healthz")
        # healthz always returns 200 (liveness-like); readyz returns 503
        self.assertEqual(resp.status_code, 200)

    def test_readyz_ok_when_upstream_reachable(self):
        with self._patch_upstream(True):
            resp = client.get("/readyz")
        self.assertEqual(resp.status_code, 200)

    def test_readyz_503_when_upstream_absent(self):
        with self._patch_upstream(False):
            resp = client.get("/readyz")
        self.assertEqual(resp.status_code, 503)

    def test_health_compat_alias(self):
        with self._patch_upstream(True):
            resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("service", body)


# ---------------------------------------------------------------------------
# Upstream status
# ---------------------------------------------------------------------------


class TestUpstreamStatus(unittest.TestCase):
    def test_upstream_reachable(self):
        with patch.object(adapter_main, "_probe_upstream", return_value={"reachable": True, "http_status": 200}):
            resp = client.get("/api/openclaw-adapter/upstream/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["reachable"])

    def test_upstream_absent(self):
        with patch.object(adapter_main, "_probe_upstream", return_value={"reachable": False, "reason": "connection refused"}):
            resp = client.get("/api/openclaw-adapter/upstream/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["reachable"])


# ---------------------------------------------------------------------------
# Capability metadata (static — no upstream call)
# ---------------------------------------------------------------------------


class TestCapabilities(unittest.TestCase):
    def test_capabilities_returned_without_upstream(self):
        with patch.object(adapter_main, "_probe_upstream", return_value={"reachable": False}):
            resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["activation_state"], "facade_only")
        self.assertEqual(body["broker_execution"], "deferred")
        self.assertEqual(body["paper_adapter"], "deferred")
        self.assertEqual(body["live_adapter"], "deferred")
        self.assertIn("supported_session_types", body)

    def test_capabilities_not_live_execution(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        body = resp.json()
        self.assertNotEqual(body.get("broker_execution"), "enabled")
        self.assertNotEqual(body.get("live_adapter"), "enabled")


# ---------------------------------------------------------------------------
# Session stubs
# ---------------------------------------------------------------------------


class TestSessions(unittest.TestCase):
    def test_list_sessions_degraded_when_upstream_absent(self):
        with patch.object(adapter_main, "_probe_upstream", return_value={"reachable": False, "reason": "no gateway"}):
            resp = client.get("/api/openclaw-adapter/sessions")
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "upstream_unavailable")
        self.assertEqual(body["sessions"], [])

    def test_list_sessions_deferred_when_upstream_reachable(self):
        with patch.object(adapter_main, "_probe_upstream", return_value={"reachable": True}):
            resp = client.get("/api/openclaw-adapter/sessions")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "deferred")

    def test_create_session_always_deferred(self):
        resp = client.post(
            "/api/openclaw-adapter/sessions",
            json={"agent_id": "agent-test", "session_type": "interactive"},
        )
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "deferred")
        self.assertEqual(body["error_code"], "CAPABILITY_DENIED")
        self.assertFalse(body["retryable"])


# ---------------------------------------------------------------------------
# Production broker guard
# ---------------------------------------------------------------------------


class TestProductionGuard(unittest.TestCase):
    def test_production_broker_disabled_by_default(self):
        self.assertFalse(adapter_main._PRODUCTION_BROKER_ENABLED)

    def test_paper_adapter_disabled_by_default(self):
        self.assertFalse(adapter_main._PAPER_ADAPTER_ENABLED)

    def test_live_adapter_disabled_by_default(self):
        self.assertFalse(adapter_main._LIVE_ADAPTER_ENABLED)

    def test_capital_binding_disabled_by_default(self):
        self.assertFalse(adapter_main._CAPITAL_BINDING_ENABLED)


class TestCapabilityFenceCompleteness(unittest.TestCase):
    """Verify the capability snapshot exposes all fail-closed execution paths."""

    def test_capabilities_capital_binding_deferred(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["capital_binding"], "deferred")

    def test_capabilities_fail_closed_flag_set(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["fail_closed"])

    def test_capabilities_activation_gates_present(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        gates = body.get("activation_gates", {})
        self.assertIn("broker_execution", gates)
        self.assertIn("paper_adapter", gates)
        self.assertIn("live_adapter", gates)
        self.assertIn("capital_binding", gates)

    def test_no_execution_paths_enabled(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for field in ("broker_execution", "paper_adapter", "live_adapter", "capital_binding"):
            self.assertNotEqual(body.get(field), "enabled", f"Expected {field} to be deferred, not enabled")


if __name__ == "__main__":
    unittest.main()
