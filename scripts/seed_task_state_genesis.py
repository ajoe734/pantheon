#!/usr/bin/env python3
"""Seed an empty authoritative task-state journal from the committed projection.

Supervisor Authority V2 treats the V2 journal as the only task truth and
refuses to fall back to ``ai-status.json``: a store that silently reappeared
from its own projection would let stale or rolled-back task truth resurrect
itself.  That refusal is correct, and this command does not weaken it.

It exists for exactly one situation, which the previous host's loss produced:
the journal does not exist at all, so there is no truth to protect and the
control plane cannot start.  The projection committed in Git is then the only
surviving record of what the tasks were.  This writes it back as one explicit,
attributed genesis commit and refuses to run against a journal that already
holds events, so it can never overwrite live task truth.

Recovering the original journal is always preferable to running this.  Prefer
it whenever the old disk, snapshot, or suspended project can still be reached.
"""
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

from rewrite.task_state_store import (  # noqa: E402
    TaskStateStoreError,
    append_state_commit,
    load_snapshot,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-config",
        help="Live supervisor config to resolve the event log and status file from.",
    )
    parser.add_argument(
        "--event-log",
        default=os.environ.get("PANTHEON_TASK_STATE_EVENT_LOG"),
        help="V2 journal path. Overrides --live-config.",
    )
    parser.add_argument(
        "--status-file",
        help="Projection to seed from. Defaults to the config's status file.",
    )
    parser.add_argument(
        "--source",
        default="genesis-migration",
        help="Attribution recorded on the genesis event.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"{label} must be a regular non-symlink file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not payload:
        raise SystemExit(f"{label} must be a non-empty JSON object: {path}")
    return payload


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    event_log = args.event_log
    status_file = args.status_file
    if args.live_config:
        config = _load_json_object(Path(args.live_config).expanduser(), label="live config")
        store = config.get("task_state_store")
        if not isinstance(store, dict):
            raise SystemExit("live config does not define task_state_store")
        event_log = event_log or store.get("event_log")
        paths = config.get("paths")
        if status_file is None and isinstance(paths, dict):
            status_file = paths.get("status_file")
    if not event_log:
        raise SystemExit("no event log: pass --event-log or --live-config")
    if not status_file:
        raise SystemExit("no status file: pass --status-file or --live-config")
    return (
        Path(os.path.expanduser(str(event_log))),
        Path(os.path.expanduser(str(status_file))),
    )


def task_census(state: dict[str, Any]) -> dict[str, int]:
    tasks = state.get("tasks")
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    census: dict[str, int] = {}
    for task in tasks if isinstance(tasks, list) else []:
        if isinstance(task, dict):
            status = str(task.get("status") or "unknown")
            census[status] = census.get(status, 0) + 1
    return dict(sorted(census.items()))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    event_log, status_file = resolve_paths(args)

    try:
        snapshot = load_snapshot(event_log)
    except TaskStateStoreError as exc:
        raise SystemExit(f"cannot read journal: {exc}") from exc

    # The whole safety of this command rests on this check. A journal with any
    # event already carries task truth that a projection must never overwrite.
    if snapshot["event_count"]:
        raise SystemExit(
            f"journal already holds {snapshot['event_count']} event(s); "
            "refusing to seed over existing task truth"
        )

    state = _load_json_object(status_file, label="status projection")
    census = task_census(state)
    if not census:
        raise SystemExit(f"status projection contains no tasks: {status_file}")

    result: dict[str, Any] = {
        "event_log": str(event_log),
        "status_file": str(status_file),
        "task_census": census,
        "source": args.source,
        "outcome": "dry_run" if args.dry_run else "seeded",
    }

    if not args.dry_run:
        event = append_state_commit(event_log, state, source=args.source)
        result["event_id"] = event["event_id"]
        result["sequence"] = event["sequence"]
        result["state_sha256"] = event["state_sha256"]
        result["committed_at"] = event["committed_at"]

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"event log:  {result['event_log']}")
        print(f"projection: {result['status_file']}")
        print(f"tasks:      {census}")
        if args.dry_run:
            print("dry run: nothing written")
        else:
            print(f"seeded sequence {result['sequence']} as {result['source']}")
            print(f"state sha256:   {result['state_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
