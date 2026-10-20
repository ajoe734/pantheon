from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from . import store as store_mod
from .store import (
    MAX_EVIDENCE_REFS,
    LoopControllerStore,
    _SCHEMA_PATH,
    _dedupe_refs,
)

TICK = "consultation://workflow-ticks/executor/{i:06d}"


def _row(refs, now):
    return {
        "loop_id": "consultation",
        "tenant_id": "default",
        "environment": "test",
        "controller_id": "ctrl-1",
        "controller_name": "Ctrl",
        "deployment_sha": "sha",
        "desired_state_query": None,
        "actual_state_query": None,
        "desired_state": {"present": True, "source": "d", "checked_at": now},
        "downstream_actual_state": {"status": "ready", "source": "a", "checked_at": now},
        "backlog": 0,
        "lag": 0,
        "dlq_count": 0,
        "last_heartbeat_at": now,
        "last_tick_at": now,
        "last_success_at": now,
        "last_failure_at": None,
        "last_failure_reason": None,
        "last_repair_at": None,
        "last_repair_reason": None,
        "truth_level": "scheduled_tick",
        "lease_token": "tok",
        "lease_expires_at": now + timedelta(seconds=60),
        "evidence_refs": refs,
        "payload": {},
    }


def test_anchor_survives_repeating_tick_class():
    anchor = "docs/evidence/anchor.json"
    ticks = [TICK.format(i=i) for i in range(1000)]
    merged = _dedupe_refs([anchor], ticks)
    assert merged[0] == anchor
    assert merged[1:] == ticks[-50:]


def test_oldest_first_and_repeat_moves_to_newest():
    assert _dedupe_refs(["a/1", "a/2"], ["a/1", "a/3"]) == ["a/2", "a/1", "a/3"]


def test_per_class_limit_configurable(monkeypatch):
    monkeypatch.setenv(store_mod.EVIDENCE_REFS_PER_CLASS_ENV, "3")
    merged = _dedupe_refs([], [f"x://t/{i}" for i in range(10)])
    assert merged == ["x://t/7", "x://t/8", "x://t/9"]


def test_oversized_row_shrinks_within_one_second():
    now = datetime.now(timezone.utc)
    existing = _row([TICK.format(i=i) for i in range(40000)], now)
    existing["evidence_refs"] = json.dumps(existing["evidence_refs"])
    incoming = _row([TICK.format(i=40000)], now)
    store = LoopControllerStore("postgresql://unused")
    start = time.perf_counter()
    merged = store._merge_record(existing, incoming, now=now)
    store.validate_record(merged)
    elapsed = time.perf_counter() - start
    assert len(merged["evidence_refs"]) == 50
    assert merged["evidence_refs"][-1] == TICK.format(i=40000)
    assert elapsed < 1.0


def test_schema_bound_agrees_with_store():
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["properties"]["evidence_refs"]["maxItems"] == MAX_EVIDENCE_REFS
