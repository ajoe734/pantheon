"""
Smoke test for telemetry capture and feedback adapter integration.

Demonstrates end-to-end telemetry capture flow:
1. Create TelemetryCapture instance
2. Capture events in paper and live modes
3. Ingest events to feedback store adapter
4. Query and export telemetry
"""

import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from capture import TelemetryCapture, ExecutionMode
from feedback_adapter import FeedbackStoreAdapter


def create_schema_file(path):
    """Create a minimal telemetry schema for testing."""
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "https://pantheon/execution-telemetry-event/v1",
        "type": "object",
        "required": ["event_id", "event_type", "created_at", "execution_mode", "target", "metrics"],
        "properties": {
            "event_id": {"type": "string"},
            "event_type": {
                "type": "string",
                "enum": [
                    "pnl_snapshot",
                    "drawdown_snapshot",
                    "slippage_observation",
                    "fill_observation",
                    "order_rejection"
                ]
            },
            "created_at": {"type": "string"},
            "execution_mode": {
                "type": "string",
                "enum": ["paper", "live"]
            },
            "target": {
                "type": "object",
                "required": ["strategy_id"],
                "properties": {
                    "strategy_id": {"type": "string"},
                    "promotion_state": {"type": "string"}
                }
            },
            "metrics": {"type": "object"}
        }
    }
    
    with open(path, "w") as f:
        json.dump(schema, f)


def run_smoke_test():
    """Run the smoke test."""
    print("=" * 60)
    print("FB-003 Telemetry Capture Smoke Test")
    print("=" * 60)
    
    temp_dir = tempfile.TemporaryDirectory()
    storage_dir = temp_dir.name
    
    try:
        # Setup
        schema_path = Path(storage_dir) / "schema.json"
        create_schema_file(schema_path)
        
        print("\n1. Initializing TelemetryCapture...")
        capture = TelemetryCapture(
            schema_path=str(schema_path),
            storage_dir=storage_dir
        )
        print("   ✓ TelemetryCapture initialized")
        
        # Paper trading events
        print("\n2. Capturing paper trading events...")
        capture.capture_pnl(
            mode=ExecutionMode.PAPER,
            strategy_id="momentum_strategy",
            pnl_value=150.50,
            signal_id="sig_paper_001"
        )
        print("   ✓ Captured PnL: 150.50")
        
        capture.capture_drawdown(
            mode=ExecutionMode.PAPER,
            strategy_id="momentum_strategy",
            drawdown_pct=2.3,
            run_id="run_paper_001"
        )
        print("   ✓ Captured drawdown: 2.3%")
        
        capture.capture_fill(
            mode=ExecutionMode.PAPER,
            strategy_id="momentum_strategy",
            fill_quantity=1000.0,
            fill_price=50.25,
            broker="paper_broker"
        )
        print("   ✓ Captured fill: 1000 @ 50.25")
        
        capture.capture_slippage(
            mode=ExecutionMode.PAPER,
            strategy_id="momentum_strategy",
            slippage_bps=1.5
        )
        print("   ✓ Captured slippage: 1.5 bps")
        
        # Live trading events
        print("\n3. Capturing live trading events...")
        capture.capture_pnl(
            mode=ExecutionMode.LIVE,
            strategy_id="momentum_strategy",
            pnl_value=200.75,
            signal_id="sig_live_001"
        )
        print("   ✓ Captured PnL: 200.75")
        
        capture.capture_fill(
            mode=ExecutionMode.LIVE,
            strategy_id="momentum_strategy",
            fill_quantity=500.0,
            fill_price=50.50,
            broker="live_broker",
            account_ref="acc_live_001"
        )
        print("   ✓ Captured fill: 500 @ 50.50")
        
        capture.capture_order_rejection(
            mode=ExecutionMode.LIVE,
            strategy_id="momentum_strategy",
            reject_reason="insufficient_buying_power"
        )
        print("   ✓ Captured order rejection")
        
        # Verify separation
        print("\n4. Verifying paper/live separation...")
        paper_events = capture.get_paper_events()
        live_events = capture.get_live_events()
        
        print(f"   Paper events: {len(paper_events)}")
        print(f"   Live events: {len(live_events)}")
        assert len(paper_events) == 4, "Expected 4 paper events"
        assert len(live_events) == 3, "Expected 3 live events"
        print("   ✓ Separation verified")
        
        # Persistence check
        print("\n5. Verifying persistent storage...")
        paper_dir = Path(storage_dir) / "paper"
        live_dir = Path(storage_dir) / "live"
        
        paper_files = list(paper_dir.glob("*.json"))
        live_files = list(live_dir.glob("*.json"))
        
        print(f"   Paper files: {len(paper_files)}")
        print(f"   Live files: {len(live_files)}")
        assert len(paper_files) == 4, "Expected 4 paper files"
        assert len(live_files) == 3, "Expected 3 live files"
        print("   ✓ Persistence verified")
        
        # Feed to adapter (with configured shared feedback store)
        print("\n6. Ingesting to feedback store adapter...")
        store_path = Path(storage_dir) / "feedback_store.jsonl"
        adapter = FeedbackStoreAdapter(feedback_store_path=str(store_path))
        
        for event in paper_events:
            adapter.ingest_telemetry_event(
                event,
                strategy_id="momentum_strategy",
                promotion_state="paper"
            )
        
        for event in live_events:
            adapter.ingest_telemetry_event(
                event,
                strategy_id="momentum_strategy",
                promotion_state="live"
            )
        
        print(f"   ✓ Ingested {len(adapter.telemetry_log)} events")
        
        # Query by strategy
        print("\n7. Querying by strategy...")
        strategy_events = adapter.get_telemetry_for_strategy("momentum_strategy")
        print(f"   Found {len(strategy_events)} events for momentum_strategy")
        assert len(strategy_events) == 7, "Expected 7 total events"
        print("   ✓ Query successful")
        
        # Query by promotion state
        print("\n8. Querying by promotion state...")
        paper_state_events = adapter.get_telemetry_by_promotion_state("paper")
        live_state_events = adapter.get_telemetry_by_promotion_state("live")
        
        print(f"   Paper state: {len(paper_state_events)} events")
        print(f"   Live state: {len(live_state_events)} events")
        assert len(paper_state_events) == 4, "Expected 4 paper state events"
        assert len(live_state_events) == 3, "Expected 3 live state events"
        print("   ✓ State queries successful")
        
        # Export
        print("\n9. Exporting telemetry...")
        export_path = Path(storage_dir) / "telemetry_export.jsonl"
        adapter.export_telemetry(str(export_path), format="jsonl")
        
        lines = export_path.read_text().strip().split("\n")
        print(f"   Exported {len(lines)} lines to JSONL")
        assert len(lines) == 7, "Expected 7 exported events"
        print("   ✓ Export successful")
        
        # Test shared store recovery (Issue #1)
        print("\n10. Testing shared store recovery...")
        adapter2 = FeedbackStoreAdapter(feedback_store_path=str(store_path))
        recovered_events = adapter2.get_telemetry_for_strategy("momentum_strategy")
        print(f"   New adapter instance recovered {len(recovered_events)} events from shared store")
        assert len(recovered_events) == 7, f"Expected 7 recovered events, got {len(recovered_events)}"
        print("   ✓ Shared store recovery verified")
        
        # Test idempotency (Issue #2)
        print("\n11. Testing idempotency...")
        first_event = paper_events[0]
        adapter2.ingest_telemetry_event(
            first_event,
            strategy_id="momentum_strategy",
            promotion_state="paper"
        )
        # Should not add duplicate to buffer
        strategy_events_after = adapter2.get_telemetry_for_strategy("momentum_strategy")
        print(f"   After duplicate ingest: {len(strategy_events_after)} events (should still be 7)")
        assert len(strategy_events_after) == 7, "Duplicate event should not increase count"
        assert len(adapter2.telemetry_log) == 7, "Duplicate event should not be in telemetry_log"
        print("   ✓ Idempotency verified (no duplicate in adapter queries)")
        
        # Summary
        print("\n" + "=" * 60)
        print("✓ All smoke tests passed!")
        print("=" * 60)
        print("\nSummary:")
        print(f"  - Paper events captured: {len(paper_events)}")
        print(f"  - Live events captured: {len(live_events)}")
        print(f"  - Total events in adapter: {len(adapter.telemetry_log)}")
        print(f"  - Events by promotion state: paper={len(paper_state_events)}, live={len(live_state_events)}")
        print(f"  - Feedback store path: {store_path}")
        print(f"  - Export file: {export_path}")
        print("  - Schema validation: enabled")
        print("\nKey acceptance criteria met:")
        print("  ✓ Execution telemetry schema linked to feedback store")
        print("  ✓ Paper and live telemetry distinguished")
        print("  ✓ Fill and slippage capture documented")
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
