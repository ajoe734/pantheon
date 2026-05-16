"""ExperimentTask and ExperimentRun schema-backed domain models.

These models define the research-orchestrator contract for the
StrategySpec -> ExperimentRun part of the research loop.  They validate
lineage-critical pins, but do not launch adapters, write registry state, or
grant deployment authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator


class ExperimentValidationError(ValueError):
    """Raised when an ExperimentTask or ExperimentRun violates the contract."""


class ExperimentTaskType(str, Enum):
    BACKTEST = "backtest"
    FACTOR_EVAL = "factor_eval"
    REGIME_MODEL = "regime_model"
    PRICING = "pricing"
    RL_TRAINING = "rl_training"
    RAPID_EVAL = "rapid_eval"


class ExperimentTaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class ExperimentTaskStatus(str, Enum):
    QUEUED = "queued"
    SELECTING_BACKEND = "selecting_backend"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExperimentRuntimeEnv(str, Enum):
    DEV = "dev"
    SANDBOX = "sandbox"
    RESEARCH = "research"


class ExperimentRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _schema_dir() -> Path:
    return Path(__file__).resolve().parent


def experiment_task_schema_path() -> Path:
    return _schema_dir() / "experiment_task.schema.json"


def experiment_run_schema_path() -> Path:
    return _schema_dir() / "experiment_run.schema.json"


def load_experiment_task_schema(schema_path: Path | None = None) -> dict[str, Any]:
    return json.loads((schema_path or experiment_task_schema_path()).read_text(encoding="utf-8"))


def load_experiment_run_schema(schema_path: Path | None = None) -> dict[str, Any]:
    return json.loads((schema_path or experiment_run_schema_path()).read_text(encoding="utf-8"))


def _location(error: Any) -> str:
    return ".".join(str(part) for part in error.path) or "<root>"


def _validate_payload(payload: Mapping[str, Any], schema: Mapping[str, Any], label: str) -> None:
    if not isinstance(payload, Mapping):
        raise ExperimentValidationError(f"{label} payload must be a JSON object")
    validator = Draft7Validator(dict(schema))
    errors = sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        raise ExperimentValidationError(f"{label} schema validation failed at {_location(first)}: {first.message}")


def validate_experiment_task_payload(payload: Mapping[str, Any], schema_path: Path | None = None) -> None:
    _validate_payload(payload, load_experiment_task_schema(schema_path), "ExperimentTask")


def validate_experiment_run_payload(payload: Mapping[str, Any], schema_path: Path | None = None) -> None:
    _validate_payload(payload, load_experiment_run_schema(schema_path), "ExperimentRun")


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ExperimentValidationError(f"{field_name} is required")
    return text


def _optional_text(value: Any, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    return _require_text(value, field_name)


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExperimentValidationError(f"{field_name} must be an object")
    return value


def _as_sequence(value: Sequence[Any] | Any | None, field_name: str) -> tuple[Any, ...]:
    if value is None:
        return tuple()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ExperimentValidationError(f"{field_name} must be an array")
    return tuple(value)


def _text_sequence(value: Sequence[Any] | Any | None, field_name: str) -> tuple[str, ...]:
    return tuple(_require_text(item, f"{field_name}[]") for item in _as_sequence(value, field_name))


def _coerce_enum(enum_type: type[Enum], value: Enum | str | None, field_name: str) -> str:
    if isinstance(value, enum_type):
        return str(value.value)
    try:
        return str(enum_type(str(value)).value)
    except ValueError as exc:
        allowed = ", ".join(str(item.value) for item in enum_type)
        raise ExperimentValidationError(f"{field_name} must be one of: {allowed}") from exc


def _optional_datetime(value: Any, field_name: str) -> str | None:
    return _optional_text(value, field_name)


@dataclass(frozen=True)
class ExperimentTask:
    task_id: str
    strategy_id: str
    strategy_spec_version: str
    requested_by: str
    task_type: ExperimentTaskType | str
    backend_selection_policy_id: str
    dataset_version_id: str
    code_version: str
    priority: ExperimentTaskPriority | str
    status: ExperimentTaskStatus | str
    idempotency_key: str
    trace_id: str
    created_at: str
    backend_id: str | None = None
    feature_spec_version: str | None = None
    label_spec_version: str | None = None
    cost_assumption_ref: str | None = None
    risk_assumption_ref: str | None = None
    updated_at: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_text(self.task_id, "task_id"))
        object.__setattr__(self, "strategy_id", _require_text(self.strategy_id, "strategy_id"))
        object.__setattr__(
            self,
            "strategy_spec_version",
            _require_text(self.strategy_spec_version, "strategy_spec_version"),
        )
        object.__setattr__(self, "requested_by", _require_text(self.requested_by, "requested_by"))
        object.__setattr__(self, "task_type", _coerce_enum(ExperimentTaskType, self.task_type, "task_type"))
        object.__setattr__(self, "backend_id", _optional_text(self.backend_id, "backend_id"))
        object.__setattr__(
            self,
            "backend_selection_policy_id",
            _require_text(self.backend_selection_policy_id, "backend_selection_policy_id"),
        )
        object.__setattr__(self, "dataset_version_id", _require_text(self.dataset_version_id, "dataset_version_id"))
        object.__setattr__(self, "code_version", _require_text(self.code_version, "code_version"))
        object.__setattr__(
            self,
            "feature_spec_version",
            _optional_text(self.feature_spec_version, "feature_spec_version"),
        )
        object.__setattr__(
            self,
            "label_spec_version",
            _optional_text(self.label_spec_version, "label_spec_version"),
        )
        object.__setattr__(
            self,
            "cost_assumption_ref",
            _optional_text(self.cost_assumption_ref, "cost_assumption_ref"),
        )
        object.__setattr__(
            self,
            "risk_assumption_ref",
            _optional_text(self.risk_assumption_ref, "risk_assumption_ref"),
        )
        object.__setattr__(self, "priority", _coerce_enum(ExperimentTaskPriority, self.priority, "priority"))
        object.__setattr__(self, "status", _coerce_enum(ExperimentTaskStatus, self.status, "status"))
        object.__setattr__(self, "idempotency_key", _require_text(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "trace_id", _require_text(self.trace_id, "trace_id"))
        object.__setattr__(self, "created_at", _require_text(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _optional_datetime(self.updated_at, "updated_at"))
        object.__setattr__(self, "metadata", dict(_as_mapping(self.metadata, "metadata")))

        errors = validate_experiment_task(self)
        if errors:
            raise ExperimentValidationError("; ".join(errors))
        validate_experiment_task_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "strategy_id": self.strategy_id,
            "strategy_spec_version": self.strategy_spec_version,
            "requested_by": self.requested_by,
            "task_type": self.task_type,
            "backend_id": self.backend_id,
            "backend_selection_policy_id": self.backend_selection_policy_id,
            "dataset_version_id": self.dataset_version_id,
            "code_version": self.code_version,
            "priority": self.priority,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "trace_id": self.trace_id,
            "created_at": self.created_at,
        }
        for key in (
            "feature_spec_version",
            "label_spec_version",
            "cost_assumption_ref",
            "risk_assumption_ref",
            "updated_at",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, validate_schema: bool = True) -> "ExperimentTask":
        if validate_schema:
            validate_experiment_task_payload(data)
        mapping = _as_mapping(data, "ExperimentTask")
        return cls(
            task_id=mapping.get("task_id", ""),
            strategy_id=mapping.get("strategy_id", ""),
            strategy_spec_version=mapping.get("strategy_spec_version", ""),
            requested_by=mapping.get("requested_by", ""),
            task_type=mapping.get("task_type", ""),
            backend_id=mapping.get("backend_id"),
            backend_selection_policy_id=mapping.get("backend_selection_policy_id", ""),
            dataset_version_id=mapping.get("dataset_version_id", ""),
            code_version=mapping.get("code_version", ""),
            feature_spec_version=mapping.get("feature_spec_version"),
            label_spec_version=mapping.get("label_spec_version"),
            cost_assumption_ref=mapping.get("cost_assumption_ref"),
            risk_assumption_ref=mapping.get("risk_assumption_ref"),
            priority=mapping.get("priority", ""),
            status=mapping.get("status", ""),
            idempotency_key=mapping.get("idempotency_key", ""),
            trace_id=mapping.get("trace_id", ""),
            created_at=mapping.get("created_at", ""),
            updated_at=mapping.get("updated_at"),
            metadata=mapping.get("metadata", {}),
        )


@dataclass(frozen=True)
class ExperimentRun:
    run_id: str
    task_id: str
    strategy_id: str
    strategy_spec_version: str
    backend_id: str
    runtime_env: ExperimentRuntimeEnv | str
    status: ExperimentRunStatus | str
    dataset_version_id: str
    code_version: str
    input_manifest_ref: str
    artifact_refs: Sequence[str]
    trace_id: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    output_manifest_ref: str | None = None
    metric_bundle_id: str | None = None
    logs_ref: str | None = None
    failure_reason: str | None = None
    updated_at: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_text(self.run_id, "run_id"))
        object.__setattr__(self, "task_id", _require_text(self.task_id, "task_id"))
        object.__setattr__(self, "strategy_id", _require_text(self.strategy_id, "strategy_id"))
        object.__setattr__(
            self,
            "strategy_spec_version",
            _require_text(self.strategy_spec_version, "strategy_spec_version"),
        )
        object.__setattr__(self, "backend_id", _require_text(self.backend_id, "backend_id"))
        object.__setattr__(self, "runtime_env", _coerce_enum(ExperimentRuntimeEnv, self.runtime_env, "runtime_env"))
        object.__setattr__(self, "status", _coerce_enum(ExperimentRunStatus, self.status, "status"))
        object.__setattr__(self, "started_at", _optional_datetime(self.started_at, "started_at"))
        object.__setattr__(self, "finished_at", _optional_datetime(self.finished_at, "finished_at"))
        object.__setattr__(self, "dataset_version_id", _require_text(self.dataset_version_id, "dataset_version_id"))
        object.__setattr__(self, "code_version", _require_text(self.code_version, "code_version"))
        object.__setattr__(self, "input_manifest_ref", _require_text(self.input_manifest_ref, "input_manifest_ref"))
        object.__setattr__(
            self,
            "output_manifest_ref",
            _optional_text(self.output_manifest_ref, "output_manifest_ref"),
        )
        object.__setattr__(self, "metric_bundle_id", _optional_text(self.metric_bundle_id, "metric_bundle_id"))
        object.__setattr__(self, "artifact_refs", _text_sequence(self.artifact_refs, "artifact_refs"))
        object.__setattr__(self, "logs_ref", _optional_text(self.logs_ref, "logs_ref"))
        object.__setattr__(self, "failure_reason", _optional_text(self.failure_reason, "failure_reason"))
        object.__setattr__(self, "trace_id", _require_text(self.trace_id, "trace_id"))
        object.__setattr__(self, "created_at", _require_text(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _optional_datetime(self.updated_at, "updated_at"))
        object.__setattr__(self, "metadata", dict(_as_mapping(self.metadata, "metadata")))

        errors = validate_experiment_run(self)
        if errors:
            raise ExperimentValidationError("; ".join(errors))
        validate_experiment_run_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "strategy_id": self.strategy_id,
            "strategy_spec_version": self.strategy_spec_version,
            "backend_id": self.backend_id,
            "runtime_env": self.runtime_env,
            "status": self.status,
            "dataset_version_id": self.dataset_version_id,
            "code_version": self.code_version,
            "input_manifest_ref": self.input_manifest_ref,
            "artifact_refs": list(self.artifact_refs),
            "trace_id": self.trace_id,
            "created_at": self.created_at,
        }
        for key in (
            "started_at",
            "finished_at",
            "output_manifest_ref",
            "metric_bundle_id",
            "logs_ref",
            "failure_reason",
            "updated_at",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, validate_schema: bool = True) -> "ExperimentRun":
        if validate_schema:
            validate_experiment_run_payload(data)
        mapping = _as_mapping(data, "ExperimentRun")
        return cls(
            run_id=mapping.get("run_id", ""),
            task_id=mapping.get("task_id", ""),
            strategy_id=mapping.get("strategy_id", ""),
            strategy_spec_version=mapping.get("strategy_spec_version", ""),
            backend_id=mapping.get("backend_id", ""),
            runtime_env=mapping.get("runtime_env", ""),
            status=mapping.get("status", ""),
            started_at=mapping.get("started_at"),
            finished_at=mapping.get("finished_at"),
            dataset_version_id=mapping.get("dataset_version_id", ""),
            code_version=mapping.get("code_version", ""),
            input_manifest_ref=mapping.get("input_manifest_ref", ""),
            output_manifest_ref=mapping.get("output_manifest_ref"),
            metric_bundle_id=mapping.get("metric_bundle_id"),
            artifact_refs=mapping.get("artifact_refs", ()),
            logs_ref=mapping.get("logs_ref"),
            failure_reason=mapping.get("failure_reason"),
            trace_id=mapping.get("trace_id", ""),
            created_at=mapping.get("created_at", ""),
            updated_at=mapping.get("updated_at"),
            metadata=mapping.get("metadata", {}),
        )


def validate_experiment_task(task: ExperimentTask) -> list[str]:
    errors: list[str] = []
    if task.status in {
        ExperimentTaskStatus.READY.value,
        ExperimentTaskStatus.RUNNING.value,
        ExperimentTaskStatus.COMPLETED.value,
        ExperimentTaskStatus.FAILED.value,
        ExperimentTaskStatus.CANCELLED.value,
    } and not task.backend_id:
        errors.append("backend_id is required once ExperimentTask leaves backend selection")
    return errors


def validate_experiment_run(run: ExperimentRun) -> list[str]:
    errors: list[str] = []
    if run.status == ExperimentRunStatus.RUNNING.value and not run.started_at:
        errors.append("running ExperimentRun requires started_at")
    if run.status == ExperimentRunStatus.COMPLETED.value:
        if not run.started_at:
            errors.append("completed ExperimentRun requires started_at")
        if not run.finished_at:
            errors.append("completed ExperimentRun requires finished_at")
        if not run.output_manifest_ref:
            errors.append("completed ExperimentRun requires output_manifest_ref")
    if run.status == ExperimentRunStatus.FAILED.value and not run.failure_reason:
        errors.append("failed ExperimentRun requires failure_reason")
    if len(set(run.artifact_refs)) != len(run.artifact_refs):
        errors.append("artifact_refs must not contain duplicates")
    return errors


def validate_experiment_run_against_task(run: ExperimentRun, task: ExperimentTask) -> list[str]:
    """Validate that a run preserves the lineage pins from its parent task."""
    errors: list[str] = []
    if run.task_id != task.task_id:
        errors.append("run.task_id must match task.task_id")
    if run.strategy_id != task.strategy_id:
        errors.append("run.strategy_id must match task.strategy_id")
    if run.strategy_spec_version != task.strategy_spec_version:
        errors.append("run.strategy_spec_version must match task.strategy_spec_version")
    if run.dataset_version_id != task.dataset_version_id:
        errors.append("run.dataset_version_id must match task.dataset_version_id")
    if run.code_version != task.code_version:
        errors.append("run.code_version must match task.code_version")
    if task.backend_id and run.backend_id != task.backend_id:
        errors.append("run.backend_id must match task.backend_id")
    return errors


__all__ = [
    "ExperimentRun",
    "ExperimentRunStatus",
    "ExperimentRuntimeEnv",
    "ExperimentTask",
    "ExperimentTaskPriority",
    "ExperimentTaskStatus",
    "ExperimentTaskType",
    "ExperimentValidationError",
    "experiment_run_schema_path",
    "experiment_task_schema_path",
    "load_experiment_run_schema",
    "load_experiment_task_schema",
    "validate_experiment_run",
    "validate_experiment_run_against_task",
    "validate_experiment_run_payload",
    "validate_experiment_task",
    "validate_experiment_task_payload",
]
