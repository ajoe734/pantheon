"""PreferenceExample and CorrectionTrace schema-backed contracts.

IMT-002 defines the governed preference/correction data shapes used at the
research imitation boundary. These models validate learning data, but they do
not write registry state, launch training, or grant runtime authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator


SCHEMA_VERSION = "1.0"


class PreferenceValidationError(ValueError):
    """Raised when PreferenceExample or CorrectionTrace violates the contract."""


class ActorRole(str, Enum):
    OPERATOR = "operator"
    APPROVER = "approver"


class PreferenceAction(str, Enum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class ArtifactType(str, Enum):
    STRATEGY_SPEC = "strategy_spec"
    MODEL_ARTIFACT = "model_artifact"
    BEHAVIOR_POLICY = "behavior_policy"
    FEATURE_SET = "feature_set"
    PROMPT_BUNDLE = "prompt_bundle"
    SIGNAL_SNAPSHOT = "signal_snapshot"
    EXECUTION_BUNDLE = "execution_bundle"


class TrainingPromotionState(str, Enum):
    CANDIDATE = "candidate"
    PAPER = "paper"


class CorrectionOperationType(str, Enum):
    REPLACE = "replace"
    APPEND = "append"
    REMOVE = "remove"
    ANNOTATE = "annotate"


def _schema_dir() -> Path:
    return Path(__file__).resolve().parent


def preference_example_schema_path() -> Path:
    return _schema_dir() / "preference_example.schema.json"


def correction_trace_schema_path() -> Path:
    return _schema_dir() / "correction_trace.schema.json"


def load_preference_example_schema(schema_path: Path | None = None) -> dict[str, Any]:
    return json.loads((schema_path or preference_example_schema_path()).read_text(encoding="utf-8"))


def load_correction_trace_schema(schema_path: Path | None = None) -> dict[str, Any]:
    return json.loads((schema_path or correction_trace_schema_path()).read_text(encoding="utf-8"))


def validate_preference_example_payload(payload: Mapping[str, Any], schema_path: Path | None = None) -> None:
    _validate_payload(payload, load_preference_example_schema(schema_path), "PreferenceExample")


def validate_correction_trace_payload(payload: Mapping[str, Any], schema_path: Path | None = None) -> None:
    _validate_payload(payload, load_correction_trace_schema(schema_path), "CorrectionTrace")


def validate_preference_example_against_correction_trace(
    example: "PreferenceExample",
    trace: "CorrectionTrace",
) -> list[str]:
    """Return cross-object lineage errors for an edit preference example."""
    errors: list[str] = []
    if example.action != PreferenceAction.EDIT.value:
        errors.append("preference example action must be edit to reference a CorrectionTrace")
    if example.correction_trace_id != trace.trace_id:
        errors.append("example.correction_trace_id must match trace.trace_id")
    if example.strategy_id != trace.strategy_id:
        errors.append("example.strategy_id must match trace.strategy_id")
    if example.source_feedback_event_id != trace.source_feedback_event_id:
        errors.append("example.source_feedback_event_id must match trace.source_feedback_event_id")
    if example.actor_id != trace.actor_id:
        errors.append("example.actor_id must match trace.actor_id")
    if _stable_json(example.rejected_artifact) != _stable_json(trace.before_artifact):
        errors.append("example.rejected_artifact must match trace.before_artifact")
    if _stable_json(example.chosen_artifact) != _stable_json(trace.after_artifact):
        errors.append("example.chosen_artifact must match trace.after_artifact")
    return errors


def _validate_payload(payload: Mapping[str, Any], schema: Mapping[str, Any], label: str) -> None:
    if not isinstance(payload, Mapping):
        raise PreferenceValidationError(f"{label} payload must be a JSON object")
    validator = Draft7Validator(dict(schema))
    errors = sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        raise PreferenceValidationError(f"{label} schema validation failed at {_location(first)}: {first.message}")


def _location(error: Any) -> str:
    return ".".join(str(part) for part in error.path) or "<root>"


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PreferenceValidationError(f"{field_name} is required")
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
        raise PreferenceValidationError(f"{field_name} must be one of: {allowed}") from exc


def _optional_enum(enum_type: type[Enum], value: Enum | str | None, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    return _coerce_enum(enum_type, value, field_name)


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PreferenceValidationError(f"{field_name} must be an object")
    return value


def _artifact_snapshot(value: Any, field_name: str) -> dict[str, Any]:
    mapping = dict(_as_mapping(value, field_name))
    if not mapping:
        raise PreferenceValidationError(f"{field_name} must contain at least one property")
    return mapping


def _nullable_artifact_snapshot(value: Any, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return _artifact_snapshot(value, field_name)


def _as_sequence(value: Sequence[Any] | Any | None, field_name: str) -> tuple[Any, ...]:
    if value is None:
        return tuple()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PreferenceValidationError(f"{field_name} must be an array")
    return tuple(value)


@dataclass(frozen=True)
class GovernedTrainingTarget:
    strategy_id: str
    promotion_state: TrainingPromotionState | str
    registry_id: str | None = None
    artifact_version: str | None = None
    artifact_type: ArtifactType | str | None = None
    lineage_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_id", _require_text(self.strategy_id, "target.strategy_id"))
        object.__setattr__(
            self,
            "promotion_state",
            _coerce_enum(TrainingPromotionState, self.promotion_state, "target.promotion_state"),
        )
        object.__setattr__(self, "registry_id", _optional_text(self.registry_id, "target.registry_id"))
        object.__setattr__(
            self,
            "artifact_version",
            _optional_text(self.artifact_version, "target.artifact_version"),
        )
        object.__setattr__(
            self,
            "artifact_type",
            _optional_enum(ArtifactType, self.artifact_type, "target.artifact_type"),
        )
        object.__setattr__(self, "lineage_ref", _optional_text(self.lineage_ref, "target.lineage_ref"))

        has_registry = self.registry_id is not None
        has_version_type = self.artifact_version is not None and self.artifact_type is not None
        has_lineage_type = self.lineage_ref is not None and self.artifact_type is not None
        if not (has_registry or has_version_type or has_lineage_type):
            raise PreferenceValidationError(
                "target requires at least one of registry_id, "
                "(artifact_version + artifact_type), or (lineage_ref + artifact_type)"
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "promotion_state": self.promotion_state,
        }
        for key in ("registry_id", "artifact_version", "artifact_type", "lineage_ref"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GovernedTrainingTarget":
        mapping = _as_mapping(data, "target")
        return cls(
            strategy_id=mapping.get("strategy_id", ""),
            promotion_state=mapping.get("promotion_state", ""),
            registry_id=mapping.get("registry_id"),
            artifact_version=mapping.get("artifact_version"),
            artifact_type=mapping.get("artifact_type"),
            lineage_ref=mapping.get("lineage_ref"),
        )


@dataclass(frozen=True)
class CorrectionOperation:
    path: str
    operation: CorrectionOperationType | str
    old_value: Any = None
    new_value: Any = None
    rationale: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _require_text(self.path, "operations[].path"))
        object.__setattr__(
            self,
            "operation",
            _coerce_enum(CorrectionOperationType, self.operation, "operations[].operation"),
        )
        object.__setattr__(self, "rationale", _optional_text(self.rationale, "operations[].rationale"))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self.path,
            "operation": self.operation,
        }
        if self.old_value is not None:
            payload["old_value"] = self.old_value
        if self.new_value is not None:
            payload["new_value"] = self.new_value
        if self.rationale is not None:
            payload["rationale"] = self.rationale
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CorrectionOperation":
        mapping = _as_mapping(data, "operations[]")
        return cls(
            path=mapping.get("path", ""),
            operation=mapping.get("operation", ""),
            old_value=mapping.get("old_value"),
            new_value=mapping.get("new_value"),
            rationale=mapping.get("rationale"),
        )


@dataclass(frozen=True)
class CorrectionTrace:
    trace_id: str
    strategy_id: str
    source_feedback_event_id: str
    actor_id: str
    actor_role: ActorRole | str
    target: GovernedTrainingTarget
    before_artifact: Mapping[str, Any]
    after_artifact: Mapping[str, Any]
    operations: Sequence[CorrectionOperation | Mapping[str, Any]]
    created_at: str
    schema_version: str = SCHEMA_VERSION
    rationale: str | None = None
    task_ref: str | None = None
    decision_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_text(self.schema_version, "schema_version"))
        if self.schema_version != SCHEMA_VERSION:
            raise PreferenceValidationError(f"schema_version must be {SCHEMA_VERSION}")
        object.__setattr__(self, "trace_id", _require_text(self.trace_id, "trace_id"))
        object.__setattr__(self, "strategy_id", _require_text(self.strategy_id, "strategy_id"))
        object.__setattr__(
            self,
            "source_feedback_event_id",
            _require_text(self.source_feedback_event_id, "source_feedback_event_id"),
        )
        object.__setattr__(self, "actor_id", _require_text(self.actor_id, "actor_id"))
        object.__setattr__(self, "actor_role", _coerce_enum(ActorRole, self.actor_role, "actor_role"))
        if not isinstance(self.target, GovernedTrainingTarget):
            object.__setattr__(self, "target", GovernedTrainingTarget.from_dict(_as_mapping(self.target, "target")))
        object.__setattr__(self, "before_artifact", _artifact_snapshot(self.before_artifact, "before_artifact"))
        object.__setattr__(self, "after_artifact", _artifact_snapshot(self.after_artifact, "after_artifact"))
        object.__setattr__(
            self,
            "operations",
            tuple(
                op if isinstance(op, CorrectionOperation) else CorrectionOperation.from_dict(_as_mapping(op, "operations[]"))
                for op in _as_sequence(self.operations, "operations")
            ),
        )
        object.__setattr__(self, "created_at", _require_text(self.created_at, "created_at"))
        object.__setattr__(self, "rationale", _optional_text(self.rationale, "rationale"))
        object.__setattr__(self, "task_ref", _optional_text(self.task_ref, "task_ref"))
        object.__setattr__(self, "decision_ref", _optional_text(self.decision_ref, "decision_ref"))
        object.__setattr__(self, "metadata", dict(_as_mapping(self.metadata, "metadata")))

        if self.target.strategy_id != self.strategy_id:
            raise PreferenceValidationError("target.strategy_id must match strategy_id")
        if not self.operations:
            raise PreferenceValidationError("operations must contain at least one correction operation")
        if _stable_json(self.before_artifact) == _stable_json(self.after_artifact):
            raise PreferenceValidationError("before_artifact and after_artifact must differ")
        validate_correction_trace_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "strategy_id": self.strategy_id,
            "source_feedback_event_id": self.source_feedback_event_id,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
            "target": self.target.to_dict(),
            "before_artifact": dict(self.before_artifact),
            "after_artifact": dict(self.after_artifact),
            "operations": [op.to_dict() for op in self.operations],
            "created_at": self.created_at,
        }
        for key in ("rationale", "task_ref", "decision_ref"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, validate_schema: bool = True) -> "CorrectionTrace":
        if validate_schema:
            validate_correction_trace_payload(data)
        mapping = _as_mapping(data, "CorrectionTrace")
        return cls(
            schema_version=mapping.get("schema_version", SCHEMA_VERSION),
            trace_id=mapping.get("trace_id", ""),
            strategy_id=mapping.get("strategy_id", ""),
            source_feedback_event_id=mapping.get("source_feedback_event_id", ""),
            actor_id=mapping.get("actor_id", ""),
            actor_role=mapping.get("actor_role", ""),
            target=GovernedTrainingTarget.from_dict(_as_mapping(mapping.get("target"), "target")),
            before_artifact=mapping.get("before_artifact", {}),
            after_artifact=mapping.get("after_artifact", {}),
            operations=mapping.get("operations", ()),
            created_at=mapping.get("created_at", ""),
            rationale=mapping.get("rationale"),
            task_ref=mapping.get("task_ref"),
            decision_ref=mapping.get("decision_ref"),
            metadata=mapping.get("metadata", {}),
        )


@dataclass(frozen=True)
class PreferenceExample:
    example_id: str
    strategy_id: str
    source_feedback_event_id: str
    actor_id: str
    actor_role: ActorRole | str
    action: PreferenceAction | str
    target: GovernedTrainingTarget
    chosen_artifact: Mapping[str, Any] | None
    rejected_artifact: Mapping[str, Any] | None
    confidence: float
    observed_at: str
    created_at: str
    schema_version: str = SCHEMA_VERSION
    correction_trace_id: str | None = None
    rationale: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_text(self.schema_version, "schema_version"))
        if self.schema_version != SCHEMA_VERSION:
            raise PreferenceValidationError(f"schema_version must be {SCHEMA_VERSION}")
        object.__setattr__(self, "example_id", _require_text(self.example_id, "example_id"))
        object.__setattr__(self, "strategy_id", _require_text(self.strategy_id, "strategy_id"))
        object.__setattr__(
            self,
            "source_feedback_event_id",
            _require_text(self.source_feedback_event_id, "source_feedback_event_id"),
        )
        object.__setattr__(self, "actor_id", _require_text(self.actor_id, "actor_id"))
        object.__setattr__(self, "actor_role", _coerce_enum(ActorRole, self.actor_role, "actor_role"))
        object.__setattr__(self, "action", _coerce_enum(PreferenceAction, self.action, "action"))
        if not isinstance(self.target, GovernedTrainingTarget):
            object.__setattr__(self, "target", GovernedTrainingTarget.from_dict(_as_mapping(self.target, "target")))
        object.__setattr__(self, "chosen_artifact", _nullable_artifact_snapshot(self.chosen_artifact, "chosen_artifact"))
        object.__setattr__(self, "rejected_artifact", _nullable_artifact_snapshot(self.rejected_artifact, "rejected_artifact"))
        object.__setattr__(self, "confidence", _normalize_confidence(self.confidence))
        object.__setattr__(self, "observed_at", _require_text(self.observed_at, "observed_at"))
        object.__setattr__(self, "created_at", _require_text(self.created_at, "created_at"))
        object.__setattr__(
            self,
            "correction_trace_id",
            _optional_text(self.correction_trace_id, "correction_trace_id"),
        )
        object.__setattr__(self, "rationale", _optional_text(self.rationale, "rationale"))
        object.__setattr__(self, "context", dict(_as_mapping(self.context, "context")))
        object.__setattr__(self, "metadata", dict(_as_mapping(self.metadata, "metadata")))

        if self.target.strategy_id != self.strategy_id:
            raise PreferenceValidationError("target.strategy_id must match strategy_id")
        if self.action == PreferenceAction.APPROVE.value and self.chosen_artifact is None:
            raise PreferenceValidationError("approve examples require chosen_artifact")
        if self.action == PreferenceAction.REJECT.value and self.rejected_artifact is None:
            raise PreferenceValidationError("reject examples require rejected_artifact")
        if self.action == PreferenceAction.EDIT.value:
            if self.chosen_artifact is None or self.rejected_artifact is None:
                raise PreferenceValidationError("edit examples require both chosen_artifact and rejected_artifact")
            if self.correction_trace_id is None:
                raise PreferenceValidationError("edit examples require correction_trace_id")
        validate_preference_example_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "example_id": self.example_id,
            "strategy_id": self.strategy_id,
            "source_feedback_event_id": self.source_feedback_event_id,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
            "action": self.action,
            "target": self.target.to_dict(),
            "chosen_artifact": dict(self.chosen_artifact) if self.chosen_artifact is not None else None,
            "rejected_artifact": dict(self.rejected_artifact) if self.rejected_artifact is not None else None,
            "confidence": self.confidence,
            "observed_at": self.observed_at,
            "created_at": self.created_at,
        }
        if self.correction_trace_id is not None:
            payload["correction_trace_id"] = self.correction_trace_id
        if self.rationale is not None:
            payload["rationale"] = self.rationale
        if self.context:
            payload["context"] = dict(self.context)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, validate_schema: bool = True) -> "PreferenceExample":
        if validate_schema:
            validate_preference_example_payload(data)
        mapping = _as_mapping(data, "PreferenceExample")
        return cls(
            schema_version=mapping.get("schema_version", SCHEMA_VERSION),
            example_id=mapping.get("example_id", ""),
            strategy_id=mapping.get("strategy_id", ""),
            source_feedback_event_id=mapping.get("source_feedback_event_id", ""),
            correction_trace_id=mapping.get("correction_trace_id"),
            actor_id=mapping.get("actor_id", ""),
            actor_role=mapping.get("actor_role", ""),
            action=mapping.get("action", ""),
            target=GovernedTrainingTarget.from_dict(_as_mapping(mapping.get("target"), "target")),
            chosen_artifact=mapping.get("chosen_artifact"),
            rejected_artifact=mapping.get("rejected_artifact"),
            confidence=mapping.get("confidence", -1),
            rationale=mapping.get("rationale"),
            observed_at=mapping.get("observed_at", ""),
            created_at=mapping.get("created_at", ""),
            context=mapping.get("context", {}),
            metadata=mapping.get("metadata", {}),
        )


def _normalize_confidence(value: Any) -> float:
    if not isinstance(value, (int, float)):
        raise PreferenceValidationError("confidence must be numeric")
    confidence = float(value)
    if confidence < 0 or confidence > 1:
        raise PreferenceValidationError("confidence must be between 0 and 1")
    return confidence
