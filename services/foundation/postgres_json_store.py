from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


_PG_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_pg_identifier(identifier: str) -> str:
    parts = identifier.split(".")
    if not parts or any(_PG_IDENTIFIER_RE.fullmatch(part) is None for part in parts):
        raise ValueError(f"Invalid Postgres identifier: {identifier}")
    return ".".join(f'"{part}"' for part in parts)


class PostgresJsonOwnerStore:
    """Small JSONB owner-store used by control-plane services.

    The table is intentionally service-owned.  Cross-service consumers should
    use the owning API surface or a read-only DB role.
    """

    def __init__(
        self,
        *,
        dsn: str,
        table: str,
        owner_service: str,
        bootstrap: bool = True,
        read_only: bool = False,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required")
        self.dsn = dsn
        self.table_name = table
        self.table = quote_pg_identifier(table)
        self.schema = table.split(".", 1)[0] if "." in table else ""
        self.owner_service = owner_service
        self.read_only = read_only
        if bootstrap and not read_only:
            self.bootstrap()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                f"psycopg is required when {self.owner_service} selects a Postgres owner store"
            ) from exc
        return psycopg.connect(self.dsn)

    def bootstrap(self) -> None:
        with self._connect() as conn:
            if self.schema:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {quote_pg_identifier(self.schema)}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    record_id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    def put(self, record_id: str, payload: Dict[str, Any]) -> None:
        if not record_id:
            raise ValueError("record_id is required")
        if self.read_only:
            raise PermissionError(
                f"{self.table_name} is read-only for this store; "
                f"writes must go through {self.owner_service}"
            )
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table} (record_id, payload)
                VALUES (%s, %s::jsonb)
                ON CONFLICT (record_id)
                DO UPDATE SET payload = EXCLUDED.payload, updated_at = now()
                """,
                (record_id, json.dumps(payload, ensure_ascii=True, sort_keys=True)),
            )

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(
                f"SELECT payload FROM {self.table} WHERE record_id = %s",
                (record_id,),
            )
            row = self._fetch_one(cursor)
        if row is None:
            return None
        payload = row[0] if isinstance(row, tuple) else row.get("payload")
        return self._decode_payload(payload)

    def list_all(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(f"SELECT payload FROM {self.table} ORDER BY updated_at ASC")
            rows = cursor.fetchall()
        records: List[Dict[str, Any]] = []
        for row in rows:
            payload = row[0] if isinstance(row, tuple) else row.get("payload")
            decoded = self._decode_payload(payload)
            if decoded is not None:
                records.append(decoded)
        return records

    @staticmethod
    def _decode_payload(payload: Any) -> Optional[Dict[str, Any]]:
        if isinstance(payload, str):
            payload = json.loads(payload)
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _fetch_one(cursor: Any) -> Any:
        if hasattr(cursor, "fetchone"):
            return cursor.fetchone()
        rows = cursor.fetchall()
        return rows[0] if rows else None
