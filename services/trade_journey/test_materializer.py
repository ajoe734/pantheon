import copy

import pytest

from services.trade_journey.materializer import JourneyMaterializer, MaterializationError


def event(event_id, stage, status, minute, **extra):
    return {
        "event_id": event_id, "journey_id": "tj_1", "tenant_id": "tenant-a",
        "environment": "paper", "occurred_at": f"2026-07-12T00:{minute:02d}:00Z",
        "source": "test", "stage": stage, "stage_status": status, **extra,
    }


def test_duplicate_out_of_order_late_and_rebuild_are_deterministic():
    history = [
        event("e1", "signal_generation", "succeeded", 1, signal_id="sig-1"),
        event("e2", "trade_decision", "succeeded", 2, decision_id="dec-1"),
        event("e3", "risk_evaluation", "succeeded", 3),
    ]
    materializer = JourneyMaterializer()
    for item in reversed(history):
        materializer.ingest(item)
    assert materializer.ingest(copy.deepcopy(history[0])) is False
    before = materializer.get("tj_1", tenant_id="tenant-a", environment="paper")
    rebuilt = JourneyMaterializer()
    rebuilt.rebuild([history[1], history[0], history[2], history[0]])
    after = rebuilt.get("tj_1", tenant_id="tenant-a", environment="paper")
    assert before.snapshot == after.snapshot
    assert [item["event_id"] for item in after.timeline] == ["e1", "e2", "e3"]
    assert rebuilt.rebuild_status == "complete"


def test_mixed_timestamp_precision_is_ordered_chronologically():
    whole_second = event("e1", "signal_generation", "succeeded", 0)
    whole_second["occurred_at"] = "2026-07-12T00:00:00Z"
    fractional_second = event("e2", "trade_decision", "succeeded", 0)
    fractional_second["occurred_at"] = "2026-07-12T00:00:00.500000Z"

    materializer = JourneyMaterializer()
    materializer.rebuild([fractional_second, whole_second])

    projection = materializer.get("tj_1", tenant_id="tenant-a", environment="paper")
    assert [item["event_id"] for item in projection.timeline] == ["e1", "e2"]
    assert projection.snapshot["created_at"] == "2026-07-12T00:00:00.000000Z"
    assert projection.snapshot["updated_at"] == "2026-07-12T00:00:00.500000Z"
    assert materializer.source_watermarks["test"] == "2026-07-12T00:00:00.500000Z"


def test_conflicting_duplicate_fails_closed_without_mutating_projection():
    materializer = JourneyMaterializer()
    materializer.ingest(event("e1", "signal_generation", "succeeded", 1))
    changed = event("e1", "signal_generation", "failed", 1)
    with pytest.raises(MaterializationError, match="conflicting duplicate"):
        materializer.ingest(changed)
    assert materializer.get("tj_1", tenant_id="tenant-a", environment="paper").snapshot["status"] == "open"


def test_reverse_index_is_typed_tenant_environment_scoped_and_rbac_filtered():
    materializer = JourneyMaterializer()
    materializer.ingest(event("e1", "order_submission", "succeeded", 1, client_order_id="same", order_id="same"))
    assert materializer.resolve("client_order_id", "same", tenant_id="tenant-a", environment="paper") == ["tj_1"]
    assert materializer.resolve("order_id", "same", tenant_id="tenant-a", environment="live") == []
    assert materializer.resolve("order_id", "same", tenant_id="tenant-a", environment="paper", allowed_journey_ids=set()) == []


def test_persona_strategy_and_fill_identifiers_are_canonical_reverse_indexes():
    materializer = JourneyMaterializer()
    materializer.ingest(
        event(
            "e1",
            "fill_management",
            "partially_succeeded",
            1,
            persona_id="persona-1",
            strategy_id="strategy-1",
            fill_id="fill-1",
        )
    )

    for identifier_type, identifier in (
        ("persona_id", "persona-1"),
        ("strategy_id", "strategy-1"),
        ("fill_id", "fill-1"),
    ):
        assert materializer.resolve(
            identifier_type,
            identifier,
            tenant_id="tenant-a",
            environment="paper",
        ) == ["tj_1"]


def test_replace_chain_partial_fill_cancel_and_graph_are_materialized():
    materializer = JourneyMaterializer()
    materializer.rebuild([
        event("e1", "order_submission", "succeeded", 1, client_order_id="client-1", order_id="order-1"),
        event("e2", "broker_acknowledgement", "succeeded", 2, order_id="order-2",
              graph_edges=[{"from": "order-1", "to": "order-2", "type": "replaced_by"}]),
        event("e3", "fill_management", "partially_succeeded", 3, broker_trade_id="trade-1"),
    ])
    projection = materializer.get("tj_1", tenant_id="tenant-a", environment="paper")
    assert projection.snapshot["status"] == "partially_filled"
    assert projection.graph_edges == [{"from": "order-1", "to": "order-2", "type": "replaced_by"}]
    materializer.ingest(event("e4", "fill_management", "cancelled", 4, event_type="order_cancelled"))
    assert materializer.get("tj_1", tenant_id="tenant-a", environment="paper").snapshot["status"] == "cancelled"


def test_reconciliation_mismatch_correction_and_revision():
    materializer = JourneyMaterializer()
    materializer.ingest(event("e1", "reconciliation", "failed", 3, reconciliation_id="recon-1"))
    projection = materializer.get("tj_1", tenant_id="tenant-a", environment="paper")
    assert projection.snapshot["status"] == "completed_with_variance"
    assert projection.snapshot["revision"] == 1
    materializer.ingest(event("e2", "reconciliation", "succeeded", 4, event_type="correction"))
    projection = materializer.get("tj_1", tenant_id="tenant-a", environment="paper")
    assert projection.snapshot["status"] == "completed"
    assert projection.snapshot["revision"] == 2


def test_reject_orphan_missing_stages_and_terminal_conflict_diagnostics():
    materializer = JourneyMaterializer()
    materializer.rebuild([
        event("e1", "risk_evaluation", "rejected", 1, order_id="orphan"),
        event("e2", "reconciliation", "succeeded", 2),
    ])
    projection = materializer.get("tj_1", tenant_id="tenant-a", environment="paper")
    codes = {item["code"] for item in projection.diagnostics}
    assert projection.snapshot["status"] == "incomplete"
    assert {"missing_stages", "orphan_identifier", "conflicting_terminal_states"} <= codes


def test_failure_injection_rejects_invalid_event_and_marks_failed_rebuild():
    materializer = JourneyMaterializer()
    invalid = event("e1", "signal_generation", "succeeded", 1)
    invalid["occurred_at"] = "not-a-date"
    with pytest.raises(MaterializationError, match="occurred_at"):
        materializer.rebuild([invalid])
    assert materializer.rebuild_status == "failed"
    assert materializer.projections == []
