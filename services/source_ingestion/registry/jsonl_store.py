"""Shared JSONL file store for dev-tier registry persistence.

In production both registries back onto Postgres; the JSONL store gives a
zero-dependency dev path.

Mutations (``upsert``, ``delete``, ``overwrite_all``) are serialised by a
sidecar ``flock`` lease that holds across threads and independent processes.
``upsert`` and ``delete`` maintain an in-memory keyed byte-span index synced by
the file's ``(st_ino, st_size, st_mtime_ns)`` signature so that:
- ``read_by_id`` is a keyed lookup rather than a full-file scan;
- ``upsert`` of an unchanged record is a no-op that writes zero bytes;
- ``upsert`` of a new or changed record performs a single-record durable write
  (and blanks the superseded span when a record grows) without rewriting the
  entire file;
- ``delete`` blanks the single record's span in place;
- stale ``*.tmp`` files left by an interrupted rewrite are removed automatically
  while holding the store's file lock.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Iterator
from uuid import uuid4

from services.source_ingestion.process_lock import exclusive_file_lock


_READ_CHUNK_BYTES = 65536
_DEFAULT_COMPACT_FACTOR = 2
_DEFAULT_COMPACT_MIN_BYTES = 4 * 1024 * 1024


@dataclass
class _PathIndexState:
    signature: tuple[int, int, int] | None = None
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    spans: dict[str, tuple[int, int]] = field(default_factory=dict)
    raw_lines: dict[str, bytes] = field(default_factory=dict)
    stale_spans: list[tuple[int, int]] = field(default_factory=list)
    live_bytes: int = 0
    file_size: int = 0
    incomplete_tail_offset: int | None = None
    generation: int = 0

    def clear(self) -> None:
        self.signature = None
        self.records.clear()
        self.spans.clear()
        self.raw_lines.clear()
        self.stale_spans.clear()
        self.live_bytes = 0
        self.file_size = 0
        self.incomplete_tail_offset = None


# One RLock per resolved data path, so threads in this process share the same
# in-process lock that the sidecar flock is paired with.
_LOCAL_LOCKS: dict[str, RLock] = {}
_LEASE_DEPTH: dict[str, int] = {}
_INDEX_STATES: dict[tuple[str, str], _PathIndexState] = {}
_LOCAL_LOCKS_GUARD = Lock()


def _resolved_key(path: Path) -> str:
    return str(path.resolve() if path.is_absolute() else (Path(os.getcwd()) / path).resolve())


def _local_lock_for(path: Path) -> RLock:
    key = _resolved_key(path)
    with _LOCAL_LOCKS_GUARD:
        lock = _LOCAL_LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _LOCAL_LOCKS[key] = lock
        return lock


def _index_state_for(path: Path, id_field: str) -> _PathIndexState:
    key = (_resolved_key(path), id_field)
    with _LOCAL_LOCKS_GUARD:
        state = _INDEX_STATES.get(key)
        if state is None:
            state = _PathIndexState()
            _INDEX_STATES[key] = state
        return state


def _pwrite_all(fd: int, data: bytes, offset: int) -> None:
    view = memoryview(data)
    while view:
        written = os.pwrite(fd, view, offset)
        offset += written
        view = view[written:]


def _is_store_temp_file(candidate_name: str, store_name: str, store_stem: str) -> bool:
    if not candidate_name.endswith(".tmp"):
        return False
    if candidate_name in (f".{store_name}.tmp", f"{store_name}.tmp", f".{store_stem}.tmp"):
        return True
    prefixes = [
        f".{store_name}.",
        f"{store_name}.",
    ]
    if store_stem and store_stem != store_name:
        prefixes.append(f".{store_stem}.")
        prefixes.append(f".{store_stem}_")
    return any(candidate_name.startswith(prefix) for prefix in prefixes)


class JsonlRegistryStore:
    """Keyed JSONL store with single-record durable writes and lock-guarded temp cleanup.

    Each non-blank line is one JSON object.  The ``id_field`` names the primary
    key field so the store can detect conflicts, skip identical replays, and
    update or blank individual records in O(1) without rewriting the whole file.
    """

    def __init__(
        self,
        path: str | Path,
        id_field: str,
        *,
        compact_factor: int = _DEFAULT_COMPACT_FACTOR,
        compact_min_bytes: int = _DEFAULT_COMPACT_MIN_BYTES,
    ) -> None:
        self._path = Path(path)
        self._id_field = id_field
        self._lock_path = self._path.with_name(f".{self._path.name}.lock")
        self._compact_factor = max(2, int(compact_factor))
        self._compact_min_bytes = max(0, int(compact_min_bytes))

    @property
    def path(self) -> Path:
        return self._path

    @property
    def generation(self) -> int:
        with _local_lock_for(self._path):
            state = self._sync_index_locked()
            return state.generation

    def _ensure_parent(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _cleanup_stale_temp_files_locked(self) -> int:
        parent = self._path.parent
        if not parent.exists():
            return 0
        store_name = self._path.name
        store_stem = self._path.stem
        removed = 0
        try:
            entries = list(os.scandir(parent))
        except OSError:
            return 0
        for entry in entries:
            if not _is_store_temp_file(entry.name, store_name, store_stem):
                continue
            try:
                Path(entry.path).unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
        return removed

    def cleanup_stale_temp_files(self) -> int:
        """Remove any orphan temp files for this store while holding the file lock."""
        with self._mutation_lease() as removed:
            return removed

    @contextmanager
    def _mutation_lease(self) -> Iterator[int]:
        """Hold the cross-process and in-process lease for a store mutation."""
        key = _resolved_key(self._path)
        local_lock = _local_lock_for(self._path)
        with local_lock:
            depth = _LEASE_DEPTH.get(key, 0)
            if depth > 0:
                _LEASE_DEPTH[key] = depth + 1
                try:
                    yield 0
                finally:
                    _LEASE_DEPTH[key] = depth
                return
            self._ensure_parent()
            with exclusive_file_lock(self._lock_path, local_lock):
                _LEASE_DEPTH[key] = 1
                try:
                    removed = self._cleanup_stale_temp_files_locked()
                    yield removed
                finally:
                    _LEASE_DEPTH[key] = 0

    def _sync_index_locked(self) -> _PathIndexState:
        state = _index_state_for(self._path, self._id_field)
        try:
            st = self._path.stat()
        except FileNotFoundError:
            if state.signature is not None or state.records:
                state.clear()
                state.generation += 1
            return state

        sig = (st.st_ino, st.st_size, st.st_mtime_ns)
        if state.signature == sig:
            return state

        state.clear()
        fd = os.open(self._path, os.O_RDONLY)
        try:
            offset = 0
            carry = b""
            carry_offset = 0
            while offset < st.st_size:
                chunk = os.pread(fd, min(_READ_CHUNK_BYTES, st.st_size - offset), offset)
                if not chunk:
                    break
                offset += len(chunk)
                data = carry + chunk
                pos = 0
                while (nl := data.find(b"\n", pos)) != -1:
                    line_bytes = data[pos : nl + 1]
                    line_offset = carry_offset + pos
                    pos = nl + 1
                    stripped = line_bytes.strip()
                    if not stripped:
                        continue
                    record = json.loads(stripped.decode("utf-8"))
                    if not isinstance(record, dict):
                        continue
                    raw_id = record.get(self._id_field)
                    entry_key = str(raw_id) if raw_id is not None else f"__line_{line_offset}"
                    if entry_key in state.spans:
                        state.stale_spans.append(state.spans.pop(entry_key))
                        old_raw = state.raw_lines.pop(entry_key, b"")
                        state.live_bytes -= len(old_raw)
                        state.records.pop(entry_key, None)
                    canonical_raw = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
                    state.records[entry_key] = record
                    state.spans[entry_key] = (line_offset, len(line_bytes))
                    state.raw_lines[entry_key] = canonical_raw
                    state.live_bytes += len(canonical_raw)
                carry = data[pos:]
                carry_offset = carry_offset + pos
            if carry:
                if carry.strip():
                    try:
                        record = json.loads(carry.strip().decode("utf-8"))
                        if isinstance(record, dict):
                            raw_id = record.get(self._id_field)
                            entry_key = str(raw_id) if raw_id is not None else f"__line_{carry_offset}"
                            if entry_key in state.spans:
                                state.stale_spans.append(state.spans.pop(entry_key))
                                old_raw = state.raw_lines.pop(entry_key, b"")
                                state.live_bytes -= len(old_raw)
                                state.records.pop(entry_key, None)
                            canonical_raw = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
                            state.records[entry_key] = record
                            state.spans[entry_key] = (carry_offset, len(carry))
                            state.raw_lines[entry_key] = canonical_raw
                            state.live_bytes += len(canonical_raw)
                    except Exception:
                        state.incomplete_tail_offset = carry_offset
                else:
                    state.incomplete_tail_offset = carry_offset
        finally:
            os.close(fd)

        state.file_size = st.st_size
        state.signature = sig
        state.generation += 1
        return state

    def _raw_records_snapshot_locked(self) -> tuple[int, dict[str, dict[str, Any]]]:
        state = self._sync_index_locked()
        return state.generation, state.records

    def read_all(self) -> list[dict[str, Any]]:
        with _local_lock_for(self._path):
            state = self._sync_index_locked()
            return [copy.deepcopy(record) for record in state.records.values()]

    def read_by_id(self, entry_id: str) -> dict[str, Any] | None:
        with _local_lock_for(self._path):
            state = self._sync_index_locked()
            record = state.records.get(str(entry_id))
            if record is None:
                return None
            return copy.deepcopy(record)

    def append(self, record: dict[str, Any]) -> None:
        if self._id_field in record:
            self.upsert(record)
            return
        with self._mutation_lease():
            state = self._sync_index_locked()
            raw = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o666)
            try:
                eof_off = os.fstat(fd).st_size
                _pwrite_all(fd, raw, eof_off)
                os.fsync(fd)
            finally:
                os.close(fd)
            state.signature = None

    def overwrite_all(self, records: list[dict[str, Any]]) -> None:
        with self._mutation_lease():
            self._ensure_parent()
            temp_path = self._path.with_name(f".{self._path.name}.{os.getpid()}.{uuid4().hex}.tmp")
            new_records: dict[str, dict[str, Any]] = {}
            new_spans: dict[str, tuple[int, int]] = {}
            new_raw_lines: dict[str, bytes] = {}
            offset = 0
            live_bytes = 0
            try:
                with open(temp_path, "x", encoding="utf-8") as fh:
                    for record in records:
                        line_str = json.dumps(record, ensure_ascii=False) + "\n"
                        raw = line_str.encode("utf-8")
                        fh.write(line_str)
                        raw_id = record.get(self._id_field)
                        entry_key = str(raw_id) if raw_id is not None else f"__line_{offset}"
                        if entry_key in new_raw_lines:
                            live_bytes -= len(new_raw_lines.pop(entry_key))
                            new_records.pop(entry_key, None)
                            new_spans.pop(entry_key, None)
                        new_records[entry_key] = copy.deepcopy(record)
                        new_spans[entry_key] = (offset, len(raw))
                        new_raw_lines[entry_key] = raw
                        live_bytes += len(raw)
                        offset += len(raw)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(temp_path, self._path)
                directory_fd = os.open(self._path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                st = self._path.stat()
                state = _index_state_for(self._path, self._id_field)
                state.clear()
                state.records = new_records
                state.spans = new_spans
                state.raw_lines = new_raw_lines
                state.live_bytes = live_bytes
                state.file_size = st.st_size
                state.signature = (st.st_ino, st.st_size, st.st_mtime_ns)
                state.generation += 1
            finally:
                temp_path.unlink(missing_ok=True)

    def _compact_in_place_fd(self, fd: int, state: _PathIndexState) -> None:
        new_spans: dict[str, tuple[int, int]] = {}
        chunks: list[bytes] = []
        offset = 0
        for entry_key in state.records:
            raw = state.raw_lines[entry_key]
            new_spans[entry_key] = (offset, len(raw))
            chunks.append(raw)
            offset += len(raw)
        payload = b"".join(chunks)
        if payload:
            _pwrite_all(fd, payload, 0)
        os.ftruncate(fd, len(payload))
        os.fsync(fd)
        state.spans = new_spans
        state.stale_spans.clear()
        state.live_bytes = len(payload)
        state.file_size = len(payload)
        state.incomplete_tail_offset = None

    def _maybe_compact_fd(self, fd: int, state: _PathIndexState) -> bool:
        dead_bytes = state.file_size - state.live_bytes
        if dead_bytes <= self._compact_min_bytes:
            return False
        if state.file_size < self._compact_factor * max(state.live_bytes, 1):
            return False
        self._compact_in_place_fd(fd, state)
        return True

    def compact(self) -> bool:
        """Compact blanked spans in-place while holding the store's file lock."""
        with self._mutation_lease():
            state = self._sync_index_locked()
            if not self._path.exists() or state.file_size <= state.live_bytes:
                return False
            fd = os.open(self._path, os.O_RDWR)
            try:
                self._compact_in_place_fd(fd, state)
                st = os.fstat(fd)
                state.signature = (st.st_ino, st.st_size, st.st_mtime_ns)
                state.generation += 1
            finally:
                os.close(fd)
            return True

    def upsert_and_delete(
        self,
        record: dict[str, Any],
        *,
        delete_ids: Iterable[str] = (),
    ) -> bool:
        """Atomically upsert one record and delete any superseded entry_ids.

        Returns True when a durable write occurred, or False when ``record`` is
        an identical replay and ``delete_ids`` had nothing to remove.
        """
        entry_id = str(record[self._id_field])
        new_line = json.dumps(record, ensure_ascii=False) + "\n"
        new_raw = new_line.encode("utf-8")

        with self._mutation_lease():
            state = self._sync_index_locked()
            to_delete = [
                str(did)
                for did in delete_ids
                if str(did) != entry_id and str(did) in state.records
            ]
            is_unchanged = (
                entry_id in state.records
                and (
                    state.raw_lines.get(entry_id) == new_raw
                    or state.records[entry_id] == record
                )
            )
            if (
                is_unchanged
                and not to_delete
                and not state.stale_spans
                and state.incomplete_tail_offset is None
            ):
                return False

            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o666)
            try:
                if state.incomplete_tail_offset is not None:
                    os.ftruncate(fd, state.incomplete_tail_offset)
                    state.file_size = state.incomplete_tail_offset
                    state.incomplete_tail_offset = None
                else:
                    state.file_size = os.fstat(fd).st_size

                if state.stale_spans:
                    for s_off, s_len in state.stale_spans:
                        if s_len > 0:
                            _pwrite_all(fd, b" " * (s_len - 1) + b"\n", s_off)
                    state.stale_spans.clear()

                for del_id in to_delete:
                    d_off, d_len = state.spans.pop(del_id)
                    d_raw = state.raw_lines.pop(del_id, b"")
                    state.records.pop(del_id, None)
                    state.live_bytes -= len(d_raw)
                    if d_len > 0:
                        _pwrite_all(fd, b" " * (d_len - 1) + b"\n", d_off)

                if entry_id in state.records:
                    if not is_unchanged:
                        old_off, old_len = state.spans[entry_id]
                        old_raw = state.raw_lines[entry_id]
                        if len(new_raw) <= old_len:
                            padded = new_raw[:-1] + (b" " * (old_len - len(new_raw))) + b"\n"
                            _pwrite_all(fd, padded, old_off)
                            state.spans[entry_id] = (old_off, old_len)
                        else:
                            if state.file_size > 0 and os.pread(fd, 1, state.file_size - 1) != b"\n":
                                _pwrite_all(fd, b"\n", state.file_size)
                                state.file_size += 1
                            eof_off = state.file_size
                            _pwrite_all(fd, new_raw, eof_off)
                            if old_len > 0:
                                _pwrite_all(fd, b" " * (old_len - 1) + b"\n", old_off)
                            state.spans.pop(entry_id, None)
                            state.spans[entry_id] = (eof_off, len(new_raw))
                            state.records.pop(entry_id, None)
                            state.file_size = eof_off + len(new_raw)
                        state.records[entry_id] = copy.deepcopy(record)
                        state.raw_lines[entry_id] = new_raw
                        state.live_bytes += len(new_raw) - len(old_raw)
                else:
                    if state.file_size > 0 and os.pread(fd, 1, state.file_size - 1) != b"\n":
                        _pwrite_all(fd, b"\n", state.file_size)
                        state.file_size += 1
                    eof_off = state.file_size
                    _pwrite_all(fd, new_raw, eof_off)
                    state.records[entry_id] = copy.deepcopy(record)
                    state.spans[entry_id] = (eof_off, len(new_raw))
                    state.raw_lines[entry_id] = new_raw
                    state.live_bytes += len(new_raw)
                    state.file_size = eof_off + len(new_raw)

                os.fsync(fd)
                self._maybe_compact_fd(fd, state)
                st = os.fstat(fd)
                state.file_size = st.st_size
                state.signature = (st.st_ino, st.st_size, st.st_mtime_ns)
                state.generation += 1
            finally:
                os.close(fd)
        return True

    def upsert(self, record: dict[str, Any]) -> bool:
        """Replace existing record with same id_field or append if absent.

        Returns True when a durable write occurred, or False when ``record``
        matched the stored record identically.
        """
        return self.upsert_and_delete(record)

    def delete(self, entry_id: str) -> bool:
        key = str(entry_id)
        with self._mutation_lease():
            state = self._sync_index_locked()
            if key not in state.records and not state.stale_spans and state.incomplete_tail_offset is None:
                return False
            if key not in state.records:
                return False
            old_off, old_len = state.spans.pop(key)
            old_raw = state.raw_lines.pop(key, b"")
            state.records.pop(key, None)
            state.live_bytes -= len(old_raw)

            fd = os.open(self._path, os.O_RDWR)
            try:
                if state.incomplete_tail_offset is not None:
                    os.ftruncate(fd, state.incomplete_tail_offset)
                    state.file_size = state.incomplete_tail_offset
                    state.incomplete_tail_offset = None
                if state.stale_spans:
                    for s_off, s_len in state.stale_spans:
                        if s_len > 0:
                            _pwrite_all(fd, b" " * (s_len - 1) + b"\n", s_off)
                    state.stale_spans.clear()
                if old_len > 0:
                    _pwrite_all(fd, b" " * (old_len - 1) + b"\n", old_off)
                os.fsync(fd)
                self._maybe_compact_fd(fd, state)
                st = os.fstat(fd)
                state.file_size = st.st_size
                state.signature = (st.st_ino, st.st_size, st.st_mtime_ns)
                state.generation += 1
            finally:
                os.close(fd)
        return True

