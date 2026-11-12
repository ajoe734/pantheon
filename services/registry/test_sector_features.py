"""Sector screens through the actual strategy entry point using recorded OpenAPI rows."""
import copy
import json
from pathlib import Path

import pytest

from services.registry import factor_rule as fr
from services.registry.candidate_pool import MARKET_FEATURE_KEY
from services.registry.sector_features import classification_at, sector_flow_observations as derive_sector_flows
from services.registry.strategy_artifact import evaluate_strategy_view, validate_strategy_artifact
from services.registry.test_candidate_pool import screen_artifact
from services.source_ingestion import feature_catalogue as fc
from services.source_ingestion.connectors import taiwan_official as tw

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "docs/deployment/evidence/TW-INDUSTRY-CLASSIFICATION-DATASET-20261112"
FIXTURE = REPO / "services/source_ingestion/tests/fixtures/tpex_company_profile_recorded.json"
ENTITIES = ("3105.TPEX", "6488.TPEX", "8069.TPEX")


def sector_flow_observations(observations, *, decision_time):
    return derive_sector_flows(observations, decision_time=decision_time, required_entities=ENTITIES)


def recorded_observations():
    profile = json.loads(FIXTURE.read_text())
    inputs = json.loads((EVIDENCE / "recorded_inputs.json").read_text())
    symbols = {r["SecuritiesCompanyCode"] for r in inputs["flows"]["records"]}
    rows = tw.normalize_industry_classification_rows(
        "TPEx", [r for r in profile["records"] if r["SecuritiesCompanyCode"] in symbols],
        source_dataset="mopsfin_t187ap03_O", api_endpoint=profile["source_url"], ingest_time=profile["recorded_at"],
    )
    obs = [o for row in rows for o in fc.observations_from_row("tw_industry_classification", row)]
    adapter = tw.TaiwanOfficialMarketDatasetAdapter(max_records=50)
    records = adapter.records_from_payload("tw_institutional_flow", "TPEx", inputs["flows"]["records"])
    for record in records:
        obs.extend(fc.observations_from_row("tw_institutional_flows", record.metadata["normalized_row"]))
    return obs, inputs


def recorded_market():
    observations, inputs = recorded_observations()
    adapter = tw.TaiwanOfficialMarketDatasetAdapter(max_records=50)
    rows = adapter.normalized_rows_from_payload("tw_price_daily", "TPEx", inputs["price"]["records"],
        source_dataset="tpex_mainboard_daily_close_quotes", api_endpoint=inputs["price"]["source_url"])
    symbols = {}
    for row in rows:
        symbol = row["symbol_canonical"].replace(".TPEX", ".TWO")
        features = {}
        for observation in observations:
            if observation["entity"] == row["symbol_canonical"]:
                features.setdefault(f"{observation['dataset']}.{observation['field']}", []).append(observation)
        symbols[symbol] = {"dataset": "tw_price_daily", "bars": [{
            **row, "event_time": row["date"], "available_time": "2026-11-12T14:00:00+08:00",
            "traded_value": float(row["value"]), "suspended": False,
        }], "features": features}
    return {"schema_version": "candidate_pool_market.v1", "scope": "all_governed_tw",
            "source_refs": [inputs["price"]["source_url"], inputs["flows"]["source_url"], str(FIXTURE.relative_to(REPO))],
            "trading_sessions": ["2026-11-12"], "calendar_coverage_through": "2026-11-14",
            "includes_delisted": False, "includes_suspended": True, "symbols": symbols}


def artifact_for(expression):
    artifact = screen_artifact()
    artifact["strategy_logic"]["inputs"] += [
        {"name": "industry", "dataset": "tw_industry_classification", "field": "industry_code", "availability_lag_days": 0},
        {"name": "sector_flow", "dataset": "tw_sector_flows", "field": "net_shares", "availability_lag_days": 0},
    ]
    artifact["strategy_logic"]["screen"]["steps"] = [{
        "kind": "filter", "expression": expression, "refresh": "daily", "group_by": {"industry_input": "industry"},
    }]
    return artifact


def evaluate(artifact, market, symbol="3105.TWO"):
    return evaluate_strategy_view(artifact, [], features={MARKET_FEATURE_KEY: market},
                                  decision_time="2026-11-13", symbol=symbol)


def test_catalogue_declares_observation_and_sector_availability():
    for dataset, field in [("tw_industry_classification", "industry_code"), ("tw_sector_flows", "net_shares")]:
        assert fc.lag_days_for(dataset, field) == 0
        assert fr.get_feature_definition(dataset, field).release_cadence == "daily"


def test_sector_sum_uses_real_flows_and_latest_known_industry():
    obs, inputs = recorded_observations()
    assert not classification_at(obs, decision_time="2026-11-11")
    assert not sector_flow_observations(obs, decision_time="2026-11-12T14:27:00Z")
    sector = sector_flow_observations(obs, decision_time="2026-11-13")
    assert len(sector) == 3
    by_entity = {o["entity"]: o for o in sector}
    expected = {}
    classes = classification_at(obs, decision_time="2026-11-13")
    for row in inputs["flows"]["records"]:
        entity = row["SecuritiesCompanyCode"] + ".TPEX"
        code = classes[entity]["value"]
        expected[code] = expected.get(code, 0) + int(row["TotalDifference"])
    for row in sector:
        assert row["value"] == expected[row["industry_code"]]
        assert row["available_time"] >= classes[row["entity"]]["available_time"]
        assert row["event_time"] == "2026-11-12"
    assert by_entity["3105.TPEX"]["value"] == by_entity["6488.TPEX"]["value"]
    features = fc.feature_inputs_for_factor_rule(obs, [{"name": "sector", "dataset": "tw_sector_flows", "field": "net_shares"}],
        entity="3105.TPEX", decision_time="2026-11-13", sector_entities=ENTITIES)
    assert features["sector"][0]["value"] == by_entity["3105.TPEX"]["value"]


@pytest.mark.parametrize("fault", ["missing_flow", "missing_industry", "stale", "prior_day", "late_profile", "late_revision", "nan"])
def test_incomplete_or_pre_observation_sector_fails_closed(fault):
    obs, _ = recorded_observations()
    if fault == "missing_flow":
        obs = [o for o in obs if not (o["entity"] == "3105.TPEX" and o["field"] == "dealer_net_shares")]
    elif fault == "missing_industry":
        obs = [o for o in obs if not (o["entity"] == "3105.TPEX" and o["field"] == "industry_code")]
    else:
        for o in obs:
            if fault == "stale" and o["entity"] == "3105.TPEX" and o["dataset"] == "tw_institutional_flows":
                o["event_time"] = "2026-11-11"  # explicit stale-session fault
            if fault == "prior_day" and o["dataset"] == "tw_institutional_flows":
                o["event_time"] = "2026-11-11"
            if fault == "late_profile" and o["field"] == "industry_code":
                o["available_time"] = "2026-11-12T17:00:00Z"  # next Taipei session
            if fault == "late_revision" and o["field"] == "dealer_net_shares":
                o["available_time"] = "2026-11-14T00:00:00Z"
            if fault == "nan" and o["field"] == "dealer_net_shares":
                o["value"] = float("nan")
    assert sector_flow_observations(obs, decision_time="2026-11-13") == []


def test_group_and_industry_neutralize_run_in_production_strategy_entry():
    market = recorded_market()
    artifact = artifact_for({"op": "industry_neutralize", "args": [{"ref": "price"}]})
    validate_strategy_artifact(artifact)
    view = evaluate(artifact, market)
    snapshot = view["candidate_pool_snapshot"]
    prices = {s: p["bars"][0]["close"] for s, p in market["symbols"].items()}
    expected = {"3105.TWO": (prices["3105.TWO"] - prices["6488.TWO"]) / 2,
                "6488.TWO": (prices["6488.TWO"] - prices["3105.TWO"]) / 2,
                "8069.TWO": 0}
    assert snapshot["steps"][0]["scores"] == expected
    assert snapshot["members"] == sorted(s for s, v in expected.items() if v > 0)
    assert snapshot["data_status"] == "ok"
    assert snapshot["survivorship_limited"] is True
    # Missing classification is a declared data hold, not a guessed category.
    market["symbols"]["3105.TWO"]["features"].pop("tw_industry_classification.industry_code")
    missing = evaluate(artifact, market)
    assert missing["action"] == "HOLD" and missing["status"] == "needs_data"
    assert missing["candidate_pool_snapshot"]["members"] == []


def test_sector_screen_derivation_runs_through_production_entry_and_missing_holds():
    artifact = artifact_for({"ref": "sector_flow"})
    market = recorded_market()
    view = evaluate(artifact, market)
    obs, _ = recorded_observations()
    expected = {o["entity"].replace(".TPEX", ".TWO"): o["value"]
                for o in sector_flow_observations(obs, decision_time="2026-11-13")}
    assert view["candidate_pool_snapshot"]["steps"][0]["scores"] == expected
    market["symbols"]["6488.TWO"]["features"].pop("tw_institutional_flows.dealer_net_shares")
    assert evaluate(artifact, market)["status"] == "needs_data"


def test_profiles_and_sector_future_revisions_do_not_change_past():
    obs, _ = recorded_observations()
    before = sector_flow_observations(obs, decision_time="2026-11-13")
    revisions = [{**o, "available_time": "2026-11-14T00:00:00Z", "value": None} for o in obs]
    assert sector_flow_observations([*obs, *revisions], decision_time="2026-11-13") == before


def test_neutralization_requires_group_and_rejects_unaligned_temporal_wrappers():
    artifact = artifact_for({"op": "industry_neutralize", "args": [{"ref": "price"}]})
    artifact["strategy_logic"]["screen"]["steps"][0].pop("group_by")
    with pytest.raises(fr.FactorRuleValidationError, match="requires governed group_by"):
        fr.validate_factor_rule_logic(artifact["strategy_logic"], artifact["parameters"])
    artifact = artifact_for({"op": "ts_mean", "args": [
        {"op": "industry_neutralize", "args": [{"ref": "price"}]}, {"const_param": "pool_window"}]})
    with pytest.raises(fr.FactorRuleValidationError, match="cross-sectional"):
        fr.validate_factor_rule_logic(artifact["strategy_logic"], artifact["parameters"])


def test_real_paper_entry_uses_identical_sector_snapshot():
    from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy
    from services.research.tests.test_candidate_pool_parity import paper_binding

    artifact = artifact_for({"op": "industry_neutralize", "args": [{"ref": "price"}]})
    market = recorded_market()
    expected = evaluate(artifact, market)["candidate_pool_snapshot"]
    binding = paper_binding(artifact, market, "2026-11-13")
    decisions = CurrentArtifactStrategy()(binding, "2026-11-13T12:00:00Z")["portfolio_decisions"]
    assert len(decisions) == 3
    assert all(o["metadata"]["candidate_pool_snapshot"] == expected for o in decisions)
    for p in market["symbols"].values():
        p["features"].pop("tw_industry_classification.industry_code")
    binding = paper_binding(artifact, market, "2026-11-13")
    decisions = CurrentArtifactStrategy()(binding, "2026-11-13T12:00:00Z")["portfolio_decisions"]
    assert all(o["action"] == "HOLD" for o in decisions)
    assert all(o["metadata"]["candidate_pool_view"]["status"] == "needs_data" for o in decisions)


def test_forward_observations_keep_original_history_and_do_not_remap_prior_flows():
    obs, _ = recorded_observations()
    first = classification_at(obs, decision_time="2026-11-13")
    before = sector_flow_observations(obs, decision_time="2026-11-13")
    # Fault injection models a later category correction, not a historical data claim.
    correction = {**first["3105.TPEX"], "event_time": "2026-11-14",
                  "available_time": "2026-11-14T10:00:00Z", "value": 26}
    combined = [*obs, correction]
    assert classification_at(combined, decision_time="2026-11-13") == first
    assert classification_at(combined, decision_time="2026-11-15")["3105.TPEX"]["value"] == 26
    assert sector_flow_observations(combined, decision_time="2026-11-15") == before


def test_sector_aggregation_requires_explicit_complete_market_boundary():
    obs, _ = recorded_observations()
    assert derive_sector_flows(obs, decision_time="2026-11-13") == []
    assert derive_sector_flows(obs, decision_time="2026-11-13", required_entities=[*ENTITIES, "1240.TPEX"]) == []
    features = fc.feature_inputs_for_factor_rule(obs, [{"name": "sector", "dataset": "tw_sector_flows", "field": "net_shares"}],
        entity="3105.TPEX", decision_time="2026-11-13")
    assert features == {"sector": []}
