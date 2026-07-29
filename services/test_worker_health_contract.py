from __future__ import annotations

import os
import time
from pathlib import Path

from services.worker_health import healthcheck, write_health


def test_healthcheck_fails_closed_until_safe_successful_tick(
    tmp_path: Path,
) -> None:
    health_file = tmp_path / "worker-health.json"
    expected = {"execution_mode": "paper", "live_capital_enabled": False}

    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=30,
            worker_name="paper-worker",
            expected=expected,
        )
        == 1
    )

    write_health(
        str(health_file),
        {
            "worker_name": "paper-worker",
            "status": "starting",
            "ticks": 0,
            **expected,
        },
    )
    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=30,
            worker_name="paper-worker",
            expected=expected,
        )
        == 1
    )

    write_health(
        str(health_file),
        {
            "worker_name": "paper-worker",
            "status": "ok",
            "ticks": 1,
            "execution_mode": "paper",
            "live_capital_enabled": True,
        },
    )
    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=30,
            worker_name="paper-worker",
            expected=expected,
        )
        == 1
    )

    write_health(
        str(health_file),
        {
            "worker_name": "paper-worker",
            "status": "ok",
            "ticks": 1,
            **expected,
        },
    )
    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=30,
            worker_name="paper-worker",
            expected=expected,
        )
        == 0
    )

    stale_at = time.time() - 91
    os.utime(health_file, (stale_at, stale_at))
    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=30,
            worker_name="paper-worker",
            expected=expected,
            now=time.time(),
        )
        == 1
    )
