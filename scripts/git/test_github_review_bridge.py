#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import github_review_bridge as bridge


HEAD = "a" * 40
REPOSITORY = "ajoe734/pantheon"
PR_URL = "https://github.com/ajoe734/pantheon/pull/4269"


class FakeRunner:
    def __init__(
        self,
        *,
        review_error: str = "",
        context_required: bool = True,
        actual_head: str = HEAD,
    ) -> None:
        self.review_error = review_error
        self.context_required = context_required
        self.actual_head = actual_head
        self.reviews: list[dict[str, Any]] = []
        self.statuses: list[dict[str, Any]] = []
        self.calls: list[tuple[list[str], Mapping[str, Any] | None]] = []

    def run_json(
        self,
        args: Sequence[str],
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        command = [str(arg) for arg in args]
        self.calls.append((command, payload))
        joined = " ".join(command)
        if command[:3] == ["gh", "pr", "view"]:
            return {
                "number": 4269,
                "url": PR_URL,
                "state": "OPEN",
                "headRefName": "task/AUDIT-001",
                "headRefOid": self.actual_head,
                "baseRefName": "dev",
            }
        if joined.endswith("/pulls/4269/reviews?per_page=100"):
            return list(self.reviews)
        if "/pulls/4269/reviews" in joined and "--method POST" in joined:
            if self.review_error:
                raise bridge.GitHubReviewBridgeError(self.review_error)
            assert payload is not None
            self.reviews.append(
                {
                    "id": 91,
                    "state": (
                        "APPROVED"
                        if payload["event"] == "APPROVE"
                        else "CHANGES_REQUESTED"
                    ),
                    "commit_id": payload["commit_id"],
                    "body": payload["body"],
                }
            )
            return dict(self.reviews[-1])
        if joined.endswith("/protection/required_status_checks"):
            contexts = [bridge.CANONICAL_REVIEW_CONTEXT] if self.context_required else []
            return {"contexts": contexts, "checks": []}
        if joined.endswith(f"/commits/{HEAD}/statuses?per_page=100"):
            return list(reversed(self.statuses))
        if f"/statuses/{HEAD}" in joined and "--method POST" in joined:
            assert payload is not None
            self.statuses.append(
                {
                    "id": 101,
                    "state": payload["state"],
                    "context": payload["context"],
                    "target_url": payload["target_url"],
                }
            )
            return dict(self.statuses[-1])
        raise AssertionError(f"unexpected fake gh call: {command}")


def binding() -> dict[str, Any]:
    return {
        "pr": 4269,
        "head_sha": HEAD,
        "head_branch": "task/AUDIT-001",
        "base": "dev",
    }


class GitHubReviewBridgeTests(unittest.TestCase):
    def test_approve_records_real_review_and_required_status(self) -> None:
        runner = FakeRunner()

        result = bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="Exact-head review passed.",
            binding=binding(),
            runner=runner,
        )

        self.assertEqual(result.mode, "pull_request_review_and_required_status")
        self.assertEqual(result.github_review_id, 91)
        self.assertEqual(result.status_context, bridge.CANONICAL_REVIEW_CONTEXT)
        self.assertEqual(result.status_state, "success")
        self.assertEqual(runner.reviews[0]["commit_id"], HEAD)
        self.assertEqual(runner.statuses[0]["state"], "success")

    def test_self_review_failure_uses_only_required_policy_status(self) -> None:
        runner = FakeRunner(
            review_error="Can not approve your own pull request",
            context_required=True,
        )

        result = bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="Independent fleet review passed.",
            binding=binding(),
            runner=runner,
        )

        self.assertEqual(result.mode, "required_commit_status")
        self.assertIsNone(result.github_review_id)
        self.assertEqual(result.status_state, "success")
        self.assertIn("approve your own", result.review_error)

    def test_internal_approval_fails_when_no_github_path_is_recognized(self) -> None:
        runner = FakeRunner(
            review_error="Can not approve your own pull request",
            context_required=False,
        )

        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError,
            "not recorded as a GitHub review",
        ):
            bridge.bridge_review_decision(
                repository=REPOSITORY,
                task_id="AUDIT-001",
                actor="Codex2",
                decision="approve",
                message="Internal only.",
                binding=binding(),
                runner=runner,
            )

        self.assertEqual(runner.statuses, [])

    def test_reopen_records_failure_status_when_request_changes_is_rejected(self) -> None:
        runner = FakeRunner(
            review_error="Can not request changes on your own pull request",
            context_required=True,
        )

        result = bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="reopen",
            message="Matrix row is stale.",
            binding=binding(),
            runner=runner,
        )

        self.assertEqual(result.mode, "required_commit_status")
        self.assertEqual(result.status_state, "failure")
        self.assertEqual(runner.statuses[0]["state"], "failure")

    def test_head_mismatch_fails_before_any_github_write(self) -> None:
        runner = FakeRunner(actual_head="b" * 40)

        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError,
            "no longer matches reviewed identity",
        ):
            bridge.bridge_review_decision(
                repository=REPOSITORY,
                task_id="AUDIT-001",
                actor="Codex2",
                decision="approve",
                message="Stale review.",
                binding=binding(),
                runner=runner,
            )

        mutation_calls = [
            command
            for command, _payload in runner.calls
            if "--method" in command and "POST" in command
        ]
        self.assertEqual(mutation_calls, [])

    def test_matching_status_is_idempotently_reused(self) -> None:
        runner = FakeRunner(
            review_error="Can not approve your own pull request",
            context_required=True,
        )
        runner.statuses.append(
            {
                "id": 77,
                "state": "success",
                "context": bridge.CANONICAL_REVIEW_CONTEXT,
                "target_url": PR_URL,
            }
        )

        result = bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="Already recorded.",
            binding=binding(),
            runner=runner,
        )

        self.assertEqual(result.status_id, 77)
        status_posts = [
            command
            for command, _payload in runner.calls
            if f"/statuses/{HEAD}" in " ".join(command) and "--method" in command
        ]
        self.assertEqual(status_posts, [])


if __name__ == "__main__":
    unittest.main()
