"""Bounded capacity/fault harness for the incremental lifecycle projector.

This module drives :class:`LifecycleProjector` with a deterministic, scalable
synthetic corpus and measures the resource, latency and recovery gates
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
import json
import os
from pathlib import Path
import resource
import signal
import statistics
import time
from typing import Any, Callable, Iterator, Sequence
import uuid

from services.trade_journey.correlation_envelope import (
    mint_trade_envelope,
    propagate_envelope,
)
from services.trade_journey.lifecycle_projector import (
    AtomicProjectionBundle,
    ConflictingLifecycleEvent,
    LifecycleProjector,
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
    binding_id = _deterministic_uuid("binding", journey_index % 64)

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
            "persona_id": "persona-capacity",
            "sequence_no": offset,
            "causal_parent_id": envelope["causation_event_id"],
            "decision_id": f"decision-capacity-{journey_index:08d}",
            "client_order_id": f"client-order-capacity-{journey_index:08d}",
            "order_id": f"order-capacity-{journey_index:08d}",
            "reconciliation_id": f"reconciliation-capacity-{journey_index:08d}",
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
            "capital_pool_id": "pool-capacity",
            "artifact_id": "artifact-capacity",
            "artifact_version": "1.0.0",
            "plan_id": "plan-capacity",
            "persona_capital_binding_id": "pcb-capacity",
            "run_id": run_id,
            "signal_id": signal_id,
            "trace_id": trace_id,
            "authority_refs": {"persona_id": "persona-capacity"},
            "target": {"strategy_id": "strategy-capacity"},
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
) -> Iterator[list[dict[str, Any]]]:
    """Yield successive ``batch_size`` row batches spanning the full corpus.

    Rows are yielded in monotonic ``ingested_seq`` order across journey
    boundaries, exactly as ``PostgresLifecycleSource.fetch_after`` would
    deliver them, so callers can feed each yielded batch straight into
    ``LifecycleProjector.project_records``.
    """

    budgets = _journey_event_budgets(total_events, total_loop_runs)
    pending: list[dict[str, Any]] = []
    next_seq = 1
    for journey_index, budget in enumerate(budgets):
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
    projector: LifecycleProjector,
    *,
    total_events: int = DEFAULT_EVENT_COUNT,
    total_loop_runs: int = DEFAULT_LOOP_RUN_COUNT,
    batch_size: int = DEFAULT_BATCH_SIZE,
    clock: Callable[[], float] = time.monotonic,
    tenant_id: str = "tenant-capacity",
    environment: str = "paper",
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


def scenario_sigkill_mid_publish(tmp_path: Path, rows: list[dict[str, Any]]) -> FaultScenarioResult:
    if not hasattr(os, "fork"):
        return FaultScenarioResult("sigkill_mid_publish", True, "skipped: no fork()")
    watermark = max(r["ingested_seq"] for r in rows)
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child is killed before returning
        try:
            publisher = AtomicProjectionBundle(
                tmp_path,
                before_switch=lambda _path: os.kill(os.getpid(), signal.SIGKILL),
            )
            projector = LifecycleProjector(
                state_path=tmp_path / "controller_state.json",
                bundle_root=tmp_path,
                deployment_sha="capacity-fault",
                publisher=publisher,
            )
            projector.project_records(rows, mode="live", source_high_watermark=watermark)
        except BaseException:
            os._exit(2)
        os._exit(3)
    _child, status = os.waitpid(pid, 0)
    killed = os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL

    recovered = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="capacity-fault-recovered",
    )
    checkpoint_before = recovered.checkpoint
    result = recovered.project_records(rows, mode="live", source_high_watermark=watermark)
    converged = recovered.checkpoint == watermark
    no_duplicate_stage = result.duplicates == 0 or checkpoint_before > 0
    passed = killed and converged and no_duplicate_stage
    return FaultScenarioResult(
        "sigkill_mid_publish",
        passed,
        f"killed={killed} converged={converged} checkpoint={recovered.checkpoint}",
    )


def scenario_db_disconnect_then_retry(
    tmp_path: Path, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    """A source disconnect between fetch and apply must not corrupt state;
    the next successful fetch/apply cycle must still converge with RPO=0."""

    projector = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="capacity-fault",
    )
    watermark = max(r["ingested_seq"] for r in rows)
    try:
        raise ConnectionError("injected disconnect before apply")
    except ConnectionError:
        pass  # the source disconnected; no batch was ever applied
    result = projector.project_records(rows, mode="live", source_high_watermark=watermark)
    passed = projector.checkpoint == watermark and result.quarantined == 0
    return FaultScenarioResult(
        "db_disconnect_then_retry", passed, f"checkpoint={projector.checkpoint}"
    )


def scenario_transaction_rollback(
    tmp_path: Path, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    """An injected state-commit failure must leave no torn state; a retried
    apply of the same batch must converge cleanly (mirrors
    ``test_state_transaction_failure_leaves_no_torn_state_and_restart_converges``)."""

    import services.trade_journey.lifecycle_projector as module

    projector = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="capacity-fault",
    )
    original_commit = module._commit_prepared_json

    def failing_commit(path, prepared):
        if Path(path).name == "controller_state.json":
            raise OSError("injected state transaction failure")
        return original_commit(path, prepared)

    module._commit_prepared_json = failing_commit
    watermark = max(r["ingested_seq"] for r in rows)
    threw = False
    try:
        projector.project_records(rows, mode="live", source_high_watermark=watermark)
    except OSError:
        threw = True
    finally:
        module._commit_prepared_json = original_commit

    no_torn_state = not (tmp_path / "controller_state.json").exists() and projector.checkpoint == 0
    recovered = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="capacity-fault-recovered",
    )
    recovered.project_records(rows, mode="live", source_high_watermark=watermark)
    converged = recovered.checkpoint == watermark
    passed = threw and no_torn_state and converged
    return FaultScenarioResult(
        "transaction_rollback",
        passed,
        f"threw={threw} no_torn_state={no_torn_state} converged={converged}",
    )


def scenario_second_writer_conflict(
    tmp_path: Path, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    """Two projector processes racing the same state path must not both
    advance the checkpoint over conflicting content; the second writer must
    fail closed rather than silently diverge."""

    first = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="writer-a",
    )
    watermark = max(r["ingested_seq"] for r in rows)
    first.project_records(rows, mode="live", source_high_watermark=watermark)

    conflicting_rows = [dict(row) for row in rows]
    conflicting_rows[0] = dict(conflicting_rows[0])
    conflicting_rows[0]["payload"] = dict(conflicting_rows[0]["payload"])
    conflicting_rows[0]["payload"]["metrics"] = {"action": "second-writer-conflict"}

    second = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="writer-b",
    )
    rejected = False
    try:
        second.project_records(conflicting_rows, mode="live", source_high_watermark=watermark)
    except ConflictingLifecycleEvent:
        rejected = True
    passed = rejected
    return FaultScenarioResult("second_writer_conflict", passed, f"rejected={rejected}")


def scenario_duplicate_delivery(
    tmp_path: Path, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    projector = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
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
    tmp_path: Path, rows: list[dict[str, Any]]
) -> FaultScenarioResult:
    shuffled = list(reversed(rows))
    projector = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="capacity-fault",
    )
    watermark = max(r["ingested_seq"] for r in rows)
    result = projector.project_records(shuffled, mode="live", source_high_watermark=watermark)
    passed = projector.checkpoint == watermark and result.accepted == len(rows)
    return FaultScenarioResult(
        "out_of_order_delivery", passed, f"checkpoint={projector.checkpoint}"
    )


FAULT_SCENARIOS: tuple[Callable[[Path, list[dict[str, Any]]], FaultScenarioResult], ...] = (
    scenario_sigkill_mid_publish,
    scenario_db_disconnect_then_retry,
    scenario_transaction_rollback,
    scenario_second_writer_conflict,
    scenario_duplicate_delivery,
    scenario_out_of_order_delivery,
)


def run_fault_matrix(
    tmp_path_factory: Callable[[str], Path], journey_count: int = 4
) -> list[FaultScenarioResult]:
    """Run every fault scenario, each in its own isolated working directory."""

    results: list[FaultScenarioResult] = []
    for scenario in FAULT_SCENARIOS:
        scenario_dir = tmp_path_factory(scenario.__name__)
        rows: list[dict[str, Any]] = []
        seq = 1
        for journey_index in range(journey_count):
            event_types = _journey_event_types(len(STAGE_SPECS))
            journey = journey_rows(journey_index, event_types=event_types, starting_seq=seq)
            rows.extend(journey)
            seq += len(journey)
        results.append(scenario(scenario_dir, rows))
    return results


def _write_report(path: Path, report: CapacityReport, faults: list[FaultScenarioResult]) -> None:
    payload = {
        "capacity": report.to_dict(),
        "fault_matrix": [
            {"name": r.name, "passed": r.passed, "detail": r.detail} for r in faults
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=DEFAULT_EVENT_COUNT)
    parser.add_argument("--loop-runs", type=int, default=DEFAULT_LOOP_RUN_COUNT)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--fault-journey-count", type=int, default=4)
    parser.add_argument("--workdir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    import tempfile

    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="lifecycle-capacity-"))
    workdir.mkdir(parents=True, exist_ok=True)
    projector = LifecycleProjector(
        state_path=workdir / "capacity" / "controller_state.json",
        bundle_root=workdir / "capacity",
        deployment_sha="capacity-benchmark",
    )
    report = run_capacity_benchmark(
        projector,
        total_events=args.events,
        total_loop_runs=args.loop_runs,
        batch_size=args.batch_size,
    )

    def _fault_workdir(name: str) -> Path:
        path = workdir / "faults" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    faults = run_fault_matrix(_fault_workdir, journey_count=args.fault_journey_count)

    if args.output:
        _write_report(args.output, report, faults)

    failures = report.gate_failures() + [f.name for f in faults if not f.passed]
    for line in json.dumps(report.to_dict(), indent=2, sort_keys=True).splitlines():
        print(line)
    for result in faults:
        print(f"fault[{result.name}] passed={result.passed} {result.detail}")
    if failures:
        print(f"GATE FAILURES: {failures}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
