"""statsmodels worker entry point for the governed research container."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import GovernedDataset, StubStatsmodelsBackend, StatsmodelsBackend, run_statsmodels_workflow


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
        print(
            "STATSMODELS_DATASET_PATH not set; using sample dataset for governed smoke mode",
            file=sys.stderr,
        )
        dataset_path = str(SERVICE_DIR / "examples" / "regime_dataset_sample.json")

    dataset = _load_dataset(dataset_path)
    analysis_paths_env = os.environ.get("STATSMODELS_ANALYSIS_PATHS", "")
    analysis_paths = [p.strip() for p in analysis_paths_env.split(",") if p.strip()] or None

    use_real = os.environ.get("PANTHEON_STATSMODELS_BACKEND", "stub").lower() == "real"
    backend = StatsmodelsBackend() if use_real else StubStatsmodelsBackend()
    if use_real:
        print("PANTHEON_STATSMODELS_BACKEND=real: using StatsmodelsBackend", file=sys.stderr)

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
