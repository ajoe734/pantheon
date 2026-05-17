#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import (
    agent_config_for,
    config_path,
    display_name_for,
    execution_context_files,
    load_config,
    load_json,
    load_status,
    new_runtime_id,
    relpath,
    render_template,
    resolve_path,
    snapshot_task,
    utc_now,
    write_activity_log,
)
from runtime_state import enqueue_event, load_runtime_state, save_runtime_state
from task_archive import DEFAULT_RECENT_LIMIT, recent_terminal_summaries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch ai-status.json and wake the right local agent with a minimal event.")
    parser.add_argument("--config", default=".orchestrator/config.json", help="Path to orchestrator config.")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit.")
    parser.add_argument("--replay", action="store_true", help="Replay pending events immediately on startup.")
    parser.add_argument("--poll-interval", type=float, default=None, help="Override poll interval seconds.")
    return parser.parse_args()


def handoff_key(handoff: dict[str, Any]) -> str:
    parts = [
        str(handoff.get("task_id") or ""),
        str(handoff.get("from") or ""),
        str(handoff.get("to") or ""),
        str(handoff.get("created_at") or ""),
        str(handoff.get("message") or ""),
    ]
    return "|".join(parts)


def enqueue_runtime_events_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("events", {}).get("enqueue_runtime_events", False))


def build_snapshot(config: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    schema = config["schema"]
    tasks_path = schema["tasks_path"]
    handoffs_path = schema["handoffs_path"]
    tasks = {
        task.get(schema["task_id_field"]): snapshot_task(task, schema)
        for task in status.get(tasks_path, [])
        if task.get(schema["task_id_field"])
    }
    pending_handoffs = [
        handoff
        for handoff in status.get(handoffs_path, [])
        if str(handoff.get("status") or "").lower() in {s.lower() for s in config.get("events", {}).get("pending_handoff_statuses", ["pending"])}
    ]
    recent_limit = int(config.get("watcher", {}).get("recent_terminal_limit", DEFAULT_RECENT_LIMIT))
    return {
        "tasks": tasks,
        "recent_terminal_tasks": recent_terminal_summaries(limit=recent_limit),
        "pending_handoff_keys": [handoff_key(item) for item in pending_handoffs],
        "pending_handoffs": pending_handoffs,
        "status_updated_at": status.get("updated_at"),
    }


def resolve_target_for_status(task: dict[str, Any], status_value: str, config: dict[str, Any]) -> str | None:
    status_targets = config.get("events", {}).get("status_targets", {})
    target_field = status_targets.get(status_value)
    if not target_field:
        return None
    if target_field == "owner":
        return task.get(config["schema"]["assignee_field"])
    if target_field == "reviewer":
        return task.get(config["schema"]["reviewer_field"])
    return task.get(target_field)


def resolve_target_for_waiting_status(status_value: str, config: dict[str, Any]) -> str | None:
    for pattern in config.get("events", {}).get("waiting_status_patterns", []):
        match = re.match(pattern, status_value)
        if not match:
            continue
        if match.groupdict().get("agent"):
            return match.group("agent")
    return None


def build_task_status_event(task_id: str, task: dict[str, Any], new_status: str, config: dict[str, Any]) -> dict[str, Any] | None:
    lower_status = new_status.lower()
    review_statuses = {value.lower() for value in config.get("events", {}).get("review_statuses", ["review"])}

    if lower_status in review_statuses and task.get("reviewer"):
        return {
            "key": f"{task_id}:status:{lower_status}:{task.get('reviewer')}",
            "task_id": task_id,
            "target_agent": task.get("reviewer"),
            "reason": f"status:{new_status}",
            "task": task,
        }

    waiting_target = resolve_target_for_waiting_status(new_status, config)
    if waiting_target:
        return {
            "key": f"{task_id}:status:{lower_status}:{waiting_target}",
            "task_id": task_id,
            "target_agent": waiting_target,
            "reason": f"status:{new_status}",
            "task": task,
        }

    target = resolve_target_for_status(task, new_status, config)
    if target:
        return {
            "key": f"{task_id}:status:{lower_status}:{target}",
            "task_id": task_id,
            "target_agent": target,
            "reason": f"status:{new_status}",
            "task": task,
        }
    return None


def compute_replay_events(current: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for task_id, task in current.get("tasks", {}).items():
        new_status = str(task.get("status") or "")
        if not new_status:
            continue
        event = build_task_status_event(task_id, task, new_status, config)
        if event:
            events.append(event)

    if config.get("events", {}).get("watch_handoffs", True):
        for handoff in current.get("pending_handoffs", []):
            events.append(
                {
                    "key": f"handoff:{handoff_key(handoff)}",
                    "task_id": handoff.get("task_id"),
                    "target_agent": handoff.get("to"),
                    "reason": "handoff_pending",
                    "task": {
                        "id": handoff.get("task_id"),
                        "artifacts": [],
                        "next": handoff.get("message"),
                    },
                    "handoff": handoff,
                }
            )
    return events


def compute_events(previous: dict[str, Any], current: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    previous_tasks = previous.get("tasks", {})
    current_tasks = current.get("tasks", {})
    review_statuses = {value.lower() for value in config.get("events", {}).get("review_statuses", ["review"])}

    for task_id, task in current_tasks.items():
        old_task = previous_tasks.get(task_id)
        if not old_task:
            continue

        if config.get("events", {}).get("watch_assignee_changes", True) and task.get("owner") != old_task.get("owner") and task.get("owner"):
            events.append(
                {
                    "key": f"{task_id}:owner:{task.get('owner')}:{task.get('status')}",
                    "task_id": task_id,
                    "target_agent": task.get("owner"),
                    "reason": "assignee_changed",
                    "task": task,
                }
            )

        if config.get("events", {}).get("watch_reviewer_changes", False) and task.get("reviewer") != old_task.get("reviewer") and task.get("reviewer"):
            events.append(
                {
                    "key": f"{task_id}:reviewer:{task.get('reviewer')}:{task.get('status')}",
                    "task_id": task_id,
                    "target_agent": task.get("reviewer"),
                    "reason": "reviewer_changed",
                    "task": task,
                }
            )

        new_status = str(task.get("status") or "")
        old_status = str(old_task.get("status") or "")
        if new_status == old_status:
            continue

        event = build_task_status_event(task_id, task, new_status, config)
        if event:
            events.append(event)

    if config.get("events", {}).get("watch_handoffs", True):
        previous_pending = set(previous.get("pending_handoff_keys", []))
        for handoff in current.get("pending_handoffs", []):
            key = handoff_key(handoff)
            if key in previous_pending:
                continue
            events.append(
                {
                    "key": f"handoff:{key}",
                    "task_id": handoff.get("task_id"),
                    "target_agent": handoff.get("to"),
                    "reason": "handoff_pending",
                    "task": {
                        "id": handoff.get("task_id"),
                        "artifacts": [],
                        "next": handoff.get("message"),
                    },
                    "handoff": handoff,
                }
            )
    return events


def render_wakeup_message(config: dict[str, Any], event: dict[str, Any], target_agent: str) -> str:
    agent = agent_config_for(config, target_agent)
    template_path = resolve_path(agent.get("wake_template") or ".orchestrator/templates/wakeup.txt")
    if template_path is None:
        raise RuntimeError("Unable to resolve wake-up template path")
    context_files = event.get("context_files") or execution_context_files(config, event.get("task_id"))
    target_files = event.get("task", {}).get("artifacts") or []
    task_payload = event.get("task", {}) or {}
    sidecar_guardrails = ""
    if str(task_payload.get("task_class") or "").lower() == "sidecar":
        helper_parent = str(task_payload.get("helper_parent") or "").strip() or "(unknown parent)"
        helper_kind = str(task_payload.get("helper_kind") or "").strip() or "support_slice"
        sidecar_guardrails = (
            "\n這是一個 sidecar support slice，不是主線 canonical 實作。\n"
            f"- Parent Task: {helper_parent}\n"
            f"- Helper Kind: {helper_kind}\n"
            "- 只允許建立或更新支援性材料與 handoff packet。\n"
            "- 不要修改 L1 canonical truth、核心 contract 真相、或主要 runtime/registry/governance 實作。\n"
            "- 盡量把輸出限制在上面列出的相關檔案；若需新增檔案，只能新增 support artifact。\n"
            "- 完成後請交接給指定 reviewer，由 parent owner 決定是否吸收進主線。\n"
        )
    task_id = str(event.get("task_id") or "").strip()
    branch_workflow = config.get("branch_workflow") if isinstance(config.get("branch_workflow"), dict) else {}
    base_branch = str(branch_workflow.get("dev_branch") or "dev")
    task_branch_prefix = str(branch_workflow.get("task_branch_prefix") or "task/")
    task_id_kebab = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-") if task_id else "none"
    branch_name = f"{task_branch_prefix}{task_id}" if task_id else f"{task_branch_prefix}(none)"
    lane = re.sub(r"[^a-z0-9]+", "-", str(target_agent or "").lower()).strip("-") or "unknown"
    variables = {
        "context_files": "\n".join(f"- {path}" for path in context_files) if context_files else "- AI_COLLABORATION_GUIDE.md",
        "task_id": task_id or "(none)",
        "task_id_kebab": task_id_kebab,
        "lane": lane,
        "base_branch": base_branch,
        "branch_name": branch_name,
        "branch_start_command": f"./scripts/git/task_start.sh \"{task_id}\"" if task_id else "./scripts/git/task_start.sh <TASK-ID>",
        "anchor_commit_subject": f"{task_id}: anchor <scope>" if task_id else "<TASK-ID>: anchor <scope>",
        "reason": event.get("reason") or "wakeup",
        "target_files": "\n".join(f"- {path}" for path in target_files) if target_files else "- (none inferred)",
        "sidecar_guardrails": sidecar_guardrails.rstrip(),
        "target_agent_display_name": display_name_for(config, agent["id"]),
    }
    return render_template(template_path, variables).strip() + "\n"


def queue_delivery_event(config: dict[str, Any], event: dict[str, Any]) -> bool:
    target_agent = event.get("target_agent")
    if not target_agent:
        write_activity_log(
            config,
            {
                "type": "wake_skipped",
                "task_id": event.get("task_id"),
                "message": f"Skipped wake-up with no target agent for reason {event.get('reason')}.",
            },
        )
        return False

    agent = agent_config_for(config, target_agent)
    context_files = event.get("context_files") or execution_context_files(config, event.get("task_id"))
    event["context_files"] = context_files
    message = render_wakeup_message(config, event, target_agent)
    queue_payload = {
        "event_id": new_runtime_id("evt"),
        "created_at": utc_now(),
        "event_key": event.get("key"),
        "task_id": event.get("task_id"),
        "target_agent": agent["id"],
        "target_display_name": display_name_for(config, agent["id"]),
        "provider": agent.get("provider", agent["id"]),
        "reason": event.get("reason"),
        "message": message,
        "context_files": context_files,
        "target_files": event.get("task", {}).get("artifacts") or [],
        "metadata": {"handoff": event.get("handoff"), "task": event.get("task", {})},
    }
    enqueue_event(config, queue_payload)
    write_activity_log(
        config,
        {
            "type": "wake_queued",
            "task_id": event.get("task_id"),
            "target_agent": display_name_for(config, agent["id"]),
            "delivery_mode": config.get("providers", {}).get(agent.get("provider", agent["id"]), {}).get(
                "delivery_mode", agent.get("adapter", "file_inbox")
            ),
            "message": f"Wake-up queued for supervisor: {event.get('reason')}",
            "queue_event_id": queue_payload["event_id"],
        },
    )
    return True


def trim_seen_events(state: dict[str, Any], max_entries: int) -> None:
    seen = state.get("seen_event_keys", {})
    if len(seen) <= max_entries:
        return
    ordered = sorted(seen.items(), key=lambda item: item[1])
    state["seen_event_keys"] = dict(ordered[-max_entries:])


def run_scan(config: dict[str, Any], state: dict[str, Any], replay: bool, provider_capabilities: dict[str, Any]) -> bool:
    status = load_status(config)
    snapshot = build_snapshot(config, status)
    is_first_run = not state.get("initialized_at")
    if is_first_run and not replay and not config.get("watcher", {}).get("replay_on_start", False):
        state["initialized_at"] = utc_now()
        state["last_scan_at"] = utc_now()
        state["tasks"] = snapshot["tasks"]
        state["recent_terminal_tasks"] = snapshot.get("recent_terminal_tasks", [])
        state["pending_handoff_keys"] = snapshot["pending_handoff_keys"]
        save_runtime_state(config, state)
        return False

    events = compute_events(state, snapshot, config)
    if replay:
        merged_events: dict[str, dict[str, Any]] = {}
        for event in compute_replay_events(snapshot, config):
            merged_events[event["key"]] = event
        for event in events:
            merged_events[event["key"]] = event
        events = list(merged_events.values())

    seen = state.setdefault("seen_event_keys", {})
    changed = False
    if enqueue_runtime_events_enabled(config):
        for event in events:
            if event["key"] in seen and not replay:
                continue
            queued = queue_delivery_event(config, event)
            if queued:
                seen[event["key"]] = utc_now()
                changed = True
    elif events:
        changed = True

    state["initialized_at"] = state.get("initialized_at") or utc_now()
    state["last_scan_at"] = utc_now()
    state["tasks"] = snapshot["tasks"]
    state["recent_terminal_tasks"] = snapshot.get("recent_terminal_tasks", [])
    state["pending_handoff_keys"] = snapshot["pending_handoff_keys"]
    trim_seen_events(state, int(config.get("watcher", {}).get("max_seen_events", 2000)))
    save_runtime_state(config, state)
    return changed


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    state = load_runtime_state(config)
    provider_capabilities = load_json(config_path(config, "provider_capabilities"), default={})

    poll_interval = args.poll_interval or float(config.get("watcher", {}).get("poll_interval_seconds", 2.0))
    run_scan(config, state, replay=args.replay, provider_capabilities=provider_capabilities)
    if args.once:
        return 0

    while True:
        time.sleep(poll_interval)
        state = load_runtime_state(config)
        run_scan(config, state, replay=False, provider_capabilities=provider_capabilities)


if __name__ == "__main__":
    raise SystemExit(main())
