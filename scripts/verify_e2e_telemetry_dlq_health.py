#!/usr/bin/env python3
"""E2E telemetry ingest / DLQ health verifier.

Business flow under test: paper fill → telemetry ingest (validated against the
RuntimeBinding store) → accepted, or → rejected to the dead-letter queue (DLQ).

Two properties:
  1. Ingest validation: events whose binding_id is not in the RuntimeBinding store
     are rejected (Evidence contract E-1) — proven live (an unknown-binding event
     returns HTTP 400 "rejected"). This verifier asserts the DLQ *health* derived
     from the ingest stats.
  2. DLQ health: a DLQ pinned at/over the incident threshold by *unreplayable*
     entries (binding-mismatch rejections, which replay_dlq never re-enqueues)
     is a stuck-DLQ condition — it keeps an incident alert latched on events that
     can never drain.

This script evaluates a DLQ stats object (fetched from the telemetry service or
piped in as JSON) and FAILs when the DLQ is pinned at/over the incident threshold
with unreplayable entries.

Usage:
    # evaluate a DLQ stats JSON ({"count":N, "threshold":T, "entries":[...]})
    cat dlq.json | python3 scripts/verify_e2e_telemetry_dlq_health.py --threshold 100
"""
from __future__ import annotations

import argparse
import json
import sys

# A DLQ entry whose reason mentions a binding-store / evidence-contract violation
# is a binding-mismatch rejection — replay_dlq() does NOT re-enqueue these, so they
# never drain on their own.
_UNREPLAYABLE_MARKERS = ("not found in runtimebinding store", "evidence contract", "binding_mismatch")


def _entry_reason(entry) -> str:
    if not isinstance(entry, dict):
        return ""
    parts = [entry.get("reason"), entry.get("rejection_reason")]
    tags = entry.get("tags")
    if isinstance(tags, (list, tuple)):
        parts.extend(str(t) for t in tags)
    elif tags:
        parts.append(str(tags))
    return " ".join(str(p) for p in parts if p).lower()


def _is_unreplayable(entry) -> bool:
    reason = _entry_reason(entry)
    return any(m in reason for m in _UNREPLAYABLE_MARKERS)


def evaluate(stats: dict, threshold: int) -> tuple[bool, dict]:
    count = int(stats.get("count") or 0)
    entries = stats.get("entries") or []
    unreplayable = sum(1 for e in entries if _is_unreplayable(e))
    pinned = count >= threshold
    # healthy unless the DLQ is at/over the incident threshold AND a meaningful
    # share of it is unreplayable (so it cannot drain by itself).
    stuck = pinned and unreplayable > 0
    return (not stuck), {
        "count": count,
        "threshold": threshold,
        "unreplayable": unreplayable,
        "pinned_at_threshold": pinned,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--threshold", type=int, default=100, help="DLQ incident threshold")
    ap.add_argument("--stats-file", default="-", help="DLQ stats JSON path ('-' = stdin)")
    args = ap.parse_args()

    raw = sys.stdin.read() if args.stats_file == "-" else open(args.stats_file).read()
    try:
        stats = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: bad DLQ stats JSON: {exc}", file=sys.stderr)
        return 2

    ok, summary = evaluate(stats, args.threshold)
    print(f"== telemetry DLQ health ==")
    print(f"  count={summary['count']} threshold={summary['threshold']} "
          f"unreplayable={summary['unreplayable']} pinned={summary['pinned_at_threshold']}")
    if not ok:
        print(f"\nFAIL: DLQ is pinned at/over the incident threshold with "
              f"{summary['unreplayable']} unreplayable (binding-mismatch) entries that "
              f"cannot drain — the incident alert is latched on permanently-stuck events.")
        return 1
    print("\nOK: telemetry DLQ is below the incident threshold or has no stuck entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
