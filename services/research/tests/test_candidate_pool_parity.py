"""Recorded official Taiwan bars through real backtest and paper entry points."""
import copy
import json

import pytest

from services.registry.test_candidate_pool import FIXTURE, recorded_market, screen_artifact
from services.registry.candidate_pool import MARKET_FEATURE_KEY
from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy, PaperSignalProducer
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.test_current_artifact_signal import _binding
from services.research.vectorbt.adapter.vectorbt_adapter import (
    BacktestConfig, GovernedVectorbtInputAdapter, PRIMARY_BACKEND, VectorbtBackend,
    _screened_artifact_view_sequence, run_replication_backtest_protocol, run_vectorbt_workflow,
)


def recorded_dataset():
    market = recorded_market()
    records = [{"instrument": symbol, "date": row["event_time"][:10],
                **{k: row[k] for k in ("open", "high", "low", "close", "volume")}}
               for symbol, payload in market["symbols"].items() for row in payload["bars"]]
    return {"dataset_id": "recorded-twse-screen-202609-202610", "strategy_id": "screen-replay",
            "data_frequency": "daily", "source_dataset_refs": market["source_refs"],
            "records": records, "candidate_pool_market": market}


def paper_binding(artifact, market, day, positions=None):
    binding = _binding(artifact, binding_id="rb-screen-recorded", include_market_input=False)
    binding["features"] = {MARKET_FEATURE_KEY: market}
    snapshots = {}
    for symbol, payload in market["symbols"].items():
        bars = [b for b in payload["bars"] if b["event_time"][:10] <= day]
        snapshots[symbol] = {
            "symbol": symbol, "closes": [b["close"] for b in bars], "event_time": day,
            "observed_at": f"{day}T12:00:00Z", "source_ref": market["source_refs"][0],
            **(positions or {}).get(symbol, {}),
        }
    binding["market_inputs"] = snapshots
    return binding


def test_real_vectorbt_entry_and_paper_replay_match_every_bar(monkeypatch):
    monkeypatch.setenv("PANTHEON_VECTORBT_BACKEND", "real")
    artifact = screen_artifact()
    # The pool must also trade the recorded member outside the provisioning list.
    artifact["parameters"]["symbols"] = ["2330.TW"]
    dataset = recorded_dataset()
    prepared = GovernedVectorbtInputAdapter().prepare(dataset)
    sequences = _screened_artifact_view_sequence(prepared, artifact)
    result = run_vectorbt_workflow(dataset, backend=VectorbtBackend(),
                                  config=BacktestConfig(strategy_artifact=artifact))
    assert result.backtest_result.backend == PRIMARY_BACKEND
    snapshots = result.artifact_bundle["candidate_pool_snapshots"]
    assert result.artifact_bundle["survivorship_limited"] is True
    assert len(snapshots) == 39
    assert any("2317.TW" in s["members"] for s in snapshots)

    strategy = CurrentArtifactStrategy()
    positions = {s: {"current_direction": "FLAT", "holding_bars": 0} for s in prepared.instruments}
    exit_count = 0
    for index, day in enumerate(prepared.dates_by_instrument["2330.TW"]):
        for position in positions.values():
            if position["current_direction"] != "FLAT":
                position["holding_bars"] += 1
        binding = paper_binding(artifact, dataset["candidate_pool_market"], day, positions)
        outputs = strategy(binding, f"{day}T12:00:00Z")["portfolio_decisions"]
        assert len(outputs) == 2
        for output in outputs:
            symbol = output["symbol"]
            expected = sequences[symbol][index]
            actual = output["metadata"]["candidate_pool_view"]
            assert actual == {k: v for k, v in expected.items() if k != "candidate_pool_snapshot"}
            assert output["metadata"]["candidate_pool_snapshot"] == expected["candidate_pool_snapshot"]
            assert expected["candidate_pool_snapshot"] == snapshots[index]
            old = positions[symbol]
            positions[symbol] = {"current_direction": actual.get("target_direction", old["current_direction"]),
                                 "holding_bars": actual.get("holding_bars", old["holding_bars"])}
            exit_count += actual.get("exit_reason") == "left_candidate_pool"
    assert exit_count > 0


def test_paper_persists_pool_even_on_hold_day_and_digest_changes_decision_key():
    artifact, market = screen_artifact(), recorded_market()
    artifact["parameters"]["pool_min"] = 2  # rank keeps one, so everyone must HOLD
    for control in artifact["mutation_surface"]["controls"]:
        control["current_value"] = artifact["parameters"][control["parameter_key"]]
    binding = paper_binding(artifact, market, "2026-09-02")
    store = InMemoryPendingSignalStore()
    producer = PaperSignalProducer(store_for=lambda _: store, strategy=CurrentArtifactStrategy())
    producer.produce(binding, "2026-09-02T12:00:00Z")
    assert producer.degraded_bindings == {}
    before = CurrentArtifactStrategy()(binding, "2026-09-02T12:00:00Z")["portfolio_decisions"]
    snapshot = before[0]["metadata"]["candidate_pool_snapshot"]
    assert all(o["action"] == "HOLD" for o in before)
    assert store.get_decision_record(f"candidate-pool-{binding['binding_id']}-{snapshot['digest']}") == snapshot
    # Fault injection changes yesterday's membership, keeping artifact and bar
    # identities fixed; the pool digest alone must alter the decision identity.
    changed = copy.deepcopy(binding)
    changed["features"][MARKET_FEATURE_KEY]["symbols"]["2317.TW"]["bars"][0]["suspended"] = True
    after = CurrentArtifactStrategy()(changed, "2026-09-02T12:00:00Z")["portfolio_decisions"]
    assert before[0]["decision_id"] != after[0]["decision_id"]


def test_survivorship_limited_history_cannot_enter_replication_admission():
    result = run_replication_backtest_protocol(recorded_dataset(), screen_artifact())
    assert result["status"] == "survivorship_limited"
    assert result["survivorship_limited"] is True


def test_fixture_ohlcv_is_parsed_from_recorded_official_rows():
    fixture = json.loads(FIXTURE.read_text())
    official = {}
    for recording in fixture["provenance"]["recordings"]:
        assert recording["url"].startswith("https://www.twse.com.tw/")
        code = recording["url"].split("stockNo=")[1]
        for row in recording["rows"]:
            year, month, day = map(int, row[0].split("/"))
            key = (code + ".TW", f"{year + 1911}-{month:02d}-{day:02d}")
            official[key] = [float(row[i].replace(",", "")) for i in (3, 4, 5, 6, 1, 2)]
    for symbol, payload in fixture["market"]["symbols"].items():
        for bar in payload["bars"]:
            assert [bar[k] for k in ("open", "high", "low", "close", "volume", "traded_value")] == official[
                symbol, bar["event_time"][:10]]
