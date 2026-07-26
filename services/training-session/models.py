"""Schema-backed TeachingSession and TeachingEvent contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator


class TeachingValidationError(ValueError):
    """Raised when a teaching session or event violates the contract."""


class TeachingSessionMode(str, Enum):
    COACHING = "coaching"
    PATCH_PREVIEW = "patch_preview"
    EVALUATION = "evaluation"
    CORRECTION = "correction"


class TeachingSessionStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    COMMITTED = "committed"
    DISCARDED = "discarded"
    EXPIRED = "expired"


class TeachingEventType(str, Enum):
    MESSAGE = "message"
    CORRECTION = "correction"
    PATCH_PROPOSED = "patch_proposed"
    PREVIEW_REQUESTED = "preview_requested"
    PREVIEW_RESULT = "preview_result"
    COMMIT = "commit"
    DISCARD = "discard"
    CONTROL_PATCH = "control_patch"
    PREVIEW_TRIGGER = "preview_trigger"


class TeachingActorType(str, Enum):
    USER = "user"
    PERSONA = "persona"
    SERVICE = "service"


def _schema_dir() -> Path:
    return Path(__file__).resolve().parent


def teaching_session_schema_path() -> Path:
    return _schema_dir() / "teaching_session.schema.json"


def teaching_event_schema_path() -> Path:
    return _schema_dir() / "teaching_event.schema.json"


def load_teaching_session_schema(schema_path: Path | None = None) -> dict[str, Any]:
    return json.loads((schema_path or teaching_session_schema_path()).read_text(encoding="utf-8"))


def load_teaching_event_schema(schema_path: Path | None = None) -> dict[str, Any]:
    return json.loads((schema_path or teaching_event_schema_path()).read_text(encoding="utf-8"))


def _location(error: Any) -> str:
    return ".".join(str(part) for part in error.path) or "<root>"


def _validate_payload(payload: Mapping[str, Any], schema: Mapping[str, Any], label: str) -> None:
    if not isinstance(payload, Mapping):
        raise TeachingValidationError(f"{label} payload must be a JSON object")
    validator = Draft7Validator(dict(schema))
    errors = sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        raise TeachingValidationError(f"{label} schema validation failed at {_location(first)}: {first.message}")


def validate_teaching_session_payload(payload: Mapping[str, Any], schema_path: Path | None = None) -> None:
    _validate_payload(payload, load_teaching_session_schema(schema_path), "TeachingSession")


def validate_teaching_event_payload(payload: Mapping[str, Any], schema_path: Path | None = None) -> None:
    _validate_payload(payload, load_teaching_event_schema(schema_path), "TeachingEvent")


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TeachingValidationError(f"{field_name} is required")
    return text


def _optional_text(value: Any, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    return _require_text(value, field_name)


def _require_positive_int(value: Any, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise TeachingValidationError(f"{field_name} must be a positive integer") from exc
    if normalized <= 0:
        raise TeachingValidationError(f"{field_name} must be a positive integer")
    return normalized


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TeachingValidationError(f"{field_name} must be an object")
    return value


def _as_sequence(value: Sequence[Any] | Any | None, field_name: str) -> tuple[Any, ...]:
    if value is None:
        return tuple()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TeachingValidationError(f"{field_name} must be an array")
    return tuple(value)


def _mapping_sequence(value: Sequence[Any] | Any | None, field_name: str) -> tuple[Mapping[str, Any], ...]:
    return tuple(_as_mapping(item, f"{field_name}[]") for item in _as_sequence(value, field_name))


def _text_sequence(value: Sequence[Any] | Any | None, field_name: str) -> tuple[str, ...]:
    return tuple(_require_text(item, f"{field_name}[]") for item in _as_sequence(value, field_name))


def _coerce_enum(enum_type: type[Enum], value: Enum | str | None, field_name: str) -> str:
    if isinstance(value, enum_type):
        return str(value.value)
    try:
        return str(enum_type(str(value)).value)
    except ValueError as exc:
        allowed = ", ".join(str(item.value) for item in enum_type)
        raise TeachingValidationError(f"{field_name} must be one of: {allowed}") from exc


def _optional_datetime(value: Any, field_name: str) -> str | None:
    return _optional_text(value, field_name)


@dataclass(frozen=True)
class TeachingEvent:
    event_id: str
    session_id: str
    tenant_id: str
    event_type: TeachingEventType | str
    actor_type: TeachingActorType | str
    payload: Mapping[str, Any]
    timestamp: str
    correlation_id: str
    sequence_number: int
    actor: str | None = None
    actor_label: str | None = None
    message_body: str | None = None
    summary: str | None = None
    emitted_at: str | None = None
    outcome_signal: str | None = None
    evidence_ref: Mapping[str, Any] | None = None
    patch_delta: Sequence[Any] | None = None
    eval_ref: Mapping[str, Any] | None = None
    artifact_refs: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_text(self.event_id, "event_id"))
        object.__setattr__(self, "session_id", _require_text(self.session_id, "session_id"))
        object.__setattr__(self, "tenant_id", _require_text(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "event_type", _coerce_enum(TeachingEventType, self.event_type, "event_type"))
        object.__setattr__(self, "actor_type", _coerce_enum(TeachingActorType, self.actor_type, "actor_type"))
        object.__setattr__(self, "payload", dict(_as_mapping(self.payload, "payload")))
        object.__setattr__(self, "timestamp", _require_text(self.timestamp, "timestamp"))
        object.__setattr__(self, "correlation_id", _require_text(self.correlation_id, "correlation_id"))
        object.__setattr__(self, "sequence_number", _require_positive_int(self.sequence_number, "sequence_number"))
        object.__setattr__(self, "actor", _optional_text(self.actor, "actor"))
        object.__setattr__(self, "actor_label", _optional_text(self.actor_label, "actor_label"))
        object.__setattr__(self, "message_body", _optional_text(self.message_body, "message_body"))
        object.__setattr__(self, "summary", _optional_text(self.summary, "summary"))
        object.__setattr__(self, "emitted_at", _optional_datetime(self.emitted_at, "emitted_at") or self.timestamp)
        object.__setattr__(self, "outcome_signal", _optional_text(self.outcome_signal, "outcome_signal"))
        if self.evidence_ref is not None:
            object.__setattr__(self, "evidence_ref", dict(_as_mapping(self.evidence_ref, "evidence_ref")))
        if self.patch_delta is not None:
            object.__setattr__(self, "patch_delta", tuple(_as_sequence(self.patch_delta, "patch_delta")))
        if self.eval_ref is not None:
            object.__setattr__(self, "eval_ref", dict(_as_mapping(self.eval_ref, "eval_ref")))
        if self.artifact_refs is not None:
            object.__setattr__(self, "artifact_refs", dict(_as_mapping(self.artifact_refs, "artifact_refs")))
        object.__setattr__(self, "metadata", dict(_as_mapping(self.metadata, "metadata")))

        errors = validate_teaching_event(self)
        if errors:
            raise TeachingValidationError("; ".join(errors))
        validate_teaching_event_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "event_type": self.event_type,
            "actor_type": self.actor_type,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "sequence_number": self.sequence_number,
            "emitted_at": self.emitted_at,
        }
        for key in (
            "actor",
            "actor_label",
            "message_body",
            "summary",
            "outcome_signal",
            "evidence_ref",
            "eval_ref",
            "artifact_refs",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = dict(value) if isinstance(value, Mapping) else value
        if self.patch_delta is not None:
            payload["patch_delta"] = list(self.patch_delta)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, validate_schema: bool = True) -> "TeachingEvent":
        if validate_schema:
            validate_teaching_event_payload(data)
        mapping = _as_mapping(data, "TeachingEvent")
        return cls(
            event_id=mapping.get("event_id", ""),
            session_id=mapping.get("session_id", ""),
            tenant_id=mapping.get("tenant_id", ""),
            event_type=mapping.get("event_type", ""),
            actor_type=mapping.get("actor_type", ""),
            payload=mapping.get("payload", {}),
            timestamp=mapping.get("timestamp", ""),
            correlation_id=mapping.get("correlation_id", ""),
            sequence_number=mapping.get("sequence_number", 0),
            actor=mapping.get("actor"),
            actor_label=mapping.get("actor_label"),
            message_body=mapping.get("message_body"),
            summary=mapping.get("summary"),
            emitted_at=mapping.get("emitted_at"),
            outcome_signal=mapping.get("outcome_signal"),
            evidence_ref=mapping.get("evidence_ref"),
            patch_delta=mapping.get("patch_delta"),
            eval_ref=mapping.get("eval_ref"),
            artifact_refs=mapping.get("artifact_refs"),
            metadata=mapping.get("metadata", {}),
        )


@dataclass(frozen=True)
class TeachingSession:
    session_id: str
    persona_id: str
    tenant_id: str
    opened_by: str
    mode: TeachingSessionMode | str
    status: TeachingSessionStatus | str
    started_at: str
    trace_id: str
    id: str | None = None
    session_type: str = "trainer"
    objective: str | None = None
    topic: str | None = None
    ended_at: str | None = None
    current_control_state_ref: str | None = None
    context_refs: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    actor_context: Mapping[str, Any] = field(default_factory=dict)
    events: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    outcomes: Sequence[str] = field(default_factory=tuple)
    replay_resolution: Mapping[str, Any] | None = None
    artifacts: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _require_text(self.session_id, "session_id"))
        object.__setattr__(self, "persona_id", _require_text(self.persona_id, "persona_id"))
        object.__setattr__(self, "tenant_id", _require_text(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "opened_by", _require_text(self.opened_by, "opened_by"))
        object.__setattr__(self, "mode", _coerce_enum(TeachingSessionMode, self.mode, "mode"))
        object.__setattr__(self, "status", _coerce_enum(TeachingSessionStatus, self.status, "status"))
        object.__setattr__(self, "started_at", _require_text(self.started_at, "started_at"))
        object.__setattr__(self, "trace_id", _require_text(self.trace_id, "trace_id"))
        object.__setattr__(self, "id", _optional_text(self.id, "id"))
        if self.session_type != "trainer":
            raise TeachingValidationError("session_type must be trainer")
        object.__setattr__(self, "objective", _optional_text(self.objective, "objective"))
        object.__setattr__(self, "topic", _optional_text(self.topic, "topic"))
        object.__setattr__(self, "ended_at", _optional_datetime(self.ended_at, "ended_at"))
        object.__setattr__(
            self,
            "current_control_state_ref",
            _optional_text(self.current_control_state_ref, "current_control_state_ref"),
        )
        object.__setattr__(self, "context_refs", tuple(dict(item) for item in _mapping_sequence(self.context_refs, "context_refs")))
        object.__setattr__(self, "actor_context", dict(_as_mapping(self.actor_context, "actor_context")))
        object.__setattr__(self, "events", tuple(dict(item) for item in _mapping_sequence(self.events, "events")))
        object.__setattr__(self, "outcomes", _text_sequence(self.outcomes, "outcomes"))
        if self.replay_resolution is not None:
            object.__setattr__(self, "replay_resolution", dict(_as_mapping(self.replay_resolution, "replay_resolution")))
        if self.artifacts is not None:
            object.__setattr__(self, "artifacts", dict(_as_mapping(self.artifacts, "artifacts")))
        object.__setattr__(self, "metadata", dict(_as_mapping(self.metadata, "metadata")))

        errors = validate_teaching_session(self)
        if errors:
            raise TeachingValidationError("; ".join(errors))
        validate_teaching_session_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "persona_id": self.persona_id,
            "tenant_id": self.tenant_id,
            "opened_by": self.opened_by,
            "mode": self.mode,
            "session_type": self.session_type,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "current_control_state_ref": self.current_control_state_ref,
            "trace_id": self.trace_id,
            "context_refs": [dict(item) for item in self.context_refs],
            "actor_context": dict(self.actor_context),
            "events": [dict(item) for item in self.events],
            "outcomes": list(self.outcomes),
        }
        for key in ("id", "objective", "topic", "replay_resolution", "artifacts"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = dict(value) if isinstance(value, Mapping) else value
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, validate_schema: bool = True) -> "TeachingSession":
        if validate_schema:
            validate_teaching_session_payload(data)
        mapping = _as_mapping(data, "TeachingSession")
        return cls(
            id=mapping.get("id"),
            session_id=mapping.get("session_id", ""),
            persona_id=mapping.get("persona_id", ""),
            tenant_id=mapping.get("tenant_id", ""),
            opened_by=mapping.get("opened_by", ""),
            mode=mapping.get("mode", ""),
            session_type=mapping.get("session_type", "trainer"),
            objective=mapping.get("objective"),
            topic=mapping.get("topic"),
            status=mapping.get("status", ""),
            started_at=mapping.get("started_at", ""),
            ended_at=mapping.get("ended_at"),
            current_control_state_ref=mapping.get("current_control_state_ref"),
            trace_id=mapping.get("trace_id", ""),
            context_refs=mapping.get("context_refs", ()),
            actor_context=mapping.get("actor_context", {}),
            events=mapping.get("events", ()),
            outcomes=mapping.get("outcomes", ()),
            replay_resolution=mapping.get("replay_resolution"),
            artifacts=mapping.get("artifacts"),
            metadata=mapping.get("metadata", {}),
        )


def validate_teaching_event(event: TeachingEvent) -> list[str]:
    errors: list[str] = []
    if event.emitted_at and event.emitted_at != event.timestamp:
        errors.append("emitted_at must match timestamp when both are present")
    if event.event_type == TeachingEventType.MESSAGE.value and not (
        event.message_body or event.payload.get("message_body")
    ):
        errors.append("message TeachingEvent requires message_body in top-level field or payload")
    return errors


def validate_teaching_session(session: TeachingSession) -> list[str]:
    errors: list[str] = []
    terminal_statuses = {
        TeachingSessionStatus.COMPLETED.value,
        TeachingSessionStatus.ABANDONED.value,
        TeachingSessionStatus.COMMITTED.value,
        TeachingSessionStatus.DISCARDED.value,
        TeachingSessionStatus.EXPIRED.value,
    }
    if session.status in terminal_statuses and not session.ended_at:
        errors.append("terminal TeachingSession status requires ended_at")
    if session.status in {TeachingSessionStatus.ACTIVE.value, TeachingSessionStatus.PAUSED.value} and session.ended_at:
        errors.append("active or paused TeachingSession must not have ended_at")
    event_ids = [
        str(event.get("event_id") or "").strip()
        for event in session.events
        if isinstance(event, Mapping) and event.get("event_id")
    ]
    if len(set(event_ids)) != len(event_ids):
        errors.append("TeachingSession events must not contain duplicate event_id values")
    if any(
        str(event.get("tenant_id") or "").strip() != session.tenant_id
        for event in session.events
        if isinstance(event, Mapping)
    ):
        errors.append("TeachingSession events must match session.tenant_id")
    return errors


def validate_teaching_event_against_session(event: TeachingEvent, session: TeachingSession) -> list[str]:
    errors: list[str] = []
    if event.session_id != session.session_id:
        errors.append("event.session_id must match session.session_id")
    if event.tenant_id != session.tenant_id:
        errors.append("event.tenant_id must match session.tenant_id")
    return errors


__all__ = [
    "TeachingActorType",
    "TeachingEvent",
    "TeachingEventType",
    "TeachingSession",
    "TeachingSessionMode",
    "TeachingSessionStatus",
    "TeachingValidationError",
    "load_teaching_event_schema",
    "load_teaching_session_schema",
    "teaching_event_schema_path",
    "teaching_session_schema_path",
    "validate_teaching_event",
    "validate_teaching_event_against_session",
    "validate_teaching_event_payload",
    "validate_teaching_session",
    "validate_teaching_session_payload",
]
