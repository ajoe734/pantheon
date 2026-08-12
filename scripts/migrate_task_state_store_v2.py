#!/usr/bin/env python3
"""Migrate a V1 full-state journal to Supervisor Authority V2.

The legacy journal is read and hash-validated once, offline.  It is not copied
or rewritten: a signed-by-content archive anchor records its immutable path,
size, digest, event tip, and final state.  The V2 journal then starts with one
genesis delta bound to that anchor.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / ".orchestrator"
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

from rewrite.task_state_store import (
    ARCHIVE_ANCHOR_TYPE,
    ARCHIVE_ANCHOR_VERSION,
    TaskStateStoreError,
    append_state_commit,
    archive_anchor_path,
    sha256_json,
    snapshot_transaction,
    utc_now,
    validate_archive_anchor,
    write_archive_anchor,
)


LEGACY_EVENT_VERSION = 1
LEGACY_EVENT_TYPE = "task_state_committed"


def normalize_legacy_state_for_v2(state: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Apply the one-time lifecycle schema cutover.

    V1 used ``quarantined`` as both a provider-health outcome and a task
    lifecycle state.  V2 keeps provider health in the account ledger and maps
    persisted task quarantine to an explicit blocked task.  This conversion is
    recorded by the anchored genesis event; no runtime compatibility branch is
    retained.
    """

    normalized = copy.deepcopy(state)
    converted = 0
    tasks = normalized.get("tasks")
    if not isinstance(tasks, list):
        return normalized, converted
    for task in tasks:
        if not isinstance(task, dict):
            continue
        raw_generation = task.get("generation")
        if raw_generation is None:
            task["generation"] = 1
        elif (
            isinstance(raw_generation, bool)
            or not isinstance(raw_generation, int)
            or raw_generation < 1
        ):
            raise TaskStateStoreError(
                f"legacy task {task.get('id') or '(unknown)'} has invalid generation"
            )
        if str(task.get("status") or "").lower() != "quarantined":
            continue
        task["status"] = "blocked"
        task["resume_status"] = "in_progress"
        task["block_reason"] = {
            "kind": "legacy_task_quarantine",
            "required_action": "operator_reopen_after_diagnosis",
        }
        converted += 1
    return normalized, converted


def _legacy_digest_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in event.items()
        if key not in {"event_id", "event_sha256"}
    }


def _validate_legacy_event(
    event: Any,
    *,
    expected_sequence: int,
    previous_sha256: str | None,
) -> dict[str, Any]:
    required = {
        "version", "type", "event_id", "event_sha256", "sequence",
        "committed_at", "source", "previous_event_sha256", "state_sha256",
        "state",
    }
    if not isinstance(event, dict) or set(event) != required:
        raise TaskStateStoreError(
            f"legacy task-state event {expected_sequence} schema mismatch"
        )
    if event.get("version") != LEGACY_EVENT_VERSION or event.get("type") != LEGACY_EVENT_TYPE:
        raise TaskStateStoreError(
            f"legacy task-state event {expected_sequence} version/type mismatch"
        )
    if event.get("sequence") != expected_sequence:
        raise TaskStateStoreError(
            f"legacy task-state sequence mismatch at event {expected_sequence}"
        )
    if event.get("previous_event_sha256") != previous_sha256:
        raise TaskStateStoreError(
            f"legacy task-state previous hash mismatch at event {expected_sequence}"
        )
    state = event.get("state")
    if not isinstance(state, dict) or event.get("state_sha256") != sha256_json(state):
        raise TaskStateStoreError(
            f"legacy task-state state digest mismatch at event {expected_sequence}"
        )
    event_sha256 = sha256_json(_legacy_digest_payload(event))
    if event.get("event_sha256") != event_sha256:
        raise TaskStateStoreError(
            f"legacy task-state event digest mismatch at event {expected_sequence}"
        )
    if event.get("event_id") != f"task-state-{event_sha256}":
        raise TaskStateStoreError(
            f"legacy task-state event id mismatch at event {expected_sequence}"
        )
    return event


def audit_legacy_journal(path: str | Path) -> dict[str, Any]:
    legacy_path = Path(path).expanduser().absolute()
    if legacy_path.is_symlink():
        raise TaskStateStoreError("legacy task-state journal must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(legacy_path, flags)
    digest = hashlib.sha256()
    event_count = 0
    previous_sha256: str | None = None
    last_event: dict[str, Any] | None = None
    byte_size = 0
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskStateStoreError("legacy task-state journal must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                digest.update(raw_line)
                byte_size += len(raw_line)
                if not raw_line.strip():
                    continue
                if not raw_line.endswith(b"\n"):
                    raise TaskStateStoreError(
                        "legacy task-state journal ends with an unterminated event"
                    )
                try:
                    event = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise TaskStateStoreError(
                        f"invalid legacy task-state event at line {line_number}: {exc}"
                    ) from exc
                last_event = _validate_legacy_event(
                    event,
                    expected_sequence=event_count + 1,
                    previous_sha256=previous_sha256,
                )
                previous_sha256 = str(last_event["event_sha256"])
                event_count += 1
    finally:
        os.close(descriptor)
    if last_event is None:
        raise TaskStateStoreError("legacy task-state journal is empty")
    return {
        "archived_path": str(legacy_path),
        "byte_size": byte_size,
        "journal_sha256": digest.hexdigest(),
        "event_count": event_count,
        "last_event_id": last_event["event_id"],
        "last_event_sha256": last_event["event_sha256"],
        "state": last_event["state"],
        "state_sha256": last_event["state_sha256"],
    }


def migrate(
    *,
    legacy_event_log: str | Path,
    event_log: str | Path,
    expected_state: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    source = Path(legacy_event_log).expanduser().absolute()
    target = Path(event_log).expanduser().absolute()
    if source == target:
        raise TaskStateStoreError("V1 archive and V2 event log must use different paths")
    head = target.with_name(f"{target.name}.head.json")

    legacy = audit_legacy_journal(source)
    if expected_state is not None and sha256_json(expected_state) != legacy["state_sha256"]:
        raise TaskStateStoreError(
            "legacy journal tip does not match the expected status projection"
        )
    migrated_state, converted_quarantined_tasks = normalize_legacy_state_for_v2(
        legacy["state"]
    )
    migrated_state_sha256 = sha256_json(migrated_state)
    anchor_created_at = created_at or utc_now()
    existing_anchor_path = archive_anchor_path(target)
    if created_at is None and existing_anchor_path.exists():
        try:
            existing_anchor = validate_archive_anchor(
                json.loads(existing_anchor_path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskStateStoreError(f"invalid existing legacy archive anchor: {exc}") from exc
        anchor_created_at = str(existing_anchor["created_at"])
    anchor_body = {
        "version": ARCHIVE_ANCHOR_VERSION,
        "type": ARCHIVE_ANCHOR_TYPE,
        "archived_path": legacy["archived_path"],
        "byte_size": legacy["byte_size"],
        "journal_sha256": legacy["journal_sha256"],
        "event_count": legacy["event_count"],
        "last_event_id": legacy["last_event_id"],
        "last_event_sha256": legacy["last_event_sha256"],
        "state_sha256": legacy["state_sha256"],
        "created_at": anchor_created_at,
    }
    expected_anchor_sha256 = sha256_json(anchor_body)
    if (target.exists() and target.stat().st_size) or head.exists():
        with snapshot_transaction(target) as transaction:
            existing = transaction.load_snapshot()
        if (
            existing["event_count"] != 1
            or existing["state_sha256"] != migrated_state_sha256
            or existing["archive_anchor_sha256"] != expected_anchor_sha256
        ):
            raise TaskStateStoreError(
                "V2 event log must be empty before migration or match the exact "
                "anchored genesis from an interrupted migration"
            )
        return {
            "ok": True,
            "already_migrated": True,
            "legacy_event_count": legacy["event_count"],
            "legacy_journal_sha256": legacy["journal_sha256"],
            "legacy_state_sha256": legacy["state_sha256"],
            "v2_state_sha256": migrated_state_sha256,
            "converted_quarantined_tasks": converted_quarantined_tasks,
            "archive_anchor_sha256": expected_anchor_sha256,
            "v2_event_log": str(target),
            "v2_genesis_event_id": existing["last_event_id"],
        }
    anchor = write_archive_anchor(
        target,
        anchor_body,
    )
    genesis = append_state_commit(
        target,
        migrated_state,
        source="task-state-v2-migration",
        committed_at=created_at,
    )
    if genesis["archive_anchor_sha256"] != anchor["anchor_sha256"]:
        raise TaskStateStoreError("V2 genesis is not bound to the legacy archive anchor")
    return {
        "ok": True,
        "already_migrated": False,
        "legacy_event_count": legacy["event_count"],
        "legacy_journal_sha256": legacy["journal_sha256"],
        "legacy_state_sha256": legacy["state_sha256"],
        "v2_state_sha256": migrated_state_sha256,
        "converted_quarantined_tasks": converted_quarantined_tasks,
        "archive_anchor_sha256": anchor["anchor_sha256"],
        "v2_event_log": str(target),
        "v2_genesis_event_id": genesis["event_id"],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-event-log", required=True)
    parser.add_argument("--event-log", required=True)
    parser.add_argument(
        "--status-file",
        help="Optional ai-status.json that must match the validated V1 journal tip.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        expected_state = None
        if args.status_file:
            expected_state = json.loads(
                Path(args.status_file).expanduser().read_text(encoding="utf-8")
            )
            if not isinstance(expected_state, dict):
                raise ValueError("status projection must be a JSON object")
        report = migrate(
            legacy_event_log=args.legacy_event_log,
            event_log=args.event_log,
            expected_state=expected_state,
        )
    except (TaskStateStoreError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        report = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"task-state V2 migration failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "task-state V2 migration complete: "
            f"legacy_events={report['legacy_event_count']} "
            f"anchor={report['archive_anchor_sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
