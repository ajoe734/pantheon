"""Safe usage/quota snapshots for assistant LLM providers.

Provider CLIs do not expose a stable, cross-provider quota command.  This
module gives the adapter a single sanitized contract for usage data supplied by
the runtime environment or a JSON snapshot file.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ClockFunc = Callable[[], datetime]

_SAFE_USAGE_FIELDS = {
    "status",
    "source",
    "remaining",
    "remaining_percent",
    "limit",
    "used",
    "unit",
    "reset_at",
    "updated_at",
    "checked_at",
    "reason",
}


def provider_usage_snapshot(
    provider_id: str,
    provider_name: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    clock: ClockFunc | None = None,
) -> dict[str, Any]:
    """Return a browser-safe usage snapshot for one assistant provider."""

    env = environ if environ is not None else os.environ
    now = clock or (lambda: datetime.now(timezone.utc))
    checked_at = now().isoformat().replace("+00:00", "Z")
    provider_keys = [provider_id]
    if provider_name and provider_name not in provider_keys:
        provider_keys.append(provider_name)

    env_entry = _usage_from_env(env, provider_keys)
    if env_entry is not None:
        return _normalize_usage(env_entry, checked_at=checked_at, default_source="env")

    file_entry = _usage_from_file(env, provider_keys)
    if file_entry is not None:
        return _normalize_usage(file_entry, checked_at=checked_at, default_source="usage_file")

    return {
        "status": "unknown",
        "source": "not_configured",
        "remaining": None,
        "remainingPercent": None,
        "remaining_percent": None,
        "limit": None,
        "used": None,
        "unit": None,
        "resetAt": None,
        "reset_at": None,
        "updatedAt": None,
        "updated_at": None,
        "checkedAt": checked_at,
        "checked_at": checked_at,
        "reason": "provider_usage_source_not_configured",
    }


def _usage_from_env(env: Mapping[str, str], provider_keys: list[str]) -> dict[str, Any] | None:
    fields = {
        "status": "STATUS",
        "remaining": "REMAINING",
        "remaining_percent": "REMAINING_PERCENT",
        "limit": "LIMIT",
        "used": "USED",
        "unit": "UNIT",
        "reset_at": "RESET_AT",
        "updated_at": "UPDATED_AT",
        "reason": "REASON",
        "source": "SOURCE",
    }
    entry: dict[str, Any] = {}
    for provider in provider_keys:
        token = _env_token(provider)
        prefixes = (
            f"PANTHEON_ASSISTANT_LLM_USAGE_{token}_",
            f"PANTHEON_ASSISTANT_USAGE_{token}_",
        )
        for field, suffix in fields.items():
            for prefix in prefixes:
                value = env.get(f"{prefix}{suffix}")
                if value is not None and value.strip() != "":
                    entry[field] = value.strip()
                    break
            if field in entry:
                continue
    return entry or None


def _usage_from_file(env: Mapping[str, str], provider_keys: list[str]) -> dict[str, Any] | None:
    configured = str(env.get("PANTHEON_ASSISTANT_LLM_USAGE_FILE") or "").strip()
    if not configured:
        return None
    try:
        payload = json.loads(Path(configured).read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": "usage_file",
            "reason": f"usage_file_read_failed:{type(exc).__name__}",
        }
    if not isinstance(payload, Mapping):
        return {
            "status": "unavailable",
            "source": "usage_file",
            "reason": "usage_file_schema_invalid",
        }

    providers = payload.get("providers") if isinstance(payload.get("providers"), Mapping) else payload
    if not isinstance(providers, Mapping):
        return {
            "status": "unavailable",
            "source": "usage_file",
            "reason": "usage_file_providers_invalid",
        }

    for provider in provider_keys:
        entry = providers.get(provider)
        if isinstance(entry, Mapping):
            return dict(entry)
    return {
        "status": "unknown",
        "source": "usage_file",
        "reason": "provider_usage_not_found",
    }


def _normalize_usage(entry: Mapping[str, Any], *, checked_at: str, default_source: str) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    aliases = {
        "remaining_percent": ("remaining_percent", "remainingPercent", "remaining_pct", "remainingPct"),
        "reset_at": ("reset_at", "resetAt", "resets_at", "resetsAt"),
        "updated_at": ("updated_at", "updatedAt"),
        "checked_at": ("checked_at", "checkedAt"),
    }
    for field in _SAFE_USAGE_FIELDS:
        keys = aliases.get(field, (field,))
        for key in keys:
            if key in entry:
                normalized[field] = _coerce_scalar(entry[key])
                break

    normalized.setdefault("source", default_source)
    normalized.setdefault("checked_at", checked_at)
    if "status" not in normalized:
        normalized["status"] = "available" if normalized.get("remaining") is not None else "unknown"
    if "reason" not in normalized and normalized["status"] == "unknown":
        normalized["reason"] = "provider_usage_unknown"

    remaining = _numeric(normalized.get("remaining"))
    limit = _numeric(normalized.get("limit"))
    if "remaining_percent" not in normalized and remaining is not None and limit and limit > 0:
        normalized["remaining_percent"] = round((remaining / limit) * 100, 2)

    normalized["remainingPercent"] = normalized.get("remaining_percent")
    normalized["resetAt"] = normalized.get("reset_at")
    normalized["updatedAt"] = normalized.get("updated_at")
    normalized["checkedAt"] = normalized.get("checked_at")
    return normalized


def _env_token(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")


def _coerce_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value).strip()
    if text == "":
        return None
    numeric = _numeric(text)
    return numeric if numeric is not None else text


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None
