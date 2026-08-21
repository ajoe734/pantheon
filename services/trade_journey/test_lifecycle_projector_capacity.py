"""Focused tests for the LIFECYCLE-PROJ-CAPACITY-001 harness.

These tests run the harness at a small, fast scale to prove the corpus
generator, RSS/latency instrumentation and fault matrix are each correct.
They do not attempt the full 1,000,000-event / 150,000-loop-run gates from
section 14: that run is multi-minute and host-load-sensitive, and must be
driven through the ``lifecycle_projector_capacity`` CLI on the documented
dev profile when the host is quiet, not from the unit test suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.trade_journey.lifecycle_projector import LifecycleProjector
from services.trade_journey.lifecycle_projector_capacity import (
    CapacityReport,
    FAULT_SCENARIOS,
    _journey_event_budgets,
    _journey_event_types,
    generate_corpus_batches,
    journey_rows,
    main,
    run_capacity_benchmark,
    run_fault_matrix,
    rss_bytes,
)


def _projector(tmp_path: Path) -> LifecycleProjector:
    return LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="capacity-test",
    )


def test_journey_event_budgets_hits_exact_totals():
    budgets = _journey_event_budgets(2_000, 300)
    assert sum(budgets) == 2_000
    assert len(budgets) == 300
    assert all(2 <= b <= 8 for b in budgets)


def test_journey_event_budgets_rejects_impossible_targets():
    with pytest.raises(ValueError):
        _journey_event_budgets(100, 100)  # needs >= 2 events per journey
    with pytest.raises(ValueError):
        _journey_event_budgets(100, 0)


def test_journey_event_types_always_closes_with_reconciliation():
    for budget in range(1, 12):
        stages = _journey_event_types(budget)
        assert stages[-1] == "reconciliation_completed"
        assert 2 <= len(stages) <= 8
        assert len(stages) == len(set(stages))


def test_journey_rows_are_unique_and_monotonic_across_journeys():
    first = journey_rows(0, event_types=_journey_event_types(8), starting_seq=1)
    second = journey_rows(1, event_types=_journey_event_types(8), starting_seq=9)

    assert [row["ingested_seq"] for row in first] == list(range(1, 9))
    assert [row["ingested_seq"] for row in second] == list(range(9, 17))

    first_journey_ids = {row["payload"]["correlation_envelope"]["journey_id"] for row in first}
    second_journey_ids = {row["payload"]["correlation_envelope"]["journey_id"] for row in second}
    assert first_journey_ids.isdisjoint(second_journey_ids)

    first_event_ids = {row["event_id"] for row in first}
    second_event_ids = {row["event_id"] for row in second}
    assert first_event_ids.isdisjoint(second_event_ids)


def test_generate_corpus_batches_covers_exact_scale_in_seq_order():
    batches = list(
        generate_corpus_batches(2_000, 300, batch_size=100, tenant_id="tenant-x", environment="paper")
    )
    all_rows = [row for batch in batches for row in batch]
    assert len(all_rows) == 2_000
    seqs = [row["ingested_seq"] for row in all_rows]
    assert seqs == sorted(seqs)
    assert seqs == list(range(1, 2_001))
    assert all(len(batch) <= 100 for batch in batches)

    completed_loop_runs = sum(
        1 for row in all_rows if row["event_type"] == "reconciliation_completed"
    )
    assert completed_loop_runs == 300


def test_rss_bytes_is_positive():
    assert rss_bytes() > 0


def test_cli_refuses_to_measure_the_legacy_json_projector():
    with pytest.raises(SystemExit, match="2"):
        main(["--events", "2", "--loop-runs", "1"])


def test_run_capacity_benchmark_projects_full_small_scale_corpus(tmp_path):
    projector = _projector(tmp_path)
    report = run_capacity_benchmark(
        projector,
        total_events=2_000,
        total_loop_runs=300,
        batch_size=100,
    )

    assert isinstance(report, CapacityReport)
    assert len(report.samples) == 20
    assert projector.checkpoint == 2_000
    assert report.peak_rss_bytes >= report.steady_rss_bytes > 0
    assert report.batch_latency_p95_seconds >= 0
    # Small-scale runs are nowhere near the section 14 ceilings.
    assert report.gate_failures() == []

    summary = report.to_dict()
    assert summary["total_events"] == 2_000
    assert summary["total_loop_runs"] == 300


def test_capacity_report_flags_gate_violations_without_a_full_scale_run():
    report = CapacityReport(total_events=1_000_000, total_loop_runs=150_000, batch_size=500)
    from services.trade_journey.lifecycle_projector_capacity import BatchSample

    # Synthesize an over-budget steady/peak RSS and slow batch latency so
    # gate_failures() can be proven without actually allocating gigabytes or
    # running the full corpus.
    report.samples = [
        BatchSample(
            batch_index=0,
            events_applied=500_000,
            checkpoint=500_000,
            latency_seconds=1.0,
            rss_bytes=int(1.0 * (1024 ** 3)),
            backlog_age_seconds=0.0,
        ),
        BatchSample(
            batch_index=1,
            events_applied=500_000,
            checkpoint=1_000_000,
            latency_seconds=6.0,
            rss_bytes=int(2.6 * (1024 ** 3)),
            backlog_age_seconds=0.0,
        ),
    ]
    failures = report.gate_failures()
    assert any("peak RSS" in f for f in failures)
    assert any("batch latency" in f for f in failures)
    assert any("RSS slope" in f for f in failures)


@pytest.mark.parametrize("scenario", FAULT_SCENARIOS, ids=lambda s: s.__name__)
def test_fault_scenario_passes_in_isolation(tmp_path, scenario):
    rows = []
    seq = 1
    for journey_index in range(3):
        stages = _journey_event_types(8)
        journey = journey_rows(journey_index, event_types=stages, starting_seq=seq)
        rows.extend(journey)
        seq += len(journey)

    result = scenario(tmp_path, rows)
    assert result.passed, f"{result.name} failed: {result.detail}"


def test_run_fault_matrix_runs_every_scenario_in_isolated_dirs(tmp_path):
    dirs: dict[str, Path] = {}

    def factory(name: str) -> Path:
        path = tmp_path / name
        path.mkdir(parents=True, exist_ok=True)
        dirs[name] = path
        return path

    results = run_fault_matrix(factory, journey_count=2)

    assert len(results) == len(FAULT_SCENARIOS)
    assert {r.name for r in results} == {
        s.__name__.removeprefix("scenario_") for s in FAULT_SCENARIOS
    }
    assert all(r.passed for r in results), [
        (r.name, r.detail) for r in results if not r.passed
    ]
    # Every scenario got its own working directory; a shared directory would
    # let one scenario's leftover state leak into the next.
    assert len(dirs) == len(FAULT_SCENARIOS)
