"""QuantLib worker entry point for the governed research container."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (
    GovernedBondSpec,
    GovernedMarketSnapshot,
    GovernedOptionSpec,
    QuantLibBackend,
    StubQuantLibBackend,
    run_quantlib_workflow,
)


def _load_snapshot(dataset_path: str) -> GovernedMarketSnapshot:
    raw = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    return GovernedMarketSnapshot(
        dataset_id=raw["dataset_id"],
        source_dataset_refs=tuple(raw["source_dataset_refs"]),
        valuation_date=raw["valuation_date"],
        option_specs=tuple(GovernedOptionSpec(**spec) for spec in raw["option_specs"]),
        bond_specs=tuple(GovernedBondSpec(**spec) for spec in raw["bond_specs"]),
        metadata=raw.get("metadata", {}),
    )


def main() -> int:
    dataset_path = os.environ.get("QUANTLIB_DATASET_PATH")
    if not dataset_path:
        print(
            "QUANTLIB_DATASET_PATH not set; using sample dataset for governed smoke mode",
            file=sys.stderr,
        )
        dataset_path = str(SERVICE_DIR / "examples" / "pricing_dataset_sample.json")

    snapshot = _load_snapshot(dataset_path)
    analysis_paths_env = os.environ.get("QUANTLIB_ANALYSIS_PATHS", "")
    analysis_paths = [path.strip() for path in analysis_paths_env.split(",") if path.strip()] or None

    use_real = os.environ.get("PANTHEON_QUANTLIB_BACKEND", "stub").lower() == "real"
    backend = QuantLibBackend() if use_real else StubQuantLibBackend()
    if use_real:
        print("PANTHEON_QUANTLIB_BACKEND=real: using QuantLibBackend", file=sys.stderr)

    result = run_quantlib_workflow(
        snapshot,
        analysis_paths=analysis_paths,
        backend=backend,
    )
    output = {
        "artifact_id": result["artifact_id"],
        "artifact_family": result["artifact_family"],
        "artifact_state": result["registry_entry"]["artifact_state"],
        "deployment_stage": result["registry_entry"]["deployment_summary"]["current_stage"],
        "analysis_path": result["analysis_path"],
        "result_keys": sorted(result["results_summary"].keys()),
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
