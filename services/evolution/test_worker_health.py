from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from services.evolution import scheduler_worker, threshold_sweep_worker
from services.evolution.worker_health import healthcheck, write_health


def test_healthcheck_fails_closed_until_a_recent_successful_tick(
    tmp_path: Path,
) -> None:
    health_file = tmp_path / "worker-health.json"

    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=30,
            worker_name="test-worker",
        )
        == 1
    )

    write_health(
        str(health_file),
        {"status": "starting", "ticks": 0},
    )
    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=30,
            worker_name="test-worker",
        )
        == 1
    )

    write_health(
        str(health_file),
        {"status": "ok", "ticks": 1},
    )
    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=30,
            worker_name="test-worker",
        )
        == 0
    )

    stale_at = time.time() - 91
    os.utime(health_file, (stale_at, stale_at))
    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=30,
            worker_name="test-worker",
            now=time.time(),
        )
        == 1
    )


def test_scheduler_writes_starting_then_successful_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health_file = tmp_path / "scheduler-health.json"
    monkeypatch.setenv("EVOLUTION_SCHEDULER_HEALTH_FILE", str(health_file))
    monkeypatch.setenv("EVOLUTION_SCHEDULER_MAX_TICKS", "1")
    monkeypatch.setenv("EVOLUTION_SCHEDULER_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("EVOLUTION_SCHEDULER_TENANT_ID", "tenant-health")
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "disabled")
    monkeypatch.setattr(
        scheduler_worker,
        "run_tick",
        lambda **_: {"sweep_id": "scheduled-daily", "proposals_created": 1},
    )

    assert scheduler_worker.main() == 0
    state = json.loads(health_file.read_text(encoding="utf-8"))
    assert state["worker_name"] == "evolution-daily-sweep-scheduler"
    assert state["status"] == "ok"
    assert state["ticks"] == 1
    assert state["tenant_id"] == "tenant-health"
    assert state["last_success_at"]


def test_scheduler_records_failure_before_preserving_restart_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health_file = tmp_path / "scheduler-health.json"
    monkeypatch.setenv("EVOLUTION_SCHEDULER_HEALTH_FILE", str(health_file))
    monkeypatch.setenv("EVOLUTION_SCHEDULER_MAX_TICKS", "1")
    monkeypatch.setenv("EVOLUTION_SCHEDULER_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("EVOLUTION_SCHEDULER_TENANT_ID", "tenant-health")
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "disabled")

    def fail_tick(**_):
        raise RuntimeError("evolution API unavailable")

    monkeypatch.setattr(scheduler_worker, "run_tick", fail_tick)
    with pytest.raises(RuntimeError, match="evolution API unavailable"):
        scheduler_worker.main()

    state = json.loads(health_file.read_text(encoding="utf-8"))
    assert state["status"] == "degraded"
    assert state["ticks"] == 1
    assert state["last_failure_at"]
    assert state["last_failure_reason"] == "evolution API unavailable"


def test_threshold_worker_health_tracks_tick_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health_file = tmp_path / "threshold-health.json"
    monkeypatch.setenv("EVOCHAIN_THRESHOLD_SWEEP_HEALTH_FILE", str(health_file))
    monkeypatch.setenv("EVOCHAIN_THRESHOLD_SWEEP_MAX_TICKS", "1")
    monkeypatch.setenv("EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS", "1")
    monkeypatch.setattr(
        threshold_sweep_worker,
        "run_tick",
        lambda **_: {
            "candidates": 0,
            "incidents_created": 0,
            "errors": 1,
            "diagnostics": ["telemetry unavailable"],
        },
    )

    assert threshold_sweep_worker.main() == 0
    state = json.loads(health_file.read_text(encoding="utf-8"))
    assert state["worker_name"] == "evolution-threshold-sweep-producer"
    assert state["status"] == "degraded"
    assert state["ticks"] == 1
    assert state["total_errors"] == 1
    assert state["last_failure_reason"] == "telemetry unavailable"
