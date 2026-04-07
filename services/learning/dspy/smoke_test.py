from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (
    DSPyBootstrapFewShotBackend,
    StubBootstrapFewShotBackend,
    TrainingConfig,
    run_dspy_workflow,
)


def load_sample_dataset() -> dict:
    sample_path = SERVICE_DIR / "examples" / "preference_dataset_sample.json"
    return json.loads(sample_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the LP-001 governed DSPy smoke test.")
    parser.add_argument(
        "--backend",
        choices=("stub", "dspy"),
        default="stub",
        help="Select stub (default) or the optional upstream DSPy backend.",
    )
    args = parser.parse_args(argv)

    backend = StubBootstrapFewShotBackend() if args.backend == "stub" else DSPyBootstrapFewShotBackend()
    result = run_dspy_workflow(
        load_sample_dataset(),
        backend=backend,
        config=TrainingConfig(version="0.1.0", requested_by="Codex"),
    )

    evaluation_summary = result.artifact_bundle["prompt_bundle"]["evaluation_summary"]
    print("LP-001 smoke test complete")
    print(f"  backend: {result.training_result.backend}")
    print(f"  training_examples: {len(result.prepared_dataset.training_examples)}")
    print(f"  evaluation_examples: {len(result.prepared_dataset.evaluation_examples)}")
    print(f"  registry_id: {result.registry_entry['registry_id']}")
    print(f"  storage_path: {result.registry_entry['storage_ref']['path']}")
    print(f"  checksum: {result.registry_entry['checksum']}")
    print("  evaluation_summary:")
    for key, value in sorted(evaluation_summary.items()):
        print(f"    - {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

