"""Automatic model rotation for antigravity-style providers.

An antigravity provider can be configured to rotate between a *primary* model
(the CLI default, typically Gemini) and a *fallback* model (typically a Claude
model reachable through the same CLI). When the model currently in use runs out
of quota / capacity, the supervisor puts that model on a short cooldown and lets
the adapter switch to the other model on the next dispatch instead of pausing the
whole provider. Only when *both* models are cooling does the provider fall back
to the normal dispatch pause. When a cooldown expires the provider automatically
returns to its primary model, so the fleet self-heals in a loop.

The cooldown state lives in a small shared JSON file next to the runtime state so
that both the supervisor (which writes cooldowns on failure) and the adapter
(which reads them at dispatch time) resolve the same model selection.

State file shape::

    {
      "antigravity":  {"gemini_until": "2026-07-11T10:00:00Z", "claude_until": null},
      "antigravity2": {"gemini_until": null, "claude_until": null}
    }

``gemini_until`` guards the primary slot and ``claude_until`` guards the fallback
slot; a slot is "cooling" while its ``*_until`` timestamp is in the future.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from common import config_path, normalize_agent_id

COOLDOWN_FILENAME = "model-rotation-cooldowns.json"
DEFAULT_COOLDOWN_SECONDS = 900
MIN_COOLDOWN_SECONDS = 60

SLOT_PRIMARY = "primary"
SLOT_FALLBACK = "fallback"

# Per the provider design the primary slot is the CLI default (Gemini) and the
# fallback slot is Claude; the state-file keys keep those human-readable names.
_SLOT_UNTIL_KEY = {SLOT_PRIMARY: "gemini_until", SLOT_FALLBACK: "claude_until"}

# Failure kinds that indicate the *model* is exhausted (as opposed to an
# account-level auth problem, which rotating the model cannot fix).
ROTATION_ELIGIBLE_FAILURE_KINDS = frozenset({"quota_terminal", "capacity_retryable"})


def cooldown_file_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").parent / COOLDOWN_FILENAME


def resolve_provider_entry(config: dict[str, Any] | None, provider_id: str | None) -> tuple[str, dict[str, Any]]:
    """Resolve a provider config entry regardless of the id form used by the caller.

    Provider ids reach this module in two spellings: config-key form (``antigravity1-1``)
    from the dispatch adapters and ``normalize_agent_id`` form (``antigravity1_1``) from
    the supervisor failure path. Config keys themselves may use either. Try the exact key
    first, then match on the normalized form so both callers resolve the same entry.
    """
    providers = ((config or {}).get("providers", {}) or {})
    raw_id = str(provider_id or "").strip()
    if not raw_id:
        return "", {}
    entry = providers.get(raw_id)
    if isinstance(entry, dict):
        return raw_id, entry
    wanted = normalize_agent_id(raw_id)
    if wanted:
        for key, value in providers.items():
            if isinstance(value, dict) and normalize_agent_id(str(key)) == wanted:
                return str(key), value
    return "", {}


def rotation_state_key(config: dict[str, Any] | None, provider_id: str | None) -> str:
    """Return the cooldown-state key shared by every provider on the same account.

    Model quota is account-level, so when one dispatch slot (``antigravity1-3``)
    exhausts a model every sibling slot on that account is exhausted too. Keying the
    cooldown state by the provider's ``account`` makes one failure rotate the whole
    group instead of requiring each slot to fail once.
    """
    key, entry = resolve_provider_entry(config, provider_id)
    account = str(entry.get("account") or "").strip() if isinstance(entry, dict) else ""
    return account or key or normalize_agent_id(provider_id or "")


def rotation_settings(config: dict[str, Any] | None, provider_id: str | None) -> dict[str, Any]:
    """Return the normalized model_rotation block for a provider."""
    _, provider = resolve_provider_entry(config, provider_id)
    raw = provider.get("model_rotation") if isinstance(provider, dict) else None
    if not isinstance(raw, dict):
        return {"enabled": False, "primary": "", "fallback": "", "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS}
    try:
        cooldown_seconds = int(raw.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS)
    except (TypeError, ValueError):
        cooldown_seconds = DEFAULT_COOLDOWN_SECONDS
    return {
        "enabled": bool(raw.get("enabled", False)),
        # empty primary => keep the CLI default model (no --model override)
        "primary": str(raw.get("primary") or "").strip(),
        "fallback": str(raw.get("fallback") or "").strip(),
        "cooldown_seconds": max(MIN_COOLDOWN_SECONDS, cooldown_seconds),
    }


def rotation_enabled(config: dict[str, Any] | None, provider_id: str | None) -> bool:
    return bool(rotation_settings(config, provider_id).get("enabled"))


def model_for_slot(settings: dict[str, Any], slot: str) -> str:
    if slot == SLOT_FALLBACK:
        return str(settings.get("fallback") or "").strip()
    return str(settings.get("primary") or "").strip()


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_all(config: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(cooldown_file_path(config).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_all(config: dict[str, Any], data: dict[str, Any]) -> None:
    path = cooldown_file_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _provider_entry(config: dict[str, Any], provider_id: str) -> dict[str, Any]:
    entry = _load_all(config).get(rotation_state_key(config, provider_id))
    return entry if isinstance(entry, dict) else {}


def slot_cooling(config: dict[str, Any], provider_id: str, slot: str, now: datetime | None = None) -> bool:
    until = _parse(_provider_entry(config, provider_id).get(_SLOT_UNTIL_KEY[slot]))
    return until is not None and until > _now(now)


def active_slot(config: dict[str, Any], provider_id: str, now: datetime | None = None) -> str | None:
    """Return the slot the adapter should dispatch with, or ``None`` if both cool.

    Primary is preferred whenever it is available; the fallback is only used
    while the primary is cooling. This is the single source of truth shared by
    the adapter (to pick a model) and the supervisor (to know which model is in
    use when a failure arrives).
    """
    now = _now(now)
    if not slot_cooling(config, provider_id, SLOT_PRIMARY, now):
        return SLOT_PRIMARY
    if not slot_cooling(config, provider_id, SLOT_FALLBACK, now):
        return SLOT_FALLBACK
    return None


def cool_slot(
    config: dict[str, Any],
    provider_id: str,
    slot: str,
    *,
    seconds: int | None = None,
    now: datetime | None = None,
) -> str:
    """Put ``slot`` on cooldown and return the ISO timestamp it is blocked until."""
    now = _now(now)
    settings = rotation_settings(config, provider_id)
    duration = int(seconds if seconds is not None else settings.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS))
    until = (now + timedelta(seconds=max(MIN_COOLDOWN_SECONDS, duration))).replace(microsecond=0)
    until_iso = until.isoformat().replace("+00:00", "Z")
    data = _load_all(config)
    state_key = rotation_state_key(config, provider_id)
    entry = data.get(state_key)
    if not isinstance(entry, dict):
        entry = {}
        data[state_key] = entry
    entry[_SLOT_UNTIL_KEY[slot]] = until_iso
    _save_all(config, data)
    return until_iso
