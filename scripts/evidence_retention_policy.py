#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TextIO

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

import sidecar_cleanup  # type: ignore  # noqa: E402

DEFAULT_EVIDENCE_ROOT = ROOT / "support" / "evidence"
DEFAULT_SIDECARS_ROOT = ROOT / "support" / "sidecars"
DEFAULT_ARCHIVE_TASKS_DIR = ROOT / "ai-task-archive" / "tasks"
DEFAULT_STATUS_PATH = ROOT / "ai-status.json"
DEFAULT_ARCHIVE_AFTER_DAYS = 14
DISABLED_DELETE_AFTER_DAYS = 1_000_000_000

ACTION_KEEP = "keep"
ACTION_ARCHIVE = "archive"

KIND_EVIDENCE = "evidence"
KIND_SIDECAR = "sidecar"

RETENTION_PROTECTED_EVIDENCE = "protected_evidence"
RETENTION_STANDARD_EVIDENCE = "standard_evidence"
RETENTION_SIDECAR_ARCHIVE_ELIGIBLE = "sidecar_archive_eligible"
RETENTION_SIDECAR_RETAINED = "sidecar_retained"

TEXT_EXTENSIONS = {
    ".csv",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

PROTECTED_SIGNAL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "live",
        (
            r"\blive\b",
            r"\bproduction[-_ ]live\b",
            r"\blive[-_ ]order\b",
            r"\blive[-_ ]broker\b",
        ),
    ),
    ("canary", (r"\bcanary\b",)),
    (
        "human_gate",
        (
            r"\bhuman[-_ ]gate\b",
            r"\brisk[-_ ]owner\b",
            r"\boperator[-_ ]approval\b",
        ),
    ),
    ("rollback", (r"\broll[-_ ]?back",)),
    ("postmortem", (r"\bpost[-_ ]?mortem",)),
)


@dataclass(frozen=True)
class EvidenceRetentionPolicy:
    archive_after_days: int = DEFAULT_ARCHIVE_AFTER_DAYS
    content_scan_bytes: int = 64 * 1024
    content_scan_files: int = 32

    def __post_init__(self) -> None:
        if self.archive_after_days < 0:
            raise ValueError("archive_after_days must be >= 0")
        if self.content_scan_bytes < 0:
            raise ValueError("content_scan_bytes must be >= 0")
        if self.content_scan_files < 0:
            raise ValueError("content_scan_files must be >= 0")

    def to_dict(self) -> dict[str, int]:
        return {
            "archive_after_days": self.archive_after_days,
            "content_scan_bytes": self.content_scan_bytes,
            "content_scan_files": self.content_scan_files,
        }


@dataclass(frozen=True)
class RetentionItem:
    kind: str
    path: Path
    action: str
    retention_class: str
    reason: str
    task_id: str | None = None
    protected: bool = False
    signals: tuple[str, ...] = ()
    age_days: int | None = None
    destination_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": str(self.path),
            "action": self.action,
            "retention_class": self.retention_class,
            "reason": self.reason,
            "task_id": self.task_id,
            "protected": self.protected,
            "signals": list(self.signals),
            "age_days": self.age_days,
            "destination_path": str(self.destination_path) if self.destination_path else None,
        }


@dataclass(frozen=True)
class RetentionPlan:
    generated_at: str
    evidence_root: Path
    sidecars_root: Path
    policy: EvidenceRetentionPolicy
    items: tuple[RetentionItem, ...]

    def archive_actions(self) -> tuple[RetentionItem, ...]:
        return tuple(item for item in self.items if item.action == ACTION_ARCHIVE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "evidence_root": str(self.evidence_root),
            "sidecars_root": str(self.sidecars_root),
            "policy": self.policy.to_dict(),
            "counts": {
                "total": len(self.items),
                KIND_EVIDENCE: sum(1 for item in self.items if item.kind == KIND_EVIDENCE),
                KIND_SIDECAR: sum(1 for item in self.items if item.kind == KIND_SIDECAR),
                ACTION_KEEP: sum(1 for item in self.items if item.action == ACTION_KEEP),
                ACTION_ARCHIVE: sum(1 for item in self.items if item.action == ACTION_ARCHIVE),
                RETENTION_PROTECTED_EVIDENCE: sum(
                    1 for item in self.items if item.retention_class == RETENTION_PROTECTED_EVIDENCE
                ),
                RETENTION_STANDARD_EVIDENCE: sum(
                    1 for item in self.items if item.retention_class == RETENTION_STANDARD_EVIDENCE
                ),
            },
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class ExecutionOperation:
    kind: str
    action: str
    source_path: Path
    destination_path: Path | None
    status: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
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
    plan: RetentionPlan
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


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
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


def _iter_packet_entries(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(root.iterdir(), key=lambda path: path.name)


def _json_fragments(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _json_fragments(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _json_fragments(nested)
    elif isinstance(value, (str, int, float, bool)) or value is None:
        yield str(value)


def _read_text_sample(path: Path, byte_limit: int) -> str:
    if byte_limit == 0:
        return ""
    try:
        data = path.read_bytes()[:byte_limit]
    except OSError:
        return ""
    return data.decode("utf-8", errors="ignore")


def _candidate_files(path: Path, policy: EvidenceRetentionPolicy) -> Iterable[Path]:
    if policy.content_scan_files == 0:
        return []
    if path.is_file():
        return [path] if path.suffix.lower() in TEXT_EXTENSIONS else []
    if not path.is_dir():
        return []
    candidates: list[Path] = []
    for child in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        if len(candidates) >= policy.content_scan_files:
            break
        if child.is_file() and child.suffix.lower() in TEXT_EXTENSIONS:
            candidates.append(child)
    return candidates


def _fragment_text_for_path(path: Path, root: Path, policy: EvidenceRetentionPolicy) -> str:
    fragments: list[str] = []
    try:
        fragments.extend(path.relative_to(root).parts)
    except ValueError:
        fragments.extend(path.parts)

    for candidate in _candidate_files(path, policy):
        try:
            fragments.extend(candidate.relative_to(root).parts)
        except ValueError:
            fragments.extend(candidate.parts)
        text = _read_text_sample(candidate, policy.content_scan_bytes)
        if not text:
            continue
        if candidate.suffix.lower() == ".json":
            try:
                fragments.extend(_json_fragments(json.loads(text)))
                continue
            except json.JSONDecodeError:
                pass
        fragments.append(text)

    return "\n".join(fragments).lower()


def protected_evidence_signals(
    evidence_path: str | Path,
    *,
    evidence_root: str | Path = DEFAULT_EVIDENCE_ROOT,
    policy: EvidenceRetentionPolicy | None = None,
) -> tuple[str, ...]:
    policy = policy or EvidenceRetentionPolicy()
    text = _fragment_text_for_path(Path(evidence_path), Path(evidence_root), policy)
    signals: list[str] = []
    for signal, patterns in PROTECTED_SIGNAL_RULES:
        if any(re.search(pattern, text) for pattern in patterns):
            signals.append(signal)
    return tuple(signals)


def classify_evidence(
    evidence_path: str | Path,
    *,
    evidence_root: str | Path = DEFAULT_EVIDENCE_ROOT,
    policy: EvidenceRetentionPolicy | None = None,
) -> RetentionItem:
    policy = policy or EvidenceRetentionPolicy()
    path = Path(evidence_path)
    signals = protected_evidence_signals(path, evidence_root=evidence_root, policy=policy)
    if signals:
        return RetentionItem(
            kind=KIND_EVIDENCE,
            path=path,
            action=ACTION_KEEP,
            retention_class=RETENTION_PROTECTED_EVIDENCE,
            reason="Part H5 protected evidence is retained in place and never deleted",
            task_id=path.name,
            protected=True,
            signals=signals,
        )
    return RetentionItem(
        kind=KIND_EVIDENCE,
        path=path,
        action=ACTION_KEEP,
        retention_class=RETENTION_STANDARD_EVIDENCE,
        reason="Part H5 standard evidence is retained in place; sidecar archival is handled separately",
        task_id=path.name,
        protected=False,
    )


def scan_evidence(
    evidence_root: str | Path = DEFAULT_EVIDENCE_ROOT,
    *,
    policy: EvidenceRetentionPolicy | None = None,
) -> tuple[RetentionItem, ...]:
    policy = policy or EvidenceRetentionPolicy()
    root = Path(evidence_root)
    return tuple(classify_evidence(path, evidence_root=root, policy=policy) for path in _iter_packet_entries(root))


def _sidecar_policy(policy: EvidenceRetentionPolicy) -> sidecar_cleanup.RetentionPolicy:
    return sidecar_cleanup.RetentionPolicy(
        archive_after_days=policy.archive_after_days,
        delete_after_days=DISABLED_DELETE_AFTER_DAYS,
    )


def _convert_sidecar_item(item: Any) -> RetentionItem:
    action = ACTION_ARCHIVE if item.action == sidecar_cleanup.ACTION_ARCHIVE else ACTION_KEEP
    retention_class = RETENTION_SIDECAR_ARCHIVE_ELIGIBLE if action == ACTION_ARCHIVE else RETENTION_SIDECAR_RETAINED
    reason = item.reason
    if item.action == sidecar_cleanup.ACTION_DELETE:
        reason = "delete suppressed by evidence retention policy; sidecar cleanup archives only in this wrapper"
    return RetentionItem(
        kind=KIND_SIDECAR,
        path=item.packet_path,
        action=action,
        retention_class=retention_class,
        reason=reason,
        task_id=item.task_id,
        protected=False,
        age_days=item.age_days,
        destination_path=item.destination_path if action == ACTION_ARCHIVE else None,
    )


def scan_sidecars(
    sidecars_root: str | Path = DEFAULT_SIDECARS_ROOT,
    *,
    archive_tasks_dir: str | Path = DEFAULT_ARCHIVE_TASKS_DIR,
    status_path: str | Path = DEFAULT_STATUS_PATH,
    now: datetime | str | None = None,
    policy: EvidenceRetentionPolicy | None = None,
) -> tuple[RetentionItem, ...]:
    policy = policy or EvidenceRetentionPolicy()
    sidecar_plan = sidecar_cleanup.scan(
        sidecars_root=sidecars_root,
        archive_tasks_dir=archive_tasks_dir,
        status_path=status_path,
        now=now,
        policy=_sidecar_policy(policy),
        include_archived=False,
    )
    return tuple(_convert_sidecar_item(item) for item in sidecar_plan.items)


def scan(
    *,
    evidence_root: str | Path = DEFAULT_EVIDENCE_ROOT,
    sidecars_root: str | Path = DEFAULT_SIDECARS_ROOT,
    archive_tasks_dir: str | Path = DEFAULT_ARCHIVE_TASKS_DIR,
    status_path: str | Path = DEFAULT_STATUS_PATH,
    now: datetime | str | None = None,
    policy: EvidenceRetentionPolicy | None = None,
    include_evidence: bool = True,
    include_sidecars: bool = True,
) -> RetentionPlan:
    policy = policy or EvidenceRetentionPolicy()
    now_dt = _coerce_datetime(now)
    items: list[RetentionItem] = []
    if include_evidence:
        items.extend(scan_evidence(evidence_root, policy=policy))
    if include_sidecars:
        items.extend(
            scan_sidecars(
                sidecars_root,
                archive_tasks_dir=archive_tasks_dir,
                status_path=status_path,
                now=now_dt,
                policy=policy,
            )
        )
    return RetentionPlan(
        generated_at=_format_timestamp(now_dt),
        evidence_root=Path(evidence_root),
        sidecars_root=Path(sidecars_root),
        policy=policy,
        items=tuple(items),
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


def execute(plan: RetentionPlan, *, dry_run: bool = True) -> RetentionPlan | ExecutionResult:
    if dry_run:
        return plan

    operations: list[ExecutionOperation] = []
    exit_code = 0
    for item in plan.archive_actions():
        if item.kind != KIND_SIDECAR:
            operations.append(
                ExecutionOperation(
                    kind=item.kind,
                    action=item.action,
                    source_path=item.path,
                    destination_path=item.destination_path,
                    status="skipped",
                    error="only sidecar archive actions are executable",
                )
            )
            exit_code = 1
            continue
        if not item.path.exists():
            operations.append(
                ExecutionOperation(
                    kind=item.kind,
                    action=item.action,
                    source_path=item.path,
                    destination_path=item.destination_path,
                    status="missing",
                )
            )
            continue
        try:
            destination = _unique_destination(item.destination_path or (plan.sidecars_root / "archived" / item.path.name))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item.path), str(destination))
            operations.append(
                ExecutionOperation(
                    kind=item.kind,
                    action=item.action,
                    source_path=item.path,
                    destination_path=destination,
                    status="moved",
                )
            )
        except OSError as exc:
            operations.append(
                ExecutionOperation(
                    kind=item.kind,
                    action=item.action,
                    source_path=item.path,
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
    parser = argparse.ArgumentParser(
        description="Classify support/evidence retention and archive eligible support/sidecars packets."
    )
    parser.add_argument("--evidence-root", default=str(DEFAULT_EVIDENCE_ROOT))
    parser.add_argument("--sidecars-root", default=str(DEFAULT_SIDECARS_ROOT))
    parser.add_argument("--archive-tasks-dir", default=str(DEFAULT_ARCHIVE_TASKS_DIR))
    parser.add_argument("--status-path", default=str(DEFAULT_STATUS_PATH))
    parser.add_argument("--archive-after-days", type=int, default=DEFAULT_ARCHIVE_AFTER_DAYS)
    parser.add_argument("--content-scan-bytes", type=int, default=64 * 1024)
    parser.add_argument("--content-scan-files", type=int, default=32)
    parser.add_argument("--now", default=None, help="UTC ISO timestamp override for deterministic dry-runs/tests.")
    parser.add_argument("--execute", action="store_true", help="Archive eligible sidecars. Evidence is never modified.")
    parser.add_argument("--no-evidence-scan", action="store_true", help="Skip support/evidence classification.")
    parser.add_argument("--no-sidecar-scan", action="store_true", help="Skip support/sidecars archive planning.")
    return parser


def main(argv: list[str] | None = None, *, stdout: TextIO | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    stdout = stdout or sys.stdout
    policy = EvidenceRetentionPolicy(
        archive_after_days=args.archive_after_days,
        content_scan_bytes=args.content_scan_bytes,
        content_scan_files=args.content_scan_files,
    )
    plan = scan(
        evidence_root=args.evidence_root,
        sidecars_root=args.sidecars_root,
        archive_tasks_dir=args.archive_tasks_dir,
        status_path=args.status_path,
        now=args.now,
        policy=policy,
        include_evidence=not args.no_evidence_scan,
        include_sidecars=not args.no_sidecar_scan,
    )
    result = execute(plan, dry_run=not args.execute)
    if isinstance(result, RetentionPlan):
        _json_dump({"dry_run": True, "plan": result.to_dict()}, stdout)
        return 0
    _json_dump(result.to_dict(), stdout)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
