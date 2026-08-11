#!/usr/bin/env python3
"""Freeze a V1 task-state journal and initialize its V2 delta store in place."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / ".orchestrator"
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

from rewrite.task_state_store import TaskStateStoreError, migrate_legacy_journal


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event-log",
        default=os.environ.get("PANTHEON_TASK_STATE_EVENT_LOG"),
        help="Absolute current V1 event log path (or PANTHEON_TASK_STATE_EVENT_LOG).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and report without changing files.")
    parser.add_argument("--json", action="store_true", help="Emit a JSON result.")
    return parser.parse_args(argv)


def emit(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True, ensure_ascii=False))
        return
    status = payload.get("status", "error")
    print(f"task-state V2 migration: status={status} event_log={payload.get('event_log', '-')}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.event_log:
        emit({"ok": False, "error": "task-state event log path is not configured"}, as_json=args.json)
        return 3
    try:
        payload = migrate_legacy_journal(Path(args.event_log).expanduser(), dry_run=args.dry_run)
    except TaskStateStoreError as exc:
        emit({"ok": False, "error": str(exc), "error_kind": "integrity"}, as_json=args.json)
        return 2
    except OSError as exc:
        emit({"ok": False, "error": str(exc), "error_kind": "operational"}, as_json=args.json)
        return 3
    emit({"ok": True, **payload}, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
