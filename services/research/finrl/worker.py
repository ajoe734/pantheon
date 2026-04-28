"""FinRL worker entry point for the governed deferred-prep container."""
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
    FinRLPPOBackend,
    PolicyTrainingConfig,
    StubFinRLBackend,
    run_finrl_workflow,
)
from config import selected_backend


def main() -> int:
    try:
        DeferredPrepGate.require_env()
    except EnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    dataset_path = os.environ.get("FINRL_DATASET_PATH")
    if not dataset_path:
        print(
            "FINRL_DATASET_PATH not set; using sample dataset for deferred-prep smoke mode",
            file=sys.stderr,
        )
        dataset_path = str(SERVICE_DIR / "examples" / "policy_input_sample.json")

    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    backend_name = selected_backend()
    backend = StubFinRLBackend() if backend_name == "stub" else FinRLPPOBackend()
    result = run_finrl_workflow(
        dataset,
        backend=backend,
        config=PolicyTrainingConfig(
            version=os.environ.get("FINRL_ARTIFACT_VERSION", "1.0.0"),
            requested_by=os.environ.get("FINRL_REQUESTED_BY", "worker"),
        ),
    )
    output = {
        "registry_id": result.registry_entry["registry_id"],
        "artifact_state": result.registry_entry["artifact_state"],
        "deployment_stage": result.registry_entry["deployment_summary"]["current_stage"],
        "candidate_next_state": result.candidate_packet["requested_artifact_state"],
        "storage_path": result.registry_entry["storage_ref"]["path"],
        "backend": result.training_result.backend,
        "metrics": result.training_result.metrics,
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
