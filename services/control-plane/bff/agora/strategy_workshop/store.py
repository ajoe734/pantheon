"""Agora workshop persistence — three Postgres tables.

Tables (owned by this module):
  strategy_workshop_session      — workshop lifecycle and subject reference
  strategy_workshop_event        — append-only event log; private content NEVER stored here
  strategy_completeness_snapshot — completeness/next-question snapshot

Privacy rule: workshop events store only ``private_content_ref`` (pointer to
the encrypted store) and ``redacted_summary``.  The raw private content must
never be written into these tables.

Backend env:
  AGORA_WORKSHOP_STORE_BACKEND   off | postgres  (default: off)
  AGORA_WORKSHOP_STORE_DSN       Postgres DSN when backend=postgres
  AGORA_WORKSHOP_STORE_SCHEMA    Schema name     (default: agora)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_logger = logging.getLogger(__name__)

BACKEND_ENV = "AGORA_WORKSHOP_STORE_BACKEND"
DSN_ENV = "AGORA_WORKSHOP_STORE_DSN"
SCHEMA_ENV = "AGORA_WORKSHOP_STORE_SCHEMA"
DEFAULT_SCHEMA = "agora"


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())


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


# --------------------------------------------------------------------------- #
# MemoryWorkshopStore — thread-safe in-memory backend for dev / tests
# --------------------------------------------------------------------------- #

_SESSION_COLS = [
    "workshop_id", "tenant_id", "user_id", "servant_persona_id",
    "openclaw_session_id", "strategy_id", "active_strategy_spec_registry_id",
    "selected_version_id", "status", "lock_version", "created_at", "updated_at",
]
_EVENT_COLS = [
    "event_id", "workshop_id", "sequence_no", "actor_type", "event_type",
    "private_content_ref", "redacted_summary", "payload_refs_json", "trace_id", "created_at",
]
_SNAPSHOT_COLS = [
    "snapshot_id", "workshop_id", "strategy_version_id",
    "state_map_json", "blocking_items_json", "next_question_json", "created_at",
]


class MemoryWorkshopStore:
    """Thread-safe in-memory store.  Used when backend=off (dev / tests)."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._events: Dict[str, List[Dict[str, Any]]] = {}
        self._snapshots: Dict[str, List[Dict[str, Any]]] = {}
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
                "status": session.get("status", "open"),
                "lock_version": 1,
                "created_at": session.get("created_at", now),
                "updated_at": session.get("updated_at", now),
            }
            self._sessions[row["workshop_id"]] = row
            return dict(row)

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

    # --- event ---

    def create_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            wid = event["workshop_id"]
            bucket = self._events.setdefault(wid, [])
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
        self.schema = schema
        q = f'"{schema}"'
        self._st = f'{q}."strategy_workshop_session"'
        self._et = f'{q}."strategy_workshop_event"'
        self._cst = f'{q}."strategy_completeness_snapshot"'
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
                conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
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
                    status                           TEXT NOT NULL DEFAULT 'open',
                    lock_version                     INTEGER NOT NULL DEFAULT 1,
                    created_at                       TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at                       TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_ws_session_user_tenant
                    ON {self._st} (user_id, tenant_id, created_at DESC)
            """)

            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._et} (
                    event_id            TEXT PRIMARY KEY,
                    workshop_id         TEXT NOT NULL,
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

            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._cst} (
                    snapshot_id         TEXT PRIMARY KEY,
                    workshop_id         TEXT NOT NULL,
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
            "status": session.get("status", "open"),
            "lock_version": 1,
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self._st}
                    (workshop_id, tenant_id, user_id, servant_persona_id,
                     openclaw_session_id, strategy_id,
                     active_strategy_spec_registry_id, selected_version_id,
                     status, lock_version, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    row["workshop_id"], row["tenant_id"], row["user_id"],
                    row["servant_persona_id"], row["openclaw_session_id"],
                    row["strategy_id"], row["active_strategy_spec_registry_id"],
                    row["selected_version_id"], row["status"], row["lock_version"],
                    row["created_at"], row["updated_at"],
                ),
            )
        return row

    def get_session(self, workshop_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT workshop_id, tenant_id, user_id, servant_persona_id,
                       openclaw_session_id, strategy_id,
                       active_strategy_spec_registry_id, selected_version_id,
                       status, lock_version,
                       created_at::text, updated_at::text
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

    def check_and_record_idempotency_key(self, scope: str, key: str) -> bool:
        """Return True if duplicate; False if first occurrence (and record it)."""
        return False  # Postgres dedup left for a dedicated idempotency table migration

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
                       status, lock_version,
                       created_at::text, updated_at::text
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
        return MemoryWorkshopStore()
    if resolved == "postgres":
        resolved_dsn = dsn or os.environ.get(DSN_ENV, "")
        if not resolved_dsn:
            raise RuntimeError(
                f"{DSN_ENV} must be set when {BACKEND_ENV}=postgres"
            )
        resolved_schema = schema or os.environ.get(SCHEMA_ENV, DEFAULT_SCHEMA)
        return PostgresWorkshopStore(dsn=resolved_dsn, schema=resolved_schema)
    raise RuntimeError(
        f"Unknown {BACKEND_ENV}={resolved!r}; expected 'off' or 'postgres'"
    )
