from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import types
import uuid

import pytest

from services.trade_journey.correlation_envelope import (
    mint_trade_envelope,
    propagate_envelope,
)
from services.trade_journey import lifecycle_projector as lifecycle_projector_module
from services.trade_journey.lifecycle_projector import (
    AtomicProjectionBundle,
    ConflictingLifecycleEvent,
    DEFAULT_HEALTH_MAX_AGE_SECONDS,
    LIFECYCLE_EVENT_TYPES,
    LIFECYCLE_EVENT_TYPE_QUERY,
    LifecycleProjector,
    PostgresLifecycleSource,
    _fingerprint,
    _record_worker_failure,
    projector_readiness,
)
from services.trade_journey.materializer import JourneyMaterializer


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
            calls.append(("fetchval", query, event_types))
            return 44

        async def fetch(
            self,
            query: str,
            checkpoint: int,
            event_types: list[str],
            limit: int,
        ) -> list[dict]:
            calls.append(("fetch", query, checkpoint, event_types, limit))
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
    assert "MAX(ingested_seq)" in fetchval[1]
    assert tuple(fetchval[2]) == LIFECYCLE_EVENT_TYPE_QUERY
    assert fetch[2] == 7
    assert tuple(fetch[3]) == LIFECYCLE_EVENT_TYPE_QUERY
    assert fetch[4] == 9


def test_postgres_lifecycle_source_startup_check_is_read_only_and_bounded(monkeypatch):
    calls: list[tuple] = []

    class Connection:
        async def fetchrow(self, query: str) -> None:
            calls.append(("fetchrow", query))
            return None

        async def close(self) -> None:
            calls.append(("close",))

    async def connect(dsn: str) -> Connection:
        calls.append(("connect", dsn))
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))

    asyncio.run(PostgresLifecycleSource("postgresql://unit").verify_read_contract())

    assert calls[0] == ("connect", "postgresql://unit")
    assert calls[1] == (
        "fetchrow",
        "SELECT ingested_seq, ingested_at FROM telemetry_events LIMIT 1",
    )
    assert calls[2] == ("close",)
    assert all("ALTER TABLE" not in str(call) and "UPDATE telemetry_events" not in str(call) for call in calls)


def test_postgres_lifecycle_source_startup_close_cannot_extend_deadline(monkeypatch):
    calls: list[str] = []

    class Connection:
        async def fetchrow(self, query: str) -> None:
            calls.append("fetchrow")
            return None

        async def close(self) -> None:
            calls.append("close")
            await asyncio.Event().wait()

        def terminate(self) -> None:
            calls.append("terminate")

    async def connect(dsn: str) -> Connection:
        calls.append("connect")
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    monkeypatch.setattr(lifecycle_projector_module, "DEFAULT_SOURCE_STARTUP_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(TimeoutError):
        asyncio.run(PostgresLifecycleSource("postgresql://unit").verify_read_contract())

    assert calls == ["connect", "fetchrow", "close", "terminate"]


def test_worker_startup_verifies_read_contract_before_publishing_identity(monkeypatch, tmp_path):
    calls: list[str] = []

    class Source:
        async def verify_read_contract(self) -> None:
            calls.append("verify")

        async def high_watermark(self) -> int:
            calls.append("high")
            return 0

        async def start_listener(self) -> None:
            calls.append("listen")

        async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict]:
            calls.append("fetch")
            return []

        async def wait(self, timeout: float) -> None:
            calls.append("wait")

        async def close(self) -> None:
            calls.append("close")

    source = Source()
    monkeypatch.setattr(lifecycle_projector_module, "PostgresLifecycleSource", lambda _: source)
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv("LIFECYCLE_PROJECTION_ROOT", str(tmp_path))
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "1")
    monkeypatch.setenv("GIT_SHA", "cafebabecafebabecafebabecafebabecafebabe")

    assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
    assert calls == ["verify", "high", "listen", "high", "fetch", "close"]
    assert _current_json(tmp_path, "loop_runs.json")["controller"]["deployment_sha"] == (
        "cafebabecafebabecafebabecafebabecafebabe"
    )


def test_worker_reaffirms_retained_snapshot_after_source_window_truncates(
    monkeypatch, tmp_path
):
    prior = _projector(tmp_path)
    row = lifecycle_rows()[0]
    row["ingested_seq"] = 6_099_223
    prior.project_records([row], mode="live", source_high_watermark=6_099_223)

    class Source:
        async def verify_read_contract(self) -> None:
            return None

        async def high_watermark(self) -> int:
            # The source table is a retained window.  Its currently-empty
            # lifecycle subset must not invalidate the durable projection.
            return 0

        async def start_listener(self) -> None:
            return None

        async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict]:
            assert checkpoint == 6_099_223
            return []

        async def wait(self, timeout: float) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(lifecycle_projector_module, "PostgresLifecycleSource", lambda _: Source())
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv("LIFECYCLE_PROJECTION_ROOT", str(tmp_path))
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "1")
    monkeypatch.setenv("GIT_SHA", "feedfacefeedfacefeedfacefeedfacefeedface")

    assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
    controller = _current_json(tmp_path, "loop_runs.json")["controller"]
    assert controller["deployment_sha"] == "feedfacefeedfacefeedfacefeedfacefeedface"
    assert controller["checkpoint"] == 6_099_223
    assert controller["source_high_watermark"] == 0
    assert controller["backlog"] == 0
    assert controller["accepted_live"] is True


def test_worker_startup_contract_failure_publishes_degraded_health(monkeypatch, tmp_path):
    calls: list[str] = []

    class Source:
        async def verify_read_contract(self) -> None:
            calls.append("verify")
            raise TimeoutError("telemetry schema check timed out")

        async def high_watermark(self) -> int:
            raise AssertionError("worker must not read after startup contract failure")

        async def start_listener(self) -> None:
            raise AssertionError("worker must not listen after startup contract failure")

        async def fetch_after(self, checkpoint: int, *, limit: int) -> list[dict]:
            raise AssertionError("worker must not fetch after startup contract failure")

        async def wait(self, timeout: float) -> None:
            calls.append("wait")

        async def close(self) -> None:
            calls.append("close")

    source = Source()
    monkeypatch.setattr(lifecycle_projector_module, "PostgresLifecycleSource", lambda _: source)
    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://unit")
    monkeypatch.setenv("LIFECYCLE_PROJECTION_ROOT", str(tmp_path))
    monkeypatch.setenv("LIFECYCLE_PROJECTOR_MAX_TICKS", "1")
    monkeypatch.setenv("GIT_SHA", "cafebabecafebabecafebabecafebabecafebabe")

    assert asyncio.run(lifecycle_projector_module.run_worker()) == 0
    controller = _current_json(tmp_path, "loop_runs.json")["controller"]
    assert calls == ["verify", "close", "close"]
    assert controller["status"] == "degraded"
    assert controller["deployment_sha"] == "cafebabecafebabecafebabecafebabecafebabe"
    assert "TimeoutError: telemetry schema check timed out" == controller["last_error"]


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
    assert after_state["aggregates"] == before_state["aggregates"]
    assert len(before_state["aggregates"]["tj-paper-001"]["event_fingerprints"]) == 2


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


def test_malformed_source_envelope_is_quarantined_and_later_rows_project(tmp_path):
    rows = lifecycle_rows()
    malformed = copy.deepcopy(rows[0])
    malformed["payload"]["created_at"] = "2026-07-14T00:00:00Z"

    projector = _projector(tmp_path)
    result = projector.project_records(
        [malformed, rows[1]], mode="live", source_high_watermark=2
    )

    assert result.accepted == 1
    assert result.quarantined == 1
    assert projector.checkpoint == 2
    assert projector.controller["status"] == "degraded"
    assert projector.controller["accepted_live"] is False
    assert projector.state["quarantine"][-1]["ingested_seq"] == 1
    assert "created_at mismatch" in projector.state["quarantine"][-1]["reason"]

    projected_ids = {
        event["event_id"]
        for event in _current_json(tmp_path, "trade_journey_events.json")["events"]
    }
    assert any(event_id.startswith(rows[1]["event_id"]) for event_id in projected_ids)


def test_historic_live_evidence_never_overrides_current_quarantine_or_backlog(tmp_path):
    projector = _projector(tmp_path)
    projector.project_records(lifecycle_rows()[:1], mode="live", source_high_watermark=1)
    assert projector.controller["accepted_live"] is True

    invalid = lifecycle_rows()[1]
    del invalid["payload"]["run_id"]
    del invalid["payload"]["metadata"]["run_id"]
    projector.project_records([invalid], mode="live", source_high_watermark=2)
    assert projector.controller["accepted_live"] is False
    assert projector.controller["truth_level"] == "live_with_historic_live"
    assert projector.controller["status"] == "degraded"

    # A later empty poll cannot erase the durable quarantine or restore live
    # readiness.  It remains an explicit repair action, not a heartbeat side
    # effect.
    projector.record_poll(source_high_watermark=2, backlog=0, mode="live")
    assert projector.controller["accepted_live"] is False
    assert projector.controller["truth_level"] == "live_with_historic_live"
    assert projector.controller["status"] == "degraded"

    # Backlog independently revokes admission even if the prior projection was
    # healthy.
    clean = _projector(tmp_path / "backlog")
    clean.project_records(lifecycle_rows()[:1], mode="live", source_high_watermark=1)
    clean.record_poll(source_high_watermark=2, backlog=1, mode="live")
    assert clean.controller["accepted_live"] is False
    assert clean.controller["truth_level"] == "live_with_historic_live"


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


NOW = "2026-07-22T12:00:00Z"


def journey_rows(journey_id: str, *, id_base: int, seq_base: int) -> list[dict]:
    """Clone the canonical eight-event lifecycle onto a distinct journey."""
    rows: list[dict] = []
    for index, row in enumerate(lifecycle_rows()):
        clone = json.loads(json.dumps(row))
        event_id = _uuid(id_base + index)
        run_id = f"run-{journey_id}"
        clone["event_id"] = event_id
        clone["ingested_seq"] = seq_base + index + 1
        payload = clone["payload"]
        payload["event_id"] = event_id
        payload["run_id"] = run_id
        payload["metadata"]["run_id"] = run_id
        payload["correlation_envelope"] = dict(payload["correlation_envelope"])
        payload["correlation_envelope"]["journey_id"] = journey_id
        rows.append(clone)
    return rows


def _baseline_entry_sort_key(entry: dict) -> tuple[str, int, str, str]:
    identity = entry["identity"]
    event = entry["event"]
    return (
        str(identity.get("journey_id") or ""),
        int(entry.get("sequence_no") or 0),
        str(event.get("created_at") or ""),
        str(event.get("event_id") or ""),
    )


def _baseline_loop_record(lifecycle, materializer, controller):
    """Transcription of the pre-incremental full-rebuild loop-record rule."""
    ordered = sorted(lifecycle, key=_baseline_entry_sort_key)
    identity = dict(ordered[0]["identity"])
    journey_id = identity["journey_id"]
    projection = materializer.get(
        journey_id, tenant_id=identity["tenant_id"], environment=identity["environment"]
    )
    if projection is None:
        return None
    event_types = [entry["event"]["event_type"] for entry in ordered]
    source_modes = sorted({str(entry["source_mode"]) for entry in ordered})
    accepted_live = any(bool(entry.get("accepted_live")) for entry in ordered)
    status = projection.snapshot.get("status") or "open"
    return {
        "id": identity["loop_run_id"],
        "loop_run_id": identity["loop_run_id"],
        "journey_id": journey_id,
        "loop_type": "paper_execution",
        "status": "active" if status in {"open", "executing", "partially_filled"} else status,
        "activePeriod": {
            "start": projection.snapshot.get("created_at"),
            "end": projection.snapshot.get("updated_at")
            if status in {"completed", "completed_with_variance", "failed", "cancelled"}
            else None,
        },
        **identity,
        "source": "canonical_telemetry_lifecycle_projector",
        "source_modes": source_modes,
        "accepted_live": accepted_live,
        "projection_mode": "live" if accepted_live else "+".join(source_modes),
        "canonical_event_count": len(ordered),
        "fill_event_count": sum(
            event_type
            in {"paper_fill_simulated", "fill_received", "order_partially_filled", "order_filled"}
            for event_type in event_types
        ),
        "position_event_count": sum("position_snapshot" in event_type for event_type in event_types),
        "reconciliation_event_count": sum(
            event_type.startswith("reconciliation_") for event_type in event_types
        ),
        "last_canonical_event_id": ordered[-1]["event"]["event_id"],
        "last_source_offset": ordered[-1].get("ingested_seq"),
        "last_projected_at": controller.get("last_projection_success_at"),
        "controller_id": controller.get("controller_id"),
        "controller_generation": controller.get("generation"),
        "deployment_sha": controller.get("deployment_sha"),
    }


def _baseline_full_rebuild(batches, *, controller, ingested_at_default):
    """Full-rebuild reference derived from the raw source rows alone.

    This deliberately shares no state with the incremental reducer: it
    re-accumulates every canonical entry from the rows the projector was handed,
    re-derives every journey event, rebuilds one global ``JourneyMaterializer``,
    and recomputes every loop record.  That is the algorithm the bounded reducer
    replaced, so an equivalence assertion against it cannot be satisfied by the
    reducer simply agreeing with itself.
    """
    entries: dict[str, dict] = {}
    for mode, rows in batches:
        ordered_rows = sorted(
            rows,
            key=lambda row: (int(row.get("ingested_seq") or 0), str(row.get("event_id") or "")),
        )
        for row in ordered_rows:
            event = LifecycleProjector._source_event(row)
            if event["event_type"] not in LIFECYCLE_EVENT_TYPES:
                continue
            event_id = event["event_id"]
            fingerprint = _fingerprint(
                {
                    "event_id": event_id,
                    "event_type": event["event_type"],
                    "created_at": event["created_at"],
                    "payload": event,
                }
            )
            existing = entries.get(event_id)
            if existing is not None:
                assert existing["fingerprint"] == fingerprint
                if mode == "live" and existing["source_mode"] != "live":
                    existing["source_mode"] = "live"
                    existing["accepted_live"] = True
                continue
            entries[event_id] = {
                "fingerprint": fingerprint,
                "event": event,
                "identity": LifecycleProjector._identity(event),
                "sequence_no": LifecycleProjector._sequence_no(event),
                "ingested_seq": int(row.get("ingested_seq") or 0),
                "ingested_at": str(row.get("ingested_at") or ingested_at_default),
                "source_mode": mode,
                "accepted_live": mode == "live",
            }

    ordered = sorted(entries.values(), key=_baseline_entry_sort_key)
    journey_events: list[dict] = []
    for entry in ordered:
        journey_events.extend(LifecycleProjector._journey_events(entry))
    journey_events.sort(key=JourneyMaterializer._sort_key)
    materializer = JourneyMaterializer()
    materializer.rebuild(journey_events)

    grouped: dict[str, list[dict]] = {}
    for entry in ordered:
        grouped.setdefault(entry["identity"]["journey_id"], []).append(entry)
    records: dict[str, dict] = {}
    for _journey_id, lifecycle in sorted(grouped.items()):
        record = _baseline_loop_record(lifecycle, materializer, controller)
        if record:
            records[record["id"]] = record
    return journey_events, records


def test_reducer_output_matches_independent_full_rebuild_baseline(tmp_path):
    rows = lifecycle_rows()
    # Aggregate sequence 6, 2, 8 delivered in that arrival order.
    out_of_order: list[dict] = []
    for arrival, index in enumerate([5, 1, 7], start=1):
        clone = json.loads(json.dumps(rows[index]))
        clone["ingested_seq"] = arrival
        out_of_order.append(clone)
    second = journey_rows("tj-paper-002", id_base=200, seq_base=100)

    batches = [
        ("recovery", out_of_order),
        ("recovery", rows),
        ("live", list(reversed(second))),
        ("live", rows[:2]),
    ]
    projector = _projector(tmp_path, clock=lambda: NOW)
    for mode, batch in batches:
        projector.project_records(batch, mode=mode, source_high_watermark=200)

    expected_events, expected_records = _baseline_full_rebuild(
        batches, controller=projector.state["controller"], ingested_at_default=NOW
    )
    published_events = _current_json(tmp_path, "trade_journey_events.json")["events"]
    published_records = _current_json(tmp_path, "loop_runs.json")["records"]

    assert published_events == expected_events
    assert published_records == expected_records
    assert len(published_records) == 2


def test_recovery_events_redelivered_live_promote_the_published_read_model(tmp_path):
    rows = lifecycle_rows()
    projector = _projector(tmp_path, clock=lambda: NOW)
    projector.project_records(rows, mode="recovery", source_high_watermark=8)

    recovered = _current_json(tmp_path, "loop_runs.json")["records"]["lr-run-paper-001"]
    assert recovered["source_modes"] == ["recovery"]
    assert recovered["accepted_live"] is False
    assert recovered["projection_mode"] == "recovery"

    result = projector.project_records(rows, mode="live", source_high_watermark=8)
    assert result.accepted == 0
    assert result.duplicates == 8

    promoted = _current_json(tmp_path, "loop_runs.json")["records"]["lr-run-paper-001"]
    assert promoted["source_modes"] == ["live"]
    assert promoted["accepted_live"] is True
    assert promoted["projection_mode"] == "live"

    events = _current_json(tmp_path, "trade_journey_events.json")["events"]
    assert {event["source_mode"] for event in events} == {"live"}
    assert {event["source"] for event in events} == {"canonical_telemetry_live"}
    assert all(event["accepted_live"] for event in events)


def test_intra_batch_duplicate_is_counted_and_intra_batch_conflict_fails_closed(tmp_path):
    rows = lifecycle_rows()
    duplicated = rows + [json.loads(json.dumps(rows[0]))]
    projector = _projector(tmp_path / "dup", clock=lambda: NOW)
    result = projector.project_records(duplicated, mode="live", source_high_watermark=8)
    assert result.accepted == 8
    assert result.duplicates == 1
    assert (
        _current_json(tmp_path / "dup", "loop_runs.json")["records"]["lr-run-paper-001"][
            "canonical_event_count"
        ]
        == 8
    )

    conflicting = json.loads(json.dumps(rows[0]))
    conflicting["payload"]["metrics"] = {"action": "changed"}
    blocked = _projector(tmp_path / "conflict", clock=lambda: NOW)
    with pytest.raises(ConflictingLifecycleEvent):
        blocked.project_records(rows + [conflicting], mode="live", source_high_watermark=8)
    assert blocked.checkpoint == 0
    assert blocked.state["generation"] == 0
    assert blocked.state["aggregates"] == {}
    assert not (tmp_path / "conflict" / "controller_state.json").exists()
    assert not (tmp_path / "conflict" / "current").exists()


def test_reducer_work_is_bounded_by_batch_and_affected_aggregates(tmp_path, monkeypatch):
    projector = _projector(tmp_path, clock=lambda: NOW)
    batches = {
        poll: journey_rows(f"tj-bounded-{poll:03d}", id_base=1000 * poll, seq_base=100 * poll)
        for poll in range(1, 7)
    }
    observed: list[dict] = []
    for poll in range(1, 6):
        projector.project_records(
            batches[poll], mode="live", source_high_watermark=100 * poll + 8
        )
        observed.append(dict(projector._materializer.stats))

    # Total history grows 8 -> 40 events across five polls while per-poll work
    # stays flat.  The pre-fix reducer re-derived every stored journey on every
    # publish, so these would read 8, 16, 24, 32, 40.
    assert [stats["entries_derived"] for stats in observed] == [8, 8, 8, 8, 8]
    assert [stats["aggregates_rematerialized"] for stats in observed] == [1, 1, 1, 1, 1]
    assert [stats["aggregates_snapshotted"] for stats in observed] == [1, 1, 1, 1, 1]
    assert len(_current_json(tmp_path, "loop_runs.json")["records"]) == 5
    assert len(_current_json(tmp_path, "trade_journey_events.json")["events"]) == 40

    # A pure duplicate replay touches one aggregate and rebuilds nothing.
    projector.project_records(batches[3], mode="live", source_high_watermark=708)
    assert projector._materializer.stats == {
        "entries_derived": 0,
        "aggregates_rematerialized": 0,
        "aggregates_snapshotted": 1,
    }

    # No poll may deep-copy the read model.  The only deep copies are the fixed
    # controller dict and the affected aggregates themselves.
    copied: list = []
    real_deepcopy = lifecycle_projector_module.copy.deepcopy

    def spy(obj, *args, **kwargs):
        copied.append(obj)
        return real_deepcopy(obj, *args, **kwargs)

    monkeypatch.setattr(lifecycle_projector_module.copy, "deepcopy", spy)
    projector.project_records(batches[6], mode="live", source_high_watermark=708)
    assert copied, "expected the controller deep copy to be observed"
    assert not [item for item in copied if isinstance(item, dict) and "aggregates" in item]
    assert not [item for item in copied if isinstance(item, dict) and "canonical_events" in item]

    assert "canonical_events" not in projector.state
    assert len(projector.state["aggregates"]) == 6
    stored = projector.state["aggregates"]["tj-bounded-006"]
    assert "events_by_id" not in stored
    assert sorted(stored["event_fingerprints"]) == sorted(stored["event_modes"])


def _records_without_controller_stamp(records: dict) -> dict:
    from services.trade_journey.incremental_materializer import LOOP_RECORD_CONTROLLER_STAMP

    return {
        record_id: {
            key: value
            for key, value in record.items()
            if key not in LOOP_RECORD_CONTROLLER_STAMP
        }
        for record_id, record in records.items()
    }


def test_record_poll_and_source_failure_preserve_loop_records(tmp_path):
    projector = _projector(tmp_path, clock=lambda: NOW)
    projector.project_records(lifecycle_rows(), mode="live", source_high_watermark=8)
    initial = _current_json(tmp_path, "loop_runs.json")["records"]
    assert len(initial) == 1
    aggregate_half = _records_without_controller_stamp(initial)

    projector.record_poll(source_high_watermark=8, backlog=0, mode="recovery")
    after_poll = _current_json(tmp_path, "loop_runs.json")["records"]
    assert _records_without_controller_stamp(after_poll) == aggregate_half
    assert after_poll["lr-run-paper-001"]["controller_generation"] == 2

    projector.record_source_failure("database connection lost", backlog=5)
    after_failure = _current_json(tmp_path, "loop_runs.json")["records"]
    assert _records_without_controller_stamp(after_failure) == aggregate_half
    assert after_failure["lr-run-paper-001"]["controller_generation"] == 3

    # A heartbeat must not resurrect a full rebuild either.
    projector._materializer.reset_stats()
    projector.record_poll(source_high_watermark=99, backlog=91, mode="recovery")
    assert projector._materializer.stats["entries_derived"] == 0
    assert projector._materializer.stats["aggregates_rematerialized"] == 0
    assert len(_current_json(tmp_path, "loop_runs.json")["records"]) == 1


def test_stage_batch_conflicting_fingerprint_fails_closed_without_mutating_state(tmp_path):
    from services.trade_journey.incremental_materializer import IncrementalLifecycleMaterializer

    rows = lifecycle_rows()
    projector = _projector(tmp_path, clock=lambda: NOW)
    projector.project_records(rows[:1], mode="live", source_high_watermark=1)

    materializer = IncrementalLifecycleMaterializer(
        projector.state, journey_events_fn=LifecycleProjector._journey_events
    )
    aggregate = materializer.aggregates["tj-paper-001"]
    assert len(aggregate.event_fingerprints) == 1
    assert not hasattr(aggregate, "events_by_id")

    event = LifecycleProjector._source_event(rows[0])
    conflicting_entry = {
        "fingerprint": "0" * 64,
        "event": event,
        "identity": LifecycleProjector._identity(event),
        "sequence_no": LifecycleProjector._sequence_no(event),
        "ingested_seq": 1,
        "ingested_at": NOW,
        "source_mode": "live",
        "accepted_live": True,
    }
    with pytest.raises(ConflictingLifecycleEvent):
        materializer.stage_batch([conflicting_entry])
    assert materializer.aggregates["tj-paper-001"].event_fingerprints == aggregate.event_fingerprints


def test_state_transaction_failure_leaves_no_torn_state_and_restart_converges(
    tmp_path, monkeypatch
):
    projector = _projector(tmp_path, clock=lambda: NOW)
    original_commit = lifecycle_projector_module._commit_prepared_json

    def fail_state_commit(path, prepared):
        if Path(path).name == "controller_state.json":
            raise OSError("injected state transaction failure")
        return original_commit(path, prepared)

    monkeypatch.setattr(lifecycle_projector_module, "_commit_prepared_json", fail_state_commit)
    rows = lifecycle_rows()
    with pytest.raises(OSError, match="injected state transaction failure"):
        projector.project_records(rows, mode="live", source_high_watermark=8)

    assert not (tmp_path / "controller_state.json").exists()
    assert projector.checkpoint == 0
    assert projector.state["aggregates"] == {}
    assert projector._materializer.aggregates == {}
    monkeypatch.undo()

    recovered = _projector(tmp_path, clock=lambda: NOW)
    result = recovered.project_records(rows, mode="live", source_high_watermark=8)
    assert result.accepted == 8
    assert recovered.checkpoint == 8

    expected_events, expected_records = _baseline_full_rebuild(
        [("live", rows)],
        controller=recovered.state["controller"],
        ingested_at_default=NOW,
    )
    assert _current_json(tmp_path, "trade_journey_events.json")["events"] == expected_events
    assert _current_json(tmp_path, "loop_runs.json")["records"] == expected_records


def _sigkill_child_during_publish(tmp_path: Path, crash_point: str, rows: list[dict]) -> int:
    pid = os.fork()
    if pid == 0:  # pragma: no cover - the child is SIGKILLed before it returns
        try:
            if crash_point == "before_bundle_switch":
                publisher = AtomicProjectionBundle(
                    tmp_path,
                    before_switch=lambda _path: os.kill(os.getpid(), signal.SIGKILL),
                )
                projector = _projector(tmp_path, clock=lambda: NOW, publisher=publisher)
            else:
                projector = _projector(tmp_path, clock=lambda: NOW)
                original_commit = lifecycle_projector_module._commit_prepared_json

                def kill_on_state_commit(path, prepared):
                    if Path(path).name == "controller_state.json":
                        os.kill(os.getpid(), signal.SIGKILL)
                    return original_commit(path, prepared)

                lifecycle_projector_module._commit_prepared_json = kill_on_state_commit
            projector.project_records(rows, mode="live", source_high_watermark=8)
        except BaseException:
            os._exit(2)
        os._exit(3)
    _child, status = os.waitpid(pid, 0)
    return status


@pytest.mark.skipif(not hasattr(os, "fork"), reason="SIGKILL convergence needs fork()")
@pytest.mark.parametrize("crash_point", ["before_bundle_switch", "after_bundle_switch"])
def test_sigkill_mid_publish_converges_on_restart(tmp_path, crash_point):
    rows = lifecycle_rows()
    status = _sigkill_child_during_publish(tmp_path, crash_point, rows)
    assert os.WIFSIGNALED(status), f"child exited normally with status {status}"
    assert os.WTERMSIG(status) == signal.SIGKILL

    # The state file is only committed after the bundle switch, so neither crash
    # point can leave a controller state that claims events the bundle lacks.
    assert not (tmp_path / "controller_state.json").exists()
    if crash_point == "before_bundle_switch":
        assert not (tmp_path / "current").exists()
    else:
        assert _current_json(tmp_path, "loop_runs.json")["generation"] == 1

    restarted = _projector(tmp_path, clock=lambda: NOW)
    result = restarted.project_records(rows, mode="live", source_high_watermark=8)
    assert result.accepted == 8
    assert restarted.checkpoint == 8

    expected_events, expected_records = _baseline_full_rebuild(
        [("live", rows)],
        controller=restarted.state["controller"],
        ingested_at_default=NOW,
    )
    assert _current_json(tmp_path, "trade_journey_events.json")["events"] == expected_events
    assert _current_json(tmp_path, "loop_runs.json")["records"] == expected_records
