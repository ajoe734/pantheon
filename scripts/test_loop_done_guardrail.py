#!/usr/bin/env python3
"""Unit tests for loop_done_guardrail and the validate_loop_completion_claim gate in ai_status."""
from __future__ import annotations

import contextlib
import copy
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

    def test_product_level_task_without_review_file_is_gap(self):
        t = _task(loop_ids=["source_ingestion"])
        t["product_level_required"] = True
        gaps = guardrail.check_task(t)
        self.assertTrue(any("product-level closeout requires" in g for g in gaps), gaps)

    def test_product_level_task_with_non_manifest_review_file_is_gap(self):
        t = _task(
            loop_ids=["source_ingestion"],
            review_file="docs/deployment/evidence/summary.md",
        )
        t["product_level_required"] = True
        gaps = guardrail.check_task(t)
        self.assertTrue(any("must be an evidence.json manifest" in g for g in gaps), gaps)

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
        "overall_admission": "pass"
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

    def test_relative_manifest_resolves_from_bound_worker_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            relative_path = Path(
                "docs/deployment/evidence/twelve-loop-gap/"
                "LOOP-AUTO-TEST/evidence.json"
            )
            full_path = workspace / relative_path
            full_path.parent.mkdir(parents=True)
            evidence = copy.deepcopy(DEFAULT_VALID_EVIDENCE)
            evidence["task"]["review_file"] = str(relative_path)
            full_path.write_text(json.dumps(evidence), encoding="utf-8")
            task = dict(self.default_task)
            task["review_file"] = str(relative_path)

            with mock.patch.dict(
                os.environ,
                {
                    "PANTHEON_WORKTREE_ROOT": str(workspace),
                    "ORCH_WORKSPACE_PATH": str(workspace),
                },
                clear=False,
            ):
                gaps = guardrail.check_task(task)

        self.assertEqual(gaps, [])

    def test_absolute_product_manifest_path_is_rejected_as_nonportable(self):
        task = dict(self.default_task)
        task["review_file"] = str(self.full_path.resolve())
        gaps = guardrail.check_task(task)
        self.assertTrue(
            any("must be repo-relative and portable" in gap for gap in gaps),
            gaps,
        )

    def test_parent_traversal_review_file_is_rejected(self):
        task = dict(self.default_task)
        task["review_file"] = "../evidence.json"
        gaps = guardrail.check_task(task)
        self.assertTrue(
            any("escapes repository scope" in gap for gap in gaps),
            gaps,
        )

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

    def test_blocked_overall_admission_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["task"] = data["task"].copy()
        data["task"]["overall_admission"] = "blocked_pending_human_deploy"
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("overall admission" in g and "not done-eligible" in g for g in gaps), gaps)

    def test_pending_overall_admission_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["task"] = data["task"].copy()
        data["task"]["overall_admission"] = "pending_independent_review"
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("overall admission" in g and "not done-eligible" in g for g in gaps), gaps)

    def test_review_required_overall_admission_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["task"] = data["task"].copy()
        data["task"]["overall_admission"] = "review_required_evidence_only"
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("overall admission" in g and "not done-eligible" in g for g in gaps), gaps)

    def test_unknown_overall_admission_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["task"] = data["task"].copy()
        data["task"]["overall_admission"] = "evidence_only"
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("overall admission" in g and "not an accepted closeout state" in g for g in gaps), gaps)

    def test_review_approved_overall_admission_passes(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["task"] = data["task"].copy()
        data["task"]["overall_admission"] = "review_approved_owner_closeout_ready"
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertEqual(gaps, [])

    def test_blocking_residual_risk_fails(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["residual_risks"] = {
            "RISK-BLOCKING": {
                "blocking_for_this_task": True,
                "containment": "Containment",
                "description": "Blocking risk",
                "expiry": "2026-07-31T00:00:00Z",
                "owner": "Antigravity",
                "recheck_trigger": "Recheck",
                "severity": "high"
            }
        }
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("blocking residual risk 'RISK-BLOCKING'" in g for g in gaps), gaps)

    def test_nonblocking_residual_risk_passes(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["residual_risks"] = {
            "RISK-NONBLOCKING": {
                "blocking_for_this_task": False,
                "containment": "Containment",
                "description": "Nonblocking risk",
                "expiry": "2026-07-31T00:00:00Z",
                "owner": "Antigravity",
                "recheck_trigger": "Recheck",
                "severity": "low"
            }
        }
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertEqual(gaps, [])

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

        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("missing reviewer verdict" in g for g in gaps), gaps)

    def test_review_ready_record_does_not_count_as_verdict(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["record_log"] = [
            {
                "sequence": 1,
                "recorded_at": "2026-07-14T00:00:00Z",
                "kind": "evidence_ready_for_independent_review",
                "status": "pass",
                "actor": "Antigravity",
                "reviewer": "Claude"
            }
        ]
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("missing reviewer verdict" in g for g in gaps), gaps)

    def test_owner_review_record_does_not_count_as_verdict(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["record_log"] = [
            {
                "sequence": 1,
                "recorded_at": "2026-07-14T00:00:00Z",
                "kind": "implementation_review",
                "status": "pass",
                "actor": "Antigravity"
            }
        ]
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertTrue(any("missing reviewer verdict" in g for g in gaps), gaps)

    def test_reviewer_approval_verdict_counts(self):
        data = DEFAULT_VALID_EVIDENCE.copy()
        data["record_log"] = [
            {
                "sequence": 1,
                "recorded_at": "2026-07-14T00:00:00Z",
                "kind": "reviewer_approval_verdict",
                "status": "approved",
                "actor": "Claude"
            }
        ]
        self.write_evidence(data)
        gaps = guardrail.check_task(self.default_task)
        self.assertEqual(gaps, [])

    def test_missing_jsonschema_dependency_returns_graceful_gap(self):
        import sys
        with mock.patch.dict(sys.modules, {"jsonschema": None}):
            gaps = guardrail.check_task(self.default_task)
            self.assertTrue(any("jsonschema library is not installed" in g for g in gaps), gaps)


class TestArchiveReplayAudit(unittest.TestCase):
    def _write_snapshot(
        self,
        root: Path,
        file_id: str,
        *,
        snapshot_task_id: str | None = None,
        task_id: str | None = None,
        review_file: str = "",
    ) -> Path:
        path = root / f"{file_id}.json"
        data = {
            "version": 1,
            "task_id": snapshot_task_id if snapshot_task_id is not None else file_id,
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": {
                "id": task_id or file_id,
                "status": "done",
                "owner": "Owner",
                "reviewer": "Reviewer",
                "review_file": review_file,
                "product_level_required": True,
            },
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_frozen_archive_replay_task_ids_are_exact(self):
        expected = [
            "LOOP-PROD-AGORA-001",
            "LOOP-PROD-AGORA-002",
            "LOOP-PROD-ALPHA-001",
            "LOOP-PROD-AUTH-001",
            "LOOP-PROD-CAP-001",
            "LOOP-PROD-CONS-001",
            "LOOP-PROD-DEP-001",
            "LOOP-PROD-DIST-001",
            "LOOP-PROD-GAP-ADDENDUM-001",
            "LOOP-PROD-GAP-ADDENDUM-002",
            "LOOP-PROD-IMIT-001",
            "LOOP-PROD-MAI-001",
            "LOOP-PROD-OODA-001",
            "LOOP-PROD-REC-001",
            "LOOP-PROD-RUNTIME-BOOT-001",
            "LOOP-PROD-SRC-001",
            "LOOP-PROD-TEACH-001",
            "LOOP-PROD-TEL-001",
        ]
        self.assertEqual(list(guardrail.FROZEN_ARCHIVE_REPLAY_TASK_IDS), expected)
        self.assertEqual(
            guardrail.DEFAULT_ARCHIVE_EXCLUDED_TASK_IDS,
            {
                "LOOP-PROD-000",
                "LOOP-PROD-001",
                "LOOP-PROD-002",
                "LOOP-PROD-DONE-GUARDRAIL-FINAL-REVIEW-001",
                "LOOP-PROD-PLANNING-BRIEFS-001",
            },
        )

    def test_archive_replay_rejects_missing_and_extra_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_snapshot(root, "LOOP-PROD-A")
            self._write_snapshot(root, "LOOP-PROD-C")
            audit = guardrail.audit_archive_root(
                root,
                excluded_task_ids=set(),
                frozen_task_ids=("LOOP-PROD-A", "LOOP-PROD-B"),
            )
        errors = audit["selection"]["source_set_errors"]
        self.assertTrue(any("missing frozen archive snapshot: LOOP-PROD-B.json" in e for e in errors), errors)
        self.assertTrue(any("unexpected LOOP-PROD archive snapshot: LOOP-PROD-C.json" in e for e in errors), errors)
        self.assertEqual(audit["selection"]["included_task_ids"], ["LOOP-PROD-A"])

    def test_archive_replay_rejects_duplicate_frozen_task_ids(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_snapshot(root, "LOOP-PROD-A")
            audit = guardrail.audit_archive_root(
                root,
                excluded_task_ids=set(),
                frozen_task_ids=("LOOP-PROD-A", "LOOP-PROD-A"),
            )
        errors = audit["selection"]["source_set_errors"]
        self.assertTrue(any("duplicate frozen archive replay task id: LOOP-PROD-A" in e for e in errors), errors)

    def test_archive_replay_rejects_malformed_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "LOOP-PROD-A.json").write_text("{not-json", encoding="utf-8")
            audit = guardrail.audit_archive_root(
                root,
                excluded_task_ids=set(),
                frozen_task_ids=("LOOP-PROD-A",),
            )
        self.assertEqual(audit["results"][0]["classification"], "stale_evidence")
        self.assertTrue(any("failed to parse archive snapshot JSON" in g for g in audit["results"][0]["gaps"]))

    def test_archive_replay_rejects_filename_task_id_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_snapshot(root, "LOOP-PROD-A", task_id="LOOP-PROD-B")
            audit = guardrail.audit_archive_root(
                root,
                excluded_task_ids=set(),
                frozen_task_ids=("LOOP-PROD-A",),
            )
        gaps = audit["results"][0]["gaps"]
        self.assertTrue(any("archive filename/task ID mismatch" in g for g in gaps), gaps)

    def test_archive_replay_rejects_top_level_task_id_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_snapshot(
                root,
                "LOOP-PROD-A",
                snapshot_task_id="LOOP-PROD-B",
            )
            audit = guardrail.audit_archive_root(
                root,
                excluded_task_ids=set(),
                frozen_task_ids=("LOOP-PROD-A",),
            )
        gaps = audit["results"][0]["gaps"]
        self.assertTrue(any("archive filename/task_id mismatch" in g for g in gaps), gaps)

    def test_archive_replay_rejects_duplicate_task_ids(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_snapshot(root, "LOOP-PROD-A", task_id="LOOP-PROD-DUP")
            self._write_snapshot(root, "LOOP-PROD-B", task_id="LOOP-PROD-DUP")
            audit = guardrail.audit_archive_root(
                root,
                excluded_task_ids=set(),
                frozen_task_ids=("LOOP-PROD-A", "LOOP-PROD-B"),
            )
        second_gaps = audit["results"][1]["gaps"]
        self.assertTrue(any("duplicate archive task id" in g for g in second_gaps), second_gaps)

    def test_archive_replay_hashes_are_immutable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = self._write_snapshot(root, "LOOP-PROD-A")
            before = guardrail._sha256_file(path)
            audit = guardrail.audit_archive_root(
                root,
                excluded_task_ids=set(),
                frozen_task_ids=("LOOP-PROD-A",),
            )
            after = guardrail._sha256_file(path)
        result = audit["results"][0]
        self.assertEqual(before, after)
        self.assertEqual(result["snapshot_sha256_before"], result["snapshot_sha256_after"])
        self.assertTrue(result["snapshot_hash_unchanged"])

    def test_archive_replay_classification_and_repair_ids(self):
        self.assertEqual(
            guardrail._classify_archive_replay_result(["blocking residual risk 'R' remains open"]),
            "false_closure",
        )
        self.assertEqual(
            guardrail._classify_archive_replay_result(["product-level closeout requires evidence"]),
            "stale_evidence",
        )
        self.assertEqual(
            guardrail._follow_up_task_id("LOOP-PROD-X", "false_closure"),
            "LOOP-PROD-X-FALSE-CLOSEOUT-REPAIR",
        )
        self.assertEqual(
            guardrail._follow_up_task_id("LOOP-PROD-X", "stale_evidence"),
            "LOOP-PROD-X-STALE-EVIDENCE-REPAIR",
        )
        self.assertIsNone(guardrail._follow_up_task_id("LOOP-PROD-X", "valid_closure"))


class TestProtectedHumanOpsCloseoutGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="l12-signoff-guard-")
        self.temp_root = Path(self.temp.name)
        self.status_root = self.temp_root / "status-root"
        self.status_root.mkdir()
        self.protected_root = self.temp_root / "protected-runtime"
        self.protected_root.mkdir()
        self.manifest_path = self.status_root / "evidence" / "closeout.json"
        self.manifest_path.parent.mkdir()
        self.manifest = {
            "deployment": {
                "environment": "pantheon-lupin-dev",
                "identity_admission": {
                    "target_environment": "pantheon-lupin-dev",
                    "frontend_sha": "c" * 40,
                    "bff_sha": "d" * 40,
                },
            }
        }
        self._write_manifest()

        self.module = guardrail._load_product_closeout_module()
        self.private_key = self.module.generate_private_key()
        self.key_id = "human-ops-key-test"
        self.policy = self.module.VerdictPolicy(
            policy_version="product-closeout-v1",
            public_keys={
                self.key_id: self.module.public_key_bytes(self.private_key)
            },
        )
        self.ledger = self.module.ProtectedVerdictLedger(
            self.protected_root / "verdict-ledger.jsonl"
        )
        self.issuer = self.module.ProductCloseoutVerdictService(
            ledger=self.ledger,
            policy=self.policy,
            private_key=self.private_key,
            key_id=self.key_id,
        )
        self.policy_path = self.protected_root / "verdict-policy.json"
        self.policy_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "policy_version": self.policy.policy_version,
                    "ledger_path": str(self.ledger.path),
                    "public_keys": {
                        self.key_id: self.module.public_key_to_base64(
                            self.policy.public_keys[self.key_id]
                        )
                    },
                    "max_verdict_age_seconds": 3600,
                    "max_ttl_seconds": 3600,
                }
            ),
            encoding="utf-8",
        )
        self.default_policy_patch = mock.patch.object(
            self.module,
            "DEFAULT_PROTECTED_POLICY_PATH",
            self.policy_path,
        )
        self.default_policy_patch.start()
        self.task = {
            "id": "L12-CLOSE-001",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Codex2",
            "loop_ids": ["source_ingestion"],
            "review_file": "evidence/closeout.json",
            "requires_human_ops_signoff": True,
        }
        # Explicitly neutralise the worker workspace bindings and the run lease
        # so the default cases resolve from the status root even when this
        # suite runs inside a dispatched task worktree.
        self.env = {
            "PANTHEON_STATUS_ROOT": str(self.status_root),
            "PANTHEON_WORKTREE_ROOT": "",
            "ORCH_WORKSPACE_PATH": "",
            "ORCH_RUN_ID": "",
        }

    def tearDown(self) -> None:
        self.default_policy_patch.stop()
        self.temp.cleanup()

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self.manifest, sort_keys=True),
            encoding="utf-8",
        )

    def _binding(self):
        catalog = json.loads(
            guardrail._PRODUCT_CLOSEOUT_CATALOG.read_text(encoding="utf-8")
        )
        return self.module.CloseoutBinding(
            program_id=catalog["program_id"],
            catalog_sha256=self.module.canonical_json_sha256(catalog),
            task_id="L12-CLOSE-001",
            closeout_manifest_sha256=guardrail._sha256_file(self.manifest_path),
            target_environment="pantheon-lupin-dev",
            frontend_sha="c" * 40,
            bff_sha="d" * 40,
        )

    def _issue(self, *, decision: str = "approved") -> dict:
        verdict = self.issuer.issue(
            self._binding(),
            self.module.HumanOpsIdentity(
                actor_id="ops-human-001",
                actor_role="ops",
                authenticated=True,
                mfa_verified=True,
            ),
            decision=decision,
            ttl_seconds=900,
        )
        self.env["PANTHEON_PRODUCT_CLOSEOUT_VERDICT_ID"] = verdict["verdict_id"]
        return verdict

    def test_review_approved_verifies_and_done_consumes_once(self) -> None:
        verdict = self._issue()
        with mock.patch.dict(os.environ, self.env, clear=False):
            reference = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )
            self.assertEqual(reference["verdict_id"], verdict["verdict_id"])
            self.task["protected_closeout_verdict"] = reference
            self.task["status"] = "review_approved"
            self.assertEqual(guardrail.check_task(self.task), [])

            consumed = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="done",
                consume=True,
                transition_actor="Codex",
            )
            self.task["protected_closeout_verdict"] = consumed
            self.task["status"] = "done"
            self.assertEqual(guardrail.check_task(self.task), [])

            retried = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="done",
                consume=True,
                transition_actor="Codex",
            )
            self.assertEqual(
                retried["consumption_record_id"],
                consumed["consumption_record_id"],
            )

            with self.assertRaisesRegex(Exception, "different transition attempt"):
                self.module.ProductCloseoutVerdictService(
                    ledger=self.ledger,
                    policy=self.policy,
                ).consume(
                    verdict["verdict_id"],
                    expected_binding=self._binding(),
                    transition="done",
                    transition_actor="Codex",
                    idempotency_key="different-logical-closeout",
                )

    def test_direct_done_state_edit_without_consumption_is_rejected(self) -> None:
        verdict = self._issue()
        self.task["status"] = "done"
        self.task.pop("requires_human_ops_signoff")
        self.task["protected_closeout_verdict"] = {
            "verdict_id": verdict["verdict_id"],
            "ledger_entry_id": verdict["ledger_entry_id"],
            "verifier_capability_sha256": verdict[
                "verifier_capability_sha256"
            ],
        }
        with mock.patch.dict(os.environ, self.env, clear=False):
            gaps = guardrail.check_task(self.task)
        self.assertTrue(
            any("requires exactly one verdict consumption" in gap for gap in gaps),
            gaps,
        )

    def test_mutable_loop_metadata_removal_cannot_disable_sink_guard(self) -> None:
        self._issue()
        self.task["status"] = "done"
        self.task.pop("loop_ids")
        self.task.pop("requires_human_ops_signoff")
        with mock.patch.dict(os.environ, self.env, clear=False):
            gaps = guardrail.check_task(self.task)

        self.assertTrue(
            any("requires exactly one verdict consumption" in gap for gap in gaps),
            gaps,
        )

    def test_consumption_reference_tamper_is_rejected(self) -> None:
        self._issue()
        with mock.patch.dict(os.environ, self.env, clear=False):
            consumed = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="done",
                consume=True,
                transition_actor="Codex",
            )
            self.task["status"] = "done"
            self.task["protected_closeout_verdict"] = {
                **consumed,
                "consumption_record_id": "tampered-consumption-reference",
            }
            gaps = guardrail.check_task(self.task)

        self.assertTrue(
            any("consumption_record_id reference mismatch" in gap for gap in gaps),
            gaps,
        )

    def test_missing_rejected_and_candidate_only_verdicts_fail_closed(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            with self.assertRaisesRegex(Exception, "requires PANTHEON"):
                guardrail.validate_protected_closeout_transition(
                    self.task,
                    transition="review_approved",
                )

        rejected = self._issue(decision="rejected")
        with mock.patch.dict(os.environ, self.env, clear=False):
            with self.assertRaisesRegex(Exception, "not approved"):
                guardrail.validate_protected_closeout_transition(
                    self.task,
                    transition="review_approved",
                )

        candidate_file = self.status_root / "candidate-signed-verdict.json"
        candidate_file.write_text(json.dumps(rejected), encoding="utf-8")
        self.env["PANTHEON_PRODUCT_CLOSEOUT_VERDICT_ID"] = "candidate-only-verdict"
        with mock.patch.dict(os.environ, self.env, clear=False):
            with self.assertRaisesRegex(Exception, "exactly one issue"):
                guardrail.validate_protected_closeout_transition(
                    self.task,
                    transition="review_approved",
                )

    def test_manifest_tamper_and_task_reference_tamper_are_rejected(self) -> None:
        verdict = self._issue()
        self.manifest["deployment"]["identity_admission"]["frontend_sha"] = "e" * 40
        self._write_manifest()
        with mock.patch.dict(os.environ, self.env, clear=False):
            with self.assertRaisesRegex(Exception, "binding mismatch"):
                guardrail.validate_protected_closeout_transition(
                    self.task,
                    transition="review_approved",
                )

        self.manifest["deployment"]["identity_admission"]["frontend_sha"] = "c" * 40
        self._write_manifest()
        self.task["protected_closeout_verdict"] = {
            "verdict_id": verdict["verdict_id"],
            "ledger_entry_id": "tampered-ledger-entry",
        }
        with mock.patch.dict(os.environ, self.env, clear=False):
            with self.assertRaisesRegex(Exception, "ledger entry reference mismatch"):
                guardrail.validate_protected_closeout_transition(
                    self.task,
                    transition="review_approved",
                )

    def test_caller_environment_cannot_override_protected_policy(self) -> None:
        self._issue()
        attacker_policy = self.protected_root / "attacker-policy.json"
        attacker_policy.write_text(
            self.policy_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        unsafe_env = {
            **self.env,
            "PANTHEON_PRODUCT_CLOSEOUT_ALLOW_TEST_CONFIG": "1",
            "PANTHEON_PRODUCT_CLOSEOUT_TEST_POLICY_PATH": str(attacker_policy),
        }
        missing_default = self.protected_root / "missing-default-policy.json"
        with (
            mock.patch.object(
                self.module,
                "DEFAULT_PROTECTED_POLICY_PATH",
                missing_default,
            ),
            mock.patch.dict(os.environ, unsafe_env, clear=True),
        ):
            with self.assertRaisesRegex(Exception, "policy is missing"):
                guardrail.validate_protected_closeout_transition(
                    self.task,
                    transition="review_approved",
                )

            accepted = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
                policy_path=self.policy_path,
            )
        self.assertEqual(
            accepted["verdict_id"],
            self.env["PANTHEON_PRODUCT_CLOSEOUT_VERDICT_ID"],
        )

    def test_review_file_parent_traversal_is_rejected(self) -> None:
        outside = self.temp_root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with (
            mock.patch.dict(os.environ, self.env, clear=False),
            self.assertRaisesRegex(RuntimeError, "parent traversal"),
        ):
            guardrail._safe_protected_closeout_artifact("../outside.json")

    def _relocate_manifest_to(self, root: Path) -> Path:
        """Move the manifest out of the status root into ``root``."""

        relocated = root / "evidence" / "closeout.json"
        relocated.parent.mkdir(parents=True, exist_ok=True)
        relocated.write_text(
            self.manifest_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.manifest_path.unlink()
        self.manifest_path = relocated
        return relocated

    def test_review_approved_resolves_manifest_from_bound_task_worktree(self) -> None:
        """The reviewed manifest lives in the worktree, not the status root."""

        worktree_root = self.temp_root / "task-worktree"
        worktree_root.mkdir()
        self._relocate_manifest_to(worktree_root)
        verdict = self._issue()
        split_root_env = {
            **self.env,
            "PANTHEON_WORKTREE_ROOT": str(worktree_root),
            "ORCH_WORKSPACE_PATH": str(worktree_root),
        }

        with mock.patch.dict(os.environ, split_root_env, clear=False):
            reference = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

        self.assertEqual(reference["verdict_id"], verdict["verdict_id"])

    def test_review_approved_resolves_manifest_from_command_root(self) -> None:
        """A merged manifest is still readable from the immutable command root."""

        command_root = self.temp_root / "command-root"
        command_root.mkdir()
        self._relocate_manifest_to(command_root)
        verdict = self._issue()

        with (
            mock.patch.object(guardrail, "ROOT", command_root),
            mock.patch.dict(os.environ, self.env, clear=False),
        ):
            reference = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

        self.assertEqual(reference["verdict_id"], verdict["verdict_id"])

    def test_conflicting_workspace_root_bindings_fail_closed(self) -> None:
        worktree_root = self.temp_root / "task-worktree"
        worktree_root.mkdir()
        self._relocate_manifest_to(worktree_root)
        self._issue()
        conflicting_env = {
            **self.env,
            "PANTHEON_WORKTREE_ROOT": str(worktree_root),
            "ORCH_WORKSPACE_PATH": str(self.temp_root / "other-worktree"),
        }

        with (
            mock.patch.dict(os.environ, conflicting_env, clear=False),
            self.assertRaisesRegex(RuntimeError, "conflicting"),
        ):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

    def test_relative_workspace_root_binding_fails_closed(self) -> None:
        self._issue()
        relative_env = {
            **self.env,
            "PANTHEON_WORKTREE_ROOT": "relative/task-worktree",
        }

        with (
            mock.patch.dict(os.environ, relative_env, clear=False),
            self.assertRaisesRegex(RuntimeError, "must be an absolute path"),
        ):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

    def test_symlinked_manifest_in_bound_worktree_is_rejected(self) -> None:
        self._issue()
        worktree_root = self.temp_root / "task-worktree"
        (worktree_root / "evidence").mkdir(parents=True)
        planted = self.temp_root / "planted.json"
        planted.write_text(
            self.manifest_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (worktree_root / "evidence" / "closeout.json").symlink_to(planted)
        self.manifest_path.unlink()
        split_root_env = {
            **self.env,
            "PANTHEON_WORKTREE_ROOT": str(worktree_root),
        }

        with (
            mock.patch.dict(os.environ, split_root_env, clear=False),
            self.assertRaisesRegex(RuntimeError, "cannot contain a symlink"),
        ):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

    def test_manifest_absent_from_every_trusted_root_reports_search(self) -> None:
        self._issue()
        worktree_root = self.temp_root / "task-worktree"
        worktree_root.mkdir()
        self.manifest_path.unlink()
        split_root_env = {
            **self.env,
            "PANTHEON_WORKTREE_ROOT": str(worktree_root),
        }

        with (
            mock.patch.dict(os.environ, split_root_env, clear=False),
            self.assertRaisesRegex(RuntimeError, "missing or not regular"),
        ):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

        with mock.patch.dict(os.environ, split_root_env, clear=False):
            roots, error = guardrail._protected_closeout_roots()
        self.assertIsNone(error)
        self.assertEqual(roots[0], worktree_root.resolve())
        self.assertIn(self.status_root.resolve(), roots)
        self.assertIn(guardrail.ROOT.resolve(), roots)

    def test_bound_worktree_manifest_takes_precedence_over_status_root(self) -> None:
        """A stale merged copy cannot silently displace the reviewed artifact."""

        worktree_root = self.temp_root / "task-worktree"
        (worktree_root / "evidence").mkdir(parents=True)
        stale = dict(self.manifest)
        stale["deployment"] = {
            **self.manifest["deployment"],
            "identity_admission": {
                **self.manifest["deployment"]["identity_admission"],
                "frontend_sha": "f" * 40,
            },
        }
        self.manifest_path.write_text(json.dumps(stale, sort_keys=True), encoding="utf-8")
        reviewed = worktree_root / "evidence" / "closeout.json"
        reviewed.write_text(json.dumps(self.manifest, sort_keys=True), encoding="utf-8")
        self.manifest_path = reviewed
        verdict = self._issue()
        split_root_env = {
            **self.env,
            "PANTHEON_WORKTREE_ROOT": str(worktree_root),
        }

        with mock.patch.dict(os.environ, split_root_env, clear=False):
            reference = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

        self.assertEqual(reference["verdict_id"], verdict["verdict_id"])

    def test_revoked_review_verdict_can_be_replaced_by_new_human_ops_verdict(
        self,
    ) -> None:
        first = self._issue()
        with mock.patch.dict(os.environ, self.env, clear=False):
            first_ref = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )
        self.task["protected_closeout_verdict"] = first_ref
        self.module.ProductCloseoutVerdictService(
            ledger=self.ledger,
            policy=self.policy,
            private_key=self.private_key,
            key_id=self.key_id,
        ).revoke(
            first["verdict_id"],
            self.module.HumanOpsIdentity(
                actor_id="ops-human-001",
                actor_role="ops",
                authenticated=True,
                mfa_verified=True,
            ),
            reason="deployment evidence was refreshed",
        )
        replacement = self.issuer.issue(
            self._binding(),
            self.module.HumanOpsIdentity(
                actor_id="ops-human-001",
                actor_role="ops",
                authenticated=True,
                mfa_verified=True,
            ),
            decision="approved",
            ttl_seconds=900,
        )
        replacement_env = {
            **self.env,
            "PANTHEON_PRODUCT_CLOSEOUT_VERDICT_ID": replacement["verdict_id"],
        }

        with mock.patch.dict(os.environ, replacement_env, clear=False):
            replacement_ref = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

        self.assertEqual(
            replacement_ref["superseded_verdict_id"],
            first["verdict_id"],
        )
        self.assertEqual(
            replacement_ref["verdict_id"],
            replacement["verdict_id"],
        )

    def _human_ops(self):
        return self.module.HumanOpsIdentity(
            actor_id="ops-human-001",
            actor_role="ops",
            authenticated=True,
            mfa_verified=True,
        )

    def _rehome_ledger_under(self, directory: Path) -> Path:
        """Point the protected policy at a ledger inside ``directory``.

        The policy file itself stays external; only the ledger moves, which is
        the configuration the independent reviewer probed.
        """

        directory.mkdir(parents=True, exist_ok=True)
        ledger_path = directory / "verdict-ledger.jsonl"
        self.ledger = self.module.ProtectedVerdictLedger(ledger_path)
        self.issuer = self.module.ProductCloseoutVerdictService(
            ledger=self.ledger,
            policy=self.policy,
            private_key=self.private_key,
            key_id=self.key_id,
        )
        policy_document = json.loads(self.policy_path.read_text(encoding="utf-8"))
        policy_document["ledger_path"] = str(ledger_path)
        self.policy_path.write_text(
            json.dumps(policy_document), encoding="utf-8"
        )
        return ledger_path

    def _revoke(self, verdict_id: str) -> None:
        self.module.ProductCloseoutVerdictService(
            ledger=self.ledger,
            policy=self.policy,
            private_key=self.private_key,
            key_id=self.key_id,
        ).revoke(
            verdict_id,
            self._human_ops(),
            reason="independent review withdrew the approval",
        )

    def _truncate_ledger_to_issue_record(self) -> None:
        """Drop every ledger row after the signed issue record.

        The ledger is a hash chain, so any prefix of it is still internally
        consistent.  A candidate that can write the file can therefore erase a
        revocation or a consumption without breaking verification: only the
        location of the file keeps this out of reach.
        """

        rows = self.ledger.path.read_text(encoding="utf-8").splitlines()
        issue_rows = [
            row for row in rows if json.loads(row).get("record_type") == "issued"
        ]
        self.assertEqual(len(issue_rows), 1, rows)
        self.ledger.path.write_text(issue_rows[0] + "\n", encoding="utf-8")

    def test_forbidden_roots_cover_every_split_root_binding(self) -> None:
        worktree_root = self.temp_root / "task-worktree"
        worktree_root.mkdir()
        bound_env = {
            **self.env,
            "PANTHEON_WORKTREE_ROOT": str(worktree_root),
            "ORCH_WORKSPACE_PATH": str(worktree_root),
        }

        with mock.patch.dict(os.environ, bound_env, clear=False):
            forbidden = guardrail._protected_forbidden_roots()

        self.assertIn(worktree_root.resolve(), forbidden)
        self.assertIn(guardrail.ROOT.resolve(), forbidden)
        self.assertIn(self.status_root.resolve(), forbidden)

    def test_ledger_inside_bound_worktree_is_rejected(self) -> None:
        """A candidate-writable ledger is refused under either worker binding."""

        worktree_root = self.temp_root / "task-worktree"
        self._rehome_ledger_under(worktree_root / "state")
        verdict = self._issue()

        # Control: with no worker binding the same ledger location verifies,
        # so the rejections below come from the authority boundary and not
        # from an unrelated failure in the fixture.
        with mock.patch.dict(os.environ, self.env, clear=False):
            accepted = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )
        self.assertEqual(accepted["verdict_id"], verdict["verdict_id"])

        for env_name in ("PANTHEON_WORKTREE_ROOT", "ORCH_WORKSPACE_PATH"):
            with self.subTest(binding=env_name):
                bound_env = {**self.env, env_name: str(worktree_root)}
                with (
                    mock.patch.dict(os.environ, bound_env, clear=False),
                    self.assertRaisesRegex(
                        Exception, "outside candidate-controlled root"
                    ),
                ):
                    guardrail.validate_protected_closeout_transition(
                        self.task,
                        transition="review_approved",
                    )

    def test_policy_inside_bound_worktree_is_rejected(self) -> None:
        worktree_root = self.temp_root / "task-worktree"
        planted_policy = worktree_root / "verdict-policy.json"
        planted_policy.parent.mkdir(parents=True, exist_ok=True)
        planted_policy.write_text(
            self.policy_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self._issue()

        for env_name in ("PANTHEON_WORKTREE_ROOT", "ORCH_WORKSPACE_PATH"):
            with self.subTest(binding=env_name):
                bound_env = {**self.env, env_name: str(worktree_root)}
                with (
                    mock.patch.dict(os.environ, bound_env, clear=False),
                    self.assertRaisesRegex(
                        Exception, "outside candidate-controlled root"
                    ),
                ):
                    guardrail.validate_protected_closeout_transition(
                        self.task,
                        transition="review_approved",
                        policy_path=planted_policy,
                    )

    def test_candidate_tail_truncation_cannot_restore_revoked_verdict(self) -> None:
        """The reviewer's probe: truncation resurrects an approval, so the
        ledger must never be reachable from a candidate-controlled root."""

        worktree_root = self.temp_root / "task-worktree"
        self._rehome_ledger_under(worktree_root / "state")
        verdict = self._issue()
        unbound_env = dict(self.env)

        with mock.patch.dict(os.environ, unbound_env, clear=False):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

        self._revoke(verdict["verdict_id"])
        with (
            mock.patch.dict(os.environ, unbound_env, clear=False),
            self.assertRaisesRegex(Exception, "revoked"),
        ):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

        # Signature and hash-chain checks cannot see a removed suffix, so
        # write access to the ledger alone is enough to undo the revocation.
        self._truncate_ledger_to_issue_record()
        with mock.patch.dict(os.environ, unbound_env, clear=False):
            restored = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )
        self.assertEqual(restored["verdict_id"], verdict["verdict_id"])

        # Binding the worktree removes that write access from the trust model:
        # the configuration is refused before any verdict is read.
        for env_name in ("PANTHEON_WORKTREE_ROOT", "ORCH_WORKSPACE_PATH"):
            with self.subTest(binding=env_name):
                bound_env = {**self.env, env_name: str(worktree_root)}
                with (
                    mock.patch.dict(os.environ, bound_env, clear=False),
                    self.assertRaisesRegex(
                        Exception, "outside candidate-controlled root"
                    ),
                ):
                    guardrail.validate_protected_closeout_transition(
                        self.task,
                        transition="review_approved",
                    )

    def test_candidate_tail_truncation_cannot_restore_consumed_verdict(self) -> None:
        worktree_root = self.temp_root / "task-worktree"
        self._rehome_ledger_under(worktree_root / "state")
        self._issue()
        unbound_env = dict(self.env)

        with mock.patch.dict(os.environ, unbound_env, clear=False):
            consumed = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="done",
                consume=True,
                transition_actor="Codex",
            )
        self.assertIn("consumption_record_id", consumed)

        self._truncate_ledger_to_issue_record()
        bound_env = {**self.env, "PANTHEON_WORKTREE_ROOT": str(worktree_root)}
        with (
            mock.patch.dict(os.environ, bound_env, clear=False),
            self.assertRaisesRegex(Exception, "outside candidate-controlled root"),
        ):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

    @contextlib.contextmanager
    def _supervisor_lease(
        self,
        worktree_root: Path,
        *,
        register_worker: bool = True,
        env: dict[str, str] | None = None,
    ):
        """Hold a valid run lease whose worktree is only known centrally.

        The reviewer's replay used exactly this shape: a live ``ORCH_RUN_ID``
        with both workspace variables erased.  The lease still names the task
        worktree in supervisor runtime state, which the candidate cannot write.
        """

        import ai_status

        run_id = "l12-signoff-guard-run"
        task_id = self.task["id"]
        workers: dict[str, object] = {}
        leases: dict[str, object] = {}
        if register_worker:
            workers[run_id] = {
                "run_id": run_id,
                "task_id": task_id,
                "status": "running",
                "workspace_path": str(worktree_root),
                "status_root": str(self.status_root),
            }
            leases[task_id] = {
                "task_id": task_id,
                "path": str(worktree_root),
                "status_root": str(self.status_root),
            }
        runtime_state = {
            "workers": workers,
            "worker_worktrees": {"leases": leases},
        }
        lease_env = {**self.env, "ORCH_RUN_ID": run_id, **(env or {})}
        with (
            mock.patch.dict(os.environ, lease_env, clear=False),
            mock.patch.object(ai_status, "STATUS_ROOT", self.status_root),
            mock.patch.object(ai_status, "load_config", return_value={}),
            mock.patch.object(
                ai_status, "load_runtime_state_snapshot", return_value=runtime_state
            ),
        ):
            yield

    def test_forbidden_roots_include_leased_root_without_env_binding(self) -> None:
        """The boundary must not shrink when the candidate erases its env."""

        worktree_root = self.temp_root / "task-worktree"
        worktree_root.mkdir()

        with self._supervisor_lease(worktree_root):
            forbidden = guardrail._protected_forbidden_roots()

        self.assertIn(worktree_root.resolve(), forbidden)
        self.assertIn(guardrail.ROOT.resolve(), forbidden)
        self.assertIn(self.status_root.resolve(), forbidden)

    def test_ledger_inside_leased_root_is_rejected_without_env_binding(self) -> None:
        worktree_root = self.temp_root / "task-worktree"
        self._rehome_ledger_under(worktree_root / "state")
        verdict = self._issue()

        # Control: with no run lease and no environment binding the same
        # ledger location verifies, which is the bypass being closed.
        with mock.patch.dict(os.environ, self.env, clear=False):
            accepted = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )
        self.assertEqual(accepted["verdict_id"], verdict["verdict_id"])

        with (
            self._supervisor_lease(worktree_root),
            self.assertRaisesRegex(Exception, "outside candidate-controlled root"),
        ):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

    def test_policy_inside_leased_root_is_rejected_without_env_binding(self) -> None:
        worktree_root = self.temp_root / "task-worktree"
        planted_policy = worktree_root / "verdict-policy.json"
        planted_policy.parent.mkdir(parents=True, exist_ok=True)
        planted_policy.write_text(
            self.policy_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self._issue()

        with (
            self._supervisor_lease(worktree_root),
            self.assertRaisesRegex(Exception, "outside candidate-controlled root"),
        ):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
                policy_path=planted_policy,
            )

    def test_tail_truncation_cannot_restore_revoked_verdict_without_env_binding(
        self,
    ) -> None:
        """The reviewer's sequence-7 replay, run against the leased boundary."""

        worktree_root = self.temp_root / "task-worktree"
        self._rehome_ledger_under(worktree_root / "state")
        verdict = self._issue()

        with mock.patch.dict(os.environ, self.env, clear=False):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

        self._revoke(verdict["verdict_id"])
        self._truncate_ledger_to_issue_record()

        # Without the lease the truncated ledger still verifies, so the
        # rejection below is produced by the canonical authority source.
        with mock.patch.dict(os.environ, self.env, clear=False):
            restored = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )
        self.assertEqual(restored["verdict_id"], verdict["verdict_id"])

        with (
            self._supervisor_lease(worktree_root),
            self.assertRaisesRegex(Exception, "outside candidate-controlled root"),
        ):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

    def test_tail_truncation_cannot_restore_consumed_verdict_without_env_binding(
        self,
    ) -> None:
        worktree_root = self.temp_root / "task-worktree"
        self._rehome_ledger_under(worktree_root / "state")
        self._issue()

        with mock.patch.dict(os.environ, self.env, clear=False):
            consumed = guardrail.validate_protected_closeout_transition(
                self.task,
                transition="done",
                consume=True,
                transition_actor="Codex",
            )
        self.assertIn("consumption_record_id", consumed)

        self._truncate_ledger_to_issue_record()
        with (
            self._supervisor_lease(worktree_root),
            self.assertRaisesRegex(Exception, "outside candidate-controlled root"),
        ):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

    def test_unresolvable_run_lease_fails_before_policy_or_ledger_access(self) -> None:
        """An active run whose lease is missing must not degrade to no boundary."""

        worktree_root = self.temp_root / "task-worktree"
        self._rehome_ledger_under(worktree_root / "state")
        self._issue()
        missing_policy = self.protected_root / "missing-default-policy.json"

        with (
            mock.patch.object(
                self.module, "DEFAULT_PROTECTED_POLICY_PATH", missing_policy
            ),
            self._supervisor_lease(worktree_root, register_worker=False),
            self.assertRaisesRegex(
                RuntimeError, "cannot resolve the supervisor-leased workspace root"
            ),
        ):
            guardrail.validate_protected_closeout_transition(
                self.task,
                transition="review_approved",
            )

    def test_untrustworthy_binding_fails_before_policy_or_ledger_access(self) -> None:
        """Binding validation must not be reached only via manifest lookup."""

        self._issue()
        missing_policy = self.protected_root / "missing-default-policy.json"
        broken_bindings = (
            (
                {
                    "PANTHEON_WORKTREE_ROOT": str(self.temp_root / "task-worktree"),
                    "ORCH_WORKSPACE_PATH": str(self.temp_root / "other-worktree"),
                },
                "conflicting",
            ),
            ({"PANTHEON_WORKTREE_ROOT": "relative/task-worktree"}, "must be an absolute path"),
        )

        for overrides, expected in broken_bindings:
            with self.subTest(binding=expected):
                with (
                    mock.patch.object(
                        self.module,
                        "DEFAULT_PROTECTED_POLICY_PATH",
                        missing_policy,
                    ),
                    mock.patch.dict(
                        os.environ, {**self.env, **overrides}, clear=False
                    ),
                    self.assertRaisesRegex(RuntimeError, expected),
                ):
                    guardrail.validate_protected_closeout_transition(
                        self.task,
                        transition="review_approved",
                    )


if __name__ == "__main__":
    unittest.main()
