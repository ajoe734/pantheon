"""Durable candidate revision, decision, validation, and approval storage."""
from __future__ import annotations

import copy
import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Optional

from .models import canonical_sha256


BACKEND_ENV = "AGORA_CANDIDATE_DECISION_STORE_BACKEND"
DSN_ENV = "AGORA_CANDIDATE_DECISION_STORE_DSN"
SCHEMA_ENV = "AGORA_CANDIDATE_DECISION_STORE_SCHEMA"
DEFAULT_SCHEMA = "agora"


class CandidateDecisionConflict(Exception):
    """Raised for stale bindings or an idempotency key payload conflict."""


@dataclass(frozen=True)
class StoredMutation:
    resource: dict[str, Any]
    replayed: bool


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    return json.loads(value)


class CandidateDecisionStore:
    """Postgres-owned immutable candidate history and decision receipts.

    ``off``/``memory`` exists only for isolated unit tests. A deployed BFF must
    select Postgres explicitly; there is no process-local production fallback.
    """

    def __init__(
        self,
        *,
        backend: Optional[str] = None,
        dsn: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> None:
        configured = backend if backend is not None else os.getenv(BACKEND_ENV)
        if configured is None:
            raise RuntimeError(
                f"{BACKEND_ENV} must be explicitly set; deployed candidate decisions cannot use an implicit memory store"
            )
        selected = configured.strip().lower()
        self.backend = "memory" if selected in {"", "off", "false", "none", "memory", ":memory:"} else selected
        self._lock = threading.RLock()
        self._candidates: dict[str, list[dict[str, Any]]] = {}
        self._decisions: dict[str, list[dict[str, Any]]] = {}
        self._validations: dict[str, list[dict[str, Any]]] = {}
        self._approvals: dict[str, list[dict[str, Any]]] = {}
        self._idempotency: dict[str, tuple[str, dict[str, Any]]] = {}
        if self.backend == "memory":
            return
        if self.backend != "postgres":
            raise RuntimeError(f"Unknown {BACKEND_ENV}={selected!r}; expected off or postgres")
        self.dsn = dsn or os.getenv(DSN_ENV, "")
        if not self.dsn:
            raise RuntimeError(f"{DSN_ENV} must be set when {BACKEND_ENV}=postgres")
        self.schema = schema or os.getenv(SCHEMA_ENV, DEFAULT_SCHEMA)
        q = f'"{self.schema}"'
        self._candidate_table = f'{q}."persona_candidate_revision"'
        self._decision_table = f'{q}."persona_candidate_decision"'
        self._validation_table = f'{q}."persona_candidate_validation_receipt"'
        self._approval_table = f'{q}."persona_candidate_approval_receipt"'
        self._idempotency_table = f'{q}."persona_candidate_idempotency"'
        self._bootstrap()

    def _connect(self) -> Any:
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("psycopg is required for the candidate decision store") from exc
        return psycopg.connect(self.dsn)

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._candidate_table} (
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
                CREATE INDEX IF NOT EXISTS idx_persona_candidate_scope
                ON {self._candidate_table}
                (tenant_id, owner_user_id, proposal_id, revision DESC)
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._decision_table} (
                    decision_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    record_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (proposal_id, revision)
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._validation_table} (
                    validation_receipt_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    record_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (proposal_id, revision, proposal_digest, validation_receipt_id)
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._approval_table} (
                    approval_decision_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    record_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (proposal_id, revision, proposal_digest, approval_decision_id)
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._idempotency_table} (
                    scope_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    response_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

    @staticmethod
    def etag(record: dict[str, Any]) -> str:
        return f'"{canonical_sha256(record)}"'

    @staticmethod
    def _compound(tenant_id: str, owner_user_id: str, operation: str, key: str) -> str:
        if not all((tenant_id, owner_user_id, operation, key)):
            raise ValueError("idempotency scope is incomplete")
        return f"{tenant_id}:{owner_user_id}:{operation}:{key}"

    def replay(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        operation: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> Optional[StoredMutation]:
        """Read an exact completed command before re-running external work."""
        compound = self._compound(tenant_id, owner_user_id, operation, idempotency_key)
        if self.backend == "memory":
            with self._lock:
                return self._memory_replay(compound, fingerprint)
        with self._connect() as conn:
            return self._pg_replay(conn, self._idempotency_table, compound, fingerprint)

    def _memory_replay(self, compound: str, fingerprint: str) -> Optional[StoredMutation]:
        replay = self._idempotency.get(compound)
        if replay is None:
            return None
        if replay[0] != fingerprint:
            raise CandidateDecisionConflict("idempotency key reused with a different payload")
        return StoredMutation(copy.deepcopy(replay[1]), True)

    @staticmethod
    def _pg_replay(conn: Any, table: str, compound: str, fingerprint: str) -> Optional[StoredMutation]:
        row = conn.execute(
            f"SELECT fingerprint,response_json FROM {table} WHERE scope_key=%s FOR UPDATE",
            (compound,),
        ).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise CandidateDecisionConflict("idempotency key reused with a different payload")
        return StoredMutation(_decode(row[1]), True)

    def create_candidate(
        self,
        record: dict[str, Any],
        *,
        idempotency_key: str,
        fingerprint: str,
    ) -> StoredMutation:
        compound = self._compound(
            record["tenant_id"], record["owner_user_id"], "create", idempotency_key
        )
        if self.backend == "memory":
            with self._lock:
                replay = self._memory_replay(compound, fingerprint)
                if replay:
                    return replay
                if record["proposal_id"] in self._candidates:
                    raise CandidateDecisionConflict("proposal id already exists")
                saved = copy.deepcopy(record)
                self._candidates[saved["proposal_id"]] = [saved]
                self._idempotency[compound] = (fingerprint, copy.deepcopy(saved))
                return StoredMutation(copy.deepcopy(saved), False)
        with self._connect() as conn:
            replay = self._pg_replay(conn, self._idempotency_table, compound, fingerprint)
            if replay:
                return replay
            inserted = conn.execute(
                f"INSERT INTO {self._idempotency_table} (scope_key,fingerprint,response_json) "
                "VALUES (%s,%s,%s::jsonb) ON CONFLICT (scope_key) DO NOTHING RETURNING scope_key",
                (compound, fingerprint, json.dumps(record, default=str)),
            ).fetchone()
            if inserted is None:
                replay = self._pg_replay(conn, self._idempotency_table, compound, fingerprint)
                if replay is None:  # pragma: no cover - transaction invariant
                    raise RuntimeError("candidate idempotency winner is not readable")
                return replay
            conn.execute(
                f"INSERT INTO {self._candidate_table} "
                "(proposal_id,revision,tenant_id,owner_user_id,record_json,etag) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
                (
                    record["proposal_id"], record["revision"], record["tenant_id"],
                    record["owner_user_id"], json.dumps(record, default=str), self.etag(record),
                ),
            )
        return StoredMutation(copy.deepcopy(record), False)

    def get(self, proposal_id: str, tenant_id: str, owner_user_id: str) -> Optional[dict[str, Any]]:
        if self.backend == "memory":
            with self._lock:
                rows = self._candidates.get(proposal_id, [])
                if not rows:
                    return None
                latest = rows[-1]
                if latest["tenant_id"] != tenant_id or latest["owner_user_id"] != owner_user_id:
                    return None
                return copy.deepcopy(latest)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT record_json FROM {self._candidate_table} "
                "WHERE proposal_id=%s AND tenant_id=%s AND owner_user_id=%s "
                "ORDER BY revision DESC LIMIT 1",
                (proposal_id, tenant_id, owner_user_id),
            ).fetchone()
        return _decode(row[0]) if row else None

    def history(self, proposal_id: str, tenant_id: str, owner_user_id: str) -> list[dict[str, Any]]:
        if self.backend == "memory":
            with self._lock:
                current = self.get(proposal_id, tenant_id, owner_user_id)
                return copy.deepcopy(self._candidates.get(proposal_id, [])) if current else []
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT record_json FROM {self._candidate_table} "
                "WHERE proposal_id=%s AND tenant_id=%s AND owner_user_id=%s ORDER BY revision",
                (proposal_id, tenant_id, owner_user_id),
            ).fetchall()
        return [_decode(row[0]) for row in rows]

    def append_decision(
        self,
        *,
        current: dict[str, Any],
        expected_etag: str,
        next_record: dict[str, Any],
        decision: dict[str, Any],
        idempotency_key: str,
        fingerprint: str,
    ) -> StoredMutation:
        tenant_id = current["tenant_id"]
        owner_user_id = current["owner_user_id"]
        compound = self._compound(tenant_id, owner_user_id, "decision", idempotency_key)
        response = {"candidate": next_record, "decision": decision}
        if self.backend == "memory":
            with self._lock:
                replay = self._memory_replay(compound, fingerprint)
                if replay:
                    return replay
                rows = self._candidates.get(current["proposal_id"], [])
                if (
                    not rows
                    or self.etag(rows[-1]) != expected_etag
                    or rows[-1]["tenant_id"] != tenant_id
                    or rows[-1]["owner_user_id"] != owner_user_id
                ):
                    raise CandidateDecisionConflict("candidate ETag is stale or scope is invalid")
                if next_record["revision"] != rows[-1]["revision"] + 1:
                    raise CandidateDecisionConflict("candidate revision is not the next immutable revision")
                rows.append(copy.deepcopy(next_record))
                self._decisions.setdefault(current["proposal_id"], []).append(copy.deepcopy(decision))
                self._idempotency[compound] = (fingerprint, copy.deepcopy(response))
                return StoredMutation(copy.deepcopy(response), False)
        with self._connect() as conn:
            replay = self._pg_replay(conn, self._idempotency_table, compound, fingerprint)
            if replay:
                return replay
            latest = conn.execute(
                f"SELECT revision,tenant_id,owner_user_id,etag FROM {self._candidate_table} "
                "WHERE proposal_id=%s ORDER BY revision DESC LIMIT 1 FOR UPDATE",
                (current["proposal_id"],),
            ).fetchone()
            # A concurrent identical request can observe no idempotency row,
            # then wait behind the winner's candidate lock. Recheck after the
            # lock before interpreting the winner's new ETag as stale.
            replay = self._pg_replay(conn, self._idempotency_table, compound, fingerprint)
            if replay:
                return replay
            if (
                latest is None
                or latest[1] != tenant_id
                or latest[2] != owner_user_id
                or latest[3] != expected_etag
            ):
                raise CandidateDecisionConflict("candidate ETag is stale or scope is invalid")
            if next_record["revision"] != latest[0] + 1:
                raise CandidateDecisionConflict("candidate revision is not the next immutable revision")
            conn.execute(
                f"INSERT INTO {self._candidate_table} "
                "(proposal_id,revision,tenant_id,owner_user_id,record_json,etag) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
                (
                    next_record["proposal_id"], next_record["revision"], tenant_id,
                    owner_user_id, json.dumps(next_record, default=str), self.etag(next_record),
                ),
            )
            conn.execute(
                f"INSERT INTO {self._decision_table} "
                "(decision_id,proposal_id,revision,tenant_id,owner_user_id,record_json) "
                "VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                (
                    decision["decision_id"], decision["proposal_id"], decision["revision"],
                    tenant_id, owner_user_id, json.dumps(decision, default=str),
                ),
            )
            conn.execute(
                f"INSERT INTO {self._idempotency_table} (scope_key,fingerprint,response_json) "
                "VALUES (%s,%s,%s::jsonb)",
                (compound, fingerprint, json.dumps(response, default=str)),
            )
        return StoredMutation(copy.deepcopy(response), False)

    def decisions(self, proposal_id: str, tenant_id: str, owner_user_id: str) -> list[dict[str, Any]]:
        if self.get(proposal_id, tenant_id, owner_user_id) is None:
            return []
        if self.backend == "memory":
            with self._lock:
                return copy.deepcopy(self._decisions.get(proposal_id, []))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT record_json FROM {self._decision_table} "
                "WHERE proposal_id=%s AND tenant_id=%s AND owner_user_id=%s ORDER BY revision",
                (proposal_id, tenant_id, owner_user_id),
            ).fetchall()
        return [_decode(row[0]) for row in rows]

    def _record_receipt(
        self,
        *,
        kind: str,
        current: dict[str, Any],
        expected_etag: str,
        receipt: dict[str, Any],
        idempotency_key: str,
        fingerprint: str,
    ) -> StoredMutation:
        if kind not in {"validation", "approval"}:
            raise ValueError("unknown canonical receipt kind")
        tenant_id = current["tenant_id"]
        owner_user_id = current["owner_user_id"]
        compound = self._compound(tenant_id, owner_user_id, kind, idempotency_key)
        memory_bucket = self._validations if kind == "validation" else self._approvals
        id_field = "validation_receipt_id" if kind == "validation" else "approval_decision_id"
        if self.backend == "memory":
            with self._lock:
                replay = self._memory_replay(compound, fingerprint)
                if replay:
                    return replay
                rows = self._candidates.get(current["proposal_id"], [])
                if not rows or self.etag(rows[-1]) != expected_etag:
                    raise CandidateDecisionConflict("candidate ETag is stale")
                if rows[-1]["tenant_id"] != tenant_id or rows[-1]["owner_user_id"] != owner_user_id:
                    raise CandidateDecisionConflict("candidate scope is invalid")
                memory_bucket.setdefault(current["proposal_id"], []).append(copy.deepcopy(receipt))
                self._idempotency[compound] = (fingerprint, copy.deepcopy(receipt))
                return StoredMutation(copy.deepcopy(receipt), False)
        table = self._validation_table if kind == "validation" else self._approval_table
        with self._connect() as conn:
            replay = self._pg_replay(conn, self._idempotency_table, compound, fingerprint)
            if replay:
                return replay
            latest = conn.execute(
                f"SELECT tenant_id,owner_user_id,etag FROM {self._candidate_table} "
                "WHERE proposal_id=%s ORDER BY revision DESC LIMIT 1 FOR UPDATE",
                (current["proposal_id"],),
            ).fetchone()
            replay = self._pg_replay(conn, self._idempotency_table, compound, fingerprint)
            if replay:
                return replay
            if latest is None or latest[0] != tenant_id or latest[1] != owner_user_id:
                raise CandidateDecisionConflict("candidate scope is invalid")
            if latest[2] != expected_etag:
                raise CandidateDecisionConflict("candidate ETag is stale")
            conn.execute(
                f"INSERT INTO {table} "
                f"({id_field},proposal_id,revision,proposal_digest,tenant_id,owner_user_id,record_json) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (
                    receipt[id_field], receipt["proposal_id"], receipt["revision"],
                    receipt["proposal_digest"], tenant_id, owner_user_id,
                    json.dumps(receipt, default=str),
                ),
            )
            conn.execute(
                f"INSERT INTO {self._idempotency_table} (scope_key,fingerprint,response_json) "
                "VALUES (%s,%s,%s::jsonb)",
                (compound, fingerprint, json.dumps(receipt, default=str)),
            )
        return StoredMutation(copy.deepcopy(receipt), False)

    def record_validation(self, **kwargs: Any) -> StoredMutation:
        return self._record_receipt(kind="validation", **kwargs)

    def record_approval(self, **kwargs: Any) -> StoredMutation:
        return self._record_receipt(kind="approval", **kwargs)

    def validation_receipts(
        self, proposal_id: str, tenant_id: str, owner_user_id: str
    ) -> list[dict[str, Any]]:
        return self._receipts("validation", proposal_id, tenant_id, owner_user_id)

    def approval_receipts(
        self, proposal_id: str, tenant_id: str, owner_user_id: str
    ) -> list[dict[str, Any]]:
        return self._receipts("approval", proposal_id, tenant_id, owner_user_id)

    def _receipts(
        self, kind: str, proposal_id: str, tenant_id: str, owner_user_id: str
    ) -> list[dict[str, Any]]:
        if self.get(proposal_id, tenant_id, owner_user_id) is None:
            return []
        bucket = self._validations if kind == "validation" else self._approvals
        if self.backend == "memory":
            with self._lock:
                return copy.deepcopy(bucket.get(proposal_id, []))
        table = self._validation_table if kind == "validation" else self._approval_table
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT record_json FROM {table} "
                "WHERE proposal_id=%s AND tenant_id=%s AND owner_user_id=%s ORDER BY created_at",
                (proposal_id, tenant_id, owner_user_id),
            ).fetchall()
        return [_decode(row[0]) for row in rows]
