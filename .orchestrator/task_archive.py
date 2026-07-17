#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from common import canonical_task_state_lock_file, write_json as durable_write_json

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
STATUS_FILE = STATUS_ROOT / "ai-status.json"

ARCHIVE_VERSION = 1
TERMINAL_STATUS_DONE = "done"
TERMINAL_OUTCOME_COMPLETED = "completed"
TERMINAL_OUTCOME_SUPERSEDED = "superseded"
DEFAULT_RECENT_LIMIT = 20


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _archive_fault(point: str) -> None:
    if str(os.environ.get("LOOP_TEST_ARCHIVE_SIGKILL_AFTER") or "").strip() == point:
        os.kill(os.getpid(), 9)


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
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=False,
        nonblocking=False,
    ):
        durable_write_json(path, payload)


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


def archive_tasks_dir_for_status_root(status_root: str | Path) -> Path:
    return Path(status_root).expanduser().resolve() / "ai-task-archive" / "tasks"


def archive_task_path_in_dir(task_id: str | None, archive_tasks_dir: str | Path) -> Path:
    normalized = normalize_task_id(task_id)
    if not normalized:
        raise ValueError("task_id is required for archive lookup")
    slug = quote(normalized, safe="-_.")
    return Path(archive_tasks_dir).expanduser().resolve() / f"{slug}.json"


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
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=True,
        nonblocking=False,
    ):
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
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=True,
        nonblocking=False,
    ):
        snapshot = load_json(path, default=None)
    if not isinstance(snapshot, dict):
        return None
    return snapshot


def load_archived_task(task_id: str | None) -> dict[str, Any] | None:
    snapshot = load_archived_snapshot(task_id)
    if not snapshot:
        return None
    return task_from_archive_snapshot(snapshot)


def task_from_archive_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize modern nested and legacy top-level archive task shapes."""

    nested = snapshot.get("task")
    if isinstance(nested, dict):
        return deepcopy(nested)
    task_id = normalize_task_id(snapshot.get("task_id") or snapshot.get("id"))
    if not task_id:
        return None
    task = deepcopy(snapshot)
    task["id"] = task_id
    task.pop("task_id", None)
    task.pop("archived_at", None)
    task.pop("version", None)
    task.pop("terminal_status", None)
    task.pop("handoffs", None)
    task.pop("blockers", None)
    task.setdefault(
        "status",
        snapshot.get("terminal_status")
        or (TERMINAL_STATUS_DONE if snapshot.get("terminal_outcome") else None),
    )
    if not task.get("status"):
        return None
    return task


def compact_terminal_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    task = task_from_archive_snapshot(snapshot) or {}
    task_id = normalize_task_id(
        snapshot.get("task_id") or snapshot.get("id") or task.get("id")
    )
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
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=True,
        nonblocking=False,
    ):
        index = load_archive_index()
        summaries: list[dict[str, Any]] = []
        for task_id in index.get("recent_terminal_ids", [])[: max(0, int(limit))]:
            snapshot = load_archived_snapshot(task_id)
            if not snapshot:
                continue
            summaries.append(compact_terminal_summary(snapshot))
        return summaries


def _rebuild_archive_index_locked(*, recent_limit: int = DEFAULT_RECENT_LIMIT) -> dict[str, Any]:
    import subprocess
    summaries: list[dict[str, Any]] = []
    if ARCHIVE_TASKS_DIR.exists():
        try:
            res = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", "HEAD", "ai-task-archive/tasks/"],
                cwd=STATUS_ROOT,
                capture_output=True,
                text=True,
                check=False
            )
            if res.returncode == 0:
                committed_relative_paths = [line.strip() for line in res.stdout.splitlines() if line.strip() and line.strip().endswith(".json")]
                tracked_paths = [STATUS_ROOT / p for p in committed_relative_paths]
                tracked_paths = [p for p in tracked_paths if p.exists()]
            else:
                tracked_paths = sorted(ARCHIVE_TASKS_DIR.glob("*.json"))
        except Exception:
            tracked_paths = sorted(ARCHIVE_TASKS_DIR.glob("*.json"))

        for path in tracked_paths:
            snapshot = load_json(path, default=None)
            if not isinstance(snapshot, dict):
                continue
            # Resolve the task id across all archive snapshot schema variants.
            # Legacy entries store the id at the top level as ``id`` (not
            # ``task_id`` / nested ``task.id``); without this fallback such files
            # resolve to None and are silently excluded from the index forever.
            task_id = normalize_task_id(
                snapshot.get("task_id")
                or ((snapshot.get("task") or {}).get("id"))
                or snapshot.get("id")
            )
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


def rebuild_archive_index(*, recent_limit: int = DEFAULT_RECENT_LIMIT) -> dict[str, Any]:
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=False,
        nonblocking=False,
    ):
        return _rebuild_archive_index_locked(recent_limit=recent_limit)


def _archive_task_snapshot_locked(
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
    archived_at = archived_at or (
        str(existing.get("archived_at") or "").strip()
        if isinstance(existing, dict)
        else ""
    ) or iso_now()
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
    if existing:
        if existing != snapshot:
            raise RuntimeError(
                f"existing archive snapshot conflicts with terminal task: {task_id}"
            )
        # An exact snapshot may have survived a crash before the index write.
        _rebuild_archive_index_locked(recent_limit=recent_limit)
        return existing
    write_json(archive_task_path(task_id), snapshot)
    _archive_fault("snapshot")

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
    _archive_fault("index")
    return snapshot


def archive_task_snapshot(
    task: dict[str, Any],
    *,
    handoffs: Iterable[dict[str, Any]] | None = None,
    blockers: Iterable[dict[str, Any]] | None = None,
    archived_at: str | None = None,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
) -> dict[str, Any]:
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=False,
        nonblocking=False,
    ):
        return _archive_task_snapshot_locked(
            task,
            handoffs=handoffs,
            blockers=blockers,
            archived_at=archived_at,
            recent_limit=recent_limit,
        )


class TaskResolver:
    def __init__(
        self,
        active_tasks: Iterable[dict[str, Any]] | dict[str, dict[str, Any]] | None = None,
        *,
        status_root: str | Path | None = None,
        archive_tasks_dir: str | Path | None = None,
    ) -> None:
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
        if archive_tasks_dir is not None:
            self._archive_tasks_dir = Path(archive_tasks_dir).expanduser().resolve()
        elif status_root is not None:
            self._archive_tasks_dir = archive_tasks_dir_for_status_root(status_root)
        else:
            self._archive_tasks_dir = None
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
            self._archive_task_cache[normalized] = self._load_archived_task(normalized)
        cached = self._archive_task_cache.get(normalized)
        return deepcopy(cached) if isinstance(cached, dict) else None

    def snapshot(self, task_id: str | None) -> dict[str, Any] | None:
        normalized = normalize_task_id(task_id)
        if not normalized or normalized in self._active:
            return None
        if normalized not in self._archive_snapshot_cache:
            self._archive_snapshot_cache[normalized] = self._load_archived_snapshot(normalized)
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

    def _load_archived_snapshot(self, task_id: str | None) -> dict[str, Any] | None:
        if self._archive_tasks_dir is None:
            return load_archived_snapshot(task_id)
        normalized = normalize_task_id(task_id)
        if not normalized:
            return None
        snapshot = load_json(archive_task_path_in_dir(normalized, self._archive_tasks_dir), default=None)
        return snapshot if isinstance(snapshot, dict) else None

    def _load_archived_task(self, task_id: str | None) -> dict[str, Any] | None:
        snapshot = self._load_archived_snapshot(task_id)
        if not snapshot:
            return None
        return task_from_archive_snapshot(snapshot)
