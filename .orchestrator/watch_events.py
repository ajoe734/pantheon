#!/usr/bin/env python3
"""Durable delivery-intent queue for Supervisor Authority V2.

The historical filename is retained for the runtime-writer registry and old
imports.  Status watching, replay, and event synthesis were removed: only the
shared planner may call this module, and only canonical execution reasons are
accepted.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import (
    agent_config_for,
    bound_commit_subject,
    config_path,
    display_name_for,
    new_runtime_id,
    render_template,
    resolve_path,
    utc_now,
    write_activity_log,
)
from dispatch_policy import is_execution_dispatch_reason


def _assert_regular_queue_leaf(path: Path, descriptor: int) -> None:
    descriptor_stat = os.fstat(descriptor)
    path_stat = path.lstat()
    if (
        not stat.S_ISREG(descriptor_stat.st_mode)
        or stat.S_ISLNK(path_stat.st_mode)
        or path_stat.st_dev != descriptor_stat.st_dev
        or path_stat.st_ino != descriptor_stat.st_ino
    ):
        raise RuntimeError(f"runtime event queue data leaf changed during append: {path}")


def _pread_exact(descriptor: int, size: int, offset: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.pread(descriptor, remaining, offset)
        if not chunk:
            break
        chunk = chunk[:remaining]
        chunks.append(chunk)
        offset += len(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _append_runtime_event_locked(config: dict[str, Any], event: dict[str, Any]) -> None:
    path = config_path(config, "event_queue")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"runtime event queue data leaf cannot be a symlink: {path}")
    payload = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    flags = (
        os.O_RDWR
        | os.O_APPEND
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        _assert_regular_queue_leaf(path, descriptor)
        offset = os.lseek(descriptor, 0, os.SEEK_END)
        if offset and os.pread(descriptor, 1, offset - 1) != b"\n":
            raise RuntimeError(f"runtime event queue is not newline terminated: {path}")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("runtime event queue append made no progress")
            view = view[written:]
        os.fsync(descriptor)
        _assert_regular_queue_leaf(path, descriptor)
        if _pread_exact(descriptor, len(payload), offset) != payload:
            raise RuntimeError(f"runtime event queue readback mismatch: {path}")
        _assert_regular_queue_leaf(path, descriptor)
    finally:
        os.close(descriptor)
    directory_descriptor = os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


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
        "target_agent_display_name": display_name_for(config, agent["id"]),
    }
    return render_template(template_path, variables).strip() + "\n"


def _queue_delivery_event_locked(
    config: dict[str, Any],
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
        "reason": reason,
        "message": render_wakeup_message(config, prepared, target_agent),
        "context_files": context_files,
        "target_files": (prepared.get("task") or {}).get("artifacts") or [],
        "metadata": {
            "handoff": prepared.get("handoff"),
            "task": prepared.get("task") or {},
        },
    }
    _append_runtime_event_locked(config, queue_payload)
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
