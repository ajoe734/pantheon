import json
import os
from pathlib import Path
import tempfile
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
from services.execution.lean_runtime.performance_telemetry import MarketMark
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

    def build_event(
        self,
        event_type,
        metrics,
        metadata=None,
        *,
        event_id=None,
        created_at=None,
    ):
        event_metrics = dict(metrics)
        stamp_key = "pnl_as_of" if event_type == "pnl_snapshot" else "drawdown_as_of"
        stamp = event_metrics.pop(stamp_key, None)
        return {
            "event_id": event_id,
            "event_type": event_type,
            "created_at": created_at,
            "binding_id": (metadata or {}).get("runtime_binding_id"),
            stamp_key: stamp,
            "metrics": event_metrics,
            "metadata": dict(metadata or {}),
        }

    def emit_payload(self, payload):
        self.events.append(json.loads(json.dumps(dict(payload))))
        return True

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

    def emit_drawdown_snapshot(self, drawdown_pct, metadata=None, extra_metrics=None):
        metrics = {"drawdown_pct": float(drawdown_pct)}
        metrics.update(extra_metrics or {})
        return self.emit("drawdown_snapshot", metrics, metadata=metadata)

    def snapshot(self):
        return {
            "enabled": True,
            "url": "memory://telemetry",
            "sent": len(self.events),
            "failed": 0,
            "last_error": None,
        }


class _DurablePairTelemetry:
    def __init__(self, binding, *, fail_once=()):
        self.enabled = True
        self.binding = dict(binding)
        self.fail_once = set(fail_once)
        self.attempts = []

    def build_event(
        self,
        event_type,
        metrics,
        metadata=None,
        *,
        event_id=None,
        created_at=None,
    ):
        event_metrics = dict(metrics)
        stamp_key = "pnl_as_of" if event_type == "pnl_snapshot" else "drawdown_as_of"
        stamp = event_metrics.pop(stamp_key)
        return {
            "event_id": event_id,
            "event_type": event_type,
            "created_at": created_at,
            "binding_id": self.binding["binding_id"],
            stamp_key: stamp,
            "metrics": event_metrics,
            "metadata": dict(metadata or {}),
        }

    def emit_payload(self, payload):
        captured = json.loads(json.dumps(dict(payload)))
        self.attempts.append(captured)
        event_type = captured["event_type"]
        if event_type in self.fail_once:
            self.fail_once.remove(event_type)
            return False
        return True

    def emit(self, event_type, metrics, metadata=None):
        return True

    def emit_heartbeat(self, metadata=None):
        return True

    def snapshot(self):
        return {
            "enabled": True,
            "url": "memory://durable-pair",
            "sent": len(self.attempts),
            "failed": 0,
            "last_error": None,
        }


class _FakeMarkProvider:
    def __init__(self, *, price=105.0, as_of=None):
        self.price = float(price)
        self.as_of = as_of

    def resolve(self, symbols):
        if self.as_of is None:
            self.as_of = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
                "+00:00", "Z"
            )
        marks = {
            symbol: MarketMark(
                symbol=symbol,
                price=self.price,
                as_of=self.as_of,
                source_ref=f"source-ingest://{symbol}",
            )
            for symbol in symbols
        }
        return marks, {
            "source": "source_ingest",
            "enabled": True,
            "requested_symbols": list(symbols),
            "resolved_symbols": sorted(marks),
            "missing_symbols": [],
        }

    def snapshot(self, *, requested_symbols=None):
        return {
            "source": "source_ingest",
            "enabled": True,
            "requested_symbols": list(requested_symbols or []),
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
        self.assertEqual(len(telemetry.events), 2)
        self.assertEqual(telemetry.events[0]["event_type"], "paper_fill_simulated")
        self.assertEqual(telemetry.events[1]["event_type"], "heartbeat")
        self.assertEqual(
            snapshot["paper_state"]["performance_telemetry"]["code"],
            "missing_market_marks",
        )

    def test_corrupt_ledger_blocks_execution_but_emits_structured_heartbeat(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_path = Path(temporary_dir) / "paper-ledger.json"
            state_path.write_text("[]", encoding="utf-8")
            store = InMemoryPendingSignalStore([self._signal()])
            telemetry = _FakeTelemetryEmitter()
            with patch.dict(
                os.environ,
                {"PANTHEON_PERFORMANCE_STATE_PATH": str(state_path)},
            ):
                service = PaperRuntimeService(
                    store=store,
                    identity=self._identity(),
                    runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
                    telemetry_emitter=telemetry,
                    poll_interval_seconds=3600,
                )

            snapshot = service.drain_once()

            diagnostic = snapshot["paper_state"]["performance_telemetry"]
            self.assertEqual(snapshot["status"], "degraded")
            self.assertEqual(store.queue_depth(), 1)
            self.assertEqual(diagnostic["status"], "invalid_ledger")
            self.assertEqual(diagnostic["code"], "performance_ledger_load_failed")
            self.assertIn("must be an object", diagnostic["detail"])
            self.assertEqual([event["event_type"] for event in telemetry.events], ["heartbeat"])
            self.assertEqual(
                telemetry.events[0]["metadata"]["performance_telemetry"]["code"],
                "performance_ledger_load_failed",
            )

    def test_fill_persist_failure_rolls_back_and_does_not_mark_signal_processed(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_path = Path(temporary_dir) / "paper-ledger.json"
            signal = self._signal()
            store = InMemoryPendingSignalStore([signal])
            telemetry = _FakeTelemetryEmitter()
            with patch.dict(
                os.environ,
                {"PANTHEON_PERFORMANCE_STATE_PATH": str(state_path)},
            ):
                service = PaperRuntimeService(
                    store=store,
                    identity=self._identity(),
                    runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
                    telemetry_emitter=telemetry,
                    poll_interval_seconds=3600,
                )

            original_persist = service._algo._persist_state

            def fail_fill_persist():
                if service._algo._fill_count > 0:
                    service._algo._state_error = "OSError: disk full"
                    return False
                return original_persist()

            with patch.object(
                service._algo,
                "_persist_state",
                side_effect=fail_fill_persist,
            ):
                snapshot = service.drain_once()

            ledger = service._algo.performance_ledger()
            self.assertFalse(store.is_processed(signal["signal_id"]))
            self.assertEqual(snapshot["paper_state"]["processed_signal_count"], 0)
            self.assertEqual(ledger["fill_count"], 0)
            self.assertEqual(ledger["cash"], 100_000.0)
            self.assertEqual(ledger["positions"], [])
            self.assertNotIn(
                "paper_fill_simulated",
                [event["event_type"] for event in telemetry.events],
            )

    def test_drain_once_emits_separate_pnl_and_drawdown_snapshots_with_real_mark(self):
        telemetry = _FakeTelemetryEmitter()
        mark_provider = _FakeMarkProvider(price=105.0)
        service = PaperRuntimeService(
            store=InMemoryPendingSignalStore([self._signal()]),
            identity=self._identity(),
            runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
            telemetry_emitter=telemetry,
            mark_provider=mark_provider,
            poll_interval_seconds=3600,
            max_batch_size=10,
        )

        snapshot = service.drain_once()

        pnl_events = [event for event in telemetry.events if event["event_type"] == "pnl_snapshot"]
        drawdown_events = [
            event for event in telemetry.events if event["event_type"] == "drawdown_snapshot"
        ]
        self.assertEqual(len(pnl_events), 1)
        self.assertEqual(len(drawdown_events), 1)
        self.assertAlmostEqual(pnl_events[0]["metrics"]["pnl"], 50.0)
        self.assertEqual(pnl_events[0]["pnl_as_of"], mark_provider.as_of)
        self.assertNotIn("pnl_as_of", pnl_events[0]["metrics"])
        self.assertNotIn("drawdown_pct", pnl_events[0]["metrics"])
        self.assertEqual(drawdown_events[0]["metrics"]["drawdown_pct"], 0.0)
        self.assertEqual(drawdown_events[0]["drawdown_as_of"], mark_provider.as_of)
        self.assertNotIn("drawdown_as_of", drawdown_events[0]["metrics"])
        self.assertNotIn("pnl", drawdown_events[0]["metrics"])
        self.assertEqual(
            snapshot["paper_state"]["performance_telemetry"]["code"],
            "performance_snapshots_emitted",
        )

        mark_provider.resolve = lambda symbols: (
            {},
            {
                "source": "source_ingest",
                "enabled": True,
                "requested_symbols": list(symbols),
                "resolved_symbols": [],
                "missing_symbols": list(symbols),
            },
        )
        second_snapshot = service.drain_once()

        self.assertEqual(
            len([event for event in telemetry.events if event["event_type"] == "pnl_snapshot"]),
            1,
        )
        self.assertEqual(
            second_snapshot["paper_state"]["performance_telemetry"]["code"],
            "missing_market_marks",
        )

    def test_partial_performance_pair_restarts_with_exact_payload_and_stable_event_id(self):
        binding = self._binding()
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_path = Path(temporary_dir) / "paper-ledger.json"
            first_telemetry = _DurablePairTelemetry(
                binding,
                fail_once={"drawdown_snapshot"},
            )
            with patch.dict(
                os.environ,
                {"PANTHEON_PERFORMANCE_STATE_PATH": str(state_path)},
            ):
                first = PaperRuntimeService(
                    store=InMemoryPendingSignalStore([self._signal()]),
                    identity=self._identity(),
                    runtime_manager_client=_FakeRuntimeManagerClient([binding]),
                    telemetry_emitter=first_telemetry,
                    mark_provider=_FakeMarkProvider(price=105.0),
                    poll_interval_seconds=3600,
                )
                first_snapshot = first.drain_once()

            pending = first._algo.pending_performance_pair()
            self.assertIsNotNone(pending)
            self.assertTrue(pending["events"]["pnl_snapshot"]["acked"])
            self.assertFalse(pending["events"]["drawdown_snapshot"]["acked"])
            self.assertEqual(
                first_snapshot["paper_state"]["performance_telemetry"]["failed_leg"],
                "drawdown_snapshot",
            )
            first_drawdown_attempt = first_telemetry.attempts[-1]

            corrupt_payload = json.loads(state_path.read_text(encoding="utf-8"))
            corrupt_payload["pending_performance_pair"]["events"][
                "drawdown_snapshot"
            ]["payload"]["binding_id"] = "another-binding"
            corrupt_path = Path(temporary_dir) / "corrupt-pending-pair.json"
            corrupt_path.write_text(json.dumps(corrupt_payload), encoding="utf-8")
            corrupt = PaperExecutionAlgorithm(state_path=str(corrupt_path))
            self.assertFalse(corrupt.BindPerformanceBinding(binding["binding_id"]))
            self.assertIn(
                "pending drawdown_snapshot payload binding mismatch",
                corrupt.performance_ledger()["state_load_error"],
            )

            class _ForbiddenMarkProvider:
                def resolve(self, _symbols):
                    raise AssertionError("pending pair must flush before mark resolution")

                def snapshot(self, *, requested_symbols=None):
                    raise AssertionError("pending pair must flush before mark snapshot")

            second_telemetry = _DurablePairTelemetry(binding)
            with patch.dict(
                os.environ,
                {"PANTHEON_PERFORMANCE_STATE_PATH": str(state_path)},
            ):
                restarted = PaperRuntimeService(
                    store=InMemoryPendingSignalStore(),
                    identity=self._identity(),
                    runtime_manager_client=_FakeRuntimeManagerClient([binding]),
                    telemetry_emitter=second_telemetry,
                    mark_provider=_ForbiddenMarkProvider(),
                    poll_interval_seconds=3600,
                )
                restarted_snapshot = restarted.drain_once()

            self.assertEqual(len(second_telemetry.attempts), 1)
            retried_drawdown = second_telemetry.attempts[0]
            self.assertEqual(retried_drawdown, first_drawdown_attempt)
            self.assertEqual(
                retried_drawdown["event_id"],
                first_drawdown_attempt["event_id"],
            )
            self.assertIsNone(restarted._algo.pending_performance_pair())
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIsNone(persisted["pending_performance_pair"])
            self.assertEqual(
                persisted["performance_window"]["schema_version"],
                "rolling_drawdown.v1",
            )
            self.assertEqual(
                restarted_snapshot["paper_state"]["performance_telemetry"]["code"],
                "performance_snapshots_emitted",
            )

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
        self.assertEqual(pnl_events, [])
        self.assertEqual(snapshot["paper_state"]["performance_telemetry"]["code"], "performance_no_fills")

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
        self.assertEqual(pnl_events, [])
        self.assertEqual(snapshot["paper_state"]["performance_telemetry"]["code"], "performance_no_fills")

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
        self.assertEqual(pnl_events, [])
        self.assertEqual(snapshot["paper_state"]["performance_telemetry"]["code"], "performance_no_fills")

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
        self.assertEqual(pnl_events, [])
        self.assertEqual(snapshot["paper_state"]["performance_telemetry"]["code"], "performance_no_fills")

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

    def test_runtime_telemetry_emitter_carries_binding_effective_boundary(self):
        binding = self._binding()
        binding["effective_at"] = "2026-07-14T10:00:00Z"
        emitter = RuntimeTelemetryEmitter(
            self._identity(),
            _FakeBindingResolver(binding),
        )

        event = emitter.build_event("heartbeat", {"heartbeat": 1})

        self.assertEqual(
            event["metadata"]["runtime_binding_effective_at"],
            "2026-07-14T10:00:00Z",
        )

    def test_runtime_telemetry_emitter_build_event_propagates_correlation_envelope(self):
        from services.trade_journey.correlation_envelope import mint_trade_envelope

        incoming = mint_trade_envelope(
            {"tenant_id": "tenant-1", "environment": "paper"},
            producer="control_plane.signal",
        )
        emitter = RuntimeTelemetryEmitter(self._identity(), _FakeBindingResolver(self._binding()))

        event = emitter.build_event(
            "heartbeat",
            {"heartbeat": 1},
            metadata={"correlation_envelope": incoming},
        )

        self.assertIsNotNone(event)
        outgoing = event["correlation_envelope"]
        self.assertEqual(outgoing["journey_id"], incoming["journey_id"])
        self.assertEqual(outgoing["causation_event_id"], incoming["event_id"])
        self.assertEqual(outgoing["producer"], "execution.paper_runtime")

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

    def test_runtime_directly_publishes_journey_events_to_bff(self):
        from unittest.mock import patch, MagicMock
        signal = self._signal()
        signal["signal_id"] = "sig-12345"
        store = InMemoryPendingSignalStore([signal])
        telemetry = RuntimeTelemetryEmitter(
            self._identity(),
            _FakeBindingResolver(self._binding())
        )
        service = PaperRuntimeService(
            store=store,
            identity=self._identity(),
            runtime_manager_client=_FakeRuntimeManagerClient([self._binding()]),
            telemetry_emitter=telemetry,
            poll_interval_seconds=3600,
            max_batch_size=10,
        )

        published_payloads = []
        def fake_urlopen(req, timeout=None):
            body = req.data.decode("utf-8")
            published_payloads.append(json.loads(body))
            resp = MagicMock()
            resp.read.return_value = b'{"status":"ok"}'
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            snapshot = service.drain_once()

        events = [ev for payload in published_payloads for ev in payload]
        stages = [ev["stage"] for ev in events]
        self.assertIn("signal_generation", stages)
        self.assertIn("trade_decision", stages)
        self.assertIn("order_submission", stages)
        self.assertIn("fill_management", stages)

        sig_gen = next(ev for ev in events if ev["stage"] == "signal_generation")
        self.assertEqual(sig_gen["journey_id"], "tj-sig-12345")
        self.assertEqual(sig_gen["signal_id"], "sig-12345")
        self.assertEqual(sig_gen["stage_status"], "succeeded")
        self.assertEqual(sig_gen["source"], "runtime")
        self.assertEqual(sig_gen["sequence"], 1)

        decision = next(ev for ev in events if ev["stage"] == "trade_decision")
        self.assertEqual(decision["journey_id"], "tj-sig-12345")
        self.assertEqual(decision["signal_id"], "sig-12345")
        self.assertEqual(decision["sequence"], 2)

        order = next(ev for ev in events if ev["stage"] == "order_submission")
        self.assertEqual(order["journey_id"], "tj-sig-12345")
        self.assertEqual(order["signal_id"], "sig-12345")
        self.assertEqual(order["sequence"], 3)

        fill = next(ev for ev in events if ev["stage"] == "fill_management")
        self.assertEqual(fill["journey_id"], "tj-sig-12345")
        self.assertEqual(fill["signal_id"], "sig-12345")
        self.assertEqual(fill["sequence"], 4)

        # Assert matching timestamps for the decision, order, fill chain
        self.assertEqual(decision["occurred_at"], order["occurred_at"])
        self.assertEqual(order["occurred_at"], fill["occurred_at"])


class PaperExecutionInputHardeningTest(unittest.TestCase):
    def test_invalid_normal_paper_fills_leave_ledger_unchanged(self):
        cases = (
            ("market_quantity", lambda algo: algo.MarketOrder("AAPL", float("nan"))),
            ("limit_price", lambda algo: algo.LimitOrder("AAPL", 1.0, float("inf"))),
            ("target_percent", lambda algo: algo.SetHoldings("AAPL", float("-inf"))),
        )
        for name, invoke in cases:
            with self.subTest(case=name):
                events = []
                algo = PaperExecutionAlgorithm(event_sink=events.append)
                before = algo.performance_ledger()

                invoke(algo)

                after = algo.performance_ledger()
                self.assertEqual(after["fill_count"], 0)
                self.assertEqual(after["cash"], before["cash"])
                self.assertEqual(after["positions"], [])
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].event_type, "order_rejection")

    def test_security_price_rejects_nonfinite_values_without_mutation(self):
        algo = PaperExecutionAlgorithm(default_price=100.0)

        with self.assertRaisesRegex(ValueError, "security price must be finite"):
            algo.SetSecurityPrice("AAPL", float("nan"))

        self.assertEqual(algo.EnsureSecurity("AAPL").Price, 100.0)

    def test_persist_failure_rolls_back_fill_and_suppresses_fill_event(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_path = Path(temporary_dir) / "paper-ledger.json"
            events = []
            algo = PaperExecutionAlgorithm(
                initial_cash=1_000.0,
                state_path=str(state_path),
                event_sink=events.append,
            )
            self.assertTrue(algo.BindPerformanceBinding("binding-a"))
            persisted_before = state_path.read_text(encoding="utf-8")

            with patch.object(Path, "write_text", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(RuntimeError, "persistence failed"):
                    algo.MarketOrder("AAPL", 2.0)

            ledger = algo.performance_ledger()
            self.assertEqual(ledger["fill_count"], 0)
            self.assertEqual(ledger["cash"], 1_000.0)
            self.assertEqual(ledger["positions"], [])
            self.assertEqual(events, [])
            self.assertIn("disk full", ledger["state_error"])
            self.assertEqual(state_path.read_text(encoding="utf-8"), persisted_before)

    def test_state_persistence_fsyncs_file_and_parent_directory(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_path = Path(temporary_dir) / "paper-ledger.json"
            algo = PaperExecutionAlgorithm(
                initial_cash=1_000.0,
                state_path=str(state_path),
            )

            with patch(
                "services.execution.lean_runtime.paper_runtime.os.fsync",
                wraps=os.fsync,
            ) as fsync:
                self.assertTrue(algo.BindPerformanceBinding("binding-a"))

            self.assertGreaterEqual(fsync.call_count, 2)
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8"))["binding_id"],
                "binding-a",
            )


class PaperLedgerLoadHardeningTest(unittest.TestCase):
    @staticmethod
    def _valid_payload() -> dict:
        return {
            "schema_version": "paper_performance_ledger.v1",
            "binding_id": "binding-a",
            "initial_cash": 1_000.0,
            "cash": 1_000.0,
            "fill_count": 0,
            "ledger_started_at": "2026-07-14T10:00:00Z",
            "first_fill_at": None,
            "last_fill_at": None,
            "performance_window": {},
            "holdings": {},
            "execution_prices": {},
        }

    def test_corrupt_ledger_shapes_fail_binding_and_preserve_original_bytes(self):
        def with_fill(payload):
            payload.update(
                {
                    "cash": 900.0,
                    "fill_count": 1,
                    "first_fill_at": "2026-07-14T10:01:00Z",
                    "last_fill_at": "2026-07-14T10:02:00Z",
                    "holdings": {"AAPL": 1.0},
                    "execution_prices": {"AAPL": 100.0},
                }
            )
            return payload

        reversed_timestamps = with_fill(self._valid_payload())
        reversed_timestamps["first_fill_at"] = "2026-07-14T10:03:00Z"
        cases = (
            ("non_object", [], "must be an object"),
            (
                "negative_fill_count",
                {**self._valid_payload(), "fill_count": -1},
                "fill_count must be non-negative",
            ),
            (
                "fractional_fill_count",
                {**self._valid_payload(), "fill_count": 1.5},
                "fill_count must be an integer",
            ),
            (
                "missing_fill_timestamp",
                {**with_fill(self._valid_payload()), "first_fill_at": None},
                "first_fill_at is missing",
            ),
            (
                "invalid_fill_timestamp",
                {**with_fill(self._valid_payload()), "last_fill_at": "not-a-time"},
                "last_fill_at is not a valid timestamp",
            ),
            (
                "reversed_fill_timestamps",
                reversed_timestamps,
                "first_fill_at is after last_fill_at",
            ),
            (
                "holdings_without_fills",
                {**self._valid_payload(), "holdings": {"AAPL": 1.0}},
                "without fills cannot contain holdings",
            ),
            (
                "cash_changed_without_fills",
                {**self._valid_payload(), "cash": 999.0},
                "without fills must retain initial cash",
            ),
            (
                "empty_holding_symbol",
                {**with_fill(self._valid_payload()), "holdings": {"": 1.0}},
                "empty symbol",
            ),
            (
                "holding_without_execution_price",
                {**with_fill(self._valid_payload()), "execution_prices": {}},
                "holdings lack execution prices",
            ),
            (
                "more_open_symbols_than_fills",
                {
                    **with_fill(self._valid_payload()),
                    "holdings": {"AAPL": 1.0, "MSFT": 1.0},
                    "execution_prices": {"AAPL": 100.0, "MSFT": 200.0},
                },
                "more open symbols than recorded fills",
            ),
        )
        for name, payload, diagnostic in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary_dir:
                state_path = Path(temporary_dir) / "paper-ledger.json"
                original = json.dumps(payload)
                state_path.write_text(original, encoding="utf-8")

                algo = PaperExecutionAlgorithm(state_path=str(state_path))

                self.assertFalse(algo.BindPerformanceBinding("binding-a"))
                self.assertIn(diagnostic, algo.performance_ledger()["state_load_error"])
                self.assertEqual(state_path.read_text(encoding="utf-8"), original)

    def test_nonfinite_performance_window_is_not_written_as_json_extension(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_path = Path(temporary_dir) / "paper-ledger.json"
            algo = PaperExecutionAlgorithm(state_path=str(state_path))
            self.assertTrue(algo.BindPerformanceBinding("binding-a"))
            original = state_path.read_text(encoding="utf-8")

            self.assertFalse(algo.save_performance_window({"peak": float("nan")}))

            self.assertEqual(state_path.read_text(encoding="utf-8"), original)
            self.assertIn("Out of range float values", algo.performance_ledger()["state_error"])


class TestExecutionComposePerformanceWiring(unittest.TestCase):
    def test_vm2_runtime_has_authoritative_marks_and_durable_state(self):
        import yaml

        repo_root = Path(__file__).resolve().parents[3]
        services = yaml.safe_load(
            (repo_root / "docker-compose.exec.yml").read_text(encoding="utf-8")
        )["services"]
        runtime = services["pantheon-paper-runtime"]
        environment = runtime["environment"]

        self.assertEqual(
            environment["PANTHEON_SOURCE_INGEST_URL"],
            "${PANTHEON_SOURCE_INGEST_URL:?PANTHEON_SOURCE_INGEST_URL is required}",
        )
        self.assertEqual(
            environment["PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS"],
            "${PANTHEON_PERFORMANCE_MARK_MAX_AGE_SECONDS:-172800}",
        )
        self.assertEqual(
            environment["PANTHEON_PERFORMANCE_STATE_PATH"],
            "/data/runtime/paper-performance/static-paper-runtime.json",
        )
        self.assertEqual(
            environment["PANTHEON_PAPER_SYNTHETIC_MARKET_DATA"],
            "${PANTHEON_PAPER_SYNTHETIC_MARKET_DATA:-false}",
        )
        self.assertIn("runtime-data:/data/runtime", runtime["volumes"])

        example = (repo_root / "env/prod-exec.env.example").read_text(encoding="utf-8")
        self.assertIn("PANTHEON_SOURCE_INGEST_URL=http://10.140.0.4:38097", example)
        self.assertIn("PANTHEON_PAPER_SYNTHETIC_MARKET_DATA=false", example)


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

    def test_taiwan_submit_posts_signal_client_id_and_correlation_envelope(self):
        from services.trade_journey.correlation_envelope import mint_trade_envelope

        algo, _ = self._algo()
        incoming = mint_trade_envelope(
            {"tenant_id": "tenant-1", "environment": "paper"},
            producer="strategy.signal",
        )
        algo.SetCurrentSignalContext({"correlation_envelope": incoming})
        captured = {}

        def capture(url, payload):
            captured.update(payload)
            return {"order_id": "ord-tw-envelope", "fill_price": 100.0, "fill_qty": 1}

        with patch.object(
            PaperExecutionAlgorithm,
            "_post_broker_paper_order",
            staticmethod(capture),
        ):
            algo.SubmitTaiwanBrokerOrder(
                "2330.TW", signal_id="signal-envelope-1", side="buy", quantity=1,
                quantity_type="SHARES", action="BUY",
            )

        self.assertEqual(captured["client_order_id"], "signal-envelope-1")
        self.assertEqual(captured["correlation_envelope"], incoming)

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

    def test_taiwan_invalid_broker_fill_is_rejected_without_ledger_mutation(self):
        invalid_fills = (
            {"fill_price": 0.0, "fill_qty": 1.0},
            {"fill_price": float("nan"), "fill_qty": 1.0},
            {"fill_price": float("inf"), "fill_qty": 1.0},
            {"fill_price": 100.0, "fill_qty": 0.0},
            {"fill_price": 100.0, "fill_qty": float("nan")},
            {"fill_price": 100.0, "fill_qty": float("inf")},
            {"fill_price": 100.0},
        )
        for fill in invalid_fills:
            with self.subTest(fill=repr(fill)):
                algo, events = self._algo()
                response = {"order_id": "ord-invalid", **fill}
                with patch.object(
                    PaperExecutionAlgorithm,
                    "_post_broker_paper_order",
                    staticmethod(lambda url, payload, response=response: response),
                ):
                    algo.SubmitTaiwanBrokerOrder(
                        "2330.TW",
                        signal_id="s-invalid",
                        side="buy",
                        quantity=1,
                        quantity_type="SHARES",
                        action="BUY",
                    )

                ledger = algo.performance_ledger()
                self.assertEqual(ledger["fill_count"], 0)
                self.assertEqual(ledger["cash"], 100_000.0)
                self.assertEqual(ledger["positions"], [])
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].event_type, "order_rejection")
                self.assertEqual(
                    events[0].broker_submission_status,
                    "tw_invalid_broker_fill",
                )
                self.assertEqual(
                    events[0].metadata["reject_reason"],
                    "invalid_taiwan_broker_fill",
                )


if __name__ == "__main__":

    unittest.main()
