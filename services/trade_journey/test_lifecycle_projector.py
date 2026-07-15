from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest

from services.trade_journey.correlation_envelope import (
    mint_trade_envelope,
    propagate_envelope,
)
from services.trade_journey.lifecycle_projector import (
    AtomicProjectionBundle,
    ConflictingLifecycleEvent,
    LifecycleProjector,
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
    assert projector.checkpoint == 0

    recovered = _projector(tmp_path)
    recovered.project_records(
        lifecycle_rows()[:1], mode="recovery", source_high_watermark=1
    )
    assert (tmp_path / "current" / "manifest.json").is_file()
    assert recovered.checkpoint == 1


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
