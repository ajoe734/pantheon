"""Tests for surfacing executed paper trades in the operator runtime-state DTO."""
import os
import sys
import unittest
from unittest.mock import MagicMock

from services.control_plane.bff import main as bff_main

_SUMMARY = {
    "window": "latest",
    "collected_at": "2026-06-14T11:36:37Z",
    "pnl": 28.12,
    "total_trades": 1,
    "fill_rate": 1.0,
    "deployment_stage": "paper",
    "state": "active",
    "executed_trade_count": 1,
    "position_count": 1,
    "positions": [{"symbol": "AAPL", "quantity": 7.0}],
    "last_fill": {"symbol": "AAPL", "quantity": 7.0, "fill_price": 100.0, "action": "market_order"},
}


class TestRuntimeStateTradesProjection(unittest.TestCase):
    def test_telemetry_summary_projection_surfaces_trade_fields(self):
        proj = bff_main._project_runtime_state_telemetry_summary(_SUMMARY)
        self.assertEqual(proj["executed_trade_count"], 1)
        self.assertEqual(proj["position_count"], 1)
        self.assertEqual(proj["positions"], [{"symbol": "AAPL", "quantity": 7.0}])
        self.assertEqual(proj["last_fill"]["symbol"], "AAPL")
        self.assertEqual(proj["metrics"]["total_trades"], 1)
        self.assertEqual(proj["metrics"]["pnl"], 28.12)

    def test_runtime_state_row_surfaces_trades_at_top_level(self):
        original = bff_main.read_store
        store = MagicMock()
        store.get_telemetry_summary.return_value = dict(_SUMMARY)
        store.get_paper_runtime_monitoring_session.return_value = None
        store.get_rollbacks.return_value = []
        bff_main.read_store = store
        try:
            row = bff_main._project_operator_runtime_state_row(
                {
                    "runtime_id": "rt-paper-001",
                    "binding_id": "rb-paper-001",
                    "deployment_stage": "paper",
                    "status": "active",
                    "capital_pool_id": "pool-001",
                    "artifact_id": "artifact-001",
                    "artifact_version": "1.0.0",
                    "plan_id": "plan-001",
                }
            )
        finally:
            bff_main.read_store = original
        self.assertEqual(row["executed_trade_count"], 1)
        self.assertEqual(row["total_trades"], 1)
        self.assertEqual(row["position_count"], 1)
        self.assertEqual(row["positions"], [{"symbol": "AAPL", "quantity": 7.0}])
        self.assertEqual(row["last_fill"]["symbol"], "AAPL")

    def test_projection_handles_summary_without_trade_fields(self):
        proj = bff_main._project_runtime_state_telemetry_summary(
            {"window": "latest", "pnl": 0.0, "deployment_stage": "paper"}
        )
        self.assertNotIn("executed_trade_count", proj)
        self.assertEqual(proj["metrics"]["total_trades"], None)


if __name__ == "__main__":
    unittest.main()
