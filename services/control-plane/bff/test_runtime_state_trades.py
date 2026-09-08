"""Tests for surfacing executed paper trades in the operator runtime-state DTO."""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(__file__))
import main as bff_main
from management_read_models.service import ManagementService

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
        original_read_store = bff_main.read_store
        original_context_service = bff_main._management_ai_context_service
        store = MagicMock()
        store.get_telemetry_summary.return_value = dict(_SUMMARY)
        store.get_paper_runtime_monitoring_session.return_value = None
        store.get_rollbacks.return_value = []
        bff_main.read_store = store
        bff_main._management_ai_context_service = ManagementService(read_store=store)
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
            bff_main.read_store = original_read_store
            bff_main._management_ai_context_service = original_context_service
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


class TestRealTradingPulseHelperPreservesTelemetryOwnerFailure(unittest.TestCase):
    """MGMT-READ-001 sixth review: exercises the real
    _mgmt_nl_trading_pulse_snippet/_mgmt_nl_scoped_runtime_rows/
    _project_operator_runtime_state_row helper chain (not stubbed) so a
    telemetry owner that explicitly reports itself unavailable is never
    masked by a healthy runtime binding, whether or not the underlying
    telemetry read raises."""

    def _run(self, raises: bool):
        binding = dict(
            runtime_id="r1",
            tenant_id="tenant-a",
            owner="runtime-owner",
            status="ok",
            source_kind="live",
            source_version="rv1",
        )
        telemetry = dict(
            runtime_id="r1",
            owner="telemetry-owner",
            status="unavailable",
            source_kind="unavailable",
            source_version="tv1",
            correlation_id="telemetry-correlation",
            observed_at=None,
            degradation_reason="telemetry owner offline",
        )

        def read_telemetry(_runtime_id):
            if raises:
                raise RuntimeError("telemetry owner offline")
            return telemetry

        from types import SimpleNamespace

        store = SimpleNamespace(
            list_runtime_bindings=lambda: [binding],
            get_telemetry_summary=read_telemetry,
            get_paper_runtime_monitoring_session=lambda **kwargs: None,
            get_rollbacks=lambda _runtime_id: [],
        )
        original_read_store = bff_main.read_store
        original_context_service = bff_main._management_ai_context_service
        bff_main.read_store = store
        bff_main._management_ai_context_service = ManagementService(read_store=store)
        try:
            return bff_main._mgmt_nl_collect_context(
                "trading_pulse", "2026-09-08T18:00:00Z", "tenant-a"
            )
        finally:
            bff_main.read_store = original_read_store
            bff_main._management_ai_context_service = original_context_service

    def test_non_raising_failed_telemetry_owner_is_preserved(self):
        import json

        result = self._run(raises=False)
        surface = result["surfaces"]["management_trading_pulse"]
        self.assertNotEqual(surface["status"], "ok", surface)
        encoded = json.dumps(surface)
        self.assertIn("runtime-owner", encoded, surface)
        self.assertIn("telemetry owner offline", encoded, surface)
        self.assertIn("telemetry-correlation", encoded, surface)

    def test_raising_telemetry_read_does_not_discard_runtime_observation(self):
        import json

        result = self._run(raises=True)
        surface = result["surfaces"]["management_trading_pulse"]
        self.assertNotEqual(surface["status"], "ok", surface)
        encoded = json.dumps(surface)
        self.assertIn("runtime-owner", encoded, surface)
        self.assertIn("telemetry owner offline", encoded, surface)


if __name__ == "__main__":
    unittest.main()
