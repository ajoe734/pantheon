"""
Unit tests for GovernedStatsmodelsAdapter.

Run: python -m pytest services/research/statsmodels/test_adapter.py -v
Target: ≥10 tests
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from adapter.statsmodels_adapter import (
    GovernedDataset,
    GovernedStatsmodelsInputAdapter,
    StubStatsmodelsBackend,
    StatsmodelsWorkflowError,
    run_statsmodels_workflow,
    _build_artifact_bundle,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

N = 20


@pytest.fixture
def valid_dataset() -> GovernedDataset:
    return GovernedDataset(
        price_series={
            "A": [float(i) for i in range(N)],
            "B": [float(i) * 1.1 for i in range(N)],
        },
        factor_series={
            "X": [0.01 * i for i in range(N)],
        },
        metadata={"governed": True},
    )


@pytest.fixture
def adapter() -> GovernedStatsmodelsInputAdapter:
    return GovernedStatsmodelsInputAdapter()


@pytest.fixture
def stub_backend() -> StubStatsmodelsBackend:
    return StubStatsmodelsBackend()


# ---------------------------------------------------------------------------
# Input adapter — schema rejection tests
# ---------------------------------------------------------------------------


def test_reject_non_governed_input(adapter):
    """Raw dict must be rejected; only GovernedDataset is a governed interface."""
    with pytest.raises(StatsmodelsWorkflowError, match="GovernedDataset"):
        adapter.validate({"price_series": {}, "factor_series": {}})  # type: ignore[arg-type]


def test_reject_insufficient_price_series(adapter):
    ds = GovernedDataset(
        price_series={"A": [1.0] * N},  # only 1 series
        factor_series={"X": [0.1] * N},
    )
    with pytest.raises(StatsmodelsWorkflowError, match="price series"):
        adapter.validate(ds)


def test_reject_no_factor_series(adapter):
    ds = GovernedDataset(
        price_series={"A": [1.0] * N, "B": [2.0] * N},
        factor_series={},  # zero factor series
    )
    with pytest.raises(StatsmodelsWorkflowError, match="factor"):
        adapter.validate(ds)


def test_reject_too_few_observations(adapter):
    ds = GovernedDataset(
        price_series={"A": [1.0] * 5, "B": [2.0] * 5},  # only 5 obs
        factor_series={"X": [0.1] * 5},
        metadata={"governed": True},
    )
    with pytest.raises(StatsmodelsWorkflowError, match="observations"):
        adapter.validate(ds)


def test_reject_non_numeric_series_values(adapter):
    ds = GovernedDataset(
        price_series={"A": [1.0] * N, "B": ["x"] * N},
        factor_series={"X": [0.1] * N},
        metadata={"governed": True},
    )
    with pytest.raises(StatsmodelsWorkflowError, match="numeric observations only"):
        adapter.validate(ds)


def test_reject_misaligned_series_lengths(adapter):
    ds = GovernedDataset(
        price_series={"A": [1.0] * 12, "B": [2.0] * 10},
        factor_series={"X": [0.1] * 10},
        metadata={"governed": True},
    )
    with pytest.raises(StatsmodelsWorkflowError, match="misaligned"):
        adapter.validate(ds)


def test_reject_nan_values(adapter):
    ds = GovernedDataset(
        price_series={"A": [1.0] * N, "B": [2.0] * N},
        factor_series={"X": [float("nan")] * N},
        metadata={"governed": True},
    )
    with pytest.raises(StatsmodelsWorkflowError, match="non-finite"):
        adapter.validate(ds)


def test_reject_ungoverned_metadata(adapter):
    ds = GovernedDataset(
        price_series={"A": [1.0] * N, "B": [2.0] * N},
        factor_series={"X": [0.1] * N},
        metadata={"governed": False},
    )
    with pytest.raises(StatsmodelsWorkflowError, match="metadata.governed=True"):
        adapter.validate(ds)


def test_accept_valid_dataset(adapter, valid_dataset):
    result = adapter.validate(valid_dataset)
    assert result is valid_dataset


# ---------------------------------------------------------------------------
# Canonical output envelope tests
# ---------------------------------------------------------------------------


def test_artifact_family_is_regime_report(valid_dataset, stub_backend):
    bundle = run_statsmodels_workflow(valid_dataset, backend=stub_backend)
    assert bundle["artifact_family"] == "regime_report"


def test_framework_is_statsmodels(valid_dataset, stub_backend):
    bundle = run_statsmodels_workflow(valid_dataset, backend=stub_backend)
    assert bundle["framework"] == "statsmodels"


def test_governance_no_direct_live_influence(valid_dataset, stub_backend):
    bundle = run_statsmodels_workflow(valid_dataset, backend=stub_backend)
    assert bundle["governance"]["direct_live_influence"] is False


def test_governance_lean_consumption(valid_dataset, stub_backend):
    bundle = run_statsmodels_workflow(valid_dataset, backend=stub_backend)
    assert bundle["governance"]["lean_consumption"] == "research_only_not_direct_action"


def test_registry_artifact_type(valid_dataset, stub_backend):
    bundle = run_statsmodels_workflow(valid_dataset, backend=stub_backend)
    assert bundle["registry_entry"]["artifact_type"] == "research_report"


def test_registry_artifact_state_is_draft(valid_dataset, stub_backend):
    bundle = run_statsmodels_workflow(valid_dataset, backend=stub_backend)
    assert bundle["registry_entry"]["artifact_state"] == "draft"


def test_registry_deployment_stage_is_none(valid_dataset, stub_backend):
    bundle = run_statsmodels_workflow(valid_dataset, backend=stub_backend)
    assert bundle["registry_entry"]["deployment_summary"]["current_stage"] == "none"


# ---------------------------------------------------------------------------
# Stub backend determinism and regime-label packaging tests
# ---------------------------------------------------------------------------


def test_stub_cointegration_deterministic(valid_dataset, stub_backend):
    r1 = stub_backend.run_cointegration(valid_dataset)
    r2 = stub_backend.run_cointegration(valid_dataset)
    assert r1 == r2


def test_stub_markov_regime_label_count(valid_dataset, stub_backend):
    result = stub_backend.run_markov_switching(valid_dataset)
    factor_len = len(next(iter(valid_dataset.factor_series.values())))
    assert len(result["regime_labels"]) == factor_len


def test_stub_markov_regime_labels_are_binary(valid_dataset, stub_backend):
    result = stub_backend.run_markov_switching(valid_dataset)
    assert all(label in (0, 1) for label in result["regime_labels"])


def test_stub_var_vecm_returns_n_equations(valid_dataset, stub_backend):
    result = stub_backend.run_var_vecm(valid_dataset)
    assert result["n_equations"] == len(valid_dataset.price_series)


# ---------------------------------------------------------------------------
# Selective analysis path tests
# ---------------------------------------------------------------------------


def test_selective_cointegration_only(valid_dataset, stub_backend):
    bundle = run_statsmodels_workflow(
        valid_dataset, analysis_paths=["cointegration"], backend=stub_backend
    )
    assert "cointegration" in bundle["results_summary"]
    assert "var_vecm" not in bundle["results_summary"]
    assert "markov_switching" not in bundle["results_summary"]


def test_selective_var_vecm_only(valid_dataset, stub_backend):
    bundle = run_statsmodels_workflow(
        valid_dataset, analysis_paths=["var_vecm"], backend=stub_backend
    )
    assert "var_vecm" in bundle["results_summary"]
    assert "cointegration" not in bundle["results_summary"]


def test_all_three_paths_present_by_default(valid_dataset, stub_backend):
    bundle = run_statsmodels_workflow(valid_dataset, backend=stub_backend)
    keys = bundle["results_summary"].keys()
    assert "cointegration" in keys
    assert "var_vecm" in keys
    assert "markov_switching" in keys


# ---------------------------------------------------------------------------
# Artifact ID uniqueness
# ---------------------------------------------------------------------------


def test_artifact_id_unique_per_run(valid_dataset, stub_backend):
    b1 = run_statsmodels_workflow(valid_dataset, backend=stub_backend)
    b2 = run_statsmodels_workflow(valid_dataset, backend=stub_backend)
    assert b1["artifact_id"] != b2["artifact_id"]
