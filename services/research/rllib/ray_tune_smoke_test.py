"""Governed Ray Tune deferred-prep smoke test."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (
    RLlibTrainingConfig,
    RayTuneDeferredPrepGate,
    RayTuneImportBackend,
    RayTuneSearchConfig,
    StubRayTuneBackend,
    run_ray_tune_workflow,
)
from config import selected_tune_backend


def load_sample_dataset() -> dict:
    sample_path = SERVICE_DIR / "examples" / "train_eval_input_sample.json"
    return json.loads(sample_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Ray Tune deferred-prep smoke test.")
    parser.add_argument(
        "--enable-deferred-prep",
        action="store_true",
        help="Acknowledge the explicit prep-only gate for this non-activating smoke path.",
    )
    parser.add_argument(
        "--backend",
        choices=("stub", "tune"),
        default=None,
        help="Select stub (default) or Ray Tune import-path scaffold.",
    )
    args = parser.parse_args(argv)

    try:
        RayTuneDeferredPrepGate.require_cli_flag(args.enable_deferred_prep)
    except EnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    backend_name = args.backend or selected_tune_backend()
    backend = StubRayTuneBackend() if backend_name == "stub" else RayTuneImportBackend()
    result = run_ray_tune_workflow(
        load_sample_dataset(),
        backend=backend,
        training_config=RLlibTrainingConfig(version="1.0.0", requested_by="Codex"),
        search_config=RayTuneSearchConfig(version="1.0.0", requested_by="Codex"),
    )

    req = result.prepared_search
    reg = result.registry_entry
    bundle = result.artifact_bundle
    packet = result.candidate_packet
    best_trial = result.search_result.best_trial

    print("Ray Tune deferred-prep smoke test complete")
    print(f"  backend:                {result.search_result.backend}")
    print(f"  search_strategy:        {req.search_strategy}")
    print(f"  objective_metric:       {req.objective_metric}")
    print(f"  search_dimensions:      {len(req.search_space_schema['parameters'])}")
    print(f"  total_trials:           {len(result.search_result.trial_results)}")
    print(f"  best_trial:             {best_trial.trial_id}")
    print(f"  best_trial_score:       {best_trial.metrics[req.objective_metric]}")
    print(f"  registry_id:            {reg['registry_id']}")
    print(f"  artifact_type:          {reg['artifact_type']}")
    print(f"  artifact_state:         {reg['artifact_state']}")
    print(f"  deployment_stage:       {reg['deployment_summary']['current_stage']}")
    print(f"  candidate_next_state:   {packet['requested_artifact_state']}")
    print(f"  gate_state:             {packet['gate_state']}")
    print(f"  output_artifacts:       {len(bundle['output_artifacts'])}")
    print("  summary:")
    for key, value in sorted(result.search_result.summary.items()):
        print(f"    - {key}: {value}")

    assert reg["artifact_type"] == "optimizer_result"
    assert reg["artifact_state"] == "draft", "artifact must start as draft"
    assert reg["deployment_summary"]["current_stage"] == "none", "deployment stage must be none"
    assert packet["requested_artifact_state"] == "candidate", "candidate packet must target candidate"
    assert packet["gate_state"] == "closed", "RL gate must remain closed"
    assert bundle["governance"]["direct_live_influence"] is False, "must be offline-only"
    assert bundle["search_space_schema"]["strategy"] == "pbt", "default search strategy must be pbt"
    assert len(bundle["search_space_schema"]["parameters"]) == 6
    assert len(bundle["output_artifacts"]) == req.top_k

    print("  assertions: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
