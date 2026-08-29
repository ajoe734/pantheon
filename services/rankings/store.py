"""Independent persistent write owner for the Rankings domain.

Write authority: Rankings domain only (rankings-svc). This module is the
sole write path for ranking records and deliberately does not import
``services/control-plane/bff/read_store.py``: that module's ranking helpers
keep an in-process local overlay dict as a response fallback, which is not a
durable write path and does not survive process restart or a second reader
process. Every read here re-reads the durable backing store (an mtime-gated
reload for the JSON backend, or a fresh ``SELECT`` for the Postgres backend)
so a ``get``/``list`` immediately observes a write committed by a different
store instance, including one in a different process.

Source Ingestion remains reconcile-only for this domain: it may read the
durable rankings state to reconcile, but it is not a write owner and must not
call the write methods below.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - posix only in this repo's runtime
    fcntl = None

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RankingWriteOwnerError(RuntimeError):
    """Raised for invalid ranking records or write-ownership violations."""


class RankingConflictError(RankingWriteOwnerError):
    """Raised when ``create_ranking`` targets an existing ``ranking_id``."""


@dataclass
class RankingRecord:
    ranking_id: str
    title: str
    criteria: str
    entries: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "active"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RankingRecord":
        return cls(
            ranking_id=str(payload["ranking_id"]),
            title=str(payload.get("title", "")),
            criteria=str(payload.get("criteria", "")),
            entries=[dict(entry) for entry in (payload.get("entries") or [])],
            status=str(payload.get("status", "active")),
            created_at=str(payload.get("created_at") or utc_now()),
            updated_at=str(payload.get("updated_at") or utc_now()),
        )


def _validate(record: RankingRecord) -> None:
    if not record.ranking_id or not record.ranking_id.strip():
        raise RankingWriteOwnerError("ranking_id is required")
    if not record.title or not record.title.strip():
        raise RankingWriteOwnerError("title is required")
    if not isinstance(record.entries, list):
        raise RankingWriteOwnerError("entries must be a list")


def _serialized_read(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._write_guard(read_only=True):
            return method(self, *args, **kwargs)

    return wrapper


def _serialized_write(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._write_guard(read_only=False):
            return method(self, *args, **kwargs)

    return wrapper


class RankingWriteStore:
    """Durable JSON-file write owner for Rankings records.

    Cross-process flock serializes read-modify-write cycles, and an
    mtime-gated reload discards any process-local cache before every
    operation, so this store never answers from a stale in-memory snapshot.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._records: Dict[str, RankingRecord] = {}
        self._path = path
        self._loaded_mtime_ns: Optional[int] = None
        self._thread_lock = threading.RLock()
        if path and path.exists():
            self._load(path)
            self._loaded_mtime_ns = path.stat().st_mtime_ns

    @contextmanager
    def _write_guard(self, *, read_only: bool) -> Iterator[None]:
        with self._thread_lock:
            if self._path is None:
                yield
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self._path.with_name(f".{self._path.name}.lock")
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_SH if read_only else fcntl.LOCK_EX)
                self._refresh_from_disk_locked()
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _refresh_from_disk_locked(self) -> None:
        if not self._path or not self._path.exists():
            return
        mtime_ns = self._path.stat().st_mtime_ns
        if self._loaded_mtime_ns == mtime_ns:
            return
        self._records.clear()
        self._load(self._path)
        self._loaded_mtime_ns = mtime_ns

    def _load(self, path: Path) -> None:
        text = path.read_text()
        if not text.strip():
            return
        data = json.loads(text)
        for payload in data.get("rankings", []):
            record = RankingRecord.from_dict(payload)
            self._records[record.ranking_id] = record

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"rankings": [record.to_dict() for record in self._records.values()]}
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary_path = Path(handle.name)
        try:
            with handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._path)
            directory_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        self._loaded_mtime_ns = self._path.stat().st_mtime_ns

    # ---- reads ----

    @_serialized_read
    def get_ranking(self, ranking_id: str) -> Optional[RankingRecord]:
        record = self._records.get(ranking_id)
        return deepcopy(record) if record is not None else None

    @_serialized_read
    def list_rankings(self) -> List[RankingRecord]:
        return [deepcopy(record) for record in self._records.values()]

    # ---- writes ----

    @_serialized_write
    def create_ranking(self, record: RankingRecord) -> RankingRecord:
        _validate(record)
        if record.ranking_id in self._records:
            raise RankingConflictError(f"ranking already exists: {record.ranking_id}")
        self._records[record.ranking_id] = deepcopy(record)
        self._save()
        return deepcopy(self._records[record.ranking_id])

    @_serialized_write
    def put_ranking(self, record: RankingRecord) -> RankingRecord:
        """Create-or-replace. The sole owner-service write entrypoint."""
        _validate(record)
        record.updated_at = utc_now()
        self._records[record.ranking_id] = deepcopy(record)
        self._save()
        return deepcopy(self._records[record.ranking_id])

    @_serialized_write
    def delete_ranking(self, ranking_id: str) -> bool:
        if ranking_id not in self._records:
            return False
        del self._records[ranking_id]
        self._save()
        return True


class PostgresRankingWriteStore(RankingWriteStore):
    """Postgres owner store for Rankings records (staging/prod backend)."""

    def __init__(
        self,
        dsn: str,
        table: str = "rankings.rankings",
        bootstrap: bool = True,
    ) -> None:
        self._records_table = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="rankings-svc",
            bootstrap=bootstrap,
        )
        super().__init__(path=None)
        self._refresh_from_postgres()

    def _refresh_from_postgres(self) -> None:
        self._records = {}
        for payload in self._records_table.list_all():
            record = RankingRecord.from_dict(payload)
            self._records[record.ranking_id] = record

    @_serialized_read
    def get_ranking(self, ranking_id: str) -> Optional[RankingRecord]:
        self._refresh_from_postgres()
        record = self._records.get(ranking_id)
        return deepcopy(record) if record is not None else None

    @_serialized_read
    def list_rankings(self) -> List[RankingRecord]:
        self._refresh_from_postgres()
        return [deepcopy(record) for record in self._records.values()]

    @_serialized_write
    def create_ranking(self, record: RankingRecord) -> RankingRecord:
        self._refresh_from_postgres()
        _validate(record)
        if record.ranking_id in self._records:
            raise RankingConflictError(f"ranking already exists: {record.ranking_id}")
        self._records_table.put(record.ranking_id, record.to_dict())
        self._refresh_from_postgres()
        return deepcopy(self._records[record.ranking_id])

    @_serialized_write
    def put_ranking(self, record: RankingRecord) -> RankingRecord:
        _validate(record)
        record.updated_at = utc_now()
        self._records_table.put(record.ranking_id, record.to_dict())
        self._refresh_from_postgres()
        return deepcopy(self._records[record.ranking_id])

    @_serialized_write
    def delete_ranking(self, ranking_id: str) -> bool:
        self._refresh_from_postgres()
        current = self._records.get(ranking_id)
        if current is None:
            return False
        deleted = self._records_table.delete_if_matches(ranking_id, current.to_dict())
        self._refresh_from_postgres()
        return deleted


def build_rankings_store(path: Optional[Path] = None) -> RankingWriteStore:
    """Select the Rankings write-owner backend from environment posture.

    ``RANKING_STORE_BACKEND=json`` (the default) is the dev/local durable
    JSON-file owner store rooted at ``path``. ``RANKING_STORE_BACKEND=postgres``
    selects the shared-cluster Postgres owner table, matching the
    write-ownership pattern documented in
    ``DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md``.
    """

    backend = (os.getenv("RANKING_STORE_BACKEND") or "json").strip().lower()
    if backend in ("", "json"):
        return RankingWriteStore(path=path)
    if backend != "postgres":
        raise ValueError("RANKING_STORE_BACKEND must be json or postgres")
    dsn = os.getenv("RANKING_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("RANKING_STORE_DSN or DATABASE_URL is required for Postgres ranking store")
    bootstrap = os.getenv("RANKING_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")
    return PostgresRankingWriteStore(
        dsn=dsn,
        table=os.getenv("RANKING_STORE_TABLE", "rankings.rankings"),
        bootstrap=bootstrap,
    )
