#!/usr/bin/env python3
"""Offline V2 migration: embed retired JSONL intents into runtime state."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / ".orchestrator"
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

from common import load_config
from runtime_state import migrate_legacy_event_queue_into_runtime_state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--legacy-event-queue", required=True, type=Path)
    parser.add_argument(
        "--offline-confirmed",
        action="store_true",
        help="required acknowledgement that supervisor and workers are stopped",
    )
    args = parser.parse_args()
    if not args.offline_confirmed:
        parser.error("--offline-confirmed is required")
    config = load_config(args.config)
    result = migrate_legacy_event_queue_into_runtime_state(
        config,
        legacy_event_queue_path=args.legacy_event_queue,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
