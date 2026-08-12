"""Authoritative concurrency model.

Agent capacity is one explicit integer, ``agents.<id>.max_parallel``.  Worker
slots describe physical delivery topology only and never infer policy.
Account capacity is read only from the provider's explicit ``account``.
"""
from __future__ import annotations

from typing import Any


def normalize_agent_id(value: Any) -> str:
    """Canonical agent id: lower-cased, dash/underscore-folded, trimmed.

    Shared normalization for slot and override lookups.
    """
    return str(value or "").strip().lower().replace("-", "_")


def max_parallel(
    config: dict[str, Any],
    agent_id: str,
    *,
    settings: dict[str, Any],
    display_name: str | None = None,
) -> int:
    """Return the one configured per-agent capacity; missing/invalid is closed."""
    agent = normalize_agent_id(agent_id)

    agent_cfg = (config.get("agents", {}) or {}).get(agent) or {}
    explicit = agent_cfg.get("max_parallel")
    try:
        return max(0, int(explicit))
    except (TypeError, ValueError):
        return 0


def account_limit(
    account_id: str,
    *,
    settings: dict[str, Any],
) -> int | None:
    """The account concurrency cap — the middle gate of the plan's
    three (global cap → **account cap** → per-agent cap).

    `max_concurrent_per_account` is keyed only by the explicit provider
    `account`; aliases never participate.

    Returns the cap as an int (>= 0). Missing or malformed authority is a
    closed zero-capacity lane; startup validation reports the schema error.
    """
    raw = settings.get("max_concurrent_per_account")
    if isinstance(raw, dict):
        normalized_limits = {
            normalize_agent_id(key): value for key, value in raw.items()
        }
        normalized_account = normalize_agent_id(account_id)
        if normalized_account in normalized_limits:
            try:
                return max(0, int(normalized_limits[normalized_account]))
            except (TypeError, ValueError):
                return 0
        return 0
    return 0
