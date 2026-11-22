"""Daily paper decisions fill at the next session's official open, end to end.

PAPER-DAILY-FILL-TIMING-AND-FLUSH-20261115 (operator decision 2026-11-15, option A).  Real entry
points: PaperSignalProducer -> CurrentArtifactStrategy -> parked pending_settlement record ->
next-session release from official daily data -> SignalConsumer/PaperRuntimeService -> paper
ledger, over two consecutive recorded sessions (2026-11-13 Fri bar, 2026-11-16 Mon bar).

Known temporary difference: the gate backtest and pool admission replay still fill daily
strategies at the decision close until GATE-DAILY-NEXT-OPEN-REBASELINE-20261115 merges.
"""
from datetime import datetime

from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy, PaperSignalProducer
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.test_current_artifact_signal import (
    _binding, _identity, _runtime_binding, _RuntimeManager, _Telemetry,
)
from services.execution.lean_runtime.test_expression_inputs import _pool, _summary
from services.execution.lean_runtime.test_notional_target_exit_integration import flat_capable_artifact
from services.execution.lean_runtime.test_portfolio_target_positions import cash_candidates, production_inputs

# Recorded official data: (trade date) -> (open, close).  The open of D+1 differs from the close of D.
OFFICIAL = {
    "2026-11-13": (101.0, 110.0),
    "2026-11-16": (104.0, 112.0),
    "2026-11-17": (111.5, 113.0),
}


def _setup(tmp_path, official=OFFICIAL):
    artifact = flat_capable_artifact()
    artifact["expression_policy"]["parameters"]["notional_fraction_at_full_strength"] = .2
    for control in artifact["mutation_surface"]["controls"]:
        if control["parameter_key"] == "notional_fraction_at_full_strength":
            control["current_value"] = .2
    binding = _binding(artifact, binding_id="rb-daily-next-open")
    binding["capital_pool_id"] = "pool-1"
    summary = _summary()
    strategy = CurrentArtifactStrategy(expression_layer=True, expression_inputs=production_inputs(summary),
                                       candidates_builder=cash_candidates)
    store, telemetry = InMemoryPendingSignalStore(), _Telemetry()

    def official_reader(symbol, session_date, now):
        row = official.get(session_date)
        if row is None:
            return []  # the session is not reconciled: nothing is substituted
        return [{"symbol": symbol, "bar_start": f"{session_date}T09:00:00+08:00", "bar_frequency": "1d",
                 "bar_availability_ts": f"{session_date}T16:00:00+08:00", "open": row[0]}]

    producer = PaperSignalProducer(lambda _: store, strategy, daily_bar_reader=official_reader)
    service = PaperRuntimeService(store=store, identity=_identity(binding),
        runtime_manager_client=_RuntimeManager(_runtime_binding(binding)), telemetry_emitter=telemetry,
        lifecycle_outbox_path=tmp_path / "lifecycle.json", capital_pool_reader=lambda _: _pool())
    service._algo._in_process_broker = True

    def decide(day, closes):
        """Evening of ``day``: the official close is reconciled and the producer decides."""
        binding["market_input"] = {"symbol": "2330.TW", "closes": closes, "source_ref": "test-only:daily-open",
            "event_time": day + "T05:30:00Z", "observed_at": day + "T06:00:00Z"}
        service._algo.Time = datetime.fromisoformat(day + "T10:00:00+00:00")
        return producer.produce(binding, day + "T10:00:00Z")

    def drain():
        for _ in range(4):
            snapshot = service.drain_once()
        assert snapshot["status"] == "ok", snapshot

    def positions():
        return {p["symbol"]: p["quantity"] for p in service._algo.positions()}

    def fills():
        return [e for e in telemetry.events if e["event_type"] == "paper_fill_simulated"]

    return decide, drain, positions, fills, store, producer, binding, service, telemetry


def test_two_sessions_fill_at_the_next_official_open_and_never_at_the_decision_close(tmp_path):
    decide, drain, positions, fills, store, producer, binding, service, telemetry = _setup(
        tmp_path / "open_111_5"
    )

    # Session 1: the 2026-11-13 bar decides on Friday evening -> parked, no order, no position.
    assert decide("2026-11-13", [100, 110]) == 0
    assert store.queue_depth() == 0
    drain()
    assert positions() == {} and fills() == []

    # Monday evening: the official 2026-11-16 data has landed; the producer tick releases the order.
    released = decide("2026-11-16", [110, 112])
    # one order from the Friday decision settled at the 2026-11-16 open; the Monday bar is parked.
    assert released == 1, (released, producer.degraded_bindings)
    drain()
    first_fill = fills()[-1]
    price = _fill_price(first_fill)
    assert price == OFFICIAL["2026-11-16"][0] != OFFICIAL["2026-11-13"][1]  # open of D+1, not close of D
    assert positions() == {"2330.TW": 363.0}
    assert [_fill_price(f) for f in fills()] == [104.0]

    # Session 2 at official open 111.5: 363 * 111.5 = 40474.5 > 40000 target notional -> already_at_target guard holds.
    released = decide("2026-11-17", [112, 113])
    assert released == 1, (released, producer.degraded_bindings)
    drain()
    assert positions() == {"2330.TW": 363.0}
    assert [_fill_price(f) for f in fills()] == [104.0]
    no_trade_events = [
        e for e in telemetry.events
        if e["event_type"] == "paper_order_simulated"
        and e.get("metadata", {}).get("reason_code") == "already_at_target"
    ]
    assert len(no_trade_events) == 1
    assert no_trade_events[-1]["metadata"]["market_price"] == OFFICIAL["2026-11-17"][0]
    assert no_trade_events[-1]["metadata"]["market_price_as_of"] == "2026-11-17T09:00:00+08:00"
    assert no_trade_events[-1]["metadata"]["submitted_to_broker"] is False
    assert no_trade_events[-1]["metadata"]["submission_sizing_evidence"]["binding_limit"] == "already_at_target"
    assert no_trade_events[-1]["metadata"]["submission_sizing_evidence"]["permitted_units"] == 0

    # Session 2 with positive remaining target capacity at official open 105.0:
    # 363 * 105.0 = 38115.0 < 40000.0 -> floor((40000.0 - 38115.0) / 105.0) = 17 additional shares fill at 105.0.
    decide_2, drain_2, positions_2, fills_2, store_2, producer_2, _, _, _ = _setup(
        tmp_path / "open_105_0",
        official={**OFFICIAL, "2026-11-17": (105.0, 113.0)},
    )
    assert decide_2("2026-11-13", [100, 110]) == 0
    drain_2()
    assert decide_2("2026-11-16", [110, 112]) == 1
    drain_2()
    assert positions_2() == {"2330.TW": 363.0}
    assert [_fill_price(f) for f in fills_2()] == [104.0]

    released_2 = decide_2("2026-11-17", [112, 113])
    assert released_2 == 1, (released_2, producer_2.degraded_bindings)
    drain_2()
    assert positions_2() == {"2330.TW": 380.0}
    assert [_fill_price(f) for f in fills_2()] == [104.0, 105.0]
    second_fill = fills_2()[-1]
    assert _fill_price(second_fill) == 105.0 != OFFICIAL["2026-11-16"][1]
    assert second_fill["metadata"]["market_price"] == 105.0
    assert second_fill["metadata"]["market_price_as_of"] == "2026-11-17T09:00:00+08:00"
    assert second_fill["metadata"]["submission_sizing_evidence"]["binding_limit"] == "existing_holdings_target_notional"
    assert second_fill["metadata"]["submission_sizing_evidence"]["permitted_units"] == 17


def _fill_price(event):
    return float(event["metrics"]["fill_price"])


def test_replay_and_restart_never_duplicate_the_order(tmp_path):
    decide, drain, positions, fills, store, producer, binding, service, _ = _setup(tmp_path)
    assert decide("2026-11-13", [100, 110]) == 0
    assert decide("2026-11-13", [100, 110]) == 0  # same bar polled again: still one parked decision
    assert decide("2026-11-16", [110, 112]) == 1
    drain()
    count = len(fills())
    # A restarted producer replays the same bars: the released decision is not offered again.
    assert decide("2026-11-16", [110, 112]) == 0
    drain()
    assert len(fills()) == count


def test_missing_next_session_open_expires_without_a_close_fill(tmp_path):
    decide, drain, positions, fills, store, producer, binding, service, _ = _setup(
        tmp_path, official={"2026-11-17": (111.5, 113.0)}  # 2026-11-16 never reconciled
    )
    assert decide("2026-11-13", [100, 110]) == 0
    # Monday evening: no official 2026-11-16 row yet -> still pending, nothing filled.
    assert decide("2026-11-16", [110, 112]) == 0
    drain()
    assert positions() == {} and fills() == []
    # After the expiry (D+2 08:55) the decision is closed with a reason; no close fill appears.
    producer.settle_pending_intraday(binding, "2026-11-17T09:00:00+08:00")
    drain()
    assert positions() == {} and fills() == []
    record = store.get_decision_record(
        next(k for k in _decision_ids(store, binding)) + "#intraday-settlement"
    )
    assert record["status"] == "expired_no_price" and record["fill"] is None
    assert record["reason"] == "target_session_not_reconciled_by_expiry"


def _decision_ids(store, binding):
    from services.execution.lean_runtime.intraday_settlement import index_slot_id

    entry = store.get_decision_record(index_slot_id(binding["binding_id"], "2026-11-13", 0))
    return [entry["decision_id"]]
