"""Ray Tune worker entry point for the governed deferred-prep container."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (
    RLlibTrainingConfig,
    RayTuneDeferredPrepGate,
    RayTuneImportBackend,
    RayTuneSearchConfig,
    StubRayTuneBackend,
    run_ray_tune_workflow,
)
from config import selected_tune_backend


def main() -> int:
    try:
        RayTuneDeferredPrepGate.require_env()
    except EnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    env_dataset_path = Path(
        os.environ.get(
            "RAYTUNE_DATASET_PATH",
            str(SERVICE_DIR / "examples" / "train_eval_input_sample.json"),
        )
    )
    if not env_dataset_path.exists():
        print(
            "RAYTUNE_DATASET_PATH not found; using sample dataset for deferred-prep smoke mode",
            file=sys.stderr,
        )
        env_dataset_path = SERVICE_DIR / "examples" / "train_eval_input_sample.json"

    dataset = json.loads(env_dataset_path.read_text(encoding="utf-8"))
    backend_name = selected_tune_backend()
    backend = StubRayTuneBackend() if backend_name == "stub" else RayTuneImportBackend()
    result = run_ray_tune_workflow(
        dataset,
        backend=backend,
        training_config=RLlibTrainingConfig(
            version=os.environ.get("RLLIB_ARTIFACT_VERSION", "1.0.0"),
            requested_by=os.environ.get("RLLIB_REQUESTED_BY", "worker"),
        ),
        search_config=RayTuneSearchConfig(
            version=os.environ.get("RAYTUNE_ARTIFACT_VERSION", "1.0.0"),
            requested_by=os.environ.get("RAYTUNE_REQUESTED_BY", "worker"),
            search_strategy=os.environ.get("RAYTUNE_SEARCH_STRATEGY", "pbt"),
            num_trials=int(os.environ.get("RAYTUNE_NUM_TRIALS", "16")),
            top_k=int(os.environ.get("RAYTUNE_TOP_K", "3")),
        ),
    )
    best_trial = result.search_result.best_trial
    output = {
        "registry_id": result.registry_entry["registry_id"],
        "artifact_type": result.registry_entry["artifact_type"],
        "artifact_state": result.registry_entry["artifact_state"],
        "deployment_stage": result.registry_entry["deployment_summary"]["current_stage"],
        "candidate_next_state": result.candidate_packet["requested_artifact_state"],
        "storage_path": result.registry_entry["storage_ref"]["path"],
        "backend": result.search_result.backend,
        "search_strategy": result.prepared_search.search_strategy,
        "num_trials": len(result.search_result.trial_results),
        "best_trial_id": best_trial.trial_id,
        "best_validation_sharpe_proxy": best_trial.metrics[
            result.prepared_search.objective_metric
        ],
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
