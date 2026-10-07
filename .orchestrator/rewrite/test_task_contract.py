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


class HandoffTrailerAdmissionTests(unittest.TestCase):
    """The handoff admission validates commit trailers and frozen ranges."""

    def test_handoff_rejects_bad_trailer_with_commit_and_repair_guidance(self) -> None:
        from types import SimpleNamespace
        from unittest import mock
        from rewrite import task_contract
        import sys

        sys.path.insert(0, str(task_contract.Path(__file__).resolve().parents[2] / "scripts" / "git"))
        import check_commit_trailers
        bridge = task_contract._ai_status_module()._github_review_bridge_module()
        config = {"branch_workflow": {}}
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
            mock.patch.object(task_contract._ai_status_module(), "_done_delivery_repository_root", return_value=(task_contract.Path.cwd(), {})),
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
            mock.patch.object(task_contract._ai_status_module(), "_done_delivery_repository_root", return_value=(task_contract.Path.cwd(), {})),
            mock.patch.object(bridge, "validate_review_admission", return_value=validated),
            mock.patch.object(bridge, "list_pull_request_files", return_value=[]),
            mock.patch.object(check_commit_trailers, "check_range", side_effect=subprocess.CalledProcessError(128, "git log")),
        ):
            with self.assertRaisesRegex(SystemExit, "cannot validate commit trailer range"):
                task_contract.validate_handoff_pr_delivery_binding(
                    {"id": "T-1"}, {"branch_workflow": {}},
                    binding, review_file="docs/e.json",
                )


class HandoffWorkspaceGitRootAdmissionTests(unittest.TestCase):
    @staticmethod
    def _git(cwd, *args: str) -> str:
        import subprocess

        res = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return res.stdout.strip()

    def _git_fixture(self, root):
        import subprocess

        coord = root / "coord"
        coord.mkdir()
        source = root / "source"
        source.mkdir()
        self._git(source, "init", "-b", "dev")
        self._git(source, "config", "user.name", "Dev Owner")
        self._git(source, "config", "user.email", "dev@example.com")
        (source / "README.md").write_text("base\n", encoding="utf-8")
        self._git(source, "add", "README.md")
        self._git(
            source,
            "commit",
            "-m",
            "dev base\n\nLLM-Agent: Prior\nTask-ID: PRIOR-001\nReviewer: PriorRev",
        )
        base_sha = self._git(source, "rev-parse", "HEAD")
        self._git(
            source,
            "remote",
            "add",
            "origin",
            "https://github.com/ajoe734/pantheon.git",
        )

        ws = root / "ws"
        self._git(source, "worktree", "add", "-b", "task/T-1", str(ws))
        manifest_rel = "docs/evidence/T-1/evidence.json"
        manifest_path = ws / manifest_rel
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("evidence\n", encoding="utf-8")
        self._git(ws, "add", manifest_rel)
        self._git(
            ws,
            "commit",
            "-m",
            "T-1: add evidence\n\nLLM-Agent: Antigravity\nTask-ID: T-1\nReviewer: Antigravity2",
        )
        head_sha = self._git(ws, "rev-parse", "HEAD")

        config = {
            "coordination": {
                "repositories": {
                    "pantheon": {
                        "repo": "ajoe734/pantheon",
                        "integration_path": str(source),
                    }
                }
            },
            "branch_workflow": {},
        }
        task = {
            "id": "T-1",
            "artifacts": ["docs/evidence/T-1/evidence.json"],
        }
        binding = {
            "pr": 10,
            "head_sha": head_sha,
            "head_branch": "task/T-1",
            "base": "dev",
            "base_sha": base_sha,
        }
        return coord, source, ws, base_sha, head_sha, config, task, binding, manifest_rel

    def test_handoff_valid_normal_worker_path_with_leased_workspace(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock
        from rewrite import task_contract

        with tempfile.TemporaryDirectory() as td:
            coord, source, ws, base_sha, head_sha, config, task, binding, manifest_rel = (
                self._git_fixture(Path(td))
            )
            admitted = SimpleNamespace(as_dict=lambda: dict(binding), base_sha=base_sha)
            bridge = task_contract._ai_status_module()._github_review_bridge_module()
            ai_status = task_contract._ai_status_module()

            with mock.patch.dict(
                os.environ,
                {
                    "PANTHEON_WORKTREE_ROOT": str(ws),
                    "ORCH_WORKSPACE_PATH": str(ws),
                    "ORCH_RUN_ID": "run-t1",
                },
                clear=True,
            ), mock.patch.object(ai_status, "STATUS_ROOT", coord):
                ai_status._STATUS_COMMAND_LEASE_LOCAL.binding = {
                    "task_id": "T-1",
                    "workspace_repository_id": "pantheon",
                    "workspace_source_root": str(source),
                }
                try:
                    with mock.patch.object(
                        bridge, "validate_review_admission", return_value=admitted
                    ), mock.patch.object(
                        bridge,
                        "list_pull_request_files",
                        return_value=[{"filename": manifest_rel, "additions": 1, "deletions": 0}],
                    ), mock.patch.object(
                        bridge, "revalidate_pull_request_snapshot"
                    ):
                        res = task_contract.validate_handoff_pr_delivery_binding(
                            task, config, binding, review_file=manifest_rel
                        )
                        self.assertEqual(res["pr"], 10)
                        self.assertEqual(res["head_sha"], head_sha)
                finally:
                    ai_status._clear_status_command_lease_binding()

    def test_handoff_valid_normal_operator_path_with_integration_path(self) -> None:
        import json
        import os
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock
        from rewrite import task_contract

        with tempfile.TemporaryDirectory() as td:
            coord, source, _ws, base_sha, _head, config, task, binding, manifest_rel = (
                self._git_fixture(Path(td))
            )
            manifest_path = source / manifest_rel
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text("evidence\n", encoding="utf-8")
            self._git(source, "add", manifest_rel)
            self._git(
                source,
                "commit",
                "-m",
                "T-1: add evidence\n\nLLM-Agent: Antigravity\nTask-ID: T-1\nReviewer: Antigravity2",
            )
            op_head_sha = self._git(source, "rev-parse", "HEAD")
            op_binding = dict(binding, head_sha=op_head_sha)
            admitted = SimpleNamespace(as_dict=lambda: dict(op_binding), base_sha=base_sha)
            bridge = task_contract._ai_status_module()._github_review_bridge_module()
            ai_status = task_contract._ai_status_module()

            live_config_file = Path(td) / "live-supervisor-config.json"
            live_config_file.write_text(
                json.dumps({"coordination": config["coordination"]}),
                encoding="utf-8",
            )

            with mock.patch.dict(
                os.environ,
                {"PANTHEON_LIVE_SUPERVISOR_CONFIG": str(live_config_file)},
                clear=True,
            ), mock.patch.object(
                ai_status, "STATUS_ROOT", coord
            ):
                with mock.patch.object(
                    bridge, "validate_review_admission", return_value=admitted
                ), mock.patch.object(
                    bridge,
                    "list_pull_request_files",
                    return_value=[{"filename": manifest_rel, "additions": 1, "deletions": 0}],
                ), mock.patch.object(
                    bridge, "revalidate_pull_request_snapshot"
                ):
                    res = task_contract.validate_handoff_pr_delivery_binding(
                        task, config, op_binding, review_file=manifest_rel
                    )
                    self.assertEqual(res["pr"], 10)
                    self.assertEqual(res["head_sha"], op_head_sha)

    def test_handoff_rejects_forged_workspace_not_registered_in_checkout(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock
        from rewrite import task_contract

        with tempfile.TemporaryDirectory() as td:
            coord, source, _ws, base_sha, head_sha, config, task, binding, manifest_rel = (
                self._git_fixture(Path(td))
            )
            unrelated = Path(td) / "unrelated"
            unrelated.mkdir()
            self._git(unrelated, "init", "-b", "task/T-1")
            self._git(unrelated, "config", "user.name", "X")
            self._git(unrelated, "config", "user.email", "x@x.com")
            (unrelated / "a").write_text("a", encoding="utf-8")
            self._git(unrelated, "add", "a")
            self._git(unrelated, "commit", "-m", "init")
            self._git(
                unrelated, "remote", "add", "origin", "https://github.com/ajoe734/pantheon.git"
            )

            admitted = SimpleNamespace(as_dict=lambda: dict(binding), base_sha=base_sha)
            bridge = task_contract._ai_status_module()._github_review_bridge_module()
            ai_status = task_contract._ai_status_module()

            with mock.patch.dict(
                os.environ,
                {
                    "PANTHEON_WORKTREE_ROOT": str(unrelated),
                    "ORCH_WORKSPACE_PATH": str(unrelated),
                    "ORCH_RUN_ID": "run-t1",
                },
                clear=True,
            ), mock.patch.object(ai_status, "STATUS_ROOT", coord):
                ai_status._STATUS_COMMAND_LEASE_LOCAL.binding = {
                    "task_id": "T-1",
                    "workspace_repository_id": "pantheon",
                    "workspace_source_root": str(source),
                }
                try:
                    with mock.patch.object(
                        bridge, "validate_review_admission", return_value=admitted
                    ), mock.patch.object(
                        bridge,
                        "list_pull_request_files",
                        return_value=[{"filename": manifest_rel, "additions": 1, "deletions": 0}],
                    ):
                        with self.assertRaisesRegex(
                            SystemExit, "delivery workspace is not registered"
                        ):
                            task_contract.validate_handoff_pr_delivery_binding(
                                task, config, binding, review_file=manifest_rel
                            )
                finally:
                    ai_status._clear_status_command_lease_binding()

    def test_handoff_rejects_repo_mismatch_against_worker_lease(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock
        from rewrite import task_contract

        with tempfile.TemporaryDirectory() as td:
            coord, source, ws, base_sha, head_sha, config, task, binding, manifest_rel = (
                self._git_fixture(Path(td))
            )
            admitted = SimpleNamespace(as_dict=lambda: dict(binding), base_sha=base_sha)
            bridge = task_contract._ai_status_module()._github_review_bridge_module()
            ai_status = task_contract._ai_status_module()

            with mock.patch.dict(
                os.environ,
                {
                    "PANTHEON_WORKTREE_ROOT": str(ws),
                    "ORCH_WORKSPACE_PATH": str(ws),
                    "ORCH_RUN_ID": "run-t1",
                },
                clear=True,
            ), mock.patch.object(ai_status, "STATUS_ROOT", coord):
                ai_status._STATUS_COMMAND_LEASE_LOCAL.binding = {
                    "task_id": "T-1",
                    "workspace_repository_id": "execute_plans",
                    "workspace_source_root": str(source),
                }
                try:
                    with mock.patch.object(
                        bridge, "validate_review_admission", return_value=admitted
                    ), mock.patch.object(
                        bridge,
                        "list_pull_request_files",
                        return_value=[{"filename": manifest_rel, "additions": 1, "deletions": 0}],
                    ):
                        with self.assertRaisesRegex(
                            SystemExit, "worker lease repository does not match"
                        ):
                            task_contract.validate_handoff_pr_delivery_binding(
                                task, config, binding, review_file=manifest_rel
                            )
                finally:
                    ai_status._clear_status_command_lease_binding()

    def test_handoff_rejects_worker_lease_task_id_mismatch(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock
        from rewrite import task_contract

        with tempfile.TemporaryDirectory() as td:
            coord, source, ws, base_sha, head_sha, config, task, binding, manifest_rel = (
                self._git_fixture(Path(td))
            )
            admitted = SimpleNamespace(as_dict=lambda: dict(binding), base_sha=base_sha)
            bridge = task_contract._ai_status_module()._github_review_bridge_module()
            ai_status = task_contract._ai_status_module()

            with mock.patch.dict(
                os.environ,
                {
                    "PANTHEON_WORKTREE_ROOT": str(ws),
                    "ORCH_WORKSPACE_PATH": str(ws),
                    "ORCH_RUN_ID": "run-t1",
                },
                clear=True,
            ), mock.patch.object(ai_status, "STATUS_ROOT", coord):
                ai_status._STATUS_COMMAND_LEASE_LOCAL.binding = {
                    "task_id": "T-OTHER",
                    "workspace_repository_id": "pantheon",
                    "workspace_source_root": str(source),
                }
                try:
                    with mock.patch.object(
                        bridge, "validate_review_admission", return_value=admitted
                    ), mock.patch.object(
                        bridge,
                        "list_pull_request_files",
                        return_value=[{"filename": manifest_rel, "additions": 1, "deletions": 0}],
                    ):
                        with self.assertRaisesRegex(
                            SystemExit, "worker lease task does not match"
                        ):
                            task_contract.validate_handoff_pr_delivery_binding(
                                task, config, binding, review_file=manifest_rel
                            )
                finally:
                    ai_status._clear_status_command_lease_binding()

    def test_handoff_rejects_unavailable_commit_objects(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock
        from rewrite import task_contract

        with tempfile.TemporaryDirectory() as td:
            coord, source, ws, base_sha, _head, config, task, binding, manifest_rel = (
                self._git_fixture(Path(td))
            )
            unavail_binding = dict(binding, head_sha="0" * 40)
            unavail_admitted = SimpleNamespace(
                as_dict=lambda: dict(unavail_binding), base_sha=base_sha
            )
            bridge = task_contract._ai_status_module()._github_review_bridge_module()
            ai_status = task_contract._ai_status_module()

            with mock.patch.dict(
                os.environ,
                {
                    "PANTHEON_WORKTREE_ROOT": str(ws),
                    "ORCH_WORKSPACE_PATH": str(ws),
                    "ORCH_RUN_ID": "run-t1",
                },
                clear=True,
            ), mock.patch.object(ai_status, "STATUS_ROOT", coord):
                ai_status._STATUS_COMMAND_LEASE_LOCAL.binding = {
                    "task_id": "T-1",
                    "workspace_repository_id": "pantheon",
                    "workspace_source_root": str(source),
                }
                try:
                    with mock.patch.object(
                        bridge, "validate_review_admission", return_value=unavail_admitted
                    ), mock.patch.object(
                        bridge,
                        "list_pull_request_files",
                        return_value=[{"filename": manifest_rel, "additions": 1, "deletions": 0}],
                    ):
                        with self.assertRaisesRegex(
                            SystemExit, "cannot validate commit trailer range"
                        ):
                            task_contract.validate_handoff_pr_delivery_binding(
                                task, config, unavail_binding, review_file=manifest_rel
                            )
                finally:
                    ai_status._clear_status_command_lease_binding()

    def test_handoff_rejects_stale_pr_head_with_invalid_trailers(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock
        from rewrite import task_contract

        with tempfile.TemporaryDirectory() as td:
            coord, source, ws, base_sha, _head, config, task, binding, manifest_rel = (
                self._git_fixture(Path(td))
            )
            (ws / "extra.txt").write_text("extra\n", encoding="utf-8")
            self._git(ws, "add", "extra.txt")
            self._git(
                ws,
                "commit",
                "-m",
                "T-1: missing reviewer\n\nLLM-Agent: Antigravity\nTask-ID: T-1",
            )
            bad_head = self._git(ws, "rev-parse", "HEAD")
            bad_binding = dict(binding, head_sha=bad_head)
            bad_admitted = SimpleNamespace(
                as_dict=lambda: dict(bad_binding), base_sha=base_sha
            )
            bridge = task_contract._ai_status_module()._github_review_bridge_module()
            ai_status = task_contract._ai_status_module()

            with mock.patch.dict(
                os.environ,
                {
                    "PANTHEON_WORKTREE_ROOT": str(ws),
                    "ORCH_WORKSPACE_PATH": str(ws),
                    "ORCH_RUN_ID": "run-t1",
                },
                clear=True,
            ), mock.patch.object(ai_status, "STATUS_ROOT", coord):
                ai_status._STATUS_COMMAND_LEASE_LOCAL.binding = {
                    "task_id": "T-1",
                    "workspace_repository_id": "pantheon",
                    "workspace_source_root": str(source),
                }
                try:
                    with mock.patch.object(
                        bridge, "validate_review_admission", return_value=bad_admitted
                    ), mock.patch.object(
                        bridge,
                        "list_pull_request_files",
                        return_value=[{"filename": manifest_rel, "additions": 1, "deletions": 0}],
                    ):
                        with self.assertRaisesRegex(
                            SystemExit, "commit trailers are invalid"
                        ):
                            task_contract.validate_handoff_pr_delivery_binding(
                                task, config, bad_binding, review_file=manifest_rel
                            )
                finally:
                    ai_status._clear_status_command_lease_binding()


if __name__ == "__main__":
    unittest.main()
