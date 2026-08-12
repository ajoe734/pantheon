#!/usr/bin/env python3
"""Verify Supervisor Authority V2 head parity or run an explicit offline audit."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / ".orchestrator"
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

from common import canonical_task_state_lock_file
from rewrite.task_state_store import (
    TaskStateStoreError,
    audit_full_journal,
    load_snapshot,
    verify_archive_anchor,
    verify_snapshot,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event-log",
        default=os.environ.get("PANTHEON_TASK_STATE_EVENT_LOG"),
        help="V2 transition-delta journal path (or PANTHEON_TASK_STATE_EVENT_LOG).",
    )
    parser.add_argument(
        "--status-file",
        default=str(Path(os.environ.get("PANTHEON_STATUS_ROOT", ROOT)) / "ai-status.json"),
        help="Incumbent ai-status.json projection to compare.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--full-replay",
        action="store_true",
        help="Offline-only: replay and hash every V2 delta event.",
    )
    parser.add_argument(
        "--verify-archive",
        action="store_true",
        help="Offline-only: additionally hash the immutable legacy V1 archive.",
    )
    return parser.parse_args(argv)


def emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        return
    print(
        "task-state shadow verification: "
        f"ok={payload.get('ok')} events={payload.get('event_count', 0)} "
        f"nonterminal_tasks={payload.get('nonterminal_task_count', '-')} "
        f"tail_events={payload.get('replayed_tail_events', '-')} "
        f"error={payload.get('error') or '-'}"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.event_log:
        payload = {"ok": False, "error": "task-state event log path is not configured"}
        emit(payload, as_json=args.json)
        return 3
    try:
        status_path = Path(args.status_file).expanduser()
        # The board and the journal must be sampled from one lock domain. Read
        # separately, a commit landing between them reports the event count of
        # the newer generation against the digest of the older one -- the
        # transient "expected SHA from event N-1, projected SHA from event N"
        # failure that a stable rerun then contradicts.
        with canonical_task_state_lock_file(status_path, shared=True):
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if not isinstance(status, dict):
                raise ValueError("status projection must be a JSON object")
            # A regular verification reads only the atomic V2 head and any tail
            # after its recorded offset.  It never hashes the journal prefix.
            snapshot = load_snapshot(
                Path(args.event_log).expanduser(),
                refresh_checkpoint=False,
            )
        report = verify_snapshot(snapshot, status)
        if args.full_replay:
            audit = audit_full_journal(Path(args.event_log).expanduser())
            report["full_audit"] = {
                key: value for key, value in audit.items() if key != "state"
            }
            report["ok"] = bool(report["ok"] and audit["state_sha256"] == snapshot["state_sha256"])
        if args.verify_archive:
            archive = verify_archive_anchor(Path(args.event_log).expanduser())
            report["archive_audit"] = archive
            report["ok"] = bool(report["ok"] and archive["ok"])
    except TaskStateStoreError as exc:
        payload = {"ok": False, "error": str(exc), "error_kind": "integrity"}
        emit(payload, as_json=args.json)
        return 2
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc), "error_kind": "operational"}
        emit(payload, as_json=args.json)
        return 3
    emit(report, as_json=args.json)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
