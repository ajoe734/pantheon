"""Orchestrator status readback for the assistant (ASST-INTEG-007).

This service reads ai-status.json and .orchestrator/state.json to provide
a unified view of the project state and worker activity.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import OrchestratorStatusResponse, OrchestratorTaskStatus, OrchestratorWorkerStatus


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _find_repo_root(start: Optional[str] = None) -> Path:
    """Walk up from *start* to find the Pantheon repo root."""
    if start:
        candidate = Path(start)
    else:
        env = os.environ.get("PANTHEON_STATUS_ROOT")
        candidate = Path(env) if env else Path(__file__).resolve()
    candidate = candidate if candidate.is_dir() else candidate.parent
    for _ in range(12):
        if (candidate / "ai-status.json").exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return Path(start or os.getcwd())


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _source_ref(root: Path, path: Path, source_type: str) -> Dict[str, Any]:
    try:
        rel_path = path.relative_to(root).as_posix()
    except ValueError:
        rel_path = path.as_posix()
    return {
        "sourceType": source_type,
        "path": rel_path,
        "available": path.exists(),
    }


def read_orchestrator_status(repo_root: Optional[str] = None) -> OrchestratorStatusResponse:
    root = _find_repo_root(repo_root)

    ai_status_path = root / "ai-status.json"
    state_path = root / ".orchestrator" / "state.json"
    bus_state_path = root / ".orchestrator" / "github-bus-state.json"
    source_refs = [
        _source_ref(root, ai_status_path, "task_status"),
        _source_ref(root, state_path, "worker_runtime"),
        _source_ref(root, bus_state_path, "github_bus"),
    ]

    ai_status = _load_json(ai_status_path)
    state = _load_json(state_path)
    bus_state = _load_json(bus_state_path)

    bus_tasks = bus_state.get("tasks", {})

    tasks: List[OrchestratorTaskStatus] = []
    for t in ai_status.get("tasks", []):
        task_id = t.get("id", "")
        brief_path = f".orchestrator/task-briefs/{task_id.lower().replace('-', '_')}.md"
        if not (root / brief_path).exists():
            brief_path = None

        # Merge GitHub bus info
        delivery = t.get("delivery") or {}
        bus_entry = bus_tasks.get(task_id, {})
        if bus_entry:
            delivery["github_bus"] = bus_entry

        tasks.append(OrchestratorTaskStatus(
            id=task_id,
            title=t.get("title", ""),
            owner=t.get("owner", ""),
            reviewer=t.get("reviewer", ""),
            status=t.get("status", "todo"),
            phase=t.get("phase"),
            next=t.get("next"),
            last_update=t.get("last_update"),
            depends_on=t.get("depends_on", []),
            artifacts=t.get("artifacts", []),
            acceptance=t.get("acceptance", []),
            summary_zh=t.get("summary_zh"),
            waiting_for=t.get("waiting_for"),
            brief_path=brief_path,
            delivery=delivery if delivery else None
        ))

    workers: List[OrchestratorWorkerStatus] = []
    for run_id, w in state.get("workers", {}).items():
        workers.append(OrchestratorWorkerStatus(
            runId=run_id,
            taskId=w.get("task_id"),
            agent=w.get("agent_id") or w.get("agent", "unknown"),
            status=w.get("status", "unknown"),
            startedAt=w.get("started_at"),
            lastEventAt=w.get("last_event_at"),
            lastError=w.get("last_error")
        ))

    return OrchestratorStatusResponse(
        snapshotAt=_now(),
        project=ai_status.get("project", "unknown"),
        sprint=ai_status.get("sprint", "unknown"),
        objective=ai_status.get("objective", ""),
        sourceRefs=source_refs,
        tasks=tasks,
        workers=workers,
        handoffs=ai_status.get("handoffs", []),
        blockers=ai_status.get("blockers", []),
        supervisor=state.get("supervisor", {}),
        coordination=state.get("coordination")
    )
