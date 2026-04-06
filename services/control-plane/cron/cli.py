#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from models import OpenClawRuntimePin
from service import CronOrchestrator
from workflows import WORKFLOW_CATALOG


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pantheon governed cron workflow runner")
    parser.add_argument("--workflow", required=True, choices=sorted(WORKFLOW_CATALOG))
    parser.add_argument("--payload-file", required=True, help="JSON payload for the selected workflow")
    parser.add_argument("--dispatch", action="store_true", help="Use transport if configured instead of dry-run mode")
    parser.add_argument("--release-tag", default="v0.0.0-local")
    parser.add_argument("--commit-sha", default="local-dev-sha")
    parser.add_argument("--image-ref", default="ghcr.io/openclaw/openclaw:v0.0.0-local")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    orchestrator = CronOrchestrator(
        runtime_pin=OpenClawRuntimePin(
            release_tag=args.release_tag,
            commit_sha=args.commit_sha,
            image_ref=args.image_ref,
        )
    )
    result = orchestrator.run(args.workflow, payload, dry_run=not args.dispatch)
    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
