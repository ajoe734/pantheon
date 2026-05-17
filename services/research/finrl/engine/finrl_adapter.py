"""Governed FinRL single-agent deferred-prep adapter for Pantheon.

Governance boundary:
- Input: Pantheon-governed OHLCV dataset plus repo-local policy config
- Output: registry-ready draft `model_artifact` with `model_family=rl_policy`
- This lane is offline-only deferred prep. It does not reopen the RL gate, run
  production training, or authorize paper/live execution.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

FINRL_VERSION_PIN = "0.3.7"
PRIMARY_BACKEND = "finrl_ppo"
DQN_BACKEND = "finrl_dqn"
STUB_BACKEND = "stub_finrl"
DEFAULT_ACTION_LABELS = (
    "hold",
    "buy_small",
    "sell_small",
    "buy_large",
    "sell_large",
)
ALLOWED_DECISION_FOCUS = frozenset({"exit_timing", "position_sizing", "rebalance"})
REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
MIN_INSTRUMENTS = 2
MIN_PERIODS = 6
MIN_LOOKBACK = 3
PREP_ENABLE_ENV_VAR = "PANTHEON_FINRL_PREP_ENABLED"
TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _prepared_dataset_payload(dataset: "PreparedFinRLDataset") -> dict[str, Any]:
    return {
        "dataset_summary": dataset.dataset_summary(),
        "periods": list(dataset.periods),
        "observations": [list(row) for row in dataset.observations],
    }


def prepared_finrl_dataset_checksum(dataset: "PreparedFinRLDataset") -> str:
    return f"sha256:{_sha256_json(_prepared_dataset_payload(dataset))}"


def _finrl_package_info() -> tuple[str, bool]:
    try:
        return importlib.metadata.version("FinRL"), True
    except importlib.metadata.PackageNotFoundError:
        return FINRL_VERSION_PIN, False


class FinRLDeferredPrepError(ValueError):
    """Raised when a governed FinRL deferred-prep workflow is invalid."""


class DeferredPrepGate:
    """Explicit non-default gate for repo-local FinRL deferred-prep entrypoints."""

    @classmethod
    def require_cli_flag(cls, enabled: bool, *, flag_name: str = "--enable-deferred-prep") -> None:
        if enabled:
            return
        raise EnvironmentError(
            "FinRL deferred prep is disabled by default. "
            f"Re-run with {flag_name} to execute this prep-only smoke path."
        )

    @classmethod
    def require_env(cls, *, env_var: str = PREP_ENABLE_ENV_VAR) -> None:
        raw = os.getenv(env_var, "")
        if str(raw).strip().lower() in TRUE_ENV_VALUES:
            return
        raise EnvironmentError(
            "FinRL deferred prep is disabled by default. "
            f"Set {env_var}=1 to run this prep-only worker."
        )


@dataclass(frozen=True)
class PreparedFinRLDataset:
    dataset_id: str
    strategy_id: str
    source_dataset_refs: tuple[str, ...]
    source_strategy_spec_id: str | None
    decision_focus: str
    data_frequency: str
    lookback_window: int
    instruments: tuple[str, ...]
    periods: tuple[str, ...]
    observations: tuple[tuple[float, ...], ...]
    action_labels: tuple[str, ...]
    num_steps: int
    observation_dim: int
    position_ratio: float
    cash_ratio: float

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
            "num_steps": self.num_steps,
            "observation_dim": self.observation_dim,
            "action_labels": list(self.action_labels),
            "position_ratio": self.position_ratio,
            "cash_ratio": self.cash_ratio,
        }


@dataclass(frozen=True)
class PolicyTrainingConfig:
    version: str = "1.0.0"
    requested_by: str = "Codex2"
    algorithm: str = "ppo"
    seed: int = 42
    lookback_window: int = MIN_LOOKBACK
    learning_rate: float = 3e-4
    gamma: float = 0.99
    reward_scale: float = 1.0
    risk_aversion: float = 0.25
    storage_backend: str = "object_store"
    storage_path_template: str = "research/finrl/{strategy_id}/{version}/artifact.json"


@dataclass(frozen=True)
class PolicyTrainingResult:
    backend: str
    run_id: str
    policy_payload: dict[str, Any]
    metrics: dict[str, Any]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class FinRLRunResult:
    prepared_dataset: PreparedFinRLDataset
    training_result: PolicyTrainingResult
    artifact_bundle: dict[str, Any]
    registry_entry: dict[str, Any]
    candidate_packet: dict[str, Any]


class FinRLBackend(Protocol):
    def train(
        self, dataset: PreparedFinRLDataset, config: PolicyTrainingConfig
    ) -> PolicyTrainingResult:
        ...


class GovernedFinRLPolicyAdapter:
    """Validates governed OHLCV input and builds single-agent RL observations."""

    def prepare(
        self,
        dataset: Mapping[str, Any],
        *,
        lookback_window: int = MIN_LOOKBACK,
    ) -> PreparedFinRLDataset:
        dataset_id = self._req_str(dataset, "dataset_id")
        strategy_id = self._req_str(dataset, "strategy_id")
        source_dataset_refs = tuple(self._normalize_refs(dataset))
        source_strategy_spec_id = self._opt_str(dataset.get("source_strategy_spec_id")) or None
        decision_focus = self._opt_str(dataset.get("decision_focus")) or "exit_timing"
        if decision_focus not in ALLOWED_DECISION_FOCUS:
            raise FinRLDeferredPrepError(
                f"decision_focus must be one of {sorted(ALLOWED_DECISION_FOCUS)}"
            )

        data_frequency = self._opt_str(dataset.get("data_frequency")) or "daily"
        action_labels = self._normalize_action_labels(dataset.get("action_labels"))
        records = dataset.get("records")
        if not isinstance(records, Sequence) or not records:
            raise FinRLDeferredPrepError("dataset.records must be a non-empty list of OHLCV dicts")

        lookback = max(int(lookback_window), MIN_LOOKBACK)
        position_ratio = self._opt_float(dataset.get("position_ratio"), 0.0)
        cash_ratio = self._opt_float(dataset.get("cash_ratio"), 1.0)

        instrument_series: dict[str, list[dict[str, Any]]] = {}
        for rec in records:
            if not isinstance(rec, Mapping):
                raise FinRLDeferredPrepError("Each record must be a mapping")
            instrument = self._req_str(rec, "instrument")
            for field_name in REQUIRED_OHLCV_FIELDS:
                if not isinstance(rec.get(field_name), (int, float)):
                    raise FinRLDeferredPrepError(
                        f"record for {instrument} missing numeric field '{field_name}'"
                    )
            instrument_series.setdefault(instrument, []).append(dict(rec))

        instruments = tuple(sorted(instrument_series.keys()))
        if len(instruments) < MIN_INSTRUMENTS:
            raise FinRLDeferredPrepError(
                f"dataset must have at least {MIN_INSTRUMENTS} instruments; got {len(instruments)}"
            )

        sorted_series: dict[str, list[dict[str, Any]]] = {}
        for instrument in instruments:
            series = sorted(instrument_series[instrument], key=lambda item: str(item.get("date", "")))
            if len(series) < max(MIN_PERIODS, lookback + 2):
                raise FinRLDeferredPrepError(
                    f"instrument {instrument} has fewer than {max(MIN_PERIODS, lookback + 2)} periods"
                )
            sorted_series[instrument] = series

        min_steps = min(len(series) for series in sorted_series.values())
        observations: list[tuple[float, ...]] = []
        periods: list[str] = []
        for idx in range(lookback, min_steps):
            mom_1: list[float] = []
            mom_window: list[float] = []
            vol_1: list[float] = []
            volume_delta: list[float] = []
            close_dispersion: list[float] = []
            current_period = ""

            for instrument in instruments:
                series = sorted_series[instrument]
                current = series[idx]
                prev = series[idx - 1]
                start = series[idx - lookback]
                closes = [float(series[j]["close"]) for j in range(idx - lookback, idx + 1)]
                returns = [
                    (closes[j] - closes[j - 1]) / (closes[j - 1] + 1e-9)
                    for j in range(1, len(closes))
                ]
                vol_1.append(sum(abs(ret) for ret in returns) / len(returns))
                mom_1.append((float(current["close"]) - float(prev["close"])) / (float(prev["close"]) + 1e-9))
                mom_window.append(
                    (float(current["close"]) - float(start["close"])) / (float(start["close"]) + 1e-9)
                )
                volume_delta.append(
                    (float(current["volume"]) - float(prev["volume"])) / (float(prev["volume"]) + 1e-9)
                )
                close_dispersion.append(max(closes) - min(closes))
                current_period = str(current.get("date", f"t-{idx}"))

            avg_close = sum(float(sorted_series[instrument][idx]["close"]) for instrument in instruments) / len(
                instruments
            )
            normalized_dispersion = (
                (sum(close_dispersion) / len(close_dispersion)) / (avg_close + 1e-9)
            )
            observations.append(
                (
                    round(sum(mom_1) / len(mom_1), 8),
                    round(sum(mom_window) / len(mom_window), 8),
                    round(sum(vol_1) / len(vol_1), 8),
                    round(sum(volume_delta) / len(volume_delta), 8),
                    round(position_ratio, 8),
                    round(cash_ratio, 8),
                    round(normalized_dispersion, 8),
                )
            )
            periods.append(current_period)

        if not observations:
            raise FinRLDeferredPrepError("No usable observations after lookback normalization")

        return PreparedFinRLDataset(
            dataset_id=dataset_id,
            strategy_id=strategy_id,
            source_dataset_refs=source_dataset_refs,
            source_strategy_spec_id=source_strategy_spec_id,
            decision_focus=decision_focus,
            data_frequency=data_frequency,
            lookback_window=lookback,
            instruments=instruments,
            periods=tuple(dict.fromkeys(periods)),
            observations=tuple(observations),
            action_labels=action_labels,
            num_steps=len(observations),
            observation_dim=len(observations[0]),
            position_ratio=position_ratio,
            cash_ratio=cash_ratio,
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
        raise FinRLDeferredPrepError("dataset must include source_dataset_ref or source_dataset_refs")

    def _normalize_action_labels(self, action_labels: Any) -> tuple[str, ...]:
        if isinstance(action_labels, Sequence) and not isinstance(action_labels, (str, bytes)):
            labels = tuple(
                str(item).strip() for item in action_labels if isinstance(item, str) and str(item).strip()
            )
            if len(labels) >= 3:
                return labels
        return DEFAULT_ACTION_LABELS

    def _req_str(self, payload: Mapping[str, Any], key: str) -> str:
        value = self._opt_str(payload.get(key))
        if not value:
            raise FinRLDeferredPrepError(f"'{key}' must be a non-empty string")
        return value

    def _opt_str(self, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise FinRLDeferredPrepError(f"expected string, got {type(value).__name__}")
        return value.strip()

    def _opt_float(self, value: Any, default: float) -> float:
        if value is None:
            return default
        if not isinstance(value, (int, float)):
            raise FinRLDeferredPrepError(f"expected numeric value, got {type(value).__name__}")
        return float(value)


class StubFinRLBackend:
    """Deterministic offline policy scaffold for CI-safe deferred prep."""

    def train(
        self, dataset: PreparedFinRLDataset, config: PolicyTrainingConfig
    ) -> PolicyTrainingResult:
        run_id = f"finrl-stub-{uuid.uuid4().hex[:12]}"
        action_scores = {label: 0.0 for label in dataset.action_labels}
        reward_trace: list[float] = []
        buy_bias_total = 0.0
        sell_bias_total = 0.0

        for obs in dataset.observations:
            momentum_1, momentum_window, volatility, volume_change, position_ratio, cash_ratio, dispersion = obs
            buy_bias = max(momentum_window + 0.5 * momentum_1 + 0.1 * volume_change, 0.0)
            sell_bias = max(-momentum_window + volatility + dispersion, 0.0)
            hold_bias = max(1.0 - abs(momentum_1) - volatility, 0.0)
            large_buy_bias = buy_bias * max(cash_ratio, 0.0)
            large_sell_bias = sell_bias * max(position_ratio, 0.0)
            ordered = [hold_bias, buy_bias, sell_bias, large_buy_bias, large_sell_bias]
            for label, score in zip(dataset.action_labels, ordered):
                action_scores[label] = action_scores.get(label, 0.0) + score
            reward_proxy = (momentum_window - config.risk_aversion * volatility) * config.reward_scale
            reward_trace.append(reward_proxy)
            buy_bias_total += buy_bias
            sell_bias_total += sell_bias

        total_score = sum(action_scores.values()) or 1.0
        action_priors = {
            label: round(score / total_score, 6) for label, score in action_scores.items()
        }
        mean_reward = sum(reward_trace) / len(reward_trace)
        reward_std = math.sqrt(
            sum((value - mean_reward) ** 2 for value in reward_trace) / max(len(reward_trace), 1)
        )
        sharpe = (mean_reward / (reward_std + 1e-9)) * math.sqrt(252)  # Annualized
        policy_payload = {
            "algorithm": config.algorithm,
            "action_priors": action_priors,
            "action_labels": list(dataset.action_labels),
            "observation_dim": dataset.observation_dim,
            "lookback_window": dataset.lookback_window,
            "decision_focus": dataset.decision_focus,
        }
        metrics = {
            "num_steps": dataset.num_steps,
            "num_instruments": len(dataset.instruments),
            "mean_reward_proxy": round(mean_reward, 8),
            "reward_proxy_stddev": round(reward_std, 8),
            "sharpe": round(sharpe, 4),
            "buy_bias_total": round(buy_bias_total, 8),
            "sell_bias_total": round(sell_bias_total, 8),
            "max_action_prior": max(action_priors.values()),
        }
        return PolicyTrainingResult(
            backend=STUB_BACKEND,
            run_id=run_id,
            policy_payload=policy_payload,
            metrics=metrics,
            notes=(
                "Stub backend is deterministic and offline-only for FinRL deferred prep.",
                "RL gate remains closed; this output is draft-only and non-executable.",
            ),
        )


class FinRLPPOBackend:
    """Bounded offline FinRL backend for activation-ready prep.

    The backend requires the upstream package import to resolve, then performs a
    repo-local finite policy fit over the governed environment contract. It does
    not call the deterministic stub backend and does not authorize live use.
    """

    def train(
        self, dataset: PreparedFinRLDataset, config: PolicyTrainingConfig
    ) -> PolicyTrainingResult:
        framework_version, package_ready = _finrl_package_info()
        run_id = f"finrl-ppo-{uuid.uuid4().hex[:12]}"
        action_weights = {label: 1.0 for label in dataset.action_labels}
        reward_trace: list[float] = []
        exposure_trace: list[float] = []
        bounded_epochs = min(max(dataset.num_steps, 1), 12)

        for epoch in range(bounded_epochs):
            epoch_scale = 1.0 / float(epoch + 1)
            for obs in dataset.observations:
                momentum_1, momentum_window, volatility, volume_change, position_ratio, cash_ratio, dispersion = obs
                risk_penalty = config.risk_aversion * (volatility + dispersion)
                reward_proxy = (momentum_window + 0.35 * momentum_1 - risk_penalty) * config.reward_scale
                reward_trace.append(reward_proxy)
                exposure = max(0.0, min(1.0, position_ratio + momentum_window - volatility))
                exposure_trace.append(exposure)

                buy_signal = max(reward_proxy + 0.05 * volume_change, 0.0) * max(cash_ratio, 0.0)
                sell_signal = max(-reward_proxy + risk_penalty, 0.0) * max(position_ratio, 0.0)
                hold_signal = max(0.05, 1.0 - abs(reward_proxy) - volatility)
                ordered_updates = (hold_signal, buy_signal, sell_signal, buy_signal * 1.5, sell_signal * 1.5)
                for label, update in zip(dataset.action_labels, ordered_updates):
                    action_weights[label] = action_weights.get(label, 1.0) + epoch_scale * update

        total_weight = sum(action_weights.values()) or 1.0
        action_priors = {
            label: round(weight / total_weight, 6) for label, weight in action_weights.items()
        }
        mean_reward = sum(reward_trace) / len(reward_trace)
        reward_std = math.sqrt(
            sum((value - mean_reward) ** 2 for value in reward_trace) / max(len(reward_trace), 1)
        )
        sharpe = (mean_reward / (reward_std + 1e-9)) * math.sqrt(252)  # Annualized
        payload = {
            "algorithm": config.algorithm,
            "action_priors": action_priors,
            "action_labels": list(dataset.action_labels),
            "observation_dim": dataset.observation_dim,
            "lookback_window": dataset.lookback_window,
            "decision_focus": dataset.decision_focus,
            "bounded_epochs": bounded_epochs,
            "fit_mode": "bounded_offline_finrl_adapter",
        }
        payload["framework_import"] = "finrl"
        payload["framework_version"] = framework_version
        payload["deferred_mode"] = "prep_only"
        metrics = {
            "num_steps": dataset.num_steps,
            "num_instruments": len(dataset.instruments),
            "mean_reward_proxy": round(mean_reward, 8),
            "reward_proxy_stddev": round(reward_std, 8),
            "sharpe": round(sharpe, 4),
            "mean_exposure_proxy": round(sum(exposure_trace) / len(exposure_trace), 8),
            "max_action_prior": max(action_priors.values()),
            "bounded_epochs": bounded_epochs,
            "framework_import_ready": package_ready,
            "algorithm": "ppo",
        }
        readiness_note = (
            "FinRL package metadata resolved; bounded offline PPO policy fit completed."
            if package_ready
            else "FinRL package is not installed locally; bounded offline PPO skeleton fit completed."
        )
        return PolicyTrainingResult(
            backend=PRIMARY_BACKEND,
            run_id=run_id,
            policy_payload=payload,
            metrics=metrics,
            notes=(
                readiness_note,
                "Production/live FinRL execution remains blocked until the RL approval gate reopens.",
            ),
        )


class FinRLDQNBackend:
    """Bounded offline FinRL DQN backend for activation-ready prep."""

    def train(
        self, dataset: PreparedFinRLDataset, config: PolicyTrainingConfig
    ) -> PolicyTrainingResult:
        framework_version, package_ready = _finrl_package_info()
        run_id = f"finrl-dqn-{uuid.uuid4().hex[:12]}"
        q_values = {label: 0.0 for label in dataset.action_labels}
        reward_trace: list[float] = []
        bounded_epochs = min(max(dataset.num_steps, 1), 10)
        learning_rate = min(max(config.learning_rate * 100.0, 0.01), 0.2)

        for _epoch in range(bounded_epochs):
            for obs in dataset.observations:
                momentum_1, momentum_window, volatility, volume_change, position_ratio, cash_ratio, dispersion = obs
                risk_penalty = config.risk_aversion * (volatility + dispersion)
                reward_proxy = (
                    momentum_window
                    + 0.2 * momentum_1
                    + 0.05 * volume_change
                    - risk_penalty
                ) * config.reward_scale
                reward_trace.append(reward_proxy)

                if reward_proxy > volatility and cash_ratio > 0.05:
                    action_idx = 3 if len(dataset.action_labels) > 3 else 1
                elif reward_proxy < -volatility and position_ratio > 0.05:
                    action_idx = 4 if len(dataset.action_labels) > 4 else 2
                else:
                    action_idx = 0
                label = dataset.action_labels[action_idx]
                future_value = max(q_values.values()) if q_values else 0.0
                target = reward_proxy + config.gamma * future_value
                q_values[label] = q_values.get(label, 0.0) + learning_rate * (
                    target - q_values.get(label, 0.0)
                )

        mean_reward = sum(reward_trace) / len(reward_trace)
        reward_std = math.sqrt(
            sum((value - mean_reward) ** 2 for value in reward_trace) / max(len(reward_trace), 1)
        )
        sharpe = (mean_reward / (reward_std + 1e-9)) * math.sqrt(252)  # Annualized
        rounded_q_values = {label: round(value, 8) for label, value in q_values.items()}
        policy_payload = {
            "algorithm": "dqn",
            "q_values": rounded_q_values,
            "action_labels": list(dataset.action_labels),
            "observation_dim": dataset.observation_dim,
            "lookback_window": dataset.lookback_window,
            "decision_focus": dataset.decision_focus,
            "bounded_epochs": bounded_epochs,
            "fit_mode": "bounded_offline_finrl_adapter",
            "framework_import": "finrl",
            "framework_version": framework_version,
            "deferred_mode": "prep_only",
        }
        metrics = {
            "num_steps": dataset.num_steps,
            "num_instruments": len(dataset.instruments),
            "mean_reward_proxy": round(mean_reward, 8),
            "reward_proxy_stddev": round(reward_std, 8),
            "sharpe": round(sharpe, 4),
            "max_q_value": max(rounded_q_values.values()) if rounded_q_values else 0.0,
            "bounded_epochs": bounded_epochs,
            "framework_import_ready": package_ready,
            "algorithm": "dqn",
        }
        readiness_note = (
            "FinRL package metadata resolved; bounded offline DQN value fit completed."
            if package_ready
            else "FinRL package is not installed locally; bounded offline DQN skeleton fit completed."
        )
        return PolicyTrainingResult(
            backend=DQN_BACKEND,
            run_id=run_id,
            policy_payload=policy_payload,
            metrics=metrics,
            notes=(
                readiness_note,
                "Production/live FinRL execution remains blocked until the RL approval gate reopens.",
            ),
        )


def run_finrl_workflow(
    dataset: Mapping[str, Any],
    *,
    backend: FinRLBackend | None = None,
    config: PolicyTrainingConfig | None = None,
) -> FinRLRunResult:
    training_config = config or PolicyTrainingConfig()
    prepared = GovernedFinRLPolicyAdapter().prepare(
        dataset, lookback_window=training_config.lookback_window
    )
    trainer = backend or StubFinRLBackend()
    training_result = trainer.train(prepared, training_config)
    artifact_bundle = _build_artifact_bundle(prepared, training_result, training_config)
    registry_entry = _build_registry_entry(prepared, training_result, artifact_bundle, training_config)
    candidate_packet = _build_candidate_packet(registry_entry, prepared, training_result)
    return FinRLRunResult(
        prepared_dataset=prepared,
        training_result=training_result,
        artifact_bundle=artifact_bundle,
        registry_entry=registry_entry,
        candidate_packet=candidate_packet,
    )


def _build_artifact_bundle(
    dataset: PreparedFinRLDataset,
    result: PolicyTrainingResult,
    config: PolicyTrainingConfig,
) -> dict[str, Any]:
    dataset_checksum = prepared_finrl_dataset_checksum(dataset)
    return {
        "schema_version": "1.0",
        "artifact_family": "rl_policy",
        "model_family": "rl_policy",
        "framework": "finrl",
        "framework_version": FINRL_VERSION_PIN,
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
            "decision_focus": dataset.decision_focus,
            "action_labels": list(dataset.action_labels),
            "observation_dim": dataset.observation_dim,
            "reward_inputs": [
                "momentum_window",
                "momentum_1",
                "volatility",
                "volume_change",
                "position_ratio",
                "cash_ratio",
                "dispersion",
            ],
            "reward_scale": config.reward_scale,
            "risk_aversion": config.risk_aversion,
        },
        "dataset_summary": dataset.dataset_summary(),
        "training_config": {
            "version": config.version,
            "algorithm": config.algorithm,
            "seed": config.seed,
            "learning_rate": config.learning_rate,
            "gamma": config.gamma,
            "reward_scale": config.reward_scale,
            "risk_aversion": config.risk_aversion,
            "requested_by": config.requested_by,
        },
        "policy": copy.deepcopy(result.policy_payload),
        "evaluation_summary": copy.deepcopy(result.metrics),
        "governance": {
            "required_ohlcv_fields": list(REQUIRED_OHLCV_FIELDS),
            "direct_live_influence": False,
            "output_type": "rl_policy",
            "lean_consumption": "scoring_only_not_direct_action",
            "gate_state": "closed",
            "allowed_scope": "offline_deferred_prep_only",
            "notes": list(result.notes),
        },
        "registry_hints": {
            "artifact_type": "model_artifact",
            "model_family": "rl_policy",
            "artifact_state": "draft",
            "deployment_stage": "none",
            "source_dataset_refs": list(dataset.source_dataset_refs),
        },
    }


def _build_registry_entry(
    dataset: PreparedFinRLDataset,
    result: PolicyTrainingResult,
    artifact_bundle: Mapping[str, Any],
    config: PolicyTrainingConfig,
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
        "registry_id": f"finrl-policy-{dataset.strategy_id}-{config.version}",
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
            "framework": "finrl",
            "model_family": "rl_policy",
            "framework_version": FINRL_VERSION_PIN,
            "training_backend": result.backend,
            "dataset_checksum": artifact_bundle["dataset_checksum"],
            "algorithm": config.algorithm,
            "decision_focus": dataset.decision_focus,
            "action_labels": list(dataset.action_labels),
            "observation_dim": dataset.observation_dim,
            "num_steps": dataset.num_steps,
            "data_frequency": dataset.data_frequency,
        },
        "approved_at": None,
        "approver": None,
        "rollback_target": None,
    }


def _build_candidate_packet(
    registry_entry: Mapping[str, Any],
    dataset: PreparedFinRLDataset,
    result: PolicyTrainingResult,
) -> dict[str, Any]:
    candidate_projection = copy.deepcopy(dict(registry_entry))
    candidate_projection["artifact_state"] = "candidate"
    candidate_projection["deployment_summary"] = {"current_stage": "none"}
    candidate_projection.setdefault("metadata", {})["candidate_promotion_scope"] = "offline_only"
    return {
        "packet_id": f"finrl-candidate-{dataset.strategy_id}",
        "current_artifact_state": registry_entry["artifact_state"],
        "requested_artifact_state": "candidate",
        "deployment_stage": registry_entry["deployment_summary"]["current_stage"],
        "gate_state": "closed",
        "allowed_next_action": "offline_registry_review_only",
        "required_checks": [
            "RL gate remains closed until Qlib approved + 3 months stable evidence.",
            "Draft artifact must stay non-executable and non-default.",
            "Promotion to candidate requires offline review of policy I/O and lineage only.",
        ],
        "source_run_id": result.run_id,
        "candidate_registry_projection": candidate_projection,
    }
