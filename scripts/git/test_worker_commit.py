#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
SCRIPTS_GIT_DIR = ROOT / "scripts" / "git"
for path in (str(ORCHESTRATOR_DIR), str(SCRIPTS_GIT_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import worker_commit


class WorkerCommitPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.repo = Path(self.tmpdir.name).resolve()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test Worker"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "worker@test.local"], cwd=self.repo, check=True)
        (self.repo / "file1.py").write_text("print('hello')\n", encoding="utf-8")
        subprocess.run(["git", "add", "file1.py"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=self.repo, check=True)

    def test_worker_commit_rejects_overlong_subject(self) -> None:
        (self.repo / "file1.py").write_text("print('updated')\n", encoding="utf-8")
        long_subject = "SUP-WORKER-SUBJECT-GUARD-20260811-EXTREMELY-LONG-TASK-ID: anchor services/control-plane/bff/adapters/management.py"
        self.assertGreater(len(long_subject), 72)

        msg_file = self.repo / "msg.txt"
        msg_file.write_text(
            f"{long_subject}\n\n"
            "LLM-Agent: Antigravity2\n"
            "Task-ID: SUP-WORKER-SUBJECT-GUARD-20260811-EXTREMELY-LONG-TASK-ID\n"
            "Reviewer: Codex2\n",
            encoding="utf-8",
        )

        argv = [
            "worker_commit.py",
            "--task-id",
            "SUP-WORKER-SUBJECT-GUARD-20260811-EXTREMELY-LONG-TASK-ID",
            "--message-file",
            str(msg_file),
            "--scope",
            str(self.repo / "file1.py"),
        ]

        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(worker_commit, "ROOT", self.repo),
            mock.patch.object(worker_commit, "STATUS_ROOT", self.repo),
        ):
            res = worker_commit.main()

        self.assertEqual(res, 5)
        # Verify no commit was made
        log = subprocess.run(
            ["git", "log", "-1", "--format=%s"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual(log, "initial")

    def test_worker_commit_accepts_valid_short_subject(self) -> None:
        (self.repo / "file1.py").write_text("print('updated')\n", encoding="utf-8")
        valid_subject = "SUP-WORKER-SUBJECT-GUARD-20260811: anchor file1.py"
        self.assertLessEqual(len(valid_subject), 72)

        msg_file = self.repo / "msg.txt"
        msg_file.write_text(
            f"{valid_subject}\n\n"
            "LLM-Agent: Antigravity2\n"
            "Task-ID: SUP-WORKER-SUBJECT-GUARD-20260811\n"
            "Reviewer: Codex2\n",
            encoding="utf-8",
        )

        argv = [
            "worker_commit.py",
            "--task-id",
            "SUP-WORKER-SUBJECT-GUARD-20260811",
            "--message-file",
            str(msg_file),
            "--scope",
            str(self.repo / "file1.py"),
        ]

        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(worker_commit, "ROOT", self.repo),
            mock.patch.object(worker_commit, "STATUS_ROOT", self.repo),
            mock.patch.object(worker_commit, "_append_audit"),
        ):
            res = worker_commit.main()

        self.assertEqual(res, 0)
        log = subprocess.run(
            ["git", "log", "-1", "--format=%s"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual(log, valid_subject)

    def test_worker_commit_rejects_missing_or_empty_message(self) -> None:
        msg_file = self.repo / "empty_msg.txt"
        msg_file.write_text("# Only comment lines\n", encoding="utf-8")

        argv = [
            "worker_commit.py",
            "--task-id",
            "SUP-WORKER-SUBJECT-GUARD-20260811",
            "--message-file",
            str(msg_file),
            "--scope",
            str(self.repo / "file1.py"),
        ]

        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(worker_commit, "ROOT", self.repo),
            mock.patch.object(worker_commit, "STATUS_ROOT", self.repo),
        ):
            res = worker_commit.main()

        self.assertEqual(res, 5)


if __name__ == "__main__":
    unittest.main()
