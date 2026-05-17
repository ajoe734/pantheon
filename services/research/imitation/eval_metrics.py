"""Evaluation metrics for governed imitation behavior policies.

IMT-006 keeps these metrics independent from the behavior-cloning trainer.  The
entry point accepts registry/artifact-style policy payloads plus governed
trajectory dictionaries and returns a JSON-serializable evaluation_result
payload.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


EPSILON = 1e-12
EVALUATOR_ID = "imitation_eval_metrics_v1"


class ImitationEvaluationError(ValueError):
    """Raised when imitation evaluation inputs are malformed or incomplete."""


@dataclass(frozen=True)
class _EvalStep:
    trajectory_id: str
    step_index: int
    observation: tuple[float, ...] | None
    expert_action: str
    expert_reward: float
    reward_by_action: dict[str, float]
    step_id: str | None = None
    feedback_event_id: str | None = None


def evaluate(
    behavior_policy_ref: Mapping[str, Any],
    eval_trajectories: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return action-match, return-gap, and KL metrics for an imitation policy.

    `action_match_rate` is the mean probability assigned to the expert action.
    For deterministic policies this is ordinary accuracy; for stochastic
    policies it is expected action-match accuracy.  `return_gap` is
    expert_return - policy_return.  When counterfactual action rewards are not
    present, policy_return uses the logged expert reward weighted by the expert
    action probability.
    """
    if not isinstance(behavior_policy_ref, Mapping):
        raise ImitationEvaluationError("behavior_policy_ref must be a mapping")

    steps = _extract_eval_steps(eval_trajectories)
    policy = _resolve_policy_payload(behavior_policy_ref)
    action_labels = _initial_action_labels(policy, steps)
    if not action_labels:
        raise ImitationEvaluationError("at least one action label is required for evaluation")

    expert_return = 0.0
    policy_return = 0.0
    expected_matches = 0.0
    greedy_matches = 0
    kl_total = 0.0

    for global_index, step in enumerate(steps):
        distribution = _distribution_for_step(policy, step, global_index, action_labels)
        for label in distribution:
            if label not in action_labels:
                action_labels.append(label)
        probabilities = _normalize_distribution(distribution)
        expert_probability = probabilities.get(step.expert_action, 0.0)

        expected_matches += expert_probability
        if _greedy_action(probabilities) == step.expert_action:
            greedy_matches += 1
        expert_return += step.expert_reward
        policy_return += _expected_policy_reward(step, probabilities)
        kl_total += -math.log(max(expert_probability, EPSILON))

    num_transitions = len(steps)
    action_match_rate = expected_matches / num_transitions
    return_gap = expert_return - policy_return
    kl_divergence = kl_total / num_transitions

    metrics = {
        "action_match_rate": _round_metric(action_match_rate),
        "return_gap": _round_metric(return_gap),
        "kl_divergence": _round_metric(kl_divergence),
    }
    result = {
        "artifact_type": "evaluation_result",
        "registry_id": _evaluation_registry_id(behavior_policy_ref),
        "target_artifact_type": "behavior_policy",
        "target_artifact_id": _target_artifact_id(behavior_policy_ref),
        "evaluator_id": EVALUATOR_ID,
        "evaluation_timestamp": _utc_now(),
        "action_match_rate": metrics["action_match_rate"],
        "return_gap": metrics["return_gap"],
        "kl_divergence": metrics["kl_divergence"],
        "metrics": metrics,
        "evaluation_summary": {
            **metrics,
            "num_transitions": num_transitions,
            "num_trajectories": len({step.trajectory_id for step in steps}),
            "n_actions": len(action_labels),
            "action_labels": list(action_labels),
            "expected_matches": _round_metric(expected_matches),
            "greedy_matches": greedy_matches,
            "greedy_action_match_rate": _round_metric(greedy_matches / num_transitions),
            "expert_return": _round_metric(expert_return),
            "policy_return": _round_metric(policy_return),
            "return_gap_definition": "expert_return - policy_return",
            "kl_divergence_definition": "mean D_KL(delta_expert_action || policy_action_distribution)",
        },
        "registry_hints": {
            "artifact_type": "evaluation_result",
            "target_artifact_type": "behavior_policy",
        },
    }
    json.dumps(result, sort_keys=True)
    return result


def _extract_eval_steps(eval_trajectories: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[_EvalStep]:
    if isinstance(eval_trajectories, Mapping):
        raw_sessions = eval_trajectories.get("sessions", eval_trajectories.get("trajectories"))
    elif isinstance(eval_trajectories, Sequence) and not isinstance(eval_trajectories, (str, bytes)):
        raw_sessions = eval_trajectories
    else:
        raise ImitationEvaluationError("eval_trajectories must be a mapping or sequence of trajectory mappings")

    if not isinstance(raw_sessions, Sequence) or isinstance(raw_sessions, (str, bytes)) or not raw_sessions:
        raise ImitationEvaluationError("eval_trajectories must contain at least one trajectory/session")

    steps: list[_EvalStep] = []
    for session_index, session in enumerate(raw_sessions):
        if not isinstance(session, Mapping):
            raise ImitationEvaluationError(f"trajectory {session_index}: must be a mapping")
        trajectory_id = _optional_text(session.get("trajectory_id")) or f"trajectory-{session_index}"
        raw_steps = session.get("steps")
        if not isinstance(raw_steps, Sequence) or isinstance(raw_steps, (str, bytes)) or not raw_steps:
            raise ImitationEvaluationError(f"trajectory {trajectory_id}: steps must be a non-empty array")
        for step_index, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, Mapping):
                raise ImitationEvaluationError(f"trajectory {trajectory_id} step {step_index}: must be a mapping")
            action = _optional_text(raw_step.get("action"))
            if not action:
                raise ImitationEvaluationError(f"trajectory {trajectory_id} step {step_index}: action is required")
            steps.append(
                _EvalStep(
                    trajectory_id=trajectory_id,
                    step_index=step_index,
                    observation=_optional_observation(raw_step.get("observation"), trajectory_id, step_index),
                    expert_action=action,
                    expert_reward=_optional_float(raw_step.get("reward"), "reward"),
                    reward_by_action=_reward_by_action(raw_step),
                    step_id=_optional_text(raw_step.get("step_id")),
                    feedback_event_id=_optional_text(raw_step.get("feedback_event_id")),
                )
            )
    if not steps:
        raise ImitationEvaluationError("eval_trajectories contain no evaluable steps")
    return steps


def _resolve_policy_payload(behavior_policy_ref: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("artifact_bundle", "policy_payload", "policy", "behavior_policy"):
        value = behavior_policy_ref.get(key)
        if isinstance(value, Mapping):
            return _resolve_policy_payload(value)
    return behavior_policy_ref


def _initial_action_labels(policy: Mapping[str, Any], steps: Sequence[_EvalStep]) -> list[str]:
    labels: list[str] = []
    for source in (policy.get("action_labels"), policy.get("actions")):
        for label in _text_sequence(source):
            if label not in labels:
                labels.append(label)
    for field_name in ("default_action_probabilities", "action_probabilities", "probabilities"):
        source = policy.get(field_name)
        if isinstance(source, Mapping):
            for label in source:
                text = _optional_text(label)
                if text and text not in labels:
                    labels.append(text)
    for step in steps:
        if step.expert_action not in labels:
            labels.append(step.expert_action)
    return labels


def _distribution_for_step(
    policy: Mapping[str, Any],
    step: _EvalStep,
    global_index: int,
    action_labels: Sequence[str],
) -> dict[str, float]:
    keyed_prediction = _lookup_keyed_prediction(policy, step, global_index, action_labels)
    if keyed_prediction is not None:
        return keyed_prediction

    observation_prediction = _lookup_observation_prediction(policy, step, action_labels)
    if observation_prediction is not None:
        return observation_prediction

    centroid_prediction = _nearest_centroid_prediction(policy, step, action_labels)
    if centroid_prediction is not None:
        return centroid_prediction

    predictor = _optional_text(policy.get("predictor"))
    if predictor in {"uniform_random", "random_uniform", "random_policy"}:
        return {label: 1.0 for label in action_labels}

    for field_name in ("default_action_probabilities", "action_probabilities", "probabilities"):
        value = policy.get(field_name)
        if value is not None:
            return _distribution_from_value(value, action_labels)

    for field_name in ("default_action", "constant_action", "action"):
        value = _optional_text(policy.get(field_name))
        if value:
            return {value: 1.0}

    raise ImitationEvaluationError(
        "behavior policy must provide predictions, action probabilities, centroids, or a uniform_random predictor"
    )


def _lookup_keyed_prediction(
    policy: Mapping[str, Any],
    step: _EvalStep,
    global_index: int,
    action_labels: Sequence[str],
) -> dict[str, float] | None:
    keys = [
        step.step_id,
        step.feedback_event_id,
        f"{step.trajectory_id}:{step.step_index}",
        f"{step.trajectory_id}:step{step.step_index}",
        str(global_index),
    ]
    for field_name in (
        "predictions",
        "prediction_by_step",
        "action_predictions",
        "action_probabilities_by_step",
        "probabilities_by_step",
    ):
        container = policy.get(field_name)
        if not isinstance(container, Mapping):
            continue
        for key in keys:
            if key is not None and key in container:
                return _distribution_from_value(container[key], action_labels)
    return None


def _lookup_observation_prediction(
    policy: Mapping[str, Any],
    step: _EvalStep,
    action_labels: Sequence[str],
) -> dict[str, float] | None:
    if step.observation is None:
        return None
    keys = [_observation_key(step.observation), json.dumps(list(step.observation))]
    for field_name in ("action_by_observation", "probabilities_by_observation"):
        container = policy.get(field_name)
        if not isinstance(container, Mapping):
            continue
        for key in keys:
            if key in container:
                return _distribution_from_value(container[key], action_labels)
    return None


def _nearest_centroid_prediction(
    policy: Mapping[str, Any],
    step: _EvalStep,
    action_labels: Sequence[str],
) -> dict[str, float] | None:
    centroids = policy.get("action_centroids")
    if not isinstance(centroids, Mapping):
        return None
    if step.observation is None:
        raise ImitationEvaluationError("nearest-centroid policy evaluation requires step observations")
    distances: dict[str, float] = {}
    for label, centroid in centroids.items():
        action = _optional_text(label)
        vector = _optional_vector(centroid)
        if not action or vector is None:
            raise ImitationEvaluationError("action_centroids must map action labels to numeric vectors")
        if len(vector) != len(step.observation):
            raise ImitationEvaluationError("action_centroids must match observation dimension")
        distances[action] = math.dist(step.observation, vector)
    if not distances:
        raise ImitationEvaluationError("action_centroids must contain at least one action")
    nearest_action = min(distances, key=distances.get)
    return {label: 1.0 if label == nearest_action else 0.0 for label in set(action_labels) | set(distances)}


def _distribution_from_value(value: Any, action_labels: Sequence[str]) -> dict[str, float]:
    text_value = _optional_text(value)
    if text_value and isinstance(value, str):
        return {text_value: 1.0}

    if isinstance(value, Mapping):
        for action_key in ("action", "predicted_action", "label"):
            action = _optional_text(value.get(action_key))
            if action:
                return {action: 1.0}
        for probabilities_key in ("probabilities", "action_probabilities", "distribution"):
            probabilities = value.get(probabilities_key)
            if probabilities is not None:
                return _distribution_from_value(probabilities, action_labels)
        distribution: dict[str, float] = {}
        for label, probability in value.items():
            if label in {"confidence"}:
                continue
            action = _optional_text(label)
            if action is None:
                continue
            distribution[action] = _probability(probability, action)
        if distribution:
            return distribution

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) > len(action_labels):
            raise ImitationEvaluationError("probability vector is longer than action_labels")
        return {
            action_labels[index]: _probability(probability, action_labels[index])
            for index, probability in enumerate(value)
        }

    raise ImitationEvaluationError("policy prediction must be an action label or probability distribution")


def _normalize_distribution(distribution: Mapping[str, float]) -> dict[str, float]:
    if not distribution:
        raise ImitationEvaluationError("policy distribution must not be empty")
    total = 0.0
    normalized_input: dict[str, float] = {}
    for label, probability in distribution.items():
        action = _optional_text(label)
        if not action:
            raise ImitationEvaluationError("policy distribution action labels must be non-empty")
        prob = _probability(probability, action)
        normalized_input[action] = prob
        total += prob
    if total <= 0.0:
        raise ImitationEvaluationError("policy distribution probabilities must sum to a positive value")
    return {label: probability / total for label, probability in normalized_input.items()}


def _expected_policy_reward(step: _EvalStep, probabilities: Mapping[str, float]) -> float:
    if step.reward_by_action:
        return sum(probabilities.get(action, 0.0) * reward for action, reward in step.reward_by_action.items())
    return probabilities.get(step.expert_action, 0.0) * step.expert_reward


def _greedy_action(probabilities: Mapping[str, float]) -> str:
    return max(probabilities.items(), key=lambda item: (item[1], item[0]))[0]


def _reward_by_action(step: Mapping[str, Any]) -> dict[str, float]:
    for key in ("reward_by_action", "rewards_by_action", "action_rewards", "counterfactual_rewards"):
        value = step.get(key)
        if not isinstance(value, Mapping):
            continue
        return {
            str(action): _optional_float(reward, f"{key}.{action}")
            for action, reward in value.items()
        }
    return {}


def _optional_observation(value: Any, trajectory_id: str, step_index: int) -> tuple[float, ...] | None:
    if value is None:
        return None
    vector = _optional_vector(value)
    if vector is None:
        raise ImitationEvaluationError(
            f"trajectory {trajectory_id} step {step_index}: observation must be a numeric sequence"
        )
    return vector


def _optional_vector(value: Any) -> tuple[float, ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    vector: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)):
            return None
        vector.append(float(item))
    return tuple(vector)


def _optional_float(value: Any, field_name: str) -> float:
    if value is None:
        return 0.0
    if not isinstance(value, (int, float)):
        raise ImitationEvaluationError(f"{field_name} must be numeric when provided")
    return float(value)


def _probability(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float)):
        raise ImitationEvaluationError(f"probability for {field_name} must be numeric")
    probability = float(value)
    if probability < 0.0:
        raise ImitationEvaluationError(f"probability for {field_name} must be non-negative")
    return probability


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _text_sequence(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [text for item in value if (text := _optional_text(item))]


def _observation_key(observation: Sequence[float]) -> str:
    return json.dumps([float(item) for item in observation], sort_keys=True, separators=(",", ":"))


def _target_artifact_id(behavior_policy_ref: Mapping[str, Any]) -> str:
    nested_registry = behavior_policy_ref.get("registry_entry")
    candidates = [
        behavior_policy_ref.get("registry_id"),
        behavior_policy_ref.get("artifact_id"),
        behavior_policy_ref.get("id"),
    ]
    if isinstance(nested_registry, Mapping):
        candidates.extend(
            [
                nested_registry.get("registry_id"),
                nested_registry.get("artifact_id"),
                nested_registry.get("id"),
            ]
        )
    for candidate in candidates:
        text = _optional_text(candidate)
        if text:
            return text
    return "behavior_policy:unknown"


def _evaluation_registry_id(behavior_policy_ref: Mapping[str, Any]) -> str:
    target = _target_artifact_id(behavior_policy_ref).replace("/", "-").replace(":", "-")
    return f"eval-{target}-{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round_metric(value: float) -> float:
    return round(float(value), 12)
