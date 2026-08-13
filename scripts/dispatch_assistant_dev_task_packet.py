#!/usr/bin/env python3
"""Dispatch a signed assistant dev task packet through repo-local automation.

This command is the trusted local bridge between a local signed task packet and
the existing supervisor/auto-worker queue. It never calls a web route and never
bypasses the packet verifier.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError


REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLING_DIR = REPO_ROOT / ".orchestrator"
if str(TOOLING_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLING_DIR))

from development_bridge.dev_bridge_dispatcher import dispatch_task_packet  # noqa: E402
from development_bridge.dev_bridge_models import BridgeDispatchRequest, DevTaskPacket  # noqa: E402


def _read_payload(packet_file: str | None) -> Any:
    if packet_file and packet_file != "-":
        return json.loads(Path(packet_file).read_text(encoding="utf-8"))
    return json.loads(sys.stdin.read())


def _as_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _extract_packet_payload(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Return a local packet payload and optional dispatch defaults."""
    if isinstance(payload.get("packet"), Mapping):
        return payload["packet"], payload

    if isinstance(payload.get("taskPacket"), Mapping):
        return payload["taskPacket"], payload

    if "packetId" in payload or "packet_id" in payload:
        return payload, {}

    raise ValueError(
        "Could not find DevTaskPacket payload. Expected a raw packet, {packet}, "
        "or {taskPacket}."
    )


def _build_request(args: argparse.Namespace) -> BridgeDispatchRequest:
    payload = _as_mapping(_read_payload(args.packet_file), label="input")
    packet_payload, defaults = _extract_packet_payload(payload)
    repo_root = args.repo_root or defaults.get("repoRoot") or defaults.get("repo_root")
    dry_run = args.dry_run or bool(defaults.get("dryRun") or defaults.get("dry_run"))

    return BridgeDispatchRequest(
        packet=DevTaskPacket(**packet_payload),
        repoRoot=str(Path(repo_root).resolve()) if repo_root else str(REPO_ROOT),
        dryRun=dry_run,
    )


def _emit_json(stream: Any, payload: Mapping[str, Any]) -> None:
    stream.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify and materialize a signed assistant DevTaskPacket through "
            "one atomic scripts/ai_status.py dev-bridge materialization batch."
        )
    )
    parser.add_argument(
        "--packet-file",
        help="Path to a packet JSON file. Use '-' or omit to read stdin.",
    )
    parser.add_argument(
        "--repo-root",
        help="Pantheon repo root that owns ai-status.json and scripts/ai_status.py.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify the packet and report task records without assigning tasks.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        request = _build_request(args)
        result = dispatch_task_packet(request)
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        _emit_json(
            sys.stderr,
            {
                "status": "error",
                "error": str(exc),
            },
        )
        return 2

    body = result.model_dump(mode="json", by_alias=True)
    _emit_json(sys.stdout, body)
    if result.replay_rejected or result.errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
