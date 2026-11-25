"""Daily bars volume and traded value acceptance (DAILY-BARS-COLLECTOR-VOLUME-VALUE-20261125).

Read-only. Counts stored daily-bars days whose traded value is null on more than 5 % of their traded rows. Days
older than the fix may still wait for the completion tick (reported, not failed); a day dated after ``since`` that
is over the threshold fails the check. Exit 0 passed, 1 failed, 77 when ACCEPTANCE_SOURCE_INGEST_URL is unset.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any, Mapping

DEFAULT_SINCE = "2026-11-26"  # the first trade date collected after the fix is deployed (override: ACCEPTANCE_DAILY_BARS_FIX_DATE)


def evaluate(report: Mapping[str, Any]) -> tuple[bool, str]:
    newer = int(report.get("days_over_threshold_newer_than_fix") or 0)
    total = int(report.get("days_over_threshold") or 0)
    detail = (f"days over 5% null traded value: {total} (newer than {report.get('since')}: {newer}); "
              f"checked {report.get('checked_days')} of {report.get('stored_days')} stored days")
    return newer == 0, detail


def fetch(base_url: str, since: str, token: str | None = None) -> Mapping[str, Any]:
    url = f"{base_url.rstrip('/')}/api/source-ingest/daily-bars/volume?" + urllib.parse.urlencode({"since": since})
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed https/http base from the environment
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    base = os.environ.get("ACCEPTANCE_SOURCE_INGEST_URL", "").strip()
    if not base:
        print("skipped: ACCEPTANCE_SOURCE_INGEST_URL is not set")
        return 77
    since = os.environ.get("ACCEPTANCE_DAILY_BARS_FIX_DATE", DEFAULT_SINCE)
    try:
        report = fetch(base, since, os.environ.get("ACCEPTANCE_SOURCE_INGEST_TOKEN") or None)
    except Exception as exc:  # noqa: BLE001 - an unreadable service is a failed check, not a pass
        print(f"failed: {type(exc).__name__}: {exc}"[:300])
        return 1
    ok, detail = evaluate(report)
    print(("passed: " if ok else "failed: ") + detail)
    for day in (report.get("days") or [])[:20]:
        print(f"  {day['trade_date']}: {day['no_value']} of {day['traded_rows']} traded rows have no traded value")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
