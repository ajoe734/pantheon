"""Unit tests for paper_telemetry_packet (MGMT-PAPER-005)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from services.telemetry.paper_telemetry_packet import (
    TASK_ID,
    PaperTelemetryContext,
    build_evidence_packet,
    build_paper_telemetry_events,
    run_ingest_validation,
    validate_evidence_packet,
    write_evidence_packet,
)


class PaperTelemetryPacketTest(unittest.TestCase):
    def test_builds_canonical_paper_telemetry_events(self) -> None:
        ctx = PaperTelemetryContext()
        events = build_paper_telemetry_events(ctx)

        self.assertEqual([event["event_type"] for event in events], [
            "heartbeat",
            "deploy_completed",
            "pnl_snapshot",
            "bracket_order_logged",
        ])
        for event in events:
            self.assertEqual(event["execution_mode"], "paper")
            self.assertEqual(event["deployment_stage"], "paper")
            self.assertEqual(event["binding_id"], ctx.binding_id)
            self.assertEqual(event["runtime_id"], ctx.runtime_id)
            self.assertEqual(event["target"]["strategy_id"], ctx.strategy_id)
            self.assertEqual(event["metadata"]["engine_bridge_repo"], "ajoe734/pantheon-lean.git")
            self.assertEqual(event["metadata"]["engine_bridge_commit"], ctx.engine_bridge_commit)

    def test_bracket_event_is_logged_only_not_broker_submission(self) -> None:
        event = build_paper_telemetry_events(PaperTelemetryContext())[-1]

        self.assertEqual(event["event_type"], "bracket_order_logged")
        self.assertEqual(event["metrics"]["action"], "bracket_logged_only")
        self.assertEqual(event["metadata"]["broker_submission_status"], "logged_only")
        self.assertFalse(event["metadata"]["submitted_to_broker"])
        self.assertFalse(event["metadata"]["is_real_order"])
        self.assertFalse(event["metadata"]["is_real_capital"])

    def test_ingest_validation_accepts_dedupes_and_rejects_bad_events(self) -> None:
        ctx = PaperTelemetryContext()
        events = build_paper_telemetry_events(ctx)

        result = run_ingest_validation(ctx, events)
        stats = result["stats"]["service"]
        summary = result["runtime_summary"]

        self.assertTrue(result["heartbeat_first_accepted"])
        self.assertTrue(result["heartbeat_duplicate_accepted"])
        self.assertTrue(result["stage_mismatch_rejected"])
        self.assertTrue(result["missing_binding_rejected"])
        self.assertEqual(stats["total_ingested"], len(events))
        self.assertEqual(stats["total_duplicates"], 1)
        self.assertEqual(stats["total_rejected"], 2)
        self.assertEqual(result["stats"]["writer"]["total_written"], len(events))
        self.assertEqual(summary["last_heartbeat_at"], events[0]["created_at"])
        self.assertEqual(summary["runtime_binding_id"], ctx.binding_id)
        self.assertEqual(summary["engine_bridge_repo"], "ajoe734/pantheon-lean.git")

    def test_evidence_packet_is_ooda_compatible(self) -> None:
        packet = build_evidence_packet(generated_at="2026-05-15T15:10:00Z")

        self.assertEqual(packet["task_id"], TASK_ID)
        self.assertEqual(packet["environment"], "paper")
        self.assertFalse(packet["live_capital_side_effects"])
        self.assertEqual(packet["validation_errors"], [])
        self.assertEqual(validate_evidence_packet(packet), [])
        self.assertEqual(
            packet["ooda_refs"]["observe"]["telemetry_refs"],
            [packet["telemetry_packet"]["events"][0]["event_id"]],
        )
        self.assertEqual(
            packet["ooda_refs"]["act"]["runtime_binding_id"],
            packet["runtime_binding_id"],
        )
        self.assertIn(
            packet["telemetry_packet"]["events"][2]["event_id"],
            packet["ooda_refs"]["learn"]["telemetry_refs"],
        )

    def test_write_evidence_packet_outputs_parseable_json(self) -> None:
        packet = build_evidence_packet(generated_at="2026-05-15T15:10:00Z")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fh:
            out_path = Path(fh.name)

        try:
            write_evidence_packet(packet, out_path)
            raw = json.loads(out_path.read_text(encoding="utf-8"))

            self.assertEqual(raw["task_id"], TASK_ID)
            self.assertEqual(raw["telemetry_packet"]["event_count"], 4)
            self.assertEqual(raw["validation_errors"], [])
            self.assertTrue(raw["safety_assertions"]["paper_stage_only"])
            self.assertTrue(raw["safety_assertions"]["bracket_logged_only"])
        finally:
            os.unlink(out_path)


if __name__ == "__main__":
    unittest.main()
