from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional dependency in some environments
    jsonschema = None


def _parse_rfc3339(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _schema_path(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "specs" / name


def build_workflow_handoff_validator():
    if jsonschema is None:  # pragma: no cover - exercised when dependency missing
        return None

    schema_path = _schema_path("workflow_handoff.schema.json")
    strategy_path = _schema_path("strategy_spec.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    strategy_schema = json.loads(strategy_path.read_text(encoding="utf-8"))
    store = {
        schema_path.resolve().as_uri(): schema,
        strategy_path.resolve().as_uri(): strategy_schema,
        schema.get("$id"): schema,
        strategy_schema.get("$id"): strategy_schema,
        "https://pantheon/workflow-handoff/strategy_spec.schema.json": strategy_schema,
    }
    resolver = jsonschema.RefResolver(base_uri=schema_path.resolve().as_uri(), referrer=schema, store=store)
    format_checker = jsonschema.FormatChecker()
    if "date-time" not in format_checker.checkers:
        format_checker.checks("date-time")(lambda value: isinstance(value, str) and _parse_rfc3339(value) is not None)
    return jsonschema.Draft7Validator(schema, resolver=resolver, format_checker=format_checker)


def validate_workflow_handoff(payload: dict[str, Any]) -> None:
    validator = build_workflow_handoff_validator()
    if validator is None:  # pragma: no cover - optional dependency fallback
        required = {
            "handoff_version",
            "handoff_id",
            "handoff_type",
            "from_stage",
            "to_stage",
            "created_at",
            "strategy_spec",
            "registry_hints",
            "governance_context",
            "provenance",
        }
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError(f"WorkflowHandoff missing required fields: {missing}")
        if _parse_rfc3339(payload["created_at"]) is None:
            raise ValueError("WorkflowHandoff created_at must be RFC3339")
        return

    validator.validate(payload)
