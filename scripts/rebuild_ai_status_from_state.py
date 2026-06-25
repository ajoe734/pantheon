#!/usr/bin/env python3
"""
Recovery tool: rebuild ai-status.json from git history + .orchestrator/state.json.

Usage:
  python3 scripts/rebuild_ai_status_from_state.py [--dry-run] [--commits N]
                                                   [--status-root PATH]

Addresses the 2026-06-20 incident where a git reset --hard / git clean rolled
back the live ai-status.json to an old HEAD, dragging in:
  - stale task statuses (e.g. DATASTRAT-PERSONA-005 missing 'phase' field)
  - lost runtime task additions not yet committed

Recovery strategy (applied in priority order):
  1. Find the newest committed ai-status.json across the last N git commits and
     use it as the base (avoids using the just-reset stale HEAD).
  2. Overlay task runtime state from .orchestrator/state.json — the supervisor
     keeps task status/owner/reviewer even when ai-status.json is stale.
  3. Validate and fill defaults for required fields that may be missing in old
     committed snapshots: 'phase', 'depends_on', 'reviewer', 'summary_zh'.
  4. Write repaired ai-status.json (or print a diff in --dry-run mode).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _status_root(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    raw = os.environ.get("PANTHEON_STATUS_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _git_root(status_root: Path) -> Path:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(status_root), "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return Path(out.strip())
    except subprocess.CalledProcessError:
        return status_root


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _committed_ai_status_at(git_root: Path, rev: str) -> dict[str, Any] | None:
    """Return ai-status.json content at the given git revision, or None."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(git_root), "show", f"{rev}:ai-status.json"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return json.loads(out)
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
        return None


def _recent_commits(git_root: Path, count: int) -> list[str]:
    """Return the last `count` commit hashes."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(git_root), "log", "--format=%H", f"-{count}"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return [h.strip() for h in out.splitlines() if h.strip()]
    except subprocess.CalledProcessError:
        return []


def _find_newest_committed_state(git_root: Path, max_commits: int) -> dict[str, Any] | None:
    """
    Search the last max_commits commits for ai-status.json and return the
    most recently updated (by 'updated_at') parseable snapshot.
    """
    best: dict[str, Any] | None = None
    best_ts: str = ""
    for rev in _recent_commits(git_root, max_commits):
        snapshot = _committed_ai_status_at(git_root, rev)
        if snapshot is None:
            continue
        ts = str(snapshot.get("updated_at") or "")
        if best is None or ts > best_ts:
            best = snapshot
            best_ts = ts
    return best


# ---------------------------------------------------------------------------
# Orchestrator state overlay
# ---------------------------------------------------------------------------

def _load_orch_state(status_root: Path) -> dict[str, Any]:
    """Load .orchestrator/state.json; return {} on any error."""
    path = status_root / ".orchestrator" / "state.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _overlay_orch_state(tasks: list[dict[str, Any]], orch_state: dict[str, Any]) -> None:
    """
    Update task list in-place using supervisor task records from state.json.

    The supervisor's state.json keeps task status/owner/reviewer in
    state["tasks"][task_id] — these are more current than a reset ai-status.json.
    Fields overlaid: status, owner, reviewer, next, last_update, artifacts.
    Fields NOT overlaid: id, title, phase, depends_on, summary_zh, acceptance
    (those come from the committed ai-status.json which is the canonical source).
    """
    orch_tasks: dict[str, Any] = orch_state.get("tasks", {}) or {}
    if not isinstance(orch_tasks, dict):
        return
    task_map = {str(t.get("id") or ""): t for t in tasks}
    for task_id, record in orch_tasks.items():
        if not isinstance(record, dict):
            continue
        task = task_map.get(str(task_id) or "")
        if task is None:
            continue
        # Only overlay if the orch record has a newer last_update.
        orch_ts = str(record.get("last_update") or "")
        ai_ts = str(task.get("last_update") or "")
        if orch_ts and orch_ts <= ai_ts:
            continue
        for field in ("status", "owner", "reviewer", "next", "last_update"):
            if record.get(field) is not None:
                task[field] = record[field]
        if record.get("artifacts") is not None:
            task["artifacts"] = list(record["artifacts"])


# ---------------------------------------------------------------------------
# Field validation / defaults
# ---------------------------------------------------------------------------

_FIELD_DEFAULTS: dict[str, Any] = {
    "phase": "-",
    "depends_on": [],
    "reviewer": "",
    "summary_zh": "",
    "artifacts": [],
    "acceptance": [],
    "next": "",
}


def _fill_missing_fields(tasks: list[dict[str, Any]]) -> list[str]:
    """
    Fill required fields with safe defaults for tasks that lack them.
    Returns a list of warning messages for any field that was backfilled.
    """
    warnings: list[str] = []
    for task in tasks:
        task_id = task.get("id") or "?"
        for field, default in _FIELD_DEFAULTS.items():
            if field not in task or task[field] is None:
                task[field] = default
                warnings.append(f"  task {task_id}: backfilled missing '{field}' → {default!r}")
    return warnings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild ai-status.json from git history + .orchestrator/state.json."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print recovered JSON to stdout; do not write ai-status.json.",
    )
    parser.add_argument(
        "--commits",
        type=int,
        default=20,
        metavar="N",
        help="Number of recent commits to search for the newest ai-status.json (default: 20).",
    )
    parser.add_argument(
        "--status-root",
        metavar="PATH",
        help="Override PANTHEON_STATUS_ROOT (defaults to env var or repo root).",
    )
    parser.add_argument(
        "--no-orch-overlay",
        action="store_true",
        help="Skip the .orchestrator/state.json overlay step.",
    )
    args = parser.parse_args(argv)

    status_root = _status_root(args.status_root)
    git_root = _git_root(status_root)
    ai_status_path = status_root / "ai-status.json"

    print(f"[rebuild] status-root : {status_root}", file=sys.stderr)
    print(f"[rebuild] git-root    : {git_root}", file=sys.stderr)
    print(f"[rebuild] ai-status   : {ai_status_path}", file=sys.stderr)

    # Step 1: find the best committed snapshot.
    print(f"[rebuild] searching last {args.commits} commits for ai-status.json ...", file=sys.stderr)
    state = _find_newest_committed_state(git_root, args.commits)
    if state is None:
        print("[rebuild] ERROR: no parseable ai-status.json found in recent git history.", file=sys.stderr)
        return 1

    committed_ts = state.get("updated_at", "?")
    task_count = len(state.get("tasks", []))
    print(f"[rebuild] found committed snapshot: updated_at={committed_ts}, tasks={task_count}", file=sys.stderr)

    tasks: list[dict[str, Any]] = state.get("tasks", [])
    if not isinstance(tasks, list):
        tasks = []
        state["tasks"] = tasks

    # Step 2: overlay .orchestrator/state.json runtime task state.
    if not args.no_orch_overlay:
        orch_state = _load_orch_state(status_root)
        if orch_state:
            print("[rebuild] overlaying .orchestrator/state.json task records ...", file=sys.stderr)
            _overlay_orch_state(tasks, orch_state)
        else:
            print("[rebuild] .orchestrator/state.json not found or empty; skipping overlay.", file=sys.stderr)

    # Step 3: fill missing required fields.
    print("[rebuild] validating required fields ...", file=sys.stderr)
    warnings = _fill_missing_fields(tasks)
    for w in warnings:
        print(f"[rebuild] WARN {w}", file=sys.stderr)

    # Stamp recovery time.
    state["updated_at"] = _iso_now()

    # Step 4: write or preview.
    recovered_json = json.dumps(state, ensure_ascii=False, indent=2)

    if args.dry_run:
        print(recovered_json)
        print("[rebuild] dry-run: ai-status.json NOT written.", file=sys.stderr)
    else:
        ai_status_path.write_text(recovered_json + "\n", encoding="utf-8")
        print(f"[rebuild] wrote {ai_status_path} ({len(tasks)} tasks).", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
