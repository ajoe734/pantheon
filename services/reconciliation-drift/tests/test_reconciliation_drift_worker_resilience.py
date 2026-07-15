from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path
from unittest import mock


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, SERVICE_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def _incident(incident_id: str = "inc-resilience-001") -> dict[str, object]:
    return {
        "incident_id": incident_id,
        "status": "open",
        "binding_id": "rtb-resilience-001",
        "runtime_id": "runtime-resilience-001",
        "telemetry_event_ids": ["evt-resilience-001"],
    }


def test_scheduler_retries_one_idempotent_tick_and_reports_recovery() -> None:
    scheduler = _load("reconciliation_drift_scheduler_resilience", "scheduler_worker.py")
    request_bodies: list[dict[str, object]] = []
    sleeps: list[float] = []

    def urlopen(request, *, timeout):
        assert timeout == 4.0
        request_bodies.append(json.loads(request.data.decode("utf-8")))
        if len(request_bodies) == 1:
            raise urllib.error.URLError("connection reset")
        return _Response({"status": "ok", "evaluated_binding_count": 1})

    with mock.patch.object(scheduler.urllib.request, "urlopen", side_effect=urlopen):
        result = scheduler.run_tick(
            api_url="http://reconciliation",
            timeout_seconds=4.0,
            max_attempts=3,
            retry_backoff_seconds=0.25,
            sleep_fn=sleeps.append,
        )

    assert result["controller_status"] == "healthy"
    assert result["attempt_count"] == 2
    assert result["error_count"] == 1
    assert result["attempts"][0]["status"] == "error"
    assert result["attempts"][1]["status"] == "ok"
    assert result["last_success_at"]
    assert result["last_failure_at"]
    assert sleeps == [0.25]
    assert request_bodies[0]["tick_id"] == request_bodies[1]["tick_id"]
    assert result["tick_id"] == request_bodies[0]["tick_id"]


def test_scheduler_retry_exhaustion_is_unhealthy_and_bounded() -> None:
    scheduler = _load("reconciliation_drift_scheduler_exhausted", "scheduler_worker.py")
    sleeps: list[float] = []
    with mock.patch.object(
        scheduler.urllib.request,
        "urlopen",
        side_effect=urllib.error.URLError("offline"),
    ) as urlopen:
        result = scheduler.run_tick(
            api_url="http://reconciliation",
            tick_id="tick-bounded-001",
            max_attempts=3,
            retry_backoff_seconds=0.5,
            sleep_fn=sleeps.append,
        )

    assert result["status"] == "error"
    assert result["controller_status"] == "unhealthy"
    assert result["attempt_count"] == 3
    assert result["error_count"] == 3
    assert urlopen.call_count == 3
    assert sleeps == [0.5, 0.5]


def test_listener_persists_partial_failure_and_replays_after_restart(tmp_path: Path) -> None:
    listener = _load("reconciliation_drift_listener_resilience", "incident_listener.py")
    state_path = tmp_path / "listener-state.json"
    incident = _incident()
    sleeps: list[float] = []

    with mock.patch.object(listener, "fetch_open_incidents", return_value=[incident]), mock.patch.object(
        listener,
        "post_incident_trigger",
        side_effect=urllib.error.URLError("reconciliation unavailable"),
    ) as post:
        failed = listener.run_tick(
            incidents_url="http://incidents",
            reconciliation_url="http://reconciliation",
            max_attempts=2,
            retry_backoff_seconds=0.2,
            state_path=state_path,
            sleep_fn=sleeps.append,
        )

    assert failed["status"] == "partial_error"
    assert failed["controller_status"] == "degraded"
    assert failed["attempt_count"] == 3  # one real fetch plus two delivery attempts
    assert failed["error_count"] == 2
    assert failed["triggered_incident_count"] == 0
    assert failed["backlog_count"] == 1
    assert failed["oldest_backlog_age_seconds"] is not None
    assert failed["last_failure_at"]
    assert post.call_count == 2
    assert sleeps == [0.2]

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert list(persisted["backlog"]) == ["inc-resilience-001"]
    assert persisted["backlog"]["inc-resilience-001"]["incident"] == incident
    assert persisted["backlog"]["inc-resilience-001"]["attempt_count"] == 2
    assert not list(tmp_path.glob("*.tmp"))

    # A new state object models a new worker process.  The incident no longer
    # appears in the open-incidents response, so only durable backlog can replay it.
    restarted_state = listener.IncidentListenerState(state_path)
    with mock.patch.object(listener, "fetch_open_incidents", return_value=[]), mock.patch.object(
        listener,
        "post_incident_trigger",
        return_value={"status": "ok", "created": True},
    ) as replay_post:
        recovered = listener.run_tick(
            incidents_url="http://incidents",
            reconciliation_url="http://reconciliation",
            max_attempts=2,
            retry_backoff_seconds=0.2,
            state=restarted_state,
            sleep_fn=lambda _seconds: (_ for _ in ()).throw(AssertionError("unexpected sleep")),
        )

    assert recovered["status"] == "ok"
    assert recovered["controller_status"] == "healthy"
    assert recovered["replay_attempted_count"] == 1
    assert recovered["replayed_incident_count"] == 1
    assert recovered["triggered_incident_count"] == 1
    assert recovered["backlog_count"] == 0
    assert recovered["oldest_backlog_age_seconds"] is None
    assert recovered["last_success_at"]
    replay_post.assert_called_once_with(
        reconciliation_url="http://reconciliation",
        incident=incident,
        timeout_seconds=30.0,
    )
    assert json.loads(state_path.read_text(encoding="utf-8"))["backlog"] == {}


def test_listener_deduplicates_replayed_incident_from_current_fetch(tmp_path: Path) -> None:
    listener = _load("reconciliation_drift_listener_dedup", "incident_listener.py")
    state_path = tmp_path / "listener-state.json"
    incident = _incident("inc-still-open-001")
    state = listener.IncidentListenerState(state_path)
    state.record_delivery_failure(
        identity="inc-still-open-001",
        incident=incident,
        error="first failure",
        attempt_count=1,
        failed_at="2026-01-01T00:00:00Z",
    )

    with mock.patch.object(listener, "fetch_open_incidents", return_value=[incident]), mock.patch.object(
        listener,
        "post_incident_trigger",
        return_value={"status": "ok", "created": False},
    ) as post:
        result = listener.run_tick(
            incidents_url="http://incidents",
            reconciliation_url="http://reconciliation",
            state=state,
        )

    assert result["controller_status"] == "healthy"
    assert result["replayed_incident_count"] == 1
    assert result["deduplicated_incident_count"] == 1
    assert result["backlog_count"] == 0
    assert post.call_count == 1


def test_listener_does_not_treat_empty_delivery_response_as_success(tmp_path: Path) -> None:
    listener = _load("reconciliation_drift_listener_empty_response", "incident_listener.py")
    state_path = tmp_path / "listener-state.json"

    with mock.patch.object(
        listener, "fetch_open_incidents", return_value=[_incident()]
    ), mock.patch.object(listener, "post_incident_trigger", return_value={}):
        result = listener.run_tick(
            incidents_url="http://incidents",
            reconciliation_url="http://reconciliation",
            state_path=state_path,
        )

    assert result["controller_status"] == "degraded"
    assert result["triggered_incident_count"] == 0
    assert result["backlog_count"] == 1
    assert "omitted delivery acknowledgement" in result["errors"][0]["detail"]


def test_corrupt_listener_state_is_not_overwritten_or_reported_green(tmp_path: Path) -> None:
    listener = _load("reconciliation_drift_listener_corrupt", "incident_listener.py")
    state_path = tmp_path / "listener-state.json"
    state_path.write_text("{not-json", encoding="utf-8")

    with mock.patch.object(listener, "fetch_open_incidents") as fetch:
        result = listener.run_tick(
            incidents_url="http://incidents",
            reconciliation_url="http://reconciliation",
            state_path=state_path,
        )

    assert result["status"] == "error"
    assert result["controller_status"] == "unhealthy"
    assert result["errors"][0]["phase"] == "load_state"
    assert state_path.read_text(encoding="utf-8") == "{not-json"
    fetch.assert_not_called()


def test_worker_mains_only_return_nonzero_for_invalid_configuration(tmp_path: Path) -> None:
    scheduler = _load("reconciliation_drift_scheduler_main_resilience", "scheduler_worker.py")
    listener = _load("reconciliation_drift_listener_main_resilience", "incident_listener.py")

    with mock.patch.dict(
        "os.environ",
        {"RECONCILIATION_DRIFT_SCHEDULER_INTERVAL_SECONDS": "0"},
    ):
        assert scheduler.main() == 2

    scheduler_env = {
        "RECONCILIATION_DRIFT_SCHEDULER_INTERVAL_SECONDS": "1",
        "RECONCILIATION_DRIFT_SCHEDULER_MAX_TICKS": "1",
        "RECONCILIATION_DRIFT_SCHEDULER_MAX_ATTEMPTS": "1",
        "RECONCILIATION_DRIFT_SCHEDULER_RETRY_BACKOFF_SECONDS": "0",
    }
    with mock.patch.dict("os.environ", scheduler_env), mock.patch.object(
        scheduler, "run_tick", side_effect=OSError("runtime unavailable")
    ):
        assert scheduler.main() == 0

    with mock.patch.dict(
        "os.environ",
        {"RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_ATTEMPTS": "0"},
    ):
        assert listener.main() == 2

    listener_env = {
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_INTERVAL_SECONDS": "1",
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_TICKS": "1",
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_ATTEMPTS": "1",
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_RETRY_BACKOFF_SECONDS": "0",
        "RECONCILIATION_DRIFT_INCIDENT_LISTENER_STATE_PATH": str(tmp_path / "main-state.json"),
    }
    with mock.patch.dict("os.environ", listener_env), mock.patch.object(
        listener, "run_tick", side_effect=OSError("runtime unavailable")
    ):
        assert listener.main() == 0
