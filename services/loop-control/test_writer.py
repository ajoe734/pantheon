from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .writer import LoopControllerWriter


class _FakeStore:
    def __init__(self):
        self.records = []

    async def upsert_record(self, record):
        self.records.append(record)
        return record


def _writer():
    w = LoopControllerWriter("postgresql://unused", tenant_id="t", environment="dev",
                             controller_id="c", controller_name="n", deployment_sha="s")
    w.store = _FakeStore()
    return w


def test_heartbeat_leaves_tick_and_success_unset_by_default():
    w = _writer()
    asyncio.run(w.record_heartbeat("per_persona_ooda"))
    rec = w.store.records[0]
    assert "last_tick_at" not in rec and "last_success_at" not in rec


def test_heartbeat_carries_explicit_consumed_run_and_success_times():
    w = _writer()
    tick = datetime(2026, 11, 17, 2, 5, tzinfo=timezone.utc)
    ok = datetime(2026, 11, 17, 2, 4, tzinfo=timezone.utc)
    asyncio.run(w.record_heartbeat("per_persona_ooda", last_tick_at=tick, last_success_at=ok))
    rec = w.store.records[0]
    assert rec["last_tick_at"] == tick and rec["last_success_at"] == ok


def test_loop12_probe_inference_still_wins():
    w = _writer()
    asyncio.run(w.record_heartbeat(
        "bff_health_monitoring", desired_state={"present": True}, downstream_actual_state={"status": "ready"}))
    rec = w.store.records[0]
    assert rec["last_tick_at"] == rec["last_success_at"]
