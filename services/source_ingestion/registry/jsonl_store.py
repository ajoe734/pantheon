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

    def overwrite_all(self, records: list[dict[str, Any]]) -> None:
        self._ensure_parent()
        with open(self._path, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

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
