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
        pr_state: str = "OPEN",
        merge_state: str = "CLEAN",
        auto_merge_request: Mapping[str, Any] | None = None,
        base_sha: str = "c" * 40,
        compare_status: str = "ahead",
        behind_by: int = 0,
        manifest_payload: Mapping[str, Any] | None = None,
        base_manifest_payload: Mapping[str, Any] | None = None,
        base_manifest_missing: bool = False,
        base_manifest_error: str = "",
        pr_files: Sequence[Mapping[str, Any]] | None = None,
        is_draft: bool = False,
    ) -> None:
        self.review_error = review_error
        self.context_required = context_required
        self.actual_head = actual_head
        self.dispatch_error = dispatch_error
        self.pr_state = pr_state
        self.merge_state = merge_state
        self.auto_merge_request = auto_merge_request
        self.base_sha = base_sha
        self.compare_status = compare_status
        self.behind_by = behind_by
        self.manifest_payload = (
            dict(manifest_payload)
            if manifest_payload is not None
            else {"type": "file", "sha": "d" * 40}
        )
        self.base_manifest_payload = (
            dict(base_manifest_payload)
            if base_manifest_payload is not None
            else {"type": "file", "sha": "e" * 40}
        )
        self.base_manifest_missing = base_manifest_missing
        self.base_manifest_error = base_manifest_error
        self.pr_files = [dict(item) for item in (pr_files or [])]
        self.is_draft = is_draft
        self.reviews: list[dict[str, Any]] = []
        self.statuses: list[dict[str, Any]] = []
        self.calls: list[tuple[list[str], Mapping[str, Any] | None]] = []
        self.tag_refs: dict[str, dict[str, Any]] = {}
        self.dispatches: list[dict[str, Any]] = []
        self.commits: dict[str, dict[str, Any]] = {}
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
                "state": self.pr_state,
                "headRefName": "task/AUDIT-001",
                "headRefOid": self.actual_head,
                "baseRefName": "dev",
                "baseRefOid": self.base_sha,
                "isDraft": self.is_draft,
                "mergeStateStatus": self.merge_state,
                "autoMergeRequest": self.auto_merge_request,
            }
        if joined == (
            f"gh api repos/{REPOSITORY}/compare/{self.base_sha}...{self.actual_head}"
        ):
            return {"status": self.compare_status, "behind_by": self.behind_by}
        if joined.startswith(f"gh api repos/{REPOSITORY}/contents/"):
            if joined.endswith(f"?ref={self.base_sha}"):
                if self.base_manifest_missing:
                    raise bridge.GitHubReviewBridgeError("Not Found (HTTP 404)")
                if self.base_manifest_error:
                    raise bridge.GitHubReviewBridgeError(self.base_manifest_error)
                return dict(self.base_manifest_payload)
            return dict(self.manifest_payload)
        if joined.endswith("/pulls/4269/files?per_page=100"):
            return list(self.pr_files)
        commit_prefix = f"repos/{REPOSITORY}/commits/"
        if (
            joined.startswith(f"gh api {commit_prefix}")
            and "/" not in command[-1][len(commit_prefix) :]
        ):
            head = command[-1][len(commit_prefix) :].partition("?")[0]
            payload = self.commits.get(head)
            if payload is None:
                raise bridge.GitHubReviewBridgeError("Not Found (HTTP 404)")
            return dict(payload)
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
    def test_result_evidence_validator_rejects_head_drift(self) -> None:
        runner = FakeRunner()
        result = bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="Exact-head review passed.",
            binding=binding(),
            runner=runner,
        ).as_dict()
        result["head_sha"] = "b" * 40

        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError, "exact-head mismatch"
        ):
            bridge.validate_result_evidence(
                result,
                repository=REPOSITORY,
                actor="Codex2",
                decision="approve",
                binding=binding(),
            )

    def test_result_evidence_validator_rejects_missing_mode_evidence(self) -> None:
        runner = FakeRunner()
        result = bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="Exact-head review passed.",
            binding=binding(),
            runner=runner,
        ).as_dict()
        result.pop("github_review_id")

        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError, "no recognized approve evidence"
        ):
            bridge.validate_result_evidence(
                result,
                repository=REPOSITORY,
                actor="Codex2",
                decision="approve",
                binding=binding(),
            )

    def test_task_brief_only_successor_is_a_narrow_direct_child(self) -> None:
        successor = "b" * 40
        runner = FakeRunner()
        runner.commits[successor] = {
            "sha": successor,
            "parents": [{"sha": HEAD}],
            "files": [
                {
                    "filename": ".orchestrator/task-briefs/audit_001.md",
                    "status": "modified",
                }
            ],
        }

        carried = bridge.task_brief_only_successor(
            repository=REPOSITORY,
            approved_head_sha=HEAD,
            successor_head_sha=successor,
            runner=runner,
        )

        self.assertEqual(
            carried,
            {
                "kind": "task_brief_only_successor",
                "approved_head_sha": HEAD,
                "successor_head_sha": successor,
                "changed_paths": [".orchestrator/task-briefs/audit_001.md"],
            },
        )
        self.assertIn(
            f"?per_page={bridge.COMMIT_FILES_PAGE_SIZE}&page=1",
            runner.calls[0][0][-1],
        )

    def test_task_brief_only_successor_rejects_a_code_change(self) -> None:
        successor = "b" * 40
        runner = FakeRunner()
        runner.commits[successor] = {
            "sha": successor,
            "parents": [{"sha": HEAD}],
            "files": [
                {"filename": ".orchestrator/task-briefs/audit_001.md"},
                {"filename": "scripts/ai_status.py"},
            ],
        }

        carried = bridge.task_brief_only_successor(
            repository=REPOSITORY,
            approved_head_sha=HEAD,
            successor_head_sha=successor,
            runner=runner,
        )

        self.assertIsNone(carried)

    def test_task_brief_only_successor_rejects_a_full_files_page(self) -> None:
        successor = "b" * 40
        runner = FakeRunner()
        runner.commits[successor] = {
            "sha": successor,
            "parents": [{"sha": HEAD}],
            "files": [
                {
                    "filename": (
                        ".orchestrator/task-briefs/"
                        f"audit_{index:03d}.md"
                    )
                }
                for index in range(bridge.COMMIT_FILES_PAGE_SIZE)
            ],
        }

        carried = bridge.task_brief_only_successor(
            repository=REPOSITORY,
            approved_head_sha=HEAD,
            successor_head_sha=successor,
            runner=runner,
        )

        self.assertIsNone(carried)

    def test_task_brief_only_successor_publishes_a_carry_forward_proof(self) -> None:
        successor = "b" * 40
        runner = FakeRunner()
        runner.commits[successor] = {
            "sha": successor,
            "parents": [{"sha": HEAD}],
            "files": [{"filename": ".orchestrator/task-briefs/audit_001.md"}],
        }

        carried = bridge.carry_approval_to_task_brief_only_successor(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            approved_head_sha=HEAD,
            successor_head_sha=successor,
            pr=4269,
            head_branch="task/AUDIT-001",
            base="dev",
            publish=True,
            runner=runner,
        )

        expected_ref = f"refs/tags/pantheon-review/approve/{successor}"
        self.assertIsNotNone(carried)
        self.assertEqual(carried["review_proof_ref"], expected_ref)
        self.assertIn(expected_ref, runner.tag_refs)
        self.assertEqual(
            runner.dispatches,
            [{"ref": "dev", "inputs": {"head_ref": "task/AUDIT-001", "head_sha": successor}}],
        )

    def test_task_brief_carry_forward_dispatch_failure_is_not_ignored(self) -> None:
        successor = "b" * 40
        runner = FakeRunner(dispatch_error="workflow dispatch forbidden")
        carried = {
            "kind": "task_brief_only_successor",
            "approved_head_sha": HEAD,
            "successor_head_sha": successor,
            "changed_paths": [".orchestrator/task-briefs/audit_001.md"],
        }

        with self.assertRaisesRegex(bridge.GitHubReviewBridgeError, "workflow dispatch forbidden"):
            bridge.publish_task_brief_only_successor_proof(
                repository=REPOSITORY,
                task_id="AUDIT-001",
                actor="Codex2",
                carried=carried,
                pr=4269,
                head_branch="task/AUDIT-001",
                base="dev",
                runner=runner,
            )

        self.assertIn(f"refs/tags/pantheon-review/approve/{successor}", runner.tag_refs)

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

    def test_same_intent_nonce_replays_review_and_status_idempotently(self) -> None:
        runner = FakeRunner()
        nonce = "1" * 32
        first = bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="First phase-two attempt.",
            binding=binding(),
            intent_nonce=nonce,
            runner=runner,
        )
        second = bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="Crash retry with the same nonce.",
            binding=binding(),
            intent_nonce=nonce,
            runner=runner,
        )

        self.assertEqual(first.intent_nonce, nonce)
        self.assertEqual(second.intent_nonce, nonce)
        self.assertEqual(len(runner.reviews), 1)
        self.assertEqual(len(runner.statuses), 1)

    def test_new_intent_nonce_does_not_reuse_orphan_review_authority(self) -> None:
        runner = FakeRunner()
        bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="Orphaned first intent.",
            binding=binding(),
            intent_nonce="1" * 32,
            runner=runner,
        )
        bridge.bridge_review_decision(
            repository=REPOSITORY,
            task_id="AUDIT-001",
            actor="Codex2",
            decision="approve",
            message="Fresh canonical intent.",
            binding=binding(),
            intent_nonce="2" * 32,
            runner=runner,
        )

        self.assertEqual(len(runner.reviews), 2)
        self.assertEqual(len(runner.statuses), 2)

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
            bridge.ReviewBindingMismatch,
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

    def test_handoff_validator_uses_the_same_exact_pr_snapshot(self) -> None:
        runner = FakeRunner()

        validated = bridge.validate_review_binding(
            repository=REPOSITORY,
            binding=binding(),
            runner=runner,
        )

        self.assertEqual(validated.head_sha, HEAD)
        self.assertEqual(validated.pr, 4269)
        mutation_calls = [
            command
            for command, _payload in runner.calls
            if "--method" in command and "POST" in command
        ]
        self.assertEqual(mutation_calls, [])

    def test_review_admission_freezes_manifest_base_and_merge_method(self) -> None:
        admitted = bridge.validate_review_admission(
            repository=REPOSITORY,
            binding=binding(),
            review_file="docs/evidence/AUDIT-001/evidence.json",
            runner=FakeRunner(),
        )

        self.assertEqual(
            admitted.as_dict(),
            {
                **binding(),
                "base_sha": "c" * 40,
                "required_merge_method": "MERGE",
                "evidence_manifest": {
                    "path": "docs/evidence/AUDIT-001/evidence.json",
                    "blob_sha": "d" * 40,
                },
            },
        )

    def test_review_admission_rejects_missing_committed_manifest(self) -> None:
        runner = FakeRunner(manifest_payload={"type": "dir", "sha": "d" * 40})

        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError, "not a committed file"
        ):
            bridge.validate_review_admission(
                repository=REPOSITORY,
                binding=binding(),
                review_file="docs/evidence/AUDIT-001/evidence.json",
                runner=runner,
            )

    def test_review_admission_rejects_manifest_unchanged_from_base(self) -> None:
        runner = FakeRunner(
            base_manifest_payload={"type": "file", "sha": "d" * 40}
        )
        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError, "unchanged from the exact base"
        ):
            bridge.validate_review_admission(
                repository=REPOSITORY,
                binding=binding(),
                review_file="docs/evidence/AUDIT-001/evidence.json",
                runner=runner,
            )

    def test_review_admission_accepts_manifest_absent_from_base(self) -> None:
        admitted = bridge.validate_review_admission(
            repository=REPOSITORY,
            binding=binding(),
            review_file="docs/evidence/AUDIT-001/evidence.json",
            runner=FakeRunner(base_manifest_missing=True),
        )
        self.assertEqual(admitted.manifest_blob_sha, "d" * 40)

    def test_review_admission_uses_exact_pr_files_when_base_lookup_fails(self) -> None:
        path = "docs/evidence/AUDIT-001/evidence.json"
        admitted = bridge.validate_review_admission(
            repository=REPOSITORY,
            binding=binding(),
            review_file=path,
            runner=FakeRunner(
                base_manifest_error="base contents temporarily unavailable",
                pr_files=[{"filename": path, "sha": "d" * 40, "status": "modified"}],
            ),
        )
        self.assertEqual(admitted.manifest_path, path)

    def test_review_admission_rejects_malformed_base_without_exact_pr_file(self) -> None:
        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError, "malformed base contents"
        ):
            bridge.validate_review_admission(
                repository=REPOSITORY,
                binding=binding(),
                review_file="docs/evidence/AUDIT-001/evidence.json",
                runner=FakeRunner(
                    base_manifest_payload={"type": "dir", "sha": "e" * 40}
                ),
            )

    def test_review_admission_rejects_other_pr_file_as_manifest_proof(self) -> None:
        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError, "no exact PR-file change evidence"
        ):
            bridge.validate_review_admission(
                repository=REPOSITORY,
                binding=binding(),
                review_file="docs/evidence/AUDIT-001/evidence.json",
                runner=FakeRunner(
                    base_manifest_error="base contents temporarily unavailable",
                    pr_files=[
                        {
                            "filename": "docs/evidence/OTHER/evidence.json",
                            "sha": "d" * 40,
                            "status": "modified",
                        }
                    ],
                ),
            )

    def test_review_admission_rejects_absolute_manifest_path(self) -> None:
        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError, "repository-relative REVIEW_FILE"
        ):
            bridge.validate_review_admission(
                repository=REPOSITORY,
                binding=binding(),
                review_file="/docs/evidence/AUDIT-001/evidence.json",
                runner=FakeRunner(),
            )

    def test_review_admission_rejects_non_merge_required_method(self) -> None:
        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError, "requires merge method MERGE"
        ):
            bridge.validate_review_admission(
                repository=REPOSITORY,
                binding=binding(),
                review_file="docs/evidence/AUDIT-001/evidence.json",
                required_merge_method="SQUASH",
                runner=FakeRunner(),
            )

    def test_review_admission_rejects_every_armed_auto_merge(self) -> None:
        for method in ("MERGE", "SQUASH"):
            with self.subTest(method=method), self.assertRaisesRegex(
                bridge.GitHubReviewBridgeError, "already has armed auto-merge"
            ):
                bridge.validate_review_admission(
                    repository=REPOSITORY,
                    binding=binding(),
                    review_file="docs/evidence/AUDIT-001/evidence.json",
                    runner=FakeRunner(auto_merge_request={"mergeMethod": method}),
                )

    def test_review_admission_rejects_behind_head(self) -> None:
        with self.assertRaisesRegex(bridge.GitHubReviewBridgeError, "is BEHIND"):
            bridge.validate_review_admission(
                repository=REPOSITORY,
                binding=binding(),
                review_file="docs/evidence/AUDIT-001/evidence.json",
                runner=FakeRunner(merge_state="BEHIND"),
            )

    def test_review_approval_revalidation_rejects_advanced_base(self) -> None:
        frozen = bridge.validate_review_admission(
            repository=REPOSITORY,
            binding=binding(),
            review_file="docs/evidence/AUDIT-001/evidence.json",
            runner=FakeRunner(),
        ).as_dict()

        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError, "does not contain current base"
        ):
            bridge.revalidate_review_admission(
                repository=REPOSITORY,
                delivery_binding=frozen,
                runner=FakeRunner(
                    base_sha="e" * 40,
                    compare_status="diverged",
                    behind_by=1,
                ),
            )

    def test_review_approval_revalidation_rejects_legacy_binding(self) -> None:
        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError, "no frozen evidence manifest"
        ):
            bridge.revalidate_review_admission(
                repository=REPOSITORY,
                delivery_binding=binding(),
                runner=FakeRunner(),
            )

    def test_review_approval_allows_new_base_already_contained_in_head(self) -> None:
        frozen = bridge.validate_review_admission(
            repository=REPOSITORY,
            binding=binding(),
            review_file="docs/evidence/AUDIT-001/evidence.json",
            runner=FakeRunner(),
        ).as_dict()

        current = bridge.revalidate_review_admission(
            repository=REPOSITORY,
            delivery_binding=frozen,
            runner=FakeRunner(base_sha="e" * 40, compare_status="ahead", behind_by=0),
        )

        self.assertEqual(current.base_sha, "e" * 40)
        self.assertEqual(current.manifest_blob_sha, "d" * 40)

    def test_normal_approve_rejects_pr_already_merged(self) -> None:
        runner = FakeRunner(pr_state="MERGED")
        with self.assertRaisesRegex(bridge.ReviewBindingMismatch, "expected open"):
            bridge.bridge_review_decision(
                repository=REPOSITORY,
                task_id="AUDIT-001",
                actor="Codex2",
                decision="approve",
                message="Merged PR must use explicit reconciliation.",
                binding=binding(),
                runner=runner,
            )

    def test_approve_fails_when_pr_is_closed_unmerged(self) -> None:
        runner = FakeRunner(pr_state="CLOSED")

        with self.assertRaisesRegex(
            bridge.GitHubReviewBridgeError,
            "expected open",
        ):
            bridge.bridge_review_decision(
                repository=REPOSITORY,
                task_id="AUDIT-001",
                actor="Codex2",
                decision="approve",
                message="Abandoned PR.",
                binding=binding(),
                runner=runner,
            )

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
                "ref": "dev",
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
