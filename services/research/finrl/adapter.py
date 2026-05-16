"""
FinRL adapter skeleton.
"""
from typing import Any, Mapping
from .adapter.finrl_adapter import run_finrl_workflow, FinRLPPOBackend, PolicyTrainingConfig

def train(strategy_spec_ref: Mapping[str, Any], backend: str = "finrl_ppo") -> Mapping[str, Any]:
    """
    Adapter train function.
    """
    # This is a skeleton, need to adapt strategy_spec_ref to the format expected by run_finrl_workflow
    # The requirement says train(strategy_spec_ref backend)
    
    # Placeholder dataset mapping based on what finrl_adapter expects
    dataset = {
        "dataset_id": "finrl-smoke-dataset",
        "strategy_id": "finrl-smoke-strategy",
        "source_dataset_refs": ["smoke-ohlcv-001"],
        "records": strategy_spec_ref.get("records", []),
    }
    
    backend_instance = FinRLPPOBackend() if backend == "finrl_ppo" else None
    
    result = run_finrl_workflow(dataset, backend=backend_instance)
    
    # Return ExperimentRun dict
    return {
        "run_id": result.training_result.run_id,
        "model_artifact_ref": result.registry_entry["registry_id"],
        "metrics": result.training_result.metrics,
        "status": "completed"
    }
