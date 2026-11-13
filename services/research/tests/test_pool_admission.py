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
