"""
PostgreSQL Relational Projection Store for Trade Journey.

LIFECYCLE-PROJ-STORE-001: Provides additive relational schema management,
typed persistence interfaces, advisory locking, and atomic batch projection transactions.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from services.trade_journey.materializer import SHARED_IDENTIFIER_TYPES

logger = logging.getLogger(__name__)

DEFAULT_PROJECTION_SCHEMA = "trade_journey_projection"
DEFAULT_PROJECTION_TIMEOUT_SECONDS = 10.0
DEFAULT_PROJECTION_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_PROJECTION_STATEMENT_TIMEOUT_SECONDS = 10.0
DEFAULT_PROJECTION_LOCK_TIMEOUT_SECONDS = 10.0
DEFAULT_PROJECTION_MIGRATION_STATEMENT_TIMEOUT_SECONDS = 300.0
DEFAULT_PROJECTION_MIGRATION_LOCK_TIMEOUT_SECONDS = 30.0
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
INITIAL_MIGRATION_PATH = (
    MIGRATIONS_DIR / "001_create_trade_journey_projection_schema.sql"
)
CONTROLLER_COLUMNS = (
    "controller_id, tenant_scope, environment_scope, checkpoint_seq, "
    "source_high_watermark, backlog_count, projection_revision, "
    "deployment_sha, mode, status, accepted_live, last_poll_at, "
    "last_success_at, last_live_success_at, last_recovery_at, "
    "last_backfill_at, last_replay_at, last_failure_at, "
    "last_error_message, unresolved_quarantine_count, updated_at"
)
PROJECTION_TABLES = ("controller", "event_receipts", "identity_links", "journeys", "journey_stages", "loop_runs", "quarantine")


def _validate_timeout(
    value: Any,
    *,
    name: str = "timeout_seconds",
    default: float = DEFAULT_PROJECTION_TIMEOUT_SECONDS,
) -> float:
    if value is None:
        return float(default)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return float(default)
        value = stripped
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive number (got {value!r})")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite positive number (got {value!r})") from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{name} must be a finite positive number (got {value!r})")
    return parsed


def _safe_close_conn(conn: Any) -> None:
    try:
        conn.close()
    except Exception:
        pass


def _can_accept_kwargs(func: Any) -> bool:
    try:
        sig = inspect.signature(func)
        for param in sig.parameters.values():
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                return True
        return "connect_timeout" in sig.parameters and "options" in sig.parameters
    except (ValueError, TypeError):
        return True


def _is_signature_mismatch_error(exc: TypeError, func: Any) -> bool:
    tb = exc.__traceback__
    if tb is not None and tb.tb_next is not None:
        # The exception was raised inside the Python function body, not by argument binding
        return False
    msg = str(exc)
    name = getattr(func, "__name__", "")
    patterns = (
        "unexpected keyword argument",
        "invalid keyword argument",
        "takes no keyword arguments",
        "takes at most",
        "takes no arguments",
    )
    if any(p in msg for p in patterns):
        return True
    if name and f"{name}() takes" in msg:
        return True
    if "positional argument" in msg and "takes" in msg:
        return True
    return False


def controller_advisory_lock_id(
    controller_id: str, tenant_scope: str, environment_scope: str
) -> int:
    """Return a stable signed-bigint lock key for one controller scope.

    Python's built-in ``hash`` is randomized independently in every process,
    so it cannot coordinate PostgreSQL advisory locks across worker processes.
    """

    identity = f"{controller_id}\x1f{tenant_scope}\x1f{environment_scope}"
    digest = hashlib.sha256(identity.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=True)


class ProjectionStoreException(Exception):
    """Base exception for projection store operations."""


class IdentityConflictException(ProjectionStoreException):
    """Raised when an identity link conflicts with an existing different journey."""


class ConflictingDuplicateException(ProjectionStoreException):
    """Raised when an event ID is reused with a different canonical fingerprint."""


class ConcurrentReceiptClaimException(ProjectionStoreException):
    """Raised when another transaction wins a previously absent event receipt."""


class QuarantineEventException(ProjectionStoreException):
    """Raised or recorded when an event is quarantined."""


@dataclass(frozen=True)
class ControllerStateRow:
    controller_id: str
    tenant_scope: str
    environment_scope: str
    checkpoint_seq: int
    source_high_watermark: int
    backlog_count: int
    projection_revision: int
    deployment_sha: str
    mode: str
    status: str
    accepted_live: bool
    last_poll_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_live_success_at: Optional[datetime] = None
    last_recovery_at: Optional[datetime] = None
    last_backfill_at: Optional[datetime] = None
    last_replay_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    last_error_message: str = ""
    unresolved_quarantine_count: int = 0
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class EventReceiptRow:
    event_id: str
    ingested_seq: int
    fingerprint: str
    tenant_id: str
    environment: str
    journey_id: str
    loop_run_id: str
    source_event_type: str
    created_at: datetime
    disposition: str  # 'applied', 'duplicate', 'ignored', 'quarantined'
    projection_revision: int
    projected_at: Optional[datetime] = None


@dataclass(frozen=True)
class IdentityLinkRow:
    tenant_id: str
    environment: str
    identifier_type: str
    identifier_value: str
    journey_id: str
    first_ingested_seq: int
    last_ingested_seq: int
    first_occurred_at: datetime
    last_occurred_at: datetime
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class JourneyRow:
    tenant_id: str
    environment: str
    journey_id: str
    status: str
    stage_coverage: dict[str, Any]
    is_terminal: bool
    first_occurred_at: datetime
    last_occurred_at: datetime
    first_ingested_seq: int
    last_ingested_seq: int
    current_identity_summary: dict[str, Any] = field(default_factory=dict)
    evidence_summary: dict[str, Any] = field(default_factory=dict)
    diagnostic_summary: dict[str, Any] = field(default_factory=dict)
    loop_run_id: str = ""
    projection_revision: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class JourneyStageRow:
    tenant_id: str
    environment: str
    journey_id: str
    source_event_id: str
    stage_name: str
    stage_status: str
    stage_ordinal: int
    source_ingested_seq: int
    event_sequence: int
    occurred_at: datetime
    recorded_at: Optional[datetime] = None
    contract_fields: dict[str, Any] = field(default_factory=dict)
    evidence_references: list[dict[str, Any]] = field(default_factory=list)
    projection_revision: int = 0
    fingerprint: str = ""


@dataclass(frozen=True)
class LoopRunRow:
    tenant_id: str
    environment: str
    loop_run_id: str
    journey_id: str = ""
    status: str = "active"
    lifecycle_summary: dict[str, Any] = field(default_factory=dict)
    freshness_lineage: dict[str, Any] = field(default_factory=dict)
    contract_payload: dict[str, Any] = field(default_factory=dict)
    projection_revision: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class QuarantineRow:
    event_id: str
    ingested_seq: int
    reason_code: str
    reason_detail: str
    source_event_type: str
    tenant_id: str = ""
    environment: str = ""
    journey_id: str = ""
    fingerprint: str = ""
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    occurrence_count: int = 1
    resolution_status: str = "unresolved"
    resolution_audit_ref: str = ""


@dataclass
class BatchProjectionMutation:
    """Contains all mutations derived from a batch of events to be atomically committed."""

    receipts: list[EventReceiptRow] = field(default_factory=list)
    identity_links: list[IdentityLinkRow] = field(default_factory=list)
    journeys: list[JourneyRow] = field(default_factory=list)
    stages: list[JourneyStageRow] = field(default_factory=list)
    loop_runs: list[LoopRunRow] = field(default_factory=list)
    quarantines: list[QuarantineRow] = field(default_factory=list)

    # Controller updates
    new_checkpoint_seq: int = 0
    source_high_watermark: int = 0
    # Source-side frontier proven under the canonical writer fence. Every source
    # row through this sequence was returned (and receives a disposition) or
    # was absent from the committed source snapshot.
    source_frontier_seq: int = 0
    backlog_count: int = 0
    mode: str = "live"
    status: str = "ok"
    accepted_live: bool = False
    deployment_sha: str = ""
    error_message: str = ""


class ProjectionStore:
    """Postgres implementation of the Trade Journey projection store."""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = DEFAULT_PROJECTION_SCHEMA,
        connect: Optional[Callable[..., Any]] = None,
        bootstrap: bool = False,
        timeout_seconds: float | None = None,
        connect_timeout_seconds: float | None = None,
        statement_timeout_seconds: float | None = None,
        lock_timeout_seconds: float | None = None,
        migration_statement_timeout_seconds: float | None = None,
        migration_lock_timeout_seconds: float | None = None,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for ProjectionStore")
        if not schema.replace("_", "").isalnum():
            raise ValueError("Invalid schema name for ProjectionStore")
        self.dsn = dsn
        self.schema = schema
        base_timeout = (
            _validate_timeout(
                timeout_seconds,
                name="timeout_seconds",
                default=DEFAULT_PROJECTION_TIMEOUT_SECONDS,
            )
            if timeout_seconds is not None
            else DEFAULT_PROJECTION_TIMEOUT_SECONDS
        )
        self.connect_timeout_seconds = _validate_timeout(
            connect_timeout_seconds,
            name="connect_timeout_seconds",
            default=base_timeout,
        )
        self.statement_timeout_seconds = _validate_timeout(
            statement_timeout_seconds,
            name="statement_timeout_seconds",
            default=base_timeout,
        )
        self.lock_timeout_seconds = _validate_timeout(
            lock_timeout_seconds,
            name="lock_timeout_seconds",
            default=base_timeout,
        )
        self.migration_statement_timeout_seconds = _validate_timeout(
            migration_statement_timeout_seconds,
            name="migration_statement_timeout_seconds",
            default=DEFAULT_PROJECTION_MIGRATION_STATEMENT_TIMEOUT_SECONDS,
        )
        self.migration_lock_timeout_seconds = _validate_timeout(
            migration_lock_timeout_seconds,
            name="migration_lock_timeout_seconds",
            default=DEFAULT_PROJECTION_MIGRATION_LOCK_TIMEOUT_SECONDS,
        )
        if connect is None:
            try:
                import psycopg  # type: ignore[import]
            except ImportError as exc:
                raise RuntimeError("psycopg is required for ProjectionStore") from exc
            connect = psycopg.connect
        self._connect = connect
        if bootstrap:
            self.bootstrap_schema()

    def _connect_db(
        self,
        *,
        statement_timeout_seconds: float | None = None,
        lock_timeout_seconds: float | None = None,
        autocommit: bool = False,
    ) -> Any:
        stmt_ms = int(math.ceil((statement_timeout_seconds or self.statement_timeout_seconds) * 1000.0))
        lock_ms = int(math.ceil((lock_timeout_seconds or self.lock_timeout_seconds) * 1000.0))
        connect_timeout_s = max(1, int(math.ceil(self.connect_timeout_seconds)))
        options = f"-c statement_timeout={stmt_ms} -c lock_timeout={lock_ms}"

        lock = threading.Lock()
        outcome: dict[str, Any] = {
            "status": "pending",
            "conn": None,
            "error": None,
        }
        done = threading.Event()

        def _worker() -> None:
            conn = None
            try:
                can_kwargs = _can_accept_kwargs(self._connect)
                if can_kwargs:
                    try:
                        kw: dict[str, Any] = {
                            "connect_timeout": connect_timeout_s,
                            "options": options,
                        }
                        if autocommit:
                            kw["autocommit"] = True
                        conn = self._connect(self.dsn, **kw)
                    except TypeError as exc:
                        if not _is_signature_mismatch_error(exc, self._connect):
                            raise
                        conn = None

                if conn is None:
                    conn = self._connect(self.dsn)
                    if autocommit and hasattr(conn, "autocommit"):
                        conn.autocommit = True
                    with conn.cursor() as cur:
                        cur.execute(
                            f"SET statement_timeout = {stmt_ms}; SET lock_timeout = {lock_ms};"
                        )

                with lock:
                    if outcome["status"] == "pending":
                        outcome["status"] = "success"
                        outcome["conn"] = conn
                        done.set()
                        return

                if conn is not None:
                    _safe_close_conn(conn)
            except BaseException as exc:
                if conn is not None:
                    _safe_close_conn(conn)
                with lock:
                    if outcome["status"] == "pending":
                        outcome["status"] = "error"
                        outcome["error"] = exc
            finally:
                done.set()

        t = threading.Thread(target=_worker, daemon=True, name="projection-store-connect")
        t.start()

        if not done.wait(timeout=self.connect_timeout_seconds):
            conn_to_close = None
            with lock:
                if outcome["status"] == "pending":
                    outcome["status"] = "timed_out"
                elif outcome["status"] == "success":
                    outcome["status"] = "timed_out"
                    conn_to_close = outcome["conn"]
                    outcome["conn"] = None
            if conn_to_close is not None:
                threading.Thread(
                    target=_safe_close_conn,
                    args=(conn_to_close,),
                    daemon=True,
                    name="projection-store-conn-cleanup",
                ).start()
            raise TimeoutError(
                f"ProjectionStore connection to database timed out after {self.connect_timeout_seconds}s"
            )

        with lock:
            if outcome["status"] == "error":
                raise outcome["error"]
            if outcome["status"] == "success":
                return outcome["conn"]
            raise TimeoutError(
                f"ProjectionStore connection to database timed out after {self.connect_timeout_seconds}s"
            )

    def _reconcile_runtime_ddl(self, cur: Any, runtime_role: str) -> None:
        """Transfer only known projection objects, in the bootstrap transaction."""
        from psycopg import sql as pgsql

        if self.schema != DEFAULT_PROJECTION_SCHEMA:
            raise ValueError("Runtime role upgrade requires the known projection schema")
        cur.execute("SELECT oid FROM pg_roles WHERE rolname=%s", (runtime_role,))
        runtime_oid = cur.fetchone()[0]
        cur.execute("SELECT oid, rolname FROM pg_roles WHERE rolname=current_user")
        migration_oid, migration_role = cur.fetchone()
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE oid<>%s AND "
            "(rolsuper OR rolcreaterole OR rolcreatedb OR oid=%s) "
            "AND pg_has_role(%s, oid, 'MEMBER'))",
            (runtime_oid, migration_oid, runtime_oid),
        )
        if cur.fetchone()[0]:
            raise ValueError("Runtime role upgrade refuses inherited administrative authority")
        cur.execute("SELECT oid, nspowner, nspacl FROM pg_namespace WHERE nspname=%s", (self.schema,))
        namespace = cur.fetchone()
        if namespace is None:
            return
        schema_oid, owner, _ = namespace
        if owner not in (runtime_oid, migration_oid):
            raise ValueError("Runtime role upgrade requires a known schema owner")
        # PUBLIC/inherited CREATE cannot be repaired without touching other roles.
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_namespace n, "
            "LATERAL aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a "
            "WHERE n.oid=%s AND a.privilege_type='CREATE' AND a.grantee<>%s "
            "AND (a.grantee=0 OR pg_has_role(%s, a.grantee, 'MEMBER')))",
            (schema_oid, runtime_oid, runtime_oid),
        )
        if cur.fetchone()[0]:
            raise ValueError("Runtime role upgrade refuses PUBLIC or inherited schema CREATE")
        cur.execute(
            "SELECT c.relname, c.relkind, c.relowner, p.relname FROM pg_class c "
            "LEFT JOIN pg_index i ON i.indexrelid=c.oid "
            "LEFT JOIN pg_class p ON p.oid=i.indrelid WHERE c.relnamespace=%s",
            (schema_oid,),
        )
        objects = cur.fetchall()
        if any(owner not in (runtime_oid, migration_oid) or not (
            kind == 'r' and name in PROJECTION_TABLES or kind == 'i' and parent in PROJECTION_TABLES
        ) for name, kind, owner, parent in objects):
            raise ValueError("Runtime role upgrade refuses unknown projection objects or owners")
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE pronamespace=%s) OR "
            "EXISTS (SELECT 1 FROM pg_type WHERE typnamespace=%s AND typrelid=0 AND typelem=0)",
            (schema_oid, schema_oid),
        )
        if cur.fetchone()[0]:
            raise ValueError("Runtime role upgrade refuses custom projection routines or types")
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_class c, "
            "LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) a "
            "WHERE c.relnamespace=%s AND c.relkind='r' AND a.privilege_type='TRIGGER' "
            "AND a.grantee<>%s AND (a.grantee=0 OR pg_has_role(%s, a.grantee, 'MEMBER'))) "
            "OR EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "WHERE c.relnamespace=%s AND NOT t.tgisinternal)",
            (schema_oid, runtime_oid, runtime_oid, schema_oid),
        )
        if cur.fetchone()[0]:
            raise ValueError("Runtime role upgrade refuses inherited TRIGGER or custom triggers")
        # All admission checks precede changes; failures below roll back ownership and ACLs.
        schema = pgsql.Identifier(self.schema)
        authority = pgsql.Identifier(migration_role)
        runtime = pgsql.Identifier(runtime_role)
        for name, kind, owner, _ in objects:
            if kind == 'r':
                table = pgsql.Identifier(name)
                if owner == runtime_oid:
                    cur.execute(pgsql.SQL("ALTER TABLE {}.{} OWNER TO {}").format(schema, table, authority))
                cur.execute(pgsql.SQL("REVOKE TRIGGER ON {}.{} FROM {} RESTRICT").format(schema, table, runtime))
        if namespace[1] == runtime_oid:
            cur.execute(pgsql.SQL("ALTER SCHEMA {} OWNER TO {}").format(schema, authority))
        cur.execute(pgsql.SQL("REVOKE CREATE ON SCHEMA {} FROM {} RESTRICT").format(schema, runtime))

    def bootstrap_schema(
        self,
        *,
        runtime_role: str | None = None,
        reconcile_runtime: bool = False,
        statement_timeout_seconds: float | None = None,
        lock_timeout_seconds: float | None = None,
    ) -> None:
        """Apply the versioned migration explicitly with migration credentials."""
        stmt_timeout = _validate_timeout(
            statement_timeout_seconds,
            name="statement_timeout_seconds",
            default=self.migration_statement_timeout_seconds,
        )
        lk_timeout = _validate_timeout(
            lock_timeout_seconds,
            name="lock_timeout_seconds",
            default=self.migration_lock_timeout_seconds,
        )
        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql")) if MIGRATIONS_DIR.is_dir() else [INITIAL_MIGRATION_PATH]
        tx_files = [f for f in migration_files if "CONCURRENTLY" not in f.read_text(encoding="utf-8").upper()]
        concurrent_files = [f for f in migration_files if "CONCURRENTLY" in f.read_text(encoding="utf-8").upper()]

        def _apply_grants(cur: Any, role: str) -> None:
            from psycopg import sql as pgsql

            cur.execute(
                "SELECT has_schema_privilege(%s, n.oid, 'CREATE') OR "
                "pg_has_role(%s, n.nspowner, 'MEMBER') OR EXISTS ("
                "SELECT 1 FROM pg_class c WHERE c.relnamespace=n.oid "
                "AND (pg_has_role(%s, c.relowner, 'MEMBER') OR EXISTS ("
                "SELECT 1 FROM aclexplode(c.relacl) a WHERE a.privilege_type='TRIGGER' "
                "AND (a.grantee=0 OR pg_has_role(%s, a.grantee, 'MEMBER'))))) "
                "FROM pg_namespace n WHERE n.nspname=%s",
                (role, role, role, role, self.schema),
            )
            if cur.fetchone()[0]:
                raise ValueError("Projection runtime must not hold schema/table DDL authority")
            cur.execute(pgsql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                pgsql.Identifier(self.schema), pgsql.Identifier(role)
            ))
            for table in PROJECTION_TABLES:
                cur.execute(pgsql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {}.{} TO {}").format(
                    pgsql.Identifier(self.schema), pgsql.Identifier(table),
                    pgsql.Identifier(role),
                ))

        with self._connect_db(
            statement_timeout_seconds=stmt_timeout,
            lock_timeout_seconds=lk_timeout,
        ) as conn, conn.cursor() as cur:
            if runtime_role is not None:
                cur.execute(
                    "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname=%s",
                    (runtime_role,),
                )
                role = cur.fetchone()
                if role is None or any(role):
                    raise ValueError("Projection runtime must be an existing non-admin role")
                if reconcile_runtime:
                    self._reconcile_runtime_ddl(cur, runtime_role)
            for migration_file in tx_files:
                cur.execute(migration_file.read_text(encoding="utf-8").replace(DEFAULT_PROJECTION_SCHEMA, self.schema))
            if runtime_role is not None and not concurrent_files:
                _apply_grants(cur, runtime_role)

        if concurrent_files:
            with self._connect_db(
                statement_timeout_seconds=stmt_timeout,
                lock_timeout_seconds=lk_timeout,
                autocommit=True,
            ) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT c.relname FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND NOT i.indisvalid",
                    (self.schema,),
                )
                for (idx_name,) in cur.fetchall():
                    cur.execute(f'REINDEX INDEX CONCURRENTLY "{self.schema}"."{idx_name}"')
                for migration_file in concurrent_files:
                    content = migration_file.read_text(encoding="utf-8").replace(DEFAULT_PROJECTION_SCHEMA, self.schema)
                    for stmt in content.split(";"):
                        if stmt.strip():
                            cur.execute(stmt.strip())
                if runtime_role is not None:
                    _apply_grants(cur, runtime_role)

    def _controller_query(self, *, for_update: bool = False) -> str:
        lock_clause = " FOR UPDATE" if for_update else ""
        return (
            f"SELECT {CONTROLLER_COLUMNS} FROM {self.schema}.controller "
            f"WHERE controller_id=%s AND tenant_scope=%s AND environment_scope=%s{lock_clause}"
        )

    def get_controller_state(
        self, controller_id: str, tenant_scope: str, environment_scope: str
    ) -> Optional[ControllerStateRow]:
        """Loads controller state row without locking."""
        with self._connect_db() as conn, conn.cursor() as cur:
            cur.execute(
                self._controller_query(),
                (controller_id, tenant_scope, environment_scope),
            )
            row = cur.fetchone()
            if not row:
                return None
            return ControllerStateRow(*row)

    def adopt_legacy_baseline(
        self,
        *,
        controller_id: str,
        migration_controller_id: str,
        tenant_scope: str,
        environment_scope: str,
        checkpoint_seq: int,
        deployment_sha: str,
        expected_receipts: int,
        expected_journeys: int,
        expected_loop_runs: int,
    ) -> ControllerStateRow:
        """Seed a non-live controller from an accepted legacy projection.

        This recovery-only operation is deliberately narrower than a normal
        checkpoint update. It fails closed unless the migration controller
        and exact baseline row counts are already durable, no live controller
        exists, no unresolved quarantine remains, and every retained stage
        offset is at or below the accepted legacy checkpoint. A byte-for-byte
        retry of an already adopted baseline is idempotent.

        Adoption never grants read authority: the new controller remains in
        ``recovery``/``repair_only`` with ``accepted_live=false`` until the
        shadow worker polls the retained PostgreSQL source to zero backlog.
        """

        if not controller_id or not migration_controller_id:
            raise ProjectionStoreException("Legacy baseline controller IDs are required")
        if controller_id == migration_controller_id:
            raise ProjectionStoreException(
                "Legacy baseline migration and live controller IDs must be distinct"
            )
        if checkpoint_seq <= 0:
            raise ProjectionStoreException("Legacy baseline checkpoint must be positive")
        expected = {
            "event_receipts": expected_receipts,
            "journeys": expected_journeys,
            "loop_runs": expected_loop_runs,
        }
        if any(value < 0 for value in expected.values()):
            raise ProjectionStoreException("Legacy baseline expected counts must be non-negative")

        def scoped_count(cur: Any, table: str) -> int:
            cur.execute(
                f"""
                SELECT COUNT(*) FROM {self.schema}.{table}
                WHERE (%s IN ('', '*') OR tenant_id=%s)
                  AND (%s IN ('', '*') OR environment=%s)
                """,
                (tenant_scope, tenant_scope, environment_scope, environment_scope),
            )
            return int(cur.fetchone()[0])

        controller_args = (controller_id, tenant_scope, environment_scope)
        migration_args = (
            migration_controller_id,
            tenant_scope,
            environment_scope,
        )

        with self._connect_db() as conn, conn.cursor() as cur:
            # Stable lock order prevents an accidental concurrent shadow start
            # from racing the baseline adoption.
            lock_ids = sorted(
                {
                    controller_advisory_lock_id(
                        controller_id, tenant_scope, environment_scope
                    ),
                    controller_advisory_lock_id(
                        migration_controller_id, tenant_scope, environment_scope
                    ),
                }
            )
            for lock_id in lock_ids:
                cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (lock_id,))
                if not cur.fetchone()[0]:
                    raise ProjectionStoreException(
                        "Could not acquire both legacy baseline controller locks"
                    )

            cur.execute(self._controller_query(for_update=True), controller_args)
            existing_live = cur.fetchone()
            if existing_live is not None:
                existing = ControllerStateRow(*existing_live)
                if (
                    existing.checkpoint_seq == checkpoint_seq
                    and existing.source_high_watermark == checkpoint_seq
                    and existing.backlog_count == 0
                    and existing.deployment_sha == deployment_sha
                    and existing.mode == "recovery"
                    and existing.status == "repair_only"
                    and not existing.accepted_live
                    and existing.unresolved_quarantine_count == 0
                ):
                    return existing
                raise ProjectionStoreException(
                    "Live controller already exists and does not match the accepted legacy baseline"
                )

            cur.execute(self._controller_query(for_update=True), migration_args)
            migration_row = cur.fetchone()
            if migration_row is None:
                raise ProjectionStoreException(
                    "Legacy baseline migration controller is not durable"
                )
            migration = ControllerStateRow(*migration_row)
            if migration.accepted_live or migration.mode != "backfill":
                raise ProjectionStoreException(
                    "Legacy baseline migration controller has an unsafe mode or live admission"
                )

            observed = {
                table: scoped_count(cur, table)
                for table in ("event_receipts", "journeys", "loop_runs")
            }
            if observed != expected:
                raise ProjectionStoreException(
                    f"Legacy baseline count mismatch: expected {expected}, observed {observed}"
                )

            cur.execute(
                f"""
                SELECT COALESCE(MAX(source_ingested_seq), 0)
                FROM {self.schema}.journey_stages
                WHERE (%s IN ('', '*') OR tenant_id=%s)
                  AND (%s IN ('', '*') OR environment=%s)
                """,
                (tenant_scope, tenant_scope, environment_scope, environment_scope),
            )
            maximum_stage_offset = int(cur.fetchone()[0])
            if maximum_stage_offset > checkpoint_seq:
                raise ProjectionStoreException(
                    "Legacy baseline stage offset exceeds the accepted checkpoint"
                )

            cur.execute(
                f"""
                SELECT COUNT(*) FROM {self.schema}.quarantine
                WHERE resolution_status='unresolved'
                  AND (%s IN ('', '*') OR tenant_id=%s)
                  AND (%s IN ('', '*') OR environment=%s)
                """,
                (tenant_scope, tenant_scope, environment_scope, environment_scope),
            )
            if int(cur.fetchone()[0]) != 0:
                raise ProjectionStoreException(
                    "Legacy baseline has unresolved quarantine; refusing live cursor seed"
                )

            now = datetime.now(timezone.utc)
            cur.execute(
                f"""
                UPDATE {self.schema}.controller
                SET checkpoint_seq=%s,
                    source_high_watermark=%s,
                    backlog_count=0,
                    deployment_sha=%s,
                    mode='backfill',
                    status='ready',
                    accepted_live=FALSE,
                    last_backfill_at=%s,
                    last_error_message='',
                    unresolved_quarantine_count=0,
                    updated_at=%s
                WHERE controller_id=%s AND tenant_scope=%s AND environment_scope=%s
                """,
                (
                    checkpoint_seq,
                    checkpoint_seq,
                    deployment_sha,
                    now,
                    now,
                    *migration_args,
                ),
            )
            cur.execute(
                f"""
                INSERT INTO {self.schema}.controller (
                    controller_id, tenant_scope, environment_scope,
                    checkpoint_seq, source_high_watermark, backlog_count,
                    projection_revision, deployment_sha, mode, status,
                    accepted_live, last_poll_at, last_success_at,
                    last_recovery_at, last_error_message,
                    unresolved_quarantine_count, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 0, %s, %s,
                    'recovery', 'repair_only', FALSE, %s, %s, %s, '', 0, %s
                )
                RETURNING {CONTROLLER_COLUMNS}
                """,
                (
                    controller_id,
                    tenant_scope,
                    environment_scope,
                    checkpoint_seq,
                    checkpoint_seq,
                    migration.projection_revision,
                    deployment_sha,
                    now,
                    now,
                    now,
                    now,
                ),
            )
            return ControllerStateRow(*cur.fetchone())

    def resolve_identity(
        self, tenant_id: str, environment: str, identifier_type: str, identifier_value: str
    ) -> Optional[str]:
        """Resolves an identity link to a journey_id."""
        sql = f"""
        SELECT journey_id FROM {self.schema}.identity_links
        WHERE tenant_id=%s AND environment=%s AND identifier_type=%s AND identifier_value=%s
        """
        with self._connect_db() as conn, conn.cursor() as cur:
            cur.execute(sql, (tenant_id, environment, identifier_type, identifier_value))
            row = cur.fetchone()
            return row[0] if row else None

    def get_receipt(self, event_id: str) -> Optional[EventReceiptRow]:
        """Gets an event receipt by event_id."""
        return self.get_receipts((event_id,)).get(event_id)

    def get_receipts(self, event_ids: list[str] | tuple[str, ...]) -> dict[str, EventReceiptRow]:
        """Load a batch's existing receipts with one indexed query.

        The relational projector performs this preflight before it starts
        reduction.  Keeping it set-based is important: a 500-row source poll
        must not turn into 500 short-lived database connections just to learn
        that all of its event IDs are new.
        """

        requested = tuple(sorted({str(event_id) for event_id in event_ids if event_id}))
        if not requested:
            return {}
        sql = f"""
        SELECT event_id, ingested_seq, fingerprint, tenant_id, environment, journey_id, loop_run_id,
               source_event_type, created_at, disposition, projection_revision, projected_at
        FROM {self.schema}.event_receipts
        WHERE event_id = ANY(%s)
        ORDER BY event_id
        """
        with self._connect_db() as conn, conn.cursor() as cur:
            cur.execute(sql, (list(requested),))
            return {str(row[0]): EventReceiptRow(*row) for row in cur.fetchall()}

    def load_journey_stage_events(
        self, tenant_id: str, environment: str, journey_id: str
    ) -> list[dict[str, Any]]:
        """Load one aggregate's bounded stage contract slice for reduction.

        This is intentionally a per-journey lookup, never a whole-projection
        snapshot.  The relational worker uses it only to hydrate an aggregate
        touched by its current source batch before it derives that aggregate's
        next summary.
        """

        key = (tenant_id, environment, journey_id)
        return self.load_journey_stage_events_bulk((key,))[key]

    @staticmethod
    def _decode_stage_contract_fields(value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise ProjectionStoreException(
                    "stored journey stage contract_fields is not valid JSON"
                ) from exc
        if not isinstance(value, Mapping):
            raise ProjectionStoreException(
                "stored journey stage contract_fields is not an object"
            )
        return dict(value)

    def load_journey_stage_events_bulk(
        self, keys: list[tuple[str, str, str]] | tuple[tuple[str, str, str], ...],
    ) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
        """Hydrate every aggregate touched by one batch with one bounded read.

        The request CTE keeps the lookup bounded to the current batch's
        aggregate keys while avoiding one connection/query per journey.  A
        ``LEFT JOIN`` intentionally retains keys with no durable stages so
        callers can distinguish an empty new aggregate from a missing result.
        """

        requested = tuple(sorted(set(keys)))
        result: dict[tuple[str, str, str], list[dict[str, Any]]] = {
            key: [] for key in requested
        }
        if not requested:
            return result
        tenant_ids, environments, journey_ids = zip(*requested)
        sql = f"""
        WITH requested(tenant_id, environment, journey_id) AS (
            SELECT * FROM unnest(%s::text[], %s::text[], %s::text[])
        )
        SELECT requested.tenant_id, requested.environment, requested.journey_id,
               stage.contract_fields
        FROM requested
        LEFT JOIN {self.schema}.journey_stages AS stage
          ON stage.tenant_id = requested.tenant_id
         AND stage.environment = requested.environment
         AND stage.journey_id = requested.journey_id
        ORDER BY requested.tenant_id, requested.environment, requested.journey_id,
                 stage.event_sequence, stage.stage_ordinal, stage.occurred_at,
                 stage.source_ingested_seq, stage.source_event_id
        """
        with self._connect_db() as conn, conn.cursor() as cur:
            cur.execute(sql, (list(tenant_ids), list(environments), list(journey_ids)))
            rows = cur.fetchall()
        for row in rows:
            if isinstance(row, tuple):
                tenant_id, environment, journey_id, value = row
            else:
                tenant_id = row["tenant_id"]
                environment = row["environment"]
                journey_id = row["journey_id"]
                value = row["contract_fields"]
            if value is None:
                continue
            key = (str(tenant_id), str(environment), str(journey_id))
            result[key].append(self._decode_stage_contract_fields(value))
        return result

    def execute_batch_transaction(
        self,
        controller_id: str,
        tenant_scope: str,
        environment_scope: str,
        mutation: BatchProjectionMutation,
    ) -> ControllerStateRow:
        """
        Executes a single atomic batch transaction:
        1. Takes non-blocking Postgres advisory lock for controller (fails fast if locked).
        2. Locks controller row FOR UPDATE (creates default row if missing).
        3. Atomically claims event receipts & detects conflicts / exact duplicates.
        4. Upserts identity links & checks for identity conflicts.
        5. Upserts journey stages, journeys, loop runs, receipts, quarantines.
        6. Advances controller revision and contiguous checkpoint atomically.
        """
        has_derived_mutations = any(
            (
                mutation.identity_links,
                mutation.journeys,
                mutation.stages,
                mutation.loop_runs,
                mutation.quarantines,
            )
        )
        if has_derived_mutations and not mutation.receipts:
            raise ProjectionStoreException(
                "Projection row mutations require at least one durable event receipt"
            )

        mode = mutation.mode.lower()
        if mode not in {"live", "backfill", "recovery", "replay"}:
            raise ProjectionStoreException(f"Unsupported projection mode: {mutation.mode}")
        if mutation.source_high_watermark < 0 or mutation.backlog_count < 0:
            raise ProjectionStoreException(
                "Controller high watermark and backlog must be non-negative"
            )

        def validate_scope(kind: str, tenant_id: str, environment: str) -> None:
            if tenant_scope not in {"", "*"} and tenant_id not in {"", tenant_scope}:
                raise ProjectionStoreException(
                    f"{kind} tenant {tenant_id!r} is outside controller scope {tenant_scope!r}"
                )
            if (
                environment_scope not in {"", "*"}
                and environment not in {"", environment_scope}
            ):
                raise ProjectionStoreException(
                    f"{kind} environment {environment!r} is outside controller scope {environment_scope!r}"
                )

        for receipt in mutation.receipts:
            validate_scope("receipt", receipt.tenant_id, receipt.environment)
        for link in mutation.identity_links:
            validate_scope("identity link", link.tenant_id, link.environment)
        for journey in mutation.journeys:
            validate_scope("journey", journey.tenant_id, journey.environment)
        for stage in mutation.stages:
            validate_scope("stage", stage.tenant_id, stage.environment)
        for loop_run in mutation.loop_runs:
            validate_scope("loop run", loop_run.tenant_id, loop_run.environment)
        for quarantine in mutation.quarantines:
            validate_scope("quarantine", quarantine.tenant_id, quarantine.environment)

        receipt_keys = {
            (receipt.event_id, receipt.ingested_seq) for receipt in mutation.receipts
        }
        receipt_event_ids = {event_id for event_id, _ in receipt_keys}
        receipt_journey_keys = {
            (receipt.tenant_id, receipt.environment, receipt.journey_id)
            for receipt in mutation.receipts
            if receipt.journey_id
        }
        receipt_loop_keys = {
            (receipt.tenant_id, receipt.environment, receipt.loop_run_id)
            for receipt in mutation.receipts
            if receipt.loop_run_id
        }
        if any(
            (link.tenant_id, link.environment, link.journey_id)
            not in receipt_journey_keys
            for link in mutation.identity_links
        ):
            raise ProjectionStoreException(
                "Every identity link mutation must be owned by a journey receipt in the same batch"
            )
        if any(
            (journey.tenant_id, journey.environment, journey.journey_id)
            not in receipt_journey_keys
            for journey in mutation.journeys
        ):
            raise ProjectionStoreException(
                "Every journey mutation must be owned by a journey receipt in the same batch"
            )
        if any(stage.source_event_id not in receipt_event_ids for stage in mutation.stages):
            raise ProjectionStoreException(
                "Every stage mutation must reference an event receipt in the same batch"
            )
        if any(
            (loop_run.tenant_id, loop_run.environment, loop_run.loop_run_id)
            not in receipt_loop_keys
            and (
                not loop_run.journey_id
                or (loop_run.tenant_id, loop_run.environment, loop_run.journey_id)
                not in receipt_journey_keys
            )
            for loop_run in mutation.loop_runs
        ):
            raise ProjectionStoreException(
                "Every loop run mutation must be owned by a loop or journey receipt in the same batch"
            )
        if any(
            (quarantine.event_id, quarantine.ingested_seq) not in receipt_keys
            for quarantine in mutation.quarantines
        ):
            raise ProjectionStoreException(
                "Every quarantine mutation must reference its event receipt in the same batch"
            )

        effective_accepted_live = mode == "live" and mutation.accepted_live
        lock_id = controller_advisory_lock_id(
            controller_id, tenant_scope, environment_scope
        )

        with self._connect_db() as conn:
            with conn.cursor() as cur:
                # 1. Non-blocking advisory lock
                cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (lock_id,))
                lock_acquired = cur.fetchone()[0]
                if not lock_acquired:
                    raise ProjectionStoreException(
                        f"Could not acquire advisory lock for controller {controller_id} ({tenant_scope}/{environment_scope})"
                    )

                # 2. Lock controller row FOR UPDATE
                cur.execute(
                    self._controller_query(for_update=True),
                    (controller_id, tenant_scope, environment_scope),
                )
                ctrl_row = cur.fetchone()
                if ctrl_row is None:
                    cur.execute(
                        f"""
                        INSERT INTO {self.schema}.controller (
                            controller_id, tenant_scope, environment_scope, checkpoint_seq,
                            source_high_watermark, backlog_count, projection_revision, deployment_sha,
                            mode, status, accepted_live
                        ) VALUES (%s, %s, %s, 0, 0, 0, 0, %s, %s, %s, %s)
                        """,
                        (
                            controller_id,
                            tenant_scope,
                            environment_scope,
                            mutation.deployment_sha,
                            mutation.mode,
                            mutation.status,
                            effective_accepted_live,
                        ),
                    )
                    curr_checkpoint_seq = 0
                    curr_revision = 0
                    curr_source_high_watermark = 0
                else:
                    curr_checkpoint_seq = ctrl_row[3]
                    curr_revision = ctrl_row[6]
                    curr_source_high_watermark = ctrl_row[4]

                now = datetime.now(timezone.utc)

                # 3. Claim receipts before any derived mutation. The initial read
                # distinguishes an already-durable exact retry from a concurrent
                # transaction that wins the global event_id claim after our read.
                # The latter fails this whole transaction rather than letting two
                # controller locks commit derived rows for one receipt.
                new_receipts: list[EventReceiptRow] = []
                exact_duplicate_receipts: list[EventReceiptRow] = []
                unique_receipts: dict[str, EventReceiptRow] = {}
                for receipt in mutation.receipts:
                    prior_receipt = unique_receipts.get(receipt.event_id)
                    if prior_receipt is not None:
                        if prior_receipt.fingerprint != receipt.fingerprint:
                            raise ConflictingDuplicateException(
                                f"Event {receipt.event_id} has conflicting fingerprints within one batch"
                            )
                        continue
                    unique_receipts[receipt.event_id] = receipt

                claimed_revision = curr_revision + 1
                ordered_receipts = tuple(
                    sorted(unique_receipts.values(), key=lambda item: item.event_id)
                )
                event_ids = [receipt.event_id for receipt in ordered_receipts]
                # Set-based preflight preserves the old distinction between a
                # durable exact retry and an input fingerprint conflict, while
                # avoiding one round trip for every event in a 500-row poll.
                cur.execute(
                    f"SELECT event_id, fingerprint FROM {self.schema}.event_receipts "
                    "WHERE event_id = ANY(%s)",
                    (event_ids,),
                )
                existing_fingerprints = {
                    str(event_id): str(fingerprint)
                    for event_id, fingerprint in cur.fetchall()
                }
                receipts_to_claim: list[EventReceiptRow] = []
                for receipt in ordered_receipts:
                    existing_fp = existing_fingerprints.get(receipt.event_id)
                    if existing_fp is None:
                        receipts_to_claim.append(receipt)
                        continue
                    if existing_fp != receipt.fingerprint:
                        raise ConflictingDuplicateException(
                            f"Event {receipt.event_id} reused with conflicting fingerprint "
                            f"{receipt.fingerprint} vs {existing_fp}"
                        )
                    exact_duplicate_receipts.append(receipt)

                if receipts_to_claim:
                    values_sql = ", ".join(
                        "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                        for _ in receipts_to_claim
                    )
                    insert_params: list[Any] = []
                    for receipt in receipts_to_claim:
                        insert_params.extend(
                            (
                                receipt.event_id,
                                receipt.ingested_seq,
                                receipt.fingerprint,
                                receipt.tenant_id,
                                receipt.environment,
                                receipt.journey_id,
                                receipt.loop_run_id,
                                receipt.source_event_type,
                                receipt.created_at,
                                receipt.disposition,
                                claimed_revision,
                                now,
                            )
                        )
                    cur.execute(
                        f"""
                        INSERT INTO {self.schema}.event_receipts (
                            event_id, ingested_seq, fingerprint, tenant_id, environment,
                            journey_id, loop_run_id, source_event_type, created_at, disposition,
                            projection_revision, projected_at
                        ) VALUES {values_sql}
                        ON CONFLICT DO NOTHING
                        RETURNING event_id, fingerprint
                        """,
                        tuple(insert_params),
                    )
                    claimed_fingerprints = {
                        str(event_id): str(fingerprint)
                        for event_id, fingerprint in cur.fetchall()
                    }
                    lost_claims = [
                        receipt
                        for receipt in receipts_to_claim
                        if receipt.event_id not in claimed_fingerprints
                    ]
                    new_receipts.extend(
                        receipt
                        for receipt in receipts_to_claim
                        if claimed_fingerprints.get(receipt.event_id) == receipt.fingerprint
                    )
                    if lost_claims:
                        cur.execute(
                            f"SELECT event_id, fingerprint FROM {self.schema}.event_receipts "
                            "WHERE event_id = ANY(%s)",
                            ([receipt.event_id for receipt in lost_claims],),
                        )
                        concurrent_fingerprints = {
                            str(event_id): str(fingerprint)
                            for event_id, fingerprint in cur.fetchall()
                        }
                        receipt = lost_claims[0]
                        existing_fp = concurrent_fingerprints.get(receipt.event_id)
                        if existing_fp != receipt.fingerprint:
                            raise ConflictingDuplicateException(
                                f"Event {receipt.event_id} lost its receipt claim with fingerprint "
                                f"{receipt.fingerprint} vs {existing_fp or '<missing>'}"
                            )
                        raise ConcurrentReceiptClaimException(
                            f"Event {receipt.event_id} was claimed concurrently by another projection transaction"
                        )

                if mutation.receipts and not new_receipts:
                    # Every event is already durable with the same fingerprint. Ignore
                    # all caller-supplied derived mutations so retries cannot rewrite
                    # stages or aggregates with non-canonical retry payloads.
                    cur.execute(
                        self._controller_query(),
                        (controller_id, tenant_scope, environment_scope),
                    )
                    return ControllerStateRow(*cur.fetchone())

                next_revision = claimed_revision if new_receipts else curr_revision
                new_event_ids = {receipt.event_id for receipt in new_receipts}
                new_receipt_keys = {
                    (receipt.event_id, receipt.ingested_seq)
                    for receipt in new_receipts
                }
                new_journey_keys = {
                    (receipt.tenant_id, receipt.environment, receipt.journey_id)
                    for receipt in new_receipts
                    if receipt.journey_id
                }
                duplicate_journey_keys = {
                    (receipt.tenant_id, receipt.environment, receipt.journey_id)
                    for receipt in exact_duplicate_receipts
                    if receipt.journey_id
                }
                new_loop_keys = {
                    (receipt.tenant_id, receipt.environment, receipt.loop_run_id)
                    for receipt in new_receipts
                    if receipt.loop_run_id
                }
                duplicate_loop_keys = {
                    (receipt.tenant_id, receipt.environment, receipt.loop_run_id)
                    for receipt in exact_duplicate_receipts
                    if receipt.loop_run_id
                }

                ambiguous_journey_keys = new_journey_keys & duplicate_journey_keys
                if any(
                    (row.tenant_id, row.environment, row.journey_id)
                    in ambiguous_journey_keys
                    for row in (*mutation.identity_links, *mutation.journeys)
                ):
                    raise ProjectionStoreException(
                        "Mixed duplicate/new batch has ambiguous journey-derived mutations; retry new receipts separately"
                    )
                ambiguous_loop_keys = new_loop_keys & duplicate_loop_keys
                if any(
                    (row.tenant_id, row.environment, row.loop_run_id)
                    in ambiguous_loop_keys
                    for row in mutation.loop_runs
                ):
                    raise ProjectionStoreException(
                        "Mixed duplicate/new batch has ambiguous loop-derived mutations; retry new receipts separately"
                    )

                effective_identity_links = [
                    link
                    for link in mutation.identity_links
                    if (link.tenant_id, link.environment, link.journey_id)
                    in new_journey_keys
                    and link.identifier_type not in SHARED_IDENTIFIER_TYPES
                ]
                effective_journeys = [
                    journey
                    for journey in mutation.journeys
                    if (journey.tenant_id, journey.environment, journey.journey_id)
                    in new_journey_keys
                ]
                effective_stages = [
                    stage
                    for stage in mutation.stages
                    if stage.source_event_id in new_event_ids
                ]
                effective_loop_runs = [
                    loop_run
                    for loop_run in mutation.loop_runs
                    if (
                        loop_run.tenant_id,
                        loop_run.environment,
                        loop_run.loop_run_id,
                    )
                    in new_loop_keys
                    or (
                        bool(loop_run.journey_id)
                        and (
                            loop_run.tenant_id,
                            loop_run.environment,
                            loop_run.journey_id,
                        )
                        in new_journey_keys
                    )
                ]
                effective_quarantines = [
                    quarantine
                    for quarantine in mutation.quarantines
                    if (quarantine.event_id, quarantine.ingested_seq)
                    in new_receipt_keys
                ]

                # 4. Process identity links & check for identity conflicts
                for link in effective_identity_links:
                    cur.execute(
                        f"""
                        SELECT journey_id FROM {self.schema}.identity_links
                        WHERE tenant_id=%s AND environment=%s AND identifier_type=%s AND identifier_value=%s
                        """,
                        (link.tenant_id, link.environment, link.identifier_type, link.identifier_value),
                    )
                    existing_link = cur.fetchone()
                    if existing_link is not None and existing_link[0] != link.journey_id:
                        raise IdentityConflictException(
                            f"Identity link ({link.identifier_type}={link.identifier_value}) already bound to journey {existing_link[0]}, cannot rebind to {link.journey_id}"
                        )

                    cur.execute(
                        f"""
                        INSERT INTO {self.schema}.identity_links (
                            tenant_id, environment, identifier_type, identifier_value, journey_id,
                            first_ingested_seq, last_ingested_seq, first_occurred_at,
                            last_occurred_at, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, environment, identifier_type, identifier_value)
                        DO UPDATE SET
                            first_ingested_seq = LEAST({self.schema}.identity_links.first_ingested_seq, EXCLUDED.first_ingested_seq),
                            last_ingested_seq = GREATEST({self.schema}.identity_links.last_ingested_seq, EXCLUDED.last_ingested_seq),
                            first_occurred_at = LEAST({self.schema}.identity_links.first_occurred_at, EXCLUDED.first_occurred_at),
                            last_occurred_at = GREATEST({self.schema}.identity_links.last_occurred_at, EXCLUDED.last_occurred_at),
                            updated_at = EXCLUDED.updated_at
                        """,
                        (
                            link.tenant_id,
                            link.environment,
                            link.identifier_type,
                            link.identifier_value,
                            link.journey_id,
                            link.first_ingested_seq,
                            link.last_ingested_seq,
                            link.first_occurred_at,
                            link.last_occurred_at,
                            now,
                            now,
                        ),
                    )

                # 5. Insert journey stages idempotently
                for stage in effective_stages:
                    cur.execute(
                        f"""
                        INSERT INTO {self.schema}.journey_stages (
                            tenant_id, environment, journey_id, source_event_id, stage_name,
                            stage_status, stage_ordinal, source_ingested_seq, event_sequence,
                            occurred_at, recorded_at, contract_fields, evidence_references,
                            projection_revision, fingerprint
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                        ON CONFLICT (tenant_id, environment, journey_id, source_event_id, stage_name)
                        DO NOTHING
                        """,
                        (
                            stage.tenant_id,
                            stage.environment,
                            stage.journey_id,
                            stage.source_event_id,
                            stage.stage_name,
                            stage.stage_status,
                            stage.stage_ordinal,
                            stage.source_ingested_seq,
                            stage.event_sequence,
                            stage.occurred_at,
                            now,
                            json.dumps(stage.contract_fields, sort_keys=True),
                            json.dumps(stage.evidence_references, sort_keys=True),
                            next_revision,
                            stage.fingerprint,
                        ),
                    )
                    if cur.rowcount == 0:
                        cur.execute(
                            f"""
                            SELECT fingerprint
                            FROM {self.schema}.journey_stages
                            WHERE tenant_id=%s AND environment=%s AND journey_id=%s
                              AND source_event_id=%s AND stage_name=%s
                            """,
                            (
                                stage.tenant_id,
                                stage.environment,
                                stage.journey_id,
                                stage.source_event_id,
                                stage.stage_name,
                            ),
                        )
                        existing_stage = cur.fetchone()
                        if existing_stage is None or existing_stage[0] != stage.fingerprint:
                            raise ConflictingDuplicateException(
                                f"Stage {stage.source_event_id}/{stage.stage_name} reused with a conflicting fingerprint"
                            )

                # 6. Upsert Journeys
                for journey in effective_journeys:
                    cur.execute(
                        f"""
                        INSERT INTO {self.schema}.journeys (
                            tenant_id, environment, journey_id, status, stage_coverage,
                            is_terminal, first_occurred_at, last_occurred_at, first_ingested_seq,
                            last_ingested_seq, current_identity_summary, evidence_summary,
                            diagnostic_summary, loop_run_id, projection_revision, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, environment, journey_id)
                        DO UPDATE SET
                            status = EXCLUDED.status,
                            stage_coverage = EXCLUDED.stage_coverage,
                            is_terminal = EXCLUDED.is_terminal,
                            first_occurred_at = LEAST({self.schema}.journeys.first_occurred_at, EXCLUDED.first_occurred_at),
                            last_occurred_at = GREATEST({self.schema}.journeys.last_occurred_at, EXCLUDED.last_occurred_at),
                            first_ingested_seq = LEAST({self.schema}.journeys.first_ingested_seq, EXCLUDED.first_ingested_seq),
                            last_ingested_seq = GREATEST({self.schema}.journeys.last_ingested_seq, EXCLUDED.last_ingested_seq),
                            current_identity_summary = EXCLUDED.current_identity_summary,
                            evidence_summary = EXCLUDED.evidence_summary,
                            diagnostic_summary = EXCLUDED.diagnostic_summary,
                            loop_run_id = CASE WHEN EXCLUDED.loop_run_id != '' THEN EXCLUDED.loop_run_id ELSE {self.schema}.journeys.loop_run_id END,
                            projection_revision = EXCLUDED.projection_revision,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (
                            journey.tenant_id,
                            journey.environment,
                            journey.journey_id,
                            journey.status,
                            json.dumps(journey.stage_coverage, sort_keys=True),
                            journey.is_terminal,
                            journey.first_occurred_at,
                            journey.last_occurred_at,
                            journey.first_ingested_seq,
                            journey.last_ingested_seq,
                            json.dumps(journey.current_identity_summary, sort_keys=True),
                            json.dumps(journey.evidence_summary, sort_keys=True),
                            json.dumps(journey.diagnostic_summary, sort_keys=True),
                            journey.loop_run_id,
                            next_revision,
                            now,
                            now,
                        ),
                    )

                # 7. Upsert Loop Runs
                for loop_run in effective_loop_runs:
                    cur.execute(
                        f"""
                        INSERT INTO {self.schema}.loop_runs (
                            tenant_id, environment, loop_run_id, journey_id, status,
                            lifecycle_summary, freshness_lineage, contract_payload,
                            projection_revision, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)
                        ON CONFLICT (tenant_id, environment, loop_run_id)
                        DO UPDATE SET
                            journey_id = CASE WHEN EXCLUDED.journey_id != '' THEN EXCLUDED.journey_id ELSE {self.schema}.loop_runs.journey_id END,
                            status = EXCLUDED.status,
                            lifecycle_summary = EXCLUDED.lifecycle_summary,
                            freshness_lineage = EXCLUDED.freshness_lineage,
                            contract_payload = EXCLUDED.contract_payload,
                            projection_revision = EXCLUDED.projection_revision,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (
                            loop_run.tenant_id,
                            loop_run.environment,
                            loop_run.loop_run_id,
                            loop_run.journey_id,
                            loop_run.status,
                            json.dumps(loop_run.lifecycle_summary, sort_keys=True),
                            json.dumps(loop_run.freshness_lineage, sort_keys=True),
                            json.dumps(loop_run.contract_payload, sort_keys=True),
                            next_revision,
                            now,
                            now,
                        ),
                    )

                # 8. Upsert Quarantine records
                for q in effective_quarantines:
                    cur.execute(
                        f"""
                        INSERT INTO {self.schema}.quarantine (
                            event_id, ingested_seq, reason_code, reason_detail, source_event_type,
                            tenant_id, environment, journey_id, fingerprint, first_seen_at, last_seen_at,
                            occurrence_count, resolution_status, resolution_audit_ref
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (event_id, ingested_seq)
                        DO UPDATE SET
                            occurrence_count = {self.schema}.quarantine.occurrence_count + 1,
                            last_seen_at = EXCLUDED.last_seen_at
                        """,
                        (
                            q.event_id,
                            q.ingested_seq,
                            q.reason_code,
                            q.reason_detail,
                            q.source_event_type,
                            q.tenant_id,
                            q.environment,
                            q.journey_id,
                            q.fingerprint,
                            now,
                            now,
                            q.occurrence_count,
                            q.resolution_status,
                            q.resolution_audit_ref,
                        ),
                    )

                # Derive the checkpoint from durable database truth. The recursive
                # index lookups also cross a previously persisted gap tail when the
                # missing receipt arrives in a later transaction.
                start_seq = (
                    curr_checkpoint_seq
                    if curr_checkpoint_seq > 0
                    else (
                        min(receipt.ingested_seq for receipt in mutation.receipts) - 1
                        if mutation.receipts
                        else 0
                    )
                )
                cur.execute(
                    f"""
                    WITH RECURSIVE contiguous(seq) AS (
                        SELECT %s::bigint
                        UNION ALL
                        SELECT contiguous.seq + 1
                        FROM contiguous
                        WHERE EXISTS (
                            SELECT 1
                            FROM {self.schema}.event_receipts AS receipt
                            WHERE receipt.ingested_seq = contiguous.seq + 1
                              AND (%s IN ('', '*') OR receipt.tenant_id=%s)
                              AND (%s IN ('', '*') OR receipt.environment=%s)
                        )
                    )
                    SELECT MAX(seq) FROM contiguous
                    """,
                    (
                        start_seq,
                        tenant_scope,
                        tenant_scope,
                        environment_scope,
                        environment_scope,
                    ),
                )
                target_checkpoint_seq = int(cur.fetchone()[0] or 0)
                proven_frontier = int(mutation.source_frontier_seq or 0)
                if proven_frontier < 0 or proven_frontier > int(mutation.source_high_watermark):
                    raise ProjectionStoreException(
                        "Source frontier must be within the observed source high watermark"
                    )
                # The source adapter sets this only after taking the same
                # transaction advisory lock as canonical writers and fetching
                # every row through the returned sequence. Missing positions in
                # that committed snapshot are sequence holes, not missing receipts.
                if proven_frontier >= curr_checkpoint_seq:
                    target_checkpoint_seq = max(target_checkpoint_seq, proven_frontier)
                next_source_high_watermark = max(
                    int(curr_source_high_watermark),
                    int(mutation.source_high_watermark),
                    int(target_checkpoint_seq or 0),
                )
                effective_backlog_count = max(
                    0, next_source_high_watermark - int(target_checkpoint_seq or 0)
                )

                # Compute controller-scoped unresolved quarantine truth.
                cur.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {self.schema}.quarantine
                    WHERE resolution_status='unresolved'
                      AND (%s IN ('', '*') OR tenant_id=%s)
                      AND (%s IN ('', '*') OR environment=%s)
                    """,
                    (
                        tenant_scope,
                        tenant_scope,
                        environment_scope,
                        environment_scope,
                    ),
                )
                actual_unresolved_quarantine_count = cur.fetchone()[0]

                # Mode-dependent timestamp updates
                update_last_live = effective_accepted_live

                last_backfill_ts = now if mode == "backfill" else None
                last_recovery_ts = now if mode == "recovery" else None
                last_replay_ts = now if mode == "replay" else None
                last_failure_ts = (
                    now
                    if mutation.error_message or mutation.status.lower() == "failed"
                    else None
                )

                cur.execute(
                    f"""
                    UPDATE {self.schema}.controller
                    SET checkpoint_seq = %s,
                        source_high_watermark = GREATEST(source_high_watermark, %s, %s),
                        backlog_count = %s,
                        projection_revision = %s,
                        deployment_sha = %s,
                        mode = %s,
                        status = %s,
                        accepted_live = %s,
                        last_poll_at = %s,
                        last_success_at = %s,
                        last_live_success_at = CASE WHEN %s THEN %s ELSE last_live_success_at END,
                        last_backfill_at = CASE WHEN %s::timestamptz IS NOT NULL THEN %s::timestamptz ELSE last_backfill_at END,
                        last_recovery_at = CASE WHEN %s::timestamptz IS NOT NULL THEN %s::timestamptz ELSE last_recovery_at END,
                        last_replay_at = CASE WHEN %s::timestamptz IS NOT NULL THEN %s::timestamptz ELSE last_replay_at END,
                        last_failure_at = CASE WHEN %s::timestamptz IS NOT NULL THEN %s::timestamptz ELSE last_failure_at END,
                        last_error_message = %s,
                        unresolved_quarantine_count = %s,
                        updated_at = %s
                    WHERE controller_id=%s AND tenant_scope=%s AND environment_scope=%s
                    RETURNING {CONTROLLER_COLUMNS}
                    """,
                    (
                        target_checkpoint_seq,
                        next_source_high_watermark,
                        target_checkpoint_seq,
                        effective_backlog_count,
                        next_revision,
                        mutation.deployment_sha,
                        mutation.mode,
                        mutation.status,
                        effective_accepted_live,
                        now,
                        now,
                        update_last_live,
                        now,
                        last_backfill_ts,
                        last_backfill_ts,
                        last_recovery_ts,
                        last_recovery_ts,
                        last_replay_ts,
                        last_replay_ts,
                        last_failure_ts,
                        last_failure_ts,
                        mutation.error_message,
                        actual_unresolved_quarantine_count,
                        now,
                        controller_id,
                        tenant_scope,
                        environment_scope,
                    ),
                )
                updated_ctrl = cur.fetchone()
                return ControllerStateRow(*updated_ctrl)
