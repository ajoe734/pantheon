from __future__ import annotations

import os

from services.execution.lean_runtime.smoke_algorithm import (
    SMOKE_BINDING_ID,
    SMOKE_PLAN_ID,
    SMOKE_SIGNAL_ID,
    SMOKE_STRATEGY_ID,
    SMOKE_VERSION,
    run_algorithm_smoke,
)


def test_lean_algorithm_smoke_loads_paper_artifact_and_records_one_fill() -> None:
    original_broker_flag = os.environ.get("BROKER_PRODUCTION_LIVE_ENABLED")

    result = run_algorithm_smoke()

    assert result.synthetic_bar_count == 5
    assert result.raw_on_data_callbacks == 5
    assert result.executed_on_data_callbacks == 1
    assert result.fill_count == 1

    assert result.loaded_metadata["strategy_id"] == SMOKE_STRATEGY_ID
    assert result.loaded_metadata["version"] == SMOKE_VERSION
    assert result.loaded_metadata["artifact_state"] == "approved"
    assert result.loaded_metadata["deployment_stage"] == "paper"
    assert result.loaded_signal["signal_id"] == SMOKE_SIGNAL_ID

    fill = result.fill_events[0]
    assert fill["signal_id"] == SMOKE_SIGNAL_ID
    assert fill["symbol"] == "AAPL"
    assert fill["quantity"] == result.loaded_signal["quantity"]
    assert fill["action"] == "market_order"
    assert fill["submitted_to_broker"] is False

    assert result.runtime_context["runtime_binding_id"] == SMOKE_BINDING_ID
    assert result.runtime_context["deployment_plan_id"] == SMOKE_PLAN_ID
    assert result.runtime_context["deployment_stage"] == "paper"
    assert result.bootstrap_env["PANTHEON_LIVE_BROKER_ENABLED"] == "false"
    assert result.broker_production_live_enabled == "false"
    assert os.environ.get("BROKER_PRODUCTION_LIVE_ENABLED") == original_broker_flag


def test_lean_algorithm_smoke_materializes_canonical_object_store_keys() -> None:
    result = run_algorithm_smoke()

    assert result.object_store_keys == [
        f"openclaw/registry/{SMOKE_STRATEGY_ID}/{SMOKE_VERSION}/artifact.bin",
        f"openclaw/registry/{SMOKE_STRATEGY_ID}/{SMOKE_VERSION}/metadata.json",
    ]
    assert all(bar["symbol"] == "AAPL" for bar in result.synthetic_ohlcv)
    assert [bar["trading_date"] for bar in result.synthetic_ohlcv] == [
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
        "2026-01-08",
        "2026-01-09",
    ]
