#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from adapters import build_adapter
from adapters.base import DeliveryRequest
from common import (
    agent_config_for,
    command_exists,
    config_path,
    display_name_for,
    load_config,
    load_json,
    new_runtime_id,
    relpath,
    selected_shared_files,
    shell_quote,
    spawn_background_process,
    utc_now,
    write_activity_log,
)
from provider_permissions import provider_capabilities as build_provider_capabilities, write_provider_capabilities
from runtime_state import load_approval_state, load_event_queue, load_runtime_state, queue_event_record, save_runtime_state
from watch_events import run_scan


SESSION_ID_PATTERNS = [
    re.compile(r'"session_id"\s*:\s*"([^"]+)"'),
    re.compile(r'"sessionId"\s*:\s*"([^"]+)"'),
]
URL_PATTERN = re.compile(r"https://github\.com/[^\s)]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local orchestrator supervisor loop.")
    parser.add_argument("--config", default=".orchestrator/config.json")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-watch", action="store_true", help="Process the event queue without running watch_events first.")
    parser.add_argument("--replay", action="store_true", help="Pass replay through to watch_events for the first scan.")
    parser.add_argument("--poll-interval", type=float, default=None)
    return parser.parse_args()


def load_provider_report(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("supervisor", {}).get("auto_refresh_provider_capabilities", True):
        report = build_provider_capabilities(config)
        write_provider_capabilities(config, report=report)
        return report
    return load_json(config_path(config, "provider_capabilities"), default={}) or {}


def build_request(config: dict[str, Any], event: dict[str, Any]) -> DeliveryRequest:
    agent = agent_config_for(config, event["target_agent"])
    metadata = dict(event.get("metadata", {}) or {})
    if agent.get("model_preference") and "model_preference" not in metadata:
        metadata["model_preference"] = agent.get("model_preference")
    return DeliveryRequest(
        agent_id=agent["id"],
        provider=agent.get("provider", agent["id"]),
        delivery_mode=config.get("providers", {}).get(agent.get("provider", agent["id"]), {}).get(
            "delivery_mode", agent.get("adapter", "file_inbox")
        ),
        message=event["message"],
        task_id=event.get("task_id"),
        reason=event.get("reason"),
        context_files=event.get("context_files", [relpath(path) for path in selected_shared_files(config)]),
        target_files=event.get("target_files", []),
        metadata=metadata,
    )


def queue_status(state: dict[str, Any], event_id: str) -> dict[str, Any]:
    return queue_event_record(state, event_id)


def process_queue(config: dict[str, Any], state: dict[str, Any], provider_report: dict[str, Any]) -> bool:
    changed = False
    for event in load_event_queue(config):
        event_id = event.get("event_id")
        if not event_id:
            continue
        record = queue_status(state, event_id)
        if record.get("status") in {"started", "manual_pending", "completed", "failed"}:
            continue
        request = build_request(config, event)
        agent = agent_config_for(config, request.agent_id)
        adapter = build_adapter(agent.get("adapter", "file_inbox"), config=config, provider_capabilities=provider_report)
        result = adapter.deliver(request)
        record["attempt_count"] = int(record.get("attempt_count", 0)) + 1
        record["last_attempt_at"] = utc_now()
        if not result.ok:
            record["status"] = "failed"
            record["error"] = result.error or result.notes
            write_activity_log(
                config,
                {
                    "type": "worker_failed",
                    "task_id": event.get("task_id"),
                    "target_agent": display_name_for(config, agent["id"]),
                    "delivery_mode": result.mode,
                    "message": result.error or result.notes or "Worker delivery failed.",
                    "queue_event_id": event_id,
                },
            )
            changed = True
            continue

        worker_run_id = result.run_id or event_id
        record["status"] = "manual_pending" if result.manual_confirmation_required and not result.auto_delivered else "started"
        record["run_id"] = worker_run_id
        record["processed_at"] = utc_now()
        state.setdefault("workers", {})[worker_run_id] = {
            "run_id": worker_run_id,
            "provider": request.provider,
            "agent_id": agent["id"],
            "task_id": request.task_id,
            "session_id": result.session_id,
            "mode": result.mode,
            "status": "manual_pending" if result.manual_confirmation_required and not result.auto_delivered else "running",
            "last_event_at": utc_now(),
            "deferred_action": None,
            "resume_token": result.resume_token or result.session_id,
            "pr_url": result.pr_url,
            "session_url": result.session_url,
            "attempt_count": record["attempt_count"],
            "queue_event_id": event_id,
            "command": result.command,
            "log_path": result.log_path,
            "payload_path": result.payload_path,
            "pid": result.pid,
            "notes": result.notes,
            "metadata": result.metadata,
        }
        write_activity_log(
            config,
            {
                "type": "worker_started",
                "task_id": event.get("task_id"),
                "target_agent": display_name_for(config, agent["id"]),
                "provider": request.provider,
                "delivery_mode": result.mode,
                "message": f"Worker started via {result.adapter}: {event.get('reason')}",
                "queue_event_id": event_id,
                "worker_run_id": worker_run_id,
                "command": result.command,
                "log_path": result.log_path,
                "payload_path": result.payload_path,
            },
        )
        changed = True
    return changed


def pid_is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def file_iso_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def update_from_log(worker: dict[str, Any]) -> None:
    log_path_value = worker.get("log_path")
    if not log_path_value:
        return
    log_path = Path(log_path_value)
    if not log_path.exists():
        return
    mtime = file_iso_mtime(log_path)
    if mtime and (not worker.get("last_event_at") or mtime > worker.get("last_event_at", "")):
        worker["last_event_at"] = mtime
    try:
        content = log_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not worker.get("session_id") and payload.get("session_id"):
            worker["session_id"] = payload.get("session_id")
            worker.setdefault("resume_token", worker["session_id"])
        if payload.get("type") == "result":
            if payload.get("stop_reason") == "tool_deferred":
                worker["status"] = "waiting_approval"
                worker["deferred_tool_use"] = payload.get("deferred_tool_use")
            if payload.get("pr_url") and not worker.get("pr_url"):
                worker["pr_url"] = payload.get("pr_url")
            if payload.get("session_url") and not worker.get("session_url"):
                worker["session_url"] = payload.get("session_url")
    if not worker.get("session_id"):
        for pattern in SESSION_ID_PATTERNS:
            match = pattern.search(content)
            if match:
                worker["session_id"] = match.group(1)
                worker.setdefault("resume_token", worker["session_id"])
                break
    if not worker.get("pr_url"):
        for url in URL_PATTERN.findall(content):
            if "/pull/" in url:
                worker["pr_url"] = url
                break
    if not worker.get("session_url"):
        for url in URL_PATTERN.findall(content):
            if "/agent" in url or "/sessions/" in url:
                worker["session_url"] = url
                break


def detect_worker_failure(worker: dict[str, Any]) -> str | None:
    log_path_value = worker.get("log_path")
    if not log_path_value:
        return None
    log_path = Path(log_path_value)
    if not log_path.exists():
        return None
    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Error:"):
            return stripped
        if stripped.startswith("error:"):
            return stripped
        if stripped.startswith("fatal:"):
            return stripped
        if "not available" in stripped.lower() and "--model" in stripped:
            return stripped
    return None


def _claude_resume_allowed_tools(approval: dict[str, Any] | None) -> list[str]:
    if not approval:
        return []
    candidates: list[str] = []
    for value in (
        approval.get("resume_override_rule"),
        approval.get("suggested_rule"),
        approval.get("tool_name"),
    ):
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


def resume_claude_worker(
    config: dict[str, Any],
    worker: dict[str, Any],
    provider_report: dict[str, Any],
    *,
    approval: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    session_id = worker.get("session_id") or worker.get("resume_token")
    if not session_id:
        return None
    cli = command_exists("claude")
    if not cli:
        return None
    provider = config.get("providers", {}).get("claude", {})
    runtime = provider.get("runtime", {})
    command = [
        runtime.get("cli") or cli,
        "-p",
        "--resume",
        str(session_id),
        "--output-format",
        runtime.get("output_format", "stream-json"),
    ]
    if runtime.get("include_hook_events", True):
        command.append("--include-hook-events")
    allowed_tools = (
        _claude_resume_allowed_tools(approval)
        if runtime.get("resume_use_allowed_tools_from_approval", True)
        else []
    )
    if allowed_tools:
        command.extend(["--allowedTools", *allowed_tools])
    provider_info = (provider_report or {}).get("providers", {}).get("claude", {})
    resume_permission_mode = runtime.get("resume_permission_mode_after_approval", "bypassPermissions")
    if worker.get("last_approval_id"):
        command.extend(["--permission-mode", resume_permission_mode])
    elif runtime.get("enable_auto_mode_if_supported", True) and provider_info.get("supports_auto_approve"):
        command.extend(["--permission-mode", runtime.get("auto_permission_mode", "auto")])
    else:
        command.extend(["--permission-mode", runtime.get("permission_mode", "acceptEdits")])
    mcp_config = runtime.get("mcp_config")
    if mcp_config:
        command.extend(["--mcp-config", str(config_path(config, "claude_mcp_config"))])
    log_path = config_path(config, "state_file").parent / "logs" / f"{new_runtime_id('claude-resume')}.log"
    env = os.environ.copy()
    env.update(
        {
            "ORCH_RUN_ID": worker["run_id"],
            "ORCH_TASK_ID": worker.get("task_id") or "",
            "ORCH_AGENT_ID": worker.get("agent_id") or "",
            "ORCH_SESSION_ID": str(session_id),
        }
    )
    process, _ = spawn_background_process(
        command,
        cwd=config_path(config, "status_file").parents[0],
        log_path=log_path,
        env=env,
    )
    previous_logs = list(worker.get("previous_log_paths") or [])
    if worker.get("log_path"):
        previous_logs.append(worker["log_path"])
    worker["previous_log_paths"] = previous_logs
    worker["pid"] = process.pid
    worker["status"] = "running"
    worker["deferred_action"] = None
    worker["last_event_at"] = utc_now()
    worker["log_path"] = str(log_path)
    worker["resume_count"] = int(worker.get("resume_count", 0)) + 1
    worker["last_resumed_session_id"] = str(session_id)
    worker["command"] = command
    worker.setdefault("metadata", {})["shell_command"] = shell_quote(command)
    worker["metadata"]["resume_permission_mode"] = resume_permission_mode if worker.get("last_approval_id") else None
    worker["metadata"]["resume_allowed_tools"] = allowed_tools
    return {
        "command": command,
        "log_path": str(log_path),
        "pid": process.pid,
        "allowed_tools": allowed_tools,
    }


def poll_workers(config: dict[str, Any], state: dict[str, Any]) -> bool:
    changed = False
    approval_state = load_approval_state(config)
    pending_by_run: dict[str, list[dict[str, Any]]] = {}
    resolved_by_run: dict[str, list[dict[str, Any]]] = {}
    for item in approval_state.get("pending", []):
        run_id = item.get("worker_run_id")
        if run_id:
            pending_by_run.setdefault(run_id, []).append(item)
    for item in approval_state.get("history", []):
        run_id = item.get("worker_run_id")
        if run_id:
            resolved_by_run.setdefault(run_id, []).append(item)

    stall_after = float(config.get("supervisor", {}).get("stall_after_seconds", 300))
    now = datetime.now(timezone.utc)
    provider_report = load_provider_report(config)
    for worker in state.get("workers", {}).values():
        update_from_log(worker)
        pending = pending_by_run.get(worker["run_id"], [])
        resolved = resolved_by_run.get(worker["run_id"], [])
        if pending:
            approval = pending[0]
            if worker.get("status") != "waiting_approval":
                worker["status"] = "waiting_approval"
                worker["deferred_action"] = approval.get("approval_id")
                worker["last_event_at"] = approval.get("created_at") or worker.get("last_event_at") or utc_now()
                write_activity_log(
                    config,
                    {
                        "type": "worker_waiting_approval",
                        "provider": worker.get("provider"),
                        "task_id": worker.get("task_id"),
                        "message": f"Worker waiting on approval {approval.get('approval_id')}",
                        "worker_run_id": worker["run_id"],
                        "approval_id": approval.get("approval_id"),
                    },
                )
                changed = True
            continue

        if worker.get("status") == "waiting_approval" and resolved:
            latest = resolved[-1]
            if latest.get("approval_id") != worker.get("last_approval_id"):
                worker["last_approval_id"] = latest.get("approval_id")
                if latest.get("decision") == "allow" and worker.get("provider") == "claude":
                    resumed = resume_claude_worker(config, worker, provider_report, approval=latest)
                    write_activity_log(
                        config,
                        {
                            "type": "worker_resumed",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": f"Resumed worker after approval {latest.get('approval_id')}",
                            "worker_run_id": worker["run_id"],
                            "approval_id": latest.get("approval_id"),
                            "command": resumed.get("command") if resumed else None,
                            "log_path": resumed.get("log_path") if resumed else None,
                            "allowed_tools": resumed.get("allowed_tools") if resumed else None,
                        },
                    )
                elif latest.get("decision") == "deny":
                    worker["status"] = "failed"
                    worker["last_event_at"] = utc_now()
                    write_activity_log(
                        config,
                        {
                            "type": "worker_failed",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": latest.get("note") or "Worker approval denied.",
                            "worker_run_id": worker["run_id"],
                            "approval_id": latest.get("approval_id"),
                        },
                    )
                changed = True
            continue

        alive = pid_is_alive(worker.get("pid"))
        current_status = worker.get("status")
        if current_status == "waiting_approval" and not pending:
            worker["status"] = "running" if alive else "completed"
            worker["deferred_action"] = None
            worker["last_event_at"] = utc_now()
            changed = True

        if alive:
            last_event = worker.get("last_event_at")
            if last_event:
                last_dt = datetime.fromisoformat(last_event.replace("Z", "+00:00"))
                if (now - last_dt).total_seconds() >= stall_after and worker.get("status") != "stalled":
                    worker["status"] = "stalled"
                    write_activity_log(
                        config,
                        {
                            "type": "worker_stalled",
                            "provider": worker.get("provider"),
                            "task_id": worker.get("task_id"),
                            "message": f"Worker appears stalled after {int(stall_after)} seconds.",
                            "worker_run_id": worker["run_id"],
                        },
                    )
                    changed = True
            continue

        failure_reason = detect_worker_failure(worker)
        if failure_reason and worker.get("status") != "failed":
            worker["status"] = "failed"
            worker["last_event_at"] = utc_now()
            write_activity_log(
                config,
                {
                    "type": "worker_failed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": failure_reason,
                    "worker_run_id": worker["run_id"],
                    "pr_url": worker.get("pr_url"),
                    "session_url": worker.get("session_url"),
                },
            )
            changed = True
            continue

        if worker.get("status") not in {"completed", "failed", "manual_pending"}:
            worker["status"] = "completed"
            worker["last_event_at"] = utc_now()
            write_activity_log(
                config,
                {
                    "type": "worker_completed",
                    "provider": worker.get("provider"),
                    "task_id": worker.get("task_id"),
                    "message": "Background worker process exited.",
                    "worker_run_id": worker["run_id"],
                    "pr_url": worker.get("pr_url"),
                    "session_url": worker.get("session_url"),
                },
            )
            changed = True
    return changed


def trim_worker_history(state: dict[str, Any], max_entries: int) -> None:
    workers = state.get("workers", {})
    if len(workers) <= max_entries:
        return
    ordered = sorted(workers.items(), key=lambda item: item[1].get("last_event_at") or "")
    state["workers"] = dict(ordered[-max_entries:])


def run_once(config: dict[str, Any], *, watch: bool, replay: bool = False) -> bool:
    state = load_runtime_state(config)
    changed = False
    provider_report = load_provider_report(config)
    if watch:
        changed = run_scan(config, state, replay=replay, provider_capabilities=provider_report) or changed
        state = load_runtime_state(config)
    changed = process_queue(config, state, provider_report) or changed
    changed = poll_workers(config, state) or changed
    trim_worker_history(state, int(config.get("supervisor", {}).get("max_worker_history", 200)))
    save_runtime_state(config, state)
    return changed


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    poll_interval = args.poll_interval or float(config.get("supervisor", {}).get("poll_interval_seconds", 2.0))
    run_once(config, watch=not args.no_watch, replay=args.replay)
    if args.once:
        return 0
    while True:
        time.sleep(poll_interval)
        run_once(config, watch=not args.no_watch, replay=False)


if __name__ == "__main__":
    raise SystemExit(main())
