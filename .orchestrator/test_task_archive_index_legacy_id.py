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
    import subprocess
    # Initialize a temporary git repo to avoid test pollution
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)

    archive_dir = tmp_path / "ai-task-archive"
    tasks_dir = archive_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    index_file = archive_dir / "index.json"

    monkeypatch.setattr(task_archive, "STATUS_ROOT", tmp_path)
    monkeypatch.setattr(task_archive, "ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(task_archive, "ARCHIVE_TASKS_DIR", tasks_dir)
    monkeypatch.setattr(task_archive, "ARCHIVE_INDEX_FILE", index_file)
    monkeypatch.setattr(task_archive, "STATUS_FILE", tmp_path / "ai-status.json")

    # Modern schema (committed)
    _write(
        tasks_dir,
        "MODERN-001.json",
        {"task_id": "MODERN-001", "terminal_outcome": "completed", "archived_at": "2026-06-14T00:00:00Z"},
    )
    # Legacy schema: id only at the top level (committed)
    _write(
        tasks_dir,
        "LEGACY-001.json",
        {"id": "LEGACY-001", "status": "done", "terminal_outcome": "completed", "archived_at": "2026-06-14T01:00:00Z"},
    )

    # Commit them to HEAD
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    # Uncommitted newly created snapshot (on disk only)
    _write(
        tasks_dir,
        "NEW-001.json",
        {
            "version": 1,
            "task_id": "NEW-001",
            "archived_at": "2026-06-14T02:00:00Z",
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": {
                "id": "NEW-001",
                "status": "done",
                "terminal_outcome": "completed",
            },
            "handoffs": [],
            "blockers": [],
        },
    )

    index = task_archive.rebuild_archive_index(recent_limit=10)

    assert index["counts"]["total"] == 3, index["counts"]
    assert "LEGACY-001" in index["recent_terminal_ids"], "legacy top-level id was dropped from the index"
    assert "MODERN-001" in index["recent_terminal_ids"]
    assert "NEW-001" in index["recent_terminal_ids"], "new uncommitted snapshot was dropped"

    summaries = task_archive.recent_terminal_summaries(10)
    assert {item["task_id"] for item in summaries} == {
        "LEGACY-001",
        "MODERN-001",
        "NEW-001",
    }
    resolver = task_archive.TaskResolver([], archive_tasks_dir=tasks_dir)
    assert resolver.get("LEGACY-001")["id"] == "LEGACY-001"
    assert resolver.dependency_satisfied("LEGACY-001") is True


def test_rebuild_index_fail_closed_on_downgrade(tmp_path, monkeypatch) -> None:
    import pytest
    import subprocess
    # Initialize a temporary git repo to avoid test pollution
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)

    archive_dir = tmp_path / "ai-task-archive"
    tasks_dir = archive_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    index_file = archive_dir / "index.json"

    monkeypatch.setattr(task_archive, "STATUS_ROOT", tmp_path)
    monkeypatch.setattr(task_archive, "ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(task_archive, "ARCHIVE_TASKS_DIR", tasks_dir)
    monkeypatch.setattr(task_archive, "ARCHIVE_INDEX_FILE", index_file)
    monkeypatch.setattr(task_archive, "STATUS_FILE", tmp_path / "ai-status.json")

    # Create a pre-existing index.json claiming total count is 5
    existing_index = {
        "version": 1,
        "updated_at": "2026-07-17T03:25:21Z",
        "counts": {
            "total": 5,
            "completed": 5,
            "superseded": 0
        },
        "recent_terminal_ids": []
    }
    index_file.write_text(json.dumps(existing_index), encoding="utf-8")

    # Create only 3 snapshots on disk
    _write(
        tasks_dir,
        "TASK-001.json",
        {"task_id": "TASK-001", "terminal_outcome": "completed", "archived_at": "2026-06-14T00:00:00Z"},
    )
    _write(
        tasks_dir,
        "TASK-002.json",
        {"task_id": "TASK-002", "terminal_outcome": "completed", "archived_at": "2026-06-14T01:00:00Z"},
    )

    # Commit them to HEAD
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    # Try rebuilding the index, which should fail closed because 2 found < 5 claimed
    with pytest.raises(RuntimeError, match="Failing closed to prevent index downgrade"):
        task_archive.rebuild_archive_index(recent_limit=10)


def test_rebuild_indexes_requires_exact_outbox_provenance_for_invalid_contracts(tmp_path, monkeypatch) -> None:
    import pytest
    import subprocess
    # Set up git repo
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)

    archive_dir = tmp_path / "ai-task-archive"
    tasks_dir = archive_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    index_file = archive_dir / "index.json"
    status_file = tmp_path / "ai-status.json"

    monkeypatch.setattr(task_archive, "STATUS_ROOT", tmp_path)
    monkeypatch.setattr(task_archive, "ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(task_archive, "ARCHIVE_TASKS_DIR", tasks_dir)
    monkeypatch.setattr(task_archive, "ARCHIVE_INDEX_FILE", index_file)
    monkeypatch.setattr(task_archive, "STATUS_FILE", status_file)

    # We write a spoofed untracked snapshot (does not satisfy modern contract)
    _write(
        tasks_dir,
        "SPOOF-001.json",
        {"task_id": "SPOOF-001"}
    )

    # Commit a baseline to git
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=tmp_path, check=True)

    # 1. Rebuilding with no outbox should raise RuntimeError
    with pytest.raises(RuntimeError, match="lacks proven durable outbox provenance"):
        task_archive.rebuild_archive_index(recent_limit=10)

    # 2. Rebuilding with obsolete key `archive_outbox` should still fail
    status_payload_obsolete = {
        "tasks": [],
        "archive_outbox": {
            "schema_version": 1,
            "snapshots": [
                {"task_id": "SPOOF-001"}
            ]
        }
    }
    status_file.write_text(json.dumps(status_payload_obsolete), encoding="utf-8")
    with pytest.raises(RuntimeError, match="lacks proven durable outbox provenance"):
        task_archive.rebuild_archive_index(recent_limit=10)

    # 3. Rebuilding with invalid status_archive_outbox (missing/bad fields) should fail closed
    status_payload_invalid = {
        "tasks": [],
        "status_archive_outbox": {
            "schema_version": 1,
            "transaction_id": "bad-id",
            "snapshots": [
                {"task_id": "SPOOF-001"}
            ]
        }
    }
    status_file.write_text(json.dumps(status_payload_invalid), encoding="utf-8")
    with pytest.raises(RuntimeError, match="status archive outbox digest mismatch|status archive outbox contract is invalid|snapshot is missing archived_at"):
        task_archive.rebuild_archive_index(recent_limit=10)

    # 4. Rebuilding with valid outbox structure but contents mismatching the file on disk should fail
    from task_archive import _canonical_json_sha256
    valid_outbox_snapshot = {
        "version": 1,
        "task_id": "SPOOF-001",
        "archived_at": "2026-07-16T15:37:21Z",
        "terminal_status": "done",
        "terminal_outcome": "completed",
        "task": {
            "id": "SPOOF-001",
            "status": "done",
            "terminal_outcome": "completed",
        },
        "handoffs": [],
        "blockers": [],
    }
    digest = _canonical_json_sha256([valid_outbox_snapshot])
    status_payload_valid = {
        "tasks": [],
        "status_archive_outbox": {
            "schema_version": 1,
            "transaction_id": "ai-status-archive-tx-" + digest,
            "snapshots": [valid_outbox_snapshot]
        }
    }
    status_file.write_text(json.dumps(status_payload_valid), encoding="utf-8")
    # File on disk has {"task_id": "SPOOF-001"} which doesn't match the valid_outbox_snapshot
    with pytest.raises(RuntimeError, match="content does not match the outbox snapshot exactly"):
        task_archive.rebuild_archive_index(recent_limit=10)

    # 5. Rebuilding with matching outbox snapshot and disk file should pass!
    _write(tasks_dir, "SPOOF-001.json", valid_outbox_snapshot)
    index = task_archive.rebuild_archive_index(recent_limit=10)
    assert index["counts"]["total"] == 1
    assert "SPOOF-001" in index["recent_terminal_ids"]


def test_load_archived_snapshot_rejects_symlink(tmp_path, monkeypatch) -> None:
    import pytest
    archive_dir = tmp_path / "ai-task-archive"
    tasks_dir = archive_dir / "tasks"
    tasks_dir.mkdir(parents=True)

    monkeypatch.setattr(task_archive, "STATUS_ROOT", tmp_path)
    monkeypatch.setattr(task_archive, "ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(task_archive, "ARCHIVE_TASKS_DIR", tasks_dir)
    monkeypatch.setattr(task_archive, "STATUS_FILE", tmp_path / "ai-status.json")

    # Create a symlink
    path = tasks_dir / "SPOOF-001.json"
    path.symlink_to(tmp_path / "nonexistent")

    with pytest.raises(RuntimeError, match="archive-leaf cannot be a symlink"):
        task_archive.load_archived_snapshot("SPOOF-001")

    resolver = task_archive.TaskResolver([], archive_tasks_dir=tasks_dir)
    with pytest.raises(RuntimeError, match="archive-leaf cannot be a symlink"):
        resolver.snapshot("SPOOF-001")
