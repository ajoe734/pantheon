#!/usr/bin/env python3
"""E2E telemetry pipeline-health verifier (backpressure / buffer / errors).

The telemetry ingest pipeline must not be silently saturating: a full buffer,
critical backpressure, rejected events, an unbounded enqueue/dequeue backlog, or
a high error rate all mean fills/heartbeats are being dropped before they reach
the runtime summary. This evaluates the telemetry `/api/telemetry/stats` object.

Failure semantics (CI-safe):
  * FAIL (exit 1) on: buffer utilization >= critical threshold; pressure_level
    "critical"; total_rejected > 0; enqueue/dequeue backlog above --max-backlog;
    or recent error rate >= critical threshold.
  * REPORT (exit 0) otherwise.

Usage:
    docker exec pantheon-telemetry-1 curl -s localhost:8083/api/telemetry/stats \
      | python3 scripts/verify_e2e_telemetry_pipeline_health.py
"""
from __future__ import annotations

import argparse
import json
import sys


def evaluate(stats: dict, max_backlog: int) -> tuple[bool, list[str]]:
    bp = stats.get("backpressure") or {}
    buf = stats.get("buffer") or {}
    problems = []

    util = float(buf.get("utilization_pct") or 0.0) / 100.0
    crit = float(bp.get("buffer_utilization_critical") or 0.9)
    if util >= crit:
        problems.append(f"buffer utilization {util:.2f} >= critical {crit:.2f}")

    if str(bp.get("pressure_level") or "").lower() == "critical":
        problems.append("backpressure pressure_level=critical")

    rejected = int(buf.get("total_rejected") or 0)
    if rejected > 0:
        problems.append(f"buffer total_rejected={rejected} (events dropped)")

    backlog = int(buf.get("total_enqueued") or 0) - int(buf.get("total_dequeued") or 0)
    # `size` is the live backlog; prefer it when present.
    live_size = buf.get("size")
    if live_size is not None:
        backlog = int(live_size)
    if backlog > max_backlog:
        problems.append(f"enqueue/dequeue backlog {backlog} > {max_backlog}")

    recent_errors = int(bp.get("recent_errors") or 0)
    recent_writes = int(bp.get("recent_writes") or 0)
    err_crit = float(bp.get("error_rate_critical") or 0.3)
    if recent_writes > 0:
        rate = recent_errors / max(recent_writes + recent_errors, 1)
        if rate >= err_crit:
            problems.append(f"error rate {rate:.2f} >= critical {err_crit:.2f}")

    return (not problems), problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-backlog", type=int, default=10000)
    ap.add_argument("--stats-file", default="-")
    args = ap.parse_args()
    raw = sys.stdin.read() if args.stats_file == "-" else open(args.stats_file).read()
    try:
        stats = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: bad stats JSON: {exc}", file=sys.stderr)
        return 2

    ok, problems = evaluate(stats, args.max_backlog)
    buf = stats.get("buffer") or {}
    bp = stats.get("backpressure") or {}
    print("== telemetry pipeline health ==")
    print(f"  buffer util%={buf.get('utilization_pct')} size={buf.get('size')} "
          f"rejected={buf.get('total_rejected')} | pressure={bp.get('pressure_level')} "
          f"errors={bp.get('recent_errors')}")
    if not ok:
        print(f"\nFAIL: telemetry pipeline degraded:")
        for p in problems:
            print(f"   {p}")
        return 1
    print("\nOK: telemetry pipeline healthy (buffer drained, no rejections, pressure normal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
