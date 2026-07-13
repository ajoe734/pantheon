"""TJ-E2E-013: telemetry -> journey-event bridge."""

from __future__ import annotations

from services.trade_journey.materializer import JourneyMaterializer
from services.trade_journey.telemetry_bridge import (
    BACKFILL_SOURCE,
    journey_events_from_telemetry,
    merge_with_store,
)


def _order_payload(event_id: str = "ord-1", signal_id: str = "sig-1") -> dict:
    return {
        "event_id": event_id,
        "created_at": "2026-07-13T10:00:00Z",
        "environment": "paper",
        "runtime_id": "rt-devloop-l0-001",
        "artifact_id": "artifact-devloop-l0-001",
        "capital_pool_id": "pool-1",
        "target": {"strategy_id": "artifact-devloop-l0-001"},
        "metrics": {"action": "buy", "submitted_to_broker": False},
        "metadata": {
            "symbol": "NVDA",
            "signal_id": signal_id,
            "order_type": "MARKET",
            "strategy_id": "strategy-devloop-l0-001",
            "order_status": "not_submitted",
            "decision_status": "order_planned",
            "price": 100.0,
            "requested_quantity": 3.0,
            "computed_quantity": 2.0,
        },
    }


def _fill_payload(event_id: str = "fil-1", signal_id: str = "sig-1", quantity: float = -5.0) -> dict:
    return {
        "event_id": event_id,
        "created_at": "2026-07-13T10:00:05Z",
        "environment": "paper",
        "runtime_id": "rt-devloop-l0-001",
        "metrics": {"action": "liquidate", "fill_price": 101.5, "fill_quantity": quantity},
        "metadata": {"symbol": "NVDA", "signal_id": signal_id, "order_type": "MARKET"},
    }


def test_order_and_fill_share_a_signal_scoped_journey():
    events = journey_events_from_telemetry([
        ("paper_order_simulated", "2026-07-13T10:00:01Z", _order_payload()),
        ("paper_fill_simulated", "2026-07-13T10:00:06Z", _fill_payload()),
    ])
    assert [e["event_id"] for e in events] == ["ord-1-decision", "ord-1-order", "fil-1-fill"]
    assert {e["journey_id"] for e in events} == {"tj-sig-1"}
    assert all(e["source"] == BACKFILL_SOURCE for e in events)
    decision, order, fill = events
    assert decision["stage"] == "trade_decision" and decision["stage_status"] == "order_planned"
    assert order["stage"] == "order_submission" and order["stage_status"] == "not_submitted"
    assert order["quantity"] == 2.0
    assert fill["stage"] == "fill_management" and fill["stage_status"] == "filled"
    assert fill["side"] == "sell" and fill["quantity"] == 5.0 and fill["price"] == 101.5


def test_rows_without_signal_or_timestamp_stay_safe():
    orphan = _fill_payload(event_id="fil-2", signal_id="")
    events = journey_events_from_telemetry([("paper_fill_simulated", "", orphan)])
    assert events[0]["journey_id"] == "tj-evt-fil-2"
    assert events[0]["recorded_at"] == events[0]["occurred_at"]
    broken = dict(_order_payload(), created_at="")
    assert journey_events_from_telemetry([("paper_order_simulated", "", broken)]) == []


def test_merge_preserves_seed_and_replaces_prior_backfill():
    seed = {"event_id": "s1-e1", "journey_id": "tj-scenario-1", "source": "seed",
            "occurred_at": "2026-07-12T12:01:00Z"}
    stale = {"event_id": "gone", "journey_id": "tj-old", "source": BACKFILL_SOURCE,
             "occurred_at": "2026-07-01T00:00:00Z"}
    fresh = journey_events_from_telemetry([
        ("paper_order_simulated", "2026-07-13T10:00:01Z", _order_payload()),
    ])
    merged = merge_with_store([seed, stale], fresh)
    ids = [event["event_id"] for event in merged]
    assert "s1-e1" in ids and "gone" not in ids
    assert "ord-1-decision" in ids and "ord-1-order" in ids


def test_materializer_accepts_bridge_output():
    events = journey_events_from_telemetry([
        ("paper_order_simulated", "2026-07-13T10:00:01Z", _order_payload()),
        ("paper_fill_simulated", "2026-07-13T10:00:06Z", _fill_payload()),
    ])
    materializer = JourneyMaterializer()
    materializer.rebuild(events)
    projection = materializer.get("tj-sig-1", tenant_id="default", environment="paper")
    assert projection is not None
    stages = projection.snapshot["stages"]
    assert set(stages) == {"trade_decision", "order_submission", "fill_management"}
    assert projection.snapshot["identifiers"]["signal_id"] == ["sig-1"]
    assert materializer.resolve("signal_id", "sig-1", tenant_id="default", environment="paper") == ["tj-sig-1"]
    missing = projection.snapshot["completeness"]["missing_stages"]
    assert "signal_generation" in missing and "risk_evaluation" in missing  # honesty: no fabricated stages
