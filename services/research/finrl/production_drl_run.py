"""TWSE FinRL offline DRL evidence run.

This script exercises the governed FinRL PPO backend over deterministic TWSE
OHLCV records and persists review artifacts. It is intentionally an
offline-review path: it does not authorize production or live execution.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

try:
    from .engine.finrl_adapter import (
        FINRL_VERSION_PIN,
        GovernedFinRLPolicyAdapter,
        PolicyTrainingConfig,
        PolicyTrainingResult,
        build_finrl_run_result,
    )
    from .registry_admission_packet import generate_admission_packet
    from .twse_stock_env import TWSESerialEnv
except ImportError:  # service-local fallback for direct script/container execution
    from engine.finrl_adapter import (
        FINRL_VERSION_PIN,
        GovernedFinRLPolicyAdapter,
        PolicyTrainingConfig,
        PolicyTrainingResult,
        build_finrl_run_result,
    )
    from registry_admission_packet import generate_admission_packet
    from twse_stock_env import TWSESerialEnv

DEFAULT_OUTPUT_DIR = Path("support/evidence/OSS-FINRL-V2-001")
PRODUCTION_BACKEND = "finrl_stocktradingenv_sb3_ppo"
PRODUCTION_FIT_MODE = "upstream_finrl_stocktradingenv_stable_baselines3_ppo"
PRODUCTION_TOTAL_TIMESTEPS = 1024


class ProductionDRLError(RuntimeError):
    """Raised when the production FinRL evidence path cannot run safely."""


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
    env = TWSESerialEnv(records)
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
        "twse_stock_env": env.environment_summary(),
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_ppo_runtime():
    try:
        import stable_baselines3
        import torch
        from stable_baselines3 import PPO
    except ImportError as exc:
        raise ProductionDRLError(
            "stable-baselines3 and CPU torch are required for production FinRL PPO evidence"
        ) from exc
    return PPO, stable_baselines3.__version__, torch.__version__, bool(torch.cuda.is_available())


def _calculate_evaluation_metrics(asset_memory: list[float]) -> dict[str, float]:
    if len(asset_memory) < 2:
        raise ProductionDRLError("evaluation asset_memory must contain at least two portfolio values")
    returns = [
        (asset_memory[index] - asset_memory[index - 1]) / max(asset_memory[index - 1], 1e-9)
        for index in range(1, len(asset_memory))
    ]
    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / max(len(returns), 1)
    stddev = math.sqrt(variance)
    sharpe = (mean_return / (stddev + 1e-9)) * math.sqrt(252)
    total_return = (asset_memory[-1] - asset_memory[0]) / max(asset_memory[0], 1e-9)
    years = max(len(returns), 1) / 252.0
    annual_return = (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1.0 else -0.99
    peak = asset_memory[0]
    max_drawdown = 0.0
    for value in asset_memory:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, (peak - value) / max(peak, 1e-9))
    return {
        "sharpe": round(sharpe, 4),
        "annual_return": round(annual_return, 6),
        "max_drawdown": round(max_drawdown, 6),
        "portfolio_value_initial": round(asset_memory[0], 8),
        "portfolio_value_final": round(asset_memory[-1], 8),
    }


def _train_upstream_ppo(
    records: list[Mapping[str, Any]],
    *,
    total_timesteps: int = PRODUCTION_TOTAL_TIMESTEPS,
) -> tuple[PolicyTrainingResult, dict[str, Any]]:
    PPO, sb3_version, torch_version, cuda_available = _load_ppo_runtime()
    train_wrapper = TWSESerialEnv(records, use_finrl=True)
    if not train_wrapper.finrl_available:
        raise ProductionDRLError(
            "FinRL StockTradingEnv could not be constructed; refusing to emit passing evidence"
        )

    model = PPO(
        "MlpPolicy",
        train_wrapper.finrl_env,
        n_steps=64,
        batch_size=64,
        n_epochs=1,
        learning_rate=3e-4,
        gamma=0.99,
        seed=42,
        device="cpu",
        verbose=0,
    )
    model.learn(total_timesteps=total_timesteps)

    eval_wrapper = TWSESerialEnv(records, use_finrl=True)
    eval_env = eval_wrapper.finrl_env
    observation, _info = eval_env.reset()
    terminated = False
    truncated = False
    evaluation_steps = 0
    while not (terminated or truncated):
        action, _state = model.predict(observation, deterministic=True)
        observation, _reward, terminated, truncated, _info = eval_env.step(action)
        evaluation_steps += 1

    asset_memory = [float(value) for value in eval_env.asset_memory]
    metrics = _calculate_evaluation_metrics(asset_memory)
    metrics.update(
        {
            "algorithm": "ppo",
            "backend": PRODUCTION_BACKEND,
            "fit_mode": PRODUCTION_FIT_MODE,
            "framework_import_ready": True,
            "stock_trading_env_used": True,
            "stable_baselines3_version": sb3_version,
            "torch_version": torch_version,
            "torch_cuda_available": cuda_available,
            "device": "cpu",
            "num_instruments": train_wrapper.stock_dim,
            "num_periods": len(train_wrapper.dates),
            "num_steps": evaluation_steps,
            "total_training_steps": int(model.num_timesteps),
            "total_trades": int(eval_env.trades),
        }
    )
    policy_payload = {
        "algorithm": "ppo",
        "backend": PRODUCTION_BACKEND,
        "fit_mode": PRODUCTION_FIT_MODE,
        "framework_import": "finrl",
        "framework_version": FINRL_VERSION_PIN,
        "stable_baselines3_version": sb3_version,
        "torch_version": torch_version,
        "device": "cpu",
        "upstream_env_class": "finrl.meta.env_stock_trading.env_stocktrading.StockTradingEnv",
        "stock_dim": train_wrapper.stock_dim,
        "state_space": train_wrapper.state_space,
        "action_space": train_wrapper.action_space,
        "total_training_steps": int(model.num_timesteps),
        "policy_class": type(model.policy).__name__,
        "model_seed": 42,
        "run_materialized": True,
    }
    training_result = PolicyTrainingResult(
        backend=PRODUCTION_BACKEND,
        run_id=f"finrl-ppo-{uuid.uuid4().hex[:12]}",
        policy_payload=policy_payload,
        metrics=metrics,
        notes=(
            "Upstream FinRL StockTradingEnv was constructed from TWSE OHLCV records.",
            "stable-baselines3 PPO trained CPU-only with device='cpu'.",
            "Registry write, broker session, order route, capital binding, and deployment remain disabled.",
        ),
    )
    return training_result, train_wrapper.environment_summary()


def main(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    total_timesteps: int = PRODUCTION_TOTAL_TIMESTEPS,
):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    records = load_twse_data(periods=200)
    dataset_config = build_dataset_config(records)
    training_result, env_summary = _train_upstream_ppo(records, total_timesteps=total_timesteps)
    dataset_config["twse_stock_env"] = env_summary
    prepared = GovernedFinRLPolicyAdapter().prepare(
        dataset_config,
        lookback_window=PolicyTrainingConfig().lookback_window,
    )
    config = PolicyTrainingConfig(
        algorithm="ppo",
        requested_by="Codex2",
        storage_path_template="research/finrl/offline/{strategy_id}/{version}/artifact.json",
        governance_scope="offline_registry_admission_evidence_only",
    )
    result = build_finrl_run_result(
        prepared,
        training_result,
        config,
        environment_metadata=env_summary,
    )

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
