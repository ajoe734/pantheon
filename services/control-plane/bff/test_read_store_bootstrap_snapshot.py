from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ports import create_in_memory_read_surface_ports


def test_unseeded_ports_do_not_invent_snapshot_records() -> None:
    ports = create_in_memory_read_surface_ports()

    assert ports.get_deployment_plan("plan-F-042") is None
    assert ports.list_bindings(persona_id="persona-alpha") == []
    assert ports.get_incident("inc-20260410-001") is None
    assert ports.get_postmortem_by_incident("inc-20260409-002") is None


def test_explicit_in_memory_ports_expose_only_declared_seed_records() -> None:
    ports = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={
            "deployment_plans": [
                {"id": "plan-F-042", "plan_id": "plan-F-042", "status": "approved"}
            ],
            "bindings": [
                {
                    "id": "binding-042",
                    "binding_id": "binding-042",
                    "persona_id": "persona-alpha",
                    "capital_pool_id": "pool-main",
                }
            ],
        },
        lifecycle_telemetry_governance_kwargs={
            "incidents": {
                "inc-20260410-001": {
                    "incident_id": "inc-20260410-001",
                    "status": "open",
                }
            },
            "postmortems": {
                "pm-20260409-002": {
                    "postmortem_id": "pm-20260409-002",
                    "incident_id": "inc-20260409-002",
                }
            },
        },
    )

    assert ports.get_deployment_plan("plan-F-042")["plan_id"] == "plan-F-042"
    assert [item["id"] for item in ports.list_bindings(persona_id="persona-alpha")] == [
        "binding-042"
    ]
    assert ports.get_incident("inc-20260410-001")["status"] == "open"
    assert (
        ports.get_postmortem_by_incident("inc-20260409-002")["postmortem_id"]
        == "pm-20260409-002"
    )
