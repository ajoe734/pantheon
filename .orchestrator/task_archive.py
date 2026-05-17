#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def status_root() -> Path:
    raw = str(os.environ.get("PANTHEON_STATUS_ROOT") or "").strip()
    if not raw:
        return ROOT
    return Path(os.path.expanduser(raw)).resolve()


STATUS_ROOT = status_root()
ARCHIVE_DIR = STATUS_ROOT / "ai-task-archive"
ARCHIVE_TASKS_DIR = ARCHIVE_DIR / "tasks"
ARCHIVE_INDEX_FILE = ARCHIVE_DIR / "index.json"

ARCHIVE_VERSION = 1
TERMINAL_STATUS_DONE = "done"
TERMINAL_OUTCOME_COMPLETED = "completed"
TERMINAL_OUTCOME_SUPERSEDED = "superseded"
DEFAULT_RECENT_LIMIT = 20


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return deepcopy(default)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return deepcopy(default)
    return json.loads(text)


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_task_id(task_id: str | None) -> str:
    return str(task_id or "").strip()


def task_status(task: dict[str, Any] | None) -> str:
    if not isinstance(task, dict):
        return ""
    return str(task.get("status") or "").strip().lower()


def terminal_outcome_for(task: dict[str, Any] | None) -> str:
    if not isinstance(task, dict):
        return ""
    outcome = str(task.get("terminal_outcome") or "").strip().lower()
    if outcome:
        return outcome
    if task_status(task) == TERMINAL_STATUS_DONE:
        return TERMINAL_OUTCOME_COMPLETED
    return ""


def is_terminal_task(task: dict[str, Any] | None) -> bool:
    return task_status(task) == TERMINAL_STATUS_DONE


def task_satisfies_dependency(task: dict[str, Any] | None) -> bool:
    return is_terminal_task(task) and terminal_outcome_for(task) != TERMINAL_OUTCOME_SUPERSEDED


def archive_task_path(task_id: str | None) -> Path:
    normalized = normalize_task_id(task_id)
    if not normalized:
        raise ValueError("task_id is required for archive lookup")
    slug = quote(normalized, safe="-_.")
    return ARCHIVE_TASKS_DIR / f"{slug}.json"


def archive_display_path(path: Path) -> str:
    for root in (STATUS_ROOT, ROOT):
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return str(path)


def default_archive_index() -> dict[str, Any]:
    return {
        "version": ARCHIVE_VERSION,
        "updated_at": None,
        "counts": {
            "total": 0,
            TERMINAL_OUTCOME_COMPLETED: 0,
            TERMINAL_OUTCOME_SUPERSEDED: 0,
        },
        "recent_terminal_ids": [],
    }


def load_archive_index() -> dict[str, Any]:
    payload = load_json(ARCHIVE_INDEX_FILE, default_archive_index()) or default_archive_index()
    counts = payload.setdefault("counts", {})
    counts["total"] = int(counts.get("total") or 0)
    counts[TERMINAL_OUTCOME_COMPLETED] = int(counts.get(TERMINAL_OUTCOME_COMPLETED) or 0)
    counts[TERMINAL_OUTCOME_SUPERSEDED] = int(counts.get(TERMINAL_OUTCOME_SUPERSEDED) or 0)
    payload["recent_terminal_ids"] = [
        normalize_task_id(item)
        for item in payload.get("recent_terminal_ids", [])
        if normalize_task_id(item)
    ]
    payload["version"] = ARCHIVE_VERSION
    payload.setdefault("updated_at", None)
    return payload


def save_archive_index(index: dict[str, Any]) -> None:
    payload = deepcopy(index)
    payload["version"] = ARCHIVE_VERSION
    write_json(ARCHIVE_INDEX_FILE, payload)


def load_archived_snapshot(task_id: str | None) -> dict[str, Any] | None:
    normalized = normalize_task_id(task_id)
    if not normalized:
        return None
    path = archive_task_path(normalized)
    snapshot = load_json(path, default=None)
    if not isinstance(snapshot, dict):
        return None
    return snapshot


def load_archived_task(task_id: str | None) -> dict[str, Any] | None:
    snapshot = load_archived_snapshot(task_id)
    if not snapshot:
        return None
    task = snapshot.get("task")
    return deepcopy(task) if isinstance(task, dict) else None


def compact_terminal_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    task = snapshot.get("task") if isinstance(snapshot.get("task"), dict) else {}
    task_id = normalize_task_id(snapshot.get("task_id") or task.get("id"))
    return {
        "task_id": task_id,
        "title": task.get("title"),
        "summary_zh": task.get("summary_zh"),
        "phase": task.get("phase"),
        "owner": task.get("owner"),
        "reviewer": task.get("reviewer"),
        "status": task.get("status"),
        "terminal_outcome": snapshot.get("terminal_outcome") or terminal_outcome_for(task),
        "last_update": task.get("last_update"),
        "archived_at": snapshot.get("archived_at"),
        "next": task.get("next"),
        "snapshot_path": archive_display_path(archive_task_path(task_id)),
    }


def recent_terminal_summaries(limit: int = DEFAULT_RECENT_LIMIT) -> list[dict[str, Any]]:
    index = load_archive_index()
    summaries: list[dict[str, Any]] = []
    for task_id in index.get("recent_terminal_ids", [])[: max(0, int(limit))]:
        snapshot = load_archived_snapshot(task_id)
        if not snapshot:
            continue
        summaries.append(compact_terminal_summary(snapshot))
    return summaries


def rebuild_archive_index(*, recent_limit: int = DEFAULT_RECENT_LIMIT) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    if ARCHIVE_TASKS_DIR.exists():
        for path in sorted(ARCHIVE_TASKS_DIR.glob("*.json")):
            snapshot = load_json(path, default=None)
            if not isinstance(snapshot, dict):
                continue
            task_id = normalize_task_id(snapshot.get("task_id") or ((snapshot.get("task") or {}).get("id")))
            if not task_id:
                continue
            outcome = str(snapshot.get("terminal_outcome") or "").strip().lower() or TERMINAL_OUTCOME_COMPLETED
            archived_at = str(snapshot.get("archived_at") or "").strip()
            summaries.append(
                {
                    "task_id": task_id,
                    "terminal_outcome": outcome,
                    "archived_at": archived_at,
                }
            )

    summaries.sort(key=lambda item: (str(item.get("archived_at") or ""), str(item.get("task_id") or "")), reverse=True)
    index = default_archive_index()
    index["counts"]["total"] = len(summaries)
    index["counts"][TERMINAL_OUTCOME_COMPLETED] = sum(1 for item in summaries if item["terminal_outcome"] == TERMINAL_OUTCOME_COMPLETED)
    index["counts"][TERMINAL_OUTCOME_SUPERSEDED] = sum(1 for item in summaries if item["terminal_outcome"] == TERMINAL_OUTCOME_SUPERSEDED)
    index["recent_terminal_ids"] = [item["task_id"] for item in summaries[: max(0, int(recent_limit))]]
    index["updated_at"] = summaries[0]["archived_at"] if summaries else None
    save_archive_index(index)
    return index


def archive_task_snapshot(
    task: dict[str, Any],
    *,
    handoffs: Iterable[dict[str, Any]] | None = None,
    blockers: Iterable[dict[str, Any]] | None = None,
    archived_at: str | None = None,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
) -> dict[str, Any]:
    if not is_terminal_task(task):
        raise ValueError("Only terminal tasks can be archived")
    task_id = normalize_task_id(task.get("id"))
    if not task_id:
        raise ValueError("Task id is required for archiving")

    existing = load_archived_snapshot(task_id)
    if existing:
        return existing

    archived_at = archived_at or iso_now()
    snapshot = {
        "version": ARCHIVE_VERSION,
        "task_id": task_id,
        "archived_at": archived_at,
        "terminal_status": TERMINAL_STATUS_DONE,
        "terminal_outcome": terminal_outcome_for(task) or TERMINAL_OUTCOME_COMPLETED,
        "task": deepcopy(task),
        "handoffs": deepcopy(list(handoffs or [])),
        "blockers": deepcopy(list(blockers or [])),
    }
    write_json(archive_task_path(task_id), snapshot)

    index = load_archive_index()
    counts = index.setdefault("counts", {})
    counts["total"] = int(counts.get("total") or 0) + 1
    outcome = snapshot["terminal_outcome"]
    counts[TERMINAL_OUTCOME_COMPLETED] = int(counts.get(TERMINAL_OUTCOME_COMPLETED) or 0)
    counts[TERMINAL_OUTCOME_SUPERSEDED] = int(counts.get(TERMINAL_OUTCOME_SUPERSEDED) or 0)
    if outcome in {TERMINAL_OUTCOME_COMPLETED, TERMINAL_OUTCOME_SUPERSEDED}:
        counts[outcome] += 1
    recent_ids = [task_id]
    recent_ids.extend(item for item in index.get("recent_terminal_ids", []) if normalize_task_id(item) and normalize_task_id(item) != task_id)
    index["recent_terminal_ids"] = recent_ids[: max(0, int(recent_limit))]
    index["updated_at"] = archived_at
    save_archive_index(index)
    return snapshot


class TaskResolver:
    def __init__(self, active_tasks: Iterable[dict[str, Any]] | dict[str, dict[str, Any]] | None = None) -> None:
        if isinstance(active_tasks, dict):
            self._active = {
                normalize_task_id(task_id): deepcopy(task)
                for task_id, task in active_tasks.items()
                if normalize_task_id(task_id) and isinstance(task, dict)
            }
        else:
            self._active = {
                normalize_task_id(task.get("id")): deepcopy(task)
                for task in (active_tasks or [])
                if isinstance(task, dict) and normalize_task_id(task.get("id"))
            }
        self._archive_task_cache: dict[str, dict[str, Any] | None] = {}
        self._archive_snapshot_cache: dict[str, dict[str, Any] | None] = {}

    def active_task_map(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._active)

    def source(self, task_id: str | None) -> str | None:
        normalized = normalize_task_id(task_id)
        if not normalized:
            return None
        if normalized in self._active:
            return "active"
        if self.get(normalized) is not None:
            return "archive"
        return None

    def get(self, task_id: str | None) -> dict[str, Any] | None:
        normalized = normalize_task_id(task_id)
        if not normalized:
            return None
        active = self._active.get(normalized)
        if active is not None:
            return deepcopy(active)
        if normalized not in self._archive_task_cache:
            self._archive_task_cache[normalized] = load_archived_task(normalized)
        cached = self._archive_task_cache.get(normalized)
        return deepcopy(cached) if isinstance(cached, dict) else None

    def snapshot(self, task_id: str | None) -> dict[str, Any] | None:
        normalized = normalize_task_id(task_id)
        if not normalized or normalized in self._active:
            return None
        if normalized not in self._archive_snapshot_cache:
            self._archive_snapshot_cache[normalized] = load_archived_snapshot(normalized)
        cached = self._archive_snapshot_cache.get(normalized)
        return deepcopy(cached) if isinstance(cached, dict) else None

    def dependency_satisfied(self, task_id: str | None) -> bool:
        return task_satisfies_dependency(self.get(task_id))

    def dependency_status(self, task_id: str | None) -> str:
        task = self.get(task_id)
        if task is None:
            return "missing"
        status = task_status(task)
        if status == TERMINAL_STATUS_DONE and terminal_outcome_for(task) == TERMINAL_OUTCOME_SUPERSEDED:
            return TERMINAL_OUTCOME_SUPERSEDED
        return status or "missing"
