#!/usr/bin/env python3
"""Drain queued assistant DevTaskPackets through the verified dispatcher."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parent.parent
BFF_DIR = REPO_ROOT / "services" / "control-plane" / "bff"
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

from assistant.dev_bridge_inbox import drain_task_packet_inbox  # noqa: E402


def _emit_json(stream: Any, payload: Mapping[str, Any]) -> None:
    stream.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drain assistant DevTaskPacket inbox through scripts/ai_status.py assign."
    )
    parser.add_argument("--repo-root", help="Pantheon repo root that owns .orchestrator and ai-status.json.")
    parser.add_argument("--inbox-dir", help="Inbox directory. Defaults to .orchestrator/assistant-dev-packets.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum pending packets to drain.")
    parser.add_argument("--dry-run", action="store_true", help="Verify and preview without moving or dispatching packets.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = drain_task_packet_inbox(
            repo_root=args.repo_root or str(REPO_ROOT),
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
