"""HTTP worker for bounded source-ingest schedule ticks."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def run_tick(*, api_url: str, max_concurrency: int, timeout_seconds: float = 30.0) -> dict[str, Any]:
    payload = json.dumps({"max_concurrency": max_concurrency}).encode("utf-8")
    request = urllib.request.Request(
        api_url.rstrip("/") + "/api/source-ingest/run-scheduled",
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def main() -> int:
    api_url = os.getenv("SOURCE_INGEST_API_URL", "http://127.0.0.1:8097")
    interval_seconds = _env_int("SOURCE_INGEST_SCHEDULER_INTERVAL_SECONDS", 60, minimum=1)
    max_concurrency = _env_int("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY", 2, minimum=1)
    max_ticks = _env_int("SOURCE_INGEST_SCHEDULER_MAX_TICKS", 0, minimum=0)
    tick = 0
    while True:
        tick += 1
        result = run_tick(api_url=api_url, max_concurrency=max_concurrency)
        print(json.dumps({"tick": tick, "result": result}, sort_keys=True), flush=True)
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke worker.
    raise SystemExit(main())
