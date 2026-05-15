#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import run_research_activation_gates as gates


class ResearchActivationGateReportTest(unittest.TestCase):
    def test_current_report_blocks_time_and_data_gated_rows(self) -> None:
        report = gates.build_report({}, date(2026, 4, 26))
        rows = {row["row"]: row for row in report["rows"]}

        self.assertEqual(report["status"], "activation_gates_blocked")
        self.assertIn("Qlib", report["blocked_rows"])
        self.assertIn("TRL", report["blocked_rows"])
        self.assertIn("RL stack", report["blocked_rows"])
        self.assertIn("W&B", report["blocked_rows"])
        self.assertIn("RS-003 candidate proof missing", rows["Qlib"]["blockers"])
        self.assertIn("fewer than 200 governed FB-002 events", rows["TRL"]["blockers"])
        self.assertIn("RL path approval gate remains closed", rows["RL stack"]["blockers"])
        self.assertEqual(rows["W&B"]["evidence"]["earliest_reopen"], "2026-05-15")

    def test_evidence_packet_can_clear_qlib_and_trl_without_reopening_rl_or_wandb(self) -> None:
        evidence = {
            "qlib": {
                "rs003_candidate_passed": True,
                "dataset_instruments": 75,
                "dataset_years": 2.5,
                "strategy_spec_binding": True,
                "activation_run_archived": True,
            },
            "trl": {
                "feedback_events": 240,
                "preference_pairs": 130,
                "action_types": ["approve", "edit", "reject"],
                "strategy_families": 3,
                "imitation_approved_artifact": True,
                "baseline_model_metrics_pass": True,
                "downstream_consumer_ready": True,
                "activation_run_archived": True,
            },
        }
        report = gates.build_report(evidence, date(2026, 4, 26))
        rows = {row["row"]: row for row in report["rows"]}

        self.assertTrue(rows["Qlib"]["production_activated"])
        self.assertTrue(rows["TRL"]["production_activated"])
        self.assertFalse(rows["RL stack"]["production_activated"])
        self.assertFalse(rows["W&B"]["production_activated"])

    def test_wandb_time_gate_clears_only_with_all_reentry_evidence_after_earliest_date(self) -> None:
        evidence = {
            "wandb": {
                "operator_preference_on_file": True,
                "adapter_generalization_review_done": True,
                "canonical_state_migration_done": True,
                "wandb_sdk_pin_verified": True,
                "network_readiness_verified": True,
                "activation_run_archived": True,
            }
        }

        blocked = gates.evaluate_wandb(evidence, date(2026, 5, 14))
        cleared = gates.evaluate_wandb(evidence, date(2026, 5, 15))

        self.assertFalse(blocked.production_activated)
        self.assertTrue(cleared.production_activated)

    def test_command_writes_summary_and_returns_blocked_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "report"
            args = type(
                "Args",
                (),
                {
                    "evidence_json": None,
                    "as_of": "2026-04-26",
                    "output_dir": str(output_dir),
                },
            )
            exit_code = gates.command_report(args)

            self.assertEqual(exit_code, 2)
            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "activation_gates_blocked")
            self.assertIn("W&B", summary["blocked_rows"])


if __name__ == "__main__":
    unittest.main()
