"""Scheduled reconciliation worker for the reconciliation-drift service.

Runs periodic reconciliation passes that fetch active runtime summaries from
the telemetry service and create evaluation records for each active binding.
Duplicate ticks with the same tick_id are idempotent.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def run_tick(
    *,
    api_url: str,
    tick_id: str | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """POST /api/reconciliation-drift/scheduled-reconcile and return the response."""
    body: dict[str, Any] = {}
    if tick_id:
        body["tick_id"] = tick_id
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        api_url.rstrip("/") + "/api/reconciliation-drift/scheduled-reconcile",
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            response_body = response.read().decode("utf-8")
        return json.loads(response_body) if response_body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"status": "error", "code": exc.code, "detail": detail}
    except urllib.error.URLError as exc:
        return {"status": "error", "detail": str(exc.reason)}


def main() -> int:
    api_url = os.getenv(
        "RECONCILIATION_DRIFT_URL", "http://reconciliation-drift-svc:8102"
    )
    interval_seconds = _env_int(
        "RECONCILIATION_DRIFT_SCHEDULER_INTERVAL_SECONDS", 300, minimum=1
    )
    max_ticks = _env_int("RECONCILIATION_DRIFT_SCHEDULER_MAX_TICKS", 0, minimum=0)
    tick = 0
    while True:
        tick += 1
        result = run_tick(api_url=api_url)
        print(json.dumps({"tick": tick, "result": result}, sort_keys=True), flush=True)
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
