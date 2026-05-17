"""Tests for governed Qlib rolling-window OOS ExperimentRun output."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_DIR = Path(__file__).resolve().parent
for path in (REPO_ROOT, SERVICE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from oos_eval import evaluate
from rolling_pipeline import QlibRollingPipelineError, run
from services.research.experiments.models import validate_experiment_run_payload


def test_rolling_pipeline_happy_path_returns_experiment_run_with_oos_metrics() -> None:
    experiment_run = run(
        "strategy-spec:test-alpha-v1",
        4,
        dataset=_dataset(periods=14),
        dataset_manifest=_dataset_manifest(),
        strategy_spec_packet=_strategy_spec_packet(),
        created_at="2026-05-16T00:00:00Z",
        code_version="git:test",
    )

    validate_experiment_run_payload(experiment_run)
    assert experiment_run["status"] == "completed"
    assert experiment_run["backend_id"] == "qlib_rolling_oos"

    metadata = experiment_run["metadata"]
    assert metadata["producer_run_id"] == experiment_run["run_id"]
    assert metadata["lineage"]["source_strategy_spec_id"] == "strategy-spec:test-alpha-v1"
    assert metadata["lineage"]["strategy_spec_artifact_id"] == "strategy-spec:test-alpha-v1"
    assert metadata["lineage"]["source_dataset_refs"] == ["dataset:test-qlib-oos"]
    assert len(metadata["oos_observations"]) > 0

    metrics = evaluate(experiment_run)
    for key in ("sharpe", "sortino", "max_dd", "ic"):
        assert key in metrics
    assert metrics["num_oos_samples"] == len(metadata["oos_observations"])
    assert metadata["evaluation_summary"]["strategy_spec_artifact_id"] == "strategy-spec:test-alpha-v1"
    assert metadata["evaluation_summary"]["oos_metrics"] == metrics
    assert metadata["evaluation_summary"]["registry_hints"]["deployment_stage"] == "none"


def test_rolling_pipeline_fails_fast_on_insufficient_data() -> None:
    with pytest.raises(QlibRollingPipelineError, match="insufficient data"):
        run(
            "strategy-spec:test-alpha-v1",
            4,
            dataset=_dataset(periods=8),
            dataset_manifest=_dataset_manifest(),
            strategy_spec_packet=_strategy_spec_packet(),
        )


def _dataset(periods: int) -> dict:
    return {
        "dataset_id": "dataset:test-qlib-oos",
        "strategy_id": "test-alpha",
        "source_dataset_refs": ["dataset:test-qlib-oos"],
        "source_strategy_spec_id": "strategy-spec:test-alpha-v1",
        "data_frequency": "daily",
        "records": _records("AAA", 100.0, periods) + _records("BBB", 80.0, periods),
    }


def _records(instrument: str, base: float, periods: int) -> list[dict]:
    rows = []
    start = date(2026, 1, 1)
    price = base
    for idx in range(periods):
        drift = 0.006 if idx % 4 else -0.004
        price = price * (1.0 + drift)
        rows.append(
            {
                "instrument": instrument,
                "date": (start + timedelta(days=idx)).isoformat(),
                "open": round(price * 0.998, 4),
                "high": round(price * 1.004, 4),
                "low": round(price * 0.996, 4),
                "close": round(price, 4),
                "volume": 1_000_000 + idx * 1000,
            }
        )
    return rows


def _dataset_manifest() -> dict:
    return {
        "schema_version": "1.0",
        "task_id": "MGMT-QLIB-001",
        "manifest_id": "qlib-dataset-manifest:dataset-test-qlib-oos",
        "dataset_id": "dataset:test-qlib-oos",
        "strategy_id": "test-alpha",
        "source_strategy_spec_id": "strategy-spec:test-alpha-v1",
        "governed_dataset": {
            "source_dataset_refs": ["dataset:test-qlib-oos"],
            "governed": True,
            "num_instruments": 2,
            "history_years": 1.0,
            "data_frequency": "daily",
            "ohlcv_fields": ["open", "high", "low", "close", "volume"],
        },
    }


def _strategy_spec_packet() -> dict:
    return {
        "registry_entry": {
            "registry_id": "strategy-spec:test-alpha-v1",
            "version": "1.0.0",
        },
        "strategy_spec_binding": {
            "strategy_spec_id": "strategy-spec:test-alpha-v1",
            "source_strategy_spec_id": "strategy-spec:test-alpha-v1",
            "strategy_id": "test-alpha",
            "source_dataset_refs": ["dataset:test-qlib-oos"],
        },
    }
