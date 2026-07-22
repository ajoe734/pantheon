"""Owner stores for governance-managed JSON records.

The governance service owns freeze-order and rollback read models.  Dev uses
one JSON file per dataset under ``GOVERNANCE_DATA_DIR``; staging and
production follow the existing governance Postgres posture.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Protocol, Sequence

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


class GovernanceRecordStore(Protocol):
    """Minimal owner-store contract shared by the read and future write APIs."""

    def put(self, record: Dict[str, Any]) -> None: ...

    def get(self, record_id: str) -> Dict[str, Any] | None: ...

    def list_all(self) -> list[Dict[str, Any]]: ...

    def insert_if_absent(
        self, record: Dict[str, Any]
    ) -> tuple[bool, Dict[str, Any]]: ...

    def compare_and_set(
        self,
        expected_record: Dict[str, Any],
        record: Dict[str, Any],
    ) -> tuple[bool, Dict[str, Any] | None]: ...


def _copy_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(record))


def _record_id(record: Dict[str, Any], id_fields: Sequence[str]) -> str:
    for field in id_fields:
        value = record.get(field)
        if value not in (None, ""):
            return str(value)
    raise ValueError(f"record requires one of: {', '.join(id_fields)}")


class JsonGovernanceRecordStore:
    """Small atomic JSON owner store for dev and local recovery posture."""

    def __init__(self, storage_path: str | Path, *, id_fields: Sequence[str]) -> None:
        self.storage_path = Path(storage_path)
        self.id_fields = tuple(id_fields)
        if not self.id_fields:
            raise ValueError("id_fields must not be empty")
        self._lock = threading.RLock()
        self._records: Dict[str, Dict[str, Any]] = {}
        if self.storage_path.exists():
            self._load()

    def put(self, record: Dict[str, Any]) -> None:
        if not isinstance(record, dict):
            raise TypeError("record must be a dictionary")
        record_id = _record_id(record, self.id_fields)
        with self._lock:
            self._records[record_id] = _copy_record(record)
            self._save()

    def get(self, record_id: str) -> Dict[str, Any] | None:
        with self._lock:
            record = self._records.get(str(record_id))
            return _copy_record(record) if record is not None else None

    def list_all(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [_copy_record(record) for record in self._records.values()]

    def insert_if_absent(
        self, record: Dict[str, Any]
    ) -> tuple[bool, Dict[str, Any]]:
        record_id = _record_id(record, self.id_fields)
        with self._lock:
            existing = self._records.get(record_id)
            if existing is not None:
                return False, _copy_record(existing)
            self._records[record_id] = _copy_record(record)
            try:
                self._save()
            except Exception:
                self._records.pop(record_id, None)
                raise
            return True, _copy_record(record)

    def compare_and_set(
        self,
        expected_record: Dict[str, Any],
        record: Dict[str, Any],
    ) -> tuple[bool, Dict[str, Any] | None]:
        expected_id = _record_id(expected_record, self.id_fields)
        record_id = _record_id(record, self.id_fields)
        if expected_id != record_id:
            raise ValueError("compare_and_set record identities must match")
        with self._lock:
            current = self._records.get(record_id)
            if current != expected_record:
                return False, _copy_record(current) if current is not None else None
            self._records[record_id] = _copy_record(record)
            try:
                self._save()
            except Exception:
                self._records[record_id] = current
                raise
            return True, _copy_record(record)

    def _load(self) -> None:
        text = self.storage_path.read_text(encoding="utf-8").strip()
        if not text:
            return
        payload = json.loads(text)
        if isinstance(payload, dict):
            raw_records = list(payload.values())
        elif isinstance(payload, list):
            raw_records = payload
        else:
            raise ValueError(f"{self.storage_path} must contain an object or list")

        loaded: Dict[str, Dict[str, Any]] = {}
        for record in raw_records:
            if not isinstance(record, dict):
                raise ValueError(f"{self.storage_path} contains a non-object record")
            record_id = _record_id(record, self.id_fields)
            if record_id in loaded:
                raise ValueError(f"{self.storage_path} contains duplicate id {record_id!r}")
            loaded[record_id] = _copy_record(record)
        self._records = loaded

    def _save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.storage_path.with_name(f".{self.storage_path.name}.tmp")
        payload = json.dumps(self._records, indent=2, sort_keys=True) + "\n"
        temporary_path.write_text(payload, encoding="utf-8")
        os.replace(temporary_path, self.storage_path)


class PostgresGovernanceRecordStore:
    """Governance-owned JSONB store used in enforced persistence posture."""

    def __init__(
        self,
        *,
        dsn: str,
        table: str,
        id_fields: Sequence[str],
        bootstrap: bool = True,
    ) -> None:
        self.id_fields = tuple(id_fields)
        if not self.id_fields:
            raise ValueError("id_fields must not be empty")
        self._records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="governance-svc",
            bootstrap=bootstrap,
        )

    def put(self, record: Dict[str, Any]) -> None:
        record_id = _record_id(record, self.id_fields)
        self._records.put(record_id, _copy_record(record))

    def get(self, record_id: str) -> Dict[str, Any] | None:
        record = self._records.get(str(record_id))
        return _copy_record(record) if record is not None else None

    def list_all(self) -> list[Dict[str, Any]]:
        return [_copy_record(record) for record in self._records.list_all()]

    def insert_if_absent(
        self, record: Dict[str, Any]
    ) -> tuple[bool, Dict[str, Any]]:
        record_id = _record_id(record, self.id_fields)
        inserted, canonical = self._records.insert_if_absent(record_id, record)
        return inserted, _copy_record(canonical)

    def compare_and_set(
        self,
        expected_record: Dict[str, Any],
        record: Dict[str, Any],
    ) -> tuple[bool, Dict[str, Any] | None]:
        expected_id = _record_id(expected_record, self.id_fields)
        record_id = _record_id(record, self.id_fields)
        if expected_id != record_id:
            raise ValueError("compare_and_set record identities must match")
        updated, canonical = self._records.compare_and_set(
            record_id, expected_record, record
        )
        return updated, _copy_record(canonical) if canonical is not None else None


def build_governance_record_store(
    storage_path: str | Path,
    *,
    table: str,
    id_fields: Sequence[str],
) -> GovernanceRecordStore:
    """Build a dataset store using the governance service persistence posture."""

    backend = os.getenv("GOVERNANCE_STORE_BACKEND", "json").strip().lower()
    if backend in ("", "json"):
        return JsonGovernanceRecordStore(storage_path, id_fields=id_fields)
    if backend != "postgres":
        raise ValueError("GOVERNANCE_STORE_BACKEND must be json or postgres")

    dsn = os.getenv("GOVERNANCE_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("GOVERNANCE_STORE_DSN or DATABASE_URL is required for Postgres governance store")
    bootstrap = os.getenv("GOVERNANCE_STORE_BOOTSTRAP", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    return PostgresGovernanceRecordStore(
        dsn=dsn,
        table=table,
        id_fields=id_fields,
        bootstrap=bootstrap,
    )
