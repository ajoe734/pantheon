"""Stale temp sweep for the governance JSON record store (RC3)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from services.governance.record_store import JsonGovernanceRecordStore

HEX = "0123456789abcdef0123456789abcdef"


def _make(path: Path) -> JsonGovernanceRecordStore:
    return JsonGovernanceRecordStore(path, id_fields=("id",))


def _leftover(path: Path, name: str, *, older: bool) -> Path:
    temp = path.with_name(name)
    temp.write_text("partial")
    base = path.stat().st_mtime_ns
    ns = base - 10**9 if older else base + 10**9
    os.utime(temp, ns=(ns, ns))
    return temp


def _files(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir() if not p.name.endswith((".lock", ".flock"))}


def test_killed_write_then_restart_leaves_one_real_file(tmp_path):
    path = tmp_path / "records.json"
    _make(path).put({"id": "a"})
    killed = [
        _leftover(path, f".records.json.{HEX}.tmp", older=True),
        _leftover(path, "records.json.tmp", older=True),
    ]
    store = _make(path)  # restart
    store.put({"id": "b"})
    assert not any(p.exists() for p in killed)
    assert _files(tmp_path) == {"records.json"}
    assert set(json.loads(path.read_text())) == {"a", "b"}


def test_newer_temp_foreign_and_symlink_are_kept(tmp_path):
    path = tmp_path / "records.json"
    _make(path).put({"id": "a"})
    newer = _leftover(path, f".records.json.{HEX}.tmp", older=False)
    foreign = _leftover(path, f".other.json.{HEX}.tmp", older=True)
    similar = _leftover(path, "records.json.bak", older=True)
    target = tmp_path / "target"
    target.write_text("keep")
    link = tmp_path / f".records.json.{'d' * 32}.tmp"
    link.symlink_to(target)
    _make(path).put({"id": "b"})
    assert newer.exists() and foreign.exists() and similar.exists()
    assert link.is_symlink() and target.read_text() == "keep"
    assert path.exists()


def test_concurrent_writers_are_never_broken(tmp_path):
    path = tmp_path / "records.json"
    stores = [_make(path) for _ in range(4)]
    errors: list[BaseException] = []

    def work(i, store):
        try:
            for n in range(10):
                store.put({"id": f"{i}-{n}"})
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i, s)) for i, s in enumerate(stores)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(json.loads(path.read_text())) == 40
    assert _files(tmp_path) == {"records.json"}


def test_new_file_mode_still_follows_umask_and_existing_mode_kept(tmp_path):
    path = tmp_path / "records.json"
    _make(path).put({"id": "a"})
    os.chmod(path, 0o640)
    _make(path).put({"id": "b"})
    assert (path.stat().st_mode & 0o777) == 0o640
