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
        dispatch_error: str = "",
    ) -> None:
        self.review_error = review_error
        self.context_required = context_required
        self.actual_head = actual_head
        self.dispatch_error = dispatch_error
        self.reviews: list[dict[str, Any]] = []
        self.statuses: list[dict[str, Any]] = []
        self.calls: list[tuple[list[str], Mapping[str, Any] | None]] = []
        self.tag_refs: dict[str, dict[str, Any]] = {}
        self.dispatches: list[dict[str, Any]] = []
        self._next_tag_sha = 200

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
        if joined.startswith(f"gh api repos/{REPOSITORY}/git/refs/tags/") and "--method" not in joined:
            tag_name = command[-1].rsplit("git/refs/tags/", 1)[-1].replace("%2F", "/")
            ref = f"refs/tags/{tag_name}"
            existing = self.tag_refs.get(ref)
            if existing is None:
                raise bridge.GitHubReviewBridgeError("Not Found (HTTP 404)")
            return dict(existing)
        if f"repos/{REPOSITORY}/git/tags" in joined and "--method POST" in joined:
            assert payload is not None
            self._next_tag_sha += 1
            return {"sha": f"tagobj{self._next_tag_sha}", "tag": payload["tag"], "message": payload["message"]}
        if f"repos/{REPOSITORY}/git/refs" in joined and "--method POST" in joined and "refs%2Ftags" not in joined:
            assert payload is not None
            ref_payload = {"ref": payload["ref"], "object": {"sha": payload["sha"], "type": "tag"}}
            self.tag_refs[payload["ref"]] = ref_payload
            return dict(ref_payload)
        if (
            f"repos/{REPOSITORY}/actions/workflows/"
            f"{bridge.CANONICAL_REVIEW_GATE_WORKFLOW_FILE}/dispatches" in joined
            and "--method POST" in joined
        ):
            if self.dispatch_error:
                raise bridge.GitHubReviewBridgeError(self.dispatch_error)
            assert payload is not None
            self.dispatches.append(dict(payload))
            return None
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
        expected_ref = f"refs/tags/pantheon-review/approve/{HEAD}"
        self.assertEqual(result.review_proof_ref, expected_ref)
        self.assertIn(expected_ref, runner.tag_refs)

    def test_approve_proof_tag_is_idempotent_on_retry(self) -> None:
        """A retried approve on the same head must not fail because the tag
        already exists (SUP-REVIEW-GATE-GIT-NATIVE-PROOF-20260804)."""

        runner = FakeRunner()
        first = bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="First attempt.",
            binding=binding(),
            runner=runner,
        )
        tag_calls_after_first = sum(
            1 for command, _ in runner.calls if "git/tags" in " ".join(command) and "POST" in " ".join(command)
        )
        second = bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="Retried attempt, same head.",
            binding=binding(),
            runner=runner,
        )
        tag_calls_after_second = sum(
            1 for command, _ in runner.calls if "git/tags" in " ".join(command) and "POST" in " ".join(command)
        )
        self.assertEqual(first.review_proof_ref, second.review_proof_ref)
        self.assertEqual(tag_calls_after_first, tag_calls_after_second)

    def test_reopen_pushes_a_distinct_proof_tag_namespace(self) -> None:
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
        self.assertEqual(result.review_proof_ref, f"refs/tags/pantheon-review/reopen/{HEAD}")

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

    def test_approve_dispatches_canonical_review_gate_workflow(self) -> None:
        """SUP-REVIEW-GATE-DISPATCH-RETRIGGER-20260805: pushing the tag alone
        does not satisfy the required check -- GitHub pins that context to
        the workflow's own run identity, not a personal-token status post.
        Approve must explicitly wake the workflow so it re-reads the tag."""

        runner = FakeRunner()
        bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="Exact-head review passed.",
            binding=binding(),
            runner=runner,
        )

        self.assertEqual(len(runner.dispatches), 1)
        self.assertEqual(
            runner.dispatches[0],
            {
                "ref": "task/AUDIT-001",
                "inputs": {"head_ref": "task/AUDIT-001", "head_sha": HEAD},
            },
        )

    def test_reopen_does_not_dispatch_canonical_review_gate_workflow(self) -> None:
        runner = FakeRunner(
            review_error="Can not request changes on your own pull request",
            context_required=True,
        )
        bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="reopen",
            message="Matrix row is stale.",
            binding=binding(),
            runner=runner,
        )

        self.assertEqual(runner.dispatches, [])

    def test_approve_survives_a_failed_workflow_dispatch(self) -> None:
        """The tag is the durable proof; a dispatch failure (e.g. transient
        API error) must not turn a real, recorded approval into an error."""

        runner = FakeRunner(dispatch_error="GitHub API rate limited")
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
        expected_ref = f"refs/tags/pantheon-review/approve/{HEAD}"
        self.assertEqual(result.review_proof_ref, expected_ref)

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
