from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest


SERVICE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVICE_DIR))

from rapid_eval_integration import RapidEvalInputError, run_rapid_eval  # noqa: E402


def _synthetic_records() -> list[dict]:
    records: list[dict] = []
    start = date(2026, 1, 1)
    for symbol_index, instrument in enumerate(("AAA", "BBB")):
        price = 100.0 + symbol_index * 23.0
        for index in range(42):
            change = 0.007 if (index + symbol_index) % 4 else -0.012
            price *= 1.0 + change
            records.append(
                {
                    "instrument": instrument,
                    "date": (start + timedelta(days=index)).isoformat(),
                    "open": round(price * 0.997, 4),
                    "high": round(price * 1.006, 4),
                    "low": round(price * 0.994, 4),
                    "close": round(price, 4),
                    "volume": 900_000 + index * 2_500,
                }
            )
    return records


def test_run_rapid_eval_calls_vectorbt_adapter_and_returns_eval_summary() -> None:
    summary = run_rapid_eval(
        {
            "patch_id": "persona-patch-alpha-v1",
            "strategy_params": {"short_window": 3, "long_window": 9},
        },
        {
            "strategy_id": "strategy-alpha",
            "source_strategy_spec_id": "strategy-spec-alpha-v1",
            "source_dataset_refs": ["dataset:deterministic-synthetic-v1"],
            "data_frequency": "daily",
            "records": _synthetic_records(),
        },
        requested_by="pytest",
    )

    assert set(("sharpe", "sortino", "max_dd")).issubset(summary)
    assert summary["eval_summary"] == {
        "sharpe": summary["sharpe"],
        "sortino": summary["sortino"],
        "max_dd": summary["max_dd"],
    }
    assert summary["framework"] == "vectorbt"
    assert summary["backend"] == "stub_backtest"
    assert summary["registry_entry"]["artifact_type"] == "backtest_result"
    assert summary["lineage"]["source_dataset_refs"] == ["dataset:deterministic-synthetic-v1"]
    assert summary["strategy_params"]["short_window"] == 3
    assert summary["strategy_params"]["long_window"] == 9
    assert summary["governance"]["direct_live_influence"] is False


def test_run_rapid_eval_fails_fast_for_missing_persona_patch_ref() -> None:
    with pytest.raises(RapidEvalInputError, match="persona_patch_ref"):
        run_rapid_eval(
            "",
            {
                "strategy_id": "strategy-alpha",
                "records": _synthetic_records(),
                "source_dataset_refs": ["dataset:deterministic-synthetic-v1"],
            },
        )
