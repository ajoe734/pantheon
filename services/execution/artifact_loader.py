from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

try:
    import jsonschema
except ImportError:  # pragma: no cover - jsonschema is expected in local validation flows
    jsonschema = None


class ArtifactLoadError(ValueError):
    """Raised when governed artifact metadata or payload loading fails."""


class ExecutionMode(str, Enum):
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"


@dataclass(frozen=True)
class ObjectStoreProjection:
    metadata_key: str
    artifact_key: str


@dataclass(frozen=True)
class LoadedArtifact:
    metadata: dict[str, Any]
    payload: bytes
    projection: ObjectStoreProjection
    artifact_path: str | None = None


class LeanObjectStoreAdapter:
    """
    Normalize LEAN Object Store access across:

    - Python algorithms (`self.object_store.read`, `save_bytes`, ...)
    - wrapped .NET APIs (`ObjectStore.Read`, `SaveBytes`, ...)
    - test doubles that expose either naming style
    """

    def __init__(self, object_store: Any):
        self._store = self._resolve_store(object_store)

    @classmethod
    def from_runtime(cls, runtime_or_store: Any) -> "LeanObjectStoreAdapter":
        return cls(runtime_or_store)

    @staticmethod
    def _resolve_store(runtime_or_store: Any) -> Any:
        for attr_name in ("object_store", "ObjectStore"):
            store = getattr(runtime_or_store, attr_name, None)
            if store is not None:
                return store
        return runtime_or_store

    def contains_key(self, key: str) -> bool:
        contains = _resolve_method(self._store, ("ContainsKey", "contains_key"))
        if contains is not None:
            return bool(contains(key))
        try:
            self.read_bytes(key)
            return True
        except Exception:
            return False

    def read_json(self, key: str) -> dict[str, Any] | None:
        read_json = _resolve_method(self._store, ("ReadJson", "read_json"))
        if read_json is None:
            return None

        try:
            payload = read_json(key)
        except TypeError:
            return None

        if isinstance(payload, Mapping):
            return dict(payload)
        return None

    def read_text(self, key: str) -> str:
        read = _resolve_method(self._store, ("Read", "read"))
        if read is not None:
            payload = read(key)
            if isinstance(payload, str):
                return payload
            if isinstance(payload, bytes):
                return payload.decode("utf-8")

        payload = self.read_bytes(key)
        return payload.decode("utf-8")

    def read_bytes(self, key: str) -> bytes:
        read_bytes = _resolve_method(self._store, ("ReadBytes", "read_bytes"))
        if read_bytes is not None:
            payload = read_bytes(key)
            if isinstance(payload, bytes):
                return payload
            if isinstance(payload, str):
                return payload.encode("utf-8")

        read = _resolve_method(self._store, ("Read", "read"))
        if read is not None:
            payload = read(key)
            if isinstance(payload, bytes):
                return payload
            if isinstance(payload, str):
                return payload.encode("utf-8")

        raise ArtifactLoadError(
            "Object Store adapter must expose Read/ReadBytes or read/read_bytes methods."
        )

    def save_json(self, key: str, payload: Mapping[str, Any]) -> None:
        save_json = _resolve_method(self._store, ("SaveJson", "save_json"))
        if save_json is not None:
            save_json(key, dict(payload))
            return
        self.save_text(key, json.dumps(dict(payload)))

    def save_text(self, key: str, payload: str) -> None:
        save = _resolve_method(self._store, ("Save", "save"))
        if save is not None:
            save(key, payload)
            return

        save_bytes = _resolve_method(self._store, ("SaveBytes", "save_bytes"))
        if save_bytes is not None:
            save_bytes(key, payload.encode("utf-8"))
            return

        raise ArtifactLoadError(
            "Object Store adapter must expose Save/SaveBytes or save/save_bytes methods."
        )

    def save_bytes(self, key: str, payload: bytes) -> None:
        save_bytes = _resolve_method(self._store, ("SaveBytes", "save_bytes"))
        if save_bytes is not None:
            save_bytes(key, payload)
            return

        save = _resolve_method(self._store, ("Save", "save"))
        if save is not None:
            try:
                save(key, payload)
                return
            except TypeError:
                save(key, payload.decode("utf-8"))
                return

        raise ArtifactLoadError(
            "Object Store adapter must expose Save/SaveBytes or save/save_bytes methods."
        )

    def get_file_path(self, key: str) -> str | None:
        get_file_path = _resolve_method(self._store, ("GetFilePath", "get_file_path"))
        if get_file_path is None:
            return None

        path = get_file_path(key)
        if path in (None, ""):
            return None
        return str(path)


class ArtifactLoader:
    """
    Service-local EX-001 reference loader.

    The loader now normalizes LEAN Object Store access for both Python and wrapped
    .NET naming styles while keeping the governed behavior testable in Python first:

    - read metadata from canonical Object Store keys
    - validate the promoted-artifact schema
    - enforce paper/live promotion-state boundaries
    - verify the artifact checksum before payload handoff
    """

    def __init__(self, object_store: Any, schema_path: str | Path | None = None):
        self._store = LeanObjectStoreAdapter.from_runtime(object_store)
        self._schema_path = Path(schema_path or self.default_schema_path()).resolve()
        self._schema = _load_schema(self._schema_path)
        self._validator = (
            jsonschema.Draft7Validator(self._schema)
            if jsonschema is not None
            else None
        )

    @staticmethod
    def default_schema_path() -> Path:
        return Path(__file__).resolve().parent / "artifact-loader" / "artifact_metadata_schema.json"

    @classmethod
    def from_runtime(
        cls,
        runtime_or_store: Any,
        schema_path: str | Path | None = None,
    ) -> "ArtifactLoader":
        return cls(runtime_or_store, schema_path=schema_path)

    @staticmethod
    def build_projection(strategy_id: str, version: str) -> ObjectStoreProjection:
        base_key = f"openclaw/registry/{strategy_id}/{version}"
        return ObjectStoreProjection(
            metadata_key=f"{base_key}/metadata.json",
            artifact_key=f"{base_key}/artifact.bin",
        )

    def load(
        self,
        strategy_id: str,
        version: str,
        execution_mode: ExecutionMode | str,
    ) -> LoadedArtifact:
        mode = ExecutionMode(execution_mode)
        projection = self.build_projection(strategy_id, version)
        metadata = self._read_metadata(projection.metadata_key)
        self._validate_metadata(metadata, strategy_id=strategy_id, version=version, mode=mode)

        payload = self._read_bytes(projection.artifact_key)
        self._validate_checksum(metadata["checksum"], payload)
        artifact_path = self._store.get_file_path(projection.artifact_key)
        return LoadedArtifact(
            metadata=metadata,
            payload=payload,
            projection=projection,
            artifact_path=artifact_path,
        )

    def _read_metadata(self, key: str) -> dict[str, Any]:
        if not self._store.contains_key(key):
            raise ArtifactLoadError(f"Object Store metadata key not found: {key}")

        payload = self._store.read_json(key)
        if payload is not None:
            return payload

        text = self._read_text(key)
        try:
            metadata = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ArtifactLoadError(f"Metadata at {key} is not valid JSON.") from exc
        if not isinstance(metadata, dict):
            raise ArtifactLoadError(f"Metadata at {key} must decode to a JSON object.")
        return metadata

    def _read_text(self, key: str) -> str:
        return self._store.read_text(key)

    def _read_bytes(self, key: str) -> bytes:
        if not self._store.contains_key(key):
            raise ArtifactLoadError(f"Object Store artifact key not found: {key}")
        return self._store.read_bytes(key)

    def _validate_metadata(
        self,
        metadata: dict[str, Any],
        *,
        strategy_id: str,
        version: str,
        mode: ExecutionMode,
    ) -> None:
        if self._validator is not None:
            errors = sorted(self._validator.iter_errors(metadata), key=lambda error: list(error.path))
            if errors:
                first = errors[0]
                path = ".".join(str(part) for part in first.path) or "<root>"
                raise ArtifactLoadError(f"Metadata schema validation failed at {path}: {first.message}")

        if metadata.get("strategy_id") != strategy_id:
            raise ArtifactLoadError(
                f"Metadata strategy_id mismatch: expected {strategy_id}, got {metadata.get('strategy_id')}"
            )
        if metadata.get("version") != version:
            raise ArtifactLoadError(
                f"Metadata version mismatch: expected {version}, got {metadata.get('version')}"
            )

        has_canonical_stage = metadata.get("deployment_stage") not in (None, "")
        artifact_state = metadata.get("artifact_state")
        if artifact_state not in (None, "approved"):
            raise ArtifactLoadError(
                "Artifact loader requires artifact_state='approved' for executable metadata; "
                f"got {artifact_state!r}."
            )
        if has_canonical_stage and artifact_state != "approved":
            raise ArtifactLoadError(
                "Artifact loader requires artifact_state='approved' when deployment_stage is present; "
                f"got {artifact_state!r}."
            )

        deployment_stage = metadata.get("deployment_stage") or metadata.get("promotion_state")
        expected_state = mode.value
        if deployment_stage != expected_state:
            raise ArtifactLoadError(
                f"{mode.value} loader rejects deployment_stage={deployment_stage!r}; expected {expected_state!r}."
            )

        if not metadata.get("checksum"):
            raise ArtifactLoadError("Artifact metadata must include checksum before payload load.")

        if mode in {ExecutionMode.CANARY, ExecutionMode.LIVE}:
            rollback = metadata.get("rollback")
            if not isinstance(rollback, dict):
                raise ArtifactLoadError(
                    f"{mode.value} artifact metadata requires an explicit rollback object."
                )
            required = ("target_registry_id", "target_version")
            missing = [field for field in required if rollback.get(field) in (None, "")]
            if missing:
                raise ArtifactLoadError(
                    f"{mode.value} artifact rollback object missing required fields: "
                    + ", ".join(missing)
                )
            if rollback.get("target_registry_id") == metadata.get("registry_id"):
                raise ArtifactLoadError(
                    f"{mode.value} artifact rollback target_registry_id cannot equal registry_id."
                )
            if rollback.get("target_version") == metadata.get("version"):
                raise ArtifactLoadError(
                    f"{mode.value} artifact rollback target_version cannot equal version."
                )

    def _validate_checksum(self, expected_checksum: str, payload: bytes) -> None:
        expected = str(expected_checksum).strip()
        actual_digest = hashlib.sha256(payload).hexdigest()
        actual_candidates = {actual_digest, f"sha256:{actual_digest}"}
        if expected.lower() not in {candidate.lower() for candidate in actual_candidates}:
            raise ArtifactLoadError(
                "Artifact checksum verification failed: "
                f"expected {expected_checksum}, got sha256:{actual_digest}"
            )


def _resolve_method(target: Any, candidates: tuple[str, ...]) -> Any | None:
    for name in candidates:
        method = getattr(target, name, None)
        if callable(method):
            return method
    return None


def materialize_execution_projection(
    runtime_or_store: Any,
    projection: Any,
    payload: bytes,
) -> ObjectStoreProjection:
    """
    Persist a REG-002 execution projection into a LEAN-compatible Object Store.

    `projection` may be an EX-001 `ObjectStoreProjection`, a REG-002 `ExecutionProjection`,
    or any object exposing `metadata_key`, `artifact_key`, and `metadata`.
    """
    metadata_key = getattr(projection, "metadata_key", None)
    artifact_key = getattr(projection, "artifact_key", None)
    metadata = getattr(projection, "metadata", None)

    if not isinstance(metadata_key, str) or not metadata_key:
        raise ArtifactLoadError("Execution projection is missing metadata_key.")
    if not isinstance(artifact_key, str) or not artifact_key:
        raise ArtifactLoadError("Execution projection is missing artifact_key.")
    if not isinstance(metadata, Mapping):
        raise ArtifactLoadError("Execution projection is missing metadata.")

    store = LeanObjectStoreAdapter.from_runtime(runtime_or_store)
    store.save_json(metadata_key, dict(metadata))
    store.save_bytes(artifact_key, payload)
    return ObjectStoreProjection(metadata_key=metadata_key, artifact_key=artifact_key)


def _load_schema(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    resolved = _resolve_local_refs(raw, path.parent)
    if not isinstance(resolved, dict):
        raise ArtifactLoadError(f"Resolved schema at {path} must be a JSON object.")
    return resolved


def _resolve_local_refs(payload: Any, base_dir: Path) -> Any:
    if isinstance(payload, dict):
        ref = payload.get("$ref")
        if isinstance(ref, str):
            ref_path = (base_dir / ref).resolve()
            target = json.loads(ref_path.read_text(encoding="utf-8"))
            return _resolve_local_refs(target, ref_path.parent)
        return {key: _resolve_local_refs(value, base_dir) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_resolve_local_refs(item, base_dir) for item in payload]
    return payload
