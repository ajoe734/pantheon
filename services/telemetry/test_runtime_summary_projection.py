"""Tests for telemetry-owned runtime status projection."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore


def _event(event_type: str = "heartbeat", *, created_at: str = "2026-05-01T00:00:00Z"):
    return {
        "event_id": f"evt-{event_type}",
        "event_type": event_type,
        "created_at": created_at,
        "deployment_stage": "paper",
        "binding_id": "rtb-paper-001",
        "runtime_id": "rt-paper-001",
        "capital_pool_id": "pool-paper-001",
        "artifact_id": "artifact-paper-001",
        "artifact_version": "1.0.0",
        "plan_id": "plan-paper-001",
        "persona_capital_binding_id": "pcb-paper-001",
        "target": {"strategy_id": "strategy-paper-001"},
        "metrics": {"heartbeat": 1} if event_type == "heartbeat" else {"action": event_type},
        "metadata": {
            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
            "engine_bridge_path": "pantheon/lean",
            "engine_bridge_commit": "abc1234",
            "runtime_adapter_version": "0.1.0",
        },
    }


def _runtime_heartbeat_event():
    event = _event(created_at="2026-05-01T00:00:05Z")
    event["metrics"].update({"queue_lag_ms": 3, "event_delivery_lag_ms": 8})
    event["metadata"].update(
        {
            "source_type": "runtime_heartbeat",
            "runtime_heartbeat": {
                "connectivity_status": "connected",
                "broker_status": "ok",
                "queue_lag_ms": 3,
                "event_delivery_lag_ms": 8,
                "health_summary": {"runtime": "ok"},
            },
            "connectivity_status": "connected",
            "broker_status": "ok",
        }
    )
    return event


class RuntimeSummaryProjectionStoreTest(unittest.TestCase):
    def test_heartbeat_updates_runtime_summary_identity_and_bridge(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        summary = store.project_event(_event())

        self.assertIsNotNone(summary)
        self.assertEqual(summary["runtime_id"], "rt-paper-001")
        self.assertEqual(summary["runtime_binding_id"], "rtb-paper-001")
        self.assertEqual(summary["deployment_stage"], "paper")
        self.assertEqual(summary["last_heartbeat_at"], "2026-05-01T00:00:00Z")
        self.assertEqual(summary["state"], "active")
        self.assertEqual(summary["engine_bridge_repo"], "ajoe734/pantheon-lean.git")
        self.assertEqual(summary["engine_bridge_commit"], "abc1234")
        self.assertEqual(summary["health_summary"]["telemetry"], "ok")

    def test_runtime_heartbeat_status_fields_are_projected(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        summary = store.project_event(_runtime_heartbeat_event())

        self.assertEqual(summary["last_heartbeat_at"], "2026-05-01T00:00:05Z")
        self.assertEqual(summary["connectivity_status"], "connected")
        self.assertEqual(summary["broker_status"], "ok")
        self.assertEqual(summary["queue_lag_ms"], 3)
        self.assertEqual(summary["event_delivery_lag_ms"], 8)
        self.assertEqual(summary["reported_health_summary"], {"runtime": "ok"})
        self.assertEqual(summary["health_summary"]["broker"], "ok")

    def test_deploy_completed_sets_runtime_active_without_fabricating_heartbeat(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        summary = store.project_event(_event("deploy_completed"))

        self.assertEqual(summary["state"], "active")
        self.assertNotIn("last_heartbeat_at", summary)
        self.assertEqual(summary["health_summary"]["telemetry"], "degraded")

    def test_stale_heartbeat_returns_degraded_summary(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        store.project_event(_event(created_at="2026-05-01T00:00:00Z"))

        summary = store.get(
            "rt-paper-001",
            now=datetime(2026, 5, 1, 0, 2, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["state"], "degraded")
        self.assertEqual(summary["health_summary"]["telemetry"], "degraded")
        self.assertEqual(summary["staleness"]["threshold_seconds"], 60)

    def test_projection_persists_as_bff_readable_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "runtime_summaries.json"
            store = RuntimeSummaryProjectionStore(path, heartbeat_stale_after_seconds=60)
            store.project_event(_event())

            reloaded = RuntimeSummaryProjectionStore(path, heartbeat_stale_after_seconds=60)

            self.assertEqual(
                reloaded.get("rt-paper-001")["runtime_binding_id"],
                "rtb-paper-001",
            )


if __name__ == "__main__":
    unittest.main()
