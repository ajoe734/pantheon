#!/usr/bin/env python3
"""Delivery-intent payload construction for Supervisor Authority V2.

There is no event-log authority in this module.  The shared planner constructs
an intent here and atomically stores it with its lease record in runtime state.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import (
    agent_config_for,
    bound_commit_subject,
    display_name_for,
    new_runtime_id,
    render_template,
    resolve_path,
    utc_now,
    write_activity_log,
)
from dispatch_policy import is_execution_dispatch_reason
from runtime_state import store_queue_event


def render_wakeup_message(
    config: dict[str, Any],
    event: dict[str, Any],
    target_agent: str,
) -> str:
    agent = agent_config_for(config, target_agent)
    template_path = resolve_path(
        agent.get("wake_template") or ".orchestrator/templates/wakeup.txt"
    )
    if template_path is None:
        raise RuntimeError("Unable to resolve wake-up template path")
    context_files = list(event.get("context_files") or [])
    target_files = list((event.get("task") or {}).get("artifacts") or [])
    task_payload = event.get("task") or {}
    task_id = str(event.get("task_id") or "").strip()
    reason = str(event.get("reason") or "")
    if not is_execution_dispatch_reason(reason):
        raise ValueError(f"unsupported delivery reason: {reason!r}")

    role_guardrails = ""
    if reason == "review_ready_dispatch":
        role_guardrails = (
            "\n這次 dispatch 的角色是 reviewer，不是 task owner。\n"
            "- 獨立核對 acceptance、diff、PR/head evidence 與驗證。\n"
            "- 不得執行 `assign`、`start`、`progress`、`handoff`、`done`。\n"
            f"- 通過時執行 ai-status.sh approve {task_id}；不通過時執行 "
            f"ai-status.sh reopen {task_id}。\n"
        )
    elif reason == "owned_finalize_dispatch":
        role_guardrails = (
            "\n這次 dispatch 的角色是已通過審查後的 task owner。\n"
            "- 不得重新指派 owner/reviewer。\n"
            "- 核對 exact-head approval 與必要交付後才執行 `done`。\n"
        )

    sidecar_guardrails = ""
    if str(task_payload.get("task_class") or "").lower() == "sidecar":
        sidecar_guardrails = (
            "\n這是 canonical sidecar support task；只處理其明列 scope，"
            "不得擴張成主線 governance 或 runtime 修改。\n"
        )

    dependency_truth = [
        item
        for item in (task_payload.get("dependency_truth") or [])
        if isinstance(item, dict) and str(item.get("task_id") or "").strip()
    ]
    dependency_lines = "\n".join(
        "- {task_id}: status={status}, satisfied={satisfied}".format(
            task_id=str(item.get("task_id") or "").strip(),
            status=str(item.get("status") or "missing").strip(),
            satisfied=str(bool(item.get("satisfied"))).lower(),
        )
        for item in dependency_truth
    ) or "- (none)"

    branch_workflow = (
        config.get("branch_workflow")
        if isinstance(config.get("branch_workflow"), dict)
        else {}
    )
    base_branch = str(branch_workflow.get("dev_branch") or "dev")
    task_branch_prefix = str(branch_workflow.get("task_branch_prefix") or "task/")
    task_id_kebab = (
        re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")
        if task_id
        else "none"
    )
    lane = (
        re.sub(r"[^a-z0-9]+", "-", str(target_agent).lower()).strip("-")
        or "unknown"
    )
    variables = {
        "context_files": "\n".join(f"- {path}" for path in context_files)
        or "- AI_COLLABORATION_GUIDE.md",
        "task_id": task_id or "(none)",
        "task_id_kebab": task_id_kebab,
        "lane": lane,
        "base_branch": base_branch,
        "branch_name": f"{task_branch_prefix}{task_id or '(none)'}",
        "branch_start_command": (
            f'./scripts/git/task_start.sh "{task_id}"'
            if task_id
            else "./scripts/git/task_start.sh <TASK-ID>"
        ),
        "anchor_commit_subject": (
            bound_commit_subject(task_id, "anchor <scope>")
            if task_id
            else "<TASK-ID>: anchor <scope>"
        ),
        "reason": reason,
        "target_files": "\n".join(f"- {path}" for path in target_files)
        or "- (none inferred)",
        "dispatch_guardrails": role_guardrails.rstrip(),
        "sidecar_guardrails": sidecar_guardrails.rstrip(),
        "dependency_truth": dependency_lines,
        "target_agent_display_name": display_name_for(config, agent["id"]),
    }
    return render_template(template_path, variables).strip() + "\n"


def _queue_delivery_event_locked(
    config: dict[str, Any],
    state: dict[str, Any],
    event: dict[str, Any],
) -> bool:
    reason = str(event.get("reason") or "")
    if not is_execution_dispatch_reason(reason):
        return False
    target_agent = str(event.get("target_agent") or "").strip()
    if not target_agent:
        return False
    try:
        task_generation = int(event.get("task_generation"))
    except (TypeError, ValueError):
        return False
    if task_generation < 1:
        return False
    agent = agent_config_for(config, target_agent)
    context_files = list(
        event.get("context_files")
        or ["AI_COLLABORATION_GUIDE.md", "ai-status.json"]
    )
    prepared = {**event, "context_files": context_files}
    queue_payload = {
        "event_id": new_runtime_id("evt"),
        "created_at": utc_now(),
        "event_key": prepared.get("key"),
        "task_id": prepared.get("task_id"),
        "task_generation": task_generation,
        "target_agent": agent["id"],
        "target_display_name": display_name_for(config, agent["id"]),
        "provider": agent.get("provider", agent["id"]),
        # The pure planner selects one physical endpoint.  The queue must keep
        # that choice so late revalidation can fail closed instead of silently
        # hopping to a different slot after an assignment has been reserved.
        "delivery_endpoint_id": prepared.get("delivery_endpoint_id"),
        "reason": reason,
        "message": render_wakeup_message(config, prepared, target_agent),
        "context_files": context_files,
        "target_files": (prepared.get("task") or {}).get("artifacts") or [],
        "metadata": {
            "handoff": prepared.get("handoff"),
            "task": prepared.get("task") or {},
        },
    }
    if not store_queue_event(state, queue_payload):
        return False
    write_activity_log(
        config,
        {
            "type": "wake_queued",
            "task_id": prepared.get("task_id"),
            "target_agent": queue_payload["target_display_name"],
            "message": f"Planner delivery intent queued: {reason}",
            "queue_event_id": queue_payload["event_id"],
        },
    )
    return True


def trim_seen_events(state: dict[str, Any], max_entries: int) -> None:
    seen = state.get("seen_event_keys")
    if not isinstance(seen, dict) or max_entries <= 0:
        state["seen_event_keys"] = {}
        return
    valid = {
        str(key): value
        for key, value in seen.items()
        if isinstance(value, str) and value
    }
    if len(valid) > max_entries:
        ordered = sorted(valid.items(), key=lambda item: (item[1], item[0]))
        valid = dict(ordered[-max_entries:])
    state["seen_event_keys"] = valid
