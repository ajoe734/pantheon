"""CPU-only RLlib PPO CartPole smoke test for OSS-RLLIB-001."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (  # noqa: E402
    DeferredPrepGate,
    MAX_NUM_ITERS,
    RLlibPPOBackend,
    RLlibTrainingConfig,
    StubRLlibBackend,
    run_rllib_workflow,
    train_ppo,
)
from config import selected_backend  # noqa: E402


def load_sample_dataset() -> dict:
    sample_path = SERVICE_DIR / "examples" / "train_eval_input_sample.json"
    return json.loads(sample_path.read_text(encoding="utf-8"))


def run_cartpole_smoke(args: argparse.Namespace) -> int:
    result = train_ppo(
        args.env_id,
        args.num_iters,
        output_dir=args.output_dir,
        seed=args.seed,
        eval_episodes=args.eval_episodes,
        require_rllib=args.require_rllib,
    )
    metrics = result["metrics"]
    registry_entry = result["registry_entry"]

    print("RLlib PPO CartPole smoke test complete")
    print(f"  backend:              {result['backend']}")
    print(f"  backend_kind:         {result['backend_kind']}")
    print(f"  env_id:               {result['env_id']}")
    print(f"  iterations:           {result['num_iters']}")
    print(f"  random_baseline:      {metrics['random_baseline_mean_reward']}")
    print(f"  mean_reward:          {metrics['mean_reward']}")
    print(f"  reward_improvement:   {metrics['reward_improvement']}")
    print(f"  model_artifact_ref:   {result['model_artifact_ref']}")
    print(f"  model_artifact_path:  {result['model_artifact_path']}")
    if "backend_failure" in result:
        failure = result["backend_failure"]
        print(f"  upstream_attempt:     {failure['status']} ({failure['error_type']})")

    assert result["cpu_only"] is True
    assert result["num_iters"] <= MAX_NUM_ITERS
    assert result["artifact_type"] == "model_artifact"
    assert result["model_artifact_ref"]
    assert Path(result["model_artifact_path"]).exists()
    assert registry_entry["artifact_type"] == "model_artifact"
    assert registry_entry["deployment_summary"]["current_stage"] == "none"
    assert metrics["mean_reward"] > metrics["random_baseline_mean_reward"]
    assert metrics["improved_vs_random_baseline"] is True

    print("  assertions: OK")
    return 0


def run_deferred_prep_smoke(args: argparse.Namespace) -> int:
    try:
        DeferredPrepGate.require_cli_flag(True)
    except EnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    backend_name = args.deferred_backend or selected_backend()
    backend = StubRLlibBackend() if backend_name == "stub" else RLlibPPOBackend()
    result = run_rllib_workflow(
        load_sample_dataset(),
        backend=backend,
        config=RLlibTrainingConfig(version="1.0.0", requested_by="Codex"),
    )

    ds = result.prepared_dataset
    reg = result.registry_entry
    bundle = result.artifact_bundle
    packet = result.candidate_packet
    metrics = result.train_eval_result.metrics
    schema = result.train_eval_result.train_eval_schema

    print("RLlib deferred-prep smoke test complete")
    print(f"  backend:                {result.train_eval_result.backend}")
    print(f"  decision_focus:         {ds.decision_focus}")
    print(f"  instruments:            {len(ds.instruments)}")
    print(f"  train_steps:            {ds.num_train_steps}")
    print(f"  eval_steps:             {ds.num_eval_steps}")
    print(f"  observation_shape:      {tuple(schema['observation_shape'])}")
    print(f"  action_vector_length:   {schema['action_vector_length']}")
    print(f"  joint_actions:          {schema['joint_action_cardinality']}")
    print(f"  search_strategy:        {schema['search_space']['strategy']}")
    print(f"  registry_id:            {reg['registry_id']}")
    print(f"  artifact_state:         {reg['artifact_state']}")
    print(f"  deployment_stage:       {reg['deployment_summary']['current_stage']}")
    print(f"  candidate_next_state:   {packet['requested_artifact_state']}")
    print(f"  gate_state:             {packet['gate_state']}")
    print(f"  artifact_family:        {bundle['artifact_family']}")
    print(f"  optimizer_method:       {bundle['registry_hints']['optimizer_method']}")
    print("  metrics:")
    for key, value in sorted(metrics.items()):
        print(f"    - {key}: {value}")

    assert reg["artifact_state"] == "draft", "artifact must start as draft"
    assert reg["deployment_summary"]["current_stage"] == "none", "deployment stage must be none"
    assert packet["requested_artifact_state"] == "candidate", "candidate packet must target candidate"
    assert packet["gate_state"] == "closed", "RL gate must remain closed"
    assert bundle["governance"]["direct_live_influence"] is False, "must be offline-only"
    assert schema["search_space"]["strategy"] == "pbt", "default search strategy must be pbt"
    assert schema["split_steps"]["train"] > 0 and schema["split_steps"]["eval"] > 0

    print("  assertions: OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the RLlib PPO CartPole smoke test.")
    parser.add_argument("--env-id", default="CartPole-v1")
    parser.add_argument("--num-iters", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-episodes", type=int, default=5)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/pantheon/research/rllib/cartpole-smoke"),
    )
    parser.add_argument(
        "--require-rllib",
        action="store_true",
        help="Fail if the upstream Ray/RLlib PPO mini-loop cannot run.",
    )
    parser.add_argument(
        "--enable-deferred-prep",
        action="store_true",
        help="Run the older governed deferred-prep smoke path instead of CartPole PPO.",
    )
    parser.add_argument(
        "--deferred-backend",
        choices=("stub", "rllib"),
        default=None,
        help="Select stub or RLlib backend for --enable-deferred-prep mode.",
    )
    args = parser.parse_args(argv)

    if args.enable_deferred_prep:
        return run_deferred_prep_smoke(args)
    return run_cartpole_smoke(args)


if __name__ == "__main__":
    raise SystemExit(main())
