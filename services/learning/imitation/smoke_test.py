from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (
    ImitationBehaviorCloningBackend,
    StubBehaviorCloningBackend,
    TrainingConfig,
    run_imitation_workflow,
)


def load_sample_dataset() -> dict:
    sample_path = SERVICE_DIR / "examples" / "trajectory_dataset_sample.json"
    return json.loads(sample_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the LP-002 governed imitation smoke test.")
    parser.add_argument(
        "--backend",
        choices=("stub", "imitation"),
        default="stub",
        help="Select stub (default) or upstream imitation backend.",
    )
    args = parser.parse_args(argv)

    backend = StubBehaviorCloningBackend() if args.backend == "stub" else ImitationBehaviorCloningBackend()
    result = run_imitation_workflow(
        load_sample_dataset(),
        backend=backend,
        config=TrainingConfig(
            version="0.1.0",
            requested_by="Codex",
            epochs=1,
        ),
    )

    print("LP-002 smoke test complete")
    print(f"  backend: {result.training_result.backend}")
    print(f"  trajectories: {result.prepared_dataset.num_trajectories}")
    print(f"  transitions: {result.prepared_dataset.num_transitions}")
    print(f"  registry_id: {result.registry_entry['registry_id']}")
    print(f"  storage_path: {result.registry_entry['storage_ref']['path']}")
    print(f"  checksum: {result.registry_entry['checksum']}")
    print("  evaluation_summary:")
    for key, value in sorted(result.training_result.metrics.items()):
        print(f"    - {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
