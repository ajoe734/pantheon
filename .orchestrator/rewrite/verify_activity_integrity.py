"""Offline activity-log integrity verifier for Supervisor Authority V2.
§3.2, anti-pattern B).

The activity log carries a content-addressed cryptographic lineage that the
incumbent re-validates **every supervisor cycle, on the hot control path, with a
hard `raise` on any mismatch**. A single missing archive therefore crash-looped
the whole supervisor for ~4 hours with zero dispatch/finalize/archive — the
incident that motivated the rewrite.

The plan's fix: the integrity guarantee is real and worth keeping, but it belongs
**offline** — "a standalone verify_activity_integrity.py cron checks it offline
and alerts, never `raise`s in a cycle." This is that tool.

It reuses the incumbent's own validation (`common.stream_logical_activity`, which
runs the full ordering/identity/content/JSON/overlap + lineage checks and
withholds every row until they pass), so it is faithful by construction — but it
runs in a standalone process, catches the invariant error instead of propagating
it, and signals via **exit code** so a cron/alert can act:

    0  OK        — every logical row validated
    2  INTEGRITY — an audit invariant failed (corruption / missing archive / drift)
    3  OPERATION — could not run the check (missing config/log, unreadable input)

Usage:
    python3 -m rewrite.verify_activity_integrity --config /path/to/config.json
    python3 -m rewrite.verify_activity_integrity --config c.json --log /path/log.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# .orchestrator on the path so we can reuse the incumbent validator.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common  # noqa: E402

EXIT_OK = 0
EXIT_INTEGRITY = 2
EXIT_OPERATION = 3


def verify(log_path: Path, *, quiet: bool = False) -> int:
    """Validate the whole activity log offline. Never raises; returns an exit code."""
    if not log_path.exists():
        # A missing *primary* log is an operational fact, not corruption. (A
        # missing *archive* referenced by the lineage surfaces as an integrity
        # failure from the validator below, which is the case that used to crash
        # the cycle.)
        print(f"no activity log at {log_path}: nothing to verify")
        return EXIT_OPERATION

    rows = 0
    try:
        for _entry, _source, _line_number in common.stream_logical_activity(log_path):
            rows += 1
    except common.ActivityAuditInvariantError as exc:
        detail = getattr(exc, "evidence", None)
        print(f"INTEGRITY FAILURE validating {log_path}: {exc}")
        if detail and not quiet:
            print(f"  evidence: {json.dumps(detail, default=str)[:1000]}")
        return EXIT_INTEGRITY
    except (RuntimeError, OSError, ValueError) as exc:
        # Any other failure to *run* the check (unreadable file, malformed input
        # the validator rejects as non-canonical, …). Reported, never raised.
        print(f"could not verify {log_path}: {type(exc).__name__}: {exc}")
        return EXIT_OPERATION

    if not quiet:
        print(f"OK: {rows} logical activity rows validated in {log_path}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Path to a supervisor config JSON (resolves activity_log).")
    parser.add_argument("--log", help="Explicit activity-log path (overrides --config).")
    parser.add_argument("--quiet", action="store_true", help="Only print on failure.")
    args = parser.parse_args(argv)

    if args.log:
        log_path = Path(args.log)
    elif args.config:
        try:
            config = json.loads(Path(args.config).read_text())
            log_path = common.config_path(config, "activity_log")
        except Exception as exc:  # noqa: BLE001 - report, never raise
            print(f"could not resolve activity log from --config {args.config}: {exc}")
            return EXIT_OPERATION
    else:
        parser.error("one of --config or --log is required")
        return EXIT_OPERATION  # unreachable; parser.error exits

    return verify(log_path, quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
