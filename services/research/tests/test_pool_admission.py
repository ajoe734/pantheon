"""Recorded Taiwan bars exercise the production interpreter and netting backtest."""
import copy
import importlib
import json
from pathlib import Path

import pytest

from services.evolution.test_approved_baselines import genuine_entry
from services.research.pool_admission import evaluate_admission

FIXTURE = Path(__file__).with_name("fixtures") / "pool_admission_tw_recorded.json"
load_policy = importlib.import_module("services.control-plane.persona.pool_policy").load_policy


def recorded_dataset():
    payload = json.loads(FIXTURE.read_text())
    records = []
    for item in payload["responses"]:
        for row in item["response"]["data"]:
            year, month, day = map(int, row[0].split("/"))
            number = lambda value: float(value.replace(",", ""))
            records.append({"instrument": item["symbol"] + ".TWSE", "date": f"{year+1911:04}-{month:02}-{day:02}",
                            "open": number(row[3]), "high": number(row[4]), "low": number(row[5]),
                            "close": number(row[6]), "volume": number(row[1])})
    return {"dataset_id": "recorded:twse:202409-202412", "strategy_id": "pool-admission",
            "data_frequency": "daily", "source_dataset_refs": [i["source_url"] for i in payload["responses"]],
            "records": records}


def entries_and_members():
    one = genuine_entry(tenant="tenant-a")
    two = genuine_entry("recorded_backtest_tw_2330_2317", ("2330.TW", "2317.TW"), tenant="tenant-a")
    def member(entry, weight):
        return {"artifact_ref": entry["registry_id"], "checksum": entry["checksum"],
                "stage": "active", "risk_weight": weight, "baseline_ref": entry["approval_decision_id"],
                "joined_at": "2024-01-01T00:00:00Z"}
    return {one["registry_id"]: one, two["registry_id"]: two}, member(one, .7), member(two, .1)


def test_recorded_backtest_uses_identical_oos_window_and_pins_artifacts():
    entries, current, candidate = entries_and_members()
    result = evaluate_admission(dataset=recorded_dataset(), current=[current],
        proposed=[current, candidate], candidate=candidate, entries=entries, trial_count=3, policy=load_policy())
    assert result["return_observations"] >= 20
    assert result["oos_start"] == "2024-11-27" and result["oos_end"] == "2024-12-31"
    assert result["persona_trial_count"] == 3 and result["sharpe_haircut"] > 0
    assert result["source_dataset_refs"] == recorded_dataset()["source_dataset_refs"]
    assert result["correlations"][current["artifact_ref"]] > .5
    assert not result["admitted"]
    corrupt = copy.deepcopy(entries)
    corrupt[candidate["artifact_ref"]]["metadata"]["strategy_artifact"]["strategy_logic"]["kind"] = "unknown"
    with pytest.raises(ValueError):
        evaluate_admission(dataset=recorded_dataset(), current=[current], proposed=[current, candidate],
            candidate=candidate, entries=corrupt, trial_count=3, policy=load_policy())


def test_missing_dates_and_source_data_fail_closed():
    entries, current, candidate = entries_and_members()
    for dataset in ({}, {**recorded_dataset(), "source_dataset_refs": []},
                    {**recorded_dataset(), "records": recorded_dataset()["records"][1:]}):
        with pytest.raises(ValueError):
            evaluate_admission(dataset=dataset, current=[current], proposed=[current, candidate],
                candidate=candidate, entries=entries, trial_count=1, policy=load_policy())


def _always_long(entries, entry_ref):
    from services.registry.strategy_artifact import strategy_artifact_checksum
    changed = copy.deepcopy(entries[entry_ref])
    artifact = changed["metadata"]["strategy_artifact"]
    artifact["strategy_logic"].update(positive_action="BUY", non_positive_action="BUY")
    artifact["parameters"]["zero_momentum_action"] = "BUY"
    changed["checksum"] = strategy_artifact_checksum(artifact)
    return changed


def test_idle_pool_first_member_must_clear_every_criterion_on_its_own():
    """Pool with no live member: its Sharpe is 0, so "combined better" means the candidate clears it alone."""
    entries, one, _ = entries_and_members()
    entries["long"] = _always_long(entries, one["artifact_ref"])
    removed = {**one, "stage": "removed", "risk_weight": 0}
    results = {}
    for ref in ("long", one["artifact_ref"]):
        trial = {**one, "artifact_ref": ref, "stage": "trial", "risk_weight": .1}
        results[ref] = evaluate_admission(dataset=recorded_dataset(), current=[removed], proposed=[removed, trial],
            candidate=trial, entries=entries, trial_count=1, policy=load_policy())
    passing, failing = results["long"], results[one["artifact_ref"]]
    assert passing["pool_idle"] and passing["pool_oos_sharpe"] == 0.0 and passing["correlations"] == {}
    assert passing["admitted"] and passing["combined_oos_sharpe"] > load_policy().minimum_sharpe_improvement
    assert not failing["admitted"]
    assert set(failing["reasons"]) == {"combined_oos_sharpe_did_not_improve",
                                       "candidate_oos_sharpe_below_persona_trial_threshold"}
    assert failing["required_candidate_sharpe"] == passing["required_candidate_sharpe"]  # same deflated bar
