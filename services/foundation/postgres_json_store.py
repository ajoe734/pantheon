from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional


_PG_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_pg_identifier(identifier: str) -> str:
    parts = identifier.split(".")
    if not parts or any(_PG_IDENTIFIER_RE.fullmatch(part) is None for part in parts):
        raise ValueError(f"Invalid Postgres identifier: {identifier}")
    return ".".join(f'"{part}"' for part in parts)


def _fetch_one(cursor: Any) -> Any:
    if hasattr(cursor, "fetchone"):
        return cursor.fetchone()
    rows = cursor.fetchall()
    return rows[0] if rows else None


def ensure_postgres_schema(conn: Any, schema: str) -> None:
    """Create a schema, or accept a pre-created schema for restricted roles."""

    clean_schema = str(schema or "").strip()
    if not clean_schema:
        return
    try:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {quote_pg_identifier(clean_schema)}")
        return
    except Exception as exc:
        sqlstate = getattr(exc, "sqlstate", "")
        if sqlstate not in ("42501", "23505"):
            raise
        if hasattr(conn, "rollback"):
            conn.rollback()
        if sqlstate == "23505":
            # Two owner-store instances racing their first bootstrap can both
            # pass "IF NOT EXISTS" before either commits (23505 = unique
            # violation on pg_namespace). The schema now exists either way;
            # only re-raise if it somehow still doesn't.
            cursor = conn.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                (clean_schema,),
            )
            if _fetch_one(cursor) is not None:
                return
            raise
        # Restricted runtime roles may have USAGE/CREATE on a pre-provisioned
        # schema but no database-level CREATE privilege.  After the failed
        # CREATE SCHEMA attempt, the transaction must be reset before probing.
        cursor = conn.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            (clean_schema,),
        )
        if _fetch_one(cursor) is not None:
            return
        raise


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

    @contextmanager
    def _use_conn(self, conn: Optional[Any]) -> Iterator[Any]:
        """Reuse a caller-supplied transaction connection, or open+commit one."""
        if conn is not None:
            yield conn
            return
        with self._connect() as owned_conn:
            yield owned_conn

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """Yield one connection so multiple owner-store calls share one commit.

        Callers pass the yielded ``conn`` into ``compare_and_set``/``put``/
        ``insert_if_absent`` via their ``conn=`` parameter so a mutation and its
        idempotent receipt land in the same database transaction instead of two
        separate auto-committed connections.
        """
        with self._connect() as conn:
            yield conn

    def bootstrap(self) -> None:
        with self._connect() as conn:
            # Reviewer finding 9: "CREATE ... IF NOT EXISTS" is not actually
            # race-free under concurrent DDL from two fresh processes
            # bootstrapping the same table for the first time — Postgres can
            # raise 23505 (unique_violation on the catalog) *or* 42P07
            # (duplicate_table/duplicate_object), depending on timing,
            # neither of which is a real failure once the schema/table
            # exists either way. A session-scoped advisory transaction lock
            # keyed on this table's name serializes the whole
            # schema-then-table bootstrap sequence across concurrent
            # processes: the loser blocks until the winner's bootstrap
            # transaction commits, then finds the schema/table already
            # present and its own DDL is a genuine no-op — closing the race
            # at the source instead of only catching more error codes after
            # the fact. The lock is released automatically when this
            # transaction commits (this connection's implicit commit on
            # context-manager exit) or rolls back.
            self.advisory_xact_lock(self.table_name, conn=conn)
            ensure_postgres_schema(conn, self.schema)
            try:
                conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.table} (
                        record_id TEXT PRIMARY KEY,
                        payload JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            except Exception as exc:
                # Belt-and-suspenders: even with the advisory lock above,
                # tolerate both duplicate error codes a concurrent winner
                # (e.g. a process not holding/respecting this lock, or a
                # legacy caller) could still produce. The table now exists
                # either way, so this is not a real failure.
                if getattr(exc, "sqlstate", "") not in ("23505", "42P07"):
                    raise
                if hasattr(conn, "rollback"):
                    conn.rollback()

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
                INSERT INTO {self.table} (record_id, payload, updated_at)
                VALUES (%s, %s::jsonb, now())
                ON CONFLICT (record_id) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
                """,
                (record_id, json.dumps(payload, ensure_ascii=True, sort_keys=True)),
            )

    def compare_and_set(
        self,
        record_id: str,
        expected_payload: Optional[Dict[str, Any]],
        payload: Dict[str, Any],
        *,
        conn: Optional[Any] = None,
    ) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Atomically replace one row only when its JSONB snapshot matches.

        ``expected_payload=None`` means the row must not exist.  Existing-row
        updates use JSONB equality in the ``UPDATE`` predicate, so competing
        service instances cannot both commit from the same stale snapshot.
        The returned payload is the durable canonical value after the attempt.

        Pass ``conn`` (from :meth:`transaction`) to commit this write in the
        same transaction as a companion receipt/outbox write; otherwise this
        opens and commits its own connection.
        """

        if not record_id:
            raise ValueError("record_id is required")
        if self.read_only:
            raise PermissionError(
                f"{self.table_name} is read-only for this store; "
                f"writes must go through {self.owner_service}"
            )
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        with self._use_conn(conn) as conn:
            if expected_payload is None:
                cursor = conn.execute(
                    f"""
                    INSERT INTO {self.table} (record_id, payload, updated_at)
                    VALUES (%s, %s::jsonb, now())
                    ON CONFLICT (record_id) DO NOTHING
                    RETURNING payload
                    """,
                    (record_id, encoded),
                )
            else:
                cursor = conn.execute(
                    f"""
                    UPDATE {self.table}
                    SET payload = %s::jsonb, updated_at = now()
                    WHERE record_id = %s AND payload = %s::jsonb
                    RETURNING payload
                    """,
                    (
                        encoded,
                        record_id,
                        json.dumps(expected_payload, ensure_ascii=True, sort_keys=True),
                    ),
                )
            row = self._fetch_one(cursor)
            if row is not None:
                canonical = row[0] if isinstance(row, tuple) else row.get("payload")
                return True, self._decode_payload(canonical)

            current_cursor = conn.execute(
                f"SELECT payload FROM {self.table} WHERE record_id = %s",
                (record_id,),
            )
            current_row = self._fetch_one(current_cursor)
            if current_row is None:
                return False, None
            current = (
                current_row[0]
                if isinstance(current_row, tuple)
                else current_row.get("payload")
            )
            return False, self._decode_payload(current)

    def delete_if_matches(self, record_id: str, expected_payload: Dict[str, Any]) -> bool:
        """Delete one row only if it is still the supplied canonical snapshot."""

        if not record_id:
            raise ValueError("record_id is required")
        if self.read_only:
            raise PermissionError(
                f"{self.table_name} is read-only for this store; "
                f"writes must go through {self.owner_service}"
            )
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                DELETE FROM {self.table}
                WHERE record_id = %s AND payload = %s::jsonb
                RETURNING payload
                """,
                (
                    record_id,
                    json.dumps(expected_payload, ensure_ascii=True, sort_keys=True),
                ),
            )
            return self._fetch_one(cursor) is not None

    def insert_if_absent(
        self,
        record_id: str,
        payload: Dict[str, Any],
        *,
        unique_fields: tuple[str, ...] = (),
        conn: Optional[Any] = None,
    ) -> tuple[bool, Dict[str, Any]]:
        """Atomically insert a record or return its identity collision.

        ``unique_fields`` defines an optional composite identity in addition to
        ``record_id``.  Delivery inboxes use it to reserve an idempotency key
        even when a divergent replay supplies a different event ID.  The table
        lock keeps the read/decision/write sequence in one transaction; normal
        ``put`` writers acquire a conflicting row-exclusive table lock.

        Pass ``conn`` (from :meth:`transaction`) to commit this insert in the
        same transaction as a companion owner-state write, so a command
        receipt and the state it records land atomically.
        """

        if not record_id:
            raise ValueError("record_id is required")
        if self.read_only:
            raise PermissionError(
                f"{self.table_name} is read-only for this store; "
                f"writes must go through {self.owner_service}"
            )
        fields = tuple(str(field).strip() for field in unique_fields)
        if any(not field for field in fields):
            raise ValueError("unique_fields must contain non-empty field names")
        missing = [field for field in fields if field not in payload]
        if missing:
            raise ValueError(f"payload missing unique fields: {missing}")

        with self._use_conn(conn) as conn:
            conn.execute(f"LOCK TABLE {self.table} IN SHARE ROW EXCLUSIVE MODE")
            direct_cursor = conn.execute(
                f"SELECT payload FROM {self.table} WHERE record_id = %s",
                (record_id,),
            )
            direct_row = self._fetch_one(direct_cursor)
            if direct_row is not None:
                existing = direct_row[0] if isinstance(direct_row, tuple) else direct_row.get("payload")
                return False, dict(self._decode_payload(existing) or {})

            if fields:
                cursor = conn.execute(f"SELECT payload FROM {self.table} ORDER BY updated_at ASC")
                for row in cursor.fetchall():
                    existing = row[0] if isinstance(row, tuple) else row.get("payload")
                    decoded = self._decode_payload(existing)
                    if decoded is not None and all(decoded.get(field) == payload.get(field) for field in fields):
                        return False, dict(decoded)

            conn.execute(
                f"""
                INSERT INTO {self.table} (record_id, payload)
                VALUES (%s, %s::jsonb)
                ON CONFLICT (record_id)
                DO NOTHING
                """,
                (record_id, json.dumps(payload, ensure_ascii=True, sort_keys=True)),
            )
        return True, dict(payload)

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

    def list_all(self, *, conn: Optional[Any] = None) -> List[Dict[str, Any]]:
        """List every row's decoded payload.

        Pass ``conn`` (from :meth:`transaction`) to read within an
        in-progress transaction — e.g. under an advisory lock held for a
        read-validate-write sequence — instead of opening a second,
        independent connection that would not see uncommitted-but-locked
        state consistently.
        """
        with self._use_conn(conn) as conn:
            cursor = conn.execute(f"SELECT payload FROM {self.table} ORDER BY updated_at ASC")
            rows = cursor.fetchall()
        records: List[Dict[str, Any]] = []
        for row in rows:
            payload = row[0] if isinstance(row, tuple) else row.get("payload")
            decoded = self._decode_payload(payload)
            if decoded is not None:
                records.append(decoded)
        return records

    def lock_table(self, *, conn: Any) -> None:
        """Acquire this table's SHARE ROW EXCLUSIVE lock on its own, with no insert.

        A caller that writes to two different owner-store tables (e.g. an
        entries table and a companion receipts table) in one transaction
        must always acquire both tables' locks in the same global order,
        regardless of which table it happens to write to first logically —
        otherwise two lawful, unrelated transactions that touch the same two
        tables in opposite orders can deadlock (each holds one table's lock
        and waits on the other's). ``insert_if_absent`` always takes this
        same lock internally; this lets a caller take it explicitly, up
        front, before any other table access in the same transaction, so a
        consistent acquisition order can be enforced across every method
        that writes to both tables.
        """
        conn.execute(f"LOCK TABLE {self.table} IN SHARE ROW EXCLUSIVE MODE")

    def advisory_xact_lock(self, key: str, *, conn: Any) -> None:
        """Take a Postgres session-scoped advisory transaction lock keyed on
        an arbitrary string, released automatically at transaction end
        (commit or rollback) — never leaked across requests/connections.

        Used to serialize a read-validate-write sequence across concurrent
        callers keyed on some aggregate identity (e.g. a strategy_id) when
        the invariant being protected spans multiple rows/versions and
        cannot be expressed as a single-row compare-and-set.
        """
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))

    @staticmethod
    def _decode_payload(payload: Any) -> Optional[Dict[str, Any]]:
        if isinstance(payload, str):
            payload = json.loads(payload)
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _fetch_one(cursor: Any) -> Any:
        return _fetch_one(cursor)
