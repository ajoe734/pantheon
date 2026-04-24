"""Qlib LightGBM worker entry point for the governed research container."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import TrainingConfig, run_qlib_workflow
from adapter.qlib_adapter import QlibLightGBMBackend, StubLightGBMBackend


def main() -> int:
    dataset_path = os.environ.get("QLIB_DATASET_PATH")
    if not dataset_path:
        print("QLIB_DATASET_PATH not set; using sample dataset for smoke-test mode", file=sys.stderr)
        dataset_path = str(SERVICE_DIR / "examples" / "equity_dataset_sample.json")

    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    config = TrainingConfig(
        version=os.environ.get("QLIB_ARTIFACT_VERSION", "1.0.0"),
        requested_by=os.environ.get("QLIB_REQUESTED_BY", "worker"),
        n_estimators=int(os.environ.get("QLIB_N_ESTIMATORS", "200")),
    )

    use_real = os.environ.get("QLIB_BACKEND", "stub").lower() == "real"
    backend = QlibLightGBMBackend() if use_real else StubLightGBMBackend()
    if use_real:
        print("QLIB_BACKEND=real: using QlibLightGBMBackend", file=sys.stderr)

    result = run_qlib_workflow(dataset, backend=backend, config=config)
    output = {
        "registry_id": result.registry_entry["registry_id"],
        "artifact_state": result.registry_entry["artifact_state"],
        "deployment_stage": result.registry_entry["deployment_summary"]["current_stage"],
        "storage_path": result.registry_entry["storage_ref"]["path"],
        "checksum": result.registry_entry["checksum"],
        "metrics": result.training_result.metrics,
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
