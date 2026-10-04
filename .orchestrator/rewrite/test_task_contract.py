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
        import sys
        sys.path.insert(0, str(task_contract.Path(__file__).resolve().parents[2] / "scripts" / "git"))
        import check_commit_trailers
        config = {"branch_workflow": {"diff_budget": {"enabled": True}}}
        binding = {"pr": 7, "head_sha": "a" * 40, "head_branch": "task/T-1", "base": "dev", "base_sha": "b" * 40}
        validated = SimpleNamespace(as_dict=lambda: dict(binding), base_sha="b" * 40)
        with (
            mock.patch.object(task_contract, "validate_task_repository_scope", return_value="pantheon"),
            mock.patch.object(task_contract, "repository_slug", return_value="o/r"),
            mock.patch.object(task_contract, "validate_review_manifest_contract_path", return_value="docs/e.json"),
            mock.patch.object(task_contract, "validate_task_artifact_diff_scope"),
            mock.patch.object(bridge, "validate_review_admission", return_value=validated),
            mock.patch.object(bridge, "list_pull_request_files", return_value=pr_files),
            mock.patch.object(bridge, "revalidate_pull_request_snapshot"),
            mock.patch.object(check_commit_trailers, "check_range", return_value=[]),
            mock.patch.object(task_contract, "repository_local_path", return_value=task_contract.Path.cwd()),
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

    def test_handoff_rejects_bad_trailer_with_commit_and_repair_guidance(self) -> None:
        from types import SimpleNamespace
        from unittest import mock
        from rewrite import task_contract
        import sys

        sys.path.insert(0, str(task_contract.Path(__file__).resolve().parents[2] / "scripts" / "git"))
        import check_commit_trailers
        bridge = task_contract._ai_status_module()._github_review_bridge_module()
        config = {"branch_workflow": {"diff_budget": {"enabled": True}}}
        binding = {"pr": 7, "head_sha": "a" * 40, "head_branch": "task/T-1", "base": "dev"}
        validated = SimpleNamespace(as_dict=lambda: {**binding, "base_sha": "b" * 40}, base_sha="b" * 40)
        task = {"id": "T-1"}
        original = (dict(task), dict(config), dict(binding))
        with (
            mock.patch.object(task_contract, "validate_task_repository_scope", return_value="pantheon"),
            mock.patch.object(task_contract, "repository_slug", return_value="o/r"),
            mock.patch.object(task_contract, "validate_review_manifest_contract_path", return_value="docs/e.json"),
            mock.patch.object(task_contract, "validate_task_artifact_diff_scope"),
            mock.patch.object(task_contract, "repository_local_path", return_value=task_contract.Path.cwd()),
            mock.patch.object(bridge, "validate_review_admission", return_value=validated),
            mock.patch.object(bridge, "list_pull_request_files", return_value=[]),
            mock.patch.object(bridge, "revalidate_pull_request_snapshot") as revalidate,
            mock.patch.object(check_commit_trailers, "check_range", return_value=[("deadbeef", ["missing trailer: Reviewer"])]) as checked_range,
        ):
            with self.assertRaisesRegex(SystemExit, "worker_commit.py") as ctx:
                task_contract.validate_handoff_pr_delivery_binding(
                    task, config, binding, review_file="docs/e.json"
                )
        self.assertIn("deadbeef", str(ctx.exception))
        self.assertIn("missing trailer: Reviewer", str(ctx.exception))
        checked_range.assert_called_once_with(
            "b" * 40 + ".." + "a" * 40,
            skip_merge=True,
            delivery_class="auto",
            expected_task_id="T-1",
            repository_root=task_contract.Path.cwd(),
        )
        revalidate.assert_not_called()
        self.assertEqual((task, config, binding), original)

    def test_missing_frozen_range_objects_reject_handoff_clearly(self) -> None:
        from types import SimpleNamespace
        from unittest import mock
        from rewrite import task_contract
        import subprocess
        import sys

        sys.path.insert(0, str(task_contract.Path(__file__).resolve().parents[2] / "scripts" / "git"))
        import check_commit_trailers
        bridge = task_contract._ai_status_module()._github_review_bridge_module()
        binding = {"pr": 7, "head_sha": "a" * 40, "head_branch": "task/T-1", "base": "dev"}
        validated = SimpleNamespace(as_dict=lambda: {**binding, "base_sha": "b" * 40}, base_sha="b" * 40)
        with (
            mock.patch.object(task_contract, "validate_task_repository_scope", return_value="pantheon"),
            mock.patch.object(task_contract, "repository_slug", return_value="o/r"),
            mock.patch.object(task_contract, "validate_review_manifest_contract_path", return_value="docs/e.json"),
            mock.patch.object(task_contract, "validate_task_artifact_diff_scope"),
            mock.patch.object(task_contract, "repository_local_path", return_value=task_contract.Path.cwd()),
            mock.patch.object(bridge, "validate_review_admission", return_value=validated),
            mock.patch.object(bridge, "list_pull_request_files", return_value=[]),
            mock.patch.object(check_commit_trailers, "check_range", side_effect=subprocess.CalledProcessError(128, "git log")),
        ):
            with self.assertRaisesRegex(SystemExit, "cannot validate commit trailer range"):
                task_contract.validate_handoff_pr_delivery_binding(
                    {"id": "T-1"}, {"branch_workflow": {"diff_budget": {"enabled": False}}},
                    binding, review_file="docs/e.json",
                )

    def test_change_class_requires_pr_delivery(self) -> None:
        from rewrite.task_contract import requires_pr_delivery_binding

        self.assertTrue(requires_pr_delivery_binding({"change_class": "simplify"}))
        self.assertFalse(requires_pr_delivery_binding({}))


if __name__ == "__main__":
    unittest.main()
