#!/usr/bin/env python3
"""Completion guardrail checker for loop-autopilot tasks.

Reads ai-status.json and reports which loop-autopilot tasks have closure
evidence gaps that would cause the 'done' transition to be rejected.

Exit codes:
  0 — all scanned tasks have sufficient evidence (or there are none)
  1 — one or more tasks have evidence gaps

Usage:
    python3 scripts/loop_done_guardrail.py [--task-id TASK_ID] [--status-file PATH]

Examples:
    # Check every loop-autopilot task in ai-status.json
    python3 scripts/loop_done_guardrail.py

    # Check a specific task
    python3 scripts/loop_done_guardrail.py --task-id LOOP-AUTO-002

    # Check against a non-default status file
    python3 scripts/loop_done_guardrail.py --status-file /path/to/ai-status.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS_FILE = ROOT / "ai-status.json"

# Canonical non-goals that trigger loop guardrail checks.
LOOP_AUTOPILOT_NON_GOALS = {
    "No panel-only closure",
    "No seed fixture as live proof",
    "No approval gate bypass",
}

# Case-insensitive substrings that indicate a panel/fixture/route-only claim.
_FIXTURE_ONLY_SIGNALS = (
    "fixture only",
    "fixture-only",
    "fixture_only",
    "seed only",
    "seed-only",
    "seed_only",
    "seed fixture as proof",
    "seed fixture as live",
    "fixture as live proof",
    "panel only",
    "panel-only",
    "panel_only",
    "panel copy",
    "route only",
    "route-only",
    "route_only",
)


def is_loop_autopilot_task(task: dict[str, Any]) -> bool:
    """Return True when the task carries loop-autopilot guardrail requirements."""
    if task.get("loop_ids"):
        return True
    non_goals: list[str] = task.get("non_goals") or []
    return bool(set(non_goals) & LOOP_AUTOPILOT_NON_GOALS)


def check_task(task: dict[str, Any]) -> list[str]:
    """Return a list of evidence gap descriptions for the task.

    An empty list means the task passes all guardrail checks.
    """
    if not is_loop_autopilot_task(task):
        return []

    gaps: list[str] = []
    non_goals: set[str] = set(task.get("non_goals") or [])
    proof_required: list[str] = task.get("proof_required") or []
    review_file = str(task.get("review_file") or "").strip()

    # Gap 1: panel-only closure prohibited but no review_file.
    if "No panel-only closure" in non_goals and not review_file:
        gaps.append(
            "non_goal 'No panel-only closure' requires a review_file with controller "
            "liveness evidence (set REVIEW_FILE=<evidence-path> during approve)"
        )

    # Gap 2: fixture/seed signals in review notes.
    if "No seed fixture as live proof" in non_goals:
        review_notes: list[str] = task.get("review_notes_zh") or []
        if isinstance(review_notes, str):
            review_notes = [review_notes]
        combined = " ".join(str(n) for n in review_notes).lower()
        flagged = next((sig for sig in _FIXTURE_ONLY_SIGNALS if sig in combined), None)
        if flagged:
            gaps.append(
                f"review notes contain '{flagged}' which violates "
                "'No seed fixture as live proof'"
            )

    # Gap 3: proof_required listed but no review_file to link evidence.
    if proof_required and not review_file:
        sample = ", ".join(f'"{p}"' for p in proof_required[:2])
        suffix = " ..." if len(proof_required) > 2 else ""
        gaps.append(
            f"proof_required ({sample}{suffix}) but no review_file was recorded — "
            "reviewer must set REVIEW_FILE=<evidence-path> during approve"
        )

    return gaps


def load_status(status_file: Path) -> dict[str, Any]:
    try:
        with open(status_file, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        print(f"ERROR: status file not found: {status_file}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"ERROR: cannot parse status file: {exc}", file=sys.stderr)
        sys.exit(2)


def run(status_file: Path, task_id: str | None) -> int:
    state = load_status(status_file)
    tasks: list[dict[str, Any]] = state.get("tasks") or []

    if task_id:
        matched = [t for t in tasks if t.get("id") == task_id]
        if not matched:
            print(f"ERROR: task '{task_id}' not found in {status_file}", file=sys.stderr)
            return 2
        tasks = matched

    loop_tasks = [t for t in tasks if is_loop_autopilot_task(t)]

    if not loop_tasks:
        target = f"'{task_id}'" if task_id else "any task"
        print(f"No loop-autopilot tasks matched ({target}). Nothing to check.")
        return 0

    fail_count = 0
    for task in loop_tasks:
        tid = task.get("id", "?")
        status = task.get("status", "?")
        gaps = check_task(task)
        if gaps:
            fail_count += 1
            print(f"[FAIL] {tid} (status={status})")
            for gap in gaps:
                print(f"       ✗ {gap}")
        else:
            print(f"[OK]   {tid} (status={status})")

    total = len(loop_tasks)
    ok_count = total - fail_count
    print(f"\n{ok_count}/{total} loop task(s) passed guardrail checks.")
    return 1 if fail_count else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task-id", metavar="ID", help="Check only this task ID")
    parser.add_argument(
        "--status-file",
        metavar="PATH",
        default=str(DEFAULT_STATUS_FILE),
        help=f"Path to ai-status.json (default: {DEFAULT_STATUS_FILE})",
    )
    args = parser.parse_args()

    status_path = Path(args.status_file)
    rc = run(status_path, args.task_id)
    sys.exit(rc)


if __name__ == "__main__":
    main()
