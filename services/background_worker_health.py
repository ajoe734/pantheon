"""File-backed liveness contract for long-running background workers.

Background workers do not expose an HTTP socket, so their container
healthchecks need a small, process-local signal that proves the worker loop is
still advancing.  The heartbeat is deliberately separate from business
readiness: callers record downstream/controller truth in the document, while
this module only accepts a recent heartbeat from the expected worker after at
least one completed tick.

Heartbeat replacement is atomic so a concurrent healthcheck never accepts a
partially written JSON document.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping


def write_health(path: str, state: Mapping[str, Any]) -> None:
    """Atomically replace *path* with the current worker heartbeat."""
    if not path:
        return

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(dict(state), handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def healthcheck(
    *,
    health_file: str,
    interval_seconds: float,
    worker_name: str,
    max_age_seconds: float | None = None,
    now: float | None = None,
) -> int:
    """Return zero only for a recent heartbeat from the expected worker."""
    if not health_file:
        print(f"{worker_name} health file is not configured", file=sys.stderr)
        return 1

    if (
        not math.isfinite(interval_seconds)
        or interval_seconds <= 0
        or (
            max_age_seconds is not None
            and (not math.isfinite(max_age_seconds) or max_age_seconds <= 0)
        )
    ):
        print(f"{worker_name} health timing is invalid", file=sys.stderr)
        return 1

    try:
        with Path(health_file).open(encoding="utf-8") as handle:
            state = json.load(handle)
        age_seconds = max(
            0.0,
            (time.time() if now is None else now) - os.path.getmtime(health_file),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"{worker_name} health unavailable: {exc}", file=sys.stderr)
        return 1

    if not isinstance(state, dict):
        print(f"{worker_name} health payload is not an object", file=sys.stderr)
        return 1
    if state.get("worker_name") != worker_name:
        print(
            f"{worker_name} health identity mismatch: "
            f"observed={state.get('worker_name')}",
            file=sys.stderr,
        )
        return 1
    if state.get("status") != "ok" or not isinstance(state.get("ticks"), int):
        print(
            f"{worker_name} health is not ready: "
            f"status={state.get('status')} ticks={state.get('ticks')}",
            file=sys.stderr,
        )
        return 1
    if state["ticks"] < 1:
        print(
            f"{worker_name} health is not ready: ticks={state['ticks']}",
            file=sys.stderr,
        )
        return 1

    allowed_age = (
        max_age_seconds
        if max_age_seconds is not None
        else max(300.0, interval_seconds * 3)
    )
    if age_seconds > allowed_age:
        print(
            f"{worker_name} health is stale: "
            f"age_seconds={age_seconds:.1f} max_age_seconds={allowed_age:.1f}",
            file=sys.stderr,
        )
        return 1
    return 0
