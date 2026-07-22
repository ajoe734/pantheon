"""AgoraDatasetStore and extract_evidence — idempotent routing to observe/learn buckets.

All writes are idempotent by evidence_id. Concurrent access is protected by
an RLock or database transaction so multiple BFF worker threads or containers
do not corrupt the store.
"""
from __future__ import annotations

import os
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from uuid import uuid4

from .models import (
    AgoraInteractionEvidenceRequest,
    DatasetKind,
    DatasetRecord,
    InteractionKind,
    route_to_dataset,
)

BACKEND_ENV = "AGORA_DATASET_STORE_BACKEND"
DSN_ENV = "AGORA_DATASET_STORE_DSN"
SCHEMA_ENV = "AGORA_DATASET_STORE_SCHEMA"
DEFAULT_SCHEMA = "agora"

_logger = logging.getLogger(__name__)


class AgoraDatasetStore:
    """Postgres or Memory store for Agora interaction dataset records, durable inbox, and handoffs."""

    def __init__(
        self,
        *,
        backend: Optional[str] = None,
        dsn: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> None:
        self._lock = threading.RLock()
        resolved = (backend or os.getenv(BACKEND_ENV, "off")).strip().lower()
        self.backend = "memory" if resolved in {"", "off", "false", "none", "memory", ":memory:"} else resolved

        # Memory structures
        self._inbox: Dict[str, Dict[str, Any]] = {}
        self._records: Dict[str, DatasetRecord] = {}
        self._handoffs: Dict[str, Dict[str, Any]] = {}

        if self.backend == "memory":
            _logger.info("Agora dataset store initialized backend=memory")
            return

        if self.backend != "postgres":
            raise RuntimeError(f"Unknown {BACKEND_ENV}={resolved!r}; expected off or postgres")

        self.dsn = dsn or os.getenv(DSN_ENV, "") or os.getenv("DATABASE_URL", "")
        if not self.dsn:
            raise RuntimeError(f"{DSN_ENV} or DATABASE_URL must be set when {BACKEND_ENV}=postgres")
        self.schema = schema or os.getenv(SCHEMA_ENV, DEFAULT_SCHEMA)
        
        q = f'"{self.schema}"'
        self._inbox_table = f'{q}."agora_evidence_inbox"'
        self._records_table = f'{q}."agora_dataset_records"'
        self._handoffs_table = f'{q}."agora_evidence_handoffs"'
        self._bootstrap()
        _logger.info("Agora dataset store initialized backend=postgres schema=%s", self.schema)

    def _connect(self) -> Any:
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("psycopg is required for Postgres Agora dataset store") from exc
        return psycopg.connect(self.dsn)

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            try:
                conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            except Exception as exc:
                if getattr(exc, "sqlstate", "") != "42501":
                    raise
                if hasattr(conn, "rollback"):
                    conn.rollback()

            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._inbox_table} (
                    evidence_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    interaction_kind TEXT NOT NULL,
                    persona_id TEXT NOT NULL,
                    session_id TEXT,
                    content JSONB NOT NULL,
                    source_refs JSONB NOT NULL,
                    learning_eligible BOOLEAN NOT NULL,
                    captured_at TEXT NOT NULL,
                    extracted_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error_message TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    processed_at TIMESTAMPTZ,
                    PRIMARY KEY (evidence_id)
                )
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_agora_evidence_inbox_status
                ON {self._inbox_table} (status)
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._records_table} (
                    evidence_id TEXT NOT NULL REFERENCES {self._inbox_table}(evidence_id) ON DELETE CASCADE,
                    dataset_kind TEXT NOT NULL,
                    interaction_kind TEXT NOT NULL,
                    persona_id TEXT NOT NULL,
                    session_id TEXT,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    content JSONB NOT NULL,
                    source_refs JSONB NOT NULL,
                    learning_eligible BOOLEAN NOT NULL,
                    governance_boundary TEXT NOT NULL DEFAULT 'observe_or_learn_only',
                    no_promote_proof TEXT NOT NULL DEFAULT 'agora_observe_learn_only',
                    no_runtime_mutation_proof TEXT NOT NULL DEFAULT 'agora_evidence_extract_only',
                    captured_at TEXT NOT NULL,
                    extracted_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (evidence_id)
                )
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_agora_dataset_records_kind
                ON {self._records_table} (dataset_kind)
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._handoffs_table} (
                    handoff_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    dataset_kind TEXT NOT NULL,
                    evidence_ids JSONB NOT NULL,
                    summary TEXT NOT NULL,
                    authority_limit TEXT NOT NULL DEFAULT 'Observe/Learn',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (handoff_id)
                )
            """)

    def add_to_inbox(
        self,
        evidence: AgoraInteractionEvidenceRequest,
        tenant_id: str,
        user_id: str,
        extracted_at: str,
    ) -> Tuple[Dict[str, Any], bool]:
        """Insert a request into the durable inbox. Idempotent by evidence_id."""
        if self.backend == "memory":
            with self._lock:
                existing = self._inbox.get(evidence.evidence_id)
                if existing is not None:
                    return existing, False
                entry = {
                    "evidence_id": evidence.evidence_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "interaction_kind": evidence.interaction_kind.value,
                    "persona_id": evidence.persona_id,
                    "session_id": evidence.session_id,
                    "content": dict(evidence.content),
                    "source_refs": list(evidence.source_refs),
                    "learning_eligible": evidence.learning_eligible,
                    "captured_at": evidence.captured_at,
                    "extracted_at": extracted_at,
                    "status": "pending",
                    "error_message": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "processed_at": None,
                }
                self._inbox[evidence.evidence_id] = entry
                return entry, True

        with self._connect() as conn:
            with conn.cursor() as cur:
                # 1. Optimistic SELECT first to avoid transaction aborts/slow paths for typical duplicates
                cur.execute(
                    f"SELECT evidence_id, tenant_id, user_id, interaction_kind, persona_id, session_id, "
                    f"content, source_refs, learning_eligible, captured_at, status, error_message, "
                    f"created_at, processed_at, extracted_at FROM {self._inbox_table} WHERE evidence_id = %s",
                    (evidence.evidence_id,)
                )
                row = cur.fetchone()
                if row:
                    entry = {
                        "evidence_id": row[0], "tenant_id": row[1], "user_id": row[2],
                        "interaction_kind": row[3], "persona_id": row[4], "session_id": row[5],
                        "content": row[6], "source_refs": row[7], "learning_eligible": row[8],
                        "captured_at": row[9], "status": row[10], "error_message": row[11],
                        "created_at": row[12].isoformat() if row[12] else None,
                        "processed_at": row[13].isoformat() if row[13] else None,
                        "extracted_at": row[14],
                    }
                    return entry, False

                # 2. INSERT with ON CONFLICT DO NOTHING to prevent TOCTOU race condition
                cur.execute(
                    f"INSERT INTO {self._inbox_table} (evidence_id, tenant_id, user_id, interaction_kind, "
                    f"persona_id, session_id, content, source_refs, learning_eligible, captured_at, extracted_at, status) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending') "
                    f"ON CONFLICT (evidence_id) DO NOTHING RETURNING created_at",
                    (
                        evidence.evidence_id, tenant_id, user_id, evidence.interaction_kind.value,
                        evidence.persona_id, evidence.session_id, json.dumps(evidence.content),
                        json.dumps(evidence.source_refs), evidence.learning_eligible, evidence.captured_at,
                        extracted_at
                    )
                )
                res = cur.fetchone()
                if res is None:
                    # Conflict occurred! Re-select the existing row.
                    cur.execute(
                        f"SELECT evidence_id, tenant_id, user_id, interaction_kind, persona_id, session_id, "
                        f"content, source_refs, learning_eligible, captured_at, status, error_message, "
                        f"created_at, processed_at, extracted_at FROM {self._inbox_table} WHERE evidence_id = %s",
                        (evidence.evidence_id,)
                    )
                    row = cur.fetchone()
                    if row:
                        entry = {
                            "evidence_id": row[0], "tenant_id": row[1], "user_id": row[2],
                            "interaction_kind": row[3], "persona_id": row[4], "session_id": row[5],
                            "content": row[6], "source_refs": row[7], "learning_eligible": row[8],
                            "captured_at": row[9], "status": row[10], "error_message": row[11],
                            "created_at": row[12].isoformat() if row[12] else None,
                            "processed_at": row[13].isoformat() if row[13] else None,
                            "extracted_at": row[14],
                        }
                        return entry, False
                    raise RuntimeError(f"Conflict on evidence_id {evidence.evidence_id} but row could not be re-selected.")

                created_at = res[0]
                entry = {
                    "evidence_id": evidence.evidence_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "interaction_kind": evidence.interaction_kind.value,
                    "persona_id": evidence.persona_id,
                    "session_id": evidence.session_id,
                    "content": dict(evidence.content),
                    "source_refs": list(evidence.source_refs),
                    "learning_eligible": evidence.learning_eligible,
                    "captured_at": evidence.captured_at,
                    "extracted_at": extracted_at,
                    "status": "pending",
                    "error_message": None,
                    "created_at": created_at.isoformat() if created_at else None,
                    "processed_at": None,
                }
                return entry, True

    def process_inbox(self) -> Dict[str, Any]:
        """Default worker processing all pending inbox events.

        Generates versioned dataset records and Observe/Learn evidence handoffs.
        """
        processed_count = 0
        failed_count = 0
        handoffs_created = 0

        if self.backend == "memory":
            with self._lock:
                pending_entries = [e for e in self._inbox.values() if e["status"] == "pending"]
                if not pending_entries:
                    return {"processed": 0, "failed": 0, "handoffs_created": 0}

                # Group by (tenant_id, user_id, dataset_kind)
                groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}

                for entry in pending_entries:
                    try:
                        kind = route_to_dataset(entry["interaction_kind"])
                        # Determine version
                        existing_record = self._records.get(entry["evidence_id"])
                        version = (existing_record.version + 1) if existing_record else 1

                        record = DatasetRecord(
                            evidence_id=entry["evidence_id"],
                            dataset_kind=kind,
                            interaction_kind=InteractionKind(entry["interaction_kind"]),
                            persona_id=entry["persona_id"],
                            session_id=entry["session_id"],
                            tenant_id=entry["tenant_id"],
                            user_id=entry["user_id"],
                            content=entry["content"],
                            source_refs=entry["source_refs"],
                            learning_eligible=entry["learning_eligible"],
                            captured_at=entry["captured_at"],
                            extracted_at=entry["extracted_at"],
                            version=version,
                        )
                        self._records[entry["evidence_id"]] = record
                        entry["status"] = "processed"
                        entry["processed_at"] = datetime.now(timezone.utc).isoformat()
                        processed_count += 1

                        group_key = (entry["tenant_id"], entry["user_id"], kind.value)
                        groups.setdefault(group_key, []).append(entry)

                    except Exception as exc:
                        entry["status"] = "failed"
                        entry["error_message"] = str(exc)
                        entry["processed_at"] = datetime.now(timezone.utc).isoformat()
                        failed_count += 1

                # Generate handoffs
                for (tenant_id, user_id, dataset_kind), entries in groups.items():
                    handoff_id = f"gh-{uuid4().hex[:12]}"
                    handoff = {
                        "handoff_id": handoff_id,
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "dataset_kind": dataset_kind,
                        "evidence_ids": [e["evidence_id"] for e in entries],
                        "summary": f"Durable Agora dataset handoff for {dataset_kind} with {len(entries)} items",
                        "authority_limit": "Observe/Learn",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                    self._handoffs[handoff_id] = handoff
                    handoffs_created += 1

            return {
                "processed": processed_count,
                "failed": failed_count,
                "handoffs_created": handoffs_created,
            }

        # Postgres implementation
        with self._connect() as conn:
            with conn.cursor() as cur:
                # Fetch pending items
                cur.execute(
                    f"SELECT evidence_id, tenant_id, user_id, interaction_kind, persona_id, session_id, "
                    f"content, source_refs, learning_eligible, captured_at, extracted_at FROM {self._inbox_table} "
                    f"WHERE status = 'pending' FOR UPDATE"
                )
                rows = cur.fetchall()
                if not rows:
                    return {"processed": 0, "failed": 0, "handoffs_created": 0}

                groups = {}
                for row in rows:
                    evidence_id, tenant_id, user_id, interaction_kind, persona_id, session_id, content, source_refs, learning_eligible, captured_at, extracted_at = row
                    try:
                        # Validate interaction_kind against InteractionKind enum
                        _ = InteractionKind(interaction_kind)
                        kind = route_to_dataset(interaction_kind)
                        
                        with conn.transaction():
                            # Get version
                            cur.execute(
                                f"SELECT version FROM {self._records_table} WHERE evidence_id = %s",
                                (evidence_id,)
                            )
                            version_row = cur.fetchone()
                            version = (version_row[0] + 1) if version_row else 1

                            # Insert/Update dataset record
                            cur.execute(
                                f"INSERT INTO {self._records_table} (evidence_id, dataset_kind, interaction_kind, "
                                f"persona_id, session_id, tenant_id, user_id, content, source_refs, learning_eligible, "
                                f"captured_at, extracted_at, version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                                f"ON CONFLICT (evidence_id) DO UPDATE SET "
                                f"dataset_kind = EXCLUDED.dataset_kind, interaction_kind = EXCLUDED.interaction_kind, "
                                f"persona_id = EXCLUDED.persona_id, session_id = EXCLUDED.session_id, "
                                f"tenant_id = EXCLUDED.tenant_id, user_id = EXCLUDED.user_id, "
                                f"content = EXCLUDED.content, source_refs = EXCLUDED.source_refs, "
                                f"learning_eligible = EXCLUDED.learning_eligible, "
                                f"captured_at = EXCLUDED.captured_at, extracted_at = EXCLUDED.extracted_at, "
                                f"version = EXCLUDED.version",
                                (
                                    evidence_id, kind.value, interaction_kind, persona_id, session_id,
                                    tenant_id, user_id, json.dumps(content), json.dumps(source_refs),
                                    learning_eligible, captured_at, extracted_at, version
                                )
                            )

                            # Update inbox status
                            cur.execute(
                                f"UPDATE {self._inbox_table} SET status = 'processed', error_message = NULL, "
                                f"processed_at = now() WHERE evidence_id = %s",
                                (evidence_id,)
                            )
                        processed_count += 1

                        group_key = (tenant_id, user_id, kind.value)
                        groups.setdefault(group_key, []).append(evidence_id)

                    except Exception as exc:
                        with conn.transaction():
                            cur.execute(
                                f"UPDATE {self._inbox_table} SET status = 'failed', error_message = %s, "
                                f"processed_at = now() WHERE evidence_id = %s",
                                (str(exc), evidence_id)
                            )
                        failed_count += 1

                # Generate handoffs
                for (tenant_id, user_id, dataset_kind), ids in groups.items():
                    handoff_id = f"gh-{uuid4().hex[:12]}"
                    summary = f"Durable Agora dataset handoff for {dataset_kind} with {len(ids)} items"
                    cur.execute(
                        f"INSERT INTO {self._handoffs_table} (handoff_id, tenant_id, user_id, dataset_kind, "
                        f"evidence_ids, summary, authority_limit) VALUES (%s, %s, %s, %s, %s, %s, 'Observe/Learn')",
                        (handoff_id, tenant_id, user_id, dataset_kind, json.dumps(ids), summary)
                    )
                    handoffs_created += 1

        return {
            "processed": processed_count,
            "failed": failed_count,
            "handoffs_created": handoffs_created,
        }

    def get(self, evidence_id: str) -> Optional[DatasetRecord]:
        """Return a single record or None."""
        if self.backend == "memory":
            with self._lock:
                return self._records.get(evidence_id)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT evidence_id, dataset_kind, interaction_kind, persona_id, session_id, "
                    f"tenant_id, user_id, content, source_refs, learning_eligible, captured_at, "
                    f"extracted_at, version FROM {self._records_table} WHERE evidence_id = %s",
                    (evidence_id,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                return DatasetRecord(
                    evidence_id=row[0],
                    dataset_kind=DatasetKind(row[1]),
                    interaction_kind=InteractionKind(row[2]),
                    persona_id=row[3],
                    session_id=row[4],
                    tenant_id=row[5],
                    user_id=row[6],
                    content=row[7],
                    source_refs=row[8],
                    learning_eligible=row[9],
                    captured_at=row[10],
                    extracted_at=row[11],
                    version=row[12],
                )

    def list_by_dataset(
        self,
        dataset_kind: DatasetKind,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        page_size: int = 50,
    ) -> List[DatasetRecord]:
        """Return records for a given dataset kind, optionally filtered by tenant/user."""
        if self.backend == "memory":
            with self._lock:
                records = [
                    r for r in self._records.values()
                    if r.dataset_kind == dataset_kind
                    and (tenant_id is None or r.tenant_id == tenant_id)
                    and (user_id is None or r.user_id == user_id)
                ]
            return records[:page_size]

        with self._connect() as conn:
            with conn.cursor() as cur:
                sql = f"SELECT evidence_id, dataset_kind, interaction_kind, persona_id, session_id, " \
                      f"tenant_id, user_id, content, source_refs, learning_eligible, captured_at, " \
                      f"extracted_at, version FROM {self._records_table} WHERE dataset_kind = %s"
                params = [dataset_kind.value]
                if tenant_id is not None:
                    sql += " AND tenant_id = %s"
                    params.append(tenant_id)
                if user_id is not None:
                    sql += " AND user_id = %s"
                    params.append(user_id)
                sql += " ORDER BY extracted_at DESC LIMIT %s"
                params.append(page_size)
                
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                records = []
                for row in rows:
                    records.append(
                        DatasetRecord(
                            evidence_id=row[0],
                            dataset_kind=DatasetKind(row[1]),
                            interaction_kind=InteractionKind(row[2]),
                            persona_id=row[3],
                            session_id=row[4],
                            tenant_id=row[5],
                            user_id=row[6],
                            content=row[7],
                            source_refs=row[8],
                            learning_eligible=row[9],
                            captured_at=row[10],
                            extracted_at=row[11],
                            version=row[12],
                        )
                    )
                return records

    def get_inbox_status(self, evidence_id: str) -> Optional[str]:
        if self.backend == "memory":
            with self._lock:
                entry = self._inbox.get(evidence_id)
                return entry["status"] if entry else None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT status FROM {self._inbox_table} WHERE evidence_id = %s", (evidence_id,))
                row = cur.fetchone()
                return row[0] if row else None

    def get_inbox_error(self, evidence_id: str) -> Optional[str]:
        if self.backend == "memory":
            with self._lock:
                entry = self._inbox.get(evidence_id)
                return entry["error_message"] if entry else None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT error_message FROM {self._inbox_table} WHERE evidence_id = %s", (evidence_id,))
                row = cur.fetchone()
                return row[0] if row else None

    def get_backlog(self, tenant_id: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all pending inbox items."""
        if self.backend == "memory":
            with self._lock:
                return [
                    e for e in self._inbox.values()
                    if e["status"] == "pending"
                    and (tenant_id is None or e["tenant_id"] == tenant_id)
                    and (user_id is None or e["user_id"] == user_id)
                ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                sql = f"SELECT evidence_id, tenant_id, user_id, interaction_kind, persona_id, session_id, " \
                      f"content, source_refs, learning_eligible, captured_at, status, created_at " \
                      f"FROM {self._inbox_table} WHERE status = 'pending'"
                params = []
                if tenant_id is not None:
                    sql += " AND tenant_id = %s"
                    params.append(tenant_id)
                if user_id is not None:
                    sql += " AND user_id = %s"
                    params.append(user_id)
                sql += " ORDER BY created_at ASC"
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                return [
                    {
                        "evidence_id": r[0], "tenant_id": r[1], "user_id": r[2], "interaction_kind": r[3],
                        "persona_id": r[4], "session_id": r[5], "content": r[6], "source_refs": r[7],
                        "learning_eligible": r[8], "captured_at": r[9], "status": r[10],
                        "created_at": r[11].isoformat() if r[11] else None
                    }
                    for r in rows
                ]

    def get_dlq(self, tenant_id: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all failed inbox items."""
        if self.backend == "memory":
            with self._lock:
                return [
                    e for e in self._inbox.values()
                    if e["status"] == "failed"
                    and (tenant_id is None or e["tenant_id"] == tenant_id)
                    and (user_id is None or e["user_id"] == user_id)
                ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                sql = f"SELECT evidence_id, tenant_id, user_id, interaction_kind, persona_id, session_id, " \
                      f"content, source_refs, learning_eligible, captured_at, status, error_message, created_at " \
                      f"FROM {self._inbox_table} WHERE status = 'failed'"
                params = []
                if tenant_id is not None:
                    sql += " AND tenant_id = %s"
                    params.append(tenant_id)
                if user_id is not None:
                    sql += " AND user_id = %s"
                    params.append(user_id)
                sql += " ORDER BY created_at ASC"
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                return [
                    {
                        "evidence_id": r[0], "tenant_id": r[1], "user_id": r[2], "interaction_kind": r[3],
                        "persona_id": r[4], "session_id": r[5], "content": r[6], "source_refs": r[7],
                        "learning_eligible": r[8], "captured_at": r[9], "status": r[10], "error_message": r[11],
                        "created_at": r[12].isoformat() if r[12] else None
                    }
                    for r in rows
                ]

    def replay_dlq_item(self, evidence_id: str) -> bool:
        """Reset a failed inbox item back to pending."""
        if self.backend == "memory":
            with self._lock:
                entry = self._inbox.get(evidence_id)
                if entry and entry["status"] == "failed":
                    entry["status"] = "pending"
                    entry["error_message"] = None
                    return True
                return False
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {self._inbox_table} SET status = 'pending', error_message = NULL "
                    f"WHERE evidence_id = %s AND status = 'failed'",
                    (evidence_id,)
                )
                return cur.rowcount > 0

    def list_handoffs(self, *, tenant_id: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return generated handoffs."""
        if self.backend == "memory":
            with self._lock:
                return [
                    h for h in self._handoffs.values()
                    if (tenant_id is None or h["tenant_id"] == tenant_id)
                    and (user_id is None or h["user_id"] == user_id)
                ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                sql = f"SELECT handoff_id, tenant_id, user_id, dataset_kind, evidence_ids, summary, " \
                      f"authority_limit, created_at FROM {self._handoffs_table}"
                params = []
                if tenant_id is not None or user_id is not None:
                    sql += " WHERE 1=1"
                    if tenant_id is not None:
                        sql += " AND tenant_id = %s"
                        params.append(tenant_id)
                    if user_id is not None:
                        sql += " AND user_id = %s"
                        params.append(user_id)
                sql += " ORDER BY created_at DESC"
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
                return [
                    {
                        "handoff_id": r[0], "tenant_id": r[1], "user_id": r[2], "dataset_kind": r[3],
                        "evidence_ids": r[4], "summary": r[5], "authority_limit": r[6],
                        "created_at": r[7].isoformat() if r[7] else None
                    }
                    for r in rows
                ]


def extract_evidence(
    evidence: AgoraInteractionEvidenceRequest,
    *,
    tenant_id: str,
    user_id: str,
    extracted_at: str,
    store: AgoraDatasetStore,
) -> tuple[DatasetRecord, bool]:
    """Extract an evidence record into the appropriate governed dataset.

    Routes evidence to the governed learning dataset:
      observe — ask, journal, note, insight (passive observation)
      learn   — feedback, training_example (supervised signal)

    Processes via the durable inbox and updates datasets/handoffs.
    """
    inbox_entry, is_new = store.add_to_inbox(evidence, tenant_id, user_id, extracted_at)
    
    # Synchronously run the worker to process the inbox for immediate consistency
    store.process_inbox()

    record = store.get(evidence.evidence_id)
    if record is None:
        if store.get_inbox_status(evidence.evidence_id) == "failed":
            error_msg = store.get_inbox_error(evidence.evidence_id)
            raise ValueError(f"Failed to process evidence in inbox: {error_msg}")
        raise ValueError(f"Evidence record {evidence.evidence_id} not processed yet")

    if not is_new:
        record = record.model_copy(update={"idempotent": True})

    return record, is_new


__all__ = ["AgoraDatasetStore", "extract_evidence"]
