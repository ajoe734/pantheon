#!/usr/bin/env python3
"""Offline verification for the authoritative Supervisor V2 task-state store."""
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
    load_snapshot,
    verify_full_chain,
    verify_snapshot,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event-log",
        default=os.environ.get("PANTHEON_TASK_STATE_EVENT_LOG"),
        help="Append-only journal path (or PANTHEON_TASK_STATE_EVENT_LOG).",
    )
    parser.add_argument(
        "--status-file",
        default=str(Path(os.environ.get("PANTHEON_STATUS_ROOT", ROOT)) / "ai-status.json"),
        help="Incumbent ai-status.json projection to compare.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--skip-full-chain",
        action="store_true",
        help="Skip offline validation of every V2 transition and frozen V1 archive.",
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
            snapshot = load_snapshot(Path(args.event_log).expanduser())
        report = verify_snapshot(snapshot, status)
        if not args.skip_full_chain:
            report["full_chain"] = verify_full_chain(Path(args.event_log).expanduser())
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
