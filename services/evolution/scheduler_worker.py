"""HTTP worker for evolution daily-sweep schedule ticks."""

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


def run_tick(
    *,
    api_url: str,
    max_incidents: int | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    body: dict[str, Any] = {"sweep_id": "scheduled-daily"}
    if max_incidents is not None:
        body["max_incidents"] = max_incidents
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        api_url.rstrip("/") + "/api/evolution/daily-sweep",
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        response_body = response.read().decode("utf-8")
    return json.loads(response_body) if response_body else {}


def main() -> int:
    api_url = os.getenv("EVOLUTION_API_URL", "http://127.0.0.1:8093")
    interval_seconds = _env_int("EVOLUTION_SCHEDULER_INTERVAL_SECONDS", 86400, minimum=1)
    max_ticks = _env_int("EVOLUTION_SCHEDULER_MAX_TICKS", 0, minimum=0)
    max_incidents_raw = os.getenv("EVOLUTION_SWEEP_MAX_INCIDENTS", "").strip()
    max_incidents = int(max_incidents_raw) if max_incidents_raw else None
    tick = 0
    while True:
        tick += 1
        result = run_tick(api_url=api_url, max_incidents=max_incidents)
        print(json.dumps({"tick": tick, "result": result}, sort_keys=True), flush=True)
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke worker.
    raise SystemExit(main())
