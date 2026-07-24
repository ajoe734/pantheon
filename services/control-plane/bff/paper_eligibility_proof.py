"""Deterministic paper positive-control producer for PPL-ALLOC-009.

The acceptance proof needs a real producer to change the telemetry inputs used
by PM-12.  This module runs a small deterministic paper benchmark and builds a
canonical TelemetryEvent from its calculated result.  It does not write BFF
read models and it cannot select canary/live execution.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Mapping


TASK_ID = "PPL-ALLOC-009"
RUN_KEY = "30095677466"
BENCHMARK_VERSION = "ppl-alloc-009-paper-positive-control-v1"
OBSERVED_AT = "2026-07-24T13:40:00Z"
EXPECTED_IDEMPOTENCY_KEY = "ppl-alloc-009-30095677466-paper-eligibility-proof"

_STARTING_CAPITAL = 100_000.0
_TRADE_COUNT = 64
_RETURN_PER_TRADE = 0.0125
_SLIPPAGE_BPS = 0.5


def _stable_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def run_positive_control() -> dict[str, Any]:
    """Execute the versioned paper benchmark and return calculated metrics."""

    equity = _STARTING_CAPITAL
    peak = equity
    max_drawdown = 0.0
    filled = 0
    slippage_observations: list[float] = []

    for index in range(_TRADE_COUNT):
        requested_price = 100.0 + (index * 0.25)
        simulated_fill_price = requested_price * (1.0 + (_SLIPPAGE_BPS / 10_000.0))
        slippage_observations.append(
            abs(simulated_fill_price - requested_price) / requested_price * 10_000.0
        )
        filled += 1
        equity += _STARTING_CAPITAL * _RETURN_PER_TRADE
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)

    scenario = {
        "benchmark_version": BENCHMARK_VERSION,
        "starting_capital": _STARTING_CAPITAL,
        "trade_attempts": _TRADE_COUNT,
        "filled_trades": filled,
        "return_per_trade": _RETURN_PER_TRADE,
        "requested_price_start": 100.0,
        "requested_price_step": 0.25,
        "slippage_bps": _SLIPPAGE_BPS,
    }
    return {
        "scenario_digest": _stable_digest(scenario),
        "scenario": scenario,
        "metrics": {
            "pnl": round((equity - _STARTING_CAPITAL) / _STARTING_CAPITAL, 6),
            "drawdown": round(max_drawdown, 6),
            "fill_rate": round(filled / _TRADE_COUNT, 6),
            "avg_slippage_bps": round(
                sum(slippage_observations) / len(slippage_observations),
                6,
            ),
            "total_trades": filled,
        },
    }


def build_telemetry_event(
    *,
    persona_id: str,
    actor_id: str,
    idempotency_key: str,
    runtime_binding: Mapping[str, Any],
    strategy_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build one immutable canonical paper event from the benchmark result."""

    benchmark = run_positive_control()
    event_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                f"pantheon:{TASK_ID}:{RUN_KEY}:{BENCHMARK_VERSION}:"
                f"{persona_id}:{idempotency_key}"
            ),
        )
    )
    trace_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_id}:trace"))
    binding_id = str(
        runtime_binding.get("runtime_binding_id")
        or runtime_binding.get("binding_id")
        or runtime_binding.get("id")
        or ""
    ).strip()
    runtime_id = str(runtime_binding.get("runtime_id") or "").strip()
    plan_id = str(
        runtime_binding.get("plan_id")
        or runtime_binding.get("deployment_plan_id")
        or ""
    ).strip()

    event = {
        "event_id": event_id,
        "event_type": "pnl_snapshot",
        "created_at": OBSERVED_AT,
        "pnl_as_of": OBSERVED_AT,
        "drawdown_as_of": OBSERVED_AT,
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": binding_id,
        "runtime_id": runtime_id,
        "capital_pool_id": str(runtime_binding.get("capital_pool_id") or "").strip(),
        "artifact_id": str(runtime_binding.get("artifact_id") or "").strip(),
        "artifact_version": str(runtime_binding.get("artifact_version") or "").strip(),
        "plan_id": plan_id,
        "persona_capital_binding_id": str(
            runtime_binding.get("persona_capital_binding_id") or ""
        ).strip(),
        "target": {
            "strategy_id": strategy_id,
            "artifact_version": str(
                runtime_binding.get("artifact_version") or ""
            ).strip(),
            "artifact_type": "execution_bundle",
            "promotion_state": "paper",
        },
        "metrics": dict(benchmark["metrics"]),
        "trace_id": trace_id,
        "metadata": {
            "producer": "bff_governed_paper_positive_control",
            "task_id": TASK_ID,
            "run_key": RUN_KEY,
            "benchmark_version": BENCHMARK_VERSION,
            "scenario_digest": benchmark["scenario_digest"],
            "actor_id": actor_id,
            "paper_only": True,
            "is_real_capital": False,
            "is_real_order": False,
            "submitted_to_broker": False,
            "canary_execution_enabled": False,
            "live_execution_enabled": False,
        },
    }
    return event, benchmark
