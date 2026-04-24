#!/usr/bin/env python3
"""Unit test for APP-002-W4 remaining catalog read surfaces."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from read_store import ReadSurfaceStore


def test_w4_remaining_catalog():
    with tempfile.TemporaryDirectory() as td:
        store_path = os.path.join(td, "read_surfaces.json")
        store = ReadSurfaceStore(store_path, allow_local_snapshot_fallback=True)

        # ------------------------------------------------------------------ #
        # Persona surfaces (PS-01 – PS-06)
        # ------------------------------------------------------------------ #
        personas = store.list_personas()
        assert len(personas) >= 1, "Expected at least one persona"
        assert personas[0]["id"] == "persona-alpha"
        print("✅ PS-01: list_personas returns personas")

        persona = store.get_persona("persona-alpha")
        assert persona is not None
        print("✅ PS-02: get_persona returns detail")

        sessions = store.list_sessions_for_persona("persona-alpha")
        assert sessions is not None
        assert len(sessions) >= 1
        print("✅ PS-03: list_sessions_for_persona returns sessions")

        session = store.get_session("sess-001")
        assert session is not None
        print("✅ PS-04: get_session returns detail")

        teaching = store.list_teaching_sessions_for_persona("persona-alpha")
        assert teaching is not None
        assert len(teaching) >= 1
        print("✅ PS-05: list_teaching_sessions_for_persona returns sessions")

        capability = store.get_capability_snapshot_for_persona("persona-alpha")
        assert capability is not None
        assert capability["snapshot_id"] == "cap-001"
        print("✅ PS-06: capability snapshot available")

        # ------------------------------------------------------------------ #
        # Capital pool and binding surfaces (CP-01 – CP-04)
        # ------------------------------------------------------------------ #
        pools = store.list_capital_pools()
        assert len(pools) >= 1
        assert pools[0]["id"] == "pool-main"
        print("✅ CP-01: list_capital_pools returns pools")

        bindings = store.list_bindings()
        assert len(bindings) >= 1
        assert bindings[0]["id"] == "binding-042"
        print("✅ CP-03: list_bindings returns bindings")

        # ------------------------------------------------------------------ #
        # Deployment surfaces (DP-01 – DP-04)
        # ------------------------------------------------------------------ #
        plans = store.list_deployment_plans()
        assert len(plans) >= 1
        assert plans[0]["id"] == "plan-F-042"
        print("✅ DP-01: list_deployment_plans returns plans")

        decisions = store.list_approval_decisions()
        assert len(decisions) >= 1
        assert decisions[0]["id"] == "approval-042"
        print("✅ DP-03: list_approval_decisions returns decisions")

        decision_detail = store.get_approval_decision("approval-042")
        assert decision_detail is not None
        assert decision_detail["id"] == "approval-042"
        print("✅ DP-04: get_approval_decision returns detail")

        # ------------------------------------------------------------------ #
        # Runtime surfaces (RT-01 – RT-03)
        # ------------------------------------------------------------------ #
        runtime_bindings = store.list_runtime_bindings()
        assert len(runtime_bindings) >= 1
        assert runtime_bindings[0]["id"] == "runtime-042"
        print("✅ RT-01: list_runtime_bindings returns bindings")

        runtime_status = store.get_runtime_binding_by_runtime_id("runtime-042")
        assert runtime_status is not None
        assert runtime_status["runtime_id"] == "runtime-042"
        print("✅ RT-03: get_runtime_binding_by_runtime_id returns status")

    print("\n" + "=" * 50)
    print("APP-002-W4-REMAINING-CATALOG surface tests: ALL PASSED")


if __name__ == "__main__":
    test_w4_remaining_catalog()
