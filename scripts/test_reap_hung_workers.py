#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reap_hung_workers


class WorktreeProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self._tmp.name) / "wt"
        (self.worktree / ".git").mkdir(parents=True)
        (self.worktree / "src").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_recent_file_write_counts_as_progress(self) -> None:
        (self.worktree / "src" / "a.py").write_text("x")
        self.assertTrue(reap_hung_workers.worktree_written_since(self.worktree, 3600))

    def test_untouched_worktree_is_not_progress(self) -> None:
        path = self.worktree / "src" / "a.py"
        path.write_text("x")
        old = 1_000_000_000  # far in the past
        import os

        os.utime(path, (old, old))
        os.utime(self.worktree / "src", (old, old))
        os.utime(self.worktree, (old, old))
        self.assertFalse(reap_hung_workers.worktree_written_since(self.worktree, 3600))

    def test_git_internal_churn_is_not_progress(self) -> None:
        # git touches its own metadata; that must not read as the worker working.
        (self.worktree / ".git" / "index").write_text("x")
        self.assertFalse(reap_hung_workers.worktree_written_since(self.worktree, 3600))

    def test_broken_probe_assumes_progress_and_never_kills(self) -> None:
        with mock.patch(
            "reap_hung_workers.subprocess.run", side_effect=subprocess.TimeoutExpired("find", 60)
        ):
            self.assertTrue(reap_hung_workers.worktree_written_since(self.worktree, 3600))


class InspectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self._tmp.name) / "wt"
        self.worktree.mkdir(parents=True)
        self.cmd = (
            "/usr/bin/python3 /root/.orchestrator/worker_runner.py "
            "--run-id codex-20260715T183548Z-c77cbcab --heartbeat-interval-seconds 15 "
            f"-- .orchestrator/bin/codex exec -C {self.worktree} -s workspace-write"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _inspect(self, *, age: float, wrote: bool, cmd: str | None = None):
        with mock.patch("reap_hung_workers._cmdline", return_value=cmd or self.cmd), \
             mock.patch("reap_hung_workers._pid_age_seconds", return_value=age), \
             mock.patch("reap_hung_workers.worktree_written_since", return_value=wrote):
            return reap_hung_workers.inspect(999, min_age=5400, stall=3600)

    def test_old_and_silent_run_is_hung(self) -> None:
        record = self._inspect(age=6 * 3600, wrote=False)
        self.assertIsNotNone(record)
        self.assertEqual(record["run_id"], "codex-20260715T183548Z-c77cbcab")
        self.assertEqual(record["worktree"], str(self.worktree))

    def test_young_run_is_never_touched_even_when_silent(self) -> None:
        self.assertIsNone(self._inspect(age=600, wrote=False))

    def test_old_but_writing_run_is_healthy(self) -> None:
        self.assertIsNone(self._inspect(age=6 * 3600, wrote=True))

    def test_non_worker_process_is_ignored(self) -> None:
        self.assertIsNone(self._inspect(age=6 * 3600, wrote=False, cmd="/usr/bin/python3 supervisor.py"))

    def test_vanished_worktree_is_left_to_the_task_reaper(self) -> None:
        cmd = self.cmd.replace(str(self.worktree), "/tmp/gone-worktree-xyz")
        self.assertIsNone(self._inspect(age=6 * 3600, wrote=False, cmd=cmd))


class KillTests(unittest.TestCase):
    def test_dry_run_does_not_signal(self) -> None:
        with mock.patch("reap_hung_workers.os.kill") as killer:
            self.assertEqual(reap_hung_workers.kill_run(999, dry_run=True), "dry-run")
            killer.assert_not_called()

    def test_term_is_enough_when_process_exits(self) -> None:
        calls = []

        def fake_kill(pid, sig):
            calls.append(sig)
            if len(calls) > 1:
                raise ProcessLookupError

        with mock.patch("reap_hung_workers.os.kill", side_effect=fake_kill), \
             mock.patch("reap_hung_workers.time.sleep"):
            self.assertEqual(reap_hung_workers.kill_run(999, dry_run=False), "terminated")

    def test_already_gone_is_not_an_error(self) -> None:
        with mock.patch("reap_hung_workers.os.kill", side_effect=ProcessLookupError):
            self.assertEqual(reap_hung_workers.kill_run(999, dry_run=False), "already-gone")


if __name__ == "__main__":
    unittest.main()
