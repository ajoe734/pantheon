"""Shared decoder for committed ``telemetry_events`` payloads."""

from __future__ import annotations

import json
from typing import Any


def decode_event_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):  # asyncpg returns jsonb as JSON text by default
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ValueError("telemetry event payload is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("telemetry event payload is not a JSON object")
    return value
