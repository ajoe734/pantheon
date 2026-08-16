from __future__ import annotations

import importlib.util
import shutil
import subprocess
from datetime import datetime as real_datetime, timezone
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("auto_commit_archive.py")
SPEC = importlib.util.spec_from_file_location("auto_commit_archive_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
auto_commit_archive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto_commit_archive)


def test_detect_pending_collects_terminal_archives_and_task_briefs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(auto_commit_archive, "ROOT", tmp_path)
    status_output = "\n".join(
        [
            "?? ai-task-archive/tasks/TERMINAL-001.json",
            "?? ai-task-archive/tasks/unrelated/nested.json",
            "?? .orchestrator/task-briefs/TASK-001.md",
            " M ai-task-archive/tasks/TRACKED-001.json",
            "",
        ]
    )

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if "status" in cmd:
            assert "--untracked-files=all" in cmd
            return subprocess.CompletedProcess(cmd, 0, status_output, "")
        if "diff" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        raise AssertionError(cmd)

    monkeypatch.setattr(auto_commit_archive, "_run", fake_run)

    pending = auto_commit_archive.detect_pending()

    assert pending == {
        "briefs": [".orchestrator/task-briefs/TASK-001.md"],
        "archives": ["ai-task-archive/tasks/TERMINAL-001.json"],
        "index_modified": True,
    }


def test_commit_message_counts_terminal_records_and_briefs() -> None:
    message = auto_commit_archive._build_commit_message(
        "OPS-ARCHIVE-AUTO-COMMIT-TEST",
        {
            "briefs": [".orchestrator/task-briefs/TASK-001.md"],
            "archives": ["ai-task-archive/tasks/TERMINAL-001.json"],
            "index_modified": False,
        },
    )

    assert "backfill 2 files" in message
    assert "1 ai-task-archive/tasks/*.json terminal task records" in message


def test_backfill_refuses_directory_scope_before_copy2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    bad_scope = root / "ai-task-archive" / "tasks" / "BAD.json"
    bad_scope.mkdir(parents=True)
    monkeypatch.setattr(auto_commit_archive, "ROOT", root)
    monkeypatch.setattr(auto_commit_archive, "open_pr_exists", lambda: False)

    class FixedDateTime:
        @classmethod
        def now(cls, tz: timezone | None = None) -> real_datetime:
            return real_datetime(2099, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    monkeypatch.setattr(auto_commit_archive, "datetime", FixedDateTime)

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if "worktree" in cmd and "add" in cmd:
            Path(cmd[-2]).mkdir(parents=True, exist_ok=False)
        elif "worktree" in cmd and "remove" in cmd:
            shutil.rmtree(Path(cmd[-1]), ignore_errors=True)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(auto_commit_archive, "_run", fake_run)

    with pytest.raises(FileNotFoundError, match="not a regular file"):
        auto_commit_archive.run_backfill_pr(
            {
                "briefs": [],
                "archives": ["ai-task-archive/tasks/BAD.json"],
                "index_modified": False,
            }
        )
