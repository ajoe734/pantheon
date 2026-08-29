"""Independent persistent write owner for the Rankings domain.

Write authority: Rankings domain only (rankings-svc). This module is the
sole write path for ranking records and deliberately does not import
``services/control-plane/bff/read_store.py``: that module's ranking helpers
keep an in-process local overlay dict as a response fallback, which is not a
durable write path and does not survive process restart or a second reader
process.

Generation 2 narrows this store to a single concrete backend:
``PostgresJsonOwnerStore`` (see
``DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md``). Generation 1 shipped a
JSON-file backend and a Postgres backend side by side, selected by an
environment variable -- two persistence implementations for the same
domain, which duplicates the durability guarantee this module exists to
provide instead of concentrating it in one place. There is exactly one
storage implementation now: every read re-runs a fresh ``SELECT`` against
the owner table, so a ``get``/``list`` immediately observes a write
committed by a different store instance, including one in a different
process.

Source Ingestion remains reconcile-only for this domain: it may read the
durable rankings state to reconcile, but it is not a write owner and must
not call the write methods below.
"""
from __future__ import annotations

import os
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

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


class RankingWriteStore:
    """The sole durable write owner for Rankings records.

    Backed by one concrete implementation, ``PostgresJsonOwnerStore``. Every
    method re-reads the backing table before answering, so this store never
    returns a stale in-memory snapshot and a write is immediately visible to
    any other store instance pointed at the same table.
    """

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
        self._thread_lock = threading.RLock()

    def _refresh(self) -> Dict[str, RankingRecord]:
        records: Dict[str, RankingRecord] = {}
        for payload in self._records_table.list_all():
            record = RankingRecord.from_dict(payload)
            records[record.ranking_id] = record
        return records

    # ---- reads ----

    def get_ranking(self, ranking_id: str) -> Optional[RankingRecord]:
        with self._thread_lock:
            record = self._refresh().get(ranking_id)
            return deepcopy(record) if record is not None else None

    def list_rankings(self) -> List[RankingRecord]:
        with self._thread_lock:
            return [deepcopy(record) for record in self._refresh().values()]

    # ---- writes ----

    def create_ranking(self, record: RankingRecord) -> RankingRecord:
        """Insert a brand-new ranking; atomic against concurrent creators."""

        with self._thread_lock:
            _validate(record)
            created, canonical = self._records_table.compare_and_set(
                record.ranking_id, None, record.to_dict()
            )
            if not created:
                raise RankingConflictError(f"ranking already exists: {record.ranking_id}")
            return RankingRecord.from_dict(canonical)

    def put_ranking(self, record: RankingRecord) -> RankingRecord:
        """Create-or-replace. The sole owner-service write entrypoint."""

        with self._thread_lock:
            _validate(record)
            record.updated_at = utc_now()
            self._records_table.put(record.ranking_id, record.to_dict())
            return deepcopy(record)

    def delete_ranking(self, ranking_id: str) -> bool:
        with self._thread_lock:
            current = self._refresh().get(ranking_id)
            if current is None:
                return False
            return self._records_table.delete_if_matches(ranking_id, current.to_dict())


def build_rankings_store(
    dsn: Optional[str] = None,
    table: Optional[str] = None,
    bootstrap: Optional[bool] = None,
) -> RankingWriteStore:
    """Build the Rankings write-owner store.

    ``PostgresJsonOwnerStore`` is the only backend, matching the
    write-ownership pattern documented in
    ``DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md``. The DSN, table, and
    bootstrap flag may be passed explicitly or resolved from the environment.
    """

    resolved_dsn = dsn or os.getenv("RANKING_STORE_DSN") or os.getenv("DATABASE_URL")
    if not resolved_dsn:
        raise ValueError("RANKING_STORE_DSN or DATABASE_URL is required for the Rankings write-owner store")
    resolved_table = table or os.getenv("RANKING_STORE_TABLE", "rankings.rankings")
    if bootstrap is None:
        bootstrap = os.getenv("RANKING_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")
    return RankingWriteStore(dsn=resolved_dsn, table=resolved_table, bootstrap=bootstrap)
