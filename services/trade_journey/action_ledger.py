"""Durable idempotency ledger for governed Trade Journey actions."""
from __future__ import annotations

import json
import os
from threading import RLock
from typing import Any, Callable, Mapping, Optional, Protocol


class ActionLedger(Protocol):
    def reserve(self, key: str, request_hash: str) -> tuple[str, Optional[dict[str, Any]]]: ...
    def complete(self, key: str, request_hash: str, receipt: Mapping[str, Any]) -> None: ...


class MemoryActionLedger:
    """Thread-safe test/development ledger with the production state machine."""

    def __init__(self) -> None:
        self._records: dict[str, tuple[str, str, Optional[dict[str, Any]]]] = {}
        self._lock = RLock()

    def reserve(self, key: str, request_hash: str) -> tuple[str, Optional[dict[str, Any]]]:
        with self._lock:
            existing = self._records.get(key)
            if existing is None:
                self._records[key] = (request_hash, "reserved", None)
                return "new", None
            old_hash, state, receipt = existing
            if old_hash != request_hash:
                return "conflict", None
            if state == "reserved":
                return "pending", None
            return "replay", dict(receipt or {})

    def complete(self, key: str, request_hash: str, receipt: Mapping[str, Any]) -> None:
        with self._lock:
            existing = self._records.get(key)
            if existing is None or existing[0] != request_hash:
                raise RuntimeError("action ledger reservation is missing or conflicts")
            self._records[key] = (request_hash, "completed", dict(receipt))


class PostgresActionLedger:
    """Cross-process ledger using a unique idempotency key reservation."""

    def __init__(self, dsn: str, *, schema: str = "public", connect: Optional[Callable[..., Any]] = None) -> None:
        if not dsn:
            raise ValueError("Trade Journey action ledger Postgres DSN is required")
        if not schema.replace("_", "").isalnum():
            raise ValueError("invalid Postgres schema")
        self.dsn, self.schema = dsn, schema
        if connect is None:
            import psycopg  # type: ignore[import]
            connect = psycopg.connect
        self._connect = connect
        self._bootstrap()

    @property
    def table(self) -> str:
        return f'{self.schema}.trade_journey_action_ledger'

    def _bootstrap(self) -> None:
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
                idempotency_key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('reserved','completed')),
                receipt JSONB,
                reserved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ
            )""")

    def reserve(self, key: str, request_hash: str) -> tuple[str, Optional[dict[str, Any]]]:
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self.table} (idempotency_key,request_hash,state) VALUES (%s,%s,'reserved') ON CONFLICT DO NOTHING RETURNING idempotency_key",
                (key, request_hash),
            )
            if cur.fetchone() is not None:
                return "new", None
            cur.execute(f"SELECT request_hash,state,receipt FROM {self.table} WHERE idempotency_key=%s", (key,))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("action ledger reservation disappeared")
            if row[0] != request_hash:
                return "conflict", None
            if row[1] == "reserved":
                return "pending", None
            receipt = row[2]
            if isinstance(receipt, str):
                receipt = json.loads(receipt)
            return "replay", dict(receipt or {})

    def complete(self, key: str, request_hash: str, receipt: Mapping[str, Any]) -> None:
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {self.table} SET state='completed',receipt=%s::jsonb,completed_at=now() WHERE idempotency_key=%s AND request_hash=%s",
                (json.dumps(dict(receipt), sort_keys=True), key, request_hash),
            )
            if cur.rowcount != 1:
                raise RuntimeError("action ledger reservation is missing or conflicts")


def make_action_ledger(env: Optional[Mapping[str, str]] = None) -> ActionLedger:
    values = os.environ if env is None else env
    backend = values.get("PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_BACKEND", "memory").lower()
    if backend == "memory":
        return MemoryActionLedger()
    if backend == "postgres":
        dsn = values.get("PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_DSN") or values.get("DATABASE_URL")
        return PostgresActionLedger(dsn or "", schema=values.get("PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_SCHEMA", "public"))
    raise ValueError("PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_BACKEND must be memory or postgres")
