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


def _load_ai_status_module():
    """Import scripts/ai_status.py as the oracle for outbox indicator behaviour."""

    import importlib.util

    module_path = Path(__file__).resolve().parents[2] / "scripts" / "ai_status.py"
    spec = importlib.util.spec_from_file_location("shadow_ai_status", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compare_outbox_indicators(state: dict[str, Any]) -> dict[str, Any]:
    """Shadow the status-write pending markers against the incumbent board.

    The load-bearing assertion is the flag-off one: `SUP-STATUS-OUTBOX-
    INTEGRITY-VISIBILITY-20260804` must be inert until it is switched on, so a
    flag-off pass has to reproduce the incumbent task rows byte for byte. The
    flag-on pass is reported as an informational delta, not a mismatch.
    """

    import os
    from copy import deepcopy

    ai_status = _load_ai_status_module()
    env_var = ai_status.STATUS_OUTBOX_VISIBILITY_ENABLED_ENV

    def run(enabled: bool) -> list[dict[str, Any]]:
        candidate = deepcopy(state)
        previous = os.environ.get(env_var)
        os.environ[env_var] = "1" if enabled else "0"
        try:
            ai_status._update_pending_outbox_indicators(candidate)
        finally:
            if previous is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = previous
        rows = candidate.get("tasks", [])
        return [row for row in rows if isinstance(row, dict)]

    incumbent = [row for row in state.get("tasks", []) if isinstance(row, dict)]
    off_rows = run(False)
    on_rows = run(True)

    def canonical(rows: list[dict[str, Any]]) -> str:
        return json.dumps(rows, sort_keys=True, ensure_ascii=False)

    marked = [
        {
            "task": str(row.get("id") or ""),
            "count": row.get("status_write_pending_count"),
        }
        for row in on_rows
        if row.get("status_write_pending")
    ]
    return {
        "checked": len(incumbent),
        "agree": canonical(off_rows) == canonical(incumbent),
        "marked_when_enabled": marked,
    }


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

        outbox = compare_outbox_indicators(board)
        if not outbox["agree"]:
            print("  MISMATCH outbox_indicators: flag-off rows differ from the incumbent board")
            exit_code = 1
        for row in outbox["marked_when_enabled"]:
            print(f"  pending {row['task']}: {row['count']} queued status writes")
        print(
            f"outbox_indicators shadow: {outbox['checked']} tasks, "
            f"{0 if outbox['agree'] else 1} mismatch, "
            f"{len(outbox['marked_when_enabled'])} marked pending when flag enabled"
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
