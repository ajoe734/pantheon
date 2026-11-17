from __future__ import annotations

import json
from pathlib import Path

from services.source_ingestion.registry.jsonl_store import JsonlRegistryStore


def _legacy_read(path: Path) -> list[dict]:
    """The previous release's reader: every non-blank line is JSON."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _fill(store: JsonlRegistryStore, n: int) -> None:
    store.overwrite_all([{"id": f"r{i}", "v": "x" * 200} for i in range(n)])


def test_identical_replay_writes_nothing(tmp_path: Path) -> None:
    store = JsonlRegistryStore(tmp_path / "s.jsonl", "id")
    assert store.upsert({"id": "a", "v": 1}) is True
    before = (tmp_path / "s.jsonl").stat()
    assert store.upsert({"id": "a", "v": 1}) is False
    after = (tmp_path / "s.jsonl").stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)


def test_upsert_bytes_written_independent_of_store_size(tmp_path: Path, monkeypatch) -> None:
    import os

    written: list[int] = []
    real = os.pwrite

    def spy(fd, data, off):
        n = real(fd, data, off)
        written.append(n)
        return n

    monkeypatch.setattr(os, "pwrite", spy)
    for size in (10, 2000):
        store = JsonlRegistryStore(tmp_path / f"s{size}.jsonl", "id")
        _fill(store, size)
        written.clear()
        store.upsert({"id": "new", "v": "y"})
        assert sum(written) < 100


def test_update_and_delete_keep_legacy_reader_compatible(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    store = JsonlRegistryStore(path, "id")
    _fill(store, 5)
    store.upsert({"id": "r2", "v": "short"})
    store.upsert({"id": "r3", "v": "z" * 900})
    assert store.delete("r0") is True
    legacy = {r["id"]: r for r in _legacy_read(path)}
    assert set(legacy) == {"r1", "r2", "r3", "r4"}
    assert legacy["r2"]["v"] == "short"
    assert {r["id"] for r in JsonlRegistryStore(path, "id").read_all()} == set(legacy)


def test_stale_temp_files_removed_on_next_write(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    store = JsonlRegistryStore(path, "id")
    store.upsert({"id": "a"})
    orphan = tmp_path / ".s.jsonl.123.deadbeef.tmp"
    orphan.write_text("partial")
    unrelated = tmp_path / ".other.jsonl.1.x.tmp"
    unrelated.write_text("keep")
    store.upsert({"id": "b"})
    assert not orphan.exists()
    assert unrelated.exists()


def test_torn_tail_is_repaired_by_next_write(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    store = JsonlRegistryStore(path, "id")
    store.upsert({"id": "a"})
    with open(path, "ab") as fh:
        fh.write(b'{"id": "torn", "v"')
    fresh = JsonlRegistryStore(path, "id")
    fresh.upsert({"id": "b"})
    assert {r["id"] for r in _legacy_read(path)} == {"a", "b"}


def test_grown_record_keeps_position_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    store = JsonlRegistryStore(path, "id")
    _fill(store, 3)
    store.upsert({"id": "r0", "v": "g" * 2000})
    assert [r["id"] for r in JsonlRegistryStore(path, "id").read_all()] == ["r0", "r1", "r2"]
