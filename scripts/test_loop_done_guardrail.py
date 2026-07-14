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


# ---------------------------------------------------------------------------
# Deep Evidence Manifest Checks Tests
# ---------------------------------------------------------------------------

DEFAULT_VALID_EVIDENCE = {
    "schema_version": "loop_product_evidence.bootstrap.v1",
    "schema_status": {
        "status": "provisional",
        "formal_schema_owner": "LOOP-PROD-002",
        "formalization_trigger": "Replace or validate this bootstrap shape when the product-evidence schema from LOOP-PROD-002 merges.",
        "note": "Valid test note"
    },
    "evidence_policy": {
        "recording_mode": "logical_append_only",
        "redacted": True,
        "missing_or_contradicted_proof_fails_closed": True,
        "mutation_rule": "Immutable after merge",
        "self_hashing": False,
        "checksum_file": "docs/deployment/evidence/temp_test_evidence.sha256"
    },
    "task": {
        "id": "LOOP-AUTO-TEST",
        "title": "Test Title",
        "phase": "Test Phase",
        "repository": "pantheon",
        "owner": "Antigravity",
        "reviewer": "Claude",
        "task_branch": "task/LOOP-AUTO-TEST",
        "base_branch": "dev",
        "target_environment": "dev",
        "product_level_required": True,
        "target_maturity": "reconciled",
        "evidence_cut_at": "2026-07-13T22:40:16Z",
        "evidence_cut_semantics": "Test semantics",
        "review_file": "docs/deployment/evidence/temp_test_evidence.json",
        "overall_admission": "review_required_evidence_only"
    },
    "authorities": {
        "actual_state": ["GET /test"],
        "desired_state": ["test spec"],
        "task_packet": "docs/test.md"
    },
    "scope": {
        "authoritative_write_owner": "Antigravity",
        "owned_layer": "test layer",
        "not_changing": "other layers",
        "composes_with": ["other-task"],
        "implementation_changed_files": ["test.py"],
        "evidence_changed_files": ["docs/deployment/evidence/temp_test_evidence.json"]
    },
    "implementation_delivery": {
        "anchor_commits": [{"sha": "abc1234", "subject": "anchor commit"}],
        "pull_request": {
            "number": 123,
            "url": "https://github.com/ajoe734/pantheon/pull/123",
            "head_sha": "abc1234",
            "base": "dev",
            "merged_at": "2026-07-14T00:00:00Z",
            "merge_sha": "def5678"
        },
        "required_checks": [
            {
                "workflow": "Branch CI Gate",
                "event": "pull_request",
                "run_id": 999,
                "url": "https://github.com/run/999",
                "conclusion": "success"
            }
        ]
    },
    "validation": {
        "validated_base_sha": "base123",
        "validated_head_sha": "head123",
        "validated_at": "2026-07-13T22:40:16Z",
        "commands": [
            {"command": "pytest", "result": "pass"}
        ]
    },
    "deployment": {
        "applicable": True,
        "environment": "dev",
        "public_bff_base_url": "https://test-bff.io",
        "publish_cut": {"conclusion": "success", "publish_ref": "v1", "release_ref": "v1", "run_id": 111},
        "canonical_root_deploy": {"conclusion": "success"},
        "identity_admission": {"admission_boundary": "Test boundary"}
    },
    "hosted_readback": {
        "pre_deploy": {
            "inventory_authenticated_http": 200,
            "health_authenticated_http": 200
        },
        "capture_time_hosted_readback": {
            "inventory_authenticated_http": 200,
            "health_authenticated_http": 200
        }
    },
    "behavioral_proof": {
        "duplicate_safety": {"proof": ["No duplicate safety issue"], "status": "pass"},
        "failure_and_degraded_behavior": {"proof": ["Degraded mode OK"], "status": "pass"},
        "request_receipt_downstream_correlation": {"proof": ["Correlation OK"], "status": "pass"},
        "restart_and_recovery": {"proof": ["Restart survives"], "status": "pass"},
        "rollback_or_compensation": {"proof": ["Rollback succeeds"], "status": "pass"}
    },
    "security_and_safety": {
        "environment_boundary": {"status": "pass"},
        "hosted_frontend": {"status": "not_applicable"},
        "mfa": {"status": "pass"},
        "no_live_capital": {"status": "pass"},
        "rbac": {"status": "pass"},
        "tenant_isolation": {"status": "pass"},
        "two_person_approval": {"status": "pass"}
    },
    "acceptance": [
        {
            "id": "AC-01",
            "statement": "Requirement statement",
            "status": "pass",
            "evidence_refs": ["behavioral_proof.restart_and_recovery"]
        }
    ],
    "residual_risks": {
        "RISK-TEST": {
            "blocking_for_this_task": False,
            "containment": "None needed",
            "description": "Risk details",
            "expiry": "2026-07-31T00:00:00Z",
            "owner": "Antigravity",
            "recheck_trigger": "None",
            "severity": "low"
        }
    },
    "integrity": {
        "algorithm": "sha256",
        "checksum_coverage": ["evidence.json"],
        "companion_checksum_path": "docs/deployment/evidence/temp_test_evidence.sha256",
        "hosted_semantic_sha256": {},
        "manifest_path": "docs/deployment/evidence/temp_test_evidence.json",
        "normalized_hosted_readback": {},
        "self_hash_omitted": True,
        "self_hash_reason": "OMIT",
        "source_artifact_sha256_by_epoch": {}
    },
    "record_log": [
        {
            "sequence": 1,
            "recorded_at": "2026-07-14T00:00:00Z",
            "kind": "review_approved",
            "status": "pass",
            "actor": "Claude"
        }
    ]
}


class TestDeepEvidenceChecks(unittest.TestCase):
    def setUp(self):
        self.evidence_path = Path("docs/deployment/evidence/temp_test_evidence.json")
        self.full_path = guardrail.ROOT / self.evidence_path
        self.full_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_task = _task(
            task_id="LOOP-AUTO-TEST",
            loop_ids=["source_ingestion"],
            review_file=str(self.evidence_path)
        )
        self.default_task["owner"] = "Antigravity"
        self.default_task["reviewer"] = "Claude"
        self.write_evidence(DEFAULT_VALID_EVIDENCE)

    def tearDown(self):
        if self.full_path.exists():
            self.full_path.unlink()

    def write_evidence(self, data: dict):
        with open(self.full_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_valid_evidence_passes(self):
        gaps = guardrail.check_task(self.default_task)
        self.assertEqual(gaps, [])

    def test_invalid_schema_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        del data["schema_version"]
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("schema validation failed" in g for g in gaps), gaps)

    def test_task_id_mismatch_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["task"] = data["task"].copy()
        data["task"]["id"] = "DIFFERENT-ID"
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("task ID mismatch" in g for g in gaps), gaps)

    def test_owner_mismatch_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["task"] = data["task"].copy()
        data["task"]["owner"] = "DIFFERENT-OWNER"
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("owner mismatch" in g for g in gaps), gaps)

    def test_reviewer_mismatch_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["task"] = data["task"].copy()
        data["task"]["reviewer"] = "DIFFERENT-REVIEWER"
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("reviewer mismatch" in g for g in gaps), gaps)

    def test_acceptance_status_not_pass_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["acceptance"] = [
            {
                "id": "AC-01",
                "statement": "Requirement statement",
                "status": "pending_independent_review",
                "evidence_refs": []
            }
        ]
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("blocking acceptance requirement" in g for g in gaps), gaps)

    def test_mock_only_live_claim_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["task"] = data["task"].copy()
        data["task"]["target_maturity"] = "live"
        data["behavioral_proof"] = data["behavioral_proof"].copy()
        data["behavioral_proof"]["restart_and_recovery"] = {
            "proof": ["Tested via fixture only setup"],
            "status": "pass"
        }
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("mock-only live claim detected" in g for g in gaps), gaps)

    def test_terminal_readback_failure_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["hosted_readback"] = {
            "pre_deploy": {
                "inventory_authenticated_http": 502
            }
        }
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("terminal readback failure observed" in g for g in gaps), gaps)

    def test_missing_restart_proof_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["behavioral_proof"] = data["behavioral_proof"].copy()
        data["behavioral_proof"]["restart_and_recovery"] = {"proof": [], "status": "pass"}
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("missing restart and recovery proof" in g for g in gaps), gaps)

    def test_missing_security_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["security_and_safety"] = data["security_and_safety"].copy()
        data["security_and_safety"]["rbac"] = {"status": "fail"}
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("missing security evidence" in g for g in gaps), gaps)

    def test_missing_reviewer_verdict_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["record_log"] = [
            {
                "sequence": 1,
                "recorded_at": "2026-07-14T00:00:00Z",
                "kind": "review_approved",
                "status": "pending"
            }
        ]
        self.write_evidence(data)
        
        # Test when task status is done
        task_done = self.default_task.copy()
        task_done["status"] = "done"
        gaps = guardrail.check_task(task_done)
        self.assertTrue(any("missing reviewer verdict" in g for g in gaps), gaps)

    def test_missing_jsonschema_dependency_returns_graceful_gap(self):
        import sys
        with mock.patch.dict(sys.modules, {"jsonschema": None}):
            gaps = guardrail.check_task(self.default_task)
            self.assertTrue(any("jsonschema library is not installed" in g for g in gaps), gaps)



if __name__ == "__main__":
    unittest.main()
