"""Fail-closed proofs for the canonical review-before-merge gate.

The regression fixtures replay eight live 2026-07-26 governance failures from
recorded canonical state: the premature auto-merges of Pantheon PRs #4212,
#4213 and #4214, and the five later events on PRs #4217, #4222, #4225
(auto-merge enable, then direct merge) and #4227. They are data only: no test
impersonates the owner or the reviewer, and nothing here writes canonical
status, activity, or GitHub state.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import auto_integrator
import task_review_merge_gate as gate


NOW = datetime(2026, 7, 26, 22, 0, 0, tzinfo=timezone.utc)


def task_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": "ABC-001",
        "title": "Ready",
        "status": "review_approved",
        "owner": "Codex",
        "reviewer": "Claude",
    }
    row.update(overrides)
    return row


def approval_event(**overrides: Any) -> dict[str, Any]:
    event = {
        "ts": "2026-07-26T12:00:00Z",
        "agent": "Claude",
        "type": "review_approved",
        "task_id": "ABC-001",
        "message": "Independent review approved the exact head.",
    }
    event.update(overrides)
    return event


def open_pr(**overrides: Any) -> dict[str, Any]:
    pr = {
        "number": 100,
        "url": "https://github.example/pr/100",
        "headRefName": "task/ABC-001",
        "headRefOid": "b" * 40,
        "baseRefName": "dev",
        "isDraft": False,
        "state": "OPEN",
        "mergeStateStatus": "CLEAN",
        "commits": [{"oid": "b" * 40, "committedDate": "2026-07-26T11:30:00Z"}],
        "statusCheckRollup": [{"name": "Smoke acceptance", "conclusion": "SUCCESS"}],
    }
    pr.update(overrides)
    return pr


def decide(
    *,
    tasks: Sequence[Mapping[str, Any]] | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
    pr: Mapping[str, Any] | None = None,
    task_id: str = "ABC-001",
) -> gate.GateDecision:
    return gate.gate_for_task(
        task_id,
        pr if pr is not None else open_pr(),
        state={"tasks": list(tasks if tasks is not None else [task_row()])},
        events=list(events if events is not None else [approval_event()]),
        now=NOW,
    )


class PolicyResolutionTests(unittest.TestCase):
    def test_default_policy_is_review_before_merge(self) -> None:
        contract = gate.contract_from_task_row(task_row())

        self.assertEqual(contract.policy, gate.POLICY_REVIEW_BEFORE_MERGE)
        self.assertTrue(contract.requires_independent_review)

    def test_declared_merge_then_review_is_refused_with_independent_reviewer(self) -> None:
        contract = gate.contract_from_task_row(task_row(merge_policy="merge_then_review"))

        self.assertEqual(contract.policy, gate.POLICY_REVIEW_BEFORE_MERGE)
        self.assertFalse(contract.declaration_honored)
        self.assertIn("independent reviewer", contract.declaration_detail)

    def test_declared_merge_then_review_is_preserved_when_contract_permits(self) -> None:
        contract = gate.contract_from_task_row(
            task_row(merge_policy="merge-then-review", reviewer="Codex")
        )

        self.assertEqual(contract.policy, gate.POLICY_MERGE_THEN_REVIEW)
        self.assertTrue(contract.declaration_honored)

    def test_merge_then_review_task_may_merge_without_approval(self) -> None:
        decision = decide(
            tasks=[task_row(status="in_progress", reviewer="Codex", merge_policy="merge_then_review")],
            events=[],
        )

        self.assertTrue(decision.allow_merge)
        self.assertTrue(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "merge_then_review_permitted")

    def test_unknown_declaration_falls_back_to_gated_default(self) -> None:
        contract = gate.contract_from_task_row(task_row(merge_policy="merge_whenever"))

        self.assertEqual(contract.policy, gate.POLICY_REVIEW_BEFORE_MERGE)
        self.assertIn("unknown declared merge policy", contract.declaration_detail)


class ApprovedPathTests(unittest.TestCase):
    def test_exact_head_approval_allows_merge_but_never_auto_merge(self) -> None:
        decision = decide()

        self.assertTrue(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "exact_head_approved")
        self.assertEqual(decision.head_oid, "b" * 40)

    def test_reviewer_identity_is_compared_case_insensitively(self) -> None:
        decision = decide(events=[approval_event(agent="claude")])

        self.assertTrue(decision.allow_merge)

    def test_declared_exact_head_must_match_the_pr_head(self) -> None:
        decision = decide(tasks=[task_row(github={"head_sha": "c" * 40})])

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "declared_head_sha_mismatch")


class FailClosedTests(unittest.TestCase):
    def test_missing_task_state_blocks(self) -> None:
        decision = decide(tasks=[], events=[])

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "task_state_unavailable")

    def test_unapproved_status_blocks(self) -> None:
        decision = decide(tasks=[task_row(status="in_progress")], events=[])

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "review_not_approved")

    def test_missing_approval_record_blocks_even_when_row_says_approved(self) -> None:
        decision = decide(events=[])

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_record_missing")

    def test_approval_by_another_agent_blocks(self) -> None:
        decision = decide(events=[approval_event(agent="Antigravity")])

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_reviewer_mismatch")

    def test_reviewer_rejection_after_approval_blocks(self) -> None:
        decision = decide(
            events=[
                approval_event(),
                {
                    "ts": "2026-07-26T12:30:00Z",
                    "agent": "Claude",
                    "type": "reopen",
                    "task_id": "ABC-001",
                    "message": "Independent rejection: contract not satisfied.",
                },
            ]
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_revoked")

    def test_reviewer_rebinding_after_approval_blocks(self) -> None:
        decision = decide(
            events=[
                approval_event(),
                {
                    "ts": "2026-07-26T12:30:00Z",
                    "agent": "Human/Ops",
                    "type": "assign",
                    "task_id": "ABC-001",
                    "message": "Assigned ABC-001 to Codex with reviewer Codex2",
                },
            ]
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_revoked")

    def test_head_change_after_approval_blocks(self) -> None:
        decision = decide(
            pr=open_pr(
                headRefOid="d" * 40,
                commits=[
                    {"oid": "b" * 40, "committedDate": "2026-07-26T11:30:00Z"},
                    {"oid": "d" * 40, "committedDate": "2026-07-26T12:15:00Z"},
                ],
            )
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "head_changed_after_approval")

    def test_unknown_head_oid_blocks(self) -> None:
        pr = open_pr()
        pr.pop("headRefOid")
        decision = decide(pr=pr)

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "pr_head_unknown")

    def test_unparseable_commit_date_blocks(self) -> None:
        decision = decide(pr=open_pr(commits=[{"oid": "b" * 40, "committedDate": "not-a-date"}]))

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "pr_head_timestamp_unknown")

    def test_cross_task_head_branch_blocks(self) -> None:
        decision = decide(pr=open_pr(headRefName="task/OTHER-999"))

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "head_branch_mismatch")

    def test_wrong_base_branch_blocks(self) -> None:
        decision = decide(pr=open_pr(baseRefName="master"))

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "base_branch_mismatch")

    def test_draft_pr_blocks(self) -> None:
        decision = decide(pr=open_pr(isDraft=True))

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "pr_is_draft")

    def test_owner_reviewing_own_task_blocks(self) -> None:
        decision = decide(tasks=[task_row(reviewer="Codex")])

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "no_independent_reviewer")

    def test_future_approval_timestamp_blocks(self) -> None:
        decision = decide(events=[approval_event(ts="2026-07-27T09:00:00Z")])

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_timestamp_not_credible")

    def test_ambiguous_open_prs_are_refused(self) -> None:
        selected, problem = gate.select_open_pr([{"number": 1}, {"number": 2}])

        self.assertIsNone(selected)
        self.assertTrue(problem.startswith("ambiguous_prs:"))


class UnreadableStateTests(unittest.TestCase):
    def test_corrupt_status_file_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ai-status.json").write_text("{not json", encoding="utf-8")

            contract = gate.load_task_contract("ABC-001", status_root=root)

        self.assertEqual(contract.source, "unreadable")
        self.assertEqual(contract.policy, gate.POLICY_REVIEW_BEFORE_MERGE)

    def test_absent_activity_audit_reports_scan_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record = gate.load_approval_record("ABC-001", status_root=Path(tmp))

        self.assertFalse(record.present)
        self.assertIn("unavailable", record.scan_error)

    def test_approval_is_read_from_the_bound_status_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ai-status.json").write_text(
                json.dumps({"tasks": [task_row()]}), encoding="utf-8"
            )
            (root / "ai-activity-log.jsonl").write_text(
                json.dumps(approval_event()) + "\n", encoding="utf-8"
            )

            decision = gate.gate_for_task("ABC-001", open_pr(), status_root=root, now=NOW)

        self.assertTrue(decision.allow_merge)
        self.assertEqual(decision.reason, "exact_head_approved")

    def test_archived_task_row_is_still_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ai-status.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
            archive = root / "ai-task-archive" / "tasks"
            archive.mkdir(parents=True)
            (archive / "abc-001.json").write_text(
                json.dumps({"task_id": "ABC-001", "task": task_row(status="done")}),
                encoding="utf-8",
            )

            contract = gate.load_task_contract("ABC-001", status_root=root)

        self.assertEqual(contract.source, "archive")
        self.assertEqual(contract.policy, gate.POLICY_REVIEW_BEFORE_MERGE)


class PrematureMergeRegressionTests(unittest.TestCase):
    """Recorded state from the three premature 2026-07-26 auto-merges.

    Each fixture is the canonical task row and audit slice as they stood when
    GitHub completed the merge, plus the merged PR payload. All three merged
    one to two minutes after the PR was opened, with no `review_approved`
    record in the audit for the task at that moment.
    """

    def test_pr_4212_supervisor_worker_truth_reconcile(self) -> None:
        decision = gate.gate_for_task(
            "SUP-WORKER-TRUTH-RECONCILE-001",
            {
                "number": 4212,
                "url": "https://github.com/ajoe734/pantheon/pull/4212",
                "headRefName": "task/SUP-WORKER-TRUTH-RECONCILE-001",
                "headRefOid": "0ffc9404c21585539d5aea6ef0a5525e1da2fa92",
                "baseRefName": "dev",
                "isDraft": False,
                "state": "MERGED",
                "mergedAt": "2026-07-26T20:04:01Z",
                "commits": [
                    {
                        "oid": "0ffc9404c21585539d5aea6ef0a5525e1da2fa92",
                        "committedDate": "2026-07-26T20:02:24Z",
                    }
                ],
            },
            state={
                "tasks": [
                    {
                        "id": "SUP-WORKER-TRUTH-RECONCILE-001",
                        "status": "in_progress",
                        "owner": "Claude",
                        "reviewer": "Codex2",
                    }
                ]
            },
            events=[
                {
                    "ts": "2026-07-26T19:30:23Z",
                    "agent": "Human/Ops",
                    "type": "assign",
                    "task_id": "SUP-WORKER-TRUTH-RECONCILE-001",
                    "message": "Assigned to Claude with reviewer Codex2",
                },
                {
                    "ts": "2026-07-26T20:12:48Z",
                    "agent": "Human/Ops",
                    "type": "reopen",
                    "task_id": "SUP-WORKER-TRUTH-RECONCILE-001",
                    "message": "Independent rejection after PR #4212 merge 8703d1f5d.",
                },
            ],
            now=NOW,
        )

        self.assertFalse(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "review_not_approved")
        self.assertTrue(decision.revoke_auto_merge)

    def test_pr_4213_telemetry_lineage_test_isolation(self) -> None:
        decision = self._telemetry_decision(
            number=4213,
            head="9e484e2522cd8778b85a4c880e4cd33d07ef401f",
            committed_at="2026-07-26T20:17:02Z",
            merged_at="2026-07-26T20:18:15Z",
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "review_not_approved")

    def test_pr_4214_telemetry_lineage_test_isolation_followup(self) -> None:
        decision = self._telemetry_decision(
            number=4214,
            head="07747254e3748062e01181b3145cb84d4b5ac1da",
            committed_at="2026-07-26T20:20:26Z",
            merged_at="2026-07-26T20:22:27Z",
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "review_not_approved")

    def test_a_later_approval_still_cannot_bless_the_premature_merge(self) -> None:
        """#4213 merged at 20:18:15Z; an approval recorded afterwards is not retroactive."""

        decision = self._telemetry_decision(
            number=4213,
            head="9e484e2522cd8778b85a4c880e4cd33d07ef401f",
            committed_at="2026-07-26T20:17:02Z",
            merged_at="2026-07-26T20:18:15Z",
            status="review_approved",
            extra_events=[
                {
                    "ts": "2026-07-26T21:30:00Z",
                    "agent": "Codex2",
                    "type": "review_approved",
                    "task_id": "OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001",
                    "message": "Independent review approved after the fact.",
                }
            ],
            reviewer="Codex2",
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "merged_before_approval")

    def _telemetry_decision(
        self,
        *,
        number: int,
        head: str,
        committed_at: str,
        merged_at: str,
        status: str = "in_progress",
        reviewer: str = "Antigravity",
        extra_events: Sequence[Mapping[str, Any]] = (),
    ) -> gate.GateDecision:
        task_id = "OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001"
        return gate.gate_for_task(
            task_id,
            {
                "number": number,
                "url": f"https://github.com/ajoe734/pantheon/pull/{number}",
                "headRefName": f"task/{task_id}",
                "headRefOid": head,
                "baseRefName": "dev",
                "isDraft": False,
                "state": "MERGED",
                "mergedAt": merged_at,
                "commits": [{"oid": head, "committedDate": committed_at}],
            },
            state={
                "tasks": [
                    {
                        "id": task_id,
                        "status": status,
                        "owner": "Claude",
                        "reviewer": reviewer,
                    }
                ]
            },
            events=[
                {
                    "ts": "2026-07-26T19:47:16Z",
                    "agent": "Human/Ops",
                    "type": "assign",
                    "task_id": task_id,
                    "message": "Assigned to Claude with reviewer Antigravity",
                },
                *extra_events,
            ],
            now=NOW,
        )


#: The live regressions below all happened after ``NOW``; they are evaluated
#: from a reference clock that sits past every recorded event.
LIVE_NOW = datetime(2026, 7, 27, 0, 0, 0, tzinfo=timezone.utc)


class LiveMergeGovernanceRegressionTests(unittest.TestCase):
    """The five live 2026-07-26 review-before-merge regressions.

    Every field is recorded state: task rows and audit events come from the
    canonical status root, PR fields from the GitHub API. None of these tests
    impersonates the owner or the reviewer, and none writes canonical state.

    | PR    | entry point            | auto-merge request      |
    | ----- | ---------------------- | ----------------------- |
    | #4217 | direct `gh pr merge`   | none                    |
    | #4222 | auto-merge enable      | enabled after the head  |
    | #4225 | auto-merge enable, then a direct merge by the same credential |
    | #4227 | auto-merge enable      | enabled *before* the head it landed |
    """

    OWNER = "Claude"
    REVIEWER = "Codex2"
    #: The single GitHub account every Pantheon agent's credential resolves to.
    #: It merged all five PRs, which is exactly why GitHub-side identity may
    #: not stand in for canonical reviewer identity.
    GITHUB_ACTOR = "ajoe734"

    def _decide(
        self,
        *,
        task_id: str,
        pr: Mapping[str, Any],
        status: str = "in_progress",
        events: Sequence[Mapping[str, Any]] = (),
        row_extra: Mapping[str, Any] | None = None,
    ) -> gate.GateDecision:
        row = {
            "id": task_id,
            "status": status,
            "owner": self.OWNER,
            "reviewer": self.REVIEWER,
        }
        row.update(row_extra or {})
        return gate.gate_for_task(
            task_id,
            pr,
            state={"tasks": [row]},
            events=list(events),
            now=LIVE_NOW,
        )

    # -- #4217: a direct merge with no auto-merge request at all -------------

    PR_4217 = {
        "number": 4217,
        "url": "https://github.com/ajoe734/pantheon/pull/4217",
        "headRefName": "task/OPS-CI-PR-TRAILER-RANGE-001",
        "headRefOid": "1e968f302e3b27285d78df0a42dfac2a24a80831",
        "baseRefName": "dev",
        "isDraft": False,
        "state": "MERGED",
        "mergedAt": "2026-07-26T21:43:27Z",
        "mergeCommit": {"oid": "71aea154b8a1ab6e652e02018f47c57f26513de0"},
        "mergedBy": {"login": GITHUB_ACTOR},
        "autoMergeRequest": None,
        "reviews": [],
        "commits": [
            {
                "oid": "1e968f302e3b27285d78df0a42dfac2a24a80831",
                "committedDate": "2026-07-26T21:33:15Z",
            }
        ],
    }

    EVENTS_4217 = (
        {
            "ts": "2026-07-26T21:01:44Z",
            "agent": "Human/Ops",
            "type": "assign",
            "task_id": "OPS-CI-PR-TRAILER-RANGE-001",
            "message": "Assigned OPS-CI-PR-TRAILER-RANGE-001 to Claude with reviewer Codex2",
        },
        {
            "ts": "2026-07-26T21:09:25Z",
            "agent": "Claude",
            "type": "start",
            "task_id": "OPS-CI-PR-TRAILER-RANGE-001",
            "message": "Supervisor auto-started OPS-CI-PR-TRAILER-RANGE-001 after successful dispatch.",
        },
    )

    def test_pr_4217_direct_merge_without_any_auto_merge_request(self) -> None:
        """The gate cannot be an auto-merge-only guard: #4217 had none."""

        decision = self._decide(
            task_id="OPS-CI-PR-TRAILER-RANGE-001",
            pr=self.PR_4217,
            events=self.EVENTS_4217,
        )

        self.assertFalse(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "review_not_approved")
        self.assertFalse(decision.auto_merge_request["present"])

    def test_pr_4217_owner_github_credential_does_not_satisfy_the_reviewer(self) -> None:
        """A GitHub approving review from the merging account changes nothing."""

        pr = dict(self.PR_4217)
        pr["reviews"] = [
            {
                "author": {"login": self.GITHUB_ACTOR},
                "state": "APPROVED",
                "submittedAt": "2026-07-26T21:43:00Z",
            }
        ]

        decision = self._decide(
            task_id="OPS-CI-PR-TRAILER-RANGE-001",
            pr=pr,
            events=self.EVENTS_4217,
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "review_not_approved")

    def test_pr_4217_owner_cannot_record_its_own_approval(self) -> None:
        """An approval whose agent is the owner is not independent review."""

        decision = self._decide(
            task_id="OPS-CI-PR-TRAILER-RANGE-001",
            pr=self.PR_4217,
            status="review_approved",
            events=(
                *self.EVENTS_4217,
                {
                    "ts": "2026-07-26T21:40:00Z",
                    "agent": self.OWNER,
                    "type": "review_approved",
                    "task_id": "OPS-CI-PR-TRAILER-RANGE-001",
                    "message": "Owner self-approval attempt.",
                },
            ),
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_reviewer_mismatch")

    # -- #4222: auto-merge enabled, merged 67 seconds later ------------------

    TASK_4222 = "OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001"

    PR_4222 = {
        "number": 4222,
        "url": "https://github.com/ajoe734/pantheon/pull/4222",
        "headRefName": f"task/{TASK_4222}",
        "headRefOid": "28e62b8f25b184044a002aeb8b1c93b5c622ae30",
        "baseRefName": "dev",
        "isDraft": False,
        "state": "MERGED",
        "mergedAt": "2026-07-26T21:55:32Z",
        "mergeCommit": {"oid": "55b17612ed150f52a518a4e8c4c6e75502830f6b"},
        "mergedBy": {"login": GITHUB_ACTOR},
        "autoMergeRequest": {
            "enabledAt": "2026-07-26T21:54:25Z",
            "enabledBy": {"login": GITHUB_ACTOR},
            "mergeMethod": "MERGE",
        },
        "reviews": [],
        "commits": [
            {
                "oid": "28e62b8f25b184044a002aeb8b1c93b5c622ae30",
                "committedDate": "2026-07-26T21:54:05Z",
            }
        ],
    }

    def test_pr_4222_auto_merge_enable_and_completion_both_fail_closed(self) -> None:
        decision = self._decide(task_id=self.TASK_4222, pr=self.PR_4222)

        self.assertFalse(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "review_not_approved")
        self.assertTrue(decision.auto_merge_request["present"])
        self.assertEqual(decision.auto_merge_request["enabled_at"], "2026-07-26T21:54:25Z")
        self.assertTrue(decision.revoke_auto_merge)

    def test_pr_4222_reviewer_rejection_arrived_twenty_minutes_after_the_merge(self) -> None:
        """Codex2 rejected the merged tree at 22:15:44Z; #4222 landed at 21:55:32Z."""

        decision = self._decide(
            task_id=self.TASK_4222,
            pr=self.PR_4222,
            status="review_approved",
            events=(
                {
                    "ts": "2026-07-26T22:10:00Z",
                    "agent": self.REVIEWER,
                    "type": "review_approved",
                    "task_id": self.TASK_4222,
                    "message": "Approval recorded after the merge already happened.",
                },
                {
                    "ts": "2026-07-26T22:15:44Z",
                    "agent": self.REVIEWER,
                    "type": "reopen",
                    "task_id": self.TASK_4222,
                    "message": "Codex2 rejects exact merged PR #4222 tree 55b17612=28e62b8f.",
                },
            ),
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_revoked")
        self.assertEqual(decision.approval["revocation_type"], "reopen")

    # -- #4225: premature auto-merge enable, then a direct merge -------------

    PR_4225_OPEN = {
        "number": 4225,
        "url": "https://github.com/ajoe734/pantheon/pull/4225",
        "headRefName": f"task/{TASK_4222}",
        "headRefOid": "3c2d883700afc3e92568a013bb8e11ec8539031b",
        "baseRefName": "dev",
        "isDraft": False,
        "state": "OPEN",
        "mergeStateStatus": "BLOCKED",
        "autoMergeRequest": {
            "enabledAt": "2026-07-26T22:42:46Z",
            "enabledBy": {"login": GITHUB_ACTOR},
            "mergeMethod": "MERGE",
        },
        "reviews": [],
        "commits": [
            {
                "oid": "3c2d883700afc3e92568a013bb8e11ec8539031b",
                "committedDate": "2026-07-26T22:42:31Z",
            }
        ],
    }

    PR_4225_MERGED = {
        **PR_4225_OPEN,
        "state": "MERGED",
        "mergeStateStatus": "UNKNOWN",
        "mergedAt": "2026-07-26T23:01:39Z",
        "mergeCommit": {"oid": "8d1b5077996a2d27aafb83ff5756f0290d0e90bc"},
        "mergedBy": {"login": GITHUB_ACTOR},
        # Human/Ops had already revoked the auto-merge request; the delivery
        # landed through a plain merge instead.
        "autoMergeRequest": None,
    }

    def test_pr_4225_premature_auto_merge_enable_is_refused(self) -> None:
        """Human/Ops disabled this by hand at 22:42:46Z; the gate must do it."""

        decision = self._decide(task_id=self.TASK_4222, pr=self.PR_4225_OPEN)

        self.assertFalse(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "review_not_approved")
        self.assertTrue(decision.auto_merge_request["present"])
        self.assertTrue(decision.revoke_auto_merge)

    def test_pr_4225_direct_merge_by_the_same_credential_is_refused(self) -> None:
        """The escalation: the blocked auto-merge became a manual merge."""

        decision = self._decide(task_id=self.TASK_4222, pr=self.PR_4225_MERGED)

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "review_not_approved")
        self.assertFalse(decision.auto_merge_request["present"])
        self.assertEqual(decision.contract["reviewer"], self.REVIEWER)

    def test_pr_4225_human_ops_do_not_merge_note_revokes_an_approval(self) -> None:
        """Exact-head do-not-merge notes stood at 22:43:10Z and 22:52:13Z."""

        decision = self._decide(
            task_id=self.TASK_4222,
            pr=self.PR_4225_OPEN,
            status="review_approved",
            events=(
                {
                    "ts": "2026-07-26T22:43:00Z",
                    "agent": self.REVIEWER,
                    "type": "review_approved",
                    "task_id": self.TASK_4222,
                    "message": "Approved head 3c2d883700afc3e92568a013bb8e11ec8539031b.",
                },
                {
                    "ts": "2026-07-26T22:52:13Z",
                    "agent": "Human/Ops",
                    "type": "note",
                    "task_id": self.TASK_4222,
                    "message": (
                        "Do not merge PR #4225 at head "
                        "3c2d883700afc3e92568a013bb8e11ec8539031b; changes required."
                    ),
                },
            ),
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_revoked")
        self.assertEqual(decision.approval["revocation_type"], "note:do_not_merge")
        self.assertEqual(decision.approval["revoked_by"], "Human/Ops")

    def test_an_ordinary_progress_note_does_not_revoke_an_approval(self) -> None:
        """Only an explicit do-not-merge marker revokes; notes are not blanket."""

        decision = self._decide(
            task_id=self.TASK_4222,
            pr=self.PR_4225_OPEN,
            status="review_approved",
            events=(
                {
                    "ts": "2026-07-26T22:43:00Z",
                    "agent": self.REVIEWER,
                    "type": "review_approved",
                    "task_id": self.TASK_4222,
                    "message": "Approved head 3c2d883700afc3e92568a013bb8e11ec8539031b.",
                },
                {
                    "ts": "2026-07-26T22:52:13Z",
                    "agent": "Human/Ops",
                    "type": "note",
                    "task_id": self.TASK_4222,
                    "message": "Import evidence packet reviewed and filed.",
                },
            ),
        )

        # The auto-merge request is still revoked, but the merge itself is now
        # unlocked for this exact head.
        self.assertTrue(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertTrue(decision.revoke_auto_merge)

    # -- #4227: an auto-merge request that outlived the head it merged ------

    TASK_4227 = "SUP-COMMAND-RUNTIME-REFRESH-001"

    PR_4227 = {
        "number": 4227,
        "url": "https://github.com/ajoe734/pantheon/pull/4227",
        "headRefName": f"task/{TASK_4227}",
        "headRefOid": "5fb21c80ba21fcdfd9f304d66b57f56362f9dc60",
        "baseRefName": "dev",
        "isDraft": False,
        "state": "MERGED",
        "mergedAt": "2026-07-26T23:14:41Z",
        "mergeCommit": {"oid": "e376955ff8ac3555871932457865ed1fd0beee83"},
        "mergedBy": {"login": GITHUB_ACTOR},
        "autoMergeRequest": {
            "enabledAt": "2026-07-26T23:10:54Z",
            "enabledBy": {"login": GITHUB_ACTOR},
            "mergeMethod": "MERGE",
        },
        "reviews": [],
        "commits": [
            {
                "oid": "5fb21c80ba21fcdfd9f304d66b57f56362f9dc60",
                "committedDate": "2026-07-26T23:13:21Z",
            }
        ],
    }

    EVENTS_4227 = (
        {
            "ts": "2026-07-26T19:37:36Z",
            "agent": "Human/Ops",
            "type": "assign",
            "task_id": TASK_4227,
            "message": "Assigned SUP-COMMAND-RUNTIME-REFRESH-001 to Claude with reviewer Codex2",
        },
        {
            "ts": "2026-07-26T22:48:46Z",
            "agent": "Claude",
            "type": "start",
            "task_id": TASK_4227,
            "message": "Begin governed command runtime refresh to accepted dev 6578ef968.",
        },
    )

    def test_pr_4227_auto_merge_creation_and_completion_fail_closed(self) -> None:
        decision = self._decide(
            task_id=self.TASK_4227,
            pr=self.PR_4227,
            events=self.EVENTS_4227,
        )

        self.assertFalse(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "review_not_approved")
        self.assertTrue(decision.revoke_auto_merge)

    def test_pr_4227_auto_merge_request_outlived_the_head_it_landed(self) -> None:
        """Enabled 23:10:54Z, head committed 23:13:21Z, merged 23:14:41Z."""

        decision = self._decide(
            task_id=self.TASK_4227,
            pr=self.PR_4227,
            events=self.EVENTS_4227,
        )

        self.assertTrue(decision.auto_merge_request["present"])
        self.assertTrue(decision.auto_merge_request["outlived_head"])
        self.assertEqual(decision.auto_merge_request["enabled_at"], "2026-07-26T23:10:54Z")

    def test_pr_4227_shape_is_refused_even_when_this_head_is_approved(self) -> None:
        """Approving the new head does not legitimise the older merge grant."""

        decision = self._decide(
            task_id=self.TASK_4227,
            pr={**self.PR_4227, "state": "OPEN", "mergedAt": None, "mergeCommit": None},
            status="review_approved",
            events=(
                *self.EVENTS_4227,
                {
                    "ts": "2026-07-26T23:13:40Z",
                    "agent": self.REVIEWER,
                    "type": "review_approved",
                    "task_id": self.TASK_4227,
                    "message": "Approved head 5fb21c80ba21fcdfd9f304d66b57f56362f9dc60.",
                },
            ),
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "auto_merge_request_outlived_head")
        self.assertTrue(decision.revoke_auto_merge)

    def test_pr_4227_docs_only_payload_claims_do_not_waive_review(self) -> None:
        """Stage-1 docs/evidence with the live swap still blocked stays gated."""

        decision = self._decide(
            task_id=self.TASK_4227,
            pr=self.PR_4227,
            events=self.EVENTS_4227,
            row_extra={
                "risk": "low",
                "payload": "docs-and-evidence-only",
                "docs_only": True,
                "review_waived": True,
            },
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.policy, gate.POLICY_REVIEW_BEFORE_MERGE)
        claims = decision.contract["ignored_waiver_claims"]
        self.assertIn("risk=low", claims)
        self.assertIn("docs_only=True", claims)
        self.assertIn("review_waived=True", claims)

    def test_a_payload_claim_cannot_downgrade_the_declared_policy(self) -> None:
        contract = gate.contract_from_task_row(
            {
                "id": self.TASK_4227,
                "status": "in_progress",
                "owner": self.OWNER,
                "reviewer": self.REVIEWER,
                "merge_policy": "merge_then_review",
                "risk": "low",
                "docs_only": True,
            }
        )

        self.assertEqual(contract.policy, gate.POLICY_REVIEW_BEFORE_MERGE)
        self.assertFalse(contract.declaration_honored)
        self.assertIn("independent reviewer", contract.declaration_detail)
        self.assertIn("ignored risk/payload claims", contract.declaration_detail)


class IntegratorGateTests(unittest.TestCase):
    """The integrator must obey the gate on live PRs, not just the module."""

    def setUp(self) -> None:
        self.candidate = auto_integrator.TaskCandidate(
            task_id="ABC-001",
            title="Ready",
            owner="Codex",
            reviewer="Claude",
            branch="task/ABC-001",
        )
        self.settings = auto_integrator.Settings()

    @staticmethod
    def _runner(pr: Mapping[str, Any] | None, **kwargs: Any) -> Any:
        from test_auto_integrator import FakeRunner

        return FakeRunner(pr=pr, **kwargs)

    def _gate(self, *, tasks: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]) -> auto_integrator.ReviewGate:
        return auto_integrator.ReviewGate(state={"tasks": list(tasks)}, events=list(events))

    def test_unapproved_gated_pr_is_never_merged_and_auto_merge_is_revoked(self) -> None:
        pr = open_pr(autoMergeRequest={"enabledAt": "2026-07-26T11:31:00Z"})
        runner = self._runner(pr)

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[]),
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("approval_record_missing", result.detail)
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(merge_commands, [["gh", "pr", "merge", "100", "--disable-auto"]])

    def test_approved_gated_pr_merges_the_exact_head_without_auto(self) -> None:
        runner = self._runner(open_pr())

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[approval_event()]),
        )

        self.assertEqual(result.action, "merged")
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(
            merge_commands,
            [["gh", "pr", "merge", "100", "--merge", "--match-head-commit", "b" * 40]],
        )
        self.assertFalse(any("--auto" in command for command in runner.commands))

    def test_approved_gated_pr_revokes_a_standing_auto_merge_request_first(self) -> None:
        """PR #4227's hazard: never leave a merge grant armed for the next push."""

        pr = open_pr(autoMergeRequest={"enabledAt": "2026-07-26T11:45:00Z"})
        runner = self._runner(pr)

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[approval_event()]),
        )

        self.assertEqual(result.action, "merged")
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(
            merge_commands,
            [
                ["gh", "pr", "merge", "100", "--disable-auto"],
                ["gh", "pr", "merge", "100", "--merge", "--match-head-commit", "b" * 40],
            ],
        )

    def test_gated_pr_needing_a_rebase_is_not_force_pushed(self) -> None:
        runner = self._runner(open_pr(mergeStateStatus="BEHIND"))
        # The rebase probe moves HEAD, which would replace the reviewed head.
        original_run = runner.run
        state = {"calls": 0}

        def run(args: Sequence[str], **kwargs: Any):  # type: ignore[override]
            command = [str(arg) for arg in args]
            if command[:3] == ["git", "rev-parse", "HEAD"]:
                state["calls"] += 1
                runner.commands.append(command)
                from test_auto_integrator import completed

                return completed(command, stdout=("before\n" if state["calls"] == 1 else "after\n"))
            return original_run(args, **kwargs)

        runner.run = run  # type: ignore[method-assign]

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[approval_event()]),
        )

        self.assertEqual(result.action, "waiting")
        self.assertIn("re-approves the new head", result.detail)
        self.assertFalse(any(command[:2] == ["git", "push"] for command in runner.commands))
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))

    def test_concurrent_open_prs_for_one_task_branch_fail_closed(self) -> None:
        from test_auto_integrator import FakeRunner, completed

        class AmbiguousRunner(FakeRunner):
            def run(self, args: Sequence[str], **kwargs: Any):  # type: ignore[override]
                command = [str(arg) for arg in args]
                if command[:3] == ["gh", "pr", "list"] and "open" in command:
                    self.commands.append(command)
                    return completed(command, stdout='[{"number": 100}, {"number": 101}]')
                return super().run(args, **kwargs)

        runner = AmbiguousRunner(pr=open_pr())

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[approval_event()]),
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("multiple open PRs", result.detail)
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))

    def test_merge_then_review_task_keeps_its_documented_behavior(self) -> None:
        runner = self._runner(open_pr())

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(
                tasks=[task_row(status="review_approved", reviewer="Codex", merge_policy="merge_then_review")],
                events=[],
            ),
        )

        self.assertEqual(result.action, "merged")
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(merge_commands, [["gh", "pr", "merge", "100", "--merge"]])


FAKE_GH = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$GH_LOG"
if [[ "$1 $2" == "pr view" ]]; then
  echo "https://github.example/pr/1"
fi
exit 0
"""


class TaskFinalizeShellTests(unittest.TestCase):
    """The PR-opening helper must ask the gate before granting merge authority."""

    def _fixture(self, task: Mapping[str, Any]) -> tuple[Path, Path]:
        tmp = Path(tempfile.mkdtemp(prefix="task-finalize-gate-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        origin = tmp / "origin.git"
        repo = tmp / "repo"
        self._git(["init", "--bare", "--initial-branch=dev", str(origin)], cwd=tmp)
        self._git(["init", "--initial-branch=dev", str(repo)], cwd=tmp)
        self._git(["config", "user.email", "gate@example.test"], cwd=repo)
        self._git(["config", "user.name", "Gate Fixture"], cwd=repo)
        self._git(["remote", "add", "origin", str(origin)], cwd=repo)

        helpers = repo / "scripts" / "git"
        helpers.mkdir(parents=True)
        source = Path(__file__).resolve().parent
        for name in ("task_finalize.sh", "task_review_merge_gate.py"):
            (helpers / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")
        (helpers / "task_finalize.sh").chmod(0o755)
        (repo / "ai-status.json").write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
        self._git(["add", "-A"], cwd=repo)
        self._git(["commit", "-m", "base", "--no-verify"], cwd=repo)
        self._git(["push", "-u", "origin", "dev"], cwd=repo)

        self._git(["checkout", "-b", f"task/{task['id']}"], cwd=repo)
        (repo / "delivery.txt").write_text("delivered\n", encoding="utf-8")
        self._git(["add", "delivery.txt"], cwd=repo)
        self._git(["commit", "-m", f"{task['id']}: deliver", "--no-verify"], cwd=repo)

        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(FAKE_GH, encoding="utf-8")
        (bin_dir / "gh").chmod(0o755)
        return repo, tmp

    @staticmethod
    def _git(args: Sequence[str], *, cwd: Path) -> None:
        subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)

    def _run_finalize(self, task: Mapping[str, Any]) -> str:
        repo, tmp = self._fixture(task)
        log = tmp / "gh.log"
        env = dict(os.environ)
        env["PATH"] = f"{tmp / 'bin'}:{env['PATH']}"
        env["GH_LOG"] = str(log)
        env["PANTHEON_STATUS_ROOT"] = str(repo)
        proc = subprocess.run(
            ["bash", "scripts/git/task_finalize.sh", str(task["id"])],
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return log.read_text(encoding="utf-8")

    def test_gated_task_pr_is_opened_without_any_auto_merge(self) -> None:
        calls = self._run_finalize(task_row(id="ABC-001", status="in_progress"))

        self.assertIn("pr create", calls)
        self.assertNotIn("--label auto-merge", calls)
        self.assertNotIn("--auto --merge", calls)
        self.assertIn("pr merge task/ABC-001 --disable-auto", calls)

    def test_merge_then_review_task_still_enables_auto_merge(self) -> None:
        calls = self._run_finalize(
            task_row(id="ABC-001", status="in_progress", reviewer="Codex", merge_policy="merge_then_review")
        )

        self.assertIn("--label auto-merge", calls)
        self.assertIn("pr merge task/ABC-001 --auto --merge", calls)
        self.assertNotIn("--disable-auto", calls)


if __name__ == "__main__":
    unittest.main()
