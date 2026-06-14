"""Regression: rebuild_archive_index must index archive snapshots that store the
id under any known schema variant — including legacy entries that only carry a
top-level ``id`` (not ``task_id`` / nested ``task.id``).

Verification campaign 2026-06-14, round 8, finding F8: such legacy files
resolved to a None id and were silently dropped from the archive index forever
(observed: ai-task-archive/tasks/OSS-STAT-001-SIDECAR-ACCEPTANCE.json).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import task_archive  # noqa: E402


def _write(dir_: Path, name: str, payload: dict) -> None:
    (dir_ / name).write_text(json.dumps(payload), encoding="utf-8")


def test_rebuild_indexes_legacy_top_level_id(tmp_path, monkeypatch) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    index_file = tmp_path / "index.json"
    monkeypatch.setattr(task_archive, "ARCHIVE_TASKS_DIR", tasks_dir)
    monkeypatch.setattr(task_archive, "ARCHIVE_INDEX_FILE", index_file)

    # Modern schema
    _write(
        tasks_dir,
        "MODERN-001.json",
        {"task_id": "MODERN-001", "terminal_outcome": "completed", "archived_at": "2026-06-14T00:00:00Z"},
    )
    # Legacy schema: id only at the top level
    _write(
        tasks_dir,
        "LEGACY-001.json",
        {"id": "LEGACY-001", "status": "done", "terminal_outcome": "completed", "archived_at": "2026-06-14T01:00:00Z"},
    )

    index = task_archive.rebuild_archive_index(recent_limit=10)

    assert index["counts"]["total"] == 2, index["counts"]
    assert "LEGACY-001" in index["recent_terminal_ids"], "legacy top-level id was dropped from the index"
    assert "MODERN-001" in index["recent_terminal_ids"]
