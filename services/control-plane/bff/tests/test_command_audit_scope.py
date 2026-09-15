"""Verification suite for CommandStore governance audit projection and scope filtering."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
import tempfile
from typing import Any, Dict

import pytest

from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.governance.command_audit import (
    project_command_record_audit_event,
    audit_event_matches,
    list_projected_governance_audit_events,
)
from services.control_plane.bff.models import (
    CommandStatus,
    CommandType,
    ObjectType,
    TargetObject,
    utc_now,
)


@pytest.fixture
def temp_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "commands.jsonl")
        yield CommandStore(path)


def test_project_command_record_audit_event_mapping():
    now = utc_now()
    record = {
        "command_id": "cmd-audit-001",
        "type": CommandType.ISSUE_SAFE_MODE.value,
        "target": {"type": ObjectType.RUNTIME.value, "id": "rt-sys-001"},
        "submitted_at": now,
        "status": CommandStatus.SUBMITTED.value,
        "params": {"safe_mode_level": "soft"},
        "audit": {
            "operator_id": "op-audit-user",
            "reason": "routine test",
            "timestamp": now,
            "evidence_refs": ["ev-1", "ev-2"],
        },
        "foundation": {
            "idempotency_record": {
                "idempotency_key": "idem-audit-001",
                "request_hash": "hash123",
            },
            "trace_context": {
                "trace_id": "trace-001",
                "correlation_id": "corr-001",
            },
        },
    }

    event = project_command_record_audit_event(record)
    assert event is not None
    assert event["entry_id"] == "audit-cmd-audit-001"
    assert event["actor"] == "op-audit-user"
    assert event["action_type"] == CommandType.ISSUE_SAFE_MODE.value
    assert event["target_type"] == ObjectType.RUNTIME.value
    assert event["target_id"] == "rt-sys-001"
    assert event["timestamp"] == now
    assert event["outcome"] == "accepted"
    assert event["audit_context"]["reason"] == "routine test"
    assert event["audit_context"]["idempotency_key"] == "idem-audit-001"
    assert event["evidence_refs"] == ["ev-1", "ev-2"]
    assert event["trace_id"] == "trace-001"
    assert event["correlation_id"] == "corr-001"


def test_list_projected_governance_audit_events_filtering(temp_store: CommandStore):
    t0 = datetime(2026, 9, 14, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=10)
    t2 = t0 + timedelta(minutes=20)
    t3 = t0 + timedelta(minutes=30)

    # Command 1: op-alice, ISSUE_SAFE_MODE, t1, tenant-A
    temp_store.submit_command(
        command_id="cmd-1",
        command_type=CommandType.ISSUE_SAFE_MODE,
        target=TargetObject(type=ObjectType.RUNTIME, id="sys-1"),
        submitted_at=t1.isoformat(),
        params={"safe_mode_level": "soft"},
        audit_context={"operator_id": "op-alice", "tenant_id": "tenant-A", "timestamp": t1.isoformat()},
    )

    # Command 2: op-bob, PAUSE_RUNTIME, t2, tenant-A
    temp_store.submit_command(
        command_id="cmd-2",
        command_type=CommandType.PAUSE_RUNTIME,
        target=TargetObject(type=ObjectType.STRATEGY, id="strat-1"),
        submitted_at=t2.isoformat(),
        params={"pause_action": "pause"},
        audit_context={"operator_id": "op-bob", "tenant_id": "tenant-A", "timestamp": t2.isoformat()},
    )

    # Command 3: op-alice, APPROVE_DECISION, t3, tenant-B
    temp_store.submit_command(
        command_id="cmd-3",
        command_type=CommandType.APPROVE_DECISION,
        target=TargetObject(type=ObjectType.STRATEGY, id="strat-2"),
        submitted_at=t3.isoformat(),
        params={"decision_id": "dec-1"},
        audit_context={"operator_id": "op-alice", "tenant_id": "tenant-B", "timestamp": t3.isoformat()},
    )

    # All events
    all_events = list_projected_governance_audit_events(temp_store)
    assert len(all_events) == 3
    # Sorted descending by timestamp
    assert all_events[0]["command_ref"] == "cmd-3"
    assert all_events[1]["command_ref"] == "cmd-2"
    assert all_events[2]["command_ref"] == "cmd-1"

    # Filter by actor
    alice_events = list_projected_governance_audit_events(temp_store, actor="op-alice")
    assert len(alice_events) == 2
    assert {e["command_ref"] for e in alice_events} == {"cmd-1", "cmd-3"}

    # Filter by action_type
    pause_events = list_projected_governance_audit_events(
        temp_store, action_types=[CommandType.PAUSE_RUNTIME.value]
    )
    assert len(pause_events) == 1
    assert pause_events[0]["command_ref"] == "cmd-2"

    # Filter by target_type
    strat_events = list_projected_governance_audit_events(
        temp_store, target_type=ObjectType.STRATEGY.value
    )
    assert len(strat_events) == 2
    assert {e["command_ref"] for e in strat_events} == {"cmd-2", "cmd-3"}

    # Filter by time range
    time_filtered = list_projected_governance_audit_events(
        temp_store,
        from_ts=t0 + timedelta(minutes=5),
        to_ts=t0 + timedelta(minutes=25),
    )
    assert len(time_filtered) == 2
    assert {e["command_ref"] for e in time_filtered} == {"cmd-1", "cmd-2"}

    # Filter by tenant_id
    tenant_a_events = list_projected_governance_audit_events(temp_store, tenant_id="tenant-A")
    assert len(tenant_a_events) == 2
    assert {e["command_ref"] for e in tenant_a_events} == {"cmd-1", "cmd-2"}
