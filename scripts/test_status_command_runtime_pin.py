#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class StatusCommandRuntimePinTests(unittest.TestCase):
    def _init_repo(self, path: Path, *, remote: str = "https://github.com/ajoe734/pantheon.git") -> str:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=path, check=True)
        (path / ".gitkeep").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=path, check=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", "HEAD"], cwd=path, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()

    def _commit_all(self, repo: Path, message: str = "commit") -> str:
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
        subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", "HEAD"], cwd=repo, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    def _copy_wrapper(self, worktree: Path) -> None:
        target = worktree / "scripts" / "ai-status.sh"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / "scripts" / "ai-status.sh", target)

    def _copy_full_status_tooling(self, destination: Path) -> None:
        for rel in (
            "scripts/ai_status.py",
            "scripts/ai-status.sh",
            "scripts/loop_done_guardrail.py",
            ".orchestrator/common.py",
            ".orchestrator/runtime_state.py",
            ".orchestrator/task_archive.py",
            ".orchestrator/multi_repo_registry.py",
            ".orchestrator/config.json",
        ):
            source = REPO_ROOT / rel
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _write_stub_command(self, command_root: Path) -> str:
        wrapper = command_root / "scripts" / "ai-status.sh"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / "scripts" / "ai-status.sh", wrapper)
        script = command_root / "scripts" / "ai_status.py"
        script.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import json, os, sys",
                    "print(json.dumps({",
                    "  'marker': 'installed-command-runtime',",
                    "  'argv': sys.argv[1:],",
                    "  'cwd': os.getcwd(),",
                    "  'status_root': os.environ.get('PANTHEON_STATUS_ROOT'),",
                    "  'delivery_root': os.environ.get('PANTHEON_WORKTREE_ROOT'),",
                    "  'wrapper_root': os.environ.get('PANTHEON_STATUS_COMMAND_WRAPPER_ROOT'),",
                    "}, sort_keys=True))",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return self._commit_all(command_root, "install command runtime")

    def _base_env(self, *, status_root: Path, worktree: Path, command_root: Path, command_sha: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "AI_NAME": "Codex2",
                "PANTHEON_STATUS_ROOT": str(status_root),
                "PANTHEON_WORKTREE_ROOT": str(worktree),
                "ORCH_WORKSPACE_PATH": str(worktree),
                "ORCH_RUN_ID": "codex-runtime-pin-test",
                "ORCH_RUNNER_STATUS_PATH": str(status_root / ".orchestrator" / "worker-runtime" / "status" / "run.json"),
                "ORCH_HEARTBEAT_PATH": str(status_root / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"),
                "PANTHEON_COMMAND_ROOT": str(command_root),
                "PANTHEON_COMMAND_RUNTIME_SHA": command_sha,
                "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
                "PANTHEON_COMMAND_BASE_REF": "origin/dev",
            }
        )
        return env

    def test_stale_worktree_wrapper_execs_installed_command_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="status-command-pin-") as tmpdir:
            root = Path(tmpdir)
            status_root = root / "status-root"
            command_root = root / "command-root"
            worktree = root / "task-worktree"
            self._init_repo(status_root)
            self._init_repo(command_root)
            self._init_repo(worktree)
            self._copy_wrapper(worktree)
            (worktree / "scripts" / "ai_status.py").write_text(
                "raise SystemExit('stale local ai_status.py executed')\n",
                encoding="utf-8",
            )
            command_sha = self._write_stub_command(command_root)
            env = self._base_env(
                status_root=status_root,
                worktree=worktree,
                command_root=command_root,
                command_sha=command_sha,
            )

            proc = subprocess.run(
                ["bash", str(command_root / "scripts" / "ai-status.sh"), "show", "TASK-1"],
                cwd=worktree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["marker"], "installed-command-runtime")
        self.assertEqual(payload["argv"], ["show", "TASK-1"])
        self.assertEqual(payload["delivery_root"], str(worktree))
        self.assertEqual(payload["status_root"], str(status_root))
        self.assertEqual(payload["wrapper_root"], str(command_root))

    def test_wrapper_rejects_invalid_command_root_before_local_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="status-command-pin-invalid-") as tmpdir:
            root = Path(tmpdir)
            status_root = root / "status-root"
            command_root = root / "command-root"
            worktree = root / "task-worktree"
            wrong_remote = root / "wrong-remote"
            unmerged = root / "unmerged"
            nested_repo = root / "nested-repo"
            self._init_repo(status_root)
            self._init_repo(command_root)
            self._init_repo(worktree)
            self._init_repo(wrong_remote, remote="https://github.com/example/not-pantheon.git")
            self._init_repo(unmerged)
            nested_sha = self._init_repo(nested_repo)
            nested_subdir = nested_repo / "subdir"
            nested_subdir.mkdir()
            command_link = root / "command-link"
            command_link.symlink_to(command_root, target_is_directory=True)
            self._copy_wrapper(worktree)
            local_sentinel = worktree / "local-sentinel.txt"
            local_sentinel.write_text("unchanged\n", encoding="utf-8")
            (worktree / "scripts" / "ai_status.py").write_text(
                "from pathlib import Path\nPath('local-sentinel.txt').write_text('mutated\\n')\n",
                encoding="utf-8",
            )
            command_sha = self._write_stub_command(command_root)
            wrong_sha = self._write_stub_command(wrong_remote)
            old_unmerged_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=unmerged, text=True).strip()
            (unmerged / "later.txt").write_text("not in origin dev\n", encoding="utf-8")
            subprocess.run(["git", "add", "later.txt"], cwd=unmerged, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "unmerged"], cwd=unmerged, check=True)
            unmerged_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=unmerged, text=True).strip()
            subprocess.run(["git", "update-ref", "refs/remotes/origin/dev", old_unmerged_sha], cwd=unmerged, check=True)

            base_env = self._base_env(
                status_root=status_root,
                worktree=worktree,
                command_root=command_root,
                command_sha=command_sha,
            )
            cases = [
                ({"PANTHEON_COMMAND_ROOT": ""}, "PANTHEON_COMMAND_ROOT is required"),
                ({"PANTHEON_COMMAND_ROOT": "relative"}, "must be an absolute path"),
                ({"PANTHEON_COMMAND_ROOT": str(command_link)}, "symlink component"),
                (
                    {
                        "PANTHEON_COMMAND_ROOT": str(nested_subdir),
                        "PANTHEON_COMMAND_RUNTIME_SHA": nested_sha,
                    },
                    "must be a git repository root",
                ),
                ({"PANTHEON_COMMAND_RUNTIME_SHA": "0" * 40}, "PANTHEON_COMMAND_RUNTIME_SHA mismatch"),
                (
                    {
                        "PANTHEON_COMMAND_ROOT": str(wrong_remote),
                        "PANTHEON_COMMAND_RUNTIME_SHA": wrong_sha,
                    },
                    "remote mismatch",
                ),
                (
                    {
                        "PANTHEON_COMMAND_ROOT": str(unmerged),
                        "PANTHEON_COMMAND_RUNTIME_SHA": unmerged_sha,
                    },
                    "is not merged into origin/dev",
                ),
            ]
            for env_update, expected in cases:
                with self.subTest(expected=expected):
                    env = dict(base_env)
                    env.update(env_update)
                    proc = subprocess.run(
                        ["bash", str(worktree / "scripts" / "ai-status.sh"), "show", "TASK-1"],
                        cwd=worktree,
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn(expected, proc.stderr + proc.stdout)
                    self.assertEqual(local_sentinel.read_text(encoding="utf-8"), "unchanged\n")

    def test_direct_python_from_stale_worktree_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="status-command-direct-") as tmpdir:
            root = Path(tmpdir)
            status_root = root / "status-root"
            command_root = root / "command-root"
            worktree = root / "task-worktree"
            self._init_repo(status_root)
            self._init_repo(command_root)
            self._init_repo(worktree)
            command_sha = self._write_stub_command(command_root)
            self._copy_full_status_tooling(worktree)
            env = self._base_env(
                status_root=status_root,
                worktree=worktree,
                command_root=command_root,
                command_sha=command_sha,
            )

            proc = subprocess.run(
                [sys.executable, str(worktree / "scripts" / "ai_status.py"), "show", "TASK-1"],
                cwd=worktree,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("must execute the installed command runtime", proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main()
