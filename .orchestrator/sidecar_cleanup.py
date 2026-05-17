#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TextIO
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SIDECARS_ROOT = ROOT / "support" / "sidecars"
DEFAULT_ARCHIVE_ROOT_NAME = "archived"
DEFAULT_ARCHIVE_TASKS_DIR = ROOT / "ai-task-archive" / "tasks"
DEFAULT_STATUS_PATH = ROOT / "ai-status.json"
DEFAULT_ARCHIVE_AFTER_DAYS = 14
DEFAULT_DELETE_AFTER_DAYS = 60

ACTION_KEEP = "keep"
ACTION_ARCHIVE = "archive"
ACTION_DELETE = "delete"


@dataclass(frozen=True)
class RetentionPolicy:
    archive_after_days: int = DEFAULT_ARCHIVE_AFTER_DAYS
    delete_after_days: int = DEFAULT_DELETE_AFTER_DAYS

    def __post_init__(self) -> None:
        if self.archive_after_days < 0:
            raise ValueError("archive_after_days must be >= 0")
        if self.delete_after_days < self.archive_after_days:
            raise ValueError("delete_after_days must be >= archive_after_days")

    def to_dict(self) -> dict[str, int]:
        return {
            "archive_after_days": self.archive_after_days,
            "delete_after_days": self.delete_after_days,
        }


@dataclass(frozen=True)
class CleanupItem:
    task_id: str
    packet_path: Path
    action: str
    reason: str
    parent_done_at: str | None = None
    age_days: int | None = None
    already_archived: bool = False
    destination_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "packet_path": str(self.packet_path),
            "action": self.action,
            "reason": self.reason,
            "parent_done_at": self.parent_done_at,
            "age_days": self.age_days,
            "already_archived": self.already_archived,
            "destination_path": str(self.destination_path) if self.destination_path else None,
        }


@dataclass(frozen=True)
class CleanupPlan:
    generated_at: str
    sidecars_root: Path
    archive_root: Path
    policy: RetentionPolicy
    items: tuple[CleanupItem, ...]

    def actions(self) -> tuple[CleanupItem, ...]:
        return tuple(item for item in self.items if item.action != ACTION_KEEP)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "sidecars_root": str(self.sidecars_root),
            "archive_root": str(self.archive_root),
            "policy": self.policy.to_dict(),
            "counts": {
                "total": len(self.items),
                ACTION_KEEP: sum(1 for item in self.items if item.action == ACTION_KEEP),
                ACTION_ARCHIVE: sum(1 for item in self.items if item.action == ACTION_ARCHIVE),
                ACTION_DELETE: sum(1 for item in self.items if item.action == ACTION_DELETE),
            },
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class ExecutionOperation:
    task_id: str
    action: str
    source_path: Path
    destination_path: Path | None
    status: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "action": self.action,
            "source_path": str(self.source_path),
            "destination_path": str(self.destination_path) if self.destination_path else None,
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class ExecutionResult:
    dry_run: bool
    exit_code: int
    plan: CleanupPlan
    operations: tuple[ExecutionOperation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "exit_code": self.exit_code,
            "plan": self.plan.to_dict(),
            "operations": [operation.to_dict() for operation in self.operations],
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_json(path: Path) -> Any | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not text:
        return None
    return json.loads(text)


def _archive_task_path(archive_tasks_dir: Path, task_id: str) -> Path:
    return archive_tasks_dir / f"{quote(task_id, safe='-_.')}.json"


def _is_terminal_done_snapshot(snapshot: dict[str, Any]) -> bool:
    terminal_status = str(snapshot.get("terminal_status") or "").strip().lower()
    task = snapshot.get("task") if isinstance(snapshot.get("task"), dict) else {}
    task_status = str(task.get("status") or "").strip().lower()
    return terminal_status == "done" or task_status == "done"


def _find_status_task(status_path: Path, task_id: str) -> dict[str, Any] | None:
    payload = _load_json(status_path)
    if not isinstance(payload, dict):
        return None
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return None
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if str(task.get("id") or "").strip() == task_id:
            return task
    return None


def _load_parent_done_record(
    task_id: str,
    *,
    archive_tasks_dir: Path,
    status_path: Path,
) -> tuple[dict[str, Any] | None, str]:
    snapshot = _load_json(_archive_task_path(archive_tasks_dir, task_id))
    if isinstance(snapshot, dict) and _is_terminal_done_snapshot(snapshot):
        return snapshot, "archive"

    task = _find_status_task(status_path, task_id)
    if isinstance(task, dict) and str(task.get("status") or "").strip().lower() == "done":
        return {"task_id": task_id, "terminal_status": "done", "task": task}, "status"

    return None, "missing"


def _done_at_from_record(record: dict[str, Any]) -> datetime | None:
    task = record.get("task") if isinstance(record.get("task"), dict) else {}
    candidates: Iterable[Any] = (
        task.get("last_update"),
        (task.get("delivery") or {}).get("recorded_at") if isinstance(task.get("delivery"), dict) else None,
        record.get("archived_at"),
        task.get("completed_at"),
        task.get("done_at"),
    )
    for candidate in candidates:
        parsed = _parse_timestamp(candidate)
        if parsed:
            return parsed
    return None


def _age_days(done_at: datetime, now: datetime) -> int:
    seconds = (now - done_at).total_seconds()
    if seconds <= 0:
        return 0
    return int(seconds // 86_400)


def _coerce_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return _utc_now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    parsed = _parse_timestamp(value)
    if not parsed:
        raise ValueError(f"Could not parse timestamp: {value}")
    return parsed


def _packet_identity(sidecar_path: Path, sidecars_root: Path) -> tuple[str, Path, bool]:
    sidecar_path = Path(sidecar_path)
    root = sidecars_root.resolve()
    candidate = sidecar_path.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return sidecar_path.name, sidecar_path, sidecar_path.parent.name == DEFAULT_ARCHIVE_ROOT_NAME

    if not relative.parts:
        raise ValueError("sidecar_path must point to a sidecar packet, not the sidecars root")

    if relative.parts[0] == DEFAULT_ARCHIVE_ROOT_NAME:
        if len(relative.parts) < 2:
            raise ValueError("archived root is not a sidecar packet")
        task_id = relative.parts[1]
        return task_id, sidecars_root / DEFAULT_ARCHIVE_ROOT_NAME / task_id, True

    task_id = relative.parts[0]
    return task_id, sidecars_root / task_id, False


def _archive_destination(archive_root: Path, task_id: str) -> Path:
    return archive_root / task_id


def classify(
    sidecar_path: str | Path,
    *,
    sidecars_root: str | Path = DEFAULT_SIDECARS_ROOT,
    archive_tasks_dir: str | Path = DEFAULT_ARCHIVE_TASKS_DIR,
    status_path: str | Path = DEFAULT_STATUS_PATH,
    now: datetime | str | None = None,
    policy: RetentionPolicy | None = None,
) -> CleanupItem:
    policy = policy or RetentionPolicy()
    now_dt = _coerce_datetime(now)
    root = Path(sidecars_root)
    archive_root = root / DEFAULT_ARCHIVE_ROOT_NAME
    task_id, packet_path, already_archived = _packet_identity(Path(sidecar_path), root)

    record, source = _load_parent_done_record(
        task_id,
        archive_tasks_dir=Path(archive_tasks_dir),
        status_path=Path(status_path),
    )
    if not record:
        return CleanupItem(
            task_id=task_id,
            packet_path=packet_path,
            action=ACTION_KEEP,
            reason="parent task is not archived as done",
            already_archived=already_archived,
        )

    done_at = _done_at_from_record(record)
    if not done_at:
        return CleanupItem(
            task_id=task_id,
            packet_path=packet_path,
            action=ACTION_KEEP,
            reason=f"parent task done record in {source} has no parseable timestamp",
            already_archived=already_archived,
        )

    age = _age_days(done_at, now_dt)
    parent_done_at = _format_timestamp(done_at)
    if age >= policy.delete_after_days:
        return CleanupItem(
            task_id=task_id,
            packet_path=packet_path,
            action=ACTION_DELETE,
            reason=f"parent task has been done for {age} days, at or beyond delete threshold",
            parent_done_at=parent_done_at,
            age_days=age,
            already_archived=already_archived,
        )

    if already_archived:
        return CleanupItem(
            task_id=task_id,
            packet_path=packet_path,
            action=ACTION_KEEP,
            reason="packet is already archived and below delete threshold",
            parent_done_at=parent_done_at,
            age_days=age,
            already_archived=True,
        )

    if age >= policy.archive_after_days:
        return CleanupItem(
            task_id=task_id,
            packet_path=packet_path,
            action=ACTION_ARCHIVE,
            reason=f"parent task has been done for {age} days, at or beyond archive threshold",
            parent_done_at=parent_done_at,
            age_days=age,
            already_archived=False,
            destination_path=_archive_destination(archive_root, task_id),
        )

    return CleanupItem(
        task_id=task_id,
        packet_path=packet_path,
        action=ACTION_KEEP,
        reason="parent task is below archive threshold",
        parent_done_at=parent_done_at,
        age_days=age,
        already_archived=False,
    )


def _iter_sidecar_packets(sidecars_root: Path, *, include_archived: bool) -> Iterable[Path]:
    if not sidecars_root.exists():
        return []
    children: list[Path] = []
    archive_root = sidecars_root / DEFAULT_ARCHIVE_ROOT_NAME
    for child in sorted(sidecars_root.iterdir(), key=lambda path: path.name):
        if child.name == DEFAULT_ARCHIVE_ROOT_NAME:
            continue
        children.append(child)
    if include_archived and archive_root.exists():
        for child in sorted(archive_root.iterdir(), key=lambda path: path.name):
            children.append(child)
    return children


def scan(
    sidecars_root: str | Path = DEFAULT_SIDECARS_ROOT,
    *,
    archive_tasks_dir: str | Path = DEFAULT_ARCHIVE_TASKS_DIR,
    status_path: str | Path = DEFAULT_STATUS_PATH,
    now: datetime | str | None = None,
    policy: RetentionPolicy | None = None,
    include_archived: bool = True,
) -> CleanupPlan:
    policy = policy or RetentionPolicy()
    now_dt = _coerce_datetime(now)
    root = Path(sidecars_root)
    archive_root = root / DEFAULT_ARCHIVE_ROOT_NAME
    items = tuple(
        classify(
            packet,
            sidecars_root=root,
            archive_tasks_dir=archive_tasks_dir,
            status_path=status_path,
            now=now_dt,
            policy=policy,
        )
        for packet in _iter_sidecar_packets(root, include_archived=include_archived)
    )
    return CleanupPlan(
        generated_at=_format_timestamp(now_dt),
        sidecars_root=root,
        archive_root=archive_root,
        policy=policy,
        items=items,
    )


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    timestamp = _format_timestamp(_utc_now()).replace(":", "").replace("-", "")
    candidate = path.with_name(f"{path.name}-{timestamp}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}-{timestamp}-{counter}")
        counter += 1
    return candidate


def _remove_packet(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def execute(plan: CleanupPlan, *, dry_run: bool = True) -> CleanupPlan | ExecutionResult:
    if dry_run:
        return plan

    operations: list[ExecutionOperation] = []
    exit_code = 0
    for item in plan.items:
        if item.action == ACTION_KEEP:
            continue

        if not item.packet_path.exists():
            operations.append(
                ExecutionOperation(
                    task_id=item.task_id,
                    action=item.action,
                    source_path=item.packet_path,
                    destination_path=item.destination_path,
                    status="missing",
                )
            )
            continue

        try:
            if item.action == ACTION_ARCHIVE:
                destination = _unique_destination(item.destination_path or (plan.archive_root / item.task_id))
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(item.packet_path), str(destination))
                operations.append(
                    ExecutionOperation(
                        task_id=item.task_id,
                        action=item.action,
                        source_path=item.packet_path,
                        destination_path=destination,
                        status="moved",
                    )
                )
            elif item.action == ACTION_DELETE:
                _remove_packet(item.packet_path)
                operations.append(
                    ExecutionOperation(
                        task_id=item.task_id,
                        action=item.action,
                        source_path=item.packet_path,
                        destination_path=None,
                        status="deleted",
                    )
                )
            else:
                operations.append(
                    ExecutionOperation(
                        task_id=item.task_id,
                        action=item.action,
                        source_path=item.packet_path,
                        destination_path=item.destination_path,
                        status="unsupported",
                        error=f"unsupported action: {item.action}",
                    )
                )
                exit_code = 1
        except OSError as exc:
            operations.append(
                ExecutionOperation(
                    task_id=item.task_id,
                    action=item.action,
                    source_path=item.packet_path,
                    destination_path=item.destination_path,
                    status="error",
                    error=str(exc),
                )
            )
            exit_code = 1

    return ExecutionResult(dry_run=False, exit_code=exit_code, plan=plan, operations=tuple(operations))


def _json_dump(payload: Any, stdout: TextIO) -> None:
    stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or execute support/sidecars retention cleanup.")
    parser.add_argument("--sidecars-root", default=str(DEFAULT_SIDECARS_ROOT))
    parser.add_argument("--archive-tasks-dir", default=str(DEFAULT_ARCHIVE_TASKS_DIR))
    parser.add_argument("--status-path", default=str(DEFAULT_STATUS_PATH))
    parser.add_argument("--archive-after-days", type=int, default=DEFAULT_ARCHIVE_AFTER_DAYS)
    parser.add_argument("--delete-after-days", type=int, default=DEFAULT_DELETE_AFTER_DAYS)
    parser.add_argument("--now", default=None, help="UTC ISO timestamp override for deterministic dry-runs/tests.")
    parser.add_argument("--execute", action="store_true", help="Apply the planned archive/delete actions. Default is dry-run.")
    parser.add_argument("--no-archived-scan", action="store_true", help="Skip scanning support/sidecars/archived for deletions.")
    return parser


def main(argv: list[str] | None = None, *, stdout: TextIO | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    stdout = stdout or sys.stdout
    policy = RetentionPolicy(
        archive_after_days=args.archive_after_days,
        delete_after_days=args.delete_after_days,
    )
    plan = scan(
        sidecars_root=args.sidecars_root,
        archive_tasks_dir=args.archive_tasks_dir,
        status_path=args.status_path,
        now=args.now,
        policy=policy,
        include_archived=not args.no_archived_scan,
    )
    result = execute(plan, dry_run=not args.execute)
    if isinstance(result, CleanupPlan):
        _json_dump({"dry_run": True, "plan": result.to_dict()}, stdout)
        return 0
    _json_dump(result.to_dict(), stdout)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
