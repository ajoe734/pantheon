from __future__ import annotations

import importlib
import os
import sys
from types import SimpleNamespace

import pytest

from services.execution.lean_runtime.smoke_algorithm import (
    LEAN_ALGORITHM_PATH,
    SMOKE_BINDING_ID,
    SMOKE_PLAN_ID,
    SMOKE_SIGNAL_ID,
    SMOKE_STRATEGY_ID,
    SMOKE_VERSION,
    run_algorithm_smoke,
    run_algorithm_smoke_from_binding,
)


def _lean_submodule_available() -> bool:
    if str(LEAN_ALGORITHM_PATH) not in sys.path:
        sys.path.insert(0, str(LEAN_ALGORITHM_PATH))
    try:
        importlib.import_module("pantheon_algo.smoke_loader_test")
        return True
    except (ImportError, ModuleNotFoundError):
        return False


pytestmark = pytest.mark.skipif(
    not _lean_submodule_available(),
    reason="LEAN submodule (lean/Algorithm.Python/pantheon_algo) not initialised",
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
    assert result.loaded_signals == [result.loaded_signal]

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


def test_lean_algorithm_smoke_from_binding_loads_strategy_packet_targets() -> None:
    plan = {
        "plan_id": "lean-plan-packet-target-smoke",
        "approval_decision_id": "lean-approval-packet-target-smoke",
        "artifact_id": f"reg-{SMOKE_STRATEGY_ID}-{SMOKE_VERSION}",
        "artifact_version": SMOKE_VERSION,
        "artifact_type": "execution_bundle",
        "target_stage": "paper",
        "capital_pool_id": "pool-packet-target-smoke",
        "strategy_id": SMOKE_STRATEGY_ID,
    }
    binding = SimpleNamespace(
        binding_id="lean-binding-packet-target-smoke",
        runtime_id="lean-runtime-packet-target-smoke",
        plan_id=plan["plan_id"],
        artifact_id=plan["artifact_id"],
        artifact_version=plan["artifact_version"],
        capital_pool_id=plan["capital_pool_id"],
        deployment_mode="paper",
        persona_capital_binding_id="pcb-packet-target-smoke",
    )
    packet = {
        "packet_ref": "lean-strategy-packet://packet-target-smoke/generation2",
        "policy_id": "policy-packet-target-smoke-gen2",
        "generation": 2,
        "validation_window": "future_holdout",
    }
    targets = [
        {
            "target_ref": "lean-packet-target://packet-target-smoke/generation2/leg0",
            "leg_index": 0,
            "instrument": "US_MSFT",
            "execution_symbol": "MSFT.US",
            "lean_symbol": "MSFT",
            "generation": 2,
            "signal_id": "sig-packet-target-smoke-0",
            "quantity": 3,
            "quantity_type": "SHARES",
            "order_type": "MARKET",
            "signal": {
                "signal_id": "sig-packet-target-smoke-0",
                "version": "1.0",
                "strategy_id": SMOKE_STRATEGY_ID,
                "timestamp": "2026-01-05T14:30:00Z",
                "symbol": "MSFT.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 3,
                "quantity_type": "SHARES",
                "order_type": "MARKET",
                "metadata": {
                    "confidence_score": 1.0,
                    "strategy_packet_ref": packet["packet_ref"],
                    "packet_target_ref": "lean-packet-target://packet-target-smoke/generation2/leg0",
                    "market_data": {"close": 250.0},
                },
            },
        },
        {
            "target_ref": "lean-packet-target://packet-target-smoke/generation2/leg1",
            "leg_index": 1,
            "instrument": "US_AAPL",
            "execution_symbol": "AAPL.US",
            "lean_symbol": "AAPL",
            "generation": 2,
            "signal_id": "sig-packet-target-smoke-1",
            "quantity": 2,
            "quantity_type": "SHARES",
            "order_type": "MARKET",
            "signal": {
                "signal_id": "sig-packet-target-smoke-1",
                "version": "1.0",
                "strategy_id": SMOKE_STRATEGY_ID,
                "timestamp": "2026-01-05T14:30:00Z",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 2,
                "quantity_type": "SHARES",
                "order_type": "MARKET",
                "metadata": {
                    "strategy_packet_ref": packet["packet_ref"],
                    "packet_target_ref": "lean-packet-target://packet-target-smoke/generation2/leg1",
                },
            },
        },
        {
            "target_ref": "lean-packet-target://packet-target-smoke/generation2/leg2",
            "leg_index": 2,
            "instrument": "US_GOOG",
            "execution_symbol": "GOOG.US",
            "lean_symbol": "GOOG",
            "generation": 2,
            "signal_id": "sig-packet-target-smoke-2",
            "quantity": 1,
            "quantity_type": "SHARES",
            "order_type": "MARKET",
            "signal": {
                "signal_id": "sig-packet-target-smoke-2",
                "version": "1.0",
                "strategy_id": SMOKE_STRATEGY_ID,
                "timestamp": "2026-01-05T14:30:00Z",
                "symbol": "GOOG.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 1,
                "quantity_type": "SHARES",
                "order_type": "MARKET",
                "metadata": {
                    "strategy_packet_ref": packet["packet_ref"],
                    "packet_target_ref": "lean-packet-target://packet-target-smoke/generation2/leg2",
                },
            },
        },
    ]

    result = run_algorithm_smoke_from_binding(
        plan,
        binding,
        strategy_packet=packet,
        packet_targets=targets,
    )

    assert result.loaded_strategy_packet == packet
    assert [target["target_ref"] for target in result.loaded_packet_targets] == [
        target["target_ref"] for target in targets
    ]
    assert [signal["signal_id"] for signal in result.loaded_signals] == [
        target["signal_id"] for target in targets
    ]
    assert [signal["symbol"] for signal in result.loaded_signals] == [
        target["execution_symbol"] for target in targets
    ]
    assert [execution["target_ref"] for execution in result.packet_target_executions] == [
        target["target_ref"] for target in targets
    ]
    assert [execution["loaded_signal_id"] for execution in result.packet_target_executions] == [
        target["signal_id"] for target in targets
    ]
    assert [execution["loaded_signal_symbol"] for execution in result.packet_target_executions] == [
        target["execution_symbol"] for target in targets
    ]
    assert all(execution["fill_count"] >= 1 for execution in result.packet_target_executions)
    assert all(all(execution["replay"].values()) for execution in result.packet_target_executions)
    assert result.loaded_signal["signal_id"] == targets[0]["signal_id"]
    assert result.loaded_signal["symbol"] == "MSFT.US"
    assert result.synthetic_ohlcv[0]["symbol"] == "MSFT"
    assert result.fill_events[0]["signal_id"] == targets[0]["signal_id"]
    assert result.fill_events[0]["symbol"] == "MSFT"
    assert result.artifact_payload_checksum == result.loaded_metadata["checksum"]
