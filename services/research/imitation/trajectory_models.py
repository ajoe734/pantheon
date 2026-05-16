"""TraderTrajectory schema-backed contract for human imitation data.

IMT-001 defines the governed trajectory input shape used before imitation
dataset assembly. The model is validation-only: it does not write registry
state, launch training, promote artifacts, or grant live execution authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator

try:  # Package import when used through services.research.imitation.
    from .preference_models import GovernedTrainingTarget
except ImportError:  # Direct import when tests run from services/research/imitation.
    from preference_models import GovernedTrainingTarget


SCHEMA_VERSION = "1.0"


class TraderTrajectoryValidationError(ValueError):
    """Raised when a TraderTrajectory violates the contract."""


class TraderActorRole(str, Enum):
    OPERATOR = "operator"
    APPROVER = "approver"


class TraderDecision(str, Enum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"
    NO_ORDER = "no_order"


class StorageBackend(str, Enum):
    OBJECT_STORE = "object_store"
    POSTGRES = "postgres"
    LOCAL_FS = "local_fs"
    GCS = "gcs"


def _schema_dir() -> Path:
    return Path(__file__).resolve().parent


def trader_trajectory_schema_path() -> Path:
    return _schema_dir() / "trader_trajectory.schema.json"


def load_trader_trajectory_schema(schema_path: Path | None = None) -> dict[str, Any]:
    return json.loads((schema_path or trader_trajectory_schema_path()).read_text(encoding="utf-8"))


def validate_trader_trajectory_payload(payload: Mapping[str, Any], schema_path: Path | None = None) -> None:
    if not isinstance(payload, Mapping):
        raise TraderTrajectoryValidationError("TraderTrajectory payload must be a JSON object")
    validator = Draft7Validator(load_trader_trajectory_schema(schema_path))
    errors = sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        raise TraderTrajectoryValidationError(
            f"TraderTrajectory schema validation failed at {_location(first)}: {first.message}"
        )


def _location(error: Any) -> str:
    return ".".join(str(part) for part in error.path) or "<root>"


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TraderTrajectoryValidationError(f"{field_name} is required")
    return text


def _optional_text(value: Any, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    return _require_text(value, field_name)


def _coerce_enum(enum_type: type[Enum], value: Enum | str | None, field_name: str) -> str:
    if isinstance(value, enum_type):
        return str(value.value)
    try:
        return str(enum_type(str(value)).value)
    except ValueError as exc:
        allowed = ", ".join(str(item.value) for item in enum_type)
        raise TraderTrajectoryValidationError(f"{field_name} must be one of: {allowed}") from exc


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TraderTrajectoryValidationError(f"{field_name} must be an object")
    return value


def _as_sequence(value: Sequence[Any] | Any | None, field_name: str) -> tuple[Any, ...]:
    if value is None:
        return tuple()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TraderTrajectoryValidationError(f"{field_name} must be an array")
    return tuple(value)


def _as_unique_text_tuple(value: Sequence[Any] | Any | None, field_name: str) -> tuple[str, ...]:
    items = tuple(_require_text(item, f"{field_name}[]") for item in _as_sequence(value, field_name))
    if len(set(items)) != len(items):
        raise TraderTrajectoryValidationError(f"{field_name} must not contain duplicates")
    return items


def _normalize_optional_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise TraderTrajectoryValidationError(f"{field_name} must be numeric when provided")
    return float(value)


@dataclass(frozen=True)
class TrajectoryStorageRef:
    backend: StorageBackend | str
    path: str
    checksum: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "backend", _coerce_enum(StorageBackend, self.backend, "storage_ref.backend"))
        object.__setattr__(self, "path", _require_text(self.path, "storage_ref.path"))
        checksum = _require_text(self.checksum, "storage_ref.checksum")
        if not checksum.startswith("sha256:") or len(checksum) != 71:
            raise TraderTrajectoryValidationError("storage_ref.checksum must be a sha256:<64 hex> value")
        try:
            int(checksum.removeprefix("sha256:"), 16)
        except ValueError as exc:
            raise TraderTrajectoryValidationError("storage_ref.checksum must be a sha256:<64 hex> value") from exc
        object.__setattr__(self, "checksum", checksum)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "path": self.path,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TrajectoryStorageRef":
        mapping = _as_mapping(data, "storage_ref")
        return cls(
            backend=mapping.get("backend", ""),
            path=mapping.get("path", ""),
            checksum=mapping.get("checksum", ""),
        )


@dataclass(frozen=True)
class TrajectoryLineage:
    source_trace_refs: Sequence[str]
    source_feedback_event_ids: Sequence[str] = ()
    source_dataset_refs: Sequence[str] = ()
    teaching_session_id: str | None = None
    decision_ref: str | None = None
    task_ref: str | None = None

    def __post_init__(self) -> None:
        source_trace_refs = _as_unique_text_tuple(self.source_trace_refs, "lineage.source_trace_refs")
        if not source_trace_refs:
            raise TraderTrajectoryValidationError("lineage.source_trace_refs must contain at least one ref")
        object.__setattr__(self, "source_trace_refs", source_trace_refs)
        object.__setattr__(
            self,
            "source_feedback_event_ids",
            _as_unique_text_tuple(self.source_feedback_event_ids, "lineage.source_feedback_event_ids"),
        )
        object.__setattr__(
            self,
            "source_dataset_refs",
            _as_unique_text_tuple(self.source_dataset_refs, "lineage.source_dataset_refs"),
        )
        for field_name in ("teaching_session_id", "decision_ref", "task_ref"):
            object.__setattr__(self, field_name, _optional_text(getattr(self, field_name), f"lineage.{field_name}"))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_trace_refs": list(self.source_trace_refs),
        }
        if self.source_feedback_event_ids:
            payload["source_feedback_event_ids"] = list(self.source_feedback_event_ids)
        if self.source_dataset_refs:
            payload["source_dataset_refs"] = list(self.source_dataset_refs)
        for key in ("teaching_session_id", "decision_ref", "task_ref"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TrajectoryLineage":
        mapping = _as_mapping(data, "lineage")
        return cls(
            source_trace_refs=mapping.get("source_trace_refs", ()),
            source_feedback_event_ids=mapping.get("source_feedback_event_ids", ()),
            source_dataset_refs=mapping.get("source_dataset_refs", ()),
            teaching_session_id=mapping.get("teaching_session_id"),
            decision_ref=mapping.get("decision_ref"),
            task_ref=mapping.get("task_ref"),
        )


@dataclass(frozen=True)
class TrajectoryGovernance:
    research_only: bool = True
    direct_live_influence: bool = False
    eligible_for_training: bool | None = None
    notes: Sequence[str] = ()

    def __post_init__(self) -> None:
        if self.research_only is not True:
            raise TraderTrajectoryValidationError("governance.research_only must be true")
        if self.direct_live_influence is not False:
            raise TraderTrajectoryValidationError("governance.direct_live_influence must be false")
        if self.eligible_for_training is not None and not isinstance(self.eligible_for_training, bool):
            raise TraderTrajectoryValidationError("governance.eligible_for_training must be boolean when provided")
        object.__setattr__(self, "notes", tuple(_require_text(item, "governance.notes[]") for item in self.notes))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "research_only": self.research_only,
            "direct_live_influence": self.direct_live_influence,
        }
        if self.eligible_for_training is not None:
            payload["eligible_for_training"] = self.eligible_for_training
        if self.notes:
            payload["notes"] = list(self.notes)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TrajectoryGovernance":
        mapping = _as_mapping(data, "governance")
        return cls(
            research_only=mapping.get("research_only"),
            direct_live_influence=mapping.get("direct_live_influence"),
            eligible_for_training=mapping.get("eligible_for_training"),
            notes=mapping.get("notes", ()),
        )


@dataclass(frozen=True)
class TraderTrajectoryStep:
    observation: Sequence[float]
    action: str
    reward: float | None = None
    feedback_event_id: str | None = None
    step_id: str | None = None
    observed_at: str | None = None
    no_order_reason: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        observation = _as_sequence(self.observation, "steps[].observation")
        if not observation:
            raise TraderTrajectoryValidationError("steps[].observation must contain at least one value")
        normalized: list[float] = []
        for item in observation:
            if not isinstance(item, (int, float)):
                raise TraderTrajectoryValidationError("steps[].observation values must be numeric")
            normalized.append(float(item))
        object.__setattr__(self, "observation", tuple(normalized))
        object.__setattr__(self, "action", _require_text(self.action, "steps[].action"))
        object.__setattr__(self, "reward", _normalize_optional_float(self.reward, "steps[].reward"))
        for field_name in ("feedback_event_id", "step_id", "observed_at", "no_order_reason"):
            object.__setattr__(self, field_name, _optional_text(getattr(self, field_name), f"steps[].{field_name}"))
        object.__setattr__(self, "context", dict(_as_mapping(self.context, "steps[].context")))
        if self.action == TraderDecision.NO_ORDER.value and self.no_order_reason is None:
            raise TraderTrajectoryValidationError("steps[] with action=no_order require no_order_reason")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "observation": list(self.observation),
            "action": self.action,
        }
        if self.reward is not None:
            payload["reward"] = self.reward
        for key in ("feedback_event_id", "step_id", "observed_at", "no_order_reason"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.context:
            payload["context"] = dict(self.context)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TraderTrajectoryStep":
        mapping = _as_mapping(data, "steps[]")
        return cls(
            step_id=mapping.get("step_id"),
            observation=mapping.get("observation", ()),
            action=mapping.get("action", ""),
            reward=mapping.get("reward"),
            feedback_event_id=mapping.get("feedback_event_id"),
            observed_at=mapping.get("observed_at"),
            no_order_reason=mapping.get("no_order_reason"),
            context=mapping.get("context", {}),
        )


@dataclass(frozen=True)
class TraderTrajectory:
    trajectory_id: str
    strategy_id: str
    actor_id: str
    actor_role: TraderActorRole | str
    decision: TraderDecision | str
    target: GovernedTrainingTarget | Mapping[str, Any]
    steps: Sequence[TraderTrajectoryStep | Mapping[str, Any]]
    lineage: TrajectoryLineage | Mapping[str, Any]
    storage_ref: TrajectoryStorageRef | Mapping[str, Any]
    observed_at: str
    created_at: str
    schema_version: str = SCHEMA_VERSION
    governance: TrajectoryGovernance | Mapping[str, Any] = field(default_factory=TrajectoryGovernance)
    no_order_reason: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_text(self.schema_version, "schema_version"))
        if self.schema_version != SCHEMA_VERSION:
            raise TraderTrajectoryValidationError(f"schema_version must be {SCHEMA_VERSION}")
        object.__setattr__(self, "trajectory_id", _require_text(self.trajectory_id, "trajectory_id"))
        object.__setattr__(self, "strategy_id", _require_text(self.strategy_id, "strategy_id"))
        object.__setattr__(self, "actor_id", _require_text(self.actor_id, "actor_id"))
        object.__setattr__(self, "actor_role", _coerce_enum(TraderActorRole, self.actor_role, "actor_role"))
        object.__setattr__(self, "decision", _coerce_enum(TraderDecision, self.decision, "decision"))
        if not isinstance(self.target, GovernedTrainingTarget):
            object.__setattr__(self, "target", GovernedTrainingTarget.from_dict(_as_mapping(self.target, "target")))
        object.__setattr__(
            self,
            "steps",
            tuple(
                step if isinstance(step, TraderTrajectoryStep) else TraderTrajectoryStep.from_dict(_as_mapping(step, "steps[]"))
                for step in _as_sequence(self.steps, "steps")
            ),
        )
        if not isinstance(self.lineage, TrajectoryLineage):
            object.__setattr__(self, "lineage", TrajectoryLineage.from_dict(_as_mapping(self.lineage, "lineage")))
        if not isinstance(self.storage_ref, TrajectoryStorageRef):
            object.__setattr__(self, "storage_ref", TrajectoryStorageRef.from_dict(_as_mapping(self.storage_ref, "storage_ref")))
        if not isinstance(self.governance, TrajectoryGovernance):
            object.__setattr__(self, "governance", TrajectoryGovernance.from_dict(_as_mapping(self.governance, "governance")))
        object.__setattr__(self, "observed_at", _require_text(self.observed_at, "observed_at"))
        object.__setattr__(self, "created_at", _require_text(self.created_at, "created_at"))
        object.__setattr__(self, "no_order_reason", _optional_text(self.no_order_reason, "no_order_reason"))
        object.__setattr__(self, "context", dict(_as_mapping(self.context, "context")))
        object.__setattr__(self, "metadata", dict(_as_mapping(self.metadata, "metadata")))

        if self.target.strategy_id != self.strategy_id:
            raise TraderTrajectoryValidationError("target.strategy_id must match strategy_id")
        if not self.steps:
            raise TraderTrajectoryValidationError("steps must contain at least one trajectory step")
        if self.decision == TraderDecision.NO_ORDER.value and self.no_order_reason is None:
            raise TraderTrajectoryValidationError("no_order decisions require no_order_reason")
        validate_trader_trajectory_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "trajectory_id": self.trajectory_id,
            "strategy_id": self.strategy_id,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
            "decision": self.decision,
            "target": self.target.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "lineage": self.lineage.to_dict(),
            "storage_ref": self.storage_ref.to_dict(),
            "governance": self.governance.to_dict(),
            "observed_at": self.observed_at,
            "created_at": self.created_at,
        }
        if self.no_order_reason is not None:
            payload["no_order_reason"] = self.no_order_reason
        if self.context:
            payload["context"] = dict(self.context)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    def to_dataset_session(self) -> dict[str, Any]:
        """Return the session shape consumed by DatasetBuildRequest."""
        return {
            "trajectory_id": self.trajectory_id,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
            "decision": self.decision,
            "target": self.target.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, validate_schema: bool = True) -> "TraderTrajectory":
        if validate_schema:
            validate_trader_trajectory_payload(data)
        mapping = _as_mapping(data, "TraderTrajectory")
        return cls(
            schema_version=mapping.get("schema_version", SCHEMA_VERSION),
            trajectory_id=mapping.get("trajectory_id", ""),
            strategy_id=mapping.get("strategy_id", ""),
            actor_id=mapping.get("actor_id", ""),
            actor_role=mapping.get("actor_role", ""),
            decision=mapping.get("decision", ""),
            no_order_reason=mapping.get("no_order_reason"),
            target=GovernedTrainingTarget.from_dict(_as_mapping(mapping.get("target"), "target")),
            steps=mapping.get("steps", ()),
            lineage=TrajectoryLineage.from_dict(_as_mapping(mapping.get("lineage"), "lineage")),
            storage_ref=TrajectoryStorageRef.from_dict(_as_mapping(mapping.get("storage_ref"), "storage_ref")),
            governance=TrajectoryGovernance.from_dict(_as_mapping(mapping.get("governance"), "governance")),
            observed_at=mapping.get("observed_at", ""),
            created_at=mapping.get("created_at", ""),
            context=mapping.get("context", {}),
            metadata=mapping.get("metadata", {}),
        )
