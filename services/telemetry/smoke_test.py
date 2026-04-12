"""
Smoke test for telemetry capture and feedback adapter integration.

TEL-001: Uses the canonical telemetry_event.schema.json for all validation.
Demonstrates end-to-end telemetry capture flow:
1. Create TelemetryCapture instance with full binding context
2. Capture events in paper and live modes
3. Capture lifecycle events (deploy, rollback, kill-switch, heartbeat)
4. Ingest events to feedback store adapter
5. Query and export telemetry
"""

import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from capture import TelemetryCapture, ExecutionMode, RollbackActionType
from feedback_adapter import FeedbackStoreAdapter

# Canonical schema path (resolved relative to this smoke test file)
CANONICAL_SCHEMA = str(Path(__file__).parent / "telemetry_event.schema.json")

FULL_BINDING_CONTEXT = {
    "binding_id": "b5e6f7a8-1234-5678-9abc-def012345678",
    "runtime_id": "lean-worker-01",
    "capital_pool_id": "pool-alpha",
    "artifact_id": "artifact-strategy-v2",
    "artifact_version": "2.1.0",
    "deployment_stage": "canary",
    "plan_id": "plan-deploy-001",
    "persona_capital_binding_id": "pcb-001",
}


def run_smoke_test():
    """Run the smoke test."""
    print("=" * 60)
    print("TEL-001 Telemetry Capture Smoke Test (canonical schema)")
    print("=" * 60)

    temp_dir = tempfile.TemporaryDirectory()
    storage_dir = temp_dir.name

    try:
        # Setup
        print("\n1. Initializing TelemetryCapture with canonical schema...")
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            storage_dir=storage_dir,
            binding_context=FULL_BINDING_CONTEXT.copy(),
        )
        print("   ✓ TelemetryCapture initialized with full binding context")

        # Paper trading events
        print("\n2. Capturing paper trading events...")
        capture.capture_pnl(
            mode=ExecutionMode.PAPER,
            strategy_id="momentum_strategy",
            pnl_value=150.50,
            signal_id="sig_paper_001",
        )
        print("   ✓ Captured PnL: 150.50")

        capture.capture_drawdown(
            mode=ExecutionMode.PAPER,
            strategy_id="momentum_strategy",
            drawdown_pct=2.3,
            run_id="run_paper_001",
        )
        print("   ✓ Captured drawdown: 2.3%")

        capture.capture_fill(
            mode=ExecutionMode.PAPER,
            strategy_id="momentum_strategy",
            fill_quantity=1000.0,
            fill_price=50.25,
            broker="paper_broker",
        )
        print("   ✓ Captured fill: 1000 @ 50.25")

        capture.capture_slippage(
            mode=ExecutionMode.PAPER,
            strategy_id="momentum_strategy",
            slippage_bps=1.5,
        )
        print("   ✓ Captured slippage: 1.5 bps")

        # Live trading events
        print("\n3. Capturing live trading events...")
        capture.capture_pnl(
            mode=ExecutionMode.LIVE,
            strategy_id="momentum_strategy",
            pnl_value=200.75,
            signal_id="sig_live_001",
        )
        print("   ✓ Captured PnL: 200.75")

        capture.capture_fill(
            mode=ExecutionMode.LIVE,
            strategy_id="momentum_strategy",
            fill_quantity=500.0,
            fill_price=50.50,
            broker="live_broker",
            account_ref="acc_live_001",
        )
        print("   ✓ Captured fill: 500 @ 50.50")

        capture.capture_order_rejection(
            mode=ExecutionMode.LIVE,
            strategy_id="momentum_strategy",
            reject_reason="insufficient_buying_power",
        )
        print("   ✓ Captured order rejection")

        # Lifecycle events (TEL-001)
        print("\n4. Capturing lifecycle events (deploy, rollback, kill-switch, heartbeat)...")
        ok = capture.capture_deploy_started(ExecutionMode.LIVE, "momentum_strategy")
        assert ok, "deploy_started should succeed with full binding context"
        print("   ✓ deploy_started")

        ok = capture.capture_deploy_completed(ExecutionMode.LIVE, "momentum_strategy")
        assert ok, "deploy_completed should succeed"
        print("   ✓ deploy_completed")

        ok = capture.capture_rollback_started(
            ExecutionMode.LIVE, "momentum_strategy",
            rollback_parent="old-binding-uuid",
            rollback_action_type=RollbackActionType.PAUSE_THEN_REPLACE,
        )
        assert ok, "rollback_started should succeed"
        print("   ✓ rollback_started")

        ok = capture.capture_rollback_completed(
            ExecutionMode.LIVE, "momentum_strategy",
            rollback_parent="old-binding-uuid",
            rollback_action_type=RollbackActionType.PAUSE_THEN_REPLACE,
        )
        assert ok, "rollback_completed should succeed"
        print("   ✓ rollback_completed")

        ok = capture.capture_kill_switch(
            ExecutionMode.LIVE, "momentum_strategy",
            metadata={"reason": "drawdown_exceeded"},
        )
        assert ok, "kill_switch_action should succeed"
        print("   ✓ kill_switch_action")

        ok = capture.capture_heartbeat(ExecutionMode.LIVE, "momentum_strategy")
        assert ok, "heartbeat should succeed"
        print("   ✓ heartbeat")

        # Verify separation
        print("\n5. Verifying paper/live separation...")
        paper_events = capture.get_paper_events()
        live_events = capture.get_live_events()

        print(f"   Paper events: {len(paper_events)}")
        print(f"   Live events: {len(live_events)}")
        assert len(paper_events) == 4, f"Expected 4 paper events, got {len(paper_events)}"
        assert len(live_events) == 9, f"Expected 9 live events, got {len(live_events)}"
        print("   ✓ Separation verified")

        # All events carry binding evidence
        for event in paper_events + live_events:
            assert event.get("binding_id") == FULL_BINDING_CONTEXT["binding_id"], \
                f"Missing binding_id in {event['event_type']}"
            assert event.get("deployment_stage") == FULL_BINDING_CONTEXT["deployment_stage"], \
                f"deployment_stage mismatch in {event['event_type']}"
            assert len(event["metrics"]) >= 1, \
                f"Empty metrics in {event['event_type']}"
        print("   ✓ All events carry binding evidence and non-empty metrics")

        # Persistence check
        print("\n6. Verifying persistent storage...")
        # NOTE: binding_context has deployment_stage=canary → execution_mode=live,
        # so _persist_event writes ALL events to live/ regardless of capture mode.
        paper_dir = Path(storage_dir) / "paper"
        live_dir = Path(storage_dir) / "live"

        paper_files = list(paper_dir.glob("*.json"))
        live_files = list(live_dir.glob("*.json"))

        print(f"   Paper files: {len(paper_files)}")
        print(f"   Live files: {len(live_files)}")
        # All 13 events persist to live/ because canary → execution_mode=live
        assert len(paper_files) == 0, f"Expected 0 paper files (canary→live), got {len(paper_files)}"
        assert len(live_files) == 13, f"Expected 13 live files, got {len(live_files)}"
        print("   ✓ Persistence verified (all events in live/ due to canary binding)")

        # Feed to adapter (with configured shared feedback store)
        print("\n7. Ingesting to feedback store adapter...")
        store_path = Path(storage_dir) / "feedback_store.jsonl"
        adapter = FeedbackStoreAdapter(feedback_store_path=str(store_path))

        for event in paper_events:
            adapter.ingest_telemetry_event(
                event,
                strategy_id="momentum_strategy",
                promotion_state="paper",
            )

        for event in live_events:
            adapter.ingest_telemetry_event(
                event,
                strategy_id="momentum_strategy",
                promotion_state="live",
            )

        print(f"   ✓ Ingested {len(adapter.telemetry_log)} events")

        # Query by strategy
        print("\n8. Querying by strategy...")
        strategy_events = adapter.get_telemetry_for_strategy("momentum_strategy")
        print(f"   Found {len(strategy_events)} events for momentum_strategy")
        assert len(strategy_events) == 13, f"Expected 13 total events, got {len(strategy_events)}"
        print("   ✓ Query successful")

        # Query by promotion state
        print("\n9. Querying by promotion state...")
        paper_state_events = adapter.get_telemetry_by_promotion_state("paper")
        live_state_events = adapter.get_telemetry_by_promotion_state("live")

        print(f"   Paper state: {len(paper_state_events)} events")
        print(f"   Live state: {len(live_state_events)} events")
        assert len(paper_state_events) == 4, f"Expected 4 paper state events, got {len(paper_state_events)}"
        assert len(live_state_events) == 9, f"Expected 9 live state events, got {len(live_state_events)}"
        print("   ✓ State queries successful")

        # Export
        print("\n10. Exporting telemetry...")
        export_path = Path(storage_dir) / "telemetry_export.jsonl"
        adapter.export_telemetry(str(export_path), format="jsonl")

        lines = export_path.read_text().strip().split("\n")
        print(f"   Exported {len(lines)} lines to JSONL")
        assert len(lines) == 13, f"Expected 13 exported events, got {len(lines)}"
        print("   ✓ Export successful")

        # Test shared store recovery
        print("\n11. Testing shared store recovery...")
        adapter2 = FeedbackStoreAdapter(feedback_store_path=str(store_path))
        recovered_events = adapter2.get_telemetry_for_strategy("momentum_strategy")
        print(f"   New adapter instance recovered {len(recovered_events)} events from shared store")
        assert len(recovered_events) == 13, f"Expected 13 recovered events, got {len(recovered_events)}"
        print("   ✓ Shared store recovery verified")

        # Test idempotency
        print("\n12. Testing idempotency...")
        first_event = paper_events[0]
        adapter2.ingest_telemetry_event(
            first_event,
            strategy_id="momentum_strategy",
            promotion_state="paper",
        )
        strategy_events_after = adapter2.get_telemetry_for_strategy("momentum_strategy")
        print(f"   After duplicate ingest: {len(strategy_events_after)} events (should still be 13)")
        assert len(strategy_events_after) == 13, "Duplicate event should not increase count"
        assert len(adapter2.telemetry_log) == 13, "Duplicate event should not be in telemetry_log"
        print("   ✓ Idempotency verified (no duplicate in adapter queries)")

        # Test partial binding context rejection (TEL-001 Fix #2)
        print("\n13. Testing partial binding context rejection...")
        partial_capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context={"binding_id": "b5e6f7a8-1234-5678-9abc-def012345678"},
        )
        ok = partial_capture.capture_pnl(ExecutionMode.PAPER, "strat", 100.0)
        assert not ok, "Partial binding context should be rejected"
        assert len(partial_capture.get_paper_events()) == 0
        print("   ✓ Partial binding context correctly rejected (no fabricated defaults)")

        # Test frozen deployment stage (TEL-001 Fix #3)
        print("\n14. Testing frozen deployment stage...")
        frozen_ctx = FULL_BINDING_CONTEXT.copy()
        frozen_ctx["deployment_stage"] = "frozen"
        frozen_capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=frozen_ctx,
        )
        ok = frozen_capture.capture_pnl(ExecutionMode.LIVE, "strat", 1.0)
        assert ok, "frozen stage should produce valid events"
        event = frozen_capture.get_live_events()[0]
        assert event["deployment_stage"] == "frozen"
        assert event["environment"] == "frozen"
        assert event["execution_mode"] == "paper", \
            f"frozen stage must map to paper mode, got {event['execution_mode']}"
        print("   ✓ Frozen deployment stage correctly maps to paper execution_mode")

        # Summary
        print("\n" + "=" * 60)
        print("✓ All smoke tests passed!")
        print("=" * 60)
        print("\nSummary:")
        print(f"  - Paper events captured: {len(paper_events)}")
        print(f"  - Live events captured: {len(live_events)}")
        print(f"  - Lifecycle events (deploy/rollback/kill-switch/heartbeat): 6")
        print(f"  - Total events in adapter: {len(adapter.telemetry_log)}")
        print(f"  - Events by promotion state: paper={len(paper_state_events)}, live={len(live_state_events)}")
        print(f"  - Feedback store path: {store_path}")
        print(f"  - Export file: {export_path}")
        print(f"  - Schema: canonical telemetry_event.schema.json")
        print("\nKey acceptance criteria met:")
        print("  ✓ All events validated against canonical telemetry_event.schema.json")
        print("  ✓ Full binding evidence injected (no fabricated defaults)")
        print("  ✓ Non-metric lifecycle events carry canonical metric payloads")
        print("  ✓ frozen deployment_stage maps to paper execution_mode")
        print("  ✓ Partial binding context explicitly rejected")
        print("  ✓ Shared feedback store semantics with cross-process recovery")
        print("  ✓ Idempotent event persistence (no duplicates in queries)")

        return True

    except Exception as e:
        print(f"\n✗ Smoke test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        temp_dir.cleanup()


if __name__ == "__main__":
    success = run_smoke_test()
    exit(0 if success else 1)
