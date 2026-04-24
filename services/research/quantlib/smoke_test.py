"""Governed QuantLib smoke test for the Pantheon research baseline."""

from __future__ import annotations

import argparse
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


def _build_snapshot() -> GovernedMarketSnapshot:
    return GovernedMarketSnapshot(
        dataset_id="dataset:quantlib-smoke-001",
        source_dataset_refs=("dataset:rates-options-smoke-001",),
        valuation_date="2026-04-17",
        option_specs=(
            GovernedOptionSpec(
                option_id="opt-eur-call-001",
                style="european",
                option_type="call",
                spot=102.0,
                strike=100.0,
                volatility=0.22,
                risk_free_rate=0.03,
                dividend_yield=0.01,
                maturity_days=90,
                quantity=10,
            ),
        ),
        bond_specs=(
            GovernedBondSpec(
                instrument_id="bond-ust-2y-001",
                face_value=1000.0,
                coupon_rate=0.04,
                market_rate=0.035,
                maturity_years=2,
                payment_frequency=2,
            ),
        ),
        metadata={"source": "smoke_test", "governed": True},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the governed QuantLib smoke test.")
    parser.add_argument(
        "--backend",
        choices=("stub", "real"),
        default="stub",
        help="Select stub (default) or real QuantLib backend.",
    )
    args = parser.parse_args(argv)

    backend = StubQuantLibBackend() if args.backend == "stub" else QuantLibBackend()
    bundle = run_quantlib_workflow(_build_snapshot(), backend=backend)

    reg = bundle["registry_entry"]
    gov = bundle["governance"]
    results = bundle["results_summary"]

    print("QuantLib smoke test complete")
    print(f"  artifact_id:        {bundle['artifact_id']}")
    print(f"  artifact_family:    {bundle['artifact_family']}")
    print(f"  framework:          {bundle['framework']}")
    print(f"  artifact_state:     {reg['artifact_state']}")
    print(f"  deployment_stage:   {reg['deployment_summary']['current_stage']}")
    print(f"  direct_influence:   {gov['direct_live_influence']}")
    print(f"  lean_consumption:   {gov['lean_consumption']}")
    print(f"  option_count:       {len(results['options_pricing'])}")
    print(f"  bond_count:         {len(results['fixed_income'])}")

    assert bundle["artifact_family"] == "pricing_report"
    assert bundle["framework"] == "quantlib"
    assert gov["direct_live_influence"] is False
    assert gov["lean_consumption"] == "research_only_not_direct_action"
    assert reg["artifact_type"] == "research_report"
    assert reg["artifact_state"] == "draft"
    assert reg["deployment_summary"]["current_stage"] == "none"

    print("  assertions: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
