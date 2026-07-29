"""HTTP worker for scheduled search index refresh ticks."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _post_json(api_url: str, path: str, body: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        api_url.rstrip("/") + path,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        text = response.read().decode("utf-8")
    return json.loads(text) if text else {}


def run_tick(
    *,
    api_url: str,
    force_full: bool = False,
    materialize: bool = False,
    trigger_ref: str | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Refresh the durable search index and optionally materialize a recovery snapshot."""

    ref = trigger_ref or f"scheduled:{int(time.time())}"
    refresh = _post_json(
        api_url,
        "/api/search/index/refresh",
        {
            "triggered_by": "scheduled_refresh",
            "trigger_ref": ref,
            "force_full": force_full,
        },
        timeout_seconds=timeout_seconds,
    )
    result: dict[str, Any] = {"refresh": refresh, "materialize": None}
    if materialize:
        result["materialize"] = _post_json(
            api_url,
            "/api/search/index/materialize",
            {},
            timeout_seconds=timeout_seconds,
        )
    return result


def main() -> int:
    api_url = os.getenv("SEARCH_API_URL", "http://127.0.0.1:8098")
    interval_seconds = _env_int("SEARCH_INDEX_SCHEDULER_INTERVAL_SECONDS", 300, minimum=1)
    max_ticks = _env_int("SEARCH_INDEX_SCHEDULER_MAX_TICKS", 0, minimum=0)
    force_full = _env_bool("SEARCH_INDEX_SCHEDULER_FORCE_FULL", False)
    materialize = _env_bool("SEARCH_INDEX_SCHEDULER_MATERIALIZE", True)
    tick = 0
    while True:
        tick += 1
        result = run_tick(api_url=api_url, force_full=force_full, materialize=materialize)
        alive_path = os.getenv("SEARCH_INDEX_SCHEDULER_ALIVE_PATH")
        if alive_path:
            try:
                alive_p = Path(alive_path)
                alive_p.parent.mkdir(parents=True, exist_ok=True)
                alive_p.touch()
            except Exception:
                pass
        print(json.dumps({"tick": tick, "result": result}, sort_keys=True), flush=True)
        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke worker.
    raise SystemExit(main())
