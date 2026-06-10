#!/usr/bin/env python3
"""Unit test for ReadSurfaceStore incident methods — no FastAPI dependency."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from read_store import ReadSurfaceStore


def test_kill_switch_status_reads_service_backed_store_when_fallback_disabled():
    with tempfile.TemporaryDirectory() as td:
        store_path = os.path.join(td, "read_surfaces.json")
        kill_switch_path = Path(td) / "kill_switch.json"
        kill_switch_path.write_text(json.dumps({
            "current": {
                "id": "current",
                "active": False,
                "active_freeze_orders": [],
                "last_checked_at": "2026-06-03T08:00:00Z",
                "last_confirmed_at": "2026-06-03T08:00:00Z",
                "last_triggered_at": None,
                "active_commands": [],
                "secondary_path_available": True,
                "safe_mode_status": "off",
                "status": "armed",
            }
        }))
        original_env = os.environ.get("PANTHEON_BFF_KILL_SWITCH_STORE")
        os.environ["PANTHEON_BFF_KILL_SWITCH_STORE"] = str(kill_switch_path)
        try:
            store = ReadSurfaceStore(store_path, allow_local_snapshot_fallback=False)
            assert store.dataset_source("kill_switch") == "service_store"
            ks = store.get_kill_switch_status()
            assert ks["status"] == "armed"
            assert ks["safe_mode_status"] == "off"
            assert ks["secondary_path_available"] is True
        finally:
            if original_env is None:
                os.environ.pop("PANTHEON_BFF_KILL_SWITCH_STORE", None)
            else:
                os.environ["PANTHEON_BFF_KILL_SWITCH_STORE"] = original_env


def test_store():
    with tempfile.TemporaryDirectory() as td:
        store_path = os.path.join(td, "read_surfaces.json")
        store = ReadSurfaceStore(store_path, allow_local_snapshot_fallback=True)

        # IN-01: list incidents
        incidents = store.list_incidents()
        assert len(incidents) >= 2, f"Expected >= 2 incidents, got {len(incidents)}"
        assert incidents[0]["status"] in ("open", "resolved")
        print("✅ IN-01: list_incidents returns all incidents")

        # IN-01: filter by status
        active = store.list_incidents(status="open")
        assert len(active) >= 1
        assert all(i["status"] == "open" for i in active)
        print("✅ IN-01: list_incidents filtered by status=open")

        # IN-01: filter by comma-separated status values
        active_or_resolved = store.list_incidents(status="open,resolved")
        assert len(active_or_resolved) >= len(active)
        assert all(i["status"] in {"open", "resolved"} for i in active_or_resolved)
        print("✅ IN-01: list_incidents filtered by comma-separated status")

        # IN-01: filter by severity
        high = store.list_incidents(severity="high")
        assert len(high) >= 1
        assert all(i["severity"] == "high" for i in high)
        print("✅ IN-01: list_incidents filtered by severity=high")

        # IN-01: filter by pool
        pool_incidents = store.list_incidents(affected_pool_id="pool-main")
        assert len(pool_incidents) >= 1
        print("✅ IN-01: list_incidents filtered by affected_pool_id")

        # IN-02: incident detail
        incident = store.get_incident("inc-20260410-001")
        assert incident is not None
        assert incident["incident_id"] == "inc-20260410-001"
        assert incident["severity"] == "high"
        assert incident["status"] == "open"
        assert incident["binding_id"] == "runtime-042"
        print("✅ IN-02: get_incident returns correct detail")

        # IN-02: not found
        assert store.get_incident("inc-nonexistent") is None
        print("✅ IN-02: get_incident returns None for nonexistent")

        # IN-03: postmortem list
        postmortems = store.list_postmortems()
        assert len(postmortems) >= 1
        print("✅ IN-03: list_postmortems returns data")

        # IN-04: postmortem detail
        pm = store.get_postmortem("pm-20260409-002")
        assert pm is not None
        assert pm["postmortem_id"] == "pm-20260409-002"
        assert "root_cause" in pm
        assert "action_items" in pm
        print("✅ IN-04: get_postmortem returns correct detail")

        # IN-04: not found
        assert store.get_postmortem("pm-nonexistent") is None
        print("✅ IN-04: get_postmortem returns None for nonexistent")

        # IN-05: kill switch status
        ks = store.get_kill_switch_status()
        assert "active_freeze_orders" in ks
        assert "safe_mode_status" in ks
        assert "last_checked_at" in ks
        assert ks["status"] in ("armed", "triggered", "cooling_down")
        assert "last_triggered_at" in ks
        assert "last_confirmed_at" in ks
        assert "active_commands" in ks
        print("✅ IN-05: get_kill_switch_status returns expected shape")

        # Composed view helpers
        decisions = store.get_evolution_decisions_by_incident("inc-20260410-001")
        assert len(decisions) >= 1
        print("✅ Composed: get_evolution_decisions_by_incident")

        rollbacks = store.get_rollbacks_by_incident("inc-20260410-001")
        assert len(rollbacks) >= 1
        assert rollbacks[0]["status"] == "completed"
        print("✅ Composed: get_rollbacks_by_incident")

        telemetry = store.get_telemetry_summary("runtime-042")
        assert telemetry is not None
        assert "pnl" in telemetry
        assert "drawdown" in telemetry
        print("✅ Composed: get_telemetry_summary")

        # Verify seed data persists
        store2 = ReadSurfaceStore(store_path, allow_local_snapshot_fallback=True)
        assert store2.get_incident("inc-20260410-001") is not None
        print("✅ Persistence: store reloads data correctly")

    print("\n" + "=" * 50)
    print("ReadSurfaceStore incident tests: ALL PASSED")


if __name__ == "__main__":
    test_store()
