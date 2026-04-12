"""Tests for APP-002-W5-SSE-LIVE: SSE transports and reconciliation."""

import unittest
from services.frontend.sse_reconciler import SseReconciler


class TestSseReconciler(unittest.TestCase):
    """Test SSE reconciliation logic."""

    def test_apply_runtime_state_changed(self):
        """Runtime state changes merge into runtimes dict."""
        r = SseReconciler()
        r.apply_event({
            "id": "evt-1",
            "type": "runtime_state_changed",
            "timestamp": "2026-04-12T00:00:00Z",
            "data": {"runtime_id": "rt-001", "current_state": "paper"},
        })
        self.assertEqual(r.last_seen_event_id, "evt-1")
        self.assertEqual(r.state["runtimes"]["rt-001"]["current_state"], "paper")

    def test_apply_runtime_state_update(self):
        """Subsequent events update existing runtime state."""
        r = SseReconciler()
        r.apply_event({
            "id": "evt-1",
            "type": "runtime_state_changed",
            "data": {"runtime_id": "rt-001", "current_state": "paper"},
        })
        r.apply_event({
            "id": "evt-2",
            "type": "runtime_state_changed",
            "data": {"runtime_id": "rt-001", "current_state": "canary"},
        })
        self.assertEqual(r.state["runtimes"]["rt-001"]["current_state"], "canary")
        self.assertEqual(r.last_seen_event_id, "evt-2")

    def test_idempotent_skip(self):
        """Re-applying the same event_id is a no-op."""
        r = SseReconciler()
        r.apply_event({
            "id": "evt-1",
            "type": "runtime_state_changed",
            "data": {"runtime_id": "rt-001", "current_state": "paper"},
        })
        state_before = dict(r.state)
        result = r.apply_event({
            "id": "evt-1",
            "type": "runtime_state_changed",
            "data": {"runtime_id": "rt-001", "current_state": "live"},
        })
        self.assertEqual(r.state, state_before)
        self.assertEqual(r.last_seen_event_id, "evt-1")

    def test_incident_lifecycle(self):
        """Incident created then updated."""
        r = SseReconciler()
        r.apply_event({
            "id": "evt-1",
            "type": "incident_created",
            "data": {"incident_id": "inc-001", "severity": "high"},
        })
        self.assertEqual(r.state["incidents"]["inc-001"]["status"], "open")
        r.apply_event({
            "id": "evt-2",
            "type": "incident_updated",
            "data": {"incident_id": "inc-001", "status": "resolved"},
        })
        self.assertEqual(r.state["incidents"]["inc-001"]["status"], "resolved")

    def test_kill_switch_activation_deactivation(self):
        """Kill switch toggles update state correctly."""
        r = SseReconciler()
        r.apply_event({
            "id": "evt-1",
            "type": "kill_switch_activated",
            "data": {"scope": "all", "reason": "emergency"},
        })
        self.assertTrue(r.state["kill_switch"]["active"])
        r.apply_event({
            "id": "evt-2",
            "type": "kill_switch_deactivated",
            "data": {"scope": "all"},
        })
        self.assertFalse(r.state["kill_switch"]["active"])

    def test_batch_replay(self):
        """Batch apply replays events in order."""
        r = SseReconciler()
        events = [
            {"id": "evt-1", "type": "runtime_state_changed", "data": {"runtime_id": "rt-001", "current_state": "paper"}},
            {"id": "evt-2", "type": "runtime_state_changed", "data": {"runtime_id": "rt-002", "current_state": "live"}},
            {"id": "evt-3", "type": "kill_switch_activated", "data": {"scope": "pool-1"}},
        ]
        r.apply_batch(events)
        self.assertEqual(r.last_seen_event_id, "evt-3")
        self.assertEqual(len(r.state["runtimes"]), 2)
        self.assertTrue(r.state["kill_switch"]["active"])

    def test_reconnect_params(self):
        """Reconnect params include last seen event ID."""
        r = SseReconciler()
        self.assertEqual(r.reconnect_params, {})
        r.apply_event({"id": "evt-5", "type": "unknown", "data": {}})
        self.assertEqual(r.reconnect_params, {"last_event_id": "evt-5"})

    def test_event_log_bounded(self):
        """Event log is bounded to prevent memory growth."""
        r = SseReconciler(max_log=3)
        for i in range(10):
            r.apply_event({"id": f"evt-{i}", "type": "unknown", "data": {}})
        self.assertEqual(len(r._event_log), 3)
        self.assertEqual(r._event_log[0]["event_id"], "evt-7")

    def test_default_handler_merges_unknown_events(self):
        """Unknown event types merge data into root state."""
        r = SseReconciler()
        r.apply_event({
            "id": "evt-1",
            "type": "custom_event",
            "data": {"custom_field": "value"},
        })
        self.assertEqual(r.state.get("custom_field"), "value")


if __name__ == "__main__":
    unittest.main()
