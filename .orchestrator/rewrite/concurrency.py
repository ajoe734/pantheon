"""Clean concurrency model — Phase 1a (SUPERVISOR_REWRITE_PLAN.md, anti-pattern D).

The incumbent expresses one concept — how many workers an agent may run at once
— two different ways, and `agent_dispatch_capacity()` collapses them by hand:

  * `agents[a].worker_slots`  : a LIST of sub-agent ids (each a real agent with a
                                reciprocal `dispatch_slot_for` back-pointer)
  * `max_tasks_per_agent` (+ `max_tasks_per_agent_by_agent` overrides) : an int

This module computes the same number as a single clean `max_parallel(agent)`,
reimplemented independently (no supervisor import), so `shadow.py` can prove it
equals the incumbent `agent_dispatch_capacity()` for every agent on the live
config before the dispatch loop is ever switched to it.

The target config shape is simply `agents[a].max_parallel` (one int). Until the
config is migrated, `max_parallel()` derives that int from the existing
worker_slots/max_tasks_per_agent fields, which shadow validation confirms is a
faithful, lossless collapse.
"""
from __future__ import annotations

from typing import Any


def normalize_agent_id(value: Any) -> str:
    """Canonical agent id: lower-cased, dash/underscore-folded, trimmed.

    Mirrors the incumbent normalization so slot/override lookups line up.
    """
    return str(value or "").strip().lower().replace("-", "_")


def worker_slot_count(config: dict[str, Any], agent_id: str) -> int:
    """Number of distinct worker slots for an agent.

    Equivalent to `len(logical_worker_slot_ids(...))`: explicit `worker_slots`
    entries that are themselves real agents, unioned with any agent whose
    `dispatch_slot_for` points back at this one. Deduplicated.
    """
    agent = normalize_agent_id(agent_id)
    if not agent:
        return 0
    agents = config.get("agents", {}) or {}
    slots: set[str] = set()
    for raw in (agents.get(agent) or {}).get("worker_slots", []) or []:
        sid = normalize_agent_id(raw)
        if sid and sid in agents:
            slots.add(sid)
    for sid, slot_agent in agents.items():
        if normalize_agent_id((slot_agent or {}).get("dispatch_slot_for")) == agent:
            nid = normalize_agent_id(sid)
            if nid:
                slots.add(nid)
    return len(slots)


def max_parallel(
    config: dict[str, Any],
    agent_id: str,
    *,
    settings: dict[str, Any],
    display_name: str | None = None,
) -> int:
    """How many workers `agent_id` may run concurrently — one clean number.

    Resolution order (identical outcome to `agent_dispatch_capacity`):
      1. explicit target shape `agents[a].max_parallel`, if present;
      2. per-agent override `max_tasks_per_agent_by_agent[agent|display]`;
      3. `max(worker_slot_count, default)` when the agent has worker slots;
      4. the global `max_tasks_per_agent` default, else 1.

    `settings` is the resolved ready-dispatcher settings block (caller supplies
    it, e.g. from the live config); `display_name` is the agent's display name
    used only for override lookup.
    """
    agent = normalize_agent_id(agent_id)

    # (1) target shape: a single int on the agent. Preferred once config migrates.
    agent_cfg = (config.get("agents", {}) or {}).get(agent) or {}
    explicit = agent_cfg.get("max_parallel")
    if explicit not in (None, ""):
        try:
            return max(1, int(explicit))
        except (TypeError, ValueError):
            pass

    # (2) per-agent override wins over everything derived below. Keys checked
    # match the incumbent exactly: normalized agent id, then raw display name.
    overrides = settings.get("max_tasks_per_agent_by_agent", {}) or {}
    for key in (agent, display_name):
        if key and key in overrides:
            try:
                return max(1, int(overrides[key]))
            except (TypeError, ValueError):
                pass

    # (3)/(4) derive from worker_slots + global default.
    raw_default = settings.get("max_tasks_per_agent")
    try:
        default_cap = max(1, int(raw_default)) if raw_default not in (None, "") else None
    except (TypeError, ValueError):
        default_cap = None

    slots = worker_slot_count(config, agent)
    if slots:
        return max(default_cap or 0, slots)
    return default_cap or 1


def account_limit(
    account_id: str,
    *,
    settings: dict[str, Any],
    identity_keys: list[str] | None = None,
) -> int | None:
    """The account concurrency cap — the middle gate of the plan's
    three (global cap → **account cap** → per-agent cap).

    The target `max_concurrent_per_account` setting is keyed only by the explicit
    provider `account`; aliases never participate. `max_concurrent_per_quota_group`
    remains a read-only compatibility surface for old configs and keeps the old
    identity-key lookup order supplied by the caller.

    Returns the cap as an int (>= 0), or None when no cap applies.
    """
    target_shape = "max_concurrent_per_account" in settings
    raw = (
        settings.get("max_concurrent_per_account")
        if target_shape
        else settings.get("max_concurrent_per_quota_group")
    )
    if isinstance(raw, dict):
        keys = (
            [account_id]
            if target_shape
            else identity_keys if identity_keys is not None else ([account_id] if account_id else [])
        )
        for key in dict.fromkeys(k for k in keys if k):
            if key in raw:
                try:
                    return max(0, int(raw[key]))
                except (TypeError, ValueError):
                    return None
        return None
    if raw in (None, ""):
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None
