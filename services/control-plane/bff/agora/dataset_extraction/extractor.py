"""Governed, tenant-scoped Agora dataset extraction.

The durable inbox is the write owner for Observe/Learn extraction.
Evidence admission is admit-only: requests are atomically persisted into the
inbox without running extraction workers inline.
A separately leased worker validates consent, purpose, retention, and redaction,
creates immutable DatasetVersion records, and publishes durable handoffs.
Downstream acknowledgement can only close that specific handoff.
This module has no RuntimeBinding, deployment, capital, broker, or order authority.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import uuid4

from .models import (
    AgoraInteractionEvidenceRequest,
    DatasetAdmissionReceipt,
    DatasetKind,
    DatasetRecord,
    InteractionKind,
    route_to_dataset,
)

BACKEND_ENV = "AGORA_DATASET_STORE_BACKEND"
DSN_ENV = "AGORA_DATASET_STORE_DSN"
SCHEMA_ENV = "AGORA_DATASET_STORE_SCHEMA"
DEFAULT_SCHEMA = "agora"
DEFAULT_BATCH_SIZE = 25
MAX_BATCH_SIZE = 100
DEFAULT_LEASE_SECONDS = 30
MAX_LEASE_SECONDS = 300
MAX_ATTEMPTS = 5

_logger = logging.getLogger(__name__)


class IdempotencyConflictError(ValueError):
    """The same scoped idempotency key or evidence id has a new digest."""


class ClaimConflictError(RuntimeError):
    """A worker attempted to complete work after losing its lease."""


class HandoffConflictError(ValueError):
    """A handoff acknowledgement does not match its durable dataset version."""


class PrivacyConsentError(ValueError):
    """Evidence fails privacy, consent, purpose, or redaction requirements."""


class DatasetEligibilityError(ValueError):
    """Evidence is ineligible for dataset extraction."""


_SENSITIVE_KEY_SUBSTRINGS = {
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "cookie",
    "credit_card",
    "ssn",
    "private_key",
}

_RAW_CONVERSATION_KEYS = {
    "raw_transcript",
    "raw_conversation",
    "raw_messages",
    "private_messages",
    "transcript_full",
    "chat_history_raw",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def evidence_request_digest(evidence: AgoraInteractionEvidenceRequest) -> str:
    """Return the stable semantic digest bound to ``Idempotency-Key``."""

    payload = {
        "route": "POST /bff/agora/interaction-evidence",
        "payload": evidence.model_dump(mode="json"),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def acknowledgement_request_digest(
    *,
    handoff_id: str,
    acknowledgement_id: str,
    dataset_version_id: str,
    downstream_ref: Any,
) -> str:
    payload = {
        "route": f"POST /bff/agora/dataset-worker/handoffs/{handoff_id}/ack",
        "payload": {
            "acknowledgement_id": acknowledgement_id,
            "dataset_version_id": dataset_version_id,
            "downstream_ref": downstream_ref,
        },
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _utc_datetime(value: Optional[datetime] = None) -> datetime:
    resolved = value or datetime.now(timezone.utc)
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc_datetime(value).isoformat().replace("+00:00", "Z")


def _dataset_version_id(
    *,
    tenant_id: str,
    user_id: str,
    evidence_id: str,
    request_digest: str,
) -> str:
    material = "\0".join((tenant_id, user_id, evidence_id, request_digest, "1"))
    return f"dsv-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _handoff_id(*, tenant_id: str, user_id: str, dataset_version_id: str) -> str:
    material = "\0".join((tenant_id, user_id, dataset_version_id))
    return f"gh-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _scope_key(tenant_id: str, user_id: str, resource_id: str) -> Tuple[str, str, str]:
    return (str(tenant_id), str(user_id), str(resource_id))


def sanitize_content_payload(
    content: Any,
    *,
    is_raw_conversation: bool = False,
    explicit_conversation_consent: bool = False,
) -> Tuple[Any, bool]:
    """Recursively redact sensitive tokens and enforce raw conversation exclusion.

    Returns (sanitized_content, redaction_applied).
    Raises PrivacyConsentError if raw conversation is included without explicit consent.
    """
    redaction_applied = False

    def _sanitize(item: Any) -> Any:
        nonlocal redaction_applied
        if isinstance(item, dict):
            new_dict = {}
            for k, v in item.items():
                k_lower = str(k).lower()
                if k_lower in _RAW_CONVERSATION_KEYS and not explicit_conversation_consent:
                    raise PrivacyConsentError(
                        "Raw private conversation excluded without explicit consent and minimization"
                    )
                if any(sub in k_lower for sub in _SENSITIVE_KEY_SUBSTRINGS):
                    redaction_applied = True
                    new_dict[k] = "[REDACTED]"
                else:
                    new_dict[k] = _sanitize(v)
            return new_dict
        elif isinstance(item, list):
            return [_sanitize(x) for x in item]
        return item

    if is_raw_conversation and not explicit_conversation_consent:
        raise PrivacyConsentError(
            "Raw private conversation excluded without explicit consent and minimization"
        )

    sanitized = _sanitize(content)
    return sanitized, redaction_applied


class AgoraDatasetStore:
    """Postgres or memory owner for inbox claims, dataset versions, and handoffs."""

    def __init__(
        self,
        *,
        backend: Optional[str] = None,
        dsn: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> None:
        self._lock = threading.RLock()
        resolved = (backend or os.getenv(BACKEND_ENV, "off")).strip().lower()
        self.backend = (
            "memory"
            if resolved in {"", "off", "false", "none", "memory", ":memory:"}
            else resolved
        )

        self._inbox: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self._records: Dict[Tuple[str, str, str], DatasetRecord] = {}
        self._handoffs: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

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
        """Create the v2 scoped schema and migrate prior tables."""

        with self._connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("agora_dataset_extraction_v2",))
            try:
                conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            except Exception as exc:
                if getattr(exc, "sqlstate", "") != "42501":
                    raise
                conn.rollback()
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    ("agora_dataset_extraction_v2",),
                )

            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._inbox_table} (
                    evidence_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
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
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_expires_at TIMESTAMPTZ,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    consent_granted BOOLEAN NOT NULL DEFAULT true,
                    purpose TEXT NOT NULL DEFAULT 'policy_learning',
                    retention_days INTEGER NOT NULL DEFAULT 30,
                    is_raw_conversation BOOLEAN NOT NULL DEFAULT false,
                    explicit_conversation_consent BOOLEAN NOT NULL DEFAULT false,
                    admission_receipt_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    processed_at TIMESTAMPTZ,
                    PRIMARY KEY (tenant_id, user_id, evidence_id),
                    UNIQUE (tenant_id, user_id, idempotency_key)
                )
                """
            )
            # ADD COLUMN keeps existing deployments forward-compatible.
            for ddl in (
                "ADD COLUMN IF NOT EXISTS idempotency_key TEXT",
                "ADD COLUMN IF NOT EXISTS request_digest TEXT",
                "ADD COLUMN IF NOT EXISTS lease_owner TEXT",
                "ADD COLUMN IF NOT EXISTS lease_token TEXT",
                "ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ",
                "ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0",
                "ADD COLUMN IF NOT EXISTS consent_granted BOOLEAN NOT NULL DEFAULT true",
                "ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'policy_learning'",
                "ADD COLUMN IF NOT EXISTS retention_days INTEGER NOT NULL DEFAULT 30",
                "ADD COLUMN IF NOT EXISTS is_raw_conversation BOOLEAN NOT NULL DEFAULT false",
                "ADD COLUMN IF NOT EXISTS explicit_conversation_consent BOOLEAN NOT NULL DEFAULT false",
                "ADD COLUMN IF NOT EXISTS admission_receipt_id TEXT",
            ):
                conn.execute(f"ALTER TABLE {self._inbox_table} {ddl}")
            conn.execute(
                f"UPDATE {self._inbox_table} "
                "SET idempotency_key = COALESCE(NULLIF(idempotency_key, ''), 'legacy:' || evidence_id), "
                "request_digest = COALESCE(NULLIF(request_digest, ''), 'legacy:' || evidence_id)"
            )
            conn.execute(
                f"ALTER TABLE {self._inbox_table} "
                "ALTER COLUMN idempotency_key SET NOT NULL, "
                "ALTER COLUMN request_digest SET NOT NULL"
            )

            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._records_table} (
                    evidence_id TEXT NOT NULL,
                    dataset_version_id TEXT NOT NULL,
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
                    consent_verified BOOLEAN NOT NULL DEFAULT true,
                    redaction_applied BOOLEAN NOT NULL DEFAULT false,
                    purpose TEXT,
                    retention_days INTEGER,
                    admission_receipt_id TEXT,
                    PRIMARY KEY (tenant_id, user_id, evidence_id),
                    UNIQUE (tenant_id, user_id, dataset_version_id)
                )
                """
            )
            for ddl in (
                "ADD COLUMN IF NOT EXISTS dataset_version_id TEXT",
                "ADD COLUMN IF NOT EXISTS consent_verified BOOLEAN NOT NULL DEFAULT true",
                "ADD COLUMN IF NOT EXISTS redaction_applied BOOLEAN NOT NULL DEFAULT false",
                "ADD COLUMN IF NOT EXISTS purpose TEXT",
                "ADD COLUMN IF NOT EXISTS retention_days INTEGER",
                "ADD COLUMN IF NOT EXISTS admission_receipt_id TEXT",
            ):
                conn.execute(f"ALTER TABLE {self._records_table} {ddl}")
            conn.execute(
                f"UPDATE {self._records_table} SET dataset_version_id = "
                "COALESCE(NULLIF(dataset_version_id, ''), "
                "'dsv-' || substr(md5(tenant_id || '|' || user_id || '|' || evidence_id), 1, 24))"
            )
            conn.execute(
                f"ALTER TABLE {self._records_table} "
                "ALTER COLUMN dataset_version_id SET NOT NULL"
            )

            self._ensure_scoped_primary_keys(conn)

            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._handoffs_table} (
                    handoff_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    dataset_version_id TEXT NOT NULL,
                    dataset_kind TEXT NOT NULL,
                    evidence_ids JSONB NOT NULL,
                    summary TEXT NOT NULL,
                    authority_limit TEXT NOT NULL DEFAULT 'Observe/Learn',
                    ack_status TEXT NOT NULL DEFAULT 'pending',
                    acknowledgement_id TEXT,
                    ack_request_digest TEXT,
                    downstream_ref JSONB,
                    acknowledged_by TEXT,
                    acknowledged_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, user_id, handoff_id),
                    UNIQUE (tenant_id, user_id, dataset_version_id)
                )
                """
            )
            for ddl in (
                "ADD COLUMN IF NOT EXISTS dataset_version_id TEXT",
                "ADD COLUMN IF NOT EXISTS ack_status TEXT NOT NULL DEFAULT 'pending'",
                "ADD COLUMN IF NOT EXISTS acknowledgement_id TEXT",
                "ADD COLUMN IF NOT EXISTS ack_request_digest TEXT",
                "ADD COLUMN IF NOT EXISTS downstream_ref JSONB",
                "ADD COLUMN IF NOT EXISTS acknowledged_by TEXT",
                "ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ",
            ):
                conn.execute(f"ALTER TABLE {self._handoffs_table} {ddl}")
            conn.execute(
                f"UPDATE {self._handoffs_table} SET dataset_version_id = "
                "COALESCE(NULLIF(dataset_version_id, ''), "
                "'legacy-' || substr(md5(tenant_id || '|' || user_id || '|' || handoff_id), 1, 24))"
            )
            conn.execute(
                f"ALTER TABLE {self._handoffs_table} "
                "ALTER COLUMN dataset_version_id SET NOT NULL"
            )
            self._ensure_handoff_primary_key(conn)

            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_agora_inbox_scope_idempotency "
                f"ON {self._inbox_table} (tenant_id, user_id, idempotency_key)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_agora_inbox_claim "
                f"ON {self._inbox_table} "
                f"(tenant_id, user_id, status, lease_expires_at, created_at)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_agora_records_scope_kind "
                f"ON {self._records_table} (tenant_id, user_id, dataset_kind, extracted_at DESC)"
            )
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_agora_handoff_scope_version "
                f"ON {self._handoffs_table} (tenant_id, user_id, dataset_version_id)"
            )

    @staticmethod
    def _constraint_names(conn: Any, table: str, constraint_type: str) -> List[str]:
        rows = conn.execute(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = %s::regclass AND contype = %s ORDER BY conname",
            (table, constraint_type),
        ).fetchall()
        return [str(row[0]) for row in rows]

    @staticmethod
    def _drop_constraint(conn: Any, table: str, name: str) -> None:
        quoted = name.replace('"', '""')
        conn.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{quoted}"')

    @staticmethod
    def _primary_key_columns(conn: Any, table: str) -> List[str]:
        rows = conn.execute(
            """
            SELECT attribute.attname
            FROM pg_constraint constraint_row
            CROSS JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY AS key(attnum, ord)
            JOIN pg_attribute attribute
              ON attribute.attrelid = constraint_row.conrelid
             AND attribute.attnum = key.attnum
            WHERE constraint_row.conrelid = %s::regclass
              AND constraint_row.contype = 'p'
            ORDER BY key.ord
            """,
            (table,),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def _ensure_scoped_primary_keys(self, conn: Any) -> None:
        desired = ["tenant_id", "user_id", "evidence_id"]
        records_pk = self._primary_key_columns(conn, self._records_table)
        inbox_pk = self._primary_key_columns(conn, self._inbox_table)
        if records_pk == desired and inbox_pk == desired:
            return

        for name in self._constraint_names(conn, self._records_table, "f"):
            self._drop_constraint(conn, self._records_table, name)
        for table, columns in (
            (self._records_table, records_pk),
            (self._inbox_table, inbox_pk),
        ):
            if columns:
                for name in self._constraint_names(conn, table, "p"):
                    self._drop_constraint(conn, table, name)
            conn.execute(
                f"ALTER TABLE {table} "
                "ADD PRIMARY KEY (tenant_id, user_id, evidence_id)"
            )

        conn.execute(
            f"ALTER TABLE {self._records_table} ADD CONSTRAINT "
            f'"fk_agora_records_scoped_inbox" FOREIGN KEY (tenant_id, user_id, evidence_id) '
            f"REFERENCES {self._inbox_table} (tenant_id, user_id, evidence_id) ON DELETE CASCADE"
        )

    def _ensure_handoff_primary_key(self, conn: Any) -> None:
        desired = ["tenant_id", "user_id", "handoff_id"]
        if self._primary_key_columns(conn, self._handoffs_table) == desired:
            return
        for name in self._constraint_names(conn, self._handoffs_table, "p"):
            self._drop_constraint(conn, self._handoffs_table, name)
        conn.execute(
            f"ALTER TABLE {self._handoffs_table} "
            "ADD PRIMARY KEY (tenant_id, user_id, handoff_id)"
        )

    @staticmethod
    def _entry(
        evidence: AgoraInteractionEvidenceRequest,
        *,
        tenant_id: str,
        user_id: str,
        extracted_at: str,
        idempotency_key: str,
        request_digest: str,
    ) -> Dict[str, Any]:
        receipt_id = f"rcpt-adm-{evidence.evidence_id}"
        return {
            "evidence_id": evidence.evidence_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "idempotency_key": idempotency_key,
            "request_digest": request_digest,
            "interaction_kind": evidence.interaction_kind.value,
            "persona_id": evidence.persona_id,
            "session_id": evidence.session_id,
            "content": dict(evidence.content),
            "source_refs": list(evidence.source_refs),
            "learning_eligible": evidence.learning_eligible,
            "consent_granted": evidence.consent_granted,
            "purpose": evidence.purpose or "policy_learning",
            "retention_days": evidence.retention_days if evidence.retention_days is not None else 30,
            "is_raw_conversation": evidence.is_raw_conversation,
            "explicit_conversation_consent": evidence.explicit_conversation_consent,
            "admission_receipt_id": receipt_id,
            "captured_at": evidence.captured_at,
            "extracted_at": extracted_at,
            "status": "pending",
            "error_message": None,
            "lease_owner": None,
            "lease_token": None,
            "lease_expires_at": None,
            "attempt_count": 0,
            "created_at": _iso(_utc_datetime()),
            "processed_at": None,
        }

    @staticmethod
    def _validate_existing(
        existing: Dict[str, Any],
        *,
        idempotency_key: str,
        request_digest: str,
    ) -> None:
        same_key = existing.get("idempotency_key") == idempotency_key
        same_digest = existing.get("request_digest") == request_digest
        if same_key and same_digest:
            return
        if same_key:
            raise IdempotencyConflictError(
                "Idempotency-Key is already bound to a different Agora evidence payload"
            )
        if not same_digest:
            raise IdempotencyConflictError(
                "evidence_id is already bound to a different Agora evidence payload"
            )

    def add_to_inbox(
        self,
        evidence: AgoraInteractionEvidenceRequest,
        tenant_id: str,
        user_id: str,
        extracted_at: str,
        *,
        idempotency_key: str,
        request_digest: str,
    ) -> Tuple[Dict[str, Any], bool]:
        """Insert one scoped request, enforcing key and evidence digest binding."""

        tenant_id = str(tenant_id).strip()
        user_id = str(user_id).strip()
        idempotency_key = str(idempotency_key).strip()
        request_digest = str(request_digest).strip()
        if not tenant_id or not user_id or not idempotency_key or not request_digest:
            raise ValueError("tenant_id, user_id, idempotency_key, and request_digest are required")

        key = _scope_key(tenant_id, user_id, evidence.evidence_id)
        if self.backend == "memory":
            with self._lock:
                for existing in self._inbox.values():
                    if (
                        existing["tenant_id"] == tenant_id
                        and existing["user_id"] == user_id
                        and existing["idempotency_key"] == idempotency_key
                    ):
                        self._validate_existing(
                            existing,
                            idempotency_key=idempotency_key,
                            request_digest=request_digest,
                        )
                        return dict(existing), False
                existing = self._inbox.get(key)
                if existing is not None:
                    self._validate_existing(
                        existing,
                        idempotency_key=idempotency_key,
                        request_digest=request_digest,
                    )
                    return dict(existing), False
                entry = self._entry(
                    evidence,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    extracted_at=extracted_at,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                )
                self._inbox[key] = entry
                return dict(entry), True

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._inbox_table} (
                        evidence_id, tenant_id, user_id, idempotency_key, request_digest,
                        interaction_kind, persona_id, session_id, content, source_refs,
                        learning_eligible, captured_at, extracted_at, status,
                        consent_granted, purpose, retention_days, is_raw_conversation,
                        explicit_conversation_consent, admission_receipt_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending',
                        %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT DO NOTHING
                    RETURNING created_at
                    """,
                    (
                        evidence.evidence_id,
                        tenant_id,
                        user_id,
                        idempotency_key,
                        request_digest,
                        evidence.interaction_kind.value,
                        evidence.persona_id,
                        evidence.session_id,
                        json.dumps(evidence.content),
                        json.dumps(evidence.source_refs),
                        evidence.learning_eligible,
                        evidence.captured_at,
                        extracted_at,
                        evidence.consent_granted,
                        evidence.purpose or "policy_learning",
                        evidence.retention_days if evidence.retention_days is not None else 30,
                        evidence.is_raw_conversation,
                        evidence.explicit_conversation_consent,
                        f"rcpt-adm-{evidence.evidence_id}",
                    ),
                )
                inserted = cur.fetchone()
                if inserted is not None:
                    entry = self._entry(
                        evidence,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        extracted_at=extracted_at,
                        idempotency_key=idempotency_key,
                        request_digest=request_digest,
                    )
                    entry["created_at"] = inserted[0].isoformat() if inserted[0] else None
                    return entry, True

                cur.execute(
                    f"""
                    SELECT evidence_id, tenant_id, user_id, idempotency_key, request_digest,
                           interaction_kind, persona_id, session_id, content, source_refs,
                           learning_eligible, captured_at, extracted_at, status, error_message,
                           lease_owner, lease_token, lease_expires_at, attempt_count,
                           consent_granted, purpose, retention_days, is_raw_conversation,
                           explicit_conversation_consent, admission_receipt_id,
                           created_at, processed_at
                    FROM {self._inbox_table}
                    WHERE tenant_id = %s AND user_id = %s
                      AND (evidence_id = %s OR idempotency_key = %s)
                    FOR UPDATE
                    """,
                    (tenant_id, user_id, evidence.evidence_id, idempotency_key),
                )
                row = cur.fetchone()
                if row is None:
                    raise IdempotencyConflictError(
                        "Scoped Agora evidence could not be inserted because of a conflicting durable key"
                    )
                existing = self._inbox_row(row)
                self._validate_existing(
                    existing,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                )
                return existing, False

    @staticmethod
    def _inbox_row(row: Any) -> Dict[str, Any]:
        return {
            "evidence_id": row[0],
            "tenant_id": row[1],
            "user_id": row[2],
            "idempotency_key": row[3],
            "request_digest": row[4],
            "interaction_kind": row[5],
            "persona_id": row[6],
            "session_id": row[7],
            "content": row[8],
            "source_refs": row[9],
            "learning_eligible": row[10],
            "captured_at": row[11],
            "extracted_at": row[12],
            "status": row[13],
            "error_message": row[14],
            "lease_owner": row[15],
            "lease_token": row[16],
            "lease_expires_at": row[17].isoformat() if row[17] else None,
            "attempt_count": row[18],
            "consent_granted": row[19] if len(row) > 19 and row[19] is not None else True,
            "purpose": row[20] if len(row) > 20 and row[20] is not None else "policy_learning",
            "retention_days": row[21] if len(row) > 21 and row[21] is not None else 30,
            "is_raw_conversation": row[22] if len(row) > 22 and row[22] is not None else False,
            "explicit_conversation_consent": row[23] if len(row) > 23 and row[23] is not None else False,
            "admission_receipt_id": row[24] if len(row) > 24 and row[24] else f"rcpt-adm-{row[0]}",
            "created_at": row[25].isoformat() if len(row) > 25 and row[25] else None,
            "processed_at": row[26].isoformat() if len(row) > 26 and row[26] else None,
        }

    def claim_inbox(
        self,
        *,
        tenant_id: str,
        user_id: str,
        worker_id: str,
        batch_size: int = DEFAULT_BATCH_SIZE,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        evidence_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Claim bounded scoped work, reclaiming only expired leases."""

        batch_size = max(1, min(int(batch_size), MAX_BATCH_SIZE))
        lease_seconds = max(1, min(int(lease_seconds), MAX_LEASE_SECONDS))
        claimed_at = _utc_datetime(now)
        lease_expires = claimed_at + timedelta(seconds=lease_seconds)

        if self.backend == "memory":
            with self._lock:
                eligible = []
                for entry in self._inbox.values():
                    if entry["tenant_id"] != tenant_id or entry["user_id"] != user_id:
                        continue
                    if evidence_id is not None and entry["evidence_id"] != evidence_id:
                        continue
                    expiry = entry.get("lease_expires_at")
                    expired = entry["status"] == "processing" and not expiry
                    if expiry:
                        expired = datetime.fromisoformat(str(expiry).replace("Z", "+00:00")) <= claimed_at
                    if entry["status"] == "pending" or (
                        entry["status"] == "processing" and expired
                    ):
                        eligible.append(entry)
                eligible.sort(key=lambda item: (str(item.get("created_at") or ""), item["evidence_id"]))
                claims = []
                for entry in eligible[:batch_size]:
                    entry["status"] = "processing"
                    entry["lease_owner"] = worker_id
                    entry["lease_token"] = uuid4().hex
                    entry["lease_expires_at"] = _iso(lease_expires)
                    entry["attempt_count"] = int(entry.get("attempt_count") or 0) + 1
                    claims.append(dict(entry))
                return claims

        params: List[Any] = [tenant_id, user_id, claimed_at]
        evidence_sql = ""
        if evidence_id is not None:
            evidence_sql = " AND evidence_id = %s"
            params.append(evidence_id)
        params.append(batch_size)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT evidence_id
                    FROM {self._inbox_table}
                    WHERE tenant_id = %s AND user_id = %s
                      AND (
                        status = 'pending'
                        OR (
                          status = 'processing'
                          AND (lease_expires_at IS NULL OR lease_expires_at <= %s)
                        )
                      )
                      {evidence_sql}
                    ORDER BY created_at ASC, evidence_id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                    """,
                    tuple(params),
                )
                ids = [str(row[0]) for row in cur.fetchall()]
                claims = []
                for claimed_id in ids:
                    lease_token = uuid4().hex
                    cur.execute(
                        f"""
                        UPDATE {self._inbox_table}
                        SET status = 'processing', lease_owner = %s, lease_token = %s,
                            lease_expires_at = %s, attempt_count = attempt_count + 1,
                            error_message = NULL
                        WHERE tenant_id = %s AND user_id = %s AND evidence_id = %s
                        RETURNING evidence_id, tenant_id, user_id, idempotency_key,
                                  request_digest, interaction_kind, persona_id, session_id,
                                  content, source_refs, learning_eligible, captured_at,
                                  extracted_at, status, error_message, lease_owner,
                                  lease_token, lease_expires_at, attempt_count,
                                  consent_granted, purpose, retention_days, is_raw_conversation,
                                  explicit_conversation_consent, admission_receipt_id,
                                  created_at, processed_at
                        """,
                        (
                            worker_id,
                            lease_token,
                            lease_expires,
                            tenant_id,
                            user_id,
                            claimed_id,
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        claims.append(self._inbox_row(row))
                return claims

    @staticmethod
    def _record_from_entry(
        entry: Dict[str, Any],
        *,
        sanitized_content: Dict[str, Any],
        redaction_applied: bool,
    ) -> DatasetRecord:
        kind = route_to_dataset(entry["interaction_kind"])
        interaction_kind = InteractionKind(entry["interaction_kind"])
        version_id = _dataset_version_id(
            tenant_id=entry["tenant_id"],
            user_id=entry["user_id"],
            evidence_id=entry["evidence_id"],
            request_digest=entry["request_digest"],
        )
        return DatasetRecord(
            evidence_id=entry["evidence_id"],
            dataset_version_id=version_id,
            dataset_kind=kind,
            interaction_kind=interaction_kind,
            persona_id=entry["persona_id"],
            session_id=entry.get("session_id"),
            tenant_id=entry["tenant_id"],
            user_id=entry["user_id"],
            content=sanitized_content,
            source_refs=entry["source_refs"],
            learning_eligible=entry["learning_eligible"],
            captured_at=entry["captured_at"],
            extracted_at=entry["extracted_at"],
            version=1,
            status="processed",
            consent_verified=True,
            redaction_applied=redaction_applied,
            purpose=entry.get("purpose"),
            retention_days=entry.get("retention_days"),
            admission_receipt_id=entry.get("admission_receipt_id") or f"rcpt-adm-{entry['evidence_id']}",
        )

    @staticmethod
    def _new_handoff(record: DatasetRecord, *, created_at: str) -> Dict[str, Any]:
        return {
            "handoff_id": _handoff_id(
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                dataset_version_id=record.dataset_version_id,
            ),
            "tenant_id": record.tenant_id,
            "user_id": record.user_id,
            "dataset_version_id": record.dataset_version_id,
            "dataset_kind": record.dataset_kind.value,
            "evidence_ids": [record.evidence_id],
            "summary": f"Durable Agora dataset handoff for {record.dataset_kind.value} with 1 item",
            "authority_limit": "Observe/Learn",
            "ack_status": "pending",
            "acknowledgement_id": None,
            "ack_request_digest": None,
            "downstream_ref": None,
            "acknowledged_by": None,
            "acknowledged_at": None,
            "created_at": created_at,
        }

    def _complete_claim(self, entry: Dict[str, Any], *, now: Optional[datetime] = None) -> Tuple[DatasetRecord, bool]:
        # Validate consent & retention
        if not entry.get("consent_granted", True):
            raise PrivacyConsentError("Evidence excluded: consent not granted or revoked")
        retention_days = int(entry.get("retention_days", 30) or 30)
        if retention_days <= 0 or retention_days > 365:
            raise PrivacyConsentError(f"Invalid retention_days: {retention_days}")

        # Validate raw conversation & redact sensitive fields
        is_raw_conv = bool(entry.get("is_raw_conversation", False))
        explicit_conv_consent = bool(entry.get("explicit_conversation_consent", False))
        sanitized_content, redaction_applied = sanitize_content_payload(
            entry.get("content", {}),
            is_raw_conversation=is_raw_conv,
            explicit_conversation_consent=explicit_conv_consent,
        )

        record = self._record_from_entry(
            entry,
            sanitized_content=sanitized_content,
            redaction_applied=redaction_applied,
        )
        processed_at = _utc_datetime(now)
        handoff = self._new_handoff(record, created_at=_iso(processed_at))
        scope_key = _scope_key(record.tenant_id, record.user_id, record.evidence_id)
        handoff_key = _scope_key(record.tenant_id, record.user_id, handoff["handoff_id"])

        if self.backend == "memory":
            with self._lock:
                current = self._inbox.get(scope_key)
                if (
                    current is None
                    or current.get("status") != "processing"
                    or current.get("lease_token") != entry.get("lease_token")
                ):
                    raise ClaimConflictError("Agora inbox lease is no longer owned by this worker")
                durable_record = self._records.setdefault(scope_key, record)
                handoff_created = handoff_key not in self._handoffs
                self._handoffs.setdefault(handoff_key, handoff)
                current.update(
                    {
                        "status": "processed",
                        "processed_at": _iso(processed_at),
                        "error_message": None,
                        "lease_owner": None,
                        "lease_token": None,
                        "lease_expires_at": None,
                    }
                )
                return durable_record, handoff_created

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT 1 FROM {self._inbox_table}
                    WHERE tenant_id = %s AND user_id = %s AND evidence_id = %s
                      AND status = 'processing' AND lease_token = %s
                    FOR UPDATE
                    """,
                    (
                        record.tenant_id,
                        record.user_id,
                        record.evidence_id,
                        entry["lease_token"],
                    ),
                )
                if cur.fetchone() is None:
                    raise ClaimConflictError("Agora inbox lease is no longer owned by this worker")

                cur.execute(
                    f"""
                    INSERT INTO {self._records_table} (
                        evidence_id, dataset_version_id, dataset_kind, interaction_kind,
                        persona_id, session_id, tenant_id, user_id, content, source_refs,
                        learning_eligible, captured_at, extracted_at, version,
                        consent_verified, redaction_applied, purpose, retention_days,
                        admission_receipt_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, user_id, evidence_id) DO NOTHING
                    """,
                    (
                        record.evidence_id,
                        record.dataset_version_id,
                        record.dataset_kind.value,
                        record.interaction_kind.value,
                        record.persona_id,
                        record.session_id,
                        record.tenant_id,
                        record.user_id,
                        json.dumps(record.content),
                        json.dumps(record.source_refs),
                        record.learning_eligible,
                        record.captured_at,
                        record.extracted_at,
                        record.consent_verified,
                        record.redaction_applied,
                        record.purpose,
                        record.retention_days,
                        record.admission_receipt_id,
                    ),
                )
                cur.execute(
                    f"""
                    INSERT INTO {self._handoffs_table} (
                        handoff_id, tenant_id, user_id, dataset_version_id,
                        dataset_kind, evidence_ids, summary, authority_limit
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'Observe/Learn')
                    ON CONFLICT (tenant_id, user_id, dataset_version_id) DO NOTHING
                    """,
                    (
                        handoff["handoff_id"],
                        record.tenant_id,
                        record.user_id,
                        record.dataset_version_id,
                        record.dataset_kind.value,
                        json.dumps(handoff["evidence_ids"]),
                        handoff["summary"],
                    ),
                )
                handoff_created = cur.rowcount > 0
                cur.execute(
                    f"""
                    UPDATE {self._inbox_table}
                    SET status = 'processed', processed_at = %s, error_message = NULL,
                        lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
                    WHERE tenant_id = %s AND user_id = %s AND evidence_id = %s
                      AND status = 'processing' AND lease_token = %s
                    """,
                    (
                        processed_at,
                        record.tenant_id,
                        record.user_id,
                        record.evidence_id,
                        entry["lease_token"],
                    ),
                )
                if cur.rowcount != 1:
                    raise ClaimConflictError("Agora inbox lease was lost before commit")
        return record, handoff_created

    def _fail_claim(self, entry: Dict[str, Any], error: Exception, *, now: Optional[datetime] = None) -> bool:
        failed_at = _utc_datetime(now)
        key = _scope_key(entry["tenant_id"], entry["user_id"], entry["evidence_id"])
        _logger.warning(
            "Agora inbox item %s failed processing in tenant %s: %s",
            entry.get("evidence_id"),
            entry.get("tenant_id"),
            str(error),
        )
        if self.backend == "memory":
            with self._lock:
                current = self._inbox.get(key)
                if (
                    current is None
                    or current.get("status") != "processing"
                    or current.get("lease_token") != entry.get("lease_token")
                ):
                    return False
                current.update(
                    {
                        "status": "failed",
                        "error_message": str(error),
                        "processed_at": _iso(failed_at),
                        "lease_owner": None,
                        "lease_token": None,
                        "lease_expires_at": None,
                    }
                )
                return True
        with self._connect() as conn:
            result = conn.execute(
                f"""
                UPDATE {self._inbox_table}
                SET status = 'failed', error_message = %s, processed_at = %s,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
                WHERE tenant_id = %s AND user_id = %s AND evidence_id = %s
                  AND status = 'processing' AND lease_token = %s
                """,
                (
                    str(error),
                    failed_at,
                    entry["tenant_id"],
                    entry["user_id"],
                    entry["evidence_id"],
                    entry["lease_token"],
                ),
            )
            return result.rowcount == 1

    def process_inbox(
        self,
        *,
        tenant_id: str,
        user_id: str,
        worker_id: Optional[str] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        evidence_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Claim and process one bounded user-private inbox batch."""

        resolved_worker = str(worker_id or f"agora-worker-{uuid4().hex[:12]}")
        claims = self.claim_inbox(
            tenant_id=tenant_id,
            user_id=user_id,
            worker_id=resolved_worker,
            batch_size=batch_size,
            lease_seconds=lease_seconds,
            evidence_id=evidence_id,
            now=now,
        )
        processed = 0
        failed = 0
        handoffs_created = 0
        lost_claims = 0
        for entry in claims:
            try:
                _, created = self._complete_claim(entry, now=now)
                processed += 1
                handoffs_created += int(created)
            except ClaimConflictError:
                lost_claims += 1
            except Exception as exc:
                if self._fail_claim(entry, exc, now=now):
                    failed += 1
                else:
                    lost_claims += 1
        return {
            "worker_id": resolved_worker,
            "claimed": len(claims),
            "processed": processed,
            "failed": failed,
            "lost_claims": lost_claims,
            "handoffs_created": handoffs_created,
        }

    def get(self, evidence_id: str, *, tenant_id: str, user_id: str) -> Optional[DatasetRecord]:
        """Return one record only when it matches the caller's full scope from dataset records."""

        key = _scope_key(tenant_id, user_id, evidence_id)
        if self.backend == "memory":
            with self._lock:
                return self._records.get(key)

        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT evidence_id, dataset_version_id, dataset_kind, interaction_kind,
                       persona_id, session_id, tenant_id, user_id, content, source_refs,
                       learning_eligible, captured_at, extracted_at, version,
                       consent_verified, redaction_applied, purpose, retention_days,
                       admission_receipt_id
                FROM {self._records_table}
                WHERE tenant_id = %s AND user_id = %s AND evidence_id = %s
                """,
                (tenant_id, user_id, evidence_id),
            ).fetchone()
            return self._dataset_row(row) if row else None

    def get_inbox_entry(self, evidence_id: str, *, tenant_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Return one raw inbox entry by scoped evidence_id."""
        key = _scope_key(tenant_id, user_id, evidence_id)
        if self.backend == "memory":
            with self._lock:
                entry = self._inbox.get(key)
                return dict(entry) if entry is not None else None

        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT evidence_id, tenant_id, user_id, idempotency_key, request_digest,
                       interaction_kind, persona_id, session_id, content, source_refs,
                       learning_eligible, captured_at, extracted_at, status, error_message,
                       lease_owner, lease_token, lease_expires_at, attempt_count,
                       consent_granted, purpose, retention_days, is_raw_conversation,
                       explicit_conversation_consent, admission_receipt_id,
                       created_at, processed_at
                FROM {self._inbox_table}
                WHERE tenant_id = %s AND user_id = %s AND evidence_id = %s
                """,
                (tenant_id, user_id, evidence_id),
            ).fetchone()
            return self._inbox_row(row) if row else None

    @staticmethod
    def _dataset_row(row: Any) -> DatasetRecord:
        return DatasetRecord(
            evidence_id=row[0],
            dataset_version_id=row[1],
            dataset_kind=DatasetKind(row[2]),
            interaction_kind=InteractionKind(row[3]),
            persona_id=row[4],
            session_id=row[5],
            tenant_id=row[6],
            user_id=row[7],
            content=row[8],
            source_refs=row[9],
            learning_eligible=row[10],
            captured_at=row[11],
            extracted_at=row[12],
            version=row[13],
            status="processed",
            consent_verified=row[14] if len(row) > 14 and row[14] is not None else True,
            redaction_applied=row[15] if len(row) > 15 and row[15] is not None else False,
            purpose=row[16] if len(row) > 16 else None,
            retention_days=row[17] if len(row) > 17 else None,
            admission_receipt_id=row[18] if len(row) > 18 else None,
        )

    def list_by_dataset(
        self,
        dataset_kind: DatasetKind,
        *,
        tenant_id: str,
        user_id: str,
        page_size: int = 50,
    ) -> List[DatasetRecord]:
        """Return a bounded user-private dataset projection."""

        if self.backend == "memory":
            with self._lock:
                records = [
                    record
                    for record in self._records.values()
                    if record.dataset_kind == dataset_kind
                    and record.tenant_id == tenant_id
                    and record.user_id == user_id
                ]
            return records[:page_size]
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT evidence_id, dataset_version_id, dataset_kind, interaction_kind,
                       persona_id, session_id, tenant_id, user_id, content, source_refs,
                       learning_eligible, captured_at, extracted_at, version,
                       consent_verified, redaction_applied, purpose, retention_days,
                       admission_receipt_id
                FROM {self._records_table}
                WHERE tenant_id = %s AND user_id = %s AND dataset_kind = %s
                ORDER BY extracted_at DESC
                LIMIT %s
                """,
                (tenant_id, user_id, dataset_kind.value, page_size),
            ).fetchall()
            return [self._dataset_row(row) for row in rows]

    def get_inbox_status(self, evidence_id: str, *, tenant_id: str, user_id: str) -> Optional[str]:
        key = _scope_key(tenant_id, user_id, evidence_id)
        if self.backend == "memory":
            with self._lock:
                entry = self._inbox.get(key)
                return str(entry["status"]) if entry else None
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT status FROM {self._inbox_table} "
                "WHERE tenant_id = %s AND user_id = %s AND evidence_id = %s",
                (tenant_id, user_id, evidence_id),
            ).fetchone()
            return str(row[0]) if row else None

    def get_inbox_error(self, evidence_id: str, *, tenant_id: str, user_id: str) -> Optional[str]:
        key = _scope_key(tenant_id, user_id, evidence_id)
        if self.backend == "memory":
            with self._lock:
                entry = self._inbox.get(key)
                return entry.get("error_message") if entry else None
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT error_message FROM {self._inbox_table} "
                "WHERE tenant_id = %s AND user_id = %s AND evidence_id = %s",
                (tenant_id, user_id, evidence_id),
            ).fetchone()
            return row[0] if row else None

    def _list_inbox(self, *, tenant_id: str, user_id: str, status: str) -> List[Dict[str, Any]]:
        if self.backend == "memory":
            with self._lock:
                return [
                    dict(entry)
                    for entry in self._inbox.values()
                    if entry["tenant_id"] == tenant_id
                    and entry["user_id"] == user_id
                    and entry["status"] == status
                ]
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT evidence_id, tenant_id, user_id, idempotency_key, request_digest,
                       interaction_kind, persona_id, session_id, content, source_refs,
                       learning_eligible, captured_at, extracted_at, status, error_message,
                       lease_owner, lease_token, lease_expires_at, attempt_count,
                       consent_granted, purpose, retention_days, is_raw_conversation,
                       explicit_conversation_consent, admission_receipt_id,
                       created_at, processed_at
                FROM {self._inbox_table}
                WHERE tenant_id = %s AND user_id = %s AND status = %s
                ORDER BY created_at ASC
                """,
                (tenant_id, user_id, status),
            ).fetchall()
            return [self._inbox_row(row) for row in rows]

    def get_backlog(self, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        return self._list_inbox(tenant_id=tenant_id, user_id=user_id, status="pending")

    def get_dlq(self, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        return self._list_inbox(tenant_id=tenant_id, user_id=user_id, status="failed")

    def replay_dlq_item(self, evidence_id: str, *, tenant_id: str, user_id: str) -> bool:
        """Reset only the caller's failed item; cross-scope ids remain invisible."""

        key = _scope_key(tenant_id, user_id, evidence_id)
        if self.backend == "memory":
            with self._lock:
                entry = self._inbox.get(key)
                if entry is None or entry["status"] != "failed":
                    return False
                entry.update(
                    {
                        "status": "pending",
                        "error_message": None,
                        "processed_at": None,
                        "lease_owner": None,
                        "lease_token": None,
                        "lease_expires_at": None,
                        "attempt_count": 0,
                    }
                )
                return True
        with self._connect() as conn:
            result = conn.execute(
                f"""
                UPDATE {self._inbox_table}
                SET status = 'pending', error_message = NULL, processed_at = NULL,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    attempt_count = 0
                WHERE tenant_id = %s AND user_id = %s AND evidence_id = %s
                  AND status = 'failed'
                """,
                (tenant_id, user_id, evidence_id),
            )
            return result.rowcount == 1

    @staticmethod
    def _handoff_row(row: Any) -> Dict[str, Any]:
        downstream = row[11]
        if isinstance(downstream, str):
            try:
                downstream = json.loads(downstream)
            except Exception:
                pass
        return {
            "handoff_id": row[0],
            "tenant_id": row[1],
            "user_id": row[2],
            "dataset_version_id": row[3],
            "dataset_kind": row[4],
            "evidence_ids": row[5],
            "summary": row[6],
            "authority_limit": row[7],
            "ack_status": row[8],
            "acknowledgement_id": row[9],
            "ack_request_digest": row[10],
            "downstream_ref": downstream,
            "acknowledged_by": row[12],
            "acknowledged_at": row[13].isoformat() if row[13] else None,
            "created_at": row[14].isoformat() if row[14] else None,
        }

    def list_handoffs(self, *, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        if self.backend == "memory":
            with self._lock:
                return [
                    dict(handoff)
                    for handoff in self._handoffs.values()
                    if handoff["tenant_id"] == tenant_id and handoff["user_id"] == user_id
                ]
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT handoff_id, tenant_id, user_id, dataset_version_id,
                       dataset_kind, evidence_ids, summary, authority_limit,
                       ack_status, acknowledgement_id, ack_request_digest,
                       downstream_ref, acknowledged_by, acknowledged_at, created_at
                FROM {self._handoffs_table}
                WHERE tenant_id = %s AND user_id = %s
                ORDER BY created_at DESC
                """,
                (tenant_id, user_id),
            ).fetchall()
            return [self._handoff_row(row) for row in rows]

    def acknowledge_handoff(
        self,
        handoff_id: str,
        *,
        tenant_id: str,
        user_id: str,
        acknowledgement_id: str,
        dataset_version_id: str,
        downstream_ref: Any,
        acknowledged_by: str,
        request_digest: str,
        acknowledged_at: str,
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Acknowledge one durable handoff exactly once within the caller scope."""

        key = _scope_key(tenant_id, user_id, handoff_id)
        if self.backend == "memory":
            with self._lock:
                handoff = self._handoffs.get(key)
                if handoff is None:
                    return None, False
                if handoff["dataset_version_id"] != dataset_version_id:
                    raise HandoffConflictError("dataset_version_id does not match the durable handoff")
                if handoff["ack_status"] == "acknowledged":
                    if (
                        handoff["acknowledgement_id"] == acknowledgement_id
                        and handoff["ack_request_digest"] == request_digest
                    ):
                        return dict(handoff), False
                    raise IdempotencyConflictError(
                        "Handoff is already acknowledged by a different downstream completion"
                    )
                handoff.update(
                    {
                        "ack_status": "acknowledged",
                        "acknowledgement_id": acknowledgement_id,
                        "ack_request_digest": request_digest,
                        "downstream_ref": downstream_ref,
                        "acknowledged_by": acknowledged_by,
                        "acknowledged_at": acknowledged_at,
                    }
                )
                return dict(handoff), True

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT handoff_id, tenant_id, user_id, dataset_version_id,
                           dataset_kind, evidence_ids, summary, authority_limit,
                           ack_status, acknowledgement_id, ack_request_digest,
                           downstream_ref, acknowledged_by, acknowledged_at, created_at
                    FROM {self._handoffs_table}
                    WHERE tenant_id = %s AND user_id = %s AND handoff_id = %s
                    FOR UPDATE
                    """,
                    (tenant_id, user_id, handoff_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None, False
                handoff = self._handoff_row(row)
                if handoff["dataset_version_id"] != dataset_version_id:
                    raise HandoffConflictError("dataset_version_id does not match the durable handoff")
                if handoff["ack_status"] == "acknowledged":
                    if (
                        handoff["acknowledgement_id"] == acknowledgement_id
                        and handoff["ack_request_digest"] == request_digest
                    ):
                        return handoff, False
                    raise IdempotencyConflictError(
                        "Handoff is already acknowledged by a different downstream completion"
                    )
                cur.execute(
                    f"""
                    UPDATE {self._handoffs_table}
                    SET ack_status = 'acknowledged', acknowledgement_id = %s,
                        ack_request_digest = %s, downstream_ref = %s,
                        acknowledged_by = %s, acknowledged_at = %s
                    WHERE tenant_id = %s AND user_id = %s AND handoff_id = %s
                    """,
                    (
                        acknowledgement_id,
                        request_digest,
                        json.dumps(downstream_ref) if not isinstance(downstream_ref, str) else downstream_ref,
                        acknowledged_by,
                        acknowledged_at,
                        tenant_id,
                        user_id,
                        handoff_id,
                    ),
                )
                handoff.update(
                    {
                        "ack_status": "acknowledged",
                        "acknowledgement_id": acknowledgement_id,
                        "ack_request_digest": request_digest,
                        "downstream_ref": downstream_ref,
                        "acknowledged_by": acknowledged_by,
                        "acknowledged_at": acknowledged_at,
                    }
                )
                return handoff, True


def admit_evidence(
    evidence: AgoraInteractionEvidenceRequest,
    *,
    tenant_id: str,
    user_id: str,
    idempotency_key: str,
    request_digest: str,
    admitted_at: str,
    store: AgoraDatasetStore,
) -> Tuple[Dict[str, Any], bool]:
    """Atomically persist eligible evidence into inbox/outbox without running worker."""

    if evidence.consent_granted is False:
        raise PrivacyConsentError("Evidence admission rejected: consent not granted or revoked")

    entry, is_new = store.add_to_inbox(
        evidence,
        tenant_id=tenant_id,
        user_id=user_id,
        extracted_at=admitted_at,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
    )
    return entry, is_new


def extract_evidence(
    evidence: AgoraInteractionEvidenceRequest,
    *,
    tenant_id: str,
    user_id: str,
    idempotency_key: str,
    request_digest: str,
    extracted_at: str,
    store: AgoraDatasetStore,
) -> Tuple[DatasetRecord, bool]:
    """Admit and immediately process single item (synchronous helper / test boundary)."""

    entry, is_new = admit_evidence(
        evidence,
        tenant_id=tenant_id,
        user_id=user_id,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        admitted_at=extracted_at,
        store=store,
    )
    store.process_inbox(
        tenant_id=tenant_id,
        user_id=user_id,
        evidence_id=evidence.evidence_id,
        batch_size=1,
    )
    record = store.get(evidence.evidence_id, tenant_id=tenant_id, user_id=user_id)
    deadline = time.monotonic() + 0.5
    while record is None and time.monotonic() < deadline:
        status = store.get_inbox_status(
            evidence.evidence_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if status != "processing":
            break
        time.sleep(0.01)
        record = store.get(evidence.evidence_id, tenant_id=tenant_id, user_id=user_id)
    if record is None:
        status = store.get_inbox_status(
            evidence.evidence_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if status == "failed":
            error_msg = store.get_inbox_error(
                evidence.evidence_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            raise ValueError(f"Failed to process evidence in inbox: {error_msg}")
        raise ValueError(f"Evidence record {evidence.evidence_id} not processed yet")
    if not is_new:
        record = record.model_copy(update={"idempotent": True})
    return record, is_new


__all__ = [
    "AgoraDatasetStore",
    "ClaimConflictError",
    "DatasetEligibilityError",
    "HandoffConflictError",
    "IdempotencyConflictError",
    "PrivacyConsentError",
    "acknowledgement_request_digest",
    "admit_evidence",
    "evidence_request_digest",
    "extract_evidence",
    "sanitize_content_payload",
]
