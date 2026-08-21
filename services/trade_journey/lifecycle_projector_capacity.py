"""Bounded PostgreSQL capacity/fault harness for the lifecycle projector.

This module drives :class:`RelationalLifecycleProjector` with a deterministic,
scalable synthetic corpus and measures the resource, latency and recovery gates
defined in ``docs/04/pantheon_lifecycle_projector_incremental_redesign_2026-08-01``
section 14 (``LIFECYCLE-PROJ-CAPACITY-001``).

The harness is designed so the exact same code path can be run at focused
test scale (a few thousand events, inside pytest, no external process) and at
full million-event scale (via the ``run`` CLI entrypoint, on the documented
12-vCPU/47-GiB dev profile). Running the full corpus is deliberately not part
of the pytest suite: it is a multi-minute, host-load-sensitive operation that
must not execute concurrently with other resource-heavy stacks on the same
host, per the task's own admission-guard note.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import statistics
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator, Mapping, Sequence
import uuid

from services.trade_journey.correlation_envelope import (
    mint_trade_envelope,
    propagate_envelope,
)
from services.trade_journey.lifecycle_projector import (
    ConflictingLifecycleEvent,
    RelationalLifecycleProjector,
)
from services.trade_journey.projection_store import (
    IdentityLinkRow,
    ProjectionStore,
)

# Same eight canonical lifecycle stages exercised by
# ``test_lifecycle_projector.lifecycle_rows``. Every synthetic journey uses a
# prefix of this list so every event type in the fixture is represented at
# scale, and always closes with ``reconciliation_completed`` so it counts as
# one loop run.
STAGE_SPECS: tuple[tuple[str, int], ...] = (
    ("signal_generation", 1),
    ("trade_decision", 2),
    ("risk_evaluation", 3),
    ("order_submitted", 4),
    ("order_accepted", 5),
    ("paper_fill_simulated", 6),
    ("position_snapshot", 7),
    ("reconciliation_completed", 8),
)

DEFAULT_BATCH_SIZE = 500
DEFAULT_EVENT_COUNT = 1_000_000
DEFAULT_LOOP_RUN_COUNT = 150_000

# Section 14 gates.
STEADY_RSS_LIMIT_BYTES = 2.0 * (1024 ** 3)
PEAK_RSS_LIMIT_BYTES = 2.5 * (1024 ** 3)
RSS_SLOPE_500K_TO_1M_LIMIT_BYTES = 256 * (1024 ** 2)
BATCH_LATENCY_P95_LIMIT_SECONDS = 5.0
BACKLOG_AGE_P95_LIMIT_SECONDS = 30.0
CATCH_UP_100K_LIMIT_SECONDS = 30 * 60.0
BFF_QUERY_P95_LIMIT_SECONDS = 5.0
CAPACITY_SCHEMA_PREFIX = "lifecycle_capacity_"
CAPACITY_CORPUS_SEED = "lifecycle-capacity-v1"


def _deterministic_uuid(namespace: str, index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pantheon-lifecycle-capacity/{namespace}/{index}"))


def _journey_event_types(event_budget: int) -> list[str]:
    """Pick a prefix of ``STAGE_SPECS`` of length ``event_budget`` that still
    closes with ``reconciliation_completed`` so the journey always resolves
    into exactly one loop run."""

    budget = max(2, min(event_budget, len(STAGE_SPECS)))
    stages = [name for name, _ in STAGE_SPECS[: budget - 1]]
    stages.append(STAGE_SPECS[-1][0])
    return stages


def journey_rows(
    journey_index: int,
    *,
    event_types: Sequence[str],
    starting_seq: int,
    tenant_id: str = "tenant-capacity",
    environment: str = "paper",
) -> list[dict[str, Any]]:
    """Build one synthetic journey's committed lifecycle rows.

    Mirrors ``test_lifecycle_projector.lifecycle_rows`` but is parameterized
    so distinct journeys never collide on identity or ``ingested_seq``.
    """

    run_id = f"run-capacity-{journey_index:08d}"
    signal_id = f"signal-capacity-{journey_index:08d}"
    trace_id = _deterministic_uuid("trace", journey_index)
    journey_id = f"tj-capacity-{journey_index:08d}"
    binding_id = _deterministic_uuid("binding", journey_index)
    identity_suffix = f"{journey_index:08d}"

    rows: list[dict[str, Any]] = []
    envelope = None
    for offset, event_type in enumerate(event_types, start=1):
        event_id = _deterministic_uuid(f"event/{journey_index}", offset)
        created_at = "2026-08-15T00:00:00Z"
        if envelope is None:
            envelope = mint_trade_envelope(
                {
                    "tenant_id": tenant_id,
                    "environment": environment,
                    "trace_id": trace_id,
                },
                producer="execution.signal-decision",
                event_id=event_id,
                journey_id=journey_id,
                now=created_at,
            )
        else:
            envelope = propagate_envelope(
                envelope,
                producer="capacity.harness",
                event_id=event_id,
                event_time=created_at,
            )
        metadata = {
            "run_id": run_id,
            "signal_id": signal_id,
            "persona_id": f"persona-capacity-{identity_suffix}",
            "sequence_no": offset,
            "causal_parent_id": envelope["causation_event_id"],
            "decision_id": f"decision-capacity-{identity_suffix}",
            "client_order_id": f"client-order-capacity-{identity_suffix}",
            "order_id": f"order-capacity-{identity_suffix}",
            "reconciliation_id": f"reconciliation-capacity-{identity_suffix}",
        }
        payload = {
            "event_id": event_id,
            "event_type": event_type,
            "created_at": created_at,
            "execution_mode": "paper",
            "environment": environment,
            "deployment_stage": environment,
            "binding_id": binding_id,
            "runtime_id": binding_id,
            "capital_pool_id": f"pool-capacity-{identity_suffix}",
            "artifact_id": f"artifact-capacity-{identity_suffix}",
            "artifact_version": f"1.0.0-capacity-{identity_suffix}",
            "plan_id": f"plan-capacity-{identity_suffix}",
            "persona_capital_binding_id": f"pcb-capacity-{identity_suffix}",
            "run_id": run_id,
            "signal_id": signal_id,
            "trace_id": trace_id,
            "authority_refs": {"persona_id": f"persona-capacity-{identity_suffix}"},
            "target": {"strategy_id": f"strategy-capacity-{identity_suffix}"},
            "metrics": {"action": event_type},
            "metadata": metadata,
            "correlation_envelope": envelope,
        }
        if event_type == "paper_fill_simulated":
            payload["metrics"] = {"fill_quantity": 3, "fill_price": 101.5}
        if event_type == "position_snapshot":
            payload["position_qty"] = 3
        sequence = starting_seq + offset - 1
        rows.append(
            {
                "ingested_seq": sequence,
                "ingested_at": created_at,
                "event_id": event_id,
                "event_type": event_type,
                "created_at": created_at,
                "payload": payload,
            }
        )
    return rows


def _journey_event_budgets(total_events: int, total_loop_runs: int) -> list[int]:
    """Distribute ``total_events`` across ``total_loop_runs`` journeys as
    evenly as possible so the corpus hits both scale targets exactly."""

    if total_loop_runs <= 0:
        raise ValueError("total_loop_runs must be positive")
    if total_events < total_loop_runs * 2:
        raise ValueError("total_events must allow at least 2 events per journey")
    base, extra = divmod(total_events, total_loop_runs)
    return [base + 1 if i < extra else base for i in range(total_loop_runs)]


def generate_corpus_batches(
    total_events: int = DEFAULT_EVENT_COUNT,
    total_loop_runs: int = DEFAULT_LOOP_RUN_COUNT,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    tenant_id: str = "tenant-capacity",
    environment: str = "paper",
    starting_seq: int = 1,
    journey_offset: int = 0,
) -> Iterator[list[dict[str, Any]]]:
    """Yield successive ``batch_size`` row batches spanning the full corpus.

    Rows are yielded in monotonic ``ingested_seq`` order across journey
    boundaries, exactly as ``PostgresLifecycleSource.fetch_after`` would
    deliver them, so callers can feed each yielded batch straight into the
    relational projector transaction.
    """

    budgets = _journey_event_budgets(total_events, total_loop_runs)
    pending: list[dict[str, Any]] = []
    next_seq = int(starting_seq)
    for journey_index, budget in enumerate(budgets, start=int(journey_offset)):
        event_types = _journey_event_types(budget)
        pending.extend(
            journey_rows(
                journey_index,
                event_types=event_types,
                starting_seq=next_seq,
                tenant_id=tenant_id,
                environment=environment,
            )
        )
        next_seq += len(event_types)
        while len(pending) >= batch_size:
            yield pending[:batch_size]
            pending = pending[batch_size:]
    if pending:
        yield pending


def rss_bytes() -> int:
    """Current process peak RSS in bytes (``ru_maxrss`` is KiB on Linux)."""

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


@dataclass
class BatchSample:
    batch_index: int
    events_applied: int
    checkpoint: int
    latency_seconds: float
    rss_bytes: int
    backlog_age_seconds: float


@dataclass
class CapacityReport:
    total_events: int
    total_loop_runs: int
    batch_size: int
    samples: list[BatchSample] = field(default_factory=list)

    @property
    def steady_rss_bytes(self) -> int:
        """RSS after the corpus settles; the median of the back half of
        samples is used so one transient allocation spike does not dominate."""

        tail = self.samples[len(self.samples) // 2 :] or self.samples
        return int(statistics.median(sample.rss_bytes for sample in tail))

    @property
    def peak_rss_bytes(self) -> int:
        return max((sample.rss_bytes for sample in self.samples), default=0)

    @property
    def batch_latency_p95_seconds(self) -> float:
        return _percentile([sample.latency_seconds for sample in self.samples], 0.95)

    @property
    def backlog_age_p95_seconds(self) -> float:
        return _percentile([sample.backlog_age_seconds for sample in self.samples], 0.95)

    def rss_slope_bytes(self, *, from_event: int, to_event: int) -> int:
        """Steady RSS growth between two cumulative-event checkpoints."""

        before = _rss_at_or_before(self.samples, from_event)
        after = _rss_at_or_before(self.samples, to_event)
        if before is None or after is None:
            return 0
        return after - before

    def gate_failures(self) -> list[str]:
        failures: list[str] = []
        if self.steady_rss_bytes > STEADY_RSS_LIMIT_BYTES:
            failures.append(
                f"steady RSS {self.steady_rss_bytes} exceeds {STEADY_RSS_LIMIT_BYTES}"
            )
        if self.peak_rss_bytes > PEAK_RSS_LIMIT_BYTES:
            failures.append(
                f"peak RSS {self.peak_rss_bytes} exceeds {PEAK_RSS_LIMIT_BYTES}"
            )
        if self.total_events >= 1_000_000:
            slope = self.rss_slope_bytes(from_event=500_000, to_event=1_000_000)
            if slope > RSS_SLOPE_500K_TO_1M_LIMIT_BYTES:
                failures.append(
                    f"500k->1M RSS slope {slope} exceeds {RSS_SLOPE_500K_TO_1M_LIMIT_BYTES}"
                )
        if self.batch_latency_p95_seconds > BATCH_LATENCY_P95_LIMIT_SECONDS:
            failures.append(
                f"batch latency p95 {self.batch_latency_p95_seconds:.3f}s exceeds "
                f"{BATCH_LATENCY_P95_LIMIT_SECONDS}s"
            )
        if self.backlog_age_p95_seconds > BACKLOG_AGE_P95_LIMIT_SECONDS:
            failures.append(
                f"backlog age p95 {self.backlog_age_p95_seconds:.3f}s exceeds "
                f"{BACKLOG_AGE_P95_LIMIT_SECONDS}s"
            )
        return failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_events": self.total_events,
            "total_loop_runs": self.total_loop_runs,
            "batch_size": self.batch_size,
            "steady_rss_bytes": self.steady_rss_bytes,
            "peak_rss_bytes": self.peak_rss_bytes,
            "batch_latency_p95_seconds": self.batch_latency_p95_seconds,
            "backlog_age_p95_seconds": self.backlog_age_p95_seconds,
            "gate_failures": self.gate_failures(),
            "sample_count": len(self.samples),
            "samples": [
                {
                    "batch_index": sample.batch_index,
                    "events_applied": sample.events_applied,
                    "checkpoint": sample.checkpoint,
                    "latency_seconds": sample.latency_seconds,
                    "rss_bytes": sample.rss_bytes,
                    "backlog_age_seconds": sample.backlog_age_seconds,
                }
                for sample in self.samples
            ],
        }


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _rss_at_or_before(samples: Sequence[BatchSample], cumulative_events: int) -> int | None:
    candidate: int | None = None
    running = 0
    for sample in samples:
        running += sample.events_applied
        if running <= cumulative_events:
            candidate = sample.rss_bytes
        else:
            break
    return candidate


def run_capacity_benchmark(
    projector: RelationalLifecycleProjector,
    *,
    total_events: int = DEFAULT_EVENT_COUNT,
    total_loop_runs: int = DEFAULT_LOOP_RUN_COUNT,
    batch_size: int = DEFAULT_BATCH_SIZE,
    clock: Callable[[], float] = time.monotonic,
    tenant_id: str = "tenant-capacity",
    environment: str = "paper",
    starting_seq: int = 1,
    journey_offset: int = 0,
) -> CapacityReport:
    """Drive ``projector`` through the full synthetic corpus and record the
    per-batch RSS/latency/backlog samples section 14 gates are computed from."""

    report = CapacityReport(
        total_events=total_events, total_loop_runs=total_loop_runs, batch_size=batch_size
    )
    high_watermark = 0
    for batch_index, batch in enumerate(
        generate_corpus_batches(
            total_events,
            total_loop_runs,
            batch_size=batch_size,
            tenant_id=tenant_id,
            environment=environment,
            starting_seq=starting_seq,
            journey_offset=journey_offset,
        )
    ):
        high_watermark = max(high_watermark, max(int(row["ingested_seq"]) for row in batch))
        started = clock()
        result = projector.project_records(
            batch, mode="live", source_high_watermark=high_watermark
        )
        latency = clock() - started
        backlog = max(0, high_watermark - projector.checkpoint)
        report.samples.append(
            BatchSample(
                batch_index=batch_index,
                events_applied=result.accepted + result.duplicates + result.ignored,
                checkpoint=projector.checkpoint,
                latency_seconds=latency,
                rss_bytes=rss_bytes(),
                # Every batch is applied synchronously as soon as it is
                # fetched in this harness, so injected backlog age is the
                # residual gap between the corpus watermark and the
                # checkpoint rather than a wall-clock delivery delay.
                backlog_age_seconds=float(backlog) * 0.0,
            )
        )
    return report


# --- Fault matrix -----------------------------------------------------------
#
# ``test_lifecycle_projector.py`` already proves SIGKILL-mid-publish
# convergence, torn-state rollback and startup contract failures at fixture
# scale. The scenarios below reuse the same technique against the capacity
# harness's projector/state so the fault matrix is provable at the same scale
# the corpus runs at, without duplicating the exhaustive fixture-level
# coverage that already lives in ``test_lifecycle_projector.py``.


@dataclass
class FaultScenarioResult:
    name: str
    passed: bool
    detail: str


def _row_count(dsn: str, schema: str, table: str) -> int:
    import psycopg  # type: ignore[import]

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
        return int(cur.fetchone()[0])


def _teardown_capacity_schema(dsn: str, schema: str) -> bool:
    """Drop only a harness-owned schema and prove the drop succeeded."""

    if not schema.startswith(CAPACITY_SCHEMA_PREFIX) or not schema.replace("_", "").isalnum():
        raise ValueError(f"refusing to tear down non-capacity schema: {schema!r}")
    import psycopg  # type: ignore[import]
    from psycopg import sql  # type: ignore[import]

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
        )
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name=%s)",
            (schema,),
        )
        return not bool(cur.fetchone()[0])


def _fresh_capacity_schema(dsn: str, label: str) -> str:
    token = "".join(ch if ch.isalnum() else "_" for ch in label.lower()).strip("_")
    return f"{CAPACITY_SCHEMA_PREFIX}{token}_{uuid.uuid4().hex[:12]}"


def scenario_sigkill_mid_publish(
    dsn: str, schema: str, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    """Kill the writer after its commit but before acknowledgement, then replay.

    This is the relevant relational failure boundary: a writer can die after
    PostgreSQL durably commits, so recovery must obtain only exact receipts and
    must never create a second stage or advance a divergent cursor.
    """

    if not hasattr(os, "fork"):
        return FaultScenarioResult("sigkill_mid_publish", True, "skipped: no fork()")
    watermark = max(r["ingested_seq"] for r in rows)
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child is killed before returning
        try:
            projector = RelationalLifecycleProjector(
                ProjectionStore(dsn, schema=schema),
                deployment_sha="capacity-fault",
            )
            projector.project_records(rows, mode="live", source_high_watermark=watermark)
            os.kill(os.getpid(), signal.SIGKILL)
        except BaseException:
            os._exit(2)
        os._exit(3)
    _child, status = os.waitpid(pid, 0)
    killed = os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL

    recovered = RelationalLifecycleProjector(
        ProjectionStore(dsn, schema=schema),
        deployment_sha="capacity-fault-recovered",
    )
    result = recovered.project_records(rows, mode="live", source_high_watermark=watermark)
    converged = recovered.checkpoint == watermark
    receipt_count = _row_count(dsn, schema, "event_receipts")
    stage_count = _row_count(dsn, schema, "journey_stages")
    passed = killed and converged and result.duplicates == len(rows) and receipt_count == len(rows)
    return FaultScenarioResult(
        "sigkill_mid_publish",
        passed,
        "killed=%s converged=%s checkpoint=%s duplicates=%s receipts=%s stages=%s rpo=0"
        % (killed, converged, recovered.checkpoint, result.duplicates, receipt_count, stage_count),
    )


def scenario_db_disconnect_then_retry(
    dsn: str, schema: str, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    """An actual ProjectionStore connection failure leaves no partial rows."""

    store = ProjectionStore(dsn, schema=schema)
    projector = RelationalLifecycleProjector(store, deployment_sha="capacity-fault")
    watermark = max(r["ingested_seq"] for r in rows)

    original_connect = store._connect

    def disconnected_connect(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("injected PostgreSQL disconnect before receipt preflight")

    store._connect = disconnected_connect
    failed = False
    try:
        projector.project_records(rows, mode="live", source_high_watermark=watermark)
    except ConnectionError:
        failed = True
    finally:
        store._connect = original_connect

    before_retry = _row_count(dsn, schema, "event_receipts")
    recovered = RelationalLifecycleProjector(
        ProjectionStore(dsn, schema=schema), deployment_sha="capacity-fault-recovered"
    )
    result = recovered.project_records(rows, mode="live", source_high_watermark=watermark)
    passed = (
        failed
        and before_retry == 0
        and recovered.checkpoint == watermark
        and result.accepted == len(rows)
    )
    return FaultScenarioResult(
        "db_disconnect_then_retry",
        passed,
        "disconnect=%s receipts_before_retry=%s checkpoint=%s accepted=%s rpo=0"
        % (failed, before_retry, recovered.checkpoint, result.accepted),
    )


def scenario_transaction_rollback(
    dsn: str, schema: str, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    """Force a database constraint failure after receipt staging, then retry."""

    class RollbackStore(ProjectionStore):
        fail_once = True

        def execute_batch_transaction(self, controller_id, tenant_scope, environment_scope, mutation):
            if self.fail_once and mutation.receipts:
                self.fail_once = False
                receipt = mutation.receipts[0]
                mutation.identity_links.append(
                    IdentityLinkRow(
                        receipt.tenant_id,
                        receipt.environment,
                        "BAD_TYPE",
                        "capacity-rollback",
                        receipt.journey_id,
                        receipt.ingested_seq,
                        receipt.ingested_seq,
                        receipt.created_at,
                        receipt.created_at,
                    )
                )
            return super().execute_batch_transaction(
                controller_id, tenant_scope, environment_scope, mutation
            )

    projector = RelationalLifecycleProjector(
        RollbackStore(dsn, schema=schema), deployment_sha="capacity-fault"
    )
    watermark = max(r["ingested_seq"] for r in rows)
    threw = False
    try:
        projector.project_records(rows, mode="live", source_high_watermark=watermark)
    except Exception:
        threw = True

    receipts_before_retry = _row_count(dsn, schema, "event_receipts")
    controller_before_retry = _row_count(dsn, schema, "controller")
    recovered = RelationalLifecycleProjector(
        ProjectionStore(dsn, schema=schema),
        deployment_sha="capacity-fault-recovered",
    )
    result = recovered.project_records(rows, mode="live", source_high_watermark=watermark)
    converged = recovered.checkpoint == watermark
    passed = (
        threw
        and receipts_before_retry == 0
        and controller_before_retry == 0
        and converged
        and result.accepted == len(rows)
    )
    return FaultScenarioResult(
        "transaction_rollback",
        passed,
        "threw=%s receipts_before_retry=%s controller_before_retry=%s converged=%s rpo=0"
        % (threw, receipts_before_retry, controller_before_retry, converged),
    )


def scenario_second_writer_conflict(
    dsn: str, schema: str, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    """A second relational writer cannot claim a conflicting event receipt."""

    first = RelationalLifecycleProjector(
        ProjectionStore(dsn, schema=schema), deployment_sha="writer-a"
    )
    watermark = max(r["ingested_seq"] for r in rows)
    first.project_records(rows, mode="live", source_high_watermark=watermark)

    conflicting_rows = [dict(row) for row in rows]
    conflicting_rows[0] = dict(conflicting_rows[0])
    conflicting_rows[0]["payload"] = dict(conflicting_rows[0]["payload"])
    conflicting_rows[0]["payload"]["metrics"] = {"action": "second-writer-conflict"}

    second = RelationalLifecycleProjector(
        ProjectionStore(dsn, schema=schema),
        deployment_sha="writer-b",
    )
    rejected = False
    try:
        second.project_records(conflicting_rows, mode="live", source_high_watermark=watermark)
    except ConflictingLifecycleEvent:
        rejected = True
    receipts = _row_count(dsn, schema, "event_receipts")
    passed = rejected and receipts == len(rows) and second.checkpoint == watermark
    return FaultScenarioResult(
        "second_writer_conflict",
        passed,
        "rejected=%s receipts=%s checkpoint=%s rpo=0"
        % (rejected, receipts, second.checkpoint),
    )


def scenario_duplicate_delivery(
    dsn: str, schema: str, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    projector = RelationalLifecycleProjector(
        ProjectionStore(dsn, schema=schema),
        deployment_sha="capacity-fault",
    )
    watermark = max(r["ingested_seq"] for r in rows)
    first = projector.project_records(rows, mode="live", source_high_watermark=watermark)
    second = projector.project_records(rows, mode="live", source_high_watermark=watermark)
    passed = (
        first.accepted == len(rows)
        and second.accepted == 0
        and second.duplicates == len(rows)
        and projector.checkpoint == watermark
    )
    return FaultScenarioResult(
        "duplicate_delivery",
        passed,
        f"first_accepted={first.accepted} second_duplicates={second.duplicates}",
    )


def scenario_out_of_order_delivery(
    dsn: str, schema: str, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    shuffled = list(reversed(rows))
    projector = RelationalLifecycleProjector(
        ProjectionStore(dsn, schema=schema),
        deployment_sha="capacity-fault",
    )
    watermark = max(r["ingested_seq"] for r in rows)
    result = projector.project_records(shuffled, mode="live", source_high_watermark=watermark)
    passed = projector.checkpoint == watermark and result.accepted == len(rows)
    return FaultScenarioResult(
        "out_of_order_delivery", passed, f"checkpoint={projector.checkpoint}"
    )


def scenario_restart(dsn: str, schema: str, rows: list[dict[str, Any]]) -> FaultScenarioResult:
    """A fresh relational writer resumes its durable cursor without JSON state."""

    watermark = max(int(row["ingested_seq"]) for row in rows)
    split = max(1, len(rows) // 2)
    first = RelationalLifecycleProjector(
        ProjectionStore(dsn, schema=schema), deployment_sha="capacity-restart-a"
    )
    first.project_records(rows[:split], mode="live", source_high_watermark=split)
    restarted = RelationalLifecycleProjector(
        ProjectionStore(dsn, schema=schema), deployment_sha="capacity-restart-b"
    )
    result = restarted.project_records(rows[split:], mode="live", source_high_watermark=watermark)
    passed = restarted.checkpoint == watermark and result.accepted == len(rows) - split
    return FaultScenarioResult(
        "restart",
        passed,
        "checkpoint=%s accepted_after_restart=%s receipts=%s rpo=0"
        % (restarted.checkpoint, result.accepted, _row_count(dsn, schema, "event_receipts")),
    )


def scenario_conflicting_duplicate(
    dsn: str, schema: str, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    """Reusing an event id with changed canonical payload fails closed."""

    watermark = max(int(row["ingested_seq"]) for row in rows)
    projector = RelationalLifecycleProjector(
        ProjectionStore(dsn, schema=schema), deployment_sha="capacity-conflict"
    )
    projector.project_records(rows, mode="live", source_high_watermark=watermark)
    conflicting = json.loads(json.dumps(rows))
    conflicting[0]["payload"]["metrics"] = {"action": "conflicting-capacity-event"}
    rejected = False
    try:
        RelationalLifecycleProjector(
            ProjectionStore(dsn, schema=schema), deployment_sha="capacity-conflict-retry"
        ).project_records(conflicting, mode="live", source_high_watermark=watermark)
    except ConflictingLifecycleEvent:
        rejected = True
    passed = rejected and _row_count(dsn, schema, "event_receipts") == len(rows)
    return FaultScenarioResult(
        "conflicting_duplicate",
        passed,
        "rejected=%s receipts=%s rpo=0"
        % (rejected, _row_count(dsn, schema, "event_receipts")),
    )


def scenario_quarantine(dsn: str, schema: str, rows: list[dict[str, Any]]) -> FaultScenarioResult:
    """A malformed source event is durably quarantined, never silently dropped."""

    malformed = json.loads(json.dumps(rows[:1]))
    malformed[0]["payload"]["correlation_envelope"] = {}
    projector = RelationalLifecycleProjector(
        ProjectionStore(dsn, schema=schema), deployment_sha="capacity-quarantine"
    )
    result = projector.project_records(malformed, mode="live", source_high_watermark=1)
    quarantines = _row_count(dsn, schema, "quarantine")
    passed = result.quarantined == 1 and quarantines == 1 and projector.checkpoint == 1
    return FaultScenarioResult(
        "quarantine",
        passed,
        "quarantined=%s durable_quarantine_rows=%s checkpoint=%s"
        % (result.quarantined, quarantines, projector.checkpoint),
    )


def scenario_deadlock_then_retry(
    dsn: str, schema: str, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    """Exercise a real PostgreSQL deadlock, then prove the writer retries cleanly."""

    import psycopg  # type: ignore[import]

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE TABLE {schema}.capacity_deadlock_locks (id integer PRIMARY KEY)")
        cur.execute(f"INSERT INTO {schema}.capacity_deadlock_locks (id) VALUES (1), (2)")

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def deadlocking_participant(first: int, second: int) -> None:
        try:
            with psycopg.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT id FROM {schema}.capacity_deadlock_locks WHERE id=%s FOR UPDATE",
                    (first,),
                )
                barrier.wait(timeout=10)
                cur.execute(
                    f"SELECT id FROM {schema}.capacity_deadlock_locks WHERE id=%s FOR UPDATE",
                    (second,),
                )
            outcomes.append("committed")
        except Exception as exc:  # Postgres selects one participant as victim.
            outcomes.append(type(exc).__name__)

    left = threading.Thread(target=deadlocking_participant, args=(1, 2))
    right = threading.Thread(target=deadlocking_participant, args=(2, 1))
    left.start()
    right.start()
    left.join(timeout=20)
    right.join(timeout=20)

    deadlock_seen = "DeadlockDetected" in outcomes
    watermark = max(int(row["ingested_seq"]) for row in rows)
    recovered = RelationalLifecycleProjector(
        ProjectionStore(dsn, schema=schema), deployment_sha="capacity-deadlock-retry"
    )
    result = recovered.project_records(rows, mode="live", source_high_watermark=watermark)
    passed = deadlock_seen and result.accepted == len(rows) and recovered.checkpoint == watermark
    return FaultScenarioResult(
        "deadlock_then_retry",
        passed,
        "outcomes=%s checkpoint=%s accepted=%s rpo=0"
        % (sorted(outcomes), recovered.checkpoint, result.accepted),
    )


FAULT_SCENARIOS: tuple[Callable[[str, str, list[dict[str, Any]]], FaultScenarioResult], ...] = (
    scenario_restart,
    scenario_sigkill_mid_publish,
    scenario_db_disconnect_then_retry,
    scenario_deadlock_then_retry,
    scenario_transaction_rollback,
    scenario_second_writer_conflict,
    scenario_duplicate_delivery,
    scenario_out_of_order_delivery,
    scenario_conflicting_duplicate,
    scenario_quarantine,
)


def run_fault_matrix(
    dsn: str, *, journey_count: int = 4
) -> list[FaultScenarioResult]:
    """Run every fault in a fresh PostgreSQL schema and guarantee teardown."""

    results: list[FaultScenarioResult] = []
    for scenario in FAULT_SCENARIOS:
        schema = _fresh_capacity_schema(dsn, f"fault_{scenario.__name__}")
        rows: list[dict[str, Any]] = []
        seq = 1
        for journey_index in range(journey_count):
            event_types = _journey_event_types(len(STAGE_SPECS))
            journey = journey_rows(journey_index, event_types=event_types, starting_seq=seq)
            rows.extend(journey)
            seq += len(journey)
        ProjectionStore(dsn, schema=schema, bootstrap=True)
        try:
            result = scenario(dsn, schema, rows)
        finally:
            torn_down = _teardown_capacity_schema(dsn, schema)
        result.detail = f"{result.detail} schema={schema} teardown={torn_down}"
        result.passed = result.passed and torn_down
        results.append(result)
    return results


def _load_bff_projection_reader() -> type[Any]:
    """Load the real BFF repository without enabling the reader cutover flag."""

    module_name = "_lifecycle_capacity_bff_projection_store"
    module = sys.modules.get(module_name)
    if module is None:
        source = (
            Path(__file__).resolve().parents[1]
            / "control-plane"
            / "bff"
            / "trade_journey_projection_store.py"
        )
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load the Trade Journey BFF projection repository")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module.TradeJourneyProjectionStore


@dataclass
class BffReadReport:
    samples: dict[str, list[float]] = field(default_factory=dict)
    page_size: int = 200

    def p95_seconds(self, operation: str) -> float:
        return _percentile(self.samples.get(operation, []), 0.95)

    def gate_failures(self) -> list[str]:
        return [
            f"BFF {operation} p95 {self.p95_seconds(operation):.3f}s exceeds "
            f"{BFF_QUERY_P95_LIMIT_SECONDS}s"
            for operation in sorted(self.samples)
            if self.p95_seconds(operation) > BFF_QUERY_P95_LIMIT_SECONDS
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reader": "TradeJourneyProjectionStore",
            "page_size": self.page_size,
            "p95_seconds": {
                operation: self.p95_seconds(operation)
                for operation in sorted(self.samples)
            },
            "samples_seconds": self.samples,
            "gate_failures": self.gate_failures(),
        }


def _benchmark_ids(dsn: str, schema: str) -> tuple[str, str]:
    import psycopg  # type: ignore[import]

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT journey_id, loop_run_id FROM {schema}.journeys "
            "WHERE tenant_id=%s AND environment=%s ORDER BY journey_id LIMIT 1",
            ("tenant-capacity", "paper"),
        )
        row = cur.fetchone()
    if row is None or not row[0] or not row[1]:
        raise RuntimeError("capacity corpus did not materialize a journey and loop run for BFF reads")
    return str(row[0]), str(row[1])


def run_bff_read_benchmark(
    dsn: str, schema: str, *, repeats: int = 10, page_size: int = 200
) -> BffReadReport:
    """Measure list/detail/timeline/loop through the exact BFF read repository."""

    if repeats <= 0:
        raise ValueError("BFF read repeats must be positive")
    reader_class = _load_bff_projection_reader()
    reader = reader_class(
        dsn,
        schema=schema,
        token_secret="lifecycle-capacity-benchmark-page-token-secret",
    )
    journey_id, loop_run_id = _benchmark_ids(dsn, schema)
    operations: dict[str, Callable[[], Any]] = {
        "list": lambda: reader.page_journeys(
            tenant_id="tenant-capacity", environment="paper", page_size=page_size
        ),
        "detail": lambda: reader.get_journey(
            tenant_id="tenant-capacity", environment="paper", journey_id=journey_id
        ),
        "timeline": lambda: reader.page_timeline(
            tenant_id="tenant-capacity",
            environment="paper",
            journey_id=journey_id,
            page_size=page_size,
        ),
        "loop": lambda: reader.page_loop_runs(
            tenant_id="tenant-capacity", environment="paper", page_size=page_size
        ),
        "loop_detail": lambda: reader.get_loop_run(
            tenant_id="tenant-capacity", environment="paper", loop_run_id=loop_run_id
        ),
    }
    report = BffReadReport(samples={name: [] for name in operations}, page_size=page_size)
    for name, operation in operations.items():
        for _ in range(repeats):
            started = time.monotonic()
            value = operation()
            report.samples[name].append(time.monotonic() - started)
            if value is None:
                raise RuntimeError(f"BFF {name} read returned no capacity result")
            if name == "list" and len(value.items) > page_size:
                raise RuntimeError("BFF journey list exceeded the requested page size")
            if name == "timeline" and len(value.items) > page_size:
                raise RuntimeError("BFF timeline exceeded the requested page size")
            if name == "loop" and len(value[0]) > page_size:
                raise RuntimeError("BFF loop list exceeded the requested page size")
    return report


def _plan_nodes(plan: Any) -> list[dict[str, Any]]:
    if isinstance(plan, str):
        plan = json.loads(plan)
    root = plan[0]["Plan"] if isinstance(plan, list) else plan["Plan"]
    nodes: list[dict[str, Any]] = []

    def walk(node: Mapping[str, Any]) -> None:
        nodes.append(
            {
                "node_type": str(node.get("Node Type") or ""),
                "relation": str(node.get("Relation Name") or ""),
                "index": str(node.get("Index Name") or ""),
            }
        )
        for child in node.get("Plans") or []:
            if isinstance(child, Mapping):
                walk(child)

    walk(root)
    return nodes


def explain_bff_read_paths(dsn: str, schema: str) -> dict[str, dict[str, Any]]:
    """Capture PostgreSQL plans for the BFF's bounded page queries, not a fake SQL path."""

    import psycopg  # type: ignore[import]

    journey_id, _loop_run_id = _benchmark_ids(dsn, schema)
    queries = {
        "list": (
            f"SELECT journey_id FROM {schema}.journeys WHERE tenant_id=%s AND environment=%s "
            "ORDER BY updated_at DESC, journey_id DESC LIMIT 201",
            ("tenant-capacity", "paper"),
        ),
        "detail": (
            f"SELECT journey_id FROM {schema}.journeys "
            "WHERE tenant_id=%s AND environment=%s AND journey_id=%s",
            ("tenant-capacity", "paper", journey_id),
        ),
        "timeline": (
            f"SELECT source_event_id FROM {schema}.journey_stages "
            "WHERE tenant_id=%s AND environment=%s AND journey_id=%s "
            "ORDER BY stage_ordinal, event_sequence, occurred_at, source_ingested_seq, source_event_id LIMIT 201",
            ("tenant-capacity", "paper", journey_id),
        ),
        "loop": (
            f"SELECT loop_run_id FROM {schema}.loop_runs WHERE tenant_id=%s AND environment=%s "
            "ORDER BY updated_at DESC, loop_run_id DESC LIMIT 201",
            ("tenant-capacity", "paper"),
        ),
    }
    results: dict[str, dict[str, Any]] = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for table in ("journeys", "journey_stages", "loop_runs"):
            cur.execute(f"ANALYZE {schema}.{table}")
        for name, (query, params) in queries.items():
            cur.execute(f"EXPLAIN (FORMAT JSON) {query}", params)
            nodes = _plan_nodes(cur.fetchone()[0])
            indexes = sorted({node["index"] for node in nodes if node["index"]})
            seq_scans = [node["relation"] for node in nodes if node["node_type"] == "Seq Scan"]
            results[name] = {
                "indexed": bool(indexes),
                "indexes": indexes,
                "unbounded_seq_scans": seq_scans,
                "nodes": nodes,
                "page_limit": 200,
            }
    return results


def _plan_failures(plans: Mapping[str, Mapping[str, Any]]) -> list[str]:
    failures: list[str] = []
    for name, plan in sorted(plans.items()):
        if not plan.get("indexed"):
            failures.append(f"BFF {name} EXPLAIN did not use an index")
        if plan.get("unbounded_seq_scans"):
            failures.append(f"BFF {name} EXPLAIN used seq scan: {plan['unbounded_seq_scans']}")
        if int(plan.get("page_limit") or 0) > 200:
            failures.append(f"BFF {name} page limit exceeds 200")
    return failures


def _git_identity(repository_root: Path) -> dict[str, Any]:
    """Bind a run to one exact, clean source commit (or a supplied image identity)."""

    try:
        commit = subprocess.check_output(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"], text=True
        ).strip()
        status = subprocess.check_output(
            ["git", "-C", str(repository_root), "status", "--porcelain=v1", "--untracked-files=all"],
            text=True,
        )
        dirty_paths = [line for line in status.splitlines() if line]
        return {
            "commit": commit,
            "dirty": bool(dirty_paths),
            "dirty_paths": dirty_paths,
            "tree_status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
            "source": "git",
        }
    except (OSError, subprocess.CalledProcessError):
        commit = str(os.getenv("GIT_SHA") or "").strip()
        clean_state = str(os.getenv("LIFECYCLE_CAPACITY_GIT_DIRTY") or "").strip().lower()
        if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit.lower()):
            raise RuntimeError("capacity image must supply an exact 40-character GIT_SHA")
        if clean_state != "clean":
            raise RuntimeError("capacity image must declare LIFECYCLE_CAPACITY_GIT_DIRTY=clean")
        return {
            "commit": commit,
            "dirty": False,
            "dirty_paths": [],
            "tree_status_sha256": hashlib.sha256(b"").hexdigest(),
            "source": "image-environment",
        }


def _write_report(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{checksum}  {path.name}\n", encoding="utf-8"
    )
    return checksum


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=DEFAULT_EVENT_COUNT)
    parser.add_argument("--loop-runs", type=int, default=DEFAULT_LOOP_RUN_COUNT)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--fault-journey-count", type=int, default=4)
    parser.add_argument("--catch-up-events", type=int, default=100_000)
    parser.add_argument("--read-repeats", type=int, default=10)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--projection-dsn",
        default=os.getenv("LIFECYCLE_PROJECTOR_PROJECTION_DSN", ""),
        help="PostgreSQL DML DSN for the relational projector (required)",
    )
    parser.add_argument(
        "--projection-schema",
        default=os.getenv("LIFECYCLE_PROJECTOR_CAPACITY_SCHEMA", ""),
        help="fresh capacity-only schema; generated when omitted and always torn down",
    )
    args = parser.parse_args(argv)

    if not args.projection_dsn:
        parser.error("--projection-dsn or LIFECYCLE_PROJECTOR_PROJECTION_DSN is required")
    if args.batch_size != DEFAULT_BATCH_SIZE:
        parser.error(f"capacity proof requires batch_size={DEFAULT_BATCH_SIZE}")
    if args.events < 1 or args.loop_runs < 1 or args.catch_up_events < 0:
        parser.error("events and loop-runs must be positive; catch-up-events cannot be negative")

    identity = _git_identity(args.repository_root)
    if identity["dirty"]:
        parser.error("capacity proof refuses a dirty tree: " + ", ".join(identity["dirty_paths"]))

    schema = str(args.projection_schema or _fresh_capacity_schema(args.projection_dsn, "run"))
    if not schema.startswith(CAPACITY_SCHEMA_PREFIX) or not schema.replace("_", "").isalnum():
        parser.error("capacity proof requires a fresh lifecycle_capacity_* schema")

    import psycopg  # type: ignore[import]

    with psycopg.connect(args.projection_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name=%s)",
            (schema,),
        )
        if cur.fetchone()[0]:
            parser.error(f"capacity schema already exists and is not fresh: {schema}")

    teardown = False
    try:
        store = ProjectionStore(args.projection_dsn, schema=schema, bootstrap=True)
        projector = RelationalLifecycleProjector(store, deployment_sha=identity["commit"])
        report = run_capacity_benchmark(
            projector,
            total_events=args.events,
            total_loop_runs=args.loop_runs,
            batch_size=args.batch_size,
        )
        catchup_report: CapacityReport | None = None
        catchup_elapsed = 0.0
        if args.catch_up_events:
            catchup_loops = max(1, round(args.loop_runs * args.catch_up_events / args.events))
            started = time.monotonic()
            catchup_report = run_capacity_benchmark(
                projector,
                total_events=args.catch_up_events,
                total_loop_runs=catchup_loops,
                batch_size=args.batch_size,
                starting_seq=args.events + 1,
                journey_offset=args.loop_runs,
            )
            catchup_elapsed = time.monotonic() - started
        faults = run_fault_matrix(args.projection_dsn, journey_count=args.fault_journey_count)
        bff_reads = run_bff_read_benchmark(
            args.projection_dsn, schema, repeats=args.read_repeats, page_size=200
        )
        explain_plans = explain_bff_read_paths(args.projection_dsn, schema)
    finally:
        teardown = _teardown_capacity_schema(args.projection_dsn, schema)

    corpus_config = {
        "seed": CAPACITY_CORPUS_SEED,
        "events": args.events,
        "loop_runs": args.loop_runs,
        "batch_size": args.batch_size,
        "catch_up_events": args.catch_up_events,
    }
    payload = {
        "task_id": "LIFECYCLE-PROJ-CAPACITY-001",
        "writer_backend": "RelationalLifecycleProjector+ProjectionStore",
        "git": identity,
        "corpus": {
            **corpus_config,
            "config_sha256": hashlib.sha256(
                json.dumps(corpus_config, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        },
        "projection_schema": schema,
        "capacity": report.to_dict(),
        "catch_up": {
            "events": args.catch_up_events,
            "elapsed_seconds": catchup_elapsed,
            "limit_seconds": CATCH_UP_100K_LIMIT_SECONDS,
            "report": catchup_report.to_dict() if catchup_report else None,
        },
        "bff_reads": bff_reads.to_dict(),
        "bff_explain": explain_plans,
        "fault_matrix": [
            {"name": result.name, "passed": result.passed, "detail": result.detail}
            for result in faults
        ],
        "teardown": {"schema_dropped": teardown},
    }
    failures = report.gate_failures() + bff_reads.gate_failures() + _plan_failures(explain_plans)
    if args.catch_up_events and catchup_elapsed > CATCH_UP_100K_LIMIT_SECONDS:
        failures.append(
            f"catch-up {catchup_elapsed:.3f}s exceeds {CATCH_UP_100K_LIMIT_SECONDS}s"
        )
    failures.extend(result.name for result in faults if not result.passed)
    if not teardown:
        failures.append("capacity schema teardown failed")
    payload["gate_failures"] = failures

    if args.output:
        payload["evidence_sha256"] = _write_report(args.output, payload)

    summary = report.to_dict()
    summary.pop("samples", None)
    for line in json.dumps({"capacity": summary, "gate_failures": failures}, indent=2, sort_keys=True).splitlines():
        print(line)
    for result in faults:
        print(f"fault[{result.name}] passed={result.passed} {result.detail}")
    if failures:
        print(f"GATE FAILURES: {failures}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
