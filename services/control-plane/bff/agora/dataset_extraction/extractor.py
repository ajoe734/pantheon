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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union
from uuid import uuid4

from .models import (
    AgoraInteractionEvidenceRequest,
    DECISION_EVIDENCE_KINDS,
    DECISION_EVIDENCE_RETENTION_BUDGET,
    DEFAULT_DATASET_RETENTION_DAYS,
    DatasetAdmissionReceipt,
    DatasetKind,
    DatasetRecord,
    ESTIMATED_DECISION_EVIDENCE_BYTES,
    InteractionKind,
    MAX_DECISION_EVIDENCE_GROWTH_BYTES_PER_TRADER_PER_DAY,
    MAX_DECISION_EVIDENCE_PER_TRADER_PER_DAY,
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
        self._bootstrapped = False
        try:
            self._bootstrap()
            _logger.info("Agora dataset store initialized backend=postgres schema=%s", self.schema)
        except Exception as exc:
            _logger.warning("Agora dataset store deferred bootstrap: %s", exc)

    def _connect(self, *, bootstrap: bool = True) -> Any:
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("psycopg is required for Postgres Agora dataset store") from exc
        if bootstrap and not self._bootstrapped:
            self._bootstrap()
        return psycopg.connect(self.dsn)

    def _bootstrap(self) -> None:
        """Create the v2 scoped schema and migrate prior tables."""

        with self._connect(bootstrap=False) as conn:
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
        self._bootstrapped = True

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
            "interaction_kind": record.interaction_kind.value,
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
        if str(entry.get("interaction_kind") or "") in DECISION_EVIDENCE_KINDS and isinstance(sanitized_content, dict):
            sanitized_content = normalize_decision_evidence_content(
                sanitized_content,
                interaction_kind=str(entry["interaction_kind"]),
                evidence_id=str(entry["evidence_id"]),
                persona_id=str(entry["persona_id"]),
                user_id=str(entry["user_id"]),
                captured_at=str(entry["captured_at"]),
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

    def save_record(self, record: DatasetRecord) -> DatasetRecord:
        """Persist a DatasetRecord directly into the durable store."""
        key = _scope_key(record.tenant_id, record.user_id, record.evidence_id)
        if self.backend == "memory":
            with self._lock:
                self._records[key] = record
                return record

        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self._records_table} (
                    evidence_id, dataset_version_id, dataset_kind, interaction_kind,
                    persona_id, session_id, tenant_id, user_id, content, source_refs,
                    learning_eligible, captured_at, extracted_at, version,
                    consent_verified, redaction_applied, purpose, retention_days,
                    admission_receipt_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, user_id, evidence_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    dataset_version_id = EXCLUDED.dataset_version_id,
                    extracted_at = EXCLUDED.extracted_at
                """,
                (
                    record.evidence_id,
                    record.dataset_version_id,
                    record.dataset_kind.value if hasattr(record.dataset_kind, "value") else str(record.dataset_kind),
                    record.interaction_kind.value if hasattr(record.interaction_kind, "value") else str(record.interaction_kind),
                    record.persona_id,
                    record.session_id,
                    record.tenant_id,
                    record.user_id,
                    json.dumps(record.content) if isinstance(record.content, (dict, list)) else record.content,
                    json.dumps(record.source_refs) if isinstance(record.source_refs, (dict, list)) else record.source_refs,
                    record.learning_eligible,
                    record.captured_at,
                    record.extracted_at,
                    record.version,
                    record.consent_verified,
                    record.redaction_applied,
                    record.purpose,
                    record.retention_days,
                    record.admission_receipt_id,
                ),
            )
            return record

    def get_by_ref(
        self,
        ref: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[DatasetRecord]:
        """Look up a record by evidence_id, dataset_version_id, or full dataset ref."""
        clean_ref = str(ref or "").strip()
        if not clean_ref:
            return None
        stripped = clean_ref.split(":", 1)[-1] if ":" in clean_ref else clean_ref
        t = str(tenant_id or "").strip()
        u = str(user_id or "").strip()

        if self.backend == "memory":
            with self._lock:
                if t and u:
                    rec = self._records.get(_scope_key(t, u, clean_ref))
                    if rec is not None:
                        return rec
                    if stripped != clean_ref:
                        rec = self._records.get(_scope_key(t, u, stripped))
                        if rec is not None:
                            return rec
                for r in self._records.values():
                    if t and r.tenant_id != t:
                        continue
                    if u and r.user_id != u:
                        continue
                    if r.evidence_id in (clean_ref, stripped) or r.dataset_version_id in (clean_ref, stripped):
                        return r
            return None

        with self._connect() as conn:
            where_parts = ["(evidence_id = %s OR dataset_version_id = %s OR evidence_id = %s OR dataset_version_id = %s)"]
            params: List[Any] = [clean_ref, clean_ref, stripped, stripped]
            if t:
                where_parts.append("tenant_id = %s")
                params.append(t)
            if u:
                where_parts.append("user_id = %s")
                params.append(u)
            row = conn.execute(
                f"""
                SELECT evidence_id, dataset_version_id, dataset_kind, interaction_kind,
                       persona_id, session_id, tenant_id, user_id, content, source_refs,
                       learning_eligible, captured_at, extracted_at, version,
                       consent_verified, redaction_applied, purpose, retention_days,
                       admission_receipt_id
                FROM {self._records_table}
                WHERE {" AND ".join(where_parts)}
                LIMIT 1
                """,
                params,
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
        content_val = row[8]
        if isinstance(content_val, str):
            try:
                content_val = json.loads(content_val)
            except Exception:
                pass
        return DatasetRecord(
            evidence_id=row[0],
            dataset_version_id=row[1],
            dataset_kind=DatasetKind(row[2]),
            interaction_kind=InteractionKind(row[3]),
            persona_id=row[4],
            session_id=row[5],
            tenant_id=row[6],
            user_id=row[7],
            content=content_val if isinstance(content_val, dict) else {},
            source_refs=row[9] if isinstance(row[9], list) else (json.loads(row[9]) if isinstance(row[9], str) else []),
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

    def _attach_handoff_record(self, handoff: Dict[str, Any]) -> Dict[str, Any]:
        enriched = dict(handoff)
        try:
            record = self.get_by_ref(
                str(enriched.get("dataset_version_id") or ""),
                tenant_id=str(enriched.get("tenant_id") or ""),
                user_id=str(enriched.get("user_id") or ""),
            )
        except Exception:
            record = None
        if record is not None:
            enriched.setdefault("interaction_kind", record.interaction_kind.value)
            enriched.setdefault("record", record.model_dump(mode="json"))
        return enriched

    def list_handoffs_for_tenant(
        self,
        *,
        tenant_id: str,
        pending_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """List service-consumable handoffs across users in one tenant.

        Browser routes remain user-private.  This tenant-wide read exists only
        for the dedicated policy-learning service boundary, whose caller is
        authenticated and tenant-bound by the router before reaching the
        store.
        """

        if self.backend == "memory":
            with self._lock:
                items = [
                    self._attach_handoff_record(handoff)
                    for handoff in self._handoffs.values()
                    if handoff["tenant_id"] == tenant_id
                    and (
                        not pending_only
                        or handoff.get("ack_status") != "acknowledged"
                    )
                ]
                return sorted(
                    items,
                    key=lambda item: str(item.get("created_at") or ""),
                    reverse=True,
                )
        pending_sql = " AND ack_status <> 'acknowledged'" if pending_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT handoff_id, tenant_id, user_id, dataset_version_id,
                       dataset_kind, evidence_ids, summary, authority_limit,
                       ack_status, acknowledgement_id, ack_request_digest,
                       downstream_ref, acknowledged_by, acknowledged_at, created_at
                FROM {self._handoffs_table}
                WHERE tenant_id = %s{pending_sql}
                ORDER BY created_at DESC
                """,
                (tenant_id,),
            ).fetchall()
            raw_handoffs = [self._handoff_row(row) for row in rows]
        return [self._attach_handoff_record(h) for h in raw_handoffs]

    def extract_decision_lock(
        self,
        lock: Mapping[str, Any],
        *,
        persona_id: Optional[str] = None,
        retention_days: int = DEFAULT_DATASET_RETENTION_DAYS,
        extracted_at: Optional[str] = None,
    ) -> Tuple[DatasetRecord, bool]:
        """Extract one decision/rejection/deferral/intervention evidence item from an Agora decision lock."""
        return extract_decision_lock_evidence(
            lock,
            store=self,
            persona_id=persona_id,
            retention_days=retention_days,
            extracted_at=extracted_at,
        )

    def extract_journal_entry(
        self,
        entry: Mapping[str, Any],
        *,
        decision_lock: Optional[Mapping[str, Any]] = None,
        snapshot: Optional[Mapping[str, Any]] = None,
        persona_id: Optional[str] = None,
        retention_days: int = DEFAULT_DATASET_RETENTION_DAYS,
        extracted_at: Optional[str] = None,
    ) -> Tuple[DatasetRecord, bool]:
        """Extract one decision/rejection/deferral/intervention evidence item from a decision journal entry."""
        return extract_journal_entry_evidence(
            entry,
            store=self,
            decision_lock=decision_lock,
            snapshot=snapshot,
            persona_id=persona_id,
            retention_days=retention_days,
            extracted_at=extracted_at,
        )

    def acknowledge_handoff_for_tenant(
        self,
        handoff_id: str,
        *,
        tenant_id: str,
        acknowledgement_id: str,
        dataset_version_id: str,
        downstream_ref: Any,
        acknowledged_by: str,
        request_digest: str,
        acknowledged_at: str,
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Acknowledge an exact handoff without impersonating its end user."""

        if self.backend == "memory":
            with self._lock:
                matches = [
                    handoff
                    for handoff in self._handoffs.values()
                    if handoff["tenant_id"] == tenant_id
                    and handoff["handoff_id"] == handoff_id
                ]
                if len(matches) > 1:
                    raise HandoffConflictError(
                        "handoff_id is not unique inside the tenant boundary"
                    )
                user_id = str(matches[0]["user_id"]) if matches else ""
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT user_id
                    FROM {self._handoffs_table}
                    WHERE tenant_id = %s AND handoff_id = %s
                    """,
                    (tenant_id, handoff_id),
                ).fetchall()
            if len(rows) > 1:
                raise HandoffConflictError(
                    "handoff_id is not unique inside the tenant boundary"
                )
            user_id = str(rows[0][0]) if rows else ""
        if not user_id:
            return None, False
        return self.acknowledge_handoff(
            handoff_id,
            tenant_id=tenant_id,
            user_id=user_id,
            acknowledgement_id=acknowledgement_id,
            dataset_version_id=dataset_version_id,
            downstream_ref=downstream_ref,
            acknowledged_by=acknowledged_by,
            request_digest=request_digest,
            acknowledged_at=acknowledged_at,
        )

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


_INTERVENTION_VERDICTS = frozenset(
    {"modify", "modified", "edit", "edited", "intervene", "intervention", "override", "manual_override"}
)
_REJECTION_VERDICTS = frozenset({"reject", "rejected", "rejection", "decline", "declined"})
_DEFERRAL_VERDICTS = frozenset({"defer", "deferred", "deferral", "postpone", "postponed", "wait", "hold_off"})
_DECISION_VERDICTS = frozenset({"approve", "approved", "decision", "confirm", "confirmed", "enter", "add", "reduce", "exit", "hold"})

_INTERVENTION_TAGS = frozenset(
    {"intervention", "modify", "modified", "override", "manual_override", "risk_trim", "position_adjust", "size_override"}
)
_REJECTION_TAGS = frozenset({"rejection", "reject", "rejected", "candidate_rejected", "signal_rejected"})
_DEFERRAL_TAGS = frozenset({"deferral", "defer", "deferred", "postpone", "postponed"})
_DECISION_TAGS = frozenset({"decision", "approve", "approved", "confirm", "confirmed", "entry_decision", "exit_decision"})
_NON_ACTION_VERDICTS = frozenset({"approve", "approved", "confirm", "confirmed", "decision"})
_EVENT_KIND_ACTION_MAP: Dict[str, str] = {
    "entry": "enter",
    "enter": "enter",
    "add": "add",
    "reduce": "reduce",
    "exit": "exit",
    "review": "hold",
    "hold": "hold",
}

_ACTION_CODE_MAP: Dict[str, float] = {
    "enter": 1.0,
    "entry": 1.0,
    "buy": 1.0,
    "add": 0.5,
    "increase": 0.5,
    "hold": 0.0,
    "review": 0.0,
    "defer": -0.25,
    "reduce": -0.5,
    "trim": -0.5,
    "exit": -1.0,
    "sell": -1.0,
    "reject": -1.0,
}


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _string_seq(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, Mapping):
        return []
    if isinstance(value, Iterable):
        out: List[str] = []
        for item in value:
            if isinstance(item, Mapping):
                ref = _clean_str(item.get("ref") or item.get("uri"))
                if not ref:
                    kind = _clean_str(item.get("kind") or item.get("type"))
                    ident = _clean_str(item.get("id"))
                    if kind and ident:
                        ref = f"{kind}://{ident}"
                    elif ident:
                        ref = ident
                if ref:
                    out.append(ref)
            else:
                cleaned = _clean_str(item)
                if cleaned:
                    out.append(cleaned)
        return out
    return []


def classify_decision_evidence_kind(
    source: Mapping[str, Any],
    *,
    decision_lock: Optional[Mapping[str, Any]] = None,
) -> Optional[InteractionKind]:
    """Classify an Agora decision lock or decision journal entry into one of the four evidence kinds.

    Returns one of:
      - InteractionKind.DECISION ("decision")
      - InteractionKind.REJECTION ("rejection")
      - InteractionKind.DEFERRAL ("deferral")
      - InteractionKind.INTERVENTION ("intervention")
    or ``None`` when the source carries no decision signal.
    """
    for candidate_key in ("evidence_kind", "interaction_kind", "decision_kind", "journal_kind", "entry_kind"):
        raw = _clean_str(source.get(candidate_key)).lower()
        if raw in DECISION_EVIDENCE_KINDS:
            return InteractionKind(raw)
        if raw in _INTERVENTION_VERDICTS:
            return InteractionKind.INTERVENTION
        if raw in _REJECTION_VERDICTS:
            return InteractionKind.REJECTION
        if raw in _DEFERRAL_VERDICTS:
            return InteractionKind.DEFERRAL
        if raw in _DECISION_VERDICTS and candidate_key in ("evidence_kind", "interaction_kind", "decision_kind"):
            if not (source.get("raw_modifications") or source.get("modifications")):
                return InteractionKind.DECISION

    verdict = _clean_str(
        source.get("trading_room_verdict")
        or source.get("verdict")
        or source.get("trader_verdict")
        or source.get("decision")
    ).lower()
    has_modifications = bool(
        source.get("raw_modifications")
        or source.get("modifications")
        or source.get("intervention")
        or source.get("is_intervention")
    )

    if verdict in _REJECTION_VERDICTS:
        return InteractionKind.REJECTION
    if verdict in _DEFERRAL_VERDICTS:
        return InteractionKind.DEFERRAL
    if verdict in _INTERVENTION_VERDICTS or has_modifications:
        return InteractionKind.INTERVENTION

    # Check if human_actual action on lock diverges from base_strategy suggestion
    arms = source.get("arms") if isinstance(source.get("arms"), Mapping) else {}
    base_arm = arms.get("base_strategy") if isinstance(arms.get("base_strategy"), Mapping) else {}
    human_arm = arms.get("human_actual") if isinstance(arms.get("human_actual"), Mapping) else {}
    base_suggested = _clean_str(base_arm.get("suggested_action") or base_arm.get("suggestion")).lower()
    human_action = _clean_str(human_arm.get("action") or source.get("action")).lower()
    if human_action in _REJECTION_VERDICTS:
        return InteractionKind.REJECTION
    if human_action in _DEFERRAL_VERDICTS:
        return InteractionKind.DEFERRAL
    if human_action in _INTERVENTION_VERDICTS:
        return InteractionKind.INTERVENTION
    if (
        base_suggested
        and human_action
        and base_suggested != human_action
        and verdict not in {"approve", "approved"}
    ):
        return InteractionKind.INTERVENTION

    tags = {t.lower() for t in _string_seq(source.get("tags"))}
    if tags & _REJECTION_TAGS:
        return InteractionKind.REJECTION
    if tags & _DEFERRAL_TAGS:
        return InteractionKind.DEFERRAL
    if tags & _INTERVENTION_TAGS:
        return InteractionKind.INTERVENTION
    if tags & _DECISION_TAGS:
        return InteractionKind.DECISION

    if verdict in _DECISION_VERDICTS or human_action in _DECISION_VERDICTS:
        return InteractionKind.DECISION

    if decision_lock is not None and isinstance(decision_lock, Mapping) and decision_lock:
        lock_kind = classify_decision_evidence_kind(decision_lock)
        return lock_kind or InteractionKind.DECISION

    has_linked_decision_id = any(
        _clean_str(source.get(key))
        for key in (
            "decision_id",
            "decisionId",
            "human_decision_id",
            "humanDecisionId",
            "decision_record_id",
            "decision_event_id",
        )
    )
    has_linked_decision_ref = any(
        ref.startswith(("decision_lock://", "decision_event://", "decision_record://"))
        for ref in _string_seq(
            source.get("context_refs") or source.get("contextRefs") or source.get("source_refs")
        )
    )
    if has_linked_decision_id or has_linked_decision_ref:
        return InteractionKind.DECISION

    return None


def build_decision_time_snapshot(
    source: Mapping[str, Any],
    *,
    decision_lock: Optional[Mapping[str, Any]] = None,
    snapshot: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the decision-time snapshot for a decision lock or journal entry.

    Pure deterministic projection — no language model computes any number.
    """
    base: Dict[str, Any] = {}
    for layer in (
        decision_lock.get("decision_time_snapshot") if isinstance(decision_lock, Mapping) else None,
        decision_lock if isinstance(decision_lock, Mapping) else None,
        source.get("decision_time_snapshot"),
        source.get("market_snapshot"),
        source.get("snapshot"),
        snapshot,
    ):
        if isinstance(layer, Mapping):
            for k, v in layer.items():
                if k in {
                    "decision_time",
                    "available_data_cutoff",
                    "data_cutoff",
                    "triggered_at",
                    "symbol",
                    "target_instrument",
                    "subject",
                    "strategy_id",
                    "strategy_version_id",
                    "strategy_spec_registry_id",
                    "event_kind",
                    "origin",
                    "suggested_action",
                    "suggested_size",
                    "confidence",
                    "probability",
                    "expected_value",
                    "invalidation",
                    "risk_notes",
                    "market_snapshot",
                    "price",
                    "close",
                    "volume",
                    "observation",
                } and v is not None:
                    base[k] = json.loads(_canonical_json(v))

    arms = source.get("arms") if isinstance(source.get("arms"), Mapping) else (
        decision_lock.get("arms") if isinstance(decision_lock, Mapping) and isinstance(decision_lock.get("arms"), Mapping) else {}
    )
    base_arm = arms.get("base_strategy") if isinstance(arms.get("base_strategy"), Mapping) else {}
    human_arm = arms.get("human_actual") if isinstance(arms.get("human_actual"), Mapping) else {}
    servant_arm = arms.get("private_servant") if isinstance(arms.get("private_servant"), Mapping) else {}
    subject = source.get("subject") if isinstance(source.get("subject"), Mapping) else (
        base.get("subject") if isinstance(base.get("subject"), Mapping) else {}
    )

    decision_time = (
        _clean_str(source.get("decision_time"))
        or _clean_str(base.get("decision_time"))
        or _clean_str(source.get("event_time"))
        or _clean_str(source.get("captured_at"))
        or _clean_str(source.get("created_at") or source.get("createdAt"))
        or _iso(_utc_datetime())
    )
    cutoff = (
        _clean_str(source.get("available_data_cutoff") or source.get("data_cutoff"))
        or _clean_str(base.get("available_data_cutoff") or base.get("data_cutoff"))
        or None
    )
    symbol = (
        _clean_str(source.get("target_instrument") or source.get("symbol") or source.get("instrument"))
        or _clean_str(subject.get("symbol"))
        or _clean_str(human_arm.get("instrument"))
        or _clean_str(base.get("symbol") or base.get("target_instrument"))
        or None
    )
    linked_strategies = _string_seq(source.get("linked_strategy_ids") or source.get("linkedStrategyIds"))
    strategy_id = (
        _clean_str(source.get("strategy_id"))
        or _clean_str(base_arm.get("strategy_id") or base_arm.get("strategy_ref"))
        or (linked_strategies[0] if linked_strategies else "")
        or _clean_str(base.get("strategy_id"))
        or None
    )
    strategy_version_id = (
        _clean_str(source.get("strategy_version_id") or source.get("strategy_version"))
        or _clean_str(base_arm.get("strategy_version_id"))
        or _clean_str(base.get("strategy_version_id"))
        or None
    )
    registry_id = (
        _clean_str(source.get("strategy_spec_registry_id") or source.get("registry_id"))
        or _clean_str(base_arm.get("strategy_spec_registry_id"))
        or _clean_str(base.get("strategy_spec_registry_id"))
        or None
    )
    suggested_action = (
        _clean_str(source.get("suggested_action"))
        or _clean_str(base_arm.get("suggested_action") or base_arm.get("suggestion"))
        or _clean_str(base.get("suggested_action"))
        or None
    )
    suggested_size = (
        source.get("suggested_size")
        if source.get("suggested_size") is not None
        else (
            base_arm.get("suggested_size")
            if base_arm.get("suggested_size") is not None
            else base.get("suggested_size")
        )
    )

    result: Dict[str, Any] = {
        **base,
        "decision_time": decision_time,
        "available_data_cutoff": cutoff,
        "symbol": symbol,
        "target_instrument": symbol,
        "subject": dict(subject) if subject else ({"symbol": symbol} if symbol else {}),
        "strategy_id": strategy_id,
        "strategy_version_id": strategy_version_id,
        "strategy_spec_registry_id": registry_id,
        "event_kind": _clean_str(source.get("event_kind") or base.get("event_kind")) or None,
        "suggested_action": suggested_action,
        "suggested_size": suggested_size,
        "confidence": source.get("confidence") if source.get("confidence") is not None else base.get("confidence"),
        "probability": source.get("probability") if source.get("probability") is not None else base.get("probability"),
        "expected_value": source.get("expected_value") if source.get("expected_value") is not None else base.get("expected_value"),
    }
    if servant_arm:
        result["servant_suggestion"] = {
            "available": servant_arm.get("available", True),
            "suggestion": servant_arm.get("suggestion") or servant_arm.get("action") or servant_arm.get("suggested_action"),
            "persona_id": servant_arm.get("persona_id") or servant_arm.get("servant_persona_id") or servant_arm.get("servant_id"),
        }
    return result


def snapshot_observation_vector(
    snapshot: Mapping[str, Any],
    *,
    source: Optional[Mapping[str, Any]] = None,
) -> List[float]:
    """Deterministically build a numeric observation vector from a decision-time snapshot."""
    src = source or {}
    for candidate in (src.get("observation"), snapshot.get("observation")):
        if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)) and candidate:
            if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in candidate):
                return [float(x) for x in candidate]

    conf = snapshot.get("confidence")
    if isinstance(conf, Mapping) and isinstance(conf.get("value"), (int, float)) and not isinstance(conf.get("value"), bool):
        conf_val = float(conf["value"])
    elif isinstance(conf, (int, float)) and not isinstance(conf, bool):
        conf_val = float(conf)
    elif isinstance(src.get("execution_confidence"), (int, float)) and not isinstance(src.get("execution_confidence"), bool):
        conf_val = float(src["execution_confidence"])
    else:
        conf_val = 0.75

    prob = snapshot.get("probability")
    if isinstance(prob, Mapping) and isinstance(prob.get("value"), (int, float)) and not isinstance(prob.get("value"), bool):
        prob_val = float(prob["value"])
    elif isinstance(prob, (int, float)) and not isinstance(prob, bool):
        prob_val = float(prob)
    else:
        prob_val = 0.5

    ev = snapshot.get("expected_value")
    if isinstance(ev, Mapping):
        net_raw = ev.get("net")
        down_raw = ev.get("downside")
        net_val = float(net_raw) if isinstance(net_raw, (int, float)) and not isinstance(net_raw, bool) else 0.0
        down_val = float(down_raw) if isinstance(down_raw, (int, float)) and not isinstance(down_raw, bool) else 0.0
    else:
        net_val = 0.0
        down_val = 0.0

    action_token = _clean_str(snapshot.get("suggested_action") or snapshot.get("event_kind")).lower()
    action_code = _ACTION_CODE_MAP.get(action_token, 0.0)
    return [conf_val, prob_val, net_val, down_val, action_code]


def _resolve_decision_action(
    source: Mapping[str, Any],
    *,
    interaction_kind: str,
    snapshot: Mapping[str, Any],
) -> Optional[str]:
    explicit_action = _clean_str(source.get("action"))
    if explicit_action and explicit_action.lower() not in _NON_ACTION_VERDICTS:
        return explicit_action
    if interaction_kind == InteractionKind.REJECTION.value:
        return "reject"
    if interaction_kind == InteractionKind.DEFERRAL.value:
        return "defer"
    mods = source.get("raw_modifications") or source.get("modifications")
    if isinstance(mods, Mapping):
        mod_action = _clean_str(mods.get("action") or mods.get("direction"))
        if mod_action and mod_action.lower() not in _NON_ACTION_VERDICTS:
            return mod_action
    if interaction_kind == InteractionKind.INTERVENTION.value:
        arms = source.get("arms") if isinstance(source.get("arms"), Mapping) else {}
        human_arm = arms.get("human_actual") if isinstance(arms.get("human_actual"), Mapping) else {}
        human_action = _clean_str(human_arm.get("action"))
        if human_action and human_action.lower() not in (_NON_ACTION_VERDICTS | {"modify", "modified"}):
            return f"intervene_{human_action}"
        return "intervene"
    arms = source.get("arms") if isinstance(source.get("arms"), Mapping) else {}
    human_arm = arms.get("human_actual") if isinstance(arms.get("human_actual"), Mapping) else {}
    for candidate in (
        human_arm.get("action"),
        source.get("decision"),
        source.get("verdict"),
        source.get("trading_room_verdict"),
        snapshot.get("suggested_action"),
    ):
        cleaned = _clean_str(candidate)
        if cleaned and cleaned.lower() not in _NON_ACTION_VERDICTS:
            return cleaned
    event_kind = _clean_str(snapshot.get("event_kind") or source.get("event_kind")).lower()
    if event_kind in _EVENT_KIND_ACTION_MAP:
        return _EVENT_KIND_ACTION_MAP[event_kind]
    return None


def normalize_decision_evidence_content(
    content: Mapping[str, Any],
    *,
    interaction_kind: str,
    evidence_id: str,
    persona_id: str,
    user_id: str,
    captured_at: str,
) -> Dict[str, Any]:
    """Ensure a decision/rejection/deferral/intervention content payload carries snapshot and trajectory fields."""
    normalized = dict(content)
    snapshot = build_decision_time_snapshot(
        {**normalized, "captured_at": normalized.get("captured_at") or captured_at}
    )
    observation = snapshot_observation_vector(snapshot, source=normalized)
    action = _resolve_decision_action(
        normalized,
        interaction_kind=interaction_kind,
        snapshot=snapshot,
    )
    strategy_id = _clean_str(normalized.get("strategy_id") or snapshot.get("strategy_id")) or None
    normalized["evidence_kind"] = interaction_kind
    normalized["decision_time_snapshot"] = snapshot
    normalized.setdefault("observation", observation)
    if action:
        normalized["action"] = action
    else:
        normalized.pop("action", None)
    if strategy_id:
        normalized.setdefault("strategy_id", strategy_id)
    if snapshot.get("strategy_spec_registry_id"):
        normalized.setdefault("registry_id", snapshot["strategy_spec_registry_id"])
    if snapshot.get("strategy_version_id"):
        normalized.setdefault("artifact_version", snapshot["strategy_version_id"])
    normalized.setdefault("actor_id", _clean_str(user_id) or _clean_str(persona_id) or "agora-trader")
    normalized.setdefault("actor_role", "operator")
    normalized.setdefault(
        "trader_verdict",
        _clean_str(normalized.get("trading_room_verdict") or normalized.get("verdict") or normalized.get("decision")) or interaction_kind,
    )
    return normalized


def _resolve_persona_id(
    source: Mapping[str, Any],
    *,
    explicit_persona_id: Optional[str] = None,
    user_id: str = "",
) -> str:
    if _clean_str(explicit_persona_id):
        return _clean_str(explicit_persona_id)
    for key in ("persona_id", "servant_persona_id", "servant_id"):
        if _clean_str(source.get(key)):
            return _clean_str(source.get(key))
    arms = source.get("arms") if isinstance(source.get("arms"), Mapping) else {}
    servant = arms.get("private_servant") if isinstance(arms.get("private_servant"), Mapping) else {}
    for key in ("persona_id", "servant_persona_id", "servant_id"):
        if _clean_str(servant.get(key)):
            return _clean_str(servant.get(key))
    linked_personas = _string_seq(source.get("linked_persona_ids") or source.get("linkedPersonaIds"))
    if linked_personas:
        return linked_personas[0]
    return f"servant-{user_id}" if _clean_str(user_id) else "agora-servant"


def build_decision_lock_evidence_request(
    lock: Mapping[str, Any],
    *,
    persona_id: Optional[str] = None,
    retention_days: int = DEFAULT_DATASET_RETENTION_DAYS,
) -> AgoraInteractionEvidenceRequest:
    """Build one AgoraInteractionEvidenceRequest from an Agora decision lock."""
    if not isinstance(lock, Mapping):
        raise ValueError("Decision lock must be a mapping")
    lock_id = _clean_str(
        lock.get("human_decision_id")
        or lock.get("decision_id")
        or lock.get("record_id")
        or lock.get("decision_event_id")
    )
    if not lock_id:
        raise ValueError("Decision lock is missing human_decision_id / decision_id")

    trader_identity = lock.get("trader_identity") if isinstance(lock.get("trader_identity"), Mapping) else {}
    user_id = _clean_str(
        lock.get("user_id")
        or lock.get("agora_user_id")
        or trader_identity.get("user_id")
        or trader_identity.get("agora_user_id")
    )
    kind = classify_decision_evidence_kind(lock) or InteractionKind.DECISION
    resolved_persona_id = _resolve_persona_id(lock, explicit_persona_id=persona_id, user_id=user_id)
    decision_time = _clean_str(lock.get("decision_time") or lock.get("created_at")) or _iso(_utc_datetime())
    snapshot = build_decision_time_snapshot(lock)
    evidence_id = _clean_str(lock.get("evidence_id")) or f"ev-lock-{lock_id}"

    source_refs: List[str] = [f"decision_lock://{lock_id}"]
    event_id = _clean_str(lock.get("decision_event_id") or lock.get("source_trading_event_id"))
    if event_id:
        source_refs.append(f"decision_event://{event_id}")
    record_id = _clean_str(lock.get("decision_record_id"))
    if record_id:
        source_refs.append(f"decision_record://{record_id}")
    strategy_id = _clean_str(lock.get("strategy_id") or snapshot.get("strategy_id"))
    if strategy_id:
        source_refs.append(f"strategy://{strategy_id}")

    raw_content: Dict[str, Any] = {
        "source_kind": "decision_lock",
        "evidence_kind": kind.value,
        "human_decision_id": lock_id,
        "decision_event_id": event_id or None,
        "decision_record_id": record_id or None,
        "strategy_id": strategy_id or None,
        "target_instrument": snapshot.get("symbol"),
        "decision_time": decision_time,
        "available_data_cutoff": snapshot.get("available_data_cutoff"),
        "decision": _clean_str(lock.get("decision")) or None,
        "trading_room_verdict": _clean_str(lock.get("trading_room_verdict")) or None,
        "execution_observation_kind": _clean_str(lock.get("execution_observation_kind")) or "pantheon_paper_proxy",
        "rationale": lock.get("raw_rationale") if lock.get("raw_rationale") is not None else lock.get("raw_text"),
        "modifications": lock.get("raw_modifications") if lock.get("raw_modifications") is not None else lock.get("modifications"),
        "account_id": _clean_str(lock.get("account_id")) or None,
        "broker_ids": _string_seq(lock.get("broker_ids")),
        "arms": json.loads(_canonical_json(lock["arms"])) if isinstance(lock.get("arms"), Mapping) else {},
        "decision_time_snapshot": snapshot,
    }
    if lock.get("observation") is not None:
        raw_content["observation"] = lock["observation"]
    if lock.get("action") is not None:
        raw_content["action"] = lock["action"]

    content = normalize_decision_evidence_content(
        raw_content,
        interaction_kind=kind.value,
        evidence_id=evidence_id,
        persona_id=resolved_persona_id,
        user_id=user_id,
        captured_at=decision_time,
    )
    resolved_retention = int(lock.get("retention_days") or retention_days or DEFAULT_DATASET_RETENTION_DAYS)
    return AgoraInteractionEvidenceRequest(
        evidence_id=evidence_id,
        interaction_kind=kind,
        persona_id=resolved_persona_id,
        session_id=_clean_str(lock.get("session_id")) or None,
        content=content,
        source_refs=list(dict.fromkeys(source_refs)),
        learning_eligible=bool(lock.get("learning_eligible", True)),
        captured_at=decision_time,
        consent_granted=bool(lock.get("consent_granted", True)),
        purpose="policy_learning",
        retention_days=resolved_retention,
    )


def build_journal_entry_evidence_request(
    entry: Mapping[str, Any],
    *,
    decision_lock: Optional[Mapping[str, Any]] = None,
    snapshot: Optional[Mapping[str, Any]] = None,
    persona_id: Optional[str] = None,
    retention_days: int = DEFAULT_DATASET_RETENTION_DAYS,
) -> Optional[AgoraInteractionEvidenceRequest]:
    """Build one AgoraInteractionEvidenceRequest from an Agora decision journal entry.

    Returns ``None`` when the journal entry is a plain note with no decision signal.
    """
    if not isinstance(entry, Mapping):
        raise ValueError("Decision journal entry must be a mapping")
    entry_id = _clean_str(
        entry.get("entry_id")
        or entry.get("id")
        or entry.get("entryId")
        or entry.get("record_id")
    )
    if not entry_id:
        raise ValueError("Decision journal entry is missing entry_id / id")

    kind = classify_decision_evidence_kind(entry, decision_lock=decision_lock)
    if kind is None:
        return None

    user_id = _clean_str(
        entry.get("user_id")
        or entry.get("agora_user_id")
        or entry.get("userId")
        or entry.get("createdBy")
        or entry.get("actor_id")
    )
    resolved_persona_id = _resolve_persona_id(
        entry,
        explicit_persona_id=persona_id or (
            _resolve_persona_id(decision_lock, user_id=user_id) if isinstance(decision_lock, Mapping) else None
        ),
        user_id=user_id,
    )
    captured_at = (
        _clean_str(
            entry.get("decision_time")
            or entry.get("event_time")
            or entry.get("created_at")
            or entry.get("createdAt")
            or entry.get("updated_at")
            or entry.get("updatedAt")
        )
        or _iso(_utc_datetime())
    )
    resolved_snapshot = build_decision_time_snapshot(
        entry,
        decision_lock=decision_lock,
        snapshot=snapshot,
    )
    evidence_id = _clean_str(entry.get("evidence_id")) or f"ev-journal-{entry_id}"

    source_refs: List[str] = [f"decision_journal://{entry_id}"]
    decision_id = _clean_str(
        entry.get("decision_id")
        or entry.get("decisionId")
        or entry.get("human_decision_id")
        or entry.get("humanDecisionId")
        or (decision_lock.get("human_decision_id") if isinstance(decision_lock, Mapping) else "")
        or (decision_lock.get("decision_id") if isinstance(decision_lock, Mapping) else "")
    )
    if decision_id:
        source_refs.append(f"decision_lock://{decision_id}")
    strategy_id = _clean_str(entry.get("strategy_id") or resolved_snapshot.get("strategy_id"))
    if strategy_id:
        source_refs.append(f"strategy://{strategy_id}")
    for ref in _string_seq(entry.get("context_refs") or entry.get("contextRefs") or entry.get("source_refs")):
        source_refs.append(ref)

    raw_text = (
        entry.get("raw_text")
        if entry.get("raw_text") is not None
        else (entry.get("body") if entry.get("body") is not None else entry.get("content", ""))
    )
    lock_decision = (
        _clean_str(decision_lock.get("decision"))
        if isinstance(decision_lock, Mapping)
        else ""
    )
    raw_content: Dict[str, Any] = {
        "source_kind": "decision_journal",
        "evidence_kind": kind.value,
        "entry_id": entry_id,
        "decision_id": decision_id or None,
        "strategy_id": strategy_id or None,
        "target_instrument": resolved_snapshot.get("symbol"),
        "decision_time": resolved_snapshot.get("decision_time") or captured_at,
        "available_data_cutoff": resolved_snapshot.get("available_data_cutoff"),
        "title": _clean_str(entry.get("title")) or f"Decision Journal {entry_id}",
        "rationale": str(raw_text or ""),
        "raw_text": str(raw_text or ""),
        "tags": _string_seq(entry.get("tags")),
        "decision": _clean_str(entry.get("decision") or entry.get("verdict") or entry.get("trading_room_verdict") or lock_decision) or None,
        "modifications": entry.get("modifications") or entry.get("raw_modifications") or (
            decision_lock.get("raw_modifications") if isinstance(decision_lock, Mapping) else None
        ),
        "outcome": json.loads(_canonical_json(entry["outcome"])) if isinstance(entry.get("outcome"), Mapping) else entry.get("outcome"),
        "account_id": _clean_str(entry.get("account_id") or entry.get("accountId")) or None,
        "broker_ids": _string_seq(entry.get("broker_ids") or entry.get("brokerIds")),
        "decision_time_snapshot": resolved_snapshot,
    }
    if entry.get("observation") is not None:
        raw_content["observation"] = entry["observation"]
    if entry.get("action") is not None:
        raw_content["action"] = entry["action"]

    content = normalize_decision_evidence_content(
        raw_content,
        interaction_kind=kind.value,
        evidence_id=evidence_id,
        persona_id=resolved_persona_id,
        user_id=user_id,
        captured_at=captured_at,
    )
    resolved_retention = int(entry.get("retention_days") or retention_days or DEFAULT_DATASET_RETENTION_DAYS)
    return AgoraInteractionEvidenceRequest(
        evidence_id=evidence_id,
        interaction_kind=kind,
        persona_id=resolved_persona_id,
        session_id=_clean_str(entry.get("session_id")) or None,
        content=content,
        source_refs=list(dict.fromkeys(source_refs)),
        learning_eligible=bool(entry.get("learning_eligible", True)),
        captured_at=captured_at,
        consent_granted=bool(entry.get("consent_granted", True)),
        purpose="policy_learning",
        retention_days=resolved_retention,
    )


def extract_decision_lock_evidence(
    lock: Mapping[str, Any],
    *,
    store: Optional[AgoraDatasetStore] = None,
    persona_id: Optional[str] = None,
    retention_days: int = DEFAULT_DATASET_RETENTION_DAYS,
    extracted_at: Optional[str] = None,
) -> Tuple[DatasetRecord, bool]:
    """Extract one learning evidence record and durable handoff from an Agora decision lock.

    Idempotent: one evidence item per decision lock (`ev-lock-<human_decision_id>`).
    """
    trader_identity = lock.get("trader_identity") if isinstance(lock.get("trader_identity"), Mapping) else {}
    tenant_id = _clean_str(lock.get("tenant_id") or trader_identity.get("tenant_id"))
    user_id = _clean_str(
        lock.get("user_id")
        or lock.get("agora_user_id")
        or trader_identity.get("user_id")
        or trader_identity.get("agora_user_id")
    )
    if not tenant_id or not user_id:
        raise ValueError("Decision lock requires tenant_id and user_id for evidence extraction")
    resolved_store = store
    if resolved_store is None:
        from .router import _default_store

        resolved_store = _default_store()
    request = build_decision_lock_evidence_request(
        lock,
        persona_id=persona_id,
        retention_days=retention_days,
    )
    resolved_extracted_at = _clean_str(extracted_at) or _clean_str(lock.get("decision_time")) or _iso(_utc_datetime())
    return extract_evidence(
        request,
        tenant_id=tenant_id,
        user_id=user_id,
        idempotency_key=f"agora-lock-evidence:{tenant_id}:{user_id}:{request.evidence_id}",
        request_digest=evidence_request_digest(request),
        extracted_at=resolved_extracted_at,
        store=resolved_store,
    )


def extract_journal_entry_evidence(
    entry: Mapping[str, Any],
    *,
    store: Optional[AgoraDatasetStore] = None,
    decision_lock: Optional[Mapping[str, Any]] = None,
    snapshot: Optional[Mapping[str, Any]] = None,
    persona_id: Optional[str] = None,
    retention_days: int = DEFAULT_DATASET_RETENTION_DAYS,
    extracted_at: Optional[str] = None,
) -> Optional[Tuple[DatasetRecord, bool]]:
    """Extract one learning evidence record and durable handoff from a decision journal entry.

    Returns ``None`` without writing to ``store`` when the entry carries no decision signal.
    Idempotent: one evidence item per decision-bearing journal entry (`ev-journal-<entry_id>`).
    """
    lock_map = decision_lock if isinstance(decision_lock, Mapping) else {}
    tenant_id = _clean_str(
        entry.get("tenant_id")
        or entry.get("tenantId")
        or lock_map.get("tenant_id")
    )
    user_id = _clean_str(
        entry.get("user_id")
        or entry.get("agora_user_id")
        or entry.get("userId")
        or entry.get("createdBy")
        or entry.get("actor_id")
        or lock_map.get("user_id")
        or lock_map.get("agora_user_id")
    )
    if not tenant_id or not user_id:
        raise ValueError("Decision journal entry requires tenant_id and user_id for evidence extraction")
    request = build_journal_entry_evidence_request(
        entry,
        decision_lock=decision_lock,
        snapshot=snapshot,
        persona_id=persona_id,
        retention_days=retention_days,
    )
    if request is None:
        return None
    resolved_store = store
    if resolved_store is None:
        from .router import _default_store

        resolved_store = _default_store()
    resolved_extracted_at = _clean_str(extracted_at) or request.captured_at or _iso(_utc_datetime())
    return extract_evidence(
        request,
        tenant_id=tenant_id,
        user_id=user_id,
        idempotency_key=f"agora-journal-evidence:{tenant_id}:{user_id}:{request.evidence_id}",
        request_digest=evidence_request_digest(request),
        extracted_at=resolved_extracted_at,
        store=resolved_store,
    )


def _emit_journal_entry_evidence_best_effort(
    entry: Mapping[str, Any],
    *,
    dataset_store: Optional[Any] = None,
) -> Optional[DatasetRecord]:
    """Best-effort journal evidence extraction that never blocks journal writes."""
    entry_id = _clean_str(
        entry.get("entry_id")
        or entry.get("id")
        or entry.get("entryId")
        or entry.get("record_id")
    )
    try:
        ds_store = dataset_store
        if ds_store is None:
            from .router import _default_store

            ds_store = _default_store()
        if ds_store is None:
            return None
        tenant_id = _clean_str(entry.get("tenant_id") or entry.get("tenantId"))
        user_id = _clean_str(
            entry.get("user_id")
            or entry.get("userId")
            or entry.get("createdBy")
            or entry.get("actor_id")
        )
        if not tenant_id or not user_id or not entry_id:
            return None
        extracted = extract_journal_entry_evidence(entry, store=ds_store)
        if extracted is None:
            return None
        record, _ = extracted
        return record
    except Exception as exc:
        _logger.warning(
            "Best-effort decision-journal learning evidence extraction failed for entry_id=%s: %s",
            entry_id,
            exc,
        )
        return None


def ensure_journal_evidence_emission_wired() -> None:
    """Wire best-effort learning evidence extraction into the production Decision Journal write path."""
    try:
        from services.control_plane.bff.governance.decision_journal_write_owner import (
            DecisionJournalOwnerAdapter,
        )

        if not getattr(DecisionJournalOwnerAdapter, "_agora_journal_evidence_hook_installed", False):
            orig_owner_create = DecisionJournalOwnerAdapter.create_decision_journal_entry

            def _wrapped_owner_create(
                self: Any,
                *args: Any,
                title: str = "",
                body: str = "",
                entry_id: Optional[str] = None,
                actor_id: Optional[str] = None,
                payload: Optional[Dict[str, Any]] = None,
                created_at: str = "",
                tenant_id: Optional[str] = None,
                user_id: Optional[str] = None,
                **kwargs: Any,
            ) -> Dict[str, Any]:
                created = orig_owner_create(
                    self,
                    *args,
                    title=title,
                    body=body,
                    entry_id=entry_id,
                    actor_id=actor_id,
                    payload=payload,
                    created_at=created_at,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    **kwargs,
                )
                raw_payload = payload if isinstance(payload, Mapping) else {}
                created_map = created if isinstance(created, Mapping) else {}
                resolved_user = (
                    user_id
                    or raw_payload.get("user_id")
                    or raw_payload.get("userId")
                    or actor_id
                    or created_map.get("user_id")
                    or created_map.get("userId")
                    or ""
                )
                _emit_journal_entry_evidence_best_effort(
                    {
                        **raw_payload,
                        **created_map,
                        "entry_id": created_map.get("id") or entry_id or raw_payload.get("id") or raw_payload.get("entryId"),
                        "tenant_id": tenant_id or created_map.get("tenant_id") or created_map.get("tenantId"),
                        "user_id": resolved_user,
                        "actor_id": actor_id or resolved_user,
                    },
                    dataset_store=kwargs.get("dataset_store") or getattr(self, "dataset_store", None),
                )
                return created

            DecisionJournalOwnerAdapter.create_decision_journal_entry = _wrapped_owner_create  # type: ignore[method-assign]
            DecisionJournalOwnerAdapter._agora_journal_evidence_hook_installed = True  # type: ignore[attr-defined]
    except Exception as exc:
        _logger.warning("DecisionJournalOwnerAdapter evidence hook wiring failed: %s", exc)

    try:
        from services.control_plane.bff.agora.service import AgoraService

        if not getattr(AgoraService, "_agora_journal_evidence_hook_installed", False):
            orig_service_create = AgoraService.create_journal_entry

            def _wrapped_service_create(
                self: Any,
                *args: Any,
                payload: Optional[Dict[str, Any]] = None,
                identity: Any = None,
                idempotency_key: Optional[str] = None,
                x_dry_run: Optional[str] = None,
                tenant_id: Optional[str] = None,
                user_id: Optional[str] = None,
                **kwargs: Any,
            ) -> Any:
                owner = getattr(self, "journal_write_owner", None)
                svc_ds_store = getattr(self, "dataset_store", None)
                if svc_ds_store is not None and owner is not None and getattr(owner, "dataset_store", None) is None:
                    try:
                        owner.dataset_store = svc_ds_store
                    except Exception:
                        pass
                result = orig_service_create(
                    self,
                    *args,
                    payload=payload,
                    identity=identity,
                    idempotency_key=idempotency_key,
                    x_dry_run=x_dry_run,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    **kwargs,
                )
                dry_run = bool(x_dry_run and str(x_dry_run).strip().lower() in ("true", "1", "yes"))
                if not dry_run and isinstance(result, Mapping) and isinstance(result.get("data"), Mapping):
                    created_data = result["data"]
                    ds_store = svc_ds_store or getattr(owner, "dataset_store", None)
                    _emit_journal_entry_evidence_best_effort(
                        {
                            **(payload if isinstance(payload, Mapping) else {}),
                            **created_data,
                            "entry_id": created_data.get("id") or created_data.get("entryId"),
                            "tenant_id": created_data.get("tenant_id") or created_data.get("tenantId") or tenant_id,
                            "user_id": created_data.get("user_id") or created_data.get("userId") or user_id or getattr(identity, "operator_id", None),
                        },
                        dataset_store=ds_store,
                    )
                return result

            AgoraService.create_journal_entry = _wrapped_service_create  # type: ignore[method-assign]
            AgoraService._agora_journal_evidence_hook_installed = True  # type: ignore[attr-defined]
    except Exception as exc:
        _logger.warning("AgoraService journal evidence hook wiring failed: %s", exc)


ensure_journal_evidence_emission_wired()


__all__ = [
    "AgoraDatasetStore",
    "ClaimConflictError",
    "DECISION_EVIDENCE_KINDS",
    "DECISION_EVIDENCE_RETENTION_BUDGET",
    "DEFAULT_DATASET_RETENTION_DAYS",
    "DatasetEligibilityError",
    "ESTIMATED_DECISION_EVIDENCE_BYTES",
    "HandoffConflictError",
    "IdempotencyConflictError",
    "MAX_DECISION_EVIDENCE_GROWTH_BYTES_PER_TRADER_PER_DAY",
    "MAX_DECISION_EVIDENCE_PER_TRADER_PER_DAY",
    "PrivacyConsentError",
    "acknowledgement_request_digest",
    "admit_evidence",
    "build_decision_lock_evidence_request",
    "build_decision_time_snapshot",
    "build_journal_entry_evidence_request",
    "classify_decision_evidence_kind",
    "ensure_journal_evidence_emission_wired",
    "evidence_request_digest",
    "extract_decision_lock_evidence",
    "extract_evidence",
    "extract_journal_entry_evidence",
    "normalize_decision_evidence_content",
    "sanitize_content_payload",
    "snapshot_observation_vector",
]
