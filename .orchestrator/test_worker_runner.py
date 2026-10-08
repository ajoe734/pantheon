"""Tests for the leased git ref writability mounts in ``worker_runner.py``."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

_P = os.path.join(os.path.dirname(__file__), "worker_runner.py")
_spec = importlib.util.spec_from_file_location("worker_runner", _P)
wr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wr)


class LeasedGitRefWritabilityTests(unittest.TestCase):
    """Only the leased task's own branch and its -v<N> replacements are writable."""

    def _git(self, *args: str, cwd: Path) -> None:
        import subprocess

        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.invalid",
            },
        )

    def _read_only_refs(self, attached: str) -> set[str]:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            self._git("init", "-q", "-b", "dev", cwd=repo)
            self._git("commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
            for branch in (
                "task/ABC-001",
                "task/ABC-001-v2",
                "task/ABC-001-v3",
                "task/ABC-001-vnext",
                "task/ABC-0011",
                "task/OTHER-001",
            ):
                self._git("branch", branch, cwd=repo)
            workspace = Path(tmp) / "leased"
            self._git("worktree", "add", "-q", str(workspace), attached, cwd=repo)
            bwrap_cmd: list[str] = []
            wr._append_leased_git_metadata_mounts(bwrap_cmd, workspace)
            heads = (repo / ".git" / "refs" / "heads").resolve()
            return {
                Path(bwrap_cmd[index + 1]).relative_to(heads).as_posix()
                for index, arg in enumerate(bwrap_cmd)
                if arg == "--ro-bind"
                and Path(bwrap_cmd[index + 1]).is_relative_to(heads)
            }

    def test_versioned_replacement_branches_of_the_leased_task_are_writable(self) -> None:
        for attached in ("task/ABC-001", "task/ABC-001-v2"):
            with self.subTest(attached=attached):
                self.assertEqual(
                    self._read_only_refs(attached),
                    {"dev", "task/ABC-001-vnext", "task/ABC-0011", "task/OTHER-001"},
                )


if __name__ == "__main__":
    unittest.main()
