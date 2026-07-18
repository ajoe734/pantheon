#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
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
            ("api", "repos/{owner}/{repo}/actions/runs/987/cancel", "-X", "POST"),
            ("api", "repos/%7Bowner%7D/%7Brepo%7D/actions/runs/654/force-cancel", "-X", "POST"),
            ("api", "repositories/1201361718/actions/runs/321/cancel", "-X", "POST"),
            ("api", "repositories/1230426594/actions/runs/321/force-cancel", "-X", "POST"),
        )
        for arguments in blocked:
            with self.subTest(arguments=arguments):
                self.assertIsNotNone(guard.blocked_reason(arguments))

    def test_raw_api_workflow_disable_is_blocked_with_method_in_either_order(self) -> None:
        blocked = (
            ("api", "--method", "PUT", "/repos/ajoe734/pantheon/actions/workflows/123/disable"),
            ("api", "repos/ajoe734/execute-plans/actions/workflows/deploy.yml/disable", "-XPUT"),
            ("api", "-X", "PUT", "repos/{owner}/{repo}/actions/workflows/deploy.yml/disable"),
            ("api", "-X", "PUT", "repositories/1201361718/actions/workflows/269991390/disable"),
            ("api", "repositories/1230426594/actions/workflows/deploy.yml/disable", "-XPUT"),
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
            mock.patch.object(common, "pinned_github_cli_binary", return_value=Path("/opt/pantheon-tools/bin/gh")),
            mock.patch.dict(os.environ, {"PATH": "/usr/local/bin:/usr/bin"}, clear=False),
        ):
            env = common.delivery_runtime_env({})

        self.assertEqual(
            env["PATH"],
            os.pathsep.join((str(common.ORCHESTRATOR_DIR / "bin"), "/usr/local/bin:/usr/bin")),
        )
        self.assertEqual(env["BASH_ENV"], str(common.ORCHESTRATOR_DIR / "bin" / "worker-shell-env"))
        self.assertEqual(env[common.WORKER_REAL_GH_BIN_ENV], "/opt/pantheon-tools/bin/gh")

    def test_pinned_real_gh_uses_natural_version_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            older = home / ".local" / "share" / "pantheon-orchestrator-tools" / "gh-2.9.0" / "bin" / "gh"
            newer = home / ".local" / "share" / "pantheon-orchestrator-tools" / "gh-2.10.0" / "bin" / "gh"
            for candidate in (older, newer):
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
                candidate.chmod(0o755)

            resolved = common.pinned_github_cli_binary(home)

        self.assertEqual(resolved, newer)

    def test_wrapper_uses_pinned_real_gh_when_worker_home_is_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            worker_home = root / "isolated-home"
            worker_home.mkdir()
            fake_gh = root / "real-gh"
            fake_gh.write_text("#!/usr/bin/env bash\nprintf 'real-gh:%s\\n' \"$*\"\n", encoding="utf-8")
            fake_gh.chmod(0o755)
            env = os.environ.copy()
            env.update({"HOME": str(worker_home), common.WORKER_REAL_GH_BIN_ENV: str(fake_gh)})

            result = subprocess.run(
                [str(ROOT / ".orchestrator" / "bin" / "gh"), "--version"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "real-gh:--version")

    def test_login_shell_reasserts_guard_before_user_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bypass_bin = root / "bypass-bin"
            bypass_bin.mkdir()
            bypass_gh = bypass_bin / "gh"
            bypass_gh.write_text("#!/usr/bin/env bash\nprintf 'bypass-gh\\n'\n", encoding="utf-8")
            bypass_gh.chmod(0o755)
            fake_real_gh = root / "real-gh"
            fake_real_gh.write_text("#!/usr/bin/env bash\nprintf 'real-gh:%s\\n' \"$*\"\n", encoding="utf-8")
            fake_real_gh.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": os.pathsep.join((str(bypass_bin), "/usr/local/bin", "/usr/bin", "/bin")),
                    "BASH_ENV": str(ROOT / ".orchestrator" / "bin" / "worker-shell-env"),
                    common.WORKER_REAL_GH_BIN_ENV: str(fake_real_gh),
                }
            )

            resolved = subprocess.run(
                ["bash", "-lc", "command -v gh; gh --version"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            blocked = subprocess.run(
                [
                    "bash",
                    "-lc",
                    "gh api 'repos/{owner}/{repo}/actions/runs/123/cancel' -X POST",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self.assertEqual(resolved.stdout.splitlines()[0], str(ROOT / ".orchestrator" / "bin" / "gh"))
        self.assertEqual(resolved.stdout.splitlines()[1], "real-gh:--version")
        self.assertEqual(blocked.returncode, guard.BLOCKED_EXIT_CODE, blocked.stdout + blocked.stderr)
        self.assertNotIn("real-gh:", blocked.stdout)


if __name__ == "__main__":
    unittest.main()
