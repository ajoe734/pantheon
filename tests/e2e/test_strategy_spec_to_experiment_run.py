"""OODA StrategySpec -> ExperimentRun transition e2e test."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from services.research.experiment_orchestrator.parallel_dispatch import run_parallel
from services.research.experiments.models import (
    ExperimentTask,
    validate_experiment_run_against_task,
)
from services.research.strategy_spec.models import StrategySpec, validate_strategy_spec_payload


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "strategy_spec_for_experiment.json"


def test_strategy_spec_submits_vectorbt_experiment_and_produces_lineaged_run(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_VECTORBT_BACKEND", "stub")

    strategy_spec_payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    validate_strategy_spec_payload(strategy_spec_payload)
    strategy_spec = StrategySpec.from_dict(strategy_spec_payload)

    experiment_task = _experiment_task_from_strategy_spec(strategy_spec)
    result = run_parallel(
        experiment_task,
        ["vectorbt"],
        created_at="2026-05-17T11:12:00Z",
        max_workers=1,
    )

    assert result.failures == {}
    assert result.comparison_summary["successful_backends"] == ["vectorbt"]
    assert result.comparison_summary["failed_backends"] == []
    assert result.comparison_summary["sharpe_by_backend"]["vectorbt"] is not None

    assert len(result.runs) == 1
    run = result.runs[0]
    assert run.status == "completed"
    assert run.backend_id == "vectorbt"
    assert run.artifact_refs
    assert run.metric_bundle_id
    assert run.output_manifest_ref
    assert validate_experiment_run_against_task(run, experiment_task) == []

    metadata = dict(run.metadata)
    assert metadata["producer_run_id"] == run.run_id
    assert metadata["evaluation_summary"]["producer_run_id"] == run.run_id
    assert metadata["evaluation_summary"]["evaluation_kind"] == "vectorbt_backtest"
    assert metadata["evaluation_summary"]["aggregate_metrics"]["total_trades"] > 0
    assert metadata["registry_entry"]["producer_run_id"] == run.run_id

    expected_strategy_spec_id = strategy_spec.metadata["strategy_spec_artifact_id"]
    lineage = metadata["lineage"]
    assert lineage["source_strategy_spec_id"] == expected_strategy_spec_id
    assert run.run_id in lineage["source_run_ids"]
    assert strategy_spec.metadata["dataset_version_id"] in lineage["source_dataset_refs"]
    assert metadata["registry_entry"]["lineage"]["source_strategy_spec_id"] == expected_strategy_spec_id


def _experiment_task_from_strategy_spec(strategy_spec: StrategySpec) -> ExperimentTask:
    metadata = dict(strategy_spec.metadata)
    dataset_version_id = str(metadata["dataset_version_id"])
    code_version = str(metadata["code_version"])
    backend_id = str(metadata["backend_id"])
    task_id = str(metadata["experiment_task_id"])
    strategy_spec_version = str(metadata["strategy_spec_version"])
    source_strategy_spec_id = str(metadata["strategy_spec_artifact_id"])

    return ExperimentTask.from_dict(
        {
            "task_id": task_id,
            "strategy_id": strategy_spec.strategy_id,
            "strategy_spec_version": strategy_spec_version,
            "requested_by": "Codex2",
            "task_type": "backtest",
            "backend_id": backend_id,
            "backend_selection_policy_id": "research-ooda-e2e-vectorbt-policy-v1",
            "dataset_version_id": dataset_version_id,
            "code_version": code_version,
            "priority": "normal",
            "status": "ready",
            "idempotency_key": "ooda-e2e-002-vectorbt",
            "trace_id": "trace-ooda-e2e-002",
            "created_at": "2026-05-17T11:11:00Z",
            "metadata": {
                "task_id": "OODA-E2E-002",
                "research_only": True,
                "source_strategy_spec_id": source_strategy_spec_id,
                "backend_inputs": {
                    backend_id: {
                        "dataset": _vectorbt_dataset(strategy_spec),
                        "config": dict(metadata["backtest_config"]),
                    }
                },
            },
        }
    )


def _vectorbt_dataset(strategy_spec: StrategySpec) -> dict[str, Any]:
    metadata = dict(strategy_spec.metadata)
    synthetic_config = dict(metadata["synthetic_ohlcv"])
    source_dataset_refs = [
        dependency.ref
        for dependency in strategy_spec.data_dependencies
        if dependency.kind == "dataset"
    ]
    return {
        "dataset_id": metadata["dataset_version_id"],
        "strategy_id": strategy_spec.strategy_id,
        "source_dataset_refs": source_dataset_refs,
        "source_strategy_spec_id": metadata["strategy_spec_artifact_id"],
        "data_frequency": synthetic_config["data_frequency"],
        "records": _synthetic_ohlcv_records(
            strategy_spec.market_scope.symbols,
            synthetic_config,
        ),
    }


def _synthetic_ohlcv_records(symbols: tuple[str, ...], config: dict[str, Any]) -> list[dict[str, Any]]:
    start = date.fromisoformat(str(config["start_date"]))
    bars = int(config["bars"])
    base_prices = dict(config["base_prices"])
    records: list[dict[str, Any]] = []

    for symbol_index, symbol in enumerate(symbols):
        price = float(base_prices[symbol])
        for offset in range(bars):
            drift = 0.006 if offset % 6 in {1, 2, 3, 4} else -0.004
            price *= 1.0 + drift + symbol_index * 0.0005
            records.append(
                {
                    "instrument": symbol,
                    "date": (start + timedelta(days=offset)).isoformat(),
                    "open": round(price * 0.997, 4),
                    "high": round(price * 1.006, 4),
                    "low": round(price * 0.994, 4),
                    "close": round(price, 4),
                    "volume": 1_000_000 + symbol_index * 100_000 + offset * 7_500,
                }
            )
    return records
