"""vectorbt worker entry point for the governed research container."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import BacktestConfig, StubVectorbtBackend, VectorbtBackend, run_vectorbt_workflow


def main() -> int:
    dataset_path = os.environ.get("VECTORBT_DATASET_PATH")
    if not dataset_path:
        print(
            "VECTORBT_DATASET_PATH not set; using sample dataset for governed smoke mode",
            file=sys.stderr,
        )
        dataset_path = str(SERVICE_DIR / "examples" / "strategy_dataset_sample.json")

    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    strategy_params = {
        "short_window": int(os.environ.get("VECTORBT_SHORT_WINDOW", "5")),
        "long_window": int(os.environ.get("VECTORBT_LONG_WINDOW", "20")),
    }
    config = BacktestConfig(
        version=os.environ.get("VECTORBT_ARTIFACT_VERSION", "1.0.0"),
        requested_by=os.environ.get("VECTORBT_REQUESTED_BY", "worker"),
        strategy_params=strategy_params,
        init_cash=float(os.environ.get("VECTORBT_INIT_CASH", "100000.0")),
        fees=float(os.environ.get("VECTORBT_FEES", "0.001")),
    )

    use_real = os.environ.get("PANTHEON_VECTORBT_BACKEND", "stub").lower() == "real"
    backend = VectorbtBackend() if use_real else StubVectorbtBackend()
    if use_real:
        print("PANTHEON_VECTORBT_BACKEND=real: using VectorbtBackend", file=sys.stderr)

    result = run_vectorbt_workflow(dataset, backend=backend, config=config)
    output = {
        "registry_id": result.registry_entry["registry_id"],
        "artifact_state": result.registry_entry["artifact_state"],
        "deployment_stage": result.registry_entry["deployment_summary"]["current_stage"],
        "storage_path": result.registry_entry["storage_ref"]["path"],
        "checksum": result.registry_entry["checksum"],
        "aggregate_metrics": result.backtest_result.aggregate_metrics,
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
