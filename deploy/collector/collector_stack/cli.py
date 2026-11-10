"""CLI entrypoint for the third-location collector stack (SD §6)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .bundle import prune_expired_bundles, utc_iso
from .config import DEFAULT_POLICY_PATH, load_collector_config
from .privacy import AuthorHasher
from .runner import CollectorRunner


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="collector_stack",
        description="Third-location collector stack for Pantheon strategy discovery (SD §6)",
    )
    parser.add_argument(
        "--policy-file",
        default=str(DEFAULT_POLICY_PATH),
        help="Path to fetch-policy.json",
    )
    parser.add_argument(
        "--root-dir",
        default="/data",
        help="Collector data directory containing bundles/, state/, and library/",
    )
    parser.add_argument(
        "--salt-file",
        default=None,
        help="Path to host-local HMAC salt file (defaults to /run/secrets/author_hmac_salt)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate-config", help="Validate fetch-policy.json and startup safety rules")

    run_p = sub.add_parser("run", help="Run one collector source and write a bundle")
    run_p.add_argument("--source", required=True, help="Source ID from fetch-policy.json")
    run_p.add_argument("--run-id", default=None, help="Explicit run ID (defaults to UTC timestamp)")

    prune_p = sub.add_parser("prune", help="Prune bundles older than raw_retention_days (90 days)")
    prune_p.add_argument("--max-age-days", type=int, default=None)

    sub.add_parser("status", help="Print current collector status.json")

    args = parser.parse_args(argv)
    config = load_collector_config(args.policy_file)

    if args.command == "validate-config":
        print(
            json.dumps(
                {
                    "status": "valid",
                    "schema_version": config.schema_version,
                    "collector_version": config.collector_version,
                    "source_count": len(config.sources),
                    "enabled_sources": sorted(
                        sid for sid, p in config.sources.items() if p.enabled
                    ),
                    "disabled_sources": sorted(
                        sid for sid, p in config.sources.items() if not p.enabled
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.command == "prune":
        max_days = args.max_age_days or config.raw_retention_days
        removed = prune_expired_bundles(args.root_dir, max_age_days=max_days)
        print(
            json.dumps(
                {
                    "status": "pruned",
                    "max_age_days": max_days,
                    "removed_count": len(removed),
                    "removed_paths": [str(p) for p in removed],
                },
                indent=2,
            )
        )
        return 0

    if args.command == "status":
        status_path = Path(args.root_dir) / "status.json"
        if status_path.is_file():
            print(status_path.read_text(encoding="utf-8"))
        else:
            print(json.dumps({"updated_at": utc_iso(), "sources": {}}, indent=2))
        return 0

    if args.command == "run":
        hasher = AuthorHasher.from_file(args.salt_file)
        runner = CollectorRunner(
            config,
            root_dir=args.root_dir,
            hasher=hasher,
        )
        run_id = args.run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
        outcome = runner.run_source(args.source, run_id=run_id)
        print(
            json.dumps(
                {
                    "source_id": outcome.source_id,
                    "run_id": outcome.run_id,
                    "status": outcome.status,
                    "bundle_dir": str(outcome.bundle.bundle_dir),
                    "bundle_sha256": outcome.bundle.manifest["bundle_sha256"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
