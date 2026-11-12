"""Screen contribution test in the replication gate, on recorded official TWSE bars.

Every test goes through ``ReplicationGate.evaluate_candidate`` (the production entry
point, which also runs the real vectorbt protocol). Market data is the recorded
fixture; the only mutations are explicit, named test-side injections.
"""
import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from services.registry.strategy_artifact import strategy_artifact_checksum
from services.registry.test_candidate_pool import screen_artifact
from services.research.replication.gate import ReplicationGate
from services.research.replication.gate_config import GateConfig
from services.research.replication import test_gate as gate_tests

FIXTURE = Path(__file__).parents[1] / "fixtures" / "recorded_screen_contribution_tw.json"

# The recorded slice is two months, not two years: relax only the history/round-trip
# minimums. The 70/30 split, folds, costs and deflation are the production defaults.
SHORT_HISTORY = dict(min_pit_history_days=30, min_pit_history_bars=20, min_oos_round_trips=1)


def recorded_market(*, attest_coverage: bool):
    market = json.loads(FIXTURE.read_text())["market"]
    if attest_coverage:  # test-only coverage attestation; the recording itself attests none
        market["includes_delisted"] = market["includes_suspended"] = True
    return market


def dataset(market):
    records = [
        {"instrument": symbol, "date": bar["event_time"][:10],
         **{k: bar[k] for k in ("open", "high", "low", "close", "volume")}}
        for symbol, payload in market["symbols"].items() for bar in payload["bars"]
    ]
    return {"dataset_id": "recorded-twse-screen-contribution", "strategy_id": "screen-contribution",
            "data_frequency": "daily", "source_dataset_refs": market["source_refs"],
            "records": records, "candidate_pool_market": market}


def artifact(side="top"):
    art = screen_artifact()
    art["parameters"]["symbols"] = ["2330.TW"]
    art["strategy_logic"]["screen"]["steps"][1]["side"] = side
    return art


def build_request(art, ds, *, with_dataset=True):
    request = gate_tests.TestReplicationGate._build_request(None)
    request.backtest_evaluation = None
    request.research_handoff["strategy_artifact"] = art
    if with_dataset:
        request.research_handoff["backtest_dataset"] = ds
    request.metadata = {
        "strategy_artifact_checksum": strategy_artifact_checksum(art),
        "dataset_version_id": ds["dataset_id"],
    }
    return request


def backtest_result(response):
    return next(r for r in response.results if r.criterion_id == "real_backtest_validation")


def evaluate(art, *, attest=True, with_dataset=True, **config):
    gate = ReplicationGate(config=GateConfig(**{**SHORT_HISTORY, **config}))
    request = build_request(art, dataset(recorded_market(attest_coverage=attest)),
                            with_dataset=with_dataset)
    response = gate.evaluate_candidate(request)
    return gate, request, response, backtest_result(response)


def test_screen_that_beats_whole_boundary_control_is_measured_and_both_variants_count():
    gate, request, response, result = evaluate(artifact("top"))
    contribution = result.details["screen_contribution"]
    assert contribution["status"] == "contributes"
    # the recorded OOS window has zero-variance returns for the screened run, so the
    # comparison falls back to after-cost total return
    assert contribution["comparison_metric"] == "oos_total_return"
    assert contribution["screened_oos_total_return"] > contribution["control_oos_total_return"]
    assert contribution["oos_improvement"] == pytest.approx(
        contribution["screened_oos_total_return"] - contribution["control_oos_total_return"])
    assert contribution["boundary_symbols"] == ["0050.TW", "2317.TW", "2330.TW"]
    # screen variant + entry variant each incremented the family counter
    assert contribution["trial_accounting"] == {
        "screen_variant_trials": 1, "entry_variant_trials": 1, "family_trial_count_after": 2}
    assert gate.get_family_trial_count(result.details["family_id"]) == 2
    assert result.details["trial_count"] == 2
    # the admission record carries the measurement
    assert response.metadata["backtest_summary"]["screen_contribution"] == contribution
    # the control is a persisted run bound to the entry-only artifact checksum
    control = gate.backtest_run_store.get(contribution["control_backtest_run_id"])
    assert control["artifact_checksum"] == contribution["control_artifact_checksum"]
    # the primary checks still ran on the screened candidate
    assert {"oos_sharpe_positive_after_costs", "walk_forward_consistent_across_folds"} <= set(
        result.details["checks"])


def test_screen_that_adds_nothing_is_dropped_and_reregistered_entry_only():
    # Bottom-ranked screen concentrates the pool on the worse symbol: it cannot beat
    # the same rules over the whole boundary.
    gate, request, response, result = evaluate(artifact("bottom"))
    contribution = result.details["screen_contribution"]
    assert contribution["oos_improvement"] <= 0.0
    assert contribution["status"] == "screen_dropped"
    assert not result.passed and result.details["status"] == "screen_dropped"
    assert response.admission_status.value == "rejected"
    registration = contribution["re_registration"]
    assert registration["candidate_kind"] == "entry_only"
    # entry-only: no selection step remains, only the governed daily boundary
    (only_step,) = registration["strategy_artifact"]["strategy_logic"]["screen"]["steps"]
    assert only_step["kind"] == "filter"
    assert registration["strategy_artifact"]["parameters"]["symbols"] == contribution["boundary_symbols"]
    assert registration["artifact_checksum"] == strategy_artifact_checksum(
        registration["strategy_artifact"])
    assert registration["reason"] in response.summary
    assert contribution["trial_accounting"]["entry_variant_trials"] == 1
    assert gate.get_family_trial_count(result.details["family_id"]) == 2
    # the re-registered entry-only version is bound to a persisted run
    assert gate.backtest_run_store.get(registration["backtest_run_id"]) is not None


def test_survivorship_limited_history_is_not_admitted_and_reason_recorded():
    gate, request, response, result = evaluate(artifact("top"), attest=False)
    assert response.admission_status.value == "rejected"
    assert not result.passed
    assert result.details["status"] == "survivorship_limited"
    assert result.details["survivorship_limited"] is True
    assert "delisted and suspended" in result.details["reason"]
    assert result.details["reason"] in response.summary
    assert gate.get_family_trial_count(request.candidate_id) == 0


def test_screened_candidate_without_market_dataset_fails_closed():
    gate, _, response, result = evaluate(artifact("top"), with_dataset=False)
    # no dataset and no recorded evaluation: no backtest can exist
    assert not result.passed and response.admission_status.value == "rejected"


def test_screened_evaluation_without_control_inputs_fails_closed():
    """A pre-computed screened evaluation cannot be admitted without the control inputs."""
    gate = ReplicationGate(config=GateConfig(**SHORT_HISTORY))
    art, ds = artifact("top"), dataset(recorded_market(attest_coverage=True))
    request = build_request(art, ds)
    first = gate.evaluate_candidate(request)  # runs and persists the protocol
    evaluation = copy.deepcopy(request.backtest_evaluation)
    assert evaluation["status"] == "completed"
    bare = build_request(art, ds, with_dataset=False)
    bare.backtest_evaluation = evaluation
    result = backtest_result(gate.evaluate_candidate(bare))
    assert not result.passed
    assert result.details["reason"] == "screen_contribution_inputs_missing"
    assert first is not None


def test_candidate_without_screen_keeps_primary_checks_and_counts_one_trial():

    gate = ReplicationGate()
    request = gate_tests.TestReplicationGate._build_request(None)
    gate_tests._bind_run(request, gate)
    result = backtest_result(gate.evaluate_candidate(request))
    assert "screen_contribution" not in result.details
    assert result.details["trial_count"] == 1
    assert gate.get_family_trial_count(result.details["family_id"]) == 1


def test_control_keeps_daily_boundary_exclusions_for_suspended_symbols():
    """0050 is suspended on every recorded day (test-side injection of the flag only).

    The screened run already excludes it; the whole-boundary control must too, so a
    suspended name can neither trade in the control nor flatter the contribution.
    """
    from services.registry.candidate_pool import build_candidate_pool_snapshot

    market = recorded_market(attest_coverage=True)
    for bar in market["symbols"]["0050.TW"]["bars"]:
        bar["suspended"] = True
    gate = ReplicationGate(config=GateConfig(**SHORT_HISTORY))
    ds = dataset(market)
    request = build_request(artifact("top"), ds)
    result = backtest_result(gate.evaluate_candidate(request))
    contribution = result.details["screen_contribution"]
    control_run = gate.backtest_run_store.get(contribution["control_backtest_run_id"])
    assert control_run is not None
    control_art = ReplicationGate._whole_boundary_control_artifact(
        artifact("top"), contribution["boundary_symbols"])
    session = market["trading_sessions"][-1]
    snapshot = build_candidate_pool_snapshot(control_art, market, f"{session}T23:00:00+08:00")
    assert "0050.TW" not in snapshot["members"]
    assert {"symbol": "0050.TW", "reason": "suspended"} in snapshot["excluded"]
    # and the control run itself never takes a position in the suspended symbol
    from services.research.vectorbt.adapter import vectorbt_adapter as va
    prepared = va.GovernedVectorbtInputAdapter().prepare(ds)
    views = va._screened_artifact_view_sequence(prepared, control_art)
    inst = next(i for i in views if i.startswith("0050"))
    assert {v["action"] for v in views[inst]} == {"HOLD"}
