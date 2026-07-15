"""Durable recovery primitives used by the loop product recovery matrix.

The module intentionally owns a *test contract* namespace, not a product loop
side effect.  A matrix run is accepted only when the durable command, ordered
outbox event, idempotent effect, receipt, controller projection, and BFF
readback all correlate.  Product tasks must still replace the test-contract
effect with their own authoritative downstream readback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

import asyncpg

LOOP_ID = "bff_health_monitoring"
FAULT_POINTS = frozenset(
    {
        "before_outbox_persist",
        "after_outbox_persist",
        "before_downstream_mutation",
        "after_downstream_mutation",
        "after_mutation_before_receipt",
        "downstream_timeout_after_commit",
        "before_projection",
        "after_projection_before_publish",
    }
)


class RecoveryHarnessError(RuntimeError):
    """Base class for a fail-closed recovery matrix error."""


class InjectedFault(RecoveryHarnessError):
    def __init__(self, point: str):
        super().__init__(f"fault injected at {point}")
        self.point = point


class DownstreamTimedOut(RecoveryHarnessError):
    pass


class LeaseLost(RecoveryHarnessError):
    pass


class IdempotencyConflict(RecoveryHarnessError):
    pass


class InvariantViolation(RecoveryHarnessError):
    pass


@dataclass(frozen=True)
class Claim:
    run_id: str
    command_id: str
    event_id: str
    trace_id: str
    idempotency_key: str
    value: str
    worker_id: str
    lease_token: str
    attempt: int


@dataclass(frozen=True)
class WorkerOutcome:
    status: str
    worker_id: str
    command_id: Optional[str] = None
    event_id: Optional[str] = None
    attempt: Optional[int] = None
    fault_point: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def payload_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require_nonprod_boundary(
    environment: str,
    *,
    live_broker_enabled: bool,
    isolated_database: bool,
) -> None:
    clean = environment.strip().lower()
    allowed = clean in {"dev", "test", "ci", "local"} or clean.startswith(
        "loop-recovery-"
    )
    if not allowed:
        raise RecoveryHarnessError(
            f"recovery harness rejects non-dev environment {environment!r}"
        )
    if live_broker_enabled:
        raise RecoveryHarnessError("recovery harness requires live broker disabled")
    if not isolated_database:
        raise RecoveryHarnessError(
            "recovery harness requires an explicitly isolated database"
        )


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS loop_controller_records (
    loop_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    controller_id TEXT NOT NULL,
    controller_name TEXT NOT NULL,
    deployment_sha TEXT NOT NULL,
    desired_state_query TEXT,
    actual_state_query TEXT,
    last_heartbeat_at TIMESTAMPTZ,
    last_tick_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    last_failure_reason TEXT,
    last_repair_at TIMESTAMPTZ,
    last_repair_reason TEXT,
    backlog INTEGER,
    lag INTEGER,
    dlq_count INTEGER,
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    truth_level TEXT NOT NULL,
    lease_expires_at TIMESTAMPTZ,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (loop_id, tenant_id, environment)
);

CREATE TABLE IF NOT EXISTS loop_recovery_run_guards (
    run_id TEXT PRIMARY KEY,
    environment TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    isolation_token_sha256 TEXT NOT NULL,
    database_name TEXT NOT NULL,
    server_address TEXT,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS loop_recovery_commands (
    run_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    payload_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    admitted_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    last_error TEXT,
    PRIMARY KEY (run_id, command_id),
    UNIQUE (run_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS loop_recovery_outbox (
    run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    sequence_no BIGINT NOT NULL,
    causal_parent_id TEXT,
    idempotency_key TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TIMESTAMPTZ,
    emitted_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    last_error TEXT,
    PRIMARY KEY (run_id, event_id),
    UNIQUE (run_id, aggregate_type, aggregate_id, sequence_no),
    UNIQUE (run_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS loop_recovery_effects (
    run_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    value TEXT NOT NULL,
    value_sha256 TEXT NOT NULL,
    canonical_apply_count INTEGER NOT NULL DEFAULT 1,
    applied_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, command_id)
);

CREATE TABLE IF NOT EXISTS loop_recovery_receipts (
    run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    effect_sha256 TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, event_id),
    UNIQUE (run_id, command_id)
);

CREATE TABLE IF NOT EXISTS loop_recovery_projections (
    run_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    effect_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    projected_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, command_id)
);

CREATE TABLE IF NOT EXISTS loop_recovery_audit (
    sequence BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    command_id TEXT,
    worker_id TEXT,
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS idx_loop_recovery_outbox_claim
    ON loop_recovery_outbox (run_id, status, lease_expires_at, emitted_at);
CREATE INDEX IF NOT EXISTS idx_loop_recovery_audit_run
    ON loop_recovery_audit (run_id, sequence);
"""


class PostgresRecoveryHarness:
    def __init__(
        self,
        dsn: str,
        *,
        run_id: str,
        tenant_id: str,
        environment: str,
        deployment_sha: str,
        isolation_token: str,
        controller_interval_seconds: float = 0.35,
        max_attempts: int = 2,
    ) -> None:
        self.dsn = dsn
        self.run_id = run_id
        self.tenant_id = tenant_id
        self.environment = environment
        self.deployment_sha = deployment_sha
        if not isolation_token:
            raise RecoveryHarnessError("isolation token must not be empty")
        self.isolation_token = isolation_token
        self.isolation_token_sha256 = payload_digest(isolation_token)
        self._guard_verified = False
        self.controller_interval_seconds = controller_interval_seconds
        self.max_attempts = max_attempts
        self.controller_id = f"loop-recovery-controller-{run_id}"

    async def initialize(self) -> None:
        conn = await asyncpg.connect(self.dsn)
        try:
            await conn.execute(_SCHEMA_SQL)
            await conn.execute(
                """
                INSERT INTO loop_recovery_run_guards (
                    run_id, environment, tenant_id, isolation_token_sha256,
                    database_name, server_address
                ) VALUES (
                    $1, $2, $3, $4, current_database(),
                    COALESCE(inet_server_addr()::text, 'local-socket')
                )
                ON CONFLICT (run_id) DO NOTHING
                """,
                self.run_id,
                self.environment,
                self.tenant_id,
                self.isolation_token_sha256,
            )
        finally:
            await conn.close()
        await self.verify_isolation_guard()

    async def verify_isolation_guard(self) -> None:
        """Require the run nonce registered by the isolated capture process.

        A worker may receive an arbitrary DSN, but it cannot mutate that
        database unless the exact run/environment/tenant nonce was registered
        there by ``initialize`` first.
        """
        if self._guard_verified:
            return
        conn = await asyncpg.connect(self.dsn)
        try:
            row = await conn.fetchrow(
                """
                SELECT environment, tenant_id, isolation_token_sha256,
                       database_name, current_database() AS current_database
                FROM loop_recovery_run_guards
                WHERE run_id=$1
                """,
                self.run_id,
            )
        except asyncpg.UndefinedTableError as exc:
            raise RecoveryHarnessError(
                "database lacks the recovery isolation attestation table"
            ) from exc
        finally:
            await conn.close()
        accepted = bool(
            row
            and row["environment"] == self.environment
            and row["tenant_id"] == self.tenant_id
            and row["isolation_token_sha256"] == self.isolation_token_sha256
            and row["database_name"] == row["current_database"]
        )
        if not accepted:
            raise RecoveryHarnessError(
                "database recovery isolation attestation did not match this run"
            )
        self._guard_verified = True

    async def _audit(
        self,
        stage: str,
        outcome: str,
        *,
        command_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
        conn: Optional[asyncpg.Connection] = None,
    ) -> None:
        owns_connection = conn is None
        active = conn or await asyncpg.connect(self.dsn)
        try:
            await active.execute(
                """
                INSERT INTO loop_recovery_audit
                    (run_id, command_id, worker_id, stage, outcome, details)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                self.run_id,
                command_id,
                worker_id,
                stage,
                outcome,
                json.dumps(dict(details or {}), sort_keys=True),
            )
        finally:
            if owns_connection:
                await active.close()

    async def _inject(
        self,
        expected: Optional[str],
        point: str,
        *,
        command_id: str,
        worker_id: Optional[str] = None,
    ) -> None:
        if expected != point:
            return
        await self._audit(
            point,
            "fault_observed",
            command_id=command_id,
            worker_id=worker_id,
        )
        raise InjectedFault(point)

    async def admit(
        self,
        command_id: str,
        value: str,
        *,
        idempotency_key: Optional[str] = None,
        trace_id: Optional[str] = None,
        fault_point: Optional[str] = None,
    ) -> dict[str, Any]:
        await self.verify_isolation_guard()
        if fault_point and fault_point not in FAULT_POINTS:
            raise RecoveryHarnessError(f"unknown fault point {fault_point!r}")
        key = idempotency_key or f"idem-{command_id}"
        trace = trace_id or f"trace-{command_id}"
        digest = payload_digest(value)
        event_id = f"event-{command_id}"

        await self._inject(
            fault_point,
            "before_outbox_persist",
            command_id=command_id,
        )

        conn = await asyncpg.connect(self.dsn)
        replayed = False
        admitted_at: Optional[datetime] = None
        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"{self.run_id}\x1f{key}",
                )
                existing = await conn.fetchrow(
                    """
                    SELECT c.command_id, c.payload_sha256, c.trace_id,
                           c.admitted_at, o.event_id
                    FROM loop_recovery_commands AS c
                    JOIN loop_recovery_outbox AS o
                      ON o.run_id=c.run_id AND o.command_id=c.command_id
                    WHERE c.run_id = $1 AND c.idempotency_key = $2
                    FOR UPDATE
                    """,
                    self.run_id,
                    key,
                )
                if existing:
                    if existing["payload_sha256"] != digest:
                        raise IdempotencyConflict(
                            f"idempotency key {key!r} was reused with another payload"
                        )
                    replayed = True
                    command_id = existing["command_id"]
                    event_id = existing["event_id"]
                    trace = existing["trace_id"]
                    admitted_at = existing["admitted_at"]
                else:
                    admitted_at = await conn.fetchval("SELECT clock_timestamp()")
                    await conn.execute(
                        """
                        INSERT INTO loop_recovery_commands (
                            run_id, command_id, idempotency_key, trace_id,
                            payload, payload_sha256, status, admitted_at
                        ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, 'pending', $7)
                        """,
                        self.run_id,
                        command_id,
                        key,
                        trace,
                        json.dumps({"value": value}, sort_keys=True),
                        digest,
                        admitted_at,
                    )
                    await conn.execute(
                        """
                        INSERT INTO loop_recovery_outbox (
                            run_id, event_id, aggregate_type, aggregate_id,
                            sequence_no, causal_parent_id, idempotency_key,
                            trace_id, command_id, payload, status, emitted_at
                        ) VALUES (
                            $1, $2, 'recovery_contract_command', $3,
                            1, NULL, $4, $5, $3, $6::jsonb, 'pending', $7
                        )
                        """,
                        self.run_id,
                        event_id,
                        command_id,
                        key,
                        trace,
                        json.dumps({"value": value}, sort_keys=True),
                        admitted_at,
                    )
                    await self._audit(
                        "outbox_persist",
                        "committed",
                        command_id=command_id,
                        details={"event_id": event_id, "trace_id": trace},
                        conn=conn,
                    )
        finally:
            await conn.close()

        try:
            await self._inject(
                fault_point,
                "after_outbox_persist",
                command_id=command_id,
            )
        except InjectedFault:
            await self.record_failure_state(
                command_id,
                "fault after durable outbox persist",
            )
            raise
        return {
            "command_id": command_id,
            "event_id": event_id,
            "trace_id": trace,
            "idempotency_key": key,
            "replayed": replayed,
            "admitted_at": utc_iso(admitted_at),
        }

    async def claim_one(self, worker_id: str) -> Optional[Claim]:
        await self.verify_isolation_guard()
        token = str(uuid.uuid4())
        lease_seconds = self.controller_interval_seconds
        conn = await asyncpg.connect(self.dsn)
        try:
            async with conn.transaction():
                exhausted = await conn.fetch(
                    """
                    UPDATE loop_recovery_outbox
                    SET status='dlq', last_error='worker lease expired at retry limit',
                        lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL
                    WHERE run_id=$1 AND status='processing'
                      AND attempts >= $2
                      AND lease_expires_at <= clock_timestamp()
                    RETURNING command_id, event_id, attempts
                    """,
                    self.run_id,
                    self.max_attempts,
                )
                for expired in exhausted:
                    await conn.execute(
                        """
                        UPDATE loop_recovery_commands
                        SET status='dlq',
                            last_error='worker lease expired at retry limit'
                        WHERE run_id=$1 AND command_id=$2
                        """,
                        self.run_id,
                        expired["command_id"],
                    )
                    await self._audit(
                        "lease_expiry",
                        "dlq",
                        command_id=expired["command_id"],
                        details={
                            "event_id": expired["event_id"],
                            "attempt": expired["attempts"],
                        },
                        conn=conn,
                    )
                row = await conn.fetchrow(
                    """
                    WITH candidate AS (
                        SELECT run_id, event_id
                        FROM loop_recovery_outbox
                        WHERE run_id = $1
                          AND attempts < $2
                          AND (
                              status = 'pending'
                              OR (
                                  status = 'processing'
                                  AND lease_expires_at <= clock_timestamp()
                              )
                          )
                        ORDER BY emitted_at, sequence_no, event_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE loop_recovery_outbox AS o
                    SET status = 'processing',
                        attempts = o.attempts + 1,
                        lease_owner = $3,
                        lease_token = $4,
                        lease_expires_at = clock_timestamp()
                            + ($5 * interval '1 second'),
                        last_error = NULL
                    FROM candidate
                    WHERE o.run_id = candidate.run_id
                      AND o.event_id = candidate.event_id
                    RETURNING o.*
                    """,
                    self.run_id,
                    self.max_attempts,
                    worker_id,
                    token,
                    lease_seconds,
                )
                if not row:
                    return None
                payload = row["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                await conn.execute(
                    """
                    UPDATE loop_recovery_commands
                    SET status = 'processing', attempts = $3
                    WHERE run_id = $1 AND command_id = $2
                    """,
                    self.run_id,
                    row["command_id"],
                    row["attempts"],
                )
                await self._audit(
                    "outbox_claim",
                    "claimed",
                    command_id=row["command_id"],
                    worker_id=worker_id,
                    details={
                        "attempt": row["attempts"],
                        "lease_token": token,
                    },
                    conn=conn,
                )
                return Claim(
                    run_id=self.run_id,
                    command_id=row["command_id"],
                    event_id=row["event_id"],
                    trace_id=row["trace_id"],
                    idempotency_key=row["idempotency_key"],
                    value=str(payload["value"]),
                    worker_id=worker_id,
                    lease_token=token,
                    attempt=int(row["attempts"]),
                )
        finally:
            await conn.close()

    async def _require_fence(
        self, conn: asyncpg.Connection, claim: Claim
    ) -> None:
        row = await conn.fetchrow(
            """
            SELECT status, lease_owner, lease_token,
                   lease_expires_at > clock_timestamp() AS unexpired
            FROM loop_recovery_outbox
            WHERE run_id = $1 AND event_id = $2
            FOR UPDATE
            """,
            claim.run_id,
            claim.event_id,
        )
        accepted = bool(
            row
            and row["status"] == "processing"
            and row["lease_owner"] == claim.worker_id
            and row["lease_token"] == claim.lease_token
            and row["unexpired"]
        )
        if not accepted:
            raise LeaseLost(
                f"worker {claim.worker_id!r} lost fence for {claim.event_id!r}"
            )

    async def _renew_fence(self, claim: Claim) -> bool:
        conn = await asyncpg.connect(self.dsn)
        try:
            row = await conn.fetchrow(
                """
                UPDATE loop_recovery_outbox
                SET lease_expires_at = clock_timestamp()
                    + ($5 * interval '1 second')
                WHERE run_id=$1 AND event_id=$2
                  AND status='processing'
                  AND lease_owner=$3 AND lease_token=$4
                  AND lease_expires_at > clock_timestamp()
                RETURNING event_id
                """,
                claim.run_id,
                claim.event_id,
                claim.worker_id,
                claim.lease_token,
                self.controller_interval_seconds,
            )
            return row is not None
        finally:
            await conn.close()

    async def _keep_fence_alive(self, claim: Claim) -> None:
        period = max(0.05, self.controller_interval_seconds / 3)
        while True:
            await asyncio.sleep(period)
            if not await self._renew_fence(claim):
                return

    async def apply_effect(
        self, claim: Claim, *, fault_point: Optional[str] = None
    ) -> None:
        await self.verify_isolation_guard()
        digest = payload_digest(claim.value)
        conn = await asyncpg.connect(self.dsn)
        try:
            try:
                async with conn.transaction():
                    await self._require_fence(conn, claim)
                    await conn.execute(
                        """
                        INSERT INTO loop_recovery_effects (
                            run_id, command_id, event_id, trace_id,
                            idempotency_key, value, value_sha256, applied_at
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, clock_timestamp()
                        )
                        ON CONFLICT (run_id, command_id) DO NOTHING
                        """,
                        claim.run_id,
                        claim.command_id,
                        claim.event_id,
                        claim.trace_id,
                        claim.idempotency_key,
                        claim.value,
                        digest,
                    )
                    row = await conn.fetchrow(
                        """
                        SELECT event_id, trace_id, idempotency_key,
                               value_sha256, canonical_apply_count
                        FROM loop_recovery_effects
                        WHERE run_id = $1 AND command_id = $2
                        """,
                        claim.run_id,
                        claim.command_id,
                    )
                    expected = {
                        "event_id": claim.event_id,
                        "trace_id": claim.trace_id,
                        "idempotency_key": claim.idempotency_key,
                        "value_sha256": digest,
                        "canonical_apply_count": 1,
                    }
                    actual = dict(row) if row else {}
                    if actual != expected:
                        raise InvariantViolation(
                            f"canonical effect conflict: expected={expected!r} actual={actual!r}"
                        )
                    if fault_point == "after_downstream_mutation":
                        raise InjectedFault("after_downstream_mutation")
                    await self._audit(
                        "downstream_mutation",
                        "authoritative_readback_pass",
                        command_id=claim.command_id,
                        worker_id=claim.worker_id,
                        details={"effect_sha256": digest},
                        conn=conn,
                    )
            except InjectedFault:
                await self._audit(
                    "after_downstream_mutation",
                    "fault_observed",
                    command_id=claim.command_id,
                    worker_id=claim.worker_id,
                    details={"transaction_rolled_back": True},
                )
                raise
        finally:
            await conn.close()

    async def persist_receipt(self, claim: Claim) -> None:
        await self.verify_isolation_guard()
        digest = payload_digest(claim.value)
        conn = await asyncpg.connect(self.dsn)
        try:
            async with conn.transaction():
                await self._require_fence(conn, claim)
                effect = await conn.fetchrow(
                    """
                    SELECT event_id, trace_id, idempotency_key, value_sha256
                    FROM loop_recovery_effects
                    WHERE run_id = $1 AND command_id = $2
                    """,
                    claim.run_id,
                    claim.command_id,
                )
                if not effect or effect["value_sha256"] != digest:
                    raise InvariantViolation("receipt refused without canonical effect")
                await conn.execute(
                    """
                    INSERT INTO loop_recovery_receipts (
                        run_id, event_id, command_id, trace_id,
                        idempotency_key, effect_sha256, processed_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, clock_timestamp())
                    ON CONFLICT (run_id, event_id) DO NOTHING
                    """,
                    claim.run_id,
                    claim.event_id,
                    claim.command_id,
                    claim.trace_id,
                    claim.idempotency_key,
                    digest,
                )
                await self._audit(
                    "receipt_persist",
                    "authoritative_readback_pass",
                    command_id=claim.command_id,
                    worker_id=claim.worker_id,
                    details={"event_id": claim.event_id},
                    conn=conn,
                )
        finally:
            await conn.close()

    async def record_failure_state(
        self,
        command_id: str,
        reason: str,
        *,
        worker_id: Optional[str] = None,
    ) -> None:
        """Project an observed non-terminal state without claiming success."""
        await self.verify_isolation_guard()
        conn = await asyncpg.connect(self.dsn)
        try:
            async with conn.transaction():
                metrics = await conn.fetchrow(
                    """
                    SELECT
                      count(*) FILTER (
                        WHERE status IN ('pending', 'processing')
                      ) AS backlog,
                      count(*) FILTER (WHERE status='dlq') AS dlq_count
                    FROM loop_recovery_outbox
                    WHERE run_id=$1
                    """,
                    self.run_id,
                )
                evidence_refs = [
                    f"recovery-run:{self.run_id}:{command_id}:failure"
                ]
                payload = {
                    "recovery_run_id": self.run_id,
                    "command_id": command_id,
                    "failure_reason": reason,
                    "worker_id": worker_id,
                }
                await conn.execute(
                    """
                    INSERT INTO loop_controller_records (
                        loop_id, tenant_id, environment, controller_id,
                        controller_name, deployment_sha, desired_state_query,
                        actual_state_query, last_heartbeat_at, last_tick_at,
                        last_failure_at, last_failure_reason, backlog, lag,
                        dlq_count, evidence_refs, truth_level,
                        lease_expires_at, payload, updated_at
                    ) VALUES (
                        $1, $2, $3, $4,
                        'loop-recovery-contract-controller', $5,
                        'admitted recovery contract commands',
                        'observed non-terminal recovery contract state',
                        clock_timestamp(), clock_timestamp(),
                        clock_timestamp(), $6, $7, 0, $8, $9::jsonb,
                        'scheduled_tick',
                        clock_timestamp() + ($11 * interval '1 second'),
                        $10::jsonb, clock_timestamp()
                    )
                    ON CONFLICT (loop_id, tenant_id, environment) DO UPDATE SET
                        controller_id=EXCLUDED.controller_id,
                        controller_name=EXCLUDED.controller_name,
                        deployment_sha=EXCLUDED.deployment_sha,
                        desired_state_query=EXCLUDED.desired_state_query,
                        actual_state_query=EXCLUDED.actual_state_query,
                        last_heartbeat_at=EXCLUDED.last_heartbeat_at,
                        last_tick_at=EXCLUDED.last_tick_at,
                        last_failure_at=EXCLUDED.last_failure_at,
                        last_failure_reason=EXCLUDED.last_failure_reason,
                        backlog=EXCLUDED.backlog,
                        lag=EXCLUDED.lag,
                        dlq_count=EXCLUDED.dlq_count,
                        evidence_refs=EXCLUDED.evidence_refs,
                        truth_level=EXCLUDED.truth_level,
                        lease_expires_at=EXCLUDED.lease_expires_at,
                        payload=EXCLUDED.payload,
                        updated_at=EXCLUDED.updated_at
                    """,
                    LOOP_ID,
                    self.tenant_id,
                    self.environment,
                    self.controller_id,
                    self.deployment_sha,
                    reason,
                    int(metrics["backlog"]),
                    int(metrics["dlq_count"]),
                    json.dumps(evidence_refs),
                    json.dumps(payload, sort_keys=True),
                    max(1, int(self.controller_interval_seconds * 4)),
                )
                await self._audit(
                    "controller_failure_projection",
                    "degraded_readback_ready",
                    command_id=command_id,
                    worker_id=worker_id,
                    details={
                        "backlog": int(metrics["backlog"]),
                        "dlq_count": int(metrics["dlq_count"]),
                        "reason": reason,
                    },
                    conn=conn,
                )
        finally:
            await conn.close()

    async def project(self, claim: Claim) -> None:
        await self.verify_isolation_guard()
        digest = payload_digest(claim.value)
        conn = await asyncpg.connect(self.dsn)
        try:
            async with conn.transaction():
                await self._require_fence(conn, claim)
                receipt = await conn.fetchrow(
                    """
                    SELECT command_id, trace_id, effect_sha256
                    FROM loop_recovery_receipts
                    WHERE run_id = $1 AND event_id = $2
                    """,
                    claim.run_id,
                    claim.event_id,
                )
                if (
                    not receipt
                    or receipt["command_id"] != claim.command_id
                    or receipt["trace_id"] != claim.trace_id
                    or receipt["effect_sha256"] != digest
                ):
                    raise InvariantViolation("projection refused without correlated receipt")
                await conn.execute(
                    """
                    INSERT INTO loop_recovery_projections (
                        run_id, command_id, event_id, trace_id,
                        effect_sha256, status, projected_at
                    ) VALUES ($1, $2, $3, $4, $5, 'completed', clock_timestamp())
                    ON CONFLICT (run_id, command_id) DO UPDATE SET
                        event_id = EXCLUDED.event_id,
                        trace_id = EXCLUDED.trace_id,
                        effect_sha256 = EXCLUDED.effect_sha256,
                        status = EXCLUDED.status,
                        projected_at = EXCLUDED.projected_at
                    """,
                    claim.run_id,
                    claim.command_id,
                    claim.event_id,
                    claim.trace_id,
                    digest,
                )
                renewed = await conn.fetchrow(
                    """
                    UPDATE loop_recovery_outbox
                    SET lease_expires_at = clock_timestamp()
                        + ($5 * interval '1 second')
                    WHERE run_id=$1 AND event_id=$2
                      AND status='processing'
                      AND lease_owner=$3 AND lease_token=$4
                      AND lease_expires_at > clock_timestamp()
                    RETURNING event_id
                    """,
                    claim.run_id,
                    claim.event_id,
                    claim.worker_id,
                    claim.lease_token,
                    self.controller_interval_seconds,
                )
                if not renewed:
                    raise LeaseLost("projection could not renew its lease fence")
                controller_payload = {
                    "last_success_summary": f"Recovery contract projected {claim.command_id}",
                    "recovery_run_id": self.run_id,
                    "command_id": claim.command_id,
                    "event_id": claim.event_id,
                    "trace_id": claim.trace_id,
                    "effect_sha256": digest,
                }
                evidence_refs = [
                    f"recovery-run:{self.run_id}:{claim.command_id}"
                ]
                metrics = await conn.fetchrow(
                    """
                    SELECT
                      count(*) FILTER (
                        WHERE status IN ('pending', 'processing')
                          AND command_id <> $2
                      ) AS backlog,
                      count(*) FILTER (WHERE status='dlq') AS dlq_count
                    FROM loop_recovery_outbox
                    WHERE run_id=$1
                    """,
                    self.run_id,
                    claim.command_id,
                )
                await conn.execute(
                    """
                    INSERT INTO loop_controller_records (
                        loop_id, tenant_id, environment, controller_id,
                        controller_name, deployment_sha, desired_state_query,
                        actual_state_query, last_heartbeat_at, last_tick_at,
                        last_success_at, backlog, lag, dlq_count,
                        evidence_refs, truth_level, lease_expires_at, payload,
                        updated_at
                    ) VALUES (
                        $1, $2, $3, $4, 'loop-recovery-contract-controller',
                        $5, 'admitted recovery contract commands',
                        'correlated effect receipt projection and BFF readback',
                        clock_timestamp(), clock_timestamp(), clock_timestamp(),
                        $6, 0, $7, $8::jsonb, 'scheduled_tick',
                        clock_timestamp() + ($10 * interval '1 second'),
                        $9::jsonb, clock_timestamp()
                    )
                    ON CONFLICT (loop_id, tenant_id, environment) DO UPDATE SET
                        controller_id=EXCLUDED.controller_id,
                        controller_name=EXCLUDED.controller_name,
                        deployment_sha=EXCLUDED.deployment_sha,
                        desired_state_query=EXCLUDED.desired_state_query,
                        actual_state_query=EXCLUDED.actual_state_query,
                        last_heartbeat_at=EXCLUDED.last_heartbeat_at,
                        last_tick_at=EXCLUDED.last_tick_at,
                        last_success_at=EXCLUDED.last_success_at,
                        backlog=EXCLUDED.backlog,
                        lag=EXCLUDED.lag,
                        dlq_count=EXCLUDED.dlq_count,
                        evidence_refs=EXCLUDED.evidence_refs,
                        truth_level=EXCLUDED.truth_level,
                        lease_expires_at=EXCLUDED.lease_expires_at,
                        payload=EXCLUDED.payload,
                        updated_at=EXCLUDED.updated_at
                    """,
                    LOOP_ID,
                    self.tenant_id,
                    self.environment,
                    self.controller_id,
                    self.deployment_sha,
                    int(metrics["backlog"]),
                    int(metrics["dlq_count"]),
                    json.dumps(evidence_refs),
                    json.dumps(controller_payload, sort_keys=True),
                    max(1, int(self.controller_interval_seconds * 4)),
                )
                await self._audit(
                    "projection",
                    "controller_readback_pass",
                    command_id=claim.command_id,
                    worker_id=claim.worker_id,
                    details={"loop_id": LOOP_ID, "effect_sha256": digest},
                    conn=conn,
                )
        finally:
            await conn.close()

    async def finalize(self, claim: Claim) -> None:
        await self.verify_isolation_guard()
        conn = await asyncpg.connect(self.dsn)
        try:
            async with conn.transaction():
                await self._require_fence(conn, claim)
                terminal = await conn.fetchrow(
                    """
                    SELECT
                      c.status AS command_status,
                      c.payload_sha256 AS command_sha256,
                      c.trace_id AS command_trace_id,
                      c.idempotency_key AS command_idempotency_key,
                      o.status AS outbox_status,
                      o.payload->>'value' AS outbox_value,
                      o.trace_id AS outbox_trace_id,
                      o.idempotency_key AS outbox_idempotency_key,
                      e.event_id AS effect_event_id,
                      e.trace_id AS effect_trace_id,
                      e.idempotency_key AS effect_idempotency_key,
                      e.value_sha256 AS effect_sha256,
                      e.canonical_apply_count,
                      r.event_id AS receipt_event_id,
                      r.trace_id AS receipt_trace_id,
                      r.idempotency_key AS receipt_idempotency_key,
                      r.effect_sha256 AS receipt_sha256,
                      p.event_id AS projection_event_id,
                      p.trace_id AS projection_trace_id,
                      p.effect_sha256 AS projection_sha256,
                      p.status AS projection_status,
                      cr.payload->>'event_id' AS controller_event_id,
                      cr.payload->>'trace_id' AS controller_trace_id,
                      cr.payload->>'effect_sha256' AS controller_sha256
                    FROM loop_recovery_commands AS c
                    JOIN loop_recovery_outbox AS o
                      ON o.run_id=c.run_id AND o.command_id=c.command_id
                    JOIN loop_recovery_effects AS e
                      ON e.run_id=c.run_id AND e.command_id=c.command_id
                    JOIN loop_recovery_receipts AS r
                      ON r.run_id=c.run_id AND r.command_id=c.command_id
                    JOIN loop_recovery_projections AS p
                      ON p.run_id=c.run_id AND p.command_id=c.command_id
                    JOIN loop_controller_records AS cr
                      ON cr.tenant_id=$3 AND cr.environment=$4
                     AND cr.loop_id=$5
                     AND cr.payload->>'recovery_run_id'=c.run_id
                     AND cr.payload->>'command_id'=c.command_id
                    WHERE c.run_id=$1 AND c.command_id=$2
                    """,
                    claim.run_id,
                    claim.command_id,
                    self.tenant_id,
                    self.environment,
                    LOOP_ID,
                )
                digest = payload_digest(claim.value)
                expected_events = {
                    claim.event_id,
                    terminal["effect_event_id"] if terminal else None,
                    terminal["receipt_event_id"] if terminal else None,
                    terminal["projection_event_id"] if terminal else None,
                    terminal["controller_event_id"] if terminal else None,
                }
                expected_traces = {
                    claim.trace_id,
                    terminal["command_trace_id"] if terminal else None,
                    terminal["outbox_trace_id"] if terminal else None,
                    terminal["effect_trace_id"] if terminal else None,
                    terminal["receipt_trace_id"] if terminal else None,
                    terminal["projection_trace_id"] if terminal else None,
                    terminal["controller_trace_id"] if terminal else None,
                }
                expected_keys = {
                    claim.idempotency_key,
                    terminal["command_idempotency_key"] if terminal else None,
                    terminal["outbox_idempotency_key"] if terminal else None,
                    terminal["effect_idempotency_key"] if terminal else None,
                    terminal["receipt_idempotency_key"] if terminal else None,
                }
                expected_hashes = {
                    digest,
                    terminal["command_sha256"] if terminal else None,
                    terminal["effect_sha256"] if terminal else None,
                    terminal["receipt_sha256"] if terminal else None,
                    terminal["projection_sha256"] if terminal else None,
                    terminal["controller_sha256"] if terminal else None,
                }
                correlated = bool(
                    terminal
                    and terminal["command_status"] == "processing"
                    and terminal["outbox_status"] == "processing"
                    and terminal["projection_status"] == "completed"
                    and terminal["outbox_value"] == claim.value
                    and terminal["canonical_apply_count"] == 1
                    and expected_events == {claim.event_id}
                    and expected_traces == {claim.trace_id}
                    and expected_keys == {claim.idempotency_key}
                    and expected_hashes == {digest}
                )
                if not correlated:
                    raise InvariantViolation(
                        "cannot publish before fully correlated terminal readback"
                    )
                completed_at = await conn.fetchval("SELECT clock_timestamp()")
                published = await conn.fetchrow(
                    """
                    UPDATE loop_recovery_outbox
                    SET status='published', published_at=$5,
                        lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL
                    WHERE run_id=$1 AND event_id=$2
                      AND status='processing'
                      AND lease_owner=$3 AND lease_token=$4
                      AND lease_expires_at > clock_timestamp()
                    RETURNING event_id
                    """,
                    claim.run_id,
                    claim.event_id,
                    claim.worker_id,
                    claim.lease_token,
                    completed_at,
                )
                if not published:
                    raise LeaseLost("terminal publish lost its lease fence")
                completed = await conn.fetchrow(
                    """
                    UPDATE loop_recovery_commands
                    SET status='completed', completed_at=$3, last_error=NULL
                    WHERE run_id=$1 AND command_id=$2
                      AND status='processing'
                    RETURNING command_id
                    """,
                    claim.run_id,
                    claim.command_id,
                    completed_at,
                )
                if not completed:
                    raise InvariantViolation("terminal command transition was rejected")
                await self._audit(
                    "outbox_publish",
                    "terminal_readback_pass",
                    command_id=claim.command_id,
                    worker_id=claim.worker_id,
                    details={"completed_at": utc_iso(completed_at)},
                    conn=conn,
                )
        finally:
            await conn.close()

    async def mark_timeout(self, claim: Claim, reason: str) -> str:
        await self.verify_isolation_guard()
        target_status = "dlq" if claim.attempt >= self.max_attempts else "pending"
        conn = await asyncpg.connect(self.dsn)
        try:
            async with conn.transaction():
                await self._require_fence(conn, claim)
                await conn.execute(
                    """
                    UPDATE loop_recovery_outbox
                    SET status=$5, last_error=$6,
                        lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL
                    WHERE run_id=$1 AND event_id=$2
                      AND lease_owner=$3 AND lease_token=$4
                    """,
                    claim.run_id,
                    claim.event_id,
                    claim.worker_id,
                    claim.lease_token,
                    target_status,
                    reason,
                )
                await conn.execute(
                    """
                    UPDATE loop_recovery_commands
                    SET status=$3, last_error=$4
                    WHERE run_id=$1 AND command_id=$2
                    """,
                    claim.run_id,
                    claim.command_id,
                    target_status,
                    reason,
                )
                await self._audit(
                    "downstream_timeout",
                    target_status,
                    command_id=claim.command_id,
                    worker_id=claim.worker_id,
                    details={"attempt": claim.attempt, "reason": reason},
                    conn=conn,
                )
        finally:
            await conn.close()
        await self.record_failure_state(
            claim.command_id,
            f"{reason}; recovery status={target_status}",
            worker_id=claim.worker_id,
        )
        return target_status

    async def process_one(
        self,
        worker_id: str,
        *,
        fault_point: Optional[str] = None,
        timeout_seconds: float = 0.08,
    ) -> WorkerOutcome:
        claim = await self.claim_one(worker_id)
        if claim is None:
            return WorkerOutcome(status="idle", worker_id=worker_id)
        renewal = asyncio.create_task(self._keep_fence_alive(claim))
        try:
            await self._inject(
                fault_point,
                "before_downstream_mutation",
                command_id=claim.command_id,
                worker_id=worker_id,
            )
            await self.apply_effect(claim, fault_point=fault_point)
            if fault_point == "downstream_timeout_after_commit":
                await self._audit(
                    "downstream_timeout_after_commit",
                    "fault_observed",
                    command_id=claim.command_id,
                    worker_id=worker_id,
                )
                try:
                    await asyncio.wait_for(
                        asyncio.sleep(timeout_seconds * 4),
                        timeout=timeout_seconds,
                    )
                except asyncio.TimeoutError as exc:
                    await self.mark_timeout(claim, "downstream response timeout")
                    raise DownstreamTimedOut(
                        "downstream effect committed but response timed out"
                    ) from exc
                raise InvariantViolation("expected downstream timeout was not observed")
            await self._inject(
                fault_point,
                "after_mutation_before_receipt",
                command_id=claim.command_id,
                worker_id=worker_id,
            )
            await self.persist_receipt(claim)
            await self._inject(
                fault_point,
                "before_projection",
                command_id=claim.command_id,
                worker_id=worker_id,
            )
            await self.project(claim)
            await self._inject(
                fault_point,
                "after_projection_before_publish",
                command_id=claim.command_id,
                worker_id=worker_id,
            )
            await self.finalize(claim)
            return WorkerOutcome(
                status="completed",
                worker_id=worker_id,
                command_id=claim.command_id,
                event_id=claim.event_id,
                attempt=claim.attempt,
            )
        except InjectedFault as exc:
            await self.record_failure_state(
                claim.command_id,
                str(exc),
                worker_id=worker_id,
            )
            return WorkerOutcome(
                status="injected_fault",
                worker_id=worker_id,
                command_id=claim.command_id,
                event_id=claim.event_id,
                attempt=claim.attempt,
                fault_point=exc.point,
                error=str(exc),
            )
        except DownstreamTimedOut as exc:
            return WorkerOutcome(
                status="timeout",
                worker_id=worker_id,
                command_id=claim.command_id,
                event_id=claim.event_id,
                attempt=claim.attempt,
                fault_point="downstream_timeout_after_commit",
                error=str(exc),
            )
        finally:
            renewal.cancel()
            try:
                await renewal
            except asyncio.CancelledError:
                pass

    async def complete_claim(self, claim: Claim) -> WorkerOutcome:
        """Complete a previously acquired claim while renewing its fence."""
        await self.verify_isolation_guard()
        renewal = asyncio.create_task(self._keep_fence_alive(claim))
        try:
            await self.apply_effect(claim)
            await self.persist_receipt(claim)
            await self.project(claim)
            await self.finalize(claim)
            return WorkerOutcome(
                status="completed",
                worker_id=claim.worker_id,
                command_id=claim.command_id,
                event_id=claim.event_id,
                attempt=claim.attempt,
            )
        finally:
            renewal.cancel()
            try:
                await renewal
            except asyncio.CancelledError:
                pass

    async def replay_dlq(self, command_id: str) -> bool:
        await self.verify_isolation_guard()
        conn = await asyncpg.connect(self.dsn)
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE loop_recovery_outbox
                    SET status='pending', attempts=0, last_error=NULL,
                        lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL
                    WHERE run_id=$1 AND command_id=$2 AND status='dlq'
                    RETURNING event_id
                    """,
                    self.run_id,
                    command_id,
                )
                if not row:
                    return False
                await conn.execute(
                    """
                    UPDATE loop_recovery_commands
                    SET status='pending', attempts=0, last_error=NULL
                    WHERE run_id=$1 AND command_id=$2
                    """,
                    self.run_id,
                    command_id,
                )
                await self._audit(
                    "dlq_replay",
                    "requeued",
                    command_id=command_id,
                    details={"event_id": row["event_id"]},
                    conn=conn,
                )
                return True
        finally:
            await conn.close()

    async def dlq_count(self) -> int:
        await self.verify_isolation_guard()
        conn = await asyncpg.connect(self.dsn)
        try:
            return int(
                await conn.fetchval(
                    """
                    SELECT count(*) FROM loop_recovery_outbox
                    WHERE run_id=$1 AND status='dlq'
                    """,
                    self.run_id,
                )
            )
        finally:
            await conn.close()

    async def fault_observation_count(self, command_id: str, point: str) -> int:
        await self.verify_isolation_guard()
        conn = await asyncpg.connect(self.dsn)
        try:
            return int(
                await conn.fetchval(
                    """
                    SELECT count(*) FROM loop_recovery_audit
                    WHERE run_id=$1 AND command_id=$2 AND stage=$3
                      AND outcome='fault_observed'
                    """,
                    self.run_id,
                    command_id,
                    point,
                )
            )
        finally:
            await conn.close()

    async def snapshot(self, command_id: str) -> dict[str, Any]:
        await self.verify_isolation_guard()
        conn = await asyncpg.connect(self.dsn)
        try:
            queries: dict[str, tuple[str, Iterable[Any]]] = {
                "commands": (
                    "SELECT * FROM loop_recovery_commands WHERE run_id=$1 AND command_id=$2",
                    (self.run_id, command_id),
                ),
                "outbox": (
                    "SELECT * FROM loop_recovery_outbox WHERE run_id=$1 AND command_id=$2",
                    (self.run_id, command_id),
                ),
                "effects": (
                    "SELECT * FROM loop_recovery_effects WHERE run_id=$1 AND command_id=$2",
                    (self.run_id, command_id),
                ),
                "receipts": (
                    "SELECT * FROM loop_recovery_receipts WHERE run_id=$1 AND command_id=$2",
                    (self.run_id, command_id),
                ),
                "projections": (
                    "SELECT * FROM loop_recovery_projections WHERE run_id=$1 AND command_id=$2",
                    (self.run_id, command_id),
                ),
                "controller_records": (
                    """
                    SELECT * FROM loop_controller_records
                    WHERE tenant_id=$1 AND environment=$2 AND loop_id=$3
                      AND payload->>'recovery_run_id'=$4
                      AND payload->>'command_id'=$5
                    """,
                    (
                        self.tenant_id,
                        self.environment,
                        LOOP_ID,
                        self.run_id,
                        command_id,
                    ),
                ),
                "audit": (
                    """
                    SELECT * FROM loop_recovery_audit
                    WHERE run_id=$1 AND command_id=$2 ORDER BY sequence
                    """,
                    (self.run_id, command_id),
                ),
            }
            result: dict[str, Any] = {}
            for name, (sql, params) in queries.items():
                rows = await conn.fetch(sql, *params)
                normalized = []
                for row in rows:
                    item = dict(row)
                    for key, value in list(item.items()):
                        if isinstance(value, str) and key in {"payload", "details"}:
                            try:
                                item[key] = json.loads(value)
                            except json.JSONDecodeError:
                                pass
                        else:
                            item[key] = utc_iso(value)
                    normalized.append(item)
                result[name] = normalized
            return result
        finally:
            await conn.close()

    async def assert_terminal_invariants(
        self,
        command_id: str,
        *,
        max_recovery_ticks: int,
        recovery_ticks: int,
        recovery_elapsed_seconds: float,
    ) -> dict[str, Any]:
        snapshot = await self.snapshot(command_id)
        expected_counts = {
            "commands": 1,
            "outbox": 1,
            "effects": 1,
            "receipts": 1,
            "projections": 1,
            "controller_records": 1,
        }
        actual_counts = {
            key: len(snapshot[key]) for key in expected_counts
        }
        if actual_counts != expected_counts:
            raise InvariantViolation(
                f"terminal row counts differ: {actual_counts!r}"
            )
        command = snapshot["commands"][0]
        outbox = snapshot["outbox"][0]
        effect = snapshot["effects"][0]
        receipt = snapshot["receipts"][0]
        projection = snapshot["projections"][0]
        controller = snapshot["controller_records"][0]
        controller_payload = controller["payload"]
        if isinstance(controller_payload, str):
            controller_payload = json.loads(controller_payload)
        command_ids = {
            command["command_id"],
            outbox["command_id"],
            effect["command_id"],
            receipt["command_id"],
            projection["command_id"],
        }
        command_correlated = command_ids == {command_id}
        command_payload = command["payload"]
        if isinstance(command_payload, str):
            command_payload = json.loads(command_payload)
        outbox_payload = outbox["payload"]
        if isinstance(outbox_payload, str):
            outbox_payload = json.loads(outbox_payload)
        command_value = str(command_payload.get("value"))
        outbox_value = str(outbox_payload.get("value"))
        digest = payload_digest(command_value)
        payload_correlated = (
            command_value == outbox_value == effect["value"]
            and command["payload_sha256"] == digest
            and effect["value_sha256"] == digest
        )
        terminal_durable = (
            command["status"] == "completed"
            and outbox["status"] == "published"
            and effect["canonical_apply_count"] == 1
            and command_correlated
            and payload_correlated
        )
        checks = {
            "command_completed": command["status"] == "completed",
            "outbox_published": outbox["status"] == "published",
            "canonical_apply_once": effect["canonical_apply_count"] == 1,
            "command_correlated": command_correlated,
            "payload_correlated": payload_correlated,
            "event_correlated": len(
                {outbox["event_id"], effect["event_id"], receipt["event_id"], projection["event_id"]}
            ) == 1,
            "trace_correlated": len(
                {outbox["trace_id"], effect["trace_id"], receipt["trace_id"], projection["trace_id"]}
            ) == 1,
            "idempotency_correlated": len(
                {outbox["idempotency_key"], effect["idempotency_key"], receipt["idempotency_key"]}
            ) == 1,
            "effect_correlated": len(
                {effect["value_sha256"], receipt["effect_sha256"], projection["effect_sha256"]}
            ) == 1,
            "controller_correlated": (
                controller_payload.get("recovery_run_id") == self.run_id
                and controller_payload.get("command_id") == command_id
                and controller_payload.get("event_id") == outbox["event_id"]
                and controller_payload.get("trace_id") == outbox["trace_id"]
                and controller_payload.get("effect_sha256") == effect["value_sha256"]
            ),
            "rpo_zero": terminal_durable,
            "recovery_within_two_intervals": (
                0 < recovery_ticks <= max_recovery_ticks
                and 0 <= recovery_elapsed_seconds
                <= self.controller_interval_seconds * max_recovery_ticks
            ),
        }
        failed = sorted(key for key, passed in checks.items() if not passed)
        if failed:
            raise InvariantViolation(
                f"terminal invariant failures: {failed!r}; "
                f"recovery_elapsed_seconds={recovery_elapsed_seconds:.6f}; "
                f"deadline_seconds={self.controller_interval_seconds * max_recovery_ticks:.6f}"
            )
        admitted_at = datetime.fromisoformat(command["admitted_at"].replace("Z", "+00:00"))
        completed_at = datetime.fromisoformat(command["completed_at"].replace("Z", "+00:00"))
        return {
            "status": "pass",
            "checks": checks,
            "counts": actual_counts,
            "recovery_ticks": recovery_ticks,
            "max_recovery_ticks": max_recovery_ticks,
            "recovery_elapsed_seconds": round(recovery_elapsed_seconds, 6),
            "recovery_seconds": round((completed_at - admitted_at).total_seconds(), 6),
            "snapshot": snapshot,
        }
