"""Daily bars volume and traded value acceptance (DAILY-BARS-COLLECTOR-VOLUME-VALUE-20261125).

Read-only. Counts stored daily-bars days (from the source-ingest health payload, daily_bars.volume) whose traded value is null on more than 5 % of their traded rows. Days
older than the fix may still wait for the completion tick (reported, not failed); a day dated after ``since`` that
is over the threshold fails the check. Exit 0 passed, 1 failed, 77 when ACCEPTANCE_SOURCE_INGEST_URL is unset.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any, Mapping

DEFAULT_SINCE = "2026-11-26"  # the first trade date collected after the fix is deployed (override: ACCEPTANCE_DAILY_BARS_FIX_DATE)


def evaluate(volume: Mapping[str, Any], since: str) -> tuple[bool, str]:
    """``volume`` is ``daily_bars.volume`` of GET /api/source-ingest/health."""
    days = list(volume.get("days") or [])
    newer = [d for d in days if str(d.get("trade_date")) > since]
    detail = (f"days over 5% null traded value: {int(volume.get('days_over_threshold') or 0)} "
              f"(newer than {since}: {len(newer)}); examined {volume.get('examined_days')} of {volume.get('stored_days')} "
              f"stored days, unretained {volume.get('unretained_days')}")
    failed = bool(volume.get("completion_failed"))
    if failed:
        run = volume.get("last_run") or {}
        detail += f"; completion FAILED (stopped {run.get('stopped')}, baseline rss {run.get('baseline_rss_mb')} MB)"
    return not newer and not failed, detail


def fetch(base_url: str, token: str | None = None) -> Mapping[str, Any]:
    url = f"{base_url.rstrip('/')}/api/source-ingest/health"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - base URL comes from the stage environment
        payload = json.loads(response.read().decode("utf-8"))
    return (payload.get("daily_bars") or {}).get("volume") or {}


def main() -> int:
    base = os.environ.get("ACCEPTANCE_SOURCE_INGEST_URL", "").strip()
    if not base:
        print("skipped: ACCEPTANCE_SOURCE_INGEST_URL is not set")
        return 77
    since = os.environ.get("ACCEPTANCE_DAILY_BARS_FIX_DATE", DEFAULT_SINCE)
    try:
        report = fetch(base, os.environ.get("ACCEPTANCE_SOURCE_INGEST_TOKEN") or None)
    except Exception as exc:  # noqa: BLE001 - an unreadable service is a failed check, not a pass
        print(f"failed: {type(exc).__name__}: {exc}"[:300])
        return 1
    if not report:
        print("failed: the health payload has no daily_bars.volume")
        return 1
    ok, detail = evaluate(report, since)
    print(("passed: " if ok else "failed: ") + detail)
    for day in (report.get("days") or [])[:20]:
        print(f"  {day['trade_date']}: {day['no_value']} of {day['traded_rows']} traded rows have no traded value")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
