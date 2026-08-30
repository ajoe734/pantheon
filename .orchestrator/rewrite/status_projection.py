"""DTG-CLEAN-M1: current-work/dashboard rendering, view-only normalization,
and derived-view file writes.

This is the "narrow status projection module" SD.md 7.4 describes: it reads
snapshots and writes derived views (current-work.md, the dashboard bundle,
the docs-site mirror); it does not mutate canonical task state or implement
command policy.

Module-boundary note (see docs/operations/development-tooling-four-gap-2026-08-30/M1_CLOSURE_FINDING.md):
the moved functions' real transitive dependency closure reaches a small set
of symbols that stay in scripts/ai_status.py because they are genuinely
shared infrastructure used far beyond dashboard rendering (task acceptance
evidence, config loading, agent-name canonicalization, archive-root
identity, ...). Rather than duplicating that logic here or pulling it into
this module (which would just relocate DTG-CLEAN-01A's "responsibility
concentration" problem instead of fixing it), those symbols are reached
through a lazy import of scripts.ai_status at call time -- the same
established pattern that module already uses for
``_github_review_bridge_module()``. A lazy (function-body) import, unlike a
module-top-level one, does not create the circular import SD.md 4.2
forbids: by the time any of these functions actually runs, both modules
have finished their own top-level initialization.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from common import durable_write_bytes, read_activity_log_tail_bytes, utc_now as iso_now
import task_archive as task_archive_module
from task_archive import (
    ARCHIVE_TASKS_DIR,
    TaskResolver,
    load_archive_index,
    load_archived_snapshot,
    recent_terminal_summaries,
)


def _ai_status_module():
    """Lazy import back to the entrypoint module for the handful of symbols
    that are genuinely shared infrastructure, not dashboard-specific (see
    module docstring). Mirrors scripts/ai_status.py's own
    ``_github_review_bridge_module()`` pattern."""
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    import sys

    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import ai_status

    return ai_status


def canonical_tier_labels(state: dict[str, Any]) -> list[str]:
    ai_status = _ai_status_module()
    ai_status.sync_canonical_document_metadata(state)
    layers = state.get("canonical_document_layers", {})
    return [f"`{name}`" for name in layers]


def parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    normalized = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_display_timestamp(value: Any) -> str:
    ai_status = _ai_status_module()
    parsed = parse_timestamp(value)
    if parsed is None:
        return "-" if value is None or value == "" else str(value)
    try:
        return parsed.astimezone(ai_status.DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, ValueError):
        if isinstance(value, str) and value.strip():
            return value.strip()
        return parsed.isoformat()


def localize_embedded_timestamps(text: Any) -> str:
    ai_status = _ai_status_module()
    if text is None:
        return "-"
    rendered = str(text)
    if not rendered:
        return "-"
    return ai_status.ISO_TIMESTAMP_RE.sub(lambda match: format_display_timestamp(match.group(0)), rendered)


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return deepcopy(default)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return deepcopy(default)


def int_config_setting(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def int_mapping_config_setting(settings: dict[str, Any], key: str) -> dict[str, int]:
    raw = settings.get(key)
    if not isinstance(raw, dict):
        return {}
    values: dict[str, int] = {}
    for name, value in raw.items():
        try:
            values[str(name)] = int(value)
        except (TypeError, ValueError):
            continue
    return values


def build_dispatch_policy_summary(config: dict[str, Any]) -> dict[str, Any]:
    ready_dispatcher = config.get("ready_dispatcher") if isinstance(config.get("ready_dispatcher"), dict) else {}
    account_caps = int_mapping_config_setting(ready_dispatcher, "max_concurrent_per_account")
    agent_caps = {
        str(agent_id): max(0, int((agent or {}).get("max_parallel", 0) or 0))
        for agent_id, agent in (config.get("agents", {}) or {}).items()
        if not str((agent or {}).get("dispatch_slot_for") or "").strip()
    }
    return {
        "mode": "single_dispatch_planner",
        "max_dispatches_per_tick": int_config_setting(ready_dispatcher, "max_dispatches_per_tick", 4),
        "max_parallel_by_agent": agent_caps,
        "max_concurrent_per_account": account_caps,
    }


def count_terminal_since(threshold_iso: str | None) -> tuple[int, int]:
    if not threshold_iso:
        return (0, 0)
    try:
        threshold = datetime.fromisoformat(str(threshold_iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return (0, 0)
    completed_count = 0
    superseded_count = 0
    if not ARCHIVE_TASKS_DIR.exists():
        return (0, 0)
    for path in ARCHIVE_TASKS_DIR.glob("*.json"):
        try:
            text = task_archive_module.read_task_archive_file_safe(path)
            snapshot = json.loads(text)
        except (OSError, json.JSONDecodeError):
            continue
        archived_at_raw = str(snapshot.get("archived_at") or "").strip()
        if not archived_at_raw:
            continue
        try:
            archived_at = datetime.fromisoformat(archived_at_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if archived_at < threshold:
            continue
        outcome = str(snapshot.get("terminal_outcome") or "").strip().lower()
        if outcome == "superseded":
            superseded_count += 1
        else:
            completed_count += 1
    return (completed_count, superseded_count)


def terminal_archive_projection(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return terminal facts with their current archive availability.

    The rich archive may be lost or pending reconciliation without invalidating
    the compact dependency fact.  Exposing that distinction prevents a
    completed task from becoming an "Unknown task" in management views.
    """
    ai_status = _ai_status_module()

    ai_status.normalize_terminal_facts(state)
    ai_status.normalize_archive_receipts(state)
    facts = state[ai_status.TERMINAL_FACTS_KEY]
    receipts = state[ai_status.ARCHIVE_RECEIPTS_KEY]
    rows: list[dict[str, Any]] = []
    for task_id in sorted(facts):
        snapshot = load_archived_snapshot(task_id)
        receipt = receipts.get(task_id)
        snapshot_sha256 = (
            ai_status._canonical_json_sha256(snapshot) if isinstance(snapshot, Mapping) else None
        )
        receipt_matches = bool(
            isinstance(receipt, Mapping)
            and snapshot_sha256
            and receipt.get("archive_root") == ai_status._archive_root_identity()
            and receipt.get("snapshot_sha256") == snapshot_sha256
        )
        rows.append(
            {
                "task_id": task_id,
                **deepcopy(facts[task_id]),
                "archive_missing": snapshot is None,
                "archive_receipt_valid": receipt_matches,
            }
        )
    return rows


def task_delivery_layer(task: dict[str, Any]) -> str:
    ai_status = _ai_status_module()
    explicit = str(task.get("delivery_layer") or "").strip().lower()
    if explicit in {"primary", "project"}:
        return "primary"
    if explicit in {"external", "upstream"}:
        return "external"
    task_id = str(task.get("id") or "")
    prefix = task_id.split("-", 1)[0]
    if prefix in ai_status.EXTERNAL_TASK_PREFIXES:
        return "external"
    id_tokens = {token.strip().upper() for token in re.split(r"[-_/]+", task_id) if token.strip()}
    if id_tokens & ai_status.EXTERNAL_TASK_ID_TOKENS:
        return "external"
    artifacts = [str(item) for item in task.get("artifacts", []) if str(item).strip()]
    if any(artifact.startswith(ai_status.EXTERNAL_TASK_ARTIFACT_PREFIXES) for artifact in artifacts):
        return "external"
    text = " ".join(
        str(task.get(field) or "")
        for field in ("id", "title", "summary_zh", "phase")
    ).lower()
    if any(keyword in text for keyword in ai_status.EXTERNAL_TASK_TEXT_KEYWORDS):
        return "external"
    return "primary"


def pending_status_write_count(task: dict[str, Any]) -> int:
    """Return how many status writes are queued behind a canonical integrity block."""

    if not isinstance(task, dict) or not task.get("status_write_pending"):
        return 0
    count = task.get("status_write_pending_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        return 0
    return count


def display_task_status(task: dict[str, Any]) -> str:
    """Render a status, flagging a row a queued write has not been able to update."""

    status = task.get("status")
    text = "" if status is None else str(status)
    pending = pending_status_write_count(task)
    if not pending:
        return text
    noun = "write" if pending == 1 else "writes"
    return f"{text} (stale: {pending} {noun} queued)"


def display_task_title(task: dict[str, Any]) -> str:
    return str(task.get("title") or "")


def activity_log_message(entry: dict[str, Any]) -> str:
    message = entry.get("message")
    if message is not None and str(message).strip():
        return str(message)

    event_type = str(entry.get("type") or "event").strip() or "event"
    details: list[str] = []
    commit = str(entry.get("commit") or "").strip()
    if commit:
        details.append(f"commit {commit[:12]}")

    scope = entry.get("scope")
    if isinstance(scope, list) and scope:
        rendered_scope = ", ".join(f"`{str(item)}`" for item in scope[:3])
        if len(scope) > 3:
            rendered_scope += ", ..."
        details.append(f"scope {rendered_scope}")

    if details:
        return f"{event_type}: {'; '.join(details)}"
    return event_type


def write_current_work(state: dict[str, Any], logs: list[dict[str, Any]]) -> None:
    ai_status = _ai_status_module()
    def cell(value: Any) -> str:
        text = "-" if value is None or value == "" else str(value)
        return text.replace("|", "\\|").replace("\n", "<br>")

    def append_layer_table(lines: list[str], tasks: list[dict[str, Any]]) -> None:
        lines.extend(
            [
                "| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        if not tasks:
            lines.append("| _(none)_ | - | - | - | - | - | - |")
            return
        for task in tasks:
            depends = ", ".join(f"`{item}`" for item in task.get("depends_on", [])) or "-"
            lines.append(
                "| `{id}` | {phase} | {title} | {owner} | {status} | {depends} | {summary} |".format(
                    id=cell(task.get("id")),
                    phase=cell(task.get("phase") or "Unassigned"),
                    title=cell(display_task_title(task)),
                    owner=cell(task.get("owner")),
                    status=cell(display_task_status(task)),
                    depends=cell(depends),
                    summary=cell(task.get("summary_zh") or "-"),
                )
            )

    current_logs = logs[-20:]
    canonical_files = ai_status.canonical_file_set(state)
    tier_labels = canonical_tier_labels(state)
    archive_index = load_archive_index()
    archive_counts = archive_index.get("counts", {}) if isinstance(archive_index.get("counts"), dict) else {}
    recent_terminal_tasks = recent_terminal_summaries(limit=ai_status.task_archive_recent_limit())
    active_tasks = [task for task in state["tasks"] if task.get("status") != "done"]
    primary_tasks = [task for task in active_tasks if task_delivery_layer(task) == "primary"]
    external_tasks = [task for task in active_tasks if task_delivery_layer(task) == "external"]
    current_sprint_lines = [
        f"- Sprint: `{state['sprint']}`",
        "- Canonical files: " + ", ".join(f"`{item}`" for item in state["canonical_files"]),
        "- Canonical tiers: " + (", ".join(tier_labels) if tier_labels else "-"),
    ]
    for path, label in ai_status.OPTIONAL_CURRENT_WORK_REFERENCES:
        if path in canonical_files:
            current_sprint_lines.append(f"- {label}: `{path}`")
    current_sprint_lines.append("- Dashboard: `docs-site/index.html`")

    lines: list[str] = [
        "# Current Work",
        "",
        "This file is generated from `ai-status.json` and `ai-activity-log.jsonl`.",
        "Do not treat this file as the machine-readable source of truth.",
        f"Absolute times below use {ai_status.DISPLAY_TIMEZONE_LABEL}.",
        "",
        f"Last updated: {format_display_timestamp(state['updated_at'])}",
        "",
        "## Objective",
        "",
        localize_embedded_timestamps(state["objective"]),
        "",
        "## Current Sprint",
        "",
        *current_sprint_lines,
        "",
    ]

    lines.extend(
        [
        "## Active Slices",
        "",
        ]
    )

    for agent in state["agents"]:
        next_text = localize_embedded_timestamps(agent.get("next") or "No active assignment")
        lines.append(f"- `{agent['name']}`: {', '.join(agent['capability_lane'])}; next: {next_text}")

    lines.extend(
        [
            "",
            "## Delivery Layers",
            "",
            "### Primary Project Work",
            "",
        ]
    )
    append_layer_table(lines, primary_tasks)
    lines.extend(
        [
            "",
            "### External / Upstream Integration Work",
            "",
        ]
    )
    append_layer_table(lines, external_tasks)

    lines.extend(
        [
            "",
            "## Recently Executed Tasks",
            "",
            f"- Archive updated: {format_display_timestamp(archive_index.get('updated_at'))}",
            f"- Terminal tasks archived: `{int(archive_counts.get('total') or 0)}` total, `{int(archive_counts.get('completed') or 0)}` completed, `{int(archive_counts.get('superseded') or 0)}` superseded",
            "",
            "| ID | Phase | Task | Owner | Outcome | Archived At | Snapshot |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    if recent_terminal_tasks:
        for task in recent_terminal_tasks:
            lines.append(
                "| `{id}` | {phase} | {title} | {owner} | {outcome} | {archived_at} | `{snapshot}` |".format(
                    id=cell(task.get("task_id")),
                    phase=cell(task.get("phase")),
                    title=cell(task.get("title") or "-"),
                    owner=cell(task.get("owner")),
                    outcome=cell(task.get("terminal_outcome")),
                    archived_at=cell(format_display_timestamp(task.get("archived_at"))),
                    snapshot=cell(task.get("snapshot_path") or "-"),
                )
            )
    else:
        lines.append("| _(none)_ | - | - | - | - | - | - |")

    pending_write_tasks = [
        task for task in state["tasks"] if pending_status_write_count(task)
    ]
    if pending_write_tasks:
        lines.extend(
            [
                "",
                "## Status Write Backlog",
                "",
                "Canonical status writes for these tasks are durably queued behind an",
                "integrity block. Their rows below may be stale; a stale row here is",
                "not evidence that the task was never touched.",
                "",
                "| Task | Owner | Displayed Status | Queued Writes |",
                "|---|---|---|---|",
            ]
        )
        for task in pending_write_tasks:
            lines.append(
                "| `{id}` | {owner} | {status} | {count} |".format(
                    id=cell(task.get("id")),
                    owner=cell(task.get("owner")),
                    status=cell(task.get("status")),
                    count=pending_status_write_count(task),
                )
            )

    lines.extend(["", "## Task Board", "", "| ID | Phase | Task | 中文說明 | Owner | Reviewer | Status | Depends On | Last Update | Next |", "|---|---|---|---|---|---|---|---|---|---|"])

    for task in state["tasks"]:
        depends = ", ".join(f"`{item}`" for item in task.get("depends_on", [])) or "-"
        lines.append(
            "| `{id}` | {phase} | {title} | {summary} | {owner} | {reviewer} | {status} | {depends} | {last_update} | {next} |".format(
                id=cell(task.get("id")),
                phase=cell(task.get("phase") or "Unassigned"),
                title=cell(display_task_title(task)),
                summary=cell(task.get("summary_zh") or "-"),
                owner=cell(task.get("owner")),
                reviewer=cell(task.get("reviewer")),
                status=cell(display_task_status(task)),
                depends=cell(depends),
                last_update=cell(format_display_timestamp(task.get("last_update"))),
                next=cell(localize_embedded_timestamps(task.get("next") or "-")),
            )
        )

    lines.extend(["", "## Handoff Queue", "", "| Task | From | To | Message | Status | Created At |", "|---|---|---|---|---|---|"])
    pending_handoffs = [handoff for handoff in state.get("handoffs", []) if handoff.get("status") != "done"]
    if pending_handoffs:
        for handoff in pending_handoffs:
            lines.append(
                f"| `{handoff['task_id']}` | {handoff['from']} | {handoff['to']} | {cell(localize_embedded_timestamps(handoff['message']))} | {handoff['status']} | {cell(format_display_timestamp(handoff['created_at']))} |"
            )
    else:
        lines.append("| _(none)_ | - | - | - | - | - |")

    lines.extend(["", "## Blockers", "", "| Task | Owner | Waiting For | Message | Status |", "|---|---|---|---|---|"])
    open_blockers = [blocker for blocker in state.get("blockers", []) if blocker.get("status") == "open"]
    if open_blockers:
        for blocker in open_blockers:
            lines.append(
                f"| `{blocker['task_id']}` | {blocker['owner']} | {blocker['waiting_for']} | {blocker['message']} | {blocker['status']} |"
            )
    else:
        lines.append("| _(none)_ | - | - | - | - |")

    lines.extend(["", "## Review Notes", "", "| Task | Reviewer | 修正重點 | Review File |", "|---|---|---|---|"])
    review_tasks = [task for task in state["tasks"] if task.get("review_notes_zh")]
    if review_tasks:
        for task in review_tasks:
            note_html = "<br>".join(localize_embedded_timestamps(note) for note in task.get("review_notes_zh", []))
            lines.append(
                f"| `{task['id']}` | {cell(task['reviewer'])} | {cell(note_html)} | {cell(task.get('review_file') or '-')} |"
            )
    else:
        lines.append("| _(none)_ | - | - | - |")

    lines.extend(["", "## Latest Checkpoints", ""])
    if current_logs:
        for entry in current_logs:
            task_id = f" `{entry['task_id']}`" if entry.get("task_id") else ""
            timestamp = entry.get("ts") or entry.get("timestamp")
            lines.append(
                f"- {format_display_timestamp(timestamp)} {entry.get('agent') or 'Unknown'}:{task_id} "
                f"{localize_embedded_timestamps(activity_log_message(entry))}"
            )
    else:
        lines.append("- No checkpoints yet.")

    ai_status.CURRENT_WORK_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def expected_task_actor(task: dict[str, Any]) -> str:
    ai_status = _ai_status_module()
    if str(task.get("status") or "").lower() == "review":
        return ai_status.canonical_agent_name(task.get("reviewer"))
    return ai_status.canonical_agent_name(task.get("owner"))


def pid_is_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    state = proc_pid_state(value)
    if not state:
        return False
    return state.upper() not in {"Z", "X"}


def proc_pid_state(pid: Any) -> str | None:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        stat = Path(f"/proc/{value}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return stat.rsplit(")", 1)[1].strip().split()[0]
    except IndexError:
        return None


def worker_has_live_runtime(worker: dict[str, Any], *, pid_alive: bool | None = None) -> bool:
    status = str(worker.get("status") or "").strip().lower()
    pid = worker.get("pid")
    has_pid = pid not in {None, "", 0, "0"}
    if pid_alive is None and has_pid:
        pid_alive = pid_is_alive(pid)

    if status in {"running", "started", "waiting_approval"}:
        if has_pid:
            return bool(pid_alive)
        return True
    if status in {"suspended_approval", "retry_backoff", "stalled"}:
        if has_pid:
            return bool(pid_alive)
        return False
    return False


def normalize_runtime_workers(state: dict[str, Any], orchestrator_state: dict[str, Any]) -> list[dict[str, Any]]:
    ai_status = _ai_status_module()
    resolver = ai_status.task_resolver(state)
    rows: list[dict[str, Any]] = []
    for run_id, worker in (orchestrator_state.get("workers", {}) or {}).items():
        task_id = str(worker.get("task_id") or "").strip()
        task = resolver.get(task_id) if task_id else None
        request_snapshot = worker.get("request_snapshot") if isinstance(worker.get("request_snapshot"), dict) else {}
        request_metadata = request_snapshot.get("metadata") if isinstance(request_snapshot.get("metadata"), dict) else {}
        handoff = request_metadata.get("handoff") if isinstance(request_metadata.get("handoff"), dict) else None
        task_status = str(task.get("status") or "") if task else None
        task_source = resolver.source(task_id) if task_id else None
        worker_status = str(worker.get("status") or "")
        reason = worker.get("reason") or request_snapshot.get("reason")
        if task is None and str(reason or "") == "handoff_pending" and handoff:
            task_status = str(handoff.get("status") or "pending")
            task_source = "handoff"
        pid = worker.get("pid")
        pid_state = proc_pid_state(pid) if pid not in {None, "", 0, "0"} else None
        pid_alive = bool(pid_state and pid_state.upper() not in {"Z", "X"}) if pid_state is not None else None
        live_runtime = worker_has_live_runtime(worker, pid_alive=pid_alive)
        if worker_status in {"superseded", "reassigned"}:
            bucket = "transition"
        elif task_status == "done" or worker_status in {"completed", "failed"}:
            bucket = "completed"
        elif not live_runtime and worker_status in {"running", "started"} and pid not in {None, "", 0, "0"}:
            bucket = "stale"
        elif live_runtime and worker_status in {"running", "started"}:
            bucket = "running"
        else:
            bucket = "pending"
        rows.append(
            {
                "run_id": run_id,
                "task_id": worker.get("task_id"),
                "queue_event_id": worker.get("queue_event_id"),
                "actor": ai_status.normalize_worker_actor(worker),
                "provider": worker.get("provider"),
                "logical_agent_id": worker.get("logical_agent_id"),
                "dispatch_slot": worker.get("dispatch_slot"),
                "dispatch_slot_id": worker.get("dispatch_slot_id"),
                "quota_group": worker.get("quota_group"),
                "status": worker_status,
                "bucket": bucket,
                "task_status": task_status,
                "task_source": task_source,
                "reason": reason,
                "handoff": handoff,
                "last_event_at": worker.get("last_event_at"),
                "started_at": worker.get("started_at"),
                "last_error": worker.get("last_error"),
                "pid": pid,
                "pid_alive": pid_alive,
                "pid_state": pid_state,
                "is_live_runtime": live_runtime,
            }
        )
    rows.sort(key=lambda item: str(item.get("last_event_at") or ""), reverse=True)
    return rows


def normalize_runtime_queue(orchestrator_state: dict[str, Any]) -> list[dict[str, Any]]:
    ai_status = _ai_status_module()
    queue_records = ((orchestrator_state.get("queue") or {}).get("events") or {})
    workers_by_event: dict[str, dict[str, Any]] = {}
    for run_id, worker in (orchestrator_state.get("workers", {}) or {}).items():
        queue_event_id = worker.get("queue_event_id")
        if queue_event_id:
            workers_by_event[str(queue_event_id)] = {"run_id": run_id, **worker}
    rows: list[dict[str, Any]] = []
    for event_id, event in queue_records.items():
        intent = event.get("intent") if isinstance(event.get("intent"), dict) else {}
        linked_worker = workers_by_event.get(str(event_id), {})
        rows.append(
            {
                "id": event_id,
                "task_id": intent.get("task_id") or linked_worker.get("task_id"),
                "status": event.get("status"),
                "agent": ai_status.canonical_agent_name(intent.get("target_display_name") or intent.get("target_agent") or linked_worker.get("agent_id")),
                "provider": intent.get("provider") or linked_worker.get("provider"),
                "reason": intent.get("reason") or linked_worker.get("reason") or (linked_worker.get("request_snapshot") or {}).get("reason"),
                "run_id": intent.get("run_id") or linked_worker.get("run_id"),
                "last_event_at": event.get("last_event_at") or event.get("processed_at") or event.get("last_attempt_at") or linked_worker.get("last_event_at"),
            }
        )
    rows.sort(key=lambda item: str(item.get("last_event_at") or ""), reverse=True)
    return rows


def mismatch_resolution_hint(item: dict[str, Any]) -> str:
    mismatch_type = str(item.get("type") or "")
    if mismatch_type == "delivery_merged_needs_closeout":
        return (
            "先用 merged-dev evidence 補正式 closeout/review 檔，"
            "再走 governed done 或 reconcile_merged_done；不要重新開工或重派已 merged 的 PR。"
        )
    if mismatch_type == "delivery_binding_stale":
        return (
            "先把 task 的 source_ref/review binding 對齊實際 reviewed/merged exact head；"
            "舊 head_sha 留在 active board 會讓 dashboard 和 supervisor 誤判。"
        )
    if mismatch_type == "github_review_gate_missing":
        return (
            "以 assigned reviewer 對 exact PR head 重新執行 governed approve；"
            "GitHub review 或 branch-policy-required canonical status 成功寫入前，"
            "不得把 internal review_approved 當成 PR completion。"
        )
    if mismatch_type == "worker_without_task":
        return "先檢查 dispatch/request snapshot 是否漏掉 task_id；如果是舊 worker，應重派成帶 task_id 的新 run。"
    if mismatch_type == "worker_task_missing":
        return "先確認 task 是否被移除或改名；若 task 已失效，應停掉 worker，否則重建對應 task。"
    if mismatch_type == "worker_assignment_mismatch":
        return "先對齊 owner/reviewer 與 runtime actor；若已改派，先把 task board assignment 寫回，再重新 dispatch。"
    if mismatch_type == "running_worker_on_todo":
        return "先把 task 狀態推成 in_progress；若 worker 是誤派，則回退 queue 或直接停掉該 run。"
    if mismatch_type == "running_worker_on_done":
        return "先確認這是不是殘留 worker；若 task 已確定 done，應停掉 worker 並清理 queue record。"
    if mismatch_type == "active_task_without_worker":
        return "要嘛重新 dispatch expected actor，要嘛把 task 狀態降回 todo/blocking truth，避免假 active。"
    if mismatch_type == "queue_started_without_worker":
        return "先檢查 queue record 是否卡在 started；如果 worker 已消失，重設 queue 或重新 dispatch。"
    if mismatch_type == "approval_missing_task":
        return "先清掉 stale approval，或先恢復 task board 中的 task，再進行批准。"
    return "先對齊 task board、queue、runtime 三者的真相，再決定是重派、回退，還是清理殘留記錄。"


def task_status_is_nonterminal(task: Mapping[str, Any]) -> bool:
    return str(task.get("status") or "").strip().lower() not in {"done", "superseded"}


def _task_text_fields(task: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in ("next", "summary_zh", "title", "phase"):
        value = task.get(key)
        if isinstance(value, str):
            values.append(value)
    for key in ("review_notes_zh", "acceptance"):
        value = task.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
    return "\n".join(values)


def merged_delivery_evidence(task: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return local evidence that a PR-backed delivery merged but is not closed.

    This intentionally avoids GitHub API calls so dashboard generation remains
    deterministic and CI-safe.  It recognizes structured status metadata first,
    then the existing Human/Ops closeout notes used by live fleet tasks.
    """
    ai_status = _ai_status_module()

    delivery = task.get("delivery")
    if isinstance(delivery, Mapping):
        if delivery.get("head_merged_to_target") is True or str(delivery.get("state") or "").upper() == "MERGED":
            commit = str(delivery.get("merge_target_sha") or delivery.get("merge_commit") or delivery.get("commit") or "").strip()
            return {
                "source": "delivery",
                "merge_commit": commit or None,
                "merge_target": str(delivery.get("merge_target_branch") or delivery.get("merge_target_ref") or "").strip() or None,
            }

    for key in (
        "source_ref",
        "github",
        ai_status.APPROVAL_BINDING_KEY,
        ai_status.GITHUB_REVIEW_BRIDGE_KEY,
        ai_status.OPERATOR_ACCEPTANCE_KEY,
    ):
        payload = task.get(key)
        if not isinstance(payload, Mapping):
            continue
        state = str(payload.get("state") or payload.get("status") or "").strip().upper()
        merged = payload.get("merged") is True or state == "MERGED" or bool(payload.get("merged_at"))
        commit = str(
            payload.get("merge_commit")
            or payload.get("merge_commit_sha")
            or payload.get("merged_commit")
            or payload.get("merged_to_dev_sha")
            or ""
        ).strip()
        if merged or commit:
            return {
                "source": key,
                "merge_commit": commit or None,
                "merge_target": str(payload.get("base") or payload.get("target") or payload.get("merge_target") or "").strip() or None,
            }

    text = _task_text_fields(task)
    match = ai_status.MERGED_DELIVERY_RE.search(text)
    if match:
        return {
            "source": "task_text",
            "merge_commit": match.group("sha").lower(),
            "merge_target": match.group("target").lower(),
        }
    return None


def delivery_binding_stale_evidence(task: Mapping[str, Any]) -> dict[str, Any] | None:
    ai_status = _ai_status_module()
    source_ref = task.get("source_ref")
    if not isinstance(source_ref, Mapping):
        return None
    recorded = str(source_ref.get("head_sha") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", recorded):
        return None

    candidates: list[tuple[str, str]] = []
    for key in (
        ai_status.APPROVAL_BINDING_KEY,
        ai_status.GITHUB_REVIEW_BRIDGE_KEY,
        ai_status.OPERATOR_ACCEPTANCE_KEY,
        "github",
    ):
        payload = task.get(key)
        if not isinstance(payload, Mapping):
            continue
        head = str(payload.get("head_sha") or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}", head):
            candidates.append((key, head))

    text = _task_text_fields(task)
    for match in ai_status.EXACT_HEAD_RE.finditer(text):
        candidates.append(("task_text_exact_head", match.group("sha").lower()))

    for source, candidate in candidates:
        if candidate != recorded:
            return {
                "source": source,
                "recorded_head_sha": recorded,
                "evidence_head_sha": candidate,
            }
    return None


def detect_truth_mismatches(
    state: dict[str, Any],
    workers: list[dict[str, Any]],
    queue_events: list[dict[str, Any]],
    approval_state: dict[str, Any],
    resolver: TaskResolver,
    orchestrator_state: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ai_status = _ai_status_module()
    task_map = {task["id"]: task for task in state.get("tasks", [])}
    del orchestrator_state
    live_workers = [
        worker
        for worker in workers
        if worker.get("bucket") in {"running", "pending"} and worker.get("is_live_runtime")
    ]
    live_workers_by_task: dict[str, list[dict[str, Any]]] = {}
    mismatches: list[dict[str, Any]] = []
    seen: set[str] = set()
    pending_approval_run_ids = {
        str(approval.get("worker_run_id") or "").strip()
        for approval in (approval_state.get("pending") or [])
        if str(approval.get("worker_run_id") or "").strip()
    }
    pending_approval_task_ids = {
        str(approval.get("task_id") or "").strip()
        for approval in (approval_state.get("pending") or [])
        if str(approval.get("task_id") or "").strip()
    }
    def push(payload: dict[str, Any]) -> None:
        key = str(payload.get("id") or f"{payload.get('type')}:{payload.get('task_id')}:{payload.get('worker_run_id')}:{payload.get('queue_event_id')}")
        if key in seen:
            return
        payload.setdefault("resolution_hint", mismatch_resolution_hint(payload))
        seen.add(key)
        mismatches.append(payload)

    for worker in live_workers:
        task_id = str(worker.get("task_id") or "").strip()
        if task_id:
            live_workers_by_task.setdefault(task_id, []).append(worker)
        else:
            push(
                {
                    "id": f"worker-without-task:{worker.get('run_id')}",
                    "type": "worker_without_task",
                    "severity": "medium",
                    "title": "Live worker 沒有綁到 task",
                    "summary": f"{worker.get('actor') or '-'} 的 worker 已在跑，但沒有 task_id。",
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )
            continue

        task = task_map.get(task_id)
        if task is None:
            if resolver.source(task_id) == "archive":
                continue
            if worker.get("task_source") == "handoff":
                continue
            push(
                {
                    "id": f"worker-task-missing:{worker.get('run_id')}",
                    "type": "worker_task_missing",
                    "severity": "high",
                    "title": "Live worker 指向不存在的 task",
                    "summary": f"{worker.get('actor') or '-'} 的 worker 綁到 {task_id}，但 task board 找不到這個 task。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )
            continue

        task_status = str(task.get("status") or "").lower()
        expected_actor = expected_task_actor(task)
        actual_actor = ai_status.canonical_agent_name(worker.get("actor") or worker.get("agent_id"))
        if expected_actor and actual_actor and expected_actor != actual_actor:
            push(
                {
                    "id": f"worker-assignment:{worker.get('run_id')}",
                    "type": "worker_assignment_mismatch",
                    "severity": "medium" if task_status == "review" else "high",
                    "title": "Live worker 與 task 指派對不上",
                    "summary": f"{task_id} 目前應由 {expected_actor} 接手，但 live worker 來自 {actual_actor}。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "expected_actor": expected_actor,
                    "actual_actor": actual_actor,
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )

        if worker.get("bucket") == "running" and task_status == "todo":
            push(
                {
                    "id": f"running-worker-on-todo:{worker.get('run_id')}",
                    "type": "running_worker_on_todo",
                    "severity": "medium",
                    "title": "Worker 已在跑，但 task 還是 todo",
                    "summary": f"{task_id} 有 live running worker，但 task status 仍是 todo。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )

        if worker.get("bucket") == "running" and task_status == "done":
            push(
                {
                    "id": f"running-worker-on-done:{worker.get('run_id')}",
                    "type": "running_worker_on_done",
                    "severity": "high",
                    "title": "Task 已完成，但 worker 仍在跑",
                    "summary": f"{task_id} 已是 done，但還有 live running worker。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )

    for task in state.get("tasks", []):
        task_status = str(task.get("status") or "").lower()
        if task_status_is_nonterminal(task):
            merged_evidence = merged_delivery_evidence(task)
            if merged_evidence is not None:
                push(
                    {
                        "id": f"delivery-merged-needs-closeout:{task['id']}",
                        "type": "delivery_merged_needs_closeout",
                        "severity": "high",
                        "title": "Delivery PR 已 merged，但 task 尚未 closeout",
                        "summary": (
                            f"{task['id']} 已有 merged-dev delivery evidence，"
                            f"但 task status 仍是 {task_status or 'unknown'}。"
                        ),
                        "task_id": task["id"],
                        "delivery_evidence": merged_evidence,
                        "detected_at": task.get("last_update"),
                    }
                )
            stale_evidence = delivery_binding_stale_evidence(task)
            if stale_evidence is not None:
                push(
                    {
                        "id": f"delivery-binding-stale:{task['id']}",
                        "type": "delivery_binding_stale",
                        "severity": "high",
                        "title": "Task delivery binding 指向舊 exact head",
                        "summary": (
                            f"{task['id']} 的 source_ref.head_sha 與後續 "
                            "review/merge evidence 不一致。"
                        ),
                        "task_id": task["id"],
                        "delivery_evidence": stale_evidence,
                        "detected_at": task.get("last_update"),
                    }
                )
        if (
            task_status == "review_approved"
            and (
                isinstance(task.get(ai_status.APPROVAL_BINDING_KEY), Mapping)
                or (
                    isinstance(task.get(ai_status.DELIVERY_BINDING_KEY), Mapping)
                    and task.get(ai_status.DELIVERY_BINDING_KEY, {}).get("kind") == "pull_request"
                )
            )
            and not ai_status.exact_head_acceptance_evidence_matches(task)
        ):
            push(
                {
                    "id": f"github-review-gate-missing:{task['id']}",
                    "type": "github_review_gate_missing",
                    "severity": "high",
                    "title": "Internal acceptance 尚未綁定 GitHub review gate",
                    "summary": (
                        f"{task['id']} 有 exact-head review binding 且狀態為 "
                        "review_approved，但沒有對應的 reviewer 或 Human/Ops "
                        "exact-head gate evidence。"
                    ),
                    "task_id": task["id"],
                    "detected_at": task.get("last_update"),
                }
            )
        if task_status != "in_progress":
            continue
        expected_actor = expected_task_actor(task)
        if str(task.get("id") or "").strip() in pending_approval_task_ids:
            continue
        if live_workers_by_task.get(task["id"]):
            continue
        push(
            {
                "id": f"active-task-without-worker:{task['id']}",
                "type": "active_task_without_worker",
                "severity": "medium",
                "title": "Active task 沒有 live worker",
                "summary": f"{task['id']} 在 task board 上是 {task_status}，但目前沒有對應的 live worker。",
                "task_id": task["id"],
                "expected_actor": expected_actor,
                "detected_at": task.get("last_update"),
            }
        )

    live_queue_ids = {str(worker.get("queue_event_id") or "") for worker in live_workers if worker.get("queue_event_id")}
    for event in queue_events:
        event_status = str(event.get("status") or "").lower()
        if event_status not in {"started", "waiting_approval"}:
            continue
        if (
            str(event.get("run_id") or "").strip() in pending_approval_run_ids
            or str(event.get("task_id") or "").strip() in pending_approval_task_ids
        ):
            continue
        if str(event.get("id") or "") in live_queue_ids:
            continue
        push(
            {
                "id": f"queue-without-worker:{event.get('id')}",
                "type": "queue_started_without_worker",
                "severity": "medium",
                "title": "Queue record 已啟動，但找不到 live worker",
                "summary": f"{event.get('task_id') or event.get('id')} 的 queue record 已是 {event_status}，但 runtime 沒有對應 worker。",
                "task_id": event.get("task_id"),
                "queue_event_id": event.get("id"),
                "detected_at": event.get("last_event_at"),
            }
        )

    for approval in (approval_state.get("pending") or []):
        task_id = str(approval.get("task_id") or "").strip()
        worker_run_id = str(approval.get("worker_run_id") or "").strip()
        if not task_id or task_id in task_map or resolver.source(task_id) == "archive":
            continue
        push(
            {
                "id": f"approval-missing-task:{approval.get('id') or approval.get('approval_id') or task_id}",
                "type": "approval_missing_task",
                "severity": "medium",
                "title": "Approval queue 指向不存在的 task",
                "summary": f"待批准項目 {task_id} 已不在 task board 中。",
                "task_id": task_id,
                "detected_at": approval.get("created_at"),
            }
        )

    severity_order = {"high": 0, "medium": 1, "low": 2}
    mismatches.sort(
        key=lambda item: (
            severity_order.get(str(item.get("severity") or "medium"), 9),
            str(item.get("detected_at") or ""),
        )
    )
    return live_workers, mismatches


def normalized_source_ref(task: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    payload = task.get("source_ref")
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        normalized[str(key)] = text
    return normalized


def build_dashboard_bundle(
    state: dict[str, Any],
    orchestrator_state: dict[str, Any] | None,
    approval_state: dict[str, Any] | None,
) -> dict[str, Any]:
    ai_status = _ai_status_module()
    orchestrator = orchestrator_state or {}
    approvals = approval_state or {}
    config = ai_status.load_config()
    dispatch_policy = build_dispatch_policy_summary(config)
    resolver = ai_status.task_resolver(state)
    task_map = resolver.active_task_map()
    archive_index = load_archive_index()
    archive_counts = archive_index.get("counts", {}) if isinstance(archive_index.get("counts"), dict) else {}
    terminal_projection = terminal_archive_projection(state)
    recent_terminal_tasks = orchestrator.get("recent_terminal_tasks")
    if not isinstance(recent_terminal_tasks, list):
        recent_terminal_tasks = recent_terminal_summaries(limit=ai_status.task_archive_recent_limit())
    workers = normalize_runtime_workers(state, orchestrator)
    queue_events = [
        event
        for event in normalize_runtime_queue(orchestrator)
        if str(event.get("status") or "").lower() not in {"completed", "failed"}
        and resolver.dependency_status(str(event.get("task_id") or "")) not in {"done", ai_status.TASK_TERMINAL_SUPERSEDED}
    ]
    live_workers, mismatches = detect_truth_mismatches(
        state,
        workers,
        queue_events,
        approvals,
        resolver,
        orchestrator,
    )
    supervisor_state = orchestrator.get("supervisor") if isinstance(orchestrator.get("supervisor"), dict) else {}

    live_workers_by_task: dict[str, list[dict[str, Any]]] = {}
    for worker in live_workers:
        task_id = str(worker.get("task_id") or "").strip()
        if task_id:
            live_workers_by_task.setdefault(task_id, []).append(worker)

    ready_now = 0
    dependency_ready = 0
    in_progress = 0
    in_review = 0
    blocked = 0
    review_approved = 0
    done = int(archive_counts.get("completed") or 0)
    superseded = int(archive_counts.get(ai_status.TASK_TERMINAL_SUPERSEDED) or 0)
    for terminal in terminal_projection:
        if not terminal["archive_missing"]:
            continue
        if terminal["terminal_outcome"] == ai_status.TASK_TERMINAL_SUPERSEDED:
            superseded += 1
        else:
            done += 1
    for task in state.get("tasks", []):
        status = str(task.get("status") or "").lower()
        if status == "todo" and all(
            ai_status.dependency_is_satisfied(resolver, dep_id, task)
            for dep_id in task.get("depends_on", [])
        ):
            dependency_ready += 1
            if any(worker.get("bucket") in {"running", "pending"} for worker in live_workers_by_task.get(str(task.get("id") or ""), [])):
                continue
            ready_now += 1
        elif status == "in_progress":
            in_progress += 1
        elif status == "review":
            in_review += 1
        elif status == "blocked":
            blocked += 1
        elif status == "review_approved":
            review_approved += 1

    worker_task_links: list[dict[str, Any]] = []
    mismatch_index: dict[tuple[str, str], list[str]] = {}
    mismatch_detail_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for mismatch in mismatches:
        task_id = str(mismatch.get("task_id") or "")
        run_id = str(mismatch.get("worker_run_id") or "")
        mismatch_index.setdefault((task_id, run_id), []).append(str(mismatch.get("type") or "mismatch"))
        mismatch_detail_index.setdefault((task_id, run_id), []).append(mismatch)
    queue_map = {str(event.get("id") or ""): event for event in queue_events}
    for worker in live_workers:
        task_id = str(worker.get("task_id") or "")
        task = task_map.get(task_id, {})
        if not task and worker.get("task_source") == "handoff" and isinstance(worker.get("handoff"), dict):
            handoff = worker["handoff"]
            task = {
                "id": task_id,
                "title": "Pending handoff",
                "summary_zh": handoff.get("message"),
                "next": handoff.get("message"),
                "status": handoff.get("status") or "pending",
                "owner": handoff.get("to"),
                "reviewer": handoff.get("from"),
                "source_plane": "handoff",
                "source_ref": {"handoff_from": handoff.get("from"), "handoff_to": handoff.get("to")},
            }
        queue_event = queue_map.get(str(worker.get("queue_event_id") or ""), {})
        linked_mismatches = mismatch_detail_index.get((task_id, str(worker.get("run_id") or "")), [])
        worker_task_links.append(
            {
                "task_id": task_id or None,
                "task_title": task.get("title"),
                "task_summary": task.get("summary_zh"),
                "task_next": task.get("next"),
                "task_status": task.get("status"),
                "owner": task.get("owner"),
                "reviewer": task.get("reviewer"),
                "github_review_bridge": task.get(ai_status.GITHUB_REVIEW_BRIDGE_KEY),
                "expected_actor": expected_task_actor(task) if task else None,
                "source_plane": task.get("source_plane"),
                "source_ref": normalized_source_ref(task),
                "worker_run_id": worker.get("run_id"),
                "queue_event_id": worker.get("queue_event_id"),
                "queue_status": queue_event.get("status"),
                "queue_last_event_at": queue_event.get("last_event_at"),
                "actor": worker.get("actor"),
                "provider": worker.get("provider"),
                "task_source": worker.get("task_source"),
                "worker_status": worker.get("status"),
                "runtime_bucket": worker.get("bucket"),
                "dispatch_reason": worker.get("reason"),
                "last_event_at": worker.get("last_event_at"),
                "last_error": worker.get("last_error"),
                "mismatch_flags": mismatch_index.get((task_id, str(worker.get("run_id") or "")), []),
                "mismatch_count": len(linked_mismatches),
                "resolution_hints": [str(item.get("resolution_hint") or "") for item in linked_mismatches if str(item.get("resolution_hint") or "")],
            }
        )

    lanes: dict[str, dict[str, int]] = {}
    for worker in workers:
        actor = str(worker.get("actor") or "-")
        lane = lanes.setdefault(actor, {"running": 0, "pending": 0, "transition": 0, "completed": 0, "failed": 0})
        bucket = str(worker.get("bucket") or "pending")
        if bucket in {"running", "pending"} and not worker.get("is_live_runtime"):
            continue
        lane[bucket] = lane.get(bucket, 0) + 1
        if worker.get("status") == "failed":
            lane["failed"] += 1

    sprint_started_at_value = str(state.get("sprint_started_at") or "").strip() or None
    completed_in_sprint, superseded_in_sprint = count_terminal_since(sprint_started_at_value)

    bff_consol_archived_ids: list[str] = []
    if ARCHIVE_TASKS_DIR.exists():
        for path in ARCHIVE_TASKS_DIR.glob("BFF-CONSOL-*.json"):
            try:
                st = os.lstat(path)
                import stat
                if stat.S_ISLNK(st.st_mode):
                    raise RuntimeError(f"archive-leaf cannot be a symlink: {path}")
                if not stat.S_ISREG(st.st_mode):
                    continue
            except OSError:
                continue
            stem = path.stem
            if stem.endswith("-SIDECAR-BFF-HANDOFF") or stem.endswith("-SIDECAR-ACCEPTANCE") or stem.endswith("-SIDECAR-REVIEW"):
                continue
            bff_consol_archived_ids.append(stem)
    bff_consol_archived_ids.sort()

    return {
        "generated_at": iso_now(),
        "runtime_summary": {
            "supervisor_pid": supervisor_state.get("pid"),
            "heartbeat_at": supervisor_state.get("last_heartbeat_at") or orchestrator.get("last_heartbeat_at"),
            "queue_depth": len(queue_events),
            "pending_approvals": len(approvals.get("pending") or []),
            "running_workers": sum(1 for worker in live_workers if worker.get("bucket") == "running"),
            "pending_workers": sum(1 for worker in live_workers if worker.get("bucket") == "pending"),
            "mismatch_count": len(mismatches),
            "lanes": lanes,
        },
        "execution_summary": {
            "ready_now": ready_now,
            "dependency_ready": dependency_ready,
            "in_progress": in_progress,
            "in_review": in_review,
            "blocked": blocked,
            "review_approved": review_approved,
            "done": done,
            "superseded": superseded,
            "live_attached": sum(1 for linked in live_workers_by_task.values() if any(worker.get("bucket") == "running" for worker in linked)),
            "mismatch_count": len(mismatches),
        },
        "archive_summary": {
            "updated_at": archive_index.get("updated_at"),
            "counts": {
                "total": int(archive_counts.get("total") or 0),
                "completed": done,
                "superseded": superseded,
                "completed_in_sprint": completed_in_sprint,
                "superseded_in_sprint": superseded_in_sprint,
            },
            "sprint_started_at": sprint_started_at_value,
            "recent_terminal_ids": archive_index.get("recent_terminal_ids") or [],
            "recent_terminal_tasks": recent_terminal_tasks,
            "bff_consol_archived_ids": bff_consol_archived_ids,
            "terminal_facts": terminal_projection,
            "archive_missing_task_ids": [
                item["task_id"] for item in terminal_projection if item["archive_missing"]
            ],
        },
        "dispatch_policy": dispatch_policy,
        "worker_task_links": worker_task_links,
        "truth_mismatches": mismatches,
    }


def write_dashboard_bundle(state: dict[str, Any]) -> None:
    ai_status = _ai_status_module()
    config = ai_status.load_config()
    try:
        orchestrator_state = ai_status.load_runtime_state(config)
    except KeyError:
        orchestrator_state = {}
    approval_state = load_json_file(ai_status.APPROVAL_QUEUE_FILE, {"pending": [], "history": []})
    bundle = build_dashboard_bundle(state, orchestrator_state, approval_state)
    ai_status.DASHBOARD_BUNDLE_FILE.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _mirror_log_tail(source: Path, target: Path, max_lines: int) -> None:
    try:
        tail = read_activity_log_tail_bytes(source, max_lines=max_lines)
        if tail is None:
            return
        durable_write_bytes(target, tail)
    except OSError:
        return


def dashboard_orchestrator_state(state: dict[str, Any], orchestrator_state: dict[str, Any]) -> dict[str, Any]:
    dashboard_state = deepcopy(orchestrator_state)
    dashboard_workers = dashboard_state.setdefault("workers", {})
    for worker in normalize_runtime_workers(state, orchestrator_state):
        run_id = str(worker.get("run_id") or "").strip()
        if not run_id or run_id not in dashboard_workers:
            continue
        dashboard_workers[run_id]["pid_alive"] = worker.get("pid_alive")
        dashboard_workers[run_id]["pid_state"] = worker.get("pid_state")
        dashboard_workers[run_id]["is_live_runtime"] = worker.get("is_live_runtime")
        dashboard_workers[run_id]["runtime_bucket"] = worker.get("bucket")
    return dashboard_state


def sync_docs_site(state: dict[str, Any]) -> None:
    ai_status = _ai_status_module()
    ai_status.DOCS_SITE_DIR.mkdir(parents=True, exist_ok=True)
    config = ai_status.load_config()
    try:
        runtime_state = ai_status.load_runtime_state(config)
    except KeyError:
        runtime_state = {}
    mirror_files = [
        ai_status.STATUS_FILE,
        ai_status.CURRENT_WORK_FILE,
        ai_status.DASHBOARD_BUNDLE_FILE,
        ai_status.ORCHESTRATOR_STATE_FILE,
        ai_status.APPROVAL_QUEUE_FILE,
    ]
    rename_map = {
        "state.json": "orchestrator-state.json",
        "approval-queue.json": "approval-queue.json",
    }
    for path in mirror_files:
        if path.exists():
            target_name = rename_map.get(path.name, path.name)
            if path.name == "state.json":
                dashboard_state = dashboard_orchestrator_state(state, runtime_state)
                (ai_status.DOCS_SITE_DIR / target_name).write_text(
                    json.dumps(dashboard_state, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            else:
                shutil.copy2(path, ai_status.DOCS_SITE_DIR / target_name)
    _mirror_log_tail(ai_status.LOG_FILE, ai_status.DOCS_SITE_DIR / ai_status.LOG_FILE.name, ai_status.DASHBOARD_LOG_TAIL_LINES)


def github_review_bridge_evidence_matches(task: Mapping[str, Any]) -> bool:
    """Return whether task evidence recognizes its exact approved PR head."""
    ai_status = _ai_status_module()

    binding = task.get(ai_status.APPROVAL_BINDING_KEY)
    evidence = task.get(ai_status.GITHUB_REVIEW_BRIDGE_KEY)
    if not isinstance(binding, Mapping) or not isinstance(evidence, Mapping):
        return False
    if str(evidence.get("decision") or "").lower() != "approve":
        return False
    if str(evidence.get("mode") or "") not in ai_status.GITHUB_REVIEW_MODES:
        return False
    try:
        if int(evidence.get("pr") or 0) != int(binding.get("pr") or 0):
            return False
    except (TypeError, ValueError):
        return False
    for key in ("head_sha", "head_branch", "base"):
        if str(evidence.get(key) or "").strip() != str(binding.get(key) or "").strip():
            return False

    mode = str(evidence.get("mode") or "")
    review_recorded = bool(evidence.get("github_review_id"))
    required_status_recorded = bool(
        evidence.get("status_id")
        and evidence.get("status_context") == ai_status.GITHUB_CANONICAL_REVIEW_CONTEXT
        and str(evidence.get("status_state") or "").lower() == "success"
    )
    if mode == "pull_request_review":
        return review_recorded
    if mode == "required_commit_status":
        return required_status_recorded
    return review_recorded and required_status_recorded
