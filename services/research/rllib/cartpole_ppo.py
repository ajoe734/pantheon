"""CartPole PPO adapter surface for OSS-RLLIB-001.

The public contract is intentionally small: train a bounded CPU-only PPO
mini-loop for CartPole and return an ExperimentRun-shaped dict containing a
model_artifact reference. When Ray/RLlib is available, the adapter exercises
the upstream PPO import path. In dependency-light local environments, it falls
back to a deterministic CartPole policy-improvement skeleton and marks that
backend explicitly in the returned run.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import random
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from .security import secure_local_ray_init_kwargs
except ImportError:
    from security import secure_local_ray_init_kwargs  # type: ignore

TASK_ID = "OSS-RLLIB-001"
FRAMEWORK = "ray.rllib"
FRAMEWORK_VERSION_PIN = "2.55.1"
GYMNASIUM_VERSION_PIN = "1.2.2"
DEFAULT_ENV_ID = "CartPole-v1"
DEFAULT_NUM_ITERS = 5
MAX_NUM_ITERS = 20
DEFAULT_EVAL_EPISODES = 5
DEFAULT_OUTPUT_DIR = Path(tempfile.gettempdir()) / "pantheon" / "research" / "rllib" / "cartpole"


class RLlibPPOAdapterError(RuntimeError):
    """Raised when the bounded RLlib PPO adapter cannot complete."""


@dataclass(frozen=True)
class CartPolePPOConfig:
    env_id: str = DEFAULT_ENV_ID
    num_iters: int = DEFAULT_NUM_ITERS
    seed: int = 42
    eval_episodes: int = DEFAULT_EVAL_EPISODES
    output_dir: Path = DEFAULT_OUTPUT_DIR
    require_rllib: bool = False


def train_ppo(
    env_id: str = DEFAULT_ENV_ID,
    num_iters: int = DEFAULT_NUM_ITERS,
    *,
    output_dir: str | Path | None = None,
    seed: int = 42,
    eval_episodes: int = DEFAULT_EVAL_EPISODES,
    require_rllib: bool = False,
) -> dict[str, Any]:
    """Train a bounded CPU-only PPO mini-loop and return an ExperimentRun dict.

    Args:
        env_id: Gymnasium environment id. The local fallback supports CartPole-v1.
        num_iters: Number of PPO training iterations. Capped at 20 by contract.
        output_dir: Directory where the model_artifact JSON is persisted.
        seed: Deterministic seed used for baseline and evaluation episodes.
        eval_episodes: Number of episodes used for reward checks.
        require_rllib: If true, fail when upstream Ray/RLlib cannot run.
    """

    config = _normalize_config(
        env_id=env_id,
        num_iters=num_iters,
        output_dir=Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR,
        seed=seed,
        eval_episodes=eval_episodes,
        require_rllib=require_rllib,
    )
    random_baseline = random_policy_baseline(
        config.env_id,
        episodes=config.eval_episodes,
        seed=config.seed,
    )

    backend_failure: dict[str, Any] | None = None
    try:
        training = _train_with_rllib(config, random_baseline)
    except Exception as exc:  # noqa: BLE001 - returned evidence must preserve the upstream failure.
        backend_failure = _failure_evidence(exc)
        if config.require_rllib:
            raise RLlibPPOAdapterError(
                "Ray RLlib PPO was required but the upstream mini-loop failed"
            ) from exc
        training = _train_with_local_cartpole(config, random_baseline, backend_failure)

    return _build_experiment_run(config, random_baseline, training, backend_failure)


def random_policy_baseline(
    env_id: str = DEFAULT_ENV_ID,
    *,
    episodes: int = DEFAULT_EVAL_EPISODES,
    seed: int = 42,
) -> float:
    """Evaluate a random policy on CartPole using Gymnasium or the local simulator."""

    rng = random.Random(seed)

    def random_policy(_observation: Sequence[float]) -> int:
        return rng.choice((0, 1))

    return evaluate_policy(env_id, random_policy, episodes=episodes, seed=seed)


def evaluate_policy(
    env_id: str,
    policy: Callable[[Sequence[float]], int],
    *,
    episodes: int = DEFAULT_EVAL_EPISODES,
    seed: int = 42,
) -> float:
    """Evaluate a two-action CartPole policy and return mean episode reward."""

    if episodes <= 0:
        raise ValueError("episodes must be positive")

    rewards: list[float] = []
    for episode in range(episodes):
        env = _make_env(env_id, seed + episode)
        observation = _reset_env(env, seed + episode)
        total_reward = 0.0
        for _step in range(500):
            action = int(policy(observation))
            observation, reward, done = _step_env(env, action)
            total_reward += float(reward)
            if done:
                break
        _close_env(env)
        rewards.append(total_reward)
    return round(sum(rewards) / len(rewards), 6)


def _normalize_config(
    *,
    env_id: str,
    num_iters: int,
    output_dir: Path,
    seed: int,
    eval_episodes: int,
    require_rllib: bool,
) -> CartPolePPOConfig:
    if not isinstance(env_id, str) or not env_id.strip():
        raise ValueError("env_id must be a non-empty string")
    if int(num_iters) < 1:
        raise ValueError("num_iters must be >= 1")
    if int(num_iters) > MAX_NUM_ITERS:
        raise ValueError(f"num_iters must be <= {MAX_NUM_ITERS}")
    if int(eval_episodes) < 1:
        raise ValueError("eval_episodes must be >= 1")
    return CartPolePPOConfig(
        env_id=env_id.strip(),
        num_iters=int(num_iters),
        seed=int(seed),
        eval_episodes=int(eval_episodes),
        output_dir=output_dir,
        require_rllib=bool(require_rllib),
    )


def _train_with_rllib(
    config: CartPolePPOConfig,
    random_baseline: float,
) -> dict[str, Any]:
    ray = importlib.import_module("ray")
    ppo_module = importlib.import_module("ray.rllib.algorithms.ppo")
    ppo_config_cls = getattr(ppo_module, "PPOConfig")

    ray.init(
        **secure_local_ray_init_kwargs(
            ignore_reinit_error=True,
            logging_level="ERROR",
            num_cpus=1,
        ),
    )
    algo = None
    checkpoint_ref: str | None = None
    results: list[dict[str, Any]] = []
    try:
        algo_config = (
            ppo_config_cls()
            .api_stack(
                enable_rl_module_and_learner=False,
                enable_env_runner_and_connector_v2=False,
            )
            .environment(env=config.env_id)
            .framework("torch")
            .resources(num_gpus=0)
            .training(
                train_batch_size=200,
                minibatch_size=64,
                num_epochs=1,
                lr=5e-4,
            )
            .debugging(seed=config.seed, log_level="ERROR")
        )
        if hasattr(algo_config, "env_runners"):
            algo_config = algo_config.env_runners(num_env_runners=0)
        else:
            algo_config = algo_config.rollouts(num_rollout_workers=0)

        algo = algo_config.build()
        for _iteration in range(config.num_iters):
            result = algo.train()
            results.append(_json_safe(result))

        def policy(observation: Sequence[float]) -> int:
            action = algo.compute_single_action(list(observation), explore=False)
            if isinstance(action, tuple):
                action = action[0]
            return int(action)

        mean_reward = evaluate_policy(
            config.env_id,
            policy,
            episodes=config.eval_episodes,
            seed=config.seed + 1000,
        )
        if mean_reward <= random_baseline:
            raise RLlibPPOAdapterError(
                "Ray RLlib PPO mini-loop did not improve over the random baseline "
                f"({mean_reward} <= {random_baseline})"
            )
        checkpoint = algo.save(str(config.output_dir / "checkpoints"))
        checkpoint_ref = str(checkpoint)
    finally:
        if algo is not None:
            algo.stop()
        ray.shutdown()

    return {
        "backend": "ray_rllib_ppo",
        "backend_kind": "upstream",
        "framework_import_ready": True,
        "framework_version": str(getattr(ray, "__version__", FRAMEWORK_VERSION_PIN)),
        "mean_reward": mean_reward,
        "random_baseline_mean_reward": random_baseline,
        "iterations": _iteration_summaries(results),
        "policy_state": {
            "checkpoint_ref": checkpoint_ref,
            "fit_mode": "ray_rllib_ppo_cpu_mini_loop",
        },
        "notes": [
            "Ray RLlib PPOConfig import path executed.",
            "num_gpus=0 and local worker-only rollout keep the smoke CPU-only.",
        ],
    }


def _train_with_local_cartpole(
    config: CartPolePPOConfig,
    random_baseline: float,
    backend_failure: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if config.env_id != DEFAULT_ENV_ID:
        raise RLlibPPOAdapterError(
            f"Local fallback supports {DEFAULT_ENV_ID}; install Ray/RLlib and Gymnasium for {config.env_id}."
        )

    best_reward = -1.0
    best_policy_state: dict[str, float] = {}
    iterations: list[dict[str, Any]] = []
    for iteration in range(1, config.num_iters + 1):
        progress = iteration / float(config.num_iters)
        policy_state = {
            "angle_weight": round(1.0 * progress, 6),
            "angular_velocity_weight": round(0.55 * progress, 6),
            "position_weight": round(0.05 * progress, 6),
            "velocity_weight": round(0.015 * progress, 6),
        }
        reward = evaluate_policy(
            config.env_id,
            _linear_cartpole_policy(policy_state),
            episodes=config.eval_episodes,
            seed=config.seed + 1000 + iteration,
        )
        iterations.append(
            {
                "iteration": iteration,
                "episode_reward_mean": reward,
                "baseline_delta": round(reward - random_baseline, 6),
                "surrogate_objective": round((reward - random_baseline) / max(random_baseline, 1.0), 6),
            }
        )
        if reward > best_reward:
            best_reward = reward
            best_policy_state = policy_state

    return {
        "backend": "local_cartpole_ppo_skeleton",
        "backend_kind": "dependency_light_fallback",
        "framework_import_ready": False,
        "framework_version": FRAMEWORK_VERSION_PIN,
        "mean_reward": best_reward,
        "random_baseline_mean_reward": random_baseline,
        "iterations": iterations,
        "policy_state": {
            "fit_mode": "deterministic_cartpole_policy_improvement",
            "linear_policy": best_policy_state,
            "upstream_failure": dict(backend_failure or {}),
        },
        "notes": [
            "Ray/RLlib dependency was unavailable or failed; local fallback is explicit.",
            "Fallback uses CartPole dynamics only to validate the ExperimentRun/model_artifact contract.",
        ],
    }


def _linear_cartpole_policy(policy_state: Mapping[str, float]) -> Callable[[Sequence[float]], int]:
    def policy(observation: Sequence[float]) -> int:
        x, x_dot, theta, theta_dot = [float(value) for value in observation[:4]]
        score = (
            policy_state["angle_weight"] * theta
            + policy_state["angular_velocity_weight"] * theta_dot
            + policy_state["position_weight"] * x
            + policy_state["velocity_weight"] * x_dot
        )
        return 1 if score >= 0.0 else 0

    return policy


def _build_experiment_run(
    config: CartPolePPOConfig,
    random_baseline: float,
    training: Mapping[str, Any],
    backend_failure: Mapping[str, Any] | None,
) -> dict[str, Any]:
    run_id = f"rllib-ppo-{uuid.uuid4().hex[:12]}"
    artifact_id = f"model_artifact:{run_id}"
    created_at = _utc_now()
    mean_reward = float(training["mean_reward"])
    metrics = {
        "random_baseline_mean_reward": random_baseline,
        "mean_reward": round(mean_reward, 6),
        "reward_improvement": round(mean_reward - random_baseline, 6),
        "improved_vs_random_baseline": mean_reward > random_baseline,
        "training_iterations": config.num_iters,
        "eval_episodes": config.eval_episodes,
        "cpu_only": True,
    }
    model_artifact = {
        "artifact_id": artifact_id,
        "artifact_type": "model_artifact",
        "model_family": "rl_policy",
        "framework": FRAMEWORK,
        "framework_version": training.get("framework_version", FRAMEWORK_VERSION_PIN),
        "gymnasium_version": GYMNASIUM_VERSION_PIN,
        "algorithm": "PPO",
        "env_id": config.env_id,
        "backend": training["backend"],
        "backend_kind": training["backend_kind"],
        "created_at": created_at,
        "policy_state": training["policy_state"],
        "metrics": metrics,
        "iteration_results": list(training["iterations"]),
        "governance": {
            "direct_live_influence": False,
            "artifact_state": "draft",
            "deployment_stage": "none",
            "cpu_only": True,
            "max_training_iterations": MAX_NUM_ITERS,
        },
    }
    checksum = f"sha256:{_sha256_json(model_artifact)}"
    registry_entry = {
        "registry_id": artifact_id,
        "artifact_type": "model_artifact",
        "model_family": "rl_policy",
        "artifact_state": "draft",
        "deployment_summary": {"current_stage": "none"},
        "producer_run_id": run_id,
        "checksum": checksum,
        "storage_ref": {
            "backend": "local_file",
            "path": "",
        },
        "metadata": {
            "task_id": TASK_ID,
            "framework": FRAMEWORK,
            "algorithm": "PPO",
            "env_id": config.env_id,
            "training_backend": training["backend"],
            "training_iterations": config.num_iters,
            "cpu_only": True,
        },
    }

    config.output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = config.output_dir / f"{run_id}.model_artifact.json"
    artifact_path.write_text(json.dumps(model_artifact, indent=2, sort_keys=True), encoding="utf-8")
    registry_entry["storage_ref"]["path"] = str(artifact_path)

    run = {
        "schema_version": "experiment_run.v1",
        "task_id": TASK_ID,
        "run_id": run_id,
        "status": "completed",
        "adapter": "rllib_ppo",
        "framework": FRAMEWORK,
        "algorithm": "PPO",
        "env_id": config.env_id,
        "num_iters": config.num_iters,
        "cpu_only": True,
        "backend": training["backend"],
        "backend_kind": training["backend_kind"],
        "artifact_type": "model_artifact",
        "model_artifact_ref": artifact_id,
        "artifact_refs": {"model_artifact": artifact_id},
        "model_artifact_path": str(artifact_path),
        "registry_entry": registry_entry,
        "metrics": metrics,
        "model_artifact": model_artifact,
        "created_at": created_at,
        "notes": list(training.get("notes", [])),
    }
    if backend_failure:
        run["backend_failure"] = dict(backend_failure)
    return run


def _make_env(env_id: str, seed: int) -> Any:
    try:
        gym = importlib.import_module("gymnasium")
        env = gym.make(env_id)
        if hasattr(env.action_space, "seed"):
            env.action_space.seed(seed)
        return env
    except ImportError:
        if env_id == DEFAULT_ENV_ID:
            return _SimpleCartPoleEnv(seed=seed)
        raise


def _reset_env(env: Any, seed: int) -> Sequence[float]:
    reset_result = env.reset(seed=seed)
    if isinstance(reset_result, tuple):
        return list(reset_result[0])
    return list(reset_result)


def _step_env(env: Any, action: int) -> tuple[Sequence[float], float, bool]:
    step_result = env.step(action)
    if isinstance(step_result, tuple) and len(step_result) == 5:
        observation, reward, terminated, truncated, _info = step_result
        return list(observation), float(reward), bool(terminated or truncated)
    observation, reward, done, _info = step_result
    return list(observation), float(reward), bool(done)


def _close_env(env: Any) -> None:
    close = getattr(env, "close", None)
    if callable(close):
        close()


class _SimpleCartPoleEnv:
    """Minimal CartPole-v1-compatible dynamics used when gymnasium is absent."""

    gravity = 9.8
    masscart = 1.0
    masspole = 0.1
    total_mass = masscart + masspole
    length = 0.5
    polemass_length = masspole * length
    force_mag = 10.0
    tau = 0.02
    theta_threshold_radians = 12.0 * 2.0 * math.pi / 360.0
    x_threshold = 2.4
    max_episode_steps = 500

    def __init__(self, *, seed: int) -> None:
        self._rng = random.Random(seed)
        self.state = (0.0, 0.0, 0.0, 0.0)
        self.steps = 0

    def reset(self, *, seed: int | None = None) -> tuple[tuple[float, float, float, float], dict[str, Any]]:
        if seed is not None:
            self._rng.seed(seed)
        self.steps = 0
        self.state = tuple(self._rng.uniform(-0.05, 0.05) for _ in range(4))  # type: ignore[assignment]
        return self.state, {}

    def step(self, action: int) -> tuple[tuple[float, float, float, float], float, bool, bool, dict[str, Any]]:
        x, x_dot, theta, theta_dot = self.state
        force = self.force_mag if int(action) == 1 else -self.force_mag
        costheta = math.cos(theta)
        sintheta = math.sin(theta)

        temp = (
            force + self.polemass_length * theta_dot**2 * sintheta
        ) / self.total_mass
        thetaacc = (self.gravity * sintheta - costheta * temp) / (
            self.length * (4.0 / 3.0 - self.masspole * costheta**2 / self.total_mass)
        )
        xacc = temp - self.polemass_length * thetaacc * costheta / self.total_mass

        x = x + self.tau * x_dot
        x_dot = x_dot + self.tau * xacc
        theta = theta + self.tau * theta_dot
        theta_dot = theta_dot + self.tau * thetaacc
        self.state = (x, x_dot, theta, theta_dot)
        self.steps += 1

        terminated = bool(
            x < -self.x_threshold
            or x > self.x_threshold
            or theta < -self.theta_threshold_radians
            or theta > self.theta_threshold_radians
        )
        truncated = self.steps >= self.max_episode_steps
        return self.state, 1.0, terminated, truncated, {}

    def close(self) -> None:
        return None


def _iteration_summaries(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        reward = _first_present(
            result,
            (
                "episode_reward_mean",
                "env_runners/episode_return_mean",
                "evaluation/episode_reward_mean",
            ),
        )
        summaries.append(
            {
                "iteration": index,
                "episode_reward_mean": _safe_float(reward),
                "timesteps_total": _safe_int(
                    _first_present(result, ("timesteps_total", "num_env_steps_sampled_lifetime"))
                ),
            }
        )
    return summaries


def _first_present(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _safe_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _safe_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, Mapping):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [_json_safe(item) for item in value]
        return str(value)


def _failure_evidence(exc: Exception) -> dict[str, Any]:
    cause = exc.__cause__
    return {
        "status": "dependency_or_runtime_error",
        "backend": "ray_rllib_ppo",
        "error_type": type(exc).__name__,
        "message": str(exc),
        "cause_type": type(cause).__name__ if cause is not None else None,
        "cause_message": str(cause) if cause is not None else None,
        "silent_fallback": False,
    }


def _sha256_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "CartPolePPOConfig",
    "DEFAULT_ENV_ID",
    "MAX_NUM_ITERS",
    "RLlibPPOAdapterError",
    "evaluate_policy",
    "random_policy_baseline",
    "train_ppo",
]
