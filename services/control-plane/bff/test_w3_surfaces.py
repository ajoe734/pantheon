#!/usr/bin/env python3
"""Unit test for APP-002-W3-POSTINCIDENT-EVOLUTION surfaces — no FastAPI dependency."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from ports import create_in_memory_lifecycle_telemetry_governance_port


def test_w3_surfaces():
    with tempfile.TemporaryDirectory() as td:
        data = json.loads(
            (Path(__file__).resolve().parent / "data" / "read_surfaces.json").read_text(
                encoding="utf-8"
            )
        )
        store = create_in_memory_lifecycle_telemetry_governance_port(
            incidents=data["incidents"],
            postmortems=data["postmortems"],
            evolution_decisions=data["evolution_decisions"],
            rollbacks_by_incident=data["rollbacks_by_incident"],
            kill_switch=data["kill_switch"],
            freeze_orders=data["freeze_orders"],
            all_rollbacks=data["all_rollbacks"],
            lineage_edges=data["lineage_edges"],
            telemetry_summaries=data["telemetry_summaries"],
            telemetry_performance=data["telemetry_performance"],
        )

        # ------------------------------------------------------------------ #
        # Post-incident review composed view data
        # ------------------------------------------------------------------ #
        incident = store.get_incident("inc-20260409-002")
        assert incident is not None, "Incident must exist for post-incident review"
        print("✅ Post-Incident: incident data available")

        postmortem = store.get_postmortem_by_incident("inc-20260409-002")
        assert postmortem is not None, "Postmortem must exist for resolved incident"
        assert postmortem["postmortem_id"] == "pm-20260409-002"
        assert "root_cause" in postmortem
        assert "action_items" in postmortem
        print("✅ Post-Incident: postmortem report available with root_cause and action_items")

        evolution = store.get_evolution_decisions_by_incident("inc-20260410-001")
        assert len(evolution) >= 1
        assert evolution[0]["action_type"] == "retrain"
        assert evolution[0]["status"] == "approved"
        print("✅ Post-Incident: evolution decisions linked to incident")

        # ------------------------------------------------------------------ #
        # LN-01: Lineage edge list
        # ------------------------------------------------------------------ #
        edges = store.list_lineage_edges()
        assert len(edges) >= 2, f"Expected >= 2 lineage edges, got {len(edges)}"
        print(f"✅ LN-01: list_lineage_edges returns {len(edges)} edges")

        # LN-01: filter by artifact
        artifact_edges = store.list_lineage_edges(artifact_id="artifact-042")
        assert len(artifact_edges) >= 2, "artifact-042 should appear in multiple edges"
        print(f"✅ LN-01: filtered edges for artifact-042 = {len(artifact_edges)}")

        # ------------------------------------------------------------------ #
        # LN-02: Lineage edge detail
        # ------------------------------------------------------------------ #
        edge = store.get_lineage_edge("ln-edge-001")
        assert edge is not None
        assert edge["from_artifact_id"] == "artifact-041"
        assert edge["to_artifact_id"] == "artifact-042"
        assert edge["relationship"] == "derived_from"
        print("✅ LN-02: get_lineage_edge returns correct detail")

        assert store.get_lineage_edge("ln-edge-nonexistent") is None
        print("✅ LN-02: get_lineage_edge returns None for nonexistent")

        # ------------------------------------------------------------------ #
        # LN-03: Lineage graph
        # ------------------------------------------------------------------ #
        graph = store.get_lineage_graph(root_id="artifact-042")
        assert len(graph) >= 2, "artifact-042 should have incoming and outgoing edges"
        print(f"✅ LN-03: lineage graph for artifact-042 = {len(graph)} edges")

        graph_depth1 = store.get_lineage_graph(root_id="artifact-042", depth=1)
        assert len(graph_depth1) >= 2
        print(f"✅ LN-03: lineage graph depth=1 returns {len(graph_depth1)} edges")

        graph_depth10 = store.get_lineage_graph(root_id="artifact-042", depth=10)
        # depth clamped to 10, same result as default
        assert len(graph_depth10) >= 2
        print(f"✅ LN-03: lineage graph depth=10 returns {len(graph_depth10)} edges")

        # ------------------------------------------------------------------ #
        # TL-01: Telemetry event list
        # ------------------------------------------------------------------ #
        events = store.list_telemetry_events()
        assert len(events) >= 1, f"Expected >= 1 telemetry event, got {len(events)}"
        assert events[0]["type"] == "telemetry_snapshot"
        assert "metrics" in events[0]
        assert "pnl" in events[0]["metrics"]
        assert "drawdown" in events[0]["metrics"]
        print(f"✅ TL-01: list_telemetry_events returns {len(events)} events with metrics")

        # TL-01: filter by artifact_id
        artifact_events = store.list_telemetry_events(artifact_id="runtime-042")
        assert len(artifact_events) >= 1
        print(f"✅ TL-01: filtered events for runtime-042 = {len(artifact_events)}")

        # ------------------------------------------------------------------ #
        # TL-02: Telemetry summary
        # ------------------------------------------------------------------ #
        summary = store.get_telemetry_summary("runtime-042")
        assert summary is not None
        assert "pnl" in summary
        assert "drawdown" in summary
        assert "sharpe_ratio" in summary
        assert summary["pnl"] == -0.12
        print("✅ TL-02: get_telemetry_summary returns correct data")

        assert store.get_telemetry_summary("runtime-nonexistent") is None
        print("✅ TL-02: get_telemetry_summary returns None for nonexistent")

        # ------------------------------------------------------------------ #
        # TL-03: Telemetry performance
        # ------------------------------------------------------------------ #
        perf = store.get_telemetry_performance("artifact-042")
        assert perf is not None
        assert "data_points" in perf
        assert "summary" in perf
        assert len(perf["data_points"]) >= 2
        assert perf["summary"]["total_pnl"] == -0.12
        print("✅ TL-03: get_telemetry_performance returns chart data with summary")

        assert store.get_telemetry_performance("artifact-nonexistent") is None
        print("✅ TL-03: get_telemetry_performance returns None for nonexistent")

        # ------------------------------------------------------------------ #
        # EV-01: Evolution decisions list
        # ------------------------------------------------------------------ #
        decisions = store.list_evolution_decisions()
        assert len(decisions) >= 1
        print(f"✅ EV-01: list_evolution_decisions returns {len(decisions)} decisions")

        decisions_filtered = store.list_evolution_decisions(status="approved")
        assert len(decisions_filtered) >= 1
        assert all(d["status"] == "approved" for d in decisions_filtered)
        print("✅ EV-01: filtered decisions by status=approved")

        # ------------------------------------------------------------------ #
        # EV-02: Evolution decision detail
        # ------------------------------------------------------------------ #
        decision = store.get_evolution_decision_by_id("evo-dec-001")
        assert decision is not None
        assert decision["action_type"] == "retrain"
        assert decision["risk_level"] == "medium"
        assert decision["updated_at"] == "2026-04-11T09:00:00Z"
        assert decision["notes"] == "Approved for retrain after promotion gate timeout root cause confirmed."
        print("✅ EV-02: get_evolution_decision_by_id returns correct detail")

        assert store.get_evolution_decision_by_id("evo-nonexistent") is None
        print("✅ EV-02: get_evolution_decision_by_id returns None for nonexistent")

        # ------------------------------------------------------------------ #
        # EV-03: Freeze orders
        # ------------------------------------------------------------------ #
        orders = store.list_freeze_orders()
        assert len(orders) >= 1
        assert orders[0]["status"] == "active"
        assert orders[0]["scope"] == "persona"
        print(f"✅ EV-03: list_freeze_orders returns {len(orders)} orders")

        orders_filtered = store.list_freeze_orders(status="active")
        assert len(orders_filtered) >= 1
        print("✅ EV-03: filtered orders by status=active")

        # ------------------------------------------------------------------ #
        # EV-04: Global rollback list
        # ------------------------------------------------------------------ #
        rollbacks = store.list_all_rollbacks()
        assert len(rollbacks) >= 1
        assert rollbacks[0]["status"] == "completed"
        print(f"✅ EV-04: list_all_rollbacks returns {len(rollbacks)} rollbacks")

        rollbacks_filtered = store.list_all_rollbacks(runtime_id="runtime-042")
        assert len(rollbacks_filtered) >= 1
        print("✅ EV-04: filtered rollbacks by runtime_id")

    print("\n" + "=" * 50)
    print("APP-002-W3-POSTINCIDENT-EVOLUTION surface tests: ALL PASSED")


if __name__ == "__main__":
    test_w3_surfaces()
