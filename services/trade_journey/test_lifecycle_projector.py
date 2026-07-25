from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import types
import uuid

import pytest

from services.trade_journey.correlation_envelope import (
    mint_trade_envelope,
    propagate_envelope,
)
from services.trade_journey.lifecycle_projector import (
    AtomicProjectionBundle,
    ConflictingLifecycleEvent,
    DEFAULT_HEALTH_MAX_AGE_SECONDS,
    LIFECYCLE_EVENT_TYPE_QUERY,
    LifecycleProjector,
    PostgresLifecycleSource,
    _record_worker_failure,
    projector_readiness,
)


IDENTITY = {
    "tenant_id": "tenant-a",
    "environment": "paper",
    "run_id": "run-paper-001",
    "signal_id": "signal-paper-001",
    "strategy_id": "strategy-paper-001",
    "runtime_id": "runtime-paper-001",
    "binding_id": "10000000-0000-0000-0000-000000000001",
    "capital_pool_id": "pool-paper-001",
    "persona_id": "persona-paper-001",
    "persona_capital_binding_id": "pcb-paper-001",
    "artifact_id": "artifact-paper-001",
    "artifact_version": "1.2.3",
    "plan_id": "plan-paper-001",
    "trace_id": "20000000-0000-0000-0000-000000000001",
}


def _uuid(number: int) -> str:
    return str(uuid.UUID(int=number))


def lifecycle_rows() -> list[dict]:
    specs = [
        ("signal_generation", 1),
        ("trade_decision", 2),
        ("risk_evaluation", 3),
        ("order_submitted", 4),
        ("order_accepted", 5),
        ("paper_fill_simulated", 6),
        ("position_snapshot", 7),
        ("reconciliation_completed", 8),
    ]
    rows: list[dict] = []
    envelope = None
    for offset, (event_type, sequence_no) in enumerate(specs, start=1):
        event_id = _uuid(offset)
        created_at = f"2026-07-15T00:00:{sequence_no:02d}Z"
        if envelope is None:
            envelope = mint_trade_envelope(
                {
                    "tenant_id": IDENTITY["tenant_id"],
                    "environment": IDENTITY["environment"],
                    "trace_id": IDENTITY["trace_id"],
                },
                producer="execution.signal-decision",
                event_id=event_id,
                journey_id="tj-paper-001",
                now=created_at,
            )
        else:
            envelope = propagate_envelope(
                envelope,
                producer="test.lifecycle",
                event_id=event_id,
                event_time=created_at,
            )
        metadata = {
            "run_id": IDENTITY["run_id"],
            "signal_id": IDENTITY["signal_id"],
            "persona_id": IDENTITY["persona_id"],
            "sequence_no": sequence_no,
            "causal_parent_id": envelope["causation_event_id"],
            "decision_id": "decision-paper-001",
            "client_order_id": "client-order-paper-001",
            "order_id": "order-paper-001",
            "reconciliation_id": "reconciliation-paper-001",
        }
        payload = {
            "event_id": event_id,
            "event_type": event_type,
            "created_at": created_at,
            "execution_mode": "paper",
            "environment": IDENTITY["environment"],
            "deployment_stage": IDENTITY["environment"],
            "binding_id": IDENTITY["binding_id"],
            "runtime_id": IDENTITY["runtime_id"],
            "capital_pool_id": IDENTITY["capital_pool_id"],
            "artifact_id": IDENTITY["artifact_id"],
            "artifact_version": IDENTITY["artifact_version"],
            "plan_id": IDENTITY["plan_id"],
            "persona_capital_binding_id": IDENTITY["persona_capital_binding_id"],
            "run_id": IDENTITY["run_id"],
            "signal_id": IDENTITY["signal_id"],
            "trace_id": IDENTITY["trace_id"],
            "authority_refs": {"persona_id": IDENTITY["persona_id"]},
            "target": {"strategy_id": IDENTITY["strategy_id"]},
            "metrics": {"action": event_type},
            "metadata": metadata,
            "correlation_envelope": envelope,
        }
        if event_type == "paper_fill_simulated":
            payload["metrics"] = {"fill_quantity": 3, "fill_price": 101.5}
        if event_type == "position_snapshot":
            payload["position_qty"] = 3
        rows.append(
            {
                "ingested_seq": offset,
                "ingested_at": f"2026-07-15T00:01:{offset:02d}Z",
                "event_id": event_id,
                "event_type": event_type,
                "created_at": created_at,
                "payload": payload,
            }
        )
    return rows


def _projector(tmp_path: Path, **kwargs) -> LifecycleProjector:
    return LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="deadbeef",
        **kwargs,
    )


def _current_json(tmp_path: Path, filename: str) -> dict:
    return json.loads((tmp_path / "current" / filename).read_text(encoding="utf-8"))


def test_postgres_lifecycle_source_filters_watermark_and_fetch(monkeypatch):
    calls: list[tuple] = []

    class Connection:
        async def fetchval(self, query: str, event_types: list[str]) -> int:
            calls.append(("fetchval", query, tuple(event_types)))
            return 44

        async def fetch(
            self,
            query: str,
            checkpoint: int,
            event_types: list[str],
            limit: int,
        ) -> list[dict]:
            calls.append(("fetch", query, checkpoint, tuple(event_types), limit))
            return []

        async def close(self) -> None:
            calls.append(("close",))

    async def connect(dsn: str) -> Connection:
        calls.append(("connect", dsn))
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    source = PostgresLifecycleSource("postgresql://unit")

    assert asyncio.run(source.high_watermark()) == 44
    assert asyncio.run(source.fetch_after(7, limit=9)) == []

    fetchval = next(call for call in calls if call[0] == "fetchval")
    fetch = next(call for call in calls if call[0] == "fetch")
    assert "event_type = ANY" in fetchval[1]
    assert "event_type = ANY" in fetch[1]
    assert fetchval[2] == LIFECYCLE_EVENT_TYPE_QUERY
    assert fetch[2] == 7
    assert fetch[3] == LIFECYCLE_EVENT_TYPE_QUERY
    assert fetch[4] == 9
    assert "heartbeat" not in fetchval[2]


def test_full_canonical_lifecycle_projects_one_identity_consistent_journey_and_loop(tmp_path):
    projector = _projector(tmp_path)
    result = projector.project_records(
        lifecycle_rows(), mode="live", source_high_watermark=8
    )

    journeys = _current_json(tmp_path, "trade_journey_events.json")
    loops = _current_json(tmp_path, "loop_runs.json")
    stages = [event["stage"] for event in journeys["events"]]
    assert stages == [
        "signal_generation",
        "trade_decision",
        "risk_evaluation",
        "order_submission",
        "broker_acknowledgement",
        "fill_management",
        "ledger_booking",
        "reconciliation",
    ]
    assert {event["journey_id"] for event in journeys["events"]} == {"tj-paper-001"}
    assert {event["loop_run_id"] for event in journeys["events"]} == {"lr-run-paper-001"}
    assert result.loop_run_count == 1
    loop = loops["records"]["lr-run-paper-001"]
    assert loop["journey_id"] == "tj-paper-001"
    assert loop["status"] == "completed"
    assert loop["canonical_event_count"] == 8
    assert loop["fill_event_count"] == 1
    assert loop["position_event_count"] == 1
    assert loop["reconciliation_event_count"] == 1
    assert loop["accepted_live"] is True
    assert loops["controller"]["truth_level"] == "canonical_live"
    assert loops["controller"]["checkpoint"] == 8


def test_paper_noop_order_does_not_duplicate_explicit_trade_decision_stage(tmp_path):
    rows = lifecycle_rows()[:4]
    rows[3]["event_type"] = "paper_order_simulated"
    rows[3]["payload"]["event_type"] = "paper_order_simulated"
    rows[3]["payload"]["metadata"]["order_status"] = "noop"

    _projector(tmp_path).project_records(
        rows,
        mode="live",
        source_high_watermark=4,
    )

    journey = _current_json(tmp_path, "trade_journey_events.json")
    stages = [event["stage"] for event in journey["events"]]
    assert stages == [
        "signal_generation",
        "trade_decision",
        "risk_evaluation",
        "order_submission",
    ]
    assert len(stages) == len(set(stages))
    assert journey["events"][-1]["stage_status"] == "skipped"


def test_exact_duplicate_is_idempotent_and_conflicting_duplicate_is_atomic(tmp_path):
    rows = lifecycle_rows()
    projector = _projector(tmp_path)
    projector.project_records(rows[:2], mode="live", source_high_watermark=2)
    before_state = json.loads((tmp_path / "controller_state.json").read_text())
    duplicate = projector.project_records(
        [rows[0]], mode="live", source_high_watermark=2
    )
    assert duplicate.duplicates == 1

    conflicting = json.loads(json.dumps(rows[0]))
    conflicting["payload"]["metrics"] = {"action": "changed"}
    with pytest.raises(ConflictingLifecycleEvent):
        projector.project_records(
            [conflicting], mode="live", source_high_watermark=2
        )
    after_state = json.loads((tmp_path / "controller_state.json").read_text())
    assert after_state["canonical_events"] == before_state["canonical_events"]


def test_out_of_order_aggregate_sequence_and_restart_converge(tmp_path):
    rows = lifecycle_rows()
    # Arrival offsets are monotonic, while aggregate sequence arrives 3, 1, 2.
    out_of_order = [rows[2], rows[0], rows[1]]
    for offset, row in enumerate(out_of_order, start=1):
        row["ingested_seq"] = offset
    first = _projector(tmp_path)
    first.project_records(out_of_order[:2], mode="recovery", source_high_watermark=3)

    restarted = _projector(tmp_path)
    restarted.project_records(out_of_order[2:], mode="recovery", source_high_watermark=3)
    journeys = _current_json(tmp_path, "trade_journey_events.json")
    assert [event["source_sequence_no"] for event in journeys["events"]] == [1, 2, 3]
    assert restarted.checkpoint == 3
    assert restarted.controller["restart_count"] == 2
    assert restarted.controller["accepted_live"] is False
    assert restarted.controller["truth_level"] == "recovery_only"

    restarted.record_poll(source_high_watermark=3, backlog=0, mode="live")
    assert restarted.controller["accepted_live"] is True
    assert restarted.controller["truth_level"] == "canonical_live"
    assert restarted.controller["status"] == "ready"
    published_controller = _current_json(tmp_path, "loop_runs.json")["controller"]
    assert published_controller["mode"] == "live"
    assert published_controller["status"] == "ready"
    assert published_controller["accepted_live"] is True


def test_restart_publishes_new_deployment_sha_without_new_events(tmp_path):
    first = _projector(tmp_path)
    first.project_records(
        lifecycle_rows()[:1], mode="live", source_high_watermark=1
    )
    assert _current_json(tmp_path, "loop_runs.json")["controller"]["deployment_sha"] == "deadbeef"

    restarted = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="feedface",
    )
    restarted.record_poll(source_high_watermark=1, backlog=0, mode="live")

    published_controller = _current_json(tmp_path, "loop_runs.json")["controller"]
    assert published_controller["deployment_sha"] == "feedface"
    assert published_controller["status"] == "ready"
    assert published_controller["accepted_live"] is True


def test_backfill_and_replay_never_advance_live_freshness(tmp_path):
    rows = lifecycle_rows()
    projector = _projector(tmp_path)
    projector.project_records(rows[:4], mode="backfill")
    controller = projector.controller
    assert projector.checkpoint == 0
    assert controller["accepted_live"] is False
    assert controller["last_live_success_at"] is None
    assert controller["truth_level"] == "backfill_only"
    assert _current_json(tmp_path, "loop_runs.json")["records"]["lr-run-paper-001"]["accepted_live"] is False

    projector.project_records(rows[4:5], mode="replay")
    assert projector.controller["last_live_success_at"] is None
    assert projector.controller["truth_level"] == "replay_only"

    live_row = rows[5]
    live_row["ingested_seq"] = 1
    projector.project_records([live_row], mode="live", source_high_watermark=1)
    assert projector.controller["accepted_live"] is True
    assert projector.controller["last_live_success_at"] is not None

    last_live_success_at = projector.controller["last_live_success_at"]
    projector.project_records(rows[6:7], mode="backfill")
    assert projector.controller["mode"] == "backfill"
    assert projector.controller["accepted_live"] is False
    assert projector.controller["truth_level"] == "backfill_with_historic_live"
    assert projector.controller["status"] == "repair_only"
    assert projector.controller["last_live_success_at"] == last_live_success_at


def test_missing_identity_is_quarantined_and_cursor_progresses(tmp_path):
    row = lifecycle_rows()[0]
    del row["payload"]["run_id"]
    del row["payload"]["metadata"]["run_id"]
    projector = _projector(tmp_path)
    result = projector.project_records([row], mode="live", source_high_watermark=1)
    assert result.accepted == 0
    assert result.quarantined == 1
    assert projector.checkpoint == 1
    assert projector.controller["status"] == "degraded"
    assert projector.controller["accepted_live"] is False


def test_bundle_failure_never_switches_partial_generation_or_checkpoint(tmp_path):
    def fail_before_switch(_path: Path) -> None:
        raise OSError("injected switch failure")

    publisher = AtomicProjectionBundle(tmp_path, before_switch=fail_before_switch)
    projector = _projector(tmp_path, publisher=publisher)
    with pytest.raises(OSError, match="injected switch failure"):
        projector.project_records(
            lifecycle_rows()[:1], mode="live", source_high_watermark=1
        )
    assert not (tmp_path / "current").exists()
    assert not (tmp_path / "controller_state.json").exists()
    assert not (tmp_path / "health_state.json").exists()
    assert projector.checkpoint == 0

    recovered = _projector(tmp_path)
    recovered.project_records(
        lifecycle_rows()[:1], mode="recovery", source_high_watermark=1
    )
    assert (tmp_path / "current" / "manifest.json").is_file()
    assert recovered.checkpoint == 1


def test_health_snapshot_stays_with_current_during_atomic_generation_switch(tmp_path):
    projector = _projector(tmp_path)
    projector.project_records(
        lifecycle_rows()[:1], mode="live", source_high_watermark=1
    )
    health_path = tmp_path / "health_state.json"
    health = json.loads(health_path.read_text(encoding="utf-8"))
    assert health["generation"] == 1
    assert "canonical_events" not in health

    observations: list[dict] = []

    def observe_before_switch(_path: Path) -> None:
        observations.append(
            projector_readiness(
                state_path=health_path,
                bundle_root=tmp_path,
                max_age_seconds=30,
                max_backlog=0,
                min_free_bytes=0,
                min_free_percent=0,
                now=datetime(2026, 7, 15, 0, 1, 2, tzinfo=timezone.utc),
            )
        )

    projector.bundle = AtomicProjectionBundle(
        tmp_path,
        before_switch=observe_before_switch,
    )
    projector.project_records(
        lifecycle_rows()[1:2], mode="live", source_high_watermark=2
    )

    assert len(observations) == 1
    assert observations[0]["ready"] is True
    assert observations[0]["current_generation"] == 1
    assert observations[0]["controller_generation"] == 1
    after = projector_readiness(
        state_path=health_path,
        bundle_root=tmp_path,
        max_age_seconds=30,
        max_backlog=0,
        min_free_bytes=0,
        min_free_percent=0,
        now=datetime(2026, 7, 15, 0, 1, 2, tzinfo=timezone.utc),
    )
    assert after["ready"] is True
    assert after["current_generation"] == 2
    assert after["controller_generation"] == 2


def test_source_failure_preserves_last_good_bundle(tmp_path):
    projector = _projector(tmp_path)
    projector.project_records(
        lifecycle_rows()[:1], mode="live", source_high_watermark=1
    )
    before = _current_json(tmp_path, "trade_journey_events.json")["events"]
    projector.record_source_failure("postgres unavailable", backlog=7)
    assert _current_json(tmp_path, "trade_journey_events.json")["events"] == before
    assert _current_json(tmp_path, "loop_runs.json")["controller"]["status"] == "degraded"
    assert projector.controller["status"] == "degraded"
    assert projector.controller["last_error"] == "postgres unavailable"
    assert projector.controller["backlog"] == 7

    projector.record_poll(source_high_watermark=1, backlog=0, mode="live")
    recovered = _current_json(tmp_path, "trade_journey_events.json")
    assert recovered["events"] == before
    assert recovered["controller"]["status"] == "ready"
    assert recovered["controller"]["last_error"] is None


def test_generation_retention_is_bounded_and_never_removes_active_generation(tmp_path):
    bundle = AtomicProjectionBundle(tmp_path, generation_retention=3)
    for generation in range(1, 7):
        bundle.publish(
            generation,
            {"schema_version": "journey-test", "events": []},
            {"schema_version": "loop-test", "records": {}},
        )

    generation_names = {
        path.name
        for path in (tmp_path / "generations").iterdir()
        if path.is_dir()
    }
    active_name = (tmp_path / "current").resolve().name
    assert len(generation_names) == 3
    assert active_name in generation_names
    assert json.loads((tmp_path / "current" / "manifest.json").read_text())["generation"] == 6


def test_publish_finishes_retention_cleanup_before_switching_current(tmp_path, monkeypatch):
    bundle = AtomicProjectionBundle(tmp_path, generation_retention=2)
    bundle.publish(
        1,
        {"schema_version": "journey-test", "events": []},
        {"schema_version": "loop-test", "records": {}},
    )
    previous_active = (tmp_path / "current").resolve().name
    observed: list[tuple[bool, str]] = []
    original_maintain = bundle.maintain

    def observe_maintain(*, reserve_for_publish: bool = False):
        observed.append((reserve_for_publish, (tmp_path / "current").resolve().name))
        return original_maintain(reserve_for_publish=reserve_for_publish)

    monkeypatch.setattr(bundle, "maintain", observe_maintain)
    bundle.publish(
        2,
        {"schema_version": "journey-test", "events": []},
        {"schema_version": "loop-test", "records": {}},
    )

    assert observed == [(True, previous_active)]
    assert (tmp_path / "current").resolve().name != previous_active


def test_retention_preserves_old_active_generation_and_cleans_only_abandoned_staging(tmp_path):
    generations = tmp_path / "generations"
    generations.mkdir()
    for generation in range(1, 5):
        (generations / f"g{generation:012d}-{generation:012x}").mkdir()
    os.symlink("generations/g000000000001-000000000001", tmp_path / "current")

    abandoned = generations / ".g000000000005-000000000005.tmp"
    recent = generations / ".g000000000006-000000000006.tmp"
    unowned_generation = generations / "g000000000007-operator-note"
    unowned_staging = generations / ".g000000000008-operator-note.tmp"
    abandoned.mkdir()
    recent.mkdir()
    unowned_generation.mkdir()
    unowned_staging.mkdir()
    os.utime(abandoned, (1, 1))
    os.utime(recent, (95, 95))
    os.utime(unowned_staging, (1, 1))

    bundle = AtomicProjectionBundle(
        tmp_path,
        generation_retention=2,
        staging_max_age_seconds=10,
        epoch_clock=lambda: 100,
    )
    report = bundle.maintain()

    generation_names = {
        path.name
        for path in generations.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
    assert generation_names == {
        "g000000000001-000000000001",
        "g000000000004-000000000004",
        "g000000000007-operator-note",
    }
    assert (tmp_path / "current").resolve().name == "g000000000001-000000000001"
    assert not abandoned.exists()
    assert recent.exists()
    assert unowned_staging.exists()
    assert report["active_generation"] == "g000000000001-000000000001"
    assert report["abandoned_staging"] == [abandoned.name]


def test_enospc_during_projection_and_error_publication_does_not_escape_worker_loop(tmp_path):
    def fail_with_enospc(_path: Path) -> None:
        raise OSError(28, "No space left on device")

    projector = _projector(
        tmp_path,
        publisher=AtomicProjectionBundle(
            tmp_path,
            before_switch=fail_with_enospc,
            generation_retention=2,
            staging_max_age_seconds=0,
        ),
    )
    with pytest.raises(OSError, match="No space left on device") as failure:
        projector.project_records(
            lifecycle_rows()[:1], mode="live", source_high_watermark=1
        )

    assert _record_worker_failure(projector, failure.value) is False
    assert projector.checkpoint == 0
    assert projector.controller["status"] == "degraded"
    assert "No space left on device" in projector.controller["last_error"]
    assert "No space left on device" in projector.controller["error_publication_failure"]
    assert not (tmp_path / "controller_state.json").exists()
    assert len(
        [
            path
            for path in (tmp_path / "generations").iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ]
    ) <= 2


def test_transient_enospc_converges_without_losing_checkpoint_or_active_generation(
    tmp_path,
):
    attempts = 0

    def fail_twice(_path: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise OSError(28, "No space left on device")

    projector = _projector(
        tmp_path,
        publisher=AtomicProjectionBundle(
            tmp_path,
            before_switch=fail_twice,
            generation_retention=2,
            staging_max_age_seconds=0,
        ),
    )
    with pytest.raises(OSError) as failure:
        projector.project_records(
            lifecycle_rows()[:1], mode="live", source_high_watermark=1
        )
    assert _record_worker_failure(projector, failure.value) is False

    recovered = projector.project_records(
        lifecycle_rows()[:1], mode="live", source_high_watermark=1
    )

    assert recovered.checkpoint == 1
    assert projector.checkpoint == 1
    assert projector.controller["status"] == "ready"
    assert projector.controller["last_error"] is None
    assert (tmp_path / "current" / "manifest.json").is_file()
    assert json.loads((tmp_path / "current" / "manifest.json").read_text())[
        "generation"
    ] == recovered.generation


def test_repeated_identical_source_failure_does_not_publish_unbounded_generations(tmp_path):
    timestamps = iter(
        [
            "2026-07-22T00:00:00Z",
            "2026-07-22T00:00:01Z",
            "2026-07-22T00:00:02Z",
        ]
    )
    projector = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="deadbeef",
        clock=lambda: next(timestamps),
    )
    projector.project_records(
        lifecycle_rows()[:1], mode="live", source_high_watermark=1
    )
    projector.record_source_failure("postgres unavailable", backlog=7)
    failure_generation = projector.controller["generation"]
    generation_count = len(list((tmp_path / "generations").iterdir()))

    projector.record_source_failure("postgres unavailable", backlog=7)

    assert projector.controller["generation"] == failure_generation
    assert len(list((tmp_path / "generations").iterdir())) == generation_count
    assert projector.controller["last_failure_at"] == "2026-07-22T00:00:02Z"


def test_projector_readiness_exposes_freshness_generation_watermark_and_disk(tmp_path):
    observed_at = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    projector = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="deadbeef",
        clock=lambda: "2026-07-22T12:00:00Z",
    )
    projector.project_records(
        lifecycle_rows()[:1], mode="live", source_high_watermark=1
    )

    healthy = projector_readiness(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        max_age_seconds=30,
        max_backlog=0,
        min_free_bytes=0,
        min_free_percent=0,
        now=observed_at,
    )
    assert healthy["ready"] is True
    assert healthy["worker_status"] == "ready"
    assert healthy["current_generation"] == healthy["controller_generation"] == 1
    assert healthy["source_high_watermark"] == 1
    assert healthy["last_successful_publish_at"] == "2026-07-22T12:00:00Z"

    stale = projector_readiness(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        max_age_seconds=30,
        min_free_bytes=0,
        min_free_percent=0,
        now=datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc),
    )
    assert stale["ready"] is False
    assert stale["worker_status"] == "stale"
    assert stale["stale_reason"].startswith("last_poll_stale:")

    low_disk = projector_readiness(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        max_age_seconds=30,
        min_free_bytes=10**30,
        min_free_percent=0,
        now=observed_at,
    )
    assert low_disk["ready"] is False
    assert low_disk["disk"]["low"] is True
    assert any(reason.startswith("disk_below_policy:") for reason in low_disk["reasons"])


def test_default_freshness_window_covers_observed_large_volume_poll(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS", raising=False)
    projector = LifecycleProjector(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        deployment_sha="deadbeef",
        clock=lambda: "2026-07-22T12:00:00Z",
    )
    projector.project_records(
        lifecycle_rows()[:1], mode="live", source_high_watermark=1
    )

    observed = projector_readiness(
        state_path=tmp_path / "controller_state.json",
        bundle_root=tmp_path,
        max_backlog=0,
        min_free_bytes=0,
        min_free_percent=0,
        now=datetime(2026, 7, 22, 12, 1, 1, tzinfo=timezone.utc),
    )

    assert DEFAULT_HEALTH_MAX_AGE_SECONDS == 120.0
    assert observed["freshness"]["max_age_seconds"] == 120.0
    assert observed["freshness"]["age_seconds"] == 61.0
    assert observed["ready"] is True
