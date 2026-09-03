#!/usr/bin/env python3
"""Seed a genuinely empty authoritative V2 task-state journal.

Supervisor Authority V2 treats the V2 journal as the only task truth and
refuses to fall back to ``ai-status.json``: a store that silently resurrected
task rows from its own projection could bring back stale or rolled-back task
truth. That refusal is correct and this command does not weaken it.

It exists for exactly one situation: the journal does not exist at all (its
host was lost), so there is no truth to protect and the control plane cannot
start. It writes one explicit, attributed genesis event whose state is empty
-- never a copy of ``ai-status.json``, an archived projection, or anything
read from a product API -- and refuses outright against a journal that
already holds any event, partial content, or content it cannot parse.

Recovering the original journal is always preferable to running this. Prefer
it whenever the old disk, snapshot, or suspended project can still be
reached. This command cannot restore task history; it only lets a rebuilt
host start a fresh journal instead of staying permanently blocked.
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


# The genesis state is intentionally empty. Populating it from ai-status.json,
# an archived projection, or any other derived source would resurrect exactly
# the stale task truth the V2 journal's fallback refusal exists to prevent.
GENESIS_STATE: dict[str, Any] = {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-config",
        help="Live supervisor config to resolve the event log path from.",
    )
    parser.add_argument(
        "--event-log",
        default=os.environ.get("PANTHEON_TASK_STATE_EVENT_LOG"),
        help="V2 journal path. Overrides --live-config.",
    )
    parser.add_argument(
        "--source",
        required=True,
        help=(
            "Required attribution recorded on the genesis event, e.g. "
            "'operator-<name>-<host>-rebuild-<date>'."
        ),
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


def resolve_event_log(args: argparse.Namespace) -> Path:
    event_log = args.event_log
    if args.live_config:
        config = _load_json_object(Path(args.live_config).expanduser(), label="live config")
        store = config.get("task_state_store")
        if not isinstance(store, dict):
            raise SystemExit("live config does not define task_state_store")
        event_log = event_log or store.get("event_log")
    if not event_log:
        raise SystemExit("no event log: pass --event-log or --live-config")
    return Path(os.path.expanduser(str(event_log)))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    event_log = resolve_event_log(args)
    if not args.source.strip():
        raise SystemExit("--source must not be empty")

    try:
        snapshot = load_snapshot(event_log)
    except TaskStateStoreError as exc:
        raise SystemExit(
            f"cannot read journal, refusing genesis on ambiguous content: {exc}"
        ) from exc

    # The whole safety of this command rests on this check. A journal with any
    # event already carries task truth that genesis must never overwrite.
    if snapshot["event_count"]:
        raise SystemExit(
            f"journal already holds {snapshot['event_count']} event(s); "
            "refusing to seed over existing task truth"
        )
    # A trailing byte range that could not be parsed as a complete event is
    # ambiguous content, not an empty journal: it may be a torn write from a
    # crashed append. Refuse rather than silently treating it as empty.
    if snapshot.get("ignored_partial_tail_bytes"):
        raise SystemExit(
            "journal has "
            f"{snapshot['ignored_partial_tail_bytes']} unparsed trailing byte(s); "
            "refusing genesis on ambiguous content; run offline recovery/audit first"
        )

    result: dict[str, Any] = {
        "event_log": str(event_log),
        "state": GENESIS_STATE,
        "source": args.source,
        "outcome": "dry_run" if args.dry_run else "seeded",
    }

    if not args.dry_run:
        event = append_state_commit(event_log, GENESIS_STATE, source=args.source)
        result["event_id"] = event["event_id"]
        result["sequence"] = event["sequence"]
        result["state_sha256"] = event["state_sha256"]
        result["committed_at"] = event["committed_at"]

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"event log: {result['event_log']}")
        print(f"state:     {result['state']} (empty genesis; no projection imported)")
        if args.dry_run:
            print("dry run: nothing written")
        else:
            print(f"seeded sequence {result['sequence']} as {result['source']}")
            print(f"state sha256:   {result['state_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
