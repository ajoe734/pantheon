#!/usr/bin/env python3
"""Tests for the shared-index sweep-in defenses.

Covers:
  - check_commit_scope.py (pre-commit guard)
  - worker_commit.py (reset-then-stage-then-commit wrapper)

Both exercise an isolated temporary git repo so we do not touch the real
worktree.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(modname: str, path: Path):
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


check_scope = _load("check_commit_scope", HERE / "check_commit_scope.py")


def _git(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


def _init_repo(root: Path) -> None:
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@pantheon.local")
    _git(root, "config", "user.name", "Pantheon Test")
    (root / "seed.txt").write_text("seed\n")
    _git(root, "add", "seed.txt")
    _git(root, "commit", "-m", "Initial commit")


class CheckCommitScopeTests(unittest.TestCase):
    def _run_guard(self, root: Path, staged: list[str], commit_msg: str) -> tuple[int, str]:
        # Plant the commit message so the guard can read trailers via either
        # path (env override or .git/COMMIT_EDITMSG).
        git_dir = root / ".git"
        (git_dir / "COMMIT_EDITMSG").write_text(commit_msg)
        msg_file = root / "msg-explicit.txt"
        msg_file.write_text(commit_msg)
        proc = subprocess.run(
            [sys.executable, str(HERE / "check_commit_scope.py"), *staged],
            cwd=root,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PANTHEON_SCOPE_CHECK_DISABLED": "0",
                "PANTHEON_COMMIT_MSG_FILE": str(msg_file),
            },
        )
        return proc.returncode, proc.stderr

    def test_passes_when_within_brief_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            brief_dir = root / ".orchestrator" / "task-briefs"
            brief_dir.mkdir(parents=True)
            (brief_dir / "FOO-001.md").write_text(
                "# Brief FOO-001\n\nscope:\n  - services/foo/\n  - tests/foo/\n"
            )
            rc, err = self._run_guard(
                root,
                ["services/foo/adapter.py", "tests/foo/test_adapter.py"],
                "FOO-001: do thing\n\nTask-ID: FOO-001\n",
            )
            self.assertEqual(rc, 0, msg=err)

    def test_rejects_leak_outside_brief_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            brief_dir = root / ".orchestrator" / "task-briefs"
            brief_dir.mkdir(parents=True)
            (brief_dir / "FOO-001.md").write_text(
                "# Brief FOO-001\n\nscope:\n  - services/foo/\n"
            )
            rc, err = self._run_guard(
                root,
                ["services/foo/adapter.py", "docs/unrelated.md"],
                "FOO-001: do thing\n\nTask-ID: FOO-001\n",
            )
            self.assertNotEqual(rc, 0)
            self.assertIn("leak outside", err)
            self.assertIn("docs/unrelated.md", err)

    def test_passes_when_no_brief_and_few_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            rc, err = self._run_guard(
                root,
                ["services/foo/a.py", "services/foo/b.py"],
                "FOO-001: do thing\n\nTask-ID: FOO-001\n",
            )
            self.assertEqual(rc, 0, msg=err)

    def test_rejects_cross_dir_spread_without_trailer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            rc, err = self._run_guard(
                root,
                [
                    "services/foo/a.py",
                    ".github/workflows/x.yml",
                    "docs/things.md",
                    ".orchestrator/config.json",
                    "tests/foo.py",
                ],
                "FOO-001: do thing\n\nTask-ID: FOO-001\n",
            )
            self.assertNotEqual(rc, 0)
            self.assertIn("top-level directories", err)

    def test_cross_dir_yes_trailer_allows_spread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            rc, err = self._run_guard(
                root,
                [
                    "services/foo/a.py",
                    ".github/workflows/x.yml",
                    "docs/things.md",
                    ".orchestrator/config.json",
                    "tests/foo.py",
                ],
                "FOO-001: do thing\n\nTask-ID: FOO-001\nCross-Dir: yes\n",
            )
            self.assertEqual(rc, 0, msg=err)

    def test_exempt_subject_skips_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            rc, err = self._run_guard(
                root,
                ["services/foo/a.py", ".github/x.yml", "docs/x.md", ".orchestrator/x.json", "tests/foo.py"],
                "wave-merge: claude FOO-001\n",
            )
            self.assertEqual(rc, 0, msg=err)

    def test_disabled_env_var_short_circuits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            proc = subprocess.run(
                [sys.executable, str(HERE / "check_commit_scope.py"),
                 "services/a.py", "docs/b.md", ".github/x.yml", ".orchestrator/c.json", "tests/d.py"],
                cwd=root,
                capture_output=True,
                text=True,
                env={**os.environ, "PANTHEON_SCOPE_CHECK_DISABLED": "1"},
            )
            self.assertEqual(proc.returncode, 0)


class WorkerCommitWrapperTests(unittest.TestCase):
    def _run_wrapper(self, root: Path, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, **(env_extra or {})}
        # Disable both pre-commit guards for clean test isolation.
        env.setdefault("PANTHEON_GENERATED_FILES_CHECK_DISABLED", "1")
        env.setdefault("PANTHEON_SCOPE_CHECK_DISABLED", "1")
        return subprocess.run(
            [sys.executable, str(HERE / "worker_commit.py"), *args],
            cwd=root,
            capture_output=True,
            text=True,
            env=env,
        )

    def _setup_repo(self) -> Path:
        tmp = tempfile.mkdtemp()
        root = Path(tmp)
        _init_repo(root)
        # Override worker_commit's ROOT detection by symlinking under the
        # expected layout: scripts/git/worker_commit.py needs ROOT = root.
        # Easiest: copy the script in so its `parents[2]` resolves to root.
        scripts_dir = root / "scripts" / "git"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "worker_commit.py").write_text(
            (HERE / "worker_commit.py").read_text()
        )
        return root

    def _wrapper(self, root: Path, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, **(env_extra or {})}
        env.setdefault("PANTHEON_GENERATED_FILES_CHECK_DISABLED", "1")
        env.setdefault("PANTHEON_SCOPE_CHECK_DISABLED", "1")
        return subprocess.run(
            [sys.executable, str(root / "scripts" / "git" / "worker_commit.py"), *args],
            cwd=root,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_clears_stale_staging_then_commits_only_scope(self) -> None:
        root = self._setup_repo()
        try:
            (root / "kept.py").write_text("a\n")
            (root / "leaked.py").write_text("b\n")
            # Simulate stale staging from a previous worker.
            _git(root, "add", "leaked.py")
            self.assertIn("leaked.py", _git(root, "diff", "--cached", "--name-only").stdout)

            msg = root / "msg.txt"
            msg.write_text("FOO-001: do thing\n\nLLM-Agent: Test\nTask-ID: FOO-001\nReviewer: Other\nWave: 2026-W21\n")
            proc = self._wrapper(
                root,
                "--task-id", "FOO-001",
                "--message-file", str(msg),
                "--scope", "kept.py",
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
            # The leaked stale staging must have been cleared.
            last_files = _git(root, "show", "--name-only", "--format=", "HEAD").stdout.split()
            self.assertIn("kept.py", last_files)
            self.assertNotIn("leaked.py", last_files)
            audit_lines = (root / "ai-activity-log.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertTrue(audit_lines)
            self.assertIn('"message": "Worker commit ', audit_lines[-1])
        finally:
            import shutil; shutil.rmtree(root)

    def test_rejects_scope_when_nothing_to_stage(self) -> None:
        root = self._setup_repo()
        try:
            msg = root / "msg.txt"
            msg.write_text("FOO-001: do thing\n\nTask-ID: FOO-001\n")
            proc = self._wrapper(
                root,
                "--task-id", "FOO-001",
                "--message-file", str(msg),
                "--scope", "nonexistent.py",
            )
            self.assertNotEqual(proc.returncode, 0)
        finally:
            import shutil; shutil.rmtree(root)

    def test_dry_run_does_not_commit(self) -> None:
        root = self._setup_repo()
        try:
            (root / "kept.py").write_text("a\n")
            msg = root / "msg.txt"
            msg.write_text("FOO-001: do thing\n\nTask-ID: FOO-001\n")
            before = _git(root, "rev-parse", "HEAD").stdout.strip()
            proc = self._wrapper(
                root,
                "--task-id", "FOO-001",
                "--message-file", str(msg),
                "--scope", "kept.py",
                "--dry-run",
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            after = _git(root, "rev-parse", "HEAD").stdout.strip()
            self.assertEqual(before, after)
        finally:
            import shutil; shutil.rmtree(root)

    def test_private_index_isolates_concurrent_staging(self) -> None:
        """A separate GIT_INDEX_FILE isolates worker B from worker A's staging."""
        root = self._setup_repo()
        try:
            # Worker A leaves stale staging in the main index.
            (root / "from_a.py").write_text("a\n")
            _git(root, "add", "from_a.py")
            # Worker B uses --index-file and only stages its own file.
            (root / "from_b.py").write_text("b\n")
            msg = root / "msg.txt"
            msg.write_text("BAR-002: worker B\n\nLLM-Agent: B\nTask-ID: BAR-002\nReviewer: A\nWave: 2026-W21\n")
            proc = self._wrapper(
                root,
                "--task-id", "BAR-002",
                "--message-file", str(msg),
                "--scope", "from_b.py",
                "--index-file", str(root / ".git" / "index-bar2"),
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
            last_files = _git(root, "show", "--name-only", "--format=", "HEAD").stdout.split()
            self.assertIn("from_b.py", last_files)
            self.assertNotIn("from_a.py", last_files)
            # And worker A's main-index staging is preserved (private index
            # didn't touch the shared one).
            self.assertIn("from_a.py", _git(root, "diff", "--cached", "--name-only").stdout)
        finally:
            import shutil; shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
