#!/usr/bin/env python3
"""Unit tests for loop_done_guardrail and the validate_loop_completion_claim gate in ai_status."""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Resolve the scripts directory so both modules can be imported without install.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import loop_done_guardrail as guardrail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task(
    *,
    task_id: str = "LOOP-AUTO-TEST",
    loop_ids: list[str] | None = None,
    non_goals: list[str] | None = None,
    proof_required: list[str] | None = None,
    review_file: str = "",
    review_notes_zh: list[str] | None = None,
    status: str = "review_approved",
) -> dict:
    t: dict = {"id": task_id, "status": status}
    if loop_ids is not None:
        t["loop_ids"] = loop_ids
    if non_goals is not None:
        t["non_goals"] = non_goals
    if proof_required is not None:
        t["proof_required"] = proof_required
    if review_file:
        t["review_file"] = review_file
    if review_notes_zh is not None:
        t["review_notes_zh"] = review_notes_zh
    return t


# ---------------------------------------------------------------------------
# is_loop_autopilot_task
# ---------------------------------------------------------------------------

class TestIsLoopAutopilotTask(unittest.TestCase):
    def test_non_loop_task_returns_false(self):
        self.assertFalse(guardrail.is_loop_autopilot_task({"id": "REG-001"}))

    def test_task_with_loop_ids_is_loop(self):
        t = _task(loop_ids=["source_ingestion"])
        self.assertTrue(guardrail.is_loop_autopilot_task(t))

    def test_task_with_empty_loop_ids_is_not_loop(self):
        t = _task(loop_ids=[])
        self.assertFalse(guardrail.is_loop_autopilot_task(t))

    def test_task_with_panel_only_non_goal_is_loop(self):
        t = _task(non_goals=["No panel-only closure", "No live-capital execution"])
        self.assertTrue(guardrail.is_loop_autopilot_task(t))

    def test_task_with_seed_non_goal_is_loop(self):
        t = _task(non_goals=["No seed fixture as live proof"])
        self.assertTrue(guardrail.is_loop_autopilot_task(t))

    def test_task_with_unrelated_non_goals_is_not_loop(self):
        t = _task(non_goals=["No live-capital execution"])
        self.assertFalse(guardrail.is_loop_autopilot_task(t))


# ---------------------------------------------------------------------------
# check_task (standalone guardrail logic)
# ---------------------------------------------------------------------------

class TestCheckTask(unittest.TestCase):
    def test_non_loop_task_has_no_gaps(self):
        gaps = guardrail.check_task({"id": "REG-001", "status": "review_approved"})
        self.assertEqual(gaps, [])

    def test_panel_only_non_goal_without_review_file_is_gap(self):
        t = _task(
            loop_ids=["source_ingestion"],
            non_goals=["No panel-only closure"],
            proof_required=["unit tests"],
        )
        gaps = guardrail.check_task(t)
        self.assertTrue(any("review_file" in g for g in gaps), gaps)

    def test_panel_only_non_goal_with_review_file_passes(self):
        t = _task(
            loop_ids=["source_ingestion"],
            non_goals=["No panel-only closure"],
            proof_required=["unit tests"],
            review_file="docs/deployment/evidence/smoke.md",
        )
        gaps = guardrail.check_task(t)
        self.assertEqual(gaps, [])

    def test_fixture_signal_in_notes_is_gap(self):
        t = _task(
            loop_ids=["evolution"],
            non_goals=["No seed fixture as live proof"],
            review_notes_zh=["Verified: fixture only approach confirmed"],
            review_file="docs/deployment/evidence/test.md",
        )
        gaps = guardrail.check_task(t)
        self.assertTrue(any("fixture only" in g for g in gaps), gaps)

    def test_seed_only_in_notes_is_gap(self):
        t = _task(
            loop_ids=["evolution"],
            non_goals=["No seed fixture as live proof"],
            review_notes_zh=["Seed only run confirmed"],
            review_file="docs/deployment/evidence/test.md",
        )
        gaps = guardrail.check_task(t)
        self.assertTrue(any("seed only" in g for g in gaps), gaps)

    def test_clean_notes_with_review_file_passes(self):
        t = _task(
            loop_ids=["evolution"],
            non_goals=["No seed fixture as live proof"],
            review_notes_zh=["Smoke run confirmed controller liveness"],
            review_file="docs/deployment/evidence/smoke.md",
        )
        gaps = guardrail.check_task(t)
        self.assertEqual(gaps, [])

    def test_proof_required_without_review_file_is_gap(self):
        t = _task(
            loop_ids=["telemetry_reconciliation"],
            proof_required=["unit tests", "contract tests", "local service smoke"],
        )
        gaps = guardrail.check_task(t)
        self.assertTrue(any("proof_required" in g or "review_file" in g for g in gaps), gaps)

    def test_proof_required_with_review_file_passes(self):
        t = _task(
            loop_ids=["telemetry_reconciliation"],
            proof_required=["unit tests"],
            review_file="docs/deployment/evidence/test-run.md",
        )
        gaps = guardrail.check_task(t)
        self.assertEqual(gaps, [])

    def test_panel_only_signal_in_notes_case_insensitive(self):
        t = _task(
            loop_ids=["capital_pool_execution"],
            non_goals=["No seed fixture as live proof"],
            review_notes_zh=["Closed via PANEL-ONLY confirmation"],
            review_file="docs/deployment/evidence/smoke.md",
        )
        gaps = guardrail.check_task(t)
        self.assertTrue(any("panel-only" in g for g in gaps), gaps)

    def test_multiple_gaps_reported(self):
        t = _task(
            loop_ids=["source_ingestion"],
            non_goals=["No panel-only closure", "No seed fixture as live proof"],
            proof_required=["unit tests"],
            review_notes_zh=["seed only confirmed"],
        )
        gaps = guardrail.check_task(t)
        # Expect at least two gaps: missing review_file (panel-only & proof_required) + fixture note.
        self.assertGreaterEqual(len(gaps), 2)


# ---------------------------------------------------------------------------
# run() with a temporary status file
# ---------------------------------------------------------------------------

class TestRunFunction(unittest.TestCase):
    def _write_status(self, tasks: list[dict]) -> Path:
        tf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump({"tasks": tasks}, tf)
        tf.close()
        return Path(tf.name)

    def test_empty_task_list_returns_0(self):
        path = self._write_status([])
        try:
            rc = guardrail.run(path, None)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(rc, 0)

    def test_all_pass_returns_0(self):
        t = _task(
            loop_ids=["source_ingestion"],
            non_goals=["No panel-only closure"],
            proof_required=["unit tests"],
            review_file="docs/deployment/evidence/smoke.md",
        )
        path = self._write_status([t])
        try:
            rc = guardrail.run(path, None)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(rc, 0)

    def test_any_gap_returns_1(self):
        t = _task(
            loop_ids=["source_ingestion"],
            non_goals=["No panel-only closure"],
            proof_required=["unit tests"],
        )
        path = self._write_status([t])
        try:
            rc = guardrail.run(path, None)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(rc, 1)

    def test_task_id_filter_unknown_returns_2(self):
        path = self._write_status([])
        try:
            rc = guardrail.run(path, "NONEXISTENT")
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(rc, 2)

    def test_task_id_filter_specific_task(self):
        t_pass = _task(
            task_id="PASS-001",
            loop_ids=["source_ingestion"],
            review_file="docs/deployment/evidence/smoke.md",
        )
        t_fail = _task(
            task_id="FAIL-001",
            loop_ids=["source_ingestion"],
            non_goals=["No panel-only closure"],
        )
        path = self._write_status([t_pass, t_fail])
        try:
            rc_pass = guardrail.run(path, "PASS-001")
            rc_fail = guardrail.run(path, "FAIL-001")
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(rc_pass, 0)
        self.assertEqual(rc_fail, 1)


# ---------------------------------------------------------------------------
# validate_loop_completion_claim in ai_status
# ---------------------------------------------------------------------------

class TestValidateLoopCompletionClaimInAIStatus(unittest.TestCase):
    """Verify that the gate in ai_status.validate_loop_completion_claim works."""

    def setUp(self):
        import ai_status
        self.ai_status = ai_status

    def test_non_loop_task_passes_silently(self):
        t = {"id": "REG-001", "status": "review_approved"}
        self.ai_status.validate_loop_completion_claim(t)  # should not raise

    def test_loop_task_without_review_file_raises(self):
        t = _task(
            loop_ids=["source_ingestion"],
            non_goals=["No panel-only closure"],
            proof_required=["unit tests"],
        )
        with self.assertRaises(SystemExit) as cm:
            self.ai_status.validate_loop_completion_claim(t)
        self.assertIn("review_file", str(cm.exception))

    def test_loop_task_with_fixture_notes_raises(self):
        t = _task(
            loop_ids=["evolution"],
            non_goals=["No seed fixture as live proof"],
            review_notes_zh=["Verified via fixture only"],
            review_file="docs/deployment/evidence/test.md",
        )
        with self.assertRaises(SystemExit) as cm:
            self.ai_status.validate_loop_completion_claim(t)
        self.assertIn("fixture only", str(cm.exception))

    def test_loop_task_with_valid_evidence_passes(self):
        t = _task(
            loop_ids=["source_ingestion"],
            non_goals=["No panel-only closure", "No seed fixture as live proof"],
            proof_required=["unit tests"],
            review_notes_zh=["Smoke run confirmed controller liveness"],
            review_file="docs/deployment/evidence/smoke.md",
        )
        self.ai_status.validate_loop_completion_claim(t)  # should not raise

    def test_loop_task_proof_required_without_review_file_raises(self):
        t = _task(
            loop_ids=["telemetry_reconciliation"],
            proof_required=["unit tests", "local service smoke"],
        )
        with self.assertRaises(SystemExit) as cm:
            self.ai_status.validate_loop_completion_claim(t)
        self.assertIn("review_file", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
