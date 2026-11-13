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
    """PaperSignalProducer -> persisted expression -> paper order gate -> ledger."""
    artifact = multi_envelope(weight=.8)
    binding = portfolio_binding(artifact)
    summary = _summary()
    provider = production_inputs(summary)
    strategy = CurrentArtifactStrategy(expression_layer=True, expression_inputs=provider,
                                       candidates_builder=cash_candidates)
    store, telemetry = InMemoryPendingSignalStore(), _Telemetry()
    producer = PaperSignalProducer(lambda _: store, strategy)
    service = PaperRuntimeService(store=store, identity=_identity(binding),
        runtime_manager_client=_RuntimeManager(_runtime_binding(binding)),
        telemetry_emitter=telemetry, lifecycle_outbox_path=tmp_path / "lifecycle.json")
    service._algo._in_process_broker = True
    assert producer.produce(binding, NOW) == 3, producer.degraded_bindings
    for _ in range(4):
        snapshot = service.drain_once()
    fills = [e for e in telemetry.events if e["event_type"] == "paper_fill_simulated"]
    assert fills, snapshot
