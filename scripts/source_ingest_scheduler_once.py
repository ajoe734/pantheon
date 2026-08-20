#!/usr/bin/env python3
"""Run one bounded source-ingest controller tick with terminal readback."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from services.source_ingestion.controller_worker import (
    CONTROLLER_MODES,
    NON_TERMINAL_TRUTH_LEVEL,
    RECONCILE_AND_PULL_MODE,
    RECONCILE_ONLY_MODE,
    run_controller_once,
)


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run exactly one bounded source-ingest controller tick with terminal readback."
    )
    parser.add_argument(
        "connectors",
        nargs="*",
        help="Optional positional connector IDs to pull exclusively during this tick.",
    )
    parser.add_argument(
        "-c",
        "--connector",
        "--connectors",
        dest="flag_connectors",
        action="append",
        default=[],
        help="Connector ID(s) (comma-separated or repeated) for exclusive execution.",
    )
    parser.add_argument(
        "--force-connector",
        "--force-connectors",
        dest="force_connectors",
        action="append",
        default=[],
        help="Connector ID(s) (comma-separated or repeated) to force run alongside mutated connectors.",
    )
    parser.add_argument(
        "--mode",
        choices=sorted(CONTROLLER_MODES),
        default=os.getenv("SOURCE_INGEST_CONTROLLER_MODE") or RECONCILE_AND_PULL_MODE,
        help="Controller mode: reconcile_and_pull (default for manual pull) or reconcile_only.",
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("SOURCE_INGEST_API_URL", "http://127.0.0.1:8097"),
        help="Base URL of the source ingestion service.",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=int(os.getenv("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY", "2")),
        help="Maximum concurrent connector pulls (1..4).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("SOURCE_INGEST_CONTROLLER_TIMEOUT_SECONDS", "30.0")),
        help="HTTP request timeout in seconds.",
    )
    parser.add_argument(
        "--truth-level",
        default=os.getenv("SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL") or None,
        help="Loop controller truth level (scheduled_tick or reconciled_live_proof).",
    )
    parser.add_argument(
        "--state-path",
        default=os.getenv("SOURCE_INGEST_CONTROLLER_STATE_PATH") or None,
        help="Path to local controller state file.",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Explicit controller bearer token.",
    )
    parser.add_argument(
        "--token-file",
        default=os.getenv("SOURCE_INGEST_CONTROLLER_TOKEN_FILE") or None,
        help="Path to controller token file.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or "",
        help="PostgreSQL database URL for loop controller telemetry (optional).",
    )

    args = parser.parse_args(argv)

    # Collect exclusive connector IDs from positional args, flag args, and env
    exclusive_ids: list[str] = []
    for c in args.connectors:
        exclusive_ids.extend(_parse_csv(c))
    for c in args.flag_connectors:
        exclusive_ids.extend(_parse_csv(c))
    env_exclusive = os.getenv("SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS") or os.getenv("SOURCE_INGEST_CONNECTORS")
    if env_exclusive and not exclusive_ids:
        exclusive_ids.extend(_parse_csv(env_exclusive))

    # Collect force connector IDs from flags and env
    force_ids: list[str] = []
    for c in args.force_connectors:
        force_ids.extend(_parse_csv(c))
    env_force = os.getenv("SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS")
    if env_force and not force_ids:
        force_ids.extend(_parse_csv(env_force))

    controller_token = args.token
    if not controller_token and args.token_file:
        try:
            controller_token = Path(args.token_file).read_text(encoding="utf-8").strip() or None
        except OSError:
            pass

    try:
        result = run_controller_once(
            mode=args.mode,
            exclusive_connector_ids=exclusive_ids,
            force_connector_ids=force_ids,
            api_url=args.api_url,
            state_path=args.state_path,
            controller_token=controller_token,
            database_url=args.database_url,
            timeout_seconds=args.timeout,
            max_concurrency=args.max_concurrency,
            truth_level=args.truth_level,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        error_payload = {
            "status": "failed",
            "stage": getattr(exc, "stage", "controller"),
            "error": f"{type(exc).__name__}: {exc}",
        }
        print(json.dumps(error_payload, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
