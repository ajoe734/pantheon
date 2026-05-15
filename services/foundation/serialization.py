"""Deterministic serialization helpers for foundation primitives."""

from __future__ import annotations

import hashlib
import json
from dataclasses import is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from .exceptions import FoundationValidationError


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def ensure_utc(value: datetime | str | None) -> datetime:
    """Normalize a timestamp to UTC and reject naive datetimes."""
    if value is None:
        return utc_now()
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        value = datetime.fromisoformat(normalized)
    if value.tzinfo is None:
        raise FoundationValidationError("datetime values must be timezone-aware")
    return value.astimezone(timezone.utc)


def isoformat_z(value: datetime | str | None) -> str:
    """Serialize a timestamp as RFC3339 UTC with a trailing Z."""
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def foundation_id(prefix: str) -> str:
    """Create a stable-looking Pantheon id with a required prefix."""
    clean_prefix = str(prefix).strip()
    if not clean_prefix:
        raise FoundationValidationError("id prefix is required")
    return f"{clean_prefix}-{uuid4().hex}"


def drop_none(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Drop None values from a mapping without mutating the input."""
    return {key: value for key, value in payload.items() if value is not None}


def serialize_value(value: Any) -> Any:
    """Convert supported Python values into JSON-compatible values."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return isoformat_z(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if is_dataclass(value):
        return {
            key: serialize_value(item)
            for key, item in value.__dict__.items()
            if not key.startswith("_") and item is not None
        }
    if isinstance(value, Mapping):
        return {
            str(key): serialize_value(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, tuple | list | set):
        return [serialize_value(item) for item in value]
    return value


def canonical_json(payload: Any) -> str:
    """Return deterministic compact JSON for hashing and audit payloads."""
    return json.dumps(
        serialize_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def sha256_checksum(payload: Any) -> str:
    """Return a SHA-256 checksum over canonical JSON."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
