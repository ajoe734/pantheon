from importlib import import_module

import pytest


envelopes = import_module("services.trade_journey.correlation_envelope")
signal_producer = import_module("services.execution.lean_runtime.signal_producer")


NOW = "2026-07-11T12:00:00Z"


def _origin():
    ids = iter(["journey", "correlation", "trace", "event", "cause"])
    return envelopes.mint_trade_envelope(
        {
            "tenant_id": "tenant-1",
            "environment": "paper",
            "research_journey_id": "rj_1",
            "strategy_lifecycle_id": "sl_1",
        },
        producer="execution.signal-decision",
        now=NOW,
        id_factory=lambda: next(ids),
    )


@pytest.mark.parametrize(
    ("producers", "terminal"),
    [
        (("risk.accepted", "broker.accepted", "ledger.booked", "reconciliation.completed"), "completed"),
        (("risk.rejected",), "risk_rejected"),
        (("risk.accepted", "broker.rejected"), "broker_rejected"),
    ],
)
def test_correlation_survives_p0_terminal_paths_without_temporal_join(producers, terminal):
    current = _origin()
    stable = {field: current.get(field) for field in envelopes.STABLE_FIELDS}
    for revision, producer in enumerate(producers, start=1):
        downstream = envelopes.propagate_envelope(
            current,
            producer=producer,
            event_id=f"event-{revision}",
            event_time=NOW,
            producer_revision=revision,
        )
        envelopes.assert_no_field_loss(current, downstream)
        current = downstream
    assert {field: current.get(field) for field in envelopes.STABLE_FIELDS} == stable
    assert terminal in {"completed", "risk_rejected", "broker_rejected"}


def test_signal_boundary_mints_one_journey_and_preserves_upstream_ids():
    decision = {
        "decision_id": "decision-1",
        "strategy_id": "strategy-1",
        "symbol": "2330.TW",
        "action": "BUY",
        "direction": "LONG",
        "quantity": 1,
        "quantity_type": "SHARES",
        "timestamp": NOW,
        "correlation_envelope": {
            "tenant_id": "tenant-1",
            "environment": "broker_sandbox",
            "research_journey_id": "rj_1",
            "strategy_lifecycle_id": "sl_1",
            "correlation_id": "corr-upstream",
            "trace_id": "trace-upstream",
            "event_id": "promotion-event-1",
        },
    }
    signal = signal_producer.build_decision_signals(decision)[0]
    envelope = signal["correlation_envelope"]
    assert envelope["journey_id"].startswith("tj_")
    assert envelope["correlation_id"] == "corr-upstream"
    assert envelope["trace_id"] == "trace-upstream"
    assert envelope["causation_event_id"] == "promotion-event-1"


def test_propagation_rejects_schema_drift_and_field_loss():
    origin = _origin()
    changed = envelopes.propagate_envelope(
        origin, producer="risk.accepted", event_id="risk-1", event_time=NOW
    )
    changed["journey_id"] = "tj_invented_downstream"
    with pytest.raises(envelopes.CorrelationEnvelopeError, match="journey_id"):
        envelopes.assert_no_field_loss(origin, changed)

    incompatible = dict(origin, schema_version="trade-journey-envelope/2")
    with pytest.raises(envelopes.CorrelationEnvelopeError, match="unsupported schema_version"):
        envelopes.propagate_envelope(
            incompatible, producer="risk.accepted", event_id="risk-2", event_time=NOW
        )
