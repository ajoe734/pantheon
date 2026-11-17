"""SQLite current-record backend for the latest market snapshot (design SD_MARKET_DATA_ONE_WRITE_PER_DAY §4.2).

One row per (symbol, dataset): the newest version replaces the older one in the same
transaction, so no superseded version exists after commit and nothing is appended after an
older copy.  The row carries the already-encoded record text (the caller owns the codec) and
its digest; a write whose digest equals the stored one touches no row.

This module is deliberately codec-free (no import of ``requirement_state``) so the store
class there can use it without a cycle.  Every method opens its own short transaction:

* writes use ``BEGIN IMMEDIATE`` with a bounded busy timeout, so a second writer waits at
  most ``busy_timeout_ms`` and then gets the retryable :class:`SnapshotWriteBusy`;
* WAL mode keeps readers unblocked while a writer commits.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Mapping

SCHEMA_VERSION = 1
DEFAULT_BUSY_TIMEOUT_MS = 2000
DEFAULT_PAGE_SIZE = 4096
VACUUM_STEP_PAGES = 200  # design 4.2: bounded incremental_vacuum step after each commit

Key = tuple[str, str]
Row = tuple[str, str, str]  # (digest, record_text, observed_at)


class SnapshotStoreError(RuntimeError):
    """The SQLite current-record store is unreadable or invalid (fail closed, readable reason)."""


class SnapshotWriteBusy(SnapshotStoreError):
    """Another writer held the database past the busy timeout; the caller may retry."""

    retryable = True


class SqliteSnapshotBackend:
    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.page_size = page_size
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        # Reads use one connection per thread so a writer holding ``self._lock`` for its
        # transaction never blocks a reader (WAL readers see the last committed state).
        self._local = threading.local()
        self._read_conns: list[sqlite3.Connection] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # -- connection ---------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                conn = sqlite3.connect(
                    self.path,
                    timeout=self.busy_timeout_ms / 1000.0,
                    isolation_level=None,  # explicit BEGIN/COMMIT
                    check_same_thread=False,
                )
                conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
                conn.execute(f"PRAGMA page_size={int(self.page_size)}")  # only effective before the first table
                conn.execute("PRAGMA auto_vacuum=INCREMENTAL")  # only effective on a new database
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("PRAGMA wal_autocheckpoint=256")
                conn.execute("PRAGMA journal_size_limit=4194304")
            except sqlite3.Error as exc:
                raise SnapshotStoreError(f"snapshot current store cannot be opened: {self.path}: {exc}") from exc
            self._conn = conn
        return self._conn

    def _rconn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            try:
                conn = sqlite3.connect(
                    self.path, timeout=self.busy_timeout_ms / 1000.0, isolation_level=None, check_same_thread=False
                )
                conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
                conn.execute("PRAGMA query_only=ON")
            except sqlite3.Error as exc:
                raise SnapshotStoreError(f"snapshot current store cannot be opened: {self.path}: {exc}") from exc
            self._local.conn = conn
            with self._lock:
                self._read_conns.append(conn)
        return conn

    def close(self) -> None:
        with self._lock:
            for conn in self._read_conns:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._read_conns.clear()
            self._local = threading.local()
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS snapshot_current ("
                    " symbol TEXT NOT NULL, dataset TEXT NOT NULL,"
                    " digest TEXT NOT NULL, record TEXT NOT NULL, observed_at TEXT NOT NULL,"
                    " PRIMARY KEY (symbol, dataset)) WITHOUT ROWID"
                )
                conn.execute("CREATE TABLE IF NOT EXISTS snapshot_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID")
                row = conn.execute("SELECT v FROM snapshot_meta WHERE k='schema_version'").fetchone()
                if row is None:
                    conn.execute("INSERT OR IGNORE INTO snapshot_meta(k, v) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
                elif row[0] != str(SCHEMA_VERSION):
                    raise SnapshotStoreError(f"snapshot current store schema {row[0]!r} is not supported")
            except sqlite3.Error as exc:
                raise SnapshotStoreError(f"snapshot current store is unreadable: {self.path}: {exc}") from exc

    @contextmanager
    def _write_txn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    raise SnapshotWriteBusy(
                        f"snapshot current store is busy (writer lock held > {self.busy_timeout_ms} ms); retry"
                    ) from exc
                raise SnapshotStoreError(f"snapshot current store write failed: {exc}") from exc
            try:
                yield conn
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            try:
                conn.execute("COMMIT")
            except sqlite3.Error as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise SnapshotStoreError(f"snapshot current store commit failed: {exc}") from exc

    # -- reads --------------------------------------------------------------------------
    def get(self, symbol: str, dataset: str) -> Row | None:
        try:
            row = self._rconn().execute(
                "SELECT digest, record, observed_at FROM snapshot_current WHERE symbol=? AND dataset=?",
                (symbol, dataset),
            ).fetchone()
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot current store read failed: {exc}") from exc
        return None if row is None else (row[0], row[1], row[2])

    def digests(self) -> dict[Key, str]:
        try:
            return {
                (s, d): g
                for s, d, g in self._rconn().execute("SELECT symbol, dataset, digest FROM snapshot_current")
            }
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot current store read failed: {exc}") from exc

    def keys(self, dataset: str | None = None) -> list[Key]:
        try:
            if dataset is None:
                cur = self._rconn().execute("SELECT symbol, dataset FROM snapshot_current ORDER BY symbol, dataset")
            else:
                cur = self._rconn().execute(
                    "SELECT symbol, dataset FROM snapshot_current WHERE dataset=? ORDER BY symbol", (dataset,)
                )
            return [(s, d) for s, d in cur]
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot current store read failed: {exc}") from exc

    def iter_records(self) -> Iterator[tuple[Key, Row]]:
        """Stream every row ordered by key (a point-in-time read; WAL keeps writers unblocked)."""
        try:
            cursor = self._rconn().execute(
                "SELECT symbol, dataset, digest, record, observed_at FROM snapshot_current ORDER BY symbol, dataset"
            )
            for s, d, g, r, o in cursor:
                yield (s, d), (g, r, o)
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot current store read failed: {exc}") from exc

    # -- writes -------------------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator["SnapshotTransaction"]:
        """One write transaction: reads inside it see the rows it will replace; commit is all-or-nothing."""
        try:
            with self._write_txn() as conn:
                yield SnapshotTransaction(conn)
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot current store write failed: {exc}") from exc
        self._maintain()

    def apply(self, upserts: Mapping[Key, Row], deletes: Iterable[Key] = ()) -> dict[str, int]:
        """Replace/delete rows in ONE transaction; an unchanged digest writes zero rows."""
        with self.transaction() as tx:
            for key, row in upserts.items():
                tx.put(key, row)
            for key in deletes:
                tx.delete(key)
        return {"written": tx.written, "unchanged": tx.unchanged, "deleted": tx.deleted}

    def _maintain(self) -> None:
        """Bounded space reclaim after a commit; never fails the write that already committed."""
        with self._lock:
            try:
                conn = self._connect()
                conn.execute(f"PRAGMA incremental_vacuum({VACUUM_STEP_PAGES})").fetchall()
            except sqlite3.Error:
                pass

    def total_changes(self) -> int:
        with self._lock:
            return self._connect().total_changes

    def checkpoint(self) -> None:
        with self._lock:
            try:
                self._connect().execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error as exc:
                raise SnapshotStoreError(f"snapshot current store checkpoint failed: {exc}") from exc

    # -- stats --------------------------------------------------------------------------
    def stats(self) -> dict[str, int]:
        try:
            rows, live = self._rconn().execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(record AS BLOB))), 0) FROM snapshot_current"
            ).fetchone()
            max_record = self._rconn().execute(
                "SELECT COALESCE(MAX(LENGTH(CAST(record AS BLOB))), 0) FROM snapshot_current"
            ).fetchone()[0]
        except sqlite3.Error as exc:
            raise SnapshotStoreError(f"snapshot current store read failed: {exc}") from exc
        db_bytes = _size(self.path)
        wal_bytes = _size(Path(str(self.path) + "-wal"))
        return {
            "record_count": int(rows),
            "live_bytes": int(live),
            "max_record_bytes": int(max_record),
            "db_bytes": db_bytes,
            "wal_bytes": wal_bytes,
            "file_bytes": db_bytes + wal_bytes,
        }


class SnapshotTransaction:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.written = self.unchanged = self.deleted = 0

    def get(self, key: Key) -> Row | None:
        row = self._conn.execute(
            "SELECT digest, record, observed_at FROM snapshot_current WHERE symbol=? AND dataset=?", key
        ).fetchone()
        return None if row is None else (row[0], row[1], row[2])

    def put(self, key: Key, row: Row) -> bool:
        """Replace the row in place; False (and no row touched) when the stored digest is equal."""
        digest, record, observed_at = row
        prior = self._conn.execute(
            "SELECT digest FROM snapshot_current WHERE symbol=? AND dataset=?", key
        ).fetchone()
        if prior is not None and prior[0] == digest:
            self.unchanged += 1
            return False
        self._conn.execute(
            "INSERT OR REPLACE INTO snapshot_current(symbol, dataset, digest, record, observed_at) VALUES (?,?,?,?,?)",
            (key[0], key[1], digest, record, observed_at),
        )
        self.written += 1
        return True

    def delete(self, key: Key) -> bool:
        cur = self._conn.execute("DELETE FROM snapshot_current WHERE symbol=? AND dataset=?", key)
        self.deleted += cur.rowcount
        return cur.rowcount > 0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


__all__ = [
    "SnapshotStoreError",
    "SnapshotWriteBusy",
    "SqliteSnapshotBackend",
    "DEFAULT_BUSY_TIMEOUT_MS",
]
