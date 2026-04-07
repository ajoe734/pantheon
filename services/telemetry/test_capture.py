"""
Unit tests for telemetry capture module.
"""

import unittest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from capture import TelemetryCapture, ExecutionMode, EventType


class TestTelemetryCapture(unittest.TestCase):
    """Test TelemetryCapture class."""

    def setUp(self):
        """Create test instance."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_dir = self.temp_dir.name
        
        # Create schema path
        schema_path = Path(self.storage_dir) / "schema.json"
        self.schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": ["event_id", "event_type", "created_at", "execution_mode", "target", "metrics"],
            "properties": {
                "event_id": {"type": "string"},
                "event_type": {
                    "type": "string",
                    "enum": ["pnl_snapshot", "drawdown_snapshot", "slippage_observation", "fill_observation", "order_rejection"]
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
                        "strategy_id": {"type": "string"}
                    }
                },
                "metrics": {"type": "object"}
            }
        }
        
        with open(schema_path, "w") as f:
            json.dump(self.schema, f)
        
        self.capture = TelemetryCapture(
            schema_path=str(schema_path),
            storage_dir=self.storage_dir
        )

    def tearDown(self):
        """Cleanup temp directory."""
        self.temp_dir.cleanup()

    def test_capture_pnl_paper(self):
        """Test capturing PnL in paper trading mode."""
        result = self.capture.capture_pnl(
            mode=ExecutionMode.PAPER,
            strategy_id="test_strategy",
            pnl_value=100.50
        )
        
        self.assertTrue(result)
        events = self.capture.get_paper_events()
        self.assertEqual(len(events), 1)
        
        event = events[0]
        self.assertEqual(event["event_type"], EventType.PNL_SNAPSHOT.value)
        self.assertEqual(event["execution_mode"], ExecutionMode.PAPER.value)
        self.assertEqual(event["target"]["strategy_id"], "test_strategy")
        self.assertEqual(event["metrics"]["pnl"], 100.50)

    def test_capture_pnl_live(self):
        """Test capturing PnL in live trading mode."""
        result = self.capture.capture_pnl(
            mode=ExecutionMode.LIVE,
            strategy_id="test_strategy",
            pnl_value=-50.25
        )
        
        self.assertTrue(result)
        events = self.capture.get_live_events()
        self.assertEqual(len(events), 1)
        
        event = events[0]
        self.assertEqual(event["execution_mode"], ExecutionMode.LIVE.value)
        self.assertEqual(event["metrics"]["pnl"], -50.25)

    def test_capture_drawdown(self):
        """Test capturing drawdown."""
        result = self.capture.capture_drawdown(
            mode=ExecutionMode.PAPER,
            strategy_id="test_strategy",
            drawdown_pct=5.5
        )
        
        self.assertTrue(result)
        events = self.capture.get_events(ExecutionMode.PAPER)
        self.assertEqual(len(events), 1)
        
        event = events[0]
        self.assertEqual(event["event_type"], EventType.DRAWDOWN_SNAPSHOT.value)
        self.assertEqual(event["metrics"]["drawdown_pct"], 5.5)

    def test_capture_slippage(self):
        """Test capturing slippage in basis points."""
        result = self.capture.capture_slippage(
            mode=ExecutionMode.LIVE,
            strategy_id="test_strategy",
            slippage_bps=2.5,
            signal_id="sig_123",
            run_id="run_456"
        )
        
        self.assertTrue(result)
        events = self.capture.get_live_events()
        self.assertEqual(len(events), 1)
        
        event = events[0]
        self.assertEqual(event["event_type"], EventType.SLIPPAGE_OBSERVATION.value)
        self.assertEqual(event["metrics"]["slippage_bps"], 2.5)
        self.assertEqual(event["signal_id"], "sig_123")
        self.assertEqual(event["run_id"], "run_456")

    def test_capture_fill(self):
        """Test capturing fill observation."""
        result = self.capture.capture_fill(
            mode=ExecutionMode.PAPER,
            strategy_id="test_strategy",
            fill_quantity=100.0,
            fill_price=50.25,
            broker="interactive_brokers"
        )
        
        self.assertTrue(result)
        events = self.capture.get_paper_events()
        self.assertEqual(len(events), 1)
        
        event = events[0]
        self.assertEqual(event["event_type"], EventType.FILL_OBSERVATION.value)
        self.assertEqual(event["metrics"]["fill_quantity"], 100.0)
        self.assertEqual(event["metrics"]["fill_price"], 50.25)
        self.assertEqual(event["broker"], "interactive_brokers")

    def test_capture_order_rejection(self):
        """Test capturing order rejection."""
        result = self.capture.capture_order_rejection(
            mode=ExecutionMode.LIVE,
            strategy_id="test_strategy",
            reject_reason="insufficient_buying_power",
            account_ref="acc_001"
        )
        
        self.assertTrue(result)
        events = self.capture.get_live_events()
        self.assertEqual(len(events), 1)
        
        event = events[0]
        self.assertEqual(event["event_type"], EventType.ORDER_REJECTION.value)
        self.assertEqual(event["metrics"]["reject_reason"], "insufficient_buying_power")
        self.assertEqual(event["account_ref"], "acc_001")

    def test_paper_and_live_separation(self):
        """Test that paper and live events are kept separate."""
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)
        self.capture.capture_pnl(ExecutionMode.LIVE, "strat_1", 200.0)
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 150.0)
        
        paper = self.capture.get_paper_events()
        live = self.capture.get_live_events()
        
        self.assertEqual(len(paper), 2)
        self.assertEqual(len(live), 1)
        
        # Verify values
        self.assertEqual(paper[0]["metrics"]["pnl"], 100.0)
        self.assertEqual(paper[1]["metrics"]["pnl"], 150.0)
        self.assertEqual(live[0]["metrics"]["pnl"], 200.0)

    def test_get_all_events(self):
        """Test retrieving all events."""
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)
        self.capture.capture_pnl(ExecutionMode.LIVE, "strat_1", 200.0)
        
        all_events = self.capture.get_events()
        self.assertEqual(len(all_events), 2)

    def test_event_has_unique_id(self):
        """Test that each event has unique ID."""
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 200.0)
        
        events = self.capture.get_paper_events()
        ids = [e["event_id"] for e in events]
        
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)  # All unique

    def test_event_has_timestamp(self):
        """Test that events have ISO8601 timestamps."""
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)
        
        event = self.capture.get_paper_events()[0]
        self.assertIn("created_at", event)
        
        # Verify it's ISO8601 format
        timestamp = event["created_at"]
        self.assertTrue(timestamp.endswith("Z"))

    def test_clear_paper_events(self):
        """Test clearing paper events."""
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)
        self.capture.capture_pnl(ExecutionMode.LIVE, "strat_1", 200.0)
        
        self.capture.clear_events(ExecutionMode.PAPER)
        
        paper = self.capture.get_paper_events()
        live = self.capture.get_live_events()
        
        self.assertEqual(len(paper), 0)
        self.assertEqual(len(live), 1)

    def test_clear_all_events(self):
        """Test clearing all events."""
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)
        self.capture.capture_pnl(ExecutionMode.LIVE, "strat_1", 200.0)
        
        self.capture.clear_events()
        
        all_events = self.capture.get_events()
        self.assertEqual(len(all_events), 0)

    def test_event_persistence(self):
        """Test that events are written to disk."""
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)
        
        paper_dir = Path(self.storage_dir) / "paper"
        self.assertTrue(paper_dir.exists())
        
        event_files = list(paper_dir.glob("*.json"))
        self.assertEqual(len(event_files), 1)
        
        # Verify file content
        with open(event_files[0], "r") as f:
            data = json.load(f)
        
        self.assertEqual(data["execution_mode"], "paper")
        self.assertEqual(data["metrics"]["pnl"], 100.0)


class TestExecutionMode(unittest.TestCase):
    """Test ExecutionMode enum."""

    def test_enum_values(self):
        """Test enum values."""
        self.assertEqual(ExecutionMode.PAPER.value, "paper")
        self.assertEqual(ExecutionMode.LIVE.value, "live")


class TestEventType(unittest.TestCase):
    """Test EventType enum."""

    def test_enum_values(self):
        """Test event type enum values."""
        self.assertEqual(EventType.PNL_SNAPSHOT.value, "pnl_snapshot")
        self.assertEqual(EventType.DRAWDOWN_SNAPSHOT.value, "drawdown_snapshot")
        self.assertEqual(EventType.SLIPPAGE_OBSERVATION.value, "slippage_observation")
        self.assertEqual(EventType.FILL_OBSERVATION.value, "fill_observation")
        self.assertEqual(EventType.ORDER_REJECTION.value, "order_rejection")


class TestGovernedLinkageFields(unittest.TestCase):
    """Regression test: telemetry preserves governed linkage fields."""

    def test_capture_preserves_governed_linkage_fields(self):
        """Test that TelemetryCapture preserves registry, artifact, and lineage fields."""
        capture = TelemetryCapture()
        metadata = {
            'registry_id': 'reg-123',
            'artifact_type': 'strategy_spec',
            'artifact_version': '2.1.0',
            'promotion_state': 'paper',
            'lineage_ref': 'parent-456',
            'metrics': {'custom_metric': 42.0},
        }
        
        capture.capture_pnl(ExecutionMode.PAPER, 'test_strategy', 100.0, metadata=metadata)
        event = capture.get_paper_events()[0]
        
        # Verify all linkage fields in target
        self.assertEqual(event['target']['strategy_id'], 'test_strategy')
        self.assertEqual(event['target']['registry_id'], 'reg-123')
        self.assertEqual(event['target']['artifact_type'], 'strategy_spec')
        self.assertEqual(event['target']['artifact_version'], '2.1.0')
        self.assertEqual(event['target']['promotion_state'], 'paper')
        self.assertEqual(event['target']['lineage_ref'], 'parent-456')
        
        # Verify metrics preserved
        self.assertEqual(event['metrics']['pnl'], 100.0)
        self.assertEqual(event['metrics']['custom_metric'], 42.0)

    def test_fill_capture_with_full_linkage(self):
        """Test capture_fill preserves full governed linkage."""
        capture = TelemetryCapture()
        metadata = {
            'registry_id': 'reg-fill-123',
            'artifact_type': 'execution_bundle',
            'artifact_version': '1.0.0',
            'lineage_ref': 'parent-fill-456',
        }
        
        capture.capture_fill(
            ExecutionMode.LIVE,
            'algo_1',
            fill_quantity=500,
            fill_price=100.50,
            signal_id='sig-789',
            metadata=metadata
        )
        
        event = capture.get_live_events()[0]
        self.assertEqual(event['event_type'], 'fill_observation')
        self.assertEqual(event['target']['registry_id'], 'reg-fill-123')
        self.assertEqual(event['target']['artifact_version'], '1.0.0')
        self.assertEqual(event['metrics']['fill_quantity'], 500)
        self.assertEqual(event['metrics']['fill_price'], 100.50)

    def test_slippage_capture_with_governance_fields(self):
        """Test capture_slippage preserves governance linkage."""
        capture = TelemetryCapture()
        metadata = {
            'registry_id': 'reg-slip-123',
            'artifact_type': 'strategy_spec',
            'promotion_state': 'live',
            'lineage_ref': 'parent-slip-789',
        }
        
        capture.capture_slippage(
            ExecutionMode.LIVE,
            'strategy_x',
            slippage_bps=2.5,
            metadata=metadata
        )
        
        event = capture.get_live_events()[0]
        self.assertEqual(event['target']['promotion_state'], 'live')
        self.assertEqual(event['target']['lineage_ref'], 'parent-slip-789')
        self.assertEqual(event['metrics']['slippage_bps'], 2.5)

    def test_repeated_capture_with_same_metadata_preserves_linkage(self):
        """
        Regression test: repeated captures with same metadata dict should preserve linkage.
        
        This ensures that TelemetryCapture uses non-mutating copy semantics,
        so reusing the same metadata dict across multiple capture calls doesn't
        lose linkage fields due to pop() or other destructive operations.
        """
        capture = TelemetryCapture()
        metadata = {
            'registry_id': 'reg-repeat-123',
            'artifact_type': 'strategy_spec',
            'artifact_version': '1.0.0',
            'promotion_state': 'paper',
            'lineage_ref': 'parent-repeat-456',
            'metrics': {'slippage_bps': 1.2},
        }
        
        # Record initial metadata keys for verification
        initial_keys = set(metadata.keys())
        
        # Capture multiple events with same metadata
        capture.capture_pnl(ExecutionMode.PAPER, 's1', 10.0, metadata=metadata)
        capture.capture_drawdown(ExecutionMode.PAPER, 's1', 2.0, metadata=metadata)
        capture.capture_slippage(ExecutionMode.PAPER, 's1', 1.5, metadata=metadata)
        
        # Verify metadata dict was not mutated
        self.assertEqual(set(metadata.keys()), initial_keys,
                        "Metadata dict was mutated during captures")
        
        events = capture.get_paper_events()
        self.assertEqual(len(events), 3, "Expected 3 events")
        
        # Verify each event preserved full linkage
        linkage_fields = ['registry_id', 'artifact_version', 'artifact_type', 'promotion_state', 'lineage_ref']
        expected_linkage = {
            'registry_id': 'reg-repeat-123',
            'artifact_version': '1.0.0',
            'artifact_type': 'strategy_spec',
            'promotion_state': 'paper',
            'lineage_ref': 'parent-repeat-456',
        }
        
        for i, event in enumerate(events):
            target = event.get('target', {})
            for field in linkage_fields:
                self.assertIn(field, target,
                             f"Event {i} missing linkage field {field}")
                self.assertEqual(target[field], expected_linkage[field],
                               f"Event {i} has incorrect {field}")
        
        # Verify additional metrics were preserved too
        self.assertEqual(events[2]['metrics'].get('slippage_bps'), 1.2,
                        "Metadata metrics not merged into event")


if __name__ == "__main__":
    unittest.main()
