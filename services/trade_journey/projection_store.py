"""
PostgreSQL Relational Projection Store for Trade Journey.

LIFECYCLE-PROJ-STORE-001: Provides additive relational schema management,
typed persistence interfaces, advisory locking, and atomic batch projection transactions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

# Advisory lock ID for Trade Journey Projector Controller
PROJECTION_CONTROLLER_ADVISORY_LOCK_ID = 918273645


class ProjectionStoreException(Exception):
    """Base exception for projection store operations."""


class IdentityConflictException(ProjectionStoreException):
    """Raised when an identity link conflicts with an existing different journey."""


class ConflictingDuplicateException(ProjectionStoreException):
    """Raised when an event ID is reused with a different canonical fingerprint."""


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
        schema: str = "trade_journey_projection",
        connect: Optional[Callable[..., Any]] = None,
        bootstrap: bool = False,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for ProjectionStore")
        if not schema.replace("_", "").isalnum():
            raise ValueError("Invalid schema name for ProjectionStore")
        self.dsn = dsn
        self.schema = schema
        if connect is None:
            import psycopg  # type: ignore[import]
            connect = psycopg.connect
        self._connect = connect
        if bootstrap:
            self.bootstrap_schema()

    def bootstrap_schema(self) -> None:
        """Applies initial schema and tables idempotently."""
        sql = f"""
        CREATE SCHEMA IF NOT EXISTS {self.schema};

        CREATE TABLE IF NOT EXISTS {self.schema}.controller (
            controller_id TEXT NOT NULL,
            tenant_scope TEXT NOT NULL,
            environment_scope TEXT NOT NULL,
            checkpoint_seq BIGINT NOT NULL DEFAULT 0,
            source_high_watermark BIGINT NOT NULL DEFAULT 0,
            backlog_count BIGINT NOT NULL DEFAULT 0,
            projection_revision BIGINT NOT NULL DEFAULT 0,
            deployment_sha TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'live',
            status TEXT NOT NULL DEFAULT 'ok',
            accepted_live BOOLEAN NOT NULL DEFAULT FALSE,
            last_poll_at TIMESTAMPTZ,
            last_success_at TIMESTAMPTZ,
            last_live_success_at TIMESTAMPTZ,
            last_recovery_at TIMESTAMPTZ,
            last_backfill_at TIMESTAMPTZ,
            last_replay_at TIMESTAMPTZ,
            last_failure_at TIMESTAMPTZ,
            last_error_message TEXT NOT NULL DEFAULT '',
            unresolved_quarantine_count BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (controller_id, tenant_scope, environment_scope)
        );

        CREATE TABLE IF NOT EXISTS {self.schema}.event_receipts (
            event_id TEXT PRIMARY KEY,
            ingested_seq BIGINT UNIQUE NOT NULL,
            fingerprint TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            environment TEXT NOT NULL,
            journey_id TEXT NOT NULL DEFAULT '',
            loop_run_id TEXT NOT NULL DEFAULT '',
            source_event_type TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            disposition TEXT NOT NULL CHECK (disposition IN ('applied', 'duplicate', 'ignored', 'quarantined')),
            projection_revision BIGINT NOT NULL DEFAULT 0,
            projected_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );

        CREATE INDEX IF NOT EXISTS idx_event_receipts_ingested_seq
            ON {self.schema}.event_receipts (ingested_seq);

        CREATE INDEX IF NOT EXISTS idx_event_receipts_tenant_env_journey
            ON {self.schema}.event_receipts (tenant_id, environment, journey_id)
            WHERE journey_id != '';

        CREATE TABLE IF NOT EXISTS {self.schema}.identity_links (
            tenant_id TEXT NOT NULL,
            environment TEXT NOT NULL,
            identifier_type TEXT NOT NULL CHECK (identifier_type IN (
                'journey_id', 'trade_id', 'order_id', 'fill_id', 'position_id',
                'signal_id', 'proposal_id', 'allocation_id', 'intent_id', 'loop_run_id'
            )),
            identifier_value TEXT NOT NULL,
            journey_id TEXT NOT NULL,
            first_ingested_seq BIGINT NOT NULL,
            last_ingested_seq BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant_id, environment, identifier_type, identifier_value)
        );

        CREATE INDEX IF NOT EXISTS idx_identity_links_tenant_env_journey
            ON {self.schema}.identity_links (tenant_id, environment, journey_id);

        CREATE TABLE IF NOT EXISTS {self.schema}.journeys (
            tenant_id TEXT NOT NULL,
            environment TEXT NOT NULL,
            journey_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            stage_coverage JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            is_terminal BOOLEAN NOT NULL DEFAULT FALSE,
            first_occurred_at TIMESTAMPTZ NOT NULL,
            last_occurred_at TIMESTAMPTZ NOT NULL,
            first_ingested_seq BIGINT NOT NULL,
            last_ingested_seq BIGINT NOT NULL,
            current_identity_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            evidence_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            diagnostic_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            loop_run_id TEXT NOT NULL DEFAULT '',
            projection_revision BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant_id, environment, journey_id)
        );

        CREATE INDEX IF NOT EXISTS idx_journeys_tenant_env_updated_journey
            ON {self.schema}.journeys (tenant_id, environment, updated_at DESC, journey_id DESC);

        CREATE INDEX IF NOT EXISTS idx_journeys_tenant_env_created_journey
            ON {self.schema}.journeys (tenant_id, environment, created_at DESC, journey_id DESC);

        CREATE INDEX IF NOT EXISTS idx_journeys_tenant_env_status_updated_journey
            ON {self.schema}.journeys (tenant_id, environment, status, updated_at DESC, journey_id DESC);

        CREATE INDEX IF NOT EXISTS idx_journeys_tenant_env_loop_run
            ON {self.schema}.journeys (tenant_id, environment, loop_run_id)
            WHERE loop_run_id != '';

        CREATE TABLE IF NOT EXISTS {self.schema}.journey_stages (
            tenant_id TEXT NOT NULL,
            environment TEXT NOT NULL,
            journey_id TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            stage_name TEXT NOT NULL,
            stage_status TEXT NOT NULL DEFAULT 'completed',
            stage_ordinal INT NOT NULL,
            source_ingested_seq BIGINT NOT NULL,
            event_sequence BIGINT NOT NULL DEFAULT 0,
            occurred_at TIMESTAMPTZ NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            contract_fields JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            evidence_references JSONB NOT NULL DEFAULT '[]'::jsonb,
            projection_revision BIGINT NOT NULL DEFAULT 0,
            fingerprint TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (tenant_id, environment, journey_id, source_event_id, stage_name)
        );

        CREATE INDEX IF NOT EXISTS idx_journey_stages_timeline
            ON {self.schema}.journey_stages (tenant_id, environment, journey_id, stage_ordinal, event_sequence, occurred_at, source_ingested_seq, source_event_id);

        CREATE TABLE IF NOT EXISTS {self.schema}.loop_runs (
            tenant_id TEXT NOT NULL,
            environment TEXT NOT NULL,
            loop_run_id TEXT NOT NULL,
            journey_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            lifecycle_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            freshness_lineage JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            contract_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            projection_revision BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant_id, environment, loop_run_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_loop_runs_tenant_env_journey
            ON {self.schema}.loop_runs (tenant_id, environment, journey_id)
            WHERE journey_id != '';

        CREATE INDEX IF NOT EXISTS idx_loop_runs_tenant_env_updated_loop
            ON {self.schema}.loop_runs (tenant_id, environment, updated_at DESC, loop_run_id DESC);

        CREATE TABLE IF NOT EXISTS {self.schema}.quarantine (
            event_id TEXT NOT NULL,
            ingested_seq BIGINT NOT NULL,
            reason_code TEXT NOT NULL,
            reason_detail TEXT NOT NULL DEFAULT '',
            source_event_type TEXT NOT NULL,
            tenant_id TEXT NOT NULL DEFAULT '',
            environment TEXT NOT NULL DEFAULT '',
            journey_id TEXT NOT NULL DEFAULT '',
            fingerprint TEXT NOT NULL DEFAULT '',
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            occurrence_count INT NOT NULL DEFAULT 1,
            resolution_status TEXT NOT NULL DEFAULT 'unresolved',
            resolution_audit_ref TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (event_id, ingested_seq)
        );

        CREATE INDEX IF NOT EXISTS idx_quarantine_status_first_seen
            ON {self.schema}.quarantine (resolution_status, first_seen_at);
        """
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql)

    def get_controller_state(
        self, controller_id: str, tenant_scope: str, environment_scope: str
    ) -> Optional[ControllerStateRow]:
        """Loads controller state row without locking."""
        sql = f"""
        SELECT controller_id, tenant_scope, environment_scope, checkpoint_seq, source_high_watermark,
               backlog_count, projection_revision, deployment_sha, mode, status, accepted_live,
               last_poll_at, last_success_at, last_live_success_at, last_recovery_at,
               last_backfill_at, last_replay_at, last_failure_at, last_error_message,
               unresolved_quarantine_count, updated_at
        FROM {self.schema}.controller
        WHERE controller_id=%s AND tenant_scope=%s AND environment_scope=%s
        """
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, (controller_id, tenant_scope, environment_scope))
            row = cur.fetchone()
            if not row:
                return None
            return ControllerStateRow(*row)

    def resolve_identity(
        self, tenant_id: str, environment: str, identifier_type: str, identifier_value: str
    ) -> Optional[str]:
        """Resolves an identity link to a journey_id."""
        sql = f"""
        SELECT journey_id FROM {self.schema}.identity_links
        WHERE tenant_id=%s AND environment=%s AND identifier_type=%s AND identifier_value=%s
        """
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, (tenant_id, environment, identifier_type, identifier_value))
            row = cur.fetchone()
            return row[0] if row else None

    def get_receipt(self, event_id: str) -> Optional[EventReceiptRow]:
        """Gets an event receipt by event_id."""
        sql = f"""
        SELECT event_id, ingested_seq, fingerprint, tenant_id, environment, journey_id, loop_run_id,
               source_event_type, created_at, disposition, projection_revision, projected_at
        FROM {self.schema}.event_receipts
        WHERE event_id=%s
        """
        with self._connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, (event_id,))
            row = cur.fetchone()
            if not row:
                return None
            return EventReceiptRow(*row)

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
        3. Verifies event receipts & detects fingerprint conflicts / exact duplicates.
        4. Upserts identity links & checks for identity conflicts.
        5. Upserts journey stages, journeys, loop runs, receipts, quarantines.
        6. Advances controller revision and contiguous checkpoint atomically.
        """
        # Derive controller-scoped lock ID from controller_id, tenant_scope, environment_scope
        lock_str = f"{controller_id}:{tenant_scope}:{environment_scope}"
        lock_id = (hash(lock_str) & 0x7FFFFFFF) or PROJECTION_CONTROLLER_ADVISORY_LOCK_ID

        with self._connect(self.dsn) as conn:
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
                    f"""
                    SELECT controller_id, tenant_scope, environment_scope, checkpoint_seq, source_high_watermark,
                           backlog_count, projection_revision, deployment_sha, mode, status, accepted_live,
                           last_poll_at, last_success_at, last_live_success_at, last_recovery_at,
                           last_backfill_at, last_replay_at, last_failure_at, last_error_message,
                           unresolved_quarantine_count, updated_at
                    FROM {self.schema}.controller
                    WHERE controller_id=%s AND tenant_scope=%s AND environment_scope=%s
                    FOR UPDATE
                    """,
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
                            mutation.accepted_live,
                        ),
                    )
                    curr_checkpoint_seq = 0
                    curr_revision = 0
                else:
                    curr_checkpoint_seq = ctrl_row[3]
                    curr_revision = ctrl_row[6]

                now = datetime.now(timezone.utc)

                # 3. Check receipts: filter out exact duplicates & raise on fingerprint conflict
                new_receipts: list[EventReceiptRow] = []
                exact_duplicate_receipts: list[EventReceiptRow] = []
                for receipt in mutation.receipts:
                    cur.execute(
                        f"SELECT fingerprint, disposition FROM {self.schema}.event_receipts WHERE event_id=%s",
                        (receipt.event_id,),
                    )
                    existing_r = cur.fetchone()
                    if existing_r is not None:
                        existing_fp, existing_disp = existing_r[0], existing_r[1]
                        if existing_fp != receipt.fingerprint:
                            raise ConflictingDuplicateException(
                                f"Event {receipt.event_id} reused with conflicting fingerprint {receipt.fingerprint} vs {existing_fp}"
                            )
                        # Exact duplicate
                        exact_duplicate_receipts.append(receipt)
                    else:
                        new_receipts.append(receipt)

                # If all receipts in mutation are exact duplicates and no new non-receipt mutations exist:
                is_pure_exact_duplicate = (
                    len(mutation.receipts) > 0
                    and len(exact_duplicate_receipts) == len(mutation.receipts)
                    and len(mutation.identity_links) == 0
                    and len(mutation.stages) == 0
                    and len(mutation.journeys) == 0
                    and len(mutation.loop_runs) == 0
                    and len(mutation.quarantines) == 0
                )

                if is_pure_exact_duplicate:
                    # Pure exact duplicate: do NOT increment revision, do NOT rewrite stage/aggregate rows
                    cur.execute(
                        f"""
                        SELECT controller_id, tenant_scope, environment_scope, checkpoint_seq, source_high_watermark,
                               backlog_count, projection_revision, deployment_sha, mode, status, accepted_live,
                               last_poll_at, last_success_at, last_live_success_at, last_recovery_at,
                               last_backfill_at, last_replay_at, last_failure_at, last_error_message,
                               unresolved_quarantine_count, updated_at
                        FROM {self.schema}.controller
                        WHERE controller_id=%s AND tenant_scope=%s AND environment_scope=%s
                        """,
                        (controller_id, tenant_scope, environment_scope),
                    )
                    return ControllerStateRow(*cur.fetchone())

                next_revision = curr_revision + 1

                # 4. Process identity links & check for identity conflicts
                for link in mutation.identity_links:
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
                            first_ingested_seq, last_ingested_seq, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, environment, identifier_type, identifier_value)
                        DO UPDATE SET
                            first_ingested_seq = LEAST({self.schema}.identity_links.first_ingested_seq, EXCLUDED.first_ingested_seq),
                            last_ingested_seq = GREATEST({self.schema}.identity_links.last_ingested_seq, EXCLUDED.last_ingested_seq),
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
                            now,
                            now,
                        ),
                    )

                # 5. Insert journey stages idempotently
                for stage in mutation.stages:
                    cur.execute(
                        f"""
                        INSERT INTO {self.schema}.journey_stages (
                            tenant_id, environment, journey_id, source_event_id, stage_name,
                            stage_status, stage_ordinal, source_ingested_seq, event_sequence,
                            occurred_at, recorded_at, contract_fields, evidence_references,
                            projection_revision, fingerprint
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                        ON CONFLICT (tenant_id, environment, journey_id, source_event_id, stage_name)
                        DO UPDATE SET
                            stage_status = EXCLUDED.stage_status,
                            contract_fields = EXCLUDED.contract_fields,
                            evidence_references = EXCLUDED.evidence_references,
                            projection_revision = EXCLUDED.projection_revision,
                            recorded_at = EXCLUDED.recorded_at
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

                # 6. Upsert Journeys
                for journey in mutation.journeys:
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
                for loop_run in mutation.loop_runs:
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
                for q in mutation.quarantines:
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

                # 9. Insert new Event Receipts
                for receipt in new_receipts:
                    cur.execute(
                        f"""
                        INSERT INTO {self.schema}.event_receipts (
                            event_id, ingested_seq, fingerprint, tenant_id, environment,
                            journey_id, loop_run_id, source_event_type, created_at, disposition,
                            projection_revision, projected_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (event_id) DO NOTHING
                        """,
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
                            next_revision,
                            now,
                        ),
                    )

                # Calculate contiguous checkpoint advancement from durable event receipts
                if mutation.receipts:
                    durable_seqs = sorted(r.ingested_seq for r in mutation.receipts)
                    expected_seq = curr_checkpoint_seq + 1
                    calculated_checkpoint = curr_checkpoint_seq
                    for seq in durable_seqs:
                        if seq == expected_seq:
                            calculated_checkpoint = seq
                            expected_seq += 1
                        elif seq <= curr_checkpoint_seq:
                            continue
                        else:
                            break
                    target_checkpoint_seq = calculated_checkpoint
                else:
                    target_checkpoint_seq = curr_checkpoint_seq

                # Compute actual unresolved quarantine count from database
                cur.execute(
                    f"SELECT COUNT(*) FROM {self.schema}.quarantine WHERE resolution_status='unresolved'"
                )
                actual_unresolved_quarantine_count = cur.fetchone()[0]

                # Mode-dependent timestamp updates
                mode = mutation.mode.lower()
                is_live_mode = (mode == "live")
                update_last_live = is_live_mode and mutation.accepted_live

                last_backfill_ts = now if mode == "backfill" else None
                last_recovery_ts = now if mode == "recovery" else None
                last_replay_ts = now if mode == "replay" else None

                cur.execute(
                    f"""
                    UPDATE {self.schema}.controller
                    SET checkpoint_seq = GREATEST(checkpoint_seq, %s),
                        source_high_watermark = %s,
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
                        last_error_message = %s,
                        unresolved_quarantine_count = %s,
                        updated_at = %s
                    WHERE controller_id=%s AND tenant_scope=%s AND environment_scope=%s
                    RETURNING controller_id, tenant_scope, environment_scope, checkpoint_seq, source_high_watermark,
                              backlog_count, projection_revision, deployment_sha, mode, status, accepted_live,
                              last_poll_at, last_success_at, last_live_success_at, last_recovery_at,
                              last_backfill_at, last_replay_at, last_failure_at, last_error_message,
                              unresolved_quarantine_count, updated_at
                    """,
                    (
                        target_checkpoint_seq,
                        mutation.source_high_watermark,
                        mutation.backlog_count,
                        next_revision,
                        mutation.deployment_sha,
                        mutation.mode,
                        mutation.status,
                        mutation.accepted_live,
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
