"""Optional Postgres-backed evidence store for source-ingest.

Enabled by env; JSONL remains the default.  Source-ingest is the sole
write owner of the source_evidence table.  Other services must read
through the owner API or a read-only database role.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict

from services.knowledge.evidence import (
    EvidenceBundle,
    EvidenceItem,
    InMemoryEvidenceRepository,
    JsonlEvidenceRepository,
    KnowledgeObject,
)
from services.knowledge.evidence.models import EvidenceValidationError
from services.source_ingestion.connectors.base import SourceRecord

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


class PostgresSourceEvidenceRepository(InMemoryEvidenceRepository):
    """Postgres-backed source evidence store for source-ingest.

    Write owner: source-ingest-svc.  Non-owner services must use the
    owner API or a read-only DB role — never write directly.
    """

    def __init__(
        self,
        dsn: str,
        table: str = "source_ingest.source_evidence",
        bootstrap: bool = True,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for PostgresSourceEvidenceRepository")
        self.dsn = dsn
        self.table = _quote_pg(table)
        self.schema = table.split(".", 1)[0] if "." in table else ""
        self.path = f"postgres:{table}"
        super().__init__()
        if bootstrap:
            self._bootstrap()
        self.reload()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required when SOURCE_INGEST_EVIDENCE_BACKEND=postgres"
            ) from exc
        return psycopg.connect(self.dsn)

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            if self.schema:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_pg(self.schema)}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    append_id   BIGSERIAL PRIMARY KEY,
                    record_id   TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    payload     JSONB NOT NULL,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (record_type, record_id)
                )
                """
            )

    def reload(self) -> None:
        self._source_records.clear()
        self._source_dedupe_index.clear()
        self._evidence_items.clear()
        self._evidence_dedupe_index.clear()
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

    def _upsert(self, record_type: str, record_id: str, payload: Dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table} (record_id, record_type, payload)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (record_type, record_id)
                DO UPDATE SET payload = EXCLUDED.payload
                """,
                (
                    record_id,
                    record_type,
                    json.dumps(payload, ensure_ascii=True, sort_keys=True),
                ),
            )

    def add_source_record(self, source: SourceRecord) -> SourceRecord:
        stored = super().add_source_record(source)
        if stored.source_id == source.source_id:
            self._upsert("source_record", stored.source_id, stored.to_dict())
        return stored

    def add_evidence_item(self, item: EvidenceItem) -> EvidenceItem:
        stored = super().add_evidence_item(item)
        if stored.evidence_item_id == item.evidence_item_id:
            self._upsert("evidence_item", stored.evidence_item_id, stored.to_dict())
        return stored

    def add_bundle(self, bundle: EvidenceBundle) -> EvidenceBundle:
        stored = super().add_bundle(bundle)
        self._upsert("evidence_bundle", stored.evidence_bundle_id, stored.to_dict())
        return stored

    def add_knowledge_object(self, knowledge_object: KnowledgeObject) -> KnowledgeObject:
        stored = super().add_knowledge_object(knowledge_object)
        self._upsert("knowledge_object", stored.knowledge_object_id, stored.to_dict())
        return stored


def build_source_evidence_repository(
    jsonl_path: Path,
) -> InMemoryEvidenceRepository:
    """Factory: returns JsonlEvidenceRepository unless SOURCE_INGEST_EVIDENCE_BACKEND=postgres."""
    backend = os.getenv("SOURCE_INGEST_EVIDENCE_BACKEND", "jsonl").strip().lower()
    if backend in ("", "jsonl"):
        return JsonlEvidenceRepository(jsonl_path)
    if backend != "postgres":
        raise ValueError("SOURCE_INGEST_EVIDENCE_BACKEND must be jsonl or postgres")
    dsn = os.getenv("SOURCE_INGEST_EVIDENCE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError(
            "SOURCE_INGEST_EVIDENCE_DSN or DATABASE_URL is required for Postgres evidence store"
        )
    table = os.getenv("SOURCE_INGEST_EVIDENCE_TABLE", "source_ingest.source_evidence")
    bootstrap = (
        os.getenv("SOURCE_INGEST_EVIDENCE_BOOTSTRAP", "1").strip().lower()
        not in ("0", "false", "no")
    )
    return PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=bootstrap)
