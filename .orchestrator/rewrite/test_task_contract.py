from __future__ import annotations

import unittest

from rewrite.task_contract import (
    acceptance_identity_mentions,
    validate_reassignment_against_acceptance,
    validate_role_based_acceptance,
)


class TaskContractTests(unittest.TestCase):
    def test_role_based_acceptance_is_allowed(self) -> None:
        validate_role_based_acceptance(
            ["Assigned reviewer approves the exact PR head."],
            ["Codex2", "Antigravity"],
        )

    def test_configured_identity_in_new_acceptance_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Codex2"):
            validate_role_based_acceptance(
                ["Codex2 independently approves the exact head."],
                ["Codex", "Codex2"],
            )

    def test_reassignment_rejects_changed_identity_pin(self) -> None:
        task = {
            "owner": "Claude",
            "reviewer": "Codex2",
            "acceptance": ["Codex2 independently approves the exact head."],
        }
        with self.assertRaisesRegex(ValueError, "supersede"):
            validate_reassignment_against_acceptance(
                task,
                new_owner="Claude",
                new_reviewer="Antigravity",
            )

    def test_unrelated_identity_does_not_block_owner_move(self) -> None:
        task = {
            "owner": "Claude",
            "reviewer": "Codex2",
            "acceptance": ["Codex2 independently approves the exact head."],
        }
        validate_reassignment_against_acceptance(
            task,
            new_owner="Antigravity",
            new_reviewer="Codex2",
        )
        self.assertIn(
            "Codex2",
            acceptance_identity_mentions(task["acceptance"], ["Codex2"]),
        )


class HandoffDiffBudgetGateTests(unittest.TestCase):
    """The handoff admission runs the diff budget on the real PR file list."""

    def _admit(self, task, pr_files):
        from types import SimpleNamespace
        from unittest import mock

        from rewrite import task_contract

        bridge = task_contract._ai_status_module()._github_review_bridge_module()
        config = {"branch_workflow": {"diff_budget": {"enabled": True}}}
        binding = {"pr": 7, "head_sha": "a" * 40, "head_branch": "task/T-1", "base": "dev"}
        validated = SimpleNamespace(as_dict=lambda: dict(binding))
        with (
            mock.patch.object(task_contract, "validate_task_repository_scope", return_value="pantheon"),
            mock.patch.object(task_contract, "repository_slug", return_value="o/r"),
            mock.patch.object(task_contract, "validate_review_manifest_contract_path", return_value="docs/e.json"),
            mock.patch.object(task_contract, "validate_task_artifact_diff_scope"),
            mock.patch.object(bridge, "validate_review_admission", return_value=validated),
            mock.patch.object(bridge, "list_pull_request_files", return_value=pr_files),
            mock.patch.object(bridge, "revalidate_pull_request_snapshot"),
        ):
            return task_contract.validate_handoff_pr_delivery_binding(
                task, config, binding, review_file="docs/e.json"
            )

    def test_refactor_growth_blocks_handoff(self) -> None:
        files = [{"filename": "svc/a.py", "additions": 50, "deletions": 10}]
        with self.assertRaises(SystemExit) as ctx:
            self._admit({"id": "T-1", "change_class": "refactor"}, files)
        self.assertIn("net +40", str(ctx.exception))

    def test_refactor_shrink_is_admitted(self) -> None:
        files = [{"filename": "svc/a.py", "additions": 10, "deletions": 50}]
        result = self._admit({"id": "T-1", "change_class": "refactor"}, files)
        self.assertEqual(result["pr"], 7)

    def test_change_class_requires_pr_delivery(self) -> None:
        from rewrite.task_contract import requires_pr_delivery_binding

        self.assertTrue(requires_pr_delivery_binding({"change_class": "simplify"}))
        self.assertFalse(requires_pr_delivery_binding({}))


if __name__ == "__main__":
    unittest.main()
