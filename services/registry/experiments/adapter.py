from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

PRIMARY_BACKEND = "mlflow"
MLFLOW_VERSION_PIN = "3.10.1"
TRACKING_URI_ENV = "MLFLOW_TRACKING_URI"
_LINEAGE_KEYS = (
    "parent_registry_ids",
    "source_run_ids",
    "source_dataset_refs",
    "source_strategy_spec_id",
)
_PROMOTION_ALIASES = {
    "draft": (),
    "candidate": ("candidate",),
    "paper": ("paper",),
    "live": ("live",),
    "retired": ("retired",),
}


class ExperimentSyncError(ValueError):
    """Raised when registry metadata cannot be mirrored safely into the experiment backend."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_tag(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _flatten_numeric_metrics(payload: Mapping[str, Any], prefix: str = "") -> dict[str, float]:
    metrics: dict[str, float] = {}
    for key, value in payload.items():
        metric_name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            metrics.update(_flatten_numeric_metrics(value, metric_name))
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            metrics[metric_name] = float(value)
    return metrics


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_name: str
    run_name: str
    tags: dict[str, str]
    metrics: dict[str, float]
    artifacts: dict[str, Any]
    aliases: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ExperimentRef:
    backend: str
    run_id: str
    artifact_uri: str | None
    project: str
    experiment_name: str
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def to_metadata_ref(self) -> dict[str, Any]:
        payload = {
            "backend": self.backend,
            "run_id": self.run_id,
            "artifact_uri": self.artifact_uri,
            "project": self.project,
            "aliases": list(self.aliases),
            "experiment_name": self.experiment_name,
        }
        return {key: value for key, value in payload.items() if value not in (None, [], ())}


@dataclass(frozen=True)
class ExperimentSyncResult:
    record: ExperimentRecord
    experiment_ref: ExperimentRef
    promoted_metadata: dict[str, Any] | None


class ExperimentBackend(Protocol):
    def record(self, record: ExperimentRecord) -> ExperimentRef:
        ...


class InMemoryMlflowBackend:
    """Test backend that mirrors the MLflow record shape without external dependencies."""

    def __init__(self, tracking_uri: str = "memory://mlflow"):
        self.tracking_uri = tracking_uri
        self.runs: dict[str, dict[str, Any]] = {}

    def record(self, record: ExperimentRecord) -> ExperimentRef:
        run_id = f"mem-{uuid.uuid4().hex[:12]}"
        artifact_uri = f"{self.tracking_uri}/{record.experiment_name}/{run_id}/artifacts"
        self.runs[run_id] = {
            "experiment_name": record.experiment_name,
            "run_name": record.run_name,
            "tags": copy.deepcopy(record.tags),
            "metrics": copy.deepcopy(record.metrics),
            "artifacts": copy.deepcopy(record.artifacts),
            "aliases": list(record.aliases),
        }
        return ExperimentRef(
            backend=PRIMARY_BACKEND,
            run_id=run_id,
            artifact_uri=artifact_uri,
            project=record.experiment_name,
            experiment_name=record.experiment_name,
            aliases=record.aliases,
        )


class MlflowTrackingBackend:
    """Minimal MLflow tracking backend wrapper for governed registry sync."""

    def __init__(self, tracking_uri: str | None = None):
        self.tracking_uri = tracking_uri or os.environ.get(TRACKING_URI_ENV) or "http://localhost:5000"
        try:
            import mlflow  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only with missing optional dep
            raise ExperimentSyncError(
                "mlflow is not installed. Install services/research/mlflow/requirements.txt first."
            ) from exc

        self.mlflow = mlflow
        self.mlflow.set_tracking_uri(self.tracking_uri)

    def record(self, record: ExperimentRecord) -> ExperimentRef:
        self.mlflow.set_tracking_uri(self.tracking_uri)
        experiment = self.mlflow.get_experiment_by_name(record.experiment_name)
        experiment_id = experiment.experiment_id if experiment else self.mlflow.create_experiment(record.experiment_name)

        with self.mlflow.start_run(
            experiment_id=experiment_id,
            run_name=record.run_name,
            tags=record.tags,
        ) as run:
            for metric_name, value in record.metrics.items():
                self.mlflow.log_metric(metric_name, value)
            for artifact_path, payload in record.artifacts.items():
                self.mlflow.log_dict(payload, artifact_path)
            run_id = run.info.run_id
            artifact_uri = run.info.artifact_uri

        return ExperimentRef(
            backend=PRIMARY_BACKEND,
            run_id=run_id,
            artifact_uri=artifact_uri,
            project=record.experiment_name,
            experiment_name=record.experiment_name,
            aliases=record.aliases,
        )


class RegistryExperimentAdapter:
    """Maps governed registry entries into MLflow experiment records."""

    def __init__(self, backend: ExperimentBackend | None = None):
        self.backend = backend or InMemoryMlflowBackend()

    @classmethod
    def from_tracking_uri(cls, tracking_uri: str | None = None) -> "RegistryExperimentAdapter":
        return cls(backend=MlflowTrackingBackend(tracking_uri=tracking_uri))

    def build_record(self, entry: Mapping[str, Any]) -> ExperimentRecord:
        normalized = self._normalize_entry(entry)
        aliases = _PROMOTION_ALIASES[normalized["lifecycle_state"]]
        experiment_name = f"pantheon/{normalized['artifact_type']}/{normalized['strategy_id']}"
        run_name = f"{normalized['version']}:{normalized['lifecycle_state']}"
        tags = self._build_tags(normalized, aliases)
        artifacts = {
            "registry_entry.json": normalized,
            "artifact_handoff.json": self._build_artifact_handoff(normalized, aliases),
        }
        if normalized.get("evaluation_summary"):
            artifacts["evaluation_summary.json"] = normalized["evaluation_summary"]
        return ExperimentRecord(
            experiment_name=experiment_name,
            run_name=run_name,
            tags=tags,
            metrics=_flatten_numeric_metrics(normalized.get("evaluation_summary", {})),
            artifacts=artifacts,
            aliases=aliases,
        )

    def sync_registry_entry(self, entry: Mapping[str, Any]) -> ExperimentSyncResult:
        record = self.build_record(entry)
        experiment_ref = self.backend.record(record)
        promoted_metadata = self._build_promoted_metadata(
            self._normalize_entry(entry),
            experiment_ref=experiment_ref,
        )
        return ExperimentSyncResult(
            record=record,
            experiment_ref=experiment_ref,
            promoted_metadata=promoted_metadata,
        )

    def _normalize_entry(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        normalized = copy.deepcopy(dict(entry))
        self._validate_required_fields(normalized)
        lifecycle_state = normalized["lifecycle_state"]
        if lifecycle_state not in _PROMOTION_ALIASES:
            raise ExperimentSyncError(f"Unsupported lifecycle_state for LP-003 sync: {lifecycle_state}")
        lineage = normalized.get("lineage")
        if not isinstance(lineage, Mapping):
            raise ExperimentSyncError("Registry entry must include a lineage object.")
        storage_ref = normalized.get("storage_ref")
        if not isinstance(storage_ref, Mapping):
            raise ExperimentSyncError("Registry entry must include a storage_ref object.")
        if "backend" not in storage_ref or "path" not in storage_ref:
            raise ExperimentSyncError("storage_ref must include backend and path.")
        return normalized

    def _validate_required_fields(self, entry: Mapping[str, Any]) -> None:
        required_fields = (
            "registry_id",
            "artifact_type",
            "strategy_id",
            "version",
            "lifecycle_state",
            "lineage",
            "storage_ref",
            "checksum",
        )
        missing = [field for field in required_fields if field not in entry or entry[field] in (None, "")]
        if missing:
            raise ExperimentSyncError(f"Registry entry missing required fields: {', '.join(missing)}")

    def _build_tags(self, entry: Mapping[str, Any], aliases: tuple[str, ...]) -> dict[str, str]:
        lineage = dict(entry["lineage"])
        storage_ref = dict(entry["storage_ref"])
        base_tags: dict[str, Any] = {
            "pantheon.registry_id": entry["registry_id"],
            "pantheon.strategy_id": entry["strategy_id"],
            "pantheon.version": entry["version"],
            "pantheon.artifact_type": entry["artifact_type"],
            "pantheon.lifecycle_state": entry["lifecycle_state"],
            "pantheon.checksum": entry["checksum"],
            "pantheon.storage_backend": storage_ref["backend"],
            "pantheon.storage_path": storage_ref["path"],
            "pantheon.lineage": lineage,
            "pantheon.aliases": list(aliases),
            "pantheon.mlflow.version_pin": MLFLOW_VERSION_PIN,
        }

        optional_fields = {
            "pantheon.producer_run_id": entry.get("producer_run_id"),
            "pantheon.promoted_at": entry.get("promoted_at"),
            "pantheon.approver": entry.get("approver"),
            "pantheon.rollback_target": entry.get("rollback_target"),
            "pantheon.evaluation_summary": entry.get("evaluation_summary"),
        }
        base_tags.update({key: value for key, value in optional_fields.items() if value not in (None, "", {}, [])})

        for lineage_key in _LINEAGE_KEYS:
            if lineage_key in lineage and lineage[lineage_key] not in (None, "", [], {}):
                base_tags[f"pantheon.lineage.{lineage_key}"] = lineage[lineage_key]

        return {key: _json_tag(value) for key, value in base_tags.items()}

    def _build_artifact_handoff(self, entry: Mapping[str, Any], aliases: tuple[str, ...]) -> dict[str, Any]:
        return {
            "backend": PRIMARY_BACKEND,
            "tracking_version": MLFLOW_VERSION_PIN,
            "registry_id": entry["registry_id"],
            "strategy_id": entry["strategy_id"],
            "artifact_type": entry["artifact_type"],
            "version": entry["version"],
            "promotion_state": entry["lifecycle_state"],
            "checksum": entry["checksum"],
            "storage_ref": copy.deepcopy(entry["storage_ref"]),
            "execution_projection": {
                "metadata_path": f"openclaw/registry/{entry['strategy_id']}/{entry['version']}/metadata.json",
                "artifact_path": f"openclaw/registry/{entry['strategy_id']}/{entry['version']}/artifact.bin",
            },
            "aliases": list(aliases),
        }

    def _build_promoted_metadata(
        self,
        entry: Mapping[str, Any],
        experiment_ref: ExperimentRef,
    ) -> dict[str, Any] | None:
        state = entry["lifecycle_state"]
        if state == "draft":
            return None

        lineage = dict(entry["lineage"])
        has_source_reference = bool(
            lineage.get("source_run_ids")
            or lineage.get("source_strategy_spec_id")
            or lineage.get("source_dataset_refs")
        )
        if not has_source_reference:
            raise ExperimentSyncError(
                f"{state} entries need lineage that points to a run, dataset, or strategy spec before MLflow sync."
            )

        metadata = {
            "registry_id": entry["registry_id"],
            "strategy_id": entry["strategy_id"],
            "version": entry["version"],
            "artifact_type": entry["artifact_type"],
            "promotion_state": state,
            "checksum": entry["checksum"],
            "lineage": copy.deepcopy(entry["lineage"]),
            "created_at": utc_now(),
            "experiment_refs": [experiment_ref.to_metadata_ref()],
        }

        if entry.get("promoted_at"):
            metadata["approved_at"] = entry["promoted_at"]
        if entry.get("approver"):
            metadata["approver"] = entry["approver"]

        rollback = self._build_rollback(entry)
        if rollback is not None:
            metadata["rollback"] = rollback
        if state == "live" and rollback is None:
            raise ExperimentSyncError(
                "Live entries need metadata.rollback or metadata.rollback_target_registry_id plus rollback_target."
            )

        return metadata

    def _build_rollback(self, entry: Mapping[str, Any]) -> dict[str, Any] | None:
        metadata = entry.get("metadata")
        if isinstance(metadata, Mapping):
            rollback = metadata.get("rollback")
            if isinstance(rollback, Mapping):
                required = ("target_registry_id", "target_version")
                missing = [field for field in required if field not in rollback or rollback[field] in (None, "")]
                if missing:
                    raise ExperimentSyncError(
                        f"metadata.rollback missing required fields: {', '.join(missing)}"
                    )
                return dict(rollback)

            registry_id = metadata.get("rollback_target_registry_id")
            version = entry.get("rollback_target")
            if registry_id and version:
                return {
                    "target_registry_id": registry_id,
                    "target_version": version,
                }

        return None
