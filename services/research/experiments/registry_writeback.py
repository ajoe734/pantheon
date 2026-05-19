"""Controlled ExperimentRun -> artifact registry writeback helpers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from services.registry.models import (
    ArtifactState,
    ArtifactType,
    Lineage,
    RegistryEntryCreate,
    RegistryEntryView,
    StorageBackend,
    StorageRef,
)
from services.registry.split_api import RegistryError, RegistryNotFoundError, RegistryService

from .models import ExperimentRun, ExperimentRunStatus


class ExperimentRegistryWritebackError(ValueError):
    """Raised when an ExperimentRun cannot be written back to the registry."""


_ALLOWED_WRITEBACK_STATES = {ArtifactState.DRAFT, ArtifactState.CANDIDATE}
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def build_registry_entry_from_experiment_run(
    run: ExperimentRun,
    artifact: Mapping[str, Any],
    *,
    artifact_type: str | None = None,
    strategy_id: str | None = None,
    version: str | None = None,
    requested_artifact_state: str | None = None,
    storage_ref: Mapping[str, Any] | str | None = None,
    checksum: str | None = None,
    source_strategy_spec_id: str | None = None,
    source_dataset_refs: Sequence[str] | None = None,
    parent_registry_ids: Sequence[str] | None = None,
    evaluation_summary: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> RegistryEntryCreate:
    """Build a governed registry create payload from a completed ExperimentRun.

    The writeback boundary is deliberately narrow: research may create only
    draft/candidate registry entries and never projects a deployment stage.
    Approval and deployment remain owned by downstream governance services.
    """

    if run.status != ExperimentRunStatus.COMPLETED.value:
        raise ExperimentRegistryWritebackError("ExperimentRun must be completed before registry writeback.")

    hints = _mapping(artifact.get("registry_hints")) or _mapping(artifact.get("registry_projection"))
    deployment_stage = _first_text(
        hints.get("deployment_stage"),
        artifact.get("deployment_stage"),
    ) or "none"
    if deployment_stage != "none":
        raise ExperimentRegistryWritebackError("ExperimentRun registry writeback must keep deployment_stage=none.")

    target_state = _artifact_state(_first_text(requested_artifact_state, hints.get("artifact_state")) or "candidate")
    target_type = _artifact_type(_first_text(artifact_type, hints.get("artifact_type"), artifact.get("artifact_type")))
    target_strategy_id = _required_text(
        _first_text(strategy_id, hints.get("strategy_id"), artifact.get("strategy_id"), run.strategy_id),
        "strategy_id",
    )
    target_version = _required_text(
        _first_text(version, hints.get("version"), artifact.get("version"), run.strategy_spec_version),
        "version",
    )

    lineage = _lineage(
        run,
        hints=hints,
        artifact=artifact,
        source_strategy_spec_id=source_strategy_spec_id,
        source_dataset_refs=source_dataset_refs,
        parent_registry_ids=parent_registry_ids,
    )
    target_storage_ref = _storage_ref(
        _first_present(storage_ref, hints.get("storage_ref"), artifact.get("storage_ref"), run.output_manifest_ref)
    )
    target_checksum = _first_text(checksum, hints.get("checksum"), artifact.get("checksum")) or _sha256_json(artifact)

    return RegistryEntryCreate(
        artifact_type=target_type,
        strategy_id=target_strategy_id,
        version=target_version,
        artifact_state=target_state,
        lineage=lineage,
        storage_ref=target_storage_ref,
        checksum=target_checksum,
        producer_run_id=run.run_id,
        evaluation_summary=_evaluation_summary(run, artifact, hints, evaluation_summary),
        metadata=_metadata(run, artifact, hints, metadata),
    )


def write_experiment_run_artifact_to_registry(
    run: ExperimentRun,
    artifact: Mapping[str, Any],
    *,
    registry_service: RegistryService,
    registry_id: str | None = None,
    **kwargs: Any,
) -> RegistryEntryView:
    """Create the draft/candidate registry entry for one completed run artifact."""

    payload = build_registry_entry_from_experiment_run(run, artifact, **kwargs)
    target_registry_id = registry_id or _default_registry_id(run, artifact, payload)
    try:
        registry_service.get(target_registry_id)
    except RegistryNotFoundError:
        pass
    else:
        raise ExperimentRegistryWritebackError(f"Registry entry already exists: {target_registry_id}")
    try:
        return registry_service.register(payload, target_registry_id)
    except RegistryError as exc:
        raise ExperimentRegistryWritebackError(str(exc)) from exc


def registry_entry_view_to_dict(view: RegistryEntryView) -> dict[str, Any]:
    """Return a JSON-serializable registry view with enum values materialized."""

    return _jsonable(view)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _first_text(*values: Any) -> str | None:
    value = _first_present(*values)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_text(value: str | None, field_name: str) -> str:
    if not value:
        raise ExperimentRegistryWritebackError(f"{field_name} is required for registry writeback.")
    return value


def _text_list(*values: Any) -> list[str]:
    refs: list[str] = []
    for value in values:
        if value in (None, ""):
            continue
        items = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else [value]
        for item in items:
            text = str(item or "").strip()
            if text and text not in refs:
                refs.append(text)
    return refs


def _artifact_state(value: str) -> ArtifactState:
    try:
        state = ArtifactState(value)
    except ValueError as exc:
        raise ExperimentRegistryWritebackError(f"Unsupported artifact_state for registry writeback: {value}") from exc
    if state not in _ALLOWED_WRITEBACK_STATES:
        raise ExperimentRegistryWritebackError("Registry writeback may only create draft or candidate artifacts.")
    return state


def _artifact_type(value: str | None) -> ArtifactType:
    if not value:
        raise ExperimentRegistryWritebackError("artifact_type is required for registry writeback.")
    try:
        return ArtifactType(value)
    except ValueError as exc:
        raise ExperimentRegistryWritebackError(f"Unsupported artifact_type for registry writeback: {value}") from exc


def _storage_ref(value: Any) -> StorageRef:
    if isinstance(value, Mapping):
        try:
            return StorageRef.from_dict(dict(value))
        except (KeyError, ValueError) as exc:
            raise ExperimentRegistryWritebackError("storage_ref must include a supported backend and path.") from exc
    path = _required_text(str(value or "").strip(), "storage_ref.path")
    backend = StorageBackend.INLINE if path.startswith("$.") else StorageBackend.OBJECT_STORE
    return StorageRef(backend=backend, path=path)


def _lineage(
    run: ExperimentRun,
    *,
    hints: Mapping[str, Any],
    artifact: Mapping[str, Any],
    source_strategy_spec_id: str | None,
    source_dataset_refs: Sequence[str] | None,
    parent_registry_ids: Sequence[str] | None,
) -> Lineage:
    strategy_spec_ref = _first_text(
        source_strategy_spec_id,
        hints.get("source_strategy_spec_id"),
        hints.get("strategy_spec_id"),
        artifact.get("source_strategy_spec_id"),
        _mapping(run.metadata).get("source_strategy_spec_id"),
        f"{run.strategy_id}@{run.strategy_spec_version}",
    )
    return Lineage(
        parent_registry_ids=_text_list(
            parent_registry_ids,
            hints.get("parent_registry_ids"),
            hints.get("strategy_spec_registry_id"),
        )
        or None,
        source_run_ids=_text_list(run.run_id, hints.get("source_run_ids")) or None,
        source_dataset_refs=_text_list(
            source_dataset_refs,
            hints.get("source_dataset_refs"),
            artifact.get("source_dataset_refs"),
            run.dataset_version_id,
        )
        or None,
        source_strategy_spec_id=strategy_spec_ref,
    )


def _evaluation_summary(
    run: ExperimentRun,
    artifact: Mapping[str, Any],
    hints: Mapping[str, Any],
    override: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    summary: dict[str, Any] = {}
    if run.metric_bundle_id:
        summary["metric_bundle_id"] = run.metric_bundle_id
    for source_name, source in (
        ("artifact.evaluation_summary", artifact.get("evaluation_summary")),
        ("registry_hints.evaluation_summary", hints.get("evaluation_summary")),
        ("evaluation_summary", override),
    ):
        if source is None:
            continue
        if not isinstance(source, Mapping):
            raise ExperimentRegistryWritebackError(f"{source_name} must be a mapping for registry writeback.")
        summary.update(dict(source))
    return summary or None


def _metadata(
    run: ExperimentRun,
    artifact: Mapping[str, Any],
    hints: Mapping[str, Any],
    override: Mapping[str, Any] | None,
) -> dict[str, Any]:
    artifact_metadata = _mapping(artifact.get("metadata"))
    payload = {
        "writeback_source": "experiment_run",
        "registry_write_authority": "research_orchestrator_controlled_writeback",
        "deployment_stage": "none",
        "experiment_run_id": run.run_id,
        "experiment_task_id": run.task_id,
        "strategy_spec_version": run.strategy_spec_version,
        "backend_id": run.backend_id,
        "runtime_env": run.runtime_env,
        "dataset_version_id": run.dataset_version_id,
        "code_version": run.code_version,
        "input_manifest_ref": run.input_manifest_ref,
        "output_manifest_ref": run.output_manifest_ref,
        "metric_bundle_id": run.metric_bundle_id,
        "source_artifact_id": _first_text(artifact.get("artifact_id"), artifact.get("id"), artifact.get("artifact_ref")),
        "source_artifact_family": artifact.get("artifact_family"),
        "registry_hints": dict(hints),
        "source_artifact_metadata": artifact_metadata,
    }
    if override:
        payload.update(dict(override))
    return {key: value for key, value in payload.items() if value not in (None, "", {}, [])}


def _default_registry_id(run: ExperimentRun, artifact: Mapping[str, Any], payload: RegistryEntryCreate) -> str:
    artifact_id = _first_text(artifact.get("artifact_id"), artifact.get("id"), "artifact")
    parts = ["reg-exp", payload.strategy_id, payload.version, run.run_id, artifact_id]
    return "-".join(_safe_id(part) for part in parts if part)


def _safe_id(value: str) -> str:
    safe = _SAFE_ID_RE.sub("-", value).strip("-")
    return safe or "unknown"


def _sha256_json(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items() if item is not None}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items() if item is not None}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "ExperimentRegistryWritebackError",
    "build_registry_entry_from_experiment_run",
    "registry_entry_view_to_dict",
    "write_experiment_run_artifact_to_registry",
]
