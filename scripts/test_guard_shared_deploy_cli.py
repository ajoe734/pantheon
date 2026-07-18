#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".orchestrator"))

import common
import guard_shared_deploy_cli as guard


class SharedDeployCliGuardTests(unittest.TestCase):
    def test_read_commands_are_allowed(self) -> None:
        allowed = (
            ("workflow", "view", "nonprod-deploy.yml", "--repo", "ajoe734/pantheon"),
            ("run", "view", "123", "--repo", "ajoe734/pantheon"),
            ("api", "/repos/ajoe734/pantheon/actions/workflows/269991390"),
            ("api", "repos/ajoe734/other/actions/runs/123/cancel", "--method", "POST"),
        )
        for arguments in allowed:
            with self.subTest(arguments=arguments):
                self.assertIsNone(guard.blocked_reason(arguments))

    def test_shorthand_mutations_are_blocked(self) -> None:
        blocked = (
            ("workflow", "disable", "nonprod-deploy.yml"),
            ("workflow", "--repo", "ajoe734/pantheon", "disable", "nonprod-deploy.yml"),
            ("--repo", "ajoe734/pantheon", "run", "cancel", "123"),
            ("run", "--repo", "ajoe734/pantheon", "cancel", "123"),
            ("run", "force-cancel", "123", "--repo", "ajoe734/pantheon"),
        )
        for arguments in blocked:
            with self.subTest(arguments=arguments):
                self.assertIsNotNone(guard.blocked_reason(arguments))

    def test_worker_cannot_create_or_import_cli_aliases(self) -> None:
        blocked = (
            ("alias", "set", "stop-run", "run cancel"),
            ("alias", "--shell", "set", "stop-run", "gh api /repos/example/actions/runs/1/cancel"),
            ("alias", "import", "aliases.yml"),
        )
        for arguments in blocked:
            with self.subTest(arguments=arguments):
                self.assertIsNotNone(guard.blocked_reason(arguments))

    def test_raw_api_run_mutations_are_blocked_for_protected_repositories(self) -> None:
        blocked = (
            ("api", "repos/ajoe734/pantheon/actions/runs/123/cancel", "-X", "POST"),
            ("api", "-X", "POST", "/repos/ajoe734/execute-plans/actions/runs/456/force-cancel"),
            (
                "api",
                "--method=POST",
                "https://api.github.com/repos/ajoe734/pantheon/actions/runs/789/cancel",
            ),
        )
        for arguments in blocked:
            with self.subTest(arguments=arguments):
                self.assertIsNotNone(guard.blocked_reason(arguments))

    def test_raw_api_workflow_disable_is_blocked_with_method_in_either_order(self) -> None:
        blocked = (
            ("api", "--method", "PUT", "/repos/ajoe734/pantheon/actions/workflows/123/disable"),
            ("api", "repos/ajoe734/execute-plans/actions/workflows/deploy.yml/disable", "-XPUT"),
        )
        for arguments in blocked:
            with self.subTest(arguments=arguments):
                self.assertIsNotNone(guard.blocked_reason(arguments))

    def test_allowed_command_execs_the_resolved_binary_without_reentering_path(self) -> None:
        real_gh = Path("/opt/pantheon-tools/bin/gh")
        with mock.patch.object(guard.os, "execv") as execv:
            exit_code = guard.main(["--gh-bin", str(real_gh), "--", "run", "view", "123"])
        self.assertEqual(exit_code, 0)
        execv.assert_called_once_with(str(real_gh), [str(real_gh), "run", "view", "123"])

    def test_blocked_command_returns_nonzero_without_exec(self) -> None:
        with mock.patch.object(guard.os, "execv") as execv:
            exit_code = guard.main(
                ["--gh-bin", "/opt/pantheon-tools/bin/gh", "--", "workflow", "disable", "deploy.yml"]
            )
        self.assertEqual(exit_code, guard.BLOCKED_EXIT_CODE)
        execv.assert_not_called()


class WorkerRuntimePathTests(unittest.TestCase):
    def test_delivery_runtime_env_prefixes_worker_bin_to_inherited_path(self) -> None:
        workspace = Path("/tmp/pantheon-task-worktree")
        status_root = Path("/tmp/pantheon-status-root")
        with (
            mock.patch.object(common, "delivery_workspace_root", return_value=workspace),
            mock.patch.object(common, "delivery_status_root", return_value=status_root),
            mock.patch.object(common, "status_command_runtime_env", return_value={}),
            mock.patch.dict(os.environ, {"PATH": "/usr/local/bin:/usr/bin"}, clear=False),
        ):
            env = common.delivery_runtime_env({})

        self.assertEqual(
            env["PATH"],
            os.pathsep.join((str(common.ORCHESTRATOR_DIR / "bin"), "/usr/local/bin:/usr/bin")),
        )


if __name__ == "__main__":
    unittest.main()
