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
def stub_backend(monkeypatch) -> StubStatsmodelsBackend:
    # The stub is test-only: it is refused unless this switch is set (see resolve_backend).
    monkeypatch.setenv("PANTHEON_STATSMODELS_TEST_STUB", "1")
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


# ---------------------------------------------------------------------------
# Regime backend: filtered probabilities only, stub unreachable from production
# ---------------------------------------------------------------------------


def _regime_dataset(n: int = 900, seed: int = 3) -> GovernedDataset:
    import random

    rnd = random.Random(seed)
    state, values = 0, []
    for _ in range(n):
        if rnd.random() < 0.02:
            state = 1 - state
        values.append(rnd.gauss(0.05, 0.7) if state == 0 else rnd.gauss(-0.1, 2.0))
    return GovernedDataset(
        price_series={"A": [100 + i for i in range(n)], "B": [50 + 0.5 * i for i in range(n)]},
        factor_series={"RET": values},
        metadata={"governed": True},
    )


def test_real_markov_backend_reports_filtered_probabilities_only():
    from adapter.statsmodels_adapter import StatsmodelsBackend

    result = StatsmodelsBackend().run_markov_switching(_regime_dataset())
    assert result["inference"] == "filtered_only"
    assert "smoothed" not in str(result.keys()).lower() + str(result["sessions"][0].keys()).lower()
    first = result["sessions"][-1]
    assert set(first) >= {"state", "probabilities", "transition_matrix", "expected_durations",
                          "sessions_in_state", "last_switch_session"}
    assert abs(sum(first["probabilities"].values()) - 1.0) < 1e-9
    assert len(result["sessions"]) == len(result["filtered_marginal_probabilities"]) == 900
    assert all(abs(sum(row) - 1.0) < 1e-9 for row in result["transition_matrix"])


def test_markov_output_never_reads_smoothed_probabilities(monkeypatch):
    """A results object whose smoothed attributes raise proves the backend never touches them."""
    from statsmodels.tsa.regime_switching import markov_switching as ms
    from adapter.statsmodels_adapter import StatsmodelsBackend

    original = ms.MarkovSwitchingResults.__getattribute__

    def guarded(self, name):
        # statsmodels reads these inside fit(); only a read from Pantheon code is a violation.
        caller = sys._getframe(1).f_code.co_filename.replace("\\", "/")
        if name in ("smoothed_marginal_probabilities", "smoothed_joint_probabilities") and "/services/research/" in caller:
            raise AssertionError(f"smoothed attribute read: {name}")
        return original(self, name)

    monkeypatch.setattr(ms.MarkovSwitchingResults, "__getattribute__", guarded)
    result = StatsmodelsBackend().run_markov_switching(_regime_dataset(n=400))
    assert result["inference"] == "filtered_only"


def test_filtered_state_at_t_ignores_later_observations():
    from adapter.statsmodels_adapter import StatsmodelsBackend

    dataset = _regime_dataset()
    full = StatsmodelsBackend().run_markov_switching(dataset)
    cut = 600
    truncated = GovernedDataset(dataset.price_series, {"RET": dataset.factor_series["RET"][:cut]}, dataset.metadata)
    part = StatsmodelsBackend().run_markov_switching(truncated)
    # Parameters differ between fits, so compare the filter under one parameter set instead.
    import pandas as pd
    from services.research.regime.models import fit_volatility

    frame = pd.DataFrame({"return_pct": dataset.factor_series["RET"]},
                         index=pd.date_range("2000-01-03", periods=900, freq="B"))
    model = fit_volatility(frame, horizon="short")
    a = model.infer(frame)[cut - 1].probabilities
    b = model.infer(frame.iloc[:cut])[cut - 1].probabilities
    assert a == pytest.approx(b, abs=1e-9)
    assert len(part["sessions"]) == cut and len(full["sessions"]) == 900


def test_stub_unreachable_without_real_backend_and_test_switch(valid_dataset, monkeypatch):
    monkeypatch.delenv("PANTHEON_STATSMODELS_BACKEND", raising=False)
    monkeypatch.delenv("PANTHEON_STATSMODELS_TEST_STUB", raising=False)
    with pytest.raises(StatsmodelsWorkflowError, match="statsmodels_backend_not_enabled"):
        run_statsmodels_workflow(valid_dataset)
    with pytest.raises(StatsmodelsWorkflowError, match="stub_backend_not_allowed"):
        run_statsmodels_workflow(valid_dataset, backend=StubStatsmodelsBackend())
    monkeypatch.setenv("PANTHEON_STATSMODELS_BACKEND", "stub")
    with pytest.raises(StatsmodelsWorkflowError, match="statsmodels_backend_not_enabled"):
        run_statsmodels_workflow(valid_dataset)


def test_production_entry_points_fail_closed(valid_dataset, monkeypatch, tmp_path):
    monkeypatch.delenv("PANTHEON_STATSMODELS_BACKEND", raising=False)
    monkeypatch.delenv("PANTHEON_STATSMODELS_TEST_STUB", raising=False)
    # Experiment dispatch calls the workflow with no backend, and never names the stub.
    import inspect

    from services.research.experiment_orchestrator import parallel_dispatch

    source = inspect.getsource(parallel_dispatch._run_statsmodels_backend)
    assert "Stub" not in source and "backend=" not in source
    # Persona OSS demo harness: explicit stub, refused without the test switch.
    from services.persona import oss_runtime

    # (a different import path gives a different exception class object, so match by message)
    with pytest.raises(Exception, match="stub_backend_not_allowed_outside_tests"):
        oss_runtime.run_persona_oss_request(oss_runtime.PersonaOSSRequest(
            persona_id="p", session_id="s", component="statsmodels", intent="run_statsmodels"))
    # Container worker: no dataset path / no real backend -> non-zero exit, no output artifact.
    import importlib.util

    spec = importlib.util.spec_from_file_location("sm_worker", os.path.join(os.path.dirname(__file__), "worker.py"))
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.delenv("STATSMODELS_DATASET_PATH", raising=False)
    assert worker.main() == 2
    dataset_file = tmp_path / "d.json"
    dataset_file.write_text('{"price_series": {}, "factor_series": {}}')
    monkeypatch.setenv("STATSMODELS_DATASET_PATH", str(dataset_file))
    assert worker.main() == 2
