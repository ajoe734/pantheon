"""Shared schema-registry primitives for governed event payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from .exceptions import FoundationValidationError
from .serialization import drop_none, ensure_utc, sha256_checksum

SCHEMA_REGISTRY_ENTRY_SCHEMA_VERSION = "schema_registry_entry.v1"


class SchemaType(str, Enum):
    JSON_SCHEMA = "json_schema"


@dataclass(frozen=True)
class SchemaRegistryEntry:
    subject: str
    version: int
    owner_service: str
    schema: Mapping[str, Any]
    schema_type: SchemaType | str = SchemaType.JSON_SCHEMA
    registered_at: datetime | str = field(default_factory=lambda: ensure_utc(None))
    checksum: str | None = None
    schema_id: str | None = None
    schema_version: str = SCHEMA_REGISTRY_ENTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("subject", "owner_service", "schema_version"):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"schema_registry.{field_name} is required")
        if int(self.version) < 1:
            raise FoundationValidationError("schema_registry.version must be >= 1")
        if not isinstance(self.schema, Mapping):
            raise FoundationValidationError("schema_registry.schema must be a mapping")
        if isinstance(self.schema_type, SchemaType):
            schema_type = self.schema_type
        else:
            try:
                schema_type = SchemaType(str(self.schema_type))
            except ValueError as exc:
                allowed = ", ".join(item.value for item in SchemaType)
                raise FoundationValidationError(f"schema_type must be one of: {allowed}") from exc
        schema_payload = dict(self.schema)
        checksum = self.checksum or sha256_checksum(schema_payload)
        schema_id = self.schema_id or f"schema-{self.subject.replace('.', '-')}-v{int(self.version)}-{checksum[:12]}"
        object.__setattr__(self, "version", int(self.version))
        object.__setattr__(self, "schema_type", schema_type)
        object.__setattr__(self, "schema", schema_payload)
        object.__setattr__(self, "registered_at", ensure_utc(self.registered_at))
        object.__setattr__(self, "checksum", checksum)
        object.__setattr__(self, "schema_id", schema_id)

    @property
    def schema_ref(self) -> str:
        return f"{self.subject}@v{self.version}"

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "schema_version": self.schema_version,
                "schema_id": self.schema_id,
                "schema_ref": self.schema_ref,
                "subject": self.subject,
                "version": self.version,
                "owner_service": self.owner_service,
                "schema_type": self.schema_type.value,
                "checksum": self.checksum,
                "registered_at": ensure_utc(self.registered_at).isoformat().replace("+00:00", "Z"),
                "schema": dict(self.schema),
            }
        )


@dataclass(frozen=True)
class SchemaValidationResult:
    valid: bool
    schema_ref: str
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "schema_ref": self.schema_ref,
            "errors": list(self.errors),
        }


class SchemaRegistry:
    """In-memory schema registry with deterministic JSON-schema subset validation."""

    def __init__(self, entries: list[SchemaRegistryEntry] | None = None):
        self._entries: dict[tuple[str, int], SchemaRegistryEntry] = {}
        for entry in entries or []:
            self.add(entry)

    def register(
        self,
        *,
        subject: str,
        version: int,
        owner_service: str,
        schema: Mapping[str, Any],
    ) -> SchemaRegistryEntry:
        entry = SchemaRegistryEntry(
            subject=subject,
            version=version,
            owner_service=owner_service,
            schema=schema,
        )
        self.add(entry)
        return entry

    def add(self, entry: SchemaRegistryEntry) -> SchemaRegistryEntry:
        key = (entry.subject, entry.version)
        existing = self._entries.get(key)
        if existing and existing.checksum != entry.checksum:
            raise FoundationValidationError(
                f"schema {entry.schema_ref} already registered with a different checksum"
            )
        self._entries[key] = entry
        return entry

    def resolve(self, schema_ref: str | None = None, *, subject: str | None = None, version: int | None = None) -> SchemaRegistryEntry:
        if schema_ref:
            subject, version = parse_schema_ref(schema_ref)
        if not subject:
            raise FoundationValidationError("schema subject is required")
        if version is None:
            versions = [candidate_version for candidate_subject, candidate_version in self._entries if candidate_subject == subject]
            if not versions:
                raise FoundationValidationError(f"schema subject is not registered: {subject}")
            version = max(versions)
        entry = self._entries.get((subject, int(version)))
        if not entry:
            raise FoundationValidationError(f"schema is not registered: {subject}@v{version}")
        return entry

    def validate(self, schema_ref: str, payload: Mapping[str, Any]) -> SchemaValidationResult:
        entry = self.resolve(schema_ref)
        errors = tuple(_validate_json_schema_subset(entry.schema, payload))
        return SchemaValidationResult(valid=not errors, schema_ref=entry.schema_ref, errors=errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [
                entry.to_dict()
                for entry in sorted(self._entries.values(), key=lambda item: (item.subject, item.version))
            ]
        }


def parse_schema_ref(schema_ref: str) -> tuple[str, int]:
    if "@v" not in str(schema_ref):
        raise FoundationValidationError("schema_ref must use '<subject>@v<version>'")
    subject, raw_version = str(schema_ref).rsplit("@v", 1)
    if not subject.strip():
        raise FoundationValidationError("schema_ref subject is required")
    try:
        version = int(raw_version)
    except ValueError as exc:
        raise FoundationValidationError("schema_ref version must be an integer") from exc
    if version < 1:
        raise FoundationValidationError("schema_ref version must be >= 1")
    return subject, version


def _validate_json_schema_subset(schema: Mapping[str, Any], payload: Any, path: str = "$") -> list[str]:
    errors: list[str] = []
    schema_type = schema.get("type")
    if schema_type and not _matches_json_type(payload, str(schema_type)):
        return [f"{path} expected {schema_type}"]

    if "enum" in schema and payload not in schema["enum"]:
        errors.append(f"{path} must be one of {list(schema['enum'])}")
    if "const" in schema and payload != schema["const"]:
        errors.append(f"{path} must equal {schema['const']!r}")

    if schema_type == "object" or isinstance(payload, Mapping):
        if not isinstance(payload, Mapping):
            return errors
        required = schema.get("required", [])
        for field_name in required:
            if field_name not in payload:
                errors.append(f"{path}.{field_name} is required")
        properties = schema.get("properties", {})
        if isinstance(properties, Mapping):
            for field_name, field_schema in properties.items():
                if field_name in payload and isinstance(field_schema, Mapping):
                    errors.extend(_validate_json_schema_subset(field_schema, payload[field_name], f"{path}.{field_name}"))

    if schema_type == "array" and isinstance(payload, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(payload):
                errors.extend(_validate_json_schema_subset(item_schema, item, f"{path}[{index}]"))

    return errors


def _matches_json_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, Mapping)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True
