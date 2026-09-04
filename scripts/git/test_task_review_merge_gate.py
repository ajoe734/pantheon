"""Fail-closed proofs for the canonical review-before-merge gate.

The regression fixtures replay eleven live 2026-07-26 governance failures
from recorded canonical state: the premature auto-merges of Pantheon PRs
#4212, #4213 and #4214, the seven later events on PRs #4217, #4222, #4225
(auto-merge enable, then direct merge), #4226, #4227 and #4230, and the
still-armed auto-merge request Human/Ops found on the BEHIND PR #4201. They
are data only: no test impersonates the owner or the reviewer, and nothing
here writes canonical status, activity, or GitHub state.
"""

from __future__ import annotations

import json
import gzip
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.git import auto_integrator
from scripts.git import task_review_merge_gate as gate
from scripts.git.test_auto_integrator import FakeRunner, completed


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


def approval_binding(**overrides: Any) -> dict[str, Any]:
    """The PR identity `ai_status.py::command_approve` stamps on an approval."""

    binding = {
        "pr": 100,
        "head_sha": "b" * 40,
        "head_branch": "task/ABC-001",
        "base": "dev",
    }
    binding.update(overrides)
    return binding


def approval_event(**overrides: Any) -> dict[str, Any]:
    event = {
        "ts": "2026-07-26T12:00:00Z",
        "agent": "Claude",
        "type": "review_approved",
        "task_id": "ABC-001",
        "message": "Independent review approved the exact head.",
        "review_binding": approval_binding(),
    }
    event.update(overrides)
    return event


def operator_acceptance_event(**overrides: Any) -> dict[str, Any]:
    """A distinct Human/Ops exact-head acceptance, never reviewer evidence."""

    binding = dict(overrides.pop("review_binding", approval_binding()))
    evidence = {
        "repository": "ajoe734/pantheon",
        "pr": binding["pr"],
        "head_sha": binding["head_sha"],
        "head_branch": binding["head_branch"],
        "base": binding["base"],
        "decision": "operator-accept",
        "actor": "Human/Ops",
        "mode": "operator_exact_head",
        "operator_acceptance_proof_ref": (
            "refs/tags/pantheon-review/operator-accept/" + binding["head_sha"]
        ),
    }
    evidence.update(overrides.pop("operator_acceptance", {}))
    event = {
        "ts": "2026-07-26T12:00:00Z",
        "agent": "Human/Ops",
        "type": "operator_accepted",
        "task_id": "ABC-001",
        "message": "Human/Ops accepted this exact head.",
        "review_binding": binding,
        "operator_acceptance": evidence,
    }
    event.update(overrides)
    return event


def exact_head_rest_merge(head: str = "b" * 40) -> list[str]:
    return [
        "gh",
        "api",
        "--method",
        "PUT",
        "repos/ajoe734/pantheon/pulls/100/merge",
        "-f",
        f"sha={head}",
        "-f",
        "merge_method=merge",
    ]


def integration_resume_event(**overrides: Any) -> dict[str, Any]:
    event = {
        "ts": "2026-07-26T13:00:00Z",
        "agent": "Human/Ops",
        "type": "integration_resumed",
        "task_id": "ABC-001",
        "message": "Resume exact reviewed PR after writable integrator recovery.",
        "operator_mode": "local_human_ops",
    }
    event.update(overrides)
    return event


#: The PR identities `command_approve` would stamp on the live-regression
#: approvals below. The PR numbers, shas and branches are the recorded ones;
#: only the binding wrapper is new, because on 2026-07-26 nothing recorded it.
BINDING_4225 = {
    "pr": 4225,
    "head_sha": "3c2d883700afc3e92568a013bb8e11ec8539031b",
    "head_branch": "task/OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001",
    "base": "dev",
}
BINDING_4227 = {
    "pr": 4227,
    "head_sha": "5fb21c80ba21fcdfd9f304d66b57f56362f9dc60",
    "head_branch": "task/SUP-COMMAND-RUNTIME-REFRESH-001",
    "base": "dev",
}
BINDING_4230 = {
    "pr": 4230,
    "head_sha": "1bb2b839bad3258d7c5fe353e957e6a5fec08545",
    "head_branch": "task/OPS-CI-PR-TRAILER-RANGE-001",
    "base": "dev",
}


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
    task_brief_carry_forward: Mapping[str, Any] | None = None,
) -> gate.GateDecision:
    return gate.gate_for_task(
        task_id,
        pr if pr is not None else open_pr(),
        state={"tasks": list(tasks if tasks is not None else [task_row()])},
        events=list(events if events is not None else [approval_event()]),
        now=NOW,
        task_brief_carry_forward=task_brief_carry_forward,
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


class OperatorExactHeadAcceptanceTests(unittest.TestCase):
    def test_exact_head_operator_acceptance_allows_merge_without_relabeling_reviewer(self) -> None:
        decision = decide(events=[operator_acceptance_event()])

        self.assertTrue(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "exact_head_operator_accepted")
        self.assertEqual(decision.approval["authority"], "operator_exact_head")
        self.assertEqual(decision.approval["reviewer"], "Human/Ops")
        self.assertIn("distinct operator exact-head acceptance", decision.detail)

    def test_operator_acceptance_from_non_human_ops_cannot_replace_review(self) -> None:
        decision = decide(events=[operator_acceptance_event(agent="Codex")])

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_binding_unusable")
        self.assertIn("not recorded by Human/Ops", decision.detail)

    def test_operator_acceptance_proof_for_another_head_is_refused(self) -> None:
        decision = decide(
            events=[
                operator_acceptance_event(
                    operator_acceptance={
                        "operator_acceptance_proof_ref": (
                            "refs/tags/pantheon-review/operator-accept/" + "c" * 40
                        )
                    }
                )
            ]
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_binding_unusable")
        self.assertIn("proof ref does not match", decision.detail)

    def test_operator_acceptance_recovers_after_environment_blocker_resume(self) -> None:
        decision = decide(
            events=[
                operator_acceptance_event(),
                {
                    "ts": "2026-07-26T12:30:00Z",
                    "agent": "AutoIntegrator",
                    "type": "blocker",
                    "task_id": "ABC-001",
                    "message": "temporary integration environment failure",
                },
                integration_resume_event(),
            ]
        )

        self.assertTrue(decision.allow_merge)
        self.assertEqual(decision.reason, "exact_head_operator_accepted")

    def test_declared_exact_head_must_match_the_pr_head(self) -> None:
        decision = decide(tasks=[task_row(github={"head_sha": "c" * 40})])

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "declared_head_sha_mismatch")

    def test_frozen_delivery_binding_precedes_stale_legacy_github_metadata(self) -> None:
        decision = decide(
            tasks=[
                task_row(
                    delivery_binding={"kind": "pull_request", **approval_binding()},
                    review_binding=approval_binding(),
                    github={
                        "head_sha": "c" * 40,
                        "head_branch": "task/ABC-001",
                    },
                )
            ]
        )

        self.assertTrue(decision.allow_merge)
        self.assertEqual(decision.reason, "exact_head_approved")

    def test_review_binding_precedes_stale_legacy_github_for_migrated_task(self) -> None:
        decision = decide(
            tasks=[
                task_row(
                    review_binding=approval_binding(),
                    github={
                        "head_sha": "c" * 40,
                        "head_branch": "task/ABC-001",
                    },
                )
            ]
        )

        self.assertTrue(decision.allow_merge)
        self.assertEqual(decision.reason, "exact_head_approved")

    def test_artifact_delivery_binding_cannot_fall_back_to_legacy_pr_metadata(self) -> None:
        decision = decide(
            tasks=[
                task_row(
                    delivery_binding={
                        "kind": "artifact_contract",
                        "contract_sha256": "a" * 64,
                    },
                    github={
                        "head_sha": "b" * 40,
                        "head_branch": "task/ABC-001",
                    },
                )
            ]
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "declared_head_sha_mismatch")


class ApprovalBindingTests(unittest.TestCase):
    """The approval must name what it approved, and the gate must compare it.

    Before the binding existed the gate only asked "is the head newer than the
    approval?". That question cannot see a head *replaced* with an older
    commit, which is how an unreviewed commit stayed mergeable.
    """

    def test_pre_dated_head_replacement_is_refused(self) -> None:
        """The reported fail-open: approve `b...`, then swap in older `c...`.

        `c` was committed at 11:00, before the 12:00 approval, so every
        timestamp rule still held and the gate returned
        `allow_merge=True reason=exact_head_approved` for a commit no reviewer
        ever saw.
        """

        replaced = open_pr(
            headRefOid="c" * 40,
            commits=[{"oid": "c" * 40, "committedDate": "2026-07-26T11:00:00Z"}],
        )

        decision = decide(pr=replaced)

        self.assertFalse(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "approval_head_mismatch")
        self.assertEqual(decision.approval["approved_head_sha"], "b" * 40)
        self.assertEqual(decision.head_oid, "c" * 40)
        self.assertTrue(decision.revoke_auto_merge)

    def test_task_brief_only_successor_carries_approval_without_rereview(self) -> None:
        successor = "d" * 40
        pr = open_pr(
            headRefOid=successor,
            commits=[{"oid": successor, "committedDate": "2026-07-26T12:05:00Z"}],
        )

        decision = decide(
            pr=pr,
            task_brief_carry_forward={
                "kind": "task_brief_only_successor",
                "approved_head_sha": "b" * 40,
                "successor_head_sha": successor,
                "changed_paths": [".orchestrator/task-briefs/abc_001.md"],
            },
        )

        self.assertTrue(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "task_brief_only_approval_carried_forward")
        self.assertEqual(decision.head_oid, successor)

    def test_pre_dated_head_replacement_survives_a_matching_commit_history(self) -> None:
        """Rewriting the PR's commit list does not rebuild the approval."""

        decision = decide(
            pr=open_pr(
                headRefOid="c" * 40,
                commits=[
                    {"oid": "b" * 40, "committedDate": "2026-07-26T11:30:00Z"},
                    {"oid": "c" * 40, "committedDate": "2026-07-26T11:00:00Z"},
                ],
            )
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_head_mismatch")

    def test_unbound_approval_cannot_open_the_gate(self) -> None:
        """A legacy approval with no binding is unusable, not permissive."""

        event = approval_event()
        event.pop("review_binding")

        decision = decide(events=[event])

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_head_binding_missing")
        self.assertFalse(decision.approval["binding_present"])

    def test_a_head_named_only_in_the_approval_message_is_not_a_binding(self) -> None:
        """Free text is not compared; only the recorded binding is."""

        event = approval_event(message=f"Approved head {'c' * 40}.")
        event.pop("review_binding")

        decision = decide(events=[event], pr=open_pr(headRefOid="c" * 40))

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_head_binding_missing")

    def test_malformed_binding_is_unusable(self) -> None:
        decision = decide(
            events=[approval_event(review_binding=approval_binding(head_sha="b" * 12))]
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_binding_unusable")
        self.assertIn("40-hex", decision.approval["binding_error"])

    def test_binding_without_a_pr_number_is_unusable(self) -> None:
        binding = approval_binding()
        binding.pop("pr")

        decision = decide(events=[approval_event(review_binding=binding)])

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_binding_unusable")

    def test_binding_for_another_pr_blocks(self) -> None:
        """Two open PRs can carry the same head; the approval names only one."""

        decision = decide(events=[approval_event(review_binding=approval_binding(pr=101))])

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_pr_mismatch")

    def test_binding_expecting_another_base_blocks(self) -> None:
        decision = decide(
            events=[approval_event(review_binding=approval_binding(base="master"))]
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_base_mismatch")

    def test_binding_naming_another_head_branch_blocks(self) -> None:
        decision = decide(
            events=[approval_event(review_binding=approval_binding(head_branch="task/XYZ-009"))]
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_head_branch_mismatch")

    def test_binding_head_sha_is_compared_case_insensitively(self) -> None:
        decision = decide(
            events=[approval_event(review_binding=approval_binding(head_sha="B" * 40))]
        )

        self.assertTrue(decision.allow_merge)
        self.assertEqual(decision.reason, "exact_head_approved")

    def test_a_string_pr_number_still_binds(self) -> None:
        decision = decide(events=[approval_event(review_binding=approval_binding(pr="#100"))])

        self.assertTrue(decision.allow_merge)
        self.assertEqual(decision.approval["approved_pr_number"], 100)

    def test_the_approved_decision_reports_what_it_was_bound_to(self) -> None:
        decision = decide()

        self.assertTrue(decision.allow_merge)
        self.assertEqual(decision.approval["approved_head_sha"], "b" * 40)
        self.assertEqual(decision.approval["approved_pr_number"], 100)
        self.assertEqual(decision.approval["approved_base_branch"], "dev")
        self.assertIn("b" * 40, decision.detail)


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

    def test_local_human_ops_resume_clears_only_a_later_blocker_revocation(self) -> None:
        decision = decide(
            events=[
                approval_event(),
                {
                    "ts": "2026-07-26T12:30:00Z",
                    "agent": "Codex",
                    "type": "blocker",
                    "task_id": "ABC-001",
                    "message": "Integrator lock is read-only in the worker sandbox.",
                },
                integration_resume_event(),
            ]
        )

        self.assertTrue(decision.allow_merge)
        self.assertEqual(decision.reason, "exact_head_approved")

    def test_resume_without_local_human_ops_marker_does_not_clear_blocker(self) -> None:
        decision = decide(
            events=[
                approval_event(),
                {
                    "ts": "2026-07-26T12:30:00Z",
                    "agent": "Codex",
                    "type": "blocker",
                    "task_id": "ABC-001",
                    "message": "Integrator lock is read-only.",
                },
                integration_resume_event(operator_mode=""),
            ]
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_revoked")

    def test_resume_cannot_clear_reviewer_reopen_hidden_by_later_blocker(self) -> None:
        decision = decide(
            events=[
                approval_event(),
                {
                    "ts": "2026-07-26T12:15:00Z",
                    "agent": "Claude",
                    "type": "reopen",
                    "task_id": "ABC-001",
                    "message": "Changes required in the implementation.",
                },
                {
                    "ts": "2026-07-26T12:30:00Z",
                    "agent": "Codex",
                    "type": "blocker",
                    "task_id": "ABC-001",
                    "message": "Integrator is unavailable.",
                },
                integration_resume_event(),
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
        self.assertEqual(decision.reason, "approval_head_mismatch")

    def test_newer_head_blocks_even_if_the_binding_names_it(self) -> None:
        """The timestamp check stays live behind the exact-identity comparison.

        If the recorded binding itself named a head committed after the
        approval, the audit line and the clock disagree; that is not a state
        the gate may resolve in favour of merging.
        """

        decision = decide(
            events=[approval_event(review_binding=approval_binding(head_sha="d" * 40))],
            pr=open_pr(
                headRefOid="d" * 40,
                commits=[{"oid": "d" * 40, "committedDate": "2026-07-26T12:15:00Z"}],
            ),
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
            task_id = "LUV-REACTIVATE-KW01-001"
            (archive / f"{task_id}.json").write_text(
                json.dumps(
                    {
                        "task_id": task_id,
                        "task": task_row(id=task_id, status="done"),
                    }
                ),
                encoding="utf-8",
            )

            contract = gate.load_task_contract(task_id, status_root=root)

        self.assertEqual(contract.source, "archive")
        self.assertEqual(contract.policy, gate.POLICY_REVIEW_BEFORE_MERGE)
        self.assertEqual(contract.task_id, task_id)


class RotatedActivityChronologyTests(unittest.TestCase):
    def _write_archive(self, root: Path, name: str, events: Sequence[Mapping[str, Any]]) -> None:
        archive = root / "archive" / "logs"
        archive.mkdir(parents=True, exist_ok=True)
        with gzip.open(archive / name, "wt", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")

    def test_content_hash_filename_order_cannot_revoke_newer_pr_5527_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_assign = {
                "ts": "2026-09-04T13:48:00Z",
                "agent": "Human/Ops",
                "type": "assign",
                "task_id": "ABC-001",
                "message": "Earlier reviewer assignment.",
                "event_id": "assign-1348",
            }
            newer_approval = approval_event(
                ts="2026-09-04T15:18:00Z", event_id="approval-1518"
            )
            # Content hashes sort approval first, then the older assignment.
            self._write_archive(root, "ai-activity-log.jsonl-0aaa.gz", [newer_approval])
            self._write_archive(root, "ai-activity-log.jsonl-ffff.gz", [old_assign])

            record = gate.load_approval_record("ABC-001", status_root=root)

        self.assertTrue(record.present)
        self.assertFalse(record.revoked)
        self.assertEqual(record.approved_at_text, "2026-09-04T15:18:00Z")

    def test_post_approval_assign_remains_non_resumable(self) -> None:
        events = [
            approval_event(ts="2026-09-04T15:18:00Z"),
            {
                "ts": "2026-09-04T15:19:00Z",
                "agent": "Human/Ops",
                "type": "assign",
                "task_id": "ABC-001",
                "message": "Reviewer changed after approval.",
            },
            integration_resume_event(ts="2026-09-04T15:20:00Z"),
        ]

        record = gate.load_approval_record("ABC-001", events=events)

        self.assertTrue(record.revoked)
        self.assertEqual(record.revocation_type, "assign")

    def test_invalid_timestamp_fails_closed(self) -> None:
        record = gate.load_approval_record(
            "ABC-001", events=[approval_event(ts="not-a-timestamp")]
        )

        self.assertIn("invalid timestamp", record.scan_error)

    def test_per_source_timestamp_regression_fails_closed(self) -> None:
        record = gate.load_approval_record(
            "ABC-001",
            events=[
                approval_event(ts="2026-09-04T15:18:00Z"),
                approval_event(ts="2026-09-04T13:48:00Z"),
            ],
        )

        self.assertIn("regresses", record.scan_error)

    def test_overlapping_archive_ranges_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_archive(
                root,
                "ai-activity-log.jsonl-aaaa.gz",
                [
                    approval_event(ts="2026-09-04T13:00:00Z"),
                    approval_event(ts="2026-09-04T15:00:00Z"),
                ],
            )
            self._write_archive(
                root,
                "ai-activity-log.jsonl-bbbb.gz",
                [approval_event(ts="2026-09-04T14:00:00Z")],
            )

            record = gate.load_approval_record("ABC-001", status_root=root)

        self.assertIn("source ranges overlap", record.scan_error)

    def test_conflicting_duplicate_event_id_fails_closed(self) -> None:
        first = approval_event(event_id="same-event")
        second = approval_event(event_id="same-event", message="different payload")

        record = gate.load_approval_record("ABC-001", events=[first, second])

        self.assertIn("conflicting payloads", record.scan_error)

    def test_distinct_events_at_same_timestamp_are_ambiguous(self) -> None:
        record = gate.load_approval_record(
            "ABC-001",
            events=[
                approval_event(),
                approval_event(message="different event at the same instant"),
            ],
        )

        self.assertIn("ambiguous", record.scan_error)


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
                    "review_binding": {
                        "pr": 4213,
                        "head_sha": "9e484e2522cd8778b85a4c880e4cd33d07ef401f",
                        "head_branch": "task/OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001",
                        "base": "dev",
                    },
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
    """The eight live 2026-07-26 review-before-merge regressions.

    Every field is recorded state: task rows and audit events come from the
    canonical status root, PR fields from the GitHub API. None of these tests
    impersonates the owner or the reviewer, and none writes canonical state.

    | PR    | entry point            | auto-merge request      |
    | ----- | ---------------------- | ----------------------- |
    | #4217 | direct `gh pr merge`   | none                    |
    | #4222 | auto-merge enable      | enabled after the head  |
    | #4225 | auto-merge enable, then a direct merge by the same credential |
    | #4226 | auto-merge enable      | enabled after the head  |
    | #4227 | auto-merge enable      | enabled *before* the head it landed |
    | #4230 | auto-merge enable      | enabled after the head  |
    | #4201 | auto-merge enable      | armed, held only by a BEHIND base |

    All of them report ``reviews=[]`` and an empty ``reviewDecision``. The
    seven that merged were merged by the one GitHub account every Pantheon
    agent shares; #4201 did not merge only because its base was stale.
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
                    "review_binding": BINDING_4225,
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
                    "review_binding": BINDING_4225,
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
                    "review_binding": BINDING_4227,
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

    # -- #4226: the third merge on one unapproved task branch ---------------

    PR_4226 = {
        "number": 4226,
        "url": "https://github.com/ajoe734/pantheon/pull/4226",
        "headRefName": f"task/{TASK_4222}",
        "headRefOid": "4628dc721b0e1b434303fac679c01a9afa05b108",
        "baseRefName": "dev",
        "isDraft": False,
        "state": "MERGED",
        "mergedAt": "2026-07-26T23:07:09Z",
        "mergeCommit": {"oid": "1cf27337e9197c8bc0840e466f55019065e3576e"},
        "mergedBy": {"login": GITHUB_ACTOR},
        "reviewDecision": "",
        "autoMergeRequest": {
            "enabledAt": "2026-07-26T23:06:04Z",
            "enabledBy": {"login": GITHUB_ACTOR},
            "mergeMethod": "MERGE",
        },
        "reviews": [],
        "latestReviews": [],
        "commits": [
            {
                "oid": "4628dc721b0e1b434303fac679c01a9afa05b108",
                "committedDate": "2026-07-26T23:05:49Z",
            }
        ],
    }

    def test_pr_4226_third_merge_on_one_unapproved_task_branch(self) -> None:
        """#4222, #4225 and #4226 all merged task/OPS-L12-...-IMPORT-001 unreviewed."""

        decision = self._decide(task_id=self.TASK_4222, pr=self.PR_4226)

        self.assertFalse(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "review_not_approved")
        self.assertTrue(decision.auto_merge_request["present"])
        self.assertEqual(decision.auto_merge_request["enabled_at"], "2026-07-26T23:06:04Z")
        self.assertTrue(decision.revoke_auto_merge)

    # -- #4230: green CI standing in for canonical review -------------------

    TASK_4230 = "OPS-CI-PR-TRAILER-RANGE-001"

    PR_4230 = {
        "number": 4230,
        "url": "https://github.com/ajoe734/pantheon/pull/4230",
        "headRefName": f"task/{TASK_4230}",
        "headRefOid": "1bb2b839bad3258d7c5fe353e957e6a5fec08545",
        "baseRefName": "dev",
        "isDraft": False,
        "state": "MERGED",
        "mergedAt": "2026-07-26T23:34:22Z",
        "mergeCommit": {"oid": "643181a067ec5c344faac0766c69de0d5cfb32eb"},
        "mergedBy": {"login": GITHUB_ACTOR},
        "reviewDecision": "",
        "autoMergeRequest": {
            "enabledAt": "2026-07-26T23:33:20Z",
            "enabledBy": {"login": GITHUB_ACTOR},
            "mergeMethod": "MERGE",
        },
        "reviews": [],
        "latestReviews": [],
        # Every required check was green; that is the entire point.
        "statusCheckRollup": [
            {"name": "Commit trailers", "conclusion": "SUCCESS"},
            {"name": "Runtime mirror guard", "conclusion": "SUCCESS"},
            {"name": "Smoke acceptance", "conclusion": "SUCCESS"},
        ],
        "commits": [
            {
                "oid": "1bb2b839bad3258d7c5fe353e957e6a5fec08545",
                "committedDate": "2026-07-26T23:32:48Z",
            }
        ],
    }

    EVENTS_4230 = (
        {
            "ts": "2026-07-26T21:01:44Z",
            "agent": "Human/Ops",
            "type": "assign",
            "task_id": TASK_4230,
            "message": "Assigned OPS-CI-PR-TRAILER-RANGE-001 to Claude with reviewer Codex2",
        },
        {
            "ts": "2026-07-26T23:28:46Z",
            "agent": "Claude",
            "type": "start",
            "task_id": TASK_4230,
            "message": "Supervisor auto-started OPS-CI-PR-TRAILER-RANGE-001 after successful dispatch.",
        },
    )

    def test_pr_4230_green_ci_is_not_canonical_reviewer_approval(self) -> None:
        """All three required checks were SUCCESS; the task was in_progress."""

        decision = self._decide(
            task_id=self.TASK_4230,
            pr=self.PR_4230,
            events=self.EVENTS_4230,
        )

        self.assertFalse(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "review_not_approved")
        self.assertEqual(decision.contract["status"], "in_progress")

    # -- the shared credential, on both halves of the merge ----------------

    def test_the_shared_credential_cannot_enable_auto_merge(self) -> None:
        """Creation half: the request #4230 armed at 23:33:20Z is refused."""

        open_form = {
            **self.PR_4230,
            "state": "OPEN",
            "mergedAt": None,
            "mergeCommit": None,
            "mergeStateStatus": "CLEAN",
        }

        decision = self._decide(
            task_id=self.TASK_4230,
            pr=open_form,
            events=self.EVENTS_4230,
        )

        self.assertFalse(decision.allow_auto_merge)
        self.assertTrue(decision.revoke_auto_merge)
        self.assertEqual(decision.auto_merge_request["enabled_by"], self.GITHUB_ACTOR)

    def test_the_shared_credential_cannot_finalize_a_merge(self) -> None:
        """Finalization half: the same account completing the merge is refused."""

        decision = self._decide(
            task_id=self.TASK_4230,
            pr=self.PR_4230,
            events=self.EVENTS_4230,
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(self.PR_4230["mergedBy"]["login"], self.GITHUB_ACTOR)
        self.assertNotEqual(
            gate.normalize_agent(self.GITHUB_ACTOR),
            gate.normalize_agent(decision.contract["reviewer"]),
        )

    def test_an_approved_task_never_unlocks_auto_merge_creation(self) -> None:
        """Approval unlocks an exact-head merge, never a standing merge grant."""

        decision = self._decide(
            task_id=self.TASK_4230,
            pr={
                **self.PR_4230,
                "state": "OPEN",
                "mergedAt": None,
                "mergeCommit": None,
                "autoMergeRequest": None,
            },
            status="review_approved",
            events=(
                *self.EVENTS_4230,
                {
                    "ts": "2026-07-26T23:33:00Z",
                    "agent": self.REVIEWER,
                    "type": "review_approved",
                    "task_id": self.TASK_4230,
                    "message": "Approved head 1bb2b839bad3258d7c5fe353e957e6a5fec08545.",
                    "review_binding": BINDING_4230,
                },
            ),
        )

        self.assertTrue(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "exact_head_approved")

    def test_a_changes_requested_decision_blocks_the_merge(self) -> None:
        """Canonical rejection blocks even with a GitHub APPROVED review present."""

        decision = self._decide(
            task_id=self.TASK_4230,
            pr={
                **self.PR_4230,
                "state": "OPEN",
                "mergedAt": None,
                "mergeCommit": None,
                "autoMergeRequest": None,
                "reviewDecision": "CHANGES_REQUESTED",
                "reviews": [
                    {
                        "author": {"login": self.GITHUB_ACTOR},
                        "state": "APPROVED",
                        "submittedAt": "2026-07-26T23:33:10Z",
                    }
                ],
            },
            status="review_approved",
            events=(
                *self.EVENTS_4230,
                {
                    "ts": "2026-07-26T23:33:00Z",
                    "agent": self.REVIEWER,
                    "type": "review_approved",
                    "task_id": self.TASK_4230,
                    "message": "Approved head 1bb2b839bad3258d7c5fe353e957e6a5fec08545.",
                    "review_binding": BINDING_4230,
                },
                {
                    "ts": "2026-07-26T23:35:00Z",
                    "agent": self.REVIEWER,
                    "type": "reopen",
                    "task_id": self.TASK_4230,
                    "message": "Changes required on head 1bb2b839bad3258d7c5fe353e957e6a5fec08545.",
                },
            ),
        )

        self.assertFalse(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "approval_revoked")

    # -- #4201: auto-merge armed on a BEHIND PR, held only by a stale base --

    TASK_4201 = "P0-TW-PAPER-ACTIVATE-001"

    PR_4201 = {
        "number": 4201,
        "url": "https://github.com/ajoe734/pantheon/pull/4201",
        "headRefName": f"task/{TASK_4201}",
        "headRefOid": "dc5d7128bad1717b23b6c750076b0cb47a213ae3",
        "baseRefName": "dev",
        "isDraft": False,
        "state": "OPEN",
        "mergeStateStatus": "BEHIND",
        "reviewDecision": "",
        "autoMergeRequest": {
            "enabledAt": "2026-07-26T17:30:00Z",
            "enabledBy": {"login": GITHUB_ACTOR},
            "mergeMethod": "MERGE",
        },
        "reviews": [],
        "latestReviews": [],
        "commits": [
            {
                "oid": "dc5d7128bad1717b23b6c750076b0cb47a213ae3",
                "committedDate": "2026-07-26T17:22:48Z",
            }
        ],
    }

    def test_pr_4201_behind_pr_may_not_retain_an_auto_merge_request(self) -> None:
        """Only `mergeStateStatus=BEHIND` was holding this back, not the gate."""

        decision = self._decide(
            task_id=self.TASK_4201,
            pr=self.PR_4201,
            row_extra={"owner": "Antigravity", "reviewer": "Claude"},
            events=(
                {
                    "ts": "2026-07-26T18:01:45Z",
                    "agent": "Antigravity",
                    "type": "progress",
                    "task_id": self.TASK_4201,
                    "message": "Supervisor re-dispatched P0-TW-PAPER-ACTIVATE-001; task remains in progress.",
                },
            ),
        )

        self.assertFalse(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertEqual(decision.reason, "review_not_approved")
        self.assertTrue(decision.revoke_auto_merge)
        self.assertTrue(decision.auto_merge_request["present"])

    def test_a_behind_pr_cannot_regain_auto_merge_once_it_catches_up(self) -> None:
        """The stale base clearing changes nothing; the task is still unapproved."""

        decision = self._decide(
            task_id=self.TASK_4201,
            pr={**self.PR_4201, "mergeStateStatus": "CLEAN"},
            row_extra={"owner": "Antigravity", "reviewer": "Claude"},
        )

        self.assertFalse(decision.allow_merge)
        self.assertFalse(decision.allow_auto_merge)
        self.assertTrue(decision.revoke_auto_merge)

    def test_approval_binds_the_head_that_would_actually_merge(self) -> None:
        """Refreshing a BEHIND branch produces a head nobody approved."""

        refreshed_head = "e" * 40
        decision = self._decide(
            task_id=self.TASK_4201,
            pr={
                **self.PR_4201,
                "mergeStateStatus": "CLEAN",
                "autoMergeRequest": None,
                "headRefOid": refreshed_head,
                "commits": [{"oid": refreshed_head, "committedDate": "2026-07-26T23:50:00Z"}],
            },
            status="review_approved",
            row_extra={"owner": "Antigravity", "reviewer": "Claude"},
            events=(
                {
                    "ts": "2026-07-26T23:45:00Z",
                    "agent": "Claude",
                    "type": "review_approved",
                    "task_id": self.TASK_4201,
                    "message": "Approved head dc5d7128bad1717b23b6c750076b0cb47a213ae3.",
                    "review_binding": {
                        "pr": 4201,
                        "head_sha": "dc5d7128bad1717b23b6c750076b0cb47a213ae3",
                        "head_branch": f"task/{self.TASK_4201}",
                        "base": "dev",
                    },
                },
            ),
        )

        self.assertFalse(decision.allow_merge)
        self.assertEqual(decision.reason, "approval_head_mismatch")
        self.assertEqual(decision.head_oid, refreshed_head)
        self.assertEqual(
            decision.approval["approved_head_sha"],
            "dc5d7128bad1717b23b6c750076b0cb47a213ae3",
        )

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

    def test_approved_gated_pr_behind_dev_waits_after_clean_ephemeral_test(self) -> None:
        """A clean disposable merge never delegates authority to a queue."""

        runner = self._runner(open_pr(mergeStateStatus="BEHIND"), merge_base_returncode=1)

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[approval_event()]),
        )

        self.assertEqual(result.action, "waiting", result.detail)
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(merge_commands, [])
        self.assertNotIn(exact_head_rest_merge(), runner.commands)
        # The ephemeral test-merge must never be pushed anywhere.
        self.assertFalse(any(command[:2] == ["git", "push"] for command in runner.commands))

    def test_approved_gated_pr_behind_dev_blocks_on_a_real_conflict(self) -> None:
        """A genuine conflict is not the same as staleness: rebasing to fix
        it would move the head past what the reviewer approved, so this
        needs the owner, not another wait cycle."""

        runner = self._runner(
            open_pr(mergeStateStatus="BEHIND"),
            merge_base_returncode=1,
            ephemeral_merge_returncode=1,
        )

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[approval_event()]),
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("real conflict, not just staleness", result.detail)
        self.assertFalse(any(command[:3] == ["gh", "pr", "merge"] for command in runner.commands))
        self.assertIn(["git", "merge", "--abort"], runner.commands)

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
        self.assertEqual(merge_commands, [])
        self.assertIn(exact_head_rest_merge(), runner.commands)
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
            [["gh", "pr", "merge", "100", "--disable-auto"]],
        )
        self.assertIn(exact_head_rest_merge(), runner.commands)

    def test_successful_revocation_that_still_reads_armed_never_merges(self) -> None:
        """A zero exit from gh is not proof that GitHub withdrew the grant."""

        runner = self._runner(
            open_pr(autoMergeRequest={"enabledAt": "2026-07-26T11:45:00Z"}),
            disable_auto_clears_request=False,
        )

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[approval_event()]),
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("readback still shows autoMergeRequest armed", result.detail)
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(merge_commands, [["gh", "pr", "merge", "100", "--disable-auto"]])
        self.assertIn(["gh", "pr", "view", "100", "--json", "autoMergeRequest"], runner.commands)
        self.assertFalse(any("--match-head-commit" in command for command in runner.commands))
        self.assertFalse(
            any("scripts/ai_status.py" in " ".join(command) and "done" in command for command in runner.commands)
        )

    def test_failed_auto_merge_revocation_never_reaches_the_merge(self) -> None:
        """An armed merge grant the integrator could not revoke blocks the pass.

        Approving this exact head does not make it safe to merge while GitHub
        independently holds authority to land whatever head stands next.
        """

        runner = self._runner(
            open_pr(autoMergeRequest={"enabledAt": "2026-07-26T11:45:00Z"}),
            disable_auto_clears_request=False,
            disable_auto_returncode=1,
        )

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[approval_event()]),
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("--disable-auto` failed", result.detail)
        self.assertIn("readback still shows autoMergeRequest armed", result.detail)
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(merge_commands, [["gh", "pr", "merge", "100", "--disable-auto"]])
        self.assertIn(["gh", "pr", "view", "100", "--json", "autoMergeRequest"], runner.commands)
        self.assertFalse(any("--match-head-commit" in command for command in runner.commands))
        self.assertFalse(
            any("scripts/ai_status.py" in " ".join(command) and "done" in command for command in runner.commands)
        )

    def test_nonzero_revocation_can_continue_only_when_readback_proves_off(self) -> None:
        """The live grant, not the command exit status, is authoritative."""

        runner = self._runner(
            open_pr(autoMergeRequest={"enabledAt": "2026-07-26T11:45:00Z"}),
            disable_auto_returncode=1,
        )

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[approval_event()]),
        )

        self.assertEqual(result.action, "merged")
        self.assertIn(["gh", "pr", "view", "100", "--json", "autoMergeRequest"], runner.commands)
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(
            merge_commands,
            [["gh", "pr", "merge", "100", "--disable-auto"]],
        )
        self.assertIn(exact_head_rest_merge(), runner.commands)

    def test_unreadable_revocation_readback_never_reaches_the_merge(self) -> None:
        """A command result cannot replace the required live state proof."""

        runner = self._runner(
            open_pr(autoMergeRequest={"enabledAt": "2026-07-26T11:45:00Z"}),
            auto_merge_read_fails=True,
        )

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[approval_event()]),
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("cannot verify autoMergeRequest after revocation", result.detail)
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(merge_commands, [["gh", "pr", "merge", "100", "--disable-auto"]])
        self.assertIn(["gh", "pr", "view", "100", "--json", "autoMergeRequest"], runner.commands)
        self.assertFalse(any("--match-head-commit" in command for command in runner.commands))

    def test_failed_revocation_on_an_unapproved_pr_still_reports_the_gate_reason(self) -> None:
        """A gate refusal is the more precise diagnosis; it must not be masked."""

        runner = self._runner(
            open_pr(autoMergeRequest={"enabledAt": "2026-07-26T11:31:00Z"}),
            disable_auto_clears_request=False,
            disable_auto_returncode=1,
        )

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[]),
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("approval_record_missing", result.detail)
        self.assertIn("still set on this PR", result.detail)
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(merge_commands, [["gh", "pr", "merge", "100", "--disable-auto"]])
        self.assertIn(["gh", "pr", "view", "100", "--json", "autoMergeRequest"], runner.commands)

    def test_gated_pr_behind_dev_waits_without_queue_or_force_push(self) -> None:
        """A BEHIND exact head is observed, never queued, rebased, or pushed."""

        runner = self._runner(
            open_pr(mergeStateStatus="BEHIND"),
            merge_base_returncode=1,
        )

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[approval_event()]),
        )

        self.assertEqual(result.action, "waiting", result.detail)
        self.assertIn(
            ["git", "merge-base", "--is-ancestor", "origin/dev", "b" * 40],
            runner.commands,
        )
        self.assertFalse(any(command[:2] == ["git", "rebase"] for command in runner.commands))
        self.assertFalse(any(command[:2] == ["git", "push"] for command in runner.commands))
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(merge_commands, [])
        self.assertNotIn(exact_head_rest_merge(), runner.commands)

    def test_behind_gated_pr_has_auto_merge_revoked_before_any_merge_probe(self) -> None:
        """PR #4201's shape: BEHIND, unapproved, auto-merge still armed."""

        pr = open_pr(
            mergeStateStatus="BEHIND",
            autoMergeRequest={"enabledAt": "2026-07-26T11:31:00Z"},
        )
        runner = self._runner(pr)

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row(status="in_progress")], events=[]),
        )

        self.assertEqual(result.action, "blocked")
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(merge_commands, [["gh", "pr", "merge", "100", "--disable-auto"]])

    def test_approved_but_behind_gated_pr_revokes_then_waits_without_queue(self) -> None:
        """A stray auto-merge grant is revoked before anything else runs,
        whether the PR ultimately waits or merges -- the revocation is
        unconditional, not contingent on this PR's outcome."""

        runner = self._runner(
            open_pr(
                mergeStateStatus="BEHIND",
                autoMergeRequest={"enabledAt": "2026-07-26T11:31:00Z"},
            ),
            merge_base_returncode=1,
        )

        result = auto_integrator.integrate_candidate(
            self.candidate,
            self.settings,
            runner,
            execute=True,
            gate=self._gate(tasks=[task_row()], events=[approval_event()]),
        )

        self.assertEqual(result.action, "waiting", result.detail)
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(
            merge_commands,
            [["gh", "pr", "merge", "100", "--disable-auto"]],
        )
        self.assertNotIn(exact_head_rest_merge(), runner.commands)
        self.assertFalse(any(command[:2] == ["git", "rebase"] for command in runner.commands))
        self.assertFalse(any(command[:2] == ["git", "push"] for command in runner.commands))

    def test_concurrent_open_prs_for_one_task_branch_fail_closed(self) -> None:
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
                tasks=[task_row(status="in_progress", reviewer="Codex", merge_policy="merge_then_review")],
                events=[],
            ),
        )

        self.assertEqual(result.action, "merged")
        merge_commands = [c for c in runner.commands if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(merge_commands, [])
        self.assertIn(exact_head_rest_merge(), runner.commands)


class RealGitExactHeadIntegrationTests(unittest.TestCase):
    """Exercise the approved merge path against a merge-rich real git graph."""

    @staticmethod
    def _git(args: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )

    def test_current_merge_rich_exact_head_reaches_match_head_merge_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="exact-head-integrator-") as tmp_text:
            tmp = Path(tmp_text)
            origin = tmp / "origin.git"
            repo = tmp / "repo"
            self._git(["init", "--bare", "--initial-branch=dev", str(origin)], cwd=tmp)
            self._git(["init", "--initial-branch=dev", str(repo)], cwd=tmp)
            self._git(["config", "user.email", "gate@example.test"], cwd=repo)
            self._git(["config", "user.name", "Gate Fixture"], cwd=repo)
            self._git(["remote", "add", "origin", str(origin)], cwd=repo)

            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            self._git(["add", "base.txt"], cwd=repo)
            self._git(["commit", "-m", "base", "--no-verify"], cwd=repo)
            self._git(["push", "-u", "origin", "dev"], cwd=repo)

            self._git(["checkout", "-b", "task/ABC-001"], cwd=repo)
            (repo / "task.txt").write_text("task\n", encoding="utf-8")
            self._git(["add", "task.txt"], cwd=repo)
            self._git(["commit", "-m", "task change", "--no-verify"], cwd=repo)

            self._git(["checkout", "dev"], cwd=repo)
            (repo / "dev.txt").write_text("dev\n", encoding="utf-8")
            self._git(["add", "dev.txt"], cwd=repo)
            self._git(["commit", "-m", "dev advance", "--no-verify"], cwd=repo)
            self._git(["push", "origin", "dev"], cwd=repo)

            self._git(["checkout", "task/ABC-001"], cwd=repo)
            self._git(["merge", "--no-ff", "dev", "-m", "compose dev", "--no-verify"], cwd=repo)
            (repo / "after-merge.txt").write_text("reviewed\n", encoding="utf-8")
            self._git(["add", "after-merge.txt"], cwd=repo)
            self._git(["commit", "-m", "reviewed head", "--no-verify"], cwd=repo)
            self._git(["push", "-u", "origin", "task/ABC-001"], cwd=repo)

            exact_head = self._git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
            counts = self._git(
                ["rev-list", "--left-right", "--count", "origin/dev...HEAD"],
                cwd=repo,
            ).stdout.split()
            self.assertEqual(counts[0], "0")
            self.assertGreater(int(counts[1]), 0)
            self.assertGreater(
                int(self._git(["rev-list", "--min-parents=2", "--count", "HEAD"], cwd=repo).stdout),
                0,
            )

            pr = open_pr(
                headRefOid=exact_head,
                commits=[{"oid": exact_head, "committedDate": "2026-07-26T11:30:00Z"}],
            )

            class RealGitRunner(FakeRunner):
                def run(self, args: Sequence[str], **kwargs: Any):  # type: ignore[override]
                    command = [str(arg) for arg in args]
                    if command and command[0] == "git":
                        return auto_integrator.CommandRunner.run(self, args, **kwargs)
                    return super().run(args, **kwargs)

                def run_shell(self, command: str, **kwargs: Any):  # type: ignore[override]
                    return auto_integrator.CommandRunner.run_shell(self, command, **kwargs)

            runner = RealGitRunner(pr=pr)
            result = auto_integrator.integrate_candidate(
                auto_integrator.TaskCandidate(
                    task_id="ABC-001",
                    title="Ready",
                    owner="Codex",
                    reviewer="Claude",
                    branch="task/ABC-001",
                ),
                auto_integrator.Settings(),
                runner,
                root=repo,
                execute=True,
                extra_smoke_commands=(
                    f'test "$(git rev-parse HEAD)" = "{exact_head}"',
                    "git merge-base --is-ancestor origin/dev HEAD",
                ),
                gate=auto_integrator.ReviewGate(
                    state={"tasks": [task_row()]},
                    events=[
                        approval_event(
                            review_binding=approval_binding(head_sha=exact_head),
                        )
                    ],
                ),
            )

            self.assertEqual(result.action, "merged", result.detail)
            self.assertIn(
                ["git", "merge-base", "--is-ancestor", "origin/dev", exact_head],
                runner.commands,
            )
            self.assertTrue(
                any(
                    command[:4] == ["git", "worktree", "add", "--detach"]
                    and command[-1] == exact_head
                    for command in runner.commands
                )
            )
            self.assertFalse(any(command[:2] == ["git", "rebase"] for command in runner.commands))
            self.assertFalse(any(command[:2] == ["git", "push"] for command in runner.commands))
            self.assertIn(exact_head_rest_merge(exact_head), runner.commands)


FAKE_GH = r"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$GH_LOG"
if [[ "$1 $2" == "pr list" ]]; then
  if [[ "${GH_PR_LIST_FAIL:-0}" == "1" ]]; then
    exit 1
  fi
  printf '%s\n' "${GH_EXISTING_PR:-}"
  exit 0
fi
if [[ "$1 $2" == "pr merge" && " $* " == *" --disable-auto "* ]]; then
  if [[ "${GH_REVOKE_FAIL:-0}" == "1" ]]; then
    exit 1
  fi
  printf 'off\n' > "$GH_STATE_FILE"
  exit 0
fi
if [[ "$1 $2" == "pr view" ]]; then
  if [[ " $* " == *" --json autoMergeRequest "* ]]; then
    cat "$GH_STATE_FILE"
  elif [[ " $* " == *" --json number "* ]]; then
    echo "1"
  else
    echo "https://github.example/pr/1"
  fi
fi
exit 0
"""


class TaskFinalizeShellTests(unittest.TestCase):
    """The PR-opening helper must ask the gate before granting merge authority."""

    def _fixture(
        self,
        task: Mapping[str, Any],
        *,
        committed_delivery: bool,
    ) -> tuple[Path, Path]:
        tmp = Path(tempfile.mkdtemp(prefix="task-finalize-gate-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        origin = tmp / "origin.git"
        repo = tmp / "repo"
        self._git(["init", "--bare", "--initial-branch=dev", str(origin)], cwd=tmp)
        pre_receive = origin / "hooks" / "pre-receive"
        pre_receive.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ \"${GH_REQUIRE_OFF_BEFORE_PUSH:-0}\" == \"1\" ]]; then\n"
            "  [[ -n \"${GH_STATE_FILE:-}\" ]] || exit 91\n"
            "  [[ \"$(cat \"$GH_STATE_FILE\")\" == \"off\" ]] || exit 92\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        pre_receive.chmod(0o755)
        self._git(["init", "--initial-branch=dev", str(repo)], cwd=tmp)
        self._git(["config", "user.email", "gate@example.test"], cwd=repo)
        self._git(["config", "user.name", "Gate Fixture"], cwd=repo)
        self._git(["remote", "add", "origin", str(origin)], cwd=repo)

        helpers = repo / "scripts" / "git"
        helpers.mkdir(parents=True)
        source = Path(__file__).resolve().parent
        for name in (
            "safe_pr.sh",
            "task_finalize.sh",
            "task_review_merge_gate.py",
            "worker_commit.py",
        ):
            (helpers / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")
        orchestrator = repo / ".orchestrator"
        orchestrator.mkdir()
        (orchestrator / "common.py").write_text(
            (source.parents[1] / ".orchestrator" / "common.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (helpers / "safe_pr.sh").chmod(0o755)
        (helpers / "task_finalize.sh").chmod(0o755)
        (repo / "ai-status.json").write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
        self._git(["add", "-A"], cwd=repo)
        self._git(["commit", "-m", "base", "--no-verify"], cwd=repo)
        self._git(["push", "-u", "origin", "dev"], cwd=repo)

        if committed_delivery:
            self._git(["checkout", "-b", f"task/{task['id']}"], cwd=repo)
            (repo / "delivery.txt").write_text("delivered\n", encoding="utf-8")
            self._git(["add", "delivery.txt"], cwd=repo)
            self._git(["commit", "-m", f"{task['id']}: deliver", "--no-verify"], cwd=repo)
        else:
            (repo / "delivery.txt").write_text("delivered\n", encoding="utf-8")

        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(FAKE_GH, encoding="utf-8")
        (bin_dir / "gh").chmod(0o755)
        return repo, tmp

    @staticmethod
    def _git(args: Sequence[str], *, cwd: Path) -> None:
        subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)

    @staticmethod
    def _fake_gh_env(
        tmp: Path,
        repo: Path,
        *,
        auto_merge_state: str,
        revoke_fails: bool,
        existing_pr: bool = False,
        ambiguous_pr: bool = False,
        pr_lookup_fails: bool = False,
        require_off_before_push: bool = False,
    ) -> dict[str, str]:
        log = tmp / "gh.log"
        env = dict(os.environ)
        env["PATH"] = f"{tmp / 'bin'}:{env['PATH']}"
        env["GH_LOG"] = str(log)
        state_file = tmp / "auto-merge-state"
        state_file.write_text(auto_merge_state + "\n", encoding="utf-8")
        env["GH_STATE_FILE"] = str(state_file)
        env["GH_REVOKE_FAIL"] = "1" if revoke_fails else "0"
        env["GH_EXISTING_PR"] = "AMBIGUOUS" if ambiguous_pr else ("1" if existing_pr else "")
        env["GH_PR_LIST_FAIL"] = "1" if pr_lookup_fails else "0"
        env["GH_REQUIRE_OFF_BEFORE_PUSH"] = "1" if require_off_before_push else "0"
        env["PANTHEON_STATUS_ROOT"] = str(repo)
        return env

    def _run_finalize(
        self,
        task: Mapping[str, Any],
        *,
        auto_merge_state: str = "off",
        revoke_fails: bool = False,
        existing_pr: bool = False,
        ambiguous_pr: bool = False,
        pr_lookup_fails: bool = False,
        require_off_before_push: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        repo, tmp = self._fixture(task, committed_delivery=True)
        env = self._fake_gh_env(
            tmp,
            repo,
            auto_merge_state=auto_merge_state,
            revoke_fails=revoke_fails,
            existing_pr=existing_pr,
            ambiguous_pr=ambiguous_pr,
            pr_lookup_fails=pr_lookup_fails,
            require_off_before_push=require_off_before_push,
        )
        proc = subprocess.run(
            ["bash", "scripts/git/task_finalize.sh", str(task["id"])],
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
        )
        return proc, (tmp / "gh.log").read_text(encoding="utf-8")

    def _run_safe_pr(
        self,
        task: Mapping[str, Any],
        *,
        auto_merge_state: str = "off",
        revoke_fails: bool = False,
        existing_pr: bool = False,
        ambiguous_pr: bool = False,
        pr_lookup_fails: bool = False,
        require_off_before_push: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        repo, tmp = self._fixture(task, committed_delivery=False)
        message = tmp / "message.txt"
        message.write_text(
            f"{task['id']}: deliver fixture\n\n"
            "LLM-Agent: Codex\n"
            f"Task-ID: {task['id']}\n"
            "Reviewer: Claude\n",
            encoding="utf-8",
        )
        index_file = Path(f"/tmp/git-index-task-{task['id']}")
        self.addCleanup(index_file.unlink, missing_ok=True)
        env = self._fake_gh_env(
            tmp,
            repo,
            auto_merge_state=auto_merge_state,
            revoke_fails=revoke_fails,
            existing_pr=existing_pr,
            ambiguous_pr=ambiguous_pr,
            pr_lookup_fails=pr_lookup_fails,
            require_off_before_push=require_off_before_push,
        )
        proc = subprocess.run(
            [
                "bash",
                "scripts/git/safe_pr.sh",
                str(task["id"]),
                "--message-file",
                str(message),
                "--scope",
                "delivery.txt",
            ],
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
        )
        return proc, (tmp / "gh.log").read_text(encoding="utf-8")

    def test_gated_task_pr_is_opened_without_any_auto_merge(self) -> None:
        proc, calls = self._run_finalize(task_row(id="ABC-001", status="in_progress"))

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("pr create", calls)
        self.assertNotIn("--label auto-merge", calls)
        self.assertNotIn("--auto --merge", calls)
        self.assertNotIn("--disable-auto", calls)
        self.assertIn("auto-merge was already off", proc.stdout)

    def test_task_finalize_revokes_a_standing_request_and_verifies_it_off(self) -> None:
        proc, calls = self._run_finalize(
            task_row(id="ABC-001", status="in_progress"),
            auto_merge_state="armed",
        )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("pr merge task/ABC-001 --disable-auto", calls)
        self.assertGreaterEqual(calls.count("--json autoMergeRequest"), 2)
        self.assertIn("standing auto-merge request revoked and verified off", proc.stdout)

    def test_task_finalize_revokes_existing_pr_before_pushing_new_head(self) -> None:
        proc, calls = self._run_finalize(
            task_row(id="ABC-001", status="in_progress"),
            auto_merge_state="armed",
            existing_pr=True,
            require_off_before_push=True,
        )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("pr create", calls)
        self.assertIn("pr merge 1 --disable-auto", calls)
        self.assertGreaterEqual(calls.count("--json autoMergeRequest"), 3)
        self.assertIn("before push", proc.stdout)

    def test_task_finalize_ambiguous_pr_lookup_fails_before_push(self) -> None:
        proc, calls = self._run_finalize(
            task_row(id="ABC-001", status="in_progress"),
            ambiguous_pr=True,
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("multiple open PRs", proc.stderr)
        self.assertNotIn("→ push", proc.stdout)
        self.assertNotIn("pr create", calls)

    def test_task_finalize_fails_closed_when_revocation_leaves_request_armed(self) -> None:
        proc, calls = self._run_finalize(
            task_row(id="ABC-001", status="in_progress"),
            auto_merge_state="armed",
            revoke_fails=True,
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("pr merge task/ABC-001 --disable-auto", calls)
        self.assertGreaterEqual(calls.count("--json autoMergeRequest"), 2)
        self.assertIn("auto-merge remains armed", proc.stderr)
        self.assertNotIn("open with auto-merge disabled", proc.stdout)

    def test_merge_then_review_task_is_submitted_without_auto_merge(self) -> None:
        proc, calls = self._run_finalize(
            task_row(id="ABC-001", status="in_progress", reviewer="Codex", merge_policy="merge_then_review")
        )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("--label auto-merge", calls)
        self.assertNotIn("--auto --merge", calls)
        self.assertNotIn("--disable-auto", calls)
        self.assertIn("canonical supervisor integration runner", proc.stdout)

    def test_safe_pr_merge_then_review_is_submitted_without_auto_merge(self) -> None:
        proc, calls = self._run_safe_pr(
            task_row(
                id="ABC-001",
                status="in_progress",
                reviewer="Codex",
                merge_policy="merge_then_review",
            )
        )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("--label auto-merge", calls)
        self.assertNotIn("--auto --merge", calls)
        self.assertNotIn("--disable-auto", calls)
        self.assertIn("canonical supervisor integration runner", proc.stdout)

    def test_safe_pr_revokes_a_standing_request_and_verifies_it_off(self) -> None:
        proc, calls = self._run_safe_pr(
            task_row(id="ABC-001", status="in_progress"),
            auto_merge_state="armed",
        )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("pr merge 1 --disable-auto", calls)
        self.assertGreaterEqual(calls.count("--json autoMergeRequest"), 2)
        self.assertIn("standing auto-merge request revoked and verified off", proc.stdout)

    def test_safe_pr_revokes_existing_pr_before_pushing_new_head(self) -> None:
        proc, calls = self._run_safe_pr(
            task_row(id="ABC-001", status="in_progress"),
            auto_merge_state="armed",
            existing_pr=True,
            require_off_before_push=True,
        )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("pr create", calls)
        self.assertIn("pr merge 1 --disable-auto", calls)
        self.assertGreaterEqual(calls.count("--json autoMergeRequest"), 3)
        self.assertIn("pre-push revoke", proc.stdout)

    def test_safe_pr_unreadable_pr_lookup_fails_before_push(self) -> None:
        proc, calls = self._run_safe_pr(
            task_row(id="ABC-001", status="in_progress"),
            pr_lookup_fails=True,
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("cannot resolve existing PR", proc.stdout + proc.stderr)
        self.assertNotIn("push task branch", proc.stdout)
        self.assertNotIn("pr create", calls)

    def test_safe_pr_fails_closed_when_revocation_leaves_request_armed(self) -> None:
        proc, calls = self._run_safe_pr(
            task_row(id="ABC-001", status="in_progress"),
            auto_merge_state="armed",
            revoke_fails=True,
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("pr merge 1 --disable-auto", calls)
        self.assertGreaterEqual(calls.count("--json autoMergeRequest"), 2)
        self.assertIn("auto-merge remains armed", proc.stdout + proc.stderr)
        self.assertNotIn("DONE — task", proc.stdout)

    def test_safe_pr_distinguishes_an_already_off_request(self) -> None:
        proc, calls = self._run_safe_pr(task_row(id="ABC-001", status="in_progress"))

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("--disable-auto", calls)
        self.assertIn("auto-merge was already off", proc.stdout)


if __name__ == "__main__":
    unittest.main()
