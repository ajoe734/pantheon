#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

import run_broker_sandbox_order_smoke as smoke


class BrokerSandboxOrderSmokeTest(unittest.TestCase):
    def args(self, **overrides: object) -> argparse.Namespace:
        values = {
            "provider": "ibkr",
            "mode": "validate_only",
            "symbol": "AAPL.US",
            "side": "buy",
            "quantity": 1,
            "limit_price": 120.0,
            "replace_limit_price": 119.0,
            "account_ref": "DU1234567",
            "credential_ref": "secret://pantheon/ibkr-paper",
            "host": "ibkr-paper-gateway",
            "port": 7497,
            "client_id": 17,
            "output_dir": "/tmp/not-used",
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_ibkr_validate_only_packet_captures_required_evidence(self) -> None:
        payload = smoke.build_payload(self.args())

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["provider"], "IBKR")
        self.assertFalse(payload["production_live"]["order_side_effects_allowed"])
        self.assertEqual(payload["place"]["request"]["contract"]["symbol"], "AAPL")
        self.assertEqual(payload["place"]["request"]["order"]["account"], "DU1234567")
        self.assertEqual(payload["execution"]["fill_status"], "no_fill_validate_only")
        self.assertEqual(payload["telemetry"]["event"]["raw_secret_material_present"], False)
        self.assertEqual(payload["reconciliation"]["status"], "passed")

    def test_shioaji_simulation_packet_sets_simulation_flag(self) -> None:
        payload = smoke.build_payload(
            self.args(
                provider="shioaji",
                mode="simulation",
                symbol="2330.TW",
                account_ref="SIM-TW-001",
                credential_ref="secret://pantheon/shioaji-sim",
            )
        )

        self.assertEqual(payload["provider"], "Shioaji")
        self.assertTrue(payload["place"]["request"]["simulation"])
        self.assertEqual(payload["place"]["side_effect"], "simulation_only")

    def test_kraken_validate_only_packet_forces_validate_true(self) -> None:
        payload = smoke.build_payload(
            self.args(
                provider="kraken",
                mode="validate_only",
                symbol="BTC/USD.KRAKEN",
                account_ref="KRAKEN-TEST-001",
                credential_ref="secret://pantheon/kraken-test",
            )
        )

        self.assertEqual(payload["provider"], "Kraken")
        self.assertTrue(payload["place"]["request"]["validate"])
        self.assertEqual(payload["place"]["request"]["pair"], "BTC/USD")

    def test_live_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "production live"):
            smoke.build_payload(self.args(mode="live"))

    def test_raw_secret_like_reference_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "raw secret"):
            smoke.build_payload(self.args(credential_ref="api_key=abc123"))

    def test_credential_ref_must_use_known_reference_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "secret reference"):
            smoke.build_payload(self.args(credential_ref="ibkr-paper-secret-name"))

    def test_write_packet_creates_required_artifacts(self) -> None:
        payload = smoke.build_payload(self.args())
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            smoke.write_packet(output_dir, payload)
            expected = {
                "summary.json",
                "auth.json",
                "account-readiness.json",
                "place.request.json",
                "cancel-replace.request.json",
                "readback.json",
                "execution.json",
                "telemetry-event.json",
                "reconciliation.json",
            }
            self.assertEqual({path.name for path in output_dir.iterdir()}, expected)
            self.assertEqual(json.loads((output_dir / "summary.json").read_text())["task_id"], smoke.TASK_ID)


if __name__ == "__main__":
    unittest.main()
