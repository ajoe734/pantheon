from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.source_ingestion import controller_healthcheck
from services.source_ingestion.controller_state import ControllerState, ControllerStateStore, utc_now

INTERVAL = 60
MAX_AGE = 180


def _state() -> ControllerState:
    state = ControllerState(
        controller_id="c", controller_name="n", environment="dev", tenant_id="t", deployment={}
    )
    state.record_tick_started()
    return state


def _check(tmp_path, state: ControllerState | None, now: datetime | None = None):
    path = tmp_path / "controller_state.json"
    if state is not None:
        ControllerStateStore(path).save(state)
    return controller_healthcheck.evaluate(path, INTERVAL, MAX_AGE, now)


def _ok(state: ControllerState) -> None:
    state.record_success(desired_state={}, reconcile={}, schedule={}, actual_readback={})


def test_missing_state_unhealthy(tmp_path) -> None:
    assert "missing" in _check(tmp_path, None)


def test_unreadable_state_unhealthy(tmp_path) -> None:
    (tmp_path / "controller_state.json").write_text("{not json")
    assert "unreadable" in _check(tmp_path, None)


def test_healthy_after_success(tmp_path) -> None:
    state = _state()
    _ok(state)
    assert _check(tmp_path, state) is None


def test_hosted_shape_connector_failure_is_healthy(tmp_path) -> None:
    """2026-11-17 15:10Z: fresh heartbeat, ticks running, last tick failed at schedule."""
    state = _state()
    _ok(state)
    for _ in range(5):
        state.record_tick_started()
        state.record_failure(
            stage="schedule", reason="scheduled source tick reported 2 failed connector(s)"
        )
    assert state.consecutive_failures == 5
    assert _check(tmp_path, state) is None


def test_no_tick_completed_is_unhealthy(tmp_path) -> None:
    assert "no controller tick has completed" in _check(tmp_path, _state())


def test_stale_heartbeat_unhealthy(tmp_path) -> None:
    state = _state()
    _ok(state)
    later = datetime.now(timezone.utc) + timedelta(seconds=MAX_AGE + 5)
    assert "heartbeat" in _check(tmp_path, state, later)


def test_old_completion_with_fresh_heartbeat_unhealthy(tmp_path) -> None:
    state = _state()
    _ok(state)
    state.last_success_at = (datetime.now(timezone.utc) - timedelta(seconds=MAX_AGE + 30)).isoformat()
    state.heartbeat_at = utc_now()
    assert "no controller tick has completed" in _check(tmp_path, state)


def test_reconcile_http_400_three_times_is_unhealthy(tmp_path) -> None:
    """2026-11-17 06:47Z: reconcile refused with HTTP 400."""
    state = _state()
    _ok(state)
    for _ in range(3):
        state.record_tick_started()
        state.record_failure(stage="controller", reason="HTTPError: HTTP Error 400: Bad Request: refused")
    reason = _check(tmp_path, state)
    assert reason and "controller level" in reason and "400" in reason


def test_two_controller_failures_still_healthy_then_unhealthy(tmp_path) -> None:
    state = _state()
    _ok(state)
    state.record_failure(stage="controller", reason="timeout")
    state.record_failure(stage="controller", reason="timeout")
    assert _check(tmp_path, state) is None
    state.record_failure(stage="reconcile", reason="refused")
    assert _check(tmp_path, state) is not None


def test_connector_failure_between_breaks_controller_streak(tmp_path) -> None:
    state = _state()
    _ok(state)
    for stage in ("controller", "controller", "schedule", "controller"):
        state.record_failure(stage=stage, reason="r")
    assert _check(tmp_path, state) is None


def test_unknown_stage_counts_as_controller_level(tmp_path) -> None:
    state = _state()
    _ok(state)
    for _ in range(3):
        state.record_failure(stage="brand_new_stage", reason="r")
    assert _check(tmp_path, state) is not None


def test_main_exit_codes(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "controller_state.json"
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_STATE_PATH", str(path))
    assert controller_healthcheck.main() == 1
    state = _state()
    state.record_failure(stage="schedule", reason="connector failed")
    ControllerStateStore(path).save(state)
    assert controller_healthcheck.main() == 0
