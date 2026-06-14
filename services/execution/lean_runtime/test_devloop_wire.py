"""DEVLOOP-WIRE: Wire PaperSignalProducer to 15 active paper bindings.

Acceptance criteria
-------------------
- No manual signal seed: all signals originate from PaperSignalProducer + SmokeStrategy.
- 15 distinct BindingRefs / PaperRuntimeService instances (simulating 15 active paper
  RuntimeBindings in the fleet).
- After one producer tick + one drain_once() per binding:
    * processed_signal_count >= 1  (loop-run)
    * execution_event_count  >= 1  (trade / fill event)
    * at least one paper_fill_simulated telemetry event received (right-half data flow)
- Producer return value: each binding_id maps to exactly 1 signal enqueued per tick.

Paper-only by construction:
- Uses InMemoryPendingSignalStore — no Redis, no broker, no live order route.
- Telemetry goes to an in-process fake emitter — no network calls.
- PaperRuntimeService is wired with a fake runtime-manager client — no HTTP.
"""
from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from typing import Any

from services.execution.lean_runtime.paper_signal_producer import (
    BindingRef,
    PaperSignalProducer,
    SmokeStrategy,
)
from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity

_N_BINDINGS = 15
_NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Minimal fakes (same pattern as test_paper_runtime.py)
# ---------------------------------------------------------------------------

class _FakeRuntimeManagerClient:
    """Returns a canned binding list so RuntimeBindingResolver never hits HTTP."""

    def __init__(self, binding: dict[str, Any]) -> None:
        self._binding = binding

    def list_all(self) -> list[dict[str, Any]]:
        return [self._binding]


class _FakeTelemetryEmitter:
    """Captures every emit() call for assertion."""

    def __init__(self) -> None:
        self.enabled = True
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, metrics: dict[str, Any], metadata: dict[str, Any] | None = None) -> bool:
        self.events.append({"event_type": event_type, "metrics": metrics, "metadata": metadata or {}})
        return True

    def emit_deploy_started(self) -> bool:
        return self.emit("deploy_started", {"action": "deploy_started"})

    def emit_deploy_completed(self) -> bool:
        return self.emit("deploy_completed", {"action": "deploy_completed"})

    def emit_heartbeat(self, metadata: dict[str, Any] | None = None) -> bool:
        return self.emit("heartbeat", {"heartbeat": 1}, metadata=metadata)

    def emit_pnl_snapshot(self, pnl: float, metadata: dict[str, Any] | None = None, extra_metrics: dict[str, Any] | None = None) -> bool:
        metrics: dict[str, Any] = {"pnl": float(pnl)}
        metrics.update(extra_metrics or {})
        return self.emit("pnl_snapshot", metrics, metadata=metadata)

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": True, "url": "memory://fake", "sent": len(self.events), "failed": 0, "last_error": None}

    def fill_event_types(self) -> list[str]:
        return [e["event_type"] for e in self.events]


def _make_binding(binding_id: str, runtime_id: str) -> dict[str, Any]:
    return {
        "binding_id": binding_id,
        "runtime_id": runtime_id,
        "capital_pool_id": f"pool-{binding_id}",
        "artifact_id": "artifact-devloop-wire",
        "artifact_version": "1.0.0",
        "deployment_mode": "paper",
        "plan_id": f"plan-{binding_id}",
        "persona_capital_binding_id": f"pcb-{binding_id}",
        "status": "active",
    }


def _make_identity(runtime_id: str) -> RuntimeIdentity:
    return RuntimeIdentity.from_env({
        "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
        "PANTHEON_RUNTIME_MODE": "paper",
        "PANTHEON_RUNTIME_ID": runtime_id,
        "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
        "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
        "PANTHEON_WORKSPACE_REF": "workspace-devloop",
        "PANTHEON_AUTH_PROFILE_REF": "auth-profile-devloop",
        "PANTHEON_PERSONA_ID": "persona-devloop",
        "PANTHEON_SESSION_ID": f"session-{runtime_id}",
        "PANTHEON_TRACE_ID": str(uuid.uuid4()),
        "PANTHEON_REQUEST_ID": f"req-{runtime_id}",
    })


# ---------------------------------------------------------------------------
# Fixtures: 15 bindings
# ---------------------------------------------------------------------------

def _build_fleet(n: int) -> list[tuple[BindingRef, InMemoryPendingSignalStore, PaperRuntimeService, _FakeTelemetryEmitter]]:
    """Return one (binding_ref, store, service, telemetry) tuple per binding."""
    fleet = []
    for i in range(1, n + 1):
        binding_id = f"rb-devloop-wire-{i:03d}"
        runtime_id = f"paper-runtime-wire-{i:03d}"
        strategy_id = f"strategy-devloop-{i:03d}"

        ref = BindingRef(binding_id=binding_id, strategy_id=strategy_id, symbol="AAPL.US")
        store = InMemoryPendingSignalStore()
        telemetry = _FakeTelemetryEmitter()
        binding_dict = _make_binding(binding_id, runtime_id)

        service = PaperRuntimeService(
            store=store,
            identity=_make_identity(runtime_id),
            runtime_manager_client=_FakeRuntimeManagerClient(binding_dict),
            telemetry_emitter=telemetry,
            poll_interval_seconds=3600,
            max_batch_size=50,
        )
        fleet.append((ref, store, service, telemetry))
    return fleet


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDevloopWire(unittest.TestCase):
    """15 active paper bindings driven by PaperSignalProducer (no manual seed)."""

    def _run_wire(self) -> list[tuple[dict[str, Any], _FakeTelemetryEmitter]]:
        """
        1. Build 15 (BindingRef, store, service) triples.
        2. Tick the producer once — signals land in per-binding stores organically.
        3. drain_once() on each service.
        4. Return (snapshot, telemetry) pairs.
        """
        fleet = _build_fleet(_N_BINDINGS)

        # ---- organic produce (no manual enqueue) ---
        refs = [ref for ref, *_ in fleet]
        stores_by_id = {ref.binding_id: store for ref, store, *_ in fleet}

        producer = PaperSignalProducer(
            store_for=lambda b: stores_by_id[b.binding_id],
            strategy=SmokeStrategy(),
        )
        tick_result = producer.tick(refs, _NOW)

        # Verify producer emitted exactly 1 signal per binding
        self.assertEqual(len(tick_result), _N_BINDINGS)
        for ref in refs:
            self.assertEqual(tick_result[ref.binding_id], 1,
                             f"Expected 1 signal for {ref.binding_id}, got {tick_result[ref.binding_id]}")

        # ---- drain each service once ---
        results = []
        for ref, store, service, telemetry in fleet:
            snapshot = service.drain_once()
            results.append((snapshot, telemetry))
        return results

    def test_15_bindings_each_produce_loop_run_and_trade(self) -> None:
        """Each of 15 bindings must have processed_signal_count>=1 and execution_event_count>=1."""
        results = self._run_wire()
        self.assertEqual(len(results), _N_BINDINGS)

        for idx, (snapshot, _) in enumerate(results, start=1):
            binding_label = f"binding {idx:02d}"
            paper = snapshot["paper_state"]
            self.assertGreaterEqual(
                paper["processed_signal_count"], 1,
                f"{binding_label}: expected loop-run (processed_signal_count>=1)"
            )
            self.assertGreaterEqual(
                paper["execution_event_count"], 1,
                f"{binding_label}: expected trade (execution_event_count>=1)"
            )
            self.assertFalse(snapshot["stub_mode"], f"{binding_label}: must not be in stub mode")
            self.assertTrue(snapshot["binding_lookup"]["resolved"], f"{binding_label}: binding must resolve")

    def test_15_bindings_each_emit_paper_fill_telemetry(self) -> None:
        """Right-half data flow: every binding's fake telemetry captures paper_fill_simulated."""
        results = self._run_wire()
        for idx, (snapshot, telemetry) in enumerate(results, start=1):
            binding_label = f"binding {idx:02d}"
            fill_events = [e for e in telemetry.events if e["event_type"] == "paper_fill_simulated"]
            self.assertGreaterEqual(
                len(fill_events), 1,
                f"{binding_label}: expected at least 1 paper_fill_simulated telemetry event"
            )
            # Sanity: fill is never submitted to a live broker (paper-only invariant)
            for event in fill_events:
                self.assertFalse(
                    event["metadata"].get("submitted_to_broker", True),
                    f"{binding_label}: paper fills must never reach live broker"
                )

    def test_no_cross_binding_signal_contamination(self) -> None:
        """Signals must stay isolated: each binding's store holds its own strategy_id."""
        fleet = _build_fleet(_N_BINDINGS)
        refs = [ref for ref, *_ in fleet]
        stores_by_id = {ref.binding_id: store for ref, store, *_ in fleet}

        producer = PaperSignalProducer(
            store_for=lambda b: stores_by_id[b.binding_id],
            strategy=SmokeStrategy(),
        )
        producer.tick(refs, _NOW)

        for ref, store, *_ in fleet:
            pending = store.get_pending()
            self.assertEqual(len(pending), 1, f"{ref.binding_id}: expected exactly 1 pending signal")
            self.assertEqual(pending[0]["strategy_id"], ref.strategy_id,
                             f"{ref.binding_id}: strategy_id mismatch (cross-binding contamination)")
            self.assertEqual(pending[0]["binding_id"], ref.binding_id,
                             f"{ref.binding_id}: binding_id mismatch in signal payload")

    def test_producer_tick_returns_all_15_counts(self) -> None:
        """PaperSignalProducer.tick() must report 1 enqueued signal for every binding."""
        fleet = _build_fleet(_N_BINDINGS)
        refs = [ref for ref, *_ in fleet]
        stores_by_id = {ref.binding_id: store for ref, store, *_ in fleet}

        producer = PaperSignalProducer(
            store_for=lambda b: stores_by_id[b.binding_id],
            strategy=SmokeStrategy(),
        )
        tick_result = producer.tick(refs, _NOW)

        self.assertEqual(set(tick_result.keys()), {ref.binding_id for ref in refs},
                         "tick() must return an entry for every binding")
        self.assertTrue(all(v == 1 for v in tick_result.values()),
                        f"Each binding must receive exactly 1 signal; got {tick_result}")


if __name__ == "__main__":
    unittest.main()
