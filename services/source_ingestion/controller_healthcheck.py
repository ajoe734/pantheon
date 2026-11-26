"""Compose healthcheck: liveness of the source-ingestion controller loop.

Healthy means the loop is alive and completing ticks. A connector that reports a failed
outcome (collector not deployed, upstream paused) does not make the container unhealthy;
that stays visible in controller readback and source health. Controller level failures
(source-ingest unreachable, reconcile refused, desired state unreadable, invalid state)
fail the check once they repeat CONTROLLER_FAILURE_LIMIT ticks in a row.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .controller_state import ControllerStateError, ControllerStateStore, parse_utc

CONTROLLER_FAILURE_LIMIT = 3


def evaluate(path: Path, interval: int, max_age: int, now: datetime | None = None) -> str | None:
    """Return None when healthy, otherwise a readable unhealthy reason."""

    now = now or datetime.now(timezone.utc)
    try:
        state = ControllerStateStore(path).load()
        if state is None or state.heartbeat_at is None:
            return "controller state is missing or has no heartbeat"
        heartbeat = parse_utc(state.heartbeat_at)
        completed = [t for t in (parse_utc(state.last_success_at), parse_utc(state.last_failure_at)) if t]
    except (ControllerStateError, ValueError, TypeError) as exc:
        return f"controller state is unreadable: {exc}"
    if heartbeat is None or (now - heartbeat).total_seconds() > max_age:
        return f"controller heartbeat is older than {max_age}s"
    if not completed or (now - max(completed)).total_seconds() > max_age:
        return f"no controller tick has completed within {max_age}s"
    if state.consecutive_controller_failures >= CONTROLLER_FAILURE_LIMIT:
        return (
            f"last {state.consecutive_controller_failures} ticks failed at controller level "
            f"(stage={state.last_failure_stage}): {state.last_failure_reason}"
        )
    return None


def main() -> int:
    path = Path(os.getenv("SOURCE_INGEST_CONTROLLER_STATE_PATH") or "/data/source-ingest/controller_state.json")
    interval = max(1, int(os.getenv("SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS") or "60"))
    max_age = max(interval * 3, int(os.getenv("SOURCE_INGEST_CONTROLLER_HEALTH_MAX_AGE_SECONDS") or "0"))
    reason = evaluate(path, interval, max_age)
    if reason:
        print(f"unhealthy: {reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
