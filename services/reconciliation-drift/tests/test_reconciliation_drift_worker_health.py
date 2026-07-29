from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

from services.background_worker_health import healthcheck, write_health


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, SERVICE_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_healthcheck_fails_closed_until_expected_worker_completes_fresh_tick(
    tmp_path: Path,
) -> None:
    health_file = tmp_path / "worker-health.json"

    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=15,
            max_age_seconds=60,
            worker_name="expected-worker",
        )
        == 1
    )

    write_health(
        str(health_file),
        {"worker_name": "expected-worker", "status": "starting", "ticks": 0},
    )
    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=15,
            max_age_seconds=60,
            worker_name="expected-worker",
        )
        == 1
    )

    write_health(
        str(health_file),
        {"worker_name": "different-worker", "status": "ok", "ticks": 1},
    )
    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=15,
            max_age_seconds=60,
            worker_name="expected-worker",
        )
        == 1
    )

    write_health(
        str(health_file),
        {
            "worker_name": "expected-worker",
            "status": "ok",
            "ticks": 1,
            "controller_status": "degraded",
        },
    )
    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=15,
            max_age_seconds=60,
            worker_name="expected-worker",
        )
        == 0
    )

    stale_at = time.time() - 61
    os.utime(health_file, (stale_at, stale_at))
    assert (
        healthcheck(
            health_file=str(health_file),
            interval_seconds=15,
            max_age_seconds=60,
            worker_name="expected-worker",
            now=time.time(),
        )
        == 1
    )


def test_consumer_publishes_completed_tick_separately_from_controller_truth(
    tmp_path: Path,
) -> None:
    consumer = _load("reconciliation_drift_consumer_health", "consumer.py")
    health_file = tmp_path / "consumer-health.json"
    state_path = tmp_path / "consumer-state.json"
    result = {
        "status": "degraded",
        "controller_status": "degraded",
        "source_error": "telemetry temporarily unavailable",
    }

    with mock.patch.dict(
        "os.environ",
        {
            "RECONCILIATION_DRIFT_CONSUMER_HEALTH_FILE": str(health_file),
            "RECONCILIATION_DRIFT_CONSUMER_MAX_TICKS": "1",
            "RECONCILIATION_DRIFT_CONSUMER_INTERVAL_SECONDS": "1",
        },
    ), mock.patch.object(
        consumer,
        "run_runtime_summary_consumer_once",
        return_value=result,
    ):
        assert (
            consumer.main(
                [
                    "--max-ticks",
                    "1",
                    "--state-path",
                    str(state_path),
                ]
            )
            == 0
        )

    state = json.loads(health_file.read_text(encoding="utf-8"))
    assert state["worker_name"] == "reconciliation-drift-consumer"
    assert state["status"] == "ok"
    assert state["ticks"] == 1
    assert state["controller_status"] == "degraded"
    assert state["last_failure_reason"] == "telemetry temporarily unavailable"
    with mock.patch.dict(
        "os.environ",
        {
            "RECONCILIATION_DRIFT_CONSUMER_HEALTH_FILE": str(health_file),
            "RECONCILIATION_DRIFT_CONSUMER_INTERVAL_SECONDS": "1",
        },
    ):
        assert consumer.healthcheck() == 0


def test_scheduler_and_listener_publish_heartbeat_after_observed_failure(
    tmp_path: Path,
) -> None:
    scheduler = _load("reconciliation_drift_scheduler_health", "scheduler_worker.py")
    listener = _load("reconciliation_drift_listener_health", "incident_listener.py")
    scheduler_health = tmp_path / "scheduler-health.json"
    listener_health = tmp_path / "listener-health.json"

    scheduler_env = {
        "RECONCILIATION_DRIFT_SCHEDULER_HEALTH_FILE": str(scheduler_health),
        "RECONCILIATION_DRIFT_SCHEDULER_INTERVAL_SECONDS": "1",
        "RECONCILIATION_DRIFT_SCHEDULER_MAX_TICKS": "1",
        "RECONCILIATION_DRIFT_SCHEDULER_MAX_ATTEMPTS": "1",
        "RECONCILIATION_DRIFT_SCHEDULER_RETRY_BACKOFF_SECONDS": "0",
    }
    with mock.patch.dict("os.environ", scheduler_env), mock.patch.object(
        scheduler,
        "run_tick",
        side_effect=OSError("reconciliation API unavailable"),
    ):
        assert scheduler.main() == 0

    scheduler_state = json.loads(scheduler_health.read_text(encoding="utf-8"))
    assert scheduler_state["status"] == "ok"
    assert scheduler_state["ticks"] == 1
    assert scheduler_state["controller_status"] == "unhealthy"
    assert scheduler_state["last_failure_reason"] == "reconciliation API unavailable"
    with mock.patch.dict("os.environ", scheduler_env):
        assert scheduler.healthcheck() == 0

    listener_env = {
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_HEALTH_FILE": str(listener_health),
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_INTERVAL_SECONDS": "1",
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_TICKS": "1",
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_ATTEMPTS": "1",
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_RETRY_BACKOFF_SECONDS": "0",
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_STATE_PATH": str(
            tmp_path / "listener-state.json"
        ),
    }
    with mock.patch.dict("os.environ", listener_env), mock.patch.object(
        listener,
        "run_tick",
        side_effect=OSError("incidents API unavailable"),
    ):
        assert listener.main() == 0

    listener_state = json.loads(listener_health.read_text(encoding="utf-8"))
    assert listener_state["status"] == "ok"
    assert listener_state["ticks"] == 1
    assert listener_state["controller_status"] == "unhealthy"
    assert listener_state["last_failure_reason"] == "incidents API unavailable"
    with mock.patch.dict("os.environ", listener_env):
        assert listener.healthcheck() == 0
