"""Durable, tenant-scoped claim/lease state for consultation workflows.

The consultation lifecycle store owns the domain records.  This SQLite store
owns only worker coordination: claims, fencing, bounded blocking, dead-letter
replay, crash recovery, and downstream acknowledgement.  SQLite is used here
because it provides cross-process transactions and uniqueness for the default
single-host deployment without weakening the Postgres domain-store option.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


TERMINAL_STATUSES = frozenset({"completed", "dead_letter"})
CLAIMABLE_STATUSES = frozenset({"pending", "blocked"})


class WorkflowStateError(RuntimeError):
    """Base error for durable workflow coordination."""


class StaleWorkflowClaim(WorkflowStateError):
    """Raised when a worker tries to mutate work after losing its lease."""


@dataclass(frozen=True)
class WorkflowClaim:
    tenant_id: str
    request_id: str
    lease_owner: str
    lease_token: str
    lease_epoch: int
    lease_expires_at: float
    attempt_count: int
    blocked_count: int
    phase: str
    contribution: dict[str, Any] | None
    memo_id: str | None
    handoff_id: str | None


def _decode_json(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise WorkflowStateError("persisted contribution must be a JSON object")
    return parsed


class WorkflowStateStore:
    """SQLite-backed durable state with compare-and-set lease fencing."""

    def __init__(
        self,
        path: str | Path,
        *,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._now = now
        self._bootstrap()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _bootstrap(self) -> None:
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(20):
            try:
                with self._connect() as connection:
                    connection.execute("PRAGMA journal_mode = WAL")
                    self._create_schema(connection)
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                last_error = exc
                time.sleep(min(0.01 * (attempt + 1), 0.2))
        raise WorkflowStateError("workflow state bootstrap remained locked") from last_error

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
                """
                CREATE TABLE IF NOT EXISTS consultation_work_items (
                    tenant_id          TEXT NOT NULL,
                    request_id         TEXT NOT NULL,
                    status             TEXT NOT NULL,
                    phase              TEXT NOT NULL,
                    attempt_count      INTEGER NOT NULL DEFAULT 0,
                    blocked_count      INTEGER NOT NULL DEFAULT 0,
                    replay_count       INTEGER NOT NULL DEFAULT 0,
                    lease_owner        TEXT,
                    lease_token        TEXT,
                    lease_epoch        INTEGER NOT NULL DEFAULT 0,
                    lease_expires_at   REAL,
                    available_at       REAL NOT NULL,
                    last_error         TEXT,
                    contribution_json  TEXT,
                    memo_id            TEXT,
                    handoff_id         TEXT,
                    acknowledged_at    REAL,
                    created_at         REAL NOT NULL,
                    updated_at         REAL NOT NULL,
                    PRIMARY KEY (tenant_id, request_id),
                    CHECK (status IN (
                        'pending', 'leased', 'blocked', 'dead_letter', 'completed'
                    ))
                )
                """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS consultation_work_claimable_idx
            ON consultation_work_items (
                tenant_id, status, available_at, created_at
            )
            """
        )

    def ensure_request(self, *, tenant_id: str, request_id: str) -> None:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO consultation_work_items (
                    tenant_id, request_id, status, phase, available_at,
                    created_at, updated_at
                ) VALUES (?, ?, 'pending', 'discovered', ?, ?, ?)
                ON CONFLICT (tenant_id, request_id) DO NOTHING
                """,
                (tenant_id, request_id, now, now, now),
            )

    def _recover_expired(self, connection: sqlite3.Connection, *, tenant_id: str) -> None:
        now = self._now()
        connection.execute(
            """
            UPDATE consultation_work_items
               SET status = 'pending',
                   lease_owner = NULL,
                   lease_token = NULL,
                   lease_expires_at = NULL,
                   available_at = ?,
                   last_error = 'lease_expired_before_acknowledgement',
                   updated_at = ?
             WHERE tenant_id = ?
               AND status = 'leased'
               AND lease_expires_at <= ?
            """,
            (now, now, tenant_id, now),
        )

    def claim_next(
        self,
        *,
        tenant_id: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> WorkflowClaim | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be >= 1")
        now = self._now()
        lease_token = uuid.uuid4().hex
        expires_at = now + lease_seconds
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._recover_expired(connection, tenant_id=tenant_id)
                row = connection.execute(
                    """
                    SELECT *
                      FROM consultation_work_items
                     WHERE tenant_id = ?
                       AND status IN ('pending', 'blocked')
                       AND available_at <= ?
                     ORDER BY available_at ASC, created_at ASC, request_id ASC
                     LIMIT 1
                    """,
                    (tenant_id, now),
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None
                changed = connection.execute(
                    """
                    UPDATE consultation_work_items
                       SET status = 'leased',
                           attempt_count = attempt_count + 1,
                           lease_owner = ?,
                           lease_token = ?,
                           lease_epoch = lease_epoch + 1,
                           lease_expires_at = ?,
                           updated_at = ?
                     WHERE tenant_id = ?
                       AND request_id = ?
                       AND status IN ('pending', 'blocked')
                       AND available_at <= ?
                    """,
                    (
                        lease_owner,
                        lease_token,
                        expires_at,
                        now,
                        tenant_id,
                        row["request_id"],
                        now,
                    ),
                ).rowcount
                if changed != 1:
                    raise WorkflowStateError("claim compare-and-set failed")
                claimed = connection.execute(
                    """
                    SELECT *
                      FROM consultation_work_items
                     WHERE tenant_id = ? AND request_id = ?
                    """,
                    (tenant_id, row["request_id"]),
                ).fetchone()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        assert claimed is not None
        return self._claim_from_row(claimed)

    def _claim_from_row(self, row: sqlite3.Row) -> WorkflowClaim:
        return WorkflowClaim(
            tenant_id=str(row["tenant_id"]),
            request_id=str(row["request_id"]),
            lease_owner=str(row["lease_owner"]),
            lease_token=str(row["lease_token"]),
            lease_epoch=int(row["lease_epoch"]),
            lease_expires_at=float(row["lease_expires_at"]),
            attempt_count=int(row["attempt_count"]),
            blocked_count=int(row["blocked_count"]),
            phase=str(row["phase"]),
            contribution=_decode_json(row["contribution_json"]),
            memo_id=str(row["memo_id"]) if row["memo_id"] else None,
            handoff_id=str(row["handoff_id"]) if row["handoff_id"] else None,
        )

    def _update_claim(
        self,
        claim: WorkflowClaim,
        sql: str,
        params: tuple[Any, ...],
    ) -> None:
        now = self._now()
        with self._connect() as connection:
            changed = connection.execute(
                sql,
                (*params, now, claim.tenant_id, claim.request_id, claim.lease_token, now),
            ).rowcount
        if changed != 1:
            raise StaleWorkflowClaim(
                f"claim is stale for {claim.tenant_id}/{claim.request_id}"
            )

    def renew(self, claim: WorkflowClaim, *, lease_seconds: int) -> WorkflowClaim:
        expires_at = self._now() + lease_seconds
        self._update_claim(
            claim,
            """
            UPDATE consultation_work_items
               SET lease_expires_at = ?, updated_at = ?
             WHERE tenant_id = ?
               AND request_id = ?
               AND lease_token = ?
               AND status = 'leased'
               AND lease_expires_at > ?
            """,
            (expires_at,),
        )
        row = self.get(tenant_id=claim.tenant_id, request_id=claim.request_id)
        if row is None:
            raise WorkflowStateError("renewed workflow disappeared")
        return self._claim_from_mapping(row)

    def _claim_from_mapping(self, row: Mapping[str, Any]) -> WorkflowClaim:
        return WorkflowClaim(
            tenant_id=str(row["tenant_id"]),
            request_id=str(row["request_id"]),
            lease_owner=str(row["lease_owner"]),
            lease_token=str(row["lease_token"]),
            lease_epoch=int(row["lease_epoch"]),
            lease_expires_at=float(row["lease_expires_at"]),
            attempt_count=int(row["attempt_count"]),
            blocked_count=int(row["blocked_count"]),
            phase=str(row["phase"]),
            contribution=(
                dict(row["contribution"])
                if isinstance(row.get("contribution"), Mapping)
                else None
            ),
            memo_id=str(row["memo_id"]) if row.get("memo_id") else None,
            handoff_id=str(row["handoff_id"]) if row.get("handoff_id") else None,
        )

    def save_progress(
        self,
        claim: WorkflowClaim,
        *,
        phase: str,
        contribution: Mapping[str, Any] | None = None,
        memo_id: str | None = None,
        handoff_id: str | None = None,
    ) -> None:
        contribution_json = (
            json.dumps(dict(contribution), sort_keys=True, separators=(",", ":"))
            if contribution is not None
            else None
        )
        self._update_claim(
            claim,
            """
            UPDATE consultation_work_items
               SET phase = ?,
                   contribution_json = COALESCE(?, contribution_json),
                   memo_id = COALESCE(?, memo_id),
                   handoff_id = COALESCE(?, handoff_id),
                   last_error = NULL,
                   updated_at = ?
             WHERE tenant_id = ?
               AND request_id = ?
               AND lease_token = ?
               AND status = 'leased'
               AND lease_expires_at > ?
            """,
            (phase, contribution_json, memo_id, handoff_id),
        )

    def block(
        self,
        claim: WorkflowClaim,
        *,
        reason: str,
        max_blocked_attempts: int,
        retry_after_seconds: int,
    ) -> str:
        if max_blocked_attempts < 1:
            raise ValueError("max_blocked_attempts must be >= 1")
        next_blocked_count = claim.blocked_count + 1
        status = (
            "dead_letter"
            if next_blocked_count >= max_blocked_attempts
            else "blocked"
        )
        available_at = self._now() + max(0, retry_after_seconds)
        self._update_claim(
            claim,
            """
            UPDATE consultation_work_items
               SET status = ?,
                   blocked_count = ?,
                   available_at = ?,
                   lease_owner = NULL,
                   lease_token = NULL,
                   lease_expires_at = NULL,
                   last_error = ?,
                   updated_at = ?
             WHERE tenant_id = ?
               AND request_id = ?
               AND lease_token = ?
               AND status = 'leased'
               AND lease_expires_at > ?
            """,
            (status, next_blocked_count, available_at, reason),
        )
        return status

    def complete(
        self,
        claim: WorkflowClaim,
        *,
        memo_id: str,
        handoff_id: str,
    ) -> None:
        acknowledged_at = self._now()
        self._update_claim(
            claim,
            """
            UPDATE consultation_work_items
               SET status = 'completed',
                   phase = 'acknowledged',
                   memo_id = ?,
                   handoff_id = ?,
                   acknowledged_at = ?,
                   lease_owner = NULL,
                   lease_token = NULL,
                   lease_expires_at = NULL,
                   last_error = NULL,
                   updated_at = ?
             WHERE tenant_id = ?
               AND request_id = ?
               AND lease_token = ?
               AND status = 'leased'
               AND lease_expires_at > ?
            """,
            (memo_id, handoff_id, acknowledged_at),
        )

    def replay_dead_letter(self, *, tenant_id: str, request_id: str) -> bool:
        now = self._now()
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE consultation_work_items
                   SET status = 'pending',
                       phase = 'replayed',
                       blocked_count = 0,
                       replay_count = replay_count + 1,
                       available_at = ?,
                       lease_owner = NULL,
                       lease_token = NULL,
                       lease_expires_at = NULL,
                       last_error = NULL,
                       updated_at = ?
                 WHERE tenant_id = ?
                   AND request_id = ?
                   AND status = 'dead_letter'
                """,
                (now, now, tenant_id, request_id),
            ).rowcount
        return changed == 1

    def get(self, *, tenant_id: str, request_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                  FROM consultation_work_items
                 WHERE tenant_id = ? AND request_id = ?
                """,
                (tenant_id, request_id),
            ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def list_items(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if status:
                rows = connection.execute(
                    """
                    SELECT *
                      FROM consultation_work_items
                     WHERE tenant_id = ? AND status = ?
                     ORDER BY created_at ASC, request_id ASC
                    """,
                    (tenant_id, status),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT *
                      FROM consultation_work_items
                     WHERE tenant_id = ?
                     ORDER BY created_at ASC, request_id ASC
                    """,
                    (tenant_id,),
                ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def counts(self, *, tenant_id: str) -> dict[str, int]:
        result = {
            "pending": 0,
            "leased": 0,
            "blocked": 0,
            "dead_letter": 0,
            "completed": 0,
        }
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                  FROM consultation_work_items
                 WHERE tenant_id = ?
                 GROUP BY status
                """,
                (tenant_id,),
            ).fetchall()
        for row in rows:
            result[str(row["status"])] = int(row["count"])
        return result

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["contribution"] = _decode_json(result.pop("contribution_json"))
        return result
