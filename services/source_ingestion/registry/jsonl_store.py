"""Shared JSONL file store for dev-tier registry persistence.

In production both registries back onto Postgres; the JSONL store gives a
zero-dependency dev path.  The store is not thread-safe; callers must serialise
writes when running in multi-threaded contexts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4


class JsonlRegistryStore:
    """Append-log JSONL store that supports read/overwrite semantics.

    Each line is one JSON object.  The ``id_field`` names the primary key
    field so the store can detect conflicts and overwrite on upsert.
    """

    def __init__(self, path: str | Path, id_field: str) -> None:
        self._path = Path(path)
        self._id_field = id_field

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

    def upsert(self, record: dict[str, Any]) -> None:
        """Replace existing record with same id_field or append if absent."""
        entry_id = record[self._id_field]
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
        existing = self.read_all()
        filtered = [r for r in existing if r.get(self._id_field) != entry_id]
        if len(filtered) == len(existing):
            return False
        self.overwrite_all(filtered)
        return True
