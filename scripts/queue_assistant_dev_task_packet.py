#!/usr/bin/env python3
"""Queue a signed assistant DevTaskPacket for supervisor pickup."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError


CODE_ROOT = Path(__file__).resolve().parent.parent
STATUS_ROOT = Path(os.environ.get("PANTHEON_STATUS_ROOT") or CODE_ROOT).resolve()

# Import path resolution precedence: CODE_ROOT first so local script code is used,
# followed by STATUS_ROOT so central status packages/libraries can be resolved.
for root in (STATUS_ROOT, CODE_ROOT):
    bff_dir = root / "services" / "control-plane" / "bff"
    if bff_dir.exists() and str(bff_dir) not in sys.path:
        sys.path.insert(0, str(bff_dir))

from assistant.dev_bridge_inbox import queue_payload  # noqa: E402


def _read_payload(packet_file: str | None) -> Any:
    if packet_file and packet_file != "-":
        return json.loads(Path(packet_file).read_text(encoding="utf-8"))
    return json.loads(sys.stdin.read())


def _as_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _emit_json(stream: Any, payload: Mapping[str, Any]) -> None:
    stream.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify and queue a signed assistant DevTaskPacket for supervisor pickup."
    )
    parser.add_argument("--packet-file", help="Path to packet JSON. Use '-' or omit to read stdin.")
    parser.add_argument("--repo-root", help="Pantheon repo root that owns .orchestrator and ai-status.json.")
    parser.add_argument("--inbox-dir", help="Inbox directory. Defaults to .orchestrator/assistant-dev-packets.")
    parser.add_argument("--source", default="management_ai_frontend_handoff", help="Queue source label.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _as_mapping(_read_payload(args.packet_file), label="input")
        result = queue_payload(
            payload,
            repo_root=args.repo_root or str(STATUS_ROOT),
            inbox_dir=args.inbox_dir,
            source=args.source,
        )
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        _emit_json(sys.stderr, {"status": "error", "error": str(exc)})
        return 2

    _emit_json(sys.stdout, result)
    return 0 if result.get("queued") or result.get("status") in {"duplicate", "replay_rejected"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
