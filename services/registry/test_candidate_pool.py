"""Screens over recorded TWSE data; faults are explicit test-only mutations."""
import copy
import json
from pathlib import Path

import pytest

from services.registry import factor_rule as fr
from services.registry.candidate_pool import (
    MARKET_FEATURE_KEY, build_candidate_pool_snapshot, digest, evaluate_screened_views,
)
from services.registry.strategy_artifact import (
    StrategyArtifactValidationError, evaluate_strategy_view, validate_strategy_artifact,
)
from services.registry.test_factor_rule import _sample_factor_rule_artifact

FIXTURE = Path(__file__).parents[1] / "research/tests/fixtures/candidate_pool_tw_recorded.json"


def recorded_market():
    return json.loads(FIXTURE.read_text())["market"]


def screen_artifact():
    artifact = _sample_factor_rule_artifact()
    artifact["strategy_logic"] = {
        "kind": "factor_rule_v1",
        "inputs": [{"name": "price", "dataset": "tw_price_daily", "field": "close", "availability_lag_days": 0},
                   {"name": "turnover", "dataset": "tw_price_daily", "field": "volume", "availability_lag_days": 0}],
        "signal": {"ref": "price"},
        "entry": {"long_when": {"op": "gt", "args": [{"ref": "signal"}, {"const_param": "entry_z"}]},
                  "short_when": None},
        "exit": {"max_holding_bars": {"const_param": "hold_bars"}, "flat_when": None},
        "strength": {"op": "clip01", "args": [{"ref": "signal"}]},
        "screen": {
            "version": "candidate_screen.v1", "min_size": {"const_param": "pool_min"},
            "eligibility": {"min_history_bars": {"const_param": "pool_history"},
                            "liquidity_window": {"const_param": "pool_window"},
                            "min_median_traded_value": {"const_param": "pool_liquidity"}},
            "steps": [{"kind": "filter", "expression": {"op": "gt", "args": [
                {"ref": "price"}, {"const_param": "entry_z"}]}, "refresh": "daily"},
                {"kind": "rank", "expression": {"ref": "turnover"}, "side": "top",
                 "count": {"const_param": "pool_count"}, "refresh": "daily"}],
        },
    }
    values = {"pool_min": 1, "pool_history": 1, "pool_window": 1, "pool_liquidity": 1,
              "pool_count": 1}
    artifact["parameters"].update(values)
    for key, value in values.items():
        artifact["mutation_surface"]["controls"].append({
            "parameter_key": key, "value_type": "integer", "current_value": value,
            "allowed_range": {"min": 1, "max": 100000000000}, "step": 1,
        })
    return artifact


def test_optional_screen_schema_and_legacy_shape():
    artifact = screen_artifact()
    validate_strategy_artifact(artifact)
    legacy = _sample_factor_rule_artifact()
    assert "screen" not in fr.validate_factor_rule_logic(
        legacy["strategy_logic"], legacy["parameters"], legacy["mutation_surface"])
    assert fr.validate_factor_rule_logic(artifact["strategy_logic"], artifact["parameters"],
                                         artifact["mutation_surface"])["screen"] == artifact["strategy_logic"]["screen"]


def test_ordered_filter_rank_and_bottom_use_current_set():
    artifact, market = screen_artifact(), recorded_market()
    snapshot = build_candidate_pool_snapshot(artifact, market, "2026-09-02")
    assert snapshot["members"] == ["2317.TW"]  # official prior-session volume
    artifact["strategy_logic"]["screen"]["steps"][1]["side"] = "bottom"
    assert build_candidate_pool_snapshot(artifact, market, "2026-09-02")["members"] == ["2330.TW"]
    # Remove the high-price member before ranking: a subsequent bottom cut may
    # never reintroduce a member filtered out by the prefix.
    artifact["strategy_logic"]["screen"]["steps"][0]["expression"]["op"] = "lt"
    artifact["parameters"]["entry_z"] = 1000
    assert build_candidate_pool_snapshot(artifact, market, "2026-09-02")["members"] == ["2317.TW"]


@pytest.mark.parametrize("fault,reason", [
    ("suspended", "suspended"), ("liquidity", "illiquid"),
    ("history", "insufficient_history"), ("missing", "missing_prior_session"),
    ("status", "missing_suspension_status"),
])
def test_boundary_exclusions_are_recorded(fault, reason):
    artifact, market = screen_artifact(), recorded_market()
    rows = market["symbols"]["2330.TW"]["bars"]
    if fault == "suspended":
        rows[0]["suspended"] = True
    elif fault == "liquidity":
        rows[0]["traded_value"] = 0
    elif fault == "history":
        artifact["parameters"]["pool_history"] = 2
    elif fault == "missing":
        rows.pop(0)
    else:
        rows[0].pop("suspended")
    snapshot = build_candidate_pool_snapshot(artifact, market, "2026-09-02")
    assert {r["symbol"]: r["reason"] for r in snapshot["excluded"]}["2330.TW"] == reason


def test_today_and_future_rows_cannot_change_snapshot_or_digest():
    artifact, market = screen_artifact(), recorded_market()
    before = build_candidate_pool_snapshot(artifact, market, "2026-09-02")
    for payload in market["symbols"].values():
        for row in payload["bars"][1:]:
            row.update(close=1e9, volume=1e9, suspended=True)
    assert build_candidate_pool_snapshot(artifact, market, "2026-09-02") == before
    unsigned = {k: v for k, v in before.items() if k != "digest"}
    assert digest(unsigned) == before["digest"]


def test_refresh_is_calendar_anchored_and_no_faster_than_slowest_input():
    artifact, market = screen_artifact(), recorded_market()
    artifact["strategy_logic"]["screen"]["steps"][-1]["refresh"] = "weekly"
    first = build_candidate_pool_snapshot(artifact, market, "2026-09-08")
    second = build_candidate_pool_snapshot(artifact, market, "2026-09-09")
    assert first["members"] == second["members"]
    assert first["steps"] == second["steps"]
    assert first["steps"][-1]["as_of"] == "2026-09-07"
    artifact["strategy_logic"]["inputs"].append({"name": "revenue", "dataset": "tw_monthly_revenue",
                                                   "field": "yoy", "availability_lag_days": 10})
    artifact["strategy_logic"]["screen"]["steps"][-1]["expression"] = {"ref": "revenue"}
    with pytest.raises(fr.FactorRuleValidationError, match="release cadence"):
        fr.validate_factor_rule_logic(artifact["strategy_logic"], artifact["parameters"])


def test_lag_applies_to_screen_and_missing_features_exclude():
    artifact, market = screen_artifact(), recorded_market()
    artifact["strategy_logic"]["inputs"].append({"name": "revenue", "dataset": "tw_monthly_revenue",
                                                   "field": "yoy", "availability_lag_days": 10})
    artifact["strategy_logic"]["screen"]["steps"] = [{"kind": "rank", "expression": {"ref": "revenue"},
        "count": {"const_param": "pool_count"}, "side": "top", "refresh": "monthly"}]
    # Recorded non-price fixture is intentionally absent. It must not fall back
    # to prices, zero, or a previously provisioned list.
    result = build_candidate_pool_snapshot(artifact, market, "2026-10-02")
    assert result["members"] == []
    assert all("missing_input" in e["reason"] for e in result["excluded"])


def test_pool_exit_and_minimum_hold_never_evaluate_excluded_entry():
    artifact, market = screen_artifact(), recorded_market()
    # An invalid entry input on the excluded member must never be accessed.
    market["symbols"]["2330.TW"]["features"]["price"] = [None]
    positions = {"2330.TW": {"current_direction": "LONG", "holding_bars": 1}}
    view = evaluate_screened_views(artifact, market, "2026-09-02", positions=positions)["2330.TW"]
    assert (view["action"], view["target_direction"], view["exit_reason"]) == (
        "SELL", "FLAT", "left_candidate_pool")
    artifact["parameters"]["pool_min"] = 2
    views = evaluate_screened_views(artifact, market, "2026-09-02", positions=positions)
    assert all(v["action"] == "HOLD" and v["strength"] == 0 for v in views.values())


def test_shared_registry_entry_point_fails_closed_without_complete_market():
    artifact = screen_artifact()
    with pytest.raises(StrategyArtifactValidationError, match="candidate_pool_market"):
        evaluate_strategy_view(artifact, [1, 2], symbol="2330.TW", decision_time="2026-09-02")
    market = recorded_market()
    market["scope"] = "provisioned_list"
    with pytest.raises(StrategyArtifactValidationError, match="complete"):
        evaluate_strategy_view(artifact, [1, 2], symbol="2330.TW", decision_time="2026-09-02",
                               features={MARKET_FEATURE_KEY: market})


def test_survivorship_defaults_to_limited_and_filter_identity_changes_digest():
    artifact, market = screen_artifact(), recorded_market()
    first = build_candidate_pool_snapshot(artifact, market, "2026-09-02")
    assert first["survivorship_limited"] is True
    market["includes_delisted"] = market["includes_suspended"] = True  # coverage fault injection
    second = build_candidate_pool_snapshot(artifact, market, "2026-09-02")
    assert second["survivorship_limited"] is False
    artifact["parameters"]["pool_count"] = 2
    third = build_candidate_pool_snapshot(artifact, market, "2026-09-02")
    assert third["digest"] != second["digest"]


def test_uncatalogued_industry_grouping_fails_closed():
    artifact = screen_artifact()
    artifact["strategy_logic"]["inputs"].append({"name": "industry", "dataset": "tw_industry_classification",
                                                   "field": "industry_code", "availability_lag_days": 0})
    artifact["strategy_logic"]["screen"]["steps"][-1]["group_by"] = {"industry_input": "industry"}
    with pytest.raises(fr.FactorRuleValidationError, match="governed release cadence"):
        fr.validate_factor_rule_logic(artifact["strategy_logic"], artifact["parameters"])


@pytest.mark.parametrize("cadence", ["monthly", "quarterly"])
def test_slow_rank_freezes_the_entire_upstream_prefix(cadence):
    artifact, market = screen_artifact(), recorded_market()
    artifact["strategy_logic"]["screen"]["steps"][-1]["refresh"] = cadence
    first = build_candidate_pool_snapshot(artifact, market, "2026-10-02")
    later = build_candidate_pool_snapshot(artifact, market, "2026-10-21")
    assert first["members"]
    assert first["members"] == later["members"]
    assert first["steps"] == later["steps"]
    assert first["steps"][-1]["as_of"] == "2026-10-01"


def test_declared_release_lag_and_late_publication_are_both_honored():
    artifact, market = screen_artifact(), recorded_market()
    artifact["strategy_logic"]["inputs"][0]["availability_lag_days"] = 3
    artifact["strategy_logic"]["screen"]["steps"] = [{
        "kind": "rank", "expression": {"ref": "price"}, "refresh": "daily",
        "count": {"const_param": "pool_count"}, "side": "top",
    }]
    assert build_candidate_pool_snapshot(artifact, market, "2026-09-02")["members"] == []
    assert build_candidate_pool_snapshot(artifact, market, "2026-09-05")["members"] == ["2330.TW"]
    # Publication-delay fault only; the recorded market values are unchanged.
    for row in market["symbols"]["2330.TW"]["bars"]:
        row["available_time"] = "2026-10-31T18:00:00+08:00"
    assert build_candidate_pool_snapshot(artifact, market, "2026-09-05")["members"] == ["2317.TW"]


def test_industry_group_cuts_with_dated_catalogued_classification(monkeypatch):
    artifact, market = screen_artifact(), recorded_market()
    definition = fr.FeatureDefinition("tw_industry_classification", "industry_code", release_cadence="daily")
    # Inject a catalogue and two classification groups solely to exercise the
    # grouping contract. This is not a dataset registration or hosted proof.
    monkeypatch.setitem(fr._FEATURE_CATALOGUE, (definition.dataset, definition.field), definition)
    artifact["strategy_logic"]["inputs"].append({"name": "industry", "dataset": definition.dataset,
                                                   "field": definition.field, "availability_lag_days": 0})
    artifact["strategy_logic"]["screen"]["steps"][-1]["group_by"] = {"industry_input": "industry"}
    for code, symbol in enumerate(sorted(market["symbols"]), start=1):
        market["symbols"][symbol]["features"]["industry"] = [{
            "event_time": "2026-09-01T13:30:00+08:00", "available_time": "2026-09-01T18:00:00+08:00",
            "value": code,
        }]
    snapshot = build_candidate_pool_snapshot(artifact, market, "2026-09-02")
    assert snapshot["members"] == ["2317.TW", "2330.TW"]
    market["symbols"]["2330.TW"]["features"]["industry"][0]["available_time"] = "2026-09-03"
    assert build_candidate_pool_snapshot(artifact, market, "2026-09-02")["members"] == ["2317.TW"]


def test_future_malformed_price_feature_is_invisible_and_entry_vwap_is_never_synthesized():
    artifact, market = screen_artifact(), recorded_market()
    payload = market["symbols"]["2317.TW"]
    payload["features"]["turnover"] = [{"event_time": row["event_time"],
        "available_time": row["available_time"], "value": row["volume"]} for row in payload["bars"]]
    before = build_candidate_pool_snapshot(artifact, market, "2026-09-02")
    payload["features"]["turnover"][-1]["value"] = None
    assert build_candidate_pool_snapshot(artifact, market, "2026-09-02") == before
    artifact["strategy_logic"]["signal"] = {"ref": "vwap"}
    view = evaluate_screened_views(artifact, market, "2026-09-02")["2317.TW"]
    assert view["action"] == "HOLD"
    assert "missing_dated_input:vwap" in view["hold_reason"]


def test_calendar_catches_a_wholly_stale_feed():
    artifact, market = screen_artifact(), recorded_market()
    for payload in market["symbols"].values():
        payload["bars"] = payload["bars"][:1]
    snapshot = build_candidate_pool_snapshot(artifact, market, "2026-09-04")
    assert snapshot["members"] == []
    assert all(row["reason"] == "missing_prior_session" for row in snapshot["excluded"])


def test_screen_constants_must_remain_governed_mutable_controls():
    artifact = screen_artifact()
    artifact["mutation_surface"]["controls"] = [c for c in artifact["mutation_surface"]["controls"]
                                                  if c["parameter_key"] != "pool_count"]
    with pytest.raises(fr.FactorRuleValidationError, match="pool_count.*mutation_surface.controls"):
        fr.validate_factor_rule_logic(artifact["strategy_logic"], artifact["parameters"], artifact["mutation_surface"])
