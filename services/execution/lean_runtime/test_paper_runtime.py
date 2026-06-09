import json
import os
import threading
import urllib.error
import urllib.request
import uuid
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from services.execution.lean_runtime.paper_runtime import _Handler, PaperRuntimeService, RuntimeTelemetryEmitter
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity


class _FakeRuntimeManagerClient:
    def __init__(self, bindings):
        self._bindings = bindings

    def list_all(self):
        return list(self._bindings)


class _FakeBindingResolver:
    def __init__(self, binding):
        self._binding = binding

    def resolve(self):
        return dict(self._binding)


class _FakeTelemetryEmitter:
    def __init__(self):
        self.enabled = True
        self.events = []

    def emit(self, event_type, metrics, metadata=None):
        self.events.append(
            {
                "event_type": event_type,
                "metrics": metrics,
                "metadata": metadata or {},
            }
        )
        return True

    def emit_deploy_started(self):
        return self.emit("deploy_started", {"action": "deploy_started"})

    def emit_deploy_completed(self):
        return self.emit("deploy_completed", {"action": "deploy_completed"})

    def emit_heartbeat(self, metadata=None):
        return self.emit("heartbeat", {"heartbeat": 1}, metadata=metadata)

    def emit_pnl_snapshot(self, pnl, metadata=None):
        return self.emit("pnl_snapshot", {"pnl": float(pnl)}, metadata=metadata)

    def snapshot(self):
        return {
            "enabled": True,
            "url": "memory://telemetry",
            "sent": len(self.events),
            "failed": 0,
            "last_error": None,
        }


class _FakeHealthService:
    def __init__(self, status="ok"):
        self._status = status

    def snapshot(self):
        return {
            "status": self._status,
            "runtime_package": "paper_execution_runtime",
            "paper_execution_ready": self._status == "ok",
        }


class PaperRuntimeServiceTest(unittest.TestCase):
    def _identity(self) -> RuntimeIdentity:
        return RuntimeIdentity.from_env(
            {
                "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
                "PANTHEON_RUNTIME_MODE": "paper",
                "PANTHEON_RUNTIME_ID": "paper-runtime-001",
                "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
                "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
                "PANTHEON_WORKSPACE_REF": "workspace-paper",
                "PANTHEON_AUTH_PROFILE_REF": "auth-profile-paper",
                "PANTHEON_PERSONA_ID": "persona-paper-ops",
                "PANTHEON_SESSION_ID": "session-paper-runtime",
                "PANTHEON_TRACE_ID": str(uuid.uuid4()),
                "PANTHEON_REQUEST_ID": "request-paper-runtime",
            }
        )

    def _binding(self):
        return {
            "binding_id": str(uuid.uuid4()),
            "runtime_id": "paper-runtime-001",
            "capital_pool_id": "pool-paper",
            "artifact_id": "artifact-paper",
            "artifact_version": "1.2.3",
            "deployment_mode": "paper",
            "plan_id": "plan-paper",
            "persona_capital_binding_id": "pcb-paper",
            "status": "active",
        }

    def _signal(self):
        return {
            "signal_id": "signal-001",
            "version": "1.0",
            "strategy_id": "strategy-paper",
            "timestamp": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "symbol": "AAPL.US",
            "action": "BUY",
            "direction": "LONG",
            "quantity": 10,
            "quantity_type": "SHARES",
        }

    def test_http_handler_exposes_standard_health_probes(self):
        with patch(
            "services.execution.lean_runtime.paper_runtime.get_service",
            return_value=_FakeHealthService(),
        ):
            server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                for path in ("/healthz", "/livez", "/readyz", "/health", "/__health__"):
                    with urllib.request.urlopen(f"{base_url}{path}", timeout=2) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertTrue(payload["live"])
                    self.assertTrue(payload["ready"])
                    self.assertEqual(payload["health_contract"]["readyz"], "/readyz")
                    self.assertIn("/__health__", payload["health_contract"]["legacy"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_http_handler_readiness_fails_when_runtime_snapshot_is_not_ok(self):
        with patch(
            "services.execution.lean_runtime.paper_runtime.get_service",
            return_value=_FakeHealthService(status="error"),
        ):
            server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(f"{base_url}/readyz", timeout=2)

                self.assertEqual(raised.exception.code, 503)
                payload = json.loads(raised.exception.read().decode("utf-8"))
                self.assertFalse(payload["ready"])

                with urllib.request.urlopen(f"{base_url}/livez", timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertTrue(payload["live"])
                self.assertFalse(payload["ready"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_drain_once_executes_signal_and_updates_runtime_state(self):
        store = InMemoryPendingSignalStore([self._signal()])
        telemetry = _FakeTelemetryEmitter()
        service = PaperRuntimeService(
            store=store,
            identity=self._identity(),
            runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
            telemetry_emitter=telemetry,
            poll_interval_seconds=3600,
            max_batch_size=10,
        )

        snapshot = service.drain_once()

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["runtime_package"], "paper_execution_runtime")
        self.assertFalse(snapshot["stub_mode"])
        self.assertTrue(snapshot["binding_lookup"]["resolved"])
        self.assertEqual(snapshot["signal_store"]["queue_depth"], 0)
        self.assertEqual(snapshot["paper_state"]["processed_signal_count"], 1)
        self.assertEqual(snapshot["paper_state"]["execution_event_count"], 1)
        self.assertEqual(snapshot["paper_state"]["positions"][0]["symbol"], "AAPL")
        self.assertEqual(len(telemetry.events), 3)
        self.assertEqual(telemetry.events[0]["event_type"], "paper_fill_simulated")
        self.assertEqual(telemetry.events[1]["event_type"], "heartbeat")
        self.assertEqual(telemetry.events[2]["event_type"], "pnl_snapshot")

    def test_snapshot_without_drain_reports_truthful_ready_state(self):
        service = PaperRuntimeService(
            store=InMemoryPendingSignalStore(),
            identity=self._identity(),
            runtime_manager_client=_FakeRuntimeManagerClient([]),
            telemetry_emitter=_FakeTelemetryEmitter(),
            poll_interval_seconds=3600,
        )

        snapshot = service.snapshot()

        self.assertTrue(snapshot["paper_execution_ready"])
        self.assertTrue(snapshot["signal_consumer_ready"])
        self.assertEqual(snapshot["binding_lookup"]["resolved"], False)
        self.assertEqual(snapshot["paper_state"]["processed_signal_count"], 0)
        self.assertEqual(snapshot["paper_state"]["execution_event_count"], 0)

    def test_drain_once_requires_runtime_binding_before_execution(self):
        store = InMemoryPendingSignalStore([self._signal()])
        telemetry = _FakeTelemetryEmitter()
        service = PaperRuntimeService(
            store=store,
            identity=self._identity(),
            runtime_manager_client=_FakeRuntimeManagerClient([]),
            telemetry_emitter=telemetry,
            poll_interval_seconds=3600,
        )

        snapshot = service.drain_once()

        self.assertEqual(snapshot["status"], "degraded")
        self.assertIn("RuntimeBinding is required", snapshot["paper_state"]["last_error"])
        self.assertEqual(snapshot["paper_state"]["processed_signal_count"], 0)
        self.assertEqual(snapshot["paper_state"]["execution_event_count"], 0)
        self.assertEqual(snapshot["paper_state"]["positions"], [])
        self.assertEqual(telemetry.events, [])

    def test_runtime_state_pool_scope_mismatch_is_rejected(self):
        service = PaperRuntimeService(
            store=InMemoryPendingSignalStore(),
            identity=self._identity(),
            runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
            telemetry_emitter=_FakeTelemetryEmitter(),
            poll_interval_seconds=3600,
        )

        violation = service.pool_access_violation("pool-other")

        self.assertIsNotNone(violation)
        assert violation is not None
        self.assertEqual(violation["status"], "blocked")
        self.assertEqual(violation["error"], "capital_pool_scope_mismatch")
        self.assertEqual(violation["runtime_capital_pool_id"], "pool-paper")
        self.assertIsNone(service.pool_access_violation("pool-paper"))

    def test_http_runtime_state_pool_scope_mismatch_returns_403(self):
        service = PaperRuntimeService(
            store=InMemoryPendingSignalStore(),
            identity=self._identity(),
            runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
            telemetry_emitter=_FakeTelemetryEmitter(),
            poll_interval_seconds=3600,
        )
        with patch(
            "services.execution.lean_runtime.paper_runtime.get_service",
            return_value=service,
        ):
            server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(
                        f"{base_url}/api/runtime/state?capital_pool_id=pool-other",
                        timeout=2,
                    )

                self.assertEqual(raised.exception.code, 403)
                payload = json.loads(raised.exception.read().decode("utf-8"))
                self.assertEqual(payload["error"], "capital_pool_scope_mismatch")
                self.assertEqual(payload["runtime_capital_pool_id"], "pool-paper")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_http_runtime_adapter_does_not_expose_kill_switch_dispatch(self):
        service = PaperRuntimeService(
            store=InMemoryPendingSignalStore(),
            identity=self._identity(),
            runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
            telemetry_emitter=_FakeTelemetryEmitter(),
            poll_interval_seconds=3600,
        )
        with patch(
            "services.execution.lean_runtime.paper_runtime.get_service",
            return_value=service,
        ):
            server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                request = urllib.request.Request(
                    f"{base_url}/api/kill-switch/dispatch",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=2)

                self.assertEqual(raised.exception.code, 404)
                payload = json.loads(raised.exception.read().decode("utf-8"))
                self.assertEqual(payload["status"], "not_found")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_guarded_paper_bracket_order_event_is_submitted_to_paper_broker(self):
        signal = self._signal()
        signal["metadata"] = {
            "risk_parameters": {
                "stop_loss_pct": 0.02,
                "take_profit_pct": 0.05,
            }
        }
        store = InMemoryPendingSignalStore([signal])
        telemetry = _FakeTelemetryEmitter()
        service = PaperRuntimeService(
            store=store,
            identity=self._identity(),
            runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
            telemetry_emitter=telemetry,
            poll_interval_seconds=3600,
            max_batch_size=10,
        )

        snapshot = service.drain_once()

        events = snapshot["paper_state"]["recent_order_events"]
        bracket_events = [event for event in events if event["event_type"] == "bracket_order_logged"]
        self.assertEqual(len(bracket_events), 1)
        self.assertEqual(bracket_events[0]["broker_submission_status"], "submitted_to_broker")
        self.assertTrue(bracket_events[0]["submitted_to_broker"])
        self.assertEqual(bracket_events[0]["metadata"]["signal_id"], "signal-001")
        self.assertEqual(bracket_events[0]["metadata"]["guard_stage"], "paper")
        self.assertTrue(snapshot["paper_state"]["bracket_order_execution_enabled"])
        self.assertEqual(snapshot["paper_state"]["bracket_order_execution_stage"], "paper")
        self.assertEqual(len(snapshot["paper_state"]["open_bracket_orders"]), 2)

        telemetry_bracket_events = [
            event for event in telemetry.events if event["event_type"] == "bracket_order_logged"
        ]
        self.assertEqual(len(telemetry_bracket_events), 1)
        self.assertEqual(
            telemetry_bracket_events[0]["metadata"]["broker_submission_status"],
            "submitted_to_broker",
        )
        self.assertEqual(
            telemetry_bracket_events[0]["metrics"]["action"],
            "bracket_submitted_to_broker",
        )
        self.assertTrue(telemetry_bracket_events[0]["metadata"]["submitted_to_broker"])
        self.assertTrue(telemetry_bracket_events[0]["metrics"]["submitted_to_broker"])

    def test_bracket_order_guard_disabled_remains_logged_only(self):
        signal = self._signal()
        signal["metadata"] = {
            "risk_parameters": {
                "stop_loss_pct": 0.02,
                "take_profit_pct": 0.05,
            }
        }
        store = InMemoryPendingSignalStore([signal])
        telemetry = _FakeTelemetryEmitter()
        with patch.dict(os.environ, {"PANTHEON_BRACKET_ORDER_EXECUTION_ENABLED": "false"}):
            service = PaperRuntimeService(
                store=store,
                identity=self._identity(),
                runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
                telemetry_emitter=telemetry,
                poll_interval_seconds=3600,
                max_batch_size=10,
            )

        snapshot = service.drain_once()

        events = snapshot["paper_state"]["recent_order_events"]
        bracket_events = [event for event in events if event["event_type"] == "bracket_order_logged"]
        self.assertEqual(len(bracket_events), 1)
        self.assertEqual(bracket_events[0]["broker_submission_status"], "logged_only")
        self.assertFalse(bracket_events[0]["submitted_to_broker"])
        self.assertFalse(snapshot["paper_state"]["bracket_order_execution_enabled"])
        self.assertEqual(snapshot["paper_state"]["open_bracket_orders"], [])

        telemetry_bracket_events = [
            event for event in telemetry.events if event["event_type"] == "bracket_order_logged"
        ]
        self.assertEqual(len(telemetry_bracket_events), 1)
        self.assertEqual(telemetry_bracket_events[0]["metrics"]["action"], "bracket_logged_only")
        self.assertFalse(telemetry_bracket_events[0]["metadata"]["submitted_to_broker"])

    def test_runtime_telemetry_emitter_builds_canonical_paper_heartbeat(self):
        binding = self._binding()
        binding.update(
            {
                "engine_bridge_repo": "ajoe734/pantheon-lean.git",
                "engine_bridge_path": "pantheon/lean",
                "engine_bridge_commit": "abc1234",
                "runtime_adapter_version": "0.1.0",
                "context_source": "launch_manifest",
            }
        )
        emitter = RuntimeTelemetryEmitter(self._identity(), _FakeBindingResolver(binding))

        event = emitter.build_event(
            "heartbeat",
            {"heartbeat": 1},
            metadata={"runtime_package": "paper_execution_runtime"},
            event_id=str(uuid.uuid4()),
            created_at="2026-05-01T00:00:00Z",
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "heartbeat")
        self.assertEqual(event["execution_mode"], "paper")
        self.assertEqual(event["deployment_stage"], "paper")
        self.assertEqual(event["binding_id"], binding["binding_id"])
        self.assertEqual(event["plan_id"], "plan-paper")
        self.assertEqual(event["target"]["artifact_type"], "execution_bundle")
        self.assertEqual(event["metadata"]["engine_bridge_repo"], "ajoe734/pantheon-lean.git")
        self.assertEqual(event["metadata"]["engine_bridge_commit"], "abc1234")
        self.assertEqual(event["metadata"]["context_source"], "launch_manifest")

    def test_runtime_telemetry_emitter_rejects_non_paper_stage(self):
        binding = self._binding()
        binding["deployment_mode"] = "live"
        emitter = RuntimeTelemetryEmitter(self._identity(), _FakeBindingResolver(binding))

        event = emitter.build_event("heartbeat", {"heartbeat": 1})

        self.assertIsNone(event)
        self.assertEqual(emitter.snapshot()["failed"], 1)
        self.assertIn("deployment_stage='paper'", emitter.snapshot()["last_error"])

    def test_bracket_order_event_carries_verifiable_entry_price_and_leg_prices(self):
        """bracket_order_logged metadata must carry entry_price and deterministic leg prices for audit."""
        signal = self._signal()
        signal["metadata"] = {
            "risk_parameters": {
                "stop_loss_pct": 0.02,
                "take_profit_pct": 0.05,
            }
        }
        store = InMemoryPendingSignalStore([signal])
        telemetry = _FakeTelemetryEmitter()
        service = PaperRuntimeService(
            store=store,
            identity=self._identity(),
            runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
            telemetry_emitter=telemetry,
            poll_interval_seconds=3600,
            max_batch_size=10,
        )

        snapshot = service.drain_once()

        events = snapshot["paper_state"]["recent_order_events"]
        bracket_events = [e for e in events if e["event_type"] == "bracket_order_logged"]
        self.assertEqual(len(bracket_events), 1)
        meta = bracket_events[0]["metadata"]

        self.assertIn("entry_price", meta)
        self.assertGreater(meta["entry_price"], 0)

        self.assertIn("legs", meta)
        self.assertEqual(len(meta["legs"]), 2)
        leg_types = {leg["leg_type"] for leg in meta["legs"]}
        self.assertEqual(leg_types, {"stop_loss", "take_profit"})

        # PaperExecutionAlgorithm default_price=100; stop 2% → 98.0, tp 5% → 105.0
        stop_leg = next(l for l in meta["legs"] if l["leg_type"] == "stop_loss")
        tp_leg = next(l for l in meta["legs"] if l["leg_type"] == "take_profit")
        self.assertAlmostEqual(stop_leg["stop_price"], 98.0, places=4)
        self.assertAlmostEqual(tp_leg["limit_price"], 105.0, places=4)


if __name__ == "__main__":
    unittest.main()
