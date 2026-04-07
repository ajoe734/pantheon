from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

IMITATION_VERSION_PIN = "1.0.1"
PRIMARY_BACKEND = "imitation_bc"
STUB_BACKEND = "stub_bc"
ALLOWED_ACTOR_ROLES = frozenset({"operator", "approver"})
ALLOWED_PROMOTION_STATES = frozenset({"candidate", "paper"})
ELIGIBLE_DECISIONS = frozenset({"approve", "edit"})


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


class ImitationWorkflowError(ValueError):
    """Raised when a governed imitation workflow cannot be built safely."""


@dataclass(frozen=True)
class PreparedTrajectory:
    trajectory_id: str
    actor_id: str
    actor_role: str
    decision: str
    target: dict[str, Any]
    observations: tuple[tuple[float, ...], ...]
    action_labels: tuple[str, ...]
    action_ids: tuple[int, ...]
    rewards: tuple[float | None, ...]
    source_feedback_ids: tuple[str, ...]

    @property
    def length(self) -> int:
        return len(self.action_ids)


@dataclass(frozen=True)
class PreparedDataset:
    dataset_id: str
    strategy_id: str
    source_dataset_refs: tuple[str, ...]
    source_strategy_spec_id: str | None
    observation_dim: int
    action_labels: tuple[str, ...]
    trajectories: tuple[PreparedTrajectory, ...]
    filtered_trajectories: dict[str, str] = field(default_factory=dict)

    @property
    def num_trajectories(self) -> int:
        return len(self.trajectories)

    @property
    def num_transitions(self) -> int:
        return sum(trajectory.length for trajectory in self.trajectories)

    def flattened_observations(self) -> list[tuple[float, ...]]:
        rows: list[tuple[float, ...]] = []
        for trajectory in self.trajectories:
            rows.extend(trajectory.observations)
        return rows

    def flattened_action_ids(self) -> list[int]:
        rows: list[int] = []
        for trajectory in self.trajectories:
            rows.extend(trajectory.action_ids)
        return rows

    def flattened_rewards(self) -> list[float]:
        rewards: list[float] = []
        for trajectory in self.trajectories:
            rewards.extend(reward for reward in trajectory.rewards if reward is not None)
        return rewards

    def event_ids(self) -> list[str]:
        identifiers: list[str] = []
        for trajectory in self.trajectories:
            identifiers.extend(trajectory.source_feedback_ids)
        return identifiers

    def dataset_summary(self) -> dict[str, Any]:
        rewards = self.flattened_rewards()
        summary = {
            "dataset_id": self.dataset_id,
            "strategy_id": self.strategy_id,
            "source_dataset_refs": list(self.source_dataset_refs),
            "source_strategy_spec_id": self.source_strategy_spec_id,
            "observation_dim": self.observation_dim,
            "num_trajectories": self.num_trajectories,
            "num_transitions": self.num_transitions,
            "action_labels": list(self.action_labels),
            "included_trajectory_ids": [trajectory.trajectory_id for trajectory in self.trajectories],
            "filtered_trajectories": copy.deepcopy(self.filtered_trajectories),
            "feedback_event_ids": self.event_ids(),
        }
        if rewards:
            summary["mean_reward"] = sum(rewards) / len(rewards)
        return summary


@dataclass(frozen=True)
class TrainingConfig:
    version: str = "0.1.0"
    requested_by: str = "Codex"
    epochs: int = 1
    seed: int = 7
    lifecycle_state: str = "draft"
    storage_backend: str = "object_store"
    storage_path_template: str = "learning/imitation/{strategy_id}/{version}/artifact_bundle.json"


@dataclass(frozen=True)
class BackendTrainingResult:
    backend: str
    run_id: str
    policy_payload: dict[str, Any]
    metrics: dict[str, Any]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImitationRunResult:
    prepared_dataset: PreparedDataset
    training_result: BackendTrainingResult
    artifact_bundle: dict[str, Any]
    registry_entry: dict[str, Any]


class BehaviorCloningBackend(Protocol):
    def train(self, dataset: PreparedDataset, config: TrainingConfig) -> BackendTrainingResult:
        ...


class GovernedTrajectoryAdapter:
    """Filters governed trader trajectories into BC-ready demonstrations."""

    def prepare(self, dataset: Mapping[str, Any]) -> PreparedDataset:
        dataset_id = self._require_string(dataset, "dataset_id")
        strategy_id = self._require_string(dataset, "strategy_id")
        source_dataset_refs = self._normalize_dataset_refs(dataset)
        source_strategy_spec_id = self._optional_string(dataset.get("source_strategy_spec_id"))
        sessions = dataset.get("sessions")
        if not isinstance(sessions, Sequence) or not sessions:
            raise ImitationWorkflowError("dataset.sessions must contain at least one governed trajectory.")

        raw_trajectories: list[dict[str, Any]] = []
        filtered: dict[str, str] = {}
        observation_dim: int | None = None
        action_order: list[str] = []

        for session in sessions:
            if not isinstance(session, Mapping):
                raise ImitationWorkflowError("Every session must be a mapping.")
            trajectory_id = self._require_string(session, "trajectory_id")
            decision = self._normalize_decision(session.get("decision") or session.get("event_type"))
            actor_role = self._require_string(session, "actor_role")
            target = session.get("target")
            steps = session.get("steps")

            if actor_role not in ALLOWED_ACTOR_ROLES:
                filtered[trajectory_id] = f"actor_role={actor_role} is outside governed imitation roles"
                continue
            if decision not in ELIGIBLE_DECISIONS:
                filtered[trajectory_id] = f"decision={decision or 'missing'} is not eligible for BC training"
                continue
            if not isinstance(target, Mapping):
                filtered[trajectory_id] = "missing governed target linkage"
                continue
            if target.get("strategy_id") != strategy_id:
                filtered[trajectory_id] = "target.strategy_id does not match dataset.strategy_id"
                continue
            promotion_state = target.get("promotion_state")
            if promotion_state not in ALLOWED_PROMOTION_STATES:
                filtered[trajectory_id] = (
                    f"promotion_state={promotion_state or 'missing'} is outside governed training states"
                )
                continue
            if not isinstance(steps, Sequence) or not steps:
                filtered[trajectory_id] = "no trajectory steps present"
                continue

            observations: list[tuple[float, ...]] = []
            actions: list[str] = []
            rewards: list[float | None] = []
            feedback_ids: list[str] = []
            invalid_reason: str | None = None

            for step in steps:
                if not isinstance(step, Mapping):
                    invalid_reason = "step is not a mapping"
                    break
                observation = self._normalize_observation(step.get("observation"))
                if observation is None:
                    invalid_reason = "step missing numeric observation vector"
                    break
                if observation_dim is None:
                    observation_dim = len(observation)
                elif len(observation) != observation_dim:
                    invalid_reason = "observation dimension mismatch across trajectories"
                    break
                action_label = self._optional_string(step.get("action"))
                if not action_label:
                    invalid_reason = "step missing action label"
                    break
                if action_label not in action_order:
                    action_order.append(action_label)
                observations.append(observation)
                actions.append(action_label)
                rewards.append(self._normalize_optional_float(step.get("reward")))
                feedback_ids.append(self._optional_string(step.get("feedback_event_id")) or f"{trajectory_id}:step{len(actions)}")

            if invalid_reason is not None:
                filtered[trajectory_id] = invalid_reason
                continue

            raw_trajectories.append(
                {
                    "trajectory_id": trajectory_id,
                    "actor_id": self._require_string(session, "actor_id"),
                    "actor_role": actor_role,
                    "decision": decision,
                    "target": dict(target),
                    "observations": observations,
                    "actions": actions,
                    "rewards": rewards,
                    "feedback_ids": feedback_ids,
                }
            )

        if not raw_trajectories:
            raise ImitationWorkflowError(
                "No governed trajectories remain after filtering. Check actor_role, decision, and promotion_state."
            )
        if observation_dim is None:
            raise ImitationWorkflowError("Unable to determine observation dimensionality from governed trajectories.")

        action_to_id = {label: index for index, label in enumerate(action_order)}
        prepared = tuple(
            PreparedTrajectory(
                trajectory_id=item["trajectory_id"],
                actor_id=item["actor_id"],
                actor_role=item["actor_role"],
                decision=item["decision"],
                target=item["target"],
                observations=tuple(item["observations"]),
                action_labels=tuple(item["actions"]),
                action_ids=tuple(action_to_id[label] for label in item["actions"]),
                rewards=tuple(item["rewards"]),
                source_feedback_ids=tuple(item["feedback_ids"]),
            )
            for item in raw_trajectories
        )
        return PreparedDataset(
            dataset_id=dataset_id,
            strategy_id=strategy_id,
            source_dataset_refs=tuple(source_dataset_refs),
            source_strategy_spec_id=source_strategy_spec_id,
            observation_dim=observation_dim,
            action_labels=tuple(action_order),
            trajectories=prepared,
            filtered_trajectories=filtered,
        )

    def _normalize_dataset_refs(self, dataset: Mapping[str, Any]) -> list[str]:
        refs = dataset.get("source_dataset_refs")
        if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)):
            normalized = [self._require_string({"value": ref}, "value") for ref in refs]
            if normalized:
                return normalized
        single = self._optional_string(dataset.get("source_dataset_ref"))
        if single:
            return [single]
        raise ImitationWorkflowError("dataset must include source_dataset_ref or source_dataset_refs")

    def _normalize_decision(self, value: Any) -> str:
        decision = self._optional_string(value)
        if not decision:
            return ""
        aliases = {
            "approved": "approve",
            "approval": "approve",
            "edited": "edit",
            "rejected": "reject",
        }
        return aliases.get(decision, decision)

    def _normalize_observation(self, value: Any) -> tuple[float, ...] | None:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return None
        normalized: list[float] = []
        for item in value:
            if not isinstance(item, (int, float)):
                return None
            normalized.append(float(item))
        if not normalized:
            return None
        return tuple(normalized)

    def _normalize_optional_float(self, value: Any) -> float | None:
        if value is None:
            return None
        if not isinstance(value, (int, float)):
            raise ImitationWorkflowError("reward must be numeric when provided")
        return float(value)

    def _require_string(self, payload: Mapping[str, Any], key: str) -> str:
        value = self._optional_string(payload.get(key))
        if not value:
            raise ImitationWorkflowError(f"{key} must be a non-empty string")
        return value

    def _optional_string(self, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ImitationWorkflowError("expected a string value in governed trajectory payload")
        trimmed = value.strip()
        return trimmed


class StubBehaviorCloningBackend:
    """Small nearest-centroid classifier for local smoke tests and deterministic CI."""

    def train(self, dataset: PreparedDataset, config: TrainingConfig) -> BackendTrainingResult:
        observations = dataset.flattened_observations()
        action_ids = dataset.flattened_action_ids()
        run_id = f"imit-{uuid.uuid4().hex[:12]}"
        per_action: dict[int, list[tuple[float, ...]]] = {index: [] for index in range(len(dataset.action_labels))}
        for observation, action_id in zip(observations, action_ids):
            per_action[action_id].append(observation)

        centroids = {
            action_id: self._centroid(rows, dataset.observation_dim)
            for action_id, rows in per_action.items()
            if rows
        }
        predictions = [self._predict(observation, centroids) for observation in observations]
        correct = sum(1 for predicted, actual in zip(predictions, action_ids) if predicted == actual)
        accuracy = correct / len(action_ids)
        rewards = dataset.flattened_rewards()
        action_counts = {dataset.action_labels[action_id]: len(rows) for action_id, rows in per_action.items() if rows}
        metrics: dict[str, Any] = {
            "training_accuracy": accuracy,
            "num_transitions": dataset.num_transitions,
            "num_trajectories": dataset.num_trajectories,
            "action_coverage_ratio": len(action_counts) / len(dataset.action_labels),
            "epochs": config.epochs,
        }
        if rewards:
            metrics["mean_reward"] = sum(rewards) / len(rewards)

        policy_payload = {
            "predictor": "nearest_centroid",
            "observation_dim": dataset.observation_dim,
            "action_labels": list(dataset.action_labels),
            "action_centroids": {
                dataset.action_labels[action_id]: centroid
                for action_id, centroid in centroids.items()
            },
            "action_counts": action_counts,
        }
        return BackendTrainingResult(
            backend=STUB_BACKEND,
            run_id=run_id,
            policy_payload=policy_payload,
            metrics=metrics,
            notes=("Stub backend is intended for governed smoke tests and packaging validation.",),
        )

    def _centroid(self, rows: Sequence[tuple[float, ...]], observation_dim: int) -> list[float]:
        return [
            sum(row[index] for row in rows) / len(rows)
            for index in range(observation_dim)
        ]

    def _predict(self, observation: tuple[float, ...], centroids: Mapping[int, Sequence[float]]) -> int:
        distances = {
            action_id: math.dist(observation, centroid)
            for action_id, centroid in centroids.items()
        }
        return min(distances, key=distances.get)


class ImitationBehaviorCloningBackend:
    """Optional upstream backend that trains via HumanCompatibleAI/imitation."""

    def train(self, dataset: PreparedDataset, config: TrainingConfig) -> BackendTrainingResult:
        try:
            import gymnasium as gym  # type: ignore
            import numpy as np  # type: ignore
            from imitation.algorithms import bc  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only when optional dep missing
            raise ImitationWorkflowError(
                "imitation backend is unavailable. Install services/learning/imitation/requirements.txt first."
            ) from exc

        observations = np.asarray(dataset.flattened_observations(), dtype=np.float32)
        actions = np.asarray(dataset.flattened_action_ids(), dtype=np.int64)
        trainer = bc.BC(
            observation_space=gym.spaces.Box(
                low=-np.inf,
                high=np.inf,
                shape=(dataset.observation_dim,),
                dtype=np.float32,
            ),
            action_space=gym.spaces.Discrete(len(dataset.action_labels)),
            demonstrations=[{"obs": observations, "acts": actions}],
            rng=np.random.default_rng(config.seed),
        )
        trainer.train(n_epochs=config.epochs)
        policy_payload = {
            "framework": "imitation",
            "algorithm": "behavior_cloning",
            "framework_version": IMITATION_VERSION_PIN,
            "policy_class": trainer.policy.__class__.__name__,
            "observation_dim": dataset.observation_dim,
            "action_labels": list(dataset.action_labels),
            "serialization_note": (
                "Serialize trainer.policy in the dedicated worker runtime before final registry submission."
            ),
        }
        metrics = {
            "num_transitions": dataset.num_transitions,
            "num_trajectories": dataset.num_trajectories,
            "action_coverage_ratio": len(set(dataset.flattened_action_ids())) / len(dataset.action_labels),
            "epochs": config.epochs,
        }
        return BackendTrainingResult(
            backend=PRIMARY_BACKEND,
            run_id=f"imit-{uuid.uuid4().hex[:12]}",
            policy_payload=policy_payload,
            metrics=metrics,
            notes=("Upstream imitation BC completed. Persist trainer.policy separately for production weights.",),
        )


def run_imitation_workflow(
    dataset: Mapping[str, Any],
    *,
    backend: BehaviorCloningBackend | None = None,
    config: TrainingConfig | None = None,
) -> ImitationRunResult:
    prepared = GovernedTrajectoryAdapter().prepare(dataset)
    training_config = config or TrainingConfig()
    trainer = backend or StubBehaviorCloningBackend()
    training_result = trainer.train(prepared, training_config)
    artifact_bundle = build_artifact_bundle(prepared, training_result, training_config)
    registry_entry = build_registry_entry(prepared, training_result, artifact_bundle, training_config)
    return ImitationRunResult(
        prepared_dataset=prepared,
        training_result=training_result,
        artifact_bundle=artifact_bundle,
        registry_entry=registry_entry,
    )


def build_artifact_bundle(
    dataset: PreparedDataset,
    training_result: BackendTrainingResult,
    config: TrainingConfig,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "artifact_family": "imitation_policy",
        "algorithm": "behavior_cloning",
        "framework": "imitation",
        "framework_version": IMITATION_VERSION_PIN,
        "created_at": utc_now(),
        "created_by": config.requested_by,
        "dataset_summary": dataset.dataset_summary(),
        "training_config": {
            "version": config.version,
            "epochs": config.epochs,
            "seed": config.seed,
            "requested_by": config.requested_by,
        },
        "policy": copy.deepcopy(training_result.policy_payload),
        "evaluation_summary": copy.deepcopy(training_result.metrics),
        "governance": {
            "eligible_actor_roles": sorted(ALLOWED_ACTOR_ROLES),
            "eligible_promotion_states": sorted(ALLOWED_PROMOTION_STATES),
            "direct_live_influence": False,
            "filtered_trajectories": copy.deepcopy(dataset.filtered_trajectories),
            "notes": list(training_result.notes),
        },
        "registry_hints": {
            "artifact_type": "model_artifact",
            "model_family": "imitation_policy",
            "initial_lifecycle_state": config.lifecycle_state,
            "source_dataset_refs": list(dataset.source_dataset_refs),
        },
    }


def build_registry_entry(
    dataset: PreparedDataset,
    training_result: BackendTrainingResult,
    artifact_bundle: Mapping[str, Any],
    config: TrainingConfig,
) -> dict[str, Any]:
    storage_path = config.storage_path_template.format(
        strategy_id=dataset.strategy_id,
        version=config.version,
    )
    lineage: dict[str, Any] = {
        "source_run_ids": [training_result.run_id],
        "source_dataset_refs": list(dataset.source_dataset_refs),
    }
    if dataset.source_strategy_spec_id:
        lineage["source_strategy_spec_id"] = dataset.source_strategy_spec_id

    return {
        "registry_id": f"reg-{dataset.strategy_id}-imitation-{config.version}",
        "artifact_type": "model_artifact",
        "strategy_id": dataset.strategy_id,
        "version": config.version,
        "lifecycle_state": config.lifecycle_state,
        "lineage": lineage,
        "storage_ref": {
            "backend": config.storage_backend,
            "path": storage_path,
        },
        "checksum": f"sha256:{_sha256_json(artifact_bundle)}",
        "producer_run_id": training_result.run_id,
        "evaluation_summary": copy.deepcopy(training_result.metrics),
        "metadata": {
            "model_family": "imitation_policy",
            "algorithm": "behavior_cloning",
            "training_backend": training_result.backend,
            "framework": "imitation",
            "framework_version": IMITATION_VERSION_PIN,
            "observation_dim": dataset.observation_dim,
            "action_labels": list(dataset.action_labels),
            "source_feedback_event_ids": dataset.event_ids(),
            "governance_filtered_trajectories": copy.deepcopy(dataset.filtered_trajectories),
        },
    }
