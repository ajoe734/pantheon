"""Shadow validator for rewrite/concurrency.py (Phase 1a).

Proves the clean `max_parallel()` reproduces the incumbent
`agent_dispatch_capacity()` for every agent in a real config, before the
dispatch loop is switched to it. This is the "shadow validation" discipline from
SUPERVISOR_REWRITE_PLAN.md §4: compute the new decision, diff it against the old
one on live data, require exact agreement before cutover.

Usage:
    python3 -m rewrite.shadow --config /path/to/live-supervisor-config.json

Exit code 0 = every agent agrees; 1 = at least one mismatch (which is a real
finding: the incumbent's extra logic is load-bearing for that agent).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# .orchestrator on the path so we can import the incumbent for comparison.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import supervisor  # noqa: E402  (comparison oracle only)

from rewrite import concurrency  # noqa: E402
from rewrite import provider_health  # noqa: E402
from rewrite import task_machine  # noqa: E402

# Every failure kind classify_worker_failure emits, plus legacy/unknown spellings,
# so the pause decision is compared across the whole vocabulary — not just what
# the live board happens to contain right now.
_FAILURE_KINDS = [
    "auth", "tool_auth", "capacity", "capacity_retryable", "quota_terminal",
    "terminal", "transient", "unknown_critical", "", "bogus_kind", None,
]


def compare_failure_pause() -> list[dict[str, Any]]:
    """One row per failure kind: incumbent should_pause_dispatch_for_failure_kind
    vs the clean provider_health.should_pause."""
    rows: list[dict[str, Any]] = []
    for kind in _FAILURE_KINDS:
        old = supervisor.should_pause_dispatch_for_failure_kind(kind)
        new = provider_health.should_pause(kind)
        rows.append({"kind": repr(kind), "old": old, "new": new, "agree": old == new})
    return rows


def compare_capacity(config: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per agent: incumbent vs clean max-parallel and whether they agree."""
    settings = supervisor.ready_dispatch_settings(config)
    rows: list[dict[str, Any]] = []
    for agent_id in (config.get("agents", {}) or {}):
        norm = supervisor.normalize_agent_id(agent_id)
        if not norm:
            continue
        display = supervisor.display_name_for(config, norm)
        old = supervisor.agent_dispatch_capacity(config, norm, settings)
        new = concurrency.max_parallel(config, norm, settings=settings, display_name=display)
        rows.append({"agent": norm, "old": old, "new": new, "agree": old == new})
    return rows


def compare_account_limit(config: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per agent: incumbent quota_group_concurrency_limit vs the clean
    account_limit fed the incumbent-resolved identity keys (in the incumbent's
    exact try order). Proves the cap arithmetic is faithful ahead of collapsing
    the 6-way account-group resolver to a single key."""
    settings = supervisor.ready_dispatch_settings(config)
    rows: list[dict[str, Any]] = []
    for agent_id in (config.get("agents", {}) or {}):
        norm = supervisor.normalize_agent_id(agent_id)
        if not norm:
            continue
        # Same key order the incumbent tries inside quota_group_concurrency_limit.
        group_id = supervisor.agent_quota_group_id(config, norm)
        provider_id = supervisor.agent_provider_id(config, norm)
        display = supervisor.display_name_for(config, norm)
        keys = [
            *supervisor.agent_quota_identity_ids(config, norm),
            group_id,
            provider_id,
            norm,
            display,
        ]
        old = supervisor.quota_group_concurrency_limit(config, norm, settings)
        new = concurrency.account_limit(group_id, settings=settings, identity_keys=keys)
        rows.append({"agent": norm, "old": old, "new": new, "agree": old == new})
    return rows


def compare_dispatch_reason(config: dict[str, Any], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (task, candidate-agent): incumbent dispatch_priority_for_task
    vs the clean task_machine.dispatch_priority, on the real board.

    Only the task's own owner and reviewer can yield a non-None priority (any
    other agent is None for both by construction), so those are the candidates
    checked. deps_satisfied is computed exactly as the incumbent does inside
    dispatch_priority_for_task (single-task lookup), so the only thing under test
    is that the state machine reproduces the ladder.
    """
    settings = supervisor.ready_dispatch_settings(config)
    done_statuses = supervisor.normalized_status_set(
        settings.get("dependency_done_statuses"), ["done"]
    )
    schema = config.get("schema", {})
    owner_field = schema.get("assignee_field", "owner")
    reviewer_field = schema.get("reviewer_field", "reviewer")

    rows: list[dict[str, Any]] = []
    for task in tasks:
        tid = str(task.get("id") or "")
        candidates = {task.get(owner_field), task.get(reviewer_field)}
        candidates.discard(None)
        deps_ok = supervisor.dependencies_satisfied(task, {tid: task}, done_statuses)
        for agent in candidates:
            old = supervisor.dispatch_priority_for_task(config, task, agent)
            new = task_machine.dispatch_priority(
                task.get("status"),
                is_owner=task.get(owner_field) == agent,
                is_reviewer=task.get(reviewer_field) == agent,
                deps_satisfied=deps_ok,
            )
            rows.append({"task": tid, "agent": agent, "old": old, "new": new, "agree": old == new})
    return rows


def compare_outbox_indicators(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare tasks on board between flag-off and flag-on outbox indicators."""
    from copy import deepcopy

    # Flag OFF calculation
    state_off = deepcopy(state)
    for task in state_off.get("tasks", []):
        if isinstance(task, dict):
            task.pop("status_write_pending", None)
            task.pop("status_write_pending_count", None)

    # Flag ON calculation
    import os
    import scripts.ai_status as ai_status
    state_on = deepcopy(state)
    env_var = getattr(ai_status, "STATUS_OUTBOX_VISIBILITY_ENABLED_ENV", "PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED")
    old_env = os.environ.get(env_var)
    os.environ[env_var] = "1"
    try:
        ai_status._update_pending_outbox_indicators(state_on)
    finally:
        if old_env is None:
            os.environ.pop(env_var, None)
        else:
            os.environ[env_var] = old_env

    tasks_off = {str(t.get("id")): t for t in state_off.get("tasks", []) if isinstance(t, dict)}
    tasks_on = {str(t.get("id")): t for t in state_on.get("tasks", []) if isinstance(t, dict)}

    rows: list[dict[str, Any]] = []
    for tid in sorted(tasks_off.keys()):
        t_off = tasks_off[tid]
        t_on = tasks_on.get(tid, {})
        off_pending = t_off.get("status_write_pending", False)
        on_pending = t_on.get("status_write_pending", False)
        rows.append({
            "task": tid,
            "off_pending": off_pending,
            "on_pending": on_pending,
            "on_count": t_on.get("status_write_pending_count"),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to a supervisor config JSON.")
    parser.add_argument("--board", help="Path to ai-status.json (enables dispatch-reason shadow).")
    parser.add_argument("--quiet", action="store_true", help="Only print mismatches + summary.")
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text())
    exit_code = 0

    cap_rows = compare_capacity(config)
    cap_mismatch = [r for r in cap_rows if not r["agree"]]
    for r in cap_rows:
        if r["agree"] and args.quiet:
            continue
        print(f"  {'OK      ' if r['agree'] else 'MISMATCH'} {r['agent']}: old={r['old']} new={r['new']}")
    print(f"\nmax_parallel shadow: {len(cap_rows)} agents, {len(cap_mismatch)} mismatch")
    if cap_mismatch:
        exit_code = 1

    acct_rows = compare_account_limit(config)
    acct_mismatch = [r for r in acct_rows if not r["agree"]]
    for r in acct_mismatch:
        print(f"  MISMATCH account_limit {r['agent']}: old={r['old']} new={r['new']}")
    print(f"account_limit shadow: {len(acct_rows)} agents, {len(acct_mismatch)} mismatch")
    if acct_mismatch:
        exit_code = 1

    pause_rows = compare_failure_pause()
    pause_mismatch = [r for r in pause_rows if not r["agree"]]
    for r in pause_mismatch:
        print(f"  MISMATCH failure_pause {r['kind']}: old={r['old']} new={r['new']}")
    print(f"failure_pause shadow: {len(pause_rows)} kinds, {len(pause_mismatch)} mismatch")
    if pause_mismatch:
        exit_code = 1

    if args.board:
        board = json.loads(Path(args.board).read_text())
        tasks = board.get("tasks", [])
        if isinstance(tasks, dict):
            tasks = list(tasks.values())
        dr_rows = compare_dispatch_reason(config, tasks)
        dr_mismatch = [r for r in dr_rows if not r["agree"]]
        for r in dr_mismatch:
            print(f"  MISMATCH {r['task']} / {r['agent']}: old={r['old']} new={r['new']}")
        print(f"dispatch_reason shadow: {len(dr_rows)} (task,agent) pairs, {len(dr_mismatch)} mismatch")
        if dr_mismatch:
            exit_code = 1

        outbox_rows = compare_outbox_indicators(board)
        pending_tasks = [r for r in outbox_rows if r["on_pending"]]
        print(f"outbox_indicators shadow: {len(outbox_rows)} tasks checked, {len(pending_tasks)} marked pending when flag enabled")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
