"""Cross-domain service reads are composed by narrow ports."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ports import create_in_memory_read_surface_ports


def test_composed_ports_keep_runtime_and_governance_records_typed() -> None:
    ports = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={
            "runtime_bindings": [{"binding_id": "runtime-001", "runtime_id": "runtime-001", "status": "active"}],
        },
        ooda_management_kwargs={
            "approval_decisions": [{"decision_id": "approval-001", "status": "approved"}],
        },
    )

    assert ports.get_runtime_binding_by_runtime_id("runtime-001")["status"] == "active"
    assert ports.get_approval_decision("approval-001")["status"] == "approved"
