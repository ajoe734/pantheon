from __future__ import annotations

import json

from services.source_ingestion.controller_state import (
    ControllerState,
    ControllerStateStore,
    is_controller_level_stage,
)


def _state() -> ControllerState:
    return ControllerState(
        controller_id="c", controller_name="n", environment="dev", tenant_id="t", deployment={}
    )


def test_unknown_and_missing_stage_are_controller_level() -> None:
    assert is_controller_level_stage(None)
    assert is_controller_level_stage("")
    assert is_controller_level_stage("never-heard-of-it")
    for stage in ("controller", "reconcile", "reconcile_contract", "actual_readback", "desired_state_read"):
        assert is_controller_level_stage(stage)
    assert not is_controller_level_stage("schedule")


def test_controller_streak_counts_only_controller_stages_and_resets() -> None:
    state = _state()
    state.record_failure(stage="controller", reason="x")
    state.record_failure(stage="reconcile", reason="x")
    assert (state.consecutive_failures, state.consecutive_controller_failures) == (2, 2)
    state.record_failure(stage="schedule", reason="connector failed")
    assert (state.consecutive_failures, state.consecutive_controller_failures) == (3, 0)
    state.record_failure(stage="controller", reason="x")
    state.record_success(desired_state={}, reconcile={}, schedule={}, actual_readback={})
    assert (state.consecutive_failures, state.consecutive_controller_failures) == (0, 0)


def test_streak_round_trips_and_legacy_files_fail_closed(tmp_path) -> None:
    store = ControllerStateStore(tmp_path / "s.json")
    state = _state()
    state.record_failure(stage="controller", reason="x")
    store.save(state)
    assert store.load().consecutive_controller_failures == 1
    payload = state.to_dict()
    del payload["consecutive_controller_failures"]
    assert ControllerState.from_dict(payload).consecutive_controller_failures == 1
    payload["last_failure_stage"] = "schedule"
    assert ControllerState.from_dict(payload).consecutive_controller_failures == 0
    assert isinstance(json.loads(store.path.read_text())["state"]["consecutive_controller_failures"], int)
