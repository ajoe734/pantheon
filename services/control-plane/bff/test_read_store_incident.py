#!/usr/bin/env python3
"""Incident/lifecycle composition through the narrow typed domain port."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ports import create_in_memory_lifecycle_telemetry_governance_port


def _port():
    return create_in_memory_lifecycle_telemetry_governance_port(
        incidents={
            "inc-open": {
                "incident_id": "inc-open",
                "severity": "high",
                "status": "open",
                "affected_pool_ids": ["pool-main"],
            },
            "inc-resolved": {
                "incident_id": "inc-resolved",
                "severity": "medium",
                "status": "resolved",
                "affected_pool_ids": ["pool-secondary"],
            },
        },
        postmortems={
            "pm-resolved": {
                "postmortem_id": "pm-resolved",
                "incident_id": "inc-resolved",
                "root_cause": "stale deployment gate",
                "action_items": ["retrain"],
            }
        },
        evolution_decisions={
            "evo-open": {
                "decision_id": "evo-open",
                "incident_ref": "inc-open",
                "action_type": "retrain",
                "status": "approved",
            }
        },
        rollbacks_by_incident={"inc-open": [{"rollback_id": "rb-1", "status": "completed"}]},
        kill_switch={
            "status": "armed",
            "safe_mode_status": "off",
            "secondary_path_available": True,
            "active_freeze_orders": [],
        },
        telemetry_summaries={
            "runtime-042": {
                "runtime_id": "runtime-042",
                "pnl": -0.12,
                "drawdown": 0.04,
            }
        },
    )


def test_incident_filters_and_details() -> None:
    port = _port()

    assert {item["incident_id"] for item in port.list_incidents()} == {
        "inc-open",
        "inc-resolved",
    }
    assert [item["incident_id"] for item in port.list_incidents(status="open")] == [
        "inc-open"
    ]
    assert [item["incident_id"] for item in port.list_incidents(severity="high")] == [
        "inc-open"
    ]
    assert port.get_incident("inc-open")["status"] == "open"
    assert port.get_incident("missing") is None


def test_composed_incident_evidence_and_safety_reads() -> None:
    port = _port()

    assert port.get_postmortem_by_incident("inc-resolved")["postmortem_id"] == "pm-resolved"
    assert port.get_evolution_decisions_by_incident("inc-open")[0]["action_type"] == "retrain"
    assert port.get_rollbacks_by_incident("inc-open")[0]["status"] == "completed"
    assert port.get_kill_switch_status()["status"] == "armed"
    assert port.get_telemetry_summary("runtime-042")["pnl"] == -0.12
