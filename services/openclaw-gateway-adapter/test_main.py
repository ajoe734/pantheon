"""Tests for the openclaw-gateway-adapter boundary service."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import httpx


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
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["live"])
        self.assertTrue(body["ready"])
        self.assertNotIn("openclaw_gateway", body["dependencies"])

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
        body = resp.json()
        self.assertEqual(body["status"], "degraded")
        self.assertTrue(body["live"])
        self.assertFalse(body["ready"])
        self.assertEqual(body["dependencies"]["openclaw_gateway"]["status"], "degraded")

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


class _FakeProbeResponse:
    def __init__(self, status: int = 200, body: bytes = b'{"status":"ok"}') -> None:
        self._status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._body


class TestUpstreamProbe(unittest.TestCase):
    def test_probe_prefers_readyz_for_upstream_readiness(self):
        calls = []

        def fake_urlopen(req, timeout):
            calls.append(req.full_url)
            return _FakeProbeResponse()

        with (
            patch.object(adapter_main, "OPENCLAW_GATEWAY_URL", "http://openclaw.test"),
            patch.object(adapter_main.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            result = adapter_main._probe_upstream()

        self.assertTrue(result["reachable"])
        self.assertEqual(result["probe"], "/readyz")
        self.assertEqual(calls, ["http://openclaw.test/readyz"])

    def test_probe_falls_back_to_healthz_for_legacy_upstream(self):
        calls = []

        def fake_urlopen(req, timeout):
            calls.append(req.full_url)
            if req.full_url.endswith("/readyz"):
                raise adapter_main.urllib.error.HTTPError(
                    req.full_url,
                    404,
                    "not found",
                    hdrs=None,
                    fp=None,
                )
            return _FakeProbeResponse()

        with (
            patch.object(adapter_main, "OPENCLAW_GATEWAY_URL", "http://openclaw.test"),
            patch.object(adapter_main.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            result = adapter_main._probe_upstream()

        self.assertTrue(result["reachable"])
        self.assertEqual(result["probe"], "/healthz")
        self.assertEqual(
            calls,
            [
                "http://openclaw.test/readyz",
                "http://openclaw.test/healthz",
            ],
        )


# ---------------------------------------------------------------------------
# Capability metadata (static — no upstream call)
# ---------------------------------------------------------------------------


class TestCapabilities(unittest.TestCase):
    def test_capabilities_returned_without_upstream(self):
        error = adapter_main.UpstreamClientError(
            status_code=503,
            error_code="UPSTREAM_UNAVAILABLE",
            message="gateway absent",
            retryable=True,
        )
        mock_client = MagicMock()
        mock_client.get_capabilities.side_effect = error
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["activation_state"], "upstream_client_degraded")
        self.assertEqual(body["broker_execution"], "deferred")
        self.assertEqual(body["paper_adapter"], "deferred")
        self.assertEqual(body["live_adapter"], "deferred")
        self.assertEqual(body["canary_adapter"], "deferred")
        self.assertFalse(body["paper_broker"]["paper_adapter_enabled"])
        self.assertEqual(body["paper_broker"]["runtime_binding_check"], "required_for_submit")
        self.assertIn("supported_session_types", body)
        self.assertEqual(body["upstream"]["status"], "degraded")

    def test_capabilities_include_upstream_when_available(self):
        mock_client = MagicMock()
        mock_client.get_capabilities.return_value = {"tools": ["shell"], "sessions": True}
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["activation_state"], "upstream_client_ready")
        self.assertEqual(body["upstream"]["status"], "ok")
        self.assertEqual(body["upstream"]["capabilities"]["tools"], ["shell"])

    def test_capabilities_not_live_execution(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        body = resp.json()
        self.assertNotEqual(body.get("broker_execution"), "enabled")
        self.assertNotEqual(body.get("live_adapter"), "enabled")

    def test_capabilities_include_governed_search(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["governed_search"], "enabled")
        self.assertIn("governed_search", body["activation_gates"])


# ---------------------------------------------------------------------------
# Session stubs
# ---------------------------------------------------------------------------


class TestSessions(unittest.TestCase):
    def test_list_sessions_degraded_when_upstream_absent(self):
        mock_client = MagicMock()
        mock_client.list_sessions.side_effect = adapter_main.UpstreamClientError(
            status_code=503,
            error_code="UPSTREAM_UNAVAILABLE",
            message="no gateway",
            retryable=True,
        )
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.get("/api/openclaw-adapter/sessions")
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "upstream_unavailable")
        self.assertEqual(body["sessions"], [])
        self.assertEqual(body["upstream"]["error_code"], "UPSTREAM_UNAVAILABLE")

    def test_list_sessions_uses_upstream_client_when_available(self):
        mock_client = MagicMock()
        mock_client.list_sessions.return_value = [
            {
                "session_id": "sess-1",
                "agent_id": "agent-test",
                "session_type": "interactive",
                "status": "running",
            }
        ]
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.get("/api/openclaw-adapter/sessions")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["sessions"][0]["session_id"], "sess-1")

    def test_get_session_uses_upstream_client_when_available(self):
        mock_client = MagicMock()
        mock_client.get_session.return_value = {
            "session_id": "sess-1",
            "agent_id": "agent-test",
            "session_type": "interactive",
            "status": "running",
        }
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.get("/api/openclaw-adapter/sessions/sess-1")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["session"]["session_id"], "sess-1")
        mock_client.get_session.assert_called_once_with("sess-1")

    def test_create_session_uses_upstream_client_when_available(self):
        mock_client = MagicMock()
        mock_client.create_session.return_value = {
            "session_id": "sess-2",
            "agent_id": "agent-test",
            "session_type": "interactive",
            "status": "created",
        }
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.post(
                "/api/openclaw-adapter/sessions",
                json={"agent_id": "agent-test", "session_type": "interactive"},
            )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["session"]["session_id"], "sess-2")

    def test_cancel_session_uses_upstream_client_when_available(self):
        mock_client = MagicMock()
        mock_client.cancel_session.return_value = {
            "session_id": "sess-2",
            "agent_id": "agent-test",
            "session_type": "interactive",
            "status": "cancel_requested",
        }
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.post("/api/openclaw-adapter/sessions/sess-2/cancel")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["session"]["status"], "cancel_requested")

    def test_create_session_maps_upstream_timeout(self):
        mock_client = MagicMock()
        mock_client.create_session.side_effect = adapter_main.UpstreamClientError(
            status_code=504,
            error_code="UPSTREAM_TIMEOUT",
            message="timeout",
            retryable=True,
        )
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.post(
                "/api/openclaw-adapter/sessions",
                json={"agent_id": "agent-test", "session_type": "interactive"},
            )
        self.assertEqual(resp.status_code, 504)
        body = resp.json()
        self.assertEqual(body["error_code"], "UPSTREAM_TIMEOUT")
        self.assertTrue(body["retryable"])


class TestUpstreamClient(unittest.TestCase):
    def test_http_session_list_is_normalized(self):
        real_client = httpx.Client
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"sessions": [{"id": "sess-1", "agent": "agent-test", "type": "interactive", "status": "running"}]},
            )
        )
        with patch.object(adapter_main.httpx, "Client", side_effect=lambda *args, **kwargs: real_client(transport=transport)):
            sessions = adapter_main.OpenClawUpstreamClient(
                "http://openclaw.test",
                timeout=1,
                retries=0,
            ).list_sessions()
        self.assertEqual(sessions[0]["session_id"], "sess-1")
        self.assertEqual(sessions[0]["agent_id"], "agent-test")

    def test_http_500_retries_then_succeeds(self):
        real_client = httpx.Client
        calls = {"count": 0}

        def handler(request):
            calls["count"] += 1
            if calls["count"] == 1:
                return httpx.Response(500, json={"error": "not ready"})
            return httpx.Response(200, json={"sessions": []})

        transport = httpx.MockTransport(handler)
        with patch.object(adapter_main.httpx, "Client", side_effect=lambda *args, **kwargs: real_client(transport=transport)):
            sessions = adapter_main.OpenClawUpstreamClient(
                "http://openclaw.test",
                timeout=1,
                retries=1,
            ).list_sessions()
        self.assertEqual(sessions, [])
        self.assertEqual(calls["count"], 2)

    def test_timeout_maps_to_retryable_gateway_timeout(self):
        real_client = httpx.Client
        transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.TimeoutException("slow")))
        with patch.object(adapter_main.httpx, "Client", side_effect=lambda *args, **kwargs: real_client(transport=transport)):
            with self.assertRaises(adapter_main.UpstreamClientError) as ctx:
                adapter_main.OpenClawUpstreamClient(
                    "http://openclaw.test",
                    timeout=1,
                    retries=0,
                ).list_sessions()
        self.assertEqual(ctx.exception.status_code, 504)
        self.assertEqual(ctx.exception.error_code, "UPSTREAM_TIMEOUT")
        self.assertTrue(ctx.exception.retryable)


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

    def test_canary_adapter_disabled_by_default(self):
        self.assertFalse(adapter_main._CANARY_ADAPTER_ENABLED)

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
        self.assertIn("canary_adapter", gates)
        self.assertIn("capital_binding", gates)

    def test_no_execution_paths_enabled(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for field in ("broker_execution", "paper_adapter", "live_adapter", "canary_adapter", "capital_binding"):
            self.assertNotEqual(body.get(field), "enabled", f"Expected {field} to be deferred, not enabled")


# ---------------------------------------------------------------------------
# Governed search route
# ---------------------------------------------------------------------------


class TestGovernedSearchRoute(unittest.TestCase):
    def test_search_requires_operator_id(self):
        resp = client.post(
            "/api/openclaw-adapter/search/query",
            json={
                "query": "momentum volatility",
                "persona_id": "persona-alpha",
                "workspace_id": "workspace-research",
            },
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error_code"], "SEARCH_OPERATOR_REQUIRED")

    def test_search_returns_sanitized_citation_pack(self):
        fake_repo = MagicMock()
        fake_search = MagicMock()
        fake_search.search.return_value = {
            "status": "ok",
            "request_id": "search-1",
            "trace_id": "trace-1",
            "results": [
                {
                    "evidence_bundle_id": "evbundle-1",
                    "citation_pack": [
                        {
                            "citation_label": "note#1",
                            "evidence_bundle_id": "evbundle-1",
                        }
                    ],
                    "relevance_score": 0.75,
                }
            ],
            "rejected_items_count": 0,
            "filters_applied": {
                "pre_ranking_filter": "acl_license_workspace_environment",
                "available_time": "not_future",
            },
        }
        with (
            patch.object(adapter_main, "_OPENCLAW_SEARCH_REPOSITORY", fake_repo),
            patch.object(adapter_main, "_OPENCLAW_SEARCH_GATEWAY", fake_search),
        ):
            resp = client.post(
                "/api/openclaw-adapter/search/query",
                json={
                    "request_id": "search-1",
                    "trace_id": "trace-1",
                    "query": "momentum volatility",
                    "persona_id": "persona-alpha",
                    "workspace_id": "workspace-research",
                    "access_scopes": ["research"],
                    "license_scopes": ["internal"],
                    "source_types": ["internal_note"],
                },
                headers={"X-Operator-Id": "op-1"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        result = body["results"][0]
        self.assertEqual(result["evidence_bundle_id"], "evbundle-1")
        self.assertEqual(result["citation_pack"][0]["citation_label"], "note#1")
        self.assertNotIn("answer_context", result)
        self.assertNotIn("matched_items", result)
        self.assertNotIn("raw_payload", result)
        fake_repo.reload.assert_called_once()
        fake_search.search.assert_called_once()

    def test_search_policy_error_maps_to_400(self):
        fake_repo = MagicMock()
        fake_search = MagicMock()
        fake_search.search.side_effect = adapter_main.OpenClawSearchPolicyError("query is required")
        with (
            patch.object(adapter_main, "_OPENCLAW_SEARCH_REPOSITORY", fake_repo),
            patch.object(adapter_main, "_OPENCLAW_SEARCH_GATEWAY", fake_search),
        ):
            resp = client.post(
                "/api/openclaw-adapter/search/query",
                json={
                    "query": " ",
                    "persona_id": "persona-alpha",
                    "workspace_id": "workspace-research",
                },
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "SEARCH_POLICY_ERROR")


# ---------------------------------------------------------------------------
# Paper broker adapter HTTP routes
# ---------------------------------------------------------------------------


class TestPaperBrokerRoutes(unittest.TestCase):
    """HTTP route coverage for the paper broker adapter endpoints in main.py."""

    def _disabled_broker(self):
        from paper_broker_adapter import PaperBrokerAdapter, PaperBrokerAdapterError

        mock = MagicMock(spec=PaperBrokerAdapter)
        mock.submit_paper_order.side_effect = PaperBrokerAdapterError(
            "PAPER_ADAPTER_DISABLED",
            "Paper broker adapter is disabled.",
            status_code=503,
        )
        mock.list_paper_orders.side_effect = PaperBrokerAdapterError(
            "PAPER_ADAPTER_DISABLED",
            "Paper broker adapter is disabled.",
            status_code=503,
        )
        mock.get_paper_order.side_effect = PaperBrokerAdapterError(
            "PAPER_ADAPTER_DISABLED",
            "Paper broker adapter is disabled.",
            status_code=503,
        )
        mock.reject_live_order.side_effect = PaperBrokerAdapterError(
            "LIVE_ADAPTER_DISABLED",
            "Live broker execution is disabled.",
            status_code=403,
        )
        mock.read_audit.return_value = []
        mock.capability_snapshot.return_value = {
            "paper_adapter_enabled": False,
            "live_adapter_enabled": False,
            "broker_sidecar_configured": False,
            "deployment_stage": "none",
            "is_real_capital": False,
            "is_real_order": False,
        }
        return mock

    def test_submit_paper_order_returns_503_when_gate_closed(self):
        with patch.object(adapter_main, "_PAPER_BROKER", self._disabled_broker()):
            resp = client.post(
                "/api/openclaw-adapter/broker/paper/orders",
                json={
                    "capital_pool_id": "pool-1",
                    "strategy_id": "strat-1",
                    "symbol": "AAPL",
                    "qty": 10.0,
                    "side": "buy",
                },
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["error_code"], "PAPER_ADAPTER_DISABLED")

    def test_submit_paper_order_requires_operator_id(self):
        resp = client.post(
            "/api/openclaw-adapter/broker/paper/orders",
            json={
                "capital_pool_id": "pool-1",
                "strategy_id": "strat-1",
                "symbol": "AAPL",
                "qty": 10.0,
                "side": "buy",
            },
        )
        self.assertEqual(resp.status_code, 401)

    def test_submit_paper_order_succeeds_when_gate_open(self):
        from paper_broker_adapter import PaperBrokerAdapter

        mock = MagicMock(spec=PaperBrokerAdapter)
        mock.submit_paper_order.return_value = {
            "status": "ok",
            "order": {
                "order_id": "ord-abc",
                "fill_price": 100.0,
                "fill_qty": 10.0,
                "status": "filled",
                "is_real_order": False,
                "is_real_capital": False,
                "sim_fill_flag": True,
                "deployment_stage": "paper",
            },
        }
        with patch.object(adapter_main, "_PAPER_BROKER", mock):
            resp = client.post(
                "/api/openclaw-adapter/broker/paper/orders",
                json={
                    "capital_pool_id": "pool-1",
                    "strategy_id": "strat-1",
                    "symbol": "AAPL",
                    "qty": 10.0,
                    "side": "buy",
                },
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["order"]["order_id"], "ord-abc")
        self.assertFalse(body["order"]["is_real_order"])

    def test_cancel_paper_order_forwards_operator_and_trace(self):
        from paper_broker_adapter import PaperBrokerAdapter

        mock = MagicMock(spec=PaperBrokerAdapter)
        mock.cancel_paper_order.return_value = {
            "status": "ok",
            "order": {
                "order_id": "ord-abc",
                "status": "canceled",
                "is_real_order": False,
                "is_real_capital": False,
            },
        }
        with patch.object(adapter_main, "_PAPER_BROKER", mock):
            resp = client.post(
                "/api/openclaw-adapter/broker/paper/orders/ord-abc/cancel",
                headers={"X-Operator-Id": "op-1", "X-Trace-Id": "trace-cancel-1"},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["order"]["status"], "canceled")
        self.assertFalse(body["order"]["is_real_order"])
        mock.cancel_paper_order.assert_called_once_with(
            "ord-abc",
            operator_id="op-1",
            trace_id="trace-cancel-1",
        )

    def test_live_order_always_rejected(self):
        from live_gate_adapter import LiveGateAdapter, LiveGateError

        mock_gate = MagicMock(spec=LiveGateAdapter)
        mock_gate.reject_live_order.side_effect = LiveGateError(
            "LIVE_EXECUTION_DISABLED",
            "Live broker execution is permanently disabled.",
            status_code=403,
            gate="live_execution",
        )
        with patch.object(adapter_main, "_LIVE_GATE", mock_gate):
            resp = client.post(
                "/api/openclaw-adapter/broker/live/orders",
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error_code"], "LIVE_EXECUTION_DISABLED")

    def test_live_order_rejected_when_paper_gate_open(self):
        from live_gate_adapter import LiveGateAdapter, LiveGateError

        mock_gate = MagicMock(spec=LiveGateAdapter)
        mock_gate.reject_live_order.side_effect = LiveGateError(
            "LIVE_EXECUTION_DISABLED",
            "Live broker execution is permanently disabled.",
            status_code=403,
            gate="live_execution",
        )
        with patch.object(adapter_main, "_LIVE_GATE", mock_gate):
            resp = client.post(
                "/api/openclaw-adapter/broker/live/orders",
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error_code"], "LIVE_EXECUTION_DISABLED")

    def test_canary_order_always_rejected(self):
        resp = client.post(
            "/api/openclaw-adapter/broker/canary/orders",
            headers={"X-Operator-Id": "op-1"},
        )
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["error_code"], "CANARY_EXECUTION_DISABLED")
        self.assertEqual(body["gate"], "canary_execution")
        self.assertFalse(body["details"]["is_real_order"])
        self.assertFalse(body["details"]["is_real_capital"])
        self.assertEqual(body["details"]["configured_gate"], "OPENCLAW_CANARY_ADAPTER_ENABLED")

    def test_broker_capabilities_endpoint(self):
        from paper_broker_adapter import PaperBrokerAdapter

        mock = MagicMock(spec=PaperBrokerAdapter)
        mock.capability_snapshot.return_value = {
            "sandbox_adapter_state": "activation_ready",
            "sandbox_gate": "OPENCLAW_PAPER_ADAPTER_ENABLED",
            "paper_adapter_enabled": False,
            "live_adapter_enabled": False,
            "broker_sidecar_configured": False,
            "deployment_stage": "none",
            "is_real_capital": False,
            "is_real_order": False,
        }
        with patch.object(adapter_main, "_PAPER_BROKER", mock):
            resp = client.get("/api/openclaw-adapter/broker/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["live_adapter_enabled"])
        self.assertFalse(body["canary_adapter_enabled"])
        self.assertFalse(body["canary_execution_enabled"])
        self.assertEqual(body["canary_gate"], "OPENCLAW_CANARY_ADAPTER_ENABLED")
        self.assertFalse(body["is_real_order"])
        self.assertFalse(body["is_real_capital"])

    def test_broker_audit_endpoint_returns_list(self):
        from paper_broker_adapter import PaperBrokerAdapter

        mock = MagicMock(spec=PaperBrokerAdapter)
        mock.read_audit.return_value = [
            {"event": "paper_order_intent", "operator_id": "op-1"},
        ]
        with patch.object(adapter_main, "_PAPER_BROKER", mock):
            resp = client.get("/api/openclaw-adapter/broker/audit")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["entries"][0]["event"], "paper_order_intent")


if __name__ == "__main__":
    unittest.main()
