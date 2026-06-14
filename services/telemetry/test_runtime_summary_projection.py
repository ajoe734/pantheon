"""Tests for telemetry-owned runtime status projection."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore


def _event(event_type: str = "heartbeat", *, created_at: str = "2026-05-01T00:00:00Z", stage: str = "paper"):
    return {
        "event_id": f"evt-{stage}-{event_type}",
        "event_type": event_type,
        "created_at": created_at,
        "deployment_stage": stage,
        "binding_id": f"rtb-{stage}-001",
        "runtime_id": f"rt-{stage}-001",
        "capital_pool_id": f"pool-{stage}-001",
        "artifact_id": f"artifact-{stage}-001",
        "artifact_version": "1.0.0",
        "plan_id": f"plan-{stage}-001",
        "persona_capital_binding_id": f"pcb-{stage}-001",
        "target": {"strategy_id": f"strategy-{stage}-001"},
        "metrics": {"heartbeat": 1} if event_type == "heartbeat" else {"action": event_type},
        "metadata": {
            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
            "engine_bridge_path": "pantheon/lean",
            "engine_bridge_commit": "abc1234",
            "runtime_adapter_version": "0.1.0",
        },
    }


def _runtime_heartbeat_event(stage: str = "paper"):
    event = _event(created_at="2026-05-01T00:00:05Z", stage=stage)
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
        self.assertEqual(summary["health_summary"]["paper_runtime"], "ok")

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
        store.project_event(_event(created_at="2026-05-01T00:00:00Z", stage="paper"))

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

    def test_canary_event_projects_canary_runtime_summary(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        summary = store.project_event(_event(stage="canary"))

        self.assertIsNotNone(summary)
        self.assertEqual(summary["runtime_id"], "rt-canary-001")
        self.assertEqual(summary["runtime_binding_id"], "rtb-canary-001")
        self.assertEqual(summary["deployment_stage"], "canary")
        self.assertEqual(summary["state"], "active")
        self.assertEqual(summary["health_summary"]["canary_runtime"], "ok")
        self.assertEqual(summary["health_summary"]["telemetry"], "ok")
        self.assertNotIn("paper_runtime", summary["health_summary"])

    def test_live_event_projects_live_runtime_summary(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        summary = store.project_event(_event(stage="live"))

        self.assertIsNotNone(summary)
        self.assertEqual(summary["runtime_id"], "rt-live-001")
        self.assertEqual(summary["deployment_stage"], "live")
        self.assertEqual(summary["health_summary"]["live_runtime"], "ok")
        self.assertNotIn("canary_runtime", summary["health_summary"])
        self.assertNotIn("paper_runtime", summary["health_summary"])

    def test_frozen_event_projects_frozen_runtime_summary(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        summary = store.project_event(_event(stage="frozen"))

        self.assertIsNotNone(summary)
        self.assertEqual(summary["deployment_stage"], "frozen")
        self.assertEqual(summary["health_summary"]["frozen_runtime"], "ok")

    def test_unknown_stage_event_is_rejected(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        event = _event()
        event["deployment_stage"] = "simulation"
        result = store.project_event(event)

        self.assertIsNone(result)

    def test_multiple_stages_coexist_without_collision(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        fresh = datetime(2026, 5, 1, 0, 0, 30, tzinfo=timezone.utc)

        store.project_event(_event(stage="paper"))
        store.project_event(_event(stage="canary"))
        store.project_event(_event(stage="live"))

        summaries = store.list(now=fresh)
        self.assertEqual(len(summaries), 3)
        stages = {s["deployment_stage"] for s in summaries}
        self.assertEqual(stages, {"paper", "canary", "live"})

        paper = store.get("rt-paper-001", now=fresh)
        self.assertEqual(paper["health_summary"]["paper_runtime"], "ok")

        canary = store.get("rt-canary-001", now=fresh)
        self.assertEqual(canary["health_summary"]["canary_runtime"], "ok")

        live = store.get("rt-live-001", now=fresh)
        self.assertEqual(live["health_summary"]["live_runtime"], "ok")

    def test_canary_stale_heartbeat_degrades_canary_runtime_key(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        store.project_event(_event(created_at="2026-05-01T00:00:00Z", stage="canary"))

        summary = store.get(
            "rt-canary-001",
            now=datetime(2026, 5, 1, 0, 2, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["state"], "degraded")
        self.assertEqual(summary["health_summary"]["telemetry"], "degraded")
        self.assertEqual(summary["health_summary"]["canary_runtime"], "degraded")


if __name__ == "__main__":
    unittest.main()


def _fill_event(*, symbol="AAPL.US", qty=7.0, price=100.0, created_at="2026-05-01T00:01:00Z", stage="paper"):
    ev = _event(event_type="paper_fill_simulated", created_at=created_at, stage=stage)
    ev["event_id"] = f"evt-fill-{created_at}"
    ev["metrics"] = {
        "fill_quantity": qty,
        "fill_price": price,
        "action": "market_order",
        "submitted_to_broker": False,
    }
    ev["metadata"]["symbol"] = symbol
    ev["metadata"]["sim_fill_flag"] = True
    return ev


class TestFillProjection(unittest.TestCase):
    def _store(self):
        tmp = tempfile.mkdtemp()
        return RuntimeSummaryProjectionStore(path=str(Path(tmp) / "summaries.json"))

    def test_paper_fill_projects_trade_count_last_fill_and_positions(self):
        store = self._store()
        summary = store.project_event(_fill_event())
        self.assertEqual(summary["executed_trade_count"], 1)
        self.assertEqual(summary["total_trades"], 1)
        self.assertEqual(summary["last_fill"]["symbol"], "AAPL.US")
        self.assertEqual(summary["last_fill"]["quantity"], 7.0)
        self.assertEqual(summary["last_fill"]["fill_price"], 100.0)
        self.assertEqual(summary["position_count"], 1)
        self.assertEqual(summary["positions"], [{"symbol": "AAPL.US", "quantity": 7.0}])

    def test_multiple_fills_accumulate_count_and_positions(self):
        store = self._store()
        store.project_event(_fill_event(qty=7.0, created_at="2026-05-01T00:01:00Z"))
        store.project_event(_fill_event(qty=3.0, created_at="2026-05-01T00:02:00Z"))
        summary = store.project_event(_fill_event(symbol="MSFT.US", qty=5.0, created_at="2026-05-01T00:03:00Z"))
        self.assertEqual(summary["executed_trade_count"], 3)
        self.assertEqual(summary["total_trades"], 3)
        self.assertEqual(summary["last_fill"]["symbol"], "MSFT.US")
        positions = {p["symbol"]: p["quantity"] for p in summary["positions"]}
        self.assertEqual(positions, {"AAPL.US": 10.0, "MSFT.US": 5.0})

    def test_total_trades_metric_does_not_regress_below_executed_count(self):
        store = self._store()
        store.project_event(_fill_event())
        store.project_event(_fill_event(created_at="2026-05-01T00:02:00Z"))
        # a later heartbeat carrying a stale/zero total_trades metric must not lower it
        hb = _event(event_type="heartbeat", created_at="2026-05-01T00:03:00Z")
        hb["metrics"] = {"heartbeat": 1, "total_trades": 0}
        summary = store.project_event(hb)
        # a stale total_trades=0 metric must NOT wipe the executed fill count
        self.assertEqual(summary["executed_trade_count"], 2)
        self.assertEqual(summary["total_trades"], 2)
