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


class TestLineageReadModel(unittest.TestCase):
    """LIN-001 regression tests for telemetry-derived lineage normalization."""

    def test_order_cancel_position_events_remain_in_telemetry_family(self):
        """SD-09 lifecycle events must survive shared-store family filtering."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_file))

            adapter.feedback_store.append(
                {
                    "event_id": "feedback-ignore-1",
                    "event_type": "approve",
                    "target": {"strategy_id": "strat-exec"},
                    "created_at": "2026-04-10T00:00:00Z",
                }
            )
            for event_type in ("order_submitted", "order_canceled", "position_snapshot"):
                adapter.ingest_telemetry_event(
                    {
                        "event_id": f"evt-{event_type}",
                        "event_type": event_type,
                        "created_at": "2026-04-10T00:01:00Z",
                        "execution_mode": "live",
                        "binding_id": "rb-exec-001",
                        "runtime_id": "runtime-exec",
                        "capital_pool_id": "pool-exec",
                        "artifact_id": "artifact-exec",
                        "artifact_version": "1.0.0",
                        "deployment_stage": "canary",
                        "plan_id": "plan-exec",
                        "persona_capital_binding_id": "pcb-exec",
                        "trace_id": "trace-exec",
                        "order_id": "order-exec-001" if event_type != "position_snapshot" else None,
                        "position_qty": 0 if event_type == "position_snapshot" else None,
                        "target": {"strategy_id": "strat-exec", "registry_id": "reg-exec"},
                        "metrics": {"action": event_type},
                    },
                    "strat-exec",
                    "live",
                )

            records = adapter.query_lineage_records("runtime_binding", "rb-exec-001")

            self.assertEqual(
                [record["event_type"] for record in records],
                ["order_submitted", "order_canceled", "position_snapshot"],
            )
            self.assertNotIn("approve", {record["event_type"] for record in records})

    def test_order_rejection_lineage_preserves_adapter_response(self):
        """Broker adapter rejection details must remain queryable in lineage."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-order-rejection-shioaji-live-disabled",
            "event_type": "order_rejection",
            "created_at": "2026-06-12T15:00:00Z",
            "execution_mode": "sandbox",
            "binding_id": "rb-reject-001",
            "runtime_id": "runtime-reject",
            "capital_pool_id": "pool-reject",
            "artifact_id": "artifact-reject",
            "artifact_version": "1.0.0",
            "deployment_stage": "sandbox",
            "plan_id": "plan-reject",
            "persona_capital_binding_id": "pcb-reject",
            "trace_id": "trace-reject",
            "target": {
                "strategy_id": "strat-reject",
                "registry_id": "reg-reject",
                "promotion_state": "sandbox",
            },
            "metrics": {"rejected_order_count": 1, "submitted_to_broker": 0},
            "metadata": {
                "adapter": "shioaji_sandbox",
                "broker": "shioaji",
                "provider": "Shioaji",
                "order_id": "client-order-reject-001",
                "order_status": "rejected",
                "broker_submission_status": "rejected_before_broker",
                "submitted_to_broker": False,
                "adapter_response_status": "rejected",
                "adapter_error_code": "SHIOAJI_LIVE_DISABLED",
                "adapter_error_message": "Live broker execution is permanently disabled.",
                "adapter_status_code": 403,
                "reject_reason": "live route blocked by adapter",
                "requested_execution_mode": "live",
                "blocked_execution_mode": "live",
                "is_real_order": False,
                "is_real_capital": False,
                "deployment_stage": "sandbox",
                "production_live_enabled": False,
                "capital_binding_enabled": False,
                "human_gate_required": True,
                "proof_boundary": "management_sandbox_facade; not canary/live/capital proof",
            },
        }
        adapter.ingest_telemetry_event(event, "strat-reject", "sandbox")

        records = adapter.query_lineage_records("runtime_binding", "rb-reject-001")

        self.assertEqual(len(records), 1)
        order_context = records[0]["order_context"]
        self.assertEqual(order_context["adapter_error_code"], "SHIOAJI_LIVE_DISABLED")
        self.assertEqual(order_context["adapter_status_code"], 403)
        self.assertEqual(order_context["broker_submission_status"], "rejected_before_broker")
        self.assertFalse(order_context["submitted_to_broker"])
        self.assertFalse(order_context["production_live_enabled"])
        self.assertTrue(order_context["human_gate_required"])

    def test_order_partial_fill_lineage_preserves_fill_metrics(self):
        """Partial-fill quantities in metrics must be promoted into order lineage."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-order-partial-paper",
            "event_type": "order_partially_filled",
            "created_at": "2026-06-12T15:30:00Z",
            "execution_mode": "paper",
            "binding_id": "rb-partial-001",
            "runtime_id": "runtime-partial",
            "capital_pool_id": "pool-partial",
            "artifact_id": "artifact-partial",
            "artifact_version": "1.0.0",
            "deployment_stage": "paper",
            "plan_id": "plan-partial",
            "persona_capital_binding_id": "pcb-partial",
            "trace_id": "trace-partial",
            "target": {
                "strategy_id": "strat-partial",
                "registry_id": "reg-partial",
                "promotion_state": "paper",
            },
            "metrics": {
                "requested_quantity": 50.0,
                "fill_quantity": 20.0,
                "fill_price": 31.25,
                "remaining_quantity": 30.0,
                "partial_fill_ratio": 0.4,
                "fill_rate": 0.4,
                "avg_slippage_bps": 0.0,
            },
            "metadata": {
                "adapter": "openclaw_paper_broker",
                "broker": "paper_broker",
                "order_id": "paper-order-partial-001",
                "order_status": "partially_filled",
                "fill_status": "partially_filled",
                "submitted_to_broker": True,
                "is_real_order": False,
                "is_real_capital": False,
                "deployment_stage": "paper",
            },
        }
        adapter.ingest_telemetry_event(event, "strat-partial", "paper")

        records = adapter.query_lineage_records("runtime_binding", "rb-partial-001")

        self.assertEqual(len(records), 1)
        order_context = records[0]["order_context"]
        self.assertEqual(order_context["order_status"], "partially_filled")
        self.assertEqual(order_context["fill_status"], "partially_filled")
        self.assertEqual(order_context["fill_quantity"], 20.0)
        self.assertEqual(order_context["remaining_quantity"], 30.0)
        self.assertEqual(order_context["partial_fill_ratio"], 0.4)
        self.assertEqual(order_context["fill_rate"], 0.4)

    def test_pnl_snapshot_lineage_preserves_runtime_performance_metrics(self):
        """Runtime PnL snapshots must keep processed/fill/open-position counters."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-pnl-runtime-performance",
            "event_type": "pnl_snapshot",
            "created_at": "2026-06-12T15:40:00Z",
            "execution_mode": "paper",
            "binding_id": "rb-pnl-001",
            "runtime_id": "runtime-pnl",
            "capital_pool_id": "pool-pnl",
            "artifact_id": "artifact-pnl",
            "artifact_version": "1.0.0",
            "deployment_stage": "paper",
            "plan_id": "plan-pnl",
            "persona_capital_binding_id": "pcb-pnl",
            "trace_id": "trace-pnl",
            "target": {
                "strategy_id": "strat-pnl",
                "registry_id": "reg-pnl",
                "promotion_state": "paper",
            },
            "metrics": {
                "pnl": 0.0,
                "processed_signal_count": 1,
                "execution_event_count": 1,
                "fill_event_count": 1,
                "fill_rate": 1.0,
                "open_position_count": 1,
                "open_bracket_order_count": 0,
                "avg_slippage_bps": 0.0,
            },
            "metadata": {
                "runtime_package": "paper_execution_runtime",
                "submitted_to_broker": False,
                "is_real_order": False,
                "is_real_capital": False,
            },
        }
        adapter.ingest_telemetry_event(event, "strat-pnl", "paper")

        records = adapter.query_lineage_records("runtime_binding", "rb-pnl-001")

        self.assertEqual(len(records), 1)
        order_context = records[0]["order_context"]
        self.assertEqual(order_context["processed_signal_count"], 1)
        self.assertEqual(order_context["execution_event_count"], 1)
        self.assertEqual(order_context["fill_event_count"], 1)
        self.assertEqual(order_context["fill_rate"], 1.0)
        self.assertEqual(order_context["open_position_count"], 1)
        self.assertEqual(order_context["open_bracket_order_count"], 0)
        self.assertEqual(order_context["pnl"], 0.0)

    def test_bracket_order_lineage_preserves_child_order_submission(self):
        """Submitted bracket child order details must remain queryable after recovery."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-bracket-submitted-paper",
            "event_type": "bracket_order_logged",
            "created_at": "2026-06-12T15:42:00Z",
            "execution_mode": "paper",
            "binding_id": "rb-bracket-001",
            "runtime_id": "runtime-bracket",
            "capital_pool_id": "pool-bracket",
            "artifact_id": "artifact-bracket",
            "artifact_version": "1.0.0",
            "deployment_stage": "paper",
            "plan_id": "plan-bracket",
            "persona_capital_binding_id": "pcb-bracket",
            "trace_id": "trace-bracket",
            "target": {
                "strategy_id": "strat-bracket",
                "registry_id": "reg-bracket",
                "promotion_state": "paper",
            },
            "metrics": {
                "action": "bracket_submitted_to_broker",
                "submitted_to_broker": True,
            },
            "metadata": {
                "signal_id": "quant-breakout-msft-bracket-040",
                "alpha_source": "pure_quant_breakout_model",
                "stop_loss_pct": 0.03,
                "take_profit_pct": 0.06,
                "entry_price": 300.0,
                "entry_quantity": 5.0,
                "legs": [
                    {
                        "leg_type": "stop_loss",
                        "order_type": "STOP_MARKET",
                        "quantity": -5.0,
                        "stop_price": 291.0,
                    },
                    {
                        "leg_type": "take_profit",
                        "order_type": "LIMIT",
                        "quantity": -5.0,
                        "limit_price": 318.0,
                    },
                ],
                "submission": {
                    "bracket_order_id": "bracket-msft-040",
                    "leg_count": 2,
                    "legs": [
                        {
                            "bracket_order_id": "bracket-msft-040",
                            "leg_id": "bracket-msft-040-1",
                            "leg_type": "stop_loss",
                            "order_type": "STOP_MARKET",
                            "quantity": -5.0,
                            "stop_price": 291.0,
                            "status": "open",
                        },
                        {
                            "bracket_order_id": "bracket-msft-040",
                            "leg_id": "bracket-msft-040-2",
                            "leg_type": "take_profit",
                            "order_type": "LIMIT",
                            "quantity": -5.0,
                            "limit_price": 318.0,
                            "status": "open",
                        },
                    ],
                },
                "guard_stage": "paper",
                "guard_reason": "paper/sim bracket execution guard passed",
                "broker_submission_status": "submitted_to_broker",
                "submitted_to_broker": True,
                "is_real_order": False,
                "is_real_capital": False,
            },
        }
        adapter.ingest_telemetry_event(event, "strat-bracket", "paper")

        records = adapter.query_lineage_records("runtime_binding", "rb-bracket-001")

        self.assertEqual(len(records), 1)
        order_context = records[0]["order_context"]
        self.assertEqual(order_context["bracket_order_id"], "bracket-msft-040")
        self.assertEqual(order_context["bracket_leg_count"], 2)
        self.assertEqual(order_context["stop_loss_pct"], 0.03)
        self.assertEqual(order_context["take_profit_pct"], 0.06)
        self.assertEqual(order_context["entry_price"], 300.0)
        self.assertEqual(order_context["entry_quantity"], 5.0)
        self.assertEqual(order_context["guard_stage"], "paper")
        self.assertTrue(order_context["submitted_to_broker"])
        self.assertEqual(order_context["submitted_legs"][0]["leg_id"], "bracket-msft-040-1")
        self.assertEqual(order_context["submitted_legs"][1]["limit_price"], 318.0)

    def test_bracket_logged_only_lineage_preserves_non_entry_reason(self):
        """Logged-only bracket feedback must keep the exact non-entry reason."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-bracket-close-logged-only",
            "event_type": "bracket_order_logged",
            "created_at": "2026-06-12T15:43:00Z",
            "execution_mode": "paper",
            "binding_id": "rb-bracket-close-001",
            "runtime_id": "runtime-bracket-close",
            "capital_pool_id": "pool-bracket-close",
            "artifact_id": "artifact-bracket-close",
            "artifact_version": "1.0.0",
            "deployment_stage": "paper",
            "plan_id": "plan-bracket-close",
            "persona_capital_binding_id": "pcb-bracket-close",
            "trace_id": "trace-bracket-close",
            "target": {
                "strategy_id": "strat-bracket-close",
                "registry_id": "reg-bracket-close",
                "promotion_state": "paper",
            },
            "metrics": {
                "action": "bracket_logged_only",
                "submitted_to_broker": False,
            },
            "metadata": {
                "signal_id": "quant-close-risk-043",
                "alpha_source": "pure_quant_close_with_risk",
                "stop_loss_pct": 0.02,
                "take_profit_pct": 0.05,
                "guard_stage": "paper",
                "guard_reason": "paper/sim bracket execution guard passed",
                "reason": "not_entry_signal",
                "broker_submission_status": "logged_only",
                "submitted_to_broker": False,
                "is_real_order": False,
                "is_real_capital": False,
            },
        }
        adapter.ingest_telemetry_event(event, "strat-bracket-close", "paper")

        records = adapter.query_lineage_records("runtime_binding", "rb-bracket-close-001")

        self.assertEqual(len(records), 1)
        order_context = records[0]["order_context"]
        self.assertEqual(order_context["broker_submission_status"], "logged_only")
        self.assertFalse(order_context["submitted_to_broker"])
        self.assertEqual(order_context["guard_stage"], "paper")
        self.assertEqual(order_context["guard_reason"], "paper/sim bracket execution guard passed")
        self.assertEqual(order_context["reason"], "not_entry_signal")

    def test_order_cancel_lineage_preserves_cancel_ack(self):
        """Cancel acknowledgements must keep reason, actor, and unfilled quantity."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-order-cancel-paper",
            "event_type": "order_canceled",
            "created_at": "2026-06-12T15:45:00Z",
            "execution_mode": "paper",
            "binding_id": "rb-cancel-001",
            "runtime_id": "runtime-cancel",
            "capital_pool_id": "pool-cancel",
            "artifact_id": "artifact-cancel",
            "artifact_version": "1.0.0",
            "deployment_stage": "paper",
            "plan_id": "plan-cancel",
            "persona_capital_binding_id": "pcb-cancel",
            "trace_id": "trace-cancel",
            "target": {
                "strategy_id": "strat-cancel",
                "registry_id": "reg-cancel",
                "promotion_state": "paper",
            },
            "metrics": {
                "requested_quantity": 12.0,
                "unfilled_quantity": 12.0,
                "cancelled_quantity": 12.0,
                "cancel_latency_ms": 42.0,
                "fill_rate": 0.0,
            },
            "metadata": {
                "adapter": "openclaw_paper_broker",
                "broker": "paper_broker",
                "order_id": "paper-order-cancel-001",
                "order_status": "canceled",
                "cancel_status": "acknowledged",
                "cancel_reason": "price_guard_invalidated",
                "cancel_requested_by": "operator-risk",
                "cancel_request_id": "cancel-request-001",
                "submitted_to_broker": True,
                "is_real_order": False,
                "is_real_capital": False,
                "deployment_stage": "paper",
            },
        }
        adapter.ingest_telemetry_event(event, "strat-cancel", "paper")

        records = adapter.query_lineage_records("runtime_binding", "rb-cancel-001")

        self.assertEqual(len(records), 1)
        order_context = records[0]["order_context"]
        self.assertEqual(order_context["cancel_status"], "acknowledged")
        self.assertEqual(order_context["cancel_reason"], "price_guard_invalidated")
        self.assertEqual(order_context["cancel_requested_by"], "operator-risk")
        self.assertEqual(order_context["unfilled_quantity"], 12.0)
        self.assertEqual(order_context["cancelled_quantity"], 12.0)
        self.assertEqual(order_context["cancel_latency_ms"], 42.0)

    def test_validate_only_venue_order_lineage_preserves_adapter_contract(self):
        """Venue-specific validate-only order context should be queryable."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-kraken-validate-only",
            "event_type": "order_accepted",
            "created_at": "2026-06-12T16:00:00Z",
            "execution_mode": "paper",
            "binding_id": "rb-kraken-001",
            "runtime_id": "runtime-kraken",
            "capital_pool_id": "pool-kraken",
            "artifact_id": "artifact-kraken",
            "artifact_version": "1.0.0",
            "deployment_stage": "paper",
            "plan_id": "plan-kraken",
            "persona_capital_binding_id": "pcb-kraken",
            "trace_id": "trace-kraken",
            "target": {
                "strategy_id": "strat-kraken",
                "registry_id": "reg-kraken",
                "promotion_state": "paper",
            },
            "metrics": {"requested_quantity": 0.75, "fill_rate": 0.0, "total_trades": 0},
            "metadata": {
                "adapter": "kraken_execution_boundary",
                "broker": "kraken",
                "provider": "Kraken",
                "client_order_id": "client-kraken-001",
                "venue": "KRAKEN",
                "pair": "ETH/USDT",
                "base_asset": "ETH",
                "quote_asset": "USDT",
                "order_type": "limit",
                "side": "buy",
                "price": 3500.5,
                "volume": "0.75",
                "validate_only": True,
                "validation_status": "accepted",
                "order_status": "accepted",
                "submitted_to_broker": False,
                "is_real_order": False,
                "is_real_capital": False,
                "deployment_stage": "paper",
            },
        }
        adapter.ingest_telemetry_event(event, "strat-kraken", "paper")

        records = adapter.query_lineage_records("runtime_binding", "rb-kraken-001")

        self.assertEqual(len(records), 1)
        order_context = records[0]["order_context"]
        self.assertEqual(order_context["venue"], "KRAKEN")
        self.assertEqual(order_context["pair"], "ETH/USDT")
        self.assertEqual(order_context["base_asset"], "ETH")
        self.assertEqual(order_context["quote_asset"], "USDT")
        self.assertTrue(order_context["validate_only"])
        self.assertEqual(order_context["validation_status"], "accepted")
        self.assertFalse(order_context["submitted_to_broker"])

    def test_ibkr_validate_order_lineage_preserves_contract_fields(self):
        """IBKR validate-only orders must keep contract and routing fields."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-ibkr-validate-only",
            "event_type": "order_accepted",
            "created_at": "2026-06-12T16:15:00Z",
            "execution_mode": "paper",
            "binding_id": "rb-ibkr-001",
            "runtime_id": "runtime-ibkr",
            "capital_pool_id": "pool-ibkr",
            "artifact_id": "artifact-ibkr",
            "artifact_version": "1.0.0",
            "deployment_stage": "paper",
            "plan_id": "plan-ibkr",
            "persona_capital_binding_id": "pcb-ibkr",
            "trace_id": "trace-ibkr",
            "target": {
                "strategy_id": "strat-ibkr",
                "registry_id": "reg-ibkr",
                "promotion_state": "paper",
            },
            "metrics": {"requested_quantity": 5.0, "fill_rate": 0.0, "total_trades": 0},
            "metadata": {
                "adapter": "ibkr_execution_boundary",
                "broker": "ibkr",
                "provider": "IBKR",
                "client_order_id": "client-ibkr-001",
                "contract_symbol": "AAPL",
                "exchange": "SMART",
                "primary_exchange": "NASDAQ",
                "sec_type": "STK",
                "currency": "USD",
                "order_type": "LMT",
                "side": "BUY",
                "price": 212.4,
                "tif": "DAY",
                "outside_rth": False,
                "account": "DU1234567",
                "readonly_market_data": True,
                "market_data_type": 3,
                "validate_only": True,
                "validation_status": "accepted",
                "order_status": "accepted",
                "submitted_to_broker": False,
                "is_real_order": False,
                "is_real_capital": False,
                "deployment_stage": "paper",
            },
        }
        adapter.ingest_telemetry_event(event, "strat-ibkr", "paper")

        records = adapter.query_lineage_records("runtime_binding", "rb-ibkr-001")

        self.assertEqual(len(records), 1)
        order_context = records[0]["order_context"]
        self.assertEqual(order_context["contract_symbol"], "AAPL")
        self.assertEqual(order_context["exchange"], "SMART")
        self.assertEqual(order_context["primary_exchange"], "NASDAQ")
        self.assertEqual(order_context["sec_type"], "STK")
        self.assertEqual(order_context["currency"], "USD")
        self.assertEqual(order_context["account"], "DU1234567")
        self.assertEqual(order_context["tif"], "DAY")
        self.assertFalse(order_context["outside_rth"])
        self.assertTrue(order_context["readonly_market_data"])

    def test_order_rejection_lineage_preserves_sizing_math(self):
        """Zero-share rejection context must keep requested and computed quantity."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-zero-share-rejection",
            "event_type": "order_rejection",
            "created_at": "2026-06-12T16:30:00Z",
            "execution_mode": "paper",
            "binding_id": "rb-zero-001",
            "runtime_id": "runtime-zero",
            "capital_pool_id": "pool-zero",
            "artifact_id": "artifact-zero",
            "artifact_version": "1.0.0",
            "deployment_stage": "paper",
            "plan_id": "plan-zero",
            "persona_capital_binding_id": "pcb-zero",
            "trace_id": "trace-zero",
            "target": {
                "strategy_id": "strat-zero",
                "registry_id": "reg-zero",
                "promotion_state": "paper",
            },
            "metrics": {
                "requested_quantity": 10.0,
                "computed_quantity": 0.0,
                "fill_quantity": 0.0,
                "fill_rate": 0.0,
            },
            "metadata": {
                "adapter": "lean_paper_runtime",
                "broker": "lean_paper",
                "order_status": "rejected",
                "reject_reason": "cash_value_resolved_to_zero_shares",
                "quantity_type": "CASH_VALUE",
                "price": 800.0,
                "market_price": 800.0,
                "broker_submission_status": "rejected_before_broker",
                "submitted_to_broker": False,
                "is_real_order": False,
                "is_real_capital": False,
                "deployment_stage": "paper",
            },
        }
        adapter.ingest_telemetry_event(event, "strat-zero", "paper")

        records = adapter.query_lineage_records("runtime_binding", "rb-zero-001")

        self.assertEqual(len(records), 1)
        order_context = records[0]["order_context"]
        self.assertEqual(order_context["reject_reason"], "cash_value_resolved_to_zero_shares")
        self.assertEqual(order_context["quantity_type"], "CASH_VALUE")
        self.assertEqual(order_context["requested_quantity"], 10.0)
        self.assertEqual(order_context["computed_quantity"], 0.0)
        self.assertEqual(order_context["market_price"], 800.0)
        self.assertEqual(order_context["fill_rate"], 0.0)
        self.assertFalse(order_context["submitted_to_broker"])

    def test_paper_order_simulated_recovery_preserves_noop_context(self):
        """HOLD no-op decisions must recover from the feedback store with order context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "feedback_store.jsonl"
            writer = FeedbackStoreAdapter(feedback_store_path=str(store_file))
            event = {
                "event_id": "evt-hold-noop-001",
                "event_type": "paper_order_simulated",
                "created_at": "2026-06-12T17:45:00Z",
                "execution_mode": "paper",
                "binding_id": "rb-hold-001",
                "runtime_id": "runtime-hold",
                "capital_pool_id": "pool-hold",
                "artifact_id": "artifact-hold",
                "artifact_version": "1.0.0",
                "deployment_stage": "paper",
                "plan_id": "plan-hold",
                "persona_capital_binding_id": "pcb-hold",
                "trace_id": "trace-hold",
                "target": {
                    "strategy_id": "strat-hold",
                    "registry_id": "reg-hold",
                    "promotion_state": "paper",
                },
                "metrics": {
                    "noop_count": 1,
                    "requested_quantity": 0.0,
                    "fill_quantity": 0.0,
                    "fill_rate": 0.0,
                },
                "metadata": {
                    "signal_id": "llm-hold-msft-riskoff-020",
                    "alpha_source": "llm_riskoff_agent",
                    "model_id": "gpt-risk-paper",
                    "noop_reason": "hold_signal",
                    "decision_status": "no_order",
                    "order_status": "not_submitted",
                    "signal_action": "HOLD",
                    "signal_direction": "LONG",
                    "quantity_type": "SHARES",
                    "price": 420.0,
                    "broker_submission_status": "not_submitted_signal_noop",
                    "submitted_to_broker": False,
                    "is_real_order": False,
                    "is_real_capital": False,
                },
            }
            stored = writer.ingest_telemetry_event(event, "strat-hold", "paper")
            recovered = FeedbackStoreAdapter(feedback_store_path=str(store_file))

            events = recovered.query_telemetry(
                strategy_id="strat-hold",
                event_type="paper_order_simulated",
                promotion_state="paper",
                limit=3,
            )
            self.assertEqual([item["event_id"] for item in events], [stored["event_id"]])

            payload = recovered.build_learn_feedback_writeback_payload(
                events[0],
                sponsor_persona_id="persona-hold-sponsor",
                contributing_persona_ids=["persona-hold-ops"],
                summary="LLM HOLD signal was processed as a no-order paper decision.",
            )
            evidence = payload["runtime_telemetry_evidence"][0]
            lineage = evidence["lineage"]
            self.assertEqual(lineage["alpha_context"]["signal_id"], "llm-hold-msft-riskoff-020")
            self.assertEqual(lineage["alpha_context"]["alpha_source"], "llm_riskoff_agent")
            order_context = lineage["order_context"]
            self.assertEqual(order_context["noop_reason"], "hold_signal")
            self.assertEqual(order_context["decision_status"], "no_order")
            self.assertEqual(order_context["order_status"], "not_submitted")
            self.assertEqual(order_context["signal_action"], "HOLD")
            self.assertEqual(order_context["signal_direction"], "LONG")
            self.assertEqual(order_context["fill_rate"], 0.0)
            self.assertEqual(order_context["noop_count"], 1)
            self.assertFalse(order_context["submitted_to_broker"])

    def test_paper_order_simulated_exit_no_position_preserves_position_context(self):
        """EXIT no-position no-ops must keep position context for Learn feedback."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-exit-empty-noop-001",
            "event_type": "paper_order_simulated",
            "created_at": "2026-06-12T18:15:00Z",
            "execution_mode": "paper",
            "binding_id": "rb-exit-empty-001",
            "runtime_id": "runtime-exit-empty",
            "capital_pool_id": "pool-exit-empty",
            "artifact_id": "artifact-exit-empty",
            "artifact_version": "1.0.0",
            "deployment_stage": "paper",
            "plan_id": "plan-exit-empty",
            "persona_capital_binding_id": "pcb-exit-empty",
            "target": {
                "strategy_id": "strat-exit-empty",
                "registry_id": "reg-exit-empty",
                "promotion_state": "paper",
            },
            "metrics": {
                "noop_count": 1,
                "requested_quantity": 0.0,
                "computed_quantity": 0.0,
                "fill_quantity": 0.0,
                "fill_rate": 0.0,
            },
            "metadata": {
                "signal_id": "quant-exit-adbe-empty-021",
                "alpha_source": "quant_drawdown_exit",
                "noop_reason": "exit_long_without_position",
                "decision_status": "no_order",
                "order_status": "not_submitted",
                "quantity_type": "SHARES",
                "position_quantity": 0.0,
                "exit_direction": "LONG",
                "price": 600.0,
                "broker_submission_status": "not_submitted_signal_noop",
                "submitted_to_broker": False,
                "is_real_order": False,
                "is_real_capital": False,
            },
        }
        adapter.ingest_telemetry_event(event, "strat-exit-empty", "paper")

        records = adapter.query_lineage_records("runtime_binding", "rb-exit-empty-001")

        self.assertEqual(len(records), 1)
        order_context = records[0]["order_context"]
        self.assertEqual(order_context["noop_reason"], "exit_long_without_position")
        self.assertEqual(order_context["computed_quantity"], 0.0)
        self.assertEqual(order_context["position_quantity"], 0.0)
        self.assertEqual(order_context["exit_direction"], "LONG")
        self.assertEqual(order_context["broker_submission_status"], "not_submitted_signal_noop")
        self.assertFalse(order_context["submitted_to_broker"])

    def test_query_lineage_records_normalizes_semantic_refs(self):
        """Telemetry raw fields must normalize to semantic read-model fields."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-lineage-1",
            "event_type": "deploy_completed",
            "created_at": "2026-04-10T01:02:03Z",
            "execution_mode": "live",
            "binding_id": "rb-001",
            "runtime_id": "runtime-001",
            "capital_pool_id": "pool-001",
            "artifact_id": "artifact-001",
            "artifact_version": "1.2.3",
            "deployment_stage": "canary",
            "plan_id": "plan-001",
            "persona_capital_binding_id": "pcb-001",
            "trace_id": "trace-001",
            "target": {
                "strategy_id": "strat-lineage",
                "registry_id": "reg-001",
                "promotion_state": "live",
                "lineage_ref": "approval-001",
            },
            "metrics": {"action": "deploy_completed"},
        }
        adapter.ingest_telemetry_event(event, "strat-lineage", "live")

        records = adapter.query_lineage_records("runtime_binding", "rb-001")
        self.assertEqual(len(records), 1)

        record = records[0]
        self.assertTrue(record["derived_only"])
        self.assertEqual(record["runtime_binding_id"], "rb-001")
        self.assertEqual(record["deployment_plan_id"], "plan-001")
        self.assertEqual(record["persona_capital_binding_id"], "pcb-001")
        self.assertEqual(record["deployment_stage"], "canary")
        self.assertEqual(record["artifact_ref"], "artifact-001@1.2.3")
        self.assertEqual(record["lineage_ref"], "approval-001")
        self.assertEqual(record["conflict_markers"], [])

    def test_query_lineage_records_accepts_alias_fields_from_shared_store(self):
        """Legacy/semantic alias fields should still normalize into one read record."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = Path(tmpdir) / "feedback_store.jsonl"
            adapter = FeedbackStoreAdapter(feedback_store_path=str(store_file))

            adapter.feedback_store.append(
                {
                    "event_id": "evt-lineage-legacy",
                    "event_type": "heartbeat",
                    "created_at": "2026-04-10T02:00:00Z",
                    "execution_mode": "live",
                    "runtime_binding_id": "rb-legacy-001",
                    "deployment_plan_id": "plan-legacy-001",
                    "runtime_id": "runtime-legacy",
                    "capital_pool_id": "pool-legacy",
                    "persona_capital_binding_id": "pcb-legacy-001",
                    "artifact_id": "artifact-legacy",
                    "artifact_version": "9.9.9",
                    "environment": "live",
                    "trace_id": "trace-legacy",
                    "target": {
                        "strategy_id": "strat-legacy",
                        "registry_id": "reg-legacy",
                        "promotion_state": "live",
                    },
                    "metrics": {"heartbeat": 1},
                }
            )

            records = adapter.query_lineage_records("runtime_binding", "rb-legacy-001")
            self.assertEqual(len(records), 1)

            record = records[0]
            self.assertEqual(record["runtime_binding_id"], "rb-legacy-001")
            self.assertEqual(record["deployment_plan_id"], "plan-legacy-001")
            self.assertEqual(record["deployment_stage"], "live")
            self.assertEqual(record["artifact_ref"], "artifact-legacy@9.9.9")

    def test_build_lineage_summary_reports_alias_conflicts(self):
        """Derived summaries must surface alias drift instead of hiding it."""
        adapter = FeedbackStoreAdapter()
        event = {
            "event_id": "evt-lineage-conflict",
            "event_type": "rollback_completed",
            "created_at": "2026-04-10T03:00:00Z",
            "execution_mode": "live",
            "binding_id": "rb-raw-001",
            "runtime_binding_id": "rb-semantic-001",
            "runtime_id": "runtime-conflict",
            "capital_pool_id": "pool-conflict",
            "artifact_id": "artifact-conflict",
            "artifact_version": "1.0.0",
            "deployment_stage": "live",
            "environment": "paper",
            "plan_id": "plan-raw-001",
            "deployment_plan_id": "plan-semantic-001",
            "persona_capital_binding_id": "pcb-conflict-001",
            "target": {
                "strategy_id": "strat-conflict",
                "registry_id": "reg-conflict",
                "promotion_state": "live",
                "artifact_version": "2.0.0",
            },
            "metrics": {"action": "rollback_completed"},
        }
        adapter.ingest_telemetry_event(event, "strat-conflict", "live")

        summary = adapter.build_lineage_summary("runtime_binding", "rb-semantic-001")
        self.assertTrue(summary["derived_only"])
        self.assertEqual(summary["record_count"], 1)
        self.assertEqual(summary["refs"]["runtime_binding_ids"], ["rb-semantic-001"])
        self.assertEqual(summary["refs"]["deployment_plan_ids"], ["plan-semantic-001"])
        self.assertEqual(summary["event_type_counts"], {"rollback_completed": 1})

        conflict_codes = {marker["code"] for marker in summary["conflict_markers"]}
        self.assertIn("runtime_binding_alias_mismatch", conflict_codes)
        self.assertIn("deployment_plan_alias_mismatch", conflict_codes)
        self.assertIn("deployment_stage_alias_mismatch", conflict_codes)
        self.assertIn("artifact_version_target_mismatch", conflict_codes)


if __name__ == "__main__":
    unittest.main()
