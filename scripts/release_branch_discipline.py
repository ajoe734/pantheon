#!/usr/bin/env python3
"""Validate release branch discipline for publish cuts.

The current per-task branch workflow uses .orchestrator/release-state.json
as the maintained release policy source and git release/publish refs as the
release history.  The legacy wave-aligned validator remains for historical
wave release-state files and tests:

    wave 2026-W25 -> publish/v2026.25.0 and release/v2026.25.0

This module is intentionally side-effect free.  It reads status/config
state, builds a validation report, and exits non-zero when a publish branch
would violate the configured discipline.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS_FILE = ROOT / "ai-status.json"
DEFAULT_CONFIG_FILE = ROOT / ".orchestrator" / "config.json"
DEFAULT_ARCHIVE_DIR = ROOT / "ai-task-archive" / "tasks"
DEFAULT_RELEASE_STATE_FILE = ROOT / ".orchestrator" / "release-state.json"

WAVE_ID_RE = re.compile(r"^(\d{4})-W(\d{1,2})$")
PUBLISH_VERSION_RE = re.compile(r"^v(\d{4})\.(\d{2})\.(\d+)$")
DAILY_PUBLISH_VERSION_RE = re.compile(r"^v(\d{4})\.(\d{2})\.(\d{2})\.(\d+)$")
TERMINAL_ACTIVE_STATUSES = {"done"}
REQUIRED_DELIVERY_METADATA = ("LLM-Agent", "Task-ID", "Reviewer")
PER_TASK_VERSION_FORMAT = "vYYYY.MM.DD.N"


class ReleaseDisciplineError(ValueError):
    """Raised when a wave or publish version is malformed."""


def load_json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def parse_wave_id(wave_id: str) -> tuple[int, int]:
    match = WAVE_ID_RE.fullmatch(str(wave_id or "").strip())
    if not match:
        raise ReleaseDisciplineError(f"invalid wave id {wave_id!r}; expected YYYY-WNN")
    year = int(match.group(1))
    week = int(match.group(2))
    max_week = last_iso_week_of_year(year)
    if week < 1 or week > max_week:
        raise ReleaseDisciplineError(
            f"invalid wave id {wave_id!r}; ISO year {year} has weeks 1..{max_week}"
        )
    return year, week


def last_iso_week_of_year(year: int) -> int:
    return datetime(year, 12, 28).isocalendar()[1]


def canonical_wave_id(wave_id: str) -> str:
    year, week = parse_wave_id(wave_id)
    return f"{year}-W{week:02d}"


def successor_wave_id(wave_id: str) -> str:
    year, week = parse_wave_id(wave_id)
    if week < last_iso_week_of_year(year):
        return f"{year}-W{week + 1:02d}"
    return f"{year + 1}-W01"


def wave_id_range(start_wave_id: str, end_wave_id: str) -> list[str]:
    start = canonical_wave_id(start_wave_id)
    end = canonical_wave_id(end_wave_id)
    waves = [start]
    current = start
    for _ in range(600):
        if current == end:
            return waves
        current = successor_wave_id(current)
        waves.append(current)
    raise ReleaseDisciplineError(f"wave range from {start!r} to {end!r} is too large")


def wave_to_publish_version(wave_id: str, patch: int = 0) -> str:
    year, week = parse_wave_id(wave_id)
    return f"v{year}.{week:02d}.{patch}"


def publish_version_to_wave_id(version: str) -> str:
    match = PUBLISH_VERSION_RE.fullmatch(str(version or "").strip())
    if not match:
        raise ReleaseDisciplineError(f"invalid publish version {version!r}; expected vYYYY.WW.N")
    year = int(match.group(1))
    week = int(match.group(2))
    parse_wave_id(f"{year}-W{week:02d}")
    return f"{year}-W{week:02d}"


def workflow_settings(config: dict[str, Any]) -> dict[str, str]:
    workflow = config.get("branch_workflow") or config.get("wave_workflow") or {}
    nightly_publish = workflow.get("nightly_publish") or {}
    return {
        "publish_branch_prefix": str(workflow.get("publish_branch_prefix") or "publish/"),
        "release_tag_prefix": str(workflow.get("release_tag_prefix") or "release/"),
        "release_candidate_prefix": str(
            workflow.get("release_candidate_branch_prefix") or "release-candidate/"
        ),
        "version_format": str(nightly_publish.get("version_format") or "vYYYY.WW.0"),
        "release_state_file": str(
            workflow.get("release_state_file")
            or nightly_publish.get("release_state_file")
            or ".orchestrator/release-state.json"
        ),
    }


def release_state_mode(release_state: dict[str, Any] | None) -> str:
    if not isinstance(release_state, dict):
        return ""
    return str(release_state.get("mode") or "").strip()


def _release_state_version_format(
    settings: dict[str, str],
    release_state: dict[str, Any] | None,
) -> str:
    if isinstance(release_state, dict) and release_state.get("version_format"):
        return str(release_state["version_format"])
    return settings["version_format"]


def list_git_release_versions(
    release_prefix: str = "release/",
    publish_prefix: str = "publish/",
) -> list[str]:
    """Return known release/publish versions from local refs without mutating git."""
    versions: set[str] = set()
    ref_specs = ("refs/tags/", "refs/remotes/origin/")
    try:
        out = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short)", *ref_specs],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    remote_publish_prefix = f"origin/{publish_prefix}"
    for line in out.splitlines():
        ref = line.strip()
        if ref.startswith(release_prefix):
            versions.add(ref[len(release_prefix) :])
        elif ref.startswith(remote_publish_prefix):
            versions.add(ref[len(remote_publish_prefix) :])
    return sorted(versions)


def next_daily_publish_version(
    now: datetime | None = None,
    existing_versions: list[str] | None = None,
) -> str:
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    prefix = f"v{today:%Y.%m.%d}."
    existing_patch_numbers: list[int] = []
    for version in existing_versions or []:
        if not version.startswith(prefix):
            continue
        match = DAILY_PUBLISH_VERSION_RE.fullmatch(version)
        if match:
            existing_patch_numbers.append(int(match.group(4)))
    patch = max(existing_patch_numbers) + 1 if existing_patch_numbers else 0
    return f"{prefix}{patch}"


def resolve_publish_version(
    version_format: str,
    wave_id: str | None = None,
    now: datetime | None = None,
    existing_versions: list[str] | None = None,
) -> str:
    if version_format == PER_TASK_VERSION_FORMAT:
        return next_daily_publish_version(now=now, existing_versions=existing_versions)
    if wave_id:
        return wave_to_publish_version(wave_id)
    raise ReleaseDisciplineError(
        f"cannot resolve publish version for format {version_format!r} without a wave id"
    )


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _wave_events(wave_state: dict[str, Any], wave_id: str) -> list[dict[str, Any]]:
    target = canonical_wave_id(wave_id)
    events: list[dict[str, Any]] = []
    for event in wave_state.get("history") or []:
        try:
            event_wave_id = canonical_wave_id(str(event.get("wave_id") or target))
        except ReleaseDisciplineError:
            continue
        if event_wave_id == target:
            events.append(event)

    current_matches = False
    try:
        current_matches = canonical_wave_id(str(wave_state.get("current_wave_id") or "")) == target
    except ReleaseDisciplineError:
        current_matches = False
    if not events and current_matches:
        if wave_state.get("opened_at"):
            events.append({"event": "open", "wave_id": target, "ts": wave_state.get("opened_at")})
        if wave_state.get("frozen_at"):
            events.append({"event": "freeze", "wave_id": target, "ts": wave_state.get("frozen_at")})
        if wave_state.get("closed_at"):
            events.append({"event": "close", "wave_id": target, "ts": wave_state.get("closed_at")})
    return events


def _opened_wave_ids(wave_state: dict[str, Any]) -> list[str]:
    opened: list[str] = []
    for event in wave_state.get("history") or []:
        if event.get("event") != "open":
            continue
        try:
            wave_id = canonical_wave_id(str(event.get("wave_id") or ""))
        except ReleaseDisciplineError:
            continue
        if wave_id not in opened:
            opened.append(wave_id)
    current_wave_id = wave_state.get("current_wave_id")
    if current_wave_id and wave_state.get("opened_at"):
        try:
            wave_id = canonical_wave_id(str(current_wave_id))
        except ReleaseDisciplineError:
            wave_id = ""
        if wave_id and wave_id not in opened:
            opened.append(wave_id)
    return opened


def validate_wave_sequence(wave_state: dict[str, Any], target_wave_id: str) -> list[str]:
    errors: list[str] = []
    target = canonical_wave_id(target_wave_id)
    opened = _opened_wave_ids(wave_state)
    if not opened:
        return [f"wave history has no open event; cannot publish {target}"]
    if target not in opened:
        return [f"target wave {target} has no open event in wave history"]

    first_open = opened[0]
    expected = wave_id_range(first_open, target)
    missing = [wave_id for wave_id in expected if wave_id not in opened]
    if missing:
        errors.append(
            "missing wave open events before publish "
            f"{target}: {', '.join(missing)}"
        )
    return errors


def validate_wave_closed_after_freeze(wave_state: dict[str, Any], target_wave_id: str) -> tuple[list[str], dict[str, Any]]:
    target = canonical_wave_id(target_wave_id)
    events = _wave_events(wave_state, target)
    if not events:
        return [f"target wave {target} has no lifecycle events"], {}

    open_index = next((index for index, event in enumerate(events) if event.get("event") == "open"), None)
    if open_index is None:
        return [f"target wave {target} has no open event"], {}

    freeze_index = next(
        (index for index, event in enumerate(events[open_index + 1 :], start=open_index + 1) if event.get("event") == "freeze"),
        None,
    )
    close_index = next(
        (index for index, event in enumerate(events[open_index + 1 :], start=open_index + 1) if event.get("event") == "close"),
        None,
    )

    errors: list[str] = []
    if freeze_index is None:
        errors.append(f"target wave {target} must be frozen before publish")
    if close_index is None:
        errors.append(f"target wave {target} must be closed before publish")
    if freeze_index is not None and close_index is not None and close_index < freeze_index:
        errors.append(f"target wave {target} close event must occur after freeze")

    lifecycle = {
        "opened_at": events[open_index].get("ts"),
        "frozen_at": events[freeze_index].get("ts") if freeze_index is not None else None,
        "closed_at": events[close_index].get("ts") if close_index is not None else None,
    }
    return errors, lifecycle


def _delivery_evidence_errors(task: dict[str, Any], task_id: str) -> list[str]:
    delivery = task.get("delivery")
    if not isinstance(delivery, dict):
        return [f"task {task_id} is done but has no delivery metadata"]
    errors: list[str] = []
    if not delivery.get("commit"):
        errors.append(f"task {task_id} delivery metadata is missing commit")
    metadata = delivery.get("commit_metadata")
    if not isinstance(metadata, dict):
        errors.append(f"task {task_id} delivery metadata is missing commit trailers")
        return errors
    for field_name in REQUIRED_DELIVERY_METADATA:
        if not str(metadata.get(field_name) or "").strip():
            errors.append(f"task {task_id} delivery metadata is missing {field_name}")
    return errors


def _terminal_evidence_errors(task: dict[str, Any], task_id: str, terminal_outcome: str | None = None) -> list[str]:
    outcome = str(terminal_outcome or task.get("terminal_outcome") or "").strip()
    if outcome == "superseded":
        if task.get("superseded_by") or task.get("next"):
            return []
        return [f"task {task_id} is superseded but has no terminal note or replacement"]
    return _delivery_evidence_errors(task, task_id)


def _is_reconciled_non_dispatchable_gate(task: dict[str, Any]) -> bool:
    """Return True for explicit human-gate placeholders that are not engineering closeout."""
    return (
        task.get("non_dispatchable") is True
        and str(task.get("gate_status") or "").strip() == "pending_human_go_no_go"
        and not task.get("allowed_workers")
    )


def _archive_entries_in_wave(archive_dir: Path, opened_at: str | None, closed_at: str | None) -> list[dict[str, Any]]:
    if not archive_dir.exists():
        return []
    opened = _parse_ts(opened_at)
    closed = _parse_ts(closed_at)
    if opened is None or closed is None:
        return []

    entries: list[dict[str, Any]] = []
    for path in sorted(archive_dir.glob("*.json")):
        payload = load_json_file(path, {})
        if not isinstance(payload, dict):
            continue
        archived_at = _parse_ts(payload.get("archived_at"))
        if archived_at is None:
            continue
        if opened <= archived_at <= closed:
            payload["_archive_path"] = str(path)
            entries.append(payload)
    return entries


def validate_closeout_evidence(
    state: dict[str, Any],
    archive_dir: Path | None,
    lifecycle: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    for task in state.get("tasks") or []:
        task_id = str(task.get("id") or "(unknown)")
        status = str(task.get("status") or "").strip()
        if status not in TERMINAL_ACTIVE_STATUSES:
            if _is_reconciled_non_dispatchable_gate(task):
                continue
            errors.append(f"task {task_id} remains {status or 'unknown'} before publish")
            continue
        errors.extend(_terminal_evidence_errors(task, task_id, task.get("terminal_outcome")))

    if archive_dir is not None:
        for entry in _archive_entries_in_wave(
            archive_dir,
            lifecycle.get("opened_at"),
            lifecycle.get("closed_at"),
        ):
            task = entry.get("task") if isinstance(entry.get("task"), dict) else {}
            task_id = str(entry.get("task_id") or task.get("id") or "(unknown)")
            terminal_status = str(entry.get("terminal_status") or task.get("status") or "").strip()
            if terminal_status != "done":
                errors.append(f"archived task {task_id} terminal status is {terminal_status or 'unknown'}")
                continue
            errors.extend(_terminal_evidence_errors(task, task_id, entry.get("terminal_outcome")))
    return errors


def build_release_discipline_report(
    state: dict[str, Any],
    config: dict[str, Any] | None = None,
    archive_dir: Path | None = None,
    target_wave_id: str | None = None,
    release_state: dict[str, Any] | None = None,
    now: datetime | None = None,
    existing_versions: list[str] | None = None,
) -> dict[str, Any]:
    settings = workflow_settings(config or {})
    mode = release_state_mode(release_state) or "legacy_wave"
    version_format = _release_state_version_format(settings, release_state)

    if mode == "per_task":
        known_versions = (
            existing_versions
            if existing_versions is not None
            else list_git_release_versions(
                release_prefix=settings["release_tag_prefix"],
                publish_prefix=settings["publish_branch_prefix"],
            )
        )
        version = resolve_publish_version(
            version_format,
            now=now,
            existing_versions=known_versions,
        )
        report: dict[str, Any] = {
            "ok": True,
            "release_state_mode": mode,
            "version_format": version_format,
            "version": version,
            "publish_branch": f"{settings['publish_branch_prefix']}{version}",
            "release_candidate_branch": f"{settings['release_candidate_prefix']}{version}",
            "release_tag": f"{settings['release_tag_prefix']}{version}",
            "checks": {
                "release_state": {
                    "ok": True,
                    "errors": [],
                    "mode": mode,
                    "source": "release-state + git release/publish refs",
                },
                "legacy_wave_state": {
                    "ok": True,
                    "errors": [],
                    "skipped": True,
                    "reason": "per-task release state supersedes ai-status.wave_state for publish cuts",
                },
                "task_board_closeout": {
                    "ok": True,
                    "errors": [],
                    "skipped": True,
                    "reason": "task closeout is enforced before dev merge by task PR checks",
                },
            },
            "errors": [],
        }
        return report

    wave_state = state.get("wave_state")
    if not isinstance(wave_state, dict):
        wave_state = {}
    if target_wave_id is None:
        target_wave_id = str(wave_state.get("current_wave_id") or "").strip()
    target = canonical_wave_id(target_wave_id or "")

    version = wave_to_publish_version(target)
    report: dict[str, Any] = {
        "ok": True,
        "release_state_mode": mode,
        "version_format": version_format,
        "wave_id": target,
        "version": version,
        "publish_branch": f"{settings['publish_branch_prefix']}{version}",
        "release_candidate_branch": f"{settings['release_candidate_prefix']}{version}",
        "release_tag": f"{settings['release_tag_prefix']}{version}",
        "checks": {},
        "errors": [],
    }

    sequence_errors = validate_wave_sequence(wave_state, target)
    report["checks"]["no_missing_wave"] = {"ok": not sequence_errors, "errors": sequence_errors}

    lifecycle_errors, lifecycle = validate_wave_closed_after_freeze(wave_state, target)
    report["checks"]["wave_frozen_then_closed"] = {
        "ok": not lifecycle_errors,
        "errors": lifecycle_errors,
        "lifecycle": lifecycle,
    }

    closeout_errors = validate_closeout_evidence(state, archive_dir, lifecycle)
    report["checks"]["closeout_evidence"] = {"ok": not closeout_errors, "errors": closeout_errors}

    for check in report["checks"].values():
        report["errors"].extend(check["errors"])
    report["ok"] = not report["errors"]
    return report


def print_human_report(report: dict[str, Any]) -> None:
    target = report.get("wave_id") or report.get("release_state_mode") or "release"
    if report["ok"]:
        print(
            "Release branch discipline passed: "
            f"{target} -> {report['publish_branch']} "
            f"({report['release_tag']})"
        )
        return
    print(
        "Release branch discipline failed: "
        f"{target} -> {report['publish_branch']}",
        file=sys.stderr,
    )
    for error in report["errors"]:
        print(f"- {error}", file=sys.stderr)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_args(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--status-file", type=Path, default=DEFAULT_STATUS_FILE)
        subparser.add_argument("--config-file", type=Path, default=DEFAULT_CONFIG_FILE)
        subparser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR)
        subparser.add_argument("--release-state-file", type=Path, default=DEFAULT_RELEASE_STATE_FILE)
        subparser.add_argument("--target-wave")
        subparser.add_argument("--json", action="store_true")

    check = subparsers.add_parser("check", help="validate release branch discipline")
    add_common_args(check)

    version = subparsers.add_parser("version", help="print the validated publish version")
    add_common_args(version)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    state = load_json_file(args.status_file, {})
    config = load_json_file(args.config_file, {})
    release_state = load_json_file(args.release_state_file, {})
    report = build_release_discipline_report(
        state=state,
        config=config,
        archive_dir=args.archive_dir,
        target_wave_id=args.target_wave,
        release_state=release_state,
    )

    if args.command == "version":
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        elif report["ok"]:
            print(report["version"])
        else:
            print_human_report(report)
        return 0 if report["ok"] else 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_human_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
