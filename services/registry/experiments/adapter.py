from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

PRIMARY_BACKEND = "mlflow"
MLFLOW_VERSION_PIN = "3.10.1"
WANDB_LOCAL_STORE_VERSION = "offline-local-store-v1"
TRACKING_URI_ENV = "MLFLOW_TRACKING_URI"
WANDB_LOCAL_STORE_DIR_ENV = "PANTHEON_WANDB_LOCAL_STORE_DIR"
WANDB_ONLINE_SYNC_FLAG = "PANTHEON_WANDB_ONLINE_SYNC_ENABLED"
_LINEAGE_KEYS = (
    "parent_registry_ids",
    "source_run_ids",
    "source_dataset_refs",
    "source_strategy_spec_id",
)
_ARTIFACT_ALIASES = {
    "draft": (),
    "candidate": ("candidate",),
    "approved": ("approved",),
    "retired": ("retired",),
}
_DEPLOYMENT_STAGES = ("none", "paper", "canary", "frozen", "live")
_LEGACY_LIFECYCLE_PROJECTION = {
    "draft": ("draft", "none"),
    "candidate": ("candidate", "none"),
    "paper": ("approved", "paper"),
    "live": ("approved", "live"),
    "retired": ("retired", "none"),
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


def _stable_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(payload: Any) -> str:
    return f"sha256:{hashlib.sha256(_stable_json_bytes(payload)).hexdigest()}"


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_name: str
    run_name: str
    tags: dict[str, str]
    params: dict[str, Any]
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
    run_uri: str | None = None
    artifact_refs: dict[str, Any] = field(default_factory=dict)
    sync_status: str | None = None

    def to_metadata_ref(self) -> dict[str, Any]:
        payload = {
            "backend": self.backend,
            "run_id": self.run_id,
            "run_uri": self.run_uri,
            "artifact_uri": self.artifact_uri,
            "artifact_refs": self.artifact_refs,
            "project": self.project,
            "aliases": list(self.aliases),
            "experiment_name": self.experiment_name,
            "sync_status": self.sync_status,
        }
        return {key: value for key, value in payload.items() if value not in (None, [], (), {})}


@dataclass(frozen=True)
class ExperimentSyncResult:
    record: ExperimentRecord
    experiment_ref: ExperimentRef
    promoted_metadata: dict[str, Any] | None


class ExperimentBackend(Protocol):
    backend_name: str
    tracking_version: str

    def record(self, record: ExperimentRecord) -> ExperimentRef:
        ...


class InMemoryMlflowBackend:
    """Test backend that mirrors the MLflow record shape without external dependencies."""

    backend_name = PRIMARY_BACKEND
    tracking_version = MLFLOW_VERSION_PIN

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
            "params": copy.deepcopy(record.params),
            "metrics": copy.deepcopy(record.metrics),
            "artifacts": copy.deepcopy(record.artifacts),
            "aliases": list(record.aliases),
        }
        return ExperimentRef(
            backend=self.backend_name,
            run_id=run_id,
            artifact_uri=artifact_uri,
            project=record.experiment_name,
            experiment_name=record.experiment_name,
            aliases=record.aliases,
        )


class MlflowTrackingBackend:
    """Minimal MLflow tracking backend wrapper for governed registry sync."""

    backend_name = PRIMARY_BACKEND
    tracking_version = MLFLOW_VERSION_PIN

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
            for param_name, value in record.params.items():
                self.mlflow.log_param(param_name, _json_tag(value))
            for metric_name, value in record.metrics.items():
                self.mlflow.log_metric(metric_name, value)
            for artifact_path, payload in record.artifacts.items():
                self.mlflow.log_dict(payload, artifact_path)
            run_id = run.info.run_id
            artifact_uri = run.info.artifact_uri

        return ExperimentRef(
            backend=self.backend_name,
            run_id=run_id,
            artifact_uri=artifact_uri,
            project=record.experiment_name,
            experiment_name=record.experiment_name,
            aliases=record.aliases,
        )


class LocalWandbRunStore:
    """Small JSON-backed local run store for offline W&B-compatible records."""

    def __init__(self, root_dir: str | os.PathLike[str] | None = None):
        self.root_dir = Path(root_dir or os.environ.get(WANDB_LOCAL_STORE_DIR_ENV) or "/tmp/pantheon/wandb-local")
        self.runs_dir = self.root_dir / "runs"
        self.artifacts_dir = self.root_dir / "artifacts"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def write_run(self, record: ExperimentRecord, *, run_id: str, mode: str) -> dict[str, Any]:
        artifact_refs: dict[str, Any] = {}
        for artifact_name, payload in record.artifacts.items():
            checksum = _sha256(payload)
            safe_name = artifact_name.replace("/", "__")
            artifact_path = self.artifacts_dir / f"{run_id}__{safe_name}.json"
            artifact_payload = {
                "run_id": run_id,
                "artifact_name": artifact_name,
                "payload": payload,
                "checksum": checksum,
                "created_at": utc_now(),
            }
            artifact_path.write_bytes(_stable_json_bytes(artifact_payload))
            artifact_refs[artifact_name] = {
                "artifact_ref": f"wandb-local://artifacts/{run_id}/{artifact_name}",
                "path": str(artifact_path),
                "checksum": checksum,
                "size_bytes": artifact_path.stat().st_size,
            }

        run_uri = f"wandb-local://runs/{run_id}"
        artifact_uri = f"wandb-local://runs/{run_id}/artifacts"
        run_payload = {
            "run_id": run_id,
            "run_uri": run_uri,
            "artifact_uri": artifact_uri,
            "backend": "wandb",
            "tracking_version": WANDB_LOCAL_STORE_VERSION,
            "experiment_name": record.experiment_name,
            "run_name": record.run_name,
            "mode": mode,
            "sync_status": "offline_local",
            "online_sync": {
                "enabled": False,
                "gate": WANDB_ONLINE_SYNC_FLAG,
                "policy": "fail_closed_no_sdk_no_network",
            },
            "tags": copy.deepcopy(record.tags),
            "params": copy.deepcopy(record.params),
            "metrics": copy.deepcopy(record.metrics),
            "artifacts": copy.deepcopy(record.artifacts),
            "artifact_refs": artifact_refs,
            "aliases": list(record.aliases),
            "created_at": utc_now(),
        }
        run_payload["checksum"] = _sha256(run_payload)
        run_path = self.runs_dir / f"{run_id}.json"
        run_payload["path"] = str(run_path)
        run_path.write_bytes(_stable_json_bytes(run_payload))
        return run_payload

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run_path = self.runs_dir / f"{run_id}.json"
        if not run_path.exists():
            return None
        return json.loads(run_path.read_text(encoding="utf-8"))

    def get_artifact(self, run_id: str, artifact_name: str) -> dict[str, Any] | None:
        artifact_path = self.artifacts_dir / f"{run_id}__{artifact_name.replace('/', '__')}.json"
        if not artifact_path.exists():
            return None
        return json.loads(artifact_path.read_text(encoding="utf-8"))

    def list_runs(self) -> list[dict[str, Any]]:
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.runs_dir.glob("*.json"))
        ]


class OfflineWandbLocalBackend:
    """Offline W&B-compatible backend that never imports the SDK or connects to the network."""

    backend_name = "wandb"
    tracking_version = WANDB_LOCAL_STORE_VERSION

    def __init__(self, mode: str = "offline", store_dir: str | os.PathLike[str] | None = None):
        normalized_mode = mode.strip().lower() or "offline"
        if normalized_mode not in ("offline", "dryrun"):
            raise ExperimentSyncError(
                f"Unsupported W&B offline mode: {mode!r}. Supported modes: ('offline', 'dryrun')."
            )
        self.mode = normalized_mode
        self.store = LocalWandbRunStore(store_dir)
        self.runs: dict[str, dict[str, Any]] = {}

    def record(self, record: ExperimentRecord) -> ExperimentRef:
        run_id = f"wandb-local-{uuid.uuid4().hex[:12]}"
        run_payload = self.store.write_run(record, run_id=run_id, mode=self.mode)
        self.runs[run_id] = copy.deepcopy(run_payload)
        return ExperimentRef(
            backend=self.backend_name,
            run_id=run_id,
            run_uri=run_payload["run_uri"],
            artifact_uri=run_payload["artifact_uri"],
            project=record.experiment_name,
            experiment_name=record.experiment_name,
            aliases=record.aliases,
            artifact_refs=copy.deepcopy(run_payload["artifact_refs"]),
            sync_status=run_payload["sync_status"],
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.store.get_run(run_id)

    def get_artifact(self, run_id: str, artifact_name: str) -> dict[str, Any] | None:
        return self.store.get_artifact(run_id, artifact_name)

    def sync_online(self, run_id: str) -> dict[str, Any]:
        if os.getenv(WANDB_ONLINE_SYNC_FLAG, "").strip().lower() not in {"1", "true", "yes", "on"}:
            raise ExperimentSyncError(
                f"W&B online sync is disabled. Set {WANDB_ONLINE_SYNC_FLAG}=1 only after the online gate is approved."
            )
        raise ExperimentSyncError(
            "W&B online sync gate is enabled, but SDK-backed/network sync is not implemented in this offline adapter."
        )


OfflineWandbPrepBackend = OfflineWandbLocalBackend


def build_backend(
    backend_name: str,
    *,
    tracking_uri: str | None = None,
    wandb_mode: str = "offline",
) -> ExperimentBackend:
    normalized = backend_name.strip().lower()
    if normalized == PRIMARY_BACKEND:
        return MlflowTrackingBackend(tracking_uri=tracking_uri)
    if normalized == "wandb":
        return OfflineWandbLocalBackend(mode=wandb_mode)
    raise ExperimentSyncError(f"Unsupported experiment backend: {backend_name!r}")


class RegistryExperimentAdapter:
    """Maps governed registry entries into experiment backend records."""

    def __init__(self, backend: ExperimentBackend | None = None):
        self.backend = backend or InMemoryMlflowBackend()

    @classmethod
    def from_tracking_uri(cls, tracking_uri: str | None = None) -> "RegistryExperimentAdapter":
        return cls(backend=build_backend(PRIMARY_BACKEND, tracking_uri=tracking_uri))

    @classmethod
    def from_backend_name(
        cls,
        backend_name: str,
        *,
        tracking_uri: str | None = None,
        wandb_mode: str = "offline",
    ) -> "RegistryExperimentAdapter":
        return cls(
            backend=build_backend(
                backend_name,
                tracking_uri=tracking_uri,
                wandb_mode=wandb_mode,
            )
        )

    @classmethod
    def from_env(cls, tracking_uri: str | None = None) -> "RegistryExperimentAdapter":
        from config import selected_backend, selected_wandb_mode

        backend_name = selected_backend()
        return cls.from_backend_name(
            backend_name,
            tracking_uri=tracking_uri,
            wandb_mode=selected_wandb_mode(),
        )

    def build_record(self, entry: Mapping[str, Any]) -> ExperimentRecord:
        normalized = self._normalize_entry(entry)
        return self._build_record_from_normalized(normalized)

    def _build_record_from_normalized(self, normalized: Mapping[str, Any]) -> ExperimentRecord:
        aliases = _ARTIFACT_ALIASES[normalized["artifact_state"]]
        experiment_name = f"pantheon/{normalized['artifact_type']}/{normalized['strategy_id']}"
        run_name = f"{normalized['version']}:{normalized['artifact_state']}:{normalized['deployment_stage']}"
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
            params=self._build_params(normalized),
            metrics=_flatten_numeric_metrics(normalized.get("evaluation_summary", {})),
            artifacts=artifacts,
            aliases=aliases,
        )

    def sync_registry_entry(self, entry: Mapping[str, Any]) -> ExperimentSyncResult:
        normalized = self._normalize_entry(entry)
        self._validate_promoted_metadata_inputs(normalized)
        record = self._build_record_from_normalized(normalized)
        experiment_ref = self.backend.record(record)
        promoted_metadata = self._build_promoted_metadata(
            normalized,
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
        if "artifact_state" not in normalized:
            lifecycle_state = normalized.get("lifecycle_state")
            if lifecycle_state not in _LEGACY_LIFECYCLE_PROJECTION:
                raise ExperimentSyncError(
                    "Registry entry must include canonical artifact_state or a supported legacy lifecycle_state."
                )
            artifact_state, deployment_stage = _LEGACY_LIFECYCLE_PROJECTION[lifecycle_state]
            normalized["artifact_state"] = artifact_state
            normalized.setdefault("deployment_stage", deployment_stage)
            normalized["legacy_lifecycle_state"] = lifecycle_state
        else:
            normalized["artifact_state"] = str(normalized["artifact_state"]).strip().lower()
            normalized["deployment_stage"] = str(normalized.get("deployment_stage") or "none").strip().lower()
            if "lifecycle_state" in normalized:
                normalized["legacy_lifecycle_state"] = normalized.pop("lifecycle_state")

        artifact_state = normalized["artifact_state"]
        if artifact_state not in _ARTIFACT_ALIASES:
            raise ExperimentSyncError(f"Unsupported artifact_state for LP-003 sync: {artifact_state}")
        deployment_stage = normalized["deployment_stage"]
        if deployment_stage not in _DEPLOYMENT_STAGES:
            raise ExperimentSyncError(
                f"Unsupported deployment_stage for LP-003 sync: {deployment_stage}. "
                f"Supported stages: {_DEPLOYMENT_STAGES}."
            )
        if deployment_stage != "none" and artifact_state != "approved":
            raise ExperimentSyncError(
                "deployment_stage may be non-none only when artifact_state=approved. "
                f"Got artifact_state={artifact_state!r}, deployment_stage={deployment_stage!r}."
            )
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
            "pantheon.artifact_state": entry["artifact_state"],
            "pantheon.deployment_stage": entry["deployment_stage"],
            "pantheon.checksum": entry["checksum"],
            "pantheon.storage_backend": storage_ref["backend"],
            "pantheon.storage_path": storage_ref["path"],
            "pantheon.lineage": lineage,
            "pantheon.aliases": list(aliases),
            "pantheon.experiment_backend": self.backend.backend_name,
            "pantheon.experiment_backend_version": self.backend.tracking_version,
        }
        if self.backend.backend_name == PRIMARY_BACKEND:
            base_tags["pantheon.mlflow.version_pin"] = MLFLOW_VERSION_PIN
        if self.backend.backend_name == "wandb":
            base_tags["pantheon.wandb.offline_local"] = True
            base_tags["pantheon.wandb.mode"] = getattr(self.backend, "mode", "offline")
            base_tags["pantheon.wandb.online_sync_gate"] = WANDB_ONLINE_SYNC_FLAG
        if entry.get("legacy_lifecycle_state"):
            base_tags["pantheon.compat.lifecycle_state"] = entry["legacy_lifecycle_state"]

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

    def _build_params(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        storage_ref = dict(entry["storage_ref"])
        params: dict[str, Any] = {
            "registry_id": entry["registry_id"],
            "strategy_id": entry["strategy_id"],
            "version": entry["version"],
            "artifact_type": entry["artifact_type"],
            "artifact_state": entry["artifact_state"],
            "deployment_stage": entry["deployment_stage"],
            "checksum": entry["checksum"],
            "storage_backend": storage_ref["backend"],
            "storage_path": storage_ref["path"],
        }
        if entry.get("producer_run_id"):
            params["producer_run_id"] = entry["producer_run_id"]
        return params

    def _build_artifact_handoff(self, entry: Mapping[str, Any], aliases: tuple[str, ...]) -> dict[str, Any]:
        return {
            "backend": self.backend.backend_name,
            "tracking_version": self.backend.tracking_version,
            "registry_id": entry["registry_id"],
            "strategy_id": entry["strategy_id"],
            "artifact_type": entry["artifact_type"],
            "version": entry["version"],
            "artifact_state": entry["artifact_state"],
            "deployment_stage": entry["deployment_stage"],
            "promotion_state": entry["artifact_state"],
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
        self._validate_promoted_metadata_inputs(entry)
        artifact_state = entry["artifact_state"]
        deployment_stage = entry["deployment_stage"]
        if artifact_state == "draft":
            return None

        metadata = {
            "registry_id": entry["registry_id"],
            "strategy_id": entry["strategy_id"],
            "version": entry["version"],
            "artifact_type": entry["artifact_type"],
            "artifact_state": artifact_state,
            "deployment_stage": deployment_stage,
            "promotion_state": artifact_state,
            "checksum": entry["checksum"],
            "lineage": copy.deepcopy(entry["lineage"]),
            "created_at": utc_now(),
            "experiment_refs": [experiment_ref.to_metadata_ref()],
        }
        if entry.get("legacy_lifecycle_state"):
            metadata["compat"] = {"legacy_lifecycle_state": entry["legacy_lifecycle_state"]}

        if entry.get("promoted_at"):
            metadata["approved_at"] = entry["promoted_at"]
        if entry.get("approver"):
            metadata["approver"] = entry["approver"]

        rollback = self._build_rollback(entry)
        if rollback is not None:
            metadata["rollback"] = rollback

        return metadata

    def _validate_promoted_metadata_inputs(self, entry: Mapping[str, Any]) -> None:
        artifact_state = entry["artifact_state"]
        if artifact_state == "draft":
            return

        lineage = dict(entry["lineage"])
        has_source_reference = bool(
            lineage.get("source_run_ids")
            or lineage.get("source_strategy_spec_id")
            or lineage.get("source_dataset_refs")
        )
        if not has_source_reference:
            raise ExperimentSyncError(
                f"{artifact_state} entries need lineage that points to a run, dataset, or strategy spec before experiment sync."
            )

        rollback = self._build_rollback(entry)
        if entry["deployment_stage"] == "live" and rollback is None:
            raise ExperimentSyncError(
                "Entries with deployment_stage=live need metadata.rollback or "
                "metadata.rollback_target_registry_id plus rollback_target."
            )

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
