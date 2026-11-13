"""Target positions through the production policy, producer and paper ledger."""
import copy
from unittest.mock import patch

import pytest

from services.execution.lean_runtime import expression_policy as ep
from services.execution.lean_runtime.expression_inputs import build_production_expression_inputs
from services.execution.lean_runtime.expression_replay import replay, counterfactual_rows
from services.execution.lean_runtime.instrument_candidates import build_candidates
from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy, PaperSignalProducer
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.test_current_artifact_signal import (
    _binding, _identity, _runtime_binding, _RuntimeManager, _Telemetry,
)
from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.test_expression_inputs import POLICY, _pool, _summary
from services.registry.test_portfolio_envelope import envelope, member_artifact
from services.registry.test_strategy_artifact import _artifact_with_expression_policy

PARAMS = {**ep.DEFAULT_PARAMETERS, "rebalance_band_fraction": .2}
BUDGET = {"gross_limit": 100000, "gross_used": 10000, "available_cash": 90000}
NOW = "2026-11-13T06:00:00Z"


def candidate(code="2330", price=100, maximum=1000):
    return {"instrument_id": "eq:" + code, "price": price, "unit": 1,
            "multiplier": 1, "kind": "cash_share", "side_capability": ["long"],
            "max_units_by_risk": maximum, "fee_per_unit_per_side": 0,
            "tax_rate_per_side": 0}


def held(units=100, instrument_id="eq:2330", direction="LONG"):
    return {"positions": [{"underlying": "2330", "instrument_id": instrument_id,
                           "direction": direction, "units": units, "notional": units * 100}]}


def decide(direction, target, book=None, candidates=None, **extra):
    view = {"decision_id": "d", "underlying": "2330", "direction": direction,
            "strength": .5, "target_notional": target, **extra}
    return ep.decide_for_policy(ep.TARGET_POLICY_KIND, view,
        [candidate()] if candidates is None else candidates, book or {}, BUDGET, PARAMS)


def test_bar_allocation_is_order_independent_and_uses_full_budget():
    views = {f"{2300+i}.TW": {"direction": "LONG", "strength": 1} for i in range(40)}
    a = ep.allocate_targets(views, BUDGET, PARAMS)
    b = ep.allocate_targets(dict(reversed(list(views.items()))), BUDGET, PARAMS)
    assert a == b
    assert set(a["targets"].values()) == {2500}
    assert sum(a["targets"].values()) == BUDGET["gross_limit"]
    for s, target in a["targets"].items():
        code = s.split(".")[0]
        out = ep.decide_for_policy(ep.TARGET_POLICY_KIND,
            {"underlying": code, "direction": "LONG", "strength": 1, "target_notional": target},
            [candidate(code)], {}, BUDGET, PARAMS)
        assert out["units"] == 25


@pytest.mark.parametrize("direction,target,units", [("FLAT", 0, 100), ("LONG", 5000, 50), ("SHORT", 5000, 100)])
def test_flat_weaker_and_reverse_reduce_only(direction, target, units):
    out = decide(direction, target, held())
    assert out["reason_code"] == "close_leg"
    assert out["side"] == "sell" and out["units"] == units
    assert out["rebalance_band"] == max(100, .2 * target)
    assert out["policy_parameters_digest"] == ep.policy_parameters_digest(PARAMS)
    assert decide("SHORT", max(target, 1))["reason_code"] == "no_feasible_instrument"


def test_band_addition_risk_cap_and_invalid_reduce():
    assert decide("LONG", 10050, held())["reason_code"] == "already_at_target"
    assert decide("LONG", 20000, held(), [candidate(maximum=3)])["units"] == 3
    assert decide("FLAT", 0, held(), [])["reason_code"] == "candidates_unavailable"
    bad = {**decide("FLAT", 0, held()), "units": 101}
    assert ep.enforce(bad, [candidate()], {"underlying": "2330"}, held())["reason_code"] == "instrument_not_in_candidates"


def test_partial_reduce_replays_and_writes_counterfactual_rows():
    view = {"decision_id": "d", "underlying": "2330", "direction": "LONG", "strength": .5}
    allocation = ep.allocate_targets({"2330.TW": view}, {"gross_limit": 10000}, PARAMS)
    record = {"decision_id": "d", "policy_kind": ep.TARGET_POLICY_KIND,
        "view": {**view, "target_notional": 5000}, "bar_allocation": allocation,
        "budget": {**BUDGET, "gross_limit": 10000}, "book_before": held(),
        "candidates": [candidate()], "parameters": PARAMS, "marks": {"eq:2330": [101]}}
    result = replay([record], PARAMS)["decisions"][0]
    assert result["record"] == ep.decide_for_policy(ep.TARGET_POLICY_KIND, record["view"],
        record["candidates"], record["book_before"], record["budget"], PARAMS)
    assert result["positions_after"][0]["units"] == 50
    assert len(counterfactual_rows(record, result)) == 1


def production_inputs(summary):
    env = {"PANTHEON_CAPITAL_API_URL": "http://capital", "DEPLOYMENT_CAPITAL_SERVICE_TOKEN": "test",
           "PANTHEON_TELEMETRY_API_URL": "http://telemetry"}
    pool = _pool()
    with patch("services.execution.lean_runtime.expression_inputs._http_get_json",
               side_effect=lambda url, *_: pool if "/capital-pools/" in url else summary):
        provider = build_production_expression_inputs(env)
    return provider


def cash_candidates(*args, **kwargs):
    # Real builder; this fixture provides no recorded derivative contracts.
    return build_candidates(*args, **kwargs, rows_reader=lambda: [])


def multi_envelope(weight=.5, bearish=True):
    symbols = ["2330.TW", "2317.TW", "2454.TW"]
    a = _artifact_with_expression_policy()
    a["parameters"]["symbols"] = symbols
    b = copy.deepcopy(a)
    b.update(artifact_id="bear", strategy_id="bear")
    if bearish:
        b["strategy_logic"]["positive_action"] = "SELL"
    return envelope([{"member_id": "a", "artifact": a, "risk_weight": weight},
                     {"member_id": "b", "artifact": b, "risk_weight": 1-weight}])


def portfolio_binding(artifact, closes=None):
    binding = _binding(artifact, binding_id="rb-target", include_market_input=False)
    binding["capital_pool_id"] = "pool-1"
    binding["market_inputs"] = {symbol: {"symbol": symbol, "closes": closes or [100, 102],
        "source_ref": "test-only:target-bars", "event_time": "2026-11-13T05:30:00Z",
        "observed_at": NOW} for symbol in artifact["parameters"]["symbols"]}
    return binding


def test_producer_uses_production_book_shape_including_held_outside_fixed_symbols():
    artifact = multi_envelope()
    binding = portfolio_binding(artifact)
    binding["market_inputs"]["9999.TW"] = {**binding["market_inputs"]["2330.TW"], "symbol": "9999.TW"}
    summary = _summary(ledger_fill_count=1, ledger_cash=90000,
        positions=[{"symbol": "9999.TW", "quantity": 10, "mark_price": 102}])
    provider = production_inputs(summary)
    strategy = CurrentArtifactStrategy(expression_layer=True, expression_inputs=provider,
                                       candidates_builder=cash_candidates)
    outputs = strategy(binding, NOW)["portfolio_decisions"]
    assert len(outputs) == 4
    assert all(o["metadata"]["portfolio_net_view"]["direction"] == "FLAT" for o in outputs)
    held_output = next(o for o in outputs if o["symbol"] == "9999.TW")
    evaluated = held_output["expression"]["evaluate"]()
    assert evaluated["decision"]["reason_code"] == "close_leg"
    assert evaluated["decision"]["units"] == 10
    result = replay([evaluated["replay_input"]], None)["decisions"][0]
    assert result["record"] == evaluated["decision"]


def test_producer_flat_closes_every_held_leg():
    artifact = multi_envelope()
    binding = portfolio_binding(artifact)
    book = held()
    book["positions"].append({"underlying": "2330", "instrument_id": "ssf:X:202611",
        "direction": "SHORT", "units": 2, "notional": 200})
    def candidates(*args, **kwargs):
        return [candidate(price=102), {**candidate(price=102), "instrument_id": "ssf:X:202611",
                "side_capability": ["long", "short"]}]
    strategy = CurrentArtifactStrategy(expression_layer=True, candidates_builder=candidates,
        expression_inputs=lambda _: {"persona_book": book, "risk_budget": BUDGET})
    outs = [o for o in strategy(binding, NOW)["portfolio_decisions"] if o["symbol"] == "2330.TW"]
    assert len(outs) == 2 and len({o["decision_id"] for o in outs}) == 2
    records = [o["expression"]["evaluate"]()["decision"] for o in outs]
    assert {r["instrument_id"] for r in records} == {"eq:2330", "ssf:X:202611"}
    assert all(r["reason_code"] == "close_leg" for r in records)


def test_runtime_fills_net_zero_close_and_weak_target_reduction(tmp_path):
    """Production inputs -> producer -> persisted expression -> risk gate -> ledger."""
    artifact = multi_envelope(weight=.8)
    # Leave room for cash payment and costs at the unchanged two-times gross limit.
    artifact["expression_policy"]["parameters"]["notional_fraction_at_full_strength"] = .2
    binding = portfolio_binding(artifact)
    summary = _summary()
    provider = production_inputs(summary)
    strategy = CurrentArtifactStrategy(expression_layer=True, expression_inputs=provider,
                                       candidates_builder=cash_candidates)
    store, telemetry = InMemoryPendingSignalStore(), _Telemetry()
    producer = PaperSignalProducer(lambda _: store, strategy)
    service = PaperRuntimeService(store=store, identity=_identity(binding),
        runtime_manager_client=_RuntimeManager(_runtime_binding(binding)),
        telemetry_emitter=telemetry, lifecycle_outbox_path=tmp_path / "lifecycle.json",
        capital_pool_reader=lambda _: _pool())
    service._algo._in_process_broker = True

    def bar(day, closes):
        for row in binding["market_inputs"].values():
            row.update(closes=closes, event_time=day + "T05:30:00Z", observed_at=day + "T06:00:00Z")
        positions = service._algo.positions()
        if positions or summary.get("ledger_fill_count"):
            summary.update(ledger_fill_count=1, ledger_cash=service._algo._cash,
                positions=[{**p, "mark_price": closes[-1]} for p in positions])
        from datetime import datetime
        service._algo.Time = datetime.fromisoformat(day + "T06:00:00+00:00")
        assert producer.produce(binding, day + "T06:00:00Z") == 3, producer.degraded_bindings
        for _ in range(4):
            snapshot = service.drain_once()
        assert snapshot["status"] == "ok", snapshot
        return {p["symbol"]: p["quantity"] for p in service._algo.positions()}

    original = bar("2026-11-13", [100, 102])
    assert len(original) == 3
    reduced = bar("2026-11-16", [100, 100.1])
    assert all(0 < reduced[s] < original[s] for s in original), (original, reduced)
    closed = bar("2026-11-17", [100, 100])
    assert closed == {}
    # A new long is opened, then a short target closes it; no short-capable
    # instrument exists in this recorded candidate set.
    reopened = bar("2026-11-18", [100, 102])
    assert len(reopened) == 3
    assert bar("2026-11-19", [102, 100]) == {}
    assert bar("2026-11-20", [102, 100]) == {}
    fills = [e for e in telemetry.events if e["event_type"] == "paper_fill_simulated"]
    assert len(fills) >= 15
    assert all(p["quantity"] >= 0 for p in service._algo.positions())


def test_member_rule_exit_reduces_combined_target_through_runtime(tmp_path):
    from datetime import datetime
    from services.registry.test_candidate_pool import screen_artifact
    from services.registry.portfolio_envelope import build_portfolio_envelope
    factor = screen_artifact()
    factor["strategy_logic"].pop("screen")
    factor["strategy_logic"]["inputs"] = [factor["strategy_logic"]["inputs"][0]]
    factor["parameters"]["symbols"] = ["2330.TW"]
    factor["parameters"]["hold_bars"] = 2
    for control in factor["mutation_surface"]["controls"]:
        control["current_value"] = factor["parameters"][control["parameter_key"]]
    constant = _artifact_with_expression_policy()
    constant["parameters"].update(symbols=["2330.TW", "2317.TW", "2454.TW"], market="TW",
                                  data_source=factor["parameters"]["data_source"])
    constant["mutation_surface"]["immutable_parameters"].append("market")
    artifact = build_portfolio_envelope(artifact_id="member-exit", strategy_id="member-exit",
        version="1.0.0", members=[
            {"member_id": "factor", "artifact": factor, "risk_weight": .5},
            {"member_id": "constant", "artifact": constant, "risk_weight": .5}])
    artifact["expression_policy"]["parameters"]["notional_fraction_at_full_strength"] = .2
    binding = portfolio_binding(artifact)
    summary = _summary()
    strategy = CurrentArtifactStrategy(expression_layer=True, expression_inputs=production_inputs(summary),
                                       candidates_builder=cash_candidates)
    store, telemetry = InMemoryPendingSignalStore(), _Telemetry()
    producer = PaperSignalProducer(lambda _: store, strategy)
    service = PaperRuntimeService(store=store, identity=_identity(binding),
        runtime_manager_client=_RuntimeManager(_runtime_binding(binding)), telemetry_emitter=telemetry,
        lifecycle_outbox_path=tmp_path / "exit.json", capital_pool_reader=lambda _: _pool())
    service._algo._in_process_broker = True

    def run(day, closes):
        for row in binding["market_inputs"].values():
            row.update(closes=closes, features={"close": closes},
                       event_time=day+"T05:30:00Z", observed_at=day+"T06:00:00Z",
                       # Deliberately wrong persona-level state must be ignored.
                       current_direction="SHORT", holding_bars=999)
        service._algo.Time = datetime.fromisoformat(day+"T06:00:00+00:00")
        positions = service._algo.positions()
        if positions:
            summary.update(ledger_fill_count=1, ledger_cash=service._algo._cash,
                positions=[{**p, "mark_price": closes[-1]} for p in positions])
        outputs = strategy(binding, day+"T06:00:00Z")["portfolio_decisions"]
        assert producer.produce(binding, day+"T06:00:00Z") == 3
        for _ in range(4):
            service.drain_once()
        return outputs, {p["symbol"]: p["quantity"] for p in service._algo.positions()}

    initial, before = run("2026-11-13", [100, 102])
    exited, after = run("2026-11-16", [100, 102, 104])
    view = next(o for o in exited if o["symbol"] == "2330.TW")
    assert view["metadata"]["portfolio_member_views"]["factor"]["view"]["direction"] == "FLAT"
    assert view["metadata"]["portfolio_net_view"]["strength"] == .5
    assert 0 < after["2330.TW"] < before["2330.TW"]


def test_member_hold_keeps_own_target():
    from services.registry.test_candidate_pool import screen_artifact
    from services.registry.portfolio_envelope import build_portfolio_envelope, evaluate_portfolio_bar
    factor = screen_artifact()
    factor["strategy_logic"].pop("screen")
    factor["strategy_logic"]["inputs"] = [factor["strategy_logic"]["inputs"][0]]
    factor["parameters"].update(symbols=["2330.TW"], hold_bars=100, entry_z=101)
    for control in factor["mutation_surface"]["controls"]:
        if control["parameter_key"] in {"entry_z", "hold_bars"}:
            control["allowed_range"]["max"] = 200
    for control in factor["mutation_surface"]["controls"]:
        control["current_value"] = factor["parameters"][control["parameter_key"]]
    artifact = build_portfolio_envelope(artifact_id="hold-target", strategy_id="hold-target",
        version="1.0.0", members=[{"member_id": "a", "artifact": factor, "risk_weight": .5},
                                {"member_id": "b", "artifact": factor, "risk_weight": .5}])
    snapshots = {"2330.TW": {"closes": [102, 100], "features": {"close": [102, 100]},
                           "event_time": NOW, "source_ref": "test-only:hold-bars"}}
    result = evaluate_portfolio_bar(artifact, snapshots)["2330.TW"]
    assert result["view"]["direction"] == "LONG"
    assert result["view"]["strength"] == 1


def test_evolution_replay_gate_supports_target_positions_and_rejects_unknown_policy():
    from services.evolution.program_qualification import expression_replay_gate
    view = {"decision_id": "gate", "underlying": "2330", "direction": "LONG", "strength": .5}
    allocation = ep.allocate_targets({"2330.TW": view}, BUDGET, PARAMS)
    record = {"decision_id": "gate", "policy_kind": ep.TARGET_POLICY_KIND,
        "view": {**view, "target_notional": allocation["targets"]["2330.TW"]},
        "bar_allocation": allocation, "budget": BUDGET, "book_before": {},
        "candidates": [candidate()], "parameters": PARAMS, "marks": {"eq:2330": [101]}}
    assert expression_replay_gate(PARAMS, PARAMS, [record])["passed"]
    assert expression_replay_gate(PARAMS, PARAMS, [{**record, "policy_kind": "unknown"}])["reason"] == "unsupported_expression_policy"


def test_frozen_bar_reads_inputs_once_and_restart_reuses_expression_record():
    artifact = multi_envelope()
    binding = portfolio_binding(artifact)
    reads = []
    def inputs(_):
        reads.append(True)
        return {"persona_book": held(), "risk_budget": BUDGET}
    strategy = CurrentArtifactStrategy(expression_layer=True, expression_inputs=inputs,
                                       candidates_builder=cash_candidates)
    outputs = strategy(binding, NOW)["portfolio_decisions"]
    assert len(reads) == 1
    for output in outputs:
        first = output["expression"]["evaluate"]()
        assert first["replay_input"]["book_before"] == held()
    assert len(reads) == 1
    store = InMemoryPendingSignalStore()
    producer = PaperSignalProducer(lambda _: store, strategy)
    assert producer.produce(binding, NOW) == 3
    def broken(*args, **kwargs):
        raise AssertionError("completed expression must not be recomputed")
    replacement = PaperSignalProducer(lambda _: store,
        CurrentArtifactStrategy(expression_layer=True, expression_inputs=inputs, candidates_builder=broken))
    # Admitted decisions reuse their stored expression even before consumer drain.
    replacement.produce(binding, NOW)


def test_unreadable_budget_replays_the_recorded_no_trade():
    from services.execution.lean_runtime.expression_inputs import ExpressionInputsUnavailable
    def unavailable(_):
        raise ExpressionInputsUnavailable("ledger", "owner read unavailable")
    strategy = CurrentArtifactStrategy(expression_layer=True, expression_inputs=unavailable,
                                       candidates_builder=cash_candidates)
    output = strategy(portfolio_binding(multi_envelope()), NOW)["portfolio_decisions"][0]
    recorded = output["expression"]["evaluate"]()
    assert "budget_unavailable: ledger" in recorded["decision"]["detail"]
    assert replay([recorded["replay_input"]], None)["decisions"][0]["record"] == recorded["decision"]
