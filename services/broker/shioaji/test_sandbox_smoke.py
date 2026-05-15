"""Tests for the Shioaji broker sandbox smoke harness."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.broker.shioaji import sandbox_smoke


class ShioajiSandboxSmokeTest(unittest.TestCase):
    def args(self, **overrides: object) -> argparse.Namespace:
        values = {
            "symbol": "2890",
            "qty": 1.0,
            "side": "buy",
            "order_type": "limit",
            "limit_price": 18.0,
            "account_kind": "stock",
            "futures_category": None,
            "submit_spacing_seconds": 0.0,
            "cancel_delay_seconds": 0.0,
            "capital_pool_id": "pool-test",
            "strategy_id": "strategy-test",
            "mock_api": True,
            "output_dir": "/tmp/not-used",
            "output_file": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_mock_api_smoke_runs_place_cancel_readback_reconcile(self) -> None:
        with patch.dict(os.environ, {"BROKER_SHIOAJI_SANDBOX_ENABLED": "1"}, clear=False):
            payload = sandbox_smoke.run_smoke(self.args())

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["provider"], "Shioaji")
        self.assertEqual(payload["task_id"], "EP5-BROKER-TW-002-RERUN-REAL-FIX")
        self.assertEqual(payload["account_kind"], "stock")
        self.assertEqual(payload["run_mode"], "mock_api_replay")
        self.assertEqual(payload["place"]["response"]["status"], "submitted")
        self.assertEqual(payload["place"]["response"]["account_kind"], "stock")
        self.assertEqual(payload["cancel"]["response"]["status"], "cancelled")
        self.assertEqual(payload["readback"]["after_cancel"]["status"], "cancelled")
        self.assertEqual(payload["reconciliation"]["status"], "passed")
        self.assertEqual(payload["live_gate"]["response"]["error_code"], "SHIOAJI_LIVE_DISABLED")
        self.assertFalse(payload["no_real_capital"]["real_capital_used"])
        self.assertFalse(payload["no_real_capital"]["production_live_order_submitted"])
        self.assertTrue(payload["order_ids"]["pantheon_order_id"])
        self.assertEqual(payload["order_ids"]["shioaji_trade_id"], "mock-shioaji-trade-001")

    def test_cancel_delay_is_recorded_and_applied(self) -> None:
        with patch.dict(os.environ, {"BROKER_SHIOAJI_SANDBOX_ENABLED": "1"}, clear=False):
            with patch.object(sandbox_smoke.time, "sleep") as sleep:
                payload = sandbox_smoke.run_smoke(self.args(cancel_delay_seconds=1.25))

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["place_to_cancel_delay"]["configured_seconds"], 1.25)
        self.assertTrue(payload["place_to_cancel_delay"]["applied"])
        self.assertTrue(payload["place_to_cancel_delay"]["meets_minimum_seconds"])
        sleep.assert_called_once_with(1.25)

    def test_gate_closed_fails_before_sdk_place(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BROKER_SHIOAJI_SANDBOX_ENABLED", None)
            payload = sandbox_smoke.run_smoke(self.args())

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"]["error_code"], "SHIOAJI_SANDBOX_DISABLED")
        self.assertFalse(payload["environment"]["BROKER_SHIOAJI_SANDBOX_ENABLED"])

    def test_write_bundle_creates_readiness_artifacts(self) -> None:
        with patch.dict(os.environ, {"BROKER_SHIOAJI_SANDBOX_ENABLED": "true"}, clear=False):
            payload = sandbox_smoke.run_smoke(self.args())

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            sandbox_smoke.write_bundle(output_dir, payload)
            expected = {
                "summary.json",
                "place.request.json",
                "place.response.json",
                "readback.after_place.json",
                "cancel.request.json",
                "cancel.response.json",
                "readback.after_cancel.json",
                "live-disabled.json",
                "reconciliation.json",
                "no-real-capital-evidence.json",
            }
            self.assertEqual({path.name for path in output_dir.iterdir()}, expected)
            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["task_id"], "EP5-BROKER-TW-002-RERUN-REAL-FIX")
            self.assertEqual(summary["status_transitions"][-1]["status"], "cancelled")

    def test_output_file_writes_single_payload(self) -> None:
        with patch.dict(os.environ, {"BROKER_SHIOAJI_SANDBOX_ENABLED": "true"}, clear=False):
            payload = sandbox_smoke.run_smoke(self.args())

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "stock-smoke.json"
            sandbox_smoke.dump_json(output_file, payload)
            summary = json.loads(output_file.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "passed")
            self.assertEqual(summary["account_kind"], "stock")

    def test_parse_sdk_stdout_observations_redacts_raw_log(self) -> None:
        payload = sandbox_smoke.parse_sdk_stdout_observations(
            "Response Code: 0 | Event Code: 0 | Info: host x | Event: Session up\n"
            "Response Code: 200 | Event Code: 16 | Info: APISUB/V1/SYS/CONTRACT | Event: Subscribe or Unsubscribe ok\n"
            "OrderState.StockOrder {'operation': {'op_type': 'New', 'op_code': '88', 'op_msg': '價格超過漲跌幅範圍'}}\n"
        )

        self.assertTrue(payload["session_response_code_0_observed"])
        self.assertTrue(payload["session_up_observed"])
        self.assertTrue(payload["contract_subscription_observed"])
        self.assertFalse(payload["raw_stdout_persisted"])
        self.assertEqual(payload["order_state_events"][0]["operation_code"], "88")


if __name__ == "__main__":
    unittest.main()
