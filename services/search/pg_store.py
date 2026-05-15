"""Optional Postgres-backed stores for search service.

Enabled by env; JSONL remains the default.  Search is the write owner
of search snapshots.  Source evidence is read-only for search — write
ownership stays with source-ingest.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from services.knowledge.evidence import (
    EvidenceBundle,
    EvidenceItem,
    InMemoryEvidenceRepository,
    JsonlEvidenceRepository,
    KnowledgeObject,
)
from services.knowledge.evidence.models import EvidenceValidationError
from services.source_ingestion.connectors.base import SourceRecord

from .index_store import JsonlSearchIndexStore, SearchIndexSnapshot

_PG_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_EVIDENCE_LOADERS: Dict[str, Any] = {
    "source_record": SourceRecord.from_dict,
    "evidence_item": EvidenceItem.from_dict,
    "evidence_bundle": EvidenceBundle.from_dict,
    "knowledge_object": KnowledgeObject.from_dict,
}


def _quote_pg(identifier: str) -> str:
    parts = identifier.split(".")
    if not parts or any(_PG_IDENTIFIER_RE.fullmatch(part) is None for part in parts):
        raise ValueError(f"Invalid Postgres identifier: {identifier}")
    return ".".join(f'"{part}"' for part in parts)


class PostgresReadOnlyEvidenceRepository(InMemoryEvidenceRepository):
    """Read-only Postgres view of source evidence for search.

    Source-ingest is the write owner.  This class never writes to the
    source_evidence table; it only reads on reload.
    """

    def __init__(
        self,
        dsn: str,
        table: str = "source_ingest.source_evidence",
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for PostgresReadOnlyEvidenceRepository")
        self.dsn = dsn
        self.table = _quote_pg(table)
        self.path = f"postgres:{table}"
        super().__init__()
        self.reload()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required when SEARCH_EVIDENCE_BACKEND=postgres"
            ) from exc
        return psycopg.connect(self.dsn)

    def reload(self) -> None:
        self._source_records.clear()
        self._evidence_items.clear()
        self._bundles.clear()
        self._knowledge_objects.clear()
        with self._connect() as conn:
            cursor = conn.execute(
                f"SELECT record_type, payload FROM {self.table} ORDER BY append_id ASC"
            )
            rows = cursor.fetchall()
        for row in rows:
            record_type = row[0] if isinstance(row, tuple) else row.get("record_type")
            payload = row[1] if isinstance(row, tuple) else row.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            loader = _EVIDENCE_LOADERS.get(str(record_type or ""))
            if loader is None or not isinstance(payload, dict):
                raise EvidenceValidationError(
                    f"Unsupported source evidence record type from Postgres: {record_type!r}"
                )
            obj = loader(payload)
            if isinstance(obj, SourceRecord):
                InMemoryEvidenceRepository.add_source_record(self, obj)
            elif isinstance(obj, EvidenceItem):
                InMemoryEvidenceRepository.add_evidence_item(self, obj)
            elif isinstance(obj, EvidenceBundle):
                InMemoryEvidenceRepository.add_bundle(self, obj)
            elif isinstance(obj, KnowledgeObject):
                InMemoryEvidenceRepository.add_knowledge_object(self, obj)

    def add_source_record(self, source: SourceRecord) -> SourceRecord:
        raise EvidenceValidationError(
            "PostgresReadOnlyEvidenceRepository is read-only; source-ingest is the write owner"
        )

    def add_evidence_item(self, item: EvidenceItem) -> EvidenceItem:
        raise EvidenceValidationError(
            "PostgresReadOnlyEvidenceRepository is read-only; source-ingest is the write owner"
        )

    def add_bundle(self, bundle: EvidenceBundle) -> EvidenceBundle:
        raise EvidenceValidationError(
            "PostgresReadOnlyEvidenceRepository is read-only; source-ingest is the write owner"
        )

    def add_knowledge_object(self, knowledge_object: KnowledgeObject) -> KnowledgeObject:
        raise EvidenceValidationError(
            "PostgresReadOnlyEvidenceRepository is read-only; source-ingest is the write owner"
        )


class PostgresSearchIndexStore:
    """Postgres-backed search snapshot store.

    Write owner: search-svc.  Append semantics match JsonlSearchIndexStore:
    the latest upsert per request_id wins on read.
    """

    def __init__(
        self,
        dsn: str,
        table: str = "search_svc.search_index_snapshots",
        bootstrap: bool = True,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for PostgresSearchIndexStore")
        self.dsn = dsn
        self.table = _quote_pg(table)
        self.schema = table.split(".", 1)[0] if "." in table else ""
        self.path = f"postgres:{table}"
        self._snapshots: Dict[str, SearchIndexSnapshot] = {}
        if bootstrap:
            self._bootstrap()
        self.reload()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required when SEARCH_INDEX_STORE_BACKEND=postgres"
            ) from exc
        return psycopg.connect(self.dsn)

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            if self.schema:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_pg(self.schema)}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    append_id  BIGSERIAL PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    payload    JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    def reload(self) -> None:
        self._snapshots = {}
        with self._connect() as conn:
            cursor = conn.execute(
                f"SELECT request_id, payload FROM {self.table} ORDER BY append_id ASC"
            )
            rows = cursor.fetchall()
        for row in rows:
            request_id = row[0] if isinstance(row, tuple) else row.get("request_id")
            payload = row[1] if isinstance(row, tuple) else row.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                snapshot = SearchIndexSnapshot.from_dict(payload)
                self._snapshots[snapshot.request_id] = snapshot

    def append_snapshot(self, snapshot: SearchIndexSnapshot) -> SearchIndexSnapshot:
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table} (request_id, payload)
                VALUES (%s, %s::jsonb)
                """,
                (
                    snapshot.request_id,
                    json.dumps(snapshot.to_dict(), ensure_ascii=True, sort_keys=True),
                ),
            )
        self._snapshots[snapshot.request_id] = snapshot
        return snapshot

    def get_snapshot(self, request_id: str) -> SearchIndexSnapshot | None:
        return self._snapshots.get(request_id)

    def list_snapshots(self) -> List[SearchIndexSnapshot]:
        return list(self._snapshots.values())


def build_search_index_store(jsonl_path: Path) -> JsonlSearchIndexStore | PostgresSearchIndexStore:
    """Factory: returns JsonlSearchIndexStore unless SEARCH_INDEX_STORE_BACKEND=postgres."""
    backend = os.getenv("SEARCH_INDEX_STORE_BACKEND", "jsonl").strip().lower()
    if backend in ("", "jsonl"):
        return JsonlSearchIndexStore(jsonl_path)
    if backend != "postgres":
        raise ValueError("SEARCH_INDEX_STORE_BACKEND must be jsonl or postgres")
    dsn = os.getenv("SEARCH_INDEX_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError(
            "SEARCH_INDEX_STORE_DSN or DATABASE_URL is required for Postgres search index store"
        )
    table = os.getenv("SEARCH_INDEX_STORE_TABLE", "search_svc.search_index_snapshots")
    bootstrap = (
        os.getenv("SEARCH_INDEX_STORE_BOOTSTRAP", "1").strip().lower()
        not in ("0", "false", "no")
    )
    return PostgresSearchIndexStore(dsn=dsn, table=table, bootstrap=bootstrap)


def build_search_evidence_repository(
    jsonl_path: Path,
) -> InMemoryEvidenceRepository:
    """Factory: returns JsonlEvidenceRepository unless SEARCH_EVIDENCE_BACKEND=postgres.

    In Postgres mode, returns a read-only view of source-ingest's evidence table.
    Source-ingest remains the sole write owner.
    """
    backend = os.getenv("SEARCH_EVIDENCE_BACKEND", "jsonl").strip().lower()
    if backend in ("", "jsonl"):
        return JsonlEvidenceRepository(jsonl_path)
    if backend != "postgres":
        raise ValueError("SEARCH_EVIDENCE_BACKEND must be jsonl or postgres")
    dsn = os.getenv("SEARCH_EVIDENCE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError(
            "SEARCH_EVIDENCE_DSN or DATABASE_URL is required for Postgres evidence store"
        )
    table = os.getenv("SEARCH_EVIDENCE_TABLE", "source_ingest.source_evidence")
    return PostgresReadOnlyEvidenceRepository(dsn=dsn, table=table)
