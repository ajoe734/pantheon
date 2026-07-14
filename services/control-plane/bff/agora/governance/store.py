"""Durable Agora proposal, command-idempotency, and side-effect outbox store."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


BACKEND_ENV = "AGORA_GOVERNANCE_STORE_BACKEND"
DSN_ENV = "AGORA_GOVERNANCE_STORE_DSN"
SCHEMA_ENV = "AGORA_GOVERNANCE_STORE_SCHEMA"
DEFAULT_SCHEMA = "agora"


class ProposalConflict(Exception):
    pass


@dataclass(frozen=True)
class OnceResult:
    data: Dict[str, Any]
    replayed: bool
    run_side_effects: bool


def payload_fingerprint(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _decode(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    return json.loads(value)


class ProposalStore:
    """Proposal revisions plus durable request/outbox state.

    ``off`` is an isolated in-memory backend for unit tests. Deployed BFFs use
    Postgres so independent workers and restarts observe the same state.
    """

    def __init__(self, *, backend: Optional[str] = None, dsn: Optional[str] = None,
                 schema: Optional[str] = None) -> None:
        resolved = (backend or os.getenv(BACKEND_ENV, "off")).strip().lower()
        self.backend = "memory" if resolved in {"", "off", "false", "none", "memory", ":memory:"} else resolved
        self._lock = threading.RLock()
        self._records: Dict[str, List[Dict[str, Any]]] = {}
        self._idempotency: Dict[str, tuple[str, str]] = {}
        self._commands: Dict[str, Dict[str, Any]] = {}
        if self.backend == "memory":
            return
        if self.backend != "postgres":
            raise RuntimeError(f"Unknown {BACKEND_ENV}={resolved!r}; expected off or postgres")
        self.dsn = dsn or os.getenv(DSN_ENV, "")
        if not self.dsn:
            raise RuntimeError(f"{DSN_ENV} must be set when {BACKEND_ENV}=postgres")
        self.schema = schema or os.getenv(SCHEMA_ENV, DEFAULT_SCHEMA)
        q = f'"{self.schema}"'
        self._proposal_table = f'{q}."governed_proposal_revision"'
        self._idem_table = f'{q}."governed_proposal_idempotency"'
        self._command_table = f'{q}."interaction_command_outbox"'
        self._bootstrap()

    def _connect(self) -> Any:
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("psycopg is required for the Agora governance store") from exc
        return psycopg.connect(self.dsn)

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._proposal_table} (
                    proposal_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    record_json JSONB NOT NULL,
                    etag TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (proposal_id, revision)
                )
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_governed_proposal_scope
                ON {self._proposal_table} (tenant_id, owner_user_id, proposal_id, revision DESC)
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._idem_table} (
                    scope_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._command_table} (
                    scope_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    response_json JSONB NOT NULL,
                    side_effect_state TEXT NOT NULL DEFAULT 'pending',
                    lease_until TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

    @staticmethod
    def etag(record: Dict[str, Any]) -> str:
        return '"' + payload_fingerprint(record) + '"'

    @staticmethod
    def _proposal_fingerprint(record: Dict[str, Any]) -> str:
        stable = {k: v for k, v in record.items() if k not in {
            "proposal_id", "created_at", "updated_at", "audit", "expires_at",
        }}
        return payload_fingerprint(stable)

    def create(self, record: Dict[str, Any], key: str, *, fingerprint: Optional[str] = None) -> Dict[str, Any]:
        fp = fingerprint or self._proposal_fingerprint(record)
        scope_key = f"{record['tenant_id']}:{record['owner_user_id']}:{key}"
        if self.backend == "memory":
            with self._lock:
                replay = self._idempotency.get(scope_key)
                if replay:
                    if replay[0] != fp:
                        raise ProposalConflict("idempotency key reused with a different payload")
                    return copy.deepcopy(self._records[replay[1]][-1])
                saved = copy.deepcopy(record)
                self._records[saved["proposal_id"]] = [saved]
                self._idempotency[scope_key] = (fp, saved["proposal_id"])
                return copy.deepcopy(saved)
        with self._connect() as conn:
            cur = conn.execute(
                f"INSERT INTO {self._idem_table} (scope_key,fingerprint,proposal_id) VALUES (%s,%s,%s) "
                "ON CONFLICT (scope_key) DO NOTHING RETURNING proposal_id",
                (scope_key, fp, record["proposal_id"]),
            )
            inserted = cur.fetchone()
            if not inserted:
                replay = conn.execute(
                    f"SELECT fingerprint,proposal_id FROM {self._idem_table} WHERE scope_key=%s FOR UPDATE",
                    (scope_key,),
                ).fetchone()
                if replay[0] != fp:
                    raise ProposalConflict("idempotency key reused with a different payload")
                row = conn.execute(
                    f"SELECT record_json FROM {self._proposal_table} WHERE proposal_id=%s ORDER BY revision DESC LIMIT 1",
                    (replay[1],),
                ).fetchone()
                if not row:
                    raise RuntimeError("proposal idempotency record is incomplete")
                return _decode(row[0])
            saved = copy.deepcopy(record)
            conn.execute(
                f"INSERT INTO {self._proposal_table} (proposal_id,revision,tenant_id,owner_user_id,record_json,etag) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
                (saved["proposal_id"], saved["revision"], saved["tenant_id"], saved["owner_user_id"],
                 json.dumps(saved, default=str), self.etag(saved)),
            )
            return saved

    def get(self, proposal_id: str, tenant_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        if self.backend == "memory":
            with self._lock:
                rows = self._records.get(proposal_id, [])
                if not rows or rows[-1]["tenant_id"] != tenant_id or rows[-1]["owner_user_id"] != user_id:
                    return None
                return copy.deepcopy(rows[-1])
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT record_json FROM {self._proposal_table} WHERE proposal_id=%s AND tenant_id=%s "
                "AND owner_user_id=%s ORDER BY revision DESC LIMIT 1",
                (proposal_id, tenant_id, user_id),
            ).fetchone()
        return _decode(row[0]) if row else None

    def history(self, proposal_id: str, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        if self.backend == "memory":
            with self._lock:
                rows = self._records.get(proposal_id, [])
                if not rows or rows[-1]["tenant_id"] != tenant_id or rows[-1]["owner_user_id"] != user_id:
                    return []
                return copy.deepcopy(rows)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT record_json FROM {self._proposal_table} WHERE proposal_id=%s AND tenant_id=%s "
                "AND owner_user_id=%s ORDER BY revision ASC",
                (proposal_id, tenant_id, user_id),
            ).fetchall()
        return [_decode(row[0]) for row in rows]

    def append(self, proposal_id: str, expected_etag: str, record: Dict[str, Any]) -> Dict[str, Any]:
        if self.backend == "memory":
            with self._lock:
                rows = self._records.get(proposal_id, [])
                if not rows or self.etag(rows[-1]) != expected_etag:
                    raise ProposalConflict("proposal ETag is stale")
                rows.append(copy.deepcopy(record))
                return copy.deepcopy(record)
        with self._connect() as conn:
            latest = conn.execute(
                f"SELECT etag FROM {self._proposal_table} WHERE proposal_id=%s ORDER BY revision DESC LIMIT 1 FOR UPDATE",
                (proposal_id,),
            ).fetchone()
            if not latest or latest[0] != expected_etag:
                raise ProposalConflict("proposal ETag is stale")
            conn.execute(
                f"INSERT INTO {self._proposal_table} (proposal_id,revision,tenant_id,owner_user_id,record_json,etag) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
                (proposal_id, record["revision"], record["tenant_id"], record["owner_user_id"],
                 json.dumps(record, default=str), self.etag(record)),
            )
        return copy.deepcopy(record)

    def once(self, scope: str, key: str, fingerprint: str,
             build: Callable[[], Dict[str, Any]]) -> OnceResult:
        compound = f"{scope}:{key}"
        candidate = build()
        if self.backend == "memory":
            with self._lock:
                existing = self._commands.get(compound)
                if existing:
                    if existing["fingerprint"] != fingerprint:
                        raise ProposalConflict("idempotency key reused with a different payload")
                    should_run = existing["side_effect_state"] == "pending"
                    if should_run:
                        existing["side_effect_state"] = "processing"
                    return OnceResult(copy.deepcopy(existing["response"]), True, should_run)
                self._commands[compound] = {
                    "fingerprint": fingerprint, "response": copy.deepcopy(candidate),
                    "side_effect_state": "processing",
                }
                return OnceResult(copy.deepcopy(candidate), False, True)
        with self._connect() as conn:
            inserted = conn.execute(
                f"INSERT INTO {self._command_table} (scope_key,fingerprint,response_json,side_effect_state,lease_until) "
                "VALUES (%s,%s,%s::jsonb,'pending',NULL) ON CONFLICT (scope_key) DO NOTHING RETURNING scope_key",
                (compound, fingerprint, json.dumps(candidate, default=str)),
            ).fetchone()
            row = conn.execute(
                f"SELECT fingerprint,response_json FROM {self._command_table} WHERE scope_key=%s FOR UPDATE",
                (compound,),
            ).fetchone()
            if row[0] != fingerprint:
                raise ProposalConflict("idempotency key reused with a different payload")
            claimed = conn.execute(
                f"UPDATE {self._command_table} SET side_effect_state='processing',lease_until=now()+interval '5 minutes',updated_at=now() "
                "WHERE scope_key=%s AND (side_effect_state='pending' OR (side_effect_state='processing' AND lease_until < now())) "
                "RETURNING scope_key",
                (compound,),
            ).fetchone()
            return OnceResult(_decode(row[1]), not bool(inserted), bool(claimed))

    def complete_side_effects(self, scope: str, key: str) -> None:
        compound = f"{scope}:{key}"
        if self.backend == "memory":
            with self._lock:
                if compound in self._commands:
                    self._commands[compound]["side_effect_state"] = "completed"
            return
        with self._connect() as conn:
            conn.execute(
                f"UPDATE {self._command_table} SET side_effect_state='completed',completed_at=now(),lease_until=NULL,updated_at=now() WHERE scope_key=%s",
                (compound,),
            )

    def release_side_effects(self, scope: str, key: str) -> None:
        compound = f"{scope}:{key}"
        if self.backend == "memory":
            with self._lock:
                if compound in self._commands:
                    self._commands[compound]["side_effect_state"] = "pending"
            return
        with self._connect() as conn:
            conn.execute(
                f"UPDATE {self._command_table} SET side_effect_state='pending',lease_until=NULL,updated_at=now() WHERE scope_key=%s AND side_effect_state='processing'",
                (compound,),
            )
