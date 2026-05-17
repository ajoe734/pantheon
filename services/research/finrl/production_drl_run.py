"""TWSE FinRL offline DRL evidence run.

This script exercises the governed FinRL PPO backend over deterministic TWSE
OHLCV records and persists review artifacts. It is intentionally an
offline-review path: it does not authorize production or live execution.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

try:
    from .engine.finrl_adapter import (
        FinRLPPOBackend,
        PolicyTrainingConfig,
        run_finrl_workflow,
    )
    from .registry_admission_packet import generate_admission_packet
except ImportError:  # service-local fallback for direct script/container execution
    from engine.finrl_adapter import (
        FinRLPPOBackend,
        PolicyTrainingConfig,
        run_finrl_workflow,
    )
    from registry_admission_packet import generate_admission_packet

DEFAULT_OUTPUT_DIR = Path("support/evidence/OSS-FINRL-V2-001")


def load_twse_data(periods: int = 48) -> list[dict[str, Any]]:
    """Build deterministic TWSE-shaped OHLCV records without pandas."""
    tickers = ("2330", "2317", "2454", "2308", "2002")
    records: list[dict[str, Any]] = []
    for ticker_index, ticker in enumerate(tickers):
        base = 80.0 + ticker_index * 17.5
        start_date = date(2024, 1, 1)
        for day in range(periods):
            current_date = start_date + timedelta(days=day)
            trend = day * (0.25 + ticker_index * 0.03)
            cycle = (day % 5) * 0.11
            open_price = base + trend + cycle
            close_price = open_price + 0.35 + (ticker_index * 0.04)
            records.append(
                {
                    "date": current_date.isoformat(),
                    "instrument": ticker,
                    "open": round(open_price, 4),
                    "high": round(close_price + 0.8, 4),
                    "low": round(open_price - 0.7, 4),
                    "close": round(close_price, 4),
                    "volume": float(100000 + ticker_index * 7500 + day * 250),
                }
            )
    return records


def build_dataset_config(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "dataset_id": "twse-offline-dataset-001",
        "strategy_id": "twse-ppo-strategy-001",
        "source_dataset_refs": ["twse-ohlcv-offline-sample-2024-001"],
        "source_strategy_spec_id": "strategy-spec:twse-ppo-offline-v1",
        "records": list(records),
        "decision_focus": "exit_timing",
        "data_frequency": "daily",
        "action_labels": ["hold", "buy_small", "sell_small", "buy_large", "sell_large"],
        "position_ratio": 0.2,
        "cash_ratio": 0.8,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main(output_dir: str | Path = DEFAULT_OUTPUT_DIR):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    records = load_twse_data(periods=90)
    dataset_config = build_dataset_config(records)
    config = PolicyTrainingConfig(
        algorithm="ppo",
        requested_by="Codex2",
        storage_path_template="research/finrl/offline/{strategy_id}/{version}/artifact.json",
    )
    result = run_finrl_workflow(dataset_config, backend=FinRLPPOBackend(), config=config)

    artifact_paths = {
        "evaluation_summary": output_path / "evaluation_summary.json",
        "artifact_bundle": output_path / "artifact_bundle.json",
        "registry_entry": output_path / "registry_entry.json",
        "candidate_packet": output_path / "candidate_packet.json",
        "admission_packet": output_path / "admission_packet.json",
    }
    _write_json(artifact_paths["evaluation_summary"], result.training_result.metrics)
    _write_json(artifact_paths["artifact_bundle"], result.artifact_bundle)
    _write_json(artifact_paths["registry_entry"], result.registry_entry)
    _write_json(artifact_paths["candidate_packet"], result.candidate_packet)
    generate_admission_packet(
        result,
        output_path=artifact_paths["admission_packet"],
        evidence_refs={
            name: str(path)
            for name, path in artifact_paths.items()
            if name != "admission_packet"
        },
    )

    print(
        json.dumps(
            {"run_id": result.training_result.run_id, "metrics": result.training_result.metrics},
            indent=2,
        )
    )
    return result


if __name__ == "__main__":
    main()
