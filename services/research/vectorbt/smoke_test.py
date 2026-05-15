"""Governed vectorbt backtesting smoke test — OSS-IMPL-003."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import BacktestConfig, StubVectorbtBackend, VectorbtBackend, run_vectorbt_workflow

_NUM_INSTRUMENTS = 2
_NUM_BARS = 35


def _build_stub_dataset() -> dict:
    """Minimal OHLCV dataset: 2 instruments, 35 bars each."""
    records: list[dict] = []
    for inst, base in [("ALPHA", 100.0), ("BETA", 50.0)]:
        price = base
        for i in range(_NUM_BARS):
            price = price * (1.0 + (0.005 if i % 3 != 0 else -0.008))
            records.append(
                {
                    "instrument": inst,
                    "date": f"2024-{(i // 30 + 1):02d}-{(i % 30 + 1):02d}",
                    "open": round(price * 0.998, 4),
                    "high": round(price * 1.005, 4),
                    "low": round(price * 0.995, 4),
                    "close": round(price, 4),
                    "volume": 1_000_000 + i * 10_000,
                }
            )
    return {
        "dataset_id": "dataset:smoke-test-001",
        "strategy_id": "smoke-strategy",
        "source_dataset_refs": ["dataset:smoke-source-001"],
        "data_frequency": "daily",
        "records": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the OSS-IMPL-003 vectorbt smoke test.")
    parser.add_argument(
        "--backend",
        choices=("stub", "real"),
        default="stub",
        help="Select stub (default) or real vectorbt backend.",
    )
    args = parser.parse_args(argv)

    dataset = _build_stub_dataset()
    backend = StubVectorbtBackend() if args.backend == "stub" else VectorbtBackend()

    result = run_vectorbt_workflow(
        dataset,
        backend=backend,
        config=BacktestConfig(version="1.0.0", requested_by="Claude"),
    )

    entry = result.registry_entry
    bundle = result.artifact_bundle
    ds = result.prepared_dataset
    agg = result.backtest_result.aggregate_metrics

    print("OSS-IMPL-003 vectorbt smoke test complete")
    print(f"  backend:                {result.backtest_result.backend}")
    print(f"  instruments:            {ds.num_instruments}")
    print(f"  total_bars:             {ds.total_bars}")
    print(f"  registry_id:            {entry['registry_id']}")
    print(f"  artifact_type:          {entry['artifact_type']}")
    print(f"  artifact_state:         {entry['artifact_state']}")
    print(f"  deployment_stage:       {entry['deployment_summary']['current_stage']}")
    print(f"  artifact_family:        {bundle['artifact_family']}")
    print(f"  framework:              {bundle['framework']}")
    print(f"  direct_live_influence:  {bundle['governance']['direct_live_influence']}")
    print(f"  lean_consumption:       {bundle['governance']['lean_consumption']}")
    print(f"  checksum:               {entry['checksum'][:32]}...")
    print("  aggregate_metrics:")
    for k, v in sorted(agg.items()):
        print(f"    - {k}: {v}")

    # --- Assertions: artifact_bundle shape ---
    assert bundle["artifact_family"] == "vectorbt_backtest", (
        f"artifact_family must be vectorbt_backtest; got {bundle['artifact_family']}"
    )
    assert bundle["framework"] == "vectorbt", (
        f"framework must be vectorbt; got {bundle['framework']}"
    )
    assert bundle["governance"]["direct_live_influence"] is False, (
        "direct_live_influence must be False"
    )
    assert bundle["governance"]["lean_consumption"] == "scoring_only_not_direct_action", (
        "lean_consumption must be scoring_only_not_direct_action"
    )

    # --- Assertions: registry_entry shape ---
    assert entry["artifact_type"] == "backtest_result", (
        f"artifact_type must be backtest_result; got {entry['artifact_type']}"
    )
    assert entry["artifact_state"] == "draft", (
        f"artifact_state must start as draft; got {entry['artifact_state']}"
    )
    assert entry["deployment_summary"]["current_stage"] == "none", (
        f"deployment stage must be none; got {entry['deployment_summary']['current_stage']}"
    )
    assert entry["checksum"].startswith("sha256:"), "checksum must be sha256-prefixed"

    # --- Assertions: data shape ---
    assert ds.num_instruments >= _NUM_INSTRUMENTS, (
        f"need at least {_NUM_INSTRUMENTS} instruments; got {ds.num_instruments}"
    )
    assert all(b >= _NUM_BARS for b in ds.bars_per_instrument.values()), (
        f"each instrument must have at least {_NUM_BARS} bars"
    )

    print("  assertions: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
