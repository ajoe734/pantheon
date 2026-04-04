#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
from typing import Any

from common import append_jsonl, config_path, load_json, load_jsonl, utc_now, write_json


def default_state() -> dict[str, Any]:
    return {
        "version": 2,
        "initialized_at": None,
        "last_scan_at": None,
        "tasks": {},
        "pending_handoff_keys": [],
        "seen_event_keys": {},
        "queue": {
            "events": {},
        },
        "workers": {},
        "approvals": {
            "last_reconciled_at": None,
        },
    }


def migrate_state(raw: dict[str, Any] | None) -> dict[str, Any]:
    state = deepcopy(default_state())
    if not raw:
        return state
    state.update({k: v for k, v in raw.items() if k in state or k in {"queue", "workers", "approvals"}})
    state.setdefault("tasks", {})
    state.setdefault("pending_handoff_keys", [])
    state.setdefault("seen_event_keys", {})
    state.setdefault("queue", {})
    state["queue"].setdefault("events", {})
    state.setdefault("workers", {})
    state.setdefault("approvals", {})
    state["approvals"].setdefault("last_reconciled_at", None)
    state["version"] = 2
    return state


def load_runtime_state(config: dict[str, Any]) -> dict[str, Any]:
    return migrate_state(load_json(config_path(config, "state_file"), default=default_state()))


def save_runtime_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    write_json(config_path(config, "state_file"), migrate_state(state))


def load_event_queue(config: dict[str, Any]) -> list[dict[str, Any]]:
    return load_jsonl(config_path(config, "event_queue"))


def enqueue_event(config: dict[str, Any], event: dict[str, Any]) -> None:
    append_jsonl(config_path(config, "event_queue"), event)


def queue_event_record(state: dict[str, Any], event_id: str) -> dict[str, Any]:
    queue = state.setdefault("queue", {})
    events = queue.setdefault("events", {})
    record = events.setdefault(event_id, {"attempt_count": 0, "status": "queued"})
    return record


def default_approval_state() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "pending": [],
        "history": [],
    }


def load_approval_state(config: dict[str, Any]) -> dict[str, Any]:
    raw = load_json(config_path(config, "approval_queue"), default=default_approval_state())
    state = deepcopy(default_approval_state())
    if isinstance(raw, dict):
        state.update(raw)
    state.setdefault("pending", [])
    state.setdefault("history", [])
    return state


def save_approval_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    payload = deepcopy(state)
    payload["updated_at"] = utc_now()
    write_json(config_path(config, "approval_queue"), payload)
