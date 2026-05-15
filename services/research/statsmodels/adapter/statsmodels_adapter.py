"""
Governed statsmodels adapter for Pantheon Research Plane.

Governance invariants:
- All inputs must pass GovernedStatsmodelsInputAdapter before reaching a backend.
- Only StubStatsmodelsBackend is used in CI / default local runs.
- Real backend requires PANTHEON_STATSMODELS_BACKEND=real env var.
- Outputs are always non-executable research artifacts at artifact_state=draft.
"""

from __future__ import annotations

import os
import uuid
import datetime
import math
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class StatsmodelsWorkflowError(Exception):
    """Raised when governance validation or backend execution fails."""


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


@dataclass
class GovernedDataset:
    """Minimal governed time-series dataset admitted by the input adapter."""

    price_series: dict[str, list[float]]  # ≥2 series required
    factor_series: dict[str, list[float]]  # ≥1 series required
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Input adapter
# ---------------------------------------------------------------------------


class GovernedStatsmodelsInputAdapter:
    """Validates incoming datasets against Research Plane governance rules."""

    MIN_PRICE_SERIES = 2
    MIN_FACTOR_SERIES = 1
    MIN_OBSERVATIONS = 10
    REQUIRED_METADATA_GOVERNED = True

    def validate(self, dataset: GovernedDataset) -> GovernedDataset:
        if not isinstance(dataset, GovernedDataset):
            raise StatsmodelsWorkflowError(
                "Input must be a GovernedDataset; raw pandas frames are not a governed interface."
            )

        if len(dataset.price_series) < self.MIN_PRICE_SERIES:
            raise StatsmodelsWorkflowError(
                f"At least {self.MIN_PRICE_SERIES} price series required for cointegration; "
                f"got {len(dataset.price_series)}."
            )

        if len(dataset.factor_series) < self.MIN_FACTOR_SERIES:
            raise StatsmodelsWorkflowError(
                f"At least {self.MIN_FACTOR_SERIES} factor/macro series required; "
                f"got {len(dataset.factor_series)}."
            )

        if dataset.metadata.get("governed") is not self.REQUIRED_METADATA_GOVERNED:
            raise StatsmodelsWorkflowError(
                "Dataset metadata must declare governed historical inputs with metadata.governed=True."
            )

        all_series = {**dataset.price_series, **dataset.factor_series}
        expected_length: int | None = None
        for name, values in all_series.items():
            if not isinstance(values, list):
                raise StatsmodelsWorkflowError(
                    f"Series '{name}' must be a list of numeric observations."
                )

            if len(values) < self.MIN_OBSERVATIONS:
                raise StatsmodelsWorkflowError(
                    f"Series '{name}' has only {len(values)} observations; "
                    f"minimum is {self.MIN_OBSERVATIONS}."
                )

            if expected_length is None:
                expected_length = len(values)
            elif len(values) != expected_length:
                raise StatsmodelsWorkflowError(
                    f"Series '{name}' is misaligned with the governed dataset; "
                    f"expected {expected_length} observations, got {len(values)}."
                )

            for index, value in enumerate(values):
                if not isinstance(value, (int, float)):
                    raise StatsmodelsWorkflowError(
                        f"Series '{name}' must contain numeric observations only; "
                        f"found {type(value).__name__} at position {index}."
                    )
                if not math.isfinite(float(value)):
                    raise StatsmodelsWorkflowError(
                        f"Series '{name}' contains non-finite observations; "
                        f"found {value!r} at position {index}."
                    )

        return dataset


# ---------------------------------------------------------------------------
# Output envelope
# ---------------------------------------------------------------------------


def _build_artifact_bundle(
    *,
    analysis_path: str,
    results: dict[str, Any],
    framework: str = "statsmodels",
) -> dict[str, Any]:
    artifact_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )

    return {
        "artifact_id": artifact_id,
        "artifact_family": "regime_report",
        "framework": framework,
        "analysis_path": analysis_path,
        "produced_at": now,
        "results_summary": results,
        "governance": {
            "direct_live_influence": False,
            "lean_consumption": "research_only_not_direct_action",
            "write_boundary": "research_plane_only",
        },
        "registry_entry": {
            "artifact_type": "research_report",
            "artifact_state": "draft",
            "deployment_summary": {
                "current_stage": "none",
            },
        },
    }


# ---------------------------------------------------------------------------
# Stub backend (CI-safe, deterministic)
# ---------------------------------------------------------------------------


class StubStatsmodelsBackend:
    """Deterministic backend for CI and default local runs.

    Returns pre-cooked results with no external data dependency.
    """

    def run_cointegration(self, dataset: GovernedDataset) -> dict[str, Any]:
        series_names = list(dataset.price_series.keys())
        return {
            "test": "engle_granger",
            "pairs_tested": [(series_names[0], series_names[1])],
            "cointegrated": True,
            "p_value": 0.02,
            "stub": True,
        }

    def run_var_vecm(self, dataset: GovernedDataset) -> dict[str, Any]:
        return {
            "model": "VAR",
            "lag_order": 2,
            "n_equations": len(dataset.price_series),
            "aic": -1234.56,
            "stub": True,
        }

    def run_markov_switching(self, dataset: GovernedDataset) -> dict[str, Any]:
        n = len(next(iter(dataset.factor_series.values())))
        return {
            "model": "MarkovRegression",
            "n_regimes": 2,
            "regime_labels": [0 if i % 2 == 0 else 1 for i in range(n)],
            "smoothed_marginal_probabilities": [[0.9, 0.1] if i % 2 == 0 else [0.1, 0.9] for i in range(n)],
            "stub": True,
        }


# ---------------------------------------------------------------------------
# Real backend (requires PANTHEON_STATSMODELS_BACKEND=real)
# ---------------------------------------------------------------------------


class StatsmodelsBackend:
    """Real backend wrapping statsmodels for governed research use."""

    def run_cointegration(self, dataset: GovernedDataset) -> dict[str, Any]:
        import numpy as np
        from statsmodels.tsa.stattools import coint

        series = list(dataset.price_series.values())
        names = list(dataset.price_series.keys())
        y0 = np.array(series[0])
        y1 = np.array(series[1])
        t_stat, p_value, crit_values = coint(y0, y1)
        return {
            "test": "engle_granger",
            "pairs_tested": [(names[0], names[1])],
            "cointegrated": bool(p_value < 0.05),
            "p_value": float(p_value),
            "t_stat": float(t_stat),
            "crit_values": {k: float(v) for k, v in zip(["1%", "5%", "10%"], crit_values)},
        }

    def run_var_vecm(self, dataset: GovernedDataset) -> dict[str, Any]:
        import numpy as np
        import pandas as pd
        from statsmodels.tsa.api import VAR

        data = pd.DataFrame(dataset.price_series)
        model = VAR(data)
        results = model.fit(maxlags=4, ic="aic")
        return {
            "model": "VAR",
            "lag_order": int(results.k_ar),
            "n_equations": int(results.neqs),
            "aic": float(results.aic),
        }

    def run_markov_switching(self, dataset: GovernedDataset) -> dict[str, Any]:
        import numpy as np
        from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

        series_name = next(iter(dataset.factor_series))
        y = np.array(dataset.factor_series[series_name])
        model = MarkovRegression(y, k_regimes=2, trend="c", switching_variance=True)
        results = model.fit(disp=False)
        regime_labels = results.smoothed_marginal_probabilities.argmax(axis=1).tolist()
        return {
            "model": "MarkovRegression",
            "n_regimes": 2,
            "regime_labels": [int(r) for r in regime_labels],
            "aic": float(results.aic),
        }


# ---------------------------------------------------------------------------
# Governed workflow entry point
# ---------------------------------------------------------------------------


def run_statsmodels_workflow(
    dataset: GovernedDataset,
    *,
    analysis_paths: list[str] | None = None,
    backend: StubStatsmodelsBackend | StatsmodelsBackend | None = None,
) -> dict[str, Any]:
    """Governed entry point: input adapter → backend → normalized artifact.

    Defaults to StubStatsmodelsBackend unless PANTHEON_STATSMODELS_BACKEND=real.
    """
    use_real = os.environ.get("PANTHEON_STATSMODELS_BACKEND", "stub").lower() == "real"

    if backend is None:
        backend = StatsmodelsBackend() if use_real else StubStatsmodelsBackend()

    adapter = GovernedStatsmodelsInputAdapter()
    validated = adapter.validate(dataset)

    paths = analysis_paths or ["cointegration", "var_vecm", "markov_switching"]
    results: dict[str, Any] = {}

    if "cointegration" in paths:
        results["cointegration"] = backend.run_cointegration(validated)
    if "var_vecm" in paths:
        results["var_vecm"] = backend.run_var_vecm(validated)
    if "markov_switching" in paths:
        results["markov_switching"] = backend.run_markov_switching(validated)

    analysis_label = "+".join(paths)
    return _build_artifact_bundle(analysis_path=analysis_label, results=results)
