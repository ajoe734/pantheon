"""statsmodels worker entry point for the governed research container."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import GovernedDataset, StatsmodelsBackend, run_statsmodels_workflow


def _load_dataset(dataset_path: str) -> GovernedDataset:
    raw = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    return GovernedDataset(
        price_series=raw["price_series"],
        factor_series=raw["factor_series"],
        metadata=raw.get("metadata", {}),
    )


def main() -> int:
    dataset_path = os.environ.get("STATSMODELS_DATASET_PATH")
    if not dataset_path:
        # Fail closed: no sample or placeholder dataset stands in for governed data.
        print("STATSMODELS_DATASET_PATH not set; refusing to run without governed data", file=sys.stderr)
        return 2

    dataset = _load_dataset(dataset_path)
    analysis_paths_env = os.environ.get("STATSMODELS_ANALYSIS_PATHS", "")
    analysis_paths = [p.strip() for p in analysis_paths_env.split(",") if p.strip()] or None

    if os.environ.get("PANTHEON_STATSMODELS_BACKEND", "").lower() != "real":
        print("PANTHEON_STATSMODELS_BACKEND=real is required; the stub backend is test-only", file=sys.stderr)
        return 2
    backend = StatsmodelsBackend()

    result = run_statsmodels_workflow(
        dataset,
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
