"""Governed Qlib LightGBM smoke test — LP-003 supervised alpha path."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (
    QlibLightGBMBackend,
    StubLightGBMBackend,
    TrainingConfig,
    run_qlib_workflow,
)


def load_sample_dataset() -> dict:
    sample_path = SERVICE_DIR / "examples" / "equity_dataset_sample.json"
    return json.loads(sample_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the LP-003 governed Qlib smoke test.")
    parser.add_argument(
        "--backend",
        choices=("stub", "qlib", "real"),
        default="stub",
        help="Select stub (default) or upstream Qlib backend. 'real' is an alias for qlib.",
    )
    args = parser.parse_args(argv)

    backend = StubLightGBMBackend() if args.backend == "stub" else QlibLightGBMBackend()
    result = run_qlib_workflow(
        load_sample_dataset(),
        backend=backend,
        config=TrainingConfig(
            version="1.0.0",
            requested_by="Claude",
            n_estimators=10,
        ),
    )

    entry = result.registry_entry
    bundle = result.artifact_bundle
    ds = result.prepared_dataset
    metrics = result.training_result.metrics

    print("LP-003 Qlib smoke test complete")
    print(f"  backend:          {result.training_result.backend}")
    print(f"  instruments:      {ds.num_instruments}")
    print(f"  samples:          {ds.num_samples}")
    print(f"  features:         {ds.num_features}")
    print(f"  registry_id:      {entry['registry_id']}")
    print(f"  artifact_state:   {entry['artifact_state']}")
    print(f"  deployment_stage: {entry['deployment_summary']['current_stage']}")
    print(f"  storage_path:     {entry['storage_ref']['path']}")
    print(f"  checksum:         {entry['checksum']}")
    print(f"  artifact_family:  {bundle['artifact_family']}")
    print("  metrics:")
    for key, value in sorted(metrics.items()):
        print(f"    - {key}: {value}")

    assert entry["artifact_state"] == "draft", "artifact_state must start as draft"
    assert entry["deployment_summary"]["current_stage"] == "none", "deployment stage must be none"
    assert entry["checksum"].startswith("sha256:"), "checksum must be sha256-prefixed"
    assert ds.num_instruments >= 2, "need at least 2 instruments"
    assert ds.num_samples > 0, "need at least one sample"

    print("  assertions: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
