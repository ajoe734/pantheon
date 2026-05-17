"""
FinRL adapter skeleton.
"""
from __future__ import annotations

from typing import Any, Mapping

try:
    from .engine.finrl_adapter import (
        DeferredPrepGate,
        FinRLDQNBackend,
        FinRLPPOBackend,
        PolicyTrainingConfig,
        StubFinRLBackend,
        run_finrl_workflow,
    )
except ImportError:  # service-local fallback for direct script/container execution
    from engine.finrl_adapter import (
        DeferredPrepGate,
        FinRLDQNBackend,
        FinRLPPOBackend,
        PolicyTrainingConfig,
        StubFinRLBackend,
        run_finrl_workflow,
    )


def train(strategy_spec_ref: Mapping[str, Any], backend: str = "finrl_ppo") -> Mapping[str, Any]:
    """
    Train a bounded offline FinRL DQN/PPO skeleton on governed OHLCV records.
    """
    backend_name = str(backend).strip().lower()
    dataset = {
        "dataset_id": strategy_spec_ref.get("dataset_id", "finrl-smoke-dataset"),
        "strategy_id": strategy_spec_ref.get("strategy_id", "finrl-smoke-strategy"),
        "source_dataset_refs": strategy_spec_ref.get("source_dataset_refs", ["smoke-ohlcv-001"]),
        "records": strategy_spec_ref.get("records", []),
    }
    for key in (
        "source_strategy_spec_id",
        "decision_focus",
        "data_frequency",
        "action_labels",
        "position_ratio",
        "cash_ratio",
    ):
        if key in strategy_spec_ref:
            dataset[key] = strategy_spec_ref[key]

    if backend_name in {"finrl", "ppo", "finrl_ppo"}:
        backend_instance = FinRLPPOBackend()
        algorithm = "ppo"
    elif backend_name in {"dqn", "finrl_dqn"}:
        backend_instance = FinRLDQNBackend()
        algorithm = "dqn"
    elif backend_name in {"stub", "stub_finrl"}:
        backend_instance = StubFinRLBackend()
        algorithm = "stub"
    else:
        raise ValueError(f"Unsupported backend: {backend}")

    result = run_finrl_workflow(
        dataset,
        backend=backend_instance,
        config=PolicyTrainingConfig(algorithm=algorithm),
    )

    return {
        "run_id": result.training_result.run_id,
        "backend": result.training_result.backend,
        "model_artifact_ref": result.registry_entry["registry_id"],
        "artifact_type": "model_artifact",
        "metrics": result.training_result.metrics,
        "status": "completed",
    }
