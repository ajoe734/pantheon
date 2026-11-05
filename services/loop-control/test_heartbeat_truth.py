import asyncio
import importlib

import pytest

LoopControllerWriter = importlib.import_module("services.loop-control.writer").LoopControllerWriter


def _heartbeat(**kwargs):
    writer = object.__new__(LoopControllerWriter)
    captured = {}

    async def fake_write_status(loop_id, truth_level, **fields):
        captured.update(fields)

    writer._write_status = fake_write_status
    asyncio.run(writer.record_heartbeat("loop", **kwargs))
    return captured


def test_observed_ready_probe_records_success():
    got = _heartbeat(desired_state={"present": True}, downstream_actual_state={"status": "ready"})
    assert got["last_success_at"] is not None and got["last_tick_at"] is not None


@pytest.mark.parametrize("actual", [{}, {"status": "unknown"}, {"status": "degraded"}, {"status": "failed"}, {"status": "error"}, {"status": "unconfigured"}, {"status": "stale"}])
def test_non_success_probe_never_records_success(actual):
    got = _heartbeat(desired_state={"present": True}, downstream_actual_state=actual)
    assert got["last_success_at"] is None and got["last_tick_at"] is None
    assert got["last_heartbeat_at"] is not None


def test_missing_state_or_other_truth_level_never_records_success():
    assert _heartbeat()["last_success_at"] is None
    got = _heartbeat(truth_level="scheduled_tick", desired_state={"present": True}, downstream_actual_state={"status": "ready"})
    assert got["last_success_at"] is None
