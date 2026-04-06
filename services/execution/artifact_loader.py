from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:  # pragma: no cover - jsonschema is expected in local validation flows
    jsonschema = None


class ArtifactLoadError(ValueError):
    """Raised when governed artifact metadata or payload loading fails."""


class ExecutionMode(str, Enum):
    PAPER = "paper"
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


class ArtifactLoader:
    """
    Service-local EX-001 reference loader.

    The real LEAN bridge still needs a runtime-side adapter around ObjectStore.Read/ReadBytes.
    This loader keeps the governed behavior testable in Python first:

    - read metadata from canonical Object Store keys
    - validate the promoted-artifact schema
    - enforce paper/live promotion-state boundaries
    - verify the artifact checksum before payload handoff
    """

    def __init__(self, object_store: Any, schema_path: str | Path | None = None):
        self._store = object_store
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
        return LoadedArtifact(metadata=metadata, payload=payload, projection=projection)

    def _read_metadata(self, key: str) -> dict[str, Any]:
        if not self._contains_key(key):
            raise ArtifactLoadError(f"Object Store metadata key not found: {key}")

        read_json = _resolve_method(self._store, ("ReadJson", "read_json"))
        if read_json is not None:
            payload = read_json(key)
            if isinstance(payload, dict):
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
        read = _resolve_method(self._store, ("Read", "read"))
        if read is not None:
            payload = read(key)
            if isinstance(payload, str):
                return payload
            if isinstance(payload, bytes):
                return payload.decode("utf-8")

        payload = self._read_bytes(key)
        return payload.decode("utf-8")

    def _read_bytes(self, key: str) -> bytes:
        if not self._contains_key(key):
            raise ArtifactLoadError(f"Object Store artifact key not found: {key}")

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

    def _contains_key(self, key: str) -> bool:
        contains = _resolve_method(self._store, ("ContainsKey", "contains_key"))
        if contains is not None:
            return bool(contains(key))
        try:
            self._read_bytes_without_guard(key)
            return True
        except ArtifactLoadError:
            return False

    def _read_bytes_without_guard(self, key: str) -> bytes:
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

        raise ArtifactLoadError(f"Object Store key not readable: {key}")

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

        promotion_state = metadata.get("promotion_state")
        expected_state = mode.value
        if promotion_state != expected_state:
            raise ArtifactLoadError(
                f"{mode.value} loader rejects promotion_state={promotion_state!r}; expected {expected_state!r}."
            )

        if not metadata.get("checksum"):
            raise ArtifactLoadError("Artifact metadata must include checksum before payload load.")

        if mode is ExecutionMode.LIVE:
            rollback = metadata.get("rollback")
            if not isinstance(rollback, dict):
                raise ArtifactLoadError("Live artifact metadata requires an explicit rollback object.")
            required = ("target_registry_id", "target_version")
            missing = [field for field in required if rollback.get(field) in (None, "")]
            if missing:
                raise ArtifactLoadError(
                    "Live artifact rollback object missing required fields: " + ", ".join(missing)
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
