"""Shared file-backed health contract for Evolution background workers.

The API has an HTTP readiness endpoint, while the scheduler-style workers do
not listen on a socket.  They publish one small heartbeat file after every
tick and Docker runs this module's validation through each worker's
``healthcheck`` command.

The file is operational state, not business authority.  It is still written
atomically so a health probe never mistakes a partially-written JSON document
for a valid heartbeat.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping


def write_health(path: str, state: Mapping[str, Any]) -> None:
    """Atomically replace *path* with the current worker health document."""
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(dict(state), handle, indent=2, sort_keys=True)
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
    interval_seconds: int,
    worker_name: str,
    now: float | None = None,
) -> int:
    """Return zero only after a recent, successful worker tick."""
    if not health_file:
        print(f"{worker_name} health file is not configured", file=sys.stderr)
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

    if state.get("status") != "ok" or state.get("ticks", 0) < 1:
        print(
            f"{worker_name} health is not ready: "
            f"status={state.get('status')} ticks={state.get('ticks')}",
            file=sys.stderr,
        )
        return 1

    max_age_seconds = max(60, interval_seconds * 3)
    if age_seconds > max_age_seconds:
        print(
            f"{worker_name} health is stale: "
            f"age_seconds={age_seconds:.1f} max_age_seconds={max_age_seconds}",
            file=sys.stderr,
        )
        return 1
    return 0
