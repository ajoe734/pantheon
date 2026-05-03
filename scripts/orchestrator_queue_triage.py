#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP = ROOT / ".orchestrator/backups/event-queue.pre-blueprint-execution-20260503T130840Z.jsonl"
DEFAULT_ACTIVE = ROOT / ".orchestrator/event-queue.jsonl"
AI_STATUS = ROOT / "ai-status.json"
ARCHIVE_DIR = ROOT / "ai-task-archive/tasks"
TERMINAL_TASK_STATUSES = {"done", "review_approved", "completed", "closed"}
TERMINAL_PAYLOAD_STATUSES = {"closed", "complete", "completed", "done", "loop-complete"}
REPLAYABLE_OPEN_PAYLOAD_STATUSES = {"pending", "needs-runtime", "blocked", "follow_up", "follow-up"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{lineno}: invalid JSONL: {exc}") from exc
        if not isinstance(item, dict):
            raise SystemExit(f"{path}:{lineno}: expected object")
        item["_source_line"] = lineno
        events.append(item)
    return events


def load_active_tasks() -> dict[str, dict[str, Any]]:
    if not AI_STATUS.exists():
        return {}
    data = json.loads(AI_STATUS.read_text())
    return {str(task.get("id")): task for task in data.get("tasks", []) if task.get("id")}


def load_archived_task_statuses() -> dict[str, str]:
    archived: dict[str, str] = {}
    if not ARCHIVE_DIR.exists():
        return archived
    for path in ARCHIVE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        task_id = str(data.get("task_id") or data.get("task", {}).get("id") or path.stem)
        status = str(data.get("terminal_status") or data.get("task", {}).get("status") or "")
        if task_id and status:
            archived[task_id] = status
    return archived


def coordination_payload(event: dict[str, Any]) -> dict[str, Any]:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    coordination = metadata.get("coordination") if isinstance(metadata.get("coordination"), dict) else {}
    payload = coordination.get("payload")
    return payload if isinstance(payload, dict) else {}


def payload_status(event: dict[str, Any]) -> str:
    payload = coordination_payload(event)
    return str(payload.get("status") or payload.get("pantheon_disposition") or "").strip().lower()


def payload_type(event: dict[str, Any]) -> str:
    payload = coordination_payload(event)
    return str(payload.get("type") or "").strip()


def coordination_files(feature_id: str) -> list[str]:
    matches: list[str] = []
    for base in (ROOT / ".coordination/requests", ROOT / ".coordination/responses"):
        if not base.exists():
            continue
        for path in sorted(base.glob(f"{feature_id}*")):
            matches.append(str(path.relative_to(ROOT)))
    return matches


def classify_event(
    event: dict[str, Any],
    active_tasks: dict[str, dict[str, Any]],
    archived_tasks: dict[str, str],
) -> tuple[str, str]:
    reason = str(event.get("reason") or "")
    task_id = str(event.get("task_id") or "")
    status = payload_status(event)

    if reason.startswith("chair_review:"):
        return "do_not_replay_chair_review", "chair review events are runtime snapshots"
    if not reason.startswith("coordination:"):
        return "do_not_replay_non_coordination", "non-coordination dispatch belongs to a past supervisor run"

    active = active_tasks.get(task_id)
    if active:
        active_status = str(active.get("status") or "").lower()
        if active_status in TERMINAL_TASK_STATUSES:
            return "do_not_replay_active_terminal", f"active task status is {active_status}"
        if status in TERMINAL_PAYLOAD_STATUSES:
            return "do_not_replay_payload_terminal", f"payload status is {status}"
        return "replay_candidate_active_open", f"active task status is {active_status or 'unknown'}"

    archived_status = archived_tasks.get(task_id)
    if archived_status and archived_status.lower() in TERMINAL_TASK_STATUSES:
        return "do_not_replay_archived_terminal", f"archived task status is {archived_status}"

    if status in TERMINAL_PAYLOAD_STATUSES:
        return "do_not_replay_payload_terminal", f"payload status is {status}"

    if status in REPLAYABLE_OPEN_PAYLOAD_STATUSES or reason == "coordination:needs-runtime":
        return "manual_review_required_open_payload", f"payload status is {status or 'empty'}"

    files = coordination_files(task_id)
    if files:
        return "manual_review_required_coordination_artifact", f"{len(files)} coordination artifact(s) still exist"
    return "stale_or_unknown_coordination", "no active task or exact archive match"


def build_report(backup: Path, active: Path) -> dict[str, Any]:
    backup_events = read_jsonl(backup)
    active_events = read_jsonl(active)
    active_tasks = load_active_tasks()
    archived_tasks = load_archived_task_statuses()

    classifications: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    payload_statuses: Counter[str] = Counter()
    payload_types: Counter[str] = Counter()
    by_task: defaultdict[str, Counter[str]] = defaultdict(Counter)
    events: list[dict[str, Any]] = []

    for event in backup_events:
        classification, note = classify_event(event, active_tasks, archived_tasks)
        task_id = str(event.get("task_id") or "")
        classifications[classification] += 1
        reasons[str(event.get("reason") or "")] += 1
        providers[str(event.get("provider") or "")] += 1
        payload_statuses[payload_status(event) or "<empty>"] += 1
        payload_types[payload_type(event) or "<empty>"] += 1
        by_task[task_id][classification] += 1
        events.append(
            {
                "line": event.get("_source_line"),
                "event_id": event.get("event_id"),
                "created_at": event.get("created_at"),
                "task_id": task_id,
                "reason": event.get("reason"),
                "provider": event.get("provider"),
                "payload_type": payload_type(event),
                "payload_status": payload_status(event),
                "classification": classification,
                "note": note,
            }
        )

    active_reason_counts = Counter(str(event.get("reason") or "") for event in active_events)
    replayable = [event for event in events if event["classification"] == "replay_candidate_active_open"]
    return {
        "backup_path": str(backup.relative_to(ROOT) if backup.is_relative_to(ROOT) else backup),
        "active_path": str(active.relative_to(ROOT) if active.is_relative_to(ROOT) else active),
        "backup_event_count": len(backup_events),
        "active_event_count": len(active_events),
        "active_reason_counts": dict(sorted(active_reason_counts.items())),
        "backup_reason_counts": dict(sorted(reasons.items())),
        "provider_counts": dict(sorted(providers.items())),
        "payload_type_counts": dict(sorted(payload_types.items())),
        "payload_status_counts": dict(sorted(payload_statuses.items())),
        "classification_counts": dict(sorted(classifications.items())),
        "unique_task_count": len(by_task),
        "top_tasks": [
            {"task_id": task_id, "count": sum(counter.values()), "classifications": dict(counter)}
            for task_id, counter in sorted(
                by_task.items(),
                key=lambda item: (-sum(item[1].values()), item[0]),
            )[:25]
        ],
        "replay_candidate_count": len(replayable),
        "replay_candidates": replayable,
        "events": events,
    }


def print_markdown(report: dict[str, Any]) -> None:
    print("# Coordination Queue Triage")
    print()
    print(f"- Backup: `{report['backup_path']}`")
    print(f"- Active queue: `{report['active_path']}`")
    print(f"- Backup events: {report['backup_event_count']}")
    print(f"- Active events: {report['active_event_count']}")
    print(f"- Replay candidates: {report['replay_candidate_count']}")
    print()
    print("## Classification Counts")
    print()
    for key, value in report["classification_counts"].items():
        print(f"- `{key}`: {value}")
    print()
    print("## Reason Counts")
    print()
    for key, value in report["backup_reason_counts"].items():
        print(f"- `{key}`: {value}")
    print()
    print("## Top Tasks")
    print()
    for item in report["top_tasks"]:
        print(f"- `{item['task_id']}`: {item['count']} ({item['classifications']})")


def write_replayable_jsonl(report: dict[str, Any], backup: Path, output: Path) -> None:
    candidate_ids = {item["event_id"] for item in report["replay_candidates"]}
    lines: list[str] = []
    for event in read_jsonl(backup):
        if event.get("event_id") in candidate_ids:
            event.pop("_source_line", None)
            lines.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + ("\n" if lines else ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run triage for isolated orchestrator queue backups.")
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    parser.add_argument("--active", type=Path, default=DEFAULT_ACTIVE)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--replayable-jsonl", type=Path, help="Write only replay_candidate_active_open events to this JSONL file.")
    args = parser.parse_args()

    backup = args.backup if args.backup.is_absolute() else ROOT / args.backup
    active = args.active if args.active.is_absolute() else ROOT / args.active
    report = build_report(backup, active)
    if args.replayable_jsonl:
        output = args.replayable_jsonl if args.replayable_jsonl.is_absolute() else ROOT / args.replayable_jsonl
        write_replayable_jsonl(report, backup, output)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print_markdown(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
