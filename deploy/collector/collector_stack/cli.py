"""CLI entrypoint for the third-location collector stack (SD §6)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .backfill import (
    BACKFILL_SOURCES,
    MONTHLY_SOURCE,
    TW_EXCHANGE_HOLIDAYS,
    estimate,
    load_extra_holidays,
    resolve_dates,
    resolve_months,
    run_backfill,
    two_year_range,
)
from .bundle import prune_expired_bundles, utc_iso
from .config import DEFAULT_POLICY_PATH, load_collector_config
from .privacy import AuthorHasher
from .runner import CollectorRunner


def _add_range_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start-month", default=None, help="First TAIEX month YYYY-MM (2012-01 or later)")
    p.add_argument("--end-month", default=None, help="Last TAIEX month YYYY-MM (defaults to current UTC month)")
    p.add_argument("--start-date", default=None, help="First trade date YYYY-MM-DD")
    p.add_argument("--end-date", default=None, help="Last trade date (default: newest published trading day)")
    p.add_argument("--trading-days", type=int, default=None, help="Last N trading days instead of a start date")


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

    _add_range_args(run_p)
    bf = sub.add_parser(
        "backfill",
        help="Resumable exchange backfill (one request per day or TAIEX month, >= 5s apart)",
    )
    bf.add_argument("--source", required=True, help="TWSE/TPEx history, index/breadth, TAIEX or T86 source ID")
    bf.add_argument("--two-years", action="store_true", help="Backfill the last two years")
    _add_range_args(bf)
    bf.add_argument("--holidays-file", default=None, help="JSON list of extra closure dates (YYYY-MM-DD)")
    bf.add_argument("--dry-run", action="store_true", help="Print request count and wall-clock only")

    sub.add_parser(
        "capture-close",
        help="Capture today's TWSE+TPEx all-stock close (14:40-17:00 Taipei window; timer retries)",
    )

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

    if args.command == "capture-close":
        from .runner import capture_same_day_close

        runner = CollectorRunner(config, root_dir=args.root_dir, hasher=AuthorHasher.from_file(args.salt_file))
        result = capture_same_day_close(runner, now=datetime.now(timezone.utc))
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 2 if result.retry_needed else 0

    if args.command == "backfill":
        now_iso = utc_iso()
        holidays = TW_EXCHANGE_HOLIDAYS | load_extra_holidays(args.holidays_file)
        try:
            if args.source == MONTHLY_SOURCE:
                if args.two_years or args.start_date or args.end_date or args.trading_days is not None:
                    raise ValueError("TAIEX monthly backfill uses --start-month / --end-month only")
                if not args.start_month:
                    raise ValueError("TAIEX monthly backfill requires --start-month")
                dates = resolve_months(args.start_month, args.end_month or now_iso[:7])
            elif args.start_month or args.end_month:
                raise ValueError("month options require the TAIEX history source")
            elif args.two_years:
                start, end = two_year_range(now_iso)
                dates = resolve_dates(start_date=start, end_date=args.end_date or end,
                                      trading_days=None, now_iso=now_iso, holidays=holidays)
            else:
                dates = resolve_dates(start_date=args.start_date, end_date=args.end_date,
                                      trading_days=args.trading_days, now_iso=now_iso, holidays=holidays)
        except ValueError as exc:
            parser.error(str(exc))
        if args.source not in BACKFILL_SOURCES:
            parser.error(f"backfill supports only {sorted(BACKFILL_SOURCES)}")
        policy = config.get_source(args.source)
        plan = {"source_id": args.source, "first_date": dates[0] if dates else None,
                "last_date": dates[-1] if dates else None,
                **estimate(dates, min_interval_seconds=policy.min_interval_seconds)}
        if args.dry_run:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        runner = CollectorRunner(config, root_dir=args.root_dir, hasher=AuthorHasher.from_file(args.salt_file))
        result = run_backfill(runner, args.source, dates)
        print(json.dumps({"plan": plan, "result": result.to_dict()}, indent=2, sort_keys=True))
        return 0 if result.complete else 2

    if args.command == "run":
        hasher = AuthorHasher.from_file(args.salt_file)
        runner = CollectorRunner(
            config,
            root_dir=args.root_dir,
            hasher=hasher,
        )
        run_id = args.run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
        collect_kwargs = None
        if args.start_date or args.end_date or args.trading_days or args.start_month or args.end_month:
            if args.source not in BACKFILL_SOURCES:
                parser.error(f"date range options are only supported for {sorted(BACKFILL_SOURCES)}")
            try:
                if args.source == MONTHLY_SOURCE:
                    if args.start_date or args.end_date or args.trading_days is not None or not args.start_month:
                        raise ValueError("TAIEX ranges require --start-month / --end-month only")
                    run_dates = resolve_months(args.start_month, args.end_month or utc_iso()[:7])
                elif args.start_month or args.end_month:
                    raise ValueError("month options require the TAIEX history source")
                else:
                    run_dates = resolve_dates(
                        start_date=args.start_date, end_date=args.end_date,
                        trading_days=args.trading_days, now_iso=utc_iso(),
                    )
            except ValueError as exc:
                parser.error(str(exc))
            cap = config.get_source(args.source).max_requests_per_run
            if len(run_dates) > cap:
                parser.error(
                    f"{len(run_dates)} trading dates exceed max_requests_per_run={cap} for a single run; "
                    "no request was made. Use the resumable `backfill` command (per-date bundles, "
                    "ledger-based resume) for large ranges."
                )
            collect_kwargs = {"dates": run_dates}
        outcome = runner.run_source(args.source, run_id=run_id, collect_kwargs=collect_kwargs)
        print(
            json.dumps(
                {
                    "source_id": outcome.source_id,
                    "run_id": outcome.run_id,
                    "status": outcome.status,
                    "skipped_items": outcome.skipped_count,
                    "reason": outcome.bundle.manifest.get("reason"),
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
