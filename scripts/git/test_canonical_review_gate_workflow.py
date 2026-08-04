#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import canonical_review_gate_ci as gate_ci
from task_review_merge_gate import GateDecision, TaskReviewGateError


def _decision(*, allow: bool, policy: str = "review_before_merge", reason: str = "ok") -> GateDecision:
    return GateDecision(
        allow_merge=allow,
        allow_auto_merge=False,
        task_id="SUP-X",
        policy=policy,
        reason=reason,
        detail="detail",
    )


class ResolveTaskIdTests(unittest.TestCase):
    def test_matches_task_branch_prefix(self) -> None:
        self.assertEqual(gate_ci.resolve_task_id("task/SUP-DISPATCH-EXPLAIN-TOOL-20260804"), "SUP-DISPATCH-EXPLAIN-TOOL-20260804")

    def test_rejects_non_task_branch(self) -> None:
        self.assertIsNone(gate_ci.resolve_task_id("feature/some-branch"))
        self.assertIsNone(gate_ci.resolve_task_id("dev"))
        self.assertIsNone(gate_ci.resolve_task_id(""))

    def test_rejects_prefix_with_no_id(self) -> None:
        self.assertIsNone(gate_ci.resolve_task_id("task/"))

    def test_honors_custom_prefix(self) -> None:
        self.assertEqual(
            gate_ci.resolve_task_id("wk/FOO-1", prefix="wk/"),
            "FOO-1",
        )


class BuildStatusPayloadTests(unittest.TestCase):
    """These are the cases that were silently unrepresented before this
    module existed: SUP-REVIEW-PIPELINE-INTEGRITY-20260804 requires that
    every one of them yields an explicit posted status, never nothing."""

    def test_non_task_branch_fails_closed_with_explicit_reason(self) -> None:
        payload = gate_ci.build_status_payload(head_ref="feature/x", pr={"number": 1})
        self.assertEqual(payload["state"], "failure")
        self.assertEqual(payload["context"], "Pantheon canonical review gate")
        self.assertIn("does not match", payload["description"])

    def test_missing_pr_payload_fails_closed(self) -> None:
        payload = gate_ci.build_status_payload(head_ref="task/SUP-X", pr=None)
        self.assertEqual(payload["state"], "failure")
        self.assertIn("SUP-X", payload["description"])

    def test_unregistered_task_fails_closed_via_gate_error(self) -> None:
        with mock.patch.object(
            gate_ci, "gate_for_task", side_effect=TaskReviewGateError("canonical task state for SUP-X is missing")
        ):
            payload = gate_ci.build_status_payload(head_ref="task/SUP-X", pr={"number": 1})
        self.assertEqual(payload["state"], "failure")
        self.assertIn("gate evaluation failed", payload["description"])

    def test_allowed_gate_decision_posts_success(self) -> None:
        with mock.patch.object(gate_ci, "gate_for_task", return_value=_decision(allow=True)):
            payload = gate_ci.build_status_payload(head_ref="task/SUP-X", pr={"number": 1})
        self.assertEqual(payload["state"], "success")
        self.assertIn("SUP-X", payload["description"])

    def test_blocked_gate_decision_posts_failure(self) -> None:
        with mock.patch.object(
            gate_ci, "gate_for_task", return_value=_decision(allow=False, reason="head_moved_after_approval")
        ):
            payload = gate_ci.build_status_payload(head_ref="task/SUP-X", pr={"number": 1})
        self.assertEqual(payload["state"], "failure")
        self.assertIn("head_moved_after_approval", payload["description"])

    def test_description_is_truncated_to_github_limit(self) -> None:
        with mock.patch.object(
            gate_ci,
            "gate_for_task",
            return_value=_decision(allow=False, reason="x" * 300),
        ):
            payload = gate_ci.build_status_payload(head_ref="task/SUP-X", pr={"number": 1})
        self.assertLessEqual(len(payload["description"]), 140)


class MainDryRunTests(unittest.TestCase):
    """`--dry-run` must never shell out to `gh`, so these exercise the real
    CLI wiring without live GitHub credentials."""

    def test_dry_run_never_calls_gh_for_unregistered_branch(self) -> None:
        with mock.patch.object(gate_ci, "_run_gh_json") as run_gh_json, mock.patch.object(
            gate_ci, "_post_status"
        ) as post_status:
            exit_code = gate_ci.main(
                [
                    "--repo",
                    "ajoe734/pantheon",
                    "--pr-number",
                    "1",
                    "--head-ref",
                    "feature/not-a-task",
                    "--head-sha",
                    "deadbeef",
                    "--dry-run",
                ]
            )
        run_gh_json.assert_not_called()
        post_status.assert_not_called()
        self.assertEqual(exit_code, 1)

    def test_dry_run_still_evaluates_task_branch_via_gh_pr_view(self) -> None:
        with (
            mock.patch.object(gate_ci, "_run_gh_json", return_value={"number": 1}) as run_gh_json,
            mock.patch.object(gate_ci, "_post_status") as post_status,
            mock.patch.object(gate_ci, "gate_for_task", return_value=_decision(allow=True)),
        ):
            exit_code = gate_ci.main(
                [
                    "--repo",
                    "ajoe734/pantheon",
                    "--pr-number",
                    "1",
                    "--head-ref",
                    "task/SUP-X",
                    "--head-sha",
                    "deadbeef",
                    "--dry-run",
                ]
            )
        run_gh_json.assert_called_once()
        post_status.assert_not_called()
        self.assertEqual(exit_code, 0)

    def test_pr_view_failure_posts_and_returns_infra_failure_code(self) -> None:
        with (
            mock.patch.object(
                gate_ci,
                "_run_gh_json",
                side_effect=subprocess.CalledProcessError(1, ["gh"]),
            ),
            mock.patch.object(gate_ci, "_post_status") as post_status,
        ):
            exit_code = gate_ci.main(
                [
                    "--repo",
                    "ajoe734/pantheon",
                    "--pr-number",
                    "1",
                    "--head-ref",
                    "task/SUP-X",
                    "--head-sha",
                    "deadbeef",
                ]
            )
        post_status.assert_called_once()
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
