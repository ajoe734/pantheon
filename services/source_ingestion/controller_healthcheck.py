"""Compose healthcheck for accepted source-ingestion controller truth."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .controller_state import ControllerStateStore, parse_utc


def main() -> int:
    path = Path(os.getenv("SOURCE_INGEST_CONTROLLER_STATE_PATH") or "/data/source-ingest/controller_state.json")
    interval = max(1, int(os.getenv("SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS") or "60"))
    max_age = max(interval * 3, int(os.getenv("SOURCE_INGEST_CONTROLLER_HEALTH_MAX_AGE_SECONDS") or "0"))
    state = ControllerStateStore(path).load()
    if state is None or state.heartbeat_at is None:
        return 1
    heartbeat = parse_utc(state.heartbeat_at)
    if heartbeat is None or (datetime.now(timezone.utc) - heartbeat).total_seconds() > max_age:
        return 1
    if state.consecutive_failures > 0:
        return 1
    if not state.last_success_at:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
