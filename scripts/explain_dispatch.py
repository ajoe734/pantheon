#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure .orchestrator is in sys.path
ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from supervisor import (
    active_quota_group_counts,
    active_worker_indexes,
    agent_auto_dispatch_block_reason,
    agent_can_take_task,
    agent_dispatch_capacity,
    agent_dispatch_loads,
    agent_quota_group_id,
    assistant_dev_bridge_task_is_admitted,
    build_dispatch_event,
    dependencies_satisfied,
    display_name_for,
    dispatch_event_is_in_unchanged_cooldown,
    load_config,
    load_status,
    normalize_agent_id,
    outstanding_delivery_indexes,
    quota_group_concurrency_limit,
    queued_quota_group_counts,
    ready_dispatch_max_concurrent_workers,
    ready_dispatch_settings,
    scan_live_worker_pids_by_agent,
    task_assignment_is_catalog_locked,
    task_execution_dispatch_candidate,
    task_resolver_for_config,
    utc_now,
    weighted_dispatch_agent_ids,
)



def load_orchestrator_state(config: dict[str, Any]) -> dict[str, Any]:
    state_file = config.get("paths", {}).get("state_file")
    if state_file and os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def load_status_data(config: dict[str, Any]) -> dict[str, Any]:
    try:
        return load_status(config)
    except Exception:
        status_root = Path(os.environ.get("PANTHEON_STATUS_ROOT") or ROOT).resolve()
        status_path = status_root / "ai-status.json"
        if status_path.exists():
            with open(status_path, "r", encoding="utf-8") as f:
                return json.load(f)
        fallback = ROOT / "ai-status.json"
        if fallback.exists():
            with open(fallback, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}



def explain_dispatch_for_task(
    config: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    target_agent_filter: str | None = None,
) -> dict[str, Any]:
    settings = ready_dispatch_settings(config)
    status = load_status_data(config)
    schema = config.get("schema", {})
    tasks_path = schema.get("tasks_path", "tasks")
    task_id_field = schema.get("task_id_field", "id")

    tasks = [t for t in status.get(tasks_path, []) if str(t.get(task_id_field) or "") == task_id]
    if not tasks:
        return {
            "task_id": task_id,
            "error": f"Task {task_id} not found in active status",
            "agents": {},
        }

    task = tasks[0]
    all_status_tasks = [t for t in status.get(tasks_path, []) if t.get(task_id_field)]
    task_map = {str(t.get(task_id_field)): t for t in all_status_tasks}
    task_resolver = task_resolver_for_config(config, task_map)

    active_statuses = {str(value) for value in settings.get("active_worker_statuses", [])}
    dependency_done_statuses = {str(value).lower() for value in settings.get("dependency_done_statuses", ["done"])}

    try:
        unchanged_cooldown_seconds = max(
            0.0,
            float(settings.get("unchanged_task_cooldown_seconds", 900)),
        )
    except (TypeError, ValueError):
        unchanged_cooldown_seconds = 900.0

    dispatch_started_at = utc_now()

    _active_agents, active_task_agents = active_worker_indexes(state, active_statuses)
    _pending_agents, pending_task_agents, pending_event_keys = outstanding_delivery_indexes(config, state)
    active_task_ids = {tid for tid, _agent in active_task_agents if tid}
    pending_task_ids = {tid for tid, _agent in pending_task_agents if tid}

    agent_loads = agent_dispatch_loads(config, state, active_statuses)
    active_quota_counts = active_quota_group_counts(config, state, active_statuses)
    pending_quota_counts = queued_quota_group_counts(config, state)
    seen = state.get("seen_event_keys", {})

    all_config_agent_ids = list(config.get("agents", {}).keys())
    agent_ids = weighted_dispatch_agent_ids(config, settings)
    for aid in all_config_agent_ids:
        if aid not in agent_ids:
            agent_ids.append(aid)

    if target_agent_filter:
        norm_filter = normalize_agent_id(target_agent_filter)
        agent_ids = [a for a in agent_ids if a == norm_filter or display_name_for(config, a) == target_agent_filter]
        if not agent_ids:
            if norm_filter in config.get("agents", {}):
                agent_ids = [norm_filter]


    results: dict[str, Any] = {
        "task_id": task_id,
        "task_status": task.get("status"),
        "task_owner": task.get("owner"),
        "task_reviewer": task.get("reviewer"),
        "agents": {},
    }

    # Global checks
    if not settings.get("enabled", True):
        results["global_block_reason"] = "Ready dispatch dispatcher is disabled in settings"
        return results

    max_concurrent = ready_dispatch_max_concurrent_workers(config)
    if max_concurrent is not None and max_concurrent > 0:
        live_total = sum(len(pids) for pids in scan_live_worker_pids_by_agent().values())
        if live_total >= max_concurrent:
            results["global_block_reason"] = f"Global max_concurrent_workers reached ({live_total} >= {max_concurrent})"
            return results
        pending_only_total = len(pending_task_ids - active_task_ids)
        reserved_total = live_total + pending_only_total
        if reserved_total >= max_concurrent:
            results["global_block_reason"] = f"Global reserved max_concurrent_workers reached ({reserved_total} >= {max_concurrent})"
            return results

    if task_id in active_task_ids:
        results["global_block_reason"] = f"Task {task_id} is already actively running"
        return results

    if task_id in pending_task_ids:
        results["global_block_reason"] = f"Task {task_id} is already pending delivery"
        return results

    if not assistant_dev_bridge_task_is_admitted(config, task):
        results["global_block_reason"] = f"Task {task_id} signed dev-bridge packet is not admitted"
        return results

    for agent_id in agent_ids:
        target_agent = display_name_for(config, agent_id)
        agent_trace: dict[str, Any] = {
            "display_name": target_agent,
            "blocked": False,
            "first_blocking_gate": None,
            "block_reason": None,
        }

        # Gate 1: agent_auto_dispatch_block_reason
        agent_block = agent_auto_dispatch_block_reason(config, state, agent_id)
        if agent_block:
            agent_trace["blocked"] = True
            agent_trace["first_blocking_gate"] = "agent_auto_dispatch_block_reason"
            agent_trace["block_reason"] = agent_block
            results["agents"][target_agent] = agent_trace
            continue

        # Gate 2: agent_can_take_task
        if not agent_can_take_task(config, target_agent, task, state=state):
            agent_trace["blocked"] = True
            agent_trace["first_blocking_gate"] = "agent_can_take_task"
            agent_trace["block_reason"] = f"Agent {target_agent} capabilities do not match task requirements"
            results["agents"][target_agent] = agent_trace
            continue

        # Gate 3: quota_group_concurrency_limit
        quota_limit = quota_group_concurrency_limit(config, agent_id, settings)
        quota_group = agent_quota_group_id(config, agent_id)
        quota_used = active_quota_counts.get(quota_group, 0) + pending_quota_counts.get(quota_group, 0)
        if quota_limit and quota_group and quota_used >= quota_limit:
            agent_trace["blocked"] = True
            agent_trace["first_blocking_gate"] = "quota_group_concurrency_limit"
            agent_trace["block_reason"] = f"Quota group {quota_group} limit reached ({quota_used} >= {quota_limit})"
            results["agents"][target_agent] = agent_trace
            continue

        # Gate 4: agent_dispatch_capacity
        agent_capacity = agent_dispatch_capacity(config, agent_id, settings)
        current_agent_load = len(agent_loads.get(target_agent, []))
        if current_agent_load >= agent_capacity:
            agent_trace["blocked"] = True
            agent_trace["first_blocking_gate"] = "agent_dispatch_capacity"
            agent_trace["block_reason"] = f"Agent capacity reached ({current_agent_load} >= {agent_capacity})"
            results["agents"][target_agent] = agent_trace
            continue

        # Gate 5: task_assignment_is_catalog_locked
        if task_assignment_is_catalog_locked(task):
            agent_trace["blocked"] = True
            agent_trace["first_blocking_gate"] = "task_assignment_is_catalog_locked"
            agent_trace["block_reason"] = f"Task assignment is catalog locked"
            results["agents"][target_agent] = agent_trace
            continue

        # Gate 6: dependencies_satisfied
        if not dependencies_satisfied(task, task_resolver, dependency_done_statuses):
            agent_trace["blocked"] = True
            agent_trace["first_blocking_gate"] = "dependencies_satisfied"
            agent_trace["block_reason"] = f"Task dependencies not satisfied"
            results["agents"][target_agent] = agent_trace
            continue

        # Gate 7: task_execution_dispatch_candidate
        candidate = task_execution_dispatch_candidate(
            config,
            task,
            target_agent,
            task_resolver,
            settings=settings,
        )
        if candidate is None:
            agent_trace["blocked"] = True
            agent_trace["first_blocking_gate"] = "task_execution_dispatch_candidate"
            agent_trace["block_reason"] = f"Task role/status not eligible for candidate dispatch to {target_agent}"
            results["agents"][target_agent] = agent_trace
            continue

        reason, priority = candidate

        # Gate 8: Event build & Cooldown check
        event = build_dispatch_event(task, target_agent, reason, task_resolver)
        if event["key"] in pending_event_keys:
            agent_trace["blocked"] = True
            agent_trace["first_blocking_gate"] = "pending_event_keys"
            agent_trace["block_reason"] = f"Event key already pending"
            results["agents"][target_agent] = agent_trace
            continue

        if dispatch_event_is_in_unchanged_cooldown(
            seen,
            event["key"],
            cooldown_seconds=unchanged_cooldown_seconds,
            now=dispatch_started_at,
        ):
            agent_trace["blocked"] = True
            agent_trace["first_blocking_gate"] = "dispatch_event_is_in_unchanged_cooldown"
            agent_trace["block_reason"] = f"Dispatch event is in unchanged cooldown"
            results["agents"][target_agent] = agent_trace
            continue

        # No blocking gate found
        agent_trace["candidate_reason"] = reason
        agent_trace["candidate_priority"] = priority
        agent_trace["verdict"] = "no blocking gate found (eligible for dispatch)"
        results["agents"][target_agent] = agent_trace



    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnostic CLI to explain supervisor dispatch decisions for a task.")
    parser.add_argument("task_id", help="The Task ID to diagnose")
    parser.add_argument("--agent", help="Optional agent ID or display name to filter diagnosis for")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    args = parser.parse_args()

    config = load_config()
    state = load_orchestrator_state(config)

    explanation = explain_dispatch_for_task(config, state, args.task_id, target_agent_filter=args.agent)

    if args.json:
        print(json.dumps(explanation, indent=2, ensure_ascii=False))
        return

    print(f"=== Dispatch Explanation for Task: {explanation.get('task_id')} ===")
    if "error" in explanation:
        print(f"Error: {explanation['error']}")
        sys.exit(1)

    if "global_block_reason" in explanation:
        print(f"Global Dispatch Block: {explanation['global_block_reason']}")
        return

    print(f"Status: {explanation.get('task_status')} | Owner: {explanation.get('task_owner')} | Reviewer: {explanation.get('task_reviewer')}")
    print("-" * 60)

    for agent_name, trace in explanation.get("agents", {}).items():
        if trace["blocked"]:
            print(f"Agent: {agent_name:<12} | BLOCKED by [{trace['first_blocking_gate']}]")
            print(f"  Reason: {trace['block_reason']}")
        else:
            print(f"Agent: {agent_name:<12} | READY (Reason: {trace.get('candidate_reason')}, Priority: {trace.get('candidate_priority')})")
            print(f"  Verdict: {trace.get('verdict')}")


if __name__ == "__main__":
    main()
