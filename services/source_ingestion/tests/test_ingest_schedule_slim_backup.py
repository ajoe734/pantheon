"""SOURCE-RECEIPT-SLIM-KEEPS-BACKUP-20261117: the in-service journal rewrite keeps the old file 7 days."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.source_ingestion import scheduler as sched
from services.source_ingestion.scheduler import JsonlIngestScheduleStore
from services.source_ingestion.tests.test_ingest_receipt_snapshot_refs_bounded import (
    _legacy_receipt,
)

# dev head this task started from: it already rewrites in place (the candidate hosted ran 2026-11-17).
REWRITE_WITHOUT_BACKUP_SHA = "3f65a8b3e"


def _previous_scheduler_module():
    """The hosted reader at PR time, loaded from git."""
    src = subprocess.run(["git", "show", f"{REWRITE_WITHOUT_BACKUP_SHA}:services/source_ingestion/scheduler.py"],
                         capture_output=True, text=True, cwd=Path(__file__).resolve().parents[3])
    if src.returncode != 0:
        pytest.fail(f"reader {REWRITE_WITHOUT_BACKUP_SHA} is not available from git: {src.stderr.strip()}")
    name = "services.source_ingestion._pre_backup_scheduler"
    module = types.ModuleType(name)
    module.__package__ = "services.source_ingestion"
    sys.modules[name] = module
    exec(compile(src.stdout, "<pre-backup scheduler>", "exec"), module.__dict__)
    return module

NOW = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)
BACKUP = ".pre-migrate.bak"


def _hosted_shape(path: Path, fresh: int = 8, old: int = 4) -> int:
    """Receipts that embed snapshot bodies, `old` of them past the 30 day window (hosted row mix)."""
    with path.open("wb") as handle:
        for index in range(fresh + old):
            payload = _legacy_receipt(index).to_dict()
            if index < old:
                payload["created_at"] = (NOW - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            else:
                payload["created_at"] = (NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            handle.write((json.dumps({"schema_version": "ingest_schedule_store.v1", "record_type": "ingest_receipt",
                                      "record_id": payload["ingest_run_id"], "payload": payload},
                                     sort_keys=True, separators=(",", ":")) + "\n").encode())
    return path.stat().st_size


def _open(path: Path, now: datetime = NOW) -> JsonlIngestScheduleStore:
    # Drive the first-load rewrite with a pinned clock.
    real = JsonlIngestScheduleStore._slim_journal_unlocked

    def pinned(self, *, now: datetime | None = None):
        return real(self, now=now or NOW_HOLDER["now"])

    NOW_HOLDER["now"] = now
    JsonlIngestScheduleStore._slim_journal_unlocked = pinned
    try:
        return JsonlIngestScheduleStore(path)
    finally:
        JsonlIngestScheduleStore._slim_journal_unlocked = real


NOW_HOLDER: dict = {"now": NOW}


def _files(path: Path) -> set[str]:
    return {p.name for p in path.parent.iterdir() if p.name != path.name + ".lock"}


def test_reproduce_hosted_deployed_rewrite_left_no_copy(tmp_path):
    """Before this task: first load rewrote in place and nothing of the previous file remained."""
    previous = _previous_scheduler_module()
    path = tmp_path / "ingest_schedule.jsonl"
    before = _hosted_shape(path)
    previous.JsonlIngestScheduleStore(path)
    assert path.stat().st_size < before // 5
    assert _files(path) == {path.name}  # no .pre-migrate.bak: the rewrite could not be undone


def test_first_load_keeps_previous_file_by_hard_link_and_states_the_plan(tmp_path, caplog):
    path = tmp_path / "ingest_schedule.jsonl"
    before = _hosted_shape(path)
    original_bytes = path.read_bytes()
    with caplog.at_level(logging.INFO, logger=sched.logger.name):
        store = _open(path)
    backup = path.with_name(path.name + BACKUP)
    assert backup.read_bytes() == original_bytes and backup.stat().st_nlink == 1
    assert path.stat().st_size < before // 5
    assert _files(path) == {path.name, backup.name}  # exactly one backup, no stray temp
    plan = store.journal_stats()["last_slim_rewrite"]["plan"]
    assert plan["receipts_to_drop_by_retention"] == 4
    assert plan["receipts_to_compact"] == 12
    assert plan["bytes_before"] == before and plan["bytes_after"] == path.stat().st_size
    message = " ".join(r.getMessage() for r in caplog.records)
    assert "compact 12 receipts, drop 4 by retention" in message and str(before) in message
    info = store.journal_stats()["rewrite_backup"]
    assert info["bytes"] == before  # extra disk held == journal size before the rewrite, no more
    assert info["expires_at"] == (datetime.fromtimestamp(backup.stat().st_mtime, timezone.utc)
                                  + timedelta(days=7)).isoformat()
    assert datetime.fromisoformat(info["created_at"]) == NOW


def test_backup_expires_after_seven_days_and_never_earlier(tmp_path):
    path = tmp_path / "ingest_schedule.jsonl"
    _hosted_shape(path)
    store = _open(path)
    backup = path.with_name(path.name + BACKUP)
    assert sched.DEFAULT_RECEIPT_BACKUP_KEEP_DAYS == 7
    store.enforce_retention(now=NOW + timedelta(days=7) - timedelta(seconds=1))
    assert backup.exists()
    store.enforce_retention(now=NOW + timedelta(days=7))
    assert not backup.exists()
    assert store.journal_stats()["rewrite_backup"] is None


def test_keep_days_is_one_named_setting(tmp_path, monkeypatch):
    monkeypatch.setenv(sched.RECEIPT_BACKUP_KEEP_DAYS_ENV, "2")
    path = tmp_path / "ingest_schedule.jsonl"
    _hosted_shape(path)
    store = _open(path)
    store.enforce_retention(now=NOW + timedelta(days=1, hours=23))
    assert path.with_name(path.name + BACKUP).exists()
    store.enforce_retention(now=NOW + timedelta(days=2))
    assert not path.with_name(path.name + BACKUP).exists()


def test_later_rewrite_does_not_replace_existing_backup(tmp_path):
    path = tmp_path / "ingest_schedule.jsonl"
    _hosted_shape(path)
    original = path.read_bytes()
    _open(path)
    backup = path.with_name(path.name + BACKUP)
    # More legacy rows arrive and are rewritten while the first backup is still inside its window.
    with path.open("ab") as handle:
        for row in _legacy_rows(100, 3):
            handle.write(row)
    _open(path, now=NOW + timedelta(days=3))
    assert backup.read_bytes() == original  # oldest complete copy survives the whole window
    assert len([p for p in path.parent.iterdir() if p.name.endswith(BACKUP)]) == 1


def _legacy_rows(start: int, count: int) -> list[bytes]:
    rows = []
    for index in range(start, start + count):
        receipt = _legacy_receipt(index)
        rows.append((json.dumps({"schema_version": "ingest_schedule_store.v1", "record_type": "ingest_receipt",
                                 "record_id": receipt.ingest_run_id, "payload": receipt.to_dict()},
                                sort_keys=True, separators=(",", ":")) + "\n").encode())
    return rows


def test_rollback_by_rename_is_read_by_new_and_previous_reader(tmp_path):
    previous = _previous_scheduler_module()
    path = tmp_path / "ingest_schedule.jsonl"
    _hosted_shape(path)
    _open(path)
    backup = path.with_name(path.name + BACKUP)
    os.replace(backup, path)  # the documented rollback
    restored_bytes = path.read_bytes()
    old_view = previous.JsonlIngestScheduleStore(path, retention_days=0)  # keep every receipt
    assert len(old_view._receipts) == 12  # all rows incl. the ones retention dropped
    path.write_bytes(restored_bytes)
    # The new store reads the restored file too (it rewrites again, keeping a fresh backup).
    restored = _open(path, now=NOW + timedelta(days=1))
    assert set(restored._receipts) <= set(old_view._receipts)
    assert {r for r in old_view._receipts if r not in restored._receipts} == {
        f"ingest-legacy-{i:04d}" for i in range(4)}


def test_crash_between_hard_link_and_rename_leaves_a_complete_journal_and_backup(tmp_path, monkeypatch):
    path = tmp_path / "ingest_schedule.jsonl"
    _hosted_shape(path)
    original = path.read_bytes()

    class Crash(BaseException):
        pass

    def crash(*_a, **_k):
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(sched.os, "replace", crash)
        with pytest.raises(Crash):
            _open(path)
    backup = path.with_name(path.name + BACKUP)
    assert path.read_bytes() == original and backup.read_bytes() == original  # both complete
    # Restart: the stale temp is discarded, the existing backup stays, and the rewrite completes.
    store = _open(path)
    assert backup.read_bytes() == original
    assert path.stat().st_size < len(original) // 5
    assert not path.with_name(path.name + ".slim.tmp").exists()
    assert len(store._receipts) == 8


def test_low_free_disk_skips_the_rewrite_with_a_readable_reason(tmp_path, monkeypatch):
    path = tmp_path / "ingest_schedule.jsonl"
    before = _hosted_shape(path)
    original = path.read_bytes()
    real = sched.shutil.disk_usage
    monkeypatch.setattr(sched.shutil, "disk_usage",
                        lambda p: real(p)._replace(free=before + sched.rewrite_free_margin_bytes() - 1))
    store = _open(path)
    assert path.read_bytes() == original and _files(path) == {path.name}
    slim = store.journal_stats()["last_slim_rewrite"]
    assert slim["rewritten"] is False and "free disk" in slim["skipped_reason"]
    # replay still compacts in memory, so the service stays usable
    assert "closes" not in json.dumps(store.get_receipt("ingest-legacy-0011").evidence_refs)


def test_no_backup_is_made_when_nothing_needs_rewriting(tmp_path):
    path = tmp_path / "ingest_schedule.jsonl"
    path.write_bytes(b"".join(_legacy_rows(0, 2)))
    _open(path)
    path.with_name(path.name + BACKUP).unlink()
    _open(path)
    assert _files(path) == {path.name}
