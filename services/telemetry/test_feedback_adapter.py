"""
Unit tests for feedback store adapter.
"""

import unittest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from feedback_adapter import FeedbackStoreAdapter
from capture import ExecutionMode


class TestFeedbackStoreAdapter(unittest.TestCase):
    """Test FeedbackStoreAdapter class."""

    def setUp(self):
        """Create test instance."""
        self.adapter = FeedbackStoreAdapter()
        
        # Sample telemetry events
        self.sample_event = {
            "event_id": "evt_001",
            "event_type": "pnl_snapshot",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "execution_mode": "paper",
            "target": {
                "strategy_id": "strat_1",
            },
            "metrics": {"pnl": 100.0}
        }

    def test_ingest_telemetry_event(self):
        """Test ingesting telemetry event."""
        result = self.adapter.ingest_telemetry_event(
            event=self.sample_event,
            strategy_id="strat_1"
        )
        
        # Verify no schema-violating fields added
        self.assertNotIn("ingestion_timestamp", result)
        self.assertNotIn("ingestion_source", result)
        
        # Verify event is in buffer
        self.assertEqual(len(self.adapter.telemetry_log), 1)
        
        # Verify strategy_id is set
        self.assertEqual(result["target"]["strategy_id"], "strat_1")

    def test_ingest_with_promotion_state(self):
        """Test ingesting event with promotion state."""
        result = self.adapter.ingest_telemetry_event(
            event=self.sample_event,
            strategy_id="strat_1",
            promotion_state="live"
        )
        
        self.assertEqual(result["target"]["promotion_state"], "live")

    def test_get_telemetry_for_strategy(self):
        """Test retrieving telemetry for strategy."""
        event1 = self.sample_event.copy()
        event1["event_id"] = "evt_001"
        event1["target"]["strategy_id"] = "strat_1"
        
        event2 = self.sample_event.copy()
        event2["event_id"] = "evt_002"
        event2["target"]["strategy_id"] = "strat_2"
        
        self.adapter.ingest_telemetry_event(event1, "strat_1")
        self.adapter.ingest_telemetry_event(event2, "strat_2")
        
        strat1_events = self.adapter.get_telemetry_for_strategy("strat_1")
        self.assertEqual(len(strat1_events), 1)
        self.assertEqual(strat1_events[0]["target"]["strategy_id"], "strat_1")
        
        strat2_events = self.adapter.get_telemetry_for_strategy("strat_2")
        self.assertEqual(len(strat2_events), 1)
        self.assertEqual(strat2_events[0]["target"]["strategy_id"], "strat_2")

    def test_get_telemetry_by_mode(self):
        """Test retrieving telemetry by execution mode."""
        event1 = self.sample_event.copy()
        event1["event_id"] = "evt_001"
        event1["execution_mode"] = "paper"
        event1["target"]["strategy_id"] = "strat_1"
        
        event2 = self.sample_event.copy()
        event2["event_id"] = "evt_002"
        event2["execution_mode"] = "live"
        event2["target"]["strategy_id"] = "strat_1"
        
        self.adapter.ingest_telemetry_event(event1, "strat_1")
        self.adapter.ingest_telemetry_event(event2, "strat_1")
        
        paper_events = self.adapter.get_telemetry_for_strategy("strat_1", mode="paper")
        self.assertEqual(len(paper_events), 1)
        self.assertEqual(paper_events[0]["execution_mode"], "paper")
        
        live_events = self.adapter.get_telemetry_for_strategy("strat_1", mode="live")
        self.assertEqual(len(live_events), 1)
        self.assertEqual(live_events[0]["execution_mode"], "live")

    def test_get_telemetry_by_promotion_state(self):
        """Test retrieving telemetry by promotion state."""
        event1 = self.sample_event.copy()
        event1["event_id"] = "evt_001"
        
        event2 = self.sample_event.copy()
        event2["event_id"] = "evt_002"
        
        self.adapter.ingest_telemetry_event(event1, "strat_1", promotion_state="candidate")
        self.adapter.ingest_telemetry_event(event2, "strat_1", promotion_state="live")
        
        candidate_events = self.adapter.get_telemetry_by_promotion_state("candidate")
        self.assertEqual(len(candidate_events), 1)
        self.assertEqual(candidate_events[0]["target"]["promotion_state"], "candidate")
        
        live_events = self.adapter.get_telemetry_by_promotion_state("live")
        self.assertEqual(len(live_events), 1)
        self.assertEqual(live_events[0]["target"]["promotion_state"], "live")

    def test_correlate_with_feedback_within_window(self):
        """Test correlating telemetry with feedback within time window."""
        now = datetime.now(timezone.utc)
        
        # Telemetry event at time T
        telemetry = self.sample_event.copy()
        telemetry["event_id"] = "evt_001"
        telemetry["created_at"] = now.isoformat().replace("+00:00", "Z")
        telemetry["target"]["strategy_id"] = "strat_1"
        
        # Feedback event 2 hours after telemetry
        feedback = {
            "event_id": "fbk_001",
            "event_type": "approve",
            "created_at": (now + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            "target": {"strategy_id": "strat_1"}
        }
        
        correlation = self.adapter.correlate_with_feedback(telemetry, [feedback])
        
        self.assertEqual(len(correlation["correlated_feedback"]), 1)
        self.assertEqual(correlation["correlated_feedback"][0]["feedback_id"], "fbk_001")

    def test_correlate_with_feedback_outside_window(self):
        """Test that feedback outside time window is not correlated."""
        now = datetime.now(timezone.utc)
        
        telemetry = self.sample_event.copy()
        telemetry["event_id"] = "evt_001"
        telemetry["created_at"] = now.isoformat().replace("+00:00", "Z")
        telemetry["target"]["strategy_id"] = "strat_1"
        
        # Feedback event 30 hours after telemetry (outside 24h window)
        feedback = {
            "event_id": "fbk_001",
            "event_type": "approve",
            "created_at": (now + timedelta(hours=30)).isoformat().replace("+00:00", "Z"),
            "target": {"strategy_id": "strat_1"}
        }
        
        correlation = self.adapter.correlate_with_feedback(telemetry, [feedback])
        
        self.assertEqual(len(correlation["correlated_feedback"]), 0)

    def test_correlate_with_feedback_different_strategy(self):
        """Test that feedback for different strategy is not correlated."""
        now = datetime.now(timezone.utc)
        
        telemetry = self.sample_event.copy()
        telemetry["event_id"] = "evt_001"
        telemetry["created_at"] = now.isoformat().replace("+00:00", "Z")
        telemetry["target"]["strategy_id"] = "strat_1"
        
        # Feedback for different strategy
        feedback = {
            "event_id": "fbk_001",
            "event_type": "approve",
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "target": {"strategy_id": "strat_2"}
        }
        
        correlation = self.adapter.correlate_with_feedback(telemetry, [feedback])
        
        self.assertEqual(len(correlation["correlated_feedback"]), 0)

    def test_export_telemetry_jsonl(self):
        """Test exporting telemetry to JSONL format."""
        temp_dir = tempfile.TemporaryDirectory()
        
        event1 = self.sample_event.copy()
        event1["event_id"] = "evt_001"
        event2 = self.sample_event.copy()
        event2["event_id"] = "evt_002"
        
        self.adapter.ingest_telemetry_event(event1, "strat_1")
        self.adapter.ingest_telemetry_event(event2, "strat_1")
        
        output_path = Path(temp_dir.name) / "telemetry.jsonl"
        self.adapter.export_telemetry(str(output_path), format="jsonl")
        
        self.assertTrue(output_path.exists())
        
        lines = output_path.read_text().strip().split("\n")
        self.assertEqual(len(lines), 2)
        
        for line in lines:
            data = json.loads(line)
            self.assertIn("event_id", data)
        
        temp_dir.cleanup()

    def test_export_telemetry_json(self):
        """Test exporting telemetry to JSON format."""
        temp_dir = tempfile.TemporaryDirectory()
        
        event1 = self.sample_event.copy()
        event1["event_id"] = "evt_001"
        
        self.adapter.ingest_telemetry_event(event1, "strat_1")
        
        output_path = Path(temp_dir.name) / "telemetry.json"
        self.adapter.export_telemetry(str(output_path), format="json")
        
        self.assertTrue(output_path.exists())
        
        data = json.loads(output_path.read_text())
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        
        temp_dir.cleanup()

    def test_clear_log(self):
        """Test clearing telemetry log."""
        event = self.sample_event.copy()
        event["event_id"] = "evt_001"
        
        self.adapter.ingest_telemetry_event(event, "strat_1")
        self.assertEqual(len(self.adapter.telemetry_log), 1)
        
        self.adapter.clear_log()
        self.assertEqual(len(self.adapter.telemetry_log), 0)

    def test_idempotent_append_with_feedback_store(self):
        """
        Regression test: duplicate event_ids should not be repeated in store.
        
        When FeedbackStoreAdapter uses TraderFeedbackStore.append(), it should
        detect duplicate event_ids and prevent them from being appended twice.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_path))
            
            # Create event with linkage
            event = {
                "event_id": "evt_idempotent_001",
                "event_type": "pnl_snapshot",
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "execution_mode": "paper",
                "target": {
                    "strategy_id": "strat_1",
                    "registry_id": "reg-123",
                    "artifact_version": "1.0.0",
                    "promotion_state": "paper",
                },
                "metrics": {"pnl": 100.0}
            }
            
            # Ingest first time
            result1 = adapter.ingest_telemetry_event(event, "strat_1")
            self.assertEqual(result1["event_id"], "evt_idempotent_001")
            
            # Try to ingest same event again
            result2 = adapter.ingest_telemetry_event(event, "strat_1")
            self.assertEqual(result2["event_id"], "evt_idempotent_001")
            
            # Check file has only one line (duplicate prevented)
            if store_path.exists():
                lines = store_path.read_text().strip().split('\n')
                self.assertEqual(len(lines), 1, 
                               "Store should contain only 1 line for duplicate event_id")
                
                # Verify linkage was preserved
                stored_event = json.loads(lines[0])
                self.assertEqual(stored_event["target"]["registry_id"], "reg-123")
                self.assertEqual(stored_event["target"]["artifact_version"], "1.0.0")


class TestAdapterSchemaCompliance(unittest.TestCase):
    """Regression test: adapter maintains schema compliance without extra fields."""

    def setUp(self):
        """Load canonical schema."""
        schema_path = Path(__file__).parent.parent.parent / "services" / "feedback" / "schema" / "execution_telemetry_event.schema.json"
        with open(schema_path) as f:
            self.schema = json.load(f)
    
    def test_ingested_event_passes_canonical_schema(self):
        """Test that adapter output validates against canonical schema."""
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-123",
            "event_type": "pnl_snapshot",
            "created_at": "2026-04-07T00:00:00Z",
            "execution_mode": "paper",
            "target": {"strategy_id": "test_strat"},
            "metrics": {"pnl": 100.0},
        }
        
        enriched = adapter.ingest_telemetry_event(event, "test_strat", promotion_state="paper")
        
        # Should not raise ValidationError
        try:
            jsonschema.validate(enriched, self.schema)
        except jsonschema.ValidationError as e:
            self.fail(f"Schema validation failed: {e.message}")

    def test_adapter_no_extra_fields(self):
        """Test that adapter doesn't add schema-violating extra fields."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-456",
            "event_type": "fill_observation",
            "created_at": "2026-04-07T00:00:00Z",
            "execution_mode": "live",
            "target": {"strategy_id": "algo_1"},
            "metrics": {"fill_quantity": 100, "fill_price": 50.25},
        }
        
        enriched = adapter.ingest_telemetry_event(event, "algo_1", promotion_state="live")
        
        # Check for schema-violating fields
        allowed_properties = set(self.schema.get("properties", {}).keys())
        actual_properties = set(enriched.keys())
        extra_fields = actual_properties - allowed_properties
        
        self.assertEqual(len(extra_fields), 0, f"Extra schema-violating fields: {extra_fields}")

    def test_adapter_persists_to_store_file(self):
        """Test that adapter actually writes to feedback store file."""
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_file))
            event = {
                "event_id": "evt-789",
                "event_type": "drawdown_snapshot",
                "created_at": "2026-04-07T00:00:00Z",
                "execution_mode": "paper",
                "target": {"strategy_id": "draw_strat"},
                "metrics": {"drawdown_pct": 5.0},
            }
            
            adapter.ingest_telemetry_event(event, "draw_strat", promotion_state="paper")
            
            # Verify file was created with event
            self.assertTrue(store_file.exists())
            
            # Verify file content
            with open(store_file) as f:
                stored_event = json.loads(f.readline())
            
            self.assertEqual(stored_event["event_id"], "evt-789")
            self.assertEqual(stored_event["target"]["promotion_state"], "paper")

    def test_adapter_preserves_governed_linkage_in_target(self):
        """Test that adapter preserves governed linkage fields in target object."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-link-123",
            "event_type": "pnl_snapshot",
            "created_at": "2026-04-07T00:00:00Z",
            "execution_mode": "live",
            "target": {
                "strategy_id": "gov_strat",
                "registry_id": "reg-456",
                "artifact_type": "strategy_spec",
                "artifact_version": "3.0.0",
                "lineage_ref": "parent-789",
            },
            "metrics": {"pnl": 500.0},
        }
        
        enriched = adapter.ingest_telemetry_event(event, "gov_strat", promotion_state="live")
        
        # All linkage fields should be preserved
        self.assertEqual(enriched["target"]["registry_id"], "reg-456")
        self.assertEqual(enriched["target"]["artifact_type"], "strategy_spec")
        self.assertEqual(enriched["target"]["artifact_version"], "3.0.0")
        self.assertEqual(enriched["target"]["lineage_ref"], "parent-789")
        self.assertEqual(enriched["target"]["promotion_state"], "live")

    def test_mixed_event_families_get_telemetry_for_strategy(self):
        """
        Regression test: Verify that shared store queries for telemetry
        do not mix feedback event families.
        
        When shared store contains both telemetry events and feedback events
        with the same strategy_id, get_telemetry_for_strategy() must return
        only telemetry events and exclude approve/edit/reject/rationale.
        """
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_file))
            
            telemetry_event = {
                "event_id": "evt-t1",
                "event_type": "pnl_snapshot",
                "created_at": "2026-04-07T00:00:00Z",
                "execution_mode": "paper",
                "target": {"strategy_id": "strat-1", "promotion_state": "paper"},
                "metrics": {"pnl": 1.0},
            }
            
            feedback_event = {
                "event_id": "fb-1",
                "event_type": "approve",
                "created_at": "2026-04-07T00:05:00Z",
                "actor_id": "u1",
                "actor_role": "approver",
                "channel": "console",
                "target": {"strategy_id": "strat-1", "promotion_state": "paper"},
            }
            
            # Persist telemetry first
            adapter.ingest_telemetry_event(telemetry_event, "strat-1", "paper")
            
            # Manually add feedback event to store (simulating it being added elsewhere)
            adapter.feedback_store.append(feedback_event)
            
            # Query telemetry for strategy - must not include feedback event
            results = adapter.get_telemetry_for_strategy("strat-1")
            
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["event_type"], "pnl_snapshot")
            self.assertEqual(results[0]["event_id"], "evt-t1")
            
            # Verify no approve events leak in
            event_types = [e.get("event_type") for e in results]
            self.assertNotIn("approve", event_types)

    def test_mixed_event_families_get_telemetry_by_promotion_state(self):
        """
        Regression test: Verify that shared store queries by promotion_state
        do not mix feedback event families.
        
        When shared store contains both telemetry and feedback events with
        the same promotion_state, get_telemetry_by_promotion_state() must
        return only telemetry events.
        """
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_file))
            
            telemetry_event = {
                "event_id": "evt-t2",
                "event_type": "drawdown_snapshot",
                "created_at": "2026-04-07T00:00:00Z",
                "execution_mode": "paper",
                "target": {"strategy_id": "strat-2", "promotion_state": "paper"},
                "metrics": {"drawdown_pct": 2.5},
            }
            
            feedback_events = [
                {
                    "event_id": "fb-2",
                    "event_type": "edit",
                    "created_at": "2026-04-07T00:05:00Z",
                    "actor_id": "u1",
                    "actor_role": "approver",
                    "channel": "console",
                    "target": {"strategy_id": "strat-2", "promotion_state": "paper"},
                },
                {
                    "event_id": "fb-3",
                    "event_type": "rationale",
                    "created_at": "2026-04-07T00:10:00Z",
                    "actor_id": "u2",
                    "actor_role": "operator",
                    "channel": "web",
                    "target": {"strategy_id": "strat-2", "promotion_state": "paper"},
                },
            ]
            
            # Persist telemetry
            adapter.ingest_telemetry_event(telemetry_event, "strat-2", "paper")
            
            # Manually add feedback events to store
            for fb in feedback_events:
                adapter.feedback_store.append(fb)
            
            # Query by promotion state - must not include feedback events
            results = adapter.get_telemetry_by_promotion_state("paper")
            
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["event_type"], "drawdown_snapshot")
            
            event_types = [e.get("event_type") for e in results]
            self.assertNotIn("edit", event_types)
            self.assertNotIn("rationale", event_types)

    def test_mixed_event_families_query_telemetry(self):
        """
        Regression test: Verify that shared store query_telemetry()
        does not mix feedback event families.
        
        When shared store contains both telemetry and feedback events,
        query_telemetry() with any combination of filters must return
        only telemetry events (pnl_snapshot, drawdown_snapshot, slippage_observation,
        fill_observation, order_rejection) and exclude all feedback events.
        """
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_file))
            
            # Create multiple telemetry events
            telemetry_events = [
                {
                    "event_id": "evt-t3",
                    "event_type": "pnl_snapshot",
                    "created_at": "2026-04-07T10:00:00Z",
                    "execution_mode": "live",
                    "target": {"strategy_id": "strat-3", "promotion_state": "live", "registry_id": "reg-123"},
                    "metrics": {"pnl": 10.0},
                },
                {
                    "event_id": "evt-t4",
                    "event_type": "slippage_observation",
                    "created_at": "2026-04-07T10:05:00Z",
                    "execution_mode": "live",
                    "target": {"strategy_id": "strat-3", "promotion_state": "live", "registry_id": "reg-123"},
                    "metrics": {"slippage_bps": 2.5},
                },
            ]
            
            # Create multiple feedback events with same linkage
            feedback_events = [
                {
                    "event_id": "fb-4",
                    "event_type": "approve",
                    "created_at": "2026-04-07T09:00:00Z",
                    "actor_id": "u3",
                    "actor_role": "approver",
                    "channel": "console",
                    "target": {"strategy_id": "strat-3", "promotion_state": "live", "registry_id": "reg-123"},
                },
                {
                    "event_id": "fb-5",
                    "event_type": "reject",
                    "created_at": "2026-04-07T11:00:00Z",
                    "actor_id": "u4",
                    "actor_role": "operator",
                    "channel": "web",
                    "target": {"strategy_id": "strat-3", "promotion_state": "live", "registry_id": "reg-123"},
                },
            ]
            
            # Persist telemetry events
            for te in telemetry_events:
                adapter.ingest_telemetry_event(te, "strat-3", "live")
            
            # Add feedback events to store
            for fb in feedback_events:
                adapter.feedback_store.append(fb)
            
            # Test 1: Query by strategy_id alone
            results = adapter.query_telemetry(strategy_id="strat-3")
            self.assertEqual(len(results), 2)
            event_types = [e.get("event_type") for e in results]
            self.assertEqual(set(event_types), {"pnl_snapshot", "slippage_observation"})
            self.assertNotIn("approve", event_types)
            self.assertNotIn("reject", event_types)
            
            # Test 2: Query by registry_id alone
            results = adapter.query_telemetry(registry_id="reg-123")
            self.assertEqual(len(results), 2)
            event_types = [e.get("event_type") for e in results]
            self.assertNotIn("approve", event_types)
            
            # Test 3: Query by promotion_state alone
            results = adapter.query_telemetry(promotion_state="live")
            self.assertEqual(len(results), 2)
            event_types = [e.get("event_type") for e in results]
            self.assertNotIn("reject", event_types)
            
            # Test 4: Query with multiple filters
            results = adapter.query_telemetry(
                strategy_id="strat-3",
                registry_id="reg-123",
                promotion_state="live"
            )
            self.assertEqual(len(results), 2)
            event_types = [e.get("event_type") for e in results]
            for et in event_types:
                self.assertNotIn(et, {"approve", "edit", "reject", "rationale"})

    def test_limit_applied_after_family_filter_get_telemetry_for_strategy(self):
        """
        Regression test: Verify that limit is applied AFTER family filtering in get_telemetry_for_strategy.
        
        This is critical for shared-store semantics where feedback events may appear
        before telemetry events. The limit should be applied to telemetry events only,
        not consumed by feedback events in the linkage.
        
        See: services/telemetry/review_fb003_codex_zh.md - New Blocking Finding
        """
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_file))
            
            # Add 120 feedback events with same linkage (simulates pre-existing feedback)
            for i in range(120):
                feedback = {
                    "event_id": f"fb-{i}",
                    "event_type": "approve",
                    "created_at": f"2026-04-07T00:{i % 60:02d}:00Z",
                    "actor_id": "u1",
                    "actor_role": "approver",
                    "channel": "console",
                    "target": {"strategy_id": "strat-x", "promotion_state": "paper"},
                }
                adapter.feedback_store.append(feedback)
            
            # Add telemetry event after all feedback (appears later in store)
            adapter.ingest_telemetry_event(
                {
                    "event_id": "evt-1",
                    "event_type": "pnl_snapshot",
                    "created_at": "2026-04-07T02:00:00Z",
                    "execution_mode": "paper",
                    "target": {"strategy_id": "strat-x", "promotion_state": "paper"},
                    "metrics": {"pnl": 1.0},
                },
                "strat-x",
                "paper",
            )
            
            # Without the fix, this would return 0 because TraderFeedbackStore.list()
            # would consume the default limit of 100 with feedback events
            results = adapter.get_telemetry_for_strategy("strat-x")
            
            # With the fix, we should get the 1 telemetry event
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["event_type"], "pnl_snapshot")

    def test_limit_applied_after_family_filter_get_telemetry_by_promotion_state(self):
        """
        Regression test: Verify that limit is applied AFTER family filtering in get_telemetry_by_promotion_state.
        """
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_file))
            
            # Add 120 feedback events with "paper" promotion_state
            for i in range(120):
                feedback = {
                    "event_id": f"fb-ps-{i}",
                    "event_type": "edit",
                    "created_at": f"2026-04-07T00:{i % 60:02d}:00Z",
                    "actor_id": "u2",
                    "actor_role": "editor",
                    "channel": "api",
                    "target": {"strategy_id": "strat-y", "promotion_state": "paper"},
                }
                adapter.feedback_store.append(feedback)
            
            # Add telemetry event after all feedback
            adapter.ingest_telemetry_event(
                {
                    "event_id": "evt-ps-1",
                    "event_type": "drawdown_snapshot",
                    "created_at": "2026-04-07T02:00:00Z",
                    "execution_mode": "paper",
                    "target": {"strategy_id": "strat-y", "promotion_state": "paper"},
                    "metrics": {"drawdown": 0.05},
                },
                "strat-y",
                "paper",
            )
            
            # Should return 1 telemetry event, not 0
            results = adapter.get_telemetry_by_promotion_state("paper")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["event_type"], "drawdown_snapshot")

    def test_limit_applied_after_family_filter_query_telemetry(self):
        """
        Regression test: Verify that limit parameter is applied AFTER family filtering in query_telemetry.
        """
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_file))
            
            # Add 150 feedback events with same linkage (exceeds default limit of 100)
            for i in range(150):
                feedback = {
                    "event_id": f"fb-qt-{i}",
                    "event_type": "reject",
                    "created_at": f"2026-04-07T00:{i % 60:02d}:00Z",
                    "actor_id": "u3",
                    "actor_role": "reviewer",
                    "channel": "console",
                    "target": {"strategy_id": "strat-z", "promotion_state": "live", "registry_id": "reg-1"},
                }
                adapter.feedback_store.append(feedback)
            
            # Add 5 telemetry events after all feedback
            for j in range(5):
                adapter.ingest_telemetry_event(
                    {
                        "event_id": f"evt-qt-{j}",
                        "event_type": "slippage_observation",
                        "created_at": f"2026-04-07T02:{j:02d}:00Z",
                        "execution_mode": "live",
                        "target": {"strategy_id": "strat-z", "promotion_state": "live", "registry_id": "reg-1"},
                        "metrics": {"slippage_bps": 1.5},
                    },
                    "strat-z",
                    "live",
                )
            
            # Query with limit=3: should return 3 telemetry events, not 0
            results = adapter.query_telemetry(
                strategy_id="strat-z",
                promotion_state="live",
                limit=3
            )
            self.assertEqual(len(results), 3)
            for event in results:
                self.assertIn(event["event_type"], {"slippage_observation"})
                self.assertNotIn(event["event_type"], {"approve", "edit", "reject", "rationale"})

    def test_shared_store_limit_applied_after_family_filter_get_telemetry_for_strategy(self):
        """
        Regression test: verify that get_telemetry_for_strategy applies limit AFTER family filtering.
        
        This ensures that large numbers of feedback events in the shared store do not
        prevent telemetry events from being returned due to store-level limit being hit
        before telemetry family filtering occurs.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_path))
            
            # Add 120 feedback events FIRST (these will consume store iteration)
            for i in range(120):
                adapter.feedback_store.append({
                    "event_id": f"fb-{i}",
                    "event_type": "approve",
                    "created_at": f"2026-04-07T00:00:{(i % 60):02d}Z",
                    "actor_id": "user1",
                    "actor_role": "approver",
                    "channel": "console",
                    "target": {
                        "strategy_id": "strat-mixed",
                        "promotion_state": "candidate"
                    }
                })
            
            # Now add a single telemetry event AFTER all feedback events
            adapter.ingest_telemetry_event(
                event={
                    "event_id": "evt-mixed-1",
                    "event_type": "pnl_snapshot",
                    "created_at": "2026-04-07T02:00:00Z",
                    "execution_mode": "paper",
                    "target": {"strategy_id": "strat-mixed"},
                    "metrics": {"pnl": 1.0}
                },
                strategy_id="strat-mixed",
                promotion_state="candidate"
            )
            
            # Query should return the telemetry event, not be blocked by feedback events
            results = adapter.get_telemetry_for_strategy("strat-mixed")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["event_type"], "pnl_snapshot")
            self.assertNotIn(results[0]["event_type"], {"approve", "edit", "reject", "rationale"})

    def test_shared_store_limit_applied_after_family_filter_get_telemetry_by_promotion_state(self):
        """
        Regression test: verify that get_telemetry_by_promotion_state applies limit AFTER family filtering.
        
        This ensures that large numbers of feedback events in the shared store do not
        prevent telemetry events from being returned.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_path))
            
            # Add 150 feedback events
            for i in range(150):
                adapter.feedback_store.append({
                    "event_id": f"fb-promo-{i}",
                    "event_type": "reject",
                    "created_at": f"2026-04-07T01:{(i % 60):02d}:00Z",
                    "actor_id": "user1",
                    "actor_role": "approver",
                    "channel": "api",
                    "target": {
                        "strategy_id": "strat-many",
                        "promotion_state": "paper"
                    }
                })
            
            # Add 5 telemetry events for the same promotion state
            for j in range(5):
                adapter.ingest_telemetry_event(
                    event={
                        "event_id": f"evt-paper-{j}",
                        "event_type": "drawdown_snapshot",
                        "created_at": f"2026-04-07T03:{j:02d}:00Z",
                        "execution_mode": "paper",
                        "target": {"strategy_id": "strat-many"},
                        "metrics": {"drawdown_pct": 0.5 * j}
                    },
                    strategy_id="strat-many",
                    promotion_state="paper"
                )
            
            # Query by promotion state should return all 5 telemetry events,
            # not be blocked or truncated by the 150 feedback events
            results = adapter.get_telemetry_by_promotion_state("paper")
            self.assertEqual(len(results), 5)
            for event in results:
                self.assertEqual(event["event_type"], "drawdown_snapshot")
                self.assertNotIn(event["event_type"], {"approve", "edit", "reject", "rationale"})

    def test_shared_store_limit_applied_after_family_filter_query_telemetry(self):
        """
        Regression test: verify that query_telemetry applies limit AFTER family filtering.
        
        This ensures that the limit parameter constrains the telemetry results,
        not the raw matches in the shared store before family filtering.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_path))
            
            # Add 200 feedback events that match the linkage
            for i in range(200):
                adapter.feedback_store.append({
                    "event_id": f"fb-query-{i}",
                    "event_type": "edit",
                    "created_at": f"2026-04-07T02:{(i % 60):02d}:{(i // 60):02d}Z",
                    "actor_id": "user2",
                    "actor_role": "editor",
                    "channel": "console",
                    "target": {
                        "strategy_id": "strat-query",
                        "promotion_state": "live"
                    }
                })
            
            # Add 10 telemetry events
            for j in range(10):
                adapter.ingest_telemetry_event(
                    event={
                        "event_id": f"evt-query-{j}",
                        "event_type": "fill_observation",
                        "created_at": f"2026-04-07T04:{j:02d}:00Z",
                        "execution_mode": "live",
                        "target": {"strategy_id": "strat-query"},
                        "metrics": {"fill_count": j * 10}
                    },
                    strategy_id="strat-query",
                    promotion_state="live"
                )
            
            # Query with limit=3 should return 3 telemetry events,
            # not be constrained by the 200 feedback events before them
            results = adapter.query_telemetry(
                strategy_id="strat-query",
                promotion_state="live",
                limit=3
            )
            self.assertEqual(len(results), 3, 
                           "limit=3 should return exactly 3 results after family filtering, "
                           "not be pre-empted by 200 feedback events")
            for event in results:
                self.assertEqual(event["event_type"], "fill_observation")
                self.assertNotIn(event["event_type"], {"approve", "edit", "reject", "rationale"})


if __name__ == "__main__":
    unittest.main()
