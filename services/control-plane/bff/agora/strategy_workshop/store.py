"""Agora workshop persistence and durable governed-command receipts.

Tables (owned by this module):
  strategy_workshop_session      — workshop lifecycle and subject reference
  strategy_workshop_event        — append-only event log; private content NEVER stored here
  strategy_completeness_snapshot — completeness/next-question snapshot
  strategy_workshop_version_link — link to immutable StrategySpec registry rows
  strategy_workshop_command_receipt — admitted/completed/failed command ledger

Privacy rule: workshop events store only ``private_content_ref`` (pointer to
the encrypted store) and ``redacted_summary``.  The raw private content must
never be written into these tables.

Backend env:
  AGORA_WORKSHOP_STORE_BACKEND   off | postgres  (default: off)
  AGORA_WORKSHOP_STORE_DSN       Postgres DSN when backend=postgres
  AGORA_WORKSHOP_STORE_SCHEMA    Schema name     (default: agora)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_logger = logging.getLogger(__name__)

BACKEND_ENV = "AGORA_WORKSHOP_STORE_BACKEND"
DSN_ENV = "AGORA_WORKSHOP_STORE_DSN"
SCHEMA_ENV = "AGORA_WORKSHOP_STORE_SCHEMA"
DEFAULT_SCHEMA = "agora"
IDEMPOTENCY_AGGREGATE_TYPE = "strategy_workshop"
WORKSHOP_STATUSES = ("open", "in_review", "concluded", "archived")
TERMINAL_WORKSHOP_STATUSES = frozenset({"concluded", "archived"})
COMMAND_STATUSES = ("admitted", "completed", "failed")
_DOCUMENT_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class WorkshopVersionProjectionConflict(ValueError):
    """A durable version link disagrees with authoritative Registry identity."""


def _document_sha256(value: Any) -> str:
    digest = str(value or "").strip().lower()
    if not _DOCUMENT_SHA256_RE.fullmatch(digest):
        raise ValueError("document_sha256 must be 64 lowercase hexadecimal characters")
    return digest


def _legacy_workshop_version_id(workshop_id: str, registry_id: str) -> str:
    digest = hashlib.sha256(
        f"{workshop_id}\x00{registry_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"wsv-legacy-{digest}"


@dataclass(frozen=True)
class StrategyWorkshopTableNames:
    """Historical deep-closure owner table names used by schema validation."""

    session: str
    event: str
    version_link: str
    completeness_snapshot: str
    private_content_object: str


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())


def _safe_id_fragment(value: Any) -> str:
    raw = str(value or "").strip() or uuid.uuid4().hex
    return "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in raw)


def _new_readiness_id(workshop_id: str, assessment_version: int) -> str:
    return f"ready_{_safe_id_fragment(workshop_id)}_{assessment_version}"


def _decode_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _json_dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True)


def _row_to_dict(row: Any, cols: List[str]) -> Dict[str, Any]:
    if isinstance(row, (tuple, list)):
        return dict(zip(cols, row))
    return {c: getattr(row, c, None) for c in cols}


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SENSITIVE_COMMAND_KEYS = frozenset({
    "authorization", "conclusion_summary", "content", "cookie", "initial_message",
    "message", "mfa_token", "parameters", "password", "patch", "private_content",
    "reason", "research_context", "secret", "subject", "summary", "token",
})


def _clean_schema(schema: Optional[str]) -> str:
    value = str(schema or "").strip()
    if value and not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Unsafe Postgres schema identifier: {value!r}")
    return value


def _quote_identifier(value: str) -> str:
    """Quote a trusted identifier after strict validation.

    This deliberately accepts identifiers, not arbitrary SQL fragments.  It is
    used for schema/table/index names; command data always remains parameterized.
    """

    parts = value.split(".")
    if not parts or any(not _IDENTIFIER_RE.fullmatch(part) for part in parts):
        raise ValueError(f"Unsafe Postgres identifier: {value!r}")
    return ".".join(f'"{part}"' for part in parts)


def _qualified(schema: Optional[str], name: str) -> str:
    clean_schema = _clean_schema(schema)
    return f"{clean_schema}.{name}" if clean_schema else name


def _quoted(schema: Optional[str], name: str) -> str:
    return _quote_identifier(_qualified(schema, name))


def _deep_copy_dict(value: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(value)


def _redact_command_payload(value: Any) -> Any:
    """Defensively redact obvious credential/private-content fields.

    Callers should already pass a redacted structured payload.  This second
    boundary prevents common auth material from being persisted accidentally.
    """

    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SENSITIVE_COMMAND_KEYS or normalized.endswith("_token"):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = _redact_command_payload(item)
        return result
    if isinstance(value, list):
        return [_redact_command_payload(item) for item in value]
    return value


def _deep_closure_tables(schema: Optional[str] = None) -> StrategyWorkshopTableNames:
    return StrategyWorkshopTableNames(
        session=_quoted(schema, "strategy_workshop_session"),
        event=_quoted(schema, "strategy_workshop_event"),
        version_link=_quoted(schema, "strategy_workshop_version_link"),
        completeness_snapshot=_quoted(schema, "strategy_completeness_snapshot"),
        private_content_object=_quoted(schema, "agora_private_content_object"),
    )


def build_strategy_workshop_table_ddl(schema: Optional[str] = None) -> List[str]:
    """Return the five AG-BE-SW deep-closure owner-table statements.

    The active BFF store has additional additive tables below.  Keeping this
    historical contract builder restores executable validation of the frozen
    persistence design without taking ownership of private-content runtime IO.
    """

    tables = _deep_closure_tables(schema)
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {tables.session} (
            workshop_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            servant_persona_id TEXT NOT NULL,
            openclaw_session_id TEXT NULL,
            strategy_id TEXT NULL,
            active_strategy_spec_registry_id TEXT NULL,
            active_workshop_version_id TEXT NULL,
            final_strategy_spec_registry_id TEXT NULL,
            final_workshop_version_id TEXT NULL,
            status TEXT NOT NULL,
            lock_version BIGINT NOT NULL DEFAULT 1,
            title TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            concluded_at TIMESTAMPTZ NULL,
            archived_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_strategy_workshop_session_status
                CHECK (status IN ('open','in_review','concluded','archived'))
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {tables.event} (
            event_id TEXT PRIMARY KEY,
            workshop_id TEXT NOT NULL REFERENCES {tables.session}(workshop_id),
            sequence_no BIGINT NOT NULL,
            actor_type TEXT NOT NULL,
            actor_ref TEXT NULL,
            event_type TEXT NOT NULL,
            private_content_ref TEXT NULL,
            redacted_summary TEXT NULL,
            redaction_policy_version TEXT NULL,
            payload_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            trace_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ux_workshop_event_sequence UNIQUE (workshop_id, sequence_no),
            CONSTRAINT ck_strategy_workshop_event_message_redaction CHECK (
                event_type <> 'message'
                OR (
                    private_content_ref IS NOT NULL
                    AND redacted_summary IS NOT NULL
                    AND redaction_policy_version IS NOT NULL
                )
            )
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {tables.version_link} (
            workshop_version_id TEXT PRIMARY KEY,
            workshop_id TEXT NOT NULL REFERENCES {tables.session}(workshop_id),
            strategy_id TEXT NOT NULL,
            strategy_spec_registry_id TEXT NOT NULL,
            parent_workshop_version_id TEXT NULL,
            source_event_id TEXT NULL,
            sequence_no BIGINT NOT NULL,
            document_sha256 CHAR(64) NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ux_workshop_version_sequence UNIQUE (workshop_id, sequence_no),
            CONSTRAINT ux_workshop_registry_version UNIQUE (workshop_id, strategy_spec_registry_id),
            CONSTRAINT ck_workshop_version_document_sha256
                CHECK (document_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {tables.completeness_snapshot} (
            snapshot_id TEXT PRIMARY KEY,
            workshop_id TEXT NOT NULL REFERENCES {tables.session}(workshop_id),
            workshop_version_id TEXT NULL,
            assessment_version BIGINT NOT NULL,
            state_map_json JSONB NOT NULL,
            blocking_items_json JSONB NOT NULL,
            next_question_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ux_workshop_completeness_version UNIQUE (workshop_id, assessment_version)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {tables.private_content_object} (
            private_content_ref TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            workshop_id TEXT NOT NULL,
            event_id TEXT NULL,
            object_uri TEXT NOT NULL,
            ciphertext_sha256 CHAR(64) NOT NULL,
            encrypted_dek BYTEA NOT NULL,
            kek_key_version TEXT NOT NULL,
            content_type TEXT NOT NULL,
            retention_class TEXT NOT NULL,
            expires_at TIMESTAMPTZ NULL,
            state TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            deleted_at TIMESTAMPTZ NULL
        )
        """,
    ]


def build_strategy_workshop_index_ddl(schema: Optional[str] = None) -> List[str]:
    tables = _deep_closure_tables(schema)

    def index_name(name: str) -> str:
        return _quoted(schema, name)

    return [
        f"CREATE INDEX IF NOT EXISTS {index_name('ix_workshop_user_status_updated')} "
        f"ON {tables.session} (tenant_id, user_id, status, updated_at DESC)",
        f"CREATE INDEX IF NOT EXISTS {index_name('ix_workshop_servant_status_updated')} "
        f"ON {tables.session} (servant_persona_id, status, updated_at DESC)",
        f"CREATE INDEX IF NOT EXISTS {index_name('ix_workshop_strategy_updated')} "
        f"ON {tables.session} (strategy_id, updated_at DESC) WHERE strategy_id IS NOT NULL",
        f"CREATE INDEX IF NOT EXISTS {index_name('ix_workshop_active_registry_ref')} "
        f"ON {tables.session} (active_strategy_spec_registry_id) "
        "WHERE active_strategy_spec_registry_id IS NOT NULL",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name('ux_workshop_openclaw_session')} "
        f"ON {tables.session} (openclaw_session_id) WHERE openclaw_session_id IS NOT NULL",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name('ux_workshop_event_sequence')} "
        f"ON {tables.event} (workshop_id, sequence_no)",
        f"CREATE INDEX IF NOT EXISTS {index_name('ix_workshop_event_created')} "
        f"ON {tables.event} (workshop_id, created_at, sequence_no)",
        f"CREATE INDEX IF NOT EXISTS {index_name('ix_workshop_event_trace')} "
        f"ON {tables.event} (trace_id)",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name('ux_workshop_event_private_ref')} "
        f"ON {tables.event} (private_content_ref) WHERE private_content_ref IS NOT NULL",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name('ux_workshop_version_sequence')} "
        f"ON {tables.version_link} (workshop_id, sequence_no)",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name('ux_workshop_registry_version')} "
        f"ON {tables.version_link} (workshop_id, strategy_spec_registry_id)",
        f"CREATE INDEX IF NOT EXISTS {index_name('ix_workshop_version_strategy')} "
        f"ON {tables.version_link} (strategy_id, created_at DESC)",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name('ux_workshop_completeness_version')} "
        f"ON {tables.completeness_snapshot} (workshop_id, assessment_version)",
        f"CREATE INDEX IF NOT EXISTS {index_name('ix_workshop_completeness_latest')} "
        f"ON {tables.completeness_snapshot} (workshop_id, created_at DESC)",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name('ux_private_content_object_uri')} "
        f"ON {tables.private_content_object} (object_uri)",
        f"CREATE INDEX IF NOT EXISTS {index_name('ix_private_content_owner_expiry')} "
        f"ON {tables.private_content_object} (tenant_id, owner_user_id, expires_at) "
        "WHERE state = 'active'",
        f"CREATE INDEX IF NOT EXISTS {index_name('ix_private_content_workshop_created')} "
        f"ON {tables.private_content_object} (workshop_id, created_at DESC)",
        f"CREATE INDEX IF NOT EXISTS {index_name('ix_private_content_expiry_gc')} "
        f"ON {tables.private_content_object} (expires_at) "
        "WHERE state = 'active' AND expires_at IS NOT NULL",
    ]


def build_strategy_workshop_bootstrap_ddl(schema: Optional[str] = None) -> List[str]:
    return build_strategy_workshop_table_ddl(schema) + build_strategy_workshop_index_ddl(schema)


# --------------------------------------------------------------------------- #
# MemoryWorkshopStore — thread-safe in-memory backend for dev / tests
# --------------------------------------------------------------------------- #

_SESSION_COLS = [
    "workshop_id", "tenant_id", "user_id", "servant_persona_id",
    "openclaw_session_id", "strategy_id", "active_strategy_spec_registry_id",
    "selected_version_id", "active_workshop_version_id",
    "final_strategy_spec_registry_id", "final_workshop_version_id",
    "status", "lock_version", "created_at", "updated_at", "concluded_at",
]
_EVENT_COLS = [
    "event_id", "workshop_id", "sequence_no", "actor_type", "event_type",
    "private_content_ref", "redacted_summary", "payload_refs_json", "trace_id", "created_at",
]
_SNAPSHOT_COLS = [
    "snapshot_id", "workshop_id", "strategy_version_id",
    "state_map_json", "blocking_items_json", "next_question_json", "created_at",
]
_READINESS_COLS = [
    "assessment_id", "workshop_id", "strategy_id", "workshop_version_id",
    "strategy_spec_registry_id", "assessment_version", "assessment_json",
    "assessed_at", "created_at",
]
_CARD_COLS = [
    "card_id", "workshop_id", "sequence_no", "card_type", "status", "title",
    "summary", "payload_json", "source_event_ids_json", "workshop_version_id",
    "strategy_spec_registry_id", "evidence_refs_json", "allowed_actions_json",
    "created_at", "updated_at",
]
_VERSION_LINK_COLS = [
    "workshop_version_id", "workshop_id", "strategy_id",
    "strategy_spec_registry_id", "parent_workshop_version_id",
    "source_event_id", "sequence_no", "document_sha256", "created_by", "created_at",
]
_COMMAND_RECEIPT_COLS = [
    "command_id", "tenant_id", "user_id", "workshop_id", "operation",
    "idempotency_key", "request_hash", "request_payload_json", "request_id",
    "trace_id", "status", "expected_lock_version", "admitted_lock_version",
    "resulting_lock_version", "result_json",
    "canonical_refs_json", "failure_json", "compensation_json", "admitted_at",
    "completed_at", "failed_at", "updated_at",
]
_SESSION_UPDATE_ALLOWLIST = frozenset({
    "strategy_id",
    "active_strategy_spec_registry_id",
    "selected_version_id",
    "active_workshop_version_id",
    "final_strategy_spec_registry_id",
    "final_workshop_version_id",
    "status",
    "concluded_at",
})


def _decode_command_receipt_row(row: Any) -> Dict[str, Any]:
    record = _row_to_dict(row, _COMMAND_RECEIPT_COLS)
    public: Dict[str, Any] = {}
    aliases = {
        "request_payload_json": "request_payload",
        "result_json": "result",
        "canonical_refs_json": "canonical_refs",
        "failure_json": "failure",
        "compensation_json": "compensation",
    }
    for key, value in record.items():
        public_key = aliases.get(key, key)
        if key.endswith("_json"):
            public[public_key] = _decode_json(value)
        else:
            public[public_key] = value
    if public.get("request_payload") is None:
        public["request_payload"] = {}
    if public.get("canonical_refs") is None:
        public["canonical_refs"] = []
    return public


class MemoryWorkshopStore:
    """Thread-safe in-memory store.  Used when backend=off (dev / tests)."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._events: Dict[str, List[Dict[str, Any]]] = {}
        self._snapshots: Dict[str, List[Dict[str, Any]]] = {}
        self._readiness: Dict[str, List[Dict[str, Any]]] = {}
        self._cards: Dict[str, List[Dict[str, Any]]] = {}
        self._version_links: Dict[str, List[Dict[str, Any]]] = {}
        self._command_receipts: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {}
        self._idempotency_keys: Dict[str, bool] = {}
        self._lock = threading.Lock()

    # --- session ---

    def create_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            now = _utc_now()
            row: Dict[str, Any] = {
                "workshop_id": session["workshop_id"],
                "tenant_id": session["tenant_id"],
                "user_id": session["user_id"],
                "servant_persona_id": session.get("servant_persona_id"),
                "openclaw_session_id": session.get("openclaw_session_id"),
                "strategy_id": session.get("strategy_id"),
                "active_strategy_spec_registry_id": session.get("active_strategy_spec_registry_id"),
                "selected_version_id": session.get("selected_version_id"),
                "active_workshop_version_id": session.get(
                    "active_workshop_version_id", session.get("selected_version_id")
                ),
                "final_strategy_spec_registry_id": session.get(
                    "final_strategy_spec_registry_id"
                ),
                "final_workshop_version_id": session.get("final_workshop_version_id"),
                "status": session.get("status", "open"),
                "lock_version": 1,
                "created_at": session.get("created_at", now),
                "updated_at": session.get("updated_at", now),
                "concluded_at": session.get("concluded_at"),
            }
            self._sessions[row["workshop_id"]] = row
            return dict(row)

    def rollback_create_session(self, workshop_id: str, *, idempotency_scope: str,
                                idempotency_key: str) -> None:
        """Remove every trace of a failed create-workshop attempt."""
        with self._lock:
            self._events.pop(workshop_id, None)
            self._version_links.pop(workshop_id, None)
            for composite in [
                composite
                for composite in self._command_receipts
                if composite[2] == workshop_id
            ]:
                self._command_receipts.pop(composite, None)
            self._sessions.pop(workshop_id, None)
            self._idempotency_keys.pop(f"{idempotency_scope}:{idempotency_key}", None)

    def get_session(self, workshop_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._sessions.get(workshop_id)
            return dict(row) if row else None

    def update_session_lock_version(self, workshop_id: str) -> int:
        with self._lock:
            session = self._sessions.get(workshop_id)
            if session is None:
                return 1
            session["lock_version"] = session.get("lock_version", 1) + 1
            session["updated_at"] = _utc_now()
            return session["lock_version"]

    def append_event_cas(
        self,
        workshop_id: str,
        expected_lock_version: int,
        event: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
        """Atomically append an event and bump lock_version if version matches.

        Returns (event_dict, new_lock_version) on success.
        Returns (None, current_lock_version) on version conflict.
        Returns (None, None) if workshop not found.

        The entire check + append + bump happens under a single lock, preventing
        concurrent same-ETag writes from both succeeding.
        """
        with self._lock:
            session = self._sessions.get(workshop_id)
            if session is None:
                return (None, None)
            current_version = session.get("lock_version", 1)
            if current_version != expected_lock_version:
                return (None, current_version)
            bucket = self._events.setdefault(workshop_id, [])
            ev: Dict[str, Any] = {
                "event_id": event.get("event_id") or _new_id(),
                "workshop_id": workshop_id,
                "sequence_no": len(bucket) + 1,
                "actor_type": event["actor_type"],
                "event_type": event["event_type"],
                "private_content_ref": event.get("private_content_ref"),
                "redacted_summary": event.get("redacted_summary"),
                "payload_refs_json": event.get("payload_refs_json"),
                "trace_id": event.get("trace_id"),
                "created_at": event.get("created_at", _utc_now()),
            }
            bucket.append(ev)
            session["lock_version"] = current_version + 1
            session["updated_at"] = _utc_now()
            return (dict(ev), current_version + 1)

    def check_and_record_idempotency_key(self, scope: str, key: str) -> bool:
        """Return True if the key was already seen (duplicate); False if it is new.

        The key is recorded on first call so subsequent calls return True.
        scope should encode user+tenant+endpoint to avoid cross-user conflicts.
        """
        composite = f"{scope}:{key}"
        with self._lock:
            if composite in self._idempotency_keys:
                return True
            self._idempotency_keys[composite] = True
            return False

    def list_sessions(
        self,
        *,
        user_id: str,
        tenant_id: str,
        status: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 20,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        with self._lock:
            rows = [
                s for s in self._sessions.values()
                if s["user_id"] == user_id
                and s["tenant_id"] == tenant_id
                and (status is None or s["status"] == status)
            ]
            rows.sort(key=lambda r: r["created_at"])
            if cursor:
                start = next(
                    (i + 1 for i, r in enumerate(rows) if r["workshop_id"] == cursor),
                    0,
                )
                rows = rows[start:]
            page = [dict(r) for r in rows[:limit]]
            next_cursor = page[-1]["workshop_id"] if len(rows) > limit else None
            return page, next_cursor

    # --- immutable StrategySpec version links ---

    def list_version_links(self, workshop_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                _deep_copy_dict(row)
                for row in sorted(
                    self._version_links.get(workshop_id, []),
                    key=lambda item: int(item["sequence_no"]),
                )
            ]

    def get_version_link(
        self,
        workshop_id: str,
        workshop_version_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = next(
                (
                    item
                    for item in self._version_links.get(workshop_id, [])
                    if item["workshop_version_id"] == workshop_version_id
                ),
                None,
            )
            return _deep_copy_dict(row) if row is not None else None

    def ensure_current_version_link(
        self,
        *,
        workshop_id: str,
        strategy_id: str,
        strategy_spec_registry_id: str,
        document_sha256: str,
    ) -> Dict[str, Any]:
        """Backfill the active Registry pointer into one immutable link.

        Legacy sessions stored only ``active_strategy_spec_registry_id``.  The
        deterministic id and session timestamps make repeated startup/readback
        migration byte-stable without advancing the workshop ETag.
        """

        digest = _document_sha256(document_sha256)
        with self._lock:
            session = self._sessions.get(workshop_id)
            if session is None:
                raise KeyError(workshop_id)
            active_registry_id = str(
                session.get("active_strategy_spec_registry_id") or ""
            )
            if active_registry_id != strategy_spec_registry_id:
                raise WorkshopVersionProjectionConflict(
                    "active StrategySpec Registry identity changed during backfill"
                )
            session_strategy_id = str(session.get("strategy_id") or "")
            if session_strategy_id and session_strategy_id != strategy_id:
                raise WorkshopVersionProjectionConflict(
                    "workshop and Registry strategy identities disagree"
                )

            links = self._version_links.setdefault(workshop_id, [])
            existing = next(
                (
                    item
                    for item in links
                    if item["strategy_spec_registry_id"] == strategy_spec_registry_id
                ),
                None,
            )
            if existing is None:
                pointer_ids = {
                    str(value)
                    for value in (
                        session.get("active_workshop_version_id"),
                        session.get("selected_version_id"),
                    )
                    if value
                }
                if len(pointer_ids) > 1:
                    raise WorkshopVersionProjectionConflict(
                        "legacy selected-version aliases disagree"
                    )
                workshop_version_id = next(iter(pointer_ids), None) or (
                    _legacy_workshop_version_id(
                        workshop_id, strategy_spec_registry_id
                    )
                )
                collision = next(
                    (
                        item
                        for item in links
                        if item["workshop_version_id"] == workshop_version_id
                    ),
                    None,
                )
                if collision is not None:
                    raise WorkshopVersionProjectionConflict(
                        "selected workshop version points to another Registry version"
                    )
                latest = max(
                    links,
                    key=lambda item: int(item["sequence_no"]),
                    default=None,
                )
                existing = {
                    "workshop_version_id": workshop_version_id,
                    "workshop_id": workshop_id,
                    "strategy_id": strategy_id,
                    "strategy_spec_registry_id": strategy_spec_registry_id,
                    "parent_workshop_version_id": (
                        latest["workshop_version_id"] if latest else None
                    ),
                    "source_event_id": None,
                    "sequence_no": int(latest["sequence_no"]) + 1 if latest else 1,
                    "document_sha256": digest,
                    "created_by": session["user_id"],
                    "created_at": session["created_at"],
                }
                links.append(existing)
            else:
                self._validate_version_link_identity(
                    existing,
                    strategy_id=strategy_id,
                    document_sha256=digest,
                )
                if existing.get("document_sha256") is None:
                    existing["document_sha256"] = digest

            link_id = existing["workshop_version_id"]
            for pointer_name in (
                "active_workshop_version_id",
                "selected_version_id",
            ):
                pointer = session.get(pointer_name)
                if pointer and pointer != link_id:
                    raise WorkshopVersionProjectionConflict(
                        f"{pointer_name} disagrees with active Registry version"
                    )
                session[pointer_name] = link_id
            if not session.get("strategy_id"):
                session["strategy_id"] = strategy_id
            return _deep_copy_dict(existing)

    @staticmethod
    def _validate_version_link_identity(
        link: Dict[str, Any],
        *,
        strategy_id: str,
        document_sha256: str,
    ) -> None:
        if str(link.get("strategy_id") or "") != strategy_id:
            raise WorkshopVersionProjectionConflict(
                "workshop version strategy identity is immutable"
            )
        stored_digest = str(link.get("document_sha256") or "")
        if stored_digest and stored_digest != document_sha256:
            raise WorkshopVersionProjectionConflict(
                "authoritative StrategySpec bytes changed for an immutable version"
            )

    def ensure_version_link_digest(
        self,
        *,
        workshop_id: str,
        workshop_version_id: str,
        strategy_id: str,
        document_sha256: str,
    ) -> Dict[str, Any]:
        digest = _document_sha256(document_sha256)
        with self._lock:
            link = next(
                (
                    item
                    for item in self._version_links.get(workshop_id, [])
                    if item["workshop_version_id"] == workshop_version_id
                ),
                None,
            )
            if link is None:
                raise KeyError(workshop_version_id)
            self._validate_version_link_identity(
                link,
                strategy_id=strategy_id,
                document_sha256=digest,
            )
            if link.get("document_sha256") is None:
                link["document_sha256"] = digest
            return _deep_copy_dict(link)

    # --- durable governed-command ledger ---

    @staticmethod
    def _command_key(
        tenant_id: str,
        user_id: str,
        workshop_id: str,
        operation: str,
        idempotency_key: str,
    ) -> Tuple[str, str, str, str, str]:
        return (tenant_id, user_id, workshop_id, operation, idempotency_key)

    def admit_command(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        expected_lock_version: int,
        request_payload: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Durably admit a command before any downstream side effect.

        Exact replay is resolved before ETag and lifecycle checks.  Therefore a
        client can recover a completed, failed, or in-flight receipt after a
        timeout even when the workshop lock has advanced.
        """

        if not operation or not idempotency_key or not request_hash:
            raise ValueError("operation, idempotency_key, and request_hash are required")
        composite = self._command_key(
            tenant_id, user_id, workshop_id, operation, idempotency_key
        )
        with self._lock:
            existing = self._command_receipts.get(composite)
            if existing is not None:
                outcome = (
                    "replay"
                    if existing["request_hash"] == request_hash
                    else "idempotency_conflict"
                )
                return {
                    "outcome": outcome,
                    "receipt": _deep_copy_dict(existing),
                    "current_lock_version": self._sessions.get(workshop_id, {}).get(
                        "lock_version"
                    ),
                }

            session = self._sessions.get(workshop_id)
            if session is None:
                return {"outcome": "not_found", "receipt": None}
            if session["tenant_id"] != tenant_id or session["user_id"] != user_id:
                return {"outcome": "scope_mismatch", "receipt": None}
            current_lock_version = int(session.get("lock_version", 1))
            if session.get("status") in TERMINAL_WORKSHOP_STATUSES:
                return {
                    "outcome": "terminal",
                    "receipt": None,
                    "workshop_status": session["status"],
                    "current_lock_version": current_lock_version,
                }
            if current_lock_version != int(expected_lock_version):
                return {
                    "outcome": "stale",
                    "receipt": None,
                    "current_lock_version": current_lock_version,
                }

            now = _utc_now()
            new_lock_version = current_lock_version + 1
            receipt: Dict[str, Any] = {
                "command_id": _new_id(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "workshop_id": workshop_id,
                "operation": operation,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "request_payload": _redact_command_payload(request_payload or {}),
                "request_id": request_id,
                "trace_id": trace_id,
                "status": "admitted",
                "expected_lock_version": current_lock_version,
                "admitted_lock_version": new_lock_version,
                "resulting_lock_version": new_lock_version,
                "result": None,
                "canonical_refs": [],
                "failure": None,
                "compensation": None,
                "admitted_at": now,
                "completed_at": None,
                "failed_at": None,
                "updated_at": now,
            }
            # The receipt write and lock advance are one critical section: an
            # admitted command cannot become externally visible without both.
            self._command_receipts[composite] = receipt
            session["lock_version"] = new_lock_version
            session["updated_at"] = now
            return {
                "outcome": "admitted",
                "receipt": _deep_copy_dict(receipt),
                "current_lock_version": new_lock_version,
            }

    def get_command_receipt(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
    ) -> Optional[Dict[str, Any]]:
        composite = self._command_key(
            tenant_id, user_id, workshop_id, operation, idempotency_key
        )
        with self._lock:
            receipt = self._command_receipts.get(composite)
            return _deep_copy_dict(receipt) if receipt is not None else None

    def _resolve_compensation_locked(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
        resolution: Dict[str, Any],
    ) -> None:
        """Merge a resolution into a failed source receipt under self._lock.

        Bookkeeping over lineage: a missing or non-failed source is skipped so
        the successor's own terminal write stays authoritative.
        """

        composite = self._command_key(
            tenant_id, user_id, workshop_id, operation, idempotency_key
        )
        receipt = self._command_receipts.get(composite)
        if receipt is None or receipt["status"] != "failed":
            return
        compensation = dict(receipt.get("compensation") or {})
        compensation.update(deepcopy(dict(resolution)))
        receipt["compensation"] = compensation
        receipt["updated_at"] = _utc_now()

    def complete_command(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        result: Dict[str, Any],
        canonical_refs: Optional[Any] = None,
        version_link: Optional[Dict[str, Any]] = None,
        session_updates: Optional[Dict[str, Any]] = None,
        event: Optional[Dict[str, Any]] = None,
        resolve_compensation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Complete an admitted command and its authoritative readback atomically.

        ``resolve_compensation`` resolves the adopted lineage source
        (``{"operation", "idempotency_key", "resolution"}``) in the same
        critical section as the successor's completion, so a committed
        successor and a still-resumable source can never coexist.
        """

        composite = self._command_key(
            tenant_id, user_id, workshop_id, operation, idempotency_key
        )
        with self._lock:
            receipt = self._command_receipts.get(composite)
            if receipt is None:
                return {"outcome": "not_found", "receipt": None}
            if receipt["request_hash"] != request_hash:
                return {
                    "outcome": "idempotency_conflict",
                    "receipt": _deep_copy_dict(receipt),
                }
            if receipt["status"] == "completed":
                return {"outcome": "replay", "receipt": _deep_copy_dict(receipt)}
            if receipt["status"] != "admitted":
                return {
                    "outcome": "invalid_state",
                    "receipt": _deep_copy_dict(receipt),
                }

            session = self._sessions.get(workshop_id)
            if session is None:
                return {"outcome": "not_found", "receipt": None}

            updates = dict(session_updates or {})
            unknown_updates = set(updates) - _SESSION_UPDATE_ALLOWLIST
            if unknown_updates:
                raise ValueError(
                    f"Unsupported session update fields: {sorted(unknown_updates)!r}"
                )
            if "status" in updates and updates["status"] not in WORKSHOP_STATUSES:
                raise ValueError(f"Unsupported workshop status: {updates['status']!r}")
            if "active_workshop_version_id" in updates:
                updates.setdefault(
                    "selected_version_id", updates["active_workshop_version_id"]
                )
            elif "selected_version_id" in updates:
                updates["active_workshop_version_id"] = updates["selected_version_id"]

            prepared_link: Optional[Dict[str, Any]] = None
            links = self._version_links.setdefault(workshop_id, [])
            if version_link is not None:
                required = {
                    "workshop_version_id", "strategy_id",
                    "strategy_spec_registry_id", "document_sha256", "created_by",
                }
                missing = required - set(version_link)
                if missing:
                    raise ValueError(f"Missing version link fields: {sorted(missing)!r}")
                prepared_link = {
                    "workshop_version_id": version_link["workshop_version_id"],
                    "workshop_id": workshop_id,
                    "strategy_id": version_link["strategy_id"],
                    "strategy_spec_registry_id": version_link[
                        "strategy_spec_registry_id"
                    ],
                    "parent_workshop_version_id": version_link.get(
                        "parent_workshop_version_id"
                    ),
                    "source_event_id": version_link.get("source_event_id"),
                    "sequence_no": int(version_link.get("sequence_no") or (len(links) + 1)),
                    "document_sha256": _document_sha256(
                        version_link["document_sha256"]
                    ),
                    "created_by": version_link["created_by"],
                    "created_at": version_link.get("created_at") or _utc_now(),
                }
                duplicate = next(
                    (
                        row
                        for row in links
                        if row["workshop_version_id"]
                        == prepared_link["workshop_version_id"]
                        or row["strategy_spec_registry_id"]
                        == prepared_link["strategy_spec_registry_id"]
                        or int(row["sequence_no"]) == prepared_link["sequence_no"]
                    ),
                    None,
                )
                if duplicate is not None and duplicate != prepared_link:
                    return {
                        "outcome": "version_conflict",
                        "receipt": _deep_copy_dict(receipt),
                    }
                if duplicate is not None:
                    prepared_link = None

            prepared_event: Optional[Dict[str, Any]] = None
            if event is not None:
                bucket = self._events.setdefault(workshop_id, [])
                requested_event_id = event.get("event_id") or _new_id()
                if any(row["event_id"] == requested_event_id for row in bucket):
                    # Parity with the Postgres event_id primary key: a
                    # colliding digest-derived event id must abort the commit
                    # instead of silently duplicating the event stream.
                    raise ValueError(
                        f"workshop event_id already exists: {requested_event_id!r}"
                    )
                prepared_event = {
                    "event_id": requested_event_id,
                    "workshop_id": workshop_id,
                    "sequence_no": len(bucket) + 1,
                    "actor_type": event["actor_type"],
                    "event_type": event["event_type"],
                    "private_content_ref": event.get("private_content_ref"),
                    "redacted_summary": event.get("redacted_summary"),
                    "payload_refs_json": event.get("payload_refs_json"),
                    "trace_id": event.get("trace_id") or receipt.get("trace_id"),
                    "created_at": event.get("created_at") or _utc_now(),
                }

            now = _utc_now()
            if prepared_link is not None:
                links.append(prepared_link)
            for field, value in updates.items():
                session[field] = value
            session["updated_at"] = now
            if prepared_event is not None:
                self._events.setdefault(workshop_id, []).append(prepared_event)
            receipt.update(
                {
                    "status": "completed",
                    "result": deepcopy(result),
                    "canonical_refs": deepcopy(canonical_refs or []),
                    "completed_at": now,
                    "updated_at": now,
                }
            )
            if resolve_compensation is not None:
                self._resolve_compensation_locked(
                    workshop_id=workshop_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    operation=str(
                        resolve_compensation.get("operation") or operation
                    ),
                    idempotency_key=str(
                        resolve_compensation.get("idempotency_key") or ""
                    ),
                    resolution=dict(resolve_compensation.get("resolution") or {}),
                )
            return {
                "outcome": "completed",
                "receipt": _deep_copy_dict(receipt),
                "version_link": (
                    _deep_copy_dict(prepared_link)
                    if prepared_link is not None
                    else None
                ),
                "event": _deep_copy_dict(prepared_event) if prepared_event else None,
                "current_lock_version": session["lock_version"],
            }

    def fail_command(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        failure: Dict[str, Any],
        compensation: Optional[Dict[str, Any]] = None,
        canonical_refs: Optional[Any] = None,
        event: Optional[Dict[str, Any]] = None,
        resolve_compensation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist failure/compensation without rolling the workshop lock back.

        ``canonical_refs`` records downstream identifiers that were already
        created before the failure (partial effects), keeping the failed
        receipt truthful about what exists downstream.
        ``resolve_compensation`` resolves (or releases the claim on) the
        adopted lineage source in the same critical section, so the lineage
        moves to exactly one live receipt.
        """

        composite = self._command_key(
            tenant_id, user_id, workshop_id, operation, idempotency_key
        )
        with self._lock:
            receipt = self._command_receipts.get(composite)
            if receipt is None:
                return {"outcome": "not_found", "receipt": None}
            if receipt["request_hash"] != request_hash:
                return {
                    "outcome": "idempotency_conflict",
                    "receipt": _deep_copy_dict(receipt),
                }
            if receipt["status"] == "failed":
                return {"outcome": "replay", "receipt": _deep_copy_dict(receipt)}
            if receipt["status"] != "admitted":
                return {
                    "outcome": "invalid_state",
                    "receipt": _deep_copy_dict(receipt),
                }

            now = _utc_now()
            receipt.update(
                {
                    "status": "failed",
                    "failure": deepcopy(failure),
                    "compensation": deepcopy(compensation),
                    "failed_at": now,
                    "updated_at": now,
                }
            )
            if canonical_refs is not None:
                receipt["canonical_refs"] = deepcopy(canonical_refs)
            prepared_event: Optional[Dict[str, Any]] = None
            if event is not None:
                bucket = self._events.setdefault(workshop_id, [])
                prepared_event = {
                    "event_id": event.get("event_id") or _new_id(),
                    "workshop_id": workshop_id,
                    "sequence_no": len(bucket) + 1,
                    "actor_type": event["actor_type"],
                    "event_type": event["event_type"],
                    "private_content_ref": event.get("private_content_ref"),
                    "redacted_summary": event.get("redacted_summary"),
                    "payload_refs_json": event.get("payload_refs_json"),
                    "trace_id": event.get("trace_id") or receipt.get("trace_id"),
                    "created_at": event.get("created_at") or now,
                }
                bucket.append(prepared_event)
            if resolve_compensation is not None:
                self._resolve_compensation_locked(
                    workshop_id=workshop_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    operation=str(
                        resolve_compensation.get("operation") or operation
                    ),
                    idempotency_key=str(
                        resolve_compensation.get("idempotency_key") or ""
                    ),
                    resolution=dict(resolve_compensation.get("resolution") or {}),
                )
            return {
                "outcome": "failed",
                "receipt": _deep_copy_dict(receipt),
                "event": _deep_copy_dict(prepared_event) if prepared_event else None,
                "current_lock_version": self._sessions.get(workshop_id, {}).get(
                    "lock_version"
                ),
            }

    def find_resumable_command(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        request_hash: str,
    ) -> Optional[Dict[str, Any]]:
        """Latest failed receipt of the same logical request with unresolved
        resumable compensation (recorded partial downstream effects)."""

        with self._lock:
            candidates = [
                receipt
                for receipt in self._command_receipts.values()
                if receipt["workshop_id"] == workshop_id
                and receipt["tenant_id"] == tenant_id
                and receipt["user_id"] == user_id
                and receipt["operation"] == operation
                and receipt["request_hash"] == request_hash
                and receipt["status"] == "failed"
                and isinstance(receipt.get("compensation"), dict)
                and receipt["compensation"].get("resumable") is True
                and not receipt["compensation"].get("resolved_at")
            ]
            if not candidates:
                return None
            candidates.sort(
                key=lambda row: str(row.get("failed_at") or row.get("updated_at") or "")
            )
            return _deep_copy_dict(candidates[-1])

    def claim_resumable_command(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        request_hash: str,
        claimed_by_idempotency_key: str,
    ) -> Optional[Dict[str, Any]]:
        """Atomically claim the latest resumable lineage for one successor.

        The find and the claim stamp are one critical section: while a
        successor holds the claim, a concurrent new-key retry gets ``None``
        and must open fresh downstream resources instead of adopting shared
        ones.  Re-claim by the same successor key succeeds so an exact
        command replay can recover after a crash mid-flight.  The claim is
        released or resolved transactionally by the successor's terminal
        ``complete_command`` / ``fail_command`` write.
        """

        if not claimed_by_idempotency_key:
            raise ValueError("claimed_by_idempotency_key is required")
        with self._lock:
            candidates = [
                receipt
                for receipt in self._command_receipts.values()
                if receipt["workshop_id"] == workshop_id
                and receipt["tenant_id"] == tenant_id
                and receipt["user_id"] == user_id
                and receipt["operation"] == operation
                and receipt["request_hash"] == request_hash
                and receipt["status"] == "failed"
                and isinstance(receipt.get("compensation"), dict)
                and receipt["compensation"].get("resumable") is True
                and not receipt["compensation"].get("resolved_at")
            ]
            if not candidates:
                return None
            candidates.sort(
                key=lambda row: str(row.get("failed_at") or row.get("updated_at") or "")
            )
            receipt = candidates[-1]
            claimed_by = str(
                receipt["compensation"].get("claimed_by_idempotency_key") or ""
            )
            if claimed_by and claimed_by != claimed_by_idempotency_key:
                # The whole lineage chain shares one downstream digest, so a
                # claim on the latest receipt blocks adoption outright rather
                # than falling back to an older receipt of the same chain.
                return None
            compensation = dict(receipt["compensation"])
            compensation["claimed_by_idempotency_key"] = claimed_by_idempotency_key
            compensation["claimed_at"] = _utc_now()
            receipt["compensation"] = compensation
            receipt["updated_at"] = _utc_now()
            return _deep_copy_dict(receipt)

    def resolve_command_compensation(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
        resolution: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge a resolution (resumed / cancelled) into a failed receipt's
        compensation so partial effects are not adopted twice."""

        composite = self._command_key(
            tenant_id, user_id, workshop_id, operation, idempotency_key
        )
        with self._lock:
            receipt = self._command_receipts.get(composite)
            if receipt is None:
                return {"outcome": "not_found", "receipt": None}
            if receipt["status"] != "failed":
                return {
                    "outcome": "invalid_state",
                    "receipt": _deep_copy_dict(receipt),
                }
            compensation = dict(receipt.get("compensation") or {})
            compensation.update(deepcopy(dict(resolution)))
            receipt["compensation"] = compensation
            receipt["updated_at"] = _utc_now()
            return {"outcome": "resolved", "receipt": _deep_copy_dict(receipt)}

    # --- event ---

    def create_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            wid = event["workshop_id"]
            bucket = self._events.setdefault(wid, [])
            requested_id = event.get("event_id")
            if requested_id:
                existing = next((row for row in bucket if row["event_id"] == requested_id), None)
                if existing is not None:
                    comparable = {
                        "workshop_id": wid,
                        "actor_type": event["actor_type"],
                        "event_type": event["event_type"],
                        "private_content_ref": event.get("private_content_ref"),
                        "redacted_summary": event.get("redacted_summary"),
                        "payload_refs_json": event.get("payload_refs_json"),
                        "trace_id": event.get("trace_id"),
                    }
                    if any(existing.get(key) != value for key, value in comparable.items()):
                        raise ValueError("event_id reused with a different payload")
                    return dict(existing)
            ev: Dict[str, Any] = {
                "event_id": event.get("event_id") or _new_id(),
                "workshop_id": wid,
                "sequence_no": len(bucket) + 1,
                "actor_type": event["actor_type"],
                "event_type": event["event_type"],
                "private_content_ref": event.get("private_content_ref"),
                "redacted_summary": event.get("redacted_summary"),
                "payload_refs_json": event.get("payload_refs_json"),
                "trace_id": event.get("trace_id"),
                "created_at": event.get("created_at", _utc_now()),
            }
            bucket.append(ev)
            return dict(ev)

    def list_events(
        self,
        workshop_id: str,
        *,
        after_sequence: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            evs = list(self._events.get(workshop_id, []))
            if after_sequence is not None:
                evs = [e for e in evs if e["sequence_no"] > after_sequence]
            return [dict(e) for e in evs]

    # --- completeness snapshot ---

    def create_completeness_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            wid = snapshot["workshop_id"]
            snap: Dict[str, Any] = {
                "snapshot_id": snapshot.get("snapshot_id") or _new_id(),
                "workshop_id": wid,
                "strategy_version_id": snapshot.get("strategy_version_id"),
                "state_map_json": snapshot.get("state_map_json"),
                "blocking_items_json": snapshot.get("blocking_items_json"),
                "next_question_json": snapshot.get("next_question_json"),
                "created_at": snapshot.get("created_at", _utc_now()),
            }
            self._snapshots.setdefault(wid, []).append(snap)
            return dict(snap)

    def get_latest_completeness_snapshot(
        self, workshop_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            snaps = self._snapshots.get(workshop_id, [])
            return dict(snaps[-1]) if snaps else None

    # --- readiness assessment ---

    def create_readiness_assessment(self, assessment: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            wid = assessment["workshop_id"]
            bucket = self._readiness.setdefault(wid, [])
            version = int(assessment.get("assessment_version") or (len(bucket) + 1))
            now = assessment.get("assessed_at", _utc_now())
            row: Dict[str, Any] = {
                "spec_version": assessment.get("spec_version", "1.0"),
                "assessment_id": assessment.get("assessment_id") or _new_readiness_id(wid, version),
                "workshop_id": wid,
                "strategy_id": assessment["strategy_id"],
                "workshop_version_id": assessment["workshop_version_id"],
                "strategy_spec_registry_id": assessment["strategy_spec_registry_id"],
                "assessment_version": version,
                "gates": assessment.get("gates", []),
                "highest_ready_gate": assessment.get("highest_ready_gate"),
                "staleness_reasons": assessment.get("staleness_reasons", []),
                "assessed_at": now,
                "evidence_refs": assessment.get("evidence_refs", []),
            }
            if assessment.get("valid_until") is not None:
                row["valid_until"] = assessment["valid_until"]
            bucket.append(row)
            return dict(row)

    def get_latest_readiness_assessment(self, workshop_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            assessments = self._readiness.get(workshop_id, [])
            return dict(assessments[-1]) if assessments else None

    # --- typed workshop cards ---

    def record_workshop_card(self, card: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            wid = card["workshop_id"]
            bucket = self._cards.setdefault(wid, [])
            existing_idx = next(
                (idx for idx, item in enumerate(bucket) if item.get("card_id") == card.get("card_id")),
                None,
            )
            sequence_no = int(card.get("sequence_no") or (len(bucket) + 1))
            now = card.get("created_at", _utc_now())
            row: Dict[str, Any] = {
                "spec_version": card.get("spec_version", "1.0"),
                "card_id": card.get("card_id") or f"card_{_safe_id_fragment(wid)}_{sequence_no}",
                "card_type": card["card_type"],
                "workshop_id": wid,
                "sequence_no": sequence_no,
                "source_event_ids": list(card.get("source_event_ids") or []),
                "workshop_version_id": card.get("workshop_version_id"),
                "strategy_spec_registry_id": card.get("strategy_spec_registry_id"),
                "status": card.get("status", "informational"),
                "title": card["title"],
                "summary": card.get("summary"),
                "payload": card.get("payload", {}),
                "evidence_refs": list(card.get("evidence_refs") or []),
                "allowed_actions": dict(card.get("allowed_actions") or {}),
                "created_at": now,
                "updated_at": card.get("updated_at"),
            }
            if existing_idx is None:
                bucket.append(row)
            else:
                bucket[existing_idx] = row
            return dict(row)

    def list_workshop_cards(
        self,
        workshop_id: str,
        *,
        after_sequence: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            cards = list(self._cards.get(workshop_id, []))
            if after_sequence is not None:
                cards = [c for c in cards if int(c.get("sequence_no", 0)) > after_sequence]
            cards.sort(key=lambda c: int(c.get("sequence_no", 0)))
            return [dict(c) for c in cards]


# --------------------------------------------------------------------------- #
# Frozen deep-closure bootstrap wrapper (kept for contract validation)
# --------------------------------------------------------------------------- #

class PostgresStrategyWorkshopStore:
    """Bootstrap wrapper for the frozen five-table deep-closure contract.

    Runtime callers should use :class:`PostgresWorkshopStore`, which includes
    additive readiness/card/idempotency/command-receipt tables and migrations.
    """

    def __init__(
        self,
        *,
        dsn: str,
        schema: Optional[str] = None,
        bootstrap: bool = True,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required")
        self.dsn = dsn
        self.schema = _clean_schema(schema)
        if bootstrap:
            self.bootstrap()

    def _connect(self) -> Any:
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required for PostgresStrategyWorkshopStore"
            ) from exc
        return psycopg.connect(self.dsn)

    def bootstrap(self) -> None:
        with self._connect() as conn:
            if self.schema:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_identifier(self.schema)}")
            for statement in build_strategy_workshop_bootstrap_ddl(self.schema):
                conn.execute(statement)


# --------------------------------------------------------------------------- #
# PostgresWorkshopStore — production Postgres backend
# --------------------------------------------------------------------------- #

class PostgresWorkshopStore:
    """Postgres-backed workshop store.

    Bootstrap creates the three tables and their §22.6 indexes on first run.
    Restricted roles that cannot CREATE SCHEMA are tolerated if the schema
    already exists.
    """

    def __init__(self, *, dsn: str, schema: str = DEFAULT_SCHEMA) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for PostgresWorkshopStore")
        self.dsn = dsn
        self.schema = _clean_schema(schema)
        if not self.schema:
            raise ValueError("Postgres schema is required for PostgresWorkshopStore")
        q = _quote_identifier(self.schema)
        self._st = f'{q}."strategy_workshop_session"'
        self._et = f'{q}."strategy_workshop_event"'
        self._vlt = f'{q}."strategy_workshop_version_link"'
        self._crt = f'{q}."strategy_workshop_command_receipt"'
        self._cst = f'{q}."strategy_completeness_snapshot"'
        self._rat = f'{q}."strategy_readiness_assessment"'
        self._wct = f'{q}."strategy_workshop_card"'
        self._ikt = f'{q}."workshop_idempotency_key"'
        self._bootstrap()

    # -- internal --

    def _connect(self) -> Any:
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required for PostgresWorkshopStore"
            ) from exc
        return psycopg.connect(self.dsn)

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            # Create schema — tolerate restricted-role 42501
            try:
                conn.execute(
                    f"CREATE SCHEMA IF NOT EXISTS {_quote_identifier(self.schema)}"
                )
            except Exception as exc:
                if getattr(exc, "sqlstate", "") != "42501":
                    raise
                if hasattr(conn, "rollback"):
                    conn.rollback()
                cur = conn.execute(
                    "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                    (self.schema,),
                )
                if not (cur.fetchone()):
                    raise

            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._st} (
                    workshop_id                      TEXT PRIMARY KEY,
                    tenant_id                        TEXT NOT NULL,
                    user_id                          TEXT NOT NULL,
                    servant_persona_id               TEXT,
                    openclaw_session_id              TEXT,
                    strategy_id                      TEXT,
                    active_strategy_spec_registry_id TEXT,
                    selected_version_id              TEXT,
                    active_workshop_version_id       TEXT,
                    final_strategy_spec_registry_id  TEXT,
                    final_workshop_version_id        TEXT,
                    status                           TEXT NOT NULL DEFAULT 'open',
                    lock_version                     BIGINT NOT NULL DEFAULT 1,
                    created_at                       TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at                       TIMESTAMPTZ NOT NULL DEFAULT now(),
                    concluded_at                     TIMESTAMPTZ,
                    CONSTRAINT ck_strategy_workshop_session_status
                        CHECK (status IN ('open','in_review','concluded','archived'))
                )
            """)
            # Additive migration for databases bootstrapped by the earlier BFF
            # implementation.  NOT VALID preserves deployability if legacy data
            # needs repair while still enforcing the status boundary for writes.
            conn.execute(f"""
                ALTER TABLE {self._st}
                    ADD COLUMN IF NOT EXISTS active_workshop_version_id TEXT,
                    ADD COLUMN IF NOT EXISTS final_strategy_spec_registry_id TEXT,
                    ADD COLUMN IF NOT EXISTS final_workshop_version_id TEXT,
                    ADD COLUMN IF NOT EXISTS concluded_at TIMESTAMPTZ
            """)
            conn.execute(f"""
                ALTER TABLE {self._st}
                    ALTER COLUMN lock_version TYPE BIGINT
            """)
            conn.execute(f"""
                UPDATE {self._st}
                   SET active_workshop_version_id = selected_version_id
                 WHERE active_workshop_version_id IS NULL
                   AND selected_version_id IS NOT NULL
            """)
            conn.execute(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                          FROM pg_constraint
                         WHERE conname = 'ck_strategy_workshop_session_status'
                           AND conrelid = '"{self.schema}"."strategy_workshop_session"'::regclass
                    ) THEN
                        ALTER TABLE {self._st}
                            ADD CONSTRAINT ck_strategy_workshop_session_status
                            CHECK (status IN ('open','in_review','concluded','archived'))
                            NOT VALID;
                    END IF;
                END $$
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_ws_session_user_tenant
                    ON {self._st} (user_id, tenant_id, created_at DESC)
            """)

            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._et} (
                    event_id            TEXT PRIMARY KEY,
                    workshop_id         TEXT NOT NULL
                        REFERENCES {self._st}(workshop_id),
                    sequence_no         INTEGER NOT NULL,
                    actor_type          TEXT NOT NULL,
                    event_type          TEXT NOT NULL,
                    private_content_ref TEXT,
                    redacted_summary    TEXT,
                    payload_refs_json   JSONB,
                    trace_id            TEXT,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT uq_ws_event_seq UNIQUE (workshop_id, sequence_no)
                )
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_ws_event_workshop_created
                    ON {self._et} (workshop_id, created_at)
            """)
            # Add named FKs for legacy tables without forcing a transaction
            # rollback on every restart when the constraint already exists.
            conn.execute(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                         WHERE conname = 'fk_ws_event_session'
                           AND conrelid = '"{self.schema}"."strategy_workshop_event"'::regclass
                    ) THEN
                        ALTER TABLE {self._et}
                            ADD CONSTRAINT fk_ws_event_session
                            FOREIGN KEY (workshop_id)
                            REFERENCES {self._st}(workshop_id);
                    END IF;
                END $$
            """)

            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._vlt} (
                    workshop_version_id          TEXT PRIMARY KEY,
                    workshop_id                  TEXT NOT NULL
                        REFERENCES {self._st}(workshop_id),
                    strategy_id                  TEXT NOT NULL,
                    strategy_spec_registry_id    TEXT NOT NULL,
                    parent_workshop_version_id   TEXT,
                    source_event_id              TEXT,
                    sequence_no                  BIGINT NOT NULL,
                    document_sha256              CHAR(64) NOT NULL,
                    created_by                   TEXT NOT NULL,
                    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT uq_ws_version_sequence
                        UNIQUE (workshop_id, sequence_no),
                    CONSTRAINT uq_ws_registry_version
                        UNIQUE (workshop_id, strategy_spec_registry_id),
                    CONSTRAINT ck_ws_version_document_sha256
                        CHECK (document_sha256 ~ '^[0-9a-f]{{64}}$')
                )
            """)
            # Legacy rows predate immutable StrategySpec digests.  Keep the
            # migration column nullable until authoritative Registry readback
            # deterministically hydrates each row; every new write requires a
            # validated digest in ``complete_command``.
            conn.execute(f"""
                ALTER TABLE {self._vlt}
                    ADD COLUMN IF NOT EXISTS document_sha256 CHAR(64)
            """)
            conn.execute(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                          FROM pg_constraint
                         WHERE conname = 'ck_ws_version_document_sha256'
                           AND conrelid = '"{self.schema}"."strategy_workshop_version_link"'::regclass
                    ) THEN
                        ALTER TABLE {self._vlt}
                            ADD CONSTRAINT ck_ws_version_document_sha256
                            CHECK (
                                document_sha256 IS NULL
                                OR document_sha256 ~ '^[0-9a-f]{{64}}$'
                            ) NOT VALID;
                    END IF;
                END $$
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_ws_version_workshop_sequence
                    ON {self._vlt} (workshop_id, sequence_no)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_ws_version_strategy_created
                    ON {self._vlt} (strategy_id, created_at DESC)
            """)

            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._cst} (
                    snapshot_id         TEXT PRIMARY KEY,
                    workshop_id         TEXT NOT NULL
                        REFERENCES {self._st}(workshop_id),
                    strategy_version_id TEXT,
                    state_map_json      JSONB,
                    blocking_items_json JSONB,
                    next_question_json  JSONB,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_ws_snapshot_workshop_created
                    ON {self._cst} (workshop_id, created_at)
            """)
            conn.execute(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                         WHERE conname = 'fk_ws_snapshot_session'
                           AND conrelid = '"{self.schema}"."strategy_completeness_snapshot"'::regclass
                    ) THEN
                        ALTER TABLE {self._cst}
                            ADD CONSTRAINT fk_ws_snapshot_session
                            FOREIGN KEY (workshop_id)
                            REFERENCES {self._st}(workshop_id);
                    END IF;
                END $$
            """)

            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._rat} (
                    assessment_id            TEXT PRIMARY KEY,
                    workshop_id              TEXT NOT NULL
                        REFERENCES {self._st}(workshop_id),
                    strategy_id              TEXT NOT NULL,
                    workshop_version_id      TEXT NOT NULL,
                    strategy_spec_registry_id TEXT NOT NULL,
                    assessment_version       INTEGER NOT NULL,
                    assessment_json          JSONB NOT NULL,
                    assessed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
                    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT uq_ws_readiness_version UNIQUE (workshop_id, assessment_version)
                )
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_ws_readiness_latest
                    ON {self._rat} (workshop_id, assessment_version DESC, created_at DESC)
            """)

            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._wct} (
                    card_id                   TEXT PRIMARY KEY,
                    workshop_id               TEXT NOT NULL
                        REFERENCES {self._st}(workshop_id),
                    sequence_no               INTEGER NOT NULL,
                    card_type                 TEXT NOT NULL,
                    status                    TEXT NOT NULL,
                    title                     TEXT NOT NULL,
                    summary                   TEXT,
                    payload_json              JSONB NOT NULL,
                    source_event_ids_json     JSONB NOT NULL DEFAULT '[]'::jsonb,
                    workshop_version_id       TEXT,
                    strategy_spec_registry_id TEXT,
                    evidence_refs_json        JSONB NOT NULL DEFAULT '[]'::jsonb,
                    allowed_actions_json      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at                TIMESTAMPTZ,
                    CONSTRAINT uq_ws_card_seq UNIQUE (workshop_id, sequence_no)
                )
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_ws_card_workshop_sequence
                    ON {self._wct} (workshop_id, sequence_no)
            """)

            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._ikt} (
                    scope               TEXT NOT NULL,
                    idempotency_key     TEXT NOT NULL,
                    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT pk_ws_idempotency_key PRIMARY KEY (scope, idempotency_key)
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._crt} (
                    command_id               TEXT PRIMARY KEY,
                    tenant_id                TEXT NOT NULL,
                    user_id                  TEXT NOT NULL,
                    workshop_id              TEXT NOT NULL
                        REFERENCES {self._st}(workshop_id),
                    operation                TEXT NOT NULL,
                    idempotency_key          TEXT NOT NULL,
                    request_hash             TEXT NOT NULL,
                    request_payload_json     JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    request_id               TEXT,
                    trace_id                 TEXT,
                    status                   TEXT NOT NULL,
                    expected_lock_version    BIGINT NOT NULL,
                    admitted_lock_version    BIGINT NOT NULL,
                    resulting_lock_version   BIGINT NOT NULL,
                    result_json              JSONB,
                    canonical_refs_json      JSONB NOT NULL DEFAULT '[]'::jsonb,
                    failure_json             JSONB,
                    compensation_json        JSONB,
                    admitted_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
                    completed_at             TIMESTAMPTZ,
                    failed_at                TIMESTAMPTZ,
                    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT uq_ws_command_idempotency UNIQUE
                        (tenant_id, user_id, workshop_id, operation, idempotency_key),
                    CONSTRAINT ck_ws_command_status
                        CHECK (status IN ('admitted','completed','failed'))
                )
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_ws_command_workshop_admitted
                    ON {self._crt} (tenant_id, user_id, workshop_id, admitted_at DESC)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_ws_command_recovery
                    ON {self._crt} (status, admitted_at)
                    WHERE status = 'admitted'
            """)

    # --- session ---

    def create_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        now = _utc_now()
        row: Dict[str, Any] = {
            "workshop_id": session["workshop_id"],
            "tenant_id": session["tenant_id"],
            "user_id": session["user_id"],
            "servant_persona_id": session.get("servant_persona_id"),
            "openclaw_session_id": session.get("openclaw_session_id"),
            "strategy_id": session.get("strategy_id"),
            "active_strategy_spec_registry_id": session.get("active_strategy_spec_registry_id"),
            "selected_version_id": session.get("selected_version_id"),
            "active_workshop_version_id": session.get(
                "active_workshop_version_id", session.get("selected_version_id")
            ),
            "final_strategy_spec_registry_id": session.get(
                "final_strategy_spec_registry_id"
            ),
            "final_workshop_version_id": session.get("final_workshop_version_id"),
            "status": session.get("status", "open"),
            "lock_version": 1,
            "created_at": now,
            "updated_at": now,
            "concluded_at": session.get("concluded_at"),
        }
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self._st}
                    (workshop_id, tenant_id, user_id, servant_persona_id,
                     openclaw_session_id, strategy_id,
                     active_strategy_spec_registry_id, selected_version_id,
                     active_workshop_version_id, final_strategy_spec_registry_id,
                     final_workshop_version_id, status, lock_version, created_at,
                     updated_at, concluded_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    row["workshop_id"], row["tenant_id"], row["user_id"],
                    row["servant_persona_id"], row["openclaw_session_id"],
                    row["strategy_id"], row["active_strategy_spec_registry_id"],
                    row["selected_version_id"], row["active_workshop_version_id"],
                    row["final_strategy_spec_registry_id"],
                    row["final_workshop_version_id"], row["status"],
                    row["lock_version"], row["created_at"], row["updated_at"],
                    row["concluded_at"],
                ),
            )
        return row

    def rollback_create_session(self, workshop_id: str, *, idempotency_scope: str,
                                idempotency_key: str) -> None:
        """Compensate a create that failed before its initial event committed."""
        with self._connect() as conn:
            conn.execute(f"DELETE FROM {self._crt} WHERE workshop_id = %s", (workshop_id,))
            conn.execute(f"DELETE FROM {self._vlt} WHERE workshop_id = %s", (workshop_id,))
            conn.execute(f"DELETE FROM {self._et} WHERE workshop_id = %s", (workshop_id,))
            conn.execute(f"DELETE FROM {self._st} WHERE workshop_id = %s", (workshop_id,))
            conn.execute(
                f"DELETE FROM {self._ikt} WHERE scope = %s AND idempotency_key = %s",
                (idempotency_scope, idempotency_key),
            )

    def get_session(self, workshop_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT workshop_id, tenant_id, user_id, servant_persona_id,
                       openclaw_session_id, strategy_id,
                       active_strategy_spec_registry_id, selected_version_id,
                       active_workshop_version_id,
                       final_strategy_spec_registry_id, final_workshop_version_id,
                       status, lock_version, created_at::text, updated_at::text,
                       concluded_at::text
                FROM {self._st} WHERE workshop_id = %s
                """,
                (workshop_id,),
            )
            row = cur.fetchone()
        return _row_to_dict(row, _SESSION_COLS) if row is not None else None

    def update_session_lock_version(self, workshop_id: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE {self._st}
                   SET lock_version = lock_version + 1,
                       updated_at   = now()
                 WHERE workshop_id = %s
                RETURNING lock_version
                """,
                (workshop_id,),
            )
            row = cur.fetchone()
        return row[0] if row else 1

    def append_event_cas(
        self,
        workshop_id: str,
        expected_lock_version: int,
        event: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
        """Atomically append an event and bump lock_version if version matches.

        Returns (event_dict, new_lock_version) on success.
        Returns (None, current_lock_version) on version conflict.
        Returns (None, None) if workshop not found.

        Everything runs in a single Postgres transaction: the CAS UPDATE and
        the event INSERT are committed or rolled back together.
        """
        now = _utc_now()
        event_id = event.get("event_id") or _new_id()
        payload_refs = event.get("payload_refs_json")
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE {self._st}
                   SET lock_version = lock_version + 1,
                       updated_at   = now()
                 WHERE workshop_id = %s AND lock_version = %s
                RETURNING lock_version
                """,
                (workshop_id, expected_lock_version),
            )
            bump_row = cur.fetchone()
            if bump_row is None:
                cur2 = conn.execute(
                    f"SELECT lock_version FROM {self._st} WHERE workshop_id = %s",
                    (workshop_id,),
                )
                current_row = cur2.fetchone()
                return (None, current_row[0] if current_row else None)
            new_version = bump_row[0]
            cur3 = conn.execute(
                f"""
                INSERT INTO {self._et}
                    (event_id, workshop_id, sequence_no, actor_type, event_type,
                     private_content_ref, redacted_summary, payload_refs_json,
                     trace_id, created_at)
                SELECT %s, %s,
                       COALESCE((SELECT MAX(sequence_no) FROM {self._et}
                                  WHERE workshop_id = %s), 0) + 1,
                       %s, %s, %s, %s, %s::jsonb, %s, %s
                RETURNING sequence_no
                """,
                (
                    event_id, workshop_id, workshop_id,
                    event["actor_type"], event["event_type"],
                    event.get("private_content_ref"),
                    event.get("redacted_summary"),
                    _json_dumps(payload_refs),
                    event.get("trace_id"),
                    now,
                ),
            )
            seq_row = cur3.fetchone()
        return (
            {
                "event_id": event_id,
                "workshop_id": workshop_id,
                "sequence_no": seq_row[0] if seq_row else 1,
                "actor_type": event["actor_type"],
                "event_type": event["event_type"],
                "private_content_ref": event.get("private_content_ref"),
                "redacted_summary": event.get("redacted_summary"),
                "payload_refs_json": payload_refs,
                "trace_id": event.get("trace_id"),
                "created_at": now,
            },
            new_version,
        )

    def check_and_record_idempotency_key(self, scope: str, key: str) -> bool:
        """Return True if duplicate; False if first occurrence (and record it).

        Uses INSERT ... ON CONFLICT DO NOTHING for atomic first-write-wins dedup.
        """
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                INSERT INTO {self._ikt} (scope, idempotency_key, recorded_at)
                VALUES (%s, %s, now())
                ON CONFLICT DO NOTHING
                RETURNING 1
                """,
                (scope, key),
            )
            row = cur.fetchone()
        # RETURNING row present → first occurrence (new insert).
        # RETURNING row absent → conflict (duplicate key).
        return row is None

    def list_sessions(
        self,
        *,
        user_id: str,
        tenant_id: str,
        status: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 20,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        params: List[Any] = [user_id, tenant_id]
        where = "user_id = %s AND tenant_id = %s"
        if status:
            where += " AND status = %s"
            params.append(status)
        if cursor:
            where += (
                f" AND created_at > (SELECT created_at FROM {self._st}"
                " WHERE workshop_id = %s)"
            )
            params.append(cursor)
        params.append(limit + 1)
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT workshop_id, tenant_id, user_id, servant_persona_id,
                       openclaw_session_id, strategy_id,
                       active_strategy_spec_registry_id, selected_version_id,
                       active_workshop_version_id,
                       final_strategy_spec_registry_id, final_workshop_version_id,
                       status, lock_version, created_at::text, updated_at::text,
                       concluded_at::text
                FROM {self._st}
                WHERE {where}
                ORDER BY created_at ASC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
        sessions = [_row_to_dict(r, _SESSION_COLS) for r in rows]
        if len(sessions) > limit:
            next_cursor = sessions[limit - 1]["workshop_id"]
            sessions = sessions[:limit]
        else:
            next_cursor = None
        return sessions, next_cursor

    # --- immutable StrategySpec version links ---

    def list_version_links(self, workshop_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT workshop_version_id, workshop_id, strategy_id,
                       strategy_spec_registry_id, parent_workshop_version_id,
                       source_event_id, sequence_no, document_sha256,
                       created_by, created_at::text
                  FROM {self._vlt}
                 WHERE workshop_id = %s
                 ORDER BY sequence_no ASC
                """,
                (workshop_id,),
            )
            rows = cur.fetchall()
        return [_row_to_dict(row, _VERSION_LINK_COLS) for row in rows]

    def get_version_link(
        self,
        workshop_id: str,
        workshop_version_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT workshop_version_id, workshop_id, strategy_id,
                       strategy_spec_registry_id, parent_workshop_version_id,
                       source_event_id, sequence_no, document_sha256,
                       created_by, created_at::text
                  FROM {self._vlt}
                 WHERE workshop_id = %s AND workshop_version_id = %s
                """,
                (workshop_id, workshop_version_id),
            )
            row = cur.fetchone()
        return _row_to_dict(row, _VERSION_LINK_COLS) if row is not None else None

    @staticmethod
    def _validate_version_link_identity(
        link: Dict[str, Any],
        *,
        strategy_id: str,
        document_sha256: str,
    ) -> None:
        if str(link.get("strategy_id") or "") != strategy_id:
            raise WorkshopVersionProjectionConflict(
                "workshop version strategy identity is immutable"
            )
        stored_digest = str(link.get("document_sha256") or "").strip()
        if stored_digest and stored_digest != document_sha256:
            raise WorkshopVersionProjectionConflict(
                "authoritative StrategySpec bytes changed for an immutable version"
            )

    def ensure_current_version_link(
        self,
        *,
        workshop_id: str,
        strategy_id: str,
        strategy_spec_registry_id: str,
        document_sha256: str,
    ) -> Dict[str, Any]:
        digest = _document_sha256(document_sha256)
        with self._connect() as conn:
            session_row = conn.execute(
                f"""
                SELECT tenant_id, user_id, strategy_id,
                       active_strategy_spec_registry_id, selected_version_id,
                       active_workshop_version_id, created_at::text
                  FROM {self._st}
                 WHERE workshop_id = %s
                   FOR UPDATE
                """,
                (workshop_id,),
            ).fetchone()
            if session_row is None:
                raise KeyError(workshop_id)
            if str(session_row[3] or "") != strategy_spec_registry_id:
                raise WorkshopVersionProjectionConflict(
                    "active StrategySpec Registry identity changed during backfill"
                )
            if session_row[2] and str(session_row[2]) != strategy_id:
                raise WorkshopVersionProjectionConflict(
                    "workshop and Registry strategy identities disagree"
                )

            link_row = conn.execute(
                f"""
                SELECT workshop_version_id, workshop_id, strategy_id,
                       strategy_spec_registry_id, parent_workshop_version_id,
                       source_event_id, sequence_no, document_sha256,
                       created_by, created_at::text
                  FROM {self._vlt}
                 WHERE workshop_id = %s AND strategy_spec_registry_id = %s
                   FOR UPDATE
                """,
                (workshop_id, strategy_spec_registry_id),
            ).fetchone()
            if link_row is None:
                pointer_ids = {
                    str(value) for value in (session_row[4], session_row[5]) if value
                }
                if len(pointer_ids) > 1:
                    raise WorkshopVersionProjectionConflict(
                        "legacy selected-version aliases disagree"
                    )
                workshop_version_id = next(iter(pointer_ids), None) or (
                    _legacy_workshop_version_id(
                        workshop_id, strategy_spec_registry_id
                    )
                )
                collision = conn.execute(
                    f"""
                    SELECT strategy_spec_registry_id
                      FROM {self._vlt}
                     WHERE workshop_version_id = %s
                    """,
                    (workshop_version_id,),
                ).fetchone()
                if collision is not None:
                    raise WorkshopVersionProjectionConflict(
                        "selected workshop version points to another Registry version"
                    )
                latest = conn.execute(
                    f"""
                    SELECT workshop_version_id, sequence_no
                      FROM {self._vlt}
                     WHERE workshop_id = %s
                     ORDER BY sequence_no DESC
                     LIMIT 1
                    """,
                    (workshop_id,),
                ).fetchone()
                parent_id = latest[0] if latest is not None else None
                sequence_no = int(latest[1]) + 1 if latest is not None else 1
                conn.execute(
                    f"""
                    INSERT INTO {self._vlt}
                        (workshop_version_id, workshop_id, strategy_id,
                         strategy_spec_registry_id, parent_workshop_version_id,
                         source_event_id, sequence_no, document_sha256,
                         created_by, created_at)
                    VALUES (%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s)
                    """,
                    (
                        workshop_version_id, workshop_id, strategy_id,
                        strategy_spec_registry_id, parent_id, sequence_no,
                        digest, session_row[1], session_row[6],
                    ),
                )
                link_row = conn.execute(
                    f"""
                    SELECT workshop_version_id, workshop_id, strategy_id,
                           strategy_spec_registry_id, parent_workshop_version_id,
                           source_event_id, sequence_no, document_sha256,
                           created_by, created_at::text
                      FROM {self._vlt}
                     WHERE workshop_id = %s AND strategy_spec_registry_id = %s
                    """,
                    (workshop_id, strategy_spec_registry_id),
                ).fetchone()

            link = _row_to_dict(link_row, _VERSION_LINK_COLS)
            self._validate_version_link_identity(
                link,
                strategy_id=strategy_id,
                document_sha256=digest,
            )
            if not link.get("document_sha256"):
                conn.execute(
                    f"""
                    UPDATE {self._vlt}
                       SET document_sha256 = %s
                     WHERE workshop_id = %s AND workshop_version_id = %s
                       AND document_sha256 IS NULL
                    """,
                    (digest, workshop_id, link["workshop_version_id"]),
                )
                link["document_sha256"] = digest

            link_id = str(link["workshop_version_id"])
            for pointer_name, pointer in (
                ("selected_version_id", session_row[4]),
                ("active_workshop_version_id", session_row[5]),
            ):
                if pointer and str(pointer) != link_id:
                    raise WorkshopVersionProjectionConflict(
                        f"{pointer_name} disagrees with active Registry version"
                    )
            # This is an additive data migration, not a workshop command:
            # preserve lock_version and timestamps so existing payload bytes
            # outside the newly populated pointers remain unchanged.
            conn.execute(
                f"""
                UPDATE {self._st}
                   SET strategy_id = COALESCE(strategy_id, %s),
                       selected_version_id = COALESCE(selected_version_id, %s),
                       active_workshop_version_id =
                           COALESCE(active_workshop_version_id, %s)
                 WHERE workshop_id = %s
                """,
                (strategy_id, link_id, link_id, workshop_id),
            )
            return link

    def ensure_version_link_digest(
        self,
        *,
        workshop_id: str,
        workshop_version_id: str,
        strategy_id: str,
        document_sha256: str,
    ) -> Dict[str, Any]:
        digest = _document_sha256(document_sha256)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT workshop_version_id, workshop_id, strategy_id,
                       strategy_spec_registry_id, parent_workshop_version_id,
                       source_event_id, sequence_no, document_sha256,
                       created_by, created_at::text
                  FROM {self._vlt}
                 WHERE workshop_id = %s AND workshop_version_id = %s
                   FOR UPDATE
                """,
                (workshop_id, workshop_version_id),
            ).fetchone()
            if row is None:
                raise KeyError(workshop_version_id)
            link = _row_to_dict(row, _VERSION_LINK_COLS)
            self._validate_version_link_identity(
                link,
                strategy_id=strategy_id,
                document_sha256=digest,
            )
            if not link.get("document_sha256"):
                conn.execute(
                    f"""
                    UPDATE {self._vlt}
                       SET document_sha256 = %s
                     WHERE workshop_id = %s AND workshop_version_id = %s
                       AND document_sha256 IS NULL
                    """,
                    (digest, workshop_id, workshop_version_id),
                )
                link["document_sha256"] = digest
            return link

    # --- durable governed-command ledger ---

    def _fetch_command_receipt(
        self,
        conn: Any,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
        for_update: bool = False,
    ) -> Optional[Dict[str, Any]]:
        lock_clause = " FOR UPDATE" if for_update else ""
        cur = conn.execute(
            f"""
            SELECT command_id, tenant_id, user_id, workshop_id, operation,
                   idempotency_key, request_hash, request_payload_json::text,
                   request_id, trace_id, status, expected_lock_version,
                   admitted_lock_version, resulting_lock_version,
                   result_json::text, canonical_refs_json::text,
                   failure_json::text, compensation_json::text,
                   admitted_at::text, completed_at::text, failed_at::text,
                   updated_at::text
              FROM {self._crt}
             WHERE tenant_id = %s AND user_id = %s AND workshop_id = %s
               AND operation = %s AND idempotency_key = %s{lock_clause}
            """,
            (tenant_id, user_id, workshop_id, operation, idempotency_key),
        )
        row = cur.fetchone()
        return _decode_command_receipt_row(row) if row is not None else None

    def admit_command(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        expected_lock_version: int,
        request_payload: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not operation or not idempotency_key or not request_hash:
            raise ValueError("operation, idempotency_key, and request_hash are required")
        with self._connect() as conn:
            # Fast replay read avoids requiring workshop existence after a
            # completed receipt has been retained for audit.
            existing = self._fetch_command_receipt(
                conn,
                workshop_id=workshop_id,
                tenant_id=tenant_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                current_row = conn.execute(
                    f"SELECT lock_version FROM {self._st} WHERE workshop_id = %s",
                    (workshop_id,),
                ).fetchone()
                return {
                    "outcome": (
                        "replay"
                        if existing["request_hash"] == request_hash
                        else "idempotency_conflict"
                    ),
                    "receipt": existing,
                    "current_lock_version": current_row[0] if current_row else None,
                }

            session_row = conn.execute(
                f"""
                SELECT tenant_id, user_id, status, lock_version
                  FROM {self._st}
                 WHERE workshop_id = %s
                   FOR UPDATE
                """,
                (workshop_id,),
            ).fetchone()
            if session_row is None:
                return {"outcome": "not_found", "receipt": None}

            # A competing request may have inserted while this transaction was
            # waiting on the workshop row lock.  Re-read before any ETag check.
            existing = self._fetch_command_receipt(
                conn,
                workshop_id=workshop_id,
                tenant_id=tenant_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return {
                    "outcome": (
                        "replay"
                        if existing["request_hash"] == request_hash
                        else "idempotency_conflict"
                    ),
                    "receipt": existing,
                    "current_lock_version": session_row[3],
                }

            if session_row[0] != tenant_id or session_row[1] != user_id:
                return {"outcome": "scope_mismatch", "receipt": None}
            current_lock_version = int(session_row[3])
            if session_row[2] in TERMINAL_WORKSHOP_STATUSES:
                return {
                    "outcome": "terminal",
                    "receipt": None,
                    "workshop_status": session_row[2],
                    "current_lock_version": current_lock_version,
                }
            if current_lock_version != int(expected_lock_version):
                return {
                    "outcome": "stale",
                    "receipt": None,
                    "current_lock_version": current_lock_version,
                }

            command_id = _new_id()
            now = _utc_now()
            new_lock_version = current_lock_version + 1
            conn.execute(
                f"""
                INSERT INTO {self._crt}
                    (command_id, tenant_id, user_id, workshop_id, operation,
                     idempotency_key, request_hash, request_payload_json,
                     request_id, trace_id, status, expected_lock_version,
                     admitted_lock_version, resulting_lock_version,
                     canonical_refs_json, admitted_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,'admitted',%s,%s,%s,
                        '[]'::jsonb,%s,%s)
                """,
                (
                    command_id, tenant_id, user_id, workshop_id, operation,
                    idempotency_key, request_hash,
                    _json_dumps(_redact_command_payload(request_payload or {})),
                    request_id, trace_id, current_lock_version,
                    new_lock_version, new_lock_version, now, now,
                ),
            )
            conn.execute(
                f"""
                UPDATE {self._st}
                   SET lock_version = %s, updated_at = %s
                 WHERE workshop_id = %s
                """,
                (new_lock_version, now, workshop_id),
            )
            receipt = self._fetch_command_receipt(
                conn,
                workshop_id=workshop_id,
                tenant_id=tenant_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
            )
            return {
                "outcome": "admitted",
                "receipt": receipt,
                "current_lock_version": new_lock_version,
            }

    def get_command_receipt(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            return self._fetch_command_receipt(
                conn,
                workshop_id=workshop_id,
                tenant_id=tenant_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
            )

    @staticmethod
    def _validate_session_updates(
        session_updates: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        updates = dict(session_updates or {})
        unknown_updates = set(updates) - _SESSION_UPDATE_ALLOWLIST
        if unknown_updates:
            raise ValueError(
                f"Unsupported session update fields: {sorted(unknown_updates)!r}"
            )
        if "status" in updates and updates["status"] not in WORKSHOP_STATUSES:
            raise ValueError(f"Unsupported workshop status: {updates['status']!r}")
        # selected_version_id is the compatibility alias for the active link.
        if "active_workshop_version_id" in updates:
            updates.setdefault("selected_version_id", updates["active_workshop_version_id"])
        elif "selected_version_id" in updates:
            updates["active_workshop_version_id"] = updates["selected_version_id"]
        return updates

    def complete_command(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        result: Dict[str, Any],
        canonical_refs: Optional[Any] = None,
        version_link: Optional[Dict[str, Any]] = None,
        session_updates: Optional[Dict[str, Any]] = None,
        event: Optional[Dict[str, Any]] = None,
        resolve_compensation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        updates = self._validate_session_updates(session_updates)
        with self._connect() as conn:
            receipt = self._fetch_command_receipt(
                conn,
                workshop_id=workshop_id,
                tenant_id=tenant_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
                for_update=True,
            )
            if receipt is None:
                return {"outcome": "not_found", "receipt": None}
            if receipt["request_hash"] != request_hash:
                return {"outcome": "idempotency_conflict", "receipt": receipt}
            if receipt["status"] == "completed":
                return {"outcome": "replay", "receipt": receipt}
            if receipt["status"] != "admitted":
                return {"outcome": "invalid_state", "receipt": receipt}

            session_row = conn.execute(
                f"SELECT lock_version FROM {self._st} WHERE workshop_id = %s FOR UPDATE",
                (workshop_id,),
            ).fetchone()
            if session_row is None:
                return {"outcome": "not_found", "receipt": None}

            stored_link: Optional[Dict[str, Any]] = None
            if version_link is not None:
                required = {
                    "workshop_version_id", "strategy_id",
                    "strategy_spec_registry_id", "document_sha256", "created_by",
                }
                missing = required - set(version_link)
                if missing:
                    raise ValueError(f"Missing version link fields: {sorted(missing)!r}")
                sequence_row = conn.execute(
                    f"""
                    SELECT COALESCE(MAX(sequence_no), 0) + 1
                      FROM {self._vlt}
                     WHERE workshop_id = %s
                    """,
                    (workshop_id,),
                ).fetchone()
                sequence_no = int(
                    version_link.get("sequence_no")
                    or (sequence_row[0] if sequence_row else 1)
                )
                document_sha256 = _document_sha256(
                    version_link["document_sha256"]
                )
                created_at = version_link.get("created_at") or _utc_now()
                inserted = conn.execute(
                    f"""
                    INSERT INTO {self._vlt}
                        (workshop_version_id, workshop_id, strategy_id,
                         strategy_spec_registry_id, parent_workshop_version_id,
                         source_event_id, sequence_no, document_sha256,
                         created_by, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                    RETURNING workshop_version_id
                    """,
                    (
                        version_link["workshop_version_id"], workshop_id,
                        version_link["strategy_id"],
                        version_link["strategy_spec_registry_id"],
                        version_link.get("parent_workshop_version_id"),
                        version_link.get("source_event_id"), sequence_no,
                        document_sha256, version_link["created_by"], created_at,
                    ),
                ).fetchone()
                candidate = conn.execute(
                    f"""
                    SELECT workshop_version_id, workshop_id, strategy_id,
                           strategy_spec_registry_id, parent_workshop_version_id,
                           source_event_id, sequence_no, document_sha256,
                           created_by, created_at::text
                      FROM {self._vlt}
                     WHERE workshop_id = %s
                       AND (
                            workshop_version_id = %s
                            OR strategy_spec_registry_id = %s
                            OR sequence_no = %s
                       )
                     ORDER BY CASE WHEN workshop_version_id = %s THEN 0 ELSE 1 END
                     LIMIT 1
                    """,
                    (
                        workshop_id, version_link["workshop_version_id"],
                        version_link["strategy_spec_registry_id"], sequence_no,
                        version_link["workshop_version_id"],
                    ),
                ).fetchone()
                if candidate is None:
                    raise RuntimeError("completed workshop version link disappeared")
                stored_link = _row_to_dict(candidate, _VERSION_LINK_COLS)
                comparable = {
                    "workshop_version_id": version_link["workshop_version_id"],
                    "workshop_id": workshop_id,
                    "strategy_id": version_link["strategy_id"],
                    "strategy_spec_registry_id": version_link[
                        "strategy_spec_registry_id"
                    ],
                    "parent_workshop_version_id": version_link.get(
                        "parent_workshop_version_id"
                    ),
                    "source_event_id": version_link.get("source_event_id"),
                    "sequence_no": sequence_no,
                    "document_sha256": document_sha256,
                    "created_by": version_link["created_by"],
                }
                if any(stored_link.get(key) != value for key, value in comparable.items()):
                    if inserted is not None:
                        raise RuntimeError("inserted workshop version link changed unexpectedly")
                    return {"outcome": "version_conflict", "receipt": receipt}

            if updates:
                assignments = [f"{column} = %s" for column in updates]
                params: List[Any] = list(updates.values())
                assignments.append("updated_at = now()")
                params.append(workshop_id)
                conn.execute(
                    f"UPDATE {self._st} SET {', '.join(assignments)} WHERE workshop_id = %s",
                    params,
                )
            else:
                conn.execute(
                    f"UPDATE {self._st} SET updated_at = now() WHERE workshop_id = %s",
                    (workshop_id,),
                )

            stored_event: Optional[Dict[str, Any]] = None
            if event is not None:
                event_id = event.get("event_id") or _new_id()
                created_at = event.get("created_at") or _utc_now()
                event_row = conn.execute(
                    f"""
                    INSERT INTO {self._et}
                        (event_id, workshop_id, sequence_no, actor_type, event_type,
                         private_content_ref, redacted_summary, payload_refs_json,
                         trace_id, created_at)
                    SELECT %s, %s,
                           COALESCE((SELECT MAX(sequence_no) FROM {self._et}
                                      WHERE workshop_id = %s), 0) + 1,
                           %s,%s,%s,%s,%s::jsonb,%s,%s
                    RETURNING sequence_no
                    """,
                    (
                        event_id, workshop_id, workshop_id, event["actor_type"],
                        event["event_type"], event.get("private_content_ref"),
                        event.get("redacted_summary"),
                        _json_dumps(event.get("payload_refs_json")),
                        event.get("trace_id") or receipt.get("trace_id"), created_at,
                    ),
                ).fetchone()
                stored_event = {
                    "event_id": event_id,
                    "workshop_id": workshop_id,
                    "sequence_no": event_row[0] if event_row else 1,
                    "actor_type": event["actor_type"],
                    "event_type": event["event_type"],
                    "private_content_ref": event.get("private_content_ref"),
                    "redacted_summary": event.get("redacted_summary"),
                    "payload_refs_json": event.get("payload_refs_json"),
                    "trace_id": event.get("trace_id") or receipt.get("trace_id"),
                    "created_at": created_at,
                }

            now = _utc_now()
            conn.execute(
                f"""
                UPDATE {self._crt}
                   SET status = 'completed', result_json = %s::jsonb,
                       canonical_refs_json = %s::jsonb, completed_at = %s,
                       updated_at = %s
                 WHERE command_id = %s AND status = 'admitted'
                """,
                (
                    _json_dumps(result), _json_dumps(canonical_refs or []),
                    now, now, receipt["command_id"],
                ),
            )
            if resolve_compensation is not None:
                # Same transaction as the successor's completion: a committed
                # successor and a still-resumable source can never coexist.
                self._resolve_compensation_in_txn(
                    conn,
                    workshop_id=workshop_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    operation=str(
                        resolve_compensation.get("operation") or operation
                    ),
                    idempotency_key=str(
                        resolve_compensation.get("idempotency_key") or ""
                    ),
                    resolution=dict(resolve_compensation.get("resolution") or {}),
                )
            completed = self._fetch_command_receipt(
                conn,
                workshop_id=workshop_id,
                tenant_id=tenant_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
            )
            return {
                "outcome": "completed",
                "receipt": completed,
                "version_link": stored_link,
                "event": stored_event,
                "current_lock_version": session_row[0],
            }

    def fail_command(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        failure: Dict[str, Any],
        compensation: Optional[Dict[str, Any]] = None,
        canonical_refs: Optional[Any] = None,
        event: Optional[Dict[str, Any]] = None,
        resolve_compensation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._connect() as conn:
            receipt = self._fetch_command_receipt(
                conn,
                workshop_id=workshop_id,
                tenant_id=tenant_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
                for_update=True,
            )
            if receipt is None:
                return {"outcome": "not_found", "receipt": None}
            if receipt["request_hash"] != request_hash:
                return {"outcome": "idempotency_conflict", "receipt": receipt}
            if receipt["status"] == "failed":
                return {"outcome": "replay", "receipt": receipt}
            if receipt["status"] != "admitted":
                return {"outcome": "invalid_state", "receipt": receipt}

            lock_row = conn.execute(
                f"SELECT lock_version FROM {self._st} WHERE workshop_id = %s FOR UPDATE",
                (workshop_id,),
            ).fetchone()
            stored_event: Optional[Dict[str, Any]] = None
            if event is not None:
                event_id = event.get("event_id") or _new_id()
                created_at = event.get("created_at") or _utc_now()
                event_row = conn.execute(
                    f"""
                    INSERT INTO {self._et}
                        (event_id, workshop_id, sequence_no, actor_type, event_type,
                         private_content_ref, redacted_summary, payload_refs_json,
                         trace_id, created_at)
                    SELECT %s, %s,
                           COALESCE((SELECT MAX(sequence_no) FROM {self._et}
                                      WHERE workshop_id = %s), 0) + 1,
                           %s,%s,%s,%s,%s::jsonb,%s,%s
                    RETURNING sequence_no
                    """,
                    (
                        event_id, workshop_id, workshop_id, event["actor_type"],
                        event["event_type"], event.get("private_content_ref"),
                        event.get("redacted_summary"),
                        _json_dumps(event.get("payload_refs_json")),
                        event.get("trace_id") or receipt.get("trace_id"), created_at,
                    ),
                ).fetchone()
                stored_event = {
                    "event_id": event_id,
                    "workshop_id": workshop_id,
                    "sequence_no": event_row[0] if event_row else 1,
                    "actor_type": event["actor_type"],
                    "event_type": event["event_type"],
                    "private_content_ref": event.get("private_content_ref"),
                    "redacted_summary": event.get("redacted_summary"),
                    "payload_refs_json": event.get("payload_refs_json"),
                    "trace_id": event.get("trace_id") or receipt.get("trace_id"),
                    "created_at": created_at,
                }
            now = _utc_now()
            if canonical_refs is not None:
                conn.execute(
                    f"""
                    UPDATE {self._crt}
                       SET status = 'failed', failure_json = %s::jsonb,
                           compensation_json = %s::jsonb,
                           canonical_refs_json = %s::jsonb, failed_at = %s,
                           updated_at = %s
                     WHERE command_id = %s AND status = 'admitted'
                    """,
                    (
                        _json_dumps(failure), _json_dumps(compensation),
                        _json_dumps(canonical_refs), now, now,
                        receipt["command_id"],
                    ),
                )
            else:
                conn.execute(
                    f"""
                    UPDATE {self._crt}
                       SET status = 'failed', failure_json = %s::jsonb,
                           compensation_json = %s::jsonb, failed_at = %s,
                           updated_at = %s
                     WHERE command_id = %s AND status = 'admitted'
                    """,
                    (
                        _json_dumps(failure), _json_dumps(compensation), now, now,
                        receipt["command_id"],
                    ),
                )
            if resolve_compensation is not None:
                # Same transaction as the successor's failure write: the
                # lineage moves to exactly one live receipt.
                self._resolve_compensation_in_txn(
                    conn,
                    workshop_id=workshop_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    operation=str(
                        resolve_compensation.get("operation") or operation
                    ),
                    idempotency_key=str(
                        resolve_compensation.get("idempotency_key") or ""
                    ),
                    resolution=dict(resolve_compensation.get("resolution") or {}),
                )
            failed = self._fetch_command_receipt(
                conn,
                workshop_id=workshop_id,
                tenant_id=tenant_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
            )
            return {
                "outcome": "failed",
                "receipt": failed,
                "event": stored_event,
                "current_lock_version": lock_row[0] if lock_row else None,
            }

    def find_resumable_command(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        request_hash: str,
    ) -> Optional[Dict[str, Any]]:
        """Latest failed receipt of the same logical request with unresolved
        resumable compensation (recorded partial downstream effects)."""

        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT command_id, tenant_id, user_id, workshop_id, operation,
                       idempotency_key, request_hash, request_payload_json::text,
                       request_id, trace_id, status, expected_lock_version,
                       admitted_lock_version, resulting_lock_version,
                       result_json::text, canonical_refs_json::text,
                       failure_json::text, compensation_json::text,
                       admitted_at::text, completed_at::text, failed_at::text,
                       updated_at::text
                  FROM {self._crt}
                 WHERE tenant_id = %s AND user_id = %s AND workshop_id = %s
                   AND operation = %s AND request_hash = %s
                   AND status = 'failed'
                   AND compensation_json->>'resumable' = 'true'
                   AND compensation_json->>'resolved_at' IS NULL
                 ORDER BY failed_at DESC NULLS LAST, updated_at DESC
                 LIMIT 1
                """,
                (tenant_id, user_id, workshop_id, operation, request_hash),
            )
            row = cur.fetchone()
            return _decode_command_receipt_row(row) if row is not None else None

    def claim_resumable_command(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        request_hash: str,
        claimed_by_idempotency_key: str,
    ) -> Optional[Dict[str, Any]]:
        """Atomically claim the latest resumable lineage for one successor.

        ``SELECT ... FOR UPDATE`` plus the claim stamp run in one
        transaction: while a successor holds the claim, a concurrent new-key
        retry gets ``None`` and must open fresh downstream resources instead
        of adopting shared ones.  Re-claim by the same successor key succeeds
        so an exact command replay can recover after a crash mid-flight.
        """

        if not claimed_by_idempotency_key:
            raise ValueError("claimed_by_idempotency_key is required")
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT command_id, tenant_id, user_id, workshop_id, operation,
                       idempotency_key, request_hash, request_payload_json::text,
                       request_id, trace_id, status, expected_lock_version,
                       admitted_lock_version, resulting_lock_version,
                       result_json::text, canonical_refs_json::text,
                       failure_json::text, compensation_json::text,
                       admitted_at::text, completed_at::text, failed_at::text,
                       updated_at::text
                  FROM {self._crt}
                 WHERE tenant_id = %s AND user_id = %s AND workshop_id = %s
                   AND operation = %s AND request_hash = %s
                   AND status = 'failed'
                   AND compensation_json->>'resumable' = 'true'
                   AND compensation_json->>'resolved_at' IS NULL
                 ORDER BY failed_at DESC NULLS LAST, updated_at DESC
                 LIMIT 1
                   FOR UPDATE
                """,
                (tenant_id, user_id, workshop_id, operation, request_hash),
            )
            row = cur.fetchone()
            if row is None:
                return None
            receipt = _decode_command_receipt_row(row)
            claimed_by = str(
                (receipt.get("compensation") or {}).get(
                    "claimed_by_idempotency_key"
                )
                or ""
            )
            if claimed_by and claimed_by != claimed_by_idempotency_key:
                # The whole lineage chain shares one downstream digest, so a
                # claim on the latest receipt blocks adoption outright rather
                # than falling back to an older receipt of the same chain.
                return None
            now = _utc_now()
            claim = {
                "claimed_by_idempotency_key": claimed_by_idempotency_key,
                "claimed_at": now,
            }
            conn.execute(
                f"""
                UPDATE {self._crt}
                   SET compensation_json =
                           COALESCE(compensation_json, '{{}}'::jsonb) || %s::jsonb,
                       updated_at = %s
                 WHERE command_id = %s AND status = 'failed'
                """,
                (_json_dumps(claim), now, receipt["command_id"]),
            )
            claimed = self._fetch_command_receipt(
                conn,
                workshop_id=workshop_id,
                tenant_id=tenant_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=str(receipt["idempotency_key"]),
            )
            return claimed

    def _resolve_compensation_in_txn(
        self,
        conn: Any,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
        resolution: Dict[str, Any],
    ) -> None:
        """Merge a resolution into a failed source receipt inside *conn*.

        Bookkeeping over lineage: a missing or non-failed source is skipped so
        the successor's own terminal write stays authoritative.  ``conn`` is
        the caller's open transaction — this never commits or rolls back.
        """

        conn.execute(
            f"""
            UPDATE {self._crt}
               SET compensation_json =
                       COALESCE(compensation_json, '{{}}'::jsonb) || %s::jsonb,
                   updated_at = %s
             WHERE tenant_id = %s AND user_id = %s AND workshop_id = %s
               AND operation = %s AND idempotency_key = %s
               AND status = 'failed'
            """,
            (
                _json_dumps(dict(resolution)), _utc_now(),
                tenant_id, user_id, workshop_id, operation, idempotency_key,
            ),
        )

    def resolve_command_compensation(
        self,
        *,
        workshop_id: str,
        tenant_id: str,
        user_id: str,
        operation: str,
        idempotency_key: str,
        resolution: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge a resolution (resumed / cancelled) into a failed receipt's
        compensation so partial effects are not adopted twice."""

        with self._connect() as conn:
            receipt = self._fetch_command_receipt(
                conn,
                workshop_id=workshop_id,
                tenant_id=tenant_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
                for_update=True,
            )
            if receipt is None:
                return {"outcome": "not_found", "receipt": None}
            if receipt["status"] != "failed":
                return {"outcome": "invalid_state", "receipt": receipt}
            now = _utc_now()
            conn.execute(
                f"""
                UPDATE {self._crt}
                   SET compensation_json =
                           COALESCE(compensation_json, '{{}}'::jsonb) || %s::jsonb,
                       updated_at = %s
                 WHERE command_id = %s AND status = 'failed'
                """,
                (_json_dumps(dict(resolution)), now, receipt["command_id"]),
            )
            resolved = self._fetch_command_receipt(
                conn,
                workshop_id=workshop_id,
                tenant_id=tenant_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
            )
            return {"outcome": "resolved", "receipt": resolved}

    # --- event ---

    def create_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        now = _utc_now()
        event_id = event.get("event_id") or _new_id()
        workshop_id = event["workshop_id"]
        payload_refs = event.get("payload_refs_json")
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                INSERT INTO {self._et}
                    (event_id, workshop_id, sequence_no, actor_type, event_type,
                     private_content_ref, redacted_summary, payload_refs_json,
                     trace_id, created_at)
                SELECT %s, %s,
                       COALESCE((SELECT MAX(sequence_no) FROM {self._et}
                                  WHERE workshop_id = %s), 0) + 1,
                       %s, %s, %s, %s, %s::jsonb, %s, %s
                ON CONFLICT (event_id) DO NOTHING
                RETURNING sequence_no
                """,
                (
                    event_id, workshop_id, workshop_id,
                    event["actor_type"], event["event_type"],
                    event.get("private_content_ref"),
                    event.get("redacted_summary"),
                    _json_dumps(payload_refs),
                    event.get("trace_id"),
                    now,
                ),
            )
            seq_row = cur.fetchone()
            if seq_row is None:
                existing = conn.execute(
                    f"SELECT workshop_id,sequence_no,actor_type,event_type,private_content_ref,"
                    f"redacted_summary,payload_refs_json::text,trace_id FROM {self._et} WHERE event_id=%s",
                    (event_id,),
                ).fetchone()
                if existing is None:
                    raise RuntimeError("idempotent workshop event disappeared")
                expected = (
                    workshop_id, event["actor_type"], event["event_type"],
                    event.get("private_content_ref"), event.get("redacted_summary"),
                    payload_refs, event.get("trace_id"),
                )
                actual = (
                    existing[0], existing[2], existing[3], existing[4], existing[5],
                    _decode_json(existing[6]), existing[7],
                )
                if actual != expected:
                    raise ValueError("event_id reused with a different payload")
                seq_row = (existing[1],)
        return {
            "event_id": event_id,
            "workshop_id": workshop_id,
            "sequence_no": seq_row[0] if seq_row else 1,
            "actor_type": event["actor_type"],
            "event_type": event["event_type"],
            "private_content_ref": event.get("private_content_ref"),
            "redacted_summary": event.get("redacted_summary"),
            "payload_refs_json": payload_refs,
            "trace_id": event.get("trace_id"),
            "created_at": now,
        }

    def list_events(
        self,
        workshop_id: str,
        *,
        after_sequence: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        params: List[Any] = [workshop_id]
        where = "workshop_id = %s"
        if after_sequence is not None:
            where += " AND sequence_no > %s"
            params.append(after_sequence)
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT event_id, workshop_id, sequence_no, actor_type, event_type,
                       private_content_ref, redacted_summary,
                       payload_refs_json::text, trace_id, created_at::text
                FROM {self._et}
                WHERE {where}
                ORDER BY sequence_no ASC
                """,
                params,
            )
            rows = cur.fetchall()
        result = []
        for row in rows:
            r = _row_to_dict(row, _EVENT_COLS)
            r["payload_refs_json"] = _decode_json(r.get("payload_refs_json"))
            result.append(r)
        return result

    # --- completeness snapshot ---

    def create_completeness_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        now = _utc_now()
        row: Dict[str, Any] = {
            "snapshot_id": snapshot.get("snapshot_id") or _new_id(),
            "workshop_id": snapshot["workshop_id"],
            "strategy_version_id": snapshot.get("strategy_version_id"),
            "state_map_json": snapshot.get("state_map_json"),
            "blocking_items_json": snapshot.get("blocking_items_json"),
            "next_question_json": snapshot.get("next_question_json"),
            "created_at": now,
        }
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self._cst}
                    (snapshot_id, workshop_id, strategy_version_id,
                     state_map_json, blocking_items_json, next_question_json, created_at)
                VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s)
                """,
                (
                    row["snapshot_id"], row["workshop_id"],
                    row["strategy_version_id"],
                    _json_dumps(row["state_map_json"]),
                    _json_dumps(row["blocking_items_json"]),
                    _json_dumps(row["next_question_json"]),
                    row["created_at"],
                ),
            )
        return row

    def get_latest_completeness_snapshot(
        self, workshop_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT snapshot_id, workshop_id, strategy_version_id,
                       state_map_json::text, blocking_items_json::text,
                       next_question_json::text, created_at::text
                FROM {self._cst}
                WHERE workshop_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (workshop_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        r = _row_to_dict(row, _SNAPSHOT_COLS)
        r["state_map_json"] = _decode_json(r.get("state_map_json"))
        r["blocking_items_json"] = _decode_json(r.get("blocking_items_json"))
        r["next_question_json"] = _decode_json(r.get("next_question_json"))
        return r

    # --- readiness assessment ---

    def create_readiness_assessment(self, assessment: Dict[str, Any]) -> Dict[str, Any]:
        workshop_id = assessment["workshop_id"]
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT COALESCE(MAX(assessment_version), 0) + 1
                FROM {self._rat}
                WHERE workshop_id = %s
                """,
                (workshop_id,),
            )
            next_row = cur.fetchone()
            version = int(assessment.get("assessment_version") or (next_row[0] if next_row else 1))
            now = assessment.get("assessed_at", _utc_now())
            row: Dict[str, Any] = {
                "spec_version": assessment.get("spec_version", "1.0"),
                "assessment_id": assessment.get("assessment_id") or _new_readiness_id(workshop_id, version),
                "workshop_id": workshop_id,
                "strategy_id": assessment["strategy_id"],
                "workshop_version_id": assessment["workshop_version_id"],
                "strategy_spec_registry_id": assessment["strategy_spec_registry_id"],
                "assessment_version": version,
                "gates": assessment.get("gates", []),
                "highest_ready_gate": assessment.get("highest_ready_gate"),
                "staleness_reasons": assessment.get("staleness_reasons", []),
                "assessed_at": now,
                "evidence_refs": assessment.get("evidence_refs", []),
            }
            if assessment.get("valid_until") is not None:
                row["valid_until"] = assessment["valid_until"]
            conn.execute(
                f"""
                INSERT INTO {self._rat}
                    (assessment_id, workshop_id, strategy_id, workshop_version_id,
                     strategy_spec_registry_id, assessment_version, assessment_json,
                     assessed_at, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,now())
                """,
                (
                    row["assessment_id"], row["workshop_id"], row["strategy_id"],
                    row["workshop_version_id"], row["strategy_spec_registry_id"],
                    row["assessment_version"], _json_dumps(row), row["assessed_at"],
                ),
            )
        return row

    def get_latest_readiness_assessment(self, workshop_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT assessment_json::text
                FROM {self._rat}
                WHERE workshop_id = %s
                ORDER BY assessment_version DESC, created_at DESC
                LIMIT 1
                """,
                (workshop_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        decoded = _decode_json(row[0])
        return decoded if isinstance(decoded, dict) else None

    # --- typed workshop cards ---

    def record_workshop_card(self, card: Dict[str, Any]) -> Dict[str, Any]:
        workshop_id = card["workshop_id"]
        with self._connect() as conn:
            cur = conn.execute(
                f"SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM {self._wct} WHERE workshop_id = %s",
                (workshop_id,),
            )
            next_row = cur.fetchone()
            sequence_no = int(card.get("sequence_no") or (next_row[0] if next_row else 1))
            now = card.get("created_at", _utc_now())
            row: Dict[str, Any] = {
                "spec_version": card.get("spec_version", "1.0"),
                "card_id": card.get("card_id") or f"card_{_safe_id_fragment(workshop_id)}_{sequence_no}",
                "card_type": card["card_type"],
                "workshop_id": workshop_id,
                "sequence_no": sequence_no,
                "source_event_ids": list(card.get("source_event_ids") or []),
                "workshop_version_id": card.get("workshop_version_id"),
                "strategy_spec_registry_id": card.get("strategy_spec_registry_id"),
                "status": card.get("status", "informational"),
                "title": card["title"],
                "summary": card.get("summary"),
                "payload": card.get("payload", {}),
                "evidence_refs": list(card.get("evidence_refs") or []),
                "allowed_actions": dict(card.get("allowed_actions") or {}),
                "created_at": now,
                "updated_at": card.get("updated_at"),
            }
            conn.execute(
                f"""
                INSERT INTO {self._wct}
                    (card_id, workshop_id, sequence_no, card_type, status, title, summary,
                     payload_json, source_event_ids_json, workshop_version_id,
                     strategy_spec_registry_id, evidence_refs_json, allowed_actions_json,
                     created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
                ON CONFLICT (card_id) DO UPDATE SET
                    sequence_no = EXCLUDED.sequence_no,
                    card_type = EXCLUDED.card_type,
                    status = EXCLUDED.status,
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    payload_json = EXCLUDED.payload_json,
                    source_event_ids_json = EXCLUDED.source_event_ids_json,
                    workshop_version_id = EXCLUDED.workshop_version_id,
                    strategy_spec_registry_id = EXCLUDED.strategy_spec_registry_id,
                    evidence_refs_json = EXCLUDED.evidence_refs_json,
                    allowed_actions_json = EXCLUDED.allowed_actions_json,
                    updated_at = COALESCE(EXCLUDED.updated_at, now())
                """,
                (
                    row["card_id"], row["workshop_id"], row["sequence_no"],
                    row["card_type"], row["status"], row["title"], row["summary"],
                    _json_dumps(row["payload"]),
                    _json_dumps(row["source_event_ids"]),
                    row["workshop_version_id"],
                    row["strategy_spec_registry_id"],
                    _json_dumps(row["evidence_refs"]),
                    _json_dumps(row["allowed_actions"]),
                    row["created_at"], row["updated_at"],
                ),
            )
        return row

    def list_workshop_cards(
        self,
        workshop_id: str,
        *,
        after_sequence: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        params: List[Any] = [workshop_id]
        where = "workshop_id = %s"
        if after_sequence is not None:
            where += " AND sequence_no > %s"
            params.append(after_sequence)
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT card_id, workshop_id, sequence_no, card_type, status, title,
                       summary, payload_json::text, source_event_ids_json::text,
                       workshop_version_id, strategy_spec_registry_id,
                       evidence_refs_json::text, allowed_actions_json::text,
                       created_at::text, updated_at::text
                FROM {self._wct}
                WHERE {where}
                ORDER BY sequence_no ASC
                """,
                params,
            )
            rows = cur.fetchall()
        result = []
        for row in rows:
            r = _row_to_dict(row, _CARD_COLS)
            card: Dict[str, Any] = {
                "spec_version": "1.0",
                "card_id": r["card_id"],
                "card_type": r["card_type"],
                "workshop_id": r["workshop_id"],
                "sequence_no": r["sequence_no"],
                "status": r["status"],
                "title": r["title"],
                "payload": _decode_json(r.get("payload_json")) or {},
                "created_at": r["created_at"],
            }
            optional_map = {
                "summary": r.get("summary"),
                "source_event_ids": _decode_json(r.get("source_event_ids_json")),
                "workshop_version_id": r.get("workshop_version_id"),
                "strategy_spec_registry_id": r.get("strategy_spec_registry_id"),
                "evidence_refs": _decode_json(r.get("evidence_refs_json")),
                "allowed_actions": _decode_json(r.get("allowed_actions_json")),
                "updated_at": r.get("updated_at"),
            }
            for key, value in optional_map.items():
                if value not in (None, [], {}):
                    card[key] = value
            result.append(card)
        return result


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

def make_workshop_store(
    backend: Optional[str] = None,
    dsn: Optional[str] = None,
    schema: Optional[str] = None,
) -> Any:
    """Return the appropriate WorkshopStore implementation.

    backend=off  → MemoryWorkshopStore (default; safe for dev and tests)
    backend=postgres → PostgresWorkshopStore
    """
    resolved = (backend or os.environ.get(BACKEND_ENV, "off")).strip().lower()
    if resolved in {"off", "false", "disabled", "none", ":memory:", ""}:
        store = MemoryWorkshopStore()
        _logger.info("Agora workshop store initialized backend=memory store=%s", type(store).__name__)
        return store
    if resolved == "postgres":
        resolved_dsn = dsn or os.environ.get(DSN_ENV, "")
        if not resolved_dsn:
            raise RuntimeError(
                f"{DSN_ENV} must be set when {BACKEND_ENV}=postgres"
            )
        resolved_schema = schema or os.environ.get(SCHEMA_ENV, DEFAULT_SCHEMA)
        store = PostgresWorkshopStore(dsn=resolved_dsn, schema=resolved_schema)
        _logger.info(
            "Agora workshop store initialized backend=postgres store=%s schema=%s",
            type(store).__name__,
            resolved_schema,
        )
        return store
    raise RuntimeError(
        f"Unknown {BACKEND_ENV}={resolved!r}; expected 'off' or 'postgres'"
    )
