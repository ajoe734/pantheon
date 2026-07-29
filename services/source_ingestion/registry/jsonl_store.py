"""Shared JSONL file store for dev-tier registry persistence.

In production both registries back onto Postgres; the JSONL store gives a
zero-dependency dev path.

Read-modify-write mutations (``upsert`` and ``delete``) rewrite the whole file,
so two concurrent writers would otherwise lose one another's records.  Those
mutations are serialised by a sidecar ``flock`` lease that holds across threads
and independent processes; the read and the overwrite happen inside one lease.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Callable, Iterator
from uuid import uuid4

from services.source_ingestion.process_lock import exclusive_file_lock


# One RLock per resolved data path, so threads in this process share the same
# in-process lock that the sidecar flock is paired with.
_LOCAL_LOCKS: dict[str, RLock] = {}
_LOCAL_LOCKS_GUARD = Lock()


def _local_lock_for(path: Path) -> RLock:
    key = str(path.resolve() if path.is_absolute() else Path(os.getcwd()) / path)
    with _LOCAL_LOCKS_GUARD:
        lock = _LOCAL_LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _LOCAL_LOCKS[key] = lock
        return lock


class JsonlRegistryStore:
    """Append-log JSONL store that supports read/overwrite semantics.

    Each line is one JSON object.  The ``id_field`` names the primary key
    field so the store can detect conflicts and overwrite on upsert.
    """

    def __init__(self, path: str | Path, id_field: str) -> None:
        self._path = Path(path)
        self._id_field = id_field
        self._lock_path = self._path.with_name(f".{self._path.name}.lock")

    @property
    def path(self) -> Path:
        return self._path

    def _ensure_parent(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def read_all(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        records: list[dict[str, Any]] = []
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def read_by_id(self, entry_id: str) -> dict[str, Any] | None:
        for record in self.read_all():
            if record.get(self._id_field) == entry_id:
                return record
        return None

    def append(self, record: dict[str, Any]) -> None:
        self._ensure_parent()
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def overwrite_all(self, records: list[dict[str, Any]]) -> None:
        self._ensure_parent()
        temp_path = self._path.with_name(f".{self._path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            with open(temp_path, "x", encoding="utf-8") as fh:
                for record in records:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, self._path)
            directory_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp_path.unlink(missing_ok=True)

    @contextmanager
    def _mutation_lease(self) -> Iterator[None]:
        """Hold the read-modify-write lease for one whole-file mutation."""
        self._ensure_parent()
        with exclusive_file_lock(self._lock_path, _local_lock_for(self._path)):
            yield

    def upsert(self, record: dict[str, Any]) -> None:
        """Replace existing record with same id_field or append if absent.

        The read and the overwrite share one lease, so a concurrent writer
        cannot base its rewrite on a snapshot that predates this record.
        """
        entry_id = record[self._id_field]
        with self._mutation_lease():
            existing = self.read_all()
            replaced = False
            updated: list[dict[str, Any]] = []
            for existing_record in existing:
                if existing_record.get(self._id_field) == entry_id:
                    updated.append(record)
                    replaced = True
                else:
                    updated.append(existing_record)
            if not replaced:
                updated.append(record)
            self.overwrite_all(updated)

    def delete(self, entry_id: str) -> bool:
        with self._mutation_lease():
            existing = self.read_all()
            filtered = [r for r in existing if r.get(self._id_field) != entry_id]
            if len(filtered) == len(existing):
                return False
            self.overwrite_all(filtered)
        return True
