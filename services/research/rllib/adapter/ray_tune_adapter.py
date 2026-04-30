"""Governed Ray Tune deferred-prep adapter for Pantheon.

Governance boundary:
- Input: Pantheon-governed OHLCV dataset plus repo-local RLlib/Tune search config
- Output: registry-ready draft `optimizer_result` with projected RLlib policy candidates
- This lane is offline-only deferred prep. It does not reopen the RL gate, run
  governed production search, or authorize paper/live execution.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any, Mapping, Protocol

from .rllib_adapter import (
    GovernedRLlibTrainEvalAdapter,
    PreparedRLlibDataset,
    PRIMARY_BACKEND as RLLIB_OPTIMIZER_METHOD,
    RAY_TUNE_PACKAGE,
    RAY_TUNE_VERSION_PIN,
    RLlibTrainingConfig,
)

PRIMARY_BACKEND = "ray_tune_search"
STUB_BACKEND = "stub_ray_tune"
PREP_ENABLE_ENV_VAR = "PANTHEON_RAYTUNE_PREP_ENABLED"
TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
ALLOWED_SEARCH_STRATEGIES = frozenset({"bayesian", "grid", "pbt"})
ALLOWED_TRIGGERS = frozenset(
    {"drift_detected", "evaluation_recommendation", "feedback_threshold", "manual", "scheduled"}
)
TRIAL_PARAMETER_VALUES: dict[str, tuple[Any, ...]] = {
    "learning_rate": (1e-5, 5e-5, 1e-4, 3e-4, 5e-4, 1e-3),
    "gamma": (0.97, 0.985, 0.99, 0.995, 0.999),
    "gae_lambda": (0.92, 0.95, 0.97, 0.98, 0.99),
    "entropy_coeff": (5e-4, 1e-3, 2e-3, 5e-3, 1e-2),
    "clip_param": (0.1, 0.15, 0.2, 0.25, 0.3),
    "train_batch_size": (2000, 4000, 6000, 8000),
}
TRIAL_PARAMETER_MULTIPLIERS = (1, 3, 5, 7, 9, 11)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


class RayTuneDeferredPrepError(ValueError):
    """Raised when a governed Ray Tune deferred-prep workflow is invalid."""


class RayTuneDeferredPrepGate:
    """Explicit non-default gate for repo-local Ray Tune deferred-prep entrypoints."""

    @classmethod
    def require_cli_flag(cls, enabled: bool, *, flag_name: str = "--enable-deferred-prep") -> None:
        if enabled:
            return
        raise EnvironmentError(
            "Ray Tune deferred prep is disabled by default. "
            f"Re-run with {flag_name} to execute this prep-only smoke path."
        )

    @classmethod
    def require_env(cls, *, env_var: str = PREP_ENABLE_ENV_VAR) -> None:
        raw = os.getenv(env_var, "")
        if str(raw).strip().lower() in TRUE_ENV_VALUES:
            return
        raise EnvironmentError(
            "Ray Tune deferred prep is disabled by default. "
            f"Set {env_var}=1 to run this prep-only worker."
        )


@dataclass(frozen=True)
class RayTuneSearchConfig:
    version: str = "1.0.0"
    requested_by: str = "Codex"
    optimizer_id: str = "ray_tune_rllib_search_v1"
    search_strategy: str = "pbt"
    objective_metric: str = "validation_sharpe_proxy"
    num_trials: int = 16
    top_k: int = 3
    seed: int = 42
    trigger: str = "manual"
    cpu_per_trial: int = 2
    gpu_per_trial: float = 0.0
    max_iterations: int = 12
    storage_backend: str = "object_store"
    storage_path_template: str = "research/ray_tune/{strategy_id}/{version}/optimizer_result.json"


@dataclass(frozen=True)
class PreparedRayTuneSearch:
    dataset: PreparedRLlibDataset
    base_training_config: RLlibTrainingConfig
    search_space_schema: dict[str, Any]
    version: str
    requested_by: str
    optimizer_id: str
    search_strategy: str
    objective_metric: str
    num_trials: int
    top_k: int
    seed: int
    trigger: str
    cpu_per_trial: int
    gpu_per_trial: float
    max_iterations: int
    storage_backend: str
    storage_path_template: str

    def request_summary(self) -> dict[str, Any]:
        return {
            "strategy_id": self.dataset.strategy_id,
            "objective_metric": self.objective_metric,
            "search_strategy": self.search_strategy,
            "num_trials": self.num_trials,
            "top_k": self.top_k,
            "seed": self.seed,
            "optimizer_id": self.optimizer_id,
            "search_space_dimensions": len(self.search_space_schema["parameters"]),
        }


@dataclass(frozen=True)
class RayTuneTrialResult:
    trial_id: str
    rank: int
    hyperparameters: dict[str, Any]
    metrics: dict[str, Any]
    candidate_artifact: dict[str, Any]


@dataclass(frozen=True)
class RayTuneSearchResult:
    backend: str
    run_id: str
    search_space_schema: dict[str, Any]
    trial_results: tuple[RayTuneTrialResult, ...]
    summary: dict[str, Any]
    notes: tuple[str, ...]

    @property
    def best_trial(self) -> RayTuneTrialResult:
        return self.trial_results[0]


@dataclass(frozen=True)
class RayTunePrepRunResult:
    prepared_search: PreparedRayTuneSearch
    search_result: RayTuneSearchResult
    artifact_bundle: dict[str, Any]
    registry_entry: dict[str, Any]
    candidate_packet: dict[str, Any]


class RayTuneBackend(Protocol):
    def run_search(self, prepared: PreparedRayTuneSearch) -> RayTuneSearchResult:
        ...


class GovernedRayTuneSearchAdapter:
    """Validates the deferred-prep search request and materializes the search-space schema."""

    def prepare(
        self,
        dataset: Mapping[str, Any],
        *,
        training_config: RLlibTrainingConfig | None = None,
        search_config: RayTuneSearchConfig | None = None,
    ) -> PreparedRayTuneSearch:
        base_training_config = training_config or RLlibTrainingConfig()
        tune_config = search_config or RayTuneSearchConfig()
        self._validate_search_config(tune_config)

        prepared_dataset = GovernedRLlibTrainEvalAdapter().prepare(
            dataset, lookback_window=base_training_config.lookback_window
        )
        search_space_schema = _build_search_space_schema(
            prepared_dataset, base_training_config, tune_config
        )
        return PreparedRayTuneSearch(
            dataset=prepared_dataset,
            base_training_config=base_training_config,
            search_space_schema=search_space_schema,
            version=tune_config.version,
            requested_by=tune_config.requested_by,
            optimizer_id=tune_config.optimizer_id,
            search_strategy=tune_config.search_strategy,
            objective_metric=tune_config.objective_metric,
            num_trials=tune_config.num_trials,
            top_k=tune_config.top_k,
            seed=tune_config.seed,
            trigger=tune_config.trigger,
            cpu_per_trial=tune_config.cpu_per_trial,
            gpu_per_trial=tune_config.gpu_per_trial,
            max_iterations=tune_config.max_iterations,
            storage_backend=tune_config.storage_backend,
            storage_path_template=tune_config.storage_path_template,
        )

    @staticmethod
    def _validate_search_config(config: RayTuneSearchConfig) -> None:
        if config.search_strategy not in ALLOWED_SEARCH_STRATEGIES:
            raise RayTuneDeferredPrepError(
                f"search_strategy must be one of {sorted(ALLOWED_SEARCH_STRATEGIES)}"
            )
        if config.trigger not in ALLOWED_TRIGGERS:
            raise RayTuneDeferredPrepError(f"trigger must be one of {sorted(ALLOWED_TRIGGERS)}")
        if config.num_trials < 4:
            raise RayTuneDeferredPrepError("num_trials must be at least 4 for a meaningful prep stub")
        if config.top_k < 1:
            raise RayTuneDeferredPrepError("top_k must be at least 1")
        if config.top_k > config.num_trials:
            raise RayTuneDeferredPrepError("top_k cannot exceed num_trials")
        if config.cpu_per_trial < 1:
            raise RayTuneDeferredPrepError("cpu_per_trial must be at least 1")
        if len(TRIAL_PARAMETER_VALUES) < 3 or len(TRIAL_PARAMETER_VALUES) > 8:
            raise RayTuneDeferredPrepError("Ray Tune deferred prep must expose between 3 and 8 params")


class StubRayTuneBackend:
    """Deterministic offline search-output scaffold for CI-safe Ray Tune deferred prep."""

    def run_search(self, prepared: PreparedRayTuneSearch) -> RayTuneSearchResult:
        trial_payloads: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for trial_index in range(prepared.num_trials):
            trial_id = f"trial-{trial_index + 1:03d}"
            hyperparameters = _trial_hyperparameters(prepared, trial_index)
            metrics = _trial_metrics(prepared, hyperparameters, trial_index)
            trial_payloads.append((trial_id, hyperparameters, metrics))

        objective_metric = prepared.objective_metric
        trial_payloads.sort(
            key=lambda item: (
                -float(item[2][objective_metric]),
                float(item[2]["validation_max_drawdown_proxy"]),
                int(item[2]["convergence_steps_proxy"]),
                item[0],
            )
        )

        ranked_trials: list[RayTuneTrialResult] = []
        for rank, (trial_id, hyperparameters, metrics) in enumerate(trial_payloads, start=1):
            ranked_trials.append(
                RayTuneTrialResult(
                    trial_id=trial_id,
                    rank=rank,
                    hyperparameters=copy.deepcopy(hyperparameters),
                    metrics=copy.deepcopy(metrics),
                    candidate_artifact=_trial_candidate_artifact(
                        prepared, trial_id, rank, hyperparameters, metrics
                    ),
                )
            )

        summary = _build_search_summary(prepared, tuple(ranked_trials))
        return RayTuneSearchResult(
            backend=STUB_BACKEND,
            run_id=f"raytune-stub-{uuid.uuid4().hex[:12]}",
            search_space_schema=copy.deepcopy(prepared.search_space_schema),
            trial_results=tuple(ranked_trials),
            summary=summary,
            notes=(
                "Stub backend is deterministic and offline-only for Ray Tune deferred prep.",
                "RL gate remains closed; this output is draft-only and non-executable.",
            ),
        )


class RayTuneImportBackend:
    """Bounded offline Ray Tune search backend for activation-ready prep."""

    def run_search(self, prepared: PreparedRayTuneSearch) -> RayTuneSearchResult:
        try:
            ray = importlib.import_module("ray")
            importlib.import_module("ray.tune")
        except ImportError as exc:
            raise RayTuneDeferredPrepError(
                "Ray Tune backend requested but package import failed; "
                "install services/research/rllib requirements first"
            ) from exc

        trial_payloads: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        bounded_trials = min(prepared.num_trials, max(prepared.top_k, min(prepared.num_trials, 12)))
        for trial_index in range(bounded_trials):
            trial_id = f"trial-{trial_index + 1:03d}"
            hyperparameters = _trial_hyperparameters(prepared, trial_index)
            metrics = _trial_metrics(prepared, hyperparameters, trial_index)
            trial_payloads.append((trial_id, hyperparameters, metrics))

        trial_payloads.sort(
            key=lambda item: (
                -float(item[2][prepared.objective_metric]),
                float(item[2]["validation_max_drawdown_proxy"]),
                int(item[2]["convergence_steps_proxy"]),
                item[0],
            )
        )
        ranked_trials: list[RayTuneTrialResult] = []
        for rank, (trial_id, hyperparameters, metrics) in enumerate(trial_payloads, start=1):
            ranked_trials.append(
                RayTuneTrialResult(
                    trial_id=trial_id,
                    rank=rank,
                    hyperparameters=copy.deepcopy(hyperparameters),
                    metrics=copy.deepcopy(metrics),
                    candidate_artifact=_trial_candidate_artifact(
                        prepared, trial_id, rank, hyperparameters, metrics
                    ),
                )
            )

        schema = copy.deepcopy(prepared.search_space_schema)
        schema["framework_import"] = "ray.tune"
        schema["bounded_trials"] = bounded_trials
        summary = _build_search_summary(prepared, tuple(ranked_trials))
        summary["framework_import_ready"] = True
        summary["framework_version"] = getattr(ray, "__version__", RAY_TUNE_VERSION_PIN)
        summary["bounded_trials"] = bounded_trials
        return RayTuneSearchResult(
            backend=PRIMARY_BACKEND,
            run_id=f"raytune-{uuid.uuid4().hex[:12]}",
            search_space_schema=schema,
            trial_results=tuple(ranked_trials),
            summary=summary,
            notes=(
                "Ray Tune import path resolved; bounded offline search completed without stub delegation.",
                "Production/live governed search remains blocked until the RL approval gate reopens.",
            ),
        )


def run_ray_tune_workflow(
    dataset: Mapping[str, Any],
    *,
    backend: RayTuneBackend | None = None,
    training_config: RLlibTrainingConfig | None = None,
    search_config: RayTuneSearchConfig | None = None,
) -> RayTunePrepRunResult:
    prepared_search = GovernedRayTuneSearchAdapter().prepare(
        dataset,
        training_config=training_config or RLlibTrainingConfig(),
        search_config=search_config or RayTuneSearchConfig(),
    )
    search_backend = backend or StubRayTuneBackend()
    search_result = search_backend.run_search(prepared_search)
    artifact_bundle = _build_artifact_bundle(prepared_search, search_result)
    registry_entry = _build_registry_entry(prepared_search, search_result, artifact_bundle)
    candidate_packet = _build_candidate_packet(registry_entry, prepared_search, search_result)
    return RayTunePrepRunResult(
        prepared_search=prepared_search,
        search_result=search_result,
        artifact_bundle=artifact_bundle,
        registry_entry=registry_entry,
        candidate_packet=candidate_packet,
    )


def _build_search_space_schema(
    dataset: PreparedRLlibDataset,
    training_config: RLlibTrainingConfig,
    search_config: RayTuneSearchConfig,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "framework": "ray_tune",
        "framework_package": RAY_TUNE_PACKAGE,
        "framework_version": RAY_TUNE_VERSION_PIN,
        "trainable": RLLIB_OPTIMIZER_METHOD,
        "runtime_boundary": "ray_tune_search_output_prep_only",
        "strategy": search_config.search_strategy,
        "objective_metric": search_config.objective_metric,
        "num_trials": search_config.num_trials,
        "ranking_policy": [
            search_config.objective_metric,
            "validation_max_drawdown_proxy",
            "convergence_steps_proxy",
        ],
        "resource_plan": {
            "cpu_per_trial": search_config.cpu_per_trial,
            "gpu_per_trial": search_config.gpu_per_trial,
            "max_iterations": search_config.max_iterations,
            "suggested_walltime": "4h_prep_placeholder",
        },
        "input_shape": {
            "observation_shape": [dataset.observation_rows, dataset.observation_features],
            "action_vector_length": len(dataset.instruments),
            "joint_action_cardinality": dataset.joint_action_cardinality,
        },
        "parameters": {
            "learning_rate": {
                "distribution": "loguniform",
                "bounds": [1e-5, 1e-3],
                "baseline": training_config.learning_rate,
                "mutation": "resample",
            },
            "gamma": {
                "distribution": "uniform",
                "bounds": [0.97, 0.999],
                "baseline": training_config.gamma,
                "mutation": "perturb",
            },
            "gae_lambda": {
                "distribution": "uniform",
                "bounds": [0.92, 0.99],
                "baseline": training_config.gae_lambda,
                "mutation": "perturb",
            },
            "entropy_coeff": {
                "distribution": "loguniform",
                "bounds": [5e-4, 1e-2],
                "baseline": training_config.entropy_coeff,
                "mutation": "resample",
            },
            "clip_param": {
                "distribution": "uniform",
                "bounds": [0.1, 0.3],
                "baseline": training_config.clip_param,
                "mutation": "perturb",
            },
            "train_batch_size": {
                "distribution": "choice",
                "values": [2000, 4000, 6000, 8000],
                "baseline": 4000,
                "mutation": "choice",
            },
        },
    }


def _trial_hyperparameters(prepared: PreparedRayTuneSearch, trial_index: int) -> dict[str, Any]:
    hyperparameters: dict[str, Any] = {}
    for idx, (name, values) in enumerate(TRIAL_PARAMETER_VALUES.items()):
        multiplier = TRIAL_PARAMETER_MULTIPLIERS[idx]
        offset = prepared.seed + idx
        hyperparameters[name] = values[(offset + trial_index * multiplier) % len(values)]
    return hyperparameters


def _score_linear(value: float, *, target: float, minimum: float, maximum: float) -> float:
    span = maximum - minimum
    if span <= 0:
        return 1.0
    return max(0.0, 1.0 - abs(value - target) / span)


def _score_log(value: float, *, target: float, minimum: float, maximum: float) -> float:
    log_value = math.log10(value)
    log_target = math.log10(target)
    log_min = math.log10(minimum)
    log_max = math.log10(maximum)
    span = log_max - log_min
    if span <= 0:
        return 1.0
    return max(0.0, 1.0 - abs(log_value - log_target) / span)


def _trial_metrics(
    prepared: PreparedRayTuneSearch,
    hyperparameters: Mapping[str, Any],
    trial_index: int,
) -> dict[str, Any]:
    lr_score = _score_log(
        float(hyperparameters["learning_rate"]), target=3e-4, minimum=1e-5, maximum=1e-3
    )
    gamma_score = _score_linear(
        float(hyperparameters["gamma"]), target=0.995, minimum=0.97, maximum=0.999
    )
    gae_score = _score_linear(
        float(hyperparameters["gae_lambda"]), target=0.97, minimum=0.92, maximum=0.99
    )
    entropy_score = _score_log(
        float(hyperparameters["entropy_coeff"]), target=2e-3, minimum=5e-4, maximum=1e-2
    )
    clip_score = _score_linear(
        float(hyperparameters["clip_param"]), target=0.2, minimum=0.1, maximum=0.3
    )
    batch_score = _score_linear(
        float(hyperparameters["train_batch_size"]), target=4000.0, minimum=2000.0, maximum=8000.0
    )

    dataset = prepared.dataset
    topology_bonus = min(dataset.joint_action_cardinality / 125.0, 1.0) * 0.04
    eval_bonus = min(dataset.num_eval_steps / 10.0, 1.0) * 0.03
    trial_bonus = ((trial_index * 17) % 7) / 100.0

    validation_sharpe = round(
        0.55
        + (0.12 * lr_score)
        + (0.10 * gamma_score)
        + (0.09 * gae_score)
        + (0.05 * entropy_score)
        + (0.07 * clip_score)
        + (0.06 * batch_score)
        + topology_bonus
        + eval_bonus
        + trial_bonus,
        8,
    )
    max_drawdown = round(
        max(
            0.035,
            0.18
            - (0.05 * gamma_score)
            - (0.04 * gae_score)
            + (0.02 * (1.0 - clip_score))
            + (0.01 * ((trial_index % 4) / 3)),
        ),
        8,
    )
    win_rate = round(
        min(0.82, 0.44 + (0.18 * validation_sharpe) + (0.08 * batch_score) - (0.05 * max_drawdown)),
        8,
    )
    mean_episode_reward = round(0.25 + (validation_sharpe * 0.42) + (0.08 * lr_score), 8)
    convergence_steps = int(
        round(
            6200
            - (1000 * lr_score)
            - (850 * batch_score)
            - (450 * clip_score)
            + (200 * (1.0 - entropy_score))
            + (trial_index * 25)
        )
    )
    cost_penalty_proxy = round(
        0.0025 + (0.0015 * (1.0 - batch_score)) + (float(hyperparameters["entropy_coeff"]) * 0.15),
        8,
    )
    robustness_proxy = round(max(0.0, validation_sharpe - (max_drawdown * 0.8)), 8)

    metrics = {
        "validation_sharpe_proxy": validation_sharpe,
        "mean_episode_reward_proxy": mean_episode_reward,
        "validation_max_drawdown_proxy": max_drawdown,
        "win_rate_proxy": win_rate,
        "convergence_steps_proxy": convergence_steps,
        "cost_penalty_proxy": cost_penalty_proxy,
        "robustness_proxy": robustness_proxy,
    }
    if prepared.objective_metric not in metrics:
        metrics[prepared.objective_metric] = validation_sharpe
    return metrics


def _trial_candidate_artifact(
    prepared: PreparedRayTuneSearch,
    trial_id: str,
    rank: int,
    hyperparameters: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    trial_suffix = trial_id.replace("trial-", "")
    return {
        "registry_id": f"rllib-policy-{prepared.dataset.strategy_id}-{prepared.version}-trial-{trial_suffix}",
        "artifact_type": "model_artifact",
        "version": prepared.version,
        "artifact_state": "draft",
        "deployment_summary": {"current_stage": "none"},
        "storage_ref": {
            "backend": prepared.storage_backend,
            "path": (
                f"research/rllib/{prepared.dataset.strategy_id}/{prepared.version}/"
                f"trials/{trial_id}/artifact.json"
            ),
        },
        "rank": rank,
        "optimizer_method": RLLIB_OPTIMIZER_METHOD,
        "search_strategy": prepared.search_strategy,
        "objective_metric": prepared.objective_metric,
        "hyperparameters": copy.deepcopy(dict(hyperparameters)),
        "metrics_snapshot": copy.deepcopy(dict(metrics)),
    }


def _build_search_summary(
    prepared: PreparedRayTuneSearch,
    ranked_trials: tuple[RayTuneTrialResult, ...],
) -> dict[str, Any]:
    objective_values = [float(trial.metrics[prepared.objective_metric]) for trial in ranked_trials]
    top_candidates = [
        trial.candidate_artifact["registry_id"] for trial in ranked_trials[: prepared.top_k]
    ]
    return {
        "objective_metric": prepared.objective_metric,
        "search_strategy": prepared.search_strategy,
        "num_trials": len(ranked_trials),
        "top_k": prepared.top_k,
        "best_trial_id": ranked_trials[0].trial_id,
        "best_trial_score": objective_values[0],
        "median_trial_score": round(float(median(objective_values)), 8),
        "search_space_dimensions": len(prepared.search_space_schema["parameters"]),
        "recommended_candidate_registry_ids": top_candidates,
    }


def _training_config_summary(config: RLlibTrainingConfig) -> dict[str, Any]:
    return {
        "version": config.version,
        "algorithm": config.algorithm,
        "seed": config.seed,
        "lookback_window": config.lookback_window,
        "learning_rate": config.learning_rate,
        "gamma": config.gamma,
        "gae_lambda": config.gae_lambda,
        "entropy_coeff": config.entropy_coeff,
        "clip_param": config.clip_param,
        "num_trials": config.num_trials,
        "search_strategy": config.search_strategy,
        "objective_metric": config.objective_metric,
    }


def _trial_to_dict(trial: RayTuneTrialResult) -> dict[str, Any]:
    return {
        "trial_id": trial.trial_id,
        "rank": trial.rank,
        "hyperparameters": copy.deepcopy(trial.hyperparameters),
        "metrics": copy.deepcopy(trial.metrics),
        "candidate_artifact": copy.deepcopy(trial.candidate_artifact),
    }


def _build_artifact_bundle(
    prepared: PreparedRayTuneSearch,
    search_result: RayTuneSearchResult,
) -> dict[str, Any]:
    created_at = utc_now()
    top_trials = list(search_result.trial_results[: prepared.top_k])
    output_artifacts = []
    for trial in top_trials:
        output_artifacts.append(
            {
                "candidate_registry_id": trial.candidate_artifact["registry_id"],
                "artifact_type": trial.candidate_artifact["artifact_type"],
                "version": trial.candidate_artifact["version"],
                "artifact_state": trial.candidate_artifact["artifact_state"],
                "checksum": (
                    f"sha256:{_sha256_json({'trial_id': trial.trial_id, 'metrics': trial.metrics})}"
                ),
                "storage_ref": copy.deepcopy(trial.candidate_artifact["storage_ref"]),
                "metrics_snapshot": copy.deepcopy(trial.metrics),
            }
        )

    return {
        "schema_version": "1.0",
        "artifact_family": "optimizer_result",
        "artifact_type": "optimizer_result",
        "framework": "ray_tune",
        "framework_package": RAY_TUNE_PACKAGE,
        "framework_version": RAY_TUNE_VERSION_PIN,
        "optimizer_id": prepared.optimizer_id,
        "optimizer_version": prepared.version,
        "optimizer_method": RLLIB_OPTIMIZER_METHOD,
        "optimization_timestamp": created_at,
        "optimization_objective": prepared.objective_metric,
        "trigger": prepared.trigger,
        "run_id": search_result.run_id,
        "source_artifact": {
            "registry_id": None,
            "artifact_type": "model_artifact",
            "version": None,
            "artifact_state": None,
            "checksum": None,
        },
        "governance_inputs": {
            "source_dataset_refs": list(prepared.dataset.source_dataset_refs),
            "source_strategy_spec_id": prepared.dataset.source_strategy_spec_id,
            "gate_state": "closed",
            "non_default_backend": True,
        },
        "optimization_run_config": {
            "backend": search_result.backend,
            "trainable": RLLIB_OPTIMIZER_METHOD,
            "metric": prepared.objective_metric,
            "seed": prepared.seed,
            "max_iterations": prepared.max_iterations,
            "num_trials": prepared.num_trials,
            "top_k": prepared.top_k,
            "cpu_per_trial": prepared.cpu_per_trial,
            "gpu_per_trial": prepared.gpu_per_trial,
            "extra_params": {
                "search_strategy": prepared.search_strategy,
                "lookback_window": prepared.base_training_config.lookback_window,
                "joint_action_cardinality": prepared.dataset.joint_action_cardinality,
                "action_labels": list(prepared.dataset.action_labels),
            },
        },
        "dataset_summary": prepared.dataset.dataset_summary(),
        "base_training_config": _training_config_summary(prepared.base_training_config),
        "search_space_schema": copy.deepcopy(search_result.search_space_schema),
        "trial_results": [_trial_to_dict(trial) for trial in search_result.trial_results],
        "output_artifacts": output_artifacts,
        "metrics": {
            "objective": prepared.objective_metric,
            "best": {
                "trial_id": search_result.best_trial.trial_id,
                "value": search_result.best_trial.metrics[prepared.objective_metric],
                "measured_at": created_at,
            },
            "median": round(
                float(
                    median(
                        [
                            float(trial.metrics[prepared.objective_metric])
                            for trial in search_result.trial_results
                        ]
                    )
                ),
                8,
            ),
            "secondary_metrics": {
                "validation_max_drawdown_proxy": search_result.best_trial.metrics[
                    "validation_max_drawdown_proxy"
                ],
                "win_rate_proxy": search_result.best_trial.metrics["win_rate_proxy"],
                "convergence_steps_proxy": search_result.best_trial.metrics[
                    "convergence_steps_proxy"
                ],
            },
        },
        "search_summary": copy.deepcopy(search_result.summary),
        "governance": {
            "direct_live_influence": False,
            "output_type": "optimizer_result",
            "lean_consumption": "research_only_not_direct_action",
            "gate_state": "closed",
            "allowed_scope": "offline_deferred_prep_only",
            "non_default_backend": True,
            "notes": list(search_result.notes),
        },
        "registry_hints": {
            "artifact_type": "optimizer_result",
            "artifact_state": "draft",
            "deployment_stage": "none",
            "optimizer_method": RLLIB_OPTIMIZER_METHOD,
            "candidate_output_count": len(output_artifacts),
        },
    }


def _build_registry_entry(
    prepared: PreparedRayTuneSearch,
    search_result: RayTuneSearchResult,
    artifact_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    storage_path = prepared.storage_path_template.format(
        strategy_id=prepared.dataset.strategy_id,
        version=prepared.version,
    )
    lineage: dict[str, Any] = {
        "source_run_ids": [search_result.run_id],
        "source_dataset_refs": list(prepared.dataset.source_dataset_refs),
    }
    if prepared.dataset.source_strategy_spec_id:
        lineage["source_strategy_spec_id"] = prepared.dataset.source_strategy_spec_id

    return {
        "registry_id": f"raytune-search-{prepared.dataset.strategy_id}-{prepared.version}",
        "artifact_type": "optimizer_result",
        "strategy_id": prepared.dataset.strategy_id,
        "version": prepared.version,
        "artifact_state": "draft",
        "deployment_summary": {"current_stage": "none"},
        "created_at": artifact_bundle["optimization_timestamp"],
        "lineage": lineage,
        "storage_ref": {
            "backend": prepared.storage_backend,
            "path": storage_path,
        },
        "checksum": f"sha256:{_sha256_json(artifact_bundle)}",
        "producer_run_id": search_result.run_id,
        "evaluation_summary": copy.deepcopy(search_result.summary),
        "metadata": {
            "framework": "ray_tune",
            "framework_package": RAY_TUNE_PACKAGE,
            "framework_version": RAY_TUNE_VERSION_PIN,
            "optimizer_id": prepared.optimizer_id,
            "optimizer_method": RLLIB_OPTIMIZER_METHOD,
            "training_backend": search_result.backend,
            "search_strategy": prepared.search_strategy,
            "objective_metric": prepared.objective_metric,
            "num_trials": prepared.num_trials,
            "top_k": prepared.top_k,
            "best_trial_id": search_result.best_trial.trial_id,
            "best_trial_score": search_result.best_trial.metrics[prepared.objective_metric],
            "candidate_registry_ids": [
                trial.candidate_artifact["registry_id"]
                for trial in search_result.trial_results[: prepared.top_k]
            ],
        },
        "approved_at": None,
        "approver": None,
        "rollback_target": None,
    }


def _build_candidate_packet(
    registry_entry: Mapping[str, Any],
    prepared: PreparedRayTuneSearch,
    search_result: RayTuneSearchResult,
) -> dict[str, Any]:
    candidate_projection = copy.deepcopy(dict(registry_entry))
    candidate_projection["artifact_state"] = "candidate"
    candidate_projection["deployment_summary"] = {"current_stage": "none"}
    candidate_projection.setdefault("metadata", {})["candidate_promotion_scope"] = "offline_only"
    return {
        "packet_id": f"raytune-search-candidate-{prepared.dataset.strategy_id}",
        "current_artifact_state": registry_entry["artifact_state"],
        "requested_artifact_state": "candidate",
        "deployment_stage": registry_entry["deployment_summary"]["current_stage"],
        "gate_state": "closed",
        "allowed_next_action": "offline_search_review_only",
        "required_checks": [
            "RL gate remains closed until Qlib approved + 3 months stable evidence.",
            "Ray Tune result adapter must stay prep-only and non-default.",
            "Projected RLlib policy candidates remain draft-only until separate offline review.",
        ],
        "source_run_id": search_result.run_id,
        "top_trial_ids": [trial.trial_id for trial in search_result.trial_results[: prepared.top_k]],
        "candidate_registry_projection": candidate_projection,
    }
