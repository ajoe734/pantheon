"""Standalone CPU behavior cloning baseline for IMT-003 datasets.

IMT-005 intentionally keeps this trainer self-contained in the research
imitation package.  It consumes the governed dataset payload emitted by
dataset_builder.py and returns a registry-ready behavior_policy artifact dict;
it does not write to the registry, launch runtime deployment, or require GPU
or optional ML frameworks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ALLOWED_ACTOR_ROLES: frozenset[str] = frozenset({"operator", "approver"})
ALLOWED_PROMOTION_STATES: frozenset[str] = frozenset({"candidate", "paper"})
ELIGIBLE_DECISIONS: frozenset[str] = frozenset({"approve", "edit"})
ARTIFACT_TYPE = "behavior_policy"
MODEL_FAMILY = "imitation_policy"
ALGORITHM = "behavior_cloning"
TRAINER_NAME = "softmax_regression_cpu"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


class BCTrainerError(ValueError):
    """Raised when a BC artifact cannot be trained from the supplied dataset."""


@dataclass(frozen=True)
class BCTrainerConfig:
    """Configuration for the CPU-only BC baseline."""

    version: str = "0.1.0"
    requested_by: str = "Codex"
    epochs: int = 25
    learning_rate: float = 0.2
    l2: float = 0.0
    seed: int = 7
    artifact_state: str = "draft"
    storage_backend: str = "inline"
    storage_path_template: str = "research/imitation/{strategy_id}/{version}/behavior_policy.json"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | "BCTrainerConfig" | None) -> "BCTrainerConfig":
        if value is None:
            return cls()
        if isinstance(value, cls):
            value._validate()
            return value
        if not isinstance(value, Mapping):
            raise BCTrainerError("model_config must be a mapping or BCTrainerConfig")
        allowed = set(cls.__dataclass_fields__.keys())
        unknown = sorted(set(value.keys()) - allowed)
        if unknown:
            raise BCTrainerError(f"unsupported model_config field(s): {', '.join(unknown)}")
        config = cls(**dict(value))
        config._validate()
        return config

    def _validate(self) -> None:
        if not _optional_text(self.version):
            raise BCTrainerError("model_config.version must be non-empty")
        if not _optional_text(self.requested_by):
            raise BCTrainerError("model_config.requested_by must be non-empty")
        if not isinstance(self.epochs, int) or isinstance(self.epochs, bool) or self.epochs <= 0:
            raise BCTrainerError("model_config.epochs must be a positive integer")
        if not isinstance(self.learning_rate, (int, float)) or self.learning_rate <= 0:
            raise BCTrainerError("model_config.learning_rate must be positive")
        if not isinstance(self.l2, (int, float)) or self.l2 < 0:
            raise BCTrainerError("model_config.l2 must be non-negative")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise BCTrainerError("model_config.seed must be an integer")
        if self.artifact_state not in {"draft", "candidate"}:
            raise BCTrainerError("model_config.artifact_state must be draft or candidate")
        if not _optional_text(self.storage_backend):
            raise BCTrainerError("model_config.storage_backend must be non-empty")
        if not _optional_text(self.storage_path_template):
            raise BCTrainerError("model_config.storage_path_template must be non-empty")


@dataclass(frozen=True)
class PreparedBCDataset:
    dataset_id: str
    strategy_id: str
    source_dataset_refs: tuple[str, ...]
    source_strategy_spec_id: str | None
    observation_dim: int
    action_labels: tuple[str, ...]
    observations: tuple[tuple[float, ...], ...]
    action_ids: tuple[int, ...]
    included_trajectory_ids: tuple[str, ...]
    feedback_event_ids: tuple[str, ...]
    filtered_trajectories: dict[str, str] = field(default_factory=dict)

    @property
    def num_transitions(self) -> int:
        return len(self.action_ids)

    @property
    def num_trajectories(self) -> int:
        return len(self.included_trajectory_ids)

    def dataset_summary(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "strategy_id": self.strategy_id,
            "source_dataset_refs": list(self.source_dataset_refs),
            "source_strategy_spec_id": self.source_strategy_spec_id,
            "observation_dim": self.observation_dim,
            "num_trajectories": self.num_trajectories,
            "num_transitions": self.num_transitions,
            "action_labels": list(self.action_labels),
            "included_trajectory_ids": list(self.included_trajectory_ids),
            "filtered_trajectories": copy.deepcopy(self.filtered_trajectories),
            "feedback_event_ids": list(self.feedback_event_ids),
        }


@dataclass(frozen=True)
class SoftmaxTrainingResult:
    run_id: str
    weights: tuple[tuple[float, ...], ...]
    bias: tuple[float, ...]
    loss_history: tuple[float, ...]
    training_accuracy: float
    action_counts: dict[str, int]

    @property
    def initial_loss(self) -> float:
        return self.loss_history[0]

    @property
    def final_loss(self) -> float:
        return self.loss_history[-1]


def train(
    dataset_ref: Mapping[str, Any] | str | Path,
    model_config: Mapping[str, Any] | BCTrainerConfig | None = None,
) -> dict[str, Any]:
    """Train a CPU-only behavior cloning baseline and return an artifact dict.

    Args:
        dataset_ref: IMT-003 dataset payload mapping, or a JSON file path
            containing that payload.
        model_config: Optional BCTrainerConfig fields.

    Returns:
        A behavior_policy artifact dict with a sha256 checksum and lineage that
        references the source dataset.
    """

    config = BCTrainerConfig.from_mapping(model_config)
    dataset = prepare_dataset(dataset_ref)
    training = _train_softmax(dataset, config)
    return build_behavior_policy_artifact(dataset, training, config)


def prepare_dataset(dataset_ref: Mapping[str, Any] | str | Path) -> PreparedBCDataset:
    """Normalize and governance-filter an IMT-003 dataset payload."""

    dataset = _load_dataset_ref(dataset_ref)
    dataset_id = _require_text(dataset.get("dataset_id"), "dataset_id")
    strategy_id = _require_text(dataset.get("strategy_id"), "strategy_id")
    source_dataset_refs = tuple(_text_list(dataset.get("source_dataset_refs")))
    if not source_dataset_refs:
        raise BCTrainerError("dataset.source_dataset_refs must contain at least one ref")
    source_strategy_spec_id = _optional_text(dataset.get("source_strategy_spec_id"))
    sessions = dataset.get("sessions")
    if not isinstance(sessions, Sequence) or isinstance(sessions, (str, bytes)) or not sessions:
        raise BCTrainerError("dataset.sessions must contain at least one governed trajectory")

    observations: list[tuple[float, ...]] = []
    action_labels: list[str] = []
    raw_action_labels: list[str] = []
    included_trajectory_ids: list[str] = []
    feedback_event_ids: list[str] = []
    filtered: dict[str, str] = {}
    observation_dim: int | None = None

    for index, raw_session in enumerate(sessions):
        if not isinstance(raw_session, Mapping):
            filtered[f"session-{index}"] = "session is not a mapping"
            continue
        trajectory_id = _optional_text(raw_session.get("trajectory_id")) or f"session-{index}"
        reason = _session_filter_reason(raw_session, strategy_id)
        if reason:
            filtered[trajectory_id] = reason
            continue

        steps = raw_session.get("steps")
        if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)) or not steps:
            filtered[trajectory_id] = "no trajectory steps present"
            continue

        session_observations: list[tuple[float, ...]] = []
        session_actions: list[str] = []
        session_feedback: list[str] = []
        invalid_reason = ""

        for step_index, step in enumerate(steps):
            if not isinstance(step, Mapping):
                invalid_reason = f"step {step_index} is not a mapping"
                break
            observation = _normalize_observation(step.get("observation"))
            if observation is None:
                invalid_reason = f"step {step_index} missing numeric observation vector"
                break
            if observation_dim is None:
                observation_dim = len(observation)
            elif len(observation) != observation_dim:
                invalid_reason = f"step {step_index} observation dimension mismatch"
                break
            action = _optional_text(step.get("action"))
            if not action:
                invalid_reason = f"step {step_index} missing action label"
                break
            feedback_id = _optional_text(step.get("feedback_event_id")) or f"{trajectory_id}:step{step_index}"
            session_observations.append(observation)
            session_actions.append(action)
            session_feedback.append(feedback_id)

        if invalid_reason:
            filtered[trajectory_id] = invalid_reason
            continue

        included_trajectory_ids.append(trajectory_id)
        observations.extend(session_observations)
        raw_action_labels.extend(session_actions)
        feedback_event_ids.extend(session_feedback)
        for action in session_actions:
            if action not in action_labels:
                action_labels.append(action)

    if not observations or observation_dim is None:
        raise BCTrainerError("No governed BC transitions remain after filtering")
    if len(action_labels) < 2:
        raise BCTrainerError("BC baseline requires at least two observed action labels")

    action_to_id = {label: idx for idx, label in enumerate(action_labels)}
    action_ids = tuple(action_to_id[action] for action in raw_action_labels)
    return PreparedBCDataset(
        dataset_id=dataset_id,
        strategy_id=strategy_id,
        source_dataset_refs=source_dataset_refs,
        source_strategy_spec_id=source_strategy_spec_id,
        observation_dim=observation_dim,
        action_labels=tuple(action_labels),
        observations=tuple(observations),
        action_ids=action_ids,
        included_trajectory_ids=tuple(included_trajectory_ids),
        feedback_event_ids=tuple(feedback_event_ids),
        filtered_trajectories=filtered,
    )


def build_behavior_policy_artifact(
    dataset: PreparedBCDataset,
    training: SoftmaxTrainingResult,
    config: BCTrainerConfig,
) -> dict[str, Any]:
    """Build the registry-ready behavior_policy artifact envelope."""

    lineage_refs = _dedupe([dataset.dataset_id, *dataset.source_dataset_refs])
    lineage: dict[str, Any] = {
        "source_dataset_ref": dataset.dataset_id,
        "source_dataset_refs": lineage_refs,
    }
    if dataset.source_strategy_spec_id:
        lineage["source_strategy_spec_id"] = dataset.source_strategy_spec_id

    storage_path = config.storage_path_template.format(
        strategy_id=dataset.strategy_id,
        version=config.version,
        dataset_id=dataset.dataset_id,
    )
    artifact: dict[str, Any] = {
        "schema_version": "1.0",
        "artifact_type": ARTIFACT_TYPE,
        "artifact_state": config.artifact_state,
        "strategy_id": dataset.strategy_id,
        "version": config.version,
        "artifact_family": MODEL_FAMILY,
        "model_family": MODEL_FAMILY,
        "algorithm": ALGORITHM,
        "trainer": TRAINER_NAME,
        "created_at": utc_now(),
        "created_by": config.requested_by,
        "lineage": lineage,
        "dataset_summary": dataset.dataset_summary(),
        "training": {
            "run_id": training.run_id,
            "device": "cpu",
            "epochs": config.epochs,
            "learning_rate": float(config.learning_rate),
            "l2": float(config.l2),
            "seed": config.seed,
            "loss_history": list(training.loss_history),
            "initial_loss": training.initial_loss,
            "final_loss": training.final_loss,
            "loss_decreased": training.final_loss < training.initial_loss,
            "training_accuracy": training.training_accuracy,
            "num_transitions": dataset.num_transitions,
            "num_trajectories": dataset.num_trajectories,
        },
        "policy": {
            "policy_type": "linear_softmax",
            "observation_dim": dataset.observation_dim,
            "action_labels": list(dataset.action_labels),
            "weights": [list(row) for row in training.weights],
            "bias": list(training.bias),
            "action_counts": copy.deepcopy(training.action_counts),
        },
        "storage_ref": {
            "backend": config.storage_backend,
            "path": storage_path,
        },
        "registry_hints": {
            "artifact_type": ARTIFACT_TYPE,
            "model_family": MODEL_FAMILY,
            "algorithm": ALGORITHM,
            "initial_artifact_state": config.artifact_state,
            "source_dataset_ref": dataset.dataset_id,
            "source_dataset_refs": lineage_refs,
        },
        "governance": {
            "research_only": True,
            "direct_live_influence": False,
            "eligible_actor_roles": sorted(ALLOWED_ACTOR_ROLES),
            "eligible_promotion_states": sorted(ALLOWED_PROMOTION_STATES),
            "eligible_decisions": sorted(ELIGIBLE_DECISIONS),
            "filtered_trajectories": copy.deepcopy(dataset.filtered_trajectories),
        },
    }
    artifact["checksum"] = artifact_checksum(artifact)
    return artifact


def artifact_checksum(artifact: Mapping[str, Any]) -> str:
    """Return the stable sha256 checksum for an artifact, excluding checksum."""

    payload = copy.deepcopy(dict(artifact))
    payload.pop("checksum", None)
    return f"sha256:{_sha256_json(payload)}"


def verify_artifact_checksum(artifact: Mapping[str, Any]) -> bool:
    return artifact.get("checksum") == artifact_checksum(artifact)


def _train_softmax(dataset: PreparedBCDataset, config: BCTrainerConfig) -> SoftmaxTrainingResult:
    rng = random.Random(config.seed)
    num_actions = len(dataset.action_labels)
    weights = [
        [rng.uniform(-0.01, 0.01) for _ in range(dataset.observation_dim)]
        for _ in range(num_actions)
    ]
    bias = [0.0 for _ in range(num_actions)]
    loss_history = [_cross_entropy_loss(dataset, weights, bias, float(config.l2))]

    for _epoch in range(config.epochs):
        grad_w = [[0.0 for _ in range(dataset.observation_dim)] for _ in range(num_actions)]
        grad_b = [0.0 for _ in range(num_actions)]
        n = float(dataset.num_transitions)

        for observation, target_action in zip(dataset.observations, dataset.action_ids):
            probabilities = _predict_proba(observation, weights, bias)
            for action_id, probability in enumerate(probabilities):
                delta = probability - (1.0 if action_id == target_action else 0.0)
                grad_b[action_id] += delta / n
                for dim, value in enumerate(observation):
                    grad_w[action_id][dim] += (delta * value) / n

        for action_id in range(num_actions):
            for dim in range(dataset.observation_dim):
                grad_w[action_id][dim] += float(config.l2) * weights[action_id][dim]
                weights[action_id][dim] -= float(config.learning_rate) * grad_w[action_id][dim]
            bias[action_id] -= float(config.learning_rate) * grad_b[action_id]

        loss_history.append(_cross_entropy_loss(dataset, weights, bias, float(config.l2)))

    predictions = [
        _argmax(_predict_proba(observation, weights, bias))
        for observation in dataset.observations
    ]
    correct = sum(1 for predicted, actual in zip(predictions, dataset.action_ids) if predicted == actual)
    action_counts = {
        label: sum(1 for action_id in dataset.action_ids if action_id == idx)
        for idx, label in enumerate(dataset.action_labels)
    }
    return SoftmaxTrainingResult(
        run_id=f"bc-{uuid.uuid4().hex[:12]}",
        weights=tuple(tuple(value for value in row) for row in weights),
        bias=tuple(bias),
        loss_history=tuple(loss_history),
        training_accuracy=correct / dataset.num_transitions,
        action_counts=action_counts,
    )


def _cross_entropy_loss(
    dataset: PreparedBCDataset,
    weights: Sequence[Sequence[float]],
    bias: Sequence[float],
    l2: float,
) -> float:
    loss = 0.0
    epsilon = 1e-12
    for observation, target_action in zip(dataset.observations, dataset.action_ids):
        probabilities = _predict_proba(observation, weights, bias)
        loss -= math.log(max(probabilities[target_action], epsilon))
    loss /= dataset.num_transitions
    if l2:
        penalty = sum(weight * weight for row in weights for weight in row)
        loss += 0.5 * l2 * penalty
    return loss


def _predict_proba(
    observation: Sequence[float],
    weights: Sequence[Sequence[float]],
    bias: Sequence[float],
) -> list[float]:
    logits = [
        sum(weight * value for weight, value in zip(row, observation)) + bias_value
        for row, bias_value in zip(weights, bias)
    ]
    max_logit = max(logits)
    exps = [math.exp(logit - max_logit) for logit in logits]
    denom = sum(exps)
    return [value / denom for value in exps]


def _argmax(values: Sequence[float]) -> int:
    return max(range(len(values)), key=lambda idx: values[idx])


def _load_dataset_ref(dataset_ref: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(dataset_ref, Mapping):
        return dict(dataset_ref)
    if isinstance(dataset_ref, (str, Path)):
        path = Path(dataset_ref)
        if not path.exists():
            raise BCTrainerError("dataset_ref string must point to an existing JSON dataset file")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise BCTrainerError("dataset_ref JSON file must contain an object")
        return dict(payload)
    raise BCTrainerError("dataset_ref must be an IMT-003 dataset mapping or JSON file path")


def _session_filter_reason(session: Mapping[str, Any], strategy_id: str) -> str:
    actor_role = _optional_text(session.get("actor_role"))
    if actor_role not in ALLOWED_ACTOR_ROLES:
        return f"actor_role={actor_role or 'missing'} is not a governed role"
    decision = _normalize_decision(session.get("decision") or session.get("event_type"))
    if decision not in ELIGIBLE_DECISIONS:
        return f"decision={decision or 'missing'} is not eligible for BC training"
    target = session.get("target")
    if not isinstance(target, Mapping):
        return "missing governed target linkage"
    target_strategy_id = _optional_text(target.get("strategy_id"))
    if target_strategy_id != strategy_id:
        return "target.strategy_id does not match dataset.strategy_id"
    promotion_state = _optional_text(target.get("promotion_state"))
    if promotion_state not in ALLOWED_PROMOTION_STATES:
        return f"promotion_state={promotion_state or 'missing'} is outside governed training states"
    return ""


def _normalize_decision(value: Any) -> str:
    decision = _optional_text(value)
    aliases = {
        "approved": "approve",
        "approval": "approve",
        "edited": "edit",
        "rejected": "reject",
    }
    return aliases.get(decision or "", decision or "")


def _normalize_observation(value: Any) -> tuple[float, ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    normalized: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            return None
        normalized.append(float(item))
    return tuple(normalized) if normalized else None


def _require_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise BCTrainerError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = value
    else:
        items = [value]
    return _dedupe(str(item).strip() for item in items if str(item).strip())


def _dedupe(values: Sequence[str] | Any) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result
