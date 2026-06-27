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

from services.execution.lean_runtime.paper_runtime import _Handler, PaperRuntimeService, RuntimeTelemetryEmitter, PaperExecutionAlgorithm, OrderEvent
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

    def emit_pnl_snapshot(self, pnl, metadata=None, extra_metrics=None):
        metrics = {"pnl": float(pnl)}
        metrics.update(extra_metrics or {})
        return self.emit("pnl_snapshot", metrics, metadata=metadata)

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

    def test_drain_once_does_not_execute_when_binding_halted(self):
        """Safety gate: a paused/halted binding must not fill orders.

        Regression for the E2E-R4 finding that a paused binding kept executing
        signals (kill-switch / operator pause not enforced at the paper loop).
        """
        for halt_status in ("paused", "pending_pause", "failed", "retired"):
            store = InMemoryPendingSignalStore([self._signal()])
            telemetry = _FakeTelemetryEmitter()
            binding = self._binding()
            binding["status"] = halt_status
            service = PaperRuntimeService(
                store=store,
                identity=self._identity(),
                runtime_manager_client=_FakeRuntimeManagerClient([binding]),
                telemetry_emitter=telemetry,
                poll_interval_seconds=3600,
                max_batch_size=10,
            )

            snapshot = service.drain_once()

            self.assertEqual(
                snapshot["paper_state"]["processed_signal_count"], 0,
                f"{halt_status}: must not execute",
            )
            self.assertEqual(snapshot["paper_state"]["execution_event_count"], 0)
            self.assertEqual(snapshot["paper_state"]["last_skipped_status"], halt_status)
            # signal is held on the queue so it replays once the binding resumes
            self.assertEqual(snapshot["signal_store"]["queue_depth"], 1)

    def test_drain_once_resumes_execution_when_binding_active(self):
        """Sanity: once the halt clears, an active binding executes normally."""
        store = InMemoryPendingSignalStore([self._signal()])
        service = PaperRuntimeService(
            store=store,
            identity=self._identity(),
            runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
            telemetry_emitter=_FakeTelemetryEmitter(),
            poll_interval_seconds=3600,
            max_batch_size=10,
        )

        snapshot = service.drain_once()

        self.assertEqual(snapshot["paper_state"]["processed_signal_count"], 1)
        self.assertIsNone(snapshot["paper_state"]["last_skipped_status"])

    def test_drain_once_dedups_duplicate_signal_id_across_polls(self):
        """E2E-R6: the same signal_id must not double-fill across two poll cycles."""
        sig = self._signal()
        store = InMemoryPendingSignalStore([dict(sig)])
        telemetry = _FakeTelemetryEmitter()
        service = PaperRuntimeService(
            store=store,
            identity=self._identity(),
            runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
            telemetry_emitter=telemetry,
            poll_interval_seconds=3600,
            max_batch_size=10,
        )

        first = service.drain_once()
        self.assertEqual(first["paper_state"]["processed_signal_count"], 1)

        # second poll: same signal_id re-enqueued must be discarded as duplicate
        store.enqueue(dict(sig))
        second = service.drain_once()
        self.assertEqual(
            second["paper_state"]["processed_signal_count"], 1,
            "duplicate signal_id must not execute a second time",
        )

    def test_cash_value_llm_alpha_for_new_symbol_executes_with_fill_provenance(self):
        signal = self._signal()
        signal.update(
            {
                "signal_id": "llm-alpha-nvda-cash-001",
                "strategy_id": "strategy-llm-alpha",
                "symbol": "NVDA.US",
                "quantity": 500,
                "quantity_type": "CASH_VALUE",
                "source_worker": "mock-llm-alpha-normalizer",
                "metadata": {
                    "alpha_source": "llm_research_agent",
                    "confidence_score": 0.82,
                    "model_id": "gpt-research-paper",
                },
            }
        )
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

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["paper_state"]["processed_signal_count"], 1)
        self.assertEqual(snapshot["paper_state"]["execution_event_count"], 1)
        self.assertEqual(snapshot["paper_state"]["positions"][0]["symbol"], "NVDA")
        self.assertEqual(snapshot["paper_state"]["positions"][0]["quantity"], 5.0)
        fill_event = snapshot["paper_state"]["recent_order_events"][0]
        self.assertEqual(fill_event["fill_price"], 100.0)
        self.assertEqual(fill_event["metadata"]["signal_id"], "llm-alpha-nvda-cash-001")
        self.assertEqual(fill_event["metadata"]["strategy_id"], "strategy-llm-alpha")
        self.assertEqual(fill_event["metadata"]["source_worker"], "mock-llm-alpha-normalizer")
        self.assertEqual(fill_event["metadata"]["alpha_source"], "llm_research_agent")
        self.assertEqual(fill_event["metadata"]["confidence_score"], 0.82)
        self.assertEqual(telemetry.events[0]["event_type"], "paper_fill_simulated")
        self.assertFalse(telemetry.events[0]["metadata"]["is_real_order"])
        self.assertEqual(telemetry.events[0]["metadata"]["alpha_source"], "llm_research_agent")

    def test_hold_llm_signal_records_paper_order_noop_without_fill(self):
        signal = self._signal()
        signal.update(
            {
                "signal_id": "llm-hold-msft-riskoff-001",
                "strategy_id": "strategy-llm-riskoff",
                "symbol": "MSFT.US",
                "action": "HOLD",
                "direction": "LONG",
                "quantity": 0,
                "quantity_type": "SHARES",
                "source_worker": "mock-llm-risk-normalizer",
                "metadata": {
                    "alpha_source": "llm_riskoff_agent",
                    "confidence_score": 0.91,
                    "model_id": "gpt-risk-paper",
                    "market_data": {"close": 420.0},
                },
            }
        )
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

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["paper_state"]["processed_signal_count"], 1)
        self.assertEqual(snapshot["paper_state"]["execution_event_count"], 1)
        self.assertEqual(snapshot["paper_state"]["positions"], [])
        event = snapshot["paper_state"]["recent_order_events"][0]
        self.assertEqual(event["event_type"], "paper_order_simulated")
        self.assertEqual(event["action"], "hold_signal_noop")
        self.assertEqual(event["quantity"], 0.0)
        self.assertEqual(event["metadata"]["signal_id"], "llm-hold-msft-riskoff-001")
        self.assertEqual(event["metadata"]["noop_reason"], "hold_signal")
        self.assertEqual(event["metadata"]["decision_status"], "no_order")
        self.assertEqual(event["metadata"]["order_status"], "not_submitted")
        self.assertEqual(event["metadata"]["price"], 420.0)
        self.assertFalse(event["submitted_to_broker"])

        noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
        fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
        pnl_events = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"]
        self.assertEqual(len(noop_events), 1)
        self.assertEqual(fill_events, [])
        self.assertEqual(noop_events[0]["metrics"]["noop_count"], 1)
        self.assertEqual(noop_events[0]["metrics"]["fill_rate"], 0.0)
        self.assertEqual(noop_events[0]["metadata"]["alpha_source"], "llm_riskoff_agent")
        self.assertEqual(pnl_events[-1]["metrics"]["fill_event_count"], 0)
        self.assertEqual(pnl_events[-1]["metrics"]["fill_rate"], 0.0)

    def test_exit_without_position_records_paper_order_noop_without_fill(self):
        signal = self._signal()
        signal.update(
            {
                "signal_id": "quant-exit-adbe-empty-001",
                "strategy_id": "strategy-quant-exit-empty",
                "symbol": "ADBE.US",
                "action": "EXIT",
                "direction": "LONG",
                "quantity": 0,
                "quantity_type": "SHARES",
                "source_worker": "mock-quant-exit-normalizer",
                "metadata": {
                    "alpha_source": "quant_drawdown_exit",
                    "confidence_score": 0.88,
                    "market_data": {"close": 600.0},
                },
            }
        )
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

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["paper_state"]["processed_signal_count"], 1)
        self.assertEqual(snapshot["paper_state"]["execution_event_count"], 1)
        self.assertEqual(snapshot["paper_state"]["positions"], [])
        event = snapshot["paper_state"]["recent_order_events"][0]
        self.assertEqual(event["event_type"], "paper_order_simulated")
        self.assertEqual(event["metadata"]["noop_reason"], "exit_long_without_position")
        self.assertEqual(event["metadata"]["computed_quantity"], 0.0)
        self.assertEqual(event["metadata"]["position_quantity"], 0.0)
        self.assertEqual(event["metadata"]["exit_direction"], "LONG")
        self.assertEqual(event["metadata"]["price"], 600.0)

        noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
        fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
        pnl_events = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"]
        self.assertEqual(len(noop_events), 1)
        self.assertEqual(fill_events, [])
        self.assertEqual(noop_events[0]["metrics"]["computed_quantity"], 0.0)
        self.assertEqual(noop_events[0]["metadata"]["alpha_source"], "quant_drawdown_exit")
        self.assertEqual(pnl_events[-1]["metrics"]["fill_event_count"], 0)
        self.assertEqual(pnl_events[-1]["metrics"]["open_position_count"], 0)

    def test_sell_long_without_position_liquidate_records_noop_without_fill(self):
        signal = self._signal()
        signal.update(
            {
                "signal_id": "quant-sell-long-empty-001",
                "strategy_id": "strategy-quant-sell-empty",
                "symbol": "CRM.US",
                "action": "SELL",
                "direction": "LONG",
                "quantity": 0,
                "quantity_type": "SHARES",
                "source_worker": "mock-quant-close-normalizer",
                "metadata": {
                    "alpha_source": "quant_stop_exit",
                    "confidence_score": 0.86,
                    "market_data": {"close": 240.0},
                },
            }
        )
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

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["paper_state"]["processed_signal_count"], 1)
        self.assertEqual(snapshot["paper_state"]["execution_event_count"], 1)
        self.assertEqual(snapshot["paper_state"]["positions"], [])
        event = snapshot["paper_state"]["recent_order_events"][0]
        self.assertEqual(event["event_type"], "paper_order_simulated")
        self.assertEqual(event["action"], "liquidate_without_position_noop")
        self.assertEqual(event["metadata"]["noop_reason"], "liquidate_without_position")
        self.assertEqual(event["metadata"]["requested_quantity"], 0.0)
        self.assertEqual(event["metadata"]["computed_quantity"], 0.0)
        self.assertEqual(event["metadata"]["position_quantity"], 0.0)
        self.assertEqual(event["metadata"]["quantity_type"], "SHARES")
        self.assertEqual(event["metadata"]["price"], 240.0)

        noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
        fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
        pnl_events = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"]
        self.assertEqual(len(noop_events), 1)
        self.assertEqual(fill_events, [])
        self.assertEqual(noop_events[0]["metrics"]["requested_quantity"], 0.0)
        self.assertEqual(noop_events[0]["metrics"]["computed_quantity"], 0.0)
        self.assertEqual(pnl_events[-1]["metrics"]["fill_event_count"], 0)
        self.assertEqual(pnl_events[-1]["metrics"]["open_position_count"], 0)

    def test_set_holdings_no_delta_records_noop_without_fill(self):
        signal = self._signal()
        signal.update(
            {
                "signal_id": "quant-percent-close-empty-001",
                "strategy_id": "strategy-quant-percent-empty",
                "symbol": "NFLX.US",
                "action": "SELL",
                "direction": "LONG",
                "quantity": 0.50,
                "quantity_type": "PERCENT_PORTFOLIO",
                "source_worker": "mock-quant-percent-close-normalizer",
                "metadata": {
                    "alpha_source": "quant_percent_close",
                    "confidence_score": 0.92,
                    "market_data": {"close": 500.0},
                },
            }
        )
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

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["paper_state"]["processed_signal_count"], 1)
        self.assertEqual(snapshot["paper_state"]["execution_event_count"], 1)
        self.assertEqual(snapshot["paper_state"]["positions"], [])
        event = snapshot["paper_state"]["recent_order_events"][0]
        self.assertEqual(event["event_type"], "paper_order_simulated")
        self.assertEqual(event["action"], "set_holdings_no_delta_noop")
        self.assertEqual(event["metadata"]["noop_reason"], "set_holdings_no_delta")
        self.assertEqual(event["metadata"]["requested_quantity"], 0.5)
        self.assertEqual(event["metadata"]["computed_quantity"], 0.0)
        self.assertEqual(event["metadata"]["position_quantity"], 0.0)
        self.assertEqual(event["metadata"]["target_quantity"], 0.0)
        self.assertEqual(event["metadata"]["target_percent"], 0.0)
        self.assertEqual(event["metadata"]["quantity_type"], "PERCENT_PORTFOLIO")
        self.assertEqual(event["metadata"]["price"], 500.0)

        noop_events = [event for event in telemetry.events if event["event_type"] == "paper_order_simulated"]
        fill_events = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
        pnl_events = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"]
        self.assertEqual(len(noop_events), 1)
        self.assertEqual(fill_events, [])
        self.assertEqual(noop_events[0]["metrics"]["requested_quantity"], 0.5)
        self.assertEqual(noop_events[0]["metrics"]["computed_quantity"], 0.0)
        self.assertEqual(pnl_events[-1]["metrics"]["fill_event_count"], 0)
        self.assertEqual(pnl_events[-1]["metrics"]["open_position_count"], 0)

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


class TestSubmitTaiwanBrokerOrder(unittest.TestCase):
    def _algo(self):
        events = []
        algo = PaperExecutionAlgorithm(event_sink=events.append)
        return algo, events

    def test_taiwan_paper_fill_published_with_shioaji_trade_id(self):
        algo, events = self._algo()
        fake_order = {"order_id": "ord-tw-1", "fill_price": 2340.0, "fill_qty": 1, "side": "sell"}
        with patch.object(PaperExecutionAlgorithm, "_post_broker_paper_order",
                          staticmethod(lambda url, payload: fake_order)):
            algo.SubmitTaiwanBrokerOrder(
                "2330.TW", signal_id="s1", side="sell", quantity=1,
                quantity_type="SHARES", action="SELL", order_type="MARKET",
            )
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.event_type, "paper_fill_simulated")
        self.assertEqual(ev.symbol, "2330.TW")
        self.assertEqual(ev.fill_price, 2340.0)
        self.assertTrue(ev.submitted_to_broker)
        self.assertEqual(ev.broker_submission_status, "filled")
        self.assertEqual(ev.metadata["shioaji_trade_id"], "ord-tw-1")
        self.assertEqual(ev.metadata["broker_order_id"], "ord-tw-1")
        self.assertEqual(ev.metadata["adapter"], "shioaji")
        self.assertEqual(ev.metadata["currency"], "TWD")
        self.assertEqual(ev.metadata["exchange"], "TSE")
        self.assertEqual(algo._holding("2330.TW").Quantity, -1)

    def test_taiwan_broker_error_records_rejection(self):
        algo, events = self._algo()
        def boom(url, payload):
            raise RuntimeError("broker HTTP 403: disabled")
        with patch.object(PaperExecutionAlgorithm, "_post_broker_paper_order",
                          staticmethod(boom)):
            algo.SubmitTaiwanBrokerOrder(
                "2330.TW", signal_id="s2", side="sell", quantity=1,
                quantity_type="SHARES", action="SELL",
            )
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.event_type, "order_rejection")
        self.assertEqual(ev.broker_submission_status, "taiwan_broker_error")
        self.assertIn("broker HTTP 403", ev.metadata["execution_error_message"])

    def test_taiwan_rejects_non_shares_quantity_type(self):
        algo, events = self._algo()
        algo.SubmitTaiwanBrokerOrder(
            "2330.TW", signal_id="s3", side="buy", quantity=0.1,
            quantity_type="PERCENT_PORTFOLIO", action="BUY",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "order_rejection")
        self.assertEqual(events[0].broker_submission_status, "tw_unsupported_quantity_type")


if __name__ == "__main__":

    unittest.main()
