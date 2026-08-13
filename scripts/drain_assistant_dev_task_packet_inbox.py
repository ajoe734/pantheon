#!/usr/bin/env python3
"""Drain queued assistant DevTaskPackets through the verified dispatcher."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping


CODE_ROOT = Path(__file__).resolve().parent.parent
STATUS_ROOT = Path(os.environ.get("PANTHEON_STATUS_ROOT") or CODE_ROOT).resolve()

# The bridge belongs to local development tooling, not the product BFF.  Load
# its package from each repository root so a provisioned status root remains
# self-contained.
for root in (STATUS_ROOT, CODE_ROOT):
    tooling_dir = root / ".orchestrator"
    if tooling_dir.exists() and str(tooling_dir) not in sys.path:
        sys.path.insert(0, str(tooling_dir))

from development_bridge.dev_bridge_inbox import (  # noqa: E402
    drain_task_packet_inbox,
    recover_failed_task_packet,
)


def _emit_json(stream: Any, payload: Mapping[str, Any]) -> None:
    stream.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Drain assistant DevTaskPacket inbox through the installed "
            "governed status runtime."
        )
    )
    parser.add_argument("--repo-root", help="Pantheon repo root that owns .orchestrator and ai-status.json.")
    parser.add_argument("--inbox-dir", help="Inbox directory. Defaults to .orchestrator/assistant-dev-packets.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum pending packets to drain.")
    parser.add_argument("--dry-run", action="store_true", help="Verify and preview without moving or dispatching packets.")
    parser.add_argument(
        "--recover-failed-packet-id",
        help=(
            "Lock, verify, and recover or rearm one exact signed packet id "
            "from failed storage. This action does not drain the packet."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.recover_failed_packet_id:
            if args.dry_run or args.limit is not None:
                raise ValueError(
                    "--recover-failed-packet-id cannot be combined with "
                    "--dry-run or --limit"
                )
            result = recover_failed_task_packet(
                args.recover_failed_packet_id,
                repo_root=args.repo_root or str(STATUS_ROOT),
                inbox_dir=args.inbox_dir,
                source="drain_cli_exact_failed_recovery",
            )
        else:
            result = drain_task_packet_inbox(
                repo_root=args.repo_root or str(STATUS_ROOT),
                inbox_dir=args.inbox_dir,
                limit=args.limit,
                dry_run=args.dry_run,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _emit_json(sys.stderr, {"status": "error", "error": str(exc)})
        return 2

    _emit_json(sys.stdout, result)
    return 1 if result.get("errorCount") else 0


if __name__ == "__main__":
    raise SystemExit(main())
