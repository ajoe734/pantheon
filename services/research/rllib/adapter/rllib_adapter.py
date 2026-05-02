"""Governed RLlib train/eval deferred-prep adapter for Pantheon.

Governance boundary:
- Input: Pantheon-governed OHLCV dataset plus repo-local train/eval config
- Output: registry-ready draft `model_artifact` with `model_family=rl_policy`
- This lane is offline-only deferred prep. It does not reopen the RL gate, run
  governed production training, or authorize paper/live execution.
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
from typing import Any, Mapping, Protocol, Sequence

RLLIB_PACKAGE = "ray[rllib]"
RLLIB_VERSION_PIN = ">=2.9.0,<3.0.0"
RAY_TUNE_PACKAGE = "ray[tune]"
RAY_TUNE_VERSION_PIN = ">=2.9.0,<3.0.0"
PRIMARY_BACKEND = "rllib_ppo"
STUB_BACKEND = "stub_rllib"
DEFAULT_ACTION_LABELS = (
    "sell_large",
    "sell_small",
    "hold",
    "buy_small",
    "buy_large",
)
ALLOWED_DECISION_FOCUS = frozenset(
    {"exit_timing", "position_sizing", "rebalance", "portfolio_optimization"}
)
REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
OBSERVATION_FEATURES = 8
MIN_INSTRUMENTS = 2
MIN_PERIODS = 8
MIN_LOOKBACK = 3
MIN_EVAL_STEPS = 1
PREP_ENABLE_ENV_VAR = "PANTHEON_RLLIB_PREP_ENABLED"
TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
DEFAULT_REWARD_SPEC = (
    ("cost_penalty", 0.1),
    ("dd_penalty", 0.5),
    ("sharpe_weight", 1.0),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _rollout_step_payload(step: "RolloutStep") -> dict[str, Any]:
    return {
        "split": step.split,
        "step_index": step.step_index,
        "period": step.period,
        "observation": list(step.observation),
        "action_mask": list(step.action_mask),
        "reward_proxy": step.reward_proxy,
        "portfolio_value_proxy": step.portfolio_value_proxy,
        "max_drawdown_proxy": step.max_drawdown_proxy,
    }


def _prepared_dataset_payload(dataset: "PreparedRLlibDataset") -> dict[str, Any]:
    return {
        "dataset_summary": dataset.dataset_summary(),
        "periods": list(dataset.periods),
        "train_rollout": [_rollout_step_payload(step) for step in dataset.train_rollout],
        "eval_rollout": [_rollout_step_payload(step) for step in dataset.eval_rollout],
    }


def prepared_rllib_dataset_checksum(dataset: "PreparedRLlibDataset") -> str:
    return f"sha256:{_sha256_json(_prepared_dataset_payload(dataset))}"


class RLlibDeferredPrepError(ValueError):
    """Raised when a governed RLlib deferred-prep workflow is invalid."""


class DeferredPrepGate:
    """Explicit non-default gate for repo-local RLlib deferred-prep entrypoints."""

    @classmethod
    def require_cli_flag(cls, enabled: bool, *, flag_name: str = "--enable-deferred-prep") -> None:
        if enabled:
            return
        raise EnvironmentError(
            "RLlib deferred prep is disabled by default. "
            f"Re-run with {flag_name} to execute this prep-only smoke path."
        )

    @classmethod
    def require_env(cls, *, env_var: str = PREP_ENABLE_ENV_VAR) -> None:
        raw = os.getenv(env_var, "")
        if str(raw).strip().lower() in TRUE_ENV_VALUES:
            return
        raise EnvironmentError(
            "RLlib deferred prep is disabled by default. "
            f"Set {env_var}=1 to run this prep-only worker."
        )


@dataclass(frozen=True)
class RolloutStep:
    split: str
    step_index: int
    period: str
    observation: tuple[float, ...]
    action_mask: tuple[int, ...]
    reward_proxy: float
    portfolio_value_proxy: float
    max_drawdown_proxy: float


@dataclass(frozen=True)
class PreparedRLlibDataset:
    dataset_id: str
    strategy_id: str
    source_dataset_refs: tuple[str, ...]
    source_strategy_spec_id: str | None
    decision_focus: str
    data_frequency: str
    lookback_window: int
    instruments: tuple[str, ...]
    periods: tuple[str, ...]
    action_labels: tuple[str, ...]
    observation_rows: int
    observation_features: int
    flattened_observation_dim: int
    joint_action_cardinality: int
    reward_spec: tuple[tuple[str, float], ...]
    transaction_cost_pct: float
    slippage_bps: float
    max_position_size: float
    train_rollout: tuple[RolloutStep, ...]
    eval_rollout: tuple[RolloutStep, ...]

    @property
    def num_train_steps(self) -> int:
        return len(self.train_rollout)

    @property
    def num_eval_steps(self) -> int:
        return len(self.eval_rollout)

    def dataset_summary(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "strategy_id": self.strategy_id,
            "source_dataset_refs": list(self.source_dataset_refs),
            "source_strategy_spec_id": self.source_strategy_spec_id,
            "decision_focus": self.decision_focus,
            "data_frequency": self.data_frequency,
            "lookback_window": self.lookback_window,
            "instruments": list(self.instruments),
            "num_instruments": len(self.instruments),
            "observation_shape": [self.observation_rows, self.observation_features],
            "flattened_observation_dim": self.flattened_observation_dim,
            "action_labels": list(self.action_labels),
            "joint_action_cardinality": self.joint_action_cardinality,
            "reward_spec": dict(self.reward_spec),
            "transaction_cost_pct": self.transaction_cost_pct,
            "slippage_bps": self.slippage_bps,
            "max_position_size": self.max_position_size,
            "train_steps": self.num_train_steps,
            "eval_steps": self.num_eval_steps,
        }


@dataclass(frozen=True)
class RLlibTrainingConfig:
    version: str = "1.0.0"
    requested_by: str = "Codex"
    algorithm: str = "ppo"
    seed: int = 42
    lookback_window: int = MIN_LOOKBACK
    learning_rate: float = 3e-4
    gamma: float = 0.99
    gae_lambda: float = 0.95
    entropy_coeff: float = 0.002
    clip_param: float = 0.2
    num_trials: int = 8
    search_strategy: str = "pbt"
    objective_metric: str = "validation_sharpe_proxy"
    storage_backend: str = "object_store"
    storage_path_template: str = "research/rllib/{strategy_id}/{version}/artifact.json"


@dataclass(frozen=True)
class RLlibTrainEvalResult:
    backend: str
    run_id: str
    policy_payload: dict[str, Any]
    train_eval_schema: dict[str, Any]
    rollout_summary: dict[str, Any]
    metrics: dict[str, Any]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class RLlibRunResult:
    prepared_dataset: PreparedRLlibDataset
    train_eval_result: RLlibTrainEvalResult
    artifact_bundle: dict[str, Any]
    registry_entry: dict[str, Any]
    candidate_packet: dict[str, Any]


class RLlibBackend(Protocol):
    def train_and_evaluate(
        self, dataset: PreparedRLlibDataset, config: RLlibTrainingConfig
    ) -> RLlibTrainEvalResult:
        ...


class GovernedRLlibTrainEvalAdapter:
    """Validates governed OHLCV input and builds RLlib-style train/eval rollouts."""

    def prepare(
        self,
        dataset: Mapping[str, Any],
        *,
        lookback_window: int = MIN_LOOKBACK,
    ) -> PreparedRLlibDataset:
        dataset_id = self._req_str(dataset, "dataset_id")
        strategy_id = self._req_str(dataset, "strategy_id")
        source_dataset_refs = tuple(self._normalize_refs(dataset))
        source_strategy_spec_id = self._opt_str(dataset.get("source_strategy_spec_id")) or None
        decision_focus = self._opt_str(dataset.get("decision_focus")) or "rebalance"
        if decision_focus not in ALLOWED_DECISION_FOCUS:
            raise RLlibDeferredPrepError(
                f"decision_focus must be one of {sorted(ALLOWED_DECISION_FOCUS)}"
            )

        data_frequency = self._opt_str(dataset.get("data_frequency")) or "daily"
        action_labels = self._normalize_action_labels(dataset.get("action_labels"))
        lookback = max(int(lookback_window), MIN_LOOKBACK)
        reward_spec = self._normalize_reward_spec(dataset.get("reward_fn_config"))
        transaction_cost_pct = self._opt_float(dataset.get("transaction_cost_pct"), 0.001)
        slippage_bps = self._opt_float(dataset.get("slippage_bps"), 5.0)
        max_position_size = self._opt_float(dataset.get("max_position_size"), 0.3)

        records = dataset.get("records")
        if not isinstance(records, Sequence) or not records:
            raise RLlibDeferredPrepError("dataset.records must be a non-empty list of OHLCV dicts")

        instrument_series: dict[str, list[dict[str, Any]]] = {}
        for rec in records:
            if not isinstance(rec, Mapping):
                raise RLlibDeferredPrepError("Each record must be a mapping")
            instrument = self._req_str(rec, "instrument")
            for field_name in REQUIRED_OHLCV_FIELDS:
                if not isinstance(rec.get(field_name), (int, float)):
                    raise RLlibDeferredPrepError(
                        f"record for {instrument} missing numeric field '{field_name}'"
                    )
            instrument_series.setdefault(instrument, []).append(dict(rec))

        instruments = tuple(sorted(instrument_series.keys()))
        if len(instruments) < MIN_INSTRUMENTS:
            raise RLlibDeferredPrepError(
                f"dataset must have at least {MIN_INSTRUMENTS} instruments; got {len(instruments)}"
            )

        sorted_series: dict[str, list[dict[str, Any]]] = {}
        for instrument in instruments:
            series = sorted(instrument_series[instrument], key=lambda item: str(item.get("date", "")))
            min_required = max(MIN_PERIODS, lookback + 3)
            if len(series) < min_required:
                raise RLlibDeferredPrepError(
                    f"instrument {instrument} has fewer than {min_required} periods"
                )
            sorted_series[instrument] = series

        min_steps = min(len(series) for series in sorted_series.values())
        rollout_candidates: list[dict[str, Any]] = []
        periods: list[str] = []
        cumulative_portfolio_value = 1.0
        peak_portfolio_value = 1.0
        reward_weights = dict(reward_spec)

        for idx in range(lookback, min_steps):
            observation: list[float] = []
            step_returns: list[float] = []
            period = ""
            for instrument in instruments:
                series = sorted_series[instrument]
                window = series[idx - lookback : idx]
                current = series[idx]
                prev = series[idx - 1]
                last_close = float(current["close"])
                avg_volume = sum(float(bar["volume"]) for bar in window) / len(window)
                for bar in window:
                    observation.extend(
                        (
                            round(float(bar["open"]) / (last_close + 1e-9), 8),
                            round(float(bar["high"]) / (last_close + 1e-9), 8),
                            round(float(bar["low"]) / (last_close + 1e-9), 8),
                            round(float(bar["close"]) / (last_close + 1e-9), 8),
                            round(float(bar["volume"]) / (avg_volume + 1e-9), 8),
                            0.0,
                            1.0,
                            1.0,
                        )
                    )
                period = str(current.get("date", f"t-{idx}"))
                step_returns.append(
                    (last_close - float(prev["close"])) / (float(prev["close"]) + 1e-9)
                )

            gross_return = sum(step_returns) / len(step_returns)
            trading_cost = transaction_cost_pct + (slippage_bps / 10000.0)
            reward_proxy = (
                reward_weights["sharpe_weight"] * gross_return
                - reward_weights["dd_penalty"] * max(-gross_return, 0.0)
                - reward_weights["cost_penalty"] * trading_cost
            )
            cumulative_portfolio_value = max(
                0.01,
                cumulative_portfolio_value * (1.0 + gross_return - trading_cost),
            )
            peak_portfolio_value = max(peak_portfolio_value, cumulative_portfolio_value)
            max_drawdown_proxy = max(
                0.0,
                1.0 - (cumulative_portfolio_value / (peak_portfolio_value + 1e-9)),
            )

            rollout_candidates.append(
                {
                    "period": period,
                    "observation": tuple(observation),
                    "action_mask": tuple(1 for _ in action_labels),
                    "reward_proxy": round(reward_proxy, 8),
                    "portfolio_value_proxy": round(cumulative_portfolio_value, 8),
                    "max_drawdown_proxy": round(max_drawdown_proxy, 8),
                }
            )
            periods.append(period)

        if len(rollout_candidates) < 3:
            raise RLlibDeferredPrepError("No usable train/eval rollout after lookback normalization")

        eval_steps = max(MIN_EVAL_STEPS, len(rollout_candidates) // 3)
        if len(rollout_candidates) - eval_steps < 1:
            eval_steps = MIN_EVAL_STEPS
        train_candidates = rollout_candidates[:-eval_steps]
        eval_candidates = rollout_candidates[-eval_steps:]

        train_rollout = tuple(
            RolloutStep(
                split="train",
                step_index=index,
                period=item["period"],
                observation=item["observation"],
                action_mask=item["action_mask"],
                reward_proxy=item["reward_proxy"],
                portfolio_value_proxy=item["portfolio_value_proxy"],
                max_drawdown_proxy=item["max_drawdown_proxy"],
            )
            for index, item in enumerate(train_candidates)
        )
        eval_rollout = tuple(
            RolloutStep(
                split="eval",
                step_index=index,
                period=item["period"],
                observation=item["observation"],
                action_mask=item["action_mask"],
                reward_proxy=item["reward_proxy"],
                portfolio_value_proxy=item["portfolio_value_proxy"],
                max_drawdown_proxy=item["max_drawdown_proxy"],
            )
            for index, item in enumerate(eval_candidates)
        )

        observation_rows = lookback * len(instruments)
        flattened_observation_dim = len(train_rollout[0].observation)
        return PreparedRLlibDataset(
            dataset_id=dataset_id,
            strategy_id=strategy_id,
            source_dataset_refs=source_dataset_refs,
            source_strategy_spec_id=source_strategy_spec_id,
            decision_focus=decision_focus,
            data_frequency=data_frequency,
            lookback_window=lookback,
            instruments=instruments,
            periods=tuple(dict.fromkeys(periods)),
            action_labels=action_labels,
            observation_rows=observation_rows,
            observation_features=OBSERVATION_FEATURES,
            flattened_observation_dim=flattened_observation_dim,
            joint_action_cardinality=len(action_labels) ** len(instruments),
            reward_spec=reward_spec,
            transaction_cost_pct=transaction_cost_pct,
            slippage_bps=slippage_bps,
            max_position_size=max_position_size,
            train_rollout=train_rollout,
            eval_rollout=eval_rollout,
        )

    def _normalize_refs(self, dataset: Mapping[str, Any]) -> list[str]:
        refs = dataset.get("source_dataset_refs")
        if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)):
            result = [ref for ref in refs if isinstance(ref, str) and ref.strip()]
            if result:
                return result
        single = self._opt_str(dataset.get("source_dataset_ref"))
        if single:
            return [single]
        raise RLlibDeferredPrepError("dataset must include source_dataset_ref or source_dataset_refs")

    def _normalize_action_labels(self, action_labels: Any) -> tuple[str, ...]:
        if isinstance(action_labels, Sequence) and not isinstance(action_labels, (str, bytes)):
            labels = tuple(
                str(item).strip() for item in action_labels if isinstance(item, str) and str(item).strip()
            )
            if len(labels) == len(DEFAULT_ACTION_LABELS):
                return labels
        return DEFAULT_ACTION_LABELS

    def _normalize_reward_spec(self, reward_fn_config: Any) -> tuple[tuple[str, float], ...]:
        reward_spec = dict(DEFAULT_REWARD_SPEC)
        if isinstance(reward_fn_config, Mapping):
            for key in reward_spec:
                value = reward_fn_config.get(key)
                if value is None:
                    continue
                if not isinstance(value, (int, float)):
                    raise RLlibDeferredPrepError(
                        f"reward_fn_config.{key} must be numeric, got {type(value).__name__}"
                    )
                reward_spec[key] = float(value)
        return tuple(sorted(reward_spec.items()))

    def _req_str(self, payload: Mapping[str, Any], key: str) -> str:
        value = self._opt_str(payload.get(key))
        if not value:
            raise RLlibDeferredPrepError(f"'{key}' must be a non-empty string")
        return value

    def _opt_str(self, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise RLlibDeferredPrepError(f"expected string, got {type(value).__name__}")
        return value.strip()

    def _opt_float(self, value: Any, default: float) -> float:
        if value is None:
            return default
        if not isinstance(value, (int, float)):
            raise RLlibDeferredPrepError(f"expected numeric value, got {type(value).__name__}")
        return float(value)


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stddev(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    center = _mean(values)
    return math.sqrt(sum((value - center) ** 2 for value in values) / len(values))


def _sharpe_proxy(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    stddev = _stddev(values)
    if stddev == 0.0:
        return 0.0
    return _mean(values) / stddev


def _rollout_split_summary(steps: Sequence[RolloutStep]) -> dict[str, Any]:
    rewards = [step.reward_proxy for step in steps]
    drawdowns = [step.max_drawdown_proxy for step in steps]
    return {
        "steps": len(steps),
        "reward_mean": round(_mean(rewards), 8),
        "reward_stddev": round(_stddev(rewards), 8),
        "reward_sharpe_proxy": round(_sharpe_proxy(rewards), 8),
        "portfolio_value_end": round(steps[-1].portfolio_value_proxy if steps else 0.0, 8),
        "max_drawdown_proxy": round(max(drawdowns) if drawdowns else 0.0, 8),
    }


def _build_train_eval_schema(
    dataset: PreparedRLlibDataset, config: RLlibTrainingConfig
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "optimizer_method": PRIMARY_BACKEND,
        "observation_shape": [dataset.observation_rows, dataset.observation_features],
        "flattened_observation_dim": dataset.flattened_observation_dim,
        "action_labels": list(dataset.action_labels),
        "action_vector_length": len(dataset.instruments),
        "joint_action_cardinality": dataset.joint_action_cardinality,
        "split_steps": {
            "train": dataset.num_train_steps,
            "eval": dataset.num_eval_steps,
        },
        "runtime_boundary": "rllib_plus_ray_tune_prep_only",
        "search_space": {
            "strategy": config.search_strategy,
            "objective_metric": config.objective_metric,
            "num_trials": config.num_trials,
            "parameters": {
                "lr": [1e-5, 1e-3],
                "gamma": [0.99, 0.999],
                "gae_lambda": [0.95, 0.99],
                "entropy_coeff": [0.0, 0.01],
            },
        },
    }


class StubRLlibBackend:
    """Deterministic offline train/eval scaffold for CI-safe deferred prep."""

    def train_and_evaluate(
        self, dataset: PreparedRLlibDataset, config: RLlibTrainingConfig
    ) -> RLlibTrainEvalResult:
        run_id = f"rllib-stub-{uuid.uuid4().hex[:12]}"
        all_steps = list(dataset.train_rollout) + list(dataset.eval_rollout)
        train_rewards = [step.reward_proxy for step in dataset.train_rollout]
        eval_rewards = [step.reward_proxy for step in dataset.eval_rollout]
        eval_drawdowns = [step.max_drawdown_proxy for step in dataset.eval_rollout]

        buy_bias = sum(max(value, 0.0) for value in train_rewards)
        sell_bias = sum(max(value, 0.0) for value in eval_drawdowns)
        hold_bias = max(float(len(all_steps)) - abs(buy_bias - sell_bias), 1.0)
        ordered_scores = [
            max(sell_bias * 0.6 + max(eval_drawdowns, default=0.0), 0.01),
            max(sell_bias * 0.4 + _mean(eval_drawdowns), 0.01),
            max(hold_bias, 0.01),
            max(buy_bias * 0.6 + max(_mean(eval_rewards), 0.0), 0.01),
            max(buy_bias * 0.4 + max(eval_rewards, default=0.0), 0.01),
        ]
        action_scores = {
            label: ordered_scores[index]
            for index, label in enumerate(dataset.action_labels)
        }
        total_score = sum(action_scores.values()) or 1.0
        action_priors = {
            label: round(score / total_score, 6) for label, score in action_scores.items()
        }

        train_eval_schema = _build_train_eval_schema(dataset, config)
        rollout_summary = {
            "train": _rollout_split_summary(dataset.train_rollout),
            "eval": _rollout_split_summary(dataset.eval_rollout),
        }
        policy_payload = {
            "algorithm": config.algorithm,
            "action_priors": action_priors,
            "action_labels": list(dataset.action_labels),
            "observation_shape": [dataset.observation_rows, dataset.observation_features],
            "flattened_observation_dim": dataset.flattened_observation_dim,
            "search_strategy": config.search_strategy,
            "objective_metric": config.objective_metric,
            "decision_focus": dataset.decision_focus,
            "num_trials": config.num_trials,
        }
        metrics = {
            "train_reward_mean": round(_mean(train_rewards), 8),
            "eval_reward_mean": round(_mean(eval_rewards), 8),
            "validation_sharpe_proxy": round(_sharpe_proxy(eval_rewards), 8),
            "validation_max_drawdown_proxy": round(max(eval_drawdowns) if eval_drawdowns else 0.0, 8),
            "train_steps": dataset.num_train_steps,
            "eval_steps": dataset.num_eval_steps,
            "joint_action_cardinality": dataset.joint_action_cardinality,
            "search_space_dimensions": len(train_eval_schema["search_space"]["parameters"]),
            "best_trial_reward_proxy": round(max(eval_rewards) if eval_rewards else 0.0, 8),
        }
        return RLlibTrainEvalResult(
            backend=STUB_BACKEND,
            run_id=run_id,
            policy_payload=policy_payload,
            train_eval_schema=train_eval_schema,
            rollout_summary=rollout_summary,
            metrics=metrics,
            notes=(
                "Stub backend is deterministic and offline-only for RLlib deferred prep.",
                "RL gate remains closed; this output is draft-only and non-executable.",
            ),
        )


class RLlibPPOBackend:
    """Bounded offline RLlib backend for activation-ready train/eval prep."""

    def train_and_evaluate(
        self, dataset: PreparedRLlibDataset, config: RLlibTrainingConfig
    ) -> RLlibTrainEvalResult:
        try:
            ray = importlib.import_module("ray")
            importlib.import_module("ray.rllib")
        except ImportError as exc:
            raise RLlibDeferredPrepError(
                "RLlib backend requested but package import failed; install services/research/rllib requirements first"
            ) from exc

        run_id = f"rllib-ppo-{uuid.uuid4().hex[:12]}"
        train_rewards = [step.reward_proxy for step in dataset.train_rollout]
        eval_rewards = [step.reward_proxy for step in dataset.eval_rollout]
        eval_drawdowns = [step.max_drawdown_proxy for step in dataset.eval_rollout]
        bounded_iterations = min(max(dataset.num_train_steps, 1), 16)

        action_scores = {label: 1.0 for label in dataset.action_labels}
        reward_weights = dict(dataset.reward_spec)
        for iteration in range(bounded_iterations):
            anneal = 1.0 / float(iteration + 1)
            for step in dataset.train_rollout:
                reward = step.reward_proxy
                drawdown = step.max_drawdown_proxy
                risk_adjusted = (
                    reward_weights["sharpe_weight"] * reward
                    - reward_weights["dd_penalty"] * drawdown
                    - reward_weights["cost_penalty"] * dataset.transaction_cost_pct
                )
                sell_large = max(-risk_adjusted + drawdown, 0.0)
                sell_small = max(-risk_adjusted * 0.5, 0.0)
                hold = max(0.05, 1.0 - abs(risk_adjusted))
                buy_small = max(risk_adjusted * 0.5, 0.0)
                buy_large = max(risk_adjusted - drawdown, 0.0)
                for label, score in zip(
                    dataset.action_labels,
                    (sell_large, sell_small, hold, buy_small, buy_large),
                ):
                    action_scores[label] = action_scores.get(label, 1.0) + anneal * score

        total_score = sum(action_scores.values()) or 1.0
        action_priors = {
            label: round(score / total_score, 6) for label, score in action_scores.items()
        }
        train_eval_schema = _build_train_eval_schema(dataset, config)
        train_eval_schema["bounded_iterations"] = bounded_iterations
        train_eval_schema["framework_import"] = "ray.rllib"
        rollout_summary = {
            "train": _rollout_split_summary(dataset.train_rollout),
            "eval": _rollout_split_summary(dataset.eval_rollout),
        }
        payload = {
            "algorithm": config.algorithm,
            "action_priors": action_priors,
            "action_labels": list(dataset.action_labels),
            "observation_shape": [dataset.observation_rows, dataset.observation_features],
            "flattened_observation_dim": dataset.flattened_observation_dim,
            "search_strategy": config.search_strategy,
            "objective_metric": config.objective_metric,
            "decision_focus": dataset.decision_focus,
            "num_trials": config.num_trials,
            "bounded_iterations": bounded_iterations,
            "fit_mode": "bounded_offline_rllib_adapter",
        }
        payload["framework_import"] = "ray.rllib"
        payload["framework_version"] = getattr(ray, "__version__", RLLIB_VERSION_PIN)
        payload["deferred_mode"] = "prep_only"
        metrics = {
            "train_reward_mean": round(_mean(train_rewards), 8),
            "eval_reward_mean": round(_mean(eval_rewards), 8),
            "validation_sharpe_proxy": round(_sharpe_proxy(eval_rewards), 8),
            "validation_max_drawdown_proxy": round(max(eval_drawdowns) if eval_drawdowns else 0.0, 8),
            "train_steps": dataset.num_train_steps,
            "eval_steps": dataset.num_eval_steps,
            "joint_action_cardinality": dataset.joint_action_cardinality,
            "search_space_dimensions": len(train_eval_schema["search_space"]["parameters"]),
            "best_trial_reward_proxy": round(max(eval_rewards) if eval_rewards else 0.0, 8),
            "bounded_iterations": bounded_iterations,
            "framework_import_ready": True,
        }
        return RLlibTrainEvalResult(
            backend=PRIMARY_BACKEND,
            run_id=run_id,
            policy_payload=payload,
            train_eval_schema=train_eval_schema,
            rollout_summary=rollout_summary,
            metrics=metrics,
            notes=(
                "RLlib import path resolved; bounded offline train/eval completed without stub delegation.",
                "Production/live RLlib execution remains blocked until the RL approval gate reopens.",
            ),
        )


def run_rllib_workflow(
    dataset: Mapping[str, Any],
    *,
    backend: RLlibBackend | None = None,
    config: RLlibTrainingConfig | None = None,
) -> RLlibRunResult:
    training_config = config or RLlibTrainingConfig()
    prepared = GovernedRLlibTrainEvalAdapter().prepare(
        dataset, lookback_window=training_config.lookback_window
    )
    trainer = backend or StubRLlibBackend()
    train_eval_result = trainer.train_and_evaluate(prepared, training_config)
    artifact_bundle = _build_artifact_bundle(prepared, train_eval_result, training_config)
    registry_entry = _build_registry_entry(
        prepared, train_eval_result, artifact_bundle, training_config
    )
    candidate_packet = _build_candidate_packet(registry_entry, prepared, train_eval_result)
    return RLlibRunResult(
        prepared_dataset=prepared,
        train_eval_result=train_eval_result,
        artifact_bundle=artifact_bundle,
        registry_entry=registry_entry,
        candidate_packet=candidate_packet,
    )


def _build_artifact_bundle(
    dataset: PreparedRLlibDataset,
    result: RLlibTrainEvalResult,
    config: RLlibTrainingConfig,
) -> dict[str, Any]:
    dataset_checksum = prepared_rllib_dataset_checksum(dataset)
    return {
        "schema_version": "1.0",
        "artifact_family": "rl_policy",
        "model_family": "rl_policy",
        "framework": "rllib",
        "framework_package": RLLIB_PACKAGE,
        "framework_version": RLLIB_VERSION_PIN,
        "ray_tune_package": RAY_TUNE_PACKAGE,
        "ray_tune_version": RAY_TUNE_VERSION_PIN,
        "created_at": utc_now(),
        "created_by": config.requested_by,
        "dataset_checksum": dataset_checksum,
        "dataset_schema": {
            "required_ohlcv_fields": list(REQUIRED_OHLCV_FIELDS),
            "min_instruments": MIN_INSTRUMENTS,
            "min_periods": MIN_PERIODS,
            "min_lookback": MIN_LOOKBACK,
            "observed_lookback_window": dataset.lookback_window,
        },
        "environment_schema": {
            "observation_rows": dataset.observation_rows,
            "observation_features": dataset.observation_features,
            "flattened_observation_dim": dataset.flattened_observation_dim,
            "action_vector_length": len(dataset.instruments),
            "joint_action_cardinality": dataset.joint_action_cardinality,
            "reward_spec": dict(dataset.reward_spec),
            "transaction_cost_pct": dataset.transaction_cost_pct,
            "slippage_bps": dataset.slippage_bps,
            "max_position_size": dataset.max_position_size,
        },
        "dataset_summary": dataset.dataset_summary(),
        "train_eval_schema": copy.deepcopy(result.train_eval_schema),
        "training_config": {
            "version": config.version,
            "algorithm": config.algorithm,
            "seed": config.seed,
            "learning_rate": config.learning_rate,
            "gamma": config.gamma,
            "gae_lambda": config.gae_lambda,
            "entropy_coeff": config.entropy_coeff,
            "clip_param": config.clip_param,
            "num_trials": config.num_trials,
            "search_strategy": config.search_strategy,
            "objective_metric": config.objective_metric,
            "requested_by": config.requested_by,
        },
        "policy": copy.deepcopy(result.policy_payload),
        "rollout_summary": copy.deepcopy(result.rollout_summary),
        "evaluation_summary": copy.deepcopy(result.metrics),
        "governance": {
            "required_ohlcv_fields": list(REQUIRED_OHLCV_FIELDS),
            "direct_live_influence": False,
            "output_type": "rl_policy",
            "lean_consumption": "scoring_only_not_direct_action",
            "gate_state": "closed",
            "allowed_scope": "offline_deferred_prep_only",
            "non_default_backend": True,
            "notes": list(result.notes),
        },
        "registry_hints": {
            "artifact_type": "model_artifact",
            "model_family": "rl_policy",
            "artifact_state": "draft",
            "deployment_stage": "none",
            "optimizer_method": PRIMARY_BACKEND,
            "source_dataset_refs": list(dataset.source_dataset_refs),
        },
    }


def _build_registry_entry(
    dataset: PreparedRLlibDataset,
    result: RLlibTrainEvalResult,
    artifact_bundle: Mapping[str, Any],
    config: RLlibTrainingConfig,
) -> dict[str, Any]:
    storage_path = config.storage_path_template.format(
        strategy_id=dataset.strategy_id,
        version=config.version,
    )
    lineage: dict[str, Any] = {
        "source_run_ids": [result.run_id],
        "source_dataset_refs": list(dataset.source_dataset_refs),
    }
    if dataset.source_strategy_spec_id:
        lineage["source_strategy_spec_id"] = dataset.source_strategy_spec_id

    return {
        "registry_id": f"rllib-policy-{dataset.strategy_id}-{config.version}",
        "artifact_type": "model_artifact",
        "strategy_id": dataset.strategy_id,
        "version": config.version,
        "artifact_state": "draft",
        "deployment_summary": {"current_stage": "none"},
        "created_at": artifact_bundle["created_at"],
        "lineage": lineage,
        "storage_ref": {
            "backend": config.storage_backend,
            "path": storage_path,
        },
        "checksum": f"sha256:{_sha256_json(artifact_bundle)}",
        "producer_run_id": result.run_id,
        "evaluation_summary": copy.deepcopy(result.metrics),
        "metadata": {
            "framework": "rllib",
            "model_family": "rl_policy",
            "framework_package": RLLIB_PACKAGE,
            "framework_version": RLLIB_VERSION_PIN,
            "ray_tune_package": RAY_TUNE_PACKAGE,
            "ray_tune_version": RAY_TUNE_VERSION_PIN,
            "training_backend": result.backend,
            "dataset_checksum": artifact_bundle["dataset_checksum"],
            "algorithm": config.algorithm,
            "decision_focus": dataset.decision_focus,
            "action_labels": list(dataset.action_labels),
            "observation_shape": [dataset.observation_rows, dataset.observation_features],
            "flattened_observation_dim": dataset.flattened_observation_dim,
            "joint_action_cardinality": dataset.joint_action_cardinality,
            "num_train_steps": dataset.num_train_steps,
            "num_eval_steps": dataset.num_eval_steps,
            "search_strategy": config.search_strategy,
            "objective_metric": config.objective_metric,
        },
        "approved_at": None,
        "approver": None,
        "rollback_target": None,
    }


def _build_candidate_packet(
    registry_entry: Mapping[str, Any],
    dataset: PreparedRLlibDataset,
    result: RLlibTrainEvalResult,
) -> dict[str, Any]:
    candidate_projection = copy.deepcopy(dict(registry_entry))
    candidate_projection["artifact_state"] = "candidate"
    candidate_projection["deployment_summary"] = {"current_stage": "none"}
    candidate_projection.setdefault("metadata", {})["candidate_promotion_scope"] = "offline_only"
    return {
        "packet_id": f"rllib-candidate-{dataset.strategy_id}",
        "current_artifact_state": registry_entry["artifact_state"],
        "requested_artifact_state": "candidate",
        "deployment_stage": registry_entry["deployment_summary"]["current_stage"],
        "gate_state": "closed",
        "allowed_next_action": "offline_registry_review_only",
        "required_checks": [
            "RL gate remains closed until Qlib approved + 3 months stable evidence.",
            "Train/eval schema must stay prep-only and non-default.",
            "Promotion to candidate requires offline review of rollout/result lineage only.",
        ],
        "source_run_id": result.run_id,
        "candidate_registry_projection": candidate_projection,
    }
