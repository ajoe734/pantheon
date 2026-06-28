"""Tests for the Shioaji sandbox evidence packet builder."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import argparse
from pathlib import Path
from unittest.mock import patch

from services.broker.sinopac import evidence_packet, sandbox_smoke


GENERATED_AT = "2026-05-15T16:10:00Z"


class ShioajiEvidencePacketTest(unittest.TestCase):
    def smoke_payload(self) -> dict[str, object]:
        args = argparse.Namespace(
            symbol="2890",
            qty=1.0,
            side="buy",
            order_type="limit",
            limit_price=18.0,
            account_kind="stock",
            futures_category=None,
            submit_spacing_seconds=0.0,
            cancel_delay_seconds=0.0,
            capital_pool_id="pool-test",
            strategy_id="strategy-test",
            mock_api=True,
            output_dir="/tmp/not-used",
            output_file=None,
        )
        with patch.dict(os.environ, {"BROKER_SHIOAJI_SANDBOX_ENABLED": "1"}, clear=False):
            return sandbox_smoke.run_smoke(args)

    def test_build_packet_wraps_sandbox_smoke_without_opening_live_or_capital(self) -> None:
        packet = evidence_packet.build_evidence_packet(
            smoke_summary=self.smoke_payload(),
            smoke_summary_path="support/evidence/MGMT-BROKER-003/summary.json",
            task_packet_path="support/evidence/MGMT-BROKER-004/shioaji-sandbox-evidence-packet.json",
            generated_at=GENERATED_AT,
        )

        self.assertEqual(packet["schema_version"], "shioaji_sandbox_evidence_packet.v1")
        self.assertEqual(packet["task_id"], "MGMT-BROKER-004")
        self.assertEqual(packet["milestone_id"], "MGMT-OODA-M3")
        self.assertEqual(packet["status"], "passed")
        self.assertEqual(packet["broker"], "shioaji")
        self.assertEqual(packet["environment"], "sandbox")
        self.assertEqual(packet["account_status"], "ready")
        self.assertEqual(packet["place_result"]["status"], "submitted")
        self.assertEqual(packet["cancel_result"]["status"], "cancelled")
        self.assertEqual(packet["readback_result"]["after_cancel"]["status"], "cancelled")
        self.assertEqual(packet["reconcile_result"]["status"], "passed")
        self.assertFalse(packet["production_live_enabled"])
        self.assertFalse(packet["capital_binding_enabled"])
        self.assertTrue(packet["human_gate_required"])
        self.assertFalse(packet["fail_closed_posture"]["production_live_order_submitted"])
        self.assertEqual(packet["ooda_packet"]["environment"], "sandbox")
        self.assertFalse(packet["ooda_packet"]["act"]["live_capital_side_effects"])
        self.assertEqual(packet["ooda_packet_validation_errors"], [])

    def test_packet_is_incomplete_when_live_gate_is_not_rejected(self) -> None:
        smoke = self.smoke_payload()
        smoke["live_gate"] = {"status": "failed", "response": {"error_code": None}}

        packet = evidence_packet.build_evidence_packet(
            smoke_summary=smoke,
            smoke_summary_path="support/evidence/MGMT-BROKER-003/summary.json",
            task_packet_path="support/evidence/MGMT-BROKER-004/shioaji-sandbox-evidence-packet.json",
            generated_at=GENERATED_AT,
        )

        checks = {item["name"]: item for item in packet["acceptance_checks"]}
        self.assertEqual(packet["status"], "incomplete")
        self.assertEqual(checks["live_broker_fail_closed"]["status"], "fail")

    def test_repo_local_absolute_paths_are_written_as_portable_refs(self) -> None:
        packet = evidence_packet.build_evidence_packet(
            smoke_summary=self.smoke_payload(),
            smoke_summary_path=str(evidence_packet.REPO_ROOT / "support/evidence/MGMT-BROKER-003/summary.json"),
            task_packet_path=str(
                evidence_packet.REPO_ROOT
                / "support/evidence/MGMT-BROKER-004/shioaji-sandbox-evidence-packet.json"
            ),
            generated_at=GENERATED_AT,
        )

        self.assertEqual(
            packet["canary_readiness_inputs"]["broker_sandbox_smoke_ref"],
            "support/evidence/MGMT-BROKER-003/summary.json",
        )
        self.assertEqual(
            packet["canary_readiness_inputs"]["broker_sandbox_evidence_packet_ref"],
            "support/evidence/MGMT-BROKER-004/shioaji-sandbox-evidence-packet.json",
        )
        self.assertEqual(
            packet["ooda_packet"]["orient"]["evidence_bundle_refs"],
            ["support/evidence/MGMT-BROKER-004/shioaji-sandbox-evidence-packet.json"],
        )

    def test_write_packet_outputs_writes_task_and_milestone_packets(self) -> None:
        packet = evidence_packet.build_evidence_packet(
            smoke_summary=self.smoke_payload(),
            smoke_summary_path="support/evidence/MGMT-BROKER-003/summary.json",
            task_packet_path="support/evidence/MGMT-BROKER-004/shioaji-sandbox-evidence-packet.json",
            generated_at=GENERATED_AT,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            outputs = evidence_packet.write_packet_outputs(
                packet=packet,
                output_dir=tmp / "MGMT-BROKER-004",
                milestone_output=tmp / "MGMT-OODA-M3-shioaji-sandbox.json",
            )

            task_packet = json.loads(Path(outputs["task_packet"]).read_text(encoding="utf-8"))
            milestone_packet = json.loads(Path(outputs["milestone_output"]).read_text(encoding="utf-8"))
            self.assertEqual(task_packet["task_id"], "MGMT-BROKER-004")
            self.assertEqual(milestone_packet["milestone_id"], "MGMT-OODA-M3")
            self.assertTrue(Path(outputs["readme"]).exists())


if __name__ == "__main__":
    unittest.main()
