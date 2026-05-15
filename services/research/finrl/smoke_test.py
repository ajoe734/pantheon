"""Governed FinRL deferred-prep smoke test."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (
    DeferredPrepGate,
    FinRLPPOBackend,
    PolicyTrainingConfig,
    StubFinRLBackend,
    run_finrl_workflow,
)
from config import selected_backend


def load_sample_dataset() -> dict:
    sample_path = SERVICE_DIR / "examples" / "policy_input_sample.json"
    return json.loads(sample_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the FinRL deferred-prep smoke test.")
    parser.add_argument(
        "--enable-deferred-prep",
        action="store_true",
        help="Acknowledge the explicit prep-only gate for this non-activating smoke path.",
    )
    parser.add_argument(
        "--backend",
        choices=("stub", "finrl"),
        default=None,
        help="Select stub (default) or FinRL import-path scaffold.",
    )
    args = parser.parse_args(argv)

    try:
        DeferredPrepGate.require_cli_flag(args.enable_deferred_prep)
    except EnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    backend_name = args.backend or selected_backend()
    backend = StubFinRLBackend() if backend_name == "stub" else FinRLPPOBackend()
    result = run_finrl_workflow(
        load_sample_dataset(),
        backend=backend,
        config=PolicyTrainingConfig(version="1.0.0", requested_by="Codex2"),
    )

    ds = result.prepared_dataset
    reg = result.registry_entry
    bundle = result.artifact_bundle
    packet = result.candidate_packet
    metrics = result.training_result.metrics

    print("FinRL deferred-prep smoke test complete")
    print(f"  backend:                {result.training_result.backend}")
    print(f"  decision_focus:         {ds.decision_focus}")
    print(f"  instruments:            {len(ds.instruments)}")
    print(f"  num_steps:              {ds.num_steps}")
    print(f"  observation_dim:        {ds.observation_dim}")
    print(f"  registry_id:            {reg['registry_id']}")
    print(f"  artifact_state:         {reg['artifact_state']}")
    print(f"  deployment_stage:       {reg['deployment_summary']['current_stage']}")
    print(f"  candidate_next_state:   {packet['requested_artifact_state']}")
    print(f"  gate_state:             {packet['gate_state']}")
    print(f"  artifact_family:        {bundle['artifact_family']}")
    print(f"  lean_consumption:       {bundle['governance']['lean_consumption']}")
    print("  metrics:")
    for key, value in sorted(metrics.items()):
        print(f"    - {key}: {value}")

    assert reg["artifact_state"] == "draft", "artifact must start as draft"
    assert reg["deployment_summary"]["current_stage"] == "none", "deployment stage must be none"
    assert packet["requested_artifact_state"] == "candidate", "candidate packet must target candidate"
    assert packet["gate_state"] == "closed", "RL gate must remain closed"
    assert bundle["governance"]["direct_live_influence"] is False, "must be offline-only"
    assert reg["checksum"].startswith("sha256:"), "checksum must be sha256-prefixed"

    print("  assertions: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
