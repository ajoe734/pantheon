from __future__ import annotations

from typing import Any

from store import parse_rfc3339

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional dependency in some dev environments
    jsonschema = None


def build_trader_feedback_validator(schema: dict[str, Any]):
    if jsonschema is None:  # pragma: no cover - exercised when dependency missing
        return None

    format_checker = jsonschema.FormatChecker()
    if "date-time" not in format_checker.checkers:
        format_checker.checks("date-time")(lambda value: isinstance(value, str) and parse_rfc3339(value) is not None)

    return jsonschema.Draft7Validator(schema, format_checker=format_checker)
