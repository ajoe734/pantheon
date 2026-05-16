"""Imitation dataset builder: assembles governed trader trajectories for BC training.

IMT-003 skeleton — bridges raw trading event data into the governed trajectory
dataset format consumed by services/learning/imitation/adapter.py.

Governance filters applied here mirror ALLOWED_ACTOR_ROLES / ELIGIBLE_DECISIONS /
ALLOWED_PROMOTION_STATES from the learning adapter, enforcing consistency at the
research-to-learning boundary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


ALLOWED_ACTOR_ROLES: frozenset[str] = frozenset({"operator", "approver"})
ALLOWED_PROMOTION_STATES: frozenset[str] = frozenset({"candidate", "paper"})
ELIGIBLE_DECISIONS: frozenset[str] = frozenset({"approve", "edit"})

_DECISION_ALIASES: dict[str, str] = {
    "approved": "approve",
    "approval": "approve",
    "edited": "edit",
    "rejected": "reject",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class DatasetBuilderError(ValueError):
    """Raised when a governed dataset cannot be assembled from raw input."""


@dataclass(frozen=True)
class TrajectoryStep:
    """One observation-action-reward tuple from a trader session."""

    observation: tuple[float, ...]
    action: str
    reward: float | None = None
    feedback_event_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "observation": list(self.observation),
            "action": self.action,
        }
        if self.reward is not None:
            payload["reward"] = self.reward
        if self.feedback_event_id:
            payload["feedback_event_id"] = self.feedback_event_id
        return payload


@dataclass(frozen=True)
class RawTrajectorySession:
    """Raw input for one trader session before governance filtering."""

    trajectory_id: str
    actor_id: str
    actor_role: str
    decision: str
    strategy_id: str
    registry_id: str
    artifact_version: str
    artifact_type: str
    promotion_state: str
    steps: tuple[TrajectoryStep, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RawTrajectorySession":
        trajectory_id = _require_string(payload, "trajectory_id")
        actor_id = _require_string(payload, "actor_id")
        actor_role = _require_string(payload, "actor_role")
        decision = _normalize_decision(payload.get("decision") or payload.get("event_type") or "")
        target = payload.get("target")
        if not isinstance(target, Mapping):
            raise DatasetBuilderError(f"trajectory {trajectory_id}: missing 'target' mapping")
        strategy_id = _require_string(target, "strategy_id")
        registry_id = _optional_string(target.get("registry_id")) or f"reg-{strategy_id}"
        artifact_version = _optional_string(target.get("artifact_version")) or "0.0.0"
        artifact_type = _optional_string(target.get("artifact_type")) or "strategy_spec"
        promotion_state = _require_string(target, "promotion_state")
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, Sequence) or not raw_steps:
            raise DatasetBuilderError(f"trajectory {trajectory_id}: no steps provided")
        steps = tuple(_parse_step(s, trajectory_id, index) for index, s in enumerate(raw_steps))
        return cls(
            trajectory_id=trajectory_id,
            actor_id=actor_id,
            actor_role=actor_role,
            decision=decision,
            strategy_id=strategy_id,
            registry_id=registry_id,
            artifact_version=artifact_version,
            artifact_type=artifact_type,
            promotion_state=promotion_state,
            steps=steps,
        )


@dataclass
class DatasetBuildRequest:
    """Request envelope for building a governed imitation dataset."""

    dataset_id: str
    strategy_id: str
    source_dataset_refs: list[str]
    sessions: list[RawTrajectorySession]
    source_strategy_spec_id: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DatasetBuildRequest":
        dataset_id = _require_string(payload, "dataset_id")
        strategy_id = _require_string(payload, "strategy_id")
        refs = payload.get("source_dataset_refs") or []
        if isinstance(refs, str):
            refs = [refs]
        source_dataset_refs = [str(r) for r in refs if r]
        if not source_dataset_refs:
            raise DatasetBuilderError("dataset must include at least one source_dataset_ref")
        raw_sessions = payload.get("sessions") or []
        sessions = [
            RawTrajectorySession.from_dict(s)
            for s in raw_sessions
            if isinstance(s, Mapping)
        ]
        return cls(
            dataset_id=dataset_id,
            strategy_id=strategy_id,
            source_dataset_refs=source_dataset_refs,
            sessions=sessions,
            source_strategy_spec_id=_optional_string(payload.get("source_strategy_spec_id")),
        )


@dataclass(frozen=True)
class DatasetBuildResult:
    """Output of the dataset builder: a governed payload plus a filter audit."""

    dataset_payload: dict[str, Any]
    included_trajectory_ids: tuple[str, ...]
    filtered_trajectory_ids: dict[str, str]
    build_id: str
    built_at: str

    @property
    def num_included(self) -> int:
        return len(self.included_trajectory_ids)

    @property
    def num_filtered(self) -> int:
        return len(self.filtered_trajectory_ids)


@dataclass
class ImitationDatasetBuilderConfig:
    """Governance parameters for dataset assembly."""

    allowed_actor_roles: frozenset[str] = field(default_factory=lambda: ALLOWED_ACTOR_ROLES)
    allowed_promotion_states: frozenset[str] = field(default_factory=lambda: ALLOWED_PROMOTION_STATES)
    eligible_decisions: frozenset[str] = field(default_factory=lambda: ELIGIBLE_DECISIONS)
    require_strategy_id_match: bool = True
    require_feedback_event_ids: bool = False


class ImitationDatasetBuilder:
    """Assembles a governed trajectory dataset from raw trading session data.

    Output format is the trajectory dataset payload defined in
    services/learning/imitation/examples/trajectory_dataset_sample.json,
    ready for GovernedTrajectoryAdapter.prepare() in the learning layer.
    """

    def __init__(self, config: ImitationDatasetBuilderConfig | None = None) -> None:
        self._config = config or ImitationDatasetBuilderConfig()

    def build(self, request: DatasetBuildRequest) -> DatasetBuildResult:
        config = self._config
        included: list[dict[str, Any]] = []
        filtered: dict[str, str] = {}

        for session in request.sessions:
            reason = self._filter_reason(session, request.strategy_id, config)
            if reason:
                filtered[session.trajectory_id] = reason
                continue

            session_payload: dict[str, Any] = {
                "trajectory_id": session.trajectory_id,
                "actor_id": session.actor_id,
                "actor_role": session.actor_role,
                "decision": session.decision,
                "target": {
                    "registry_id": session.registry_id,
                    "strategy_id": session.strategy_id,
                    "artifact_version": session.artifact_version,
                    "artifact_type": session.artifact_type,
                    "promotion_state": session.promotion_state,
                },
                "steps": [step.to_dict() for step in session.steps],
            }
            included.append(session_payload)

        if not included:
            raise DatasetBuilderError(
                "No governed trajectories remain after filtering. "
                f"Filtered: {filtered!r}"
            )

        dataset_payload: dict[str, Any] = {
            "dataset_id": request.dataset_id,
            "strategy_id": request.strategy_id,
            "source_dataset_refs": list(request.source_dataset_refs),
            "sessions": included,
        }
        if request.source_strategy_spec_id:
            dataset_payload["source_strategy_spec_id"] = request.source_strategy_spec_id

        build_id = f"dsb-{uuid.uuid4().hex[:12]}"
        return DatasetBuildResult(
            dataset_payload=dataset_payload,
            included_trajectory_ids=tuple(s["trajectory_id"] for s in included),
            filtered_trajectory_ids=filtered,
            build_id=build_id,
            built_at=utc_now(),
        )

    def _filter_reason(
        self,
        session: RawTrajectorySession,
        dataset_strategy_id: str,
        config: ImitationDatasetBuilderConfig,
    ) -> str:
        if session.actor_role not in config.allowed_actor_roles:
            return f"actor_role={session.actor_role!r} is not a governed role"
        if session.decision not in config.eligible_decisions:
            return f"decision={session.decision!r} is not eligible for BC training"
        if config.require_strategy_id_match and session.strategy_id != dataset_strategy_id:
            return (
                f"target.strategy_id={session.strategy_id!r} does not match "
                f"dataset.strategy_id={dataset_strategy_id!r}"
            )
        if session.promotion_state not in config.allowed_promotion_states:
            return f"promotion_state={session.promotion_state!r} is outside governed training states"
        if not session.steps:
            return "no trajectory steps present"
        if config.require_feedback_event_ids:
            missing = [s for s in session.steps if not s.feedback_event_id]
            if missing:
                return f"{len(missing)} step(s) missing feedback_event_id"
        return ""


def build_dataset(
    request: DatasetBuildRequest,
    *,
    config: ImitationDatasetBuilderConfig | None = None,
) -> DatasetBuildResult:
    """Convenience entry point for the imitation dataset builder."""
    return ImitationDatasetBuilder(config).build(request)


# ── helpers ──────────────────────────────────────────────────────────────────


def _normalize_decision(value: Any) -> str:
    s = _optional_string(value)
    return _DECISION_ALIASES.get(s, s) if s else ""


def _require_string(payload: Mapping[str, Any], key: str) -> str:
    val = _optional_string(payload.get(key))
    if not val:
        raise DatasetBuilderError(f"'{key}' must be a non-empty string")
    return val


def _optional_string(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value).strip()
    return value.strip()


def _parse_step(
    step: Any,
    trajectory_id: str,
    index: int,
) -> TrajectoryStep:
    if not isinstance(step, Mapping):
        raise DatasetBuilderError(f"trajectory {trajectory_id} step {index}: must be a mapping")
    obs_raw = step.get("observation")
    if not isinstance(obs_raw, Sequence) or isinstance(obs_raw, (str, bytes)):
        raise DatasetBuilderError(
            f"trajectory {trajectory_id} step {index}: observation must be a numeric sequence"
        )
    observation: list[float] = []
    for item in obs_raw:
        if not isinstance(item, (int, float)):
            raise DatasetBuilderError(
                f"trajectory {trajectory_id} step {index}: observation elements must be numeric"
            )
        observation.append(float(item))
    if not observation:
        raise DatasetBuilderError(f"trajectory {trajectory_id} step {index}: empty observation vector")
    action = _optional_string(step.get("action"))
    if not action:
        raise DatasetBuilderError(f"trajectory {trajectory_id} step {index}: missing action label")
    reward_raw = step.get("reward")
    reward: float | None = None
    if reward_raw is not None:
        if not isinstance(reward_raw, (int, float)):
            raise DatasetBuilderError(
                f"trajectory {trajectory_id} step {index}: reward must be numeric when provided"
            )
        reward = float(reward_raw)
    feedback_event_id = _optional_string(step.get("feedback_event_id"))
    return TrajectoryStep(
        observation=tuple(observation),
        action=action,
        reward=reward,
        feedback_event_id=feedback_event_id,
    )
