"""RLlib worker entry point for the governed deferred-prep container."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (
    DeferredPrepGate,
    RLlibPPOBackend,
    RLlibTrainingConfig,
    StubRLlibBackend,
    run_rllib_workflow,
)
from config import selected_backend


def _persist_artifacts(result) -> dict[str, str]:
    output_dir = Path(os.environ.get("RLLIB_OUTPUT_DIR", "/tmp/pantheon/research/rllib"))
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "artifact_bundle": result.artifact_bundle,
        "registry_entry": result.registry_entry,
        "candidate_packet": result.candidate_packet,
    }
    paths: dict[str, str] = {}
    for name, payload in artifacts.items():
        path = output_dir / f"{result.train_eval_result.run_id}.{name}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        paths[name] = str(path)
    return paths


def main() -> int:
    try:
        DeferredPrepGate.require_env()
    except EnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    env_dataset_path = Path(
        os.environ.get(
            "RLLIB_DATASET_PATH",
            str(SERVICE_DIR / "examples" / "train_eval_input_sample.json"),
        )
    )
    if not env_dataset_path.exists():
        print(
            "RLLIB_DATASET_PATH not found; using sample dataset for deferred-prep smoke mode",
            file=sys.stderr,
        )
        env_dataset_path = SERVICE_DIR / "examples" / "train_eval_input_sample.json"

    dataset = json.loads(env_dataset_path.read_text(encoding="utf-8"))
    backend_name = selected_backend()
    backend = StubRLlibBackend() if backend_name == "stub" else RLlibPPOBackend()
    result = run_rllib_workflow(
        dataset,
        backend=backend,
        config=RLlibTrainingConfig(
            version=os.environ.get("RLLIB_ARTIFACT_VERSION", "1.0.0"),
            requested_by=os.environ.get("RLLIB_REQUESTED_BY", "worker"),
        ),
    )
    output = {
        "registry_id": result.registry_entry["registry_id"],
        "artifact_state": result.registry_entry["artifact_state"],
        "deployment_stage": result.registry_entry["deployment_summary"]["current_stage"],
        "candidate_next_state": result.candidate_packet["requested_artifact_state"],
        "storage_path": result.registry_entry["storage_ref"]["path"],
        "artifact_paths": _persist_artifacts(result),
        "backend": result.train_eval_result.backend,
        "train_steps": result.prepared_dataset.num_train_steps,
        "eval_steps": result.prepared_dataset.num_eval_steps,
        "search_strategy": result.train_eval_result.train_eval_schema["search_space"]["strategy"],
        "validation_sharpe_proxy": result.train_eval_result.metrics["validation_sharpe_proxy"],
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
