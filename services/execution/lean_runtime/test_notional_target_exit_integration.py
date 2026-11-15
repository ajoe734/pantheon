"""notional_target_v1: a flat view closes and a weaker view trims, end to end.

Real entry points only: PaperSignalProducer -> CurrentArtifactStrategy with the
production expression inputs and the real candidate builder -> persisted
expression record -> PaperRuntimeService risk gate -> paper ledger.
"""
from datetime import datetime

from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy, PaperSignalProducer
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.test_current_artifact_signal import (
    _binding, _identity, _runtime_binding, _RuntimeManager, _Telemetry,
)
from services.execution.lean_runtime.test_expression_inputs import _pool, _summary
from services.execution.lean_runtime.test_portfolio_target_positions import cash_candidates, production_inputs
from services.registry.test_strategy_artifact import _artifact_with_expression_policy


def flat_capable_artifact():
    """Momentum artifact whose non-positive momentum is a FLAT view (not a SHORT)."""
    artifact = _artifact_with_expression_policy()
    artifact["strategy_logic"]["non_positive_action"] = "HOLD"
    if "zero_momentum_action" in artifact["parameters"]:
        artifact["parameters"]["zero_momentum_action"] = "HOLD"
    return artifact


def _setup(tmp_path):
    artifact = flat_capable_artifact()
    # Keep the position inside cash so the paper risk gate and cash check pass.
    artifact["expression_policy"]["parameters"]["notional_fraction_at_full_strength"] = .2
    for control in artifact["mutation_surface"]["controls"]:
        if control["parameter_key"] == "notional_fraction_at_full_strength":
            control["current_value"] = .2
    binding = _binding(artifact, binding_id="rb-flat-trim")
    binding["capital_pool_id"] = "pool-1"
    summary = _summary()
    strategy = CurrentArtifactStrategy(expression_layer=True, expression_inputs=production_inputs(summary),
                                       candidates_builder=cash_candidates)
    store, telemetry = InMemoryPendingSignalStore(), _Telemetry()
    producer = PaperSignalProducer(lambda _: store, strategy)
    service = PaperRuntimeService(store=store, identity=_identity(binding),
        runtime_manager_client=_RuntimeManager(_runtime_binding(binding)), telemetry_emitter=telemetry,
        lifecycle_outbox_path=tmp_path / "lifecycle.json", capital_pool_reader=lambda _: _pool())
    service._algo._in_process_broker = True

    def bar(day, closes):
        binding["market_input"] = {"symbol": "2330.TW", "closes": closes,
            "source_ref": "test-only:flat-trim-bars", "event_time": day + "T05:30:00Z",
            "observed_at": day + "T06:00:00Z"}
        positions = service._algo.positions()
        if positions or summary.get("ledger_fill_count"):
            summary.update(ledger_fill_count=1, ledger_cash=service._algo._cash,
                positions=[{**p, "mark_price": closes[-1]} for p in positions])
        service._algo.Time = datetime.fromisoformat(day + "T06:00:00+00:00")
        produced = producer.produce(binding, day + "T06:00:00Z")
        assert produced == 1, (produced, producer.degraded_bindings)
        for _ in range(4):
            snapshot = service.drain_once()
        assert snapshot["status"] == "ok", snapshot
        return {p["symbol"]: p["quantity"] for p in service._algo.positions()}

    return bar, store, telemetry


def test_flat_view_closes_and_weaker_view_trims_through_the_paper_ledger(tmp_path):
    bar, store, telemetry = _setup(tmp_path)
    opened = bar("2026-11-13", [100, 110])
    assert len(opened) == 1
    symbol, units = next(iter(opened.items()))
    assert units > 0
    # Same-direction view that is weaker: the position is trimmed, not held.
    trimmed = bar("2026-11-16", [100, 100.4])
    assert 0 < trimmed[symbol] < units, (opened, trimmed)
    from services.registry.strategy_artifact import evaluate_strategy_view
    assert evaluate_strategy_view(flat_capable_artifact(), [100, 100])["direction"] == "FLAT"
    # A genuinely FLAT view (not an opposite view) closes what is left.
    assert bar("2026-11-17", [100, 100]) == {}
    fills = [e for e in telemetry.events if e["event_type"] == "paper_fill_simulated"]
    assert len(fills) == 3
