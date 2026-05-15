"""
Smoke test for the governed statsmodels adapter.

Uses StubStatsmodelsBackend by default (CI-safe, no external data).
Run: python services/research/statsmodels/smoke_test.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapter.statsmodels_adapter import (
    GovernedDataset,
    GovernedStatsmodelsInputAdapter,
    StubStatsmodelsBackend,
    run_statsmodels_workflow,
)


def _make_minimal_dataset() -> GovernedDataset:
    n = 60
    price_a = [100.0 + i * 0.5 + (i % 7) * 0.1 for i in range(n)]
    price_b = [200.0 + i * 0.4 + (i % 5) * 0.2 for i in range(n)]
    factor_x = [0.02 + (i % 11) * 0.001 for i in range(n)]
    return GovernedDataset(
        price_series={"ASSET_A": price_a, "ASSET_B": price_b},
        factor_series={"MACRO_X": factor_x},
        metadata={"source": "smoke_test", "governed": True},
    )


def run_smoke_test() -> None:
    print("=== statsmodels governed adapter smoke test ===")

    dataset = _make_minimal_dataset()
    print(f"Dataset: {len(dataset.price_series)} price series, {len(dataset.factor_series)} factor series")

    bundle = run_statsmodels_workflow(dataset, backend=StubStatsmodelsBackend())

    # Validate artifact_bundle shape
    assert bundle["artifact_family"] == "regime_report", (
        f"Expected artifact_family='regime_report', got '{bundle['artifact_family']}'"
    )
    assert bundle["framework"] == "statsmodels", (
        f"Expected framework='statsmodels', got '{bundle['framework']}'"
    )
    gov = bundle["governance"]
    assert gov["direct_live_influence"] is False, (
        f"Expected direct_live_influence=false, got {gov['direct_live_influence']}"
    )
    assert gov["lean_consumption"] == "research_only_not_direct_action", (
        f"Unexpected lean_consumption: {gov['lean_consumption']}"
    )

    # Validate registry_entry
    reg = bundle["registry_entry"]
    assert reg["artifact_type"] == "research_report", (
        f"Expected artifact_type='research_report', got '{reg['artifact_type']}'"
    )
    assert reg["artifact_state"] == "draft", (
        f"Expected artifact_state='draft', got '{reg['artifact_state']}'"
    )
    assert reg["deployment_summary"]["current_stage"] == "none", (
        f"Expected current_stage='none', got '{reg['deployment_summary']['current_stage']}'"
    )

    print(f"artifact_id        : {bundle['artifact_id']}")
    print(f"artifact_family    : {bundle['artifact_family']}")
    print(f"artifact_state     : {reg['artifact_state']}")
    print(f"current_stage      : {reg['deployment_summary']['current_stage']}")
    print(f"direct_live_inf.   : {gov['direct_live_influence']}")
    print(f"lean_consumption   : {gov['lean_consumption']}")

    paths_in_results = list(bundle["results_summary"].keys())
    print(f"analysis paths     : {paths_in_results}")
    assert "cointegration" in paths_in_results
    assert "var_vecm" in paths_in_results
    assert "markov_switching" in paths_in_results

    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    run_smoke_test()
