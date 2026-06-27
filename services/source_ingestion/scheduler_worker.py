"""HTTP worker for bounded source-ingest schedule ticks."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


class SchedulerState:
    """Persistent metrics for cross-restart missed-tick tracking.

    State is persisted to a JSON file so a restarted worker can compute how
    many scheduled ticks were missed while the process was down.
    """

    def __init__(self, state_path: str | Path | None = None) -> None:
        self._path = Path(state_path) if state_path else None
        self.last_success_at: str | None = None
        self.last_failure_at: str | None = None
        self.last_failure_error: str | None = None
        self.missed_tick_count: int = 0
        self.total_successes: int = 0
        self.total_failures: int = 0
        if self._path and self._path.exists():
            self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
            self.last_success_at = data.get("last_success_at")
            self.last_failure_at = data.get("last_failure_at")
            self.last_failure_error = data.get("last_failure_error")
            self.missed_tick_count = int(data.get("missed_tick_count", 0))
            self.total_successes = int(data.get("total_successes", 0))
            self.total_failures = int(data.get("total_failures", 0))
        except Exception:
            pass

    def save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {
                    "last_success_at": self.last_success_at,
                    "last_failure_at": self.last_failure_at,
                    "last_failure_error": self.last_failure_error,
                    "missed_tick_count": self.missed_tick_count,
                    "total_successes": self.total_successes,
                    "total_failures": self.total_failures,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def compute_startup_missed(self, *, interval_seconds: int) -> int:
        """Estimate ticks missed during downtime (for startup missed-tick reporting).

        Anchors on the latest tick attempt (success or failure) so that failures
        already persisted in missed_tick_count are not recounted.  The caller is
        about to run a tick immediately, so the current incomplete interval is
        not counted (-1 adjustment).
        Returns 0 when no tick attempt has ever been recorded.
        """
        # Use the most recent tick attempt as the anchor.  Failures that occurred
        # after the last success already incremented missed_tick_count, so anchoring
        # on last_success_at would double-count those ticks on restart.
        last_attempt = self.last_success_at
        if self.last_failure_at and (not last_attempt or self.last_failure_at > last_attempt):
            last_attempt = self.last_failure_at
        if not last_attempt:
            return 0
        try:
            last_dt = datetime.fromisoformat(last_attempt.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
            return max(0, int(elapsed / interval_seconds) - 1)
        except Exception:
            return 0

    def record_success(self) -> None:
        self.last_success_at = _utc_now()
        self.missed_tick_count = 0
        self.total_successes += 1
        self.save()

    def record_failure(self, error: str) -> None:
        self.last_failure_at = _utc_now()
        self.last_failure_error = error
        self.missed_tick_count += 1
        self.total_failures += 1
        self.save()

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "missed_tick_count": self.missed_tick_count,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
        }


def main() -> int:
    api_url = os.getenv("SOURCE_INGEST_API_URL", "http://127.0.0.1:8097")
    interval_seconds = _env_int("SOURCE_INGEST_SCHEDULER_INTERVAL_SECONDS", 60, minimum=1)
    max_concurrency = _env_int("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY", 2, minimum=1)
    max_ticks = _env_int("SOURCE_INGEST_SCHEDULER_MAX_TICKS", 0, minimum=0)
    state_path = os.getenv("SOURCE_INGEST_SCHEDULER_STATE_PATH", "")
    alive_path = os.getenv("SOURCE_INGEST_SCHEDULER_ALIVE_PATH", "")

    state = SchedulerState(state_path or None)

    # Compute ticks missed while the process was down (restart recovery).
    startup_missed = state.compute_startup_missed(interval_seconds=interval_seconds)
    if startup_missed > 0:
        state.missed_tick_count += startup_missed

    print(
        json.dumps(
            {"event": "startup", "startup_missed_ticks": startup_missed, **state.to_dict()},
            sort_keys=True,
        ),
        flush=True,
    )

    tick = 0
    while True:
        tick += 1
        try:
            result = run_tick(api_url=api_url, max_concurrency=max_concurrency)
            state.record_success()
            print(json.dumps({"tick": tick, "result": result, **state.to_dict()}, sort_keys=True), flush=True)
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
            state.record_failure(error)
            print(json.dumps({"tick": tick, "error": error, **state.to_dict()}, sort_keys=True), flush=True)

        if alive_path:
            try:
                Path(alive_path).write_text(_utc_now(), encoding="utf-8")
            except Exception:
                pass

        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke worker.
    raise SystemExit(main())
