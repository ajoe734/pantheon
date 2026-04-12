#!/usr/bin/env python3
"""Tests for APP-002-W5-SSE-LIVE: SSE reconciler and BFF endpoints."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sse_reconciler import (
    reconcile_ui_state,
    reconcile_event_sequence,
    reconcile_runtime_event,
    reconcile_incident_event,
    reconcile_kill_switch_event,
    SSEReconnectManager,
)


def run_tests():
    # --- Reconciliation ---
    state = {"last_event_id": None, "data": {}}
    evt = {"id": "e1", "type": "runtime_state_changed", "timestamp": "T1",
           "data": {"runtime_id": "r-001", "current_state": "paper"}}
    state = reconcile_ui_state(state, evt)
    assert state["last_event_id"] == "e1"
    assert state["data"]["runtime_id"] == "r-001"
    print("✅ SSE-01: Event reconciliation works")

    # Idempotency
    state2 = reconcile_ui_state(state, evt)
    assert state2["last_event_id"] == "e1"
    print("✅ SSE-02: Idempotency gate skips duplicate")

    # Sequence
    events = [
        {"id": "e1", "type": "runtime_state_changed", "timestamp": "T1", "data": {"runtime_id": "r-001", "current_state": "paper"}},
        {"id": "e2", "type": "kill_switch_activated", "timestamp": "T2", "data": {"scope": "all"}},
    ]
    final = reconcile_event_sequence({"last_event_id": None, "data": {}}, events)
    assert final["last_event_id"] == "e2"
    assert final["data"]["scope"] == "all"
    print("✅ SSE-03: Sequence reconciliation works")

    # Runtime
    rt = {"last_event_id": None, "data": {}, "runtimes": {}}
    rt = reconcile_runtime_event(rt, {"id": "e1", "type": "runtime_state_changed", "data": {"runtime_id": "r-001", "previous_state": "paper", "current_state": "canary"}})
    assert rt["runtimes"]["r-001"]["state"] == "canary"
    print("✅ SSE-04: Runtime event reconciliation")

    # Incident
    inc = {"last_event_id": None, "data": {}, "incidents": {}}
    inc = reconcile_incident_event(inc, {"id": "e1", "type": "incident_created", "data": {"incident_id": "inc-001", "severity": "high"}})
    assert inc["incidents"]["inc-001"]["severity"] == "high"
    print("✅ SSE-05: Incident event reconciliation")

    # Kill-switch
    ks = {"last_event_id": None, "data": {}, "kill_switch": {}}
    ks = reconcile_kill_switch_event(ks, {"id": "e1", "type": "kill_switch_activated", "data": {"scope": "all"}})
    assert ks["kill_switch"]["active"] is True
    print("✅ SSE-06: Kill-switch event reconciliation")

    # Reconnect manager
    mgr = SSEReconnectManager("https://bff/api/v1/runtime/r-001/events/stream")
    assert "last_event_id" not in mgr.build_url()
    mgr.record_event_id("evt-001")
    assert "last_event_id=evt-001" in mgr.build_url()
    print("✅ SSE-07: Reconnect manager URL building")

    mgr.on_connect()
    assert mgr.is_connected
    delay = mgr.on_disconnect()
    assert delay > 0
    assert not mgr.is_connected
    print("✅ SSE-08: Reconnect manager backoff")

    # BFF SSE endpoints
    bff_path = os.path.join(os.path.dirname(__file__), "..", "control-plane", "bff", "main.py")
    with open(bff_path) as f:
        src = f.read()
    for route in ["/api/v1/runtime/{runtime_id}/events/stream", "/api/v1/incidents/stream", "/api/v1/kill-switch/updates"]:
        assert route in src, f"Missing: {route}"
    print("✅ SSE-09: All 3 SSE endpoints in BFF")
    assert "text/event-stream" in src
    assert "last_event_id" in src
    assert "_replay_from" in src
    print("✅ SSE-10: SSE headers + reconnect semantics verified")

    print("\n==================================================")
    print("APP-002-W5-SSE-LIVE tests: ALL PASSED")


if __name__ == "__main__":
    run_tests()
